"""arail.nucleus.providers.fallback_ollama — eval-only, no logprobs."""

from __future__ import annotations

import pytest

from arail.nucleus.providers.base import Decoding, Prompt
from arail.nucleus.providers.fallback_ollama import OllamaFallbackProvider


def test_capabilities_never_advertise_logprobs():
    provider = OllamaFallbackProvider("ai-engineer")
    caps = provider.capabilities()
    assert caps.logprobs_topn is False
    assert caps.max_top_n == 0


def test_generate_with_topn_raises_not_implemented():
    provider = OllamaFallbackProvider("ai-engineer")
    with pytest.raises(NotImplementedError, match="logprobs_topn"):
        provider.generate_with_topn([Prompt(item_id="a", text="q")], Decoding(), top_n=5)


def test_generate_posts_to_loopback(monkeypatch):
    calls = []

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"response": "the answer"}

    def _fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return _FakeResponse()

    monkeypatch.setattr("requests.post", _fake_post)
    provider = OllamaFallbackProvider("ai-engineer")
    out = provider.generate([Prompt(item_id="a", text="hello")], Decoding(temperature=0.2))
    assert out[0].text == "the answer"
    assert calls[0][0] == "http://127.0.0.1:11434/api/generate"
    assert calls[0][1]["model"] == "ai-engineer"
