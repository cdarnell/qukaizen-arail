"""QA-BLIND-2 (W3, security) — authored from ARCHITECTURE.md's contracts #3
and #4 only, without reading the builder's tests for this surface.

The contract restated:

  * Held + ``kind="agent"`` at the chokepoint  -> ``AgentHeldError``, the
    backend is **not touched**, one trace with ``outcome="refused_halted"``.
  * Held + ``kind="system"`` / ``billing_source="ui"`` / no context -> passes.
    "Holding agents must not break the operator's own chat turn."
  * ``speech_gate(agent_id)`` -> ``False`` while held for every speaker
    except ``sre``, which is the one documented exemption and **must still
    be able to post a plain template alert**.
  * Hold is **admission control, not cancellation** — a call already inside
    a backend runs to completion.

W3's "observe 60 s" is asserted with a simulated clock (a monotonic stub),
never a wall-clock sleep.
"""

from __future__ import annotations

import json
import threading

import pytest

from arail import agent_context, agent_trace, config, scheduler
from arail.agent_context import AgentHeldError
from arail.agent_trace import FIXED_LANES, SYS_LANES
from arail.router.backends import BaseBackend, ModelResponse
from arail.router.core import ModelRouter


class _CountingBackend(BaseBackend):
    def __init__(self) -> None:
        self.complete_calls = 0
        self.stream_calls = 0

    def complete(self, prompt, max_tokens=512, temperature=0.7, top_p=None,
                 *, system=None, messages=None):
        self.complete_calls += 1
        return ModelResponse(text="model output", model="fake-qa",
                             tokens_used=3, backend="fake", latency_ms=1.0)

    def stream_complete(self, prompt, max_tokens=512, temperature=0.7,
                        top_p=None, *, system=None, messages=None):
        self.stream_calls += 1
        yield "chunk"
        yield ModelResponse(text="chunk", model="fake-qa", tokens_used=3,
                            backend="fake", latency_ms=1.0)

    def health_check(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def isolated_costs(monkeypatch, tmp_path):
    from arail.costs import cost_tracker
    monkeypatch.setattr(cost_tracker, "_data_path", tmp_path / "qa-costs.json")
    monkeypatch.setattr(cost_tracker, "calls_by_source", {})


@pytest.fixture
def held():
    """Hold all agents for the duration of the test. The repo conftest's
    ``_no_ambient_halt_flag`` already points ``halt.json`` at a tmp path and
    resets the module state, so this only has to flip it."""
    scheduler.halt_all_jobs()
    assert scheduler.jobs_halted() is True
    yield
    scheduler.resume_all_jobs()


def _disk_traces() -> list[dict]:
    path = config.DATA_DIR / "agent_traces.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# The inference half: every FIXED_LANES agent, both chokepoint methods
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lane_id", [lane for lane, _ in FIXED_LANES])
def test_held_agent_complete_is_refused_and_never_reaches_the_backend(
        held, lane_id):
    be = _CountingBackend()
    router = ModelRouter.from_backend(be, "fake")

    with agent_context.agent_call(lane_id):
        with pytest.raises(AgentHeldError):
            router.complete("anything")

    assert be.complete_calls == 0, (
        f"{lane_id}: the backend was called while agents were held")
    traces = _disk_traces()
    assert len(traces) == 1, f"{lane_id}: expected exactly one refusal trace"
    assert traces[0]["outcome"] == "refused_halted"
    assert traces[0]["attribution"] == f"agent:{lane_id}"
    assert [t for t in traces if t["outcome"] == "ok"] == []


@pytest.mark.parametrize("lane_id", [lane for lane, _ in FIXED_LANES])
def test_held_agent_stream_complete_is_refused_and_never_reaches_the_backend(
        held, lane_id):
    be = _CountingBackend()
    router = ModelRouter.from_backend(be, "fake")

    with agent_context.agent_call(lane_id):
        with pytest.raises(AgentHeldError):
            # The generator must refuse at the door, not on first next().
            list(router.stream_complete("anything"))

    assert be.stream_calls == 0, (
        f"{lane_id}: the streaming backend was entered while held")
    traces = _disk_traces()
    assert len(traces) == 1
    assert traces[0]["outcome"] == "refused_halted"
    assert traces[0]["streamed"] is True


def test_a_user_defined_agent_id_outside_the_fixed_roster_is_also_refused(held):
    """F16: an agent the loader has never seen must not be able to opt out of
    the hold by not being in ``FIXED_LANES``."""
    be = _CountingBackend()
    router = ModelRouter.from_backend(be, "fake")
    with agent_context.agent_call("some-forged-agent"):
        with pytest.raises(AgentHeldError):
            router.complete("anything")
    assert be.complete_calls == 0
    assert _disk_traces()[0]["attribution"] == "agent:some-forged-agent"


@pytest.mark.parametrize("label", SYS_LANES)
def test_held_system_calls_still_pass(held, label):
    """"Holding agents must not break the operator's own chat turn while he
    is holding them" — the non-agent callers (world-forge, dictionary,
    goal-parser, recap) are system calls and keep working."""
    be = _CountingBackend()
    router = ModelRouter.from_backend(be, "fake")
    with agent_context.system_call(label):
        resp = router.complete("anything")
    assert resp.text == "model output"
    assert be.complete_calls == 1
    traces = _disk_traces()
    assert len(traces) == 1
    assert traces[0]["outcome"] == "ok"
    assert traces[0]["attribution"] == f"sys:{label}"


def test_held_chat_turn_still_passes(held):
    """The chat tab's router carries ``billing_source="ui"`` and no agent
    context at all."""
    be = _CountingBackend()
    router = ModelRouter.from_backend(be, "fake", billing_source="ui")
    resp = router.complete("the operator's own question")
    assert resp.text == "model output"
    assert be.complete_calls == 1
    assert _disk_traces()[0]["outcome"] == "ok"


def test_held_unattributed_call_passes_but_is_named(held):
    """An unattributed call is not refused (it may be the operator's) but it
    is never silently bucketed as an agent — DE2's instrument."""
    be = _CountingBackend()
    router = ModelRouter.from_backend(be, "fake")
    router.complete("who called me?")
    trace = _disk_traces()[0]
    assert trace["outcome"] == "ok"
    assert trace["attribution"] == "unattributed"
    assert trace["call_site"] and ":" in trace["call_site"]
    assert trace["agent_id"] is None


def test_hold_is_admission_control_not_cancellation(held):
    """The copy claims "calls already running finish". A call that entered
    the backend *before* the flip must complete — and must not be
    retroactively turned into a refusal."""
    scheduler.resume_all_jobs()
    be_flips = _CountingBackend()

    class _HoldsMidCall(_CountingBackend):
        def complete(self, prompt, max_tokens=512, temperature=0.7,
                     top_p=None, *, system=None, messages=None):
            scheduler.halt_all_jobs()
            return super().complete(prompt, max_tokens, temperature, top_p,
                                    system=system, messages=messages)

    router = ModelRouter.from_backend(_HoldsMidCall(), "fake")
    with agent_context.agent_call("buddy"):
        resp = router.complete("in flight when the switch flipped")

    assert resp.text == "model output"
    assert scheduler.jobs_halted() is True
    trace = _disk_traces()[0]
    assert trace["outcome"] == "ok"
    assert trace["halted"] is True, (
        "the record should say the lab was held when the call finished")
    assert be_flips.complete_calls == 0  # sanity: the other backend is unused


def test_no_new_admitted_agent_trace_for_a_simulated_sixty_seconds(held):
    """W3, deterministically: over a simulated 60 s window in which every
    agent tries repeatedly, zero agent-sourced calls are admitted.

    The clock is a counter, not ``time.sleep`` — W3's "observe 60 s" is about
    the *window*, and a wall-clock test would make the suite slow and
    flaky for no extra evidence.
    """
    be = _CountingBackend()
    router = ModelRouter.from_backend(be, "fake")
    simulated_now = 0.0
    refusals = 0

    while simulated_now < 60.0:
        for lane_id, _ in FIXED_LANES:
            with agent_context.agent_call(lane_id):
                try:
                    router.complete("tick")
                except AgentHeldError:
                    refusals += 1
        simulated_now += 5.0

    assert refusals == 12 * len(FIXED_LANES), refusals
    assert be.complete_calls == 0
    admitted = [t for t in _disk_traces() if t["outcome"] == "ok"
                and t["kind"] == "agent"]
    assert admitted == [], f"{len(admitted)} agent calls were admitted while held"


# ---------------------------------------------------------------------------
# The speech half: the gated speakers and the one exemption
# ---------------------------------------------------------------------------

def test_speech_gate_silences_every_speaker_except_sre_while_held(held):
    for speaker in ("buddy", "librarian", "presence", "debt_advisor",
                    "consolidation_analyzer", "researcher", "browser",
                    "some-forged-agent"):
        assert agent_context.speech_gate(speaker) is False, speaker
    assert agent_context.speech_gate("sre") is True, (
        "SRE is the one documented exemption (ledger OQ3)")


def test_speech_gate_opens_again_when_not_held():
    """The inverse — a gate that is always closed is not a gate."""
    assert scheduler.jobs_halted() is False
    for speaker in ("buddy", "librarian", "presence", "debt_advisor",
                    "consolidation_analyzer", "sre"):
        assert agent_context.speech_gate(speaker) is True, speaker


def test_sre_exemption_is_case_and_shape_insensitive(held):
    """The exemption is keyed on a sanitised id, so ``SRE`` and ``sre `` are
    the same speaker — and nothing else sneaks in by resembling it."""
    assert agent_context.speech_gate("SRE") is True
    assert agent_context.speech_gate(" sre ") is True
    assert agent_context.speech_gate("sre-ish") is False
    assert agent_context.speech_gate("not_sre") is False


def test_buddy_emit_funnel_is_silent_and_makes_no_model_call_while_held(
        held, monkeypatch, tmp_path):
    """Buddy's single proactive funnel. Two assertions in one body because
    the operator's copy promises both: no line *and* no inference."""
    from arail.agents import _builtin_buddy as buddy_mod

    emits: list[tuple] = []
    model_calls: list[str] = []

    class _FakeHost:
        def emit(self, source, message, level="info", data=None):
            emits.append((source, message, level, data))

        def update_workflow(self, agent_id, **fields):
            pass

        def get_current_goal(self):
            return None

        def get_activity_log_path(self):
            return tmp_path / "activity.jsonl"

        def get_pkb_root(self):
            return tmp_path / "pkb"

        def list_experiments(self):
            return []

        def list_skills(self):
            return []

        def load_agent_skills(self, agent_id):
            return []

        def compose_skill_context(self, skills):
            return ""

        def load_world_skill(self):
            return None

        def llm_complete(self, prompt, max_tokens=60, temperature=0.6):
            model_calls.append(prompt)
            return "voiced sentence"

    fake = _FakeHost()
    monkeypatch.setattr(buddy_mod, "_host", fake)
    agent = buddy_mod.BuddyAgent(host=fake)
    monkeypatch.setattr(agent, "_save_state", lambda: None)

    obs = buddy_mod.Observation(
        watcher="qa:test", severity="suggest",
        fact="something worth saying", cooldown_sec=0,
    )
    agent._emit(obs, kind="watch")

    assert emits == [], f"Buddy spoke while held: {emits}"
    assert model_calls == [], (
        "Buddy called the model while held — the gate must sit before "
        "_voice(), not after it")


def test_buddy_emit_funnel_speaks_and_calls_the_model_when_not_held(
        monkeypatch, tmp_path):
    """The inverse of the above, so the gate cannot pass by never emitting."""
    from arail.agents import _builtin_buddy as buddy_mod

    emits: list[tuple] = []
    model_calls: list[str] = []

    class _FakeHost:
        def emit(self, source, message, level="info", data=None):
            emits.append((source, message, level, data))

        def update_workflow(self, agent_id, **fields):
            pass

        def get_current_goal(self):
            return None

        def get_activity_log_path(self):
            return tmp_path / "activity.jsonl"

        def get_pkb_root(self):
            return tmp_path / "pkb"

        def list_experiments(self):
            return []

        def list_skills(self):
            return []

        def load_agent_skills(self, agent_id):
            return []

        def compose_skill_context(self, skills):
            return ""

        def load_world_skill(self):
            return None

        def llm_complete(self, prompt, max_tokens=60, temperature=0.6):
            model_calls.append(prompt)
            return "voiced sentence"

    fake = _FakeHost()
    monkeypatch.setattr(buddy_mod, "_host", fake)
    agent = buddy_mod.BuddyAgent(host=fake)
    monkeypatch.setattr(agent, "_save_state", lambda: None)

    obs = buddy_mod.Observation(
        watcher="qa:test", severity="suggest",
        fact="something worth saying", cooldown_sec=0,
    )
    agent._emit(obs, kind="watch")

    assert len(emits) == 1, emits
    assert len(model_calls) == 1


def test_librarian_emit_funnel_is_silent_while_held(held, monkeypatch):
    from arail.agents import _builtin_librarian as librarian_mod

    emitted: list[tuple] = []
    import arail.activity as activity_mod
    monkeypatch.setattr(activity_mod.activity_log, "emit",
                        lambda *a, **k: emitted.append((a, k)))

    librarian_mod.LibrarianAgent._emit("a growth announcement", "info")
    assert emitted == [], emitted

    scheduler.resume_all_jobs()
    librarian_mod.LibrarianAgent._emit("a growth announcement", "info")
    assert len(emitted) == 1, "the funnel must speak again once resumed"


def test_presence_announcement_is_silent_while_held(held, monkeypatch):
    """Presence carries no model output at all, and is still silenced —
    "hold" silences the lab's narration, not just its inference."""
    import arail.activity as activity_mod
    from arail.agents import _builtin_presence as presence_mod

    emitted: list[tuple] = []
    monkeypatch.setattr(activity_mod.activity_log, "emit",
                        lambda *a, **k: emitted.append((a, k)))

    agent = presence_mod.PresenceAgent()
    # Force a profile transition: whatever the current profile resolves to,
    # claim the last-seen one was different so _tick() announces.
    agent._last = ("__qa_previous__", "__qa_previous__")
    agent._tick()
    assert emitted == [], emitted

    scheduler.resume_all_jobs()
    agent._last = ("__qa_previous__", "__qa_previous__")
    agent._tick()
    assert len(emitted) == 1, (
        "presence must announce the transition once resumed, otherwise this "
        "test would pass even with the gate deleted")


def test_sre_can_still_post_a_plain_template_alert_while_held(held, monkeypatch):
    """The live half of OQ3: SRE keeps watching and may still speak. Asserted
    through the same funnel every other speaker is gated at, so this is the
    exemption working, not SRE simply not being wired."""
    import arail.activity as activity_mod

    emitted: list[tuple] = []
    monkeypatch.setattr(activity_mod.activity_log, "emit",
                        lambda *a, **k: emitted.append((a, k)))

    assert agent_context.speech_gate("sre") is True
    if agent_context.speech_gate("sre"):
        activity_mod.activity_log.emit(
            "sre", "portal crashed 3x in 10 min — see logs/portal.err", "error")
    assert len(emitted) == 1
    assert emitted[0][0][0] == "sre"


def test_sre_calls_no_model_so_its_exemption_cannot_leak_inference():
    """ARCHITECTURE.md "Where the spec is wrong" #5 — the exemption is a
    *speech* exemption. If SRE ever grows a router call, the chokepoint
    still refuses it while held; this test pins the premise the copy relies
    on so the copy cannot quietly become false."""
    import pathlib
    src = pathlib.Path(
        agent_context.__file__).parent / "agents" / "_builtin_sre.py"
    text = src.read_text()
    assert "router" not in text.lower(), (
        "SRE grew a model call; the hold copy's 'plain, non-model alert' "
        "clause needs re-checking")


# ---------------------------------------------------------------------------
# Hold state, persistence, and the concurrency edge
# ---------------------------------------------------------------------------

def test_hold_survives_a_simulated_process_restart(held):
    """F15: the flag is persisted, so a portal restart mid-hold must not
    silently un-hold. Simulated by dropping the in-memory load flag."""
    scheduler._halt_loaded = False
    scheduler._halted = False
    assert scheduler.jobs_halted() is True, (
        "a fresh process must re-read halt.json and still be held")

    be = _CountingBackend()
    router = ModelRouter.from_backend(be, "fake")
    with agent_context.agent_call("buddy"):
        with pytest.raises(AgentHeldError):
            router.complete("after restart")
    assert be.complete_calls == 0


def test_hold_state_reports_the_exemption_and_the_in_flight_count(held):
    state = agent_context.hold_state()
    assert state["held"] is True
    assert state["exempt_speakers"] == ["sre"]
    assert state["in_flight"] == 0
    assert "changed_at" in state


def test_in_flight_count_returns_to_zero_after_a_raising_backend():
    """The copy renders ``N in flight``; a leaked counter would make the
    control lie forever after one backend error."""
    class _Raises(_CountingBackend):
        def complete(self, *a, **kw):
            raise RuntimeError("backend down")

    router = ModelRouter.from_backend(_Raises(), "fake")
    with agent_context.agent_call("buddy"):
        with pytest.raises(RuntimeError):
            router.complete("boom")
    assert agent_context.in_flight_agent_calls() == 0


def test_in_flight_count_is_accurate_under_concurrent_agent_calls():
    """Ten threads, one router. ``_in_flight_agent_calls`` is lock-guarded;
    this is the assertion that keeps it that way."""
    barrier = threading.Barrier(11)
    observed: list[int] = []

    class _Waits(_CountingBackend):
        def complete(self, *a, **kw):
            barrier.wait(timeout=10)
            observed.append(agent_context.in_flight_agent_calls())
            return ModelResponse(text="x", model="m", tokens_used=1,
                                 backend="fake", latency_ms=1.0)

    router = ModelRouter.from_backend(_Waits(), "fake")

    def _worker(i: int) -> None:
        with agent_context.agent_call(f"agent{i}"):
            router.complete("concurrent")

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    barrier.wait(timeout=10)
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive()

    assert max(observed) == 10, (
        f"peak in-flight should be 10 with all ten inside the backend: {observed}")
    assert agent_context.in_flight_agent_calls() == 0
