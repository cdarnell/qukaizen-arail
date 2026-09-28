"""arail.nucleus.cards.build_report (T-REPORT-1)."""

from __future__ import annotations

import pytest

from arail.nucleus.cards import build_report


def _report(**overrides):
    kwargs = dict(
        decision="CERTIFIED", beats_base=True, achieved=0.8, base_composite=0.4,
        composite_formula_id="composite/v1-nc", composite_value=0.8,
        closed_ended={"cve_detection": {"f1": 0.75, "n": 20}},
        open_ended={"patch_explanation": {"status": "not_run", "reason": "x"}},
        executable={"patch_applies": {"rate": 0.9, "n": 10}},
        eyeball_prompts=[f"prompt {i}" for i in range(10)],
        student_outputs=[f"student answer {i}" for i in range(10)],
        base_outputs=[f"base answer {i}" for i in range(10)],
    )
    kwargs.update(overrides)
    return build_report.render(**kwargs)


# ── T-REPORT-1: exactly 10 eyeball sections, summary before composite ──

def test_exactly_10_eyeball_sections():
    import re

    text = _report()
    assert len(re.findall(r"^### \d+\. ", text, flags=re.MULTILINE)) == 10


def test_each_section_has_student_and_base_labels():
    text = _report()
    assert text.count("**Student:**") == 10
    assert text.count("**Base student:**") == 10


def test_summary_appears_before_composite_table():
    text = _report()
    summary_idx = text.index("Decision: CERTIFIED")
    composite_idx = text.index("Composite formula")
    assert summary_idx < composite_idx


def test_eyeball_section_before_metrics_section():
    text = _report()
    eyeball_idx = text.index("## Eyeball prompts")
    metrics_idx = text.index("## Metrics")
    assert eyeball_idx < metrics_idx


def test_previous_version_column_when_given():
    text = _report(previous_outputs=[f"prev {i}" for i in range(10)])
    assert text.count("**Previous version:**") == 10


def test_no_previous_version_column_when_absent():
    text = _report()
    assert "**Previous version:**" not in text


def test_wrong_prompt_count_raises():
    with pytest.raises(ValueError):
        _report(eyeball_prompts=["only one"])


# ── T-FORGE-3-adjacent: model output is always fenced, never raw markup ──

def test_html_in_output_is_fenced_not_raw():
    text = _report(student_outputs=["<script>alert(1)</script>"] + [f"s{i}" for i in range(9)])
    assert "```" in text
    # The dangerous text is inside a fence, not emitted as bare HTML.
    fence_start = text.index("```")
    assert text[fence_start:fence_start + 200].count("<script>") <= 1


def test_chat_hint_included_when_fused_dir_given():
    text = _report(fused_dir_abs="/abs/path/to/fused")
    assert "To chat with this shard" in text
    assert "/abs/path/to/fused" in text


def test_no_chat_hint_when_fused_dir_absent():
    text = _report()
    assert "To chat with this shard" not in text
