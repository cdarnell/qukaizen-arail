"""Unit tests for arail.dbspec.ensure — tests 1-12 of
sprints/2026-08-10-arail2-persistence-instantiated/ARCHITECTURE.md §7.

No callers wired yet (recommended implementation order step 1): this is the
whole risk surface of the "seamless" DB path, so it lands proven first.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

from arail.dbspec.ensure import ensure_db, classify_migration, MIGRATION_NAME_RE

REPO_SPEC_DIR = Path(__file__).resolve().parents[1] / "spec"


def _snapshot(path: Path) -> set:
    if not path.exists():
        return set()
    return {
        (str(p.relative_to(path)), p.stat().st_size if p.is_file() else None)
        for p in path.rglob("*")
    }


# ── 1. apply=False on empty dir is write-free ──────────────────────────────

def test_apply_false_on_empty_dir_is_pending_and_write_free(tmp_path: Path):
    d = tmp_path / "data"
    d.mkdir()
    before = _snapshot(d)
    report = ensure_db(d, apply=False, spec_dir=REPO_SPEC_DIR)
    after = _snapshot(d)

    assert report.present is False
    assert after == before, "apply=False must perform zero writes"
    assert report.state in ("pending", "unavailable")


# ── 2. apply=True on empty dir creates the db ───────────────────────────────

def test_apply_true_on_empty_dir_creates_db(tmp_path: Path):
    d = tmp_path / "data"
    d.mkdir()
    report = ensure_db(d, apply=True, spec_dir=REPO_SPEC_DIR)

    assert report.state == "created"
    db_path = d / "arail.db"
    assert db_path.exists()

    conn = sqlite3.connect(db_path)
    try:
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver == report.version
        row = conn.execute(
            "SELECT version, spec_sha256 FROM schema_version "
            "ORDER BY version DESC LIMIT 1").fetchone()
        assert row is not None
        assert row[1] == report.spec_sha256
    finally:
        conn.close()


# ── 3. idempotence ──────────────────────────────────────────────────────────

def test_apply_true_idempotent(tmp_path: Path):
    d = tmp_path / "data"
    d.mkdir()
    ensure_db(d, apply=True, spec_dir=REPO_SPEC_DIR)
    second = ensure_db(d, apply=True, spec_dir=REPO_SPEC_DIR)

    assert second.state == "ok"
    assert second.applied == []


# ── 5. lossy classifier, table-driven ──────────────────────────────────────
# REVIEW.md BLOCK-1: the classifier is now an ALLOWLIST, not a denylist.
# This table includes the four verified-executable bypasses the review
# found against the old denylist, plus DROP VIEW/TRIGGER, plus a
# deliberately unparseable/unrecognized statement — every one of them
# must be LOSSY, because the allowlist's default for anything it doesn't
# recognize is LOSSY (fail closed), not SAFE-FORWARD.

@pytest.mark.parametrize("sql,expected", [
    ("DROP TABLE x;", "LOSSY"),
    ("ALTER TABLE x DROP COLUMN y;", "LOSSY"),
    ("DELETE FROM worlds;", "LOSSY"),
    ("UPDATE worlds SET status = 'x';", "LOSSY"),
    ("CREATE TABLE new_x (id text); INSERT INTO new_x SELECT * FROM x; "
     "DROP TABLE x; ALTER TABLE new_x RENAME TO x;", "LOSSY"),
    ("CREATE TABLE x (id text);", "SAFE-FORWARD"),
    ("CREATE INDEX idx_x ON x (id);", "SAFE-FORWARD"),
    ("ALTER TABLE x ADD COLUMN y text;", "SAFE-FORWARD"),
    # REVIEW.md BLOCK-1's four verified-executable bypasses of the OLD
    # denylist — every one must be LOSSY under the allowlist.
    ("ALTER TABLE worlds DROP slug;", "LOSSY"),               # no COLUMN keyword — the idiomatic short form
    ("UPDATE OR REPLACE worlds SET status=0;", "LOSSY"),
    ("REPLACE INTO worlds VALUES (1);", "LOSSY"),
    ("INSERT OR REPLACE INTO worlds VALUES (1);", "LOSSY"),
    # Plus the review's other named adversarial cases.
    ("DROP VIEW v1;", "LOSSY"),
    ("DROP TRIGGER t1;", "LOSSY"),
    # REVIEW2.md BLOCK-4: bare INSERT INTO was removed from the allowlist
    # entirely (not guarded) after SQLite's upsert suffix was found to
    # turn it into a row-rewriting statement while the "INSERT INTO"
    # prefix still matches — verified executable, an existing row's
    # value silently overwritten. Both forms below, and plain INSERT
    # INTO on its own, are all now LOSSY by construction (the keyword
    # simply isn't in the allowlist), not by guessing every dangerous
    # suffix.
    ("INSERT INTO worlds VALUES (1);", "LOSSY"),
    ("INSERT INTO worlds VALUES ('w1','WIPED') "
     "ON CONFLICT(id) DO UPDATE SET status='WIPED';", "LOSSY"),
    ("INSERT INTO worlds (id) VALUES (1)\n"
     "ON CONFLICT DO UPDATE SET status = 2;", "LOSSY"),
    # REVIEW2.md BLOCK-4: CREATE TRIGGER was also removed from the
    # allowlist — a trigger's BEGIN...END body can contain arbitrary DML
    # not bounded by the leading "CREATE TRIGGER" keyword at all. Before
    # this fix it classified LOSSY only by accident (the naive `;` split
    # fragmenting the body into pieces that happened not to parse); now
    # it is LOSSY by construction, for a stated reason, regardless of
    # what the trigger body contains or how it happens to split.
    ("CREATE TRIGGER t1 AFTER INSERT ON worlds "
     "BEGIN DELETE FROM entities; END;", "LOSSY"),
    # A statement the allowlist does not recognize at all — fails closed
    # by default, not by pattern-matching a known-bad form.
    ("PRAGMA foreign_keys = OFF;", "LOSSY"),
    ("VACUUM;", "LOSSY"),
    ("this is not valid sql at all", "LOSSY"),
    # A leading comment (Atlas's own generated migrations use this on
    # nearly every statement — a normal, expected, non-adversarial form)
    # is stripped before the allowlist match, so a genuinely safe
    # commented statement classifies correctly...
    ("-- Create table x\nCREATE TABLE x (id text);", "SAFE-FORWARD"),
    # ...but a comment cannot be used to smuggle danger past the
    # allowlist: the DROP TABLE text inside a comment never executes as
    # SQL, so this remains correctly SAFE-FORWARD (there is no destructive
    # SQL here to have missed) — a precise allowlist match, not a false
    # positive, and not a bypass either.
    ("-- DROP TABLE x\nCREATE TABLE y (id text);", "SAFE-FORWARD"),
    # A lossy keyword inside a STRING LITERAL default value is likewise
    # not executable SQL — genuinely safe, correctly SAFE-FORWARD.
    ("CREATE TABLE y (id text, note text DEFAULT 'DROP TABLE x');", "SAFE-FORWARD"),
])
def test_lossy_classifier_table_driven(sql, expected):
    assert classify_migration(sql) == expected


def test_real_baseline_migration_classifies_safe_forward():
    """The actual committed baseline — every statement is a comment-
    prefixed CREATE TABLE/CREATE [UNIQUE] INDEX, Atlas's own generated
    form — must still classify SAFE-FORWARD under the new allowlist, or
    nothing would ever auto-apply."""
    from arail.dbspec.ensure import classify_migration
    text = (REPO_SPEC_DIR / "schema" / "migrations"
           / "20260808155711_baseline.sql").read_text()
    assert classify_migration(text) == "SAFE-FORWARD"


# ── 6. ahead ────────────────────────────────────────────────────────────────

def test_user_version_ahead_of_ledger(tmp_path: Path):
    d = tmp_path / "data"
    d.mkdir()
    ensure_db(d, apply=True, spec_dir=REPO_SPEC_DIR)
    conn = sqlite3.connect(d / "arail.db")
    conn.execute("PRAGMA user_version = 999")
    conn.close()

    before = _snapshot(d)
    report = ensure_db(d, apply=False, spec_dir=REPO_SPEC_DIR)
    after = _snapshot(d)

    assert report.state == "ahead"
    assert after == before


def test_user_version_ahead_apply_true_makes_no_writes(tmp_path: Path):
    d = tmp_path / "data"
    d.mkdir()
    ensure_db(d, apply=True, spec_dir=REPO_SPEC_DIR)
    conn = sqlite3.connect(d / "arail.db")
    conn.execute("PRAGMA user_version = 999")
    conn.close()

    report = ensure_db(d, apply=True, spec_dir=REPO_SPEC_DIR)
    assert report.state == "ahead"
    assert report.applied == []


# ── 7. diverged ─────────────────────────────────────────────────────────────

def test_diverged_migration_file_mutated(tmp_path: Path):
    d = tmp_path / "data"
    d.mkdir()
    spec_copy = tmp_path / "spec"
    _copy_spec(spec_copy)
    ensure_db(d, apply=True, spec_dir=spec_copy)

    mig = next((spec_copy / "schema" / "migrations").glob("*.sql"))
    mig.write_text(mig.read_text() + "\n-- tampered\n")

    before = _snapshot(d)
    report = ensure_db(d, apply=False, spec_dir=spec_copy)
    after = _snapshot(d)

    assert report.state == "diverged"
    assert after == before


def _copy_spec(dest: Path) -> None:
    import shutil
    shutil.copytree(REPO_SPEC_DIR, dest)


# ── 8. failure isolation ────────────────────────────────────────────────────

def _append_atlas_sum_entry(migrations_dir: Path, path: Path) -> None:
    """Ledger verification (BLOCK-2) now runs before ANY migration in the
    directory is even considered, so a test that adds a synthetic
    migration file must also add its real entry to the copied atlas.sum —
    using ensure.py's OWN hash function, not a reimplementation, so this
    helper can never silently drift from what the module actually
    checks."""
    from arail.dbspec.ensure import _atlas_file_hash
    with open(migrations_dir / "atlas.sum", "a") as f:
        f.write(f"{path.name} {_atlas_file_hash(path)}\n")


def test_failure_isolation_bad_migration(tmp_path: Path):
    d = tmp_path / "data"
    d.mkdir()
    spec_copy = tmp_path / "spec"
    _copy_spec(spec_copy)
    migrations_dir = spec_copy / "schema" / "migrations"

    # Migration 2: passes classification (a real "CREATE TABLE" leading
    # keyword — allowlisted) but is malformed SQL that sqlite3 rejects at
    # EXECUTION time — this is what test 8/F2 (per-file transaction
    # rollback on an execution error) actually needs, distinct from
    # BLOCK-1's classification gate.
    broken = migrations_dir / "20260808155712_broken.sql"
    broken.write_text("CREATE TABLE this_is_not_valid_sql (;")
    _append_atlas_sum_entry(migrations_dir, broken)
    # Migration 3: would otherwise be fine.
    third = migrations_dir / "20260808155713_third.sql"
    third.write_text("CREATE TABLE third_table (id text);")
    _append_atlas_sum_entry(migrations_dir, third)

    report = ensure_db(d, apply=True, spec_dir=spec_copy)

    assert report.state == "blocked"
    assert report.applied == ["20260808155711_baseline.sql"]
    conn = sqlite3.connect(d / "arail.db")
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        # migration 2's DDL never landed.
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "third_table" not in tables
    finally:
        conn.close()


# ── 9. no atlas/lancedb/embed imported ──────────────────────────────────────

def test_ensure_does_not_import_atlas_lancedb_or_embed(tmp_path: Path):
    d = tmp_path / "data"
    d.mkdir()
    for mod in ("atlas", "lancedb", "arail.dbspec.atlas", "arail.dbspec.embed"):
        sys.modules.pop(mod, None)

    ensure_db(d, apply=True, spec_dir=REPO_SPEC_DIR)

    assert "lancedb" not in sys.modules
    assert "arail.dbspec.atlas" not in sys.modules
    assert "arail.dbspec.embed" not in sys.modules


# ── 10. unwritable data_dir ─────────────────────────────────────────────────

@pytest.mark.skipif(os.name == "nt", reason="posix permissions only")
def test_unwritable_data_dir_blocked(tmp_path: Path):
    d = tmp_path / "data"
    d.mkdir()
    d.chmod(0o500)
    try:
        report = ensure_db(d, apply=True, spec_dir=REPO_SPEC_DIR)
    finally:
        d.chmod(0o700)

    assert report.state == "blocked"
    assert report.action


# ── 11. truncated/garbage arail.db ─────────────────────────────────────────

def test_truncated_db_blocked_and_untouched(tmp_path: Path):
    d = tmp_path / "data"
    d.mkdir()
    db_path = d / "arail.db"
    db_path.write_bytes(b"not a sqlite file at all")
    before = db_path.read_bytes()

    report = ensure_db(d, apply=False, spec_dir=REPO_SPEC_DIR)

    assert report.state == "blocked"
    assert db_path.read_bytes() == before


def test_truncated_db_blocked_apply_true_never_deletes(tmp_path: Path):
    d = tmp_path / "data"
    d.mkdir()
    db_path = d / "arail.db"
    db_path.write_bytes(b"not a sqlite file at all")
    before = db_path.read_bytes()

    report = ensure_db(d, apply=True, spec_dir=REPO_SPEC_DIR)

    assert report.state == "blocked"
    assert db_path.exists()
    assert db_path.read_bytes() == before


# ── 12. concurrency ──────────────────────────────────────────────────────────

def test_concurrent_apply_both_finish_ok(tmp_path: Path):
    d = tmp_path / "data"
    d.mkdir()
    results = []

    def _run():
        results.append(ensure_db(d, apply=True, spec_dir=REPO_SPEC_DIR))

    t1 = threading.Thread(target=_run)
    t2 = threading.Thread(target=_run)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(results) == 2
    for r in results:
        assert r.state in ("created", "updated", "ok")

    conn = sqlite3.connect(d / "arail.db")
    try:
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver == 1
        conn.execute("SELECT 1 FROM schema_version").fetchone()
    finally:
        conn.close()


# ── misc contract checks ────────────────────────────────────────────────────

def test_migration_name_regex_rejects_traversal():
    assert MIGRATION_NAME_RE.match("20260808155711_baseline.sql")
    assert not MIGRATION_NAME_RE.match("../../etc/passwd.sql")
    assert not MIGRATION_NAME_RE.match("00_x.sql")
    assert not MIGRATION_NAME_RE.match("20260808155711_Baseline.sql")


def test_unavailable_when_no_migrations_dir(tmp_path: Path):
    d = tmp_path / "data"
    d.mkdir()
    empty_spec = tmp_path / "spec_empty"
    (empty_spec / "schema" / "migrations").mkdir(parents=True)
    report = ensure_db(d, apply=False, spec_dir=empty_spec)
    assert report.state == "unavailable"
    assert not (d / "arail.db").exists()


# ── BLOCK-2: ledger verification against atlas.sum, before execution ──────

def test_atlas_sum_hash_matches_atlas_own_algorithm():
    """The hash function itself: b64(sha256(filename_bytes + content_bytes)),
    verified byte-for-byte against the real committed atlas.sum — this is
    the exact reproduction the review demanded, pinned as a regression
    test so it can never silently drift."""
    from arail.dbspec.ensure import _atlas_file_hash
    mig = REPO_SPEC_DIR / "schema" / "migrations" / "20260808155711_baseline.sql"
    sums = (REPO_SPEC_DIR / "schema" / "migrations" / "atlas.sum").read_text().splitlines()
    recorded = dict(line.split(" ", 1) for line in sums[1:] if line.strip())
    assert _atlas_file_hash(mig) == recorded[mig.name]


def test_fresh_clone_verifies_ledger_before_any_execution(tmp_path: Path):
    """BLOCK-2's core claim: on a fresh clone (no sidecar has ever existed),
    ensure_db(apply=True) must verify the migration against atlas.sum
    BEFORE executing it — not "having verified nothing at all." Tampering
    the migration file before its very FIRST apply (no sidecar, no prior
    history) must still block it."""
    d = tmp_path / "data"
    d.mkdir()
    spec_copy = tmp_path / "spec"
    _copy_spec(spec_copy)
    mig = next((spec_copy / "schema" / "migrations").glob("*.sql"))
    mig.write_text(mig.read_text() + "\n-- tampered before first apply\n")

    report = ensure_db(d, apply=True, spec_dir=spec_copy)

    assert report.state == "diverged"
    assert report.applied == []
    assert not (d / "arail.db").exists()


def test_migration_absent_from_ledger_is_diverged(tmp_path: Path):
    """A migration file that exists on disk but was never added to
    atlas.sum — cannot be proven to match what was committed — blocks
    the whole apply, not just that one file."""
    d = tmp_path / "data"
    d.mkdir()
    spec_copy = tmp_path / "spec"
    _copy_spec(spec_copy)
    migrations_dir = spec_copy / "schema" / "migrations"
    (migrations_dir / "20260808155712_unlisted.sql").write_text(
        "CREATE TABLE unlisted (id text);")
    # deliberately NOT added to atlas.sum

    report = ensure_db(d, apply=True, spec_dir=spec_copy)

    assert report.state == "diverged"
    assert "20260808155712_unlisted.sql" in report.detail
    assert report.applied == []


def test_sidecar_deletion_no_longer_defeats_divergence_detection(tmp_path: Path):
    """The exact bypass the review reproduced live: deleting
    .arail_ensure_state.json used to silently turn "diverged" back into
    "ok", because the sidecar was the ONLY integrity check. Ledger
    verification against atlas.sum now catches it independently of the
    sidecar's presence."""
    d = tmp_path / "data"
    d.mkdir()
    spec_copy = tmp_path / "spec"
    _copy_spec(spec_copy)
    ensure_db(d, apply=True, spec_dir=spec_copy)

    mig = next((spec_copy / "schema" / "migrations").glob("*.sql"))
    mig.write_text(mig.read_text() + "\n-- tampered\n")

    sidecar = d / ".arail_ensure_state.json"
    assert sidecar.exists()
    sidecar.unlink()  # the exact bypass from REVIEW.md BLOCK-2

    report = ensure_db(d, apply=False, spec_dir=spec_copy)

    assert report.state == "diverged"


def test_ledger_verification_runs_even_on_apply_false(tmp_path: Path):
    """status/doctor (apply=False) must catch a tampered ledger too, not
    only start (apply=True) — reported as early as possible."""
    d = tmp_path / "data"
    d.mkdir()
    spec_copy = tmp_path / "spec"
    _copy_spec(spec_copy)
    mig = next((spec_copy / "schema" / "migrations").glob("*.sql"))
    mig.write_text(mig.read_text() + "\n-- tampered\n")

    before = _snapshot(d)
    report = ensure_db(d, apply=False, spec_dir=spec_copy)
    after = _snapshot(d)

    assert report.state == "diverged"
    assert after == before  # still write-free


def test_missing_atlas_sum_blocks_everything(tmp_path: Path):
    d = tmp_path / "data"
    d.mkdir()
    spec_copy = tmp_path / "spec"
    _copy_spec(spec_copy)
    (spec_copy / "schema" / "migrations" / "atlas.sum").unlink()

    report = ensure_db(d, apply=True, spec_dir=spec_copy)

    assert report.state == "diverged"
    assert report.applied == []
    assert not (d / "arail.db").exists()


# ── ASK-1: spec_dir resolves from package location, not CWD ────────────────

def test_default_spec_dir_is_package_relative_not_cwd(tmp_path: Path, monkeypatch):
    """A caller with the wrong CWD must still find the real spec tree —
    this used to silently return "unavailable" instead of the true state."""
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.chdir(tmp_path)  # nowhere near the real repo

    report = ensure_db(d, apply=False)  # no spec_dir passed — uses the default

    assert report.state != "unavailable"
    assert report.state in ("pending", "ok")
