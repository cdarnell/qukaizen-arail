"""Integration tests for the ModelRouter chokepoint (ARCHITECTURE.md
interface contract #3): exactly one agent_trace.record() per call including
failure; billing_source rewrite except for the "ui" bucket (F19); the
"unattributed" sentinel with call_site (F3); two concurrent tasks through one
cached router keep separate attribution (F5).
"""

from __future__ import annotations

import asyncio

import pytest

from arail import agent_context, agent_trace
from arail.router.backends import BaseBackend, ModelResponse
from arail.router.core import ModelRouter


class _FakeBackend(BaseBackend):
    """Minimal backend: complete() returns a canned response; stream_complete
    yields two string deltas then the terminal ModelResponse, matching every
    real backend's contract."""

    def __init__(self, model: str = "fake-model", backend_name: str = "fake",
                fail: bool = False):
        self._model = model
        self._backend_name = backend_name
        self._fail = fail

    def complete(self, prompt, max_tokens=512, temperature=0.7, top_p=None,
                *, system=None, messages=None):
        if self._fail:
            raise RuntimeError("backend exploded")
        return ModelResponse(
            text="hello", model=self._model, tokens_used=7,
            backend=self._backend_name, latency_ms=12.5,
        )

    def stream_complete(self, prompt, max_tokens=512, temperature=0.7,
                        top_p=None, *, system=None, messages=None):
        if self._fail:
            raise RuntimeError("stream exploded")
            yield  # pragma: no cover - unreachable, satisfies generator shape
        yield "hel"
        yield "lo"
        yield ModelResponse(
            text="hello", model=self._model, tokens_used=7,
            backend=self._backend_name, latency_ms=12.5,
        )

    def health_check(self):
        return True


def _router(billing_source="agent", fail=False) -> ModelRouter:
    return ModelRouter.from_backend(_FakeBackend(fail=fail), "fake",
                                     billing_source=billing_source)


@pytest.fixture(autouse=True)
def _clean_state():
    agent_context._reset_for_tests()
    agent_trace._reset_for_tests()
    yield
    agent_context._reset_for_tests()
    agent_trace._reset_for_tests()


# ---------------------------------------------------------------------------
# Exactly one record() per call
# ---------------------------------------------------------------------------

def test_complete_records_exactly_one_trace():
    router = _router()
    with agent_context.agent_call("researcher"):
        router.complete("hi")
    assert agent_trace.stats()["recorded"] == 1


def test_complete_records_on_backend_failure():
    router = _router(fail=True)
    with agent_context.agent_call("researcher"):
        with pytest.raises(RuntimeError):
            router.complete("hi")
    assert agent_trace.stats()["recorded"] == 1
    rec = agent_trace.ring(1)[0]
    assert rec["outcome"] == "error"
    assert rec["error_class"] == "RuntimeError"


def test_stream_complete_records_exactly_one_trace():
    router = _router()
    with agent_context.agent_call("buddy"):
        list(router.stream_complete("hi"))
    assert agent_trace.stats()["recorded"] == 1
    rec = agent_trace.ring(1)[0]
    assert rec["outcome"] == "ok"
    assert rec["streamed"] is True


def test_stream_complete_records_on_backend_failure():
    router = _router(fail=True)
    with agent_context.agent_call("buddy"):
        with pytest.raises(RuntimeError):
            list(router.stream_complete("hi"))
    assert agent_trace.stats()["recorded"] == 1
    rec = agent_trace.ring(1)[0]
    assert rec["outcome"] == "error"


# ---------------------------------------------------------------------------
# billing_source rewrite (F19 — "ui" is never rewritten)
# ---------------------------------------------------------------------------

def test_ui_billing_source_untouched_even_with_active_agent_context():
    router = _router(billing_source="ui")
    from arail.costs import cost_tracker
    before = cost_tracker.calls_by_source.get("ui", 0)
    with agent_context.agent_call("buddy"):
        router.complete("hi")
    assert cost_tracker.calls_by_source.get("ui", 0) == before + 1


def test_agent_billing_source_rewritten_per_agent():
    router = _router(billing_source="agent")
    from arail.costs import cost_tracker
    before = cost_tracker.calls_by_source.get("agent:buddy", 0)
    with agent_context.agent_call("buddy"):
        router.complete("hi")
    assert cost_tracker.calls_by_source.get("agent:buddy", 0) == before + 1
    # Never the blanket bucket.
    assert "agent" not in cost_tracker.calls_by_source or (
        cost_tracker.calls_by_source["agent"] == 0
    )


def test_unattributed_call_never_billed_as_bare_agent():
    router = _router(billing_source="agent")
    from arail.costs import cost_tracker
    before_unattributed = cost_tracker.calls_by_source.get("unattributed", 0)
    router.complete("hi")  # no context active
    assert cost_tracker.calls_by_source.get("unattributed", 0) == before_unattributed + 1


# ---------------------------------------------------------------------------
# F3 — the unattributed sentinel carries call_site
# ---------------------------------------------------------------------------

def test_unattributed_trace_carries_call_site():
    router = _router()
    router.complete("hi")  # no context active
    rec = agent_trace.ring(1)[0]
    assert rec["attribution"] == "unattributed"
    assert rec["call_site"] is not None
    assert "test_router_trace_chokepoint" in rec["call_site"]


def test_attributed_trace_records_agent_fields():
    router = _router()
    with agent_context.agent_call("buddy", brain="deep", effort="high"):
        router.complete("hi")
    rec = agent_trace.ring(1)[0]
    assert rec["agent_id"] == "buddy"
    assert rec["attribution"] == "agent:buddy"
    assert rec["brain"] == "deep"
    assert rec["effort"] == "high"
    assert rec["model"] == "fake-model"
    assert rec["backend"] == "fake"
    assert rec["tokens_out"] == 7


def test_system_call_trace_records_sys_label():
    router = _router()
    with agent_context.system_call("world-forge"):
        router.complete("hi")
    rec = agent_trace.ring(1)[0]
    assert rec["kind"] == "system"
    assert rec["label"] == "world-forge"
    assert rec["attribution"] == "sys:world-forge"


# ---------------------------------------------------------------------------
# non-streaming TTFT honesty
# ---------------------------------------------------------------------------

def test_complete_ttft_is_always_non_streaming():
    router = _router()
    with agent_context.agent_call("buddy"):
        router.complete("hi")
    rec = agent_trace.ring(1)[0]
    assert rec["ttft_ms"] is None
    assert rec["ttft_status"] == "non_streaming"
    assert rec["ttft_ms"] != rec["latency_ms"]


# ---------------------------------------------------------------------------
# F5 — two concurrent tasks through one cached router keep separate
# attribution.
# ---------------------------------------------------------------------------

def test_two_concurrent_tasks_through_one_cached_router_stay_separate():
    router = _router()  # one shared, cached-like router instance

    async def _call(agent_id: str, delay: float):
        with agent_context.agent_call(agent_id):
            await asyncio.sleep(delay)
            router.complete("hi")

    async def _scenario():
        await asyncio.gather(_call("researcher", 0.02), _call("browser", 0.0))

    asyncio.run(_scenario())

    recs = agent_trace.ring(10)
    assert len(recs) == 2
    ids = {r["agent_id"] for r in recs}
    assert ids == {"researcher", "browser"}
    trace_ids = {r["trace_id"] for r in recs}
    assert len(trace_ids) == 2  # never shared across the two tasks
