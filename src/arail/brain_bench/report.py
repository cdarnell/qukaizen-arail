"""report.md from results.json. Every number is printed through ``metrics`` (FM21)."""
from __future__ import annotations

import json
from typing import Any, Dict, List

from arail.brain_bench import metrics

UNTRAINED = "UNTRAINED stand-in: plumbing only, never evidence about the house model"
ORDER = ["M1", "M8", "M2", "M2o", "M2u", "M3", "M4", "M4s", "M4i", "M5", "M6", "M7", "M9", "M10"]


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
    head = ["Metric", "Unit / direction", "Threshold"] + [a["id"] for a in model_arms] + ["Meaning and why"]
    L += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    heldout = (res.get("candidate_pin") or {}).get("heldout_n") or 0
    for mid in ORDER:
        if not any(mid in (res["metrics"].get(a["id"]) or {}) for a in model_arms):
            continue
        d = metrics.define(mid)
        cells = [metrics.fmt_value(mid, res["metrics"][a["id"]][mid]) if mid in (res["metrics"].get(a["id"]) or {}) else "n/a"
                 for a in model_arms]
        why = d["meaning"] + " " + d["rationale"]
        if mid == "M8" and not heldout:
            why += " Contamination unknown: the pin carries no held-out terms."
        if mid == "M2":
            zero = all((res["metrics"].get(a["id"]) or {}).get("M2", {"numerator": 1})["numerator"] == 0 for a in model_arms
                       if "M2" in (res["metrics"].get(a["id"]) or {}))
            if zero:
                n = next(v["M2"]["denominator"] for v in res["metrics"].values() if "M2" in v)
                why += (f" No tested brain abstains on its own when shown unrelated cards (0/{n} each). Buddy's abstain "
                        "therefore depends on code: retrieval must withhold cards. M2 cannot rank these arms.")
        L.append(f"| **{mid}** {d['label']} | {d['unit']}; {d['direction']} | {d['threshold']} | " + " | ".join(cells) + f" | {why} |")
    ch = res.get("chance") or {}
    if ch:
        L += ["", "### Chance guard: where the first cite lands (D+G, structured; uniform gold is 20% per slot)", "",
              "| Arm | Slot 0 | Slot 1 | Slot 2 | Slot 3 | Slot 4 | Largest share | M1 and 95% CI | Reading |", "|---|---|---|---|---|---|---|---|---|"]
        for arm, c in ch.items():
            if not c:
                continue
            ci = "n/a" if not c["m1_ci"] else f"{100 * c['m1']:.1f}% ({100 * c['m1_ci'][0]:.1f} to {100 * c['m1_ci'][1]:.1f})"
            share = "n/a" if c["max_share"] is None else f"{100 * c['max_share']:.1f}%"
            read = "M1 at chance: does not reflect card content for this arm" if c["at_chance"] else "above chance"
            L.append(f"| {arm} | " + " | ".join(str(x) for x in c["positions"]) + f" | {share} | {ci} | {read} |")
    L += ["", "### Deltas (paired bootstrap, 10,000 resamples)", "",
          "| Metric | A to B | Delta | CI | Level | Rule | Threshold | Meets |", "|---|---|---|---|---|---|---|---|"]
    for c in res["comparisons"]:
        d = metrics.define(c["metric_id"])
        ci = "n/a" if c["ci"] is None else f"{c['ci'][0]:+.1f} to {c['ci'][1]:+.1f} pp"
        dp = "n/a" if c["delta_pp"] is None else f"{c['delta_pp']:+.1f} pp"
        L.append(f"| {c['metric_id']} {d['label']} | {c['a']} to {c['b']}{' (near-A/A)' if c.get('near_aa') else ''} | {dp} | {ci} | "
                 f"{100 * c['ci_level']:.2f}% | {c.get('rule_id', '')} | {c.get('threshold', '')} | "
                 f"{'uninformative (zero variance)' if c.get('zero_variance') else c.get('meets', '')} |")
    L += ["", "## Promotion rules (PROPOSAL, not decided)", "",
          f"Rules file `{res['rules'].get('path', 'n/a')}` sha256 `{_short(res['rules'].get('sha256'))}` (hash-pinned before the run).", ""]
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
    L += ["", "## Latency and memory", "",
          "TTFT is the time from sending the request to the first non-empty token (measured by the client); total is the "
          "whole call. Threshold (R7, PROPOSAL, P2 required): the candidate's TTFT p50 is at most 1.5x the default's and its "
          "total p95 at most 2x, compared on the same runner type (a different runner is not like-for-like).", "",
          "| Arm | Calls | TTFT p50 | TTFT p95 | Total p50 | Total p95 | Tokens out p50 | TTFT p50 vs default | Total p95 vs default |",
          "|---|---|---|---|---|---|---|---|---|"]
    bl = res["latency"].get("baseline") or {}
    for k, x in res["latency"].items():
        f = lambda v: "n/a" if v is None else f"{v:.0f}"  # noqa: E731
        r1 = "n/a" if not (bl.get("ttft_p50_ms") and x.get("ttft_p50_ms")) else f"x{x['ttft_p50_ms'] / bl['ttft_p50_ms']:.2f} (limit x1.5)"
        r2 = "n/a" if not (bl.get("total_p95_ms") and x.get("total_p95_ms")) else f"x{x['total_p95_ms'] / bl['total_p95_ms']:.2f} (limit x2)"
        L.append(f"| {k} | {x['calls']} | {f(x['ttft_p50_ms'])} ms | {f(x['ttft_p95_ms'])} ms | {f(x['total_p50_ms'])} ms | "
                 f"{f(x['total_p95_ms'])} ms | {f(x['tokens_out_p50'])} | {r1 if k == 'candidate' else '-'} | {r2 if k == 'candidate' else '-'} |")
    L += ["", "## What this run cannot tell you", "",
          "- Domain scores are labelled contamination unknown until the pin carries held-out terms.",
          "- No judge: only code-checkable behaviour is graded (O4). Lab-state and multi-turn questions are not included.",
          "- The template check approximates Buddy's `usable_sentence` (no polarity check).",
          "- Buddy's own `finalize` safety net is not applied: this grades the brain before it.",
          "- Greedy decoding stands in for production sampling.",
          "- Stock and merged arms may use different runners (ggml vs llamacpp); latency is not like-for-like across them.",
          "- The out-of-World set is mostly 'an AI question offered qukaizen cards' (the `ai` World already holds a `qukaizen` category, "
          "so only a few qukaizen terms qualify as out-of-World questions against it): 26 of 32 items are that kind.",
          "- Borderline out-of-World items (an offered card is arguably related): "
          + (", ".join(res.get("borderline_o_items") or []) or "none listed") + "."]
    sc4 = res.get("sc4")
    if sc4:
        L.append(f"- SC4 (opaque ids, reference arm, {sc4['n']} items): card content {sc4['m1_content']:.1f}% vs ids only "
                 f"{sc4['m1_empty']:.1f}% (chance is 20%).")
    for w in res.get("warnings") or []:
        L.append(f"- warning: {w}")
    return "\n".join(L) + "\n"


def render_file(path: str) -> str:
    from arail.brain_bench import tasks as T
    res = json.loads(open(path, encoding="utf-8").read())
    name = (res.get("rules") or {}).get("path", "rules.v1.json").rsplit("/", 1)[-1]
    rules = json.loads((T.EVAL_DIR / name).read_text())
    return render(res, rules)
