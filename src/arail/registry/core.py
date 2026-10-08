"""Registry core — entries, resolution, fallback events, the singleton."""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Deque, Dict, List, Optional

TASK_PROFILES = ("fast", "reasoning", "long_context", "tool_use", "build")

PROVIDER_TYPES = ("local", "aerollm", "gateway", "anthropic", "xai")

# Which profile falls back to what when its bound entry is unusable.
# ``fast`` deliberately has NO fallback: Tier 0 down is a structured failure
# the UI must show, not a silent hop to a heavier model.
FALLBACK_CHAIN: Dict[str, List[str]] = {
    "fast": [],
    "reasoning": ["fast"],
    "build": ["reasoning", "fast"],
    "long_context": ["reasoning", "fast"],
    "tool_use": ["fast"],
}


@dataclass
class ModelCapabilities:
    tools: bool = False
    json_mode: bool = False
    streaming: bool = True
    vision: bool = False


@dataclass
class HealthState:
    """Runtime-only health; never persisted as authority."""
    # unknown|healthy|cold|warming|unhealthy|blocked_airgap|not_installed|no_key
    status: str = "unknown"
    latency_ms: Optional[float] = None
    checked_at: float = 0.0
    endpoint: Optional[str] = None
    detail: str = ""

    @property
    def usable(self) -> bool:
        # "cold" (server up / runtime importable but weights not resident),
        # "warming" (load in flight) and "unknown" (not yet probed) are
        # optimistically usable — failure is caught at call time.
        return self.status in ("healthy", "cold", "warming", "unknown")


@dataclass
class ModelEntry:
    id: str
    display_name: str
    provider_type: str            # local | aerollm | gateway | anthropic | xai
    backend: str                  # BACKEND_MAP key
    endpoint: Optional[str]       # None for in-process aerollm
    model_id: str
    context_window: Optional[int] = None
    params_b: Optional[float] = None
    architecture: str = "dense"   # dense | moe
    moe: Optional[Dict[str, Any]] = None   # {"num_experts", "top_k", "active_params_b"}
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)
    cost_tier: str = "free_local"  # free_local | metered
    tier: Optional[int] = None     # 0 resident, 1 deep, None cloud/gateway
    tags: List[str] = field(default_factory=list)
    enabled: bool = True
    source: str = "builtin"        # seed_env | builtin | user | artifact
    artifact: Optional[Dict[str, Any]] = None
    key_env: Optional[str] = None
    note: str = ""
    health: HealthState = field(default_factory=HealthState)

    def to_public(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class FallbackEvent:
    ts: float
    profile: str
    tab: Optional[str]
    from_id: str
    to_id: Optional[str]           # None == hard failure, nothing usable
    reason: str                    # unhealthy | blocked_airgap | no_key | disabled | error
    detail: str
    endpoint: Optional[str]
    status: str
    latency_ms: Optional[float]


@dataclass
class Resolution:
    entry: Optional[ModelEntry]    # None == structured failure — render an error
    requested: Optional[ModelEntry]
    profile: str
    tab: Optional[str]
    fallback: Optional[FallbackEvent]
    config_version: int

    def router(self, *, billing_source: str = "agent"):
        """Build a ModelRouter for the resolved entry, or None.

        Build failures mark the entry unhealthy (report_failure) so the next
        resolve() falls back visibly; this call itself never raises.
        """
        if self.entry is None:
            return None
        from arail.registry import binding
        try:
            return binding.build_router(
                self.entry, billing_source=billing_source, tab=self.tab)
        except Exception as exc:  # noqa: BLE001
            get_registry().report_failure(self.entry.id, exc)
            return None


def _gate_reason(entry: ModelEntry) -> Optional[tuple[str, str]]:
    """Return (reason, detail) when *entry* is unusable, else None."""
    if not entry.enabled:
        return ("disabled", f"{entry.id} is disabled")
    if _is_cloud_entry(entry):
        from arail.airgap import is_airgapped
        if is_airgapped():
            return ("blocked_airgap",
                    f"{entry.display_name} is blocked — the lab is airgapped "
                    "(set LAB_MODE=hybrid to allow cloud providers)")
        if entry.key_env:
            import os
            if not os.getenv(entry.key_env, "").strip():
                return ("no_key",
                        f"{entry.display_name} needs {entry.key_env} (not set)")
    if entry.health.status in ("unhealthy", "not_installed"):
        return ("unhealthy",
                entry.health.detail or f"{entry.display_name} is "
                f"{entry.health.status}")
    return None


def _is_cloud_entry(entry: ModelEntry) -> bool:
    if entry.provider_type in ("anthropic", "xai"):
        return True
    if entry.provider_type == "gateway" and entry.endpoint:
        try:
            from urllib.parse import urlparse
            host = urlparse(entry.endpoint).hostname or ""
            from arail.airgap import is_local_ip
            if host in ("localhost", "localhost.localdomain", ""):
                return False
            try:
                return not is_local_ip(host)
            except Exception:  # noqa: BLE001  # hostname, not IP literal
                return not host.endswith(".local")
        except Exception:  # noqa: BLE001
            return True
    return False


class ModelRegistry:
    """Process-wide registry. Use ``get_registry()``; do not construct."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.entries: Dict[str, ModelEntry] = {}
        self.bindings: Dict[str, Optional[str]] = {p: None for p in TASK_PROFILES}
        self.tab_overrides: Dict[str, Dict[str, Optional[str]]] = {}
        # "Env wins only when env moved" (sprints/2026-08-11-two-slot-chat-
        # models Part 4): the env fingerprint _seed_from_env last saw for
        # each seeded tier, so a stationary env value never overwrites a
        # UI-driven pick on the next boot — only an operator's actual .env
        # edit does. See arail.registry.store._seed_from_env.
        self.seed_state: Dict[str, Optional[str]] = {}
        self.config_version: int = 0
        self.recent_events: Deque[FallbackEvent] = deque(maxlen=50)
        self._loaded = False
        self._health_started = False

    # ── loading ─────────────────────────────────────────────────────
    def _ensure_loaded(self) -> None:
        with self._lock:
            if self._loaded:
                return
            from arail.registry import store
            store.load_or_seed(self)
            self._rehydrate_events()
            self._loaded = True

    def _rehydrate_events(self) -> None:
        """Rebuild the fallback-event timeline from activity.jsonl's tail.

        Every event already rode the activity stream (via _emit), so no new
        state file is needed — the banner/timeline just re-reads the copies
        after a restart. Best-effort; failures leave an empty deque."""
        try:
            from arail.activity import ActivityLog
            for line in ActivityLog._tail_lines(500):
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                me = ((ev.get("data") or {}).get("model_event") or {})
                if me.get("kind") != "fallback" or "from_id" not in me:
                    continue
                self.recent_events.append(FallbackEvent(
                    ts=float(me.get("ts") or 0.0),
                    profile=str(me.get("profile") or ""),
                    tab=me.get("tab"),
                    from_id=str(me.get("from_id") or ""),
                    to_id=me.get("to_id"),
                    reason=str(me.get("reason") or ""),
                    detail=str(me.get("detail") or ""),
                    endpoint=me.get("endpoint"),
                    status=str(me.get("status") or ""),
                    latency_ms=me.get("latency_ms")))
        except Exception:  # noqa: BLE001
            pass

    # ── mutation ────────────────────────────────────────────────────
    def _bump(self) -> None:
        self.config_version += 1

    def add_entry(self, entry: ModelEntry, *, persist: bool = True) -> None:
        self._ensure_loaded()
        with self._lock:
            self.entries[entry.id] = entry
            self._bump()
            if persist:
                from arail.registry import store
                store.save(self)

    def bind(self, profile: str, entry_id: Optional[str],
             tab: Optional[str] = None) -> None:
        """Bind *profile* (or ``"*"`` wildcard with a tab) to an entry.

        ``entry_id=None`` clears the binding/override.
        """
        self._ensure_loaded()
        if profile != "*" and profile not in TASK_PROFILES:
            raise ValueError(f"unknown profile: {profile}")
        if entry_id is not None and entry_id not in self.entries:
            raise ValueError(f"unknown entry: {entry_id}")
        with self._lock:
            if tab:
                overrides = self.tab_overrides.setdefault(tab, {})
                if entry_id is None:
                    overrides.pop(profile, None)
                    if not overrides:
                        self.tab_overrides.pop(tab, None)
                else:
                    overrides[profile] = entry_id
            else:
                if profile == "*":
                    raise ValueError("wildcard binding requires a tab")
                self.bindings[profile] = entry_id
            self._bump()
            from arail.registry import store
            store.save(self)

    # ── health feedback ─────────────────────────────────────────────
    def report_failure(self, entry_id: str, exc: Exception) -> None:
        """Mark an entry unhealthy after a call-time failure (visible)."""
        self._ensure_loaded()
        with self._lock:
            entry = self.entries.get(entry_id)
            if entry is None:
                return
            was = entry.health.status
            entry.health = HealthState(
                status="unhealthy",
                checked_at=time.time(),
                endpoint=entry.endpoint,
                detail=f"{type(exc).__name__}: {str(exc)[:160]}",
            )
        if was != "unhealthy":
            self._emit("warn",
                       f"Model '{entry.display_name}' failed: "
                       f"{entry.health.detail}",
                       {"entry_id": entry_id, "endpoint": entry.endpoint,
                        "kind": "failure"})

    def report_success(self, entry_id: str,
                       latency_ms: Optional[float] = None) -> None:
        self._ensure_loaded()
        with self._lock:
            entry = self.entries.get(entry_id)
            if entry is None:
                return
            was = entry.health.status
            entry.health = HealthState(
                status="healthy", latency_ms=latency_ms,
                checked_at=time.time(), endpoint=entry.endpoint)
        if was == "unhealthy":
            self._emit("info",
                       f"Model '{entry.display_name}' recovered.",
                       {"entry_id": entry_id, "kind": "recovery"})

    def record_fallback(self, event: FallbackEvent) -> None:
        with self._lock:
            self.recent_events.append(event)
        target = (self.entries.get(event.to_id).display_name
                  if event.to_id and event.to_id in self.entries else None)
        source = (self.entries.get(event.from_id).display_name
                  if event.from_id in self.entries else event.from_id)
        if event.to_id is None:
            msg = (f"No usable model for '{event.profile}'"
                   f"{f' ({event.tab} tab)' if event.tab else ''}: "
                   f"{source} — {event.detail}")
        else:
            msg = (f"Model fallback ({event.profile}"
                   f"{f', {event.tab} tab' if event.tab else ''}): "
                   f"{source} → {target} — {event.detail}")
        self._emit("warn", msg, {"kind": "fallback", **asdict(event)})

    @staticmethod
    def _emit(level: str, message: str, model_event: Dict[str, Any]) -> None:
        try:
            from arail.activity import activity_log
            activity_log.emit("registry", message, level,
                              {"model_event": model_event})
        except Exception:  # noqa: BLE001  # never let telemetry break a call
            pass

    # ── resolution ──────────────────────────────────────────────────
    def _builtin_binding(self, profile: str) -> Optional[ModelEntry]:
        entries = [e for e in self.entries.values() if e.enabled]
        tier0 = next((e for e in entries if e.tier == 0), None)
        tier1 = next((e for e in entries if e.tier == 1), None)
        if profile == "fast":
            return tier0
        if profile in ("reasoning", "build"):
            return tier1 or tier0
        if profile == "long_context":
            big = [e for e in entries
                   if (e.context_window or 0) >= 32_000]
            if big:
                return max(big, key=lambda e: e.context_window or 0)
            return tier1 or tier0
        if profile == "tool_use":
            tools = [e for e in entries if e.capabilities.tools]
            if tools:
                tools.sort(key=lambda e: (e.tier is None, e.tier))
                return tools[0]
            return tier0 or tier1
        return None

    def _bound_entry(self, profile: str, tab: Optional[str]) -> Optional[ModelEntry]:
        if tab and tab in self.tab_overrides:
            ov = self.tab_overrides[tab]
            eid = ov.get(profile) or ov.get("*")
            if eid and eid in self.entries:
                return self.entries[eid]
        eid = self.bindings.get(profile)
        if eid and eid in self.entries:
            return self.entries[eid]
        return self._builtin_binding(profile)

    def resolve(self, profile: str, tab: Optional[str] = None, *,
                allow_fallback: bool = True) -> Resolution:
        self._ensure_loaded()
        if profile not in TASK_PROFILES:
            raise ValueError(
                f"unknown task profile '{profile}' "
                f"(choose from {', '.join(TASK_PROFILES)})")
        with self._lock:
            requested = self._bound_entry(profile, tab)
            cfgv = self.config_version
            if requested is None:
                event = FallbackEvent(
                    ts=time.time(), profile=profile, tab=tab,
                    from_id="(none)", to_id=None, reason="unbound",
                    detail=f"no model bound for profile '{profile}'",
                    endpoint=None, status="unknown", latency_ms=None)
                self.record_fallback(event)
                return Resolution(None, None, profile, tab, event, cfgv)

            gate = _gate_reason(requested)
            if gate is None:
                return Resolution(requested, requested, profile, tab, None, cfgv)

            reason, detail = gate
            if allow_fallback:
                for fb_profile in FALLBACK_CHAIN.get(profile, []):
                    candidate = self._bound_entry(fb_profile, tab)
                    if candidate is not None and candidate.id != requested.id \
                            and _gate_reason(candidate) is None:
                        event = FallbackEvent(
                            ts=time.time(), profile=profile, tab=tab,
                            from_id=requested.id, to_id=candidate.id,
                            reason=reason, detail=detail,
                            endpoint=requested.endpoint,
                            status=requested.health.status,
                            latency_ms=requested.health.latency_ms)
                        self.record_fallback(event)
                        return Resolution(candidate, requested, profile, tab,
                                          event, cfgv)
            event = FallbackEvent(
                ts=time.time(), profile=profile, tab=tab,
                from_id=requested.id, to_id=None, reason=reason, detail=detail,
                endpoint=requested.endpoint, status=requested.health.status,
                latency_ms=requested.health.latency_ms)
            self.record_fallback(event)
            return Resolution(None, requested, profile, tab, event, cfgv)

    # ── presentation ────────────────────────────────────────────────
    def statusbar_text(self) -> str:
        self._ensure_loaded()
        parts: List[str] = []
        tier0 = next((e for e in self.entries.values()
                      if e.tier == 0 and e.enabled), None)
        tier1 = next((e for e in self.entries.values()
                      if e.tier == 1 and e.enabled), None)
        if tier0:
            parts.append(f"{tier0.display_name} (resident)")
        if tier1:
            parts.append(f"{tier1.display_name} @ QueueLLM")
        return " · ".join(parts) if parts else "no models configured"

    def to_state(self) -> Dict[str, Any]:
        self._ensure_loaded()
        with self._lock:
            return {
                "config_version": self.config_version,
                "entries": [e.to_public() for e in self.entries.values()],
                "bindings": dict(self.bindings),
                "tab_overrides": {t: dict(o)
                                  for t, o in self.tab_overrides.items()},
                "recent_events": [asdict(ev) for ev in self.recent_events],
                "statusbar": self.statusbar_text(),
                "profiles": list(TASK_PROFILES),
            }

    # ── background health ───────────────────────────────────────────
    def start_background(self) -> None:
        """Kick off startup preflight + interval health loop (idempotent)."""
        with self._lock:
            if self._health_started:
                return
            self._health_started = True
        from arail.registry import health
        health.start_background(self)


_registry: Optional[ModelRegistry] = None
_registry_lock = threading.Lock()


def get_registry() -> ModelRegistry:
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = ModelRegistry()
    return _registry


def reset_registry() -> None:
    """Testing hook — drop the singleton so the next get_registry() reloads."""
    global _registry
    with _registry_lock:
        _registry = None


def resolve(profile: str, tab: Optional[str] = None, *,
            allow_fallback: bool = True) -> Resolution:
    return get_registry().resolve(profile, tab, allow_fallback=allow_fallback)
