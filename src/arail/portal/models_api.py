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

from fastapi import APIRouter, HTTPException, Response
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
    gguf_path: Optional[str] = None  # explicit path when known client-side


@models_router.post("/register-artifact")
async def models_register_artifact(req: RegisterArtifactRequest) -> Dict[str, Any]:
    """Register a nucleus-graduated model into the registry.

    The entry lands as ``not_installed`` with an install hint; the interval
    health probe flips it healthy once the model appears in Ollama, at which
    point it is selectable in the switcher for every tab.
    """
    from arail.registry.core import ModelEntry
    import os

    grad: Dict[str, Any] = {}
    gguf = req.gguf_path
    if gguf is None:
        try:
            from arail.build.nucleus_client import NucleusClient
            grad = NucleusClient().graduation(req.run_id) or {}
            gguf = grad.get("gguf_path") or grad.get("model_path")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=502,
                detail=f"could not fetch graduation info for run "
                       f"'{req.run_id}': {exc}")
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


# ── Boot model resolution -- auto-resolved, pins optional ──────────────
#
# ARAIL resolves two slots itself at portal start (arail.model_resolve):
#   A -- the fast, resident chat model (an installed Ollama tag)
#   B -- the deep model AeroLLM streams in (a directory under ARAIL_MODELS_DIR)
# model_defaults.yaml holds operator pins + a cache of the last result; it is
# never a gate. (Replaces the PR #186 picker/"settle" flow;
# sprints/2026-10-20-boot-model-autoresolve.)
#
# GET  /api/models/boot    -- hidden | degraded + the resolution
# POST /api/models/pin     -- pin (or clear) one slot, re-resolve, warm async
# POST /api/models/settle  -- DEPRECATED alias for two pin writes
#
# Fetched ONCE per page load (never on an interval); the optional ?full=1
# adds the override UI's candidate lists, which cost an Ollama /api/tags
# probe plus a models-dir scan -- the same per-load cost /api/chat/models
# already pays.

_BOOT_CANDIDATES_CACHE: Dict[str, Any] = {"ts": 0.0, "payload": None}
_BOOT_CANDIDATES_TTL = 30.0

from arail.model_resolve import fit_primary as _fit_primary  # noqa: E402,F401
from arail.model_resolve import fit_secondary as _fit_secondary  # noqa: E402,F401


def _tier1_resident_model_differs(new_default_b: Optional[str]) -> bool:
    """True iff AeroLLM already has a DIFFERENT model resident than
    *new_default_b* -- the signal the pin endpoint uses to say
    "applies on next start" instead of silently unloading multi-GB
    weights. Never constructs anything (mirrors registry/health.py's R5
    contract) -- inspects the shared-instance dict only."""
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


def _slot_a_candidates(airgapped: bool) -> List[Dict[str, Any]]:
    from arail.model_resolve import slot_a_candidates
    return slot_a_candidates(airgapped)


def _slot_b_candidates(airgapped: bool) -> List[Dict[str, Any]]:
    from arail.model_resolve import slot_b_candidates
    return slot_b_candidates(airgapped)


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


def _current_resolution() -> Dict[str, Any]:
    """This process's resolution, else the cached one on disk (a request
    can arrive before the boot task finishes)."""
    from arail import model_defaults as _md
    from arail import model_resolve as _mr
    last = _mr.last_resolution()
    if last:
        return dict(last)
    cached = _md.read()["resolved"]
    return dict(cached) if cached else {}


@models_router.get("/boot")
async def models_boot(full: bool = False, refresh: bool = False) -> Dict[str, Any]:
    """What the boot strip should show. ``mode`` is ``hidden`` (nothing to
    say) or ``degraded`` (something the operator can act on). Never a
    picker, never a "drift" problem."""
    from arail.airgap import is_airgapped
    airgapped = is_airgapped()
    res = _current_resolution()

    def _slot(key: str) -> Dict[str, Any]:
        s = res.get(key) if isinstance(res.get(key), dict) else {}
        return {"model": s.get("model"), "source": s.get("source") or "none",
                "reason": s.get("reason"), "size_gb": s.get("size_gb")}

    degraded = [d for d in (res.get("degraded") or []) if isinstance(d, dict)]
    payload: Dict[str, Any] = {
        "mode": "degraded" if degraded else "hidden",
        "resolution": {"a": _slot("a"), "b": _slot("b")},
        "warm": {"a": (res.get("warm") or {}).get("a"),
                 "b": (res.get("warm") or {}).get("b")},
        "degraded": degraded,
        "airgapped": airgapped,
        "hardware": {},
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
    if full:
        candidates = _boot_candidates(airgapped, refresh=refresh)
        payload["candidates"] = candidates
    return payload


def _validate_pin(slot: str, model: Optional[str]) -> Optional[str]:
    """Validate one pin; returns the normalised value to store (a model id,
    ``"none"`` or None to clear) or raises HTTPException(400). Same bar as
    the old settle endpoint, plus a path-containment check for slot B."""
    from arail.registry.ceiling import ModelCeilingViolation, resolve_answering_model

    value = (model or "").strip()
    if not value or value.lower() == "auto":
        return None
    if value.lower() == "none":
        if slot == "a":
            raise HTTPException(status_code=400,
                                detail="the fast slot can't be pinned to 'none'")
        return "none"

    if slot == "a":
        from arail.chat import detect_installed_models
        installed = detect_installed_models()
        installed_ollama = {m["id"] for m in installed if m["runtime"] == "ollama"}
        short_installed = {i.split(":", 1)[0] for i in installed_ollama}
        if value not in installed_ollama and value.split(":", 1)[0] not in short_installed:
            raise HTTPException(status_code=400, detail=(
                f"'{value}' is not installed in Ollama yet. "
                f"Run: ollama pull {value}"))
        try:
            resolve_answering_model(value, role="primary", backend="ollama_native")
        except ModelCeilingViolation as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return value

    models_dir = os.getenv("ARAIL_MODELS_DIR", "lab/models")
    model_path = value if os.path.isabs(value) else os.path.join(models_dir, value)
    real = os.path.realpath(model_path)
    root = os.path.realpath(models_dir)
    inside = os.path.commonpath([real, root]) == root
    env_b = os.environ.get("AEROLLM_MODEL", "")
    env_points_here = (os.path.isabs(env_b) and os.path.realpath(env_b) == real
                       and os.path.isabs(value))
    if not (inside or env_points_here):
        raise HTTPException(status_code=400, detail=(
            f"'{value}' is outside the models directory ({models_dir}) -- "
            "put the model there, or set ARAIL_MODELS_DIR."))
    if not os.path.isdir(model_path):
        raise HTTPException(status_code=400, detail=(
            f"'{value}' was not found at {model_path}. Try: "
            f"hf download mlx-community/{value} --local-dir {model_path}"))
    try:
        resolve_answering_model(value, role="secondary",
                                backend="aerollm", model_path=model_path)
    except ModelCeilingViolation as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return value


async def apply_pin(slot: str, value: Optional[str], *, schedule_warm: bool = True
                    ) -> Dict[str, Any]:
    """Write one (already validated) pin, re-resolve + stamp env/registry,
    and schedule that slot's warm without waiting on it. Shared by the pin
    endpoint, the settle alias and POST /api/chat/default so a chip pick
    survives a reboot. Returns ``{"resolution", "restart_required"}``."""
    return await apply_pins({slot: value}, schedule_warm=schedule_warm)


async def apply_pins(pins: Dict[str, Optional[str]], *, schedule_warm: bool = True
                     ) -> Dict[str, Any]:
    import asyncio
    from arail import model_defaults as _md
    from arail import model_resolve as _mr
    from arail.portal import model_warmth as _mw

    restart_required = False
    if "b" in pins:
        new_b = pins["b"]
        restart_required = _tier1_resident_model_differs(
            None if new_b in (None, "none") else new_b)

    def _do() -> Dict[str, Any]:
        with _mr.LOCK:
            for slot, value in pins.items():
                _md.write_pin(slot, value)
            if "b" in pins:
                _mw.clear_sentinel()    # an explicit pick is the retry
            return _mr.resolve_and_apply(skip_b=_mw.crashed_model())

    try:
        res = await asyncio.to_thread(_do)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=(
            f"couldn't write model_defaults.yaml: {exc}"))
    _BOOT_CANDIDATES_CACHE["payload"] = None

    if schedule_warm:
        try:
            from arail.portal import app as _app
            if _app._boot_warm_enabled():
                if "a" in pins and (res.get("a") or {}).get("model"):
                    asyncio.create_task(_app._warm_slot_a())
                if "b" in pins and not restart_required:
                    asyncio.create_task(_app._warm_slot_b((res.get("b") or {}).get("model")))
        except Exception:  # noqa: BLE001
            pass
    return {"resolution": res, "restart_required": restart_required}


def _event(kind: str, text: str, **extra: Any) -> None:
    from arail.activity import activity_log
    activity_log.emit("registry", text, "success",
                      {"model_event": {"kind": kind, **extra}})


class PinRequest(BaseModel):
    slot: str                       # "a" | "b"
    model: Optional[str] = None     # id | "none" (b only) | None / "auto" clears


@models_router.post("/pin")
async def models_pin(req: PinRequest) -> Dict[str, Any]:
    """Pin (or clear) one slot. Validated like the old settle; the pin is
    written, the slot re-resolved, and the warm scheduled -- the response
    never waits on the warm."""
    slot = (req.slot or "").strip().lower()
    if slot not in ("a", "b"):
        raise HTTPException(status_code=400, detail="slot must be 'a' or 'b'")
    value = _validate_pin(slot, req.model)
    out = await apply_pin(slot, value)
    res = out["resolution"]
    _event("pinned", f"Model pin set: {slot.upper()}={value or 'auto'}",
           slot=slot, model=value, restart_required=out["restart_required"])
    return {"ok": True, "slot": slot, "pin": value,
            "resolution": {"a": res.get("a"), "b": res.get("b")},
            "restart_required": out["restart_required"],
            "state": _registry().to_state()}


class SettleRequest(BaseModel):
    default_a: str
    default_b: Optional[str] = None


@models_router.post("/settle")
async def models_settle(req: SettleRequest, response: Response) -> Dict[str, Any]:
    """DEPRECATED alias (remove next release): two pin writes. An omitted
    ``default_b`` is the ``none`` pin, as the old explicit null was."""
    default_a = (req.default_a or "").strip()
    if not default_a:
        raise HTTPException(status_code=400, detail="default_a is required")
    a = _validate_pin("a", default_a)
    b = _validate_pin("b", (req.default_b or "").strip() or "none")
    out = await apply_pins({"a": a, "b": b})
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = '</api/models/pin>; rel="successor-version"'
    _event("settled", f"Model defaults pinned (via deprecated /settle): A={a}, "
           f"B={b}", default_a=a, default_b=b,
           restart_required=out["restart_required"])
    return {"ok": True, "deprecated": True,
            "restart_required": out["restart_required"],
            "state": _registry().to_state()}
