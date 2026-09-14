"""Import smoke test for the world_forge.py -> dac_world shim (Failure F3).

Asserts ``import dac_world`` succeeds with the pinned/editable version, and
that the ``world_forge`` shim re-exports exactly the public names ARAIL's
portal/librarian_scout/world_sources/test suite actually import — a
regression net for the shim itself, independent of dac_world's own test
suite (which lives in qukaizen-ddac).
"""

from __future__ import annotations

import arail.world_forge as wf


def test_dac_world_importable():
    import dac_world  # noqa: F401 — the assertion is that this doesn't raise


def test_world_forge_shim_reexports_expected_names():
    # Grepped from every `wf.<name>` / `from arail.world_forge import <name>`
    # call site in src/ and tests/ (see qukaizen-ddac's BUILD_LOG.md step 6).
    expected = {
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
    }
    missing = expected - set(dir(wf))
    assert not missing, f"world_forge shim is missing re-exports: {sorted(missing)}"


def test_forge_world_requires_router_and_never_reaches_into_arail_itself():
    """dac_world.forge_world no longer defaults router=None to constructing
    arail.router.ModelRouter (that was the F4 violation removed in the
    migration) — it raises ValueError instead."""
    import pytest

    with pytest.raises(ValueError, match="router"):
        wf.forge_world(wf.ForgeParams("subject", "subject"), router=None)
