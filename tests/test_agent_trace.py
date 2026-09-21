"""Unit tests for arail.agent_trace — the bounded trace store.

Covers: record() never raises with DATA_DIR read-only (F1); ring bound
honoured; dropped_writes increments and is exposed; rotation at 5MB
produces exactly two files (F2); schema string present on every record;
every documented key present with null rather than absent; per-instance
isolation (F14) — no module constant, two DATA_DIR roots stay separate.
"""

from __future__ import annotations

import json
import os
import stat

import pytest

from arail import agent_trace, config


@pytest.fixture(autouse=True)
def _clean_trace_store():
    agent_trace._reset_for_tests()
    yield
    agent_trace._reset_for_tests()


# ---------------------------------------------------------------------------
# Schema + field completeness
# ---------------------------------------------------------------------------

def test_record_has_schema_string():
    agent_trace.record(trace_id="a" * 16, kind="agent", agent_id="buddy")
    rec = agent_trace.ring(1)[0]
    assert rec["schema"] == agent_trace.SCHEMA


def test_every_documented_key_present_as_null_when_absent():
    agent_trace.record(trace_id="a" * 16)
    rec = agent_trace.ring(1)[0]
    for key in agent_trace._FIELDS:
        assert key in rec, f"missing key {key!r}"
    # Fields never supplied are null, not 0/""/"n/a".
    assert rec["tokens_out"] is None
    assert rec["model"] is None
    assert rec["ttft_status"] is None


def test_unknown_kwargs_are_dropped_not_stored():
    agent_trace.record(trace_id="a" * 16, made_up_field="should not appear")
    rec = agent_trace.ring(1)[0]
    assert "made_up_field" not in rec


# ---------------------------------------------------------------------------
# Ring bound
# ---------------------------------------------------------------------------

def test_ring_honours_maxlen(monkeypatch):
    monkeypatch.setenv("ARAIL_TRACE_RING", "50")
    for i in range(120):
        agent_trace.record(trace_id=f"{i:016x}")
    assert len(agent_trace.ring(1000)) == 50
    # Last 50 minted (indices 70-119), oldest-first.
    assert agent_trace.ring(1000)[0]["trace_id"] == f"{70:016x}"
    assert agent_trace.ring(1000)[-1]["trace_id"] == f"{119:016x}"


def test_ring_size_clamped(monkeypatch):
    monkeypatch.setenv("ARAIL_TRACE_RING", "1")
    assert agent_trace._ring_maxlen() == 50   # clamp floor
    monkeypatch.setenv("ARAIL_TRACE_RING", "999999")
    assert agent_trace._ring_maxlen() == 5000  # clamp ceiling


def test_ring_malformed_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("ARAIL_TRACE_RING", "not-a-number")
    assert agent_trace._ring_maxlen() == 500


# ---------------------------------------------------------------------------
# F1 — disk write failure never raises, ring keeps serving
# ---------------------------------------------------------------------------

def test_record_never_raises_with_data_dir_read_only(monkeypatch, tmp_path):
    ro_dir = tmp_path / "ro"
    ro_dir.mkdir()
    ro_dir.chmod(stat.S_IREAD | stat.S_IEXEC)
    monkeypatch.setattr(config, "DATA_DIR", ro_dir)
    try:
        agent_trace.record(trace_id="a" * 16, agent_id="buddy", kind="agent")
        agent_trace.record(trace_id="b" * 16, agent_id="buddy", kind="agent")
    finally:
        ro_dir.chmod(stat.S_IRWXU)  # restore so tmp_path cleanup can remove it

    # The ring still served both records — a disk failure never costs the
    # live view.
    assert len(agent_trace.ring(10)) == 2
    assert agent_trace.stats()["dropped_writes"] >= 1


def test_dropped_writes_surfaced_in_stats(monkeypatch, tmp_path):
    ro_dir = tmp_path / "ro2"
    ro_dir.mkdir()
    ro_dir.chmod(stat.S_IREAD | stat.S_IEXEC)
    monkeypatch.setattr(config, "DATA_DIR", ro_dir)
    try:
        agent_trace.record(trace_id="c" * 16)
    finally:
        ro_dir.chmod(stat.S_IRWXU)
    assert agent_trace.stats()["dropped_writes"] == 1


# ---------------------------------------------------------------------------
# F2 — rotation at 5MB produces exactly two files
# ---------------------------------------------------------------------------

def test_rotation_at_5mb(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    path = tmp_path / "agent_traces.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write(("{\"pad\":\"" + "y" * 1024 + "\"}\n") * 6000)  # > 5MB
    assert path.stat().st_size > 5 * 1024 * 1024

    agent_trace._write_count = 63  # tip the modulo-64 rotation check
    agent_trace.record(trace_id="d" * 16, agent_id="buddy", kind="agent")

    rotated = path.with_suffix(".jsonl.1")
    assert rotated.exists()
    assert path.exists()
    assert path.stat().st_size < 5 * 1024 * 1024


# ---------------------------------------------------------------------------
# F14 — per-instance isolation: DATA_DIR resolved lazily, two roots stay put
# ---------------------------------------------------------------------------

def test_two_data_dirs_never_interleave(monkeypatch, tmp_path):
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()

    monkeypatch.setattr(config, "DATA_DIR", root_a)
    agent_trace.record(trace_id="e" * 16, agent_id="alpha", kind="agent")

    monkeypatch.setattr(config, "DATA_DIR", root_b)
    agent_trace.record(trace_id="f" * 16, agent_id="beta", kind="agent")

    a_lines = (root_a / "agent_traces.jsonl").read_text().strip().splitlines()
    b_lines = (root_b / "agent_traces.jsonl").read_text().strip().splitlines()
    assert len(a_lines) == 1
    assert len(b_lines) == 1
    assert json.loads(a_lines[0])["agent_id"] == "alpha"
    assert json.loads(b_lines[0])["agent_id"] == "beta"


def test_agent_trace_never_globs_lab_instances():
    """Nothing in this module enumerates lab/instances/ or aggregates
    across roots (VISION note 10) -- checked as an absence of any glob/
    directory-walk call, not a substring ban (the module's own docstring
    legitimately *names* lab/instances/ while documenting the invariant)."""
    import pathlib
    src = pathlib.Path(agent_trace.__file__).read_text()
    for forbidden in ("glob(", "rglob(", "os.walk(", "iterdir("):
        assert forbidden not in src, f"found {forbidden!r} in agent_trace.py"


# ---------------------------------------------------------------------------
# ARAIL_TRACE_PERSIST=0 keeps the ring, disables disk
# ---------------------------------------------------------------------------

def test_persist_disabled_keeps_ring_no_disk(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("ARAIL_TRACE_PERSIST", "0")
    agent_trace.record(trace_id="g" * 16, agent_id="buddy", kind="agent")
    assert len(agent_trace.ring(10)) == 1
    assert not (tmp_path / "agent_traces.jsonl").exists()


# ---------------------------------------------------------------------------
# lanes_snapshot() — roster + unattributed + empty states
# ---------------------------------------------------------------------------

def test_lanes_snapshot_schema():
    snap = agent_trace.lanes_snapshot()
    assert snap["schema"] == "arail.agent_lanes/v1"
    assert isinstance(snap["lanes"], list)


def test_lanes_snapshot_includes_every_fixed_lane_even_with_no_calls():
    snap = agent_trace.lanes_snapshot()
    ids = {lane["id"] for lane in snap["lanes"]}
    for agent_id, _ in agent_trace.FIXED_LANES:
        assert agent_id in ids


def test_lanes_snapshot_empty_reason_for_model_free_lanes():
    snap = agent_trace.lanes_snapshot()
    by_id = {lane["id"]: lane for lane in snap["lanes"]}
    assert by_id["sre"]["empty_reason"] == "does not call a model"
    assert by_id["presence"]["empty_reason"] == "does not call a model"
    # Confirmed during S2's wiring pass (not assumed): curator has zero
    # router references; forge is a code generator whose own top-level code
    # never calls a model (the *generated* agent inherits attribution via
    # L1 once deployed, under its own id, never under "forge").
    assert by_id["curator"]["empty_reason"] == "does not call a model"
    assert by_id["forge"]["empty_reason"] == "does not call a model"


def test_lanes_snapshot_generic_empty_reason_for_others():
    snap = agent_trace.lanes_snapshot()
    by_id = {lane["id"]: lane for lane in snap["lanes"]}
    assert by_id["buddy"]["empty_reason"] == "no calls yet this session"


def test_lanes_snapshot_reflects_a_real_call():
    agent_trace.record(trace_id="h" * 16, agent_id="buddy", kind="agent",
                       attribution="agent:buddy", brain="deep",
                       ttft_ms=412.0, ttft_status="measured", tokens_out=1180)
    snap = agent_trace.lanes_snapshot()
    by_id = {lane["id"]: lane for lane in snap["lanes"]}
    assert by_id["buddy"]["calls"] == 1
    assert by_id["buddy"]["empty_reason"] is None
    assert by_id["buddy"]["brain"] == "deep"
    assert by_id["buddy"]["ttft_ms"] == 412.0


def test_lanes_snapshot_unattributed_lane_never_hidden():
    agent_trace.record(trace_id="i" * 16, attribution="unattributed",
                       call_site="arail.dictionary:393")
    snap = agent_trace.lanes_snapshot()
    assert snap["unattributed"]["calls"] == 1
    assert snap["unattributed"]["call_sites"][0]["call_site"] == "arail.dictionary:393"


def test_lanes_snapshot_drops_and_window_present():
    snap = agent_trace.lanes_snapshot()
    assert "dropped_writes" in snap["drops"]
    assert "ring_size" in snap["window"]
    assert "recorded" in snap["window"]


# ---------------------------------------------------------------------------
# stats()
# ---------------------------------------------------------------------------

def test_stats_shape():
    s = agent_trace.stats()
    assert set(s.keys()) == {"recorded", "dropped_writes", "overlap_pct"}


def test_overlap_pct_from_agent_calls_with_slot():
    agent_trace.record(trace_id="j" * 16, kind="agent", agent_id="buddy",
                       slot={"held_by_other": True})
    agent_trace.record(trace_id="k" * 16, kind="agent", agent_id="buddy",
                       slot={"held_by_other": False})
    assert agent_trace.stats()["overlap_pct"] == 50.0


def test_overlap_pct_ignores_system_and_unattributed():
    agent_trace.record(trace_id="l" * 16, kind="system", label="dictionary",
                       slot={"held_by_other": True})
    assert agent_trace.stats()["overlap_pct"] == 0.0


# ---------------------------------------------------------------------------
# subscribe() — SSE push, no polling (structural half of W1's "within 2s")
# ---------------------------------------------------------------------------

def test_subscribe_receives_a_record_pushed_before_record_returns():
    import asyncio

    async def _scenario():
        gen = agent_trace.subscribe()
        first = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0)  # let subscribe() register itself
        agent_trace.record(trace_id="m" * 16, agent_id="buddy", kind="agent")
        rec = await asyncio.wait_for(first, timeout=1.0)
        assert rec["agent_id"] == "buddy"
        await gen.aclose()

    asyncio.run(_scenario())


def test_f18_burst_of_traces_leaves_activity_log_untouched(monkeypatch, tmp_path):
    """F18: the trace store is a separate persistence path from
    activity.jsonl — nothing new is written there, so a burst of agent
    traces cannot evict the operator's activity history out of its own
    200-event ring."""
    from arail import activity
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(activity, "LOG_FILE", tmp_path / "activity.jsonl")
    activity.ActivityLog._instance = None
    log = activity.ActivityLog()
    for i in range(250):
        log.emit("test", f"event-{i}")
    before = log.recent(200)

    for i in range(1000):
        agent_trace.record(trace_id=f"{i:016x}", agent_id="buddy", kind="agent")

    after = log.recent(200)
    assert after == before
    assert len(after) == 200
    activity.ActivityLog._instance = None


def test_subscribe_receives_from_a_foreign_thread():
    """record() called from a to_thread worker must still wake a subscriber
    on the event loop via call_soon_threadsafe (the activity.py idiom)."""
    import asyncio

    async def _scenario():
        gen = agent_trace.subscribe()
        first = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0)
        await asyncio.to_thread(
            agent_trace.record, trace_id="n" * 16, agent_id="researcher",
            kind="agent",
        )
        rec = await asyncio.wait_for(first, timeout=1.0)
        assert rec["agent_id"] == "researcher"
        await gen.aclose()

    asyncio.run(_scenario())
