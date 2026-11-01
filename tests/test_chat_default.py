"""Tests for POST /api/chat/default + _apply_chat_defaults.

Sprint: 2026-05-18-provider-aware-chat-dropdown, Phase C step 10.

Covers F-DEFAULT-LEAK, clear path, per-message wins (A8).
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import patch

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(_REPO_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))


def _deep_slot_resolvable(monkeypatch, tmp_path, model):
    """Machine facts that let the resolver accept *model* as slot B."""
    from arail import model_resolve as mr
    models = tmp_path / "models"
    (models / model).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ARAIL_MODELS_DIR", str(models))
    monkeypatch.setattr(mr, "_deep_slot_available", lambda: None)
    monkeypatch.setattr(mr, "_scan_models_dir", lambda: [{"id": model, "size_gb": 2.0}])
    monkeypatch.setattr(mr, "_total_ram_gb", lambda: 32.0)
    monkeypatch.setattr(mr, "_secondary_cap_b", lambda: 14.0)


def _make_client(monkeypatch, lab_mode="hybrid"):
    monkeypatch.setenv("LAB_MODE", lab_mode)
    from fastapi.testclient import TestClient
    from arail.portal.app import app
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# POST /api/chat/default — set path
# ---------------------------------------------------------------------------

def test_chat_default_set_local_ok(monkeypatch, isolated_secrets):
    """Setting a local (my_machine) default is always allowed."""
    client = _make_client(monkeypatch, lab_mode="airgapped")
    r = client.post(
        "/api/chat/default",
        json={"provider": "my_machine", "model": "qwen3:8b", "runtime": "ollama"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    assert body.get("provider") == "my_machine"


def test_chat_default_set_cloud_while_airgapped_refused(monkeypatch, isolated_secrets):
    """F-DEFAULT-LEAK: setting a cloud default while airgapped must be refused."""
    client = _make_client(monkeypatch, lab_mode="airgapped")
    r = client.post(
        "/api/chat/default",
        json={"provider": "claude", "model": "claude-opus-4-7", "runtime": "claude"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is False
    assert "airgapped" in body.get("error", "").lower()


def test_chat_default_set_cloud_in_hybrid_ok(monkeypatch, isolated_secrets):
    """Setting a cloud default in hybrid mode is allowed."""
    client = _make_client(monkeypatch, lab_mode="hybrid")
    r = client.post(
        "/api/chat/default",
        json={"provider": "claude", "model": "claude-sonnet-4-20250514", "runtime": "claude"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True


# ---------------------------------------------------------------------------
# POST /api/chat/default — clear path
# ---------------------------------------------------------------------------

def test_chat_default_clear(monkeypatch, isolated_secrets):
    """Clear removes ARAIL_CHAT_DEFAULT_MODEL from env."""
    monkeypatch.setenv("ARAIL_CHAT_DEFAULT_MODEL",
                       json.dumps({"model": "qwen3:8b", "runtime": "ollama"}))
    client = _make_client(monkeypatch)
    r = client.post("/api/chat/default", json={"clear": True})
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    assert body.get("cleared") is True


# ---------------------------------------------------------------------------
# _apply_chat_defaults — unit tests
# ---------------------------------------------------------------------------

def test_apply_chat_defaults_per_message_wins(monkeypatch):
    """A8: if all three are provided, _apply_chat_defaults returns them unchanged."""
    monkeypatch.setenv("ARAIL_CHAT_DEFAULT_MODEL",
                       json.dumps({"model": "stored-model", "runtime": "stored-runtime"}))
    monkeypatch.setenv("COMPUTE_SOURCE", "stored-provider")

    from arail.portal.app import _apply_chat_defaults
    b, m, r = _apply_chat_defaults("my-backend", "my-model", "my-runtime")
    assert b == "my-backend"
    assert m == "my-model"
    assert r == "my-runtime"


def test_apply_chat_defaults_fills_blanks(monkeypatch):
    """Blanks (None) are filled from the stored default."""
    monkeypatch.setenv("ARAIL_CHAT_DEFAULT_MODEL",
                       json.dumps({"model": "qwen3:8b", "runtime": "ollama"}))
    monkeypatch.setenv("COMPUTE_SOURCE", "my_machine")
    monkeypatch.setenv("LAB_MODE", "hybrid")

    from arail.portal.app import _apply_chat_defaults
    b, m, r = _apply_chat_defaults(None, None, None)
    assert m == "qwen3:8b"
    assert r == "ollama"


def test_apply_chat_defaults_drops_cloud_default_when_airgapped(monkeypatch):
    """F-DEFAULT-LEAK: stored cloud default dropped when lab is airgapped."""
    monkeypatch.setenv("ARAIL_CHAT_DEFAULT_MODEL",
                       json.dumps({"model": "claude-opus-4-7", "runtime": "claude"}))
    monkeypatch.setenv("COMPUTE_SOURCE", "claude")
    monkeypatch.setenv("LAB_MODE", "airgapped")

    from arail.portal.app import _apply_chat_defaults, _CLOUD_PROVIDERS
    assert "claude" in _CLOUD_PROVIDERS  # sanity

    b, m, r = _apply_chat_defaults(None, None, None)
    # Cloud default must be dropped; fallback to my_machine or None
    assert m not in ("claude-opus-4-7",), (
        f"F-DEFAULT-LEAK: cloud model leaked through airgap: {m!r}"
    )
    assert b not in _CLOUD_PROVIDERS, (
        f"F-DEFAULT-LEAK: cloud backend leaked through airgap: {b!r}"
    )


def test_apply_chat_defaults_no_stored_default_returns_original(monkeypatch):
    """If no default is stored, return the original values unchanged."""
    monkeypatch.delenv("ARAIL_CHAT_DEFAULT_MODEL", raising=False)
    monkeypatch.setenv("LAB_MODE", "hybrid")

    from arail.portal.app import _apply_chat_defaults
    b, m, r = _apply_chat_defaults(None, "my-model", "my-runtime")
    assert m == "my-model"
    assert r == "my-runtime"


def test_apply_chat_defaults_bad_json_returns_original(monkeypatch):
    """Bad JSON in ARAIL_CHAT_DEFAULT_MODEL — must not raise, return original."""
    monkeypatch.setenv("ARAIL_CHAT_DEFAULT_MODEL", "NOT VALID JSON {{{")

    from arail.portal.app import _apply_chat_defaults
    b, m, r = _apply_chat_defaults(None, None, None)
    # Should return the originals (all None) without raising
    assert b is None
    assert m is None
    assert r is None


# ---------------------------------------------------------------------------
# POST /api/chat/default — `slot` (sprints/2026-08-11-two-slot-chat-models)
# ---------------------------------------------------------------------------

def _isolate_registry(monkeypatch, tmp_path):
    from arail.registry import core as reg_core
    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE",
                       str(tmp_path / "model_registry.json"))
    monkeypatch.setenv("MODEL_BACKEND", "ollama_native")
    monkeypatch.setenv("MODEL_NAME", "llama-ai-eng")
    monkeypatch.setenv("AEROLLM_MODEL", "Qwen2.5-7B-Instruct-4bit")
    reg_core.reset_registry()


def test_chat_default_unknown_slot_refused(monkeypatch, isolated_secrets, tmp_path):
    _isolate_registry(monkeypatch, tmp_path)
    client = _make_client(monkeypatch, lab_mode="airgapped")
    r = client.post("/api/chat/default", json={"slot": "sideways", "model": "x"})
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is False
    assert "sideways" in body.get("error", "")


def test_chat_default_deep_slot_requires_model(monkeypatch, isolated_secrets, tmp_path):
    _isolate_registry(monkeypatch, tmp_path)
    client = _make_client(monkeypatch, lab_mode="airgapped")
    r = client.post("/api/chat/default", json={"slot": "deep"})
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is False
    assert "model" in body.get("error", "").lower()


def test_chat_default_deep_slot_refuses_oversized_model(monkeypatch, isolated_secrets, tmp_path):
    """The deep slot goes through the SAME secondary-role chokepoint as
    everything else — an unreadable/oversized model must refuse here too,
    not just at load/send time."""
    _isolate_registry(monkeypatch, tmp_path)
    client = _make_client(monkeypatch, lab_mode="airgapped")
    r = client.post("/api/chat/default", json={"slot": "deep", "model": "totally-unknown-model-xyz"})
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is False
    assert "unknown parameter count" in body.get("error", "").lower() or "params" in body.get("error", "").lower()


def test_chat_default_deep_slot_refuses_when_not_on_disk(monkeypatch, isolated_secrets, tmp_path):
    _isolate_registry(monkeypatch, tmp_path)
    from arail.portal import app as portal_app
    with patch.object(portal_app, "_aerollm_model_ready", return_value=False):
        client = _make_client(monkeypatch, lab_mode="airgapped")
        r = client.post("/api/chat/default", json={"slot": "deep", "model": "Qwen2.5-3B-Instruct-4bit"})
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is False
    assert "disk" in body.get("error", "").lower()


def test_chat_default_deep_slot_success_updates_env_and_registry(monkeypatch, isolated_secrets, tmp_path):
    _isolate_registry(monkeypatch, tmp_path)
    _deep_slot_resolvable(monkeypatch, tmp_path, "Qwen2.5-3B-Instruct-4bit")
    # requires_restart is now computed from the resident runtime (it used to
    # be a constant False); pin "nothing resident" so another test's leftover
    # AeroLLMBackend singleton can't change the answer.
    from arail.router.backends import AeroLLMBackend
    monkeypatch.setattr(AeroLLMBackend, "_shared", {}, raising=False)
    from arail.portal import app as portal_app
    with patch.object(portal_app, "_aerollm_model_ready", return_value=True):
        client = _make_client(monkeypatch, lab_mode="airgapped")
        r = client.post("/api/chat/default", json={"slot": "deep", "model": "Qwen2.5-3B-Instruct-4bit"})
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    assert body.get("slot") == "deep"
    assert body.get("model") == "Qwen2.5-3B-Instruct-4bit"
    assert body.get("registry_updated") is True
    assert body.get("requires_restart") is False
    assert os.environ.get("AEROLLM_MODEL") == "Qwen2.5-3B-Instruct-4bit"

    from arail.registry import get_registry
    reg = get_registry()
    reg._ensure_loaded()
    assert reg.entries["tier1-aerollm"].model_id == "Qwen2.5-3B-Instruct-4bit"
    assert reg.entries["tier1-aerollm"].source == "user"
    # The pick is a pin, so it survives a reboot (it used to live only in
    # env + registry and was lost when the registry was re-seeded).
    from arail import model_defaults
    assert model_defaults.read()["pins"]["b"] == "Qwen2.5-3B-Instruct-4bit"


def test_chat_default_resident_write_through_for_small_ollama_model(monkeypatch, isolated_secrets, tmp_path):
    _isolate_registry(monkeypatch, tmp_path)
    client = _make_client(monkeypatch, lab_mode="airgapped")
    r = client.post(
        "/api/chat/default",
        json={"provider": "my_machine", "model": "llama-ai-eng:latest", "runtime": "ollama"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    assert body.get("registry_updated") is True

    from arail.registry import get_registry
    reg = get_registry()
    reg._ensure_loaded()
    assert reg.entries["tier0-local"].model_id == "llama-ai-eng:latest"
    assert reg.entries["tier0-local"].source == "user"


def test_chat_default_resident_write_through_skipped_for_oversized_model(monkeypatch, isolated_secrets, tmp_path):
    """A >=8B pick still sets the chat default (ok:true, back-compat) but is
    NOT promoted to the registry's tier0 identity — the ceiling refusal is
    silent to the chat-default caller, loud (False) in registry_updated."""
    _isolate_registry(monkeypatch, tmp_path)
    client = _make_client(monkeypatch, lab_mode="airgapped")
    r = client.post(
        "/api/chat/default",
        json={"provider": "my_machine", "model": "qwen2.5:14b", "runtime": "ollama"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    assert body.get("registry_updated") is False

    from arail.registry import get_registry
    reg = get_registry()
    reg._ensure_loaded()
    assert reg.entries["tier0-local"].model_id != "qwen2.5:14b"


def test_chat_default_resident_write_through_skipped_for_non_ollama_runtime(monkeypatch, isolated_secrets, tmp_path):
    """mlx/other local runtimes persist as the chat default only — pinning
    and the registry's tier0 identity are an Ollama-model story."""
    _isolate_registry(monkeypatch, tmp_path)
    client = _make_client(monkeypatch, lab_mode="airgapped")
    r = client.post(
        "/api/chat/default",
        json={"provider": "my_machine", "model": "Qwen2.5-3B-Instruct-4bit", "runtime": "mlx"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    assert body.get("registry_updated") is False
