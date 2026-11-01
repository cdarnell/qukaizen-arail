"""QA target 8: ``_apply_lock`` across PROCESSES.

F17 in ARCHITECTURE.md:

    Two processes ensure the same DB concurrently -> busy_timeout=5000 +
    per-file txn; test two concurrent ensure_db(apply=True). Last writer
    wins at the same version; both end ok, no corruption.

The sprint's own test covers the in-process (threading.Lock) half. The
``fcntl.flock`` half — the only part that actually protects two *processes*,
which is the case ``install`` (looping over six roots) and a concurrently
booting ``start`` genuinely produce — has never been exercised. This does it
by spawning real interpreters running the real CLI shim.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from arail.dbspec.ensure import DEFAULT_SPEC_DIR, _migration_files, ensure_db

pytestmark = pytest.mark.skipif(
    not (DEFAULT_SPEC_DIR / "schema" / "migrations").is_dir(),
    reason="no spec/schema/migrations in this checkout",
)

N_PROCS = 8


def _env():
    env = dict(os.environ)
    src = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _spawn(data_dir: Path):
    return subprocess.Popen(
        [sys.executable, "-m", "arail.dbspec.ensure", str(data_dir),
         "--apply", "--json"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_env())


def _expected_version() -> int:
    return len(_migration_files(DEFAULT_SPEC_DIR / "schema" / "migrations"))


def test_concurrent_processes_all_succeed_on_one_data_dir(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    procs = [_spawn(data_dir) for _ in range(N_PROCS)]
    results = []
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, (p.returncode, out, err)
        assert err.strip() == "", f"a concurrent ensure wrote to stderr: {err}"
        results.append(json.loads(out))

    # Every process must end in a healthy state — never "blocked", never a
    # half-applied schema, never a traceback.
    states = sorted(r["state"] for r in results)
    assert set(states) <= {"created", "updated", "ok"}, states
    # Exactly one process may claim it CREATED the database.
    assert states.count("created") <= 1, (
        "more than one process claimed to create the same database: %s" % states)

    expected = _expected_version()
    for r in results:
        assert r["version"] == expected, r
    # No migration may be applied twice across the whole run.
    applied = [name for r in results for name in r["applied"]]
    assert len(applied) == len(set(applied)), (
        "a migration was applied by more than one process: %s" % applied)


def test_the_database_is_not_corrupted_by_concurrent_appliers(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    procs = [_spawn(data_dir) for _ in range(N_PROCS)]
    for p in procs:
        p.communicate(timeout=120)

    db_path = data_dir / "arail.db"
    assert db_path.exists()
    conn = sqlite3.connect(str(db_path))
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == \
            _expected_version()
        # The declared tables are all there exactly once.
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")]
        assert len(names) == len(set(names)), names
        assert names, "the concurrent run produced an empty schema"
    finally:
        conn.close()


def test_a_stale_lock_file_does_not_wedge_a_later_run(tmp_path):
    """The lock file is created and never removed (by design — unlinking a
    flock target is a classic race). A leftover ``.arail_ensure.lock`` from
    a killed process must not block the next run."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / ".arail_ensure.lock").write_text("stale")
    rep = ensure_db(data_dir, apply=True)
    assert rep.state == "created", rep


def test_the_lock_file_lives_inside_the_data_dir_only(tmp_path):
    """§4.1: ``ensure`` never writes anywhere except inside ``data_dir``.
    The lock is a write; assert it lands in the right place and that the
    parent directory gains nothing."""
    parent = tmp_path / "lab"
    data_dir = parent / "data"
    data_dir.mkdir(parents=True)
    before_parent = {p.name for p in parent.iterdir()}
    ensure_db(data_dir, apply=True)
    assert (data_dir / ".arail_ensure.lock").exists()
    assert {p.name for p in parent.iterdir()} == before_parent
