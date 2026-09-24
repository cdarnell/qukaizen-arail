"""Dream daemon — nightly reflection loop for agents.

Once per day during the lab's heavy work window, every registered
agent with ``dream: true`` in its ``AGENT.md`` gets its async
``dream()`` method called. The agent writes its reflection to
``lab/pkb/agents/<name>/dreams/YYYY-MM-DD.md`` — the daemon just
orchestrates when.

This is the memory-consolidation layer of the agent architecture.
Yesterday's dream is read back into today's system prompt by each
agent's prompt composer, so an agent literally "wakes up knowing"
what it figured out the night before.

## Design

- **Polling, not cron.** Every poll (default 15 min) we check the
  current work window. If it's heavy AND an agent hasn't dreamed
  today, dream.
- **Idempotent.** Each agent's ``dream()`` checks whether today's
  file already exists and skips when it does. Run the daemon as
  often as you like — one dream per agent per day.
- **Per-agent registry.** Agents opt in by calling
  ``register(agent_id, agent_instance)``. v1 registers Buddy in the
  portal startup; the dynamic agent loader auto-registers every
  agent folder that opts in via ``dream: true`` frontmatter.
- **Failure isolation.** A crashing ``dream()`` is logged and the
  daemon continues. One agent can't take the whole loop down.

## Opt-out

Set ``LAB_DREAMS=off`` to disable the daemon entirely — useful for
CI runs or labs that want the personality layer without the memory
layer.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from arail import agent_context
from arail.activity import activity_log
from arail.scheduler import current_window, jobs_halted

log = logging.getLogger(__name__)


class Dreamer(Protocol):
    """Duck-type contract for anything that can dream."""
    async def dream(self) -> str: ...


# Registry of (agent_id, agent_instance). Mutated by register() at
# startup — agents add themselves here, the daemon iterates on tick.
_REGISTRY: Dict[str, Dreamer] = {}


def register(agent_id: str, agent: Dreamer) -> None:
    """Opt an agent into the dream loop. Called at portal startup."""
    _REGISTRY[agent_id] = agent


def unregister(agent_id: str) -> None:
    _REGISTRY.pop(agent_id, None)


def _dream_file_for(agent_id: str, when: datetime) -> Path:
    """Where <agent_id>'s dream for the given UTC date gets written.

    Uses _pkb_root() lazily so a test can swap the PKB location by
    setting ARAIL_PKB_ROOT and the daemon picks it up per-call.
    """
    from arail.pkb import _pkb_root
    return (_pkb_root() / "agents" / agent_id / "dreams"
            / f"{when.strftime('%Y-%m-%d')}.md")


async def _dream_once(agent_id: str, agent: Dreamer) -> None:
    """Call ``agent.dream()`` unless today's file already exists."""
    today = datetime.now(timezone.utc)
    target = _dream_file_for(agent_id, today)
    if target.exists():
        return  # already dreamed today

    activity_log.emit(
        "dream",
        f"{agent_id} entering dream window…",
        "info",
        data={"agent": agent_id, "date": today.strftime("%Y-%m-%d")},
    )
    try:
        # L2 (ARCHITECTURE.md): the daemon calls agent.dream() from outside
        # start()'s loop, so without setting the context here a nightly
        # dream would be attributed to whatever the daemon's own task
        # happens to carry instead of the dreaming agent.
        with agent_context.agent_call(agent_id):
            reflection = await agent.dream()
    except Exception as e:  # noqa: BLE001
        activity_log.emit(
            "dream",
            f"{agent_id} dream failed: {type(e).__name__}: {e}",
            "warn",
        )
        return

    # The agent writes the file itself (schema + frontmatter stays
    # under its control). The daemon just logs completion.
    if target.exists():
        activity_log.emit(
            "dream",
            f"{agent_id} dreamed · {len(reflection)} chars",
            "success",
            data={"file": str(target)},
        )
    # If dream() ran but didn't write (e.g. model unreachable), the
    # agent itself emitted its own warning — no duplicate from here.


class DreamDaemon:
    """Background task that nudges each registered agent once per
    heavy window."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._status = "idle"  # idle | running
        self._last_tick: Optional[float] = None

    @property
    def status(self) -> str:
        return self._status

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._status = "running"
        self._task = asyncio.create_task(self._run())
        activity_log.emit(
            "dream",
            "Dream daemon online — reflections fire once per heavy window.",
            "info",
        )

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._status = "idle"

    async def _run(self) -> None:
        poll_sec = max(60, int(os.getenv("LAB_DREAM_POLL_SEC", "900")))  # 15 min
        try:
            while True:
                try:
                    await self._tick()
                except Exception as e:  # noqa: BLE001
                    log.warning("dream daemon tick failed: %s", e)
                await asyncio.sleep(poll_sec)
        except asyncio.CancelledError:
            return

    async def _tick(self) -> None:
        """One poll cycle. Decide whether to dream, and which agents."""
        # Respect the global halt flag — if the user hit 'Hold all agents'
        # on the dashboard they don't want background model calls.
        if jobs_halted():
            return

        # Only fire during the heavy window. Users can override by
        # setting LAB_DREAM_WINDOW=any to dream any time — useful
        # for testing.
        allowed_window = os.getenv("LAB_DREAM_WINDOW", "heavy")
        if allowed_window != "any" and current_window() != allowed_window:
            return

        for agent_id, agent in list(_REGISTRY.items()):
            await _dream_once(agent_id, agent)


# Module-level singleton — matches the BuddyAgent pattern.
dream_daemon = DreamDaemon()
