"""arail.nucleus.cards.seal — legacy-5 seal sign/verify (T-SEAL-1..8)."""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from arail.nucleus.cards import seal as seal_mod
from arail.nucleus.cards.dna_v2 import card_sha256

pytest.importorskip("cryptography")


def _minimal_card(**overrides) -> dict:
    from tests.nucleus.test_dna_card import _golden_card

    card = _golden_card()
    card.update(overrides)
    return card


# ── T-SEAL-1: signed-payload keys == the 5 legacy fields exactly ─────

def test_payload_has_exactly_five_legacy_fields():
    payload = seal_mod.build_payload(pipeline_run_id="run-1", chain_hash="deadbeef",
                                     gate_results=[{"gate_name": "x", "passed": True, "value": "y"}])
    assert set(payload) == {"dna_id", "pipeline_run_id", "chain_hash", "gate_results", "timestamp"}


# ── sign + verify round trip (also exercises T-SEAL-1's shape end to end) ──

def test_sign_and_verify_round_trip(tmp_path):
    key_path = tmp_path / "signing.ed25519"
    card = _minimal_card()
    card_hash = card_sha256(card)
    gate_results = seal_mod.build_gate_results(card_sha256=card_hash, eval_hash=card["eval_hash"],
                                               decision=card["fidelity"]["decision"],
                                               contamination_overlap=0.0021)
    payload = seal_mod.build_payload(pipeline_run_id="build-1", chain_hash="deadbeef" * 8,
                                     gate_results=gate_results)
    sealed = seal_mod.sign(payload, key_path=key_path)
    assert sealed.signed["format"] == "nucleus-seal/legacy-5"
    assert seal_mod.verify_signature(sealed.signed) is True


def test_tampered_signature_fails():
    key_path_payload = seal_mod.build_payload(pipeline_run_id="p", chain_hash="c",
                                              gate_results=[{"gate_name": "g", "passed": True, "value": "v"}])
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        sealed = seal_mod.sign(key_path_payload, key_path=Path(tmp) / "k.ed25519")
        tampered = dict(sealed.signed)
        tampered["chain_hash"] = "different"
        assert seal_mod.verify_signature(tampered) is False


# ── T-SEAL-3: non-ASCII -> SealError ──────────────────────────────────

def test_non_ascii_in_payload_raises():
    payload = seal_mod.build_payload(pipeline_run_id="café", chain_hash="c",
                                     gate_results=[{"gate_name": "g", "passed": True, "value": "v"}])
    with pytest.raises(seal_mod.SealError):
        seal_mod.signed_bytes(payload)


# ── T-SEAL-4: float in gate_results -> SealError ──────────────────────

def test_float_in_gate_results_raises():
    payload = seal_mod.build_payload(pipeline_run_id="p", chain_hash="c",
                                     gate_results=[{"gate_name": "g", "passed": True, "value": 0.5}])
    with pytest.raises(seal_mod.SealError):
        seal_mod.signed_bytes(payload)


def test_contamination_value_is_a_string_not_a_float():
    gate_results = seal_mod.build_gate_results(card_sha256="a", eval_hash="b", decision="CERTIFIED",
                                               contamination_overlap=0.0021)
    contamination_gate = next(g for g in gate_results if g["gate_name"] == "contamination")
    assert isinstance(contamination_gate["value"], str)
    assert contamination_gate["value"] == "0.0021"


# ── T-SEAL-5: untrusted key -> verify key: untrusted ──────────────────

def test_verify_untrusted_key(tmp_path):
    key_path = tmp_path / "signing.ed25519"
    card = _minimal_card()
    card_hash = card_sha256(card)
    gate_results = seal_mod.build_gate_results(card_sha256=card_hash, eval_hash=card["eval_hash"],
                                               decision="CERTIFIED", contamination_overlap=0.0)
    payload = seal_mod.build_payload(pipeline_run_id="build-1", chain_hash="c" * 64, gate_results=gate_results)
    sealed = seal_mod.sign(payload, key_path=key_path)
    card["signed"] = sealed.signed

    empty_trusted = tmp_path / "trusted_keys.txt"
    empty_trusted.write_text("")
    result = seal_mod.verify(card, fast=True, trusted_keys_path=empty_trusted)
    assert result.key == "untrusted"


def test_verify_trusted_key(tmp_path):
    key_path = tmp_path / "signing.ed25519"
    card = _minimal_card()
    card_hash = card_sha256(card)
    gate_results = seal_mod.build_gate_results(card_sha256=card_hash, eval_hash=card["eval_hash"],
                                               decision="CERTIFIED", contamination_overlap=0.0)
    payload = seal_mod.build_payload(pipeline_run_id="build-1", chain_hash="c" * 64, gate_results=gate_results)
    sealed = seal_mod.sign(payload, key_path=key_path)
    card["signed"] = sealed.signed

    trusted = tmp_path / "trusted_keys.txt"
    trusted.write_text(sealed.signed["public_key_hex"] + "\n")
    result = seal_mod.verify(card, fast=True, trusted_keys_path=trusted)
    assert result.key == "trusted"
    assert result.signature == "valid"
    assert result.card_hash == "match"


# ── T-SEAL-6: edit one metric -> card_hash mismatch ───────────────────

def test_verify_detects_edited_card(tmp_path):
    key_path = tmp_path / "signing.ed25519"
    card = _minimal_card()
    card_hash = card_sha256(card)
    gate_results = seal_mod.build_gate_results(card_sha256=card_hash, eval_hash=card["eval_hash"],
                                               decision="CERTIFIED", contamination_overlap=0.0)
    payload = seal_mod.build_payload(pipeline_run_id="build-1", chain_hash="c" * 64, gate_results=gate_results)
    sealed = seal_mod.sign(payload, key_path=key_path)
    card["signed"] = sealed.signed

    trusted = tmp_path / "trusted_keys.txt"
    trusted.write_text(sealed.signed["public_key_hex"] + "\n")

    card["closed_ended"]["cve_detection"]["f1"] = 0.999  # edit after signing
    result = seal_mod.verify(card, fast=True, trusted_keys_path=trusted)
    assert result.card_hash == "mismatch"


# ── T-SEAL-7: key file 0644 -> refuse to sign ─────────────────────────

def test_wrong_key_permissions_refused(tmp_path):
    key_path = tmp_path / "signing.ed25519"
    key_path.write_bytes(os.urandom(32))
    os.chmod(key_path, 0o644)
    with pytest.raises(seal_mod.SealError, match="0600"):
        seal_mod.load_or_generate_key(key_path)


def test_new_key_created_0600(tmp_path):
    key_path = tmp_path / "sub" / "signing.ed25519"
    seal_mod.load_or_generate_key(key_path)
    mode = key_path.stat().st_mode & 0o777
    assert mode == 0o600
    assert (key_path.parent / "signing.pub").is_file()
    assert (key_path.parent / "trusted_keys.txt").is_file()


def test_key_wrong_length_refused(tmp_path):
    key_path = tmp_path / "signing.ed25519"
    key_path.write_bytes(os.urandom(16))
    os.chmod(key_path, 0o600)
    with pytest.raises(seal_mod.SealError, match="32"):
        seal_mod.load_or_generate_key(key_path)


# ── T-SEAL-2 / T-SEAL-8: golden vector against the REAL Rust qkz binary ──

QKZ_BIN = os.getenv("NUCLEUS_QKZ_BIN")


@pytest.mark.requires_qkz_bin
@pytest.mark.skipif(not QKZ_BIN or not os.path.isfile(QKZ_BIN), reason="NUCLEUS_QKZ_BIN not set")
def test_real_qkz_binary_verifies_our_seal(tmp_path):
    key_path = tmp_path / "signing.ed25519"
    card = _minimal_card()
    card_hash = card_sha256(card)
    gate_results = seal_mod.build_gate_results(card_sha256=card_hash, eval_hash=card["eval_hash"],
                                               decision="CERTIFIED", contamination_overlap=0.0021)
    payload = seal_mod.build_payload(pipeline_run_id="build-1", chain_hash="c" * 64, gate_results=gate_results)
    sealed = seal_mod.sign(payload, key_path=key_path)

    seal_json_path = tmp_path / "seal.json"
    seal_json_path.write_text(json.dumps(sealed.seal_json))

    result = subprocess.run(
        [QKZ_BIN, "isotope", "verify", "local-verify-probe", "--from-file", str(seal_json_path)],
        capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr.decode() + result.stdout.decode()


# ── CLI verify verb wiring ─────────────────────────────────────────

def test_cli_verify_verb_reports_trusted(tmp_path, monkeypatch, capsys):
    from arail.nucleus import cli
    from arail.nucleus.cards import seal as seal_mod
    from arail.nucleus.cards.dna_v2 import write_card

    key_path = tmp_path / "keys" / "signing.ed25519"
    card = _minimal_card()
    card_hash = card_sha256(card)
    gate_results = seal_mod.build_gate_results(card_sha256=card_hash, eval_hash=card["eval_hash"],
                                               decision="CERTIFIED", contamination_overlap=0.0)
    payload = seal_mod.build_payload(pipeline_run_id="build-1", chain_hash="c" * 64, gate_results=gate_results)
    sealed = seal_mod.sign(payload, key_path=key_path)
    card["signed"] = sealed.signed

    card_dir = tmp_path / "forge" / "qkz-x" / "0.1.0"
    card_dir.mkdir(parents=True)
    write_card(card, card_dir / "dna-card.yaml")

    trusted = key_path.parent / "trusted_keys.txt"
    trusted.write_text(sealed.signed["public_key_hex"] + "\n")

    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(key_path))
    monkeypatch.setenv("LAB_TIER", "minimalist")

    code = cli.main(["verify", str(card_dir), "--fast"])
    out = capsys.readouterr().out
    assert "key: trusted" in out
    assert code == 0
