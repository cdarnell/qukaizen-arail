"""Health probes + startup preflight + interval loop.

Contract (R5): this module NEVER constructs ``AeroLLMBackend`` — a probe
must not load multi-GB weights. aerollm health is derived from importability
(wheel installed), model-dir presence (``cold``), and whether deep_policy's
resident singleton is already warm (``healthy``).

Probes are plain ``requests`` to localhost (always allowed by the egress
guard) with a 2s timeout. The background loop runs on a daemon thread so it
works identically under uvicorn and in tests; startup is never blocked.
"""

from __future__ import annotations

import importlib.util
import os
import threading
import time
from typing import Optional

from arail.registry.core import HealthState, ModelEntry, ModelRegistry

_PROBE_TIMEOUT = 2.0

# Entries with a weight-load in flight (boot warm / aerollm preload). A
# marked entry probes as "warming" until cleared or stale (15 min cap so a
# wedged load can never freeze the display).
_WARMING: dict[str, float] = {}
_WARMING_STALE_SEC = 900.0


def mark_warming(entry_id: str) -> None:
    _WARMING[entry_id] = time.time()


def clear_warming(entry_id: str) -> None:
    _WARMING.pop(entry_id, None)


def _is_warming(entry_id: str) -> bool:
    started = _WARMING.get(entry_id)
    if started is None:
        return False
    if time.time() - started > _WARMING_STALE_SEC:
        _WARMING.pop(entry_id, None)
        return False
    return True


def _probe_http_models(endpoint: str, model_id: Optional[str] = None,
                       check_model: bool = False) -> HealthState:
    """GET {endpoint}/models (OpenAI-compat; Ollama serves it via the shim)."""
    import requests
    base = endpoint.rstrip("/")
    start = time.monotonic()
    try:
        r = requests.get(f"{base}/models", timeout=_PROBE_TIMEOUT)
        latency = (time.monotonic() - start) * 1000
        if r.status_code != 200:
            return HealthState(status="unhealthy", latency_ms=latency,
                               checked_at=time.time(), endpoint=endpoint,
                               detail=f"HTTP {r.status_code} from {base}/models")
        if check_model and model_id:
            try:
                data = r.json()
                ids = [m.get("id", "") for m in (data.get("data") or [])]
                short = model_id.split(":", 1)[0]
                if ids and not any(short in i for i in ids):
                    return HealthState(
                        status="not_installed", latency_ms=latency,
                        checked_at=time.time(), endpoint=endpoint,
                        detail=f"'{model_id}' not found at {base} "
                               f"({len(ids)} models present)")
            except Exception:  # noqa: BLE001  # shape surprise ≠ down
                pass
        # Residency (Ollama only): the server being up does not mean the
        # weights are loaded — /api/ps lists actually-resident models.
        # None = can't tell (non-Ollama server / shape surprise) → stay
        # plain healthy rather than claim cold.
        if check_model and model_id:
            resident = _probe_ollama_residency(base, model_id)
            if resident is False:
                return HealthState(
                    status="cold", latency_ms=latency,
                    checked_at=time.time(), endpoint=endpoint,
                    detail="server up, model not loaded (loads on first call)")
            if resident is True:
                return HealthState(status="healthy", latency_ms=latency,
                                   checked_at=time.time(), endpoint=endpoint,
                                   detail="resident")
        return HealthState(status="healthy", latency_ms=latency,
                           checked_at=time.time(), endpoint=endpoint)
    except Exception as exc:  # noqa: BLE001
        return HealthState(
            status="unhealthy",
            latency_ms=(time.monotonic() - start) * 1000,
            checked_at=time.time(), endpoint=endpoint,
            detail=f"{type(exc).__name__}: {str(exc)[:160]}")


def _probe_ollama_residency(base: str, model_id: str) -> Optional[bool]:
    """True/False iff Ollama's /api/ps says the model is/isn't loaded;
    None when the endpoint isn't Ollama or the shape surprises us."""
    import requests
    root = base[:-3] if base.endswith("/v1") else base
    try:
        r = requests.get(f"{root}/api/ps", timeout=_PROBE_TIMEOUT)
        if r.status_code != 200:
            return None
        models = (r.json() or {}).get("models")
        if models is None:
            return None
        short = model_id.split(":", 1)[0]
        return any(short in (m.get("name") or m.get("model") or "")
                   for m in models)
    except Exception:  # noqa: BLE001
        return None


def _probe_aerollm(entry: ModelEntry) -> HealthState:
    now = time.time()
    if importlib.util.find_spec("aerollm_api") is None:
        return HealthState(status="unhealthy", checked_at=now,
                           detail="aerollm_api wheel not installed "
                                  "(./arailctl deep rebuild)")
    models_dir = os.getenv("ARAIL_MODELS_DIR", "lab/models")
    model_path = (entry.model_id if os.path.isabs(entry.model_id)
                  else os.path.join(models_dir, entry.model_id))
    if not os.path.isdir(model_path):
        return HealthState(status="not_installed", checked_at=now,
                           detail=f"model dir missing: {model_path}")
    # Warm iff deep_policy's resident backend singleton exists — inspected
    # WITHOUT constructing anything (R5).
    try:
        from arail.router.backends import AeroLLMBackend
        shared = getattr(AeroLLMBackend, "_shared", None) or {}
        if any(getattr(inst, "_runtime", None) is not None
               for inst in shared.values()):
            return HealthState(status="healthy", checked_at=now,
                               detail="runtime resident (warm)")
    except Exception:  # noqa: BLE001
        pass
    return HealthState(status="cold", checked_at=now,
                       detail="ready to load on first deep call "
                              "(weights not yet resident)")


def probe_entry(entry: ModelEntry) -> HealthState:
    if not entry.enabled:
        return HealthState(status="unknown", checked_at=time.time(),
                           detail="disabled")
    if _is_warming(entry.id):
        # A load is in flight; report it honestly unless the underlying
        # probe already sees the weights resident.
        state = (_probe_aerollm(entry) if entry.provider_type == "aerollm"
                 else None)
        if state is not None and state.status == "healthy":
            clear_warming(entry.id)
            return state
        return HealthState(status="warming", checked_at=time.time(),
                           endpoint=entry.endpoint,
                           detail="loading weights…")
    if entry.provider_type == "aerollm":
        return _probe_aerollm(entry)

    # Cloud gating first — never send probe traffic while airgapped.
    from arail.registry.core import _is_cloud_entry
    if _is_cloud_entry(entry):
        from arail.airgap import is_airgapped
        if is_airgapped():
            return HealthState(status="blocked_airgap",
                               checked_at=time.time(), endpoint=entry.endpoint,
                               detail="lab is airgapped (LAB_MODE!=hybrid)")
        if entry.key_env and not os.getenv(entry.key_env, "").strip():
            return HealthState(status="no_key", checked_at=time.time(),
                               endpoint=entry.endpoint,
                               detail=f"{entry.key_env} not set")
        # Hybrid + key: probing cloud /models on an interval is cheap and
        # keyless-401s are caught as unhealthy with the status code visible.
        if entry.provider_type == "anthropic":
            # Anthropic's /models needs the x-api-key header; skip network,
            # key presence is the meaningful preflight for this provider.
            return HealthState(status="healthy", checked_at=time.time(),
                               endpoint=entry.endpoint,
                               detail="key present (probe on first call)")

    if not entry.endpoint:
        return HealthState(status="unknown", checked_at=time.time(),
                           detail="no endpoint configured")
    check_model = entry.provider_type == "local"
    return _probe_http_models(entry.endpoint, entry.model_id,
                              check_model=check_model)


# ── preflight + interval loop ──────────────────────────────────────

def _tier_line(entry: ModelEntry) -> str:
    h = entry.health
    where = entry.endpoint or ("QueueLLM (in-process)"
                               if entry.provider_type == "aerollm" else "local")
    lat = f", {h.latency_ms:.0f}ms" if h.latency_ms is not None else ""
    detail = f" — {h.detail}" if h.detail and h.status not in ("healthy",) else ""
    return f"{entry.display_name} @ {where} ({h.status}{lat}){detail}"


def run_preflight(reg: ModelRegistry, *, announce: bool = True) -> None:
    """Probe every entry once; loudly report the two baseline tiers."""
    for entry in list(reg.entries.values()):
        old = entry.health.status
        entry.health = probe_entry(entry)
        new = entry.health.status
        if announce and old not in ("unknown", new):
            level = "info" if entry.health.usable else "warn"
            reg._emit(level,
                      f"Model '{entry.display_name}' health: {old} → {new}"
                      + (f" ({entry.health.detail})" if entry.health.detail else ""),
                      {"kind": "health", "entry_id": entry.id,
                       "status": new, "endpoint": entry.endpoint})

    if not announce:
        return
    tier0 = next((e for e in reg.entries.values() if e.tier == 0), None)
    tier1 = next((e for e in reg.entries.values() if e.tier == 1), None)
    problems = []
    lines = []
    for tier_entry in (tier0, tier1):
        if tier_entry is None:
            continue
        lines.append(_tier_line(tier_entry))
        if not tier_entry.health.usable:
            problems.append(tier_entry)
    try:
        from arail.activity import activity_log
        if problems:
            for p in problems:
                activity_log.emit(
                    "registry",
                    f"MODEL TIER DOWN — {_tier_line(p)}. The lab stays up, "
                    f"but '{', '.join(p.tags or ['this'])}' work will degrade "
                    "or fail visibly until it recovers.",
                    "warn",
                    {"model_event": {"kind": "tier_down", "entry_id": p.id,
                                     "endpoint": p.endpoint,
                                     "detail": p.health.detail}})
        else:
            activity_log.emit("registry",
                              "Model tiers ready: " + " · ".join(lines),
                              "success",
                              {"model_event": {"kind": "preflight_ok"}})
    except Exception:  # noqa: BLE001
        pass


def start_background(reg: ModelRegistry) -> None:
    """Startup preflight + interval re-probe on a daemon thread.

    ``MODEL_HEALTH_INTERVAL_SEC=0`` runs a single startup preflight and skips
    the re-probe loop entirely (one-shot, no recurring "MODEL TIER DOWN" noise).
    Any positive value is the loop cadence (floored at 5s).
    """
    interval = float(os.getenv("MODEL_HEALTH_INTERVAL_SEC", "60"))

    def _loop() -> None:
        run_preflight(reg, announce=True)
        if interval <= 0:
            return  # one-shot preflight; no interval re-probe
        while True:
            time.sleep(max(interval, 5.0))
            try:
                run_preflight(reg, announce=True)
            except Exception:  # noqa: BLE001
                pass

    t = threading.Thread(target=_loop, name="model-registry-health",
                         daemon=True)
    t.start()
