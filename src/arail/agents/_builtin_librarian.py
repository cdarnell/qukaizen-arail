"""Librarian — the compiled-knowledge lifecycle agent.

The Librarian owns everything DaC in the lab: it runs the overnight
growth pass on the mounted World, scouts the lab's own signals (PKB
inbox, research notes, approved docs) for terms the World is missing,
and aggregates forge/grow/scout state for the DaC tab's focus panel.

Design laws (inherited from DaC's world-forge lineage):

- Every proposed term goes **through the gate**
  (``world_forge.assert_closed_sourced_graph``) — no exceptions.
- Provenance stays honest: locally-drafted terms are ``model:<name>``
  (dreamed); only a real captured URL earns the ``sourced`` tier.
- **A human approves** before anything compiles into the sealed World.
  The Librarian only files proposals; the operator clicks Approve.

Cadence discipline: one heavy job per pass — a growth pass and a scout
draft never run in the same tick, and both defer to the scheduler's
work windows and the global halt switch.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Optional

log = logging.getLogger(__name__)

_DEFAULT_INTERVAL_SEC = 1800       # growth/scout check cadence (30 min)
_DEFAULT_SCAN_HOURS = 6.0          # how often the scout mines lab signals


def _scan_interval_sec() -> float:
    raw = os.getenv("ARAIL_LIBRARIAN_SCAN_HOURS", "")
    try:
        v = float(raw)
        return (v if v > 0 else _DEFAULT_SCAN_HOURS) * 3600.0
    except ValueError:
        return _DEFAULT_SCAN_HOURS * 3600.0


class LibrarianAgent:
    """Background task owning the compiled-knowledge lifecycle.

    ``status`` follows the loader convention (idle | running | paused);
    ``snapshot()`` is the one JSON the DaC panel hydrates from
    (``GET /api/librarian/status``).
    """

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._status = "idle"          # idle | running | paused
        self._activity = ""            # human line: what it's doing right now
        self._last_grown_day: Optional[str] = None
        self._last_scan_ts: float = 0.0
        self._scout_note = ""

    @property
    def status(self) -> str:
        return self._status

    # ── lifecycle ───────────────────────────────────────────────────

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._status = "running"
        self._activity = "Watching the mounted World"
        self._task = asyncio.create_task(self._run())
        self._emit("Librarian is on duty — curating the lab's compiled "
                   "knowledge (growth, term scouting, provenance).", "info")

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._status = "idle"
        self._activity = ""

    def pause(self) -> None:
        if self._status == "running":
            self._status = "paused"
            self._activity = "Paused by the operator"

    def resume(self) -> None:
        if self._status == "paused":
            self._status = "running"
            self._activity = "Watching the mounted World"

    # ── the loop ────────────────────────────────────────────────────

    def _interval(self) -> float:
        raw = os.getenv("LAB_LIBRARIAN_INTERVAL_SEC", "")
        try:
            v = float(raw)
            return v if v > 0 else _DEFAULT_INTERVAL_SEC
        except ValueError:
            return _DEFAULT_INTERVAL_SEC

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._interval())
                if self._status != "running":
                    continue
                await self._tick()
        except asyncio.CancelledError:
            return

    async def _tick(self) -> None:
        # One heavy job per tick: growth first (overnight-gated inside
        # growth_tick); the scout only runs on ticks growth skipped.
        grown = False
        try:
            from arail.portal.world_routes import growth_tick
            before = self._last_grown_day
            self._last_grown_day = await growth_tick(self._last_grown_day)
            grown = self._last_grown_day != before
            if grown:
                self._activity = "Ran the overnight growth pass"
        except Exception as e:  # noqa: BLE001
            log.warning("librarian: growth tick failed: %s", e)

        if grown:
            return
        try:
            from arail.scheduler import current_window, jobs_halted
            if current_window() == "quiet" or jobs_halted():
                return
        except Exception:  # noqa: BLE001
            return

        # Horizon watch: act on the mounted World's declared agenda.json
        # watches (hybrid-only, consent-gated, findings staged for review —
        # see arail.research.agenda_watch). Airgapped labs no-op inside.
        await self.watch_horizon()

        if time.time() - self._last_scan_ts < _scan_interval_sec():
            return
        self._last_scan_ts = time.time()
        await self.scout_once()

    async def watch_horizon(self) -> dict:
        """One agenda-watch pass: the World's declared sources, checked on
        their own cadence, changes staged as review findings. Best-effort —
        a watch failure never breaks the Librarian's tick."""
        try:
            from arail.research import agenda_watch
            summary = await asyncio.to_thread(agenda_watch.tick)
        except Exception as e:  # noqa: BLE001
            log.warning("librarian: agenda watch failed: %s", e)
            return {"ok": False, "reason": str(e)[:200]}
        found = int(summary.get("findings", 0) or 0)
        if found:
            world = summary.get("world", "the mounted World")
            self._emit(
                f"Horizon watch: {found} change{'s' if found != 1 else ''} at "
                f"sources '{world}' declares — staged for your review on the "
                "DaC tab (Compiled KB queue).", "info",
                {"agenda_watch": {"action": "finding", "count": found}})
        return summary

    # ── term scouting (the MCP-in-2023 loop) ───────────────────────

    async def scout_once(self) -> dict:
        """One scout pass over the mounted World: mine lab signals for
        candidate terms, draft proposals for the ripe ones, file them in
        the sidecar for the operator's review. Returns a summary dict."""
        try:
            from arail import librarian_scout as ls
        except Exception:  # noqa: BLE001 — scout module not shipped yet
            return {"ok": False, "reason": "scout_unavailable"}
        prior = self._activity
        self._activity = "Scouting lab signals for missing terms"
        try:
            summary = await asyncio.to_thread(ls.scout_mounted_world)
        except Exception as e:  # noqa: BLE001
            log.warning("librarian: scout pass failed: %s", e)
            self._activity = prior
            return {"ok": False, "reason": str(e)[:200]}
        self._activity = "Watching the mounted World"
        proposed = summary.get("proposed", 0)
        if proposed:
            world = summary.get("world", "the mounted World")
            self._scout_note = (f"{proposed} term candidate"
                                f"{'s' if proposed != 1 else ''} awaiting review")
            self._emit(
                f"Scouted {proposed} term candidate"
                f"{'s' if proposed != 1 else ''} for '{world}' — "
                f"awaiting your review on the DaC tab.", "info",
                {"dac_proposals": {"action": "propose", "count": proposed}})
        return {"ok": True, **summary}

    # ── status for the DaC panel ───────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        world: dict[str, Any] = {"mounted": False}
        forge: dict[str, Any] = {}
        grow: dict[str, Any] = {}
        scout: dict[str, Any] = {
            "last_scan": self._last_scan_ts or None,
            "note": self._scout_note,
            "pending": 0,
        }
        try:
            from arail import world_mount as wm
            rec = wm.current_mount()
            if rec is not None:
                world = {"mounted": True, "slug": rec.world}
        except Exception:  # noqa: BLE001
            pass
        try:
            from arail.portal import world_routes as wr
            forge = {k: v for k, v in wr._forge_state.items()
                     if not k.startswith("_")}
            grow = {k: v for k, v in wr._grow_state.items()
                    if not k.startswith("_")}
        except Exception:  # noqa: BLE001
            pass
        try:
            from arail import librarian_scout as ls
            side = ls.load_mounted_sidecar()
            if side is not None:
                scout["pending"] = sum(
                    1 for p in side.get("proposals", [])
                    if p.get("status") == "pending")
                scout["last_scan"] = side.get("last_scan") or scout["last_scan"]
        except Exception:  # noqa: BLE001
            pass
        return {
            "status": self._status,
            "activity": self._activity,
            "world": world,
            "forge": forge,
            "grow": grow,
            "scout": scout,
        }

    # ── nightly reflection ─────────────────────────────────────────

    async def dream(self) -> str:
        """One-paragraph reflection for the dream daemon: what the
        compiled knowledge looks like tonight."""
        snap = self.snapshot()
        world = snap["world"]
        if not world.get("mounted"):
            return ("No World is mounted tonight. The lab's compiled "
                    "knowledge is waiting for an identity — forge or mount "
                    "one from the Worlds page.")
        pending = snap["scout"].get("pending", 0)
        lines = [f"Tending the '{world.get('slug')}' World."]
        if pending:
            lines.append(f"{pending} scouted term proposal"
                         f"{'s' if pending != 1 else ''} await the "
                         "operator's judgement.")
        if self._last_grown_day:
            lines.append(f"Last growth pass: {self._last_grown_day}.")
        lines.append("Everything that enters the World goes through the "
                     "gate, honestly labeled.")
        return " ".join(lines)

    # ── helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _emit(message: str, level: str, data: Optional[dict] = None) -> None:
        # F9 (ARCHITECTURE.md): the single funnel for everything Librarian
        # says proactively (growth/scout/horizon-watch announcements) —
        # gated here rather than at each caller.
        try:
            from arail import agent_context
            if not agent_context.speech_gate("librarian"):
                return
        except Exception:  # noqa: BLE001
            pass
        try:
            from arail.activity import activity_log
            activity_log.emit("librarian", message, level, data)
        except Exception as e:  # noqa: BLE001
            log.warning("librarian: emit failed: %s", e)


# Singleton — the loader picks this up via the agent contract.
librarian = LibrarianAgent()
