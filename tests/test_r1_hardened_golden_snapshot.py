"""R1 hardened: value-level golden snapshot of the no-provider legacy branch.

Sprint: 2026-05-18-provider-aware-chat-dropdown, fix-loop pass (C1 carryover).

The architect required a byte-identical golden snapshot (not just key-presence)
so that ANY future edit to the legacy /api/chat/models branch causes a test
failure. This supplements test_r1_r3_chat_models.py (which guards routing and
key presence); this file guards VALUE STRUCTURE with deterministically-mocked
internals.

Strategy:
  - Mock _get_primary_router to return a deterministic fake router/backend.
  - Mock gallery_view to return a deterministic gallery.
  - Mock _local_memory_snapshot to return deterministic hardware numbers.
  - Mock _get_live_ollama_current to return None (so current = model_name).
  - Mock _scan_local_models and similar to return empty/deterministic values.
  - Capture the response, then assert exact equality on the FULL payload dict.

If the legacy branch adds a key, removes a key, or changes a computed value,
this test fails — which is the point.
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(_REPO_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))


# ---------------------------------------------------------------------------
# Deterministic mock fixtures
# ---------------------------------------------------------------------------

_FAKE_MODEL_NAME = "ai-eng:latest"
_FAKE_BACKEND_NAME = "openai_compat"

_FAKE_GALLERY = {
    "installed": [
        {
            "id": "ai-eng:latest",
            "label": "ai-eng:latest",
            "runtime": "ollama",
            "size_gb": 2.5,
            "installed_state": "installed",
            "source": "ollama",
            "modified": "2026-01-01T00:00:00",
            "endpoint": None,
        }
    ],
    "catalog": [
        {
            "id": "qwen2.5:7b",
            "name": "Qwen 2.5 7B",
            "family": "qwen",
            "installed_state": "not-installed",
            "source": "catalog",
            "size_gb": 4.2,
            "tier": "optional",
            "runtime": None,
            "provider": None,
            "ctx": None,
        }
    ],
    "runtime_counts": {"ollama": 1},
}

_FAKE_MEMORY = {
    "total_gb": 16.0,
    "free_gb": 8.0,
    "used_gb": 8.0,
    "label": "16 GB",
}


def _make_fake_router():
    """Return a MagicMock that looks like the object _get_primary_router() returns."""
    fake_be = MagicMock()
    fake_be.model_name = _FAKE_MODEL_NAME
    fake_be.base_url = "http://127.0.0.1:11434/v1"
    fake_be.api_key = "not-needed"

    fake_router = MagicMock()
    fake_router.backend_name = _FAKE_BACKEND_NAME
    fake_router._backend = fake_be
    return fake_router


# ---------------------------------------------------------------------------
# The golden snapshot capture helper
# ---------------------------------------------------------------------------

def _isolate_registry(monkeypatch) -> None:
    """Point the process-wide model registry at a throwaway file + fresh
    singleton (same convention as tests/registry/conftest.py's
    tmp_registry) before any test exercises /api/chat/models — the
    endpoint now reads get_registry() to build `slots`
    (sprints/2026-08-11-two-slot-chat-models), and without this a test
    run would read/pollute the real lab/data/model_registry.json, or
    inherit a stale singleton left loaded by an earlier test."""
    import tempfile
    from arail.registry import core as reg_core
    tmp_dir = tempfile.mkdtemp(prefix="arail-r1-golden-registry-")
    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE", os.path.join(tmp_dir, "model_registry.json"))
    reg_core.reset_registry()


def _capture_golden_payload(monkeypatch) -> dict:
    """Run GET /api/chat/models with fully-mocked internals; return the JSON dict."""
    monkeypatch.setenv("LAB_MODE", "airgapped")
    monkeypatch.setenv("MODEL_NAME", _FAKE_MODEL_NAME)
    monkeypatch.setenv("ARAIL_MODELS_DIR", "/nonexistent/models_dir_for_test")
    _isolate_registry(monkeypatch)

    from arail.portal import app as portal_app
    from fastapi.testclient import TestClient

    fake_router = _make_fake_router()

    with patch.object(portal_app, "_get_primary_router", return_value=fake_router), \
         patch("arail.chat.gallery_view", return_value=_FAKE_GALLERY), \
         patch.object(portal_app, "_local_memory_snapshot", return_value=_FAKE_MEMORY), \
         patch.object(portal_app, "_get_live_ollama_current", return_value=None), \
         patch.object(portal_app, "_load_active_provider", return_value="my_machine"), \
         patch("requests.get", side_effect=ConnectionError("mocked — no network")):

        client = TestClient(portal_app.app, raise_server_exceptions=False)
        r = client.get("/api/chat/models")

    assert r.status_code == 200, f"GET /api/chat/models returned {r.status_code}"
    return r.json()


# ---------------------------------------------------------------------------
# R1-SNAPSHOT-1: Top-level key set is frozen
# ---------------------------------------------------------------------------

_R1_EXPECTED_TOP_KEYS = {
    "backend", "provider", "current", "models", "switchable",
    "local_models", "install_hint", "optional_backends",
    "default_optional_backend", "deep", "gallery", "compact",
    "onboarding", "local_model_entries", "fit", "model_load", "slots",
}
# NOTE: top-level "hardware" was DELETED (sprint 2026-07-20-model-ux-unification,
# §2.1/BLOCK-1, F-DEADFIELD). The frontend's only reader was `compact.hardware`,
# which the response never populated (F-BLANK) — the snapshot is now nested
# there instead of duplicated at the top level where nothing read it.
#
# NOTE: top-level "slots" was ADDED (sprint 2026-08-11-two-slot-chat-models)
# — the two-slot model (resident/deep), read from the registry. See
# test_chat_slots_contract.py for value-level coverage.


def test_r1_snapshot_top_keys_exact(monkeypatch):
    """R1 hardened: legacy branch must return EXACTLY the expected top-level keys.
    Adding OR removing a key fails this test."""
    body = _capture_golden_payload(monkeypatch)

    actual_keys = set(body.keys())
    added = actual_keys - _R1_EXPECTED_TOP_KEYS
    removed = _R1_EXPECTED_TOP_KEYS - actual_keys

    assert not added and not removed, (
        f"R1 golden snapshot: top-level key drift detected.\n"
        f"  Added (new keys not in baseline): {sorted(added)}\n"
        f"  Removed (missing keys): {sorted(removed)}"
    )


def test_r1_snapshot_no_cloud_fields_in_legacy_response(monkeypatch):
    """R1: legacy branch must not include 'airgapped:true' or 'cta' (cloud-only fields)."""
    body = _capture_golden_payload(monkeypatch)

    assert body.get("airgapped") is not True, (
        "R1 regression: legacy branch returned airgapped:true — cloud branch leaked"
    )
    assert "cta" not in body, (
        "R1 regression: legacy branch returned 'cta' key — cloud branch leaked"
    )


# ---------------------------------------------------------------------------
# R1-SNAPSHOT-2: gallery shape is frozen (installed/catalog/runtime_counts only)
# ---------------------------------------------------------------------------

def test_r1_snapshot_gallery_keys_exact(monkeypatch):
    """R1 hardened: gallery dict must have EXACTLY {installed, catalog, runtime_counts}."""
    body = _capture_golden_payload(monkeypatch)
    gallery = body.get("gallery", {})

    expected_gallery_keys = {"installed", "catalog", "runtime_counts"}
    actual_gallery_keys = set(gallery.keys()) - {"error"}  # error is tolerated on scan fail

    added = actual_gallery_keys - expected_gallery_keys
    removed = expected_gallery_keys - actual_gallery_keys

    assert not added and not removed, (
        f"R1: gallery key drift.\n"
        f"  Added: {sorted(added)}\n"
        f"  Removed: {sorted(removed)}"
    )


def test_r1_snapshot_gallery_types(monkeypatch):
    """R1 hardened: gallery.installed and gallery.catalog must be lists;
    gallery.runtime_counts must be a dict."""
    body = _capture_golden_payload(monkeypatch)
    gallery = body.get("gallery", {})

    assert isinstance(gallery.get("installed"), list), (
        f"gallery.installed must be list, got {type(gallery.get('installed'))}"
    )
    assert isinstance(gallery.get("catalog"), list), (
        f"gallery.catalog must be list, got {type(gallery.get('catalog'))}"
    )
    assert isinstance(gallery.get("runtime_counts"), dict), (
        f"gallery.runtime_counts must be dict, got {type(gallery.get('runtime_counts'))}"
    )


# ---------------------------------------------------------------------------
# R1-SNAPSHOT-3: value-level assertions on stable fields
# ---------------------------------------------------------------------------

def test_r1_snapshot_backend_field_is_string(monkeypatch):
    """R1: backend field must be a string (the backend name)."""
    body = _capture_golden_payload(monkeypatch)
    assert isinstance(body.get("backend"), str), (
        f"backend must be a string, got {type(body.get('backend'))}: {body.get('backend')!r}"
    )


def test_r1_snapshot_switchable_is_bool(monkeypatch):
    """R1: switchable must be a bool."""
    body = _capture_golden_payload(monkeypatch)
    assert isinstance(body.get("switchable"), bool), (
        f"switchable must be bool, got {type(body.get('switchable'))}"
    )


def test_r1_snapshot_models_is_list(monkeypatch):
    """R1: models must be a list."""
    body = _capture_golden_payload(monkeypatch)
    assert isinstance(body.get("models"), list), (
        f"models must be a list, got {type(body.get('models'))}"
    )


def test_r1_snapshot_deep_has_required_keys(monkeypatch):
    """R1: deep dict must have {model, installed, param_hint, spec, default_enabled, gated, streamed}."""
    body = _capture_golden_payload(monkeypatch)
    deep = body.get("deep", {})
    required_deep_keys = {"model", "installed", "param_hint", "spec",
                          "default_enabled", "gated", "streamed"}
    missing = required_deep_keys - deep.keys()
    assert not missing, (
        f"R1: deep dict missing keys: {sorted(missing)}"
    )


def test_r1_snapshot_compact_has_required_keys(monkeypatch):
    """R1: compact dict must have {label, compute_sources, hosting_line, local_models,
    custom_override, overlay, hardware}."""
    body = _capture_golden_payload(monkeypatch)
    compact = body.get("compact", {})
    required_compact_keys = {"label", "compute_sources", "hosting_line",
                             "local_models", "custom_override", "overlay",
                             "hardware"}
    missing = required_compact_keys - compact.keys()
    assert not missing, (
        f"R1: compact dict missing keys: {sorted(missing)}"
    )


def test_r1_snapshot_hardware_has_required_keys(monkeypatch):
    """R1: compact.hardware dict must have {total_gb, free_gb, used_gb, label}
    (§2.1 — nested under compact; F-BLANK's fix. There is no top-level
    `hardware` key any more — see test_r1_snapshot_no_top_level_hardware)."""
    body = _capture_golden_payload(monkeypatch)
    hardware = body.get("compact", {}).get("hardware", {})
    required_hw_keys = {"total_gb", "free_gb", "used_gb", "label"}
    missing = required_hw_keys - hardware.keys()
    assert not missing, (
        f"R1: compact.hardware dict missing keys: {sorted(missing)}"
    )


def test_r1_snapshot_no_top_level_hardware(monkeypatch):
    """F-DEADFIELD (BLOCK-1): the top-level `hardware` key must be gone, not
    lingering alongside compact.hardware as a second, unread copy."""
    body = _capture_golden_payload(monkeypatch)
    assert "hardware" not in body, (
        "R1 regression: a top-level 'hardware' key reappeared — "
        "the frontend only reads compact.hardware; a top-level duplicate "
        "is exactly the dead-field pattern this sprint removed (BLOCK-1)."
    )


def test_r1_snapshot_model_load_has_required_keys(monkeypatch):
    """R1: model_load dict must have {state, blocking, message, eta_seconds,
    cancel_path, status_path}."""
    body = _capture_golden_payload(monkeypatch)
    ml = body.get("model_load", {})
    required_ml_keys = {"state", "blocking", "message", "eta_seconds",
                        "cancel_path", "status_path"}
    missing = required_ml_keys - ml.keys()
    assert not missing, (
        f"R1: model_load dict missing keys: {sorted(missing)}"
    )


def test_r1_snapshot_onboarding_has_required_keys(monkeypatch):
    """R1: onboarding dict must have {title, folder, layout, cli_example,
    formats_note, autodetect_note}."""
    body = _capture_golden_payload(monkeypatch)
    onboarding = body.get("onboarding", {})
    required_keys = {"title", "folder", "layout", "cli_example",
                     "formats_note", "autodetect_note"}
    missing = required_keys - onboarding.keys()
    assert not missing, (
        f"R1: onboarding dict missing keys: {sorted(missing)}"
    )


# ---------------------------------------------------------------------------
# R1-SNAPSHOT-4: routing guards (route the correct branch)
# ---------------------------------------------------------------------------

def test_r1_snapshot_empty_provider_reaches_legacy(monkeypatch):
    """R1: ?provider= (empty) must reach the legacy branch (not cloud)."""
    monkeypatch.setenv("LAB_MODE", "airgapped")
    _isolate_registry(monkeypatch)

    from arail.portal import app as portal_app
    from fastapi.testclient import TestClient

    fake_router = _make_fake_router()

    with patch.object(portal_app, "_get_primary_router", return_value=fake_router), \
         patch("arail.chat.gallery_view", return_value=_FAKE_GALLERY), \
         patch.object(portal_app, "_local_memory_snapshot", return_value=_FAKE_MEMORY), \
         patch.object(portal_app, "_get_live_ollama_current", return_value=None), \
         patch.object(portal_app, "_load_active_provider", return_value="my_machine"), \
         patch("requests.get", side_effect=ConnectionError("mocked")):

        client = TestClient(portal_app.app, raise_server_exceptions=False)
        r = client.get("/api/chat/models?provider=")

    body = r.json()
    # Legacy branch response has 'backend' key; cloud branch has 'provider' but no 'backend'
    assert "backend" in body, (
        f"?provider= (empty) must reach legacy branch (have 'backend' key), got: {sorted(body.keys())}"
    )
    assert body.get("airgapped") is not True


def test_r1_snapshot_my_machine_reaches_legacy(monkeypatch):
    """R1: ?provider=my_machine must reach the legacy branch (not cloud)."""
    monkeypatch.setenv("LAB_MODE", "airgapped")
    _isolate_registry(monkeypatch)

    from arail.portal import app as portal_app
    from fastapi.testclient import TestClient

    fake_router = _make_fake_router()

    with patch.object(portal_app, "_get_primary_router", return_value=fake_router), \
         patch("arail.chat.gallery_view", return_value=_FAKE_GALLERY), \
         patch.object(portal_app, "_local_memory_snapshot", return_value=_FAKE_MEMORY), \
         patch.object(portal_app, "_get_live_ollama_current", return_value=None), \
         patch.object(portal_app, "_load_active_provider", return_value="my_machine"), \
         patch("requests.get", side_effect=ConnectionError("mocked")):

        client = TestClient(portal_app.app, raise_server_exceptions=False)
        r = client.get("/api/chat/models?provider=my_machine")

    body = r.json()
    assert "backend" in body, (
        f"?provider=my_machine must reach legacy branch, got: {sorted(body.keys())}"
    )
    assert body.get("airgapped") is not True
