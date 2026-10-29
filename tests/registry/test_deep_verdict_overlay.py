"""The tier-1 registry entry cannot say "healthy" while deep generation is failing.

C5 and the boot/preload lines of C8 in sprints/2026-10-29-arail-deep-honest/
ARCHITECTURE.md. Tests H1 to H12 are named for the architecture's list. H2 is
the regression this sprint exists for: the Models "Check" button used to erase
the one real failure record, because a residency probe says "healthy" for any
runtime whose weights are in memory.
"""
from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient

from arail import deep_verdict as dv
from arail.registry import core as reg_core
from arail.registry import health
from arail.registry.store import TIER1_ID
from tests.fakes.fake_deep_runtime import fake_deep  # noqa: F401  (fixture)
from tests.test_deep_verdict import FAIL_ERR, probe_result


@pytest.fixture
def env(tmp_registry, fake_deep):
    """A maximus registry (tier 1 enabled) over the fake runtime, model dir present."""
    fd = fake_deep("pass")
    tier1 = tmp_registry.entries[TIER1_ID]
    tier1.model_id = fd.model_name                 # a model dir that exists under ARAIL_MODELS_DIR
    assert tier1.enabled
    b = dv.identity()
    return type("Env", (), {"reg": tmp_registry, "fd": fd, "tier1": tier1, "b": b})


def fail(env, error="RuntimeError: rms_norm has 1 elements"):
    dv.note_call(False, error=error, consumer="agent:buddy")


def tier1_state(reg):
    return next(e for e in reg.to_state()["entries"] if e["id"] == TIER1_ID)


def events(monkeypatch):
    got = []
    from arail import activity
    monkeypatch.setattr(activity.activity_log, "emit", lambda *a, **k: got.append(a))
    return got


# ── H1: the probe says unhealthy while failing, resident or not ────────────

def test_h1_the_probe_says_unhealthy_while_failing_cold_or_resident(env):
    cold = health._probe_aerollm(env.tier1)
    assert cold.status == "cold", "no failure recorded: today's answer"
    fail(env)
    st = health._probe_aerollm(env.tier1)
    assert st.status == "unhealthy" and st.detail.startswith(dv.HEALTH_PREFIX)
    assert f"sha256 {env.b.sha256[:12]}" in st.detail
    env.fd.backend()                                   # weights resident now
    assert any(getattr(i, "_runtime", None) is not None
               for i in __import__("arail.router.backends", fromlist=["x"]).AeroLLMBackend._shared.values())
    st = health._probe_aerollm(env.tier1)
    assert st.status == "unhealthy", "resident weights do not make a failing runtime healthy"


# ── H2: Check stays unhealthy (the erase regression) ───────────────────────

def test_h2_check_stays_unhealthy(env, monkeypatch):
    env.fd.backend()
    fail(env)
    reg_core.get_registry().report_failure(TIER1_ID, RuntimeError("rms_norm"))   # the old, erasable record
    import arail.portal.app as app_mod
    c = TestClient(app_mod.app)           # no lifespan: the boot resolver would re-seed the registry
    before = next(e for e in c.get("/api/models/state").json()["entries"] if e["id"] == TIER1_ID)
    assert before["health"]["status"] == "unhealthy"
    r = c.post("/api/models/health/refresh")                          # the Check button
    assert r.status_code == 200
    after_post = next(e for e in r.json()["entries"] if e["id"] == TIER1_ID)
    after_get = next(e for e in c.get("/api/models/state").json()["entries"] if e["id"] == TIER1_ID)
    for e in (after_post, after_get):
        assert e["health"]["status"] == "unhealthy", "Check must never erase a recorded generation failure"
        assert e["health"]["detail"].startswith(dv.HEALTH_PREFIX)
        assert "only ./arailctl deep probe can clear it" in e["health"]["detail"]


def test_h2_check_never_generates(env):
    env.fd.backend()
    fail(env)
    n = len(env.fd.gen_calls())
    health.run_preflight(env.reg, announce=False)
    health.run_preflight(env.reg, announce=True)
    assert len(env.fd.gen_calls()) == n


# ── H3: report_success cannot show healthy, and does not announce recovery ─

def test_h3_report_success_cannot_show_healthy(env, monkeypatch):
    got = events(monkeypatch)
    fail(env)
    env.reg.report_failure(TIER1_ID, RuntimeError("rms_norm"))
    env.reg.report_success(TIER1_ID, latency_ms=5.0)
    assert env.tier1.health.status == "healthy", "the stored value is a stored write"
    assert tier1_state(env.reg)["health"]["status"] == "unhealthy", "but every read overlays it"
    assert not [a for a in got if "recovered" in a[1]], "no false 'recovered' line while failing"


def test_h3_report_success_announces_recovery_when_nothing_is_failing(env, monkeypatch):
    got = events(monkeypatch)
    env.reg.report_failure(TIER1_ID, RuntimeError("one-off in some other stack"))
    env.reg.report_success(TIER1_ID)
    assert [a for a in got if "recovered" in a[1]], "today's behaviour is unchanged when the verdict is clean"


# ── H4: the warms and the loop re-probe through the same overlay ───────────

def test_h4_preload_and_boot_warm_reprobes_cannot_erase_it(env):
    fail(env)
    env.tier1.health = reg_core.HealthState(status="healthy", checked_at=1.0, detail="runtime resident (warm)")
    env.tier1.health = health.probe_entry(env.tier1)           # what _preload_once and boot_warm_deep do
    assert env.tier1.health.status == "unhealthy"
    env.tier1.health = reg_core.HealthState(status="healthy", checked_at=1.0)
    env.tier1.health = health.probe_entry(env.tier1)
    assert env.tier1.health.detail.startswith(dv.HEALTH_PREFIX)


# ── H5: not failing -> today's statuses ────────────────────────────────────

def test_h5_untested_and_passing_give_todays_statuses(env):
    assert health._probe_aerollm(env.tier1).status == "cold"
    dv.record_probe(probe_result(env.b, "pass", passed=5, started_ts=dv._now()))
    assert health._probe_aerollm(env.tier1).status == "cold"
    env.fd.backend()
    st = health._probe_aerollm(env.tier1)
    assert (st.status, st.detail) == ("healthy", "runtime resident (warm)")


def test_h5_a_missing_model_dir_is_still_not_installed(env, monkeypatch):
    env.tier1.model_id = "no-such-model"
    assert health._probe_aerollm(env.tier1).status == "not_installed"


# ── H6: recheck_unusable ───────────────────────────────────────────────────

def test_h6_recheck_unusable_keeps_it_down_and_stays_quiet(env, monkeypatch):
    got = events(monkeypatch)
    fail(env)
    env.reg.report_failure(TIER1_ID, RuntimeError("rms_norm"))
    env.tier1.health = reg_core.replace(env.tier1.health, checked_at=1.0)
    env.reg.bind("reasoning", TIER1_ID)
    assert env.reg.recheck_unusable("reasoning", min_age_s=0) is True
    assert tier1_state(env.reg)["health"]["status"] == "unhealthy"
    assert not [a for a in got if "recovered" in a[1]]


def test_h6_after_a_passing_probe_recheck_recovers(env, monkeypatch):
    got = events(monkeypatch)
    fail(env)
    env.reg.report_failure(TIER1_ID, RuntimeError("rms_norm"))
    env.reg.bind("reasoning", TIER1_ID)
    dv.record_probe(probe_result(env.b, "pass", passed=5, started_ts=dv._now() + 1))
    env.tier1.health = reg_core.replace(env.tier1.health, checked_at=1.0)
    assert env.reg.recheck_unusable("reasoning", min_age_s=0) is True
    assert tier1_state(env.reg)["health"]["status"] == "cold"
    assert [a for a in got if "recovered" in a[1]]


# ── H8: a fresh registry that never re-probed already says unhealthy ───────

def test_h8_a_fresh_registry_reads_unhealthy_with_no_probe_ever_run(env):
    fail(env)
    reg_core.reset_registry()
    fresh = reg_core.get_registry()
    fresh._ensure_loaded()
    assert fresh.entries[TIER1_ID].health.status == "unknown", "no probe has run in this registry"
    st = tier1_state(fresh)
    assert st["health"]["status"] == "unhealthy" and st["health"]["detail"].startswith(dv.HEALTH_PREFIX)


def test_h8_resolve_falls_back_and_says_why(env):
    fail(env)
    env.reg.bind("reasoning", TIER1_ID)
    res = env.reg.resolve("reasoning")
    assert res.entry is not None and res.entry.id != TIER1_ID, "reasoning falls back to the fast model"
    assert res.fallback is not None and res.fallback.reason == "unhealthy"
    assert res.fallback.detail.startswith(dv.HEALTH_PREFIX)


# ── H9: registry reads never import the runtime ────────────────────────────

def test_h9_minimalist_registry_reads_never_import_aerollm_api(tmp_path, monkeypatch):
    boom = tmp_path / "boom"
    (boom / "aerollm_api").mkdir(parents=True)
    (boom / "aerollm_api" / "__init__.py").write_text("raise RuntimeError('a registry read imported the runtime')\n")
    for name in [n for n in sys.modules if n.split(".")[0] in ("aerollm_api", "queuellm_api")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.syspath_prepend(str(boom))
    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE", str(tmp_path / "model_registry.json"))
    monkeypatch.setenv("LAB_TIER", "minimalist")
    monkeypatch.setenv("MODEL_BACKEND", "ollama_native")
    monkeypatch.setenv("MODEL_NAME", "llama-ai-eng")
    reg_core.reset_registry()
    reg = reg_core.get_registry()
    reg.to_state()
    reg.resolve("reasoning")
    reg.peek("fast")
    health.run_preflight(reg, announce=False)
    assert "aerollm_api" not in sys.modules
    reg_core.reset_registry()


def test_h9_a_maximus_registry_read_uses_static_identity_and_never_imports(env, tmp_path, monkeypatch):
    # the fake is on the path but not yet imported by this process
    for name in [n for n in sys.modules if n.split(".")[0] in ("aerollm_api", "queuellm_api")]:
        monkeypatch.delitem(sys.modules, name)
    dv._reset_for_tests()
    reg_core.get_registry().to_state()
    assert "aerollm_api" not in sys.modules


# ── H10: the opt-in loop announces once per change ─────────────────────────

def test_h10_the_loop_announces_a_sticky_failure_once(env, monkeypatch):
    got = events(monkeypatch)
    fail(env)
    for _ in range(3):
        health.run_preflight(env.reg, announce=True)
    down = [a for a in got if "MODEL TIER DOWN" in a[1] and "QueueLLM" in a[1]]
    assert len(down) == 1, [a[1][:80] for a in got]
    assert "generation failing on this build" in down[0][1]

    dv.record_probe(probe_result(env.b, "pass", passed=5, started_ts=dv._now() + 1))
    health.run_preflight(env.reg, announce=True)
    assert [a for a in got if "Model tiers ready" in a[1]]
    got.clear()
    dv.note_call(False, error="another failure")          # the same binary fails again
    health.run_preflight(env.reg, announce=True)
    health.run_preflight(env.reg, announce=True)
    assert len([a for a in got if "MODEL TIER DOWN" in a[1] and "QueueLLM" in a[1]]) == 1, \
        "a later outage is announced again, once"


def test_h10_a_changed_detail_is_announced_again(env, monkeypatch):
    got = events(monkeypatch)
    fail(env, error="first error")
    health.run_preflight(env.reg, announce=True)
    dv.record_probe(probe_result(env.b, "fail", passed=1, started_ts=dv._now(), first_error=FAIL_ERR))
    health.run_preflight(env.reg, announce=True)
    assert len([a for a in got if "MODEL TIER DOWN" in a[1] and "QueueLLM" in a[1]]) == 2


# ── H11: a wheel-less or minimalist lab sees no change ─────────────────────

def test_h11_no_overlay_for_a_disabled_entry(env):
    fail(env)
    env.tier1.enabled = False
    assert reg_core._deep_overlay(env.tier1) is None
    assert tier1_state(env.reg)["health"]["status"] != "unhealthy" or env.tier1.health.status == "unhealthy"


def test_h11_no_overlay_for_a_non_aerollm_entry(env):
    fail(env)
    tier0 = env.reg.entries["tier0-local"]
    assert reg_core._deep_overlay(tier0) is None


def test_h11_a_wheel_less_lab_gets_todays_probe_and_the_corrected_hint(env, monkeypatch):
    import importlib.util as ilu
    monkeypatch.setattr(ilu, "find_spec", lambda name, *a, **k: None if name == "aerollm_api" else None)
    st = health._probe_aerollm(env.tier1)
    assert st.status == "unhealthy"
    assert st.detail == "aerollm_api wheel not installed (./arailctl deep install)"
    assert "deep rebuild" not in st.detail


# ── H12: after a passing probe the dot turns green with no Check ───────────

def test_h12_a_passing_probe_clears_the_overlay_on_the_next_read(env):
    fail(env)
    health.run_preflight(env.reg, announce=False)              # stores the overlay text
    assert env.tier1.health.detail.startswith(dv.HEALTH_PREFIX)
    assert tier1_state(env.reg)["health"]["status"] == "unhealthy"
    dv.record_probe(probe_result(env.b, "pass", passed=5, started_ts=dv._now() + 1))
    st = tier1_state(env.reg)                                  # no Check, no re-probe call by the test
    assert st["health"]["status"] == "cold" and not st["health"]["detail"].startswith(dv.HEALTH_PREFIX)
    assert env.reg.peek("reasoning") is not None


def test_h12_a_genuinely_unhealthy_entry_is_not_touched_by_the_refresh(env):
    env.reg.report_failure(TIER1_ID, RuntimeError("some other failure"))
    st = tier1_state(env.reg)
    assert st["health"]["status"] == "unhealthy" and "some other failure" in st["health"]["detail"]
