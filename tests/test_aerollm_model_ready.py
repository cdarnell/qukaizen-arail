"""`_aerollm_model_ready()` and the `model_ready` field it feeds.

Reported live: the chat page's Compare auto-pick kept defaulting Column B
to AeroLLM · Qwen2.5-7B-Instruct-4bit — a model never downloaded on the
machine — even after the auto-warm-on-page-load bug was fixed. Root
cause: `_is_aerollm_installed()` only ever answered "is the aerollm_api
PACKAGE importable," a question completely separate from "will loading
THIS configured model actually work." `deep.installed` and
`optional_backends[].installed` reported true regardless of whether the
model's weights existed, so the client-side auto-pick — which only
checked `installed` — kept defaulting to a selection guaranteed to fail
with "AeroLLM model dir not found."

This suite pins the real, disk-backed check and its two response fields.
The client-side half was originally the `deepEntries`-filtered auto-pick
in `setCompare()`; sprints/2026-08-11-two-slot-chat-models Phase 5
replaced `deepEntries` with the server's own `slots.deep` block and a
dedicated Deep picker (`renderDeepPicker`/`selectDeepModel`) — the
`model_ready`-gates-auto-pick property now lives in `setCompare()`
reading `State.deepSlot.model_ready` directly, still covered here by
source-level assertions since it's plain JS with no server round-trip.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import arail.portal.app as appmod
from arail.portal.app import app

CHAT_HTML = (
    Path(__file__).resolve().parent.parent
    / "src" / "arail" / "portal" / "templates" / "chat.html"
).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# _aerollm_model_ready() — the real disk check
# ---------------------------------------------------------------------------

def test_ready_true_for_a_real_directory(tmp_path, monkeypatch):
    (tmp_path / "some-model").mkdir()
    monkeypatch.setenv("ARAIL_MODELS_DIR", str(tmp_path))
    assert appmod._aerollm_model_ready("some-model") is True


def test_ready_false_for_a_missing_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("ARAIL_MODELS_DIR", str(tmp_path))
    assert appmod._aerollm_model_ready("never-downloaded") is False


def test_ready_false_for_empty_or_none_name(tmp_path, monkeypatch):
    monkeypatch.setenv("ARAIL_MODELS_DIR", str(tmp_path))
    assert appmod._aerollm_model_ready("") is False
    assert appmod._aerollm_model_ready(None) is False


def test_ready_handles_absolute_paths_directly(tmp_path):
    real = tmp_path / "abs-model"
    real.mkdir()
    assert appmod._aerollm_model_ready(str(real)) is True
    assert appmod._aerollm_model_ready(str(tmp_path / "abs-missing")) is False


def test_ready_mirrors_aerollmbackend_resolution_exactly(tmp_path, monkeypatch):
    """Same precedence AeroLLMBackend.__init__ uses (router/backends.py):
    bare names join ARAIL_MODELS_DIR; the two resolvers must never drift,
    or "ready" and "actually loadable" can disagree again."""
    monkeypatch.setenv("ARAIL_MODELS_DIR", str(tmp_path / "custom-models"))
    (tmp_path / "custom-models" / "my-model").mkdir(parents=True)
    assert appmod._aerollm_model_ready("my-model") is True
    assert appmod._aerollm_model_ready("other-model") is False


# ---------------------------------------------------------------------------
# The fields it feeds — installed vs. model_ready must be independently
# observable, since that's the exact distinction the bug collapsed.
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    return TestClient(app)


def test_deep_info_reports_ready_false_when_installed_but_no_weights(client, monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_is_aerollm_installed", lambda: True)
    monkeypatch.setenv("ARAIL_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("AEROLLM_MODEL", "not-downloaded-here")
    res = client.get("/api/chat/models")
    assert res.status_code == 200
    deep = res.json()["deep"]
    assert deep["installed"] is True, "package IS importable in this scenario"
    assert deep["model_ready"] is False, (
        "the configured model's weights do NOT exist — installed=true "
        "must not imply model_ready=true"
    )


def test_deep_info_reports_ready_true_when_weights_present(client, monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_is_aerollm_installed", lambda: True)
    (tmp_path / "totally-real-model").mkdir()
    monkeypatch.setenv("ARAIL_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("AEROLLM_MODEL", "totally-real-model")
    res = client.get("/api/chat/models")
    assert res.status_code == 200
    deep = res.json()["deep"]
    assert deep["installed"] is True
    assert deep["model_ready"] is True


def test_optional_backends_aerollm_entry_carries_model_ready(client, monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_is_aerollm_installed", lambda: True)
    monkeypatch.setenv("ARAIL_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("AEROLLM_MODEL", "not-downloaded-here")
    res = client.get("/api/chat/models")
    entries = {e["id"]: e for e in res.json()["optional_backends"]}
    assert "aerollm" in entries
    assert entries["aerollm"]["installed"] is True
    assert entries["aerollm"]["model_ready"] is False


# ---------------------------------------------------------------------------
# Client-side gating — source-level (plain JS, no server round-trip)
# ---------------------------------------------------------------------------

def test_deep_slot_carries_model_ready_through_to_client_state():
    """Successor to the deepEntries version: State.deepSlot (populated in
    init() from the server's slots.deep block) is what setCompare() reads
    now — there's no longer a filtered model LIST to carry the field
    through, just the one deep-slot object."""
    assert "State.deepSlot = (d.slots && d.slots.deep) || null;" in CHAT_HTML


def test_setcompare_autopick_requires_model_ready_not_just_installed():
    assert "if (deep.model_ready) {" in CHAT_HTML, (
        "the auto-pick filter must require model_ready, not just "
        "installed — that's the entire fix"
    )
    # And the negative: installed-but-not-ready must NOT take the
    # model_ready branch (a separate, explicit "not downloaded" message).
    assert "} else if (!deep.installed) {" in CHAT_HTML


def test_setcompare_never_falls_back_to_a_broken_default_silently():
    """When nothing deep is truly ready, the honest message must
    distinguish 'not built' from 'built but no model downloaded' — never
    silently pick something and claim success."""
    assert "the deep model isn’t downloaded yet" in CHAT_HTML
    assert "QueueLLM isn’t built" in CHAT_HTML


def test_comparison_strip_never_displays_deep_model_as_a_silent_fallback():
    """renderComparisonStrip() used to fall back to `deep.model` (the
    CONFIGURED default) whenever State.bId was unset — displaying a
    model name in the summary card as if it were genuinely selected,
    even when the auto-pick logic had correctly refused to select it.
    State.bId must be the only source of truth for what the strip shows
    as selected; anything else is the strip lying about state."""
    assert "State.bId || deep.model" not in CHAT_HTML
    assert "not selected yet" in CHAT_HTML
