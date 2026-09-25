"""F20 — ARAIL_AGENT_STREAM_FAST: the fast branch of complete_preferring_deep
streams and joins deltas into the identical string complete() would return;
any failure falls back to complete() unconditionally; flipping the env
default to 0 reverts to the old non-streaming call untouched.
"""

from __future__ import annotations

import pytest

from arail.agents import deep_policy


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    deep_policy._reset_for_tests()
    monkeypatch.delenv("ARAIL_AGENT_DEEP", raising=False)
    monkeypatch.delenv("ARAIL_AGENT_STREAM_FAST", raising=False)
    monkeypatch.setattr(deep_policy, "_aerollm_importable", lambda: True)
    monkeypatch.setattr("arail.tier.is_maximus", lambda: False)  # force fast
    yield
    deep_policy._reset_for_tests()


class _Resp:
    def __init__(self, text):
        self.text = text


class _StreamingRouter:
    """A fake fast router whose stream_complete yields real deltas then a
    terminal ModelResponse — mirrors OllamaNativeBackend's real shape."""

    def __init__(self, text="fast-answer", boom_stream=False, boom_complete=False):
        self.text = text
        self.boom_stream = boom_stream
        self.boom_complete = boom_complete
        self.stream_calls = 0
        self.complete_calls = 0

    def stream_complete(self, prompt, max_tokens=256, temperature=0.7, system=None):
        self.stream_calls += 1
        if self.boom_stream:
            raise RuntimeError("stream exploded")
        for chunk in [self.text[:len(self.text) // 2], self.text[len(self.text) // 2:]]:
            yield chunk
        yield _Resp(self.text)

    def complete(self, prompt, max_tokens=256, temperature=0.7, system=None):
        self.complete_calls += 1
        if self.boom_complete:
            raise RuntimeError("complete exploded")
        return _Resp(self.text)


def test_fast_branch_streams_by_default():
    fast = _StreamingRouter("fast-answer")
    out = deep_policy.complete_preferring_deep("hi", foreground=True, fast_router=fast)
    assert out == "fast-answer"
    assert fast.stream_calls == 1
    assert fast.complete_calls == 0


def test_joined_stream_text_equals_complete_text():
    """The identical string complete() would return -- same content, just
    fetched via stream:true instead of stream:false."""
    streamed = _StreamingRouter("the quick brown fox")
    non_streamed = _StreamingRouter("the quick brown fox")
    out_streamed = deep_policy.complete_preferring_deep(
        "hi", foreground=True, fast_router=streamed)
    out_complete = non_streamed.complete("hi").text
    assert out_streamed == out_complete


def test_stream_failure_falls_back_to_complete_unconditionally():
    fast = _StreamingRouter("fast-answer", boom_stream=True)
    out = deep_policy.complete_preferring_deep("hi", foreground=True, fast_router=fast)
    assert out == "fast-answer"
    assert fast.stream_calls == 1
    assert fast.complete_calls == 1


def test_both_stream_and_complete_fail_returns_none():
    fast = _StreamingRouter(boom_stream=True, boom_complete=True)
    out = deep_policy.complete_preferring_deep("hi", foreground=True, fast_router=fast)
    assert out is None


def test_env_flag_disables_streaming_reverts_to_old_call(monkeypatch):
    monkeypatch.setenv("ARAIL_AGENT_STREAM_FAST", "0")
    fast = _StreamingRouter("fast-answer")
    out = deep_policy.complete_preferring_deep("hi", foreground=True, fast_router=fast)
    assert out == "fast-answer"
    assert fast.stream_calls == 0
    assert fast.complete_calls == 1


@pytest.mark.parametrize("value", ["0", "false", "No", "FALSE"])
def test_env_flag_accepts_common_falsy_spellings(monkeypatch, value):
    monkeypatch.setenv("ARAIL_AGENT_STREAM_FAST", value)
    assert deep_policy._agent_stream_fast_enabled() is False


def test_env_flag_default_is_on():
    assert deep_policy._agent_stream_fast_enabled() is True


def test_router_with_no_stream_complete_falls_back_gracefully():
    """A router lacking stream_complete entirely (e.g. an old fake in a
    caller's test) must not crash -- the unconditional fallback covers
    AttributeError same as any other exception."""
    class _CompleteOnly:
        def __init__(self):
            self.calls = 0

        def complete(self, prompt, **kw):
            self.calls += 1
            return _Resp("legacy-answer")

    fast = _CompleteOnly()
    out = deep_policy.complete_preferring_deep("hi", foreground=True, fast_router=fast)
    assert out == "legacy-answer"
    assert fast.calls == 1
