"""Phase 0b (load/unload lifecycle honesty) — final checkpoint
(implementation-order step 14): the remaining named Unit/Security/
Regression tests from ARCHITECTURE.md's Test Strategy that don't already
have dedicated coverage in the sibling test_model_ux_phase0b_*.py files.

Sprint: 2026-07-20-model-ux-unification
Architecture: sprints/2026-07-20-model-ux-unification/ARCHITECTURE.md

Not covered here (out of unit-test reach, per the architecture's own Test
Strategy — QA's Persistence & Honesty suite runs these on real hardware):
  T-EJECT-OLLAMA — real `ollama ps` residency delta after a real eject.
  T-RESTART      — real portal restart + RSS return-to-baseline.
  T-NOFLICK      — 20x GET /api/chat/models on a real idle box; this file
                   covers the *pure-function* half (C3's hysteresis bands
                   are deterministic for fixed input) but not real memory
                   jitter, which requires a real machine.
"""

from __future__ import annotations

import os
import re
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(_REPO_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))


# ---------------------------------------------------------------------------
# C3 — _fit_verdict_label boundary table (UNCHANGED this sprint, but named
# explicitly in the Test Strategy's Unit section — was previously only
# exercised incidentally, not as a full boundary table)
# ---------------------------------------------------------------------------

def test_fit_verdict_label_boundary_table():
    import arail.portal.app as app_mod

    avail = 20.0
    good_edge = round(avail * 0.82, 2)
    marginal_edge = round(avail * 1.08, 2)

    assert app_mod._fit_verdict_label(good_edge, avail) == "Good"
    assert app_mod._fit_verdict_label(good_edge + 0.5, avail) == "Marginal"
    assert app_mod._fit_verdict_label(marginal_edge, avail) == "Marginal"
    assert app_mod._fit_verdict_label(marginal_edge + 0.5, avail) == "Requires streaming"
    assert app_mod._fit_verdict_label(0, avail) == "Unknown"
    assert app_mod._fit_verdict_label(-5, avail) == "Unknown"
    assert app_mod._fit_verdict_label(10, 0) == "Unknown"
    assert app_mod._fit_verdict_label(10, -3) == "Unknown"


def test_fit_verdict_label_is_a_pure_deterministic_function_no_flicker():
    """T-NOFLICK's pure-function half: for a FIXED reading, the verdict
    must be byte-identical across repeated calls — any nondeterminism
    here (not just real hardware jitter) would itself be a flicker bug."""
    import arail.portal.app as app_mod

    results = {app_mod._fit_verdict_label(14.4, 7.1) for _ in range(50)}
    assert len(results) == 1


# ---------------------------------------------------------------------------
# F-XSS — new/touched fields render escaped
# ---------------------------------------------------------------------------

_CHAT_HTML = os.path.join(_REPO_ROOT, "src", "arail", "portal", "templates", "chat.html")


def _chat_html_text() -> str:
    with open(_CHAT_HTML, "r", encoding="utf-8") as f:
        return f.read()


def test_warmth_is_shown_without_an_unescaped_text_label():
    """F-XSS successor (sprints/2026-08-11-two-slot-chat-models Phase 5):
    the rail card's separate `warmLabelText` string is gone along with the
    rail itself. Warmth is now a CSS-only signal (.col-chip.warm::before,
    toggled by updateChipWarmState() via classList) — there is no
    server-derived warm-status text interpolated into innerHTML at all,
    so there is nothing left needing escapeHtml() for this specific
    concern (identity-field escaping generally is covered by
    test_sec_chat_html_escapes_every_model_identity_field_insertion)."""
    text = _chat_html_text()
    assert "warmLabelText" not in text
    assert "classList.toggle('warm', warmA)" in text


def test_eject_and_flash_status_never_use_inner_html_for_server_text():
    """flashStatus (which renders eject notes/freed lists) must use
    .textContent, never .innerHTML — the server-supplied `notes` array
    (backend names, error strings) must not be interpretable as markup."""
    text = _chat_html_text()
    m = re.search(r"function flashStatus\(msg\) \{(.*?)\n    \}", text, re.S)
    assert m, "flashStatus function not found"
    body = m.group(1)
    assert ".textContent" in body
    assert ".innerHTML" not in body


# ---------------------------------------------------------------------------
# BLOCK-2 regression — no AERO_MOE_SELECT-gated code anywhere in src/
# ---------------------------------------------------------------------------

def test_no_aero_moe_select_flag_or_gated_code_path_in_src():
    """Non-goal: aeroLLM true frontier layer-streaming (AERO_MOE_SELECT)
    is named in prose only — no reserved code hook, no flag-gated code
    path. Naming the concept in a comment/docstring explaining that it's
    OFF (as this sprint's own F-OVERSELL fixes do) is exactly what "prose
    only" permits; what must be absent is any CODE that actually reads or
    branches on it (`os.getenv("AERO_MOE_SELECT")`, an `if`/ternary
    gating on the name, etc.) — a dormant lane, not a description of one.
    """
    src_dir = os.path.join(_REPO_ROOT, "src")
    gating_pattern = re.compile(
        r"""(getenv\(\s*["']AERO_MOE_SELECT["']|os\.environ\[["']AERO_MOE_SELECT["']\]|
             \bif\b[^\n]*AERO_MOE_SELECT|AERO_MOE_SELECT\s*[=!]=|
             AERO_MOE_SELECT\s*\?)""",
        re.VERBOSE,
    )
    hits = []
    for root, _dirs, files in os.walk(src_dir):
        for fname in files:
            if not fname.endswith((".py", ".html", ".yaml", ".yml")):
                continue
            path = os.path.join(root, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
            except (UnicodeDecodeError, OSError):
                continue
            if gating_pattern.search(text):
                hits.append(path)
    assert not hits, (
        f"AERO_MOE_SELECT is gating real code in src/ (should be prose-only, "
        f"per BLOCK-2): {hits}"
    )


def test_no_backend_notice_or_top_level_hardware_field_anywhere_in_src():
    """F8/F-DEADFIELD final regression sweep across the whole portal
    package, not just app.py — the dead-field pattern this sprint exists
    to kill must not reappear anywhere."""
    portal_dir = os.path.join(_REPO_ROOT, "src", "arail", "portal")
    hits = []
    for root, _dirs, files in os.walk(portal_dir):
        for fname in files:
            if not fname.endswith((".py", ".html")):
                continue
            path = os.path.join(root, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    if "backend_notice" in f.read():
                        hits.append(path)
            except (UnicodeDecodeError, OSError):
                continue
    assert not hits, f"backend_notice reappeared: {hits}"
