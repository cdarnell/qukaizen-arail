"""DaC World → Nucleus training corpus.

Pulls ARAIL-approved terms from a mounted-then-remounted DaC World and turns
them into a trained model — bypassing Nucleus's orchestrator/KICE entirely,
because World content has already been through DaC's compile-time gate
(sourced, closed, categorized) and ARAIL's Compiled-KB human-approval gate;
re-running KICE's heuristic keyword tagging over it would only downgrade it.

TEMPORARY RE-EXPORT SHIM (sprints/2026-09-23-nucleus-sprint-1, ARCHITECTURE.md
§8): the deterministic pull/read half of this module (``_safe_term_slug``,
``resolve_world_bundle``, ``all_categories``, ``category_breakdown``,
``pull_approved_terms``, ``CRAFT_CATEGORIES``) moved to
``arail.world_catalog`` — a neutral module ``compiled_kb`` already needed and
a future ``world:`` corpus source will need, without depending on the
build-specific (and KICE/docker-Nucleus-bound) orchestration below. This
module re-exports those names for any caller still importing them from here.
The shim is removed when ``/build`` is retired (commit 26); import from
``arail.world_catalog`` in new code.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

from arail.world_catalog import (  # noqa: F401 — re-export shim, see module docstring
    CRAFT_CATEGORIES,
    _safe_term_slug,
    all_categories,
    category_breakdown,
    pull_approved_terms,
    resolve_world_bundle,
)

log = logging.getLogger(__name__)

# Reused verbatim from qukaizen-nucleus's docs/EXTRACTION_LAYERS_GUIDE.md —
# layer only affects RAFT distractor/oracle proximity (a quality heuristic),
# never gating, so an approximate re-derivation against already-curated text
# is legitimate reuse, not a KICE reimplementation.
_L6_AMBIGUITY_CUES = ("it depends", "varies by", "context dependent",
                      "implementation defined", "undefined behavior",
                      "unspecified", "ambiguous", "not clearly documented",
                      "no universal", "no single")
_L5_REASONING_CUES = ("because", "therefore", "however", "on the other hand",
                      "trade-off", "tradeoff", "the reason is", "this implies",
                      "as a result", "which means")


# ── term → KICEExample mapping ──────────────────────────────────────

def _infer_layer(term: Dict[str, Any]) -> int:
    """1 (default) unless the term's own text carries L5/L6 cues."""
    text = f"{term.get('definition','')} {term.get('example','')}".lower()
    if any(cue in text for cue in _L6_AMBIGUITY_CUES):
        return 6
    hits = sum(1 for cue in _L5_REASONING_CUES if cue in text)
    if hits >= 2:
        return 5
    related = term.get("related")
    if isinstance(related, list) and len(related) >= 4:
        return 4
    return 1


def term_to_kice_example(term: Dict[str, Any], *,
                         id_prefix: str = "world") -> Dict[str, Any]:
    """Field mapping against nucleus's KICEExample (synthesizer/service.py):
    id, subdomain (=category — the axis RAFT's distractor selection groups
    on), layer, source_type, title, content, reasoning_prompt, quality_score.
    """
    slug = _safe_term_slug(term.get("slug", ""))
    name = term.get("term") or slug
    parts = [f"{name} — {term.get('short','')}".strip(" —"),
             term.get("definition", "")]
    if term.get("example"):
        parts.append(f"Example: {term['example']}")
    source = term.get("source", "")
    if source:
        parts.append(f"Source: {source}")
    content = "\n\n".join(p for p in parts if p)

    return {
        "id": f"{id_prefix}-{slug}",
        "subdomain": term.get("category", "general"),
        "layer": _infer_layer(term),
        "source_type": "world_term",
        "title": name,
        "content": content,
        "reasoning_prompt": (
            f"Explain {name} in photography: what it is, why it matters, "
            f"and how it's used."),
        "quality_score": 0.7 if source else 0.5,
    }


def chunk(items: List[Any], size: int = 15) -> List[List[Any]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def tag_source(records: List[Dict[str, Any]], tag: str) -> List[Dict[str, Any]]:
    """Tag each trainer record with a top-level 'source' field — Trainer's
    curriculum_weights (default {"tier2": 2.5}) auto-oversamples whichever
    tag matches, with zero Trainer-side changes."""
    for r in records:
        r["source"] = tag
    return records


# ── orchestration ───────────────────────────────────────────────────

def build_world_corpus(
    world_slug: str,
    run_id: str,
    *,
    categories: Iterable[str] = CRAFT_CATEGORIES,
    tier2_categories: Iterable[str] = (),
    student_model: str = "mlx-community/Qwen2.5-3B-Instruct-4bit",
    client: Optional[Any] = None,
    job_store: Optional[Any] = None,
    batch_size: int = 15,
    synthesize_timeout: float = 600.0,
    worlds_dir: Optional[Any] = None,
    pkb_root: Optional[Any] = None,
) -> Dict[str, Any]:
    """pull -> map -> synthesize (tier1, then tier2 if requested) -> tag ->
    merge -> train. Checkpoints job_store at each phase. Blocking — run this
    on a background thread from the portal layer, never on the event loop
    (a full pass over ~150 terms can run 10-60+ minutes).

    tier2_categories selects the "hotspot" subset re-synthesized through a
    second /synthesize pass. The caller is responsible for having restarted
    the nucleus-teacher process with TEACHER_BACKEND=anthropic between the
    tier1 and tier2 calls (see docs/persistence.md's World-corpus runbook —
    Synthesizer has no per-request tier field, so this is process-level,
    not something this function can automate).
    """
    from arail.build.jobs import BuildJobStore
    from arail.build.nucleus_client import NucleusClient

    client = client or NucleusClient()
    job_store = job_store or BuildJobStore()

    def _update(**fields: Any) -> None:
        try:
            job_store.update(run_id, **fields)
        except Exception:  # noqa: BLE001 — progress tracking must never abort a build
            log.warning("world_corpus: job_store.update failed for %s", run_id)

    _update(phase="pull")
    tier2_set = set(tier2_categories)
    all_cats = list(dict.fromkeys(list(categories) + list(tier2_set)))
    terms = pull_approved_terms(world_slug, categories=all_cats,
                                worlds_dir=worlds_dir, pkb_root=pkb_root)
    if not terms:
        raise ValueError(
            f"no approved terms found for World '{world_slug}' in "
            f"categories {list(all_cats)} — mount + approve first")

    tier1_terms = [t for t in terms if t.get("category") not in tier2_set]
    tier2_terms = [t for t in terms if t.get("category") in tier2_set]

    _update(phase="synthesize_tier1",
           synth_progress={"tier": 1, "batch": 0, "of": len(chunk(tier1_terms, batch_size))})
    tier1_records = _synthesize_all(client, tier1_terms, id_prefix="world",
                                    batch_size=batch_size,
                                    timeout=synthesize_timeout,
                                    on_progress=lambda b, n: _update(
                                        phase="synthesize_tier1",
                                        synth_progress={"tier": 1, "batch": b, "of": n}))
    tag_source(tier1_records, "tier1")

    tier2_records: List[Dict[str, Any]] = []
    if tier2_terms:
        _update(phase="synthesize_tier2",
               synth_progress={"tier": 2, "batch": 0, "of": len(chunk(tier2_terms, batch_size))})
        tier2_records = _synthesize_all(client, tier2_terms, id_prefix="world-tier2",
                                        batch_size=batch_size,
                                        timeout=synthesize_timeout,
                                        on_progress=lambda b, n: _update(
                                            phase="synthesize_tier2",
                                            synth_progress={"tier": 2, "batch": b, "of": n}))
        tag_source(tier2_records, "tier2")

    dataset = tier1_records + tier2_records
    _update(phase="train", record_count=len(dataset))
    train_result = client.train_direct(dataset, run_id=run_id)
    _update(phase="training", train_started=train_result)

    return {
        "world_slug": world_slug,
        "categories": list(all_cats),
        "tier2_categories": list(tier2_set),
        "term_count": len(terms),
        "record_count": len(dataset),
        "train_result": train_result,
    }


def _synthesize_all(client: Any, terms: List[Dict[str, Any]], *,
                    id_prefix: str, batch_size: int, timeout: float,
                    on_progress: Any = None) -> List[Dict[str, Any]]:
    examples_by_chunk = chunk(
        [term_to_kice_example(t, id_prefix=id_prefix) for t in terms],
        batch_size)
    records: List[Dict[str, Any]] = []
    for i, batch in enumerate(examples_by_chunk, start=1):
        if on_progress:
            on_progress(i, len(examples_by_chunk))
        result = client.synthesize(batch, timeout=timeout)
        records.extend(result.get("training_records", []))
    return records
