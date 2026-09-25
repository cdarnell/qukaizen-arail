"""V7 fix (ARCHITECTURE.md contract #8): GET /api/agents/status's per-agent
token figure sources from real usage (the trace ring), not
prompt_trace.max_tokens (the requested ceiling). tokens_out is the new,
correct key; tokens is a deprecated alias now carrying the same corrected
value for one release.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from arail import agent_trace, config
from arail.portal.app import app


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    agent_trace._reset_for_tests()
    yield
    agent_trace._reset_for_tests()


def _client() -> TestClient:
    return TestClient(app)


def test_tokens_out_sourced_from_trace_ring_not_max_tokens():
    # Requested a large ceiling, actually used a small amount -- the old
    # bug would report the ceiling; V7 must report the real usage.
    agent_trace.record(trace_id="a" * 16, agent_id="researcher", kind="agent",
                       tokens_out=17)
    r = _client().get("/api/agents/status")
    data = r.json()
    assert data["researcher"]["tokens_out"] == 17
    assert data["researcher"]["tokens"] == 17  # deprecated alias, same value


def test_tokens_out_sums_multiple_calls():
    agent_trace.record(trace_id="b" * 16, agent_id="buddy", kind="agent", tokens_out=10)
    agent_trace.record(trace_id="c" * 16, agent_id="buddy", kind="agent", tokens_out=5)
    r = _client().get("/api/agents/status")
    assert r.json()["buddy"]["tokens_out"] == 15


def test_tokens_out_zero_when_no_calls():
    r = _client().get("/api/agents/status")
    data = r.json()
    assert data["researcher"]["tokens_out"] == 0
    assert data["browser"]["tokens_out"] == 0


def test_system_and_unattributed_never_counted_toward_an_agent():
    agent_trace.record(trace_id="d" * 16, kind="system", label="dictionary", tokens_out=99)
    agent_trace.record(trace_id="e" * 16, attribution="unattributed", tokens_out=42)
    r = _client().get("/api/agents/status")
    data = r.json()
    for agent in ("researcher", "curator", "browser", "buddy", "sre"):
        assert data[agent]["tokens_out"] == 0
