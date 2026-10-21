"""Retention for autoresearch branches and bench logs (hazards H3/H4).

The pure-policy tests inject branches and a clock so they never touch a
repo. The last group drives REAL git, because "we did not delete the
win" is the kind of claim that should be demonstrated against the tool
that actually does the deleting.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from arail.experiments.branch_browser import BranchSummary
from arail.experiments import retention as R


NOW = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)


def _b(name, status="loss", age_days=100):
    when = (NOW - timedelta(days=age_days)).isoformat()
    return BranchSummary(
        branch=f"autoresearch/{name}", exp_id=name, backend="aerollm",
        base_short_sha="aaaaaaa", head_short_sha="bbbbbbb", commit_count=1,
        status=status, headline=None, when_created=when, diff_url=None,
    )


def _plan(branches, **kw):
    policy = R.RetentionPolicy(**{
        "keep_recent": kw.pop("keep_recent", 0),
        "min_age_days": kw.pop("min_age_days", 14),
        "max_bench_lines": kw.pop("max_bench_lines", 5000),
    })
    return R.plan_branch_prune(
        policy, now=NOW, branches=branches,
        current_branch=kw.pop("current_branch", "main"),
    )


# ── What must never be pruned ───────────────────────────────────────

@pytest.mark.parametrize("status", sorted(R.PROTECTED_STATUSES))
def test_protected_statuses_are_never_pruned(status):
    plan = _plan([_b("old-one", status=status, age_days=999)])
    assert plan.to_prune == []
    assert "protected" in plan.decisions[0].reason or \
           "not in the prunable set" in plan.decisions[0].reason


def test_a_win_survives_even_when_ancient_and_outside_every_window():
    plan = _plan([_b("winner", status="win", age_days=10_000)],
                 keep_recent=0, min_age_days=0)
    assert plan.to_prune == []


def test_current_branch_is_never_pruned():
    plan = _plan([_b("checked-out", age_days=999)],
                 current_branch="autoresearch/checked-out")
    assert plan.to_prune == []
    assert plan.decisions[0].reason == "currently checked out"


def test_keep_recent_window_protects_the_newest():
    branches = [_b(f"v{i}", age_days=100 + i) for i in range(5)]
    plan = _plan(branches, keep_recent=3)
    kept = [d.branch for d in plan.to_keep]
    assert len(kept) == 3
    # Newest three, by date — not input order.
    assert kept == ["autoresearch/v0", "autoresearch/v1", "autoresearch/v2"]


def test_young_branches_are_never_pruned():
    plan = _plan([_b("fresh", age_days=3)], min_age_days=14)
    assert plan.to_prune == []
    assert "younger than 14d" in plan.decisions[0].reason


def test_unparseable_date_keeps_rather_than_prunes():
    b = _b("weird", age_days=999)
    b.when_created = "not-a-date"
    plan = _plan([b])
    assert plan.to_prune == []
    assert "could not parse" in plan.decisions[0].reason


def test_branch_outside_the_namespace_is_never_pruned():
    b = _b("x", age_days=999)
    b.branch = "main"
    plan = _plan([b])
    assert plan.to_prune == []


def test_keep_recent_is_applied_by_recency_not_list_order():
    """A caller handing us an unsorted list must not be able to make the
    keep_recent window protect the wrong branches."""
    oldest = _b("oldest", age_days=900)
    newest = _b("newest", age_days=20)
    plan = _plan([oldest, newest], keep_recent=1)
    assert [d.branch for d in plan.to_keep] == ["autoresearch/newest"]
    assert [d.branch for d in plan.to_prune] == ["autoresearch/oldest"]


# ── What is eligible ────────────────────────────────────────────────

def test_old_loss_and_baseline_are_prunable():
    plan = _plan([_b("l", status="loss", age_days=99),
                  _b("bl", status="baseline", age_days=99)])
    assert {d.branch for d in plan.to_prune} == {
        "autoresearch/l", "autoresearch/bl"}


def test_policy_validation_rejects_nonsense():
    for kwargs in ({"keep_recent": -1}, {"min_age_days": -5},
                   {"max_bench_lines": 0}):
        with pytest.raises(ValueError):
            R.RetentionPolicy(**kwargs).validate()


# ── apply: receipts and re-validation ───────────────────────────────

def test_apply_refuses_a_hand_edited_plan_that_targets_a_win(tmp_path):
    """The plan is re-validated at the point of deletion, so a plan that
    was tampered with (or built against a stale view) cannot delete a
    protected branch."""
    plan = R.PrunePlan(policy=R.DEFAULT_POLICY, current_branch="main")
    plan.decisions.append(R.BranchDecision(
        branch="autoresearch/winner", status="win", head_short_sha="abc",
        when_created=NOW.isoformat(), prune=True, reason="tampered",
    ))
    calls = []

    def fake_run(args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    result = R.apply_branch_prune(
        plan, receipt_path=tmp_path / "r.jsonl", runner=fake_run)
    assert result["deleted"] == []
    assert result["failed"][0]["error"] == "status=win is protected"
    assert not any("branch" in c and "-D" in c for c in calls)


def test_receipt_is_written_before_the_delete(tmp_path):
    receipt = tmp_path / "pruned.jsonl"
    plan = R.PrunePlan(policy=R.DEFAULT_POLICY, current_branch="main")
    plan.decisions.append(R.BranchDecision(
        branch="autoresearch/old", status="loss", head_short_sha="bbbbbbb",
        when_created=NOW.isoformat(), prune=True, reason="old",
    ))
    seen_at_delete = {}

    def fake_run(args):
        if args[:2] == ["branch", "-D"]:
            seen_at_delete["receipt_exists"] = receipt.exists()
        if args[0] == "rev-parse":
            return subprocess.CompletedProcess(args, 0, "f" * 40, "")
        return subprocess.CompletedProcess(args, 0, "", "")

    result = R.apply_branch_prune(plan, receipt_path=receipt, runner=fake_run)
    assert result["deleted"] == ["autoresearch/old"]
    assert seen_at_delete["receipt_exists"] is True
    row = json.loads(receipt.read_text().strip())
    assert row["schema"] == R.RECEIPT_SCHEMA
    assert row["sha"] == "f" * 40
    assert row["restore_with"] == f"git branch autoresearch/old {'f' * 40}"


def test_delete_failure_is_reported_not_swallowed(tmp_path):
    plan = R.PrunePlan(policy=R.DEFAULT_POLICY, current_branch="main")
    plan.decisions.append(R.BranchDecision(
        branch="autoresearch/stubborn", status="loss", head_short_sha="b",
        when_created=NOW.isoformat(), prune=True, reason="old",
    ))

    def fake_run(args):
        if args[:2] == ["branch", "-D"]:
            return subprocess.CompletedProcess(args, 1, "", "it said no")
        return subprocess.CompletedProcess(args, 0, "", "")

    result = R.apply_branch_prune(
        plan, receipt_path=tmp_path / "r.jsonl", runner=fake_run)
    assert result["deleted"] == []
    assert result["failed"][0]["error"] == "it said no"


# ── Bench rotation (H4) ─────────────────────────────────────────────

def _write_lines(p: Path, n: int, start: int = 0):
    p.write_text("".join(json.dumps({"i": i}) + "\n"
                         for i in range(start, start + n)))


def _append_lines(p: Path, n: int, start: int):
    """How the loop actually grows the log — append_run, not rewrite."""
    with open(p, "a") as fh:
        for i in range(start, start + n):
            fh.write(json.dumps({"i": i}) + "\n")


def test_rotation_not_needed_under_the_limit(tmp_path):
    p = tmp_path / "bench.jsonl"
    _write_lines(p, 10)
    plan = R.plan_bench_rotation(p, R.RetentionPolicy(max_bench_lines=100))
    assert plan.needed is False
    assert R.apply_bench_rotation(plan)["rotated"] is False


def test_rotation_keeps_newest_and_archives_oldest(tmp_path):
    p = tmp_path / "bench.jsonl"
    _write_lines(p, 100)
    plan = R.plan_bench_rotation(p, R.RetentionPolicy(max_bench_lines=30))
    assert plan.needed and plan.archive_lines == 70

    result = R.apply_bench_rotation(plan)
    assert result["rotated"] is True

    kept = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
    assert len(kept) == 30
    # The NEWEST 30 stayed — i.e. 70..99, not 0..29.
    assert kept[0]["i"] == 70 and kept[-1]["i"] == 99

    archived = [json.loads(x) for x
                in plan.archive_path.read_text().splitlines() if x.strip()]
    assert len(archived) == 70
    assert archived[0]["i"] == 0


def test_rotation_never_loses_a_record_across_repeats(tmp_path):
    """Two rotations in a row must still account for every record."""
    p = tmp_path / "bench.jsonl"
    _write_lines(p, 50)
    policy = R.RetentionPolicy(max_bench_lines=20)

    R.apply_bench_rotation(R.plan_bench_rotation(p, policy, stamp="a"))
    _append_lines(p, 30, start=50)  # more runs land, as append_run does
    plan2 = R.plan_bench_rotation(p, policy, stamp="a")
    R.apply_bench_rotation(plan2)

    live = [json.loads(x)["i"] for x in p.read_text().splitlines() if x.strip()]
    arch = [json.loads(x)["i"] for x
            in plan2.archive_path.read_text().splitlines() if x.strip()]

    assert len(live) == 20
    assert live == list(range(60, 80)), "the newest 20 should stay live"
    # Every record written is still somewhere, exactly once: the archive
    # was appended to across both rotations, not overwritten.
    assert sorted(arch) == list(range(60))
    assert sorted(live + arch) == list(range(80))
    assert not (set(live) & set(arch))


def test_missing_bench_file_is_a_no_op(tmp_path):
    plan = R.plan_bench_rotation(tmp_path / "nope.jsonl")
    assert plan.needed is False
    assert "does not exist" in plan.reason


# ── Against real git ────────────────────────────────────────────────

def _git(repo, *args, check=True):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, check=check)


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main", ".")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "T")
    (root / "f.txt").write_text("x\n")
    _git(root, "add", "f.txt")
    _git(root, "commit", "-qm", "init")
    monkeypatch.setattr(R, "_repo_root", lambda: root)
    return root


def test_real_prune_deletes_the_loser_and_spares_the_winner(repo, tmp_path):
    _git(repo, "branch", "autoresearch/loser")
    _git(repo, "branch", "autoresearch/winner")

    plan = R.PrunePlan(policy=R.DEFAULT_POLICY, current_branch="main")
    plan.decisions = [
        R.BranchDecision(branch="autoresearch/loser", status="loss",
                         head_short_sha="x", when_created=NOW.isoformat(),
                         prune=True, reason="old"),
        R.BranchDecision(branch="autoresearch/winner", status="win",
                         head_short_sha="x", when_created=NOW.isoformat(),
                         prune=True, reason="tampered"),
    ]
    result = R.apply_branch_prune(plan, receipt_path=tmp_path / "r.jsonl")

    refs = _git(repo, "branch", "--list", "autoresearch/*").stdout
    assert "loser" not in refs
    assert "winner" in refs
    assert result["deleted"] == ["autoresearch/loser"]


def test_pruned_branch_is_recoverable_from_its_receipt(repo, tmp_path):
    _git(repo, "branch", "autoresearch/gone")
    receipt = tmp_path / "r.jsonl"
    plan = R.PrunePlan(policy=R.DEFAULT_POLICY, current_branch="main")
    plan.decisions = [R.BranchDecision(
        branch="autoresearch/gone", status="loss", head_short_sha="x",
        when_created=NOW.isoformat(), prune=True, reason="old")]

    R.apply_branch_prune(plan, receipt_path=receipt)
    assert "gone" not in _git(repo, "branch", "--list",
                              "autoresearch/*").stdout

    row = json.loads(receipt.read_text().strip())
    # The documented recovery path, run verbatim.
    _git(repo, *row["restore_with"].split()[1:])
    assert "gone" in _git(repo, "branch", "--list", "autoresearch/*").stdout
