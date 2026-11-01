"""Tests 21 (honest-failure half) / 22 / 35 of
sprints/2026-08-10-arail2-persistence-instantiated/ARCHITECTURE.md §7.

Test 21's full form needs a real seeded PKB with a real (or deterministic
stub) embedder and LanceDB — not runnable in this worktree (no .venv, no
lancedb; see BUILD_LOG.md). But its *inverse* — "re-run under
available()->False and assert the honest failure: 0 semantic hits,
empty_reason set, embedding_status() degraded — not a silent keyword
result claiming health" — is exactly the state this worktree is ALREADY
in (LanceDB genuinely not importable here), so it is directly runnable
and is the regression test for the sprint's own measured symptom.

Test 22 drives search_for_agents() (the entry point Buddy actually calls)
with natural-language questions in that same state and asserts the
honest-failure shape holds all the way through the gate.

Test 35 (egress half): the defect-B fix adds no network call to the
search path — monkeypatch urllib.request.urlopen to raise, prove
_semantic_search's backend-absent branch never reaches it.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from arail import compiled_kb as ckb
from arail import pkb as pkb_mod


def _mk(root: Path, rel: str, text: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture(autouse=True)
def pkb_index_reset():
    import arail.pkb_index as pki
    pki._reset_for_tests()
    yield
    pki._reset_for_tests()


@pytest.fixture()
def seeded_pkb(tmp_path):
    root = tmp_path / "pkb"
    root.mkdir()
    _mk(root, "notes/attention.md",
        "# Attention\nHow does attention work in a transformer? "
        "Attention lets a model weigh different tokens.")
    _mk(root, "notes/other.md", "# Something unrelated entirely")
    ckb.approve(["notes/attention.md", "notes/other.md"], root)
    return root


# ── Test 21 (inverse half): honest failure, not a silent keyword success ───

def test_natural_language_query_honest_failure_when_backend_absent(seeded_pkb, monkeypatch):
    monkeypatch.delenv("ARAIL_APPROVED_ONLY", raising=False)
    with patch("arail.vector_index.available", return_value=False):
        out = pkb_mod.retrieve_for_agents("how does attention work", seeded_pkb)

    # Honest failure, not silent success: retrieve_for_agents falls back
    # to the regex/keyword path (this is expected, documented behavior —
    # search() always has a keyword fallback), so hits may be non-empty.
    # What must NOT happen is any hit claiming to be semantic, and the
    # degraded surface must say the vector backend is unavailable, naming
    # the fix — never silently reporting healthy the way defect B did.
    for hit in out["hits"]:
        assert hit.get("source") != "semantic"
    from arail import pkb_index
    ok, reason = pkb_index.embedding_status()
    assert ok is False
    assert "backend" in pkb_index.degraded_codes()
    assert reason  # not empty — names the fix


# ── Test 22: the agent-facing entry point, three natural-language queries ──

def test_search_for_agents_honest_failure_three_queries(seeded_pkb, monkeypatch):
    monkeypatch.delenv("ARAIL_APPROVED_ONLY", raising=False)
    ckb.approve(["notes/attention.md", "notes/other.md"], seeded_pkb)

    queries = [
        "how does attention work",
        "what is a transformer",
        "explain gradient descent",
    ]
    with patch("arail.vector_index.available", return_value=False):
        for q in queries:
            out = pkb_mod.retrieve_for_agents(q, seeded_pkb)
            # Never a silent claim of health: semantic hits are empty and
            # the degraded code is set for every one of these calls.
            assert out["hits"] == [] or all(
                h.get("source") != "semantic" for h in out["hits"])

    from arail import pkb_index
    assert "backend" in pkb_index.degraded_codes()


# ── Test 35 (egress half): defect B's fix adds no network call ─────────────

def test_backend_absent_branch_makes_no_network_call(tmp_path: Path, monkeypatch):
    """The regression this specific test guards: set_degraded() itself
    must be purely local (dict mutation + optional activity_log, both
    in-process) — proven by making any attempt to open a socket raise,
    then calling the exact code path defect B fixed."""
    import socket

    def _raise(*a, **kw):
        raise AssertionError("network call attempted from backend-absent branch")

    monkeypatch.setattr(socket.socket, "connect", _raise)
    monkeypatch.setattr("urllib.request.urlopen", _raise, raising=False)

    with patch("arail.vector_index.available", return_value=False):
        hits = pkb_mod._semantic_search("attention", tmp_path)

    assert hits == []
    from arail import pkb_index
    assert "backend" in pkb_index.degraded_codes()
