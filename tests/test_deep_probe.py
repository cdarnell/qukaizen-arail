"""./arailctl deep probe: the supervisor, the child and the teaching.

C3 of sprints/2026-10-29-arail-deep-honest/ARCHITECTURE.md. Tests Q1 to Q19,
TQ1, TQ2, SEC3, SEC4 and SEC6 are named for the architecture's list (Q16 is in
test_deep_probe_prompts.py, Q20 in test_deep_probe_cli.py). The supervisor and
the child run as real subprocesses over the fake runtime.
"""
from __future__ import annotations

import io
import json
import os
import re
import signal
import subprocess
import sys
import time
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
        block = re.split(r"\n  (?=\S)", legend_text.split(f"\n  {key}\n", 1)[1], maxsplit=1)[0]
        assert all(lbl in block for lbl in FOUR_LABELS), key
        assert all(part.split(":", 1)[1].strip() for part in block.strip().splitlines()), (key, "an empty part")
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
    assert r.returncode == 0 and "exit: 0 five of five | 1 fewer than five | 2 usage | 5 not run | 130" in r.stdout


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
