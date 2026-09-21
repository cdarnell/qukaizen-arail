"""S5 — flight recorder: off by default, redacted, capped, latched at call
start (F10). Covers agent_trace's recorder toggle and the router chokepoint's
body-capture wiring end to end (redact.py's own unit tests cover the
redaction passes in isolation).
"""

from __future__ import annotations

import pytest

from arail import agent_context, agent_trace, config
from arail.router.backends import BaseBackend, ModelResponse
from arail.router.core import ModelRouter


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    agent_context._reset_for_tests()
    agent_trace._reset_for_tests()
    yield
    agent_context._reset_for_tests()
    agent_trace._reset_for_tests()


class _FakeBackend(BaseBackend):
    def complete(self, prompt, *a, **kw):
        return ModelResponse(text=f"response to: {prompt}", model="m",
                             tokens_used=3, backend="fake", latency_ms=1.0)

    def stream_complete(self, prompt, *a, **kw):
        yield "resp"
        yield " text"
        yield ModelResponse(text="resp text", model="m", tokens_used=3,
                            backend="fake", latency_ms=1.0)

    def health_check(self):
        return True


def _router() -> ModelRouter:
    return ModelRouter.from_backend(_FakeBackend(), "fake")


# ---------------------------------------------------------------------------
# recorder_on() / recorder_state() / set_recorder_enabled()
# ---------------------------------------------------------------------------

def test_recorder_off_by_default_on_fresh_lab():
    assert agent_trace.recorder_on() is False
    assert agent_trace.recorder_state() == {"enabled": False, "changed_at": None}


def test_set_recorder_enabled_persists(tmp_path):
    result = agent_trace.set_recorder_enabled(True)
    assert result["enabled"] is True
    assert result["changed_at"] is not None
    assert (tmp_path / "flight_recorder.json").exists()


def test_recorder_state_survives_reload(tmp_path):
    agent_trace.set_recorder_enabled(True)
    agent_trace._recorder_enabled = None  # simulate a fresh process re-read
    assert agent_trace.recorder_on() is True


def test_recorder_corrupt_file_fails_open_to_off(tmp_path):
    (tmp_path / "flight_recorder.json").write_text("{not json")
    assert agent_trace.recorder_on() is False


# ---------------------------------------------------------------------------
# W4 baseline — recorder off: zero prompt/response keys anywhere in the
# trace. Recorder on: bodies present, redacted, capped.
# ---------------------------------------------------------------------------

def test_recorder_off_no_bodies_captured():
    router = _router()
    with agent_context.agent_call("researcher"):
        router.complete("a secret-looking prompt sk-notreallyplantedhere012345")
    rec = agent_trace.ring(1)[0]
    assert rec["bodies"] is None


def test_recorder_on_bodies_captured_and_redacted():
    agent_trace.set_recorder_enabled(True)
    router = _router()
    planted = "sk-plantedkeyshouldberedacted0123456789"
    with agent_context.agent_call("researcher"):
        router.complete(f"prompt with {planted} inside")
    rec = agent_trace.ring(1)[0]
    assert rec["bodies"] is not None
    assert planted not in rec["bodies"]["prompt"]
    assert rec["bodies"]["redactions"] >= 1


def test_recorder_on_bodies_captured_for_streaming():
    agent_trace.set_recorder_enabled(True)
    router = _router()
    with agent_context.agent_call("buddy"):
        list(router.stream_complete("hi"))
    rec = agent_trace.ring(1)[0]
    assert rec["bodies"] is not None
    assert rec["bodies"]["response"] == "resp text"


def test_recorder_off_no_bodies_for_streaming():
    router = _router()
    with agent_context.agent_call("buddy"):
        list(router.stream_complete("hi"))
    rec = agent_trace.ring(1)[0]
    assert rec["bodies"] is None


# ---------------------------------------------------------------------------
# F10 — latched at call start, both directions
# ---------------------------------------------------------------------------

def test_f10_off_to_on_mid_call_does_not_capture():
    """Flipping the recorder on partway through a call must not capture
    that in-flight call -- only calls that start after the flip do."""

    class _SlowBackend(_FakeBackend):
        def complete(self, prompt, *a, **kw):
            # Recorder flips on *during* this call.
            agent_trace.set_recorder_enabled(True)
            return super().complete(prompt, *a, **kw)

    router = ModelRouter.from_backend(_SlowBackend(), "fake")
    with agent_context.agent_call("researcher"):
        router.complete("hi")
    rec = agent_trace.ring(1)[0]
    assert rec["bodies"] is None  # latched off at call start


def test_f10_on_to_off_mid_call_still_captures():
    """The safer half: turning it off mid-call still captures the call
    that was already in flight when it was on."""
    agent_trace.set_recorder_enabled(True)

    class _SlowBackend(_FakeBackend):
        def complete(self, prompt, *a, **kw):
            agent_trace.set_recorder_enabled(False)
            return super().complete(prompt, *a, **kw)

    router = ModelRouter.from_backend(_SlowBackend(), "fake")
    with agent_context.agent_call("researcher"):
        router.complete("hi")
    rec = agent_trace.ring(1)[0]
    assert rec["bodies"] is not None  # latched on at call start


# ---------------------------------------------------------------------------
# W4/V5 — researcher.py and browser.py stop writing bodies to
# activity.jsonl (the pre-existing hole this whole slice exists to close).
# ---------------------------------------------------------------------------

def test_researcher_activity_log_carries_no_body(monkeypatch):
    from arail.activity import activity_log
    from arail.agents import researcher

    class _R:
        def complete(self, prompt, max_tokens=512, temperature=0.7, system=None):
            return type("Resp", (), {
                "text": "a fully generated answer with sensitive-looking content",
                "model": "m", "backend": "b",
            })()

    activity_log._buffer.clear()  # deque has maxlen=200; clear, not index, to isolate new events
    researcher._llm_complete(_R(), "a prompt with sensitive-looking content")
    new_events = list(activity_log._buffer)
    done = [e for e in new_events if "LLM call completed" in e.get("message", "")]
    assert done
    trace = done[0]["data"]["prompt_trace"]
    assert "prompt" not in trace
    assert "response" not in trace
    # And the raw prompt/response text is nowhere in the emitted event at all.
    import json as _json
    blob = _json.dumps(done[0])
    assert "sensitive-looking content" not in blob


def test_browser_navigate_activity_log_carries_no_body(monkeypatch):
    from arail.activity import activity_log
    from arail.agents import browser

    monkeypatch.setattr(browser, "_is_airgapped", lambda: False)
    monkeypatch.setattr(browser, "_ab_available", lambda: True)

    class _R:
        def complete(self, prompt, max_tokens=256, temperature=0.2):
            return type("Resp", (), {
                "text": '{"url": "https://example.com/secret-path-abc123"}',
            })()

    monkeypatch.setattr(browser, "_get_router", lambda: _R())
    monkeypatch.setattr(browser, "_consent_gate", lambda url, reason=None: None)
    monkeypatch.setattr(browser, "_ab_run", lambda cmd, timeout=30: {"success": True})

    activity_log._buffer.clear()
    browser.chat("go to a secret path")
    new_events = list(activity_log._buffer)
    nav = [e for e in new_events if "Navigation plan" in e.get("message", "")]
    assert nav
    trace = nav[0]["data"]["prompt_trace"]
    assert "prompt" not in trace
    assert "response" not in trace


def test_next_call_after_flip_off_respects_new_state():
    agent_trace.set_recorder_enabled(True)
    router = _router()
    with agent_context.agent_call("researcher"):
        router.complete("first")
    agent_trace.set_recorder_enabled(False)
    with agent_context.agent_call("researcher"):
        router.complete("second")
    recs = agent_trace.ring(2)
    assert recs[0]["bodies"] is not None
    assert recs[1]["bodies"] is None
