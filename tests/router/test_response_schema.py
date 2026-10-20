"""A4 — structured output through the router and Ollama's native endpoint.

ARCHITECTURE.md §5.11 / §6.1: Buddy's only way to propose an action is a field in
a grammar-constrained JSON object, so `ModelRouter.complete/stream_complete` take
a keyword-only `response_schema` and `OllamaNativeBackend` sends it as Ollama's
`format`. The rule that matters is the negative one: when no schema is given,
every existing caller's request is byte-identical to what it was before.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from arail import agent_context, agent_trace
from arail.router import backends as B
from arail.router.backends import BaseBackend, ModelResponse
from arail.router.core import ModelRouter

GOLDEN = json.loads(
    (pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "ollama_native_request_bodies.json").read_text()
)["cases"]

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["kind", "propose"],
    "properties": {"kind": {"enum": ["answer", "abstain"]}, "propose": {"enum": ["none", "pause_research"]}},
}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("ARAIL_OLLAMA_KEEP_ALIVE", "2h")
    agent_context._reset_for_tests()
    agent_trace._reset_for_tests()
    yield
    agent_context._reset_for_tests()
    agent_trace._reset_for_tests()


# ---------------------------------------------------------------------------
# a recording Ollama session
# ---------------------------------------------------------------------------

class _Resp:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return {"message": {"content": "ok"}, "model": "llama-ai-eng", "eval_count": 3, "prompt_eval_duration": 1000000}

    def iter_lines(self, decode_unicode=True):
        yield json.dumps({"message": {"content": "o"}})
        yield json.dumps({"message": {"content": "k"}, "done": True, "eval_count": 2})

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Session:
    def __init__(self):
        self.calls = []

    def post(self, url, **kw):
        self.calls.append({"url": url, "json": kw["json"], "stream": kw.get("stream", False)})
        return _Resp()


def _backend(num_ctx=None):
    b = B.OllamaNativeBackend.__new__(B.OllamaNativeBackend)
    b.base_url = "http://127.0.0.1:11434/v1"
    b.api_key = "k"
    b.model_name = "llama-ai-eng"
    b._num_ctx = num_ctx
    b._session = _Session()
    return b


CALLERS = {
    "complete_plain": lambda b: b.complete("hello", 100, 0.7),
    "complete_system": lambda b: b.complete("hello", 64, 0.2, 0.9, system="SYS"),
    "complete_messages_ignored": lambda b: b.complete("hello", 64, 0.2, messages=[{"role": "user", "content": "x"}]),
    "complete_think_false": lambda b: b.complete("ping", 1, 0.0, think=False),
    "complete_no_max_tokens": lambda b: b.complete("hello", None, 0.7),
    "stream_plain": lambda b: list(b.stream_complete("hello", 100, 0.7)),
    "stream_system": lambda b: list(b.stream_complete("hello", 64, 0.2, 0.9, system="SYS")),
    "stream_think_false": lambda b: list(b.stream_complete("ping", 1, 0.0, think=False)),
}
SCHEMA_CALLERS = {
    "complete_plain": lambda b: b.complete("hello", 100, 0.7, response_schema=SCHEMA),
    "complete_system": lambda b: b.complete("hello", 64, 0.2, 0.9, system="SYS", response_schema=SCHEMA),
    "complete_messages_ignored": lambda b: b.complete("hello", 64, 0.2, messages=[{"role": "user", "content": "x"}], response_schema=SCHEMA),
    "complete_think_false": lambda b: b.complete("ping", 1, 0.0, think=False, response_schema=SCHEMA),
    "complete_no_max_tokens": lambda b: b.complete("hello", None, 0.7, response_schema=SCHEMA),
    "stream_plain": lambda b: list(b.stream_complete("hello", 100, 0.7, response_schema=SCHEMA)),
    "stream_system": lambda b: list(b.stream_complete("hello", 64, 0.2, 0.9, system="SYS", response_schema=SCHEMA)),
    "stream_think_false": lambda b: list(b.stream_complete("ping", 1, 0.0, think=False, response_schema=SCHEMA)),
}


@pytest.mark.parametrize("ctx", [None, 4096])
@pytest.mark.parametrize("name", sorted(CALLERS))
def test_without_a_schema_the_ollama_request_is_byte_identical_to_main(name, ctx):
    b = _backend(ctx)
    CALLERS[name](b)
    assert b._session.calls[0] == GOLDEN[f"{name}|num_ctx={ctx}"]
    assert "format" not in b._session.calls[0]["json"]


@pytest.mark.parametrize("ctx", [None, 4096])
@pytest.mark.parametrize("name", sorted(CALLERS))
def test_a_schema_adds_exactly_one_key_the_format(name, ctx):
    b = _backend(ctx)
    SCHEMA_CALLERS[name](b)
    expected = json.loads(json.dumps(GOLDEN[f"{name}|num_ctx={ctx}"]))
    expected["json"]["format"] = SCHEMA
    assert b._session.calls[0] == expected


def test_a_schema_is_sent_as_given_not_copied_or_wrapped():
    b = _backend()
    b.complete("hello", 10, 0.1, response_schema=SCHEMA)
    assert b._session.calls[0]["json"]["format"] is SCHEMA or b._session.calls[0]["json"]["format"] == SCHEMA


def test_response_schema_is_keyword_only_on_the_backend():
    with pytest.raises(TypeError):
        _backend().complete("hello", 10, 0.1, None, SCHEMA)  # a 5th positional arg


# ---------------------------------------------------------------------------
# the router
# ---------------------------------------------------------------------------

class _Recording(BaseBackend):
    def __init__(self):
        self.complete_kw = None
        self.stream_kw = None

    def complete(self, prompt, max_tokens=512, temperature=0.7, top_p=None, *, system=None, messages=None, **kw):
        self.complete_kw = kw
        return ModelResponse(text="x", model="m", tokens_used=1, backend="ollama_native", latency_ms=1.0)

    def stream_complete(self, prompt, max_tokens=512, temperature=0.7, top_p=None, *, system=None, messages=None, **kw):
        self.stream_kw = kw
        yield "x"
        yield ModelResponse(text="x", model="m", tokens_used=1, backend="ollama_native", latency_ms=1.0)

    def health_check(self):
        return True


class _LegacyBackend(BaseBackend):
    """The pre-A4 signature: no **kw, no response_schema. Every third-party and
    in-tree backend looks like this."""

    def __init__(self):
        self.called = 0

    def complete(self, prompt, max_tokens=512, temperature=0.7, top_p=None, *, system=None, messages=None):
        self.called += 1
        return ModelResponse(text="x", model="m", tokens_used=1, backend="fake", latency_ms=1.0)

    def stream_complete(self, prompt, max_tokens=512, temperature=0.7, top_p=None, *, system=None, messages=None):
        self.called += 1
        yield ModelResponse(text="x", model="m", tokens_used=1, backend="fake", latency_ms=1.0)

    def health_check(self):
        return True


def test_the_router_forwards_nothing_extra_when_no_schema_is_given():
    backend = _Recording()
    router = ModelRouter.from_backend(backend, "ollama_native")
    router.complete("hi")
    list(router.stream_complete("hi"))
    assert backend.complete_kw == {} and backend.stream_kw == {}


def test_the_router_forwards_the_schema_only_when_given():
    backend = _Recording()
    router = ModelRouter.from_backend(backend, "ollama_native")
    router.complete("hi", response_schema=SCHEMA)
    list(router.stream_complete("hi", response_schema=SCHEMA))
    assert backend.complete_kw == {"response_schema": SCHEMA}
    assert backend.stream_kw == {"response_schema": SCHEMA}


def test_legacy_backends_keep_working_through_the_router():
    backend = _LegacyBackend()
    router = ModelRouter.from_backend(backend, "fake")
    router.complete("hi")
    list(router.stream_complete("hi"))
    assert backend.called == 2


def test_a_schema_sent_to_a_backend_that_cannot_take_one_fails_loudly_and_is_traced_once():
    router = ModelRouter.from_backend(_LegacyBackend(), "fake")
    with agent_context.agent_call("buddy"):
        with pytest.raises(TypeError):
            router.complete("hi", response_schema=SCHEMA)
    assert agent_trace.stats()["recorded"] == 1
    assert agent_trace.ring(1)[0]["outcome"] == "error"


def test_response_schema_is_keyword_only_on_the_router():
    router = ModelRouter.from_backend(_Recording(), "ollama_native")
    with pytest.raises(TypeError):
        router.complete("hi", 10, 0.7, None, SCHEMA)  # type: ignore[misc]
