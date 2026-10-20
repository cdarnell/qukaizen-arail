"""A3 — the small ARAIL plumbing the Buddy edge relies on.

ARCHITECTURE.md §5.11: `scheduler.halt_load_failed()` (the edge must be able to
tell "not held" from "could not read the hold file"), `inference_slot`'s
per-call acquire timeout, the presence-meter skip for machine traffic, the
fast-path metric prefixes, and the public `is_cloud_entry`.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from arail import config, runtime_profile, scheduler
from arail.portal import scheduler as inference
from arail.portal.app import app


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    scheduler._reset_halt_for_tests()
    runtime_profile._reset_for_tests() if hasattr(runtime_profile, "_reset_for_tests") else None
    yield
    scheduler._reset_halt_for_tests()


def _fresh_load(tmp_path, text=None):
    """Make the next jobs_halted()/halt_load_failed() read halt.json from disk."""
    path = scheduler._halt_path()  # conftest points this at a per-test directory
    if text is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(text)
    scheduler._halt_loaded = False
    scheduler._halted = False
    scheduler._halt_load_failed = False
    return path


# ---------------------------------------------------------------------------
# halt_load_failed
# ---------------------------------------------------------------------------

def test_no_halt_file_is_not_a_load_failure(tmp_path):
    _fresh_load(tmp_path)
    assert scheduler.jobs_halted() is False
    assert scheduler.halt_load_failed() is False


def test_a_valid_halt_file_is_not_a_load_failure(tmp_path):
    _fresh_load(tmp_path, '{"halted": true, "changed_at": "2026-10-20T14:00:00"}')
    assert scheduler.jobs_halted() is True
    assert scheduler.halt_load_failed() is False


@pytest.mark.parametrize("text", ["{not json", "", "[1, 2]", '"held"', "null", "\x00\x01"])
def test_a_corrupt_halt_file_loads_as_not_held_but_is_flagged(tmp_path, text):
    """ARAIL's own behaviour is unchanged (debt D10): corrupt reads as not held.
    The edge reads the flag and refuses instead (F9)."""
    _fresh_load(tmp_path, text)
    assert scheduler.jobs_halted() is False
    assert scheduler.halt_load_failed() is True


def test_the_flag_clears_on_the_next_successful_write(tmp_path):
    _fresh_load(tmp_path, "{broken")
    assert scheduler.halt_load_failed() is True
    scheduler.halt_all_jobs()
    assert scheduler.halt_load_failed() is False
    assert scheduler.jobs_halted() is True
    path = _fresh_load(tmp_path, "{broken")
    scheduler.resume_all_jobs()
    assert scheduler.halt_load_failed() is False
    assert not path.exists()


def test_the_flag_stays_if_the_write_cannot_happen(tmp_path, monkeypatch):
    _fresh_load(tmp_path, "{broken")
    assert scheduler.halt_load_failed() is True
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a directory should be")
    original = scheduler._halt_path
    monkeypatch.setattr(scheduler, "_halt_path", lambda: blocker / "halt.json")
    try:
        scheduler.halt_all_jobs()
        assert scheduler.jobs_halted() is True       # in-memory state still applies
        assert scheduler.halt_load_failed() is True  # nothing good was written
    finally:
        monkeypatch.setattr(scheduler, "_halt_path", original)


def test_reset_for_tests_clears_the_flag(tmp_path):
    _fresh_load(tmp_path, "{broken")
    assert scheduler.halt_load_failed() is True
    scheduler._reset_halt_for_tests()
    assert scheduler.halt_load_failed() is False


# ---------------------------------------------------------------------------
# inference_slot(acquire_timeout_s=...)
# ---------------------------------------------------------------------------

@pytest.fixture
def slot_env(monkeypatch):
    monkeypatch.setenv("ARAIL_INFERENCE_CONCURRENCY", "1")
    monkeypatch.setattr(inference, "_SEM", None)
    yield


def _hold_then_wait(**kw):
    async def run():
        async with inference.inference_slot("holder"):
            t0 = time.perf_counter()
            with pytest.raises(inference.InferenceSlotTimeout):
                async with inference.inference_slot("buddy-edge", **kw):
                    pass
            return time.perf_counter() - t0
    return asyncio.run(run())


def test_a_per_call_timeout_shortens_the_wait(slot_env):
    waited = _hold_then_wait(acquire_timeout_s=0.1)
    assert 0.08 <= waited < 1.0


def test_none_keeps_the_global_timeout(slot_env, monkeypatch):
    monkeypatch.setenv("ARAIL_INFERENCE_SLOT_TIMEOUT_S", "0.15")
    waited = _hold_then_wait(acquire_timeout_s=None)
    assert 0.1 <= waited < 1.0


def test_omitting_the_argument_is_the_same_as_none(slot_env, monkeypatch):
    monkeypatch.setenv("ARAIL_INFERENCE_SLOT_TIMEOUT_S", "0.15")
    assert 0.1 <= _hold_then_wait() < 1.0


@pytest.mark.parametrize("bad", [0, -1, float("nan"), "x"])
def test_a_bad_per_call_timeout_falls_back_to_the_global_one(slot_env, monkeypatch, bad):
    monkeypatch.setenv("ARAIL_INFERENCE_SLOT_TIMEOUT_S", "0.15")
    assert 0.1 <= _hold_then_wait(acquire_timeout_s=bad) < 1.0


def test_a_free_slot_is_acquired_immediately_and_released(slot_env):
    async def run():
        async with inference.inference_slot("buddy-edge", acquire_timeout_s=0.5):
            assert inference.snapshot()["in_flight"] == 1
        return inference.snapshot()["in_flight"]
    assert asyncio.run(run()) == 0


def test_the_timeout_argument_is_keyword_only():
    with pytest.raises(TypeError):
        inference.inference_slot("x", 1.0)


# ---------------------------------------------------------------------------
# presence skip, fast-path prefixes, is_cloud_entry
# ---------------------------------------------------------------------------

def test_edge_traffic_is_not_operator_presence(monkeypatch):
    """F22: a 20-second handshake would otherwise pin the lab in the interactive
    profile for ever and starve background deep work."""
    runtime_profile._last_presence_ts = 0.0
    client = TestClient(app)
    client.get("/api/host/v1/handshake")
    client.post("/api/host/v1/handshake", json={})
    client.get("/api/host/v1/brain/models")
    client.post("/api/host/v1/trace/record", json={})
    assert runtime_profile._last_presence_ts == 0.0


def test_other_traffic_still_counts_as_presence():
    runtime_profile._last_presence_ts = 0.0
    TestClient(app).get("/api/agents/status")
    assert runtime_profile._last_presence_ts > 0.0


def test_fast_path_prefixes_name_the_four_non_brain_edge_paths_and_not_brain():
    prefixes = inference.FAST_PATH_PREFIXES
    for p in ("/api/host/v1/handshake", "/api/host/v1/trace/", "/api/host/v1/hold_all", "/api/host/v1/pause_research"):
        assert p in prefixes
    assert not any("/api/host/v1/brain" in p for p in prefixes)
    assert "/api/host/v1/" not in prefixes  # brain.chat is long-lived and must not be timed


def test_is_cloud_entry_is_public_and_is_the_same_function():
    from arail.registry import core
    assert core.is_cloud_entry is core._is_cloud_entry
