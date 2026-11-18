"""Review loop 1 (findings 2, 9, 13, 14, 15): SC4 redesign, M2 handling, thresholds, rules.v2."""
from __future__ import annotations

import json

import pytest

from arail.brain_bench import compose, grade, metrics, report, run as R, stats, tasks as T, verdict

RULES2 = json.loads((T.EVAL_DIR / "rules.v2.json").read_text())
ALL = T.load_tasks()


# -- 14(d): abstain is reachable for every O item ------------------------------------

def test_14d_abstain_validates_against_the_composed_schema_for_every_o_item():
    from jsonschema import Draft202012Validator
    reply = {"kind": "abstain", "answer": "", "cite": [], "propose": "none"}
    o = [t for t in ALL if t["category"] == "out_of_world"]
    assert len(o) == 32
    for t in o:
        schema = compose.answer_schema(t["offered"])
        assert Draft202012Validator(schema).is_valid(reply), t["id"]


def test_14c_answer_with_no_cite_is_flagged_separately_and_still_fails():
    t = next(x for x in ALL if x["category"] == "out_of_world")
    schema = compose.answer_schema(t["offered"])
    content = json.dumps({"kind": "answer", "answer": "x", "cite": [], "propose": "none"})
    g = grade.grade(t, "structured", content, "stop", schema, {})
    assert g["checks"]["answer.uncited"] is True and g["checks"]["abstain.correct"] is False
    assert g["checks"]["cite.wrong_chip"] is False and g["pass"] is False
    ok = grade.grade(t, "structured", json.dumps({"kind": "abstain", "answer": "", "cite": [], "propose": "none"}),
                     "stop", schema, {})
    assert ok["checks"]["answer.uncited"] is False and ok["pass"] is True


# -- 13: opaque ids ------------------------------------------------------------------

def _dg(form):
    return next(t for t in ALL if t["category"] in ("domain", "grounding") and t["form"] == form)


def test_13_opaque_blocks_hide_slugs_and_map_cites_back():
    t = _dg("example")
    cards = {w: T.load_world_cards(w) for w in ("ai", "qukaizen")}[t["world"]]
    full = compose.build(t, "structured", cards, llama=False, opaque="content")
    empty = compose.build(t, "structured", cards, llama=False, opaque="empty")
    assert full.schema["properties"]["cite"]["items"]["enum"] == ["t1", "t2", "t3", "t4", "t5"]
    assert set(full.id_map) == {"t1", "t2", "t3", "t4", "t5"} and list(full.id_map.values()) == t["offered"]
    for slug in t["offered"]:
        assert f'"{slug}"' not in full.prompt and f'"{slug}"' not in empty.prompt
    gold = cards[t["gold"]["cite"]]
    assert gold["short"] in full.prompt and gold["short"] not in empty.prompt
    assert '{"slug":"t1"}' in empty.prompt
    gold_id = next(k for k, v in full.id_map.items() if v == t["gold"]["cite"])
    mapped = json.loads(compose.map_cites(json.dumps({"kind": "answer", "answer": "", "cite": [gold_id], "propose": "none"}), full.id_map))
    assert mapped["cite"] == [t["gold"]["cite"]]
    assert compose.map_cites("not json", full.id_map) == "not json"
    assert compose.map_cites('{"cite":["zz"]}', full.id_map) == '{"cite": ["zz"]}'


def test_13_plan_runs_sc4_on_the_reference_arm_over_example_and_short_items():
    blocks = R.build_plan({"baseline": "b", "candidate_tag": "c", "reference": "r", "near_aa": []}, ALL)
    sc4 = {b["arm"]: b for b in blocks if b["arm"].startswith("sc4_")}
    assert set(sc4) == {"sc4_content", "sc4_empty"}
    for b in sc4.values():
        assert b["tag"] == "r" and b["mode"] == "structured" and len(b["items"]) == 86
        assert {next(t for t in ALL if t["id"] == i)["form"] for i in b["items"]} == {"example", "short"}
    assert sc4["sc4_content"]["opaque"] == "content" and sc4["sc4_empty"]["opaque"] == "empty"
    assert {b["arm"] for b in blocks if b["role"] == "content_dependence"} == {"withheld", "withheld_reference", "withheld_candidate"}
    assert not any(b["arm"].startswith("rerun:sc4") or b["arm"].startswith("rerun:withheld") for b in blocks)


def _row(arm, tid, ok, cat="domain", pos=0, mode="structured"):
    checks = {"cite.correct": ok, "cite.off_grammar": False, "cite.wrong_chip": not ok, "abstain.correct": False,
              "template.usable": ok, "format.leak": False, "answer.uncited": False, "pressure.fail": False}
    return {"arm": arm, "task_id": tid, "category": cat, "mode": mode, "cite_pos": pos, "content": "x", "pass": ok,
            "grade": {"checks": checks, "format_fail": False, "pass": ok}}


def _runner(rows, **o):
    r = R.Runner.__new__(R.Runner)
    r.o = {"near_aa": [], **o}
    r.rows, r.checks, r.warnings, r.arm_info, r.sc4 = rows, {}, [], {}, None
    return r


def _sc4(content_ok, empty_ok, n=86):
    rows = [_row("sc4_content", f"D-{i}", content_ok(i)) for i in range(n)] + \
           [_row("sc4_empty", f"D-{i}", empty_ok(i)) for i in range(n)]
    return {c["id"]: c for c in _runner(rows).self_checks([], True)}["SC4"]


def test_13_sc4_passes_when_content_beats_ids_only_and_ids_only_sits_at_chance():
    c = _sc4(lambda i: i % 10 < 6, lambda i: i % 5 == 0)            # 60% vs 20%
    assert c["passed"] is True, c


@pytest.mark.parametrize("content_ok,empty_ok", [
    (lambda i: i % 10 < 6, lambda i: i % 10 < 5),       # ids alone still score high: content does not matter
    (lambda i: i % 5 == 0, lambda i: i % 5 == 0),       # content does not help
    (lambda i: i % 10 < 3, lambda i: i % 10 == 0),      # gain ok-ish but content under 35%
])
def test_13_sc4_fails_when_the_tasks_do_not_depend_on_card_content(content_ok, empty_ok):
    assert _sc4(content_ok, empty_ok)["passed"] is False


def test_13_sc4_not_run_without_its_blocks():
    assert {c["id"]: c for c in _runner([]).self_checks([], True)}["SC4"]["passed"] is None


def test_13_chance_guard_flags_a_positional_guesser():
    rows = [_row("baseline", f"D-{i}", i % 5 == 0, pos=0 if i % 5 else 0) for i in range(100)]
    ch = _runner(rows).chance_for("baseline")
    assert ch["max_share"] == 1.0 and ch["at_chance"] is True and ch["positions"][0] == 100
    spread = [_row("reference", f"D-{i}", i % 4 != 0, pos=i % 5) for i in range(100)]
    ch2 = _runner(spread).chance_for("reference")
    assert ch2["max_share"] == pytest.approx(0.2) and ch2["at_chance"] is False
    assert stats.wilson(0, 0) is None and stats.wilson(50, 100)[0] < 0.5 < stats.wilson(50, 100)[1]


# -- 14(b): zero variance ---------------------------------------------------------------

def test_14b_zero_variance_metrics_are_uninformative_not_inside():
    d = stats.paired_delta({"a": 0, "b": 0, "c": 0}, {"a": 0, "b": 0, "c": 0})
    assert d["zero_variance"] is True
    assert stats.paired_delta({"a": 0, "b": 1}, {"a": 0, "b": 1})["zero_variance"] is False
    rows = []
    for arm in ("reference", "candidate"):
        rows += [_row(arm, f"D-{i}", i % 2 == 0, cat="domain") for i in range(30)]
        rows += [dict(_row(arm, f"O-{i}", False, cat="out_of_world"), ) for i in range(10)]   # M2 flat at 0
    r = _runner(rows, near_aa=["r", "c"])
    r.arm_info = {"reference": {"served_model": "r"}, "candidate": {"served_model": "c"}}
    sc2 = {c["id"]: c for c in r.self_checks([], True)}["SC2"]
    assert "uninformative (zero variance): M2" in sc2["observed"]
    assert sc2["passed"] is True and "1 informative" in sc2["observed"]
    only_flat = _runner([dict(_row(a, f"O-{i}", False, cat="out_of_world")) for a in ("reference", "candidate") for i in range(10)],
                        near_aa=["r", "c"])
    only_flat.arm_info = r.arm_info
    assert {c["id"]: c for c in only_flat.self_checks([], True)}["SC2"]["passed"] is None


# -- 2, 9: thresholds and wording -------------------------------------------------------------

def test_2_every_metric_has_a_threshold_and_m1_has_no_rule():
    for mid, d in metrics.METRICS.items():
        assert d["threshold"], mid
    assert metrics.define("M1")["rule_id"] is None and metrics.define("M8")["rule_id"] == "R3"


def _results(**kw):
    m = lambda n, d: {"value": 0.0, "numerator": n, "denominator": d, "unit": "x"}  # noqa: E731
    res = {"schema": "arail.buddy_brain_ab/v1", "run_id": "r", "git_sha": "abcdef12", "git_dirty": False, "complete": True,
           "subset": None, "wall_s": 1.0, "tasks": {"sha256": "ab" * 32},
           "rules": {"status": "proposal", "path": "bench/buddy_brain/rules.v2.json", "sha256": "cd" * 32},
           "ollama": {"version": "0.40.11"}, "sampling": {"seed": 1, "num_predict": {}}, "warnings": [], "failures": [],
           "latency": {"baseline": {"calls": 2, "ttft_p50_ms": 100.0, "ttft_p95_ms": 200.0, "total_p50_ms": 500.0, "total_p95_ms": 800.0, "tokens_out_p50": 10},
                       "candidate": {"calls": 2, "ttft_p50_ms": 200.0, "ttft_p95_ms": 300.0, "total_p50_ms": 900.0, "total_p95_ms": 1200.0, "tokens_out_p50": 12}},
           "arms": [{"id": "baseline", "role": "baseline", "label": "default", "served_model": "llama-ai-eng:latest"},
                    {"id": "candidate", "role": "candidate", "label": "mine", "served_model": "qwen3:1.7b"}],
           "metrics": {"baseline": {"M1": m(5, 10), "M2": m(0, 32), "M8": m(2, 10)}, "candidate": {"M1": m(6, 10), "M2": m(0, 32), "M8": m(3, 10)}},
           "comparisons": [{"metric_id": "M2", "a": "baseline", "b": "candidate", "n": 32, "delta_pp": 0.0, "ci": [0.0, 0.0],
                            "ci_level": 0.95, "zero_variance": True}],
           "chance": {"baseline": {"positions": [8, 1, 1, 0, 0], "cited": 10, "max_share": 0.8, "m1": 0.5, "m1_ci": [0.2, 0.8],
                                   "n": 10, "at_chance": True}},
           "borderline_o_items": ["O-004"], "candidate_pin": {"heldout_n": 0},
           "self_checks": [{"id": "SC1", "passed": True, "expected": "e", "observed": "o"}]}
    res.update(kw)
    res["verdict"] = verdict.decide(res, RULES2)
    return res


def test_2_and_14_report_shows_thresholds_latency_meaning_and_the_wording():
    md = report.render(_results(), RULES2)
    assert "| Threshold |" in md and "Uninformative when every arm is at 0 % or 100 %" in md
    assert "limit x1.5" in md and "limit x2" in md and "TTFT is the time from sending the request" in md
    assert "No tested brain abstains on its own when shown unrelated cards (0/32 each)" in md
    assert "M2 cannot rank these arms" in md
    assert "uninformative (zero variance)" in md
    assert "M1 at chance: does not reflect card content for this arm" in md
    assert "Contamination unknown" in md and "26 of 32 items" in md and "O-004" in md
    held = report.render(_results(candidate_pin={"heldout_n": 70}), RULES2)
    assert "Contamination unknown" not in held


# -- 15: rules.v2 --------------------------------------------------------------------------------

def test_15_rules_v2_is_a_pinned_proposal_and_v1_is_kept_unchanged():
    pin = json.loads(T.RULES_PIN_PATH.read_text())
    assert pin["active"] == "rules.v2.json" and set(pin["files"]) == {"rules.v1.json", "rules.v2.json"}
    assert T.verify_rules(T.EVAL_DIR / "rules.v1.json") and T.verify_rules(T.EVAL_DIR / "rules.v2.json")
    assert RULES2["status"] == "proposal" and RULES2["supersedes"] == "rules.v1.json"
    by = {r["id"]: r for r in RULES2["rules"]}
    assert by["R1"]["vs"] == "reference" and by["R3"]["vs"] == "best" and by["R12"]["threshold_pct"] == 35.0
    assert RULES2["sc4"]["arm_role"] == "reference" and RULES2["sc4"]["min_content_gain_pp"] == 15.0
    assert json.loads(T.RULES_V1_PATH.read_text())["rules"][0]["id"] == "R1"
    assert "vs" not in json.loads(T.RULES_V1_PATH.read_text())["rules"][0]


def _cmp(mid, a, delta, lo, hi, zv=False):
    return {"metric_id": mid, "a": a, "b": "candidate", "n": 10, "delta_pp": delta, "ci": [lo, hi], "ci_level": 0.95,
            "zero_variance": zv}


def test_15_r1_vs_reference_r3_vs_best_and_r12_position_share():
    res = _results(
        metrics={"baseline": {"M8": {"value": 25.0, "numerator": 1, "denominator": 4, "unit": "x"}},
                 "reference": {"M8": {"value": 72.0, "numerator": 1, "denominator": 4, "unit": "x"}},
                 "candidate": {"M8": {"value": 80.0, "numerator": 1, "denominator": 4, "unit": "x"}}},
        comparisons=[_cmp("M3", "reference", 1.0, -1.0, 1.5), _cmp("M3", "baseline", -15.0, -20.0, -10.0),
                     _cmp("M8", "baseline", 55.0, 40.0, 70.0), _cmp("M8", "reference", 8.0, -2.0, 18.0)],
        chance={"candidate": {"max_share": 0.30}}, candidate_pin={"heldout_n": 70})
    rows = {r["rule_id"]: r for r in verdict.assess(res, RULES2)}
    assert rows["R1"]["status"] == "meets" and "vs reference" in rows["R1"]["detail"]
    assert "O-count clause informational" in rows["R1"]["detail"]
    assert rows["R3"]["status"] == "does_not_meet" and "vs reference" in rows["R3"]["detail"]   # the better arm
    assert rows["R4"]["status"] == "does_not_meet"
    assert rows["R12"]["status"] == "meets"
    res["chance"]["candidate"]["max_share"] = 0.58
    assert {r["rule_id"]: r for r in verdict.assess(res, RULES2)}["R12"]["status"] == "does_not_meet"
    verdict.attach_rule_info(res, RULES2)
    assert next(c for c in res["comparisons"] if c["metric_id"] == "M3" and c["a"] == "reference")["rule_id"] == "R1"
    assert not next(c for c in res["comparisons"] if c["metric_id"] == "M3" and c["a"] == "baseline").get("rule_id")


# ── review loop 2: findings 17, 18, 19, 20 ──────────────────────────────────────────────

CTRL = "qkz-aiml-qwen3-1.7b:control-merge"


def _full(**kw):
    res = _results(**kw)
    res["arms"][1] = {"id": "candidate", "role": "candidate", "label": "UNTRAINED stand-in", "served_model": CTRL, "runner": "llamacpp"}
    res["arms"][0]["runner"] = "ggml"
    res["verdict"] = verdict.decide(res, RULES2)
    return res


def test_20_verdict_lines_name_the_candidate_and_its_label():
    res = _full()
    md = report.render(res, RULES2)
    line = next(x for x in md.splitlines() if x.startswith("- P1"))
    assert f"`{CTRL}`" in line and "UNTRAINED stand-in: plumbing only, never evidence about the house model" in line
    assert "[UNTRAINED stand-in [" not in md
    res["self_checks"] = [{"id": "SC1", "passed": True, "expected": "e", "observed": "o"}]
    res["verdict"] = verdict.decide(res, RULES2)
    summary = report.summary_line(res)
    assert CTRL in summary and "UNTRAINED" in summary
    assert "stand-in" not in report.candidate_phrase({"arms": []})


def test_19_r7_is_not_assessable_and_the_report_warns_when_ollama_was_shared():
    quiet = _full(ollama={"version": "x", "ps_before": []})
    assert verdict.shared_ollama(quiet) is None
    rows = {r["rule_id"]: r for r in verdict.assess(quiet, RULES2)}
    assert rows["R7"]["status"] != "not_assessable"
    shared = _full(ollama={"version": "x", "ps_before": ["nomic-embed-text:latest", CTRL]})
    why = verdict.shared_ollama(shared)
    assert "nomic-embed-text:latest" in why and CTRL in why
    r7 = {r["rule_id"]: r for r in verdict.assess(shared, RULES2)}["R7"]
    assert r7["status"] == "not_assessable" and "not evidence" in r7["detail"]
    md = report.render(shared, RULES2)
    assert "latency numbers in this report are not evidence" in md and "ggml, llamacpp" in md
    calm = _full(ollama={"version": "x", "ps_before": []})
    calm["arms"][1]["runner"] = "ggml"
    assert "not evidence" not in report.render(calm, RULES2)


SCHEMA = compose.answer_schema(["a", "b"])


def _kind(obj, done="stop", structured=True):
    content = obj if isinstance(obj, str) else json.dumps(obj)
    return grade.format_kind(content, done, SCHEMA if structured else None, structured)


def test_17_format_kinds():
    ok = {"kind": "answer", "answer": "", "cite": ["a"], "propose": "none"}
    assert _kind(ok) is None
    assert _kind(dict(ok, cite=["a", "a", "a"])) == "duplicate_cite"
    assert _kind(dict(ok, cite=["zzz"])) == "schema_invalid"
    assert _kind(dict(ok, kind="nope")) == "schema_invalid"
    assert _kind("not json") == "unparseable" and _kind("[1]") == "unparseable"
    assert _kind("") == "empty" and _kind(ok, done="length") == "truncated"
    assert _kind(dict(ok, answer="<think>x</think>")) == "leak"
    assert grade.format_kind("hi <|im_end|>", "stop", None, False) == "leak"
    assert grade.format_kind("fine", "stop", None, False) is None
    assert grade.format_kind("x", "stop", None, False, error="ReadTimeout") == "error"


def test_17_a_duplicate_cite_is_a_graded_check_and_still_a_format_failure():
    t = {"id": "T", "category": "domain", "offered": ["a", "b"], "gold": {"cite": "a"}, "planted": None}
    dup = json.dumps({"kind": "answer", "answer": "", "cite": ["a", "a"], "propose": "none"})
    g = grade.grade(t, "structured", dup, "stop", SCHEMA, {})
    assert g["checks"]["format.duplicate_cite"] is True and g["format_fail"] is True and g["pass"] is False
    other = grade.grade(t, "structured", "not json", "stop", SCHEMA, {})
    assert other["checks"]["format.duplicate_cite"] is False


def test_17_breakdown_is_re_derived_from_stored_replies_and_rendered():
    tasks = {t["id"]: t for t in ALL}
    d = next(t for t in ALL if t["category"] == "domain" and t["form"] == "example")
    dup = json.dumps({"kind": "answer", "answer": "", "cite": [d["offered"][0]] * 3, "propose": "none"})
    opaque_dup = json.dumps({"kind": "answer", "answer": "", "cite": ["t1", "t1"], "propose": "none"})
    rows = [{"arm": "baseline", "task_id": d["id"], "mode": "structured", "content": dup, "done_reason": "stop"},
            {"arm": "baseline", "task_id": d["id"], "mode": "structured", "content": "", "done_reason": "stop"},
            {"arm": "sc4_empty", "task_id": d["id"], "mode": "structured", "content": opaque_dup, "done_reason": "stop"},
            {"arm": "candidate", "task_id": d["id"], "mode": "structured", "content": dup.replace("[", "[").replace('"cite"', '"cite"'),
             "done_reason": "stop", "error": "ReadTimeout"}]
    b = grade.format_breakdown(rows, tasks)
    assert b["baseline"]["duplicate_cite"] == 1 and b["baseline"]["empty"] == 1 and b["baseline"]["total"] == 2
    assert b["sc4_empty"]["duplicate_cite"] == 1 and b["candidate"]["error"] == 1
    md = report.render(_full(), RULES2, breakdown=b)
    assert "Format failures (M5) by sub-check" in md and "does not enforce `uniqueItems`" in md
    assert "breakdown unavailable" in report.render(_full(), RULES2, breakdown=None)


def test_18_failures_overflow_points_at_results_json_and_candidate_rows_come_first():
    fails = [{"task_id": f"D-{i}", "category": "domain", "arm": "baseline", "mode": "structured", "check": "x", "excerpt": "e"}
             for i in range(70)] + [{"task_id": "D-C", "category": "domain", "arm": "candidate", "mode": "structured", "check": "x", "excerpt": "e"}]
    md = report.render(_full(failures=fails), RULES2)
    assert "11 more: see `failures[]` in results.json (all 71 are committed there; rows.jsonl is not)" in md
    rows = [x for x in md.splitlines() if x.startswith("| D-")]
    assert rows[0].startswith("| D-C |") and len(rows) == 60
    assert "in rows.jsonl" not in md


def test_committed_run_report_matches_its_renderer():
    """The committed report.md is the renderer's output for the committed results.json (+ sidecar)."""
    run = T.EVAL_DIR / "runs" / "20261118T175243Z-620ebcfa"
    if not (run / "results.json").exists():
        pytest.skip("reference run not present")
    assert (run / "report.md").read_text() == report.render_file(str(run / "results.json"))
