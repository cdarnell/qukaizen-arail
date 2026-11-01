"""``./arailctl pkb reembed`` — explicit, resumable re-embed of one PKB
root's vector index (C2 in ARCHITECTURE.md, arail2-tier1-integration).

Never triggered implicitly by a *query*: the lazy ``index_all()`` call
inside ``pkb._semantic_search`` is removed on this integration (see
FM11) — an empty or stale index degrades honestly instead of firing
hundreds of synchronous embed calls from inside a search request.

This is **not** the only path that (re)writes ``pkb_pages`` — an earlier
draft of this docstring claimed that, and REVIEW3.md's BLOCK-3 caught the
gap it was hiding: ``pkb_index.ensure_ready(build=True)`` (the default)
also calls ``index_all()`` when a World's table doesn't exist yet or its
schema needs a columns-only upgrade, and three production call sites use
that default deliberately, because they are genuine content-write paths,
not diagnostics: the portal's own startup readiness check
(``app.py``'s ``_kb_index_ready``), a captured voice/OCR note being
indexed right after it's written (``app.py``), and a World mount staging
its term pages for indexing (``world_mount.py``). What THIS command
uniquely provides, that none of those three do, is the *shadow-build +
verified swap* — an atomic-feeling replace of an EXISTING table's
vectors, safe to run against a populated index without a lazy
drop-and-rebuild. The one hard guarantee every caller of
``ensure_ready``/``index_all`` must honour, this command included, is
C2/FM12: a vector-*dimension* mismatch is NEVER silently dropped and
rebuilt — only this explicit verb, or a fresh from-empty build, may
rewrite those rows. Diagnostic callers (``./arailctl doctor``) must pass
``ensure_ready(build=False)``, which performs zero embeds and creates no
index at all — see ``doctor.check_knowledge_base`` and REVIEW3.md.

Shape, per C2:
  * **Explicit.** Only this command re-embeds. Nothing else calls it.
  * **Shadow build + swap.** Vectors are written into
    ``<pkb_root>/.cache/lancedb.next/`` batch by batch. Only after every
    row succeeds does the live table get replaced — ``pkb_pages.lance`` is
    moved to ``pkb_pages.lance.bak-<ts>`` first, so a crash between steps
    leaves either the old table or the old table plus a ``.next`` dir,
    never a half-embedded live table (FM13).
  * **Resumable + interruptible.** A checkpoint at
    ``<pkb_root>/.cache/reembed-state.json`` is written after every batch.
    SIGINT stops queuing new batches (the in-flight batch finishes and its
    checkpoint is written, so nothing is lost) and the process exits 130.
    ``--resume`` refuses to continue if the checkpoint's
    model/dim/spec_sha256 disagree with the current spec.
  * **Provenance written last**, via ``pkb_provenance`` (C4), after the
    swap — never before.

Usage (invoked by ``arailctl``'s bash dispatcher, one call per resolved
pkb root):
    python -m arail.pkb_reembed --pkb-root PATH [--world-label LABEL]
        [--resume] [--dry-run] [--yes]

Exit codes: 0 ok · 1 error (e.g. LanceDB unavailable, checkpoint spec
mismatch) · 2 bad input (pkb root missing) · 4 EmbeddingError (provider
unavailable) · 130 interrupted (SIGINT; resume with --resume).
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable

SCHEMA = "arail.reembed_checkpoint/v1"


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------

def _checkpoint_path(pkb_root: Path) -> Path:
    return pkb_root / ".cache" / "reembed-state.json"


def _shadow_dir(pkb_root: Path) -> Path:
    return pkb_root / ".cache" / "lancedb.next"


def _live_dir(pkb_root: Path) -> Path:
    return pkb_root / ".cache" / "lancedb"


# --------------------------------------------------------------------------
# checkpoint
# --------------------------------------------------------------------------

def _load_checkpoint(pkb_root: Path) -> dict[str, Any] | None:
    path = _checkpoint_path(pkb_root)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _write_checkpoint(pkb_root: Path, state: dict[str, Any]) -> None:
    path = _checkpoint_path(pkb_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(path)


def _clear_checkpoint(pkb_root: Path) -> None:
    try:
        _checkpoint_path(pkb_root).unlink()
    except FileNotFoundError:
        pass


class CheckpointSpecMismatch(RuntimeError):
    """--resume was passed but the checkpoint was started with a different
    embedding spec (model/dim/spec_sha256)."""


class ShadowBuildIncomplete(RuntimeError):
    """The shadow build's row count disagrees with what the checkpoint (or
    the corpus scan) expected — REVIEW2.md BLOCK-2. Raised instead of
    swapping a truncated index into place. The shadow build and checkpoint
    are discarded before this is raised, so a plain re-run (without
    --resume) starts clean."""


class EmptyCorpusRefused(RuntimeError):
    """total == 0 but a live table already exists — REVIEW2.md BLOCK-2.
    Refusing to swap an empty result over a populated index; an empty
    corpus is far more likely to mean "PKB dir not populated yet" or a
    transient scan failure than "the operator wants to wipe their index.\""""


class ReembedLocked(RuntimeError):
    """Another ``pkb reembed`` is already running against this root
    (REVIEW2.md BLOCK-2, scenario 6: two concurrent runs raced LanceDB's
    own transaction conflict resolver and surfaced a raw Rust error)."""


class ReembedIOError(RuntimeError):
    """A filesystem operation in the write phase failed for a mundane
    reason -- permissions, a full disk, a read-only mount -- rather than a
    data-integrity concern (QA-2, TEST_REPORT.md round 1). Wrapped so
    ``main()`` reports it the same actionable way as every other
    ``RuntimeError`` here (an English message and a non-zero exit code),
    instead of a raw ``PermissionError``/``OSError`` traceback."""


# --------------------------------------------------------------------------
# lock — one concurrent `pkb reembed` per root
# --------------------------------------------------------------------------

def _lock_path(pkb_root: Path) -> Path:
    return pkb_root / ".cache" / "reembed.lock"


class _ReembedLock:
    """``fcntl.flock`` lock file. Held for the duration of the write phase
    (not --dry-run, which touches no shared state).

    REVIEW4.md ASK-2: an earlier PID-heuristic version of this lock (check
    whether the PID named in the lock file is alive, unlink-and-retry if
    not) reintroduced REVIEW2.md BLOCK-2 (broken mutual exclusion) via a
    TOCTOU race between the staleness check and the unlink -- measured at
    1.3us median / 6.5us max over 200 samples, both processes holding the
    lock simultaneously. ``flock`` deletes the check-then-act heuristic
    entirely: the kernel is the sole arbiter of who holds the lock, and it
    releases the lock automatically when the holding process dies for any
    reason (normal exit, SIGKILL, OOM-kill), so stale-lock recovery stops
    being something we implement. PID reuse is not a hazard here either --
    we never make a decision based on what PID is/was recorded.

    The lock file itself is created once (``O_CREAT``) and never unlinked
    by ``release()``: unlinking a file that ``flock`` is still the sole
    exclusion mechanism for is its own TOCTOU hazard (a third process could
    ``open(O_CREAT)`` a *fresh* inode after the unlink and acquire a lock on
    it immediately, without ever contending with a process still holding
    the flock on the old, now-orphaned inode). The lock file persists
    harmlessly under ``.cache/`` (already git-ignored, already excluded
    from PKB indexing) for the life of the PKB root.
    """

    def __init__(self, pkb_root: Path):
        self.path = _lock_path(pkb_root)
        self._fd: int | None = None

    def acquire(self) -> None:
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise ReembedLocked(
                f"another `pkb reembed` appears to already be running "
                f"against this root (lock held: {self.path}). Wait for it "
                f"to finish and re-run; there is no manual lock-file "
                f"removal needed or supported -- the OS releases the lock "
                f"automatically when the holding process exits."
            ) from None
        # PID is written purely for human inspection (e.g. `cat` the lock
        # file while debugging); it is never read back or used for any
        # correctness decision.
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            import fcntl

            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        # Deliberately no unlink() here -- see class docstring.

    def __enter__(self) -> "_ReembedLock":
        self.acquire()
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------

def run(pkb_root: Path, *, resume: bool = False, dry_run: bool = False,
        batch_size: int | None = None, include_docs: bool = True,
        progress: Callable[..., None] = lambda **kw: None) -> dict[str, Any]:
    """Do the reembed. Returns a result dict; never raises for a SIGINT
    (returns ``{"interrupted": True, ...}`` instead) — but DOES raise
    ``arail.dbspec.embed.EmbeddingError`` if the provider is unavailable,
    ``CheckpointSpecMismatch`` if ``--resume``'s checkpoint disagrees with
    the current spec, ``ShadowBuildIncomplete`` if the shadow build's row
    count disagrees with what was expected (BLOCK-2), ``EmptyCorpusRefused``
    if the corpus is empty and a live table already exists (BLOCK-2),
    ``ReembedLocked`` if another run is already in progress against this
    root, and ``RuntimeError`` if LanceDB is unavailable.
    """
    import lancedb  # type: ignore[import-not-found]
    from arail import pkb as pkb_mod
    from arail import pkb_provenance
    from arail.vector_index import available, VectorIndex
    from arail.dbspec import embed as embed_mod
    from arail.dbspec.generated.models_registry import EMBEDDING_DIM, embedding_model

    if not available():
        raise RuntimeError("LanceDB is not importable; cannot reembed")

    batch_size = batch_size or embed_mod._BATCH
    model = embedding_model()
    spec_sha = pkb_mod._current_spec_sha256()

    pending_rows = pkb_mod.collect_pending_rows(pkb_root, include_docs=include_docs)
    total = len(pending_rows)

    if dry_run:
        probe_rows = pending_rows[:32]
        t0 = time.monotonic()
        if probe_rows:
            embed_mod.embed_documents([r["embed_input"] for r in probe_rows])
        elapsed = time.monotonic() - t0
        rate = (len(probe_rows) / elapsed) if elapsed > 0 else float("inf")
        eta = (total / rate) if rate not in (0, float("inf")) else 0.0
        return {
            "dry_run": True, "total": total, "probe_rows": len(probe_rows),
            "rows_per_sec": rate, "eta_sec": eta,
        }

    with _ReembedLock(pkb_root):
        try:
            return _run_locked(
                pkb_root, resume=resume, batch_size=batch_size,
                pending_rows=pending_rows, total=total, model=model,
                spec_sha=spec_sha, progress=progress,
                lancedb=lancedb, pkb_mod=pkb_mod, pkb_provenance=pkb_provenance,
                VectorIndex=VectorIndex, embed_mod=embed_mod,
                EMBEDDING_DIM=EMBEDDING_DIM,
            )
        except OSError as e:
            # QA-2: a read-only/full .cache raised a bare PermissionError/
            # OSError past every RuntimeError handler in main(). Every other
            # failure mode this verb reports uses an English message and a
            # non-zero exit code -- this one must too.
            raise ReembedIOError(
                f"a filesystem operation failed while re-embedding "
                f"{pkb_root}: {e}. Check that {_shadow_dir(pkb_root).parent} "
                f"is writable (not full, not read-only) and re-run "
                f"`./arailctl pkb reembed`."
            ) from e


def _run_locked(pkb_root: Path, *, resume: bool, batch_size: int,
                 pending_rows: list[dict[str, Any]], total: int, model,
                 spec_sha: str, progress: Callable[..., None],
                 lancedb, pkb_mod, pkb_provenance, VectorIndex, embed_mod,
                 EMBEDDING_DIM: int) -> dict[str, Any]:
    """The write phase, called with the per-root lock already held."""

    # BLOCK-2: an empty corpus must never silently wipe a populated live
    # index. Refuse before touching anything.
    live_table_path = _live_dir(pkb_root) / "pkb_pages.lance"
    if total == 0 and live_table_path.exists():
        raise EmptyCorpusRefused(
            f"the corpus at {pkb_root} has zero rows to embed, but a live "
            f"index already exists at {live_table_path} — refusing to swap "
            f"an empty result over a populated index. If the corpus is "
            f"genuinely and permanently empty and you want to clear the "
            f"index, remove {live_table_path} manually.")

    checkpoint = _load_checkpoint(pkb_root) if resume else None
    completed_paths: set[str] = set()
    resume_discarded_reason: str | None = None
    if checkpoint is not None:
        if (checkpoint.get("model") != model.name
                or checkpoint.get("dim") != EMBEDDING_DIM
                or checkpoint.get("spec_sha256") != spec_sha):
            raise CheckpointSpecMismatch(
                "checkpoint was started with a different embedding spec "
                "(model/dim/spec changed) — refusing to mix vector spaces "
                "in one shadow build. Run without --resume to start over "
                "(the previous shadow build will be discarded).")
        completed_paths = set(checkpoint.get("completed_paths", []))

    shadow_dir = _shadow_dir(pkb_root)

    if completed_paths:
        # BLOCK-2 (scenario 4): the checkpoint claims N completed paths —
        # verify the shadow build actually HAS N rows before trusting it.
        # A missing .next dir, a row count that disagrees, or an unreadable
        # table all mean the same thing: this checkpoint cannot be trusted
        # to resume from. Discard it and start fresh rather than embedding
        # only the "remaining" rows and reporting the checkpoint's stale
        # total as if it were achieved.
        shadow_row_count = 0
        if shadow_dir.exists():
            try:
                probe_db = lancedb.connect(str(shadow_dir))
                existing = VectorIndex._existing_tables(probe_db)
                if "pkb_pages" in existing:
                    shadow_row_count = int(probe_db.open_table("pkb_pages").count_rows())
            except Exception:  # noqa: BLE001
                shadow_row_count = -1  # unreadable -> definitely a mismatch
        if shadow_row_count != len(completed_paths):
            resume_discarded_reason = (
                f"--resume checkpoint claimed {len(completed_paths)} "
                f"completed rows, but the shadow build at {shadow_dir} has "
                f"{shadow_row_count if shadow_row_count >= 0 else 'an unreadable'} "
                f"row count — discarding the checkpoint and starting over.")
            _log_stderr(resume_discarded_reason)
            completed_paths = set()
            checkpoint = None

    if not completed_paths:
        # Fresh start (either genuinely fresh, or a discarded/absent
        # resume): discard any stale shadow build so it can't get silently
        # mixed with this run.
        _clear_checkpoint(pkb_root)
        shutil.rmtree(shadow_dir, ignore_errors=True)

    remaining = [r for r in pending_rows if r["path"] not in completed_paths]

    shadow_dir.parent.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(shadow_dir))
    table = None
    if completed_paths:
        existing = VectorIndex._existing_tables(db)
        if "pkb_pages" in existing:
            table = db.open_table("pkb_pages")

    started_at = checkpoint.get("started_at") if checkpoint else _now_iso()

    interrupted = {"flag": False}

    def _sigint_handler(signum, frame):  # noqa: ARG001
        interrupted["flag"] = True

    old_handler = signal.signal(signal.SIGINT, _sigint_handler)
    completed_count = len(completed_paths)
    try:
        for start in range(0, len(remaining), batch_size):
            if interrupted["flag"]:
                break
            batch = remaining[start:start + batch_size]
            t0 = time.monotonic()
            vectors = embed_mod.embed_documents([r["embed_input"] for r in batch])
            elapsed = time.monotonic() - t0
            rows = [
                {
                    "path": r["path"], "name": r["name"], "vector": v,
                    "mtime": r["mtime"], "source_kind": r["source_kind"],
                }
                for r, v in zip(batch, vectors)
            ]
            if table is None:
                table = db.create_table("pkb_pages", data=rows, mode="overwrite")
            else:
                table.add(rows)
            completed_paths.update(r["path"] for r in batch)
            completed_count += len(batch)
            _write_checkpoint(pkb_root, {
                "schema": SCHEMA, "model": model.name, "dim": EMBEDDING_DIM,
                "spec_sha256": spec_sha, "started_at": started_at,
                "total": total, "completed_paths": sorted(completed_paths),
                "batch": batch_size,
            })
            rate = (len(batch) / elapsed) if elapsed > 0 else None
            progress(done=completed_count, total=total, rate=rate)
    finally:
        signal.signal(signal.SIGINT, old_handler)

    if interrupted["flag"]:
        return {
            "interrupted": True, "completed": completed_count, "total": total,
            "resume_discarded_reason": resume_discarded_reason,
        }

    # BLOCK-2 (scenario 4, defense in depth): verify the shadow build's
    # actual row count matches `total` before swapping ANYTHING live. Even
    # though the loop above believes it processed every remaining row, this
    # re-reads the table LanceDB actually wrote rather than trusting the
    # in-memory counters — the same discipline the checkpoint-resume check
    # above applies, now applied to the write this process itself just did.
    shadow_row_count = int(table.count_rows()) if table is not None else 0
    if shadow_row_count != total:
        shutil.rmtree(shadow_dir, ignore_errors=True)
        _clear_checkpoint(pkb_root)
        raise ShadowBuildIncomplete(
            f"shadow build has {shadow_row_count} rows but the corpus scan "
            f"expected {total} — discarding the inconsistent shadow build "
            f"and checkpoint rather than swapping a truncated index in. "
            f"Re-run `./arailctl pkb reembed` (without --resume) to start "
            f"over.")

    # All rows verified present — atomic-ish swap. Only now does the live
    # table move; a crash before this point leaves the old live table
    # untouched plus a (harmless, resumable-or-discardable) .next shadow dir.
    live_dir = _live_dir(pkb_root)
    live_dir.mkdir(parents=True, exist_ok=True)
    live_table = live_dir / "pkb_pages.lance"
    backup_path: str | None = None
    live_table_exists = live_table.exists()
    if live_table_exists:
        # QA-1: two reembeds completing within the same wall-clock second
        # both compute the same second-resolution backup name, and the
        # second os.replace() collided with the first's non-empty backup
        # dir (bare OSError(ENOTEMPTY), unhandled). Pick a name nothing is
        # using instead of assuming the timestamp alone is unique.
        bak = live_dir / f"pkb_pages.lance.bak-{int(time.time())}"
        suffix = 1
        while bak.exists():
            bak = live_dir / f"pkb_pages.lance.bak-{int(time.time())}-{suffix}"
            suffix += 1
        os.replace(live_table, bak)
        backup_path = str(bak)
    shadow_table = shadow_dir / "pkb_pages.lance"
    if shadow_table.exists():
        os.replace(shadow_table, live_table)
        live_table_exists = True

    if live_table_exists:
        # QA-3: an empty corpus with no pre-existing live table never
        # creates `table` above (the batch loop has nothing to iterate),
        # so there is nothing to swap in here either -- writing the
        # sidecar unconditionally left a provenance record describing a
        # table that was never written. Only write it when a live table
        # actually exists after the swap attempt.
        pkb_provenance.write(
            live_dir, embedding_model=model.name, embedding_dim=EMBEDDING_DIM,
            spec_sha256=spec_sha, rows=completed_count)

    _clear_checkpoint(pkb_root)
    shutil.rmtree(shadow_dir, ignore_errors=True)

    try:
        from arail import pkb_index
        pkb_index.clear_degraded(None)
    except Exception:  # noqa: BLE001
        pass

    return {
        "interrupted": False, "completed": completed_count, "total": total,
        "backup": backup_path, "resume_discarded_reason": resume_discarded_reason,
    }


def _log_stderr(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr, flush=True)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _default_progress(world_label: str):
    def _p(*, done: int, total: int, rate: float | None = None):
        if rate:
            eta = (total - done) / rate if rate > 0 else 0.0
            print(f"[{world_label}] {done}/{total} rows, {rate:.1f} rows/s, "
                  f"ETA {eta:.0f}s", flush=True)
        else:
            print(f"[{world_label}] {done}/{total} rows", flush=True)
    return _p


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m arail.pkb_reembed",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pkb-root", required=True,
                   help="Absolute path to the PKB root to reembed.")
    p.add_argument("--world-label", default="root",
                   help="Label used in progress lines / activity events "
                        "(the arailctl dispatcher passes the world slug "
                        "or 'root').")
    p.add_argument("--resume", action="store_true",
                   help="Resume from the last checkpoint. Refuses if the "
                        "checkpoint's model/dim/spec disagree with the "
                        "current spec.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print row count and an ETA from a 32-row timing "
                        "probe. Writes nothing.")
    p.add_argument("--yes", action="store_true",
                   help="Reserved for future confirmation prompts; "
                        "currently a no-op (this verb never prompts).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    pkb_root = Path(args.pkb_root)
    if not pkb_root.exists():
        print(f"error: pkb root {pkb_root} does not exist", file=sys.stderr)
        return 2

    from arail.dbspec.embed import EmbeddingError

    try:
        from arail.activity import activity_log
        activity_log.emit(
            "pkb", f"pkb reembed starting for '{args.world_label}'", "info")
    except Exception:  # noqa: BLE001
        pass

    try:
        result = run(pkb_root, resume=args.resume, dry_run=args.dry_run,
                      progress=_default_progress(args.world_label))
    except CheckpointSpecMismatch as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except EmbeddingError as e:
        print(f"error: {e}", file=sys.stderr)
        return 4
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"[{args.world_label}] dry-run: {result['total']} rows, "
              f"~{result['rows_per_sec']:.1f} rows/s, "
              f"ETA {result['eta_sec']:.0f}s — nothing written")
        return 0

    if result.get("resume_discarded_reason"):
        print(f"[{args.world_label}] {result['resume_discarded_reason']}",
              file=sys.stderr)

    if result.get("interrupted"):
        print(f"\n[{args.world_label}] interrupted at "
              f"{result['completed']}/{result['total']} rows — checkpoint "
              f"saved. Resume with: ./arailctl pkb reembed ... --resume",
              file=sys.stderr)
        return 130

    print(f"[{args.world_label}] done: {result['completed']}/{result['total']} "
          f"rows re-embedded"
          + (f" (previous index backed up to {result['backup']})"
             if result.get("backup") else ""))
    try:
        from arail.activity import activity_log
        activity_log.emit(
            "pkb", f"pkb reembed finished for '{args.world_label}': "
                   f"{result['completed']} rows", "info")
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
