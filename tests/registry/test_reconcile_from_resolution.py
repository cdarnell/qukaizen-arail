"""store.reconcile_from_resolution — resolution drives tier0/tier1.

Regression for F2 (sprints/2026-10-20-boot-model-autoresolve): with slot B
resolved to "none" the registry still held an enabled Qwen tier-1 entry
(hardcoded fallback), so the preload loop would have loaded the model the
operator had disabled because it crashes the process.
"""
from __future__ import annotations

from arail.registry import core as reg_core
from arail.registry import store


def _res(a="llama-ai-eng:latest", b=None, a_src="auto", b_src="auto"):
    return {"a": {"model": a, "source": a_src},
            "b": {"model": b, "source": b_src if b else "pin"}}


def test_b_none_disables_tier1_with_no_qwen(tmp_registry):
    assert tmp_registry.entries["tier1-aerollm"].enabled is True  # fixture seeds Qwen
    assert store.reconcile_from_resolution(tmp_registry, _res(b=None)) is True
    t1 = tmp_registry.entries["tier1-aerollm"]
    assert t1.enabled is False and t1.model_id == ""
    assert "Qwen" not in t1.display_name


def test_b_none_survives_restart_without_qwen_fallback(tmp_registry, monkeypatch):
    store.reconcile_from_resolution(tmp_registry, _res(b=None))
    monkeypatch.delenv("AEROLLM_MODEL", raising=False)   # what apply() does for none
    reg_core.reset_registry()
    reg2 = reg_core.get_registry()
    reg2._ensure_loaded()
    t1 = reg2.entries["tier1-aerollm"]
    assert t1.model_id == "" and t1.enabled is False


def test_fresh_seed_with_unset_env_has_no_deep_model(tmp_registry, monkeypatch, tmp_path):
    monkeypatch.delenv("AEROLLM_MODEL", raising=False)
    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE", str(tmp_path / "fresh.json"))
    reg_core.reset_registry()
    reg = reg_core.get_registry()
    reg._ensure_loaded()
    t1 = reg.entries["tier1-aerollm"]
    assert t1.model_id == "" and t1.enabled is False


def test_tier0_follows_resolution_and_marks_pin_as_user(tmp_registry):
    store.reconcile_from_resolution(tmp_registry, _res(a="qwen3:4b", a_src="pin", b="Dense-7B"))
    t0 = tmp_registry.entries["tier0-local"]
    assert t0.model_id == "qwen3:4b" and t0.source == "user"
    t1 = tmp_registry.entries["tier1-aerollm"]
    assert t1.model_id == "Dense-7B" and t1.enabled is True


def test_reconcile_is_idempotent_and_keeps_seed_state_in_step(tmp_registry, monkeypatch):
    r = _res(a="qwen3:4b", b="Dense-7B")
    assert store.reconcile_from_resolution(tmp_registry, r) is True
    assert store.reconcile_from_resolution(tmp_registry, r) is False
    # env "moves" to match the resolution (apply() stamps it): no re-seed fight
    monkeypatch.setenv("MODEL_NAME", "qwen3:4b")
    monkeypatch.setenv("AEROLLM_MODEL", "Dense-7B")
    reg_core.reset_registry()
    reg2 = reg_core.get_registry()
    reg2._ensure_loaded()
    assert reg2.entries["tier0-local"].model_id == "qwen3:4b"
    assert reg2.entries["tier1-aerollm"].model_id == "Dense-7B"


def test_tier0_untouched_when_not_ollama_backed(tmp_registry):
    from dataclasses import replace
    t0 = replace(tmp_registry.entries["tier0-local"], backend="mlx", model_id="mlx-thing")
    tmp_registry.add_entry(t0)
    store.reconcile_from_resolution(tmp_registry, _res(a="qwen3:4b"))
    assert tmp_registry.entries["tier0-local"].model_id == "mlx-thing"


def test_a_none_leaves_tier0_alone(tmp_registry):
    before = tmp_registry.entries["tier0-local"].model_id
    store.reconcile_from_resolution(tmp_registry, _res(a=None))
    assert tmp_registry.entries["tier0-local"].model_id == before
