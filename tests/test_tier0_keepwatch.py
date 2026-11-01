"""Tier-0 (resident) keep-watch: the small-model analogue of
tests/test_aerollm_preload.py — mirrors its structure closely.

Sprint: 2026-08-11-two-slot-chat-models Part 3.
"""

from __future__ import annotations

import asyncio

import pytest

from arail.portal import model_warmth
from arail.registry import health as reg_health
from arail.registry.store import TIER0_ID


@pytest.fixture
def tmp_registry(monkeypatch, tmp_path):
    """Local copy of tests/registry/conftest.py's fixture (scoped there)."""
    from arail.registry import core as reg_core
    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE",
                       str(tmp_path / "model_registry.json"))
    monkeypatch.setenv("MODEL_BACKEND", "ollama_native")
    monkeypatch.setenv("MODEL_NAME", "llama-ai-eng:latest")
    monkeypatch.delenv("MODEL_API_BASE", raising=False)
    reg_core.reset_registry()
    reg = reg_core.get_registry()
    reg._ensure_loaded()
    yield reg
    reg_core.reset_registry()


@pytest.fixture(autouse=True)
def _reset_suppression():
    """Every test starts with a clean (unsuppressed) keep-watch clock."""
    model_warmth._SUPPRESS_TIER0_KEEPWATCH_UNTIL = 0.0
    yield
    model_warmth._SUPPRESS_TIER0_KEEPWATCH_UNTIL = 0.0


def _run(coro):
    return asyncio.run(coro)


def _scoped_inflight(monkeypatch):
    import arail.portal.app as app_mod
    monkeypatch.setattr(app_mod, "_CHAT_MODEL_LOAD_INFLIGHT", asyncio.Lock())


# ---------------------------------------------------------------------------
# Registry shape guards
# ---------------------------------------------------------------------------

def test_no_tier0_entry_is_a_noop(monkeypatch):
    from arail.registry import core as reg_core
    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE", "/nonexistent-dir/model_registry.json")
    reg_core.reset_registry()
    reg = reg_core.get_registry()
    reg._ensure_loaded()
    reg.entries.pop(TIER0_ID, None)
    assert _run(model_warmth._tier0_keepwatch_tick()) == "no_tier0_entry"
    reg_core.reset_registry()


def test_non_ollama_backend_skips_honestly(monkeypatch, tmp_path):
    """No live residency probe exists for mlx/cpu/cuda — an honest skip,
    never a guessed re-warm."""
    from arail.registry import core as reg_core
    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE", str(tmp_path / "model_registry.json"))
    monkeypatch.setenv("MODEL_BACKEND", "mlx")
    monkeypatch.setenv("MODEL_NAME", "some-mlx-model")
    reg_core.reset_registry()
    reg = reg_core.get_registry()
    reg._ensure_loaded()
    assert _run(model_warmth._tier0_keepwatch_tick()) == "skip_non_ollama"
    reg_core.reset_registry()


# ---------------------------------------------------------------------------
# Core matrix
# ---------------------------------------------------------------------------

def test_already_resident_short_circuits(tmp_registry, monkeypatch):
    import arail.portal.app as app_mod
    monkeypatch.setattr(app_mod, "_ollama_ps_resident_ids", lambda: {"llama-ai-eng:latest"})
    monkeypatch.setattr(app_mod, "_get_runtime_backend",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert _run(model_warmth._tier0_keepwatch_tick()) == "already_resident"


def test_suppressed_after_eject_skips(tmp_registry, monkeypatch):
    import arail.portal.app as app_mod
    monkeypatch.setattr(app_mod, "_ollama_ps_resident_ids", lambda: set())
    model_warmth.suppress_tier0_keepwatch(seconds=60)
    assert _run(model_warmth._tier0_keepwatch_tick()) == "suppressed_after_eject"


def test_load_inflight_is_never_fought(tmp_registry, monkeypatch):
    import arail.portal.app as app_mod
    monkeypatch.setattr(app_mod, "_ollama_ps_resident_ids", lambda: set())
    _scoped_inflight(monkeypatch)

    async def _scenario():
        await app_mod._CHAT_MODEL_LOAD_INFLIGHT.acquire()
        try:
            return await model_warmth._tier0_keepwatch_tick()
        finally:
            app_mod._CHAT_MODEL_LOAD_INFLIGHT.release()

    assert asyncio.run(_scenario()) == "skip_load_inflight"


def test_low_free_memory_skips(tmp_registry, monkeypatch):
    import arail.portal.app as app_mod
    monkeypatch.setattr(app_mod, "_ollama_ps_resident_ids", lambda: set())
    _scoped_inflight(monkeypatch)
    monkeypatch.setattr(app_mod, "_local_memory_snapshot", lambda: {"free_gb": 0.4})
    assert _run(model_warmth._tier0_keepwatch_tick()) == "skip_low_memory"


def test_rewarm_calls_the_runtime_backend_and_clears_warming(tmp_registry, monkeypatch):
    import arail.portal.app as app_mod
    monkeypatch.setattr(app_mod, "_ollama_ps_resident_ids", lambda: set())
    _scoped_inflight(monkeypatch)
    monkeypatch.setattr(app_mod, "_local_memory_snapshot", lambda: {"free_gb": 8.0})

    class _FakeBackend:
        backend_name = "ollama:native"
        calls = []

        def complete(self, *a, **k):
            _FakeBackend.calls.append((a, k))
            return object()

    calls = []
    monkeypatch.setattr(
        app_mod, "_get_runtime_backend",
        lambda runtime, model: (calls.append((runtime, model)), _FakeBackend())[1])

    status = _run(model_warmth._tier0_keepwatch_tick())
    assert status == "rewarmed"
    assert calls == [("ollama", "llama-ai-eng:latest")]
    assert _FakeBackend.calls and _FakeBackend.calls[0][1].get("think") is False
    assert TIER0_ID not in reg_health._WARMING  # cleared after rewarm


def test_backend_failure_reports_failed_and_clears_warming(tmp_registry, monkeypatch):
    import arail.portal.app as app_mod
    monkeypatch.setattr(app_mod, "_ollama_ps_resident_ids", lambda: set())
    _scoped_inflight(monkeypatch)
    monkeypatch.setattr(app_mod, "_local_memory_snapshot", lambda: {"free_gb": 8.0})
    monkeypatch.setattr(
        app_mod, "_get_runtime_backend",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ollama down")))

    status = _run(model_warmth._tier0_keepwatch_tick())
    assert status.startswith("failed:")
    assert TIER0_ID not in reg_health._WARMING  # cleared even on failure


# ---------------------------------------------------------------------------
# Loop-level: kill switch, interval floor
# ---------------------------------------------------------------------------

def test_kill_switch(monkeypatch):
    monkeypatch.setenv("ARAIL_TIER0_KEEPWATCH", "0")
    assert model_warmth._tier0_keepwatch_enabled() is False
    _run(model_warmth.tier0_keepwatch_loop())  # returns immediately


def test_interval_floor(monkeypatch):
    monkeypatch.setenv("ARAIL_TIER0_KEEPWATCH_INTERVAL_SEC", "1")
    assert model_warmth._tier0_keepwatch_interval() == 30.0


def test_interval_default(monkeypatch):
    monkeypatch.delenv("ARAIL_TIER0_KEEPWATCH_INTERVAL_SEC", raising=False)
    assert model_warmth._tier0_keepwatch_interval() == 120.0


def test_interval_bad_value_falls_back(monkeypatch):
    monkeypatch.setenv("ARAIL_TIER0_KEEPWATCH_INTERVAL_SEC", "not-a-number")
    assert model_warmth._tier0_keepwatch_interval() == 120.0
