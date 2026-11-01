"""Round 4: the header-byte read that fixed QA-1 introduced a new wedge.

QA-1 is genuinely fixed — ``_read_user_version_readonly`` no longer opens
SQLite at all, so ``apply=False`` is write-free on every SQLite version and a
healthy database in a read-only directory no longer reports ``blocked``. That
half is verified in ``test_qa_ensure_write_free.py``, which now passes on both
interpreters.

What the fix changed as a side effect is the treatment of a database file that
exists but has no valid header yet. The old code asked SQLite, and SQLite
treats a **zero-length file as a valid, empty database** (``PRAGMA
user_version`` returns 0). The new code validates the 100-byte header itself
and raises ``DatabaseError`` — which ``ensure_db`` turns into ``blocked``, a
DEGRADING state, on both the read path *and* the apply path.

Two consequences, both measured:

  * **QA-12a (persistent wedge).** A zero-byte ``arail.db`` — what any crash,
    kill, full disk, or laptop-sleep between file creation and the first
    commit leaves behind — is now ``blocked`` forever. ``install`` and
    ``start`` both refuse, ``doctor`` cannot repair it (this module never
    deletes a database, correctly), and no documented verb clears it. On the
    round-3 code the same file healed: ``pending`` -> ``updated``.

  * **QA-12b (concurrency regression).** ``ensure_db`` reads the version
    *before* taking ``_apply_lock``, so a second process reading while the
    first has created the file but not yet written its header now gets
    ``blocked`` instead of "version 0". Measured over 20 runs of the
    8-process race in ``test_qa_ensure_concurrency.py``:
    **round-3 code 0/20 failures, round-4 code 3/20** — with the process
    exiting 3 and reporting "does not look like a SQLite database file" for a
    database that is entirely fine. F17 promises "both end ok, no corruption".

The distinction that matters, and that these tests encode: a **non-empty**
file whose header is not SQLite's is a genuinely corrupt database and must
stay ``blocked`` (F18, "never auto-delete"). An **empty** one is not corrupt —
it is what SQLite itself calls a new database.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from arail.dbspec.ensure import DEFAULT_SPEC_DIR, ensure_db

pytestmark = pytest.mark.skipif(
    not (DEFAULT_SPEC_DIR / "schema" / "migrations").is_dir(),
    reason="no spec/schema/migrations in this checkout",
)


def _data(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


# ── QA-12a: the zero-byte wedge ─────────────────────────────────────────

def test_a_zero_byte_database_is_not_a_permanent_wedge(tmp_path):
    """FINDING QA-12a (MEDIUM, round-4 regression).

    SQLite's own view of a zero-length file is "a valid empty database"
    (verified in the same test), so ARAIL must be able to bring it up rather
    than refuse forever. Asserted against the CORRECT behaviour.
    """
    d = _data(tmp_path)
    (d / "arail.db").touch()

    conn = sqlite3.connect(str(d / "arail.db"))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    conn.close()

    ro = ensure_db(d, apply=False)
    assert ro.state != "blocked", (
        "a zero-byte arail.db reports %r — status/doctor degrade a lab to "
        "exit 3 over an empty file: %s" % (ro.state, ro.detail))

    rw = ensure_db(d, apply=True)
    assert rw.state in ("created", "updated", "ok"), (
        "install/start cannot recover a zero-byte arail.db (%r: %s); no "
        "documented verb deletes it, so the lab is wedged" % (rw.state, rw.detail))


def test_a_truncated_but_nonempty_header_is_still_blocked(tmp_path):
    """The other side of the line, pinned so a fix for the above cannot
    turn a genuinely corrupt file into something we silently overwrite.
    F18: report it, never auto-delete it."""
    d = _data(tmp_path)
    (d / "arail.db").write_bytes(b"not a database at all, but definitely not empty")
    rep = ensure_db(d, apply=False)
    assert rep.state == "blocked", rep
    assert (d / "arail.db").exists(), "a corrupt database was removed"


def test_a_short_but_valid_magic_prefix_is_blocked_not_misread(tmp_path):
    """A file that starts with the SQLite magic but is shorter than the
    100-byte header must not be read as version 0 by a sloppier fix."""
    d = _data(tmp_path)
    (d / "arail.db").write_bytes(b"SQLite format 3\x00" + b"\x00" * 20)
    rep = ensure_db(d, apply=False)
    assert rep.state == "blocked", rep


# ── QA-12b: the race the header read reopened ───────────────────────────

@pytest.mark.parametrize("attempt", range(6))
def test_a_concurrent_reader_never_sees_a_half_created_database(tmp_path,
                                                                attempt):
    """FINDING QA-12b (MEDIUM, round-4 regression).

    One process applying while another reads — exactly what `install`
    (looping six roots) plus a concurrently booting `start` produces. The
    reader must never report a degrading state for a database that is
    merely being born. Repeated, because the window is narrow: the
    8-process version of this in test_qa_ensure_concurrency.py reproduces
    3 times in 20 on this code and 0 times in 20 on round 3's.
    """
    d = _data(tmp_path)
    writer = subprocess.Popen(
        [sys.executable, "-m", "arail.dbspec.ensure", str(d),
         "--apply", "--json"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    states = [ensure_db(d, apply=False).state for _ in range(40)]
    writer.communicate(timeout=120)
    assert "blocked" not in states, (
        "a read-only check reported 'blocked' while a sibling process was "
        "creating the database: %s" % sorted(set(states)))


# ── The staleness question the builder was asked to justify ─────────────

def test_the_header_never_lies_after_a_clean_apply(tmp_path):
    """The invariant the fix's correctness rests on, pinned. ensure_db's
    finally: conn.close() checkpoints the WAL, so the header is truth."""
    d = _data(tmp_path)
    rep = ensure_db(d, apply=True)
    conn = sqlite3.connect(str(d / "arail.db"))
    truth = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert ensure_db(d, apply=False).version == truth == rep.version


def test_an_unflushed_wal_leaves_the_header_behind_but_self_heals(tmp_path):
    """FINDING QA-14 (LOW, documented-behaviour pin).

    A process killed after committing a user_version bump but before
    closing leaves the WAL uncheckpointed, and the header — the only thing
    the read path now looks at — keeps the OLD value. The module's
    docstring calls this "transient" and attributes it to a live concurrent
    writer; it is neither. With the writer long dead the stale value
    persists on disk until something opens the file with SQLite again.

    Bounded, which is why this is LOW and pinned rather than filed as a
    blocker: the *apply* path reads the PRAGMA through SQLite (not the
    header), so it always sees truth and its close() heals the header. The
    read path can only UNDER-report the version, which yields `pending`/`ok`
    — never a wrong write, never a skipped migration.
    """
    d = _data(tmp_path)
    ensure_db(d, apply=True)
    db = d / "arail.db"
    subprocess.run(
        [sys.executable, "-c",
         "import sqlite3, os\n"
         f"c = sqlite3.connect({str(db)!r})\n"
         "c.execute('PRAGMA journal_mode=WAL')\n"
         "c.execute('PRAGMA user_version=42')\n"
         "c.commit()\n"
         "os._exit(0)\n"],
        check=True)

    conn = sqlite3.connect(str(db))
    truth = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert truth == 42

    # Documented current behaviour: the read path may under-report. Whether
    # it does on any given run depends on when SQLite happened to
    # checkpoint, so this asserts the DIRECTION of the error (never ahead of
    # truth, i.e. never a skipped migration) rather than that it always
    # occurs — a QA test may not be a coin flip. A persistent stale read was
    # measured directly on both interpreters (header 7 vs truth 42) and is
    # recorded in TEST_REPORT.md.
    stale = ensure_db(d, apply=False)
    assert stale.version <= truth, (
        "the read path reported a version AHEAD of the database's true "
        "user_version — that direction could skip a migration")
    assert stale.state in ("ok", "pending", "ahead"), stale

    # …and the next apply=True sees truth and heals the header.
    healed = ensure_db(d, apply=True)
    assert healed.version == truth
    assert ensure_db(d, apply=False).version == truth
