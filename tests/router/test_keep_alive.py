"""OllamaNativeBackend sends keep_alive so Tier 0 stays resident.

Sprint: 2026-08-11-two-slot-chat-models Part 3 rewrote _keep_alive from a
plain env-only staticmethod into a per-model instance method — the
resident (registry tier0) model now pins by default; any other
Ollama model keeps the old 2h.

CORRECTED 2026-08-17: these tests originally asserted the wire value was
the STRING "-1". That is exactly the value Ollama rejects — it parses a
string keep_alive as a Go duration, and "-1" carries no unit, so every
/api/chat call answered 400 {"error":"time: missing unit in duration
\"-1\""} and the entire local model lane was dead. The pinned value is
now the JSON number -1 (seconds; negative means keep loaded forever).
The tests had encoded the implementation rather than what the server
accepts, so they passed while the feature was completely broken. Every test here isolates the registry
(tmp file + fresh singleton) so `_is_registry_tier0_model()` never reads
or seeds a developer's real lab/data/model_registry.json.
"""

from __future__ import annotations

import tempfile

import pytest

from arail.router.backends import OllamaNativeBackend


class _CapturingSession:
    def __init__(self):
        self.bodies = []

    def post(self, url, headers=None, json=None, timeout=None, stream=False):
        self.bodies.append(json)

        class _R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"message": {"content": "ok"}, "eval_count": 1,
                        "model": "m"}
        return _R()


def _isolate_registry(monkeypatch, *, tier0_model="ai-engineer:latest"):
    """Fresh registry singleton, seeded so tier0's model_id == tier0_model."""
    from arail.registry import core as reg_core
    tmp_dir = tempfile.mkdtemp(prefix="arail-keepalive-registry-")
    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE", tmp_dir + "/model_registry.json")
    monkeypatch.setenv("MODEL_BACKEND", "ollama_native")
    monkeypatch.setenv("MODEL_NAME", tier0_model)
    monkeypatch.delenv("MODEL_API_BASE", raising=False)
    reg_core.reset_registry()


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "ai-engineer:latest")
    monkeypatch.delenv("MODEL_API_BASE", raising=False)
    be = OllamaNativeBackend()
    be._session = _CapturingSession()
    return be


# ---------------------------------------------------------------------------
# Explicit ARAIL_OLLAMA_KEEP_ALIVE always wins, regardless of tier0 status
# ---------------------------------------------------------------------------

def test_empty_env_omits_keep_alive(backend, monkeypatch):
    monkeypatch.setenv("ARAIL_OLLAMA_KEEP_ALIVE", "")
    backend.complete("hi", max_tokens=8)
    assert "keep_alive" not in backend._session.bodies[0]


def test_pin_forever_passthrough(backend, monkeypatch):
    """An operator writing -1 means "forever"; it must reach the wire as
    a number, never as the bare string Ollama refuses."""
    monkeypatch.setenv("ARAIL_OLLAMA_KEEP_ALIVE", "-1")
    backend.complete("hi", max_tokens=8)
    sent = backend._session.bodies[0]["keep_alive"]
    assert sent == -1
    assert not isinstance(sent, str)


def test_explicit_env_wins_even_when_this_is_the_tier0_model(monkeypatch, tmp_path):
    _isolate_registry(monkeypatch, tier0_model="ai-engineer:latest")
    monkeypatch.setenv("ARAIL_OLLAMA_KEEP_ALIVE", "4h")
    be = OllamaNativeBackend()
    be._session = _CapturingSession()
    be.complete("hi", max_tokens=8)
    assert be._session.bodies[0]["keep_alive"] == "4h"


def test_explicit_env_wins_even_when_pin_would_otherwise_apply(monkeypatch):
    _isolate_registry(monkeypatch, tier0_model="ai-engineer:latest")
    monkeypatch.setenv("ARAIL_OLLAMA_KEEP_ALIVE", "2h")
    be = OllamaNativeBackend()
    be._session = _CapturingSession()
    be.complete("hi", max_tokens=8)
    assert be._session.bodies[0]["keep_alive"] == "2h"


# ---------------------------------------------------------------------------
# The new matrix: tier0 model pins, everything else keeps 2h
# ---------------------------------------------------------------------------

def test_tier0_model_pins_by_default(monkeypatch):
    monkeypatch.delenv("ARAIL_OLLAMA_KEEP_ALIVE", raising=False)
    _isolate_registry(monkeypatch, tier0_model="llama-ai-eng:latest")
    be = OllamaNativeBackend()
    be.model_name = "llama-ai-eng:latest"
    be._session = _CapturingSession()
    be.complete("hi", max_tokens=8)
    sent = be._session.bodies[0]["keep_alive"]
    assert sent == -1 and not isinstance(sent, str)


def test_tier0_model_pins_even_when_bare_tag_matches_tagged_registry_entry(monkeypatch):
    """The registry's env-seeded model_id is bare ("llama-ai-eng"); a
    request against the Ollama-native tag-qualified form ("llama-ai-
    eng:latest") must still be recognized as the same model."""
    monkeypatch.delenv("ARAIL_OLLAMA_KEEP_ALIVE", raising=False)
    _isolate_registry(monkeypatch, tier0_model="llama-ai-eng")
    be = OllamaNativeBackend()
    be.model_name = "llama-ai-eng:latest"
    be._session = _CapturingSession()
    be.complete("hi", max_tokens=8)
    sent = be._session.bodies[0]["keep_alive"]
    assert sent == -1 and not isinstance(sent, str)


def test_non_tier0_model_keeps_2h(monkeypatch):
    """A model picked ad hoc from the rail — NOT the registry's resident
    slot — must not get pinned; only the resident slot pins."""
    monkeypatch.delenv("ARAIL_OLLAMA_KEEP_ALIVE", raising=False)
    _isolate_registry(monkeypatch, tier0_model="llama-ai-eng:latest")
    be = OllamaNativeBackend()
    be.model_name = "some-other-model:latest"
    be._session = _CapturingSession()
    be.complete("hi", max_tokens=8)
    assert be._session.bodies[0]["keep_alive"] == "2h"


def test_pin_disabled_restores_2h_for_the_tier0_model(monkeypatch):
    monkeypatch.delenv("ARAIL_OLLAMA_KEEP_ALIVE", raising=False)
    _isolate_registry(monkeypatch, tier0_model="llama-ai-eng:latest")
    monkeypatch.setenv("ARAIL_RESIDENT_PIN", "0")
    be = OllamaNativeBackend()
    be.model_name = "llama-ai-eng:latest"
    be._session = _CapturingSession()
    be.complete("hi", max_tokens=8)
    assert be._session.bodies[0]["keep_alive"] == "2h"


def test_registry_unavailable_falls_back_to_2h_never_raises(monkeypatch):
    """A broken/unreadable registry must never crash a chat completion —
    and must never silently pin the wrong model forever; the safe
    fallback is the old, universal 2h."""
    monkeypatch.delenv("ARAIL_OLLAMA_KEEP_ALIVE", raising=False)
    monkeypatch.setenv("MODEL_NAME", "llama-ai-eng:latest")
    import arail.registry as registry_pkg

    def _boom():
        raise RuntimeError("registry file corrupt")

    monkeypatch.setattr(registry_pkg, "get_registry", _boom)
    be = OllamaNativeBackend()
    be.model_name = "llama-ai-eng:latest"
    be._session = _CapturingSession()
    be.complete("hi", max_tokens=8)  # must not raise
    assert be._session.bodies[0]["keep_alive"] == "2h"


def test_non_ollama_tier0_backend_never_pins(monkeypatch):
    """If the registry's tier0 entry is mlx (or any non-Ollama backend),
    an Ollama-runtime model of the SAME name must not be treated as the
    resident slot — the pin is specifically about the Ollama-hosted
    resident model."""
    monkeypatch.delenv("ARAIL_OLLAMA_KEEP_ALIVE", raising=False)
    from arail.registry import core as reg_core
    tmp_dir = tempfile.mkdtemp(prefix="arail-keepalive-mlx-registry-")
    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE", tmp_dir + "/model_registry.json")
    monkeypatch.setenv("MODEL_BACKEND", "mlx")
    monkeypatch.setenv("MODEL_NAME", "same-name-model")
    reg_core.reset_registry()

    be = OllamaNativeBackend()
    be.model_name = "same-name-model"
    be._session = _CapturingSession()
    be.complete("hi", max_tokens=8)
    assert be._session.bodies[0]["keep_alive"] == "2h"


def test_chat_gallery_new_path_gets_keep_alive_too(monkeypatch):
    """__new__-constructed instances (the chat gallery's runtime-override
    path) use the same instance method; a non-tier0 model there also
    keeps 2h."""
    monkeypatch.delenv("ARAIL_OLLAMA_KEEP_ALIVE", raising=False)
    _isolate_registry(monkeypatch, tier0_model="ai-engineer:latest")
    be = OllamaNativeBackend.__new__(OllamaNativeBackend)
    be.base_url = "http://127.0.0.1:11434/v1"
    be.model_name = "llama3.2:1b"
    be.api_key = "not-needed"
    be._session = _CapturingSession()
    be.complete("hi", max_tokens=8)
    assert be._session.bodies[0]["keep_alive"] == "2h"


# ---------------------------------------------------------------------------
# The wire guard: complete()/stream_complete() normalize keep_alive at the
# request boundary, so NO producer — env, pin, a subclass, a drifted fork of
# _keep_alive on another branch — can put the bare string "-1" on the wire.
# (Ollama parses a string keep_alive as a Go duration; "-1" has no unit and
# 400s the call — the failure that painted the dashboard's "unhealthy"
# banner over a perfectly healthy lab.)
# ---------------------------------------------------------------------------

class _StreamingCapturingSession(_CapturingSession):
    """Adds the context-manager + iter_lines surface stream_complete needs."""

    def post(self, url, headers=None, json=None, timeout=None, stream=False):
        self.bodies.append(json)

        class _R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"message": {"content": "ok"}, "eval_count": 1,
                        "model": "m"}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def iter_lines(self, decode_unicode=False):
                return iter(['{"message":{"content":"ok"},"done":true,'
                             '"eval_count":1,"model":"m"}'])
        return _R()


def test_wire_guard_normalizes_a_drifted_keep_alive_override(backend, monkeypatch):
    """Simulates the fork drift that re-shipped this bug: _keep_alive
    resolving to the bare string "-1". The body must still carry the
    JSON number."""
    monkeypatch.delenv("ARAIL_OLLAMA_KEEP_ALIVE", raising=False)
    monkeypatch.setattr(backend, "_keep_alive", lambda: "-1")
    backend.complete("hi", max_tokens=8)
    sent = backend._session.bodies[0]["keep_alive"]
    assert sent == -1
    assert not isinstance(sent, str)


def test_wire_guard_leaves_real_durations_alone(backend, monkeypatch):
    monkeypatch.delenv("ARAIL_OLLAMA_KEEP_ALIVE", raising=False)
    monkeypatch.setattr(backend, "_keep_alive", lambda: "30s")
    backend.complete("hi", max_tokens=8)
    assert backend._session.bodies[0]["keep_alive"] == "30s"


def test_stream_body_keep_alive_is_normalized_too(monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "ai-engineer:latest")
    monkeypatch.delenv("MODEL_API_BASE", raising=False)
    monkeypatch.setenv("ARAIL_OLLAMA_KEEP_ALIVE", "-1")
    be = OllamaNativeBackend()
    be._session = _StreamingCapturingSession()
    list(be.stream_complete("hi", max_tokens=8))
    sent = be._session.bodies[0]["keep_alive"]
    assert sent == -1
    assert not isinstance(sent, str)
