"""build-report.md generator (ARCHITECTURE.md §4.10; brief §5.7).

A two-line plain-language summary comes first (decision, whether the
student beat its base, and by how much), then metric tables, then the 10
eyeball prompts with verbatim student | base_student | previous-version
outputs — the composite number appears only in the metric table, listed
AFTER the eyeball section (W3), because Dubois's point (also in the
brief) is you only catch "scored poorly, good in practice" by reading
outputs first.
"""

from __future__ import annotations

from typing import List, Optional


def _fence(text: str) -> str:
    """Fenced code block — never raw HTML, so the /forge renderer's
    markdown-it-py(html=False) + Jinja autoescape can't be defeated by a
    model output that looks like markup (T-FORGE-3)."""
    body = (text or "").replace("```", "``​`")  # defang an embedded fence
    return f"```\n{body}\n```"


def _fmt(value) -> str:
    """B3 (2026-09-23 review): achieved/base_composite can now legitimately
    be the string "not_computed" (composite.NOT_COMPUTED) when an input
    metric is not_run -- format it as-is instead of crashing on a `:.3f`
    applied to a string."""
    return f"{value:.3f}" if isinstance(value, (int, float)) else str(value)


def _summary_lines(*, decision: str, beats_base: Optional[bool], achieved, base_composite) -> List[str]:
    if isinstance(achieved, (int, float)) and isinstance(base_composite, (int, float)):
        delta = achieved - base_composite
        if beats_base is True:
            beat_text = f"beats its base by {delta:+.3f} composite points"
        elif beats_base is False:
            beat_text = f"does NOT beat its base ({delta:+.3f} composite points)"
        else:
            beat_text = "— whether it beats its base is UNKNOWN (no comparable base score)"
    else:
        beat_text = "— composite not computed (one or more required metrics is not_run)"
    return [
        f"**Decision: {decision}.** The student {beat_text}.",
        f"Composite achieved: {_fmt(achieved)}.",
    ]


def _metric_table(rows: List[dict], *, headers: List[str]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row["cells"]) + " |")
    return "\n".join(lines)


def render(
    *, decision: str, beats_base: Optional[bool], achieved, base_composite,
    composite_formula_id: str, composite_value, closed_ended: dict, open_ended: dict,
    executable: dict, eyeball_prompts: List[str], student_outputs: List[str],
    base_outputs: List[str], previous_outputs: Optional[List[str]] = None,
    fused_dir_abs: Optional[str] = None,
) -> str:
    if len(eyeball_prompts) != 10 or len(student_outputs) != 10 or len(base_outputs) != 10:
        raise ValueError("build report requires exactly 10 eyeball prompts/student/base outputs")
    previous_outputs = previous_outputs or [None] * 10

    parts: List[str] = []
    parts.extend(_summary_lines(decision=decision, beats_base=beats_base, achieved=achieved,
                                base_composite=base_composite))
    parts.append("")

    parts.append("## Eyeball prompts")
    parts.append("")
    for i, (prompt, student_out, base_out, prev_out) in enumerate(
        zip(eyeball_prompts, student_outputs, base_outputs, previous_outputs), start=1
    ):
        parts.append(f"### {i}. {prompt}")
        parts.append("")
        parts.append("**Student:**")
        parts.append(_fence(student_out))
        parts.append("**Base student:**")
        parts.append(_fence(base_out))
        if prev_out is not None:
            parts.append("**Previous version:**")
            parts.append(_fence(prev_out))
        parts.append("")

    parts.append("## Metrics")
    parts.append("")
    formatted_composite = f"{composite_value:.4f}" if isinstance(composite_value, (int, float)) else str(composite_value)
    parts.append(f"Composite formula: `{composite_formula_id}` = **{formatted_composite}**")
    parts.append("")

    closed_rows = [{"cells": [name, row.get("f1", row.get("macro_f1", "—")), row.get("n", "—")]}
                   for name, row in closed_ended.items()]
    parts.append("### Closed-ended")
    parts.append(_metric_table(closed_rows, headers=["task", "F1 / macro-F1", "n"]))
    parts.append("")

    open_rows = [{"cells": [name, row.get("lc_win_rate_vs_base", row.get("status", "—")), row.get("n", "—")]}
                for name, row in open_ended.items()]
    parts.append("### Open-ended (length-controlled)")
    parts.append(_metric_table(open_rows, headers=["task", "LC win rate vs base", "n"]))
    parts.append("")

    exec_rows = [{"cells": [name, row.get("rate", row.get("status", "—")), row.get("n", "—")]}
                for name, row in executable.items()]
    parts.append("### Executable")
    parts.append(_metric_table(exec_rows, headers=["check", "rate", "n"]))
    parts.append("")

    if fused_dir_abs:
        from arail.nucleus.runtime_names import chat_env_hint

        parts.append(chat_env_hint(fused_dir_abs))
        parts.append("")

    return "\n".join(parts)
