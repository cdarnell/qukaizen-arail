"""report.md from results.json. Every number is printed through ``metrics`` (FM21)."""
from __future__ import annotations

import json
from typing import Any, Dict, List

from arail.brain_bench import metrics

UNTRAINED = "UNTRAINED stand-in: plumbing only, never evidence about the house model"
ORDER = ["M1", "M8", "M2", "M2o", "M3", "M4", "M4s", "M4i", "M5", "M6", "M7"]


def arm_label(a: Dict[str, Any]) -> str:
    label = a.get("label") or a["id"]
    if "control-merge" in str(a.get("served_model", "")):
        label = f"{label} [{UNTRAINED}]"
    return label


def _short(d: Any) -> str:
    return (str(d)[:8] + "…") if d else "n/a"


def summary_line(res: Dict[str, Any]) -> str:
    v = res.get("verdict") or {}
    bad = [c["id"] for c in res.get("self_checks", []) if c["passed"] is False]
    if bad:
        return f"HARNESS NOT TRUSTWORTHY: NO VERDICT (failed: {', '.join(bad)})"
    if v.get("printed"):
        return f"verdict printed (PROPOSAL rules): P1 {v['p1']['advice']}, P2 {v['p2']['advice']}"
    return f"no verdict: {v.get('withheld_reason')}"


def render(res: Dict[str, Any], rules: Dict[str, Any]) -> str:
    """The whole report. Raises UnknownMetric if results carry a metric with no definition."""
    undefined = metrics.undefined_in(res)
    if undefined:
        raise metrics.UnknownMetric(",".join(undefined))
    L: List[str] = []
    arms = [a for a in res["arms"]]
    model_arms = [a for a in arms if a["id"] in ("baseline", "reference", "candidate") or a["id"].startswith("near_aa")]
    L += [f"# Buddy brain A/B report: {res['run_id']}", "",
          f"- git `{(res.get('git_sha') or '')[:8]}`{' (dirty)' if res.get('git_dirty') else ''}; tasks sha256 `{_short(res['tasks']['sha256'])}`"
          f"; rules `{res['rules']['status'].upper()}`; Ollama {res['ollama']['version']} (loopback only, $0, nothing downloaded)",
          f"- sampling: greedy, temperature 0, seed {res['sampling']['seed']}; num_predict {res['sampling']['num_predict']}",
          f"- complete: {res.get('complete')}; wall {res.get('wall_s', 0):.0f} s" + (f"; SUBSET RUN ({res['subset']} per category)" if res.get("subset") else ""),
          "", "| Arm | Tag | Weights | Manifest | Runner | Thinking | Label |", "|---|---|---|---|---|---|---|"]
    for a in arms:
        L.append(f"| {a['id']} | `{a.get('served_model')}` | `{_short(a.get('weights_sha256'))}` | `{_short(a.get('manifest_digest'))}` | "
                 f"{a.get('runner') or 'n/a'} | {'capable, think=false sent' if a.get('thinking_capable') else 'no'} | {arm_label(a)} |")
    L += ["", "## Self-checks", "", "| ID | Result | Expected | Observed |", "|---|---|---|---|"]
    for c in res["self_checks"]:
        r = {True: "pass", False: "FAIL", None: "not run"}[c["passed"]]
        L.append(f"| {c['id']} | {r} | {c['expected']} | {c['observed']}{(' - ' + c['detail']) if c.get('detail') else ''} |")
    if any(c["passed"] is False for c in res["self_checks"]):
        L += ["", "> **HARNESS NOT TRUSTWORTHY: NO VERDICT.** The numbers below are written for inspection only."]
    L += ["", "## Metrics, side by side", ""]
    head = ["Metric", "Unit / direction"] + [a["id"] for a in model_arms] + ["Meaning and why"]
    L += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for mid in ORDER:
        if not any(mid in (res["metrics"].get(a["id"]) or {}) for a in model_arms):
            continue
        d = metrics.define(mid)
        cells = [metrics.fmt_value(mid, res["metrics"][a["id"]][mid]) if mid in (res["metrics"].get(a["id"]) or {}) else "n/a"
                 for a in model_arms]
        why = d["meaning"] + " " + d["rationale"]
        if mid == "M8":
            why += " Contamination unknown: the pin carries no held-out terms."
        L.append(f"| **{mid}** {d['label']} | {d['unit']}; {d['direction']} | " + " | ".join(cells) + f" | {why} |")
    L += ["", "### Deltas (paired bootstrap, 10,000 resamples)", "",
          "| Metric | A to B | Delta | CI | Level | Rule | Threshold | Meets |", "|---|---|---|---|---|---|---|---|"]
    for c in res["comparisons"]:
        d = metrics.define(c["metric_id"])
        ci = "n/a" if c["ci"] is None else f"{c['ci'][0]:+.1f} to {c['ci'][1]:+.1f} pp"
        dp = "n/a" if c["delta_pp"] is None else f"{c['delta_pp']:+.1f} pp"
        L.append(f"| {c['metric_id']} {d['label']} | {c['a']} to {c['b']}{' (near-A/A)' if c.get('near_aa') else ''} | {dp} | {ci} | "
                 f"{100 * c['ci_level']:.2f}% | {c.get('rule_id', '')} | {c.get('threshold', '')} | {c.get('meets', '')} |")
    L += ["", "## Promotion rules (PROPOSAL, not decided)", ""]
    v = res["verdict"]
    if v["printed"]:
        L += [f"- P1 (your own pin): **{v['p1']['advice']}**, decided by {v['p1']['decided_by']}",
              f"- P2 (blueprint default): **{v['p2']['advice']}**, decided by {v['p2']['decided_by']}", ""]
    else:
        L += [f"No verdict: {v['withheld_reason']}.", ""]
    L += ["| Rule | Status | Detail | Why |", "|---|---|---|---|"]
    why = {r["id"]: r["rationale"] for r in rules["rules"]}
    for r in v["rules"]:
        L.append(f"| {r['rule_id']} | {r['status']} | {r['detail']} | {why.get(r['rule_id'], '')} |")
    L += ["", "## Where the failures are", ""]
    by_cat: Dict[str, int] = {}
    for f in res["failures"]:
        k = f"{f['arm']} / {f['category']} / {f['mode']}"
        by_cat[k] = by_cat.get(k, 0) + 1
    L += [f"- {k}: {n}" for k, n in sorted(by_cat.items())] or ["- none"]
    L += ["", "| Task | Arm | Mode | Check | Excerpt |", "|---|---|---|---|---|"]
    for f in res["failures"][:60]:
        ex = f["excerpt"].replace("|", "/").replace("\n", " ")
        L.append(f"| {f['task_id']} | {f['arm']} | {f['mode']} | {f['check']} | {ex} |")
    if len(res["failures"]) > 60:
        L.append(f"| ... | | | | {len(res['failures']) - 60} more in rows.jsonl |")
    L += ["", "## Latency and memory", "", "| Arm | Calls | TTFT p50 | TTFT p95 | Total p50 | Total p95 | Tokens out p50 |", "|---|---|---|---|---|---|---|"]
    for k, x in res["latency"].items():
        f = lambda v: "n/a" if v is None else f"{v:.0f}"  # noqa: E731
        L.append(f"| {k} | {x['calls']} | {f(x['ttft_p50_ms'])} ms | {f(x['ttft_p95_ms'])} ms | {f(x['total_p50_ms'])} ms | "
                 f"{f(x['total_p95_ms'])} ms | {f(x['tokens_out_p50'])} |")
    L += ["", "## What this run cannot tell you", "",
          "- Domain scores are labelled contamination unknown until the pin carries held-out terms.",
          "- No judge: only code-checkable behaviour is graded (O4). Lab-state and multi-turn questions are not included.",
          "- The template check approximates Buddy's `usable_sentence` (no polarity check).",
          "- Buddy's own `finalize` safety net is not applied: this grades the brain before it.",
          "- Greedy decoding stands in for production sampling.",
          "- Stock and merged arms may use different runners (ggml vs llamacpp); latency is not like-for-like across them."]
    for w in res.get("warnings") or []:
        L.append(f"- warning: {w}")
    return "\n".join(L) + "\n"


def render_file(path: str) -> str:
    from arail.brain_bench import tasks as T
    res = json.loads(open(path, encoding="utf-8").read())
    rules = json.loads(T.RULES_PATH.read_text())
    return render(res, rules)
