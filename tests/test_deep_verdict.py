"""deep_verdict: identity, the per-binary store, and the failure that stays visible.

C1 and C2 of sprints/2026-10-29-arail-deep-honest/ARCHITECTURE.md. Tests U1 to
U13 and SEC1 are named for the architecture's list. The module never generates
and never imports the real runtime, so everything here runs on a fixture.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import types
from pathlib import Path

import pytest

from arail import deep_verdict as dv
from tests.fakes.fake_deep_runtime import fake_deep  # noqa: F401  (fixture)


# ── helpers ────────────────────────────────────────────────────────────────

def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _register(monkeypatch, name: str, file: Path, version: str | None = None) -> None:
    mod = types.ModuleType(name)
    mod.__file__ = str(file)
    if version is not None:
        mod.__version__ = version
    monkeypatch.setitem(sys.modules, name, mod)


def _native_names() -> list[str]:
    return [n for n in sys.modules if n.split(".")[0] in ("aerollm_api", "queuellm_api")]


@pytest.fixture
def clean_modules(monkeypatch):
    """No real or fake runtime in sys.modules, and clean verdict caches."""
    for name in _native_names():
        monkeypatch.delitem(sys.modules, name)
    dv._reset_for_tests()
    yield
    dv._reset_for_tests()


def store_path() -> Path:
    return dv.state_dir() / "verdict.json"


def read_store() -> dict:
    return json.loads(store_path().read_text(encoding="utf-8"))


def probe_result(binary: dv.Binary, outcome: str, *, passed: int, started_ts: float,
                 first_error: dict | None = None, **extra) -> dict:
    return {
        "binary": {"module": binary.module, "native_path": binary.native_path,
                   "sha256": binary.sha256, "bytes": binary.bytes, "version": binary.version},
        "outcome": outcome, "passed": passed, "total": 5,
        "median_ok_s": 9.65 if passed else None,
        "first_error": first_error,
        "started_ts": started_ts, "started_at": dv._iso(started_ts),
        "model": "Qwen2.5-7B-Instruct-4bit", "ring_depth": 2,
        "ring_depth_source": "profile:balanced", "max_tokens": 384,
        "prompt_set_sha256": "b5bcb562" + "0" * 56, "python": sys.executable, "lab": "root",
        **extra,
    }


def run_py(fd, code: str, **env) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], env=fd.env(**env),
                          capture_output=True, text=True, timeout=90)


FAIL_ERR = {"call": 2, "kind": "long",
            "error": "RuntimeError: backend `mlx-native` failed: [rms_norm] has 1 elements"}


# ── U1: identity ───────────────────────────────────────────────────────────

def test_u1_identity_package_layout(tmp_path, monkeypatch, clean_modules):
    site = tmp_path / "site"
    init = _write(site / "aerollm_api" / "__init__.py", b"from .aerollm_api import *\n")
    so = _write(site / "aerollm_api" / "aerollm_api.abi3.so", b"native-A" * 100)
    _register(monkeypatch, "aerollm_api", init, version="0.1.0-rc.2")
    _register(monkeypatch, "aerollm_api.aerollm_api", so)
    b = dv.identity()
    assert b is not None
    assert b.module == "aerollm_api"
    assert b.resolves_to == str(init)
    assert b.native_path == str(so)
    assert b.sha256 == hashlib.sha256(so.read_bytes()).hexdigest()
    assert b.bytes == so.stat().st_size
    assert b.version == "0.1.0-rc.2"
    assert b.pinned is True
    assert b.mtime.endswith("Z")


def test_u1_identity_top_level_extension_layout(tmp_path, monkeypatch, clean_modules):
    so = _write(tmp_path / "site" / "aerollm_api.abi3.so", b"native-B" * 100)
    _register(monkeypatch, "aerollm_api", so, version="1.0.0")
    b = dv.identity()
    assert b is not None
    assert b.resolves_to == b.native_path == str(so)
    assert b.version == "1.0.0"


def test_u1_identity_shim_over_queuellm_layout(tmp_path, monkeypatch, clean_modules):
    init = _write(tmp_path / "shim" / "aerollm_api" / "__init__.py", b"from queuellm_api import *\n")
    so = _write(tmp_path / "ext" / "queuellm_api.abi3.so", b"native-C" * 100)
    _register(monkeypatch, "aerollm_api", init, version="1.1.0")
    _register(monkeypatch, "queuellm_api", so)
    b = dv.identity()
    assert b is not None
    assert b.module == "aerollm_api", "the frozen name is what ARAIL imports, whatever the native is called"
    assert b.native_path == str(so)
    assert b.resolves_to == str(init)


def test_u1_zero_native_modules_is_untested_with_a_reason(tmp_path, monkeypatch, clean_modules):
    init = _write(tmp_path / "site" / "aerollm_api" / "__init__.py", b"")
    _register(monkeypatch, "aerollm_api", init)
    assert dv.identity() is None
    v = dv.current()
    assert v.state == "untested" and v.binary is None
    assert "could not be identified" in v.detail and "no native" in v.detail


def test_u1_two_native_modules_is_untested_with_a_reason(tmp_path, monkeypatch, clean_modules):
    a = _write(tmp_path / "a" / "aerollm_api.abi3.so", b"one" * 50)
    b = _write(tmp_path / "b" / "queuellm_api.abi3.so", b"two" * 50)
    _register(monkeypatch, "aerollm_api", a)
    _register(monkeypatch, "queuellm_api", b)
    assert dv.identity() is None
    v = dv.current()
    assert v.state == "untested"
    assert "2 native" in v.detail


def test_u1_one_file_under_two_names_is_one_binary(tmp_path, monkeypatch, clean_modules):
    so = _write(tmp_path / "site" / "queuellm_api.abi3.so", b"same" * 50)
    _register(monkeypatch, "queuellm_api", so)
    _register(monkeypatch, "aerollm_api.alias", so)
    b = dv.identity()
    assert b is not None and b.native_path == str(so)


def test_u1_static_identity_never_imports(tmp_path, monkeypatch, clean_modules):
    boom = b"raise RuntimeError('static identity must not import this')\n"
    site = tmp_path / "site"
    _write(site / "aerollm_api" / "__init__.py", boom)
    so = _write(site / "aerollm_api" / "aerollm_api.abi3.so", b"static-A" * 100)
    monkeypatch.syspath_prepend(str(site))
    b = dv.identity(import_if_needed=False)
    assert b is not None
    assert b.native_path == str(so)
    assert b.pinned is False, "a static answer is not pinned; nothing is loaded"
    assert "aerollm_api" not in sys.modules


def test_u1_static_identity_top_level_none_and_ambiguous(tmp_path, monkeypatch, clean_modules):
    # top-level extension
    top = tmp_path / "top"
    so = _write(top / "aerollm_api.abi3.so", b"top" * 80)
    monkeypatch.syspath_prepend(str(top))
    b = dv.identity(import_if_needed=False)
    assert b is not None and b.native_path == str(so) and b.resolves_to == str(so)
    # a package with no extension, then with two
    monkeypatch.syspath_prepend(str(tmp_path / "pkg-none"))
    _write(tmp_path / "pkg-none" / "aerollm_api" / "__init__.py", b"")
    dv._reset_for_tests()
    assert dv.identity(import_if_needed=False) is None
    monkeypatch.syspath_prepend(str(tmp_path / "pkg-two"))
    _write(tmp_path / "pkg-two" / "aerollm_api" / "__init__.py", b"")
    _write(tmp_path / "pkg-two" / "aerollm_api" / "one.abi3.so", b"1")
    _write(tmp_path / "pkg-two" / "aerollm_api" / "two.abi3.so", b"2")
    dv._reset_for_tests()
    assert dv.identity(import_if_needed=False) is None


def test_u1_static_identity_with_no_module_at_all(clean_modules):
    assert dv.identity(import_if_needed=False) is None
    v = dv.current(import_if_needed=False)
    assert v.state == "untested" and v.binary is None


# ── U2: pinning ────────────────────────────────────────────────────────────

def test_u2_binary_changed_on_disk_keeps_the_old_key(fake_deep):
    fd = fake_deep()
    b0 = dv.identity()
    assert b0 is not None and b0.sha256 == fd.sha256
    assert dv.current().binary_changed_on_disk is False
    dv.note_call(False, error="boom", consumer="agent:buddy")
    old = fd.sha256

    fd.rewrite_native(b"A DIFFERENT BUILD ENTIRELY\n" * 40)
    v = dv.current()
    assert v.binary_changed_on_disk is True
    assert "restart the lab to load the new build" in v.detail
    assert dv.identity().sha256 == old, "the process keeps the key of the binary it loaded"
    assert v.state == "failing", "the old binary's state is still the state of this process"

    # A probe of the new file never clears the old binary's state here.
    new = dv.Binary(module="aerollm_api", resolves_to=b0.resolves_to, native_path=b0.native_path,
                    sha256=fd.sha256, bytes=fd.native_path.stat().st_size, mtime=b0.mtime,
                    version="x", pinned=False)
    ok, err = dv.record_probe(probe_result(new, "pass", passed=5, started_ts=dv._now() + 1))
    assert ok, err
    assert dv.current().state == "failing"


# ── U3: missing store ──────────────────────────────────────────────────────

def test_u3_missing_store_is_untested_and_the_first_failure_creates_it(fake_deep):
    fake_deep()
    assert not store_path().exists()
    v = dv.current()
    assert v.state == "untested" and v.failure is None and v.last_probe is None
    assert dv.is_failing() is False
    assert "has not been tested" in v.detail and "./arailctl deep probe" in v.detail
    dv.note_call(False, error="boom")
    doc = read_store()
    assert doc["schema"] == "arail.deep-verdict/v1"
    assert doc["binaries"][dv.identity().sha256]["failure"]["count"] == 1


# ── U4: corrupt or wrong-schema store ──────────────────────────────────────

@pytest.mark.parametrize("content", [b"{not json", b"[]", b'{"schema": "other/v9", "binaries": {}}',
                                     b'{"schema": "arail.deep-verdict/v1", "binaries": []}'])
def test_u4_corrupt_store_fails_closed_then_is_moved_aside(fake_deep, content):
    fake_deep()
    store_path().parent.mkdir(parents=True, exist_ok=True)
    store_path().write_bytes(content)
    v = dv.current()
    assert v.state == "failing" and v.source == "store_unreadable"
    assert str(store_path()) in v.detail and "unreadable" in v.detail
    assert v.store_error

    dv.note_call(False, error="boom")           # the next writer
    aside = list(store_path().parent.glob("verdict.json.corrupt-*"))
    assert len(aside) == 1 and aside[0].read_bytes() == content
    assert read_store()["schema"] == "arail.deep-verdict/v1"
    assert dv.current().source == "call"


# ── U5: a failure persists, other processes see it, a later success does not clear ──

def test_u5_a_failure_persists_and_another_process_sees_it(fake_deep):
    fd = fake_deep()
    dv.identity()
    dv.note_call(False, error="boom 1", consumer="agent:buddy")
    r = run_py(fd, "import aerollm_api\nfrom arail import deep_verdict as dv\n"
                   "v = dv.current()\nprint(v.state, v.failure['consumer'], v.failure['count'])\n")
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["failing", "agent:buddy", "1"]


def test_u5_a_later_ok_call_does_not_clear(fake_deep):
    fake_deep()
    dv.identity()
    dv.note_call(False, error="boom")
    for _ in range(3):
        dv.note_call(True)
    assert dv.current().state == "failing"
    assert dv.counters() == {"ok": 3, "failed": 1}


# ── U6: a pass clears only what was last seen before the probe started ─────

def test_u6_a_pass_clears_only_failures_before_the_probe_started(fake_deep, monkeypatch):
    fake_deep()
    b = dv.identity()
    clock = {"now": 1000.0}
    monkeypatch.setattr(dv, "_now", lambda: clock["now"])
    dv.note_call(False, error="e1", consumer="agent:buddy")
    ok, err = dv.record_probe(probe_result(b, "pass", passed=5, started_ts=1010.0))
    assert ok, err
    v = dv.current()
    assert v.state == "passing" and v.failure is None
    assert read_store()["binaries"][b.sha256]["last_pass_at"]

    clock["now"] = 1020.0
    dv.note_call(False, error="e2", consumer="agent:debt-advisor")
    ok, err = dv.record_probe(probe_result(b, "pass", passed=5, started_ts=1015.0))
    assert ok, err
    v = dv.current()
    assert v.state == "failing", "a failure that landed while the probe ran survives the pass"
    assert v.failure["last_ts"] == 1020.0
    assert v.last_probe["outcome"] == "pass"


# ── U7: a failed probe records the first error and its index ───────────────

def test_u7_a_failed_probe_records_the_first_error_and_its_index(fake_deep):
    fake_deep()
    b = dv.identity()
    long_err = dict(FAIL_ERR, error="RuntimeError: " + "x" * 600)
    ok, err = dv.record_probe(probe_result(b, "fail", passed=1, started_ts=dv._now(), first_error=long_err))
    assert ok, err
    v = dv.current()
    assert v.state == "failing" and v.source == "probe"
    assert v.failure["consumer"] == "deep-probe"
    assert v.last_probe["passed"] == 1 and v.last_probe["total"] == 5
    assert v.last_probe["first_error"]["call"] == 2 and v.last_probe["first_error"]["kind"] == "long"
    assert len(v.failure["error"]) <= 300
    assert "1/5" in v.detail and "call 2" in v.detail


# ── U8: an unwritable store still reports failing, in this process ─────────

def test_u8_unwritable_store_is_failing_in_process_with_store_error(fake_deep, tmp_path, monkeypatch):
    fake_deep()
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    monkeypatch.setenv("ARAIL_DEEP_STATE_DIR", str(blocker / "deep"))
    dv._reset_for_tests()
    b = dv.identity()
    dv.note_call(False, error="boom")
    v = dv.current()
    assert v.state == "failing" and v.store_error
    ok, err = dv.record_probe(probe_result(b, "pass", passed=5, started_ts=dv._now()))
    assert ok is False and err


# ── U9: concurrent writers keep an exact count ─────────────────────────────

def test_u9_eight_processes_fifty_writes_each_keep_an_exact_count(fake_deep):
    fd = fake_deep()
    code = ("import aerollm_api\nfrom arail import deep_verdict as dv\n"
            "for _ in range(50):\n    dv.note_call(False, error='p', consumer='agent:t')\n")
    procs = [subprocess.Popen([sys.executable, "-c", code], env=fd.env(),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for _ in range(8)]
    for p in procs:
        out, err = p.communicate(timeout=180)
        assert p.returncode == 0, err
    doc = read_store()
    entry = doc["binaries"][fd.sha256]
    assert entry["failure"]["count"] == 400
    assert doc["schema"] == "arail.deep-verdict/v1"


# ── U10: binaries are independent ──────────────────────────────────────────

def test_u10_a_new_binary_starts_untested_not_passing(fake_deep):
    fd = fake_deep()
    a = dv.identity()
    dv.note_call(False, error="boom")
    fd.rewrite_native(b"FAKE-NATIVE-BINARY-B\n" * 64)
    r = run_py(fd, "import aerollm_api\nfrom arail import deep_verdict as dv\n"
                   "v = dv.current()\nprint(v.state, v.binary.sha256)\n")
    assert r.returncode == 0, r.stderr
    state, sha = r.stdout.split()
    assert state == "untested" and sha != a.sha256
    doc = read_store()
    assert a.sha256 in doc["binaries"] and sha not in doc["binaries"]


# ── U11: where the store lives ─────────────────────────────────────────────

def test_u11_state_dir_rules(tmp_path, monkeypatch):
    env_dir = tmp_path / "envdir"
    monkeypatch.setenv("ARAIL_DEEP_STATE_DIR", str(env_dir))
    assert dv.state_dir() == env_dir

    monkeypatch.delenv("ARAIL_DEEP_STATE_DIR")
    checkout = tmp_path / "co"
    checkout.mkdir()
    (checkout / "arailctl").write_text("#!/bin/sh\n")
    monkeypatch.setattr(dv, "_checkout_root", lambda: checkout)
    assert dv.state_dir() == checkout / "lab" / "data" / "deep"

    from arail import config
    monkeypatch.setattr(dv, "_checkout_root", lambda: None)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "cfgdata")
    assert dv.state_dir() == tmp_path / "cfgdata" / "deep"


def test_u11_the_real_checkout_root_holds_arailctl_and_is_not_a_venv():
    root = dv._checkout_root()
    assert root is not None and (root / "arailctl").is_file()
    assert root == Path(dv.__file__).resolve().parents[2]
    assert "site-packages" not in root.parts


def test_u11_a_package_outside_a_checkout_has_no_checkout_root(tmp_path, monkeypatch):
    fake_file = tmp_path / "venv" / "lib" / "python3.11" / "site-packages" / "arail" / "deep_verdict.py"
    fake_file.parent.mkdir(parents=True)
    fake_file.write_text("")
    monkeypatch.setattr(dv, "__file__", str(fake_file))
    assert dv._checkout_root() is None


# ── U12: a model-dir failure is this process only ──────────────────────────

def test_u12_a_model_dir_failure_is_not_persisted(fake_deep):
    fd = fake_deep()
    dv.identity()
    dv.note_load_failure(RuntimeError("QueueLLM model dir not found: /nowhere/m"), persist=False,
                         consumer="agent:buddy")
    v = dv.current()
    assert v.state == "failing" and v.source == "load"
    assert "/nowhere/m" in v.detail
    assert not store_path().exists()
    r = run_py(fd, "import aerollm_api\nfrom arail import deep_verdict as dv\nprint(dv.current().state)\n")
    assert r.stdout.split() == ["untested"]


def test_u12_a_runtime_load_failure_is_persisted(fake_deep):
    fake_deep()
    b = dv.identity()
    dv.note_load_failure(RuntimeError("could not start"), persist=True, consumer="system:world-forge")
    v = dv.current()
    assert v.state == "failing" and v.source == "load"
    assert read_store()["binaries"][b.sha256]["failure"]["consumer"] == "system:world-forge"


# ── U13: tests never write the operator's verdict ──────────────────────────

def _sig(path: Path):
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return (st.st_mtime_ns, st.st_size)


def test_u13_the_suite_never_writes_the_checkout_verdict(fake_deep):
    fake_deep()
    real = Path(dv.__file__).resolve().parents[2] / "lab" / "data" / "deep" / "verdict.json"
    before = _sig(real)
    assert dv.state_dir() != real.parent, "the autouse fixture must point the store at tmp"
    dv.identity()
    dv.note_call(False, error="boom")
    assert store_path().exists()
    assert _sig(real) == before


# ── SEC1: modes and symlinks ───────────────────────────────────────────────

def test_sec1_modes_are_private(fake_deep):
    fake_deep()
    dv.identity()
    dv.note_call(False, error="boom")
    d = dv.state_dir()
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    assert stat.S_IMODE((d / "verdict.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((d / "verdict.lock").stat().st_mode) == 0o600


def test_sec1_a_symlink_at_verdict_json_is_replaced_not_followed(fake_deep, tmp_path):
    fake_deep()
    dv.identity()
    target = tmp_path / "elsewhere.txt"
    target.write_text("SENTINEL")
    d = dv.state_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "verdict.json").symlink_to(target)
    v = dv.current()
    assert v.state == "failing" and v.source == "store_unreadable", "reading never follows the link"
    dv.note_call(False, error="boom")
    assert target.read_text() == "SENTINEL"
    assert not (d / "verdict.json").is_symlink()
    assert read_store()["schema"] == "arail.deep-verdict/v1"


def test_sec1_a_symlink_at_the_lock_is_not_followed(fake_deep, tmp_path):
    fake_deep()
    dv.identity()
    target = tmp_path / "lock-target.txt"
    target.write_text("SENTINEL")
    d = dv.state_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "verdict.lock").symlink_to(target)
    dv.note_call(False, error="boom")
    assert target.read_text() == "SENTINEL"
    v = dv.current()
    assert v.state == "failing" and v.store_error


# ── never raises, transition line, probe child, eviction, details ──────────

def test_calls_never_raise_to_a_caller(fake_deep, monkeypatch):
    fake_deep()
    dv.identity()
    monkeypatch.setenv("ARAIL_DEEP_STATE_DIR", "/dev/null/impossible")
    dv.note_call(False, error=object(), consumer=object())
    dv.note_call(True)
    dv.note_load_failure(None, persist=True)
    ok, err = dv.record_probe({})
    assert ok is False and err
    assert dv.record_probe(None)[0] is False
    assert dv.current().state == "failing"
    assert dv.is_failing() is True


def test_a_transition_emits_one_warn_line(fake_deep):
    from arail.activity import activity_log
    fake_deep()
    b = dv.identity()
    dv.note_call(False, error="RuntimeError: boom", consumer="agent:buddy")
    lines = [e for e in activity_log.recent(50) if e["message"].startswith("Deep model failed in")]
    assert len(lines) == 1 and lines[0]["level"] == "warn"
    msg = lines[0]["message"]
    assert "agent:buddy (root)" in msg and "RuntimeError: boom" in msg
    assert f"sha256 {b.sha256[:12]}" in msg
    assert "./arailctl deep probe passes 5 of 5" in msg and "fast model" in msg
    dv.note_call(False, error="RuntimeError: boom again", consumer="agent:buddy")
    again = [e for e in activity_log.recent(50) if e["message"].startswith("Deep model failed in")]
    assert len(again) == 1, "only the transition speaks"


def test_a_lab_instance_is_named_in_the_record(fake_deep, monkeypatch):
    fake_deep()
    dv.identity()
    monkeypatch.setenv("ARAIL_INSTANCE", "ai")
    dv.note_call(False, error="boom", consumer="agent:buddy")
    assert dv.current().failure["lab"] == "ai"
    assert "failed in ai" in dv.current().detail or "in ai (" in dv.current().detail


def test_the_probe_child_keeps_failures_in_memory_and_stays_silent(fake_deep, monkeypatch):
    from arail.activity import activity_log
    fake_deep()
    dv.identity()
    monkeypatch.setenv("ARAIL_DEEP_PROBE_CHILD", "1")
    dv.note_call(False, error="boom")
    dv.note_load_failure(RuntimeError("x"), persist=True)
    assert not store_path().exists(), "the supervisor is the only writer"
    assert dv.current().state == "failing"
    assert not [e for e in activity_log.recent(50) if "Deep model failed" in e["message"]]


def test_the_store_keeps_at_most_32_binaries_and_drops_the_oldest(fake_deep):
    fake_deep()
    real = dv.identity()
    for i in range(40):
        fake = dv.Binary(module="aerollm_api", resolves_to="x", native_path=f"/x/{i}", sha256=f"{i:064x}",
                         bytes=1, mtime="t", version="v", pinned=False)
        ok, err = dv.record_probe(probe_result(fake, "pass", passed=5, started_ts=1000.0 + i,
                                               at=dv._iso(1000.0 + i)))
        assert ok, err
    ok, err = dv.record_probe(probe_result(real, "fail", passed=1, started_ts=5000.0,
                                           first_error=FAIL_ERR, at=dv._iso(5000.0)))
    assert ok, err
    bins = read_store()["binaries"]
    assert len(bins) <= 32
    assert real.sha256 in bins, "the entry being written is never the one evicted"
    assert f"{0:064x}" not in bins and f"{39:064x}" in bins


def test_details_say_what_is_known_and_what_to_run(fake_deep):
    fake_deep()
    b = dv.identity()
    untested = dv.current().detail
    assert "an import is not a generation test" in untested

    dv.record_probe(probe_result(b, "fail", passed=1, started_ts=dv._now(), first_error=FAIL_ERR))
    failing = dv.current().detail
    assert failing.startswith("deep generation is failing on this build (QueueLLM 0.0.0-fake, sha256 ")
    assert b.sha256[:12] in failing
    assert "last probe 1/5, first error at call 2" in failing
    assert "Agents and the Curator use the fast model." in failing
    assert "Re-test with ./arailctl deep probe after installing a new build." in failing

    hd = dv.health_detail(dv.current())
    assert hd.startswith(dv.HEALTH_PREFIX)
    assert f"sha256 {b.sha256[:12]}" in hd
    assert hd.endswith("Check re-reads this; only ./arailctl deep probe can clear it.")

    dv.record_probe(probe_result(b, "pass", passed=5, started_ts=dv._now() + 1))
    v = dv.current()
    assert v.state == "passing" and "5 of 5" in v.detail and "median 9.7 s" in v.detail
    assert dv.health_detail(v) == ""


def test_the_hash_is_cached_and_cheap(fake_deep):
    import time
    fake_deep()
    dv.identity()
    t = time.perf_counter()
    for _ in range(2000):
        dv.is_failing()
    assert time.perf_counter() - t < 1.0


def test_teaching_has_four_parts_for_every_entry():
    assert dv.TEACHING, "the status numbers need teaching"
    for key, entry in dv.TEACHING.items():
        assert set(entry) == {"meaning", "threshold", "out_of_range", "failure_lives_in"}, key
        assert all(isinstance(v, str) and v.strip() for v in entry.values()), key
