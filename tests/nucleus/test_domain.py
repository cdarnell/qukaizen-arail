"""arail.nucleus.domain — nucleus.domain/v1 load/validate (T-DOM-1..4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from arail.nucleus.domain import load_domain
from arail.nucleus.errors import DomainConfigError

VALID_YAML = """\
student:
  base: qwen2.5-3b-instruct
teacher:
  profile: local
  model: auto
runtime: queuellm
corpus:
  sources:
    - git:linux
    - cve:vulns
  cutoff: "2026-06-01"
eval:
  cert_set: frozen
  eyeball_prompts: {name}.eyeball.txt
  dev_fraction: 0.1
  cert_n: 800
fidelity:
  target: 0.75
distill:
  top_n: 20
shard: qkz-{name}
"""

EYEBALL_10 = "\n".join(f"prompt {i}" for i in range(1, 11)) + "\n"


def _write_domain(tmp_path: Path, name: str, yaml_text: str, *, eyeball: str = EYEBALL_10) -> Path:
    d = tmp_path / "domains"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.yaml").write_text(yaml_text)
    (d / f"{name}.eyeball.txt").write_text(eyeball)
    return d


# ── T-DOM-1: valid domain loads with defaults ────────────────────────

def test_valid_domain_loads_with_defaults(tmp_path):
    d = _write_domain(tmp_path, "kernel", VALID_YAML.format(name="kernel"))
    domain = load_domain("kernel", domains_dir=d)
    assert domain.name == "kernel"
    assert domain.student_base == "qwen2.5-3b-instruct"
    assert domain.runtime == "queuellm"
    assert domain.corpus_sources == (("git", "linux"), ("cve", "vulns"))
    assert domain.corpus_cutoff == "2026-06-01"
    assert domain.eval_cert_n == 800
    assert domain.distill_top_n == 20
    assert domain.shard == "qkz-kernel"


def test_defaults_applied_when_omitted(tmp_path):
    minimal = """\
student:
  base: qwen2.5-3b-instruct
corpus:
  sources: [git:linux]
  cutoff: "2026-01-01"
eval:
  cert_set: frozen
  eyeball_prompts: min.eyeball.txt
  dev_fraction: 0.2
fidelity:
  target: 0.5
"""
    d = _write_domain(tmp_path, "min", minimal)
    domain = load_domain("min", domains_dir=d)
    assert domain.runtime == "queuellm"           # default
    assert domain.eval_cert_n == 800               # default
    assert domain.distill_top_n == 20              # default
    assert domain.shard == "qkz-min"                # default
    assert domain.teacher_profile == "local"        # default


def test_unknown_key_rejected(tmp_path):
    bad = VALID_YAML.format(name="kernel") + "typo_field: 1\n"
    d = _write_domain(tmp_path, "kernel", bad)
    with pytest.raises(DomainConfigError):
        load_domain("kernel", domains_dir=d)


# ── T-DOM-2: legacy superskill manifest ──────────────────────────────

def test_legacy_superskill_manifest_message(tmp_path):
    d = tmp_path / "domains"
    d.mkdir()
    (d / "legacy.yaml").write_text("superskill: qukaizen-project-aware\nversion: 1\n")
    with pytest.raises(DomainConfigError, match="legacy Nucleus superskill manifest"):
        load_domain("legacy", domains_dir=d)


# ── T-DOM-3: path traversal / non-identifier sources ─────────────────

def test_eyeball_path_traversal_rejected(tmp_path):
    bad = VALID_YAML.format(name="kernel").replace(
        "eyeball_prompts: kernel.eyeball.txt", "eyeball_prompts: ../../etc/passwd")
    d = _write_domain(tmp_path, "kernel", bad)
    with pytest.raises(DomainConfigError):
        load_domain("kernel", domains_dir=d)


def test_eyeball_absolute_path_rejected(tmp_path):
    bad = VALID_YAML.format(name="kernel").replace(
        "eyeball_prompts: kernel.eyeball.txt", "eyeball_prompts: /etc/passwd")
    d = _write_domain(tmp_path, "kernel", bad)
    with pytest.raises(DomainConfigError):
        load_domain("kernel", domains_dir=d)


def test_eyeball_symlink_escape_rejected(tmp_path):
    """A symlinked eyeball file whose real target resolves outside
    configs/domains/ must be rejected — realpath containment, not just a
    string-prefix check."""
    escaped_dir = tmp_path / "elsewhere"
    escaped_dir.mkdir()
    (escaped_dir / "escape.eyeball.txt").write_text(EYEBALL_10)

    d = tmp_path / "domains"
    d.mkdir()
    (d / "escape.eyeball.txt").symlink_to(escaped_dir / "escape.eyeball.txt")
    (d / "escape.yaml").write_text(VALID_YAML.format(name="escape"))

    with pytest.raises(DomainConfigError):
        load_domain("escape", domains_dir=d)


def test_source_with_slash_rejected(tmp_path):
    bad = VALID_YAML.format(name="kernel").replace("git:linux", "git:../linux")
    d = _write_domain(tmp_path, "kernel", bad)
    with pytest.raises(DomainConfigError):
        load_domain("kernel", domains_dir=d)


def test_eyeball_wrong_count_rejected(tmp_path):
    d = _write_domain(tmp_path, "kernel", VALID_YAML.format(name="kernel"), eyeball="only one\n")
    with pytest.raises(DomainConfigError, match="exactly 10"):
        load_domain("kernel", domains_dir=d)


def test_refresh_cert_set_not_supported(tmp_path):
    bad = VALID_YAML.format(name="kernel").replace("cert_set: frozen", "cert_set: refresh:7")
    d = _write_domain(tmp_path, "kernel", bad)
    with pytest.raises(DomainConfigError, match="not supported"):
        load_domain("kernel", domains_dir=d)


# ── T-DOM-4: student >= 8B refused (via injected resolver) ───────────

class _FakeResolved:
    def __init__(self, params_est_b):
        self.params_est_b = params_est_b


def test_student_over_8b_refused(tmp_path):
    d = _write_domain(tmp_path, "kernel", VALID_YAML.format(name="kernel"))
    with pytest.raises(DomainConfigError, match="8B"):
        load_domain("kernel", domains_dir=d,
                   model_resolver=lambda name: _FakeResolved(70.0))


def test_student_under_8b_accepted(tmp_path):
    d = _write_domain(tmp_path, "kernel", VALID_YAML.format(name="kernel"))
    domain = load_domain("kernel", domains_dir=d,
                         model_resolver=lambda name: _FakeResolved(3.0))
    assert domain.student_base == "qwen2.5-3b-instruct"


# ── misc ──────────────────────────────────────────────────────────────

def test_domain_name_pattern_enforced(tmp_path):
    with pytest.raises(DomainConfigError):
        load_domain("Not_Valid!", domains_dir=tmp_path)


def test_missing_domain_file(tmp_path):
    with pytest.raises(DomainConfigError, match="no domain config"):
        load_domain("nope", domains_dir=tmp_path)


def test_canonical_bytes_stable_and_defaults_fold_in(tmp_path):
    explicit = VALID_YAML.format(name="a")
    minimal = """\
student:
  base: qwen2.5-3b-instruct
teacher:
  profile: local
  model: auto
corpus:
  sources: [git:linux, cve:vulns]
  cutoff: "2026-06-01"
eval:
  cert_set: frozen
  eyeball_prompts: b.eyeball.txt
  dev_fraction: 0.1
fidelity:
  target: 0.75
"""
    d1 = _write_domain(tmp_path, "a", explicit)
    d2 = _write_domain(tmp_path, "b", minimal)
    domain_a = load_domain("a", domains_dir=d1)
    domain_b = load_domain("b", domains_dir=d2)
    # Same content modulo name/shard/eyeball-path -> same canonical shape.
    a_bytes = domain_a.canonical_bytes()
    b_bytes = domain_b.canonical_bytes()
    assert a_bytes != b_bytes  # different shard/eyeball_prompts strings
    assert domain_a.eval_cert_n == domain_b.eval_cert_n == 800
    assert domain_a.distill_top_n == domain_b.distill_top_n == 20
