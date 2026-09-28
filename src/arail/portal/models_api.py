"""Model registry API — the portal surface of ``arail.registry``.

Kept out of portal/app.py (the monolith) per the wiki/world/librarian
pattern: app.py only does ``app.include_router(models_router)``.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

log = logging.getLogger(__name__)

models_router = APIRouter(prefix="/api/models", tags=["models"])


def _registry():
    from arail.registry import get_registry
    return get_registry()


@models_router.get("/state")
async def models_state() -> Dict[str, Any]:
    """Everything the statusbar/switcher/banner/chips need, in one call."""
    return _registry().to_state()


class BindRequest(BaseModel):
    profile: str                  # task profile or "*" (with tab)
    entry_id: Optional[str] = None  # None clears the binding/override
    tab: Optional[str] = None


@models_router.post("/bind")
async def models_bind(req: BindRequest) -> Dict[str, Any]:
    try:
        _registry().bind(req.profile, req.entry_id, tab=req.tab)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    from arail.activity import activity_log
    scope = f"{req.tab} tab" if req.tab else "lab-wide"
    activity_log.emit(
        "registry",
        f"Model binding changed ({scope}): {req.profile} → "
        f"{req.entry_id or 'default'}", "info",
        {"model_event": {"kind": "bind", "profile": req.profile,
                         "entry_id": req.entry_id, "tab": req.tab}})
    return _registry().to_state()


@models_router.post("/health/refresh")
async def models_health_refresh() -> Dict[str, Any]:
    """Force a re-probe of every entry (runs off-loop; probes are 2s-capped)."""
    import anyio
    reg = _registry()
    reg._ensure_loaded()
    from arail.registry import health
    await anyio.to_thread.run_sync(
        lambda: health.run_preflight(reg, announce=False))
    return reg.to_state()


@models_router.get("/resolve")
async def models_resolve(profile: str, tab: Optional[str] = None) -> Dict[str, Any]:
    """Dry-run resolution — what would this profile/tab get right now?"""
    from dataclasses import asdict
    try:
        res = _registry().resolve(profile, tab)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "profile": res.profile,
        "tab": res.tab,
        "entry": res.entry.to_public() if res.entry else None,
        "requested": res.requested.to_public() if res.requested else None,
        "fallback": asdict(res.fallback) if res.fallback else None,
        "config_version": res.config_version,
    }


class RegisterArtifactRequest(BaseModel):
    run_id: str
    name: Optional[str] = None       # entry id / ollama model name override
    gguf_path: str                   # required -- /build's NucleusClient graduation
                                      # lookup is retired; the caller supplies the path
                                      # (Model Forge doesn't build GGUF this sprint either,
                                      # ARCHITECTURE.md §3 "Brief §2 GGUF output" — this
                                      # endpoint stays for artifacts registered by hand)


@models_router.post("/register-artifact")
async def models_register_artifact(req: RegisterArtifactRequest) -> Dict[str, Any]:
    """Register a graduated model into the registry.

    The entry lands as ``not_installed`` with an install hint; the interval
    health probe flips it healthy once the model appears in Ollama, at which
    point it is selectable in the switcher for every tab.
    """
    from arail.registry.core import ModelEntry
    import os

    grad: Dict[str, Any] = {}
    gguf = req.gguf_path
    name = (req.name or grad.get("skill_id")
            or f"nucleus-{req.run_id}").lower().replace("_", "-")

    entry = ModelEntry(
        id=name,
        display_name=name,
        provider_type="local",
        backend="ollama_native",
        endpoint=f"http://127.0.0.1:{os.getenv('OLLAMA_PORT', '11434')}/v1",
        model_id=name,
        tags=["fast"],
        source="artifact",
        artifact={"run_id": req.run_id, "gguf": gguf,
                  "graduated": grad or None},
        note="Graduated from Nucleus. Install with the generated Modelfile "
             "(ollama create) — health flips automatically once present.",
    )
    reg = _registry()
    reg.add_entry(entry)
    from arail.activity import activity_log
    activity_log.emit(
        "registry",
        f"Registered graduated model '{name}' (run {req.run_id}) — "
        "install it in Ollama to activate.", "success",
        {"model_event": {"kind": "artifact_registered", "entry_id": name}})
    install_hint = (
        f"# Install the graduated model into Ollama:\n"
        f"cat > /tmp/Modelfile.{name} <<EOF\nFROM {gguf or '<path-to-gguf>'}\nEOF\n"
        f"ollama create {name} -f /tmp/Modelfile.{name}")
    return {"entry": entry.to_public(), "install_hint": install_hint,
            "state": reg.to_state()}


# ── Boot model selection — "settle it once and for all" ────────────────
#
# Two slots, settled once into model_defaults.yaml, then left alone:
#   A — the primary, resident chat model (an installed Ollama tag)
#   B — the model AeroLLM (the deep / 2nd inference) loads (a directory
#       under ARAIL_MODELS_DIR)
#
# GET  /api/models/boot    — what to show: hidden | picker | problem
# POST /api/models/settle  — write the two slots, refusing dishonest choices
#
# Deliberately separate from GET /api/models/state (polled every 10s by
# every open tab — see _model_switcher.html) and from GET /api/chat/models
# (the chat-tab gallery monolith): this endpoint is fetched ONCE per page
# load by the boot banner, never on an interval, so it can afford the same
# per-load cost /api/chat/models already pays (an Ollama /api/tags probe +
# a models-dir scan) without violating the quiet-boot contract those other
# two surfaces are held to. See docs/adr equivalent discussion in
# CLAUDE.md "Model checkpoint paths stay relative / env-driven".

_BOOT_CANDIDATES_CACHE: Dict[str, Any] = {"ts": 0.0, "payload": None}
_BOOT_CANDIDATES_TTL = 30.0


def _fit_primary(model_id: str) -> Dict[str, Any]:
    from arail import model_specs as _specs
    from arail.registry.ceiling import PRIMARY_CEILING_B
    params, _src = _specs.resolve_params_b(model_id)
    if params is None:
        return {"verdict": "unknown", "detail": "parameter count unknown"}
    if params < PRIMARY_CEILING_B:
        return {"verdict": "fits", "detail": f"~{params:g}B"}
    return {"verdict": "too_big",
            "detail": f"~{params:g}B — at/over the {PRIMARY_CEILING_B:g}B "
                      "primary ceiling"}


def _fit_secondary(model_id: str, model_path: Optional[str]) -> Dict[str, Any]:
    from arail import hardware as _hw
    from arail import model_specs as _specs
    cap = _hw.secondary_model_cap_b()
    params, _src = _specs.resolve_params_b(model_id, model_path)
    if params is None:
        return {"verdict": "unknown", "detail": "parameter count unknown"}
    if params <= cap:
        return {"verdict": "fits", "detail": f"~{params:g}B (cap ~{cap:g}B)"}
    return {"verdict": "too_big",
            "detail": f"~{params:g}B — over the ~{cap:g}B this machine's "
                      "discovered RAM can hold stably"}


def _tier1_resident_model_differs(new_default_b: Optional[str]) -> bool:
    """True iff AeroLLM already has a DIFFERENT model resident than
    *new_default_b* — the signal the settle endpoint uses to say
    "applies on next start" instead of silently unloading multi-GB
    weights. Never constructs anything (mirrors registry/health.py's R5
    contract) — inspects the shared-instance dict only."""
    try:
        from arail.router.backends import AeroLLMBackend
        shared = getattr(AeroLLMBackend, "_shared", None) or {}
        for key, inst in shared.items():
            if getattr(inst, "_runtime", None) is None:
                continue
            resident_model = key.split("::", 1)[-1] if "::" in key else key
            if new_default_b is None or resident_model != new_default_b:
                return True
        return False
    except Exception:  # noqa: BLE001
        return False


def _boot_settlement() -> Dict[str, Any]:
    """Cheap-ish determination of mode + why. Called once per page load
    (never on an interval), so an Ollama presence probe here is the same
    cost /api/chat/models already pays on every chat page load."""
    from arail import model_defaults as _md
    slots = _md.resolve_slots()

    if not slots["settled"]:
        return {"mode": "picker", "settled": False, "problems": [],
                "default_a": slots["default_a"], "default_b": slots["default_b"]}

    problems: List[Dict[str, Any]] = []

    # Drift: env no longer matches what was settled (hand-edited .env).
    try:
        import yaml
        file_data = yaml.safe_load(Path(slots["path"]).read_text(
            encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        file_data = {}
    file_a = file_data.get("default_a") if isinstance(file_data, dict) else None
    if file_a and slots["default_a"] != file_a:
        problems.append({"slot": "a", "kind": "drift", "model": slots["default_a"],
                         "detail": f"env no longer matches the settled value "
                                   f"({file_a!r})"})
    if isinstance(file_data, dict) and "default_b" in file_data:
        file_b = file_data.get("default_b")
        if slots["default_b"] != file_b:
            problems.append({"slot": "b", "kind": "drift", "model": slots["default_b"],
                             "detail": f"env no longer matches the settled "
                                       f"value ({file_b!r})"})

    # Slot A — is it actually still installed in Ollama?
    a = slots["default_a"]
    try:
        from arail.chat import _ollama_installed_models
        installed_ids = {m["id"] for m in _ollama_installed_models()}
        short_installed = {i.split(":", 1)[0] for i in installed_ids}
        a_present = a in installed_ids or a.split(":", 1)[0] in short_installed
    except Exception:  # noqa: BLE001
        a_present = True  # probe failure must never manufacture a false alarm
    if not a_present:
        problems.append({"slot": "a", "kind": "missing", "model": a,
                         "detail": f"'{a}' is no longer installed in Ollama"})
    else:
        fit = _fit_primary(a)
        if fit["verdict"] == "too_big":
            problems.append({"slot": "a", "kind": "unfit", "model": a,
                             "detail": fit["detail"]})

    # Slot B — is the directory still there, and does it still fit?
    b = slots["default_b"]
    if b:
        models_dir = os.getenv("ARAIL_MODELS_DIR", "lab/models")
        b_path = b if os.path.isabs(b) else os.path.join(models_dir, b)
        if not os.path.isdir(b_path):
            problems.append({"slot": "b", "kind": "missing", "model": b,
                             "detail": f"not found at {b_path}"})
        else:
            fit = _fit_secondary(b, b_path)
            if fit["verdict"] == "too_big":
                problems.append({"slot": "b", "kind": "unfit", "model": b,
                                 "detail": fit["detail"]})

    mode = "problem" if problems else "hidden"
    return {"mode": mode, "settled": True, "problems": problems,
            "default_a": a, "default_b": b}


_TIER_ORDER = {"recommended": 0, "flagship": 1, "optional": 2}


def _slot_a_candidates(airgapped: bool) -> List[Dict[str, Any]]:
    from arail.chat import _ollama_installed_models, load_catalog
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for m in _ollama_installed_models():
        rid = m["id"]
        fit = _fit_primary(rid)
        rows.append({"id": rid, "name": rid, "source": "ollama",
                     "size_gb": m.get("size_gb"), "present": True,
                     "fit": fit, "install_command": None, "hf_url": None})
        seen.add(rid)
        seen.add(rid.split(":", 1)[0])
    catalog = sorted(
        (e for e in load_catalog() if e.source == "ollama"),
        key=lambda e: _TIER_ORDER.get(e.tier, 3))
    for e in catalog:
        if e.id in seen or e.id.split(":", 1)[0] in seen:
            continue
        fit = _fit_primary(e.id)
        rows.append({"id": e.id, "name": e.name, "source": "ollama",
                     "size_gb": e.size_gb, "present": False, "fit": fit,
                     "install_command": e.install or None, "hf_url": None})
        seen.add(e.id)
    return rows


def _slot_b_candidates(airgapped: bool) -> List[Dict[str, Any]]:
    from arail.chat import _mlx_dir_installed_models, load_catalog
    models_dir = os.getenv("ARAIL_MODELS_DIR", "lab/models")
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for m in _mlx_dir_installed_models():
        rid = m["id"]
        path = os.path.join(models_dir, rid)
        fit = _fit_secondary(rid, path)
        rows.append({"id": rid, "name": rid, "source": "mlx",
                     "size_gb": m.get("size_gb"), "present": True,
                     "fit": fit, "install_command": None, "hf_url": None})
        seen.add(rid)
    catalog = sorted(
        (e for e in load_catalog()
         if e.source in ("mlx", "hf") and not e.id.startswith("__")),
        key=lambda e: _TIER_ORDER.get(e.tier, 3))
    for e in catalog:
        if e.id in seen:
            continue
        path = os.path.join(models_dir, e.id)
        present = os.path.isdir(path)
        fit = _fit_secondary(e.id, path if present else None)
        hf_url = None if (present or airgapped) else (
            f"https://huggingface.co/{e.hf_repo}" if e.hf_repo else None)
        rows.append({"id": e.id, "name": e.name, "source": e.source,
                     "size_gb": e.size_gb, "present": present, "fit": fit,
                     "install_command": None if present else (e.install or None),
                     "hf_url": hf_url})
        seen.add(e.id)
    return rows


def _boot_candidates(airgapped: bool, *, refresh: bool = False) -> Dict[str, Any]:
    now = time.time()
    if (not refresh and _BOOT_CANDIDATES_CACHE["payload"] is not None
            and now - _BOOT_CANDIDATES_CACHE["ts"] < _BOOT_CANDIDATES_TTL):
        return _BOOT_CANDIDATES_CACHE["payload"]
    payload = {"a": _slot_a_candidates(airgapped),
               "b": _slot_b_candidates(airgapped)}
    _BOOT_CANDIDATES_CACHE["ts"] = now
    _BOOT_CANDIDATES_CACHE["payload"] = payload
    return payload


@models_router.get("/boot")
async def models_boot(full: bool = False, refresh: bool = False) -> Dict[str, Any]:
    """What the boot model-selection banner should show, and with what
    data. ``mode`` is one of:
      hidden   — settled, both slots healthy; render nothing.
      picker   — never settled; show the full two-slot picker.
      problem  — settled but something broke (missing / unfit / drift);
                 show a compact strip with a "Fix…" that expands to the
                 picker (``full=1``).
    Candidate lists (the expensive part — per-model fit checks against
    the catalog) are assembled only when ``full=1`` or mode != hidden.
    """
    from arail.airgap import is_airgapped
    airgapped = is_airgapped()
    settlement = _boot_settlement()

    payload: Dict[str, Any] = {
        "mode": settlement["mode"],
        "settlement": {"settled": settlement["settled"],
                       "problems": settlement["problems"]},
        "airgapped": airgapped,
        "hardware": {},
        "slots": {
            "a": {"label": "Load in GPU/Memory now",
                  "configured": settlement["default_a"], "allow_none": False},
            "b": {"label": "aeroLLM deep reference",
                  "configured": settlement["default_b"], "allow_none": True},
        },
    }
    try:
        from arail import hardware as _hw
        profile = _hw.load_or_discover()
        payload["hardware"] = {
            "total_gb": profile.total_ram_gb,
            "secondary_cap_b": _hw.secondary_model_cap_b(profile),
        }
    except Exception:  # noqa: BLE001
        pass

    stamp_bits = [settlement["mode"], str(settlement["settled"]),
                  str(len(settlement["problems"]))]
    payload["stamp"] = "|".join(stamp_bits)

    if full or settlement["mode"] != "hidden":
        candidates = _boot_candidates(airgapped, refresh=refresh)
        payload["slots"]["a"]["candidates"] = candidates["a"]
        payload["slots"]["b"]["candidates"] = candidates["b"]

    return payload


class SettleRequest(BaseModel):
    default_a: str
    default_b: Optional[str] = None


@models_router.post("/settle")
async def models_settle(req: SettleRequest) -> Dict[str, Any]:
    """Write the two boot slots to model_defaults.yaml — refusing any
    choice that isn't actually installed/on-disk, or that violates the
    answering-model ceiling (arail.registry.ceiling). ``default_b: null``
    (or omitted) is always a valid "no deep model" choice."""
    from arail import model_defaults as _md
    from arail.chat import detect_installed_models
    from arail.registry import store as _store
    from arail.registry.ceiling import ModelCeilingViolation, resolve_answering_model

    default_a = (req.default_a or "").strip()
    if not default_a:
        raise HTTPException(status_code=400, detail="default_a is required")

    installed = detect_installed_models()
    installed_ollama = {m["id"] for m in installed if m["runtime"] == "ollama"}
    short_installed = {i.split(":", 1)[0] for i in installed_ollama}
    a_short = default_a.split(":", 1)[0]
    if default_a not in installed_ollama and a_short not in short_installed:
        raise HTTPException(status_code=400, detail=(
            f"'{default_a}' is not installed in Ollama yet. "
            f"Run: ollama pull {default_a}"))
    try:
        resolve_answering_model(default_a, role="primary", backend="ollama_native")
    except ModelCeilingViolation as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    default_b = (req.default_b or "").strip() or None
    if default_b:
        models_dir = os.getenv("ARAIL_MODELS_DIR", "lab/models")
        model_path = (default_b if os.path.isabs(default_b)
                      else os.path.join(models_dir, default_b))
        if not os.path.isdir(model_path):
            raise HTTPException(status_code=400, detail=(
                f"'{default_b}' was not found at {model_path}. Try: "
                f"hf download mlx-community/{default_b} "
                f"--local-dir {model_path}"))
        try:
            resolve_answering_model(default_b, role="secondary",
                                    backend="aerollm", model_path=model_path)
        except ModelCeilingViolation as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    restart_required = _tier1_resident_model_differs(default_b)

    _md.write_defaults(default_a, default_b)
    _md.apply()

    reg = _registry()
    _store.reconcile_from_env(reg)

    # Force the next boot-banner read to see this settlement immediately —
    # a request-scoped TTL cache would otherwise show stale candidates for
    # up to _BOOT_CANDIDATES_TTL seconds right after settling.
    _BOOT_CANDIDATES_CACHE["payload"] = None

    from arail.activity import activity_log
    activity_log.emit(
        "registry",
        f"Model defaults settled: A={default_a}"
        + (f", B={default_b}" if default_b else ", B=(none)"),
        "success",
        {"model_event": {"kind": "settled", "default_a": default_a,
                         "default_b": default_b,
                         "restart_required": restart_required}})

    return {"ok": True, "restart_required": restart_required,
            "state": reg.to_state()}
