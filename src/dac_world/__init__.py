"""dac_world — DDaC's shared, model-free World forge/seal/gate/skill core.

This package is the single generator for ``dac.world-bundle/v1`` bundles.
qukaizen-arail imports it at runtime (an intentional, human-approved
reversal of the prior "no cross-repo runtime imports" stance — see
``sprints/2026-07-19-dac-generates-arail-worlds/ARCHITECTURE.md`` in
qukaizen-dac for the full rationale, assumptions, and failure modes).

Everything under this package is pure and stdlib-only: no model calls of
its own (a caller-supplied ``router`` object is required), no filesystem
writes outside ``write_bundle``/``reseal_bundle``, and — enforced by a
static CI check (``tests/python/test_no_arail_backimport.py``) — no
``import arail`` anywhere in this package. Anything that needs ARAIL's
router, async wrapping, cancel-event plumbing, progress-to-SSE, or the
mount-time theme schema stays in ARAIL and is injected in as a parameter
(see ``seal.write_bundle``'s ``theme_validator``).

This top-level namespace re-exports the full public surface that
qukaizen-arail's ``world_forge.py`` shim depends on (grepped from ARAIL's
portal/librarian_scout/tests call sites — see BUILD_LOG.md step 6).
"""

from __future__ import annotations

from .forge import (
    FORGE_STAGES,
    MAX_DEFINITION,
    MAX_EXAMPLE,
    MAX_RELATED_PER_TERM,
    MAX_SHORT,
    MAX_TERMS_SOFT,
    ForgeCancelled,
    ForgeParams,
    ForgeResult,
    ProgressCb,
    _source_tag_from_model,
    forge_world,
)
from .gate import GateRefused, GateResult, assert_closed_sourced_graph
from .parsing import first_array, loose_json, slugify
from .provenance import compute_provenance_tier, tier_of_source
from .reconcile import (
    ReviewFlag,
    apply_corrections,
    goal_suggestions,
    propose_new_terms,
    reconcile_terms,
)
from .seal import (
    BUNDLE_SCHEMA,
    SEALED_FILES,
    reseal_bundle,
    write_bundle,
)
from .skill import (
    SKILL_CHAR_BUDGET,
    SKILL_CHARS_PER_TERM,
    _skill_terms_capped,
    estimate_skill_chars,
    render_world_skill,
    sanitize_body_field,
    sanitize_frontmatter_scalar,
)
from .validate import ContentInvalid, validate_bundle_content

__all__ = [
    "FORGE_STAGES", "MAX_DEFINITION", "MAX_EXAMPLE", "MAX_RELATED_PER_TERM",
    "MAX_SHORT", "MAX_TERMS_SOFT", "ForgeCancelled", "ForgeParams",
    "ForgeResult", "ProgressCb", "forge_world",
    "GateRefused", "GateResult", "assert_closed_sourced_graph",
    "first_array", "loose_json", "slugify",
    "compute_provenance_tier", "tier_of_source",
    "ReviewFlag", "apply_corrections", "goal_suggestions",
    "propose_new_terms", "reconcile_terms",
    "BUNDLE_SCHEMA", "SEALED_FILES", "reseal_bundle", "write_bundle",
    "SKILL_CHAR_BUDGET", "SKILL_CHARS_PER_TERM", "estimate_skill_chars",
    "render_world_skill", "sanitize_body_field", "sanitize_frontmatter_scalar",
    "ContentInvalid", "validate_bundle_content",
]
