"""Call-scoped attribution for agent-sourced inference.

One ``contextvars.ContextVar`` carries "who is calling the model right now"
from wherever a call originates (the loader, a daemon, a top-level module,
or a subprocess round-trip) down to the single chokepoint that all of them
funnel through: ``ModelRouter.complete`` / ``.stream_complete``
(``arail/router/core.py``).

Why a contextvar and not a router attribute or a new parameter: routers are
cached and shared (``deep_policy._deep_router``, ``researcher._router_cache``,
...) — stamping an id on a shared router is an aliasing bug and a race.
Adding a parameter to ``complete()``/``stream_complete()`` means changing
every backend signature and every call site. A contextvar is call-scoped by
construction and has prior art in this exact codebase:
``costs._recap_depth_tls`` (set by ``recap/router_adapter.py``, read by
``cost_tracker.track()``) is the same shape, the same reader, the same
threading exposure.

See ``sprints/2026-09-20-buddy-front-and-center/ARCHITECTURE.md`` section
"Interface contracts" #1 and #4, and "Attribution: which layer sets the
context, and why three" for the full design story.
"""

from __future__ import annotations

import contextvars
import re
import secrets
import sys
import threading
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Optional


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentCall:
    """One call-scoped attribution record. Immutable — a new one is created
    on every context entry; nothing mutates a live record in place."""

    agent_id: Optional[str]        # None only for kind="system"
    kind: str                      # "agent" | "system"
    label: str                     # agent_id, or the system label ("world-forge")
    trace_id: str                  # 16 hex chars, minted at context entry
    brain: Optional[str] = None    # "reflex" | "standard" | "deep" | None
    effort: Optional[str] = None
    foreground: Optional[bool] = None
    deep_reason_code: Optional[str] = None       # verbatim from deep_policy.explain()
    deep_reason_detail: Optional[str] = None
    out_of_process: bool = False
    # Not in the interface-contract snippet's dataclass listing, but required
    # to fulfil the reentrancy promise stated in prose ("a different agent_id
    # nests ... and the record carries parent_agent_id") — additive, not a
    # redefinition of any documented field.
    parent_agent_id: Optional[str] = None


_CALL: "contextvars.ContextVar[Optional[AgentCall]]" = contextvars.ContextVar(
    "arail_agent_call", default=None
)

_SANITIZE_RE = re.compile(r"[^a-z0-9_.-]+")
_MAX_ID_LEN = 64


def _sanitize_agent_id(agent_id: Any) -> Optional[str]:
    """Coerce to a safe token, or None for an unattributed call.

    ``None`` or ``""`` (after coercion) -> None (unattributed; never invents
    a label). Anything else is lowercased and stripped of everything outside
    ``[a-z0-9_.-]``, with ``..`` sequences broken first so a hostile
    ``../../etc/passwd``-shaped id cannot survive as a path-traversal-looking
    token, then truncated to 64 chars.
    """
    if agent_id is None:
        return None
    s = str(agent_id).strip().lower()
    if not s:
        return None
    s = s.replace("..", "_")
    s = _SANITIZE_RE.sub("_", s)
    s = s.strip("_")
    if not s:
        return None
    return s[:_MAX_ID_LEN]


def _mint_trace_id() -> str:
    return secrets.token_hex(8)  # 16 hex chars


def call_site(depth: int = 2) -> Optional[str]:
    """``module:lineno`` of the caller ``depth`` frames up. One frame,
    ~1 microsecond — never ``traceback.format_stack()``. Best-effort: any
    failure (e.g. no such frame) returns None rather than raising.

    Used only for **unattributed** calls (``current() is None``) — the
    chokepoint calls this to name the exact call site in the Unattributed
    lane, which is DE2's instrument. Attributed calls don't need it; their
    ``agent_id`` already says who called.
    """
    try:
        frame = sys._getframe(depth)
        name = frame.f_globals.get("__name__", "?")
        return f"{name}:{frame.f_lineno}"
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public reads
# ---------------------------------------------------------------------------

def current() -> Optional[AgentCall]:
    """The innermost active record, or None. Never raises."""
    try:
        return _CALL.get()
    except Exception:  # pragma: no cover - ContextVar.get() with a default
        return None     # never actually raises, but "never raises" is a promise


# ---------------------------------------------------------------------------
# Context managers
# ---------------------------------------------------------------------------

@contextmanager
def agent_call(
    agent_id: Any,
    *,
    brain: Optional[str] = None,
    effort: Optional[str] = None,
    foreground: Optional[bool] = None,
    deep_reason: Optional[tuple] = None,
    trace_id: Optional[str] = None,
) -> Iterator[Optional[AgentCall]]:
    """Attribute the enclosed block to ``agent_id``.

    Reentrant: an inner ``agent_call`` with the same (sanitised) ``agent_id``
    reuses the outer ``trace_id`` and lineage, so a multi-step decision by
    one agent shares one trace id. A *different* ``agent_id`` nests — the
    innermost wins for the duration of the block, and the record's
    ``parent_agent_id`` names the outer agent.

    ``deep_reason`` accepts the ``(ok, reason_code, detail)`` or
    ``(reason_code, detail)`` tuple shape returned by
    ``deep_policy.explain()`` — whichever a caller has in hand — and stores
    ``reason_code``/``detail`` verbatim.

    Bad input: a falsy ``agent_id`` (``None`` or ``""`` after sanitising) is
    an *unattributed* call — nothing is set, ``current()`` keeps returning
    whatever the enclosing context already was. No label is invented.
    """
    sanitized = _sanitize_agent_id(agent_id)
    if sanitized is None:
        yield current()
        return

    reason_code: Optional[str] = None
    reason_detail: Optional[str] = None
    if deep_reason is not None:
        if len(deep_reason) == 3:
            _, reason_code, reason_detail = deep_reason
        elif len(deep_reason) == 2:
            reason_code, reason_detail = deep_reason

    outer = current()
    if outer is not None and outer.kind == "agent" and outer.agent_id == sanitized:
        tid = outer.trace_id
        parent = outer.parent_agent_id
    else:
        tid = trace_id or _mint_trace_id()
        parent = outer.agent_id if outer is not None else None

    call = AgentCall(
        agent_id=sanitized,
        kind="agent",
        label=sanitized,
        trace_id=tid,
        brain=brain,
        effort=effort,
        foreground=foreground,
        deep_reason_code=reason_code,
        deep_reason_detail=reason_detail,
        parent_agent_id=parent,
    )
    token = _CALL.set(call)
    try:
        yield call
    finally:
        _CALL.reset(token)


@contextmanager
def system_call(
    label: Any,
    *,
    trace_id: Optional[str] = None,
) -> Iterator[Optional[AgentCall]]:
    """Attribute the enclosed block to a non-agent system label
    (``"world-forge"``, ``"dictionary"``, ``"goal-parser"``, ...).

    Same reentrancy and bad-input rules as :func:`agent_call`, keyed on the
    label rather than an agent id.
    """
    sanitized = _sanitize_agent_id(label)
    if sanitized is None:
        yield current()
        return

    outer = current()
    if outer is not None and outer.kind == "system" and outer.label == sanitized:
        tid = outer.trace_id
        parent = outer.parent_agent_id
    else:
        tid = trace_id or _mint_trace_id()
        parent = outer.agent_id if outer is not None else None

    call = AgentCall(
        agent_id=None,
        kind="system",
        label=sanitized,
        trace_id=tid,
        parent_agent_id=parent,
    )
    token = _CALL.set(call)
    try:
        yield call
    finally:
        _CALL.reset(token)


def spawn_thread(target, *args, **kwargs) -> threading.Thread:
    """``threading.Thread(target=target, args=args, kwargs=kwargs)`` that
    also copies the calling ``contextvars.Context`` into the new thread —
    a bare ``Thread`` does not (proven in
    ``tests/test_agent_context_propagation.py``, A3).

    Does not call ``.start()`` — mirrors plain ``threading.Thread(...)``
    construction so callers keep their existing start-when-ready control
    flow; only the context-copying differs.
    """
    ctx = contextvars.copy_context()
    return threading.Thread(target=ctx.run, args=(target, *args), kwargs=kwargs)


# ---------------------------------------------------------------------------
# Subprocess round-trip (contract #9 — the out-of-process leg)
# ---------------------------------------------------------------------------

def to_subprocess_payload() -> dict:
    """What a parent should merge into a subprocess's JSON stdin so the
    child can reconstruct equivalent attribution locally. If there is no
    active context, a fresh ``trace_id`` is minted so the child still has
    one to carry (the parent authors the eventual trace either way)."""
    ctx = current()
    if ctx is None:
        return {"trace_id": _mint_trace_id(), "agent_id": None,
                "brain": None, "effort": None}
    return {
        "trace_id": ctx.trace_id,
        "agent_id": ctx.agent_id,
        "brain": ctx.brain,
        "effort": ctx.effort,
    }


def from_subprocess_payload(
    payload: dict, *, default_label: str = "subprocess"
) -> AbstractContextManager:
    """Reconstruct a context manager inside the child process from the
    parent's payload. Marked ``out_of_process=True`` so the chokepoint
    (``router/core.py``) skips writing its own trace record — the parent is
    the sole trace author (contract #9: exactly one writer per process, no
    interleaved appends).

    ``default_label`` covers the case the wire payload does not carry a
    system label at all (only ``trace_id``/``agent_id``/``brain``/``effort``
    per contract #9) — a child with no ``agent_id`` in its payload becomes a
    ``system_call`` under ``default_label`` (a real caller, e.g. the
    goal-parser runner, passes its own label rather than relying on the
    generic default). This keyword is additive to the documented single-arg
    signature; existing single-argument callers are unaffected.
    """
    agent_id = _sanitize_agent_id(
        payload.get("agent_id") if isinstance(payload, dict) else None
    )
    trace_id = (
        (payload.get("trace_id") if isinstance(payload, dict) else None)
        or _mint_trace_id()
    )
    brain = payload.get("brain") if isinstance(payload, dict) else None
    effort = payload.get("effort") if isinstance(payload, dict) else None

    @contextmanager
    def _cm() -> Iterator[AgentCall]:
        if agent_id is not None:
            call = AgentCall(
                agent_id=agent_id, kind="agent", label=agent_id,
                trace_id=trace_id, brain=brain, effort=effort,
                out_of_process=True,
            )
        else:
            label = _sanitize_agent_id(default_label) or "subprocess"
            call = AgentCall(
                agent_id=None, kind="system", label=label,
                trace_id=trace_id, out_of_process=True,
            )
        token = _CALL.set(call)
        try:
            yield call
        finally:
            _CALL.reset(token)

    return _cm()


# ---------------------------------------------------------------------------
# Billing source — the W2 contract
# ---------------------------------------------------------------------------

def billing_source(ctx: Optional[AgentCall]) -> str:
    """``agent:<id>`` / ``sys:<label>`` / ``unattributed`` — never a bare
    ``"agent"``, never a plausible-looking invented id, never dropped.

    Callers at the chokepoint must special-case ``self.billing_source ==
    "ui"`` *before* calling this (chat keeps its bucket regardless of any
    context that happens to be active) — this function only ever returns
    the per-agent/per-system/"unattributed" triad.
    """
    if ctx is None:
        return "unattributed"
    if ctx.kind == "agent" and ctx.agent_id:
        return f"agent:{ctx.agent_id}"
    if ctx.kind == "system" and ctx.label:
        return f"sys:{ctx.label}"
    return "unattributed"


# ---------------------------------------------------------------------------
# Hold ("hold all agents") — S4
# ---------------------------------------------------------------------------

# Ledger OQ3: the SRE crash watcher keeps watching and may still post a
# plain, non-model template alert while agents are held — every other
# proactive speaker goes silent. This is the one hard-coded exemption.
HOLD_EXEMPT_SPEAKERS = frozenset({"sre"})

_in_flight_lock = threading.Lock()
_in_flight_agent_calls = 0


class AgentHeldError(RuntimeError):
    """Raised at the router chokepoint when agents are held and the active
    context is an agent-sourced call. Never raised for kind="system", "ui",
    or an unattributed call — holding agents must not break the operator's
    own chat turn while he is holding them."""


def halt_gate(ctx: Optional[AgentCall]) -> None:
    """Refuse an agent-sourced call while held. Raises :class:`AgentHeldError`
    or returns. Admission control, not cancellation — a call already inside
    a backend when the switch flips runs to completion; this only refuses
    *new* calls at the door.

    D6 (REVIEW.md): the ``arail.scheduler`` import and the ``jobs_halted()``
    call are both inside the same ``try`` — this observability/hold-check
    machinery must never itself be the reason an inference fails. An
    ``ImportError`` or any other failure here fails **open** (does not
    refuse) rather than raising into the chokepoint, the same posture
    ``_halted()`` already takes for the informational ``halted`` field.
    """
    if ctx is not None and ctx.kind == "agent":
        try:
            from arail import scheduler as _job_scheduler
            held = _job_scheduler.jobs_halted()
        except Exception:  # noqa: BLE001 - hold-check must never break inference
            return
        if held:
            raise AgentHeldError(
                f"agent {ctx.agent_id!r} refused: agents are held "
                "(hold all agents is on)"
            )


def speech_gate(agent_id: Any) -> bool:
    """``True`` = allowed to speak right now. ``False`` when held, unless
    ``agent_id`` is in :data:`HOLD_EXEMPT_SPEAKERS`. Never raises — a
    resolution failure fails open (speak), because silencing on an
    unrelated error would be a worse surprise than one extra line."""
    sanitized = _sanitize_agent_id(agent_id) or ""
    if sanitized in HOLD_EXEMPT_SPEAKERS:
        return True
    try:
        from arail import scheduler as _job_scheduler
        return not _job_scheduler.jobs_halted()
    except Exception:  # noqa: BLE001
        return True


def hold_state() -> dict:
    """Snapshot for the Admin control and F17's copy-and-behaviour test:
    ``{held, changed_at, exempt_speakers, in_flight}``."""
    try:
        from arail import scheduler as _job_scheduler
        held = _job_scheduler.jobs_halted()
        changed_at = _job_scheduler.halt_changed_at()
    except Exception:  # noqa: BLE001
        held = False
        changed_at = None
    return {
        "held": held,
        "changed_at": changed_at,
        "exempt_speakers": sorted(HOLD_EXEMPT_SPEAKERS),
        "in_flight": in_flight_agent_calls(),
    }


def note_agent_call_entered(ctx: Optional[AgentCall]) -> None:
    """Count one more agent-sourced call as in-flight. Called by the router
    chokepoint immediately after ``halt_gate`` admits the call. No-op for
    non-agent contexts."""
    if ctx is None or ctx.kind != "agent":
        return
    global _in_flight_agent_calls
    with _in_flight_lock:
        _in_flight_agent_calls += 1


def note_agent_call_exited(ctx: Optional[AgentCall]) -> None:
    """Counterpart to :func:`note_agent_call_entered`, called in a
    ``finally`` so a raising backend call still decrements."""
    if ctx is None or ctx.kind != "agent":
        return
    global _in_flight_agent_calls
    with _in_flight_lock:
        _in_flight_agent_calls = max(0, _in_flight_agent_calls - 1)


def in_flight_agent_calls() -> int:
    return _in_flight_agent_calls


def _reset_for_tests() -> None:
    """Test-only: drop whatever the current context is. Tests that assert
    on a clean slate call this in a fixture; production code never does."""
    global _in_flight_agent_calls
    _CALL.set(None)
    with _in_flight_lock:
        _in_flight_agent_calls = 0
