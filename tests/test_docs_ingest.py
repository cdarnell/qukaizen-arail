"""LanceDB docs ingest tests — docs-hub-sprint-3.

Covers failure modes F1, F2, F7, F8 from ARCHITECTURE.md and the interface
contract on pkb.index_all(include_docs=True).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_frontmatter_doc(path: Path, slug: str, title: str, body: str = "body") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntitle: {title}\ncategory: Reference\n---\n# {title}\n{body}\n",
        encoding="utf-8",
    )


def _make_pkb_root(tmp_path: Path) -> Path:
    """Create a minimal PKB root with one article so the ingest has PKB rows."""
    root = tmp_path / "pkb"
    articles = root / "sources" / "articles"
    articles.mkdir(parents=True, exist_ok=True)
    for i in range(50):
        (articles / f"article-{i:04d}.md").write_text(
            f"# Article {i}\n\nContent for article {i}.\n",
            encoding="utf-8",
        )
    return root


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


def test_index_all_includes_docs_rows(tmp_path, monkeypatch):
    """index_all(include_docs=True) returns indexed_docs >= 1 and each docs
    row has source_kind='docs' (interface contract).
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from arail.vector_index import available

    if not available():
        pytest.skip("LanceDB not available in this environment")

    from arail.pkb import index_all  # noqa: PLC0415

    pkb_root = _make_pkb_root(tmp_path)
    docs_dir = tmp_path / "docs"
    _write_frontmatter_doc(docs_dir / "test-doc.md", "test-doc", "Test Doc")

    # Monkeypatch docs_registry to point at our temp docs dir
    import importlib
    import arail.portal.docs_registry as reg_mod
    monkeypatch.setattr(reg_mod, "_repo_root", lambda: tmp_path)
    reg_mod._invalidate_cache()

    result = index_all(pkb_root=pkb_root, include_docs=True)

    assert result["ok"] is True
    assert result["indexed_docs"] >= 1, (
        f"Expected indexed_docs >= 1, got {result['indexed_docs']}"
    )
    assert "indexed" in result
    assert "path" in result

    # Verify the docs rows landed in LanceDB with source_kind='docs'. Read
    # the table directly rather than through VectorIndex.search(), which
    # always query-embeds via hash_embedding at its own fixed default
    # dimension (128) — incompatible with a table written at the spec's
    # declared dimension (768) since the Tier 1.2 embedder swap. This test
    # is about row presence, not retrieval.
    from arail.vector_index import VectorIndex  # noqa: PLC0415
    from arail.pkb import _vector_db_path  # noqa: PLC0415

    idx = VectorIndex(name="pkb_pages", db_path=_vector_db_path(pkb_root))
    table = idx._table()  # noqa: SLF001 — test-only direct read
    assert table is not None
    all_rows = table.to_pandas().to_dict("records")
    docs_rows = [r for r in all_rows if r.get("source_kind") == "docs"]
    assert len(docs_rows) >= 1, (
        "No rows with source_kind='docs' found in LanceDB after index_all"
    )


def test_index_all_include_docs_false_skips_docs(tmp_path, monkeypatch):
    """index_all(include_docs=False) returns indexed_docs=0 and no docs rows."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from arail.vector_index import available

    if not available():
        pytest.skip("LanceDB not available in this environment")

    from arail.pkb import index_all, _vector_db_path  # noqa: PLC0415
    from arail.vector_index import VectorIndex  # noqa: PLC0415

    pkb_root = _make_pkb_root(tmp_path)
    docs_dir = tmp_path / "docs"
    _write_frontmatter_doc(docs_dir / "skip-doc.md", "skip-doc", "Skip Doc")

    import arail.portal.docs_registry as reg_mod
    monkeypatch.setattr(reg_mod, "_repo_root", lambda: tmp_path)
    reg_mod._invalidate_cache()

    result = index_all(pkb_root=pkb_root, include_docs=False)

    assert result["ok"] is True
    assert result["indexed_docs"] == 0, (
        f"indexed_docs should be 0 when include_docs=False, got {result['indexed_docs']}"
    )

    # Confirm no docs rows in the index
    idx = VectorIndex(name="pkb_pages", db_path=_vector_db_path(pkb_root))
    all_rows = idx.search("Skip Doc", k=200, min_score=0.0)
    docs_rows = [r for r in all_rows if r.get("source_kind") == "docs"]
    assert len(docs_rows) == 0, (
        f"Found {len(docs_rows)} docs rows even though include_docs=False"
    )


def test_index_all_handles_registry_failure_gracefully(tmp_path, monkeypatch):
    """If docs_registry.all_docs() raises, index_all still indexes PKB rows
    and returns ok=True.  Docs ingest must never block PKB ingest (F8 boundary).
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from arail.vector_index import available

    if not available():
        pytest.skip("LanceDB not available in this environment")

    from arail.pkb import index_all  # noqa: PLC0415

    pkb_root = _make_pkb_root(tmp_path)

    import arail.portal.docs_registry as reg_mod

    def _raise():
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(reg_mod, "all_docs", _raise)

    result = index_all(pkb_root=pkb_root, include_docs=True)

    # PKB must still be indexed despite registry failure
    assert result["ok"] is True
    assert result["indexed"] > 0, "PKB rows should have been indexed"
    assert result["indexed_docs"] == 0, "indexed_docs must be 0 on registry failure"


def test_index_all_empty_body_doc_does_not_crash(tmp_path, monkeypatch):
    """A doc with empty body (F7) must not crash index_all.

    Ingest's `body[:4096]` slice handles '' cleanly.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from arail.vector_index import available

    if not available():
        pytest.skip("LanceDB not available in this environment")

    from arail.pkb import index_all  # noqa: PLC0415

    pkb_root = _make_pkb_root(tmp_path)
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    # Minimal frontmatter, empty body
    (docs_dir / "empty-body.md").write_text(
        "---\ntitle: Empty Body\ncategory: Reference\n---\n",
        encoding="utf-8",
    )

    import arail.portal.docs_registry as reg_mod
    monkeypatch.setattr(reg_mod, "_repo_root", lambda: tmp_path)
    reg_mod._invalidate_cache()

    # Must not raise
    result = index_all(pkb_root=pkb_root, include_docs=True)
    assert result["ok"] is True
    assert result["indexed_docs"] >= 1


def test_index_all_stale_doc_removed_on_reingest(tmp_path, monkeypatch):
    """If a doc is deleted between two calls to index_all, the second call
    removes the stale row (F2).  VectorIndex.replace() is full-replace semantics.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from arail.vector_index import available

    if not available():
        pytest.skip("LanceDB not available in this environment")

    from arail.pkb import index_all, _vector_db_path  # noqa: PLC0415
    from arail.vector_index import VectorIndex  # noqa: PLC0415

    pkb_root = _make_pkb_root(tmp_path)
    docs_dir = tmp_path / "docs"
    stale = docs_dir / "stale-doc.md"
    _write_frontmatter_doc(stale, "stale-doc", "Stale Doc")

    import arail.portal.docs_registry as reg_mod
    monkeypatch.setattr(reg_mod, "_repo_root", lambda: tmp_path)
    reg_mod._invalidate_cache()

    # First ingest — stale doc present
    r1 = index_all(pkb_root=pkb_root, include_docs=True)
    assert r1["indexed_docs"] >= 1

    # Delete the doc and re-ingest
    stale.unlink()
    reg_mod._invalidate_cache()

    r2 = index_all(pkb_root=pkb_root, include_docs=True)

    # Confirm the stale row is gone
    idx = VectorIndex(name="pkb_pages", db_path=_vector_db_path(pkb_root))
    all_rows = idx.search("Stale Doc", k=200, min_score=0.0)
    stale_rows = [r for r in all_rows if r.get("path", "").startswith("docs/stale-doc")]
    assert len(stale_rows) == 0, (
        f"Stale row for deleted doc still present after re-ingest (F2): {stale_rows}"
    )


def test_index_all_source_kind_docs_does_not_pollute_pkb_source_kind(tmp_path, monkeypatch):
    """Existing PKB rows must retain their original source_kind after docs are
    added.  Docs ingest is additive — it must not modify PKB row source_kinds
    (F8 isolation contract).
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from arail.vector_index import available

    if not available():
        pytest.skip("LanceDB not available in this environment")

    from arail.pkb import index_all, _vector_db_path  # noqa: PLC0415
    from arail.vector_index import VectorIndex  # noqa: PLC0415

    pkb_root = _make_pkb_root(tmp_path)
    docs_dir = tmp_path / "docs"
    _write_frontmatter_doc(docs_dir / "isolation-doc.md", "isolation-doc", "Isolation Doc")

    import arail.portal.docs_registry as reg_mod
    monkeypatch.setattr(reg_mod, "_repo_root", lambda: tmp_path)
    reg_mod._invalidate_cache()

    index_all(pkb_root=pkb_root, include_docs=True)

    idx = VectorIndex(name="pkb_pages", db_path=_vector_db_path(pkb_root))
    all_rows = idx.search("article", k=200, min_score=0.0)

    # All PKB rows must have source_kind != 'docs'
    polluted = [r for r in all_rows if r.get("source_kind") == "docs"
                and not r.get("path", "").startswith(("docs/", "root/"))]
    assert len(polluted) == 0, (
        f"PKB rows with wrong source_kind='docs': {polluted} (F8)"
    )


# ---------------------------------------------------------------------------
# Performance test
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_index_all_perf_under_2s(tmp_path, monkeypatch):
    """Synthetic 50-PKB + real-24-docs ingest completes in <2.0s (F1).

    This bound is deliberately generous — the hash-embedding is O(n*body_len)
    but body is capped at 4 KB and the corpus is small.  If this test starts
    flaking on CI, the first question is whether LanceDB write latency spiked.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from arail.vector_index import available

    if not available():
        pytest.skip("LanceDB not available in this environment")

    from arail.pkb import index_all  # noqa: PLC0415

    # 50 synthetic PKB rows
    pkb_root = _make_pkb_root(tmp_path)  # creates 50 articles

    # Use real docs corpus (the 24 registered docs)
    # _repo_root is not patched — we want the real docs.
    import arail.portal.docs_registry as reg_mod
    reg_mod._invalidate_cache()

    t0 = time.perf_counter()
    result = index_all(pkb_root=pkb_root, include_docs=True)
    elapsed = time.perf_counter() - t0

    assert result["ok"] is True
    assert elapsed < 2.0, (
        f"index_all with 50 PKB + {result['indexed_docs']} docs took "
        f"{elapsed:.2f}s — exceeds 2.0s budget (F1)"
    )
