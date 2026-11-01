"""Chat page load must never auto-trigger a model load.

`autoWarmBoxes()` used to fire automatically on every /chat page visit
via `queueMicrotask()`: it warmed Box A unconditionally, and — if it
judged conditions "eligible" (tier + installed + not opted out + enough
free RAM) — silently turned Compare ON and loaded a second, heavy deep-
backend model (aeroLLM/AirLLM) with zero user action. This is almost
certainly what made the aeroLLM "2nd chat box" load a misconfigured
model and freeze the whole event loop before the operator had ever
touched Compare — the exact same "unsolicited post-boot activity"
category the operator explicitly ruled out for the cold-start overlay
(_boot_overlay.html, which replaced an /api/ready-polling implementation
for the identical reason).

Removed. `send()` doesn't need a prior warm: it posts straight to
/api/chat/stream, and the backend (Ollama, or whichever runtime is
selected) cold-loads on first request the same way it always would —
the only change is that cost is now paid honestly, inline, on the
user's first real message, not invisibly before they've done anything.
"""
from __future__ import annotations

from pathlib import Path

CHAT_HTML = (
    Path(__file__).resolve().parent.parent
    / "src" / "arail" / "portal" / "templates" / "chat.html"
).read_text(encoding="utf-8")


def test_autowarmboxes_function_is_gone():
    assert "autoWarmBoxes" not in CHAT_HTML


def test_init_does_not_queue_a_microtask_warm():
    assert "queueMicrotask" not in CHAT_HTML, (
        "chat.html previously used queueMicrotask() for exactly one "
        "thing — auto-warming both boxes on page load. Its reappearance "
        "likely means the auto-warm behavior is back."
    )


def test_loadmodel_still_exists_for_the_explicit_load_button():
    """The fix removes the *automatic* trigger, not the mechanism itself.
    sprints/2026-08-11-two-slot-chat-models Phase 5 replaced the per-card
    rail "Load" button with the resident/deep pickers — clicking a picker
    row IS the explicit, user-initiated load trigger now
    (selectResidentModel/selectDeepModel, both of which await loadModel())."""
    assert "async function loadModel(m)" in CHAT_HTML
    assert "const ok = await loadModel(m);" in CHAT_HTML  # selectResidentModel
    assert "await loadModel({ id: m.id" in CHAT_HTML       # selectDeepModel


def test_init_function_ends_without_calling_loadmodel_directly():
    """init() itself (the function that runs unconditionally on every
    page load) must not call loadModel — only user-triggered handlers
    (a picker row click, via selectResidentModel/selectDeepModel) are
    allowed to. Only counts loadModel as an actual call site (`loadModel(`
    not preceded by a comment marker on the same line) — a prose mention
    in a comment explaining this exact invariant doesn't count."""
    start = CHAT_HTML.index("async function init(")
    end = CHAT_HTML.index("\n    init();", start)
    init_body = CHAT_HTML[start:end]
    call_sites = [
        ln for ln in init_body.splitlines()
        if "loadModel(" in ln and "//" not in ln.split("loadModel(")[0]
    ]
    assert not call_sites, f"init() calls loadModel() directly: {call_sites}"
