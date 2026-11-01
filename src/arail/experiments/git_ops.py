"""arail.experiments.git_ops — Safe git primitives for the
autoresearch loop.

Every function here is synchronous, non-interactive, and refuses
to do anything destructive to the user's working tree. The loop
only:
  - reads current state (SHA, branch, dirty flag)
  - creates experiment branches under autoresearch/
  - writes ONE file (config/tuning.yml)
  - commits that file + lab/data/aerollm-bench.jsonl with a
    structured message that includes the baseline/variant numbers
  - never pushes, never force-anything, never touches main

If any operation would require `--force`, we abort. If the tree
is dirty at the start, we abort. This module is intentionally
dumb and loud.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


# Prefix for every branch the autoresearch loop creates. The UI
# groups runs by this namespace, and humans can `git branch -D
# autoresearch/*` to clean up.
AUTORESEARCH_BRANCH_PREFIX = "autoresearch/"

# The only files we're ever allowed to write as part of an
# autoresearch commit. If the agent proposes edits elsewhere, the
# commit is refused.
#
# Two backends live here side-by-side:
#   - AeroLLM (CUDA, 1 TB disk-streamed models): tuning.yml + aerollm-bench
#   - AeroLLM MLX (Apple, unified memory):       tuning-mlx.yml + mlx-bench
#
# Adding a third backend means adding exactly two more entries here,
# with loud justification in the commit message. This set is load-bearing
# for the safety model — a test asserts it stays small.
ALLOWED_WRITABLE_FILES = {
    "config/tuning.yml",
    "config/tuning-mlx.yml",
    "lab/data/aerollm-bench.jsonl",
    "lab/data/mlx-bench.jsonl",
}


@dataclass
class GitState:
    sha: str
    short_sha: str
    branch: str
    is_dirty: bool
    dirty_files: List[str]

    def to_dict(self) -> dict:
        return {
            "sha": self.sha,
            "short_sha": self.short_sha,
            "branch": self.branch,
            "is_dirty": self.is_dirty,
            "dirty_files": self.dirty_files,
        }


class GitSafetyError(RuntimeError):
    """Raised when the autoresearch loop tries something we don't allow."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent


def _run(args: List[str], *, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command from the repo root and return the result."""
    return subprocess.run(
        ["git"] + args,
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        check=check,
    )


def git_state() -> GitState:
    """Snapshot the current git state. Read-only."""
    sha = _run(["rev-parse", "HEAD"]).stdout.strip()
    short = _run(["rev-parse", "--short", "HEAD"]).stdout.strip()
    branch = _run(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    status = _run(["status", "--porcelain"]).stdout
    dirty_files = [
        line[3:].strip() for line in status.splitlines() if line.strip()
    ]
    return GitState(
        sha=sha,
        short_sha=short,
        branch=branch,
        is_dirty=bool(dirty_files),
        dirty_files=dirty_files,
    )


def assert_clean_tree() -> None:
    """Raise GitSafetyError if the tree has uncommitted changes.
    The autoresearch loop refuses to run on a dirty tree because
    it would otherwise confuse 'variant I applied' with 'user's
    in-progress work'."""
    state = git_state()
    if state.is_dirty:
        raise GitSafetyError(
            "Working tree is dirty; refusing to run autoresearch. "
            "Commit or stash your changes first. Dirty files: "
            + ", ".join(state.dirty_files[:10])
        )


def create_experiment_branch(exp_id: str, base_branch: Optional[str] = None) -> str:
    """Create and check out a new autoresearch/ branch. Returns
    the branch name. Refuses if one with the same id already
    exists (we don't clobber)."""
    branch = f"{AUTORESEARCH_BRANCH_PREFIX}{exp_id}"
    existing = _run(["branch", "--list", branch]).stdout.strip()
    if existing:
        raise GitSafetyError(f"branch already exists: {branch}")
    base = base_branch or git_state().branch
    _run(["checkout", "-b", branch, base])
    return branch


def abort_experiment(return_to_branch: str) -> None:
    """Discard the current branch's changes and switch back.
    Used when a variant fails to beat baseline — we don't want
    the losing branch lingering.

    Leaves the branch ref in place so humans can inspect; just
    checks out the original branch and resets the working tree."""
    _run(["checkout", "--", "."], check=False)
    _run(["checkout", return_to_branch])


def commit_experiment(
    *,
    subject: str,
    body: str,
    files: List[str],
) -> str:
    """Stage ONLY the listed files (each must be in ALLOWED_WRITABLE_FILES)
    and create a commit. Returns the new HEAD SHA.

    The restriction exists so that if the agent tries to sneak in a
    bad edit elsewhere, this function will fail loudly rather than
    commit it."""
    for f in files:
        if f not in ALLOWED_WRITABLE_FILES:
            raise GitSafetyError(
                f"refusing to commit {f}: not in ALLOWED_WRITABLE_FILES"
            )
    # Stage explicitly; never `git add -A`.
    #
    # `-f` is required, not sloppiness: `lab/data/` is gitignored
    # wholesale (.gitignore:42), and two of the four whitelisted paths
    # live under it. A plain `git add` on an ignored path exits 1 and
    # stages nothing, so with check=True this raised CalledProcessError
    # on the FIRST commit of every pass — the baseline capture, whose
    # caller only catches GitSafetyError. The loop could not complete a
    # single pass on a clean checkout.
    #
    # Forcing is safe here precisely because it can only ever reach
    # ALLOWED_WRITABLE_FILES: the membership check above has already
    # rejected anything else, so `-f` cannot be used to sneak an
    # ignored path into a commit.
    for f in files:
        full = _repo_root() / f
        if not full.exists():
            continue  # nothing to stage for this path
        _run(["add", "-f", "--", f])

    # Check if there's actually anything staged. If the variant
    # produced no diff, we don't make an empty commit.
    staged = _run(["diff", "--cached", "--name-only"]).stdout.strip()
    if not staged:
        raise GitSafetyError("no changes staged; skipping empty commit")

    msg = subject.rstrip() + "\n\n" + body.rstrip() + "\n"
    # Use -F - to pass the message via stdin; avoids any shell quoting.
    result = subprocess.run(
        ["git", "commit", "-F", "-"],
        cwd=_repo_root(),
        input=msg,
        capture_output=True,
        text=True,
        check=True,
    )
    _ = result  # unused; `git commit` exits non-zero on failure
    return _run(["rev-parse", "HEAD"]).stdout.strip()


def diff_url(sha: str, remote: str = "origin") -> Optional[str]:
    """Best-effort construction of a GitHub diff URL for the given
    SHA. Returns None if the remote isn't a recognizable GitHub URL."""
    try:
        url = _run(["remote", "get-url", remote]).stdout.strip()
    except subprocess.CalledProcessError:
        return None
    if not url:
        return None
    # git@github.com:owner/repo.git  or  https://github.com/owner/repo.git
    repo_part: Optional[str] = None
    if url.startswith("git@github.com:"):
        repo_part = url[len("git@github.com:"):]
    elif url.startswith("https://github.com/"):
        repo_part = url[len("https://github.com/"):]
    if not repo_part:
        return None
    if repo_part.endswith(".git"):
        repo_part = repo_part[:-4]
    return f"https://github.com/{repo_part}/commit/{sha}"
