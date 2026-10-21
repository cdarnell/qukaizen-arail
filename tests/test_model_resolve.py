"""model_resolve.resolve_boot — table-driven over fixtures (no network)."""
from __future__ import annotations

import time

import pytest

from arail import model_resolve as mr


def _tag(name, gb, family="llama", ps=""):
    return {"id": name, "size_gb": gb, "family": family, "parameter_size": ps}


@pytest.fixture
def world(monkeypatch, tmp_path):
    """Stub every external fact the resolver reads."""
    w = {
        "tags": [_tag("llama-ai-eng:latest", 1.3, ps="1.2B"),
                 _tag("nomic-embed-text:latest", 0.3, family="nomic-bert")],
        "caps": {},      # name -> list; missing -> None (heuristic)
        "ram": 32.0, "cap": 14.0, "dirs": [], "deep": None,
    }
    monkeypatch.setattr(mr, "_fetch_ollama_tags", lambda: w["tags"])
    monkeypatch.setattr(mr, "_fetch_capabilities", lambda n: w["caps"].get(n))
    monkeypatch.setattr(mr, "_total_ram_gb", lambda: w["ram"])
    monkeypatch.setattr(mr, "_secondary_cap_b", lambda: w["cap"])
    monkeypatch.setattr(mr, "_scan_models_dir", lambda: w["dirs"])
    monkeypatch.setattr(mr, "_deep_slot_available", lambda: w["deep"])
    monkeypatch.setenv("ARAIL_MODELS_DIR", str(tmp_path))
    w["dir"] = tmp_path
    return w


def _mkdir(world, name, gb, params_name=None):
    (world["dir"] / name).mkdir()
    world["dirs"].append({"id": name, "size_gb": gb})


def test_auto_a_prefers_shipped_default_and_excludes_embeddings(world):
    world["tags"].append(_tag("qwen3:4b", 2.5, ps="4B"))
    res = mr.resolve_boot()
    assert res.a.model == "llama-ai-eng:latest" and res.a.source == "auto"


def test_embedding_only_install_gives_no_a(world):
    world["tags"] = [_tag("nomic-embed-text:latest", 0.3, family="nomic-bert")]
    res = mr.resolve_boot()
    assert res.a.model is None
    assert any("ollama pull llama3.2:1b" in d["detail"] for d in res.degraded)


def test_capabilities_exclude_non_completion(world):
    world["tags"] = [_tag("weird:3b", 1.0, ps="3B")]
    world["caps"] = {"weird:3b": ["embedding"]}
    assert mr.resolve_boot().a.model is None


def test_auto_a_smallest_when_no_default(world):
    world["tags"] = [_tag("zeta:3b", 2.0, ps="3B"), _tag("alpha:1b", 0.8, ps="1B")]
    assert mr.resolve_boot().a.model == "alpha:1b"


def test_a_over_ceiling_excluded(world):
    world["tags"] = [_tag("big:14b", 9.0, ps="14B")]
    assert mr.resolve_boot().a.model is None


def test_valid_pin_wins_over_env_and_auto(world):
    world["tags"].append(_tag("qwen3:4b", 2.5, ps="4B"))
    res = mr.resolve_boot(pins={"a": "qwen3:4b"}, env_pref={"a": "llama-ai-eng"})
    assert (res.a.model, res.a.source) == ("qwen3:4b", "pin")


def test_env_pref_used_without_pin(world):
    world["tags"].append(_tag("qwen3:4b", 2.5, ps="4B"))
    res = mr.resolve_boot(env_pref={"a": "qwen3:4b"})
    assert (res.a.model, res.a.source) == ("qwen3:4b", "env")


def test_missing_pin_falls_through_with_reason(world):  # FM3
    res = mr.resolve_boot(pins={"a": "ghost:7b"})
    assert res.a.model == "llama-ai-eng:latest" and res.a.source == "auto"
    assert "ghost:7b" in res.a.reason and "ollama pull ghost:7b" in res.a.reason
    assert any(d["kind"] == "missing" for d in res.degraded)


def test_ollama_down_uses_cache(world, monkeypatch):  # FM1
    monkeypatch.setattr(mr, "_fetch_ollama_tags", lambda: None)
    res = mr.resolve_boot(cache={"a": {"model": "llama-ai-eng:latest"}})
    assert (res.a.model, res.a.source) == ("llama-ai-eng:latest", "cache")
    assert any(d["kind"] == "ollama_down" for d in res.degraded)


def test_ollama_down_no_cache(world, monkeypatch):
    monkeypatch.setattr(mr, "_fetch_ollama_tags", lambda: None)
    res = mr.resolve_boot()
    assert res.a.model is None and res.a.source == "none"


def test_b_none_when_not_maximus(world):
    world["deep"] = "deep model needs maximus"
    res = mr.resolve_boot()
    assert res.b.model is None and "maximus" in res.b.reason


def test_b_pin_none_is_honoured_even_with_candidates(world):
    _mkdir(world, "Qwen3-30B-A3B-MoE-4bit", 8.0)
    res = mr.resolve_boot(pins={"b": "none"}, env_pref={"b": "Qwen3-30B-A3B-MoE-4bit"})
    assert res.b.model is None and res.b.source == "pin"


def test_b_auto_prefers_moe_then_known_size(world, monkeypatch):
    _mkdir(world, "Dense-7B-Instruct-4bit", 4.0)
    _mkdir(world, "Mystery-model", 4.0)          # unknown size -> excluded
    _mkdir(world, "Qwen3-MoE-12B-4bit", 6.0)
    res = mr.resolve_boot()
    assert res.b.model == "Qwen3-MoE-12B-4bit" and res.b.source == "auto"


def test_b_unknown_size_not_auto_eligible_but_pinnable_fails_too(world):
    _mkdir(world, "Mystery-model", 4.0)
    res = mr.resolve_boot()
    assert res.b.model is None


def test_b_over_cap_excluded(world):
    world["cap"] = 3.0
    _mkdir(world, "Dense-7B-Instruct-4bit", 4.0)
    assert mr.resolve_boot().b.model is None


def test_ram_budget_drops_b_keeps_a(world):  # FM4, FM16
    world["ram"] = 16.0                       # budget 9.6 GB
    world["tags"] = [_tag("llama-ai-eng:latest", 4.7, ps="1.2B")]
    _mkdir(world, "Dense-7B-Instruct-4bit", 6.0)
    res = mr.resolve_boot()
    assert res.a.model == "llama-ai-eng:latest"
    assert res.b.model is None and "insufficient_ram" in res.b.reason
    assert any(d["kind"] == "insufficient_ram" for d in res.degraded)


def test_b_pin_missing_dir_falls_through(world):
    _mkdir(world, "Dense-7B-Instruct-4bit", 4.0)
    res = mr.resolve_boot(pins={"b": "Gone-7B"})
    assert res.b.model == "Dense-7B-Instruct-4bit" and "Gone-7B" in res.b.reason


def test_skip_b_after_crash(world):  # FM5
    _mkdir(world, "Dense-7B-Instruct-4bit", 4.0)
    res = mr.resolve_boot(skip_b="Dense-7B-Instruct-4bit")
    assert res.b.model is None
    assert any(d["kind"] == "crashed_last_boot" for d in res.degraded)


def test_never_raises(world, monkeypatch):
    def boom():
        raise RuntimeError("x")
    monkeypatch.setattr(mr, "_total_ram_gb", boom)
    res = mr.resolve_boot(cache={"a": {"model": "m"}})
    assert res.a.model == "m"


def test_timeout_keeps_cache(world, monkeypatch):  # FM9
    monkeypatch.setattr(mr, "_fetch_ollama_tags", lambda: time.sleep(1) or [])
    t0 = time.time()
    res = mr.resolve_boot_bounded(timeout=0.1, cache={"a": {"model": "cached:1b"}})
    assert time.time() - t0 < 0.8
    assert res.a.model == "cached:1b" and res.a.source == "cache"
    assert res.degraded[0]["kind"] == "resolver_timeout"


@pytest.mark.parametrize("host,ok", [("127.0.0.1", True), ("localhost", True),
                                     ("8.8.8.8", False), ("evil.example.com", False)])
def test_ollama_base_is_loopback_only(monkeypatch, host, ok):  # FM14
    monkeypatch.setenv("OLLAMA_HOST", host)
    monkeypatch.delenv("OLLAMA_PORT", raising=False)
    assert (mr._ollama_base() is not None) is ok


def test_ollama_port_env_is_honoured(monkeypatch):  # FM18
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1")
    monkeypatch.setenv("OLLAMA_PORT", "22434")
    assert mr._ollama_base() == "http://127.0.0.1:22434"


def test_non_loopback_host_means_unreachable_without_request(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "8.8.8.8")
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("network used"))
    assert mr._fetch_ollama_tags() is None
