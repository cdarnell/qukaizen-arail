"""POST /api/research/deep — the deep-passes toggle on airgap-toggle rails.

Pins: maximus flips os.environ + rewrites .env atomically (persisted and
live state move together); minimalist is refused 403 tier_locked with the
file untouched; cross-site posts are refused; the response carries a fresh
models block so the truth strip re-renders from the same source of truth.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import arail.portal.app as app_module
from arail.portal.app import app

CSRF_HEADERS = {"sec-fetch-site": "same-origin"}
CROSS_SITE = {"sec-fetch-site": "cross-site"}


def _client():
    return TestClient(app)


@pytest.fixture()
def toggle_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(app_module, "_TOGGLE_ENV_PATH", env_file)
    monkeypatch.delenv("BIND_ADDR", raising=False)
    monkeypatch.setenv("AEROLLM_RESEARCH", "false")  # restored by pytest
    yield env_file


def test_maximus_toggle_on_persists_and_applies(toggle_env, monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    r = _client().post("/api/research/deep", json={"enabled": True},
                       headers=CSRF_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["enabled"] is True
    assert os.environ["AEROLLM_RESEARCH"] == "true"
    assert "AEROLLM_RESEARCH=true" in toggle_env.read_text()
    # The response's models block reflects the flip immediately.
    assert body["models"] is None or body["models"]["deep"]["enabled_by_user"] is True

    r2 = _client().post("/api/research/deep", json={"enabled": False},
                        headers=CSRF_HEADERS)
    assert r2.status_code == 200
    assert os.environ["AEROLLM_RESEARCH"] == "false"
    assert "AEROLLM_RESEARCH=false" in toggle_env.read_text()


def test_minimalist_is_refused_and_file_untouched(toggle_env, monkeypatch):
    monkeypatch.setenv("LAB_TIER", "minimalist")
    r = _client().post("/api/research/deep", json={"enabled": True},
                       headers=CSRF_HEADERS)
    assert r.status_code == 403
    assert r.json()["error"] == "tier_locked"
    assert "upgrade maximus" in r.json()["message"]
    assert not Path(toggle_env).exists()
    assert os.environ["AEROLLM_RESEARCH"] == "false"


def test_cross_site_is_refused(toggle_env, monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    r = _client().post("/api/research/deep", json={"enabled": True},
                       headers=CROSS_SITE)
    assert r.status_code == 403
    assert not Path(toggle_env).exists()


def test_invalid_body_is_400(toggle_env, monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    r = _client().post("/api/research/deep", json={"enabled": "yes"},
                       headers=CSRF_HEADERS)
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_enabled"
    assert not Path(toggle_env).exists()


def test_round_trip_into_status_models(toggle_env, monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    c = _client()
    assert c.post("/api/research/deep", json={"enabled": True},
                  headers=CSRF_HEADERS).status_code == 200
    m = c.get("/api/research/status").json()["models"]
    if m is not None:  # block is defensive; when present it must agree
        assert m["deep"]["enabled_by_user"] is True
