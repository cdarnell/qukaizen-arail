"""./arailctl pkb reembed (C2 in ARCHITECTURE.md, arail2-tier1-integration).

No Ollama required — tests/conftest.py's autouse _stub_embedding_provider
fixture stubs embed_documents/embed_query at the module boundary.
"""

from __future__ import annotations

import json
import os
import signal
from pathlib import Path

import pytest

import arail.pkb_reembed as reembed


def _make_pkb(tmp_path: Path, n: int = 6) -> Path:
    pkb_root = tmp_path / "pkb"
    notes = pkb_root / "notes"
    notes.mkdir(parents=True)
    for i in range(n):
        (notes / f"doc{i}.md").write_text(f"# doc {i}\ncontent {i}\n")
    return pkb_root


# --------------------------------------------------------------------------
# happy path
# --------------------------------------------------------------------------

def test_full_run_writes_provenance_and_swaps_table(tmp_path):
    pkb_root = _make_pkb(tmp_path)
    result = reembed.run(pkb_root, include_docs=False)

    assert result["interrupted"] is False
    assert result["completed"] == result["total"] == 6

    live_table = pkb_root / ".cache" / "lancedb" / "pkb_pages.lance"
    assert live_table.exists()

    from arail import pkb_provenance
    record = pkb_provenance.read(pkb_root / ".cache" / "lancedb")
    assert record is not None
    assert record["schema"] == pkb_provenance.SCHEMA
    assert record["rows"] == 6

    # Shadow build and checkpoint are cleaned up after a successful swap.
    assert not (pkb_root / ".cache" / "lancedb.next").exists()
    assert not reembed._checkpoint_path(pkb_root).exists()


def test_second_run_backs_up_previous_live_table(tmp_path):
    pkb_root = _make_pkb(tmp_path)
    reembed.run(pkb_root, include_docs=False)
    result2 = reembed.run(pkb_root, include_docs=False)

    assert result2["backup"] is not None
    assert Path(result2["backup"]).exists()
    assert Path(result2["backup"]).name.startswith("pkb_pages.lance.bak-")


def test_dry_run_writes_nothing(tmp_path):
    pkb_root = _make_pkb(tmp_path)
    result = reembed.run(pkb_root, dry_run=True, include_docs=False)

    assert result["dry_run"] is True
    assert result["total"] == 6
    assert not (pkb_root / ".cache").exists()


def test_empty_corpus_writes_zero_rows(tmp_path):
    pkb_root = tmp_path / "pkb"
    pkb_root.mkdir()
    result = reembed.run(pkb_root, include_docs=False)
    assert result["completed"] == 0
    assert result["total"] == 0


# --------------------------------------------------------------------------
# FM13 — SIGINT mid-run: live table unchanged, checkpoint written, resume
# --------------------------------------------------------------------------

def test_sigint_mid_run_leaves_live_table_untouched_and_writes_checkpoint(tmp_path, monkeypatch):
    pkb_root = _make_pkb(tmp_path, n=12)

    from arail.dbspec import embed as embed_mod
    from arail.vector_index import hash_embedding

    call_count = {"n": 0}

    def flaky_embed_documents(texts):
        call_count["n"] += 1
        if call_count["n"] == 2:
            os.kill(os.getpid(), signal.SIGINT)
        return [hash_embedding(t, dim=768) for t in texts]

    monkeypatch.setattr(embed_mod, "embed_documents", flaky_embed_documents)

    result = reembed.run(pkb_root, batch_size=3, include_docs=False)

    assert result["interrupted"] is True
    assert 0 < result["completed"] < result["total"] == 12

    live_table = pkb_root / ".cache" / "lancedb" / "pkb_pages.lance"
    assert not live_table.exists(), "an interrupted run must never touch the live table"

    checkpoint = reembed._load_checkpoint(pkb_root)
    assert checkpoint is not None
    assert checkpoint["schema"] == reembed.SCHEMA
    assert len(checkpoint["completed_paths"]) == result["completed"]


def test_resume_after_sigint_completes_to_full_row_count(tmp_path, monkeypatch):
    pkb_root = _make_pkb(tmp_path, n=12)

    from arail.dbspec import embed as embed_mod
    from arail.vector_index import hash_embedding

    call_count = {"n": 0}

    def flaky_embed_documents(texts):
        call_count["n"] += 1
        if call_count["n"] == 2:
            os.kill(os.getpid(), signal.SIGINT)
        return [hash_embedding(t, dim=768) for t in texts]

    monkeypatch.setattr(embed_mod, "embed_documents", flaky_embed_documents)
    interrupted_result = reembed.run(pkb_root, batch_size=3, include_docs=False)
    assert interrupted_result["interrupted"] is True

    # Un-flake the embedder for the resume.
    monkeypatch.setattr(
        embed_mod, "embed_documents",
        lambda texts: [hash_embedding(t, dim=768) for t in texts])

    result = reembed.run(pkb_root, resume=True, batch_size=3, include_docs=False)

    assert result["interrupted"] is False
    assert result["completed"] == result["total"] == 12

    live_table = pkb_root / ".cache" / "lancedb" / "pkb_pages.lance"
    assert live_table.exists()
    assert not reembed._checkpoint_path(pkb_root).exists()

    import lancedb  # type: ignore[import-not-found]
    db = lancedb.connect(str(pkb_root / ".cache" / "lancedb"))
    table = db.open_table("pkb_pages")
    assert table.count_rows() == 12


def test_resume_refuses_on_checkpoint_spec_mismatch(tmp_path):
    pkb_root = _make_pkb(tmp_path, n=3)
    reembed._write_checkpoint(pkb_root, {
        "schema": reembed.SCHEMA, "model": "some-other-model", "dim": 999,
        "spec_sha256": "not-the-current-spec", "started_at": "x",
        "total": 3, "completed_paths": [], "batch": 32,
    })

    with pytest.raises(reembed.CheckpointSpecMismatch):
        reembed.run(pkb_root, resume=True, include_docs=False)

    # Refusing to mix spaces must not touch the live table.
    assert not (pkb_root / ".cache" / "lancedb" / "pkb_pages.lance").exists()


def test_resume_with_no_checkpoint_starts_fresh(tmp_path):
    pkb_root = _make_pkb(tmp_path, n=4)
    result = reembed.run(pkb_root, resume=True, include_docs=False)
    assert result["interrupted"] is False
    assert result["completed"] == 4


# --------------------------------------------------------------------------
# EmbeddingError propagates, writes no live/partial table
# --------------------------------------------------------------------------

def test_embedding_error_propagates_and_writes_nothing_live(tmp_path, monkeypatch):
    pkb_root = _make_pkb(tmp_path, n=3)
    from arail.dbspec import embed as embed_mod
    from arail.dbspec.embed import EmbeddingError

    def raising_embed_documents(texts):
        raise EmbeddingError("simulated outage")

    monkeypatch.setattr(embed_mod, "embed_documents", raising_embed_documents)

    with pytest.raises(EmbeddingError):
        reembed.run(pkb_root, include_docs=False)

    assert not (pkb_root / ".cache" / "lancedb" / "pkb_pages.lance").exists()


# --------------------------------------------------------------------------
# CLI (main()) exit codes
# --------------------------------------------------------------------------

def test_main_missing_pkb_root_exits_2(tmp_path, capsys):
    rc = reembed.main(["--pkb-root", str(tmp_path / "nope"), "--world-label", "x"])
    assert rc == 2


def test_main_dry_run_exits_0_and_prints_eta(tmp_path, capsys):
    pkb_root = _make_pkb(tmp_path, n=2)
    rc = reembed.main(["--pkb-root", str(pkb_root), "--world-label", "x", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dry-run" in out


def test_main_happy_path_exits_0(tmp_path, capsys, monkeypatch):
    pkb_root = _make_pkb(tmp_path, n=2)
    # main() always calls run() with include_docs=True (default) via CLI;
    # keep this fast by stubbing docs_registry to empty.
    import arail.portal.docs_registry as reg
    monkeypatch.setattr(reg, "all_docs", lambda: ())
    rc = reembed.main(["--pkb-root", str(pkb_root), "--world-label", "x"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "done: 2/2" in out


# --------------------------------------------------------------------------
# REVIEW2.md BLOCK-2 required tests
# --------------------------------------------------------------------------

def test_resume_with_checkpoint_shadow_mismatch_discards_and_starts_over(tmp_path):
    """Required test #2: --resume with a checkpoint/shadow mismatch must
    never swap a truncated index in. Reproduces REVIEW2.md scenario 4:
    checkpoint claims N completed paths but the shadow build disagrees
    (here: shadow dir entirely absent, the most common real-world cause —
    a cleanup path removing the "obviously disposable" .next directory)."""
    pkb_root = _make_pkb(tmp_path, n=10)

    # Write a checkpoint claiming 6 of 10 rows done, but create NO shadow
    # build at all -- the exact REVIEW2.md scenario 4 shape.
    from arail.dbspec.generated.models_registry import EMBEDDING_DIM, embedding_model
    from arail.pkb import _current_spec_sha256
    fake_completed = [f"notes/doc{i}.md" for i in range(6)]
    reembed._write_checkpoint(pkb_root, {
        "schema": reembed.SCHEMA, "model": embedding_model().name,
        "dim": EMBEDDING_DIM, "spec_sha256": _current_spec_sha256(), "started_at": "x",
        "total": 10, "completed_paths": fake_completed, "batch": 32,
    })
    assert not reembed._shadow_dir(pkb_root).exists()

    result = reembed.run(pkb_root, resume=True, include_docs=False)

    assert result["interrupted"] is False
    assert result["resume_discarded_reason"] is not None
    assert "discarding" in result["resume_discarded_reason"]
    # Must have started over and embedded the FULL 10, not trusted the
    # checkpoint's stale claim of 6-already-done / 10 total.
    assert result["completed"] == result["total"] == 10

    import lancedb  # type: ignore[import-not-found]
    db = lancedb.connect(str(reembed._live_dir(pkb_root)))
    table = db.open_table("pkb_pages")
    assert table.count_rows() == 10


def test_resume_with_shadow_row_count_disagreement_discards_and_starts_over(tmp_path, monkeypatch):
    """Same failure class, different cause: the shadow dir exists but its
    row count disagrees with the checkpoint's completed_paths count (a
    partial/corrupted write)."""
    import lancedb  # type: ignore[import-not-found]
    from arail.vector_index import hash_embedding
    from arail.dbspec.generated.models_registry import EMBEDDING_DIM, embedding_model
    from arail.pkb import _current_spec_sha256

    pkb_root = _make_pkb(tmp_path, n=10)
    shadow_dir = reembed._shadow_dir(pkb_root)
    shadow_dir.mkdir(parents=True)
    db = lancedb.connect(str(shadow_dir))
    # Checkpoint will claim 6 completed, but the shadow table only has 2 rows.
    db.create_table("pkb_pages", data=[
        {"path": f"notes/doc{i}.md", "name": f"doc{i}.md",
         "vector": hash_embedding(f"content {i}", dim=768),
         "mtime": 0.0, "source_kind": "user"}
        for i in range(2)
    ], mode="overwrite")

    fake_completed = [f"notes/doc{i}.md" for i in range(6)]
    reembed._write_checkpoint(pkb_root, {
        "schema": reembed.SCHEMA, "model": embedding_model().name,
        "dim": EMBEDDING_DIM, "spec_sha256": _current_spec_sha256(), "started_at": "x",
        "total": 10, "completed_paths": fake_completed, "batch": 32,
    })

    result = reembed.run(pkb_root, resume=True, include_docs=False)

    assert result["resume_discarded_reason"] is not None
    assert result["completed"] == result["total"] == 10


def test_total_zero_with_existing_live_table_refuses_swap(tmp_path):
    """Required test #3: total == 0 against a live, populated table must
    never swap — an empty scan (transient or a not-yet-populated World)
    must not delete a healthy index."""
    import lancedb  # type: ignore[import-not-found]
    from arail.vector_index import hash_embedding

    pkb_root = tmp_path / "pkb"
    pkb_root.mkdir()
    live_dir = reembed._live_dir(pkb_root)
    live_dir.mkdir(parents=True)
    db = lancedb.connect(str(live_dir))
    db.create_table("pkb_pages", data=[
        {"path": "notes/existing.md", "name": "existing.md",
         "vector": hash_embedding("existing", dim=768),
         "mtime": 0.0, "source_kind": "user"}
    ], mode="overwrite")

    with pytest.raises(reembed.EmptyCorpusRefused):
        reembed.run(pkb_root, include_docs=False)

    # The live table must be completely untouched -- not renamed, not
    # replaced with an empty one.
    db2 = lancedb.connect(str(live_dir))
    table = db2.open_table("pkb_pages")
    assert table.count_rows() == 1
    assert not any(p.name.startswith("pkb_pages.lance.bak-") for p in live_dir.iterdir())


def test_total_zero_with_no_existing_table_succeeds_as_noop(tmp_path):
    """An empty corpus with NO existing live table is not a BLOCK-2 concern
    -- there's nothing to protect, and the existing empty-corpus test
    (test_empty_corpus_writes_zero_rows) already covers this path
    completing normally."""
    pkb_root = tmp_path / "pkb"
    pkb_root.mkdir()
    result = reembed.run(pkb_root, include_docs=False)
    assert result["completed"] == result["total"] == 0


def test_shadow_verification_is_cardinality_only_documented_limitation(tmp_path):
    """REVIEW3.md 'also fix' #3 (coverage half): the resume-time shadow
    check is a row COUNT comparison (shadow row count == len(completed_
    paths)), not a content/freshness check on the rows it trusts as
    already-done. Demonstrates the concrete blind spot: a checkpoint
    correctly names 2 of 3 real paths as completed, backed by a shadow
    table with exactly those 2 real paths present -- but carrying a STALE
    vector (as if embedded from old file content, never re-verified). The
    count math is satisfied (2 shadow rows == 2 completed_paths; +1
    freshly embedded remaining row == 3 == total), so resume trusts the
    2 stale rows verbatim into the swapped-in live table rather than
    re-checking their freshness. This is a known, filed limitation
    (sprints/BACKLOG.md) -- not fixed this sprint per explicit
    instruction; note it requires the completed paths to genuinely match
    real corpus paths (an all-wrong-paths checkpoint, tried first while
    writing this test, is actually caught: `remaining` is computed by
    path-set membership, not by count, so wrong paths cause every real
    row to be re-embedded and the final total-row check then correctly
    trips ShadowBuildIncomplete)."""
    import lancedb  # type: ignore[import-not-found]
    from arail.vector_index import hash_embedding
    from arail.dbspec.generated.models_registry import EMBEDDING_DIM, embedding_model
    from arail.pkb import _current_spec_sha256

    pkb_root = _make_pkb(tmp_path, n=3)  # real paths: notes/doc0.md, doc1.md, doc2.md

    shadow_dir = reembed._shadow_dir(pkb_root)
    shadow_dir.mkdir(parents=True)
    db = lancedb.connect(str(shadow_dir))
    stale_vector = hash_embedding("this is deliberately STALE content", dim=768)
    db.create_table("pkb_pages", data=[
        {"path": "notes/doc0.md", "name": "doc0.md", "vector": stale_vector,
         "mtime": 0.0, "source_kind": "user"},
        {"path": "notes/doc1.md", "name": "doc1.md", "vector": stale_vector,
         "mtime": 0.0, "source_kind": "user"},
    ], mode="overwrite")

    reembed._write_checkpoint(pkb_root, {
        "schema": reembed.SCHEMA, "model": embedding_model().name,
        "dim": EMBEDDING_DIM, "spec_sha256": _current_spec_sha256(),
        "started_at": "x", "total": 3,
        "completed_paths": ["notes/doc0.md", "notes/doc1.md"], "batch": 32,
    })

    result = reembed.run(pkb_root, resume=True, include_docs=False)

    # The checkpoint is trusted -- no discard -- because the counts line
    # up (2 shadow rows == 2 completed_paths at resume time; 3 total rows
    # after embedding the 1 remaining real row == total).
    assert result["resume_discarded_reason"] is None, (
        "documents the known gap: matching counts trust the 2 pre-existing "
        "(here: deliberately stale) shadow rows without re-verifying them")
    assert result["completed"] == result["total"] == 3

    db2 = lancedb.connect(str(reembed._live_dir(pkb_root)))
    table2 = db2.open_table("pkb_pages")
    rows_by_path = {r["path"]: r for r in table2.to_pandas().to_dict("records")}
    assert list(rows_by_path["notes/doc0.md"]["vector"]) == stale_vector, (
        "the stale vector was carried through unverified into the swapped-in "
        "live table -- the cardinality-only blind spot, filed in "
        "sprints/BACKLOG.md, not fixed this sprint")


# --------------------------------------------------------------------------
# REVIEW2.md BLOCK-2 required test #4 — concurrent runs
# --------------------------------------------------------------------------

def test_second_concurrent_run_is_refused_by_lock(tmp_path):
    pkb_root = _make_pkb(tmp_path, n=3)
    lock = reembed._ReembedLock(pkb_root)
    lock.acquire()
    try:
        with pytest.raises(reembed.ReembedLocked):
            reembed.run(pkb_root, include_docs=False)
    finally:
        lock.release()


# --------------------------------------------------------------------------
# REVIEW4.md ASK-2 — flock-based mutual exclusion, no stale-lock heuristic
# --------------------------------------------------------------------------

def test_lock_file_with_garbage_content_is_irrelevant_to_flock(tmp_path):
    """The lock file's *content* (a PID, garbage, or nothing) is purely
    informational under flock -- only the kernel's advisory-lock state on
    the fd matters. A lock file that's empty or unparseable must not be
    treated as special in any way: a fresh acquire against it must succeed
    immediately, exactly as if the file didn't exist, because nothing is
    holding the flock on it."""
    pkb_root = _make_pkb(tmp_path, n=4)
    lock_path = reembed._lock_path(pkb_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("")  # empty / unparseable content, nobody holds it

    result = reembed.run(pkb_root, include_docs=False)

    assert result["completed"] == result["total"] == 4


def test_lock_file_naming_a_pid_that_still_exists_is_not_special(tmp_path):
    """A lock file whose written content happens to name a live PID (e.g.
    this test process) must NOT be refused as if that content meant
    anything -- under flock, only actually holding the advisory lock
    matters. Since nothing has flock'd this file, a run must proceed
    normally despite the file naming a very-much-alive PID."""
    pkb_root = _make_pkb(tmp_path, n=3)
    lock_path = reembed._lock_path(pkb_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(str(os.getpid()))  # content is decorative only

    result = reembed.run(pkb_root, include_docs=False)

    assert result["interrupted"] is False
    assert result["completed"] == result["total"] == 3


def test_holding_flock_directly_refuses_a_concurrent_run(tmp_path):
    """The actual mutual-exclusion guarantee: while a second file
    descriptor genuinely holds the flock (bypassing _ReembedLock entirely,
    to simulate "some other process has it"), `run()` must be refused with
    ReembedLocked, never proceed."""
    import fcntl

    pkb_root = _make_pkb(tmp_path, n=3)
    lock_path = reembed._lock_path(pkb_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(reembed.ReembedLocked):
            reembed.run(pkb_root, include_docs=False)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    # Once the external holder releases, a normal run proceeds.
    result = reembed.run(pkb_root, include_docs=False)
    assert result["interrupted"] is False


def test_sigkilled_holder_releases_lock_automatically(tmp_path):
    """The core promise of flock over the old PID heuristic: a holder that
    is SIGKILLed (no chance to run any cleanup code, unlike a normal
    exception path) must have its lock released by the kernel, with zero
    staleness logic on our side. A subprocess acquires the lock and blocks
    forever; we SIGKILL it; a fresh acquire in this process must succeed
    right away."""
    import fcntl
    import signal
    import subprocess
    import sys as _sys
    import time as _time

    pkb_root = _make_pkb(tmp_path, n=1)
    lock_path = reembed._lock_path(pkb_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    holder_script = (
        "import fcntl, os, sys, time\n"
        f"p = {str(lock_path)!r}\n"
        "os.makedirs(os.path.dirname(p), exist_ok=True)\n"
        "fd = os.open(p, os.O_CREAT | os.O_RDWR)\n"
        "fcntl.flock(fd, fcntl.LOCK_EX)\n"
        "sys.stdout.write('locked\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(60)\n"
    )
    proc = subprocess.Popen(
        [_sys.executable, "-c", holder_script],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        line = proc.stdout.readline()
        assert line.strip() == "locked", f"holder didn't confirm lock: {line!r}"

        # Confirm the lock is genuinely held right now.
        with pytest.raises(reembed.ReembedLocked):
            reembed.run(pkb_root, include_docs=False)

        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)

        deadline = _time.monotonic() + 5
        last_err = None
        while _time.monotonic() < deadline:
            try:
                result = reembed.run(pkb_root, include_docs=False)
                assert result["interrupted"] is False
                return
            except reembed.ReembedLocked as e:  # pragma: no cover - flaky retry
                last_err = e
                _time.sleep(0.05)
        raise AssertionError(
            f"lock was not released after holder was SIGKILLed: {last_err}")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def test_lock_released_after_run_completes(tmp_path):
    pkb_root = _make_pkb(tmp_path, n=2)
    reembed.run(pkb_root, include_docs=False)
    # REVIEW4.md ASK-2: the lock file is deliberately never unlinked (see
    # _ReembedLock's docstring) -- it persists harmlessly under .cache/.
    # What must be true is that it's no longer *held*: a fresh acquire on
    # the same path must succeed immediately.
    assert reembed._lock_path(pkb_root).exists()
    lock = reembed._ReembedLock(pkb_root)
    lock.acquire()
    lock.release()
    # A second run afterward must succeed normally (lock isn't stuck held).
    result = reembed.run(pkb_root, include_docs=False)
    assert result["interrupted"] is False


def test_lock_released_after_run_raises(tmp_path):
    """The lock must be released even when the write phase raises (e.g.
    EmptyCorpusRefused) -- otherwise one failed run wedges every future one."""
    import lancedb  # type: ignore[import-not-found]
    from arail.vector_index import hash_embedding

    pkb_root = tmp_path / "pkb"
    pkb_root.mkdir()
    live_dir = reembed._live_dir(pkb_root)
    live_dir.mkdir(parents=True)
    db = lancedb.connect(str(live_dir))
    db.create_table("pkb_pages", data=[
        {"path": "notes/existing.md", "name": "existing.md",
         "vector": hash_embedding("existing", dim=768),
         "mtime": 0.0, "source_kind": "user"}
    ], mode="overwrite")

    with pytest.raises(reembed.EmptyCorpusRefused):
        reembed.run(pkb_root, include_docs=False)

    # Not unlinked (see _ReembedLock docstring) -- but must no longer be held.
    lock = reembed._ReembedLock(pkb_root)
    lock.acquire()
    lock.release()


def test_two_concurrent_runs_in_process_second_is_locked_out(tmp_path):
    """In-process approximation of REVIEW2.md scenario 6: while the lock is
    held (simulating a first run's write phase in progress), a second
    run() call must fail with ReembedLocked -- a sentence, never a raw
    LanceDB transaction-conflict traceback. (A true multi-thread race is
    exercised by test_two_concurrent_reembed_processes_one_loses_cleanly
    below, which uses real subprocesses since reembed.run() installs a
    SIGINT handler that only works on the main thread.)"""
    pkb_root = _make_pkb(tmp_path, n=20)
    lock = reembed._ReembedLock(pkb_root)
    lock.acquire()
    try:
        with pytest.raises(reembed.ReembedLocked):
            reembed.run(pkb_root, include_docs=False)
    finally:
        lock.release()

    # Once released, a normal run succeeds -- the lock isn't permanently
    # wedged by the refused attempt.
    result = reembed.run(pkb_root, include_docs=False)
    assert result["interrupted"] is False
    assert result["completed"] == result["total"] == 20


@pytest.mark.requires_ollama
def test_two_concurrent_reembed_processes_one_loses_cleanly(tmp_path):
    """Required test #4, the real shape: two actual `python -m
    arail.pkb_reembed` subprocesses racing against the same root
    (REVIEW2.md scenario 6 reproduced two raw processes, not threads --
    reembed.run() installs a SIGINT handler that only works on a process's
    main thread, so an in-process thread-based race isn't representative).
    Exactly one process must succeed (exit 0); the other must fail cleanly
    with exit 1 and a sentence on stderr, never a raw Lance/Rust
    traceback."""
    import os
    import subprocess
    import sys as _sys

    pkb_root = _make_pkb(tmp_path, n=40)
    cmd = [
        _sys.executable, "-m", "arail.pkb_reembed",
        "--pkb-root", str(pkb_root), "--world-label", "concurrency-test",
    ]
    env = dict(os.environ)
    src_path = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = src_path + (
        (":" + env["PYTHONPATH"]) if env.get("PYTHONPATH") else "")
    repo_root = str(Path(__file__).resolve().parents[1])

    # Launch both back-to-back (Popen returns immediately, no need to wait
    # for either) so they race for the same lock file.
    procs = [
        subprocess.Popen(cmd, cwd=repo_root, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(2)
    ]
    outs = [p.communicate(timeout=60) for p in procs]
    codes = [p.returncode for p in procs]

    assert sorted(codes) == [0, 1], f"expected one success one lock-refusal, got {codes}: {outs}"
    loser_stderr = next(o[1] for o, c in zip(outs, codes) if c == 1)
    assert "lock" in loser_stderr.lower() or "already" in loser_stderr.lower()
    assert "lance error" not in loser_stderr.lower()
    assert "traceback" not in loser_stderr.lower()
