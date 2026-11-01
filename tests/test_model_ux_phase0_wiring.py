"""Phase 0 (display fidelity) mechanical wiring fixes.

Sprint: 2026-07-20-model-ux-unification
Architecture: sprints/2026-07-20-model-ux-unification/ARCHITECTURE.md

This file covers ONLY the narrow, mechanical subset landed in this commit
(implementation-order items 1, 2, 4, 5 — nest the hardware snapshot into
`compact`, point the rail's data source at the list with real per-model
`fit`, delete the dead `backend_notice` field, and fix the phantom
`gallery.py` References pointer). The Phase-0b load/unload lifecycle work
(item 6 / §2.6) and the eject-honesty endpoint rewrite (item 3 / §2.3) are
explicitly OUT of scope here — they are real, design-dependent behavior
changes that get their own commit(s) per BUILD_LOG.md.

Failure modes covered:
  F-BLANK        — compact.hardware undefined (telemetry read `—`)
  F-DEADFIELD     — top-level `hardware` AND `backend_notice` linger unread
  F-FALLBACKLIE   — psutil-import-fail fallback claimed free_gb == total_gb
  F-FAKEFIT       — missing fit.verdict rendered a fake "good" chip
  (discovered)    — compact.local_models.items dropped `endpoint`, which
                    chat.html's selectModel()/renderModelInfo()/chat-send
                    payload read directly off gallery.installed[] entries;
                    fixed as part of the §2.2 data-source swap so it stays a
                    lossless enrichment (A1), not a lossy one.
"""

from __future__ import annotations

import builtins
import os
import sys

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


# ---------------------------------------------------------------------------
# §2.1 — nest the snapshot into compact.hardware; delete the top-level key
# ---------------------------------------------------------------------------

class _FakeBackend:
    model_name = "mlx-community/Qwen3-8B-4bit"


class _FakeRouter:
    backend_name = "mlx"
    _backend = _FakeBackend()


def _client(monkeypatch):
    from fastapi.testclient import TestClient
    import arail.portal.app as app_mod

    monkeypatch.setattr(app_mod, "_get_primary_router", lambda: _FakeRouter())
    return TestClient(app_mod.app), app_mod


def test_compact_hardware_is_the_real_snapshot_not_undefined(monkeypatch, tmp_path):
    """F-BLANK: compact.hardware must carry the real _local_memory_snapshot()
    values — the frontend's tele-hw/tele-vram telemetry reads exactly this
    path (chat.html: `const hw = d.compact && d.compact.hardware;`)."""
    client, app_mod = _client(monkeypatch)
    import arail.chat as chat_mod

    monkeypatch.setenv("ARAIL_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setattr(app_mod, "_local_memory_snapshot", lambda: {
        "label": "Apple M5",
        "gpu_label": None,
        "total_gb": 24.0,
        "used_gb": 6.0,
        "free_gb": 18.0,
    })
    monkeypatch.setattr(chat_mod, "gallery_view", lambda: {
        "installed": [], "catalog": [], "runtime_counts": {},
    })

    r = client.get("/api/chat/models")
    assert r.status_code == 200
    body = r.json()

    hw = body["compact"]["hardware"]
    assert hw["free_gb"] == 18.0
    assert hw["total_gb"] == 24.0
    assert hw["label"] == "Apple M5"


def test_no_top_level_hardware_key_left_unread(monkeypatch, tmp_path):
    """F-DEADFIELD (BLOCK-1): a top-level `hardware` key must not linger
    alongside compact.hardware — that's a second, unread copy of the exact
    class this sprint exists to kill."""
    client, app_mod = _client(monkeypatch)
    import arail.chat as chat_mod

    monkeypatch.setenv("ARAIL_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setattr(app_mod, "_local_memory_snapshot", lambda: {
        "label": "Apple M5", "gpu_label": None,
        "total_gb": 24.0, "used_gb": 6.0, "free_gb": 18.0,
    })
    monkeypatch.setattr(chat_mod, "gallery_view", lambda: {
        "installed": [], "catalog": [], "runtime_counts": {},
    })

    r = client.get("/api/chat/models")
    assert r.status_code == 200
    assert "hardware" not in r.json()


# ---------------------------------------------------------------------------
# F-FALLBACKLIE — psutil-import-fail path must never claim free_gb==total_gb
# ---------------------------------------------------------------------------

def test_memory_snapshot_psutil_fallback_never_fabricates_free_gb(monkeypatch):
    """A failed psutil probe must leave free_gb at 0.0 (→ Unknown verdict),
    never set free_gb = total_gb — that's an optimistic fabrication that
    would falsely render a "Good" fit chip on a machine we know nothing
    about (F-FALLBACKLIE)."""
    import arail.portal.app as app_mod

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "psutil":
            raise ImportError("psutil unavailable (simulated)")
        return real_import(name, *args, **kwargs)

    def _fake_check_output(cmd, timeout=3):
        if cmd[:2] == ["sysctl", "-n"] and cmd[2] == "hw.memsize":
            return str(32 * (1024 ** 3)).encode()  # 32 GB total
        if cmd[:2] == ["sysctl", "-n"] and cmd[2] == "machdep.cpu.brand_string":
            return b"Apple M5\n"
        raise AssertionError(f"unexpected subprocess call: {cmd}")

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    monkeypatch.setattr(app_mod.sys, "platform", "darwin")
    monkeypatch.setattr(app_mod.subprocess, "check_output", _fake_check_output)
    monkeypatch.setattr(app_mod.shutil, "which", lambda _name: None)  # no nvidia-smi

    snapshot = app_mod._local_memory_snapshot()

    assert snapshot["total_gb"] == pytest.approx(32.0, abs=0.1)
    assert snapshot["free_gb"] == 0.0, (
        f"psutil-fail fallback must yield free_gb=0, got {snapshot['free_gb']} "
        f"(total_gb={snapshot['total_gb']}) — a fabricated 'free==total' reading"
    )
    assert snapshot["free_gb"] != snapshot["total_gb"]

    # And the downstream verdict must be Unknown, never a fake Good.
    verdict = app_mod._fit_verdict_label(14.0, snapshot["free_gb"])
    assert verdict == "Unknown"


# ---------------------------------------------------------------------------
# §2.2 — rail data source: compact.local_models.items carries real fit
# (and did not silently drop `endpoint`, which routing depends on)
# ---------------------------------------------------------------------------

def test_local_model_entry_carries_real_fit_and_preserves_endpoint(monkeypatch, tmp_path):
    """compact.local_models.items (the list chat.html now reads for
    State.models) must carry a real fit.verdict AND the top-level `endpoint`
    field that gallery.installed[] used to provide — chat.html's
    selectModel()/renderModelInfo() and the outgoing chat-send payload read
    `m.endpoint` directly. _build_local_model_entry previously nested it
    only under `overlay.endpoint`, which would have silently broken routing
    to non-default local backends (e.g. the MLX OpenAI server) the moment
    State.models stopped sourcing from gallery.installed[]."""
    client, app_mod = _client(monkeypatch)
    import arail.chat as chat_mod

    monkeypatch.setenv("ARAIL_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setattr(app_mod, "_local_memory_snapshot", lambda: {
        "label": "Apple M5", "gpu_label": None,
        "total_gb": 24.0, "used_gb": 6.0, "free_gb": 18.0,
    })
    monkeypatch.setattr(chat_mod, "gallery_view", lambda: {
        "installed": [
            {
                "id": "mlx-community/Qwen3-8B-4bit",
                "runtime": "mlx-openai",
                "size_gb": 7.8,
                "modified": "2026-04-27T10:00:00Z",
                "endpoint": "http://127.0.0.1:11435/v1",
            }
        ],
        "catalog": [], "runtime_counts": {"mlx-openai": 1},
    })

    r = client.get("/api/chat/models")
    assert r.status_code == 200
    item = r.json()["compact"]["local_models"]["items"][0]

    assert item["fit"]["verdict"] == "Good"
    assert item["endpoint"] == "http://127.0.0.1:11435/v1", (
        "compact.local_models.items[*].endpoint must mirror gallery.installed's "
        "endpoint field — chat.html reads m.endpoint directly for routing"
    )


# ---------------------------------------------------------------------------
# §2.4 — dead `backend_notice` field deleted
# ---------------------------------------------------------------------------

def test_build_chat_result_no_longer_returns_backend_notice():
    """F-DEADFIELD: `backend_notice` (six weeks unread — F8) must not
    reappear in the chat-send response shape. The honest fit chip +
    warmth badge (this sprint) supersede it."""
    from arail.portal import app as app_mod
    from arail.router.backends import ModelResponse

    response = ModelResponse(
        text="hello",
        model="ai-eng:latest",
        tokens_used=3,
        backend="aerollm",
        latency_ms=42.0,
    )
    result = app_mod._build_chat_result(response, wants_deep=False)

    assert "backend_notice" not in result
    assert result["reply"] == "hello"
    assert result["backend"] == "aerollm"


def test_backend_notice_source_fully_removed():
    """Grep-level regression: the dead `_backend_notices` dict / variable
    must not reappear in app.py (F8/F-DEADFIELD)."""
    app_py = os.path.join(_REPO_ROOT, "src", "arail", "portal", "app.py")
    with open(app_py, "r", encoding="utf-8") as f:
        text = f.read()
    assert "backend_notice" not in text
    assert "_backend_notices" not in text


# ---------------------------------------------------------------------------
# §2.2 (frontend source) — rail reads the truthful list; both 'good'
# fallbacks in the reconciled path are gone
# ---------------------------------------------------------------------------

def test_chat_html_state_models_sources_from_compact_local_models_items():
    text = _chat_html_text()
    assert (
        "State.models = (d.compact && d.compact.local_models "
        "&& d.compact.local_models.items) || [];"
    ) in text
    assert "State.models = (d.gallery && d.gallery.installed) || [];" not in text


def test_chat_html_rail_and_active_fit_fallback_to_unknown_not_good():
    """C2, closed out (sprints/2026-08-11-two-slot-chat-models Phase 5):
    renderModelRail and renderActiveCard — the two sites this test
    originally pinned — are gone (both collapsed onto the picker system).
    That leaves makeOpt() as the ONLY row renderer and thus the only
    remaining site for this fallback; the 'known survivor' BUILD_LOG.md
    flagged for follow-up is fixed here, closing the last F-FAKEFIT gap."""
    text = _chat_html_text()
    honest_fallback = "const verdict = m.fit && m.fit.verdict ? m.fit.verdict : 'Unknown';"
    lying_fallback = "const verdict = m.fit && m.fit.verdict ? m.fit.verdict : 'good';"

    assert text.count(honest_fallback) == 1
    assert text.count(lying_fallback) == 0


# ---------------------------------------------------------------------------
# §2.5 — References panel pointer corrected
# ---------------------------------------------------------------------------

def test_chat_html_references_panel_points_at_real_gallery_view_location():
    text = _chat_html_text()
    assert "src/arail/chat/gallery.py" not in text, (
        "phantom src/arail/chat/gallery.py pointer must be gone"
    )
    assert '<code class="inline-path">src/arail/chat/__init__.py</code>' in text
