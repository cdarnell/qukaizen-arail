"""F13 — the legacy-bodies admin endpoints 404 on minimalist, and no GET
mutates state. Parameterised over the endpoint list so a future endpoint
added without the gate fails loudly.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from arail import activity, config
from arail.portal.app import app


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setattr(activity, "LOG_FILE", tmp_path / "activity.jsonl")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    yield


def _client() -> TestClient:
    return TestClient(app)


_ENDPOINTS = [
    ("get", "/api/admin/legacy-bodies"),
    ("post", "/api/admin/legacy-bodies/purge"),
    ("post", "/api/admin/legacy-bodies/dismiss"),
]


@pytest.mark.parametrize("method,path", _ENDPOINTS)
def test_404_on_minimalist(monkeypatch, method, path):
    monkeypatch.setenv("LAB_TIER", "minimalist")
    client = _client()
    resp = getattr(client, method)(path)
    assert resp.status_code == 404


@pytest.mark.parametrize("method,path", _ENDPOINTS)
def test_reachable_on_maximus(monkeypatch, method, path):
    monkeypatch.setenv("LAB_TIER", "maximus")
    client = _client()
    resp = getattr(client, method)(path)
    assert resp.status_code == 200


def test_get_status_never_mutates(monkeypatch, tmp_path):
    monkeypatch.setenv("LAB_TIER", "maximus")
    legacy = {
        "ts": "2026-01-01T00:00:00Z", "source": "researcher", "level": "info",
        "message": "x", "data": {"prompt_trace": {"prompt": "p", "response": "r"}},
    }
    import json
    path = tmp_path / "activity.jsonl"
    path.write_text(json.dumps(legacy) + "\n")
    before = path.read_text()

    client = _client()
    r = client.get("/api/admin/legacy-bodies")
    assert r.status_code == 200
    assert r.json()["count"] == 1
    assert path.read_text() == before  # GET never mutates


def test_purge_endpoint_actually_purges(monkeypatch, tmp_path):
    monkeypatch.setenv("LAB_TIER", "maximus")
    legacy = {
        "ts": "2026-01-01T00:00:00Z", "source": "researcher", "level": "info",
        "message": "x", "data": {"prompt_trace": {"prompt": "p", "response": "r"}},
    }
    import json
    path = tmp_path / "activity.jsonl"
    path.write_text(json.dumps(legacy) + "\n")

    client = _client()
    r = client.post("/api/admin/legacy-bodies/purge")
    assert r.status_code == 200
    assert r.json()["purged"] == 1
    written = json.loads(path.read_text().strip())
    trace = written["data"]["prompt_trace"]
    assert "prompt" not in trace
    assert "response" not in trace
    assert trace["body_purged"] is True


def test_dismiss_endpoint_is_remembered(monkeypatch, tmp_path):
    monkeypatch.setenv("LAB_TIER", "maximus")
    client = _client()
    r = client.post("/api/admin/legacy-bodies/dismiss")
    assert r.status_code == 200
    assert r.json() == {"dismissed": True}
    assert activity.legacy_notice_dismissed() is True
