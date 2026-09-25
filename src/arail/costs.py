"""CostTracker — simulate what local inference would cost via cloud APIs.

Every inference call is tracked: tokens in/out, backend used, latency.
The tracker computes:
  1. Cloud-equivalent cost (what you'd pay OpenRouter / Anthropic / Nvidia)
  2. Local energy cost          (watts × hours × $/kWh)
  3. Net savings                (cloud − energy)

Persists running totals to ``data/costs.json``.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional


# ---------------------------------------------------------------------------
# ReCAP recap_depth contextvar (paper §2.4 / sprint 2026-05-17-recap-core)
# ---------------------------------------------------------------------------
# Set by RouterAdapter via the context manager below; read by CostTracker.track().
# Using ContextVar (not threading.local) so async tasks inherit it correctly.
_recap_depth_tls: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "recap_depth", default=None
)


def current_recap_depth() -> Optional[int]:
    """Return the currently-active recap recursion depth, or None."""
    return _recap_depth_tls.get()


@contextlib.contextmanager
def recap_depth_context(depth: int) -> Iterator[None]:
    """Context manager: set recap_depth for the duration of a block.

    Safe for concurrent async tasks — each task gets its own contextvar
    token and the reset is guaranteed by the finally clause.
    """
    token = _recap_depth_tls.set(depth)
    try:
        yield
    finally:
        _recap_depth_tls.reset(token)


# ---------------------------------------------------------------------------
# Cloud-equivalent pricing  (per 1 M tokens, approximate 2025-2026 rates)
# ---------------------------------------------------------------------------
# Keyed by *model class* — the router maps backend+model to one of these.
CLOUD_PRICING: Dict[str, Dict[str, float]] = {
    #                          input $/1M   output $/1M
    "slm":      {"input": 0.03,  "output": 0.06},   # sub-3B  (Phi / Qwen-1.5B)
    "7b":       {"input": 0.07,  "output": 0.07},   # 7-8B    (Mistral 7B)
    "13b":      {"input": 0.15,  "output": 0.15},   # 13-14B  (Llama 13B)
    "70b":      {"input": 0.60,  "output": 0.90},   # 70B     (Llama 70B)
    "405b":     {"input": 2.00,  "output": 6.00},   # 405B+   (Llama 405B)
    "claude":   {"input": 15.00, "output": 75.00},  # Claude Opus
    "cloud_sm": {"input": 0.10,  "output": 0.10},   # generic cloud small
    "cloud_lg": {"input": 1.00,  "output": 3.00},   # generic cloud large
}

# ---------------------------------------------------------------------------
# Platform power-draw estimates  (watts, conservative)
# ---------------------------------------------------------------------------
PLATFORM_WATTS: Dict[str, float] = {
    "mlx":          20.0,    # Apple Silicon unified memory
    "cuda":        250.0,    # Nvidia discrete GPU
    "cpu":          65.0,    # Desktop / server CPU
    "airllm":       30.0,    # Mostly disk I/O + light CPU
    "aerollm":      30.0,    # Mostly disk I/O + light CPU
    "openai_compat": 10.0,   # Idle — server does the work
    "huggingface":   5.0,    # Network only
    "openrouter":    5.0,
    "claude":        5.0,
}

# Joules-per-token SWAG by backend — used as a floor for the energy estimate
# so realistic per-token energy is captured even when per-call latency is tiny
# or missing. Derived from typical sustained tok/s × wattage for each backend.
JOULES_PER_TOKEN: Dict[str, float] = {
    "mlx":           1.0,   # ~20 tok/s @ 20W
    "cuda":          5.0,   # ~50 tok/s @ 250W
    "cpu":           8.0,   # ~8 tok/s  @ 65W
    "airllm":       30.0,   # ~1 tok/s  @ 30W (layer streaming is slow)
    "aerollm":      30.0,
    "openai_compat": 0.5,
    "huggingface":   0.2,
    "openrouter":    0.2,
    "claude":        0.2,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _classify_model(backend: str, model_name: str) -> str:
    """Map a backend + model name to a pricing class key."""
    if backend == "claude":
        return "claude"
    if backend in ("huggingface", "openrouter"):
        # Cloud backends — estimate by name heuristics
        lower = model_name.lower()
        if "405b" in lower or "400b" in lower:
            return "405b"
        if "70b" in lower or "65b" in lower:
            return "70b"
        if "13b" in lower or "14b" in lower:
            return "13b"
        if "7b" in lower or "8b" in lower:
            return "7b"
        return "cloud_sm"
    if backend in ("airllm", "aerollm"):
        lower = model_name.lower()
        if "405b" in lower or "400b" in lower:
            return "405b"
        return "70b"   # default assumption for layer-streaming backends
    # Local backends — classify by model name
    lower = model_name.lower()
    if any(k in lower for k in ("phi", "qwen2.5-1", "qwen-1", "1.5b", "1b", "2b", "3b")):
        return "slm"
    if "70b" in lower or "65b" in lower:
        return "70b"
    if "13b" in lower or "14b" in lower:
        return "13b"
    if "7b" in lower or "8b" in lower:
        return "7b"
    return "slm"  # default to cheapest


@dataclass
class CostRecord:
    """A single inference cost record."""
    ts: float
    backend: str
    model: str
    model_class: str
    tokens_in: int
    tokens_out: int
    latency_ms: float
    cloud_equivalent_usd: float
    energy_usd: float
    savings_usd: float
    # Model-registry attribution (arail.registry) — None for legacy callers.
    provider: Optional[str] = None
    entry_id: Optional[str] = None
    tab: Optional[str] = None


# ---------------------------------------------------------------------------
# CostTracker  (singleton)
# ---------------------------------------------------------------------------
class CostTracker:
    _instance: Optional["CostTracker"] = None

    def __new__(cls) -> "CostTracker":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True

        from arail.config import DATA_DIR
        self._energy_rate = float(os.getenv("ENERGY_RATE_KWH", "0.15"))
        # Billing knobs: simulate SaaS pricing (base subscription + overage)
        self._subscription_monthly_usd = float(
            os.getenv("SIM_BILL_MONTHLY_BASE_USD", "20")
        )
        self._usage_multiplier = float(os.getenv("SIM_BILL_USAGE_MULTIPLIER", "2.0"))
        self._agent_usage_multiplier = float(
            os.getenv("SIM_BILL_AGENT_USAGE_MULTIPLIER", "1.25")
        )
        self._min_call_usd = float(os.getenv("SIM_BILL_MIN_CALL_USD", "0.002"))
        self._include_subscription = os.getenv(
            "SIM_BILL_INCLUDE_SUBSCRIPTION", "true"
        ).lower() not in ("0", "false", "no", "off")
        self._data_path = DATA_DIR / "costs.json"
        self._data_path.parent.mkdir(parents=True, exist_ok=True)

        # Running totals
        self.total_tokens_in: int = 0
        self.total_tokens_out: int = 0
        # Back-compat: total_cloud_usd now reflects *billed* usage overage.
        self.total_cloud_usd: float = 0.0
        self.total_raw_cloud_usd: float = 0.0
        self.total_billed_usage_usd: float = 0.0
        self.total_energy_usd: float = 0.0
        self.total_savings_usd: float = 0.0
        self.total_calls: int = 0
        self.total_latency_ms: float = 0.0
        self.latency_by_backend: Dict[str, float] = {}
        self.calls_by_backend: Dict[str, int] = {}
        self.calls_by_source: Dict[str, int] = {}
        self.tokens_by_backend: Dict[str, int] = {}
        self.cloud_by_backend: Dict[str, float] = {}
        # ReCAP telemetry: counts per recursion depth (depth -> call count).
        self.calls_by_recap_depth: Dict[int, int] = {}
        self._history: List[Dict[str, Any]] = []
        self._started_at: float = time.time()

        self._load()

    # ── Persistence ──────────────────────────────────────────────

    def _load(self) -> None:
        if self._data_path.exists():
            try:
                data = json.loads(self._data_path.read_text())
                self.total_tokens_in = data.get("total_tokens_in", 0)
                self.total_tokens_out = data.get("total_tokens_out", 0)
                legacy_cloud = data.get("total_cloud_usd", 0.0)
                self.total_raw_cloud_usd = data.get("total_raw_cloud_usd", legacy_cloud)
                self.total_billed_usage_usd = data.get("total_billed_usage_usd", legacy_cloud)
                self.total_cloud_usd = self.total_billed_usage_usd
                self.total_energy_usd = data.get("total_energy_usd", 0.0)
                self.total_savings_usd = data.get("total_savings_usd", 0.0)
                self.total_calls = data.get("total_calls", 0)
                self.total_latency_ms = data.get("total_latency_ms", 0.0)
                self.latency_by_backend = data.get("latency_by_backend", {})
                self.calls_by_backend = data.get("calls_by_backend", {})
                self.calls_by_source = data.get("calls_by_source", {})
                # Legacy-key migration (sprint 2026-09-20-buddy-front-and-
                # center, W2): calls_by_source is persisted, so a bare
                # "agent" bucket accumulated by every pre-attribution call
                # would never return to zero no matter what new code does.
                # One-time, idempotent rename on load — nothing writes the
                # bare key again after this sprint's chokepoint change.
                legacy_agent_calls = self.calls_by_source.pop("agent", None)
                if legacy_agent_calls:
                    self.calls_by_source["agent:pre-p1-legacy"] = (
                        self.calls_by_source.get("agent:pre-p1-legacy", 0)
                        + legacy_agent_calls
                    )
                self.tokens_by_backend = data.get("tokens_by_backend", {})
                self.cloud_by_backend = data.get("cloud_by_backend", {})
                self._started_at = data.get("started_at", time.time())
                # Durable extras (added 2026-07; legacy files simply lack
                # them): recent-call history + cache/recap counters used to
                # reset to empty on every restart.
                hist = data.get("history")
                if isinstance(hist, list):
                    self._history = hist[-500:]
                recap = data.get("calls_by_recap_depth")
                if isinstance(recap, dict):
                    self.calls_by_recap_depth = {
                        int(k): v for k, v in recap.items()
                        if str(k).lstrip("-").isdigit()}
                self.total_cache_read_tokens = data.get(
                    "total_cache_read_tokens", 0)
                self.total_cache_creation_tokens = data.get(
                    "total_cache_creation_tokens", 0)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                pass
            # Reconcile historical energy estimates that were latency-only —
            # if the on-disk number is implausibly small for the tokens
            # processed, rebuild it from the per-backend J/token SWAG.
            swag_energy = 0.0
            for bk, toks in self.tokens_by_backend.items():
                jpt = JOULES_PER_TOKEN.get(bk, 5.0)
                swag_energy += (toks * jpt / 3_600_000.0) * self._energy_rate
            if swag_energy > self.total_energy_usd * 5:
                self.total_energy_usd = round(swag_energy, 6)

    def _save(self) -> None:
        self._data_path.write_text(json.dumps({
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "total_cloud_usd": self.total_cloud_usd,
            "total_raw_cloud_usd": self.total_raw_cloud_usd,
            "total_billed_usage_usd": self.total_billed_usage_usd,
            "total_energy_usd": self.total_energy_usd,
            "total_savings_usd": self.total_savings_usd,
            "total_calls": self.total_calls,
            "total_latency_ms": self.total_latency_ms,
            "latency_by_backend": self.latency_by_backend,
            "calls_by_backend": self.calls_by_backend,
            "calls_by_source": self.calls_by_source,
            "tokens_by_backend": self.tokens_by_backend,
            "cloud_by_backend": self.cloud_by_backend,
            "started_at": self._started_at,
            "history": self._history[-500:],
            "calls_by_recap_depth": {str(k): v for k, v in
                                     self.calls_by_recap_depth.items()},
            "total_cache_read_tokens": getattr(self, "total_cache_read_tokens", 0),
            "total_cache_creation_tokens": getattr(self, "total_cache_creation_tokens", 0),
        }, indent=2))

    def _subscription_accrued_usd(self) -> float:
        """Accrue subscription over runtime, assuming a 30-day billing month."""
        if not self._include_subscription:
            return 0.0
        uptime_hours = max((time.time() - self._started_at) / 3600.0, 0.0)
        return self._subscription_monthly_usd * (uptime_hours / (24.0 * 30.0))

    # ── Core tracking ────────────────────────────────────────────

    def track(self, backend: str, model: str, tokens_in: int,
              tokens_out: int, latency_ms: float,
              source: str = "agent",
              recap_depth: Optional[int] = None,
              cache_read_input_tokens: int = 0,
              cache_creation_input_tokens: int = 0,
              provider: Optional[str] = None,
              entry_id: Optional[str] = None,
              tab: Optional[str] = None) -> CostRecord:
        """Record one inference call and return the cost breakdown.

        ``cache_read_input_tokens`` / ``cache_creation_input_tokens`` are
        Anthropic prompt-caching counters (0 for every other backend). They
        are accumulated and surfaced in ``get_summary`` so cache savings are
        visible in the dashboard's cost panel.
        """
        model_class = _classify_model(backend, model)
        pricing = CLOUD_PRICING.get(model_class, CLOUD_PRICING["slm"])

        # Cloud-equivalent cost
        cloud_input = (tokens_in / 1_000_000) * pricing["input"]
        cloud_output = (tokens_out / 1_000_000) * pricing["output"]
        raw_cloud_total = cloud_input + cloud_output

        # Simulated billable overage: multiplier + floor per call.
        effective_multiplier = self._usage_multiplier
        if source != "ui":
            effective_multiplier *= self._agent_usage_multiplier
        billed_usage_total = max(raw_cloud_total * effective_multiplier, self._min_call_usd)

        # Energy cost — take the larger of (latency × watts) and a
        # token-based SWAG (joules/token). Latency-only severely undercounts
        # when the tracker sees just generation time, not the full call,
        # so the J/token floor keeps the lifetime estimate realistic.
        watts = PLATFORM_WATTS.get(backend, 30.0)
        hours = latency_ms / 3_600_000.0
        latency_energy = watts * hours * self._energy_rate / 1000.0
        joules_per_tok = JOULES_PER_TOKEN.get(backend, 5.0)
        token_kwh = (tokens_in + tokens_out) * joules_per_tok / 3_600_000.0
        token_energy = token_kwh * self._energy_rate
        energy_cost = max(latency_energy, token_energy)

        # Savings
        savings = billed_usage_total - energy_cost

        # Accumulate
        self.total_tokens_in += tokens_in
        self.total_tokens_out += tokens_out
        self.total_raw_cloud_usd += raw_cloud_total
        self.total_billed_usage_usd += billed_usage_total
        self.total_cloud_usd = self.total_billed_usage_usd
        self.total_energy_usd += energy_cost
        self.total_savings_usd += savings
        self.total_calls += 1
        self.total_latency_ms += float(latency_ms or 0.0)
        self.latency_by_backend[backend] = (
            self.latency_by_backend.get(backend, 0.0) + float(latency_ms or 0.0)
        )
        self.calls_by_backend[backend] = self.calls_by_backend.get(backend, 0) + 1
        self.calls_by_source[source] = self.calls_by_source.get(source, 0) + 1
        self.tokens_by_backend[backend] = self.tokens_by_backend.get(backend, 0) + tokens_in + tokens_out
        self.cloud_by_backend[backend] = self.cloud_by_backend.get(backend, 0) + billed_usage_total
        if recap_depth is not None:
            self.calls_by_recap_depth[recap_depth] = (
                self.calls_by_recap_depth.get(recap_depth, 0) + 1
            )
        # Anthropic prompt-cache counters. Lazy-init via getattr so we don't
        # have to touch __init__/_load (older persisted state lacks them).
        self.total_cache_read_tokens = (
            getattr(self, "total_cache_read_tokens", 0) + cache_read_input_tokens
        )
        self.total_cache_creation_tokens = (
            getattr(self, "total_cache_creation_tokens", 0)
            + cache_creation_input_tokens
        )

        record = CostRecord(
            ts=time.time(),
            backend=backend,
            model=model,
            model_class=model_class,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            cloud_equivalent_usd=billed_usage_total,
            energy_usd=energy_cost,
            savings_usd=savings,
            provider=provider,
            entry_id=entry_id,
            tab=tab,
        )

        # Keep last 500 in memory
        self._history.append({
            "ts": record.ts,
            "backend": backend,
            "source": source,
            "model_class": model_class,
            "tokens": tokens_in + tokens_out,
            # Keep both keys for backward-compat with current UI helpers.
            "cloud_usd": round(billed_usage_total, 6),
            "cloud_cost_usd": round(billed_usage_total, 6),
            "raw_cloud_usd": round(raw_cloud_total, 6),
            "energy_usd": round(energy_cost, 6),
            "recap_depth": recap_depth,
            "cache_read_tokens": cache_read_input_tokens,
            "cache_creation_tokens": cache_creation_input_tokens,
            "model": model,
            "provider": provider,
            "entry_id": entry_id,
            "tab": tab,
            "latency_ms": round(float(latency_ms or 0.0), 1),
        })
        if len(self._history) > 500:
            self._history = self._history[-500:]

        self._save()
        return record

    # ── Queries ──────────────────────────────────────────────────

    def get_summary(self) -> Dict[str, Any]:
        """Return full cost summary for the dashboard."""
        uptime_hours = (time.time() - self._started_at) / 3600.0
        subscription_usd = self._subscription_accrued_usd()
        avg_latency_ms = (
            self.total_latency_ms / self.total_calls if self.total_calls else 0.0
        )
        avg_latency_by_backend = {
            k: round(self.latency_by_backend.get(k, 0.0) / v, 1)
            for k, v in self.calls_by_backend.items() if v
        }
        simulated_spend_usd = self.total_billed_usage_usd + subscription_usd
        return {
            "total_calls": self.total_calls,
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "total_tokens": self.total_tokens_in + self.total_tokens_out,
            # Keep legacy key used by dashboard spend meter; now includes
            # subscription + usage overage to feel like real SaaS billing.
            "cloud_equivalent_usd": round(simulated_spend_usd, 4),
            "raw_cloud_equivalent_usd": round(self.total_raw_cloud_usd, 4),
            "billed_usage_usd": round(self.total_billed_usage_usd, 4),
            "subscription_usd": round(subscription_usd, 4),
            "simulated_spend_usd": round(simulated_spend_usd, 4),
            "projected_monthly_bill_usd": round(
                self._subscription_monthly_usd + self.total_billed_usage_usd,
                4,
            ),
            "energy_usd": round(self.total_energy_usd, 4),
            "savings_usd": round(simulated_spend_usd - self.total_energy_usd, 4),
            "energy_rate_kwh": self._energy_rate,
            "calls_by_backend": self.calls_by_backend,
            "calls_by_source": self.calls_by_source,
            "tokens_by_backend": self.tokens_by_backend,
            "cloud_by_backend": {k: round(v, 4) for k, v in self.cloud_by_backend.items()},
            "usage_multiplier": self._usage_multiplier,
            "agent_usage_multiplier": self._agent_usage_multiplier,
            "min_call_usd": self._min_call_usd,
            "subscription_monthly_usd": self._subscription_monthly_usd,
            "subscription_enabled": self._include_subscription,
            "uptime_hours": round(uptime_hours, 2),
            "avg_latency_ms": round(avg_latency_ms, 1),
            "avg_latency_by_backend": avg_latency_by_backend,
            "total_latency_ms": round(self.total_latency_ms, 1),
            # Anthropic prompt-cache totals (claude backend, hybrid mode).
            "cache_read_tokens": getattr(self, "total_cache_read_tokens", 0),
            "cache_creation_tokens": getattr(self, "total_cache_creation_tokens", 0),
            "recent_history": self._history[-50:],
        }

    def get_last_record(self) -> Optional[Dict[str, Any]]:
        """Return the most recent cost record."""
        return self._history[-1] if self._history else None


# Module-level singleton
cost_tracker = CostTracker()
