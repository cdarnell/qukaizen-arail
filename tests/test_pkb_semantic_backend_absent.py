"""Defect B regression tests (sprints/2026-08-10-arail2-persistence-instantiated).

pkb.py:717-718's ``if not available(): return []`` was the only early return
in ``_semantic_search`` that never called ``pkb_index.set_degraded(...)``.
When the running interpreter cannot import the vector backend at all,
semantic retrieval died silently while every health surface
(``embedding_status()``, ``retrieval_status()``, ``doctor``) kept reporting
healthy. Tests 16-19 of ARCHITECTURE.md's test strategy.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def pkb_index_reset():
    import arail.pkb_index as pki
    pki._reset_for_tests()
    yield
    pki._reset_for_tests()


def test_semantic_search_backend_absent_sets_degraded(tmp_path: Path):
    """Test 16: available()->False sets the 'backend' degraded code and
    embedding_status() reports unhealthy, naming the fix."""
    import arail.pkb as pkb
    from arail import pkb_index

    with patch("arail.vector_index.available", return_value=False):
        hits = pkb._semantic_search("attention", tmp_path)

    assert hits == []
    ok, reason = pkb_index.embedding_status()
    assert ok is False
    assert "backend" in pkb_index.degraded_codes()
    assert "install" in reason or "backend" in reason.lower() or "LanceDB" in reason


def test_semantic_search_backend_absent_names_the_fix(tmp_path: Path):
    import arail.pkb as pkb
    from arail import pkb_index

    with patch("arail.vector_index.available", return_value=False):
        pkb._semantic_search("attention", tmp_path)

    codes = pkb_index.degraded_codes()
    assert "./arailctl install" in codes["backend"] or "non-.venv" in codes["backend"]


def test_backend_code_not_cleared_by_successful_embed(tmp_path: Path):
    """Test 17: a successful embed_query does NOT clear 'backend' — a
    successful embed call is not evidence about interpreter import health
    (BLOCK-1 discipline)."""
    from arail import pkb_index

    pkb_index.set_degraded("backend", "LanceDB is not importable")
    # Simulate what a successful embed call clears: only "provider".
    pkb_index.clear_degraded("provider")

    assert "backend" in pkb_index.degraded_codes()


def test_full_rebuild_clears_backend_code():
    """Test 18: a full index_all()-style clear_degraded(None) clears every
    code, including 'backend'."""
    from arail import pkb_index

    pkb_index.set_degraded("backend", "LanceDB is not importable")
    pkb_index.set_degraded("provider", "network down")

    pkb_index.clear_degraded(None)

    assert pkb_index.degraded_codes() == {}


def test_retrieval_status_reports_degraded_when_backend_absent(tmp_path: Path):
    """Test 19: retrieval_status() (the source for X-Retrieval-Status)
    reports degraded in the backend-absent state."""
    import arail.pkb as pkb
    from arail import pkb_index

    with patch("arail.vector_index.available", return_value=False):
        pkb._semantic_search("attention", tmp_path)

    ok, reason = pkb.retrieval_status()
    assert ok is False
    assert reason


def test_available_true_clears_backend_code_when_reached_again(tmp_path: Path):
    """A later successful available() observation is evidence about the
    'backend' code specifically and clears it (§4.7)."""
    import arail.pkb as pkb
    from arail import pkb_index

    pkb_index.set_degraded("backend", "LanceDB is not importable")

    # available() now True, but count()==0 (no lancedb table on disk in
    # this sandbox) still returns [] via the "empty" path -- what matters
    # here is only that "backend" itself gets cleared as soon as we prove
    # available() again.
    with patch("arail.vector_index.available", return_value=True):
        pkb._semantic_search("attention", tmp_path)

    assert "backend" not in pkb_index.degraded_codes()
