"""Local model resolution, identity hashing, aliases, and teacher
auto-selection (ARCHITECTURE.md §4.4).

Every model is resolved only under ``ARAIL_MODELS_DIR`` — a domain.yaml
never supplies a path, only a name or a known alias (F9-adjacent: this is
how Nucleus stays independent of any single-model-env-var singleton the
way the router's deep-mode backend is, per ARCHITECTURE.md's F9).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from arail.nucleus.errors import DomainConfigError
from arail.nucleus.tokenizer_parity import Parity, parity as _parity

# name/alias -> directory name under ARAIL_MODELS_DIR.
ALIASES: Dict[str, str] = {
    "qwen2.5-3b-instruct": "Qwen2.5-3B-Instruct-4bit",
    "ai-engineer": "Qwen2.5-7B-Instruct-4bit",
}

_HF_DOWNLOAD_HINT = (
    "hf download {repo} --local-dir {models_dir}/{dirname}  "
    "(needs network — do this before switching to airgapped)"
)

# A minimal, best-effort alias -> HF repo id map, used only to print a
# copy-pasteable download command in the missing-model error. Not
# authoritative; operators can download any compatible model under any
# directory name and reference it by that directory name directly.
_HF_REPO_HINTS: Dict[str, str] = {
    "Qwen2.5-3B-Instruct-4bit": "mlx-community/Qwen2.5-3B-Instruct-4bit",
    "Qwen2.5-7B-Instruct-4bit": "mlx-community/Qwen2.5-7B-Instruct-4bit",
}


@dataclass(frozen=True)
class LocalModel:
    name: str            # the resolved directory name
    path: Path
    config: dict
    params_est_b: float
    is_moe: bool
    weights_bytes: int


def _models_dir() -> Path:
    from arail.config import MODELS_DIR

    return Path(MODELS_DIR)


def _resolve_dirname(name_or_alias: str) -> str:
    return ALIASES.get(name_or_alias, name_or_alias)


def _weight_files(path: Path) -> List[Path]:
    return sorted(path.glob("*.safetensors"))


def _estimate_params_b(config: dict, weights_bytes: int) -> float:
    if isinstance(config.get("num_parameters"), (int, float)):
        return float(config["num_parameters"]) / 1e9
    bits = 16
    quant = config.get("quantization")
    if isinstance(quant, dict) and isinstance(quant.get("bits"), (int, float)):
        bits = int(quant["bits"])
    if weights_bytes <= 0:
        return 0.0
    return (weights_bytes * 8 / bits) / 1e9


_MOE_KEY_HINTS = ("num_local_experts", "n_routed_experts", "num_experts",
                  "moe_intermediate_size")


def _is_moe(config: dict) -> bool:
    if any(k in config for k in _MOE_KEY_HINTS):
        return True
    model_type = str(config.get("model_type", "")).lower()
    return "moe" in model_type


def resolve_model(name_or_alias: str, *, models_dir: Optional[Path] = None) -> LocalModel:
    """Resolve *name_or_alias* to a LocalModel under ARAIL_MODELS_DIR.

    Absolute paths are never accepted here — only a bare directory name or
    a known alias. A CLI flag that wants to accept an absolute path must
    do so itself, explicitly, never by passing it through this function
    (domain.yaml can never carry a path — ARCHITECTURE.md §4.4).
    """
    if "/" in (name_or_alias or "") or (name_or_alias or "").startswith("."):
        raise DomainConfigError(
            f"model name must be a bare name or alias, not a path: {name_or_alias!r}"
        )

    root = Path(models_dir) if models_dir is not None else _models_dir()
    dirname = _resolve_dirname(name_or_alias)
    path = (root / dirname).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise DomainConfigError(f"resolved model path {path} escapes ARAIL_MODELS_DIR {root}") from exc

    config_path = path / "config.json"
    if not path.is_dir() or not config_path.is_file():
        repo = _HF_REPO_HINTS.get(dirname)
        hint = (
            _HF_DOWNLOAD_HINT.format(repo=repo, models_dir=root, dirname=dirname)
            if repo else
            f"place a HuggingFace-format model dir at {path} (needs network — "
            f"do this before switching to airgapped)"
        )
        raise DomainConfigError(f"model {name_or_alias!r} not found at {path}. {hint}")

    config = json.loads(config_path.read_text())
    weight_files = _weight_files(path)
    weights_bytes = sum(f.stat().st_size for f in weight_files)
    params_est_b = _estimate_params_b(config, weights_bytes)
    is_moe = _is_moe(config)

    return LocalModel(name=dirname, path=path, config=config,
                      params_est_b=params_est_b, is_moe=is_moe,
                      weights_bytes=weights_bytes)


# ── identity hashing ───────────────────────────────────────────────

def model_identity(model: LocalModel) -> str:
    """Cheap identity: config.json + tokenizer.json + the safetensors index
    plus each shard's (size, first-1MiB sha256). Used for judge != teacher
    comparisons and folded into eval_hash — cheap enough to compute on
    every eval without hashing full weight files."""
    h = hashlib.sha256()
    for fname in ("config.json", "tokenizer.json", "model.safetensors.index.json"):
        fpath = model.path / fname
        if fpath.is_file():
            h.update(fname.encode())
            h.update(fpath.read_bytes())
    for wf in _weight_files(model.path):
        h.update(wf.name.encode())
        size = wf.stat().st_size
        h.update(str(size).encode())
        with wf.open("rb") as f:
            h.update(f.read(1024 * 1024))
    return h.hexdigest()


def content_hash(model: LocalModel, *, cache_path: Optional[Path] = None) -> str:
    """Full sha256 over every weight file's bytes, cached by
    (path, size, mtime) in hash-cache.json (T-SETUP/perf: avoids re-hashing
    multi-GB weights on every build when nothing changed). Used for the
    seal's teacher_hash/training_hash — those need the real bytes, not the
    cheap identity."""
    cache_path = Path(cache_path) if cache_path is not None else _default_cache_path()
    cache = _load_hash_cache(cache_path)

    h = hashlib.sha256()
    dirty = False
    for wf in _weight_files(model.path):
        stat = wf.stat()
        key = str(wf)
        cached = cache.get(key)
        if cached and cached.get("size") == stat.st_size and cached.get("mtime") == stat.st_mtime:
            file_hash = cached["sha256"]
        else:
            file_hash = hashlib.sha256(wf.read_bytes()).hexdigest()
            cache[key] = {"size": stat.st_size, "mtime": stat.st_mtime, "sha256": file_hash}
            dirty = True
        h.update(wf.name.encode())
        h.update(file_hash.encode())

    if dirty:
        _save_hash_cache(cache_path, cache)
    return h.hexdigest()


def _default_cache_path() -> Path:
    from arail.nucleus.paths import nucleus_data

    return nucleus_data() / "hash-cache.json"


def _load_hash_cache(cache_path: Path) -> dict:
    if not cache_path.is_file():
        return {}
    try:
        return json.loads(cache_path.read_text())
    except (OSError, ValueError):
        return {}


def _save_hash_cache(cache_path: Path, cache: dict) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        cache_path.write_text(json.dumps(cache))
        os.chmod(cache_path, 0o600)
    except OSError:
        pass  # the cache is a perf optimization, never load-bearing


# ── tokenizer parity + teacher selection ─────────────────────────────

def parity(student: LocalModel, teacher: LocalModel) -> Parity:
    return _parity(student.path, teacher.path)


def local_model_at(path) -> LocalModel:
    """Build a LocalModel directly from an operator-supplied absolute
    directory path -- never routed through resolve_model(), whose '/' ban
    exists specifically so a domain.yaml can never carry a path (§4.4).
    This is for callers that already hold a real absolute path, such as
    preflight's Buddy-protection resolution (R3, 2026-09-23 review round
    2): Buddy's frozen deep-runtime model env var (see runtime_names.py)
    is a supported absolute-path configuration too, and Buddy's model
    must be recognized by content identity in that form, not only as a
    bare ARAIL_MODELS_DIR name."""
    resolved = Path(path).resolve()
    config_path = resolved / "config.json"
    if not resolved.is_dir() or not config_path.is_file():
        raise DomainConfigError(f"no model config.json found at {resolved}")

    config = json.loads(config_path.read_text())
    weight_files = _weight_files(resolved)
    weights_bytes = sum(f.stat().st_size for f in weight_files)
    params_est_b = _estimate_params_b(config, weights_bytes)
    is_moe = _is_moe(config)

    return LocalModel(name=resolved.name, path=resolved, config=config,
                      params_est_b=params_est_b, is_moe=is_moe, weights_bytes=weights_bytes)


def best_effort_identity(name: str) -> str:
    """The content identity of *name* when it resolves to a real, on-disk
    model under ARAIL_MODELS_DIR, or a plain "unresolved:<name>" fallback
    when it doesn't (never a fake hash) -- shared by certify.py's
    pipeline_hash/teacher_hash provenance and build.py's PC-phase judge-
    identity check (R2/ASK judge-identity, 2026-09-23 review round 2),
    which previously duplicated this exact logic as a private nested
    function in certify.py only."""
    if not name or name == "auto":
        return "unresolved:auto"
    try:
        return model_identity(resolve_model(name))
    except Exception:  # noqa: BLE001 -- identity falls back to the name, never raises
        return f"unresolved:{name}"


def select_teacher(
    auto: bool,
    student: LocalModel,
    budget_gb: float,
    exclude: Iterable[str],
    *,
    candidates: Iterable[LocalModel],
) -> Tuple[LocalModel, str]:
    """Deterministic ordering (ARCHITECTURE.md §3 N3): tokenizer parity
    required (exact or superset — `none` is dropped from consideration
    entirely, never silently accepted), then MoE, then fits resident in
    the Phase-A budget, then total params (larger preferred among what
    fits), then dir name (tiebreak, deterministic).

    ``candidates`` is the list of LocalModel to choose among — resolving
    "every model under ARAIL_MODELS_DIR" is the caller's job (this
    function is pure selection logic, independently testable).
    """
    exclude_set = set(exclude)
    scored = []
    rationale_by_name: Dict[str, str] = {}
    for cand in candidates:
        if cand.name in exclude_set or cand.name == student.name:
            continue
        par = parity(student, cand)
        if par.kind == "none":
            rationale_by_name[cand.name] = f"excluded: tokenizer parity is 'none' ({par.detail})"
            continue
        # weights * 1.10 as a rough "fits resident" proxy — the real
        # streamed-window path lives in preflight.py (commit 8); this
        # selection only needs a monotonic proxy for ranking.
        resident_gb = (cand.weights_bytes * 1.10) / (1024 ** 3)
        fits = resident_gb <= budget_gb
        scored.append((par.kind == "exact", cand.is_moe, fits, cand.params_est_b, cand.name, cand, par))

    if not scored:
        raise DomainConfigError(
            "no teacher candidate has tokenizer parity with the student — "
            "auto-selection found nothing usable"
        )

    # Sort key: exact parity first, MoE first, fits-budget first, larger
    # params first (among what fits — non-fitting entries sort after
    # fitting ones regardless of size because `fits` is compared before
    # params_est_b), name last as a deterministic tiebreak.
    scored.sort(key=lambda row: (not row[0], not row[1], not row[2], -row[3], row[4]))
    _, _, _, _, name, chosen, par = scored[0]

    rationale = (
        f"selected {chosen.name}: tokenizer parity={par.kind}, moe={chosen.is_moe}, "
        f"params={chosen.params_est_b:.1f}B, budget_gb={budget_gb:.1f}"
    )
    return chosen, rationale
