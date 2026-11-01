"""Tests for the Researcher planning-trace educational disclosure (Phase 3 part 2).

Verifies:
  - _plan_research populates _planning_trace with chosen + alternatives
  - The split happens at _CHOSEN_LIMIT
  - Heuristic fallback also populates the trace with source="heuristic"
  - get_planning_trace returns a defensive copy (callers can't mutate
    internal state)
  - get_planning_trace returns None on a fresh agent
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from arail.agents.researcher import ResearcherAgent


@pytest.fixture
def agent():
    return ResearcherAgent()


def test_get_planning_trace_returns_none_on_fresh_agent(agent):
    """Fresh agent has no plan yet — trace is None, not empty dict."""
    assert agent.get_planning_trace() is None


def test_plan_research_heuristic_fallback_populates_trace(agent):
    """When LLM is unavailable, heuristic fallback still records a trace
    with source=heuristic so the UI can teach 'no LLM today'."""
    parsed = {
        "goal": "improve query latency",
        "domain": "general",
        "sub_objectives": [],
        "intent": "ai",
    }
    # Force the LLM path to return None — exercises heuristic fallback.
    with patch("arail.agents.researcher._deep_complete", return_value=None):
        chosen = agent._plan_research(parsed)
    assert chosen, "heuristic fallback must return at least one hypothesis"
    trace = agent.get_planning_trace()
    assert trace is not None
    assert trace["source"] == "heuristic"
    assert trace["chosen"] == chosen
    assert trace["alternatives"] == []  # generic fallback only emits 2
    assert "rationale" in trace and trace["rationale"]
    assert trace["llm_response"] is None
    assert trace["generated_at"]


def test_plan_research_heuristic_fallback_with_subobjectives_populates_alts(agent):
    """When sub-objectives exceed _CHOSEN_LIMIT, the overflow becomes
    alternatives — even on the heuristic path."""
    parsed = {
        "goal": "ship a feature",
        "domain": "general",
        # 8 sub-objectives — limit is 5, so 3 should overflow into alternatives.
        "sub_objectives": [f"obj-{i}" for i in range(8)],
        "intent": "ai",
    }
    with patch("arail.agents.researcher._deep_complete", return_value=None):
        chosen = agent._plan_research(parsed)
    trace = agent.get_planning_trace()
    assert trace["source"] == "heuristic"
    assert len(trace["chosen"]) == 5
    assert len(trace["alternatives"]) == 3
    assert chosen == trace["chosen"]


def test_plan_research_llm_split_at_chosen_limit(agent):
    """When LLM returns N candidates with N > _CHOSEN_LIMIT, top _CHOSEN_LIMIT
    become experiments and the rest become alternatives."""
    fake_llm_response = "\n".join([
        f"{i}. Hypothesis number {i}: a sufficiently long candidate to clear the >10-char filter."
        for i in range(1, 9)  # 8 candidates
    ])
    parsed = {
        "goal": "test the split",
        "domain": "general",
        "sub_objectives": [],
        "intent": "ai",
    }
    with patch("arail.agents.researcher._deep_complete",
               return_value=fake_llm_response):
        chosen = agent._plan_research(parsed)
    trace = agent.get_planning_trace()
    assert trace["source"] == "llm"
    assert len(trace["chosen"]) == ResearcherAgent._CHOSEN_LIMIT
    assert len(trace["alternatives"]) == 8 - ResearcherAgent._CHOSEN_LIMIT
    assert trace["llm_response"] == fake_llm_response
    assert chosen == trace["chosen"]
    # Order preserved: chosen[0] is the LLM's top-ranked candidate.
    assert "Hypothesis number 1" in chosen[0]


def test_plan_research_llm_returns_few_candidates_no_alternatives(agent):
    """When LLM returns fewer candidates than _CHOSEN_LIMIT, all become
    chosen and alternatives is empty."""
    fake_llm_response = "\n".join([
        f"{i}. Short hypothesis {i} that easily clears the >10-char filter."
        for i in range(1, 4)  # only 3 candidates
    ])
    parsed = {
        "goal": "few candidates",
        "domain": "general",
        "sub_objectives": [],
        "intent": "ai",
    }
    with patch("arail.agents.researcher._deep_complete",
               return_value=fake_llm_response):
        chosen = agent._plan_research(parsed)
    trace = agent.get_planning_trace()
    assert len(trace["chosen"]) == 3
    assert trace["alternatives"] == []
    assert chosen == trace["chosen"]


def test_get_planning_trace_returns_defensive_copy(agent):
    """Mutating the returned trace must not affect the agent's internal
    state. Educational disclosure is read-only from the API side."""
    parsed = {
        "goal": "no mutation across the boundary",
        "domain": "general",
        "sub_objectives": ["a", "b"],
        "intent": "ai",
    }
    with patch("arail.agents.researcher._deep_complete", return_value=None):
        agent._plan_research(parsed)
    trace1 = agent.get_planning_trace()
    trace1["chosen"].append("MUTATED")
    trace1["alternatives"].append("ALSO MUTATED")
    trace1["rationale"] = "mutated"
    trace2 = agent.get_planning_trace()
    assert "MUTATED" not in trace2["chosen"]
    assert "ALSO MUTATED" not in trace2["alternatives"]
    assert trace2["rationale"] != "mutated"


def test_plan_research_overwrites_previous_trace(agent):
    """Each _plan_research call replaces the trace — previous-run state
    must not leak into the next planning step."""
    parsed_a = {"goal": "first goal", "domain": "general", "sub_objectives": [], "intent": "ai"}
    parsed_b = {"goal": "second goal", "domain": "general", "sub_objectives": [], "intent": "ai"}
    with patch("arail.agents.researcher._deep_complete", return_value=None):
        agent._plan_research(parsed_a)
        first_trace = agent.get_planning_trace()
        agent._plan_research(parsed_b)
        second_trace = agent.get_planning_trace()
    assert first_trace["generated_at"] != second_trace["generated_at"] or \
           first_trace["chosen"] != second_trace["chosen"]
    # The second plan's chosen reflects the new goal text.
    assert "second goal" in " ".join(second_trace["chosen"])


# ── Chat scaffolding is not a hypothesis ────────────────────────────
#
# A real run spent one of five experiment slots "testing" the sentence
# "Certainly! Here are five testable hypotheses that directly address
# the goal:" — the model's preamble survived normalization and became a
# tracked experiment.

import pytest as _pytest

from arail.agents.researcher import _normalize_hypothesis_line as _norm


@_pytest.mark.parametrize("line", [
    "Certainly! Here are five testable hypotheses that address the goal:",
    "Sure! Below are the hypotheses you asked for:",
    "Here are 5 hypotheses, ordered by directness:",
    "These hypotheses are specific, measurable, and grounded in the domain.",
    "The following hypotheses can be tested locally:",
    "I have generated the following list:",
])
def test_conversational_scaffolding_is_not_a_hypothesis(line):
    assert _norm(line) is None, f"scaffolding survived: {line!r}"


@_pytest.mark.parametrize("line", [
    "1. Increasing prefetch lookahead depth from 2 to 4 will raise tok/s by 10%",
    "- Implementing a persistent KV cache will improve throughput by 15-20%",
    "**Speculative decoding** with a fast SLM will boost tok/s by at least 20%",
    "Switching to mixed-precision per-layer will improve tok/s by 30%",
])
def test_real_hypotheses_still_survive(line):
    out = _norm(line)
    assert out and len(out) > 10
    assert not out.startswith(("1.", "-", "*"))


def test_a_long_claim_ending_in_a_colon_is_kept():
    """The colon rule targets short introducers, not a real claim that
    happens to end in one."""
    line = ("Increasing the prefetch lookahead depth from two to four layers "
            "on a 7B model will improve decode throughput measurably on this "
            "machine under the following conditions:")
    assert _norm(line) is not None
