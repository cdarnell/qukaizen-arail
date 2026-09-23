"""arail.nucleus.models — resolution, identity, teacher selection
(T-PAR-* via models.parity, T-SETUP-2)."""

from __future__ import annotations

import json

import pytest

from arail.nucleus import models
from arail.nucleus.errors import DomainConfigError


def _make_model_dir(root, dirname, *, vocab_size=100, extra_vocab=None,
                    num_parameters=None, weight_bytes=1024, moe=False):
    d = root / dirname
    d.mkdir(parents=True)
    config = {}
    if num_parameters is not None:
        config["num_parameters"] = num_parameters
    if moe:
        config["num_local_experts"] = 8
    (d / "config.json").write_text(json.dumps(config))

    vocab = {f"tok{i}": i for i in range(vocab_size)}
    if extra_vocab:
        vocab.update(extra_vocab)
    tok = {"model": {"vocab": vocab}, "added_tokens": [],
          "normalizer": {"type": "NFC"}, "pre_tokenizer": {"type": "ByteLevel"}}
    (d / "tokenizer.json").write_text(json.dumps(tok))

    if weight_bytes:
        (d / "model.safetensors").write_bytes(b"\x00" * weight_bytes)
    return d


# ── resolve_model ─────────────────────────────────────────────────────

def test_resolve_model_by_alias(tmp_path):
    _make_model_dir(tmp_path, "Qwen2.5-3B-Instruct-4bit", num_parameters=3_000_000_000)
    model = models.resolve_model("qwen2.5-3b-instruct", models_dir=tmp_path)
    assert model.name == "Qwen2.5-3B-Instruct-4bit"
    assert model.params_est_b == pytest.approx(3.0)


def test_resolve_model_by_bare_dirname(tmp_path):
    _make_model_dir(tmp_path, "MyModel")
    model = models.resolve_model("MyModel", models_dir=tmp_path)
    assert model.name == "MyModel"


def test_resolve_model_rejects_path(tmp_path):
    with pytest.raises(DomainConfigError):
        models.resolve_model("../escape", models_dir=tmp_path)
    with pytest.raises(DomainConfigError):
        models.resolve_model("sub/dir", models_dir=tmp_path)


def test_resolve_model_missing_gives_hf_download_hint(tmp_path):
    with pytest.raises(DomainConfigError, match="hf download"):
        models.resolve_model("qwen2.5-3b-instruct", models_dir=tmp_path)


def test_resolve_model_missing_unknown_alias_generic_hint(tmp_path):
    with pytest.raises(DomainConfigError, match="needs network"):
        models.resolve_model("some-custom-model", models_dir=tmp_path)


def test_is_moe_detected():
    _dir_unused = None
    assert models._is_moe({"num_local_experts": 8})
    assert models._is_moe({"model_type": "qwen3_moe"})
    assert not models._is_moe({"model_type": "qwen2"})


def test_params_estimate_from_quantized_weight_bytes():
    # 4-bit quant, 1 GiB of weight bytes -> ~2B params (bytes*8/bits/1e9).
    est = models._estimate_params_b({"quantization": {"bits": 4}}, 1024 ** 3)
    assert est == pytest.approx((1024**3 * 8 / 4) / 1e9)


# ── identity hashing ────────────────────────────────────────────────

def test_model_identity_deterministic(tmp_path):
    _make_model_dir(tmp_path, "M", num_parameters=1e9)
    m1 = models.resolve_model("M", models_dir=tmp_path)
    m2 = models.resolve_model("M", models_dir=tmp_path)
    assert models.model_identity(m1) == models.model_identity(m2)


def test_model_identity_changes_with_config(tmp_path):
    _make_model_dir(tmp_path, "M", num_parameters=1e9)
    m1 = models.resolve_model("M", models_dir=tmp_path)
    id1 = models.model_identity(m1)
    (tmp_path / "M" / "config.json").write_text(json.dumps({"num_parameters": 2e9}))
    m2 = models.resolve_model("M", models_dir=tmp_path)
    assert models.model_identity(m2) != id1


def test_content_hash_caches_and_matches(tmp_path):
    _make_model_dir(tmp_path, "M", weight_bytes=4096)
    m = models.resolve_model("M", models_dir=tmp_path)
    cache_path = tmp_path / "cache.json"
    h1 = models.content_hash(m, cache_path=cache_path)
    assert cache_path.is_file()
    h2 = models.content_hash(m, cache_path=cache_path)
    assert h1 == h2


def test_content_hash_changes_when_weights_change(tmp_path):
    _make_model_dir(tmp_path, "M", weight_bytes=4096)
    m = models.resolve_model("M", models_dir=tmp_path)
    cache_path = tmp_path / "cache.json"
    h1 = models.content_hash(m, cache_path=cache_path)
    (tmp_path / "M" / "model.safetensors").write_bytes(b"\x01" * 4096)
    m2 = models.resolve_model("M", models_dir=tmp_path)
    h2 = models.content_hash(m2, cache_path=cache_path)
    assert h1 != h2


# ── select_teacher ──────────────────────────────────────────────────

def test_select_teacher_excludes_no_parity(tmp_path):
    _make_model_dir(tmp_path, "Student", vocab_size=50)
    _make_model_dir(tmp_path, "BadTeacher", vocab_size=50, extra_vocab={"tok10": 9999})
    _make_model_dir(tmp_path, "GoodTeacher", vocab_size=50, extra_vocab={"extra": 50})
    student = models.resolve_model("Student", models_dir=tmp_path)
    bad = models.resolve_model("BadTeacher", models_dir=tmp_path)
    good = models.resolve_model("GoodTeacher", models_dir=tmp_path)

    chosen, rationale = models.select_teacher(
        True, student, budget_gb=100, exclude=(), candidates=[bad, good])
    assert chosen.name == "GoodTeacher"
    assert "GoodTeacher" in rationale


def test_select_teacher_prefers_moe_and_exact(tmp_path):
    _make_model_dir(tmp_path, "Student", vocab_size=50)
    _make_model_dir(tmp_path, "Dense", vocab_size=50, extra_vocab={"extra": 50},
                    num_parameters=7e9)
    _make_model_dir(tmp_path, "Moe", vocab_size=50, extra_vocab={"extra": 50},
                    num_parameters=30e9, moe=True)
    student = models.resolve_model("Student", models_dir=tmp_path)
    dense = models.resolve_model("Dense", models_dir=tmp_path)
    moe = models.resolve_model("Moe", models_dir=tmp_path)

    chosen, _ = models.select_teacher(
        True, student, budget_gb=1000, exclude=(), candidates=[dense, moe])
    assert chosen.name == "Moe"


def test_select_teacher_excludes_named_models(tmp_path):
    _make_model_dir(tmp_path, "Student", vocab_size=50)
    _make_model_dir(tmp_path, "Excluded", vocab_size=50, extra_vocab={"extra": 50})
    student = models.resolve_model("Student", models_dir=tmp_path)
    excluded = models.resolve_model("Excluded", models_dir=tmp_path)

    with pytest.raises(DomainConfigError):
        models.select_teacher(True, student, budget_gb=100,
                              exclude=("Excluded",), candidates=[excluded])


def test_select_teacher_deterministic_tiebreak_by_name(tmp_path):
    _make_model_dir(tmp_path, "Student", vocab_size=50)
    _make_model_dir(tmp_path, "Alpha", vocab_size=50, extra_vocab={"extra": 50}, num_parameters=1e9)
    _make_model_dir(tmp_path, "Beta", vocab_size=50, extra_vocab={"extra": 50}, num_parameters=1e9)
    student = models.resolve_model("Student", models_dir=tmp_path)
    a = models.resolve_model("Alpha", models_dir=tmp_path)
    b = models.resolve_model("Beta", models_dir=tmp_path)

    chosen, _ = models.select_teacher(True, student, budget_gb=100, exclude=(), candidates=[b, a])
    assert chosen.name == "Alpha"  # alphabetically first, deterministic
