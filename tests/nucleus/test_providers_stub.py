"""arail.nucleus.providers.stub — deterministic CI provider (T-STUB-2 partial)."""

from __future__ import annotations

import pytest

from arail.nucleus.errors import DomainConfigError
from arail.nucleus.providers.base import Decoding, Prompt
from arail.nucleus.providers.stub import StubJudge, StubProvider


def test_stub_disabled_without_env(monkeypatch):
    monkeypatch.delenv("ARAIL_NUCLEUS_STUB", raising=False)
    with pytest.raises(DomainConfigError):
        StubProvider()


def test_stub_generate_is_deterministic(monkeypatch):
    monkeypatch.setenv("ARAIL_NUCLEUS_STUB", "1")
    p1 = StubProvider(answers={("item-1", "teacher"): "the answer"})
    p2 = StubProvider(answers={("item-1", "teacher"): "the answer"})
    prompts = [Prompt(item_id="item-1", text="q", role="teacher")]
    g1 = p1.generate(prompts, Decoding())
    g2 = p2.generate(prompts, Decoding())
    assert g1[0].text == g2[0].text == "the answer"
    assert g1[0].token_ids == g2[0].token_ids


def test_stub_generate_with_topn_shapes(monkeypatch):
    monkeypatch.setenv("ARAIL_NUCLEUS_STUB", "1")
    p = StubProvider(answers={("item-1", "teacher"): "two words"})
    prompts = [Prompt(item_id="item-1", text="q", role="teacher")]
    out = p.generate_with_topn(prompts, Decoding(), top_n=5)
    gen = out[0]
    assert len(gen.token_ids) == 2  # "two words" -> 2 tokens
    assert all(len(row) == 5 for row in gen.topn_ids)
    assert all(len(row) == 5 for row in gen.topn_logprobs)
    # the sampled token at each step is always present in that step's top-N
    for step, sampled in enumerate(gen.token_ids):
        assert sampled in gen.topn_ids[step]


def test_stub_falls_back_to_placeholder_for_unknown_item(monkeypatch):
    monkeypatch.setenv("ARAIL_NUCLEUS_STUB", "1")
    p = StubProvider(quality="degraded")
    out = p.generate([Prompt(item_id="unknown", text="q")], Decoding())
    assert "degraded" in out[0].text
    assert "unknown" in out[0].text


def test_stub_judge_deterministic_preferences(monkeypatch):
    monkeypatch.setenv("ARAIL_NUCLEUS_STUB", "1")
    judge = StubJudge(preferences={"item-1": "B"})
    assert judge.judge("item-1", "a", "b") == "B"
    assert judge.judge("item-2", "a", "b") == "A"  # default fallback


def test_stub_capabilities(monkeypatch):
    monkeypatch.setenv("ARAIL_NUCLEUS_STUB", "1")
    caps = StubProvider().capabilities()
    assert caps.logprobs_topn is True
    assert caps.max_top_n >= 20


def test_stub_close_is_idempotent(monkeypatch):
    monkeypatch.setenv("ARAIL_NUCLEUS_STUB", "1")
    p = StubProvider()
    p.close()
    p.close()
