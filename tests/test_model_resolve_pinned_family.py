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


# -- F1 / F2 closed: the opt-in family is pin-only and digest-verified

def test_t_r1_only_family_installed_gives_none(world):
    world["tags"] = [_tag(CONTROL, 1.4, ps="1.7B")]
    res = mr.resolve_boot()
    assert res.a.model is None


def test_t_r3_pin_of_real_tag_never_resolves_to_control(world):
    world["tags"] = [_tag(CONTROL, 1.4, ps="1.7B"),
                     _tag("llama-ai-eng:latest", 1.3, family="llama", ps="1.2B")]
    res = mr.resolve_boot(pins={"a": f"{FAMILY}:1.0.0"})
    assert res.a.model == "llama-ai-eng:latest"


# -- helpers for a pinned record served by a stubbed Ollama -------------------

import json  # noqa: E402

from arail import model_pins as mp  # noqa: E402

GOOD = "ab" * 32
OTHER = "d67e0abf" + "1" * 56
TAG = f"{FAMILY}:1.0.0"


@pytest.fixture
def pinned(monkeypatch, tmp_path):
    doc = json.loads(mp.PINS_PATH.read_text())
    rec = doc["pins"][0]
    rec.update(status="pinned", ollama_tag=TAG, weights_sha256=GOOD)
    rec["serving"]["parameters"]["num_ctx"] = 8192
    rec["release"]["source_commit"] = "c" * 40
    f = tmp_path / "pins.json"
    f.write_text(json.dumps(doc))
    monkeypatch.setenv("ARAIL_MODEL_PINS_FILE", str(f))
    st = {"tags": {}, "show": {}}
    monkeypatch.setattr(mp, "_fetch_tags", lambda: st["tags"])
    monkeypatch.setattr(mp, "_fetch_show", lambda t: st["show"].get(t))

    def serve(tag, weights):
        st["tags"][tag] = "m-" + tag
        st["show"][tag] = {"modelfile": f"FROM /Users/x/.ollama/blobs/sha256-{weights}\n",
                           "template": "t", "parameters": "num_ctx 8192"}
    st["serve"] = serve
    mp.clear_caches()
    return st


def test_t_r2_auto_never_picks_family_when_llama_absent(world):
    world["tags"] = [_tag(CONTROL, 1.4, ps="1.7B"), _tag("qwen3:1.7b", 1.4, ps="1.7B")]
    res = mr.resolve_boot()
    assert (res.a.model, res.a.source) == ("qwen3:1.7b", "auto")


def test_t_r1_reason_names_the_opt_in_model(world):
    world["tags"] = [_tag(CONTROL, 1.4, ps="1.7B")]
    res = mr.resolve_boot()
    assert "opt-in" in res.a.reason and "llama-ai-eng" in res.a.reason


def test_t_r3_pending_record_skips_pin_with_missing_note(world):
    world["tags"] = [_tag(CONTROL, 1.4, ps="1.7B"),
                     _tag("llama-ai-eng:latest", 1.3, family="llama", ps="1.2B")]
    res = mr.resolve_boot(pins={"a": CONTROL})
    assert res.a.model == "llama-ai-eng:latest" and res.a.source == "auto"
    assert any(d["kind"] == "missing" for d in res.degraded)


def test_t_r3_pin_naming_control_is_a_pin_mismatch(world, pinned):
    world["tags"] = [_tag(CONTROL, 1.4, ps="1.7B"),
                     _tag("llama-ai-eng:latest", 1.3, family="llama", ps="1.2B")]
    pinned["serve"](CONTROL, OTHER)
    res = mr.resolve_boot(pins={"a": CONTROL})
    assert res.a.model == "llama-ai-eng:latest"
    notes = [d for d in res.degraded if d["kind"] == "pin_mismatch"]
    assert notes and "/Users/" not in notes[0]["detail"]


def test_t_r4_ollama_down_never_honours_family_pin(world, monkeypatch):
    monkeypatch.setattr(mr, "_fetch_ollama_tags", lambda: None)
    res = mr.resolve_boot(pins={"a": TAG})
    assert res.a.model is None and "while Ollama is down" in res.a.reason
    res = mr.resolve_boot(pins={"a": TAG}, cache={"a": {"model": CONTROL}},
                          env_pref={"a": "llama-ai-eng"})
    assert (res.a.model, res.a.source) == ("llama-ai-eng", "env")
    res = mr.resolve_boot(pins={"a": "llama-ai-eng"})
    assert (res.a.model, res.a.source) == ("llama-ai-eng", "pin")


def test_t_r5_verified_pin_resolves_with_source_pin(world, pinned):
    world["tags"] = [_tag(TAG, 1.4, ps="1.7B"),
                     _tag("llama-ai-eng:latest", 1.3, family="llama", ps="1.2B")]
    pinned["serve"](TAG, GOOD)
    res = mr.resolve_boot(pins={"a": TAG})
    assert (res.a.model, res.a.source) == (TAG, "pin")
    res = mr.resolve_boot(pins={"a": FAMILY})        # bare family id -> pinned tag
    assert (res.a.model, res.a.source) == (TAG, "pin")


def test_auto_still_skips_a_verified_family_tag(world, pinned):
    world["tags"] = [_tag(TAG, 1.4, ps="1.7B")]
    pinned["serve"](TAG, GOOD)
    assert mr.resolve_boot().a.model is None
