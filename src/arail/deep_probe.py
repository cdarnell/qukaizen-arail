"""./arailctl deep probe: does the deep model answer five calls in a row?

Five sequential generations through ARAIL's own ``AeroLLMBackend``, in a fresh
child process, never inside the portal. Token-free and local: no network.

Why it exists (sprint 2026-10-29-arail-deep-honest): every QueueLLM build tested
on this Mac answers the first deep call in a process and fails the second, and
ARAIL's status surfaces tested import, policy and residency, never generation.
This is the one explicit test that does, and its result is stored per binary in
``arail.deep_verdict`` so every surface can read it.

Two processes, so a crash or a hang cannot take the supervisor with it:

* the SUPERVISOR (this module's ``run``) never imports ``aerollm_api``. It
  checks the lock, the wheel, the model dir and memory, spawns the child in its
  own process group, reads the child's one-JSON-object-per-line protocol with
  deadlines, kills the group on a timeout, and records the result.
* the CHILD (``--child``) parks stdout on stderr (native code may print), keeps
  the protocol on a private copy of the original stdout, imports the runtime,
  builds ``AeroLLMBackend`` and makes the five calls.

Exit codes: 0 five of five and recorded passing; 1 the probe ran and fewer than
five answered (a load failure, a crash and a timeout count), or five answered but
the result could not be saved or a failure elsewhere still stands, so deep is not
cleared; 2 usage; 5 not run (nothing recorded); 130 interrupted (child killed,
nothing recorded).

Every number printed carries its meaning, its bar and why, what out of range
means and where the failure lives (``TEACHING``). A surface prints numbers only
through ``_Nums``, which records each key it used, so the legend cannot miss one.
"""
from __future__ import annotations

import errno
import json
import os
import selectors
import signal
import statistics
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional

from arail import deep_verdict as dv

SCHEMA = "arail.deep-probe/v1"
TOTAL_CALLS = 5
MAX_TOKENS = 384
TEMPERATURE = 0.2
HEADROOM_GIB = 2.0
DEFAULT_CALL_TIMEOUT_S = 300.0
DEFAULT_LOAD_TIMEOUT_S = 300.0
STDERR_TAIL_LINES = 20

EXIT_PASS, EXIT_FAIL, EXIT_USAGE, EXIT_NOT_RUN, EXIT_INTERRUPTED = 0, 1, 2, 5, 130

# ---------------------------------------------------------------------------
# Teaching. Keys are the normalised JSON paths of every number the probe prints
# (list indices become []). Each entry has four non-empty parts. Identifiers are
# not numbers and are labelled, not taught: sha256 values, pids, paths,
# timestamps, versions, file byte sizes, the call row index, prompt_set_sha256.
# ---------------------------------------------------------------------------
TEACHING: dict[str, dict[str, str]] = {
    "passed": {
        "meaning": "deep calls that answered with non-empty text, out of 5 in a row in one fresh process",
        "threshold": "5/5: every consumer makes more than one call per process, and call 2 is where "
                     "every known broken build fails",
        "out_of_range": "1/5: answers once per process (the call-2 bug). 0/5: cannot answer at all. "
                        "2-4: intermittent, treated as failing",
        "failure_lives_in": "the QueueLLM binary on the binary line, not ARAIL settings; read the first error",
    },
    "total": {
        "meaning": "calls attempted",
        "threshold": "5",
        "out_of_range": "fewer: the probe stopped early (a crash or a timeout)",
        "failure_lives_in": "the supervisor's record and the stderr tail",
    },
    "median_ok_s": {
        "meaning": "middle wall time of the calls that answered: prefill plus up to 384 new tokens",
        "threshold": "none for this probe; an interactive deep answer should stay under 120 s",
        "out_of_range": "over 120 s: memory pressure or a slow build. No value: nothing answered",
        "failure_lives_in": "runtime decode speed, or memory on this Mac (see the memory line)",
    },
    "calls[].seconds": {
        "meaning": "wall time of one call",
        "threshold": "must end within the per-call deadline, 300 s unless ARAIL_DEEP_PROBE_CALL_TIMEOUT_S says otherwise",
        "out_of_range": "about 0.0 s with an error: it failed before the first token (in prefill)",
        "failure_lives_in": "the runtime",
    },
    "calls[].prompt_tokens": {
        "meaning": "prompt length after ARAIL's chat wrap, by the model's tokenizer",
        "threshold": "short about 33; long about 1,933, the size of a chat turn with lab context",
        "out_of_range": "absent: the tokenizer did not load (the prompts were still sent)",
        "failure_lives_in": "the model dir's tokenizer files",
    },
    "calls[].words_out": {
        "meaning": "words in the answer",
        "threshold": "above 0; an empty answer counts as a failure",
        "out_of_range": "0: an empty answer",
        "failure_lives_in": "runtime decode",
    },
    "first_error.call": {
        "meaning": "index of the first failed call",
        "threshold": "absent when all pass",
        "out_of_range": "2: the known call-2 bug. 1: cannot answer at all",
        "failure_lives_in": "the runtime",
    },
    "load.seconds": {
        "meaning": "weights load plus runtime start",
        "threshold": "must end within the load deadline, 300 s unless ARAIL_DEEP_PROBE_LOAD_TIMEOUT_S says otherwise",
        "out_of_range": "slow: disk or memory pressure",
        "failure_lives_in": "the model dir and the runtime",
    },
    "memory.available_gib": {
        "meaning": "memory available before loading",
        "threshold": "at least the weights on disk plus headroom, or the probe refuses unless --force",
        "out_of_range": "below: a second copy could push the Mac into swap",
        "failure_lives_in": "other processes (see the holders on the memory line)",
    },
    "memory.needed_gib": {
        "meaning": "weights on disk plus headroom for activations and the runtime beyond the KV budget",
        "threshold": "the bar for memory.available_gib",
        "out_of_range": "far above weights plus headroom: the weights size was misread",
        "failure_lives_in": "model.disk_gib and the model dir",
    },
    "model.disk_gib": {
        "meaning": "weights on disk, about the resident size",
        "threshold": "input to the memory bar; about 4.0 GiB for the 4-bit 7B",
        "out_of_range": "far from 4 GiB for this model: the wrong model dir",
        "failure_lives_in": "AEROLLM_MODEL and ARAIL_MODELS_DIR",
    },
    "runtime.ring_depth": {
        "meaning": "layer blocks QueueLLM keeps resident, set by the profile",
        "threshold": "none; recorded because a pass at one depth does not prove another",
        "out_of_range": "a depth other than the lab runtime's: the result may not carry over to the lab",
        "failure_lives_in": "AEROLLM_RING_DEPTH or the runtime profile",
    },
    "runtime.kv_budget_gib": {
        "meaning": "the KV cache budget ARAIL gave the runtime",
        "threshold": "set from AEROLLM_KV_BUDGET_PCT and available memory",
        "out_of_range": "very small: prompts may fail for memory, not for the bug",
        "failure_lives_in": ".env and available memory",
    },
    "runtime.max_tokens": {
        "meaning": "new tokens each call may produce",
        "threshold": "384, the same as the day-1 check, so results compare",
        "out_of_range": "another value: results no longer compare with DAY1.json",
        "failure_lives_in": "the deep_probe.py constants",
    },
    "runtime.temperature": {
        "meaning": "sampling temperature of each call",
        "threshold": "0.2, the same as the day-1 check, so results compare",
        "out_of_range": "another value: results no longer compare with DAY1.json",
        "failure_lives_in": "the deep_probe.py constants",
    },
    "duration_s": {
        "meaning": "whole probe wall time, from lock to record",
        "threshold": "under 30 minutes by the deadlines",
        "out_of_range": "longer: a deadline did not fire",
        "failure_lives_in": "deep_probe.py",
    },
    "exit_code": {
        "meaning": "the process exit code: 0 five of five and recorded passing, 1 deep is not cleared, "
                   "5 not run, 130 interrupted",
        "threshold": "0, and only 0 clears a recorded failure",
        "out_of_range": "1: fewer than five answered, so deep is failing; or five answered but the result could "
                        "not be saved or a failure elsewhere still stands, so deep is not cleared. 5: nothing ran "
                        "and nothing was recorded, so fix the line that says why and run it again",
        "failure_lives_in": "the line that explains it: the first error for 1 (or the NOT SAVED line, or the line "
                            "about a failure elsewhere), the not-run reason for 5",
    },
}


# How each number is labelled where a person reads it. The legend's headings are these labels, not the
# JSON paths the teaching object uses. Each entry is (heading, the words printed on the line beside the
# number). A test checks that every printed number sits on a line that carries one of its words, so a
# heading can never name something the line does not say. Keys that only the JSON carries have no words.
LABELS: dict[str, tuple[str, tuple[str, ...]]] = {
    "passed": ("passed (the first number of 1/5 on the result line)", ("passed", "answered", "passes")),
    "total": ("calls (the 5 on the first line, and the second number of 1/5)", ("calls", "planned", "passed", "passes")),
    "median_ok_s": ("median (the median line)", ("median",)),
    "calls[].seconds": ("seconds (the s column of each call line)", (" s",)),
    "calls[].prompt_tokens": ("tokens in (each call line)", ("tokens in",)),
    "calls[].words_out": ("words (each call line)", (" words",)),
    "first_error.call": ("first error at call (the first line, and 'same error as call')", ("call",)),
    "load.seconds": ("load time (the load line)", ("load",)),
    "runtime.ring_depth": ("ring depth (the load line)", ("ring depth",)),
    "runtime.max_tokens": ("new tokens per call (the load line)", ("new tokens",)),
    "memory.available_gib": ("GiB available (the memory line)", ("available",)),
    "memory.needed_gib": ("GiB needed (the not-run line)", ("needed",)),
    "model.disk_gib": ("GiB on disk (the model line)", ("on disk", "second copy", "weights")),
    "runtime.kv_budget_gib": ("KV budget GiB (JSON only)", ()),
    "runtime.temperature": ("temperature (JSON only)", ()),
    "duration_s": ("duration seconds (JSON only)", ()),
    "exit_code": ("exit code (JSON only; the process exit status)", ()),
}


class _Nums:
    """Every number a surface prints goes through here, so its key is recorded, and so is the line it
    landed on (``by_line``), which is how a test checks the number sits beside its label."""

    def __init__(self) -> None:
        self.used: list[str] = []
        self.rendered: list[tuple[str, str]] = []
        self.by_line: dict[int, list[tuple[str, str]]] = {}
        self._pending: list[tuple[str, str]] = []

    def __call__(self, key: str, text: str) -> str:
        if key not in TEACHING:
            raise KeyError(f"no teaching for {key!r}")
        if key not in self.used:
            self.used.append(key)
        self.rendered.append((key, text))
        self._pending.append((key, text))
        return text

    def mark(self, line_index: int) -> None:
        """The line just printed carries the numbers rendered since the last one."""
        if self._pending:
            self.by_line[line_index] = self._pending
            self._pending = []


def legend_lines(keys: list[str]) -> list[str]:
    """'What the numbers mean': one entry per key used, all four parts each."""
    out = ["", "What the numbers mean"]
    for key in keys:
        t = TEACHING[key]
        out.append(f"  {LABELS[key][0]}")
        out.append(f"      meaning: {t['meaning']}")
        out.append(f"      bar and why: {t['threshold']}")
        out.append(f"      out of range: {t['out_of_range']}")
        out.append(f"      the fault lives in: {t['failure_lives_in']}")
    return out


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
class _Usage(Exception):
    pass


def _parse(argv: list[str]) -> dict:
    opts = {"json": False, "force": False, "child": False, "help": False}
    for a in argv:
        if a == "--json":
            opts["json"] = True
        elif a == "--force":
            opts["force"] = True
        elif a == "--child":
            opts["child"] = True
        elif a in ("-h", "--help"):
            opts["help"] = True
        else:
            raise _Usage(f"unknown argument {a!r}")
    return opts


_USAGE = """usage: ./arailctl deep probe [--json] [--force]
       python -m arail.deep_probe [--json] [--force]

Makes 5 sequential deep calls through ARAIL's own backend in a fresh process and
records the result for the binary it loaded. Explicit only: nothing runs it at boot.

  --json    one JSON object on stdout and nothing else (schema arail.deep-probe/v1)
  --force   skip the memory refusal only; never the lock

exit: 0 five of five, recorded | 1 deep not cleared | 2 usage | 5 not run | 130 interrupted
"""


def _env_float(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, "").strip() or default)
        return v if v > 0 else default
    except ValueError:
        return default


def _gib(n_bytes: float) -> float:
    return n_bytes / float(1 << 30)


def _dir_bytes(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _signal_name(rc: int) -> str:
    try:
        sig = signal.Signals(-rc)
        return f"signal {sig.value}, {sig.name}"
    except ValueError:
        return f"signal {-rc}"


def _default_memory_gib() -> Optional[float]:
    try:
        import psutil
        return _gib(psutil.virtual_memory().available)
    except Exception:  # noqa: BLE001
        return None


def _lsof_holders(native_path: str) -> list[dict]:
    """Other processes that already have this binary mapped: ``[{pid, command}]``.
    Argv lists only; the path is one argument after ``--``. Empty if lsof is absent."""
    try:
        r = subprocess.run(["lsof", "-t", "-n", "-P", "--", native_path], capture_output=True,
                           text=True, timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return []
    pids = sorted({int(x) for x in r.stdout.split() if x.isdigit()} - {os.getpid()})
    out = []
    for pid in pids:
        cmd = ""
        try:
            p = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True,
                               timeout=5, stdin=subprocess.DEVNULL)
            cmd = " ".join(p.stdout.split())
        except (OSError, subprocess.SubprocessError):
            pass
        out.append({"pid": pid, "command": cmd[:48]})
    return out


# ---------------------------------------------------------------------------
# The CHILD: runs five calls and speaks the protocol. Never run by the portal.
# ---------------------------------------------------------------------------
def _child_main() -> int:
    proto = os.dup(1)
    os.dup2(2, 1)                      # stray native stdout goes to stderr, never into the protocol
    parent = os.getppid()

    def emit(ev: str, **fields: Any) -> None:
        data = (json.dumps({"probe": 1, "ev": ev, **fields}, ensure_ascii=False) + "\n").encode("utf-8")
        while data:
            n = os.write(proto, data)
            data = data[n:]

    def finish(code: int = 0) -> int:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:  # noqa: BLE001
            pass
        os._exit(code)                  # skips atexit: its executor shutdown would wait on the runtime

    try:
        import importlib
        importlib.import_module(dv.FROZEN_MODULE)
    except BaseException as e:  # noqa: BLE001 - a native import can fail in many ways
        emit("load_failed", error=dv._clip(f"import {dv.FROZEN_MODULE} failed: {dv._err_text(e)}", 300))
        return finish()
    b = dv.identity(import_if_needed=False)
    if b is None:
        emit("load_failed", error=dv._clip("the loaded binary could not be identified: "
                                           + (dv._P.ident_reason or "unknown"), 300))
        return finish()
    emit("identity", module=b.module, resolves_to=b.resolves_to, native_path=b.native_path,
         sha256=b.sha256, bytes=b.bytes, mtime=b.mtime, version=b.version)

    from arail.deep_probe_prompts import PROMPTS
    from arail.router import backends
    seen: dict[str, Any] = {}
    orig = backends.AeroLLMBackend._emit_budget_activity

    def spy(self: Any, reasoning: dict) -> None:
        seen["kv_bytes"] = reasoning.get("budget_bytes")
        return orig(self, reasoning)

    backends.AeroLLMBackend._emit_budget_activity = spy      # type: ignore[method-assign]
    backends.AeroLLMBackend._shared = {}
    t0 = time.monotonic()
    try:
        backend = backends.AeroLLMBackend()
    except BaseException as e:  # noqa: BLE001
        emit("load_failed", error=dv._clip(dv._err_text(e), 300))
        return finish()
    load_s = time.monotonic() - t0
    tokens: Optional[list] = None
    tok = getattr(backend, "_tokenizer", None)
    if tok is not None:
        try:
            tokens = [len(tok.encode(backend._wrap_prompt(p))) for _k, p in PROMPTS]
        except Exception:  # noqa: BLE001
            tokens = None
    kv = seen.get("kv_bytes")
    emit("loaded", seconds=load_s, ring_depth=getattr(backend, "_ring_depth", None),
         ring_depth_source=getattr(backend, "_ring_depth_source", None),
         kv_budget_gib=(_gib(kv) if isinstance(kv, (int, float)) else None),
         prompt_tokens=tokens, tokenizer=tok is not None)
    for i, (kind, prompt) in enumerate(PROMPTS, 1):
        if os.getppid() != parent:     # the supervisor died; do not start another call
            return finish(3)
        t = time.monotonic()
        try:
            resp = backend.complete(prompt, max_tokens=MAX_TOKENS, temperature=TEMPERATURE)
            text = resp.text or ""
            ok = bool(text.strip())
            emit("call", i=i, kind=kind, ok=ok, s=time.monotonic() - t, words=len(text.split()),
                 error=None if ok else "the runtime returned no text", end="answered" if ok else "empty")
        except Exception as e:  # noqa: BLE001
            emit("call", i=i, kind=kind, ok=False, s=time.monotonic() - t, words=0,
                 error=dv._clip(dv._err_text(e), 300), end="error")
    emit("done")
    return finish()


# ---------------------------------------------------------------------------
# The SUPERVISOR
# ---------------------------------------------------------------------------
def _take_probe_lock(state: Path) -> tuple[Optional[int], Optional[dict]]:
    """``(fd, None)`` when taken, else ``(None, holder)``. flock, non-blocking."""
    import fcntl
    dir_fd = dv._open_state_dir(state)       # refuses a link or a folder that is not yours (QA-4)
    try:
        fd = os.open("probe.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=dir_fd)
    finally:
        os.close(dir_fd)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        holder: dict = {}
        if e.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
            try:
                holder = json.loads(os.pread(fd, 4096, 0).decode("utf-8") or "{}")
            except Exception:  # noqa: BLE001 - unreadable, not JSON, or nested too deeply (RecursionError)
                holder = {}
        os.close(fd)
        return None, holder
    os.ftruncate(fd, 0)
    os.pwrite(fd, json.dumps({"pid": os.getpid(), "since": dv._iso(dv._now())}).encode("utf-8"), 0)
    return fd, None


def _kill_group(proc: subprocess.Popen) -> None:
    """SIGTERM the child's process group, wait 2 s, then SIGKILL it."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


class _Interrupted(Exception):
    pass


def _drive_child(proc: subprocess.Popen, *, load_timeout: float, call_timeout: float,
                 interrupted: threading.Event, on_event: Callable[[dict], None]) -> dict:
    """Read the protocol until ``done``, a death, a deadline or an interrupt.

    Returns ``identity``, ``loaded``, ``load_failed``, ``calls`` (the call events),
    ``end`` (done | died | timeout), ``died_during`` (a call number, or "load"),
    ``returncode`` and ``since_last_s`` (time since the last event, for a row that
    never reported)."""
    sel = selectors.DefaultSelector()
    assert proc.stdout is not None
    fd = proc.stdout.fileno()
    os.set_blocking(fd, False)
    sel.register(fd, selectors.EVENT_READ)
    buf = b""
    st: dict[str, Any] = {"identity": None, "loaded": None, "load_failed": None, "calls": [], "end": None,
                          "died_during": None, "returncode": None, "since_last_s": 0.0}
    last = time.monotonic()
    deadline = last + load_timeout
    eof = False
    while True:
        if interrupted.is_set():
            raise _Interrupted()
        now = time.monotonic()
        if now >= deadline:
            st.update(end="timeout", died_during=(len(st["calls"]) + 1) if st["loaded"] else "load",
                      since_last_s=now - last)
            _kill_group(proc)
            break
        for _key, _mask in sel.select(timeout=max(0.0, min(0.25, deadline - now))):
            try:
                chunk = os.read(fd, 65536)
            except BlockingIOError:
                continue
            except OSError:
                chunk = b""
            if not chunk:
                eof = True
            buf += chunk
        while b"\n" in buf and st["end"] is None:
            raw, buf = buf.split(b"\n", 1)
            try:
                ev = json.loads(raw.decode("utf-8", "replace"))
            except (ValueError, RecursionError):
                continue
            if not isinstance(ev, dict) or ev.get("probe") != 1:
                continue                            # not ours: native noise that happened to look like JSON
            last = time.monotonic()
            kind = ev.get("ev")
            if kind == "identity":
                st["identity"] = ev
            elif kind == "loaded":
                st["loaded"] = ev
                deadline = last + call_timeout
            elif kind == "load_failed":
                st["load_failed"] = ev
                st["end"] = "done"
            elif kind == "call":
                st["calls"].append(ev)
                deadline = last + call_timeout
            elif kind == "done":
                st["end"] = "done"
            on_event(ev)
        if st["end"] is not None:
            break
        rc = proc.poll()
        if eof or rc is not None:
            if rc is None:
                try:
                    rc = proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    rc = None
            st.update(end="died", returncode=rc, since_last_s=time.monotonic() - last,
                      died_during=(len(st["calls"]) + 1) if st["loaded"] else "load")
            break
    sel.close()
    if st["end"] == "done":
        try:
            st["returncode"] = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
    return st


def _gather_stderr(proc: subprocess.Popen, tail: deque) -> threading.Thread:
    def pump() -> None:
        assert proc.stderr is not None
        for raw in iter(proc.stderr.readline, b""):
            tail.append(dv._clip(raw.decode("utf-8", "replace").rstrip("\n"), 300))
    t = threading.Thread(target=pump, name="deep-probe-stderr", daemon=True)
    t.start()
    return t


def _round(v: Any, n: int) -> Any:
    return round(float(v), n) if isinstance(v, (int, float)) and not isinstance(v, bool) else v


def _death_text(res: dict, where: Any, call_timeout: float, load_timeout: float) -> str:
    if res["end"] == "timeout":
        limit = load_timeout if where == "load" else call_timeout
        return f"did not finish within {limit:.0f} s; the probe stopped its child process"
    rc = res.get("returncode")
    if isinstance(rc, int) and rc < 0:
        how = "died (" + _signal_name(rc) + ")"
    elif isinstance(rc, int) and rc > 0:
        how = "exited with status " + str(rc)
    else:
        how = "exited without finishing"
    during = "while loading" if where == "load" else "during this call"
    return "the probe child " + how + " " + during + "; its last stderr lines are in the record"


# ── text rows ───────────────────────────────────────────────────────────────
_END_LABEL = {"answered": "ok", "error": "FAILED", "empty": "EMPTY", "timeout": "NO ANSWER",
              "crashed": "CRASHED"}


def _load_line(nums: _Nums, ev: dict) -> str:
    depth = ev.get("ring_depth")
    src = " (" + str(ev["ring_depth_source"]) + ")" if ev.get("ring_depth_source") else ""
    ring = ("ring depth " + nums("runtime.ring_depth", str(depth)) + src) if depth is not None else "ring depth unset"
    secs = nums("load.seconds", "%.1f s" % float(ev.get("seconds") or 0.0))
    return ("load      " + secs + " · " + ring + " · up to " + nums("runtime.max_tokens", str(MAX_TOKENS))
            + " new tokens per call")


def _call_line(nums: _Nums, r: dict, first_error: Optional[dict]) -> str:
    if r.get("prompt_tokens") is not None:
        tok = nums("calls[].prompt_tokens", str(r["prompt_tokens"])).rjust(5) + " tokens in"
    else:
        tok = "      tokens unknown"
    label = _END_LABEL.get(r["end"], r["end"])
    secs = nums("calls[].seconds", "%.1f s" % r["seconds"])
    if r["end"] == "answered":
        tail = nums("calls[].words_out", str(r["words_out"])) + " words"
    else:
        err = r.get("error") or ""
        if first_error and r["index"] != first_error.get("call") and r["end"] == "error" \
                and dv._clip(err, 300) == dv._clip(first_error.get("error"), 300):
            tail = "same error as call " + nums("first_error.call", str(first_error["call"]))
        else:
            tail = dv._clip(err, 160)
    return ("call %d/%d  %-19s%s   %-9s%7s   %s" % (r["index"], TOTAL_CALLS, r["kind"], tok, label, secs, tail)).rstrip()


def _rows_from_result(res: dict, loaded: Optional[dict], tokens: list, call_timeout: float,
                      load_timeout: float) -> list[dict]:
    from arail.deep_probe_prompts import PROMPTS
    by_index = {int(c["i"]): c for c in res["calls"]}
    rows = []
    for i, (kind, _p) in enumerate(PROMPTS, 1):
        row = {"index": i, "kind": kind, "prompt_tokens": tokens[i - 1] if i - 1 < len(tokens) else None,
               "ok": False, "seconds": 0.0, "words_out": 0, "error": None, "end": "not_reached"}
        c = by_index.get(i)
        if c is not None:
            row.update(ok=bool(c.get("ok")), seconds=round(float(c.get("s") or 0.0), 2),
                       words_out=int(c.get("words") or 0), error=c.get("error"),
                       end=c.get("end") or ("answered" if c.get("ok") else "error"))
        rows.append(row)
    di = res.get("died_during")
    if loaded and isinstance(di, int) and 1 <= di <= len(rows) and res["end"] in ("died", "timeout"):
        rows[di - 1].update(end="timeout" if res["end"] == "timeout" else "crashed",
                            seconds=round(float(res.get("since_last_s") or 0.0), 2),
                            error=_death_text(res, di, call_timeout, load_timeout))
    return rows


# ── JSON teaching ───────────────────────────────────────────────────────────
def _numeric_leaves(obj: Any, path: str = "") -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += _numeric_leaves(v, path + "." + k if path else k)
    elif isinstance(obj, list):
        for v in obj:
            out += _numeric_leaves(v, path + "[]")
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out.append((path, obj))
    return out


# Numbers that are labels, not measurements: file sizes, pids, the row index of a call.
IDENTIFIER_PATHS = frozenset({"binary.bytes", "binary.shadowed[].bytes", "calls[].index",
                              "memory.holders[].pid"})


def attach_teaching(doc: dict) -> dict:
    """Add the ``teaching`` object: one four-part entry per numeric leaf that is not an identifier."""
    t: dict[str, dict] = {}
    for path, _v in _numeric_leaves({k: v for k, v in doc.items() if k != "teaching"}):
        if path in IDENTIFIER_PATHS:
            continue
        if path not in TEACHING:
            raise KeyError("no teaching for numeric field " + repr(path))
        t[path] = dict(TEACHING[path])
    doc["teaching"] = t
    return doc


# ── the run ─────────────────────────────────────────────────────────────────
class ProbeRun:
    """What ``execute`` produced: the exit code, the JSON document, the text lines
    and the ``_Nums`` that rendered every number (a test seam for TQ1 and TQ2)."""

    def __init__(self, exit_code: int, doc: dict, lines: list[str], nums: _Nums) -> None:
        self.exit_code, self.doc, self.lines, self.nums = exit_code, doc, lines, nums


def execute(opts: dict, *, memory_reader: Optional[Callable[[], Optional[float]]] = None,
            holders_reader: Optional[Callable[[str], list]] = None, out: Any = None) -> ProbeRun:
    """Run the supervisor. In text mode the lines are written to ``out`` as they happen."""
    out = out or sys.stdout
    json_mode = bool(opts.get("json"))
    nums = _Nums()
    lines: list[str] = []
    t_start = time.monotonic()
    started_ts = dv._now()
    started_at = dv._iso(started_ts)

    def say(text: str) -> None:
        lines.append(text)
        nums.mark(len(lines) - 1)
        if not json_mode:
            out.write(text + "\n")
            out.flush()

    try:
        import arail.config  # noqa: F401 - loads .env and model_defaults so the model resolves as in the portal
    except Exception:  # noqa: BLE001
        pass
    from arail.deep_probe_prompts import PROMPT_SET_SHA256

    lab = dv._lab()
    py = sys.executable or "python"
    call_timeout = _env_float("ARAIL_DEEP_PROBE_CALL_TIMEOUT_S", DEFAULT_CALL_TIMEOUT_S)
    load_timeout = _env_float("ARAIL_DEEP_PROBE_LOAD_TIMEOUT_S", DEFAULT_LOAD_TIMEOUT_S)
    doc: dict[str, Any] = {
        "schema": SCHEMA, "outcome": "not_run", "exit_code": EXIT_NOT_RUN, "not_run_reason": None,
        "started_at": started_at, "finished_at": None, "duration_s": None, "python": py,
        "binary": None, "model": None, "runtime": None, "memory": None, "load": None, "calls": [],
        "passed": None, "total": None, "median_ok_s": None, "first_error": None, "stderr_tail": [],
        "verdict_store": {"path": str(dv.state_dir() / "verdict.json"), "written": False, "error": None,
                          "still_failing": False},
    }

    def done(code: int) -> ProbeRun:
        doc["exit_code"] = code
        doc["finished_at"] = doc["finished_at"] or dv._iso(dv._now())
        if doc["duration_s"] is None:
            doc["duration_s"] = round(time.monotonic() - t_start, 1)
        return ProbeRun(code, doc, lines, nums)

    def not_run(reason: str) -> ProbeRun:
        doc["not_run_reason"] = reason
        say("not run   " + reason)
        return done(EXIT_NOT_RUN)

    say("QueueLLM deep probe: " + nums("total", str(TOTAL_CALLS)) + " calls in a fresh process, through "
        "ARAIL's own deep backend. Token-free, no network.")
    say("python    " + py)

    state = dv.state_dir()
    try:
        lock_fd, holder = _take_probe_lock(state)
    except dv.StateDirRefused as e:
        return not_run("the probe lock could not be taken: " + str(e.strerror))
    except OSError as e:
        return not_run("the probe lock could not be taken (" + str(state) + ": " + str(e.strerror or e)
                       + ") — check the permissions of that folder")
    if lock_fd is None:
        who = ""
        if holder and holder.get("pid"):
            since = str(holder.get("since") or "")[11:19]
            who = " (pid " + str(holder["pid"]) + (", since " + since + " UTC" if since else "") + ")"
        return not_run("another deep probe is running" + who + " — wait for it")
    try:
        return _execute_locked(opts, nums, say, doc, done, not_run, memory_reader, holders_reader, json_mode,
                               py, lab, call_timeout, load_timeout, started_ts, started_at, t_start,
                               PROMPT_SET_SHA256, out)
    finally:
        try:
            os.close(lock_fd)
        except OSError:
            pass


def _execute_locked(opts, nums, say, doc, done, not_run, memory_reader, holders_reader, json_mode, py, lab,
                    call_timeout, load_timeout, started_ts, started_at, t_start, prompt_set_sha256, out):
    spec = dv._find_spec_safe()
    if spec is None:
        return not_run(dv.FROZEN_MODULE + " is not importable by " + py
                       + " — install QueueLLM first: ./arailctl deep install")
    native, why = dv.static_native(spec)
    resolves = getattr(spec, "origin", None) or ""
    kind = "package" if resolves.endswith("__init__.py") else "extension"
    say("import    " + dv.FROZEN_MODULE + " -> " + resolves + " (" + kind + ")")
    say("binary    " + (native if native else "(not known before loading: " + why + ")"))
    shadows = dv.shadowed(native)
    for s in shadows:
        say("shadowed  " + s["path"] + " · sha256 " + s["sha256"][:12] + "… · modified " + s["mtime"][:10]
            + " · on the path, never loaded")

    model = os.environ.get("AEROLLM_MODEL", "Qwen2.5-7B-Instruct-4bit")
    models_dir = os.environ.get("ARAIL_MODELS_DIR", "lab/models")
    model_path = model if os.path.isabs(model) else os.path.join(models_dir, model)
    if not os.path.isdir(model_path):
        return not_run("model dir missing: " + model_path + " — check AEROLLM_MODEL and ARAIL_MODELS_DIR")
    disk_gib = _gib(_dir_bytes(model_path))
    needed_gib = disk_gib + HEADROOM_GIB
    avail = (memory_reader or _default_memory_gib)()
    holders = (holders_reader or _lsof_holders)(native) if native else []
    disk_txt = "%.1f GiB" % disk_gib
    say("model     " + model + " · " + models_dir + " · " + nums("model.disk_gib", disk_txt) + " on disk")
    doc["model"] = {"name": model, "dir": models_dir, "disk_gib": round(disk_gib, 2)}
    doc["memory"] = {"available_gib": None if avail is None else round(avail, 1),
                     "needed_gib": round(needed_gib, 1), "holders": holders, "forced": bool(opts.get("force"))}
    if holders:
        h = holders[0]
        held = ("pid " + str(h["pid"]) + " (" + h["command"] + "…) already has this binary loaded; if it holds "
                "the model, this probe is a second copy (" + nums("model.disk_gib", disk_txt) + ") until it exits")
    else:
        held = "no other process has this binary loaded · this probe holds its own copy until it exits"
    if avail is None:
        say("memory    unknown (psutil is not installed) · " + held)
    else:
        avail_txt = "%.1f GiB" % avail
        say("memory    " + nums("memory.available_gib", avail_txt) + " available · " + held)
        if avail < needed_gib and not opts.get("force"):
            return not_run("not enough available memory for another copy: "
                           + nums("memory.available_gib", avail_txt) + " available, "
                           + nums("memory.needed_gib", "%.1f GiB" % needed_gib) + " needed ("
                           + nums("model.disk_gib", disk_txt) + " weights plus headroom) — "
                           "stop the lab or pass --force")

    # ── spawn the child and read its protocol ────────────────────────────────
    env = dict(os.environ)
    env.update({"ARAIL_DEEP_PROBE_CHILD": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    interrupted = threading.Event()
    old_handler = None
    try:
        old_handler = signal.signal(signal.SIGINT, lambda *_: interrupted.set())
    except ValueError:           # not the main thread (a test): no handler, same behaviour otherwise
        pass
    tail: deque = deque(maxlen=STDERR_TAIL_LINES)
    proc = subprocess.Popen([py, "-m", "arail.deep_probe", "--child"], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                            start_new_session=True, close_fds=True)
    pump = _gather_stderr(proc, tail)
    tokens: list = [None] * TOTAL_CALLS
    first_seen: dict[str, Any] = {"err": None}

    def on_event(ev: dict) -> None:
        k = ev.get("ev")
        if k == "loaded":
            tk = ev.get("prompt_tokens")
            if isinstance(tk, list):
                for idx, v in enumerate(tk[:TOTAL_CALLS]):
                    tokens[idx] = v
            say(_load_line(nums, ev))
        elif k == "call":
            i = int(ev["i"])
            row = {"index": i, "kind": ev.get("kind"), "prompt_tokens": tokens[i - 1] if 0 < i <= TOTAL_CALLS else None,
                   "ok": bool(ev.get("ok")), "seconds": round(float(ev.get("s") or 0.0), 2),
                   "words_out": int(ev.get("words") or 0), "error": ev.get("error"),
                   "end": ev.get("end") or ("answered" if ev.get("ok") else "error")}
            if not row["ok"] and first_seen["err"] is None:
                first_seen["err"] = {"call": i, "error": row["error"]}
            say(_call_line(nums, row, first_seen["err"]))

    try:
        res = _drive_child(proc, load_timeout=load_timeout, call_timeout=call_timeout,
                           interrupted=interrupted, on_event=on_event)
    except _Interrupted:
        _kill_group(proc)
        if not json_mode:
            out.write("\ninterrupted: the probe child was stopped and nothing was recorded\n")
            out.flush()
        pr = done(EXIT_INTERRUPTED)
        pr.doc["outcome"] = "interrupted"
        return pr
    finally:
        if proc.poll() is None:
            _kill_group(proc)
        if old_handler is not None:
            try:
                signal.signal(signal.SIGINT, old_handler)
            except ValueError:
                pass
    pump.join(timeout=2)
    doc["stderr_tail"] = list(tail)

    # ── build the result ──────────────────────────────────────────────────────
    ident = res["identity"]
    if ident is None and native:
        try:
            b = dv._make_binary(native, resolves, "unknown", pinned=False)
            ident = {"module": b.module, "resolves_to": b.resolves_to, "native_path": b.native_path,
                     "sha256": b.sha256, "bytes": b.bytes, "mtime": b.mtime, "version": b.version}
        except OSError:
            ident = None
    if ident is None:
        return not_run("the binary could not be identified — see the lines above")
    doc["binary"] = {**{k: ident.get(k) for k in ("module", "resolves_to", "native_path", "sha256", "bytes",
                                                  "mtime", "version")},
                     "shadowed": [{"path": s["path"], "sha256": s["sha256"], "mtime": s["mtime"]} for s in shadows]}
    loaded = res["loaded"]
    if loaded and isinstance(loaded.get("prompt_tokens"), list):
        for idx, v in enumerate(loaded["prompt_tokens"][:TOTAL_CALLS]):
            tokens[idx] = v
    doc["runtime"] = {"ring_depth": (loaded or {}).get("ring_depth"),
                      "ring_depth_source": (loaded or {}).get("ring_depth_source"),
                      "kv_budget_gib": _round((loaded or {}).get("kv_budget_gib"), 2),
                      "max_tokens": MAX_TOKENS, "temperature": TEMPERATURE,
                      "prompt_set_sha256": prompt_set_sha256}
    died_in_load = res["end"] in ("died", "timeout") and res["died_during"] == "load"
    load_err = (res["load_failed"] or {}).get("error") or (
        _death_text(res, "load", call_timeout, load_timeout) if died_in_load else None)
    doc["load"] = {"ok": bool(loaded),
                   "seconds": _round((loaded or {}).get("seconds") if loaded else time.monotonic() - t_start, 2),
                   "error": load_err}
    rows = _rows_from_result(res, loaded, tokens, call_timeout, load_timeout)
    attempted = [r for r in rows if r["end"] != "not_reached"]
    doc["calls"] = rows if loaded else []
    ok_secs = [r["seconds"] for r in rows if r["ok"]]
    passed = len(ok_secs)
    first = next((r for r in rows if not r["ok"]), None)
    if not loaded:
        first_error = {"call": None, "kind": "load", "error": dv._clip(load_err or "the runtime did not load", 300)}
    elif first is not None:
        first_error = {"call": first["index"], "kind": first["kind"], "error": dv._clip(first["error"] or "no answer", 300)}
    else:
        first_error = None
    full = bool(loaded) and passed == TOTAL_CALLS
    doc.update(passed=passed, total=len(attempted) if loaded else 0, first_error=first_error,
               median_ok_s=round(statistics.median(ok_secs), 2) if ok_secs else None,
               outcome="pass" if full else "fail")

    if not json_mode:
        if not loaded:
            say("load      FAILED · " + dv._clip(load_err, 200))
        for r in rows:
            if r["end"] in ("timeout", "crashed"):      # these never reported, so no event printed them
                say(_call_line(nums, r, first_seen["err"]))

    # ── record it ──────────────────────────────────────────────────────────────
    written, store_err = dv.record_probe({
        "binary": {k: doc["binary"][k] for k in ("module", "native_path", "sha256", "bytes", "version")},
        "outcome": doc["outcome"], "passed": passed, "total": doc["total"], "median_ok_s": doc["median_ok_s"],
        "first_error": first_error if doc["outcome"] == "fail" else None,
        "started_ts": started_ts, "started_at": started_at, "at": dv._iso(dv._now()),
        "model": model, "ring_depth": doc["runtime"]["ring_depth"],
        "ring_depth_source": doc["runtime"]["ring_depth_source"], "max_tokens": MAX_TOKENS,
        "prompt_set_sha256": prompt_set_sha256, "python": py, "lab": lab,
    })
    still_failing = False
    if written and doc["outcome"] == "pass":
        still_failing = (dv.record_for(doc["binary"]["sha256"]) or {}).get("failure") is not None
    doc["verdict_store"].update(written=bool(written), error=store_err, still_failing=still_failing)
    doc["duration_s"] = round(time.monotonic() - t_start, 1)
    _summary_lines(doc, nums, say)
    sha12 = doc["binary"]["sha256"][:12]
    if doc["outcome"] == "fail":
        where = ("; first error at call " + str(first_error["call"])) if first_error.get("call") else ""
        dv._emit("warn", "Deep probe: %d/%d passed on build %s — generation failing%s: %s. (5/5 is the bar)"
                 % (passed, doc["total"], sha12, where, dv._clip(first_error["error"], 120)))
    elif still_failing:
        dv._emit("warn", "Deep probe: 5/5 passed on build %s, but a deep call failed elsewhere while the probe "
                         "ran, so deep is still failing." % sha12)
    elif not written:
        dv._emit("warn", "Deep probe: 5/5 passed on build %s, but the result could not be saved (%s), so nothing "
                         "was recorded and a failure already on record is not cleared."
                 % (sha12, dv._clip(store_err, 100)))
    else:
        dv._emit("success", "Deep probe: 5/5 passed on build %s — median %.1f s. Deep is cleared for agents "
                            "and the Curator." % (sha12, doc["median_ok_s"] or 0.0))
    return done(EXIT_PASS if (full and written and not still_failing) else EXIT_FAIL)


def _summary_lines(doc: dict, nums: _Nums, say: Callable[[str], None]) -> None:
    passed, total = doc["passed"], doc["total"]
    p = nums("passed", str(passed)) + "/" + nums("total", str(total))
    if doc["outcome"] == "pass":
        say("result    " + p + " passed. Deep generation answered every call on this binary.")
    else:
        stop = ""
        if total != TOTAL_CALLS and doc["load"]["ok"]:
            stop = " The probe stopped early: only " + nums("total", str(total)) + " of the planned calls ran."
        say("result    " + p + " passed. Deep generation is FAILING on this binary." + stop)
    if doc["median_ok_s"] is not None:
        say("median    " + nums("median_ok_s", "%.1f s" % doc["median_ok_s"]) + " over the "
            + nums("passed", str(passed)) + " call" + ("s" if passed != 1 else "") + " that answered")
    fe = doc.get("first_error")
    if fe:
        if fe.get("call"):
            say("first     call " + nums("first_error.call", str(fe["call"])) + " (" + fe["kind"] + "): "
                + dv._clip(fe["error"], 160))
        else:
            say("first     " + fe["kind"] + ": " + dv._clip(fe["error"], 160))
    vs = doc["verdict_store"]
    if not vs["written"]:
        say("recorded  NOT SAVED (" + str(vs["error"]) + ") — status surfaces will not show this result"
            + (", and the probe exits non-zero: a failure already on record is not cleared by a pass that "
               "was not recorded" if doc["outcome"] == "pass" else ""))
    elif vs.get("still_failing"):
        say("recorded  " + vs["path"] + " · a deep call failed elsewhere while the probe ran; still failing")
    elif doc["outcome"] == "pass":
        say("recorded  passing · " + vs["path"] + " · deep is cleared for agents and the Curator")
    else:
        say("recorded  failing · " + vs["path"] + " · agents and the Curator use the fast model until "
            "./arailctl deep probe passes " + nums("passed", "5") + "/" + nums("total", "5") + " on this build")


def run(argv: list[str], *, memory_reader: Optional[Callable[[], Optional[float]]] = None,
        holders_reader: Optional[Callable[[str], list]] = None, out: Any = None) -> int:
    out = out or sys.stdout
    try:
        opts = _parse(argv)
    except _Usage as e:
        sys.stderr.write("deep probe: " + str(e) + "\n" + _USAGE)
        return EXIT_USAGE
    if opts["help"]:
        out.write(_USAGE)
        return EXIT_PASS
    if opts["child"]:
        return _child_main()
    try:
        pr = execute(opts, memory_reader=memory_reader, holders_reader=holders_reader, out=out)
    except Exception as e:  # noqa: BLE001
        # A bug in the probe is not a verdict about the binary: say so, record nothing, exit 5.
        import traceback
        traceback.print_exc()
        msg = "the probe itself failed (" + type(e).__name__ + ": " + dv._clip(e, 160) + ") — nothing was recorded"
        if opts["json"]:
            out.write(json.dumps({"schema": SCHEMA, "outcome": "not_run", "exit_code": EXIT_NOT_RUN,
                                  "not_run_reason": msg, "teaching": {"exit_code": dict(TEACHING["exit_code"])}},
                                 indent=2) + "\n")
        else:
            out.write("not run   " + msg + "\n")
        out.flush()
        return EXIT_NOT_RUN
    if pr.exit_code == EXIT_INTERRUPTED:
        return EXIT_INTERRUPTED
    if opts["json"]:
        attach_teaching(pr.doc)
        out.write(json.dumps(pr.doc, ensure_ascii=False, indent=2) + "\n")
    else:
        for ln in legend_lines(pr.nums.used):
            out.write(ln + "\n")
    out.flush()
    return pr.exit_code


def main(argv: Optional[list[str]] = None) -> int:
    return run(list(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess tests
    raise SystemExit(main())
