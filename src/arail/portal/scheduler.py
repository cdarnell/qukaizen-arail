"""In-process inference priority queue + fast-path metrics.

This module exposes a single async semaphore that gates calls into
the local-LLM router. Lightweight HTTP paths (dashboard polls, system
health, etc.) bypass the semaphore via the FAST_PATH middleware so
they never queue behind a 30-second inference response.

Usage
-----
In a chat handler::

    async with scheduler.inference_slot("chat-stream"):
        async for item in _stream_sync_iterator(router.stream_complete(...)):
            ...

In the FAST_PATH middleware::

    scheduler.fast_path_record(request.url.path, elapsed_ms)

Reading metrics::

    data = scheduler.snapshot()  # JSON-safe dict

Configuration
-------------
``ARAIL_INFERENCE_CONCURRENCY`` — integer [1, 4], default 1.  Controls
the semaphore capacity.  Set higher only when you have confirmed that
the router supports concurrent calls without thrashing (e.g. multiple
CPU threads / GPUs).  Single-worker uvicorn (the default) means this
is a process-wide limit.

``ARAIL_INFERENCE_SLOT_TIMEOUT_S`` — float seconds, default 300.  Max
time a caller waits to *acquire* a slot before ``inference_slot`` raises
``InferenceSlotTimeout``. With capacity 1 (the default), one stuck
backend call previously blocked every other chat/agent/admin caller
indefinitely (Phase 1 review finding #19 — no acquire timeout existed
at all). This does not bound how long the call *inside* the slot may
run; it only guarantees waiters don't queue forever behind a hang.
"""

from __future__ import annotations

import asyncio
import os
from collections import deque
from contextlib import asynccontextmanager
from time import perf_counter
from typing import AsyncIterator

# ---------------------------------------------------------------------------
# Fast-path prefix set
# ---------------------------------------------------------------------------
# Path prefixes that the fastpath_meter middleware times AND lets through
# without acquiring the inference semaphore. Anything not in this list
# is "heavy" — heavy paths still pass through the middleware (it just
# doesn't time them) and acquire the semaphore inside the handler.
#
# Guard: do NOT add /api/chat, /api/teacher/ask, or /api/agents/<id>/ask
# here — those are the callers of inference_slot.
FAST_PATH_PREFIXES: tuple[str, ...] = (
    "/api/system/",
    "/api/jobs/",
    "/api/activity/",
    "/api/agents/status",
    "/api/admin/components",
    "/api/admin/check-updates",
    "/api/admin/perf",
    "/api/admin/cleanup",
    "/api/admin/security",
    "/api/pkb/",
    "/api/research/",
    "/static/",
    "/favicon.ico",
)


# ---------------------------------------------------------------------------
# Module-level state (single-process; per-worker when --workers >1)
# ---------------------------------------------------------------------------
_SEM: asyncio.Semaphore | None = None
_INFLIGHT: int = 0
_PENDING: int = 0
_WAIT_SAMPLES: dict[str, deque[float]] = {}   # ms per label, maxlen 256
_RUN_SAMPLES: dict[str, deque[float]] = {}    # ms per label, maxlen 256
_FAST_SAMPLES: deque[float] = deque(maxlen=512)   # fast-path latency ms
_COMPLETED: deque[float] = deque(maxlen=4096)     # epoch sec of completions

# Per-label counters for Prometheus /metrics exposition.
# These mirror _INFLIGHT/_COMPLETED but split by label so the scraper
# can distinguish chat-default from agent-pip, etc.
_INFLIGHT_BY_LABEL: dict[str, int] = {}
_COMPLETED_BY_LABEL: dict[str, int] = {}


class InferenceSlotTimeout(TimeoutError):
    """Raised when a caller waits longer than ARAIL_INFERENCE_SLOT_TIMEOUT_S
    to acquire an inference slot — i.e. a request already holding the slot
    is stuck. Callers already wrap ``inference_slot`` in a broad
    ``except Exception`` that surfaces a loud error to the user (see
    ``_run_chat_completion[_stream]`` in app.py), so raising here turns a
    silent, permanent hang into a visible, recoverable failure.
    """


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _capacity() -> int:
    """Read ARAIL_INFERENCE_CONCURRENCY env, clamp to [1, 4], default 1.

    Malformed or empty values silently fall back to 1.
    """
    raw = os.getenv("ARAIL_INFERENCE_CONCURRENCY", "").strip()
    try:
        val = int(raw)
    except (ValueError, TypeError):
        return 1
    return max(1, min(4, val))


def _acquire_timeout_s() -> float:
    """Read ARAIL_INFERENCE_SLOT_TIMEOUT_S, default 300s.

    Malformed, empty, or non-positive values silently fall back to the
    default rather than disabling the timeout — a hang here is exactly
    the failure mode this exists to prevent.
    """
    raw = os.getenv("ARAIL_INFERENCE_SLOT_TIMEOUT_S", "").strip()
    try:
        val = float(raw)
    except (ValueError, TypeError):
        return 300.0
    return val if val > 0 else 300.0


def _get_semaphore() -> asyncio.Semaphore:
    """Lazy-init the semaphore on the running event loop.

    Called only from within ``inference_slot``, which is always inside a
    running asyncio loop.  Because asyncio is cooperative and there is no
    ``await`` between the ``None`` check and the assignment, there is no
    race between two coroutines hitting this path simultaneously.
    """
    global _SEM
    if _SEM is None:
        _SEM = asyncio.Semaphore(_capacity())
    return _SEM


def _percentile(data: deque[float], p: float) -> float:
    """Return the p-th percentile of *data* (0–100).  Returns 0.0 if empty."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = (p / 100.0) * (len(sorted_data) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_data) - 1)
    frac = idx - lo
    return sorted_data[lo] + frac * (sorted_data[hi] - sorted_data[lo])


def _label_stats(samples: dict[str, deque[float]]) -> dict[str, dict]:
    return {
        label: {
            "p50": round(_percentile(q, 50), 2),
            "p95": round(_percentile(q, 95), 2),
            "n": len(q),
        }
        for label, q in samples.items()
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@asynccontextmanager
async def inference_slot(label: str = "chat") -> AsyncIterator[None]:
    """Acquire one inference slot. Records wait_ms + run_ms per label.

    Postcondition: the slot is always released, even if the body raises.
    The ``try/finally`` guarantees that a handler exception never deadlocks
    subsequent callers.

    Bad input: ``label=""`` is recorded under ``"_unknown"``; never raises.
    """
    global _INFLIGHT, _PENDING

    if not label:
        label = "_unknown"

    if label not in _WAIT_SAMPLES:
        _WAIT_SAMPLES[label] = deque(maxlen=256)
    if label not in _RUN_SAMPLES:
        _RUN_SAMPLES[label] = deque(maxlen=256)
    # Initialise per-label counters on first encounter (mirrors aggregate init).
    if label not in _INFLIGHT_BY_LABEL:
        _INFLIGHT_BY_LABEL[label] = 0
    if label not in _COMPLETED_BY_LABEL:
        _COMPLETED_BY_LABEL[label] = 0

    sem = _get_semaphore()
    _PENDING += 1
    t_wait_start = perf_counter()

    try:
        await asyncio.wait_for(sem.acquire(), timeout=_acquire_timeout_s())
    except asyncio.TimeoutError:
        _PENDING -= 1
        waited_s = perf_counter() - t_wait_start
        raise InferenceSlotTimeout(
            f"Timed out after {waited_s:.0f}s waiting for an inference "
            f"slot (label={label!r}). A request ahead of this one appears "
            f"stuck — its backend call may have hung. Try again in a "
            f"moment; if this repeats, restart the lab."
        ) from None

    t_wait_end = perf_counter()
    _PENDING -= 1
    _INFLIGHT += 1
    _INFLIGHT_BY_LABEL[label] += 1  # OBS6: increment after acquire, matching _INFLIGHT
    wait_ms = (t_wait_end - t_wait_start) * 1000.0
    _WAIT_SAMPLES[label].append(wait_ms)

    t_run_start = perf_counter()
    try:
        yield
    finally:
        t_run_end = perf_counter()
        run_ms = (t_run_end - t_run_start) * 1000.0
        _RUN_SAMPLES[label].append(run_ms)
        _COMPLETED.append(t_run_end)   # epoch-style via perf_counter is fine for 5m window
        _COMPLETED_BY_LABEL[label] += 1  # OBS6: monotonic counter, in finally
        _INFLIGHT -= 1
        _INFLIGHT_BY_LABEL[label] -= 1  # OBS6: decrement in finally, matching _INFLIGHT
        sem.release()


def fast_path_record(path: str, ms: float) -> None:
    """Append a fast-path latency sample.  Never raises; drops on overflow."""
    try:
        _FAST_SAMPLES.append(ms)
    except Exception:  # noqa: BLE001
        pass


def snapshot() -> dict:
    """Return a JSON-safe metrics snapshot.  Always succeeds.

    Shape::

        {
          "capacity": int,
          "in_flight": int,
          "pending": int,
          "completed_5m": int,          # run-completes in last 300 s
          "wait_ms":     {"<label>": {"p50": float, "p95": float, "n": int}},
          "run_ms":      {"<label>": {"p50": float, "p95": float, "n": int}},
          "fast_path_ms": {"p50": float, "p95": float, "n": int},
        }
    """
    now = perf_counter()
    cutoff = now - 300.0  # 5 minutes
    completed_5m = sum(1 for ts in _COMPLETED if ts >= cutoff)

    fast_stats: dict[str, object] = {
        "p50": round(_percentile(_FAST_SAMPLES, 50), 2),
        "p95": round(_percentile(_FAST_SAMPLES, 95), 2),
        "n": len(_FAST_SAMPLES),
    }

    return {
        "capacity": _capacity(),
        "in_flight": _INFLIGHT,
        "pending": _PENDING,
        "completed_5m": completed_5m,
        "wait_ms": _label_stats(_WAIT_SAMPLES),
        "run_ms": _label_stats(_RUN_SAMPLES),
        "fast_path_ms": fast_stats,
    }


def slot_pressure() -> dict:
    """Cheap read for the agent-trace chokepoint: capacity/in_flight/pending
    only, no percentile computation.

    ``snapshot()`` sorts up to 256 floats per label per call — ~50-200 µs,
    the wrong cost to add to every agent inference. This reads three module
    globals instead. ``held_by_other = in_flight > 0`` is the caller's job
    (agent calls are never themselves *in* the slot — V3 — so any non-zero
    ``in_flight`` here is necessarily someone else's chat/world-forge/etc.
    call).

    Known imprecision, stated not hidden: ``_INFLIGHT`` is a plain int
    mutated from the event-loop thread and read here, possibly from a
    ``to_thread`` worker — an individual sample may be stale by
    microseconds. Acceptable for a percentage over hundreds of samples; a
    lock on this hot path would be the wrong trade.
    """
    return {
        "capacity": _capacity(),
        "in_flight": _INFLIGHT,
        "pending": _PENDING,
    }


def per_label_snapshot() -> dict[str, dict]:
    """Per-label inference stats for Prometheus /metrics exposition.

    Always succeeds — returns an empty dict if no slots have ever been used.
    The snapshot() shape is intentionally unchanged; this helper is the new
    surface for Prometheus without disrupting the admin Performance card.

    Shape::

        {
          "<label>": {
            "in_flight":      int,    # current in-flight requests for this label
            "completed_total": int,   # monotonic counter since process boot
            "wait_ms": {"p50": float, "p95": float, "n": int},
            "run_ms":  {"p50": float, "p95": float, "n": int},
          }
        }
    """
    # Collect all known labels across all state dicts to handle the edge case
    # where a label appeared in wait/run samples but the per-label counters
    # were initialised before this helper existed (e.g. across a hot-reload).
    all_labels = (
        set(_INFLIGHT_BY_LABEL)
        | set(_COMPLETED_BY_LABEL)
        | set(_WAIT_SAMPLES)
        | set(_RUN_SAMPLES)
    )
    result: dict[str, dict] = {}
    for lbl in all_labels:
        wait_q = _WAIT_SAMPLES.get(lbl, deque())
        run_q = _RUN_SAMPLES.get(lbl, deque())
        result[lbl] = {
            "in_flight": _INFLIGHT_BY_LABEL.get(lbl, 0),
            "completed_total": _COMPLETED_BY_LABEL.get(lbl, 0),
            "wait_ms": {
                "p50": round(_percentile(wait_q, 50), 2),
                "p95": round(_percentile(wait_q, 95), 2),
                "n": len(wait_q),
            },
            "run_ms": {
                "p50": round(_percentile(run_q, 50), 2),
                "p95": round(_percentile(run_q, 95), 2),
                "n": len(run_q),
            },
        }
    return result
