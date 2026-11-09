"""The World Terms page shows the Curator's notices as text.

C10 and SEC2 (World Terms) of sprints/2026-10-29-arail-deep-honest/ARCHITECTURE.md. The portal has no
JavaScript harness (TD11), so these are static checks on the source: weak, and said to be.
"""
from __future__ import annotations

from pathlib import Path

PORTAL = Path(__file__).resolve().parents[1] / "src" / "arail" / "portal"


def test_c10_world_terms_shows_the_notice_as_text_and_treats_not_applied_as_terminal():
    js = (PORTAL / "static" / "js" / "world-terms.js").read_text(encoding="utf-8")
    assert "S.reviewNote" in js and "S.growNote" in js
    assert "r.body.outcome === 'partial' || r.body.outcome === 'failed'" in js
    assert "S.growState === 'not_applied' ? (r.body.message || '') : ''" in js
    assert "el('div', 'wt-notice wt-notice--warn', note)" in js, "inserted through el(), which sets textContent"
    poll = js.split("async function pollGrow()", 1)[1].split("async function startGrow()", 1)[0]
    assert "if (S.growState === 'running')" in poll and "else {" in poll, "every non-running state is terminal"
    assert "loadTerms()" in poll and "S.growState === 'done'" in poll.split("loadTerms()")[0].splitlines()[-1], \
        "only a pass that was applied reloads the terms"
    css = (PORTAL / "static" / "knowledge.css").read_text(encoding="utf-8")
    assert ".wt-notice--warn" in css


def test_sec2_world_terms_never_assigns_html():
    js = (PORTAL / "static" / "js" / "world-terms.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "outerHTML" not in js
