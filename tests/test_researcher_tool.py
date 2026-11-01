"""Unit tests for arail.mcp.researcher_tool (T-U-5, T-U-6, T-U-9 subset)."""
from __future__ import annotations

import asyncio
import subprocess
import sys

import pytest

from arail.mcp import researcher_tool as rt


@pytest.mark.parametrize("goal", ["", "   ", None, 5, ["x"], "x" * 2001])
def test_invalid_goal_rejected(goal):
    assert rt.validate_goal(goal) == "invalid_goal"


def test_valid_goal_accepted():
    assert rt.validate_goal("x" * 2000) is None


def test_run_once_invalid_goal_returns_error_code():
    assert asyncio.run(rt.run_once("")) == {"error": "invalid_goal"}


def test_wrong_tier(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "minimalist")
    assert asyncio.run(rt.run_once("measure it")) == {"error": "wrong_tier"}


def test_busy_refuses_without_touching_goal_store(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    monkeypatch.setattr(rt, "researcher_is_busy", lambda: True)
    from arail.agents.researcher import researcher
    before = researcher.goal_store.get_current()
    assert asyncio.run(rt.run_once("measure it")) == {"error": "researcher_busy"}
    assert researcher.goal_store.get_current() == before


@pytest.fixture()
def lockdir(tmp_path, monkeypatch):
    monkeypatch.setattr(rt, "lock_path", lambda: tmp_path / "mastra" / "run-once.lock")
    return tmp_path


def test_lock_is_exclusive_and_released(lockdir):
    with rt.exclusive_run_lock() as a:
        with rt.exclusive_run_lock() as b:
            assert a is True and b is False
        assert rt.lock_path().exists()          # a refused caller never deletes the holder's lock
    with rt.exclusive_run_lock() as c:
        assert c is True


def test_lock_from_killed_holder_is_not_stale(lockdir):
    code = ("import sys; from arail.mcp import researcher_tool as rt; import pathlib;"
            "rt.lock_path = lambda: pathlib.Path(sys.argv[1]);"
            "cm = rt.exclusive_run_lock(); cm.__enter__(); print('held', flush=True); import time; time.sleep(60)")
    p = subprocess.Popen([sys.executable, "-c", code, str(rt.lock_path())], stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "held"
        with rt.exclusive_run_lock() as live:
            assert live is False                # live holder keeps it
        p.kill(); p.wait()
        with rt.exclusive_run_lock() as dead:
            assert dead is True                 # dead holder's lock is simply free; nothing was deleted
    finally:
        p.kill()


def test_two_concurrent_run_once_exactly_one_proceeds(lockdir, monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    monkeypatch.setattr(rt, "researcher_is_busy", lambda: False)
    monkeypatch.setattr(rt, "owner_goal_in_progress", lambda: False)
    entered = []

    async def fake_locked(goal):
        entered.append(goal)
        await asyncio.sleep(0.3)
        return {"report": "r", "experiment_id": "e", "trace_id": "t", "steps": []}

    monkeypatch.setattr(rt, "_run_locked", fake_locked)

    async def both():
        return await asyncio.gather(rt.run_once("goal one"), rt.run_once("goal two"))

    results = asyncio.run(both())
    assert len(entered) == 1
    assert sorted("error" in r for r in results) == [False, True]
    assert {"error": "researcher_busy"} in results


def test_lock_released_after_failure(lockdir, monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    monkeypatch.setattr(rt, "researcher_is_busy", lambda: False)
    monkeypatch.setattr(rt, "owner_goal_in_progress", lambda: False)

    async def boom(goal):
        raise RuntimeError("x")

    monkeypatch.setattr(rt, "_run_locked", boom)
    with pytest.raises(RuntimeError):
        asyncio.run(rt.run_once("g"))
    with rt.exclusive_run_lock() as got:
        assert got is True


def test_unfinished_owner_goal_is_never_displaced(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    monkeypatch.setattr(rt, "researcher_is_busy", lambda: False)
    monkeypatch.setattr(rt, "owner_goal_in_progress", lambda: True)
    assert asyncio.run(rt.run_once("measure it")) == {"error": "goal_in_progress"}


def test_owner_goal_in_progress_rules(monkeypatch):
    from arail.agents.researcher import researcher
    for cur, want in [({}, False), ({"status": "active", "progress": 0.4}, True),
                      ({"status": "active", "progress": 1.0}, False),
                      ({"status": "archived", "progress": 0.2}, False)]:
        monkeypatch.setattr(researcher.goal_store, "get_current", lambda c=cur: c or None)
        assert rt.owner_goal_in_progress() is want


def test_build_steps_maps_and_caps():
    recs = [{"ts": 100.0 + i, "latency_ms": 500, "label": "researcher", "model": "m",
             "outcome": "ok", "tokens_in": 1, "tokens_out": 2} for i in range(250)]
    events = [
        {"ts": "2026-10-21T00:00:00+00:00", "message": "Generated 3 research hypotheses."},
        {"ts": "2026-10-21T00:00:01+00:00", "message": "Created experiment ab12: x"},
        {"ts": "2026-10-21T00:00:02+00:00", "message": "unrelated chatter"},
    ]
    steps, truncated = rt.build_steps(recs, events)
    assert truncated and len(steps) == rt.MAX_STEPS
    small, trunc2 = rt.build_steps(recs[:1], events)
    kinds = {s["kind"] for s in small}
    assert kinds == {"model_call", "planning", "experiment"} and not trunc2
    assert all(s["input_summary"] and s["output_summary"] for s in small)
    assert all(len(s["output_summary"]) <= rt.SUMMARY_CHARS for s in small)


def test_stdio_server_writes_only_mcp_frames_on_stdout():
    """T-U-9: initialize over stdio; every stdout line must be a JSON-RPC frame."""
    import json
    init = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2024-11-05", "capabilities": {},
        "clientInfo": {"name": "t", "version": "0"}}}) + "\n"
    p = subprocess.run([sys.executable, "-m", "arail.mcp.researcher_tool"], input=init,
                       capture_output=True, text=True, timeout=60)
    lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
    assert lines, p.stderr[-500:]
    for ln in lines:
        assert json.loads(ln)["jsonrpc"] == "2.0"
