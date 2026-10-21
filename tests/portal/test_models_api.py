"""Portal API for the model registry: state, bind, resolve, register-artifact."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    from arail.registry import core as reg_core

    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE",
                       str(tmp_path / "model_registry.json"))
    monkeypatch.setenv("MODEL_BACKEND", "ollama_native")
    monkeypatch.setenv("MODEL_NAME", "ai-engineer:latest")
    monkeypatch.delenv("MODEL_API_BASE", raising=False)
    monkeypatch.setenv("AEROLLM_MODEL", "gpt-oss-20b-MLX-4bit")
    monkeypatch.setenv("LAB_MODE", "airgapped")
    reg_core.reset_registry()

    import arail.portal.app as app_mod
    with TestClient(app_mod.app) as c:
        yield c
    reg_core.reset_registry()


def test_state_shape(client):
    r = client.get("/api/models/state")
    assert r.status_code == 200
    s = r.json()
    assert {"entries", "bindings", "tab_overrides", "recent_events",
            "config_version", "statusbar", "profiles"} <= set(s)
    ids = {e["id"] for e in s["entries"]}
    assert {"tier0-local", "tier1-aerollm"} <= ids
    # Statusbar names both tiers, resident first.
    assert "(resident)" not in s["statusbar"] or "ai-engineer" in s["statusbar"]
    # Cloud entries visible with a health payload, even airgapped.
    cloud = next(e for e in s["entries"] if e["id"] == "cloud-anthropic")
    assert "health" in cloud


def test_bind_and_clear_roundtrip(client):
    r = client.post("/api/models/bind",
                    json={"profile": "reasoning", "entry_id": "tier0-local"})
    assert r.status_code == 200
    assert r.json()["bindings"]["reasoning"] == "tier0-local"

    r = client.post("/api/models/bind",
                    json={"profile": "*", "entry_id": "tier1-aerollm",
                          "tab": "agents"})
    assert r.json()["tab_overrides"]["agents"]["*"] == "tier1-aerollm"

    r = client.post("/api/models/bind",
                    json={"profile": "*", "entry_id": None, "tab": "agents"})
    assert "agents" not in r.json()["tab_overrides"]


def test_bind_rejects_unknown(client):
    assert client.post("/api/models/bind",
                       json={"profile": "reasoning",
                             "entry_id": "nope"}).status_code == 400
    assert client.post("/api/models/bind",
                       json={"profile": "warp",
                             "entry_id": "tier0-local"}).status_code == 400


def test_resolve_endpoint(client):
    r = client.get("/api/models/resolve",
                   params={"profile": "fast", "tab": "research"})
    assert r.status_code == 200
    body = r.json()
    assert body["entry"]["id"] == "tier0-local"
    assert body["fallback"] is None
    assert client.get("/api/models/resolve",
                      params={"profile": "bogus"}).status_code == 400


def test_register_artifact_creates_entry(client):
    r = client.post("/api/models/register-artifact",
                    json={"run_id": "qkz-test-1",
                          "name": "qkz-super-3b",
                          "gguf_path": "/tmp/fake/superskill-q4.gguf"})
    assert r.status_code == 200
    body = r.json()
    assert body["entry"]["id"] == "qkz-super-3b"
    assert body["entry"]["source"] == "artifact"
    assert "ollama create qkz-super-3b" in body["install_hint"]
    # Now selectable (present in state) for every tab's switcher.
    state = client.get("/api/models/state").json()
    assert any(e["id"] == "qkz-super-3b" for e in state["entries"])


def test_register_artifact_requires_gguf_path(client):
    # T-REG-4: gguf_path is required now that the /build NucleusClient
    # graduation-lookup fallback is retired (sprints/2026-09-23-nucleus-sprint-1).
    r = client.post("/api/models/register-artifact", json={"run_id": "qkz-test-2"})
    assert r.status_code == 422


def test_register_artifact_no_longer_imports_arail_build():
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src" / "arail" / "portal" / "models_api.py"
    tree = ast.parse(src.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    assert not any(n.startswith("arail.build") for n in names)


def test_health_refresh_probes_without_constructing_aerollm(client, monkeypatch):
    from arail.router.backends import AeroLLMBackend
    constructed = []
    orig_new = AeroLLMBackend.__new__

    def _spy_new(cls, *a, **k):
        constructed.append(1)
        return orig_new(cls)

    monkeypatch.setattr(AeroLLMBackend, "__new__", _spy_new)
    r = client.post("/api/models/health/refresh")
    assert r.status_code == 200
    assert constructed == []          # R5: probes never build the runtime
    tier1 = next(e for e in r.json()["entries"] if e["id"] == "tier1-aerollm")
    assert tier1["health"]["status"] in (
        "unhealthy", "not_installed", "cold", "healthy")
