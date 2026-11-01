"""F6 — the TTFT truth table, one test per row. TTFT must never be derived
from latency_ms, and every non-"measured" status must carry ttft_ms=None.
Also covers prefill_ms's separate provenance field (never rendered as TTFT).
"""

from __future__ import annotations

import time

import pytest

from arail import agent_context, agent_trace
from arail.router.backends import BaseBackend, ModelResponse
from arail.router.core import ModelRouter


@pytest.fixture(autouse=True)
def _clean_state():
    agent_context._reset_for_tests()
    agent_trace._reset_for_tests()
    yield
    agent_context._reset_for_tests()
    agent_trace._reset_for_tests()


def _router(backend) -> ModelRouter:
    return ModelRouter.from_backend(backend, "fake")


# ---------------------------------------------------------------------------
# Row: complete() — always non_streaming, ttft_ms always None
# ---------------------------------------------------------------------------

class _CompleteOnlyBackend(BaseBackend):
    def complete(self, *a, **kw):
        time.sleep(0.005)
        return ModelResponse(text="hi", model="m", tokens_used=2,
                             backend="fake", latency_ms=5.0)

    def stream_complete(self, *a, **kw):
        yield self.complete(*a, **kw)

    def health_check(self):
        return True


def test_complete_is_always_non_streaming():
    router = _router(_CompleteOnlyBackend())
    router.complete("hi")
    rec = agent_trace.ring(1)[0]
    assert rec["ttft_ms"] is None
    assert rec["ttft_status"] == "non_streaming"
    assert rec["ttft_ms"] != rec["latency_ms"]


# ---------------------------------------------------------------------------
# Row: stream_complete, first item is a non-empty str -> measured
# ---------------------------------------------------------------------------

class _RealStreamBackend(BaseBackend):
    def complete(self, *a, **kw):
        raise NotImplementedError

    def stream_complete(self, *a, **kw):
        time.sleep(0.01)
        yield "hel"
        yield "lo"
        yield ModelResponse(text="hello", model="m", tokens_used=2,
                            backend="fake", latency_ms=20.0)

    def health_check(self):
        return True


def test_stream_first_nonempty_str_is_measured():
    router = _router(_RealStreamBackend())
    list(router.stream_complete("hi"))
    rec = agent_trace.ring(1)[0]
    assert rec["ttft_status"] == "measured"
    assert rec["ttft_ms"] is not None
    assert rec["ttft_ms"] > 0
    assert rec["ttft_ms"] != rec["latency_ms"]


def test_stream_ttft_measured_before_generator_entered_not_after():
    """TTFT must reflect time-to-first-token, not time-to-loop-start —
    a slow first delta should show up in the measured ttft_ms."""
    router = _router(_RealStreamBackend())  # sleeps 10ms before first yield
    list(router.stream_complete("hi"))
    rec = agent_trace.ring(1)[0]
    assert rec["ttft_ms"] >= 8.0  # generous floor under the 10ms sleep


# ---------------------------------------------------------------------------
# Row: stream_complete, first item is a ModelResponse -> emulated_stream
# ---------------------------------------------------------------------------

class _EmulatedStreamBackend(BaseBackend):
    def complete(self, *a, **kw):
        return ModelResponse(text="hello", model="m", tokens_used=2,
                             backend="fake", latency_ms=15.0)

    def stream_complete(self, *a, **kw):
        yield self.complete(*a, **kw)  # BaseBackend's default shape

    def health_check(self):
        return True


def test_stream_first_item_response_is_emulated():
    router = _router(_EmulatedStreamBackend())
    list(router.stream_complete("hi"))
    rec = agent_trace.ring(1)[0]
    assert rec["ttft_status"] == "emulated_stream"
    assert rec["ttft_ms"] is None


# ---------------------------------------------------------------------------
# Row: stream_complete yields only empty strings then a response -> no_tokens
# ---------------------------------------------------------------------------

class _EmptyStringsBackend(BaseBackend):
    def complete(self, *a, **kw):
        raise NotImplementedError

    def stream_complete(self, *a, **kw):
        yield ""
        yield ""
        yield ModelResponse(text="", model="m", tokens_used=0,
                            backend="fake", latency_ms=8.0)

    def health_check(self):
        return True


def test_stream_only_empty_strings_is_no_tokens():
    router = _router(_EmptyStringsBackend())
    list(router.stream_complete("hi"))
    rec = agent_trace.ring(1)[0]
    assert rec["ttft_status"] == "no_tokens"
    assert rec["ttft_ms"] is None


# ---------------------------------------------------------------------------
# Row: stream_complete raises before any item -> error
# ---------------------------------------------------------------------------

class _RaisesImmediatelyBackend(BaseBackend):
    def complete(self, *a, **kw):
        raise NotImplementedError

    def stream_complete(self, *a, **kw):
        raise RuntimeError("boom")
        yield  # pragma: no cover - unreachable, keeps this a generator

    def health_check(self):
        return True


def test_stream_raises_before_any_item_is_error():
    router = _router(_RaisesImmediatelyBackend())
    with pytest.raises(RuntimeError):
        list(router.stream_complete("hi"))
    rec = agent_trace.ring(1)[0]
    assert rec["ttft_status"] == "error"
    assert rec["ttft_ms"] is None
    assert rec["outcome"] == "error"


class _RaisesAfterTokensBackend(BaseBackend):
    def complete(self, *a, **kw):
        raise NotImplementedError

    def stream_complete(self, *a, **kw):
        yield "partial"
        raise RuntimeError("boom mid-stream")

    def health_check(self):
        return True


def test_stream_raises_after_real_token_keeps_measured_ttft():
    """An error after real tokens arrived doesn't retroactively erase an
    already-observed TTFT."""
    router = _router(_RaisesAfterTokensBackend())
    with pytest.raises(RuntimeError):
        list(router.stream_complete("hi"))
    rec = agent_trace.ring(1)[0]
    assert rec["ttft_status"] == "measured"
    assert rec["ttft_ms"] is not None
    assert rec["outcome"] == "error"


# ---------------------------------------------------------------------------
# No code path can ever produce ttft_ms == latency_ms (the specific
# regression this whole design exists to prevent — mlx_backend.py's
# renamed-but-aliased situation).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("backend_cls", [
    _CompleteOnlyBackend, _RealStreamBackend, _EmulatedStreamBackend,
    _EmptyStringsBackend,
])
def test_ttft_never_equals_latency(backend_cls):
    router = _router(backend_cls())
    if backend_cls is _CompleteOnlyBackend:
        router.complete("hi")
    else:
        list(router.stream_complete("hi"))
    rec = agent_trace.ring(1)[0]
    if rec["ttft_ms"] is not None:
        assert rec["ttft_ms"] != rec["latency_ms"]


# ---------------------------------------------------------------------------
# prefill_ms — separate field, server_reported only, never rendered as TTFT
# ---------------------------------------------------------------------------

class _PrefillReportingBackend(BaseBackend):
    def complete(self, *a, **kw):
        raise NotImplementedError

    def stream_complete(self, *a, **kw):
        yield "hi"
        yield ModelResponse(text="hi", model="m", tokens_used=1,
                            backend="fake", latency_ms=10.0, prefill_ms=42.0)

    def health_check(self):
        return True


def test_prefill_ms_is_server_reported_and_separate_from_ttft():
    router = _router(_PrefillReportingBackend())
    list(router.stream_complete("hi"))
    rec = agent_trace.ring(1)[0]
    assert rec["prefill_ms"] == 42.0
    assert rec["prefill_source"] == "server_reported"
    assert rec["ttft_status"] == "measured"
    assert rec["prefill_ms"] != rec["ttft_ms"]


def test_prefill_ms_absent_when_backend_does_not_report_it():
    router = _router(_RealStreamBackend())
    list(router.stream_complete("hi"))
    rec = agent_trace.ring(1)[0]
    assert rec["prefill_ms"] is None
    assert rec["prefill_source"] is None
