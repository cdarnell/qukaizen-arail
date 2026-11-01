"""arail.experiments.retention — bounding what the tuning loop leaves behind.

The loop is careful about each individual write and careless about the
pile. Every pass creates a baseline branch plus one branch per variant,
appends to a bench log, and prunes nothing, ever. Run it nightly for a
month and you have hundreds of refs and an unbounded JSONL — hazards H3
and H4 in docs/plans/autoresearch-integration.md.

Two deliberate design choices, both about not being clever with other
people's data:

1. **Planning is separate from applying.** ``plan_*`` reads and returns
   what *would* happen, with a reason attached to every decision, kept
   and pruned alike. Nothing in this module deletes anything unless the
   caller takes a plan and hands it back to ``apply_*``. The CLI
   defaults to printing the plan.

2. **A deletion is recorded before it happens.** ``apply_branch_prune``
   writes a receipt carrying each branch's head SHA *before* running
   ``git branch -D``, so a mistake is recoverable with
   ``git branch <name> <sha>`` for as long as the objects live. We
   delete refs, never objects — no ``gc``, no ``reflog expire``.

What is never pruned, regardless of policy:

    - the branch you are currently on
    - anything whose head commit reads as a **win** — wins are the
      entire point of running the loop
    - anything classified **unknown** or **running** — "unknown" means
      we could not parse the head commit, which is exactly the case
      where a human's own branch may be sitting in the namespace
    - the newest ``keep_recent`` branches, whatever their status
    - anything younger than ``min_age_days``
    - any ref outside ``autoresearch/`` (structurally: we only ever
      enumerate that namespace, and re-validate before deleting)

That leaves losing variants and superseded baselines, which is the pile
that actually grows.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from arail.experiments.branch_browser import (
    _BRANCH_RE,
    BranchSummary,
    list_autoresearch_branches,
)


# Statuses we refuse to prune under any policy. "win" is the product;
# "unknown"/"running" mean we don't understand the branch well enough to
# be deleting it.
PROTECTED_STATUSES = frozenset({"win", "unknown", "running"})

# Statuses that are eligible, once every other gate has passed.
PRUNABLE_STATUSES = frozenset({"loss", "baseline"})

RECEIPT_SCHEMA = "arail.autoresearch-prune/v1"


@dataclass(frozen=True)
class RetentionPolicy:
    """Conservative by construction: the defaults keep more than they
    remove, and every field only ever *narrows* what may be pruned."""

    keep_recent: int = 20
    min_age_days: int = 14
    max_bench_lines: int = 5000

    def validate(self) -> None:
        if self.keep_recent < 0:
            raise ValueError("keep_recent must be >= 0")
        if self.min_age_days < 0:
            raise ValueError("min_age_days must be >= 0")
        if self.max_bench_lines < 1:
            raise ValueError("max_bench_lines must be >= 1")


DEFAULT_POLICY = RetentionPolicy()


@dataclass
class BranchDecision:
    branch: str
    status: str
    head_short_sha: str
    when_created: str
    prune: bool
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "branch": self.branch,
            "status": self.status,
            "head_short_sha": self.head_short_sha,
            "when_created": self.when_created,
            "prune": self.prune,
            "reason": self.reason,
        }


@dataclass
class PrunePlan:
    policy: RetentionPolicy
    decisions: List[BranchDecision] = field(default_factory=list)
    current_branch: Optional[str] = None

    @property
    def to_prune(self) -> List[BranchDecision]:
        return [d for d in self.decisions if d.prune]

    @property
    def to_keep(self) -> List[BranchDecision]:
        return [d for d in self.decisions if not d.prune]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "policy": {
                "keep_recent": self.policy.keep_recent,
                "min_age_days": self.policy.min_age_days,
                "max_bench_lines": self.policy.max_bench_lines,
            },
            "current_branch": self.current_branch,
            "prune_count": len(self.to_prune),
            "keep_count": len(self.to_keep),
            "decisions": [d.to_dict() for d in self.decisions],
        }


# ── Time helpers ────────────────────────────────────────────────────

def _parse_iso(value: str) -> Optional[datetime]:
    """Parse git's iso-strict output. Returns None rather than raising —
    an unparseable date must make a branch *ineligible*, never eligible."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ── Branch retention ────────────────────────────────────────────────

def plan_branch_prune(
    policy: RetentionPolicy = DEFAULT_POLICY,
    *,
    now: Optional[datetime] = None,
    branches: Optional[Sequence[BranchSummary]] = None,
    current_branch: Optional[str] = None,
    limit: int = 1000,
) -> PrunePlan:
    """Decide what a prune *would* remove. Reads only.

    ``branches``/``now``/``current_branch`` are injectable so this is
    testable without a clock or a repo.
    """
    policy.validate()
    now = now or datetime.now(timezone.utc)
    if branches is None:
        branches = list_autoresearch_branches(backend="all", limit=limit)
    if current_branch is None:
        current_branch = _current_branch()

    # list_autoresearch_branches already sorts newest-first, but this
    # function's contract shouldn't silently depend on a caller-supplied
    # list being sorted — the keep_recent window has to mean "newest".
    ordered = sorted(
        branches,
        key=lambda b: (_parse_iso(b.when_created) or datetime.min.replace(
            tzinfo=timezone.utc)),
        reverse=True,
    )

    cutoff = now - timedelta(days=policy.min_age_days)
    plan = PrunePlan(policy=policy, current_branch=current_branch)

    for index, b in enumerate(ordered):
        decision = _decide(b, index, cutoff, policy, current_branch)
        plan.decisions.append(decision)
    return plan


def _decide(
    b: BranchSummary,
    index: int,
    cutoff: datetime,
    policy: RetentionPolicy,
    current_branch: Optional[str],
) -> BranchDecision:
    """Every gate is a *keep* gate. A branch is pruned only by falling
    through all of them, so a new failure mode defaults to keeping."""
    def keep(reason: str) -> BranchDecision:
        return BranchDecision(
            branch=b.branch, status=b.status,
            head_short_sha=b.head_short_sha,
            when_created=b.when_created, prune=False, reason=reason,
        )

    if not _BRANCH_RE.match(b.branch or ""):
        return keep("not a valid autoresearch/ branch name")
    if current_branch and b.branch == current_branch:
        return keep("currently checked out")
    if b.status in PROTECTED_STATUSES:
        return keep(f"status={b.status} is protected")
    if b.status not in PRUNABLE_STATUSES:
        return keep(f"status={b.status} is not in the prunable set")
    if index < policy.keep_recent:
        return keep(
            f"among the {policy.keep_recent} most recent "
            f"(#{index + 1})"
        )
    created = _parse_iso(b.when_created)
    if created is None:
        return keep("could not parse its creation date")
    if created > cutoff:
        return keep(f"younger than {policy.min_age_days}d")

    return BranchDecision(
        branch=b.branch, status=b.status,
        head_short_sha=b.head_short_sha,
        when_created=b.when_created, prune=True,
        reason=(
            f"status={b.status}, older than {policy.min_age_days}d, "
            f"and outside the {policy.keep_recent} most recent"
        ),
    )


def apply_branch_prune(
    plan: PrunePlan,
    *,
    receipt_path: Optional[Path] = None,
    runner=None,
) -> Dict[str, Any]:
    """Delete the branches the plan marked. Writes a recovery receipt
    for each *before* deleting it.

    Returns a summary dict. Never raises on an individual failure — one
    stubborn branch shouldn't abort the sweep — but reports every one.
    """
    run = runner or _run_git
    receipt_path = receipt_path or default_receipt_path()

    deleted: List[str] = []
    failed: List[Dict[str, str]] = []

    for d in plan.to_prune:
        # Re-validate at the point of deletion. The plan may have been
        # built minutes ago, or hand-edited, or come off a wire; this is
        # the last gate before an irreversible-ish operation.
        if not _BRANCH_RE.match(d.branch or ""):
            failed.append({"branch": d.branch, "error": "invalid branch name"})
            continue
        if d.status in PROTECTED_STATUSES:
            failed.append({"branch": d.branch,
                           "error": f"status={d.status} is protected"})
            continue
        if plan.current_branch and d.branch == plan.current_branch:
            failed.append({"branch": d.branch,
                           "error": "currently checked out"})
            continue

        sha = _full_sha(d.branch, run)
        # Receipt first. If we crash between the write and the delete we
        # have a harmless extra line; the other order loses the SHA.
        _append_receipt(receipt_path, {
            "schema": RECEIPT_SCHEMA,
            "branch": d.branch,
            "sha": sha or d.head_short_sha,
            "status": d.status,
            "when_created": d.when_created,
            "reason": d.reason,
            "restore_with": f"git branch {d.branch} {sha or d.head_short_sha}",
        })

        result = run(["branch", "-D", d.branch])
        if result.returncode == 0:
            deleted.append(d.branch)
        else:
            failed.append({
                "branch": d.branch,
                "error": (result.stderr or "").strip() or "git branch -D failed",
            })

    return {
        "deleted": deleted,
        "deleted_count": len(deleted),
        "failed": failed,
        "kept_count": len(plan.to_keep),
        "receipt_path": str(receipt_path),
    }


# ── Bench-log rotation ──────────────────────────────────────────────

@dataclass
class RotationPlan:
    path: Path
    total_lines: int
    keep_lines: int
    archive_lines: int
    archive_path: Optional[Path]
    needed: bool
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": str(self.path),
            "total_lines": self.total_lines,
            "keep_lines": self.keep_lines,
            "archive_lines": self.archive_lines,
            "archive_path": str(self.archive_path) if self.archive_path else None,
            "needed": self.needed,
            "reason": self.reason,
        }


def plan_bench_rotation(
    path: Path,
    policy: RetentionPolicy = DEFAULT_POLICY,
    *,
    stamp: str = "",
) -> RotationPlan:
    """Decide whether a bench JSONL needs rotating. Reads only."""
    policy.validate()
    if not path.exists():
        return RotationPlan(path=path, total_lines=0, keep_lines=0,
                            archive_lines=0, archive_path=None,
                            needed=False, reason="file does not exist")
    try:
        lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    except OSError as exc:
        return RotationPlan(path=path, total_lines=0, keep_lines=0,
                            archive_lines=0, archive_path=None,
                            needed=False, reason=f"unreadable: {exc}")

    total = len(lines)
    if total <= policy.max_bench_lines:
        return RotationPlan(path=path, total_lines=total, keep_lines=total,
                            archive_lines=0, archive_path=None, needed=False,
                            reason=(f"{total} lines is within the "
                                    f"{policy.max_bench_lines} limit"))

    archive_lines = total - policy.max_bench_lines
    suffix = stamp or "archive"
    archive_path = path.with_name(f"{path.stem}.{suffix}{path.suffix}")
    return RotationPlan(
        path=path, total_lines=total,
        keep_lines=policy.max_bench_lines,
        archive_lines=archive_lines, archive_path=archive_path, needed=True,
        reason=(f"{total} lines exceeds the {policy.max_bench_lines} limit; "
                f"the oldest {archive_lines} would move to the archive"),
    )


def apply_bench_rotation(plan: RotationPlan) -> Dict[str, Any]:
    """Move the oldest records out of the live log into a sibling
    archive, keeping the newest in place.

    Nothing is discarded — the archive is appended to, so repeated
    rotations accumulate there rather than overwrite. The live file is
    replaced atomically so a crash mid-rotation cannot truncate it.
    """
    if not plan.needed or plan.archive_path is None:
        return {"rotated": False, "reason": plan.reason}

    lines = [ln for ln in plan.path.read_text().splitlines() if ln.strip()]
    archive_part = lines[:plan.archive_lines]
    keep_part = lines[plan.archive_lines:]

    with open(plan.archive_path, "a") as fh:
        for ln in archive_part:
            fh.write(ln + "\n")

    tmp = plan.path.with_suffix(plan.path.suffix + ".tmp")
    tmp.write_text("".join(ln + "\n" for ln in keep_part))
    tmp.replace(plan.path)

    return {
        "rotated": True,
        "archived_lines": len(archive_part),
        "kept_lines": len(keep_part),
        "archive_path": str(plan.archive_path),
        "path": str(plan.path),
    }


def bench_paths() -> List[Path]:
    """The two bench logs, resolved through config so tests and
    alternate data roots work."""
    from arail.config import DATA_DIR
    return [DATA_DIR / "aerollm-bench.jsonl", DATA_DIR / "mlx-bench.jsonl"]


def default_receipt_path() -> Path:
    from arail.config import DATA_DIR
    return DATA_DIR / "autoresearch-pruned.jsonl"


# ── git plumbing (small, local, read-mostly) ────────────────────────

def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent


def _run_git(args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args, cwd=_repo_root(),
        capture_output=True, text=True, check=False,
    )


def _current_branch(runner=None) -> str:
    run = runner or _run_git
    r = run(["rev-parse", "--abbrev-ref", "HEAD"])
    return (r.stdout or "").strip()


def _full_sha(branch: str, runner=None) -> Optional[str]:
    run = runner or _run_git
    r = run(["rev-parse", branch])
    sha = (r.stdout or "").strip()
    return sha or None


def _append_receipt(path: Path, row: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        # A receipt we cannot write is a reason not to delete, but the
        # caller decides that; surface it by re-raising.
        raise


# ── CLI (./arailctl autoresearch …) ─────────────────────────────────

def _fmt_plan(plan: PrunePlan, *, verbose: bool) -> List[str]:
    out: List[str] = []
    out.append(f"branches   : {len(plan.decisions)} in autoresearch/")
    out.append(f"prunable   : {len(plan.to_prune)}")
    out.append(f"protected  : {len(plan.to_keep)}")
    out.append(
        f"policy     : keep_recent={plan.policy.keep_recent} "
        f"min_age_days={plan.policy.min_age_days}"
    )
    if plan.to_prune:
        out.append("")
        out.append("would prune:")
        for d in plan.to_prune:
            out.append(f"  - {d.branch}  [{d.status}]  {d.reason}")
    if verbose and plan.to_keep:
        out.append("")
        out.append("kept:")
        for d in plan.to_keep:
            out.append(f"  · {d.branch}  [{d.status}]  {d.reason}")
    return out


def _cli(argv: Optional[List[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="arailctl autoresearch",
        description=(
            "Bound what the tuning loop leaves behind: experiment "
            "branches and bench logs. Dry-run by default."
        ),
    )
    sub = ap.add_subparsers(dest="op", required=True)

    p_prune = sub.add_parser(
        "prune",
        help="remove old losing/superseded autoresearch/* branches",
    )
    p_prune.add_argument("--apply", action="store_true",
                         help="actually delete (default: show the plan only)")
    p_prune.add_argument("--dry-run", action="store_true",
                         help="explicit no-op form of the default")
    p_prune.add_argument("--keep-recent", type=int,
                         default=DEFAULT_POLICY.keep_recent,
                         help="never prune the N newest branches")
    p_prune.add_argument("--older-than-days", type=int,
                         default=DEFAULT_POLICY.min_age_days,
                         help="never prune anything younger than N days")
    p_prune.add_argument("--verbose", action="store_true",
                         help="also list what is kept, and why")
    p_prune.add_argument("--json", action="store_true",
                         help="emit the plan as JSON")

    p_rot = sub.add_parser(
        "rotate", help="archive the oldest records out of the bench logs",
    )
    p_rot.add_argument("--apply", action="store_true",
                       help="actually rotate (default: show the plan only)")
    p_rot.add_argument("--dry-run", action="store_true",
                       help="explicit no-op form of the default")
    p_rot.add_argument("--max-lines", type=int,
                       default=DEFAULT_POLICY.max_bench_lines,
                       help="how many of the newest records to keep in place")
    p_rot.add_argument("--json", action="store_true",
                       help="emit the plan as JSON")

    args = ap.parse_args(argv)

    if getattr(args, "apply", False) and getattr(args, "dry_run", False):
        print("--apply and --dry-run are mutually exclusive")
        return 2

    try:
        policy = RetentionPolicy(
            keep_recent=getattr(args, "keep_recent",
                                DEFAULT_POLICY.keep_recent),
            min_age_days=getattr(args, "older_than_days",
                                 DEFAULT_POLICY.min_age_days),
            max_bench_lines=getattr(args, "max_lines",
                                    DEFAULT_POLICY.max_bench_lines),
        )
        policy.validate()
    except ValueError as exc:
        print(f"invalid policy: {exc}")
        return 2

    if args.op == "prune":
        return _cli_prune(args, policy)
    if args.op == "rotate":
        return _cli_rotate(args, policy)
    return 2


def _cli_prune(args, policy: RetentionPolicy) -> int:
    plan = plan_branch_prune(policy)

    if args.json:
        print(json.dumps(plan.to_dict(), indent=2))
    else:
        for line in _fmt_plan(plan, verbose=args.verbose):
            print(line)

    if not args.apply:
        if not args.json:
            print("")
            print("dry run — nothing deleted. Re-run with --apply to delete.")
        return 0

    if not plan.to_prune:
        if not args.json:
            print("")
            print("nothing to prune.")
        return 0

    result = apply_branch_prune(plan)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("")
        print(f"deleted    : {result['deleted_count']}")
        print(f"receipts   : {result['receipt_path']}")
        if result["failed"]:
            print(f"failed     : {len(result['failed'])}")
            for f in result["failed"]:
                print(f"  ! {f['branch']}: {f['error']}")
        print("")
        print("Recover any of these with the restore_with line in the "
              "receipt file — the objects outlive the ref.")
    # Partial failure is degraded, not success: the pile is still there.
    return 3 if result["failed"] else 0


def _cli_rotate(args, policy: RetentionPolicy) -> int:
    plans = [plan_bench_rotation(p, policy) for p in bench_paths()]

    if args.json:
        print(json.dumps([p.to_dict() for p in plans], indent=2))
    else:
        for p in plans:
            print(f"{p.path.name:24s} {p.reason}")

    if not args.apply:
        if not args.json:
            print("")
            print("dry run — nothing moved. Re-run with --apply to rotate.")
        return 0

    results = [apply_bench_rotation(p) for p in plans if p.needed]
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        if not results:
            print("")
            print("nothing to rotate.")
        for r in results:
            print(f"archived {r['archived_lines']} records to "
                  f"{r['archive_path']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
