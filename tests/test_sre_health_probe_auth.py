"""A.1: the SRE liveness watcher probes the anonymous /api/system/health, never a gated route."""
from __future__ import annotations

import asyncio
import socket
import threading
import time

import pytest

from arail.agents import _builtin_sre as sre_mod

pytestmark = pytest.mark.real_auth


def test_probe_returns_none_against_a_live_portal_with_auth_on_and_no_credential(monkeypatch):
    import uvicorn

    from arail.portal import app as portal_app

    monkeypatch.setenv("ARAIL_PASSWORD", "test-passphrase-not-real")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    port = sock.getsockname()[1]
    monkeypatch.setenv("PORTAL_PORT", str(port))
    server = uvicorn.Server(uvicorn.Config(portal_app.app, lifespan="off", log_level="warning"))
    t = threading.Thread(target=lambda: asyncio.run(server.serve(sockets=[sock])), daemon=True)
    t.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started
        # a gated route really is gated for this caller (no credential)...
        import urllib.error
        import urllib.request
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/jobs/state", timeout=2)
        assert ei.value.code == 401
        # ...and the watcher does not depend on it
        assert sre_mod._watch_service_health() is None
    finally:
        server.should_exit = True
        t.join(5)


def test_a_503_is_up_but_unhealthy_not_unreachable(monkeypatch):
    async def handler(reader, writer):
        try:
            await reader.readuntil(b"\r\n\r\n")
        except Exception:  # noqa: BLE001
            pass
        writer.write(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()

    async def scenario():
        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        monkeypatch.setenv("PORTAL_PORT", str(server.sockets[0].getsockname()[1]))
        async with server:
            return await asyncio.to_thread(sre_mod._watch_service_health)

    obs = asyncio.run(scenario())
    assert obs is not None and "503" in obs.fact and "unreachable" not in obs.fact
