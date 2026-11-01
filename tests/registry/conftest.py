"""Shared fixtures for model-registry tests.

Every test gets a tmp registry file and a fresh singleton — the registry is
process-global, so the reset must run around each test.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def tmp_registry(monkeypatch, tmp_path):
    """Fresh registry singleton persisted to a per-test tmp file.

    Also pins the model env to the known lab default (ollama_native +
    ai-engineer + aerollm Qwen2.5-7B-Instruct-4bit) so seeding is
    deterministic.

    Tier-1 `enabled` (sprints/2026-08-11-two-slot-chat-models Part 2) is a
    capability fact — ``tier.is_maximus() and find_spec("aerollm_api")`` —
    not an env opt-in flag any more, so the fixture pins BOTH sides of that
    fact instead of AEROLLM_RESEARCH (which now gates only the autoresearch
    loop and no longer has any bearing on the registry): LAB_TIER=maximus,
    and a faked find_spec so this fixture stays deterministic regardless of
    whether the aerollm wheel happens to be built on the machine running
    the suite.
    """
    from arail.registry import core as reg_core

    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE",
                       str(tmp_path / "model_registry.json"))
    monkeypatch.setenv("MODEL_BACKEND", "ollama_native")
    monkeypatch.setenv("MODEL_NAME", "ai-engineer:latest")
    monkeypatch.delenv("MODEL_API_BASE", raising=False)
    monkeypatch.delenv("OLLAMA_PORT", raising=False)
    monkeypatch.setenv("AEROLLM_MODEL", "Qwen2.5-7B-Instruct-4bit")
    monkeypatch.setenv("AEROLLM_RESEARCH", "true")
    monkeypatch.setenv("LAB_MODE", "airgapped")
    monkeypatch.setenv("LAB_TIER", "maximus")

    import importlib.util as _importlib_util
    _orig_find_spec = _importlib_util.find_spec

    def _fake_find_spec(name, *a, **k):
        if name == "aerollm_api":
            return object()  # truthy sentinel; presence is all callers check
        return _orig_find_spec(name, *a, **k)

    monkeypatch.setattr(_importlib_util, "find_spec", _fake_find_spec)

    reg_core.reset_registry()
    reg = reg_core.get_registry()
    reg._ensure_loaded()   # tests inspect .entries/.bindings directly
    yield reg
    reg_core.reset_registry()
