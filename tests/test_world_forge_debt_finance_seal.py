"""Setup-tier (30%): the debt-finance World bundle seals cleanly and
respects the agenda-ordering rule.

Per ARCHITECTURE.md §3.2/§10: dac_world/seal.py derives agenda.json.watches
from the first 3 knowledge_sources[] entries by raw array position,
regardless of kind. A misordering silently drops an intended URL-kind
source. This test asserts the sealed bundle's watches are exactly the
intended feed URLs — the CI catch for that bug class.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from arail import world_mount as wm

REPO = Path(__file__).resolve().parents[1]
BUNDLE = REPO / "examples" / "worlds" / "debt-finance"


def test_bundle_is_sealed_and_verifies():
    bundle = wm.load_bundle(BUNDLE)
    seal = wm.verify_seal(bundle)
    assert seal.ok, seal.reason


def test_bundle_is_not_a_catalog_default():
    """debt-finance ships as an opt-in example (personal-finance data),
    never auto-mounted on a fresh lab — must live under examples/worlds/,
    not lab/worlds/.

    The guarantee is about what a FRESH CLONE gets, so it is asked of git
    rather than of the filesystem. An operator who deliberately mounts
    debt-finance has a copy in their own lab/worlds/ — that is the opt-in
    working as designed, and asserting the path is absent instead failed on
    exactly the labs that took the option.
    """
    assert BUNDLE.exists()
    tracked = subprocess.run(
        ["git", "ls-files", "lab/worlds/debt-finance"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert not tracked, (
        "debt-finance is tracked under lab/worlds/ — it would auto-mount on a "
        f"fresh clone. Tracked paths:\n{tracked}")


def test_bundle_has_no_investing_category():
    """The category gate structurally excludes investing content — no
    spec.json category named 'investing' is declared, and the gate would
    reject any term that tried to use one."""
    spec = json.loads((BUNDLE / "spec.json").read_text())
    ids = {c["id"] for c in spec["categories"]}
    assert "investing" not in ids


def test_bundle_is_fully_sourced():
    manifest = json.loads((BUNDLE / "manifest.json").read_text())
    assert manifest["provenance_tier"] == "sourced"
    assert manifest["provenance_counts"]["model"] == 0


def test_agenda_watches_are_exactly_the_intended_feed_urls():
    """The CI catch for the position-based agenda-cap bug (ARCHITECTURE.md
    §3.2/§10): the first 3 knowledge_sources[] entries are url-kind, in
    priority order, so all 3 (and only those 3) become live watches."""
    spec = json.loads((BUNDLE / "spec.json").read_text())
    agenda = json.loads((BUNDLE / "agenda.json").read_text())

    intended = [
        s["ref"] for s in spec["knowledge_sources"][:3]
        if s.get("kind") == "url"
    ]
    got = sorted(
        feed for watch in agenda["watches"] for feed in watch["feeds"]
    )
    assert got == sorted(intended)
    assert len(got) == 3


def test_fourth_source_position_does_not_produce_a_watch():
    """Position 4 is a real url-kind source (issuer offer page) kept for
    citation/provenance only — confirms the ordering-cap rule is understood,
    not just satisfied by accident."""
    spec = json.loads((BUNDLE / "spec.json").read_text())
    agenda = json.loads((BUNDLE / "agenda.json").read_text())

    fourth = spec["knowledge_sources"][3]
    assert fourth["kind"] == "url"
    all_feeds = {feed for watch in agenda["watches"] for feed in watch["feeds"]}
    assert fourth["ref"] not in all_feeds


def test_compliance_disclaimer_shipped_and_seal_exempt():
    """compliance/DISCLAIMER.md is a seal-exempt sibling (same tier as
    SKILL.md) — present alongside the sealed bundle, not one of the 6
    sha256-pinned files."""
    disclaimer = BUNDLE / "compliance" / "DISCLAIMER.md"
    assert disclaimer.exists()
    manifest = json.loads((BUNDLE / "manifest.json").read_text())
    assert "compliance/DISCLAIMER.md" not in json.dumps(manifest.get("sealed_files", manifest))

    from arail.agents.debt_finance_compliance import CANONICAL_PHRASE
    assert CANONICAL_PHRASE in disclaimer.read_text()


def test_terms_graph_is_closed_and_sourced():
    terms = json.loads((BUNDLE / "terms.json").read_text())["terms"]
    slugs = {t["slug"] for t in terms}
    for t in terms:
        assert t.get("source"), f"{t['slug']} missing provenance"
        for r in t.get("related", []):
            assert r in slugs, f"{t['slug']} links dangling {r}"
