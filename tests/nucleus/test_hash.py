"""arail.nucleus.evals.hash — eval_hash + pipeline_hash (T-HASH-1..3)."""

from __future__ import annotations

import copy
import dataclasses

import pytest

from arail.nucleus.evals.hash import EvalHashInputs, assert_closed_world, eval_hash, pipeline_hash


def _base_inputs() -> EvalHashInputs:
    return EvalHashInputs(
        harness_version="1",
        prompts="70726f6d7074",
        few_shot={"bytes_hex": "", "k": 0},
        scoring={
            "metric_defs": ["f1", "macro_f1"], "positive_classes": {"cve_detection": "cve"},
            "composite_formula_id": "composite/v1-nc", "composite_formula": "0.4*a+0.3*b+0.3*c",
            "decision_rule_id": "decision_rule/v1", "judge_rubric": "abc123",
            "judge_model_identity": "sha-judge", "lc_method": "alpacaeval2-lc", "lc_params": {"n": 1000},
            "bootstrap_n": 1000, "bootstrap_seed": 42, "position_seed": 7,
            "executable_checks": ["patch_applies", "checkpatch_clean"],
            "checkpatch_sha256": "deadbeef", "git_version": "2.42.0",
            "contamination_method": "13gram+exact-sha", "contamination_params": {"n_gram": 13},
        },
        decoding={"student": {"temperature": 0.0}, "base": {"temperature": 0.0},
                 "teacher": {"temperature": 0.0}},
        cert_set_version="cert-sha-abc",
    )


def _mutate(inputs: EvalHashInputs, **overrides) -> EvalHashInputs:
    return dataclasses.replace(inputs, **overrides)


# ── T-HASH-1: mutating each included field changes the hash ──────────

def test_harness_version_changes_hash():
    a, b = _base_inputs(), _mutate(_base_inputs(), harness_version="2")
    assert eval_hash(a) != eval_hash(b)


def test_prompts_changes_hash():
    a, b = _base_inputs(), _mutate(_base_inputs(), prompts="646966666572656e74")
    assert eval_hash(a) != eval_hash(b)


def test_few_shot_changes_hash():
    a = _base_inputs()
    b = _mutate(a, few_shot={"bytes_hex": "", "k": 3})
    assert eval_hash(a) != eval_hash(b)


@pytest.mark.parametrize("key,new_value", [
    ("metric_defs", ["f1"]),
    ("positive_classes", {"cve_detection": "not"}),
    ("composite_formula_id", "composite/v1"),
    ("composite_formula", "different string"),
    ("decision_rule_id", "decision_rule/v2"),
    ("judge_rubric", "different-rubric"),
    ("judge_model_identity", "sha-different-judge"),
    ("lc_method", "different-method"),
    ("lc_params", {"n": 500}),
    ("bootstrap_n", 500),
    ("bootstrap_seed", 1),
    ("position_seed", 1),
    ("executable_checks", ["patch_applies"]),
    ("checkpatch_sha256", "different-sha"),
    ("git_version", "2.40.0"),
    ("contamination_method", "different-method"),
    ("contamination_params", {"n_gram": 5}),
])
def test_every_scoring_subfield_changes_hash(key, new_value):
    a = _base_inputs()
    scoring_b = dict(a.scoring)
    scoring_b[key] = new_value
    b = _mutate(a, scoring=scoring_b)
    assert eval_hash(a) != eval_hash(b), f"scoring.{key} did not change the hash"


def test_decoding_changes_hash():
    a = _base_inputs()
    decoding_b = copy.deepcopy(a.decoding)
    decoding_b["student"]["temperature"] = 0.5
    b = _mutate(a, decoding=decoding_b)
    assert eval_hash(a) != eval_hash(b)


def test_cert_set_version_changes_hash():
    a, b = _base_inputs(), _mutate(_base_inputs(), cert_set_version="cert-sha-different")
    assert eval_hash(a) != eval_hash(b)


# ── T-HASH-2: excluded fields never appear, so mutating them (outside
# the dataclass) never changes the hash -- demonstrated by two identical
# EvalHashInputs constructed with different surrounding context ─────────

def test_hash_unaffected_by_surrounding_context():
    a = _base_inputs()
    b = _base_inputs()
    assert eval_hash(a) == eval_hash(b)
    # Nothing about build_id, host, timestamp, output path, training seed,
    # top-N, or student identity is a field on EvalHashInputs at all --
    # there is no way to even pass them in, which is the point.
    field_names = {f.name for f in __import__("dataclasses").fields(EvalHashInputs)}
    for excluded in ("build_id", "host", "timestamp", "output_path",
                     "training_seed", "top_n", "student_identity"):
        assert excluded not in field_names


def test_hash_format_prefix():
    assert eval_hash(_base_inputs()).startswith("sha256:")


def test_hash_deterministic():
    assert eval_hash(_base_inputs()) == eval_hash(_base_inputs())


# ── T-HASH-3: closed-world field set ──────────────────────────────────

def test_closed_world_passes_for_current_dataclass():
    assert_closed_world()  # no raise


def test_closed_world_fails_if_a_field_is_unclassified(monkeypatch):
    import arail.nucleus.evals.hash as hash_mod

    monkeypatch.setattr(hash_mod, "_CLASSIFIED_FIELDS",
                        frozenset({"harness_version", "prompts", "few_shot", "scoring", "decoding"}))
    # cert_set_version is now "missing" from the classified set relative to
    # the dataclass's real fields.
    with pytest.raises(AssertionError, match="drifted"):
        hash_mod.assert_closed_world()


# ── eval-config.lock round trip ────────────────────────────────────────

def test_eval_config_lock_round_trip(tmp_path):
    from arail.nucleus.evals.hash import read_eval_config_lock, write_eval_config_lock

    inputs = _base_inputs()
    lock_path = tmp_path / "eval-config.lock"
    write_eval_config_lock(inputs, lock_path)
    reloaded = read_eval_config_lock(lock_path)
    assert eval_hash(inputs) == eval_hash(reloaded)


# ── pipeline_hash ──────────────────────────────────────────────────────

def test_pipeline_hash_changes_with_domain_bytes(tmp_path):
    src_root = tmp_path / "src"
    src_root.mkdir()
    (src_root / "a.py").write_text("x = 1\n")

    h1 = pipeline_hash(nucleus_src_root=src_root, domain_canonical_bytes=b'{"a":1}',
                       corpus_manifest_sha="m", teacher_identity="t", student_base_identity="s",
                       distill_params={"top_n": 20}, training_hyperparams={"lr": 1e-4})
    h2 = pipeline_hash(nucleus_src_root=src_root, domain_canonical_bytes=b'{"a":2}',
                       corpus_manifest_sha="m", teacher_identity="t", student_base_identity="s",
                       distill_params={"top_n": 20}, training_hyperparams={"lr": 1e-4})
    assert h1 != h2
    assert h1.startswith("sha256:")


def test_pipeline_hash_changes_with_source_bytes(tmp_path):
    src_root = tmp_path / "src"
    src_root.mkdir()
    (src_root / "a.py").write_text("x = 1\n")
    kwargs = dict(domain_canonical_bytes=b'{}', corpus_manifest_sha="m", teacher_identity="t",
                 student_base_identity="s", distill_params={}, training_hyperparams={})
    h1 = pipeline_hash(nucleus_src_root=src_root, **kwargs)
    (src_root / "a.py").write_text("x = 2\n")
    h2 = pipeline_hash(nucleus_src_root=src_root, **kwargs)
    assert h1 != h2
