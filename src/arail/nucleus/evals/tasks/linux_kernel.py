"""Task adapters for the linux-kernel domain (ARCHITECTURE.md §4.9's
task-adapter seam).

Two closed-ended tasks:
    cve_detection      binary — is this commit a security fix?
    subsystem_routing  multi-class macro-F1 — which subsystem owns it?

Subsystem ground truth is derived from the commit subject's conventional
``<subsystem>: <description>`` prefix (both the real Linux kernel and this
sprint's synthetic fixture, tests/fixtures/nucleus/linux-kernel-mini/,
follow this convention) rather than requiring a file-path field the
corpus schema doesn't carry — MAINTAINERS-pattern-based routing from
actual changed file paths is a documented seam (Assumption 9 in
ARCHITECTURE.md), not built this sprint.
"""

from __future__ import annotations

from typing import List, Tuple

from arail.nucleus.providers.base import Prompt


def cve_detection_task(items: List[dict]) -> Tuple[List[Prompt], List[str]]:
    prompts: List[Prompt] = []
    gold: List[str] = []
    for item in items:
        subject = item.get("subject", "")
        body = item.get("text", "")
        prompts.append(Prompt(
            item_id=item["id"],
            text=(f"Is the following kernel commit a security fix (CVE)? "
                 f"Answer exactly 'cve' or 'not'.\n\nSubject: {subject}\n\n{body}"),
            role="cve_detection",
        ))
        gold.append("cve" if item.get("labels", {}).get("cve") else "not")
    return prompts, gold


def subsystem_routing_task(items: List[dict]) -> Tuple[List[Prompt], List[str]]:
    prompts: List[Prompt] = []
    gold: List[str] = []
    for item in items:
        subject = item.get("subject", "")
        prompts.append(Prompt(
            item_id=item["id"],
            text=(f"Which subsystem does this kernel commit belong to? "
                 f"Answer with just the subsystem name.\n\nSubject: {subject}"),
            role="subsystem_routing",
        ))
        subsystem, _, _ = subject.partition(":")
        gold.append(subsystem.strip() or "unknown")
    return prompts, gold


def parse_closed_answer(text: str, *, valid: Tuple[str, ...]) -> str:
    """Constrained parse: the first valid label found in the model's
    (lowercased) answer text, or "invalid" if none match."""
    lowered = (text or "").strip().lower()
    for label in valid:
        if label.lower() in lowered:
            return label
    return "invalid"
