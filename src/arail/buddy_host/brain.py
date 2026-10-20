"""The edge's view of the brain: which model answers a phone question, and may it.

ARCHITECTURE.md §5.5, §5.6. Agent ``buddy`` may use exactly one brain: the registry's
``fast`` profile, and only while that entry is a **local** ``ollama_native`` model
reached over loopback. A cloud entry, a remote endpoint, any other backend, or no
entry at all is refused (``brain_not_local`` / ``brain_unavailable``). There is no
fallback: ``fast`` has none in the registry, and the edge adds none.

This module only decides and describes. The functions marked *seam* are the three
places the edge touches the outside world (registry, Ollama, the model catalog); tests
replace them.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
NON_LOCAL_PROVIDERS = frozenset({"aerollm", "anthropic", "xai", "gateway"})
MIN_OLLAMA = (0, 5)
_PROBE_OK_TTL_S = 60.0
_PROBE_FAIL_TTL_S = 10.0

_probe_cache: dict[str, tuple[float, bool]] = {}
_probe_lock = threading.Lock()


@dataclass(frozen=True)
class BrainView:
    available: bool
    local: bool
    model_id: Optional[str]
    backend: Optional[str]
    structured_output: bool
    attribution: Optional[str]
    reason: Optional[str]
    entry: Any = None  # the ModelEntry; never serialised

    def public(self) -> dict:
        return {"available": self.available, "local": self.local, "model_id": self.model_id,
                "backend": self.backend, "structured_output": self.structured_output,
                "attribution": self.attribution, "reason": self.reason}


# --------------------------------------------------------------------------- seams
def resolve_entry() -> Any:
    """The registry entry for profile ``fast`` on the agents tab, or None. (seam)

    Uses ``peek``: the handshake asks every 20 s and must not write a fallback event
    and an activity line each time the model is down."""
    from arail.registry import get_registry
    return get_registry().peek("fast", tab="agents")


def probe_structured_output(entry: Any) -> bool:
    """True if the Ollama behind ``entry`` takes a JSON-schema ``format`` (>= 0.5). Cached. (seam)"""
    root = ollama_root(entry)
    now = time.monotonic()
    with _probe_lock:
        hit = _probe_cache.get(root)
        if hit is not None and hit[0] > now:
            return hit[1]
    ok = False
    try:
        import requests
        resp = requests.get(root + "/api/version", timeout=1.5)
        resp.raise_for_status()
        parts = str(resp.json().get("version", "")).split("-")[0].split(".")
        ok = (int(parts[0]), int(parts[1])) >= MIN_OLLAMA
    except Exception:  # noqa: BLE001 - an unreachable or odd Ollama reads as "not supported"
        ok = False
    with _probe_lock:
        _probe_cache[root] = (now + (_PROBE_OK_TTL_S if ok else _PROBE_FAIL_TTL_S), ok)
    return ok


def catalog_family(model_id: str) -> Optional[str]:
    """The model catalog's ``family`` for ``model_id`` (tag stripped), or None. (seam)"""
    try:
        from arail.chat import load_catalog
        bare = (model_id or "").split(":", 1)[0]
        for e in load_catalog():
            if e.id == bare:
                return e.family
    except Exception:  # noqa: BLE001
        pass
    return None


# --------------------------------------------------------------------------- decisions
def ollama_root(entry: Any) -> str:
    """The Ollama root URL (the endpoint without a trailing ``/v1``); loopback default when unset."""
    import os
    base = (getattr(entry, "endpoint", None) or f"http://127.0.0.1:{os.getenv('OLLAMA_PORT', '11434')}/v1").rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


def entry_is_local(entry: Any) -> bool:
    """Local means: not a cloud entry, an ``ollama_native`` backend, not a remote runtime, loopback host."""
    from arail.registry.core import is_cloud_entry
    if is_cloud_entry(entry):
        return False
    if getattr(entry, "provider_type", None) in NON_LOCAL_PROVIDERS:
        return False
    if getattr(entry, "backend", None) != "ollama_native":
        return False
    host = urlparse(ollama_root(entry)).hostname or ""
    return host in LOOPBACK_HOSTS


def describe() -> BrainView:
    """What the handshake reports and brain.chat enforces. Never raises."""
    try:
        entry = resolve_entry()
    except Exception:  # noqa: BLE001 - a registry hiccup is "no brain", fail closed
        entry = None
    if entry is None:
        return BrainView(False, False, None, None, False, None, "brain_unavailable")
    try:
        local = entry_is_local(entry)
    except Exception:  # noqa: BLE001
        local = False
    if not local:
        return BrainView(False, False, None, None, False, None, "brain_not_local")
    try:
        structured = bool(probe_structured_output(entry))
    except Exception:  # noqa: BLE001
        structured = False
    try:
        family = catalog_family(entry.model_id)
    except Exception:  # noqa: BLE001
        family = None
    return BrainView(True, True, str(entry.model_id)[:128], "ollama_native", structured,
                     "Built with Llama" if family == "llama" else None, None, entry)


def _reset_for_tests() -> None:
    with _probe_lock:
        _probe_cache.clear()
