"""arail.world_catalog: deterministic approved-term pulls, category
breakdowns, and slug-sanitizer parity.

Moved from tests/build/test_world_corpus.py (sprints/2026-09-23-nucleus-sprint-1,
ARCHITECTURE.md §8) — the read/pull half of the old world_corpus test suite,
now exercised against arail.world_catalog directly. The KICE-synthesis
orchestration tests (term_to_kice_example, build_world_corpus, chunk,
tag_source) stay in tests/build/test_world_corpus.py against the temporary
re-export shim until /build is retired (commit 26).
"""

from __future__ import annotations

import json

import pytest

from arail import world_catalog as wc


@pytest.fixture
def synthetic_world(tmp_path):
    """A tiny 2-craft-category + 1-business-category World, mirroring the
    real photography bundle's on-disk shape closely enough to exercise
    pull_approved_terms end to end."""
    worlds_dir = tmp_path / "worlds"
    slug = "testworld"
    bundle_dir = worlds_dir / slug
    bundle_dir.mkdir(parents=True)
    terms = [
        {"slug": "wide-aperture", "term": "Wide Aperture", "category": "exposure",
         "definition": "d1", "source": "s1"},
        {"slug": "golden-hour", "term": "Golden Hour", "category": "light",
         "definition": "d2", "source": "s2"},
        {"slug": "not-approved-yet", "term": "Not Approved", "category": "exposure",
         "definition": "d3", "source": "s3"},
        {"slug": "wix-thing", "term": "Wix Thing", "category": "web-platform",
         "definition": "d4", "source": "s4"},
    ]
    (bundle_dir / "terms.json").write_text(json.dumps({"terms": terms}))
    (bundle_dir / "spec.json").write_text(json.dumps({"categories": [
        {"id": "exposure", "label": "Exposure"},
        {"id": "light", "label": "Light"},
        {"id": "web-platform", "label": "Web Platform"},
        {"id": "empty-cat", "label": "Empty Category"}]}))

    pkb_root = tmp_path / "pkb"
    kb_dir = pkb_root / "compiled" / "kb"
    kb_dir.mkdir(parents=True)
    # Approve everything except "not-approved-yet".
    approved = [
        {"path": f"sources/world-{slug}/terms/wide-aperture.md"},
        {"path": f"sources/world-{slug}/terms/golden-hour.md"},
        {"path": f"sources/world-{slug}/terms/wix-thing.md"},
    ]
    (kb_dir / "approved.json").write_text(json.dumps(approved))
    return {"worlds_dir": worlds_dir, "pkb_root": pkb_root, "slug": slug}


def test_pull_approved_terms_category_and_approval_filter(synthetic_world):
    terms = wc.pull_approved_terms(
        synthetic_world["slug"], categories=("exposure", "light"),
        worlds_dir=synthetic_world["worlds_dir"],
        pkb_root=synthetic_world["pkb_root"])
    slugs = [t["slug"] for t in terms]
    assert slugs == ["wide-aperture", "golden-hour"]   # sorted by spec order
    # not-approved-yet excluded despite matching category (correction: the
    # approval gate governs retrieval eligibility, independent of DaC's
    # own curation confidence).
    assert "not-approved-yet" not in slugs
    # wix-thing excluded by category filter even though it IS approved.
    assert "wix-thing" not in slugs


def test_pull_approved_terms_business_category_when_requested(synthetic_world):
    terms = wc.pull_approved_terms(
        synthetic_world["slug"], categories=("web-platform",),
        worlds_dir=synthetic_world["worlds_dir"],
        pkb_root=synthetic_world["pkb_root"])
    assert [t["slug"] for t in terms] == ["wix-thing"]


def test_pull_survives_simulated_remount_sweep(synthetic_world, monkeypatch):
    """Regression for the mount-swap correction: world_mount._sweep_other_worlds
    deletes STAGED markdown (sources/world-<slug>/) when a different World is
    mounted, but the catalog copy (WORLDS_DIR/<slug>/) and approved.json are
    untouched — pull_approved_terms must keep working after that sweep."""
    staged = synthetic_world["pkb_root"] / "sources" / f"world-{synthetic_world['slug']}"
    staged.mkdir(parents=True)
    (staged / "terms").mkdir()
    (staged / "terms" / "wide-aperture.md").write_text("---\ntitle: x\n---\n")
    assert staged.exists()

    import shutil
    shutil.rmtree(staged)   # simulate _sweep_other_worlds
    assert not staged.exists()

    terms = wc.pull_approved_terms(
        synthetic_world["slug"], categories=("exposure",),
        worlds_dir=synthetic_world["worlds_dir"],
        pkb_root=synthetic_world["pkb_root"])
    assert [t["slug"] for t in terms] == ["wide-aperture"]


def test_resolve_world_bundle_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        wc.resolve_world_bundle("nope", worlds_dir=tmp_path)


def test_all_categories_returns_spec_order(synthetic_world):
    cats = wc.all_categories(
        synthetic_world["slug"], worlds_dir=synthetic_world["worlds_dir"])
    assert cats == ["exposure", "light", "web-platform", "empty-cat"]


def test_category_breakdown_counts_and_labels(synthetic_world):
    breakdown = wc.category_breakdown(
        synthetic_world["slug"],
        worlds_dir=synthetic_world["worlds_dir"],
        pkb_root=synthetic_world["pkb_root"])
    by_id = {row["id"]: row for row in breakdown}

    assert by_id["exposure"]["label"] == "Exposure"
    assert by_id["exposure"]["term_count"] == 2      # wide-aperture + not-approved-yet
    assert by_id["exposure"]["approved_count"] == 1  # only wide-aperture approved

    assert by_id["light"]["term_count"] == 1
    assert by_id["light"]["approved_count"] == 1

    assert by_id["web-platform"]["term_count"] == 1
    assert by_id["web-platform"]["approved_count"] == 1

    # zero-approved-count / disabled-row case: declared in spec.json but no
    # terms.json entries and nothing approved.
    assert by_id["empty-cat"]["label"] == "Empty Category"
    assert by_id["empty-cat"]["term_count"] == 0
    assert by_id["empty-cat"]["approved_count"] == 0

    assert [row["id"] for row in breakdown] == \
        ["exposure", "light", "web-platform", "empty-cat"]


def test_category_breakdown_missing_world_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        wc.category_breakdown("nonexistent", worlds_dir=tmp_path / "worlds")


def test_safe_term_slug_matches_compiled_kb_and_world_mount():
    """Direct parity check (also covered end-to-end by
    tests/test_qa6_security_gate.py::test_slug_sanitizer_parity_with_world_mount_and_world_corpus)."""
    from arail import compiled_kb as ckb
    from arail import world_mount

    samples = ["apr", "401(k) Loan", "  spaced  ", "CAPS", "café",
               "a" * 200, "../../etc/passwd", "", "---", "9lives"]
    for s in samples:
        assert wc._safe_term_slug(s) == ckb._safe_term_slug(s) == \
            world_mount._safe_term_slug(s), s
