#!/usr/bin/env python3
"""Forge the sealed "Video Games" World bundle from its authored inputs.

Authoring inputs live in ``scripts/worlds_src/video-games/`` and are the
reviewable source of truth; the sealed bundle in ``lab/worlds/video-games/``
is this script's output and is committed alongside them.

Unlike the ``ai`` and ``qukaizen`` defaults — which are authored and sealed
upstream in the sibling qukaizen-ddac repo and vendored in — this World is
authored IN THIS REPO and sealed by the same shared ``dac_world`` sealer
(re-exported as ``arail.world_forge``). Same format, same seal, no vendoring.

Run it from the repo root::

    PYTHONPATH=src python scripts/forge_video_games_world.py

The run is deterministic: with unchanged inputs it reproduces byte-identical
output, so a second run leaves the working tree clean.

CAVEAT — the sealer regenerates ``capabilities.json`` and ``SKILL.md`` on every
seal, emitting only knowledge-grounding capabilities and the plain glossary
projection. The declared Layer-B capabilities and the authored "Research
method" persona section are therefore merged in AFTER sealing (both files are
seal-exempt, so this does not disturb the seal). A portal term edit reseals the
bundle and would regenerate them away — re-run this script to restore them.
``tests/test_default_worlds_catalog.py`` asserts both merges survive, so an
accidental regression fails CI rather than shipping silently.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from arail import world_forge as wf
from arail import world_mount as wm
from arail.capabilities.spec import parse_capabilities_file
from arail.world_theme import parse_world_theme

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts" / "worlds_src" / "video-games"
OUT = ROOT / "lab" / "worlds" / "video-games"

# Pinned so re-runs are byte-identical; the sealer would otherwise stamp now().
CREATED_AT = "2026-07-25T00:00:00.000Z"

PERSONA_ANCHOR = "_Answer only from the terms below."
PERSONA_HEADING = "### Research method"

# Numeric performance claims are forbidden (truth-in-UI: measured or it does not
# exist). Definitional numbers are fine, but each must be justified here by slug
# so that adding one is a deliberate act, not an oversight.
NUMBER_ALLOWLIST: set[tuple[str, str]] = {
    ("monitor-refresh-rate", "example"),  # definitional: 144 Hz shows up to 144 frames
}
_FPS_RE = re.compile(r"\b\d+(?:\.\d+)?\s*fps\b", re.I)
_CLAIM_RE = re.compile(r"\b(gets|achieves|scores|averages|hits|reaches)\b.*\d", re.I)


def _pretty(obj: object) -> str:
    """Match the sealer's own JSON formatting exactly."""
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def _load(name: str) -> object:
    return json.loads((SRC / name).read_text(encoding="utf-8"))


def preflight(spec: dict, terms: list[dict], face: dict) -> list[str]:
    """Every check that can refuse the bundle, reported in one pass."""
    problems: list[str] = []

    declared = [str(c.get("id", "")) for c in spec.get("categories", [])]
    gate = wf.assert_closed_sourced_graph(terms, declared)
    if not gate.ok:
        problems.extend(f"gate: {v}" for v in gate.violations)

    theme_spec, reason = parse_world_theme(face.get("theme"), spec.get("slug", "?"))
    if theme_spec is None:
        problems.append(f"theme: {reason}")

    try:
        wf.validate_bundle_content(face, spec, terms)
    except wf.ContentInvalid as exc:
        problems.append(f"content: {exc}")

    if len(terms) > wf.MAX_TERMS_SOFT:
        problems.append(f"budget: {len(terms)} terms exceeds {wf.MAX_TERMS_SOFT}")
    budgets = (("short", wf.MAX_SHORT), ("definition", wf.MAX_DEFINITION),
               ("example", wf.MAX_EXAMPLE))
    for t in terms:
        slug = t.get("slug", "?")
        for field, limit in budgets:
            if len(str(t.get(field, ""))) > limit:
                problems.append(
                    f"budget: {slug}.{field} is {len(str(t[field]))} chars (max {limit})")
        if len(t.get("related", [])) > wf.MAX_RELATED_PER_TERM:
            problems.append(
                f"budget: {slug} has {len(t['related'])} related "
                f"(max {wf.MAX_RELATED_PER_TERM})")

    tier, counts = wf.compute_provenance_tier([str(t.get("source", "")) for t in terms])
    if tier != "sourced" or counts.get("model"):
        problems.append(f"provenance: expected a fully sourced World, got {tier} {counts}")

    for t in terms:
        slug = t.get("slug", "?")
        for field in ("short", "definition", "example"):
            value = str(t.get(field, ""))
            if (_FPS_RE.search(value) or _CLAIM_RE.search(value)) \
                    and (slug, field) not in NUMBER_ALLOWLIST:
                problems.append(
                    f"numbers: {slug}.{field} reads like a performance claim — "
                    f"measured numbers only, or allowlist it deliberately")

    return problems


def merge_capabilities(extra: list[dict]) -> None:
    """Append the declared Layer-B capabilities the sealer does not emit."""
    path = OUT / "capabilities.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    extra_ids = {c["id"] for c in extra}
    kept = [c for c in doc.get("capabilities", []) if c.get("id") not in extra_ids]
    doc["capabilities"] = kept + extra
    path.write_text(_pretty(doc), encoding="utf-8")


def merge_skill_persona(persona: str) -> None:
    """Insert the authored research-method section into the generated SKILL.md."""
    path = OUT / "SKILL.md"
    body = path.read_text(encoding="utf-8")
    if PERSONA_HEADING in body:
        return
    lines = body.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith(PERSONA_ANCHOR):
            insert_at = i + 1
            break
    else:
        raise SystemExit(
            f"SKILL.md is missing the expected anchor line {PERSONA_ANCHOR!r} — "
            "the sealer's skill renderer changed; update this script.")
    block = "\n" + persona.strip() + "\n"
    path.write_text("".join(lines[:insert_at]) + block + "".join(lines[insert_at:]),
                    encoding="utf-8")


def main() -> int:
    spec = _load("spec.json")
    terms = _load("terms.json")["terms"]
    face = _load("face.json")
    extra_caps = _load("capabilities-extra.json")
    persona = (SRC / "skill-persona.md").read_text(encoding="utf-8")

    problems = preflight(spec, terms, face)
    if problems:
        print(f"refusing to forge — {len(problems)} problem(s):", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    wf.write_bundle(OUT, spec, terms,
                    face_overrides=face,
                    theme_validator=parse_world_theme,
                    created_at=CREATED_AT)
    merge_capabilities(extra_caps)
    merge_skill_persona(persona)

    bundle = wm.load_bundle(OUT)
    seal = wm.verify_seal(bundle)
    if not seal.ok:
        print(f"sealed bundle failed verification: {seal.reason}", file=sys.stderr)
        return 1
    wm.check_compat(bundle)
    wm.check_categories(bundle)

    caps = parse_capabilities_file(OUT / "capabilities.json")
    ids = {c.id for c in caps}
    missing = {c["id"] for c in extra_caps} - ids
    if missing:
        print(f"declared capabilities missing after merge: {sorted(missing)}",
              file=sys.stderr)
        return 1
    if PERSONA_HEADING not in (OUT / "SKILL.md").read_text(encoding="utf-8"):
        print("SKILL.md is missing the research-method section", file=sys.stderr)
        return 1

    manifest = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
    print(f"forged {manifest['world']} — {len(terms)} terms, "
          f"{manifest['provenance_tier']} ({manifest['provenance_counts']})")
    print(f"  world_sha256 {manifest['world_sha256']}")
    print(f"  capabilities {len(ids)} declared ({len(extra_caps)} beyond grounding)")
    print(f"  -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
