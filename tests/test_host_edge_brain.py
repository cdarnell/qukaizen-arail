"""A8 - brain.chat beyond the shared conformance cases.

ARCHITECTURE.md §5.6, §7 F6, F12, F21, F29-F31, F45. The conformance cases pin the
refusal order and the flattening; these cover what only a running edge can show: that
the stream is what the OpenAI SDK (nanobot's client) expects, that attribution survives
the worker thread, that the inference slot and the budget are released and counted
exactly once, and that the prompt never ends up in a log.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time

import httpx
import openai
import pytest
from jsonschema import Draft202012Validator

from arail import agent_trace, scheduler
from arail.buddy_host import CONTRACT, CONTRACT_SHA256, brain, budget
from arail.portal import host_edge_routes
from arail.portal import scheduler as inference
from arail.router.backends import BaseBackend, ModelResponse
from arail.router.core import ModelRouter
from tests.buddy_conformance import FakeOllama, World

CRED = CONTRACT["conformance"]["fake_credential"]
TID = "0123456789abcdef"
WHO = {"pseudonym": "p_0123456789", "role": "talker"}
BASE = CONTRACT["transport"]["base_path"]
REPLY = CONTRACT["conformance"]["fake_reply"]


def header(**kw):
    h = {"trace_id": TID, "channel": "telegram", "identity": WHO, "response_schema": None}
    h.update(kw)
    return "BUDDY-TURN/1 " + json.dumps(h, separators=(",", ":"))


def chat(question="If a test also fails on main, can I call it pre-existing?", stream=True, **kw):
    body = {"model": "buddy-fast", "stream": stream, "stream_options": {"include_usage": True}, "max_tokens": 400,
            "messages": [{"role": "system", "content": header() + "\nYou are Buddy."},
                         {"role": "user", "content": question}]}
    body.update(kw)
    return body


def H():
    return {"Authorization": "Bearer " + CRED, "X-Buddy-Contract": "sha256:" + CONTRACT_SHA256,
            "Content-Type": "application/json"}


@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.setattr(inference, "_SEM", None)  # a fresh semaphore on this test's loop
    return World({}, "brain.chat", tmp_path, monkeypatch)


def post(lab, body):
    return lab.client.post(BASE + "/brain/chat/completions", content=json.dumps(body), headers=H())


def sdk(lab, **client_kw):
    """The OpenAI SDK, the client nanobot uses, pointed at the real app over ASGI."""
    transport = httpx.ASGITransport(app=lab.client.app, client=("127.0.0.1", 9))
    http = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    return openai.AsyncOpenAI(api_key=CRED, base_url="http://testserver" + BASE + "/brain", http_client=http,
                              default_headers={"X-Buddy-Contract": "sha256:" + CONTRACT_SHA256}, max_retries=0,
                              **client_kw)


# ---------------------------------------------------------------------------
# what nanobot's client sees
# ---------------------------------------------------------------------------

def test_the_openai_sdk_streams_the_reply_and_reads_usage(lab):
    async def go():
        client = sdk(lab)
        stream = await client.chat.completions.create(
            model="buddy-fast", stream=True, stream_options={"include_usage": True}, max_tokens=400,
            messages=[{"role": "system", "content": header() + "\nYou are Buddy."},
                      {"role": "user", "content": "q"}])
        chunks = [c async for c in stream]
        return chunks

    chunks = asyncio.run(go())
    text = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
    assert text == REPLY
    assert chunks[0].choices[0].delta.role == "assistant"
    assert [c.choices[0].finish_reason for c in chunks if c.choices][-1] == "stop"
    usage = [c.usage for c in chunks if c.usage]
    assert usage and usage[-1].completion_tokens == 12 and usage[-1].total_tokens == usage[-1].prompt_tokens + 12
    assert all(c.model == "buddy-fast" for c in chunks)


def test_the_openai_sdk_reads_a_non_stream_completion(lab):
    async def go():
        return await sdk(lab).chat.completions.create(
            model="buddy-fast", stream=False, max_tokens=400,
            messages=[{"role": "system", "content": header() + "\nYou are Buddy."}, {"role": "user", "content": "q"}])

    out = asyncio.run(go())
    assert out.choices[0].message.content == REPLY and out.choices[0].finish_reason == "stop"
    raw = post(lab, chat(stream=False)).json()   # the SDK model adds Nones; the contract is about the wire
    Draft202012Validator(CONTRACT["methods"]["brain.chat"]["response"]["non_stream"]).validate(raw)


def test_the_openai_sdk_sees_a_refusal_as_an_api_error_that_must_not_be_retried(tmp_path, monkeypatch):
    w = World({"held": True}, "brain.chat", tmp_path, monkeypatch)

    async def go():
        try:
            await sdk(w).chat.completions.create(
                model="buddy-fast", stream=True,
                messages=[{"role": "system", "content": header() + "\np"}, {"role": "user", "content": "q"}])
        except openai.APIStatusError as exc:
            return exc

    exc = asyncio.run(go())
    assert exc.status_code == 403 and exc.response.headers["x-should-retry"] == "false"
    assert "held" in str(exc.body) and exc.body["code"] == "held"


def test_the_models_endpoint_names_one_model(lab):
    r = lab.client.get(BASE + "/brain/models", headers=H())
    assert r.status_code == 200 and [m["id"] for m in r.json()["data"]] == ["buddy-fast"]


# ---------------------------------------------------------------------------
# attribution, trace, budget
# ---------------------------------------------------------------------------

def test_the_model_call_record_carries_who_asked_through_the_worker_thread(lab):
    """F45: a contextvar lost in the worker thread would leave channel and identity null."""
    assert post(lab, chat()).status_code == 200
    rec = next(r for r in agent_trace.ring(50) if r["record_type"] == "model_call")
    assert (rec["channel"], rec["origin"], rec["agent_id"], rec["brain"], rec["foreground"]) == (
        "telegram", "edge", "buddy", "reflex", True)
    assert rec["identity"] == WHO and rec["trace_id"] == TID
    assert rec["outcome"] == "ok" and rec["streamed"] is True
    assert rec["ttft_status"] == "measured" and isinstance(rec["ttft_ms"], float)
    assert rec["cost"]["label"] == "simulated" and rec["cost"]["cash_usd"] == 0.0
    assert rec["bodies"] is None  # flight recorder off: no prompt, no answer


def test_the_header_trace_id_joins_the_model_call_to_buddys_own_events(lab):
    lab.client.post(BASE + "/trace/record", headers=H(), content=json.dumps({"v": 1, "records": [
        {"event": "inbound_accepted", "trace_id": TID, "client_ts": "2026-10-20T14:00:00Z", "channel": "telegram",
         "identity": WHO, "outcome": None, "reason": None, "attrs": {"input_chars": 5}}]}))
    post(lab, chat())
    rec, related = agent_trace.find_with_related(TID)
    kinds = [rec.get("record_type")] + [r.get("record_type") for r in related]
    assert kinds.count("model_call") == 1 and kinds.count("event") == 1


def test_a_turn_counts_once_tokens_are_added_and_refusals_cost_nothing(tmp_path, monkeypatch):
    w = World({}, "brain.chat", tmp_path, monkeypatch)
    snap0 = budget.default_budget().snapshot()
    assert post(w, chat()).status_code == 200
    snap1 = budget.default_budget().snapshot()
    assert snap1["turns_used"] == snap0["turns_used"] + 1
    assert snap1["tokens_used"] >= 12 + 1
    scheduler.halt_all_jobs()
    assert post(w, chat()).status_code == 403
    assert budget.default_budget().snapshot() == snap1


def test_a_busy_slot_costs_no_budget(tmp_path, monkeypatch):
    w = World({"slot": "busy"}, "brain.chat", tmp_path, monkeypatch)
    assert post(w, chat()).status_code == 424
    assert budget.default_budget().snapshot()["turns_used"] == 0


def test_throttle_counts_admitted_calls_only(lab):
    for _ in range(20):
        assert post(lab, chat()).status_code == 200
    r = post(lab, chat())
    assert r.status_code == 403 and r.json()["error"]["code"] == "identity_throttled"
    other = chat()
    other["messages"][0]["content"] = header(identity={"pseudonym": "p_abcdef0123", "role": "talker"}) + "\np"
    assert post(lab, other).status_code == 200


def test_the_question_never_appears_in_a_log_or_the_trace(lab, caplog):
    canary = "CANARY-4c7e91-private-question"
    caplog.set_level(logging.DEBUG)
    assert post(lab, chat(question=canary)).status_code == 200
    assert post(lab, chat(question=canary, stream=False)).status_code == 200
    assert canary not in caplog.text
    assert canary not in json.dumps(agent_trace.ring(500), default=str)


# ---------------------------------------------------------------------------
# the inference slot
# ---------------------------------------------------------------------------

class _Gate(BaseBackend):
    """A backend that blocks until released, so a second request meets a held slot."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def complete(self, *a, **kw):  # pragma: no cover
        raise NotImplementedError

    def stream_complete(self, prompt, max_tokens=512, temperature=0.7, top_p=None, *, system=None, messages=None,
                        response_schema=None, system_as_message=False):
        self.entered.set()
        assert self.release.wait(10)
        yield REPLY
        yield ModelResponse(text=REPLY, model="llama-ai-eng", tokens_used=3, backend="ollama_native", latency_ms=1.0)

    def health_check(self):
        return True


def test_a_second_question_hears_busy_after_the_wait_and_the_first_still_finishes(tmp_path, monkeypatch):
    w = World({}, "brain.chat", tmp_path, monkeypatch)
    monkeypatch.setattr(inference, "_SEM", None)
    monkeypatch.setattr(inference, "_INFLIGHT", 0)
    monkeypatch.setattr(host_edge_routes, "SLOT_WAIT_S", 0.3)
    gate = _Gate()
    monkeypatch.setattr(brain, "build_router", lambda: ModelRouter.from_backend(gate, "ollama_native"))

    async def go():
        transport = httpx.ASGITransport(app=w.client.app, client=("127.0.0.1", 9))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            first = asyncio.create_task(c.post(BASE + "/brain/chat/completions", content=json.dumps(chat()), headers=H()))
            await asyncio.get_running_loop().run_in_executor(None, gate.entered.wait, 5)
            t0 = time.perf_counter()
            second = await c.post(BASE + "/brain/chat/completions", content=json.dumps(chat()), headers=H())
            waited = time.perf_counter() - t0
            gate.release.set()
            return waited, second, await first

    waited, second, first = asyncio.run(go())
    assert 0.25 <= waited < 3.0
    assert second.status_code == 424 and second.json()["error"]["code"] == "brain_busy"
    assert second.headers["x-should-retry"] == "false"
    assert first.status_code == 200
    assert inference.snapshot()["in_flight"] == 0
    refused = [r for r in agent_trace.ring(100) if r.get("outcome") == "refused_brain_busy"]
    assert len(refused) == 1 and refused[0]["identity"] == WHO


class _Boom(BaseBackend):
    def __init__(self, after_first=False):
        self.after_first = after_first

    def complete(self, *a, **kw):  # pragma: no cover
        raise NotImplementedError

    def stream_complete(self, prompt, max_tokens=512, temperature=0.7, top_p=None, *, system=None, messages=None,
                        response_schema=None, system_as_message=False):
        if self.after_first:
            yield "{"
        raise RuntimeError("backend exploded: PRIVATE-PROMPT-TEXT")

    def health_check(self):
        return True


def test_a_failure_before_the_first_token_is_a_424_and_frees_the_slot(tmp_path, monkeypatch):
    w = World({}, "brain.chat", tmp_path, monkeypatch)
    monkeypatch.setattr(brain, "build_router", lambda: ModelRouter.from_backend(_Boom(), "ollama_native"))
    r = post(w, chat())
    assert r.status_code == 424 and r.json()["error"]["code"] == "brain_unavailable"
    assert "PRIVATE-PROMPT-TEXT" not in r.text
    assert inference.snapshot()["in_flight"] == 0
    errs = [x for x in agent_trace.ring(50) if x.get("record_type") == "model_call"]
    assert errs[-1]["outcome"] == "error" and errs[-1]["error_class"] == "RuntimeError" and errs[-1]["cost"] is None
    monkeypatch.setattr(brain, "build_router", lambda: ModelRouter.from_backend(FakeOllama(REPLY), "ollama_native"))
    assert post(w, chat()).status_code == 200  # the slot was released


def test_a_failure_after_the_first_token_ends_with_an_error_chunk_then_done(tmp_path, monkeypatch):
    w = World({}, "brain.chat", tmp_path, monkeypatch)
    monkeypatch.setattr(brain, "build_router", lambda: ModelRouter.from_backend(_Boom(after_first=True), "ollama_native"))
    r = post(w, chat())
    assert r.status_code == 200
    frames = [f for f in r.text.split("\n\n") if f.strip()]
    assert frames[-1] == "data: [DONE]"
    err = json.loads(frames[-2][6:])
    assert err == {"error": {"code": "brain_failed", "message": CONTRACT["errors"]["stream_errors"]["brain_failed"]}}
    assert "PRIVATE-PROMPT-TEXT" not in r.text
    assert inference.snapshot()["in_flight"] == 0


def test_a_hold_that_lands_between_the_edge_check_and_the_chokepoint_is_refused(tmp_path, monkeypatch):
    w = World({}, "brain.chat", tmp_path, monkeypatch)
    real = host_edge_routes._require_not_held

    def late_hold():
        real()
        scheduler.halt_all_jobs()  # the desk flips the switch right after the edge looked

    monkeypatch.setattr(host_edge_routes, "_require_not_held", late_hold)
    r = post(w, chat())
    assert r.status_code == 403 and r.json()["error"]["code"] == "held"
    assert any(x.get("outcome") == "refused_halted" for x in agent_trace.ring(50))
    assert w.backend.calls == []


def test_no_tools_ever_reach_the_router(lab):
    assert post(lab, chat(tools=[])).status_code == 200
    assert lab.backend.calls[-1].keys() == {"system", "prompt", "max_tokens", "response_schema", "system_as_message"}


def test_a_response_schema_reaches_the_backend_as_the_format(lab):
    schema = {"type": "object", "properties": {"propose": {"enum": ["none", "pause_research"]}}}
    body = chat()
    body["messages"][0]["content"] = header(response_schema=schema) + "\nYou are Buddy."
    assert post(lab, body).status_code == 200
    assert lab.backend.calls[-1]["response_schema"] == schema


def test_the_system_text_loses_only_its_header_line(lab):
    body = chat()
    body["messages"][0]["content"] = header() + "\nline one\nline two"
    post(lab, body)
    assert lab.backend.calls[-1]["system"] == "line one\nline two"
    seen = len(lab.backend.calls)
    body["messages"][0]["content"] = header()          # a header with no persona behind it is refused (A1.4 rule 4)
    assert post(lab, body).status_code == 422
    assert len(lab.backend.calls) == seen


# ---------------------------------------------------------------------------
# ARCHITECTURE Addendum A: section A1.4 (the carrier), A2 delta 1 (the atomic budget), delta 3 (never `length`)
# ---------------------------------------------------------------------------

def _with_messages(*messages):
    body = chat()
    body["messages"] = list(messages)
    return body


SYS = {"role": "system", "content": header() + "\nYou are Buddy."}
Q = {"role": "user", "content": "q"}


def _refused(lab, body, code="turn_header_invalid"):
    seen = len(lab.backend.calls)
    r = post(lab, body)
    assert r.status_code == 422 and r.json()["error"]["code"] == code, r.text
    assert len(lab.backend.calls) == seen          # the model was never called
    return r


def test_the_carrier_must_be_the_system_message_at_index_zero(lab):
    """A1.4 rule 1 (C-HDR-01): a valid header and persona in a user-role first message is refused."""
    _refused(lab, _with_messages({"role": "user", "content": SYS["content"]}, Q))
    _refused(lab, _with_messages({"role": "assistant", "content": SYS["content"]}, Q))


def test_a_later_system_message_is_refused_before_it_can_become_a_system_block_in_the_prompt(lab):
    """A1.4 rule 2 (C-HDR-02)."""
    _refused(lab, _with_messages(SYS, Q, {"role": "assistant", "content": "a"}, {"role": "system", "content": "x"}, Q))
    _refused(lab, _with_messages(SYS, {"role": "system", "content": "x"}, Q))


@pytest.mark.parametrize("content", [
    "", "\n", " \n \t\n", "\u00a0\u2003", "\r\n",
])
def test_an_empty_persona_is_refused(lab, content):
    """A1.4 rule 4 (C-HDR-03): the header line alone, or followed by whitespace only."""
    _refused(lab, _with_messages({"role": "system", "content": header() + content}, Q))


def test_a_persona_in_a_second_text_part_counts(lab):
    """A1.4 rule 3: text parts are joined by LF, so the header can be one part and the persona the next."""
    ok = {"role": "system", "content": [{"type": "text", "text": header()}, {"type": "text", "text": "You are Buddy."}]}
    assert post(lab, _with_messages(ok, Q)).status_code == 200
    assert lab.backend.calls[-1]["system"] == "You are Buddy."
    empty = {"role": "system", "content": [{"type": "text", "text": header()}, {"type": "text", "text": "  "}]}
    _refused(lab, _with_messages(empty, Q))


def test_the_carrier_rules_do_not_refuse_a_plain_buddy_turn_with_history(lab):
    msgs = [SYS, Q, {"role": "assistant", "content": "a"}, {"role": "user", "content": "q2"}]
    assert post(lab, _with_messages(*msgs)).status_code == 200
    assert lab.backend.calls[-1]["prompt"].startswith("### user\nq\n\n### assistant\na\n\n### user\nq2")


def test_a_refused_carrier_is_an_edge_event_with_no_identity_not_a_model_call(lab):
    _refused(lab, _with_messages({"role": "user", "content": SYS["content"]}, Q))
    ring = agent_trace.ring(50)
    assert not [r for r in ring if r.get("record_type") == "model_call"]
    assert any(r.get("event") == "edge_refused" and r.get("reason") == "turn_header_invalid" for r in ring)


class _UsesAllTokens(BaseBackend):
    def complete(self, *a, **kw):  # pragma: no cover
        raise NotImplementedError

    def stream_complete(self, prompt, max_tokens=512, temperature=0.7, top_p=None, *, system=None, messages=None,
                        response_schema=None, system_as_message=False):
        yield REPLY
        yield ModelResponse(text=REPLY, model="llama-ai-eng", tokens_used=max_tokens, backend="ollama_native",
                            latency_ms=1.0)

    def health_check(self):
        return True


def test_i_edge_13_a_token_capped_answer_ends_with_stop_so_nanobot_does_not_call_again(tmp_path, monkeypatch):
    """F55: one answer that used all of max_tokens is one finish_reason `stop`, on both paths and through the SDK."""
    w = World({}, "brain.chat", tmp_path, monkeypatch)
    monkeypatch.setattr(brain, "build_router", lambda: ModelRouter.from_backend(_UsesAllTokens(), "ollama_native"))

    async def go():
        stream = await sdk(w).chat.completions.create(
            model="buddy-fast", stream=True, stream_options={"include_usage": True}, max_tokens=400,
            messages=[SYS, Q])
        return [c async for c in stream]

    chunks = asyncio.run(go())
    assert [c.choices[0].finish_reason for c in chunks if c.choices][-1] == "stop"
    assert not [c for c in chunks if c.choices and c.choices[0].finish_reason == "length"]
    assert [c.usage.completion_tokens for c in chunks if c.usage] == [400]
    plain = post(w, chat(stream=False)).json()
    assert plain["choices"][0]["finish_reason"] == "stop" and plain["usage"]["completion_tokens"] == 400
    recs = [r for r in agent_trace.ring(100) if r.get("record_type") == "model_call"]
    assert len(recs) == 2 and all(r["tokens_out"] == 400 for r in recs)       # truncation stays visible here
    assert snapshot_in_flight() == 0


def snapshot_in_flight():
    return inference.snapshot()["in_flight"]


def _flag_second_check(monkeypatch, seen):
    """Set ``seen`` once two brain.chat calls have passed step 9 (both look fine; the slot is what separates them)."""
    real = budget.Budget.check
    count = [0]
    lock = threading.Lock()

    def check(self, pseudonym):
        real(self, pseudonym)
        with lock:
            count[0] += 1
            if count[0] >= 2:
                seen.set()

    monkeypatch.setattr(budget.Budget, "check", check)


def test_i_edge_14_two_calls_at_299_turns_give_one_answer_and_one_budget_exhausted(tmp_path, monkeypatch):
    """Both calls pass step 9 (299 < 300) and queue on the slot; the claim made with the slot in hand lets one in."""
    w = World({"budget_used": {"turns": 299, "tokens": 0}}, "brain.chat", tmp_path, monkeypatch)
    monkeypatch.setattr(inference, "_SEM", None)
    monkeypatch.setattr(inference, "_INFLIGHT", 0)
    both_checked = threading.Event()
    _flag_second_check(monkeypatch, both_checked)
    other = chat()
    other["messages"][0]["content"] = header(identity={"pseudonym": "p_abcdef0123", "role": "talker"},
                                             trace_id="fedcba9876543210") + "\np"

    async def go():
        transport = httpx.ASGITransport(app=w.client.app, client=("127.0.0.1", 9))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            async with inference.inference_slot("test-holder"):         # the slot is busy while both calls arrive
                a = asyncio.create_task(c.post(BASE + "/brain/chat/completions", content=json.dumps(chat()), headers=H()))
                b = asyncio.create_task(c.post(BASE + "/brain/chat/completions", content=json.dumps(other), headers=H()))
                assert await asyncio.get_running_loop().run_in_executor(None, both_checked.wait, 5)
            return await a, await b

    a, b = asyncio.run(go())
    codes = sorted((r.status_code, r.json()["error"]["code"] if r.status_code >= 400 else "ok") for r in (a, b))
    assert codes == [(200, "ok"), (403, "budget_exhausted")]
    loser = a if a.status_code == 403 else b
    assert loser.headers["x-should-retry"] == "false"
    assert budget.default_budget().snapshot()["turns_used"] == 300
    refused = [r for r in agent_trace.ring(100) if r.get("outcome") == "refused_budget_exhausted"]
    assert len(refused) == 1 and refused[0]["identity"] is not None
    assert len(w.backend.calls) == 1
    assert snapshot_in_flight() == 0


def test_lab_mode_is_read_again_after_the_wait_for_the_slot(tmp_path, monkeypatch):
    """A8 delta 1: the slot wait can be 8 s, so a flip to airgapped during it must stop the call."""
    w = World({}, "brain.chat", tmp_path, monkeypatch)
    monkeypatch.setattr(inference, "_SEM", None)
    monkeypatch.setattr(inference, "_INFLIGHT", 0)
    gate = _Gate()
    monkeypatch.setattr(brain, "build_router", lambda: ModelRouter.from_backend(gate, "ollama_native"))
    both_checked = threading.Event()
    _flag_second_check(monkeypatch, both_checked)
    other = chat()
    other["messages"][0]["content"] = header(identity={"pseudonym": "p_abcdef0123", "role": "talker"}) + "\np"

    async def go():
        transport = httpx.ASGITransport(app=w.client.app, client=("127.0.0.1", 9))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            first = asyncio.create_task(c.post(BASE + "/brain/chat/completions", content=json.dumps(chat()), headers=H()))
            await asyncio.get_running_loop().run_in_executor(None, gate.entered.wait, 5)
            second = asyncio.create_task(c.post(BASE + "/brain/chat/completions", content=json.dumps(other), headers=H()))
            assert await asyncio.get_running_loop().run_in_executor(None, both_checked.wait, 5)
            monkeypatch.setenv("LAB_MODE", "airgapped")
            gate.release.set()
            return await first, await second

    first, second = asyncio.run(go())
    assert first.status_code == 200
    assert second.status_code == 403 and second.json()["error"]["code"] == "mode_airgapped"
    assert budget.default_budget().snapshot()["turns_used"] == 1          # the refused call counted nothing
    assert len([r for r in agent_trace.ring(100) if r.get("outcome") == "refused_mode_airgapped"]) == 1


# ---------------------------------------------------------------------------
# R1-4c: the persona the model reads is Buddy's, not the Modelfile's
# ---------------------------------------------------------------------------
# REVIEW finding 4(c): the edge flattened Buddy's persona into `system`, and OllamaNativeBackend prepended it to the prompt and sent ONE user
# message. With no system message in the request, Ollama uses the Modelfile's SYSTEM; for llama-ai-eng that is "You are an AI engineering
# assistant ... You reason step by step ...". So every answer was Buddy's rules arriving as user text underneath a different persona.

PERSONA = "You are Buddy, the QA expert of the qa-practice World.\nAnswer from the cards or say you cannot."


class _OllamaSession:
    """A recording stand-in for the HTTP session of the real OllamaNativeBackend (streams one short answer)."""

    def __init__(self):
        self.bodies = []

    def post(self, url, **kw):
        self.bodies.append(kw["json"])
        session = self

        class _Resp:
            status_code = 200

            def raise_for_status(self):
                pass

            def iter_lines(self, decode_unicode=True):
                yield json.dumps({"message": {"content": REPLY}})
                yield json.dumps({"message": {"content": ""}, "done": True, "eval_count": 4, "prompt_eval_duration": 1000})

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _Resp()


def _real_backend_router(session):
    from arail.router.backends import OllamaNativeBackend
    b = OllamaNativeBackend.__new__(OllamaNativeBackend)
    b.base_url, b.api_key, b.model_name, b._num_ctx, b._session = "http://127.0.0.1:11434/v1", "k", "llama-ai-eng", None, session
    return ModelRouter.from_backend(b, "ollama_native")


def test_the_edge_asks_the_backend_for_a_real_system_message(lab):
    body = chat()
    body["messages"][0]["content"] = header() + "\n" + PERSONA
    assert post(lab, body).status_code == 200
    call = lab.backend.calls[-1]
    assert call["system_as_message"] is True and call["system"] == PERSONA


def test_the_request_ollama_receives_has_buddys_persona_as_its_system_message_and_nothing_else_does(tmp_path, monkeypatch):
    w = World({}, "brain.chat", tmp_path, monkeypatch)
    session = _OllamaSession()
    monkeypatch.setattr(brain, "build_router", lambda: _real_backend_router(session))
    body = chat(question="Explain the pre-existing failure rule.")
    body["messages"][0]["content"] = header() + "\n" + PERSONA
    body["messages"][1:1] = [{"role": "user", "content": "earlier question"}, {"role": "assistant", "content": "earlier answer"}]
    assert post(w, body).status_code == 200
    (sent,) = session.bodies
    system = [m for m in sent["messages"] if m["role"] == "system"]
    assert sent["messages"][0] == {"role": "system", "content": PERSONA} and len(system) == 1
    assert [m["role"] for m in sent["messages"]] == ["system", "user"]
    user = sent["messages"][1]["content"]
    assert PERSONA not in user and "BUDDY-TURN" not in user
    assert user == "### user\nearlier question\n\n### assistant\nearlier answer\n\n### user\nExplain the pre-existing failure rule.\n\n"
    assert sent["model"] == "llama-ai-eng" and sent["stream"] is True and sent["options"]["num_predict"] == 400


def test_a_schema_still_reaches_ollama_as_the_format_beside_the_system_message(tmp_path, monkeypatch):
    w = World({}, "brain.chat", tmp_path, monkeypatch)
    session = _OllamaSession()
    monkeypatch.setattr(brain, "build_router", lambda: _real_backend_router(session))
    schema = {"type": "object", "properties": {"propose": {"enum": ["none", "pause_research"]}}}
    body = chat()
    body["messages"][0]["content"] = header(response_schema=schema) + "\n" + PERSONA
    assert post(w, body).status_code == 200
    assert session.bodies[0]["format"] == schema and session.bodies[0]["messages"][0]["role"] == "system"

