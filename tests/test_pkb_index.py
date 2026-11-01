"""Unit tests for arail.pkb_index.

These tests cover the module-level coalescer, schema validation, staleness
sweep, path safety, and LanceDB-unavailable graceful degradation.

Every test resets module state via the pkb_index_reset fixture so tests
are fully isolated from one another.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def pkb_index_reset():
    """Reset pkb_index module state before and after each test."""
    import arail.pkb_index as pki
    pki._reset_for_tests()
    yield
    pki._reset_for_tests()


# ── schedule_upsert: dedup ────────────────────────────────────────────────

def test_schedule_upsert_dedupes_same_path(tmp_path: Path):
    """Two schedule_upsert calls for the same path inside the debounce
    window produce exactly one entry in _pending (set-dedup)."""
    import arail.pkb_index as pki

    # Use a very long debounce so the timer doesn't fire during the test.
    with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "60"}):
        f = tmp_path / "agents" / "research" / "note.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("hello")

        pki.schedule_upsert(f, pkb_root=tmp_path)
        pki.schedule_upsert(f, pkb_root=tmp_path)
        pki.schedule_upsert(f, pkb_root=tmp_path)

        with pki._lock:
            count = len(pki._pending)

    assert count == 1, f"expected 1 pending entry; got {count}"


# ── schedule_upsert: path normalization ───────────────────────────────────

def test_schedule_upsert_normalizes_paths(tmp_path: Path):
    """Paths are stored as POSIX strings (forward slashes) regardless of OS."""
    import arail.pkb_index as pki

    with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "60"}):
        f = tmp_path / "agents" / "research" / "note.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("hello")

        pki.schedule_upsert(f, pkb_root=tmp_path)

        with pki._lock:
            pending = set(pki._pending)

    assert len(pending) == 1
    stored = next(iter(pending))
    assert "\\" not in stored, f"expected POSIX path; got: {stored!r}"
    assert stored == "agents/research/note.md"


# ── schedule_upsert: path traversal ──────────────────────────────────────

def test_path_traversal_rejected(tmp_path: Path):
    """A path outside pkb_root is silently rejected — no entry in _pending."""
    import arail.pkb_index as pki

    with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "60"}):
        evil_path = tmp_path / ".." / ".." / "etc" / "passwd"
        # schedule_upsert must not raise and must not insert the path.
        pki.schedule_upsert(evil_path, pkb_root=tmp_path)

        with pki._lock:
            pending = set(pki._pending)

    assert not pending, f"expected empty pending; got {pending}"


# ── _flush: missing file treated as delete ────────────────────────────────

def test_flush_handles_missing_file_as_delete(tmp_path: Path):
    """If a file is unlinked between schedule_upsert and flush, its row
    is deleted from the table (not inserted as a ghost row)."""
    import arail.pkb_index as pki
    from arail.vector_index import hash_embedding
    import lancedb  # type: ignore[import-not-found]

    db_path = tmp_path / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))

    # Pre-populate the table with the file's row.
    rel = "agents/research/ghost.md"
    existing_row = {
        "path": rel,
        "name": "ghost.md",
        "vector": hash_embedding("ghost"),
        "mtime": 0.0,
        "source_kind": "agent_research",
    }
    db.create_table("pkb_pages", data=[existing_row], mode="overwrite")

    # The file itself does NOT exist on disk.
    ghost = tmp_path / rel  # file not created

    with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "60"}):
        pki._pkb_root_cache = tmp_path
        pki._initialized = True

        with pki._lock:
            pki._pending.add(rel)

        # Call _flush directly (bypass timer).
        pki._flush()

    # Verify the row was deleted.
    db2 = lancedb.connect(str(db_path))
    table = db2.open_table("pkb_pages")
    rows = table.to_pandas()
    assert len(rows) == 0 or "ghost.md" not in rows["name"].tolist(), \
        "ghost row should have been deleted"


# ── ensure_ready: legacy table triggers rebuild ───────────────────────────

def test_ensure_ready_legacy_table_triggers_rebuild(tmp_path: Path, monkeypatch):
    """A table with only {path, name, vector} (missing mtime/source_kind)
    must trigger a full index_all rebuild."""
    import arail.pkb_index as pki
    from arail.vector_index import hash_embedding
    import arail.pkb as pkb_mod
    import lancedb  # type: ignore[import-not-found]

    # Create legacy table (no mtime / source_kind).
    db_path = tmp_path / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[{
        "path": "notes/old.md",
        "name": "old.md",
        "vector": hash_embedding("old content"),
    }], mode="overwrite")

    # Provide a real file for index_all to find.
    notes = tmp_path / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    (notes / "old.md").write_text("# Old\n\nLegacy content.\n")

    rebuild_called = []
    original_index_all = pkb_mod.index_all

    def mock_index_all(root=None):
        rebuild_called.append(True)
        return original_index_all(root or tmp_path)

    monkeypatch.setattr(pkb_mod, "index_all", mock_index_all)

    pki.ensure_ready(tmp_path)

    assert rebuild_called, "index_all should have been called for legacy schema"


# ── ensure_ready: compatible table reuses ────────────────────────────────

def test_ensure_ready_compatible_table_reuses(tmp_path: Path, monkeypatch):
    """A table with all required columns at the right vector dim must be
    reused without calling index_all."""
    import os
    import arail.pkb_index as pki
    import arail.pkb as pkb_mod
    from arail.vector_index import hash_embedding
    import lancedb  # type: ignore[import-not-found]

    # This test is about schema-compatibility reuse, not the embedding
    # provider or provenance (C2/C4) — see test_ensure_ready_staleness_sweep
    # for the same pattern.
    monkeypatch.setattr(pki, "_vector_dim", lambda: 128)
    monkeypatch.setattr("arail.pkb_provenance.agrees_with_spec", lambda *a, **k: True)

    db_path = tmp_path / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))

    # Create a schema-compatible table with N rows.
    notes = tmp_path / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(3):
        f = notes / f"file{i}.md"
        f.write_text(f"content {i}")
        # Set a fixed old mtime so the staleness sweep sees no change.
        os.utime(f, (1000.0 + i, 1000.0 + i))
        rows.append({
            "path": f"notes/file{i}.md",
            "name": f"file{i}.md",
            "vector": hash_embedding(f"content {i}"),
            "mtime": 1000.0 + i,
            "source_kind": "user",
        })
    db.create_table("pkb_pages", data=rows, mode="overwrite")

    rebuild_called = []

    def mock_index_all(root=None):
        rebuild_called.append(True)

    monkeypatch.setattr(pkb_mod, "index_all", mock_index_all)

    pki.ensure_ready(tmp_path)

    assert not rebuild_called, "index_all must NOT be called when schema is compatible"

    # Row count must be unchanged.
    db2 = lancedb.connect(str(db_path))
    t = db2.open_table("pkb_pages")
    assert t.count_rows() == 3


# ── ensure_ready: staleness sweep ────────────────────────────────────────

def test_ensure_ready_staleness_sweep(tmp_path: Path, monkeypatch):
    """A file with a newer mtime than its table row must be re-upserted
    after ensure_ready finishes (within debounce window)."""
    import arail.pkb_index as pki
    from arail.vector_index import hash_embedding
    import lancedb  # type: ignore[import-not-found]
    import os

    # This test is about the staleness sweep, not the embedding provider or
    # provenance (C2/C4) — stub the provenance check to "agrees" so
    # ensure_ready falls through to the staleness sweep this test actually
    # exercises. The seed vector's dimension must match what the (stubbed,
    # tests/conftest.py) embedder actually produces (768, the spec's real
    # declared dimension) since the sweep incrementally upserts a NEW row
    # into this SAME table, and a dimension conflict inside one LanceDB
    # table is a hard write failure, not a soft one.
    monkeypatch.setattr("arail.pkb_provenance.agrees_with_spec", lambda *a, **k: True)

    db_path = tmp_path / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))

    notes = tmp_path / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    f = notes / "stale.md"
    f.write_text("# Stale\n\nOld content.\n")

    # Insert a row with a very old mtime so the sweep detects staleness.
    old_mtime = 1000.0
    db.create_table("pkb_pages", data=[{
        "path": "notes/stale.md",
        "name": "stale.md",
        "vector": hash_embedding("old content", dim=768),
        "mtime": old_mtime,
        "source_kind": "user",
    }], mode="overwrite")

    # The file has a much newer mtime (current time after writing).
    disk_mtime = f.stat().st_mtime
    assert disk_mtime > old_mtime, "test setup: file must be newer than table row"

    # Use a short debounce so the flush fires quickly.
    with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "0.1"}):
        pki.ensure_ready(tmp_path)
        # Wait for debounce to fire.
        time.sleep(0.5)

    with pki._lock:
        pending = set(pki._pending)

    # After flush _pending should be empty (the path was processed).
    assert not pending, f"pending should be empty after flush; got {pending}"

    # The table row should now have the updated mtime.
    db2 = lancedb.connect(str(db_path))
    t = db2.open_table("pkb_pages")
    rows = t.to_pandas()
    updated_mtime = float(rows[rows["path"] == "notes/stale.md"]["mtime"].iloc[0])
    assert updated_mtime > old_mtime, "table row mtime should be updated after staleness sweep flush"


# ── LanceDB unavailable ───────────────────────────────────────────────────

def test_lancedb_unavailable_is_silent(tmp_path: Path):
    """When vector_index.available() returns False, schedule_upsert must
    not raise and must not modify _pending."""
    import arail.pkb_index as pki

    f = tmp_path / "agents" / "research" / "note.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("content")

    with patch("arail.vector_index.available", return_value=False):
        # Should not raise.
        pki.schedule_upsert(f, pkb_root=tmp_path)

        with pki._lock:
            pending = set(pki._pending)

    assert not pending, "pending should be empty when LanceDB is unavailable"


# ── source_kind inference ─────────────────────────────────────────────────

def test_source_kind_for_various_paths():
    """_source_kind_for_path maps prefix → expected kind."""
    from arail.pkb_index import _source_kind_for_path

    cases = [
        ("agents/research/2026-05-01_x.md", "agent_research"),
        ("agents/experiments/run1.md", "agent_experiment"),
        ("agents/experiments/_rollup.md", "agent_experiment"),
        ("agents/synthesis/topic.md", "agent_synthesis"),
        ("agents/recommendations/rec.md", "agent_recommendation"),
        ("agents/buddy/dreams/2026-05-01.md", "agent_buddy_dream"),
        ("teacher/2026-05-01_12-00-00.md", "teacher_qa"),
        ("notes/scratch/my-note.md", "user"),
        ("sources/articles/paper.md", "user"),
    ]
    for path, expected in cases:
        assert _source_kind_for_path(path) == expected, f"path={path!r} expected {expected!r}"
