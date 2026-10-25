"""Browser login, sessions, CSRF with cookie auth, onboarding hand-off (S4 step 4).

ARCHITECTURE.md C4/C5/C6; failure modes F1, F4-F7, F9, F11, F13, F20.
"""
from __future__ import annotations

import logging
import os
import stat

import pytest
from fastapi.testclient import TestClient

from arail import config
from arail.portal import app as portal_app
from arail.portal import auth

pytestmark = pytest.mark.real_auth

PASS = "test-passphrase-not-real"


@pytest.fixture
def lab(monkeypatch):
    monkeypatch.setenv("ARAIL_PASSWORD", PASS)
    monkeypatch.setenv("PORTAL_PORT", "8080")
    auth.ensure_token()


def mk():
    return TestClient(portal_app.app, follow_redirects=False, raise_server_exceptions=False)


def login(c, passphrase=PASS, **kw):
    return c.post("/login", data={"passphrase": passphrase, **kw})


def test_login_sets_a_strict_httponly_cookie_and_opens_the_dashboard(lab):
    c = mk()
    r = login(c)
    assert r.status_code == 303 and r.headers["location"] == "/"
    sc = r.headers["set-cookie"]
    assert sc.startswith("arail_sid_8080=")
    assert "HttpOnly" in sc and "SameSite=strict" in sc and "Path=/" in sc and "Max-Age=2592000" in sc
    assert "Secure" not in sc
    home = c.get("/", headers={"Accept": "text/html"})
    # "/" is 200, or the existing one-shot World nudge; never the login page.
    assert home.status_code == 200 or home.headers["location"] == "/welcome?step=world"
    assert c.get("/research", headers={"Accept": "text/html"}).status_code == 200
    assert c.get("/api/auth/whoami").json() == {"principal": "session"}


def test_pages_carry_the_recovery_hook_and_the_logout_form(lab):
    c = mk()
    login(c)
    html = c.get("/research", headers={"Accept": "text/html"}).text
    assert "/api/auth/whoami" in html and 'action="/logout"' in html


def test_cookie_is_secure_when_served_over_https(lab):
    c = TestClient(portal_app.app, base_url="https://testserver", follow_redirects=False)
    assert "Secure" in login(c).headers["set-cookie"]


def test_wrong_passphrase_is_401_with_one_fixed_message_and_no_cookie(lab, caplog):
    c = mk()
    with caplog.at_level(logging.DEBUG):
        r = login(c, "not-the-one-123")
    assert r.status_code == 401 and "did not match" in r.text and "set-cookie" not in r.headers
    assert "not-the-one-123" not in r.text + caplog.text
    ev = [e for e in portal_app.activity_log.recent(20) if e.get("source") == "security"]
    assert ev and "not-the-one-123" not in str(ev)


def test_session_fixation_a_planted_cookie_never_authenticates(lab):
    c = mk()
    c.cookies.set("arail_sid_8080", "P" * 43)
    r = login(c)
    new = r.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    assert new != "P" * 43
    assert not auth.get_store().touch("P" * 43)


def test_sixth_failure_is_429_and_a_correct_passphrase_is_not_evaluated(lab):
    c = mk()
    for _ in range(5):
        assert login(c, "wrong-wrong-1").status_code == 401
    r = login(c, PASS)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1 and "set-cookie" not in r.headers
    auth.limiter.not_before = 0.0          # time passes
    assert login(c, PASS).status_code == 303
    assert auth.limiter.fails == 0         # success resets


@pytest.mark.parametrize("bad", ["//evil.com", "/\\evil.com", "https://evil.com", "/%2F%2Fevil.com"])
def test_login_next_cannot_redirect_off_site(lab, bad):
    r = login(mk(), next=bad)
    assert r.headers["location"] == "/"


def test_login_next_keeps_a_local_path(lab):
    assert login(mk(), next="/research").headers["location"] == "/research"


def test_logout_revokes_the_session_server_side(lab):
    c = mk()
    login(c)
    sid = c.cookies.get("arail_sid_8080")
    r = c.post("/logout", headers={"Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert not auth.get_store().touch(sid)
    c2 = mk()
    c2.cookies.set("arail_sid_8080", sid)
    assert c2.get("/api/jobs/state").status_code == 401


def test_expired_session_goes_back_to_login(lab):
    c = mk()
    login(c)
    auth.get_store().force_expire(c.cookies.get("arail_sid_8080"))
    assert c.get("/research", headers={"Accept": "text/html"}).status_code == 302
    assert c.get("/api/jobs/state").status_code == 401


def test_cookie_name_is_port_qualified_so_labs_do_not_overwrite_each_other(lab, monkeypatch):
    monkeypatch.setenv("PORTAL_PORT", "8090")
    assert auth.cookie_name() == "arail_sid_8090"


def test_another_labs_session_is_rejected(lab, tmp_path, monkeypatch):
    """F7/F11: sessions and tokens are per DATA_DIR."""
    c = mk()
    login(c)
    sid = c.cookies.get("arail_sid_8080")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "instance-b")
    monkeypatch.setattr(auth, "_stores", {})
    c2 = mk()
    c2.cookies.set("arail_sid_8080", sid)
    assert c2.get("/api/jobs/state").status_code == 401


# --- CSRF with cookie auth (F4, F5) -------------------------------------------------------------
@pytest.mark.parametrize("headers,why", [
    ({"Sec-Fetch-Site": "cross-site"}, "cross-site"),
    ({"Origin": "http://evil.example"}, "foreign Origin"),
    ({}, "neither header"),
    ({"Sec-Fetch-Site": "same-site"}, "same-site (another local port)"),
    ({"Sec-Fetch-Site": "same-site", "Origin": "http://127.0.0.1:9999"}, "another port's page"),
])
def test_session_mutations_need_same_origin(lab, headers, why):
    c = mk()
    login(c)
    r = c.post("/api/jobs/halt", headers=headers)
    assert r.status_code == 403, why


def test_session_mutation_with_same_origin_passes(lab):
    c = mk()
    login(c)
    assert c.post("/api/jobs/resume", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert c.post("/api/jobs/resume", headers={"Origin": "http://testserver"}).status_code == 200


def test_bearer_mutation_needs_no_origin(lab):
    tok = auth.token_path().read_text().strip()
    assert mk().post("/api/jobs/resume", headers={"Authorization": f"Bearer {tok}"}).status_code == 200


def test_airgap_flip_without_origin_or_credential_is_401(lab):
    assert mk().post("/api/airgap/toggle", json={}).status_code == 401


# --- onboarding -------------------------------------------------------------------------------
@pytest.fixture
def fresh(monkeypatch, tmp_path):
    monkeypatch.delenv("ARAIL_PASSWORD", raising=False)
    monkeypatch.delenv("OPEN_NOTEBOOK_ENCRYPTION_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARAIL_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PORTAL_PORT", "8080")
    return tmp_path


def test_setup_logs_the_browser_in_and_mints_a_0600_token(fresh):
    c = mk()
    r = c.post("/api/welcome/setup", json={"passphrase": "brand-new-pass-1", "confirm": "brand-new-pass-1"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert "arail_sid_8080=" in r.headers["set-cookie"] and "HttpOnly" in r.headers["set-cookie"]
    tp = auth.token_path()
    assert stat.S_IMODE(os.stat(tp).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(tp.parent).st_mode) == 0o700
    assert c.get("/", headers={"Accept": "text/html"}).status_code in (200, 302)
    assert c.get("/api/auth/whoami").json() == {"principal": "session"}


def test_setup_refuses_a_lan_peer_when_bound_beyond_loopback(fresh, monkeypatch):
    monkeypatch.setenv("BIND_ADDR", "0.0.0.0")
    r = mk().post("/api/welcome/setup", json={"passphrase": "brand-new-pass-1", "confirm": "brand-new-pass-1"})
    assert r.status_code == 403 and r.json()["error"] == "setup_local_only"
    assert not (fresh / ".env").exists()


def test_login_before_onboarding_goes_to_welcome(fresh):
    assert mk().get("/login").headers["location"] == "/welcome"


def test_login_page_names_the_reset_command(lab):
    r = mk().get("/login")
    assert r.status_code == 200 and "./arailctl auth reset" in r.text
    assert r.text.count('id="ui-theme-vars"') == 1


def test_already_logged_in_login_page_redirects(lab):
    c = mk()
    login(c)
    assert c.get("/login?next=/research").headers["location"] == "/research"


# --- F1: the token never reaches a log ------------------------------------------------------------
def test_redactor_masks_the_portal_token_shape():
    from arail import redact
    tok = "arl1_" + "Qx9-" * 10 + "abc"
    assert tok not in redact.capture_body(prompt=f"curl with {tok}", response="")["prompt"]


def test_token_never_appears_in_logs_or_activity(lab, caplog):
    tok = auth.token_path().read_text().strip()
    c = mk()
    with caplog.at_level(logging.DEBUG):
        c.get("/api/jobs/state", headers={"Authorization": f"Bearer {tok}"})
        c.get("/api/jobs/state", headers={"Authorization": "Bearer " + tok[:-1] + "x"})
        c.get(f"/api/jobs/state?token={tok}")
    # httpx logs the TEST CLIENT's own request URL; the server-side loggers are what matter here.
    server_side = "\n".join(r.getMessage() for r in caplog.records if r.name != "httpx")
    assert tok not in server_side
    assert tok not in str(portal_app.activity_log.recent(200))
