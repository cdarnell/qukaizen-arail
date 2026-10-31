"""T-RESEED (F7): a registry persisted before the QueueLLM display rename
still holds the old built-in Tier-1 note. It must be refreshed on the next
load, but only when it is the exact old built-in text (a user-edited note
survives), and the entry id is never touched."""
from __future__ import annotations

from dataclasses import replace

from arail.registry.store import _LEGACY_TIER1_NOTE, _TIER1_NOTE, TIER1_ID


def _restart():
    from arail.registry import core as reg_core
    reg_core.reset_registry()
    reg = reg_core.get_registry()
    reg._ensure_loaded()
    return reg


def _persist_note(reg, note):
    from arail.registry import store
    reg.entries[TIER1_ID] = replace(reg.entries[TIER1_ID], note=note)
    store.save(reg)


def test_fresh_seed_uses_queuellm_note(tmp_registry):
    note = tmp_registry.entries[TIER1_ID].note
    assert note == _TIER1_NOTE
    assert "QueueLLM" in note and "aeroLLM" not in note


def test_legacy_builtin_note_is_refreshed_on_load(tmp_registry):
    assert "aeroLLM" in _LEGACY_TIER1_NOTE
    _persist_note(tmp_registry, _LEGACY_TIER1_NOTE)
    reg = _restart()
    entry = reg.entries[TIER1_ID]
    assert entry.note == _TIER1_NOTE
    assert entry.id == "tier1-aerollm"
    assert entry.backend == "aerollm" and entry.provider_type == "aerollm"


def test_user_edited_note_is_not_overwritten(tmp_registry):
    _persist_note(tmp_registry, "my own note about the deep model")
    reg = _restart()
    assert reg.entries[TIER1_ID].note == "my own note about the deep model"
