"""The sealer -- port of DDaC's ``scripts/export-bundle.mts``.

Moved from qukaizen-arail's ``src/arail/world_forge.py`` as part of the
``dac_world`` migration — see
``sprints/2026-07-19-dac-generates-arail-worlds/ARCHITECTURE.md`` (qukaizen-dac).

**Delta from the moved code:** the original ``_build_face`` did
``from arail.world_theme import parse_world_theme`` inline to hard-validate
a ``theme`` face override. That is a literal ``import arail`` and is
forbidden inside this package (Failure F4). ``theme_validator`` is now an
injected callable — ``Callable[[Any], tuple[Optional[Any], str]]`` matching
``parse_world_theme``'s ``(spec_or_None, reason)`` contract — threaded through
``write_bundle``/``reseal_bundle``. If a ``theme`` override is present and no
validator was injected, sealing fails closed with ``ValueError`` (same
"validated HARD before sealing" stance as before — an un-injected validator
is a caller bug, not a reason to skip validation).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .gate import GateRefused, assert_closed_sourced_graph
from .provenance import compute_provenance_tier
from .skill import _skill_terms_capped, render_world_skill
from .validate import validate_bundle_content

_log = logging.getLogger(__name__)

BUNDLE_SCHEMA = "dac.world-bundle/v1"
SEALED_FILES = ("terms.json", "spec.json", "roster.json", "face.json",
                "agenda.json", "drift-report.json")

# Files write_bundle emits itself; everything else in a bundle dir is a
# sidecar to carry over verbatim during reseal. Derived from SEALED_FILES
# (the same constant write_bundle iterates over) plus the fixed set of
# always-regenerated non-sealed outputs, so this cannot drift independently
# of what write_bundle actually writes.
REGENERATED_FILES = frozenset(SEALED_FILES) | {
    "manifest.json", "SKILL.md", "capabilities.json", "arail-plugin.json",
}
# Sidecars we know about — presence is expected, no warning. Unknown
# survivors are still preserved (see reseal_bundle), but warned about so a
# stray/misnamed file fails loud instead of silent.
KNOWN_SIDECARS = frozenset({
    "model.json", "review.json", "evolution.json", "librarian-scout.json",
})

Theme_Validator = Callable[[Any], "tuple[Optional[Any], str]"]

_FRAMING_BY_TIER = {
    "sourced": "Every factual claim is grounded in the World's cited sources.",
    "mixed": "Some terms are model-asserted (unverified); cite a source when promoting them.",
    "model-asserted": "This World was DREAMED by a model — terms are model-asserted and UNVERIFIED.",
}

# Authored/display fields a caller may override (mirrors DDaC's allow-list).
_FACE_DISPLAY_KEYS = ("name", "tagline", "domain_framing", "vocabulary_register",
                      "palette_hint", "theme")


def _cmp_key(s: str) -> str:
    return s.casefold()


def _pretty(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _build_face(spec: dict, tier: str, counts: dict, overrides: Optional[dict],
                 theme_validator: Optional[Theme_Validator] = None) -> dict:
    slug = str(spec.get("slug", ""))
    display = str(spec.get("display_name", slug))
    dreamed = " (dreamed)" if tier == "model-asserted" else ""
    declared = ", ".join(str(c.get("id", "")) for c in spec.get("categories", []))
    face: dict = {
        "schema": "dac.world-face/v1",
        "world": slug,
        "name": display,
        "tagline": f"A {display} World{dreamed}.",
        "palette_hint": "slate-violet",
        "domain_framing": (f"This lab studies {display}. {_FRAMING_BY_TIER.get(tier, _FRAMING_BY_TIER['model-asserted'])} "
                           f"Hypotheses and answers stay within the declared categories ({declared})."),
        "vocabulary_register": "Use the World's own terms; cite a source for every factual claim.",
        "provenance_tier": tier,
        "provenance_counts": counts,
    }
    for key in _FACE_DISPLAY_KEYS:
        if overrides and overrides.get(key) is not None:
            if key == "theme":
                # A theme block is validated HARD before sealing (same stance
                # as DDaC's exporter): a sealed bundle must never carry a theme
                # ARAIL's own mount-time validator would reject. The validator
                # itself is INJECTED (never imported) — see module docstring.
                if theme_validator is None:
                    raise ValueError(
                        "face theme invalid: no theme_validator injected "
                        "(a theme override requires the caller to pass one)"
                    )
                theme_spec, reason = theme_validator(overrides[key])
                if theme_spec is None:
                    raise ValueError(f"face theme invalid: {reason}")
                face["theme"] = overrides[key]
            else:
                face[key] = str(overrides[key])
    # Integrity fields force-derived LAST — authored copy can never assert provenance.
    face["schema"] = "dac.world-face/v1"
    face["world"] = slug
    face["provenance_tier"] = tier
    face["provenance_counts"] = counts
    return face


def _build_capabilities(spec: dict, tier: str, terms: list[dict], world_sha: str) -> dict:
    slug = str(spec.get("slug", ""))
    display = str(spec.get("display_name", slug))
    count_by_cat: dict[str, int] = {}
    for t in terms:
        cat = str(t.get("category", ""))
        if cat:
            count_by_cat[cat] = count_by_cat.get(cat, 0) + 1
    cats = sorted(
        (c for c in spec.get("categories", [])
         if isinstance(c, dict) and count_by_cat.get(str(c.get("id", "")), 0) > 0),
        key=lambda c: _cmp_key(str(c.get("id", ""))),
    )
    cat_ids = [str(c["id"]) for c in cats]
    caps = [{
        "id": f"knowledge.ground.{slug}",
        "purpose": f"Ground claims about {display} in the World's gated glossary.",
        "desired": True,
        "interface": {"kind": "knowledge-grounding", "world": slug, "world_sha256": world_sha,
                      "categories": cat_ids, "term_count": len(terms), "provenance_tier": tier},
    }]
    for c in cats:
        cid = str(c["id"])
        caps.append({
            "id": f"knowledge.ground.{slug}.{cid}",
            "purpose": f"Ground claims about {c.get('label', cid)} in {display}.",
            "desired": True,
            "interface": {"kind": "knowledge-grounding", "world": slug, "category": cid,
                          "term_count": count_by_cat[cid]},
        })
    return {"schema": "dac.world-capabilities/v1", "world": slug, "capabilities": caps}


def _build_plugin_manifest(slug: str, display: str, term_count: int, world_sha: str) -> dict:
    return {
        "name": f"qukaizen/dac-world-{slug}",
        "type": "world",
        "description": f"DaC WorldBundle for {display} — {term_count} terms, mountable in ARAIL.",
        "version": "1.0.0",
        "dac": {
            "schema": "dac.arail-plugin/v1",
            "world": slug,
            "world_sha256": world_sha,
            "bundle": ".",
            "provides": {"capabilities": "capabilities.json", "skill": "SKILL.md",
                         "bundle_manifest": "manifest.json"},
        },
    }


def write_bundle(
    out_dir: Path,
    spec: dict,
    terms: list[dict],
    *,
    face_overrides: Optional[dict] = None,
    roster: Optional[dict] = None,
    created_at: Optional[str] = None,
    theme_validator: Optional[Theme_Validator] = None,
) -> Path:
    """Write a sealed ``dac.world-bundle/v1`` that round-trips ARAIL's own
    load_bundle + verify_seal + check_compat + check_categories.

    Gate-refuses an invalid corpus (a sealer that sealed unsourced/dangling
    terms would defeat the whole point). Refuses placeholder-shaped content
    (``ContentInvalid``) before any file is written.
    """
    out_dir = Path(out_dir)
    slug = str(spec.get("slug", ""))
    display = str(spec.get("display_name", slug))
    declared = {str(c.get("id", "")) for c in spec.get("categories", []) if isinstance(c, dict)}

    gate = assert_closed_sourced_graph(terms, declared)
    if not gate.ok:
        raise GateRefused(gate)
    tier, counts = compute_provenance_tier([t.get("source") for t in terms])

    face = _build_face(spec, tier, counts, face_overrides, theme_validator)
    validate_bundle_content(face, spec, terms)
    agenda = {
        "schema": "dac.world-agenda/v1",
        "world": slug,
        "watches": [
            {"node": slug, "feeds": [str(s.get("ref") or s.get("holder") or "source")],
             "cadence": "occasional"}
            for s in (spec.get("knowledge_sources") or [])[:3] if isinstance(s, dict)
        ],
    }
    drift = {
        "schema": "dac.world-drift/v1",
        "world": slug,
        "declared": sorted(str(t.get("slug", "")) for t in terms if t.get("slug")),
        "missing": [],
        "extra": [],
        "ok": True,
    }
    roster_doc = roster or {"schema": "dac.world-roster/v1", "world": slug,
                            "desired": [str(t.get("slug", "")) for t in terms]}

    out_dir.mkdir(parents=True, exist_ok=True)
    sealed_bytes: dict[str, bytes] = {
        "terms.json": _pretty({"version": 1, "terms": terms}),
        "spec.json": _pretty(spec),
        "roster.json": _pretty(roster_doc),
        "face.json": _pretty(face),
        "agenda.json": _pretty(agenda),
        "drift-report.json": _pretty(drift),
    }
    files: dict[str, str] = {}
    for name, raw in sealed_bytes.items():
        (out_dir / name).write_bytes(raw)
        files[name] = hashlib.sha256(raw).hexdigest()
    world_sha = files["terms.json"]

    _skill_terms, _skill_note = _skill_terms_capped(terms)
    (out_dir / "SKILL.md").write_text(
        render_world_skill(spec, face, _skill_terms, world_sha, extra_note=_skill_note),
        encoding="utf-8", newline="\n")
    (out_dir / "capabilities.json").write_bytes(
        _pretty(_build_capabilities(spec, tier, terms, world_sha)))
    (out_dir / "arail-plugin.json").write_bytes(
        _pretty(_build_plugin_manifest(slug, display, len(terms), world_sha)))

    manifest = {
        "schema": BUNDLE_SCHEMA,
        "bundle_version": 1,
        "world": slug,
        "display_name": display,
        "created_at": created_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "world_sha256": world_sha,
        "files": files,
        "provenance_tier": tier,
        "provenance_counts": counts,
        "refresh_cadence": "see agenda.json",
        "compat": {"bundle_schema": 1, "terms_schema": 1},
    }
    (out_dir / "manifest.json").write_bytes(_pretty(manifest))
    return out_dir


def reseal_bundle(bundle_dir: Path, terms: Optional[list[dict]] = None, *,
                   theme_validator: Optional[Theme_Validator] = None) -> Path:
    """Re-seal a bundle after a terms edit: re-derive everything downstream of
    terms.json (tier/counts, drift, SKILL.md, capabilities, plugin manifest,
    manifest hashes) while preserving authored display fields (name, tagline,
    domain_framing, vocabulary_register, palette_hint, theme) and the roster
    wish-list verbatim. Atomic: builds a sibling temp dir, then swaps.
    """
    bundle_dir = Path(bundle_dir)
    spec = json.loads((bundle_dir / "spec.json").read_bytes())
    old_manifest = json.loads((bundle_dir / "manifest.json").read_bytes())
    if terms is None:
        terms = json.loads((bundle_dir / "terms.json").read_bytes()).get("terms", [])
    roster = None
    if (bundle_dir / "roster.json").exists():
        try:
            roster = json.loads((bundle_dir / "roster.json").read_bytes())
        except Exception:  # noqa: BLE001
            roster = None
    overrides: dict = {}
    if (bundle_dir / "face.json").exists():
        try:
            old_face = json.loads((bundle_dir / "face.json").read_bytes())
            overrides = {k: old_face.get(k) for k in _FACE_DISPLAY_KEYS if old_face.get(k) is not None}
        except Exception:  # noqa: BLE001
            overrides = {}

    # F1: reseal preserves display fields verbatim — validate THEM before
    # re-sealing, or garbage content (e.g. XXXX/YYYY) survives every reseal.
    validate_bundle_content(overrides, spec, terms)

    tmp = bundle_dir.parent / f".{bundle_dir.name}.reseal-tmp"
    old = bundle_dir.parent / f".{bundle_dir.name}.reseal-old"
    for leftover in (tmp, old):
        if leftover.exists():
            shutil.rmtree(leftover)
    write_bundle(tmp, spec, terms, face_overrides=overrides, roster=roster,
                 created_at=str(old_manifest.get("created_at") or "") or None,
                 theme_validator=theme_validator)
    # Carry over every file the sealer does not regenerate (sidecars + any
    # nested state) — not a fixed name list, so a future sidecar-producing
    # feature can never lose its file on first reseal by forgetting to edit
    # this module. See ARCHITECTURE.md's sidecar-preservation addendum.
    for entry in bundle_dir.iterdir():
        if entry.name in REGENERATED_FILES:
            continue
        if entry.name not in KNOWN_SIDECARS:
            _log.warning(
                "dac_world.seal.reseal_bundle: preserving unrecognized bundle "
                "file %r in %s (not a regenerated output; carried over "
                "verbatim)", entry.name, bundle_dir.name)
        dest = tmp / entry.name
        if entry.is_dir():
            shutil.copytree(entry, dest)
        else:
            shutil.copy2(entry, dest)
    os.rename(bundle_dir, old)
    try:
        os.rename(tmp, bundle_dir)
    except Exception:
        os.rename(old, bundle_dir)  # roll back — never leave the slug missing
        raise
    shutil.rmtree(old)
    return bundle_dir
