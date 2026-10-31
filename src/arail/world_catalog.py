"""DaC World catalog reads — deterministic approved-term pulls.

Salvaged from ``arail.build.world_corpus`` (sprints/2026-09-23-nucleus-sprint-1,
ARCHITECTURE.md §8): the pull/read-only half of that module, with the
KICE/docker-Nucleus synthesis orchestration (``build_world_corpus``,
``term_to_kice_example``, ``_infer_layer``, ``chunk``, ``tag_source``,
``_synthesize_all``) left behind and deleted along with ``/build``.

This is neutral, deterministic World-catalog reading, used today by
``compiled_kb`` and, in a future sprint, by Model Forge's reserved
``world:`` corpus source id (the adapter itself is a seam — not built
this sprint).

Two approval layers matter here (see docs/persistence.md-adjacent design
notes in the original World-corpus plan): DaC's gate proves *form*;
ARAIL's Compiled-KB gate (``arail.compiled_kb``) proves *retrieval
eligibility*. This module only trusts the second — a term must be in
``compiled_kb.approved_paths()`` to be pulled, regardless of how
confidently DaC sourced it.

Content survives remounting a different World: ``world_mount.mount()``
sweeps the *staged* KB markdown for every non-current World, but the
bundle is also copied byte-for-byte into ``WORLDS_DIR/<slug>/`` (the
switcher catalog) — this module reads terms from THAT copy, not from
staged markdown, so a World does not need to stay mounted once its
terms are approved.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# The craft/technique categories a "domain expert" bake should train on —
# excludes business/business-entity/web-platform/session-workflow, which are
# operator- or client-specific rather than general domain expertise.
CRAFT_CATEGORIES = ("genres", "gear", "exposure", "light", "composition",
                    "post-production")


def _safe_term_slug(raw: Any) -> str:
    """Mirror arail.compiled_kb._safe_term_slug / world_mount._safe_term_slug
    exactly — the staged term page filenames (and therefore approved_paths
    entries) were written with this sanitizer; reconstructing the path any
    other way risks silently missing approved terms with unusual slug
    characters. Parity is enforced by
    tests/test_qa6_security_gate.py::test_slug_sanitizer_parity_with_world_mount_and_world_corpus."""
    return re.sub(r"[^a-z0-9-]+", "-", str(raw).lower()).strip("-")[:80]


# ── bundle resolution ──────────────────────────────────────────────

def resolve_world_bundle(world_slug: str,
                         worlds_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Read terms.json + spec.json from the switcher catalog copy —
    WORLDS_DIR/<slug>/ — which persists independent of which World is
    currently mounted (see module docstring)."""
    if worlds_dir is None:
        from arail.config import WORLDS_DIR
        worlds_dir = Path(WORLDS_DIR)
    bundle_dir = worlds_dir / world_slug
    terms_path = bundle_dir / "terms.json"
    spec_path = bundle_dir / "spec.json"
    if not terms_path.exists():
        raise FileNotFoundError(
            f"no World bundle at {bundle_dir} — mount it at least once "
            f"(./arailctl world mount <bundle-dir>) so it's adopted into "
            f"the catalog")
    terms_data = json.loads(terms_path.read_text())
    spec_data = json.loads(spec_path.read_text()) if spec_path.exists() else {}
    terms = terms_data.get("terms", terms_data if isinstance(terms_data, list) else [])
    return {"terms": terms, "spec": spec_data, "bundle_dir": bundle_dir}


def all_categories(world_slug: str,
                   worlds_dir: Optional[Path] = None) -> List[str]:
    """Every category id declared in this World's own spec.json, in spec
    order — the generalized "nothing specified" default. Replaces a fixed
    tuple like CRAFT_CATEGORIES (which encodes a photography-specific
    judgment call and is wrong for every other World) with whatever THIS
    World actually declares."""
    bundle = resolve_world_bundle(world_slug, worlds_dir=worlds_dir)
    return [c.get("id") for c in bundle["spec"].get("categories", [])
            if isinstance(c, dict) and c.get("id")]


def category_breakdown(
    world_slug: str, *,
    worlds_dir: Optional[Path] = None,
    pkb_root: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Per-category term counts for a build-scope picker: spec order, each
    entry {id, label, term_count, approved_count}. Pure aggregation over
    terms.json + compiled_kb.approved_paths() — no new approval semantics,
    just counting what pull_approved_terms would otherwise return as full
    term objects. approved_count fails closed to 0 (via approved_paths'
    own fail-closed behavior) rather than raising, so a KB read error never
    crashes the picker — it just shows nothing as approved yet."""
    from arail import compiled_kb

    bundle = resolve_world_bundle(world_slug, worlds_dir=worlds_dir)
    approved = compiled_kb.approved_paths(pkb_root=pkb_root)

    total_by_cat: Dict[str, int] = {}
    approved_by_cat: Dict[str, int] = {}
    for term in bundle["terms"]:
        if not isinstance(term, dict):
            continue
        cat = term.get("category", "")
        total_by_cat[cat] = total_by_cat.get(cat, 0) + 1
        slug = _safe_term_slug(term.get("slug", ""))
        if not slug:
            continue
        rel_path = f"sources/world-{world_slug}/terms/{slug}.md"
        if rel_path in approved:
            approved_by_cat[cat] = approved_by_cat.get(cat, 0) + 1

    out: List[Dict[str, Any]] = []
    for cat in bundle["spec"].get("categories", []):
        if not isinstance(cat, dict) or not cat.get("id"):
            continue
        cid = cat["id"]
        out.append({
            "id": cid,
            "label": cat.get("label") or cid,
            "term_count": total_by_cat.get(cid, 0),
            "approved_count": approved_by_cat.get(cid, 0),
        })
    return out


# ── approved + filtered pull ────────────────────────────────────────

def pull_approved_terms(
    world_slug: str, *,
    categories: Iterable[str] = CRAFT_CATEGORIES,
    worlds_dir: Optional[Path] = None,
    pkb_root: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Deterministic pull: every term in WORLDS_DIR/<slug>/terms.json whose
    category is in *categories* AND whose staged-page path is in
    compiled_kb.approved_paths() — never a fuzzy/semantic search, since a
    training corpus needs complete, reproducible coverage, not top-K.

    Sorted by the World's own spec.json category order, then by slug within
    a category, mirroring the order world_mount stages term pages in.
    """
    from arail import compiled_kb

    bundle = resolve_world_bundle(world_slug, worlds_dir=worlds_dir)
    approved = compiled_kb.approved_paths(pkb_root=pkb_root)
    cat_set = set(categories)
    spec_categories = [c.get("id") for c in bundle["spec"].get("categories", [])
                       if isinstance(c, dict) and c.get("id")]
    cat_order = {c: i for i, c in enumerate(spec_categories)}

    out: List[Dict[str, Any]] = []
    for term in bundle["terms"]:
        if not isinstance(term, dict):
            continue
        category = term.get("category", "")
        if category not in cat_set:
            continue
        slug = _safe_term_slug(term.get("slug", ""))
        if not slug:
            continue
        rel_path = f"sources/world-{world_slug}/terms/{slug}.md"
        if rel_path not in approved:
            continue
        out.append(term)

    out.sort(key=lambda t: (cat_order.get(t.get("category", ""), 999),
                            _safe_term_slug(t.get("slug", ""))))
    return out
