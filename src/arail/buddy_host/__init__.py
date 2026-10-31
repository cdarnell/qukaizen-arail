"""Buddy's host edge: ``buddy.host/v1`` (sprint 2026-10-20-buddy-always-on).

qukaizen-buddy is a separate app. This package is ARAIL's side of the one
loopback contract between them: it carries the byte-identical vendored copy of
the contract (``contract/``, stamped by ``scripts/sync-buddy-contract.py``) and,
in later modules, the edge's admission, handshake, brain, ledger and link logic.

Importing this package reads the vendored contract once and checks it against
its stamp. A copy that does not match raises :class:`ContractStampError`: an
edge that cannot vouch for its contract must not run (the portal catches that
and serves no edge routes instead of failing to boot).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

CONTRACT_DIR = Path(__file__).resolve().parent / "contract"
CONTRACT_FILE = CONTRACT_DIR / "buddy.host.v1.json"
PROVENANCE_FILE = CONTRACT_DIR / "PROVENANCE.json"


class ContractStampError(RuntimeError):
    """The vendored contract bytes do not match PROVENANCE.json."""


def _load() -> tuple[bytes, str, dict[str, Any], dict[str, Any]]:
    try:
        data = CONTRACT_FILE.read_bytes()
        stamp = json.loads(PROVENANCE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractStampError(f"cannot read the vendored buddy.host/v1 contract: {type(exc).__name__}") from exc
    digest = hashlib.sha256(data).hexdigest()
    stamped = (stamp.get("files") or {}).get("buddy.host.v1.json")
    if stamped != "sha256:" + digest:
        raise ContractStampError(
            "vendored buddy.host.v1.json does not match PROVENANCE.json; "
            'run: python scripts/sync-buddy-contract.py --from "$QKZ_BUDDY_PATH"'
        )
    try:
        contract = json.loads(data.decode("utf-8"))
    except ValueError as exc:
        raise ContractStampError("vendored buddy.host.v1.json is not valid JSON") from exc
    return data, digest, contract, stamp


CONTRACT_BYTES, CONTRACT_SHA256, CONTRACT, PROVENANCE = _load()

__all__ = [
    "CONTRACT", "CONTRACT_BYTES", "CONTRACT_DIR", "CONTRACT_FILE", "CONTRACT_SHA256",
    "ContractStampError", "PROVENANCE", "PROVENANCE_FILE",
]
