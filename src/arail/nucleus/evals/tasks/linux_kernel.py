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

B5 (2026-09-23 review): the routing prompt used to show the full subject
line, ``"<subsystem>: <description>"`` -- the exact gold label, verbatim,
right in the prompt the model is scored on. The prompt now shows only the
description (the part after the first ``:``), never the subsystem prefix
itself, so macro-F1 measures routing, not copying.
"""

from __future__ import annotations

import re
from typing import List, Tuple

from arail.nucleus.providers.base import Prompt

_WORD_RE = re.compile(r"[a-z0-9]+")


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
        subsystem, sep, description = subject.partition(":")
        subsystem = subsystem.strip() or "unknown"
        # Show only the description -- never the "<subsystem>:" prefix,
        # which IS the gold label (B5: the model must not be able to copy
        # its own answer out of the prompt).
        shown = description.strip() if sep else subject
        prompts.append(Prompt(
            item_id=item["id"],
            text=(f"Which subsystem does this kernel commit belong to? "
                 f"Answer with just the subsystem name.\n\nCommit description: {shown}"),
            role="subsystem_routing",
        ))
        gold.append(subsystem)
    return prompts, gold


def parse_closed_answer(text: str, *, valid: Tuple[str, ...]) -> str:
    """Strict parse: scans the model's (lowercased) answer left to right
    and returns the first WHOLE token that equals one of ``valid`` --
    never a substring match. This is order-of-appearance-in-the-ANSWER,
    not order-of-appearance-in-``valid``, so a negation stated before the
    label it negates ("this is not a CVE fix") parses as the negation,
    not the label it's negating (B5)."""
    lowered = (text or "").strip().lower()
    valid_set = {label.lower() for label in valid}
    label_by_lower = {label.lower(): label for label in valid}
    for token in _WORD_RE.findall(lowered):
        if token in valid_set:
            return label_by_lower[token]
    return "invalid"
