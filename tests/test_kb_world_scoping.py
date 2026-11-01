"""Review-queue scoping — one World's Knowledge Base is that World's only.

Two defects, found together when a freshly-forged World's /dac showed 81
candidates it had never seen:

1. **Cross-World leakage.** ``list_pending`` walked the whole PKB root with no
   World filter, so a mounted World's queue listed another World's glossary.
   The PKB root is shared by every World in a lab, so this was structural,
   not a data accident.
2. **Config offered as knowledge.** ``AGENT.md`` / ``SKILL.md`` / ``README.md``
   entered the queue as candidates. Adding two tutor agents put their config
   files in front of the operator as knowledge awaiting approval.

The fix has to hold both halves at once, and must NOT throw out agent
*outputs*, which live under the same ``agents/`` tree as the config.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arail import compiled_kb as ckb


@pytest.fixture()
def pkb(tmp_path: Path) -> Path:
    root = tmp_path / "pkb"
    for sub in ("sources", "notes", "agents"):
        (root / sub).mkdir(parents=True)
    return root


def _write(root: Path, rel: str, text: str = "# doc\n\nbody text\n") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Lab furniture is not knowledge
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rel", [
    "agents/math_coach/AGENT.md",
    "agents/buddy/decisions.md",
    "agents/README.md",
    "skills/blended-apr-calc/SKILL.md",
    "sources/README.md",
])
def test_config_files_never_enter_the_queue(pkb, rel):
    """Regression: the day two coaches were added, their AGENT.md files showed
    up as candidates. Config describes how the lab behaves; it is not a claim
    about the world for an operator to approve."""
    _write(pkb, rel)
    assert [r["path"] for r in ckb.list_pending(pkb)] == []


@pytest.mark.parametrize("rel", [
    "agents/research/finding-01.md",
    "agents/experiments/exp-02.md",
    "agents/synthesis/summary.md",
    "agents/recommendations/next-steps.md",
])
def test_agent_outputs_are_still_candidates(pkb, rel):
    """The guard is by FILENAME, not by directory — agent findings live under
    agents/ too, and excluding the tree would silently drop the real work."""
    _write(pkb, rel)
    assert [r["path"] for r in ckb.list_pending(pkb)] == [rel]


def test_furniture_exclusion_is_case_insensitive(pkb):
    _write(pkb, "agents/x/Agent.md")
    _write(pkb, "skills/y/Skill.md")
    assert ckb.list_pending(pkb) == []


def test_a_file_merely_named_like_config_elsewhere_is_still_excluded(pkb):
    """A README anywhere is folder documentation. Stated explicitly so the
    rule's breadth is a decision on record, not an accident."""
    _write(pkb, "sources/world-x/terms/README.md")
    assert ckb.list_pending(pkb) == []


# ---------------------------------------------------------------------------
# World scoping
# ---------------------------------------------------------------------------

def test_queue_scoped_to_a_world_excludes_other_worlds(pkb):
    _write(pkb, "sources/world-debt-finance/terms/apr.md")
    _write(pkb, "sources/world-sophie-school/terms/no-solution.md")

    scoped = ckb.list_pending(pkb, world="world-sophie-school")
    assert [r["path"] for r in scoped] == ["sources/world-sophie-school/terms/no-solution.md"]
    assert all(r["world"] == "world-sophie-school" for r in scoped)


def test_a_brand_new_world_starts_empty(pkb):
    """The headline promise: forge a World, and its Knowledge Base is its own.
    A lab full of another World's terms must not leak into it."""
    for i in range(25):
        _write(pkb, f"sources/world-debt-finance/terms/term-{i}.md")
    assert ckb.list_pending(pkb, world="world-brand-new") == []
    assert ckb.pending_count(pkb, world="world-brand-new") == 0


def test_unscoped_queue_is_the_cross_world_view(pkb):
    """The root lab keeps seeing everything — that is its job — and every row
    carries the World it belongs to so the UI can label it."""
    _write(pkb, "sources/world-debt-finance/terms/apr.md")
    _write(pkb, "sources/world-sophie-school/terms/no-solution.md")
    _write(pkb, "notes/loose-note.md")

    rows = ckb.list_pending(pkb)
    assert len(rows) == 3
    assert {r["world"] for r in rows} == {"world-debt-finance", "world-sophie-school", None}


def test_unscoped_items_are_hidden_from_a_mounted_world(pkb):
    """Consequence of "tag at ingest": a World's queue is that World's only.
    Legacy untagged items stay reachable from the root-lab view."""
    _write(pkb, "notes/loose-note.md")
    assert ckb.list_pending(pkb, world="world-sophie-school") == []
    assert len(ckb.list_pending(pkb)) == 1


def test_count_and_list_never_disagree(pkb):
    """pending_count feeds the hero stats and list_pending feeds the queue;
    if they applied different filters the UI would contradict itself."""
    _write(pkb, "sources/world-a/terms/one.md")
    _write(pkb, "sources/world-b/terms/two.md")
    _write(pkb, "agents/a/AGENT.md")
    for scope in (None, "world-a", "world-b", "world-missing"):
        assert ckb.pending_count(pkb, world=scope) == len(ckb.list_pending(pkb, world=scope))


def test_approved_and_rejected_still_filtered_under_a_scope(pkb):
    """Scoping is an ADDITIONAL filter — it must not bypass the gate's own
    approved/rejected bookkeeping."""
    _write(pkb, "sources/world-a/terms/one.md")
    _write(pkb, "sources/world-a/terms/two.md")
    ckb.approve(["sources/world-a/terms/one.md"], pkb)
    assert [r["path"] for r in ckb.list_pending(pkb, world="world-a")] == [
        "sources/world-a/terms/two.md"]


# ---------------------------------------------------------------------------
# Ingest attribution
# ---------------------------------------------------------------------------

def test_ingest_tags_uploads_to_the_mounted_world(pkb, monkeypatch):
    """Knowledge added while a World is mounted belongs to that World — which
    is what keeps the scoped queue populated going forward."""
    import arail.pkb as pkb_mod
    (pkb / "inbox").mkdir(exist_ok=True)
    (pkb / "inbox" / "worksheet.md").write_text("# Worksheet\n", encoding="utf-8")

    monkeypatch.setattr(pkb_mod, "_mounted_world_prefix", lambda: "world-sophie-school")
    dest = pkb_mod.ingest(pkb)["destinations"]["worksheet.md"]

    assert dest.startswith("sources/world-sophie-school/")
    assert ckb._world_of(dest) == "world-sophie-school"
    assert [r["path"] for r in ckb.list_pending(pkb, world="world-sophie-school")] == [dest]


def test_ingest_unmounted_stays_unscoped(pkb, monkeypatch):
    """No mounted World means no World to attribute to. Inventing one would be
    a false provenance claim, so the flat layout is kept."""
    import arail.pkb as pkb_mod
    (pkb / "inbox").mkdir(exist_ok=True)
    (pkb / "inbox" / "worksheet.md").write_text("# Worksheet\n", encoding="utf-8")

    monkeypatch.setattr(pkb_mod, "_mounted_world_prefix", lambda: None)
    dest = pkb_mod.ingest(pkb)["destinations"]["worksheet.md"]

    assert dest.startswith("sources/articles/")
    assert ckb._world_of(dest) is None


def test_mount_lookup_failure_degrades_to_unscoped(pkb, monkeypatch):
    """A broken mount record must not break ingest."""
    import arail.pkb as pkb_mod
    import arail.world_mount as wm
    monkeypatch.setattr(wm, "current_mount", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    assert pkb_mod._mounted_world_prefix() is None


# ---------------------------------------------------------------------------
# The endpoint wiring
# ---------------------------------------------------------------------------

def _review_client(monkeypatch, pkb_root):
    """A client whose review queue reads ``pkb_root``.

    Points ``compiled_kb._pkb_root`` at the fixture directly instead of
    setting LAB_PKM: ``arail.config.PKB_ROOT`` is a module constant resolved
    at import and never rebound in-process (a load-bearing invariant — see
    the pkb_index docstring), so an env-based override only takes effect for
    whichever test imports the app first. Patching the accessor keeps these
    tests independent of collection order.
    """
    from fastapi.testclient import TestClient
    import arail.compiled_kb as ckb_mod
    monkeypatch.setenv("ARAIL_PASSWORD", "test-passphrase-not-real")
    monkeypatch.setattr(ckb_mod, "_pkb_root", lambda: pkb_root)
    import arail.portal.app as _app_mod
    return TestClient(_app_mod.app)


def test_review_endpoint_reports_and_applies_scope(pkb, monkeypatch):
    """The library filter is only useful if the route actually passes the
    mounted World into it — and says which scope it used."""
    import arail.world_mount as wm
    _write(pkb, "sources/world-debt-finance/terms/apr.md")
    _write(pkb, "sources/world-sophie-school/terms/no-solution.md")

    class _Rec:
        world = "sophie-school"

    monkeypatch.setattr(wm, "current_mount", lambda *a, **k: _Rec())
    client = _review_client(monkeypatch, pkb)
    body = client.get("/api/pkb/review").json()

    assert body["scope"] == "world-sophie-school"
    assert [r["path"] for r in body["pending"]] == [
        "sources/world-sophie-school/terms/no-solution.md"]


def test_review_endpoint_unmounted_is_cross_world(pkb, monkeypatch):
    import arail.world_mount as wm
    _write(pkb, "sources/world-debt-finance/terms/apr.md")
    _write(pkb, "sources/world-sophie-school/terms/no-solution.md")

    monkeypatch.setattr(wm, "current_mount", lambda *a, **k: None)
    client = _review_client(monkeypatch, pkb)
    body = client.get("/api/pkb/review").json()

    assert body["scope"] is None
    assert len(body["pending"]) == 2
