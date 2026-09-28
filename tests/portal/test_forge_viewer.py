"""/forge — read-only DNA-card viewer (T-FORGE-1..3)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LAB_MODE", "airgapped")
    monkeypatch.setattr("arail.config.DATA_DIR", tmp_path / "data")
    monkeypatch.setattr("arail.config.MODELS_DIR", str(tmp_path / "models"))

    import arail.portal.app as app_mod
    with TestClient(app_mod.app) as c:
        yield c


def _write_card(tmp_path, shard="qkz-x", version="0.1.0", *, signed=True, runtime="queuellm"):
    forge_dir = tmp_path / "models" / "forge" / shard / version
    forge_dir.mkdir(parents=True)
    card = {
        "shard": shard, "version": version, "built": "2026-01-01T00:00:00+00:00",
        "pipeline_hash": "sha256:a", "eval_hash": "sha256:b", "runtime": runtime,
        "lab_mode": "airgapped",
        "distillation": {"mode": "logit", "teacher": {}, "student": {}, "tokenizer_parity": True},
        "splits": {}, "contamination": {}, "corpus": {},
        "closed_ended": {}, "open_ended": {}, "executable": {},
        "composite": {"formula_id": "composite/v1-nc", "formula": "x", "value": 0.6},
        "fidelity": {"target": 0.6, "achieved": 0.6, "decision": "CERTIFIED"},
    }
    if signed:
        card["signed"] = {"format": "nucleus-seal/legacy-5", "key_fingerprint": "ephemeral-stub"}
    import yaml

    (forge_dir / "dna-card.yaml").write_text(yaml.safe_dump(card))
    (forge_dir / "build-report.md").write_text(
        "**Decision: CERTIFIED.**\n\n" + "\n".join(f"### {i}. p{i}\n" for i in range(10))
    )
    return forge_dir


# ── T-FORGE-1: renders on minimalist and maximus ──────────────────────

def test_forge_renders_on_minimalist(client, tmp_path, monkeypatch):
    monkeypatch.delenv("LAB_TIER", raising=False)
    resp = client.get("/forge")
    assert resp.status_code == 200
    assert "Model Forge" in resp.text


def test_forge_renders_on_maximus(client, monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    resp = client.get("/forge")
    assert resp.status_code == 200


def test_forge_list_shows_card(client, tmp_path):
    _write_card(tmp_path)
    resp = client.get("/forge")
    assert "qkz-x" in resp.text


def test_forge_detail_page(client, tmp_path):
    _write_card(tmp_path)
    resp = client.get("/forge/qkz-x/0.1.0")
    assert resp.status_code == 200
    assert "CERTIFIED" in resp.text


def test_forge_detail_shows_stub_badge(client, tmp_path):
    _write_card(tmp_path, runtime="stub")
    resp = client.get("/forge/qkz-x/0.1.0")
    assert "STUB" in resp.text or "ephemeral-stub" in resp.text


# ── B2 (2026-09-23 review): a card whose metrics were hand-edited after
# signing must never show a "trusted" badge ──────────────────────────

def test_verify_badge_reports_tampered_on_card_hash_mismatch(tmp_path, monkeypatch):
    pytest.importorskip("cryptography")
    monkeypatch.setattr("arail.config.DATA_DIR", tmp_path / "data")
    monkeypatch.setattr("arail.config.MODELS_DIR", str(tmp_path / "models"))

    from arail.nucleus.cards import seal as seal_mod
    from arail.nucleus.cards.dna_v2 import card_sha256
    from arail.portal import forge_api

    card = {
        "shard": "qkz-x", "version": "0.1.0", "built": "2026-01-01T00:00:00+00:00",
        "pipeline_hash": "sha256:a", "eval_hash": "sha256:b", "runtime": "queuellm",
        "lab_mode": "airgapped",
        "distillation": {"mode": "logit", "teacher": {}, "student": {}, "tokenizer_parity": True},
        "splits": {}, "contamination": {}, "corpus": {},
        "closed_ended": {"cve_detection": {"f1": 0.9}}, "open_ended": {}, "executable": {},
        "composite": {"formula_id": "composite/v1-nc", "formula": "x", "value": 0.6},
        "fidelity": {"target": 0.6, "achieved": 0.6, "decision": "CERTIFIED"},
    }
    key_path = tmp_path / "keys" / "signing.ed25519"
    card_hash = card_sha256(card)
    gate_results = seal_mod.build_gate_results(card_sha256=card_hash, eval_hash=card["eval_hash"],
                                               decision="CERTIFIED", contamination_overlap=0.0)
    payload = seal_mod.build_payload(pipeline_run_id="build-1", chain_hash="c" * 64, gate_results=gate_results)
    sealed = seal_mod.sign(payload, key_path=key_path)
    card["signed"] = sealed.signed

    trusted = key_path.parent / "trusted_keys.txt"
    trusted.write_text(sealed.signed["public_key_hex"] + "\n")
    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(key_path))

    assert forge_api.verify_badge(card) == "trusted"

    tampered = dict(card)
    tampered["closed_ended"] = {"cve_detection": {"f1": 0.999}}  # edited after signing
    assert forge_api.verify_badge(tampered) != "trusted"
    assert forge_api.verify_badge(tampered) == "tampered"


def test_api_forge_cards_json(client, tmp_path):
    _write_card(tmp_path)
    resp = client.get("/api/forge/cards")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cards"][0]["shard"] == "qkz-x"


# ── T-FORGE-2: path traversal / bad semver -> 404 ─────────────────────

def test_forge_detail_path_traversal_404(client):
    resp = client.get("/forge/..%2f..%2fetc/passwd", follow_redirects=False)
    assert resp.status_code == 404


def test_forge_detail_bad_semver_404(client, tmp_path):
    _write_card(tmp_path)
    resp = client.get("/forge/qkz-x/not-a-version")
    assert resp.status_code == 404


def test_forge_detail_missing_card_404(client):
    resp = client.get("/forge/nope/1.0.0")
    assert resp.status_code == 404


def test_forge_detail_bad_shard_chars_404(client):
    resp = client.get("/forge/UPPER_CASE!/1.0.0")
    assert resp.status_code == 404


# ── T-FORGE-3: eyeball output containing <script> renders escaped ────

def test_eyeball_script_tag_escaped_in_report(client, tmp_path):
    forge_dir = _write_card(tmp_path)
    (forge_dir / "build-report.md").write_text(
        "**Decision: CERTIFIED.**\n\n```\n<script>alert(1)</script>\n```\n"
    )
    resp = client.get("/forge/qkz-x/0.1.0")
    assert "<script>alert(1)</script>" not in resp.text
    assert "&lt;script&gt;" in resp.text


# ── T-FORGE-4: /build -> 308 /forge; /api/build/* -> 404 ─────────────

def test_build_redirects_to_forge(client):
    resp = client.get("/build", follow_redirects=False)
    assert resp.status_code == 308
    assert resp.headers["location"].startswith("/forge")


def test_api_build_jobs_404(client):
    resp = client.get("/api/build/jobs")
    assert resp.status_code == 404
