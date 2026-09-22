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
    # lan_exposed is additive (B5/decision (b)) -- assert the pre-existing
    # fields precisely rather than exact dict equality, so a genuinely new
    # field doesn't look like a shape regression here.
    state = agent_trace.recorder_state()
    assert state["enabled"] is False
    assert state["changed_at"] is None


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
    from arail.agents import researcher

    class _R:
        def complete(self, prompt, max_tokens=512, temperature=0.7, system=None):
            return type("Resp", (), {
                "text": "a fully generated answer with sensitive-looking content",
                "model": "m", "backend": "b",
            })()

    # Read researcher.py's OWN bound reference to activity_log -- NOT a
    # fresh `from arail.activity import activity_log`. Both name the same
    # object in the common case, but a test elsewhere in the same session
    # that does `importlib.reload(arail.activity)` (tests/test_boot_
    # security_scan.py does exactly this) rebinds arail.activity's module
    # attribute to a brand-new ActivityLog() while researcher.py's own
    # `from arail.activity import activity_log` (captured once, at
    # researcher.py's own import time) keeps pointing at the pre-reload
    # object. A fresh re-import in the test would then clear and inspect
    # a DIFFERENT object than the one researcher._llm_complete() actually
    # emits into -- exactly what produced this test's `assert []` failure
    # order-dependently. Reading it off `researcher` itself guarantees
    # we're looking at whatever object the code under test really uses.
    log = researcher.activity_log
    log._buffer.clear()  # deque has maxlen=200; clear, not index, to isolate new events
    researcher._llm_complete(_R(), "a prompt with sensitive-looking content")
    new_events = list(log._buffer)
    done = [e for e in new_events if "LLM call completed" in e.get("message", "")]
    # Never pass vacuously: an empty `done` proves nothing about bodies,
    # it just means nothing was observed. Fail loudly and specifically.
    assert done, (
        f"no 'LLM call completed' event was emitted at all (buffer had "
        f"{len(new_events)} other event(s)) -- this test proves nothing "
        f"about body redaction unless a real event was captured first"
    )
    trace = done[0]["data"]["prompt_trace"]
    assert "prompt" not in trace
    assert "response" not in trace
    # And the raw prompt/response text is nowhere in the emitted event at all.
    import json as _json
    blob = _json.dumps(done[0])
    assert "sensitive-looking content" not in blob


def test_browser_navigate_activity_log_carries_no_body(monkeypatch):
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

    # Same reasoning as the researcher test above: read browser.py's own
    # bound reference, not a fresh re-import, so a reloaded arail.activity
    # elsewhere in the session can't silently point this test at an
    # object browser.py never actually writes into.
    log = browser.activity_log
    log._buffer.clear()
    browser.chat("go to a secret path")
    new_events = list(log._buffer)
    nav = [e for e in new_events if "Navigation plan" in e.get("message", "")]
    assert nav, (
        f"no 'Navigation plan' event was emitted at all (buffer had "
        f"{len(new_events)} other event(s)) -- this test proves nothing "
        f"about body redaction unless a real event was captured first"
    )
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


# ---------------------------------------------------------------------------
# B5 (REVIEW.md) / operator decision (b), SPRINT.md
# 2026-09-20-buddy-front-and-center: the LAN-bind x live-recorder warning.
# recorder_state() and set_recorder_enabled() both carry a "lan_exposed"
# flag Admin's banner and the recorder toggle's own copy read -- both
# branches (loopback-safe, non-loopback-exposed) pinned here so the
# warning can't silently stop firing.
# ---------------------------------------------------------------------------

def test_recorder_state_not_lan_exposed_on_loopback_bind(monkeypatch):
    monkeypatch.setenv("BIND_ADDR", "127.0.0.1")
    agent_trace.set_recorder_enabled(True)
    state = agent_trace.recorder_state()
    assert state["enabled"] is True
    assert state["lan_exposed"] is False


def test_recorder_state_lan_exposed_on_non_loopback_bind(monkeypatch):
    monkeypatch.setenv("BIND_ADDR", "0.0.0.0")
    agent_trace.set_recorder_enabled(True)
    state = agent_trace.recorder_state()
    assert state["enabled"] is True
    assert state["lan_exposed"] is True


def test_set_recorder_enabled_return_value_carries_lan_exposed_too(monkeypatch):
    """The toggle endpoint returns set_recorder_enabled()'s dict directly
    (see admin_flight_recorder in portal/app.py) -- the flag has to be on
    THIS return value, not only on the separate recorder_state() getter,
    or the toggle's own response would omit it on the very click that
    turns the recorder on."""
    monkeypatch.setenv("BIND_ADDR", "192.168.1.50")
    result = agent_trace.set_recorder_enabled(True)
    assert result["lan_exposed"] is True

    result = agent_trace.set_recorder_enabled(False)
    # lan_exposed reflects the bind address, not the enabled flag -- it
    # stays True (still LAN-bound) even though the recorder is now off;
    # callers gate the *warning* on enabled AND lan_exposed together.
    assert result["lan_exposed"] is True
    assert result["enabled"] is False


def test_lan_exposed_false_when_bind_addr_unreadable(monkeypatch):
    """_lan_exposed() must never raise -- an unreadable/malformed
    BIND_ADDR should fail to warn, not crash the recorder-status
    endpoint an operator is trying to check."""
    import arail.config as config_mod

    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(config_mod, "bind_is_loopback", _boom)
    assert agent_trace._lan_exposed() is False
    # And the surrounding recorder_state() call still succeeds.
    state = agent_trace.recorder_state()
    assert state["lan_exposed"] is False
