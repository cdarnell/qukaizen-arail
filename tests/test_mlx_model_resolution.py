"""The tuning loop must find its model on an airgapped box.

config/tuning-mlx.yml names the research model by HF id
("mlx-community/Qwen2.5-7B-Instruct-4bit"). The lab is airgapped by
default, so an uncached id is not a slow path — mlx_lm raises
LocalEntryNotFoundError and the pass dies at baseline, while the
checkpoint sits unused in ARAIL_MODELS_DIR.
"""
from __future__ import annotations

import pytest

from arail.experiments import mlx_backend as mb


def _make_model_dir(root, name):
    d = root / name
    d.mkdir(parents=True)
    (d / "config.json").write_text("{}")
    return d


def test_hf_id_resolves_to_a_local_checkout(tmp_path, monkeypatch):
    local = _make_model_dir(tmp_path, "Qwen2.5-7B-Instruct-4bit")
    monkeypatch.setattr("arail.config.MODELS_DIR", str(tmp_path))
    assert mb.resolve_model_path(
        "mlx-community/Qwen2.5-7B-Instruct-4bit") == str(local)


def test_unknown_id_is_handed_back_untouched(tmp_path, monkeypatch):
    """A hybrid lab with a warm HF cache must keep working."""
    monkeypatch.setattr("arail.config.MODELS_DIR", str(tmp_path))
    assert mb.resolve_model_path("mlx-community/Nope-9B") == "mlx-community/Nope-9B"


def test_an_explicit_directory_is_left_alone(tmp_path, monkeypatch):
    local = _make_model_dir(tmp_path, "Some-Model")
    monkeypatch.setattr("arail.config.MODELS_DIR", str(tmp_path / "elsewhere"))
    assert mb.resolve_model_path(str(local)) == str(local)


def test_a_directory_without_config_json_is_not_a_model(tmp_path, monkeypatch):
    (tmp_path / "Qwen2.5-7B-Instruct-4bit").mkdir()
    monkeypatch.setattr("arail.config.MODELS_DIR", str(tmp_path))
    assert mb.resolve_model_path(
        "mlx-community/Qwen2.5-7B-Instruct-4bit"
    ) == "mlx-community/Qwen2.5-7B-Instruct-4bit"


def test_empty_id_is_passed_through(tmp_path, monkeypatch):
    monkeypatch.setattr("arail.config.MODELS_DIR", str(tmp_path))
    assert mb.resolve_model_path("") == ""


def test_knob_override_is_also_resolved(tmp_path, monkeypatch):
    """model_quant_variant picks a different checkpoint — it needs the
    same local resolution, or a variant sweep dies where baseline lived."""
    local = _make_model_dir(tmp_path, "Qwen2.5-7B-Instruct-8bit")
    monkeypatch.setattr("arail.config.MODELS_DIR", str(tmp_path))
    got = mb._pick_model_id(
        {"model_quant_variant": "mlx-community/Qwen2.5-7B-Instruct-8bit"},
        fallback="mlx-community/Qwen2.5-7B-Instruct-4bit")
    assert got == str(local)


def test_fallback_is_resolved_when_no_override(tmp_path, monkeypatch):
    local = _make_model_dir(tmp_path, "Qwen2.5-7B-Instruct-4bit")
    monkeypatch.setattr("arail.config.MODELS_DIR", str(tmp_path))
    assert mb._pick_model_id(
        {}, fallback="mlx-community/Qwen2.5-7B-Instruct-4bit") == str(local)


# ── Runnability, not just schema-validity ───────────────────────────
#
# The first real tuning pass ran the kv-8bit variant three times, got
# three identical NotImplementedErrors from mlx_lm, and reported "no
# measurable tok/s" — a message that names neither the knob nor the
# cause. max_kv_size defaults to 4096, which makes the KV cache
# rotating, and mlx_lm cannot quantize a rotating cache.

def test_kv_quantization_with_a_rotating_cache_is_refused():
    reason = mb.unsupported_combination(
        {"kv_bits": "8bit", "max_kv_size": 4096})
    assert reason and "RotatingKVCache" in reason


@pytest.mark.parametrize("bits", ["8bit", "4bit"])
def test_every_quantized_kv_setting_is_caught(bits):
    assert mb.unsupported_combination({"kv_bits": bits, "max_kv_size": 4096})


def test_kv_quantization_without_a_cache_cap_is_allowed():
    """Exactly the combination that ran fine standalone at 16.7 tok/s."""
    assert mb.unsupported_combination({"kv_bits": "8bit"}) is None
    assert mb.unsupported_combination(
        {"kv_bits": "8bit", "max_kv_size": 0}) is None


def test_fp16_kv_with_a_cache_cap_is_allowed():
    assert mb.unsupported_combination(
        {"kv_bits": "fp16", "max_kv_size": 4096}) is None


def test_unrelated_knobs_are_allowed():
    assert mb.unsupported_combination({"prefill_step_size": 1024}) is None
    assert mb.unsupported_combination({}) is None
