"""Presence — silent runtime-profile observer.

Ticks every ``tick_interval_sec`` (default 60), calls
``arail.runtime_profile.resolve()``, diffs the resolved
``(profile, source)`` against last tick, and emits an
``activity_log`` event with ``source="profile"`` on transition.

No speech. No state on disk by default. The dashboard pill subscribes
to the activity SSE stream and updates the DOM directly from the
emitted events.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Optional

log = logging.getLogger(__name__)

_DEFAULT_INTERVAL_SEC = 60


class PresenceAgent:
    """Observer of runtime profile transitions.

    The ``status`` attribute is read by the loader's health checks
    and surfaces in ``/api/agents/list`` as the agent's heartbeat.
    """

    def __init__(self) -> None:
        self.status: str = "idle"
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last: Optional[tuple[str, str]] = None

    def _interval(self) -> float:
        raw = os.getenv("LAB_PRESENCE_AGENT_INTERVAL_SEC", "")
        try:
            v = float(raw)
            return v if v > 0 else _DEFAULT_INTERVAL_SEC
        except ValueError:
            return _DEFAULT_INTERVAL_SEC

    def _tick(self) -> None:
        try:
            from arail.runtime_profile import resolve, snapshot
            from arail.activity import activity_log
        except Exception as e:  # noqa: BLE001
            log.warning("presence: imports unavailable: %s", e)
            return

        try:
            current = resolve()
        except Exception as e:  # noqa: BLE001
            log.warning("presence: resolve() failed: %s", e)
            return

        if self._last is None:
            # First tick — record but don't emit; we only emit transitions.
            self._last = current
            return

        if current == self._last:
            return

        old_profile, old_source = self._last
        new_profile, new_source = current
        self._last = current

        snap: dict[str, Any] = {}
        try:
            snap = snapshot()
        except Exception:
            snap = {"profile": new_profile, "source": new_source}

        message = self._format_message(old_profile, old_source, new_profile, new_source)
        # F9 (ARCHITECTURE.md): presence's one proactive announcement,
        # gated the same as every other proactive speaker even though this
        # one carries no model output at all — "hold" silences the lab's
        # narration, not just its inference.
        try:
            from arail import agent_context
            if not agent_context.speech_gate("presence"):
                return
        except Exception:  # noqa: BLE001
            pass
        try:
            activity_log.emit("profile", message, "info", data=snap)
        except Exception as e:  # noqa: BLE001
            log.warning("presence: emit failed: %s", e)

    @staticmethod
    def _format_message(old_profile: str, old_source: str,
                        new_profile: str, new_source: str) -> str:
        # Compact, human-friendly transition descriptions.
        # Examples:
        #   "Profile → throughput (window) — heavy hours, batches running"
        #   "Profile → interactive (presence) — operator active"
        tag = {
            ("interactive", "presence"): "operator active",
            ("interactive", "override"): "manual pin",
            ("balanced", "default"): "auto-resolved",
            ("throughput", "window"): "heavy hours, batches running",
            ("throughput", "override"): "manual throughput pin",
        }.get((new_profile, new_source), new_source)
        return f"Profile → {new_profile} ({new_source}) — {tag}"

    def _loop(self) -> None:
        self.status = "running"
        # Don't fire immediately — wait one interval so resolver can warm.
        if self._stop.wait(self._interval()):
            self.status = "idle"
            return
        while not self._stop.is_set():
            self._tick()
            if self._stop.wait(self._interval()):
                break
        self.status = "idle"

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="presence-agent", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)


# Singleton — the loader picks this up via the agent contract.
presence = PresenceAgent()
