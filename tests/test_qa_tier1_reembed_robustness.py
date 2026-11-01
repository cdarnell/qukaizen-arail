"""QA (2026-08-08-arail2-tier1-integration): ``pkb reembed`` under stress,
and the ``ensure_ready`` ordering that ``doctor``'s exit-3 depends on.

REVIEW4.md's carried QA list, items 3/4/6/7. The bar for every test here
is the one the sprint set for itself: an interrupted, contended, or
denied re-embed must never leave a half-swapped, truncated or empty index,
and must never wedge the recovery verb.

QA-1/QA-2/QA-3 (TEST_REPORT.md) were originally filed as ``xfail(strict=
True)`` defect reproducers -- they asserted the behaviour wanted, failed
on first write, and were designed to XPASS-fail the moment they were
fixed without the marker being removed. All three are now fixed (the
final build pass) and the markers dropped; they run as ordinary
regression tests below.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from arail import pkb_reembed as R

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _reset():
    import arail.pkb_index as pki
    pki._reset_for_tests()
    yield
    pki._reset_for_tests()


def _corpus(tmp_path, n=6, name="pkb"):
    root = tmp_path / name
    (root / "notes").mkdir(parents=True)
    for i in range(n):
        (root / "notes" / f"n{i}.md").write_text(f"# note {i}\nbody {i}\n")
    return root


def _live_fingerprint(root: Path) -> str:
    import hashlib
    table = root / ".cache" / "lancedb" / "pkb_pages.lance"
    h = hashlib.sha256()
    for p in sorted(table.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(table).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def _rows(root: Path) -> int:
    import lancedb  # type: ignore[import-not-found]
    db = lancedb.connect(str(root / ".cache" / "lancedb"))
    return int(db.open_table("pkb_pages").count_rows())


# ---------------------------------------------------------------------------
# happy path / idempotence
# ---------------------------------------------------------------------------

def test_reembed_is_idempotent_and_keeps_a_restorable_backup(tmp_path):
    root = _corpus(tmp_path)
    first = R.run(root, include_docs=False)
    assert first["completed"] == first["total"] == 6
    assert first["backup"] is None, "nothing to back up on a first build"

    time.sleep(1.1)  # distinct backup timestamp — see QA-1
    second = R.run(root, include_docs=False)
    assert second["completed"] == 6
    assert second["backup"] and Path(second["backup"]).exists(), \
        "the pre-reembed table must remain restorable"
    assert _rows(root) == 6


def test_reembed_defragments_the_index(tmp_path):
    """Not a stated goal, but a real operator benefit worth pinning: the
    shadow build produces one fresh table, so the incremental-upsert
    fragment sprawl (the operator's `ai` World carries 2421 fragments)
    collapses. If a future change makes reembed additive instead, this
    fails."""
    root = _corpus(tmp_path)
    R.run(root, include_docs=False)
    data_dir = root / ".cache" / "lancedb" / "pkb_pages.lance" / "data"
    assert len(list(data_dir.glob("*"))) <= 2


# ---------------------------------------------------------------------------
# interruption / crash
# ---------------------------------------------------------------------------

def test_sigkill_midrun_leaves_the_live_table_byte_identical_and_resume_completes(tmp_path):
    """FM13 under SIGKILL (not SIGINT — no handler runs, no cleanup).

    Reproduced against the operator-scale case as well: 40 rows, killed at
    24/40, live table unchanged, ``--resume`` finished to 40/40.
    """
    root = _corpus(tmp_path, n=24)
    R.run(root, include_docs=False)
    before = _live_fingerprint(root)

    runner = tmp_path / "slow_runner.py"
    runner.write_text(
        "import sys, time\n"
        "from pathlib import Path\n"
        "import arail.dbspec.embed as E\n"
        "from arail.dbspec.generated.models_registry import EMBEDDING_DIM\n"
        "def slow(texts):\n"
        "    time.sleep(1.0)\n"
        "    return [[0.25] * EMBEDDING_DIM for _ in texts]\n"
        "E.embed_documents = slow\n"
        "from arail import pkb_reembed as R\n"
        "print('GO', flush=True)\n"
        "R.run(Path(sys.argv[1]), include_docs=False, batch_size=4,\n"
        "      progress=lambda **kw: print('P', kw['done'], flush=True))\n"
    )
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT / "src"))
    proc = subprocess.Popen([sys.executable, str(runner), str(root)],
                            env=env, stdout=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 30
        batches = 0
        while time.monotonic() < deadline and batches < 2:
            line = proc.stdout.readline()
            if not line:
                break
            if line.startswith("P "):
                batches += 1
        assert batches >= 2, "the child never got past two batches"
        proc.send_signal(signal.SIGKILL)
    finally:
        proc.wait(timeout=30)

    assert _live_fingerprint(root) == before, \
        "SIGKILL mid-shadow-build must not touch the live table"
    checkpoint = json.loads((root / ".cache" / "reembed-state.json").read_text())
    assert 0 < len(checkpoint["completed_paths"]) < 24

    result = R.run(root, resume=True, include_docs=False, batch_size=4)
    assert result["completed"] == result["total"] == 24
    assert _rows(root) == 24
    assert not (root / ".cache" / "reembed-state.json").exists()
    assert not (root / ".cache" / "lancedb.next").exists()


def test_a_dead_holders_lock_file_never_blocks_the_next_run(tmp_path):
    """A SIGKILLed run leaves ``reembed.lock`` on disk. flock is released
    by the kernel, so the recovery verb the degraded message tells users to
    run must not be wedged by it. (The pre-flock PID heuristic — REVIEW4
    ASK-2 — is what made this worth pinning separately from the lock's
    exclusion behaviour.)"""
    root = _corpus(tmp_path)
    lock = root / ".cache" / "reembed.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("999999")  # a PID that is not alive
    result = R.run(root, include_docs=False)
    assert result["completed"] == 6


def test_corrupt_checkpoint_is_discarded_rather_than_wedging_resume(tmp_path):
    root = _corpus(tmp_path)
    (root / ".cache").mkdir(parents=True, exist_ok=True)
    (root / ".cache" / "reembed-state.json").write_text("}{ truncated json")
    result = R.run(root, resume=True, include_docs=False)
    assert result["completed"] == result["total"] == 6


def test_checkpoint_claiming_more_rows_than_the_shadow_has_is_discarded(tmp_path):
    """BLOCK-2 scenario 4 — verified end to end rather than by unit stub."""
    from arail import pkb as pkb_mod
    from arail.dbspec.generated.models_registry import EMBEDDING_DIM, embedding_model

    root = _corpus(tmp_path)
    (root / ".cache").mkdir(parents=True, exist_ok=True)
    (root / ".cache" / "reembed-state.json").write_text(json.dumps({
        "schema": R.SCHEMA, "model": embedding_model().name,
        "dim": EMBEDDING_DIM, "spec_sha256": pkb_mod._current_spec_sha256(),
        "started_at": "x", "total": 6,
        "completed_paths": ["notes/n0.md", "notes/n1.md"], "batch": 32,
    }))
    result = R.run(root, resume=True, include_docs=False)
    assert "discarding the checkpoint" in (result["resume_discarded_reason"] or "")
    assert result["completed"] == 6
    assert _rows(root) == 6


def test_corrupt_provenance_sidecar_degrades_instead_of_serving(tmp_path):
    """A truncated sidecar (interrupted backup restore, partial sync) reads
    as absent. The index must not be served as semantic."""
    import arail.pkb as pkb
    import arail.pkb_index as pki

    root = _corpus(tmp_path)
    R.run(root, include_docs=False)
    sidecar = root / ".cache" / "lancedb" / "pkb_pages.provenance.json"
    sidecar.write_text("{not json")

    pki._reset_for_tests()
    hits = pkb.search("note", root)
    assert "provenance" in pki.degraded_codes()
    assert all(h["source"] == "keyword" for h in hits)


# ---------------------------------------------------------------------------
# concurrency
# ---------------------------------------------------------------------------

def test_two_processes_racing_the_same_root_produce_exactly_one_winner(tmp_path):
    """REVIEW4 QA item 4. Five in-process pairs would not exercise the
    kernel lock across address spaces, so this spawns real processes and
    asserts the *live index* is intact regardless of who wins."""
    root = _corpus(tmp_path, n=16)
    runner = tmp_path / "racer.py"
    runner.write_text(
        "import sys, time\n"
        "from pathlib import Path\n"
        "import arail.dbspec.embed as E\n"
        "from arail.dbspec.generated.models_registry import EMBEDDING_DIM\n"
        "def slow(texts):\n"
        "    time.sleep(0.15)\n"
        "    return [[0.25] * EMBEDDING_DIM for _ in texts]\n"
        "E.embed_documents = slow\n"
        "from arail import pkb_reembed as R\n"
        "try:\n"
        "    R.run(Path(sys.argv[1]), include_docs=False, batch_size=4)\n"
        "    print('WON')\n"
        "except R.ReembedLocked as e:\n"
        "    print('LOCKED')\n"
    )
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT / "src"))
    procs = [subprocess.Popen([sys.executable, str(runner), str(root)],
                              env=env, stdout=subprocess.PIPE, text=True)
             for _ in range(2)]
    outs = [p.communicate(timeout=180)[0] for p in procs]

    assert sum("WON" in o for o in outs) == 1, f"exactly one winner: {outs}"
    assert sum("LOCKED" in o for o in outs) == 1, f"one clean lock-out: {outs}"
    assert _rows(root) == 16


# ---------------------------------------------------------------------------
# ensure_ready ordering — what doctor's exit 3 rests on (REVIEW4 QA item 6)
# ---------------------------------------------------------------------------

def test_readonly_ensure_ready_still_sees_a_missing_provenance_sidecar(tmp_path):
    """The provenance check MUST stay above ``if not build: return``.

    A 768-dim table whose sidecar has been deleted (a partial restore, a
    hand-cleaned ``.cache``) is dimension-correct, so only the provenance
    check can catch it. Move the early return up and this is the test that
    fails; ``doctor`` would otherwise report OK on an index
    ``_semantic_search`` refuses to serve.
    """
    import arail.pkb as pkb
    import arail.pkb_index as pki

    root = _corpus(tmp_path)
    pkb.index_all(pkb_root=root, include_docs=False)
    (root / ".cache" / "lancedb" / "pkb_pages.provenance.json").unlink()

    pki._reset_for_tests()
    pki.ensure_ready(root, build=False)
    assert "provenance" in pki.degraded_codes(), (
        "read-only ensure_ready must run the provenance check before it "
        "returns — doctor's exit 3 depends on it")


def test_readonly_ensure_ready_writes_nothing_at_all(tmp_path):
    """BLOCK-3's guarantee, asserted as a filesystem inventory rather than
    as the absence of one directory."""
    import arail.pkb as pkb
    import arail.pkb_index as pki

    root = _corpus(tmp_path)
    pkb.index_all(pkb_root=root, include_docs=False)

    def inventory():
        return {str(p.relative_to(root)): (p.stat().st_mtime_ns, p.stat().st_size)
                for p in sorted(root.rglob("*")) if p.is_file()}

    before = inventory()
    pki._reset_for_tests()
    pki.ensure_ready(root, build=False)
    assert inventory() == before


def test_readonly_ensure_ready_on_a_world_with_no_index_creates_no_directory(tmp_path):
    import arail.pkb_index as pki

    root = _corpus(tmp_path)
    pki.ensure_ready(root, build=False)
    assert not (root / ".cache").exists(), \
        "a diagnostic must not create the cache dir it is diagnosing"
    assert "empty" in pki.degraded_codes()


# ---------------------------------------------------------------------------
# Defect reproducers — see TEST_REPORT.md QA-1..QA-3
# ---------------------------------------------------------------------------

def test_two_reembeds_completing_in_the_same_second_do_not_crash(tmp_path):
    """QA-1, fixed: the backup name used to be second-resolution only, so
    two reembeds completing in the same wall-clock second collided and the
    second's bare OSError(ENOTEMPTY) escaped every handler in main(). The
    backup-naming loop now picks a name nothing is using."""
    root = _corpus(tmp_path, n=2)
    R.run(root, include_docs=False)
    # Occupy the backup name this second's run would otherwise choose.
    bak = (root / ".cache" / "lancedb"
           / f"pkb_pages.lance.bak-{int(time.time())}")
    bak.mkdir(parents=True, exist_ok=True)
    (bak / "occupied").write_text("x")
    R.run(root, include_docs=False)   # must not raise


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores mode bits")
def test_reembed_on_an_unwritable_cache_reports_actionably(tmp_path):
    """QA-2, fixed: a read-only/full .cache used to raise a bare
    PermissionError past every RuntimeError handler in main(). run() now
    wraps any OSError from the write phase in ReembedIOError (a
    RuntimeError subclass), so main() reports it the same actionable way
    (English message, exit 1) as every other failure mode here."""
    root = _corpus(tmp_path)
    R.run(root, include_docs=False)
    cache = root / ".cache"
    os.chmod(cache, 0o500)
    try:
        with pytest.raises(RuntimeError):  # the class main() knows how to report
            R.run(root, include_docs=False)
    finally:
        os.chmod(cache, 0o700)


def test_empty_corpus_does_not_leave_a_sidecar_without_a_table(tmp_path):
    """QA-3, fixed: an empty corpus with no pre-existing live table never
    creates a table to swap in, but the provenance sidecar used to be
    written unconditionally anyway. Now gated on a live table actually
    existing after the swap attempt."""
    root = tmp_path / "empty"
    root.mkdir()
    R.run(root, include_docs=False)
    sidecar = root / ".cache" / "lancedb" / "pkb_pages.provenance.json"
    table = root / ".cache" / "lancedb" / "pkb_pages.lance"
    assert sidecar.exists() <= table.exists(), \
        "no provenance record for a table that was never written"
