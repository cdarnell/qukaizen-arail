"""On-device experiment engine for the Researcher agent (Autoresearch).

This is the Researcher's REAL measurement engine — every number it produces is
computed by code from an actual run on this machine, or it does not exist. It
replaces the previous simulated path (timed sleeps + LLM-invented / hardcoded
metrics). It never fabricates: honest outcomes include ``cannot_run`` (e.g. no
local model) and ``unmeasured`` (a hypothesis nothing here can measure) — it
never reports invented success.

It is DISTINCT from ``arail.experiments`` (the /tuning inference-tuning loop,
which owns git branches and tuning.yml). This engine owns the Autoresearch
page's experiments.

Four v1 archetypes, all airgapped-safe (no network imports):
  • ``model_throughput``        — measured TTFT / decode-rate / latency (needs a model)
  • ``prompt_variant``          — prompt variants scored by deterministic proxies (needs a model)
  • ``retrieval_quality``       — quality of the APPROVED KB (needs no model)
  • ``game_config_optimization`` — measured one-variable-at-a-time game-config
    search via a user-configured benchmark command (needs no model; needs a
    real, user-supplied benchmark — never fabricates a frame rate)

Only one piece of output is model-authored: an optional 1–2 sentence
``interpretation`` OF the measured numbers, always labeled ``model-narrated``.
With no model there is no interpretation — never a canned "looks promising".
"""

from __future__ import annotations

import asyncio
import json
import platform
import re
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

ENGINE_ID = "mini_experiments/v1"

# ── archetype selection ──────────────────────────────────────────────

_THROUGHPUT_KW = ("throughput", "speed", "latency", "fast", "faster", "tokens",
                  "tok/s", "model", "performance", "benchmark", "quantization",
                  "quantize", "size", "memory")
_PROMPT_KW = ("prompt", "phrasing", "instruction", "format", "wording",
              "template", "system message", "few-shot", "chain-of-thought")
_RETRIEVAL_KW = ("knowledge", "retrieval", "retrieve", "source", "kb",
                 "grounding", "grounded", "citation", "corpus", "search",
                 "index", "document", "recall")
_GAME_CONFIG_KW = ("fps", "frame rate", "framerate", "frame time", "1% low",
                   "one percent low", "graphics setting", "game config",
                   "in-game setting", "game settings")


# Interventions this lab has no lever for. Each names something inside
# an inference engine or a training pipeline; nothing here can be varied
# by any v1 runner, which can only measure the local model AS IT IS,
# swap prompts, probe the approved KB, or change a game config.
#
# Matching one of these does not make a hypothesis wrong — it makes it
# untestable HERE, which is a different and honest answer.
_NO_LEVER_KW = (
    "prefetch", "lookahead", "kv cache", "key-value cache", "kv-cache",
    "speculative decoding", "draft token", "mixed-precision",
    "mixed precision", "per-layer", "per layer", "layer streaming",
    "expert cache", "batching depth", "batch depth", "concurrent-prompt",
    "kernel", "cuda", "metal shader", "flash attention", "paged attention",
    "fine-tune", "finetune", "fine tune", "lora", "distill",
    "quantize the", "requantize",
)

# Verbs that turn a statement into a proposed change. "The local model
# sustains 60 tok/s" is measurable as-is; "increasing X will raise
# throughput" proposes an intervention.
_INTERVENTION_VERB_KW = (
    "increas", "decreas", "reduc", "rais", "lower", "switch", "swap",
    "enabl", "disabl", "implement", "integrat", "appli", "apply",
    "doubl", "halv", "tune", "adjust", "chang", "replac", "add ",
    "introduc", "optimiz",
)


def _no_lever_reason(hypothesis: str) -> Optional[str]:
    """Why this lab cannot test the hypothesis, or None if it can.

    The engine has exactly four levers. A hypothesis that proposes
    changing something else — engine internals, a training regime, a
    model this lab is not running — cannot be tested by measuring the
    local model harder. Saying so is the honest result; silently
    measuring something else and calling it "supported" is not.
    """
    h = (hypothesis or "").lower()
    hit = next((kw for kw in _NO_LEVER_KW if kw in h), None)
    if not hit:
        return None
    if not any(v in h for v in _INTERVENTION_VERB_KW):
        # Mentions the concept without proposing to change it — e.g.
        # "the model's KV cache behaviour is documented" — so it may
        # still be a plain measurement.
        return None
    return (
        f"this lab has no lever for '{hit.strip()}' — the on-device engine "
        "can measure the local model as-is, vary prompts, probe the "
        "approved knowledge base, or change a game config, and nothing "
        "else. Testing it needs a harness that can actually vary that "
        "setting."
    )


def classify_hypothesis(hypothesis: str) -> tuple[Optional[str], Optional[str]]:
    """(archetype, unmeasurable_reason). Exactly one is non-None.

    Deterministic — no model, no randomness.
    """
    reason = _no_lever_reason(hypothesis)
    if reason:
        return None, reason

    h = (hypothesis or "").lower()

    def _score(words: tuple[str, ...]) -> int:
        return sum(1 for w in words if w in h)

    scores = {
        "model_throughput": _score(_THROUGHPUT_KW),
        "prompt_variant": _score(_PROMPT_KW),
        "retrieval_quality": _score(_RETRIEVAL_KW),
        "game_config_optimization": _score(_GAME_CONFIG_KW),
    }
    best = max(scores, key=lambda k: scores[k])
    if scores[best] > 0:
        return best, None
    return None, ("no on-device archetype matches this hypothesis — the "
                  "engine can measure throughput, prompt variants, "
                  "retrieval quality, or a game config.")


def select_archetype(hypothesis: str) -> Optional[str]:
    """Map a hypothesis to a measurable archetype, or None if unmeasurable.

    Thin wrapper over ``classify_hypothesis`` kept for callers that only
    need the archetype.
    """
    return classify_hypothesis(hypothesis)[0]


# Real metric names per archetype (for tracker.create's metrics list + the UI).
ARCHETYPE_METRICS: Dict[str, List[str]] = {
    "model_throughput": ["ttft_ms", "decode_tok_per_sec", "total_latency_ms", "tokens_out"],
    "prompt_variant": ["best_compliance_rate", "baseline_compliance_rate",
                       "median_latency_ms", "consistency"],
    "retrieval_quality": ["approved_docs_count", "coverage",
                          "self_retrieval_top1", "median_score"],
    "game_config_optimization": ["variable_tested", "baseline_value", "best_value",
                                 "baseline_avg_fps", "baseline_one_percent_low_fps",
                                 "best_avg_fps", "best_one_percent_low_fps",
                                 "candidates_ok"],
    "unmeasured": [],
}

ARCHETYPE_METHODOLOGY: Dict[str, str] = {
    "model_throughput":
        "Measure TTFT and steady-state decode rate over 3 runs of a fixed "
        "prompt through the locally-resolved model; report the median.",
    "prompt_variant":
        "Run 2–3 prompt variants (k=3 samples each) on a goal-derived task and "
        "score them with deterministic, code-computed proxies (format "
        "compliance, latency, output consistency) — never a model self-score.",
    "retrieval_quality":
        "Probe the human-APPROVED knowledge base with goal keywords and each "
        "approved document's own title; measure coverage and self-retrieval "
        "rank. Needs no model.",
    "game_config_optimization":
        "Change one game setting at a time and measure avg FPS + 1% lows via a "
        "user-configured benchmark command (never a model, never a guess); keep "
        "the value only if both improve over baseline. Needs a real benchmark "
        "command — with none configured, or none that runs, this reports "
        "cannot_run rather than a recommendation.",
    "unmeasured":
        "Recorded for the record — this hypothesis is not measurable on-device "
        "with the current engine, so no metrics are produced.",
}


# ── context + result types ───────────────────────────────────────────

@dataclass
class ExperimentContext:
    """Everything the engine needs, injected by the Researcher so this module
    imports no portal/jobs state (no cycles, trivially testable)."""
    router: Any = None                       # fast router, or None
    kb_search: Optional[Callable[[str, int], List[Dict[str, Any]]]] = None
    kb_approved_titles: Optional[Callable[[], List[str]]] = None
    observe: Optional[Callable[[str, Dict[str, Any]], None]] = None
    halt_check: Optional[Callable[[], bool]] = None
    pause_wait: Optional[Callable[[], Awaitable[None]]] = None
    time_budget_sec: float = 60.0
    model_name: str = ""
    backend_name: str = ""


@dataclass
class MiniResult:
    archetype: str
    provenance: str                          # measured | cannot_run | unmeasured
    outcome: str                             # supported | not_supported | inconclusive | cannot_run
    success: bool
    metrics: Dict[str, Any] = field(default_factory=dict)
    conclusion: str = ""
    runs: int = 0
    cannot_run_reason: Optional[str] = None
    interpretation: Optional[Dict[str, str]] = None
    started_at: str = ""
    duration_sec: float = 0.0

    def to_results_payload(self, ctx: "ExperimentContext") -> Dict[str, Any]:
        """The dict handed to tracker.complete(results=...)."""
        return {
            "engine": ENGINE_ID,
            "archetype": self.archetype,
            "provenance": self.provenance,
            "outcome": self.outcome,
            "metrics": self.metrics,
            "runs": self.runs,
            "model": ctx.model_name or None,
            "backend": ctx.backend_name or None,
            "environment": {"platform": platform.platform(),
                            "machine": platform.machine()},
            "started_at": self.started_at,
            "duration_sec": round(self.duration_sec, 2),
            "cannot_run_reason": self.cannot_run_reason,
            "interpretation": self.interpretation,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cannot_run(archetype: str, reason: str, started: str, t0: float) -> MiniResult:
    return MiniResult(
        archetype=archetype, provenance="cannot_run", outcome="cannot_run",
        success=False, conclusion=f"Could not run: {reason}",
        cannot_run_reason=reason, started_at=started,
        duration_sec=time.monotonic() - t0)


# ── timed model call ─────────────────────────────────────────────────

@dataclass
class _Call:
    text: str
    tokens_out: int
    ttft_ms: Optional[float]
    total_ms: float
    decode_tps: Optional[float]
    ok: bool


def _timed_complete(router, prompt: str, max_tokens: int,
                    temperature: float = 0.7) -> _Call:
    """One measured completion: a 1-token warmup approximates TTFT, then the
    full call; decode rate subtracts the warmup token (steady-state). Mirrors
    the technique in arail.experiments.bench.run_bench."""
    try:
        t_warm = time.monotonic()
        router.complete("ok", max_tokens=1, temperature=temperature)
        ttft_ms = (time.monotonic() - t_warm) * 1000.0

        t0 = time.monotonic()
        resp = router.complete(prompt, max_tokens=max_tokens, temperature=temperature)
        total_ms = (time.monotonic() - t0) * 1000.0
        text = (getattr(resp, "text", "") or "")
        tokens_out = int(getattr(resp, "tokens_used", 0) or 0)
        decode_tps = None
        if tokens_out > 1 and total_ms > 0:
            decode_tps = round((tokens_out - 1) / (total_ms / 1000.0), 3)
        return _Call(text, tokens_out, round(ttft_ms, 2), round(total_ms, 2),
                     decode_tps, True)
    except Exception:
        return _Call("", 0, None, 0.0, None, False)


async def _await_call(ctx: ExperimentContext, router, prompt: str,
                      max_tokens: int, temperature: float = 0.7) -> _Call:
    """Run a completion off the event loop and honor pause/halt around it."""
    if ctx.pause_wait is not None:
        await ctx.pause_wait()
    return await asyncio.to_thread(_timed_complete, router, prompt, max_tokens, temperature)


def _median(vals: List[float]) -> Optional[float]:
    vals = [v for v in vals if v is not None]
    return round(statistics.median(vals), 3) if vals else None


def _emit(ctx: ExperimentContext, text: str, data: Optional[Dict[str, Any]] = None) -> None:
    if ctx.observe is not None:
        try:
            ctx.observe(text, data or {})
        except Exception:  # never let logging break a run
            pass


# ── archetype runners ────────────────────────────────────────────────

async def _run_throughput(exp: Dict[str, Any], ctx: ExperimentContext,
                          started: str, t0: float) -> MiniResult:
    if ctx.router is None:
        return _cannot_run("model_throughput", "no local model available", started, t0)
    prompt = ("Explain in two sentences why smaller models can be faster to "
              "run than larger ones.")
    runs: List[_Call] = []
    n = 3
    for i in range(n):
        if ctx.halt_check and ctx.halt_check():
            break
        call = await _await_call(ctx, ctx.router, prompt, max_tokens=64)
        runs.append(call)
        if call.ok:
            _emit(ctx, f"run {i+1}/{n}: {call.decode_tps or 0:.1f} tok/s, "
                       f"TTFT {call.ttft_ms or 0:.0f} ms",
                  {"ttft_ms": call.ttft_ms, "decode_tok_per_sec": call.decode_tps})
        else:
            _emit(ctx, f"run {i+1}/{n}: model call failed", {})
        if (time.monotonic() - t0) > ctx.time_budget_sec:
            break
    ok_runs = [r for r in runs if r.ok]
    if not ok_runs:
        return _cannot_run("model_throughput",
                           "the local model did not respond", started, t0)
    metrics = {
        "ttft_ms": _median([r.ttft_ms for r in ok_runs]),
        "decode_tok_per_sec": _median([r.decode_tps for r in ok_runs]),
        "total_latency_ms": _median([r.total_ms for r in ok_runs]),
        "tokens_out": _median([float(r.tokens_out) for r in ok_runs]),
        "runs_ok": len(ok_runs),
    }
    rate = metrics["decode_tok_per_sec"]
    success = rate is not None and rate > 0
    return MiniResult(
        archetype="model_throughput", provenance="measured",
        outcome="supported" if success else "inconclusive", success=success,
        metrics=metrics, runs=len(ok_runs), started_at=started,
        duration_sec=time.monotonic() - t0,
        conclusion=(f"Measured {rate} tok/s decode, "
                    f"{metrics['ttft_ms']} ms TTFT (median of {len(ok_runs)} runs)."
                    if success else
                    "Ran, but could not compute a decode rate (too few tokens)."))


_VARIANTS = [
    ("baseline", "{q}"),
    ("structured", "{q}\n\nAnswer in exactly 3 bullet points, each line starting with '- '."),
    ("constrained", "{q}\n\nAnswer in exactly 3 bullet points, each starting with "
                    "'- ' and under 15 words."),
]


def _bullet_compliance(text: str) -> float:
    """Deterministic: fraction of compliance with 'exactly 3 bullets'. 1.0 when
    exactly three lines start with '- '; partial credit otherwise."""
    bullets = [ln for ln in text.splitlines() if ln.strip().startswith("- ")]
    n = len(bullets)
    if n == 0:
        return 0.0
    return round(1.0 - abs(3 - n) / 3.0, 3) if n <= 6 else 0.0


def _jaccard(a: str, b: str) -> float:
    ta = set(re.findall(r"[a-z0-9]+", a.lower()))
    tb = set(re.findall(r"[a-z0-9]+", b.lower()))
    if not ta or not tb:
        return 0.0
    return round(len(ta & tb) / len(ta | tb), 3)


async def _run_prompt_variant(exp: Dict[str, Any], ctx: ExperimentContext,
                              started: str, t0: float) -> MiniResult:
    if ctx.router is None:
        return _cannot_run("prompt_variant", "no local model available", started, t0)
    question = (f"About: {exp.get('hypothesis', 'the research goal')[:120]} — "
                "list the three most important considerations.")
    k = 3
    per_variant: Dict[str, Dict[str, Any]] = {}
    for label, tmpl in _VARIANTS:
        if ctx.halt_check and ctx.halt_check():
            break
        prompt = tmpl.format(q=question)
        samples: List[_Call] = []
        for _ in range(k):
            call = await _await_call(ctx, ctx.router, prompt, max_tokens=120, temperature=0.7)
            if call.ok:
                samples.append(call)
            if (time.monotonic() - t0) > ctx.time_budget_sec:
                break
        if not samples:
            continue
        compliances = [_bullet_compliance(s.text) for s in samples]
        # consistency = mean pairwise Jaccard across the k samples
        pairs = [(_jaccard(samples[i].text, samples[j].text))
                 for i in range(len(samples)) for j in range(i + 1, len(samples))]
        per_variant[label] = {
            "compliance_rate": round(statistics.mean(compliances), 3),
            "median_latency_ms": _median([s.total_ms for s in samples]),
            "tokens_out": _median([float(s.tokens_out) for s in samples]),
            "consistency": round(statistics.mean(pairs), 3) if pairs else None,
            "samples": len(samples),
        }
        _emit(ctx, f"variant '{label}': compliance "
                   f"{per_variant[label]['compliance_rate']}, "
                   f"{per_variant[label]['median_latency_ms']} ms",
              per_variant[label])
    if "baseline" not in per_variant or len(per_variant) < 2:
        return _cannot_run("prompt_variant",
                           "the local model did not return enough samples to compare",
                           started, t0)
    baseline_c = per_variant["baseline"]["compliance_rate"]
    best_label = max(per_variant, key=lambda v: per_variant[v]["compliance_rate"])
    best_c = per_variant[best_label]["compliance_rate"]
    success = best_c > baseline_c and best_label != "baseline"
    metrics = {
        "best_variant": best_label,
        "best_compliance_rate": best_c,
        "baseline_compliance_rate": baseline_c,
        "median_latency_ms": per_variant[best_label]["median_latency_ms"],
        "consistency": per_variant[best_label]["consistency"],
        "per_variant": per_variant,
    }
    return MiniResult(
        archetype="prompt_variant", provenance="measured",
        outcome="supported" if success else "not_supported", success=success,
        metrics=metrics, runs=sum(v["samples"] for v in per_variant.values()),
        started_at=started, duration_sec=time.monotonic() - t0,
        conclusion=(
            f"Prompt phrasing mattered: '{best_label}' reached "
            f"{best_c} format-compliance vs {baseline_c} for the bare prompt."
            if success else
            f"Prompt phrasing didn't clearly help here — best was '{best_label}' "
            f"at {best_c} vs {baseline_c} baseline."))


async def _run_retrieval(exp: Dict[str, Any], ctx: ExperimentContext,
                         started: str, t0: float) -> MiniResult:
    if ctx.kb_search is None:
        return _cannot_run("retrieval_quality", "knowledge base is unavailable",
                           started, t0)
    titles = ctx.kb_approved_titles() if ctx.kb_approved_titles else []
    approved_count = len(titles)
    if approved_count == 0:
        return _cannot_run(
            "retrieval_quality",
            "no approved knowledge — approve documents on the Knowledge (DaC) "
            "page first, then this experiment can measure retrieval quality",
            started, t0)
    # Probe 1: goal keywords → is anything retrieved?
    goal_words = [w for w in re.findall(r"[a-zA-Z]{4,}",
                  str(exp.get("hypothesis", "")))][:6]
    probes = list(goal_words) + list(titles[:8])
    hits_per_probe: List[int] = []
    scores: List[float] = []
    self_top1 = 0
    for probe in probes:
        if ctx.halt_check and ctx.halt_check():
            break
        try:
            hits = ctx.kb_search(probe, 5) or []
        except Exception:
            hits = []
        hits_per_probe.append(1 if hits else 0)
        for h in hits:
            s = h.get("score") if isinstance(h, dict) else None
            if isinstance(s, (int, float)):
                scores.append(float(s))
        # self-retrieval: querying a doc's own title should rank it first
        if probe in titles and hits:
            top = hits[0]
            name = (top.get("name") or top.get("path") or "") if isinstance(top, dict) else ""
            if probe.lower() in str(name).lower():
                self_top1 += 1
        _emit(ctx, f"probe '{probe[:40]}': {'hit' if hits else 'no hit'} "
                   f"({len(hits)} results)", {"hits": len(hits)})
        if (time.monotonic() - t0) > ctx.time_budget_sec:
            break
    n_title_probes = sum(1 for p in probes if p in titles)
    coverage = round(sum(hits_per_probe) / len(hits_per_probe), 3) if hits_per_probe else 0.0
    self_rate = round(self_top1 / n_title_probes, 3) if n_title_probes else 0.0
    metrics = {
        "approved_docs_count": approved_count,
        "coverage": coverage,
        "self_retrieval_top1": self_rate,
        "median_score": _median(scores),
        "probes": len(hits_per_probe),
    }
    success = coverage >= 0.5 or self_rate >= 0.5
    return MiniResult(
        archetype="retrieval_quality", provenance="measured",
        outcome="supported" if success else "not_supported", success=success,
        metrics=metrics, runs=len(hits_per_probe), started_at=started,
        duration_sec=time.monotonic() - t0,
        conclusion=(
            f"Approved KB ({approved_count} docs) retrieved relevant results for "
            f"{int(coverage*100)}% of probes; self-retrieval {int(self_rate*100)}%."
            if success else
            f"Approved KB ({approved_count} docs) retrieved weakly — coverage "
            f"{int(coverage*100)}%, self-retrieval {int(self_rate*100)}%. "
            "More/better-titled documents would help."))


# ── game config optimization ─────────────────────────────────────────
#
# INPUTS (runtime, user-provided, never sealed into a World bundle — see
# ADR-0002): exp["variables"] must carry
#   "benchmark_command": List[str]   — argv prefix for the user's own benchmark
#                                       script/binary for THIS game.
#   "game_tunables":      Dict[str, List[Any]]  — setting name -> candidate
#                                       values to try (the game's "manual").
# Optional: "variable" (which tunable to test; defaults to the first, sorted,
# key) and "timeout_sec" per run (defaults to 30s, capped by the experiment's
# time budget).
#
# PROTOCOL: each invocation is `benchmark_command [--set KEY=VALUE]`, and the
# benchmark process must print, as its last non-empty stdout line, a JSON
# object with numeric "avg_fps" and "one_percent_low_fps" keys. Anything else
# (missing binary, non-zero exit, timeout, unparsable output) counts as that
# run failing — never a fabricated number standing in for it.

def _parse_benchmark_output(stdout: bytes) -> Optional[Dict[str, float]]:
    lines = [ln for ln in stdout.decode("utf-8", "replace").splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        payload = json.loads(lines[-1])
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    avg_fps = payload.get("avg_fps")
    one_pct = payload.get("one_percent_low_fps")
    if not isinstance(avg_fps, (int, float)) or not isinstance(one_pct, (int, float)):
        return None
    if avg_fps <= 0 or one_pct <= 0:
        return None
    return {"avg_fps": float(avg_fps), "one_percent_low_fps": float(one_pct)}


async def _run_benchmark(cmd: List[str], extra_args: List[str],
                         timeout: float) -> Optional[Dict[str, float]]:
    """Invoke the user's benchmark command; return measured fps metrics, or
    None if it could not be run or its output could not be trusted. Never
    raises — a broken benchmark command is a failed run, not an engine crash."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, *extra_args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    except (OSError, ValueError):
        return None
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return None
    if proc.returncode != 0:
        return None
    return _parse_benchmark_output(stdout)


async def _run_game_config_optimization(exp: Dict[str, Any], ctx: ExperimentContext,
                                        started: str, t0: float) -> MiniResult:
    variables = exp.get("variables") or {}
    cmd = variables.get("benchmark_command")
    tunables = variables.get("game_tunables")
    if not isinstance(cmd, list) or not cmd or not all(isinstance(c, str) for c in cmd):
        return _cannot_run(
            "game_config_optimization",
            "no benchmark command configured for this game — this World can "
            "only measure a config against a real benchmark you provide",
            started, t0)
    if not isinstance(tunables, dict) or not tunables:
        return _cannot_run(
            "game_config_optimization",
            "no game tunables configured — read the game's settings manual "
            "and provide at least one setting with candidate values",
            started, t0)
    variable = variables.get("variable")
    if not isinstance(variable, str) or variable not in tunables:
        variable = sorted(tunables.keys())[0]
    candidates = tunables.get(variable) or []
    if not isinstance(candidates, list) or not candidates:
        return _cannot_run(
            "game_config_optimization",
            f"no candidate values configured for '{variable}'", started, t0)
    timeout = min(float(variables.get("timeout_sec", 30.0)), ctx.time_budget_sec)

    baseline = await _run_benchmark(cmd, [], timeout)
    _emit(ctx, "baseline: " + ("measured" if baseline else "benchmark did not run"),
          baseline or {})
    if baseline is None:
        return _cannot_run(
            "game_config_optimization",
            "the configured benchmark command did not produce a usable "
            "baseline measurement — check it runs and prints avg_fps / "
            "one_percent_low_fps as JSON", started, t0)

    results: Dict[Any, Dict[str, float]] = {}
    for value in candidates:
        if ctx.halt_check and ctx.halt_check():
            break
        if (time.monotonic() - t0) > ctx.time_budget_sec:
            break
        measured = await _run_benchmark(cmd, ["--set", f"{variable}={value}"], timeout)
        if measured is not None:
            results[value] = measured
            _emit(ctx, f"{variable}={value}: {measured['avg_fps']:.1f} avg fps, "
                       f"{measured['one_percent_low_fps']:.1f} 1% low", measured)
        else:
            _emit(ctx, f"{variable}={value}: benchmark did not run", {})

    if not results:
        return _cannot_run(
            "game_config_optimization",
            f"the benchmark command did not run for any candidate value of "
            f"'{variable}'", started, t0)

    best_value = max(results, key=lambda v: results[v]["avg_fps"])
    best = results[best_value]
    success = (best["avg_fps"] > baseline["avg_fps"]
               and best["one_percent_low_fps"] >= baseline["one_percent_low_fps"])
    metrics = {
        "variable_tested": variable,
        "baseline_value": "current",
        "best_value": best_value,
        "baseline_avg_fps": baseline["avg_fps"],
        "baseline_one_percent_low_fps": baseline["one_percent_low_fps"],
        "best_avg_fps": best["avg_fps"],
        "best_one_percent_low_fps": best["one_percent_low_fps"],
        "candidates_ok": len(results),
        "candidates_attempted": len(candidates),
    }
    return MiniResult(
        archetype="game_config_optimization", provenance="measured",
        outcome="supported" if success else "inconclusive", success=success,
        metrics=metrics, runs=len(results) + 1, started_at=started,
        duration_sec=time.monotonic() - t0,
        conclusion=(
            f"Setting {variable}={best_value} measured {best['avg_fps']:.1f} avg fps "
            f"/ {best['one_percent_low_fps']:.1f} 1% low, both better than the "
            f"current value ({baseline['avg_fps']:.1f} / "
            f"{baseline['one_percent_low_fps']:.1f})."
            if success else
            f"No tested value of {variable} measurably beat the current config "
            f"({baseline['avg_fps']:.1f} avg fps / "
            f"{baseline['one_percent_low_fps']:.1f} 1% low) without a smoothness "
            "regression."))


_RUNNERS = {
    "model_throughput": _run_throughput,
    "prompt_variant": _run_prompt_variant,
    "retrieval_quality": _run_retrieval,
    "game_config_optimization": _run_game_config_optimization,
}


async def run_experiment(exp: Dict[str, Any], ctx: ExperimentContext) -> MiniResult:
    """Run one experiment for real and return a MiniResult. Never raises for a
    measurement failure — it returns a cannot_run result instead."""
    started = _now_iso()
    t0 = time.monotonic()
    archetype = (exp.get("variables") or {}).get("archetype") or "unmeasured"
    runner = _RUNNERS.get(archetype)
    if runner is None:
        # Unmeasurable hypothesis — recorded honestly, zero metrics, not success.
        # Carry the designer's specific reason when it had one; "no lever
        # for prefetch depth" is actionable, "isn't measurable" is not.
        reason = str((exp.get("variables") or {}).get("unmeasurable_reason") or "")
        return MiniResult(
            archetype="unmeasured", provenance="unmeasured", outcome="inconclusive",
            success=False, started_at=started, duration_sec=time.monotonic() - t0,
            cannot_run_reason=reason or None,
            conclusion=(f"Not tested: {reason}" if reason else
                        "This hypothesis isn't measurable on-device with the "
                        "current engine — recorded for the record, no metrics produced."))
    try:
        return await runner(exp, ctx, started, t0)
    except Exception as e:  # defensive — a runner bug is a cannot_run, not fake data
        return _cannot_run(archetype, f"engine error: {type(e).__name__}", started, t0)


def maybe_interpret(result: MiniResult, ctx: ExperimentContext) -> None:
    """Optionally add a 1–2 sentence model-narrated interpretation OF the
    measured numbers. No model → nothing added (never a canned line)."""
    if ctx.router is None or result.provenance != "measured" or not result.metrics:
        return
    try:
        prompt = ("In one or two sentences, plainly interpret these measured "
                  f"experiment results for a non-expert. Metrics: {result.metrics}. "
                  "Do not invent numbers; only interpret what's given.")
        resp = ctx.router.complete(prompt, max_tokens=90, temperature=0.5)
        text = (getattr(resp, "text", "") or "").strip()
        if text:
            result.interpretation = {"text": text[:400], "provenance": "model-narrated"}
    except Exception:
        pass  # interpretation is optional; measured metrics stand on their own


def provenance_line(result_payload: Dict[str, Any]) -> str:
    """One-line provenance header for the KB markdown."""
    prov = result_payload.get("provenance")
    arche = result_payload.get("archetype", "?")
    if prov == "measured":
        model = result_payload.get("model") or "local model"
        runs = result_payload.get("runs", 0)
        return f"**Provenance:** measured by {ENGINE_ID} · {arche} · {model} · {runs} run(s)"
    if prov == "cannot_run":
        return f"**Provenance:** NOT RUN — {result_payload.get('cannot_run_reason', 'could not run')}"
    return f"**Provenance:** unmeasured — {arche} (no on-device measurement available)"
