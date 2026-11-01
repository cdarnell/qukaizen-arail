"""Client-behavior tests for research.html's goal-scoped experiment summary.

Same Node JS-render harness pattern as test_world_step_dom.py: the portal
has no jsdom/jest/vitest harness, so this shells out to Node against a
minimal DOM shim, running the ACTUAL goalScopedExperiments()/renderSummary()
extracted out of the live research.html — not a reimplementation.

The bug this pins: GET /api/experiments returns the whole experiment
corpus (every goal, every World, newest-first, unfiltered). Before this
fix, the "Experiments testing your goal" section rendered the raw top-5 of
that corpus, so a brand-new goal — before the Researcher had designed a
single experiment for it — could show five OTHER goals' experiments,
marked supported/not-supported from THEIR runs, under a header claiming
they tested this one. Observed live: a debt-consolidation goal displaying
"✓ supported" aeroLLM-inference-engine and learn-math experiments from
July.

This test shells out to Node and self-skips if Node is unavailable.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HARNESS = os.path.join(_REPO_ROOT, "tests", "js", "research_summary_harness.mjs")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available for JS-render test")
def test_js_render_research_summary_harness():
    """Run the Node JS-render harness for research.html's goal-scoped summary."""
    assert os.path.exists(_HARNESS), f"harness missing: {_HARNESS}"
    proc = subprocess.run(
        ["node", _HARNESS],
        capture_output=True, text=True, timeout=30,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, f"JS-render harness failed:\n{out}"
    assert "research-summary JS-render assertions passed" in out, f"unexpected harness output:\n{out}"
