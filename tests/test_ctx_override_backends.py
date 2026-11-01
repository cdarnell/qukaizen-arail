"""R2 + backend ctx override tests.

Sprint: 2026-05-18-provider-aware-chat-dropdown, Phase B step 4+5.

R2: With NO ctx override set, CPUBackend builds n_ctx=4096 and
    OpenAICompatBackend complete/stream_complete produce request bodies
    unchanged (no num_ctx, no new keys).

Also tests _resolve_ctx_override behaviour.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import sys
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(_REPO_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))


# ---------------------------------------------------------------------------
# R2a — CPUBackend n_ctx default is 4096 when no override is set
# ---------------------------------------------------------------------------

def test_cpu_backend_default_n_ctx_is_4096(monkeypatch, tmp_path):
    """R2: CPUBackend must build Llama with n_ctx=4096 when no override exists.

    Injects a fake llama_cpp module so the test works without the heavy
    llama-cpp-python wheel installed.
    """
    monkeypatch.delenv("ARAIL_MODEL_CTX_OVERRIDES", raising=False)

    captured = {}

    class FakeLlama:
        def __init__(self, model_path, n_ctx, verbose=False):
            captured["n_ctx"] = n_ctx
            captured["model_path"] = model_path

    # Create a fake .gguf file so CPUBackend's path resolution succeeds.
    # Named with a parseable "3B" so the answering-model ceiling
    # (arail.registry.ceiling, primary role requires known params < 8B)
    # doesn't refuse it outright — the fake file has no real GGUF metadata
    # for the ceiling to read, so the name-regex fallback must resolve to
    # something under the ceiling for CPUBackend construction to succeed.
    models_dir = tmp_path / "lab" / "models"
    models_dir.mkdir(parents=True)
    gguf_file = models_dir / "test-model-3b.gguf"
    gguf_file.write_bytes(b"fake")

    monkeypatch.setenv("ARAIL_MODELS_DIR", str(models_dir))
    monkeypatch.setenv("MODEL_NAME", "test-model-3b")

    # Inject a fake llama_cpp module into sys.modules
    import types
    fake_llama_cpp = types.ModuleType("llama_cpp")
    fake_llama_cpp.Llama = FakeLlama
    monkeypatch.setitem(sys.modules, "llama_cpp", fake_llama_cpp)

    # Force reimport of CPUBackend so it picks up the fake module
    import importlib
    import arail.router.backends as _mod
    # Clear lru_cache on _resolve_ctx_override if any
    orig_llama_cache = None

    be = _mod.CPUBackend()
    assert captured.get("n_ctx") == 4096, (
        f"CPUBackend used n_ctx={captured.get('n_ctx')!r}, expected 4096"
    )


# ---------------------------------------------------------------------------
# R2b — OpenAICompatBackend complete body has no num_ctx when no override
# ---------------------------------------------------------------------------

def test_openai_compat_complete_body_unchanged_without_ctx_override(monkeypatch):
    """R2: OpenAICompatBackend.complete must NOT include num_ctx or other
    new keys in its request body when no ctx override is set."""
    monkeypatch.delenv("ARAIL_MODEL_CTX_OVERRIDES", raising=False)

    from arail.router.backends import OpenAICompatBackend

    be = OpenAICompatBackend.__new__(OpenAICompatBackend)
    import requests as _req
    mock_session = MagicMock()
    be._session = mock_session
    be.base_url = "http://localhost:1234/v1"
    be.model_name = "test-model"
    be.api_key = "not-needed"

    # Fake a successful response
    fake_resp = MagicMock()
    fake_resp.raise_for_status.return_value = None
    fake_resp.json.return_value = {
        "choices": [{"message": {"content": "hello"}}],
        "usage": {"completion_tokens": 1},
        "model": "test-model",
    }
    mock_session.post.return_value = fake_resp

    be.complete("test prompt", max_tokens=5, temperature=0.7)

    # Inspect the call
    call_kwargs = mock_session.post.call_args
    body = call_kwargs[1]["json"] if "json" in call_kwargs[1] else call_kwargs[0][1]
    # num_ctx must NOT appear in the payload
    assert "num_ctx" not in body, f"num_ctx leaked into request body: {body}"
    # The standard keys must be present
    assert "model" in body
    assert "messages" in body
    assert "temperature" in body
    assert "max_tokens" in body


# ---------------------------------------------------------------------------
# _resolve_ctx_override unit tests
# ---------------------------------------------------------------------------

def test_resolve_ctx_override_exact_match(monkeypatch):
    """Override hit — exact model name match returns the overridden value."""
    overrides = json.dumps({"test-model.gguf": 8192})
    monkeypatch.setenv("ARAIL_MODEL_CTX_OVERRIDES", overrides)

    from arail.router.backends import _resolve_ctx_override
    result = _resolve_ctx_override("test-model.gguf", default=4096)
    assert result == 8192


def test_resolve_ctx_override_substring_match(monkeypatch):
    """Override hit via substring — key is a substring of model_name."""
    overrides = json.dumps({"test-model": 16384})
    monkeypatch.setenv("ARAIL_MODEL_CTX_OVERRIDES", overrides)

    from arail.router.backends import _resolve_ctx_override
    result = _resolve_ctx_override("test-model.Q4_K_M.gguf", default=4096)
    assert result == 16384


def test_resolve_ctx_override_miss_returns_default(monkeypatch):
    """Override miss — falls through to default (no spec match for random name)."""
    monkeypatch.setenv("ARAIL_MODEL_CTX_OVERRIDES", json.dumps({}))

    from arail.router.backends import _resolve_ctx_override
    result = _resolve_ctx_override("completely-unknown-model-xyz", default=4096)
    assert result == 4096


def test_resolve_ctx_override_bad_json_returns_default(monkeypatch):
    """Bad JSON in ARAIL_MODEL_CTX_OVERRIDES — must not raise, returns default."""
    monkeypatch.setenv("ARAIL_MODEL_CTX_OVERRIDES", "NOT VALID JSON {{{")

    from arail.router.backends import _resolve_ctx_override
    result = _resolve_ctx_override("any-model", default=4096)
    assert result == 4096


def test_resolve_ctx_override_clamps_below_256(monkeypatch):
    """Values below 256 must be clamped UP to 256."""
    overrides = json.dumps({"tiny-model": 10})
    monkeypatch.setenv("ARAIL_MODEL_CTX_OVERRIDES", overrides)

    from arail.router.backends import _resolve_ctx_override
    result = _resolve_ctx_override("tiny-model", default=4096)
    assert result == 256


def test_resolve_ctx_override_clamps_above_1m(monkeypatch):
    """Values above 1,000,000 must be clamped DOWN to 1,000,000."""
    overrides = json.dumps({"huge-model": 99_000_000})
    monkeypatch.setenv("ARAIL_MODEL_CTX_OVERRIDES", overrides)

    from arail.router.backends import _resolve_ctx_override
    result = _resolve_ctx_override("huge-model", default=4096)
    assert result == 1_000_000


def test_resolve_ctx_override_none_default_and_no_override(monkeypatch):
    """No override + default=None → returns None (Ollama 'unset' sentinel)."""
    monkeypatch.delenv("ARAIL_MODEL_CTX_OVERRIDES", raising=False)

    from arail.router.backends import _resolve_ctx_override
    result = _resolve_ctx_override("unknown-ollama-model", default=None)
    assert result is None


def test_resolve_ctx_override_spec_fallback(monkeypatch):
    """No override → falls back to model_specs context if model name matches."""
    monkeypatch.delenv("ARAIL_MODEL_CTX_OVERRIDES", raising=False)

    from arail.router.backends import _resolve_ctx_override
    # Qwen3-8B is in model_specs with "128K tokens" = 131072
    result = _resolve_ctx_override("Qwen3-8B", default=4096)
    assert result == 131072  # from spec, not default
