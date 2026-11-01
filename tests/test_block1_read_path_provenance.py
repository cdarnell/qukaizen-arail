"""REVIEW2.md BLOCK-1 fix verification: a successful search must not erase
a standing provenance/dimension degradation, and C4 must be enforced on
the read path (pkb._semantic_search), not only at ensure_ready/startup.

Reproduces REVIEW2.md's scenarios 1-3 as regression tests:
  1. legacy 128-dim table -> ensure_ready degrades, does not drop (FM12,
     already covered elsewhere) -- then a search must NOT clear the flag
     and must NOT serve semantic hits.
  2. a sidecar naming a foreign model at the SAME dimension -> ensure_ready
     degrades ("provenance") -- then a search must not serve hits from
     that disagreeing table and must not clear the flag.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def pkb_index_reset():
    import arail.pkb_index as pki
    pki._reset_for_tests()
    yield
    pki._reset_for_tests()


@pytest.fixture
def isolated_pkb(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LAB_PKB", str(tmp_path))
    import arail.config
    import arail.pkb as pkb
    importlib.reload(arail.config)
    importlib.reload(pkb)
    pkb.scaffold(tmp_path)
    return tmp_path


def test_legacy_128dim_table_search_does_not_clear_dimension_degradation(isolated_pkb):
    """REVIEW2.md scenario 1+2: ensure_ready degrades a legacy 128-dim
    table correctly (does not drop). A subsequent pkb.search() call must
    NOT clear that degradation and must NOT return semantic hits."""
    import arail.pkb as pkb
    import arail.pkb_index as pki
    import lancedb  # type: ignore[import-not-found]
    from arail.vector_index import hash_embedding

    notes = isolated_pkb / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    target = notes / "findme.md"
    target.write_text("# findme\nunique marker zzqxflarp9000\n")

    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[{
        "path": "notes/findme.md", "name": "findme.md",
        "vector": hash_embedding("findme unique marker zzqxflarp9000"),  # 128-dim
        "mtime": target.stat().st_mtime, "source_kind": "user",
    }], mode="overwrite")

    from arail.dbspec.generated.models_registry import EMBEDDING_DIM
    open_table = db.open_table("pkb_pages")
    missing_columns, dim_mismatch = pki._schema_column_status(open_table)
    assert dim_mismatch is True  # sanity: 128 != current spec dim (768)

    # ensure_ready degrades correctly (FM12: does not drop).
    pki.ensure_ready(isolated_pkb)
    ok, reason = pki.embedding_status()
    assert ok is False
    assert "dimension" in pki.degraded_codes()

    # THE FIX: a search must not clear it, and must not serve semantic hits.
    results = pkb.search("zzqxflarp9000", pkb_root=isolated_pkb)
    ok_after, _ = pki.embedding_status()
    assert ok_after is False, "a search must NOT clear a standing dimension degradation"
    assert "dimension" in pki.degraded_codes()
    # search() falls through to the regex sweep -- results must be
    # keyword-sourced, never claimed as semantic from the bad table.
    assert results
    assert all(r["source"] == "keyword" for r in results)

    # The table itself is still there, untouched (FM12).
    db2 = lancedb.connect(str(db_path))
    table2 = db2.open_table("pkb_pages")
    assert table2.count_rows() == 1


def test_foreign_model_same_dimension_search_refuses_to_serve_hits(isolated_pkb):
    """REVIEW2.md scenario 3: a sidecar naming a DIFFERENT model at the
    SAME dimension (768) -- a raw LanceDB dimension check can't catch this,
    only the explicit provenance comparison can. Before the fix, search
    returned real hits labelled source="semantic" from this foreign vector
    space and cleared the warning that said not to."""
    import arail.pkb as pkb
    import arail.pkb_index as pki
    import arail.pkb_provenance as prov
    from arail.dbspec.generated.models_registry import embedding_model

    notes = isolated_pkb / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    (notes / "findme.md").write_text("# findme\nunique marker zzqxflarp9000\n")

    # Build a real (768-dim, stubbed-embedder) index...
    pkb.index_all(pkb_root=isolated_pkb, include_docs=False)
    db_path = pkb._vector_db_path(isolated_pkb)

    # ...then overwrite its provenance to claim a DIFFERENT model, same dim.
    prov.write(db_path, embedding_model="some-other-foreign-model",
               embedding_dim=768, spec_sha256="deadbeef", rows=1)

    pki.ensure_ready(isolated_pkb)
    ok, _ = pki.embedding_status()
    assert ok is False
    assert "provenance" in pki.degraded_codes()

    results = pkb.search("zzqxflarp9000", pkb_root=isolated_pkb)

    ok_after, reason_after = pki.embedding_status()
    assert ok_after is False, "a search must NOT clear a standing provenance degradation"
    assert "provenance" in pki.degraded_codes()
    assert "some-other-foreign-model" in reason_after

    # Must fall through to keyword search -- never label a hit from the
    # disagreeing table as semantic.
    assert results
    assert all(r["source"] == "keyword" for r in results), (
        "a query must never be served from a table whose provenance "
        "disagrees with the spec (C4)")


def test_search_path_enforces_provenance_even_without_prior_ensure_ready(isolated_pkb):
    """The read-path check must be independent of ensure_ready having run
    in this process -- ensure_ready runs once at startup behind an
    _initialized guard; a table that goes bad *after* that (sidecar
    rewritten mid-process) must still be caught on the very next search."""
    import arail.pkb as pkb
    import arail.pkb_index as pki
    import arail.pkb_provenance as prov

    notes = isolated_pkb / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    (notes / "findme.md").write_text("# findme\nunique marker zzqxflarp9000\n")
    pkb.index_all(pkb_root=isolated_pkb, include_docs=False)
    db_path = pkb._vector_db_path(isolated_pkb)

    # No ensure_ready() call at all in this process -- go straight to search.
    prov.write(db_path, embedding_model="some-other-foreign-model",
               embedding_dim=768, spec_sha256="deadbeef", rows=1)

    results = pkb.search("zzqxflarp9000", pkb_root=isolated_pkb)
    assert all(r["source"] == "keyword" for r in results)
    ok, _ = pki.embedding_status()
    assert ok is False


def test_flush_success_does_not_clear_standing_dimension_degradation(isolated_pkb, monkeypatch):
    """REVIEW2.md code-quality finding: pkb_index._flush's unconditional
    clear_degraded() (pre-fix) meant a single file save cleared a
    provenance/dimension warning. Now it clears only "provider"."""
    import arail.pkb_index as pki

    pki.set_degraded("dimension", "pre-existing dimension mismatch")
    pki._pkb_root_cache = isolated_pkb
    pki._initialized = True

    import lancedb  # type: ignore[import-not-found]
    from arail.vector_index import hash_embedding
    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[{
        "path": "_seed.md", "name": "_seed.md",
        "vector": hash_embedding("seed", dim=768),
        "mtime": 0.0, "source_kind": "user",
    }], mode="overwrite")

    notes = isolated_pkb / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    (notes / "a.md").write_text("# a\n")
    with pki._lock:
        pki._pending.add("notes/a.md")

    pki._flush()

    ok, _ = pki.embedding_status()
    assert ok is False, "a successful flush must not clear a standing dimension degradation"
    assert "dimension" in pki.degraded_codes()
    assert "provider" not in pki.degraded_codes()
