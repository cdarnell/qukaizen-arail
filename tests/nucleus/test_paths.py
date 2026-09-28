"""arail.nucleus.paths — layout, id validation, git-ignore guard, build lock
(T-PATH-1..3, T-LOCK-1..2)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from arail.nucleus import paths
from arail.nucleus.errors import RefusedByPolicy


# ── build_id / shard / version validation ────────────────────────────

def test_new_build_id_matches_format():
    build_id = paths.new_build_id("linux-kernel")
    assert paths._BUILD_ID_RE.match(build_id)
    assert build_id.startswith("linux-kernel-")


def test_validate_build_id_rejects_garbage():
    with pytest.raises(RefusedByPolicy):
        paths.validate_build_id("not a build id")


def test_shard_dir_rejects_bad_shard(tmp_path, monkeypatch):
    monkeypatch.setattr("arail.config.MODELS_DIR", str(tmp_path / "models"))
    with pytest.raises(RefusedByPolicy):
        paths.shard_dir("Not Valid!", "1.0.0")


def test_shard_dir_rejects_bad_version(tmp_path, monkeypatch):
    monkeypatch.setattr("arail.config.MODELS_DIR", str(tmp_path / "models"))
    with pytest.raises(RefusedByPolicy):
        paths.shard_dir("qkz-kernel", "v1")


def test_next_patch_version_starts_at_0_1_0(tmp_path, monkeypatch):
    monkeypatch.setattr("arail.config.MODELS_DIR", str(tmp_path / "models"))
    assert paths.next_patch_version("qkz-kernel") == "0.1.0"


def test_next_patch_version_increments(tmp_path, monkeypatch):
    monkeypatch.setattr("arail.config.MODELS_DIR", str(tmp_path / "models"))
    (tmp_path / "models" / "forge" / "qkz-kernel" / "0.3.1").mkdir(parents=True)
    (tmp_path / "models" / "forge" / "qkz-kernel" / "0.2.9").mkdir(parents=True)
    assert paths.next_patch_version("qkz-kernel") == "0.3.2"


# ── T-PATH-1/2: git-ignore guard ─────────────────────────────────────

def _init_repo(root):
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True)


def test_guard_passes_for_gitignored_path(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("lab/\n")
    target = tmp_path / "lab" / "models" / "forge"
    target.mkdir(parents=True)
    paths.guard_committable_output(target)  # no raise


def test_guard_refuses_for_untracked_ignored_path(tmp_path):
    _init_repo(tmp_path)
    # No .gitignore entry -> not ignored.
    target = tmp_path / "models" / "forge"
    target.mkdir(parents=True)
    with pytest.raises(RefusedByPolicy, match="committable"):
        paths.guard_committable_output(target)


def test_guard_noop_outside_any_git_worktree(tmp_path):
    target = tmp_path / "outside" / "forge"
    target.mkdir(parents=True)
    paths.guard_committable_output(target)  # no raise — no .git anywhere above


def test_shard_dir_refuses_when_forge_root_is_tracked(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    monkeypatch.setattr("arail.config.MODELS_DIR", str(tmp_path / "models"))
    (tmp_path / "models").mkdir()
    with pytest.raises(RefusedByPolicy, match="committable"):
        paths.shard_dir("qkz-kernel", "1.0.0")


def test_shard_dir_default_lab_models_passes_git_ignore(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("lab/\n")
    monkeypatch.setattr("arail.config.MODELS_DIR", str(tmp_path / "lab" / "models"))
    result = paths.shard_dir("qkz-kernel", "1.0.0")
    assert result == (tmp_path / "lab" / "models" / "forge" / "qkz-kernel" / "1.0.0").resolve()


# ── T-PATH-3: existing version never overwritten ─────────────────────

def test_shard_dir_refuses_existing_version(tmp_path, monkeypatch):
    monkeypatch.setattr("arail.config.MODELS_DIR", str(tmp_path / "models"))
    existing = tmp_path / "models" / "forge" / "qkz-kernel" / "1.0.0"
    existing.mkdir(parents=True)
    with pytest.raises(RefusedByPolicy, match="already exists"):
        paths.shard_dir("qkz-kernel", "1.0.0")


# ── T-LOCK-1/2: build lock ────────────────────────────────────────────

_SRC_DIR = str(Path(__file__).resolve().parents[2] / "src")

_CHILD_SCRIPT = """
import sys
sys.path.insert(0, {src!r})
from arail.nucleus import paths
try:
    with paths.build_lock("kernel-20260101T000000Z-dead"):
        pass
    sys.exit(0)
except Exception as e:
    sys.stderr.write(str(e))
    sys.exit(3)
"""


def test_build_lock_excludes_concurrent_build(tmp_path, monkeypatch):
    monkeypatch.setattr("arail.config.DATA_DIR", str(tmp_path / "data"))
    build_id_1 = paths.new_build_id("kernel")
    with paths.build_lock(build_id_1):
        env = dict(os.environ)
        env["ARAIL_DATA_DIR"] = str(tmp_path / "data")
        result = subprocess.run(
            [sys.executable, "-c", _CHILD_SCRIPT.format(src=_SRC_DIR)],
            capture_output=True, env=env,
        )
        assert result.returncode == 3
        assert b"already running" in result.stderr


def test_build_lock_takes_over_stale_lock(tmp_path, monkeypatch):
    monkeypatch.setattr("arail.config.DATA_DIR", str(tmp_path / "data"))
    root = paths.ensure_nucleus_data_layout()
    lock_path = root / "build.lock"
    lock_path.write_text('{"pid": 999999, "build_id": "kernel-20260101T000000Z-dead", "started": "x"}')

    with paths.build_lock(paths.new_build_id("kernel")):
        pass  # no raise -> stale lock was taken over
