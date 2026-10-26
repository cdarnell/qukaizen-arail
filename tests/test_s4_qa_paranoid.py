"""QA pass (sprint 2026-10-25-buddy-s4-gateway-in-box): cases the build's own tests did not cover.

Passing tests here are regression guards. The three ``xfail(strict=True)`` tests are QA defects
filed in TEST_REPORT.md (QA-S4-1..3); each flips to XPASS (and fails the run, so the marker gets
removed) when the builder fixes it.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from arail.portal import app as portal_app
from arail.portal import auth
from arail.portal.route_table import flatten

pytestmark = pytest.mark.real_auth

PASS = "qa-paranoid-passphrase"
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def lab(monkeypatch):
    monkeypatch.setenv("ARAIL_PASSWORD", PASS)
    monkeypatch.setenv("PORTAL_PORT", "8080")
    auth.ensure_token()


def mk():
    return TestClient(portal_app.app, follow_redirects=False, raise_server_exceptions=False)


def _login(c, passphrase=PASS, cookies=None):
    return c.post("/login", data={"passphrase": passphrase}, cookies=cookies or {})


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "qa", path)


# ----------------------------------------------------------------- sessions
def test_a_stolen_cookie_replayed_after_logout_is_refused(lab):
    c = mk()
    _login(c)
    sid = c.cookies.get(auth.cookie_name())
    assert c.get("/api/jobs/state").status_code == 200
    c.post("/logout", headers={"Sec-Fetch-Site": "same-origin"})
    thief = mk()
    r = thief.get("/api/jobs/state", cookies={auth.cookie_name(): sid})
    assert r.status_code == 401
    assert thief.get("/research", cookies={auth.cookie_name(): sid}, headers={"Accept": "text/html"}).status_code == 302


def test_absolute_expiry_ends_a_session_that_was_kept_alive_every_day(tmp_path, monkeypatch):
    store = auth.SessionStore(tmp_path / "s.json")
    sid = store.create()
    t0 = time.time()
    for day in (20, 40, 60, 80, 89):
        monkeypatch.setattr(auth.time, "time", lambda d=day: t0 + d * 86400)
        assert store.touch(sid), day
    monkeypatch.setattr(auth.time, "time", lambda: t0 + 90 * 86400 + 1)
    assert not store.touch(sid)


def test_cross_site_logout_is_refused_and_the_session_survives(lab):
    c = mk()
    _login(c)
    r = c.post("/logout", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert c.get("/api/jobs/state").status_code == 200


# ----------------------------------------------------------------- CSRF over every mutating route
_MUT = [(e.path, m) for e in flatten(portal_app.app) if e.kind == "route"
        for m in sorted(e.methods) if m in ("POST", "PUT", "PATCH", "DELETE")
        and not e.path.startswith(("/api/host/v1", "/api/openai/v1")) and e.path not in ("/login",)]


def test_the_csrf_sweep_covers_put_patch_and_delete():
    methods = {m for _, m in _MUT}
    assert {"POST", "PUT", "PATCH", "DELETE"} <= methods and len(_MUT) >= 100


@pytest.mark.parametrize("proof", [{}, {"Sec-Fetch-Site": "same-site"}, {"Sec-Fetch-Site": "cross-site"},
                                   {"Origin": "null"}, {"Origin": "http://127.0.0.1:9999"}],
                         ids=["no-headers", "same-site", "cross-site", "origin-null", "origin-other-port"])
def test_every_session_mutation_without_same_origin_proof_is_403(lab, proof):
    c = mk()
    _login(c)
    leaks = []
    for path, m in _MUT:
        r = c.request(m, _concrete(path), headers=proof, json={})
        if r.status_code != 403:
            leaks.append(f"{m} {path} -> {r.status_code}")
    assert not leaks, leaks[:10]


# ----------------------------------------------------------------- login flood
def test_fifty_wrong_guesses_from_unknown_clients_evaluate_at_most_five(lab):
    evaluated = 0
    for _ in range(50):
        r = mk().post("/login", json={"passphrase": "wrong-" + secrets.token_hex(3)})
        evaluated += r.status_code == 401
        assert r.status_code in (401, 429)
    assert evaluated <= 5
    assert mk().post("/login", json={"passphrase": PASS}).status_code == 429   # not evaluated, even when right


def test_fifty_forged_device_cookies_buy_no_guesses(lab):
    for _ in range(6):
        mk().post("/login", json={"passphrase": "wrong-guess-xx"})
    name = auth.device_cookie_name()
    codes = {mk().post("/login", json={"passphrase": "wrong-guess-xx"},
                       cookies={name: secrets.token_urlsafe(32)}).status_code for _ in range(50)}
    assert codes == {429}


# ----------------------------------------------------------------- onboarding cannot be re-opened
@pytest.mark.parametrize("path", ["/api/welcome/setup", "/api/welcome/setup/", "/api//welcome/setup",
                                  "/API/welcome/setup", "/api/welcome/./setup"])
def test_welcome_setup_cannot_be_retriggered_anonymously(lab, path):
    r = mk().post(path, json={"passphrase": "attacker-pass-123", "confirm": "attacker-pass-123"})
    assert r.status_code in (401, 302, 404, 405)
    assert os.environ["ARAIL_PASSWORD"] == PASS


def test_welcome_setup_with_a_real_credential_still_refuses_to_overwrite(lab):
    tok = auth.token_path().read_text().strip()
    r = mk().post("/api/welcome/setup", headers={"Authorization": "Bearer " + tok},
                  json={"passphrase": "attacker-pass-123", "confirm": "attacker-pass-123"})
    assert r.status_code == 409


# ----------------------------------------------------------------- edge listener
def test_edge_rejects_a_wellformed_wrong_credential_and_the_portal_bearer():
    from arail.portal.edge_app import edge_app
    c = TestClient(edge_app, base_url="http://127.0.0.1:8089")
    for bearer in ("bhc1_" + "Z" * 43, "arl1_" + "Z" * 43, "arl1s_" + "Z" * 43):
        r = c.post("/api/host/v1/handshake", headers={"Authorization": "Bearer " + bearer}, json={})
        # TestClient's peer is not loopback, so admission may answer 403 before 401; live TCP gives 401.
        assert r.status_code in (401, 403) and bearer not in r.text


@pytest.mark.xfail(strict=True, reason="QA-S4-2: SO_REUSEADDR lets the edge co-bind 127.0.0.1 under another "
                                       "process's 0.0.0.0 listener on the same port; state says 'listening'")
def test_edge_reports_port_in_use_when_another_process_holds_the_wildcard():
    from arail.portal.edge_app import EdgeListener
    occ = socket.socket()
    occ.bind(("0.0.0.0", 0))
    occ.listen(1)
    port = occ.getsockname()[1]

    async def go():
        lst = EdgeListener()
        state = lst.start(port, instance=False)
        await lst.stop()
        return state
    try:
        assert asyncio.run(go()) == "port_in_use"
    finally:
        occ.close()


@pytest.mark.xfail(strict=True, reason="QA-S4-3: the edge's uvicorn.Config re-levels the process-global uvicorn "
                                       "loggers and strips uvicorn.access handlers, silencing the portal's logs")
def test_starting_the_edge_leaves_the_portals_uvicorn_loggers_alone():
    from arail.portal.edge_app import EdgeListener
    err, acc = logging.getLogger("uvicorn.error"), logging.getLogger("uvicorn.access")
    err.setLevel(logging.INFO)
    acc.setLevel(logging.INFO)
    h = logging.NullHandler()
    acc.addHandler(h)
    before = (err.level, acc.level, list(acc.handlers), acc.propagate)

    async def go():
        lst = EdgeListener()
        lst.start(0 or _free_port(), instance=False)
        await lst.stop()
    try:
        asyncio.run(go())
        assert (err.level, acc.level, list(acc.handlers), acc.propagate) == before
    finally:
        acc.removeHandler(h)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ----------------------------------------------------------------- revocation vs open streams (live)
def test_an_open_activity_stream_stops_after_logout_all(tmp_path):
    port, edge = _free_port(), 0
    lab = tmp_path / "lab"
    lab.mkdir(mode=0o700)
    envf = lab / ".env"
    envf.write_text(f"ARAIL_PASSWORD={PASS}\n")
    os.chmod(envf, 0o600)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ARAIL_", "PORTAL_", "LAB_"))}
    env.update(PORTAL_PORT=str(port), ARAIL_EDGE_PORT=str(edge), LAB_ROOT=str(lab), ARAIL_DATA_DIR=str(lab / "data"),
               ARAIL_ENV_FILE=str(envf), HOME=str(tmp_path), BIND_ADDR="127.0.0.1", LAB_TIER="maximus",
               ARAIL_BOOT_WARM="0", PYTHONPATH=str(REPO / "src"))
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "arail.portal.app:app", "--host", "127.0.0.1",
                             "--port", str(port), "--app-dir", str(REPO / "src"), "--log-level", "warning"],
                            cwd=lab, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    import http.client
    import json

    def post_login(pw):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("POST", "/login", body=json.dumps({"passphrase": pw}), headers={"Content-Type": "application/json"})
        r = c.getresponse()
        r.read()
        return r
    try:
        for _ in range(120):
            try:
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                c.request("GET", "/api/instance")
                if c.getresponse().status == 200:
                    break
            except OSError:
                time.sleep(0.5)
        r = post_login(PASS)
        sid = re.search(r"(arail_sid_\d+=[^;]+)", r.getheader("set-cookie")).group(1)
        s = socket.create_connection(("127.0.0.1", port))
        s.settimeout(0.5)
        s.sendall(f"GET /api/activity/stream HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nCookie: {sid}\r\n\r\n".encode())
        t0 = time.time()
        while time.time() - t0 < 2:
            try:
                s.recv(65536)
            except socket.timeout:
                pass
        sub = subprocess.run([sys.executable, "-m", "arail.portal.auth_cli", "logout-all"], env=env, cwd=lab,
                             capture_output=True, text=True, timeout=60)
        assert sub.returncode == 0
        for _ in range(3):
            post_login("wrong-guess-for-event")      # each emits a 'security' activity event
        got = b""
        t0 = time.time()
        while time.time() - t0 < 10:
            try:
                chunk = s.recv(65536)
                if not chunk:
                    break
                got += chunk
            except socket.timeout:
                pass
        s.close()
        assert b"Failed portal login" not in got
    finally:
        proc.terminate()
        proc.wait(10)
