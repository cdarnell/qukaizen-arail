"""setup.sh and the install hints stop claiming readiness or a route that no longer builds main.

7b of sprints/2026-10-29-arail-deep-honest/ARCHITECTURE.md (C7, last paragraph). The wheel-missing health
detail is covered with the registry tests; it is repeated here only as a pointer check.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_setup_sh_no_longer_claims_readiness():
    setup = (REPO / "scripts" / "setup.sh").read_text(encoding="utf-8")
    assert "aerollm_api already present — generation not tested here; run ./arailctl deep probe." in setup
    assert "QueueLLM installed — run ./arailctl deep probe to test generation." in setup
    for gone in ("QueueLLM ready", "QueueLLM is the deep-mode 2nd inference."):
        assert gone not in setup
    # the failure message still names the outside-user route (test_aerollm_bundle_qa_hardening pins this too)
    window = setup[setup.find("QueueLLM not installed"):][:400]
    assert "deep install" in window


def test_the_import_error_hint_says_rebuild_needs_a_pre_rename_checkout():
    import arail.router.backends as backends
    sys_mod = sys.modules.pop("aerollm_api", None)
    sys.modules["aerollm_api"] = None            # `import aerollm_api` raises ImportError
    backends.AeroLLMBackend._shared.clear()
    try:
        with pytest.raises(ImportError) as ei:
            backends.AeroLLMBackend()
    finally:
        sys.modules.pop("aerollm_api", None)
        if sys_mod is not None:
            sys.modules["aerollm_api"] = sys_mod
        backends.AeroLLMBackend._shared.clear()
    msg = str(ei.value)
    assert "the pre-rename crates/aerollm-api" in msg and "refuses a checkout with the renamed crates/queuellm-api" in msg
    assert msg.index("deep install") < msg.index("deep rebuild") < msg.index("deep update")
    assert "build from the local sibling repo" not in msg


# ── review B2 / TD13: a user with no checkout is sent to `deep install`, not `deep rebuild` ──────────────

def test_the_portal_hints_for_a_missing_engine_say_deep_install(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import arail.chat as chat_mod
    from arail.portal import app as appmod
    from arail.portal import model_warmth

    # the Chat error reply (it names the config's install_command)
    assert appmod._OPTIONAL_CHAT_BACKEND_CONFIG["aerollm"]["install_command"] == "./arailctl deep install"
    reply = appmod._optional_backend_error_result("aerollm", RuntimeError("boom"))["reply"]
    assert "`./arailctl deep install`" in reply and "deep rebuild" not in reply

    # the optional_backends entry the Chat tab reads
    monkeypatch.setenv("ARAIL_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setattr(appmod, "_local_memory_snapshot", lambda: {
        "label": "Apple M5", "gpu_label": None, "total_gb": 24.0, "used_gb": 6.0, "free_gb": 18.0})
    monkeypatch.setattr(chat_mod, "gallery_view", lambda: {"installed": [], "catalog": [], "runtime_counts": {}})
    monkeypatch.setattr(appmod, "_is_aerollm_installed", lambda: False)
    monkeypatch.setattr(model_warmth, "_tier1_resident", lambda: False)
    body = TestClient(appmod.app).get("/api/chat/models").json()
    entry = next(b for b in body["optional_backends"] if b["id"] == "aerollm")
    assert entry["installed"] is False and entry["install_command"] == "./arailctl deep install"


def test_the_chat_page_hint_for_a_missing_engine_says_deep_install():
    from arail.portal import app as appmod
    html = (Path(appmod.__file__).parent / "templates" / "chat.html").read_text(encoding="utf-8")
    assert "QueueLLM isn’t installed — run ./arailctl deep install" in html
    assert "deep rebuild" not in html


def test_the_upgrade_to_maximus_hint_says_deep_install():
    """scripts/upgrade.sh prints the next steps to a user who has just moved to maximus; that user
    has no checkout. Found by the wider grep for TD13; same class, same fix."""
    text = (Path(__file__).resolve().parents[1] / "scripts" / "upgrade.sh").read_text(encoding="utf-8")
    assert "1. Install QueueLLM: ./arailctl deep install" in text
    assert "deep rebuild" not in text


def test_the_wheel_missing_health_detail_points_at_deep_install():
    from arail.registry import health
    src = Path(health.__file__).read_text(encoding="utf-8")
    assert "aerollm_api wheel not installed " in src and "(./arailctl deep install)" in src
    assert "(./arailctl deep rebuild)" not in src
