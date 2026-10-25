"""The box-only edge app: ``/api/host/v1/*`` on ``127.0.0.1:$ARAIL_EDGE_PORT`` and nothing else.

Sprint 2026-10-25-buddy-s4-gateway-in-box, ARCHITECTURE.md C8. A second ASGI app with no portal
routes, no portal middleware, no cookies and no sessions, so S5 can allow the box exactly one host
target and an auth bug in the portal cannot be reached through it. It runs inside the portal
process on the portal's event loop (the edge needs the scheduler and the researcher, and
``pause_research`` must run on the loop thread).

Failure containment (F16/F17/F19):

* The socket is bound here, by us, before uvicorn sees it. If uvicorn bound it and failed, its
  ``startup`` would ``sys.exit(1)`` and take the portal down; a bind error here is just a state.
* ``capture_signals`` is a no-op on our server so the portal's SIGTERM/SIGINT handling, which
  launchd relies on, is untouched.
* The bind address is the literal ``127.0.0.1``, never ``BIND_ADDR``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
from typing import Optional

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from arail.buddy_host.link import DEFAULT_EDGE_PORT, edge_port  # noqa: F401 - re-exported
from arail.portal import host_edge_routes

_log = logging.getLogger(__name__)

EDGE_HOST = "127.0.0.1"          # a constant on purpose: not configurable, never BIND_ADDR
_LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "::1"})

edge_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def _hostname(raw: str) -> str:
    raw = raw.strip().lower()
    if raw.startswith("[") and "]" in raw:
        return raw[1:raw.index("]")]
    return raw.rsplit(":", 1)[0] if ":" in raw else raw


@edge_app.middleware("http")
async def _loopback_host_only(request, call_next):
    """No ARAIL_ALLOWED_HOSTS extension here: only a loopback name reaches the edge."""
    if _hostname(request.headers.get("host", "")) not in _LOOPBACK_NAMES:
        return JSONResponse(status_code=403, content={"error": "untrusted_host"})
    return await call_next(request)


host_edge_routes.register_routes(edge_app)


class EdgeServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):
        yield


class EdgeListener:
    """Owns the pre-bound socket and the serving task. ``state`` is what the operator is told."""

    def __init__(self) -> None:
        self.state = "not_started"
        self.port: Optional[int] = None
        self._server: Optional[EdgeServer] = None
        self._task: Optional[asyncio.Task] = None
        self._sock: Optional[socket.socket] = None

    def start(self, port: Optional[int] = None, *, instance: Optional[bool] = None) -> str:
        if instance is None:
            instance = bool(os.getenv("ARAIL_INSTANCE", "").strip())
        port = edge_port() if port is None else port
        if instance:
            self.state = "off (instance)"
            return self.state
        if port == 0:
            self.state = "off (ARAIL_EDGE_PORT=0)"
            return self.state
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((EDGE_HOST, port))
            sock.listen(128)
        except OSError:
            sock.close()
            self.state = "port_in_use"
            _log.warning("Buddy edge not started: 127.0.0.1:%s is in use. "
                         "Set ARAIL_EDGE_PORT to another port (0 turns the edge app off).", port)
            return self.state
        sock.setblocking(False)
        self._sock = sock
        self.port = sock.getsockname()[1]
        cfg = uvicorn.Config(edge_app, lifespan="off", access_log=False, log_level="warning",
                             log_config=None, server_header=False)
        self._server = EdgeServer(cfg)
        self._task = asyncio.get_running_loop().create_task(self._server.serve(sockets=[sock]))
        self._task.add_done_callback(self._finished)
        self.state = "listening"
        return self.state

    def _finished(self, task: asyncio.Task) -> None:
        if self.state == "stopping":
            self.state = "stopped"
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            exc = None
        self.state = "stopped:" + (type(exc).__name__ if exc else "Exited")
        _log.warning("Buddy edge listener %s", self.state)

    async def stop(self) -> None:
        if self._server is None or self._task is None:
            return
        self.state = "stopping"
        self._server.should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=3.0)
        except (asyncio.TimeoutError, asyncio.CancelledError, BaseException):  # noqa: BLE001 - SystemExit too
            self._task.cancel()
        self.state = "stopped"
        self._server = self._task = None
        if self._sock is not None:
            with contextlib.suppress(OSError):
                self._sock.close()
            self._sock = None


listener = EdgeListener()
