"""eval_hash + pipeline_hash (ARCHITECTURE.md §4.9).

``EvalHashInputs``' field set is a CLOSED WORLD: exactly the six fields
below and no others. A new field on the dataclass without a matching
entry in ``_CLASSIFIED_FIELDS`` fails T-HASH-3 loudly, on purpose — every
future contributor who adds a yardstick input has to explicitly decide
whether it belongs in the hash.

Excluded by construction (never touch this module): timestamps, paths,
run/build ids, host, training seeds, top-N, student identity — none of
those change what's being measured, only where/when/by-whom it ran.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Tuple


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode()


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclasses.dataclass(frozen=True)
class EvalHashInputs:
    harness_version: str
    prompts: str            # task template bytes, hex-encoded for JSON-safety
    few_shot: Dict[str, Any]   # {"bytes_hex": ..., "k": int}
    scoring: Dict[str, Any]
    decoding: Dict[str, Any]
    cert_set_version: str    # cert manifest sha256


# The closed-world declaration T-HASH-3 checks against `dataclasses.fields`.
_CLASSIFIED_FIELDS = frozenset({
    "harness_version", "prompts", "few_shot", "scoring", "decoding", "cert_set_version",
})


def assert_closed_world() -> None:
    declared = {f.name for f in dataclasses.fields(EvalHashInputs)}
    if declared != _CLASSIFIED_FIELDS:
        extra = declared - _CLASSIFIED_FIELDS
        missing = _CLASSIFIED_FIELDS - declared
        raise AssertionError(
            f"EvalHashInputs field set drifted from the closed-world list: "
            f"unclassified={sorted(extra)} missing={sorted(missing)}"
        )


def eval_hash(inputs: EvalHashInputs) -> str:
    assert_closed_world()
    payload = canonical_json(dataclasses.asdict(inputs))
    return "sha256:" + _sha256_hex(payload)


def write_eval_config_lock(inputs: EvalHashInputs, path: Path) -> None:
    assert_closed_world()
    path.write_text(json.dumps(dataclasses.asdict(inputs), sort_keys=True, indent=2))


def read_eval_config_lock(path: Path) -> EvalHashInputs:
    data = json.loads(Path(path).read_text())
    return EvalHashInputs(**data)


# ── pipeline_hash ────────────────────────────────────────────────────

def _nucleus_source_hash(nucleus_src_root: Path) -> str:
    """Sorted file bytes of src/arail/nucleus/**/*.py — deterministic
    regardless of filesystem iteration order or mtimes."""
    h = hashlib.sha256()
    for path in sorted(nucleus_src_root.rglob("*.py")):
        rel = path.relative_to(nucleus_src_root).as_posix()
        h.update(rel.encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def pipeline_hash(
    *, nucleus_src_root: Path, domain_canonical_bytes: bytes, corpus_manifest_sha: str,
    teacher_identity: str, student_base_identity: str,
    distill_params: Dict[str, Any], training_hyperparams: Dict[str, Any],
) -> str:
    payload = {
        "nucleus_source_hash": _nucleus_source_hash(Path(nucleus_src_root)),
        "domain": hashlib.sha256(domain_canonical_bytes).hexdigest(),
        "corpus_manifest_sha": corpus_manifest_sha,
        "teacher_identity": teacher_identity,
        "student_base_identity": student_base_identity,
        "distill_params": distill_params,
        "training_hyperparams": training_hyperparams,
    }
    return "sha256:" + _sha256_hex(canonical_json(payload))
