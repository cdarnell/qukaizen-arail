"""Pure-derivation tests for arail.research.options.

Pins the world-generic contract of the Autoresearch tab's option cards:
- options[0] is always the primary direction (default when mounted),
- one deepen card per declared category with honest term counts,
- the fill-gaps card is OMITTED when there are no gaps (never invent work),
- the watch card only describes the consent-gated scouting loop and says
  plainly when airgapped mode keeps watches dormant,
- generic seeds map onto REAL measurable archetypes via the engine's own
  deterministic keyword mapper (the executable honesty invariant).
"""

from __future__ import annotations

from arail.research.mini_experiments import select_archetype
from arail.research.options import (WorldOptionInputs, derive_options,
                                    options_payload)


def _debt_finance_like(**over) -> WorldOptionInputs:
    """A debt-finance-shaped fixture: 6 categories, sourced terms, 3 watches,
    roster fully declared, empty drift — the no-gaps happy path."""
    categories = [
        {"id": "debt-types", "label": "Debt Types"},
        {"id": "credit-products", "label": "Credit Products"},
        {"id": "institutions", "label": "Lenders & Institutions"},
        {"id": "strategies", "label": "Payoff & Consolidation Strategies"},
        {"id": "terminology", "label": "Terminology"},
        {"id": "retirement-and-secured-credit",
         "label": "Retirement & Secured Credit"},
    ]
    terms = []
    for i, cat in enumerate(categories):
        for j in range(2):
            slug = f"term-{i}-{j}"
            terms.append({"slug": slug, "term": slug, "category": cat["id"],
                          "short": "s", "definition": "d",
                          "source": "https://example.org/cite"})
    base = dict(
        slug="debt-finance",
        display_name="World of Debt Finance",
        tagline="A sourced glossary of debt types.",
        tier="sourced",
        counts={"model": 0, "sourced": len(terms), "total": len(terms)},
        spec={"slug": "debt-finance", "display_name": "World of Debt Finance",
              "categories": categories},
        terms=terms,
        agenda={"watches": [{"node": "a", "feeds": ["https://x/1"]},
                            {"node": "b", "feeds": ["https://x/2"]},
                            {"node": "c", "feeds": ["https://x/3"]}]},
        roster={"desired": [t["slug"] for t in terms]},
        drift={"missing": [], "ok": True},
    )
    base.update(over)
    return WorldOptionInputs(**base)


def test_mounted_default_first_and_deepen_cards():
    opts = derive_options(_debt_finance_like(), airgapped=True)
    assert opts[0]["kind"] == "default"
    assert opts[0]["goal_text"].startswith("Study World of Debt Finance")
    assert "12 sourced terms across 6 categories" in opts[0]["detail"]
    deepen = [o for o in opts if o["kind"] == "deepen"]
    assert len(deepen) == 6
    assert all(o["goal_text"] for o in deepen)
    assert all(o["meta"]["term_count"] == 2 for o in deepen)
    # Deepen goal texts come from goal_suggestions' category lines /
    # template and always name the World.
    assert all("World of Debt Finance" in o["goal_text"] for o in deepen)


def test_no_gaps_means_no_fill_gaps_card():
    opts = derive_options(_debt_finance_like(), airgapped=True)
    assert not [o for o in opts if o["kind"] == "verify"]


def test_watch_card_airgapped_vs_hybrid_copy():
    airgapped = derive_options(_debt_finance_like(), airgapped=True)
    hybrid = derive_options(_debt_finance_like(), airgapped=False)
    w_air = next(o for o in airgapped if o["kind"] == "watch")
    w_hyb = next(o for o in hybrid if o["kind"] == "watch")
    assert w_air["goal_text"] is None and w_air["href"] == "/dac"
    assert "Dormant in airgapped mode" in w_air["detail"]
    assert "never fetches" in w_air["detail"]
    assert "consent-gated" in w_hyb["detail"]
    assert w_air["meta"] == {"feeds": 3, "airgapped": True}


def test_gaps_from_drift_produce_honest_fill_gaps_card():
    inputs = _debt_finance_like(
        drift={"missing": ["heloc", "apr", "origination-fee", "grace"],
               "ok": False})
    card = next(o for o in derive_options(inputs, airgapped=True)
                if o["kind"] == "verify")
    assert card["meta"]["gaps"] == 4
    assert "Fill 4 glossary gaps" in card["goal_text"]
    assert "heloc" in card["goal_text"]


def test_gaps_fall_back_to_roster_minus_declared():
    inputs = _debt_finance_like(drift=None)
    inputs.roster = {"desired": [t["slug"] for t in inputs.terms]
                     + ["wishlist-term"]}
    card = next(o for o in derive_options(inputs, airgapped=True)
                if o["kind"] == "verify")
    assert card["meta"]["gaps"] == 1
    assert "wishlist-term" in card["goal_text"]


def test_model_asserted_terms_produce_verify_card():
    inputs = _debt_finance_like(
        tier="mixed", counts={"model": 3, "sourced": 9, "total": 12})
    card = next(o for o in derive_options(inputs, airgapped=True)
                if o["kind"] == "verify")
    assert card["meta"]["under_cited"] == 3
    assert "model-asserted" in card["goal_text"]


def test_generic_seeds_map_onto_real_archetypes():
    """The executable honesty invariant: every generic seed's wording must
    land on a measurable archetype in the engine's own keyword mapper."""
    expected = {
        "generic:model-speed": "model_throughput",
        "generic:prompt-phrasing": "prompt_variant",
        "generic:kb-retrieval": "retrieval_quality",
    }
    opts = derive_options(None, airgapped=True)
    assert [o["id"] for o in opts] == list(expected)
    for o in opts:
        assert o["kind"] == "generic" and o["goal_text"]
        assert select_archetype(o["goal_text"]) == expected[o["id"]]


def test_every_goal_bearing_option_has_goal_text():
    for inputs in (None, _debt_finance_like()):
        for o in derive_options(inputs, airgapped=True):
            if o["kind"] == "watch":
                assert o["goal_text"] is None
            else:
                assert isinstance(o["goal_text"], str) and o["goal_text"]


def test_every_option_says_what_it_measures():
    """'If you can measure it, we can improve it' — every card carries an
    honest measure line (the watch card describes what it gathers)."""
    for inputs in (None, _debt_finance_like()):
        for o in derive_options(inputs, airgapped=True):
            assert isinstance(o["measure"], str) and o["measure"], o["id"]


def test_zero_approved_docs_adds_setup_hints():
    """Data points required first: with an empty approved KB the glossary/KB
    cards tell the user to gather material in Knowledge."""
    opts = derive_options(_debt_finance_like(), airgapped=True,
                          approved_docs=0)
    kb_kinds = [o for o in opts if o["kind"] in ("default", "deepen")]
    assert kb_kinds and all(
        o["setup"] and "0 approved documents" in o["setup"]["hint"]
        and o["setup"]["href"] == "/dac" for o in kb_kinds)
    # Known-populated KB → no nagging.
    opts_ok = derive_options(_debt_finance_like(), airgapped=True,
                             approved_docs=12)
    assert all(o["setup"] is None for o in opts_ok
               if o["kind"] in ("default", "deepen"))
    # Unknown count → no claim either way.
    opts_unknown = derive_options(_debt_finance_like(), airgapped=True)
    assert all(o["setup"] is None for o in opts_unknown
               if o["kind"] in ("default", "deepen"))


def test_airgapped_watch_card_has_arm_the_scout_setup():
    opts = derive_options(_debt_finance_like(), airgapped=True)
    watch = next(o for o in opts if o["kind"] == "watch")
    assert watch["setup"] and "Arm the scout" in watch["setup"]["hint"]
    assert "gathers material" in watch["setup"]["hint"]
    hybrid = next(o for o in derive_options(_debt_finance_like(),
                                            airgapped=False)
                  if o["kind"] == "watch")
    assert hybrid["setup"] is None


def test_options_payload_shapes(monkeypatch):
    import arail.research.options as mod

    monkeypatch.setattr(mod, "load_world_inputs", lambda: None)
    unmounted = options_payload(airgapped=True)
    assert unmounted["world"] is None
    assert unmounted["airgapped"] is True
    assert len(unmounted["options"]) == 3

    monkeypatch.setattr(mod, "load_world_inputs", _debt_finance_like)
    mounted = options_payload(airgapped=False)
    assert mounted["world"] == "debt-finance"
    assert mounted["display_name"] == "World of Debt Finance"
    assert mounted["provenance_tier"] == "sourced"
    assert mounted["term_count"] == 12
    assert mounted["options"][0]["kind"] == "default"
