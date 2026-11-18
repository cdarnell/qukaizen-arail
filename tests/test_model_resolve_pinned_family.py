"""Resolver behaviour for the opt-in ``qkz-aiml-qwen3-1.7b`` family.

Sprint 2026-11-18-aiml-slm-engine. The opt-in family is pin-only, exact-tag,
and verified against the pinned weights digest; auto never picks it.
"""
from __future__ import annotations

import pytest

from arail import model_resolve as mr

FAMILY = "qkz-aiml-qwen3-1.7b"
CONTROL = f"{FAMILY}:control-merge"


def _tag(name, gb, family="qwen3", ps=""):
    return {"id": name, "size_gb": gb, "family": family, "parameter_size": ps}


@pytest.fixture
def world(monkeypatch, tmp_path):
    w = {"tags": [], "caps": {}, "ram": 32.0}
    monkeypatch.setattr(mr, "_fetch_ollama_tags", lambda: w["tags"])
    monkeypatch.setattr(mr, "_fetch_capabilities", lambda n: w["caps"].get(n))
    monkeypatch.setattr(mr, "_total_ram_gb", lambda: w["ram"])
    monkeypatch.setattr(mr, "_secondary_cap_b", lambda: 14.0)
    monkeypatch.setattr(mr, "_scan_models_dir", lambda: [])
    monkeypatch.setattr(mr, "_deep_slot_available", lambda: "no deep in test")
    monkeypatch.setenv("ARAIL_MODELS_DIR", str(tmp_path))
    return w


# -- T-R6: regression lock for the non-family bare-name fallback (TD3) -------

def test_non_family_bare_name_fallback_is_unchanged(world):
    world["tags"] = [_tag("alpha:2b", 1.0, ps="2B")]
    res = mr.resolve_boot(pins={"a": "alpha:1b"})
    assert (res.a.model, res.a.source) == ("alpha:2b", "pin")


def test_llama_still_ranks_first_with_family_installed(world):
    world["tags"] = [_tag(CONTROL, 1.4, ps="1.7B"),
                     _tag("llama-ai-eng:latest", 1.3, family="llama", ps="1.2B")]
    res = mr.resolve_boot()
    assert res.a.model == "llama-ai-eng:latest"


# -- F1 / F2 as they behave today; flipped to plain tests by the resolver commit

@pytest.mark.xfail(strict=True, reason="F2: auto picks the opt-in family when it is the only model")
def test_t_r1_only_family_installed_gives_none(world):
    world["tags"] = [_tag(CONTROL, 1.4, ps="1.7B")]
    res = mr.resolve_boot()
    assert res.a.model is None


@pytest.mark.xfail(strict=True, reason="F1: a pin of the real tag resolves to control-merge via the bare-name fallback")
def test_t_r3_pin_of_real_tag_never_resolves_to_control(world):
    world["tags"] = [_tag(CONTROL, 1.4, ps="1.7B"),
                     _tag("llama-ai-eng:latest", 1.3, family="llama", ps="1.2B")]
    res = mr.resolve_boot(pins={"a": f"{FAMILY}:1.0.0"})
    assert res.a.model == "llama-ai-eng:latest"
