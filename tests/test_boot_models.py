"""Boot model resolution wiring — sprints/2026-10-20-boot-model-autoresolve.

Replaces tests/test_registry_env_export.py (the registry->env export it
covered, ``_export_registry_env``, is deleted: resolution now flows
resolve -> env + registry, one direction).
"""
from __future__ import annotations

import asyncio
import os

import pytest

from arail import model_defaults as md
from arail import model_resolve as mr


def _tag(name, gb, ps=""):
    return {"id": name, "size_gb": gb, "family": "llama", "parameter_size": ps}


@pytest.fixture
def booted(monkeypatch, tmp_path):
    """Isolated registry + yaml, stubbed machine facts."""
    from arail.registry import core as reg_core
    f = tmp_path / "model_defaults.yaml"
    monkeypatch.setenv("ARAIL_MODEL_DEFAULTS_FILE", str(f))
    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE", str(tmp_path / "reg.json"))
    monkeypatch.setenv("MODEL_BACKEND", "ollama_native")
    monkeypatch.setenv("MODEL_NAME", "ai-engineer:latest")
    monkeypatch.delenv("AEROLLM_MODEL", raising=False)
    monkeypatch.delenv("ARAIL_ENV_PREF_A", raising=False)
    monkeypatch.delenv("ARAIL_ENV_PREF_B", raising=False)
    monkeypatch.setattr(mr, "_fetch_ollama_tags",
                        lambda: [_tag("llama-ai-eng:latest", 1.3, "1.2B")])
    monkeypatch.setattr(mr, "_fetch_capabilities", lambda n: ["completion"])
    monkeypatch.setattr(mr, "_total_ram_gb", lambda: 32.0)
    monkeypatch.setattr(mr, "_secondary_cap_b", lambda: 14.0)
    monkeypatch.setattr(mr, "_scan_models_dir", lambda: [])
    monkeypatch.setattr(mr, "_deep_slot_available", lambda: "deep model needs maximus")
    reg_core.reset_registry()
    yield f
    reg_core.reset_registry()
    for k in ("MODEL_NAME", "AEROLLM_MODEL", "ARAIL_ENV_PREF_A", "ARAIL_ENV_PREF_B"):
        os.environ.pop(k, None)


def test_resolve_and_apply_writes_cache_stamps_env_and_registry(booted):
    out = mr.resolve_and_apply()
    assert out["a"]["model"] == "llama-ai-eng:latest" and out["saved"] is True
    # .env's ai-engineer was a valid preference but is not installed -> falls through
    assert out["a"]["source"] == "auto"
    assert os.environ["MODEL_NAME"] == "llama-ai-eng:latest"
    data = md.read(booted)
    assert data["resolved"]["a"]["model"] == "llama-ai-eng:latest"
    assert data["pins"] == {"a": None, "b": None}
    from arail.registry import get_registry
    reg = get_registry()
    assert reg.entries["tier0-local"].model_id == "llama-ai-eng:latest"
    assert mr.last_resolution()["a"]["model"] == "llama-ai-eng:latest"


def test_env_preference_from_dotenv_is_honoured_when_installed(booted, monkeypatch):
    monkeypatch.setattr(mr, "_fetch_ollama_tags", lambda: [
        _tag("llama-ai-eng:latest", 1.3, "1.2B"), _tag("ai-engineer:latest", 4.7, "7B")])
    out = mr.resolve_and_apply()
    assert (out["a"]["model"], out["a"]["source"]) == ("ai-engineer:latest", "env")


def test_pins_are_never_rewritten_by_boot(booted):
    md.write_pin("b", "none", path=booted)
    mr.resolve_and_apply()
    mr.resolve_and_apply()
    assert md.read(booted)["pins"]["b"] == "none"


def test_v1_file_migrates_once_and_keeps_b_off(booted):  # F2 / FM11
    booted.write_text("default_a: llama-ai-eng:latest\ndefault_b: null\n")
    out = mr.resolve_and_apply()
    assert out["migrated"] is True
    assert md.read(booted)["pins"] == {"a": "llama-ai-eng:latest", "b": "none"}
    assert out["b"]["model"] is None and out["b"]["source"] == "pin"
    assert "AEROLLM_MODEL" not in os.environ
    from arail.registry import get_registry
    t1 = get_registry().entries["tier1-aerollm"]
    assert t1.enabled is False and t1.model_id == ""


def test_ollama_down_boot_completes_and_keeps_cache(booted, monkeypatch):  # FM1
    mr.resolve_and_apply()
    monkeypatch.setattr(mr, "_fetch_ollama_tags", lambda: None)
    out = mr.resolve_and_apply()
    assert out["a"]["model"] == "llama-ai-eng:latest" and out["a"]["source"] == "cache"
    assert any(d["kind"] == "ollama_down" for d in out["degraded"])


def test_unwritable_yaml_still_resolves(booted, monkeypatch):  # FM10
    def boom(*a, **k):
        raise OSError("ro")
    monkeypatch.setattr(os, "replace", boom)
    out = mr.resolve_and_apply()
    assert out["saved"] is False and out["a"]["model"] == "llama-ai-eng:latest"


# ── portal wiring ──────────────────────────────────────────────────

class _FakeRouter:
    backend_name = "ollama_native"

    def __init__(self):
        self.calls = []

    def complete(self, prompt, max_tokens=512, *a, **k):
        self.calls.append((prompt, max_tokens))


def test_boot_models_warms_a_by_default_without_autochecks(booted, monkeypatch):
    import arail.portal.app as app_mod
    monkeypatch.delenv("ARAIL_AUTOCHECKS", raising=False)
    monkeypatch.setenv("ARAIL_BOOT_WARM", "1")
    fake = _FakeRouter()
    monkeypatch.setattr(app_mod, "_get_primary_router", lambda: fake)
    app_mod._MODEL_WARM = False
    asyncio.run(app_mod._boot_models())
    assert fake.calls == [("ok", 1)]
    assert app_mod._MODEL_WARM is True
    assert md.read(booted)["resolved"]["warm"]["a"] == "ok"


def test_boot_warm_opt_out_flips_ready_and_never_builds_a_router(booted, monkeypatch):
    import arail.portal.app as app_mod
    monkeypatch.setenv("ARAIL_BOOT_WARM", "0")
    monkeypatch.setattr(app_mod, "_get_primary_router",
                        lambda: pytest.fail("router built on opt-out"))
    app_mod._MODEL_WARM = False
    asyncio.run(app_mod._boot_models())
    assert app_mod._MODEL_WARM is True
    assert app_mod._BOOT_WARM == {"a": "disabled", "b": "disabled"}


def test_boot_models_with_no_installed_model_skips_warm_but_flips_ready(booted, monkeypatch):  # FM2
    import arail.portal.app as app_mod
    monkeypatch.setenv("ARAIL_BOOT_WARM", "1")
    monkeypatch.setattr(mr, "_fetch_ollama_tags", lambda: [])
    monkeypatch.setattr(app_mod, "_get_primary_router",
                        lambda: pytest.fail("warm attempted without a model"))
    app_mod._MODEL_WARM = False
    asyncio.run(app_mod._boot_models())
    assert app_mod._MODEL_WARM is True and app_mod._BOOT_WARM["a"] == "skipped"


def test_boot_models_never_raises_when_resolver_explodes(booted, monkeypatch):
    import arail.portal.app as app_mod
    monkeypatch.setenv("ARAIL_BOOT_WARM", "0")

    def boom(**k):
        raise RuntimeError("boom")
    monkeypatch.setattr(mr, "resolve_and_apply", boom)
    app_mod._MODEL_WARM = False
    asyncio.run(app_mod._boot_models())
    assert app_mod._MODEL_WARM is True


def test_export_registry_env_is_gone():
    import arail.portal.app as app_mod
    assert not hasattr(app_mod, "_export_registry_env")


def test_portal_starts_ready_with_ollama_down(booted, monkeypatch):  # integration, FM1
    monkeypatch.setattr(mr, "_fetch_ollama_tags", lambda: None)
    import arail.portal.app as app_mod
    from fastapi.testclient import TestClient
    with TestClient(app_mod.app) as client:
        assert client.get("/api/ready").json()["ready"] is True


# ── round 2: review BLOCK 1 / ASK 2 ────────────────────────────────

_MANGLED = ("version: 2\npins:\n  a: llama-ai-eng\n  b: none\n"
            "resolved: [unclosed\n")


def test_corrupt_yaml_is_never_overwritten_by_boot(booted):  # review B1
    booted.write_text(_MANGLED)
    out = mr.resolve_and_apply()
    assert booted.read_text() == _MANGLED          # bytes untouched, pins survive
    assert out["saved"] is False and out["file_ok"] is False
    note = [d for d in out["degraded"] if d["kind"] == "cache_unsaved"][0]
    assert "unreadable" in note["detail"] and "left untouched" in note["detail"]
    assert "couldn't save" not in note["detail"]


def test_unwritable_but_readable_yaml_keeps_the_save_wording(booted, monkeypatch):
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("ro")))
    out = mr.resolve_and_apply()
    note = [d for d in out["degraded"] if d["kind"] == "cache_unsaved"][0]
    assert "couldn't save" in note["detail"]


def _with_deep_b(monkeypatch, tmp_path):
    (tmp_path / "models" / "Good-7B").mkdir(parents=True)
    monkeypatch.setenv("ARAIL_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setattr(mr, "_deep_slot_available", lambda: None)
    monkeypatch.setattr(mr, "_scan_models_dir", lambda: [{"id": "Good-7B", "size_gb": 4.0}])


def test_reresolve_keeps_warm_of_slot_whose_model_is_unchanged(booted, monkeypatch, tmp_path):  # review A2
    _with_deep_b(monkeypatch, tmp_path)
    assert mr.resolve_and_apply()["b"]["model"] == "Good-7B"
    mr.last_resolution()["warm"] = {"a": "ok", "b": "ok"}
    out = mr.resolve_and_apply()
    assert out["warm"] == {"a": "ok", "b": "ok"}
    assert md.read(booted)["resolved"]["warm"] == {"a": "ok", "b": "ok"}


def test_reresolve_drops_warm_of_slot_whose_model_changed(booted, monkeypatch, tmp_path):
    _with_deep_b(monkeypatch, tmp_path)
    mr.resolve_and_apply()
    mr.last_resolution()["warm"] = {"a": "ok", "b": "ok"}
    monkeypatch.setattr(mr, "_fetch_ollama_tags", lambda: [
        _tag("llama-ai-eng:latest", 1.3, "1.2B"), _tag("other:1b", 1.0, "1B")])
    md.write_pin("a", "other:1b", path=booted)
    out = mr.resolve_and_apply()
    assert out["a"]["model"] == "other:1b"
    assert "a" not in out["warm"] and out["warm"]["b"] == "ok"


# ── round 3: QA-1 (MLX primary backend must keep its own MODEL_NAME) ──

@pytest.mark.parametrize("backend", ["mlx", "cuda", "cpu", None])
def test_non_ollama_primary_backend_keeps_its_model_name(booted, monkeypatch, backend):  # QA-1
    if backend is None:
        monkeypatch.delenv("MODEL_BACKEND", raising=False)
    else:
        monkeypatch.setenv("MODEL_BACKEND", backend)
    monkeypatch.setenv("MODEL_NAME", "mlx-community/Qwen3-8B-4bit")
    out = mr.resolve_and_apply()
    assert os.environ["MODEL_NAME"] == "mlx-community/Qwen3-8B-4bit"
    # .env's MLX repo id is not an Ollama preference: no bogus "isn't installed" note
    assert not [d for d in out["degraded"] if d["slot"] == "a"
                and "mlx-community" in (d.get("detail") or "")]
    from arail.registry import get_registry
    t0 = get_registry().entries["tier0-local"]
    assert t0.model_id != "llama-ai-eng:latest"


def test_non_ollama_backend_still_stamps_slot_b_and_caches_a(booted, monkeypatch, tmp_path):
    monkeypatch.setenv("MODEL_BACKEND", "mlx")
    monkeypatch.setenv("MODEL_NAME", "mlx-community/Qwen3-8B-4bit")
    (tmp_path / "models" / "Good-7B").mkdir(parents=True)
    monkeypatch.setenv("ARAIL_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setattr(mr, "_deep_slot_available", lambda: None)
    monkeypatch.setattr(mr, "_scan_models_dir", lambda: [{"id": "Good-7B", "size_gb": 4.0}])
    mr.resolve_and_apply()
    assert os.environ["AEROLLM_MODEL"] == "Good-7B"
    assert md.read(booted)["resolved"]["a"]["model"] == "llama-ai-eng:latest"


def test_apply_from_cache_does_not_stamp_a_under_mlx(booted, monkeypatch):
    mr.resolve_and_apply()                     # cache now names llama-ai-eng
    monkeypatch.setenv("MODEL_BACKEND", "mlx")
    monkeypatch.setenv("MODEL_NAME", "mlx-community/Qwen3-8B-4bit")
    md.apply(booted)
    assert os.environ["MODEL_NAME"] == "mlx-community/Qwen3-8B-4bit"
