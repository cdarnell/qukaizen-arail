"""ResearcherAgent — the lab's default background agent.

Takes a parsed goal, auto-generates hypotheses, designs experiments,
gathers sources (via Curator + consent), analyzes findings, and
produces a report.  Every step is emitted to the ActivityLog so the
dashboard shows live progress.

The agent's entire personality is shaped by the lab's *intent* — set at
bootstrap time.  An AI Engineer lab produces hypotheses about models and
architectures.  A Farming lab produces hypotheses about soil, crops, and
yield.  The intent rewrites the system context for every LLM call.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from arail import pkb as pkb_mod
from arail.activity import activity_log
from arail.agent_redirects import get_agent_redirect, redirect_profile
from arail.agent_workflows import update_agent_workflow
from arail.goals import GoalStore
from arail.agents.consent import ConsentStore
from arail.agents.curator import CuratorAgent
from arail.scheduler import (current_window, jobs_halted,
                              startup_delay_seconds, window_label)
from arail.skills.experiment_tracker import ExperimentTracker
from arail.skills.goal_parser import infer_domain, matched_keywords
from arail.swarm_goals import known_swarm_worker_ids


# ── Intent → System Context ─────────────────────────────────────────────
# These shape every LLM prompt the researcher generates.

INTENT_SYSTEM_CONTEXTS: Dict[str, str] = {
    "ai": (
        "You are an AI engineering research lab. Your expertise is artificial "
        "intelligence: model architectures, inference engines, training techniques, "
        "benchmarks, toolchains, and the practical craft of building AI systems. "
        "You evaluate models, recommend swaps, explain internals, and chase the "
        "frontier of what's possible with local hardware."
    ),
    "farming": (
        "You are an agricultural research lab. Your expertise is crop science, "
        "soil health, regional growing conditions, pest management, irrigation, "
        "fertilization, and yield optimization. You think in terms of growing "
        "seasons, USDA zones, soil pH, rainfall patterns, and what a farmer "
        "actually needs to know to get the best harvest from their land."
    ),
    "ml": (
        "You are a machine learning research lab. Your expertise is training "
        "pipelines, dataset curation, model evaluation, fine-tuning strategies, "
        "hyperparameter optimization, and reproducing results from papers. "
        "You think in terms of loss curves, ablation studies, and baselines."
    ),
    "business": (
        "You are a business analysis lab. Your expertise is market research, "
        "competitive intelligence, unit economics, growth strategy, and "
        "data-driven decision making. You think in terms of CAC, LTV, market "
        "size, and defensible moats."
    ),
    "education": (
        "You are a learning sciences lab. Your expertise is pedagogy, curriculum "
        "design, knowledge assessment, spaced repetition, and adaptive learning "
        "paths. You think in terms of mastery, prerequisites, and skill trees."
    ),
    "health": (
        "You are a health and wellness research lab. Your expertise is exercise "
        "science, nutrition, sleep physiology, stress management, and evidence-based "
        "health protocols. You think in terms of biomarkers, dose-response, and "
        "individual variability."
    ),
    "culinary": (
        "You are a culinary science lab. Your expertise is cooking technique, "
        "flavor chemistry, fermentation, ingredient interactions, and recipe "
        "development. You think in terms of Maillard reactions, emulsification, "
        "texture, and sensory analysis."
    ),
    "trade": (
        "You are a skilled-trades research lab. Your expertise is the practical "
        "craft of working trades — woodworking, electrical, plumbing, welding, "
        "HVAC, masonry, automotive, and machining. You think in terms of "
        "technique, materials science, tool selection, code and standard "
        "references (NEC, IRC, OSHA, AWS), safety, and the apprentice → "
        "journeyman → master progression. You favor hands-on, evidence-based "
        "answers grounded in field experience and authoritative codebooks."
    ),
}

DEFAULT_SYSTEM_CONTEXT = (
    "You are a research lab. You design experiments, collect data, analyze "
    "results, and produce actionable findings."
)


def _get_lab_intent() -> str:
    """Live lab intent. Sourced from the mount sidecar via effective_identity()
    so the researcher reframes instantly when a World is mounted/unmounted
    (mounted → "other"); operator's LAB_INTENT still wins on the unmounted path."""
    from arail.identity import effective_identity
    return effective_identity().intent


def _get_system_context(intent: str | None = None) -> str:
    """Build the system context string for the current lab intent.

    Composes two layers:
      1. The intent-flavored base prompt (engineering / research /
         farming / etc — see ``INTENT_SYSTEM_CONTEXTS``).
      2. The skills loaded from ``lab/pkb/agents/researcher/AGENT.md``
         via the shared ``arail.skills_loader``. Each listed skill's
         body gets appended to the system prompt — hot-reloaded on
         every call so editing a SKILL.md in the Skills tab is
         visible on the next utterance.
    """
    if intent is None:
        intent = _get_lab_intent()
    if intent == "other":
        # Free-form lab — compose a personalized base from the live identity
        # (the mounted World's name + domain_framing, or the operator's
        # LAB_INTENT_* on the unmounted path).
        from arail.identity import effective_identity
        _ident = effective_identity()
        intent_name = (_ident.intent_name or "").strip() or "research"
        description = (_ident.intent_description or "").strip()
        if description:
            base = (
                f"You are a {intent_name} research lab. Your focus is: "
                f"{description}. You design experiments, collect evidence, "
                "and produce findings grounded in your domain's primary "
                "sources and best practices."
            )
        else:
            base = DEFAULT_SYSTEM_CONTEXT
    else:
        base = INTENT_SYSTEM_CONTEXTS.get(intent, DEFAULT_SYSTEM_CONTEXT)

    # Append the researcher's skill loadout. Failsoft — if the
    # AGENT.md hasn't been seeded yet (first boot before
    # ensure_default_loadouts runs) or the loader raises, we proceed
    # with the base context alone.
    try:
        from arail.skills_loader import (
            load_agent_skills,
            compose_system_context,
            load_world_skill,
        )
        skills = load_agent_skills("researcher")
        ws = load_world_skill()
        if ws is not None:
            skills = skills + [ws]
        skill_ctx = compose_system_context(skills)
    except Exception:
        skill_ctx = ""
    if skill_ctx:
        return f"{base}\n\n{skill_ctx}"
    return base


def _get_router():
    """Resolve the fast (Tier 0) router via the model registry.

    Returns None when no usable model exists for the 'fast' profile — the
    registry has already emitted a visible FallbackEvent in that case, so
    the degradation is never silent.
    """
    try:
        from arail.registry import resolve
        return resolve("fast", tab="research").router(billing_source="agent")
    except Exception:
        return None


def research_deep_enabled() -> bool:
    """The operator's deep-passes preference for research (AEROLLM_RESEARCH).

    Single source of the semantics: read here by the deep-router gate, by
    the portal's research-status models block, and by the deep toggle
    endpoint — so the UI can never disagree with what the agent does.
    Code default is on; ``.env.example`` ships it off, so arailctl installs
    start disabled until the operator flips the tab's toggle.
    """
    import os
    return os.getenv("AEROLLM_RESEARCH", "true").lower() not in ("0", "false", "no")


def _get_deep_router():
    """The shared aeroLLM deep router (the "2nd inference") for research.

    Returns None when AEROLLM_RESEARCH is disabled or aeroLLM isn't available.
    Delegates to ``arail.agents.deep_policy`` so the lab keeps a SINGLE resident
    deep model across all agents — two copies would OOM the box.
    """
    if not research_deep_enabled():
        return None
    try:
        # Registry resolution honors per-tab overrides (e.g. Claude when
        # hybrid). The default 'reasoning' binding is the aerollm entry, whose
        # router wraps deep_policy's shared resident runtime — still a SINGLE
        # deep model across all agents. allow_fallback=False: _deep_complete
        # has its own fast fallback; a second hop here would double-fall.
        from arail.registry import resolve
        return resolve("reasoning", tab="research",
                       allow_fallback=False).router(billing_source="agent")
    except Exception:
        return None


def _llm_complete(router, prompt: str, max_tokens: int = 512,
                  *, system: str | None = None) -> str | None:
    """Call the LLM and return text, or None on failure.

    ``system`` is an optional stable prefix (the intent system context). For
    the Claude backend it is sent as a cached ``system`` block (prompt
    caching) so the 3-5 calls in one research run reuse it; other backends
    prepend it to the prompt. See ClaudeBackend / build_system_prompt_parts.

    Failures are logged to activity_log at `warn` level so the dashboard
    reveals when heuristic fallbacks are masking a real inference problem.
    Every call emits a prompt_trace for the Agents tab Prompt Inspector.
    """
    if router is None:
        return None
    try:
        import time as _time
        from arail import agent_context
        t0 = _time.monotonic()
        # L3 (ARCHITECTURE.md): researcher is a top-level module the loader
        # never touches, so it gets its own explicit wrapper at this single
        # model-acquisition helper -- both the deep and fast paths funnel
        # through here.
        with agent_context.agent_call("researcher"):
            resp = router.complete(prompt, max_tokens=max_tokens, temperature=0.7,
                                   system=system)
        elapsed = (_time.monotonic() - t0) * 1000
        text = resp.text.strip() if resp.text else None

        traced_prompt = f"{system}\n\n{prompt}" if system else prompt
        # getattr-defensive: tests and bespoke routers pass duck-typed
        # responses that only guarantee `.text`.
        model = getattr(resp, "model", None)
        backend = getattr(resp, "backend", None)
        where = f", {model} @ {backend}" if model and backend else ""
        activity_log.emit("researcher",
                          f"LLM call completed ({int(elapsed)}ms{where})",
                          "info", {
                              "prompt_trace": {
                                  "prompt": traced_prompt[:3000],
                                  "response": text[:2000] if text else None,
                                  "max_tokens": max_tokens,
                                  "latency_ms": round(elapsed, 1),
                                  "model": model,
                                  "backend": backend,
                                  "provider": getattr(router, "provider", None),
                                  "entry_id": getattr(router, "entry_id", None),
                                  "tokens_out": getattr(resp, "tokens_used", None),
                              }
                          })
        if not text:
            activity_log.emit("researcher",
                              "LLM returned empty response — using heuristic fallback.",
                              "warn")
        return text
    except Exception as e:
        activity_log.emit("researcher",
                          f"LLM call failed ({type(e).__name__}: {str(e)[:80]}) "
                          f"— using heuristic fallback.",
                          "warn")
        return None


def _deep_complete(deep_router, fast_router, prompt: str,
                   max_tokens: int = 512, *, system: str | None = None) -> str | None:
    """Try deep (AeroLLM) inference first, fall back to fast router.

    ``system`` is the optional cached stable prefix — forwarded to both the
    deep and fast routers so either path benefits from prompt caching.
    Emits an activity event so the dashboard shows deep inference is active.
    """
    if deep_router is not None:
        activity_log.emit("researcher",
                          "Deep inference — the larger reasoning model, this takes time…",
                          "info", {"mode": "deep"})
        result = _llm_complete(deep_router, prompt, max_tokens, system=system)
        if result:
            return result
        activity_log.emit("researcher",
                          "Deep engine unavailable, falling back to fast model.",
                          "warn")
        # Surface the degradation as a registry fallback event so the global
        # banner shows it — the old warn string above is easy to miss.
        try:
            import time as _t
            from arail.registry import get_registry
            from arail.registry.core import FallbackEvent
            reg = get_registry()
            reg.record_fallback(FallbackEvent(
                ts=_t.time(), profile="reasoning", tab="research",
                from_id=getattr(deep_router, "entry_id", None) or "tier1-aerollm",
                to_id=getattr(fast_router, "entry_id", None) or "tier0-local",
                reason="error",
                detail="deep engine returned no result — research is running "
                       "on the fast model",
                endpoint=None,
                status="unhealthy",
                latency_ms=None))
        except Exception:
            pass
    return _llm_complete(fast_router, prompt, max_tokens, system=system)


def _active_redirect() -> dict[str, Any] | None:
    try:
        return get_agent_redirect("researcher")
    except Exception:
        return None


def _brief_prompt_block() -> str:
    """The lab brief, prepended to planning prompts so research grounds
    itself in what the lab *is* — the mounted World, the approved
    knowledge digest, redirects, and the program headline. Same text the
    Knowledge page's raw-brief disclosure shows. Best-effort: empty
    string when unavailable (a missing brief must never stall a run)."""
    try:
        from arail.lab_brief import brief_markdown, get_cached_brief
        text = brief_markdown(get_cached_brief()).strip()
        return text + "\n\n" if text else ""
    except Exception:  # noqa: BLE001
        return ""


def _redirect_prompt_block(redirect: dict[str, Any] | None) -> str:
    if not redirect:
        return ""
    profile = redirect_profile(redirect)
    instruction = str(redirect.get("instruction") or "").strip()
    if not instruction:
        return ""

    lines = [
        "Operator redirect is active. Treat it as higher-priority steering for this run.",
        f"Redirect: {instruction}",
    ]
    if profile["skip_fetch"]:
        lines.append("Do not gather more external sources unless they are absolutely necessary.")
    if profile["focus_measurement"]:
        lines.append("Prioritize measurement design, evaluation criteria, instrumentation, and success metrics.")
    if profile["prefer_autoresearch"]:
        lines.append("Bias toward work that prepares the goal for an Autoresearch loop with explicit metrics, variants, and stop conditions.")
    if profile["broaden_search"]:
        lines.append("Broaden retrieval and source gathering before narrowing the loop.")
    return "\n".join(lines) + "\n\n"


# LLM hypothesis lists sometimes come back with markdown wrappers
# ("**Hypothesis:** X") or interleaved rationale lines. Without
# normalization those literals leak into experiment.hypothesis and show
# up in the dashboard table.
_RATIONALE_LABEL_RE = re.compile(
    r"^\s*(?:\*+|_+)?\s*(?:rationale|reasoning|explanation|notes?)\s*(?:\*+|_+)?\s*:",
    re.IGNORECASE,
)
_HYPOTHESIS_LABEL_RE = re.compile(
    r"^\s*(?:\*+|_+)?\s*(?:hypothesis|claim|theory|h\d+)\s*(?:\*+|_+)?\s*:\s*",
    re.IGNORECASE,
)


# Chat models wrap a list in conversational scaffolding: an opening
# "Certainly! Here are five testable hypotheses:" and a closing "These
# hypotheses are specific and measurable." Neither is a hypothesis, and
# an un-filtered preamble consumes a real experiment slot — the run that
# exposed this spent one of five slots "testing" the sentence
# "Certainly! Here are five testable hypotheses...".
_PREAMBLE_RE = re.compile(
    r"^(certainly|sure|of course|absolutely|here (are|is)|below (are|is)|"
    r"the following|these (are|hypotheses)|i (have|will|can)|"
    r"as (an|a) )\b",
    re.IGNORECASE,
)


def _looks_like_scaffolding(s: str) -> bool:
    """True for the model's framing sentences around the actual list."""
    if _PREAMBLE_RE.match(s):
        return True
    # A line that only introduces what follows ends in a colon and makes
    # no claim of its own.
    if s.endswith(":") and len(s.split()) <= 14:
        return True
    return False


def _normalize_hypothesis_line(raw: str) -> str | None:
    s = raw.strip().lstrip("0123456789.-) ").strip()
    if not s:
        return None
    if _RATIONALE_LABEL_RE.match(s):
        return None
    s = _HYPOTHESIS_LABEL_RE.sub("", s)
    s = re.sub(r"\*+|_+", "", s).strip()
    if len(s) <= 10:
        return None
    if _looks_like_scaffolding(s):
        return None
    return s


# ── Report grounding ────────────────────────────────────────────────
#
# The metrics are code-measured, but the narrative around them is
# model-written, and a model asked to "write a report" will furnish it:
# a Hardware: line naming a machine that was never used, a Date:
# YYYY-MM-DD placeholder, and — worst — a real measured number attached
# to a hypothesis that was never tested. A reader cannot tell those from
# the true parts, which makes the whole document untrustworthy even
# though most of it is right.
#
# So: facts are emitted by code, and the narrative has to survive a
# check against those facts before it is allowed into the document.

_PLACEHOLDER_RE = re.compile(
    r"YYYY-MM-DD|MM/DD/YYYY|<[A-Za-z][A-Za-z _-]{1,30}>|\[TBD\]|\bTBD\b|"
    r"\bXX+\b|\bN/A\b",
    re.IGNORECASE,
)

# Header fields a narrative must not assert: they are facts about the
# run, and the run knows them.
_ASSERTED_HEADER_RE = re.compile(
    r"^\s*\**\s*(hardware|machine|device|gpu|date|seed|prompt count|"
    r"temperature|model)\s*\**\s*:", re.IGNORECASE | re.MULTILINE,
)

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def _experiment_facts(exp: dict) -> dict:
    """The verifiable truth about one experiment, straight off the record."""
    r = exp.get("results") or {}
    metrics = {k: v for k, v in (r.get("metrics") or {}).items()
               if isinstance(v, (int, float))}
    return {
        "id": exp.get("id", "?"),
        "hypothesis": exp.get("hypothesis", "") or "",
        "archetype": r.get("archetype") or (exp.get("variables") or {}).get("archetype") or "unknown",
        "provenance": str(r.get("provenance") or "unmeasured"),
        "outcome": str(r.get("outcome") or "unknown"),
        "model": r.get("model"),
        "backend": r.get("backend"),
        "platform": (r.get("environment") or {}).get("platform"),
        "runs": r.get("runs"),
        "metrics": metrics,
        "cannot_run_reason": r.get("cannot_run_reason"),
    }


def _allowed_numbers(facts: list[dict]) -> set[float]:
    """Every number a narrative may legitimately state.

    Measured values, plus any number already present in the hypothesis
    text (the model is entitled to restate its own hypothesis, including
    the "by at least 20%" it wrote there).
    """
    allowed: set[float] = set()
    for f in facts:
        for v in f["metrics"].values():
            allowed.add(float(v))
        if f.get("runs") is not None:
            try:
                allowed.add(float(f["runs"]))
            except (TypeError, ValueError):
                pass
        for text in (f["hypothesis"], str(f.get("archetype") or "")):
            for tok in _NUMBER_RE.findall(text):
                try:
                    allowed.add(float(tok))
                except ValueError:
                    pass
    allowed.update(float(i) for i in range(0, 13))  # list numbering, counts
    return allowed


def _number_is_supported(x: float, allowed: set[float]) -> bool:
    """Rounding is legitimate reporting; invention is not."""
    for v in allowed:
        if x == v:
            return True
        tol = max(0.05, abs(v) * 0.02)
        if abs(x - v) <= tol:
            return True
        if v and round(v) == x:
            return True
    return False


def verify_report_narrative(text: str, experiments: list[dict]) -> list[str]:
    """Problems that disqualify a model-written narrative. Empty == clean."""
    problems: list[str] = []
    if not text:
        return ["narrative was empty"]

    ph = _PLACEHOLDER_RE.findall(text)
    if ph:
        problems.append(
            f"unfilled placeholders: {', '.join(sorted(set(ph))[:4])}")

    hdr = _ASSERTED_HEADER_RE.findall(text)
    if hdr:
        problems.append(
            "asserts run facts the narrative cannot know "
            f"({', '.join(sorted({h.lower() for h in hdr})[:4])}) — "
            "these are emitted from the record instead")

    facts = [_experiment_facts(e) for e in experiments]
    allowed = _allowed_numbers(facts)
    unsupported: list[str] = []
    for tok in _NUMBER_RE.findall(text):
        try:
            x = float(tok)
        except ValueError:
            continue
        if not _number_is_supported(x, allowed):
            unsupported.append(tok)
    if unsupported:
        problems.append(
            "numbers not traceable to any measurement: "
            + ", ".join(sorted(set(unsupported))[:6]))

    # A narrative framed around hypotheses the run never tested will
    # mis-attribute real numbers to changes that never happened — the
    # failure that motivated this check. One observed report described
    # "Prefetch Lookahead: increased from 2 to 3 layers" and cited two
    # genuine throughput figures, when the lab had simply measured the
    # same local model five times and varied nothing at all.
    mismatched = [f for f in facts if _hypothesis_subject_mismatch(f)]
    if mismatched:
        problems.append(
            f"{len(mismatched)} measured experiment(s) did not test their "
            "stated hypothesis (the engine measured a local archetype "
            "instead), so any hypothesis-framed narrative mis-attributes "
            "the numbers")

    # Every measured experiment here varied nothing between runs, so
    # differences between them are noise. Prose that reads them as an
    # A/B result is inventing a comparison.
    measured = [f for f in facts if f["provenance"] == "measured"]
    if len(measured) > 1:
        signatures = {(f["archetype"], f.get("model")) for f in measured}
        if len(signatures) == 1 and _READS_AS_COMPARISON_RE.search(text):
            problems.append(
                f"all {len(measured)} measured runs are the same "
                f"{measured[0]['archetype']} measurement of "
                f"`{measured[0].get('model')}` — the narrative compares them "
                "as if something was varied")
    return problems


# Prose that asserts a differential result between runs.
_READS_AS_COMPARISON_RE = re.compile(
    r"\b(compared to|versus|vs\.?|outperform\w*|better than|worse than|"
    r"improve\w*\s+(?:on|over)|increase[ds]?\s+from|decrease[ds]?\s+from|"
    r"baseline)\b",
    re.IGNORECASE,
)


def _hypothesis_subject_mismatch(f: dict) -> str | None:
    """Flag when the thing measured is plainly not the thing claimed.

    The engine maps every hypothesis onto one of a few archetypes and
    measures THAT. So a hypothesis about speculative decoding on some
    235B model becomes a throughput measurement of whatever local model
    is loaded — and the record then says "supported". It is not: the run
    never touched the subject of the claim.
    """
    model = (f.get("model") or "").strip()
    if not model or f["provenance"] != "measured":
        return None
    hyp = f["hypothesis"]
    # Model names in the hypothesis look like Qwen3-235B / Llama-3.2-1B.
    named = re.findall(r"\b[A-Za-z][A-Za-z0-9.]*-?\d+\s*[BbMm]\b|"
                       r"\b(?:Qwen|Llama|Mistral|Gemma|Phi|DeepSeek)[A-Za-z0-9.\-]*",
                       hyp)
    named = [n.strip() for n in named if n and n.strip()]
    if not named:
        return None
    short = model.split(":")[0].lower()
    if any(short in n.lower() or n.lower() in short for n in named):
        return None
    return (f"measured `{model}`; the hypothesis names "
            f"{', '.join(sorted(set(named))[:2])} — this run does not test "
            "that claim")


def completion_status(experiments: list[dict]) -> tuple[str, str, dict]:
    """(level, message, counts) for the end-of-run activity entry.

    "Research complete. Report generated." went out at success level even
    when every experiment came back cannot_run and the report held no
    findings — so a lab with a dead Ollama looked exactly like a lab
    doing science. Observed twice on a real machine: once behind the
    keep_alive 400, once with the service simply stopped.
    """
    total = len(experiments)
    measured = sum(
        1 for e in experiments
        if str(((e.get("results") or {}).get("provenance")) or "") == "measured"
    )
    counts = {"measured": measured, "experiments": total}
    if measured:
        return ("success",
                f"Research complete — {measured}/{total} experiment(s) "
                "measured. Report generated.", counts)
    if total:
        return ("warn",
                f"Research finished with no measurements: 0/{total} "
                "experiments produced data. The report records why.", counts)
    return ("warn",
            "Research finished without running any experiment. "
            "The report records why.", counts)


def measured_facts_block(experiments: list[dict]) -> str:
    """Code-generated ground truth. Never model-written."""
    facts = [_experiment_facts(e) for e in experiments]
    if not facts:
        return ""
    lines = ["## What was actually measured", "",
             "_Emitted from the experiment records, not written by a model._",
             ""]
    envs = {f["platform"] for f in facts if f.get("platform")}
    models = {f["model"] for f in facts if f.get("model")}
    if models:
        lines.append(f"**Model(s) exercised:** {', '.join(sorted(models))}")
    if envs:
        lines.append(f"**Platform:** {', '.join(sorted(envs))}")
    if models or envs:
        lines.append("")

    for f in facts:
        lines.append(f"### `{f['id']}` — {f['archetype']} ({f['provenance']})")
        if f["provenance"] == "measured" and f["metrics"]:
            for k, v in f["metrics"].items():
                lines.append(f"- `{k}` = {v}")
        elif f["provenance"] == "cannot_run":
            lines.append(f"- could not run: {f.get('cannot_run_reason') or 'no reason recorded'}")
        else:
            reason = f.get("cannot_run_reason")
            lines.append(f"- **not tested** — {reason}" if reason
                         else "- not measurable on this machine")
            # A refusal on its own is honest but useless. Where the
            # other loop has a real knob for this, name it; where no
            # knob exists anywhere, say that too.
            if reason:
                try:
                    from arail.experiments.levers import handoff_line
                    lines.append(f"- ➡️ {handoff_line(f['hypothesis'])}")
                except Exception:
                    pass
        mismatch = _hypothesis_subject_mismatch(f)
        if mismatch:
            lines.append(f"- ⚠️ **{mismatch}**")
        lines.append("")
    return "\n".join(lines).rstrip()


class ResearcherAgent:
    """Autonomous research agent that drives experiments toward a goal."""

    def __init__(self) -> None:
        self.goal_store = GoalStore()
        self.tracker = ExperimentTracker()
        self.curator = CuratorAgent()
        # Fast router is resolved lazily via the `_router` property (below):
        # this singleton is constructed at import time, before the registry —
        # or a portal config change — exists. Caching a router here would
        # freeze a possibly-broken binding until process restart.
        self._router_cache = None
        self._router_cfgv: Optional[int] = None
        # Set when a model-written report narrative fails verification, so
        # the fallback report can say why it was withheld.
        self._last_report_problems: Optional[list[str]] = None
        # Lazy: the deep (aeroLLM) model is multi-GB. Don't load it at boot —
        # _active_deep_router() fetches the shared router on first background-
        # safe use, so an idle maximus lab never carries the 2nd inference.
        self._deep_router = None
        self._task: Optional[asyncio.Task] = None
        self._paused = False
        self._status = "idle"  # idle | running | paused | completed | error
        self._objective = ""
        self._completed_steps: list[str] = []
        self._current_task: str | None = None
        self._next_step: str | None = None
        self._pause_reason: str | None = None
        self._swarm_plan_snapshot: dict[str, Any] | None = None
        # Phase 3 educational disclosure: capture WHAT the LLM proposed and
        # WHY we picked the chosen subset, so the UI can teach the user
        # "the Researcher considered N hypotheses, ran these K, set
        # these N-K aside." Reset every time _plan_research runs.
        self._planning_trace: dict[str, Any] | None = None

    @property
    def _router(self):
        """Fast (Tier 0) router, re-resolved when the registry config changes.

        Never caches a None permanently: if resolution failed last time we
        re-attempt on the next access (the registry is the throttle — it just
        returns the current entry's state, no heavy construction happens).
        """
        cfgv = None
        try:
            from arail.registry import get_registry
            cfgv = get_registry().config_version
        except Exception:
            pass
        if self._router_cache is None or cfgv != self._router_cfgv:
            self._router_cache = _get_router()
            self._router_cfgv = cfgv
        return self._router_cache

    @_router.setter
    def _router(self, value) -> None:
        # Tests (and callers with a bespoke router) may inject directly.
        self._router_cache = value
        try:
            from arail.registry import get_registry
            self._router_cfgv = get_registry().config_version
        except Exception:
            self._router_cfgv = None

    @property
    def status(self) -> str:
        return self._status

    # ── Control ──────────────────────────────────────────────────────

    def start(self, parsed_goal: Dict[str, Any], *, delay: int | None = None,
              resume_state: Dict[str, Any] | None = None) -> None:
        """Start a run — or, with ``resume_state``, re-enter an interrupted
        one at its last persisted checkpoint (see _run's resume semantics)."""
        if self._task and not self._task.done():
            self._task.cancel()
        self._swarm_plan_snapshot = self._swarm_plan(parsed_goal) or (
            resume_state.get("swarm_plan_snapshot") if resume_state else None)
        self._paused = bool(resume_state.get("paused")) if resume_state else False
        self._status = "paused" if self._paused else "running"
        self._objective = parsed_goal.get("goal", parsed_goal.get("primary_objective", ""))
        if self._swarm_plan_snapshot:
            self._objective = str(
                self._swarm_plan_snapshot.get("mission_brief")
                or self._objective
            )
        if resume_state:
            self._completed_steps = list(resume_state.get("completed_steps") or [])
            self._planning_trace = resume_state.get("planning_trace")
            self._current_task = "Resuming interrupted research"
            self._next_step = resume_state.get("next_step") or "Continue from checkpoint"
        else:
            self._completed_steps = []
            self._current_task = "Queued for research"
            self._next_step = "Plan research hypotheses"
        self._pause_reason = resume_state.get("pause_reason") if resume_state else None
        self._sync_workflow()
        delay_sec = startup_delay_seconds() if delay is None else max(0, delay)
        self._task = asyncio.create_task(
            self._run(parsed_goal, delay_sec, resume_state=resume_state))

    def pause(self) -> None:
        self._paused = True
        self._status = "paused"
        self._pause_reason = "Paused by user"
        self._sync_workflow()
        self._set_swarm_lane_status("paused", current_task="Paused by user", next_step="Resume the swarm plan")
        activity_log.emit("researcher", "Research paused by user.", "warn")

    def resume(self) -> None:
        self._paused = False
        self._status = "running"
        self._pause_reason = None
        self._sync_workflow()
        self._set_swarm_lane_status("running", current_task="Swarm plan resumed", next_step="Continue assigned lane")
        activity_log.emit("researcher", "Research resumed.", "info")

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._status = "idle"
        self._paused = False
        self._pause_reason = None
        self._current_task = "Stopped"
        self._next_step = None
        self._sync_workflow()
        self._set_swarm_lane_status("idle", current_task="Stopped", next_step=None)
        activity_log.emit("researcher", "Research stopped.", "warn")

    def _sync_workflow(self, *, progress: float | None = None) -> None:
        current = self.goal_store.get_current() or {}
        redirect = _active_redirect()
        self._persist_run_state(current)
        update_agent_workflow(
            "researcher",
            status=self._status,
            objective=self._objective,
            current_task=self._current_task,
            next_step=self._next_step,
            completed_steps=list(self._completed_steps),
            paused=self._paused,
            pause_reason=self._pause_reason,
            progress=progress if progress is not None else current.get("progress", 0),
            recent_actions=list(self._completed_steps[-3:]),
            chatter={"too_chatty": False},
            metadata={
                "experiments": len(current.get("experiments", [])),
                "intent": current.get("parsed", {}).get("intent") if isinstance(current.get("parsed"), dict) else None,
                "redirect": redirect,
            },
        )

    def _persist_run_state(self, current: Dict[str, Any]) -> None:
        """Durable snapshot of the in-memory run state (crash recovery).

        Written on every _sync_workflow transition; cleared when the run
        reaches a state with nothing to resume. The boot reconciliation
        hook reads this to decide whether (and where) to auto-resume.
        """
        from arail import goals as goals_mod
        try:
            if self._status in ("idle", "completed") or not current:
                goals_mod.clear_run_state()
                return
            goals_mod.save_run_state({
                "goal_id": current.get("id"),
                "status": self._status,
                "paused": self._paused,
                "pause_reason": self._pause_reason,
                "current_task": self._current_task,
                "next_step": self._next_step,
                "completed_steps": list(self._completed_steps),
                "planning_trace": self._planning_trace,
                "swarm_plan_snapshot": self._swarm_plan_snapshot,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:  # noqa: BLE001  # persistence must never break a run
            pass

    def _advance_workflow(self, completed_step: str | None, current_task: str, next_step: str | None, *, progress: float | None = None) -> None:
        if completed_step:
            self._completed_steps.append(completed_step)
        self._current_task = current_task
        self._next_step = next_step
        self._pause_reason = None
        self._sync_workflow(progress=progress)

    def _swarm_plan(self, parsed_goal: Dict[str, Any]) -> dict[str, Any] | None:
        plan = parsed_goal.get("swarm_plan")
        return plan if isinstance(plan, dict) else None

    def _enabled_swarm_workers(self, parsed_goal: Dict[str, Any]) -> list[dict[str, Any]]:
        plan = self._swarm_plan(parsed_goal)
        if not plan:
            return []
        workers = plan.get("workers")
        if not isinstance(workers, list):
            return []
        return [worker for worker in workers if isinstance(worker, dict) and worker.get("enabled", True)]

    def _swarm_prompt_block(self, parsed_goal: Dict[str, Any]) -> str:
        plan = self._swarm_plan(parsed_goal)
        if not plan:
            return ""
        workers = self._enabled_swarm_workers(parsed_goal)
        if not workers:
            return ""
        worker_lines = "\n".join(
            f"- {worker.get('label', worker.get('id', 'worker'))}: {worker.get('role', '')} -> {worker.get('deliverable', '')}"
            for worker in workers
        )
        review = plan.get("review") if isinstance(plan.get("review"), dict) else {}
        questions = review.get("open_questions") if isinstance(review.get("open_questions"), list) else []
        question_lines = "\n".join(f"- {question}" for question in questions[:4])
        return (
            f"Swarm mission brief: {plan.get('mission_brief', '')}\n"
            f"Swarm archetype: {plan.get('goal_archetype', 'general')}\n"
            f"Enabled worker lanes:\n{worker_lines}\n"
            f"Operator notes: {plan.get('operator_notes', '') or 'none'}\n"
            f"Open questions to tighten before you converge:\n{question_lines or '- none'}\n\n"
        )

    def _retire_swarm_lanes(self, active_worker_ids: set[str] | None = None) -> None:
        active = active_worker_ids or set()
        for worker_id in known_swarm_worker_ids():
            if worker_id in active:
                continue
            update_agent_workflow(
                f"swarm-{worker_id}",
                status="idle",
                objective="Swarm lane idle",
                current_task="Idle",
                next_step=None,
                completed_steps=[],
                paused=False,
                pause_reason=None,
                progress=0.0,
                recent_actions=[],
                chatter={"too_chatty": False},
                metadata={"swarm_lane": worker_id},
            )

    def _set_swarm_lane_status(self, status: str, *, current_task: str, next_step: str | None) -> None:
        plan = self._swarm_plan_snapshot
        if not plan:
            self._retire_swarm_lanes()
            return
        workers = [worker for worker in plan.get("workers", []) if isinstance(worker, dict) and worker.get("enabled", True)]
        active_ids = {str(worker.get("id") or "") for worker in workers}
        self._retire_swarm_lanes(active_ids)
        for worker in workers:
            worker_id = str(worker.get("id") or "")
            update_agent_workflow(
                f"swarm-{worker_id}",
                status=status,
                objective=str(worker.get("purpose") or worker.get("role") or "Swarm lane"),
                current_task=current_task,
                next_step=next_step,
                completed_steps=["Approved into swarm plan"],
                paused=status == "paused",
                pause_reason=self._pause_reason if status == "paused" else None,
                progress=(self.goal_store.get_current() or {}).get("progress", 0),
                recent_actions=[str(worker.get("deliverable") or "")],
                chatter={"too_chatty": False},
                metadata={
                    "swarm_lane": worker_id,
                    "goal_archetype": plan.get("goal_archetype"),
                    "deliverable": worker.get("deliverable"),
                    "kind": worker.get("kind"),
                },
            )

    def _sync_swarm_phase(self, parsed_goal: Dict[str, Any], phase_id: str, *, completed_worker_ids: set[str] | None = None) -> None:
        plan = self._swarm_plan(parsed_goal)
        if not plan:
            self._retire_swarm_lanes()
            return
        workers = self._enabled_swarm_workers(parsed_goal)
        active_ids = {str(worker.get("id") or "") for worker in workers}
        self._retire_swarm_lanes(active_ids)
        phases = plan.get("phases") if isinstance(plan.get("phases"), list) else []
        phase = next((item for item in phases if isinstance(item, dict) and item.get("id") == phase_id), None) or {}
        running_ids = set(phase.get("worker_ids") or [])
        completed_ids = completed_worker_ids or set()
        phase_title = str(phase.get("title") or "Swarm phase")
        for worker in workers:
            worker_id = str(worker.get("id") or "")
            status = "planned"
            if worker_id in completed_ids:
                status = "completed"
            elif worker_id in running_ids:
                status = "running"
            update_agent_workflow(
                f"swarm-{worker_id}",
                status=status,
                objective=str(worker.get("purpose") or worker.get("role") or "Swarm lane"),
                current_task=phase_title if status == "running" else str(worker.get("deliverable") or phase_title),
                next_step=None if status == "completed" else str(worker.get("deliverable") or "Continue assigned lane"),
                completed_steps=["Approved into swarm plan"] + ([f"Completed {phase_title}"] if status == "completed" else []),
                paused=False,
                pause_reason=None,
                progress=(self.goal_store.get_current() or {}).get("progress", 0),
                recent_actions=[phase_title],
                chatter={"too_chatty": False},
                metadata={
                    "swarm_lane": worker_id,
                    "goal_archetype": plan.get("goal_archetype"),
                    "deliverable": worker.get("deliverable"),
                    "kind": worker.get("kind"),
                    "phase": phase_id,
                },
            )

    # ── Core loop ────────────────────────────────────────────────────

    def _reload_experiments(self) -> list[Dict[str, Any]]:
        """Reload this goal's experiments from the tracker's on-disk records
        (full definition + status + observations survive restarts)."""
        current = self.goal_store.get_current() or {}
        out: list[Dict[str, Any]] = []
        for exp_id in current.get("experiments", []) or []:
            try:
                out.append(self.tracker._load(exp_id))
            except Exception:  # noqa: BLE001  # missing file → just skip it
                continue
        return out

    async def _run(self, parsed_goal: Dict[str, Any], delay_sec: int = 0,
                   resume_state: Dict[str, Any] | None = None) -> None:
        # ── Resume checkpoint (crash/restart recovery) ────────────────
        # Honest semantics: below 0.3 nothing reusable was persisted
        # (hypotheses never hit disk) so resume == fresh re-plan; from 0.3
        # experiments live in the tracker and steps re-enter after the
        # last completed checkpoint, skipping already-completed work.
        resume_p = 0.0
        if resume_state is not None:
            current0 = self.goal_store.get_current() or {}
            try:
                resume_p = float(current0.get("progress") or 0.0)
            except (TypeError, ValueError):
                resume_p = 0.0
            if resume_p >= 0.3:
                activity_log.emit(
                    "researcher",
                    f"Resuming from checkpoint (progress {resume_p:.1f}) — "
                    f"{len(current0.get('experiments', []) or [])} experiments on disk.",
                    "info", {"resume": True, "progress": resume_p})
            else:
                activity_log.emit(
                    "researcher",
                    "Resuming interrupted research from planning (no experiment "
                    "checkpoint yet) — re-planning from the top.",
                    "info", {"resume": True})
                resume_p = 0.0

        goal_text = parsed_goal.get("goal", parsed_goal.get("primary_objective", ""))
        domain = parsed_goal.get("domain", "general")
        intent = parsed_goal.get("intent", _get_lab_intent())
        from arail.identity import effective_identity
        intent_name = parsed_goal.get("intent_name",
                                       effective_identity().intent_name)
        swarm_plan = self._swarm_plan(parsed_goal)

        try:
            if delay_sec:
                self._advance_workflow(None, f"Waiting {delay_sec}s courtesy delay", "Plan research hypotheses", progress=0.0)
                activity_log.emit("researcher",
                                  f"Queued — starting in {delay_sec}s "
                                  f"({window_label()}). Halt anytime from the dashboard.",
                                  "info")
                slept = 0
                while slept < delay_sec:
                    if jobs_halted():
                        activity_log.emit("researcher",
                                          "Halted before start.", "warn")
                        self._status = "idle"
                        return
                    await asyncio.sleep(min(1, delay_sec - slept))
                    slept += 1

            # Consult the knowledge base FIRST so the researcher's opening
            # move is visibly grounded in what the lab already knows. The
            # KB ships LanceDB-backed semantic search (see
            # arail.pkb.search), so a single-shot fuzzy query like
            # "Improve AeroLLM SSD inference" lands on the right primer
            # without us having to split keywords or strip stopwords.
            #
            # We still drop structural noise (manifests, generated
            # indexes, wiki cache) because semantic search will happily
            # rank them on word overlap.
            def _is_structural(path: str) -> bool:
                p = path.lower()
                return (
                    p.endswith(".json")
                    or p.endswith("/index.md")
                    or p == "index.md"
                    or "/.wiki-cache/" in p
                    or "/manifest" in p
                )

            try:
                kb_hits = [
                    h for h in pkb_mod.search_for_agents(goal_text)
                    if h.get("path") and not _is_structural(h["path"])
                ][:5]
            except Exception:
                kb_hits = []
            if kb_hits:
                hit_names = ", ".join(h.get("name", "?") for h in kb_hits[:3])
                more = f" (+{len(kb_hits) - 3} more)" if len(kb_hits) > 3 else ""
                activity_log.emit(
                    "researcher",
                    f"Consulting the KB for context — found {len(kb_hits)} entries on "
                    f"'{goal_text[:60]}': {hit_names}{more}",
                    "info",
                    {"kb_hits": [h.get("path") for h in kb_hits]},
                )
            else:
                activity_log.emit(
                    "researcher",
                    f"No KB entries match '{goal_text[:60]}' yet — proceeding from prior knowledge.",
                    "info",
                )

            # Read the lab brief once per run — world identity + approved-KB
            # digest + redirects ground the planning prompt (visible on the
            # Knowledge page's Agent Focus so the human sees the same text).
            brief_block = _brief_prompt_block()
            if brief_block:
                try:
                    from arail.lab_brief import get_cached_brief
                    _b = get_cached_brief()
                    _w = (_b.get("world") or {}).get("display_name")
                    _k = _b.get("knowledge") or {}
                    activity_log.emit(
                        "researcher",
                        "Read the lab brief — "
                        + (f"world '{_w}', " if _w else "no world mounted, ")
                        + f"{_k.get('approved_total', 0)} approved items, "
                        + f"{_k.get('pending_total', 0)} pending review",
                        "info",
                    )
                except Exception:  # noqa: BLE001
                    pass
            self._brief_block = brief_block

            activity_log.emit("researcher",
                              f"Starting research ({intent_name}): {goal_text}",
                              "success")
            # One honest line per run about the deep (aeroLLM) model. When
            # deep passes are off/unavailable the fallback used to be silent
            # (_get_deep_router returns None and _deep_complete emits nothing)
            # — the actual deep-use event still fires from _deep_complete.
            try:
                from arail.agents import deep_policy as _dp
                if not research_deep_enabled():
                    activity_log.emit(
                        "researcher",
                        "Deep passes off for this run: switched off "
                        "(AEROLLM_RESEARCH) — running on the fast model.",
                        "info")
                else:
                    _ok, _code, _detail = _dp.explain(foreground=False)
                    if not _ok:
                        activity_log.emit(
                            "researcher",
                            f"Deep passes off for this run: {_detail}.",
                            "info")
            except Exception:  # noqa: BLE001
                pass
            if swarm_plan:
                worker_labels = ", ".join(
                    str(worker.get("label") or worker.get("id") or "worker")
                    for worker in self._enabled_swarm_workers(parsed_goal)
                )
                activity_log.emit(
                    "researcher",
                    f"Swarm plan active — coordinating {len(self._enabled_swarm_workers(parsed_goal))} worker lanes: {worker_labels}",
                    "info",
                    {"swarm": swarm_plan},
                )
                self.goal_store.add_finding(
                    {
                        "type": "swarm-plan",
                        "summary": swarm_plan.get("mission_brief", ""),
                        "workers": [worker.get("id") for worker in self._enabled_swarm_workers(parsed_goal)],
                    }
                )
                self._sync_swarm_phase(parsed_goal, "shape")
            redirect = _active_redirect()
            redirect_flags = redirect_profile(redirect)
            if redirect:
                activity_log.emit(
                    "researcher",
                    f"Redirect active — {str(redirect.get('instruction') or '')[:160]}",
                    "warn",
                    {"redirect": redirect},
                )
                if redirect_flags["focus_measurement"]:
                    self._current_task = "Reframing the run around measurement and evaluation"
                    self._next_step = "Design eval-ready experiments"
                    self._sync_workflow(progress=0.0)
            self._advance_workflow(None, "Planning research hypotheses", "Design experiments", progress=0.0)

            # Step 1: Plan research — generate hypotheses
            # Step 2: Design experiments for each hypothesis
            # (resume ≥0.3: both already done — experiments reload from the
            #  tracker's on-disk records instead of re-planning)
            if resume_p < 0.3:
                await self._wait_if_paused()
                hypotheses = self._plan_research(parsed_goal)
                activity_log.emit("researcher",
                                  f"Generated {len(hypotheses)} research hypotheses.",
                                  "info", {"hypotheses": hypotheses, "progress": 0.1})
                self.goal_store.update_progress(0.1)
                if swarm_plan:
                    shape_phase = next((phase for phase in swarm_plan.get("phases", []) if phase.get("id") == "shape"), {})
                    self._sync_swarm_phase(parsed_goal, "branch", completed_worker_ids=set(shape_phase.get("worker_ids") or []))
                self._advance_workflow("Planned research hypotheses", "Designing experiments", "Gather sources", progress=0.1)

                await self._wait_if_paused()
                experiments = []
                for i, hyp in enumerate(hypotheses):
                    exp = self._design_experiment(hyp, domain)
                    experiments.append(exp)
                    self.goal_store.link_experiment(exp["id"])
                    activity_log.emit("researcher",
                                      f"Created experiment {exp['id']}: {exp['hypothesis'][:80]}",
                                      "info", {"experiment_id": exp["id"]})
                    await asyncio.sleep(0.5)  # pacing for UX
                # Guarantee at least one genuinely-measurable experiment: if every
                # hypothesis mapped to 'unmeasured', append a lab-capability
                # baseline so a run always produces a real measured-or-refused
                # result rather than only paper hypotheses (ARCHITECTURE §A4).
                def _archetype_of(e):
                    return (e.get("variables") or {}).get("archetype", "unmeasured")
                if experiments and all(_archetype_of(e) == "unmeasured" for e in experiments):
                    kind = ("model_throughput" if self._router is not None
                            else "retrieval_quality")
                    baseline = self.tracker.create(
                        hypothesis=f"Baseline: measure this lab's {'model responsiveness' if kind == 'model_throughput' else 'approved-knowledge retrieval'} for '{domain}'.",
                        methodology="Lab-capability baseline (auto-added because no "
                                    "hypothesis was measurable on-device).",
                        variables={"domain": domain, "archetype": kind,
                                   "auto_baseline": True},
                        duration_days=None, metrics=[], domain=domain)
                    experiments.append(baseline)
                    self.goal_store.link_experiment(baseline["id"])
                    activity_log.emit("researcher",
                                      "Added a measurable lab-capability baseline "
                                      "(no hypothesis was measurable on-device).", "info")
                activity_log.emit("researcher", "Experiments designed.", "info", {"progress": 0.3})
                self.goal_store.update_progress(0.3)
                self._advance_workflow("Designed experiments", "Gathering sources", "Run experiments", progress=0.3)
            else:
                experiments = self._reload_experiments()
                done_count = sum(1 for e in experiments
                                 if str(e.get("status")) == "completed")
                activity_log.emit(
                    "researcher",
                    f"Checkpoint restore: {len(experiments)} experiments "
                    f"reloaded, {done_count} already complete.",
                    "info", {"resume": True})

            # Step 3: Gather sources via Curator (resume ≥0.5: already done)
            if resume_p < 0.5:
                await self._wait_if_paused()
                redirect = _active_redirect()
                redirect_flags = redirect_profile(redirect)
                if redirect_flags["skip_fetch"]:
                    activity_log.emit(
                        "researcher",
                        "Redirect active — skipping new source fetching and tightening the eval path instead.",
                        "warn",
                        {"redirect": redirect, "progress": 0.5},
                    )
                else:
                    activity_log.emit("researcher",
                                      "Querying curator for relevant data sources...", "info")
                    proposals = self.curator.propose_sources(parsed_goal)
                    if proposals:
                        consent_results = self.curator.submit_proposals(proposals)
                        approved = [r for r in consent_results if r["status"] == "auto_approved"]
                        pending = [r for r in consent_results if r["status"] == "pending"]
                        # Note: this records *permission* to reach these
                        # domains — it does not download anything. The lab runs
                        # on your approved local knowledge; approval just clears
                        # a source for a future explicit fetch.
                        if approved:
                            activity_log.emit("researcher",
                                              f"{len(approved)} source domain(s) already permitted "
                                              "(on your allowlist). Nothing fetched.",
                                              "info")
                        if pending:
                            activity_log.emit("researcher",
                                              f"{len(pending)} source domain(s) noted — approve them on "
                                              "the Agents page to permit future access. Nothing fetched yet.",
                                              "info")
                    else:
                        activity_log.emit("researcher",
                                          "No external sources needed — running fully local.",
                                          "info")
                activity_log.emit("researcher", "Source gathering complete.", "info", {"progress": 0.5})
                self.goal_store.update_progress(0.5)
                self._advance_workflow("Gathered sources", "Running experiments", "Analyze results", progress=0.5)

            # Step 4: Run experiments — for REAL. Each experiment is a genuine
            # on-device measurement (throughput / prompt-variant / KB-retrieval)
            # via the mini-experiments engine. The liveness the old sleep loop
            # faked now comes from actual work; every observation is measured.
            # Completion happens here too (the old fabricated "analyze" step is
            # gone), so a crash-resume finds finished experiments and skips them.
            if resume_p < 0.9:
                await self._wait_if_paused()
                ctx = self._build_experiment_context(intent)
                for exp in experiments:
                    if str(exp.get("status")) == "completed":
                        # Idempotent re-entry: never re-run finished work.
                        continue
                    if jobs_halted():
                        activity_log.emit("researcher", "Halted before running "
                                          f"experiment {exp['id']}.", "warn")
                        self._status = "idle"
                        return
                    self.tracker.start(exp["id"])
                    archetype = (exp.get("variables") or {}).get("archetype", "?")
                    activity_log.emit("researcher",
                                      f"Running experiment {exp['id']} "
                                      f"({archetype}, measured)…", "info")
                    self._current_task = f"Running experiment {exp['id']}"
                    self._next_step = "Generate report"
                    self._sync_workflow(progress=0.6)

                    from arail.research import mini_experiments as mx
                    exp_id = exp["id"]
                    ctx.observe = (lambda text, data, _id=exp_id:
                                   self._log_experiment_observation(_id, text, data))
                    result = await mx.run_experiment(exp, ctx)
                    mx.maybe_interpret(result, ctx)
                    self.tracker.complete(
                        exp_id, result.to_results_payload(ctx),
                        result.conclusion, result.success)
                    level = ("success" if result.success else
                             "warn" if result.provenance == "measured" else "info")
                    activity_log.emit("researcher",
                                      f"Experiment {exp_id} — {result.outcome} "
                                      f"({result.provenance}).", level)
                activity_log.emit("researcher", "Experiments complete.", "info", {"progress": 0.7})
                self.goal_store.update_progress(0.7)
                if swarm_plan:
                    branch_phase = next((phase for phase in swarm_plan.get("phases", []) if phase.get("id") == "branch"), {})
                    branch_done = set(branch_phase.get("worker_ids") or [])
                    self._sync_swarm_phase(parsed_goal, "challenge", completed_worker_ids=branch_done)
                self._advance_workflow("Ran experiments", "Generating report", "Write report to knowledge base", progress=0.7)

            # Step 5: Collect completed experiments (measurement + analysis both
            # happened in Step 4 — reload the finished records for the report).
            completed_experiments: list[dict[str, Any]] = []
            for exp in experiments:
                try:
                    completed_experiments.append(self.tracker._load(exp["id"]))
                except Exception:
                    completed_experiments.append(exp)
            self.goal_store.update_progress(0.9)

            # Step 6: Generate report
            await self._wait_if_paused()
            report = self._generate_report(parsed_goal, completed_experiments)
            self.goal_store.set_report(report)
            self.goal_store.update_progress(1.0)

            # Write results to PKM
            try:
                from arail import pkb as pkb_mod
                goal_id = parsed_goal.get("id", domain)[:40]
                pkb_mod.write_agent_research(goal_id, report)
                for exp in completed_experiments:
                    exp_md = self._experiment_markdown(exp)
                    pkb_mod.write_agent_experiment(exp["id"], exp_md)
                rollup_writer = getattr(pkb_mod, "write_agent_experiment_rollup", None)
                if callable(rollup_writer):
                    rollup_writer(completed_experiments, domain=domain)
                pkb_mod.write_agent_recommendation(
                    f"# Recommendations — {domain}\n\n"
                    f"Based on {len(completed_experiments)} experiments for: {goal_text}\n\n"
                    f"Review the full report in agents/research/\n"
                )
                activity_log.emit("researcher",
                                  "Results written to knowledge base (lab/pkm/agents/).",
                                  "info")
                self._current_task = "Writing report to knowledge base"
                self._next_step = "Finalize research run"
                self._sync_workflow(progress=1.0)
            except Exception as e:
                activity_log.emit("researcher",
                                  f"PKM write failed ({type(e).__name__}: {str(e)[:80]}). "
                                  f"Report is still in lab/data/goals/current.json.",
                                  "warn")

            # Kick the wiki to recompile — debounced so a burst of
            # write_agent_* calls only triggers one rebuild.
            try:
                from arail import wiki as _wiki
                _wiki.schedule_rebuild()
            except Exception:  # pragma: no cover
                pass

            # Say what the run actually produced. "Research complete.
            # Report generated." was emitted at success level even when
            # every experiment came back cannot_run and the report held
            # no findings at all — which is how a lab with a dead model
            # looked identical to a lab doing science.
            _level, _msg, _counts = completion_status(completed_experiments)
            activity_log.emit("researcher", _msg, _level,
                              {"report_preview": report[:200], "progress": 1.0,
                               **_counts})
            self._status = "completed"
            self._set_swarm_lane_status("completed", current_task="Swarm synthesis complete", next_step=None)
            self._advance_workflow("Generated final report", "Research complete", None, progress=1.0)

        except asyncio.CancelledError:
            activity_log.emit("researcher", "Research cancelled.", "warn")
            self._status = "idle"
            self._current_task = "Cancelled"
            self._next_step = None
            self._set_swarm_lane_status("idle", current_task="Cancelled", next_step=None)
            self._sync_workflow()
        except Exception as e:
            activity_log.emit("researcher", f"Research error: {e}", "error")
            self._status = "error"
            self._current_task = f"Error: {type(e).__name__}"
            self._next_step = "Inspect recent activity and retry"
            self._set_swarm_lane_status("error", current_task=self._current_task, next_step=self._next_step)
            self._sync_workflow()

    async def _wait_if_paused(self) -> None:
        if jobs_halted():
            raise asyncio.CancelledError("halted")
        while self._paused:
            if jobs_halted():
                raise asyncio.CancelledError("halted")
            await asyncio.sleep(0.5)

    def _active_deep_router(self):
        """Lazily return the shared aeroLLM deep router, but only when the deep
        policy says it's safe to run the 2nd inference in the background right
        now: maximus tier, heavy/idle window, no operator presence, and Metal
        memory pressure below the background ceiling. The heavy model is loaded
        on first such call — never at boot — and is shared process-wide so chat
        + agents never hold two copies. During active hours, when the operator
        is present, or under memory pressure we force the fast SLM path so the
        lab stays responsive — and never OOMs."""
        try:
            from arail.agents import deep_policy
            if not deep_policy.prefer_deep(foreground=False):
                return None
            # Honors AEROLLM_RESEARCH + returns the shared, cached deep router
            # (constructs the Runtime on first call only).
            self._deep_router = _get_deep_router()
            return self._deep_router
        except Exception:
            # If the policy can't be consulted, stay conservative: only outside
            # the active window, and don't force a fresh load.
            if current_window() == "active":
                return None
            return self._deep_router

    # ── Research methods (LLM-enhanced with heuristic fallback) ────

    # Cap on how many hypotheses actually become experiments per run.
    # Above this is captured as alternatives the user can inspect in the
    # Phase 3 brief disclosure ("the Researcher considered N, ran K").
    _CHOSEN_LIMIT = 5

    def _plan_research(self, parsed_goal: Dict[str, Any]) -> List[str]:
        """Generate hypotheses from the goal.  Uses LLM if available.

        Side effect: populates ``self._planning_trace`` with the chosen
        hypotheses, the alternatives that were considered but not run,
        the LLM raw response (when available), the source ("llm" or
        "heuristic"), and a generated_at timestamp. The trace is what
        the /api/research/planning-trace endpoint returns and what the
        Phase 3 educational disclosure surfaces in the UI.
        """
        goal_text = parsed_goal.get("goal", "")
        domain = parsed_goal.get("domain", "general")
        intent = parsed_goal.get("intent", _get_lab_intent())
        sub_objectives = parsed_goal.get("sub_objectives", [])
        sys_ctx = _get_system_context(intent)
        redirect_block = _redirect_prompt_block(_active_redirect())

        # Widen the LLM candidate pool from 3-5 to 5-8 so we have a
        # genuine alternatives bench to expose in the UI. The first
        # _CHOSEN_LIMIT survive into experiments; the rest become
        # alternatives.
        # sys_ctx is the stable prefix → cached system block (Claude); the rest
        # is the per-run volatile body. See _llm_complete / build_chat_payload.
        # The lab brief (fetched once in _run) rides ahead of the redirect —
        # both are volatile per-run steering; sys_ctx stays the cached prefix.
        brief_block = getattr(self, "_brief_block", "") or _brief_prompt_block()
        prompt = (
            f"{brief_block}"
            f"{redirect_block}"
            f"{self._swarm_prompt_block(parsed_goal)}"
            f"Given the goal below, generate 5-8 testable hypotheses "
            f"as a numbered list. Each hypothesis should be specific, "
            f"measurable, and grounded in the domain. Order them by "
            f"how directly they address the goal — the strongest first.\n\n"
            f"Goal: {goal_text}\nDomain: {domain}\n"
            f"Sub-objectives: {', '.join(sub_objectives) if sub_objectives else 'none'}\n\n"
            f"Hypotheses:"
        )
        llm_text = _deep_complete(self._active_deep_router(), self._router, prompt, max_tokens=600, system=sys_ctx)
        if llm_text:
            all_candidates = [
                cleaned for line in llm_text.split("\n")
                if (cleaned := _normalize_hypothesis_line(line))
            ]
            if all_candidates:
                chosen = all_candidates[:self._CHOSEN_LIMIT]
                alternatives = all_candidates[self._CHOSEN_LIMIT:]
                self._record_planning_trace(
                    chosen=chosen,
                    alternatives=alternatives,
                    source="llm",
                    llm_response=llm_text,
                    rationale=(
                        "The LLM was asked to order hypotheses by how directly "
                        f"they address the goal. The first {len(chosen)} entered "
                        f"the experiment queue; "
                        f"{len(alternatives)} ranked lower and were set aside as "
                        "alternatives the lab can swap in if you change framing."
                    ),
                )
                return chosen

        # Heuristic fallback
        hypotheses: list[str] = []
        if sub_objectives:
            for obj in sub_objectives[:8]:
                hypotheses.append(
                    f"Optimizing '{obj}' will contribute to: {goal_text}")
            rationale = (
                f"No LLM available for hypothesis generation; fell back to the "
                f"sub-objective heuristic. Each of the {len(hypotheses)} "
                f"sub-objectives became one hypothesis."
            )
        else:
            relevant = matched_keywords(goal_text, domain)
            if relevant:
                for kw in relevant[:6]:
                    hypotheses.append(
                        f"Focusing on {kw} optimization is key to: {goal_text}")
                rationale = (
                    "No LLM available; fell back to the domain-keyword "
                    f"heuristic. Matched {len(relevant)} keywords from the "
                    f"{domain} domain in the goal text."
                )
            if not hypotheses:
                hypotheses = [
                    f"A systematic approach to '{goal_text}' will yield measurable results",
                    f"Iterative experimentation will identify optimal parameters for: {goal_text}",
                ]
                rationale = (
                    "No LLM available and no domain keyword matched; fell back "
                    "to the generic systematic-approach hypothesis pair."
                )
        chosen = hypotheses[:self._CHOSEN_LIMIT]
        alternatives = hypotheses[self._CHOSEN_LIMIT:]
        self._record_planning_trace(
            chosen=chosen,
            alternatives=alternatives,
            source="heuristic",
            llm_response=None,
            rationale=rationale,
        )
        return chosen

    def _record_planning_trace(
        self,
        *,
        chosen: list[str],
        alternatives: list[str],
        source: str,
        llm_response: str | None,
        rationale: str,
    ) -> None:
        """Capture the planning step's reasoning for educational disclosure.

        Stored in-process only — no on-disk schema migration. Rebuilt
        each time _plan_research runs. Returned by GET /api/research/
        planning-trace and rendered in the Phase 3 research brief.
        """
        from datetime import datetime, timezone
        self._planning_trace = {
            "chosen": list(chosen),
            "alternatives": list(alternatives),
            "source": source,
            "llm_response": llm_response,
            "rationale": rationale,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def get_planning_trace(self) -> dict[str, Any] | None:
        """Public read of the most recent planning trace (or None)."""
        if not self._planning_trace:
            return None
        # Defensive copy so callers can't mutate the agent's internal state.
        trace = self._planning_trace
        return {
            "chosen": list(trace.get("chosen") or []),
            "alternatives": list(trace.get("alternatives") or []),
            "source": trace.get("source"),
            "llm_response": trace.get("llm_response"),
            "rationale": trace.get("rationale"),
            "generated_at": trace.get("generated_at"),
        }

    def _design_experiment(self, hypothesis: str, domain: str) -> Dict[str, Any]:
        """Create an experiment from a hypothesis, tagged with the on-device
        measurement archetype the engine will actually run (or 'unmeasured')."""
        from arail.research import mini_experiments as mx
        redirect = _active_redirect()
        archetype, unmeasurable_reason = mx.classify_hypothesis(hypothesis)
        archetype = archetype or "unmeasured"
        methodology = mx.ARCHETYPE_METHODOLOGY.get(archetype, "")
        if unmeasurable_reason:
            # Refuse rather than substitute. Mapping "increase prefetch
            # depth" onto a plain throughput run measured something real
            # and then reported it as evidence for a change that never
            # happened.
            methodology = f"Not run: {unmeasurable_reason}"
        metrics = mx.ARCHETYPE_METRICS.get(archetype, [])
        return self.tracker.create(
            hypothesis=hypothesis,
            methodology=methodology,
            variables={
                "domain": domain,
                "archetype": archetype,
                "unmeasurable_reason": unmeasurable_reason or "",
                "redirect_preset": str((redirect or {}).get("preset") or ""),
            },
            duration_days=None,
            metrics=metrics,
            domain=domain,
        )

    def _build_experiment_context(self, intent: str | None):
        """Assemble the injected context the mini-experiments engine runs in —
        the fast router, an approved-KB search, halt/pause callbacks, and the
        per-experiment time budget. No fabricated data ever enters here."""
        from arail.research.mini_experiments import ExperimentContext
        from arail import pkb as pkb_mod
        model_name = ""
        backend_name = ""
        try:
            from arail.registry import resolve
            entry = resolve("fast", tab="research").entry
            if entry is not None:
                model_name = getattr(entry, "display_name", "") or getattr(entry, "model_id", "")
                backend_name = getattr(entry, "backend", "") or ""
        except Exception:
            pass

        def _kb_search(query: str, k: int = 5):
            try:
                return pkb_mod.search_for_agents(query)[:k]
            except Exception:
                return []

        def _kb_titles():
            try:
                from arail.compiled_kb import approved_paths
                from pathlib import Path as _P
                return sorted({_P(p).stem for p in approved_paths()})
            except Exception:
                return []

        budget = max(1.0, float(os.getenv("LAB_EXP_RUNTIME_SEC", "60")))
        return ExperimentContext(
            router=self._router, kb_search=_kb_search, kb_approved_titles=_kb_titles,
            halt_check=jobs_halted, pause_wait=self._wait_if_paused,
            time_budget_sec=budget, model_name=model_name, backend_name=backend_name)

    def _log_experiment_observation(self, exp_id: str, text: str,
                                    data: Dict[str, Any]) -> None:
        """Record a REAL, measured observation from a running experiment."""
        try:
            self.tracker.observe(exp_id, text, data or {})
        except Exception:
            pass
        activity_log.emit("researcher", f"[{exp_id}] {text[:90]}", "info")

    def _experiment_markdown(self, exp: Dict[str, Any]) -> str:
        """Render a high-signal experiment entry for the PKB.

        Keeps core facts (hypothesis, metrics, outcome) visible so
        /dac isn't filled with opaque ID-only stubs.
        """
        from arail.research import mini_experiments as mx
        results = exp.get("results") or {}
        observations = exp.get("observations") or []
        outcome = str(results.get("outcome") or
                      ("supported" if exp.get("hypothesis_supported") else "not supported"))

        lines = [
            f"# Experiment {exp['id']}",
            "",
            mx.provenance_line(results),   # measured / NOT RUN / unmeasured — always honest
            f"**Outcome:** {outcome}",
            f"**Domain:** {exp.get('domain', 'general')}",
            f"**Hypothesis:** {exp.get('hypothesis', '')}",
            f"**Methodology:** {exp.get('methodology', '')}",
            "",
        ]

        metrics = results.get("metrics") or {}
        if metrics:
            lines.extend(["## Measured metrics", ""])
            for k, v in metrics.items():
                if isinstance(v, dict):
                    continue  # skip nested per-variant detail in the summary
                lines.append(f"- **{k}**: {v}")
            lines.append("")
        elif results.get("provenance") == "cannot_run":
            lines.extend([f"_Could not run: {results.get('cannot_run_reason', '')}._", ""])

        interp = results.get("interpretation")
        if isinstance(interp, dict) and interp.get("text"):
            lines.extend(["## Interpretation (model-narrated)", "",
                          str(interp["text"]), ""])

        lines.extend(["## Conclusion", "", str(exp.get("conclusion", "See results."))])

        if observations:
            lines.extend(["", "## Observations (measured)", ""])
            for ob in observations[-6:]:
                lines.append(f"- {ob.get('date', '')}: {ob.get('observation', '')}")

        return "\n".join(lines) + "\n"

    def _generate_report(self, parsed_goal: Dict[str, Any],
                         experiments: List[Dict[str, Any]]) -> str:
        """Generate a markdown research report — LLM-enhanced with fallback."""
        from arail.research.mini_experiments import provenance_line as mx_provenance_line
        goal_text = parsed_goal.get("goal", "")
        domain = parsed_goal.get("domain", "general")
        intent = parsed_goal.get("intent", _get_lab_intent())
        sys_ctx = _get_system_context(intent)
        redirect_flags = redirect_profile(_active_redirect())
        redirect_block = _redirect_prompt_block(_active_redirect())
        n = len(experiments)

        # Provenance summary — how many experiments were actually measured.
        def _prov(exp):
            return str(((exp.get("results") or {}).get("provenance")) or "unmeasured")
        n_measured = sum(1 for e in experiments if _prov(e) == "measured")
        n_cannot = sum(1 for e in experiments if _prov(e) == "cannot_run")
        n_unmeasured = n - n_measured - n_cannot

        # A compact, truthful metrics digest handed to the LLM AND printed in the
        # heuristic fallback — the report is grounded in the measured numbers.
        def _digest(exp):
            r = exp.get("results") or {}
            m = {k: v for k, v in (r.get("metrics") or {}).items()
                 if not isinstance(v, dict)}
            tag = _prov(exp)
            head = f"- [{tag}] {exp.get('hypothesis', '')[:70]}"
            if tag == "measured" and m:
                return head + " → " + ", ".join(f"{k}={v}" for k, v in list(m.items())[:4])
            if tag == "cannot_run":
                return head + f" → could not run: {r.get('cannot_run_reason', '')[:60]}"
            return head + " → not measurable on-device"
        digest = "\n".join(_digest(e) for e in experiments)

        prompt = (
            f"{redirect_block}"
            f"{self._swarm_prompt_block(parsed_goal)}"
            f"Write a concise research report in Markdown from these MEASURED "
            f"results. Do not invent numbers — use only what's given.\n\n"
            f"Goal: {goal_text}\nDomain: {domain}\n"
            f"Experiments ({n}): {n_measured} measured, {n_cannot} could-not-run, "
            f"{n_unmeasured} unmeasured.\n{digest}\n\n"
            f"Include: Summary, Key Findings (cite the measured metrics), "
            f"Recommendations. Under 300 words.\n\n"
            f"Rules: do NOT write a header block — no Hardware:, Date:, "
            f"Model:, Seed: or Prompt count: lines; those are emitted from "
            f"the record. Do NOT leave placeholders like YYYY-MM-DD. Every "
            f"number you write must be one of the measured values above.\n\n"
            f"Report:"
        )
        llm_text = _deep_complete(self._active_deep_router(), self._router, prompt, max_tokens=600, system=sys_ctx)

        facts_block = measured_facts_block(experiments)
        footer = (f"\n\n---\n*Provenance: {n_measured} measured · "
                  f"{n_unmeasured} unmeasured · {n_cannot} could-not-run. "
                  "Narrative is model-written; the measured-facts section "
                  "and every metric are code-emitted from the experiment "
                  "records.*")

        if llm_text and len(llm_text) > 50:
            problems = verify_report_narrative(llm_text, experiments)
            if not problems:
                parts = [llm_text.rstrip()]
                if facts_block:
                    parts.append(facts_block)
                return "\n\n".join(parts) + footer
            # The narrative failed verification. Do not publish it — a
            # confident report with one invented number is worse than a
            # plain one, because the reader cannot tell which number.
            activity_log.emit(
                "researcher",
                "Report narrative failed verification — publishing the "
                f"measured results only ({problems[0]}).",
                "warn", {"verification_problems": problems},
            )
            self._last_report_problems = problems

        # Heuristic fallback — prints the actual measured numbers, no fabrication.
        report_lines = [
            "# Research Report",
            "",
            f"**Goal:** {goal_text}",
            f"**Domain:** {domain}",
            f"**Experiments:** {n} ({n_measured} measured · {n_unmeasured} unmeasured · "
            f"{n_cannot} could-not-run)",
            "",
            "## Results",
            "",
        ]
        for exp in experiments:
            r = exp.get("results") or {}
            report_lines.append(f"### `{exp['id']}` — {exp.get('hypothesis', '')[:70]}")
            report_lines.append(f"- {mx_provenance_line(r)}")
            report_lines.append(f"- **Conclusion:** {exp.get('conclusion', 'See results.')}")
            metrics = {k: v for k, v in (r.get("metrics") or {}).items()
                       if not isinstance(v, dict)}
            for k, v in metrics.items():
                report_lines.append(f"  - {k}: {v}")
            report_lines.append("")

        report_lines.extend([
            "## Recommendations",
            "",
        ])
        if n_cannot:
            report_lines.append("1. Some experiments could not run (e.g. no local "
                                "model, or no approved knowledge). Install a model "
                                "and approve documents on the Knowledge page, then re-run.")
        if n_measured:
            report_lines.append(f"{'2' if n_cannot else '1'}. Build on the measured "
                                "results above; re-run to confirm stability.")
        if redirect_flags["prefer_autoresearch"]:
            report_lines.append("Convert the strongest measured path into a repeatable "
                                "loop with explicit stop conditions.")
        problems = getattr(self, "_last_report_problems", None)
        if problems:
            report_lines.extend([
                "",
                "> **The model-written narrative for this run was withheld.**",
                "> It did not survive verification against the experiment "
                "records, so only code-emitted results are shown above.",
                ">",
                *[f"> - {p}" for p in problems],
            ])
            self._last_report_problems = None

        if facts_block:
            report_lines.extend(["", facts_block])

        report_lines.extend(["", "---", "*Generated by Arail Researcher Agent — "
                             "metrics are code-measured on this machine.*"])
        return "\n".join(report_lines)


# Singleton instance for the portal
researcher = ResearcherAgent()
