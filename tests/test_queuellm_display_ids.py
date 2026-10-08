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


def test_template_ids_and_runtime_values_are_untouched():
    chat = (TEMPLATES / "chat.html").read_text(encoding="utf-8")
    tuning = (TEMPLATES / "tuning.html").read_text(encoding="utf-8")
    research = (TEMPLATES / "research.html").read_text(encoding="utf-8")
    assert "runtime: 'aerollm'" in chat
    assert "'aerollm'" in chat and "tier1-aerollm" in chat
    assert 'data-view="aerollm-mlx"' in tuning
    assert 'data-view="aerollm-cuda"' in tuning
    assert 'id="tn-arch-aerollm"' in tuning
    assert '"aerollm-mlx"' in tuning            # VIEWS / localStorage value
    assert 'value="aerollm"' in research        # radio value posted to the API


def test_pages_render_queuellm_and_no_user_visible_aerollm(monkeypatch):
    import re
    monkeypatch.setenv("LAB_TIER", "maximus")
    from fastapi.testclient import TestClient

    client = TestClient(appmod.app, raise_server_exceptions=False)
    for path in ("/chat", "/research", "/tuning"):
        r = client.get(path)
        assert r.status_code == 200, (path, r.status_code)
        html = r.text
        assert "QueueLLM" in html, path
        # Strip comments and identifier-shaped tokens, then no old display name.
        body = re.sub(r"<!--.*?-->|/\*.*?\*/", "", html, flags=re.S)
        body = re.sub(r"(?m)^\s*//.*$", "", body)
        body = re.sub(r"['\"`][a-z0-9_:.\-/]*aerollm[a-z0-9_:.\-/]*['\"`]", "", body)
        body = re.sub(r"[#.\w-]*aerollm[\w-]*", "", body)   # selectors, ids
        body = re.sub(r"[A-Z0-9_]*AERO(?:LLM)?_[A-Z0-9_]*", "", body)
        assert not re.search(r"aero\s*llm", body, re.I), (
            path, re.search(r".{40}aero\s*llm.{40}", body, re.I | re.S))
