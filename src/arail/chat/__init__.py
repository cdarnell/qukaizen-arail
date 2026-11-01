"""Chat-side helpers: curated SLM catalog + installed-model detection.

The chat tab's model gallery surfaces every catalog entry — installed
or not — so the user sees the breadth of available local models
without committing disk first. Installed models bind to a runtime
(MLX in-process, Ollama, MLX OpenAI server) so the chat send path
can route to whichever backend owns the chosen model.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]


_CATALOG_PATH = Path(__file__).parent / "models_catalog.yaml"


@dataclass
class CatalogEntry:
    """One row in the curated SLM catalog."""

    id: str
    name: str
    family: str
    size_gb: float
    released: str
    source: str          # ollama | mlx | hf | cloud
    good_at: list[str]
    description: str
    install: str
    tier: str            # recommended | optional | flagship
    # Optional fields for cloud catalog rows (L2/L3 — sprint 2026-05-18).
    # Legacy rows (no provider/ctx in YAML) default to None; existing callers
    # are unaffected. F-CATALOG: must be emitted by as_dict() or cloud rows
    # vanish before reaching the gallery.
    provider: "str | None" = field(default=None)
    ctx: "str | None" = field(default=None)
    # Structured HF repo id for source: hf|mlx rows (e.g.
    # "mlx-community/Qwen2.5-7B-Instruct-4bit") — previously only embedded
    # inside the free-text `install` string, which meant a real HF link
    # could never be derived without parsing shell commands. Empty string
    # (not None) when unset, matching the rest of this dataclass's "" convention.
    hf_repo: str = field(default="")

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "family": self.family,
            "size_gb": self.size_gb,
            "released": self.released,
            "source": self.source,
            "good_at": list(self.good_at),
            "description": self.description,
            "install": self.install,
            "tier": self.tier,
            # L3 optional fields — always emitted so gallery renderer never
            # sees a missing key; None means "not set".
            "provider": self.provider,
            "ctx": self.ctx,
            "hf_repo": self.hf_repo,
            "hf_url": (f"https://huggingface.co/{self.hf_repo}"
                       if self.hf_repo else None),
        }


def load_catalog() -> list[CatalogEntry]:
    """Read models_catalog.yaml. Returns [] on parse failure so the
    chat surface degrades gracefully."""
    if not _CATALOG_PATH.exists():
        return []
    try:
        raw = yaml.safe_load(_CATALOG_PATH.read_text())
    except yaml.YAMLError:
        return []
    if not isinstance(raw, list):
        return []
    out: list[CatalogEntry] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            out.append(CatalogEntry(
                id=str(entry["id"]),
                name=str(entry.get("name") or entry["id"]),
                family=str(entry.get("family") or "unknown"),
                size_gb=float(entry.get("size_gb") or 0),
                released=str(entry.get("released") or ""),
                source=str(entry.get("source") or "ollama"),
                good_at=[str(t) for t in (entry.get("good_at") or [])],
                description=str(entry.get("description") or ""),
                install=str(entry.get("install") or ""),
                tier=str(entry.get("tier") or "optional"),
                # Optional fields (L2/L3) — None when absent so legacy rows are
                # unaffected and as_dict() always emits them (F-CATALOG).
                provider=(str(entry["provider"]) if entry.get("provider") else None),
                ctx=(str(entry["ctx"]) if entry.get("ctx") else None),
                hf_repo=str(entry.get("hf_repo") or ""),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return out


# ── Installed-model detection ────────────────────────────────────


def _ollama_installed_models(host: str = "127.0.0.1", port: int = 11434) -> list[dict[str, Any]]:
    """Query Ollama's /api/tags endpoint for installed models.

    Returns ``[{id, size_gb, modified}, ...]`` or [] if Ollama isn't
    reachable. Tight timeout (1.5s) so a hung Ollama doesn't hang
    the chat page.
    """
    try:
        req = urllib.request.Request(f"http://{host}:{port}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            if resp.status != 200:
                return []
            data = json.loads(resp.read())
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for m in data.get("models") or []:
        if not isinstance(m, dict):
            continue
        name = m.get("name") or m.get("model")
        if not name:
            continue
        size_b = m.get("size") or 0
        out.append({
            "id": str(name),
            "runtime": "ollama",
            "size_gb": round(size_b / (1024 ** 3), 2) if size_b else None,
            "modified": str(m.get("modified_at") or ""),
            "endpoint": f"http://{host}:{port}/v1",
        })
    return out


def _mlx_dir_installed_models() -> list[dict[str, Any]]:
    """Scan ARAIL_MODELS_DIR (default lab/models/) for MLX-style
    folders. Each folder = one installed local model."""
    models_dir = Path(os.getenv("ARAIL_MODELS_DIR", "lab/models"))
    if not models_dir.exists():
        return []
    out: list[dict[str, Any]] = []
    for child in sorted(models_dir.iterdir()):
        if not child.is_dir() or child.name.startswith((".", "_")):
            continue
        if "_cache" in child.name:
            continue
        # Best-effort size: sum of children up to a few levels deep.
        size_b = 0
        try:
            for f in child.rglob("*"):
                if f.is_file():
                    size_b += f.stat().st_size
        except OSError:
            pass
        out.append({
            "id": child.name,
            "runtime": "mlx",
            "size_gb": round(size_b / (1024 ** 3), 2) if size_b else None,
            "modified": "",
            "endpoint": None,   # in-process
        })
    return out


def _mlx_openai_server_models(host: str = "127.0.0.1", port: int | None = None) -> list[dict[str, Any]]:
    """Query the lab's local MLX OpenAI-compat server for the model
    it currently exposes. Returns [] if the server isn't reachable."""
    p = port or int(os.getenv("MLX_OPENAI_PORT", "11435"))
    try:
        req = urllib.request.Request(f"http://{host}:{p}/v1/models", method="GET")
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            if resp.status != 200:
                return []
            data = json.loads(resp.read())
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for m in data.get("data") or []:
        if not isinstance(m, dict):
            continue
        mid = m.get("id")
        if not mid:
            continue
        out.append({
            "id": str(mid),
            "runtime": "mlx-openai",
            "size_gb": None,
            "modified": "",
            "endpoint": f"http://{host}:{p}/v1",
        })
    return out


def detect_installed_models() -> list[dict[str, Any]]:
    """Union of every locally-installed model the lab can route to.

    Sources:
      * MLX in-process (folders under ARAIL_MODELS_DIR)
      * MLX OpenAI server (the lab's own ``arail.mlx_openai_server`` on :11435)
      * Ollama (whatever ``ollama list`` reports via the /api/tags endpoint)

    Each entry: ``{id, runtime, size_gb, modified, endpoint}``.
    Duplicates are deduped by ``(runtime, id)``.
    """
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for source in (_ollama_installed_models, _mlx_openai_server_models, _mlx_dir_installed_models):
        for entry in source():
            key = (entry["runtime"], entry["id"])
            if key in seen:
                continue
            seen.add(key)
            out.append(entry)
    return out


def _resolve_hint_for_gallery(
    hint: dict[str, Any] | None,
    installed_ids: set[str],
    catalog_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Join a model-hint sidecar against the live catalog + installed set.

    Pure + total: returns None on any inconsistency (no hint, no recommendation,
    malformed sidecar). The volatile installed-vs-available distinction is
    computed HERE (at read time) so it is always fresh — see ARCHITECTURE §2.2.

    Returned block (or None):
      { state, id, name, size_gb, good_at, rationale, family, world,
        catalog_entry, promoted_from_fallback }
    where state ∈ {recommended_installed, recommended_available, recommended_unknown}.
    """
    if not isinstance(hint, dict):
        return None
    rec = hint.get("recommended")
    if not isinstance(rec, dict):
        return None
    rid = rec.get("id")
    if not isinstance(rid, str) or not rid:
        return None

    world = hint.get("world")
    fallback = hint.get("fallback") if isinstance(hint.get("fallback"), list) else []

    def _state_for(mid: str) -> str | None:
        """Catalog/installed state for an id, or None if not in catalog."""
        if mid not in catalog_by_id:
            return None
        return "recommended_installed" if mid in installed_ids else "recommended_available"

    surfaced_id = rid
    promoted = False
    state = _state_for(rid)

    if state is None:
        # recommended is unknown to the catalog — walk fallback[] in order; the
        # first that resolves (installed or available) is promoted.
        for fid in fallback:
            if not isinstance(fid, str):
                continue
            fstate = _state_for(fid)
            if fstate is not None:
                surfaced_id = fid
                state = fstate
                promoted = True
                break

    if state is None:
        # Nothing resolved — advisory-only against the original recommendation.
        return {
            "state": "recommended_unknown",
            "id": rid,
            "name": rec.get("family") or rid,
            "size_gb": rec.get("size_gb"),
            "good_at": list(rec.get("good_at") or []),
            "rationale": rec.get("rationale"),
            "family": rec.get("family"),
            "world": world,
            "catalog_entry": None,
            "promoted_from_fallback": False,
        }

    entry = catalog_by_id.get(surfaced_id)
    # Catalog wins for display fields when matched; the hint supplies advisory
    # rationale (DATA, escaped at render time — never enters a prompt).
    return {
        "state": state,
        "id": surfaced_id,
        "name": (entry or {}).get("name") or surfaced_id,
        "size_gb": (entry or {}).get("size_gb") if entry else rec.get("size_gb"),
        "good_at": list((entry or {}).get("good_at") or rec.get("good_at") or []),
        "rationale": rec.get("rationale"),
        "family": (entry or {}).get("family") or rec.get("family"),
        "world": world,
        "catalog_entry": entry,
        "promoted_from_fallback": promoted,
    }


def gallery_view() -> dict[str, Any]:
    """Build the unified chat-page gallery payload.

    Returns:
      ``installed``   list of running models (MLX + Ollama + ...)
      ``catalog``     list of curated entries with ``installed_state``
                      = installed | available
      ``runtimes``    short tally per runtime for the UI footer

    The chat UI uses ``catalog`` for the gallery cards and
    ``installed`` for the active-model dropdown / send routing.
    """
    installed = detect_installed_models()
    installed_ids = {e["id"] for e in installed}
    catalog = []
    catalog_by_id: dict[str, dict[str, Any]] = {}
    for entry in load_catalog():
        d = entry.as_dict()
        d["installed_state"] = "installed" if entry.id in installed_ids else "available"
        catalog.append(d)
        catalog_by_id[entry.id] = d
    # Tally
    runtime_counts: dict[str, int] = {}
    for e in installed:
        runtime_counts[e["runtime"]] = runtime_counts.get(e["runtime"], 0) + 1

    # World-declared model hint (additive; None when no World / no hint). The
    # volatile installed/available state is derived HERE against the fresh
    # installed set — ARCHITECTURE §2.2/§3.2.
    try:
        from arail.world_mount import current_model_hint
        model_hint = _resolve_hint_for_gallery(
            current_model_hint(), installed_ids, catalog_by_id
        )
    except Exception:  # noqa: BLE001 — hint must never break the gallery
        model_hint = None

    return {
        "installed": installed,
        "catalog": catalog,
        "runtime_counts": runtime_counts,
        "model_hint": model_hint,
    }
