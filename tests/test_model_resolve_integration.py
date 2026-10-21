"""Boot model resolution -- end-to-end scenarios (sprints/2026-10-20-boot-
model-autoresolve): clean machine, Ollama down for real, loopback-only,
performance, and live-Ollama checks (skipped when no Ollama answers)."""
from __future__ import annotations

import json
import os
import time
import urllib.request

import pytest

from arail import model_defaults as md
from arail import model_resolve as mr


def _live_ollama() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=1) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


requires_live_ollama = pytest.mark.skipif(
    not _live_ollama(), reason="no local Ollama answering on 127.0.0.1:11434")


def _tag(name, gb=1.3, ps="1.2B"):
    return {"id": name, "size_gb": gb, "family": "llama", "parameter_size": ps}


# ── setup-on-clean-machine ─────────────────────────────────────────

def test_clean_minimalist_machine_with_only_llama_ai_eng(monkeypatch, tmp_path):
    """Fresh clone, minimalist, Ollama with only llama-ai-eng: A resolves,
    B is none because the deep model needs maximus (the REAL tier check),
    the strip stays hidden, and a yaml exists afterwards."""
    from arail.registry import core as reg_core
    f = tmp_path / "model_defaults.yaml"
    monkeypatch.setenv("ARAIL_MODEL_DEFAULTS_FILE", str(f))
    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE", str(tmp_path / "reg.json"))
    monkeypatch.setenv("LAB_TIER", "minimalist")
    monkeypatch.setenv("MODEL_BACKEND", "ollama_native")
    monkeypatch.delenv("AEROLLM_MODEL", raising=False)
    monkeypatch.setattr(mr, "_fetch_ollama_tags", lambda: [_tag("llama-ai-eng:latest")])
    monkeypatch.setattr(mr, "_fetch_capabilities", lambda n: ["completion"])
    monkeypatch.setattr(mr, "_total_ram_gb", lambda: 16.0)
    reg_core.reset_registry()
    assert not f.exists()
    out = mr.resolve_and_apply()
    assert out["a"]["model"] == "llama-ai-eng:latest"
    assert out["b"]["model"] is None and "maximus" in out["b"]["reason"]
    assert out["degraded"] == []                      # informational only
    assert f.exists()
    reg_core.reset_registry()


# ── Ollama down for real (closed loopback port, no stubs) ──────────

@pytest.mark.real_resolver_net
def test_portal_boots_ready_when_ollama_is_really_down(monkeypatch, tmp_path):
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                                          # nothing listens here now
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1")
    monkeypatch.setenv("OLLAMA_PORT", str(port))
    monkeypatch.setenv("ARAIL_MODEL_DEFAULTS_FILE", str(tmp_path / "md.yaml"))
    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE", str(tmp_path / "reg.json"))
    from fastapi.testclient import TestClient
    import arail.portal.app as app_mod
    with TestClient(app_mod.app) as client:
        end = time.time() + 5
        while not mr.last_resolution() and time.time() < end:
            time.sleep(0.02)
        assert client.get("/api/ready").json()["ready"] is True
        body = client.get("/api/models/boot").json()
    assert body["mode"] == "degraded"
    assert any(d["kind"] == "ollama_down" for d in body["degraded"])


# ── security: loopback only, airgapped ─────────────────────────────

@pytest.mark.real_resolver_net
@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "8.8.8.8", "example.com", "192.168.1.5"])
def test_resolver_requests_only_loopback_when_airgapped(monkeypatch, host):
    monkeypatch.setenv("LAB_MODE", "airgapped")
    monkeypatch.setenv("OLLAMA_HOST", host)
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append(req.full_url)
        raise OSError("blocked in test")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(mr, "_total_ram_gb", lambda: 16.0)
    monkeypatch.setattr(mr, "_scan_models_dir", lambda: [])
    mr.resolve_boot()
    for url in seen:
        assert url.split("//")[1].split(":")[0] in ("127.0.0.1", "localhost"), url
    if host not in ("127.0.0.1", "localhost"):
        assert seen == []            # a non-loopback host is never contacted


# ── performance ────────────────────────────────────────────────────

def test_resolve_boot_p95_under_one_second_with_ten_models(monkeypatch):
    tags = [_tag(f"model{i}:1b", 1.0, "1B") for i in range(10)]

    def slow(fn):
        def wrapped(*a, **k):
            time.sleep(0.02)
            return fn(*a, **k)
        return wrapped
    monkeypatch.setattr(mr, "_fetch_ollama_tags", slow(lambda: tags))
    monkeypatch.setattr(mr, "_fetch_capabilities", slow(lambda n: ["completion"]))
    monkeypatch.setattr(mr, "_total_ram_gb", lambda: 32.0)
    monkeypatch.setattr(mr, "_scan_models_dir", lambda: [])
    monkeypatch.setattr(mr, "_deep_slot_available", lambda: "deep model needs maximus")
    times = []
    for _ in range(20):
        t0 = time.perf_counter()
        mr.resolve_boot()
        times.append(time.perf_counter() - t0)
    times.sort()
    assert times[int(len(times) * 0.95) - 1] < 1.0


# ── live Ollama (skipped when absent) ──────────────────────────────

@requires_live_ollama
@pytest.mark.real_resolver_net
def test_live_resolver_returns_a_completion_capable_model():
    res = mr.resolve_boot()
    assert res.a.model is not None, res.degraded
    assert "embed" not in res.a.model.lower()


@requires_live_ollama
@pytest.mark.real_resolver_net
def test_live_pinned_keep_alive_is_accepted_by_real_ollama(monkeypatch):
    """F1 end-to-end: the tier-0 pin sends keep_alive as the number -1;
    Ollama 0.32+ answers 200 (the string "-1" was a 400)."""
    from arail.router.backends import OllamaNativeBackend
    res = mr.resolve_boot()
    assert res.a.model
    monkeypatch.setenv("MODEL_BACKEND", "ollama_native")
    monkeypatch.setenv("MODEL_NAME", res.a.model)
    monkeypatch.delenv("ARAIL_OLLAMA_KEEP_ALIVE", raising=False)
    be = OllamaNativeBackend()
    be.model_name = res.a.model
    monkeypatch.setattr(be, "_keep_alive", lambda: -1)
    try:
        assert be.complete("ok", max_tokens=1, think=False).text is not None
    finally:
        # Don't leave the developer's model pinned in memory forever.
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate", method="POST",
            data=json.dumps({"model": res.a.model, "keep_alive": 0}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10).read()
        except Exception:  # noqa: BLE001
            pass
