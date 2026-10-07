"""model_defaults.yaml — the one file to check for what's active.

Historically "which model does chat use" and "which model does AeroLLM
load" were each answered by a chain of .env vars, hardcoded fallback
constants, and installed-model detection spread across app.py and
router/backends.py. This is the single, authoritative source for those
two settings: `apply()` stamps its values into the SAME env vars (
MODEL_NAME, AEROLLM_MODEL) every existing call site already reads, so
nothing downstream needs to change to respect it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from arail import model_defaults

_REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """`apply()` writes MODEL_NAME/AEROLLM_MODEL straight to os.environ —
    by design (it must win over whatever .env set, the same way the
    portal's other os.environ-direct writers, e.g. the airgap toggle,
    intentionally bypass monkeypatch). That means monkeypatch's own
    teardown can't undo it: a test here that calls apply() directly
    (with an explicit path=, not through arail.config) leaves a real,
    persistent mutation that otherwise survives for the rest of the
    pytest session and can break unrelated tests elsewhere (confirmed —
    this exact leak once made test_aerollm_model_ready.py fail only when
    run after this file). Restore-by-hand in teardown, matching
    conftest.py's isolated_secrets fixture for the identical problem
    shape."""
    monkeypatch.delenv("MODEL_NAME", raising=False)
    monkeypatch.delenv("AEROLLM_MODEL", raising=False)
    _saved = {k: os.environ.get(k) for k in ("MODEL_NAME", "AEROLLM_MODEL")}
    yield
    for k, v in _saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_missing_file_overrides_nothing(tmp_path):
    result = model_defaults.apply(tmp_path / "does-not-exist.yaml")
    assert result == {}
    assert "MODEL_NAME" not in os.environ
    assert "AEROLLM_MODEL" not in os.environ


def test_default_a_stamps_model_name(tmp_path):
    f = tmp_path / "model_defaults.yaml"
    f.write_text("default_a: ai-engineer:latest\n", encoding="utf-8")
    result = model_defaults.apply(f)
    assert os.environ["MODEL_NAME"] == "ai-engineer:latest"
    assert result["default_a"] == "ai-engineer:latest"


def test_default_b_stamps_aerollm_model(tmp_path):
    f = tmp_path / "model_defaults.yaml"
    f.write_text("default_b: Qwen3-8B-4bit\n", encoding="utf-8")
    result = model_defaults.apply(f)
    assert os.environ["AEROLLM_MODEL"] == "Qwen3-8B-4bit"
    assert result["default_b"] == "Qwen3-8B-4bit"


def test_explicit_null_default_b_clears_a_stale_env_value(tmp_path, monkeypatch):
    """The honesty guarantee: an explicit `default_b: null` must win over
    whatever .env already set — "not configured" must not be silently
    overridden by a stale environment value from before this file
    existed."""
    monkeypatch.setenv("AEROLLM_MODEL", "some-stale-value-from-dot-env")
    f = tmp_path / "model_defaults.yaml"
    f.write_text("default_b: null\n", encoding="utf-8")
    result = model_defaults.apply(f)
    assert "AEROLLM_MODEL" not in os.environ
    assert result["default_b"] is None


def test_omitting_default_b_entirely_leaves_env_untouched(tmp_path, monkeypatch):
    """Distinguish 'the key is absent' (don't touch anything — some other
    mechanism, e.g. .env, may still be setting this) from 'the key is
    explicitly null' (clear it) — same distinction os.getenv's own
    default-vs-present-but-empty semantics needed getting right for
    AEROLLM_MODEL earlier tonight."""
    monkeypatch.setenv("AEROLLM_MODEL", "untouched-value")
    f = tmp_path / "model_defaults.yaml"
    f.write_text("default_a: ai-engineer:latest\n", encoding="utf-8")
    model_defaults.apply(f)
    assert os.environ["AEROLLM_MODEL"] == "untouched-value"


def test_malformed_yaml_never_raises(tmp_path):
    f = tmp_path / "model_defaults.yaml"
    f.write_text("not: valid: yaml: [[[", encoding="utf-8")
    result = model_defaults.apply(f)
    assert result == {}


def test_non_mapping_yaml_never_raises(tmp_path):
    f = tmp_path / "model_defaults.yaml"
    f.write_text("- just\n- a\n- list\n", encoding="utf-8")
    result = model_defaults.apply(f)
    assert result == {}


def test_empty_file_overrides_nothing(tmp_path):
    f = tmp_path / "model_defaults.yaml"
    f.write_text("", encoding="utf-8")
    result = model_defaults.apply(f)
    assert result == {}
    assert "MODEL_NAME" not in os.environ


def test_config_module_applies_it_at_import(monkeypatch, tmp_path):
    """The actual integration point: arail.config calls apply() right
    after load_dotenv(), so every existing MODEL_NAME/AEROLLM_MODEL
    reader across the codebase sees this file's values for free.

    Goes through ARAIL_MODEL_DEFAULTS_FILE (not chdir) — that's the real
    override path used everywhere else (config.py, and the session-level
    isolation in conftest.py that keeps a developer's real
    model_defaults.yaml from leaking into every other test)."""
    import importlib
    real = tmp_path / "model_defaults.yaml"
    real.write_text("default_a: test-model:latest\ndefault_b: test-deep-model\n", encoding="utf-8")
    monkeypatch.setenv("ARAIL_MODEL_DEFAULTS_FILE", str(real))
    from arail import config as config_mod
    importlib.reload(config_mod)
    assert os.environ.get("MODEL_NAME") == "test-model:latest"
    assert os.environ.get("AEROLLM_MODEL") == "test-deep-model"


def test_araIl_model_defaults_file_env_override_wins_over_cwd_default(monkeypatch, tmp_path):
    """The isolation mechanism itself, tested directly: with the env var
    set, a real model_defaults.yaml sitting in the CWD must be ignored —
    this is exactly what stops a developer's real file from leaking into
    a test process the way ARAIL_ENV_FILE already prevents for .env."""
    cwd_file = tmp_path / "cwd"
    cwd_file.mkdir()
    (cwd_file / "model_defaults.yaml").write_text(
        "default_a: from-cwd:latest\n", encoding="utf-8"
    )
    override_file = tmp_path / "override.yaml"
    override_file.write_text("default_a: from-override:latest\n", encoding="utf-8")

    monkeypatch.chdir(cwd_file)
    monkeypatch.setenv("ARAIL_MODEL_DEFAULTS_FILE", str(override_file))
    result = model_defaults.apply()
    assert result["default_a"] == "from-override:latest"
    assert os.environ["MODEL_NAME"] == "from-override:latest"


def test_session_isolation_points_at_a_file_that_does_not_exist():
    """The actual conftest.py wiring: ARAIL_MODEL_DEFAULTS_FILE is set
    session-wide before any arail.* import, pointing at a path that
    can never exist — so importing arail.config in ANY test never reads
    whatever is really sitting in this repo's own model_defaults.yaml."""
    override = os.environ.get("ARAIL_MODEL_DEFAULTS_FILE")
    assert override, "conftest.py must set ARAIL_MODEL_DEFAULTS_FILE for every test"
    assert not Path(override).exists()


def test_example_file_is_valid_yaml_and_documents_both_keys():
    """The committed .example file must actually parse and name both
    slots — it's the thing a new operator reads first."""
    import yaml
    example = Path(__file__).resolve().parent.parent / "model_defaults.yaml.example"
    assert example.exists()
    data = yaml.safe_load(example.read_text(encoding="utf-8"))
    assert "default_a" in data
    assert "default_b" in data


# ---------------------------------------------------------------------------
# write_defaults() — the boot-settle endpoint's persistence path
# ---------------------------------------------------------------------------

def test_write_defaults_roundtrips_through_apply(tmp_path):
    f = tmp_path / "model_defaults.yaml"
    written = model_defaults.write_defaults("qwen2.5:7b", "Qwen2.5-7B-Instruct-4bit", path=f)
    assert written == f
    result = model_defaults.apply(f)
    assert result["default_a"] == "qwen2.5:7b"
    assert result["default_b"] == "Qwen2.5-7B-Instruct-4bit"


def test_write_defaults_explicit_none_b_roundtrips_as_null(tmp_path):
    f = tmp_path / "model_defaults.yaml"
    model_defaults.write_defaults("llama-ai-eng", None, path=f)
    import yaml
    data = yaml.safe_load(f.read_text(encoding="utf-8"))
    assert data["default_a"] == "llama-ai-eng"
    assert data["default_b"] is None
    # And apply() must treat that null as "explicitly cleared", not "absent" —
    # same honesty guarantee test_explicit_null_default_b_clears_a_stale_env_value
    # pins for hand-authored files.
    os.environ["AEROLLM_MODEL"] = "some-stale-value"
    result = model_defaults.apply(f)
    assert "AEROLLM_MODEL" not in os.environ
    assert result["default_b"] is None


def test_write_defaults_is_atomic_no_partial_file_on_crash(tmp_path, monkeypatch):
    """A write that dies mid-flight must never leave a corrupt/partial
    model_defaults.yaml behind — the temp file is written and renamed,
    never the real path opened in place."""
    f = tmp_path / "model_defaults.yaml"

    real_replace = os.replace

    def _boom(*a, **kw):
        raise OSError("simulated crash")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError):
        model_defaults.write_defaults("llama-ai-eng", None, path=f)
    assert not f.exists()
    # No stray .tmp files left behind either.
    assert list(tmp_path.glob(".model_defaults.*.tmp")) == []
    monkeypatch.setattr(os, "replace", real_replace)


def test_write_defaults_respects_path_override_precedence(monkeypatch, tmp_path):
    """Same precedence apply() uses: explicit path= wins over
    ARAIL_MODEL_DEFAULTS_FILE wins over the CWD default."""
    env_file = tmp_path / "env.yaml"
    explicit_file = tmp_path / "explicit.yaml"
    monkeypatch.setenv("ARAIL_MODEL_DEFAULTS_FILE", str(env_file))
    written = model_defaults.write_defaults("a", "b", path=explicit_file)
    assert written == explicit_file
    assert explicit_file.exists()
    assert not env_file.exists()


def test_write_defaults_without_explicit_path_uses_env_override(monkeypatch, tmp_path):
    env_file = tmp_path / "env.yaml"
    monkeypatch.setenv("ARAIL_MODEL_DEFAULTS_FILE", str(env_file))
    written = model_defaults.write_defaults("llama-ai-eng", None)
    assert written == env_file
    assert env_file.exists()


# ---------------------------------------------------------------------------
# resolve_slots() — "settled" detection the boot banner's mode hinges on
# ---------------------------------------------------------------------------

def test_resolve_slots_reports_unsettled_when_file_absent(monkeypatch, tmp_path):
    f = tmp_path / "model_defaults.yaml"
    monkeypatch.setenv("ARAIL_MODEL_DEFAULTS_FILE", str(f))
    result = model_defaults.resolve_slots()
    assert result["settled"] is False
    assert result["default_a"] == "llama-ai-eng"  # builtin fallback
    assert result["default_b"] is None


def test_resolve_slots_reports_settled_after_write(monkeypatch, tmp_path):
    f = tmp_path / "model_defaults.yaml"
    monkeypatch.setenv("ARAIL_MODEL_DEFAULTS_FILE", str(f))
    model_defaults.write_defaults("qwen3:8b", None, path=f)
    model_defaults.apply(f)
    result = model_defaults.resolve_slots()
    assert result["settled"] is True
    assert result["default_a"] == "qwen3:8b"


def test_resolve_slots_settled_requires_truthy_default_a(monkeypatch, tmp_path):
    """An empty/blank default_a must not count as settled — the picker
    should still show up rather than silently trusting a garbage file."""
    f = tmp_path / "model_defaults.yaml"
    f.write_text("default_a: \"\"\ndefault_b: null\n", encoding="utf-8")
    monkeypatch.setenv("ARAIL_MODEL_DEFAULTS_FILE", str(f))
    result = model_defaults.resolve_slots()
    assert result["settled"] is False


# ---------------------------------------------------------------------------
# _gather_slot_facts() / report() — presence, size, fit, install command
# ---------------------------------------------------------------------------

def test_gather_slot_facts_slot_a_present_via_ollama(monkeypatch, tmp_path):
    f = tmp_path / "model_defaults.yaml"
    monkeypatch.setenv("ARAIL_MODEL_DEFAULTS_FILE", str(f))
    monkeypatch.setenv("MODEL_NAME", "llama-ai-eng")
    monkeypatch.setattr(
        "arail.chat._ollama_installed_models",
        lambda *a, **kw: [{"id": "llama-ai-eng", "runtime": "ollama",
                            "size_gb": 0.9, "modified": "", "endpoint": "x"}])
    facts = model_defaults._gather_slot_facts()
    assert facts["a"]["present"] is True
    assert facts["a"]["size_gb"] == 0.9
    assert facts["a"]["install_command"] is None


def test_gather_slot_facts_slot_a_absent_carries_install_command(monkeypatch, tmp_path):
    f = tmp_path / "model_defaults.yaml"
    monkeypatch.setenv("ARAIL_MODEL_DEFAULTS_FILE", str(f))
    monkeypatch.setenv("MODEL_NAME", "not-installed:latest")
    monkeypatch.setattr("arail.chat._ollama_installed_models", lambda *a, **kw: [])
    facts = model_defaults._gather_slot_facts()
    assert facts["a"]["present"] is False
    assert facts["a"]["install_command"] == "ollama pull not-installed:latest"


def test_gather_slot_facts_slot_b_none_when_unconfigured(monkeypatch, tmp_path):
    f = tmp_path / "model_defaults.yaml"
    monkeypatch.setenv("ARAIL_MODEL_DEFAULTS_FILE", str(f))
    monkeypatch.delenv("AEROLLM_MODEL", raising=False)
    facts = model_defaults._gather_slot_facts()
    assert facts["b"] is None


def test_gather_slot_facts_slot_b_present_reports_fit_and_size(tmp_path, monkeypatch):
    model_dir = tmp_path / "models" / "Qwen2.5-7B-Instruct-4bit"
    model_dir.mkdir(parents=True)
    (model_dir / "weights.bin").write_bytes(b"0" * 1024)
    monkeypatch.setenv("ARAIL_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("AEROLLM_MODEL", "Qwen2.5-7B-Instruct-4bit")
    facts = model_defaults._gather_slot_facts()
    assert facts["b"]["present"] is True
    assert facts["b"]["fit"] in ("fits", "too_big")  # resolved via name-regex (7B)
    assert facts["b"]["install_command"] is None


def test_gather_slot_facts_slot_b_absent_carries_hf_download_hint(tmp_path, monkeypatch):
    monkeypatch.setenv("ARAIL_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("AEROLLM_MODEL", "never-downloaded")
    facts = model_defaults._gather_slot_facts()
    assert facts["b"]["present"] is False
    assert "hf download mlx-community/never-downloaded" in facts["b"]["install_command"]


def test_report_never_raises_and_mentions_both_slots(monkeypatch, tmp_path):
    monkeypatch.setenv("ARAIL_MODEL_DEFAULTS_FILE", str(tmp_path / "model_defaults.yaml"))
    monkeypatch.setattr("arail.chat._ollama_installed_models", lambda *a, **kw: [])
    text = model_defaults.report()
    assert "A (resident):" in text
    assert "B (QueueLLM):" in text


# ---------------------------------------------------------------------------
# CLI (python -m arail.model_defaults) — the start.sh readiness-banner surface
# ---------------------------------------------------------------------------

def test_cli_get_default_a_unsettled_prints_builtin_fallback(tmp_path, monkeypatch):
    env = dict(os.environ)
    env["ARAIL_MODEL_DEFAULTS_FILE"] = str(tmp_path / "model_defaults.yaml")
    env["ARAIL_ENV_FILE"] = str(tmp_path / "env")
    result = subprocess.run(
        [sys.executable, "-m", "arail.model_defaults", "--get", "default_a"],
        capture_output=True, text=True, env=env, cwd=_REPO_ROOT)
    assert result.returncode == 0
    assert result.stdout.strip() == "llama-ai-eng"


def test_cli_banner_prints_models_block(tmp_path):
    env = dict(os.environ)
    env["ARAIL_MODEL_DEFAULTS_FILE"] = str(tmp_path / "model_defaults.yaml")
    env["ARAIL_ENV_FILE"] = str(tmp_path / "env")
    result = subprocess.run(
        [sys.executable, "-m", "arail.model_defaults", "--banner"],
        capture_output=True, text=True, env=env, cwd=_REPO_ROOT)
    assert result.returncode == 0
    assert "Models:" in result.stdout
    assert "A (resident):" in result.stdout


def test_cli_json_is_valid_json_with_expected_keys(tmp_path):
    env = dict(os.environ)
    env["ARAIL_MODEL_DEFAULTS_FILE"] = str(tmp_path / "model_defaults.yaml")
    env["ARAIL_ENV_FILE"] = str(tmp_path / "env")
    result = subprocess.run(
        [sys.executable, "-m", "arail.model_defaults", "--json"],
        capture_output=True, text=True, env=env, cwd=_REPO_ROOT)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert "settled" in payload and "facts" in payload
    assert payload["settled"] is False


def test_report_banner_columns_align_after_label_rename(monkeypatch, tmp_path):
    """`B (QueueLLM):` is one character wider than the old `B (aeroLLM):`;
    the A and B model columns must still start at the same offset."""
    monkeypatch.setenv("ARAIL_MODEL_DEFAULTS_FILE", str(tmp_path / "model_defaults.yaml"))
    monkeypatch.setattr("arail.chat._ollama_installed_models", lambda *a, **kw: [])
    monkeypatch.setattr(
        model_defaults, "_gather_slot_facts",
        lambda: {
            "a": {"model": "model-a", "size_gb": 4.0, "present": True,
                  "fit": "ok", "install_command": None},
            "b": {"model": "model-b", "size_gb": 8.0, "present": True,
                  "fit": "ok", "cap_b": 30, "install_command": None},
        },
    )
    lines = model_defaults.report().splitlines()
    a_line = next(x for x in lines if "A (resident):" in x)
    b_line = next(x for x in lines if "B (QueueLLM):" in x)
    assert "aeroLLM" not in "\n".join(lines)
    assert a_line.index("model-a") == b_line.index("model-b")
