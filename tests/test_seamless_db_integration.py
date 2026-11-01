"""Test 20 (DB-creation half) of
sprints/2026-08-10-arail2-persistence-instantiated/ARCHITECTURE.md §7:
"fresh clone -> setup -> start yields a working DB."

This exercises the real ensure_db()/resolve_data_dirs() functions the
way install.sh's `_install_db_ensure` and start.sh's `_instance_db_ensure`
actually call them (via the `python -m arail.dbspec.ensure` CLI, in a
real subprocess) against a from-scratch fixture tree that mirrors what a
freshly-cloned checkout's `lab/` looks like before any World has ever
been registered.

**What this does NOT cover** (see BUILD_LOG.md): the actual portal
boot, the shell wiring in install.sh/start.sh itself (bash, not
exercised by pytest here), and anything requiring lancedb/fastapi. This
worktree has no .venv; this test uses `sys.executable` (the same
interpreter running pytest), which is sufficient to prove ensure_db's
own contract end-to-end even though it can't prove the full shell
integration.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_DIR = REPO_ROOT / "spec"


def _run_ensure_cli(data_dir: Path, *extra_args: str) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "arail.dbspec.ensure", str(data_dir),
         "--spec-dir", str(SPEC_DIR), "--json", *extra_args],
        cwd=REPO_ROOT, env={"PYTHONPATH": str(REPO_ROOT / "src")},
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def test_fresh_root_and_two_instances_all_get_a_working_db(tmp_path: Path):
    """Mirrors install.sh's loop over resolve_data_dirs(): the root lab
    plus two "instances" (bare data dirs here — the shell wiring is what
    actually resolves real instance directories, not exercised here)."""
    root_data = tmp_path / "lab" / "data"
    inst_a = tmp_path / "lab" / "instances" / "ai" / "data"
    inst_b = tmp_path / "lab" / "instances" / "finance" / "data"
    for d in (root_data, inst_a, inst_b):
        d.mkdir(parents=True)

    # "install" step: apply=True over every resolved root.
    for d in (root_data, inst_a, inst_b):
        report = _run_ensure_cli(d, "--apply")
        assert report["state"] == "created", report
        assert (d / "arail.db").exists()

    # "start" step, later, on just one instance: idempotent, quiet
    # (nothing applied a second time), still working.
    report = _run_ensure_cli(inst_a, "--apply")
    assert report["state"] == "ok"
    assert report["applied"] == []

    # The DB is genuinely usable: query it directly, no ensure_db
    # involved, proving "a working DB" and not just "a file that exists."
    import sqlite3
    conn = sqlite3.connect(inst_a / "arail.db")
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "schema_version" in tables
        assert "worlds" in tables
        row = conn.execute(
            "SELECT version, spec_sha256 FROM schema_version").fetchone()
        assert row is not None
    finally:
        conn.close()


def test_status_never_creates_what_it_checks_end_to_end(tmp_path: Path):
    """The other half of test 20/F6: a status-equivalent (apply=False)
    call against a fresh, never-provisioned tree creates nothing."""
    data_dir = tmp_path / "lab" / "data"
    data_dir.mkdir(parents=True)
    before = sorted(p.name for p in data_dir.iterdir())

    report = _run_ensure_cli(data_dir)  # no --apply

    after = sorted(p.name for p in data_dir.iterdir())
    assert before == after == []
    assert report["state"] in ("pending", "unavailable")
