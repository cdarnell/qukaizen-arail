"""Portal auth over the whole route table (S4 step 3; ARCHITECTURE.md C2/C3, F2, F14).

Every test here exercises the real middleware: ``real_auth`` opts out of the conftest bearer.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from arail import config
from arail.portal import app as portal_app
from arail.portal import auth
from arail.portal.route_table import flatten, sample_path

pytestmark = pytest.mark.real_auth

PASS = "test-passphrase-not-real"
SKIP_METHODS = {"HEAD", "OPTIONS"}


@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(auth, "_stores", {})
    monkeypatch.setenv("ARAIL_PASSWORD", PASS)
    auth.ensure_token()
    tok = (tmp_path / "data" / "portal.token").read_text().strip()
    return {"token": tok, "bearer": {"Authorization": f"Bearer {tok}"}}


@pytest.fixture
def client(lab):
    return TestClient(portal_app.app, follow_redirects=False, raise_server_exceptions=False)


def _requests():
    """(method, concrete path) for every route and mount in the flattened table."""
    seen = set()
    for e in flatten(portal_app.app):
        if e.kind == "route":
            for m in sorted(e.methods - SKIP_METHODS):
                seen.add((m, sample_path(e.path)))
        elif e.kind == "mount":
            seen.add(("GET", e.path + "/x"))
            seen.add(("GET", e.path + "/"))
    return sorted(seen)


def _exempt(method: str, path: str) -> bool:
    return auth.is_exempt(path, method) or path.startswith(auth.EDGE_PREFIX)


def test_route_table_is_big_enough_to_mean_something():
    table = flatten(portal_app.app)
    api = [e for e in table if e.kind == "route" and e.path.startswith("/api")]
    assert len(api) >= 175, f"only {len(api)} /api routes; the walk is missing routers"
    assert not [e for e in table if e.kind == "websocket"], "A2: a WebSocket route needs a pure-ASGI auth layer"


@pytest.mark.parametrize("variant", ["none", "bad_bearer", "bhc1_bearer", "unknown_session"])
def test_no_unlisted_route_answers_without_a_credential(client, variant):
    """Count of non-exempt 2xx/3xx-other/4xx-other responses must be 0: any 2xx means a local
    process can read or change the lab without the passphrase."""
    headers, cookies = {}, {}
    if variant == "bad_bearer":
        headers = {"Authorization": "Bearer arl1_" + "B" * 43}
    elif variant == "bhc1_bearer":
        headers = {"Authorization": "Bearer bhc1_" + "C" * 43}
    elif variant == "unknown_session":
        cookies = {auth.cookie_name(): "A" * 43}
    bad = []
    for method, path in _requests():
        if _exempt(method, path):
            continue
        r = client.request(method, path, headers=headers, cookies=cookies)
        if r.status_code not in (401, 302):
            bad.append((method, path, r.status_code))
    assert not bad, f"{len(bad)} routes answered an unauthenticated request: {bad[:10]}"


def test_exempt_routes_are_routes_that_exist():
    paths = {e.path for e in flatten(portal_app.app) if e.kind == "route"}
    for p in auth.EXEMPT_EXACT:
        assert p in paths or p == "/favicon.ico", p   # favicon is served outside the table


def test_bearer_opens_the_same_routes(client, lab):
    assert client.get("/api/jobs/state", headers=lab["bearer"]).status_code == 200
    assert client.get("/api/jobs/state").status_code == 401


def test_exempt_anonymous_routes_still_answer(client):
    for p in ("/health", "/healthz", "/api/system/health", "/api/instance", "/favicon.ico"):
        assert client.get(p).status_code != 401, p
    assert client.get("/api/system/health/stream").status_code == 401
    assert client.get("/api/instances").status_code == 401


def test_metrics_need_credentials_once_onboarded(client, lab):
    for p in ("/metrics", "/api/system/metrics"):
        assert client.get(p).status_code == 401
        assert client.get(p, headers=lab["bearer"]).status_code == 200


def test_untrusted_host_is_403_before_auth_and_trusted_host_is_401(client, monkeypatch):
    """C3: Host/CSRF reject first. A rebinding request must not cost a session lookup."""
    assert client.get("/api/jobs/state", headers={"Host": "evil.example"}).status_code == 403
    assert client.get("/api/jobs/state").status_code == 401


def test_page_without_credential_redirects_to_login(client):
    r = client.get("/research", headers={"Accept": "text/html"})
    assert r.status_code == 302 and r.headers["location"] == "/login?next=/research"


def test_api_401_is_json_with_login_hint(client):
    r = client.get("/api/jobs/state")
    assert r.status_code == 401
    assert r.json() == {"error": "auth_required", "login": "/login"}
    assert r.headers["www-authenticate"].startswith("Bearer")


def test_token_in_the_query_string_is_never_read(client, lab):
    """F2: a URL is logged by servers and kept in browser history."""
    for q in ("token", "access_token"):
        assert client.get(f"/api/jobs/state?{q}={lab['token']}").status_code == 401


def test_wrong_bearer_is_401_auth_invalid_and_does_not_fall_back(client, lab):
    r = client.get("/api/jobs/state", headers={"Authorization": "Bearer arl1_" + "Z" * 43})
    assert r.status_code == 401 and r.json()["error"] == "auth_invalid"


def test_edge_prefix_defers_to_edge_admission_not_portal_credentials(client, lab):
    """A portal bearer on /api/host/v1/* does NOT admit: admission still needs bhc1_."""
    r = client.post("/api/host/v1/handshake", headers=lab["bearer"], json={})
    # The TestClient peer is not loopback, so admission refuses at placement (403); either way the
    # refusal is the edge's own code, never the portal's, and nothing is admitted.
    assert r.status_code in (401, 403)
    assert r.json().get("error") not in ("auth_required", "auth_invalid")


def test_openai_shim_is_bearer_only(client, lab):
    assert client.get("/api/openai/v1/models").status_code == 401
    assert client.get("/api/openai/v1/models", headers=lab["bearer"]).status_code == 200
    client.cookies.set(auth.cookie_name(), "A" * 43)
    assert client.get("/api/openai/v1/models").status_code == 401


def test_not_onboarded_lab_keeps_todays_behaviour_and_mints_no_token(tmp_path, monkeypatch):
    """F13: with no passphrase every non-onboarding route is 401 lab_not_onboarded / 302 /welcome."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setenv("ARAIL_PASSWORD", "")
    monkeypatch.setenv("ARAIL_ENV_FILE", str(tmp_path / "empty.env"))
    c = TestClient(portal_app.app, follow_redirects=False, raise_server_exceptions=False)
    assert c.get("/api/jobs/state").json()["error"] == "lab_not_onboarded"
    assert c.get("/research", headers={"Accept": "text/html"}).headers["location"] == "/welcome"
    assert not (tmp_path / "data" / "portal.token").exists()


def test_welcome_is_closed_once_a_passphrase_exists(client):
    """Addendum A.4: /welcome is open only before onboarding; afterwards it is a re-onboarding surface."""
    r = client.get("/welcome", headers={"Accept": "text/html"})
    assert r.status_code == 302 and r.headers["location"].startswith("/login")
    assert client.post("/api/welcome/setup", json={"passphrase": "x" * 9, "confirm": "x" * 9}).status_code == 401
