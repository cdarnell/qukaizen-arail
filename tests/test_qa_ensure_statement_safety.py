"""QA target 1: the statement-splitting / classification surface.

sprints/2026-08-10-arail2-persistence-instantiated/REVIEW3.md's QA target
list, item 2:

    Fuzz from SQLite's grammar rather than a fixed table. The oracle that
    matters: seed a database, apply every migration the classifier calls
    SAFE-FORWARD, and assert row-for-row equality of the pre-existing
    data. Any statement that mutates a pre-existing row while classifying
    SAFE-FORWARD is a defect regardless of spelling. Both BLOCK-1 and
    BLOCK-4 were found this way; assume a third exists.

So this module does NOT assert a table of expected classifications (that
only ever re-checks the cases someone already thought of, which is how
BLOCK-1 and BLOCK-4 both survived their own unit tests). It builds
migration files by composing a grammar of SQLite statement forms with a
grammar of *lexical wrappers* (semicolons inside string literals, inside
line comments, inside quoted identifiers, inside block comments, leading
comments, case and whitespace mangling), runs each one through the real
``ensure_db(apply=True)`` against a real seeded database, and asks the
only question that matters:

    did any pre-existing row change?

The classification is then merely the *explanation*: a mutation is a
defect if the classifier said SAFE-FORWARD, and a non-mutation is fine
whatever it said (the classifier is allowed, by contract, to fail closed
and refuse safe SQL).
"""

from __future__ import annotations

import base64
import hashlib
import random
import sqlite3
from pathlib import Path

import pytest

from arail.dbspec.ensure import classify_migration, ensure_db

# ── Fixture plumbing ────────────────────────────────────────────────────

SEED_SQL = """
CREATE TABLE keep (id INTEGER PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE other (id INTEGER PRIMARY KEY, w TEXT);
CREATE INDEX keep_v_idx ON keep (v);
CREATE VIEW keep_view AS SELECT id, v FROM keep;
"""

SEED_ROWS = [(1, "alpha"), (2, "beta"), (3, "gamma"), (4, "delta")]


def _atlas_hash(name: str, content: bytes) -> str:
    return "h1:" + base64.standard_b64encode(
        hashlib.sha256(name.encode("utf-8") + content).digest()
    ).decode("ascii")


def _write_spec(spec_dir: Path, migrations: list) -> None:
    """Write a synthetic, correctly-ledgered migration directory.

    ``migrations`` is [(filename, sql_text), ...]. atlas.sum is generated
    with Atlas's real per-file digest so ``_verify_ledger`` passes — this
    fixture is about the *classifier*, so the ledger gate must not be the
    thing that stops a candidate (a test that passes because an unrelated
    gate fired proves nothing about the gate under test).
    """
    mdir = spec_dir / "schema" / "migrations"
    mdir.mkdir(parents=True, exist_ok=True)
    lines = ["h1:qa-synthetic-directory-hash="]
    for name, sql in migrations:
        content = sql.encode("utf-8")
        (mdir / name).write_bytes(content)
        lines.append(f"{name} {_atlas_hash(name, content)}")
    (mdir / "atlas.sum").write_text("\n".join(lines) + "\n")


def _tables(conn) -> list:
    return [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def _shape(db_path: Path) -> dict:
    """{table: [column, ...]} for the user tables that exist right now."""
    conn = sqlite3.connect(str(db_path))
    try:
        return {
            t: [r[1] for r in conn.execute(f'PRAGMA table_info("{t}")')]
            for t in _tables(conn)
        }
    finally:
        conn.close()


def _snapshot(db_path: Path, shape: dict) -> dict:
    """Every row of every table that existed in ``shape``, projected onto
    the columns that existed then. THIS is the oracle: "did a row the user
    already had change?"

    Deliberately blind to *additive* change — a new table, or a new column
    on an existing table — because that is exactly what a SAFE-FORWARD
    migration is allowed to do. It is not blind to a dropped or renamed
    table (that table simply stops being readable, which the sentinel
    below records), to a dropped or renamed column, or to any change in a
    pre-existing cell.
    """
    if not db_path.exists():
        return {t: "<db missing>" for t in shape}
    conn = sqlite3.connect(str(db_path))
    try:
        out = {}
        for t, cols in shape.items():
            collist = ", ".join(f'"{c}"' for c in cols)
            try:
                out[t] = sorted(map(repr, conn.execute(f'SELECT {collist} FROM "{t}"')))
            except sqlite3.Error as exc:
                out[t] = f"<gone: {exc}>"
        return out
    finally:
        conn.close()


def _seeded_lab(tmp_path: Path, name: str):
    """(data_dir, spec_dir) with migration 001 applied and SEED_ROWS in
    place — i.e. exactly the state a real user's lab is in when the next
    migration lands."""
    data_dir = tmp_path / name / "data"
    spec_dir = tmp_path / name / "spec"
    data_dir.mkdir(parents=True)
    _write_spec(spec_dir, [("20260101000000_seed.sql", SEED_SQL)])
    rep = ensure_db(data_dir, apply=True, spec_dir=spec_dir)
    assert rep.state == "created", rep
    db_path = Path(rep.db_path)
    conn = sqlite3.connect(str(db_path))
    with conn:
        conn.executemany("INSERT INTO keep (id, v) VALUES (?, ?)", SEED_ROWS)
        conn.executemany("INSERT INTO other (id, w) VALUES (?, ?)", SEED_ROWS)
    conn.close()
    return data_dir, spec_dir, db_path


# ── The grammar ─────────────────────────────────────────────────────────
#
# Statement forms drawn from SQLite's grammar, deliberately including
# every historical bypass plus forms nobody has classified yet. The
# comment on each is what it would DO if executed, not what we expect the
# classifier to say — the oracle judges effects.

DESTRUCTIVE_FORMS = [
    "DELETE FROM keep",
    "DELETE FROM keep WHERE id = 1",
    "UPDATE keep SET v = 'zzz'",
    "UPDATE OR REPLACE keep SET v = 'zzz'",
    "UPDATE keep SET v = 'zzz' WHERE id IN (SELECT id FROM other)",
    "DROP TABLE keep",
    "DROP TABLE IF EXISTS keep",
    "DROP VIEW keep_view",
    "DROP INDEX keep_v_idx",
    "ALTER TABLE keep DROP v",                       # BLOCK-1 bypass #1
    "ALTER TABLE keep DROP COLUMN v",
    "REPLACE INTO keep (id, v) VALUES (1, 'zzz')",   # BLOCK-1 bypass #2
    "INSERT OR REPLACE INTO keep (id, v) VALUES (1, 'zzz')",
    "INSERT INTO keep (id, v) VALUES (1, 'zzz') "
    "ON CONFLICT(id) DO UPDATE SET v = 'zzz'",       # BLOCK-4 bypass
    "INSERT INTO keep (id, v) SELECT id, 'zzz' FROM other WHERE 0",
    "ALTER TABLE keep RENAME TO keep_old",
    "ALTER TABLE keep RENAME COLUMN v TO vv",
    "ALTER TABLE keep RENAME v TO vv",
    "CREATE TRIGGER t_del AFTER INSERT ON other BEGIN DELETE FROM keep; END",
    "CREATE TEMP TRIGGER t_upd AFTER INSERT ON other "
    "BEGIN UPDATE keep SET v = 'zzz'; END",
    "PRAGMA writable_schema = 1",
    "VACUUM",
    "REINDEX",
    "ANALYZE",
    "ATTACH DATABASE ':memory:' AS side",
    "CREATE TEMP TABLE tmp_keep AS SELECT * FROM keep",
    "CREATE VIRTUAL TABLE fts_keep USING fts5(v, content='keep')",
    "WITH d AS (SELECT id FROM keep) DELETE FROM keep WHERE id IN (SELECT id FROM d)",
]

BENIGN_FORMS = [
    "CREATE TABLE fresh (id INTEGER PRIMARY KEY, z TEXT)",
    "CREATE TABLE IF NOT EXISTS fresh2 (id INTEGER)",
    "CREATE INDEX other_w_idx ON other (w)",
    "CREATE UNIQUE INDEX other_id_idx ON other (id)",
    "CREATE VIEW v2 AS SELECT id FROM keep",
    "ALTER TABLE keep ADD COLUMN extra TEXT",
    "ALTER TABLE keep ADD COLUMN extra2 TEXT NOT NULL DEFAULT 'd'",
    "CREATE TABLE derived AS SELECT id, v FROM keep",
]

# Lexical wrappers — the actual subject. Each takes a statement and
# returns a full migration file body. These are where a naive `;` split
# can disagree with SQLite's own tokenizer.
WRAPPERS = {
    "bare": lambda s: f"{s};\n",
    "no_trailing_semicolon": lambda s: f"{s}\n",
    "leading_line_comment": lambda s: f"-- migration note\n{s};\n",
    "leading_block_comment": lambda s: f"/* note */ {s};\n",
    "semicolon_in_leading_comment": lambda s: f"-- note; with a semicolon\n{s};\n",
    "semicolon_in_block_comment": lambda s: f"/* a; b; c */\n{s};\n",
    "trailing_line_comment": lambda s: f"{s}; -- trailing; note\n",
    "after_safe_ddl": lambda s: f"CREATE TABLE pre_{abs(hash(s)) % 997} (q INTEGER);\n{s};\n",
    "before_safe_ddl": lambda s: f"{s};\nCREATE TABLE post_{abs(hash(s)) % 997} (q INTEGER);\n",
    "string_with_semicolon_nearby": lambda s: (
        "CREATE TABLE strtab (q TEXT DEFAULT 'a;b');\n" + f"{s};\n"
    ),
    "quoted_identifier_with_semicolon": lambda s: (
        'CREATE TABLE "odd;name" (q INTEGER);\n' + f"{s};\n"
    ),
    "blank_lines_and_tabs": lambda s: f"\n\n\t{s}\t;\n\n",
    "lowercased": lambda s: f"{s.lower()};\n",
    "collapsed_whitespace": lambda s: " ".join(f"{s};".split()) + "\n",
    "extra_semicolons": lambda s: f";;\n{s};;\n",
}


def _candidates():
    """(label, sql_text, expected_to_be_destructive) for every
    form x wrapper pair, plus randomized multi-statement compositions."""
    for form in DESTRUCTIVE_FORMS:
        for wname, w in WRAPPERS.items():
            yield f"destructive/{wname}/{form[:40]}", w(form), True
    for form in BENIGN_FORMS:
        for wname, w in WRAPPERS.items():
            yield f"benign/{wname}/{form[:40]}", w(form), False
    # Randomized compositions: N statements from the union, shuffled,
    # each independently wrapped. Deterministic seeds — no time-of-day
    # flakiness, and a failure is reproducible from its label alone.
    for seed in range(60):
        rnd = random.Random(seed)
        n = rnd.randint(2, 4)
        picks = [
            rnd.choice(DESTRUCTIVE_FORMS + BENIGN_FORMS + BENIGN_FORMS)
            for _ in range(n)
        ]
        body = "".join(rnd.choice(list(WRAPPERS.values()))(p) for p in picks)
        yield f"composed/seed{seed}", body, any(p in DESTRUCTIVE_FORMS for p in picks)


ALL_CANDIDATES = list(_candidates())


def test_the_fuzz_corpus_is_actually_large_and_covers_both_polarities():
    """A guard on the guard: if a refactor collapses the generator, the
    oracle test below would silently pass on an empty corpus."""
    assert len(ALL_CANDIDATES) > 500, len(ALL_CANDIDATES)
    assert any(c[2] for c in ALL_CANDIDATES)
    assert any(not c[2] for c in ALL_CANDIDATES)


@pytest.mark.parametrize("label,sql,_destructive", ALL_CANDIDATES,
                         ids=[c[0] for c in ALL_CANDIDATES])
def test_safe_forward_never_mutates_a_preexisting_row(tmp_path, label, sql,
                                                      _destructive):
    """THE ORACLE (REVIEW3 QA target 2).

    Seed a database with rows, hand ``ensure_db(apply=True)`` a migration
    containing the candidate SQL, and assert row-for-row equality of every
    pre-existing table afterwards *whenever the classifier said
    SAFE-FORWARD*. A statement that classifies SAFE-FORWARD and changes a
    row is a data-loss defect regardless of how it is spelled.

    The converse is deliberately NOT asserted: the classifier is
    contractually allowed to fail closed, so "LOSSY but actually
    harmless" is a false positive we accept.
    """
    safe = tmp_path.name  # unique per parametrization via tmp_path
    data_dir, spec_dir, db_path = _seeded_lab(tmp_path, "lab")
    shape = _shape(db_path)
    before = _snapshot(db_path, shape)
    assert before["keep"], "fixture did not seed rows"

    _write_spec(
        spec_dir,
        [("20260101000000_seed.sql", SEED_SQL),
         ("20260102000000_candidate.sql", sql)],
    )
    verdict = classify_migration(sql)
    report = ensure_db(data_dir, apply=True, spec_dir=spec_dir)
    after = _snapshot(db_path, shape)

    if verdict == "SAFE-FORWARD":
        assert after == before, (
            f"DATA-LOSS DEFECT: candidate {label!r} classified SAFE-FORWARD "
            f"and changed pre-existing rows.\nstate={report.state} "
            f"applied={report.applied}\nSQL:\n{sql}\n"
            f"before={before}\nafter={after}"
        )
    else:
        # LOSSY: never applied at all, so the data must be untouched for
        # the stronger reason that nothing ran.
        assert after == before, (
            f"LOSSY candidate {label!r} was applied anyway — "
            f"state={report.state} applied={report.applied}\nSQL:\n{sql}"
        )
        assert "20260102000000_candidate.sql" not in report.applied
    assert safe  # keep the linter honest about tmp_path uniqueness


@pytest.mark.parametrize("stmt", DESTRUCTIVE_FORMS)
def test_every_known_destructive_form_classifies_lossy_in_isolation(stmt):
    """Regression floor: the four verified BLOCK-1 bypasses, the BLOCK-4
    upsert, and every other data-touching form we know of, each on its
    own. This is the "known-fixed stays fixed" half; the fuzz above is
    the "find the third one" half."""
    assert classify_migration(stmt + ";") == "LOSSY", stmt


def test_lossy_statement_anywhere_blocks_the_whole_file(tmp_path):
    """One LOSSY statement makes the file LOSSY even when surrounded by
    allowlisted DDL — the position of the bad statement must not matter."""
    for pos in range(3):
        stmts = ["CREATE TABLE a%d (x INTEGER)" % i for i in range(3)]
        stmts[pos] = "DELETE FROM keep"
        assert classify_migration(";\n".join(stmts) + ";") == "LOSSY", pos


def test_a_lossy_migration_stops_the_run_and_leaves_later_ones_unapplied(tmp_path):
    """F3 + ordering: migration 2 is LOSSY, migration 3 is SAFE. The run
    must stop at 2 — applying 3 over a skipped 2 would produce a schema
    the ledger never describes."""
    data_dir, spec_dir, db_path = _seeded_lab(tmp_path, "lab")
    _write_spec(spec_dir, [
        ("20260101000000_seed.sql", SEED_SQL),
        ("20260102000000_lossy.sql", "DELETE FROM keep;"),
        ("20260103000000_safe.sql", "CREATE TABLE later (x INTEGER);"),
    ])
    report = ensure_db(data_dir, apply=True, spec_dir=spec_dir)
    assert report.applied == []
    assert report.state == "blocked"
    assert report.version == 1
    assert "later" not in _shape(db_path)
    assert _snapshot(db_path, _shape(db_path))["keep"], (
        "rows were deleted by a LOSSY migration")


def test_commented_out_allowlisted_ddl_is_still_executed(tmp_path):
    """FINDING (documented, low severity): ``_split_statements`` splits on
    ``;`` with no lexer, so a semicolon *inside a line comment* ends the
    comment as far as the splitter is concerned. Everything after it on
    that line becomes a standalone statement — and if it happens to be
    allowlisted DDL, it is classified SAFE-FORWARD and EXECUTED, even
    though SQLite's own tokenizer would treat it as comment text.

    This fails *closed* for anything destructive (the fragment stops
    matching the allowlist, so the whole file is LOSSY), which is why it
    is not a data-loss defect. It is still a surprise: a migration author
    who comments out a CREATE TABLE on the same line as another
    semicolon gets it created anyway. Pinned here so the behaviour is a
    decision rather than an accident.
    """
    data_dir, spec_dir, db_path = _seeded_lab(tmp_path, "lab")
    sql = "CREATE TABLE real_one (x INTEGER);\n-- disabled; CREATE TABLE ghost (y INTEGER)\n"
    assert classify_migration(sql) == "SAFE-FORWARD"
    _write_spec(spec_dir, [("20260101000000_seed.sql", SEED_SQL),
                           ("20260102000000_c.sql", sql)])
    ensure_db(data_dir, apply=True, spec_dir=spec_dir)
    tables = set(_shape(db_path))
    assert "real_one" in tables
    assert "ghost" in tables, (
        "behaviour changed — if the splitter became comment-aware this "
        "assertion should be inverted, not deleted"
    )


def test_add_column_with_on_delete_cascade_installs_a_future_cascade(tmp_path):
    """REVIEW3's recorded residual, pinned as a test so it cannot silently
    get worse. The cascade is second-order (it only fires on a later
    DELETE, itself LOSSY and never auto-applied), so the assertion is
    exactly that: SAFE-FORWARD, and *no existing row changes now*."""
    data_dir, spec_dir, db_path = _seeded_lab(tmp_path, "lab")
    sql = ("ALTER TABLE keep ADD COLUMN parent INTEGER "
           "REFERENCES other(id) ON DELETE CASCADE;")
    assert classify_migration(sql) == "SAFE-FORWARD"
    shape = _shape(db_path)
    before = _snapshot(db_path, shape)["keep"]
    _write_spec(spec_dir, [("20260101000000_seed.sql", SEED_SQL),
                           ("20260102000000_c.sql", sql)])
    ensure_db(data_dir, apply=True, spec_dir=spec_dir)
    after = _snapshot(db_path, shape)["keep"]
    assert after == before


# ── Ledger tampering (REVIEW3 QA target 7) ──────────────────────────────

def test_tampered_migration_yields_diverged_and_never_creates_the_db(tmp_path):
    data_dir = tmp_path / "data"
    spec_dir = tmp_path / "spec"
    data_dir.mkdir()
    _write_spec(spec_dir, [("20260101000000_seed.sql", SEED_SQL)])
    mfile = spec_dir / "schema" / "migrations" / "20260101000000_seed.sql"
    mfile.write_text(SEED_SQL + "\nCREATE TABLE sneaky (x INTEGER);\n")
    report = ensure_db(data_dir, apply=True, spec_dir=spec_dir)
    assert report.state == "diverged", report
    assert not (data_dir / "arail.db").exists()


def test_missing_atlas_sum_yields_diverged_and_never_creates_the_db(tmp_path):
    data_dir = tmp_path / "data"
    spec_dir = tmp_path / "spec"
    data_dir.mkdir()
    _write_spec(spec_dir, [("20260101000000_seed.sql", SEED_SQL)])
    (spec_dir / "schema" / "migrations" / "atlas.sum").unlink()
    report = ensure_db(data_dir, apply=True, spec_dir=spec_dir)
    assert report.state == "diverged", report
    assert not (data_dir / "arail.db").exists()


def test_corrupt_atlas_sum_yields_diverged(tmp_path):
    data_dir = tmp_path / "data"
    spec_dir = tmp_path / "spec"
    data_dir.mkdir()
    _write_spec(spec_dir, [("20260101000000_seed.sql", SEED_SQL)])
    (spec_dir / "schema" / "migrations" / "atlas.sum").write_text(
        "h1:x=\nthis line has no hash field at all and cannot be parsed\n"
    )
    report = ensure_db(data_dir, apply=True, spec_dir=spec_dir)
    assert report.state == "diverged", report
    assert not (data_dir / "arail.db").exists()


def test_unlisted_extra_migration_yields_diverged(tmp_path):
    """An attacker (or a bad merge) drops a new .sql into the migrations
    dir without touching atlas.sum. It must not execute."""
    data_dir = tmp_path / "data"
    spec_dir = tmp_path / "spec"
    data_dir.mkdir()
    _write_spec(spec_dir, [("20260101000000_seed.sql", SEED_SQL)])
    (spec_dir / "schema" / "migrations" / "20260102000000_extra.sql").write_text(
        "CREATE TABLE injected (x INTEGER);"
    )
    report = ensure_db(data_dir, apply=True, spec_dir=spec_dir)
    assert report.state == "diverged", report
    assert not (data_dir / "arail.db").exists()


def test_migration_name_gate_ignores_ineligible_filenames(tmp_path):
    """Test 36: only ``^\\d{14}_[a-z0-9_]+\\.sql$`` is eligible. A file
    with a traversal-ish or off-pattern name is never read *or* executed
    — and, importantly, its absence from atlas.sum must not make the
    whole ledger diverge either (it is not a migration at all)."""
    data_dir = tmp_path / "data"
    spec_dir = tmp_path / "spec"
    data_dir.mkdir()
    _write_spec(spec_dir, [("20260101000000_seed.sql", SEED_SQL)])
    mdir = spec_dir / "schema" / "migrations"
    for bad in ("00_x.sql", "20260102000000_Bad.sql", "20260102000000_x.SQL",
                "..%2f..%2fetc%2fpasswd.sql", "2026010200000_x.sql"):
        (mdir / bad).write_text("DROP TABLE keep;")
    report = ensure_db(data_dir, apply=True, spec_dir=spec_dir)
    assert report.state == "created", report
    assert report.applied == ["20260101000000_seed.sql"]


def test_deleting_a_committed_migration_is_not_detected(tmp_path):
    """FINDING (QA-3, medium): ``_verify_ledger`` verifies every file
    *present on disk* against atlas.sum, but never checks the converse —
    that every filename atlas.sum lists still exists. Delete migration 1
    of 2 and the ledger still verifies; migration 2 then applies as
    ``user_version = 1``, so the cursor now means something different
    from what it means in an intact checkout.

    Asserted as the current (defective) behaviour so the report has an
    executable repro. When the completeness check lands, this test should
    flip to asserting ``state == "diverged"``.
    """
    data_dir = tmp_path / "data"
    spec_dir = tmp_path / "spec"
    data_dir.mkdir()
    _write_spec(spec_dir, [
        ("20260101000000_one.sql", "CREATE TABLE one (x INTEGER);"),
        ("20260102000000_two.sql", "CREATE TABLE two (x INTEGER);"),
    ])
    # atlas.sum keeps listing both; only the file goes away.
    (spec_dir / "schema" / "migrations" / "20260101000000_one.sql").unlink()
    report = ensure_db(data_dir, apply=True, spec_dir=spec_dir)
    assert report.state == "created"
    assert report.version == 1
    snap = _shape(Path(report.db_path))
    assert "two" in snap and "one" not in snap, (
        "a migration nobody deleted from the ledger was skipped silently"
    )


def test_status_calls_a_pending_run_safe_when_a_later_file_is_lossy(tmp_path):
    """FINDING (QA-4, low): ``apply=False`` classifies only
    ``pending_files[0]`` but reports *every* pending filename under
    ``state="pending"`` with the detail "safe-forward migration(s) not yet
    applied" and the action "./arailctl start, or ./arailctl install".
    When file 2 is safe and file 3 is lossy, that advice is wrong:
    running install will stop at 3 and report blocked. status promises
    something start cannot deliver."""
    data_dir = tmp_path / "data"
    spec_dir = tmp_path / "spec"
    data_dir.mkdir()
    _write_spec(spec_dir, [
        ("20260101000000_one.sql", "CREATE TABLE one (x INTEGER);"),
        ("20260102000000_two.sql", "DROP TABLE one;"),
    ])
    ro = ensure_db(data_dir, apply=False, spec_dir=spec_dir)
    assert ro.state == "pending"
    assert ro.pending == ["20260101000000_one.sql", "20260102000000_two.sql"]
    assert "safe-forward" in ro.detail
    rw = ensure_db(data_dir, apply=True, spec_dir=spec_dir)
    assert rw.state == "blocked", (
        "status said 'pending, run install'; install actually ends blocked"
    )
