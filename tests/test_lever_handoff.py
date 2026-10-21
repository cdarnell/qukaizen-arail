"""A refusal should say where the thing CAN be tested.

The Researcher correctly refuses hypotheses the on-device engine cannot
vary. On its own that leaves an operator whose entire research interest
is inference speed with a page of "not tested" and nowhere to go — while
/tuning has had a `prefetch_lookahead` knob the whole time.
"""
from __future__ import annotations

import pytest

from arail.experiments import levers


def test_real_knobs_are_discovered_from_the_live_schema():
    knobs = levers.available_knobs()
    assert "prefetch_lookahead" in knobs and knobs["prefetch_lookahead"] == "aerollm"
    assert "kv_bits" in knobs and knobs["kv_bits"] == "mlx"


def test_loop_machinery_is_not_offered_as_a_lever():
    """bench_runs_per_config tunes the harness, not the thing under test."""
    knobs = levers.available_knobs()
    assert "bench_runs_per_config" not in knobs
    assert "improvement_threshold_pct" not in knobs


def test_prefetch_hypothesis_points_at_the_prefetch_knobs():
    hits = dict(levers.levers_for(
        "Increasing prefetch lookahead from 2 to 4 will raise tok/s"))
    assert "prefetch_lookahead" in hits and "prefetch_enabled" in hits


def test_mixed_precision_spans_both_backends():
    hits = dict(levers.levers_for(
        "Switching to mixed-precision per-layer will improve tokens per second"))
    assert hits.get("aerollm_compression") == "aerollm"
    assert hits.get("kv_bits") == "mlx"


@pytest.mark.parametrize("hyp", [
    "Integrating speculative decoding will boost tok/s",
    "Increasing concurrent-prompt batching depth will help latency",
    "Applying LoRA fine-tuning will make it faster",
])
def test_no_knob_says_so_plainly(hyp):
    assert levers.levers_for(hyp) == []
    line = levers.handoff_line(hyp)
    assert "No knob" in line and "QueueLLM itself" in line


def test_handoff_line_never_invents_a_knob(monkeypatch):
    """Rule 1: only name a knob confirmed present in the live schema."""
    monkeypatch.setattr(levers, "available_knobs", lambda: {})
    assert levers.levers_for("increase prefetch lookahead") == []
    assert "No knob" in levers.handoff_line("increase prefetch lookahead")


def test_handoff_line_names_the_surface_to_go_to():
    line = levers.handoff_line("increase the prefetch lookahead depth")
    assert "/tuning" in line and "prefetch_lookahead" in line


def test_unreadable_config_degrades_to_naming_nothing(monkeypatch):
    monkeypatch.setattr(levers, "_repo_root", lambda: __import__("pathlib").Path("/nonexistent"))
    levers.available_knobs.cache_clear()
    try:
        assert levers.available_knobs() == {}
    finally:
        levers.available_knobs.cache_clear()
