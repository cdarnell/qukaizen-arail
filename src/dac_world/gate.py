"""The gate — port of DDaC's ``src/gate.ts``, three laws: sourced, declared
category, closed related-graph.

Moved verbatim from qukaizen-arail's ``src/arail/world_forge.py`` as part of
the ``dac_world`` migration — see
``sprints/2026-07-19-dac-generates-arail-worlds/ARCHITECTURE.md`` (qukaizen-dac).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GateResult:
    ok: bool = True
    dangling_edges: list[tuple[str, str]] = field(default_factory=list)
    unsourced: list[str] = field(default_factory=list)
    undeclared_category: list[tuple[str, str]] = field(default_factory=list)


class GateRefused(Exception):
    """The drafted corpus failed the gate (or produced nothing usable)."""

    def __init__(self, gate: GateResult, message: str = "gate refused the corpus"):
        super().__init__(message)
        self.gate = gate


def _edge_target(edge: Any) -> str:
    if isinstance(edge, str):
        return edge.strip()
    if isinstance(edge, dict) and isinstance(edge.get("slug"), str):
        return edge["slug"].strip()
    return ""


def assert_closed_sourced_graph(terms: list[dict], declared: set[str]) -> GateResult:
    """The three laws: sourced, declared category, closed related-graph.

    Pure, total, deterministic -- never raises. Empty corpus -> vacuously ok.
    Self-edges resolve like any other edge. Missing slug -> reported violation.
    """
    result = GateResult()
    slug_set = {t["slug"].strip() for t in terms
                if isinstance(t.get("slug"), str) and t["slug"].strip()}

    for t in terms:
        slug = t.get("slug", "")
        slug = slug.strip() if isinstance(slug, str) else ""
        if not slug:
            result.undeclared_category.append(("<missing-slug>", str(t.get("category", "<missing>"))))
            result.ok = False
            continue
        src = t.get("source", "")
        if not (isinstance(src, str) and src.strip()):
            result.unsourced.append(slug)
            result.ok = False
        cat = t.get("category", "")
        cat = cat.strip() if isinstance(cat, str) else ""
        if not cat or cat not in declared:
            result.undeclared_category.append((slug, cat))
            result.ok = False
        for edge in t.get("related") or []:
            target = _edge_target(edge)
            if target and target not in slug_set:
                result.dangling_edges.append((slug, target))
                result.ok = False
    return result
