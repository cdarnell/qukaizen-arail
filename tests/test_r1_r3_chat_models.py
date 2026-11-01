"""R1 + R3 — load-bearing regressions for /api/chat/models.

Sprint: 2026-05-18-provider-aware-chat-dropdown, Phase C step 8.

R1: GET /api/chat/models with NO ?provider= is byte-identical to a
    captured baseline (keys + structure). The cloud branch must be a
    strict `if provider and provider != "my_machine":` wrapper; the
    legacy code must never enter it.

R3: Airgapped /api/chat/models?provider=<p> returns airgapped:true +
    empty gallery for ALL 10 cloud providers; assert no outbound
    requests.get/requests.post call is made (F-AIRGAP).
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch, MagicMock

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(_REPO_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))


# ---------------------------------------------------------------------------
# R1 — legacy branch key structure is frozen
# ---------------------------------------------------------------------------

def _make_client(monkeypatch):
    """Build a TestClient with minimal env so /api/chat/models works."""
    from fastapi.testclient import TestClient
    monkeypatch.setenv("LAB_MODE", "airgapped")
    # Isolate the process-wide model registry — /api/chat/models reads
    # get_registry() to build `slots` (sprints/2026-08-11-two-slot-chat-
    # models); without this a test run would read/pollute the real
    # lab/data/model_registry.json. Same convention as
    # tests/registry/conftest.py's tmp_registry.
    import tempfile
    from arail.registry import core as reg_core
    tmp_dir = tempfile.mkdtemp(prefix="arail-r1r3-registry-")
    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE", os.path.join(tmp_dir, "model_registry.json"))
    reg_core.reset_registry()
    from arail.portal.app import app
    return TestClient(app, raise_server_exceptions=False)


R1_REQUIRED_TOP_KEYS = {
    "backend", "provider", "current", "models", "switchable",
    "local_models", "install_hint", "optional_backends",
    "default_optional_backend", "deep", "gallery", "compact",
    "onboarding", "local_model_entries", "fit", "model_load", "slots",
}
# NOTE: top-level "hardware" was DELETED (sprint 2026-07-20-model-ux-unification,
# §2.1/BLOCK-1) — it's now nested at compact.hardware, the only place the
# frontend reads it. See test_r1_hardened_golden_snapshot.py for the
# compact.hardware coverage.
#
# NOTE: top-level "slots" was ADDED (sprint 2026-08-11-two-slot-chat-models)
# — the two-slot model (resident/deep), read from the registry.

R1_REQUIRED_GALLERY_KEYS = {"installed", "catalog", "runtime_counts"}


def test_r1_no_provider_param_returns_legacy_keys(monkeypatch):
    """R1: GET /api/chat/models (no ?provider=) must have all legacy top-level keys."""
    client = _make_client(monkeypatch)
    r = client.get("/api/chat/models")
    assert r.status_code == 200
    body = r.json()
    missing = R1_REQUIRED_TOP_KEYS - body.keys()
    assert not missing, f"Legacy branch dropped keys: {missing}"


def test_r1_gallery_shape_preserved(monkeypatch):
    """R1: gallery key must have installed/catalog/runtime_counts."""
    client = _make_client(monkeypatch)
    r = client.get("/api/chat/models")
    assert r.status_code == 200
    body = r.json()
    gallery = body.get("gallery", {})
    missing = R1_REQUIRED_GALLERY_KEYS - gallery.keys()
    assert not missing, f"gallery missing keys: {missing}"


def test_r1_empty_provider_param_goes_to_legacy_branch(monkeypatch):
    """R1: ?provider= (empty string) must still go to legacy branch."""
    client = _make_client(monkeypatch)
    r = client.get("/api/chat/models?provider=")
    assert r.status_code == 200
    body = r.json()
    missing = R1_REQUIRED_TOP_KEYS - body.keys()
    assert not missing, f"Empty provider= went to wrong branch, dropped: {missing}"


def test_r1_my_machine_param_goes_to_legacy_branch(monkeypatch):
    """R1: ?provider=my_machine must go to legacy branch (not cloud)."""
    client = _make_client(monkeypatch)
    r = client.get("/api/chat/models?provider=my_machine")
    assert r.status_code == 200
    body = r.json()
    missing = R1_REQUIRED_TOP_KEYS - body.keys()
    assert not missing, f"my_machine provider went to wrong branch, dropped: {missing}"


def test_r1_no_cloud_in_legacy_response(monkeypatch):
    """R1: legacy branch must not include airgapped/cta fields (those are cloud-only)."""
    client = _make_client(monkeypatch)
    r = client.get("/api/chat/models")
    assert r.status_code == 200
    body = r.json()
    # 'airgapped' is a cloud-branch field; must not appear in the legacy response
    # (it may be absent or None — not a forced boolean True/False in cloud shape)
    assert body.get("airgapped") is not True, (
        "Legacy branch must not return airgapped:true"
    )


# ---------------------------------------------------------------------------
# R3 — airgap parametrized over ALL 10 cloud providers
# ---------------------------------------------------------------------------

ALL_CLOUD_PROVIDERS = [
    "claude", "nvidia", "openrouter", "huggingface", "custom",
    "xai", "google", "mistral", "cohere", "together",
]


@pytest.mark.parametrize("provider", ALL_CLOUD_PROVIDERS)
def test_r3_airgapped_refuses_cloud_provider(monkeypatch, provider):
    """R3: airgapped + ?provider=<p> → airgapped:true, empty gallery, no outbound call."""
    monkeypatch.setenv("LAB_MODE", "airgapped")

    from arail.portal import app as portal_app
    from fastapi.testclient import TestClient

    with patch("requests.get") as mock_get, \
         patch("requests.post") as mock_post:
        client = TestClient(portal_app.app, raise_server_exceptions=False)
        r = client.get(f"/api/chat/models?provider={provider}")

    assert r.status_code == 200
    body = r.json()

    assert body.get("airgapped") is True, (
        f"Provider {provider!r}: expected airgapped:true, got: {body!r}"
    )
    gallery = body.get("gallery", {})
    assert gallery.get("installed") == [], (
        f"Provider {provider!r}: installed must be [] when airgapped"
    )
    assert gallery.get("catalog") == [], (
        f"Provider {provider!r}: catalog must be [] when airgapped"
    )
    # No outbound network calls must be made (F-AIRGAP)
    mock_get.assert_not_called()
    mock_post.assert_not_called()


@pytest.mark.parametrize("provider", ALL_CLOUD_PROVIDERS)
def test_r3_airgapped_chat_default_refuses_cloud_provider(monkeypatch, provider):
    """R3: airgapped + POST /api/chat/default {provider:<p>} → refused."""
    monkeypatch.setenv("LAB_MODE", "airgapped")

    from arail.portal import app as portal_app
    from fastapi.testclient import TestClient

    with patch("requests.get") as mock_get, \
         patch("requests.post") as mock_post:
        client = TestClient(portal_app.app, raise_server_exceptions=False)
        r = client.post(
            "/api/chat/default",
            json={"provider": provider, "model": "some-model", "runtime": provider},
        )

    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is False, (
        f"Provider {provider!r}: cloud default must be refused when airgapped, got: {body!r}"
    )
    mock_get.assert_not_called()
    mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# Cloud branch — structure tests (cloud success / no-token / unknown-provider)
# ---------------------------------------------------------------------------

def test_cloud_branch_no_token_returns_cta(monkeypatch):
    """Cloud branch with no saved token → cta.kind=='no_token', not silent empty."""
    monkeypatch.setenv("LAB_MODE", "hybrid")
    # Clear any token for claude
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    from arail.portal import app as portal_app
    from fastapi.testclient import TestClient

    with patch.object(portal_app, "_provider_token", return_value=""):
        client = TestClient(portal_app.app, raise_server_exceptions=False)
        r = client.get("/api/chat/models?provider=claude")

    assert r.status_code == 200
    body = r.json()
    assert "cta" in body, f"no_token response must include cta, got: {body.keys()}"
    assert body["cta"].get("kind") == "no_token"
    assert body.get("airgapped") is False


def test_cloud_branch_unknown_provider_returns_cta(monkeypatch):
    """Unknown provider → cta.kind=='unknown_provider', never 500."""
    monkeypatch.setenv("LAB_MODE", "hybrid")

    from arail.portal import app as portal_app
    from fastapi.testclient import TestClient

    client = TestClient(portal_app.app, raise_server_exceptions=False)
    r = client.get("/api/chat/models?provider=nonexistent_provider_xyz")

    assert r.status_code == 200
    body = r.json()
    assert "cta" in body, f"Unknown provider must return cta, got: {body.keys()}"
    assert body["cta"].get("kind") == "unknown_provider"


def test_cloud_branch_current_is_cloud_model_not_local(monkeypatch):
    """F-CLOUD-CURRENT: cloud branch must override current to a cloud model id,
    never leaving a local model id (e.g. qwen2.5:7b) as current."""
    monkeypatch.setenv("LAB_MODE", "hybrid")

    from arail.portal import app as portal_app
    from fastapi.testclient import TestClient

    fake_models = ["claude-opus-4-7", "claude-sonnet-4-20250514"]

    with patch.object(portal_app, "_provider_token", return_value="sk-test-token"), \
         patch.object(portal_app, "_fetch_provider_models", return_value=fake_models):
        client = TestClient(portal_app.app, raise_server_exceptions=False)
        r = client.get("/api/chat/models?provider=claude")

    assert r.status_code == 200
    body = r.json()
    current = body.get("current")
    # current must be one of the cloud models, never a local ollama id
    assert current in fake_models or current is None, (
        f"F-CLOUD-CURRENT: current={current!r} is not a cloud model id"
    )
    # Explicitly: must not be a local model (this is the original bug)
    local_ids = {"qwen2.5:7b", "ai-eng:latest", "qwen3:8b", "mistral:7b"}
    assert current not in local_ids, (
        f"F-CLOUD-CURRENT: current={current!r} is a LOCAL model id under a cloud provider"
    )


def test_cloud_branch_gallery_shape_preserved(monkeypatch):
    """Cloud branch response must always have gallery with installed/catalog/runtime_counts."""
    monkeypatch.setenv("LAB_MODE", "hybrid")

    from arail.portal import app as portal_app
    from fastapi.testclient import TestClient

    with patch.object(portal_app, "_provider_token", return_value=""):
        client = TestClient(portal_app.app, raise_server_exceptions=False)
        r = client.get("/api/chat/models?provider=claude")

    body = r.json()
    gallery = body.get("gallery", {})
    for key in ("installed", "catalog", "runtime_counts"):
        assert key in gallery, f"gallery missing {key!r}: {gallery}"
