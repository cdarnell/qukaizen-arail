"""S4 — the kill switch made real.

halt_gate refuses kind="agent", passes system/ui/None; speech_gate exempts
"sre" only; the chokepoint refuses before touching the backend (F7); the
in-flight counter and hold_state() shape (feeding F17's UI, built in S6).
"""

from __future__ import annotations

import pytest

from arail import agent_context, agent_trace, scheduler
from arail.router.backends import BaseBackend, ModelResponse
from arail.router.core import ModelRouter


@pytest.fixture(autouse=True)
def _clean_state():
    agent_context._reset_for_tests()
    agent_trace._reset_for_tests()
    scheduler._reset_halt_for_tests()
    yield
    agent_context._reset_for_tests()
    agent_trace._reset_for_tests()
    scheduler._reset_halt_for_tests()


# ---------------------------------------------------------------------------
# halt_gate — kind="agent" refuses only while held; system/ui/None pass
# ---------------------------------------------------------------------------

def test_halt_gate_passes_when_not_held():
    with agent_context.agent_call("buddy") as call:
        agent_context.halt_gate(call)  # must not raise


def test_halt_gate_refuses_agent_kind_when_held():
    scheduler.halt_all_jobs()
    with agent_context.agent_call("buddy") as call:
        with pytest.raises(agent_context.AgentHeldError):
            agent_context.halt_gate(call)


def test_halt_gate_passes_system_kind_when_held():
    scheduler.halt_all_jobs()
    with agent_context.system_call("world-forge") as call:
        agent_context.halt_gate(call)  # must not raise


def test_halt_gate_passes_none_context_when_held():
    scheduler.halt_all_jobs()
    agent_context.halt_gate(None)  # must not raise -- the operator's own
    # chat turn must not break because agents are held.


def test_halt_gate_passes_unattributed_when_held():
    scheduler.halt_all_jobs()
    # An unattributed call has no context at all, same as ctx=None.
    agent_context.halt_gate(agent_context.current())


def test_agent_held_error_is_a_runtime_error():
    assert issubclass(agent_context.AgentHeldError, RuntimeError)


# ---------------------------------------------------------------------------
# speech_gate — exempts "sre" only
# ---------------------------------------------------------------------------

def test_speech_gate_true_when_not_held():
    assert agent_context.speech_gate("buddy") is True


def test_speech_gate_false_when_held():
    scheduler.halt_all_jobs()
    assert agent_context.speech_gate("buddy") is False


@pytest.mark.parametrize("agent_id", [
    "librarian", "presence", "debt_advisor", "consolidation_analyzer",
])
def test_speech_gate_silences_every_non_exempt_speaker(agent_id):
    scheduler.halt_all_jobs()
    assert agent_context.speech_gate(agent_id) is False


def test_speech_gate_exempts_sre_only():
    scheduler.halt_all_jobs()
    assert agent_context.speech_gate("sre") is True


def test_hold_exempt_speakers_is_exactly_sre():
    assert agent_context.HOLD_EXEMPT_SPEAKERS == frozenset({"sre"})


# ---------------------------------------------------------------------------
# F7 — a halted agent still calling a model is refused at the chokepoint,
# before the backend is touched.
# ---------------------------------------------------------------------------

class _CountingBackend(BaseBackend):
    def __init__(self):
        self.calls = 0

    def complete(self, *a, **kw):
        self.calls += 1
        return ModelResponse(text="hi", model="m", tokens_used=1,
                             backend="fake", latency_ms=1.0)

    def stream_complete(self, *a, **kw):
        self.calls += 1
        yield ModelResponse(text="hi", model="m", tokens_used=1,
                            backend="fake", latency_ms=1.0)

    def health_check(self):
        return True


def test_complete_refused_while_held_backend_never_touched():
    scheduler.halt_all_jobs()
    backend = _CountingBackend()
    router = ModelRouter.from_backend(backend, "fake")
    with agent_context.agent_call("researcher"):
        with pytest.raises(agent_context.AgentHeldError):
            router.complete("hi")
    assert backend.calls == 0
    rec = agent_trace.ring(1)[0]
    assert rec["outcome"] == "refused_halted"
    assert rec["kind"] == "agent"
    assert rec["agent_id"] == "researcher"


def test_stream_complete_refused_while_held_backend_never_touched():
    scheduler.halt_all_jobs()
    backend = _CountingBackend()
    router = ModelRouter.from_backend(backend, "fake")
    with agent_context.agent_call("buddy"):
        with pytest.raises(agent_context.AgentHeldError):
            list(router.stream_complete("hi"))
    assert backend.calls == 0
    rec = agent_trace.ring(1)[0]
    assert rec["outcome"] == "refused_halted"


def test_ui_call_not_refused_while_held():
    """Holding agents must not break the operator's own chat turn."""
    scheduler.halt_all_jobs()
    backend = _CountingBackend()
    router = ModelRouter.from_backend(backend, "fake", billing_source="ui")
    router.complete("hi")  # must not raise
    assert backend.calls == 1
    rec = agent_trace.ring(1)[0]
    assert rec["outcome"] == "ok"


def test_system_call_not_refused_while_held():
    scheduler.halt_all_jobs()
    backend = _CountingBackend()
    router = ModelRouter.from_backend(backend, "fake")
    with agent_context.system_call("dictionary"):
        router.complete("hi")  # must not raise
    assert backend.calls == 1


def test_resume_admits_agent_calls_again():
    scheduler.halt_all_jobs()
    scheduler.resume_all_jobs()
    backend = _CountingBackend()
    router = ModelRouter.from_backend(backend, "fake")
    with agent_context.agent_call("buddy"):
        router.complete("hi")  # must not raise
    assert backend.calls == 1


# ---------------------------------------------------------------------------
# W3 precondition — zero new agent-sourced traces with outcome="ok" during
# a held window, only refused_halted ones.
# ---------------------------------------------------------------------------

def test_w3_zero_admitted_agent_traces_while_held():
    scheduler.halt_all_jobs()
    backend = _CountingBackend()
    router = ModelRouter.from_backend(backend, "fake")
    for agent_id in ("buddy", "researcher", "librarian"):
        with agent_context.agent_call(agent_id):
            with pytest.raises(agent_context.AgentHeldError):
                router.complete("hi")
    admitted = [r for r in agent_trace.ring(10)
                if r["kind"] == "agent" and r["outcome"] == "ok"]
    assert admitted == []


# ---------------------------------------------------------------------------
# in-flight counter (feeds hold_state()'s "N in flight" — F17's UI copy)
# ---------------------------------------------------------------------------

def test_in_flight_counts_agent_calls_only():
    assert agent_context.in_flight_agent_calls() == 0
    backend = _CountingBackend()
    router = ModelRouter.from_backend(backend, "fake")
    with agent_context.agent_call("buddy"):
        router.complete("hi")
    # complete() is synchronous end-to-end here, so by the time it returns
    # the counter is back at zero -- this proves the increment/decrement
    # pairing rather than genuine concurrency (that's F17/S6's live-view
    # concern with a real in-flight call).
    assert agent_context.in_flight_agent_calls() == 0


def test_in_flight_not_counted_for_system_or_ui():
    backend = _CountingBackend()
    router = ModelRouter.from_backend(backend, "fake", billing_source="ui")
    router.complete("hi")
    assert agent_context.in_flight_agent_calls() == 0


def test_note_agent_call_exited_never_goes_negative():
    agent_context.note_agent_call_exited(None)
    with agent_context.agent_call("buddy") as call:
        agent_context.note_agent_call_exited(call)
        agent_context.note_agent_call_exited(call)
    assert agent_context.in_flight_agent_calls() == 0


# ---------------------------------------------------------------------------
# hold_state() — the Admin control's data source
# ---------------------------------------------------------------------------

def test_hold_state_shape_when_not_held():
    state = agent_context.hold_state()
    assert state == {
        "held": False, "changed_at": None,
        "exempt_speakers": ["sre"], "in_flight": 0,
    }


def test_hold_state_reflects_held():
    scheduler.halt_all_jobs()
    state = agent_context.hold_state()
    assert state["held"] is True
    assert state["changed_at"] is not None


# ---------------------------------------------------------------------------
# F17 — the three behaviours the "Hold all agents" control claims, tested
# together so copy and behaviour cannot drift (the literal copy string is
# rendered in S6's admin.html; this proves the three underlying claims).
# ---------------------------------------------------------------------------

def test_f17_claim_1_agents_stop_calling_models():
    scheduler.halt_all_jobs()
    backend = _CountingBackend()
    router = ModelRouter.from_backend(backend, "fake")
    with agent_context.agent_call("buddy"):
        with pytest.raises(agent_context.AgentHeldError):
            router.complete("hi")
    assert backend.calls == 0


def test_f17_claim_2_agents_stop_speaking_sre_exempt():
    scheduler.halt_all_jobs()
    for agent_id in ("buddy", "librarian", "presence", "debt_advisor",
                     "consolidation_analyzer"):
        assert agent_context.speech_gate(agent_id) is False
    assert agent_context.speech_gate("sre") is True


def test_f17_claim_3_sre_crash_watcher_code_path_never_checks_hold():
    """SRE's non-LLM crash detection keeps running and may still post a
    plain, non-model alert -- verified structurally: nothing in
    _builtin_sre.py gates on jobs_halted(), so there is nothing there for
    hold to break."""
    import pathlib
    from arail.agents import _builtin_sre
    src = pathlib.Path(_builtin_sre.__file__).read_text()
    assert "jobs_halted" not in src


def test_f17_claim_4_this_world_only():
    """Hold is DATA_DIR-scoped (halt.json lives under the per-World
    DATA_DIR), same invariant F14 already tests for the trace store."""
    import pathlib
    src = pathlib.Path(scheduler.__file__).read_text()
    assert "from arail.config import DATA_DIR" in src


def test_hold_is_admission_control_not_cancellation():
    """A call already inside the backend when the switch flips runs to
    completion -- halt_gate is checked once, before the backend call, not
    injected into the backend's own execution."""
    scheduler.resume_all_jobs()
    backend = _CountingBackend()
    router = ModelRouter.from_backend(backend, "fake")

    class _SlowBackend(_CountingBackend):
        def complete(self, *a, **kw):
            # Simulate the switch flipping *during* the backend call.
            scheduler.halt_all_jobs()
            return super().complete(*a, **kw)

    router = ModelRouter.from_backend(_SlowBackend(), "fake")
    with agent_context.agent_call("buddy"):
        response = router.complete("hi")  # must not raise -- already admitted
    assert response.text == "hi"
