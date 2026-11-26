"""``python -m arail.brain_bench run|verify-tasks|report`` (see docs/buddy-brain-bench.md)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from arail.brain_bench import run as R
from arail.brain_bench import tasks as T


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["_child"]:
        return R.child_main(args[1])
    ap = argparse.ArgumentParser(prog="python -m arail.brain_bench")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="run the A/B")
    r.add_argument("--baseline", default="llama-ai-eng:latest")
    r.add_argument("--candidate", required=True, help="an Ollama tag, or pin:<id>")
    r.add_argument("--candidate-label", default=None)
    r.add_argument("--reference", required=True)
    r.add_argument("--near-aa", default="", help="two tags, comma separated: same weights by different routes")
    r.add_argument("--tasks", default=None)
    r.add_argument("--rules", default=None)
    r.add_argument("--out", default=None)
    r.add_argument("--max-minutes", type=float, default=R.DEFAULT_MAX_MINUTES)
    r.add_argument("--limit-per-category", type=int, default=None, help="smoke run: first N tasks per category (no verdict)")
    r.add_argument("--keep-root", action="store_true")
    r.add_argument("--dry-run", action="store_true")
    sub.add_parser("verify-tasks", help="hash check only")
    p = sub.add_parser("report", help="re-render report.md from results.json")
    p.add_argument("results")
    p.add_argument("--derive-breakdown", action="store_true",
                   help="first write format_breakdown.json beside results.json, re-derived from rows.jsonl")
    try:
        ns = ap.parse_args(args)
    except SystemExit as exc:
        return R.EXIT_USAGE if exc.code else 0
    if ns.cmd == "verify-tasks":
        try:
            man = T.verify()
        except T.TaskError as exc:
            print(f"refused: {exc.code}", file=sys.stderr)
            return R.EXIT_REFUSED
        print(f"tasks ok: {man['counts']}")
        return R.EXIT_OK
    if ns.cmd == "report":
        from arail.brain_bench import report
        if ns.derive_breakdown:
            print("wrote " + report.write_breakdown_sidecar(ns.results), file=sys.stderr)
        sys.stdout.write(report.render_file(ns.results))
        return R.EXIT_OK
    if ns.cmd == "run":
        near = [t for t in ns.near_aa.split(",") if t]
        if near and len(near) != 2:
            print("usage: --near-aa takes exactly two tags", file=sys.stderr)
            return R.EXIT_USAGE
        return R.run({"baseline": ns.baseline, "candidate": ns.candidate, "candidate_label": ns.candidate_label,
                      "reference": ns.reference, "near_aa": near, "tasks": ns.tasks and Path(ns.tasks),
                      "rules": ns.rules, "out": ns.out, "max_minutes": ns.max_minutes,
                      "limit_per_category": ns.limit_per_category, "keep_root": ns.keep_root, "dry_run": ns.dry_run})
    ap.print_usage(sys.stderr)
    return R.EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
