"""QA (2026-08-08-arail2-tier1-integration): what Buddy actually receives.

Product gating for arail weights Buddy behaviour at 30%. The sprint's
headline is "+40.6pp recall@5 from nomic-embed-text", and REVIEW4.md's
top QA item was: *quantify the user-facing degradation on a legacy
128-dim World, and say whether the operator gets any signal at all.*

These tests answer that on the agent path specifically
(``pkb.search_for_agents`` -> ``lab_brain.retrieve_chat_context``), which
is the one Buddy uses. They are written as behaviour pins, not as
implementation checks: each asserts what a *user* would observe.

Measured against the operator's real ``video-games`` World (copied to a
scratch root; the live lab was never written to) the same behaviours
reproduce exactly — see TEST_REPORT.md's "Buddy" section.
"""
from __future__ import annotations

import importlib

import pytest

pytestmark = pytest.mark.usefixtures("_qa_pkb_reset")


@pytest.fixture
def _qa_pkb_reset():
    import arail.pkb_index as pki
    pki._reset_for_tests()
    yield
    pki._reset_for_tests()


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A scratch PKB root with a small, semantically-labelled corpus."""
    monkeypatch.setenv("LAB_PKB", str(tmp_path))
    import arail.config
    import arail.pkb as pkb
    importlib.reload(arail.config)
    importlib.reload(pkb)
    notes = tmp_path / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    (notes / "upscaling.md").write_text(
        "# Upscaling\nRendering at a lower internal resolution and "
        "reconstructing the image restores headroom.\n")
    (notes / "thermals.md").write_text(
        "# Thermal throttling\nWhen the die exceeds its limit the clock "
        "is reduced until it cools.\n")
    (notes / "wheelbase.md").write_text(
        "# Force feedback\nA direct-drive base couples the motor to the "
        "shaft with no belt, so detail is not damped.\n")
    return tmp_path


def _legacy_128dim_table(root):
    """Write a pkb_pages table with 128-dim hash vectors and no sidecar —
    byte-for-byte the state of four of the operator's five real Worlds."""
    import lancedb  # type: ignore[import-not-found]
    from arail.vector_index import hash_embedding
    db_path = root / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[
        {"path": "notes/upscaling.md", "name": "upscaling.md",
         "vector": hash_embedding("upscaling"), "mtime": 0.0,
         "source_kind": "user"},
    ], mode="overwrite")
    return db_path


# ---------------------------------------------------------------------------
# The headline: what the agent path reports on a legacy World
# ---------------------------------------------------------------------------

def test_agent_retrieval_returns_nothing_and_reports_healthy_on_a_legacy_world(world):
    """REVIEW4 QA item 1, answered.

    With the Compiled-KB gate at its shipped default (on) and nothing yet
    approved — the state of ALL SIX of the operator's real PKB roots —
    ``search_for_agents`` short-circuits in ``pkb.search`` *before*
    ``_semantic_search`` runs. So on a legacy 128-dim World:

      * Buddy receives zero hits, and
      * ``retrieval_status()`` still reports (True, "") — healthy.

    The degraded state C1 designed is therefore not merely un-surfaced in
    the UI (the documented deferral); on the agent path it is never *set*.
    A Buddy context header wired exactly as ARCHITECTURE.md C1 specifies
    would print "retrieval healthy" while Buddy got nothing.

    This test pins the hazard so the day it is fixed it fails loudly and
    points here.
    """
    import arail.pkb as pkb
    import arail.pkb_index as pki
    from arail.compiled_kb import approved_paths, gate_enabled

    _legacy_128dim_table(world)

    assert gate_enabled() is True, "the gate ships on by default"
    assert approved_paths(world) == set(), "a fresh World has nothing approved"

    hits = pkb.search_for_agents("why does my frame rate collapse", world)

    assert hits == [], "Buddy gets nothing"
    assert pki.degraded_codes() == {}, (
        "and no degraded code is set — the agent path never reaches the "
        "read-path health check")
    assert pkb.retrieval_status() == (True, ""), (
        "retrieval_status() claims healthy while the agent got zero rows")


def test_ungated_search_on_a_legacy_world_does_set_the_dimension_code(world):
    """The status contract does work on the ungated path — which is what
    ``/api/pkb/search`` (and its ``X-Retrieval-Status`` header) uses. The
    gap above is specific to the Compiled-KB-gated agent path."""
    import arail.pkb as pkb
    import arail.pkb_index as pki

    _legacy_128dim_table(world)
    pkb.search("why does my frame rate collapse", world)

    assert "dimension" in pki.degraded_codes()
    ok, reason = pkb.retrieval_status()
    assert ok is False
    assert "pkb reembed" in reason, "the message must name a runnable remedy"


def test_keyword_fallback_on_a_legacy_world_cannot_answer_a_question(world):
    """"Degrades to keyword search" is generous. ``_build_snippets`` matches
    the *entire query string* as one literal (``re.escape(query)``), so a
    natural-language question matches nothing at all. On a legacy World the
    honest description is: natural-language retrieval returns zero rows."""
    import arail.pkb as pkb

    _legacy_128dim_table(world)

    question = "what should I do when the card gets too hot"
    assert pkb.search(question, world) == []
    # The same corpus answers an exact-token query fine — the fallback is
    # a literal substring sweep, not a bag-of-words matcher.
    assert pkb.search("throttling", world), "literal tokens still resolve"


def test_agent_retrieval_is_semantic_once_provenance_agrees(world, monkeypatch):
    """Happy path: a correctly-provisioned index + an approved corpus and
    the agent path returns ``source="semantic"``. This is the shape the
    +40.6pp measurement actually reaches the user through."""
    import arail.pkb as pkb
    import arail.compiled_kb as ckb

    pkb.index_all(pkb_root=world, include_docs=False)
    monkeypatch.setattr(
        ckb, "approved_paths",
        lambda root=None: {"notes/upscaling.md", "notes/thermals.md",
                           "notes/wheelbase.md"})
    monkeypatch.setattr("arail.pkb.retrieval_status", pkb.retrieval_status)

    hits = pkb.search_for_agents("upscaling", world)
    assert hits, "an approved, provenance-correct index serves the agent"
    assert hits[0]["source"] == "semantic"


def test_gate_off_on_a_legacy_world_surfaces_the_degrade_to_the_agent(world, monkeypatch):
    """With ``ARAIL_APPROVED_ONLY=off`` the agent path does reach
    ``_semantic_search`` and the dimension code is set — so the fix for the
    first test is reachable without redesigning the gate."""
    import arail.pkb as pkb
    import arail.pkb_index as pki

    monkeypatch.setenv("ARAIL_APPROVED_ONLY", "off")
    _legacy_128dim_table(world)

    pkb.search_for_agents("why does my frame rate collapse", world)
    assert "dimension" in pki.degraded_codes()
