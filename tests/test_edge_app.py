"""The box-only edge app (S4 step 6; ARCHITECTURE.md C8, correction 1, F7, F16-F19)."""
from __future__ import annotations

import asyncio
import json
import signal
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from arail.buddy_host import link
from arail.portal import edge_app as ea
from arail.portal.route_table import flatten, sample_path

REPO = Path(__file__).resolve().parents[1]


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_default_edge_port_is_outside_every_world_instance_block():
    """Correction 1: 8091 was inside block 0 (8090-8099) of scripts/lib/instances.sh."""
    text = (REPO / "scripts/lib/instances.sh").read_text()
    base = int(next(l for l in text.splitlines() if l.startswith("INST_PORT_BASE=")).split("=")[1].split()[0].strip('"${}:-'))
    assert link.DEFAULT_EDGE_PORT == 8089 and link.DEFAULT_EDGE_PORT < base
    assert not (base <= link.DEFAULT_EDGE_PORT < 9100)


def test_edge_port_env(monkeypatch):
    monkeypatch.delenv("ARAIL_EDGE_PORT", raising=False)
    assert link.edge_port() == 8089
    monkeypatch.setenv("ARAIL_EDGE_PORT", "0")
    assert link.edge_port() == 0
    monkeypatch.setenv("ARAIL_EDGE_PORT", "9071")
    assert link.edge_port() == 9071
    monkeypatch.setenv("ARAIL_EDGE_PORT", "banana")
    assert link.edge_port() == 8089


def test_edge_app_serves_only_the_edge_routes():
    paths = [e.path for e in flatten(ea.edge_app) if e.kind in ("route", "mount")]
    assert paths and all(p.startswith("/api/host/v1/") for p in paths), paths


def edge_client():
    return TestClient(ea.edge_app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000),
                      raise_server_exceptions=False)


def test_every_portal_path_is_404_on_the_edge_app():
    """F18: a portal route is never reachable through the edge."""
    from arail.portal import app as portal_app
    c = edge_client()
    leaks = []
    for e in flatten(portal_app.app):
        if e.kind != "route" or e.path.startswith("/api/host/v1/"):
            continue
        for m in sorted(e.methods - {"HEAD", "OPTIONS"}):
            r = c.request(m, sample_path(e.path))
            if r.status_code != 404:
                leaks.append((m, e.path, r.status_code))
    assert not leaks, leaks[:10]


def test_non_loopback_host_is_403_on_the_edge():
    r = TestClient(ea.edge_app, base_url="http://evil.example", client=("127.0.0.1", 1)).post("/api/host/v1/handshake")
    assert r.status_code == 403


def test_extra_allowed_hosts_do_not_extend_the_edge(monkeypatch):
    monkeypatch.setenv("ARAIL_ALLOWED_HOSTS", "lab.local")
    r = TestClient(ea.edge_app, base_url="http://lab.local", client=("127.0.0.1", 1)).post("/api/host/v1/handshake")
    assert r.status_code == 403


def test_edge_ignores_portal_cookies_and_bearers_and_wants_the_capability_credential():
    """F7: a valid portal session or token gives nothing on the edge; admission wants bhc1_."""
    c = edge_client()
    c.cookies.set("arail_sid_8080", "A" * 43)
    r = c.post("/api/host/v1/handshake", headers={"Authorization": "Bearer arl1_" + "A" * 43})
    assert r.status_code == 401 and r.json()["error"]["code"] in ("credential_invalid", "not_linked")   # the edge's own refusal
    r = c.post("/api/host/v1/handshake")
    assert r.json()["error"]["code"] == "credential_missing"


def test_link_url_follows_the_edge_port(monkeypatch):
    monkeypatch.setenv("ARAIL_EDGE_PORT", "9071")
    assert link.edge_base_url() == "http://127.0.0.1:9071/api/host/v1"
    monkeypatch.setenv("ARAIL_EDGE_PORT", "0")
    monkeypatch.setenv("PORTAL_PORT", "8080")
    with pytest.raises(link.PolicyRefusal, match="Buddy needs the edge app"):            # S5: no fallback to the portal's port
        link.edge_base_url()


# --- the listener itself (real sockets) --------------------------------------------------------
def _run(coro):
    return asyncio.run(coro)


def test_listener_binds_loopback_only_and_answers_then_stops():
    port = free_port()

    async def go():
        lis = ea.EdgeListener()
        assert lis.start(port, instance=False) == "listening"
        await asyncio.sleep(0.3)
        host, p = lis._sock.getsockname()[:2]
        assert host == "127.0.0.1" and p == port       # F19: the literal loopback, not 0.0.0.0
        body = await asyncio.to_thread(_get, port, "/api/host/v1/brain/models")
        await lis.stop()
        return body, lis.state

    (code, text), state = _run(go())
    assert code == 401 and "credential_missing" in text and state == "stopped"


def _get(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=3) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def test_unknown_paths_are_404_over_a_real_socket():
    port = free_port()

    async def go():
        lis = ea.EdgeListener()
        lis.start(port, instance=False)
        await asyncio.sleep(0.3)
        out = [await asyncio.to_thread(_get, port, p) for p in ("/", "/api/jobs/state", "/login", "/metrics", "/api/docs")]
        await lis.stop()
        return out

    assert [c for c, _ in _run(go())] == [404] * 5


def test_port_in_use_is_a_state_not_a_crash():
    """F16: the portal serves on; no SystemExit."""
    hog = socket.socket()
    hog.bind(("127.0.0.1", 0))
    hog.listen(1)
    port = hog.getsockname()[1]
    try:
        async def go():
            lis = ea.EdgeListener()
            return lis.start(port, instance=False)
        assert _run(go()) == "port_in_use"
    finally:
        hog.close()


def test_a_world_instance_never_starts_the_edge():
    lis = ea.EdgeListener()
    assert lis.start(free_port(), instance=True) == "off (instance)"
    assert lis._sock is None


def test_port_zero_turns_it_off():
    assert ea.EdgeListener().start(0, instance=False).startswith("off")


def test_starting_the_edge_leaves_signal_handlers_alone():
    """F17: uvicorn would otherwise install its own SIGINT/SIGTERM handlers."""
    port = free_port()

    async def go():
        before = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT))
        lis = ea.EdgeListener()
        lis.start(port, instance=False)
        await asyncio.sleep(0.3)
        during = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT))
        await lis.stop()
        return before, during, (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT))

    before, during, after = _run(go())
    assert before == during == after


def test_sigterm_still_stops_a_process_that_runs_the_edge():
    """F17 end to end: a process serving the edge exits on SIGTERM within 5 s."""
    port = free_port()
    code = (
        "import asyncio, signal, sys\n"
        "from arail.portal import edge_app as ea\n"
        "async def main():\n"
        "    stop = asyncio.Event()\n"
        "    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)\n"
        f"    ea.listener.start({port}, instance=False)\n"
        "    print('up', flush=True)\n"
        "    await stop.wait()\n"
        "    await ea.listener.stop()\n"
        "asyncio.run(main())\n"
    )
    p = subprocess.Popen([sys.executable, "-c", code], cwd=REPO, stdout=subprocess.PIPE, text=True,
                         env={**__import__("os").environ, "PYTHONPATH": str(REPO / "src")})
    try:
        assert p.stdout.readline().strip() == "up"
        p.send_signal(signal.SIGTERM)
        assert p.wait(timeout=5) == 0
    finally:
        if p.poll() is None:
            p.kill()


def test_a_serving_failure_is_recorded_not_raised():
    port = free_port()

    async def go():
        lis = ea.EdgeListener()
        lis.start(port, instance=False)
        await asyncio.sleep(0.2)
        lis._task.cancel()
        await asyncio.sleep(0.2)
        return lis.state

    assert _run(go()).startswith("stopped")
