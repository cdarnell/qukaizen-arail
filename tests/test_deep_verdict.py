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


# ── QA-1 (fix pass 3): a writer that fails for any reason reports a problem; it never raises ────────

def test_qa1_a_writer_that_breaks_for_any_reason_returns_a_problem_and_releases_the_lock(fake_deep):
    """A RecursionError from the parser was one case of "the store cannot be read or written". Whatever
    else goes wrong inside the update, the caller gets a one-line problem (it keeps the failure in
    memory and says why), the lock is released, and the next writer works."""
    fake_deep()
    dv.identity()

    def boom(doc):
        raise ZeroDivisionError("the update broke")

    problem = dv._locked_update(boom)
    assert problem and "ZeroDivisionError" in problem and "the update broke" in problem
    assert dv._locked_update(lambda doc: None) is None, "the lock was released and the store still works"
    assert not list(dv.state_dir().glob("verdict.json.tmp-*")), "no temp file left behind"


def test_qa1_a_failure_is_kept_in_memory_and_says_why_when_the_store_cannot_be_written_back(fake_deep, monkeypatch):
    fake_deep()
    dv.identity()

    def too_deep(path, doc, dir_fd=None):
        raise RecursionError("maximum recursion depth exceeded while encoding a JSON object")

    monkeypatch.setattr(dv, "_write_atomic", too_deep)
    dv.note_call(False, error="RuntimeError: rms_norm has 1 elements", consumer="agent:buddy")
    v = dv.current()
    assert v.state == "failing", "the failure is not lost"
    assert v.store_error and "RecursionError" in v.store_error


# ── QA-2 (fix pass 3): the store is validated for what the readers rely on, and for nothing more ────

def _entry_doc(sha: str, **entry) -> dict:
    base = {"module": "aerollm_api", "native_path": "/x", "bytes": 1, "version": "v", "failure": None,
            "last_probe": None, "last_pass_at": None}
    base.update(entry)
    return {"schema": "arail.deep-verdict/v1", "updated_at": "2026-10-31T00:00:00Z", "binaries": {sha: base}}


def _good_failure(**over) -> dict:
    f = {"since": "2026-10-31T00:00:00Z", "since_ts": 1.0, "last_at": "2026-10-31T00:00:00Z", "last_ts": 2.0,
         "source": "call", "consumer": "agent:buddy", "lab": "root", "error": "e", "last_error": "e", "count": 1}
    f.update(over)
    return f


BAD_ENTRIES = {
    "last_ts is text": {"failure": _good_failure(last_ts="soon")},
    "last_ts is NaN": {"failure": _good_failure(last_ts=float("nan"))},
    "since_ts is infinite": {"failure": _good_failure(since_ts=float("inf"))},
    "count is a fraction": {"failure": _good_failure(count=2.5)},
    "count is a boolean": {"failure": _good_failure(count=True)},
    "last_at is a number": {"failure": _good_failure(last_at=7)},
    "last_probe is a string": {"last_probe": "passed"},
    "last_probe is a list": {"last_probe": [1, 2]},
    "last_probe.started_ts is text": {"last_probe": {"outcome": "pass", "started_ts": "now"}},
    "last_probe.first_error is text": {"last_probe": {"outcome": "fail", "first_error": "boom"}},
    "last_probe.passed is NaN": {"last_probe": {"outcome": "pass", "passed": float("nan"), "total": 5}},
    "last_probe.at is a number": {"last_probe": {"outcome": "pass", "at": 5}},
    "last_pass_at is a number": {"last_pass_at": 12345},
}


@pytest.mark.parametrize("entry", list(BAD_ENTRIES.values()), ids=list(BAD_ENTRIES))
def test_qa2_an_entry_the_readers_cannot_use_fails_closed_and_the_next_writer_moves_the_file_aside(fake_deep, entry):
    fake_deep()
    b = dv.identity()
    store_path().parent.mkdir(parents=True, exist_ok=True)
    store_path().write_text(json.dumps(_entry_doc(b.sha256, **entry)), encoding="utf-8")   # NaN is written as NaN
    v = dv.current()
    assert v.state == "failing" and v.source == "store_unreadable", v.detail
    assert f"binaries[{b.sha256[:12]}]" in v.store_error
    assert "./arailctl deep probe" in v.detail
    dv.note_call(False, error="RuntimeError: boom", consumer="agent:buddy")
    assert len(list(store_path().parent.glob("verdict.json.corrupt-*"))) == 1
    assert read_store()["binaries"][b.sha256]["failure"]["count"] == 1


def test_qa2_unknown_extra_keys_are_valid_and_are_kept_when_the_store_is_written(fake_deep):
    """A later version may add keys. This one reads what it relies on and carries the rest through."""
    fake_deep()
    b = dv.identity()
    doc = _entry_doc(b.sha256, failure=_good_failure(future_failure_key={"a": [1, 2]}), future_entry_key="kept")
    doc["future_doc_key"] = {"nested": True}
    assert dv._validate_doc(doc) is None
    store_path().parent.mkdir(parents=True, exist_ok=True)
    store_path().write_text(json.dumps(doc), encoding="utf-8")
    assert dv.current().state == "failing" and dv.current().source == "call"
    dv.note_call(False, error="RuntimeError: another", consumer="agent:buddy")
    after = read_store()
    entry = after["binaries"][b.sha256]
    assert after["future_doc_key"] == {"nested": True} and entry["future_entry_key"] == "kept"
    assert entry["failure"]["future_failure_key"] == {"a": [1, 2]} and entry["failure"]["count"] == 2
    assert not list(store_path().parent.glob("verdict.json.corrupt-*")), "a valid file is not moved aside"


def test_qa2_an_empty_failure_object_is_a_failure_that_says_it_has_no_error_text(fake_deep):
    """C2: failure not null means failing. The text must not trail off after a colon."""
    fake_deep()
    b = dv.identity()
    store_path().parent.mkdir(parents=True, exist_ok=True)
    store_path().write_text(json.dumps(_entry_doc(b.sha256, failure={})), encoding="utf-8")
    v = dv.current()
    assert v.state == "failing" and "no error text was recorded" in v.detail
    written, err = dv.record_probe(probe_result(b, "pass", passed=5, started_ts=dv._now() + 1))
    assert written, err
    assert dv.current().state == "passing", "a passing probe clears an empty failure like any other"


# ── QA-3 (fix pass 3): only a failure that could have landed during a probe survives its pass ───────

def test_qa3_the_window_a_failure_must_fall_in_to_survive_a_passing_probe():
    """Between the probe's start and now (plus a few seconds of clock slack): it landed while the probe ran.
    Before the start: the pass clears it (as before). After now, or not a finite time: it cannot have landed
    during this probe, so the pass clears it too; that is the clock stepped back, or a hand edit."""
    started, now = 1_000.0, 1_100.0
    landed = dv._landed_during_probe
    for ts, why in ((1_050.0, "inside the run"), (started, "at the start"), (now, "at the end"),
                    (now + dv.CLOCK_SLACK_S - 1, "inside the clock slack")):
        assert landed({"last_ts": ts}, started, now) is True, why
    for ts, why in ((999.0, "before the probe"), (now + 3600.0, "an hour in the future"),
                    (now + dv.CLOCK_SLACK_S + 1, "just past the slack"), (float("nan"), "NaN"),
                    (float("inf"), "infinity"), (float("-inf"), "minus infinity"), ("soon", "text"),
                    ([1], "a list"), (None, "missing")):
        assert landed({"last_ts": ts}, started, now) is False, why


@pytest.mark.parametrize("stamp, survives", [(4_970.0, False), (4_980.0, True), (4_990.0, True), (5_000.0, True),
                                             (5_002.0, True), (5_000.0 + 3_600.0, False)],
                         ids=["before the probe", "at its start", "inside its run", "at its end",
                              "2 s ahead (slack)", "an hour ahead"])
def test_qa3_a_passing_probe_clears_a_failure_stamped_outside_its_window_and_keeps_one_inside_it(
        fake_deep, monkeypatch, stamp, survives):
    fake_deep()
    b = dv.identity()
    store_path().parent.mkdir(parents=True, exist_ok=True)
    store_path().write_text(json.dumps(_entry_doc(b.sha256, failure=_good_failure(last_ts=stamp, since_ts=stamp))),
                            encoding="utf-8")
    monkeypatch.setattr(dv, "_now", lambda: 5_000.0)                  # the probe finishes at 5000, began at 4980
    written, err = dv.record_probe(probe_result(b, "pass", passed=5, started_ts=4_980.0))
    assert written, err
    assert (dv.current().state == "failing") is survives, dv.current().detail


@pytest.mark.parametrize("stamp", ["NaN", "Infinity", "-Infinity"])
def test_qa3_record_probe_clears_a_non_finite_stamp_on_its_own_even_if_the_validator_lets_it_through(
        fake_deep, monkeypatch, stamp):
    """The validator (QA-2) makes such a file unreadable, and the probe's move-aside then replaces it. This
    is the rule in record_probe itself, kept as the second line so the fix does not depend on the first."""
    fake_deep()
    b = dv.identity()
    monkeypatch.setattr(dv, "_validate_doc", lambda doc: None)
    raw = json.dumps(_entry_doc(b.sha256, failure=_good_failure(last_ts=0.0))).replace('"last_ts": 0.0', f'"last_ts": {stamp}')
    store_path().parent.mkdir(parents=True, exist_ok=True)
    store_path().write_text(raw, encoding="utf-8")
    assert dv.current().state == "failing"
    written, err = dv.record_probe(probe_result(b, "pass", passed=5, started_ts=dv._now()))
    assert written, err
    assert dv.current().state == "passing"
    assert not list(store_path().parent.glob("verdict.json.corrupt-*")), "cleared in place, not moved aside"


# ── QA-4 (fix pass 3): the state folder is checked before every write ───────────────────────────────

def _state_at(monkeypatch, folder: Path) -> None:
    monkeypatch.setenv("ARAIL_DEEP_STATE_DIR", str(folder))
    dv._reset_for_tests()
    dv.identity()


def test_qa4_a_link_to_a_folder_that_does_not_exist_makes_nothing_at_its_target(fake_deep, tmp_path, monkeypatch):
    fake_deep()
    link = tmp_path / "deep"
    link.symlink_to(tmp_path / "nowhere", target_is_directory=True)
    _state_at(monkeypatch, link)
    dv.note_call(False, error="RuntimeError: boom", consumer="agent:buddy")
    v = dv.current()
    assert v.state == "failing", "the failure is kept in this process"
    assert "is a symbolic link" in v.store_error and "ARAIL_DEEP_STATE_DIR" in v.store_error
    assert not (tmp_path / "nowhere").exists(), "makedirs did not follow the link and create its target"


def test_qa4_a_folder_that_another_user_owns_is_refused_and_left_exactly_as_it_was(fake_deep, tmp_path, monkeypatch):
    """Not reachable by a QA test without a second account: the owner check is driven with a different uid."""
    fake_deep()
    shared = tmp_path / "shared-checkout-deep"
    shared.mkdir()
    shared.chmod(0o775)
    _state_at(monkeypatch, shared)
    monkeypatch.setattr(dv, "_my_uid", lambda: os.getuid() + 1)       # the folder is not ours
    dv.note_call(False, error="RuntimeError: boom", consumer="agent:buddy")
    v = dv.current()
    assert v.state == "failing" and "owned by another user" in v.store_error
    assert f"uid {os.getuid()}" in v.store_error and "ARAIL_DEEP_STATE_DIR" in v.store_error
    assert list(shared.iterdir()) == [], "nothing was written there"
    assert stat.S_IMODE(shared.stat().st_mode) == 0o775, "and it was not re-permissioned"


def test_qa4_a_symlink_above_the_state_folder_keeps_working(fake_deep, tmp_path, monkeypatch):
    """The lab kept on another disk: tmp/lab is a link, the folder under it is a real one."""
    fake_deep()
    disk2 = tmp_path / "disk2" / "lab"
    disk2.mkdir(parents=True)
    (tmp_path / "lab").symlink_to(disk2, target_is_directory=True)
    _state_at(monkeypatch, tmp_path / "lab" / "data" / "deep")
    dv.note_call(False, error="RuntimeError: boom", consumer="agent:buddy")
    v = dv.current()
    assert v.state == "failing" and v.store_error is None, v.store_error
    real = disk2 / "data" / "deep"
    assert json.loads((real / "verdict.json").read_text())["binaries"]
    assert stat.S_IMODE(real.stat().st_mode) == 0o700 and stat.S_IMODE((real / "verdict.json").stat().st_mode) == 0o600


def test_qa4_a_link_planted_after_the_check_cannot_redirect_the_write(fake_deep, tmp_path, monkeypatch):
    """The folder is checked, then opened; the lock, the temp file, the replace and the move-aside all go
    through that descriptor. Swap the folder for a link to somewhere else after the open: the write still
    lands in the folder that was checked."""
    fake_deep()
    d = tmp_path / "deep"
    _state_at(monkeypatch, d)
    fd = dv._open_state_dir(d)
    try:
        moved, other = tmp_path / "moved-away", tmp_path / "attackers-folder"
        d.rename(moved)
        other.mkdir()
        d.symlink_to(other, target_is_directory=True)
        dv._write_atomic(d / "verdict.json", {"schema": dv.SCHEMA, "updated_at": "x", "binaries": {}}, fd)
        (moved / "verdict.json.bad").write_text("x")
        (moved / "verdict.json").write_text("{not json")
        dv._move_aside(d / "verdict.json", fd)
    finally:
        os.close(fd)
    assert list(other.iterdir()) == [], "nothing reached the folder that was planted"
    assert sorted(p.name for p in moved.iterdir() if p.name.startswith("verdict.json.corrupt-")), "moved aside inside the checked folder"


def test_qa4_a_link_that_appears_between_the_check_and_the_open_is_refused_by_the_open(fake_deep, tmp_path, monkeypatch):
    """Two layers: lstat first, then an open that refuses a link by itself. Make lstat lie (it reports a real
    folder) and the open must still refuse."""
    fake_deep()
    elsewhere = tmp_path / "somebody-elses-dir"
    elsewhere.mkdir()
    link = tmp_path / "deep"
    link.symlink_to(elsewhere, target_is_directory=True)
    real_lstat = os.lstat
    monkeypatch.setattr(os, "lstat", lambda p, *a, **k: real_lstat(elsewhere) if str(p) == str(link) else real_lstat(p, *a, **k))
    with pytest.raises(dv.StateDirRefused) as ei:
        dv._open_state_dir(link)
    assert "is a symbolic link" in ei.value.strerror
    assert list(elsewhere.iterdir()) == []


def test_qa4_a_folder_swapped_for_a_link_in_the_middle_of_an_update_still_gets_the_whole_write(
        fake_deep, tmp_path, monkeypatch):
    """The swap happens right after the folder was checked and opened, whatever the code does next: the
    re-permissioning, the lock file, the temp file and the replace all follow the descriptor, so all of them
    land in the folder that was checked and none of them touches the folder that was planted."""
    fake_deep()
    d, moved, other = tmp_path / "deep", tmp_path / "moved-away", tmp_path / "attackers-folder"
    other.mkdir()
    other.chmod(0o755)
    _state_at(monkeypatch, d)
    real_open, swapped = dv._open_state_dir, []

    def swap_right_after_the_open(folder):
        fd = real_open(folder)                # the folder passed the check and is open
        if not swapped:
            swapped.append(1)
            d.rename(moved)
            d.symlink_to(other, target_is_directory=True)
        return fd

    monkeypatch.setattr(dv, "_open_state_dir", swap_right_after_the_open)
    assert dv._locked_update(lambda doc: doc["binaries"].setdefault("x", {})) is None
    assert swapped, "the folder was swapped for a link after it was opened"
    assert list(other.iterdir()) == [], "nothing reached the folder that was planted"
    assert stat.S_IMODE(other.stat().st_mode) == 0o755, "and the folder that was planted was not re-permissioned"
    assert {"verdict.lock", "verdict.json"} <= {p.name for p in moved.iterdir()}
    assert stat.S_IMODE(moved.stat().st_mode) == 0o700


# ── QA-4 follow-up (fix pass 3): macOS fails a lost create race with ENOENT, and the store retries it ──
# Found by test_u9 failing one run in about twenty after QA-4 moved the lock file onto the folder's descriptor.
# When several processes create the same file in one folder at the same instant, openat(O_CREAT) on macOS lets
# one win and fails the others with ENOENT although the folder is there (measured: with eight processes, seven
# failed in nearly every round, always fixed by the next attempt; the same call by path never failed). The write
# that met it was reported as "No such file or directory" and kept in memory, so a count came up one short.

def _enoent_on(monkeypatch, name: str, times: int) -> list:
    """Make os.open fail with ENOENT the first ``times`` times it is asked to open ``name`` in a folder."""
    real, seen = os.open, []

    def flaky(path, flags, mode=0o777, *, dir_fd=None):
        if path == name and dir_fd is not None:
            seen.append(1)
            if len(seen) <= times:
                raise FileNotFoundError(2, "No such file or directory", name)
        return real(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", flaky)
    return seen


def test_qa4_a_lost_create_race_on_the_lock_file_is_retried_not_reported_as_a_failed_write(fake_deep, monkeypatch):
    fake_deep()
    b = dv.identity()
    seen = _enoent_on(monkeypatch, "verdict.lock", times=2)
    dv.note_call(False, error="RuntimeError: boom", consumer="agent:buddy")
    assert len(seen) == 3, "two ENOENT answers, then the open that worked"
    v = dv.current()
    assert v.store_error is None, v.store_error
    assert read_store()["binaries"][b.sha256]["failure"]["count"] == 1, "the write landed"


def test_qa4_a_folder_that_is_really_gone_fails_after_a_few_tries_and_says_so(fake_deep, monkeypatch):
    import time
    fake_deep()
    dv.identity()
    seen = _enoent_on(monkeypatch, "verdict.lock", times=10 ** 6)
    t0 = time.monotonic()
    problem = dv._locked_update(lambda doc: None)
    assert problem and "No such file or directory" in problem
    assert len(seen) == dv.OPEN_ATTEMPTS, "a bounded number of tries"
    assert time.monotonic() - t0 < 2.0, "and a short wait"


def test_qa4_only_a_missing_file_is_retried(fake_deep, monkeypatch):
    fake_deep()
    d = dv.state_dir()
    d.mkdir(parents=True, mode=0o700)
    dir_fd = dv._open_state_dir(d)
    calls = []

    def denied(path, flags, mode=0o777, *, dir_fd=None):
        calls.append(path)
        raise PermissionError(13, "Permission denied", path)

    monkeypatch.setattr(os, "open", denied)
    try:
        with pytest.raises(PermissionError):
            dv._open_in_dir(dir_fd, "verdict.lock", os.O_RDWR | os.O_CREAT, 0o600)
    finally:
        monkeypatch.undo()
        os.close(dir_fd)
    assert calls == ["verdict.lock"], "one try; a permission problem does not clear by waiting"


def test_qa4_eight_processes_creating_the_store_at_the_same_instant_lose_no_write(fake_deep, tmp_path):
    """Every process waits for the same instant, then writes into a folder that does not exist yet. Three fresh
    folders, so the lock file is created three times by eight processes at once."""
    fd = fake_deep()
    code = ("import time, sys, aerollm_api\nfrom arail import deep_verdict as dv\n"
            "dv.LOCK_TIMEOUT_S = 120.0\n"
            "while time.time() < float(sys.argv[1]):\n    pass\n"
            "for _ in range(3):\n    dv.note_call(False, error='p', consumer='agent:t')\n")
    for n in range(3):
        state = tmp_path / f"race-{n}"
        t0 = str(__import__("time").time() + 0.6)
        procs = [subprocess.Popen([sys.executable, "-c", code, t0], env=fd.env(ARAIL_DEEP_STATE_DIR=str(state)),
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(8)]
        for p in procs:
            _out, err = p.communicate(timeout=120)
            assert p.returncode == 0, err
        doc = json.loads((state / "verdict.json").read_text(encoding="utf-8"))
        assert doc["binaries"][fd.sha256]["failure"]["count"] == 24, f"round {n}: a write was lost"

# ── QA-5 (fix pass 3): error text reaches a terminal without its control characters ─────────────────

NASTY = "\x1b[2K\x1b[1A\x9b2K\x07\x7f"          # erase line, cursor up, a one-character CSI, BEL, DEL
ESC_ERROR = "RuntimeError: \x1b[2K\x1b[1Ainjected\x1b[0m\x07 rms_norm"


def controls_in(text: str, allowed: str = "") -> list[str]:
    """The characters of ``text`` a terminal would act on: C0 controls, DEL and the C1 range."""
    return sorted({hex(ord(c)) for c in text if (ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F) and c not in allowed})


def test_qa5_clip_replaces_what_a_terminal_acts_on_and_keeps_the_words():
    raw = ("RuntimeError: \x1b[2K\x1b[1Ainjected\x1b[0m\x07 rms_norm\nsecond line\t\x00nul \x7fdel \x80c1 "
           "\x9b31m csi \x9f")
    assert dv._clip(raw, 300) == "RuntimeError: ?[2K?[1Ainjected?[0m? rms_norm second line ?nul ?del ?c1 ?31m csi ?"


def test_qa5_exactly_the_c0_del_and_c1_controls_are_replaced_and_whitespace_still_collapses():
    """Every code point to U+02FF, one at a time: the replaced set is the spec's and nothing else moves."""
    for i in range(0x300):
        ch = chr(i)
        got = dv._clip("a" + ch + "b", 9)
        if ch.isspace():
            want = "a b"                       # newline, tab, U+001C-U+001F, U+0085, U+00A0 ... are spaces, not "?"
        elif i < 0x20 or 0x7F <= i <= 0x9F:
            want = "a?b"
        else:
            want = "a" + ch + "b"
        assert got == want, f"U+{i:04X} came out as {got!r}, wanted {want!r}"


def test_qa5_clip_leaves_ordinary_text_alone_and_the_limit_counts_what_is_shown():
    ok = "RuntimeError: échec — 失败 · 😀 [rms_norm] has 1 elements…"
    assert dv._clip(ok, 300) == ok
    cut = dv._clip("x" * 400, 300)
    assert len(cut) == 300 and cut.endswith("…")
    assert dv._clip("\x1b" * 400, 300) == "?" * 299 + "…"


def test_qa5_printable_makes_exactly_one_line_without_collapsing_or_cutting():
    assert dv._printable("a\nb\rc\x1b[0m  d\te") == "a?b?c?[0m  d?e"
    assert dv._printable(None) == "" and dv._printable("x" * 500) == "x" * 500


def test_qa5_the_stored_error_text_is_clean_in_the_file_too(fake_deep):
    fake_deep()
    b = dv.identity()
    dv.note_call(False, error=ESC_ERROR, consumer="agent:\x1b[2Kbuddy")
    f = read_store()["binaries"][b.sha256]["failure"]
    assert f["error"] == f["last_error"] == "RuntimeError: ?[2K?[1Ainjected?[0m? rms_norm"
    assert f["consumer"] == "agent:?[2Kbuddy"
    raw = store_path().read_text(encoding="utf-8")
    assert "\\u001b" not in raw and "\\u0007" not in raw, "no control character is stored, escaped or not"


@pytest.mark.parametrize("which", ["a stored call failure", "a stored failing probe"])
def test_qa5_a_hand_edited_store_cannot_write_control_characters_to_the_terminal(fake_deep, which):
    """Error text is cleaned when it is stored, but the file is editable and some of what `deep status` prints
    is not error text at all: the lab and consumer names, the timestamps, the probe's call number. The block
    is made printable where it leaves the module. A newline in a name cannot forge a second row either."""
    fake_deep()
    b = dv.identity()
    forged = "\n    generation:   passing - 5/5 at the last probe"
    if which == "a stored call failure":
        entry = {"failure": _good_failure(since="2026-10-31" + NASTY + "T13:35:02Z", lab="lab" + NASTY + forged,
                                           consumer="agent:" + NASTY, error="boom" + NASTY, last_error="boom" + NASTY)}
    else:
        entry = {"failure": _good_failure(source="probe", lab="lab" + NASTY),
                 "last_probe": {"outcome": "fail", "started_ts": 1.0, "started_at": "x", "passed": 1, "total": 5,
                                "at": "2026-10-31" + NASTY + "T13:35:02Z",
                                "first_error": {"call": "2" + NASTY + forged, "kind": "long", "error": "boom" + NASTY}}}
    store_path().parent.mkdir(parents=True, exist_ok=True)
    store_path().write_text(json.dumps(_entry_doc(b.sha256, **entry)), encoding="utf-8")
    lines = dv.status_lines()
    text = "\n".join(lines)
    assert "FAILING" in text and "boom" in text, text
    assert controls_in(text, allowed="\n") == [], "no row carries a control character"
    assert all("\n" not in ln for ln in lines)
    assert sum(1 for ln in lines if ln.lstrip().startswith("generation:")) == 1, "a name cannot forge a row"


# ── QA-6 (fix pass 3): a failure this lab could not save is cleared by a passing probe it reads ────

def _fail_while_the_lock_is_held(monkeypatch) -> None:
    """A deep call fails while another process holds the verdict lock past the cap: the failure lives only in
    this process's memory (F12), and `deep status` says NOT SAVED."""
    import fcntl
    monkeypatch.setattr(dv, "LOCK_TIMEOUT_S", 0.05)
    state = dv.state_dir()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    held = os.open(state / "verdict.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(held, fcntl.LOCK_EX)
    try:
        dv.note_call(False, error="RuntimeError: rms_norm has 1 elements", consumer="agent:buddy")
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        os.close(held)
    assert dv.current().state == "failing" and dv.current().store_error, "kept in this process, as designed"


def _another_process_stores_a_probe(b: dv.Binary, outcome: str, started_ts: float) -> None:
    """What `deep probe`, running in another process, leaves on disk. This process's memory is not touched
    (record_probe would reset the write error here, which a separate process cannot)."""
    lp = {"outcome": outcome, "started_ts": started_ts, "started_at": "x", "at": "2026-10-31T13:35:02Z",
          "passed": 5 if outcome == "pass" else 1, "total": 5, "first_error": None}
    store_path().parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    store_path().write_text(json.dumps(_entry_doc(b.sha256, last_probe=lp)), encoding="utf-8")


def test_qa6_a_pass_that_started_after_the_unsaved_failure_clears_it_and_the_not_saved_line(fake_deep, monkeypatch):
    fake_deep("pass")
    b = dv.identity()
    clock = {"now": 1000.0}
    monkeypatch.setattr(dv, "_now", lambda: clock["now"])
    _fail_while_the_lock_is_held(monkeypatch)
    assert any("NOT SAVED" in ln for ln in dv.status_lines())
    clock["now"] = 1100.0
    _another_process_stores_a_probe(b, "pass", started_ts=1050.0)
    v = dv.current()
    assert v.state == "passing", (v.state, v.detail)
    assert v.store_error is None and not any("NOT SAVED" in ln for ln in dv.status_lines()), \
        "nothing is left unsaved once the failure is gone"


@pytest.mark.parametrize("started_ts,why", [(900.0, "the probe started before the failure: it landed during the run"),
                                            (1000.0, "the same instant: it may have landed during the run")])
def test_qa6_a_failure_noted_during_or_after_the_probe_stands(fake_deep, monkeypatch, started_ts, why):
    fake_deep("pass")
    b = dv.identity()
    monkeypatch.setattr(dv, "_now", lambda: 1000.0)
    _another_process_stores_a_probe(b, "pass", started_ts=started_ts)
    assert dv.current().state == "passing"
    _fail_while_the_lock_is_held(monkeypatch)           # the failure is stamped 1000.0
    v = dv.current()
    assert v.state == "failing" and v.store_error, why
    assert any("NOT SAVED" in ln for ln in dv.status_lines())


def test_qa6_a_failing_probe_on_disk_never_clears_an_unsaved_failure(fake_deep, monkeypatch):
    fake_deep("pass")
    b = dv.identity()
    monkeypatch.setattr(dv, "_now", lambda: 1000.0)
    _fail_while_the_lock_is_held(monkeypatch)
    _another_process_stores_a_probe(b, "fail", started_ts=1050.0)       # no stored failure: only the outcome decides
    v = dv.current()
    assert v.state == "failing" and v.store_error and v.detail


@pytest.mark.parametrize("f,lp", [
    ({"last_ts": 1.0}, None),
    ({"last_ts": 1.0}, "pass"),
    ({"last_ts": 1.0}, {"outcome": "fail", "started_ts": 2.0}),
    ({"last_ts": 1.0}, {"outcome": "pass"}),
    ({"last_ts": 1.0}, {"outcome": "pass", "started_ts": "later"}),
    ({}, {"outcome": "pass", "started_ts": 2.0}),
    ({"last_ts": "soon"}, {"outcome": "pass", "started_ts": 2.0}),
    ({"last_ts": 2.0}, {"outcome": "pass", "started_ts": 2.0}),
    ({"last_ts": 3.0}, {"outcome": "pass", "started_ts": 2.0}),
], ids=["no probe", "probe is not an object", "probe failed", "no start stamp", "start stamp is text",
        "no failure stamp", "failure stamp is text", "same instant", "failure is later"])
def test_qa6_passed_since_says_yes_only_for_a_passing_probe_that_started_strictly_later(f, lp):
    assert dv._passed_since(f, lp) is False


def test_qa6_passed_since_says_yes_for_a_passing_probe_that_started_later():
    assert dv._passed_since({"last_ts": 1.0}, {"outcome": "pass", "started_ts": 2.0}) is True

# ── QA-7 (fix pass 3): passed_after, the one rule a record kept elsewhere is judged by ─────────────

def _verdict(state: str, last_probe) -> dv.Verdict:
    return dv.Verdict(state=state, binary=None, detail="", since=None, source=None, failure=None,
                      last_probe=last_probe, binary_changed_on_disk=False, store_error=None)


PASS_AT_2000 = {"outcome": "pass", "started_ts": 2000.0}


@pytest.mark.parametrize("stamp,want", [(1000.0, True), (1999.5, True), (2000.0, False), (2500.0, False),
                                        (None, False), ("soon", False), (float("nan"), False)],
                         ids=["older", "just older", "same instant", "newer", "none", "text", "NaN"])
def test_qa7_passed_after_is_true_only_for_a_record_older_than_the_passing_probe(stamp, want):
    assert dv.passed_after(_verdict("passing", PASS_AT_2000), stamp) is want


@pytest.mark.parametrize("state", ["failing", "untested"])
def test_qa7_a_verdict_that_is_not_passing_supersedes_nothing(state):
    """A failing verdict can still carry a passing last_probe (a failure landed during the run and stands)."""
    assert dv.passed_after(_verdict(state, PASS_AT_2000), 1000.0) is False


def test_qa7_a_passing_verdict_without_a_usable_probe_supersedes_nothing():
    assert dv.passed_after(_verdict("passing", None), 1000.0) is False
    assert dv.passed_after(_verdict("passing", {"outcome": "pass"}), 1000.0) is False
    assert dv.passed_after(_verdict("passing", {"outcome": "fail", "started_ts": 2000.0}), 1000.0) is False

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
    assert v.source == "call", "a state dir that cannot exist is a missing file, not an unreadable one"
    ok, err = dv.record_probe(probe_result(b, "pass", passed=5, started_ts=dv._now()))
    assert ok is False and err


# ── U9: concurrent writers keep an exact count ─────────────────────────────

def test_u9_eight_processes_fifty_writes_each_keep_an_exact_count(fake_deep):
    """The claim is the lock: every write by every process lands, so the count is exactly 400.

    The store gives up on the lock after LOCK_TIMEOUT_S (2 s) and keeps that failure in memory, by design
    (test_u9b). On a loaded machine that cap is not the thing under test: it lost 31 of 400 writes at load
    average 13 (review M7), and 117 and 124 of 400 in two runs with 150 busy processes and this test at
    background priority. So the writers here wait as long as the machine needs; a lost write can then only
    be a defect of the lock, and the test still fails if the lock is removed."""
    fd = fake_deep()
    code = ("import aerollm_api\nfrom arail import deep_verdict as dv\n"
            "dv.LOCK_TIMEOUT_S = 240.0\n"
            "for _ in range(50):\n    dv.note_call(False, error='p', consumer='agent:t')\n")
    procs = [subprocess.Popen([sys.executable, "-c", code], env=fd.env(),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for _ in range(8)]
    try:
        for p in procs:
            out, err = p.communicate(timeout=300)
            assert p.returncode == 0, err
    finally:
        for p in procs:                     # a writer left running would outlive a failed test
            if p.poll() is None:
                p.kill()
    doc = read_store()
    entry = doc["binaries"][fd.sha256]
    assert entry["failure"]["count"] == 400
    assert doc["schema"] == "arail.deep-verdict/v1"


def test_u9b_a_lock_held_past_the_cap_keeps_the_failure_in_memory_and_says_so(fake_deep, monkeypatch):
    """The cap U9 raises, pinned directly: a writer does not wait for ever on a lock another process holds. The
    failure is kept in this process, the verdict says failing, and store_error says why; nothing is written. The
    next write after the lock is released lands. (Before review M7 only a loaded machine exercised this.)"""
    import fcntl
    import os
    fake_deep()
    b = dv.identity()
    state = dv.state_dir()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    monkeypatch.setattr(dv, "LOCK_TIMEOUT_S", 0.3)
    held = os.open(state / "verdict.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(held, fcntl.LOCK_EX)
    try:
        dv.note_call(False, error="while the lock is held", consumer="agent:t")
        v = dv.current()
        assert v.state == "failing", "the failure is not lost: it is in this process"
        assert v.store_error and "the verdict lock was busy" in v.store_error
        assert not store_path().exists(), "nothing could be written"
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        os.close(held)
    dv.note_call(False, error="after the lock is released", consumer="agent:t")
    assert read_store()["binaries"][b.sha256]["failure"]["count"] >= 1
    assert dv.current().store_error is None


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
    # Review W4(d): a probe of the SAME build also clears a failure, so "after installing a new build" was wrong
    assert "Agents and the Curator use the fast model until ./arailctl deep probe passes 5/5 on this build." in failing
    assert "new build" not in failing

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
