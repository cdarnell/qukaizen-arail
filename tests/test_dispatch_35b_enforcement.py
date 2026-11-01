"""Test the server-side answering-model parameter ceiling.

Sprint: 2026-05-03-models-admin-dashboard
Updated: 2026-05-10-chat-model-sync (floor lowered 35B → 30B, routing updated)
Updated: 2026-08-04/05 model-inference-hardening — the 30B "force to deep"
reroute was replaced by a strict primary ceiling (arail.registry.ceiling):
primary candidates must be verifiably < 8B params, and a model at/over that
(or of unknown size) is REFUSED outright rather than silently substituted
for a bigger one. There is no more upward escalation: the client's backend
choice never bumps a primary request onto the deep backend. The deep
backend is reached only when the client explicitly asks for it
(backend="airllm"/"aerollm"), and even then the requested model must fit
the discovered-hardware secondary cap.

Headline scenario (current):
  Client posts to `/api/chat` with `backend: "mlx"` (a non-Deep backend) AND
  a model whose params are >= 8B (e.g. Llama-3.1-70B). The server MUST
  refuse loudly (error_result) — it must NOT silently reroute to the deep
  backend and answer with a different model than requested.

We don't actually invoke a real model — we mock both the Deep backend and
the primary router and assert which one was (or wasn't) called.
"""

from __future__ import annotations

import asyncio
import importlib
import os
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fakes — minimal stand-ins for the Deep / primary backends
# ---------------------------------------------------------------------------

@dataclass
class _FakeResponse:
    text: str = "ok"
    backend: str = "fake"
    model: str = "fake-model"
    latency_ms: float = 1.0
    tokens_used: int = 1
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


class _FakeDeepBackend:
    """Stand-in for the AirLLM optional backend."""
    backend_name = "airllm"
    model_name = "Llama-3.1-70B"

    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def complete(self, prompt, max_tokens=None, temperature=None, top_p=None,
                 *, system=None, messages=None):
        self.calls.append({"prompt": prompt, "max_tokens": max_tokens,
                           "system": system, "messages": messages})
        return _FakeResponse(backend="airllm", model="Llama-3.1-70B")


class _FakePrimaryBackend:
    backend_name = "mlx"
    model_name = "mlx-default-7B"


class _FakePrimaryRouter:
    backend_name = "mlx"
    _backend = _FakePrimaryBackend()

    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def complete(self, prompt, max_tokens=None, temperature=None, top_p=None,
                 *, system=None, messages=None):
        self.calls.append({"prompt": prompt, "max_tokens": max_tokens,
                           "system": system, "messages": messages})
        return _FakeResponse(backend="mlx", model="mlx-default-7B")


@pytest.fixture()
def patched_app(monkeypatch):
    from arail.portal import app as app_mod

    fake_deep = _FakeDeepBackend()
    fake_router = _FakePrimaryRouter()

    # Hard-floor routing now calls _resolve_default_deep_backend() instead of
    # hardcoding "airllm". Pin it to "airllm" so the fixture's fake_deep
    # (which only handles "airllm") is still selected on the override path.
    monkeypatch.setattr(app_mod, "_resolve_default_deep_backend", lambda: "airllm")
    monkeypatch.setattr(app_mod, "_get_optional_chat_backend",
                        lambda name: fake_deep if name == "airllm" else (_ for _ in ()).throw(ValueError(name)))
    monkeypatch.setattr(app_mod, "_get_primary_router", lambda: fake_router)
    monkeypatch.setattr(app_mod, "_is_airllm_installed", lambda: True)
    return app_mod, fake_deep, fake_router


# ---------------------------------------------------------------------------
# Primary ceiling refuses >= 8B, regardless of the client's claimed backend
# ---------------------------------------------------------------------------

def test_chat_with_backend_mlx_and_70b_model_is_refused(patched_app, monkeypatch):
    """HEADLINE: client lies about backend, but a 70B model can never be the
    primary answering model — the server refuses, it does NOT silently
    reroute to Deep and answer with a different model than requested."""
    app_mod, fake_deep, fake_router = patched_app
    # Ensure the fallback default model is also under the ceiling.
    monkeypatch.setenv("MODEL_NAME", "Qwen2.5-3B-Instruct")

    client = TestClient(app_mod.app)
    r = client.post("/api/chat", json={
        "message": "hello",
        "backend": "mlx",  # client claims they want mlx (non-Deep)
        "model": "Llama-3.1-70B",  # 70B → over the 8B primary ceiling
    })
    assert r.status_code == 200, r.text
    body = r.json()
    # Neither backend actually generated a reply — this is a refusal, not a
    # silent substitution.
    assert len(fake_deep.calls) == 0, "Deep backend was invoked — silent reroute leaked"
    assert len(fake_router.calls) == 0, "Primary router was invoked with an over-ceiling model"
    assert "error" in body
    assert "ceiling" in (body.get("error") or "").lower()


def test_chat_with_no_backend_and_70b_model_is_refused(patched_app, monkeypatch):
    """Variant: backend not set at all + 70B model → still refused."""
    app_mod, fake_deep, fake_router = patched_app
    monkeypatch.setenv("MODEL_NAME", "Qwen2.5-3B-Instruct")

    r = TestClient(app_mod.app).post("/api/chat", json={
        "message": "hello", "model": "Llama-3.1-70B",
    })
    assert r.status_code == 200, r.text
    assert len(fake_deep.calls) == 0
    assert len(fake_router.calls) == 0
    assert "ceiling" in (r.json().get("error") or "").lower()


def test_chat_with_llama4_maverick_is_refused(patched_app, monkeypatch):
    """Llama-4 Maverick (400B via override) is also refused as primary,
    regardless of the claimed backend."""
    app_mod, fake_deep, fake_router = patched_app
    monkeypatch.setenv("MODEL_NAME", "Qwen2.5-3B-Instruct")

    r = TestClient(app_mod.app).post("/api/chat", json={
        "message": "hello",
        "backend": "mlx",
        "model": "Llama-4-Maverick-17B-128E-Instruct-fp8",
    })
    assert r.status_code == 200, r.text
    assert len(fake_deep.calls) == 0
    assert len(fake_router.calls) == 0
    assert "error" in r.json()


def test_chat_with_sub_8b_model_goes_through_primary_router(patched_app, monkeypatch):
    """A genuinely small (< 8B) model is NOT refused — primary router gets it."""
    app_mod, fake_deep, fake_router = patched_app
    monkeypatch.setenv("MODEL_NAME", "Qwen2.5-3B-Instruct")

    r = TestClient(app_mod.app).post("/api/chat", json={
        "message": "hello",
        "model": "Qwen2.5-3B-Instruct",
    })
    assert r.status_code == 200, r.text
    assert len(fake_deep.calls) == 0
    assert len(fake_router.calls) == 1
    assert r.json().get("deep") is False


def test_chat_branch_b_is_checked_as_secondary_not_primary(patched_app, monkeypatch):
    """Column B ("2nd inference") sends `branch: "B"` in the request body.
    Before this fix, `_prepare_chat_context` never saw that signal, so any
    model dropped into column B (not just an aerollm/airllm pick — the "use
    as B" affordance works on any local model) was checked against the
    strict <8B *primary* ceiling and refused, even though the UI's own
    column-B chip labels that slot as the 2nd inference. A 70B model in
    column B must be checked against the (larger) hardware-driven secondary
    cap instead, exactly like an explicit aerollm/airllm pick would be."""
    app_mod, fake_deep, fake_router = patched_app
    monkeypatch.setenv("MODEL_NAME", "Qwen2.5-3B-Instruct")

    from arail import hardware as hardware_mod
    monkeypatch.setattr(hardware_mod, "secondary_model_cap_b", lambda *a, **k: 100.0)

    r = TestClient(app_mod.app).post("/api/chat", json={
        "message": "hello",
        "backend": "mlx",  # not an explicit deep-backend pick
        "model": "Llama-3.1-70B",
        "branch": "B",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert "error" not in body or not body["error"], (
        f"column B should be checked as secondary (hardware cap), not "
        f"primary — got refusal: {body}"
    )
    assert len(fake_router.calls) == 1, "primary router should have answered on column B"


def test_chat_branch_a_default_still_refuses_70b(patched_app, monkeypatch):
    """Sanity check the fix is scoped to branch B: an unspecified/`"A"`
    branch keeps the strict primary ceiling even with a generous secondary
    cap available."""
    app_mod, fake_deep, fake_router = patched_app
    monkeypatch.setenv("MODEL_NAME", "Qwen2.5-3B-Instruct")

    from arail import hardware as hardware_mod
    monkeypatch.setattr(hardware_mod, "secondary_model_cap_b", lambda *a, **k: 100.0)

    r = TestClient(app_mod.app).post("/api/chat", json={
        "message": "hello",
        "backend": "mlx",
        "model": "Llama-3.1-70B",
    })
    assert r.status_code == 200, r.text
    assert len(fake_router.calls) == 0
    assert "ceiling" in (r.json().get("error") or "").lower()


def test_chat_dispatch_refusal_emits_activity_log(patched_app, monkeypatch):
    """The refusal path emits a warn activity_log line for audit trails."""
    app_mod, fake_deep, fake_router = patched_app
    monkeypatch.setenv("MODEL_NAME", "Qwen2.5-3B-Instruct")

    captured = []
    real_emit = app_mod.activity_log.emit

    def _spy(source, message, level="info"):
        captured.append((source, message, level))
        return real_emit(source, message, level)

    monkeypatch.setattr(app_mod.activity_log, "emit", _spy)

    r = TestClient(app_mod.app).post("/api/chat", json={
        "message": "hello",
        "backend": "mlx",
        "model": "Llama-3.1-70B",
    })
    assert r.status_code == 200
    # Find a chat-source emit that mentions the ceiling refusal — that's the
    # audit line.
    matches = [m for src, m, lvl in captured
               if src == "chat" and "ceiling" in m.lower() and lvl == "warn"]
    assert matches, f"expected a warn activity_log line about the ceiling refusal; got: {captured}"


# ---------------------------------------------------------------------------
# Streaming path inherits the same refusal (A7)
# ---------------------------------------------------------------------------

def test_stream_path_also_refuses_70b(patched_app, monkeypatch):
    """A7: _run_chat_completion_stream also calls _prepare_chat_context, so
    the refusal is inherited for free. We exercise the dispatch logic by
    invoking the helper directly with backend=mlx + a 70B model and
    asserting the prepared context carries an error_result, not a deep
    reroute."""
    app_mod, fake_deep, fake_router = patched_app
    monkeypatch.setenv("MODEL_NAME", "Qwen2.5-3B-Instruct")

    ctx = app_mod._prepare_chat_context(
        message="hi", history=[],
        backend_override="mlx",
        model_override="Llama-3.1-70B",
    )
    assert ctx.get("error_result") is not None
    assert "ceiling" in ctx["error_result"]["reply"].lower()
    # No silent substitution: deep backend was never engaged (backend="mlx"
    # never sets wants_deep, so the ceiling short-circuits before any deep
    # backend construction is attempted).
    assert fake_deep.calls == []


def test_stream_path_does_not_refuse_for_sub_8b_model(patched_app, monkeypatch):
    """A genuinely small model is not refused."""
    app_mod, fake_deep, fake_router = patched_app
    monkeypatch.setenv("MODEL_NAME", "Qwen2.5-3B-Instruct")

    ctx = app_mod._prepare_chat_context(
        message="hi", history=[],
        backend_override=None,
        model_override="Qwen2.5-3B-Instruct",
    )
    assert ctx.get("error_result") is None
    assert ctx.get("deep_backend") is None
    assert ctx["wants_deep"] is False


# ---------------------------------------------------------------------------
# AirLLM not installed (A3) — explicitly requesting the deep backend still
# degrades gracefully
# ---------------------------------------------------------------------------

def test_explicit_deep_backend_when_airllm_missing_returns_friendly_error(patched_app, monkeypatch):
    """A3: client explicitly asks for backend=airllm, but AirLLM isn't
    installed → server emits an error_result, NOT a 5xx."""
    app_mod, fake_deep, fake_router = patched_app

    def _raise(name):
        raise ImportError(f"{name} backend not installed (test)")

    monkeypatch.setattr(app_mod, "_get_optional_chat_backend", _raise)

    r = TestClient(app_mod.app).post("/api/chat", json={
        "message": "hello",
        "backend": "airllm",
        "model": "Llama-3.1-70B",
    })
    # 200 + readable reply (per _optional_backend_error_result contract)
    assert r.status_code == 200
    body = r.json()
    assert "reply" in body
    # Either reply or error mentions install / AirLLM / not installed
    text = (body.get("reply") or "") + " " + (body.get("error") or "")
    assert ("not installed" in text.lower()
            or "airllm" in text.lower()
            or "upgrade" in text.lower())


# ---------------------------------------------------------------------------
# MODEL_NAME env var fallback when no explicit model passed
# ---------------------------------------------------------------------------

def test_refusal_when_active_backend_model_is_over_ceiling(patched_app, monkeypatch):
    """When no per-request model override is given, the ceiling is still
    checked against whatever model the resolved primary backend is actually
    carrying — it does not silently escalate to Deep."""
    app_mod, fake_deep, fake_router = patched_app
    monkeypatch.setattr(fake_router._backend, "model_name", "Llama-3.1-70B")

    ctx = app_mod._prepare_chat_context(
        message="hi", history=[],
        backend_override=None,
        model_override=None,
    )
    assert ctx.get("error_result") is not None
    assert "ceiling" in ctx["error_result"]["reply"].lower()
    assert fake_deep.calls == []
