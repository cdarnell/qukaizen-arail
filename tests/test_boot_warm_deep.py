"""Deep-slot boot warm, crash sentinel, boot_warm gate, /api/instance fields.

sprints/2026-10-20-boot-model-autoresolve steps 6 (FM5, FM6, FM17).
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time

import pytest

from arail.agents import deep_policy
from arail.portal import model_warmth as mw


@pytest.fixture
def sentinel(monkeypatch, tmp_path):
    p = tmp_path / ".aerollm_boot_warm.inflight"
    monkeypatch.setattr(mw, "sentinel_path", lambda: p)
    return p


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _write(p, model, pid, started_at=None, **extra):
    body = {"model": model, "pid": pid,
            "started_at": time.time() if started_at is None else started_at}
    body.update(extra)
    p.write_text(json.dumps(body))


# ── gate ───────────────────────────────────────────────────────────

def _gate_env(monkeypatch, *, maximus=True, wheel=True, pressure=0.1):
    monkeypatch.setattr("arail.tier.is_maximus", lambda: maximus)
    monkeypatch.setattr(deep_policy, "_aerollm_importable", lambda: wheel)
    monkeypatch.setattr("arail.router.mlx_guard.metal_memory_pressure", lambda: pressure)


def test_boot_gate_passes_even_when_operator_present_and_jobs_halted(monkeypatch):
    _gate_env(monkeypatch)
    monkeypatch.setattr(deep_policy, "_background_gate",
                        lambda: (False, "deferred_now", "operator present"))
    assert deep_policy.boot_warm_safe() is True       # background_safe() would be False


@pytest.mark.parametrize("kw,code", [({"maximus": False}, "tier_locked"),
                                     ({"wheel": False}, "wheel_missing"),
                                     ({"pressure": 0.9}, "deferred_now")])
def test_boot_gate_refusals(monkeypatch, kw, code):
    _gate_env(monkeypatch, **kw)
    ok, got, _ = deep_policy.boot_warm_gate()
    assert (ok, got) == (False, code)


# ── sentinel ───────────────────────────────────────────────────────

def test_stale_sentinel_with_dead_pid_marks_model_crashed(sentinel):  # FM5
    _write(sentinel, "Bad-7B", _dead_pid())
    assert mw.crashed_model() == "Bad-7B"
    assert sentinel.exists()      # kept: the skip persists across boots


def test_live_pid_or_own_pid_is_not_a_crash(sentinel):
    _write(sentinel, "M", os.getpid())
    assert mw.crashed_model() is None
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        _write(sentinel, "M", live.pid)
        assert mw.crashed_model() is None
        assert mw._loading_elsewhere() is True
    finally:
        live.kill()
        live.wait()


def test_live_but_old_pid_is_a_crash_not_a_wedge(sentinel):  # review B2 (PID reuse)
    old = time.time() - mw.SENTINEL_MAX_AGE_S - 60
    _write(sentinel, "Bad-7B", 1, started_at=old)        # pid 1 is always alive
    assert mw.crashed_model() == "Bad-7B"
    assert mw._loading_elsewhere() is False


def test_sentinel_from_a_previous_boot_is_a_crash(sentinel, monkeypatch):  # review B2
    monkeypatch.setattr(mw, "_boot_id", lambda: "boot-2")
    _write(sentinel, "Bad-7B", 1, boot_id="boot-1")      # fresh and pid alive
    assert mw.crashed_model() == "Bad-7B"
    assert mw._loading_elsewhere() is False


def test_same_boot_fresh_live_pid_still_defers(sentinel, monkeypatch):
    monkeypatch.setattr(mw, "_boot_id", lambda: "boot-1")
    _write(sentinel, "M", 1, boot_id="boot-1")
    assert mw.crashed_model() is None and mw._loading_elsewhere() is True


def test_sentinel_without_started_at_is_stale(sentinel):
    sentinel.write_text(json.dumps({"model": "M", "pid": 1}))
    assert mw.crashed_model() == "M"


def test_missing_or_garbage_sentinel_is_ignored(sentinel):
    assert mw.crashed_model() is None
    sentinel.write_text("{not json")
    assert mw.crashed_model() is None


# ── boot_warm_deep ─────────────────────────────────────────────────

@pytest.fixture
def deep(monkeypatch, sentinel):
    monkeypatch.setattr(deep_policy, "boot_warm_gate", lambda: (True, "ok", ""))
    monkeypatch.setattr(mw, "_tier1_resident", lambda: False)
    monkeypatch.setattr(mw, "_enabled", lambda: True)
    state = {"router": object(), "seen_sentinel": None}

    def get_router():
        state["seen_sentinel"] = sentinel.exists()
        r = state["router"]
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(deep_policy, "get_deep_router", get_router)
    monkeypatch.setattr("arail.registry.health.probe_entry", lambda e: e.health)
    return state


def test_success_writes_sentinel_during_load_and_deletes_it_after(deep, sentinel):
    assert asyncio.run(mw.boot_warm_deep("Good-7B")) == "ok"
    assert deep["seen_sentinel"] is True
    assert not sentinel.exists()


def test_router_none_is_failed_and_sentinel_removed(deep, sentinel):  # FM6
    deep["router"] = None
    assert asyncio.run(mw.boot_warm_deep("Good-7B")) == "failed"
    assert not sentinel.exists()


def test_python_exception_is_failed_and_sentinel_removed(deep, sentinel):  # FM6
    deep["router"] = RuntimeError("load blew up")
    assert asyncio.run(mw.boot_warm_deep("Good-7B")) == "failed"
    assert not sentinel.exists()


def test_other_live_instance_defers_without_loading(deep, sentinel):  # FM17
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        _write(sentinel, "Other", live.pid)
        assert asyncio.run(mw.boot_warm_deep("Good-7B")) == "deferred"
        assert deep["seen_sentinel"] is None
    finally:
        live.kill()
        live.wait()


def test_gate_refusal_statuses(deep, monkeypatch):
    monkeypatch.setattr(deep_policy, "boot_warm_gate",
                        lambda: (False, "deferred_now", "pressure"))
    assert asyncio.run(mw.boot_warm_deep("M")) == "deferred"
    monkeypatch.setattr(deep_policy, "boot_warm_gate",
                        lambda: (False, "tier_locked", "x"))
    assert asyncio.run(mw.boot_warm_deep("M")) == "skipped"


def test_no_model_and_opt_out(deep, monkeypatch):
    assert asyncio.run(mw.boot_warm_deep(None)) == "skipped"
    monkeypatch.setattr(mw, "_enabled", lambda: False)
    assert asyncio.run(mw.boot_warm_deep("M")) == "disabled"


# ── portal wiring ──────────────────────────────────────────────────

def test_boot_models_skips_crashed_model_and_reports_it(sentinel, monkeypatch, tmp_path):  # FM5
    import arail.portal.app as app_mod
    from arail import model_resolve as mr
    monkeypatch.setenv("ARAIL_MODEL_DEFAULTS_FILE", str(tmp_path / "md.yaml"))
    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE", str(tmp_path / "reg.json"))
    from arail.registry import core as reg_core
    reg_core.reset_registry()
    (tmp_path / "models" / "Bad-7B").mkdir(parents=True)
    monkeypatch.setenv("ARAIL_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setattr(mr, "_deep_slot_available", lambda: None)
    monkeypatch.setattr(mr, "_scan_models_dir", lambda: [{"id": "Bad-7B", "size_gb": 4.0}])
    monkeypatch.setattr(mr, "_total_ram_gb", lambda: 32.0)
    monkeypatch.setattr(mr, "_secondary_cap_b", lambda: 14.0)
    monkeypatch.setattr(mr, "_fetch_ollama_tags", lambda: [])
    _write(sentinel, "Bad-7B", _dead_pid())
    monkeypatch.setenv("ARAIL_BOOT_WARM", "1")
    monkeypatch.setattr(mw, "boot_warm_deep",
                        lambda model: asyncio.sleep(0, result="skipped"))
    asyncio.run(app_mod._boot_models())
    last = mr.last_resolution()
    assert last["b"]["model"] is None
    assert any(d["kind"] == "crashed_last_boot" for d in last["degraded"])
    reg_core.reset_registry()


def test_api_instance_exposes_warm_a_and_warm_b(monkeypatch):
    import arail.portal.app as app_mod
    from fastapi.testclient import TestClient
    with TestClient(app_mod.app) as client:
        monkeypatch.setitem(app_mod._BOOT_WARM, "a", "ok")
        monkeypatch.setitem(app_mod._BOOT_WARM, "b", "deferred")
        body = client.get("/api/instance").json()
    assert body["warm_a"] == "ok" and body["warm_b"] == "deferred"
    assert {"warm", "warm_ms", "warm_skipped", "backend"} <= set(body)


def test_deferred_boot_warm_is_visible_in_the_degraded_strip(monkeypatch):  # review B2
    import arail.portal.app as app_mod
    from arail import model_resolve as mr
    mr.last_resolution().clear()
    mr.last_resolution().update({"degraded": []})
    monkeypatch.setattr(mw, "boot_warm_deep",
                        lambda model: asyncio.sleep(0, result="deferred"))
    asyncio.run(app_mod._warm_slot_b("Some-7B"))
    kinds = [d["kind"] for d in mr.last_resolution()["degraded"]]
    assert "warm_deferred" in kinds
    mr.last_resolution().clear()
