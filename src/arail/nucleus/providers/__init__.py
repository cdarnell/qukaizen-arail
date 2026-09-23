"""Generation providers for arail.nucleus — see base.py for the Protocol."""

from __future__ import annotations


def select_local_provider(role: str, *, model_path: str = "", ollama_model: str = "",
                          **runtime_kwargs):
    """Try the deep local runtime first; ImportError falls back to Ollama
    (eval-only roles). The caller's card records `runtime: ollama` for
    that role when the fallback fires (ARCHITECTURE.md §4.7 "Selection").

    A local `build` with no deep runtime available has no fallback teacher
    at all — Phase A refuses in preflight (T-PROV-2's second half), which
    this function does not itself enforce (that's a preflight-time
    decision, not a provider-construction-time one).
    """
    from arail.nucleus.providers.queuellm import QueueLLMProvider

    try:
        return QueueLLMProvider(model_path, **runtime_kwargs)
    except ImportError:
        from arail.nucleus.providers.fallback_ollama import OllamaFallbackProvider

        return OllamaFallbackProvider(ollama_model or model_path)
