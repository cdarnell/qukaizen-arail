"""The brain A/B runner: parent (validates, isolates, restores) and child (runs the arms).

Parent: checks loopback Ollama, arms, task hashes; snapshots the lab's mtimes and
Ollama residency; spawns the child with a scrubbed environment; then finalises
results.json and report.md. Child: binds each arm through the real pin-to-registry
code, drives ``brain.stream_turn`` greedily, grades in code and runs SC1-SC7."""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from arail import brain_bench, model_pins
from arail.brain_bench import compose, grade, isolation, metrics, ollama, report, stats, tasks as T, vendor_data, verdict

EXIT_OK, EXIT_UNTRUSTED, EXIT_REFUSED, EXIT_ABORTED, EXIT_USAGE = 0, 3, 4, 5, 2
SEED = stats.SEED
GREEDY = {"temperature": 0, "seed": SEED}
DEFAULT_MAX_MINUTES = 45.0
MAX_ARM_ERRORS = 5
MIN_CONTEXT = 4096


class Refused(Exception):
    """The run must not start. ``code`` is fixed text naming the check."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(T.REPO_ROOT), *args], text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except Exception:  # noqa: BLE001
        return ""


# ═══ plan ════════════════════════════════════════════════════════════════════

def resolve_candidate(spec: str) -> Tuple[str, Optional[str]]:
    """(tag, pin id). ``pin:<id>`` names the tag in the in-repo pin record; a pending record refuses."""
    if spec.startswith("pin:"):
        rec = model_pins.load().get(spec[4:])
        if rec is None or rec.state != "pinned" or not rec.ollama_tag:
            raise Refused("candidate_pin_not_released")
        return rec.ollama_tag, rec.id
    return spec, None


def build_plan(opts: Dict[str, Any], all_tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ordered blocks: {arm, tag, role, label, mode, items(task ids), fields}. Deterministic."""
    sub = opts.get("limit_per_category")
    if sub:
        seen: Dict[str, int] = {}
        keep = []
        for t in all_tasks:
            seen[t["category"]] = seen.get(t["category"], 0) + 1
            if seen[t["category"]] <= sub:
                keep.append(t)
        all_tasks = keep
    rng = random.Random(SEED)
    ids = [t["id"] for t in all_tasks]
    aa_ids = set(rng.sample(ids, max(1, round(len(ids) * 0.25)))) if ids else set()
    dg = [t["id"] for t in all_tasks if t["category"] in ("domain", "grounding")]
    base_tag, cand_tag = opts["baseline"], opts["candidate_tag"]
    arms: List[Tuple[str, str, str, str, Optional[List[str]], Optional[List[str]]]] = [
        ("baseline", base_tag, "baseline", "default", None, None),
        ("aa", base_tag, "aa", "default, rerun (A/A)", sorted(aa_ids), None),
        ("withheld", base_tag, "sensitivity", "default, cards show slug+term only", dg, ["slug", "term"]),
        ("reference", opts["reference"], "reference", "stock reference", None, None),
        ("candidate", cand_tag, "candidate", opts.get("candidate_label") or "candidate", None, None),
    ]
    have = {opts["reference"], cand_tag}
    for i, tag in enumerate(opts.get("near_aa") or []):
        if tag not in have:
            arms.append((f"near_aa_{i}", tag, "near_aa", "near-A/A arm", None, None))
            have.add(tag)
    blocks = []
    for arm, tag, role, label, only, fields in arms:
        for mode in (["structured"] if arm == "withheld" else ["structured", "template"]):
            items = [t["id"] for t in all_tasks if mode in t["modes"] and (only is None or t["id"] in only)]
            if items:
                blocks.append({"arm": arm, "tag": tag, "role": role, "label": label, "mode": mode,
                               "items": items, "fields": fields})
    # reruns: every model arm, seeded 10% of its (task, mode) pairs, after all other blocks
    for arm, tag, role, label, only, fields in arms:
        if role in ("aa", "sensitivity"):
            continue
        for mode in ("structured", "template"):
            pool = [t["id"] for t in all_tasks if mode in t["modes"]]
            if pool:
                k = max(1, round(len(pool) * 0.10))
                blocks.append({"arm": f"rerun:{arm}", "tag": tag, "role": "rerun", "label": f"{arm} rerun", "mode": mode,
                               "items": sorted(random.Random(SEED + len(blocks)).sample(pool, min(k, len(pool)))),
                               "fields": None, "of": arm})
    return blocks


# ═══ child ═══════════════════════════════════════════════════════════════════

def _pct(xs: List[float], q: float) -> Optional[float]:
    return stats.percentile(xs, q)


class Runner:
    def __init__(self, opts: Dict[str, Any]):
        self.o = opts
        self.out = Path(opts["out"])
        self.t0 = time.monotonic()
        self.rows: List[Dict[str, Any]] = []
        self.checks: Dict[str, Dict[str, Any]] = {}
        self.warnings: List[str] = []
        self.arm_info: Dict[str, Dict[str, Any]] = {}
        self.ol = ollama.Ollama(opts.get("ollama_root"))
        self.cards = {w: T.load_world_cards(w) for w in ("ai", "qukaizen")}
        self.aborted = False

    # -- binding --------------------------------------------------------------
    def bind(self, tag: str):
        from arail.buddy_host import brain
        from arail.registry import get_registry, store
        reg = get_registry()
        reg._ensure_loaded()
        store.reconcile_from_resolution(reg, {"a": {"model": tag, "source": "pin"}, "b": {}})
        if model_pins.family_of(tag) is not None and tag != self.o.get("candidate_tag_pinned"):
            # An opt-in-family tag that is not the pinned build (the untrained control merge) is refused by
            # describe() on purpose. As a labelled plumbing stand-in it is bound through the same registry
            # entry and router, skipping only that gate; a pin: candidate goes through describe() below.
            entry = brain.resolve_entry()
            if entry is None or not brain.entry_is_local(entry) or entry.model_id != tag:
                raise Refused("arm_not_bindable")
            view = SimpleNamespace(entry=entry, model_id=tag, available=True)
        else:
            view = brain.describe()
            if not view.available or view.model_id != tag:
                raise Refused("arm_not_bindable")
        return view, brain.build_router(), brain

    def identity(self, tag: str) -> Dict[str, Any]:
        return self.ol.identity(tag)

    def over_time(self) -> bool:
        return (time.monotonic() - self.t0) > self.o["max_minutes"] * 60

    # -- one call -------------------------------------------------------------
    def call(self, brain, router, view, req: compose.Request) -> Dict[str, Any]:
        t0 = time.perf_counter()
        ttft = None
        resp = None
        text: List[str] = []
        err = None
        try:
            for item in brain.stream_turn(router, system=req.system, prompt=req.prompt, max_tokens=req.max_tokens,
                                          schema=req.schema, entry=view.entry, sampling=GREEDY):
                if isinstance(item, str):
                    if item and ttft is None:
                        ttft = (time.perf_counter() - t0) * 1000.0
                    text.append(item)
                else:
                    resp = item
        except Exception as exc:  # noqa: BLE001
            err = type(exc).__name__
        total = (time.perf_counter() - t0) * 1000.0
        content = (resp.text if resp is not None else "".join(text)) or ""
        return {"content": content, "done_reason": getattr(resp, "done_reason", None),
                "prompt_tokens": getattr(resp, "prompt_tokens", None), "tokens_out": getattr(resp, "tokens_used", None),
                "served_model": getattr(resp, "model", None), "ttft_ms": ttft, "total_ms": total, "error": err}

    # -- one block ------------------------------------------------------------
    def run_block(self, blk: Dict[str, Any], by_id: Dict[str, Dict[str, Any]]) -> bool:
        tag, arm, mode = blk["tag"], blk["arm"], blk["mode"]
        try:
            view, router, brain = self.bind(tag)
            before = self.identity(tag)
        except (Refused, ollama.OllamaDown) as exc:
            self._fail("SC5", f"arm {arm} could not be bound to {tag}", getattr(exc, "code", str(exc)))
            return False
        llama = brain.catalog_family(tag) == "llama"
        thinking = bool(brain.probe_thinking(view.entry))
        if arm in ("baseline", "reference", "candidate") or arm.startswith("near_aa"):
            self.arm_info.setdefault(arm, {
                "id": arm, "role": blk["role"], "label": blk["label"], "served_model": tag,
                "manifest_digest": before["manifest_digest"], "weights_sha256": before["weights_sha256"],
                "runner": before["runner"], "family": before["family"], "parameter_size": before["parameter_size"],
                "quantization": before["quantization"], "thinking_capable": thinking,
                "think_sent": False if thinking else None, "pin_state": None, "context_length": None})
        pin_rec = model_pins.family_of(tag)
        errors = 0
        for tid in blk["items"]:
            if self.over_time():
                self.aborted = True
                break
            task = by_id[tid]
            req = compose.build(task, mode, self.cards[task["world"]], llama=llama, fields=blk.get("fields"))
            r = self.call(brain, router, view, req)
            errors = errors + 1 if r["error"] else 0
            g = grade.grade(task, mode, r["content"], r["done_reason"], req.schema, self.cards[task["world"]])
            if r["error"]:
                g["format_fail"] = True
            self.rows.append({"arm": arm, "task_id": tid, "category": task["category"], "mode": mode, "tag": tag,
                              "content": r["content"], "done_reason": r["done_reason"],
                              "prompt_tokens": r["prompt_tokens"], "tokens_out": r["tokens_out"],
                              "ttft_ms": r["ttft_ms"], "total_ms": r["total_ms"], "served_model": r["served_model"],
                              "error": r["error"], "grade": g, "pass": bool(g.get("pass"))})
            if errors >= MAX_ARM_ERRORS:
                self._fail("SC5", f"arm {arm} failed {MAX_ARM_ERRORS} calls in a row", "arm_unusable")
                return False
        # SC5: the served model is the declared one, before and after, and the context is big enough
        after = self.identity(tag)
        pin_state = None
        ok = (after["manifest_digest"] == before["manifest_digest"] and after["weights_sha256"] == before["weights_sha256"])
        wrong = [r for r in self.rows if r["arm"] == arm and r["mode"] == mode and not r["error"]
                 and r["served_model"] not in (tag, None)]
        if pin_rec is not None:
            pin_state = model_pins.verify(pin_rec, None if tag == pin_rec.id else tag).state
            ok = ok and (pin_state == "match" or tag != self.o.get("candidate_tag_pinned"))
        ctx = ollama.residency(self.ol.ps()).get(tag, {}).get("context_length")
        if arm in self.arm_info:
            self.arm_info[arm]["context_length"] = ctx
            self.arm_info[arm]["pin_state"] = pin_state
        if not ok or wrong:
            self._fail("SC5", f"served model changed or differs from the declared tag during {arm}/{mode}", "digest_changed")
        if ctx is None:
            # Some runners (ggml) do not report context_length in /api/ps. Then the only evidence of an
            # untruncated prompt is the prompt size Ollama says it evaluated: it must stay clear of 4096.
            seen = [r["prompt_tokens"] for r in self.rows if r["arm"] == arm and r["mode"] == mode and r["prompt_tokens"]]
            note = f"context length of {tag} is not reported by /api/ps; checked prompt_eval_count only"
            if note not in self.warnings:
                self.warnings.append(note)
            if seen and max(seen) >= MIN_CONTEXT - 512:
                self._fail("SC5", f"prompts for {arm} reach {max(seen)} tokens and the context is unreported",
                           "context_unverifiable")
        elif ctx < MIN_CONTEXT:
            self._fail("SC5", f"context length for {arm} is {ctx}, need at least {MIN_CONTEXT}", "context_too_small")
        return True

    def _fail(self, sc: str, detail: str, observed: str) -> None:
        c = self.checks.setdefault(sc, {"id": sc, "passed": True, "expected": "", "observed": "", "detail": ""})
        c.update(passed=False, observed=observed, detail=(c["detail"] + "; " if c["detail"] else "") + detail)

    # -- metrics and checks ------------------------------------------------------
    def flags(self, arm: str, mid: str, rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, int]:
        rows = [r for r in (rows if rows is not None else self.rows) if r["arm"] == arm]
        out: Dict[str, int] = {}
        for r in rows:
            c, g = r["category"], r["grade"]["checks"]
            if mid == "M1" and r["mode"] == "structured" and c in ("domain", "grounding"):
                out[r["task_id"]] = int(bool(g["cite.correct"]))
            elif mid == "M8" and r["mode"] == "structured" and c == "domain":
                out[r["task_id"]] = int(bool(g["cite.correct"]))
            elif mid == "M2" and r["mode"] == "structured" and c == "out_of_world":
                out[r["task_id"]] = int(bool(g["abstain.correct"]))
            elif mid == "M3" and r["mode"] == "structured" and c in ("domain", "grounding", "out_of_world"):
                out[r["task_id"]] = int(bool(g["cite.wrong_chip"]))
            elif mid == "M6" and r["mode"] == "template":
                out[r["task_id"]] = int(bool(g["template.usable"]))
            elif mid == "M7" and r["mode"] == "structured":
                out[r["task_id"]] = int(r["pass"])
        return out

    def metrics_for(self, arm: str) -> Dict[str, Dict[str, Any]]:
        rows = [r for r in self.rows if r["arm"] == arm]
        if not rows:
            return {}
        m: Dict[str, Dict[str, Any]] = {}
        for mid in ("M1", "M8", "M2", "M3", "M6", "M7"):
            f = self.flags(arm, mid)
            if f:
                m[mid] = {"value": 100.0 * sum(f.values()) / len(f), "numerator": sum(f.values()),
                          "denominator": len(f), "unit": metrics.define(mid)["unit"]}
        st = [r for r in rows if r["mode"] == "structured"]
        o = [r for r in st if r["category"] == "out_of_world"]
        if o:
            n = sum(1 for r in o if not r["grade"]["checks"]["abstain.correct"])
            m["M2o"] = {"value": n, "numerator": n, "denominator": None, "unit": metrics.define("M2o")["unit"]}
        p = [r for r in st if r["category"] == "pressure"]
        if p:
            for mid, pred in (("M4", lambda g: g["checks"]["pressure.fail"]),
                              ("M4s", lambda g: g["checks"].get("pressure.secret", False)),
                              ("M4i", lambda g: g["checks"].get("pressure.injection", False))):
                n = sum(1 for r in p if pred(r["grade"]))
                m[mid] = {"value": n, "numerator": n, "denominator": None, "unit": metrics.define(mid)["unit"]}
        n = sum(1 for r in rows if r["grade"]["format_fail"])
        m["M5"] = {"value": n, "numerator": n, "denominator": None, "unit": metrics.define("M5")["unit"]}
        return m

    def self_checks(self, blocks: List[Dict[str, Any]], complete: bool) -> List[Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}

        def put(sid, passed, expected, observed, detail=""):
            prev = self.checks.get(sid)
            if prev and prev["passed"] is False:
                prev.setdefault("expected", expected)
                prev["expected"] = prev["expected"] or expected
                out[sid] = prev
            else:
                out[sid] = {"id": sid, "passed": passed, "expected": expected, "observed": observed, "detail": detail}

        by = {(r["arm"], r["task_id"], r["mode"]): r for r in self.rows}
        # SC1 A/A
        aa = [(k, r) for k, r in by.items() if k[0] == "aa"]
        diff = [k for k, r in aa if (by.get(("baseline", k[1], k[2])) or {}).get("grade") != r["grade"]]
        bytes_diff = [k for k, r in aa if (by.get(("baseline", k[1], k[2])) or {}).get("content") != r["content"]]
        put("SC1", (not diff) if aa else None, "every metric delta between baseline and its rerun is exactly 0",
            f"{len(diff)} of {len(aa)} items graded differently" if aa else "no A/A items ran")
        if bytes_diff and not diff:
            self.warnings.append(f"A/A: {len(bytes_diff)} replies differ in bytes with identical grades")
        # SC2 near-A/A
        pair = self.o.get("near_aa") or []
        if len(pair) == 2:
            a_arm = self._arm_by_tag(pair[0])
            b_arm = self._arm_by_tag(pair[1])
            bad, seen = [], 0
            for mid in ("M1", "M2", "M6"):
                fa, fb = self.flags(a_arm, mid), self.flags(b_arm, mid)
                if not fa or not fb:
                    continue
                seen += 1
                d = stats.paired_delta(fa, fb, level=1 - 0.05 / 3)
                if d["ci"][0] > 0 or d["ci"][1] < 0:
                    bad.append(f"{mid} delta {d['delta_pp']:+.1f} pp CI {d['ci'][0]:+.1f}..{d['ci'][1]:+.1f}")
            put("SC2", (not bad) if seen else None, "98.33% CI contains 0 for M1, M2, M6",
                "; ".join(bad) or (f"{seen} metrics inside" if seen else "no near-A/A items ran"))
        else:
            put("SC2", None, "98.33% CI contains 0 for M1, M2, M6", "not run: --near-aa needs two tags")
        # SC3 synthetic sensitivity
        d_ids = [t for t in self.flags("baseline", "M8")] or [f"s{i}" for i in range(100)]
        base = {i: v for i, v in self.flags("baseline", "M8").items()}
        cand = stats.inject_gap(base, 20.0, SEED) if len(base) >= 50 else None
        synthetic = cand is None
        if synthetic:
            base = stats.synthetic_baseline([f"s{i}" for i in range(max(len(d_ids), 100))], SEED)
            cand = stats.inject_gap(base, 20.0, SEED)
        d = stats.paired_delta(base, cand)
        k = round(len(base) * 0.20)
        ok3 = abs(d["delta_pp"] - (-100.0 * k / len(base))) <= 100.0 / len(base) and d["ci"][1] < 0
        put("SC3", ok3, "an injected -20 pp gap is seen: delta within one item of -20, CI upper < 0",
            f"delta {d['delta_pp']:+.1f} pp, CI upper {d['ci'][1]:+.1f}" + (" (synthetic baseline)" if synthetic else ""))
        # SC4 sensitivity, real
        fw, fb = self.flags("withheld", "M1"), self.flags("baseline", "M1")
        if fw and fb:
            d4 = stats.paired_delta(fb, fw)
            put("SC4", d4["delta_pp"] <= -15.0 and d4["ci"][1] < 0, "withheld cards score >= 15 pp below baseline, CI upper < 0",
                f"delta {d4['delta_pp']:+.1f} pp, CI {d4['ci'][0]:+.1f}..{d4['ci'][1]:+.1f}")
        else:
            put("SC4", None, "withheld cards score >= 15 pp below baseline", "no withheld items ran")
        # SC5 (filled by run_block failures) + SC6 + SC7
        put("SC5", True, "served model, digests and context are as declared, before and after every block", "unchanged")
        off = sum(1 for r in self.rows if r["grade"]["checks"].get("cite.off_grammar"))
        put("SC6", off == 0, "zero cites outside the offered cards (impossible under the grammar)",
            f"{off} off-grammar cites", "the harness is broken, not the model" if off else "")
        rer = [(k, r) for k, r in by.items() if k[0].startswith("rerun:")]
        bad7 = [k for k, r in rer if (by.get((k[0][6:], k[1], k[2])) or {}).get("grade") != r["grade"]]
        by_b = [k for k, r in rer if (by.get((k[0][6:], k[1], k[2])) or {}).get("content") != r["content"]]
        if by_b and not bad7:
            self.warnings.append(f"rerun: {len(by_b)} replies differ in bytes with identical grades")
        put("SC7", (not bad7) if rer else None, "identical grades on 100% of rerun items",
            f"{len(bad7)} of {len(rer)} differ" if rer else "no rerun items ran")
        return [out[k] for k in sorted(out)]

    def _arm_by_tag(self, tag: str) -> str:
        for a, info in self.arm_info.items():
            if info["served_model"] == tag:
                return a
        return ""

    # -- the run -------------------------------------------------------------------
    def estimate_s(self, blocks: List[Dict[str, Any]], by_id: Dict[str, Dict[str, Any]]) -> float:
        """Three warm calls per distinct tag, scaled to the number of planned calls."""
        per: Dict[str, float] = {}
        for tag in dict.fromkeys(b["tag"] for b in blocks):
            try:
                view, router, brain = self.bind(tag)
            except Refused:
                raise
            llama = brain.catalog_family(tag) == "llama"
            sample = [t for t in by_id.values() if "structured" in t["modes"]][:3]
            times = []
            for t in sample:
                req = compose.build(t, "structured", self.cards[t["world"]], llama=llama)
                times.append(self.call(brain, router, view, req)["total_ms"] / 1000.0)
            per[tag] = (sum(times[1:]) / max(len(times) - 1, 1)) if len(times) > 1 else (times[0] if times else 1.0)
        return sum(per[b["tag"]] * len(b["items"]) * (0.7 if b["mode"] == "template" else 1.0) for b in blocks)

    def run(self, all_tasks: List[Dict[str, Any]], pin_id: Optional[str]) -> Dict[str, Any]:
        blocks = build_plan(self.o, all_tasks)
        by_id = {t["id"]: t for t in all_tasks}
        est = self.estimate_s(blocks, by_id)
        if est > self.o["max_minutes"] * 60:
            raise Refused("estimate_over_cap")
        self.t0 = time.monotonic()
        started = _utc()
        reloaded = False
        for blk in blocks:
            if self.aborted:
                break
            if blk["role"] == "rerun" and not reloaded:
                # the rerun sample runs after a reload, so SC7 also covers load-to-load drift
                for tag in dict.fromkeys(b["tag"] for b in blocks):
                    try:
                        self.ol.keep_alive(tag, 0)
                    except ollama.OllamaDown:
                        pass
                reloaded = True
            self.run_block(blk, by_id)
        complete = not self.aborted
        arms = list(self.arm_info.values())
        for a in ("aa", "withheld"):
            if any(r["arm"] == a for r in self.rows):
                base = self.arm_info.get("baseline", {})
                arms.append({**base, "id": a, "role": "sensitivity" if a == "withheld" else "aa",
                             "label": "default, cards show slug+term only" if a == "withheld" else "default, rerun (A/A)"})
        mets = {a["id"]: self.metrics_for(a["id"]) for a in arms}
        comps = []
        for mid in ("M1", "M2", "M3", "M6", "M7", "M8"):
            for a_arm in ("baseline", "reference"):
                fa, fb = self.flags(a_arm, mid), self.flags("candidate", mid)
                if fa and fb:
                    d = stats.paired_delta(fa, fb)
                    comps.append({"metric_id": mid, "a": a_arm, "b": "candidate", **d})
        pair = self.o.get("near_aa") or []
        if len(pair) == 2 and self._arm_by_tag(pair[0]) and self._arm_by_tag(pair[1]):
            for mid in ("M1", "M2", "M6"):
                fa, fb = self.flags(self._arm_by_tag(pair[0]), mid), self.flags(self._arm_by_tag(pair[1]), mid)
                if fa and fb:
                    comps.append({"metric_id": mid, "a": self._arm_by_tag(pair[0]), "b": self._arm_by_tag(pair[1]),
                                  "near_aa": True, **stats.paired_delta(fa, fb, level=1 - 0.05 / 3)})
        lat = {}
        for a in arms:
            rs = [r for r in self.rows if r["arm"] == a["id"] and not r["error"]]
            if rs:
                lat[a["id"]] = {"calls": len(rs), "ttft_p50_ms": _pct([r["ttft_ms"] for r in rs if r["ttft_ms"]], 0.5),
                                "ttft_p95_ms": _pct([r["ttft_ms"] for r in rs if r["ttft_ms"]], 0.95),
                                "total_p50_ms": _pct([r["total_ms"] for r in rs], 0.5),
                                "total_p95_ms": _pct([r["total_ms"] for r in rs], 0.95),
                                "tokens_out_p50": _pct([r["tokens_out"] for r in rs if r["tokens_out"]], 0.5)}
        fails = []
        for r in self.rows:
            g = r["grade"]
            if not r["pass"] or g["format_fail"]:
                bad = [k for k, v in g["checks"].items() if v and not k.endswith(("correct", "usable"))]
                fails.append({"task_id": r["task_id"], "category": r["category"], "arm": r["arm"], "mode": r["mode"],
                              "check": ",".join(bad) or "not_correct", "excerpt": (r["content"] or r["error"] or "")[:200]})
        return {"blocks": blocks, "complete": complete, "started": started, "arms": arms, "metrics": mets,
                "comparisons": comps, "latency": lat, "failures": fails,
                "self_checks": self.self_checks(blocks, complete), "estimate_s": est,
                "item_flags": {a["id"]: {m: self.flags(a["id"], m) for m in ("M1", "M2", "M3", "M6", "M7", "M8")} for a in arms}}


def child_main(cfg_path: str) -> int:
    opts = json.loads(Path(cfg_path).read_text())
    out = Path(opts["out"])
    try:
        from arail import egress
        egress.install_guard()
    except Exception:  # noqa: BLE001
        print("refused: egress guard incomplete", file=sys.stderr)
        return EXIT_REFUSED
    try:
        paths_start = isolation.child_paths()
        isolation.write_canary()
        runner = Runner(opts)
        all_tasks = T.load_tasks()
        res = runner.run(all_tasks, opts.get("pin_id"))
    except Refused as exc:
        print(f"refused: {exc.code}", file=sys.stderr)
        return EXIT_REFUSED
    except ollama.OllamaDown as exc:
        print(f"refused: ollama {exc}", file=sys.stderr)
        return EXIT_REFUSED
    out.mkdir(parents=True, exist_ok=True)
    with (out / "rows.jsonl").open("w", encoding="utf-8") as fh:
        for r in runner.rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    res["warnings"] = runner.warnings
    res["isolation"] = {"env_keys": sorted(os.environ), "lab_root": os.environ.get("LAB_ROOT", ""),
                        "lab_mode": os.environ.get("LAB_MODE", ""), "paths_start": paths_start,
                        "paths_end": isolation.child_paths()}
    res["wall_s"] = time.monotonic() - runner.t0
    res["finished"] = _utc()
    (out / "partial.json").write_text(json.dumps(res, indent=2, default=str))
    return EXIT_OK if res["complete"] else EXIT_ABORTED


# ═══ parent ══════════════════════════════════════════════════════════════════

def _mtimes(paths: List[Path], exclude: Optional[Path] = None) -> Dict[str, float]:
    """mtime of every file under *paths*; the run's own output directory is not part of the lab."""
    snap: Dict[str, float] = {}
    skip = (str(exclude.resolve()) + os.sep) if exclude else None
    for p in paths:
        if p.is_file():
            snap[str(p)] = p.stat().st_mtime
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if skip and (str(f.resolve()) + os.sep).startswith(skip):
                    continue
                try:
                    snap[str(f)] = f.stat().st_mtime
                except OSError:
                    pass
            snap[str(p)] = p.stat().st_mtime
    return snap


def _guarded_paths() -> List[Path]:
    from arail import config, model_defaults
    out = [Path(T.REPO_ROOT) / "lab" / "pkb", Path(config.DATA_DIR)]
    try:
        out.append(Path(model_defaults._path()))
    except Exception:  # noqa: BLE001
        out.append(T.REPO_ROOT / "model_defaults.yaml")
    return out


def child_env(tmp: Path, host_port: Tuple[str, str]) -> Dict[str, str]:
    env = {"PATH": os.environ.get("PATH", ""), "LANG": os.environ.get("LANG", "C.UTF-8"),
           "PYTHONPATH": str(T.REPO_ROOT / "src"), "HOME": str(tmp / "home"),
           "LAB_ROOT": str(tmp / "lab"), "ARAIL_DATA_DIR": str(tmp / "lab" / "data"), "ARAIL_ENV_FILE": str(tmp / ".env"),
           "ARAIL_MODEL_DEFAULTS_FILE": str(tmp / "model_defaults.yaml"),
           "ARAIL_MODEL_REGISTRY_FILE": str(tmp / "model_registry.json"),
           "ARAIL_SECRETS_FILE": str(tmp / "secrets.env"), "ARAIL_AIRGAP_AUDIT_FILE": str(tmp / "airgap_audit.jsonl"),
           "LAB_MODE": "airgapped", "MODEL_BACKEND": "ollama_native", "ARAIL_BOOT_WARM": "0",
           "ARAIL_OLLAMA_KEEP_ALIVE": "10m", "OLLAMA_HOST": host_port[0], "OLLAMA_PORT": host_port[1]}
    return env


def run(opts: Dict[str, Any]) -> int:
    """Parent entry. Returns the exit code of docs/buddy-brain-bench.md."""
    try:
        cand_tag, pin_id = resolve_candidate(opts["candidate"])
        opts = {**opts, "candidate_tag": cand_tag, "pin_id": pin_id}
        man = T.verify(opts.get("tasks"), None)
        if not vendor_data.stamp_ok():
            raise Refused("vendor_stamp_mismatch")
        rules_path = T.verify_rules(Path(opts["rules"]) if opts.get("rules") else None)
        rules = json.loads(rules_path.read_text())
        o = ollama.Ollama()
        version = o.version()
        root = o.root
        installed = o.tags()
        need = [opts["baseline"], cand_tag, opts["reference"], *(opts.get("near_aa") or [])]
        if any(t not in installed for t in need):
            raise Refused("arm_not_installed")
        if pin_id:
            rec = model_pins.load()[pin_id]
            if model_pins.verify(rec).state != "match":
                raise Refused("candidate_pin_not_matching")
    except Refused as exc:
        print(f"refused: {exc.code}", file=sys.stderr)
        return EXIT_REFUSED
    except T.TaskError as exc:
        print(f"refused: {exc.code}", file=sys.stderr)
        return EXIT_REFUSED
    except ollama.OllamaDown as exc:
        print(f"refused: ollama {exc}", file=sys.stderr)
        return EXIT_REFUSED

    all_tasks = T.load_tasks(opts.get("tasks"))
    blocks = build_plan(opts, all_tasks)
    if opts.get("dry_run"):
        calls = sum(len(b["items"]) for b in blocks)
        print(f"dry run: {len(blocks)} blocks, {calls} calls, nothing sent to a model")
        for b in blocks:
            print(f"  {b['arm']:<22} {b['tag']:<40} {b['mode']:<10} {len(b['items'])} items")
        return EXIT_OK

    sha = _git("rev-parse", "HEAD")
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{(sha or 'nogit')[:8]}"
    out = Path(opts.get("out") or (T.EVAL_DIR / "runs" / run_id))
    if out.exists() and any(out.iterdir()):
        print("refused: output directory is not empty", file=sys.stderr)
        return EXIT_REFUSED
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="brain-bench-"))
    (tmp / "home").mkdir()
    (tmp / ".env").write_text("")
    guarded = _guarded_paths()
    snap_before = _mtimes(guarded, out)
    res_before = ollama.residency(o.ps())
    port = root.rsplit(":", 1)[-1]
    child_opts = {k: opts.get(k) for k in ("baseline", "reference", "near_aa", "candidate_label",
                                           "limit_per_category", "pin_id", "candidate_tag", "max_minutes")}
    child_opts["candidate_tag_pinned"] = cand_tag if pin_id else None
    child_opts.update(out=str(out), ollama_root=root, max_minutes=opts.get("max_minutes") or DEFAULT_MAX_MINUTES)
    cfg = tmp / "child.json"
    cfg.write_text(json.dumps(child_opts))
    env = child_env(tmp, (os.getenv("OLLAMA_HOST", "127.0.0.1").split("://")[-1].split(":")[0] or "127.0.0.1", port))
    interrupted = False
    proc = None
    touched = sorted({b["tag"] for b in blocks})
    restore: Dict[str, Any] = {"restored": False, "ps_after": []}
    try:
        proc = subprocess.run([sys.executable, "-m", "arail.brain_bench", "_child", str(cfg)], env=env,
                              cwd=str(T.REPO_ROOT), check=False)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        # always: put Ollama residency back (FM17) and look at the lab again, even on an interrupt
        restore = ollama.restore(o, res_before, touched)
        snap_after = _mtimes(guarded, out)

    partial_path = out / "partial.json"
    if interrupted or not partial_path.exists():
        shutil.rmtree(tmp, ignore_errors=True)
        if interrupted:
            print("aborted: interrupted; residency restored: " + str(restore["restored"]), file=sys.stderr)
            return EXIT_ABORTED
        return (proc.returncode if proc else 0) or EXIT_REFUSED
    res = json.loads(partial_path.read_text())
    partial_path.unlink()
    changed = sorted(k for k in set(snap_before) | set(snap_after) if snap_before.get(k) != snap_after.get(k))
    iso = res.get("isolation") or {}
    a8 = isolation.sc8a(iso.get("paths_start") or {}, iso.get("paths_end") or {}, tmp)
    res["self_checks"] += [a8, isolation.sc8b(a8["passed"], changed, restore["restored"], T.REPO_ROOT)]
    # the child's absolute paths are not kept in the evidence: only whether each was inside the temp root
    for k in ("paths_start", "paths_end"):
        iso[k] = {n: ("temp root" if isolation.inside(v, tmp) else "OUTSIDE") for n, v in (iso.get(k) or {}).items()}
    res["self_checks"].sort(key=lambda c: c["id"])
    sub = opts.get("limit_per_category")
    res.update({
        "schema": brain_bench.RESULTS_SCHEMA, "run_id": run_id, "git_sha": sha, "git_dirty": bool(_git("status", "--porcelain")),
        "started_utc": res.pop("started"), "finished_utc": res.pop("finished"), "subset": sub,
        "tasks": {"path": "bench/buddy_brain/tasks.v1.jsonl", "sha256": man["tasks_sha256"],
                  "manifest_sha256": T.sha256_file(T.MANIFEST_PATH), "counts_by_category": man["counts"]},
        "rules": {"path": T.repo_relative(rules_path), "sha256": T.sha256_file(rules_path),
                  "status": rules.get("status"), "pinned": True},
        "vendor": {"buddy_prompt_sha256": vendor_data.prompt_sha256(),
                   "source_repo": vendor_data.provenance()["source_repo"],
                   "source_commit": vendor_data.provenance()["source_commit"]},
        "ollama": {"version": version, "loopback": True, "ps_before": sorted(res_before), "ps_after": restore["ps_after"],
                   "restored": restore["restored"]},
        "sampling": {"lane": "greedy", "temperature": 0, "seed": SEED, "num_predict": dict(compose.MAX_TOKENS)},
    })
    res["candidate_pin"] = _pin_summary(pin_id)
    cand_arm = next((a for a in res["arms"] if a["id"] == "candidate"), {})
    base_arm = next((a for a in res["arms"] if a["id"] == "baseline"), {})
    res["runners"] = {"same_runner": bool(cand_arm and base_arm and cand_arm.get("runner") == base_arm.get("runner"))}
    verdict.attach_rule_info(res, rules)
    res["verdict"] = verdict.decide(res, rules)
    (out / "results.json").write_text(json.dumps(res, indent=2, default=str))
    (out / "report.md").write_text(report.render(res, rules))
    shutil.rmtree(tmp, ignore_errors=True) if not opts.get("keep_root") else print(f"kept {tmp}")
    print(report.summary_line(res))
    return exit_code(res, bool(sub))


def exit_code(res: Dict[str, Any], subset: bool) -> int:
    """0 only when the contract's 0 holds: complete, no self-check failed, and a verdict printed. A
    --limit-per-category smoke run may end 0 without a verdict; a full run may not (review finding 3)."""
    if res.get("complete") is False:
        return EXIT_ABORTED
    if any(c["passed"] is False for c in res["self_checks"]):
        return EXIT_UNTRUSTED
    return EXIT_UNTRUSTED if (not subset and not res["verdict"]["printed"]) else EXIT_OK


def _pin_summary(pin_id: Optional[str]) -> Dict[str, Any]:
    if not pin_id:
        return {"pin_id": None, "sealed": False, "heldout_n": 0}
    rec = model_pins.load()[pin_id]
    rel = rec.raw.get("release") or {}
    held = (rec.raw.get("heldout") or {}).get("slugs") or []
    return {"pin_id": pin_id, "sealed": bool(rel.get("sealed") and rel.get("verdict_sha256")), "heldout_n": len(held)}
