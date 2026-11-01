"""Atlas-free, codegen-free replay of the committed migration ledger.

``./arailctl db apply`` (see ``arail.dbspec.cli``/``atlas``) is a developer
tool: it shells out to the ``atlas`` binary, generates a new migration file
into ``spec/schema/migrations/``, lints it, and regenerates code into the
source tree. A user who clones the repo and runs ``./arailctl setup`` has
none of that installed and must never need it (Assumption 1,
sprints/2026-08-10-arail2-persistence-instantiated/ARCHITECTURE.md).

This module is the seamless path instead: it replays the already-committed,
already-lint-gated migration files in ``spec/schema/migrations/`` into a
per-data-dir SQLite file, using ``PRAGMA user_version`` as the applied-
migration cursor (Assumption 4 — invisible to ``atlas schema diff``, so it
adds no drift against ``spec/schema/schema.hcl``). It never authors a
migration, never invokes ``atlas``, never runs codegen, and never touches
Lance reconciliation or the 1.x->2.0 import — those stay behind their own
explicit verbs (§4.2 of ARCHITECTURE.md).

The safe/lossy line (§4.2) — ALLOWLIST, not a denylist (REVIEW.md BLOCK-1)
---------------------------------------------------------------------------
A denylist cannot fail closed: "fail closed" means "anything I cannot
prove safe is LOSSY," and a denylist's default is the opposite — anything
it doesn't happen to name is (wrongly) SAFE-FORWARD. An earlier version of
this module was exactly that (six regex patterns) and had four verified
executable bypasses: ``ALTER TABLE t DROP c`` (no ``COLUMN`` keyword —
the idiomatic short form, and the single most likely destructive
migration anyone actually writes), ``REPLACE INTO``, ``INSERT OR REPLACE
INTO``, ``UPDATE OR REPLACE``, plus ``DROP VIEW``/``DROP TRIGGER``. All
four are real, data-destroying SQLite that the denylist waved through.

The classifier now works the other way. Every statement in a migration
file (split via ``_split_statements``) is checked against an explicit
ALLOWLIST of leading keywords — ``CREATE TABLE``, ``CREATE [UNIQUE]
INDEX``, ``CREATE VIEW``, and ``ALTER TABLE … ADD COLUMN`` — and
classified SAFE-FORWARD only if it matches. **Everything else, including
anything this classifier does not recognize at all, is LOSSY.** A
statement prefixed by a comment (so the allowlist regex doesn't match at
position 0) is also LOSSY — a false positive is acceptable; a false
negative is not, and this now actually holds (test 5 exercises the four
round-1 verified bypasses plus additional adversarial cases, all
correctly LOSSY). A migration file is SAFE-FORWARD only if every one of
its statements is; one non-allowlisted statement anywhere makes the
whole file LOSSY.

**Round-2 review (BLOCK-4): a leading keyword does not bound what a
statement DOES.** ``INSERT INTO`` and ``CREATE TRIGGER`` were both
removed from the allowlist for that reason — see the constant
``_ALLOWLIST_RE``'s own comment for the specific verified/structural
danger each one carried and why dropping (not guarding) was chosen.
Anyone re-adding a keyword to this allowlist in the future must ask the
same question BLOCK-4 asked: is this statement's *entire* effect on
existing data bounded by its prefix, or can something after the prefix
(a suffix clause, a body, a subquery) change what it does to rows that
already exist? Pure schema DDL (``CREATE TABLE``/``CREATE INDEX``/
``CREATE VIEW``/``ALTER TABLE … ADD COLUMN``) always is — none of them
have a form that rewrites or deletes an existing row's data. ``INSERT``
and ``CREATE TRIGGER`` are not, and stay out.

Ledger verification (§4.2) — REQUIRED before executing anything (BLOCK-2)
---------------------------------------------------------------------------
Before any migration's SQL is executed (``apply=True``) — and reported
by ``apply=False`` too, so ``status``/``doctor`` catch it as early as
``start`` would — every committed migration file in
``spec/schema/migrations/`` is verified against ``spec/schema/migrations/
atlas.sum``'s own per-file hash. Atlas's digest is **not** undocumented
or binary-only, contrary to an earlier draft of this module's reasoning:
it is ``base64(sha256(filename_bytes + file_content_bytes))``, reproduced
here in pure Python with no ``atlas`` binary (verified byte-for-byte
against the real ``atlas.sum`` in this repo). A file missing from the
ledger, or whose hash disagrees with what's recorded, makes the whole
call ``state="diverged"`` and executes zero statements — this is the
actual precondition for auto-executing SQL at boot, not a follow-up.

This is a *second*, complementary check to the sidecar below, not a
replacement for it: ``atlas.sum`` only proves a file matches what was
committed to *this checkout*; it says nothing about whether the file
this DB already applied is the same bytes it applied last time (a
tampered-then-reverted file, or a DB moved between checkouts at
different commits, could match today's ``atlas.sum`` while having
silently changed what got executed against this specific database).

Post-apply divergence (the sidecar)
-------------------------------------
The first time this module applies a migration file, it records a plain
sha256 of that file's bytes in a JSON sidecar next to the database
(``<data_dir>/.arail_ensure_state.json`` — the same pattern the
vector-index provenance sidecar already uses elsewhere in this codebase).
Every later call re-hashes the file and compares against what it
recorded. A mismatch is DIVERGED. This catches "someone edited an
already-applied migration file" — a fact about *this database's own
history* that ``atlas.sum`` alone cannot express, since ``atlas.sum``
only ever describes the current state of the checkout, not what a given
database has already executed.

Write discipline (contract, §4.1)
----------------------------------
``apply=False`` performs **zero writes**: no file creation, no directory
creation, no PRAGMA writes, no ``sqlite3.connect(path)`` in the mode that
would create a missing file. ``status`` and ``doctor`` use only this mode.
This is the exact bug a previous sprint shipped in a read-only check; do
not reintroduce it (see the module test ``test_apply_false_is_write_free``
in ``tests/test_dbspec_ensure.py``, which snapshots the directory tree
before/after).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from arail.dbspec import db as dbmod

try:
    import fcntl  # posix only
except ImportError:  # pragma: no cover — non-posix
    fcntl = None

# F17: two processes/threads calling ensure_db(apply=True) on the same
# data_dir concurrently must both finish with a valid, correctly-versioned
# database — "last writer wins at the same version, both end ok, no
# corruption" (ARCHITECTURE.md F17). A thread-level lock keyed by data_dir
# handles the in-process case (concurrent Worlds are one-process-per-World,
# so this is the realistic case); an flock on a sidecar lock file extends
# the same guarantee across processes on POSIX, best-effort elsewhere.
_thread_locks: dict = {}
_thread_locks_guard = threading.Lock()


def _thread_lock_for(data_dir: Path) -> threading.Lock:
    key = str(data_dir)
    with _thread_locks_guard:
        lock = _thread_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _thread_locks[key] = lock
        return lock


@contextlib.contextmanager
def _apply_lock(data_dir: Path):
    data_dir.mkdir(parents=True, exist_ok=True)
    with _thread_lock_for(data_dir):
        if fcntl is None:
            yield
            return
        lock_path = data_dir / ".arail_ensure.lock"
        with open(lock_path, "a+") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

__all__ = ["EnsureReport", "ensure_db", "classify_migration", "classify_statement",
          "MIGRATION_NAME_RE", "DEFAULT_SPEC_DIR"]

SCHEMA = "arail.db-ensure/v1"

# Test 36: only files matching this pattern are ever eligible to be read as
# migrations — a path-traversal-ish name (``../../etc/passwd.sql``,
# ``00_x.sql``) is silently ignored, not executed.
MIGRATION_NAME_RE = re.compile(r"^\d{14}_[a-z0-9_]+\.sql$")

_SIDECAR_NAME = ".arail_ensure_state.json"
_ATLAS_SUM_NAME = "atlas.sum"

# ASK-1: resolved from the installed package location, not CWD — a caller
# with the wrong working directory (doctor.check_provisioning passes
# repo_root=os.getcwd()) must not get a silent, non-degrading
# "unavailable" on a perfectly healthy database. Every current shell
# caller happens to `cd "$REPO_ROOT"` first, which made this latent
# rather than live, but "latent" is not "safe."
DEFAULT_SPEC_DIR = Path(__file__).resolve().parents[3] / "spec"

# BLOCK-1: an ALLOWLIST, not a denylist — see the module docstring for why
# a denylist cannot fail closed. A statement is SAFE-FORWARD only if its
# leading keywords match one of these; anything else, including anything
# unrecognized, is LOSSY.
#
# BLOCK-4 (round-2 review): allowlisting a leading keyword does not bound
# what the statement DOES — the prefix says nothing about a destructive
# suffix or body. Two keywords were removed from this allowlist for
# exactly that reason, neither missed by the committed baseline (checked:
# `grep -c 'INSERT\|TRIGGER' spec/schema/migrations/*.sql` == 0 in both
# cases):
#
#   INSERT INTO  — SQLite's upsert suffix (`... ON CONFLICT DO UPDATE
#                  SET ...`) turns a bare INSERT into a row-rewriting
#                  statement while the "INSERT INTO" prefix still
#                  matches; verified executable (an existing row's value
#                  silently overwritten). No negative guard was chosen
#                  over dropping the keyword — a guard against
#                  ON CONFLICT/RETURNING/etc. only protects against the
#                  suffixes named today, the exact "denylist" failure
#                  mode BLOCK-1 already replaced once. Schema migrations
#                  don't need data DML; a seeding migration can go
#                  through the non-seamless `./arailctl db apply` path
#                  like any other change this module refuses to
#                  auto-apply.
#   CREATE TRIGGER — a trigger's BODY (inside BEGIN...END) can contain
#                  arbitrary DML (DELETE, UPDATE, ...), and that body is
#                  not bounded by the leading "CREATE TRIGGER" keyword
#                  at all. It happened to classify LOSSY before this
#                  fix, but only by accident: `_split_statements`'s
#                  naive `;`-split fragments a trigger body at its own
#                  internal semicolons, and the resulting fragments
#                  usually (not always, and not by any actual rule)
#                  fail to parse as valid standalone SQL. Safety resting
#                  on a tokenization accident is not safety — removed
#                  rather than "fixed to still happen to work."
_ALLOWLIST_RE = re.compile(
    r"^(CREATE\s+TABLE\b"
    r"|CREATE\s+(UNIQUE\s+)?INDEX\b"
    r"|CREATE\s+VIEW\b"
    r"|ALTER\s+TABLE\s+\S+\s+ADD\s+COLUMN\b)",
    re.IGNORECASE,
)

# Atlas's own generated migrations prefix nearly every statement with an
# explanatory `-- comment` line (see spec/schema/migrations/*.sql) — a
# normal, expected, non-adversarial form, not obfuscation. Stripping ONLY
# unambiguous leading SQL comment forms (line comments to end-of-line,
# block comments) before the allowlist match lets real committed
# migrations classify correctly without reopening BLOCK-1: SQL comments
# cannot be "escaped" early (a `--` line comment always runs to the next
# newline; a `/* */` block comment always runs to its own close), so
# there is no way to smuggle executable SQL into what this strips.
_LEADING_COMMENT_RE = re.compile(r"^(\s*(--[^\n]*(\n|$)|/\*.*?\*/))+", re.DOTALL)


def _strip_leading_comments(stmt: str) -> str:
    return _LEADING_COMMENT_RE.sub("", stmt).strip()


@dataclass(frozen=True)
class EnsureReport:
    schema: str
    data_dir: str
    db_path: str
    present: bool
    applied: list
    pending: list
    version: int
    spec_version: int
    spec_sha256: str
    state: str
    detail: str
    action: str


def classify_statement(stmt: str) -> str:
    """"SAFE-FORWARD" or "LOSSY" for ONE statement (already stripped of the
    trailing ``;`` by ``_split_statements``). Leading SQL comments are
    stripped first (see ``_strip_leading_comments`` — a normal, non-
    adversarial form Atlas's own generated migrations use on nearly every
    statement); what remains is allowlist-matched. A statement that is
    ALL comment (nothing left after stripping) is SAFE-FORWARD — there is
    no executable SQL in it, so nothing to be unsafe about. Anything else
    that doesn't match the allowlist — including a statement this
    classifier does not recognize at all — is LOSSY. Fails closed: a
    false positive (safe SQL called LOSSY) is acceptable, a false
    negative (destructive SQL called SAFE-FORWARD) is not (test 5)."""
    remainder = _strip_leading_comments(stmt)
    if not remainder:
        return "SAFE-FORWARD"
    if _ALLOWLIST_RE.match(remainder):
        return "SAFE-FORWARD"
    return "LOSSY"


def classify_migration(sql_text: str) -> str:
    """"SAFE-FORWARD" iff EVERY statement in the file is; one LOSSY
    statement anywhere makes the whole migration LOSSY. A file with no
    statements at all (blank/comments-only) is SAFE-FORWARD — there is
    nothing to execute, so nothing to be unsafe about."""
    statements = _split_statements(sql_text)
    if not statements:
        return "SAFE-FORWARD"
    for stmt in statements:
        if classify_statement(stmt) == "LOSSY":
            return "LOSSY"
    return "SAFE-FORWARD"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _migration_files(migrations_dir: Path) -> list:
    if not migrations_dir.is_dir():
        return []
    return sorted(
        p for p in migrations_dir.iterdir()
        if p.is_file() and MIGRATION_NAME_RE.match(p.name)
    )


def _split_statements(sql_text: str) -> list:
    parts = [s.strip() for s in sql_text.split(";")]
    return [s for s in parts if s]


def _sidecar_path(data_dir: Path) -> Path:
    return data_dir / _SIDECAR_NAME


def _load_sidecar(data_dir: Path) -> dict:
    path = _sidecar_path(data_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001 — a corrupt sidecar is not fatal
        return {}


def _save_sidecar(data_dir: Path, state: dict) -> None:
    path = _sidecar_path(data_dir)
    path.write_text(json.dumps(state, indent=2, sort_keys=True))


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atlas_file_hash(path: Path) -> str:
    """Atlas's OWN per-file hash (REVIEW.md BLOCK-2): ``h1:`` +
    base64(sha256(filename_bytes + content_bytes)). Reproduced in pure
    Python, no ``atlas`` binary — verified byte-for-byte against this
    repo's real ``atlas.sum``. Contrary to an earlier draft of this
    module, this is not undocumented or binary-only."""
    digest = hashlib.sha256(path.name.encode("utf-8") + path.read_bytes()).digest()
    return "h1:" + base64.standard_b64encode(digest).decode("ascii")


def _parse_atlas_sum(migrations_dir: Path):
    """dict[filename -> "h1:..."] from atlas.sum's per-file lines, or None
    if the file is missing or malformed — either of which means "cannot
    verify," which _verify_ledger treats as a hard no, not an assumption
    of safety."""
    path = migrations_dir / _ATLAS_SUM_NAME
    if not path.is_file():
        return None
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    if not lines:
        return None
    entries: dict = {}
    # First line is the ledger's own overall directory hash — not verified
    # here (this module only needs per-file provenance to decide what's
    # safe to execute); every remaining line is "<filename> h1:<hash>".
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.rsplit(" ", 1)
        if len(parts) != 2:
            return None
        name, h = parts
        entries[name] = h
    return entries


def _verify_ledger(migrations_dir: Path, files) -> tuple:
    """BLOCK-2: verify every file in `files` against atlas.sum's committed
    per-file hash BEFORE any of their SQL is ever executed. Returns
    (ok, detail). ok=False on: atlas.sum missing/unparseable, a file
    absent from the ledger, or a hash mismatch — any of which means we
    cannot prove the file on disk is what was committed, so nothing gets
    auto-applied. This is a precondition for auto-execution, not a
    follow-up: the sidecar (see below) only ever proves a file matches
    what THIS database already applied — it verifies nothing on first
    apply, which is exactly the fresh-clone case this sprint serves."""
    entries = _parse_atlas_sum(migrations_dir)
    if entries is None:
        return False, (f"no readable {migrations_dir / _ATLAS_SUM_NAME} — "
                       f"cannot verify the migration ledger")
    for f in files:
        expected = entries.get(f.name)
        if expected is None:
            return False, f"{f.name} is not listed in atlas.sum"
        actual = _atlas_file_hash(f)
        if actual != expected:
            return False, (f"{f.name} does not match atlas.sum "
                           f"(expected {expected}, got {actual})")
    return True, ""


_SQLITE_HEADER_MAGIC = b"SQLite format 3\x00"


def _read_user_version_readonly(db_path: Path) -> int:
    """QA-1 (round 4): the original implementation opened the database via
    ``sqlite3.connect("file:...?mode=ro", uri=True)``, which is NOT
    write-free on a WAL-journaled database (``dbmod.connect`` always sets
    ``journal_mode = WAL``) — verified in both directions by QA:

    * SQLite >= 3.53: a ``mode=ro`` open of a WAL database MATERIALIZES
      ``-wal``/``-shm`` sidecar files in ``data_dir`` — a write, on the
      path §4.1 contractually defines as write-free.
    * SQLite <= 3.51: the same open FAILS outright
      (``unable to open database file``) whenever the ``-shm`` isn't
      already present, because older SQLite cannot bring a WAL database
      up read-only without creating it — and that failure becomes
      ``state="blocked"``, which is a DEGRADING state, so a perfectly
      healthy database in a data dir the process merely can't write
      (read-only mount, tight restore permissions, a different SQLite
      version than wrote it) degrades a live lab's ``status`` to exit 3.

    Fix: read ``PRAGMA user_version``'s value directly from the SQLite
    file header — bytes 60-63, big-endian signed 32-bit integer, per the
    documented SQLite file format — via a plain ``open(path, "rb")``.
    This never invokes SQLite at all, so it cannot create a WAL sidecar,
    cannot fail to open a WAL file, and performs exactly one read() of at
    most 100 bytes. Verified equivalent to the PRAGMA's own value on a
    freshly-ensured database.

    Correctness depends on one invariant elsewhere in this module: every
    ``apply=True`` caller closes its connection before returning
    (``ensure_db``'s ``finally: conn.close()``), which triggers SQLite's
    normal WAL checkpoint-on-last-close and flushes the true
    ``user_version`` into this header before any later ``apply=False``
    call can read it. Do not remove that ``close()`` — this function
    would then read a stale, pre-checkpoint value from a concurrently
    open WAL database.

    An unclosed/uncheckpointed WAL after an abnormal exit (a killed
    writer, ``os._exit`` before ``close()``) can leave the header behind
    the database's true ``user_version`` — QA measured this directly
    (header showed an old value while the true value, read through
    SQLite, was ahead) and it is NOT transient in that case: with the
    writer dead, the stale header persists on disk until something opens
    the file with SQLite again (which is exactly what the next
    ``apply=True`` does — it reads the PRAGMA through SQLite, not this
    header, so it always sees the truth and its own ``close()`` heals the
    header for every reader after it). The consequence is bounded to
    UNDER-reporting: this function can return a version that is stale-low
    (yielding ``ok``/``pending`` a beat later than the truth), never a
    version ahead of the real one, so the apply path can only ever be
    slow to notice new committed work — it can never skip a migration or
    write from a wrong cursor. Do not "fix" this by reading through
    SQLite instead; that reopens QA-1.

    QA-12 (round 4 regression, fixed here): a ZERO-LENGTH file is not a
    corrupt database — it is exactly what SQLite itself calls a valid,
    new, empty database (``PRAGMA user_version`` reads 0 on one). The
    first version of this fix raised ``DatabaseError`` for ANY header
    that didn't fully validate, including a merely-empty file — which
    ``ensure_db`` turns into ``state="blocked"``, a state nothing in this
    module (correctly) ever auto-clears. That wedged a lab PERMANENTLY on
    exactly the file a crash, ``kill -9``, a full disk, or a laptop
    losing power between file creation and the first commit leaves
    behind — worse than the bug QA-1's fix replaced, because the old
    read-via-SQLite path treated a zero-length file as version 0 too and
    self-healed. A length-0 file is now treated as version 0, same as
    SQLite's own semantics; a NON-EMPTY file that isn't a valid SQLite
    header (bad magic, truncated mid-header) still raises and stays
    ``blocked`` — F18's "never auto-delete, report it" is unchanged for
    genuine corruption. This distinction is pinned by both halves in
    ``tests/test_qa_ensure_header_read.py``.
    """
    try:
        size = db_path.stat().st_size
    except OSError as exc:
        raise sqlite3.DatabaseError(f"cannot read {db_path}: {exc}") from exc
    if size == 0:
        return 0
    try:
        with open(db_path, "rb") as f:
            header = f.read(100)
    except OSError as exc:
        raise sqlite3.DatabaseError(f"cannot read {db_path}: {exc}") from exc
    if len(header) < 100 or not header.startswith(_SQLITE_HEADER_MAGIC):
        raise sqlite3.DatabaseError(
            f"{db_path} does not look like a SQLite database file")
    return int.from_bytes(header[60:64], byteorder="big", signed=True)


def _load_spec_meta(spec_dir: Path):
    """Returns (version, sha256) or (0, "") if the spec cannot be loaded.
    Never raises — a spec load failure becomes a state, not an exception
    (contract: ensure_db never raises to a caller)."""
    try:
        from arail.dbspec.spec import load_spec
        spec_obj = load_spec(spec_dir)
        return spec_obj.version, spec_obj.sha256
    except Exception:  # noqa: BLE001
        return 0, ""


def ensure_db(data_dir, *, apply: bool = False, spec_dir=None) -> EnsureReport:
    data_dir = Path(data_dir)
    # ASK-1: resolved from the package location by default, never CWD —
    # see DEFAULT_SPEC_DIR's own comment.
    spec_dir = Path(spec_dir) if spec_dir is not None else DEFAULT_SPEC_DIR
    migrations_dir = spec_dir / "schema" / "migrations"
    db_path = dbmod.database_path(data_dir)
    present = db_path.exists()

    spec_version, spec_sha256 = _load_spec_meta(spec_dir)

    def _report(**kw) -> EnsureReport:
        base = dict(
            schema=SCHEMA, data_dir=str(data_dir), db_path=str(db_path),
            present=present, applied=[], pending=[], version=0,
            spec_version=spec_version, spec_sha256=spec_sha256,
            state="unavailable", detail="", action="",
        )
        base.update(kw)
        return EnsureReport(**base)

    if not migrations_dir.is_dir():
        return _report(
            state="unavailable",
            detail=f"no migrations directory at {migrations_dir}",
        )

    migrations = _migration_files(migrations_dir)
    if not migrations:
        return _report(
            state="unavailable",
            detail=f"no eligible migration files in {migrations_dir}",
        )

    # BLOCK-2: verify EVERY committed migration against atlas.sum BEFORE
    # any of them is executed — reported here (apply=False, so status/
    # doctor catch it too), not just gated inside the apply=True loop.
    # A tampered or unlisted file makes the whole call diverged, zero
    # statements ever executed, regardless of apply=.
    ledger_ok, ledger_detail = _verify_ledger(migrations_dir, migrations)
    if not ledger_ok:
        return _report(
            state="diverged", detail=ledger_detail,
            action="./arailctl db plan",
        )

    # Read the current cursor without ever creating the file.
    if not db_path.exists():
        version = 0
    else:
        try:
            version = _read_user_version_readonly(db_path)
        except sqlite3.DatabaseError as exc:
            return _report(
                state="blocked",
                detail=f"cannot read {db_path}: {exc}",
                action="./arailctl doctor",
            )

    total = len(migrations)

    # AHEAD.
    if version > total:
        return _report(
            state="ahead", version=version,
            detail=(f"database is at schema v{version} but this checkout "
                    f"only knows {total} migration(s)"),
            action="update this checkout — this database was written by a "
                   "newer ARAIL",
        )

    # DIVERGED — check every migration this DB claims to have already
    # applied against the recorded sidecar hash, if we have one.
    sidecar = _load_sidecar(data_dir)
    for m in migrations[:version]:
        recorded = sidecar.get(m.name)
        if recorded is None:
            continue  # unknown provenance (e.g. applied by `db apply`) — not verifiable, not flagged
        if recorded != _file_hash(m):
            return _report(
                state="diverged", version=version,
                detail=f"{m.name} has changed since it was applied",
                action="./arailctl db plan",
            )

    pending_files = migrations[version:total]
    pending_names = [m.name for m in pending_files]

    if not apply:
        if not pending_files:
            return _report(state="ok" if present else "unavailable",
                            version=version, pending=[])
        first_class = classify_migration(pending_files[0].read_text())
        if first_class == "LOSSY":
            return _report(
                state="blocked", version=version, pending=pending_names,
                detail=f"{pending_files[0].name} contains statements that "
                       f"can remove or rewrite data",
                action="./arailctl db apply --allow-destructive",
            )
        return _report(state="pending", version=version, pending=pending_names,
                        detail="safe-forward migration(s) not yet applied",
                        action="./arailctl start, or ./arailctl install")

    # ---- apply=True ----
    try:
        with _apply_lock(data_dir):
            try:
                conn = dbmod.connect(data_dir, create=True)
            except dbmod.DatabaseError as exc:
                return _report(
                    state="blocked", version=version,
                    detail=str(exc), action="./arailctl doctor",
                )
            try:
                return _apply_locked(conn, data_dir, migrations, total, present,
                                     spec_version, spec_sha256, _report)
            finally:
                conn.close()
    except OSError as exc:
        # Unwritable data_dir (chmod 0500, etc): cannot even take the lock.
        return _report(
            state="blocked", version=version,
            detail=f"cannot write to {data_dir}: {exc}",
            action="./arailctl doctor",
        )


def _apply_locked(conn, data_dir, migrations, total, present,
                  spec_version, spec_sha256, _report) -> EnsureReport:
    # Re-read the cursor now that we hold the lock — this is the
    # authoritative value for this apply, immune to the TOCTOU race between
    # the earlier read-only pre-check and lock acquisition (F17).
    cur_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    sidecar = _load_sidecar(data_dir)
    applied: list = []
    for m in migrations[cur_version:total]:
        sql_text = m.read_text()
        if classify_migration(sql_text) == "LOSSY":
            break  # never auto-applied — stop, leave it (and everything
                   # after it) pending.
        statements = _split_statements(sql_text)
        next_version = cur_version + 1
        try:
            with dbmod.transaction(conn):
                for stmt in statements:
                    conn.execute(stmt)
                conn.execute(f"PRAGMA user_version = {next_version}")
        except sqlite3.Error as exc:
            # F2: per-file transaction rolled back; user_version stays
            # at the last fully-applied migration.
            return _report(
                state="blocked", version=cur_version, applied=applied,
                pending=[x.name for x in migrations[cur_version:total]],
                detail=f"{m.name} failed to apply: {exc}",
                action="./arailctl doctor",
            )
        sidecar[m.name] = _file_hash(m)
        _save_sidecar(data_dir, sidecar)
        applied.append(m.name)
        cur_version = next_version

    pending_now = [x.name for x in migrations[cur_version:total]]

    # ASK-5: recording the applied spec version is skipped, silently, when
    # the spec failed to load (spec_version == 0, _load_spec_meta's
    # failure sentinel) — a DB could otherwise reach state="created" with
    # an EMPTY schema_version table, which dbmod.applied_version() reads
    # as "never applied." Report it instead of succeeding quietly.
    record_version_skipped = False
    if applied:
        if spec_version:
            dbmod.record_version(conn, spec_version, spec_sha256, _now_iso())
        else:
            record_version_skipped = True

    if pending_now:
        # Stopped early because the next pending migration is LOSSY.
        state = "blocked"
        detail = (f"{migrations[cur_version].name} contains statements "
                  f"that can remove or rewrite data")
        action = "./arailctl db apply --allow-destructive"
    elif applied:
        state = "created" if not present else "updated"
        detail = ("schema applied, but the spec failed to load — "
                  "schema_version was not recorded" if record_version_skipped
                  else "")
        action = "./arailctl doctor" if record_version_skipped else ""
    else:
        state = "ok"
        detail = ""
        action = ""

    return _report(
        state=state, version=cur_version, applied=applied,
        pending=pending_now, detail=detail, action=action,
    )


def _report_line(report: "EnsureReport") -> str:
    """One human-readable line for a single root — the shape `install`'s
    per-root summary and `start`'s single line both use. Quiet boot
    (F10): the caller decides whether to print at all; this only decides
    the wording once it has decided to print."""
    if report.state in ("created", "updated"):
        verb = "created" if report.state == "created" else "applied"
        n = len(report.applied)
        plural = "" if n == 1 else "s"
        return (f"db: {verb} {n} migration{plural} at {report.db_path} "
                f"(schema v{report.version})")
    if report.state == "ok":
        return f"db: {report.db_path} — ok (schema v{report.version})"
    # blocked / ahead / diverged / unavailable
    detail = f" — {report.detail}" if report.detail else ""
    action = f" — run {report.action}" if report.action else ""
    return f"db: {report.db_path} — {report.state}{detail}{action}"


def main(argv=None) -> int:  # pragma: no cover — thin CLI shim, exercised
    # via scripts/install.sh and scripts/start.sh, not directly by pytest.
    """``python -m arail.dbspec.ensure <data_dir> [--apply]`` — the shell
    integration point for ``install``/``start``, so neither has to embed
    Python beyond a single ``python -m`` invocation (matches the existing
    ``python -m arail.compiled_kb bootstrap`` pattern in
    ``scripts/install.sh``). Exits 0 for ok/created/updated/pending/
    unavailable, and 3 for blocked/ahead/diverged, so a caller that cares
    can detect "did not come up clean" without parsing prose.

    REVIEW.md ASK-2: ``unavailable`` (no ``spec/schema/migrations`` at
    all — a stripped-down fork per ``BLUEPRINTS.md``, the feature simply
    doesn't apply here) is deliberately excluded from the degrading set,
    matching ``status.sh``'s identical exclusion (see
    ``scripts/status.sh``'s ``_DB_DEGRADING_STATES`` comment) — the two
    surfaces used to disagree about this exact state."""
    import argparse
    import sys as _sys

    ap = argparse.ArgumentParser(prog="python -m arail.dbspec.ensure")
    ap.add_argument("data_dir")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--spec-dir", default=None)
    ap.add_argument("--quiet-ok", action="store_true",
                    help="print nothing when state is 'ok' with nothing applied")
    ap.add_argument("--json", action="store_true",
                    help="print the EnsureReport as one JSON line instead "
                         "of the human summary — status/doctor's contract "
                         "with this CLI, never used by install/start")
    args = ap.parse_args(argv)

    report = ensure_db(args.data_dir, apply=args.apply, spec_dir=args.spec_dir)
    if args.json:
        import json as _json
        print(_json.dumps({
            "schema": report.schema, "data_dir": report.data_dir,
            "db_path": report.db_path, "present": report.present,
            "applied": report.applied, "pending": report.pending,
            "version": report.version, "spec_version": report.spec_version,
            "spec_sha256": report.spec_sha256, "state": report.state,
            "detail": report.detail, "action": report.action,
        }))
    elif not (args.quiet_ok and report.state == "ok" and not report.applied):
        print(_report_line(report))
    return 0 if report.state in ("ok", "created", "updated", "pending",
                                 "unavailable") else 3


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(main())
