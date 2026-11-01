"""./arailctl deep probe: the supervisor, the child and the teaching.

C3 of sprints/2026-10-29-arail-deep-honest/ARCHITECTURE.md. Tests Q1 to Q19,
TQ1, TQ2, SEC3, SEC4 and SEC6 are named for the architecture's list (Q16 is in
test_deep_probe_prompts.py, Q20 in test_deep_probe_cli.py). The supervisor and
the child run as real subprocesses over the fake runtime.
"""
from __future__ import annotations

import errno
import io
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import types
from collections import deque
from pathlib import Path

import pytest

from arail import deep_probe as dp
from arail import deep_verdict as dv
from tests.fakes.fake_deep_runtime import fake_deep  # noqa: F401  (fixture)

SRC = str(Path(dp.__file__).resolve().parents[1])
ALLOWED_END = {"answered", "error", "empty", "timeout", "crashed", "not_reached"}
FOUR_LABELS = ("meaning:", "bar and why:", "out of range:", "the fault lives in:")


def run_probe(fd, *args: str, timeout: float = 120, **env) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "arail.deep_probe", *args], env=fd.env(**env),
                          capture_output=True, text=True, timeout=timeout)


def probe_json(fd, *args: str, **env):
    r = run_probe(fd, "--json", *args, **env)
    return r, json.loads(r.stdout)


def stored(fd) -> dict:
    return dv.record_for(fd.sha256) or {}


# ── Q1: pass ───────────────────────────────────────────────────────────────

def test_q1_pass_exits_0_records_passing_and_the_ring_depth(fake_deep):
    fd = fake_deep("pass")
    r = run_probe(fd)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "result    5/5 passed. Deep generation answered every call on this binary." in r.stdout
    assert "recorded  passing" in r.stdout
    rec = stored(fd)
    assert rec["failure"] is None and rec["last_probe"]["outcome"] == "pass"
    assert rec["last_probe"]["passed"] == 5 and rec["last_probe"]["total"] == 5
    assert rec["last_probe"]["ring_depth"] == 2 and rec["last_probe"]["ring_depth_source"] == "env"
    assert rec["last_probe"]["prompt_set_sha256"].startswith("b5bcb562")
    dv.identity()
    assert dv.current().state == "passing"
    assert len(fd.gen_calls()) == 5


def test_q1_a_passing_probe_clears_an_earlier_failure(fake_deep):
    fd = fake_deep("pass")
    dv.identity()
    dv.note_call(False, error="boom")
    assert dv.current().state == "failing"
    assert run_probe(fd).returncode == 0
    assert dv.current().state == "passing"


# ── Q2: the bug ────────────────────────────────────────────────────────────

def test_q2_fail2_exits_1_with_1_of_5_and_the_first_error_at_call_2(fake_deep):
    fd = fake_deep("fail2")
    r, doc = probe_json(fd)
    assert r.returncode == 1, r.stderr
    assert doc["outcome"] == "fail" and doc["exit_code"] == 1
    assert (doc["passed"], doc["total"]) == (1, 5)
    assert doc["first_error"]["call"] == 2 and doc["first_error"]["kind"] == "long"
    assert "[rms_norm]" in doc["first_error"]["error"]
    assert [c["end"] for c in doc["calls"]] == ["answered", "error", "error", "error", "error"]
    assert all(c["seconds"] < 5 for c in doc["calls"])
    assert doc["median_ok_s"] is not None
    rec = stored(fd)
    assert rec["failure"]["source"] == "probe" and rec["failure"]["consumer"] == "deep-probe"
    assert rec["last_probe"]["first_error"]["call"] == 2
    dv.identity()
    assert dv.current().state == "failing"


def test_q2_the_text_names_the_first_error_and_collapses_repeats(fake_deep):
    fd = fake_deep("fail2")
    r = run_probe(fd)
    assert r.returncode == 1
    out = r.stdout
    assert "result    1/5 passed. Deep generation is FAILING on this binary." in out
    assert re.search(r"^first     call 2 \(long\): RuntimeError: backend `mlx-native` failed", out, re.M)
    assert out.count("same error as call 2") == 3
    assert "recorded  failing" in out and "5/5" in out
    assert "use the fast model until ./arailctl deep probe passes 5/5 on this build" in out
    assert "new build" not in out, "a probe of the same build clears it too (review W4)"


# ── Q3: the child crashes mid-call ─────────────────────────────────────────

def test_q3_a_crash_is_a_crashed_row_and_recorded_failing_with_the_stderr_tail(fake_deep):
    fd = fake_deep("crash3")
    r, doc = probe_json(fd)
    assert r.returncode == 1
    ends = [c["end"] for c in doc["calls"]]
    assert ends == ["answered", "answered", "crashed", "not_reached", "not_reached"]
    assert (doc["passed"], doc["total"]) == (2, 3), "total is the calls attempted"
    assert "the probe child died (signal 9, SIGKILL) during this call" in doc["calls"][2]["error"]
    assert any("fatal error in layer 3" in ln for ln in doc["stderr_tail"])
    assert stored(fd)["failure"]["source"] == "probe"
    text = run_probe(fake_deep("crash3")).stdout
    assert "CRASHED" in text and "The probe stopped early" in text


def test_q3_a_native_abort_is_named_sigabrt():
    class Res(dict):
        pass
    res = {"end": "died", "returncode": -6}
    assert "signal 6, SIGABRT" in dp._death_text(res, 3, 300.0, 300.0)
    assert "exited with status 2" in dp._death_text({"end": "died", "returncode": 2}, 3, 300.0, 300.0)
    assert "exited without finishing" in dp._death_text({"end": "died", "returncode": 0}, 3, 300.0, 300.0)
    assert "while loading" in dp._death_text({"end": "died", "returncode": -6}, "load", 300.0, 300.0)


# ── Q4: a hang is killed at the deadline, no orphan ────────────────────────

def test_q4_a_hung_call_is_stopped_at_the_deadline_and_leaves_no_orphan(fake_deep):
    fd = fake_deep("hang2")
    t0 = time.monotonic()
    r, doc = probe_json(fd, ARAIL_DEEP_PROBE_CALL_TIMEOUT_S="2")
    took = time.monotonic() - t0
    assert r.returncode == 1 and took < 30
    assert [c["end"] for c in doc["calls"]] == ["answered", "timeout", "not_reached", "not_reached", "not_reached"]
    assert "did not finish within 2 s" in doc["calls"][1]["error"]
    assert 1.5 <= doc["calls"][1]["seconds"] < 10
    pids = {e["pid"] for e in fd.events() if e.get("ev") == "init"}
    assert pids
    time.sleep(0.3)
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    assert stored(fd)["failure"] is not None


# ── Q5: a runtime that will not load ───────────────────────────────────────

def test_q5_a_load_failure_is_exit_1_with_zero_passed(fake_deep):
    fd = fake_deep("loadfail")
    r, doc = probe_json(fd)
    assert r.returncode == 1
    assert doc["load"]["ok"] is False and "could not start" in doc["load"]["error"]
    assert doc["passed"] == 0 and doc["total"] == 0 and doc["calls"] == []
    assert doc["first_error"]["kind"] == "load"
    assert stored(fd)["failure"]["source"] == "probe"
    text = run_probe(fd).stdout
    assert "load      FAILED" in text


# ── Q6, Q18: not run, nothing recorded ─────────────────────────────────────

def test_q6_not_importable_is_exit_5_and_records_nothing(fake_deep, tmp_path):
    fd = fake_deep("pass")
    env = fd.env()
    env["PYTHONPATH"] = SRC                       # the fake is not on the path
    r = subprocess.run([sys.executable, "-m", "arail.deep_probe"], env=env, capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 5
    assert "not run   aerollm_api is not importable by" in r.stdout
    assert "install QueueLLM first: ./arailctl deep install" in r.stdout
    assert not (dv.state_dir() / "verdict.json").exists()
    r2 = subprocess.run([sys.executable, "-m", "arail.deep_probe", "--json"], env=env, capture_output=True,
                        text=True, timeout=60)
    doc = json.loads(r2.stdout)
    assert doc["outcome"] == "not_run" and doc["exit_code"] == 5 and "not importable" in doc["not_run_reason"]
    assert doc["calls"] == [] and doc["passed"] is None


def test_q18_a_missing_model_dir_is_exit_5_and_records_nothing(fake_deep):
    fd = fake_deep("pass")
    r = run_probe(fd, AEROLLM_MODEL="no-such-model")
    assert r.returncode == 5
    assert "not run   model dir missing:" in r.stdout and "check AEROLLM_MODEL and ARAIL_MODELS_DIR" in r.stdout
    assert not (dv.state_dir() / "verdict.json").exists()
    assert fd.gen_calls() == [], "nothing was loaded"


# ── Q7: two probes at once ─────────────────────────────────────────────────

def test_q7_a_second_probe_is_refused_while_the_first_runs(fake_deep):
    fd = fake_deep("slow:0.6")
    first = subprocess.Popen([sys.executable, "-m", "arail.deep_probe", "--json"], env=fd.env(),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    lock = dv.state_dir() / "probe.lock"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not (lock.exists() and lock.read_text().strip()):
        time.sleep(0.05)
    r2 = run_probe(fd)
    assert r2.returncode == 5
    assert re.search(r"not run   another deep probe is running \(pid \d+, since \d\d:\d\d:\d\d UTC\) — wait for it",
                     r2.stdout), r2.stdout
    out, _err = first.communicate(timeout=120)
    assert first.returncode == 0, "the first probe is unaffected"
    assert json.loads(out)["passed"] == 5


# ── Q8: native noise on stdout ─────────────────────────────────────────────

def test_q8_stdout_noise_does_not_reach_the_protocol(fake_deep):
    fd = fake_deep("stdout-noise")
    r, doc = probe_json(fd)
    assert r.returncode == 0, r.stderr
    assert doc["passed"] == 5 and doc["total"] == 5
    assert [c["index"] for c in doc["calls"]] == [1, 2, 3, 4, 5], "no phantom call from the JSON-looking noise line"
    assert any("native banner on stdout" in ln for ln in doc["stderr_tail"]), \
        "the noise went to the child's stderr and was kept in the tail, not mixed into the protocol"
    assert any('"ev": "call", "i": 99' in ln for ln in doc["stderr_tail"]), "a look-alike line has no probe:1 mark"


# ── Q9: memory ─────────────────────────────────────────────────────────────

def _inproc(fd, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", f"{fd.pkg_dir}{os.pathsep}{SRC}")


def test_q9_too_little_memory_is_refused_without_force_and_runs_with_it(fake_deep, monkeypatch):
    fd = fake_deep("pass")
    _inproc(fd, monkeypatch)
    out = io.StringIO()
    rc = dp.run([], memory_reader=lambda: 1.0, holders_reader=lambda p: [], out=out)
    assert rc == 5
    text = out.getvalue()
    assert "not enough available memory for another copy" in text and "1.0 GiB available" in text
    assert "2.0 GiB needed" in text and "stop the lab or pass --force" in text
    assert not (dv.state_dir() / "verdict.json").exists() and fd.gen_calls() == []

    out = io.StringIO()
    rc = dp.run(["--force"], memory_reader=lambda: 1.0, holders_reader=lambda p: [], out=out)
    assert rc == 0, out.getvalue()
    assert "memory    1.0 GiB available" in out.getvalue()
    assert dv.record_for(fd.sha256)["last_probe"]["outcome"] == "pass"


def test_q9_force_never_skips_the_lock(fake_deep, monkeypatch):
    fd = fake_deep("pass")
    _inproc(fd, monkeypatch)
    lock_fd, _ = dp._take_probe_lock(dv.state_dir())
    try:
        out = io.StringIO()
        rc = dp.run(["--force"], memory_reader=lambda: 99.0, holders_reader=lambda p: [], out=out)
        assert rc == 5 and "another deep probe is running" in out.getvalue()
    finally:
        os.close(lock_fd)


def test_q9_a_holder_changes_the_memory_line(fake_deep, monkeypatch):
    fd = fake_deep("pass")
    _inproc(fd, monkeypatch)
    out = io.StringIO()
    holders = [{"pid": 4242, "command": "uvicorn arail.portal.app:app --port 8080"}]
    rc = dp.run([], memory_reader=lambda: 16.2, holders_reader=lambda p: holders, out=out)
    assert rc == 0
    line = next(ln for ln in out.getvalue().splitlines() if ln.startswith("memory "))
    assert "16.2 GiB available" in line
    assert "pid 4242 (uvicorn arail.portal.app:app --port 8080…) already has this binary loaded" in line
    assert "if it holds the model, this probe is a second copy" in line


# ── Q10: the JSON ──────────────────────────────────────────────────────────

def test_q10_json_is_one_object_with_the_schema_and_nothing_else(fake_deep):
    fd = fake_deep("fail2")
    fd.with_tokenizer()
    r = run_probe(fd, "--json")
    doc = json.loads(r.stdout)                           # the whole of stdout parses
    assert doc["schema"] == "arail.deep-probe/v1"
    for key in ("outcome", "exit_code", "not_run_reason", "started_at", "finished_at", "duration_s", "python",
                "binary", "model", "runtime", "memory", "load", "calls", "passed", "total", "median_ok_s",
                "first_error", "stderr_tail", "verdict_store", "teaching"):
        assert key in doc, key
    b = doc["binary"]
    assert b["sha256"] == fd.sha256 and b["version"] == "0.0.0-fake" and b["module"] == "aerollm_api"
    assert b["resolves_to"].endswith("aerollm_api/__init__.py") and b["native_path"].endswith("_native.abi3.so")
    assert doc["model"]["name"] == "fake-model" and doc["runtime"]["max_tokens"] == 384
    assert doc["runtime"]["temperature"] == 0.2 and doc["runtime"]["ring_depth"] == 2
    assert doc["runtime"]["prompt_set_sha256"].startswith("b5bcb562")
    assert doc["memory"]["needed_gib"] >= 2.0 and isinstance(doc["memory"]["holders"], list)
    assert doc["verdict_store"]["written"] is True
    assert {c["end"] for c in doc["calls"]} <= ALLOWED_END
    assert [c["kind"] for c in doc["calls"]] == ["short", "long", "short-repeat", "long-shared-prefix", "short-2"]
    assert all(isinstance(c["prompt_tokens"], int) and c["prompt_tokens"] > 0 for c in doc["calls"]), \
        "with a tokenizer the prompt token counts are real"
    assert doc["calls"][1]["prompt_tokens"] > doc["calls"][0]["prompt_tokens"] * 20
    assert doc["outcome"] in ("pass", "fail", "not_run")


# ── Q11: the text and its legend ───────────────────────────────────────────

def test_q11_text_has_the_lines_and_a_legend_with_all_four_parts(fake_deep):
    fd = fake_deep("fail2")
    fd.with_tokenizer()
    r = run_probe(fd)
    lines = r.stdout.splitlines()
    assert lines[0].startswith("QueueLLM deep probe: 5 calls in a fresh process, through ARAIL's own deep backend.")
    assert "Token-free, no network." in lines[0]
    labels = [ln.split()[0] for ln in lines if ln and not ln.startswith(" ")]
    for must in ("python", "import", "binary", "model", "memory", "load", "call", "result", "median", "first",
                 "recorded"):
        assert must in labels, (must, labels)
    i = lines.index("What the numbers mean")
    legend = lines[i + 1:]
    entries = {}
    cur = None
    for ln in legend:
        if ln.startswith("  ") and not ln.startswith("      "):
            cur = ln.strip()
            entries[cur] = []
        elif cur and ln.strip():
            entries[cur].append(ln.strip())
    assert entries
    for key, parts in entries.items():
        text = "\n".join(parts)
        assert all(lbl in text for lbl in FOUR_LABELS), (key, parts)
        assert all(p.split(":", 1)[1].strip() for p in parts), (key, "an empty part")


# ── Q12: a shadowed copy is listed ─────────────────────────────────────────

def test_q12_a_shadowed_top_level_copy_is_listed_and_never_loaded(fake_deep):
    fd = fake_deep("pass")
    decoy = fd.pkg_dir / "aerollm_api.abi3.so"
    decoy.write_bytes(b"AN OLDER BUILD THAT IS NEVER LOADED\n" * 30)
    r, doc = probe_json(fd)
    assert r.returncode == 0
    sh = doc["binary"]["shadowed"]
    assert [s["path"] for s in sh] == [str(decoy)]
    assert doc["binary"]["sha256"] == fd.sha256 != sh[0]["sha256"]
    text = run_probe(fd).stdout
    assert f"shadowed  {decoy} · sha256 " in text and "on the path, never loaded" in text


# ── Q13: a corrupt store does not stop the probe ───────────────────────────

def test_q13_a_corrupt_store_is_moved_aside_and_the_probe_still_records(fake_deep):
    fd = fake_deep("pass")
    dv.state_dir().mkdir(parents=True, exist_ok=True)
    (dv.state_dir() / "verdict.json").write_text("{broken")
    r, doc = probe_json(fd)
    assert r.returncode == 0 and doc["verdict_store"]["written"] is True
    assert len(list(dv.state_dir().glob("verdict.json.corrupt-*"))) == 1
    assert stored(fd)["last_probe"]["outcome"] == "pass"


# ── Q14: the supervisor never imports the runtime ──────────────────────────

def test_q14_the_supervisor_never_imports_aerollm_api(fake_deep):
    fd = fake_deep("pass")
    code = ("import io, sys\nfrom arail import deep_probe\n"
            "rc = deep_probe.run(['--json'], out=io.StringIO())\n"
            "print(rc, 'aerollm_api' in sys.modules, 'queuellm_api' in sys.modules)\n")
    r = subprocess.run([sys.executable, "-c", code], env=fd.env(), capture_output=True, text=True, timeout=120)
    assert r.stdout.split() == ["0", "False", "False"], r.stdout + r.stderr


# ── Q15, Q19: interrupts and a dead supervisor ─────────────────────────────

def _start_slow(fd, mode):
    fd.set_mode(mode)
    p = subprocess.Popen([sys.executable, "-m", "arail.deep_probe"], env=fd.env(), stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 60
    seen = ""
    while time.monotonic() < deadline:
        line = p.stdout.readline()
        seen += line
        if line.startswith("load "):
            return p, seen
        if not line and p.poll() is not None:
            break
    p.kill()
    raise AssertionError("the probe never reached the load line:\n" + seen + p.stderr.read())


def _child_pids(fd):
    return {e["pid"] for e in fd.events() if e.get("ev") == "init"}


def test_q15_sigint_is_exit_130_kills_the_child_and_records_nothing(fake_deep):
    fd = fake_deep("pass")
    p, _seen = _start_slow(fd, "slow:5")
    time.sleep(0.5)
    p.send_signal(signal.SIGINT)
    out, _ = p.communicate(timeout=60)
    assert p.returncode == 130, out
    assert "interrupted: the probe child was stopped and nothing was recorded" in out
    time.sleep(0.5)
    for pid in _child_pids(fd):
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    assert not (dv.state_dir() / "verdict.json").exists()


def test_q19_a_killed_supervisor_leaves_a_child_that_exits_after_its_call(fake_deep):
    fd = fake_deep("pass")
    p, _seen = _start_slow(fd, "slow:1.5")
    time.sleep(0.4)
    pids = _child_pids(fd)
    assert pids
    p.kill()                                          # SIGKILL: no handler runs
    p.communicate(timeout=30)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            for pid in pids:
                os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.2)
    else:
        pytest.fail("the orphaned child kept running")
    assert len(fd.gen_calls()) <= 2, "it finished its running call and did not start another"
    assert not (dv.state_dir() / "verdict.json").exists()


# ── W3 of the review: the two layers that had no test of their own ──────────────────────────

def test_w3_a_line_without_the_probe_tag_is_not_an_event():
    """Native code can print JSON to fd 1 that looks like a protocol line. dup2 keeps almost all of it
    off the pipe (Q8, which passes without the tag check); the "probe": 1 tag is the second layer, and
    this test is the one that fails without it."""
    lines = [
        json.dumps({"ev": "call", "i": 99, "kind": "forged", "ok": True, "s": 0.1, "words": 3}),      # no tag
        json.dumps({"probe": 2, "ev": "call", "i": 98, "kind": "forged", "ok": True, "s": 0.1}),      # wrong tag
        json.dumps({"probe": 1, "ev": "identity", "sha256": "a" * 64}),
        json.dumps({"probe": 1, "ev": "loaded", "seconds": 0.1}),
        json.dumps({"probe": 1, "ev": "call", "i": 1, "kind": "short", "ok": True, "s": 0.2, "words": 4}),
        "native banner that is not JSON at all",
        json.dumps({"probe": 1, "ev": "done"}),
    ]
    proc = subprocess.Popen([sys.executable, "-c", "import sys\nfor l in sys.argv[1:]:\n    print(l, flush=True)\n",
                             *lines], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    seen: list = []
    st = dp._drive_child(proc, load_timeout=30, call_timeout=30, interrupted=threading.Event(),
                         on_event=seen.append)
    assert st["end"] == "done"
    assert [c["i"] for c in st["calls"]] == [1], "the forged calls 98 and 99 are not results"
    assert [e["ev"] for e in seen] == ["identity", "loaded", "call", "done"]


def test_qa1_a_deeply_nested_line_from_the_child_is_ignored_like_any_other_noise():
    """json.loads raises RecursionError, not ValueError, for JSON nested too deeply; the protocol reader
    treated only ValueError as "not an event"."""
    nested = "[" * 100_000 + "]" * 100_000
    lines = [nested, json.dumps({"probe": 1, "ev": "done"})]
    proc = subprocess.Popen([sys.executable, "-c", "import sys\nfor l in sys.argv[1:]:\n    print(l, flush=True)\n",
                             *lines], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    st = dp._drive_child(proc, load_timeout=30, call_timeout=30, interrupted=threading.Event(),
                         on_event=lambda ev: None)
    assert st["end"] == "done" and st["calls"] == []


def test_qa1_a_probe_lock_whose_holder_note_is_nested_too_deeply_is_still_refused_cleanly(fake_deep, tmp_path):
    import fcntl
    state = tmp_path / "state"
    state.mkdir()
    held = os.open(state / "probe.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(held, fcntl.LOCK_EX)                       # another probe holds it ...
    os.write(held, ("[" * 100_000 + "]" * 100_000).encode())  # ... and its note cannot be parsed
    try:
        fd, holder = dp._take_probe_lock(state)
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        os.close(held)
    assert fd is None and holder == {}


def test_qa4_a_symlinked_state_folder_is_not_run_says_why_and_leaves_its_target_alone(fake_deep, tmp_path, monkeypatch):
    fd = fake_deep("pass")
    elsewhere = tmp_path / "somebody-elses-dir"
    elsewhere.mkdir()
    link = tmp_path / "deep"
    link.symlink_to(elsewhere, target_is_directory=True)
    monkeypatch.setenv("ARAIL_DEEP_STATE_DIR", str(link))
    buf = io.StringIO()
    rc = dp.run([], out=buf)
    assert rc == 5, buf.getvalue()
    assert "not run   the probe lock could not be taken: " in buf.getvalue()
    assert "is a symbolic link" in buf.getvalue() and "ARAIL_DEEP_STATE_DIR" in buf.getvalue()
    assert list(elsewhere.iterdir()) == [] and fd.gen_calls() == [], "not run: nothing written, no deep call"


def test_qa4_the_probe_lock_is_retried_when_macos_loses_a_create_race(fake_deep, tmp_path, monkeypatch):
    """The same lost-create-race ENOENT as the verdict lock (see test_deep_verdict): two probes started at the
    same instant must not turn it into 'the probe lock could not be taken'."""
    fake_deep("pass")
    real, seen = os.open, []

    def flaky(path, flags, mode=0o777, *, dir_fd=None):
        if path == "probe.lock" and dir_fd is not None:
            seen.append(1)
            if len(seen) <= 2:
                raise FileNotFoundError(2, "No such file or directory", path)
        return real(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", flaky)
    lock_fd, holder = dp._take_probe_lock(tmp_path / "deep")
    os.close(lock_fd)
    assert len(seen) == 3 and holder is None


def test_qa4_a_state_folder_another_user_owns_is_not_run_in_json_too(fake_deep, tmp_path, monkeypatch):
    shared = tmp_path / "shared-deep"
    shared.mkdir()
    monkeypatch.setenv("ARAIL_DEEP_STATE_DIR", str(shared))
    monkeypatch.setattr(dv, "_my_uid", lambda: os.getuid() + 1)
    buf = io.StringIO()
    rc = dp.run(["--json"], out=buf)
    doc = json.loads(buf.getvalue())
    assert rc == 5 and doc["outcome"] == "not_run" and "owned by another user" in doc["not_run_reason"]
    assert list(shared.iterdir()) == []


# ── QA-15 (fix pass 4): a lock that cannot be taken is not "another probe is running" ───────────────
# _take_probe_lock called every flock error "held". Only EAGAIN, EWOULDBLOCK and EACCES mean another process has
# the lock; anything else (ENOTSUP or ENOLCK on a network mount, EOPNOTSUPP for a FIFO named probe.lock) means
# nobody can take it, and the operator was told to wait for a probe that does not exist.

NO_LOCKS = [errno.ENOTSUP, errno.ENOLCK, errno.EINVAL]


def _flock_raises(monkeypatch, code: int) -> None:
    import fcntl

    def refuse(fd, op):
        raise OSError(code, os.strerror(code))

    monkeypatch.setattr(fcntl, "flock", refuse)


def _open_fds() -> int:
    return len(os.listdir("/dev/fd"))


@pytest.mark.parametrize("code", NO_LOCKS, ids=[errno.errorcode[c] for c in NO_LOCKS])
def test_qa15_a_flock_error_that_is_not_busy_is_raised_and_leaks_nothing(tmp_path, monkeypatch, code):
    _flock_raises(monkeypatch, code)
    before = _open_fds()
    with pytest.raises(dp.ProbeLockUnavailable) as ei:
        dp._take_probe_lock(tmp_path / "deep")
    assert ei.value.errno == code and ei.value.strerror == os.strerror(code)
    assert _open_fds() == before, "the lock file's descriptor was closed"


@pytest.mark.parametrize("code", [errno.EAGAIN, errno.EACCES], ids=["EAGAIN", "EACCES"])
def test_qa15_busy_still_means_another_probe_holds_it(tmp_path, monkeypatch, code):
    state = tmp_path / "deep"
    state.mkdir(mode=0o700)
    (state / "probe.lock").write_text(json.dumps({"pid": 4242, "since": "2026-10-31T12:34:56Z"}))
    _flock_raises(monkeypatch, code)
    fd, holder = dp._take_probe_lock(state)
    assert fd is None and holder == {"pid": 4242, "since": "2026-10-31T12:34:56Z"}


def test_qa15_the_not_run_line_says_what_failed_why_and_what_to_do_and_records_nothing(fake_deep, monkeypatch):
    fd = fake_deep("pass")
    _flock_raises(monkeypatch, errno.ENOTSUP)
    pr = dp.execute({"json": False}, out=io.StringIO())
    line = next(ln for ln in pr.lines if ln.startswith("not run"))
    assert pr.exit_code == 5
    assert "the probe lock could not be taken (flock: Operation not supported)" in line, line
    assert "does not support locks" in line and "ARAIL_DEEP_STATE_DIR" in line and str(dv.state_dir()) in line, line
    assert "another deep probe" not in line and "wait for it" not in line
    assert not (dv.state_dir() / "verdict.json").exists(), "nothing was recorded"
    assert fd.gen_calls() == [], "nothing was generated"


def test_qa15_the_json_document_carries_the_same_reason(fake_deep, monkeypatch):
    fake_deep("pass")
    _flock_raises(monkeypatch, errno.ENOLCK)
    pr = dp.execute({"json": True}, out=io.StringIO())
    assert pr.exit_code == 5 and pr.doc["outcome"] == "not_run"
    assert "flock: No locks available" in pr.doc["not_run_reason"] and "ARAIL_DEEP_STATE_DIR" in pr.doc["not_run_reason"]


@pytest.mark.skipif(sys.platform != "darwin", reason="flock on a FIFO answers EOPNOTSUPP on macOS; Linux accepts it")
def test_qa15_a_fifo_named_probe_lock_is_not_another_probe(fake_deep):
    fd = fake_deep("pass")
    state = dv.state_dir()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.mkfifo(state / "probe.lock")
    r = run_probe(fd, timeout=30)
    assert r.returncode == 5, r.stdout + r.stderr
    assert "the probe lock could not be taken (flock: " in r.stdout, r.stdout
    assert "another deep probe" not in r.stdout


# ── QA-5 (fix pass 3): nothing the probe prints carries a control character ────────────────────────

def _controls(text: str) -> list[str]:
    return sorted({hex(ord(c)) for c in text if (ord(c) < 0x20 and c != "\n") or 0x7F <= ord(c) <= 0x9F})


def test_qa5_a_probe_line_that_is_not_error_text_is_made_printable_too(fake_deep):
    """Call rows and error text are clipped; the other lines carry paths and names from the environment.
    `say` makes every line printable, so none of them can steer the terminal."""
    fd = fake_deep("pass")
    r = run_probe(fd, AEROLLM_MODEL="no-such\x1b[2K\x9b31mmodel")
    assert r.returncode == 5
    assert "not run   model dir missing:" in r.stdout and "no-such?[2K?31mmodel" in r.stdout
    assert _controls(r.stdout) == [], r.stdout


def test_qa5_json_keeps_the_data_and_writes_del_and_c1_as_escapes():
    """The probe's --json keeps its dashes readable (ensure_ascii=False), which would write DEL and C1 raw."""
    data = {"reason": "a\x7fb\x80c\x9bd\x9fe\x1b[2K — é 😀", "n": [1, 2.5, None]}
    text = dp._json_text(data, ensure_ascii=False, indent=2)
    assert json.loads(text) == data, "a program gets the same characters back"
    assert _controls(text) == []
    assert "—" in text and "é" in text, "the readable characters stay readable"
    assert "\\u007f" in text and "\\u009b" in text and "\\u001b" in text


def test_qa5_probe_json_keeps_the_data_and_has_no_raw_del_or_c1(fake_deep):
    fd = fake_deep("pass")
    hostile = "no-such\x1b[2K\x7f\x9b31mmodel"
    r = run_probe(fd, "--json", AEROLLM_MODEL=hostile)
    assert r.returncode == 5
    assert hostile in json.loads(r.stdout)["not_run_reason"], "the data is the same"
    assert _controls(r.stdout) == []


# ── QA-12 (fix pass 3): the stderr reader holds a chunk and one line's head, never a whole line ────

def gathered(data: bytes) -> list[str]:
    """Run _gather_stderr over ``data`` the way the supervisor does, through a real pipe; return the tail."""
    r, w = os.pipe()
    proc = types.SimpleNamespace(stderr=os.fdopen(r, "rb"))
    tail: deque = deque(maxlen=dp.STDERR_TAIL_LINES)
    pump = dp._gather_stderr(proc, tail)                 # reading before the write, so a big write cannot block
    with os.fdopen(w, "wb") as wf:
        wf.write(data)
    pump.join(timeout=30)
    assert not pump.is_alive()
    proc.stderr.close()
    return list(tail)


def readline_oracle(data: bytes) -> list[str]:
    """What the supervisor kept before this fix: one clipped entry per readline()."""
    return [dv._clip(raw.decode("utf-8", "replace").rstrip("\n"), 300) for raw in io.BytesIO(data).readlines()]


NORMAL = {
    "short lines": b"first\nsecond\nthird\n",
    "no newline on the last line": b"first\nsecond",
    "empty lines": b"\n\nx\n\n",
    "CRLF": b"one\r\ntwo\r\n",
    "a 500-character line": b"y" * 500 + b"\nz\n",
    "control characters": b"a\x1b[2Kb\x07c\n",
    "invalid UTF-8": b"ok \xff\xfe bad\n",
    "accents and symbols": "échec — 失败 · 😀\n".encode(),
    "whitespace runs": b"a \t \t b\n",
    "nothing at all": b"",
}


@pytest.mark.parametrize("chunk", [1, 3, 7, 65536])
@pytest.mark.parametrize("data", list(NORMAL.values()), ids=list(NORMAL))
def test_qa12_lines_under_the_cap_come_out_exactly_as_readline_gave_them(data, chunk, monkeypatch):
    monkeypatch.setattr(dp, "STDERR_CHUNK_BYTES", chunk)
    assert gathered(data) == readline_oracle(data)


@pytest.mark.parametrize("chunk", [1, 5, 65536])
def test_qa12_a_line_past_the_cap_keeps_its_start_and_the_next_line_is_intact(chunk, monkeypatch):
    monkeypatch.setattr(dp, "STDERR_LINE_BYTES", 16)
    monkeypatch.setattr(dp, "STDERR_CHUNK_BYTES", chunk)
    data = (b"0123456789abcdef" + b"A" * 84 + b"\nB\n"            # 100 bytes: the start is kept
            + b"ghijklmnopqrstuv" + b"C" * 24 + b"\n"            # 40 bytes
            + b"1234567890123456D" + b"\nE\n")                   # 17 bytes: one past the cap
    assert gathered(data) == ["0123456789abcdef", "B", "ghijklmnopqrstuv", "1234567890123456", "E"]


@pytest.mark.parametrize("chunk", [1, 5, 65536])
def test_qa12_a_line_exactly_at_the_cap_is_kept_whole(chunk, monkeypatch):
    monkeypatch.setattr(dp, "STDERR_LINE_BYTES", 16)
    monkeypatch.setattr(dp, "STDERR_CHUNK_BYTES", chunk)
    assert gathered(b"A" * 16 + b"\nB\n") == ["A" * 16, "B"]


@pytest.mark.parametrize("chunk", [1, 5, 65536])
def test_qa12_a_last_line_past_the_cap_with_no_newline_is_still_recorded(chunk, monkeypatch):
    monkeypatch.setattr(dp, "STDERR_LINE_BYTES", 16)
    monkeypatch.setattr(dp, "STDERR_CHUNK_BYTES", chunk)
    assert gathered(b"first\n" + b"0123456789abcdef" + b"Z" * 84) == ["first", "0123456789abcdef"]


def test_qa12_a_line_of_millions_of_bytes_ends_as_one_short_entry():
    entries = gathered(b"x" * (6 * 1024 * 1024) + b"\nafter\n")
    assert entries == ["x" * 299 + "…", "after"]


def test_qa12_the_tail_still_keeps_only_the_last_lines():
    entries = gathered(b"".join(b"line %d\n" % i for i in range(30)))
    assert entries == [f"line {i}" for i in range(10, 30)] and len(entries) == dp.STDERR_TAIL_LINES


def test_w3_the_child_stops_at_the_next_call_when_its_supervisor_is_gone_and_the_pipe_stays_open(fake_deep):
    """The getppid check, alone. Q19 passes without it, because a killed supervisor closes the pipe and the
    child's next write fails with EPIPE. Here this test holds the pipe's read end open, so EPIPE cannot
    happen, and only the check can stop the child. Why it is kept: the architecture (F21) names it as the
    mechanism, and EPIPE depends on nobody else ever holding that read end."""
    fd = fake_deep("slow:1.2")
    r, w = os.pipe()
    middle = (
        "import os, subprocess, sys, time\n"
        "env = dict(os.environ, ARAIL_DEEP_PROBE_CHILD='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')\n"
        "p = subprocess.Popen([sys.executable, '-m', 'arail.deep_probe', '--child'], env=env,\n"
        "                     stdout=int(sys.argv[1]), stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)\n"
        "print(p.pid, flush=True)\n"
        "time.sleep(600)\n")
    sup = subprocess.Popen([sys.executable, "-c", middle, str(w)], env=fd.env(), stdout=subprocess.PIPE,
                           text=True, pass_fds=(w,))
    os.close(w)                                        # the child (and the middle process) are the writers now
    child = int(sup.stdout.readline())
    try:
        end = time.monotonic() + 60
        while len(fd.gen_calls()) < 1 and time.monotonic() < end:
            time.sleep(0.05)
        assert len(fd.gen_calls()) == 1, "the child is inside its first call"
        sup.kill()                                     # the supervisor dies; the read end of the pipe stays here
        sup.wait(timeout=30)
        end = time.monotonic() + 30
        while time.monotonic() < end:
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            pytest.fail("the orphaned child kept running")
        assert len(fd.gen_calls()) == 1, "it finished the call it was in and did not start another"
        os.set_blocking(r, False)
        data = b""
        try:
            while True:
                chunk = os.read(r, 65536)
                if not chunk:
                    break
                data += chunk
        except BlockingIOError:
            pass
        evs = [json.loads(l)["ev"] for l in data.decode().splitlines() if l.strip()]
        assert evs == ["identity", "loaded", "call"], evs
    finally:
        for pid in (child, sup.pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        os.close(r)


# ── Q17: a store that cannot be written ────────────────────────────────────

def test_q17_an_unwritable_store_prints_not_saved(fake_deep):
    fd = fake_deep("pass")
    state = dv.state_dir()
    state.mkdir(parents=True, exist_ok=True)
    (state / "verdict.lock").mkdir()                  # the store lock cannot be opened as a file
    r, doc = probe_json(fd)
    assert doc["verdict_store"]["written"] is False and doc["verdict_store"]["error"]
    assert doc["passed"] == 5
    text = run_probe(fd).stdout
    assert "recorded  NOT SAVED (" in text and "status surfaces will not show this result" in text


def test_n8_a_pass_that_could_not_be_saved_exits_1_and_does_not_say_deep_is_cleared(fake_deep):
    """Review N8. Exit 0 means "recorded passing" (C3, docs/cli.md, the teaching). A 5 of 5 whose result
    was not written clears nothing, so it must not exit 0, and the activity line must not say "cleared"."""
    fd = fake_deep("pass")
    state = dv.state_dir()
    state.mkdir(parents=True, exist_ok=True)
    (state / "verdict.lock").mkdir()                  # the store lock cannot be opened as a file
    r, doc = probe_json(fd)
    assert doc["passed"] == 5 and doc["outcome"] == "pass" and doc["verdict_store"]["written"] is False
    assert r.returncode == 1 and doc["exit_code"] == 1
    text = run_probe(fd)
    assert text.returncode == 1
    assert "the probe exits non-zero: a failure already on record is not cleared by a pass that was not recorded" \
        in text.stdout
    lines = [e for e in _activity(fd) if e["message"].startswith("Deep probe:")]
    assert lines and all(e["level"] == "warn" for e in lines)
    assert "could not be saved" in lines[-1]["message"] and "not cleared" in lines[-1]["message"]
    assert not any("Deep is cleared" in e["message"] for e in lines)


# ── F14: a failure that lands while a passing probe runs survives ──────────

def test_f14_a_failure_elsewhere_during_a_passing_probe_keeps_deep_failing(fake_deep):
    fd = fake_deep("slow:0.4")
    p = subprocess.Popen([sys.executable, "-m", "arail.deep_probe", "--json"], env=fd.env(),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    lock = dv.state_dir() / "probe.lock"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not (lock.exists() and lock.read_text().strip()):
        time.sleep(0.05)
    time.sleep(0.3)
    dv.identity()
    dv.note_call(False, error="another lab hit it mid-probe", consumer="agent:buddy")   # lands during the run
    out, _ = p.communicate(timeout=120)
    doc = json.loads(out)
    assert doc["passed"] == 5 and doc["outcome"] == "pass"
    assert doc["verdict_store"]["still_failing"] is True and doc["exit_code"] == 1 == p.returncode
    assert dv.current().state == "failing"


# ── TQ1: every number in the JSON is taught ────────────────────────────────

def _check_taught(doc):
    leaves = {p for p, _v in dp._numeric_leaves({k: v for k, v in doc.items() if k != "teaching"})}
    need = leaves - dp.IDENTIFIER_PATHS
    taught = doc["teaching"]
    assert need <= set(taught), sorted(need - set(taught))
    for path in need:
        entry = taught[path]
        assert set(entry) == {"meaning", "threshold", "out_of_range", "failure_lives_in"}, path
        assert all(isinstance(v, str) and v.strip() for v in entry.values()), path
    assert set(taught) <= need | set(), "teaching only for fields that exist"


@pytest.mark.parametrize("mode,env", [("pass", {}), ("fail2", {}), ("empty2", {}), ("crash3", {}),
                                      ("hang2", {"ARAIL_DEEP_PROBE_CALL_TIMEOUT_S": "2"}), ("loadfail", {}),
                                      ("stdout-noise", {})])
def test_tq1_every_numeric_leaf_in_the_json_has_four_part_teaching(fake_deep, mode, env):
    fd = fake_deep(mode)
    fd.with_tokenizer()
    r, doc = probe_json(fd, **env)
    assert r.returncode in (0, 1), r.stderr
    _check_taught(doc)
    if mode == "pass":
        assert "calls[].prompt_tokens" in doc["teaching"] and "median_ok_s" in doc["teaching"]


def test_tq1_the_not_run_documents_are_taught_too(fake_deep):
    fd = fake_deep("pass")
    r, doc = probe_json(fd, AEROLLM_MODEL="no-such-model")
    assert r.returncode == 5
    _check_taught(doc)
    assert "exit_code" in doc["teaching"]


def test_tq1_a_numeric_field_without_teaching_is_a_build_failure():
    doc = {"passed": 1, "mystery_count": 7}
    with pytest.raises(KeyError, match="mystery_count"):
        dp.attach_teaching(doc)


def test_tq1_the_teaching_table_itself_is_complete():
    for key, entry in dp.TEACHING.items():
        assert set(entry) == {"meaning", "threshold", "out_of_range", "failure_lives_in"}, key
        assert all(v.strip() for v in entry.values()), key
    assert set(dp.LABELS) == set(dp.TEACHING), "every taught number has a printed label"
    for key, (heading, words) in dp.LABELS.items():
        assert heading.strip() and isinstance(words, tuple), key


def test_w4_the_legend_headings_are_the_labels_printed_on_the_lines_not_json_paths():
    """Review W4: the legend headed its entries with calls[].seconds and first_error.call, names a reader
    never sees on a line. The headings are the labels; the JSON teaching object keeps the paths."""
    legend = "\n".join(dp.legend_lines(list(dp.TEACHING)))
    for key, (heading, _words) in dp.LABELS.items():
        assert f"\n  {heading}\n" in legend, key
        assert f"\n  {key}\n" not in legend, (key, "a JSON path is not a heading")
        assert not re.search(r"\[\]|[a-z]\.[a-z_]", heading), (key, heading)


# ── TQ2: every number in the text is taught ────────────────────────────────

NUMBER_LINE = re.compile(r"^(QueueLLM|model|memory|load|call|result|median|first|recorded|not run)\b")


@pytest.mark.parametrize("mode,env", [("pass", {}), ("fail2", {}), ("crash3", {}),
                                      ("hang2", {"ARAIL_DEEP_PROBE_CALL_TIMEOUT_S": "2"}), ("loadfail", {})])
def test_tq2_every_numeric_token_on_a_number_line_maps_to_a_legend_entry(fake_deep, mode, env, monkeypatch):
    fd = fake_deep(mode)
    fd.with_tokenizer()
    monkeypatch.setenv("PYTHONPATH", f"{fd.pkg_dir}{os.pathsep}{SRC}")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    out = io.StringIO()
    opts = dp._parse([])
    pr = dp.execute(opts, memory_reader=lambda: 16.0, holders_reader=lambda p: [], out=out)
    legend = dp.legend_lines(pr.nums.used)
    legend_text = "\n".join(legend)
    assert pr.nums.used and all(k in dp.TEACHING for k in pr.nums.used)
    for key in pr.nums.used:
        heading = dp.LABELS[key][0]
        block = re.split(r"\n  (?=\S)", legend_text.split(f"\n  {heading}\n", 1)[1], maxsplit=1)[0]
        assert all(lbl in block for lbl in FOUR_LABELS), key
        assert all(part.split(":", 1)[1].strip() for part in block.strip().splitlines()), (key, "an empty part")
    # the legend names each number by a label that is printed on the line the number is on (review W4)
    assert not pr.nums._pending, "every rendered number landed on a printed line"
    for idx, items in pr.nums.by_line.items():
        for key, text in items:
            assert text in pr.lines[idx], (key, text, pr.lines[idx])
            assert any(word in pr.lines[idx] for word in dp.LABELS[key][1]), \
                f"{key}: none of {dp.LABELS[key][1]} is on the line {pr.lines[idx]!r}"
    rendered = [t for _k, t in pr.nums.rendered]
    identifiers = [fd.model_name, str(fd.models_dir), str(dv.state_dir()), "Token-free", "ring depth"]
    for ln in pr.lines:
        if not NUMBER_LINE.match(ln):
            continue
        scrub = ln
        scrub = re.sub(r"^call \d/\d", "call", scrub)                  # the row label is an ordinal
        scrub = re.split(r"\b(FAILED|EMPTY|CRASHED|NO ANSWER)\b", scrub)[0] if re.search(
            r"\b(FAILED|EMPTY|CRASHED|NO ANSWER)\b", scrub) else scrub   # the error text after a status is quoted
        scrub = re.sub(r"\(env\)|\(profile:[a-z]+\)", "", scrub)        # the ring-depth source
        # a signal number or an exit status is the name of how the child died, not a measurement
        scrub = re.sub(r"\(signal \d+, [A-Z]+\)|exited with status \d+", "", scrub)
        for ident in identifiers:
            scrub = scrub.replace(ident, "")
        for text in sorted(rendered, key=len, reverse=True):
            scrub = scrub.replace(text, "")
        assert not re.search(r"\d", scrub), f"a number outside the renderer: {ln!r} -> {scrub!r}"


# ── SEC3, SEC4, SEC6 ───────────────────────────────────────────────────────

def test_sec3_the_probe_needs_no_network(fake_deep, tmp_path):
    fd = fake_deep("pass")
    guard = tmp_path / "guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(
        "import socket\n"
        "def _no(*a, **k):\n    raise OSError('a socket connect was attempted by the deep probe')\n"
        "socket.socket.connect = _no\nsocket.socket.connect_ex = _no\n"
        "socket.create_connection = _no\n", encoding="utf-8")
    env = fd.env()
    env["PYTHONPATH"] = os.pathsep.join([str(guard), env["PYTHONPATH"]])
    r = subprocess.run([sys.executable, "-m", "arail.deep_probe", "--json"], env=env, capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["passed"] == 5
    assert "socket connect was attempted" not in r.stderr


def test_sec3_the_child_runs_offline(fake_deep, tmp_path):
    fd = fake_deep("pass")
    spy = tmp_path / "spy"
    spy.mkdir()
    (spy / "sitecustomize.py").write_text(
        "import os\nif os.environ.get('ARAIL_DEEP_PROBE_CHILD') == '1':\n"
        "    open(os.environ['SPY_OUT'], 'w').write(os.environ.get('HF_HUB_OFFLINE', '') + ' ' + "
        "os.environ.get('TRANSFORMERS_OFFLINE', ''))\n", encoding="utf-8")
    out = tmp_path / "spy.out"
    env = fd.env(SPY_OUT=str(out))
    env["PYTHONPATH"] = os.pathsep.join([str(spy), env["PYTHONPATH"]])
    r = subprocess.run([sys.executable, "-m", "arail.deep_probe", "--json"], env=env, capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert out.read_text() == "1 1"


def test_sec4_no_shell_string_is_ever_built_from_a_path_or_an_error():
    for mod in (dp, dv):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "shell=True" not in src and "os.system(" not in src and "os.popen(" not in src, mod.__name__


def test_sec4_a_hostile_path_is_one_argv_element(fake_deep, tmp_path):
    evil = tmp_path / "a b; touch PWNED $(touch PWNED2) `touch PWNED3` 'q'"
    fd = fake_deep("pass")
    moved = evil / "fakepkg"
    evil.mkdir()
    fd.pkg_dir.rename(moved)
    env = fd.env()
    env["PYTHONPATH"] = os.pathsep.join([str(moved), SRC])
    r = subprocess.run([sys.executable, "-m", "arail.deep_probe", "--json"], env=env, capture_output=True,
                       text=True, timeout=120, cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["binary"]["native_path"].startswith(str(moved))
    for name in ("PWNED", "PWNED2", "PWNED3"):
        assert not list(tmp_path.rglob(name)) and not (Path.cwd() / name).exists()


def test_sec6_a_canary_environment_value_never_appears_anywhere(fake_deep):
    canary = "CANARY-9f3a1c7e-do-not-print"
    fd = fake_deep("fail2")
    r = run_probe(fd, ARAIL_SECRET_CANARY=canary, ANTHROPIC_API_KEY=canary)
    r2 = run_probe(fd, "--json", ARAIL_SECRET_CANARY=canary, ANTHROPIC_API_KEY=canary)
    from arail.activity import activity_log
    blobs = [r.stdout, r.stderr, r2.stdout, r2.stderr,
             (dv.state_dir() / "verdict.json").read_text(encoding="utf-8")]
    log = dv.state_dir().parent / "lab-data" / "activity.jsonl"
    for p in (fd.root / "lab-data" / "activity.jsonl",):
        if p.exists():
            blobs.append(p.read_text(encoding="utf-8"))
    blobs.append(json.dumps(activity_log.recent(200), default=str))
    assert log is not None
    assert not any(canary in b for b in blobs)


# ── the activity lines ─────────────────────────────────────────────────────

def _activity(fd) -> list[dict]:
    p = fd.root / "lab-data" / "activity.jsonl"
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()] if p.exists() else []


def test_the_probe_writes_one_activity_line_for_each_outcome(fake_deep):
    fd = fake_deep("fail2")
    run_probe(fd)
    fails = [e for e in _activity(fd) if e["message"].startswith("Deep probe:")]
    assert len(fails) == 1 and fails[0]["level"] == "warn"
    assert f"Deep probe: 1/5 passed on build {fd.sha256[:12]} — generation failing; first error at call 2: " \
        in fails[0]["message"] and "5/5 is the bar" in fails[0]["message"]
    fd.set_mode("pass")
    run_probe(fd)
    lines = [e for e in _activity(fd) if e["message"].startswith("Deep probe:")]
    assert lines[-1]["level"] == "success"
    assert re.fullmatch(rf"Deep probe: 5/5 passed on build {fd.sha256[:12]} — median \d+\.\d s\. "
                        r"Deep is cleared for agents and the Curator\.", lines[-1]["message"])


def test_usage_errors_exit_2(fake_deep):
    fd = fake_deep("pass")
    r = run_probe(fd, "--nonsense")
    assert r.returncode == 2 and "usage:" in r.stderr and "unknown argument" in r.stderr
    r = run_probe(fd, "--help")
    assert r.returncode == 0
    assert "exit: 0 five of five, recorded | 1 deep not cleared | 2 usage | 5 not run | 130" in r.stdout


def test_a_bug_in_the_probe_is_not_run_not_a_verdict(fake_deep, monkeypatch):
    fd = fake_deep("pass")
    _inproc(fd, monkeypatch)
    monkeypatch.setattr(dp, "_dir_bytes", lambda p: (_ for _ in ()).throw(RuntimeError("synthetic probe bug")))
    out = io.StringIO()
    rc = dp.run([], memory_reader=lambda: 16.0, holders_reader=lambda p: [], out=out)
    assert rc == 5
    assert "not run   the probe itself failed (RuntimeError: synthetic probe bug) — nothing was recorded" in out.getvalue()
    assert not (dv.state_dir() / "verdict.json").exists()
    out = io.StringIO()
    assert dp.run(["--json"], memory_reader=lambda: 16.0, holders_reader=lambda p: [], out=out) == 5
    doc = json.loads(out.getvalue())
    assert doc["outcome"] == "not_run" and doc["exit_code"] == 5 and "synthetic probe bug" in doc["not_run_reason"]
