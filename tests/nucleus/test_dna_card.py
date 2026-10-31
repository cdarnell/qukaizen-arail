"""arail.nucleus.cards.dna_v2 (T-CARD-1..3)."""

from __future__ import annotations

import pytest

from arail.nucleus.cards.dna_v2 import (
    build_card, card_sha256, dump_card_yaml, not_run, validate_card,
)
from arail.nucleus.errors import DomainConfigError


def _golden_card() -> dict:
    return build_card(
        shard="qkz-linux-kernel-mini", version="0.1.0", built="2026-09-23T00:00:00+00:00",
        pipeline_hash="sha256:abc", eval_hash="sha256:def", runtime="stub", lab_mode="airgapped",
        distillation={
            "mode": "logit",
            "teacher": {"profile": "local", "model": "stub-teacher", "tokenizer": "stub",
                       "provenance": {"runtime": "stub"}, "selection": "stub selection"},
            "student": {"base": "qwen2.5-3b-instruct", "tokenizer": "qwen2", "method": "lora"},
            "tokenizer_parity": True, "tokenizer_parity_detail": "exact",
            "logit_source": "teacher_generated_topn", "top_n": 5, "renorm": "topn_softmax",
            "captured_mass": 0.95, "dropped_mass": 0.05,
        },
        splits={"corpus_cutoff": "2026-06-01", "dev": {"n": 3, "seen_by_arbitrage": True},
               "cert": {"n": 20, "seen_by_arbitrage": False, "frozen": True, "version": "cert-v1"}},
        contamination={"method": "13gram+exact-sha", "overlap_train_vs_cert": 0.0, "temporal_leak": "none"},
        corpus={"sources": [{"id": "git:linux", "license": "GPL-2.0-only",
                            "redistributable": True, "origin_commit": "abc123", "items": 50}]},
        closed_ended={"cve_detection": {"precision": 0.8, "recall": 0.7, "f1": 0.75, "n": 20, "support": 5},
                     "subsystem_routing": {"macro_f1": 0.6, "per_class": {}, "n": 20}},
        open_ended={"patch_explanation": not_run("no fixture judge wired at this commit")},
        executable={"patch_applies": not_run("no repo wired"), "compiles": not_run("no build host"),
                   "checkpatch_clean": not_run("no repo wired")},
        composite={"formula_id": "composite/v1-nc", "formula": "0.4*a+0.3*b+0.3*c", "value": 0.6},
        fidelity={"target": 0.6, "achieved": 0.6, "decision": "CERTIFIED"},
    )


# ── T-CARD-1: golden stub card validates against the schema ──────────

def test_golden_stub_card_validates():
    card = _golden_card()
    validate_card(card)  # no raise


def test_invalid_card_missing_required_field_raises():
    card = _golden_card()
    del card["composite"]
    with pytest.raises(DomainConfigError):
        validate_card(card)


def test_accuracy_key_rejected_by_schema():
    card = _golden_card()
    card["closed_ended"]["cve_detection"]["accuracy"] = 0.95
    with pytest.raises(DomainConfigError):
        validate_card(card)


def test_unknown_top_level_key_rejected():
    card = _golden_card()
    card["mystery_field"] = 1
    with pytest.raises(DomainConfigError):
        validate_card(card)


# ── T-CARD-2: YAML dump -> load -> canonical hash stable; built stays a string ──

def test_built_stays_string_through_yaml_round_trip():
    card = _golden_card()
    text = dump_card_yaml(card)
    import yaml

    reloaded = yaml.safe_load(text)
    assert isinstance(reloaded["built"], str)
    assert reloaded["built"] == card["built"]


def test_card_sha256_stable_across_reruns():
    card = _golden_card()
    assert card_sha256(card) == card_sha256(card)


def test_card_sha256_excludes_signed():
    card = _golden_card()
    unsigned_hash = card_sha256(card)
    signed_card = dict(card)
    signed_card["signed"] = {"signature_hex": "deadbeef"}
    assert card_sha256(signed_card) == unsigned_hash


def test_card_sha256_changes_when_a_metric_changes():
    card_a = _golden_card()
    card_b = _golden_card()
    card_b["closed_ended"]["cve_detection"]["f1"] = 0.5
    assert card_sha256(card_a) != card_sha256(card_b)


# ── T-CARD-3: not_run metrics allowed and rendered ────────────────────

def test_not_run_metrics_allowed():
    card = _golden_card()
    validate_card(card)  # open_ended/executable entries are all not_run above
    assert card["open_ended"]["patch_explanation"]["status"] == "not_run"


def test_write_and_load_card_round_trip(tmp_path):
    from arail.nucleus.cards.dna_v2 import load_card, write_card

    card = _golden_card()
    path = tmp_path / "dna-card.yaml"
    write_card(card, path)
    reloaded = load_card(path)
    assert reloaded["shard"] == card["shard"]
    assert card_sha256(reloaded) == card_sha256(card)


def test_write_card_refuses_invalid():
    from arail.nucleus.cards.dna_v2 import write_card

    card = _golden_card()
    del card["fidelity"]
    with pytest.raises(DomainConfigError):
        write_card(card, __import__("pathlib").Path("/tmp/should-not-be-written.yaml"))
    assert not __import__("pathlib").Path("/tmp/should-not-be-written.yaml").exists()
