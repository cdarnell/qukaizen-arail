"""arail.nucleus.providers selection — QueueLLM-first, Ollama-eval-fallback
(T-PROV-1..3, A5)."""

from __future__ import annotations

import sys
import types

import pytest

from arail.nucleus import providers
from arail.nucleus.errors import CapabilityMissing
from arail.nucleus.runtime_names import PY_MODULE


class _FakeRuntime:
    def __init__(self, *a, **kw):
        self.calls = []

    def generate(self, *a, **kw):
        self.calls.append((a, kw))
        return "fake output"

    def close(self):
        pass


def _install_fake_module(monkeypatch):
    fake = types.ModuleType(PY_MODULE)
    fake.Runtime = _FakeRuntime
    fake.__version__ = "0.1.0-rc.2-fake"
    monkeypatch.setitem(sys.modules, PY_MODULE, fake)
    return fake


# ── T-PROV-1 / A5: deep runtime importable -> never constructs fallback ──

def test_select_local_provider_prefers_deep_runtime(monkeypatch):
    _install_fake_module(monkeypatch)

    calls = {"ollama_constructed": False}

    class _SpyOllama:
        def __init__(self, *a, **kw):
            calls["ollama_constructed"] = True

    monkeypatch.setattr(
        "arail.nucleus.providers.fallback_ollama.OllamaFallbackProvider", _SpyOllama
    )

    provider = providers.select_local_provider("teacher", model_path="/models/x")
    assert provider.runtime == "queuellm"
    assert calls["ollama_constructed"] is False


# ── T-PROV-2: ImportError -> Ollama fallback, runtime field reads "ollama" ──

def test_select_local_provider_falls_back_on_import_error(monkeypatch):
    monkeypatch.delitem(sys.modules, PY_MODULE, raising=False)

    def _raise_import_error(*a, **kw):
        raise ImportError(f"no module named {PY_MODULE!r}")

    monkeypatch.setattr(
        "arail.nucleus.providers.queuellm.QueueLLMProvider", _raise_import_error
    )
    provider = providers.select_local_provider("judge", ollama_model="ai-engineer")
    assert provider.runtime == "ollama"


# ── T-PROV-3: unstable-api ValueError -> CapabilityMissing with rebuild hint ──

def test_probe_logprobs_capability_raises_capability_missing(monkeypatch, tmp_path):
    fake = _install_fake_module(monkeypatch)

    class _RaisingRuntime(_FakeRuntime):
        def generate(self, *a, **kw):
            raise ValueError("logprobs is unstable per STABILITY.md; rebuild with --features unstable-api")

    fake.Runtime = _RaisingRuntime

    from arail.nucleus.providers.queuellm import QueueLLMProvider

    provider = QueueLLMProvider("/models/x", run_dir=tmp_path)
    with pytest.raises(CapabilityMissing, match="unstable-api"):
        provider.probe_logprobs_capability()


def test_probe_logprobs_capability_reraises_other_value_errors(monkeypatch, tmp_path):
    fake = _install_fake_module(monkeypatch)

    class _OtherErrorRuntime(_FakeRuntime):
        def generate(self, *a, **kw):
            raise ValueError("some other problem")

    fake.Runtime = _OtherErrorRuntime

    from arail.nucleus.providers.queuellm import QueueLLMProvider

    provider = QueueLLMProvider("/models/x", run_dir=tmp_path)
    with pytest.raises(ValueError, match="some other problem"):
        provider.probe_logprobs_capability()


def test_refuses_mlx_shim_backend(monkeypatch):
    _install_fake_module(monkeypatch)
    from arail.nucleus.errors import DomainConfigError
    from arail.nucleus.providers.queuellm import QueueLLMProvider

    with pytest.raises(DomainConfigError, match="shim"):
        QueueLLMProvider("/models/x", backend="mlx")
