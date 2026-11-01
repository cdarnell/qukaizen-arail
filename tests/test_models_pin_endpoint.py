"""POST /api/models/pin -- pin or clear one slot (sprints/2026-10-20-boot-
model-autoresolve). The pin is written, the slot re-resolved, env + registry
stamped, and the warm scheduled without the response waiting on it."""
from __future__ import annotations

import asyncio
import os

import pytest

from arail import model_defaults as md
from arail import model_resolve as mr


def _tags(*names):
    return [{"id": n, "size_gb": 1.0, "family": "llama", "parameter_size": ""}
            for n in names]


@pytest.fixture
def env(monkeypatch, tmp_path):
    from arail.registry import core as reg_core
    f = tmp_path / "model_defaults.yaml"
    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE", str(tmp_path / "model_registry.json"))
    monkeypatch.setenv("ARAIL_MODEL_DEFAULTS_FILE", str(f))
    monkeypatch.setenv("ARAIL_MODELS_DIR", str(tmp_path / "models"))
    (tmp_path / "models").mkdir()
    monkeypatch.setenv("MODEL_BACKEND", "ollama_native")   # tier 0 fronts Ollama
    reg_core.reset_registry()
    state = {"tags": _tags("llama-ai-eng:latest", "qwen3:4b")}
    monkeypatch.setattr(mr, "_fetch_ollama_tags", lambda: state["tags"])
    monkeypatch.setattr(mr, "_fetch_capabilities", lambda n: ["completion"])
    monkeypatch.setattr(mr, "_total_ram_gb", lambda: 32.0)
    monkeypatch.setattr(mr, "_secondary_cap_b", lambda: 14.0)
    monkeypatch.setattr(mr, "_deep_slot_available", lambda: None)

    def installed():
        return [{"id": t["id"], "runtime": "ollama", "size_gb": 1.0,
                 "modified": "", "endpoint": "x"} for t in state["tags"]]
    monkeypatch.setattr("arail.chat._ollama_installed_models", lambda *a, **k: installed())
    state["file"], state["dir"] = f, tmp_path / "models"
    yield state
    reg_core.reset_registry()


@pytest.fixture
def client(env):
    from fastapi.testclient import TestClient
    import arail.portal.app as app_mod
    with TestClient(app_mod.app) as c:
        yield c


def test_pin_a_writes_pin_resolves_and_survives_restart(client, env):
    r = client.post("/api/models/pin", json={"slot": "a", "model": "qwen3:4b"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resolution"]["a"]["model"] == "qwen3:4b"
    assert body["resolution"]["a"]["source"] == "pin"
    assert md.read(env["file"])["pins"]["a"] == "qwen3:4b"
    assert client.get("/api/models/boot").json()["resolution"]["a"]["model"] == "qwen3:4b"
    entries = {e["id"]: e for e in body["state"]["entries"]}
    assert entries["tier0-local"]["model_id"] == "qwen3:4b"
    # "Restart": drop in-process state; the pin alone reproduces the choice.
    mr.last_resolution().clear()
    again = mr.resolve_and_apply()
    assert again["a"]["model"] == "qwen3:4b" and again["a"]["source"] == "pin"


def test_pin_returns_before_the_warm_completes(client, env, monkeypatch):
    import arail.portal.app as app_mod
    monkeypatch.setenv("ARAIL_BOOT_WARM", "1")
    gate = {"finished": False}

    async def slow_warm():
        await asyncio.sleep(0.5)
        gate["finished"] = True

    monkeypatch.setattr(app_mod, "_warm_slot_a", slow_warm)
    r = client.post("/api/models/pin", json={"slot": "a", "model": "qwen3:4b"})
    assert r.status_code == 200
    assert gate["finished"] is False      # response did not wait on the warm


def test_clear_pin_returns_to_auto(client, env):
    client.post("/api/models/pin", json={"slot": "a", "model": "qwen3:4b"})
    r = client.post("/api/models/pin", json={"slot": "a", "model": None})
    assert r.status_code == 200
    assert md.read(env["file"])["pins"]["a"] is None
    assert r.json()["resolution"]["a"]["source"] == "auto"


def test_pin_none_for_b_is_honoured_and_a_cannot_be_none(client, env):
    (env["dir"] / "Dense-7B-4bit").mkdir()
    r = client.post("/api/models/pin", json={"slot": "b", "model": "none"})
    assert r.status_code == 200
    assert r.json()["resolution"]["b"]["model"] is None
    assert md.read(env["file"])["pins"]["b"] == "none"
    assert "AEROLLM_MODEL" not in os.environ
    bad = client.post("/api/models/pin", json={"slot": "a", "model": "none"})
    assert bad.status_code == 400


def test_pin_b_valid_model(client, env):
    (env["dir"] / "Dense-7B-4bit").mkdir()
    r = client.post("/api/models/pin", json={"slot": "b", "model": "Dense-7B-4bit"})
    assert r.status_code == 200, r.text
    assert r.json()["resolution"]["b"]["model"] == "Dense-7B-4bit"
    assert os.environ["AEROLLM_MODEL"] == "Dense-7B-4bit"


def test_pin_refuses_unknown_slot_and_uninstalled_a(client):
    assert client.post("/api/models/pin", json={"slot": "c", "model": "x"}).status_code == 400
    r = client.post("/api/models/pin", json={"slot": "a", "model": "ghost:3b"})
    assert r.status_code == 400 and "ollama pull ghost:3b" in r.json()["detail"]


def test_pin_a_over_ceiling_refused(client, env):
    env["tags"].append(_tags("big:70b")[0])
    r = client.post("/api/models/pin", json={"slot": "a", "model": "big:70b"})
    assert r.status_code == 400


@pytest.mark.parametrize("evil", ["../outside", "../../etc", "a/../../outside",
                                  "/etc", "/private/tmp"])
def test_pin_b_rejects_path_traversal_and_outside_absolute_paths(client, env, evil):
    r = client.post("/api/models/pin", json={"slot": "b", "model": evil})
    assert r.status_code == 400
    assert md.read(env["file"])["pins"]["b"] is None


def test_pin_b_symlink_escape_rejected(client, env, tmp_path):
    outside = tmp_path / "outside-dir"
    outside.mkdir()
    (env["dir"] / "sneaky").symlink_to(outside)
    r = client.post("/api/models/pin", json={"slot": "b", "model": "sneaky"})
    assert r.status_code == 400


def test_pin_b_absolute_path_allowed_only_when_env_already_points_there(client, env, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere" / "Dense-7B-4bit"
    elsewhere.mkdir(parents=True)
    r = client.post("/api/models/pin", json={"slot": "b", "model": str(elsewhere)})
    assert r.status_code == 400
    monkeypatch.setenv("AEROLLM_MODEL", str(elsewhere))
    r = client.post("/api/models/pin", json={"slot": "b", "model": str(elsewhere)})
    assert r.status_code == 200, r.text


def test_pin_b_clears_a_crash_sentinel_so_it_is_a_retry(client, env, monkeypatch):
    from arail.portal import model_warmth as mw
    (env["dir"] / "Dense-7B-4bit").mkdir()
    import subprocess
    import sys
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    import json
    mw.sentinel_path().write_text(json.dumps(
        {"model": "Dense-7B-4bit", "pid": p.pid, "started_at": 1}))
    assert mw.crashed_model() == "Dense-7B-4bit"
    r = client.post("/api/models/pin", json={"slot": "b", "model": "Dense-7B-4bit"})
    assert r.status_code == 200
    assert r.json()["resolution"]["b"]["model"] == "Dense-7B-4bit"
    assert not mw.sentinel_path().exists()


def test_unwritable_yaml_is_a_clean_500_not_a_crash(client, env, monkeypatch):
    def boom(*a, **k):
        raise OSError("read-only")
    monkeypatch.setattr(os, "replace", boom)
    r = client.post("/api/models/pin", json={"slot": "a", "model": "qwen3:4b"})
    assert r.status_code == 500 and "model_defaults.yaml" in r.json()["detail"]


def test_chat_default_deep_slot_persists_as_pin_b(client, env, isolated_secrets):
    from unittest.mock import patch
    from arail.portal import app as portal_app
    (env["dir"] / "Dense-7B-4bit").mkdir()
    with patch.object(portal_app, "_aerollm_model_ready", return_value=True):
        r = client.post("/api/chat/default", json={"slot": "deep", "model": "Dense-7B-4bit"})
    assert r.json()["ok"] is True
    assert md.read(env["file"])["pins"]["b"] == "Dense-7B-4bit"


def test_chat_default_resident_persists_as_pin_a(client, env, isolated_secrets):
    r = client.post("/api/chat/default", json={
        "provider": "my_machine", "model": "qwen3:4b", "runtime": "ollama"})
    assert r.json()["registry_updated"] is True
    assert md.read(env["file"])["pins"]["a"] == "qwen3:4b"
