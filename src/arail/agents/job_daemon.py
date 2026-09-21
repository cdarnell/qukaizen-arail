"""Job daemon — a light in-process cron for the admin scheduler section.

Same shape as :mod:`arail.agents.dream_daemon` on purpose: a module-level
singleton, start()/stop()/status, a poll loop that respects the global
halt flag, and failure isolation per job. Where dream_daemon nudges
registered *agents* once per heavy window, this nudges registered *jobs*
on their own interval — any world's lab/ script, or any callable.

Deliberately not real cron. This codebase already made that call in
scheduler.py's own docstring ("deliberately simple: no cron, no DAG"),
and a polling loop with a plain interval covers every real use here —
"hourly," "nightly," "every 15 minutes." A job that genuinely needs
day-of-week or day-of-month precision can ask for it later; nothing
here forecloses that, it's just not built until a job needs it.

Each run's result is a JSON receipt on disk, not a database row — same
choice dream_daemon made (it writes markdown files, not SQL). One file
per run, under logs/scheduler/<job_id>/<iso-timestamp>.json. No schema
migration, no new table, nothing for spec/schema/migrations to gate.

Usage
-----
Register a job once, anywhere at import time (see JOBS below for the
two seeded on 2026-08-16)::

    register(Job(
        id="qukaizen-lab-ceiling-check",
        name="Ceiling check",
        world="qukaizen-team/worlds/qukaizen-lab",
        script="lab/ceiling_check.py",
        interval_sec=3600,
    ))

Start the daemon the same place dream_daemon starts (see app.py's
startup event, right next to ``dream_daemon.start()``)::

    from arail.agents.job_daemon import job_daemon
    job_daemon.start()
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time as _time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from arail import agent_context
from arail.scheduler import jobs_halted

log = logging.getLogger("arail.job_daemon")

LOGS_ROOT = Path(os.getenv("ARAIL_SCHEDULER_LOG_DIR", "logs/scheduler"))


@dataclass
class Job:
    id: str
    name: str
    world: str            # path to the world dir, relative to the lab root or absolute
    script: str            # path to the script, relative to `world`
    interval_sec: int      # poll-loop interval; matches dream_daemon's LAB_DREAM_POLL_SEC style
    args: list[str] = field(default_factory=list)
    watch_for: Optional[str] = None   # plain-language note: what this job is watching for,
                                        # set when a job is born from "flag this next time"
    enabled: bool = True

    @property
    def schedule_label(self) -> str:
        if self.interval_sec % 3600 == 0:
            hrs = self.interval_sec // 3600
            return "hourly" if hrs == 1 else f"every {hrs}h"
        if self.interval_sec % 60 == 0:
            return f"every {self.interval_sec // 60}m"
        return f"every {self.interval_sec}s"


# Registry of jobs. Mutated by register()/unregister() — matches dream_daemon's
# module-level _REGISTRY pattern.
_REGISTRY: dict[str, Job] = {}
_LAST_RUN_AT: dict[str, float] = {}  # job_id -> monotonic time of last completed run


def register(job: Job) -> None:
    _REGISTRY[job.id] = job


def unregister(job_id: str) -> None:
    _REGISTRY.pop(job_id, None)
    _LAST_RUN_AT.pop(job_id, None)


def _receipt_dir(job_id: str) -> Path:
    d = LOGS_ROOT / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _latest_receipt(job_id: str) -> Optional[dict]:
    d = LOGS_ROOT / job_id
    if not d.exists():
        return None
    files = sorted(d.glob("*.json"), reverse=True)
    if not files:
        return None
    try:
        return json.loads(files[0].read_text())
    except (OSError, json.JSONDecodeError):
        return None


async def _run_job(job: Job) -> dict:
    """Run one job's script as a subprocess, write a receipt, return it.

    Subprocess, not in-process import — a runaway or crashing job script
    can't take the portal process down with it. Same isolation goal as
    dream_daemon's per-agent try/except, one level stronger because these
    jobs are arbitrary world scripts, not trusted first-party agent code.
    """
    # L2 (ARCHITECTURE.md): the job daemon calls this from outside any
    # agent's own loop, per scheduled job rather than per agent. A job's
    # script runs as its own subprocess with no JSON attribution protocol
    # today (unlike the goal-parser's), so this context has no visible
    # effect on the trace store yet — it is here so a future job that
    # *does* call the router in-process (rather than shelling out) inherits
    # attribution for free rather than needing its own edit. system_call,
    # not agent_call: a scheduled world script is not one of the FIXED_LANES
    # agents and should not render as a user-defined agent lane.
    with agent_context.system_call(job.id):
        started = datetime.now(timezone.utc)
        cmd = ["python3", job.script, *job.args]
        receipt = {
            "job_id": job.id,
            "name": job.name,
            "started_at": started.isoformat(),
            "cmd": cmd,
            "cwd": job.world,
        }
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=job.world,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await proc.communicate()
            finished = datetime.now(timezone.utc)
            output = stdout.decode(errors="replace")
            receipt.update(
                finished_at=finished.isoformat(),
                exit_code=proc.returncode,
                status="ok" if proc.returncode == 0 else "error",
                output_tail=output[-4000:],
            )
        except (OSError, FileNotFoundError) as exc:
            finished = datetime.now(timezone.utc)
            receipt.update(
                finished_at=finished.isoformat(),
                exit_code=None,
                status="error",
                output_tail=f"failed to launch: {exc}",
            )

    out_path = _receipt_dir(job.id) / f"{started.strftime('%Y%m%dT%H%M%SZ')}.json"
    out_path.write_text(json.dumps(receipt, indent=1))
    _LAST_RUN_AT[job.id] = _time.monotonic()
    return receipt


async def run_job_now(job_id: str) -> dict:
    """Run a single job immediately, outside the poll loop. Used by the
    admin panel's 'Run now' button — same shape as security's run-scan."""
    job = _REGISTRY.get(job_id)
    if job is None:
        return {"ok": False, "error": f"no such job: {job_id}"}
    receipt = await _run_job(job)
    return {"ok": True, "receipt": receipt}


def list_jobs_status() -> list[dict]:
    """Everything the admin panel's Scheduler section needs, in one call."""
    out = []
    for job in _REGISTRY.values():
        last = _latest_receipt(job.id)
        out.append({
            "id": job.id,
            "name": job.name,
            "schedule": job.schedule_label,
            "world": job.world,
            "script": job.script,
            "watch_for": job.watch_for,
            "enabled": job.enabled,
            "last_run_at": last.get("finished_at") if last else None,
            "last_status": last.get("status") if last else None,
        })
    return out


class JobDaemon:
    """Background task that runs due jobs on their own interval.
    Same shape as dream_daemon.DreamDaemon — start/stop/status, a poll
    loop, failure isolation per iteration."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._status = "idle"  # idle | running

    @property
    def status(self) -> str:
        return self._status

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._status = "running"
        self._task = asyncio.create_task(self._run())
        log.info("job daemon online — %d job(s) registered", len(_REGISTRY))

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._status = "idle"

    async def _run(self) -> None:
        poll_sec = max(30, int(os.getenv("ARAIL_SCHEDULER_POLL_SEC", "60")))
        try:
            while True:
                try:
                    await self._tick()
                except Exception as exc:  # noqa: BLE001
                    log.warning("job daemon tick failed: %s", exc)
                await asyncio.sleep(poll_sec)
        except asyncio.CancelledError:
            return

    async def _tick(self) -> None:
        # Same guard dream_daemon uses: if the user hit "Halt jobs" on the
        # dashboard, background work — including scheduled jobs — stops too.
        if jobs_halted():
            return
        now = _time.monotonic()
        for job in list(_REGISTRY.values()):
            if not job.enabled:
                continue
            last = _LAST_RUN_AT.get(job.id)
            if last is not None and (now - last) < job.interval_sec:
                continue
            await _run_job(job)


# Module-level singleton — matches dream_daemon's pattern.
job_daemon = JobDaemon()


# ---------------------------------------------------------------------------
# Seeded jobs, 2026-08-16 — the two qukaizen-lab instruments already built
# and proven this session, now running on a clock instead of by hand. This
# is the concrete answer to "a system that's already tracking the
# experiments that run every hour."
# ---------------------------------------------------------------------------

register(Job(
    id="qukaizen-lab-ceiling-check",
    name="Ceiling check",
    world=os.path.expanduser("~/ProJects/qukaizen-team/worlds/qukaizen-lab"),
    script="lab/ceiling_check.py",
    interval_sec=3600,
))

register(Job(
    id="qukaizen-lab-spec-drift",
    name="Spec drift",
    world=os.path.expanduser("~/ProJects/qukaizen-team/worlds/qukaizen-lab"),
    script="lab/spec_drift.py",
    args=[os.path.expanduser("~/ProJects/qukaizen-team")],
    interval_sec=3600,
))
