"""GET /api/agents/prompts — CHANGED SHAPE (contract #7, breaking).

Sourced from agent_trace, not activity_log's prompt_trace convention.
Bodies present only when the flight recorder is on; absent (not empty
string) otherwise.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from arail import agent_context, agent_trace, config
from arail.portal.app import app


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    agent_context._reset_for_tests()
    agent_trace._reset_for_tests()
    yield
    agent_context._reset_for_tests()
    agent_trace._reset_for_tests()


def _client() -> TestClient:
    return TestClient(app)


def test_shape_when_no_traces():
    r = _client().get("/api/agents/prompts")
    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) == {"recorder", "empty_state", "traces"}
    assert data["recorder"] == {"enabled": False, "reason": "off_by_default"}
    assert data["empty_state"] == "flight recorder off — flip to capture prompt bodies"
    assert data["traces"] == []


def test_recorder_off_traces_have_no_prompt_or_response_keys():
    agent_trace.record(
        trace_id="a" * 16, agent_id="researcher", kind="agent",
        attribution="agent:researcher", model="m", backend="b",
        tokens_out=10, latency_ms=5.0,
        bodies={"prompt": "should never surface", "response": "should never surface",
               "truncated": False, "redactions": 0},
    )
    r = _client().get("/api/agents/prompts")
    data = r.json()
    assert len(data["traces"]) == 1
    trace = data["traces"][0]
    assert "prompt" not in trace
    assert "response" not in trace
    assert trace["model"] == "m"
    assert trace["tokens_out"] == 10


def test_recorder_on_traces_carry_bodies():
    agent_trace.set_recorder_enabled(True)
    agent_trace.record(
        trace_id="b" * 16, agent_id="researcher", kind="agent",
        attribution="agent:researcher", model="m", backend="b",
        tokens_out=10, latency_ms=5.0,
        bodies={"prompt": "the prompt", "response": "the response",
               "truncated": False, "redactions": 0},
    )
    r = _client().get("/api/agents/prompts")
    data = r.json()
    assert data["recorder"]["enabled"] is True
    trace = data["traces"][0]
    assert trace["prompt"] == "the prompt"
    assert trace["response"] == "the response"


def test_recorder_on_but_no_bodies_captured_for_this_record():
    """A record with bodies=None (e.g. captured before the recorder was
    flipped on) still must not surface prompt/response keys."""
    agent_trace.set_recorder_enabled(True)
    agent_trace.record(
        trace_id="c" * 16, agent_id="researcher", kind="agent",
        attribution="agent:researcher", model="m", backend="b",
        bodies=None,
    )
    r = _client().get("/api/agents/prompts")
    trace = r.json()["traces"][0]
    assert "prompt" not in trace
    assert "response" not in trace


def test_filters_by_agent():
    agent_trace.record(trace_id="d" * 16, agent_id="researcher", kind="agent")
    agent_trace.record(trace_id="e" * 16, agent_id="buddy", kind="agent")
    r = _client().get("/api/agents/prompts?agent=buddy")
    data = r.json()
    assert len(data["traces"]) == 1
    assert data["traces"][0]["source"] == "buddy"


def test_unattributed_and_ui_excluded():
    agent_trace.record(trace_id="f" * 16, attribution="unattributed")
    r = _client().get("/api/agents/prompts")
    assert r.json()["traces"] == []
