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
                        response_schema=None):
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
                        response_schema=None):
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
    assert lab.backend.calls[-1].keys() == {"system", "prompt", "max_tokens", "response_schema"}


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
    body["messages"][0]["content"] = header()
    post(lab, body)
    assert lab.backend.calls[-1]["system"] == ""
