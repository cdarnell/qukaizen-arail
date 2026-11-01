"""Unit tests for arail.agent_context — the call-scoped attribution layer.

Covers: reentrancy (same id reuses trace_id; different id nests and records
parent_agent_id), finally-reset on a raising body, sanitisation of hostile
agent_id input, and the billing_source() table exhaustively including the
unattributed row.
"""

from __future__ import annotations

import threading

import pytest

from arail import agent_context


@pytest.fixture(autouse=True)
def _clean_context():
    agent_context._reset_for_tests()
    yield
    agent_context._reset_for_tests()


# ---------------------------------------------------------------------------
# current() — never raises, returns None outside any context
# ---------------------------------------------------------------------------

def test_current_is_none_outside_any_context():
    assert agent_context.current() is None


def test_current_returns_the_active_call():
    with agent_context.agent_call("buddy") as call:
        assert agent_context.current() is call
        assert call.agent_id == "buddy"
        assert call.kind == "agent"


# ---------------------------------------------------------------------------
# Reentrancy
# ---------------------------------------------------------------------------

def test_same_agent_id_reuses_outer_trace_id():
    with agent_context.agent_call("buddy") as outer:
        with agent_context.agent_call("buddy") as inner:
            assert inner.trace_id == outer.trace_id
        # Exiting the inner CM restores the outer as current().
        assert agent_context.current() is outer


def test_different_agent_id_nests_and_records_parent():
    with agent_context.agent_call("researcher") as outer:
        with agent_context.agent_call("browser") as inner:
            assert inner.trace_id != outer.trace_id
            assert inner.parent_agent_id == "researcher"
            assert agent_context.current() is inner
        assert agent_context.current() is outer


def test_system_call_reentrancy_mirrors_agent_call():
    with agent_context.system_call("world-forge") as outer:
        with agent_context.system_call("world-forge") as inner:
            assert inner.trace_id == outer.trace_id


def test_agent_call_nested_inside_system_call_records_no_parent_id():
    # system_call's agent_id is None by construction, so an agent nested
    # inside one has no agent parent to name -- not None-as-a-bug, None
    # because there genuinely isn't an agent parent.
    with agent_context.system_call("dictionary"):
        with agent_context.agent_call("researcher") as inner:
            assert inner.parent_agent_id is None
            assert inner.kind == "agent"


# ---------------------------------------------------------------------------
# finally-reset on a raising body
# ---------------------------------------------------------------------------

def test_context_resets_on_raise():
    with pytest.raises(ValueError):
        with agent_context.agent_call("buddy"):
            assert agent_context.current() is not None
            raise ValueError("boom")
    assert agent_context.current() is None


def test_context_resets_on_raise_does_not_leak_into_sibling():
    """A raising inner context must not leave attribution set for whatever
    runs after it in the same (outer) scope."""
    with agent_context.agent_call("researcher") as outer:
        try:
            with agent_context.agent_call("browser"):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        # Back to the outer researcher context, not leaked as browser.
        assert agent_context.current() is outer
        assert agent_context.current().agent_id == "researcher"


# ---------------------------------------------------------------------------
# Sanitisation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("agent_id", [None, ""])
def test_falsy_agent_id_is_unattributed(agent_id):
    with agent_context.agent_call(agent_id) as call:
        assert call is None
        assert agent_context.current() is None


def test_unattributed_does_not_clobber_outer_context():
    with agent_context.agent_call("buddy") as outer:
        with agent_context.agent_call(None) as inner:
            assert inner is outer  # yields current(), i.e. the outer value
            assert agent_context.current() is outer


def test_agent_id_is_lowercased():
    with agent_context.agent_call("Buddy") as call:
        assert call.agent_id == "buddy"


def test_agent_id_path_traversal_sanitised():
    with agent_context.agent_call("../../etc/passwd") as call:
        assert ".." not in call.agent_id
        assert "/" not in call.agent_id


def test_agent_id_control_characters_sanitised():
    with agent_context.agent_call("buddy\x00\x01\n\t") as call:
        assert call.agent_id is not None
        assert all(32 < ord(c) < 127 or c in "_.-" for c in call.agent_id)


def test_agent_id_whitespace_sanitised():
    with agent_context.agent_call("  buddy dream  ") as call:
        assert " " not in call.agent_id


def test_agent_id_truncated_at_64_chars():
    with agent_context.agent_call("a" * 200) as call:
        assert len(call.agent_id) == 64


def test_agent_id_coerced_via_str():
    with agent_context.agent_call(12345) as call:
        assert call.agent_id == "12345"


# ---------------------------------------------------------------------------
# billing_source() — the W2 contract, exhaustive
# ---------------------------------------------------------------------------

def test_billing_source_agent():
    with agent_context.agent_call("buddy") as call:
        assert agent_context.billing_source(call) == "agent:buddy"


def test_billing_source_system():
    with agent_context.system_call("world-forge") as call:
        assert agent_context.billing_source(call) == "sys:world-forge"


def test_billing_source_none_is_unattributed():
    assert agent_context.billing_source(None) == "unattributed"


def test_billing_source_never_invents_an_agent_id():
    # A degenerate AgentCall with kind="agent" but no agent_id must not be
    # billed as a plausible-looking id -- falls through to unattributed.
    degenerate = agent_context.AgentCall(
        agent_id=None, kind="agent", label="", trace_id="deadbeefcafef00d"
    )
    assert agent_context.billing_source(degenerate) == "unattributed"


# ---------------------------------------------------------------------------
# spawn_thread — the copy_context().run(...) shim (A3)
# ---------------------------------------------------------------------------

def test_spawn_thread_copies_context():
    seen = {}

    def _target():
        call = agent_context.current()
        seen["agent_id"] = call.agent_id if call else None

    with agent_context.agent_call("presence"):
        t = agent_context.spawn_thread(_target)
        t.start()
        t.join()

    assert seen["agent_id"] == "presence"


def test_spawn_thread_does_not_autostart():
    t = agent_context.spawn_thread(lambda: None)
    assert isinstance(t, threading.Thread)
    assert not t.is_alive()


# ---------------------------------------------------------------------------
# Subprocess round-trip helpers
# ---------------------------------------------------------------------------

def test_to_subprocess_payload_carries_current_context():
    with agent_context.agent_call("researcher", brain="deep", effort="high") as call:
        payload = agent_context.to_subprocess_payload()
    assert payload["trace_id"] == call.trace_id
    assert payload["agent_id"] == "researcher"
    assert payload["brain"] == "deep"
    assert payload["effort"] == "high"


def test_to_subprocess_payload_mints_trace_id_when_unattributed():
    payload = agent_context.to_subprocess_payload()
    assert payload["agent_id"] is None
    assert payload["trace_id"]  # non-empty


def test_from_subprocess_payload_sets_agent_context():
    payload = {"trace_id": "abc123abc123abcd", "agent_id": "researcher",
               "brain": "fast", "effort": None}
    with agent_context.from_subprocess_payload(payload) as call:
        assert call.agent_id == "researcher"
        assert call.trace_id == "abc123abc123abcd"
        assert call.out_of_process is True
    assert agent_context.current() is None


def test_from_subprocess_payload_falls_back_to_system_label():
    payload = {"trace_id": "abc123abc123abcd", "agent_id": None}
    with agent_context.from_subprocess_payload(
        payload, default_label="goal-parser"
    ) as call:
        assert call.kind == "system"
        assert call.label == "goal-parser"
        assert call.out_of_process is True
