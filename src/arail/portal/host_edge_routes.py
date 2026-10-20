"""``/api/host/v1/*``: the loopback edge between ARAIL and Buddy (sprint 2026-10-20-buddy-always-on).

ARCHITECTURE.md §5.1, §5.5 to §5.9. The routes live on the portal's existing port. Every
request goes through :func:`arail.buddy_host.admission.admit` first (root lab, loopback
bind, loopback peer, capability credential, contract hash, JSON Schema); a refusal is a
fixed-message error from the contract. ARAIL cannot verify a Telegram identity: it trusts
Buddy's credential, and the only actions are reductive (hold everything, pause the
research run).

Each route is a thin shell around a ``_core_*`` function so the conformance runner can
inject a fault into exactly one method.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from arail.buddy_host import CONTRACT, CONTRACT_SHA256, brain, budget, events, ledger
from arail.buddy_host import _time as T
from arail.buddy_host.admission import Admitted, EdgeError, admit, error_response, note_refusal

_log = logging.getLogger(__name__)
router = APIRouter()
BASE = CONTRACT["transport"]["base_path"]
POLL_AFTER_S = 20
_APPROVAL = CONTRACT["limits"]["approval"]
_seen_instances: set[str] = set()
_seen_lock = threading.Lock()


# --------------------------------------------------------------------------- ARAIL state (seams)
def _researcher() -> Any:
    from arail.agents.researcher import researcher
    return researcher


def _research_status() -> str:
    try:
        status = str(_researcher().status)
    except Exception:  # noqa: BLE001
        return "unknown"
    return status if status in ("idle", "running", "paused", "completed", "error") else "unknown"


def _consent_ok() -> bool:
    try:
        from arail.agents.consent import ConsentStore
        return bool(ConsentStore().is_allowed("https://api.telegram.org/"))
    except Exception:  # noqa: BLE001 - corrupt consent reads as no consent
        return False


def _hold_view() -> dict:
    """``{held, readable, changed_at}``. Unreadable reads as held: the edge never guesses."""
    from arail import scheduler
    readable = True
    held = False
    try:
        held = bool(scheduler.jobs_halted())
        if scheduler.halt_load_failed():
            readable = False
    except Exception:  # noqa: BLE001
        readable = False
    if not readable:
        held = True
    try:
        changed = T.from_naive_local(scheduler.halt_changed_at())
    except Exception:  # noqa: BLE001
        changed = None
    return {"held": held, "readable": readable, "changed_at": changed}


def _require_not_held() -> None:
    view = _hold_view()
    if not view["readable"]:
        raise EdgeError("hold_unreadable")
    if view["held"]:
        raise EdgeError("held")


# --------------------------------------------------------------------------- handshake
def _core_handshake(adm: Admitted) -> dict:
    from arail import airgap, tier
    body = adm.body
    instance_id = body["buddy"]["instance_id"]
    with _seen_lock:
        first = instance_id not in _seen_instances
        _seen_instances.add(instance_id)
    if first:
        events.write_edge_link(instance_id, body["buddy"], body["world"])
    view = brain.describe()
    return {
        "v": 1,
        "contract_sha256": CONTRACT_SHA256,
        "server_time": T.fmt(T.now_utc()),
        "lab": {"mode": airgap.lab_mode(), "tier": tier.get_current_tier(), "root": True},
        "consent": {"api.telegram.org": _consent_ok()},
        "hold": _hold_view(),
        "brain": view.public(),
        "budget": budget.default_budget().snapshot(),
        "research": {"status": _research_status()},
        "poll_after_s": POLL_AFTER_S,
    }


# --------------------------------------------------------------------------- trace.record
def _core_trace_record(adm: Admitted) -> dict:
    records = adm.body["records"]
    for rec in records:
        events.write_buddy_record(rec)
    return {"accepted": len(records)}


# --------------------------------------------------------------------------- hold_all
def _core_hold_all(adm: Admitted) -> dict:
    from arail import scheduler
    body = adm.body
    try:
        already = bool(scheduler.jobs_halted())
    except Exception:  # noqa: BLE001 - cannot read: halt anyway, the reductive direction
        already = False
    scheduler.halt_all_jobs()
    events.write_hold_all(trace_id=body["trace_id"], by=body["by"], already_held=already)
    return {"held": True, "already_held": already, "changed_at": T.from_naive_local(scheduler.halt_changed_at())}


# --------------------------------------------------------------------------- pause_research
def _record_sha256(body: dict) -> str:
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _check_freshness(body: dict, now) -> None:
    try:
        proposed = T.parse(body["proposal"]["proposed_at"])
        confirmed = T.parse(body["confirm"]["confirmed_at"])
        expires = T.parse(body["expires_at"])
    except ValueError:
        raise EdgeError("schema_invalid") from None
    ok = (proposed <= confirmed <= expires
          and (expires - proposed).total_seconds() <= _APPROVAL["ttl_s_max"]
          and abs((now - confirmed).total_seconds()) <= _APPROVAL["freshness_s"])
    if not ok:
        raise EdgeError("stale_approval")


def _pause_reply(approval_id: str, outcome: str, executed_at, replayed: bool) -> dict:
    return {"approval_id": approval_id, "outcome": outcome, "research_status": _research_status(),
            "executed_at": executed_at, "replayed": replayed}


def _core_pause_research(adm: Admitted) -> dict:
    body = adm.body
    approval_id = body["approval_id"]
    if approval_id != body["proposal"]["id"]:
        raise EdgeError("schema_invalid")
    record_sha = _record_sha256(body)
    nonce_sha = body["confirm"]["nonce_sha256"]
    led = ledger.default_ledger()
    try:
        row = led.lookup(approval_id, record_sha, nonce_sha)
    except ledger.ApprovalConflict:
        raise EdgeError("approval_conflict") from None
    if row is not None:  # a replay: answered from the ledger, never executed again
        return _pause_reply(approval_id, row.outcome or "indeterminate_restart", row.executed_at, True)

    _require_not_held()
    if body["confirm"]["confirmer"]["role"] != "confirmer":
        raise EdgeError("role_not_confirmer")
    now = T.now_utc()
    _check_freshness(body, now)
    try:
        led.begin(approval_id, nonce_sha, record_sha, T.fmt(now))
    except ledger.ApprovalConflict:
        raise EdgeError("approval_conflict") from None

    researcher = _researcher()
    status = _research_status()
    if status == "running":
        researcher.pause()
        outcome = "paused"
    elif status == "paused":
        outcome = "already_paused"
    else:
        outcome = "not_running"  # never pause() an idle researcher: it would report "paused" for nothing
    executed_at = T.fmt(T.now_utc())
    led.finish(approval_id, outcome, executed_at)
    events.write_action_executed(trace_id=body["trace_id"], by=body["confirm"]["confirmer"], outcome=outcome,
                                 approval_id=approval_id)
    return _pause_reply(approval_id, outcome, executed_at, False)


# --------------------------------------------------------------------------- plumbing
async def _serve(request: Request, method: str, core: Callable[[Admitted], dict], *, status: int = 200,
                 offload: bool = False) -> JSONResponse:
    try:
        adm = await admit(request, method)
    except EdgeError as exc:
        note_refusal(exc.code)
        return error_response(exc.code, **exc.extra)
    except Exception as exc:  # noqa: BLE001
        _log.error("buddy edge admission failed: %s", type(exc).__name__)
        return error_response("internal")
    try:
        out = await run_in_threadpool(core, adm) if offload else core(adm)
        return JSONResponse(status_code=status, content=out)
    except EdgeError as exc:
        return error_response(exc.code, **exc.extra)
    except Exception as exc:  # noqa: BLE001 - class name only: a message could carry request text
        _log.error("buddy edge %s failed: %s", method, type(exc).__name__)
        return error_response("internal")


@router.post(BASE + "/handshake")
async def handshake(request: Request):
    # the registry and the Ollama version probe block: keep them off the event loop
    return await _serve(request, "handshake", lambda a: _core_handshake(a), offload=True)


@router.post(BASE + "/trace/record")
async def trace_record(request: Request):
    return await _serve(request, "trace.record", lambda a: _core_trace_record(a), status=202)


@router.post(BASE + "/hold_all")
async def hold_all(request: Request):
    return await _serve(request, "hold_all", lambda a: _core_hold_all(a))


@router.post(BASE + "/pause_research")
async def pause_research(request: Request):
    # researcher.pause() belongs to the event loop thread, like /api/research/pause
    return await _serve(request, "pause_research", lambda a: _core_pause_research(a))


def register_routes(app) -> None:
    """Mount the edge on the portal app (called once from portal/app.py)."""
    app.include_router(router)
