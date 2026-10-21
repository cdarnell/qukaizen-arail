"""WK-8: AeroLLM as a Chat Compute Source.

AeroLLM is ARAIL's sibling inference engine, already wired as an in-process
deep backend (AeroLLMBackend). This surfaces it in the Chat Compute Source
pivot as a LOCAL source — no token, allowed in airgapped mode (it never
touches the network), install-gated so there's never a dead option, and
routed to backend_override='aerollm' by the existing chat plumbing.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import arail.portal.app as appmod
from arail.portal.app import app

CSRF = {"sec-fetch-site": "same-origin"}


@pytest.fixture()
def installed(monkeypatch):
    monkeypatch.setattr(appmod, "_is_aerollm_installed", lambda: True)


@pytest.fixture()
def not_installed(monkeypatch):
    monkeypatch.setattr(appmod, "_is_aerollm_installed", lambda: False)


def test_aerollm_is_a_local_source_not_cloud():
    assert "aerollm" in appmod._LOCAL_COMPUTE_SOURCES
    assert "aerollm" not in appmod._CLOUD_PROVIDERS


def test_display_name():
    assert appmod._display_provider_name("aerollm") == "QueueLLM"


def test_active_provider_accepts_aerollm(monkeypatch):
    monkeypatch.setenv("COMPUTE_SOURCE", "aerollm")
    assert appmod._load_active_provider() == "aerollm"
    monkeypatch.setenv("COMPUTE_SOURCE", "bogus")
    assert appmod._load_active_provider() == "my_machine"


def test_pivot_lists_aerollm_only_when_built(installed):
    ids = [s["id"] for s in appmod._compact_compute_sources("my_machine")]
    assert "aerollm" in ids
    # positioned right after my_machine (a local sibling, before the cloud row)
    assert ids.index("aerollm") == ids.index("my_machine") + 1


def test_pivot_hides_aerollm_when_not_built(not_installed):
    ids = [s["id"] for s in appmod._compact_compute_sources("my_machine")]
    assert "aerollm" not in ids


def test_select_aerollm_allowed_airgapped_when_built(installed, monkeypatch):
    monkeypatch.setenv("LAB_MODE", "airgapped")
    with TestClient(app) as c:
        r = c.post("/api/providers/active", json={"provider": "aerollm"}, headers=CSRF)
        assert r.status_code == 200
        assert r.json().get("ok") is True
        assert r.json().get("provider") == "aerollm"


def test_select_aerollm_refused_when_not_built(not_installed, monkeypatch):
    monkeypatch.setenv("LAB_MODE", "airgapped")
    with TestClient(app) as c:
        r = c.post("/api/providers/active", json={"provider": "aerollm"}, headers=CSRF)
        assert r.json().get("ok") is False
        assert "deep rebuild" in r.json().get("error", "")


def test_cloud_still_blocked_airgapped(monkeypatch):
    monkeypatch.setenv("LAB_MODE", "airgapped")
    with TestClient(app) as c:
        r = c.post("/api/providers/active", json={"provider": "claude"}, headers=CSRF)
        assert r.json().get("ok") is False


def test_status_reports_aerollm_availability(installed):
    with TestClient(app) as c:
        data = c.get("/api/providers/status").json()
        assert data["available"].get("aerollm") is True
