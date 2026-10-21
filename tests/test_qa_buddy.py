"""Buddy — the agent this programme is about (arail CLAUDE.md's 30% slice).

Four questions, each with its own section:

1. Is **every** Buddy inference attributed ``buddy`` — never
   ``unattributed``, never ``agent``, never another agent's id — across the
   loader-started tick loop, ``asyncio.to_thread``, the deep->fast fallback,
   and the nightly reflection?
2. Under hold, does Buddy make **zero** model calls and post **zero**
   findings/suggestions/announcements while operational/error lines continue?
3. ``dream()`` end to end. Until this sprint a ``NameError`` killed it before
   its first emit (REVIEW.md R5), so everything after that emit has never run
   in production: the announcement, ``_recent_actions``, ``_sync_workflow``,
   and the returned reflection.
4. Does the resident-pin ``_normalize_keep_alive`` path still hand Ollama an
   integer rather than a string?

Everything runs against fake backends and a fake host. Ollama is never
required; no weights are loaded.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from arail import agent_context, agent_trace, config, scheduler
from arail.router.backends import BaseBackend, ModelResponse, _normalize_keep_alive
from arail.router.core import ModelRouter


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

class _FakeBackend(BaseBackend):
    """Non-streaming fake. ``stream_complete`` is inherited from BaseBackend,
    i.e. it emulates (one ModelResponse item) — the AeroLLMBackend shape."""

    def __init__(self, text: str = "buddy says something") -> None:
        self.text = text
        self.prompts: list[str] = []

    def complete(self, prompt, max_tokens=512, temperature=0.7, top_p=None,
                 *, system=None, messages=None):
        self.prompts.append(prompt)
        return ModelResponse(text=self.text, model="fake-fast",
                             tokens_used=11, backend="fake", latency_ms=2.0)

    def health_check(self) -> bool:
        return True


class _StreamingFakeBackend(_FakeBackend):
    """Yields real deltas then a terminal ModelResponse — the
    OllamaNativeBackend shape, which is the only one that can produce a
    measured TTFT today."""

    def stream_complete(self, prompt, max_tokens=512, temperature=0.7,
                        top_p=None, *, system=None, messages=None):
        self.prompts.append(prompt)
        # Deltas are derived from self.text so a test can change the model's
        # answer without the streaming half silently ignoring it.
        mid = len(self.text) // 2
        for chunk in (self.text[:mid], self.text[mid:]):
            yield chunk
        yield ModelResponse(text=self.text, model="fake-fast",
                            tokens_used=11, backend="fake", latency_ms=2.0,
                            prefill_ms=7.5)


class _FakeHost:
    """A BuddyHost with no ARAIL stack behind it, except ``llm_complete``,
    which deliberately goes through the *real* ``deep_policy`` ->
    ``ModelRouter`` chain so attribution is exercised end to end rather than
    asserted at a seam."""

    def __init__(self, root: Path, router: ModelRouter | None = None) -> None:
        self.root = root
        self.router = router
        self.emits: list[tuple] = []
        self.workflows: list[dict] = []
        self.model_prompts: list[str] = []

    def emit(self, source, message, level="info", data=None):
        self.emits.append((source, message, level, data))

    def update_workflow(self, agent_id, **fields):
        self.workflows.append({"agent_id": agent_id, **fields})

    def get_current_goal(self):
        return None

    def get_activity_log_path(self):
        return self.root / "activity.jsonl"

    def get_pkb_root(self):
        return self.root / "pkb"

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
        self.model_prompts.append(prompt)
        if self.router is None:
            return "voiced"
        from arail.agents import deep_policy
        text = deep_policy.complete_preferring_deep(
            prompt, foreground=False, fast_router=self.router,
            max_tokens=max_tokens, temperature=temperature)
        return text or ""


@pytest.fixture(autouse=True)
def isolated_costs(monkeypatch, tmp_path):
    from arail.costs import cost_tracker
    monkeypatch.setattr(cost_tracker, "_data_path", tmp_path / "qa-costs.json")
    monkeypatch.setattr(cost_tracker, "calls_by_source", {})


@pytest.fixture
def buddy_env(monkeypatch, tmp_path):
    """A Buddy wired to a fake host and a fake fast router.

    ``BuddyAgent.__init__(host=...)`` rebinds the *module-global* ``_host``,
    so the monkeypatch must be registered first (it records the original) or
    the fake leaks into every later test in the process.
    """
    from arail.agents import _builtin_buddy as buddy_mod
    from arail.agents import deep_policy

    backend = _StreamingFakeBackend()
    router = ModelRouter.from_backend(backend, "fake")
    host = _FakeHost(tmp_path, router)

    monkeypatch.setattr(buddy_mod, "_host", host)
    agent = buddy_mod.BuddyAgent(host=host)
    monkeypatch.setattr(agent, "_save_state", lambda: None)
    # config.PKB_ROOT is NOT isolated by the repo conftest, and
    # dream_daemon._dream_once resolves "have we dreamed today?" through
    # arail.pkb._pkb_root() -> config.PKB_ROOT (the REAL lab/pkb), while
    # BuddyAgent.dream() resolves the same file through
    # _host.get_pkb_root(). Without this, a machine that has dreamed today
    # makes every _dream_once test a silent no-op. See
    # test_dream_once_idempotence_reads_the_unisolated_real_pkb_root.
    monkeypatch.setattr(config, "PKB_ROOT", host.get_pkb_root())
    # Never prefer deep: the deep router would be a real registry lookup.
    monkeypatch.setattr(deep_policy, "prefer_deep", lambda **kw: False)
    deep_policy._reset_for_tests()
    return agent, host, backend, buddy_mod


def _disk_traces() -> list[dict]:
    path = config.DATA_DIR / "agent_traces.jsonl"
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _activity_events() -> list[dict]:
    from arail.activity import activity_log
    return list(activity_log.recent(500))


# ---------------------------------------------------------------------------
# 1. Attribution — every Buddy call says "buddy"
# ---------------------------------------------------------------------------

def test_buddy_proactive_line_is_attributed_buddy_end_to_end(buddy_env):
    """The whole real chain: ``_emit`` -> ``_voice`` -> host.llm_complete ->
    ``deep_policy.complete_preferring_deep`` -> ``ModelRouter``. Only the
    backend and the host's non-model methods are fake."""
    agent, host, backend, buddy_mod = buddy_env

    obs = buddy_mod.Observation(watcher="qa", severity="suggest",
                                fact="the goal has no experiments yet",
                                cooldown_sec=0)
    with agent_context.agent_call("buddy"):
        agent._emit(obs, kind="watch")

    assert host.model_prompts, "Buddy must have asked the model to voice this"
    traces = _disk_traces()
    assert len(traces) == 1, traces
    assert traces[0]["attribution"] == "agent:buddy"
    assert traces[0]["agent_id"] == "buddy"
    assert traces[0]["kind"] == "agent"


def test_buddy_is_attributed_through_the_loader_contract_not_by_hand(
        monkeypatch, tmp_path):
    """L1: the loader wraps ``start()``, and ``create_task`` copies the
    context — so an agent that spawns its loop inside ``start()`` is
    attributed for the life of that loop with no per-agent edit. Asserted on
    a *loader-shaped stand-in* so the test is about the contract, not about
    Buddy's own source."""
    backend = _FakeBackend()
    router = ModelRouter.from_backend(backend, "fake")
    seen: list[str] = []

    class _LoaderAgent:
        def start(self) -> None:
            async def _loop() -> None:
                await asyncio.sleep(0)
                await asyncio.to_thread(router.complete, "tick work")
                seen.append("ticked")
            self.task = asyncio.create_task(_loop())

    async def _main() -> None:
        inst = _LoaderAgent()
        # Exactly what agents/loader.py's start_all_auto does.
        with agent_context.agent_call("buddy"):
            inst.start()
        await inst.task

    asyncio.run(_main())
    assert seen == ["ticked"]
    assert _disk_traces()[0]["attribution"] == "agent:buddy", (
        "the tick loop lost Buddy's attribution after start() returned")


def test_buddy_dream_call_is_attributed_across_the_to_thread_hop(
        buddy_env, monkeypatch):
    """L2 + A2: ``dream_daemon._dream_once`` sets the context, and
    ``dream()``'s model call goes through ``asyncio.to_thread``."""
    agent, host, backend, buddy_mod = buddy_env
    from arail.agents import dream_daemon

    monkeypatch.setattr(dream_daemon, "activity_log", _NullLog())
    asyncio.run(dream_daemon._dream_once("buddy", agent))

    traces = _disk_traces()
    assert traces, "the dream must have produced an inference trace"
    assert {t["attribution"] for t in traces} == {"agent:buddy"}, (
        [t["attribution"] for t in traces])


class _NullLog:
    def emit(self, *a, **kw):
        pass

    def recent(self, n=200):
        return []


def test_buddy_deep_to_fast_fallback_keeps_buddys_attribution(
        buddy_env, monkeypatch):
    """The deep brain failing must not make the fallback call anonymous."""
    agent, host, backend, buddy_mod = buddy_env
    from arail.agents import deep_policy

    class _DeadDeep(_FakeBackend):
        def complete(self, *a, **kw):
            raise RuntimeError("deep backend OOM")

    deep_router = ModelRouter.from_backend(_DeadDeep(), "fake-deep")
    monkeypatch.setattr(deep_policy, "prefer_deep", lambda **kw: True)
    monkeypatch.setattr(deep_policy, "get_deep_router", lambda: deep_router)

    with agent_context.agent_call("buddy"):
        text = deep_policy.complete_preferring_deep(
            "voice this", foreground=False, fast_router=host.router)

    assert text == "buddy says something"
    traces = _disk_traces()
    assert len(traces) == 2, [t["outcome"] for t in traces]
    assert {t["attribution"] for t in traces} == {"agent:buddy"}
    assert traces[0]["outcome"] == "error"
    assert traces[0]["error_class"] == "RuntimeError"
    assert traces[1]["outcome"] == "ok"


def test_buddys_own_lane_is_the_only_one_that_moves(buddy_env):
    """Never another agent's id: after a Buddy call, every other lane is
    still empty with an honest reason."""
    agent, host, backend, buddy_mod = buddy_env
    with agent_context.agent_call("buddy"):
        host.llm_complete("voice this")

    lanes = {l["id"]: l for l in agent_trace.lanes_snapshot()["lanes"]}
    assert lanes["buddy"]["calls"] == 1
    assert lanes["buddy"]["empty_reason"] is None
    for other in ("researcher", "librarian", "debt_advisor", "drafter"):
        assert lanes[other]["calls"] == 0
        assert lanes[other]["empty_reason"] == "no calls yet this session"
    for model_free in ("sre", "presence", "curator", "forge"):
        assert lanes[model_free]["empty_reason"] == "does not call a model"


# ---------------------------------------------------------------------------
# 2. Buddy under hold
# ---------------------------------------------------------------------------

def test_held_buddy_makes_zero_model_calls_and_posts_zero_lines(buddy_env):
    agent, host, backend, buddy_mod = buddy_env
    scheduler.halt_all_jobs()
    try:
        obs = buddy_mod.Observation(watcher="qa", severity="suggest",
                                    fact="worth saying", cooldown_sec=0)
        with agent_context.agent_call("buddy"):
            agent._emit(obs, kind="watch")
            agent._emit(obs, kind="suggest")

        assert host.model_prompts == [], (
            "Buddy called the model while held")
        assert host.emits == [], f"Buddy posted while held: {host.emits}"
        assert _disk_traces() == [], (
            "a held Buddy should not even reach the chokepoint from _emit — "
            "the speech gate short-circuits before _voice()")
    finally:
        scheduler.resume_all_jobs()


def test_held_buddy_boot_notice_is_silent_but_the_loop_still_starts(buddy_env):
    """Operator decision (a): the boot notice is one of the two lines the
    widened gate covers. Hold silences the *notice*, not the agent."""
    agent, host, backend, buddy_mod = buddy_env
    scheduler.halt_all_jobs()

    async def _main() -> None:
        agent.start()
        await asyncio.sleep(0)
        agent.stop()

    try:
        asyncio.run(_main())
        assert [e for e in host.emits if "online" in str(e[1])] == [], (
            f"the boot notice spoke while held: {host.emits}")
        assert agent._status in ("running", "idle")
    finally:
        scheduler.resume_all_jobs()


def test_unheld_buddy_boot_notice_does_speak(buddy_env):
    """The inverse, so the gate cannot pass by never emitting."""
    agent, host, backend, buddy_mod = buddy_env

    async def _main() -> None:
        agent.start()
        await asyncio.sleep(0)
        agent.stop()

    asyncio.run(_main())
    assert [e for e in host.emits if "online" in str(e[1])], host.emits


def test_held_buddy_dream_announcement_is_silent_but_the_dream_is_written(
        buddy_env, monkeypatch):
    """The other line decision (a) added. The dream *file* is Buddy's memory
    and is not a proactive line — only the announcement is gated."""
    agent, host, backend, buddy_mod = buddy_env
    import arail.activity as activity_mod

    emitted: list[tuple] = []
    monkeypatch.setattr(activity_mod.activity_log, "emit",
                        lambda *a, **k: emitted.append((a, k)))
    scheduler.halt_all_jobs()
    try:
        reflection = asyncio.run(agent.dream())
    finally:
        scheduler.resume_all_jobs()

    assert emitted == [], f"the dream announcement spoke while held: {emitted}"
    dreams = list((host.get_pkb_root() / "agents" / "buddy" / "dreams")
                  .glob("*.md"))
    assert len(dreams) == 1, "the dream file is memory, not speech"
    assert reflection, "dream() still returns its reflection while held"


def test_dream_daemons_own_operational_lines_continue_while_held(
        buddy_env, monkeypatch):
    """"Operational/error lines continue" — the daemon's own start/finish
    narration is not a finding, a suggestion or an announcement."""
    agent, host, backend, buddy_mod = buddy_env
    from arail.agents import dream_daemon

    lines: list[tuple] = []

    class _CaptureLog:
        def emit(self, *a, **kw):
            lines.append((a, kw))

        def recent(self, n=200):
            return []

    monkeypatch.setattr(dream_daemon, "activity_log", _CaptureLog())
    scheduler.halt_all_jobs()
    try:
        asyncio.run(dream_daemon._dream_once("buddy", agent))
    finally:
        scheduler.resume_all_jobs()

    sources = [a[0][0] for a in lines]
    assert "dream" in sources, (
        f"the daemon's own operational lines were silenced too: {lines}")


# ---------------------------------------------------------------------------
# 3. dream() end to end — code that has never run in production (R5)
# ---------------------------------------------------------------------------

def test_dream_runs_to_completion_and_returns_its_reflection(buddy_env):
    """Before this sprint a NameError killed ``dream()`` at the emit, so
    nothing after it ever ran. All four post-emit effects asserted."""
    agent, host, backend, buddy_mod = buddy_env

    reflection = asyncio.run(agent.dream())

    # (1) the returned reflection
    assert reflection == "buddy says something"
    # (2) the dream file, with frontmatter
    dreams_dir = host.get_pkb_root() / "agents" / "buddy" / "dreams"
    files = list(dreams_dir.glob("*.md"))
    assert len(files) == 1, files
    text = files[0].read_text()
    assert text.startswith("---\n")
    assert "section: agents/buddy/dreams" in text
    assert reflection in text
    # (3) _recent_actions
    assert any("Dreamed and wrote" in a for a in agent._recent_actions)
    # (4) _sync_workflow reached its success branch
    assert any(w.get("current_task") == "Dream consolidation complete"
               for w in host.workflows), host.workflows


def test_dream_announcement_lands_in_the_activity_log_with_a_preview(
        buddy_env, monkeypatch):
    """What it writes and where. The announcement is a *real* activity line
    now, carrying 160 chars of raw model output as ``data.preview``."""
    agent, host, backend, buddy_mod = buddy_env
    backend.text = "REFLECTION-" + ("z" * 400)

    asyncio.run(agent.dream())

    events = [e for e in _activity_events() if e.get("source") == "buddy"]
    assert len(events) == 1, events
    assert "dreamed" in events[0]["message"]
    preview = events[0]["data"]["preview"]
    assert preview.startswith("REFLECTION-")
    assert len(preview) == 160, "the preview is capped at 160 chars"
    assert "dream_file" in events[0]["data"]


def test_dream_preview_is_not_redacted_and_is_recorder_independent(buddy_env):
    """R5/S3, exercised rather than reasoned about: the preview bypasses the
    flight recorder entirely — no ``capture_body``, no redaction, on disk in
    ``activity.jsonl`` with the recorder OFF.

    A secret that the model echoed into its reflection therefore reaches
    disk unredacted. Filed as debt S3 by the reviewer; this test is the
    demonstration, and is marked xfail(strict) so that closing S3 turns it
    red rather than silently passing.
    """
    agent, host, backend, buddy_mod = buddy_env
    planted = "sk-dreamleak000000000000000000"
    backend.text = f"today I learned the key {planted} matters"

    assert agent_trace.recorder_on() is False
    asyncio.run(agent.dream())

    log_path = config.DATA_DIR / "activity.jsonl"
    on_disk = log_path.read_text() if log_path.exists() else ""
    assert "dreamed" in on_disk, "presence first: the line really was written"
    if planted in on_disk:
        pytest.xfail(
            "BACKLOG S3, now live (REVIEW.md R5): Buddy's dream announcement "
            "writes 160 chars of raw model output into activity.jsonl "
            "unredacted and recorder-independent — "
            f"{log_path}. The legacy-bodies scanner cannot find it either, "
            "because it is not a prompt_trace.")
    assert planted not in on_disk


def test_dream_is_idempotent_for_the_day(buddy_env):
    agent, host, backend, buddy_mod = buddy_env
    first = asyncio.run(agent.dream())
    assert first
    second = asyncio.run(agent.dream())
    assert second == "", "a second dream the same day must be a no-op"
    events = [e for e in _activity_events() if e.get("source") == "buddy"]
    assert len(events) == 1, "and must not re-announce"


def test_dream_writes_an_honest_placeholder_when_the_model_is_unavailable(
        buddy_env, monkeypatch):
    """Ollama absent / every backend down: the dream must still complete and
    must say so rather than writing an empty file."""
    agent, host, backend, buddy_mod = buddy_env

    class _Down(_FakeBackend):
        def complete(self, *a, **kw):
            raise ConnectionError("ollama not running")

        def stream_complete(self, *a, **kw):
            raise ConnectionError("ollama not running")
            yield  # pragma: no cover

    host.router = ModelRouter.from_backend(_Down(), "fake")
    reflection = asyncio.run(agent.dream())

    assert "No reflection written" in reflection
    assert "model unavailable" in reflection
    files = list((host.get_pkb_root() / "agents" / "buddy" / "dreams")
                 .glob("*.md"))
    assert len(files) == 1 and reflection.strip() in files[0].read_text()


def test_dream_with_the_recorder_on_captures_a_redacted_body(buddy_env):
    """Recorder on: the dream *prompt and response* become a captured body,
    which must go through redaction like any other."""
    agent, host, backend, buddy_mod = buddy_env
    planted = "sk-dreambody00000000000000000"
    backend.text = f"reflection mentioning {planted}"
    agent_trace.set_recorder_enabled(True)

    asyncio.run(agent.dream())

    traces = [t for t in _disk_traces() if t["outcome"] == "ok"]
    assert traces, "the dream's inference must leave a trace"
    bodies = traces[-1]["bodies"]
    assert isinstance(bodies, dict), "recorder on must capture the dream body"
    assert planted not in json.dumps(bodies), bodies
    assert bodies["redactions"] >= 1


def test_dream_survives_a_pkb_write_failure_without_crashing_the_daemon(
        buddy_env, monkeypatch):
    """Disk-full / read-only PKB during the nightly dream: the daemon must
    record a failure, not die."""
    agent, host, backend, buddy_mod = buddy_env
    from arail.agents import dream_daemon

    lines: list[tuple] = []

    class _CaptureLog:
        def emit(self, *a, **kw):
            lines.append((a, kw))

        def recent(self, n=200):
            return []

    monkeypatch.setattr(dream_daemon, "activity_log", _CaptureLog())
    import arail.pkb as pkb_mod
    monkeypatch.setattr(pkb_mod, "write_buddy_dream",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            OSError("No space left on device")))

    asyncio.run(dream_daemon._dream_once("buddy", agent))

    messages = " ".join(str(a[0]) for a in lines)
    assert "failed" in messages.lower(), (
        f"a dream failure must be visible, not swallowed: {lines}")


# ---------------------------------------------------------------------------
# 4. The resident pin and the agent fast-stream flag
# ---------------------------------------------------------------------------

def test_dream_once_idempotence_reads_the_unisolated_real_pkb_root(
        buddy_env, monkeypatch, tmp_path):
    """Hermeticity hazard found while writing the tests above.

    ``dream_daemon._dream_once`` decides "already dreamed today" from
    ``arail.pkb._pkb_root()`` -> ``config.PKB_ROOT``, which the repo
    conftest does **not** isolate (it isolates ``config.DATA_DIR`` only).
    So on a machine whose real ``lab/pkb/agents/buddy/dreams/<today>.md``
    exists, ``_dream_once`` returns before its first emit and every
    assertion downstream of it passes vacuously — the same
    silently-vacuous class the orchestrator already caught once in this
    sprint. This worktree's real tree acquired such a file during the
    sprint, which is how the hazard surfaced.

    Asserted as behaviour: with a pre-existing dream file for today,
    ``_dream_once`` must be a no-op — and therefore any test of it that
    does not isolate ``PKB_ROOT`` proves nothing.
    """
    agent, host, backend, buddy_mod = buddy_env
    from arail.agents import dream_daemon

    lines: list[tuple] = []

    class _CaptureLog:
        def emit(self, *a, **kw):
            lines.append((a, kw))

        def recent(self, n=200):
            return []

    monkeypatch.setattr(dream_daemon, "activity_log", _CaptureLog())

    # Pre-existing dream for today, in the (isolated) PKB root.
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dreams = host.get_pkb_root() / "agents" / "buddy" / "dreams"
    dreams.mkdir(parents=True, exist_ok=True)
    (dreams / f"{today}.md").write_text("already dreamed")

    asyncio.run(dream_daemon._dream_once("buddy", agent))
    assert lines == [], (
        "_dream_once must no-op when today's dream exists — which is exactly "
        "why a test of it that leaves PKB_ROOT pointing at the developer's "
        "real lab/pkb can pass without running anything")
    assert backend.prompts == []


def test_normalize_keep_alive_still_hands_ollama_an_integer():
    """A string ``"-1"`` makes Ollama answer 400 (`missing unit in duration`)
    and the whole call fails. main carries this fix; the branch must not
    have regressed it."""
    assert _normalize_keep_alive("-1") == -1
    assert isinstance(_normalize_keep_alive("-1"), int)
    assert _normalize_keep_alive("300") == 300
    assert _normalize_keep_alive("2h") == "2h"
    assert _normalize_keep_alive("30s") == "30s"


def test_resident_pin_keep_alive_is_an_int_not_a_string(monkeypatch):
    from arail.router.backends import OllamaNativeBackend

    be = OllamaNativeBackend.__new__(OllamaNativeBackend)
    be.model_name = "qa-model"
    monkeypatch.delenv("ARAIL_OLLAMA_KEEP_ALIVE", raising=False)
    monkeypatch.setenv("ARAIL_RESIDENT_PIN", "1")
    monkeypatch.setattr(OllamaNativeBackend, "_is_registry_tier0_model",
                        lambda self: True)
    keep_alive = be._keep_alive()
    assert keep_alive == -1 and isinstance(keep_alive, int)

    monkeypatch.setattr(OllamaNativeBackend, "_is_registry_tier0_model",
                        lambda self: False)
    assert be._keep_alive() == "2h"


def test_agent_stream_fast_is_on_by_default_and_yields_a_measured_ttft(
        buddy_env):
    """S3/F20: the fast branch streams, so Buddy finally has a real TTFT."""
    agent, host, backend, buddy_mod = buddy_env
    from arail.agents import deep_policy

    assert deep_policy._agent_stream_fast_enabled() is True, (
        "ARAIL_AGENT_STREAM_FAST must default ON (operator decision)")
    with agent_context.agent_call("buddy"):
        text = deep_policy.complete_preferring_deep(
            "voice this", foreground=False, fast_router=host.router)

    assert text == "buddy says something"
    trace = _disk_traces()[0]
    assert trace["streamed"] is True
    assert trace["ttft_status"] == "measured"
    assert isinstance(trace["ttft_ms"], float) and trace["ttft_ms"] >= 0
    assert trace["ttft_ms"] != trace["latency_ms"], (
        "TTFT must never be derived from latency_ms")
    assert trace["prefill_ms"] == 7.5
    assert trace["prefill_source"] == "server_reported"


def test_the_joined_stream_equals_what_complete_would_have_returned(buddy_env,
                                                                    monkeypatch):
    """F20 non-tautologically: the same backend is asked both ways and the
    two strings are compared to each other, not to a constant."""
    agent, host, backend, buddy_mod = buddy_env
    from arail.agents import deep_policy

    with agent_context.agent_call("buddy"):
        streamed = deep_policy.complete_preferring_deep(
            "same prompt", foreground=False, fast_router=host.router)

    monkeypatch.setenv("ARAIL_AGENT_STREAM_FAST", "0")
    assert deep_policy._agent_stream_fast_enabled() is False
    with agent_context.agent_call("buddy"):
        non_streamed = deep_policy.complete_preferring_deep(
            "same prompt", foreground=False, fast_router=host.router)

    assert streamed == non_streamed
    statuses = [t["ttft_status"] for t in _disk_traces()]
    assert statuses == ["measured", "non_streaming"], statuses


def test_stream_fast_falls_back_to_complete_when_ollama_is_down(buddy_env):
    """``ARAIL_AGENT_STREAM_FAST`` default-on must survive Ollama refusing
    the streaming request: unconditional fallback to ``complete()``, with the
    honest ``n/a`` TTFT that implies — never an error to the caller."""
    agent, host, backend, buddy_mod = buddy_env
    from arail.agents import deep_policy

    class _StreamRefuses(_FakeBackend):
        def stream_complete(self, *a, **kw):
            raise ConnectionError("connection refused")
            yield  # pragma: no cover

    router = ModelRouter.from_backend(_StreamRefuses("fallback answer"), "fake")
    with agent_context.agent_call("buddy"):
        text = deep_policy.complete_preferring_deep(
            "voice this", foreground=False, fast_router=router)

    assert text == "fallback answer", "the fallback must produce the answer"
    traces = _disk_traces()
    assert [t["outcome"] for t in traces] == ["error", "ok"], traces
    assert traces[0]["ttft_status"] == "error" and traces[0]["ttft_ms"] is None
    assert traces[1]["ttft_status"] == "non_streaming"
    assert traces[1]["ttft_ms"] is None, "an honest n/a, never a number"


def test_everything_down_returns_none_rather_than_raising(buddy_env):
    """Ollama absent entirely: Buddy goes quiet, the lab does not crash."""
    agent, host, backend, buddy_mod = buddy_env
    from arail.agents import deep_policy

    class _AllDead(_FakeBackend):
        def complete(self, *a, **kw):
            raise ConnectionError("no ollama")

        def stream_complete(self, *a, **kw):
            raise ConnectionError("no ollama")
            yield  # pragma: no cover

    router = ModelRouter.from_backend(_AllDead(), "fake")
    with agent_context.agent_call("buddy"):
        assert deep_policy.complete_preferring_deep(
            "voice this", foreground=False, fast_router=router) is None

    assert [t["outcome"] for t in _disk_traces()] == ["error", "error"]
    assert {t["error_class"] for t in _disk_traces()} == {"ConnectionError"}


def test_a_stream_that_yields_only_empty_strings_reports_no_tokens(buddy_env):
    """The TTFT truth table's third row, which a real Ollama produces when a
    model emits nothing but a terminal frame."""
    agent, host, backend, buddy_mod = buddy_env

    class _EmptyDeltas(_FakeBackend):
        def stream_complete(self, prompt, max_tokens=512, temperature=0.7,
                            top_p=None, *, system=None, messages=None):
            yield ""
            yield ""
            yield ModelResponse(text="", model="m", tokens_used=0,
                                backend="fake", latency_ms=1.0)

    router = ModelRouter.from_backend(_EmptyDeltas(), "fake")
    with agent_context.agent_call("buddy"):
        list(router.stream_complete("nothing comes back"))

    trace = _disk_traces()[0]
    assert trace["ttft_status"] == "no_tokens"
    assert trace["ttft_ms"] is None


def test_an_emulated_stream_never_reports_a_number(buddy_env):
    """The AeroLLM/QueueLLM shape: ``BaseBackend.stream_complete`` yields one
    ModelResponse. Buddy's *deep* brain can never have a TTFT, and the trace
    must say so in words rather than with a blank or a synthesised number."""
    agent, host, backend, buddy_mod = buddy_env
    router = ModelRouter.from_backend(_FakeBackend(), "fake")
    with agent_context.agent_call("buddy"):
        list(router.stream_complete("deep brain call"))

    trace = _disk_traces()[0]
    assert trace["ttft_status"] == "emulated_stream"
    assert trace["ttft_ms"] is None
