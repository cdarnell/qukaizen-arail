"""S6 — the four admin endpoints over the trace store.

F13: 404 on minimalist, parameterised so a fifth endpoint added without
the gate fails. No GET mutates state. F17: the hold control's copy and
its three behavioural claims tested together. W1's "within 2s" structural
half: no setInterval polling for lane data.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from arail import agent_context, agent_trace, config, scheduler
from arail.portal.app import app


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    agent_context._reset_for_tests()
    agent_trace._reset_for_tests()
    scheduler._reset_halt_for_tests()
    yield
    agent_context._reset_for_tests()
    agent_trace._reset_for_tests()
    scheduler._reset_halt_for_tests()


def _client() -> TestClient:
    return TestClient(app)


# GET entries carry no body; POST entries carry a minimal valid JSON body.
_ENDPOINTS = [
    ("get", "/api/admin/agent-lanes", None),
    ("get", "/api/admin/agent-trace/deadbeefcafef00d", None),
    ("post", "/api/admin/agents/hold", {"hold": False}),
    ("post", "/api/admin/flight-recorder", {"enabled": False}),
]


@pytest.mark.parametrize("method,path,body", _ENDPOINTS)
def test_f13_404_on_minimalist(monkeypatch, method, path, body):
    monkeypatch.setenv("LAB_TIER", "minimalist")
    client = _client()
    resp = (client.get(path) if body is None
            else client.post(path, json=body))
    assert resp.status_code == 404


@pytest.mark.parametrize("method,path,body", _ENDPOINTS)
def test_reachable_on_maximus(monkeypatch, method, path, body):
    monkeypatch.setenv("LAB_TIER", "maximus")
    client = _client()
    resp = (client.get(path) if body is None
            else client.post(path, json=body))
    assert resp.status_code in (200, 404)  # 404 only for the unknown trace_id
    if path.startswith("/api/admin/agent-trace/"):
        assert resp.status_code == 404  # unknown id, correctly 404
    else:
        assert resp.status_code == 200


def test_agent_lanes_get_never_mutates_state(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    agent_trace.record(trace_id="a" * 16, agent_id="buddy", kind="agent")
    before = agent_trace.stats()
    _client().get("/api/admin/agent-lanes")
    assert agent_trace.stats() == before


def test_agent_trace_detail_get_never_mutates_state(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    agent_trace.record(trace_id="b" * 16, agent_id="buddy", kind="agent")
    before = agent_trace.stats()
    _client().get("/api/admin/agent-trace/" + "b" * 16)
    assert agent_trace.stats() == before


# ---------------------------------------------------------------------------
# GET /api/admin/agent-lanes
# ---------------------------------------------------------------------------

def test_agent_lanes_shape(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    r = _client().get("/api/admin/agent-lanes")
    data = r.json()
    assert data["schema"] == "arail.agent_lanes/v1"
    assert isinstance(data["lanes"], list)
    assert "hold" in data
    assert "recorder" in data
    assert "slot" in data


# ---------------------------------------------------------------------------
# GET /api/admin/agent-trace/{trace_id}
# ---------------------------------------------------------------------------

def test_agent_trace_detail_returns_the_record(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    agent_trace.record(trace_id="c" * 16, agent_id="researcher", kind="agent",
                       model="m")
    r = _client().get("/api/admin/agent-trace/" + "c" * 16)
    assert r.status_code == 200
    assert r.json()["model"] == "m"


def test_agent_trace_detail_404_on_unknown_id(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    r = _client().get("/api/admin/agent-trace/" + "0" * 16)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/admin/agents/hold
# ---------------------------------------------------------------------------

def test_hold_endpoint_flips_the_flag(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    client = _client()
    r = client.post("/api/admin/agents/hold", json={"hold": True})
    assert r.status_code == 200
    assert r.json()["held"] is True
    assert scheduler.jobs_halted() is True

    r2 = client.post("/api/admin/agents/hold", json={"hold": False})
    assert r2.json()["held"] is False
    assert scheduler.jobs_halted() is False


def test_hold_endpoint_rejects_cross_site(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    client = _client()
    r = client.post("/api/admin/agents/hold", json={"hold": True},
                    headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/admin/flight-recorder
# ---------------------------------------------------------------------------

def test_flight_recorder_endpoint_flips_the_flag(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    client = _client()
    r = client.post("/api/admin/flight-recorder", json={"enabled": True})
    assert r.status_code == 200
    assert r.json()["enabled"] is True
    assert agent_trace.recorder_on() is True


def test_flight_recorder_endpoint_rejects_cross_site(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    client = _client()
    r = client.post("/api/admin/flight-recorder", json={"enabled": True},
                    headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# No GET mutates state (static check over the route decorators)
# ---------------------------------------------------------------------------

def test_no_new_get_route_mutates_static_check():
    """The two GET endpoints in this family must be pure reads -- checked
    by source inspection for any call that writes state."""
    import inspect
    from arail.portal import app as app_mod

    for fn_name in ("admin_agent_lanes", "admin_agent_trace_detail",
                    "admin_agent_trace_stream"):
        src = inspect.getsource(getattr(app_mod, fn_name))
        for forbidden in ("halt_all_jobs(", "resume_all_jobs(",
                          "set_recorder_enabled(", "purge_legacy_bodies(",
                          "dismiss_legacy_notice("):
            assert forbidden not in src, f"{fn_name} calls {forbidden}"


# ---------------------------------------------------------------------------
# F17 — copy + behaviour tested together (the literal control string)
# ---------------------------------------------------------------------------

HOLD_ALL_AGENTS_COPY = (
    "Hold all agents — agents stop calling models and stop speaking. "
    "Calls already running finish ({in_flight} in flight). The SRE crash "
    "watcher keeps watching and may still post a plain, non-model alert. "
    "This World only; survives a restart."
)


def test_f17_copy_matches_behaviour(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    client = _client()
    r = client.post("/api/admin/agents/hold", json={"hold": True})
    state = r.json()
    rendered = HOLD_ALL_AGENTS_COPY.format(in_flight=state["in_flight"])

    # Claim 1: agents stop calling models.
    from arail.router.backends import BaseBackend, ModelResponse
    from arail.router.core import ModelRouter

    class _B(BaseBackend):
        def complete(self, *a, **kw):
            return ModelResponse(text="x", model="m", tokens_used=1,
                                 backend="fake", latency_ms=1.0)

        def stream_complete(self, *a, **kw):
            yield ModelResponse(text="x", model="m", tokens_used=1,
                                backend="fake", latency_ms=1.0)

        def health_check(self):
            return True

    router = ModelRouter.from_backend(_B(), "fake")
    with agent_context.agent_call("buddy"):
        with pytest.raises(agent_context.AgentHeldError):
            router.complete("hi")
    assert "agents stop calling models" in rendered

    # Claim 2: agents stop speaking.
    assert agent_context.speech_gate("buddy") is False
    assert "stop speaking" in rendered

    # Claim 3: SRE keeps watching, may still post a plain alert.
    assert agent_context.speech_gate("sre") is True
    assert "SRE crash watcher keeps watching" in rendered
    import pathlib
    from arail.agents import _builtin_sre
    assert "jobs_halted" not in pathlib.Path(_builtin_sre.__file__).read_text()

    # Claim 4: This World only; survives a restart -- DATA_DIR-scoped,
    # persisted (already tested in test_halt_persistence.py / F14's tests).
    assert "This World only" in rendered
    assert "survives a restart" in rendered


# ---------------------------------------------------------------------------
# W1's structural "no polling" guard
# ---------------------------------------------------------------------------

def test_admin_template_uses_sse_not_setinterval_for_lanes():
    import pathlib
    from arail.portal import app as app_mod
    admin_html = (
        pathlib.Path(app_mod.__file__).parent / "templates" / "admin.html"
    )
    if not admin_html.exists():
        pytest.skip("admin.html not present in this checkout")
    src = admin_html.read_text()
    assert "agent-trace-stream" in src or "EventSource" in src
    # No setInterval anywhere near the lanes rendering -- a poll interval
    # is the only way this path could exceed W1's 2s bound.
    lanes_section_start = src.find("agent-lanes")
    if lanes_section_start != -1:
        window = src[max(0, lanes_section_start - 500):lanes_section_start + 3000]
        assert "setInterval" not in window
