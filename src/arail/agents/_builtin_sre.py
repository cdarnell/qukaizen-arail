"""SRE Watch — reliability agent for Arail.

    An agent is a loop that notices things and speaks up.

This one watches for crashes and errors. It reads activity.jsonl on
every tick, fingerprints recent error events, and emits alerts into
the activity feed when:

    1. A new error pattern appears that hasn't been seen before.
    2. The same pattern recurs 3+ times in a 30-minute window.
    3. The portal's /api/jobs/state endpoint stops responding.

The mental model:

    1. Personality  — who SRE is (NAME, EMOJI, SYSTEM_PROMPT)
    2. Watchers     — functions that scan for problems (WATCHERS)
    3. Speech       — raw fact preferred; precision > personality
    4. Loop         — the heartbeat (SREAgent._run, _maybe_speak)
    5. Memory       — fingerprints + cooldowns (state.json)

Design rules:

    - Technical strings stay technical. No LLM paraphrasing unless
      the fact is ambiguous. Error messages must be reproducible.
    - Per-fingerprint cooldowns so one stuck loop doesn't flood the feed.
    - 3-minute global cooldown — enough quiet time between cascades.
    - Never hard-fail on log read errors; return None from watchers.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from arail.activity import activity_log
from arail.agent_workflows import update_agent_workflow


# ── Where memory lives ───────────────────────────────────────────────
def _state_file() -> Path:
    from arail.pkb import _pkb_root
    return _pkb_root() / "agents" / "sre" / "state.json"


def _activity_log_path() -> Path:
    from arail.pkb import _pkb_root
    root = _pkb_root()
    # activity.jsonl lives in lab/data/, two levels up from pkb root
    return root.parent.parent / "data" / "activity.jsonl"


# ══════════════════════════════════════════════════════════════════════
#  1. PERSONALITY — who SRE is
# ══════════════════════════════════════════════════════════════════════

NAME = "SRE"
EMOJI = "🔥"

# SRE prefers raw technical facts over LLM paraphrasing, so this
# system prompt is a fallback — only used if the fact itself is empty.
SYSTEM_PROMPT = (
    "You are an SRE bot. You report incidents in one clinical sentence. "
    "State the error type, source, and occurrence count. No emojis. "
    "No softening language. Under 25 words."
)


# ══════════════════════════════════════════════════════════════════════
#  2. OBSERVATIONS — what SRE notices
# ══════════════════════════════════════════════════════════════════════

@dataclass
class Observation:
    watcher: str
    severity: str           # "warn" | "error" (mapped to "warn" in emit)
    fact: str
    cooldown_key: str       # fingerprint or watcher name for per-key cooldown
    cooldown_sec: int = 10 * 60

    def rank(self) -> int:
        return {"error": 3, "warn": 2, "info": 1}.get(self.severity, 0)


# ══════════════════════════════════════════════════════════════════════
#  3. HELPERS — log reading & fingerprinting
# ══════════════════════════════════════════════════════════════════════

def _fingerprint(source: str, message: str) -> str:
    """Stable short ID for a (source, message) pair.

    Uses the first 40 chars of the message so similar messages from
    the same source collapse into one fingerprint.
    """
    return f"{source}::{message[:40]}"


def _tail_jsonl(path: Path, n: int) -> List[dict]:
    """Read the last n lines of a JSONL file. Fast for large files."""
    if not path.exists():
        return []
    try:
        with path.open("rb") as f:
            # Seek to end, walk backwards collecting newlines
            f.seek(0, 2)
            size = f.tell()
            buf = bytearray()
            lines_found = 0
            pos = size
            chunk = 4096
            while pos > 0 and lines_found <= n:
                read_size = min(chunk, pos)
                pos -= read_size
                f.seek(pos)
                buf = bytearray(f.read(read_size)) + buf
                lines_found = buf.count(b"\n")
            raw_lines = buf.decode("utf-8", errors="replace").splitlines()
            tail = raw_lines[-n:] if len(raw_lines) >= n else raw_lines
        events = []
        for line in tail:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return events
    except OSError:
        return []


def _parse_ts(ts_str: str) -> float:
    """Parse an ISO 8601 timestamp string to a Unix float. Returns 0 on error."""
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0


# ══════════════════════════════════════════════════════════════════════
#  4. WATCHERS — where noticing happens
# ══════════════════════════════════════════════════════════════════════

def _watch_recent_errors() -> Optional[Observation]:
    """Fires when a new error/warn pattern appears in the last 5 minutes.

    Reads the last 200 lines of activity.jsonl. For each event with
    level 'error' or 'warn', computes a fingerprint. Returns an
    Observation for the first fingerprint that's genuinely new (not
    in seen_fingerprints state). Per-fingerprint cooldown: 10 min.
    """
    log_path = _activity_log_path()
    events = _tail_jsonl(log_path, 200)
    if not events:
        return None

    now = time.time()
    cutoff = now - 5 * 60  # 5 minutes ago

    # Load seen fingerprints from state (we'll update state after emit)
    seen: Dict[str, float] = {}
    state_path = _state_file()
    if state_path.exists():
        try:
            data = json.loads(state_path.read_text())
            seen = dict(data.get("seen_fingerprints") or {})
        except Exception:
            pass

    for ev in reversed(events):
        if ev.get("level") not in ("error", "warn"):
            continue
        ts = _parse_ts(ev.get("ts", ""))
        if ts < cutoff:
            break  # events are chronological; once we're past 5min, stop
        source = ev.get("source", "?")
        message = ev.get("message", "")
        fp = _fingerprint(source, message)
        first_seen = seen.get(fp, 0.0)
        if now - first_seen < 10 * 60:
            continue  # already surfaced recently
        # New fingerprint — report it
        level_word = "[ERROR]" if ev.get("level") == "error" else "[WARN]"
        fact = f"{level_word} {source}: {message[:120]}"
        return Observation(
            watcher="recent-errors",
            severity="warn",
            fact=fact,
            cooldown_key=fp,
            cooldown_sec=10 * 60,
        )
    return None


def _watch_crash_recurrence() -> Optional[Observation]:
    """Fires when the same error pattern recurs 3+ times in 30 minutes.

    Reads the last 500 lines. Counts fingerprint occurrences in a
    30-minute window. If any fingerprint hits ≥3 occurrences, returns
    a warn observation. Per-fingerprint cooldown: 15 min.
    """
    log_path = _activity_log_path()
    events = _tail_jsonl(log_path, 500)
    if not events:
        return None

    now = time.time()
    window_start = now - 30 * 60

    # Load last-said for recurrence watcher
    last_said: Dict[str, float] = {}
    state_path = _state_file()
    if state_path.exists():
        try:
            data = json.loads(state_path.read_text())
            last_said = dict(data.get("last_said") or {})
        except Exception:
            pass

    counts: Dict[str, int] = {}
    fp_examples: Dict[str, str] = {}
    for ev in events:
        if ev.get("level") not in ("error", "warn"):
            continue
        ts = _parse_ts(ev.get("ts", ""))
        if ts < window_start:
            continue
        source = ev.get("source", "?")
        message = ev.get("message", "")
        fp = _fingerprint(source, message)
        counts[fp] = counts.get(fp, 0) + 1
        if fp not in fp_examples:
            fp_examples[fp] = f"{source}: {message[:80]}"

    for fp, count in counts.items():
        if count < 3:
            continue
        cooldown_key = f"recurrence::{fp}"
        last = last_said.get(cooldown_key, 0.0)
        if now - last < 15 * 60:
            continue
        fact = f"[RECURRENCE] {fp_examples[fp]!r} fired {count}x in 30 min."
        return Observation(
            watcher="crash-recurrence",
            severity="warn",
            fact=fact,
            cooldown_key=cooldown_key,
            cooldown_sec=15 * 60,
        )
    return None


def _watch_service_health() -> Optional[Observation]:
    """Fires when the portal's liveness endpoint is unreachable or unhealthy.

    Probes ``/api/system/health``, which portal auth keeps anonymous (a liveness check that needed
    a credential would report "down" whenever the token file was unreadable or rotated). A quick
    GET with a 2-second timeout. A 4xx/5xx means the portal is up but unhealthy; only a connection
    failure means unreachable. Cooldown: 10 min (keyed to 'service-health').
    """
    try:
        import urllib.error
        import urllib.request
        port = int(os.getenv("PORTAL_PORT", "8080"))
        url = f"http://127.0.0.1:{port}/api/system/health"
        req = urllib.request.urlopen(url, timeout=2)
        req.read()
        return None  # healthy
    except urllib.error.HTTPError as exc:
        return Observation(
            watcher="service-health",
            severity="warn",
            fact=f"Portal is up but unhealthy: /api/system/health answered HTTP {exc.code}.",
            cooldown_key="service-health",
            cooldown_sec=10 * 60,
        )
    except OSError:
        return Observation(
            watcher="service-health",
            severity="warn",
            fact="Portal /api/system/health is unreachable — portal may be down.",
            cooldown_key="service-health",
            cooldown_sec=10 * 60,
        )
    except Exception:
        return None  # unexpected error — don't spam


def _sre_lab_mode() -> str:
    """Return the current lab mode via the canonical airgap helper.

    Delegates to arail.airgap.lab_mode() — the single source of truth for
    the LAB_MODE → ARAIL_MODE → 'airgapped' fallback chain.
    """
    from arail.airgap import lab_mode
    return lab_mode()


def _sre_data_dir() -> Path:
    """Resolve DATA_DIR the same way arail.config does, without importing it."""
    from arail.config import DATA_DIR
    return Path(DATA_DIR)


def _watch_dependency_vulnerabilities() -> Optional[Observation]:
    """Watch for CVE findings in last_scan.json.

    Three branches (verbatim from ARCHITECTURE.md interface contracts):
    (a) High/Critical present → severity=error, cooldown_key includes last_run_ts.
    (b) Medium-only → severity=warn, cooldown_key includes last_run_ts.
    (c) No scan in 24h+ AND hybrid mode → warn user to run a scan.
    In airgapped mode, branch (c) never fires (no scan expected).
    Returns None if file missing in airgapped mode or scan is fresh.
    """
    try:
        scan_path = _sre_data_dir() / "security" / "last_scan.json"
    except Exception:
        return None

    # Read and parse the scan file
    scan_data: dict = {}
    file_missing = False
    try:
        scan_data = json.loads(scan_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        file_missing = True
    except (OSError, json.JSONDecodeError):
        return None  # Unreadable file — don't spam

    if file_missing:
        # Branch (c): no scan ever ran
        if _sre_lab_mode() == "hybrid":
            return Observation(
                watcher="dependency-vulnerabilities",
                severity="warn",
                fact="[CVE] No security scan in 24h+. Run a scan in Admin → Production Readiness → Security.",
                cooldown_key=f"cve::nag::{date.today().isoformat()}",
                cooldown_sec=24 * 3600,
            )
        return None

    # Parse last_run_ts for age check (E2 mitigation: wrap in try/except)
    last_run_ts = scan_data.get("last_run_ts")
    last_run_age_h: float = float("inf")
    try:
        if last_run_ts:
            last_run_dt = datetime.fromisoformat(last_run_ts.replace("Z", "+00:00"))
            last_run_age_h = (datetime.now(timezone.utc) - last_run_dt).total_seconds() / 3600.0
    except (ValueError, TypeError):
        pass  # E2: treat as unknown age

    summary = scan_data.get("summary") or {}
    n_crit = int(summary.get("critical", 0))
    n_high = int(summary.get("high", 0))
    n_med = int(summary.get("medium", 0))

    # Branch (a): High/Critical present
    if n_crit + n_high > 0:
        return Observation(
            watcher="dependency-vulnerabilities",
            severity="error",
            fact=f"[CVE] {n_crit + n_high} High/Critical vulnerabilities in pip dependencies (Admin → Production Readiness → Security).",
            cooldown_key=f"cve::{last_run_ts}::{n_crit}::{n_high}",
            cooldown_sec=6 * 3600,
        )

    # Branch (b): Medium-only
    if n_med > 0:
        return Observation(
            watcher="dependency-vulnerabilities",
            severity="warn",
            fact=f"[CVE] {n_med} Medium vulnerabilities in pip dependencies (review in Admin).",
            cooldown_key=f"cve::med::{last_run_ts}::{n_med}",
            cooldown_sec=12 * 3600,
        )

    # Branch (c): No findings but scan is stale (>24h) in hybrid mode
    if _sre_lab_mode() == "hybrid" and last_run_age_h > 24:
        return Observation(
            watcher="dependency-vulnerabilities",
            severity="warn",
            fact="[CVE] No security scan in 24h+. Run a scan in Admin → Production Readiness → Security.",
            cooldown_key=f"cve::nag::{date.today().isoformat()}",
            cooldown_sec=24 * 3600,
        )

    return None


def _watch_lab_cleanup() -> Optional[Observation]:
    """Watch for oversized wiki cache.

    Thresholds are env-configurable:
      LAB_CLEANUP_CACHE_MAX_GB — default 5
      LAB_CLEANUP_LOG_AGE_DAYS — default 30 (reserved for future file-age logic)

    Severity:
      cache_gb > threshold_gb        → warn
      cache_gb > 2 * threshold_gb    → error

    Uses rglob-based walk; caps at 10,000 entries (E6).
    Result is per-call (no module-level caching) — the SRE loop runs
    every 2 minutes so a per-call scandir walk is cheap enough.
    """
    try:
        from arail.config import LAB_ROOT
        cache_root = Path(LAB_ROOT) / "pkb" / ".wiki-cache"
    except Exception:
        return None

    if not cache_root.exists():
        return None

    try:
        threshold_gb = float(os.getenv("LAB_CLEANUP_CACHE_MAX_GB", "5"))
    except (ValueError, TypeError):
        threshold_gb = 5.0

    total_bytes = 0
    count = 0
    limit = 10_000  # E6 mitigation
    try:
        for entry in cache_root.rglob("*"):
            if count >= limit:
                break
            if entry.is_file() and not entry.is_symlink():
                try:
                    total_bytes += entry.stat().st_size
                except OSError:
                    pass
            count += 1
    except OSError:
        return None

    cache_gb = total_bytes / (2 ** 30)
    if cache_gb <= threshold_gb:
        return None

    # Bucket the size to 0.5 GB increments for stable cooldown keys
    age_bucket = round(cache_gb * 2) / 2
    severity = "error" if cache_gb > 2 * threshold_gb else "warn"
    return Observation(
        watcher="lab-cleanup",
        severity=severity,
        fact=f"[CLEANUP] Wiki cache is {cache_gb:.1f} GB (threshold {threshold_gb} GB). Prune in Admin → Production Readiness → Cleanup.",
        cooldown_key=f"cleanup::cache::{round(cache_gb)}::{age_bucket}",
        cooldown_sec=24 * 3600,
    )


WATCHERS: List[Callable[[], Optional[Observation]]] = [
    _watch_recent_errors,
    _watch_crash_recurrence,
    _watch_service_health,
    _watch_dependency_vulnerabilities,
    _watch_lab_cleanup,
]


# ══════════════════════════════════════════════════════════════════════
#  5. LOOP — the heartbeat
# ══════════════════════════════════════════════════════════════════════

class SREAgent:
    """Background task that ticks every LAB_SRE_INTERVAL_SEC (default 120s)
    and surfaces crashes, error recurrences, and service downtime in the
    activity feed."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._status = "idle"
        self._last_said: Dict[str, float] = {}
        self._last_global: float = 0.0
        self._seen_fingerprints: Dict[str, float] = {}
        self._recent_actions: List[str] = []

    @property
    def status(self) -> str:
        return self._status

    # ── Memory ─────────────────────────────────────────────────────

    def _load_state(self) -> None:
        path = _state_file()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            self._last_said = dict(data.get("last_said") or {})
            self._last_global = float(data.get("last_global") or 0.0)
            self._seen_fingerprints = dict(data.get("seen_fingerprints") or {})
        except Exception:
            pass

    def _save_state(self) -> None:
        path = _state_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "last_said": self._last_said,
                "last_global": self._last_global,
                "seen_fingerprints": self._seen_fingerprints,
            }, indent=2))
        except OSError:
            pass

    # ── Lifecycle ──────────────────────────────────────────────────

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._load_state()
        self._status = "running"
        self._sync_workflow("Scanning for incidents", "Wait for an incident pattern")
        self._task = asyncio.create_task(self._run())
        activity_log.emit(
            "sre",
            f"{EMOJI} {NAME} Watch is online — monitoring for crashes and errors.",
            "info",
        )

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._status = "idle"
        self._sync_workflow("Offline", None)

    def _sync_workflow(self, current_task: str, next_step: str | None) -> None:
        global_cooldown = max(60, int(os.getenv("LAB_SRE_COOLDOWN_SEC", "180")))
        too_chatty = len(self._recent_actions[-5:]) >= 4 and global_cooldown < 180
        update_agent_workflow(
            "sre",
            status=self._status,
            objective="Detect failures, recurrences, and service interruptions early",
            current_task=current_task,
            next_step=next_step,
            completed_steps=list(self._recent_actions[-5:]),
            paused=False,
            pause_reason=None,
            chatter={
                "alerts_seen": len(self._recent_actions),
                "global_cooldown_sec": global_cooldown,
                "too_chatty": too_chatty,
            },
            recent_actions=list(self._recent_actions[-3:]),
        )

    async def _run(self) -> None:
        interval = max(30, int(os.getenv("LAB_SRE_INTERVAL_SEC", "120")))
        global_cooldown = max(
            60, int(os.getenv("LAB_SRE_COOLDOWN_SEC", "180"))
        )
        try:
            while True:
                await asyncio.sleep(interval)
                # asyncio.to_thread, not a direct call: _maybe_speak() runs
                # every watcher synchronously, and _watch_service_health()
                # makes a BLOCKING urllib call to the portal's own
                # /api/jobs/state — i.e. this coroutine, if it called
                # _maybe_speak directly, would block the single asyncio
                # event loop that ALSO has to answer that very HTTP
                # request. That's a guaranteed self-deadlock: the request
                # can never complete until this coroutine yields, and this
                # coroutine can never finish until the request completes.
                # It reproduces 100% of the time, not intermittently —
                # confirmed live: a fresh instance fired "Portal
                # /api/jobs/state is unreachable — portal may be down" on
                # its very first health-check tick, while an external
                # process hitting the identical URL at the identical
                # moment got a clean 200. Dispatching the whole watcher
                # pass to a worker thread frees the loop to answer its own
                # request. Safe to do for the ENTIRE call, not just the
                # network probe: _maybe_speak only touches plain dict/file
                # state on `self` and calls activity_log.emit()/
                # update_agent_workflow(), both plain sync functions with
                # no event-loop affinity (emit() is explicitly documented
                # "Called from sync or async code" and already handles the
                # no-running-loop case via call_soon_threadsafe). Calls are
                # strictly sequential (this loop always awaits the
                # previous tick before starting the next), so there is
                # never more than one thread touching that state at once.
                await asyncio.to_thread(self._maybe_speak, global_cooldown)
        except asyncio.CancelledError:
            return

    def _maybe_speak(self, global_cooldown: int) -> None:
        """Run every watcher, pick the highest-ranked observation, emit once."""
        now = time.time()
        if now - self._last_global < global_cooldown:
            return

        candidates: List[Observation] = []
        for watcher in WATCHERS:
            try:
                obs = watcher()
            except Exception:
                continue
            if obs is None:
                continue
            # Per-key cooldown check (fingerprint or watcher name)
            last = self._last_said.get(obs.cooldown_key, 0.0)
            if now - last < obs.cooldown_sec:
                continue
            candidates.append(obs)

        if not candidates:
            return

        candidates.sort(key=lambda o: o.rank(), reverse=True)
        chosen = candidates[0]

        # SRE prefers raw facts — skip LLM unless fact is somehow empty.
        sentence = chosen.fact or f"{EMOJI} Unspecified incident from {chosen.watcher}."

        activity_log.emit(
            "sre",
            f"{EMOJI} {NAME}: {sentence}",
            "warn",
            data={
                "watcher": chosen.watcher,
                "severity": chosen.severity,
                "fact": chosen.fact,
            },
        )

        # Mark this fingerprint as seen so recent-errors doesn't repeat it.
        if chosen.watcher == "recent-errors":
            # Extract the fingerprint from the cooldown_key
            self._seen_fingerprints[chosen.cooldown_key] = now

        self._last_said[chosen.cooldown_key] = now
        self._last_global = now
        self._recent_actions.append(f"Alerted on {chosen.watcher}: {chosen.fact[:80]}")
        self._save_state()
        self._sync_workflow(f"Alerted on {chosen.watcher}", "Wait for the next incident")


# ── Singleton export ─────────────────────────────────────────────────
# The loader discovers this via the module attribute named after the
# agent folder (agent_id == "sre", so it looks for `sre.sre`).
sre = SREAgent()
