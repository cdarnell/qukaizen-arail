"""arail.nucleus.build — phase bodies (in-process, direct calls), plus
T-RESUME-1 (extract skips already-indexed windows on --resume)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from arail.nucleus import build as build_mod


def _run_phase(context, phase):
    """run_phase_body() + persist_phase_output() — mirrors what
    worker.py does for a real subprocess phase (B9: certify reads
    fuse.json from phase_output/), for tests that call phase bodies
    in-process rather than through a spawned worker."""
    output = build_mod.run_phase_body(phase, context)
    build_mod.persist_phase_output(context, phase, output)
    return output

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "nucleus" / "linux-kernel-mini"


def _load_fixture_module():
    spec = importlib.util.spec_from_file_location(
        "linux_kernel_mini_fixture_buildphases", FIXTURE_DIR / "make_fixture_repo.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def staged_context(tmp_path, monkeypatch):
    fixture_module = _load_fixture_module()
    built = fixture_module.build(tmp_path / "fixture")

    domains_dir = tmp_path / "domains"
    domains_dir.mkdir()
    (domains_dir / "kernel.yaml").write_text(f"""\
student:
  base: qwen2.5-3b-instruct
teacher:
  profile: local
  model: auto
corpus:
  sources: [git:linux, cve:vulns]
  cutoff: "{built.cutoff}"
eval:
  cert_set: frozen
  eyeball_prompts: kernel.eyeball.txt
  dev_fraction: 0.1
  cert_n: 5
fidelity:
  target: 0.6
distill:
  top_n: 3
""")
    (domains_dir / "kernel.eyeball.txt").write_text("\n".join(f"p{i}" for i in range(10)) + "\n")

    data_dir = tmp_path / "data"
    models_dir = tmp_path / "models"
    student_dir = models_dir / "Qwen2.5-3B-Instruct-4bit"
    student_dir.mkdir(parents=True)
    (student_dir / "config.json").write_text('{"num_parameters": 3000000000}')

    monkeypatch.setattr("arail.config.DATA_DIR", str(data_dir))
    monkeypatch.setattr("arail.config.MODELS_DIR", str(models_dir))
    monkeypatch.setenv("ARAIL_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ARAIL_MODELS_DIR", str(models_dir))
    monkeypatch.setenv("ARAIL_NUCLEUS_STUB", "1")

    from arail.nucleus.corpus.stage import stage as stage_corpus
    from arail.nucleus.domain import load_domain
    from arail.nucleus.evals.splits import CertStore
    from arail.nucleus.models import resolve_model

    domain = load_domain("kernel", domains_dir=domains_dir, model_resolver=resolve_model)
    stage_result = stage_corpus(domain, provided_sources={"linux": built.repo_dir, "vulns": built.vulns_dir},
                               nucleus_data=data_dir / "nucleus")
    CertStore(nucleus_data=data_dir / "nucleus").create(domain, stage_result)

    build_id = "kernel-20260101T000000Z-abcd"
    context = {"domain_name": "kernel", "build_id": build_id,
              "domains_dir": str(domains_dir), "nucleus_data": str(data_dir / "nucleus"),
              "stub": True, "provider": "stub"}
    return context


def test_phase_extract_train(staged_context):
    out = _run_phase(staged_context, "PA")
    assert out["n_extracted_this_run"] > 0
    assert out["n_train_total"] > 0


def test_resume_skips_already_indexed(staged_context):
    out1 = _run_phase(staged_context, "PA")
    out2 = _run_phase(staged_context, "PA")
    assert out2["n_extracted_this_run"] == 0  # nothing new -- everything was already indexed
    assert out1["n_extracted_this_run"] == out1["n_train_total"]


def test_phase_extract_cert(staged_context):
    out = _run_phase(staged_context, "PA2")
    assert out["n_cert"] == 5

    from arail.nucleus.build import _run_dir

    eval_dir = _run_dir(staged_context) / "eval"
    assert (eval_dir / "teacher_cve.jsonl").is_file()
    assert (eval_dir / "teacher_subsystem.jsonl").is_file()


def test_phase_train_stub(staged_context):
    out = _run_phase(staged_context, "PB")
    assert out["stop_metric"] in ("target_reached", "fidelity_plateau_3_cycles", "budget")
    assert out["n_cycles"] > 0
    assert out["best_adapter_path"]


def test_phase_fuse_stub(staged_context):
    _run_phase(staged_context, "PB")
    out = _run_phase(staged_context, "fuse")
    output_dir = Path(out["output_dir"])
    assert output_dir.is_dir()
    assert (output_dir / "FUSED_STUB_MARKER").is_file()


def test_phase_eval_stub(staged_context):
    _run_phase(staged_context, "PB")
    _run_phase(staged_context, "fuse")
    out = _run_phase(staged_context, "PC")
    assert out["n_cert"] == 5
    assert 0.0 <= out["closed_mean_f1"] <= 1.0

    from arail.nucleus.build import _run_dir

    metrics_path = _run_dir(staged_context) / "eval" / "metrics.json"
    metrics = json.loads(metrics_path.read_text())
    assert "closed" in metrics and "open" in metrics and "executable" in metrics


def test_unknown_phase_refused(staged_context):
    from arail.nucleus.errors import RefusedByPolicy

    with pytest.raises(RefusedByPolicy):
        _run_phase(staged_context, "nonexistent")


# ── B7 (2026-09-23 review): a non-stub build refuses up front, before
# any lock/run dir/phase exists — never partway through with a
# misleading "model '' not found" error ─────────────────────────────

def test_build_run_refuses_non_stub_up_front(staged_context, monkeypatch, tmp_path):
    domains_dir = Path(staged_context["domains_dir"])
    monkeypatch.setattr("arail.nucleus.domain._DOMAINS_DIR", domains_dir)
    monkeypatch.setenv("ARAIL_DATA_DIR", str(Path(staged_context["nucleus_data"]).parent))
    monkeypatch.setattr("arail.config.DATA_DIR", str(Path(staged_context["nucleus_data"]).parent))
    monkeypatch.delenv("ARAIL_NUCLEUS_STUB", raising=False)
    from arail.nucleus.errors import RefusedByPolicy
    from arail.nucleus.paths import nucleus_data

    with pytest.raises(RefusedByPolicy) as exc_info:
        build_mod.run(["kernel", "--profile", "local"])
    assert "ARAIL_NUCLEUS_STUB=1" in str(exc_info.value)

    runs_dir = nucleus_data() / "runs"
    assert not runs_dir.is_dir() or not any(runs_dir.iterdir())
    assert not (nucleus_data() / "build.lock").exists()


def test_cli_build_non_stub_exits_3(staged_context, monkeypatch):
    domains_dir = Path(staged_context["domains_dir"])
    monkeypatch.setattr("arail.nucleus.domain._DOMAINS_DIR", domains_dir)
    monkeypatch.setenv("ARAIL_DATA_DIR", str(Path(staged_context["nucleus_data"]).parent))
    monkeypatch.setattr("arail.config.DATA_DIR", str(Path(staged_context["nucleus_data"]).parent))
    monkeypatch.delenv("ARAIL_NUCLEUS_STUB", raising=False)
    from arail.nucleus import cli

    rc = cli.main(["build", "kernel", "--profile", "local"])
    assert rc == 3


# ── build.run() end-to-end (real subprocess phases, stub mode) ────────

def test_build_run_end_to_end(staged_context, monkeypatch):
    from arail.nucleus import build as build_mod

    domains_dir = Path(staged_context["domains_dir"])
    monkeypatch.setattr("arail.nucleus.domain._DOMAINS_DIR", domains_dir)
    monkeypatch.setenv("ARAIL_DATA_DIR", str(Path(staged_context["nucleus_data"]).parent))

    code = build_mod.run(["kernel", "--profile", "local"])
    assert code == 0


def test_status_reports_run_json(tmp_path, monkeypatch):
    from arail.nucleus import build as build_mod
    from arail.nucleus.phases import RunLedger
    from arail.nucleus.paths import run_dir

    monkeypatch.setattr("arail.config.DATA_DIR", str(tmp_path / "data"))
    build_id = "kernel-20260101T000000Z-face"
    rd = run_dir(build_id)
    rd.mkdir(parents=True)
    ledger = RunLedger(rd, build_id, "kernel")
    ledger.start_phase("PA", 1)
    ledger.finish_phase("PA", status="done")

    code = build_mod.status([build_id])
    assert code == 0


def test_status_missing_build_refuses(tmp_path, monkeypatch):
    from arail.nucleus import build as build_mod

    monkeypatch.setattr("arail.config.DATA_DIR", str(tmp_path / "data"))
    code = build_mod.status(["kernel-20260101T000000Z-dead"])
    assert code == 3


# ── ASK judge-identity check (2026-09-23 review round 2): the PC phase
# must call the judge != teacher/student-base identity check BEFORE any
# generation happens, wired for the stub judge too ─────────────────────

def test_score_open_lc_refuses_when_judge_identity_matches_teacher(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from arail.nucleus.evals import open_lc_judge

    monkeypatch.setenv("ARAIL_NUCLEUS_STUB", "1")
    eyeball = tmp_path / "eyeball.txt"
    eyeball.write_text("\n".join(f"p{i}" for i in range(10)) + "\n")
    domain = SimpleNamespace(eval_eyeball_prompts=eyeball, student_base="qwen2.5-3b-instruct",
                             teacher_model="some-teacher")
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    # Force a collision at the wiring level: the judge's computed content
    # identity resolves to the SAME value the teacher's does (in reality
    # this happens when the judge model IS the teacher, by content, not
    # name/alias).
    monkeypatch.setattr("arail.nucleus.models.best_effort_identity", lambda name: "sha-collision")
    monkeypatch.setattr(build_mod, "_stub_judge_identity", lambda winner_table: "sha-collision")

    with pytest.raises(open_lc_judge.JudgeIsTeacher):
        build_mod._score_open_lc(domain, fused_model_dir=tmp_path / "fused", run_dir=run_dir)

    # Refused BEFORE any generation -- no provider output on disk.
    assert not any((tmp_path / "fused").rglob("*"))
