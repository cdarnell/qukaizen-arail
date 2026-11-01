"""QA-6: the question the whole sprint exists to answer — after this change,
does Buddy actually get non-empty knowledge-base results on a World?

This goes through the surfaces Buddy really uses (lab_brain.retrieve_chat_context
→ pkb.search_for_agents, and the prompt builder that puts the hits in front of
the model), not through compiled_kb directly. It uses a real sealed bundle
mounted into a temp PKB root; the operator's lab/ is never touched.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from arail import compiled_kb as ckb
from arail import lab_brain
from arail import pkb as pkb_mod
from arail.world_mount import mount, swap

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "world-bundles"
PHYSICS = FIXTURES / "physics"
ART = FIXTURES / "art-history-skill"


@pytest.fixture()
def lab(tmp_path, monkeypatch):
    """A temp lab wired in as the process-wide PKB root, so callers that do
    not take a pkb_root argument (Buddy's chat retrieval, the goal drafter,
    the researcher) resolve to it."""
    import arail.config as cfg
    pkb = tmp_path / "pkb"
    dd = tmp_path / "data"
    dd.mkdir()
    monkeypatch.setattr(cfg, "PKB_ROOT", pkb)
    monkeypatch.setattr(cfg, "PKM_ROOT", pkb, raising=False)
    monkeypatch.delenv("ARAIL_AUTO_APPROVE_WORLD_TERMS", raising=False)
    monkeypatch.delenv("ARAIL_APPROVED_ONLY", raising=False)
    return pkb, dd


def _a_term(bundle_dir: pathlib.Path) -> dict:
    terms = json.loads((bundle_dir / "terms.json").read_text())["terms"]
    return terms[0]


def test_buddy_gets_zero_before_the_fix_state_and_nonzero_after_mount(lab):
    """The regression test for QA-6 itself, stated as Buddy sees it."""
    pkb, dd = lab
    term = _a_term(PHYSICS)
    query = term["term"]

    # Pre-mount: the lab has content but no manifest — this is the exact
    # state every one of the operator's six roots was in.
    pkb.mkdir(parents=True)
    (pkb / "notes").mkdir()
    (pkb / "notes" / "seed.md").write_text(f"# seed\n{query}\n")
    assert ckb.gate_state(pkb)["state"] == "unbootstrapped"
    assert lab_brain.retrieve_chat_context(query) == []

    mount(PHYSICS, pkb_root=pkb, data_dir=dd)

    hits = lab_brain.retrieve_chat_context(query)
    assert hits, "Buddy still gets nothing from the knowledge base after a mount"
    assert any(h["path"].startswith("sources/world-physics/terms/") for h in hits)


def test_buddy_prompt_actually_carries_the_knowledge(lab):
    """Not just "the search returned rows" — the retrieved text reaches the
    model's context."""
    pkb, dd = lab
    mount(PHYSICS, pkb_root=pkb, data_dir=dd)
    term = _a_term(PHYSICS)

    messages = lab_brain.build_chat_messages(term["term"])
    system = messages[0]["content"]
    assert "sources/world-physics/terms/" in system, system[:2000]


def test_buddy_follows_the_operator_across_a_world_switch(lab):
    pkb, dd = lab
    mount(PHYSICS, pkb_root=pkb, data_dir=dd)
    art_term = _a_term(ART)
    phys_term = _a_term(PHYSICS)

    assert lab_brain.retrieve_chat_context(phys_term["term"])

    swap(ART, pkb_root=pkb, data_dir=dd)

    after = lab_brain.retrieve_chat_context(art_term["term"])
    assert after, "Buddy went blind after a World switch"
    assert all(not h["path"].startswith("sources/world-physics/") for h in after)


def test_buddy_never_sees_a_personal_note_on_the_mounted_world(lab):
    pkb, dd = lab
    mount(PHYSICS, pkb_root=pkb, data_dir=dd)
    (pkb / "notes").mkdir(exist_ok=True)
    (pkb / "notes" / "personal.md").write_text("my account is ACCT-XYZ-4417\n")

    assert lab_brain.retrieve_chat_context("ACCT-XYZ-4417") == []
    assert pkb_mod.retrieve_for_agents("ACCT-XYZ-4417", pkb)["empty_reason"] == "no_match"


def test_goal_drafter_surface_sees_the_same_knowledge(lab):
    """portal/app.py:3160 calls search_for_agents with the process-wide root
    — same wiring, so assert it through the same entry point."""
    pkb, dd = lab
    mount(PHYSICS, pkb_root=pkb, data_dir=dd)
    assert pkb_mod.search_for_agents(_a_term(PHYSICS)["term"])[:8]


def test_researcher_kb_search_surface_sees_the_same_knowledge(lab):
    pkb, dd = lab
    mount(PHYSICS, pkb_root=pkb, data_dir=dd)
    hits = pkb_mod.search_for_agents(_a_term(PHYSICS)["term"])[:5]
    assert hits and all(isinstance(h, dict) and "path" in h for h in hits)


def test_bootstrap_verb_alone_repairs_a_root_that_was_never_re_mounted(
        lab, tmp_path, monkeypatch):
    """The operator's actual six-root situation: staged content already on
    disk, no manifest, and no intention of re-mounting. `pkb bootstrap` must
    be sufficient on its own."""
    import arail.config as cfg
    pkb, dd = lab
    mount(PHYSICS, pkb_root=pkb, data_dir=dd)
    # simulate the pre-sprint state: content staged, manifest never written
    (pkb / "compiled" / "kb" / "approved.json").unlink()
    assert lab_brain.retrieve_chat_context(_a_term(PHYSICS)["term"]) == []

    monkeypatch.setattr(cfg, "WORLDS_DIR", str(FIXTURES.parent / "world-bundles"))
    res = ckb.bootstrap(pkb)
    assert res["world"] == "physics" and res["approved"] > 0
    assert lab_brain.retrieve_chat_context(_a_term(PHYSICS)["term"])
