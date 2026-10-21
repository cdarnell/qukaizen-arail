"""Arail scheduler — time windows + a global halt flag.

Two concepts:

1. **Window** — active / heavy / idle, derived from `LAB_ACTIVE_HOURS`
   and `LAB_HEAVY_HOURS` in the environment. Default: active 08:00-22:00,
   heavy 22:00-08:00 (so the GPU hammers while you sleep).
2. **Halt flag** — a process-wide boolean that agents poll. Setting it
   via :func:`halt_all_jobs` cancels in-flight work without tearing down
   the portal itself (that's :func:`arailctl stop`).

The scheduler is deliberately simple: no cron, no DAG, no wake-up
callbacks. Agents are responsible for calling :func:`current_window`
and :func:`jobs_halted` at their own tick points.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Literal

Window = Literal["active", "heavy", "idle"]


@dataclass(frozen=True)
class HourRange:
    start: time
    end: time

    def contains(self, now: time) -> bool:
        if self.start <= self.end:
            return self.start <= now < self.end
        # Wraps midnight (e.g. 22:00-08:00)
        return now >= self.start or now < self.end


def _parse_range(raw: str) -> HourRange | None:
    try:
        lo, hi = raw.strip().split("-", 1)
        return HourRange(time.fromisoformat(lo.strip()), time.fromisoformat(hi.strip()))
    except (ValueError, AttributeError):
        return None


def _active_range() -> HourRange:
    return _parse_range(os.getenv("LAB_ACTIVE_HOURS", "")) \
        or HourRange(time(8, 0), time(22, 0))


def _heavy_range() -> HourRange:
    return _parse_range(os.getenv("LAB_HEAVY_HOURS", "")) \
        or HourRange(time(22, 0), time(8, 0))


def current_window(now: datetime | None = None) -> Window:
    """Return the current window. A manual override (until the next
    schedule boundary) wins; otherwise heavy takes precedence over
    active when ranges overlap, and anything outside both is 'idle'."""
    ov = get_window_override(now)
    if ov is not None:
        return ov["window"]
    t = (now or datetime.now()).time()
    if _heavy_range().contains(t):
        return "heavy"
    if _active_range().contains(t):
        return "active"
    return "idle"


def window_label(w: Window | None = None) -> str:
    """Human-friendly label for the dashboard mode indicator."""
    w = w or current_window()
    return {
        "active": "☀ active — light work only",
        "heavy":  "🌙 heavy window — experiments running",
        "idle":   "◦ idle — queued work drains",
    }[w]


def startup_delay_seconds() -> int:
    """How long agents should wait on boot before their first tick."""
    try:
        return max(0, int(os.getenv("LAB_STARTUP_DELAY_SEC", "300")))
    except ValueError:
        return 300


# ── Window override ──────────────────────────────────────────────────
# A manual pin of the window ("active" = light work, "heavy") that lasts
# until the next schedule boundary, then the clock schedule resumes.
# Persisted so a portal restart doesn't drop the operator's choice.

_OVERRIDABLE: tuple[Window, ...] = ("active", "heavy")

_ov_lock = threading.Lock()
_ov: dict | None = None  # {"window": Window, "set_at": iso, "expires_at": iso}
_ov_loaded = False


def _override_path():
    from arail.config import DATA_DIR
    return DATA_DIR / "window_override.json"


def next_boundary(now: datetime | None = None) -> datetime:
    """Next schedule edge strictly after `now`.

    Edges are the start/end times-of-day of the active and heavy ranges;
    each edge's next occurrence is today at that time, rolled forward a
    day if not strictly in the future. Overnight ranges (22:00-08:00)
    need no special casing — their edges are bare times too.
    """
    now = now or datetime.now()
    edges = set()
    for rng in (_active_range(), _heavy_range()):
        edges.add(rng.start)
        edges.add(rng.end)
    candidates = []
    for t in edges:
        cand = now.replace(hour=t.hour, minute=t.minute,
                           second=t.second, microsecond=0)
        if cand <= now:
            cand += timedelta(days=1)
        candidates.append(cand)
    return min(candidates)


def _load_override_locked() -> None:
    global _ov, _ov_loaded
    if _ov_loaded:
        return
    _ov_loaded = True
    path = _override_path()
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return
    if isinstance(data, dict) and data.get("window") in _OVERRIDABLE:
        try:
            datetime.fromisoformat(str(data.get("expires_at")))
        except (TypeError, ValueError):
            return
        _ov = {
            "window": data["window"],
            "set_at": str(data.get("set_at", "")),
            "expires_at": str(data["expires_at"]),
        }


def _persist_override_locked() -> None:
    path = _override_path()
    try:
        if _ov is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(_ov, indent=2))
    except OSError:
        pass  # best-effort persistence; in-memory state still applies


def set_window_override(window: Window, now: datetime | None = None) -> dict:
    """Pin the window until the next schedule boundary. Returns the record."""
    if window not in _OVERRIDABLE:
        raise ValueError(f"window override must be one of {_OVERRIDABLE}, got {window!r}")
    global _ov
    now = now or datetime.now()
    with _ov_lock:
        _load_override_locked()
        _ov = {
            "window": window,
            "set_at": now.isoformat(timespec="seconds"),
            "expires_at": next_boundary(now).isoformat(timespec="seconds"),
        }
        _persist_override_locked()
        return dict(_ov)


def clear_window_override() -> None:
    global _ov
    with _ov_lock:
        _load_override_locked()
        _ov = None
        _persist_override_locked()


def get_window_override(now: datetime | None = None) -> dict | None:
    """Active override record, or None. Expired overrides self-clear."""
    global _ov
    now = now or datetime.now()
    with _ov_lock:
        _load_override_locked()
        if _ov is None:
            return None
        if now >= datetime.fromisoformat(_ov["expires_at"]):
            _ov = None
            _persist_override_locked()
            return None
        return dict(_ov)


def _reset_window_override_for_tests() -> None:
    global _ov, _ov_loaded
    with _ov_lock:
        _ov = None
        _ov_loaded = True
        _override_path().unlink(missing_ok=True)


# ── Global halt flag ─────────────────────────────────────────────────
# Persisted (like the window override) so a portal restart cannot silently
# un-halt the lab: an operator who halted jobs expects them to STAY halted
# until an explicit resume — including across crashes and daemon respawns.

_halt_lock = threading.Lock()
_halted = False
_halt_loaded = False
_halt_changed_at: str | None = None


def _halt_path():
    from arail.config import DATA_DIR
    return DATA_DIR / "halt.json"


def _load_halt_locked() -> None:
    global _halted, _halt_loaded, _halt_changed_at
    if _halt_loaded:
        return
    _halt_loaded = True
    path = _halt_path()
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return
    if isinstance(data, dict):
        _halted = bool(data.get("halted", False))
        _halt_changed_at = data.get("changed_at")


def _persist_halt_locked() -> None:
    global _halt_changed_at
    path = _halt_path()
    try:
        if not _halted:
            _halt_changed_at = None
            path.unlink(missing_ok=True)
        else:
            _halt_changed_at = datetime.now().isoformat(timespec="seconds")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "halted": True,
                "changed_at": _halt_changed_at,
            }, indent=2))
    except OSError:
        pass  # best-effort persistence; in-memory state still applies


def jobs_halted() -> bool:
    with _halt_lock:
        _load_halt_locked()
        return _halted


def halt_changed_at() -> str | None:
    """ISO timestamp of the last halt/resume transition, or None if the
    lab has never been held this DATA_DIR's lifetime. Used by
    ``agent_context.hold_state()`` for the Admin control's copy (F17)."""
    with _halt_lock:
        _load_halt_locked()
        return _halt_changed_at


def halt_all_jobs() -> None:
    """Flip the halt flag. Agents poll this and abort their current tick."""
    global _halted
    with _halt_lock:
        _load_halt_locked()
        _halted = True
        _persist_halt_locked()


def resume_all_jobs() -> None:
    global _halted
    with _halt_lock:
        _load_halt_locked()
        _halted = False
        _persist_halt_locked()


def _reset_halt_for_tests() -> None:
    global _halted, _halt_loaded, _halt_changed_at
    with _halt_lock:
        _halted = False
        _halt_loaded = True
        _halt_changed_at = None
        _halt_path().unlink(missing_ok=True)


def state() -> dict:
    """Serializable snapshot for the portal's /api/jobs/state endpoint."""
    w = current_window()
    out = {
        "window": w,
        "label": window_label(w),
        "halted": jobs_halted(),
        "override": get_window_override(),
        "active_hours": os.getenv("LAB_ACTIVE_HOURS", "08:00-22:00"),
        "heavy_hours": os.getenv("LAB_HEAVY_HOURS", "22:00-08:00"),
        "startup_delay_sec": startup_delay_seconds(),
    }
    # Lazy import to avoid circular: runtime_profile imports current_window.
    try:
        from arail.runtime_profile import snapshot as _profile_snapshot
        out["runtime_profile"] = _profile_snapshot()
    except Exception:
        pass
    return out
