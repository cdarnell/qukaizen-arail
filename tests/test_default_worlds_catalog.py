"""WK-6 + WK-7: the shipped default World catalog.

Defaults (in lab/worlds/, scanned into the catalog): the `ai` World, the
`qukaizen` product World, and the `video-games` World. The three demo Worlds
(art-history, horticulture, physics) are demoted to examples/worlds/ — not
offered as defaults, but still sealed and importable by path.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from arail import world_mount as wm
from arail.capabilities.spec import parse_capabilities_file

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "lab" / "worlds"
EXAMPLES = REPO / "examples" / "worlds"

DEFAULTS = {"ai", "qukaizen", "video-games"}
DEMOTED = {"art-history", "horticulture", "physics"}


def _bundle_dirs(root: Path) -> set[str]:
    return {d.name for d in root.iterdir()
            if d.is_dir() and (d / "manifest.json").exists()}


def test_catalog_ships_the_default_worlds():
    """The three defaults are present and sealed.

    Deliberately a SUBSET check, not equality. ``lab/worlds/`` is also where
    every World a user forges or mounts lands at runtime — a school-work
    World, a demo they pulled down — so asserting the directory contains
    *exactly* the shipped three made the suite fail on any lab that had
    actually been used, which is every real one. The shipped catalog being
    correct is what this guards; what else the operator keeps beside it is
    their business (and is git-ignored — see .gitignore's lab/worlds/ rule).
    """
    present = _bundle_dirs(CATALOG)
    missing = DEFAULTS - present
    assert not missing, f"shipped default World(s) missing from the catalog: {sorted(missing)}"


def test_demoted_worlds_moved_to_examples():
    present = _bundle_dirs(EXAMPLES)
    assert DEMOTED <= present

    # ...and they are no longer SHIPPED from the catalog. Asked of git, not of
    # the filesystem, because "demoted" is a claim about what the repo
    # distributes — an operator is free to mount `physics` at runtime, and
    # that lands in lab/worlds/ without making it a shipped default again.
    tracked = subprocess.run(
        ["git", "ls-files", "lab/worlds/"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.split("\n")
    shipped = {line.split("/")[2] for line in tracked
               if line.startswith("lab/worlds/") and line.count("/") >= 2}
    assert not (DEMOTED & shipped), (
        f"demoted demo World(s) still tracked in the catalog: {sorted(DEMOTED & shipped)}")


def test_qukaizen_world_seals_and_is_sourced():
    b = wm.load_bundle(CATALOG / "qukaizen")
    assert wm.verify_seal(b).ok
    man = json.loads((CATALOG / "qukaizen" / "manifest.json").read_text())
    assert man["provenance_tier"] == "sourced"          # human-authored from docs
    assert man["provenance_counts"]["model"] == 0        # never model-asserted


def test_qukaizen_graph_is_closed_and_connected():
    terms = json.loads((CATALOG / "qukaizen" / "terms.json").read_text())["terms"]
    slugs = {t["slug"] for t in terms}
    for t in terms:
        for r in t.get("related", []):
            assert r in slugs, f"{t['slug']} links dangling {r}"
        assert t.get("source"), f"{t['slug']} missing provenance"
    # the product story is represented across all four categories
    cats = {t["category"] for t in terms}
    assert cats == {"arail", "dac", "nucleus", "aerollm"}


def test_ai_world_seals():
    # The other shipped default — vendored qukaizen-dac export, seal intact.
    assert wm.verify_seal(wm.load_bundle(CATALOG / "ai")).ok


def test_shipped_worlds_have_story_taglines():
    """The welcome picker renders face.json taglines — both defaults need one."""
    for slug in DEFAULTS:
        face = json.loads((CATALOG / slug / "face.json").read_text())
        assert str(face.get("tagline", "")).strip(), f"{slug} missing tagline"


def test_demoted_examples_still_seal_valid():
    # examples remain importable — a broken seal would make import fail
    for slug in DEMOTED:
        assert wm.verify_seal(wm.load_bundle(EXAMPLES / slug)).ok, slug


def test_video_games_world_seals_and_is_sourced():
    b = wm.load_bundle(CATALOG / "video-games")
    assert wm.verify_seal(b).ok
    man = json.loads((CATALOG / "video-games" / "manifest.json").read_text())
    assert man["provenance_tier"] == "sourced"
    assert man["provenance_counts"]["model"] == 0


def test_video_games_graph_is_closed_and_categories_exact():
    terms = json.loads((CATALOG / "video-games" / "terms.json").read_text())["terms"]
    slugs = {t["slug"] for t in terms}
    for t in terms:
        for r in t.get("related", []):
            assert r in slugs, f"{t['slug']} links dangling {r}"
        assert t.get("source"), f"{t['slug']} missing provenance"
    cats = {t["category"] for t in terms}
    assert cats == {"graphics-settings", "hardware", "sim-racing",
                     "drivers", "performance-metrics"}


def test_video_games_capabilities_declare_layer_b():
    """The forge script merges Layer-B capabilities post-seal (world_forge.py's
    sealer only ever emits knowledge-grounding entries). This is a tripwire:
    a portal reseal regenerates capabilities.json and would silently drop
    these — if it ever does, this test catches it.
    """
    caps = parse_capabilities_file(CATALOG / "video-games" / "capabilities.json")
    ids = {c.id for c in caps}
    assert "knowledge.ground.video-games" in ids
    for cat in ("graphics-settings", "hardware", "sim-racing",
                "drivers", "performance-metrics"):
        assert f"knowledge.ground.video-games.{cat}" in ids

    layer_b = {"research.game-config-optimization", "scout.driver-watch",
               "scout.release-watch"}
    assert layer_b <= ids
    for cap in caps:
        if cap.id in layer_b:
            assert cap.desired is True
            # Layer B/C code now exists (mini_experiments.py / scouting.py),
            # but no capability adapter is registered for these ids yet, so
            # they still resolve declared_unavailable — the purpose text
            # must say so honestly rather than claim they're live.
            assert "no capability adapter is registered" in cap.purpose
            assert "unavailable" in cap.purpose


def test_video_games_skill_carries_research_method_and_seal_trailer():
    """Same tripwire as above, for the SKILL.md merge."""
    skill = (CATALOG / "video-games" / "SKILL.md").read_text()
    assert "### Research method" in skill
    man = json.loads((CATALOG / "video-games" / "manifest.json").read_text())
    assert f"dac:world_sha256 {man['world_sha256']}" in skill
