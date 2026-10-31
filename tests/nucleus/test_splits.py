"""arail.nucleus.evals.splits — temporal + dev split, CertStore
(T-SPLIT-1..3, T-CERT-1..3)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from arail.nucleus.errors import RefusedByPolicy
from arail.nucleus.evals import splits as splits_mod


def _items(n, *, before_cutoff=True, prefix="it"):
    date = "2026-01-01" if before_cutoff else "2026-12-01"
    return [{"id": f"{prefix}-{i}", "date": date, "text": f"body {i}"} for i in range(n)]


CUTOFF = "2026-06-01"


# ── T-SPLIT-1: no cert item <= cutoff, no train item > cutoff ─────────

def test_split_respects_cutoff_boundary():
    items = _items(20, before_cutoff=True) + _items(10, before_cutoff=False)
    result = splits_mod.split(items, cutoff=CUTOFF, dev_fraction=0.1)
    assert all(it["date"] <= CUTOFF for it in result.train)
    assert all(it["date"] <= CUTOFF for it in result.dev)
    assert all(it["date"] > CUTOFF for it in result.cert_eligible)
    assert len(result.train) + len(result.dev) == 20
    assert len(result.cert_eligible) == 10


# ── T-SPLIT-2: dev split deterministic across runs/restarts ───────────

def test_dev_split_deterministic():
    items = _items(200, before_cutoff=True)
    r1 = splits_mod.split(items, cutoff=CUTOFF, dev_fraction=0.2)
    r2 = splits_mod.split(items, cutoff=CUTOFF, dev_fraction=0.2)
    assert {it["id"] for it in r1.dev} == {it["id"] for it in r2.dev}
    assert {it["id"] for it in r1.train} == {it["id"] for it in r2.train}


def test_dev_fraction_roughly_matches(monkeypatch):
    items = _items(2000, before_cutoff=True)
    result = splits_mod.split(items, cutoff=CUTOFF, dev_fraction=0.1)
    ratio = len(result.dev) / len(items)
    assert 0.05 < ratio < 0.15  # sha-based, so approximate not exact


# ── T-SPLIT-3: insufficient cert items -> refusal with count ──────────

def test_sample_cert_set_insufficient_items():
    eligible = _items(5, before_cutoff=False)
    with pytest.raises(RefusedByPolicy, match="only 5"):
        splits_mod.sample_cert_set(eligible, cert_n=20)


def test_sample_cert_set_deterministic_and_sized():
    eligible = _items(50, before_cutoff=False)
    a = splits_mod.sample_cert_set(eligible, cert_n=20)
    b = splits_mod.sample_cert_set(eligible, cert_n=20)
    assert len(a) == 20
    assert [it["id"] for it in a] == [it["id"] for it in b]


# ── T-CERT-1: cert sha identical before/after a stub run (no mutation) ──

def test_cert_store_create_and_reopen(tmp_path):
    from arail.nucleus.corpus.stage import StageResult

    domain = SimpleNamespace(name="kernel", corpus_cutoff=CUTOFF, eval_dev_fraction=0.1, eval_cert_n=5)
    items = _items(20, before_cutoff=True) + _items(10, before_cutoff=False)
    stage_result = SimpleNamespace()
    stage_result.manifest_sha256 = "deadbeef"

    store = splits_mod.CertStore(nucleus_data=tmp_path)

    import arail.nucleus.corpus.stage as stage_mod
    orig_load_items = stage_mod.load_items
    stage_mod.load_items = lambda sr: items
    try:
        version = store.create(domain, stage_result)
    finally:
        stage_mod.load_items = orig_load_items

    assert version.version == "cert-v1"
    assert version.manifest["n"] == 5

    access1 = store.open("kernel")
    cert_items_1 = access1.open()
    access2 = store.open("kernel")
    cert_items_2 = access2.open()
    assert cert_items_1 == cert_items_2
    assert access1.open_count == 1


def test_cert_store_frozen_reuses_latest_version(tmp_path):
    domain = SimpleNamespace(name="kernel", corpus_cutoff=CUTOFF, eval_dev_fraction=0.1, eval_cert_n=5)
    items = _items(20, before_cutoff=True) + _items(10, before_cutoff=False)
    stage_result = SimpleNamespace(manifest_sha256="abc")

    store = splits_mod.CertStore(nucleus_data=tmp_path)
    import arail.nucleus.corpus.stage as stage_mod
    orig = stage_mod.load_items
    stage_mod.load_items = lambda sr: items
    try:
        v1 = store.create(domain, stage_result)
    finally:
        stage_mod.load_items = orig

    # A second `stage` run without --new-cert-version never calls create()
    # again; open() with no explicit version reuses v1.
    access = store.open("kernel")
    assert access is not None
    assert store.latest_version("kernel") == v1.version


# ── T-CERT-2: a flipped byte -> CertTampered ───────────────────────────

def test_cert_tampered_detected(tmp_path):
    domain = SimpleNamespace(name="kernel", corpus_cutoff=CUTOFF, eval_dev_fraction=0.1, eval_cert_n=5)
    items = _items(20, before_cutoff=True) + _items(10, before_cutoff=False)
    stage_result = SimpleNamespace(manifest_sha256="abc")

    store = splits_mod.CertStore(nucleus_data=tmp_path)
    import arail.nucleus.corpus.stage as stage_mod
    orig = stage_mod.load_items
    stage_mod.load_items = lambda sr: items
    try:
        version = store.create(domain, stage_result)
    finally:
        stage_mod.load_items = orig

    import os

    os.chmod(version.cert_path, 0o600)
    with version.cert_path.open("a") as f:
        f.write("tampered\n")

    access = store.open("kernel")
    with pytest.raises(splits_mod.CertTampered):
        access.open()


# ── T-CERT-3: arbitrage.py never imports CertStore/CertAccess (import-lint half) ──

def test_arbitrage_module_does_not_import_certstore():
    import ast
    from pathlib import Path

    arbitrage_path = Path(__file__).resolve().parents[2] / "src" / "arail" / "nucleus" / "arbitrage.py"
    if not arbitrage_path.is_file():
        pytest.skip("arbitrage.py not implemented yet (lands in commit 18)")
    tree = ast.parse(arbitrage_path.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    assert "CertStore" not in names
    assert "CertAccess" not in names
