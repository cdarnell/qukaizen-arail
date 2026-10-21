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
    monkeypatch.delenv("ARAIL_ENV_PREF_A", raising=False)
    monkeypatch.delenv("ARAIL_ENV_PREF_B", raising=False)
    _saved = {k: os.environ.get(k) for k in ("MODEL_NAME", "AEROLLM_MODEL",
                                  "ARAIL_ENV_PREF_A", "ARAIL_ENV_PREF_B")}
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


def test_example_file_is_valid_v2_yaml_with_both_pins_unset():
    """The committed .example file must parse as v2, pin nothing (so copying
    it changes nothing), and read back through model_defaults.read()."""
    import yaml
    example = Path(__file__).resolve().parent.parent / "model_defaults.yaml.example"
    assert example.exists()
    data = yaml.safe_load(example.read_text(encoding="utf-8"))
    assert data["version"] == 2
    assert data["pins"] == {"a": None, "b": None}
    got = model_defaults.read(example)
    assert got["version"] == 2 and got["pins"] == {"a": None, "b": None}


# ---------------------------------------------------------------------------
# v2: pins + resolved cache (sprint 2026-10-20-boot-model-autoresolve)
# ---------------------------------------------------------------------------

import warnings

import yaml


def _res(a="llama-ai-eng:latest", b=None):
    return {"resolved_at": "2026-10-20T00:00:00+0000",
            "a": {"model": a, "source": "auto", "reason": None, "size_gb": 1.3},
            "b": {"model": b, "source": "none" if not b else "auto",
                  "reason": None if b else "pinned off", "size_gb": None}}


def test_write_pin_roundtrips_and_preserves_cache(tmp_path):
    f = tmp_path / "model_defaults.yaml"
    model_defaults.write_cache(_res(), path=f)
    model_defaults.write_pin("a", "qwen3:4b", path=f)
    data = model_defaults.read(f)
    assert data["pins"] == {"a": "qwen3:4b", "b": None}
    assert data["resolved"]["a"]["model"] == "llama-ai-eng:latest"


def test_write_cache_preserves_pins_byte_stable(tmp_path):
    f = tmp_path / "model_defaults.yaml"
    model_defaults.write_pin("a", "qwen3:4b", path=f)
    model_defaults.write_pin("b", "none", path=f)
    before = yaml.safe_load(f.read_text())["pins"]
    model_defaults.write_cache(_res(), path=f)
    model_defaults.write_cache(_res(a="other:1b"), path=f)
    after = yaml.safe_load(f.read_text())
    assert after["pins"] == before == {"a": "qwen3:4b", "b": "none"}
    assert after["version"] == 2
    assert after["resolved"]["a"]["model"] == "other:1b"


def test_write_pin_none_only_for_slot_b_and_auto_clears(tmp_path):
    f = tmp_path / "model_defaults.yaml"
    with pytest.raises(ValueError):
        model_defaults.write_pin("a", "none", path=f)
    with pytest.raises(ValueError):
        model_defaults.write_pin("c", "x", path=f)
    model_defaults.write_pin("b", "none", path=f)
    assert model_defaults.read(f)["pins"]["b"] == "none"
    model_defaults.write_pin("b", "auto", path=f)
    assert model_defaults.read(f)["pins"]["b"] is None


def test_apply_v2_stamps_resolved_and_clears_b_when_none(tmp_path, monkeypatch):
    f = tmp_path / "model_defaults.yaml"
    model_defaults.write_cache(_res(a="llama-ai-eng:latest", b=None), path=f)
    monkeypatch.setenv("AEROLLM_MODEL", "Qwen2.5-7B-Instruct-4bit")
    monkeypatch.setenv("MODEL_NAME", "from-dotenv")
    monkeypatch.delenv("ARAIL_ENV_PREF_A", raising=False)
    monkeypatch.delenv("ARAIL_ENV_PREF_B", raising=False)
    try:
        model_defaults.apply(f)
        assert os.environ["MODEL_NAME"] == "llama-ai-eng:latest"
        assert "AEROLLM_MODEL" not in os.environ
        # the .env value is remembered as a preference, not lost
        assert model_defaults.env_preference() == {
            "a": "from-dotenv", "b": "Qwen2.5-7B-Instruct-4bit"}
        model_defaults.apply(f)  # idempotent: marker keeps the first capture
        assert model_defaults.env_preference()["a"] == "from-dotenv"
    finally:
        os.environ.pop("ARAIL_ENV_PREF_A", None)
        os.environ.pop("ARAIL_ENV_PREF_B", None)


def test_apply_v2_without_cache_uses_pins(tmp_path):
    f = tmp_path / "model_defaults.yaml"
    model_defaults.write_pin("a", "qwen3:4b", path=f)
    try:
        assert model_defaults.apply(f)["default_a"] == "qwen3:4b"
    finally:
        os.environ.pop("ARAIL_ENV_PREF_A", None)


def test_v1_file_reads_as_pins_with_null_b_as_none(tmp_path):  # FM11
    f = tmp_path / "model_defaults.yaml"
    f.write_text("default_a: ai-engineer:latest\ndefault_b: null\n")
    data = model_defaults.read(f)
    assert data["legacy"] is True
    assert data["pins"] == {"a": "ai-engineer:latest", "b": "none"}


def test_migrate_v1_rewrites_v2_with_backup_once(tmp_path):  # FM11
    f = tmp_path / "model_defaults.yaml"
    original = "# operator note: Qwen SIGABRTs\ndefault_a: ai-engineer:latest\ndefault_b: null\n"
    f.write_text(original)
    assert model_defaults.migrate_v1(f) is True
    data = yaml.safe_load(f.read_text())
    assert data["version"] == 2
    assert data["pins"] == {"a": "ai-engineer:latest", "b": "none"}
    assert "migrated from v1" in data["pin_notes"]["b"]
    bak = tmp_path / ".model_defaults.v1.bak"
    assert bak.read_text() == original
    assert model_defaults.migrate_v1(f) is False      # already v2


def test_write_cache_unwritable_returns_false_never_raises(tmp_path, monkeypatch):  # FM10
    f = tmp_path / "model_defaults.yaml"

    def boom(*a, **k):
        raise OSError("read-only")
    monkeypatch.setattr(os, "replace", boom)
    assert model_defaults.write_cache(_res(), path=f) is False


def test_corrupt_file_reads_as_no_pins(tmp_path):  # FM10
    f = tmp_path / "model_defaults.yaml"
    f.write_text("{{{ not yaml")
    data = model_defaults.read(f)
    assert data["ok"] is False and data["pins"] == {"a": None, "b": None}


def test_update_warm_records_status(tmp_path):
    f = tmp_path / "model_defaults.yaml"
    model_defaults.write_cache(_res(), path=f)
    model_defaults.update_warm("a", "ok", path=f)
    assert model_defaults.read(f)["resolved"]["warm"] == {"a": "ok"}


def test_write_defaults_is_deprecated_shim_mapping_to_pins(tmp_path):
    f = tmp_path / "model_defaults.yaml"
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        written = model_defaults.write_defaults("llama-ai-eng", None, path=f)
    assert written == f
    assert any(issubclass(x.category, DeprecationWarning) for x in w)
    assert model_defaults.read(f)["pins"] == {"a": "llama-ai-eng", "b": "none"}


def test_write_pin_is_atomic_no_partial_file_on_crash(tmp_path, monkeypatch):
    f = tmp_path / "model_defaults.yaml"

    def _boom(*a, **kw):
        raise OSError("simulated crash")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError):
        model_defaults.write_pin("a", "llama-ai-eng", path=f)
    assert not f.exists()
    assert list(tmp_path.glob(".model_defaults.*.tmp")) == []


def test_write_pin_respects_path_override_precedence(monkeypatch, tmp_path):
    env_file = tmp_path / "env.yaml"
    explicit_file = tmp_path / "explicit.yaml"
    monkeypatch.setenv("ARAIL_MODEL_DEFAULTS_FILE", str(env_file))
    assert model_defaults.write_pin("a", "x", path=explicit_file) == explicit_file
    assert not env_file.exists()
    assert model_defaults.write_pin("a", "x") == env_file


def test_concurrent_write_pin_during_cache_write_keeps_pin(tmp_path):  # FM13
    import threading
    f = tmp_path / "model_defaults.yaml"
    model_defaults.write_pin("a", "qwen3:4b", path=f)
    stop = threading.Event()

    def spam():
        while not stop.is_set():
            model_defaults.write_cache(_res(), path=f)
    t = threading.Thread(target=spam)
    t.start()
    try:
        for _ in range(20):
            model_defaults.write_pin("b", "none", path=f)
    finally:
        stop.set()
        t.join()
    assert model_defaults.read(f)["pins"]["a"] == "qwen3:4b"


# ---------------------------------------------------------------------------
# resolve_slots() / _gather_slot_facts() / report() — projections of the cache
# ---------------------------------------------------------------------------

def test_resolve_slots_without_file_falls_back_to_builtin(monkeypatch, tmp_path):
    monkeypatch.setenv("ARAIL_MODEL_DEFAULTS_FILE", str(tmp_path / "m.yaml"))
    result = model_defaults.resolve_slots()
    assert "settled" not in result
    assert result["default_a"] == "llama-ai-eng"
    assert result["default_b"] is None


def test_resolve_slots_reports_source_and_reason_from_cache(monkeypatch, tmp_path):
    f = tmp_path / "model_defaults.yaml"
    monkeypatch.setenv("ARAIL_MODEL_DEFAULTS_FILE", str(f))
    model_defaults.write_cache(_res(), path=f)
    monkeypatch.setenv("MODEL_NAME", "llama-ai-eng:latest")
    result = model_defaults.resolve_slots()
    assert result["a"]["source"] == "auto"
    assert result["b"]["reason"] == "pinned off"
    assert result["version"] == 2


def test_gather_slot_facts_is_a_projection_without_network(monkeypatch, tmp_path):
    f = tmp_path / "model_defaults.yaml"
    monkeypatch.setenv("ARAIL_MODEL_DEFAULTS_FILE", str(f))
    model_defaults.write_cache(_res(), path=f)
    monkeypatch.setenv("MODEL_NAME", "llama-ai-eng:latest")
    monkeypatch.delenv("AEROLLM_MODEL", raising=False)

    def no_net(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr("arail.chat._ollama_installed_models", no_net)
    facts = model_defaults._gather_slot_facts()
    assert facts["a"]["model"] == "llama-ai-eng:latest"
    assert facts["a"]["size_gb"] == 1.3
    assert facts["a"]["install_command"] is None
    assert facts["b"]["model"] is None and facts["b"]["reason"] == "pinned off"


def test_report_never_raises_and_mentions_both_slots_with_source(monkeypatch, tmp_path):
    f = tmp_path / "model_defaults.yaml"
    monkeypatch.setenv("ARAIL_MODEL_DEFAULTS_FILE", str(f))
    model_defaults.write_cache(_res(), path=f)
    text = model_defaults.report()
    assert "A (resident):" in text and "B (aeroLLM):" in text
    assert "source: auto" in text and "pinned off" in text


# ---------------------------------------------------------------------------
# CLI (python -m arail.model_defaults) — the start.sh readiness-banner surface
# ---------------------------------------------------------------------------

def _cli(tmp_path, *args):
    env = dict(os.environ)
    env["ARAIL_MODEL_DEFAULTS_FILE"] = str(tmp_path / "model_defaults.yaml")
    env["ARAIL_ENV_FILE"] = str(tmp_path / "env")
    for k in ("MODEL_NAME", "AEROLLM_MODEL", "ARAIL_ENV_PREF_A", "ARAIL_ENV_PREF_B"):
        env.pop(k, None)
    return subprocess.run([sys.executable, "-m", "arail.model_defaults", *args],
                          capture_output=True, text=True, env=env, cwd=_REPO_ROOT)


def test_cli_get_default_a_unsettled_prints_builtin_fallback(tmp_path):
    result = _cli(tmp_path, "--get", "default_a")
    assert result.returncode == 0
    assert result.stdout.strip() == "llama-ai-eng"


def test_cli_get_reads_resolved_cache(tmp_path):
    model_defaults.write_cache(_res(a="qwen3:4b"), path=tmp_path / "model_defaults.yaml")
    assert _cli(tmp_path, "--get", "default_a").stdout.strip() == "qwen3:4b"


def test_cli_banner_prints_models_block(tmp_path):
    model_defaults.write_cache(_res(), path=tmp_path / "model_defaults.yaml")
    result = _cli(tmp_path, "--banner")
    assert result.returncode == 0
    assert "Models:" in result.stdout and "source: auto" in result.stdout


def test_cli_json_v2_shape(tmp_path):
    model_defaults.write_cache(_res(), path=tmp_path / "model_defaults.yaml")
    result = _cli(tmp_path, "--json")
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["version"] == 2
    assert {"pins", "resolved"} <= set(payload)
    assert "settled" not in payload
    assert payload["resolved"]["a"]["model"] == "llama-ai-eng:latest"
