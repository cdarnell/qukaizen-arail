"""The two-slot model — reachability tests for `/api/chat/models`'s
`slots` block (resident/deep).

Sprint: 2026-08-11-two-slot-chat-models

These are "contracts must meet in the middle" tests in the spirit of
learnings/2026-05-20-reachability-tests-for-new-classes.md: a green unit
test for the ceiling module and a green unit test for the payload builder
both passed in a prior surface (sprints/2026-05-18-provider-aware-chat-
dropdown) while the two sides silently disagreed about field names. The
tests below don't just assert `slots` has the right keys — they prove the
payload builder actually calls through the shared chokepoints
(`arail.registry.ceiling.resolve_answering_model`,
`arail.hardware.secondary_model_cap_b`) rather than re-deriving their
logic locally, by patching the chokepoint itself and checking its exact
output survives into the response untouched.

Write-through convergence (`POST /api/chat/default slot=deep` then
`GET /api/models/state` and `GET /api/chat/models` agreeing) is Phase 2
work (registry write-through) — those tests land with that phase.
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(_REPO_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))


def _isolate_registry(monkeypatch) -> None:
    """Fresh registry singleton persisted to a throwaway file — same
    convention as tests/registry/conftest.py's tmp_registry."""
    from arail.registry import core as reg_core
    tmp_dir = tempfile.mkdtemp(prefix="arail-slots-contract-registry-")
    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE", os.path.join(tmp_dir, "model_registry.json"))
    reg_core.reset_registry()


def _seed_env(monkeypatch, *, model_name: str, backend: str = "ollama_native",
              aerollm_model: str = "Qwen2.5-7B-Instruct-4bit") -> None:
    monkeypatch.setenv("MODEL_BACKEND", backend)
    monkeypatch.setenv("MODEL_NAME", model_name)
    monkeypatch.setenv("AEROLLM_MODEL", aerollm_model)
    monkeypatch.setenv("AEROLLM_RESEARCH", "true")
    monkeypatch.delenv("MODEL_API_BASE", raising=False)
    monkeypatch.delenv("OLLAMA_PORT", raising=False)
    monkeypatch.delenv("ARAIL_OLLAMA_KEEP_ALIVE", raising=False)


# ---------------------------------------------------------------------------
# Shape: both slots present with the documented keys
# ---------------------------------------------------------------------------

def test_slots_present_with_resident_and_deep(monkeypatch):
    import arail.portal.app as app_mod

    _isolate_registry(monkeypatch)
    _seed_env(monkeypatch, model_name="llama-ai-eng")

    slots = app_mod._chat_slots_payload(ollama_warm_ids=set())

    assert slots["resident"] is not None, "resident slot must build from the seeded tier0-local entry"
    assert slots["deep"] is not None, "deep slot must build from the seeded tier1-aerollm entry"

    resident_required = {
        "entry_id", "model_id", "display_name", "runtime", "params_b",
        "param_source", "warm", "health", "ceiling", "pinned", "keepwatch",
    }
    missing = resident_required - slots["resident"].keys()
    assert not missing, f"slots.resident missing keys: {sorted(missing)}"

    deep_required = {
        "entry_id", "model_id", "display_name", "installed", "model_ready",
        "resident", "params_b", "param_source", "cap_b", "eligible",
        "reason", "regime", "ring_depth", "ring_depth_source",
        "restart_note", "available_in_tier", "upgrade_command", "swap",
    }
    missing = deep_required - slots["deep"].keys()
    assert not missing, f"slots.deep missing keys: {sorted(missing)}"

    assert slots["resident"]["entry_id"] == "tier0-local"
    assert slots["deep"]["entry_id"] == "tier1-aerollm"


def test_slots_deep_never_claims_streaming_for_aerollm(monkeypatch):
    """F-OVERSELL: aeroLLM keeps its model resident once loaded. The deep
    slot's regime must say so — never a streaming claim aeroLLM can't
    back up (the honesty rule sprints/2026-07-20-model-ux-unification
    enforced on optional_backends.aerollm.streamed=False)."""
    import arail.portal.app as app_mod

    _isolate_registry(monkeypatch)
    _seed_env(monkeypatch, model_name="llama-ai-eng")

    slots = app_mod._chat_slots_payload(ollama_warm_ids=set())
    assert slots["deep"]["regime"] == "aerollm_resident"


# ---------------------------------------------------------------------------
# Reachability: the payload builder calls the REAL chokepoints, not a
# local re-implementation — proven by patching the chokepoint itself and
# checking its exact, distinctive output survives untouched.
# ---------------------------------------------------------------------------

def test_slots_resident_ceiling_propagates_the_real_chokepoints_message(monkeypatch):
    import arail.portal.app as app_mod
    from arail.registry import ceiling as ceiling_mod

    _isolate_registry(monkeypatch)
    _seed_env(monkeypatch, model_name="fake-model-for-ceiling-test")

    sentinel = "SENTINEL-C-CEILROW-8f3a1d — must survive into slots.resident.ceiling.reason untouched"

    def _fake_resolve(model_id, *, role, backend, model_path=None):
        if role == "primary":
            raise ceiling_mod.ModelCeilingViolation(sentinel, model_id=model_id, role=role)
        return ceiling_mod.ModelProvenance(model_id, 7.0, "override", role, backend)

    monkeypatch.setattr(ceiling_mod, "resolve_answering_model", _fake_resolve)

    slots = app_mod._chat_slots_payload(ollama_warm_ids=set())

    assert slots["resident"]["ceiling"]["eligible"] is False
    assert slots["resident"]["ceiling"]["reason"] == sentinel, (
        "slots.resident.ceiling.reason must be the chokepoint's own message "
        "verbatim — a mismatch means app.py is re-deriving eligibility "
        "instead of calling through arail.registry.ceiling"
    )


def test_slots_deep_ceiling_propagates_the_real_chokepoints_message(monkeypatch):
    import arail.portal.app as app_mod
    from arail.registry import ceiling as ceiling_mod

    _isolate_registry(monkeypatch)
    _seed_env(monkeypatch, model_name="llama-ai-eng",
              aerollm_model="fake-oversized-deep-model")

    sentinel = "SENTINEL-C-CEILROW-DEEP-2b7e — must survive into slots.deep.reason untouched"

    def _fake_resolve(model_id, *, role, backend, model_path=None):
        if role == "secondary":
            raise ceiling_mod.ModelCeilingViolation(sentinel, model_id=model_id, role=role)
        return ceiling_mod.ModelProvenance(model_id, 1.0, "override", role, backend)

    monkeypatch.setattr(ceiling_mod, "resolve_answering_model", _fake_resolve)

    slots = app_mod._chat_slots_payload(ollama_warm_ids=set())

    assert slots["deep"]["eligible"] is False
    assert slots["deep"]["reason"] == sentinel, (
        "slots.deep.reason must be the chokepoint's own message verbatim"
    )


def test_slots_deep_cap_b_matches_the_real_hardware_function(monkeypatch):
    """slots.deep.cap_b must be read from arail.hardware.secondary_model_cap_b(),
    not a re-derived constant — patch it to a distinctive value and check
    the exact value survives."""
    import arail.portal.app as app_mod
    from arail import hardware as hardware_mod

    _isolate_registry(monkeypatch)
    _seed_env(monkeypatch, model_name="llama-ai-eng")

    monkeypatch.setattr(hardware_mod, "secondary_model_cap_b", lambda: 12.5)

    slots = app_mod._chat_slots_payload(ollama_warm_ids=set())
    assert slots["deep"]["cap_b"] == 12.5


def test_slots_resident_ceiling_eligible_when_small(monkeypatch):
    """Sanity check the happy path too, not just the refusal path — the
    shipped default (llama-ai-eng, ~1B via the model_specs override
    table) must be eligible with no reason."""
    import arail.portal.app as app_mod

    _isolate_registry(monkeypatch)
    _seed_env(monkeypatch, model_name="llama-ai-eng")

    slots = app_mod._chat_slots_payload(ollama_warm_ids=set())
    assert slots["resident"]["ceiling"]["eligible"] is True
    assert slots["resident"]["ceiling"]["reason"] is None
    assert slots["resident"]["params_b"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Attribution: the Llama license naming clause (NOTICE:36-46) must be
# satisfied by the picker payload, not just by convention.
# ---------------------------------------------------------------------------

def test_local_model_entry_carries_built_with_llama_attribution():
    import arail.portal.app as app_mod

    entry = app_mod._build_local_model_entry(
        "llama-ai-eng:latest",
        runtime="ollama",
        size_gb=0.9,
        modified="",
        endpoint=None,
        current=None,
        detected_gb=16.0,
        free_gb=8.0,
        catalog_family={"llama-ai-eng": "llama"},
    )
    assert entry["attribution"] == "Built with Llama"


def test_local_model_entry_no_attribution_for_non_llama_family():
    import arail.portal.app as app_mod

    entry = app_mod._build_local_model_entry(
        "ai-engineer:latest",
        runtime="ollama",
        size_gb=4.7,
        modified="",
        endpoint=None,
        current=None,
        detected_gb=16.0,
        free_gb=8.0,
        catalog_family={"ai-engineer": "qwen"},
    )
    assert entry["attribution"] is None


def test_local_model_entry_slot_default_visible_reflects_the_3b_cutoff():
    import arail.portal.app as app_mod

    small = app_mod._build_local_model_entry(
        "llama-ai-eng:latest", runtime="ollama", size_gb=0.9, modified="",
        endpoint=None, current=None, detected_gb=16.0, free_gb=8.0,
        catalog_family={"llama-ai-eng": "llama"},
    )
    large = app_mod._build_local_model_entry(
        "ai-engineer:latest", runtime="ollama", size_gb=4.7, modified="",
        endpoint=None, current=None, detected_gb=16.0, free_gb=8.0,
        catalog_family={"ai-engineer": "qwen"},
    )
    assert small["slot_default_visible"] is True    # ~1B, under the 3B cutoff
    assert large["slot_default_visible"] is False    # ~7B, "show larger" territory
