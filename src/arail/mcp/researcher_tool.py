"""Single-tool stdio MCP server: run the Researcher once for one goal.

Used only by the opt-in Mastra Studio spike (sprints/2026-10-13-mastra-studio-spike).
stdio only: no port, no socket. Nothing but MCP frames may reach stdout, so the
module logs to stderr and the portal-side callers never import this for output.

Exposes exactly one tool, ``researcher_run_once``. It wraps
``ResearcherAgent._run`` (it does not reimplement it) and never starts the
background loop.
"""
from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

MAX_GOAL_CHARS = 2000
MAX_STEPS = 200
SUMMARY_CHARS = 500

# Error codes (returned as MCP tool errors, never raised past the boundary).
INVALID_GOAL = "invalid_goal"
RESEARCHER_BUSY = "researcher_busy"
WRONG_TIER = "wrong_tier"
GOAL_IN_PROGRESS = "goal_in_progress"


def _trunc(value: Any) -> str:
    return str(value if value is not None else "")[:SUMMARY_CHARS]


def lock_path():
    from pathlib import Path
    from arail.config import DATA_DIR
    return Path(DATA_DIR) / "mastra" / "run-once.lock"


@contextlib.contextmanager
def exclusive_run_lock():
    """Hold an exclusive lock for the whole run; yields True if acquired, else False.

    An flock on a persistent file: the kernel drops it when the holder dies, so a dead
    holder's lock is never stale, and we never delete or steal a live holder's lock
    (the file itself is never unlinked). The holder pid is written for diagnostics only.
    Taken BEFORE the busy / goal checks, so check-then-act cannot interleave between
    two run_once callers (CLI + portal, two tabs).
    """
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        try:
            yield True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def validate_goal(goal: Any) -> str | None:
    """Return an error code, or None when ``goal`` is acceptable."""
    if not isinstance(goal, str) or not goal.strip() or len(goal) > MAX_GOAL_CHARS:
        return INVALID_GOAL
    return None


def researcher_is_busy() -> bool:
    """True when a Researcher run is active in this lab (any process).

    The run-state file is written by ``ResearcherAgent._sync_workflow`` on every
    transition, so it also catches a run owned by the portal process.
    """
    from arail import goals as goals_mod
    from arail.agents.researcher import researcher

    if researcher.status in ("running", "paused"):
        return True
    state = goals_mod.load_run_state() or {}
    return state.get("status") in ("running", "paused")


def owner_goal_in_progress() -> bool:
    """True when the lab's current goal is unfinished.

    run_once sets a goal (``GoalStore.set_goal`` archives the previous one, exactly
    like POST /api/goal). To never displace work the owner has not finished, it
    refuses while the current goal is active with progress < 1.0.
    """
    from arail.agents.researcher import researcher
    cur = researcher.goal_store.get_current() or {}
    try:
        progress = float(cur.get("progress") or 0.0)
    except (TypeError, ValueError):
        progress = 0.0
    return bool(cur) and cur.get("status") == "active" and progress < 1.0


def _iso_to_ts(iso: Any) -> float:
    try:
        return datetime.fromisoformat(str(iso)).timestamp()
    except ValueError:
        return 0.0


def build_steps(trace_records: List[dict], activity_events: List[dict]) -> tuple[list, bool]:
    """Map one run's records to ordered steps. Returns (steps, truncated).

    Model calls come from ``agent_trace`` records, planning and experiment
    steps from the Researcher's own activity events. Only metadata is used, so
    the result is the same whether or not the flight recorder keeps bodies.
    """
    steps: list[dict] = []
    for rec in trace_records:
        ts = float(rec.get("ts") or 0.0)
        lat = (rec.get("latency_ms") or 0) / 1000.0
        steps.append({
            "name": f"model call: {rec.get('label') or rec.get('agent_id') or 'router'}",
            "kind": "model_call",
            "start_ts": ts - lat,
            "end_ts": ts,
            "input_summary": _trunc(f"{rec.get('attribution') or rec.get('agent_id') or 'agent'} "
                                    f"tokens_in={rec.get('tokens_in')}"),
            "output_summary": _trunc(f"model={rec.get('model')} outcome={rec.get('outcome')} "
                                     f"tokens_out={rec.get('tokens_out')}"),
        })
    for ev in activity_events:
        msg = str(ev.get("message") or "")
        low = msg.lower()
        if low.startswith("generated") and "hypotheses" in low:
            kind, name = "planning", "planning: research hypotheses"
        elif low.startswith(("created experiment", "added a measurable")):
            kind, name = "experiment", "experiment: " + msg[:60]
        elif low.startswith(("experiments complete", "results written", "source gathering")):
            kind, name = "experiment", "experiment phase: " + msg[:50]
        else:
            continue
        ts = _iso_to_ts(ev.get("ts"))
        steps.append({
            "name": name, "kind": kind, "start_ts": ts, "end_ts": ts,
            "input_summary": _trunc(low[:80]), "output_summary": _trunc(msg),
        })
    steps.sort(key=lambda s: s["start_ts"])
    truncated = len(steps) > MAX_STEPS
    return steps[:MAX_STEPS], truncated


async def run_once(goal: Any) -> Dict[str, Any]:
    """Run the Researcher exactly once. Returns a result dict or ``{"error": code}``."""
    bad = validate_goal(goal)
    if bad:
        return {"error": bad}
    from arail.tier import get_current_tier
    if get_current_tier() != "maximus":
        return {"error": WRONG_TIER}
    with exclusive_run_lock() as got:
        if not got:
            return {"error": RESEARCHER_BUSY}
        if researcher_is_busy():
            return {"error": RESEARCHER_BUSY}
        if owner_goal_in_progress():
            return {"error": GOAL_IN_PROGRESS}
        return await _run_locked(goal)


async def _run_locked(goal: str) -> Dict[str, Any]:
    from arail import agent_trace
    from arail.activity import activity_log
    from arail.agents.researcher import researcher
    from arail.skills.goal_parser import GoalParser

    t0 = time.time()
    run_id = secrets.token_hex(8)
    parser = GoalParser()
    try:
        parsed = parser.parse(goal)
    except Exception:  # noqa: BLE001 - same fallback as POST /api/goal
        parsed = parser.parse_offline(goal)
    researcher.goal_store.set_goal(parsed)
    try:
        await researcher._run(parsed, 0)
    except Exception as exc:  # noqa: BLE001 - _run catches its own; belt and braces
        return {"error": f"run_failed:{type(exc).__name__}"}

    current = researcher.goal_store.get_current() or {}
    exp_ids = list(current.get("experiments") or [])
    trace_records = [r for r in agent_trace.ring(10_000) if float(r.get("ts") or 0) >= t0]
    events = [e for e in list(activity_log._buffer)
              if e.get("source") == "researcher" and _iso_to_ts(e.get("ts")) >= t0]
    steps, truncated = build_steps(trace_records, events)
    if researcher.status == "error":
        return {"error": "run_failed:ResearcherError"}
    out: Dict[str, Any] = {
        "report": _trunc_report(current.get("report")),
        "experiment_id": exp_ids[0] if exp_ids else None,
        "trace_id": run_id,
        "steps": steps,
    }
    if truncated:
        out["truncated"] = True
    return out


def _trunc_report(report: Any) -> str:
    return str(report or "")[:20000]


def build_server():
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.exceptions import ToolError

    server = FastMCP("arail-researcher-once")

    @server.tool(name="researcher_run_once",
                 description="Run the ARAIL Researcher once for one goal and return its report and steps.")
    async def researcher_run_once(goal: str) -> Dict[str, Any]:
        result = await run_once(goal)
        if "error" in result:
            raise ToolError(result["error"])
        return result

    return server


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
