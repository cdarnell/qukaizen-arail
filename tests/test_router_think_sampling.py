"""think / sampling / trace_attrs pass-through (sprint 2026-11-18-aiml-slm-engine).

Each is handed on only when set, so every existing caller's request body and
trace record are unchanged."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from arail import agent_trace
from arail.router.backends import BaseBackend, ModelResponse, OllamaNativeBackend
from arail.router.core import ModelRouter


def _backend():
    be = OllamaNativeBackend.__new__(OllamaNativeBackend)
    be._session = MagicMock()
    be.base_url = "http://127.0.0.1:11434/v1"
    be.model_name = "m:1"
    be.api_key = "x"
    be.backend_name = "ollama:native"
    be._num_ctx = None
    return be


def _resp(content="{}", **extra):
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = {"message": {"role": "assistant", "content": content},
                           "model": "m:1", "done": True, **extra}
    return r


def _body(be):
    return be._session.post.call_args.kwargs["json"]


def test_complete_body_unchanged_without_think_or_sampling():
    be = _backend()
    be._session.post.return_value = _resp()
    be.complete("hi", 50, 0.7)
    body = _body(be)
    assert "think" not in body
    assert body["options"] == {"num_predict": 50}


def test_complete_sends_think_and_sampling_when_set():
    be = _backend()
    be._session.post.return_value = _resp(done_reason="length", prompt_eval_count=9)
    out = be.complete("hi", 50, 0.7, think=False,
                      sampling={"temperature": 0, "seed": 7, "bogus": 1})
    body = _body(be)
    assert body["think"] is False
    assert body["options"] == {"num_predict": 50, "temperature": 0, "seed": 7}
    assert (out.done_reason, out.prompt_tokens) == ("length", 9)


def test_stream_sends_sampling_and_reports_done_reason():
    be = _backend()
    lines = [json.dumps({"message": {"content": "a"}, "done": False}),
             json.dumps({"message": {"content": ""}, "done": True,
                         "done_reason": "stop", "eval_count": 3, "prompt_eval_count": 11})]
    cm = MagicMock()
    cm.__enter__.return_value.iter_lines.return_value = lines
    be._session.post.return_value = cm
    items = list(be.stream_complete("hi", 50, 0.7, think=False, sampling={"seed": 1}))
    body = _body(be)
    assert body["think"] is False and body["options"]["seed"] == 1
    assert isinstance(items[-1], ModelResponse)
    assert (items[-1].done_reason, items[-1].prompt_tokens) == ("stop", 11)


class _Spy(BaseBackend):
    def __init__(self):
        self.kw = None

    def complete(self, prompt, max_tokens=512, temperature=0.7, top_p=None, **kw):
        self.kw = kw
        return ModelResponse(text="t", model="m", tokens_used=1, backend="ollama_native", latency_ms=1.0)

    def stream_complete(self, prompt, max_tokens=512, temperature=0.7, top_p=None, **kw):
        self.kw = kw
        yield "t"
        yield ModelResponse(text="t", model="m", tokens_used=1, backend="ollama_native", latency_ms=1.0)

    def health_check(self):
        return True


def test_router_hands_on_only_what_is_set(monkeypatch):
    spy = _Spy()
    r = ModelRouter.from_backend(spy, "ollama_native")
    def extra():
        return {k: v for k, v in spy.kw.items() if k not in ("system", "messages")}
    r.complete("p", 10)
    assert extra() == {}
    list(r.stream_complete("p", 10))
    assert extra() == {}
    list(r.stream_complete("p", 10, think=False, sampling={"seed": 1}, trace_attrs={"a": 1}))
    assert extra() == {"think": False, "sampling": {"seed": 1}}


def test_trace_attrs_land_in_the_one_model_call_record():
    r = ModelRouter.from_backend(_Spy(), "ollama_native")
    before = len(agent_trace.ring(500))
    list(r.stream_complete("p", 10, trace_attrs={"served": {"tag": "m:1"}, "think": False}))
    r.complete("p", 10, trace_attrs={"think": None, "served": {"tag": "m:1"}})
    recs = [x for x in agent_trace.ring(500)][before:] if before < 500 else agent_trace.ring(500)
    withattrs = [x for x in recs if x.get("attrs") and x["attrs"].get("served")]
    assert len(withattrs) == 2
    assert all(x["record_type"] == "model_call" for x in withattrs)
    plain = ModelRouter.from_backend(_Spy(), "ollama_native")
    plain.complete("p", 10)
    assert agent_trace.ring(1)[-1].get("attrs") is None
