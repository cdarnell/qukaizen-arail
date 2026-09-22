"""Re-test of the QA fix loop (commits 61f34bd7..b81cd978).

TEST_REPORT.md's five must-fix items were worked, plus F3 as a judgment
call. This file verifies each **against the code**, not against the
Re-review index, and goes one step past the original finding in each case —
the step the coordinator asked for:

  * F7/F8 — not just "a failed replace reports 0", but: the tmp file is
    cleaned up, a *crash* (not an ``OSError``) between the tmp write and the
    ``os.replace`` leaves nothing a later scan misreads, a partial failure
    across two paths counts only the path that landed, and the successful
    path still clears memory.
  * F2   — not just "the message no longer reaches disk", but: can the
    allow-list be bypassed by a class-name-shaped secret, and is every other
    free-text exception field gone at the schema level?
  * F4/F5 — not just "it does not raise", but: the metadata record is still
    written, on both chokepoint methods.
  * F9   — is the re-``__init__`` masking a real ``cost_tracker`` bug, or is
    the binding genuinely test-only (per-process, per-World)?
  * F3   — is ``isinstance`` the right guard, and does the *endpoint* degrade
    honestly, not just the scan function?
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from arail import activity, agent_context, agent_trace, config, jsonl_purge
from arail.router.backends import BaseBackend, ModelResponse
from arail.router.core import ModelRouter

SECRET = "sk-retestfixloop0000000000000"


class _FakeBackend(BaseBackend):
    def __init__(self, text: str = "answer") -> None:
        self.text = text

    def complete(self, prompt, max_tokens=512, temperature=0.7, top_p=None,
                 *, system=None, messages=None):
        return ModelResponse(text=self.text, model="fake-qa", tokens_used=4,
                             backend="fake", latency_ms=1.0)

    def health_check(self) -> bool:
        return True


class _StreamingFake(_FakeBackend):
    def stream_complete(self, prompt, max_tokens=512, temperature=0.7,
                        top_p=None, *, system=None, messages=None):
        yield "ans"
        yield ModelResponse(text=self.text, model="fake-qa", tokens_used=4,
                            backend="fake", latency_ms=1.0)


@pytest.fixture
def maximus(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")


@pytest.fixture
def client():
    from arail.portal.app import app
    with TestClient(app) as c:
        yield c


def _seed_legacy(path: Path, n: int = 4) -> None:
    lines = [json.dumps({
        "ts": f"2026-09-0{i + 1}T00:00:00Z", "source": "researcher",
        "level": "info", "message": f"call {i}",
        "data": {"prompt_trace": {"prompt": f"secret {SECRET} {i}",
                                  "response": "reply", "latency_ms": 12}},
    }) for i in range(n)]
    path.write_text("\n".join(lines) + "\n")


def _disk_traces() -> list[dict]:
    p = config.DATA_DIR / "agent_traces.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


# ===========================================================================
# F7 / F8 — the purge tells the truth, and only clears memory it really cleaned
# ===========================================================================

def test_f7_the_purge_contract_now_reports_ok_and_errors(tmp_path,
                                                         monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(activity, "LOG_FILE", tmp_path / "activity.jsonl")
    _seed_legacy(tmp_path / "activity.jsonl", 4)

    result = activity.purge_legacy_bodies()
    assert result["ok"] is True
    assert result["purged"] == 4
    assert result["purged_memory"] == 0  # nothing in the buffer this test
    assert SECRET not in (tmp_path / "activity.jsonl").read_text()


def test_f7_a_failed_replace_reports_zero_purged_and_not_ok(tmp_path,
                                                            monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(activity, "LOG_FILE", tmp_path / "activity.jsonl")
    path = tmp_path / "activity.jsonl"
    _seed_legacy(path, 4)
    original = path.read_bytes()

    monkeypatch.setattr(jsonl_purge.os, "replace",
                        lambda src, dst: (_ for _ in ()).throw(
                            OSError(28, "No space left on device")))
    result = activity.purge_legacy_bodies()

    assert result["purged"] == 0, result
    assert result["ok"] is False, result
    assert path.read_bytes() == original
    assert list(tmp_path.glob("*.purge_tmp")) == []
    # `errors` is produced by the helper but NOT forwarded by
    # purge_legacy_bodies(), so the API answers "it failed" without saying
    # why. Assert it at the layer that owns it so the detail is not lost.
    helper = jsonl_purge.purge_jsonl_bodies(
        [path], activity._has_legacy_body, activity._strip_legacy_body)
    assert helper["ok"] is False
    assert helper["errors"] and helper["errors"][0]["path"].endswith(
        "activity.jsonl")
    assert "error" in helper["errors"][0]
    assert "errors" not in result, (
        "if this starts forwarding, tighten the API assertion above")


def test_f7_a_partial_failure_counts_only_the_path_that_landed(tmp_path,
                                                               monkeypatch):
    """Rotation means two paths. If the rotated file replaces and the active
    one does not, the count must reflect only the first, and ``ok`` must be
    False so no caller clears an in-memory copy."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    active = tmp_path / "activity.jsonl"
    rotated = tmp_path / "activity.jsonl.1"
    monkeypatch.setattr(activity, "LOG_FILE", active)
    _seed_legacy(active, 4)
    _seed_legacy(rotated, 3)
    active_before = active.read_bytes()

    real_replace = os.replace

    def _selective(src, dst):
        if str(dst).endswith("activity.jsonl"):
            raise OSError(30, "Read-only file system")
        return real_replace(src, dst)

    monkeypatch.setattr(jsonl_purge.os, "replace", _selective)
    result = activity.purge_legacy_bodies()

    assert result["purged"] == 3, (
        f"only the rotated file landed; got {result}")
    assert result["ok"] is False
    assert active.read_bytes() == active_before
    assert SECRET not in rotated.read_text()
    assert list(tmp_path.glob("*.purge_tmp")) == []


def test_f7_a_crash_mid_purge_leaves_nothing_a_later_scan_misreads(
        tmp_path, monkeypatch):
    """The coordinator's harder case: a process death between the tmp write
    and ``os.replace`` is not an ``OSError``, so the cleanup ``except`` never
    runs and a ``*.purge_tmp`` survives. That is acceptable **only if** no
    later reader mistakes it for a log."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    path = tmp_path / "activity.jsonl"
    monkeypatch.setattr(activity, "LOG_FILE", path)
    _seed_legacy(path, 4)
    original = path.read_bytes()

    class _Killed(BaseException):
        """Not an Exception — models SIGKILL/SystemExit, which no handler
        in the purge path catches."""

    real_replace = jsonl_purge.os.replace
    jsonl_purge.os.replace = lambda src, dst: (_ for _ in ()).throw(_Killed())
    try:
        with pytest.raises(_Killed):
            activity.purge_legacy_bodies()
    finally:
        # Restore only this patch -- monkeypatch.undo() would also revert
        # DATA_DIR/LOG_FILE and point the scan below at the REAL lab log.
        jsonl_purge.os.replace = real_replace

    strays = list(tmp_path.glob("*.purge_tmp"))
    assert len(strays) == 1, "precondition: the crash really left a temp file"
    assert path.read_bytes() == original, "the original must be untouched"

    # The surviving temp file must not be read by anything.
    scan = activity.scan_for_legacy_bodies()
    assert scan["count"] == 4, (
        f"the scan counted the stray temp file too: {scan}")
    # And it is strictly less sensitive than the original anyway.
    assert SECRET not in strays[0].read_text()


def test_f8_memory_is_cleared_only_once_disk_is_actually_clean(tmp_path,
                                                                monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(activity, "LOG_FILE", tmp_path / "activity.jsonl")
    _seed_legacy(tmp_path / "activity.jsonl", 2)
    activity.activity_log.emit(
        "researcher", "in memory too", "info",
        {"prompt_trace": {"prompt": f"mem {SECRET}", "response": "r"}})

    # (1) disk fails -> memory untouched
    real_replace = jsonl_purge.os.replace
    monkeypatch.setattr(jsonl_purge.os, "replace",
                        lambda src, dst: (_ for _ in ()).throw(
                            OSError(30, "Read-only file system")))
    failed = activity.purge_legacy_bodies()
    assert failed["purged_memory"] == 0 and failed["ok"] is False
    assert SECRET in json.dumps(activity.activity_log.recent(50))

    # (2) disk succeeds -> memory cleared in the same call. Three on disk,
    # not two: activity_log.emit() above appended its own body-bearing line
    # to the same LOG_FILE.
    monkeypatch.setattr(jsonl_purge.os, "replace", real_replace)
    ok = activity.purge_legacy_bodies()
    assert ok["ok"] is True and ok["purged"] == 3, ok
    assert ok["purged_memory"] == 1
    assert SECRET not in json.dumps(activity.activity_log.recent(50))
    assert SECRET not in (tmp_path / "activity.jsonl").read_text()


def test_f8_the_flight_recorder_twin_gates_its_ring_the_same_way(monkeypatch):
    """`agent_trace.purge_flight_recorder_bodies` got the same fix. The ring
    is the live Admin view, so clearing it after a failed disk rewrite would
    hide the leak in exactly the same way."""
    # A marker with no credential shape: the point here is "is the body
    # still on disk", and a key-shaped one would be redacted away.
    marker = "PLAINBODYMARKER"
    agent_trace.set_recorder_enabled(True)
    router = ModelRouter.from_backend(_FakeBackend(f"resp {marker}"), "fake")
    with agent_context.agent_call("researcher"):
        router.complete(f"prompt {marker}")
    assert isinstance(agent_trace.ring(1)[0]["bodies"], dict)

    # Save/restore this one patch by hand: monkeypatch.undo() would also
    # revert the conftest autouse fixtures' patches (they share this same
    # function-scoped monkeypatch instance), re-pointing config.DATA_DIR at
    # the developer's REAL lab/data for the rest of the test.
    real_replace = jsonl_purge.os.replace
    jsonl_purge.os.replace = lambda src, dst: (_ for _ in ()).throw(
        OSError(28, "No space left on device"))
    try:
        result = agent_trace.purge_flight_recorder_bodies()
    finally:
        jsonl_purge.os.replace = real_replace

    assert result["purged"] == 0 and result["ok"] is False
    assert result["purged_memory"] == 0
    assert isinstance(agent_trace.ring(1)[0]["bodies"], dict), (
        "the ring was cleared while the bodies were still on disk")
    assert marker in (config.DATA_DIR / "agent_traces.jsonl").read_text()

    good = agent_trace.purge_flight_recorder_bodies()
    assert good["ok"] is True and good["purged"] >= 1
    assert good["purged_memory"] >= 1
    assert agent_trace.ring(1)[0]["bodies"] is None
    assert marker not in (config.DATA_DIR / "agent_traces.jsonl").read_text()


def test_f7_both_purge_endpoints_still_answer_200_on_a_failed_purge(
        tmp_path, monkeypatch, client, maximus):
    """A failed purge must be a reported failure, not an exception."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(activity, "LOG_FILE", tmp_path / "activity.jsonl")
    _seed_legacy(tmp_path / "activity.jsonl", 2)
    monkeypatch.setattr(jsonl_purge.os, "replace",
                        lambda src, dst: (_ for _ in ()).throw(
                            OSError(28, "No space left on device")))

    resp = client.post("/api/admin/legacy-bodies/purge")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False and body["purged"] == 0

    # Give the recorder purge a real file to fail on, or it correctly
    # reports ok=True for "there was nothing to purge".
    agent_trace.set_recorder_enabled(True)
    router = ModelRouter.from_backend(_FakeBackend("body"), "fake")
    with agent_context.agent_call("researcher"):
        router.complete("captured")
    assert (config.DATA_DIR / "agent_traces.jsonl").exists()

    resp2 = client.post("/api/admin/flight-recorder",
                        json={"enabled": False, "purge": True})
    assert resp2.status_code == 200
    assert resp2.json()["purge"]["ok"] is False


def test_f7_residual_the_admin_ui_must_surface_a_failed_purge():
    """**New finding (fix-loop residual).** The API now tells the truth
    (``ok: false``, ``purged: 0``, ``errors``), but both purge handlers in
    ``admin.html`` read only ``purged``/``purged_memory`` and log at
    ``info``:

        adminLog(`Legacy bodies purge: ${data.purged} on disk, …`, 'info')
        adminLog(`Flight recorder purge: ${p.purged} on disk, …`, 'info')

    so a failed purge renders as "0 on disk, 0 in memory" — indistinguishable
    from "there was nothing to purge". F7's whole point was that the operator
    must not be misled about whether secrets were deleted; the server half is
    fixed and the operator-facing half is now silent instead of wrong.

    Mitigation that keeps this off the must-fix list: the legacy notice
    re-scans immediately afterwards and still shows its unchanged count, so
    an attentive operator has a signal. The flight-recorder purge has no such
    second signal.
    """
    page = Path("src/arail/portal/templates/admin.html").read_text()
    purge_js = page[page.index("async function purgeFlightRecorderBodies"):]
    purge_js = purge_js[:purge_js.index("async function loadLegacyBodiesNotice")]
    legacy_js = page[page.index("async function purgeLegacyBodies"):]
    legacy_js = legacy_js[:legacy_js.index("async function dismissLegacyBodiesNotice")]

    for name, js in (("flight recorder", purge_js), ("legacy bodies", legacy_js)):
        if ".ok" not in js:
            pytest.xfail(
                f"BACKLOG (QA re-test, F7 residual): the {name} purge handler "
                "in admin.html ignores the new `ok` flag and logs a failed "
                "purge at info level as '0 on disk, 0 in memory'. Three-line "
                "fix: `if (!data.ok) adminLog(..., 'error')`.")
    for name, js in (("flight recorder", purge_js), ("legacy bodies", legacy_js)):
        assert "'error'" in js, name


# ===========================================================================
# F2 — error_class is a class name, and nothing else free-text reaches a record
# ===========================================================================

_CLASS_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _run_goal_parser_subprocess(monkeypatch, stdout_payload: dict):
    import subprocess as subprocess_mod

    from arail.skills.goal_parser import GoalParser

    class _Completed:
        returncode = 0
        stdout = json.dumps(stdout_payload)
        stderr = ""

    monkeypatch.setattr(subprocess_mod, "run", lambda *a, **kw: _Completed())
    parser = GoalParser.__new__(GoalParser)
    return parser._llm_subprocess("parse this goal")


@pytest.mark.parametrize("child_error_class", [
    "AuthError: 401 for https://api.example.com (Authorization: Bearer x)",
    "RuntimeError: " + SECRET,
    "has spaces",
    "has-a-dash",
    "has.a.dot",
    "1StartsWithADigit",
    "",
    "A" * 65,
    None,
    12345,
    {"nested": "dict"},
])
def test_f2_a_non_class_name_error_class_falls_back_to_a_generic_name(
        monkeypatch, child_error_class):
    payload = {"ok": False, "error": f"free text with {SECRET}"}
    if child_error_class is not None or "error_class" in payload:
        payload["error_class"] = child_error_class

    assert _run_goal_parser_subprocess(monkeypatch, payload) is None

    rec = agent_trace.ring(1)[0]
    assert rec["error_class"] == "SubprocessError", rec["error_class"]
    on_disk = (config.DATA_DIR / "agent_traces.jsonl").read_text()
    assert on_disk.strip(), "presence first"
    assert SECRET not in on_disk
    assert "Authorization" not in on_disk


def test_f2_the_childs_free_text_error_and_traceback_never_reach_a_record(
        monkeypatch):
    """The real fix: the parent no longer reads the ``error`` message field
    at all, and the child's ``trace`` (500 chars of traceback) was never
    read."""
    assert _run_goal_parser_subprocess(monkeypatch, {
        "ok": False,
        "error": f"RuntimeError: 401 (Authorization: Bearer {SECRET})",
        "error_class": "RuntimeError",
        "trace": f"Traceback...\n  api_key={SECRET}\n",
    }) is None

    rec = agent_trace.ring(1)[0]
    assert rec["error_class"] == "RuntimeError"
    dumped = json.dumps(rec, default=str)
    assert SECRET not in dumped
    assert "Traceback" not in dumped
    assert "Authorization" not in dumped


def test_f2_no_field_in_the_trace_schema_can_carry_an_exception_message():
    """Schema level, as asked: there is no free-text exception field at all,
    and ``record()`` drops any a future producer invents."""
    for forbidden in ("error", "error_message", "message", "trace",
                      "traceback", "stderr", "detail", "exception"):
        assert forbidden not in agent_trace._FIELDS, forbidden

    agent_trace.record(
        kind="agent", agent_id="buddy", outcome="error",
        error_class="RuntimeError",
        error=f"smuggled {SECRET}", trace=f"tb {SECRET}",
        message=f"msg {SECRET}", stderr=f"err {SECRET}",
    )
    rec = agent_trace.ring(1)[0]
    assert set(rec) == set(agent_trace._FIELDS)
    assert SECRET not in json.dumps(rec, default=str)
    assert SECRET not in (config.DATA_DIR / "agent_traces.jsonl").read_text()


def test_f2_the_chokepoints_own_error_path_is_still_a_class_name():
    class _Leaky(_FakeBackend):
        def complete(self, *a, **kw):
            raise RuntimeError(f"401 (Authorization: Bearer {SECRET})")

    router = ModelRouter.from_backend(_Leaky(), "fake")
    with agent_context.agent_call("researcher"):
        with pytest.raises(RuntimeError):
            router.complete("trigger")
    rec = agent_trace.ring(1)[0]
    assert _CLASS_NAME_RE.fullmatch(rec["error_class"])
    assert SECRET not in (config.DATA_DIR / "agent_traces.jsonl").read_text()


def test_f2_residual_a_class_name_shaped_secret_still_passes_the_allow_list(
        monkeypatch):
    """**New finding (low, defense-in-depth).** The allow-list is a *shape*
    check, and several real credential formats are class-name-shaped:
    ``hf_<24 alnum>``, ``AKIAIOSFODNN7EXAMPLE``, an alphanumeric passphrase.
    A child that put one in ``error_class`` would still reach disk.

    Not a live leak — the only producer is our own ``_subprocess_runner``,
    which sends ``type(exc).__name__`` — so this is a gap in the enforcement
    *point*, not in current behaviour. Closing it is cheap: run the candidate
    through ``redact.redact()`` and reject it if anything changes.
    """
    hf_token = "hf_" + "a" * 24
    assert _CLASS_NAME_RE.fullmatch(hf_token), "precondition: it is shaped ok"

    assert _run_goal_parser_subprocess(monkeypatch, {
        "ok": False, "error": "irrelevant", "error_class": hf_token}) is None

    on_disk = (config.DATA_DIR / "agent_traces.jsonl").read_text()
    if hf_token in on_disk:
        pytest.xfail(
            "BACKLOG (QA re-test, F2 residual): _sanitize_error_class "
            "(goal_parser/__init__.py:37-47) validates shape only, so an "
            "alphanumeric credential (hf_*, AKIA*, an alphanumeric "
            "passphrase) is class-name-shaped and passes. No live producer "
            "sends one; harden with a redact.redact() round-trip.")
    assert hf_token not in on_disk


# ===========================================================================
# F4 / F5 — the chokepoint owns its no-raise guarantee, and still records
# ===========================================================================

def _break_redact_import(monkeypatch):
    import sys

    import arail
    monkeypatch.delattr(arail, "redact", raising=False)
    monkeypatch.setitem(sys.modules, "arail.redact", None)


def test_f4_a_broken_redact_import_still_leaves_a_metadata_record(monkeypatch):
    agent_trace.set_recorder_enabled(True)
    router = ModelRouter.from_backend(_FakeBackend("the answer"), "fake")
    _break_redact_import(monkeypatch)

    with agent_context.agent_call("researcher"):
        resp = router.complete("work")

    assert resp.text == "the answer"
    traces = _disk_traces()
    assert len(traces) == 1, "the metadata record must still be written"
    assert traces[0]["outcome"] == "ok"
    assert traces[0]["bodies"] is None
    assert traces[0]["attribution"] == "agent:researcher"
    assert traces[0]["tokens_out"] == 4


def test_f5_a_raising_capture_body_still_leaves_a_metadata_record(monkeypatch):
    import arail.redact as redact_mod
    agent_trace.set_recorder_enabled(True)
    monkeypatch.setattr(redact_mod, "capture_body",
                        lambda p, r: (_ for _ in ()).throw(
                            RuntimeError("redactor exploded")))
    router = ModelRouter.from_backend(_FakeBackend("the answer"), "fake")

    with agent_context.agent_call("researcher"):
        resp = router.complete("work")

    assert resp.text == "the answer"
    traces = _disk_traces()
    assert len(traces) == 1 and traces[0]["outcome"] == "ok"
    assert traces[0]["bodies"] is None


def test_f4_f5_the_streaming_site_is_guarded_too_and_still_yields(monkeypatch):
    """The second of the two sites, and the one where a raise would abort a
    stream that has already yielded real tokens to a caller."""
    import arail.redact as redact_mod
    agent_trace.set_recorder_enabled(True)
    monkeypatch.setattr(redact_mod, "capture_body",
                        lambda p, r: (_ for _ in ()).throw(
                            RuntimeError("redactor exploded")))
    router = ModelRouter.from_backend(_StreamingFake("streamed"), "fake")

    with agent_context.agent_call("buddy"):
        items = list(router.stream_complete("work"))

    assert [i for i in items if isinstance(i, str)] == ["ans"]
    traces = _disk_traces()
    assert len(traces) == 1
    assert traces[0]["outcome"] == "ok" and traces[0]["streamed"] is True
    assert traces[0]["bodies"] is None
    assert traces[0]["ttft_status"] == "measured"


def test_f4_a_broken_redact_import_does_not_break_the_stream(monkeypatch):
    agent_trace.set_recorder_enabled(True)
    router = ModelRouter.from_backend(_StreamingFake("streamed"), "fake")
    _break_redact_import(monkeypatch)

    with agent_context.agent_call("buddy"):
        items = list(router.stream_complete("work"))

    assert any(isinstance(i, ModelResponse) for i in items)
    assert len(_disk_traces()) == 1


def test_f4_f5_the_guard_is_scoped_to_the_body_capture_only(monkeypatch):
    """The guard must not have swallowed a *backend* failure along with the
    redaction failure — an inference error still has to propagate."""
    class _Dead(_FakeBackend):
        def complete(self, *a, **kw):
            raise ConnectionError("backend down")

    router = ModelRouter.from_backend(_Dead(), "fake")
    with agent_context.agent_call("researcher"):
        with pytest.raises(ConnectionError):
            router.complete("work")
    assert _disk_traces()[0]["outcome"] == "error"


# ===========================================================================
# F9 — cost_tracker isolation: correct shape, and test-only in production
# ===========================================================================

def test_f9_every_test_starts_from_a_zeroed_cost_tracker_under_tmp():
    from arail.costs import cost_tracker
    assert cost_tracker._data_path.parent == config.DATA_DIR
    assert str(config.DATA_DIR).startswith(("/private/var", "/tmp", "/var")), (
        f"DATA_DIR is not a tmp path: {config.DATA_DIR}")
    assert cost_tracker.total_calls == 0
    assert cost_tracker.total_billed_usage_usd == 0.0
    assert cost_tracker.calls_by_source == {}


def test_f9_a_call_billed_in_one_test_does_not_survive_into_the_next():
    """Paired with the test below: this one bills, the next asserts zero.
    Named so they sort adjacently and the pair is obvious."""
    from arail.costs import cost_tracker
    router = ModelRouter.from_backend(_FakeBackend(), "fake")
    with agent_context.agent_call("researcher"):
        router.complete("bill me")
    assert cost_tracker.total_calls == 1
    assert cost_tracker.total_billed_usage_usd > 0


def test_f9_b_the_next_test_sees_a_zeroed_tracker_again():
    from arail.costs import cost_tracker
    assert cost_tracker.total_calls == 0
    assert cost_tracker.total_billed_usage_usd == 0.0


def test_f9_the_re_init_approach_is_the_only_one_other_modules_would_see():
    """Why re-``__init__`` rather than a fresh instance: every module did
    ``from arail.costs import cost_tracker`` at import time, so rebinding the
    module attribute would leave those references on the old object — the
    ``ActivityLog`` lesson. ``CostTracker()`` also cannot make a new one."""
    from arail import costs as costs_mod
    from arail.costs import cost_tracker
    assert costs_mod.CostTracker() is cost_tracker, (
        "CostTracker is a singleton; a fresh instance is not obtainable")
    from arail.router import core as router_core
    assert router_core.cost_tracker is cost_tracker, (
        "the router holds its own import-time reference to the same object")


def test_f9_the_binding_is_import_time_which_is_safe_per_world_in_production():
    """Is the re-``__init__`` masking a production bug? No: ``_data_path`` is
    bound from ``config.DATA_DIR``, which is assigned exactly once at
    ``arail.config`` import from ``ARAIL_DATA_DIR`` and never rebound in
    ``src/``. Each concurrent World is its own process with that env set
    before import, so the binding is correct for that World's whole life.
    The hazard is test-only — which is exactly where the fix went."""
    import ast

    src_root = Path(config.__file__).parent
    config_tree = ast.parse(Path(config.__file__).read_text())
    assignments = [
        node for node in ast.walk(config_tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "DATA_DIR"
                for t in node.targets)
    ]
    assert len(assignments) == 1, (
        f"config.DATA_DIR is assigned {len(assignments)} times")

    rebinds = []
    for path in src_root.rglob("*.py"):
        if path == Path(config.__file__):
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "DATA_DIR":
                parent_is_store = isinstance(node.ctx, ast.Store)
                if parent_is_store:
                    rebinds.append(str(path))
    assert rebinds == [], (
        f"config.DATA_DIR is rebound in-process, which would break the "
        f"per-World binding this fixture relies on: {rebinds}")


def test_f9_the_real_lab_costs_json_is_not_recreated_by_the_suite():
    """The orchestrator deleted this worktree's polluted file. The fixture
    must keep it deleted."""
    repo_root = Path(__file__).resolve().parent.parent
    real = repo_root / "lab" / "data" / "costs.json"
    router = ModelRouter.from_backend(_FakeBackend(), "fake")
    with agent_context.agent_call("buddy"):
        router.complete("bill me")
    assert not real.exists(), (
        f"the suite recreated the real cost file at {real}")


def test_f9_recap_cost_ceiling_reads_the_isolated_tracker():
    """The mechanism behind the 21-test regression, pinned: recap's ceiling
    reads the same accumulator the fixture now zeroes."""
    from arail.costs import cost_tracker
    router = ModelRouter.from_backend(_FakeBackend(), "fake")
    for _ in range(5):
        with agent_context.agent_call("researcher"):
            router.complete("work")
    assert cost_tracker.total_billed_usage_usd < 5.0, (
        "five fake calls should be nowhere near recap's $5 ceiling")


# ===========================================================================
# F3 — the isinstance guard, and the endpoint (not just the function)
# ===========================================================================

@pytest.mark.parametrize("line", [
    "[1, 2, 3]",
    "123",
    "12.5",
    '"a bare string"',
    "true",
    "null",
    '{"data": "oops, not a dict"}',
    '{"data": [1, 2]}',
    '{"data": 7}',
    '{"data": {"prompt_trace": "not a dict"}}',
    '{"data": {"prompt_trace": [1, 2]}}',
    '{"data": {"prompt_trace": null}}',
    '{"data": {}}',
    "{}",
])
def test_f3_the_scan_never_raises_on_any_non_dict_shape(tmp_path, monkeypatch,
                                                        line):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(activity, "LOG_FILE", tmp_path / "activity.jsonl")
    good = json.dumps({"source": "researcher",
                       "data": {"prompt_trace": {"prompt": "p"}}})
    (tmp_path / "activity.jsonl").write_text(line + "\n" + good + "\n")

    result = activity.scan_for_legacy_bodies()
    assert result["count"] == 1, (
        f"{line!r} broke the scan or was miscounted: {result}")


@pytest.mark.parametrize("line", ['{"data": "oops"}', "[1,2,3]", "123"])
def test_f3_the_admin_endpoint_degrades_honestly_on_every_page_load(
        tmp_path, monkeypatch, client, maximus, line):
    """The scan runs on every Admin page load via ``loadLegacyBodiesNotice()``
    — the endpoint, not just the function, has to survive."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(activity, "LOG_FILE", tmp_path / "activity.jsonl")
    body = json.dumps({"source": "researcher",
                       "data": {"prompt_trace": {"prompt": "p"}}})
    (tmp_path / "activity.jsonl").write_text(line + "\n" + body + "\n")

    resp = client.get("/api/admin/legacy-bodies")
    assert resp.status_code == 200, resp.text[:200]
    assert resp.json()["count"] == 1

    assert client.get("/admin").status_code == 200


def test_f3_the_purge_also_survives_a_corrupt_line_byte_identically(
        tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(activity, "LOG_FILE", tmp_path / "activity.jsonl")
    path = tmp_path / "activity.jsonl"
    good = json.dumps({"source": "researcher",
                       "data": {"prompt_trace": {"prompt": SECRET}}})
    path.write_text('{"data": "oops"}\n' + good + "\n[1,2,3]\n")

    result = activity.purge_legacy_bodies()
    lines = path.read_text().splitlines()
    assert result["purged"] == 1 and result["ok"] is True
    assert len(lines) == 3, "the exact line count never changes"
    # Every parseable line is re-serialised by json.dumps, so "byte-identical"
    # in the docstring covers only lines that fail to parse. Semantics, not
    # bytes, is the promise that actually holds for these.
    assert json.loads(lines[0]) == {"data": "oops"}
    assert json.loads(lines[2]) == [1, 2, 3]
    assert SECRET not in path.read_text()


def test_f3_the_guard_is_isinstance_not_a_blanket_try(tmp_path, monkeypatch):
    """Judging the guard: ``isinstance`` keeps a *genuine* malformed record
    countable rather than swallowing every error. A blanket ``except`` around
    the loop would have hidden a real bug in ``_has_legacy_body``; this does
    not — a body-bearing dict is still detected on the very next line."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(activity, "LOG_FILE", tmp_path / "activity.jsonl")
    rows = ['{"data": "oops"}']
    rows += [json.dumps({"source": f"a{i}",
                         "data": {"prompt_trace": {"prompt": "p"}}})
             for i in range(3)]
    (tmp_path / "activity.jsonl").write_text("\n".join(rows) + "\n")

    result = activity.scan_for_legacy_bodies()
    assert result["count"] == 3
    assert result["by_source"] == {"a0": 1, "a1": 1, "a2": 1}, (
        "a corrupt line must not abort or skew the rest of the scan")
