"""ARCHITECTURE.md §7 test 4 / F16 — the one specified test nobody wrote.

    Schema fidelity: after ``ensure_db(apply=True)``, ``atlas schema diff``
    (dev-only test, skipped if ``atlas`` absent) reports **no statements** —
    proves the Atlas-free replay reproduces the declared schema (F16).

This is the load-bearing claim under the entire seamless path: `ensure` replays
`spec/schema/migrations/` *without* Atlas, and everything downstream assumes
the result is identical to what `spec/schema/schema.hcl` declares. Five build
rounds shipped on that assumption unverified. It holds — measured on
2026-08-10 with atlas at /opt/homebrew/bin/atlas:

    Schemas are synced, no changes to be made.

Skips cleanly where `atlas` is absent (every user machine, and CI), which is
exactly why it was safe to leave out and exactly why it needed writing: the
one environment that *does* have Atlas is a developer's, and a developer is
the only person who can catch a drift between the ledger and the declaration
before it reaches anybody else.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from arail.dbspec.ensure import DEFAULT_SPEC_DIR, ensure_db

pytestmark = [
    pytest.mark.skipif(shutil.which("atlas") is None,
                       reason="atlas is a developer tool; not installed here"),
    pytest.mark.skipif(
        not (DEFAULT_SPEC_DIR / "schema" / "schema.hcl").is_file(),
        reason="no spec/schema/schema.hcl in this checkout"),
]


def test_the_atlas_free_replay_reproduces_the_declared_schema(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    report = ensure_db(data_dir, apply=True)
    assert report.state == "created", report

    result = subprocess.run(
        ["atlas", "schema", "diff",
         "--from", f"sqlite://{data_dir / 'arail.db'}",
         "--to", f"file://{DEFAULT_SPEC_DIR / 'schema' / 'schema.hcl'}",
         "--dev-url", "sqlite://dev?mode=memory"],
        capture_output=True, text=True, timeout=120)

    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert "no changes to be made" in result.stdout, (
        "the replayed schema differs from spec/schema/schema.hcl — the "
        "Atlas-free path and the declaration have drifted:\n%s" % result.stdout)


def test_user_version_is_invisible_to_the_declared_schema(tmp_path):
    """Assumption 4: using ``PRAGMA user_version`` as the migration cursor
    adds no table, so it cannot show up as drift against schema.hcl. The
    test above would catch a violation; this one names the reason, so a
    failure points at the cause rather than at a diff."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    ensure_db(data_dir, apply=True)
    import sqlite3
    conn = sqlite3.connect(str(data_dir / "arail.db"))
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert not any(n.startswith("_arail_migration") for n in names), names
    assert not any("user_version" in n for n in names), names
