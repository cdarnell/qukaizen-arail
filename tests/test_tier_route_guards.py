"""WP3: tier is a real access boundary, not just nav visibility.

A minimalist lab must 404 on maximus-only routes even when the URL is typed
directly — previously the nav hid them but the pages/endpoints still served,
exposing code-execution surfaces (terminal, notebooks, plugins) and the admin
panel. 404 (not 403) so route existence isn't disclosed.
"""

from __future__ import annotations

import pytest


MAXIMUS_ONLY_GETS = [
    "/terminal", "/notebook", "/notebooks", "/marimo",
    "/plugins", "/admin", "/tuning",
]
MAXIMUS_ONLY_POSTS = [
    ("/api/notebook/start", {}),
    ("/api/marimo/start", {}),
    ("/api/plugins/install", {"github_url": "https://github.com/x/y",
                              "confirm_code_execution": True}),
]


@pytest.fixture
def client(monkeypatch, tmp_path):
    from arail.registry import core as reg_core
    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE",
                       str(tmp_path / "model_registry.json"))
    reg_core.reset_registry()
    from fastapi.testclient import TestClient
    import arail.portal.app as app_mod
    with TestClient(app_mod.app) as c:
        yield c
    reg_core.reset_registry()


def test_minimalist_404s_on_maximus_routes(client, monkeypatch):
    monkeypatch.setenv("LAB_TIER", "minimalist")
    for path in MAXIMUS_ONLY_GETS:
        r = client.get(path)
        assert r.status_code == 404, f"{path} must 404 on minimalist, got {r.status_code}"
    for path, body in MAXIMUS_ONLY_POSTS:
        r = client.post(path, json=body)
        assert r.status_code == 404, f"{path} must 404 on minimalist, got {r.status_code}"


def test_maximus_serves_maximus_routes(client, monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    for path in MAXIMUS_ONLY_GETS:
        r = client.get(path)
        assert r.status_code != 404, f"{path} must serve on maximus, got 404"


def test_build_redirects_to_forge_on_every_tier(client, monkeypatch):
    """/build is retired (sprints/2026-09-23-nucleus-sprint-1 ARCHITECTURE.md
    §8): it is a permanent 308 redirect to /forge, not a tier-gated route, so
    it is deliberately absent from MAXIMUS_ONLY_GETS above (T-FORGE-4)."""
    for tier in ("minimalist", "maximus"):
        monkeypatch.setenv("LAB_TIER", tier)
        r = client.get("/build", follow_redirects=False)
        assert r.status_code == 308, f"/build on {tier} must 308, got {r.status_code}"
        assert r.headers["location"] == "/forge"


def test_plugin_install_requires_confirmation(client, monkeypatch):
    """On maximus, installing without the explicit code-exec confirmation is
    refused (no git clone / pip install fires)."""
    monkeypatch.setenv("LAB_TIER", "maximus")
    r = client.post("/api/plugins/install",
                    json={"github_url": "https://github.com/x/y"})
    assert r.status_code == 200
    assert r.json().get("error") == "confirmation_required"
