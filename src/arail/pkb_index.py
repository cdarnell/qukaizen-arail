"""PKB incremental index — in-process debounced upsert into LanceDB.

Public API
----------
ensure_ready(pkb_root, build=True) — Call once at portal startup, or from
                           any genuine content-write path (a World mount,
                           a captured note). Checks the pkb_pages table
                           schema and runs a bounded staleness sweep.
                           Triggers index_all() if the table is missing or
                           schema-mismatched (dimension mismatch never drops
                           the table — see C2/FM12). ``build=False``
                           (REVIEW3.md BLOCK-3) is genuinely read-only:
                           inspects and reports the same status without
                           ever calling index_all(), dropping a table, or
                           scheduling an upsert. Every diagnostic caller
                           (``./arailctl doctor``) MUST pass ``build=False``
                           — a health check must never build, or pay for,
                           the thing it is checking.
schedule_upsert(path)    — Enqueue a path for upsert. Safe to call from any
                           thread; the write helper never blocks. Debounce
                           window is LAB_PKB_UPSERT_DEBOUNCE_SEC (default 2s),
                           or the error back-off (60s) while degraded.
embedding_status()       — (ok, message) for the embedding subsystem. ok is
                           False while any degraded code is set (C1).
set_degraded(code, reason) — Mark ONE cause degraded. Codes: "provider"
                           (embed call failed), "dimension" (table's vector
                           width disagrees with the spec), "provenance"
                           (sidecar disagrees with the spec), "empty" (no
                           rows yet). REVIEW2.md BLOCK-1: these are
                           independent facts. A successful embed call only
                           proves the *provider* is reachable — it must
                           NEVER clear "dimension"/"provenance"/"empty",
                           which are facts about the table, not the network.
clear_degraded(code=None) — Clear one cause (evidence about THAT cause
                           only), or every cause (code=None — used only
                           after a full rebuild, which is evidence about
                           all of them at once: index_all/pkb_reembed).
degraded_codes()         — dict of the currently active codes -> reasons.
                           Callers that need to know WHICH cause (not just
                           whether something is wrong — e.g. doctor's
                           exit-code decision) use this instead of parsing
                           embedding_status()'s prose.
check_read_path_health(table, db_path) — C4 enforcement ON THE READ PATH,
                           not just at ensure_ready/startup: dimension then
                           provenance, both required before a query is
                           served as semantic. Sets/clears "dimension"/
                           "provenance" as a side effect. Called by
                           pkb._semantic_search on EVERY search.
_reset_for_tests()       — Reset module-level state between tests. Not for
                           production use.

Design notes
------------
* threading.Timer — not asyncio — so this module works from sync helpers,
  async portal handlers, and CLI entrypoints without requiring a running
  event loop. Deliberately diverges from wiki.py's asyncio debouncer.
* One threading.Lock serializes all state mutations and the merge_insert call.
* The file write is NEVER blocked by indexing. Every schedule_upsert call
  in the write helpers is wrapped in try/except Exception: pass.
* merge_insert upsert path (LanceDB ≥ 0.5). Falls back to delete+add if
  merge_insert is absent on a pinned env.
* Path safety: paths outside pkb_root are rejected via relative_to().
* C1 error contract (ARCHITECTURE.md): an embedding outage must be
  impossible to mistake for an empty corpus. Every call site that can
  trigger a network embed call catches ``arail.dbspec.embed.EmbeddingError``
  *separately* from every other exception: logs at ERROR (not WARNING),
  emits an ``activity_log`` event at severity "error", and sets the module
  degraded flag via ``set_degraded()``. Non-embedding exceptions keep the
  original WARNING behaviour — those are the SKIP/DEGRADE classes, not LOUD.
* Reason-scoped degraded state (REVIEW2.md BLOCK-1, fixed here): the
  degraded flag used to be one module-global bool covering five unrelated
  facts, so a successful search (which only proves the *provider* is
  reachable) was clearing a standing dimension/provenance warning and
  ``doctor`` would report "ok" while semantic search silently returned
  nothing forever. Every ``set_degraded``/``clear_degraded`` call below
  names the specific code it has evidence about.

Load-bearing invariant: one PKB root per process
-------------------------------------------------
``_pending``, ``_timer``, ``_initialized_roots``, ``_pkb_root_cache``, and
``_degraded_codes`` are all process-wide module globals, not per-root
state. That is safe ONLY because ``arail.config.PKB_ROOT`` is a module
constant resolved once at import and never rebound in-process anywhere in
``src/``, and because concurrent Worlds run one process per World
(``./arailctl start --world <slug>`` — see ``docs/concurrent-worlds.md``).
Given that, "the PKB root this process cares about" never changes after
startup, so a process-wide global is equivalent to a per-root one.

If either half of that invariant is ever broken — ``PKB_ROOT`` gets
rebound in-process (e.g. a multi-tenant server loop), or two Worlds start
sharing one process — these globals stop being safe: degraded-state codes
from one root would leak into another root's ``embedding_status()``, and
(per REVIEW4.md ASK-1) a read-only ``ensure_ready(build=False)`` check on
one root could redirect ``_pkb_root_cache``, the resolution target
``_flush()`` joins ``_pending``'s root-relative paths against, causing a
pending upsert queued for root A to be written under root B. This is
tracked as filed debt, not fixed, in ``sprints/BACKLOG.md`` ("pkb_index's
degraded state is a module global; PKB roots are per-World") — see that
entry before changing either half of this invariant.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# ── Module-level coalescer state ─────────────────────────────────────────

_lock: threading.Lock = threading.Lock()
_pending: set[str] = set()          # POSIX relative paths
_timer: threading.Timer | None = None
# One-shot guard for a COMPLETED-INTENT build=True ensure_ready() pass, per
# root (resolved path). REVIEW4 (coordinator finding, post-BLOCK-3): the
# guard used to be a single process-global bool set unconditionally by
# ENTERING ensure_ready at all -- so a read-only build=False call (e.g.
# ./arailctl doctor) "used up" the one-shot slot without doing any work,
# and a later, genuine build=True call in the same process for the SAME OR
# ANY OTHER root would then silently no-op. A read-only check must never
# satisfy, suppress, or substitute for a build. Keyed by root so a
# read-only check against one World also cannot affect another. Only
# build=True ever adds to this set; build=False never reads or writes it,
# so it always re-executes its (idempotent, cheap) read-only logic fresh.
_initialized_roots: set[Path] = set()
_pkb_root_cache: Path | None = None  # set by ensure_ready

# ── C1: degraded-embedding state ─────────────────────────────────────────
# code -> reason. Set by every call site that has evidence of that specific
# cause; cleared only by evidence about that same cause (REVIEW2.md
# BLOCK-1). Read via embedding_status()/degraded_codes() by
# pkb.retrieval_status() (wired into the /api/pkb/search payload),
# ./arailctl doctor, and pkb._semantic_search's own read-path check.
#
# Known codes:
#   "provider"   — the embedding call itself failed (network/model down).
#                  Cleared by the NEXT successful embed call, nothing else.
#   "dimension"  — the table's vector width disagrees with the spec.
#                  Cleared only by a full rebuild (index_all/pkb_reembed)
#                  or by check_read_path_health() re-observing agreement.
#   "provenance" — the sidecar disagrees with (or is absent for) the spec.
#                  Cleared the same way as "dimension".
#   "empty"      — the table has zero rows. Cleared only by a rebuild.
#   "backend"    — vector_index.available() is False: the running
#                  interpreter cannot import the vector backend at all
#                  (defect B, sprints/2026-08-10-arail2-persistence-
#                  instantiated). Cleared only by a later successful
#                  available() observation or a full rebuild — a
#                  successful embed call is not evidence about this code.
_degraded_codes: dict[str, str] = {}

# Required columns (and their expected presence) for schema validation.
_REQUIRED_COLS = {"path", "name", "vector", "mtime", "source_kind"}
_STALENESS_CAP = 200   # max files checked in the sweep before falling back to index_all
_DEFAULT_DEBOUNCE = 2.0
_ERROR_BACKOFF_SEC = 60.0  # C1/FM17: back off retries after an EmbeddingError


def _vector_dim() -> int:
    """The spec-declared embedding dimension. A function, not a module
    constant, so it always reflects the current spec/models.hcl rather than
    a value frozen at import time — matters once ``embedding_model()``'s
    declared dimension can change without a code deploy."""
    from arail.dbspec.generated.models_registry import EMBEDDING_DIM
    return EMBEDDING_DIM


def set_degraded(code: str, reason: str) -> None:
    """Mark ONE cause (``code``) degraded. Idempotent; safe to call from any
    thread (no lock needed — a stale read of a dict entry is harmless, and
    callers only ever set a code to a fresher reason for that same code)."""
    _degraded_codes[code] = reason


def clear_degraded(code: str | None = None) -> None:
    """Clear one cause (``code``), or every cause if ``code`` is None.

    ``code=None`` is deliberately reserved for callers that have evidence
    about ALL causes at once — a full rebuild (index_all/pkb_reembed
    success) freshly writes the table and its provenance, so every code is
    simultaneously resolved. A single successful embed call, or a single
    successful incremental upsert, is NOT such evidence (BLOCK-1) — those
    call sites must pass the specific code they have evidence about.
    """
    if code is None:
        _degraded_codes.clear()
    else:
        _degraded_codes.pop(code, None)


def degraded_codes() -> dict[str, str]:
    """Copy of the currently active degraded codes -> reasons. Callers that
    need to distinguish WHICH cause (not just whether something is wrong —
    e.g. doctor's exit-code decision) use this instead of substring-matching
    ``embedding_status()``'s prose message."""
    return dict(_degraded_codes)


def embedding_status() -> tuple[bool, str]:
    """(ok, message). ok=False while any degraded code is set. Read by
    ``arail.pkb.retrieval_status()`` (wired into the ``/api/pkb/search``
    payload), ``./arailctl doctor``, and ``pkb._semantic_search``'s own
    read-path check."""
    if not _degraded_codes:
        return True, ""
    reason = "; ".join(f"{v}" for v in _degraded_codes.values())
    return False, reason


def _index_all_reporting_embedding_errors(root: Path, context: str) -> None:
    """Shared body for every ``pkb.index_all()`` call site in this module.

    Catches ``EmbeddingError`` (LOUD, per C1) separately from every other
    exception (kept as the original WARNING/SKIP behaviour). Never raises —
    the caller sites all treat index_all() as best-effort background work,
    same as before this contract existed.
    """
    from arail.dbspec.embed import EmbeddingError

    try:
        from arail import pkb as pkb_mod
        pkb_mod.index_all(root)
        # A full rebuild is evidence about EVERY code at once: fresh
        # vectors at the spec's dimension, fresh provenance sidecar,
        # non-empty (unless the corpus genuinely is empty, in which case
        # index_all's own "empty" degrade below re-sets it).
        clear_degraded(None)
        if pkb_mod._vector_db_path(root).exists():
            idx_count = _open_table_row_count(root)
            if idx_count == 0:
                set_degraded("empty", "KB index is empty — run `./arailctl pkb reembed`")
    except EmbeddingError as e:
        _log.error("pkb_index: %s: embedding provider unavailable: %s", context, e)
        try:
            from arail.activity import activity_log
            activity_log.emit(
                "pkb", f"KB index build failed ({context}): embedding provider "
                       f"unavailable — {e}", "error")
        except Exception:
            pass
        set_degraded("provider", str(e))
    except Exception as e:  # noqa: BLE001 — SKIP/DEGRADE class, original behaviour
        _log.warning("pkb_index: %s failed: %s", context, e)


def _open_table_row_count(root: Path) -> int:
    """Best-effort row count for the just-(re)built pkb_pages table. Used
    only to decide whether to re-set the "empty" code after a rebuild that
    legitimately produced zero rows. Never raises."""
    try:
        import lancedb  # type: ignore[import-not-found]
        db = lancedb.connect(str(_vector_db_path(root)))
        table = _open_table(db, "pkb_pages")
        return int(table.count_rows()) if table is not None else 0
    except Exception:  # noqa: BLE001
        return 0


def _debounce_sec() -> float:
    """Normal debounce, or the 60s error back-off while degraded (FM17) —
    a dead embedding provider must not turn the debounce timer into a
    tight retry storm."""
    if _degraded_codes:
        return _ERROR_BACKOFF_SEC
    try:
        return float(os.environ.get("LAB_PKB_UPSERT_DEBOUNCE_SEC", _DEFAULT_DEBOUNCE))
    except (TypeError, ValueError):
        return _DEFAULT_DEBOUNCE


# ── Helpers ───────────────────────────────────────────────────────────────

def _vector_db_path(root: Path) -> Path:
    return root / ".cache" / "lancedb"


def _pkb_root_from_env() -> Path:
    """Resolve PKB root the same way pkb.py does."""
    from arail.config import PKB_ROOT
    return PKB_ROOT


def _build_row(abs_path: Path, rel_posix: str, source_kind: str) -> dict[str, Any] | None:
    """Read the file and return a row dict, or None if the file is
    unreadable (SKIP class — an OSError here is a per-item concern, not a
    provider outage).

    Raises ``arail.dbspec.embed.EmbeddingError`` (LOUD, per C1) if the
    embedding call fails — deliberately NOT caught here. The one caller,
    ``_flush``, catches it separately from every other exception so a dead
    provider aborts the whole flush instead of being recorded as a
    per-path failure and retried in a tight loop (FM17)."""
    from arail.dbspec.embed import embed_documents
    try:
        text = abs_path.read_text(errors="replace")
    except OSError:
        return None
    snippet = text[:4096]
    name = abs_path.name
    vec = embed_documents([f"{name} {rel_posix} {snippet}"])[0]
    mtime = abs_path.stat().st_mtime
    return {
        "path": rel_posix,
        "name": name,
        "vector": vec,
        "mtime": mtime,
        "source_kind": source_kind,
    }


def _source_kind_for_path(rel_posix: str) -> str:
    """Infer source_kind from the relative path prefix.

    Delegates to the single implementation in ``arail.pkb`` so the two indexing
    paths (full rebuild vs incremental upsert) can never drift apart. (The
    review-queue's ``compiled_kb.kind_of`` is a deliberately separate vocabulary
    — e.g. it labels dreams ``agent_dream`` and adds ``world_term`` — and is not
    merged here.)"""
    from arail.pkb import _source_kind_for_rel
    return _source_kind_for_rel(rel_posix)


def _open_table(db, name: str):
    """Open an existing table; returns None if not present."""
    from arail.vector_index import VectorIndex
    existing = VectorIndex._existing_tables(db)
    if name in existing:
        try:
            return db.open_table(name)
        except Exception:
            return None
    return None


def _schema_column_status(table) -> tuple[bool, bool]:
    """Return (missing_columns, dim_mismatch).

    Split out from the old single ``_schema_ok`` bool so ``ensure_ready``
    can tell the two failure kinds apart (C2/FM12): missing columns is a
    cheap, offline, safe-to-drop-and-rebuild schema upgrade; a dimension
    mismatch (e.g. 128 hash -> 768 nomic) is NEVER safe to drop — that is
    how a config change silently empties a user's index. ``(True, False)``
    on any read failure (unreadable schema): treated as "needs rebuild",
    never as a dimension issue, since we genuinely don't know the dimension.
    """
    try:
        schema = table.schema
        names = set(schema.names)
        missing_columns = not _REQUIRED_COLS.issubset(names)
        dim_mismatch = False
        if "vector" in names:
            vec_field = schema.field("vector")
            # Arrow FixedSizeList type has .list_size attribute.
            fsl = vec_field.type
            size = getattr(fsl, "list_size", None)
            if size is None:
                # Older Arrow: .value_type exists but not list_size.
                size = getattr(fsl, "value_size", None)
            if size is not None and int(size) != _vector_dim():
                dim_mismatch = True
        return missing_columns, dim_mismatch
    except Exception:
        return True, False


def _schema_ok(table) -> bool:
    """True iff the table has all required columns and the correct vector
    dimension. Thin wrapper over ``_schema_column_status`` kept for any
    caller/test that only wants the combined answer."""
    missing_columns, dim_mismatch = _schema_column_status(table)
    return not missing_columns and not dim_mismatch


def _check_provenance(table, db_path: Path) -> tuple[bool, str]:
    """Provenance-only check (C4): does the sidecar agree with the spec?

    Assumes the caller already confirmed schema/dimension are fine — this
    is the check that catches "same dimension, different model" (a foreign
    vector space that a raw LanceDB dimension check cannot see). Sets or
    clears the "provenance" degraded code as a side effect, so this is the
    single shared implementation both ``ensure_ready`` and
    ``check_read_path_health`` (the search-path check) call, rather than
    two copies that could drift (REVIEW2.md BLOCK-1's root cause was
    exactly this: C4 lived only in ``ensure_ready`` and the read path had
    no equivalent check at all)."""
    from arail import pkb_provenance
    from arail.dbspec.generated.models_registry import embedding_model as _embedding_model

    current = _embedding_model()
    record = pkb_provenance.read(db_path)
    if not pkb_provenance.agrees_with_spec(
            record, embedding_model=current.name, embedding_dim=_vector_dim()):
        if record is None:
            reason = (
                "pkb_pages index has no provenance record — treated as a "
                "legacy index. Run `./arailctl pkb reembed` to upgrade."
            )
        else:
            reason = (
                f"pkb_pages index provenance "
                f"({record.get('embedding_model')}/{record.get('embedding_dim')}d) "
                f"disagrees with the current spec "
                f"({current.name}/{_vector_dim()}d) — run "
                f"`./arailctl pkb reembed` to upgrade. Existing rows are untouched."
            )
        set_degraded("provenance", reason)
        return False, reason
    clear_degraded("provenance")
    return True, ""


def check_read_path_health(table, db_path: Path) -> tuple[bool, str]:
    """Full read-path health check (C4/BLOCK-1 fix): dimension, then
    provenance — both required before a query is served as semantic.

    Called by ``pkb._semantic_search`` on EVERY search, not just at
    ``ensure_ready``/startup, so a table that degrades mid-process (a
    sidecar rewritten to a different model, a schema changed underneath)
    is caught on the very next search rather than only at the next process
    boot. Returns ``(ok, reason)``; sets/clears the "dimension"/
    "provenance" codes as a side effect, so a passing check is exactly as
    load-bearing as a failing one — REVIEW2.md's fix requires that only
    evidence about a specific cause clears it, and re-verifying dimension/
    provenance on every search IS that evidence."""
    missing_columns, dim_mismatch = _schema_column_status(table)
    if dim_mismatch and not missing_columns:
        reason = (
            "pkb_pages index was built with a different embedding "
            "dimension than the current spec declares — run "
            "`./arailctl pkb reembed` to upgrade. Existing rows are "
            "untouched."
        )
        set_degraded("dimension", reason)
        return False, reason
    if missing_columns:
        reason = "pkb_pages index schema is missing required columns."
        set_degraded("dimension", reason)
        return False, reason
    clear_degraded("dimension")
    return _check_provenance(table, db_path)


def _flush() -> None:
    """Flush the pending set to LanceDB. Called by the debounce timer."""
    global _timer
    from arail.vector_index import available

    if not available():
        _log.warning("pkb_index: LanceDB not available; skipping flush")
        with _lock:
            _timer = None
        return

    with _lock:
        _timer = None
        if not _pending:
            return
        snapshot = set(_pending)
        # Do NOT clear _pending yet — only clear the paths we successfully
        # process (delete or upsert). On error, keep them so next arm retries.
        root = _pkb_root_cache

    if root is None:
        return

    import lancedb  # type: ignore[import-not-found]

    db_path = _vector_db_path(root)
    db_path.mkdir(parents=True, exist_ok=True)
    try:
        db = lancedb.connect(str(db_path))
    except Exception as e:
        _log.warning("pkb_index: cannot connect to LanceDB: %s", e)
        return

    table = _open_table(db, "pkb_pages")
    if table is None:
        # Table disappeared between ensure_ready and flush — rebuild.
        _index_all_reporting_embedding_errors(root, "flush: pkb_pages table missing")
        with _lock:
            _pending.difference_update(snapshot)
        return

    from arail.dbspec.embed import EmbeddingError

    t0 = time.monotonic()
    upserted = 0
    deleted = 0
    failed_paths: set[str] = set()
    embedding_aborted = False

    for rel_posix in snapshot:
        abs_path = root / rel_posix
        if not abs_path.exists():
            # File was deleted between schedule_upsert and flush.
            try:
                escaped = rel_posix.replace("'", "''")
                table.delete(f"path = '{escaped}'")
                deleted += 1
            except Exception as e:
                _log.warning("pkb_index: delete failed for %s: %s", rel_posix, e)
                failed_paths.add(rel_posix)
            continue

        source_kind = _source_kind_for_path(rel_posix)
        try:
            row = _build_row(abs_path, rel_posix, source_kind)
        except EmbeddingError as e:
            # C1/FM17: an EmbeddingError on one path must NOT be recorded as
            # a per-path failure and retried in a tight loop. Abort the
            # whole flush, keep every remaining path in _pending (including
            # this one) for the next arm, degrade, and back off to 60s.
            _log.error("pkb_index: flush aborted — embedding provider "
                       "unavailable: %s", e)
            try:
                from arail.activity import activity_log
                activity_log.emit(
                    "pkb", f"KB flush aborted: embedding provider unavailable "
                           f"({e})", "error")
            except Exception:
                pass
            set_degraded("provider", str(e))
            embedding_aborted = True
            break
        if row is None:
            continue

        try:
            mi = getattr(table, "merge_insert", None)
            if mi is not None:
                # Preferred: single merge_insert transaction
                table.merge_insert("path") \
                     .when_matched_update_all() \
                     .when_not_matched_insert_all() \
                     .execute([row])
            else:
                # Fallback: delete + add
                escaped = rel_posix.replace("'", "''")
                table.delete(f"path = '{escaped}'")
                table.add([row])
            upserted += 1
            # A successful upsert only proves the embedding PROVIDER is
            # reachable — it says nothing about the table's dimension or
            # provenance (BLOCK-1: the old blanket clear_degraded() here is
            # exactly how a single file save cleared a standing provenance
            # warning). Clear only "provider".
            clear_degraded("provider")
        except Exception as e:
            _log.warning("pkb_index: upsert failed for %s: %s", rel_posix, e)
            failed_paths.add(rel_posix)

    if embedding_aborted:
        # _pending is untouched — the whole snapshot, including whatever
        # this loop hadn't reached yet, stays queued for the next arm.
        # Re-arm here (not just wait for the next schedule_upsert) so a dead
        # Ollama is retried automatically at the 60s back-off rather than
        # only on the next unrelated file write.
        with _lock:
            _arm_timer()
        return

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    _log.info(
        "pkb_index: upserted %d rows, deleted %d rows in %d ms",
        upserted, deleted, elapsed_ms,
    )

    # Emit to activity log (best-effort)
    try:
        from arail.activity import activity_log
        if upserted or deleted:
            activity_log.emit(
                "pkb",
                f"pkb_index: upserted {upserted} rows, deleted {deleted} rows in {elapsed_ms} ms",
                "info",
            )
    except Exception:
        pass

    with _lock:
        # Remove successfully processed paths; keep failed ones for retry.
        _pending.difference_update(snapshot - failed_paths)


def _arm_timer() -> None:
    """Cancel any existing timer and start a fresh debounce. Must be called under _lock."""
    global _timer
    if _timer is not None:
        _timer.cancel()
    _timer = threading.Timer(_debounce_sec(), _flush)
    _timer.daemon = True
    _timer.start()


def flush_now() -> None:
    """Flush any pending upserts immediately instead of waiting out the
    debounce window. Used after a World mount/swap so the KB is searchable
    right away rather than seconds later. Cancels the pending timer, runs the
    same ``_flush`` synchronously. Never raises; no-op when LanceDB is absent
    (``_flush`` self-guards)."""
    global _timer
    with _lock:
        if _timer is not None:
            _timer.cancel()
            _timer = None
    try:
        _flush()
    except Exception as e:  # noqa: BLE001
        _log.warning("pkb_index: flush_now failed: %s", e)


# ── Public API ────────────────────────────────────────────────────────────

def ensure_ready(pkb_root: Path | None = None, *, build: bool = True) -> None:
    """Check (and, if ``build=True``, build) the pkb_pages table.

    ``build=True`` (default) — call once at portal startup, or from any
    genuine content-write path (a World mount, a captured voice/OCR note)
    that legitimately wants its new content indexed. Detection logic, in
    order:
    1. If LanceDB is unavailable, return immediately.
    2. If the table is missing, call index_all() and return.
    3. If required columns are missing, drop and call index_all() once
       (cheap, offline-safe schema upgrade).
    4. If only the vector *dimension* differs from the current spec
       (e.g. 128-dim hash -> 768-dim nomic), NEVER drop the table — degrade
       with an actionable message and leave the rows exactly as they are
       (C2/FM12). An explicit ``./arailctl pkb reembed`` is the only path
       that rewrites those rows.
    5. If the schema and dimension are fine but the provenance sidecar
       (C4) disagrees with — or is missing for — the current spec, degrade;
       no query is served from a table whose provenance disagrees with the
       spec.
    6. Otherwise run the bounded staleness sweep (cap 200 files), which
       SCHEDULES upserts (a later debounced write) for stale files. If more
       than 200 stale files are found, fall back to index_all().

    ``build=False`` (REVIEW3.md BLOCK-3 fix) — genuinely read-only: every
    branch above that would call ``index_all()``, drop a table, or
    schedule an upsert instead only *reports* the same status (degrading
    with the same message where applicable) and returns. Zero
    ``embed_documents``/``embed_query`` calls, zero writes, zero
    ``schedule_upsert`` calls. This is the mode every diagnostic caller
    (``./arailctl doctor``) must use — a health check must never be the
    thing that builds, or pays for, the thing it is checking. Before this
    parameter existed, ``doctor`` calling the ``build=True`` default wrote
    a brand-new index (one full embedding pass) into any World whose table
    didn't exist yet, which also meant a diagnostic command could become a
    corpus-egress path under ``LAB_MODE=hybrid``.
    """
    global _pkb_root_cache

    from arail.vector_index import available

    root = pkb_root or _pkb_root_from_env()
    root_key = root.resolve()

    with _lock:
        if build:
            # The one-shot guard applies ONLY to a build=True pass, and
            # only build=True ever claims it. A read-only (build=False)
            # call always re-runs its (cheap, idempotent) inspection —
            # it must never be able to consume the one-shot slot a later
            # genuine build depends on.
            if root_key in _initialized_roots:
                return
            _initialized_roots.add(root_key)
            # REVIEW4.md ASK-1: this assignment must stay inside the
            # `if build:` guard. _pkb_root_cache is the single process-wide
            # write target _flush()/schedule_upsert() resolve against
            # (joined with _pending's root-RELATIVE paths) — a read-only
            # call redirecting it would be worse than a no-op: it would
            # make a later flush write World A's pending paths under World
            # B's root. build=False never reads _pkb_root_cache either
            # (it only ever uses its own local `root`), so it has no
            # reason to write it.
            _pkb_root_cache = root

    if not available():
        _log.warning("pkb_index: LanceDB not available; ensure_ready is a no-op")
        return

    if not root.exists():
        _log.warning("pkb_index: pkb_root %s does not exist; skipping ensure_ready", root)
        return

    import lancedb  # type: ignore[import-not-found]

    db_path = _vector_db_path(root)
    if build:
        db_path.mkdir(parents=True, exist_ok=True)
    elif not db_path.exists():
        # Read-only mode against a World with no index at all yet: nothing
        # to connect to, nothing to build. Report "empty" rather than
        # creating the directory just to open a connection into it.
        set_degraded(
            "empty",
            "pkb_pages index does not exist yet for this World — run "
            "`./arailctl pkb reembed` (or start the portal) to build it.")
        return
    try:
        db = lancedb.connect(str(db_path))
    except Exception as e:
        _log.warning("pkb_index: cannot connect to LanceDB in ensure_ready: %s", e)
        return

    table = _open_table(db, "pkb_pages")

    if table is None:
        if not build:
            set_degraded(
                "empty",
                "pkb_pages index does not exist yet for this World — run "
                "`./arailctl pkb reembed` (or start the portal) to build it.")
            return
        _log.info("pkb_index: pkb_pages table missing — building index")
        _index_all_reporting_embedding_errors(root, "ensure_ready: table missing")
        return

    missing_columns, dim_mismatch = _schema_column_status(table)

    if dim_mismatch and not missing_columns:
        reason = (
            "pkb_pages index was built with a different embedding "
            "dimension than the current spec declares — run "
            "`./arailctl pkb reembed` to upgrade. Existing rows are "
            "untouched."
        )
        _log.warning("pkb_index: %s", reason)
        set_degraded("dimension", reason)
        try:
            from arail.activity import activity_log
            activity_log.emit("pkb", reason, "warn")
        except Exception:
            pass
        return

    if missing_columns:
        if not build:
            set_degraded(
                "dimension",
                "pkb_pages index schema is missing required columns — run "
                "`./arailctl pkb reembed` (or start the portal) to rebuild it.")
            return
        _log.info(
            "pkb_index: pkb_pages schema missing required columns "
            "— dropping and rebuilding (schema upgrade)"
        )
        try:
            from arail.activity import activity_log
            activity_log.emit("pkb", "Rebuilding KB index for schema upgrade", "info")
        except Exception:
            pass
        try:
            db.drop_table("pkb_pages")
        except Exception:
            pass
        _index_all_reporting_embedding_errors(root, "ensure_ready: schema drop-and-rebuild")
        return

    # Schema/dimension are fine — clear that code (this check IS evidence
    # about it) and delegate the provenance check to the same function
    # pkb._semantic_search calls on every search, so the two enforcement
    # points can never drift apart (BLOCK-1's root cause was exactly two
    # divergent copies — really, one real copy and a missing one). Reading
    # the sidecar is not a write, so this runs the same way regardless of
    # ``build``.
    clear_degraded("dimension")
    ok, _reason = _check_provenance(table, db_path)
    if not ok:
        return

    if not build:
        # Read-only mode stops here: the staleness sweep below SCHEDULES
        # upserts (a later debounced write, eventually an embed call) --
        # exactly the mutation build=False promises not to cause.
        return

    # Table exists, schema and provenance both match — run the bounded
    # staleness sweep.
    _staleness_sweep(root, table, db)


def _staleness_sweep(root: Path, table, db) -> None:
    """Compare on-disk file mtimes to table row mtimes; schedule upserts for stale files."""
    from arail import pkb as pkb_mod

    # Load the current table rows into a dict: rel_posix -> mtime
    try:
        rows = table.to_pandas()[["path", "mtime"]].to_dict("records")
        indexed_mtimes: dict[str, float] = {r["path"]: float(r["mtime"]) for r in rows}
    except Exception as e:
        _log.warning("pkb_index: staleness sweep could not read table: %s", e)
        return

    indexed_paths = set(indexed_mtimes.keys())
    on_disk_paths: set[str] = set()
    stale_count = 0
    cap_exceeded = False

    for abs_path, _text in pkb_mod._iter_pkb_files(root):
        try:
            rel_posix = abs_path.relative_to(root).as_posix()
        except ValueError:
            continue
        on_disk_paths.add(rel_posix)
        disk_mtime = abs_path.stat().st_mtime
        table_mtime = indexed_mtimes.get(rel_posix)
        if table_mtime is None or disk_mtime > table_mtime:
            stale_count += 1
            if stale_count > _STALENESS_CAP:
                cap_exceeded = True
                break
            with _lock:
                _pending.add(rel_posix)

    if cap_exceeded:
        _log.info(
            "pkb_index: staleness sweep found >%d stale files; triggering full index_all",
            _STALENESS_CAP,
        )
        try:
            from arail.activity import activity_log
            activity_log.emit("pkb", "KB staleness sweep: too many stale files, rebuilding index", "info")
        except Exception:
            pass
        with _lock:
            _pending.clear()
        _index_all_reporting_embedding_errors(root, "staleness sweep: cap exceeded")
        return

    # Delete rows for files that no longer exist on disk.
    for rel_posix in indexed_paths - on_disk_paths:
        with _lock:
            _pending.add(rel_posix)

    if stale_count > 0 or (indexed_paths - on_disk_paths):
        _log.info(
            "pkb_index: staleness sweep found %d stale/new files and %d deleted",
            stale_count, len(indexed_paths - on_disk_paths),
        )
        # Arm the timer to flush any stale paths queued above.
        with _lock:
            if _pending:
                _arm_timer()


def schedule_upsert(path: Path, *, pkb_root: Path | None = None) -> None:
    """Enqueue a path for incremental upsert. Never raises; never blocks writes.

    The path must be inside pkb_root. Paths outside are silently rejected.
    The actual LanceDB write happens after the debounce window expires.
    """
    global _pkb_root_cache
    from arail.vector_index import available
    if not available():
        return

    # Never embed staged World bundle-machinery (agenda/drift/roster/spec/
    # terms.json) — the world's content is indexed as per-term pages instead.
    try:
        from arail.world_mount import is_world_machinery_path
        if is_world_machinery_path(path):
            return
    except Exception:  # noqa: BLE001
        pass

    root = pkb_root or _pkb_root_cache or _pkb_root_from_env()

    # Lazily initialize if ensure_ready was never called.
    with _lock:
        if _pkb_root_cache is None:
            _pkb_root_cache = root

    try:
        # Resolve both paths to canonical absolute paths before comparing,
        # so symlinks and ".." components can't escape pkb_root.
        resolved_path = path.resolve()
        resolved_root = root.resolve()
        rel_posix = resolved_path.relative_to(resolved_root).as_posix()
    except ValueError:
        _log.warning(
            "pkb_index: schedule_upsert rejected path outside pkb_root: %s", path
        )
        return

    with _lock:
        _pending.add(rel_posix)
        _arm_timer()


def _reset_for_tests() -> None:
    """Reset all module-level state. Call from test fixtures only."""
    global _pending, _timer, _pkb_root_cache
    with _lock:
        if _timer is not None:
            _timer.cancel()
            _timer = None
        _pending.clear()
        _initialized_roots.clear()
        _pkb_root_cache = None
    clear_degraded()
