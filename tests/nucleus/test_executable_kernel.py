"""arail.nucleus.evals.executable_kernel (T-EXEC-1..4)."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from arail.nucleus.evals import executable_kernel as ek

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "nucleus" / "linux-kernel-mini"


def _git(args, cwd):
    env = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
          "HOME": str(cwd), "PATH": "/usr/bin:/bin"}
    result = subprocess.run(["git", *args], cwd=str(cwd), env=env, capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
    return result


@pytest.fixture
def base_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "a@b.invalid"], repo)
    _git(["config", "user.name", "a"], repo)
    (repo / "file.txt").write_text("line1\nline2\nline3\n")
    _git(["add", "file.txt"], repo)
    _git(["commit", "-q", "-m", "init"], repo)
    sha = _git(["rev-parse", "HEAD"], repo).stdout.decode().strip()
    return repo, sha


def _worktree_hash(repo: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(repo.rglob("*")):
        if ".git" in f.parts:
            continue
        if f.is_file():
            h.update(f.read_bytes())
    return h.hexdigest()


# ── T-EXEC-1: good patch applies; conflicting patch doesn't ──────────

GOOD_PATCH = """\
--- a/file.txt
+++ b/file.txt
@@ -1,3 +1,3 @@
 line1
-line2
+line2-changed
 line3
"""

CONFLICTING_PATCH = """\
--- a/file.txt
+++ b/file.txt
@@ -1,3 +1,3 @@
 line1
-this line does not exist
+replacement
 line3
"""


def test_good_patch_applies(base_repo):
    repo, sha = base_repo
    result = ek.patch_applies(GOOD_PATCH, base_repo=repo, base_commit=sha)
    assert result.ok is True


def test_conflicting_patch_does_not_apply(base_repo):
    repo, sha = base_repo
    result = ek.patch_applies(CONFLICTING_PATCH, base_repo=repo, base_commit=sha)
    assert result.ok is False


def test_worktree_unchanged_by_check(base_repo):
    repo, sha = base_repo
    before = _worktree_hash(repo)
    ek.patch_applies(GOOD_PATCH, base_repo=repo, base_commit=sha)
    after = _worktree_hash(repo)
    assert before == after


# ── T-EXEC-2: hostile patches -> not-applies, worktree unchanged ─────

HOSTILE_PATCHES = {
    "path_traversal": "--- a/../../../etc/passwd\n+++ b/../../../etc/passwd\n@@ -1 +1 @@\n-x\n+y\n",
    "absolute_path": "--- a//etc/passwd\n+++ b//etc/passwd\n@@ -1 +1 @@\n-x\n+y\n",
    "symlink": "diff --git a/x b/x\nnew mode 120000\nindex 0000000..1111111\n--- /dev/null\n+++ b/x\n@@ -0,0 +1 @@\n+/etc/passwd\n",
    "oversized": "--- a/file.txt\n+++ b/file.txt\n@@ -1,3 +1,3 @@\n" + ("+" + "a" * 100 + "\n") * 700,
    "binary": "--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-x\n+\x00\x01\x02binary\n",
    "nul_bytes": "some patch\x00with a nul byte in the middle\n",
}


@pytest.mark.parametrize("name", sorted(HOSTILE_PATCHES))
def test_hostile_patch_never_applies_and_worktree_unchanged(base_repo, name):
    repo, sha = base_repo
    before = _worktree_hash(repo)
    result = ek.patch_applies(HOSTILE_PATCHES[name], base_repo=repo, base_commit=sha)
    assert result.ok is False, f"{name} should not apply"
    after = _worktree_hash(repo)
    assert before == after, f"{name} must not touch the worktree"


# ── T-EXEC-3: compiles is NotRun; no subprocess call ──────────────────

def test_compiles_is_not_run(monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("compiles() must never invoke a subprocess")

    monkeypatch.setattr("subprocess.run", _boom)
    result = ek.compiles()
    assert isinstance(result, ek.NotRun)
    assert result == ek.NotRun("requires a Linux build host; not available in sprint 1")


# ── T-EXEC-4: checkpatch timeout recorded ─────────────────────────────

def test_checkpatch_timeout_recorded(tmp_path):
    sleepy = tmp_path / "sleepy_checkpatch.py"
    sleepy.write_text(
        "import time, sys\n"
        "time.sleep(120)\n"
        "sys.exit(0)\n"
    )
    # not .pl -> dispatched via python3; force a very short timeout for the test
    original_timeout = ek._TIMEOUT_S
    try:
        ek._TIMEOUT_S = 0.2
        result = ek.checkpatch_clean("--- a/x\n+++ b/x\n", checkpatch_path=sleepy)
    finally:
        ek._TIMEOUT_S = original_timeout
    assert result.timed_out is True
    assert result.ok is False


def test_checkpatch_clean_pass_and_fail(tmp_path):
    fake = FIXTURE_DIR / "fake_checkpatch.py"
    clean = ek.checkpatch_clean("--- a/x\n+++ b/x\n+clean\n", checkpatch_path=fake)
    assert clean.ok is True
    dirty = ek.checkpatch_clean("--- a/x\n+++ b/x\n+dirty   \n", checkpatch_path=fake)
    assert dirty.ok is False
    assert "WARNING" in dirty.reason


def test_checkpatch_missing_file():
    result = ek.checkpatch_clean("patch", checkpatch_path=Path("/nonexistent/checkpatch.pl"))
    assert result.ok is False
    assert "not found" in result.reason
