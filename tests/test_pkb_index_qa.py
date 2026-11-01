"""QA paranoid edge-case tests for arail.pkb_index.

These tests close the three minor findings the architect's REVIEW.md left
for QA, and add the QA-specific paranoia layer:

* merge_insert-absent fallback (Finding #1)
* staleness-sweep cap=200 boundary crossing (Finding #3)
* debounce coalescing for the same path within the window
* SIGTERM-style mid-flush survival (next boot's staleness sweep recovers)
* file deleted between schedule_upsert and flush keeps no stale row
* hostile filenames (unicode, spaces, single-quote SQL-ish, null byte)
* path-traversal corpus (../, absolute outside, symlink escape)
* airgapped strict no-network claim (socket.socket patched)
* index_all error fallback to regex still serves search()
* end-to-end witness scenario per VISION.md threshold #3
* concurrent same-path writes (set-dedup correctness under racing threads)
* deeply nested + long-name paths

Marked QA paranoia tests with pytest.mark.qa (registered below). Tests are
fully isolated via the autouse pkb_index_reset fixture.
"""

from __future__ import annotations

import importlib
import os
import socket
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def pkb_index_reset():
    """Reset pkb_index module state before and after each test."""
    import arail.pkb_index as pki
    pki._reset_for_tests()
    yield
    pki._reset_for_tests()


@pytest.fixture
def isolated_pkb(tmp_path: Path, monkeypatch):
    """Provide a tmp_path-rooted PKB that pkb.py and pkb_index agree on."""
    monkeypatch.setenv("LAB_PKB", str(tmp_path))
    import arail.config
    import arail.pkb as pkb
    importlib.reload(arail.config)
    importlib.reload(pkb)
    pkb.scaffold(tmp_path)
    return tmp_path


# ── Finding #1 — merge_insert-absent fallback ────────────────────────────

def test_merge_insert_absent_falls_back_to_delete_add(isolated_pkb: Path):
    """When the LanceDB table lacks merge_insert (older pin), the upsert
    must succeed via delete+add and the row must end up in the table."""
    import arail.pkb_index as pki
    from arail.vector_index import hash_embedding
    import lancedb  # type: ignore[import-not-found]

    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[{
        "path": "_seed.md",
        "name": "_seed.md",
        "vector": hash_embedding("seed", dim=768),
        "mtime": 0.0,
        "source_kind": "user",
    }], mode="overwrite")

    # Wrap _open_table so we can strip merge_insert from the returned table.
    real_open_table = pki._open_table

    def stripping_open_table(db_arg, name):
        t = real_open_table(db_arg, name)
        if t is not None:
            # Remove the merge_insert attribute so the fallback path is taken.
            try:
                # Use object.__setattr__ via instance-dict bypass: easier to
                # just monkey-patch with a property-shadowing assignment.
                t.merge_insert = None  # type: ignore[attr-defined]
            except Exception:
                pass
        return t

    with patch.object(pki, "_open_table", side_effect=stripping_open_table):
        with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "60"}):
            target = isolated_pkb / "agents" / "research" / "fallback_test.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# Fallback\n\nDELETE_ADD_FALLBACK_TOKEN content.\n")

            pki._pkb_root_cache = isolated_pkb
            pki._initialized = True
            pki.schedule_upsert(target, pkb_root=isolated_pkb)

            # Bypass timer — flush directly.
            pki._flush()

    # Reopen the table fresh and assert the row landed.
    db2 = lancedb.connect(str(db_path))
    table = db2.open_table("pkb_pages")
    rows = table.to_pandas()
    assert "agents/research/fallback_test.md" in rows["path"].values, \
        "delete+add fallback must land the row in the table"


def test_merge_insert_absent_idempotent_on_repeat(isolated_pkb: Path):
    """Calling the fallback path twice for the same file does not produce
    duplicate rows (delete-then-add must clean up the prior row)."""
    import arail.pkb_index as pki
    from arail.vector_index import hash_embedding
    import lancedb  # type: ignore[import-not-found]

    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[{
        "path": "_seed.md",
        "name": "_seed.md",
        "vector": hash_embedding("seed", dim=768),
        "mtime": 0.0,
        "source_kind": "user",
    }], mode="overwrite")

    real_open_table = pki._open_table

    def stripping_open_table(db_arg, name):
        t = real_open_table(db_arg, name)
        if t is not None:
            try:
                t.merge_insert = None  # type: ignore[attr-defined]
            except Exception:
                pass
        return t

    with patch.object(pki, "_open_table", side_effect=stripping_open_table):
        with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "60"}):
            target = isolated_pkb / "agents" / "research" / "idem_fallback.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("v1")

            pki._pkb_root_cache = isolated_pkb
            pki._initialized = True

            pki.schedule_upsert(target, pkb_root=isolated_pkb)
            pki._flush()

            target.write_text("v2")
            pki.schedule_upsert(target, pkb_root=isolated_pkb)
            pki._flush()

    db2 = lancedb.connect(str(db_path))
    table = db2.open_table("pkb_pages")
    rows = table.to_pandas()
    matches = rows[rows["path"] == "agents/research/idem_fallback.md"]
    assert len(matches) == 1, \
        f"fallback must be idempotent; got {len(matches)} rows for the same path"


# ── Finding #3 — staleness-sweep cap=200 boundary ────────────────────────

@pytest.mark.qa
def test_staleness_sweep_cap_exceeded_falls_back_to_index_all(
    isolated_pkb: Path, monkeypatch
):
    """When the staleness sweep finds more than _STALENESS_CAP stale files,
    it must clear _pending and call index_all() once."""
    import arail.pkb_index as pki
    import arail.pkb as pkb_mod
    from arail.vector_index import hash_embedding
    import lancedb  # type: ignore[import-not-found]

    # This test is about the staleness-sweep cap, not the embedding
    # provider or provenance (C2/C4) — see test_ensure_ready_staleness_sweep
    # in test_pkb_index.py for the same pattern.
    monkeypatch.setattr(pki, "_vector_dim", lambda: 128)
    monkeypatch.setattr("arail.pkb_provenance.agrees_with_spec", lambda *a, **k: True)

    # Lower the cap so the test is fast.
    monkeypatch.setattr(pki, "_STALENESS_CAP", 3)

    # Pre-populate an empty schema-correct table so ensure_ready does NOT
    # fall through to the "table missing" branch.
    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[{
        "path": "_seed.md",
        "name": "_seed.md",
        "vector": hash_embedding("seed"),
        "mtime": 9999999999.0,  # newer than all on-disk
        "source_kind": "user",
    }], mode="overwrite")

    # Drop 5 fresh files into a scanned directory; with cap=3 the sweep
    # must trip after the 4th file and bail to index_all.
    notes = isolated_pkb / "notes" / "scratch"
    notes.mkdir(parents=True, exist_ok=True)
    for i in range(5):
        (notes / f"stale_{i:02d}.md").write_text(f"# Stale {i}\n")

    rebuild_calls: list[bool] = []
    original_index_all = pkb_mod.index_all

    def patched_index_all(root=None):
        rebuild_calls.append(True)
        return original_index_all(root or isolated_pkb)

    monkeypatch.setattr(pkb_mod, "index_all", patched_index_all)

    pki.ensure_ready(isolated_pkb)

    assert rebuild_calls, \
        "exceeding the staleness cap must trigger index_all once"
    # Pending must be cleared so a stray flush does not re-process the
    # cap-exceeded snapshot after index_all already covered it.
    with pki._lock:
        pending_after = set(pki._pending)
    assert not pending_after, \
        f"_pending must be empty after cap-exceeded fallback; got {pending_after}"


@pytest.mark.qa
def test_staleness_sweep_at_cap_does_not_trigger_fallback(
    isolated_pkb: Path, monkeypatch
):
    """Boundary: exactly _STALENESS_CAP stale files must NOT trigger the
    full rebuild. Only > cap does."""
    import arail.pkb_index as pki
    import arail.pkb as pkb_mod
    from arail.vector_index import hash_embedding
    import lancedb  # type: ignore[import-not-found]

    monkeypatch.setattr(pki, "_STALENESS_CAP", 3)

    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[{
        "path": "_seed.md",
        "name": "_seed.md",
        "vector": hash_embedding("seed"),
        "mtime": 9999999999.0,
        "source_kind": "user",
    }], mode="overwrite")

    notes = isolated_pkb / "notes" / "scratch"
    notes.mkdir(parents=True, exist_ok=True)
    # Exactly cap=3 stale files.
    for i in range(3):
        (notes / f"borderline_{i}.md").write_text(f"content {i}")

    rebuild_calls: list[bool] = []
    original_index_all = pkb_mod.index_all

    def patched_index_all(root=None):
        rebuild_calls.append(True)
        return original_index_all(root or isolated_pkb)

    monkeypatch.setattr(pkb_mod, "index_all", patched_index_all)

    pki.ensure_ready(isolated_pkb)

    assert not rebuild_calls, \
        f"at-cap (3 stale, cap=3) must NOT trigger index_all; got {len(rebuild_calls)}"


# ── Debounce coalescing for the same path within the window ──────────────

@pytest.mark.qa
def test_debouncer_coalesces_two_writes_10ms_apart(isolated_pkb: Path):
    """Two writes to the SAME path 10 ms apart must produce one row,
    one flush — set-dedup + a single fired timer."""
    import arail.pkb_index as pki
    from arail.vector_index import hash_embedding
    import lancedb  # type: ignore[import-not-found]

    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[{
        "path": "_seed.md",
        "name": "_seed.md",
        "vector": hash_embedding("seed", dim=768),
        "mtime": 0.0,
        "source_kind": "user",
    }], mode="overwrite")

    pki._pkb_root_cache = isolated_pkb
    pki._initialized = True

    target = isolated_pkb / "agents" / "research" / "coalesce.md"
    target.parent.mkdir(parents=True, exist_ok=True)

    flush_calls: list[int] = []
    real_flush = pki._flush

    def counting_flush():
        flush_calls.append(1)
        real_flush()

    with patch.object(pki, "_flush", side_effect=counting_flush):
        with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "0.4"}):
            target.write_text("v1")
            pki.schedule_upsert(target, pkb_root=isolated_pkb)
            time.sleep(0.01)
            target.write_text("v2-final")
            pki.schedule_upsert(target, pkb_root=isolated_pkb)
            # Wait for the single coalesced flush.
            time.sleep(1.0)

    assert len(flush_calls) == 1, \
        f"two writes 10 ms apart must coalesce to one flush; got {len(flush_calls)}"

    db2 = lancedb.connect(str(db_path))
    table = db2.open_table("pkb_pages")
    rows = table.to_pandas()
    matches = rows[rows["path"] == "agents/research/coalesce.md"]
    assert len(matches) == 1, \
        f"coalesced upsert must produce exactly 1 row; got {len(matches)}"


# ── SIGTERM-style: pending write lost, next boot recovers via sweep ──────

@pytest.mark.qa
def test_pending_write_recovered_by_next_boot_sweep(isolated_pkb: Path, monkeypatch):
    """Simulate: schedule_upsert fires, debounce timer is pending, process
    is killed before flush. Next process boot's ensure_ready staleness
    sweep must catch the un-indexed file and queue it."""
    import arail.pkb_index as pki
    from arail.vector_index import hash_embedding
    import lancedb  # type: ignore[import-not-found]

    # This test is about the debounce/sweep recovery path, not the
    # embedding provider or provenance (C2/C4). The seed vector's
    # dimension must match what the (stubbed) embedder actually produces
    # (768) since this test's sweep incrementally upserts a NEW row into
    # this SAME table.
    monkeypatch.setattr("arail.pkb_provenance.agrees_with_spec", lambda *a, **k: True)

    # Pre-populate a schema-correct table that is empty of agent files.
    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[{
        "path": "_seed.md",
        "name": "_seed.md",
        "vector": hash_embedding("seed", dim=768),
        "mtime": 0.0,  # very old; on-disk file will be newer
        "source_kind": "user",
    }], mode="overwrite")

    # Write a file as if a helper ran but the flush never landed.
    lost = isolated_pkb / "agents" / "research" / "lost_write.md"
    lost.parent.mkdir(parents=True, exist_ok=True)
    lost.write_text("# Lost\n\nLOST_AFTER_SIGTERM_TOKEN payload.\n")

    # Schedule the upsert but cancel before flush — simulates the SIGTERM.
    pki._pkb_root_cache = isolated_pkb
    pki._initialized = True
    with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "60"}):
        pki.schedule_upsert(lost, pkb_root=isolated_pkb)
        # Verify the upsert is pending.
        with pki._lock:
            assert "agents/research/lost_write.md" in pki._pending

    # Hard reset (process death simulation): drop _pending, _timer, _init.
    pki._reset_for_tests()
    with pki._lock:
        assert not pki._pending  # state really is gone

    # Cold boot: ensure_ready should detect the file is on-disk-newer than
    # any existing row and enqueue it via the staleness sweep.
    with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "0.2"}):
        pki.ensure_ready(isolated_pkb)
        time.sleep(0.7)  # let the staleness-sweep flush land

    db2 = lancedb.connect(str(db_path))
    table = db2.open_table("pkb_pages")
    rows = table.to_pandas()
    assert "agents/research/lost_write.md" in rows["path"].values, \
        "post-SIGTERM cold boot must recover the lost write via staleness sweep"


# ── File deleted between upsert and the next sweep — no ghost row ────────

@pytest.mark.qa
def test_file_deleted_after_upsert_then_sweep_drops_row(isolated_pkb: Path, monkeypatch):
    """File is upserted (row exists), then unlinked from disk. The next
    cold-start ensure_ready sweep must DELETE the orphan row — not leave it
    around forever."""
    import arail.pkb_index as pki
    from arail.vector_index import hash_embedding
    import lancedb  # type: ignore[import-not-found]

    # This test is about the delete-on-sweep path, not the embedding
    # provider or provenance (C2/C4).
    monkeypatch.setattr(pki, "_vector_dim", lambda: 128)
    monkeypatch.setattr("arail.pkb_provenance.agrees_with_spec", lambda *a, **k: True)

    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))

    # Write a file and pre-index it (fake the flush).
    f = isolated_pkb / "agents" / "research" / "transient.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("# transient\n\ndoomed content.\n")
    rel = "agents/research/transient.md"
    db.create_table("pkb_pages", data=[{
        "path": rel,
        "name": "transient.md",
        "vector": hash_embedding("transient"),
        "mtime": f.stat().st_mtime,
        "source_kind": "agent_research",
    }], mode="overwrite")

    # Now delete the file before the next sweep.
    f.unlink()
    assert not f.exists()

    # Cold boot: sweep should detect the orphan row and queue it for delete.
    with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "0.2"}):
        pki.ensure_ready(isolated_pkb)
        time.sleep(0.7)  # let the delete flush land

    db2 = lancedb.connect(str(db_path))
    table = db2.open_table("pkb_pages")
    rows = table.to_pandas()
    assert rel not in rows["path"].values, \
        "orphan row must be removed by the staleness sweep delete pass"


# ── Hostile filenames ─────────────────────────────────────────────────────

@pytest.mark.qa
def test_filename_with_single_quote_does_not_break_delete_clause(
    isolated_pkb: Path,
):
    """A filename containing a single quote must not let SQL injection
    leak into the table.delete clause when the file is missing-and-deleted.
    The escape pattern in _flush is `''` (SQL doubled). Verify."""
    import arail.pkb_index as pki
    from arail.vector_index import hash_embedding
    import lancedb  # type: ignore[import-not-found]

    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))

    # Pre-create a row whose path contains a single quote AND a
    # "harmless" companion row that we want to confirm is NOT touched
    # by an injection attack.
    quote_rel = "agents/research/it's_a_quote.md"
    db.create_table("pkb_pages", data=[
        {
            "path": quote_rel,
            "name": "it's_a_quote.md",
            "vector": hash_embedding("a"),
            "mtime": 0.0,
            "source_kind": "agent_research",
        },
        {
            "path": "agents/research/innocent.md",
            "name": "innocent.md",
            "vector": hash_embedding("b"),
            "mtime": 0.0,
            "source_kind": "agent_research",
        },
    ], mode="overwrite")

    # The file does NOT exist on disk; _flush should run the delete branch
    # with the doubled-quote escape.
    pki._pkb_root_cache = isolated_pkb
    pki._initialized = True
    with pki._lock:
        pki._pending.add(quote_rel)

    # Should not raise (the escape `''` is correct).
    pki._flush()

    db2 = lancedb.connect(str(db_path))
    table = db2.open_table("pkb_pages")
    rows = table.to_pandas()
    # Quote row deleted; innocent row preserved.
    assert quote_rel not in rows["path"].values, \
        "quoted row should be deleted cleanly via escape"
    assert "agents/research/innocent.md" in rows["path"].values, \
        "innocent row must NOT be collateral damage from a poorly escaped delete"


@pytest.mark.qa
def test_unicode_filename_round_trips_through_upsert(isolated_pkb: Path):
    """A filename with non-ASCII characters must upsert and search cleanly."""
    import arail.pkb_index as pki
    from arail.vector_index import hash_embedding
    import lancedb  # type: ignore[import-not-found]

    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[{
        "path": "_seed.md",
        "name": "_seed.md",
        "vector": hash_embedding("seed", dim=768),
        "mtime": 0.0,
        "source_kind": "user",
    }], mode="overwrite")

    pki._pkb_root_cache = isolated_pkb
    pki._initialized = True

    target = isolated_pkb / "agents" / "research" / "résumé_日本語_🚀.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Unicode test\n\nΣ Δ Ω content.\n")

    with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "60"}):
        pki.schedule_upsert(target, pkb_root=isolated_pkb)

    pki._flush()

    db2 = lancedb.connect(str(db_path))
    table = db2.open_table("pkb_pages")
    rows = table.to_pandas()
    rel = "agents/research/résumé_日本語_🚀.md"
    assert rel in rows["path"].values, \
        f"unicode filename must upsert cleanly; got paths {list(rows['path'].values)}"


@pytest.mark.qa
def test_filename_with_spaces_round_trips(isolated_pkb: Path):
    """Filenames with spaces must upsert and survive a delete."""
    import arail.pkb_index as pki
    from arail.vector_index import hash_embedding
    import lancedb  # type: ignore[import-not-found]

    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[{
        "path": "_seed.md",
        "name": "_seed.md",
        "vector": hash_embedding("seed", dim=768),
        "mtime": 0.0,
        "source_kind": "user",
    }], mode="overwrite")

    pki._pkb_root_cache = isolated_pkb
    pki._initialized = True

    target = isolated_pkb / "agents" / "research" / "file with spaces.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Spaced filename\nContent.\n")

    with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "60"}):
        pki.schedule_upsert(target, pkb_root=isolated_pkb)
    pki._flush()

    db2 = lancedb.connect(str(db_path))
    table = db2.open_table("pkb_pages")
    rows = table.to_pandas()
    assert "agents/research/file with spaces.md" in rows["path"].values


# ── Path-traversal corpus ────────────────────────────────────────────────

@pytest.mark.qa
@pytest.mark.parametrize("evil_suffix", [
    "../../etc/passwd",
    "../../../../../../etc/shadow",
    "./../../sensitive.txt",
    "subdir/../../../escape.md",
])
def test_path_traversal_corpus_rejected(
    isolated_pkb: Path, evil_suffix: str,
):
    """A corpus of traversal patterns must all be rejected with no
    entry in _pending and no exception raised.

    Note: Windows-style backslash traversal (``..\\..\\windows\\system32``)
    is intentionally NOT in this corpus on POSIX. On POSIX, ``\\`` is a
    literal filename character — ``..\\..\\windows\\system32`` is a
    single-component filename that DOES resolve inside pkb_root, so it
    is correctly accepted. On Windows, ``\\`` is a separator and the
    same string would be a real traversal — but pkb_index.py will be
    running under a Windows Python so ``Path.resolve()`` collapses it
    correctly there. This asymmetry is OS-correct, not a vuln."""
    import arail.pkb_index as pki

    pki._pkb_root_cache = isolated_pkb
    pki._initialized = True

    with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "60"}):
        evil = isolated_pkb / evil_suffix
        # No raise.
        pki.schedule_upsert(evil, pkb_root=isolated_pkb)
        with pki._lock:
            pending = set(pki._pending)
    assert not pending, \
        f"traversal {evil_suffix!r} should be rejected; pending={pending}"


@pytest.mark.qa
def test_absolute_path_outside_pkb_rejected(tmp_path: Path):
    """An absolute path entirely outside pkb_root is rejected silently."""
    import arail.pkb_index as pki

    pkb_root = tmp_path / "lab" / "pkb"
    pkb_root.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside" / "secret.md"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("secret")

    pki._pkb_root_cache = pkb_root
    pki._initialized = True

    with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "60"}):
        pki.schedule_upsert(outside, pkb_root=pkb_root)
        with pki._lock:
            pending = set(pki._pending)
    assert not pending, "absolute path outside pkb_root must be rejected"


@pytest.mark.qa
def test_null_byte_in_path_rejected(isolated_pkb: Path):
    """A path with an embedded null byte raises ValueError inside
    Path.resolve(); schedule_upsert must catch it and silently reject —
    not propagate the exception up to the write helper."""
    import arail.pkb_index as pki

    pki._pkb_root_cache = isolated_pkb
    pki._initialized = True

    evil = isolated_pkb / "agents" / "research" / "foo\x00bar.md"

    with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "60"}):
        # Must not raise — the contract is "never break the file write".
        pki.schedule_upsert(evil, pkb_root=isolated_pkb)
        with pki._lock:
            pending = set(pki._pending)

    assert not pending, \
        f"null-byte path must be rejected; got pending={pending}"


@pytest.mark.qa
def test_symlink_escape_rejected(tmp_path: Path):
    """A symlink under pkb_root pointing outside must be rejected by the
    resolve()-based traversal check."""
    import arail.pkb_index as pki

    pkb_root = tmp_path / "lab" / "pkb"
    pkb_root.mkdir(parents=True, exist_ok=True)

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir(parents=True, exist_ok=True)
    secret = outside_dir / "secret.md"
    secret.write_text("dont index me")

    # Symlink inside pkb_root pointing to outside file.
    link = pkb_root / "agents" / "research" / "trojan.md"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlink not supported on this platform")

    pki._pkb_root_cache = pkb_root
    pki._initialized = True

    with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "60"}):
        pki.schedule_upsert(link, pkb_root=pkb_root)
        with pki._lock:
            pending = set(pki._pending)
    assert not pending, \
        "symlink escape must resolve to outside-root and be rejected"


# ── Airgapped strict no-network claim ────────────────────────────────────

@pytest.mark.qa
def test_airgapped_strict_no_socket_during_full_round_trip(
    isolated_pkb: Path, monkeypatch
):
    """In airgapped mode, the entire write→flush→search round trip must
    open zero network sockets. We patch socket.socket to raise."""
    import arail.pkb as pkb
    import arail.pkb_index as pki
    from arail.vector_index import hash_embedding
    import lancedb  # type: ignore[import-not-found]

    monkeypatch.setenv("LAB_MODE", "airgapped")

    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[{
        "path": "_seed.md",
        "name": "_seed.md",
        "vector": hash_embedding("seed"),
        "mtime": 0.0,
        "source_kind": "user",
    }], mode="overwrite")

    pki._pkb_root_cache = isolated_pkb
    pki._initialized = True

    real_socket = socket.socket
    socket_calls: list[tuple] = []

    def hostile_socket(*args, **kwargs):
        # AF_UNIX is fine (LanceDB or arrow may use it); only block AF_INET.
        family = args[0] if args else kwargs.get("family", socket.AF_INET)
        if family in (socket.AF_INET, socket.AF_INET6):
            socket_calls.append((args, kwargs))
            raise RuntimeError(
                "airgapped breach: AF_INET socket attempted during "
                "write→flush→search loop"
            )
        return real_socket(*args, **kwargs)

    with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "0.2"}):
        with patch("socket.socket", side_effect=hostile_socket):
            pkb.write_agent_research(
                "airgapped-test",
                "AIRGAPPED_PROOF_TOKEN content for round trip.",
                isolated_pkb,
            )
            time.sleep(0.6)

            hits = pkb.search("AIRGAPPED_PROOF_TOKEN", pkb_root=isolated_pkb)

    assert hits, "airgapped-mode write must still be searchable"
    assert not socket_calls, \
        f"airgapped mode must not open INET sockets; got {socket_calls}"


# ── Embedder is stdlib-only: missing model dir is irrelevant ─────────────

@pytest.mark.qa
def test_no_sentence_transformer_import_anywhere_in_module():
    """pkb_index must not import sentence_transformers (airgapped guarantee)."""
    import arail.pkb_index as pki  # noqa: F401
    # If the module imported sentence_transformers it would be in sys.modules
    # by now (we just imported pki). Verify it isn't.
    assert "sentence_transformers" not in sys.modules, \
        "pkb_index must not pull in sentence_transformers (airgapped)"


# ── index_all error fallback to regex still serves search() ──────────────

@pytest.mark.qa
def test_search_falls_back_to_regex_when_index_all_fails(
    isolated_pkb: Path, monkeypatch
):
    """If index_all itself errors at cold start, pkb.search must still
    serve regex-fallback hits (the chat path stays alive)."""
    import arail.pkb as pkb

    (isolated_pkb / "notes" / "scratch").mkdir(parents=True, exist_ok=True)
    (isolated_pkb / "notes" / "scratch" / "regex_target.md").write_text(
        "# Note\n\nUNIQUE_REGEX_FALLBACK_TOKEN appears here.\n"
    )

    # Force semantic search to return [].
    def empty_semantic(query, root, **kwargs):
        return []

    monkeypatch.setattr(pkb, "_semantic_search", empty_semantic)

    hits = pkb.search("UNIQUE_REGEX_FALLBACK_TOKEN", pkb_root=isolated_pkb)
    assert hits, "regex fallback must serve hits even when semantic search fails"
    assert hits[0]["source"] == "keyword"
    assert "UNIQUE_REGEX_FALLBACK_TOKEN" in str(hits[0]["snippets"])


# ── Concurrent same-path writes (race the set-dedup) ─────────────────────

@pytest.mark.qa
def test_concurrent_same_path_writes_produce_one_row(isolated_pkb: Path):
    """Eight threads scheduling the SAME path — set-dedup must collapse
    to exactly one row (not eight) regardless of interleaving."""
    import arail.pkb_index as pki
    from arail.vector_index import hash_embedding
    import lancedb  # type: ignore[import-not-found]

    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[{
        "path": "_seed.md",
        "name": "_seed.md",
        "vector": hash_embedding("seed"),
        "mtime": 0.0,
        "source_kind": "user",
    }], mode="overwrite")

    pki._pkb_root_cache = isolated_pkb
    pki._initialized = True

    target = isolated_pkb / "agents" / "research" / "race.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("racing content")

    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        pki.schedule_upsert(target, pkb_root=isolated_pkb)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "60"}):
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        with pki._lock:
            pending_count = len(pki._pending)

    assert pending_count == 1, \
        f"set-dedup must collapse 8 same-path writes to 1; got {pending_count}"


# ── Deeply nested + long-name path ──────────────────────────────────────

@pytest.mark.qa
def test_deeply_nested_path_round_trips(isolated_pkb: Path):
    """A 10-level deep relative path with a long basename must upsert."""
    import arail.pkb_index as pki
    from arail.vector_index import hash_embedding
    import lancedb  # type: ignore[import-not-found]

    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[{
        "path": "_seed.md",
        "name": "_seed.md",
        "vector": hash_embedding("seed", dim=768),
        "mtime": 0.0,
        "source_kind": "user",
    }], mode="overwrite")

    pki._pkb_root_cache = isolated_pkb
    pki._initialized = True

    deep_dir = isolated_pkb
    for i in range(10):
        deep_dir = deep_dir / f"level_{i}"
    deep_dir.mkdir(parents=True, exist_ok=True)

    long_name = ("x" * 80) + ".md"
    target = deep_dir / long_name
    target.write_text("deep content")

    with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "60"}):
        pki.schedule_upsert(target, pkb_root=isolated_pkb)
    pki._flush()

    db2 = lancedb.connect(str(db_path))
    table = db2.open_table("pkb_pages")
    rows = table.to_pandas()
    expected = "/".join([f"level_{i}" for i in range(10)] + [long_name])
    assert expected in rows["path"].values, \
        f"deep path must upsert; expected {expected!r}"


# ── End-to-end witness — VISION.md threshold #3 ──────────────────────────

@pytest.mark.e2e
def test_e2e_researcher_write_findable_within_10_seconds(
    isolated_pkb: Path,
):
    """VISION.md threshold #3, ASSERTED.

    Simulate the Researcher writing an experiment note. Within 10 seconds
    the content must surface via pkb.search(), exercising the full
    schedule_upsert → debounce → flush → LanceDB → search round trip
    with NO manual rebuild step.

    No real model call — we invoke the helper directly. The point is to
    prove the loop closes end-to-end through the live debouncer."""
    import arail.pkb as pkb
    import arail.pkb_index as pki
    from arail.vector_index import hash_embedding
    import lancedb  # type: ignore[import-not-found]

    # Build the schema-correct base table so search has something to query.
    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[{
        "path": "_seed.md",
        "name": "_seed.md",
        "vector": hash_embedding("seed"),
        "mtime": 0.0,
        "source_kind": "user",
    }], mode="overwrite")

    pki._pkb_root_cache = isolated_pkb
    pki._initialized = True

    # Use a realistic short debounce — the win condition is 10 s end-to-end.
    with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "0.5"}):
        # The Researcher writes its experiment finding.
        unique_token = "WITNESS_E2E_TOKEN_" + str(int(time.time() * 1000))
        pkb.write_agent_experiment(
            "exp-witness",
            f"# Experiment\n\nThe Researcher found: {unique_token}\n",
            isolated_pkb,
        )

        # Wait up to 10 s — the win-condition budget.
        t0 = time.monotonic()
        deadline = t0 + 10.0
        found = False
        last_hits: list = []
        while time.monotonic() < deadline:
            hits = pkb.search(unique_token, pkb_root=isolated_pkb)
            last_hits = hits
            if hits and any(unique_token in str(h.get("snippets", "")) or
                            "exp-witness" in str(h.get("path", ""))
                            for h in hits):
                found = True
                break
            time.sleep(0.2)
        latency = time.monotonic() - t0

    assert found, (
        f"E2E witness FAILED — wrote {unique_token!r} via "
        f"write_agent_experiment but pkb.search did not surface it "
        f"within 10 s. last_hits={last_hits}"
    )
    assert latency <= 10.0, (
        f"E2E witness latency {latency:.2f}s exceeded 10 s budget"
    )


@pytest.mark.e2e
def test_e2e_witness_survives_simulated_restart(isolated_pkb: Path):
    """VISION.md threshold #2 + #3 combined: write, simulate process death,
    cold-boot, and the original write must STILL be searchable."""
    import arail.pkb as pkb
    import arail.pkb_index as pki

    with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "0.3"}):
        # First "process": ensure_ready, write, wait for flush.
        pki.ensure_ready(isolated_pkb)
        unique_token = "DURABLE_E2E_TOKEN_" + str(int(time.time() * 1000))
        pkb.write_agent_research(
            "exp-durable",
            f"# Durable\n\n{unique_token} should outlive a restart.\n",
            isolated_pkb,
        )
        # Wait for the debounce + flush to complete.
        time.sleep(1.0)

        # "Process death" — drop module-level state.
        pki._reset_for_tests()

        # Cold boot.
        pki.ensure_ready(isolated_pkb)
        # Allow time for any post-sweep flush to fire.
        time.sleep(1.0)

        hits = pkb.search(unique_token, pkb_root=isolated_pkb)

    assert hits, (
        f"E2E durability FAILED — {unique_token!r} disappeared after "
        f"simulated restart. Win condition #2 violated."
    )


# ── Regression: write helpers still write the file even if upsert errors ─

def test_write_helper_succeeds_when_schedule_upsert_explodes(
    isolated_pkb: Path, monkeypatch
):
    """The contract: 'the file write to disk is never blocked or broken
    by an indexing failure'. Verify by making schedule_upsert raise."""
    import arail.pkb as pkb
    import arail.pkb_index as pki

    def boom(*a, **k):
        raise RuntimeError("simulated indexing catastrophe")

    monkeypatch.setattr(pki, "schedule_upsert", boom)

    # Helper must NOT raise.
    path = pkb.write_agent_research(
        "reg-test",
        "the file write must survive indexing failure",
        isolated_pkb,
    )

    assert path.exists(), "write helper must persist the file even when index errors"
    assert "the file write must survive" in path.read_text()


# ── Regression: empty-string upsert pending paths handled ────────────────

def test_empty_pending_flush_is_noop(isolated_pkb: Path):
    """_flush with no pending entries must return cleanly with no LanceDB
    interaction (and no logged 'upserted 0 rows')."""
    import arail.pkb_index as pki

    pki._pkb_root_cache = isolated_pkb
    pki._initialized = True

    # No pending entries; should not raise and should reset _timer to None.
    pki._timer = None
    pki._flush()

    with pki._lock:
        assert pki._timer is None
        assert not pki._pending


# ── Regression: index_all path still works on cold start ────────────────

def test_index_all_cold_start_still_works(isolated_pkb: Path):
    """index_all directly (bypassing pkb_index) still produces a usable
    table — guarantees the cold-start fallback path is intact."""
    import arail.pkb as pkb

    (isolated_pkb / "notes" / "scratch").mkdir(parents=True, exist_ok=True)
    (isolated_pkb / "notes" / "scratch" / "cold.md").write_text(
        "# Cold start\n\nCOLD_START_TOKEN_99 content.\n"
    )

    result = pkb.index_all(isolated_pkb)
    assert result["ok"] is True
    assert result["indexed"] >= 1

    # And the search path picks it up.
    hits = pkb.search("COLD_START_TOKEN_99", pkb_root=isolated_pkb)
    assert hits, "index_all + search must work end-to-end"


# ── schedule_upsert on a directory (not a file) ──────────────────────────

@pytest.mark.qa
def test_schedule_upsert_on_directory_does_not_crash(isolated_pkb: Path):
    """If schedule_upsert is somehow called with a directory path, the
    flush-time _build_row read_text will fail; the failed path should be
    retained in _pending and the flush must not raise."""
    import arail.pkb_index as pki
    from arail.vector_index import hash_embedding
    import lancedb  # type: ignore[import-not-found]

    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[{
        "path": "_seed.md",
        "name": "_seed.md",
        "vector": hash_embedding("seed"),
        "mtime": 0.0,
        "source_kind": "user",
    }], mode="overwrite")

    pki._pkb_root_cache = isolated_pkb
    pki._initialized = True

    # Pass a directory (not a file).
    a_dir = isolated_pkb / "agents" / "research"
    a_dir.mkdir(parents=True, exist_ok=True)

    with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "60"}):
        # schedule_upsert should accept the resolve()-relative path; flush
        # should then no-op for the row (read_text on a directory raises).
        pki.schedule_upsert(a_dir, pkb_root=isolated_pkb)
        # Bypass timer.
        pki._flush()  # must not raise

    # Sanity: no spurious row inserted for the directory.
    db2 = lancedb.connect(str(db_path))
    table = db2.open_table("pkb_pages")
    rows = table.to_pandas()
    assert "agents/research" not in rows["path"].values, \
        "directory path should not produce a row"


# ── LanceDB dir corrupted with random bytes ──────────────────────────────

@pytest.mark.qa
def test_corrupted_lancedb_dir_does_not_crash_ensure_ready(isolated_pkb: Path):
    """A LanceDB directory with garbage on disk (e.g., partial write, disk
    corruption) must not crash ensure_ready; the contract is best-effort
    indexing and graceful degradation to regex search."""
    import arail.pkb_index as pki

    # Create a malformed LanceDB cache directory with random bytes.
    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    # Create a mock pkb_pages.lance directory with garbage that will
    # confuse LanceDB on open.
    bogus_table = db_path / "pkb_pages.lance"
    bogus_table.mkdir(parents=True, exist_ok=True)
    (bogus_table / "_versions").mkdir(parents=True, exist_ok=True)
    (bogus_table / "_versions" / "1.manifest").write_bytes(b"\x00\xff\xde\xad\xbe\xef" * 50)
    (bogus_table / "data").mkdir(parents=True, exist_ok=True)
    (bogus_table / "data" / "garbage.lance").write_bytes(b"corrupted-not-a-real-lance-file" * 100)

    # ensure_ready must not raise even on corrupted state.
    try:
        pki.ensure_ready(isolated_pkb)
    except Exception as e:  # noqa: BLE001
        pytest.fail(
            f"ensure_ready must not propagate exceptions on corrupted "
            f"LanceDB dir; got {type(e).__name__}: {e}"
        )


# ── Wrong vector dim with otherwise correct schema ───────────────────────

@pytest.mark.qa
def test_wrong_vector_dim_never_drops_table(isolated_pkb: Path, monkeypatch):
    """C2/FM12: a table with all required columns but a vector dimension
    that disagrees with the current spec (e.g. 128-dim hash vectors vs a
    768-dim spec) must NEVER be dropped and rebuilt automatically — that is
    exactly how a 128->768 embedder change would silently empty every
    existing lab's index. It must degrade with an actionable message and
    leave the rows exactly as they are; only an explicit
    ``./arailctl pkb reembed`` may rewrite them."""
    import arail.pkb_index as pki
    import arail.pkb as pkb_mod
    import lancedb  # type: ignore[import-not-found]

    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))

    # Wrong-dim vectors (64) — all required columns present, dim disagrees
    # with the current spec's declared dimension (768).
    bad_vec = [0.1] * 64
    db.create_table("pkb_pages", data=[{
        "path": "notes/wrong.md",
        "name": "wrong.md",
        "vector": bad_vec,
        "mtime": 1000.0,
        "source_kind": "user",
    }], mode="overwrite")

    (isolated_pkb / "notes").mkdir(parents=True, exist_ok=True)
    (isolated_pkb / "notes" / "wrong.md").write_text("# wrong dim\n")

    rebuild_calls: list[bool] = []
    original_index_all = pkb_mod.index_all

    def patched_index_all(root=None):
        rebuild_calls.append(True)
        return original_index_all(root or isolated_pkb)

    monkeypatch.setattr(pkb_mod, "index_all", patched_index_all)

    pki.ensure_ready(isolated_pkb)

    assert not rebuild_calls, (
        "a vector-dimension mismatch must NEVER trigger an automatic "
        "drop-and-rebuild (C2/FM12)")

    # The table itself must be untouched — same row, same (wrong) vector.
    db2 = lancedb.connect(str(db_path))
    table = db2.open_table("pkb_pages")
    rows = table.to_pandas()
    assert len(rows) == 1
    assert rows.iloc[0]["path"] == "notes/wrong.md"

    ok, reason = pki.embedding_status()
    assert ok is False
    assert "pkb reembed" in reason


# ── Two LanceDB connections to same db (MVCC sanity) ─────────────────────

@pytest.mark.qa
def test_two_connections_to_same_db_can_both_query(isolated_pkb: Path):
    """LanceDB MVCC: a second process (modeled as a second connection)
    opening the same db simultaneously must see the writes the first
    process committed. This is the closest we can get to the "portal +
    benchmark CLI" multi-process scenario without forking."""
    import arail.pkb_index as pki
    from arail.vector_index import hash_embedding
    import lancedb  # type: ignore[import-not-found]

    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db1 = lancedb.connect(str(db_path))
    db1.create_table("pkb_pages", data=[{
        "path": "_seed.md",
        "name": "_seed.md",
        "vector": hash_embedding("seed", dim=768),
        "mtime": 0.0,
        "source_kind": "user",
    }], mode="overwrite")

    pki._pkb_root_cache = isolated_pkb
    pki._initialized = True

    # First "process" writes via the helper.
    target = isolated_pkb / "agents" / "research" / "mvcc_test.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# MVCC test\n\nMVCC_TOKEN_UNIQUE content.\n")

    with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "60"}):
        pki.schedule_upsert(target, pkb_root=isolated_pkb)
    pki._flush()

    # Second "process" opens a fresh connection to the same on-disk db
    # and must see the row.
    db2 = lancedb.connect(str(db_path))
    table2 = db2.open_table("pkb_pages")
    rows = table2.to_pandas()
    assert "agents/research/mvcc_test.md" in rows["path"].values, \
        "second connection must see the first connection's committed write"


# ── pkb_root path with spaces and unicode ────────────────────────────────

@pytest.mark.qa
def test_pkb_root_with_spaces_and_unicode_works(tmp_path: Path):
    """A pkb_root containing spaces and unicode in its absolute path must
    not break path-resolution or LanceDB connection."""
    import arail.pkb_index as pki
    from arail.vector_index import hash_embedding
    import lancedb  # type: ignore[import-not-found]

    weird_root = tmp_path / "lab dir with spaces" / "pkb_日本"
    weird_root.mkdir(parents=True, exist_ok=True)

    db_path = weird_root / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[{
        "path": "_seed.md",
        "name": "_seed.md",
        "vector": hash_embedding("seed", dim=768),
        "mtime": 0.0,
        "source_kind": "user",
    }], mode="overwrite")

    pki._pkb_root_cache = weird_root
    pki._initialized = True

    target = weird_root / "agents" / "research" / "weird_root.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Weird root\n\ncontent\n")

    with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "60"}):
        pki.schedule_upsert(target, pkb_root=weird_root)
    pki._flush()

    db2 = lancedb.connect(str(db_path))
    rows = db2.open_table("pkb_pages").to_pandas()
    assert "agents/research/weird_root.md" in rows["path"].values, \
        "pkb_root with spaces+unicode must not break upserts"


# ── Portal-startup hook isolation ────────────────────────────────────────

@pytest.mark.qa
def test_ensure_ready_failure_isolated_from_caller(isolated_pkb: Path, monkeypatch):
    """The portal's _startup() wraps ensure_ready in try/except. Verify
    that even if ensure_ready raises an unexpected exception type, callers
    that handle it can recover. (Defense-in-depth for the startup path.)"""
    import arail.pkb_index as pki

    # Force ensure_ready to raise mid-execution by breaking the available()
    # probe to throw instead of returning False.
    def explosive_available():
        raise OSError("simulated environment catastrophe")

    monkeypatch.setattr("arail.vector_index.available", explosive_available)

    # Without a try/except wrapper, ensure_ready WILL raise — that's
    # expected and is exactly what the portal's wrapping try/except is
    # there to catch. We assert the exception type is recognizable (so
    # the portal's logging would print something sensible).
    raised = False
    try:
        pki.ensure_ready(isolated_pkb)
    except OSError:
        raised = True
    except Exception as e:  # noqa: BLE001
        pytest.fail(f"ensure_ready raised unexpected type: {type(e).__name__}: {e}")

    # The point: ensure_ready DOES propagate (no internal swallow on
    # available()-itself-raising), so the portal MUST keep its wrapping
    # try/except. This is a contract test for the portal hook author.
    assert raised, (
        "ensure_ready propagated cleanly — the portal's startup hook "
        "must keep its try/except wrapper (verified at app.py:346-351)"
    )


# ── Secrets cannot leak into index even if dropped under pkb_root ────────

@pytest.mark.qa
def test_dotenv_under_pkb_root_not_indexed_by_iter(isolated_pkb: Path):
    """Defense: if a user accidentally drops a .env file under lab/pkb/,
    _iter_pkb_files (the source for index_all) must NOT pick it up
    because .env is not in _PKB_TEXT_SUFFIXES."""
    import arail.pkb as pkb

    # Drop a fake .env-like file under pkb_root.
    secret = isolated_pkb / "agents" / "research" / "secrets.env"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("OPENAI_API_KEY=sk-LEAKED_KEY_DO_NOT_INDEX")

    # Also drop a .key file (not in suffix allowlist).
    keyf = isolated_pkb / "agents" / "research" / "private.key"
    keyf.write_text("-----BEGIN PRIVATE KEY-----\nLEAKED_KEY\n-----END PRIVATE KEY-----")

    found_paths = [
        p.as_posix() for p, _ in pkb._iter_pkb_files(isolated_pkb)
    ]
    for p in found_paths:
        assert not p.endswith(".env"), \
            f".env file leaked into index iter: {p}"
        assert not p.endswith(".key"), \
            f".key file leaked into index iter: {p}"


@pytest.mark.qa
def test_cache_dir_contents_never_indexed_by_iter(isolated_pkb: Path):
    """.cache/ holds the vector index plus (C2) the reembed checkpoint and
    provenance sidecars, both .json — machine state, never PKB content.
    Without this exclusion, pkb_reembed's own checkpoint/provenance files
    would be picked up and re-embedded as if they were user content on the
    very next reembed run (a self-referential bug C2 surfaced)."""
    import arail.pkb as pkb

    cache_dir = isolated_pkb / ".cache"
    (cache_dir / "lancedb").mkdir(parents=True, exist_ok=True)
    (cache_dir / "lancedb" / "pkb_pages.provenance.json").write_text('{"schema": "x"}')
    (cache_dir / "reembed-state.json").write_text('{"schema": "x"}')

    found_paths = [
        p.as_posix() for p, _ in pkb._iter_pkb_files(isolated_pkb)
    ]
    for p in found_paths:
        assert ".cache/" not in p, f".cache/ content leaked into index iter: {p}"


# ── Failed flush: failed paths persist in _pending for retry ─────────────

@pytest.mark.qa
def test_failed_flush_keeps_paths_in_pending_for_retry(isolated_pkb: Path):
    """If merge_insert raises (e.g., disk full), the failed path must
    REMAIN in _pending so the next flush can retry. This is the
    architect's stated recovery semantics for the disk-full failure mode."""
    import arail.pkb_index as pki
    from arail.vector_index import hash_embedding
    import lancedb  # type: ignore[import-not-found]

    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[{
        "path": "_seed.md",
        "name": "_seed.md",
        "vector": hash_embedding("seed"),
        "mtime": 0.0,
        "source_kind": "user",
    }], mode="overwrite")

    pki._pkb_root_cache = isolated_pkb
    pki._initialized = True

    target = isolated_pkb / "agents" / "research" / "retry_me.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Retry\n\nmust persist in pending after failed flush\n")

    rel = "agents/research/retry_me.md"

    # Hostile open_table that returns a table whose merge_insert raises.
    real_open_table = pki._open_table

    class FakeMergeInsertBuilder:
        def when_matched_update_all(self):
            return self
        def when_not_matched_insert_all(self):
            return self
        def execute(self, _rows):
            raise OSError("simulated disk full")

    def hostile_merge(*_a, **_k):
        return FakeMergeInsertBuilder()

    def hostile_open_table(db_arg, name):
        t = real_open_table(db_arg, name)
        if t is not None:
            t.merge_insert = hostile_merge  # type: ignore[attr-defined]
        return t

    with patch.object(pki, "_open_table", side_effect=hostile_open_table):
        with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "60"}):
            pki.schedule_upsert(target, pkb_root=isolated_pkb)
            # Flush — should swallow the OSError.
            pki._flush()  # must not raise

            # Failed path must remain in _pending for retry.
            with pki._lock:
                pending = set(pki._pending)
            assert rel in pending, (
                f"failed paths must REMAIN in _pending for retry; "
                f"got pending={pending}"
            )


# ── Schedule_upsert from non-main thread: works end-to-end ───────────────

@pytest.mark.qa
def test_schedule_upsert_from_non_main_thread(isolated_pkb: Path):
    """Threading.Timer-based debounce must work even when schedule_upsert
    is called from a thread that is not main. (Researcher loop, daemon
    threads, etc.)"""
    import arail.pkb_index as pki
    from arail.vector_index import hash_embedding
    import lancedb  # type: ignore[import-not-found]

    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[{
        "path": "_seed.md",
        "name": "_seed.md",
        "vector": hash_embedding("seed", dim=768),
        "mtime": 0.0,
        "source_kind": "user",
    }], mode="overwrite")

    pki._pkb_root_cache = isolated_pkb
    pki._initialized = True

    target = isolated_pkb / "agents" / "research" / "from_thread.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# from thread\n")

    err_box: list[Exception] = []

    def worker():
        try:
            with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "0.2"}):
                pki.schedule_upsert(target, pkb_root=isolated_pkb)
        except Exception as e:  # noqa: BLE001
            err_box.append(e)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert not err_box, f"schedule_upsert from non-main thread raised: {err_box}"

    # Wait for flush.
    time.sleep(0.7)

    db2 = lancedb.connect(str(db_path))
    rows = db2.open_table("pkb_pages").to_pandas()
    assert "agents/research/from_thread.md" in rows["path"].values, \
        "non-main-thread upsert must produce a row"
