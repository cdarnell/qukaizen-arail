"""deep_policy: ``deep_failing`` and the stamped fast path.

C4 (policy) of sprints/2026-10-29-arail-deep-honest/ARCHITECTURE.md. Tests P1
to P5 are named for the architecture's list. P2 and P3 run ARAIL's own
AeroLLMBackend over the fake runtime, so the verdict is set by the real hook.
"""
from __future__ import annotations

import pytest

from arail import agent_context, agent_trace
from arail import deep_verdict as dv
from arail.agent_context import AgentHeldError
from arail.agents import deep_policy
from arail.router.backends import BaseBackend, ModelResponse
from arail.router.core import ModelRouter
from tests.fakes.fake_deep_runtime import fake_deep  # noqa: F401  (fixture)
from tests.test_deep_verdict import probe_result


@pytest.fixture()
def gates(monkeypatch):
    """Maximus, kill switch off, every background gate open."""
    monkeypatch.delenv("ARAIL_AGENT_DEEP", raising=False)
    monkeypatch.setenv("LAB_TIER", "maximus")
    from arail import runtime_profile, scheduler
    from arail.router import mlx_guard
    monkeypatch.setattr(scheduler, "jobs_halted", lambda: False)
    monkeypatch.setattr(scheduler, "current_window", lambda *a, **k: "heavy")
    monkeypatch.setattr(runtime_profile, "resolve", lambda *a, **k: ("overnight", "t"))
    monkeypatch.setattr(runtime_profile, "params", lambda p: {"background_aerollm": True})
    monkeypatch.setattr(mlx_guard, "metal_memory_pressure", lambda: 0.10)
    return monkeypatch


class _FastBackend(BaseBackend):
    """Stands in for the fast model. Counts calls; answers 'fast answer'."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, prompt, max_tokens=512, temperature=0.7, top_p=None, *, system=None, messages=None):
        self.calls += 1
        return ModelResponse(text="fast answer", model="stub-fast", tokens_used=2, backend="stub",
                             latency_ms=1.0, cost_usd=0.0)

    def health_check(self) -> bool:
        return True


@pytest.fixture()
def fast():
    be = _FastBackend()
    router = ModelRouter.from_backend(be, "stub")
    router.be = be
    return router


def _traces():
    return [r for r in agent_trace.ring(200) if r.get("record_type") == "model_call"]


# ── P1: the reason code ────────────────────────────────────────────────────

def test_p1_deep_failing_for_both_foreground_values(fake_deep, gates):
    fake_deep("pass")
    assert deep_policy.explain(foreground=True) == (True, "ok", "deep inference available")
    dv.identity()
    dv.note_call(False, error="RuntimeError: boom", consumer="agent:buddy")
    detail = dv.current().detail
    for fg in (True, False):
        ok, code, got = deep_policy.explain(foreground=fg)
        assert (ok, code) == (False, "deep_failing")
        assert got == detail and "./arailctl deep probe" in got
        assert deep_policy.prefer_deep(foreground=fg) is False


def test_p1_earlier_codes_still_win_while_failing(fake_deep, gates):
    fake_deep("pass")
    dv.identity()
    dv.note_call(False, error="boom")
    gates.setenv("ARAIL_AGENT_DEEP", "0")
    assert deep_policy.explain(foreground=True)[1] == "disabled"
    gates.delenv("ARAIL_AGENT_DEEP")
    gates.setenv("LAB_TIER", "minimalist")
    assert deep_policy.explain(foreground=True)[1] == "tier_locked"
    gates.setenv("LAB_TIER", "maximus")
    gates.setattr(deep_policy, "_aerollm_importable", lambda: False)
    assert deep_policy.explain(foreground=False)[1] == "wheel_missing"


def test_p1_deep_failing_is_distinct_from_deferred_now(fake_deep, gates):
    fake_deep("pass")
    dv.identity()
    from arail import scheduler
    gates.setattr(scheduler, "current_window", lambda *a, **k: "active")
    assert deep_policy.explain(foreground=False)[1] == "deferred_now"
    dv.note_call(False, error="boom")
    assert deep_policy.explain(foreground=False)[1] == "deep_failing", "not working outranks not now"
    assert deep_policy.explain(foreground=True)[1] == "deep_failing"


def test_p1_a_passing_probe_makes_deep_ok_again(fake_deep, gates):
    fake_deep("pass")
    b = dv.identity()
    dv.note_call(False, error="boom")
    assert deep_policy.explain(foreground=True)[1] == "deep_failing"
    dv.record_probe(probe_result(b, "pass", passed=5, started_ts=dv._now() + 1))
    assert deep_policy.explain(foreground=True)[:2] == (True, "ok")


def test_p1_untested_and_passing_leave_every_existing_code_alone(fake_deep, gates):
    fake_deep("pass")
    assert deep_policy.explain(foreground=False) == (True, "ok", "deep passes will run in the background")
    b = dv.identity()
    dv.record_probe(probe_result(b, "pass", passed=5, started_ts=dv._now()))
    assert deep_policy.explain(foreground=True) == (True, "ok", "deep inference available")


def test_p1_a_store_that_cannot_be_read_is_deep_failing(fake_deep, gates):
    fake_deep("pass")
    dv.identity()
    dv.state_dir().mkdir(parents=True, exist_ok=True)
    (dv.state_dir() / "verdict.json").write_text("{broken")
    ok, code, detail = deep_policy.explain(foreground=True)
    assert (ok, code) == (False, "deep_failing") and "unreadable" in detail


# ── P2, P3: the fast path, stamped; no second deep attempt ─────────────────

def _run_three(fd, fast_router):
    outs = []
    with agent_context.agent_call("buddy", brain="standard", effort="low", foreground=False):
        for i in (1, 2, 3):
            outs.append(deep_policy.complete_preferring_deep(f"q{i}", foreground=True,
                                                             fast_router=fast_router))
    return outs


def test_p2_call_2_goes_fast_and_stamped_and_call_3_makes_no_deep_attempt(fake_deep, gates, fast):
    fd = fake_deep("fail2")
    outs = _run_three(fd, fast)
    assert outs[0].startswith("fake deep answer 1")
    assert outs[1] == "fast answer" and outs[2] == "fast answer"
    assert len(fd.gen_calls()) == 2, "call 3 made no deep attempt"
    assert fast.be.calls == 2
    assert deep_policy.counters()["fast_instead_of_deep"] == 1
    assert dv.current().state == "failing"

    fast_traces = [t for t in _traces() if t.get("backend") == "stub"]
    assert len(fast_traces) == 2
    for t in fast_traces:
        assert t["deep_reason_code"] == "deep_failing"
        assert "deep generation is failing on this build" in t["deep_reason_detail"]
        assert t["agent_id"] == "buddy" and t["brain"] == "standard" and t["effort"] == "low"
        assert t["outcome"] == "ok"
    # the error path records no backend field, so pick deep traces by outcome as well
    deep_traces = [t for t in _traces()
                   if t.get("backend") == "aerollm" or t.get("error_class") == "RuntimeError"]
    assert [t["outcome"] for t in deep_traces] == ["ok", "error"]
    assert deep_traces[1]["error_class"] == "RuntimeError"
    assert deep_traces[0]["deep_reason_code"] is None, "an answered deep call carries no reason"


def test_p3_empty_text_behaves_the_same(fake_deep, gates, fast):
    fd = fake_deep("empty2")
    outs = _run_three(fd, fast)
    assert outs[0].startswith("fake deep answer 1")
    assert outs[1] == "fast answer" and outs[2] == "fast answer"
    assert len(fd.gen_calls()) == 2
    v = dv.current()
    assert v.state == "failing" and "no text" in v.failure["error"]
    stamped = [t for t in _traces() if t.get("backend") == "stub"]
    assert len(stamped) == 2 and all(t["deep_reason_code"] == "deep_failing" for t in stamped)


def test_p2_the_registry_still_hears_about_a_raise(fake_deep, gates, fast, monkeypatch):
    from arail.registry import get_registry
    fake_deep("fail2")
    seen = []
    monkeypatch.setattr(get_registry(), "report_failure", lambda eid, exc: seen.append((eid, type(exc).__name__)))
    _run_three(None, fast)
    assert seen == [("tier1-aerollm", "RuntimeError")]


def test_other_non_ok_codes_are_not_stamped_this_sprint(fake_deep, gates, fast):
    fake_deep("pass")
    gates.setenv("LAB_TIER", "minimalist")
    with agent_context.agent_call("buddy", foreground=False):
        assert deep_policy.complete_preferring_deep("q", foreground=True, fast_router=fast) == "fast answer"
    t = [r for r in _traces() if r.get("backend") == "stub"][-1]
    assert t["deep_reason_code"] is None
    assert deep_policy.counters()["fast_instead_of_deep"] == 0


# ── P4: a held agent is unchanged ──────────────────────────────────────────

def test_p4_agent_held_error_is_unchanged(gates):
    class Held:
        def complete(self, *a, **k):
            raise AgentHeldError("agents are held")

    called = {"fast": 0}

    class Fast:
        def complete(self, *a, **k):
            called["fast"] += 1

    gates.setattr(deep_policy, "_aerollm_importable", lambda: True)
    gates.setattr(deep_policy, "get_deep_router", lambda: Held())
    assert deep_policy.complete_preferring_deep("q", foreground=True, fast_router=Fast()) is None
    assert called["fast"] == 0


# ── P5: with_deep_reason keeps everything else ─────────────────────────────

def test_p5_with_deep_reason_keeps_the_trace_id_brain_effort_and_foreground():
    with agent_context.agent_call("buddy", brain="standard", effort="low", foreground=False) as outer:
        with agent_context.with_deep_reason("deep_failing", "why") as inner:
            assert inner is agent_context.current()
            assert inner.trace_id == outer.trace_id
            assert (inner.brain, inner.effort, inner.foreground) == ("standard", "low", False)
            assert (inner.agent_id, inner.kind, inner.label) == ("buddy", "agent", "buddy")
            assert (inner.deep_reason_code, inner.deep_reason_detail) == ("deep_failing", "why")
        assert agent_context.current() is outer, "the outer record is restored"
        assert outer.deep_reason_code is None, "frozen: the outer record was never mutated"


def test_p5_with_deep_reason_does_nothing_without_a_context():
    assert agent_context.current() is None
    with agent_context.with_deep_reason("deep_failing", "why") as got:
        assert got is None
        assert agent_context.current() is None


def test_p5_a_system_call_can_be_stamped_too():
    with agent_context.system_call("world-review") as outer:
        with agent_context.with_deep_reason("deep_failing", "d") as inner:
            assert inner.kind == "system" and inner.label == "world-review"
            assert inner.trace_id == outer.trace_id



# QA-16 (fix pass 4): when the verdict is not failing, a fast call made because a deep attempt just raised carries a
# plain sentence in its trace, built from the exception text as raised.

def test_qa16_the_plain_fallback_detail_is_printable_and_one_line(fake_deep, monkeypatch):
    fake_deep()
    monkeypatch.setattr(deep_policy, "_deep_failing_detail", lambda: None)
    detail = deep_policy._failed_detail("RuntimeError: \x1b[2K\x1b[1Ainjected\x07\x9b2K\x7f\nsecond line")
    bad = sorted({hex(ord(c)) for c in detail if ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F})
    assert not bad, f"{bad}: {detail!r}"
    assert detail.startswith("deep generation failed: RuntimeError: ?[2K") and "second line" in detail
    assert len(detail) <= len("deep generation failed: ") + 160
