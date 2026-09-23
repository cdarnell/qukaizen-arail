"""DNA card v2 assembly + canonical hash + schema validation
(ARCHITECTURE.md §4.10; brief §5 field shape, with the deviations listed
in ARCHITECTURE.md §4.10).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import jsonschema
import yaml

from arail.nucleus.errors import DomainConfigError

_SCHEMA_PATH = Path(__file__).resolve().parents[4] / "spec" / "dna-card-v2.schema.json"


def not_run(reason: str) -> dict:
    return {"status": "not_run", "reason": reason}


def _load_schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text())


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode()


def build_card(
    *, shard: str, version: str, built: str, pipeline_hash: str, eval_hash: str,
    runtime: str, lab_mode: str, distillation: dict, splits: dict, contamination: dict,
    corpus: dict, closed_ended: dict, open_ended: dict, executable: dict,
    composite: dict, fidelity: dict, residency: Optional[dict] = None,
    arbitrage: Optional[dict] = None, inner_loop: Optional[dict] = None,
    hallucination_rate: Any = None, irreducible_floor: Any = None,
    baselines: Optional[dict] = None, efficiency: Optional[dict] = None,
    weights: Optional[dict] = None,
) -> dict:
    """Assembles the card dict (WITHOUT `signed` — seal.py, commit 20,
    attaches that after signing). `built` must already be a plain string
    (the caller quotes the timestamp) so the round trip below never turns
    it into a datetime."""
    card = {
        "shard": shard, "version": version, "built": built,
        "pipeline_hash": pipeline_hash, "eval_hash": eval_hash,
        "runtime": runtime, "lab_mode": lab_mode,
        "distillation": distillation, "splits": splits, "contamination": contamination,
        "corpus": corpus, "closed_ended": closed_ended, "open_ended": open_ended,
        "executable": executable, "composite": composite, "fidelity": fidelity,
        "residency": residency or {"status": "unmeasured"},
        "arbitrage": arbitrage or {},
        "inner_loop": inner_loop or {},
        "hallucination_rate": hallucination_rate if hallucination_rate is not None
                              else not_run("no reference-grounded claim checker this sprint"),
        "irreducible_floor": irreducible_floor if irreducible_floor is not None
                            else not_run("teacher-floor measurement not wired this sprint"),
        "baselines": baselines or {},
        "efficiency": efficiency or {"build_energy_est": not_run("needs sudo powermetrics")},
        "weights": weights or {"gguf": "not_built"},
    }
    return card


def validate_card(card: dict) -> None:
    """Raises DomainConfigError (never writes an invalid card) — validated
    BEFORE signing, per ARCHITECTURE.md §4.10."""
    try:
        jsonschema.validate(card, _load_schema())
    except jsonschema.ValidationError as exc:
        raise DomainConfigError(
            f"DNA card fails schema validation: {exc.message} "
            f"(at {'/'.join(str(p) for p in exc.path) or '<root>'})"
        ) from exc


def card_sha256(card: dict) -> str:
    """Computed on the YAML-reloaded dict (round-trip tested — T-CARD-2:
    a YAML dumper forces `built` to stay a quoted string so
    yaml.safe_load never turns it into a datetime and silently changes
    the hash), and always excludes `signed`."""
    import hashlib

    without_signed = {k: v for k, v in card.items() if k != "signed"}
    text = dump_card_yaml(without_signed)
    reloaded = yaml.safe_load(text)
    return hashlib.sha256(canonical_json(reloaded)).hexdigest()


class _QuotedStr(str):
    pass


def _quoted_str_representer(dumper: yaml.Dumper, data: _QuotedStr):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style='"')


yaml.add_representer(_QuotedStr, _quoted_str_representer, Dumper=yaml.SafeDumper)


def _quote_dates(obj: Any) -> Any:
    """Force `built` (and anything under it) to a quoted YAML string.
    Applied only at the top-level `built` key — the rest of the card
    already only carries plain strings/numbers/bools/dicts/lists."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "built" and isinstance(v, str):
                out[k] = _QuotedStr(v)
            else:
                out[k] = v
        return out
    return obj


def dump_card_yaml(card: dict) -> str:
    return yaml.dump(_quote_dates(card), Dumper=yaml.SafeDumper, sort_keys=True,
                     default_flow_style=False, allow_unicode=False)


def write_card(card: dict, path: Path) -> None:
    validate_card(card)
    path.write_text(dump_card_yaml(card))


def load_card(path: Path) -> dict:
    return yaml.safe_load(Path(path).read_text())
