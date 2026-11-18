"""Buddy brain A/B harness (sprint 2026-11-18-aiml-slm-engine).

Sends the same frozen Buddy tasks through ARAIL's real Buddy brain path
(``buddy_host.brain``) for a baseline tag and a candidate tag, grades every
reply in code, and writes a side-by-side report that refuses to give a verdict
when its own self-checks fail. See docs/buddy-brain-bench.md.
"""
RESULTS_SCHEMA = "arail.buddy_brain_ab/v1"
