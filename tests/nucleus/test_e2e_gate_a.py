"""Gate A: the stub-provider pipeline end to end, as a real subprocess CLI
run — plan -> stage -> build -> certify -> verify (T-E2E-1, T-EGR-1)."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "nucleus" / "linux-kernel-mini"
SRC_DIR = str(Path(__file__).resolve().parents[2] / "src")


def _load_fixture_module():
    spec = importlib.util.spec_from_file_location(
        "linux_kernel_mini_fixture_e2e", FIXTURE_DIR / "make_fixture_repo.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run_cli(args, env, *, timeout=120):
    return subprocess.run(
        [sys.executable, "-m", "arail.nucleus", *args],
        env=env, capture_output=True, timeout=timeout,
    )


@pytest.fixture
def gate_a_env(tmp_path):
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
  cert_n: 20
fidelity:
  target: 0.5
distill:
  top_n: 3
""")
    (domains_dir / "kernel.eyeball.txt").write_text("\n".join(f"eyeball prompt {i}" for i in range(10)) + "\n")

    data_dir = tmp_path / "data"
    models_dir = tmp_path / "models"
    student_dir = models_dir / "Qwen2.5-3B-Instruct-4bit"
    student_dir.mkdir(parents=True)
    (student_dir / "config.json").write_text('{"num_parameters": 3000000000}')

    env = dict(os.environ)
    env.update({
        "PYTHONPATH": SRC_DIR,
        "ARAIL_DATA_DIR": str(data_dir),
        "ARAIL_MODELS_DIR": str(models_dir),
        "ARAIL_NUCLEUS_DOMAINS_DIR": str(domains_dir),
        "ARAIL_NUCLEUS_STUB": "1",
        "LAB_MODE": "airgapped",
        "LAB_TIER": "maximus",
        "NUCLEUS_SIGNING_KEY_PATH": str(tmp_path / "signing.ed25519"),
    })
    return {"env": env, "built": built, "data_dir": data_dir, "models_dir": models_dir,
           "domains_dir": domains_dir, "tmp_path": tmp_path}


def test_gate_a_stub_pipeline_end_to_end(gate_a_env, monkeypatch):
    env = gate_a_env["env"]
    built = gate_a_env["built"]
    tmp_path = gate_a_env["tmp_path"]

    # plan (preflight-only load of the already-written domain).
    result = _run_cli(["plan", "kernel"], env)
    assert result.returncode == 0, result.stderr.decode()

    # stage.
    result = _run_cli(["stage", "kernel",
                       "--source", f"git:linux={built.repo_dir}",
                       "--source", f"cve:vulns={built.vulns_dir}"], env)
    assert result.returncode == 0, result.stderr.decode()

    cert_dir_glob = list((gate_a_env["data_dir"] / "nucleus" / "domains" / "kernel" / "cert").glob("cert-v*"))
    assert not cert_dir_glob  # no cert version minted yet -- build mints it via CertStore lazily below

    # Cert set: create it now (a real build would need this before Phase A2;
    # this test mints it directly rather than teaching `stage` a
    # --new-cert-version flag path through subprocess env, which is already
    # covered at the unit level in test_splits.py).
    _mint_cert_version(env, gate_a_env)

    cert_manifest_path = next((gate_a_env["data_dir"] / "nucleus" / "domains" / "kernel" / "cert").glob("cert-v*")) \
        / "manifest.json"
    cert_sha_before = json.loads(cert_manifest_path.read_text())["sha256"]
    cert_jsonl_before = (cert_manifest_path.parent / "cert.jsonl").read_bytes()

    # build.
    build_id = "kernel-20260101T000000Z-e2ea"
    result = _run_cli(["build", "kernel", "--profile", "local", "--resume", build_id], env, timeout=180)
    assert result.returncode == 0, result.stderr.decode()

    run_json_path = gate_a_env["data_dir"] / "nucleus" / "runs" / build_id / "run.json"
    run_data = json.loads(run_json_path.read_text())
    _assert_phase_intervals_non_overlapping(run_data["phases"])

    # certify.
    result = _run_cli(["certify", build_id], env)
    assert result.returncode == 0, result.stderr.decode()

    # Cert set must be byte-identical after the whole build+certify run.
    cert_sha_after = json.loads(cert_manifest_path.read_text())["sha256"]
    cert_jsonl_after = (cert_manifest_path.parent / "cert.jsonl").read_bytes()
    assert cert_sha_before == cert_sha_after
    assert cert_jsonl_before == cert_jsonl_after

    # B9 (2026-09-23 review): the card lands under FORGE_ROOT/<shard>/<ver>,
    # not the run dir's own "forge_out" scratch path -- so /forge and
    # `verify <shard>@<ver>` can actually find it.
    fuse_output = json.loads(
        (gate_a_env["data_dir"] / "nucleus" / "runs" / build_id / "phase_output" / "fuse.json").read_text())
    shard, fused_version = fuse_output["shard"], fuse_output["version"]
    forge_out = gate_a_env["models_dir"] / "forge" / shard / fused_version
    assert forge_out == Path(fuse_output["shard_dir"])
    card_path = forge_out / "dna-card.yaml"
    assert card_path.is_file()

    from arail.nucleus.cards.dna_v2 import load_card, validate_card

    card = load_card(card_path)
    validate_card(card)  # no raise
    assert card["runtime"] == "stub"
    assert card["shard"] == shard
    assert card["version"] == fused_version

    # B10 (2026-09-23 review): exact golden metric values, composite, and
    # decision -- not "decision in the four known values" (tautological).
    # These are the real, reproducible numbers this fixture produces
    # (deterministic since make_fixture_repo.py forces GIT_COMMITTER_DATE,
    # and build._stub_closed_answer_table derives wrong answers from a
    # fixed hash of item_id, not randomness).
    cve = card["closed_ended"]["cve_detection"]
    assert cve["f1"] == pytest.approx(0.5)
    assert cve["precision"] == pytest.approx(0.333333, abs=1e-5)
    assert cve["recall"] == pytest.approx(1.0)
    sub = card["closed_ended"]["subsystem_routing"]
    assert sub["macro_f1"] == pytest.approx(0.720635, abs=1e-5)
    assert card["composite"]["formula_id"] == "composite/v1-open"
    assert card["composite"]["formula"] == "0.6*closed.mean_f1+0.4*open.lc_win_rate"
    assert card["composite"]["value"] == pytest.approx(0.566191, abs=1e-5)
    assert card["fidelity"]["achieved"] == pytest.approx(0.566191, abs=1e-5)
    assert card["fidelity"]["decision"] == "CERTIFIED"
    for name in ("compiles", "patch_applies", "checkpatch_clean"):
        assert card["executable"][name]["status"] == "not_run"

    # eval-config.lock recomputes to the card's eval_hash (B10 item 3).
    from arail.nucleus.evals.hash import eval_hash as _recompute_eval_hash
    from arail.nucleus.evals.hash import read_eval_config_lock

    lock_inputs = read_eval_config_lock(forge_out / "eval-config.lock")
    assert _recompute_eval_hash(lock_inputs) == card["eval_hash"]

    # Stub is never ledgered (T-STUB-2 / T-LEDGER-3).
    ledger_path = gate_a_env["data_dir"] / "nucleus" / "CERTIFIED_SHARDS.md"
    assert not ledger_path.exists() or shard not in ledger_path.read_text()

    report_text = (forge_out / "build-report.md").read_text()
    import re

    assert len(re.findall(r"^### \d+\. ", report_text, flags=re.MULTILINE)) == 10

    # verify: valid + ephemeral-stub, both by directory and by <shard>@<ver>.
    result = _run_cli(["verify", str(forge_out), "--fast"], env)
    out = result.stdout.decode()
    assert "signature: valid" in out
    assert "key: ephemeral-stub" in out

    result = _run_cli(["verify", f"{shard}@{fused_version}", "--fast"], env)
    out = result.stdout.decode()
    assert "signature: valid" in out
    assert "key: ephemeral-stub" in out

    # /api/forge/cards lists it (forge_api._list_cards() scans FORGE_ROOT).
    # In-process, so point THIS process's arail.config.MODELS_DIR at the
    # same models dir the subprocess CLI calls above used.
    monkeypatch.setattr("arail.config.MODELS_DIR", str(gate_a_env["models_dir"]))
    from arail.portal import forge_api

    cards = forge_api.list_cards()
    assert any(c["shard"] == shard and c["version"] == fused_version for c in cards)

    # egress.jsonl is absent or byte-identical (zero egress across the
    # whole run) -- absent is the expected state here since nothing in
    # the stub pipeline makes any network call at all.
    egress_path = gate_a_env["data_dir"] / "egress.jsonl"
    assert not egress_path.exists()

    # ActivityLog has nucleus events.
    activity_path = gate_a_env["data_dir"] / "activity.jsonl"
    assert activity_path.is_file()
    events = [json.loads(ln) for ln in activity_path.read_text().splitlines() if ln.strip()]
    assert any(e.get("source") == "nucleus" for e in events)


def _mint_cert_version(env, gate_a_env):
    script = (
        "import sys; sys.path.insert(0, %r)\n"
        "from arail.nucleus.domain import load_domain\n"
        "from arail.nucleus.models import resolve_model\n"
        "from arail.nucleus.corpus.stage import load_staged\n"
        "from arail.nucleus.evals.splits import CertStore\n"
        "domain = load_domain('kernel', model_resolver=resolve_model)\n"
        "stage_result = load_staged(domain.name)\n"
        "CertStore().create(domain, stage_result)\n"
    ) % SRC_DIR
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True)
    assert result.returncode == 0, result.stderr.decode()


def _assert_phase_intervals_non_overlapping(phases):
    from datetime import datetime

    intervals = []
    for p in phases:
        if p.get("start") and p.get("end"):
            intervals.append((datetime.fromisoformat(p["start"]), datetime.fromisoformat(p["end"]), p["phase"]))
    intervals.sort()
    for i in range(1, len(intervals)):
        prev_end = intervals[i - 1][1]
        cur_start = intervals[i][0]
        assert cur_start >= prev_end, f"phase interval overlap: {intervals[i-1]} vs {intervals[i]}"


# ── T-EGR-1: in-process, socket.socket.connect patched to raise on
# non-loopback -> no raise during the pipeline ─────────────────────────

def test_no_non_loopback_socket_connect_in_process(tmp_path, monkeypatch):
    import socket

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
    monkeypatch.setenv("LAB_MODE", "airgapped")

    from arail.nucleus.corpus.stage import stage as stage_corpus
    from arail.nucleus.domain import load_domain
    from arail.nucleus.evals.splits import CertStore
    from arail.nucleus.models import resolve_model

    domain = load_domain("kernel", domains_dir=domains_dir, model_resolver=resolve_model)

    original_connect = socket.socket.connect

    def _guarded_connect(self, address, *a, **kw):
        host = address[0] if isinstance(address, tuple) else str(address)
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"non-loopback connect attempted: {address}")
        return original_connect(self, address, *a, **kw)

    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)

    stage_result = stage_corpus(domain, provided_sources={"linux": built.repo_dir, "vulns": built.vulns_dir},
                               nucleus_data=data_dir / "nucleus")
    CertStore(nucleus_data=data_dir / "nucleus").create(domain, stage_result)  # no raise -> no non-loopback connect
