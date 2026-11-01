"""GoalStore — persists the user's current goal and history."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


from arail.config import DATA_DIR

GOALS_DIR = DATA_DIR / "goals"
CURRENT_FILE = GOALS_DIR / "current.json"
HISTORY_DIR = GOALS_DIR / "history"
PREVIEW_FILE = GOALS_DIR / "preview.json"


# ── Researcher run-state (crash/restart recovery) ────────────────────
# Written by ResearcherAgent._sync_workflow on every transition; read by
# the boot reconciliation hook so an interrupted run can auto-resume from
# its last checkpoint instead of stranding progress. A function (not a
# constant) so tests can monkeypatch the path.

def run_state_path() -> Path:
    return GOALS_DIR / "run_state.json"


def save_run_state(state: Dict[str, Any]) -> None:
    try:
        path = run_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2, default=str))
    except OSError:
        pass  # best-effort; resume is a convenience, never a crash source


def load_run_state() -> Optional[Dict[str, Any]]:
    path = run_state_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def clear_run_state() -> None:
    try:
        run_state_path().unlink(missing_ok=True)
    except OSError:
        pass


class GoalStore:
    """Manages the active goal and archives old ones."""

    def __init__(self) -> None:
        GOALS_DIR.mkdir(parents=True, exist_ok=True)
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    def set_goal(
        self,
        parsed_goal: Dict[str, Any],
        *,
        swarm_plan: Dict[str, Any] | None = None,
        source: str = "direct",
        preview_id: str | None = None,
    ) -> Dict[str, Any]:
        """Set a new active goal.  Archives the previous one."""
        # Archive current if exists
        current = self.get_current()
        if current:
            self._archive(current)

        parsed_copy = dict(parsed_goal)
        if swarm_plan:
            parsed_copy["swarm_plan"] = swarm_plan

        goal_record: Dict[str, Any] = {
            "id": uuid.uuid4().hex[:8],
            "goal_text": parsed_copy.get("goal", ""),
            "parsed": parsed_copy,
            "swarm": swarm_plan,
            "goal_mode": "swarm" if swarm_plan else "direct",
            "source": source,
            "source_preview_id": preview_id,
            "created_at": _now(),
            "status": "active",
            "experiments": [],       # linked experiment IDs
            "findings": [],          # research findings
            "report": None,          # final researcher report
            "progress": 0.0,
            "world": _mounted_world(),  # None when no World is mounted
        }
        self._save_current(goal_record)
        _notify_listeners("goal_set", {
            "record": goal_record,
            "archive_id": (current or {}).get("id"),
        })
        return goal_record

    def save_preview(
        self,
        goal_text: str,
        parsed_goal: Dict[str, Any],
        swarm_plan: Dict[str, Any],
    ) -> Dict[str, Any]:
        preview = {
            "id": uuid.uuid4().hex[:8],
            "goal_text": goal_text,
            "parsed": {**parsed_goal, "swarm_plan": swarm_plan},
            "swarm": swarm_plan,
            "created_at": _now(),
            "updated_at": _now(),
            "status": "preview",
            "goal_mode": "swarm-preview",
            "current_goal_id": (self.get_current() or {}).get("id"),
        }
        self._save_preview(preview)
        return preview

    def get_preview(self) -> Optional[Dict[str, Any]]:
        if not PREVIEW_FILE.exists():
            return None
        try:
            data = json.loads(PREVIEW_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        return data if isinstance(data, dict) else None

    def update_preview(self, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        preview = self.get_preview()
        if not preview:
            return None
        preview.update(updates)
        preview["updated_at"] = _now()
        self._save_preview(preview)
        return preview

    def confirm_preview(self) -> Optional[Dict[str, Any]]:
        preview = self.get_preview()
        if not preview:
            return None
        parsed = dict(preview.get("parsed") or {})
        # The preview stores goal_text at the top level; parsed may not carry
        # the "goal" key. Without this, set_goal records goal_text="" and the
        # cockpit keeps showing the previously-archived goal.
        preview_goal_text = str(preview.get("goal_text") or "").strip()
        if preview_goal_text and not str(parsed.get("goal") or "").strip():
            parsed["goal"] = preview_goal_text
        record = self.set_goal(
            parsed,
            swarm_plan=preview.get("swarm") if isinstance(preview.get("swarm"), dict) else None,
            source="preview",
            preview_id=str(preview.get("id") or ""),
        )
        record["confirmed_at"] = _now()
        self._save_current(record)
        self.clear_preview()
        return record

    def clear_preview(self) -> None:
        if PREVIEW_FILE.exists():
            PREVIEW_FILE.unlink()

    def get_current(self) -> Optional[Dict[str, Any]]:
        if not CURRENT_FILE.exists():
            return None
        try:
            return json.loads(CURRENT_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def update_current(self, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        current = self.get_current()
        if not current:
            return None
        current.update(updates)
        current["updated_at"] = _now()
        self._save_current(current)
        return current

    def link_experiment(self, exp_id: str) -> None:
        current = self.get_current()
        if current and exp_id not in current["experiments"]:
            current["experiments"].append(exp_id)
            current["updated_at"] = _now()
            self._save_current(current)

    def add_finding(self, finding: Dict[str, Any]) -> None:
        current = self.get_current()
        if current:
            current["findings"].append({
                **finding,
                "found_at": _now(),
            })
            current["updated_at"] = _now()
            self._save_current(current)

    def set_report(self, report: str) -> None:
        current = self.get_current()
        if current:
            current["report"] = report
            current["updated_at"] = _now()
            self._save_current(current)

    def update_progress(self, progress: float) -> None:
        current = self.get_current()
        if current:
            current["progress"] = round(min(1.0, max(0.0, progress)), 2)
            current["updated_at"] = _now()
            self._save_current(current)

    def archive_if_world_mismatch(self, new_world: Optional[str]) -> bool:
        """Called by world_mount on mount/swap/unmount: if the CURRENT goal
        belongs to a different World than the one now mounted, archive it
        (the ordinary, already-battle-tested path — same as clear_current)
        rather than leave it silently "running" under the wrong World.

        This is what closes the gap _sweep_other_worlds() deliberately
        doesn't: switching the World a lab reflects must also switch which
        goal that lab is actively researching, on the same "the lab
        reflects the mounted World" rule the Compiled-KB gate already
        follows. Observed live before this existed: a goal set while
        World A was mounted kept running (and its experiments kept
        rendering as "yours") after switching to World B.

        A goal predating this field (``world`` key absent — legacy record)
        is treated as belonging to no World (None), same as a goal set
        with nothing mounted — so it archives on switching TO a World, and
        is left alone on unmount (None == None). Fail-safe direction:
        losing an already-superseded reference to a wrong-World goal is
        far cheaper than silently continuing to research under one.

        Does NOT restore a prior goal for the newly-mounted World from
        history — switching back to a World starts fresh, deliberately
        (see docs/concurrent-worlds.md's per-World-instance precedent:
        state either lives with the instance or it doesn't; this avoids a
        second, subtler kind of surprise — "why is an old goal suddenly
        running again" — in exchange for the simpler, always-legible one:
        a World switch archives, never resurrects).

        Returns True iff a goal was archived.
        """
        current = self.get_current()
        if not current:
            return False
        if current.get("world") == new_world:
            return False
        self._archive(current)
        _notify_listeners("goal_cleared", {
            "goal_id": current.get("id"),
            "reason": "world_switched",
            "from_world": current.get("world"),
            "to_world": new_world,
        })
        if CURRENT_FILE.exists():
            CURRENT_FILE.unlink()
        clear_run_state()
        return True

    def clear_current(self) -> None:
        """Archive and remove the current goal."""
        current = self.get_current()
        if current:
            self._archive(current)
            _notify_listeners("goal_cleared", {"goal_id": current.get("id")})
        if CURRENT_FILE.exists():
            CURRENT_FILE.unlink()
        clear_run_state()   # a cleared goal has no run to resume

    def list_history(self) -> List[Dict[str, Any]]:
        history = []
        for f in sorted(HISTORY_DIR.glob("*.json"), reverse=True):
            try:
                history.append(json.loads(f.read_text()))
            except (json.JSONDecodeError, OSError):
                continue
        return history

    # -- internal --

    def _save_current(self, record: Dict[str, Any]) -> None:
        CURRENT_FILE.write_text(json.dumps(record, indent=2, default=str))

    def _save_preview(self, record: Dict[str, Any]) -> None:
        PREVIEW_FILE.write_text(json.dumps(record, indent=2, default=str))

    def _archive(self, record: Dict[str, Any]) -> None:
        record["status"] = "archived"
        record["archived_at"] = _now()
        path = HISTORY_DIR / f"{record['id']}.json"
        path.write_text(json.dumps(record, indent=2, default=str))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mounted_world() -> Optional[str]:
    """The slug of the currently mounted World, or None. Lazy-imported to
    avoid a module-load-order coupling with world_mount (neither module
    currently imports the other at top level; keep it that way). Never
    raises — a goal must always be settable even if the mount sidecar is
    unreadable."""
    try:
        from arail import world_mount
        record = world_mount.current_mount()
        return record.world if record is not None else None
    except Exception:
        return None


# Lightweight goal-event bus. Listeners are registered by other modules
# (e.g. the portal wires the Knowledge Canvas sync as a listener). Kept
# in-process and synchronous: callers must not block. Failures in any
# listener are swallowed so the goal store stays the source of truth.
_LISTENERS: list[Any] = []


def add_listener(fn: Any) -> None:
    """Register fn(event_name: str, payload: dict). Idempotent on identity."""
    if fn not in _LISTENERS:
        _LISTENERS.append(fn)


def remove_listener(fn: Any) -> None:
    if fn in _LISTENERS:
        _LISTENERS.remove(fn)


def _notify_listeners(event: str, payload: Dict[str, Any]) -> None:
    for fn in list(_LISTENERS):
        try:
            fn(event, payload)
        except Exception:
            # Listeners must never break goal-store mutations.
            pass
