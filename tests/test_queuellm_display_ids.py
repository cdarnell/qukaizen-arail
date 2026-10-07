"""T-IDS (F1): the QueueLLM display rename changes labels, never ids.

Machine-read values (backend id, registry id, class name, provider/runtime
values, DOM ids, data-view) must stay byte-identical so the frontend and API
consumers keep routing correctly. Only the human-readable label changes.
"""
from __future__ import annotations

from pathlib import Path

import arail.portal.app as appmod
from arail.router.backends import BACKEND_MAP, AeroLLMBackend

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "src" / "arail" / "portal" / "templates"


def test_backend_and_registry_ids_are_frozen():
    assert BACKEND_MAP["aerollm"] is AeroLLMBackend
    from arail.registry.store import TIER1_ID
    assert TIER1_ID == "tier1-aerollm"


def test_optional_backend_config_label_changes_ids_do_not():
    cfg = appmod._OPTIONAL_CHAT_BACKEND_CONFIG["aerollm"]
    assert cfg["label"] == "QueueLLM"
    assert cfg["class_name"] == "AeroLLMBackend"


def test_compute_source_row_keeps_id_and_shows_new_label(monkeypatch):
    monkeypatch.setattr(appmod, "_is_aerollm_installed", lambda: True)
    rows = {s["id"]: s for s in appmod._compact_compute_sources("my_machine")}
    assert "aerollm" in rows
    assert "QueueLLM" in str(rows["aerollm"])
    assert "aeroLLM" not in str(rows["aerollm"])


def test_display_provider_name_maps_only_the_label():
    assert appmod._display_provider_name("aerollm") == "QueueLLM"
    assert appmod._display_provider_name("claude") == "Claude"
