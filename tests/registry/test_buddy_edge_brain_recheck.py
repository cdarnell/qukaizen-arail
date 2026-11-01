"""Q-8 fix (sprint 2026-10-20-buddy-always-on, build round 3): the edge re-probes a down local ``fast`` entry on its own.

The recovery itself is QA's ``test_qa_buddy_edge_brain_recovery.py``. These pin the edges of the fix: it waits out the
fail TTL, it does not hammer a model that is still down, it never probes a non-local entry, and a good probe is
visible in the registry like any recovery.
"""

from __future__ import annotations

import socket
import time

import pytest

from arail.buddy_host import brain
from tests.registry.test_qa_buddy_edge_brain_recovery import ollama_back  # noqa: F401 - the fixture


def _clock(monkeypatch, seconds: float) -> None:
    wall, mono = time.time(), time.monotonic()
    monkeypatch.setattr(time, "time", lambda: wall + seconds)
    monkeypatch.setattr(time, "monotonic", lambda: mono + seconds)


def _dead_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_nothing_is_probed_inside_the_fail_ttl(ollama_back, monkeypatch):  # noqa: F811
    ollama_back.report_failure("tier0-local", ConnectionError("connection refused"))
    _clock(monkeypatch, brain._PROBE_FAIL_TTL_S - 1)
    assert brain.describe().reason == "brain_unavailable"


def test_a_good_probe_after_the_ttl_restores_the_brain_and_records_the_recovery(ollama_back, monkeypatch):  # noqa: F811
    ollama_back.report_failure("tier0-local", ConnectionError("connection refused"))
    _clock(monkeypatch, brain._PROBE_FAIL_TTL_S + 1)
    assert brain.describe().available
    assert ollama_back.entries["tier0-local"].health.usable


def test_a_model_that_is_still_down_is_probed_once_per_ttl_not_once_per_handshake(ollama_back, monkeypatch):  # noqa: F811
    entry = ollama_back.entries["tier0-local"]
    entry.endpoint = f"http://127.0.0.1:{_dead_port()}/v1"
    ollama_back.report_failure("tier0-local", ConnectionError("connection refused"))
    calls = []
    from arail.registry import health
    real = health.probe_entry
    monkeypatch.setattr(health, "probe_entry", lambda e: calls.append(e.id) or real(e))
    _clock(monkeypatch, brain._PROBE_FAIL_TTL_S + 1)
    for _ in range(5):
        assert brain.describe().reason == "brain_unavailable"
    assert len(calls) == 1
    _clock(monkeypatch, 2 * brain._PROBE_FAIL_TTL_S + 2)
    assert brain.describe().reason == "brain_unavailable"
    assert len(calls) == 2


def test_a_cloud_entry_in_the_fast_slot_is_never_probed(ollama_back, monkeypatch):  # noqa: F811
    entry = ollama_back.entries["tier0-local"]
    entry.provider_type, entry.endpoint = "anthropic", "https://api.anthropic.com/v1"
    ollama_back.report_failure("tier0-local", ConnectionError("down"))
    from arail.registry import health
    monkeypatch.setattr(health, "probe_entry", lambda e: pytest.fail("a non-local entry was probed"))
    _clock(monkeypatch, brain._PROBE_FAIL_TTL_S + 1)
    assert not brain.describe().available


def test_a_remote_endpoint_in_the_fast_slot_is_never_probed(ollama_back, monkeypatch):  # noqa: F811
    ollama_back.entries["tier0-local"].endpoint = "http://192.0.2.7:11434/v1"
    ollama_back.report_failure("tier0-local", ConnectionError("down"))
    from arail.registry import health
    monkeypatch.setattr(health, "probe_entry", lambda e: pytest.fail("a non-local entry was probed"))
    _clock(monkeypatch, brain._PROBE_FAIL_TTL_S + 1)
    assert not brain.describe().available


def test_a_healthy_entry_costs_no_probe(ollama_back, monkeypatch):  # noqa: F811
    from arail.registry import health
    monkeypatch.setattr(health, "probe_entry", lambda e: pytest.fail("a healthy entry was probed"))
    assert brain.describe().available
