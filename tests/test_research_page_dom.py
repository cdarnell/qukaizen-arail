"""Structure + honesty assertions for the redesigned Autoresearch tab.

Source-level checks on research.html / _model_switcher.html (the chat.html
convention from test_aerollm_model_ready.py — plain JS, no server round
trip) plus a Jinja parse to catch block-nesting mistakes:

  - the options panel, truth strip, and modal-suggestions scaffolding exist,
  - the Hokkaido placeholder and the false git-branch promise are gone,
  - the tuning-loop branches panel is tier-gated and attributed,
  - dynamic option/model text routes through esc(),
  - the research model chip no longer advertises the reasoning profile,
  - experiment cards read the engine-stamped results.model.
"""

from __future__ import annotations

from pathlib import Path

import jinja2

_TPL = (Path(__file__).resolve().parent.parent
        / "src" / "arail" / "portal" / "templates")
RESEARCH = (_TPL / "research.html").read_text()
SWITCHER = (_TPL / "_model_switcher.html").read_text()


def test_jinja_parses():
    jinja2.Environment().parse(RESEARCH)


def test_required_ids_present():
    for el_id in ("rx-truth", "rx-truth-text", "rx-truth-toggle",
                  "rx-truth-toggle-input", "rx-truth-lock",
                  "rx-options", "rx-options-world", "rx-options-primary",
                  "rx-options-grid", "rx-modal-suggestions",
                  "rx-empty-open"):
        assert f'id="{el_id}"' in RESEARCH, f"missing #{el_id}"


def test_dishonest_copy_is_gone():
    assert "Hokkaido" not in RESEARCH
    assert "Every winning variant becomes a branch" not in RESEARCH
    # The honest engine description replaced the branch promise.
    assert "measured, could-not-run, or" in RESEARCH


def test_branches_panel_is_tier_gated_and_attributed():
    assert "{% if 'tuning' in tier_surfaces %}" in RESEARCH
    guard = RESEARCH.index("{% if 'tuning' in tier_surfaces %}")
    section = RESEARCH.index('class="rx-branches"')
    assert guard < section, "tier guard must wrap the branches section"
    assert "{% endif %}" in RESEARCH[section:]
    assert "QueueLLM tuning</a> loop" in RESEARCH
    assert "Tuning loop — experiment branches" in RESEARCH


def test_dynamic_text_routes_through_esc():
    for needle in ("esc(data.display_name", "esc(o.goal_text)",
                   "esc(primary.goal_text)", "esc(deep.reason",
                   "esc(fast.display_name", "esc(deep.display_name",
                   "esc(o.title", "esc(primary.detail",
                   "esc(o.measure)", "esc(o.setup.hint)"):
        assert needle in RESEARCH, f"unescaped write suspected: {needle}"


def test_truth_strip_and_options_are_wired():
    for needle in ("fetchOptions()", "/api/research/options",
                   "renderTruthStrip()", "/api/research/deep",
                   "wireTruthToggle()"):
        assert needle in RESEARCH, f"missing wiring: {needle}"


def test_experiment_cards_show_engine_stamped_model():
    assert "exp.results.model" in RESEARCH
    assert "rx-exp-model" in RESEARCH


def test_research_chip_no_longer_claims_reasoning():
    assert "(tab==='research'||tab==='build')" not in SWITCHER
    assert "var profile = (tab==='build') ? 'reasoning' : 'fast';" in SWITCHER
