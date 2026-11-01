"""legacy-5 Nucleus seal sign/verify, key custody, trust anchors
(ARCHITECTURE.md §4.10, F3/F4/F5).

Signs exactly the 5-field legacy payload the Nucleus Rust `qkz isotope
verify` binary checks (dna_id, pipeline_run_id, chain_hash, gate_results,
timestamp) — NOT the 10-field payload Nucleus's own current Python
signer produces (F3: those two already disagree upstream; that's filed as
a Nucleus-side follow-up, not fixed here). The signed bytes are
``json.dumps(payload, sort_keys=True)`` using Python's DEFAULT separators
(``", "``/``": "``) — deliberately NOT this package's usual compact
canonical-JSON convention, because the Rust verifier's ``PythonJsonFormatter``
byte-matches exactly that default, nothing else.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

from arail.nucleus.errors import RefusedByPolicy

SEAL_FORMAT = "nucleus-seal/legacy-5"
_LEGACY_FIELDS = ("dna_id", "pipeline_run_id", "chain_hash", "gate_results", "timestamp")


class SealError(RefusedByPolicy):
    pass


def _require_cryptography():
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey, Ed25519PublicKey,
        )
    except ImportError as exc:
        raise SealError(
            "signature signing/verification needs the maximus extra "
            "(cryptography) — install with `pip install -e '.[maximus]'`"
        ) from exc
    return Ed25519PrivateKey, Ed25519PublicKey


# ── key custody ──────────────────────────────────────────────────────

def default_key_path() -> Path:
    override = os.getenv("NUCLEUS_SIGNING_KEY_PATH")
    if override:
        return Path(override)
    from arail.nucleus.paths import nucleus_data

    return nucleus_data() / "keys" / "signing.ed25519"


def load_or_generate_key(key_path: Optional[Path] = None) -> "Any":
    """Loads a 32-raw-byte Ed25519 key, or generates one on first use
    (loud 'back this up' warning), same on-disk format as Nucleus's own
    ``NucleusDNAGenerator.from_key_file`` — this is deliberate, so
    ``NUCLEUS_SIGNING_KEY_PATH`` can point at the real lineage key (Q2).
    """
    Ed25519PrivateKey, _ = _require_cryptography()
    key_path = Path(key_path) if key_path is not None else default_key_path()

    if key_path.exists():
        mode = key_path.stat().st_mode & 0o777
        if mode != 0o600:
            raise SealError(
                f"signing key at {key_path} has mode {oct(mode)}, not 0600 — "
                f"refusing to sign with a key that isn't owner-only readable"
            )
        raw = key_path.read_bytes()
        if len(raw) != 32:
            raise SealError(f"signing key at {key_path} is {len(raw)} bytes, expected 32 raw Ed25519 bytes")
        return Ed25519PrivateKey.from_private_bytes(raw)

    private_key = Ed25519PrivateKey.generate()
    key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw = private_key.private_bytes_raw()
    fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, raw)
    finally:
        os.close(fd)
    import sys

    sys.stderr.write(
        f"nucleus: generated a NEW signing key at {key_path} (mode 0600). "
        f"Back this file up — losing it means losing the ability to mint "
        f"new seals in this lineage.\n"
    )

    pub_hex = private_key.public_key().public_bytes_raw().hex()
    (key_path.parent / "signing.pub").write_text(pub_hex + "\n")
    _seed_trusted_keys(key_path.parent / "trusted_keys.txt", pub_hex)

    return private_key


def _seed_trusted_keys(path: Path, pub_hex: str) -> None:
    existing = path.read_text().splitlines() if path.is_file() else []
    if pub_hex not in existing:
        with path.open("a") as f:
            f.write(pub_hex + "\n")


def is_trusted_key(pub_hex: str, *, trusted_keys_path: Optional[Path] = None) -> bool:
    if trusted_keys_path is None:
        trusted_keys_path = default_key_path().parent / "trusted_keys.txt"
    if not trusted_keys_path.is_file():
        return False
    lines = {ln.strip() for ln in trusted_keys_path.read_text().splitlines() if ln.strip()}
    return pub_hex in lines


# ── payload construction + signing ──────────────────────────────────

def _assert_ascii_no_floats(obj: Any, *, path: str = "$") -> None:
    if isinstance(obj, float):
        raise SealError(f"seal payload contains a float at {path} — every value must be a string")
    if isinstance(obj, str):
        if not obj.isascii():
            raise SealError(f"seal payload contains a non-ASCII string at {path}: {obj!r}")
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            _assert_ascii_no_floats(k, path=f"{path}.{k}(key)")
            _assert_ascii_no_floats(v, path=f"{path}.{k}")
        return
    if isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _assert_ascii_no_floats(v, path=f"{path}[{i}]")
        return
    # int/bool/None are fine as-is.


def build_gate_results(*, card_sha256: str, eval_hash: str, decision: str,
                       contamination_overlap: float) -> List[dict]:
    return [
        {"gate_name": "card_sha256", "passed": True, "value": card_sha256},
        {"gate_name": "eval_hash", "passed": True, "value": eval_hash},
        {"gate_name": "decision", "passed": decision in ("CERTIFIED", "COMPATIBLE", "BETA"), "value": decision},
        {"gate_name": "contamination", "passed": contamination_overlap < 0.01,
         "value": f"{contamination_overlap:.4f}"},
        {"gate_name": "schema", "passed": True, "value": "dna-card/v2"},
    ]


def chain_hash(*, corpus_hash: str, teacher_hash: str, config_hash: str, training_hash: str) -> str:
    import hashlib

    combined = "|".join([corpus_hash, teacher_hash, config_hash, training_hash])
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def build_payload(*, pipeline_run_id: str, chain_hash: str, gate_results: List[dict],
                  dna_id: Optional[str] = None, timestamp: Optional[str] = None) -> dict:
    payload = {
        "dna_id": dna_id or str(uuid.uuid4()),
        "pipeline_run_id": pipeline_run_id,
        "chain_hash": chain_hash,
        "gate_results": gate_results,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
    }
    assert set(payload) == set(_LEGACY_FIELDS), "seal payload must be exactly the 5 legacy fields"
    return payload


def signed_bytes(payload: dict) -> bytes:
    """json.dumps(payload, sort_keys=True) with Python's DEFAULT
    separators — matches the Rust PythonJsonFormatter byte for byte."""
    _assert_ascii_no_floats(payload)
    return json.dumps(payload, sort_keys=True).encode("utf-8")


@dataclass(frozen=True)
class SealedCard:
    signed: dict
    seal_json: dict


def sign(payload: dict, *, key_path: Optional[Path] = None, ephemeral: bool = False) -> SealedCard:
    """``ephemeral=True`` (stub builds only) generates an in-memory key
    that is never written to disk and is never the lab's real signing
    key — the seal is still a real, self-consistent Ed25519 signature
    (so `qkz isotope verify` and this module's own verify_signature()
    both pass), but `key_fingerprint` is the literal marker
    "ephemeral-stub" instead of a real fingerprint, so verify() can tell
    the two apart and a stub card can never read as `trusted`."""
    if ephemeral:
        Ed25519PrivateKey, _ = _require_cryptography()
        private_key = Ed25519PrivateKey.generate()
    else:
        private_key = load_or_generate_key(key_path)

    data = signed_bytes(payload)
    signature = private_key.sign(data)
    public_key_hex = private_key.public_key().public_bytes_raw().hex()
    signature_hex = signature.hex()

    if ephemeral:
        key_fingerprint = "ephemeral-stub"
    else:
        import hashlib

        key_fingerprint = hashlib.sha256(bytes.fromhex(public_key_hex)).hexdigest()[:16]

    dna_seal = {
        "format": SEAL_FORMAT,
        **payload,
        "public_key_hex": public_key_hex,
        "signature_hex": signature_hex,
        "key_fingerprint": key_fingerprint,
    }
    return SealedCard(signed=dna_seal, seal_json={"dna_seal": dna_seal})


# ── verify ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class VerifyResult:
    signature: str    # "valid" | "invalid"
    key: str          # "trusted" | "untrusted" | "ephemeral-stub"
    card_hash: str     # "match" | "mismatch"
    eval_hash: str      # "match" | "mismatch" | "skipped"
    chain: str          # "match" | "mismatch" | "not_checked" | "skipped"
    fast: bool = False  # was this VerifyResult produced by a --fast verify?

    @property
    def all_ok(self) -> bool:
        """B2 (2026-09-23 review): a `--fast` verify legitimately never
        attempts eval_hash/chain, so "skipped" is fine there — that's what
        --fast MEANS. A non-fast verify that never actually recomputed
        something (chain re-derivation isn't implemented yet; eval_hash
        without a recompute value) must NOT read as ok just because the
        field says "skipped"/"not_checked" instead of "mismatch" — a
        skipped check is not a passed check."""
        base_ok = self.signature == "valid" and self.key == "trusted" and self.card_hash == "match"
        if self.fast:
            return base_ok and self.eval_hash in ("match", "skipped") and self.chain in ("match", "skipped")
        return base_ok and self.eval_hash == "match" and self.chain == "match"


def verify_signature(signed: dict) -> bool:
    _, Ed25519PublicKey = _require_cryptography()
    payload = {k: signed[k] for k in _LEGACY_FIELDS}
    data = signed_bytes(payload)
    pub_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(signed["public_key_hex"]))
    try:
        pub_key.verify(bytes.fromhex(signed["signature_hex"]), data)
        return True
    except Exception:  # noqa: BLE001 — cryptography raises InvalidSignature, treat any failure as invalid
        return False


def verify(
    card: dict, *, eval_hash_recompute: Optional[str] = None,
    fast: bool = False, trusted_keys_path: Optional[Path] = None,
) -> VerifyResult:
    from arail.nucleus.cards.dna_v2 import card_sha256

    signed = card.get("signed")
    if not signed:
        return VerifyResult(signature="invalid", key="untrusted", card_hash="mismatch",
                            eval_hash="skipped", chain="skipped", fast=fast)

    sig_ok = verify_signature(signed)
    is_stub_key = signed.get("key_fingerprint") == "ephemeral-stub"
    if is_stub_key:
        key_status = "ephemeral-stub"
    else:
        key_status = "trusted" if is_trusted_key(signed["public_key_hex"],
                                                  trusted_keys_path=trusted_keys_path) else "untrusted"

    recomputed_card_hash = card_sha256(card)
    card_hash_gate = next((g for g in signed["gate_results"] if g["gate_name"] == "card_sha256"), None)
    card_hash_status = "match" if card_hash_gate and card_hash_gate["value"] == recomputed_card_hash else "mismatch"

    if fast:
        eval_hash_status = "skipped"
    else:
        eval_gate = next((g for g in signed["gate_results"] if g["gate_name"] == "eval_hash"), None)
        if eval_hash_recompute is None or eval_gate is None:
            eval_hash_status = "skipped"
        else:
            eval_hash_status = "match" if eval_gate["value"] == eval_hash_recompute else "mismatch"

    # B2 (2026-09-23 review): chain re-derivation needs weight hashing,
    # which isn't implemented yet -- a non-fast verify must say so
    # honestly ("not_checked") rather than claiming "match" for a check
    # that was never actually performed.
    chain_status = "skipped" if fast else "not_checked"

    return VerifyResult(signature="valid" if sig_ok else "invalid", key=key_status,
                        card_hash=card_hash_status, eval_hash=eval_hash_status, chain=chain_status,
                        fast=fast)


# ── CLI: `arailctl nucleus verify <shard>@<ver>|<dir>` ────────────────

def _resolve_card_dir(target: str) -> Path:
    if "@" in target:
        shard, _, version = target.partition("@")
        from arail.nucleus.paths import forge_root

        return forge_root() / shard / version
    return Path(target)


def run(argv) -> int:
    import sys

    from arail.nucleus.cards.dna_v2 import load_card

    if not argv:
        sys.stderr.write("usage: nucleus verify <shard>@<ver>|<dir>\n")
        return 2

    fast = "--fast" in argv
    args = [a for a in argv if a != "--fast"]
    card_dir = _resolve_card_dir(args[0])
    card_path = card_dir / "dna-card.yaml"
    if not card_path.is_file():
        sys.stderr.write(f"no dna-card.yaml at {card_path}\n")
        return 3

    card = load_card(card_path)

    # B2 (2026-09-23 review): a non-fast verify must actually attempt the
    # eval_hash recompute -- load eval-config.lock next to the card and
    # recompute from it, rather than silently reporting "skipped". A
    # missing lock is a mismatch (something is missing that should be
    # there), never a free pass.
    eval_hash_recompute = None
    if not fast:
        from arail.nucleus.evals.hash import eval_hash as _compute_eval_hash
        from arail.nucleus.evals.hash import read_eval_config_lock

        lock_path = card_dir / "eval-config.lock"
        if lock_path.is_file():
            try:
                eval_hash_recompute = _compute_eval_hash(read_eval_config_lock(lock_path))
            except (OSError, ValueError, TypeError):
                eval_hash_recompute = ""  # unreadable/malformed lock -> forces a mismatch below
        else:
            eval_hash_recompute = ""  # no lock at all -> forces a mismatch below

    result = verify(card, eval_hash_recompute=eval_hash_recompute, fast=fast)
    sys.stdout.write(
        f"signature: {result.signature}\nkey: {result.key}\n"
        f"card_hash: {result.card_hash}\neval_hash: {result.eval_hash}\n"
        f"chain: {result.chain}\n"
    )
    return 0 if result.all_ok else 3
