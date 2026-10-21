"""SKILL.md renderer -- port of DDaC's ``src/arail-export/skill.ts``.

Moved verbatim from qukaizen-arail's ``src/arail/world_forge.py`` as part of
the ``dac_world`` migration — see
``sprints/2026-07-19-dac-generates-arail-worlds/ARCHITECTURE.md`` (qukaizen-dac).
"""

from __future__ import annotations

import re
from typing import Optional

# skills_loader body cap is 56K chars; warn with margin (~300 chars/term).
SKILL_CHAR_BUDGET = 48_000
SKILL_CHARS_PER_TERM = 300

_BODY_CONTROL_RE = re.compile(r"^([#\->`])")


def estimate_skill_chars(n_terms: int) -> int:
    return n_terms * SKILL_CHARS_PER_TERM + 1200


def sanitize_frontmatter_scalar(s: str) -> str:
    """F1: collapse CR/LF, trim, double-quote with internal escapes."""
    flat = re.sub(r"[\r\n]+", " ", str(s)).strip()
    return '"' + flat.replace("\\", "\\\\").replace('"', '\\"') + '"'


def sanitize_body_field(s: str) -> str:
    """F2: collapse CR/LF to a space; ZWNJ-neutralize a leading control token."""
    flat = re.sub(r"[\r\n]+", " ", str(s)).strip()
    return _BODY_CONTROL_RE.sub("‌\\1", flat)


def _cmp_key(s: str) -> str:
    return s.casefold()


def measured_skill_size(t: dict) -> int:
    """The canonical "what a term costs in SKILL.md" measurement (C2,
    sprints/2026-08-27-heavy-world-model/ARCHITECTURE.md). Mirrors the TS
    twin, ``src/size.ts::measureTermSize``, byte-for-byte — parity is
    asserted on a shared vector (tests/python/test_measured_skill_size.py +
    tests/size.test.ts both read
    tests/fixtures/measure-term-size-vectors.json).

    Deliberately NOT ``len(short)+len(definition)+len(example)`` (that
    payload is ~1.7x larger than what SKILL.md actually renders — the
    premise correction in ARCHITECTURE.md). Returns the UTF-8 byte length of
    the two SKILL.md lines this term renders as (render_world_skill L126-127),
    each including its trailing newline:
        `- **{term}** (`{slug}`) — {short}`
        `  - Source: {source}`

    Pure, total: never throws; a missing field contributes "" to its segment.
    """
    term = str(t.get("term") or "")
    slug = str(t.get("slug") or "")
    short = str(t.get("short") or "")
    source = str(t.get("source") or "")
    line1 = f"- **{term}** (`{slug}`) — {short}"
    line2 = f"  - Source: {source}"
    return len((line1 + "\n").encode("utf-8")) + len((line2 + "\n").encode("utf-8"))


def _related_edge_slug(edge: object) -> str:
    """A related edge is either a bare slug string or a typed {slug, rel}
    dict (RelatedEdge, src/types.ts). Always resolve to the target SLUG for
    degree counting — F5: counting `str(dict)` instead of `.slug` silently
    zeroes inbound degree for every typed edge once one reaches this path."""
    if isinstance(edge, dict):
        return str(edge.get("slug", "")).strip()
    return str(edge).strip()


def _skill_terms_capped(terms: list[dict]) -> tuple[list[dict], Optional[str]]:
    """Pick which terms SKILL.md carries. skills_loader caps the body at ~56K
    chars, so a big World (e.g. 512 fetched terms) can't render every term into
    the agent prompt. Keep the most CONNECTED terms (highest related-degree —
    the concepts the World hangs off of) up to the MEASURED render budget
    (measured_skill_size, C2 — not the flat SKILL_CHARS_PER_TERM estimate,
    which overcounts real SKILL-line cost ~1.7x and needlessly drops terms a
    World's real render would have fit), and return an honest note so agents
    know the full glossary lives in the Knowledge Base. Small Worlds render
    whole (note=None).

    OVERHEAD is a conservative allowance for frontmatter + framing + category
    headers + rails that aren't per-term. It is NOT trusted blindly for
    correctness: the caller (write_bundle) renders the returned `kept` set for
    real, and callers' tests assert the real rendered body stays under
    SKILL_CHAR_BUDGET (F6) — this function's job is only to pick a kept set
    that SHOULD fit, not to certify that it does.
    """
    OVERHEAD = 1_500  # conservative: frontmatter + framing + category headers + rails
    total = OVERHEAD + sum(measured_skill_size(t) for t in terms)
    if total <= SKILL_CHAR_BUDGET:
        return terms, None

    # Degree = inbound + outbound related edges, counted by SLUG (F5) so a
    # typed {slug,rel} edge counts identically to an equivalent bare-slug one.
    indeg: dict[str, int] = {}
    for t in terms:
        for r in (t.get("related") or []):
            rs = _related_edge_slug(r)
            if rs:
                indeg[rs] = indeg.get(rs, 0) + 1

    def _degree(t: dict) -> int:
        out_deg = sum(1 for r in (t.get("related") or []) if _related_edge_slug(r))
        return out_deg + indeg.get(str(t.get("slug", "")), 0)

    ranked = sorted(terms, key=lambda t: (-_degree(t), str(t.get("slug", ""))))

    # The highest-degree PREFIX that fits the MEASURED budget (C4 Promises) —
    # stop at the first term that would overflow, don't skip ahead to a
    # smaller lower-ranked one (that would silently reorder the "most
    # connected" guarantee the honest note makes). Always keep at least one
    # term from a non-empty over-budget corpus (never an empty SKILL.md for a
    # real World).
    kept: list[dict] = []
    running = OVERHEAD
    for t in ranked:
        cost = measured_skill_size(t)
        if kept and running + cost > SKILL_CHAR_BUDGET:
            break
        running += cost
        kept.append(t)

    note = (f"This World has {len(terms)} terms; the {len(kept)} most connected "
            "are shown here. The full glossary lives in the Knowledge Base.")
    return kept, note


def render_world_skill(spec: dict, face: dict, terms: list[dict], world_sha: str,
                       *, extra_note: Optional[str] = None) -> str:
    """SKILL.md in the exact shape skills_loader parses. Pure projection of
    gated fields (slug/term/short/source) — the honesty rail."""
    slug = str(spec.get("slug", ""))
    display_raw = str(spec.get("display_name", slug))
    display_fm = sanitize_frontmatter_scalar(display_raw)
    display_body = sanitize_body_field(display_raw)
    prov_tier = str(face.get("provenance_tier", ""))

    cat_label = {str(c.get("id", "")): str(c.get("label") or c.get("id", ""))
                 for c in spec.get("categories", []) if isinstance(c, dict)}

    by_cat: dict[str, list[dict]] = {}
    for t in terms:
        by_cat.setdefault(str(t.get("category", "")), []).append(t)
    sorted_cats = sorted(by_cat, key=_cmp_key)
    for cat in sorted_cats:
        by_cat[cat].sort(key=lambda t: _cmp_key(str(t.get("slug", ""))))

    frontmatter = "\n".join([
        "---",
        f"title: {display_fm}",
        f"id: world-{slug}",
        f"name: {display_fm}",
        f"domain: {slug}",
        'version: "1.0.0"',
        f"tags: [world, knowledge, {slug}]",
        "when_to_use:",
        f"  - When the user asks about {display_body} or its declared categories",
        "  - When grounding a claim that falls inside this World's domain",
        "when_not_to_use:",
        "  - When the question is outside this World's declared categories",
        "  - When a claim cannot be tied to one of this World's sourced terms (say so; don't invent)",
        "---",
    ])

    prov_line = (
        "Every term in this World is grounded in a cited source."
        if prov_tier == "sourced"
        else "Some terms are model-asserted (unverified); cite a source when promoting them."
        if prov_tier == "mixed"
        else "This World was DREAMED by a model — terms are model-asserted and UNVERIFIED."
    )
    rail_line = ("Answer only from the terms below. Every term lists its source. "
                 "If a question cannot be answered from these terms, say the World "
                 "does not cover it — do not invent.")

    sections: list[str] = []
    for cat in sorted_cats:
        lines: list[str] = []
        for t in by_cat[cat]:
            term_safe = sanitize_body_field(str(t.get("term", "")))
            slug_safe = sanitize_body_field(str(t.get("slug", "")))
            short_safe = sanitize_body_field(str(t.get("short", "")))
            source_safe = sanitize_body_field(str(t.get("source", "")))
            lines.append(f"- **{term_safe}** (`{slug_safe}`) — {short_safe}")
            lines.append(f"  - Source: {source_safe}")
        label = sanitize_body_field(cat_label.get(cat, cat))
        sections.append(f"### {label}\n\n" + "\n".join(lines))

    body_parts = [
        sanitize_body_field(str(face.get("domain_framing", ""))),
        "",
        prov_line,
    ]
    if extra_note:
        body_parts += ["", f"_{sanitize_body_field(extra_note)}_"]
    body_parts += [
        "",
        f"_{rail_line}_",
        "",
        "\n\n".join(sections),
        "",
        f"<!-- dac:world_sha256 {world_sha} -->",
    ]
    body = "\n".join(body_parts)
    return frontmatter + "\n" + body + "\n"
