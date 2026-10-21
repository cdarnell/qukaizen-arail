"""POST /api/models/settle -- DEPRECATED alias (one release) for two pin
writes via POST /api/models/pin, kept working with the same refusals.
Refuses any choice that isn't actually installed/on-disk, or that violates
the answering-model ceiling (arail.registry.ceiling).

Changed from the PR #186 suite: the success path now also asserts the
Deprecation/Link headers and ``deprecated: true`` (new), the file assertion
checks v2 pins instead of v1 keys, and "mode == hidden afterwards" is "no
degraded notes". The old "settled" activity event text is kept.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def client(monkeypatch, tmp_path):
    from arail.registry import core as reg_core
    from arail.portal import models_api

    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE", str(tmp_path / "model_registry.json"))
    monkeypatch.setenv("ARAIL_MODEL_DEFAULTS_FILE", str(tmp_path / "model_defaults.yaml"))
    monkeypatch.setenv("ARAIL_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "models").mkdir()
    reg_core.reset_registry()
    models_api._BOOT_CANDIDATES_CACHE["payload"] = None
    models_api._BOOT_CANDIDATES_CACHE["ts"] = 0.0

    from fastapi.testclient import TestClient
    import arail.portal.app as app_mod
    with TestClient(app_mod.app) as c:
        yield c
    reg_core.reset_registry()
    models_api._BOOT_CANDIDATES_CACHE["payload"] = None


def _mock_ollama_installed(monkeypatch, models):
    monkeypatch.setattr("arail.chat._ollama_installed_models", lambda *a, **kw: models)
    # The re-resolve after a pin reads the resolver's own view of the machine.
    from arail import model_resolve as mr
    tags = [{"id": m["id"], "size_gb": m.get("size_gb"), "family": "llama",
             "parameter_size": ""} for m in models]
    monkeypatch.setattr(mr, "_fetch_ollama_tags", lambda: tags)
    monkeypatch.setattr(mr, "_fetch_capabilities", lambda n: ["completion"])
    monkeypatch.setattr(mr, "_total_ram_gb", lambda: 32.0)
    monkeypatch.setattr(mr, "_deep_slot_available", lambda: None)


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

def test_refuses_default_a_not_installed(client, monkeypatch):
    _mock_ollama_installed(monkeypatch, [])
    r = client.post("/api/models/settle", json={"default_a": "not-installed:latest"})
    assert r.status_code == 400
    assert "ollama pull not-installed:latest" in r.json()["detail"]


def test_refuses_empty_default_a(client):
    r = client.post("/api/models/settle", json={"default_a": ""})
    assert r.status_code == 400


def test_refuses_default_a_over_primary_ceiling(client, monkeypatch):
    _mock_ollama_installed(monkeypatch, [
        {"id": "llama-3.1-405b", "runtime": "ollama", "size_gb": 200,
         "modified": "", "endpoint": "x"}])
    r = client.post("/api/models/settle", json={"default_a": "llama-3.1-405b"})
    assert r.status_code == 400
    assert "8" in r.json()["detail"]  # ceiling message mentions the 8B threshold


def test_refuses_default_a_with_unknown_param_count(client, monkeypatch):
    """The ceiling's core rule: unknown params refuse, never default to
    small — an installed model with a name the resolver can't parse
    must not sail through as if it were tiny."""
    _mock_ollama_installed(monkeypatch, [
        {"id": "totally-opaque-name", "runtime": "ollama", "size_gb": 5,
         "modified": "", "endpoint": "x"}])
    r = client.post("/api/models/settle", json={"default_a": "totally-opaque-name"})
    assert r.status_code == 400
    assert "unknown parameter count" in r.json()["detail"]


def test_refuses_default_b_directory_missing(client, monkeypatch):
    _mock_ollama_installed(monkeypatch, [
        {"id": "llama-ai-eng", "runtime": "ollama", "size_gb": 0.9,
         "modified": "", "endpoint": "x"}])
    r = client.post("/api/models/settle",
                     json={"default_a": "llama-ai-eng", "default_b": "never-downloaded"})
    assert r.status_code == 400
    assert "hf download" in r.json()["detail"]


def test_refuses_default_b_over_secondary_cap(client, monkeypatch, tmp_path):
    _mock_ollama_installed(monkeypatch, [
        {"id": "llama-ai-eng", "runtime": "ollama", "size_gb": 0.9,
         "modified": "", "endpoint": "x"}])
    (tmp_path / "models" / "Llama-3.1-405B-4bit").mkdir()
    from arail import hardware as hw
    monkeypatch.setattr(hw, "secondary_model_cap_b", lambda *a, **kw: 8.0)
    r = client.post("/api/models/settle", json={
        "default_a": "llama-ai-eng", "default_b": "Llama-3.1-405B-4bit"})
    assert r.status_code == 400
    assert "cap" in r.json()["detail"] or "over" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_settle_default_b_none_is_always_valid(client, monkeypatch, tmp_path):
    # restart_required is computed from the resident runtime; pin "nothing
    # resident" so a leftover AeroLLMBackend singleton from another test (seen
    # after test_aerollm_defaults.py) can't flip it. Same fix as 8b9d5a0e.
    from arail.router.backends import AeroLLMBackend
    monkeypatch.setattr(AeroLLMBackend, "_shared", {}, raising=False)
    _mock_ollama_installed(monkeypatch, [
        {"id": "llama-ai-eng", "runtime": "ollama", "size_gb": 0.9,
         "modified": "", "endpoint": "x"}])
    r = client.post("/api/models/settle", json={"default_a": "llama-ai-eng", "default_b": None})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["restart_required"] is False
    assert body["deprecated"] is True
    assert r.headers["Deprecation"] == "true"
    assert 'rel="successor-version"' in r.headers["Link"]
    assert "/api/models/pin" in r.headers["Link"]

    written = (tmp_path / "model_defaults.yaml").read_text(encoding="utf-8")
    # v2 file: settle maps to pins; an omitted B is the explicit "none" pin
    # (replaces the v1 `default_a:` / `default_b: null` assertions).
    import yaml
    data = yaml.safe_load(written)
    assert data["pins"] == {"a": "llama-ai-eng", "b": "none"}


def test_settle_writes_file_and_updates_registry_immediately(client, monkeypatch, tmp_path):
    _mock_ollama_installed(monkeypatch, [
        {"id": "llama-ai-eng", "runtime": "ollama", "size_gb": 0.9,
         "modified": "", "endpoint": "x"}])
    (tmp_path / "models" / "Qwen2.5-7B-Instruct-4bit").mkdir()

    r = client.post("/api/models/settle", json={
        "default_a": "llama-ai-eng", "default_b": "Qwen2.5-7B-Instruct-4bit"})
    assert r.status_code == 200
    body = r.json()

    entries = {e["id"]: e for e in body["state"]["entries"]}
    assert entries["tier0-local"]["model_id"] == "llama-ai-eng"
    assert entries["tier1-aerollm"]["model_id"] == "Qwen2.5-7B-Instruct-4bit"

    # Immediately re-fetching /api/models/boot must see the settlement —
    # no request-scoped staleness from the boot endpoint's candidate cache.
    r2 = client.get("/api/models/boot")
    assert r2.json()["mode"] == "hidden"
    assert r2.json()["resolution"]["b"]["model"] == "Qwen2.5-7B-Instruct-4bit"
    assert r2.json()["resolution"]["b"]["source"] == "pin"


def test_settle_short_ollama_tag_matches_full_installed_tag(client, monkeypatch):
    """':latest'-style tag suffixes must not cause a false 'not installed'
    refusal — same normalization _ollama_installed_models callers use
    elsewhere in the codebase."""
    _mock_ollama_installed(monkeypatch, [
        {"id": "llama-ai-eng:latest", "runtime": "ollama", "size_gb": 0.9,
         "modified": "", "endpoint": "x"}])
    r = client.post("/api/models/settle", json={"default_a": "llama-ai-eng"})
    assert r.status_code == 200


def test_settle_emits_activity_event(client, monkeypatch):
    _mock_ollama_installed(monkeypatch, [
        {"id": "llama-ai-eng", "runtime": "ollama", "size_gb": 0.9,
         "modified": "", "endpoint": "x"}])
    from arail.activity import activity_log
    events = []
    monkeypatch.setattr(activity_log, "emit",
                        lambda *a, **kw: events.append((a, kw)) or {})
    r = client.post("/api/models/settle", json={"default_a": "llama-ai-eng"})
    assert r.status_code == 200
    assert any("settled" in str(a) for a, kw in events)


# ---------------------------------------------------------------------------
# restart_required — never silently unload a resident deep model
# ---------------------------------------------------------------------------

def test_restart_required_true_when_a_different_deep_model_is_resident(client, monkeypatch, tmp_path):
    from arail import hardware as hw
    monkeypatch.setattr(hw, "secondary_model_cap_b", lambda *a, **kw: 32.0)
    _mock_ollama_installed(monkeypatch, [
        {"id": "llama-ai-eng", "runtime": "ollama", "size_gb": 0.9,
         "modified": "", "endpoint": "x"}])
    (tmp_path / "models" / "New-Deep-Model-7B-4bit").mkdir()

    class _FakeInst:
        _runtime = object()

    from arail.router.backends import AeroLLMBackend
    monkeypatch.setattr(
        AeroLLMBackend, "_shared",
        {f"{tmp_path / 'models'}::Old-Resident-Model-4bit": _FakeInst()},
        raising=False)

    r = client.post("/api/models/settle", json={
        "default_a": "llama-ai-eng", "default_b": "New-Deep-Model-7B-4bit"})
    assert r.status_code == 200
    assert r.json()["restart_required"] is True


def test_restart_not_required_when_settling_the_already_resident_model(client, monkeypatch, tmp_path):
    from arail import hardware as hw
    monkeypatch.setattr(hw, "secondary_model_cap_b", lambda *a, **kw: 32.0)
    _mock_ollama_installed(monkeypatch, [
        {"id": "llama-ai-eng", "runtime": "ollama", "size_gb": 0.9,
         "modified": "", "endpoint": "x"}])
    models_dir = tmp_path / "models"
    (models_dir / "Same-Model-7B-4bit").mkdir()

    class _FakeInst:
        _runtime = object()

    from arail.router.backends import AeroLLMBackend
    monkeypatch.setattr(
        AeroLLMBackend, "_shared",
        {f"{models_dir}::Same-Model-7B-4bit": _FakeInst()},
        raising=False)

    r = client.post("/api/models/settle", json={
        "default_a": "llama-ai-eng", "default_b": "Same-Model-7B-4bit"})
    assert r.status_code == 200
    assert r.json()["restart_required"] is False
