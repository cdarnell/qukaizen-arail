"""Bounded, append-only trace store for agent-sourced inference decisions.

A third persistence path beside ``activity.jsonl`` (a 200-event ring — a
chatty agent trace would evict the operator's activity history, F18) and
``costs.json`` (rewrites its whole file every call, an order of magnitude
more expensive than one ``jsonl`` append here). See ARCHITECTURE.md
"Hot-path cost budget" and "Tech debt" for the full justification.

Design invariants, load-bearing:

- :func:`record` never raises and never blocks the caller on disk I/O
  succeeding — the in-memory ring is updated first, SSE subscribers are
  notified second, and the disk append is attempted last, inside its own
  exception boundary so a disk failure never costs the live view (F1/F2).
- ``DATA_DIR`` is resolved **lazily, per call** — ``from arail import
  config; config.DATA_DIR`` inside the function that needs it, never a
  module-level constant — so two ``ARAIL_DATA_DIR`` roots never interleave
  (F14) and tests can monkeypatch ``arail.config.DATA_DIR`` directly
  without needing a second monkeypatch of this module's own attribute
  (contrast ``activity.py``'s ``LOG_FILE`` module constant, which does).
- Nothing here enumerates ``lab/instances/`` or aggregates across roots —
  per-World isolation is free and must stay free (VISION note 10).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Optional

SCHEMA = "arail.agent_trace/v1"

_log = logging.getLogger(__name__)

# Every key documented in ARCHITECTURE.md's "Record shape" — always present,
# absent data is None, never 0/""/"n/a".
_FIELDS: tuple[str, ...] = (
    "schema", "trace_id", "ts", "iso",
    "agent_id", "parent_agent_id", "kind", "attribution", "label", "call_site",
    "model", "backend", "provider", "entry_id",
    "brain", "effort", "foreground",
    "deep_reason_code", "deep_reason_detail",
    "ttft_ms", "ttft_status",
    "prefill_ms", "prefill_source",
    "tokens_in", "tokens_out", "latency_ms",
    "streamed", "out_of_process",
    "slot",
    "halted",
    "outcome", "error_class",
    "bodies",
)

# ---------------------------------------------------------------------------
# Module-global state (per-process, per-World by construction — see docstring)
# ---------------------------------------------------------------------------

_RING: Optional["deque[dict]"] = None
_SUBSCRIBERS: list[tuple[asyncio.Queue, Optional[asyncio.AbstractEventLoop]]] = []

_total_recorded = 0
_dropped_writes = 0
_last_drop_log_ts = 0.0
_write_count = 0

_overlap_hits = 0
_overlap_samples = 0


def _ring_maxlen() -> int:
    raw = os.getenv("ARAIL_TRACE_RING", "").strip()
    try:
        v = int(raw)
    except ValueError:
        v = 500
    return max(50, min(5000, v))


def _get_ring() -> "deque[dict]":
    global _RING
    if _RING is None:
        _RING = deque(maxlen=_ring_maxlen())
    return _RING


def _persist_enabled() -> bool:
    raw = os.getenv("ARAIL_TRACE_PERSIST", "1").strip()
    return raw.lower() not in ("0", "false", "no", "off")


def _trace_path():
    """Lazily-resolved path — see module docstring on why this is not a
    module-level constant."""
    from arail import config
    return config.DATA_DIR / "agent_traces.jsonl"


# ---------------------------------------------------------------------------
# record()
# ---------------------------------------------------------------------------

def record(**fields: Any) -> None:
    """Append one trace record. Never raises.

    Order (load-bearing, see module docstring): ring append, then SSE
    fan-out, then disk append. The disk append has its own inner exception
    boundary so a disk failure increments ``dropped_writes`` without
    touching the ring or the live view; an outer boundary catches anything
    else (a malformed field, an unexpected type) so this function truly
    never raises into the router chokepoint.
    """
    try:
        now = time.time()
        rec: dict[str, Any] = {k: None for k in _FIELDS}
        rec["schema"] = SCHEMA
        rec["ts"] = now
        rec["iso"] = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
        for k, v in fields.items():
            if k in rec:
                rec[k] = v
        if not rec.get("trace_id"):
            # Defensive only — every real caller supplies one from the
            # active AgentCall or a fresh mint at the chokepoint.
            import secrets
            rec["trace_id"] = secrets.token_hex(8)

        ring = _get_ring()
        ring.append(rec)
        global _total_recorded
        _total_recorded += 1

        _note_overlap(rec.get("kind"), rec.get("slot"))

        _fanout(rec)

        if _persist_enabled():
            try:
                _append_disk(rec)
            except Exception as exc:  # noqa: BLE001
                _note_drop(exc)
    except Exception as exc:  # noqa: BLE001 - record() must never raise
        _note_drop(exc)


def _note_overlap(kind: Any, slot: Any) -> None:
    global _overlap_hits, _overlap_samples
    if kind != "agent" or not isinstance(slot, dict):
        return
    _overlap_samples += 1
    if slot.get("held_by_other"):
        _overlap_hits += 1


def _note_drop(exc: Optional[Exception] = None) -> None:
    global _dropped_writes, _last_drop_log_ts
    _dropped_writes += 1
    now = time.time()
    if now - _last_drop_log_ts >= 60.0:
        _last_drop_log_ts = now
        try:
            _log.warning(
                "agent_trace: dropped a record (%d dropped this process): %s",
                _dropped_writes, exc,
            )
        except Exception:  # noqa: BLE001 - logging must never raise either
            pass


def _append_disk(rec: dict) -> None:
    global _write_count
    path = _trace_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_count += 1
    if _write_count % 64 == 0:
        try:
            if path.exists() and path.stat().st_size > 5 * 1024 * 1024:
                os.replace(path, path.with_suffix(".jsonl.1"))
        except OSError:
            pass
    with open(path, "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def ring(n: int = 200) -> list[dict]:
    """Last *n* records, oldest first. Never raises; empty list if none."""
    if n <= 0:
        return []
    return list(_get_ring())[-n:]


def stats() -> dict:
    return {
        "recorded": _total_recorded,
        "dropped_writes": _dropped_writes,
        "overlap_pct": _overlap_pct(),
    }


def _overlap_pct() -> float:
    if _overlap_samples == 0:
        return 0.0
    return round(100.0 * _overlap_hits / _overlap_samples, 1)


# ---------------------------------------------------------------------------
# SSE fan-out — the activity.py call_soon_threadsafe idiom, copied not
# re-derived (ARCHITECTURE.md interface contract #2).
# ---------------------------------------------------------------------------

def _fanout(rec: dict) -> None:
    try:
        running: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
    except RuntimeError:
        running = None

    dead: list[tuple[asyncio.Queue, Optional[asyncio.AbstractEventLoop]]] = []
    for entry in list(_SUBSCRIBERS):
        q, loop = entry
        if loop is None or loop is running:
            try:
                q.put_nowait(rec)
            except asyncio.QueueFull:
                dead.append(entry)
        elif loop.is_closed():
            dead.append(entry)
        else:
            def _put(q: asyncio.Queue = q) -> None:
                try:
                    q.put_nowait(rec)
                except asyncio.QueueFull:
                    pass
            try:
                loop.call_soon_threadsafe(_put)
            except RuntimeError:
                dead.append(entry)
    for entry in dead:
        if entry in _SUBSCRIBERS:
            _SUBSCRIBERS.remove(entry)


async def subscribe() -> AsyncGenerator[dict, None]:
    """Yield records as they arrive. Used by the admin SSE endpoint."""
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    entry = (q, asyncio.get_running_loop())
    _SUBSCRIBERS.append(entry)
    try:
        while True:
            rec = await q.get()
            yield rec
    finally:
        if entry in _SUBSCRIBERS:
            _SUBSCRIBERS.remove(entry)


# ---------------------------------------------------------------------------
# Lane roster + snapshot (ledger OQ4) — fixed built-in list; a user-defined
# loader agent outside this list aggregates into one generic lane; the
# mandatory sentinel lane is always present, never hidden (VISION note 9).
#
# Built incrementally across slices: S1 gives per-lane call aggregation from
# the ring; S4 (hold) and S5 (flight recorder) each add their own section to
# the same snapshot as those features land, per the slice plan.
# ---------------------------------------------------------------------------

FIXED_LANES: tuple[tuple[str, str], ...] = (
    ("buddy", "Buddy"),
    ("researcher", "Researcher"),
    ("browser", "Browser"),
    ("curator", "Curator"),
    ("sre", "SRE"),
    ("librarian", "Librarian"),
    ("presence", "Presence"),
    ("debt_advisor", "Debt Advisor"),
    ("consolidation_analyzer", "Consolidation Analyzer"),
    ("drafter", "Drafter"),
    ("forge", "Forge"),
)

# Verified in code (ARCHITECTURE.md "Where the spec is wrong" #5 and roster
# note): these two genuinely call no model today — a file-tail/service-probe
# watcher (sre) and a runtime-profile observer with no router reference
# (presence). Everything else defaults to the generic "no calls yet this
# session" empty state rather than a claim this builder could not verify.
_MODEL_FREE_LANES = frozenset({"sre", "presence"})

# The non-agent system callers this sprint wires with system_call() in S2:
# world-forge (world_routes.py), dictionary, goal-parser (the subprocess
# protocol), and recap (recap/router_adapter.py) — the "four non-agent
# callers" ARCHITECTURE.md's roster section counts without spelling out by
# name; inferred from the L3 module list plus the recap contextvar
# precedent. Flagged for architect review, not a blocking gap — an unknown
# sys label still renders (mandatory unattributed/generic fallback), it
# just wouldn't get a friendly display name until confirmed.
SYS_LANES: tuple[str, ...] = ("world-forge", "dictionary", "goal-parser", "recap")


def _empty_reason(agent_id: str, has_calls: bool) -> Optional[str]:
    if has_calls:
        return None
    if agent_id in _MODEL_FREE_LANES:
        return "does not call a model"
    return "no calls yet this session"


def lanes_snapshot() -> dict:
    """What the Admin agent-lanes endpoint serialises. Schema
    ``arail.agent_lanes/v1``."""
    records = ring(_get_ring().maxlen or 500)

    by_agent: dict[str, list[dict]] = {}
    unattributed_sites: dict[str, int] = {}
    user_defined_calls = 0
    known_ids = {aid for aid, _ in FIXED_LANES}

    for rec in records:
        kind = rec.get("kind")
        agent_id = rec.get("agent_id")
        if kind == "agent" and agent_id:
            by_agent.setdefault(agent_id, []).append(rec)
            if agent_id not in known_ids:
                user_defined_calls += 1
        elif rec.get("attribution") == "unattributed":
            site = rec.get("call_site") or "unknown"
            unattributed_sites[site] = unattributed_sites.get(site, 0) + 1

    lanes = []
    for agent_id, display in FIXED_LANES:
        calls = by_agent.get(agent_id, [])
        last = calls[-1] if calls else None
        lanes.append({
            "id": agent_id,
            "display": display,
            "group": "builtin",
            "calls": len(calls),
            "last": last,
            "brain": last.get("brain") if last else None,
            "effort": last.get("effort") if last else None,
            "ttft_ms": last.get("ttft_ms") if last else None,
            "ttft_status": last.get("ttft_status") if last else None,
            "tokens_out": last.get("tokens_out") if last else None,
            "deep_reason_code": last.get("deep_reason_code") if last else None,
            "deep_reason_detail": last.get("deep_reason_detail") if last else None,
            "empty_reason": _empty_reason(agent_id, bool(calls)),
        })

    return {
        "schema": "arail.agent_lanes/v1",
        "lanes": lanes,
        "unattributed": {
            "calls": sum(unattributed_sites.values()),
            "call_sites": sorted(
                ({"call_site": site, "calls": n}
                 for site, n in unattributed_sites.items()),
                key=lambda d: -d["calls"],
            ),
        },
        "user_defined": {"calls": user_defined_calls},
        "drops": {"dropped_writes": _dropped_writes},
        "window": {"ring_size": _get_ring().maxlen, "recorded": _total_recorded},
    }


def _reset_for_tests() -> None:
    """Test-only: drop all module state. Production code never calls this."""
    global _RING, _total_recorded, _dropped_writes, _last_drop_log_ts
    global _write_count, _overlap_hits, _overlap_samples
    _RING = None
    _SUBSCRIBERS.clear()
    _total_recorded = 0
    _dropped_writes = 0
    _last_drop_log_ts = 0.0
    _write_count = 0
    _overlap_hits = 0
    _overlap_samples = 0
