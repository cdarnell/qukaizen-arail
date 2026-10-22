"""agent_trace v2 + the chokepoint's channel / identity / origin / cost fields.

Sprint 2026-10-20-buddy-always-on, ARCHITECTURE.md §5.10 (A1). The v1 keys keep
their names and meaning; v2 appends ``record_type``, ``event``, ``channel``,
``identity``, ``cost``, ``origin``, ``reason``, ``attrs``. A phone-origin model
call carries who asked (a salted pseudonym + role, never a raw id), over which
channel, via which writer, and what it cost (labelled simulated).
"""

from __future__ import annotations

import pytest

from arail import agent_context, agent_trace, scheduler
from arail.router.backends import BaseBackend, ModelResponse
from arail.router.core import ModelRouter

# The v1 field list, frozen here on purpose: v2 may only append to it.
V1_FIELDS = (
    "schema", "trace_id", "ts", "iso",
    "agent_id", "parent_agent_id", "kind", "attribution", "label", "call_site",
    "model", "backend", "provider", "entry_id",
    "brain", "effort", "foreground",
    "deep_reason_code", "deep_reason_detail",
    "ttft_ms", "ttft_status",
    "prefill_ms", "prefill_source",
    "tokens_in", "tokens_out", "latency_ms",
    "streamed", "out_of_process",
    "slot",
    "halted",
    "outcome", "error_class",
    "bodies", "bodies_purged",
)
V2_NEW = ("record_type", "event", "channel", "identity", "cost", "origin", "reason", "attrs")
WHO = ("p_0123456789", "talker")


class _FakeBackend(BaseBackend):
    def __init__(self, backend_name="ollama_native", model="llama-ai-eng", fail=False):
        self._name, self._model, self._fail = backend_name, model, fail

    def _resp(self):
        return ModelResponse(text="hello", model=self._model, tokens_used=7,
                             backend=self._name, latency_ms=12.5)

    def complete(self, prompt, max_tokens=512, temperature=0.7, top_p=None, *, system=None, messages=None):
        if self._fail:
            raise RuntimeError("backend exploded")
        return self._resp()

    def stream_complete(self, prompt, max_tokens=512, temperature=0.7, top_p=None, *, system=None, messages=None):
        if self._fail:
            raise RuntimeError("stream exploded")
            yield  # pragma: no cover
        yield "hel"
        yield "lo"
        yield self._resp()

    def health_check(self):
        return True


def _router(**kw) -> ModelRouter:
    return ModelRouter.from_backend(_FakeBackend(**kw), "fake")


@pytest.fixture(autouse=True)
def _clean_state():
    agent_context._reset_for_tests()
    agent_trace._reset_for_tests()
    scheduler._reset_halt_for_tests()
    yield
    agent_context._reset_for_tests()
    agent_trace._reset_for_tests()
    scheduler._reset_halt_for_tests()


# ---------------------------------------------------------------------------
# schema + fields
# ---------------------------------------------------------------------------

def test_schema_string_is_v2():
    assert agent_trace.SCHEMA == "arail.agent_trace/v2"


def test_v1_keys_keep_their_names_and_order_and_v2_only_appends():
    assert agent_trace._FIELDS[: len(V1_FIELDS)] == V1_FIELDS
    assert agent_trace._FIELDS[len(V1_FIELDS):] == V2_NEW


def test_a_record_without_v2_keys_reads_them_as_null():
    agent_trace.record(trace_id="a" * 16, kind="agent", agent_id="buddy")
    rec = agent_trace.ring(1)[0]
    assert rec["schema"] == "arail.agent_trace/v2"
    for key in V2_NEW:
        assert key in rec and rec[key] is None


def test_record_stores_v2_keys_and_still_drops_unknown_ones():
    agent_trace.record(trace_id="a" * 16, record_type="event", event="startup", channel="telegram",
                       identity={"pseudonym": WHO[0], "role": WHO[1]}, cost=None, origin="edge",
                       reason="open", attrs={"state": "open"}, made_up="nope")
    rec = agent_trace.ring(1)[0]
    assert (rec["record_type"], rec["event"], rec["channel"], rec["origin"]) == ("event", "startup", "telegram", "edge")
    assert rec["identity"] == {"pseudonym": WHO[0], "role": WHO[1]}
    assert rec["reason"] == "open" and rec["attrs"] == {"state": "open"}
    assert "made_up" not in rec


# ---------------------------------------------------------------------------
# agent_context: channel / identity / origin
# ---------------------------------------------------------------------------

def test_agent_call_carries_channel_identity_origin_and_defaults_to_none():
    with agent_context.agent_call("buddy") as plain:
        assert (plain.channel, plain.identity, plain.origin) == (None, None, None)
    with agent_context.agent_call("buddy", channel="telegram", identity=WHO, origin="edge") as call:
        assert call.channel == "telegram"
        assert call.identity == WHO
        assert call.origin == "edge"
        assert agent_context.current() is call
    assert agent_context.current() is None


def test_same_agent_inner_call_inherits_what_it_does_not_set():
    with agent_context.agent_call("buddy", channel="telegram", identity=WHO, origin="edge") as outer:
        with agent_context.agent_call("buddy") as inner:
            assert inner.trace_id == outer.trace_id
            assert (inner.channel, inner.identity, inner.origin) == ("telegram", WHO, "edge")


def test_a_different_agent_nested_inside_does_not_inherit_a_phone_identity():
    with agent_context.agent_call("buddy", channel="telegram", identity=WHO, origin="edge"):
        with agent_context.agent_call("researcher") as inner:
            assert (inner.channel, inner.identity, inner.origin) == (None, None, None)
            assert inner.parent_agent_id == "buddy"


@pytest.mark.parametrize("bad", ["p_0123456789", ("only-one",), (1, 2), ("a", "b", "c"), {"pseudonym": "x"}, 5])
def test_a_malformed_identity_becomes_none_and_never_raises(bad):
    with agent_context.agent_call("buddy", identity=bad) as call:
        assert call.identity is None


def test_channel_and_origin_are_sanitised_tokens():
    with agent_context.agent_call("buddy", channel=" Telegram\n", origin="EDGE") as call:
        assert call.channel == "telegram"
        assert call.origin == "edge"
    with agent_context.agent_call("buddy", channel="   ", origin=object()) as call:
        assert call.channel is None and call.origin is None


# ---------------------------------------------------------------------------
# the chokepoint
# ---------------------------------------------------------------------------

def test_complete_writes_a_model_call_record_with_channel_identity_origin_and_cost():
    with agent_context.agent_call("buddy", channel="telegram", identity=WHO, origin="edge", brain="reflex"):
        _router().complete("hi", max_tokens=50)
    rec = agent_trace.ring(1)[0]
    assert rec["record_type"] == "model_call"
    assert rec["event"] is None
    assert rec["channel"] == "telegram"
    assert rec["identity"] == {"pseudonym": WHO[0], "role": WHO[1]}
    assert rec["origin"] == "edge"
    assert rec["outcome"] == "ok"
    cost = rec["cost"]
    assert cost["tokens"] == rec["tokens_in"] + rec["tokens_out"]
    assert cost["label"] == "simulated"
    assert cost["cash_usd"] == 0.0 and cost["cash_basis"] == "local"
    assert isinstance(cost["simulated_usd"], float) and cost["simulated_usd"] >= 0.0


def test_stream_complete_writes_the_same_v2_fields():
    with agent_context.agent_call("buddy", channel="telegram", identity=WHO, origin="edge"):
        list(_router().stream_complete("hi"))
    rec = agent_trace.ring(1)[0]
    assert rec["record_type"] == "model_call" and rec["streamed"] is True
    assert (rec["channel"], rec["origin"]) == ("telegram", "edge")
    assert rec["identity"] == {"pseudonym": WHO[0], "role": WHO[1]}
    assert rec["cost"]["label"] == "simulated" and rec["cost"]["tokens"] > 0


def test_in_process_callers_default_to_origin_in_process_with_no_channel_or_identity():
    with agent_context.agent_call("researcher"):
        _router().complete("hi")
    rec = agent_trace.ring(1)[0]
    assert rec["record_type"] == "model_call"
    assert (rec["channel"], rec["identity"], rec["origin"]) == (None, None, "in_process")
    assert rec["cost"] is not None


def test_an_unattributed_call_still_gets_record_type_and_origin():
    _router().complete("hi")
    rec = agent_trace.ring(1)[0]
    assert rec["attribution"] == "unattributed"
    assert (rec["record_type"], rec["origin"], rec["channel"], rec["identity"]) == ("model_call", "in_process", None, None)


def test_a_billed_estimate_backend_reports_cash_as_an_estimate():
    with agent_context.agent_call("researcher"):
        _router(backend_name="claude", model="claude-sonnet-4").complete("hi")
    cost = agent_trace.ring(1)[0]["cost"]
    assert cost["cash_basis"] == "estimate"
    assert cost["cash_usd"] > 0.0
    assert cost["simulated_usd"] > 0.0
    assert cost["label"] == "simulated"


def test_refusal_records_carry_who_asked_and_no_cost():
    scheduler.halt_all_jobs()
    with agent_context.agent_call("buddy", channel="telegram", identity=WHO, origin="edge"):
        with pytest.raises(agent_context.AgentHeldError):
            _router().complete("hi")
    rec = agent_trace.ring(1)[0]
    assert rec["outcome"] == "refused_halted"
    assert (rec["record_type"], rec["channel"], rec["origin"]) == ("model_call", "telegram", "edge")
    assert rec["identity"] == {"pseudonym": WHO[0], "role": WHO[1]}
    assert rec["cost"] is None


def test_error_records_carry_who_asked_and_no_cost():
    with agent_context.agent_call("buddy", channel="telegram", identity=WHO, origin="edge"):
        with pytest.raises(RuntimeError):
            _router(fail=True).complete("hi")
    rec = agent_trace.ring(1)[0]
    assert rec["outcome"] == "error" and rec["error_class"] == "RuntimeError"
    assert rec["cost"] is None
    assert rec["identity"] == {"pseudonym": WHO[0], "role": WHO[1]}
    assert rec["record_type"] == "model_call"


def test_the_trace_never_holds_a_raw_telegram_id():
    with agent_context.agent_call("buddy", channel="telegram", identity=WHO, origin="edge"):
        _router().complete("hi")
    text = repr(agent_trace.ring(1)[0])
    assert "p_0123456789" in text and "123456789" not in text.replace("p_0123456789", "")
