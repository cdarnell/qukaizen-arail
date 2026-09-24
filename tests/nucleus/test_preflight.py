"""arail.nucleus.preflight — memory plan, Buddy reserve, refusal contract,
capability rows (T-PRE-1..5, T-SETUP-3)."""

from __future__ import annotations

import json
import random
import shutil
from types import SimpleNamespace

import pytest

from arail.nucleus import preflight as pf


def _model(name, params_b, weight_gb, *, num_hidden_layers=32):
    weights_bytes = int(weight_gb * (1024 ** 3))
    return SimpleNamespace(name=name, params_est_b=params_b, weights_bytes=weights_bytes,
                           config={"num_hidden_layers": num_hidden_layers}, is_moe=False)


def _domain():
    return SimpleNamespace(name="kernel")


def _no_capability_probes():
    return {}


# ── _status ────────────────────────────────────────────────────────

def test_status_green_amber_red():
    assert pf._status(10, 100) == "green"
    assert pf._status(90, 100) == "amber"
    assert pf._status(150, 100) == "red"
    assert pf._status(10, 0) == "red"


# ── T-PRE-1: refusal names the teacher, needs > budget ───────────────

def test_teacher_refusal_names_model(monkeypatch):
    # No Buddy model configured in this test -> protected is empty, and
    # the oversized teacher itself is the (only, droppable) candidate.
    monkeypatch.setattr("arail.config.MODEL_NAME", "")
    monkeypatch.delenv("QUEUELLM_MODEL", raising=False)
    monkeypatch.delenv("AEROLLM_MODEL", raising=False)
    capacity = {"ram_gb": 24.0, "vram_gb": 18.0, "disk_gb": 500.0}
    buddy = pf.BuddyReserve(measured_gb=0.0, declared_gb=0.0)
    teacher = _model("Qwen3-235B-A22B-4bit", 235.0, 130.0)  # far too big, not streamable
    with pytest.raises(pf.PreflightRefusal) as exc_info:
        pf.run_preflight(_domain(), capacity=capacity, buddy=buddy,
                         memory_budget_gb=24, teacher_model=teacher,
                         runtime_streams=False, capability_probes=_no_capability_probes())
    refusal = exc_info.value
    assert refusal.phase == "A"
    assert "Qwen3-235B-A22B-4bit" in refusal.drop
    assert refusal.protected == []


# ── B6 (2026-09-23 review): `protected` is Buddy's RESOLVED model
# identity, not the literal string "Buddy" -- and the oversized model
# itself is never proposed as a drop candidate when it IS that model ──

def test_teacher_refusal_never_drops_buddys_own_deep_model(monkeypatch):
    monkeypatch.setattr("arail.config.MODEL_NAME", "")
    monkeypatch.setenv("AEROLLM_MODEL", "Qwen2.5-7B-Instruct-4bit")
    monkeypatch.delenv("QUEUELLM_MODEL", raising=False)
    capacity = {"ram_gb": 24.0, "vram_gb": 18.0, "disk_gb": 500.0}
    buddy = pf.BuddyReserve(measured_gb=0.0, declared_gb=0.0)
    # The judge alias `ai-engineer` resolves to this exact directory name
    # (models.ALIASES) -- the review's concrete collision scenario. The
    # teacher IS Buddy's deep model here, and it's the only candidate.
    teacher = _model("Qwen2.5-7B-Instruct-4bit", 7.0, 130.0)
    with pytest.raises(pf.PreflightRefusal) as exc_info:
        pf.run_preflight(_domain(), capacity=capacity, buddy=buddy,
                         memory_budget_gb=24, teacher_model=teacher,
                         runtime_streams=False, capability_probes=_no_capability_probes())
    refusal = exc_info.value
    assert refusal.drop == []  # no safe candidate -- the only one is protected
    assert "Qwen2.5-7B-Instruct-4bit" in refusal.protected


def test_phase_c_never_drops_buddys_model_even_when_largest(monkeypatch):
    monkeypatch.setattr("arail.config.MODEL_NAME", "")
    monkeypatch.setenv("AEROLLM_MODEL", "Qwen2.5-7B-Instruct-4bit")
    monkeypatch.delenv("QUEUELLM_MODEL", raising=False)
    capacity = {"ram_gb": 4.0, "vram_gb": 3.0, "disk_gb": 500.0}
    buddy = pf.BuddyReserve(measured_gb=0.0, declared_gb=0.0)
    student = _model("student", 1.0, 0.5)
    base = _model("base", 1.0, 0.5)
    # judge is both the largest Phase-C candidate AND Buddy's deep model.
    judge = _model("Qwen2.5-7B-Instruct-4bit", 7.0, 20.0)
    with pytest.raises(pf.PreflightRefusal) as exc_info:
        pf.run_preflight(_domain(), capacity=capacity, buddy=buddy,
                         memory_budget_gb=4, student_model=student, base_student_model=base,
                         judge_model=judge, capability_probes=_no_capability_probes())
    refusal = exc_info.value
    assert "Qwen2.5-7B-Instruct-4bit" not in refusal.drop
    assert refusal.drop and refusal.drop[0] in ("student", "base")


# ── R3 (2026-09-23 review round 2): B6's guard missed Buddy's model when
# it's configured by an ABSOLUTE PATH -- resolve_model() refuses any name
# containing "/" (its '/' ban exists so domain.yaml can never carry a
# path), so the protected key fell back to the raw path string, which
# never matches a candidate's content-derived identity. Real tmp model
# dirs, not SimpleNamespace doubles -- content identity is the whole
# point being tested here.

def _write_real_model_dir(path, *, num_parameters=None, weight_bytes=1024):
    path.mkdir(parents=True)
    config = {"num_parameters": num_parameters} if num_parameters else {}
    (path / "config.json").write_text(json.dumps(config))
    (path / "model.safetensors").write_bytes(b"\x00" * weight_bytes)
    return path


def test_buddy_protected_matches_bare_name_absolute_path_and_byte_identical_copy(tmp_path, monkeypatch):
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    buddy_dir = _write_real_model_dir(models_dir / "Qwen2.5-7B-Instruct-4bit",
                                      num_parameters=7_000_000_000, weight_bytes=5_000_000)
    _write_real_model_dir(models_dir / "student", num_parameters=1_000_000_000, weight_bytes=500_000)
    _write_real_model_dir(models_dir / "base", num_parameters=1_000_000_000, weight_bytes=500_000)

    monkeypatch.setattr("arail.config.MODELS_DIR", str(models_dir))
    from arail.nucleus.models import resolve_model

    student = resolve_model("student")
    base = resolve_model("base")
    # The judge alias `ai-engineer` resolves to the exact same directory
    # Buddy's deep model uses -- the review's concrete collision scenario.
    judge = resolve_model("ai-engineer")

    capacity = {"ram_gb": 4.0, "vram_gb": 3.0, "disk_gb": 500.0}
    buddy_reserve = pf.BuddyReserve(measured_gb=0.0, declared_gb=0.0)

    def _assert_buddy_never_dropped(buddy_env_value, label):
        monkeypatch.setattr("arail.config.MODEL_NAME", "")
        monkeypatch.setenv("AEROLLM_MODEL", buddy_env_value)
        monkeypatch.delenv("QUEUELLM_MODEL", raising=False)
        with pytest.raises(pf.PreflightRefusal) as exc_info:
            pf.run_preflight(_domain(), capacity=capacity, buddy=buddy_reserve,
                             memory_budget_gb=0.001, student_model=student, base_student_model=base,
                             judge_model=judge, capability_probes=_no_capability_probes())
        refusal = exc_info.value
        assert judge.name not in refusal.drop, label
        assert refusal.drop and refusal.drop[0] in ("student", "base"), label

    _assert_buddy_never_dropped("Qwen2.5-7B-Instruct-4bit", "bare name")
    _assert_buddy_never_dropped(str(buddy_dir), "absolute path")

    copy_dir = models_dir / "buddy-copy"
    shutil.copytree(buddy_dir, copy_dir)
    _assert_buddy_never_dropped("buddy-copy", "byte-identical copy under another name")


# ── T-PRE-2: streamed window chosen when runtime streams ─────────────

def test_streamed_window_chosen_when_runtime_streams():
    capacity = {"ram_gb": 24.0, "vram_gb": 18.0, "disk_gb": 500.0}
    buddy = pf.BuddyReserve(measured_gb=0.0, declared_gb=0.0)
    teacher = _model("Qwen3-30B-A3B", 30.0, 16.0)
    report = pf.run_preflight(_domain(), capacity=capacity, buddy=buddy,
                              memory_budget_gb=24, teacher_model=teacher,
                              runtime_streams=True, capability_probes=_no_capability_probes())
    row = next(r for r in report.rows if r.name == "Phase A/A2 (teacher)")
    assert "streamed window" in row.note
    assert row.status in ("green", "amber")


# ── T-PRE-3: property test — drop never includes a protected model ───
# Half the combos make the (single) candidate model Buddy's own deep
# model by name, so the "never drops the protected model" assertion is
# actually exercised, not vacuously true because "Buddy" never appears
# as a directory name (the original bug).

def test_drop_never_includes_protected_over_200_combos(monkeypatch):
    rng = random.Random(1234)
    for i in range(200):
        if i % 2 == 0:
            monkeypatch.setenv("AEROLLM_MODEL", "teacher-x")
        else:
            monkeypatch.delenv("AEROLLM_MODEL", raising=False)
        monkeypatch.setattr("arail.config.MODEL_NAME", "")
        monkeypatch.delenv("QUEUELLM_MODEL", raising=False)

        ram = rng.uniform(8, 64)
        capacity = {"ram_gb": ram, "vram_gb": ram * 0.75, "disk_gb": 500.0}
        buddy = pf.BuddyReserve(measured_gb=rng.uniform(0, 8), declared_gb=rng.uniform(0, 4))
        teacher_gb = rng.uniform(1, 300)
        teacher = _model("teacher-x", teacher_gb, teacher_gb)
        try:
            pf.run_preflight(_domain(), capacity=capacity, buddy=buddy,
                             teacher_model=teacher, runtime_streams=rng.choice([True, False]),
                             capability_probes=_no_capability_probes())
        except pf.PreflightRefusal as refusal:
            assert not (set(refusal.drop) & set(refusal.protected))
            if i % 2 == 0:
                assert "teacher-x" not in refusal.drop
                assert refusal.drop == []


# ── T-PRE-4: Buddy not running -> declared reserve used ──────────────

def test_buddy_not_running_uses_declared_reserve():
    capacity = {"ram_gb": 36.0, "vram_gb": 27.0, "disk_gb": 500.0}
    buddy = pf.BuddyReserve(measured_gb=0.0, declared_gb=5.0)
    report = pf.run_preflight(_domain(), capacity=capacity, buddy=buddy,
                              capability_probes=_no_capability_probes())
    assert report.buddy_reserve_gb == 5.0


# ── T-PRE-5: disk row (informational — real disk check lands with corpus/staging) ──

def test_capacity_includes_disk_field():
    cap = pf._capacity()
    assert "disk_gb" in cap


# ── T-SETUP-3: mlx_lm version range ───────────────────────────────────

def test_mlx_lm_version_probe_rejects_out_of_range(monkeypatch):
    import sys
    import types

    fake = types.ModuleType("mlx_lm")
    fake.__version__ = "0.40.0"
    monkeypatch.setitem(sys.modules, "mlx_lm", fake)
    row = pf._probe_mlx_lm_version()
    assert row.status == "red"
    assert "0.31" in row.note


def test_mlx_lm_version_probe_accepts_in_range(monkeypatch):
    import sys
    import types

    fake = types.ModuleType("mlx_lm")
    fake.__version__ = "0.31.3"
    monkeypatch.setitem(sys.modules, "mlx_lm", fake)
    row = pf._probe_mlx_lm_version()
    assert row.status == "green"


# ── Phase C: sequential max, not sum ──────────────────────────────────

def test_phase_c_is_max_not_sum():
    capacity = {"ram_gb": 36.0, "vram_gb": 27.0, "disk_gb": 500.0}
    buddy = pf.BuddyReserve()
    student = _model("student", 3.0, 2.0)
    base = _model("base", 3.0, 2.0)
    judge = _model("judge", 7.0, 4.5)
    report = pf.run_preflight(_domain(), capacity=capacity, buddy=buddy,
                              student_model=student, base_student_model=base,
                              judge_model=judge, capability_probes=_no_capability_probes())
    row = next(r for r in report.rows if r.name.startswith("Phase C"))
    assert "max(" in row.note
    # judge is the largest of the three -> its size (not the sum) drives the row.
    assert report.phase_requirements_gb["C"] < (2.0 + 2.0 + 4.5) * 1.10 + 1.0


# ── T-RES-1..3: residency classifier ─────────────────────────────────

def test_residency_rss_drop_violated():
    from arail.nucleus import residency

    samples = [
        residency.sample_once(portal_rss_bytes=1_000_000_000, ollama_models=[], available_bytes=1),
        residency.sample_once(portal_rss_bytes=930_000_000, ollama_models=[], available_bytes=1),  # -7%
    ]
    result = residency.classify(samples)
    assert result.status == "violated"
    assert result.max_drift_pct >= 6.0


def test_residency_model_evicted_before_expiry_violated():
    from arail.nucleus import residency

    samples = [
        residency.sample_once(portal_rss_bytes=1_000, ollama_models=[
            {"name": "llama-ai-eng", "size_vram": 1, "expires_at": 1_000_000}]),
        residency.sample_once(portal_rss_bytes=1_000, ollama_models=[]),
    ]
    # Force the second sample's ts below expires_at so it reads as premature.
    samples[1] = residency.ResiditySample(ts=500_000, portal_rss_bytes=1_000,
                                          ollama_models=[], available_bytes=None)
    result = residency.classify(samples)
    assert result.status == "violated"
    assert any(e["kind"] == "model_evicted" for e in result.events)


def test_residency_ttl_expired_not_a_violation():
    from arail.nucleus import residency

    samples = [
        residency.sample_once(portal_rss_bytes=1_000, ollama_models=[
            {"name": "llama-ai-eng", "size_vram": 1, "expires_at": 100}]),
        residency.ResiditySample(ts=200, portal_rss_bytes=1_000, ollama_models=[], available_bytes=None),
    ]
    result = residency.classify(samples)
    assert result.status == "ok"
    assert any(e["kind"] == "ttl_expired" for e in result.events)


def test_residency_unmeasured_when_buddy_not_running():
    from arail.nucleus import residency

    samples = [residency.sample_once(portal_rss_bytes=None, ollama_models=[], available_bytes=1)]
    result = residency.classify(samples)
    assert result.status == "unmeasured"
