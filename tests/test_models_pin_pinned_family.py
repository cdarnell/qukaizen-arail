"""Pin entry points refuse the opt-in family unless the served weights match
its pin record (sprint 2026-11-18-aiml-slm-engine, T-A1..T-A3)."""
from __future__ import annotations

import json

import pytest

from arail import agent_trace
from arail import model_defaults as md
from arail import model_pins as mp
from arail import model_resolve as mr

FAMILY = "qkz-aiml-qwen3-1.7b"
CONTROL = f"{FAMILY}:control-merge"
TAG = f"{FAMILY}:1.0.0"
GOOD = "ab" * 32
OTHER = "d67e0abf" + "1" * 56


def _tags(*names):
    return [{"id": n, "size_gb": 1.0, "family": "llama", "parameter_size": "1.2B"}
            for n in names]


@pytest.fixture
def env(monkeypatch, tmp_path):
    from arail.registry import core as reg_core
    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE", str(tmp_path / "model_registry.json"))
    monkeypatch.setenv("ARAIL_MODELS_DIR", str(tmp_path / "models"))
    (tmp_path / "models").mkdir()
    monkeypatch.setenv("MODEL_BACKEND", "ollama_native")
    reg_core.reset_registry()
    st = {"tags": _tags("llama-ai-eng:latest", CONTROL), "pin_tags": {}, "show": {}}
    monkeypatch.setattr(mr, "_fetch_ollama_tags", lambda: st["tags"])
    monkeypatch.setattr(mr, "_fetch_capabilities", lambda n: ["completion"])
    monkeypatch.setattr(mr, "_total_ram_gb", lambda: 32.0)
    monkeypatch.setattr(mr, "_secondary_cap_b", lambda: 14.0)
    monkeypatch.setattr(mr, "_deep_slot_available", lambda: None)
    monkeypatch.setattr(mp, "_fetch_tags", lambda: st["pin_tags"])
    monkeypatch.setattr(mp, "_fetch_show", lambda t: st["show"].get(t))
    monkeypatch.setattr(
        "arail.chat._ollama_installed_models",
        lambda *a, **k: [{"id": t["id"], "runtime": "ollama", "size_gb": 1.0,
                          "modified": "", "endpoint": "x"} for t in st["tags"]])
    mp.clear_caches()
    st["tmp"] = tmp_path
    yield st
    reg_core.reset_registry()


def _pin_pinned(st, monkeypatch):
    doc = json.loads(mp.PINS_PATH.read_text())
    rec = doc["pins"][0]
    rec.update(status="pinned", ollama_tag=TAG, weights_sha256=GOOD)
    rec["serving"]["parameters"]["num_ctx"] = 8192
    rec["release"]["source_commit"] = "c" * 40
    f = st["tmp"] / "pins.json"
    f.write_text(json.dumps(doc))
    monkeypatch.setenv("ARAIL_MODEL_PINS_FILE", str(f))
    mp.clear_caches()


def _serve(st, tag, weights):
    st["pin_tags"][tag] = "m-" + tag
    st["show"][tag] = {"modelfile": f"FROM /Users/x/blobs/sha256-{weights}\n",
                       "template": "t", "parameters": "num_ctx 8192"}


@pytest.fixture
def client(env):
    from fastapi.testclient import TestClient
    import arail.portal.app as app_mod
    with TestClient(app_mod.app) as c:
        yield c


def test_t_a1_pending_record_refuses_with_plain_message(client):
    r = client.post("/api/models/pin", json={"slot": "a", "model": FAMILY})
    assert r.status_code == 400 and "not released yet" in r.json()["detail"]
    r = client.post("/api/models/pin", json={"slot": "a", "model": CONTROL})
    assert r.status_code == 400 and "not released yet" in r.json()["detail"]


def test_t_a2_control_merge_is_not_the_pinned_build(client, env, monkeypatch):
    _pin_pinned(env, monkeypatch)
    _serve(env, CONTROL, OTHER)
    r = client.post("/api/models/pin", json={"slot": "a", "model": CONTROL})
    assert r.status_code == 400
    assert "not the pinned build" in r.json()["detail"]
    assert "/Users/" not in r.json()["detail"]
    r = client.post("/api/chat/default", json={
        "provider": "ollama", "model": CONTROL, "runtime": "ollama"})
    assert r.json()["ok"] is False
    import os
    assert os.environ.get("ARAIL_CHAT_DEFAULT_MODEL") is None
    secrets = env["tmp"] / "secrets.env"
    assert not secrets.exists() or CONTROL not in secrets.read_text()


def test_t_a3_verified_pin_emits_trace_event_and_clear_reresolves(client, env, monkeypatch):
    _pin_pinned(env, monkeypatch)
    env["tags"] = _tags("llama-ai-eng:latest", TAG)
    _serve(env, TAG, GOOD)
    events = []
    monkeypatch.setattr(agent_trace, "record", lambda **f: events.append(f))
    r = client.post("/api/models/pin", json={"slot": "a", "model": TAG})
    assert r.status_code == 200, r.text
    assert r.json()["resolution"]["a"]["model"] == TAG
    pins = [e for e in events if e.get("event") == "model_pin"]
    assert len(pins) == 1
    assert pins[0]["attrs"] == {"slot": "a", "pin": TAG, "resolved_model": TAG,
                                "pin_state": "match"}
    r = client.post("/api/models/pin", json={"slot": "a", "model": None})
    assert r.status_code == 200
    assert r.json()["resolution"]["a"]["model"] == "llama-ai-eng:latest"
    assert md.read()["pins"]["a"] is None
