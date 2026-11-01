"""DE4's kill gate: instrumentation must not change what it observes.

Design budget (ARCHITECTURE.md "Hot-path cost budget"): <= 1.0 ms p95 per
record() call with the recorder off, one-fifth of DE4's 5 ms kill threshold.
Marked perf so CI can exclude with -m "not perf" on a loaded/shared box; this
test reports the number either way rather than only asserting pass/fail, so a
future run has a number to compare against.
"""

from __future__ import annotations

import time

import pytest

from arail import agent_trace

pytestmark = pytest.mark.perf


@pytest.fixture(autouse=True)
def _clean():
    agent_trace._reset_for_tests()
    yield
    agent_trace._reset_for_tests()


def test_record_p95_under_one_millisecond(monkeypatch, tmp_path):
    from arail import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    samples = []
    for i in range(2000):
        t0 = time.perf_counter()
        agent_trace.record(
            trace_id=f"{i:016x}", agent_id="buddy", kind="agent",
            attribution="agent:buddy", model="fake", backend="fake",
            tokens_in=10, tokens_out=20, latency_ms=12.0,
            slot={"capacity": 1, "in_flight": 0, "pending": 0,
                  "held_by_other": False},
        )
        samples.append((time.perf_counter() - t0) * 1000.0)

    samples.sort()
    p95 = samples[int(0.95 * (len(samples) - 1))]
    print(f"\nagent_trace.record() p95 over 2000 calls: {p95:.4f} ms")
    assert p95 < 1.0, (
        f"record() p95 is {p95:.4f} ms, over the 1.0 ms design budget "
        "(DE4 kills at 5 ms — this is not yet a kill, but the budget is "
        "meant to leave headroom before that line)."
    )
