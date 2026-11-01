"""AeroLLMBackend ring_depth wiring — sprints/2026-08-11-two-slot-chat-
models Part 4 (completes the TODO that used to leave AEROLLM_RING_DEPTH
as the only way to set it).

Uses the same fake-aerollm_api-module pattern as
test_aerollm_backend_budget_emit.py so construction completes without
loading real weights, and inspects the kwargs the fake Runtime received.
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import patch

import pytest


def _make_fake_aerollm_api() -> types.ModuleType:
    fake = types.ModuleType("aerollm_api")
    fake.__version__ = "0.0.0-test"

    class FakeRuntime:
        _last_kwargs: "dict[str, Any] | None" = None

        def __init__(self, model_path: str, **kwargs: Any) -> None:
            FakeRuntime._last_kwargs = kwargs

        def start(self) -> None:
            pass

    fake.Runtime = FakeRuntime
    return fake


@pytest.fixture(autouse=True)
def _patch_env_and_model(monkeypatch, tmp_path):
    model_dir = tmp_path / "Qwen2.5-7B-Instruct-4bit"
    model_dir.mkdir()
    monkeypatch.setenv("ARAIL_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("AEROLLM_MODEL", "Qwen2.5-7B-Instruct-4bit")
    monkeypatch.delenv("AEROLLM_KV_BUDGET_PCT", raising=False)
    monkeypatch.delenv("AEROLLM_DRAFT_MODEL", raising=False)
    monkeypatch.delenv("AEROLLM_RING_DEPTH", raising=False)


@pytest.fixture(autouse=True)
def _clear_shared():
    from arail.router.backends import AeroLLMBackend
    AeroLLMBackend._shared.clear()
    yield
    AeroLLMBackend._shared.clear()


def _construct():
    """Build one AeroLLMBackend with KV-budget resolution stubbed out
    (irrelevant here) and return (instance, kwargs the fake Runtime saw)."""
    from arail.router.backends import AeroLLMBackend
    fake_aero = _make_fake_aerollm_api()
    with (
        patch.dict(sys.modules, {"aerollm_api": fake_aero}),
        patch("arail.router.backends._resolve_kv_budget",
              return_value={"budget_bytes": None, "reason": "n/a", "fields": {"source": "unavailable"}}),
        patch.object(AeroLLMBackend, "_emit_budget_activity"),
    ):
        backend = AeroLLMBackend()
    return backend, fake_aero.Runtime._last_kwargs


def test_env_explicit_positive_wins_over_profile(monkeypatch):
    monkeypatch.setenv("AEROLLM_RING_DEPTH", "3")
    backend, kwargs = _construct()
    assert kwargs.get("ring_depth") == 3
    assert backend._ring_depth == 3
    assert backend._ring_depth_source == "env"


def test_env_explicit_zero_means_no_eviction_and_wins_over_profile(monkeypatch):
    monkeypatch.setenv("AEROLLM_RING_DEPTH", "0")
    backend, kwargs = _construct()
    assert "ring_depth" not in kwargs
    assert backend._ring_depth is None
    assert backend._ring_depth_source == "env"


def test_env_invalid_value_means_no_eviction_and_still_wins_over_profile(monkeypatch):
    monkeypatch.setenv("AEROLLM_RING_DEPTH", "not-a-number")
    backend, kwargs = _construct()
    assert "ring_depth" not in kwargs
    assert backend._ring_depth is None
    assert backend._ring_depth_source == "env"


def test_no_env_falls_back_to_runtime_profile(monkeypatch):
    import arail.runtime_profile as rp_mod
    monkeypatch.setattr(rp_mod, "resolve", lambda: ("balanced", "default"))
    backend, kwargs = _construct()
    assert kwargs.get("ring_depth") == 2  # balanced profile's aerollm_ring_depth
    assert backend._ring_depth == 2
    assert backend._ring_depth_source == "profile:balanced"


def test_no_env_different_profile_wires_a_different_depth(monkeypatch):
    import arail.runtime_profile as rp_mod
    monkeypatch.setattr(rp_mod, "resolve", lambda: ("throughput", "window"))
    backend, kwargs = _construct()
    assert kwargs.get("ring_depth") == 4  # throughput profile's aerollm_ring_depth
    assert backend._ring_depth_source == "profile:throughput"


def test_no_env_interactive_profile_pins_ring_depth_one(monkeypatch):
    import arail.runtime_profile as rp_mod
    monkeypatch.setattr(rp_mod, "resolve", lambda: ("interactive", "presence"))
    backend, kwargs = _construct()
    assert kwargs.get("ring_depth") == 1
    assert backend._ring_depth_source == "profile:interactive"


def test_profile_resolution_failure_never_raises_and_omits_ring_depth(monkeypatch):
    import arail.runtime_profile as rp_mod

    def _boom():
        raise RuntimeError("profile state corrupt")

    monkeypatch.setattr(rp_mod, "resolve", _boom)
    backend, kwargs = _construct()  # must not raise
    assert "ring_depth" not in kwargs
    assert backend._ring_depth is None
    assert backend._ring_depth_source is None
