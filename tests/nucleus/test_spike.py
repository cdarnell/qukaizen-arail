"""arail.nucleus.spike — Gate B harness (T-SPIKE-1)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from arail.nucleus import spike as spike_mod

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "nucleus" / "linux-kernel-mini"


def _load_fixture_module():
    spec = importlib.util.spec_from_file_location(
        "linux_kernel_mini_fixture_spike", FIXTURE_DIR / "make_fixture_repo.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def staged_domain(tmp_path, monkeypatch):
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
  target: 0.5
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
    monkeypatch.setenv("ARAIL_NUCLEUS_STUB", "1")

    from arail.nucleus.corpus.stage import stage as stage_corpus
    from arail.nucleus.domain import load_domain
    from arail.nucleus.evals.splits import CertStore
    from arail.nucleus.models import resolve_model

    domain = load_domain("kernel", domains_dir=domains_dir, model_resolver=resolve_model)
    stage_result = stage_corpus(domain, provided_sources={"linux": built.repo_dir, "vulns": built.vulns_dir},
                               nucleus_data=data_dir / "nucleus")
    CertStore(nucleus_data=data_dir / "nucleus").create(domain, stage_result)
    return domain


def test_spike_produces_report_with_b0_b3_fields(staged_domain):
    result = spike_mod.run_spike(staged_domain, windows=5, cert_sample=5)
    d = result.to_dict()
    for key in ("b0_pass", "b1_pass", "b2_pass", "b3_pass", "captured_mass", "overall_pass"):
        assert key in d


def test_spike_b0_passes_for_stub_deterministic_provider(staged_domain):
    result = spike_mod.run_spike(staged_domain, windows=5, cert_sample=5)
    assert result.b0_pass is True  # stub's generate() and generate_with_topn() agree on text


def test_spike_captured_mass_reported():
    import math

    from arail.nucleus.spike import _mean_captured_mass
    from types import SimpleNamespace

    # Realistic top-3 logprobs -- log of a valid (sub-1.0) probability
    # distribution, not arbitrary numbers.
    probs = [0.5, 0.3, 0.15]
    logprobs = [math.log(p) for p in probs]
    gen = SimpleNamespace(topn_ids=[[0, 1, 2]], topn_logprobs=[logprobs])
    mass = _mean_captured_mass([gen], student_max_id=None)
    assert mass == pytest.approx(sum(probs))


def test_spike_cli_writes_report_file(staged_domain, monkeypatch, tmp_path):
    from arail.nucleus import domain as domain_mod

    monkeypatch.setattr(domain_mod, "_DOMAINS_DIR", Path(staged_domain.eval_eyeball_prompts).parent)
    code = spike_mod.run(["kernel", "--windows", "5", "--cert-sample", "5"])
    assert code in (0, 3)

    from arail.nucleus.paths import nucleus_data

    report_path = nucleus_data() / "domains" / "kernel" / "spike-report.json"
    assert report_path.is_file()
    data = json.loads(report_path.read_text())
    assert "overall_pass" in data
