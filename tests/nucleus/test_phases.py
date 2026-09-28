"""arail.nucleus.phases — D3 enforcement + real subprocess phase execution
(T-D3-1, T-EGR-2, and a resume smoke check feeding T-RESUME-1)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from arail.nucleus.phases import PhaseRecord, RunLedger, run_phase, worker_offline_env


# ── T-D3-1: refuse to start a train phase while a teacher-resident pid is alive ──

def test_d3_refuses_train_while_teacher_resident(tmp_path):
    ledger = RunLedger(tmp_path, "build-1", "kernel")
    ledger.phases.append(PhaseRecord(phase="PA", pid=os.getpid(), status="running"))
    from arail.nucleus.errors import RefusedByPolicy

    with pytest.raises(RefusedByPolicy, match="D3"):
        ledger.assert_no_teacher_resident_before("PB")


def test_d3_allows_train_after_teacher_phase_done(tmp_path):
    ledger = RunLedger(tmp_path, "build-1", "kernel")
    ledger.phases.append(PhaseRecord(phase="PA", pid=os.getpid(), status="done"))
    ledger.assert_no_teacher_resident_before("PB")  # no raise


def test_d3_ignores_non_train_phases(tmp_path):
    ledger = RunLedger(tmp_path, "build-1", "kernel")
    ledger.phases.append(PhaseRecord(phase="PA", pid=os.getpid(), status="running"))
    ledger.assert_no_teacher_resident_before("PA2")  # no raise -- PA2 isn't a train phase


def test_d3_dead_pid_does_not_block():
    ledger = RunLedger(Path("/tmp"), "build-1", "kernel")
    # A pid essentially guaranteed not to exist.
    ledger.phases.append(PhaseRecord(phase="PA", pid=999999, status="running"))
    ledger.assert_no_teacher_resident_before("PB")  # no raise -- pid is dead


# ── T-EGR-2 (unit half): worker_offline_env sets the HF_*_OFFLINE vars ──

def test_worker_offline_env_vars():
    env = worker_offline_env()
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["TRANSFORMERS_OFFLINE"] == "1"
    assert env["HF_DATASETS_OFFLINE"] == "1"
    assert env["TOKENIZERS_PARALLELISM"] == "false"


# ── run.json ledger round trip ──────────────────────────────────────

def test_ledger_round_trips_through_disk(tmp_path):
    ledger = RunLedger(tmp_path, "build-1", "kernel")
    ledger.start_phase("PA", 12345)
    ledger.finish_phase("PA", status="done", output_hashes={"a": "b"})

    reloaded = RunLedger(tmp_path, "build-1", "kernel")
    assert reloaded.is_done("PA")
    assert reloaded.record_for("PA").output_hashes == {"a": "b"}


# ── real subprocess integration: run_phase spawns a real worker ──────

_SRC_DIR = str(Path(__file__).resolve().parents[2] / "src")
FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "nucleus" / "linux-kernel-mini"


def _load_fixture_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "linux_kernel_mini_fixture_phases", FIXTURE_DIR / "make_fixture_repo.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_run_phase_spawns_real_worker_extract_train(tmp_path, monkeypatch):
    """Real end-to-end: a real `python -m arail.nucleus.worker PA <build_id>`
    subprocess, real dispatch through worker.py -> build.py's PA phase body,
    against a real staged fixture corpus, in stub mode -- proves the offline
    env actually reaches the worker (T-EGR-2) and that the ledger correctly
    records a real subprocess's pid/start/end."""
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
    from arail.nucleus.models import resolve_model

    domain = load_domain("kernel", domains_dir=domains_dir, model_resolver=resolve_model)
    stage_corpus(domain, provided_sources={"linux": built.repo_dir, "vulns": built.vulns_dir},
                nucleus_data=data_dir / "nucleus")

    from arail.nucleus.paths import run_dir

    build_id = "kernel-20260101T000000Z-abcd"
    rd = run_dir(build_id)
    rd.mkdir(parents=True)
    (rd / "context.json").write_text(json.dumps({
        "domain_name": "kernel", "build_id": build_id,
        "domains_dir": str(domains_dir), "nucleus_data": str(data_dir / "nucleus"),
    }))

    ledger = RunLedger(rd, build_id, "kernel")
    run_phase("PA", build_id, ledger=ledger)

    assert ledger.is_done("PA")
    rec = ledger.record_for("PA")
    assert rec.pid is not None and rec.pid != os.getpid()
    assert rec.start and rec.end

    output = json.loads((rd / "phase_output" / "PA.json").read_text())
    assert output["n_extracted_this_run"] > 0
    index_path = rd / "extract" / "index.jsonl"
    assert index_path.is_file()
    n_shards = len(list((rd / "extract").glob("*.npz")))
    assert n_shards == output["n_extracted_this_run"]
