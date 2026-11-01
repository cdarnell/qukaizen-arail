"""The `models` block on GET /api/research/status — honest reason codes.

Env matrix for the deep slot: tier_locked (minimalist) → wheel_missing
(maximus, no aerollm_api) → disabled (AEROLLM_RESEARCH=false) →
deferred_now (background gate) → ok. Availability (tier/wheel) must
outrank the operator toggle so a minimalist lab renders a locked control,
not a dead one. The fast side must mirror resolve("fast", tab="research").

find_spec stub + tmp-registry conventions from
tests/test_autoresearch_e2e_fake_aerollm.py.
"""

from __future__ import annotations

import importlib.util as _importlib_util

import pytest
from fastapi.testclient import TestClient

from arail.portal.app import app
from arail.registry import core as reg_core


def _client():
    return TestClient(app)


@pytest.fixture()
def registry_env(tmp_path, monkeypatch):
    """Fresh registry seeded from a controlled env; reset again on exit so
    later tests re-seed from their own env."""
    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE",
                       str(tmp_path / "model_registry.json"))
    monkeypatch.setenv("MODEL_BACKEND", "ollama_native")
    monkeypatch.setenv("MODEL_NAME", "ai-engineer:latest")
    monkeypatch.delenv("MODEL_API_BASE", raising=False)
    monkeypatch.setenv("AEROLLM_MODEL", "gpt-oss-20b-MLX-4bit")
    monkeypatch.setenv("LAB_MODE", "airgapped")
    monkeypatch.delenv("ARAIL_AGENT_DEEP", raising=False)
    reg_core.reset_registry()
    yield monkeypatch
    reg_core.reset_registry()


def _stub_wheel(monkeypatch, present: bool):
    """Pin the wheel gate on both seams: the registry seeds via find_spec,
    deep_policy's gate does a real import (patched at the helper)."""
    orig = _importlib_util.find_spec

    def fake(name, *a, **k):
        if name == "aerollm_api":
            return object() if present else None
        return orig(name, *a, **k)

    monkeypatch.setattr(_importlib_util, "find_spec", fake)
    from arail.agents import deep_policy
    monkeypatch.setattr(deep_policy, "_aerollm_importable", lambda: present)


def _models():
    r = _client().get("/api/research/status")
    assert r.status_code == 200
    body = r.json()
    assert "models" in body
    return body["models"]


def test_minimalist_is_tier_locked(registry_env):
    registry_env.setenv("LAB_TIER", "minimalist")
    registry_env.setenv("AEROLLM_RESEARCH", "true")
    reg_core.reset_registry()
    m = _models()
    assert m["tier"] == "minimalist"
    assert m["deep"]["reason_code"] == "tier_locked"
    assert m["deep"]["available"] is False
    assert m["deep"]["eligible_now"] is False
    assert "upgrade maximus" in m["deep"]["reason"]


def test_maximus_without_wheel_is_wheel_missing(registry_env):
    registry_env.setenv("LAB_TIER", "maximus")
    registry_env.setenv("AEROLLM_RESEARCH", "true")
    _stub_wheel(registry_env, present=False)
    reg_core.reset_registry()
    m = _models()
    assert m["deep"]["reason_code"] == "wheel_missing"
    assert m["deep"]["available"] is False


def test_toggle_off_is_disabled(registry_env):
    registry_env.setenv("LAB_TIER", "maximus")
    registry_env.setenv("AEROLLM_RESEARCH", "false")
    _stub_wheel(registry_env, present=True)
    reg_core.reset_registry()
    m = _models()
    assert m["deep"]["reason_code"] == "disabled"
    assert m["deep"]["enabled_by_user"] is False
    assert m["deep"]["available"] is True
    assert m["deep"]["eligible_now"] is False


def test_background_gate_failure_is_deferred_now(registry_env):
    registry_env.setenv("LAB_TIER", "maximus")
    registry_env.setenv("AEROLLM_RESEARCH", "true")
    _stub_wheel(registry_env, present=True)
    from arail import scheduler as sched
    registry_env.setattr(sched, "jobs_halted", lambda: True)
    reg_core.reset_registry()
    m = _models()
    assert m["deep"]["reason_code"] == "deferred_now"
    assert m["deep"]["available"] is True
    assert m["deep"]["enabled_by_user"] is True
    assert m["deep"]["eligible_now"] is False
    assert "halted" in m["deep"]["reason"]


def test_fast_mirrors_registry_resolution(registry_env):
    registry_env.setenv("LAB_TIER", "minimalist")
    reg_core.reset_registry()
    m = _models()
    from arail.registry import resolve
    entry = resolve("fast", tab="research").entry
    assert entry is not None
    assert m["fast"]["entry_id"] == entry.id
    assert m["fast"]["display_name"] == (entry.display_name or entry.model_id)
    assert m["fast"]["backend"] == entry.backend
    # Deep identity always names the aeroLLM slot model, exactly as the
    # registry (or the AEROLLM_MODEL env fallback) spells it.
    from arail.registry import get_registry
    from arail.registry.store import TIER1_ID
    tier1 = get_registry().entries.get(TIER1_ID)
    expected = tier1.display_name if tier1 is not None else "gpt-oss-20b-MLX-4bit"
    assert m["deep"]["display_name"] == expected
