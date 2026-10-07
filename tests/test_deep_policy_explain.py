"""deep_policy.explain() — reason codes + parity with prefer_deep().

prefer_deep() is now a boolean projection of explain(); this matrix pins
both the per-gate reason codes and the projection so the truth strip's
explanation can never drift from what the agents actually decide.
"""

from __future__ import annotations

import importlib.util as _importlib_util

import pytest

from arail.agents import deep_policy


@pytest.fixture()
def gates(monkeypatch):
    """All gates open by default; each test closes one."""
    monkeypatch.delenv("ARAIL_AGENT_DEEP", raising=False)
    monkeypatch.setenv("LAB_TIER", "maximus")

    orig = _importlib_util.find_spec
    state = {"wheel": True}

    def fake_find_spec(name, *a, **k):
        if name == "aerollm_api":
            return object() if state["wheel"] else None
        return orig(name, *a, **k)

    # deep_policy._aerollm_importable does a real `import aerollm_api`;
    # patch the helper itself so the gate is deterministic.
    monkeypatch.setattr(deep_policy, "_aerollm_importable",
                        lambda: state["wheel"])
    monkeypatch.setattr(_importlib_util, "find_spec", fake_find_spec)

    from arail import runtime_profile, scheduler
    monkeypatch.setattr(scheduler, "jobs_halted", lambda: False)
    monkeypatch.setattr(scheduler, "current_window", lambda: "heavy")
    monkeypatch.setattr(runtime_profile, "resolve", lambda: ("overnight", "t"))
    monkeypatch.setattr(runtime_profile, "params",
                        lambda p: {"background_aerollm": True})
    from arail.router import mlx_guard
    monkeypatch.setattr(mlx_guard, "metal_memory_pressure", lambda: 0.10)
    return state, monkeypatch


def _check(foreground: bool, want_ok: bool, want_code: str):
    ok, code, detail = deep_policy.explain(foreground=foreground)
    assert (ok, code) == (want_ok, want_code), detail
    assert deep_policy.prefer_deep(foreground=foreground) is ok
    assert isinstance(detail, str) and detail


def test_all_gates_open(gates):
    _check(True, True, "ok")
    _check(False, True, "ok")


def test_kill_switch_is_disabled(gates):
    _state, mp = gates
    mp.setenv("ARAIL_AGENT_DEEP", "0")
    _check(True, False, "disabled")
    _check(False, False, "disabled")


def test_minimalist_is_tier_locked(gates):
    _state, mp = gates
    mp.setenv("LAB_TIER", "minimalist")
    _check(True, False, "tier_locked")


def test_missing_wheel_is_wheel_missing(gates):
    state, _mp = gates
    state["wheel"] = False
    _check(True, False, "wheel_missing")


def test_foreground_skips_background_gate(gates):
    _state, mp = gates
    from arail import scheduler
    mp.setattr(scheduler, "jobs_halted", lambda: True)
    _check(True, True, "ok")
    _check(False, False, "deferred_now")


@pytest.mark.parametrize("close_gate, expect_in_detail", [
    ("halted", "halted"),
    ("active_window", "active work window"),
    ("interactive", "operator present"),
    ("profile_param", "disables background QueueLLM"),
    ("pressure", "memory pressure"),
])
def test_each_background_gate_names_itself(gates, close_gate, expect_in_detail):
    _state, mp = gates
    from arail import runtime_profile, scheduler
    from arail.router import mlx_guard
    if close_gate == "halted":
        mp.setattr(scheduler, "jobs_halted", lambda: True)
    elif close_gate == "active_window":
        mp.setattr(scheduler, "current_window", lambda: "active")
    elif close_gate == "interactive":
        mp.setattr(runtime_profile, "resolve", lambda: ("interactive", "t"))
    elif close_gate == "profile_param":
        mp.setattr(runtime_profile, "params",
                   lambda p: {"background_aerollm": False})
    elif close_gate == "pressure":
        mp.setattr(mlx_guard, "metal_memory_pressure", lambda: 0.95)
    ok, code, detail = deep_policy.explain(foreground=False)
    assert not ok and code == "deferred_now"
    assert expect_in_detail in detail
    assert deep_policy.prefer_deep(foreground=False) is False
    assert deep_policy.background_safe() is False
