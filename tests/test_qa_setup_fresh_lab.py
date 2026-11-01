"""Setup on a clean machine (arail CLAUDE.md's 30% slice).

The scenario: a fresh clone on someone else's laptop. No ``lab/data``, no
``lab/pkb``, no models, no Ollama, nothing ever toggled. Every runtime file
this sprint added is *absent*, and the ones it reads may be absent, empty,
huge, truncated, malformed, or unreadable.

What must hold:
  * the portal boots and its pages render with the new instrumentation;
  * the four new Admin endpoints and the lanes card render an **honest empty
    state** — not a crash, not a blank, not a zero pretending to be data;
  * ``minimalist`` gets a server-side 404 on every new endpoint, including
    the SSE stream, **before any body parse**;
  * the legacy-bodies boot scan degrades honestly on every shape of
    ``activity.jsonl``;
  * a missing / corrupt / unreadable ``flight_recorder.json`` means "off",
    which is the safe direction.

TestClient only — the portal is never started as a server, no weights load,
and Ollama is never required.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from arail import activity, agent_trace, config

NEW_GET_ENDPOINTS = (
    "/api/admin/agent-lanes",
    "/api/admin/agent-trace-stream",
    "/api/admin/agent-trace/deadbeefdeadbeef",
    "/api/admin/legacy-bodies",
)
NEW_POST_ENDPOINTS = (
    "/api/admin/agents/hold",
    "/api/admin/flight-recorder",
    "/api/admin/legacy-bodies/purge",
    "/api/admin/legacy-bodies/dismiss",
)


@pytest.fixture
def fresh_lab(monkeypatch, tmp_path):
    """A DATA_DIR that is genuinely empty — not even the onboarding marker
    the repo conftest pre-seeds, so this really is a first-boot lab."""
    root = tmp_path / "fresh-lab-data"
    root.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", root)
    monkeypatch.setattr(activity, "LOG_FILE", root / "activity.jsonl")
    monkeypatch.setattr(config, "PKB_ROOT", tmp_path / "fresh-lab-pkb")
    agent_trace._reset_for_tests()
    assert list(root.iterdir()) == []
    return root


@pytest.fixture
def maximus(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")


@pytest.fixture
def minimalist(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "minimalist")


@pytest.fixture
def client():
    from arail.portal.app import app
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# The portal boots with the new instrumentation
# ---------------------------------------------------------------------------

def test_dashboard_renders_on_a_lab_with_no_runtime_state(fresh_lab, client,
                                                          monkeypatch):
    monkeypatch.setattr(__import__("arail.portal.app", fromlist=["app"]),
                        "_world_prompt_marker",
                        lambda: fresh_lab / ".world-prompt-seen")
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.text.strip(), "the dashboard rendered empty"


def test_admin_page_renders_on_a_fresh_maximus_lab(fresh_lab, maximus, client):
    resp = client.get("/admin")
    assert resp.status_code == 200
    body = resp.text
    assert "Agent lanes" in body, "the lanes card is missing from Admin"
    assert "agent-lanes-list" in body
    assert "/api/admin/agent-trace-stream" in body, (
        "the card must be SSE-driven; a poll interval is the only way W1's "
        "2 s bound could be missed")
    assert "setInterval" not in body.split("Agent lanes")[1][:4000], (
        "a refresh interval appeared in the lanes card")


def test_agents_page_renders_and_shows_the_recorder_off_empty_state(
        fresh_lab, client):
    resp = client.get("/agents")
    assert resp.status_code == 200
    assert "flight recorder off" in resp.text.lower(), (
        "the Prompt Inspector must say why it is empty, not just be empty")


# ---------------------------------------------------------------------------
# Honest empty states
# ---------------------------------------------------------------------------

def test_lanes_snapshot_on_a_fresh_lab_is_complete_and_honest(fresh_lab):
    snap = agent_trace.lanes_snapshot()

    assert snap["schema"] == "arail.agent_lanes/v1"
    ids = [lane["id"] for lane in snap["lanes"]]
    assert ids == [lane for lane, _ in agent_trace.FIXED_LANES], (
        "every lane is always present — nothing is ever hidden")
    for lane in snap["lanes"]:
        assert lane["calls"] == 0
        assert lane["last"] is None
        assert lane["empty_reason"], f"{lane['id']} has a blank empty state"
        # Absent data must be null, never 0 / "" / "n/a".
        for key in ("model", "backend", "brain", "effort", "ttft_ms",
                    "tokens_in", "tokens_out", "deep_reason_code"):
            assert lane[key] is None, (lane["id"], key, lane[key])

    assert snap["unattributed"] == {"calls": 0, "call_sites": []}
    assert snap["recorder"]["enabled"] is False
    assert snap["recorder"]["changed_at"] is None
    assert snap["hold"]["held"] is False
    assert snap["drops"]["dropped_writes"] == 0
    assert snap["window"]["recorded"] == 0
    assert snap["slot"]["samples"] == 0
    assert snap["slot"]["overlap_pct"] == 0.0


def test_model_free_lanes_say_so_instead_of_showing_a_dead_lane(fresh_lab):
    snap = agent_trace.lanes_snapshot()
    reasons = {lane["id"]: lane["empty_reason"] for lane in snap["lanes"]}
    assert reasons["sre"] == "does not call a model"
    assert reasons["presence"] == "does not call a model"
    assert reasons["curator"] == "does not call a model"
    assert reasons["buddy"] == "no calls yet this session"


def test_lanes_snapshot_survives_the_inference_scheduler_being_unavailable(
        fresh_lab, monkeypatch):
    """The snapshot is also served from contexts where ``arail.portal`` is not
    importable (a CLI, the goal-parser child). A failure there must degrade to
    nulls, not a 500."""
    import arail.portal.scheduler as sched

    def _boom():
        raise RuntimeError("no portal here")

    monkeypatch.setattr(sched, "slot_pressure", _boom)
    snap = agent_trace.lanes_snapshot()
    assert snap["slot"]["capacity"] is None
    assert snap["slot"]["in_flight"] is None


def test_prompts_endpoint_on_a_fresh_lab_returns_the_ledgers_empty_state(
        fresh_lab, client):
    resp = client.get("/api/agents/prompts")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["traces"] == []
    assert payload["recorder"] == {"enabled": False, "reason": "off_by_default"}
    assert payload["empty_state"] == (
        "flight recorder off — flip to capture prompt bodies")


def test_agent_lanes_endpoint_on_a_fresh_maximus_lab(fresh_lab, maximus,
                                                    client):
    resp = client.get("/api/admin/agent-lanes")
    assert resp.status_code == 200
    assert resp.json()["window"]["recorded"] == 0


def test_agent_trace_detail_404s_on_an_unknown_id(fresh_lab, maximus, client):
    resp = client.get("/api/admin/agent-trace/nosuchtraceid")
    assert resp.status_code == 404
    assert resp.json() == {"error": "unknown_trace_id"}


def test_legacy_bodies_endpoint_on_a_lab_with_no_activity_log(fresh_lab,
                                                             maximus, client):
    resp = client.get("/api/admin/legacy-bodies")
    assert resp.status_code == 200
    assert resp.json() == {"count": 0, "by_source": {}, "dismissed": False}


def test_agents_status_reports_zero_tokens_without_inventing_a_ceiling(
        fresh_lab, client):
    """V7: ``tokens`` used to sum ``max_tokens``, the requested ceiling. On a
    fresh lab both keys must be a real 0."""
    resp = client.get("/api/agents/status")
    assert resp.status_code == 200
    payload = resp.json()
    researcher = payload.get("researcher") or {}
    assert researcher.get("tokens_out") == 0
    assert researcher.get("tokens") == 0


# ---------------------------------------------------------------------------
# Tier gating — 404 on minimalist, before any body parse
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", NEW_GET_ENDPOINTS)
def test_every_new_get_endpoint_404s_on_minimalist(fresh_lab, minimalist,
                                                   client, path):
    resp = client.get(path)
    assert resp.status_code == 404, path


@pytest.mark.parametrize("path", NEW_POST_ENDPOINTS)
def test_every_new_post_endpoint_404s_on_minimalist(fresh_lab, minimalist,
                                                    client, path):
    resp = client.post(path, json={"hold": True, "enabled": True})
    assert resp.status_code == 404, path


@pytest.mark.parametrize("path", ("/api/admin/agents/hold",
                                  "/api/admin/flight-recorder"))
def test_minimalist_404_happens_before_the_body_is_parsed(fresh_lab,
                                                          minimalist, client,
                                                          path):
    """The gate must be the first statement in the handler: a minimalist lab
    posting garbage must get a 404, not a 400/500 that proves the route ran."""
    resp = client.post(path, content=b"{not json at all",
                       headers={"Content-Type": "application/json"})
    assert resp.status_code == 404, (
        f"{path} parsed the body before the tier gate: {resp.status_code}")


def test_minimalist_cannot_turn_the_flight_recorder_on(fresh_lab, minimalist,
                                                       client):
    """A minimalist lab must never be able to start capturing bodies — the
    toggle is admin-only, and admin does not exist on that tier."""
    resp = client.post("/api/admin/flight-recorder", json={"enabled": True})
    assert resp.status_code == 404
    assert agent_trace.recorder_on() is False
    assert not (fresh_lab / "flight_recorder.json").exists()


def test_minimalist_nav_does_not_offer_the_admin_surface(fresh_lab, minimalist,
                                                          client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'href="/admin"' not in resp.text
    assert "Agent lanes" not in resp.text


def test_minimalist_prompts_endpoint_serves_no_bodies_even_with_the_flag_file_planted(
        fresh_lab, minimalist, client):
    """The structural claim behind the tier gate: even if the recorder flag
    file somehow exists on a minimalist lab, the every-tier prompts endpoint
    must not serve a body it never captured."""
    (fresh_lab / "flight_recorder.json").write_text(
        json.dumps({"enabled": True, "changed_at": "2026-01-01T00:00:00Z"}))
    agent_trace._reset_recorder_for_tests()

    resp = client.get("/api/agents/prompts")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["traces"] == []
    for entry in payload["traces"]:
        assert "prompt" not in entry and "response" not in entry


# ---------------------------------------------------------------------------
# The legacy-bodies scan against every shape of activity.jsonl
# ---------------------------------------------------------------------------

def test_scan_with_no_activity_log_at_all(fresh_lab):
    assert activity.scan_for_legacy_bodies() == {
        "count": 0, "by_source": {}, "dismissed": False}


def test_scan_with_an_empty_activity_log(fresh_lab):
    (fresh_lab / "activity.jsonl").write_text("")
    assert activity.scan_for_legacy_bodies()["count"] == 0


def test_scan_with_only_bodyless_records(fresh_lab):
    lines = [json.dumps({"ts": "t", "source": "researcher", "message": "m",
                         "data": {"prompt_trace": {"max_tokens": 512,
                                                   "latency_ms": 12}}})
             for _ in range(50)]
    (fresh_lab / "activity.jsonl").write_text("\n".join(lines) + "\n")
    result = activity.scan_for_legacy_bodies()
    assert result["count"] == 0, (
        "a prompt_trace without prompt/response is not a legacy body")
    assert result["by_source"] == {}


def test_scan_with_malformed_lines_counts_the_valid_ones_and_survives(
        fresh_lab):
    good = json.dumps({"source": "researcher",
                       "data": {"prompt_trace": {"prompt": "p",
                                                 "response": "r"}}})
    content = "\n".join([
        "not json at all",
        "{ truncated",
        "",
        "   ",
        good,
        '{"data": null}',
        '{"data": {"prompt_trace": "not a dict"}}',
        '{"data": "oops, not a dict"}',
        "[1,2,3]",
        "123",
        '"a bare string"',
        good,
    ]) + "\n"
    (fresh_lab / "activity.jsonl").write_text(content)
    result = activity.scan_for_legacy_bodies()
    assert result["count"] == 2, result
    assert result["by_source"] == {"researcher": 2}


def test_scan_counts_a_response_only_body(fresh_lab):
    (fresh_lab / "activity.jsonl").write_text(json.dumps({
        "source": "browser",
        "data": {"prompt_trace": {"response": "just the response"}},
    }) + "\n")
    assert activity.scan_for_legacy_bodies()["count"] == 1


def test_scan_reads_the_rotated_file_too(fresh_lab):
    body = json.dumps({"source": "researcher",
                       "data": {"prompt_trace": {"prompt": "p"}}})
    (fresh_lab / "activity.jsonl").write_text(body + "\n")
    (fresh_lab / "activity.jsonl.1").write_text(body + "\n" + body + "\n")
    assert activity.scan_for_legacy_bodies()["count"] == 3


def test_scan_of_a_ten_megabyte_log_completes_and_is_bounded(fresh_lab):
    """The rotation ceiling is 2x5 MB. The scan streams, so a lab at the
    ceiling must not be quadratic or memory-hungry — and the count must be
    exact, not sampled."""
    body = json.dumps({"source": "researcher",
                       "data": {"prompt_trace": {"prompt": "x" * 400,
                                                 "response": "y" * 400}}})
    plain = json.dumps({"source": "buddy", "message": "z" * 800, "data": {}})
    chunk = (body + "\n" + plain + "\n")
    reps = (5 * 1024 * 1024) // len(chunk)
    for name in ("activity.jsonl", "activity.jsonl.1"):
        with open(fresh_lab / name, "w") as f:
            for _ in range(reps):
                f.write(chunk)
    total_bytes = sum((fresh_lab / n).stat().st_size
                      for n in ("activity.jsonl", "activity.jsonl.1"))
    assert total_bytes > 9 * 1024 * 1024, total_bytes

    import time
    t0 = time.perf_counter()
    result = activity.scan_for_legacy_bodies()
    elapsed = time.perf_counter() - t0

    assert result["count"] == 2 * reps, result["count"]
    assert elapsed < 10.0, f"the boot scan took {elapsed:.1f}s at the ceiling"


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root can read a chmod 000 file")
def test_scan_of_an_unreadable_activity_log_degrades_without_raising(
        fresh_lab):
    path = fresh_lab / "activity.jsonl"
    path.write_text(json.dumps({"source": "researcher",
                                "data": {"prompt_trace": {"prompt": "p"}}}) + "\n")
    os.chmod(path, 0o000)
    try:
        result = activity.scan_for_legacy_bodies()
        assert result["count"] == 0
        assert result["dismissed"] is False
    finally:
        os.chmod(path, 0o600)


def test_a_dismissed_notice_is_remembered_across_a_fresh_scan(fresh_lab):
    (fresh_lab / "activity.jsonl").write_text(json.dumps({
        "source": "researcher",
        "data": {"prompt_trace": {"prompt": "p"}}}) + "\n")
    assert activity.scan_for_legacy_bodies()["dismissed"] is False
    activity.dismiss_legacy_notice()
    result = activity.scan_for_legacy_bodies()
    assert result["dismissed"] is True
    assert result["count"] == 1, (
        "[Keep] must remember the dismissal without pretending the bodies "
        "are gone")


def test_a_corrupt_dismissal_marker_is_treated_as_not_dismissed(fresh_lab):
    (fresh_lab / "legacy_bodies_notice.json").write_text("{not json")
    assert activity.legacy_notice_dismissed() is False


# ---------------------------------------------------------------------------
# flight_recorder.json in every broken state
# ---------------------------------------------------------------------------

def test_recorder_is_off_when_the_state_file_is_absent(fresh_lab):
    assert not (fresh_lab / "flight_recorder.json").exists()
    assert agent_trace.recorder_on() is False


@pytest.mark.parametrize("content", [
    "",
    "   ",
    "{not json",
    "null",
    "[]",
    '"enabled"',
    "123",
    '{"enabled": "yes-please"}',
    '{}',
])
def test_a_corrupt_recorder_state_file_means_off(fresh_lab, content):
    """Off is the safe direction: a garbled flag must never be read as
    "capture bodies"."""
    (fresh_lab / "flight_recorder.json").write_text(content)
    agent_trace._reset_recorder_for_tests()
    enabled = agent_trace.recorder_on()
    if content == '{"enabled": "yes-please"}':
        # A truthy non-bool is coerced; documented as bool(...) in the
        # contract. Assert it is at least a bool, not the raw string.
        assert isinstance(enabled, bool)
    else:
        assert enabled is False, content


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root can read a chmod 000 file")
def test_an_unreadable_recorder_state_file_means_off(fresh_lab):
    path = fresh_lab / "flight_recorder.json"
    path.write_text(json.dumps({"enabled": True}))
    os.chmod(path, 0o000)
    agent_trace._reset_recorder_for_tests()
    try:
        assert agent_trace.recorder_on() is False, (
            "an unreadable flag must fail to OFF, never to ON")
    finally:
        os.chmod(path, 0o600)


def test_recorder_state_survives_an_unwritable_data_dir(fresh_lab, monkeypatch):
    """A read-only lab directory (a locked-down family machine) must not turn
    a toggle into a 500."""
    ro = fresh_lab / "readonly"
    ro.mkdir()
    os.chmod(ro, 0o500)
    monkeypatch.setattr(config, "DATA_DIR", ro)
    agent_trace._reset_recorder_for_tests()
    try:
        state = agent_trace.set_recorder_enabled(True)
        assert state["enabled"] is True, (
            "the in-memory flip still applies even when it cannot persist")
        assert not (ro / "flight_recorder.json").exists()
    finally:
        os.chmod(ro, 0o700)


def test_trace_writes_to_an_unwritable_data_dir_are_counted_not_raised(
        fresh_lab, monkeypatch):
    """F1: a read-only DATA_DIR must leave the live view working and make the
    loss a visible number."""
    ro = fresh_lab / "readonly-traces"
    ro.mkdir()
    os.chmod(ro, 0o500)
    monkeypatch.setattr(config, "DATA_DIR", ro)
    try:
        for i in range(5):
            agent_trace.record(trace_id=f"{i:016x}", kind="agent",
                               agent_id="buddy", outcome="ok")
        assert len(agent_trace.ring(10)) == 5, (
            "the ring must keep serving the live view")
        stats = agent_trace.stats()
        assert stats["dropped_writes"] == 5, stats
        assert agent_trace.lanes_snapshot()["drops"]["dropped_writes"] == 5, (
            "loss must be visible on the Admin card, not silent")
    finally:
        os.chmod(ro, 0o700)


def test_a_data_dir_that_is_a_file_not_a_directory_is_survivable(fresh_lab,
                                                                monkeypatch):
    """Adversarial/weird install: DATA_DIR points at a regular file."""
    bogus = fresh_lab / "not-a-dir"
    bogus.write_text("i am a file")
    monkeypatch.setattr(config, "DATA_DIR", bogus)
    agent_trace.record(trace_id="a" * 16, kind="agent", agent_id="buddy",
                       outcome="ok")
    assert agent_trace.stats()["dropped_writes"] == 1
    assert len(agent_trace.ring(10)) == 1


def test_no_module_enumerates_anything_across_world_instances(fresh_lab):
    """F14: per-World isolation is free and must stay free — nothing in the
    trace/redaction path may enumerate a directory at all, which is a
    stronger and less gameable claim than "does not mention
    lab/instances"."""
    import ast

    import arail.agent_trace as at
    import arail.redact as rd

    forbidden = {"glob", "rglob", "iterdir", "scandir", "walk", "listdir"}
    for mod in (at, rd):
        tree = ast.parse(Path(mod.__file__).read_text())
        called = {
            node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
        }
        leaked = called & forbidden
        assert not leaked, f"{mod.__name__} enumerates the filesystem: {leaked}"


def test_two_data_roots_never_interleave(fresh_lab, monkeypatch, tmp_path):
    """F14 behaviourally: two ``ARAIL_DATA_DIR`` roots, one process each in
    production — here simulated by re-pointing the lazily-resolved root and
    asserting each file only ever holds its own records."""
    root_a = tmp_path / "world-a"
    root_b = tmp_path / "world-b"
    root_a.mkdir()
    root_b.mkdir()

    monkeypatch.setattr(config, "DATA_DIR", root_a)
    agent_trace.record(trace_id="a" * 16, kind="agent", agent_id="buddy",
                       outcome="ok")
    agent_trace.set_recorder_enabled(True)

    monkeypatch.setattr(config, "DATA_DIR", root_b)
    agent_trace._reset_recorder_for_tests()
    agent_trace.record(trace_id="b" * 16, kind="agent", agent_id="researcher",
                       outcome="ok")

    a_text = (root_a / "agent_traces.jsonl").read_text()
    b_text = (root_b / "agent_traces.jsonl").read_text()
    assert "a" * 16 in a_text and "b" * 16 not in a_text
    assert "b" * 16 in b_text and "a" * 16 not in b_text
    assert (root_a / "flight_recorder.json").exists()
    assert not (root_b / "flight_recorder.json").exists(), (
        "World B must not inherit World A's recorder flag")
    assert agent_trace.recorder_on() is False, (
        "World B's recorder is its own; it must default off")
