"""Goal Parser — converts natural language goals into structured objectives."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from arail import agent_context, agent_trace
from arail.router import ModelRouter


# Subprocess timeout for the LLM parse call. Default is generous
# (60s) because cold MLX loads on Apple Silicon can take 20-40s
# the first time. Tunable via ARAIL_GOAL_PARSE_TIMEOUT_SEC.
_SUBPROCESS_TIMEOUT_SEC = int(os.getenv("ARAIL_GOAL_PARSE_TIMEOUT_SEC", "60"))

# QA F2 (TEST_REPORT.md): error_class is documented in the trace schema as
# a class name -- ``type(exc).__name__`` shape only, never free text. The
# child (_subprocess_runner.py) is trusted to send a real class name in
# its own "error_class" field, but the parent must not take that on
# faith: an old or hostile child could still send only the legacy
# "error" message field (or something else entirely) in its JSON, and
# that message text (which has already been shown to carry an
# ``Authorization: Bearer ...`` fragment) must never reach a trace
# record just because it happened to be present. This allow-list is the
# actual enforcement point; everything else is defense in depth.
_ERROR_CLASS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ERROR_CLASS_MAX_LEN = 64


def _sanitize_error_class(payload: Any) -> str:
    """Return a value safe for the trace schema's error_class field, or
    a generic fallback -- never anything derived from a free-text
    ``error`` message, and never unbounded length."""
    if isinstance(payload, dict):
        candidate = payload.get("error_class")
        if (isinstance(candidate, str)
                and 0 < len(candidate) <= _ERROR_CLASS_MAX_LEN
                and _ERROR_CLASS_RE.fullmatch(candidate)):
            return candidate
    return "SubprocessError"


DOMAIN_KEYWORDS: Dict[str, list[str]] = {
    "farming": ["crop", "crop yield", "farm", "farming", "soil", "harvest",
                 "peanut", "corn", "wheat", "garden", "irrigation",
                 "acre", "livestock", "agronomy"],
    "ml-research": ["model", "training", "accuracy", "dataset", "neural",
                     "llm", "fine-tune", "gpu", "benchmark"],
    "culinary": ["cook", "recipe", "cuisine", "pastry", "dish",
                  "ingredient", "technique", "ferment"],
    "business": ["revenue", "growth", "customer", "market", "sales",
                  "profit", "scale", "startup", "debt", "portfolio",
                  "yield curve", "interest rate", "cash flow"],
    "health": ["fitness", "health", "diet", "exercise", "wellness",
                "strength", "nutrition"],
    "education": ["learn", "skill", "knowledge", "course", "master",
                   "understand", "study"],
    "trade": ["woodwork", "carpentry", "electrical", "plumbing", "weld",
               "hvac", "mason", "tile", "machining", "automotive",
               "mechanic", "apprentice", "journeyman", "framing",
               "drywall", "pipefitter"],
}

_KEYWORD_PATTERNS: Dict[str, "re.Pattern[str]"] = {}


def _keyword_pattern(keyword: str) -> "re.Pattern[str]":
    """Word-boundary matcher for a keyword or multi-word phrase.

    Substring matching is wrong here: it makes "corn" fire on
    "cornerstone" and "scale" fire on "escalate".
    """
    pattern = _KEYWORD_PATTERNS.get(keyword)
    if pattern is None:
        pattern = re.compile(rf"\b{re.escape(keyword)}\b", re.IGNORECASE)
        _KEYWORD_PATTERNS[keyword] = pattern
    return pattern


def matched_keywords(text: str, domain: str) -> list[str]:
    """Keywords of ``domain`` that occur in ``text`` on word boundaries."""
    return [kw for kw in DOMAIN_KEYWORDS.get(domain, [])
            if _keyword_pattern(kw).search(text)]


def infer_domain(text: str) -> str:
    """Best-effort domain label for a goal, or ``"general"``.

    A tie resolves to ``"general"``, never to a domain. The previous
    implementation used ``max(scores, key=scores.get)``, which returns
    the first maximal key in dict order — so every tie silently
    resolved to whichever domain happened to be declared first
    (``farming``), and finance or games goals came back labelled as
    agriculture. Keep this order-independent: the caller wants "I don't
    know" rather than a confident wrong answer, because the label
    selects agent system prompts and source allowlists downstream.
    """
    scores = {domain: len(matched_keywords(text, domain))
              for domain in DOMAIN_KEYWORDS}
    top = max(scores.values(), default=0)
    if top == 0:
        return "general"
    winners = [domain for domain, score in scores.items() if score == top]
    if len(winners) > 1:
        return "general"
    return winners[0]


def extract_entities(text: str) -> Dict[str, list[str]]:
    entities: Dict[str, list[str]] = {
        "locations": [], "subjects": [], "metrics": [], "timeframes": [],
    }
    locs = re.findall(r"in\s+([A-Za-z\s]+?)(?:[.,;]|$)", text)
    if locs:
        entities["locations"] = [l.strip() for l in locs]
    subjs = re.findall(
        r"(?:grow|build|learn|create|master)\s+(?:the\s+)?([A-Za-z\s]+?)(?:\s+in\s+|$)",
        text, re.IGNORECASE,
    )
    if subjs:
        entities["subjects"] = [s.strip() for s in subjs]
    return entities


class GoalParser:
    """Parse a natural-language goal into a structured dict."""

    def __init__(self, router: ModelRouter | None = None) -> None:
        self.router = router  # lazy — only created if actually used

    def _get_router(self) -> ModelRouter:
        if self.router is None:
            self.router = ModelRouter()
        return self.router

    def parse(self, goal_text: str,
              context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Parse a goal via the LLM, isolated in a subprocess.

        The MLX backend's Metal allocator throws an uncatchable C++
        OOM under memory pressure, which historically nuked the whole
        lab process. We isolate the call in a subprocess so a crash
        there only kills the worker — the parent observes a non-zero
        exit code and falls back to the heuristic parser, the lab
        stays up.

        Set ``ARAIL_GOAL_PARSE_INPROC=1`` to bypass isolation
        (useful in tests where the subprocess overhead is wasteful
        and the LLM call is mocked anyway).
        """
        prompt = (
            "Parse this goal into a structured JSON object:\n\n"
            f"Goal: {goal_text}\n\n"
            "Return ONLY valid JSON with keys: goal, domain, "
            "primary_objective, sub_objectives (list), success_metrics (dict), "
            "timeline, constraints (list), resources_needed (list)."
        )

        text: Optional[str] = None
        if os.getenv("ARAIL_GOAL_PARSE_INPROC", "0") == "1":
            text = self._llm_inproc(prompt)
        else:
            text = self._llm_subprocess(prompt)

        if text:
            try:
                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0]
                elif "```" in text:
                    text = text.split("```")[1].split("```")[0]
                parsed = json.loads(text.strip())
            except (json.JSONDecodeError, IndexError):
                parsed = self._heuristic(goal_text)
        else:
            parsed = self._heuristic(goal_text)

        parsed.setdefault("domain", infer_domain(goal_text))
        parsed["extracted_entities"] = extract_entities(goal_text)
        if context:
            parsed["context"] = context
        parsed["parsed_at"] = datetime.now(timezone.utc).isoformat()
        parsed["confidence"] = self._confidence(parsed)
        return parsed

    # ── LLM execution paths ─────────────────────────────────────────────

    def _llm_inproc(self, prompt: str) -> Optional[str]:
        """Run the LLM call in-process (the legacy path).

        Catches Python-level exceptions (including MetalOutOfMemory
        from mlx_guard); a C++ Metal OOM still nukes the parent. Use
        :meth:`_llm_subprocess` in production.
        """
        try:
            # L3 (ARCHITECTURE.md): goal_parser is not an agent -- system_call,
            # same label as the subprocess path uses for the out-of-process
            # trace, so the in-proc test path and the production path agree
            # on what this work is called.
            with agent_context.system_call("goal-parser"):
                resp = self._get_router().complete(prompt, max_tokens=800,
                                                   temperature=0.5)
            return resp.text
        except Exception:
            return None

    def _llm_subprocess(self, prompt: str) -> Optional[str]:
        """Run the LLM call in an isolated subprocess.

        Returns the raw response text on success, or None on any
        recoverable failure (subprocess crash, timeout, OOM, JSON
        protocol error). Callers fall back to the heuristic parser.
        """
        # Attribution round-trip (ARCHITECTURE.md contract #9): merge the
        # current context into the wire payload so the child can bill its
        # own cost_tracker correctly. The child never writes a trace file
        # itself (see _subprocess_runner.py's docstring) -- this function
        # is the sole trace author for the whole round-trip, always
        # recording exactly once, success or failure, so DE2's known leak
        # (this subprocess) is closed rather than merely labelled.
        subprocess_ctx = agent_context.to_subprocess_payload()
        request = json.dumps({
            "prompt": prompt,
            "max_tokens": 800,
            "temperature": 0.5,
            **subprocess_ctx,
        })
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                [sys.executable, "-m",
                 "arail.skills.goal_parser._subprocess_runner"],
                input=request,
                capture_output=True,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired:
            self._record_subprocess_trace(
                subprocess_ctx, outcome="error", error_class="TimeoutExpired")
            return None
        except (OSError, ValueError) as exc:
            # Couldn't spawn the subprocess at all (bad path, ulimit,
            # weird sys.executable). Fall back silently.
            self._record_subprocess_trace(
                subprocess_ctx, outcome="error", error_class=type(exc).__name__)
            return None
        elapsed_ms = (time.monotonic() - t0) * 1000

        if proc.returncode != 0:
            # The runner caught its own errors and exited 0 even on
            # Python-level failure, so a non-zero return code means
            # the process itself died (Metal OOM, segfault, etc).
            # That's the hardened path the user reported — survive it.
            self._record_subprocess_trace(
                subprocess_ctx, outcome="error", error_class="SubprocessDied",
                latency_ms=elapsed_ms)
            return None

        try:
            payload = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            self._record_subprocess_trace(
                subprocess_ctx, outcome="error", error_class="JSONDecodeError",
                latency_ms=elapsed_ms)
            return None
        if not payload.get("ok"):
            self._record_subprocess_trace(
                subprocess_ctx, outcome="error",
                error_class=_sanitize_error_class(payload),
                latency_ms=elapsed_ms)
            return None

        self._record_subprocess_trace(
            subprocess_ctx, outcome="ok", latency_ms=elapsed_ms,
            model=payload.get("model"), backend=payload.get("backend"),
            tokens_out=payload.get("tokens_used"))
        return payload.get("text")

    @staticmethod
    def _record_subprocess_trace(subprocess_ctx: Dict[str, Any], **fields: Any) -> None:
        """The parent authors the one trace record for this out-of-process
        call (contract #9). ``out_of_process=True`` distinguishes it from
        anything recorded by the router chokepoint directly."""
        agent_trace.record(
            trace_id=subprocess_ctx.get("trace_id"),
            agent_id=subprocess_ctx.get("agent_id"),
            kind="agent" if subprocess_ctx.get("agent_id") else "system",
            label=subprocess_ctx.get("agent_id") or "goal-parser",
            attribution=(
                f"agent:{subprocess_ctx['agent_id']}"
                if subprocess_ctx.get("agent_id") else "sys:goal-parser"
            ),
            brain=subprocess_ctx.get("brain"),
            effort=subprocess_ctx.get("effort"),
            out_of_process=True,
            streamed=False,
            ttft_ms=None,
            ttft_status="non_streaming",
            **fields,
        )

    def parse_offline(self, goal_text: str) -> Dict[str, Any]:
        """Heuristic-only parsing — no LLM needed (works airgapped with no
        model loaded)."""
        parsed = self._heuristic(goal_text)
        parsed["extracted_entities"] = extract_entities(goal_text)
        parsed["parsed_at"] = datetime.now(timezone.utc).isoformat()
        parsed["confidence"] = self._confidence(parsed)
        return parsed

    # ------------------------------------------------------------------
    def _heuristic(self, goal_text: str) -> Dict[str, Any]:
        domain = infer_domain(goal_text)

        # Split on common conjunctions / punctuation to extract sub-goals
        parts = re.split(r"[;,]\s*|\band\b|\bor\b|\bthen\b", goal_text, flags=re.IGNORECASE)
        parts = [p.strip() for p in parts if len(p.strip()) > 8]
        if len(parts) > 1:
            primary = parts[0]
            sub_objectives = parts[1:]
        else:
            primary = goal_text
            sub_objectives = []

        # Infer resources from domain
        resource_hints: Dict[str, list[str]] = {
            "ml-research": ["local LLM", "GPU or AeroLLM", "research papers"],
            "farming": ["weather data", "soil data", "crop databases"],
            "culinary": ["recipe databases", "ingredient sources"],
            "business": ["market data", "analytics tools"],
            "health": ["health metrics", "fitness tracking"],
            "education": ["learning materials", "practice exercises"],
        }
        resources = resource_hints.get(domain, ["local knowledge base"])

        return {
            "goal": goal_text,
            "domain": domain,
            "primary_objective": primary,
            "sub_objectives": sub_objectives,
            "success_metrics": {"research_cycles": "≥ 1 complete", "findings": "actionable"},
            "timeline": "ongoing",
            "constraints": ["local-first", "airgapped by default"],
            "resources_needed": resources,
            "parsing_method": "heuristic",
        }

    @staticmethod
    def _confidence(parsed: Dict[str, Any]) -> float:
        c = 0.7
        if not parsed.get("success_metrics"):
            c -= 0.2
        if not parsed.get("timeline") or parsed["timeline"] == "unspecified":
            c -= 0.1
        if parsed.get("domain", "general") == "general":
            c -= 0.1
        return round(min(1.0, max(0.0, c)), 2)
