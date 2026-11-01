"""C1 error contract (ARCHITECTURE.md, arail2-tier1-integration W6).

An embedding outage must be impossible to mistake for an empty corpus.
Covers FM10 (index_all propagates EmbeddingError, never silently empties
an index) and FM17 (a dead embedding provider must not turn the debounce
timer into a retry storm), plus the degraded-state primitives themselves
and the C4 provenance read-side in pkb_index.ensure_ready.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from arail.dbspec.embed import EmbeddingError


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


def _last_activity_event():
    from arail.activity import activity_log
    return activity_log._buffer[-1] if activity_log._buffer else None


# --------------------------------------------------------------------------
# degraded-state primitives
# --------------------------------------------------------------------------

def test_set_degraded_and_embedding_status():
    import arail.pkb_index as pki
    ok, reason = pki.embedding_status()
    assert ok is True
    assert reason == ""

    pki.set_degraded("provider", "simulated outage")
    ok, reason = pki.embedding_status()
    assert ok is False
    assert reason == "simulated outage"


def test_clear_degraded_resets_status():
    import arail.pkb_index as pki
    pki.set_degraded("provider", "simulated outage")
    pki.clear_degraded()
    ok, reason = pki.embedding_status()
    assert ok is True
    assert reason == ""


def test_clear_degraded_with_code_clears_only_that_code():
    """REVIEW2.md BLOCK-1: a cause can only be cleared by evidence about
    THAT cause. clear_degraded("provider") must not clear "provenance"."""
    import arail.pkb_index as pki
    pki.set_degraded("provider", "outage")
    pki.set_degraded("provenance", "disagreement")
    pki.clear_degraded("provider")
    codes = pki.degraded_codes()
    assert "provider" not in codes
    assert "provenance" in codes
    ok, _ = pki.embedding_status()
    assert ok is False, "provenance must still be degraded"


def test_degraded_codes_returns_all_active_causes():
    import arail.pkb_index as pki
    pki.set_degraded("provider", "outage")
    pki.set_degraded("dimension", "wrong dim")
    codes = pki.degraded_codes()
    assert codes == {"provider": "outage", "dimension": "wrong dim"}


def test_clear_degraded_none_clears_every_code():
    import arail.pkb_index as pki
    pki.set_degraded("provider", "outage")
    pki.set_degraded("provenance", "disagreement")
    pki.clear_degraded(None)
    assert pki.degraded_codes() == {}
    ok, _ = pki.embedding_status()
    assert ok is True


def test_reset_for_tests_clears_degraded_flag():
    import arail.pkb_index as pki
    pki.set_degraded("provider", "leaked from a previous test")
    pki._reset_for_tests()
    ok, _ = pki.embedding_status()
    assert ok is True
    assert pki.degraded_codes() == {}


def test_debounce_sec_backs_off_while_degraded(monkeypatch):
    import arail.pkb_index as pki
    monkeypatch.delenv("LAB_PKB_UPSERT_DEBOUNCE_SEC", raising=False)
    assert pki._debounce_sec() == pki._DEFAULT_DEBOUNCE
    pki.set_degraded("provider", "outage")
    assert pki._debounce_sec() == pki._ERROR_BACKOFF_SEC == 60.0


# --------------------------------------------------------------------------
# FM10 — index_all's EmbeddingError is LOUD, never silently swallowed
# --------------------------------------------------------------------------

def test_index_all_wrapper_sets_degraded_logs_error_and_emits_activity_error(
    isolated_pkb, monkeypatch, caplog
):
    import arail.pkb_index as pki
    import arail.pkb as pkb_mod

    def raising_index_all(root=None, **kwargs):
        raise EmbeddingError("simulated Ollama outage")

    monkeypatch.setattr(pkb_mod, "index_all", raising_index_all)

    with caplog.at_level("ERROR", logger="arail.pkb_index"):
        pki._index_all_reporting_embedding_errors(isolated_pkb, "test-context")

    ok, reason = pki.embedding_status()
    assert ok is False
    assert "simulated Ollama outage" in reason

    assert any(
        r.levelname == "ERROR" and "test-context" in r.getMessage()
        for r in caplog.records
    ), "EmbeddingError must be logged at ERROR, not WARNING"

    evt = _last_activity_event()
    assert evt is not None
    assert evt["level"] == "error"
    assert "simulated Ollama outage" in evt["message"]


def test_index_all_wrapper_never_raises_out(isolated_pkb, monkeypatch):
    """The wrapper is called from timer callbacks and startup hooks that
    must never crash the caller — it degrades, it does not propagate."""
    import arail.pkb_index as pki
    import arail.pkb as pkb_mod

    def raising_index_all(root=None, **kwargs):
        raise EmbeddingError("simulated outage")

    monkeypatch.setattr(pkb_mod, "index_all", raising_index_all)
    # Must not raise.
    pki._index_all_reporting_embedding_errors(isolated_pkb, "ctx")


def test_index_all_wrapper_non_embedding_exception_keeps_warning_behaviour(
    isolated_pkb, monkeypatch, caplog
):
    """A non-embedding exception (SKIP/DEGRADE class) keeps the original
    WARNING-only behaviour and does NOT set the degraded flag."""
    import arail.pkb_index as pki
    import arail.pkb as pkb_mod

    def raising_index_all(root=None, **kwargs):
        raise RuntimeError("unrelated failure")

    monkeypatch.setattr(pkb_mod, "index_all", raising_index_all)

    with caplog.at_level("WARNING", logger="arail.pkb_index"):
        pki._index_all_reporting_embedding_errors(isolated_pkb, "ctx")

    ok, _ = pki.embedding_status()
    assert ok is True, "a non-embedding failure must not set the degraded flag"
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_index_all_wrapper_success_clears_prior_degraded_state(isolated_pkb, monkeypatch):
    import arail.pkb_index as pki
    import arail.pkb as pkb_mod

    pki.set_degraded("provider", "stale from a previous failure")

    def ok_index_all(root=None, **kwargs):
        return {"ok": True, "indexed": 0, "indexed_docs": 0, "path": None}

    monkeypatch.setattr(pkb_mod, "index_all", ok_index_all)
    pki._index_all_reporting_embedding_errors(isolated_pkb, "ctx")

    ok, reason = pki.embedding_status()
    assert ok is True
    assert reason == ""


# --------------------------------------------------------------------------
# FM17 — _flush aborts (not per-path-retries) on EmbeddingError, backs off
# --------------------------------------------------------------------------

def test_flush_aborts_on_embedding_error_keeps_pending_and_degrades(
    isolated_pkb, monkeypatch
):
    import arail.pkb_index as pki
    import lancedb  # type: ignore[import-not-found]
    from arail.vector_index import hash_embedding

    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[{
        "path": "_seed.md", "name": "_seed.md",
        "vector": hash_embedding("seed"), "mtime": 0.0, "source_kind": "user",
    }], mode="overwrite")

    notes = isolated_pkb / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    (notes / "a.md").write_text("# a\n")
    (notes / "b.md").write_text("# b\n")

    def raising_build_row(abs_path, rel_posix, source_kind):
        raise EmbeddingError("simulated outage mid-flush")

    monkeypatch.setattr(pki, "_build_row", raising_build_row)

    pki._pkb_root_cache = isolated_pkb
    pki._initialized = True
    with pki._lock:
        pki._pending.update({"notes/a.md", "notes/b.md"})

    pki._flush()

    with pki._lock:
        pending_after = set(pki._pending)
    assert pending_after == {"notes/a.md", "notes/b.md"}, (
        "an EmbeddingError mid-flush must not be recorded as a per-path "
        "failure — the whole snapshot stays pending for the next arm")

    ok, reason = pki.embedding_status()
    assert ok is False
    assert "simulated outage mid-flush" in reason

    # The retry timer must be armed at the 60s back-off, not the 2s default.
    with pki._lock:
        assert pki._timer is not None
        assert pki._timer.interval == pki._ERROR_BACKOFF_SEC
        pki._timer.cancel()


def test_flush_single_failure_does_not_rearm_at_normal_debounce(isolated_pkb, monkeypatch):
    """FM17, stated directly: a single EmbeddingError must not leave the
    system armed to retry within the normal (2s) debounce window."""
    import arail.pkb_index as pki
    import lancedb  # type: ignore[import-not-found]
    from arail.vector_index import hash_embedding

    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[{
        "path": "_seed.md", "name": "_seed.md",
        "vector": hash_embedding("seed"), "mtime": 0.0, "source_kind": "user",
    }], mode="overwrite")

    notes = isolated_pkb / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    (notes / "a.md").write_text("# a\n")

    monkeypatch.setattr(
        pki, "_build_row",
        lambda *a, **k: (_ for _ in ()).throw(EmbeddingError("outage")))

    pki._pkb_root_cache = isolated_pkb
    pki._initialized = True
    with pki._lock:
        pki._pending.add("notes/a.md")

    pki._flush()

    with pki._lock:
        interval = pki._timer.interval
        pki._timer.cancel()
    assert interval != 2.0
    assert interval == 60.0


# --------------------------------------------------------------------------
# C4 read-side — ensure_ready never serves a query from disagreeing provenance
# --------------------------------------------------------------------------

def test_ensure_ready_degrades_when_provenance_missing(isolated_pkb, monkeypatch):
    import arail.pkb_index as pki
    import lancedb  # type: ignore[import-not-found]
    from arail.vector_index import hash_embedding

    # Dimension matches the (mocked) current spec, but there is no
    # provenance sidecar at all.
    monkeypatch.setattr(pki, "_vector_dim", lambda: 128)
    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[{
        "path": "notes/a.md", "name": "a.md",
        "vector": hash_embedding("a"), "mtime": 0.0, "source_kind": "user",
    }], mode="overwrite")

    pki.ensure_ready(isolated_pkb)

    ok, reason = pki.embedding_status()
    assert ok is False
    assert "provenance" in reason
    assert "pkb reembed" in reason


def test_ensure_ready_degrades_when_provenance_disagrees_with_spec(isolated_pkb, monkeypatch):
    import arail.pkb_index as pki
    import arail.pkb_provenance as prov
    import lancedb  # type: ignore[import-not-found]
    from arail.vector_index import hash_embedding

    monkeypatch.setattr(pki, "_vector_dim", lambda: 128)
    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[{
        "path": "notes/a.md", "name": "a.md",
        "vector": hash_embedding("a"), "mtime": 0.0, "source_kind": "user",
    }], mode="overwrite")
    prov.write(db_path, embedding_model="some-other-model", embedding_dim=128,
               spec_sha256="deadbeef", rows=1)

    pki.ensure_ready(isolated_pkb)

    ok, reason = pki.embedding_status()
    assert ok is False
    assert "some-other-model" in reason


def test_ensure_ready_proceeds_when_provenance_agrees(isolated_pkb, monkeypatch):
    import arail.pkb_index as pki
    import arail.pkb_provenance as prov
    from arail.dbspec.generated.models_registry import embedding_model
    import lancedb  # type: ignore[import-not-found]
    from arail.vector_index import hash_embedding

    monkeypatch.setattr(pki, "_vector_dim", lambda: 128)
    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    now = 99999999999.0
    db.create_table("pkb_pages", data=[{
        "path": "notes/a.md", "name": "a.md",
        "vector": hash_embedding("a"), "mtime": now, "source_kind": "user",
    }], mode="overwrite")
    prov.write(db_path, embedding_model=embedding_model().name, embedding_dim=128,
               spec_sha256="deadbeef", rows=1)

    (isolated_pkb / "notes").mkdir(parents=True, exist_ok=True)
    (isolated_pkb / "notes" / "a.md").write_text("# a\n")

    pki.ensure_ready(isolated_pkb)

    ok, _ = pki.embedding_status()
    assert ok is True, "matching provenance must not degrade — the staleness sweep should run instead"


# --------------------------------------------------------------------------
# REVIEW3.md BLOCK-3 — ensure_ready(build=False) is genuinely read-only
# --------------------------------------------------------------------------

def test_ensure_ready_build_false_on_no_index_creates_nothing(isolated_pkb, monkeypatch):
    import arail.pkb_index as pki
    from arail.dbspec import embed as embed_mod

    calls = []
    monkeypatch.setattr(embed_mod, "embed_documents", lambda texts: calls.append(texts) or [])

    (isolated_pkb / "notes").mkdir(parents=True, exist_ok=True)
    (isolated_pkb / "notes" / "a.md").write_text("# a\n")

    db_path = isolated_pkb / ".cache" / "lancedb"
    assert not db_path.exists()

    pki.ensure_ready(isolated_pkb, build=False)

    assert calls == [], "build=False must never call embed_documents"
    assert not db_path.exists(), "build=False must never create the index directory"
    ok, reason = pki.embedding_status()
    assert ok is False
    assert "empty" in pki.degraded_codes()
    assert "pkb reembed" in reason


def test_ensure_ready_build_false_on_missing_columns_reports_without_dropping(isolated_pkb):
    import arail.pkb_index as pki
    import lancedb  # type: ignore[import-not-found]
    from arail.vector_index import hash_embedding

    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[{
        "path": "notes/a.md", "name": "a.md", "vector": hash_embedding("a"),
        # no mtime / source_kind -- legacy schema missing required columns
    }], mode="overwrite")

    pki.ensure_ready(isolated_pkb, build=False)

    ok, _ = pki.embedding_status()
    assert ok is False
    assert "dimension" in pki.degraded_codes()  # reused code for any schema issue

    # Table must be untouched -- not dropped, not rebuilt.
    db2 = lancedb.connect(str(db_path))
    table2 = db2.open_table("pkb_pages")
    assert table2.count_rows() == 1


def test_ensure_ready_build_false_on_dim_mismatch_reports_without_dropping(isolated_pkb):
    """Already read-only before BLOCK-3 (dimension mismatch never drops
    regardless of build), pinned here alongside the new build=False tests
    for completeness."""
    import arail.pkb_index as pki
    import lancedb  # type: ignore[import-not-found]

    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[{
        "path": "notes/a.md", "name": "a.md", "vector": [0.1] * 64,
        "mtime": 0.0, "source_kind": "user",
    }], mode="overwrite")

    pki.ensure_ready(isolated_pkb, build=False)

    ok, _ = pki.embedding_status()
    assert ok is False
    assert "dimension" in pki.degraded_codes()
    db2 = lancedb.connect(str(db_path))
    assert db2.open_table("pkb_pages").count_rows() == 1


def test_ensure_ready_build_false_on_healthy_index_does_not_sweep(isolated_pkb, monkeypatch):
    """build=False must skip the staleness sweep entirely -- it SCHEDULES
    upserts (an eventual debounced embed call), which is exactly the
    mutation build=False promises not to cause."""
    import arail.pkb_index as pki
    import arail.pkb_provenance as prov
    from arail.dbspec.generated.models_registry import embedding_model
    import lancedb  # type: ignore[import-not-found]
    from arail.vector_index import hash_embedding

    monkeypatch.setattr(pki, "_vector_dim", lambda: 128)
    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[{
        "path": "notes/a.md", "name": "a.md",
        "vector": hash_embedding("a"), "mtime": 0.0,  # old mtime -> stale
        "source_kind": "user",
    }], mode="overwrite")
    prov.write(db_path, embedding_model=embedding_model().name, embedding_dim=128,
               spec_sha256="deadbeef", rows=1)

    (isolated_pkb / "notes").mkdir(parents=True, exist_ok=True)
    (isolated_pkb / "notes" / "a.md").write_text("# a, definitely newer\n")

    pki.ensure_ready(isolated_pkb, build=False)

    ok, _ = pki.embedding_status()
    assert ok is True  # schema + provenance both fine
    with pki._lock:
        pending = set(pki._pending)
    assert pending == set(), "build=False must not schedule any upserts via the staleness sweep"
