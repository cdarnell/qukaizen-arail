"""git_ops against a REAL temporary git repo.

The rest of the experiments suite stubs every git seam, which is right
for testing loop logic but means the actual git invocations were never
exercised. That gap hid a blocker: two of the four whitelisted paths
live under `lab/data/`, which .gitignore excludes wholesale, so a plain
`git add` exited 1 and staged nothing — raising CalledProcessError on
the first commit of every pass. These tests drive real git so that
class of bug cannot come back.
"""
from __future__ import annotations

import subprocess

import pytest

from arail.experiments import git_ops


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=check,
    )


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """A throwaway repo shaped like ARAIL: lab/data/ gitignored, a
    tracked config file, and an untracked-because-ignored bench log."""
    root = tmp_path / "lab-repo"
    root.mkdir()
    _git(root, "init", "-q", ".")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test")
    (root / "config").mkdir()
    (root / "lab" / "data").mkdir(parents=True)

    # The real .gitignore rule that caused the bug.
    (root / ".gitignore").write_text("lab/data/\n")
    (root / "config" / "tuning.yml").write_text("baseline_metrics: {}\n")
    _git(root, "add", ".gitignore", "config/tuning.yml")
    _git(root, "commit", "-qm", "init")

    monkeypatch.setattr(git_ops, "_repo_root", lambda: root)
    return root


def test_bench_file_is_actually_ignored(repo):
    """Guard the premise: if this stops being true the -f below is moot,
    and someone should ask why the ignore rule changed."""
    r = _git(repo, "check-ignore", "lab/data/aerollm-bench.jsonl", check=False)
    assert r.returncode == 0, "lab/data/ is no longer gitignored"


def test_commit_experiment_stages_gitignored_bench_file(repo):
    (repo / "lab" / "data" / "aerollm-bench.jsonl").write_text(
        '{"decode_tok_per_sec": 12.5}\n'
    )
    (repo / "config" / "tuning.yml").write_text("baseline_metrics: {n: 1}\n")

    sha = git_ops.commit_experiment(
        subject="bench(aerollm): capture baseline 12.500 tok/s for m",
        body="body",
        files=["config/tuning.yml", "lab/data/aerollm-bench.jsonl"],
    )

    assert sha
    listed = _git(repo, "show", "--stat", "--name-only", "--format=", "HEAD")
    names = set(listed.stdout.split())
    assert "lab/data/aerollm-bench.jsonl" in names, (
        "the ignored-but-whitelisted bench log did not make it into the commit"
    )
    assert "config/tuning.yml" in names
    # And the tree is clean afterwards — nothing left dangling.
    assert not _git(repo, "status", "--porcelain").stdout.strip()


def test_commit_experiment_still_refuses_paths_outside_the_whitelist(repo):
    """`-f` must not become a way to sneak an ignored file in. The
    membership check runs before any staging."""
    (repo / "lab" / "data" / "secrets.env").write_text("TOKEN=hunter2\n")
    with pytest.raises(git_ops.GitSafetyError):
        git_ops.commit_experiment(
            subject="s", body="b", files=["lab/data/secrets.env"],
        )
    # Nothing staged, nothing committed.
    assert not _git(repo, "diff", "--cached", "--name-only").stdout.strip()
    assert _git(repo, "rev-list", "--count", "HEAD").stdout.strip() == "1"


def test_empty_diff_raises_rather_than_making_an_empty_commit(repo):
    with pytest.raises(git_ops.GitSafetyError):
        git_ops.commit_experiment(
            subject="s", body="b", files=["config/tuning.yml"],
        )


def test_full_branch_lifecycle_against_real_git(repo):
    """create -> commit -> abort leaves the loser branch in place and
    puts us back where we started, with the user's tree untouched."""
    start = git_ops.git_state().branch

    branch = git_ops.create_experiment_branch("20260817-000000-probe",
                                              base_branch=start)
    assert branch == "autoresearch/20260817-000000-probe"
    assert git_ops.git_state().branch == branch

    (repo / "config" / "tuning.yml").write_text("knob: changed\n")
    git_ops.abort_experiment(start)

    assert git_ops.git_state().branch == start
    # The losing branch ref survives for inspection...
    refs = _git(repo, "branch", "--list", "autoresearch/*").stdout
    assert "20260817-000000-probe" in refs
    # ...and the discarded edit is gone.
    assert "changed" not in (repo / "config" / "tuning.yml").read_text()
