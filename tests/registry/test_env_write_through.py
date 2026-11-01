""""Env wins only when env moved" — sprints/2026-08-11-two-slot-chat-models
Part 4 (registry write-through unification).

Before this change, `_seed_from_env` unconditionally overwrote the
tier0/tier1 entries whenever they disagreed with the current env value —
so a model picked through the (future) Chat tab picker would be silently
reverted to the env default on the very next portal restart, because
nothing distinguished "the operator edited .env" from "the entry no
longer matches a stationary env value because the UI changed it".

These tests exercise `_seed_from_env` directly (via a second
`_ensure_loaded()` after a simulated restart) rather than through the not
 -yet-built POST /api/chat/default `slot=` endpoint (Phase 2 registry
layer only) — that endpoint's own tests land with Phase 5's UI wiring
and are covered end-to-end in test_chat_slots_contract.py's write-through
convergence test once it exists.
"""

from __future__ import annotations

from dataclasses import replace


def _restart(monkeypatch, tmp_path):
    """Simulate a portal restart: drop the singleton, get a fresh one,
    force it to reload from the (already-persisted) tmp registry file."""
    from arail.registry import core as reg_core
    reg_core.reset_registry()
    reg = reg_core.get_registry()
    reg._ensure_loaded()
    return reg


def test_seed_state_persists_across_restart(tmp_registry):
    reg = tmp_registry
    assert reg.seed_state.get("tier0") == "ai-engineer:latest::ollama_native::http://127.0.0.1:11434/v1"
    assert reg.seed_state.get("tier1") == "Qwen2.5-7B-Instruct-4bit"

    from arail.registry import core as reg_core
    reg_core.reset_registry()
    reg2 = reg_core.get_registry()
    reg2._ensure_loaded()
    assert reg2.seed_state.get("tier0") == reg.seed_state.get("tier0")
    assert reg2.seed_state.get("tier1") == reg.seed_state.get("tier1")


def test_user_pick_survives_a_restart_when_env_is_unchanged(tmp_registry, monkeypatch, tmp_path):
    reg = tmp_registry
    picked = replace(
        reg.entries["tier0-local"],
        model_id="llama-ai-eng:latest",
        display_name="llama-ai-eng",
        params_b=1.0,
        source="user",
    )
    reg.add_entry(picked)  # persists — mirrors what Phase 2's endpoint will do

    # Restart with MODEL_NAME/MODEL_BACKEND untouched (env did not move).
    reg2 = _restart(monkeypatch, tmp_path)
    assert reg2.entries["tier0-local"].model_id == "llama-ai-eng:latest", (
        "a UI pick must survive a restart when the operator never edited .env"
    )
    assert reg2.entries["tier0-local"].source == "user"


def test_operator_env_edit_overrides_a_prior_user_pick(tmp_registry, monkeypatch, tmp_path):
    reg = tmp_registry
    picked = replace(
        reg.entries["tier0-local"],
        model_id="llama-ai-eng:latest",
        source="user",
    )
    reg.add_entry(picked)

    # The operator now edits .env to a genuinely different model — env
    # moving must win over the standing user pick.
    monkeypatch.setenv("MODEL_NAME", "qwen2.5:14b")
    reg2 = _restart(monkeypatch, tmp_path)
    assert reg2.entries["tier0-local"].model_id == "qwen2.5:14b", (
        "an actual .env edit must overwrite a stale user pick, exactly like "
        "the pre-Part-4 unconditional-overwrite behavior did"
    )
    assert reg2.entries["tier0-local"].source == "seed_env"


def test_user_pick_survives_deep_slot_too(tmp_registry, monkeypatch, tmp_path):
    reg = tmp_registry
    picked = replace(
        reg.entries["tier1-aerollm"],
        model_id="Qwen2.5-3B-Instruct-4bit",
        source="user",
    )
    reg.add_entry(picked)

    reg2 = _restart(monkeypatch, tmp_path)
    assert reg2.entries["tier1-aerollm"].model_id == "Qwen2.5-3B-Instruct-4bit"
    assert reg2.entries["tier1-aerollm"].source == "user"


def test_operator_env_edit_overrides_deep_slot_user_pick(tmp_registry, monkeypatch, tmp_path):
    reg = tmp_registry
    picked = replace(
        reg.entries["tier1-aerollm"],
        model_id="Qwen2.5-3B-Instruct-4bit",
        source="user",
    )
    reg.add_entry(picked)

    monkeypatch.setenv("AEROLLM_MODEL", "Qwen2.5-14B-Instruct-4bit")
    reg2 = _restart(monkeypatch, tmp_path)
    assert reg2.entries["tier1-aerollm"].model_id == "Qwen2.5-14B-Instruct-4bit"
    assert reg2.entries["tier1-aerollm"].source == "seed_env"


def test_tier1_enabled_recomputes_on_every_restart_independent_of_model_pick(
    tmp_registry, monkeypatch, tmp_path
):
    """`enabled` is a capability fact (tier + wheel), not a user choice — it
    must track the current tier even when the model_id was user-picked and
    env never moved (unlike model_id, which the write-through gate above
    protects)."""
    reg = tmp_registry
    picked = replace(reg.entries["tier1-aerollm"], model_id="user-picked-model", source="user")
    reg.add_entry(picked)
    assert reg.entries["tier1-aerollm"].enabled is True  # fixture pins maximus + fake wheel

    monkeypatch.setenv("LAB_TIER", "minimalist")
    reg2 = _restart(monkeypatch, tmp_path)
    assert reg2.entries["tier1-aerollm"].enabled is False, (
        "a tier flip must be reflected even though the model_id (a user "
        "pick, env unmoved) correctly stayed put"
    )
    assert reg2.entries["tier1-aerollm"].model_id == "user-picked-model"


# ---------------------------------------------------------------------------
# Tier-1 `enabled` decoupling matrix (no longer AEROLLM_RESEARCH-gated)
# ---------------------------------------------------------------------------

def test_tier1_disabled_on_minimalist_even_with_wheel(tmp_registry, monkeypatch, tmp_path):
    monkeypatch.setenv("LAB_TIER", "minimalist")
    reg2 = _restart(monkeypatch, tmp_path)
    assert reg2.entries["tier1-aerollm"].enabled is False


def test_tier1_disabled_on_maximus_without_the_wheel(tmp_registry, monkeypatch, tmp_path):
    import importlib.util as _importlib_util
    monkeypatch.setattr(_importlib_util, "find_spec", lambda name, *a, **k: None)
    reg2 = _restart(monkeypatch, tmp_path)
    assert reg2.entries["tier1-aerollm"].enabled is False


def test_tier1_enabled_on_maximus_with_the_wheel(tmp_registry, monkeypatch, tmp_path):
    # tmp_registry already pins LAB_TIER=maximus + a faked wheel.
    reg2 = _restart(monkeypatch, tmp_path)
    assert reg2.entries["tier1-aerollm"].enabled is True


def test_aerollm_research_env_no_longer_affects_tier1_enabled(tmp_registry, monkeypatch, tmp_path):
    """The var that used to be the sole gate must now be inert for the
    registry — AEROLLM_RESEARCH=false must NOT disable the row on a
    maximus box with the wheel present (the .env.example bug this Part
    fixes: the row was hidden even on maximus out of the box)."""
    monkeypatch.setenv("AEROLLM_RESEARCH", "false")
    reg2 = _restart(monkeypatch, tmp_path)
    assert reg2.entries["tier1-aerollm"].enabled is True
