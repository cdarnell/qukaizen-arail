"""arail.nucleus.build — phase bodies (in-process, direct calls), plus
T-RESUME-1 (extract skips already-indexed windows on --resume)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from arail.nucleus import build as build_mod

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
              "domains_dir": str(domains_dir), "nucleus_data": str(data_dir / "nucleus")}
    return context


def test_phase_extract_train(staged_context):
    out = build_mod.run_phase_body("PA", staged_context)
    assert out["n_extracted_this_run"] > 0
    assert out["n_train_total"] > 0


def test_resume_skips_already_indexed(staged_context):
    out1 = build_mod.run_phase_body("PA", staged_context)
    out2 = build_mod.run_phase_body("PA", staged_context)
    assert out2["n_extracted_this_run"] == 0  # nothing new -- everything was already indexed
    assert out1["n_extracted_this_run"] == out1["n_train_total"]


def test_phase_extract_cert(staged_context):
    out = build_mod.run_phase_body("PA2", staged_context)
    assert out["n_cert"] == 5

    from arail.nucleus.build import _run_dir

    eval_dir = _run_dir(staged_context) / "eval"
    assert (eval_dir / "teacher_cve.jsonl").is_file()
    assert (eval_dir / "teacher_subsystem.jsonl").is_file()


def test_phase_train_stub(staged_context):
    out = build_mod.run_phase_body("PB", staged_context)
    assert out["stop_metric"] in ("target_reached", "fidelity_plateau_3_cycles", "budget")
    assert out["n_cycles"] > 0
    assert out["best_adapter_path"]


def test_phase_fuse_stub(staged_context):
    build_mod.run_phase_body("PB", staged_context)
    out = build_mod.run_phase_body("fuse", staged_context)
    output_dir = Path(out["output_dir"])
    assert output_dir.is_dir()
    assert (output_dir / "FUSED_STUB_MARKER").is_file()


def test_phase_eval_stub(staged_context):
    build_mod.run_phase_body("PB", staged_context)
    build_mod.run_phase_body("fuse", staged_context)
    out = build_mod.run_phase_body("PC", staged_context)
    assert out["n_cert"] == 5
    assert 0.0 <= out["closed_mean_f1"] <= 1.0

    from arail.nucleus.build import _run_dir

    metrics_path = _run_dir(staged_context) / "eval" / "metrics.json"
    metrics = json.loads(metrics_path.read_text())
    assert "closed" in metrics and "open" in metrics and "executable" in metrics


def test_unknown_phase_refused(staged_context):
    from arail.nucleus.errors import RefusedByPolicy

    with pytest.raises(RefusedByPolicy):
        build_mod.run_phase_body("nonexistent", staged_context)


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
