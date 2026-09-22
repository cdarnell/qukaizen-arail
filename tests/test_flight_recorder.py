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


# ---------------------------------------------------------------------------
# S1 (REVIEW.md) / operator decision (c), SPRINT.md
# 2026-09-20-buddy-front-and-center: "recorder off" must mean stop
# capturing AND stop showing. A record's bodies are latched at call start
# (F10) and never change afterward, so find()/lanes_snapshot()/subscribe()
# must each re-check *current* recorder state before serving them, the
# same way /api/agents/prompts already does.
# ---------------------------------------------------------------------------

def test_find_hides_bodies_once_recorder_turned_off():
    agent_trace.set_recorder_enabled(True)
    agent_trace.record(trace_id="a" * 16, agent_id="researcher", kind="agent",
                       bodies={"prompt": "p", "response": "r"})

    rec = agent_trace.find("a" * 16)
    assert rec["bodies"] == {"prompt": "p", "response": "r"}

    agent_trace.set_recorder_enabled(False)
    rec2 = agent_trace.find("a" * 16)
    assert rec2["bodies"] is None


def test_find_reveals_bodies_again_if_recorder_turned_back_on():
    """The read-gate is reversible -- purge (below) is the permanent
    control. Turning the recorder back on re-exposes a record captured
    earlier while it was on; this is deliberate, not a bug, since the
    record itself was never mutated by the gate."""
    agent_trace.set_recorder_enabled(True)
    agent_trace.record(trace_id="b" * 16, agent_id="researcher", kind="agent",
                       bodies={"prompt": "p", "response": "r"})
    agent_trace.set_recorder_enabled(False)
    assert agent_trace.find("b" * 16)["bodies"] is None
    agent_trace.set_recorder_enabled(True)
    assert agent_trace.find("b" * 16)["bodies"] == {"prompt": "p", "response": "r"}


def test_lanes_snapshot_last_hides_bodies_once_recorder_turned_off():
    agent_trace.set_recorder_enabled(True)
    agent_trace.record(trace_id="c" * 16, agent_id="buddy", kind="agent",
                       bodies={"prompt": "p", "response": "r"})

    snap = agent_trace.lanes_snapshot()
    lane = next(l for l in snap["lanes"] if l["id"] == "buddy")
    assert lane["last"]["bodies"] == {"prompt": "p", "response": "r"}

    agent_trace.set_recorder_enabled(False)
    snap2 = agent_trace.lanes_snapshot()
    lane2 = next(l for l in snap2["lanes"] if l["id"] == "buddy")
    assert lane2["last"]["bodies"] is None


def test_subscribe_hides_bodies_once_recorder_turned_off():
    import asyncio

    async def _run():
        agent_trace.set_recorder_enabled(True)
        gen = agent_trace.subscribe()
        task = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0)  # let subscribe() register before record()
        agent_trace.record(trace_id="d" * 16, agent_id="researcher", kind="agent",
                           bodies={"prompt": "p", "response": "r"})
        rec = await asyncio.wait_for(task, timeout=5)
        assert rec["bodies"] == {"prompt": "p", "response": "r"}

        agent_trace.set_recorder_enabled(False)
        task2 = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0)
        agent_trace.record(trace_id="e" * 16, agent_id="researcher", kind="agent",
                           bodies={"prompt": "p2", "response": "r2"})
        rec2 = await asyncio.wait_for(task2, timeout=5)
        assert rec2["bodies"] is None
        await gen.aclose()

    asyncio.run(_run())


def test_strip_bodies_if_recorder_off_no_op_when_no_bodies():
    """Fast path -- a record with no bodies at all must come back as the
    exact same object (identity, not just equality), never allocating a
    copy when the recorder is off and there is nothing to strip."""
    agent_trace.set_recorder_enabled(False)
    rec = {"trace_id": "x", "bodies": None}
    assert agent_trace._strip_bodies_if_recorder_off(rec) is rec


# ---------------------------------------------------------------------------
# S1 / operator decision (c): the permanent half -- Purge deletes bodies
# from disk AND memory, reusing jsonl_purge.purge_jsonl_bodies (the same
# mechanism activity.py's legacy-bodies purge uses, not a second one).
# ---------------------------------------------------------------------------

def test_purge_flight_recorder_bodies_strips_ring_and_disk(tmp_path):
    agent_trace.set_recorder_enabled(True)
    agent_trace.record(trace_id="f" * 16, agent_id="researcher", kind="agent",
                       model="m", bodies={"prompt": "p", "response": "r"})

    result = agent_trace.purge_flight_recorder_bodies()
    # "ok" is additive (QA F7/F8) -- assert the pre-existing fields
    # precisely rather than exact dict equality.
    assert result["purged"] == 1
    assert result["purged_memory"] == 1
    assert result["ok"] is True

    # In-memory ring: stripped in place, other fields untouched.
    rec = agent_trace.find("f" * 16)
    assert rec["bodies"] is None
    assert rec["bodies_purged"] is True
    assert rec["model"] == "m"  # non-body fields survive

    # On disk: same shape, same guarantee.
    written = (tmp_path / "agent_traces.jsonl").read_text().strip().splitlines()
    assert len(written) == 1
    import json
    on_disk = json.loads(written[0])
    assert on_disk["bodies"] is None
    assert on_disk["bodies_purged"] is True
    assert on_disk["trace_id"] == "f" * 16


def test_purge_flight_recorder_bodies_is_permanent_even_if_recorder_turned_back_on(tmp_path):
    agent_trace.set_recorder_enabled(True)
    agent_trace.record(trace_id="1" * 16, agent_id="researcher", kind="agent",
                       bodies={"prompt": "p", "response": "r"})
    agent_trace.purge_flight_recorder_bodies()
    # Unlike the reversible read-gate, purge actually deleted the bodies --
    # turning the recorder back on must not resurrect them.
    assert agent_trace.find("1" * 16)["bodies"] is None


def test_purge_flight_recorder_bodies_count_is_zero_when_nothing_captured():
    agent_trace.set_recorder_enabled(False)
    agent_trace.record(trace_id="2" * 16, agent_id="researcher", kind="agent")
    result = agent_trace.purge_flight_recorder_bodies()
    assert result["purged"] == 0
    assert result["purged_memory"] == 0
    assert result["ok"] is True


def test_purge_flight_recorder_bodies_leaves_bodyless_records_untouched(tmp_path):
    agent_trace.set_recorder_enabled(False)
    agent_trace.record(trace_id="3" * 16, agent_id="researcher", kind="agent",
                       model="m")
    result = agent_trace.purge_flight_recorder_bodies()
    assert result["purged"] == 0
    assert result["purged_memory"] == 0
    assert result["ok"] is True
    rec = agent_trace.find("3" * 16)
    # bodies_purged is an always-present field (like every other key in
    # _FIELDS) -- None here means "never had a body to purge", not "field
    # missing".
    assert rec["bodies_purged"] is None
