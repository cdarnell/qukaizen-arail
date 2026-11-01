"""S2 — attribution wiring: L1 (loader), L2 (daemons), L3 (top-level modules
and the subprocess protocol). Each test proves the wrapper is present and
correctly scoped, using fakes/monkeypatches rather than real model calls —
QA's blind tests exercise the end-to-end behaviour; these prove the wiring
mechanism at each site.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from arail import agent_context, agent_trace


@pytest.fixture(autouse=True)
def _clean_state():
    agent_context._reset_for_tests()
    agent_trace._reset_for_tests()
    yield
    agent_context._reset_for_tests()
    agent_trace._reset_for_tests()


# ---------------------------------------------------------------------------
# L1 — loader.start_all_auto wraps instance.start() in agent_call(folder_name)
# ---------------------------------------------------------------------------

def test_loader_wraps_start_in_agent_call(monkeypatch):
    from arail.agents import loader

    seen = {}

    class _FakeAgent:
        def start(self):
            call = agent_context.current()
            seen["agent_id"] = call.agent_id if call else None

    monkeypatch.setattr(loader, "_agents_root", lambda pkb_root=None: __import__("pathlib").Path("/nonexistent"))

    # Bypass frontmatter lookup (no AGENT.md needed for this unit test).
    monkeypatch.setattr(
        "arail.skills_loader.parse_frontmatter",
        lambda text: {}, raising=False,
    )

    loader.start_all_auto({"my_agent": _FakeAgent()})
    assert seen["agent_id"] == "my_agent"
    # And the context is gone again once start() returns.
    assert agent_context.current() is None


def test_loader_agent_loop_spawned_inside_start_inherits_attribution(monkeypatch):
    """The whole point of L1: create_task inside start() carries the
    context into the spawned loop for free (A1)."""
    from arail.agents import loader

    seen = {}

    class _AsyncAgent:
        def start(self):
            self._task = asyncio.get_event_loop().create_task(self._loop())

        async def _loop(self):
            call = agent_context.current()
            seen["agent_id"] = call.agent_id if call else None

    monkeypatch.setattr(
        "arail.skills_loader.parse_frontmatter", lambda text: {}, raising=False,
    )

    async def _scenario():
        agent = _AsyncAgent()
        loader.start_all_auto({"my_agent": agent})
        await agent._task

    asyncio.run(_scenario())
    assert seen["agent_id"] == "my_agent"


# ---------------------------------------------------------------------------
# L2 — dream_daemon._dream_once
# ---------------------------------------------------------------------------

def test_dream_once_sets_agent_call(monkeypatch, tmp_path):
    from arail.agents import dream_daemon

    monkeypatch.setattr(dream_daemon, "_dream_file_for",
                        lambda agent_id, when: tmp_path / "nope.md")

    seen = {}

    class _Dreamer:
        async def dream(self):
            call = agent_context.current()
            seen["agent_id"] = call.agent_id if call else None
            return "reflection"

    asyncio.run(dream_daemon._dream_once("buddy", _Dreamer()))
    assert seen["agent_id"] == "buddy"
    assert agent_context.current() is None


# ---------------------------------------------------------------------------
# L2 — job_daemon._run_job (system_call, not agent_call)
# ---------------------------------------------------------------------------

def test_run_job_sets_system_call(monkeypatch, tmp_path):
    from arail.agents import job_daemon

    seen = {}

    async def _fake_create_subprocess_exec(*cmd, **kw):
        call = agent_context.current()
        seen["kind"] = call.kind if call else None
        seen["label"] = call.label if call else None

        class _Proc:
            returncode = 0

            async def communicate(self):
                return (b"ok", None)

        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    monkeypatch.setattr(job_daemon, "_receipt_dir", lambda job_id: tmp_path)

    job = job_daemon.Job(id="test-job", name="Test", world=".",
                        script="noop.py", interval_sec=60)
    asyncio.run(job_daemon._run_job(job))

    assert seen["kind"] == "system"
    assert seen["label"] == "test-job"
    assert agent_context.current() is None


# ---------------------------------------------------------------------------
# L3 — researcher._llm_complete
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, text="hi", model="m", backend="b", tokens_used=3):
        self.text = text
        self.model = model
        self.backend = backend
        self.tokens_used = tokens_used


def test_researcher_llm_complete_attributes_to_researcher():
    from arail.agents import researcher

    seen = {}

    class _FakeRouter:
        def complete(self, prompt, max_tokens=512, temperature=0.7, system=None):
            call = agent_context.current()
            seen["agent_id"] = call.agent_id if call else None
            return _FakeResp()

    researcher._llm_complete(_FakeRouter(), "prompt")
    assert seen["agent_id"] == "researcher"
    assert agent_context.current() is None


# ---------------------------------------------------------------------------
# L3 — browser.py: three call sites all attribute to "browser"
# ---------------------------------------------------------------------------

def test_browser_source_has_three_agent_call_sites():
    import pathlib
    src = pathlib.Path(
        __import__("arail.agents.browser", fromlist=["x"]).__file__
    ).read_text()
    assert src.count('agent_context.agent_call("browser")') == 3


# ---------------------------------------------------------------------------
# L3 — librarian_scout.draft_proposal attributes to "librarian"
# ---------------------------------------------------------------------------

def test_draft_proposal_attributes_to_librarian():
    from arail import librarian_scout as ls

    seen = {}

    class _FakeRouter:
        def complete(self, prompt, max_tokens=500, temperature=0.3, top_p=0.9):
            call = agent_context.current()
            seen["agent_id"] = call.agent_id if call else None
            return _FakeResp(text='{"category":"c1","definition":"d","related":[]}')

    result = ls.draft_proposal(
        "slug1", {"term": "Term", "evidence": []},
        {"display_name": "World", "categories": [{"id": "c1"}]},
        [], _FakeRouter(),
    )
    assert seen["agent_id"] == "librarian"
    assert result is not None


# ---------------------------------------------------------------------------
# L3 — _builtin_drafter.compose attributes to "drafter"
# ---------------------------------------------------------------------------

def test_drafter_compose_attributes_to_drafter():
    from arail.agents._builtin_drafter import DrafterAgent

    seen = {}

    class _FakeRouter:
        def complete(self, prompt, max_tokens=400, temperature=0.7):
            call = agent_context.current()
            seen["agent_id"] = call.agent_id if call else None
            return _FakeResp(text="a draft")

    agent = DrafterAgent()
    draft = agent.compose("some context", "reply", router=_FakeRouter())
    assert seen["agent_id"] == "drafter"
    assert draft.text == "a draft"


# ---------------------------------------------------------------------------
# L3 — recap/router_adapter.RouterAdapter.chat attributes to sys:recap
# ---------------------------------------------------------------------------

def test_recap_router_adapter_attributes_to_sys_recap():
    from arail.agents.recap.router_adapter import RouterAdapter

    seen = {}

    class _FakeRouter:
        def complete(self, prompt, max_tokens=512, temperature=0.7):
            call = agent_context.current()
            seen["kind"] = call.kind if call else None
            seen["label"] = call.label if call else None
            return _FakeResp(text="reply")

    adapter = RouterAdapter(_FakeRouter())
    text = adapter.chat([{"role": "user", "content": "hi"}])
    assert seen["kind"] == "system"
    assert seen["label"] == "recap"
    assert text == "reply"


# ---------------------------------------------------------------------------
# L3 — dictionary.generate_terms / expand_term attribute to sys:dictionary
# ---------------------------------------------------------------------------

def test_dictionary_generate_terms_attributes_to_sys_dictionary():
    from arail import dictionary

    seen = {}

    class _FakeRouter:
        def complete(self, prompt, max_tokens=1400, temperature=0.7, top_p=0.9):
            call = agent_context.current()
            seen["label"] = call.label if call else None
            return _FakeResp(text="[]")

    dictionary.generate_terms({"label": "Theme"}, count=1, router=_FakeRouter())
    assert seen["label"] == "dictionary"


def test_dictionary_expand_term_attributes_to_sys_dictionary():
    from arail import dictionary

    seen = {}

    class _FakeRouter:
        def complete(self, prompt, max_tokens=320, temperature=0.6, top_p=0.9):
            call = agent_context.current()
            seen["label"] = call.label if call else None
            return _FakeResp(text="an explanation")

    dictionary.expand_term({"label": "Theme"}, "term", router=_FakeRouter())
    assert seen["label"] == "dictionary"


# ---------------------------------------------------------------------------
# L3 — world_routes.py: system_call labels match the existing inference_slot
# labels at each of the four model-acquisition sites (structural check —
# functional coverage needs the full FastAPI dependency graph, QA's job).
# ---------------------------------------------------------------------------

def test_world_routes_system_call_labels_match_inference_slot_labels():
    import pathlib
    from arail.portal import world_routes
    src = pathlib.Path(world_routes.__file__).read_text()
    for label in ("world-forge", "term-draft", "world-review", "world-grow"):
        assert f'agent_context.system_call("{label}")' in src, (
            f"expected a system_call(\"{label}\") wrapper in world_routes.py"
        )
        assert f'inference_slot("{label}")' in src or label == "world-forge", (
            f"expected inference_slot(\"{label}\") to still exist for {label}"
        )


# ---------------------------------------------------------------------------
# L3 / contract #9 — goal_parser subprocess protocol extension
# ---------------------------------------------------------------------------

def test_goal_parser_inproc_attributes_to_sys_goal_parser():
    from arail.skills.goal_parser import GoalParser

    seen = {}

    class _FakeRouter:
        def complete(self, prompt, max_tokens=800, temperature=0.5):
            call = agent_context.current()
            seen["kind"] = call.kind if call else None
            seen["label"] = call.label if call else None
            return _FakeResp(text='{"goal":"g"}')

    parser = GoalParser(router=_FakeRouter())
    text = parser._llm_inproc("prompt")
    assert seen["kind"] == "system"
    assert seen["label"] == "goal-parser"
    assert text == '{"goal":"g"}'


def test_subprocess_runner_main_sets_context_from_payload(monkeypatch):
    """Drive _subprocess_runner._main() as a plain function (not a real
    subprocess) with a stubbed router and stdin/stdout, proving the child
    sets its own attribution context from the wire payload and reports
    model/backend/tokens_used back — without spawning a real backend."""
    from arail.skills.goal_parser import _subprocess_runner as runner
    import io

    seen = {}

    class _FakeRouter:
        def complete(self, prompt, max_tokens=800, temperature=0.5):
            call = agent_context.current()
            seen["agent_id"] = call.agent_id if call else None
            seen["out_of_process"] = call.out_of_process if call else None
            return _FakeResp(text="parsed", model="fake-model",
                             backend="fake-backend", tokens_used=42)

    monkeypatch.setattr("arail.router.ModelRouter", lambda *a, **kw: _FakeRouter())

    payload = json.dumps({
        "prompt": "parse this",
        "trace_id": "a" * 16,
        "agent_id": None,
        "brain": None,
        "effort": None,
    })
    monkeypatch.setattr(runner.sys, "stdin", io.StringIO(payload))
    out = io.StringIO()
    monkeypatch.setattr(runner.sys, "stdout", out)

    rc = runner._main()

    assert rc == 0
    assert seen["agent_id"] is None
    assert seen["out_of_process"] is True  # chokepoint must skip its own record

    result = json.loads(out.getvalue())
    assert result["ok"] is True
    assert result["model"] == "fake-model"
    assert result["backend"] == "fake-backend"
    assert result["tokens_used"] == 42


def test_subprocess_runner_does_not_write_its_own_trace(monkeypatch, tmp_path):
    """Contract #9: the child never writes the trace file -- the router
    chokepoint's out_of_process skip must hold even when a real ModelRouter/
    agent_trace path is exercised in-process (no real subprocess needed to
    prove this: the chokepoint code path is identical either way)."""
    from arail import config
    from arail.router.backends import BaseBackend, ModelResponse
    from arail.router.core import ModelRouter

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    agent_trace._reset_for_tests()

    class _FakeBackend(BaseBackend):
        def complete(self, *a, **kw):
            return ModelResponse(text="x", model="m", tokens_used=1,
                                 backend="fake", latency_ms=1.0)

        def stream_complete(self, *a, **kw):
            yield ModelResponse(text="x", model="m", tokens_used=1,
                                backend="fake", latency_ms=1.0)

        def health_check(self):
            return True

    router = ModelRouter.from_backend(_FakeBackend(), "fake")
    payload = {"trace_id": "b" * 16, "agent_id": None}
    with agent_context.from_subprocess_payload(payload, default_label="goal-parser"):
        router.complete("hi")

    # The chokepoint skipped its own record entirely.
    assert agent_trace.stats()["recorded"] == 0

    # Now the parent authors its own single record for the round-trip.
    from arail.skills.goal_parser import GoalParser
    GoalParser._record_subprocess_trace(
        {"trace_id": "b" * 16, "agent_id": None, "brain": None, "effort": None},
        outcome="ok", model="m", backend="fake", tokens_out=1,
    )
    assert agent_trace.stats()["recorded"] == 1
    rec = agent_trace.ring(1)[0]
    assert rec["out_of_process"] is True
    assert rec["outcome"] == "ok"
