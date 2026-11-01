"""Throttle-aware deep-inference policy for the built-in agents.

Centralises one decision: *should this agent call run on the aeroLLM "2nd
inference" (deep backend), or the fast on-GPU model?* — and owns the single
shared deep ``ModelRouter`` so the lab never holds two resident copies (double
residency is an OOM on the target hardware).

Policy, in order:
  • Gated to the ``maximus`` tier (the only tier that ships aeroLLM) and an
    importable ``aerollm_api``; otherwise always fast.
  • Foreground calls (a user is waiting) may use deep immediately.
  • Background calls (proactive agent chatter, reflection, research) use deep
    only when it is *non-intrusive*: not halted, outside the active window, no
    operator presence, the runtime profile allows it, and Metal memory
    pressure is below a gentle background ceiling.
  • Any deep failure (including OOM) falls back to fast, and the fall-back is
    no longer silent: the failure is recorded per binary in
    ``arail.deep_verdict``, ``explain()`` says ``deep_failing`` until a
    ``./arailctl deep probe`` passes 5/5, and the fast call's trace carries
    that reason (sprint 2026-10-29-arail-deep-honest).

Kept dependency-light and side-effect-free so it unit-tests without loading
real model weights. All heavy imports are lazy and inside functions.
"""
from __future__ import annotations

import contextlib
import os
import threading
from typing import Any, Optional

from arail import agent_context
from arail.agent_context import AgentHeldError

# Gentle background pressure ceiling — well under the 0.75 hard chat guard in
# mlx_guard, so background deep work bows out long before the foreground would.
_BG_PRESSURE_DEFAULT = 0.60

_FAILED = object()  # sentinel: construction attempted and failed

_lock = threading.Lock()
_deep_router: Any = None   # cached ModelRouter, or _FAILED
_fast_router: Any = None   # cached fast ModelRouter, or _FAILED

# In-process counters. They explain nothing and clear nothing; they let a test
# (and a curious operator in a shell) see how often the fast model stood in.
_COUNTERS = {"fast_instead_of_deep": 0}


def counters() -> "dict[str, int]":
    """Copy of the in-process counters."""
    return dict(_COUNTERS)


def _enabled() -> bool:
    """Master kill switch (default on). Set ``ARAIL_AGENT_DEEP=0`` to force fast."""
    return os.getenv("ARAIL_AGENT_DEEP", "true").strip().lower() not in ("0", "false", "no")


def _bg_pressure_ceiling() -> float:
    raw = os.getenv("ARAIL_AEROLLM_BG_PRESSURE_PCT", "").strip()
    try:
        v = float(raw)
    except ValueError:
        return _BG_PRESSURE_DEFAULT
    # Clamp to a sane band; never above the hard chat guard (0.75).
    return min(max(v, 0.30), 0.75)


def _aerollm_importable() -> bool:
    try:
        import aerollm_api  # type: ignore  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _background_gate() -> "tuple[bool, str, str]":
    """Walk the background non-intrusiveness checks, in policy order.

    Returns ``(ok, reason_code, detail)`` — code/detail empty when ok.
    Extracted from background_safe() so explain() can name the failing
    gate without duplicating the decision logic.
    """
    from arail import runtime_profile, scheduler
    if scheduler.jobs_halted():
        return False, "deferred_now", "background jobs are halted"
    if scheduler.current_window() == "active":
        return (False, "deferred_now",
                "inside the active work window — deep passes run in "
                "heavy/idle hours")
    profile, _ = runtime_profile.resolve()
    if profile == "interactive":  # operator is present — yield to them
        return (False, "deferred_now",
                "operator present (interactive profile) — deep passes run "
                "when the lab is idle")
    if not runtime_profile.params(profile).get("background_aerollm"):
        return (False, "deferred_now",
                f"runtime profile '{profile}' disables background QueueLLM")
    try:
        from arail.router.mlx_guard import metal_memory_pressure
        pressure = metal_memory_pressure()
    except Exception:  # noqa: BLE001
        pressure = None
    if pressure is not None and pressure >= _bg_pressure_ceiling():
        return (False, "deferred_now",
                f"memory pressure {pressure:.0%} is above the background "
                "ceiling")
    return True, "", ""


def background_safe() -> bool:
    """True when running the heavy 2nd inference in the background right now
    would not be intrusive (window / presence / profile / memory)."""
    return _background_gate()[0]


def boot_warm_gate() -> "tuple[bool, str, str]":
    """Gate for the one-shot boot warm of the deep slot (sprints/2026-10-20-
    boot-model-autoresolve): ``(ok, reason_code, detail)``.

    Same tier / wheel checks as explain(), plus the gentle Metal memory-
    pressure ceiling -- but deliberately NOT operator presence, halted jobs
    or the active window: the operator is always present at boot, so
    background_safe() would never pass. Model fit is decided by the boot
    resolver before this is asked. The interval preload loop keeps the full
    background gate as the later re-warmer.
    """
    from arail.tier import is_maximus
    if not _enabled():
        return (False, "disabled",
                "ARAIL_AGENT_DEEP is off (agent deep-inference kill switch)")
    if not is_maximus():
        return (False, "tier_locked", "deep inference needs the maximus tier")
    if not _aerollm_importable():
        return (False, "wheel_missing", "aerollm_api is not installed")
    try:
        from arail.router.mlx_guard import metal_memory_pressure
        pressure = metal_memory_pressure()
    except Exception:  # noqa: BLE001
        pressure = None
    if pressure is not None and pressure >= _bg_pressure_ceiling():
        return (False, "deferred_now",
                f"memory pressure {pressure:.0%} is above the boot-warm ceiling")
    return True, "ok", "deep model can warm at boot"


def boot_warm_safe() -> bool:
    """Boolean projection of boot_warm_gate()."""
    return boot_warm_gate()[0]


def _deep_failing_detail() -> "Optional[str]":
    """The verdict's one-sentence detail while deep generation is failing on the
    binary this process loaded, else None. Never imports the wheel (the caller
    has just done that) and never raises."""
    try:
        from arail import deep_verdict
        v = deep_verdict.current(import_if_needed=False)
        return v.detail if v.state == "failing" else None
    except Exception:  # noqa: BLE001
        return None


def explain(*, foreground: bool) -> "tuple[bool, str, str]":
    """``prefer_deep`` with its reason: ``(ok, reason_code, detail)``.

    reason_code ∈ {"ok", "disabled", "tier_locked", "wheel_missing",
    "deep_failing", "deferred_now"}. prefer_deep() is a boolean projection of
    this walk, so the UI's explanation and the agents' decision can never
    drift.

    ``deep_failing`` (additive) means a probe, a deep call or a load failed on
    the binary this process loaded and no ``deep probe`` has passed 5/5 since.
    It applies to foreground and background alike and is distinct from
    ``deferred_now``, which says "not now"; this says "not working".
    """
    from arail.tier import is_maximus
    if not _enabled():
        return (False, "disabled",
                "ARAIL_AGENT_DEEP is off (agent deep-inference kill switch)")
    if not is_maximus():
        return (False, "tier_locked",
                "deep inference needs the maximus tier — run "
                "./arailctl upgrade maximus")
    if not _aerollm_importable():
        return (False, "wheel_missing",
                "aerollm_api is not installed — run ./arailctl deep install")
    failing = _deep_failing_detail()
    if failing is not None:
        return False, "deep_failing", failing
    if foreground:
        return True, "ok", "deep inference available"
    ok, code, detail = _background_gate()
    if not ok:
        return False, code, detail
    return True, "ok", "deep passes will run in the background"


def prefer_deep(*, foreground: bool) -> bool:
    """The single yes/no: use the aeroLLM 2nd inference for this call?"""
    return explain(foreground=foreground)[0]


def get_deep_router():
    """The single shared aeroLLM ``ModelRouter``, lazily built and cached.

    Returns None if construction fails (wheel missing, model dir missing, …).
    The failure is cached so we don't re-attempt a heavy load every call.
    """
    global _deep_router
    if _deep_router is _FAILED:
        return None
    if _deep_router is not None:
        return _deep_router
    with _lock:
        if _deep_router is _FAILED:
            return None
        if _deep_router is not None:
            return _deep_router
        try:
            from arail.router.core import ModelRouter
            _deep_router = ModelRouter(backend="aerollm", billing_source="agent")
        except Exception:  # noqa: BLE001
            _deep_router = _FAILED
            return None
        return _deep_router


def invalidate_deep_router() -> None:
    """Drop the cached deep (aeroLLM) router so the next get_deep_router()
    call re-resolves (sprints/2026-08-11-two-slot-chat-models Part 4).

    Called after an in-process deep-model swap replaces the underlying
    AeroLLMBackend singleton — without this, get_deep_router() would keep
    returning a ModelRouter wrapping the just-closed backend instance
    forever (it's cached unconditionally once built). Public and safe to
    call from production code, unlike _reset_for_tests (which also drops
    the unrelated _fast_router cache — this touches only _deep_router).
    """
    global _deep_router
    with _lock:
        _deep_router = None


_fast_cfgv: "int | None" = None


def _get_fast_router():
    """Fast (Tier 0) router via the model registry.

    Cached per registry config_version — a binding change in the portal
    reaches the agents without a restart (the old module-level _FAILED
    sentinel could permanently latch a transient failure)."""
    global _fast_router, _fast_cfgv
    with _lock:
        try:
            from arail.registry import get_registry
            reg = get_registry()
            res = reg.resolve("fast", tab="agents")
            if (_fast_router is not None and _fast_router is not _FAILED
                    and _fast_cfgv == res.config_version):
                return _fast_router
            _fast_router = res.router(billing_source="agent")
            _fast_cfgv = res.config_version
            return _fast_router
        except Exception:  # noqa: BLE001
            return None


def _agent_stream_fast_enabled() -> bool:
    """The only way to get a real (not honest-n/a) TTFT for any agent call
    today (ARCHITECTURE.md finding 1) — the fast branch streams from
    Ollama's native /api/chat with stream:true instead of stream:false,
    joining the deltas back into the identical string complete() would
    return. Default on; ``ARAIL_AGENT_STREAM_FAST=0`` reverts to the old
    non-streaming call and every agent's TTFT becomes an honest ``n/a``
    (F20) — one env var, not a revert of this function."""
    return os.getenv("ARAIL_AGENT_STREAM_FAST", "1").strip().lower() not in (
        "0", "false", "no")


def _join_stream(router: Any, prompt: str, *, max_tokens: int,
                 temperature: float, system: Optional[str]) -> Optional[str]:
    """Consume ``stream_complete`` and return the same string ``complete()``
    would — the terminal ``ModelResponse.text`` is the ground truth when
    present, deltas joined otherwise. Raises on any failure; the caller
    falls back to ``complete()`` unconditionally (F20)."""
    parts: list[str] = []
    final_text: Optional[str] = None
    for item in router.stream_complete(prompt, max_tokens=max_tokens,
                                       temperature=temperature, system=system):
        if isinstance(item, str):
            parts.append(item)
        else:
            final_text = getattr(item, "text", None)
    return final_text if final_text is not None else "".join(parts)


def complete_preferring_deep(
    prompt: str,
    *,
    foreground: bool,
    fast_router: Any = None,
    max_tokens: int = 256,
    temperature: float = 0.7,
    system: Optional[str] = None,
) -> Optional[str]:
    """Complete on the deep backend when policy allows, else fast.

    Any deep failure (including OOM) falls back to fast — we never crash an
    agent or risk OOMing the box for a background nicety. ``None`` is returned
    only when both paths fail. Callers may pass an existing ``fast_router`` to
    avoid constructing a second one.

    The fall-back is not silent (sprint 2026-10-29-arail-deep-honest). The
    backend has already recorded the failure per binary, ``explain()`` now
    says ``deep_failing`` until a probe passes, and every fast call made
    because of it runs inside ``agent_context.with_deep_reason``, so its trace
    carries ``deep_reason_code="deep_failing"`` and the detail.
    """
    ok, code, detail = explain(foreground=foreground)
    stamp: "Optional[tuple[str, str]]" = None
    if code == "deep_failing":
        # Known broken: no deep attempt at all. Calling it again could only fail again.
        _COUNTERS["fast_instead_of_deep"] += 1
        stamp = (code, detail)
    elif ok:
        router = get_deep_router()
        if router is not None:
            try:
                resp = router.complete(prompt, max_tokens=max_tokens,
                                       temperature=temperature, system=system)
                text = (resp.text or "").strip() if resp else ""
                if text:
                    return text
                stamp = ("deep_failing", _failed_detail("the deep model returned no text"))
            except AgentHeldError:
                # F8 (ARCHITECTURE.md): agents are held -- do not also try
                # fast, which would hit the chokepoint a second time and
                # emit a second refused_halted trace for one decision.
                return None
            except Exception as exc:  # noqa: BLE001
                # Fall through to fast — never crash, never OOM — but make
                # the degradation VISIBLE via the registry health state.
                try:
                    from arail.registry import get_registry
                    from arail.registry.store import TIER1_ID
                    get_registry().report_failure(TIER1_ID, exc)
                except Exception:  # noqa: BLE001
                    pass
                stamp = ("deep_failing", _failed_detail(f"{type(exc).__name__}: {exc}"))
    reason = (agent_context.with_deep_reason(*stamp) if stamp is not None
              else contextlib.nullcontext())
    with reason:
        return _complete_on_fast(prompt, fast_router=fast_router, max_tokens=max_tokens,
                                 temperature=temperature, system=system)


def _failed_detail(what: str) -> str:
    """Detail for a fast call made because this call's deep attempt just failed:
    the verdict's own sentence if it is failing now, else a plain one. The plain one quotes the exception as
    raised, and it lands in an agent's trace, so it is one printable line (QA-16)."""
    from arail import deep_verdict
    return _deep_failing_detail() or f"deep generation failed: {deep_verdict._clip(what, 160)}"


def _complete_on_fast(
    prompt: str,
    *,
    fast_router: Any,
    max_tokens: int,
    temperature: float,
    system: Optional[str],
) -> Optional[str]:
    """The fast half of ``complete_preferring_deep`` (unchanged behaviour)."""
    fr = fast_router or _get_fast_router()
    if fr is None:
        return None
    if _agent_stream_fast_enabled():
        try:
            text = _join_stream(fr, prompt, max_tokens=max_tokens,
                                temperature=temperature, system=system)
            return (text or "").strip() or None
        except AgentHeldError:
            # F8: one refusal is enough -- do not also try .complete().
            return None
        except Exception:  # noqa: BLE001 - unconditional fallback (F20)
            pass
    try:
        resp = fr.complete(prompt, max_tokens=max_tokens,
                           temperature=temperature, system=system)
        return ((resp.text or "").strip() or None) if resp else None
    except AgentHeldError:
        return None
    except Exception:  # noqa: BLE001
        return None


def _reset_for_tests() -> None:
    """Test-only: drop the cached routers and the counters."""
    global _deep_router, _fast_router
    with _lock:
        _deep_router = None
        _fast_router = None
    _COUNTERS["fast_instead_of_deep"] = 0
