"""Regression tests for the domain-inference bias that made every
ambiguous goal come back as ``farming``.

The original ``infer_domain`` used ``max(scores, key=scores.get)``, which
returns the first maximal key in dict order. ``farming`` was declared
first, so every tie resolved to agriculture — and its keyword list
(``yield``, ``crop``, ``harvest``) collides with ordinary finance and ML
phrasing. Operators saw a lab that "always answers about farming".
"""

from __future__ import annotations

import pytest

from arail.skills.goal_parser import (
    DOMAIN_KEYWORDS,
    infer_domain,
    matched_keywords,
)


def test_finance_goal_is_not_labelled_farming():
    """The reported symptom: a debt-finance goal answered as agriculture."""
    assert infer_domain("Track my high-yield debt payoff") != "farming"


@pytest.mark.parametrize("goal", [
    "Compare yield curve inversions to recession onset",
    "Model my debt payoff schedule against interest rate changes",
    "Build a portfolio cash flow tracker",
])
def test_finance_goals_resolve_to_business(goal):
    assert infer_domain(goal) == "business"


def test_tie_resolves_to_general_not_to_first_declared_domain():
    """A goal matching exactly one keyword in two domains is ambiguous."""
    goal = "Improve the harvest and the recipe"  # farming=1, culinary=1
    assert matched_keywords(goal, "farming") == ["harvest"]
    assert matched_keywords(goal, "culinary") == ["recipe"]
    assert infer_domain(goal) == "general"


def test_inference_is_independent_of_declaration_order():
    """Reordering DOMAIN_KEYWORDS must not change any verdict.

    This is the property the bug violated. Guard it directly so a future
    edit to the dict literal cannot silently reintroduce positional bias.
    """
    goals = [
        "Track my high-yield debt payoff",
        "Improve the harvest and the recipe",
        "Grow peanuts on twenty acres",
        "Fine-tune an llm on a new dataset",
        "Something with no keywords at all",
    ]
    before = {g: infer_domain(g) for g in goals}

    original = dict(DOMAIN_KEYWORDS)
    try:
        reversed_order = dict(reversed(list(original.items())))
        DOMAIN_KEYWORDS.clear()
        DOMAIN_KEYWORDS.update(reversed_order)
        after = {g: infer_domain(g) for g in goals}
    finally:
        DOMAIN_KEYWORDS.clear()
        DOMAIN_KEYWORDS.update(original)

    assert before == after


def test_keywords_match_on_word_boundaries_only():
    """Substring matching made 'corn' fire on 'cornerstone'."""
    assert matched_keywords("cornerstone of the strategy", "farming") == []
    assert matched_keywords("escalate to the team", "business") == []
    assert matched_keywords("plant corn this spring", "farming") == ["corn"]


def test_genuine_farming_goals_still_resolve_to_farming():
    """The fix must not overcorrect into never labelling agriculture."""
    assert infer_domain("Grow peanuts on twenty acres of poor soil") == "farming"
    assert infer_domain("Plan crop rotation and irrigation for the farm") == "farming"


def test_no_keywords_is_general():
    assert infer_domain("Do the thing") == "general"
    assert infer_domain("") == "general"
