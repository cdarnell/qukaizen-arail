"""QA (sprint 2026-10-07-queuellm-display-rename): registry note migration
on an existing registry, beyond the builder's three T-RESEED cases.
Uses the tmp_registry fixture (tmp file, never the real lab)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from tests.test_queuellm_rename_qa import _visible_hits


# ═════════════════════════════════════════════════════════════════════════
# 5. Registry note migration on an existing registry
# ═════════════════════════════════════════════════════════════════════════
def _legacy_registry_file(tmp_registry, mutate=None):
    """Rewrite the persisted registry as an older build would have written it."""
    from arail.registry.store import _LEGACY_TIER1_NOTE, TIER1_ID
    path = Path(os.environ["ARAIL_MODEL_REGISTRY_FILE"])
    data = json.loads(path.read_text())
    entries = data["entries"] if isinstance(data.get("entries"), list) else None
    assert entries is not None, "registry file layout changed; update this test"
    for e in entries:
        if e["id"] == TIER1_ID:
            e["note"] = _LEGACY_TIER1_NOTE
            if mutate:
                mutate(e)
    path.write_text(json.dumps(data))
    return path


def _restart():
    from arail.registry import core as reg_core
    reg_core.reset_registry()
    reg = reg_core.get_registry()
    reg._ensure_loaded()
    return reg


def test_legacy_note_on_disabled_tier1_is_refreshed(tmp_registry, monkeypatch):
    """A non-maximus lab (Tier-1 disabled) also had the old note persisted."""
    from arail.registry.store import _TIER1_NOTE, TIER1_ID
    _legacy_registry_file(tmp_registry, lambda e: e.update(enabled=False))
    monkeypatch.setenv("LAB_TIER", "minimalist")
    reg = _restart()
    entry = reg.entries[TIER1_ID]
    assert entry.note == _TIER1_NOTE
    assert entry.enabled is False


def test_legacy_note_refresh_is_persisted_and_idempotent(tmp_registry):
    from arail.registry.store import _TIER1_NOTE
    path = _legacy_registry_file(tmp_registry)
    _restart()
    first = path.read_text()
    assert _TIER1_NOTE in first and "via aeroLLM" not in first
    _restart()
    assert path.read_text() == first


def test_aerollm_model_env_still_drives_tier1_and_note_is_new(tmp_registry, monkeypatch):
    """Env compatibility: changing AEROLLM_MODEL re-seeds Tier 1 from env
    ("env wins when env moved") and the re-seeded note uses the new name."""
    from arail.registry.store import _TIER1_NOTE, TIER1_ID
    _legacy_registry_file(tmp_registry)
    monkeypatch.setenv("AEROLLM_MODEL", "Qwen3-30B-A3B-4bit")
    reg = _restart()
    entry = reg.entries[TIER1_ID]
    assert entry.model_id == "Qwen3-30B-A3B-4bit"
    assert entry.note == _TIER1_NOTE
    assert entry.id == "tier1-aerollm" and entry.backend == "aerollm"
    from arail.registry.store import _entry_to_dict
    blob = json.dumps(_entry_to_dict(entry))
    assert not _visible_hits(blob.replace('"tier1-aerollm"', "").replace('"aerollm"', ""))
