"""Shared-fixture parity test: scripts/lib/instances.sh's
inst_resolve_data_dirs() and arail.data_dirs.resolve_data_dirs() must agree
on slug set + origin for the same on-disk fixture (ARCHITECTURE.md §8
tech-debt note: "two implementations of one rule... mitigated by a shared
fixture test asserting they agree").

Requires `bash` on PATH; skipped otherwise (CI on every platform this repo
targets has bash, so this should never actually skip in practice).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from arail.data_dirs import resolve_data_dirs

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTANCES_SH = REPO_ROOT / "scripts" / "lib" / "instances.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash not on PATH")


def _run_shell_resolver(fixture_root: Path) -> set:
    script = f'''
REPO_ROOT="{fixture_root}"
source "{INSTANCES_SH}"
inst_resolve_data_dirs
'''
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=True)
    rows = set()
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        slug, data_dir, origin = line.split("\t")
        rows.add((slug, origin))
    return rows


def _build_fixture(root: Path) -> None:
    (root / "lab" / "instances" / "registry.d").mkdir(parents=True)
    (root / "lab" / "instances" / "registry.d" / "finance.json").write_text(
        json.dumps({
            "data_dir": str(root / "lab" / "instances" / "finance" / "data"),
            "pkb_root": str(root / "lab" / "instances" / "finance" / "pkb"),
        }))
    for slug in ("ai", "qukaizen", "video-games"):
        (root / "lab" / "instances" / slug / "data").mkdir(parents=True)


def test_shell_and_python_resolvers_agree_on_slug_and_origin(tmp_path: Path):
    _build_fixture(tmp_path)

    shell_rows = _run_shell_resolver(tmp_path)
    py_rows = {
        (r.slug if r.slug != "__root__" else "__root__", r.origin)
        for r in resolve_data_dirs(tmp_path)
    }

    assert shell_rows == py_rows
    assert len(shell_rows) == 5  # root + finance(registry) + 3 ondisk
