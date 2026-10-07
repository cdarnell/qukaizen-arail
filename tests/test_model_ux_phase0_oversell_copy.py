"""Phase 0 (display fidelity) — F-OVERSELL: aeroLLM copy never claims
streaming, selective expert-streaming, or bit-exactness; the Gemma
catalog entry never claims Apache-2.0.

Sprint: 2026-07-20-model-ux-unification
Architecture: sprints/2026-07-20-model-ux-unification/ARCHITECTURE.md
Implementation-order step 4 (full site sweep, not one site).

aeroLLM keeps its resident model in memory once loaded; it does not
stream from disk per-token, and `AERO_MOE_SELECT` (real selective/
active-only expert streaming) is off and absent from src/. AirLLM is
the backend that genuinely layer-streams, and it must be named as such
— never stamped onto an aeroLLM row (finding 5).

The Gemma 4 26B-A4B catalog entry shipped mislabeled "(Apache-2.0)";
Gemma ships under the Gemma Terms of Use (workspace CLAUDE.md's "Gemma
disclosure exception"). Corrected as part of this same sweep per
ARCHITECTURE.md's "Folded into this sprint" note.
"""

from __future__ import annotations

import os
import re

import pytest
import yaml

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CHAT_HTML = os.path.join(
    _REPO_ROOT, "src", "arail", "portal", "templates", "chat.html"
)
_CATALOG_YAML = os.path.join(
    _REPO_ROOT, "src", "arail", "chat", "models_catalog.yaml"
)


def _chat_html_text() -> str:
    with open(_CHAT_HTML, "r", encoding="utf-8") as f:
        return f.read()


def _catalog_text() -> str:
    with open(_CATALOG_YAML, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# T-COPY — no aeroLLM-attributed streaming/selective-expert/bit-exact claim
# ---------------------------------------------------------------------------

# Exact historical oversell phrases that directly attributed streaming /
# selective-expert / bit-exact behavior TO aeroLLM. A contrastive phrase
# like "AeroLLM resident · AirLLM layer-streamed" is fine (it correctly
# attributes streaming to AirLLM, not aeroLLM) and must NOT trip this —
# hence an exact-phrase blacklist rather than a same-line co-occurrence
# heuristic (which would false-positive on the honest contrastive copy).
_BANNED_PHRASES_OLD_NAME = (
    "aerollm layer-stream",
    "aerollm's native selective",
    "aerollm's rust runtime ships native selective",
    "via aerollm's native selective expert-streaming",
    "aerollm ... layer-stream",
    "layer-streamed via aerollm",
    "layer-streams a frontier",
    "aerollm) so it can layer-stream",
    "so it can layer-stream from disk",
    "selective expert-streaming backend",
    "bit-exact vs",
    "ssd (streamed)",
    "streamed from disk via aerollm",
)

# The display name is now QueueLLM, so every banned phrase that names the
# old engine also needs a QueueLLM twin; otherwise these negative checks
# pass vacuously while oversell copy slips in under the new name.
_BANNED_PHRASES = _BANNED_PHRASES_OLD_NAME + tuple(
    p.replace("aerollm", "queuellm")
    for p in _BANNED_PHRASES_OLD_NAME
    if "aerollm" in p
)


def test_every_banned_aerollm_phrase_has_a_queuellm_twin():
    for phrase in _BANNED_PHRASES_OLD_NAME:
        if "aerollm" in phrase:
            assert phrase.replace("aerollm", "queuellm") in _BANNED_PHRASES, phrase


def _lines_with_banned_phrases(text: str) -> list[str]:
    hits = []
    low_text = text.lower()
    for phrase in _BANNED_PHRASES:
        if phrase in low_text:
            hits.append(phrase)
    return hits


def test_chat_html_no_aerollm_line_claims_streaming():
    text = _chat_html_text()
    hits = _lines_with_banned_phrases(text)
    assert not hits, f"aeroLLM-attributed oversell copy found: {hits}"


def test_models_catalog_no_aerollm_entry_claims_streaming():
    text = _catalog_text()
    hits = _lines_with_banned_phrases(text)
    assert not hits, f"aeroLLM-attributed oversell copy found: {hits}"


def test_chat_html_streamed_badge_does_not_hardcode_airllm_for_any_row():
    """The generic "exceeds hardware floor" streamed-badge used to say
    "via AirLLM" unconditionally — wrong when the row would actually
    route through aeroLLM (preferred on Apple Silicon). Now backend-
    neutral, naming both real behaviors."""
    text = _chat_html_text()
    assert "Layer-streamed via AirLLM" not in text


def test_chat_html_deep_entries_verdict_is_warmth_driven_not_installed_bool():
    """F-OVERSELL's central site, relocated (sprints/2026-08-11-two-slot-
    chat-models Phase 5): the client-synthesized `deepEntries` mapping
    (which set `fit.verdict = o.installed ? 'streaming' : 'not installed'`
    — a literal lie for aeroLLM) is gone. aeroLLM now gets its own
    dedicated Deep picker (renderDeepPicker), fed by the server's real
    `slots.deep` block (residency probed via model_warmth._tier1_resident,
    never an `installed` bool). Verdict must still be warmth-driven and
    must never say 'streaming' for aerollm."""
    text = _chat_html_text()
    assert "o.installed ? 'streaming' : 'not installed'" not in text
    assert (
        "fit: { verdict: deep.resident ? 'Resident' : "
        "(deep.model_ready ? 'Ready to load' : 'Not installed') },"
    ) in text


def test_models_catalog_gemma_moe_entry_is_not_labeled_apache():
    entries = yaml.safe_load(_catalog_text())
    gemma = next(e for e in entries if e.get("id") == "gemma-4-26b-a4b")
    desc = gemma["description"]
    assert "Apache-2.0" not in desc, (
        "gemma-4-26b-a4b must not claim Apache-2.0 — Gemma ships under the "
        "Gemma Terms of Use"
    )
    assert "Gemma Terms of Use" in desc
    assert "Built with Gemma" in desc
