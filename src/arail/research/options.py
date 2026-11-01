"""World-aligned research options for the Autoresearch tab.

Derives "think to research" directions from whatever World is mounted —
purely from the sealed bundle's own data (spec categories, roster/drift
gaps, agenda watches, provenance counts) — plus generic seeds when no
World is mounted. World-generic by design: no per-world code, ever (the
standing decision from sprints/2026-07-26-world-of-debt-finance).

Honesty rails:
  * Options are GOAL SEEDS for the existing composer / swarm-review flow.
    They never auto-start research, and they never promise a measurable
    experiment for a domain the engine cannot measure — the planner may
    still classify a hypothesis ``unmeasured``, and that stays the honest
    outcome.
  * The fill-gaps card is omitted when there are no gaps: the lab never
    invents work.
  * The watch card only *describes* the existing consent-gated
    ``agenda_watch`` loop; it fetches nothing itself and says plainly
    when airgapped mode keeps watches dormant.
  * Generic seeds are worded so ``mini_experiments.select_archetype``
    maps each onto a real measurable archetype (pinned by test).

Portal-free and side-effect-free: ``derive_options`` is pure over
already-loaded dicts; ``load_world_inputs`` does bounded JSON reads from
the mounted World's catalog dir (never the staged PKB copy — see the
``_stage_files`` allow-list) and returns None on any problem. Neither
ever raises.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

_log = logging.getLogger("arail.research.options")


@dataclass
class WorldOptionInputs:
    """Already-loaded bundle data ``derive_options`` works over."""

    slug: str
    display_name: str
    tagline: str
    tier: str                       # "sourced" | "mixed" | "model-asserted"
    counts: dict                    # {"model": n, "sourced": n, "total": n}
    spec: dict
    terms: list[dict]
    agenda: Optional[dict] = None
    roster: Optional[dict] = None
    drift: Optional[dict] = None


def _read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_bytes())
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 — absent/corrupt sidecars are normal
        return None


def load_world_inputs() -> Optional[WorldOptionInputs]:
    """Resolve the mounted World's catalog dir and load its bundle data.

    Same resolution as the /worlds routes: prefer the adopted copy under
    ``WORLDS_DIR/<slug>``, fall back to the recorded bundle dir. Returns
    None when no World is mounted or the required files are unreadable —
    callers then fall back to the generic seeds.
    """
    try:
        from arail import world_mount as wm
        record = wm.current_mount()
        if record is None:
            return None
        # Same seam world_routes._mounted_catalog_dir uses (and the same
        # one the test fixtures monkeypatch): the adopted catalog copy
        # first, then the recorded bundle dir.
        candidates = [wm._default_worlds_dir() / record.world,
                      Path(record.bundle_dir)]
        bundle_dir = next(
            (c for c in candidates if (c / "manifest.json").exists()), None)
        if bundle_dir is None:
            return None

        manifest = _read_json(bundle_dir / "manifest.json")
        spec = _read_json(bundle_dir / "spec.json")
        terms_doc = _read_json(bundle_dir / "terms.json")
        if manifest is None or spec is None or terms_doc is None:
            return None
        terms = terms_doc.get("terms")
        if not isinstance(terms, list):
            return None

        # Prefer the manifest's sealed tier/counts; derive live for legacy
        # bundles (same rule as world_routes._live_tier).
        tier = manifest.get("provenance_tier")
        counts = manifest.get("provenance_counts")
        if not tier or not isinstance(counts, dict):
            from arail import world_forge as wf
            tier, counts = wf.compute_provenance_tier(
                [t.get("source") for t in terms if isinstance(t, dict)])

        face = _read_json(bundle_dir / "face.json") or {}
        return WorldOptionInputs(
            slug=record.world,
            display_name=str(manifest.get("display_name")
                             or spec.get("display_name") or record.world),
            tagline=str(face.get("tagline") or ""),
            tier=str(tier),
            counts=dict(counts),
            spec=spec,
            terms=[t for t in terms if isinstance(t, dict)],
            agenda=_read_json(bundle_dir / "agenda.json"),
            roster=_read_json(bundle_dir / "roster.json"),
            drift=_read_json(bundle_dir / "drift-report.json"),
        )
    except Exception as e:  # noqa: BLE001 — options must never break the tab
        _log.warning("research options: world inputs unavailable: %s", e)
        return None


# ── pure derivation ──────────────────────────────────────────────────


def _option(oid: str, kind: str, title: str, goal_text: Optional[str],
            detail: str, *, category: Optional[str] = None,
            href: Optional[str] = None, meta: Optional[dict] = None,
            measure: Optional[str] = None,
            setup: Optional[dict] = None) -> dict:
    """``measure`` says what the engine can actually measure for this
    direction ("if you can measure it, we can improve it" — never claims a
    number the engine won't produce). ``setup`` is a readiness hint when
    data points are required first: ``{"hint": str, "href": str}``."""
    return {"id": oid, "kind": kind, "title": title, "goal_text": goal_text,
            "detail": detail, "category": category, "href": href,
            "meta": meta or {}, "measure": measure, "setup": setup}


# What the engine really measures for glossary/KB-shaped goals: their
# wording maps to the retrieval_quality archetype (probes the
# human-APPROVED knowledge base — coverage + self-retrieval, no model).
_MEASURE_RETRIEVAL = ("KB coverage & self-retrieval over the approved "
                      "knowledge base — a real baseline before, a real "
                      "number after you add material.")


def _kb_setup_hint(approved_docs: Optional[int]) -> Optional[dict]:
    """Data points required first: with zero approved documents the
    coverage baseline is empty — send the user to Knowledge to ingest and
    approve material. Unknown count → no claim."""
    if approved_docs == 0:
        return {"hint": "0 approved documents yet — ingest sources in "
                        "Knowledge and approve them so improvement is "
                        "measurable.",
                "href": "/dac"}
    return None


def _generic_options(approved_docs: Optional[int] = None) -> list[dict]:
    """Seeds for a lab with no World mounted (or an unreadable bundle).

    Each goal is worded so the engine's deterministic keyword mapper
    (``mini_experiments.select_archetype``) lands it on a real,
    measurable archetype — pinned by test_research_options.py.
    """
    return [
        _option(
            "generic:model-speed", "generic", "Benchmark my resident model",
            "Measure my resident model's speed on this machine — "
            "time-to-first-token, decode throughput, and total latency at a "
            "fixed prompt.",
            "Real timing runs through the model that answers your chat — "
            "medians over 3 runs, measured by code.",
            measure="TTFT, decode tok/s, total latency — median of 3 runs."),
        _option(
            "generic:prompt-phrasing", "generic", "Tune prompt phrasing",
            "Find which prompt phrasing and instruction format gives my "
            "local model the most consistent, compliant answers.",
            "Runs 2–3 prompt variants and scores them with deterministic "
            "code-computed proxies — never a model self-score.",
            measure="Format compliance, consistency, latency per variant."),
        _option(
            "generic:kb-retrieval", "generic", "Probe my knowledge base",
            "Measure how well my approved knowledge base covers its own "
            "documents — retrieval coverage and self-search quality.",
            "Probes the human-approved KB index; needs no model at all.",
            measure="Coverage & self-retrieval rank over approved docs.",
            setup=_kb_setup_hint(approved_docs)),
    ]


def _terms_by_category(terms: list[dict]) -> dict[str, int]:
    tally: dict[str, int] = {}
    for t in terms:
        cat = str(t.get("category") or "")
        if cat:
            tally[cat] = tally.get(cat, 0) + 1
    return tally


def _category_id_label(cat: Any) -> tuple[str, str]:
    if isinstance(cat, dict):
        cid = str(cat.get("id") or cat.get("label") or "")
        return cid, str(cat.get("label") or cid)
    return str(cat), str(cat)


def derive_options(inputs: Optional[WorldOptionInputs], *,
                   airgapped: bool,
                   approved_docs: Optional[int] = None) -> list[dict]:
    """The tab's research directions; ``options[0]`` is always the primary.

    ``approved_docs`` is the approved-KB document count when known — it
    drives the "data points required first" setup hints (None = unknown,
    no claim made).
    """
    if inputs is None:
        return _generic_options(approved_docs)

    display = inputs.display_name
    try:
        from arail import world_forge as wf
        suggestions = wf.goal_suggestions(inputs.spec, inputs.tier)
    except Exception:  # noqa: BLE001
        suggestions = []
    if not suggestions:
        return _generic_options(approved_docs)

    kb_setup = _kb_setup_hint(approved_docs)
    total = int(inputs.counts.get("total") or len(inputs.terms))
    categories = [c for c in inputs.spec.get("categories", []) if c]
    out: list[dict] = [_option(
        "default", "default", f"Study {display}", suggestions[0],
        f"{total} {inputs.tier} terms across {len(categories)} categories. "
        "The Researcher plans hypotheses against this World's glossary, "
        "runs what is measurable on this machine, and reports the rest "
        "honestly.",
        meta={"term_count": total, "categories": len(categories),
              "provenance_tier": inputs.tier},
        measure=_MEASURE_RETRIEVAL, setup=kb_setup)]

    # One deepen card per declared category; goal text from
    # goal_suggestions' category lines where they exist (first 4).
    tally = _terms_by_category(inputs.terms)
    category_goals = suggestions[1:]
    for i, cat in enumerate(categories[:6]):
        cid, label = _category_id_label(cat)
        if not label:
            continue
        goal = (category_goals[i] if i < len(category_goals) else
                f"Deepen {label}: add sourced examples and new terms in "
                f"{display}.")
        n = tally.get(cid, 0)
        detail = (f"{n} term{'s' if n != 1 else ''} today — grow this "
                  "category with sourced additions."
                  if n else "No terms yet — a fresh category to seed.")
        out.append(_option(f"deepen:{cid}", "deepen", f"Deepen {label}",
                           goal, detail, category=cid,
                           meta={"term_count": n},
                           measure=_MEASURE_RETRIEVAL, setup=kb_setup))

    # Fill-gaps: only when gaps actually exist — never invent work.
    declared = {str(t.get("slug") or "") for t in inputs.terms}
    gap_slugs: list[str] = []
    if isinstance(inputs.drift, dict) and isinstance(
            inputs.drift.get("missing"), list):
        gap_slugs = [str(s) for s in inputs.drift["missing"] if s]
    elif isinstance(inputs.roster, dict) and isinstance(
            inputs.roster.get("desired"), list):
        gap_slugs = [str(s) for s in inputs.roster["desired"]
                     if s and str(s) not in declared]
    under_cited = int(inputs.counts.get("model") or 0)
    if gap_slugs:
        head = ", ".join(gap_slugs[:3]) + ("…" if len(gap_slugs) > 3 else "")
        out.append(_option(
            "fill-gaps", "verify", "Fill glossary gaps",
            f"Fill {len(gap_slugs)} glossary gap"
            f"{'s' if len(gap_slugs) != 1 else ''} in {display}: draft "
            f"sourced definitions for {head}.",
            "Terms this World declares it wants but does not have yet.",
            meta={"gaps": len(gap_slugs)},
            measure="Gap count is exact (roster/drift); coverage is "
                    "re-measured as gaps close.",
            setup=kb_setup))
    elif under_cited:
        out.append(_option(
            "fill-gaps", "verify", "Verify model-asserted terms",
            f"Verify {display}: find real sources for {under_cited} "
            "model-asserted terms in the glossary.",
            "Definitions the model dreamed up that still need a citation.",
            meta={"under_cited": under_cited},
            measure=f"{under_cited} model-asserted terms counted from the "
                    "sealed bundle — verified terms move the provenance "
                    "tier itself.",
            setup=kb_setup))

    # Watch card: describes the existing consent-gated agenda_watch loop.
    watches = []
    if isinstance(inputs.agenda, dict) and isinstance(
            inputs.agenda.get("watches"), list):
        watches = [w for w in inputs.agenda["watches"] if isinstance(w, dict)]
    if watches:
        n = len(watches)
        plural = "es" if n != 1 else ""
        detail = (
            f"{n} source watch{plural} declared by this World. Dormant in "
            "airgapped mode — this lab never fetches. Flip to hybrid and "
            "approve consent in Knowledge to arm them."
            if airgapped else
            f"{n} source watch{plural} declared by this World feed the "
            "consent-gated scout queue — findings land in Knowledge for "
            "your review.")
        out.append(_option(
            "watch-sources", "watch", "Gather source material", None,
            detail, href="/dac", meta={"feeds": n, "airgapped": airgapped},
            measure="Code-extracted data points (declared regex patterns, "
                    "e.g. rates/percents) from each fetched source — "
                    "reviewed by you before entering the KB.",
            setup=({"hint": "Arm the scout: flip to hybrid and grant "
                            "consent in Knowledge — the Librarian then "
                            "gathers material for your review.",
                    "href": "/dac"} if airgapped else None)))

    return out


def _approved_kb_count() -> Optional[int]:
    """Approved-document count for the readiness hints; None = unknown."""
    try:
        from arail.compiled_kb import approved_paths
        return len(list(approved_paths()))
    except Exception:  # noqa: BLE001
        return None


def options_payload(*, airgapped: bool) -> dict:
    """The full ``GET /api/research/options`` response body."""
    inputs = load_world_inputs()
    options = derive_options(inputs, airgapped=airgapped,
                             approved_docs=_approved_kb_count())
    return {
        "world": inputs.slug if inputs else None,
        "display_name": inputs.display_name if inputs else None,
        "tagline": inputs.tagline if inputs else None,
        "provenance_tier": inputs.tier if inputs else None,
        "term_count": (int(inputs.counts.get("total") or len(inputs.terms))
                       if inputs else None),
        "airgapped": airgapped,
        "options": options,
    }
