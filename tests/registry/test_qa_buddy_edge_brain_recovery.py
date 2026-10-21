"""QA (sprint 2026-10-20-buddy-always-on, TEST_REPORT finding Q-8): one failed model call must not leave Buddy without a brain.

Seen on the real system: QA's Ollama died for about five minutes during the blind run. One Buddy question failed with a
ConnectionError, the registry binding marked ``tier0-local`` unhealthy (``report_failure``), and from then on every handshake and
every ``brain.chat`` said ``brain_unavailable`` (HTTP 424; ``/status``: "Brain: not available."), for as long as ARAIL ran, with
Ollama answering again. The edge asks the registry with ``peek``, which never re-probes, and refuses before calling, so no
``report_success`` can ever clear the flag; the registry's own health loop runs only with ``ARAIL_AUTOCHECKS`` on (off by default).
Only a desk action (an ARAIL restart, a chat in the portal, the Models re-probe) brings Buddy back.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from arail.buddy_host import brain


class _Ollama(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = {"/api/version": {"version": "0.32.4"}, "/api/tags": {"models": [{"name": "ai-engineer:latest"}]},
                "/v1/models": {"object": "list", "data": [{"id": "ai-engineer:latest", "object": "model"}]}}.get(self.path.split("?")[0])
        self.send_response(200 if body is not None else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body or {}).encode())

    def log_message(self, *a):
        pass


@pytest.fixture
def ollama_back(tmp_registry):
    """A local Ollama that answers, and the registry's fast entry pointed at it."""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Ollama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tmp_registry.entries["tier0-local"].endpoint = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    brain._reset_for_tests()
    yield tmp_registry
    srv.shutdown()
    brain._reset_for_tests()


def test_baseline_a_healthy_local_fast_entry_is_buddys_brain(ollama_back):
    view = brain.describe()
    assert view.available and view.local and view.reason is None


def test_while_the_call_has_just_failed_the_brain_is_reported_unavailable(ollama_back):
    ollama_back.report_failure("tier0-local", ConnectionError("connection refused"))   # what registry/binding.py does
    assert brain.describe().reason == "brain_unavailable"


def test_once_ollama_answers_again_buddy_gets_his_brain_back_without_a_desk_action(ollama_back, monkeypatch):
    ollama_back.report_failure("tier0-local", ConnectionError("connection refused"))
    assert not brain.describe().available
    wall, mono = time.time(), time.monotonic()
    for step in range(1, 31):                     # thirty handshakes, 20 s apart: ten minutes
        monkeypatch.setattr(time, "time", lambda s=step: wall + 20 * s)
        monkeypatch.setattr(time, "monotonic", lambda s=step: mono + 20 * s)
        if brain.describe().available:
            break
    assert brain.describe().available
