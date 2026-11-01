"""A research report may not say more than the run measured.

The metrics were always code-measured, but the narrative around them was
model-written and unchecked. A real run produced a report that opened

    **Hardware:** Qwen3-235B-A22B on M2 Max, 32 GB
    **Date:** YYYY-MM-DD
    **Seed:** <fixed>

on a machine where the measurements came from `ai-engineer` — and then
attached genuine numbers to hypotheses the lab never tested. Most of the
document was true, which is exactly what made it dangerous: a reader
cannot tell which half to believe.
"""
from __future__ import annotations

import pytest

from arail.agents.researcher import (
    measured_facts_block,
    verify_report_narrative,
)


def _exp(**kw):
    base = {
        "id": "abc123",
        "hypothesis": "Constrained prompts will raise compliance",
        "results": {
            "archetype": "prompt_variant",
            "provenance": "measured",
            "outcome": "supported",
            "model": "ai-engineer",
            "backend": "ollama_native",
            "runs": 3,
            "environment": {"platform": "macOS-26.5.2-arm64-arm-64bit"},
            "metrics": {"best_compliance_rate": 1.0,
                        "baseline_compliance_rate": 0.667,
                        "median_latency_ms": 1611.13},
        },
    }
    base.update(kw)
    return base


# ── Rejection ───────────────────────────────────────────────────────

def test_placeholders_are_rejected():
    probs = verify_report_narrative(
        "**Date:** YYYY-MM-DD\nCompliance reached 1.0.", [_exp()])
    assert any("placeholder" in p for p in probs)


def test_asserted_hardware_header_is_rejected():
    probs = verify_report_narrative(
        "**Hardware:** Qwen3-235B on M2 Max\nCompliance reached 1.0.",
        [_exp()])
    assert any("cannot know" in p for p in probs)


def test_invented_number_is_rejected():
    """The '32' in 'M2 Max, 32 GB' — a real report wrote exactly this."""
    probs = verify_report_narrative(
        "The run used 32 GB of unified memory.", [_exp()])
    assert any("not traceable" in p for p in probs)


def test_the_actual_fabricated_report_is_rejected():
    narrative = (
        "# Eval\n\n**Date:** YYYY-MM-DD\n"
        "**Hardware:** Qwen3-235B-A22B on M2 Max, 32 GB\n"
        "**Seed:** <fixed>\n\n"
        "Compliance reached 1.0 versus 0.667 baseline."
    )
    probs = verify_report_narrative(narrative, [_exp()])
    assert len(probs) >= 3


def test_empty_narrative_is_rejected():
    assert verify_report_narrative("", [_exp()])


# ── Acceptance ──────────────────────────────────────────────────────

def test_measured_values_are_accepted():
    narrative = ("Compliance rose from 0.667 to 1.0 while median latency "
                 "was 1611.13 ms over 3 runs.")
    assert verify_report_narrative(narrative, [_exp()]) == []


def test_rounding_a_measured_value_is_allowed():
    """Reporting 1611.13 ms as 1611 ms is reporting, not inventing."""
    assert verify_report_narrative(
        "Median latency was about 1611 ms.", [_exp()]) == []


def test_numbers_from_the_hypothesis_may_be_restated():
    e = _exp(hypothesis="Constrained prompts lift compliance by 20%")
    assert verify_report_narrative(
        "The hypothesis predicted a 20% lift.", [e]) == []


def test_list_numbering_is_not_flagged():
    assert verify_report_narrative(
        "1. First point.\n2. Second point.\n3. Third point.", [_exp()]) == []


# ── The code-emitted facts block ────────────────────────────────────

def test_facts_block_reports_the_real_model_and_platform():
    block = measured_facts_block([_exp()])
    assert "ai-engineer" in block
    assert "macOS-26.5.2-arm64-arm-64bit" in block
    assert "not written by a model" in block


def test_facts_block_lists_every_measured_metric():
    block = measured_facts_block([_exp()])
    for key in ("best_compliance_rate", "baseline_compliance_rate",
                "median_latency_ms"):
        assert key in block


def test_facts_block_flags_a_hypothesis_the_run_never_tested():
    """The engine maps any hypothesis onto an archetype and measures
    THAT, so a claim about a 235B model becomes a local throughput run
    and the record still says 'supported'."""
    e = _exp(hypothesis=(
        "Speculative decoding will boost t/min for Qwen3-235B-A22B by 20%"))
    block = measured_facts_block([e])
    assert "does not test that claim" in block
    assert "ai-engineer" in block


def test_no_mismatch_flag_when_the_hypothesis_names_the_measured_model():
    e = _exp(hypothesis="ai-engineer will decode faster with short prompts")
    assert "does not test that claim" not in measured_facts_block([e])


def test_no_mismatch_flag_when_no_model_is_named():
    assert "does not test that claim" not in measured_facts_block([_exp()])


def test_cannot_run_experiments_state_their_reason():
    e = _exp(results={"archetype": "model_throughput",
                      "provenance": "cannot_run",
                      "cannot_run_reason": "no local model available",
                      "metrics": {}})
    block = measured_facts_block([e])
    assert "could not run" in block and "no local model available" in block


def test_unmeasured_experiments_say_so():
    e = _exp(results={"archetype": "unmeasured", "provenance": "unmeasured",
                      "metrics": {}})
    assert "not measurable on this machine" in measured_facts_block([e])


def test_facts_block_is_empty_without_experiments():
    assert measured_facts_block([]) == ""


# ── The run must actually have tested what the prose discusses ──────

def test_narrative_is_rejected_when_the_run_never_tested_the_hypothesis():
    e = _exp(hypothesis=(
        "Speculative decoding will boost t/min for Qwen3-235B-A22B by 20%"))
    probs = verify_report_narrative(
        "Speculative decoding reached 1.0 compliance.", [e])
    assert any("did not test their stated hypothesis" in p for p in probs)


def test_comparison_prose_is_rejected_when_nothing_was_varied():
    """Five identical throughput runs are not an A/B test. A real report
    read run-to-run noise as 'Prefetch Lookahead: increased from 2 to 3
    layers'."""
    runs = [
        _exp(id="a", hypothesis="Prefetch depth helps",
             results={"archetype": "model_throughput", "provenance": "measured",
                      "model": "ai-engineer", "runs": 3,
                      "metrics": {"decode_tok_per_sec": 63.601}}),
        _exp(id="b", hypothesis="KV cache helps",
             results={"archetype": "model_throughput", "provenance": "measured",
                      "model": "ai-engineer", "runs": 3,
                      "metrics": {"decode_tok_per_sec": 66.327}}),
    ]
    probs = verify_report_narrative(
        "Prefetch lookahead decreased decode_tok_per_sec from 63.601 to "
        "66.327 compared to baseline.", runs)
    assert any("as if something was varied" in p for p in probs)


def test_plain_description_of_identical_runs_is_allowed():
    """Reporting the same measurement twice is fine — only claiming a
    difference between them is not."""
    runs = [
        _exp(id="a", results={"archetype": "model_throughput",
                              "provenance": "measured", "model": "ai-engineer",
                              "runs": 3, "metrics": {"decode_tok_per_sec": 63.601}}),
        _exp(id="b", results={"archetype": "model_throughput",
                              "provenance": "measured", "model": "ai-engineer",
                              "runs": 3, "metrics": {"decode_tok_per_sec": 66.327}}),
    ]
    assert verify_report_narrative(
        "Decode throughput sat between 63.601 and 66.327 tok/s.", runs) == []


def test_comparison_prose_is_fine_when_runs_actually_differ():
    runs = [
        _exp(id="a", results={"archetype": "prompt_variant",
                              "provenance": "measured", "model": "ai-engineer",
                              "runs": 3, "metrics": {"best_compliance_rate": 1.0}}),
        _exp(id="b", results={"archetype": "model_throughput",
                              "provenance": "measured", "model": "ai-engineer",
                              "runs": 3, "metrics": {"decode_tok_per_sec": 66.327}}),
    ]
    assert verify_report_narrative(
        "Compliance reached 1.0 compared to the throughput baseline of "
        "66.327 tok/s.", runs) == []


# ── The completion message must reflect the outcome ─────────────────

from arail.agents.researcher import completion_status


def _exp_with(provenance):
    return {"id": "x", "results": {"provenance": provenance}}


def test_zero_measurements_is_not_a_success():
    """A lab with a dead model must not look like a lab doing science."""
    level, msg, counts = completion_status(
        [_exp_with("cannot_run"), _exp_with("cannot_run")])
    assert level == "warn"
    assert "no measurements" in msg
    assert counts == {"measured": 0, "experiments": 2}


def test_all_unmeasured_is_also_not_a_success():
    level, msg, _ = completion_status([_exp_with("unmeasured")])
    assert level == "warn" and "0/1" in msg


def test_no_experiments_at_all_is_not_a_success():
    level, msg, counts = completion_status([])
    assert level == "warn"
    assert "without running any experiment" in msg
    assert counts == {"measured": 0, "experiments": 0}


def test_a_measured_run_is_a_success_and_names_the_count():
    level, msg, counts = completion_status(
        [_exp_with("measured"), _exp_with("cannot_run"),
         _exp_with("unmeasured")])
    assert level == "success"
    assert "1/3" in msg
    assert counts == {"measured": 1, "experiments": 3}


def test_a_fully_measured_run_reads_cleanly():
    level, msg, _ = completion_status([_exp_with("measured")] * 4)
    assert level == "success" and "4/4" in msg


def test_a_missing_results_block_counts_as_unmeasured():
    level, _, counts = completion_status([{"id": "x"}])
    assert level == "warn" and counts["measured"] == 0
