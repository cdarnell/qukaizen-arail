"""The halt flag must survive a portal restart — a halted lab stays halted."""

from __future__ import annotations

import json

from arail import scheduler


def _simulate_restart() -> None:
    """New process ≈ module state reset with the file left in place."""
    with scheduler._halt_lock:
        scheduler._halted = False
        scheduler._halt_loaded = False
        scheduler._halt_changed_at = None


def test_halt_survives_restart():
    assert scheduler.jobs_halted() is False
    scheduler.halt_all_jobs()
    assert scheduler.jobs_halted() is True

    _simulate_restart()
    assert scheduler.jobs_halted() is True     # reloaded from halt.json


def test_resume_survives_restart():
    scheduler.halt_all_jobs()
    scheduler.resume_all_jobs()
    _simulate_restart()
    assert scheduler.jobs_halted() is False
    assert not scheduler._halt_path().exists()  # resumed = file removed


def test_halt_file_shape():
    scheduler.halt_all_jobs()
    data = json.loads(scheduler._halt_path().read_text())
    assert data["halted"] is True
    assert "changed_at" in data


def test_corrupt_halt_file_fails_open():
    scheduler._halt_path().write_text("{not json")
    _simulate_restart()
    # Corrupt file → not halted (fail open: never brick the lab), no raise.
    assert scheduler.jobs_halted() is False


def test_halt_changed_at_survives_restart():
    """agent_context.hold_state()'s "changed_at" (sprint 2026-09-20-
    buddy-front-and-center) reads scheduler.halt_changed_at(), which must
    survive a restart the same way the halted flag itself does."""
    assert scheduler.halt_changed_at() is None
    scheduler.halt_all_jobs()
    stamped = scheduler.halt_changed_at()
    assert stamped is not None

    _simulate_restart()
    assert scheduler.halt_changed_at() == stamped


def test_halt_changed_at_cleared_on_resume():
    scheduler.halt_all_jobs()
    assert scheduler.halt_changed_at() is not None
    scheduler.resume_all_jobs()
    assert scheduler.halt_changed_at() is None
