"""Backend construction for registry entries.

This module centralizes the construction knowledge that used to live in
three places (ModelRouter.__init__ env resolution, OpenAICompatBackend env
defaults, and the chat tab's ``_get_runtime_backend``). Backends themselves
are unchanged — we build them, we don't reimplement them.

Contract: this module NEVER constructs ``AeroLLMBackend`` directly. The
aerollm provider delegates to ``arail.agents.deep_policy`` which owns the
single resident runtime (constructing a second one would OOM the box).
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional

from arail.registry.core import ModelEntry, get_registry


class _ReportingBackend:
    """Proxy that reports call-time failures/successes to the registry.

    Keeps ``ModelRouter``'s cost tracking intact (successes flow through
    normally) while making a dead endpoint a *visible* health transition
    instead of a silently swallowed exception.
    """

    def __init__(self, inner: Any, entry_id: str) -> None:
        self._inner = inner
        self._entry_id = entry_id

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def complete(self, *args: Any, **kwargs: Any):
        start = time.monotonic()
        try:
            resp = self._inner.complete(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            get_registry().report_failure(self._entry_id, exc)
            raise
        get_registry().report_success(
            self._entry_id, latency_ms=(time.monotonic() - start) * 1000)
        return resp

    def stream_complete(self, *args: Any, **kwargs: Any):
        start = time.monotonic()
        try:
            for item in self._inner.stream_complete(*args, **kwargs):
                yield item
        except Exception as exc:  # noqa: BLE001
            get_registry().report_failure(self._entry_id, exc)
            raise
        get_registry().report_success(
            self._entry_id, latency_ms=(time.monotonic() - start) * 1000)

    def health_check(self) -> bool:
        try:
            return bool(self._inner.health_check())
        except Exception:  # noqa: BLE001
            return False


def _build_openai_compat(entry: ModelEntry):
    """OpenAICompat/OllamaNative via __new__ with explicit attributes
    (lifted from the chat tab's runtime-override path)."""
    import requests as _req
    from arail.router.backends import (OllamaNativeBackend,
                                       OpenAICompatBackend,
                                       _resolve_ctx_override)

    if entry.backend == "ollama_native":
        be = OllamaNativeBackend.__new__(OllamaNativeBackend)
        be._session = _req.Session()
        be.base_url = (entry.endpoint or
                       f"http://127.0.0.1:{os.getenv('OLLAMA_PORT', '11434')}/v1"
                       ).rstrip("/")
        be.model_name = entry.model_id
        be.api_key = "not-needed"
        be.backend_name = "ollama:native"
        be._num_ctx = _resolve_ctx_override(entry.model_id, default=None)
        return be

    be = OpenAICompatBackend.__new__(OpenAICompatBackend)
    be._session = _req.Session()
    if not entry.endpoint:
        raise ValueError(f"entry '{entry.id}' has no endpoint")
    be.base_url = entry.endpoint.rstrip("/")
    be.model_name = entry.model_id
    be.api_key = (os.getenv(entry.key_env, "") if entry.key_env
                  else "") or "not-needed"
    be.backend_name = f"{entry.provider_type}:openai_compat"
    return be


def build_router(entry: ModelEntry, *, billing_source: str = "agent",
                 tab: Optional[str] = None):
    """Build a ``ModelRouter`` for *entry* (raises on construction failure)."""
    from arail.router.core import ModelRouter

    if entry.provider_type == "aerollm":
        # deep_policy owns the resident aerollm runtime; the registry only
        # fronts it. get_deep_router() returns None when the wheel/model is
        # missing — surface that as a build failure so resolve() falls back.
        from arail.agents import deep_policy
        deep = deep_policy.get_deep_router()
        if deep is None:
            raise RuntimeError(
                f"QueueLLM runtime unavailable (model '{entry.model_id}' — "
                "wheel not installed or model dir missing)")
        # Rewrap the shared resident backend in a fresh router so per-call
        # attribution (tab/entry) never mutates the deep_policy singleton.
        backend = _ReportingBackend(deep._backend, entry.id)
        router = ModelRouter.from_backend(backend, "aerollm",
                                          billing_source=billing_source)
        router.provider = entry.provider_type
        router.entry_id = entry.id
        router.tab = tab
        return router

    if entry.provider_type == "anthropic":
        router = ModelRouter(backend="claude", billing_source=billing_source)
        backend = _ReportingBackend(router._backend, entry.id)
        router = ModelRouter.from_backend(backend, "claude",
                                          billing_source=billing_source)
    else:
        backend = _ReportingBackend(_build_openai_compat(entry), entry.id)
        name = ("ollama_native" if entry.backend == "ollama_native"
                else "openai_compat")
        router = ModelRouter.from_backend(backend, name,
                                          billing_source=billing_source)
    router.provider = entry.provider_type
    router.entry_id = entry.id
    router.tab = tab
    return router


def build_runtime_backend(runtime: str, model_id: str):
    """Chat-gallery runtime override — behavior identical to the historical
    ``_get_runtime_backend`` body in portal/app.py (which now delegates here).

    NOT wrapped in _ReportingBackend: chat surfaces its own errors inline and
    the returned object's attribute layout is relied on by chat code.
    """
    import requests as _req

    runtime_bases = {
        "ollama":      f"http://127.0.0.1:{os.getenv('OLLAMA_PORT', '11434')}/v1",
        "mlx-openai":  f"http://127.0.0.1:{os.getenv('MLX_OPENAI_PORT', '11435')}/v1",
        # Future: lmstudio, vllm, lmdeploy, etc. Same shape.
    }
    base = runtime_bases.get(runtime)
    if base is None:
        raise ValueError(f"unknown runtime: {runtime}")

    if runtime == "ollama":
        # B2 (ARCHITECTURE.md L3): use OllamaNativeBackend so options.num_ctx
        # reaches the native /api/chat endpoint rather than being silently
        # dropped by Ollama's OpenAI /v1 shim (F-OLLAMA-SHIM).
        from arail.router.backends import OllamaNativeBackend, _resolve_ctx_override
        be = OllamaNativeBackend.__new__(OllamaNativeBackend)
        be._session = _req.Session()
        be.base_url = base
        be.model_name = model_id
        be.api_key = "not-needed"   # local runtimes ignore auth
        be.backend_name = "ollama:native"
        be._num_ctx = _resolve_ctx_override(model_id, default=None)
    else:
        from arail.router.backends import OpenAICompatBackend
        be = OpenAICompatBackend.__new__(OpenAICompatBackend)
        be._session = _req.Session()
        be.base_url = base
        be.model_name = model_id
        be.api_key = "not-needed"   # local runtimes ignore auth
        be.backend_name = f"{runtime}:openai_compat"
    return be
