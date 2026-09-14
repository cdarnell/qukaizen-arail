"""World Forge — thin re-export shim over ``dac_world``.

The pure forge/gate/provenance/seal/skill/reconcile core that used to live
here has moved to DaC's shared ``dac_world`` package (an intentional,
human-approved reversal of the prior "no cross-repo runtime imports" stance —
see ``qukaizen-ddac/sprints/2026-07-19-dac-generates-arail-worlds/
ARCHITECTURE.md`` for the full rationale, assumptions, and failure modes;
``BUILD_LOG.md`` in that same directory for what actually moved and why).
``dac_world`` now ships as a vendored copy at ``src/dac_world`` rather than a
pinned git dependency on the private ``qukaizen-ddac`` repo — see
``docs/adr/0004-vendor-dac-world-for-offline-friendly-setup.md`` for why.

This module re-exports exactly the public (and two underscore-prefixed but
externally-depended-on) names that ARAIL's portal, ``librarian_scout``,
``world_sources/wikipedia.py``, ``world_mount``, and the test suite import —
grepped from every ``wf.<name>`` / ``from arail.world_forge import <name>``
call site before writing this list (BUILD_LOG.md step 6), not guessed.

What stays ARAIL-side (never moves to ``dac_world``): ``arail.router``
construction, ``inference_slot``/``asyncio.to_thread`` wrapping, cancel-event
plumbing, progress-to-SSE, and ``arail.world_theme.parse_world_theme`` (the
mount-time theme schema) — the latter is now INJECTED into
``write_bundle``/``reseal_bundle`` as ``theme_validator`` by callers in this
repo (see ``arail/portal/world_routes.py``) rather than imported from inside
``dac_world`` (Failure F4: no ``import arail`` may appear in ``dac_world``).
"""

from __future__ import annotations

from dac_world import (
    BUNDLE_SCHEMA,
    FORGE_STAGES,
    MAX_DEFINITION,
    MAX_EXAMPLE,
    MAX_RELATED_PER_TERM,
    MAX_SHORT,
    MAX_TERMS_SOFT,
    SEALED_FILES,
    SKILL_CHAR_BUDGET,
    SKILL_CHARS_PER_TERM,
    ContentInvalid,
    ForgeCancelled,
    ForgeParams,
    ForgeResult,
    GateRefused,
    GateResult,
    ProgressCb,
    ReviewFlag,
    _skill_terms_capped,
    _source_tag_from_model,
    apply_corrections,
    assert_closed_sourced_graph,
    compute_provenance_tier,
    estimate_skill_chars,
    first_array,
    forge_world,
    goal_suggestions,
    loose_json,
    propose_new_terms,
    reconcile_terms,
    render_world_skill,
    reseal_bundle,
    sanitize_body_field,
    sanitize_frontmatter_scalar,
    slugify,
    tier_of_source,
    validate_bundle_content,
    write_bundle,
)

__all__ = [
    "BUNDLE_SCHEMA", "FORGE_STAGES", "MAX_DEFINITION", "MAX_EXAMPLE",
    "MAX_RELATED_PER_TERM", "MAX_SHORT", "MAX_TERMS_SOFT", "SEALED_FILES",
    "SKILL_CHAR_BUDGET", "SKILL_CHARS_PER_TERM",
    "ContentInvalid", "ForgeCancelled", "ForgeParams", "ForgeResult",
    "GateRefused", "GateResult", "ProgressCb", "ReviewFlag",
    "_skill_terms_capped", "_source_tag_from_model",
    "apply_corrections", "assert_closed_sourced_graph",
    "compute_provenance_tier", "estimate_skill_chars", "first_array",
    "forge_world", "goal_suggestions", "loose_json", "propose_new_terms",
    "reconcile_terms", "render_world_skill", "reseal_bundle",
    "sanitize_body_field", "sanitize_frontmatter_scalar", "slugify",
    "tier_of_source", "validate_bundle_content", "write_bundle",
]
