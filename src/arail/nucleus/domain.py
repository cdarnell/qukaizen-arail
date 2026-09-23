"""domain.yaml load/validate — nucleus.domain/v1.

Promise: ``load_domain(name) -> DomainConfig``, a frozen dataclass with
defaults applied and canonical bytes available for ``pipeline_hash``
(hash.py, commit 16).

Every domain lives at ``configs/domains/<name>.yaml`` and nowhere else —
``name`` is resolved through a strict slug pattern into that one path, never
a user-supplied path (T-DOM-3 / ARCHITECTURE.md §4.2).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

import jsonschema
import yaml

from arail.nucleus.errors import DomainConfigError
from arail.nucleus.runtime_names import resolve_user_runtime

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_MAX_DOMAIN_BYTES = 64 * 1024

_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "spec" / "nucleus-domain-v1.schema.json"
_DOMAINS_DIR = Path(__file__).resolve().parents[3] / "configs" / "domains"

_DEFAULTS = {
    "runtime": "queuellm",
    "eval.cert_n": 800,
    "distill.top_n": 20,
}


def _load_schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text())


@dataclass(frozen=True)
class DomainConfig:
    name: str
    student_base: str
    teacher_profile: str
    teacher_model: str
    runtime: str
    corpus_sources: Tuple[Tuple[str, str], ...]
    corpus_cutoff: str
    eval_cert_set: str
    eval_eyeball_prompts: Path
    eval_dev_fraction: float
    eval_cert_n: int
    fidelity_target: float
    distill_top_n: int
    shard: str
    # The exact dict this config was built from (after defaults applied),
    # used to produce stable canonical bytes for pipeline_hash (commit 16).
    raw: dict = field(compare=False)

    def canonical_bytes(self) -> bytes:
        return json.dumps(self.raw, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode()


def _check_superskill_manifest(data: Any, name: str) -> None:
    if isinstance(data, dict) and "superskill" in data:
        raise DomainConfigError(
            f"{name}.yaml is a legacy Nucleus superskill manifest, not a "
            f"Model Forge domain — 'superskill:' is not a domain.yaml key"
        )


def _normalize_cutoff(raw: Any) -> str:
    if isinstance(raw, (date, datetime)):
        return raw.isoformat() if isinstance(raw, date) and not isinstance(raw, datetime) \
            else raw.date().isoformat()
    if isinstance(raw, str):
        try:
            date.fromisoformat(raw)
        except ValueError as exc:
            raise DomainConfigError(
                f"corpus.cutoff must be an ISO date string (YYYY-MM-DD), got {raw!r}"
            ) from exc
        return raw
    raise DomainConfigError(f"corpus.cutoff must be a quoted ISO date string, got {raw!r}")


def _check_eyeball_prompts(rel: str, name: str, *, base: Path) -> Path:
    base = base.resolve()
    candidate = (base / rel).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise DomainConfigError(
            f"eval.eyeball_prompts must resolve inside {base} — got {rel!r}"
        ) from exc
    if not candidate.is_file():
        raise DomainConfigError(f"eval.eyeball_prompts file not found: {candidate}")
    lines = [ln for ln in candidate.read_text().splitlines() if ln.strip()]
    if len(lines) != 10:
        raise DomainConfigError(
            f"eval.eyeball_prompts must contain exactly 10 prompts, found {len(lines)} in {candidate}"
        )
    return candidate


def load_domain(
    name: str, *,
    domains_dir: Optional[Path] = None,
    model_resolver: Optional[Callable[[str], Any]] = None,
) -> DomainConfig:
    # model_resolver, if given, is called with student.base and must
    # return an object with a params_est_b attribute; a value >= 8 raises
    # DomainConfigError (T-DOM-4). It is optional and unset by default
    # because resolving a model requires arail.nucleus.models (commit 7,
    # ARCHITECTURE.md §10) -- callers that have it (cli.py's plan/build,
    # from commit 7 onward) pass arail.nucleus.models.resolve_model
    # explicitly. Domain *shape* validation (this function's other checks)
    # does not depend on models.py and is complete as of this commit.

    if not _SLUG_RE.match(name or ""):
        raise DomainConfigError(
            f"domain name must match ^[a-z0-9][a-z0-9-]{{0,62}}$, got {name!r}"
        )

    domains_dir = Path(domains_dir).resolve() if domains_dir else _DOMAINS_DIR.resolve()
    path = domains_dir / f"{name}.yaml"
    if not path.is_file():
        raise DomainConfigError(f"no domain config at {path}")

    size = path.stat().st_size
    if size > _MAX_DOMAIN_BYTES:
        raise DomainConfigError(f"{path} is {size} bytes, over the {_MAX_DOMAIN_BYTES}-byte cap")

    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise DomainConfigError(f"{path} is not valid YAML: {exc}") from exc

    _check_superskill_manifest(data, name)
    if not isinstance(data, dict):
        raise DomainConfigError(f"{path} must be a YAML mapping at the top level")

    try:
        jsonschema.validate(data, _load_schema())
    except jsonschema.ValidationError as exc:
        raise DomainConfigError(f"{path}: {exc.message} (at {'/'.join(str(p) for p in exc.path) or '<root>'})") from exc

    # ── semantic checks the schema can't express ──
    runtime = data.get("runtime", _DEFAULTS["runtime"])
    resolve_user_runtime(runtime)  # raises DomainConfigError on unknown name

    cert_set = data["eval"]["cert_set"]
    if cert_set != "frozen":
        if cert_set.startswith("refresh:"):
            raise DomainConfigError("eval.cert_set 'refresh:<days>' is not supported until a later sprint")
        raise DomainConfigError(f"eval.cert_set must be 'frozen', got {cert_set!r}")

    eyeball_path = _check_eyeball_prompts(data["eval"]["eyeball_prompts"], name, base=domains_dir)

    sources: list = []
    for entry in data["corpus"]["sources"]:
        kind, _, source_name = entry.partition(":")
        if "/" in source_name or not source_name:
            raise DomainConfigError(f"corpus.sources entries are identifiers, not paths: {entry!r}")
        sources.append((kind, source_name))

    cutoff = _normalize_cutoff(data["corpus"]["cutoff"])

    teacher = data.get("teacher", {})
    teacher_profile = teacher.get("profile", "local")
    teacher_model = teacher.get("model", "auto")

    cert_n = data["eval"].get("cert_n", _DEFAULTS["eval.cert_n"])
    top_n = data.get("distill", {}).get("top_n", _DEFAULTS["distill.top_n"])
    shard = data.get("shard", f"qkz-{name}")

    # Canonical dict — defaults folded in, so a domain relying on an
    # implicit default and one spelling it out explicitly hash the same.
    canonical = {
        "student": {"base": data["student"]["base"]},
        "teacher": {"profile": teacher_profile, "model": teacher_model},
        "runtime": runtime,
        "corpus": {"sources": [f"{k}:{n}" for k, n in sources], "cutoff": cutoff},
        "eval": {
            "cert_set": cert_set,
            "eyeball_prompts": data["eval"]["eyeball_prompts"],
            "dev_fraction": data["eval"]["dev_fraction"],
            "cert_n": cert_n,
        },
        "fidelity": {"target": data["fidelity"]["target"]},
        "distill": {"top_n": top_n},
        "shard": shard,
    }

    if model_resolver is not None:
        resolved = model_resolver(data["student"]["base"])
        params_b = getattr(resolved, "params_est_b", None)
        if params_b is not None and params_b >= 8:
            raise DomainConfigError(
                f"student.base {data['student']['base']!r} is an estimated "
                f"{params_b:.1f}B params -- student must be < 8B"
            )

    return DomainConfig(
        name=name,
        student_base=data["student"]["base"],
        teacher_profile=teacher_profile,
        teacher_model=teacher_model,
        runtime=runtime,
        corpus_sources=tuple(sources),
        corpus_cutoff=cutoff,
        eval_cert_set=cert_set,
        eval_eyeball_prompts=eyeball_path,
        eval_dev_fraction=data["eval"]["dev_fraction"],
        eval_cert_n=cert_n,
        fidelity_target=data["fidelity"]["target"],
        distill_top_n=top_n,
        shard=shard,
        raw=canonical,
    )
