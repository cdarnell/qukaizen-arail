"""Phase 0 (display fidelity) — F-WARMDOT: the warm-dot / badge tracks
real residency, never `installed` or client-side in-memory state.

Sprint: 2026-07-20-model-ux-unification
Architecture: sprints/2026-07-20-model-ux-unification/ARCHITECTURE.md
Implementation-order step 8 (warmth probe).

  - Ollama rows: real, live `ollama ps` (never `/api/tags`'s "installed",
    never a client-only Set that starts empty every page load).
  - aeroLLM: `model_warmth._tier1_resident()` — inspects
    `AeroLLMBackend._shared` without constructing anything.
  - AirLLM: presence in the thin wrapper cache (best-effort — AirLLM is
    subprocess-isolated and has no equivalent of `_shared`).

Also covers C5/finding 6's UI half: deep rows (aeroLLM/AirLLM) never
render an Unload/Eject affordance in the rail or active-card — only
Load, since the singleton cannot be hot-freed this sprint (A3).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(_REPO_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))

_CHAT_HTML = os.path.join(
    _REPO_ROOT, "src", "arail", "portal", "templates", "chat.html"
)


def _chat_html_text() -> str:
    with open(_CHAT_HTML, "r", encoding="utf-8") as f:
        return f.read()


def _client():
    from fastapi.testclient import TestClient
    import arail.portal.app as app_mod
    return TestClient(app_mod.app), app_mod


# ---------------------------------------------------------------------------
# Server: _ollama_ps_resident_ids — live probe, short timeout, cached
# fallback on failure
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, payload: dict, status: int = 200):
        self._body = json.dumps(payload).encode()
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_ollama_ps_resident_ids_reads_live_ps_not_tags(monkeypatch):
    import arail.portal.app as app_mod

    calls = []

    def _fake_urlopen(req, timeout=None):
        calls.append((req.full_url, timeout))
        return _FakeResp({"models": [{"name": "llama3.2:1b"}, {"model": "qwen2.5:7b"}]})

    import urllib.request as _ureq
    monkeypatch.setattr(_ureq, "urlopen", _fake_urlopen)

    ids = app_mod._ollama_ps_resident_ids()
    assert ids == {"llama3.2:1b", "qwen2.5:7b"}
    assert calls, "must actually make a request"
    assert "/api/ps" in calls[0][0], "must probe /api/ps (live), not /api/tags (installed)"
    assert calls[0][1] <= 1.0, "Performance guard: probe timeout must be <=1s"


def test_ollama_ps_resident_ids_falls_back_to_last_known_on_timeout(monkeypatch):
    import arail.portal.app as app_mod
    import urllib.request as _ureq

    # First call succeeds and populates the last-known cache.
    monkeypatch.setattr(_ureq, "urlopen", lambda req, timeout=None: _FakeResp(
        {"models": [{"name": "gemma-4-26b-a4b:latest"}]}
    ))
    first = app_mod._ollama_ps_resident_ids()
    assert first == {"gemma-4-26b-a4b:latest"}

    # Second call times out — must NOT silently report everything cold.
    def _boom(req, timeout=None):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(_ureq, "urlopen", _boom)
    second = app_mod._ollama_ps_resident_ids()
    assert second == {"gemma-4-26b-a4b:latest"}, (
        "a failed/timed-out probe must fall back to the last-known reading, "
        "not silently claim every row is cold"
    )


def test_build_local_model_entry_warm_field_reflects_the_probe():
    import arail.portal.app as app_mod

    warm_entry = app_mod._build_local_model_entry(
        "gemma-4-26b-a4b:latest", runtime="ollama", size_gb=14.4, modified="",
        endpoint="http://127.0.0.1:11434/v1", current=None,
        detected_gb=24.0, free_gb=6.0, warm=True,
    )
    cold_entry = app_mod._build_local_model_entry(
        "gemma-4-26b-a4b:latest", runtime="ollama", size_gb=14.4, modified="",
        endpoint="http://127.0.0.1:11434/v1", current=None,
        detected_gb=24.0, free_gb=6.0, warm=False,
    )
    assert warm_entry["warm"] is True
    assert cold_entry["warm"] is False


# ---------------------------------------------------------------------------
# Server: optional_backends carries a real `resident` probe, never
# `installed`, and aeroLLM is never marked `streamed`
# ---------------------------------------------------------------------------

def test_optional_backends_aerollm_resident_is_tier1_resident_not_installed(monkeypatch, tmp_path):
    client, app_mod = _client()
    import arail.chat as chat_mod
    from arail.portal import model_warmth

    monkeypatch.setenv("ARAIL_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setattr(app_mod, "_local_memory_snapshot", lambda: {
        "label": "Apple M5", "gpu_label": None,
        "total_gb": 24.0, "used_gb": 6.0, "free_gb": 18.0,
    })
    monkeypatch.setattr(chat_mod, "gallery_view", lambda: {
        "installed": [], "catalog": [], "runtime_counts": {},
    })
    monkeypatch.setattr(app_mod, "_is_aerollm_installed", lambda: True)
    monkeypatch.setattr(model_warmth, "_tier1_resident", lambda: True)
    # app.py imports the symbol locally at call time via
    # `from arail.portal.model_warmth import _tier1_resident`, so patching
    # the module attribute above is what the running code actually sees.

    r = client.get("/api/chat/models")
    body = r.json()
    aerollm_entry = next(b for b in body["optional_backends"] if b["id"] == "aerollm")

    assert aerollm_entry["resident"] is True
    assert aerollm_entry["streamed"] is False, (
        "F-OVERSELL: aeroLLM must never be marked streamed — it keeps its "
        "model resident once loaded"
    )


def test_optional_backends_aerollm_resident_false_when_cold(monkeypatch, tmp_path):
    client, app_mod = _client()
    import arail.chat as chat_mod
    from arail.portal import model_warmth

    monkeypatch.setenv("ARAIL_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setattr(app_mod, "_local_memory_snapshot", lambda: {
        "label": "Apple M5", "gpu_label": None,
        "total_gb": 24.0, "used_gb": 6.0, "free_gb": 18.0,
    })
    monkeypatch.setattr(chat_mod, "gallery_view", lambda: {
        "installed": [], "catalog": [], "runtime_counts": {},
    })
    monkeypatch.setattr(app_mod, "_is_aerollm_installed", lambda: True)
    monkeypatch.setattr(model_warmth, "_tier1_resident", lambda: False)

    r = client.get("/api/chat/models")
    body = r.json()
    aerollm_entry = next(b for b in body["optional_backends"] if b["id"] == "aerollm")
    assert aerollm_entry["resident"] is False, (
        "installed-but-cold must not report resident=True — a cold "
        "singleton must not claim residency"
    )


# ---------------------------------------------------------------------------
# sprints/2026-08-11-two-slot-chat-models Phase 5 superseded this whole
# cluster. The below-the-fold rail (renderModelRail) and the separate
# "active model" strip (renderActiveCard) — the two surfaces the tests
# above pinned as twins — are gone; both collapsed onto the two header
# chips (#model-picker / #model-picker-B) and their shared ejectModel(side)
# handler. That consolidation also REVERSED the A3 restriction the old
# tests protected ("the aeroLLM singleton cannot be hot-freed this
# sprint"): Phase 4 wired a real in-process teardown
# (AeroLLMBackend._close(), _swap_optional_chat_backend), so a warm deep
# chip now gets a working eject, not a disabled "can't hot-free" notice.
# See docs/chat-studio.spec.md §3 and the Phase 4/5 sprint ledger entries.
# ---------------------------------------------------------------------------

def test_chip_eject_gating_matches_what_can_actually_be_freed():
    """F-WARMDOT/A3 successor: the resident chip's eject is gated on the
    ollama runtime specifically (the only one /api/chat/eject can free
    in-process); the deep chip's eject is gated on residency alone — no
    runtime carve-out — because Phase 4 made aeroLLM genuinely freeable.
    Both live in updateChipWarmState(), replacing the old rail/active-card
    per-row `canFree`/`isDeep` branches."""
    text = _chat_html_text()
    assert "if (ejectA) ejectA.hidden = !(warmA && State.currentRuntime === 'ollama');" in text
    assert "if (ejectB) ejectB.hidden = !warmB;" in text


def test_chat_html_seeds_warm_models_from_server_truth_not_only_client_actions():
    text = _chat_html_text()
    assert "State.models.forEach(m => { if (m.warm) State.warmModels.add(m.id); });" in text
    # Resident-slot warmth doesn't need a parallel client-side seed — it's
    # read directly off the server-probed State.residentSlot.warm.
    assert "State.residentSlot && State.residentSlot.warm" in text


def test_ejectmodel_only_clears_warm_dot_on_confirmed_success():
    """HON-1 successor: the single, shared ejectModel(side) handler (which
    replaced the separate rail-card/active-card copies) still gates
    State.warmModels.delete on the endpoint's own d.ok — never optimistic."""
    text = _chat_html_text()
    assert "if (d.ok) {" in text
    assert "if (model) State.warmModels.delete(model);" in text


def test_column_b_has_real_choice_not_a_hard_lock():
    """Successor to 'use as B is never hard disabled': the old rail-card
    fix gave column B a real alternative by un-disabling a generic 'use as
    B' button on every card. Phase 5 goes further — column B now has a
    DEDICATED Deep picker (renderDeepPicker) listing the configured
    aeroLLM model plus every other installed MLX-runtime model as a real,
    clickable alternative (over-cap rows shown but marked ineligible with
    a reason, never silently hidden)."""
    text = _chat_html_text()
    assert (
        "const alts = State.models.filter(m =>\n"
        "        m.id !== deep.model_id && (m.runtime === 'mlx' || m.runtime === 'mlx-openai'));"
    ) in text
    assert "pop.appendChild(makeOpt(m, deep.model_id, x => selectDeepModel(x), {" in text
