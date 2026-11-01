"""The Curator judge + organic-growth loop -- port of DDaC's
``scripts/reconcile-world.mts``.

Moved from qukaizen-arail's ``src/arail/world_forge.py`` as part of the
``dac_world`` migration — see
``sprints/2026-07-19-dac-generates-arail-worlds/ARCHITECTURE.md`` (qukaizen-dac).

Not named in ARCHITECTURE.md's illustrative package-layout list (which names
only ``forge``/``gate``/``provenance``/``seal``/``skill``/``validate``), but
moved here per BUILD_LOG.md's "Scope note" — it is pure, model-free (a
caller-injected ``router``, same pattern as ``forge.forge_world``), and
ARAIL's portal (`world_routes.py`) depends on it via ``wf.<name>`` alongside
the named 6.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from .forge import MAX_DEFINITION, MAX_EXAMPLE, MAX_RELATED_PER_TERM, MAX_SHORT
from .parsing import first_array, loose_json, slugify

_log = logging.getLogger(__name__)


@dataclass
class ReviewFlag:
    slug: str
    verdict: str                 # "accept" | "correct" | "reject"
    better_category: str = ""
    bad_edges: list[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return {"slug": self.slug, "verdict": self.verdict,
                "better_category": self.better_category,
                "bad_edges": list(self.bad_edges), "note": self.note}


def apply_corrections(
    terms: list[dict], flags: "list[ReviewFlag]", declared: set,
) -> tuple[list[dict], list[dict]]:
    """Autonomously apply high-confidence Curator corrections to a term list.

    Returns (new_terms, changes). Each change is a reversible record:
    {slug, kind, field, before, after}. ``reject`` verdicts are NOT auto-
    removed here (dropping a term is destructive — that stays operator-gated);
    we apply only the safe, reversible fixes: a confident ``better_category``
    (in the declared set) and stripping ``bad_edges`` the judge flagged.
    Pure — no model calls, no I/O. The caller re-gates + reseals.
    """
    by_slug = {t["slug"]: t for t in terms}
    changes: list[dict] = []
    for f in flags:
        t = by_slug.get(f.slug)
        if t is None:
            continue
        if f.better_category and f.better_category in declared and f.better_category != t.get("category"):
            changes.append({"slug": f.slug, "kind": "recategorize", "field": "category",
                            "before": t.get("category"), "after": f.better_category, "note": f.note})
            t["category"] = f.better_category
        if f.bad_edges:
            kept = [e for e in (t.get("related") or []) if e not in f.bad_edges]
            if kept != (t.get("related") or []):
                changes.append({"slug": f.slug, "kind": "unlink", "field": "related",
                                "before": list(t.get("related") or []), "after": kept, "note": f.note})
                t["related"] = kept
    return terms, changes


def _grow_source_tag(resp: Any) -> str:
    """`model:<name>` from a growth response's backend model. Keeps growth
    terms honestly model-asserted (promotable to sourced later)."""
    model = str(getattr(resp, "model", "") or "").lower()
    model = model.replace(":latest", "").replace(":", "/") or "local"
    return f"model:{model}"


def propose_new_terms(
    spec: dict,
    terms: list[dict],
    *,
    router: Any,
    limit: int = 8,
    cancel: Optional[threading.Event] = None,
) -> list[dict]:
    """Ask a (deep, when available) model which important terms are MISSING
    from this World's glossary, then draft + define + link them.

    This is the *growth* half of the organic loop: a newly prominent concept
    (a fresh formula, a new technique) that isn't in the World yet gets added
    as ``model-asserted`` — honestly labeled, later promotable to ``sourced``
    by the autoresearch loop. Returns a list of new term dicts (closed-linked
    to the existing set + each other). Never raises into the caller.
    """
    subject = str(spec.get("display_name") or spec.get("slug") or "")
    declared = [str(c.get("id", "")) for c in spec.get("categories", []) if isinstance(c, dict)]
    existing = {t["slug"] for t in terms}
    roster = ", ".join(sorted(t["term"] for t in terms)[:400])

    ask = (
        f'Subject: "{subject}". This World already covers these concepts:\n{roster}\n\n'
        f"Name up to {max(1, limit)} IMPORTANT, well-established concepts in this subject "
        f"that are MISSING from the list above (do not repeat any). Prefer foundational or "
        f"newly-significant terms. Return JSON array \"terms\", each "
        f'{{"term":"short name","category":"one of: {", ".join(declared)}"}}.'
    )
    try:
        resp = router.complete(ask, max_tokens=500, temperature=0.3)
    except Exception as e:  # noqa: BLE001
        _log.warning("dac_world.reconcile.propose_new_terms: ask failed: %s", e)
        return []
    proposed = first_array(loose_json(getattr(resp, "text", "") or ""))

    new: list[dict] = []
    new_slugs: set = set()
    default_cat = declared[0] if declared else "core-concepts"
    for item in proposed[: max(1, limit)]:
        if cancel is not None and cancel.is_set():
            break
        if not isinstance(item, dict):
            continue
        name = str(item.get("term") or "").strip()
        slug = slugify(name)
        cat = slugify(str(item.get("category") or ""))
        if cat not in declared:
            cat = default_cat
        if not slug or slug in existing or slug in new_slugs:
            continue
        # DEFINE (reuse the forge prompt shape)
        try:
            d = router.complete(
                f'Subject: "{subject}". Define the concept "{name}" as JSON: '
                f'{{"short":"one line","definition":"2-3 sentences","example":"one concrete example"}}.',
                max_tokens=300, temperature=0.2)
        except Exception:  # noqa: BLE001
            continue
        dj = loose_json(getattr(d, "text", "") or "")
        if not isinstance(dj, dict):
            continue
        short = str(dj.get("short") or "")[:MAX_SHORT]
        definition = str(dj.get("definition") or short or name)[:MAX_DEFINITION]
        if len(short.strip()) < 3:
            short = definition[:MAX_SHORT]
        new.append({
            "slug": slug, "term": name, "category": cat,
            "short": short, "definition": definition,
            "example": str(dj.get("example") or "")[:MAX_EXAMPLE],
            "related": [], "source": _grow_source_tag(resp),
        })
        new_slugs.add(slug)

    # LINK new terms into the whole (existing + new) set — closed by construction.
    all_slugs = existing | new_slugs
    if new:
        roster2 = ", ".join(f"{t['slug']} ({t['term']})" for t in (terms + new))
        for t in new:
            if cancel is not None and cancel.is_set():
                break
            try:
                r = router.complete(
                    f'Subject: "{subject}". From THIS list of known concepts:\n{roster2}\n'
                    f'Return JSON array "related" of the slugs most directly associated with '
                    f'"{t["term"]}" (up to {MAX_RELATED_PER_TERM}, choose ONLY slugs from the list, '
                    f'exclude "{t["slug"]}").', max_tokens=200, temperature=0.1)
            except Exception:  # noqa: BLE001
                continue
            rel: list[str] = []
            for x in first_array(loose_json(getattr(r, "text", "") or ""))[:MAX_RELATED_PER_TERM]:
                rs = slugify(str(x.get("slug") if isinstance(x, dict) else x))
                if rs in all_slugs and rs != t["slug"] and rs not in rel:
                    rel.append(rs)
            t["related"] = rel
    return new


def reconcile_terms(
    spec: dict,
    terms: list[dict],
    *,
    router: Any,
    limit: int = 16,
    cancel: Optional[threading.Event] = None,
) -> list[ReviewFlag]:
    """The Curator's review: a (deeper, when available) model judges terms.

    Returns flags ONLY for terms that need attention (correct/reject) —
    accepted terms produce no flag. Advisory sidecar data; never sealed.
    """
    subject = str(spec.get("display_name") or spec.get("slug") or "")
    cats = ", ".join(str(c.get("id", "")) for c in spec.get("categories", []))
    declared = {str(c.get("id", "")) for c in spec.get("categories", [])}
    flags: list[ReviewFlag] = []

    for t in terms[: max(1, limit)]:
        if cancel is not None and cancel.is_set():
            break
        prompt = (
            f'Subject: "{subject}". Declared categories: {cats}.\n'
            f"A smaller model drafted this term. Judge it. Return JSON:\n"
            f'{{"correct": true|false, "category_ok": true|false, '
            f'"better_category": "id-or-empty", '
            f'"bad_edges": ["slugs in related[] that are NOT really associated"], '
            f'"note": "<=12 words"}}\n\n'
            f'term: "{t.get("term", "")}"  category: "{t.get("category", "")}"  '
            f'definition: "{str(t.get("definition", ""))[:400]}"  '
            f"related: {json.dumps(t.get('related') or [])}"
        )
        try:
            resp = router.complete(prompt, max_tokens=300, temperature=0.1)
        except Exception as e:  # noqa: BLE001
            _log.warning("dac_world.reconcile: judge call failed for %s: %s", t.get("slug"), e)
            continue
        verdict = loose_json(getattr(resp, "text", "") or "")
        if not isinstance(verdict, dict):
            continue
        correct = bool(verdict.get("correct", True))
        cat_ok = bool(verdict.get("category_ok", True))
        better = slugify(str(verdict.get("better_category") or ""))
        if better not in declared:
            better = ""
        related = set(t.get("related") or [])
        bad_edges = [s for s in (slugify(str(x)) for x in (verdict.get("bad_edges") or [])
                                 if isinstance(x, str)) if s in related]
        if correct and cat_ok and not bad_edges:
            continue  # accepted — no flag
        flags.append(ReviewFlag(
            slug=str(t.get("slug", "")),
            verdict="reject" if not correct else "correct",
            better_category=better if not cat_ok else "",
            bad_edges=bad_edges,
            note=str(verdict.get("note") or "")[:120],
        ))
    return flags


def goal_suggestions(spec: dict, tier: str) -> list[str]:
    """World-derived study goals — pure function over the mounted spec."""
    display = str(spec.get("display_name") or spec.get("slug") or "this World")
    out = [f"Study {display}: verify and deepen the glossary — find sources for "
           f"{'dreamed' if tier != 'sourced' else 'under-cited'} terms."]
    for c in spec.get("categories", [])[:4]:
        label = str(c.get("label") or c.get("id", "")) if isinstance(c, dict) else str(c)
        if label:
            out.append(f"Deepen {label}: add sourced examples and new terms in {display}.")
    return out
