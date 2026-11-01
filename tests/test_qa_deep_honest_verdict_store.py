"""QA, sprint 2026-10-29-arail-deep-honest: attacks on the deep verdict store (arail.deep_verdict).

Written by the QA pass (sprints/2026-10-29-arail-deep-honest/TEST_REPORT.md). How to read this file:

* A plain test that FAILS is an open QA finding (QA-n in TEST_REPORT.md). It states the behaviour the
  architecture promises (C1, C2: "a corrupt file reads as failing", "only a 5/5 probe clears a failure")
  and stays red until the builder fixes the product. QA does not edit product code.
* A test that passes is a guard QA added where the builder's tests did not reach.

Everything runs on the fake runtime (tests/fakes) in a per-test state dir (the autouse fixture in
tests/conftest.py), so no real wheel, model or lab is touched.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from arail import deep_verdict as dv
from tests.fakes.fake_deep_runtime import fake_deep  # noqa: F401  (fixture)
from tests.test_deep_verdict import probe_result, store_path

SCHEMA = "arail.deep-verdict/v1"


def _write_store(data: "bytes | str | dict") -> Path:
    p = store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, dict):
        data = json.dumps(data)
    p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return p


def _failure(last_ts: "float | str", *, since: "str | int" = "2026-10-31T00:00:00Z", count=1) -> dict:
    return {"since": since, "since_ts": 1.0, "last_at": "2026-10-31T00:00:00Z", "last_ts": last_ts,
            "source": "call", "consumer": "agent:buddy", "lab": "root", "error": "RuntimeError: e",
            "last_error": "RuntimeError: e", "count": count}


def _doc_with_failure(sha: str, failure) -> dict:
    return {"schema": SCHEMA, "updated_at": "2026-10-31T00:00:00Z",
            "binaries": {sha: {"module": "aerollm_api", "native_path": "/x", "bytes": 1, "version": "v",
                               "failure": failure, "last_probe": None, "last_pass_at": None}}}


def _corrupt_copies() -> list[Path]:
    return sorted(dv.state_dir().glob("verdict.json.corrupt-*"))


# ── QA-1: a store the JSON parser cannot finish must fail closed, not open ──────────────────────────
# deep_verdict._read_store catches OSError, ValueError and UnicodeDecodeError. json.loads raises
# RecursionError (a RuntimeError) on deeply nested input, which escapes to current()'s catch-all and
# comes back as "untested": explain() says ok and every agent calls the broken binary again. The
# writers raise the same error, so a failed deep call is not recorded (not even in memory) and a
# probe cannot repair the file. C1: "Corrupt or wrong-schema file: fail closed."

NESTED = '{"schema": "arail.deep-verdict/v1", "binaries": {}, "x": ' + "[" * 100_000 + "]" * 100_000 + "}"


def test_qa1_a_deeply_nested_verdict_file_reads_as_failing_not_untested(fake_deep):
    fake_deep("pass")
    dv.identity()
    _write_store(NESTED)
    v = dv.current()
    assert v.state == "failing", (
        f"a verdict file json cannot parse must fail closed (C1); got {v.state!r}: {v.detail[:160]}")


def test_qa1_a_deep_call_that_fails_while_the_file_is_deeply_nested_is_still_recorded(fake_deep):
    fake_deep("pass")
    dv.identity()
    _write_store(NESTED)
    dv.note_call(False, error="RuntimeError: rms_norm has 1 elements", consumer="agent:buddy")
    assert dv.current().state == "failing", "the failure was lost: neither stored nor kept in memory"
    assert _corrupt_copies(), "the next writer moves an unreadable file aside (C1)"


def test_qa1_a_probe_repairs_a_deeply_nested_file(fake_deep):
    fake_deep("pass")
    b = dv.identity()
    _write_store(NESTED)
    written, err = dv.record_probe(probe_result(b, "fail", passed=1, started_ts=dv._now(),
                                                first_error={"call": 2, "kind": "long", "error": "e"}))
    assert written, f"a probe must be able to replace an unreadable store; record_probe said: {err}"


# ── QA-2: a schema-valid file whose failure entry is not a failure object must fail closed ──────────
# _validate_doc checks only the top level. A failure that is not an object (or one with a non-string
# 'since') raises inside _assemble, and current() returns "untested". C2: "failure not null means
# failing". The writers raise too, so the next failed call is lost and a probe reports NOT SAVED.

MALFORMED = {
    "failure is a string": "yes",
    "failure is a number": 5,
    "failure is a list": ["x"],
    "failure is an empty object": {},
    "failure.since is a number": _failure(1.0, since=12345),
}


@pytest.mark.parametrize("failure", list(MALFORMED.values()), ids=list(MALFORMED))
def test_qa2_a_malformed_failure_entry_reads_as_failing(fake_deep, failure):
    fake_deep("pass")
    b = dv.identity()
    _write_store(_doc_with_failure(b.sha256, failure))
    v = dv.current()
    assert v.state == "failing", f"C2: a failure that is not null means failing; got {v.state!r}: {v.detail[:160]}"


@pytest.mark.parametrize("failure", ["yes", 5, ["x"]], ids=["string", "number", "list"])
def test_qa2_a_failed_call_and_a_probe_still_land_on_a_malformed_entry(fake_deep, failure):
    fake_deep("pass")
    b = dv.identity()
    _write_store(_doc_with_failure(b.sha256, failure))
    dv.note_call(False, error="RuntimeError: boom", consumer="agent:buddy")
    rec = json.loads(store_path().read_text(encoding="utf-8"))["binaries"].get(b.sha256, {})
    assert isinstance(rec.get("failure"), dict) and rec["failure"].get("count", 0) >= 1, (
        f"the failed call was not recorded: the entry is still {rec.get('failure')!r}")
    written, err = dv.record_probe(probe_result(b, "pass", passed=5, started_ts=dv._now() + 1))
    assert written, f"record_probe could not write over the malformed entry: {err}"


# ── QA-3: a failure stamped later than now (clock stepped back, NaN, Infinity) can never be cleared ──
# record_probe clears a failure only when failure.last_ts < probe.started_ts. A failure written while
# the clock was ahead (then corrected), or a last_ts of NaN or Infinity, never satisfies that, so a
# probe that passes 5/5 leaves deep failing until the wall clock catches up (never, for NaN or
# Infinity), and the probe tells the user "a deep call failed elsewhere while the probe ran", which did
# not happen. The only way out today is to edit or delete verdict.json by hand.

@pytest.mark.parametrize("last_ts", ["NaN", "Infinity", "now+3600"], ids=["NaN", "Infinity", "one hour ahead"])
def test_qa3_a_failure_with_a_future_or_non_finite_time_is_cleared_by_a_passing_probe(fake_deep, last_ts):
    fake_deep("pass")
    b = dv.identity()
    ts = repr(time.time() + 3600) if last_ts == "now+3600" else last_ts
    raw = json.dumps(_doc_with_failure(b.sha256, _failure(0.0))).replace('"last_ts": 0.0', f'"last_ts": {ts}')
    _write_store(raw)
    assert dv.current().state == "failing"
    written, err = dv.record_probe(probe_result(b, "pass", passed=5, started_ts=dv._now()))
    assert written, err
    v = dv.current()
    assert v.state == "passing", (
        f"a 5/5 probe cannot clear a failure stamped at {last_ts}; deep is stuck: {v.detail[:160]}")


def test_qa3_after_the_clock_steps_back_the_real_probe_neither_clears_deep_nor_tells_the_truth(
        fake_deep, monkeypatch):
    """End to end: the failure is recorded while the wall clock runs one hour ahead (then corrected),
    and the real ``python -m arail.deep_probe`` passes 5 of 5 on the same binary."""
    fd = fake_deep("pass")
    dv.identity()
    real_now = dv._now
    monkeypatch.setattr(dv, "_now", lambda: real_now() + 3600)
    dv.note_call(False, error="RuntimeError: rms_norm has 1 elements", consumer="agent:buddy")
    monkeypatch.setattr(dv, "_now", real_now)                       # the clock is corrected
    r = subprocess.run([sys.executable, "-m", "arail.deep_probe"], env=fd.env(), capture_output=True,
                       text=True, timeout=120)
    assert "result    5/5 passed" in r.stdout, r.stdout + r.stderr
    assert "a deep call failed elsewhere while the probe ran" not in r.stdout, (
        "false: no deep call failed while this probe ran; the stored failure is from before it, "
        "stamped ahead of the corrected clock")
    assert r.returncode == 0 and dv.current().state == "passing", (
        f"exit {r.returncode}; deep stays failing after a 5/5 probe of the same binary")


# ── guards that hold today (QA coverage the sprint did not have) ───────────────────────────────────

def test_qa_guard_a_non_utf8_store_fails_closed_and_the_next_writer_moves_it_aside(fake_deep):
    fake_deep("pass")
    dv.identity()
    _write_store(b'{"schema": "arail.deep-verdict/v1", "binaries": {"\xff\xfe": {}}}')
    v = dv.current()
    assert v.state == "failing" and v.source == "store_unreadable"
    dv.note_call(False, error="boom")
    assert _corrupt_copies() and json.loads(store_path().read_text(encoding="utf-8"))["schema"] == SCHEMA


def test_qa_guard_a_binary_key_that_looks_like_a_path_writes_nothing_outside_the_store(fake_deep, tmp_path):
    fake_deep("pass")
    b = dv.identity()
    doc = _doc_with_failure(b.sha256, None)
    doc["binaries"]["../../../../escaped"] = {"failure": None, "last_probe": None,
                                             "native_path": "../../escaped.so"}
    _write_store(doc)
    dv.note_call(False, error="boom")
    dv.record_probe(probe_result(b, "pass", passed=5, started_ts=dv._now() + 1))
    assert not list(tmp_path.rglob("escaped*")), "a key or a stored path is never used as a file name"


def test_qa_guard_a_symlink_at_the_lock_is_never_followed_and_the_failure_still_shows(fake_deep, tmp_path):
    fake_deep("pass")
    dv.identity()
    target = tmp_path / "victim.txt"
    target.write_text("untouched\n")
    target.chmod(0o644)
    dv.state_dir().mkdir(parents=True)
    (dv.state_dir() / "verdict.lock").symlink_to(target)
    dv.note_call(False, error="boom")
    v = dv.current()
    assert v.state == "failing" and v.store_error, "kept in memory, and the reason it was not saved is shown"
    assert target.read_text() == "untouched\n" and stat.S_IMODE(target.stat().st_mode) == 0o644


def test_qa_guard_a_planted_symlink_at_the_temp_name_is_replaced_not_followed(fake_deep, tmp_path):
    fake_deep("pass")
    dv.identity()
    import threading
    target = tmp_path / "victim.txt"
    target.write_text("untouched\n")
    dv.state_dir().mkdir(parents=True)
    tmp_name = dv.state_dir() / f"verdict.json.tmp-{os.getpid()}-{threading.get_ident()}"
    tmp_name.symlink_to(target)
    dv.note_call(False, error="boom")
    assert target.read_text() == "untouched\n"
    assert json.loads(store_path().read_text(encoding="utf-8"))["schema"] == SCHEMA
    assert not tmp_name.exists() and not tmp_name.is_symlink()


def test_qa_guard_every_write_path_leaves_0700_and_0600_over_looser_modes(fake_deep):
    fake_deep("pass")
    b = dv.identity()
    d = dv.state_dir()
    d.mkdir(parents=True)
    d.chmod(0o755)
    _write_store({"schema": SCHEMA, "updated_at": "x", "binaries": {}}).chmod(0o644)
    (d / "verdict.lock").write_text("")
    (d / "verdict.lock").chmod(0o644)
    writes = [lambda: dv.note_call(False, error="boom"),
              lambda: dv.note_load_failure("could not start", persist=True),
              lambda: dv.record_probe(probe_result(b, "pass", passed=5, started_ts=dv._now() + 1)),
              lambda: dv.record_probe(probe_result(b, "fail", passed=1, started_ts=dv._now(),
                                                   first_error={"call": 2, "kind": "long", "error": "e"}))]
    for write in writes:
        write()
        assert stat.S_IMODE(d.stat().st_mode) == 0o700
        assert stat.S_IMODE(store_path().stat().st_mode) == 0o600
        assert stat.S_IMODE((d / "verdict.lock").stat().st_mode) == 0o600
        for p in (d / "verdict.json", d / "verdict.lock"):
            p.chmod(0o644)
        d.chmod(0o755)


# ── QA-4: a symlink planted at the state folder itself sends every write outside it ────────────────
# The files open with O_NOFOLLOW, but that guards only the last path component. When the folder
# (lab/data/deep, or ARAIL_DEEP_STATE_DIR) is itself a symlink, _locked_update follows it: it chmods
# the TARGET directory to 0700 and writes verdict.lock, verdict.json and temp files into it (and the
# probe writes probe.lock there). Whoever can plant that symlink (write access to lab/data) chooses a
# directory ARAIL then re-permissions and writes into.

def test_qa4_a_symlinked_state_folder_is_not_written_through(fake_deep, tmp_path, monkeypatch):
    fake_deep("pass")
    dv.identity()
    elsewhere = tmp_path / "somebody-elses-dir"
    elsewhere.mkdir()
    elsewhere.chmod(0o755)
    (tmp_path / "lab" / "data").mkdir(parents=True)
    link = tmp_path / "lab" / "data" / "deep"
    link.symlink_to(elsewhere, target_is_directory=True)
    monkeypatch.setenv("ARAIL_DEEP_STATE_DIR", str(link))
    dv._reset_for_tests()
    dv.identity()
    dv.note_call(False, error="RuntimeError: boom", consumer="agent:buddy")
    assert dv.current().state == "failing", "the failure must still show in this process"
    assert sorted(p.name for p in elsewhere.iterdir()) == [], "ARAIL wrote into the symlink's target"
    assert stat.S_IMODE(elsewhere.stat().st_mode) == 0o755, "ARAIL re-permissioned the symlink's target"


# ── QA-5: error text reaches the terminal with its control characters ──────────────────────────────
# _clip collapses whitespace but keeps C0 control characters, so an ESC sequence or a BEL inside a
# runtime error (or in a stored verdict) is printed raw by `deep status` and by `deep probe`, where
# it can rewrite what the terminal shows (CWE-150). The portal escapes HTML (F25); the CLI has no
# equivalent.

ESC_ERROR = "RuntimeError: \x1b[2K\x1b[1Ainjected\x1b[0m\x07 rms_norm"


def test_qa5_deep_status_prints_no_raw_control_characters_from_a_stored_error(fake_deep):
    fake_deep("pass")
    dv.identity()
    dv.note_call(False, error=ESC_ERROR, consumer="agent:buddy")
    text = "\n".join(dv.status_lines())
    assert "FAILING" in text
    bad = sorted({hex(ord(c)) for c in text if ord(c) < 0x20 and c not in "\n\t"})
    assert not bad, f"deep status would write control characters {bad} to the terminal"


def test_qa5_deep_probe_prints_no_raw_control_characters_from_a_runtime_error(fake_deep):
    fd = fake_deep("pass")
    with open(fd.package / "__init__.py", "a", encoding="utf-8") as fh:     # a runtime whose error carries ESC
        fh.write("\n_qa_orig = Runtime.generate\n"
                 "def _qa_generate(self, prompt, **kw):\n"
                 "    if self.calls >= 1 and os.environ.get('ARAIL_QA_RUNTIME_ERROR'):\n"
                 "        self.calls += 1\n"
                 "        raise RuntimeError(os.environ['ARAIL_QA_RUNTIME_ERROR'])\n"
                 "    return _qa_orig(self, prompt, **kw)\n"
                 "Runtime.generate = _qa_generate\n")
    r = subprocess.run([sys.executable, "-m", "arail.deep_probe"], capture_output=True, text=True,
                       timeout=120, env=fd.env(ARAIL_QA_RUNTIME_ERROR=ESC_ERROR.split(": ", 1)[1]))
    assert r.returncode == 1 and "FAILED" in r.stdout, r.stdout + r.stderr
    bad = sorted({hex(ord(c)) for c in r.stdout if ord(c) < 0x20 and c not in "\n\t"})
    assert not bad, f"deep probe wrote control characters {bad} to the terminal"
