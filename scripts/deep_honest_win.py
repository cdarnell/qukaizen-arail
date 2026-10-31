#!/usr/bin/env python3
"""The one live check of sprint 2026-10-29-arail-deep-honest (driver for scripts/deep-honest-win.sh).

Run it through scripts/deep-honest-win.sh, which builds the scratch root and the scratch
.env first. This file runs rows W0 to W8, each in its own fresh process, and prints one
table with a teaching block for every row.

What it checks, on a Mac that has the real model and a broken wheel: when QueueLLM answers
call 1 and fails every later call in the process, ARAIL says so on every surface and never
says deep is fine or the work done. It needs the real wheel and the real model, so it is not
part of pytest. A dry run against the fake runtime is (tests/test_deep_honest_win.py).

It never changes the python it is pointed at. Every row runs with PYTHONDONTWRITEBYTECODE=1,
scratch folders for all state and a scratch .env that holds four non-secret keys. The portal
is driven in process by an ASGI client on one event loop, with no lifespan and no port, so
the consumer under test is the only deep caller. Token-free: nothing leaves this machine.

Exit 0 only when every row passes. A row that could not test its claim is INCONCLUSIVE, and
that is not a pass.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import textwrap
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

ROW_IDS = ("W0", "W1", "W2", "W3", "W4", "W5", "W6", "W7", "W8")
PASS, FAIL, INCONCLUSIVE, NOT_RUN = "PASS", "FAIL", "INCONCLUSIVE", "NOT RUN"
SFS = {"sec-fetch-site": "same-origin"}          # what a browser adds; a bearer needs none
FOUR_KEYS = ("AEROLLM_MODEL", "ARAIL_MODELS_DIR", "AEROLLM_KV_BUDGET_PCT", "LAB_TIER")
DEFAULT_ROW_TIMEOUT_S = 900.0
DEFAULT_PATH = "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"

SHORT = {
    "W0": "preflight",
    "W1": "Chat x5",
    "W2": "agents x5",
    "W3": "Curator review",
    "W4": "Curator grow",
    "W5": "deep probe",
    "W6": "fresh process",
    "W7": "build script",
    "W8": "postflight",
}
TITLES = {
    "W0": "preflight: what is installed and what the model folder holds",
    "W1": "Chat, five turns on the deep backend",
    "W2": "background agents, five calls (Buddy 2, debt advisor 2, consolidation analyzer 1)",
    "W3": "Curator review of a scratch 6-term World",
    "W4": "Curator grow of the same World",
    "W5": "./arailctl deep probe --json on a fresh state folder",
    "W6": "a fresh process on W5's state: every surface, then Check",
    "W7": "deep status, and the build script's refusals (deep rebuild and auto)",
    "W8": "postflight: nothing of the operator's changed",
}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------
@dataclass
class Check:
    name: str
    ok: bool
    observed: str
    expected: str
    silent: bool = False        # a failed check of this kind is a "silent outcome"


@dataclass
class RowResult:
    row: str
    status: str = FAIL
    attempted: Optional[int] = None
    failed: Optional[int] = None
    checks: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    reason: str = ""
    extra: dict = field(default_factory=dict)
    seconds: float = 0.0

    def check(self, name: str, ok: bool, observed: Any, expected: Any, *, silent: bool = False) -> bool:
        self.checks.append(Check(name, bool(ok), str(observed), str(expected), silent))
        return bool(ok)

    @property
    def silent(self) -> int:
        return sum(1 for c in self.checks if c.silent and not c.ok)

    def to_json(self) -> dict:
        d = asdict(self)
        d["silent"] = self.silent
        return d

    @staticmethod
    def from_json(d: dict) -> "RowResult":
        r = RowResult(row=d["row"])
        for k in ("status", "attempted", "failed", "evidence", "reason", "extra", "seconds"):
            if k in d:
                setattr(r, k, d[k])
        r.checks = [Check(**c) for c in d.get("checks", [])]
        return r


class Inconclusive(Exception):
    """A precondition of the row failed, so the row cannot test its claim."""


# ---------------------------------------------------------------------------
# Teaching. Every number in the table has four parts: what it means, the bar and why,
# what out of range means, and where the fault lives. The bar is per row; the other
# three are the same for every row that shows the number.
# ---------------------------------------------------------------------------
NUMBERS = ("attempted", "failed", "silent")
NUMBER_NAME = {"attempted": "deep calls attempted", "failed": "deep calls failed", "silent": "silent outcomes"}
PARTS = ("meaning", "threshold", "out_of_range", "failure_lives_in")

COMMON = {
    "attempted": {
        "meaning": "deep (QueueLLM) generation calls the row's consumers made in the row's own fresh "
                   "process, counted by ARAIL's per-process deep counters, not by this script",
        "out_of_range": "more than the bar: ARAIL kept calling a build it already knew was failing, so a "
                        "gate did not close. fewer: a call that should have run did not, so the row did "
                        "not test what it claims",
        "failure_lives_in": "ARAIL: the gate (deep_policy.explain) and the backend hook "
                            "(router/backends.py), not QueueLLM",
    },
    "failed": {
        "meaning": "of those calls, the ones that raised an error or returned no text",
        "out_of_range": "fewer than the bar: the wheel behaves differently from DAY1.json (re-baseline it) "
                        "or an error was swallowed before it was counted. more: a new fault, such as "
                        "call 1 failing too",
        "failure_lives_in": "the failures themselves live in the QueueLLM binary (DAY1.json: mlx-native "
                            "Qwen2 prefill, an rms_norm weight of 1 element); a failure that was not "
                            "counted lives in ARAIL's backend hook",
    },
    "silent": {
        "meaning": "places where ARAIL said deep was fine, or the work done, when it was not: a surface "
                   "that does not say failing, a reply or a log line that hides an error",
        "out_of_range": "1 or more: the check lines of the row name the row and the surface that said it. "
                        "Any one is the bug this sprint fixes",
        "failure_lives_in": "the code of the surface a failed check names, not QueueLLM",
    },
}
ZERO_BAR = ("0, because any one is the bug this sprint fixes: ARAIL said deep was fine, or the work done, "
            "when it was not")


def _na(reason: str) -> tuple:
    return ("n/a", reason)


BARS: dict = {
    "W0": {
        "attempted": "0: the preflight reads files and git state only. A deep call here would be recorded "
                     "as a failure and spoil the rows after it",
        "failed": "0, for the same reason: no call, no failure",
        "silent": _na("the preflight only records what is installed; it states nothing about deep that "
                      "could be wrong"),
    },
    "W1": {
        "attempted": "5: Chat is the operator's own pick, so every turn calls deep, even after a failure. "
                     "It never falls back to the fast model on its own",
        "failed": "4: calls 2 to 5. The day-1 wheel answers once per process (DAY1.json)",
        "silent": ZERO_BAR,
    },
    "W2": {
        "attempted": "2: call 1 answers, call 2 fails and is recorded, then deep is marked failing and "
                     "calls 3 to 5 do not try it. 5 would mean agents kept calling a build ARAIL knew "
                     "was failing",
        "failed": "1: only call 2 meets the bug; after it the agents stop calling deep",
        "silent": ZERO_BAR,
    },
    "W3": {
        "attempted": "6: one judge call per term, and the World has 6 terms (the Curator reviews up to "
                     "16 per pass)",
        "failed": "5: calls 2 to 6. The review must say judged 1 of 6, not 'All clear'",
        "silent": ZERO_BAR,
    },
    "W4": {
        "attempted": "2 or more: a growth pass reviews terms and then proposes new ones, so the count "
                     "depends on the World. The bar is that every call is counted",
        "failed": "the calls minus 1: the wheel answers once per process, so every call after the first "
                  "fails, and a pass with any failed call must change nothing (state not_applied)",
        "silent": ZERO_BAR,
    },
    "W5": {
        "attempted": "5: the probe makes five sequential calls in one fresh process (its bar and its "
                     "reason are in the probe's own output)",
        "failed": "4: calls 2 to 5, as in DAY1.json. The probe must exit 1 and record it",
        "silent": ZERO_BAR,
    },
    "W6": {
        "attempted": "0: a fresh process that only reads state and presses Check must not generate. A "
                     "call here would spend tokens on a build known to fail",
        "failed": "0, for the same reason: no call, no failure",
        "silent": ZERO_BAR,
    },
    "W7": {
        "attempted": _na("the build and status scripts make no deep call, and a call made in a "
                         "subprocess cannot be counted from here"),
        "failed": _na("the build and status scripts make no deep call, and a call made in a "
                      "subprocess cannot be counted from here"),
        "silent": "0: none of five false claims may appear in the three outputs: the old 'No QueueLLM "
                  "sibling repo' and 'QueueLLM ready' lines, the two '(... channel)' guesses, and a "
                  "channel line that names release or dev without proof",
    },
    "W8": {
        "attempted": _na("the postflight compares files and git state; it generates nothing"),
        "failed": _na("the postflight compares files and git state; it generates nothing"),
        "silent": _na("the postflight is a comparison with the preflight, not a statement about deep"),
    },
}


def teaching_for(row: str) -> dict:
    """All three numbers of a row, four non-empty parts each."""
    out: dict = {}
    for num in NUMBERS:
        bar = BARS[row][num]
        if isinstance(bar, tuple):
            out[num] = {
                "meaning": "not counted in this row",
                "threshold": bar[1],
                "out_of_range": "not applicable: the cell shows - and there is nothing to range-check",
                "failure_lives_in": "not applicable: nothing is measured here",
            }
        else:
            c = COMMON[num]
            out[num] = {"meaning": c["meaning"], "threshold": bar, "out_of_range": c["out_of_range"],
                        "failure_lives_in": c["failure_lives_in"]}
    return out


TIME_TEACHING = {
    "meaning": "wall time of the whole check, from the first row to the last",
    "threshold": "under 5 minutes: nine rows, and the longest are the model loads and the probe (the first two "
                 "live runs took 64 s and 141 s)",
    "out_of_range": "much longer: a row waited for a deadline, which means a call hung",
    "failure_lives_in": "the slowest row; its log is in the scratch root when ARAIL_WIN_KEEP=1",
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def say(text: str = "") -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def line_with(text: str, needle: str) -> str:
    """The first line that holds ``needle``, else the last line, clipped: what a check saw."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in lines:
        if needle in ln:
            return clip(ln, 110)
    return clip(lines[-1], 110) if lines else "(no output)"


def clip(text: Any, limit: int = 160) -> str:
    s = " ".join(str(text).split())
    return s if len(s) <= limit else s[: limit - 3] + "..."


_CURRENT: list = []          # the subprocess the orchestrator is waiting on, for the signal handler


def kill_group(proc: subprocess.Popen) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:
            continue


def run(argv: list, *, env: dict, timeout: float, cwd: Optional[str] = None) -> tuple:
    """(returncode, stdout, stderr, timed_out). An argv list, never a shell string. A new session, so a
    timeout can end the whole group."""
    proc = subprocess.Popen(argv, env=env, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, errors="replace", start_new_session=True)
    _CURRENT.append(proc)
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out, err, False
    except subprocess.TimeoutExpired:
        kill_group(proc)
        out, err = proc.communicate()
        return (proc.returncode if proc.returncode is not None else -9), out or "", err or "", True
    finally:
        if proc in _CURRENT:
            _CURRENT.remove(proc)


def git(repo: str, *args: str) -> tuple:
    """(returncode, stdout) of a read-only git command. --no-optional-locks keeps `status` from
    refreshing the index of a checkout this script must not touch."""
    try:
        p = subprocess.run(["git", "--no-optional-locks", "-C", repo, *args], capture_output=True, text=True,
                           errors="replace", timeout=120)
        return p.returncode, p.stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


def parse_env_file(path: Path) -> dict:
    out: dict = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip("'\"")
    except OSError:
        pass
    return out


# ---------------------------------------------------------------------------
# The orchestrator: one fresh child process per row, then the table
# ---------------------------------------------------------------------------
def load_day1(repo: Path) -> dict:
    """The expected wheel, read from the day-1 record so no hash is typed in twice."""
    p = repo / "sprints" / "2026-10-29-arail-deep-honest" / "DAY1.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        w = d["runs"]["live_venv_wheel"]
        return {"sha256": w["native_sha256"], "version": w["version"], "arail_base": d["pins"]["arail"]}
    except (OSError, KeyError, ValueError):
        return {}


def build_config(a: argparse.Namespace) -> dict:
    repo = Path(a.repo).resolve()
    day1 = load_day1(repo)
    return {
        "root": str(Path(a.root).resolve()),
        "repo": str(repo),
        "python": a.python,
        "queuellm": a.queuellm,
        "expect_sha256": a.expect_sha256 or day1.get("sha256", ""),
        "expect_version": a.expect_version or day1.get("version", ""),
        "dac_base": a.dac_base or day1.get("arail_base", ""),
        "arailctl": str(repo / "arailctl"),
        "build_script": str(repo / "scripts" / "build-aerollm.sh"),
        "row_timeout": float(a.row_timeout),
        "path": a.path,
    }


def row_env(cfg: dict, a: argparse.Namespace, row: str, password: str) -> dict:
    """The whole environment of a row. Nothing is inherited, so an AERO_* or ARAIL_* variable in the
    operator's shell cannot change what is measured. W6 and W7 read W5's verdict folder."""
    root, repo = Path(cfg["root"]), cfg["repo"]
    d = root / row
    state_row = "W5" if row in ("W6", "W7") else row
    env = {
        "PATH": cfg["path"],
        "HOME": str(root / "home"),
        "TMPDIR": str(root / "tmp"),
        "LANG": "en_US.UTF-8",
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join([*a.pythonpath_prepend, repo + "/src", repo]),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "LAB_MODE": "airgapped",
        "ARAIL_EDGE_PORT": "0",
        "ARAIL_PASSWORD": password,
        "ARAIL_ALLOWED_HOSTS": "testserver",
        "ARAIL_DATA_DIR": str(d / "data"),
        "LAB_ROOT": str(d / "lab"),
        "LAB_PKB": str(d / "pkb"),
        "ARAIL_WORLDS_DIR": str(d / "worlds"),
        "ARAIL_DEEP_STATE_DIR": str(root / state_row / "deep"),
        "ARAIL_ENV_FILE": str(d / "env"),
        "ARAIL_MODEL_DEFAULTS_FILE": str(d / "no-model-defaults.json"),     # absent on purpose
        "ARAIL_AEROLLM_REPO": cfg["queuellm"],
        "PYTHON": cfg["python"],                                            # what ./arailctl deep ... runs
        # Tier 0 (the fast model) is what agents fall back to. A .env with only the four copied keys
        # seeds it as an mlx backend that has no router on many machines, and then no fast call is
        # made and no stamp can be seen. Pointing it at Ollama's native backend, with a model name
        # that does not exist, means a fast call is always made and traced, answers or not, and the
        # operator's own Ollama model is never loaded.
        "MODEL_BACKEND": "ollama_native",
        "MODEL_NAME": "arail-deep-honest-no-such-model",
    }
    for kv in a.extra_env:
        k, _, v = kv.partition("=")
        env[k] = v
    return env


def prepare_row(cfg: dict, row: str, env_src: Path) -> Path:
    d = Path(cfg["root"]) / row
    for sub in ("data", "lab", "pkb", "worlds"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    env_file = d / "env"
    env_file.write_text(env_src.read_text(encoding="utf-8"), encoding="utf-8")
    os.chmod(env_file, 0o600)
    return d


def run_row(cfg: dict, a: argparse.Namespace, row: str, password: str) -> RowResult:
    root = Path(cfg["root"])
    d = prepare_row(cfg, row, Path(a.env_from))
    cmd = [cfg["python"], str(Path(__file__).resolve()), "--child", row, "--config", str(root / "config.json")]
    t0 = time.monotonic()
    rc, out, err, timed_out = run(cmd, env=row_env(cfg, a, row, password), timeout=cfg["row_timeout"], cwd=str(d))
    (d / "row.log").write_text(out + "\n--- stderr ---\n" + err, encoding="utf-8")
    rp = d / "result.json"
    res: Optional[RowResult] = None
    if rp.exists():
        try:
            res = RowResult.from_json(json.loads(rp.read_text(encoding="utf-8")))
        except (ValueError, KeyError, TypeError):
            res = None
    if res is None:
        res = RowResult(row=row, status=FAIL)
        if timed_out:
            res.reason = ("the row did not finish in %d s and was stopped: a deep call that never returns "
                          "would hang its consumer the same way" % int(cfg["row_timeout"]))
        else:
            res.reason = "the row process ended with code %s and left no result" % rc
        tail = (err.strip().splitlines() or out.strip().splitlines())[-6:]
        res.evidence += ["log tail: " + clip(line, 200) for line in tail]
    res.seconds = time.monotonic() - t0
    return res


# ---------------------------------------------------------------------------
# Rendering: the row blocks, the table, the teaching, the verdict
# ---------------------------------------------------------------------------
def print_row(res: RowResult) -> None:
    say("")
    say("%s  %s  [%s]" % (res.row, TITLES[res.row], res.status))
    for line in res.evidence:
        say("      " + line)
    for c in res.checks:
        mark = "ok  " if c.ok else "FAIL"
        tail = "" if c.ok else "   (expected: %s)" % c.expected
        say("      [%s] %s: %s%s" % (mark, c.name, c.observed, tail))
    if res.reason:
        say("      " + res.reason)


def silent_applies(row: str) -> bool:
    return not isinstance(BARS[row]["silent"], tuple)


def cell(v: Any) -> str:
    return "-" if v is None else str(v)


def row_numbers(r: RowResult) -> dict:
    ran = r.status != NOT_RUN
    return {"attempted": r.attempted if ran else None, "failed": r.failed if ran else None,
            "silent": (r.silent if (ran and silent_applies(r.row)) else None)}


def render_table(results: list) -> list:
    lines = ["", "SUMMARY", ""]
    head = "%-4s %-15s %9s %7s %7s  %s" % ("row", "check", "attempted", "failed", "silent", "result")
    lines += [head, "-" * len(head)]
    for r in results:
        n = row_numbers(r)
        lines.append("%-4s %-15s %9s %7s %7s  %s" % (r.row, SHORT[r.row], cell(n["attempted"]), cell(n["failed"]),
                                                      cell(n["silent"]), r.status))
    lines.append("")
    lines.append("attempted = deep calls attempted, failed = deep calls failed, silent = silent outcomes.")
    lines.append("Every number is taught below, row by row.")
    return lines


def _wrap(prefix: str, label: str, text: str, width: int = 100) -> list:
    return textwrap.wrap("%s: %s" % (label, text), width=width, initial_indent=prefix,
                         subsequent_indent=prefix + "    ", break_on_hyphens=False)


def render_teaching(results: list) -> list:
    lines = ["", "WHAT THE NUMBERS MEAN", "Each number has four parts: what it is, the bar and why, what out of range means, "
             "and where the fault lives."]
    labels = {"meaning": "what it is", "threshold": "the bar and why", "out_of_range": "out of range",
              "failure_lives_in": "the fault lives in"}
    for r in results:
        t, n = teaching_for(r.row), row_numbers(r)
        lines += ["", "%s  %s" % (r.row, TITLES[r.row])]
        for num in NUMBERS:
            lines.append("    %s = %s" % (NUMBER_NAME[num], cell(n[num])))
            for part in PARTS:
                lines += _wrap("        ", labels[part], t[num][part])
    return lines


def render_verdict(results: list, total_s: float) -> tuple:
    bad = [r for r in results if r.status != PASS]
    lines = ["", "RESULT"]
    if not bad:
        full = len(results) == len(ROW_IDS)
        lines.append("%d of %d rows passed.%s" % (len(results), len(results),
                     " Every surface said failing and nothing said deep was fine." if full
                     else " This was a partial run (--only), so it proves only these rows."))
    else:
        lines.append("%d of %d rows did not pass: %s." % (len(bad), len(results),
                                                          ", ".join("%s %s" % (r.row, r.status) for r in bad)))
    lines += _wrap("    ", "what it is", "rows that passed, out of the rows run")
    lines += _wrap("    ", "the bar and why", "all of them: one row that does not pass means ARAIL still hides a failure, "
                                              "or the check could not look")
    lines += _wrap("    ", "out of range", "a FAIL names a row and a surface that said deep was fine or done. An "
                                           "INCONCLUSIVE row could not test its claim and counts as not passed. NOT RUN "
                                           "means an earlier row stopped the run")
    lines += _wrap("    ", "the fault lives in", "the row's check lines say which surface; the code of that surface")
    lines.append("")
    lines.append("total time %d s" % round(total_s))
    for part, label in zip(PARTS, ("what it is", "the bar and why", "out of range", "the fault lives in")):
        lines += _wrap("    ", label, TIME_TEACHING[part])
    return (0 if not bad else 1), lines


def summary_json(results: list, total_s: float, code: int) -> dict:
    rows = []
    for r in results:
        d = r.to_json()
        d["title"] = TITLES[r.row]
        d["teaching"] = teaching_for(r.row)
        rows.append(d)
    return {"schema": "arail.deep-honest-win/v1", "rows": rows, "total_seconds": round(total_s, 1),
            "total_time_teaching": TIME_TEACHING, "exit_code": code}


def orchestrate(a: argparse.Namespace) -> int:
    cfg = build_config(a)
    root = Path(cfg["root"])
    for sub in ("home", "tmp"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "config.json").write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    wanted = [r for r in ROW_IDS if not a.only or r in a.only]
    password = secrets.token_urlsafe(24)

    def _stop(signum: int, _frame: Any) -> None:
        for p in list(_CURRENT):
            kill_group(p)
        sys.stderr.write("\ninterrupted: the running row was stopped\n")
        raise SystemExit(130)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    started = time.monotonic()
    say("Deep honest check. Token-free: local only, nothing installed, nothing downloaded.")
    say("python %s" % cfg["python"])
    say("checkout %s" % cfg["repo"])
    say("scratch %s (removed at the end)" % cfg["root"])
    results: list = []
    stopped_by: Optional[str] = None
    for row in wanted:
        if stopped_by and row != "W8":
            res = RowResult(row=row, status=NOT_RUN, reason="not run: %s" % stopped_by)
        else:
            say("")
            say("running %s %s ..." % (row, SHORT[row]))
            res = run_row(cfg, a, row, password)
            if row == "W0" and res.status != PASS:
                stopped_by = "W0 is %s, so the rows that need the wheel and the model cannot run" % res.status
        results.append(res)
        print_row(res)
    total = time.monotonic() - started
    code, verdict = render_verdict(results, total)
    for line in render_table(results) + render_teaching(results) + verdict:
        say(line)
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(summary_json(results, total, code), indent=2) + "\n", encoding="utf-8")
    return code


# ---------------------------------------------------------------------------
# Child side: one row, in a fresh process
# ---------------------------------------------------------------------------
DONE_STATES = ("done", "partial", "failed", "error", "not_applied", "cancelled")
CATS = [{"id": "x", "label": "X"}, {"id": "y", "label": "Y"}]
AGENT_IDS = ("buddy", "debt_advisor", "consolidation_analyzer")


class Portal:
    """The portal app driven in process: an ASGI client on a loop of its own, no lifespan, no port.

    Not TestClient, for two reasons. A plain TestClient gives every request a loop of its own, and the
    Curator's background task dies with the request that started it. `with TestClient(app)` keeps one
    loop but runs the lifespan, which starts the portal's own deep callers and breaks the rule that the
    consumer under test is the only one (TD7). httpx.ASGITransport, which TestClient is built on, with
    one loop in a thread has neither problem. Requests still pass the real middleware: the host
    allow-list, the bearer check and the CSRF gates."""

    def __init__(self, token: str) -> None:
        import asyncio
        import httpx
        from arail.portal.app import app
        self._asyncio = asyncio
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="win-portal", daemon=True)
        self._thread.start()
        self._client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver",
                                         headers={"Authorization": "Bearer " + token}, timeout=None)

    def request(self, method: str, url: str, **kw: Any):
        fut = self._asyncio.run_coroutine_threadsafe(self._client.request(method, url, **kw), self._loop)
        return fut.result()

    def get(self, url: str, **kw: Any):
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: Any):
        return self.request("POST", url, **kw)

    @staticmethod
    def body(resp: Any) -> dict:
        try:
            out = resp.json()
            return out if isinstance(out, dict) else {"_value": out}
        except ValueError:
            return {"_http": resp.status_code, "_text": resp.text[:200]}


def open_portal() -> Portal:
    from arail.portal import auth
    auth.ensure_token()
    return Portal(auth.token_path().read_text(encoding="ascii").strip())


def deep_counts() -> tuple:
    """(attempted, failed): deep calls this process made, counted by the backend hook."""
    from arail import deep_verdict as dv
    c = dv.counters()
    return int(c["ok"]) + int(c["failed"]), int(c["failed"])


def set_counts(res: RowResult) -> tuple:
    res.attempted, res.failed = deep_counts()
    return res.attempted, res.failed


def activity_messages() -> list:
    from arail.activity import activity_log
    return [str(e.get("message", "")) for e in activity_log.recent(500)]


def terms(n: int) -> list:
    return [{"slug": "term-%d" % i, "term": "Term %d" % i, "category": "x", "short": "s", "definition": "d",
             "example": "e", "related": [], "source": "model:local"} for i in range(1, n + 1)]


def mount_world(n: int = 6, slug: str = "mathworld") -> Path:
    """A sealed scratch World, built by the test builder and mounted in this row's folders."""
    from arail import config, world_mount as wm
    from tests.world_bundle_builder import make_bundle
    parent = Path(os.environ["ARAIL_DATA_DIR"]).parent / "bundle"
    bundle = make_bundle(parent, slug=slug, display_name="Math World", terms_list=terms(n), categories=CATS)
    wm.mount(bundle, data_dir=Path(config.DATA_DIR), pkb_root=Path(config.PKB_ROOT),
             worlds_dir=Path(config.WORLDS_DIR))
    return Path(config.WORLDS_DIR) / slug


def wait_done(portal: Portal, url: str, secs: float = 600.0) -> dict:
    end = time.monotonic() + secs
    body: dict = {}
    while time.monotonic() < end:
        body = portal.body(portal.get(url))
        if body.get("state") in DONE_STATES:
            return body
        time.sleep(0.5)
    return body


def tier1_status(body: dict) -> str:
    from arail.registry.store import TIER1_ID
    for e in body.get("entries", []) or []:
        if e.get("id") == TIER1_ID:
            return str((e.get("health") or {}).get("status"))
    return "no tier-1 entry"


def read_surfaces(cfg: dict, portal: Portal, res: RowResult) -> None:
    """Every place that says whether deep works. Each must say failing; one that does not is a silent
    outcome. Press Check last, because it used to erase the one real failure record."""
    from arail.agents import deep_policy
    from arail.portal import model_warmth as mw
    before = deep_counts()
    for fg in (True, False):
        ok, code, detail = deep_policy.explain(foreground=fg)
        res.check("explain(foreground=%s)" % fg, (not ok) and code == "deep_failing",
                  "%s: %s" % (code, clip(detail, 90)), "deep_failing", silent=True)
    r = portal.get("/api/research/status")
    deep = (portal.body(r).get("models") or {}).get("deep") or {}
    res.check("GET /api/research/status (truth strip)",
              r.status_code == 200 and deep.get("reason_code") == "deep_failing" and deep.get("eligible_now") is False,
              "HTTP %d, reason_code=%s, eligible_now=%s" % (r.status_code, deep.get("reason_code"), deep.get("eligible_now")),
              "reason_code=deep_failing, eligible_now=False", silent=True)
    st = tier1_status(portal.body(portal.get("/api/models/state")))
    res.check("GET /api/models/state (tier 1)", st == "unhealthy", "health=%s" % st, "unhealthy", silent=True)
    posted = tier1_status(portal.body(portal.post("/api/models/health/refresh", headers=SFS)))
    after = tier1_status(portal.body(portal.get("/api/models/state")))
    res.check("POST /api/models/health/refresh (Check), then GET", posted == "unhealthy" and after == "unhealthy",
              "after Check: %s, then %s" % (posted, after), "unhealthy both times", silent=True)
    rc, out, err, timed_out = run([cfg["arailctl"], "deep", "status"], env=dict(os.environ), timeout=180)
    m = re.search(r"^\s*generation:\s*(\S+)", out, re.M)
    res.check("./arailctl deep status", rc == 0 and bool(m) and m.group(1) == "FAILING",
              "exit %s, generation: %s" % (rc, m.group(1) if m else "(no such line)"), "exit 0, generation: FAILING",
              silent=True)
    for lead in ("Deep model loaded at boot", "Deep model preloaded"):
        level, text, gen = mw._deep_loaded_line(lead)
        res.check("boot line '%s'" % lead, gen == "failing" and not re.search(r"\bready\b", text, re.I),
                  "%s: %s" % (gen, clip(text, 110)), "failing, and no 'ready'", silent=True)
    res.check("reading the surfaces and pressing Check generated nothing", deep_counts() == before,
              "deep calls %s -> %s" % (before[0], deep_counts()[0]), "no change")


def conclude(res: RowResult) -> None:
    if res.status != INCONCLUSIVE:
        res.status = PASS if res.checks and all(c.ok for c in res.checks) else FAIL


# ── W0 ─────────────────────────────────────────────────────────────────────
def site_manifest(site: Path) -> tuple:
    """(manifest, listing): path, size, mtime and sha256 of everything aerollm or queuellm in the
    interpreter's site-packages, and the sorted top-level listing. W8 compares them with W0's."""
    manifest: dict = {}
    listing: list = []
    try:
        names = sorted(os.listdir(site))
    except OSError:
        return manifest, listing
    listing = names
    for name in names:
        if not any(h in name.lower() for h in ("aerollm", "queuellm")):
            continue
        top = site / name
        files = [top] if top.is_file() or top.is_symlink() else sorted(p for p in top.rglob("*"))
        for f in files:
            try:
                if f.is_symlink():
                    manifest[str(f.relative_to(site))] = {"symlink": os.readlink(f)}
                elif f.is_file():
                    st = f.stat()
                    manifest[str(f.relative_to(site))] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
                                                          "sha256": sha256_file(f)}
            except OSError as e:
                manifest[str(f.relative_to(site))] = {"error": str(e.strerror or e)}
    return manifest, listing


def short_head(head: str) -> str:
    return head[:12] if re.fullmatch(r"[0-9a-f]{40}", head or "") else head


def queuellm_state(path: str) -> dict:
    if not Path(path).is_dir():
        return {"path": path, "head": "no such folder", "status": ""}
    rc, head = git(path, "rev-parse", "HEAD")
    rc2, status = git(path, "status", "--short")
    return {"path": path, "head": head.strip() if rc == 0 else "not a git checkout",
            "status": status if rc2 == 0 else ""}


def probe_pids(root: str) -> list:
    """pids of deep-probe processes: any `arail.deep_probe`, and anything that mentions the scratch root."""
    try:
        p = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, errors="replace",
                           timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return []
    mine = {os.getpid(), os.getppid()}
    out = []
    for line in p.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit() or int(parts[0]) in mine:
            continue
        if "arail.deep_probe" in parts[1] or (root and root in parts[1] and "deep_honest_win" not in parts[1]):
            out.append(int(parts[0]))
    return sorted(out)


def row_w0(cfg: dict, res: RowResult) -> None:
    import importlib.util
    import sysconfig
    repo = Path(cfg["repo"])
    import arail
    here = Path(arail.__file__).resolve()
    res.check("the code under test is this checkout", str(here).startswith(str((repo / "src").resolve()) + os.sep),
              str(here), "under " + str(repo / "src"))
    have = parse_env_file(Path(os.environ["ARAIL_ENV_FILE"]))
    missing = [k for k in FOUR_KEYS if k not in have]
    if missing:
        raise Inconclusive("the scratch .env lacks %s; they are read from the operator's .env "
                           "(ARAIL_WIN_ENV_FROM) and nothing else is copied" % ", ".join(missing))
    res.evidence.append("scratch .env holds only: " + ", ".join(sorted(have)))
    spec = importlib.util.find_spec("aerollm_api")
    if spec is None or not spec.origin:
        raise Inconclusive("aerollm_api is not importable by %s, so there is no wheel to test" % sys.executable)
    from arail import deep_verdict as dv
    native, why = dv.static_native(spec)
    res.check("aerollm_api resolves, without importing it", bool(native), native or why, "a native file")
    res.evidence.append("aerollm_api resolves to " + spec.origin)
    model_dir = Path(have["ARAIL_MODELS_DIR"]) / have["AEROLLM_MODEL"]
    if not model_dir.is_dir():
        raise Inconclusive("the model folder %s does not exist, so no deep call can run" % model_dir)
    res.check("the model folder exists", True, str(model_dir), "an existing folder")
    site = Path(sysconfig.get_path("platlib"))
    manifest, listing = site_manifest(site)
    q = queuellm_state(cfg["queuellm"])
    res.evidence.append("site-packages: %s (%d aerollm or queuellm files recorded, %d top-level entries)"
                        % (site, len(manifest), len(listing)))
    res.evidence.append("QueueLLM checkout: %s, HEAD %s, working tree %s"
                        % (q["path"], short_head(q["head"]), "clean" if not q["status"].strip() else "has changes"))
    res.extra = {"site": str(site), "manifest": manifest, "listing": listing, "queuellm": q,
                 "probe_pids": probe_pids(cfg["root"])}
    set_counts(res)
    res.check("the preflight made no deep call", (res.attempted, res.failed) == (0, 0),
              "attempted %s, failed %s" % (res.attempted, res.failed), "0 and 0")


# ── W1 ─────────────────────────────────────────────────────────────────────
def row_w1(cfg: dict, res: RowResult) -> None:
    from arail.deep_probe_prompts import PROMPTS
    portal = open_portal()
    bodies = []
    for i, (kind, text) in enumerate(PROMPTS, 1):
        r = portal.post("/api/chat", json={"message": text, "backend": "aerollm", "max_tokens": 384,
                                          "temperature": 0.2}, headers=SFS)
        b = portal.body(r)
        bodies.append(b)
        reply = str(b.get("reply", ""))
        if i == 1 and (b.get("error") or reply.startswith("Inference failed:") or not reply.strip()):
            raise Inconclusive("Chat turn 1 did not answer (%s), so the call-2 bug cannot be tested. "
                               "If the wheel was fixed, re-baseline DAY1.json" % clip(b.get("error") or reply, 140))
        res.evidence.append("turn %d (%s): %s" % (i, kind, "answered" if i == 1 else clip(reply, 110)))
    for i, b in enumerate(bodies[1:], 2):
        reply = str(b.get("reply", ""))
        res.check("Chat turn %d says it failed" % i, reply.startswith("Inference failed:") and bool(b.get("error")),
                  clip(reply, 70), "a reply that starts 'Inference failed:' and an error field", silent=True)
        res.check("Chat turn %d did not fall back to another model" % i, b.get("backend") == "aerollm",
                  "backend=%s" % b.get("backend"), "backend=aerollm", silent=True)
    attempted, failed = set_counts(res)
    res.check("deep calls: all five turns tried deep, turns 2 to 5 failed", (attempted, failed) == (5, 4),
              "attempted %d, failed %d" % (attempted, failed), "attempted 5, failed 4")
    read_surfaces(cfg, portal, res)


# ── W2 ─────────────────────────────────────────────────────────────────────
def row_w2(cfg: dict, res: RowResult) -> None:
    from arail import agent_context, agent_trace
    from arail.agents import _builtin_buddy, _builtin_consolidation_analyzer, _builtin_debt_advisor, deep_policy
    portal = open_portal()
    w = portal.post("/api/window/override", json={"window": "heavy"}, headers=SFS)
    p = portal.post("/api/runtime/profile", json={"profile": "throughput"}, headers=SFS)
    res.evidence.append("opened the background gate with the portal's own endpoints: window override heavy %s, "
                        "profile throughput %s" % ("accepted" if w.status_code == 200 else "REFUSED",
                                                   "accepted" if p.status_code == 200 else "REFUSED"))
    ok, code, detail = deep_policy.explain(foreground=False)
    if not ok:
        raise Inconclusive("the background gate is closed before call 1 (%s: %s). That is not a deep failure, so "
                           "this row cannot test one. Free memory and run it again" % (code, clip(detail, 140)))
    calls = [
        ("buddy", _builtin_buddy.ArailHost(), "In one short sentence, cheer on a lab partner who just finished a long training run."),
        ("buddy", _builtin_buddy.ArailHost(), "In one short sentence, remind a lab partner to write down the learning rate."),
        ("debt_advisor", _builtin_debt_advisor.ArailHost(), "In one short sentence, say what a minimum payment is."),
        ("debt_advisor", _builtin_debt_advisor.ArailHost(), "In one short sentence, say why paying the highest rate first saves interest."),
        ("consolidation_analyzer", _builtin_consolidation_analyzer.ArailHost(),
         "In one short sentence, say what combining two loans into one can change."),
    ]
    per_call: list = []
    for i, (agent_id, host, prompt) in enumerate(calls, 1):
        mark = len(agent_trace.ring(5000))
        with agent_context.agent_call(agent_id):
            text = host.llm_complete(prompt, max_tokens=40, temperature=0.4)
        recs = [x for x in agent_trace.ring(5000)[mark:] if x.get("agent_id") == agent_id]
        per_call.append(recs)
        steps = []
        for x in recs:
            stamp = x.get("deep_reason_code")
            steps.append("%s%s" % (x.get("outcome"), " [stamped %s]" % stamp if stamp else ""))
        res.evidence.append("call %d, %s: %s; the agent got %s" % (
            i, agent_id, " then ".join(steps) if steps else "no model call was traced",
            "text" if str(text or "").strip() else "no text"))
    attempted, failed = set_counts(res)
    res.check("deep calls: call 1 answered, call 2 failed, calls 3 to 5 did not try deep",
              (attempted, failed) == (2, 1), "attempted %d, failed %d" % (attempted, failed),
              "attempted 2, failed 1")
    first = per_call[0]
    res.check("call 1 was one deep call that answered", len(first) == 1 and first[0].get("outcome") == "ok",
              "%d trace record(s): %s" % (len(first), ", ".join(str(x.get("outcome")) for x in first) or "none"),
              "one record, outcome ok")
    second = per_call[1]
    res.check("call 2's first trace is the deep failure", bool(second) and second[0].get("outcome") == "error",
              second[0].get("outcome") if second else "no record", "outcome error")
    fast = (second[1:] if second else []) + [x for recs in per_call[2:] for x in recs]
    if not fast:
        raise Inconclusive("no fast-model call was traced after the failure, so the deep_failing stamp could not be "
                           "observed (the fast model is not reachable on this machine at all)")
    unstamped = [x for x in fast if x.get("deep_reason_code") != "deep_failing"]
    res.check("every fast call after the failure carries the deep_failing stamp", not unstamped,
              "%d of %d stamped" % (len(fast) - len(unstamped), len(fast)), "all of them", silent=True)
    res.check("the stamp carries a reason a reader can use", all(str(x.get("deep_reason_detail") or "").strip() for x in fast),
              clip(fast[0].get("deep_reason_detail"), 120), "a non-empty detail", silent=True)
    answered = [x for x in fast if x.get("outcome") == "ok"]
    res.evidence.append("the fast model %s (%s). Tier 0 is pointed at a model name that does not exist, on purpose: "
                        "it answers nothing and Ollama never loads one of yours" % (
                            "answered" if answered else "did not answer",
                            "Ollama on this machine, token-free" if answered else clip(fast[0].get("error_class"), 60)))
    read_surfaces(cfg, portal, res)


# ── W3 and W4 ──────────────────────────────────────────────────────────────
def row_w3(cfg: dict, res: RowResult) -> None:
    portal = open_portal()
    mount_world()
    r = portal.post("/api/worlds/review", headers=SFS)
    res.check("the review was accepted", r.status_code == 202, "HTTP %d" % r.status_code, "HTTP 202")
    body = wait_done(portal, "/api/worlds/review")
    attempted, failed = set_counts(res)
    res.evidence.append("the Curator says: " + clip(body.get("message"), 220))
    res.check("deep calls: one judge call per term, the first answered", (attempted, failed) == (6, 5),
              "attempted %d, failed %d" % (attempted, failed), "attempted 6, failed 5")
    res.check("outcome is partial, not complete", body.get("outcome") == "partial", str(body.get("outcome")),
              "partial", silent=True)
    res.check("it says judged 1 of 6, failed 5",
              (body.get("judged"), body.get("planned"), body.get("failed")) == (1, 6, 5),
              "judged %s of %s, failed %s" % (body.get("judged"), body.get("planned"), body.get("failed")),
              "judged 1 of 6, failed 5", silent=True)
    res.check("its count matches what the backend saw", body.get("judged") == attempted - failed,
              "Curator judged %s, backend answered %d" % (body.get("judged"), attempted - failed),
              "the same number", silent=True)
    msgs = activity_messages()
    res.check("no 'All clear' in the activity log", not any("All clear" in m for m in msgs),
              "%d line(s) say it" % sum("All clear" in m for m in msgs), "none", silent=True)
    read_surfaces(cfg, portal, res)


def _file_hash(p: Path) -> str:
    return sha256_file(p) if p.exists() else "absent"


def row_w4(cfg: dict, res: RowResult) -> None:
    portal = open_portal()
    world = mount_world()
    files = [world / "terms.json", world / "manifest.json", world / "evolution.json"]
    before = {f.name: _file_hash(f) for f in files}
    r = portal.post("/api/worlds/grow", json={"brain": "auto"}, headers=SFS)
    res.check("the growth pass was accepted", r.status_code == 202, "HTTP %d" % r.status_code, "HTTP 202")
    body = wait_done(portal, "/api/worlds/grow")
    attempted, failed = set_counts(res)
    res.evidence.append("the Curator says: " + clip(body.get("message"), 220))
    res.check("deep calls: the first answered, every other call failed", attempted >= 2 and failed == attempted - 1,
              "attempted %d, failed %d" % (attempted, failed), "attempted 2 or more, failed = attempted - 1")
    res.check("the state is not_applied", body.get("state") == "not_applied", str(body.get("state")),
              "not_applied", silent=True)
    after = {f.name: _file_hash(f) for f in files}
    res.check("terms.json, manifest.json and evolution.json are unchanged", before == after,
              "changed: " + (", ".join(k for k in before if before[k] != after[k]) or "none"), "none changed",
              silent=True)
    msgs = activity_messages()
    res.check("no 'evolved via' line in the activity log", not any("evolved via" in m for m in msgs),
              "%d line(s) say it" % sum("evolved via" in m for m in msgs), "none", silent=True)
    calls = body.get("calls") or {}
    res.check("its own call count matches the backend's", (calls.get("attempted"), calls.get("failed")) == (attempted, failed),
              "Curator %s/%s, backend %d/%d" % (calls.get("attempted"), calls.get("failed"), attempted, failed),
              "the same numbers", silent=True)
    read_surfaces(cfg, portal, res)


# ── W5 ─────────────────────────────────────────────────────────────────────
def row_w5(cfg: dict, res: RowResult) -> None:
    if not cfg["expect_sha256"] or not cfg["expect_version"]:
        raise Inconclusive("there is no expected wheel to compare with: DAY1.json was not found and "
                           "--expect-sha256 and --expect-version were not given")
    from arail import deep_probe as dp
    rc, out, err, timed_out = run([cfg["arailctl"], "deep", "probe", "--json"], env=dict(os.environ),
                                  timeout=max(60.0, cfg["row_timeout"] - 30.0))
    try:
        doc = json.loads(out)
    except ValueError:
        doc = None
    res.check("the probe printed one JSON document", isinstance(doc, dict),
              ("exit %s, schema %s" % (rc, doc.get("schema"))) if isinstance(doc, dict) else
              "exit %s, %s" % (rc, clip(out or err, 140)), "a JSON document", silent=True)
    if not isinstance(doc, dict):
        return
    binary, fe, store = doc.get("binary") or {}, doc.get("first_error") or {}, doc.get("verdict_store") or {}
    total, passed = doc.get("total"), doc.get("passed")
    if isinstance(total, int) and isinstance(passed, int):
        res.attempted, res.failed = total, total - passed
    res.evidence.append("the probe said: %s; first error: %s" % (doc.get("outcome"), clip(fe.get("error"), 150)))
    res.check("exit code 1: the probe ran and deep is failing", rc == 1, "exit %s" % rc, "exit 1", silent=True)
    res.check("passed 1 of 5", (passed, total) == (1, 5), "passed %s of %s" % (passed, total), "passed 1 of 5",
              silent=True)
    res.check("the first error is at call 2 and names rms_norm",
              fe.get("call") == 2 and "rms_norm" in str(fe.get("error")),
              "call %s: %s" % (fe.get("call"), clip(fe.get("error"), 80)), "call 2, text has rms_norm")
    res.check("the import resolves to a package's __init__.py",
              str(binary.get("resolves_to", "")).endswith("aerollm_api/__init__.py"),
              str(binary.get("resolves_to")), "ends with aerollm_api/__init__.py")
    res.check("the binary is the day-1 wheel (sha256)", binary.get("sha256") == cfg["expect_sha256"],
              str(binary.get("sha256"))[:16] + "...", cfg["expect_sha256"][:16] + "... (DAY1.json)")
    res.check("the version is the day-1 version", binary.get("version") == cfg["expect_version"],
              str(binary.get("version")), cfg["expect_version"] + " (DAY1.json)")
    res.check("the failure was written to the verdict file", store.get("written") is True and not store.get("error"),
              "written=%s error=%s" % (store.get("written"), store.get("error")), "written, no error", silent=True)
    teach = doc.get("teaching") or {}
    leaves = [p for p, _v in dp._numeric_leaves({k: v for k, v in doc.items() if k != "teaching"})
              if p not in dp.IDENTIFIER_PATHS]
    thin = [p for p in leaves if not all(str((teach.get(p) or {}).get(part, "")).strip() for part in PARTS)]
    res.check("every number in the probe's JSON has its four taught parts", bool(teach) and not thin,
              "missing: " + (", ".join(thin[:4]) or "none"), "none missing")


# ── W6 ─────────────────────────────────────────────────────────────────────
def row_w6(cfg: dict, res: RowResult) -> None:
    store = Path(os.environ["ARAIL_DEEP_STATE_DIR"]) / "verdict.json"
    if not store.exists():
        raise Inconclusive("W5 left no verdict file (%s), so there is nothing for a fresh process to read" % store)
    portal = open_portal()
    read_surfaces(cfg, portal, res)
    attempted, failed = set_counts(res)
    res.check("this process made no deep call", (attempted, failed) == (0, 0),
              "attempted %d, failed %d" % (attempted, failed), "0 and 0")


# ── W7 ─────────────────────────────────────────────────────────────────────
FORBIDDEN = (
    ("No QueueLLM sibling repo", re.compile(r"No QueueLLM sibling repo")),
    ("QueueLLM ready", re.compile(r"QueueLLM ready")),
    ("(missing -> release/bundled channel)", re.compile("\\(missing → release/bundled channel\\)")),
    ("(found -> source/dev channel)", re.compile("\\(found → source/dev channel\\)")),
    ("a channel: line that says release or dev", re.compile(r"channel:\s+(release|dev)\b", re.I)),
)


def row_w7(cfg: dict, res: RowResult) -> None:
    root, env = Path(cfg["root"]), dict(os.environ)
    if not (Path(env["ARAIL_DEEP_STATE_DIR"]) / "verdict.json").exists():
        raise Inconclusive("W5 left no verdict file, so deep status has no generation line to check")
    rc_s, out_s, err_s, _ = run([cfg["arailctl"], "deep", "status"], env=env, timeout=180)
    status_text = out_s + err_s
    for label in ("import", "binary", "sha256", "shadowed", "channel", "generation"):
        res.check("deep status prints the %s line" % label, re.search(r"^\s+%s:" % label, out_s, re.M) is not None,
                  "present" if re.search(r"^\s+%s:" % label, out_s, re.M) else "missing", "present")
    ch = re.search(r"^\s+channel:\s*(.*)$", out_s, re.M)
    gen = re.search(r"^\s+generation:\s*(\S+)", out_s, re.M)
    res.evidence.append("channel line: " + clip(ch.group(1) if ch else "(none)", 170))
    res.check("the channel is unknown, because nothing proves otherwise", bool(ch) and ch.group(1).startswith("unknown"),
              clip(ch.group(1) if ch else "(none)", 60), "unknown - <why>", silent=True)
    res.check("the generation line says FAILING", rc_s == 0 and bool(gen) and gen.group(1) == "FAILING",
              "exit %s, %s" % (rc_s, gen.group(1) if gen else "(none)"), "exit 0, FAILING", silent=True)
    if cfg["expect_sha256"]:
        res.check("the sha256 line names the day-1 wheel", cfg["expect_sha256"][:12] in out_s,
                  "found" if cfg["expect_sha256"][:12] in out_s else "not found", cfg["expect_sha256"][:12])

    ctl = Path(cfg["arailctl"]).read_text(encoding="utf-8")
    same = re.search(r'^\s*rebuild\)\s+exec bash "\$REPO_ROOT/scripts/build-aerollm\.sh" build\s*;;', ctl, re.M)
    res.evidence.append("./arailctl deep rebuild is only `exec bash scripts/build-aerollm.sh build`, so this row "
                        "runs that line; the rule for this build is not to run ./arailctl deep rebuild itself")
    res.check("deep rebuild dispatches to build-aerollm.sh build", bool(same),
              "yes" if same else "the dispatch line changed", "the dispatch line", silent=False)

    venv = root / "emptyvenv"
    rc_v, _o, err_v, _t = run([cfg["python"], "-m", "venv", "--without-pip", str(venv)], env=env, timeout=180)
    if rc_v != 0:
        raise Inconclusive("could not make the scratch empty venv: " + clip(err_v, 160))
    stub = root / "stubbin"
    stub.mkdir(exist_ok=True)
    calls = stub / "cargo.calls"
    cargo = stub / "cargo"
    cargo.write_text("#!/bin/sh\necho \"$@\" >> '%s'\nexit 99\n" % calls, encoding="utf-8")
    os.chmod(cargo, 0o755)
    benv = dict(env)
    benv.update({"PYTHON": str(venv / "bin" / "python"), "PATH": str(stub) + os.pathsep + env["PATH"],
                 "AEROLLM_BUNDLE_FILE": str(root / "no-such-bundle.tar.gz")})
    outputs = {"deep status": status_text}
    for mode in ("build", "auto"):
        rc, o, e, _t = run(["bash", cfg["build_script"], mode], env=benv, timeout=300)
        outputs["build-aerollm.sh " + mode] = o + e
        res.check("build-aerollm.sh %s refuses with exit 1" % mode, rc == 1, "exit %s" % rc, "exit 1")
        res.check("build-aerollm.sh %s says nothing was built or installed" % mode,
                  "Nothing was built or installed" in (o + e), line_with(o + e, "Nothing was built or installed"),
                  "'Nothing was built or installed'", silent=True)
    n_cargo = len(calls.read_text(encoding="utf-8").splitlines()) if calls.exists() else 0
    res.check("cargo was never started (a stub stood in its place)", n_cargo == 0, "%d call(s)" % n_cargo, "none")
    combined = "\n".join(outputs.values())
    for label, rx in FORBIDDEN:
        n = len(rx.findall(combined))
        res.check("the old false claim is gone: " + label, n == 0, "%d time(s) in the three outputs" % n, "0 times",
                  silent=True)


# ── W8 ─────────────────────────────────────────────────────────────────────
def row_w8(cfg: dict, res: RowResult) -> None:
    w0p = Path(cfg["root"]) / "W0" / "result.json"
    try:
        w0 = json.loads(w0p.read_text(encoding="utf-8")).get("extra") or {}
    except (OSError, ValueError):
        w0 = {}
    if not w0.get("site"):
        raise Inconclusive("W0 left no manifest, so there is nothing to compare the postflight with")
    manifest, listing = site_manifest(Path(w0["site"]))
    moved = sorted(k for k in set(manifest) | set(w0["manifest"]) if manifest.get(k) != w0["manifest"].get(k))
    res.check("the aerollm and queuellm files in site-packages are identical to W0's", not moved,
              "changed: " + (", ".join(moved[:3]) or "none"), "none changed")
    res.check("the top-level site-packages listing is identical", listing == w0["listing"],
              "added or removed: " + (", ".join(sorted(set(listing) ^ set(w0["listing"]))[:3]) or "none"), "none")
    q = queuellm_state(cfg["queuellm"])
    res.check("the QueueLLM checkout (HEAD and working tree) is identical", q == w0["queuellm"],
              "%s, tree %s" % (short_head(q["head"]), "clean" if not q["status"].strip() else "has changes"), "as in W0")
    if cfg["dac_base"]:
        rc, _ = git(cfg["repo"], "diff", "--quiet", cfg["dac_base"], "--", "src/dac_world")
        if rc not in (0, 1):
            raise Inconclusive("git could not compare src/dac_world with %s (exit %s)" % (cfg["dac_base"], rc))
        res.check("src/dac_world is byte-identical to %s" % cfg["dac_base"], rc == 0,
                  "identical" if rc == 0 else "differs", "identical")
    extra = [p for p in probe_pids(cfg["root"]) if p not in (w0.get("probe_pids") or [])]
    res.check("no probe child process is left", not extra, "left: " + (", ".join(map(str, extra)) or "none"), "none")


ROW_FUNCS = {"W0": row_w0, "W1": row_w1, "W2": row_w2, "W3": row_w3, "W4": row_w4, "W5": row_w5, "W6": row_w6,
             "W7": row_w7, "W8": row_w8}


def child_main(row: str, config_path: str) -> int:
    cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
    res = RowResult(row=row)
    t0 = time.monotonic()
    try:
        ROW_FUNCS[row](cfg, res)
        conclude(res)
    except Inconclusive as e:
        res.status, res.reason = INCONCLUSIVE, str(e)
    except Exception as e:  # noqa: BLE001 - a crashed row is a failed row, with its traceback tail
        import traceback
        res.status, res.reason = FAIL, "the row crashed: %s: %s" % (type(e).__name__, clip(e, 200))
        res.evidence += ["traceback: " + clip(ln, 200) for ln in traceback.format_exc().strip().splitlines()[-6:]]
    res.seconds = time.monotonic() - t0
    out = Path(os.environ["ARAIL_DATA_DIR"]).parent / "result.json"
    tmp = out.with_name("result.json.tmp")
    tmp.write_text(json.dumps(res.to_json(), indent=1), encoding="utf-8")
    os.replace(tmp, out)
    sys.stdout.flush()
    sys.stderr.flush()
    # Skip interpreter teardown: a loaded Metal runtime and its worker thread can hang or abort on the
    # way out, and an abort writes a crash report into the operator's DiagnosticReports folder.
    os._exit(0)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------
def parse_args(argv: list) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="deep_honest_win.py", description="Run the deep-honest live check "
                                 "(use scripts/deep-honest-win.sh, which prepares the scratch root).")
    ap.add_argument("--child", metavar="ROW", help=argparse.SUPPRESS)
    ap.add_argument("--config", help=argparse.SUPPRESS)
    ap.add_argument("--root", help="scratch root (made by the wrapper)")
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]), help="the checkout under test")
    ap.add_argument("--python", default=sys.executable, help="the interpreter the rows run under")
    ap.add_argument("--env-from", help="the scratch .env: four non-secret keys")
    ap.add_argument("--queuellm", default="", help="the QueueLLM checkout, read only")
    ap.add_argument("--only", default="", help="comma list of rows, for example W5,W6")
    ap.add_argument("--expect-sha256", default="", help="the wheel's sha256 (default: DAY1.json)")
    ap.add_argument("--expect-version", default="", help="the wheel's version (default: DAY1.json)")
    ap.add_argument("--dac-base", default="", help="the commit src/dac_world must equal (default: DAY1.json)")
    ap.add_argument("--json-out", default="", help="also write the whole result as JSON to this file")
    ap.add_argument("--row-timeout", type=float, default=float(os.environ.get("ARAIL_WIN_ROW_TIMEOUT_S", DEFAULT_ROW_TIMEOUT_S)))
    ap.add_argument("--path", default=DEFAULT_PATH, help="PATH for the rows")
    ap.add_argument("--extra-env", action="append", default=[], metavar="KEY=VALUE",
                    help="dry run only: extra environment for the rows")
    ap.add_argument("--pythonpath-prepend", action="append", default=[], metavar="DIR",
                    help="dry run only: a folder put before the checkout on PYTHONPATH")
    a = ap.parse_args(argv)
    if a.child:
        if a.child not in ROW_IDS or not a.config:
            ap.error("--child needs a row and --config")
        return a
    for need in ("root", "env_from", "queuellm"):
        if not getattr(a, need):
            ap.error("--%s is required" % need.replace("_", "-"))
    a.only = {r.strip().upper() for r in a.only.split(",") if r.strip()}
    bad = sorted(a.only - set(ROW_IDS))
    if bad:
        ap.error("unknown row(s): " + ", ".join(bad))
    return a


def main(argv: Optional[list] = None) -> int:
    a = parse_args(list(sys.argv[1:] if argv is None else argv))
    if a.child:
        return child_main(a.child, a.config)
    return orchestrate(a)


if __name__ == "__main__":
    raise SystemExit(main())
