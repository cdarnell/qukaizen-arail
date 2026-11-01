"""arail.nucleus.corpus.stage — snapshot writer + adapters
(T-STAGE-1,2, T-SEC-GIT-1)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from arail.nucleus.corpus import stage as stage_mod
from arail.nucleus.errors import DomainConfigError

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "nucleus" / "linux-kernel-mini"


def _load_fixture_module():
    spec = importlib.util.spec_from_file_location(
        "linux_kernel_mini_fixture", FIXTURE_DIR / "make_fixture_repo.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass() needs the module registered
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def fixture_module():
    return _load_fixture_module()


@pytest.fixture
def built_fixture(tmp_path, fixture_module):
    return fixture_module.build(tmp_path / "fixture")


class _FakeDomain:
    def __init__(self, sources, cutoff, name="kernel"):
        self.name = name
        self.corpus_sources = sources
        self.corpus_cutoff = cutoff


# ── T-STAGE-1: staging produces a manifest with licenses + origin commit ──

def test_stage_produces_manifest_with_licenses(tmp_path, built_fixture):
    domain = _FakeDomain((("git", "linux"), ("cve", "vulns")), built_fixture.cutoff)
    result = stage_mod.stage(
        domain,
        provided_sources={"linux": built_fixture.repo_dir, "vulns": built_fixture.vulns_dir},
        nucleus_data=tmp_path / "nucleus-data",
    )
    assert result.n_items == 51  # 50 fixture commits + the initial MAINTAINERS commit
    git_row = next(r for r in result.manifest["sources"] if r["id"] == "git:linux")
    assert git_row["status"] == "staged"
    assert git_row["license"] == "GPL-2.0-only"
    assert git_row["redistributable"] is True
    assert git_row["origin_commit"]
    cve_row = next(r for r in result.manifest["sources"] if r["id"] == "cve:vulns")
    assert cve_row["items"] == 8


def test_stage_second_run_is_deterministic(tmp_path, built_fixture):
    domain = _FakeDomain((("git", "linux"), ("cve", "vulns")), built_fixture.cutoff)
    r1 = stage_mod.stage(domain, provided_sources={"linux": built_fixture.repo_dir,
                                                   "vulns": built_fixture.vulns_dir},
                         nucleus_data=tmp_path / "d1")
    r2 = stage_mod.stage(domain, provided_sources={"linux": built_fixture.repo_dir,
                                                   "vulns": built_fixture.vulns_dir},
                         nucleus_data=tmp_path / "d2")
    assert r1.manifest_sha256 == r2.manifest_sha256


def test_stage_applies_cve_labels(tmp_path, built_fixture):
    domain = _FakeDomain((("git", "linux"), ("cve", "vulns")), built_fixture.cutoff)
    result = stage_mod.stage(domain, provided_sources={"linux": built_fixture.repo_dir,
                                                       "vulns": built_fixture.vulns_dir},
                             nucleus_data=tmp_path / "nucleus-data")
    items = stage_mod.load_items(result)
    labeled = [it for it in items if it["labels"].get("cve")]
    assert len(labeled) == 8


def test_stage_records_absent_for_missing_source(tmp_path, built_fixture):
    domain = _FakeDomain((("git", "linux"), ("cve", "vulns")), built_fixture.cutoff)
    result = stage_mod.stage(domain, provided_sources={"linux": built_fixture.repo_dir},
                             nucleus_data=tmp_path / "nucleus-data")
    cve_row = next(r for r in result.manifest["sources"] if r["id"] == "cve:vulns")
    assert cve_row["status"] == "absent"
    assert cve_row["license"] is None


# ── T-STAGE-2: build without staging -> refusal naming `stage` ──────────

def test_load_staged_returns_none_when_nothing_staged(tmp_path):
    assert stage_mod.load_staged("kernel", nucleus_data=tmp_path / "nucleus-data") is None


# ── T-SEC-GIT-1: hooks/fsmonitor never fire ──────────────────────────

def test_hostile_repo_hooks_and_fsmonitor_never_fire(tmp_path):
    from arail.nucleus.corpus.sources import git_kernel

    repo = tmp_path / "hostile"
    repo.mkdir()
    marker = tmp_path / "pwned"
    import subprocess

    env = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
          "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    subprocess.run(["git", "init", "-q"], cwd=str(repo), env=env, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.invalid"], cwd=str(repo), env=env, check=True)
    subprocess.run(["git", "config", "user.name", "a"], cwd=str(repo), env=env, check=True)
    subprocess.run(["git", "config", "core.fsmonitor", f"touch {marker}"], cwd=str(repo), env=env, check=True)

    hooks_dir = repo / ".git" / "hooks"
    hook = hooks_dir / "post-commit"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)

    (repo / "MAINTAINERS").write_text("X\n")
    subprocess.run(["git", "add", "MAINTAINERS"], cwd=str(repo), env=env, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(repo), env=env, check=True)

    # The *setup* commit above ran with plain (non-hardened) git and may
    # itself have tripped the repo's own core.fsmonitor config -- that's
    # the realistic starting state for a hostile repo we receive already
    # configured this way, not something our code caused. Clear any marker
    # left by setup, then assert our hardened reader creates none of its own.
    marker.unlink(missing_ok=True)

    git_kernel.extract(repo)
    assert not marker.exists(), "hook or fsmonitor fired during a hardened git log"


# B8 (2026-09-23 review): T-SEC-GIT-1 above never actually exercises the
# repo-local-config exec vector -- `git log` never runs post-checkout or
# fsmonitor regardless of hardening. This test reproduces the review's
# repro exactly: a repo with `log.showSignature=true` and `gpg.program`
# pointed at a script, plus a commit carrying a (fabricated, unverifiable
# is fine -- log.showSignature invokes the program regardless) `gpgsig`
# header. Without HARDENED_CONFIG_ARGS's `-c gpg.program=...`/
# `-c log.showSignature=false` overrides (and LOG_SAFETY_ARGS'
# `--no-show-signature`), this fires the script during a plain `git log`.
def test_hostile_repo_gpg_program_never_executes_during_log(tmp_path):
    from arail.nucleus.corpus.sources import git_kernel

    repo = tmp_path / "hostile-gpg"
    repo.mkdir()
    marker = tmp_path / "gpg-pwned"
    import subprocess

    env = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
          "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    subprocess.run(["git", "init", "-q"], cwd=str(repo), env=env, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.invalid"], cwd=str(repo), env=env, check=True)
    subprocess.run(["git", "config", "user.name", "a"], cwd=str(repo), env=env, check=True)

    (repo / "MAINTAINERS").write_text("X\n")
    subprocess.run(["git", "add", "MAINTAINERS"], cwd=str(repo), env=env, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(repo), env=env, check=True)

    tree_sha = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=str(repo), env=env,
                              check=True, capture_output=True).stdout.decode().strip()

    # A malicious "gpg" that just proves it ran.
    fake_gpg = tmp_path / "fake-gpg.sh"
    fake_gpg.write_text(f"#!/bin/sh\ntouch {marker}\necho 'gpg: Good signature' >&2\nexit 0\n")
    fake_gpg.chmod(0o755)
    subprocess.run(["git", "config", "gpg.program", str(fake_gpg)], cwd=str(repo), env=env, check=True)
    subprocess.run(["git", "config", "log.showSignature", "true"], cwd=str(repo), env=env, check=True)

    # Fabricate a signed commit object by hand -- a fake (unverifiable, but
    # present) gpgsig header is all `log.showSignature` needs to invoke
    # gpg.program; it doesn't need to actually verify.
    author = "a <a@b.invalid> 1700000000 +0000"
    raw_commit = (
        f"tree {tree_sha}\n"
        f"author {author}\n"
        f"committer {author}\n"
        f"gpgsig -----BEGIN PGP SIGNATURE-----\n"
        f" not a real signature, just present\n"
        f" -----END PGP SIGNATURE-----\n"
        f"\nsigned commit\n"
    )
    hash_obj = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--stdin"],
                              cwd=str(repo), env=env, input=raw_commit.encode(),
                              check=True, capture_output=True)
    signed_sha = hash_obj.stdout.decode().strip()
    subprocess.run(["git", "update-ref", "refs/heads/master", signed_sha], cwd=str(repo), env=env, check=True)

    marker.unlink(missing_ok=True)

    git_kernel.extract(repo)
    assert not marker.exists(), "gpg.program executed during a hardened git log"


def test_git_kernel_extract_rejects_non_git_dir(tmp_path):
    plain = tmp_path / "notgit"
    plain.mkdir()
    from arail.nucleus.corpus.sources import git_kernel

    with pytest.raises(DomainConfigError):
        git_kernel.extract(plain)


def test_cve_vulns_extract_requires_mapping_json(tmp_path):
    from arail.nucleus.corpus.sources import cve_vulns

    with pytest.raises(DomainConfigError):
        cve_vulns.extract(tmp_path)


def test_unknown_source_kind_rejected(tmp_path, built_fixture):
    domain = _FakeDomain((("lwn", "example"),), built_fixture.cutoff)
    result = stage_mod.stage(domain, provided_sources={}, nucleus_data=tmp_path / "nucleus-data")
    row = result.manifest["sources"][0]
    assert row["status"] == "absent"


def test_stage_output_guarded_against_committable_path(tmp_path, built_fixture, monkeypatch):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
    domain = _FakeDomain((("git", "linux"),), built_fixture.cutoff)
    with pytest.raises(Exception):
        stage_mod.stage(domain, provided_sources={"linux": built_fixture.repo_dir},
                        nucleus_data=tmp_path / "tracked-nucleus-data")


# ── fake_checkpatch.py sanity (real use lands in evals/executable_kernel.py, commit 15) ──

def test_fake_checkpatch_clean_patch_exits_zero():
    import subprocess
    import sys as _sys

    script = FIXTURE_DIR / "fake_checkpatch.py"
    result = subprocess.run(
        [_sys.executable, str(script), "--no-tree", "--terse", "-"],
        input=b"--- a/x\n+++ b/x\n+clean line\n", capture_output=True,
    )
    assert result.returncode == 0
    assert result.stdout == b""


def test_fake_checkpatch_trailing_whitespace_exits_one():
    import subprocess
    import sys as _sys

    script = FIXTURE_DIR / "fake_checkpatch.py"
    result = subprocess.run(
        [_sys.executable, str(script), "--no-tree", "--terse", "-"],
        input=b"--- a/x\n+++ b/x\n+dirty line   \n", capture_output=True,
    )
    assert result.returncode == 1
    assert b"WARNING" in result.stdout


# ── stage CLI verb (via cli.py dispatch) ─────────────────────────────

def test_stage_cli_verb_wired(monkeypatch, tmp_path, built_fixture, capsys):
    from arail.nucleus import cli
    from arail.nucleus import domain as domain_mod

    domains_dir = tmp_path / "domains"
    domains_dir.mkdir()
    yaml_text = f"""\
student:
  base: qwen2.5-3b-instruct
teacher:
  profile: local
  model: auto
corpus:
  sources: [git:linux, cve:vulns]
  cutoff: "{built_fixture.cutoff}"
eval:
  cert_set: frozen
  eyeball_prompts: kernel.eyeball.txt
  dev_fraction: 0.1
  cert_n: 20
fidelity:
  target: 0.6
"""
    (domains_dir / "kernel.yaml").write_text(yaml_text)
    (domains_dir / "kernel.eyeball.txt").write_text("\n".join(f"p{i}" for i in range(10)) + "\n")
    monkeypatch.setattr(domain_mod, "_DOMAINS_DIR", domains_dir)

    student_dir = tmp_path / "models" / "Qwen2.5-3B-Instruct-4bit"
    student_dir.mkdir(parents=True)
    (student_dir / "config.json").write_text('{"num_parameters": 3000000000}')
    monkeypatch.setattr("arail.config.MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setattr("arail.config.DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LAB_TIER", "maximus")

    code = cli.main([
        "stage", "kernel",
        "--source", f"git:linux={built_fixture.repo_dir}",
        "--source", f"cve:vulns={built_fixture.vulns_dir}",
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "staged" in out
