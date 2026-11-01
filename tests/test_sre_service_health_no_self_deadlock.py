"""Regression for a self-deadlock in the SRE watchdog's health check.

`SREAgent._run()` is a plain asyncio coroutine (one task, on the portal's
single event loop). It used to call `self._maybe_speak(...)` directly, and
`_maybe_speak()` synchronously runs every watcher in `WATCHERS` — including
`_watch_service_health()`, which does a BLOCKING `urllib.request.urlopen()`
GET against the portal's own `/api/jobs/state`.

That is a guaranteed self-deadlock: the request can't be answered until the
event loop is free, and the event loop can't become free until the blocking
request completes. It reproduces 100% of the time, not intermittently —
confirmed live against a real instance: a fresh World instance fired
"Portal /api/jobs/state is unreachable — portal may be down" on its very
first health-check tick, seconds after boot, while an independent process
hitting the identical URL at the identical moment got a clean 200. Over a
25+ hour World-instance session this fired roughly every 10 minutes for the
entire session, continuously — not restart noise, a structural bug.

Fixed by dispatching `_maybe_speak` to a worker thread
(`asyncio.to_thread`), freeing the event loop to answer its own request
while the check is in flight.

This test proves BOTH directions with the REAL SRE code (not a
reimplementation): a raw `asyncio.start_server` stands in for "the portal"
(enough to answer a GET with a 200 — `_watch_service_health` only calls
`urlopen(...).read()`, it doesn't parse the response), bound on the same
event loop as the check. Calling `_watch_service_health` synchronously
in-loop must observe "unreachable" (the bug, reproduced); calling it via
`asyncio.to_thread` (the fix) must observe success.
"""
from __future__ import annotations

import asyncio
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(_REPO_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))


async def _serve_one_ok_response(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Minimal HTTP/1.1 200 responder — just enough for urlopen().read()."""
    try:
        await reader.readuntil(b"\r\n\r\n")
    except (asyncio.IncompleteReadError, ConnectionError):
        pass
    body = b"{}"
    resp = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"Connection: close\r\n\r\n" + body
    )
    try:
        writer.write(resp)
        await writer.drain()
    except ConnectionError:
        pass
    writer.close()


async def _start_stub_portal() -> tuple[asyncio.AbstractServer, int]:
    server = await asyncio.start_server(_serve_one_ok_response, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port


def test_service_health_check_deadlocks_when_called_directly_in_loop(monkeypatch):
    """Sanity check pinning the bug class itself: calling the REAL
    `_watch_service_health` synchronously from the same event loop that
    must answer it times out (the 2s socket timeout inside the function
    plus the outer wait_for both firing is the deadlock manifesting
    exactly as it did in the field — an 'unreachable' verdict against a
    server that was, provably, up and listening the whole time)."""
    from arail.agents import _builtin_sre as sre_mod

    async def _direct_call(mod):
        # Deliberately NOT asyncio.to_thread — this is the pre-fix call
        # shape (`self._maybe_speak(...)` called bare from `_run()`).
        # Calling the sync function directly makes it block THIS
        # coroutine's own thread, matching the bug exactly.
        return mod._watch_service_health()

    async def _scenario():
        server, port = await _start_stub_portal()
        monkeypatch.setenv("PORTAL_PORT", str(port))
        async with server:
            # The bug, reproduced: a direct (non-threaded) call to the
            # watcher blocks THIS coroutine, which is the only thing that
            # could ever accept the connection it's making to itself. The
            # stub server can never run its handler.
            return await asyncio.wait_for(_direct_call(sre_mod), timeout=3.0)

    obs = asyncio.run(_scenario())
    assert obs is not None, (
        "expected the self-deadlock to manifest as an 'unreachable' "
        "Observation when the health check is awaited directly in-loop"
    )
    assert obs.watcher == "service-health"
    assert "unreachable" in obs.fact


def test_service_health_check_succeeds_via_to_thread(monkeypatch):
    """The fix: the identical check, against the identical stub server, on
    the identical event loop — but dispatched through `asyncio.to_thread`
    (what `SREAgent._run()` now does) — must succeed, because the worker
    thread's blocking call no longer starves the loop that has to answer
    it."""
    from arail.agents import _builtin_sre as sre_mod

    async def _scenario():
        server, port = await _start_stub_portal()
        monkeypatch.setenv("PORTAL_PORT", str(port))
        async with server:
            return await asyncio.wait_for(
                asyncio.to_thread(sre_mod._watch_service_health), timeout=3.0,
            )

    obs = asyncio.run(_scenario())
    assert obs is None, (
        f"expected a healthy (None) result via asyncio.to_thread, got {obs!r}"
    )


def test_sre_run_dispatches_maybe_speak_via_to_thread():
    """Contract check on SREAgent._run() itself, not just the watcher: the
    fix must live in the tick loop, or a different code path could
    regress it right back to a bare synchronous call without this test
    file's own two functions above ever running against the real _run()
    loop (which is a `while True` — not something to await to completion
    in a unit test)."""
    import inspect
    from arail.agents._builtin_sre import SREAgent

    src = inspect.getsource(SREAgent._run)
    assert "asyncio.to_thread" in src, (
        "SREAgent._run() must dispatch _maybe_speak via asyncio.to_thread — "
        "a direct call reintroduces the self-deadlock this file pins"
    )
