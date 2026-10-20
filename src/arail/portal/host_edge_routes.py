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

import asyncio
import contextvars
import hashlib
import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Optional

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response, StreamingResponse

from arail import agent_context
from arail.buddy_host import CONTRACT, CONTRACT_SHA256, brain, budget, events, ledger
from arail.buddy_host import _time as T
from arail.buddy_host.admission import Admitted, EdgeError, admit, error_response, note_refusal
from arail.portal import scheduler as inference

_log = logging.getLogger(__name__)
router = APIRouter()
BASE = CONTRACT["transport"]["base_path"]
POLL_AFTER_S = 20
SLOT_WAIT_S = float(CONTRACT["limits"]["brain_chat"]["slot_wait_s"])
STREAM_ERRORS = CONTRACT["errors"]["stream_errors"]
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


# --------------------------------------------------------------------------- brain.models
def _core_brain_models(adm: Admitted) -> dict:
    return {"object": "list", "data": [{"id": brain.MODEL_NAME, "object": "model", "owned_by": "arail"}]}


# --------------------------------------------------------------------------- brain.chat
@dataclass
class ChatPlan:
    header: brain.TurnHeader
    system: str
    prompt: str
    max_tokens: int
    schema: Optional[dict]
    stream: bool
    router: Any


class _End:
    """Marks the end of the producer's output."""


class _Fail:
    """A failure the producer hit: ``code`` is a contract error code."""

    def __init__(self, code: str) -> None:
        self.code = code


_END = _End()
_background: set = set()


def _refuse(plan_header: brain.TurnHeader, code: str) -> None:
    events.write_refused_model_call(code, trace_id=plan_header.trace_id, channel=plan_header.channel,
                                    pseudonym=plan_header.pseudonym, role=plan_header.role)


def _core_brain_chat(adm: Admitted) -> ChatPlan:
    """Steps 3 to 9 of ARCHITECTURE.md §5.6. Raises EdgeError; never calls a model."""
    from arail import airgap
    body = adm.body
    try:  # steps 2 (the rule the schema cannot state) to 5: no trusted identity yet
        brain.check_shape(body)
        brain.check_tools_and_content(body)
        brain.check_size(body)
        header = brain.parse_header(body["messages"][0]["content"])
    except EdgeError as exc:
        note_refusal(exc.code)
        raise
    try:  # steps 6 to 9: the header is valid, so the refusal is a model_call record naming who asked
        _require_not_held()                                                    # 6
        if header.channel == "telegram" and airgap.lab_mode() != "hybrid":     # 7
            raise EdgeError("mode_airgapped")
        view = brain.describe()                                                # 8
        if not view.available:
            raise EdgeError(view.reason or "brain_unavailable")
        if header.response_schema is not None and not view.structured_output:
            raise EdgeError("structured_output_unsupported")
        router = brain.build_router()
        try:                                                                   # 9
            budget.default_budget().check(header.pseudonym)
        except budget.BudgetRefused as refused:
            raise EdgeError(refused.code) from None
    except EdgeError as exc:
        _refuse(header, exc.code)
        raise
    system, prompt = brain.flatten(body["messages"])
    return ChatPlan(header, system, prompt, brain.max_tokens_of(body), header.response_schema,
                    bool(body.get("stream", False)), router)


def _drain(plan: ChatPlan, loop: asyncio.AbstractEventLoop, queue: "asyncio.Queue") -> None:
    """Runs in a worker thread inside a copy of the caller's context (the agent_call is on it)."""
    for item in plan.router.stream_complete(plan.prompt, plan.max_tokens, system=plan.system,
                                            response_schema=plan.schema):
        loop.call_soon_threadsafe(queue.put_nowait, item)


async def _produce(plan: ChatPlan, queue: "asyncio.Queue") -> None:
    """Holds the inference slot, runs the model, feeds ``queue``. Always ends with ``_END``."""
    from arail.router.backends import ModelResponse
    loop = asyncio.get_running_loop()
    h = plan.header
    try:
        async with inference.inference_slot("buddy-edge", acquire_timeout_s=SLOT_WAIT_S):
            budget.default_budget().begin_turn(h.pseudonym)  # a turn counts once it is let into inference
            with agent_context.agent_call("buddy", brain="reflex", foreground=True, trace_id=h.trace_id,
                                          channel=h.channel, identity=(h.pseudonym, h.role), origin="edge"):
                ctx = contextvars.copy_context()  # F45: the worker thread must carry the attribution
                inner: "asyncio.Queue" = asyncio.Queue()
                work = loop.run_in_executor(None, ctx.run, _drain, plan, loop, inner)
                work.add_done_callback(lambda _f: inner.put_nowait(_END))
                while True:
                    item = await inner.get()
                    if isinstance(item, _End):
                        break
                    if isinstance(item, ModelResponse):
                        total = brain.estimate_prompt_tokens(plan.system, plan.prompt) + int(item.tokens_used or 0)
                        budget.default_budget().add_tokens(total)
                    queue.put_nowait(item)
                await work  # re-raises a backend failure
    except inference.InferenceSlotTimeout:
        _refuse(h, "brain_busy")
        queue.put_nowait(_Fail("brain_busy"))
    except agent_context.AgentHeldError:
        queue.put_nowait(_Fail("held"))  # the chokepoint already wrote refused_halted
    except Exception as exc:  # noqa: BLE001 - class name only: a backend message could carry prompt text
        _log.error("buddy edge brain.chat failed: %s", type(exc).__name__)
        queue.put_nowait(_Fail("brain_failed"))
    finally:
        queue.put_nowait(_END)


def _chunk(cid: str, created: int, delta: dict, finish: Optional[str], usage: Optional[dict] = None) -> dict:
    out = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": brain.MODEL_NAME,
           "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage is not None:
        out["usage"] = usage
    return out


def _sse(obj: Any) -> bytes:
    return b"data: " + json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n\n"


def _usage_and_finish(plan: ChatPlan, final: Any) -> tuple[dict, str]:
    completion = int(getattr(final, "tokens_used", 0) or 0)
    prompt_tokens = brain.estimate_prompt_tokens(plan.system, plan.prompt)
    usage = {"prompt_tokens": prompt_tokens, "completion_tokens": completion, "total_tokens": prompt_tokens + completion}
    return usage, ("length" if completion >= plan.max_tokens else "stop")


async def _stream_body(plan: ChatPlan, first: Any, queue: "asyncio.Queue") -> AsyncIterator[bytes]:
    from arail.router.backends import ModelResponse
    cid, created = "chatcmpl-" + secrets.token_hex(8), int(time.time())
    yield _sse(_chunk(cid, created, {"role": "assistant", "content": ""}, None))
    item = first
    while not isinstance(item, _End):
        if isinstance(item, _Fail):
            yield _sse({"error": {"code": "brain_failed", "message": STREAM_ERRORS["brain_failed"]}})
            yield b"data: [DONE]\n\n"
            return
        if isinstance(item, ModelResponse):
            usage, finish = _usage_and_finish(plan, item)
            yield _sse(_chunk(cid, created, {}, finish, usage))
        elif item:
            yield _sse(_chunk(cid, created, {"content": item}, None))
        item = await queue.get()
    yield b"data: [DONE]\n\n"


async def _collect(plan: ChatPlan, first: Any, queue: "asyncio.Queue") -> Any:
    from arail.router.backends import ModelResponse
    parts: list = []
    final = None
    item = first
    while not isinstance(item, _End):
        if isinstance(item, _Fail):
            return None
        if isinstance(item, ModelResponse):
            final = item
        elif item:
            parts.append(item)
        item = await queue.get()
    if final is None:
        return None
    usage, finish = _usage_and_finish(plan, final)
    text = final.text if getattr(final, "text", None) else "".join(parts)
    return {"id": "chatcmpl-" + secrets.token_hex(8), "object": "chat.completion", "created": int(time.time()),
            "model": brain.MODEL_NAME,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish}],
            "usage": usage}


async def _serve_chat(request: Request) -> Response:
    try:
        adm = await admit(request, "brain.chat")
    except EdgeError as exc:
        note_refusal(exc.code)
        return error_response(exc.code, **exc.extra)
    except Exception as exc:  # noqa: BLE001
        _log.error("buddy edge admission failed: %s", type(exc).__name__)
        return error_response("internal")
    try:
        plan = await run_in_threadpool(_core_brain_chat, adm)
        queue: "asyncio.Queue" = asyncio.Queue()
        task = asyncio.create_task(_produce(plan, queue))
        _background.add(task)
        task.add_done_callback(_background.discard)
        first = await queue.get()
        if isinstance(first, _Fail):
            return error_response(first.code if first.code != "brain_failed" else "brain_unavailable")
        if isinstance(first, _End):
            return error_response("brain_unavailable")
        if plan.stream:
            return StreamingResponse(_stream_body(plan, first, queue), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
        out = await _collect(plan, first, queue)
        if out is None:
            return error_response("brain_unavailable")
        return JSONResponse(out)
    except EdgeError as exc:
        return error_response(exc.code, **exc.extra)
    except Exception as exc:  # noqa: BLE001
        _log.error("buddy edge brain.chat failed: %s", type(exc).__name__)
        return error_response("internal")


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


@router.get(BASE + "/brain/models")
async def brain_models(request: Request):
    return await _serve(request, "brain.models", lambda a: _core_brain_models(a))


@router.post(BASE + "/brain/chat/completions")
async def brain_chat(request: Request):
    return await _serve_chat(request)


def register_routes(app) -> None:
    """Mount the edge on the portal app (called once from portal/app.py)."""
    app.include_router(router)
