"""F8 — per-module halt survival: every agent-kind model-calling site
survives AgentHeldError and reaches its documented fallback, none of them
crash their own loop.

Non-agent (system_call) callers -- dictionary, world_routes, goal_parser,
recap -- are categorically exempt: halt_gate only ever raises for
kind="agent" (tested exhaustively in test_halt_gate.py), so there is no
refusal for them to survive. This file covers only the agent-kind sites A7
identified: deep_policy.complete_preferring_deep (covers buddy,
debt_advisor, consolidation_analyzer), researcher._llm_complete, browser,
librarian_scout.draft_proposal, and _builtin_drafter.compose (the one site
that does NOT swallow broadly and needed an explicit guard).
"""

from __future__ import annotations

import pytest

from arail import agent_context


@pytest.fixture(autouse=True)
def _clean():
    agent_context._reset_for_tests()
    yield
    agent_context._reset_for_tests()


class _HeldRouter:
    """A fake router whose .complete() raises AgentHeldError, exactly what
    the real chokepoint does when an agent-kind call is refused."""

    def complete(self, *a, **kw):
        raise agent_context.AgentHeldError("refused: held")

    def stream_complete(self, *a, **kw):
        raise agent_context.AgentHeldError("refused: held")
        yield  # pragma: no cover - unreachable, keeps this a generator


# ---------------------------------------------------------------------------
# deep_policy.complete_preferring_deep — covers buddy, debt_advisor,
# consolidation_analyzer (they all call this one function with no
# exception handling of their own beyond it).
# ---------------------------------------------------------------------------

def test_deep_policy_survives_held_fast_router(monkeypatch):
    from arail.agents import deep_policy
    monkeypatch.setattr("arail.tier.is_maximus", lambda: False)  # force fast
    out = deep_policy.complete_preferring_deep(
        "hi", foreground=True, fast_router=_HeldRouter())
    assert out is None  # survives -- does not raise


def test_deep_policy_survives_held_deep_router(monkeypatch):
    from arail.agents import deep_policy
    deep_policy._reset_for_tests()
    monkeypatch.setattr(deep_policy, "_aerollm_importable", lambda: True)
    monkeypatch.setattr("arail.tier.is_maximus", lambda: True)
    monkeypatch.setattr(deep_policy, "get_deep_router", lambda: _HeldRouter())
    out = deep_policy.complete_preferring_deep(
        "hi", foreground=True, fast_router=_HeldRouter())
    assert out is None  # both paths held -- survives, does not raise
    deep_policy._reset_for_tests()


def test_ariail_host_llm_complete_survives_held(monkeypatch):
    """Buddy/debt_advisor/consolidation_analyzer all call llm_complete,
    which calls complete_preferring_deep with no try/except of its own
    beyond the one already inside ArailHost.llm_complete -- proves the
    whole chain from the host's perspective, not just deep_policy's."""
    from arail.agents._builtin_buddy import ArailHost
    from arail.agents import deep_policy
    monkeypatch.setattr("arail.tier.is_maximus", lambda: False)
    monkeypatch.setattr(deep_policy, "_get_fast_router", lambda: _HeldRouter())
    host = ArailHost()
    text = host.llm_complete("hi")
    assert text == ""  # never raises into the caller


# ---------------------------------------------------------------------------
# researcher._llm_complete
# ---------------------------------------------------------------------------

def test_researcher_llm_complete_survives_held():
    from arail.agents import researcher
    result = researcher._llm_complete(_HeldRouter(), "prompt")
    assert result is None


# ---------------------------------------------------------------------------
# browser.py's three call sites (navigate/interact/summarize) -- each
# wraps router.complete() in its own try/except already; proving the
# navigate site (the first, unconditional one) is representative.
# ---------------------------------------------------------------------------

def test_browser_navigate_survives_held(monkeypatch):
    from arail.agents import browser

    monkeypatch.setattr(browser, "_is_airgapped", lambda: False)
    monkeypatch.setattr(browser, "_ab_available", lambda: True)
    monkeypatch.setattr(browser, "_get_router", lambda: _HeldRouter())
    monkeypatch.setattr(browser, "_extract_url", lambda instruction: "")

    result = browser.chat("go somewhere and look around")
    assert result["success"] is False  # degrades gracefully, never raises


# ---------------------------------------------------------------------------
# librarian_scout.draft_proposal
# ---------------------------------------------------------------------------

def test_draft_proposal_survives_held():
    from arail import librarian_scout as ls

    result = ls.draft_proposal(
        "slug1", {"term": "Term", "evidence": []},
        {"display_name": "World", "categories": [{"id": "c1"}]},
        [], _HeldRouter(),
    )
    assert result is None  # survives -- does not raise


# ---------------------------------------------------------------------------
# _builtin_drafter.compose -- the one site that did NOT swallow before this
# sprint's explicit guard (F8's central example).
# ---------------------------------------------------------------------------

def test_drafter_compose_survives_held():
    from arail.agents._builtin_drafter import DrafterAgent

    agent = DrafterAgent()
    draft = agent.compose("some context", "reply", router=_HeldRouter())
    assert draft.text == ""
    assert draft.metadata == {"error": "held"}
    assert draft.model == "(held)"
    assert draft.requires_consent is True  # unchanged contract


def test_drafter_compose_held_path_is_distinguishable_from_no_router():
    """The held-degradation and the pre-existing no-router-available
    degradation must not collapse into the same signal -- a caller (or a
    future admin view) needs to tell them apart."""
    from arail.agents._builtin_drafter import DrafterAgent

    agent = DrafterAgent()
    held = agent.compose("ctx", "intent", router=_HeldRouter())
    assert held.metadata.get("error") == "held"

    agent2 = DrafterAgent()
    agent2._router = None
    no_router = agent2.compose("ctx", "intent", router=None)
    # (router resolution will likely fail in a test env with no registry,
    # landing in the "no router available" branch -- either branch is
    # acceptable here; the assertion is just that it's not "held".)
    assert no_router.metadata.get("error") != "held"
