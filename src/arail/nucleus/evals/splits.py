"""Temporal + dev split, and the CertStore (ARCHITECTURE.md §4.9).

Train/dev pool = items with ``date <= cutoff``. Dev = the items where
``int(sha256(item_id)[:8], 16) % 1000 < dev_fraction * 1000`` — deterministic,
no RNG state to seed or persist. Cert = items with ``date > cutoff``, sampled
by sorted sha to ``cert_n``. Cert items are never in the train pool, by
construction (they're on opposite sides of the cutoff).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from arail.nucleus.errors import RefusedByPolicy


def _item_sha_int(item_id: str) -> int:
    return int(hashlib.sha256(item_id.encode()).hexdigest()[:8], 16)


def is_dev_item(item_id: str, dev_fraction: float) -> bool:
    return _item_sha_int(item_id) % 1000 < dev_fraction * 1000


@dataclass(frozen=True)
class Splits:
    train: List[dict]
    dev: List[dict]
    cert_eligible: List[dict]  # everything > cutoff, before cert_n sampling


def split(items: List[dict], *, cutoff: str, dev_fraction: float) -> Splits:
    pool = [it for it in items if it["date"] <= cutoff]
    cert_eligible = [it for it in items if it["date"] > cutoff]

    dev = [it for it in pool if is_dev_item(it["id"], dev_fraction)]
    train = [it for it in pool if not is_dev_item(it["id"], dev_fraction)]

    return Splits(train=train, dev=dev, cert_eligible=cert_eligible)


def sample_cert_set(cert_eligible: List[dict], cert_n: int) -> List[dict]:
    if len(cert_eligible) < cert_n:
        raise RefusedByPolicy(
            f"only {len(cert_eligible)} cert-eligible items (date > cutoff), "
            f"need cert_n={cert_n} — stage more corpus or lower cert_n, or "
            f"move the cutoff earlier"
        )
    ordered = sorted(cert_eligible, key=lambda it: hashlib.sha256(it["id"].encode()).hexdigest())
    return sorted(ordered[:cert_n], key=lambda it: it["id"])


# ── CertStore ─────────────────────────────────────────────────────────

def _canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode()


@dataclass(frozen=True)
class CertVersion:
    domain: str
    version: str
    manifest: dict
    cert_path: Path
    manifest_path: Path


class CertAccess:
    """Minted only by eval.py/certify.py/spike.py — arbitrage.py must never
    construct one (T-CERT-3's import-lint half enforces the "never
    imports CertStore" side; this class enforces the other half: a
    CertAccess re-hashes the file on every open())."""

    def __init__(self, cert_dir: Path, manifest: dict):
        self._cert_dir = cert_dir
        self._manifest = manifest
        self._open_count = 0

    def open(self) -> List[dict]:
        self._open_count += 1
        cert_path = self._cert_dir / "cert.jsonl"
        raw = cert_path.read_bytes()
        actual_sha = hashlib.sha256(raw).hexdigest()
        expected_sha = self._manifest["sha256"]
        if actual_sha != expected_sha:
            raise CertTampered(
                f"cert set at {cert_path} has sha256={actual_sha}, "
                f"manifest recorded {expected_sha} — refusing to use a "
                f"possibly-modified cert set"
            )
        return [json.loads(line) for line in raw.decode().splitlines() if line.strip()]

    @property
    def open_count(self) -> int:
        return self._open_count


class CertTampered(RefusedByPolicy):
    pass


class CertStore:
    def __init__(self, *, nucleus_data: Optional[Path] = None):
        if nucleus_data is None:
            from arail.nucleus.paths import nucleus_data as _nd

            nucleus_data = _nd()
        self._nucleus_data = Path(nucleus_data)

    def _domain_cert_root(self, domain_name: str) -> Path:
        return self._nucleus_data / "domains" / domain_name / "cert"

    def latest_version(self, domain_name: str) -> Optional[str]:
        root = self._domain_cert_root(domain_name)
        if not root.is_dir():
            return None
        versions = sorted(
            (d.name for d in root.iterdir() if d.is_dir() and d.name.startswith("cert-v")),
            key=lambda name: int(name[len("cert-v"):]),
        )
        return versions[-1] if versions else None

    def create(self, domain, stage_result) -> CertVersion:
        """Mints a NEW cert-vN — only called explicitly (`stage --new-cert-
        version`). Frozen: an existing cert-vN for the domain is reused
        across builds otherwise (see `open` below)."""
        from arail.nucleus.corpus.stage import load_items

        items = load_items(stage_result)
        splits = split(items, cutoff=domain.corpus_cutoff, dev_fraction=domain.eval_dev_fraction)
        cert_items = sample_cert_set(splits.cert_eligible, domain.eval_cert_n)

        root = self._domain_cert_root(domain.name)
        existing = self.latest_version(domain.name)
        next_n = (int(existing[len("cert-v"):]) + 1) if existing else 1
        version = f"cert-v{next_n}"
        cert_dir = root / version
        cert_dir.mkdir(parents=True, exist_ok=False, mode=0o700)

        cert_bytes = b"\n".join(_canonical_json(it) for it in cert_items) + (b"\n" if cert_items else b"")
        cert_path = cert_dir / "cert.jsonl"
        cert_path.write_bytes(cert_bytes)
        cert_sha = hashlib.sha256(cert_bytes).hexdigest()

        manifest = {
            "version": version, "n": len(cert_items), "cutoff": domain.corpus_cutoff,
            "snapshot_sha": stage_result.manifest_sha256, "sha256": cert_sha,
        }
        manifest_path = cert_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2))

        for p in (cert_path, manifest_path):
            os.chmod(p, 0o444)

        return CertVersion(domain=domain.name, version=version, manifest=manifest,
                          cert_path=cert_path, manifest_path=manifest_path)

    def open(self, domain_name: str, *, version: Optional[str] = None) -> CertAccess:
        version = version or self.latest_version(domain_name)
        if version is None:
            raise RefusedByPolicy(
                f"no cert set for domain {domain_name!r} — run "
                f"`nucleus stage {domain_name} --new-cert-version` first"
            )
        cert_dir = self._domain_cert_root(domain_name) / version
        manifest_path = cert_dir / "manifest.json"
        if not manifest_path.is_file():
            raise RefusedByPolicy(f"cert version {version!r} not found for {domain_name!r}")
        manifest = json.loads(manifest_path.read_text())
        return CertAccess(cert_dir, manifest)
