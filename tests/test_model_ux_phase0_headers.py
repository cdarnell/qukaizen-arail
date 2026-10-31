"""Phase 0 (display fidelity) — F-HEADER: rail/picker headers make no
false size claim on the local column, and no false streaming claim on
the aeroLLM (deep) column.

Sprint: 2026-07-20-model-ux-unification
Architecture: sprints/2026-07-20-model-ux-unification/ARCHITECTURE.md
Implementation-order step 3 (both header twins).

Both twins (rail card headers AND picker-popup section headers) must be
fixed — a partial fix (one twin left standing) is exactly the bug class
this sprint exists to close.

Failure mode: F-HEADER
  - "26B under `≤ 8B`" — the local-GPU column's static header claimed an
    8B ceiling; a real installed 26B MoE (gemma-4-26b-a4b) renders in
    that same column with a truthful fit chip that contradicts it.
  - "deep rows under `SSD (streamed)`" — aeroLLM keeps its resident model
    in memory once loaded; it does not stream from disk per-token
    (AERO_MOE_SELECT, the real expert-streaming feature, is off and
    absent from src/). Only AirLLM (opt-in, CUDA/x86) layer-streams.
"""

from __future__ import annotations

import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CHAT_HTML = os.path.join(
    _REPO_ROOT, "src", "arail", "portal", "templates", "chat.html"
)


def _chat_html_text() -> str:
    with open(_CHAT_HTML, "r", encoding="utf-8") as f:
        return f.read()


def test_no_local_column_size_claim_anywhere_in_chat_html():
    """F-HEADER: the local-GPU column must never claim an "8B" ceiling —
    a real installed model can exceed it (gemma-4-26b-a4b, 26.5B total)."""
    text = _chat_html_text()
    assert "GPU (&le; 8B)" not in text
    assert "(&le; 8B)" not in text


def test_no_ssd_streamed_claim_on_the_deep_column_anywhere_in_chat_html():
    """F-HEADER twin: aeroLLM is not "SSD (streamed)" — it keeps the
    resident model in memory once loaded. Only AirLLM (opt-in) streams."""
    text = _chat_html_text()
    assert "SSD (streamed)" not in text


def test_resident_picker_header_is_honest():
    """sprints/2026-08-11-two-slot-chat-models Phase 5: the below-the-fold
    rail (rc-head twin) is gone — replaced by the resident-slot picker,
    whose header carries hardware context instead of a static size claim
    (per-row ceiling eligibility is computed server-side, C-CEILROW)."""
    text = _chat_html_text()
    assert (
        '<div class="head"><span>Resident model</span><span class="hw">${hwLabel}</span></div>'
    ) in text


def test_deep_picker_header_is_honest():
    text = _chat_html_text()
    assert "sec.innerHTML = `<div class=\"head\"><span>Deep model · QueueLLM</span>" in text


def test_deep_rail_subtitle_does_not_claim_streamed_from_disk():
    """The rail-card subtitle under the aeroLLM column previously read
    "30B+ models streamed from disk via AeroLLM." — an F-OVERSELL-adjacent
    header claim fixed alongside the header itself."""
    text = _chat_html_text()
    assert "streamed from disk via AeroLLM" not in text
    assert "streamed from disk via QueueLLM" not in text
