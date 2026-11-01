"""QA-1: ``apply=False`` is not actually write-free on current SQLite.

Contract §4.1 of the sprint's ARCHITECTURE.md:

    ``apply=False`` performs **zero writes** — no file creation, no
    directory creation, no ``PRAGMA`` writes, no ``connect(create=True)``.
    ``status`` and ``doctor`` use only this mode. (A read-only check that
    creates the thing it is checking is the exact bug ``doctor`` hit in
    the previous sprint; do not reintroduce it.)

and ``ensure.py``'s own docstring on ``_read_user_version_readonly``:

    Open strictly read-only (``mode=ro``) — never creates the file, never
    writes a byte, even a ``-wal``/``-shm`` sidecar.

That last claim is false, and it breaks in two different directions on two
different SQLite versions. ``dbmod.connect`` puts the database in WAL journal
mode, and a read-only open of a WAL database is not the pure read the
docstring promises:

  * **SQLite 3.53.4** (the operator's ``.venv``): the ``mode=ro`` open
    MATERIALIZES ``arail.db-wal`` and ``arail.db-shm`` in the data dir. That
    is a write, on the path contractually defined as write-free. The sprint's
    own ``test_user_version_ahead_of_ledger`` catches it — and PASSES on the
    worktree's system python (SQLite 3.51.0), which is why three review
    rounds did not see it. It was found by running the sprint's existing
    suite against the operator's real interpreter, nothing more.

  * **SQLite 3.51.0** (the worktree's system python, and plenty of distro
    pythons): the same open FAILS outright — ``unable to open database
    file`` — whenever the transient ``-shm`` is not already on disk, because
    an older SQLite cannot bring up a WAL database read-only without
    creating the shared-memory file. ``ensure_db`` turns that into
    ``state="blocked"``.

``blocked`` is in ``status.sh``'s ``_DB_DEGRADING_STATES``, so in both
directions a perfectly healthy database can degrade a live lab to exit 3 and
tell the operator to run ``./arailctl doctor``. Real triggers: a data dir on a
read-only mount or restored with tight permissions; a backup/restore that
copies only ``arail.db`` (the documented way to back up an idle SQLite
database, which is precisely "without the sidecars"); or a lab created under
one SQLite version and inspected under another.

These tests are written to assert the CORRECT behaviour and are expected to
FAIL until the read path stops writing.
"""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

import pytest

from arail.dbspec.ensure import DEFAULT_SPEC_DIR, ensure_db

pytestmark = pytest.mark.skipif(
    not (DEFAULT_SPEC_DIR / "schema" / "migrations").is_dir(),
    reason="no spec/schema/migrations in this checkout",
)


def _tree(d: Path) -> set:
    return {(p.relative_to(d).as_posix(), p.stat().st_size)
            for p in sorted(d.rglob("*")) if p.is_file()}


def _created(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    rep = ensure_db(data_dir, apply=True)
    assert rep.state == "created", rep
    return data_dir


def _drop_wal_sidecars(data_dir: Path) -> None:
    for n in ("arail.db-wal", "arail.db-shm"):
        p = data_dir / n
        if p.exists():
            p.unlink()


def test_apply_false_writes_no_new_files_on_a_healthy_db(tmp_path):
    """The core §4.1 contract, on the plainest possible input: a healthy,
    fully-applied database. Nothing new may appear in data_dir."""
    data_dir = _created(tmp_path)
    _drop_wal_sidecars(data_dir)
    before = _tree(data_dir)
    rep = ensure_db(data_dir, apply=False)
    after = _tree(data_dir)
    assert rep.state == "ok", rep
    assert after == before, (
        "apply=False created files: "
        f"{sorted(n for n, _ in after - before)}"
    )


def test_apply_false_writes_no_new_files_when_the_db_is_ahead(tmp_path):
    """Same claim on the AHEAD path — the one the sprint's own test
    exercises, reproduced here so the report has an independent repro that
    names the SQLite-version dependence."""
    data_dir = _created(tmp_path)
    _drop_wal_sidecars(data_dir)
    conn = sqlite3.connect(str(data_dir / "arail.db"))
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()
    _drop_wal_sidecars(data_dir)
    before = _tree(data_dir)
    rep = ensure_db(data_dir, apply=False)
    after = _tree(data_dir)
    assert rep.state == "ahead", rep
    assert after == before, (
        f"sqlite {sqlite3.sqlite_version}: apply=False created "
        f"{sorted(n for n, _ in after - before)}"
    )


def test_readonly_data_dir_reports_blocked_on_a_healthy_db(tmp_path):
    """THE CONSEQUENCE (QA-1, medium severity).

    A healthy, fully-applied database in a data dir the process cannot
    write reports ``blocked`` — a degrading state — because the read-only
    open tries to materialize the WAL sidecars. status/doctor then degrade
    a live lab to exit 3 over a database that is completely fine.
    """
    data_dir = _created(tmp_path)
    _drop_wal_sidecars(data_dir)
    mode = stat.S_IMODE(data_dir.stat().st_mode)
    os.chmod(data_dir, 0o500)
    try:
        rep = ensure_db(data_dir, apply=False)
    finally:
        os.chmod(data_dir, mode)
    assert rep.state == "ok", (
        f"a healthy db in a read-only dir reported {rep.state!r}: {rep.detail}"
    )


def test_apply_false_never_creates_the_database_it_checks(tmp_path):
    """The half that DOES hold, pinned so a fix for the above cannot
    regress it: on an empty data dir, apply=False creates nothing at all."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    before = _tree(data_dir)
    rep = ensure_db(data_dir, apply=False)
    assert rep.state in ("pending", "unavailable"), rep
    assert _tree(data_dir) == before
    assert not (data_dir / "arail.db").exists()


def test_apply_false_does_not_create_a_missing_data_dir(tmp_path):
    """A missing data root must not be conjured into existence by a
    read-only check (the F6 class, one level up)."""
    data_dir = tmp_path / "nope" / "data"
    rep = ensure_db(data_dir, apply=False)
    assert not data_dir.exists(), "apply=False created the data dir"
    assert rep.state in ("pending", "unavailable", "blocked"), rep
