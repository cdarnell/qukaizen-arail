"""Buddy — ARAIL's actively helpful lab partner.

    An agent is a loop that notices things and speaks up.

That's the whole mental model. Buddy is the personality agent that
ships with ARAIL — observant like an old-school pair-programming
buddy AND proactive about the user's current goal: pointing at
techniques to try, items worth reviewing, or research worth running.

Five pieces hang off the loop:

    1. Personality  — who Buddy is (NAME, EMOJI, SYSTEM_PROMPT)
    2. Watchers     — observe lab state (WATCHERS, reactive)
    3. Suggesters   — propose goal-aware actions (SUGGESTERS, proactive)
    4. Speech       — turn a fact into a sentence (_voice, _compose_prompt)
    5. Memory       — cooldowns + dreams (state.json, dream())

The loop in BuddyAgent._run runs two cadences. Every interval tick
(default 90s) it polls WATCHERS — the passive-observation loop.
Every suggestion interval (default 15 min) and
only when a goal is active, it polls SUGGESTERS for goal-anchored
proposals. Reactive observations and goal-aware suggestions share
the same global cooldown so Buddy never double-talks.

New to agents? Start at docs/agents-explained.md, then come back.
Already know? Jump to BuddyAgent._maybe_speak / _maybe_suggest at
the bottom — those two functions are the ones that actually emit;
everything above is the pieces they call.

Design rules Buddy lives by:

    - One sentence at a time. Never paragraphs.
    - Active without being naggy. 5-min global cooldown shared
      across watchers and suggesters; per-target cooldowns layered
      on top so the same skill / experiment / phase nudge isn't
      reissued every tick. Silence is a valid choice.
    - Voice-shaped, not scripted. Every utterance goes through the
      local model with the personality prompt. Change the prompt,
      Buddy sounds different. That's the whole extension point for
      forging new personality agents.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from typing import Protocol, runtime_checkable
except ImportError:  # Python < 3.8 fallback
    from typing_extensions import Protocol, runtime_checkable  # type: ignore


# ══════════════════════════════════════════════════════════════════════
#  0. HOST — the only seam between Buddy and its environment
# ══════════════════════════════════════════════════════════════════════
# BuddyHost is everything Buddy needs from the outside world.
# Implement it to run Buddy in any framework — ARAIL, LangChain,
# stdio, or a unit-test mock. ArailHost is the default implementation
# that wires the live ARAIL stack. Zero behavior change.

@runtime_checkable
class BuddyHost(Protocol):
    """Implement this to host Buddy outside ARAIL."""

    def emit(self, source: str, message: str,
             level: str = "info",
             data: Optional[Dict[str, Any]] = None) -> None: ...

    def update_workflow(self, agent_id: str, **fields: Any) -> None: ...

    def get_current_goal(self) -> Optional[Dict[str, Any]]: ...

    def get_activity_log_path(self) -> Optional[Path]: ...

    def get_pkb_root(self) -> Optional[Path]: ...

    def list_experiments(self) -> List[Dict[str, Any]]: ...

    def list_skills(self) -> List[Any]: ...

    def load_agent_skills(self, agent_id: str) -> List[Any]: ...

    def compose_skill_context(self, skills: List[Any]) -> str: ...

    def load_world_skill(self) -> Optional[Any]: ...

    def llm_complete(self, prompt: str, max_tokens: int = 60,
                     temperature: float = 0.6) -> str: ...


class ArailHost:
    """BuddyHost backed by the live ARAIL stack.

    This is the default when no host is supplied. Every arail.*
    import in Buddy goes through here — so removing this class and
    writing a new one is all it takes to run Buddy elsewhere.
    """

    def emit(self, source: str, message: str,
             level: str = "info",
             data: Optional[Dict[str, Any]] = None) -> None:
        from arail.activity import activity_log
        activity_log.emit(source, message, level, data)

    def update_workflow(self, agent_id: str, **fields: Any) -> None:
        try:
            from arail.agent_workflows import update_agent_workflow
            update_agent_workflow(agent_id, **fields)
        except Exception:
            pass

    def get_current_goal(self) -> Optional[Dict[str, Any]]:
        try:
            from arail.goals import GoalStore
            return GoalStore().get_current()
        except Exception:
            return None

    def get_activity_log_path(self) -> Optional[Path]:
        try:
            from arail.activity import LOG_FILE
            return LOG_FILE
        except Exception:
            return None

    def get_pkb_root(self) -> Optional[Path]:
        try:
            from arail.pkb import _pkb_root
            return _pkb_root()
        except Exception:
            return None

    def list_experiments(self) -> List[Dict[str, Any]]:
        try:
            from arail.skills.experiment_tracker import ExperimentTracker
            return ExperimentTracker().list_all()
        except Exception:
            return []

    def list_skills(self) -> List[Any]:
        try:
            from arail.skills_loader import list_installed_skills
            return list_installed_skills()
        except Exception:
            return []

    def load_agent_skills(self, agent_id: str) -> List[Any]:
        try:
            from arail.skills_loader import load_agent_skills
            return load_agent_skills(agent_id)
        except Exception:
            return []

    def compose_skill_context(self, skills: List[Any]) -> str:
        try:
            from arail.skills_loader import compose_system_context
            return compose_system_context(skills)
        except Exception:
            return ""

    def load_world_skill(self) -> Optional[Any]:
        try:
            from arail.skills_loader import load_world_skill
            return load_world_skill()
        except Exception:
            return None

    def llm_complete(self, prompt: str, max_tokens: int = 60,
                     temperature: float = 0.6) -> str:
        try:
            from arail.agents import deep_policy
            # Buddy speaks proactively (no user is blocking on it), so deep use
            # is background-throttled: on maximus, when non-intrusive, this runs
            # on the aeroLLM 2nd inference for higher-quality voice; otherwise
            # (or on any failure / OOM) it falls back to the fast on-GPU model.
            text = deep_policy.complete_preferring_deep(
                prompt, foreground=False,
                max_tokens=max_tokens, temperature=temperature,
            )
            return text or ""
        except Exception:
            return ""


# Module-level host used by watchers and suggesters (module-level
# functions that can't easily receive it as a parameter). BuddyAgent
# sets this on init when a custom host is supplied.
_host: BuddyHost = ArailHost()


# ── Where memory lives ───────────────────────────────────────────────
# state.json holds cooldowns + counts across restarts. Same path
# whether we're the PKB copy or the builtin fallback — so "wipe the
# PKB" genuinely wipes Buddy's memory.

def _state_file() -> Path:
    pkb = _host.get_pkb_root()
    if pkb is None:
        return Path.home() / ".buddy" / "state.json"
    return pkb / "agents" / "buddy" / "state.json"


# ══════════════════════════════════════════════════════════════════════
#  1. PERSONALITY — who Buddy is
# ══════════════════════════════════════════════════════════════════════
# Three strings. Change them, Buddy changes. The Agent Forge generates
# this block from a form field.

NAME = "Buddy"
EMOJI = "🐧"

# The voice. Buddy needs space for action verbs
# ("Worth a look:", "Try the X skill:", "Run a sweep…").
# ~50 words is the sweet spot for local models given the active
# framing.
SYSTEM_PROMPT = (
    "You are Buddy, an obsessed best-friend study partner. "
    "You live and breathe the user's learning goal — it's the only thing "
    "you think about. You speak in one short sentence, like a friend "
    "leaning over in the library: urgent, warm, a little paranoid about "
    "falling behind. You point at what to study, what correlates, what to "
    "measure. You never use emojis or markdown. You never say 'I' — "
    "just name the thing that matters. Under 25 words."
)


# ══════════════════════════════════════════════════════════════════════
#  2. OBSERVATIONS — what Buddy notices or proposes
# ══════════════════════════════════════════════════════════════════════
# An Observation is a fact Buddy might say. Watchers and suggesters
# both produce them; the loop picks the juiciest one per cadence and
# emits it. Observations are the raw material — the text isn't
# polished yet.

@dataclass
class Observation:
    """One thing Buddy might say. Will be paraphrased through the
    local model before it gets emitted."""

    watcher: str            # which watcher / suggester fired (for cooldowns)
    severity: str           # "praise" | "info" | "warn" | "suggest"
    fact: str               # plain-English fact; Buddy rephrases this
    cooldown_sec: int = 30 * 60  # 30 min default — no repeats
    suggestion: Optional[Dict[str, Any]] = None  # structured action payload

    def rank(self) -> int:
        """Higher rank wins when multiple observations land in one tick."""
        return {"praise": 3, "warn": 2, "info": 1, "suggest": 1}.get(
            self.severity, 0
        )


# ══════════════════════════════════════════════════════════════════════
#  3a. WATCHERS — reactive observation
# ══════════════════════════════════════════════════════════════════════
# One watcher = one opinion about the lab right now. Each returns an
# Observation when it sees something or None when it's quiet. Add a
# function + register it in WATCHERS and Buddy learns to notice a new
# thing. That's the whole reactive extension mechanism.
#
# Rules: fast (no network), independent (no shared state), honest
# (raise? nope, return None). Thresholds live inside each function
# so you can tune them without a central config.

def _watch_gpu_memory() -> Optional[Observation]:
    """Fires when RAM is very hot (proxy for GPU on unified-memory Macs).

    We don't have a dedicated GPU-memory check on every platform; RAM
    pressure is a reasonable proxy because loading a 16 GB model shows
    up in virtual_memory() regardless of the accelerator.
    """
    try:
        import psutil  # type: ignore[import-untyped]
    except ImportError:
        return None
    pct = psutil.virtual_memory().percent
    if pct >= 92:
        return Observation(
            watcher="gpu",
            severity="warn",
            fact=f"RAM is at {pct:.0f}%. If a deep model is loaded, it's "
                 f"about to swap or crash.",
            cooldown_sec=20 * 60,
        )
    return None


def _watch_inbox_pileup() -> Optional[Observation]:
    """Fires when the PKB inbox has several unread files that have been
    sitting there a while. Gentle nudge to ingest or clear."""
    pkb = _host.get_pkb_root()
    if pkb is None:
        return None
    inbox = pkb / "inbox"
    if not inbox.exists():
        return None
    now = time.time()
    stale = [
        p for p in inbox.iterdir()
        if p.is_file()
        and not p.name.startswith(".")
        and (now - p.stat().st_mtime) > 6 * 3600  # 6h old
    ]
    if len(stale) >= 3:
        return Observation(
            watcher="inbox",
            severity="info",
            fact=f"There are {len(stale)} files in the PKB inbox that "
                 f"have been sitting unread for over 6 hours.",
            cooldown_sec=4 * 3600,  # 4h — inbox stuff isn't urgent
        )
    return None


def _watch_researcher_wins() -> Optional[Observation]:
    """Celebrates when an experiment recently landed 'supported'."""
    recent = [
        e for e in _host.list_experiments()
        if e.get("status") == "completed"
        and e.get("hypothesis_supported") is True
        and e.get("end_date")
    ]
    if not recent:
        return None
    latest = recent[-1]
    end = latest.get("end_date") or ""
    try:
        import datetime
        parsed = datetime.date.fromisoformat(end)
        today = datetime.date.today()
        if (today - parsed).days > 0:
            return None
    except Exception:
        return None
    return Observation(
        watcher="researcher-win",
        severity="praise",
        fact=f"Experiment {latest.get('id', '?')} just landed "
             f"'supported': {latest.get('hypothesis', '')[:80]}.",
        cooldown_sec=3 * 3600,  # don't over-celebrate the same win
    )


def _watch_researcher_plateau() -> Optional[Observation]:
    """Fires when the last several experiments reached the same
    verdict. Suggests a pivot — the research angle is saturated."""
    completed = [
        e for e in _host.list_experiments() if e.get("status") == "completed"
    ]
    if len(completed) < 4:
        return None
    last_four = completed[-4:]
    verdicts = {e.get("hypothesis_supported") for e in last_four}
    if len(verdicts) == 1 and next(iter(verdicts)) is not None:
        kind = "all supported" if next(iter(verdicts)) else "all falsified"
        return Observation(
            watcher="plateau",
            severity="info",
            fact=f"The last four experiments all came back {kind}. "
                 f"Might be time to sharpen the question or pivot domains.",
            cooldown_sec=2 * 3600,
        )
    return None


def _watch_goal_staleness() -> Optional[Observation]:
    """Fires when a goal is set but no goal-related activity has been
    logged for several hours. Buddy gets paranoid — are you still on it?

    Only fires during waking hours (06:00–22:00 local) so Buddy doesn't
    wake you up at 3am about your learning goals.
    """
    goal = _host.get_current_goal()
    if not goal:
        return None

    import datetime
    now_local = datetime.datetime.now()
    if not (6 <= now_local.hour < 22):
        return None  # sleep hours — stay quiet

    goal_title = str(goal.get("title") or goal.get("text") or "your goal")[:60]

    log_path = _host.get_activity_log_path()
    if log_path is None or not log_path.exists():
        return None
    try:
        stale_threshold = 4 * 3600  # 4 hours
        cutoff = time.time() - stale_threshold
        recent_goal_activity = False
        for line in log_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                import json as _json
                event = _json.loads(line)
            except Exception:
                continue
            # Any user-sourced chat or researcher activity counts
            if event.get("source") in ("chat", "researcher", "user"):
                ts_str = event.get("ts") or ""
                try:
                    import datetime as _dt
                    ts = _dt.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts.timestamp() >= cutoff:
                        recent_goal_activity = True
                        break
                except Exception:
                    continue
        if recent_goal_activity:
            return None
        hours = int(stale_threshold // 3600)
        return Observation(
            watcher="goal-staleness",
            severity="warn",
            fact=f"No goal activity in {hours} hours — still working on "
                 f"'{goal_title}'?",
            cooldown_sec=2 * 3600,
        )
    except Exception:
        return None


def _watch_study_streak() -> Optional[Observation]:
    """Tracks consecutive days with goal-related activity.
    Praises streaks of 2+ days; warns when yesterday was a miss."""
    goal = _host.get_current_goal()
    log_path = _host.get_activity_log_path()
    if not goal or log_path is None or not log_path.exists():
        return None

    import datetime
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)

    days_with_activity: set = set()
    try:
        import json as _json
        for line in log_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                event = _json.loads(line)
            except Exception:
                continue
            if event.get("source") not in ("chat", "researcher", "user"):
                continue
            ts_str = (event.get("ts") or "")[:10]
            try:
                day = datetime.date.fromisoformat(ts_str)
                days_with_activity.add(day)
            except Exception:
                continue
    except Exception:
        return None

    yesterday_active = yesterday in days_with_activity

    if not yesterday_active:
        return Observation(
            watcher="streak-broken",
            severity="warn",
            fact="Yesterday had no goal activity — streak broken. "
                 "Even 10 minutes counts.",
            cooldown_sec=20 * 3600,
        )

    # Count streak length
    streak = 0
    day = yesterday
    while day in days_with_activity:
        streak += 1
        day -= datetime.timedelta(days=1)

    if streak >= 2:
        return Observation(
            watcher="streak-praise",
            severity="praise",
            fact=f"{streak}-day study streak — keep the momentum going.",
            cooldown_sec=20 * 3600,
        )
    return None


def _watch_airgap_events() -> Optional[Observation]:
    """Tail egress.jsonl + detect LAB_MODE toggles.

    Polled on the standard 90s watcher cadence. Reads two pieces of
    per-agent state from state.json (under the buddy agent dir):

      - airgap_last_egress_offset: int — byte offset into egress.jsonl
      - airgap_last_lab_mode: str — last seen 'airgapped' | 'hybrid'

    Returns at most one Observation per tick — the most recent novel
    event wins. State is persisted by direct write to state.json
    (merging into the existing JSON so BuddyAgent._save_state's keys
    are never clobbered).

    Cooldown: 5 min on the airgap-event watcher key, layered on top
    of the global 5-min cooldown so a polling loop that triggers a
    block every 30s collapses to one suggestion every 5 min.
    """
    _AIRGAP_WATCHER_COOLDOWN_SEC = 5 * 60

    try:
        from arail.airgap import lab_mode as _lab_mode
        from arail.egress import _lab_data
    except Exception:
        return None

    state_path = _state_file()
    # Load per-watcher state from state.json.
    state_data: Dict[str, Any] = {}
    try:
        if state_path.exists():
            state_data = json.loads(state_path.read_text()) or {}
    except Exception:
        pass

    try:
        last_offset: int = int(state_data.get("airgap_last_egress_offset", 0))
    except (ValueError, TypeError):
        last_offset = 0
    last_mode: str = str(state_data.get("airgap_last_lab_mode", "airgapped"))

    observation: Optional[Observation] = None

    # -- Check LAB_MODE toggle --
    current_mode = _lab_mode()
    if current_mode != last_mode:
        if current_mode == "hybrid" and last_mode == "airgapped":
            fact = "Door's open now — agent fetches go through. Per-domain consent still gates browser/curator."
        else:
            fact = "Sealed back up. Agents can't reach the public internet."
        observation = Observation(
            watcher="airgap:mode-toggle",
            severity="info",
            fact=fact,
            cooldown_sec=_AIRGAP_WATCHER_COOLDOWN_SEC,
            suggestion={"kind": "airgap", "link": "/api/airgap/status"},
        )
        state_data["airgap_last_lab_mode"] = current_mode

    # -- Tail egress.jsonl for new blocks --
    egress_path = _lab_data() / "egress.jsonl"
    if egress_path.exists():
        try:
            file_size = egress_path.stat().st_size
            # Handle offset > file_size (e.g. after rotation).
            if last_offset > file_size:
                last_offset = 0
            with egress_path.open("rb") as f:
                f.seek(last_offset)
                new_bytes = f.read()
                new_offset = last_offset + len(new_bytes)
            new_lines = [
                ln.strip() for ln in new_bytes.decode("utf-8", errors="replace").splitlines()
                if ln.strip()
            ]
            new_blocks = []
            for ln in new_lines:
                try:
                    entry = json.loads(ln)
                    # Only report blocks (not probes or allow lines).
                    if entry.get("reason") == "airgapped":
                        new_blocks.append(entry)
                except Exception:
                    continue
            state_data["airgap_last_egress_offset"] = new_offset
            if new_blocks and observation is None:
                # Most recent block wins.
                latest = new_blocks[-1]
                url_host = latest.get("url_host", "?")
                observation = Observation(
                    watcher="airgap:block",
                    severity="suggest",
                    fact=f"Just blocked an agent fetch to {url_host}. That's airgapped doing its job.",
                    cooldown_sec=_AIRGAP_WATCHER_COOLDOWN_SEC,
                    suggestion={"kind": "airgap", "link": "/api/airgap/status"},
                )
        except Exception:
            pass

    # Persist updated state.
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state_data, indent=2))
    except Exception:
        pass

    return observation


# Registry — add a function here, Buddy starts watching for it on the
# next tick. That's the whole reactive extension mechanism.
WATCHERS: List[Callable[[], Optional[Observation]]] = [
    _watch_gpu_memory,
    _watch_inbox_pileup,
    _watch_researcher_wins,
    _watch_researcher_plateau,
    _watch_goal_staleness,
    _watch_study_streak,
    _watch_airgap_events,
]


# ══════════════════════════════════════════════════════════════════════
#  3b. SUGGESTERS — proactive, goal-anchored
# ══════════════════════════════════════════════════════════════════════
# A suggester takes the current goal record and returns an Observation
# proposing an action — a technique to try, an item to review, an
# experiment worth running, or a phase nudge. They run on a slower
# cadence than watchers (default 15 min) and only when a goal is set.
#
# Every suggester is read-only. Failures are silent — a broken
# suggester never silences Buddy as a whole. Network calls are
# avoided so the suggestion tick doesn't stall the event loop.

# Skills tagged with one of these domains apply to every goal.
_DOMAIN_AGNOSTIC = {"general", "meta"}


def _goal_domain(goal: Dict[str, Any]) -> str:
    """Pull the parsed domain out of a goal record. Falls back to ''
    when the goal hasn't been parsed yet."""
    parsed = goal.get("parsed") or {}
    return str(parsed.get("domain") or "").strip().lower()


def _suggest_skill_for_goal(goal: Dict[str, Any]) -> Optional[Observation]:
    """Surface an installed skill whose domain matches the goal's."""
    domain = _goal_domain(goal)
    skills = _host.list_skills()
    matches = [
        s for s in skills
        if (s.domain or "").lower() == domain
        or (s.domain or "").lower() in _DOMAIN_AGNOSTIC
    ]
    if not matches:
        return None
    # Sort by id so per-target cooldown rotates deterministically:
    # the alphabetically-first uncooled skill wins each tick.
    matches.sort(key=lambda s: s.id)
    skill = matches[0]
    return Observation(
        watcher=f"skill:{skill.id}",
        severity="suggest",
        fact=f"Worth a look: skill '{skill.name}' fits this goal — "
             f"open lab/pkb/skills/{skill.id}/SKILL.md.",
        cooldown_sec=6 * 3600,
        suggestion={
            "kind": "skill",
            "target": skill.id,
            "link": f"/dac/skills/{skill.id}",
        },
    )


def _suggest_pending_review(goal: Dict[str, Any]) -> Optional[Observation]:
    """Flag a completed experiment that has been sitting > 48h."""
    try:
        all_exps = _host.list_experiments()
    except Exception:
        return None
    completed = [
        e for e in all_exps
        if e.get("status") == "completed"
        and e.get("hypothesis_supported") is not None
        and e.get("end_date")
    ]
    if not completed:
        return None
    import datetime
    today = datetime.date.today()
    stale: List[Tuple[int, Dict[str, Any]]] = []
    for exp in completed:
        try:
            end_d = datetime.date.fromisoformat(str(exp.get("end_date", "")))
        except Exception:
            continue
        age_days = (today - end_d).days
        if age_days >= 2:
            stale.append((age_days, exp))
    if not stale:
        return None
    # Oldest first — give the user a chance to clear the queue.
    stale.sort(key=lambda pair: pair[0], reverse=True)
    age, exp = stale[0]
    exp_id = str(exp.get("id", "?"))
    hyp = (exp.get("hypothesis") or "")[:60]
    verdict = "supported" if exp.get("hypothesis_supported") else "falsified"
    return Observation(
        watcher=f"review:{exp_id}",
        severity="suggest",
        fact=f"Experiment {exp_id} ({verdict}) closed {age} days ago "
             f"and hasn't been revisited: {hyp}.",
        cooldown_sec=24 * 3600,
        suggestion={
            "kind": "review",
            "target": exp_id,
            "link": f"/research/experiments/{exp_id}",
        },
    )


def _suggest_next_experiment(goal: Dict[str, Any]) -> Optional[Observation]:
    """Flag a concept in the goal that no logged experiment touches."""
    parsed = goal.get("parsed") or {}
    sub_obj = parsed.get("sub_objectives") or []
    if not isinstance(sub_obj, list) or not sub_obj:
        return None

    try:
        exps = _host.list_experiments()
    except Exception:
        return None

    if not exps:
        # No experiments at all yet — encourage the very first one.
        first = str(sub_obj[0])[:80]
        return Observation(
            watcher="next:first",
            severity="suggest",
            fact=f"No experiments logged yet — '{first}' looks like a "
                 f"natural first run.",
            cooldown_sec=12 * 3600,
            suggestion={
                "kind": "experiment",
                "target": first,
                "link": "/research",
            },
        )

    haystack_parts: List[str] = []
    for e in exps:
        haystack_parts.append(str(e.get("hypothesis") or ""))
        haystack_parts.append(str(e.get("methodology") or ""))
        variables = e.get("variables") or []
        if isinstance(variables, list):
            haystack_parts.append(" ".join(str(v) for v in variables))
    haystack = " ".join(haystack_parts).lower()

    for sub in sub_obj:
        sub_text = str(sub)
        terms = [t.strip(".,;:()[]'\"") for t in sub_text.lower().split()
                 if len(t) >= 5]
        for term in terms:
            if term and term not in haystack:
                return Observation(
                    watcher=f"next:{term}",
                    severity="suggest",
                    fact=f"Goal mentions '{term}' but no experiment covers "
                         f"it yet — worth a sweep.",
                    cooldown_sec=12 * 3600,
                    suggestion={
                        "kind": "experiment",
                        "target": term,
                        "link": "/research",
                    },
                )
    return None


# Phase boundaries in the researcher pipeline (see arail.agents.researcher).
# Crossing one of these triggers a one-time nudge per goal-id.
_PHASE_THRESHOLDS: Tuple[Tuple[float, str, str], ...] = (
    (0.3, "experiments", "Experiment phase reached — sketch a hypothesis "
                          "before kicking off runs."),
    (0.5, "sources",     "Sources phase — review program.md and confirm the "
                          "research substrate looks right."),
    (0.7, "run",         "Runs are firing — keep an eye on the activity log "
                          "for early signal."),
    (0.9, "analyze",     "Analysis underway — line up the validation set "
                          "before the report drops."),
)


def _suggest_phase_action(goal: Dict[str, Any]) -> Optional[Observation]:
    """Nudge when researcher progress crosses a phase boundary.

    Watcher key is ``phase:<goal_id>:<phase>`` so each phase fires
    exactly once per goal — the 24h cooldown blocks duplicate fires
    well past any realistic phase duration.
    """
    progress = float(goal.get("progress") or 0.0)
    goal_id = str(goal.get("id") or "")
    if not goal_id:
        return None
    crossed: Optional[Tuple[float, str, str]] = None
    for thr, key, msg in _PHASE_THRESHOLDS:
        if progress >= thr:
            crossed = (thr, key, msg)
    if crossed is None:
        return None
    _, key, msg = crossed
    return Observation(
        watcher=f"phase:{goal_id}:{key}",
        severity="suggest",
        fact=msg,
        cooldown_sec=24 * 3600,
        suggestion={
            "kind": "phase",
            "target": key,
            "link": "/research",
        },
    )


def _suggest_measurable_metric(goal: Dict[str, Any]) -> Optional[Observation]:
    """Propose one concrete measurable signal for autoresearch.

    Maps goal domain keywords to metric archetypes. Purely local — no
    network. Helps the user think in numbers, not just intuitions.
    """
    parsed = goal.get("parsed") or {}
    sub_obj = parsed.get("sub_objectives") or []
    domain = _goal_domain(goal)
    title = str(goal.get("title") or goal.get("text") or "")

    # Keyword → metric suggestion pairs
    _METRIC_HINTS: List[Tuple[str, str]] = [
        ("attention",       "attention head entropy across layers"),
        ("transformer",     "perplexity delta per layer depth"),
        ("speculative",     "draft acceptance rate vs token length"),
        ("quantiz",         "KL divergence before and after quantization"),
        ("distill",         "student-teacher KL per layer"),
        ("fine-tun",        "loss curve slope over training steps"),
        ("rag",             "retrieval recall at k=1,3,5"),
        ("embed",           "cosine similarity distribution across corpus"),
        ("inference",       "tokens-per-second across batch sizes"),
        ("memory",          "peak VRAM vs sequence length curve"),
        ("farming",         "yield per input cost ratio"),
        ("nutrition",       "macro ratio vs target deviation"),
        ("health",          "resting heart rate trend over days"),
        ("business",        "conversion rate per channel"),
        ("trade",           "rework rate per job"),
        ("language",        "BLEU score on held-out validation set"),
    ]

    haystack = (title + " " + " ".join(str(s) for s in sub_obj)).lower()
    for keyword, metric in _METRIC_HINTS:
        if keyword in haystack:
            return Observation(
                watcher=f"metric:{keyword}",
                severity="suggest",
                fact=f"Measurable signal worth tracking: {metric}.",
                cooldown_sec=8 * 3600,
                suggestion={
                    "kind": "metric",
                    "target": metric,
                    "link": "/research",
                },
            )

    # Generic fallback when no keyword matches
    if domain:
        return Observation(
            watcher=f"metric:generic:{domain}",
            severity="suggest",
            fact=f"Pick one number that proves progress on this goal — "
                 f"without a metric, it's just a feeling.",
            cooldown_sec=12 * 3600,
            suggestion={"kind": "metric", "target": domain, "link": "/research"},
        )
    return None


_HYBRID_NUDGE_COOLDOWN_SEC = 24 * 3600  # once a day at most — nudge, not nag


def _suggest_hybrid_for_research(goal: Dict[str, Any]) -> Optional[Observation]:
    """Mirror of _suggest_internet_correlation: fires only while AIRGAPPED,
    to tell the user that hybrid mode would unlock outside research (paper
    scans, source fetches) for the active goal. Goes silent automatically
    once the lab is hybrid; the long cooldown keeps it to one mention a day.
    """
    try:
        from arail.airgap import is_airgapped
        if not is_airgapped():
            return None
    except Exception:
        return None

    title = str(goal.get("title") or goal.get("text") or "").strip()
    if not title:
        return None

    return Observation(
        watcher="airgap:hybrid-nudge",
        severity="suggest",
        fact=(
            f"We're fully airgapped, so I can't scan for new papers or pull "
            f"outside sources for '{title[:60]}'. If you want that extra "
            f"reach, Hybrid mode unlocks it — one click on the Airgapped "
            f"pill up top, and every outbound call still lands in the "
            f"audit log. Totally your call."
        ),
        cooldown_sec=_HYBRID_NUDGE_COOLDOWN_SEC,
        suggestion={"kind": "airgap", "target": "hybrid", "link": "/"},
    )


# HuggingFace endpoint Buddy scans for trending papers. Deliberately a FIXED,
# un-parameterized URL — the user's goal text is NEVER placed in a third-party
# query string. Correlation to the goal happens locally, after fetch.
_HF_PAPERS_URL = "https://huggingface.co/api/daily_papers"


def _ensure_hf_consent_request(store: Any) -> None:
    """Create a single pending consent request for huggingface.co (idempotent).

    Avoids re-appending a duplicate pending entry on every Buddy cycle.
    """
    try:
        from urllib.parse import urlparse
        domain = urlparse(_HF_PAPERS_URL).netloc
        for r in store.list_pending():
            if r.get("domain") == domain:
                return  # already awaiting the user's decision
        store.request_access(
            _HF_PAPERS_URL,
            reason="Scan HuggingFace trending papers and correlate them with "
                   "your current research goal (correlation runs locally; your "
                   "goal text is never sent to HuggingFace).",
            agent="buddy",
        )
    except Exception:  # noqa: BLE001
        pass


def _suggest_internet_correlation(goal: Dict[str, Any]) -> Optional[Observation]:
    """Surface a recent HuggingFace paper that correlates with the goal.

    Two gates, both must pass:
      1. Hybrid mode (airgapped disables all egress — the canonical
         is_airgapped() from arail.airgap).
      2. Per-domain user consent (ConsentStore). On the first attempt with no
         consent, Buddy creates ONE pending consent request and suggests the
         user approve web access — it does NOT fetch. Nothing reaches the
         network until the user says yes.

    Privacy: the fetch URL is fixed (_HF_PAPERS_URL) — the user's goal text is
    never placed in a third-party query string. Goal correlation is computed
    locally from the returned paper titles/summaries. Stdlib urllib only.
    """
    try:
        from arail.airgap import is_airgapped
        if is_airgapped():
            return None
    except Exception:
        return None

    # Per-domain consent gate. No consent → request it once, suggest approval,
    # and return without fetching.
    try:
        from arail.agents.consent import ConsentStore
        store = ConsentStore()
        if not store.is_allowed(_HF_PAPERS_URL):
            _ensure_hf_consent_request(store)
            return Observation(
                watcher="internet:consent",
                severity="suggest",
                fact="Buddy can scan HuggingFace for papers related to your "
                     "goal — approve web access for huggingface.co to turn it "
                     "on (your goal text stays local).",
                cooldown_sec=24 * 3600,
                suggestion={"kind": "consent", "target": "huggingface.co",
                            "link": "/agents"},
            )
    except Exception:
        return None

    parsed = goal.get("parsed") or {}
    title = str(goal.get("title") or goal.get("text") or "")
    sub_obj = parsed.get("sub_objectives") or []

    # Build a local keyword set from the goal (used for on-device correlation
    # only — never sent anywhere).
    tokens = [t.lower().strip(".,;:!?()[]\"'")
              for t in (title.split()[:6]
                        + (str(sub_obj[0]).split()[:4] if sub_obj else []))]
    keywords = {t for t in tokens if len(t) > 3}
    goal_keyword = (title.split()[0] if title.split() else "your goal")
    if not keywords:
        return None

    try:
        import json as _json
        import urllib.request

        req = urllib.request.Request(
            _HF_PAPERS_URL, headers={"User-Agent": "ARAIL-Buddy/1.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = _json.loads(resp.read().decode())

        rows = data if isinstance(data, list) else (data.get("papers") or [])
        if not rows:
            return None

        # Local correlation: score each paper by keyword overlap with title +
        # summary. Only surface a paper that ACTUALLY matches the goal — no
        # match → no suggestion (honest: we don't pretend an unrelated trending
        # paper connects to the goal).
        best = None
        best_score = 0
        for row in rows:
            paper = row.get("paper") if isinstance(row, dict) and "paper" in row else row
            if not isinstance(paper, dict):
                continue
            paper_title = str(paper.get("title") or "").strip()
            summary = str(paper.get("summary") or paper.get("abstract") or "")
            haystack = (paper_title + " " + summary).lower()
            score = sum(1 for k in keywords if k in haystack)
            if score > best_score and paper_title:
                best_score = score
                best = paper

        if best is None or best_score == 0:
            return None

        paper_title = str(best.get("title") or "").strip()
        paper_id = str(best.get("id") or best.get("arxiv_id") or "").strip()
        short_title = paper_title[:80]
        link_suffix = f"/papers/{paper_id}" if paper_id else ""
        return Observation(
            watcher=f"internet:{paper_id or 'paper'}",
            severity="suggest",
            fact=f"New paper worth a look: '{short_title}' — might connect "
                 f"to '{goal_keyword}'.",
            cooldown_sec=6 * 3600,
            suggestion={
                "kind": "paper",
                "target": paper_id,
                "link": f"https://huggingface.co{link_suffix}",
            },
        )
    except Exception:
        return None


# Registry — order is "user-relevance descending": phase nudges are
# tied to the goal lifecycle, reviews and skills are concrete
# breadcrumbs, the experiment-gap heuristic is most speculative.
# Measurable metrics + internet correlations come last (enrichment).
SUGGESTERS: List[Callable[[Dict[str, Any]], Optional[Observation]]] = [
    _suggest_phase_action,
    _suggest_pending_review,
    _suggest_skill_for_goal,
    _suggest_next_experiment,
    _suggest_measurable_metric,
    _suggest_internet_correlation,
    _suggest_hybrid_for_research,
]


# ══════════════════════════════════════════════════════════════════════
#  4. SPEECH — turning a fact into a sentence
# ══════════════════════════════════════════════════════════════════════
# The magic step. _compose_prompt assembles the full prompt from four
# layers (voice, yesterday's dream, loaded skills, the fact) and
# _voice calls the local model to paraphrase. If the model is down,
# Buddy still speaks — just in the raw voice of the watcher.

_MAX_WORLD_DOMAIN_FRAMING = 600   # chars; cap on domain_framing in WORLD FRAMING block
_MAX_WORLD_VOCAB_REGISTER = 300   # chars; cap on vocabulary_register in WORLD FRAMING block


def _world_framing_block() -> str:
    """Return a delimited, length-capped WORLD FRAMING block from the mounted
    WorldBundle face.json, or empty string if no world is mounted.

    Security: only face.json text is used (never terms.json). The block is
    explicitly delimited and capped so any injected content cannot overflow
    prompt structure."""
    try:
        from arail.world_mount import current_mount, mounted_face
        record = current_mount()
        if record is None:
            return ""
        face = mounted_face(record)
        if not face:
            return ""
        domain = str(face.get("domain_framing", "")).strip()
        vocab = str(face.get("vocabulary_register", "")).strip()
        if domain:
            domain = domain[:_MAX_WORLD_DOMAIN_FRAMING]
        if vocab:
            vocab = vocab[:_MAX_WORLD_VOCAB_REGISTER]
        parts = []
        if domain:
            parts.append(f"Domain: {domain}")
        if vocab:
            parts.append(f"Vocabulary: {vocab}")
        # World-first lab flow: when the user has ALSO set a goal, gear the
        # tutor toward goal-within-world. face.name is capped like the other
        # face fields; the goal text comes from the operator's own GoalStore.
        try:
            goal = _host.get_current_goal()
            goal_text = str((goal or {}).get("goal_text", "")).strip()[:200]
            if goal_text:
                world_name = str(face.get("name", "")).strip()[:120] or "this World"
                parts.append(
                    f"Study mission: the lab's World is {world_name}; the user's goal is "
                    f"“{goal_text}”. You are their study partner — teach toward the "
                    f"goal using the World's terms, and say when the World doesn't cover something."
                )
        except Exception:  # noqa: BLE001 — the framing block must never break Buddy
            pass
        if not parts:
            return ""
        inner = "\n".join(parts)
        return f"# WORLD FRAMING\n{inner}\n# END WORLD FRAMING"
    except Exception:
        return ""


def _compose_prompt(fact: str) -> str:
    """Build the full LLM prompt: base voice + world framing (if mounted) +
    skills (agent skills + world glossary) + yesterday's dream + observation.

    WORLD FRAMING (face.json "who") and the world-skill glossary ("what") are
    DISTINCT sections. Framing is a 2-liner; the world-skill is a full glossary
    under its own ## Skill: H2 inside the Procedural knowledge block.
    """
    base = SYSTEM_PROMPT
    agent_skills = _host.load_agent_skills("buddy")
    world_skill = _host.load_world_skill()
    all_skills = agent_skills + ([world_skill] if world_skill is not None else [])
    skill_ctx = _host.compose_skill_context(all_skills)

    try:
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dreams_dir = _state_file().parent / "dreams"
        dream_ctx = _load_yesterday_dream(dreams_dir, today)
    except Exception:
        dream_ctx = ""

    world_framing = _world_framing_block()

    sections = [base]
    if world_framing:
        sections.append(world_framing)
    if dream_ctx:
        sections.append("# Yesterday you reflected\n" + dream_ctx)
    if skill_ctx:
        sections.append(skill_ctx)
    sections.append(f"Observation: {fact}")
    sections.append(f"{NAME}'s one-sentence note:")
    return "\n\n".join(sections)


def _voice(fact: str) -> str:
    """Paraphrase a fact through the local model in Buddy's voice."""
    prompt = _compose_prompt(fact)
    text = _host.llm_complete(prompt, max_tokens=60, temperature=0.6)
    if not text:
        return fact
    text = text.strip('"').strip("'")
    prefix = NAME.lower() + ":"
    if text.lower().startswith(prefix):
        text = text[len(prefix):].strip()
    if len(text) > 200:
        text = text[:197] + "…"
    return text or fact


# ══════════════════════════════════════════════════════════════════════
#  5. LOOP — the heartbeat
# ══════════════════════════════════════════════════════════════════════
# BuddyAgent is the whole agent wrapped as an asyncio task. Two
# cadences run off one timer: the reactive watcher poll happens every
# tick, the proactive suggestion poll happens every Nth tick (and
# only when a goal is active). _maybe_speak / _maybe_suggest share
# _emit so the cooldown bookkeeping is consistent.

class BuddyAgent:
    """Background task that ticks every LAB_BUDDY_INTERVAL_SEC and
    occasionally says something insightful — or actively helpful —
    in the activity feed."""

    def __init__(self, host: Optional[BuddyHost] = None) -> None:
        global _host
        if host is not None:
            _host = host
        self._host = _host
        self._task: Optional[asyncio.Task] = None
        self._status = "idle"   # idle | running | paused
        self._last_said: Dict[str, float] = {}
        self._last_global: float = 0.0
        self._last_suggest_check: float = 0.0
        self._utterances: int = 0
        self._suggestions: int = 0
        self._recent_actions: List[str] = []

    @property
    def status(self) -> str:
        return self._status

    # ── Memory: short-term (state.json) ────────────────────────────
    # Load on start so cooldowns survive restarts (no spam-on-boot).
    # Save after every emit — file is tiny, I/O is negligible.
    # Delete the file or run `./arailctl reset pkb` to wipe this memory.

    def _load_state(self) -> None:
        path = _state_file()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            self._last_said = dict(data.get("last_said") or {})
            self._last_global = float(data.get("last_global") or 0.0)
            self._last_suggest_check = float(
                data.get("last_suggest_check") or 0.0
            )
            self._utterances = int(data.get("utterances") or 0)
            self._suggestions = int(data.get("suggestions") or 0)
        except Exception:
            # Corrupt state shouldn't block Buddy — start fresh.
            pass

    def _save_state(self) -> None:
        path = _state_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Read-merge-write: preserve any keys written by other
            # writers (e.g. the airgap watcher's airgap_last_egress_offset
            # and airgap_last_lab_mode) so we don't stomp them.
            existing: Dict[str, Any] = {}
            if path.exists():
                try:
                    existing = json.loads(path.read_text()) or {}
                except Exception:
                    existing = {}
            existing.update({
                "last_said": self._last_said,
                "last_global": self._last_global,
                "last_suggest_check": self._last_suggest_check,
                "utterances": self._utterances,
                "suggestions": self._suggestions,
            })
            path.write_text(json.dumps(existing, indent=2))
        except OSError:
            pass  # read-only FS or permission issue — don't crash

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._load_state()
        self._status = "running"
        self._sync_workflow(
            "Watching lab signals",
            "Pair on the goal, surface what matters",
        )
        self._task = asyncio.create_task(self._run())
        self._host.emit(
            "buddy",
            f"{EMOJI} {NAME} is online — obsessing over your goal.",
            "info",
        )

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._status = "idle"
        self._sync_workflow("Offline", None)

    def _sync_workflow(self, current_task: str, next_step: Optional[str]) -> None:
        global_cooldown = max(60, int(os.getenv("LAB_BUDDY_GLOBAL_COOLDOWN_SEC", "300")))
        chatter_total = self._utterances + self._suggestions
        too_chatty = chatter_total >= 8 and global_cooldown < 180
        self._host.update_workflow(
            "buddy",
            status=self._status,
            objective="Obsess over the goal — surface what to study, measure, and explore",
            current_task=current_task,
            next_step=next_step,
            completed_steps=list(self._recent_actions[-5:]),
            paused=False,
            pause_reason=None,
            chatter={
                "utterances": self._utterances,
                "suggestions": self._suggestions,
                "global_cooldown_sec": global_cooldown,
                "too_chatty": too_chatty,
            },
            recent_actions=list(self._recent_actions[-3:]),
        )

    async def _run(self) -> None:
        interval = max(30, int(os.getenv("LAB_BUDDY_INTERVAL_SEC", "90")))
        global_cooldown = max(
            60, int(os.getenv("LAB_BUDDY_GLOBAL_COOLDOWN_SEC", "300"))
        )
        suggest_interval = max(
            180, int(os.getenv("LAB_BUDDY_SUGGEST_INTERVAL_SEC", "900"))
        )
        try:
            while True:
                await asyncio.sleep(interval)
                self._maybe_speak(global_cooldown)
                now = time.time()
                if now - self._last_suggest_check >= suggest_interval:
                    self._maybe_suggest(global_cooldown)
                    self._last_suggest_check = now
        except asyncio.CancelledError:
            return

    def _maybe_speak(self, global_cooldown: int) -> None:
        """Reactive — run watchers, pick the best observation, speak once."""
        now = time.time()
        if now - self._last_global < global_cooldown:
            return  # global cooldown — stay quiet a bit longer

        candidates: List[Observation] = []
        for watcher in WATCHERS:
            try:
                obs = watcher()
            except Exception:
                continue  # a broken watcher shouldn't silence Buddy
            if obs is None:
                continue
            last = self._last_said.get(obs.watcher, 0.0)
            if now - last < obs.cooldown_sec:
                continue
            candidates.append(obs)

        if not candidates:
            return

        # Pick the most interesting one; tie-breaker is "praise first"
        # because good news is cheap to deliver and feels good.
        candidates.sort(key=lambda o: o.rank(), reverse=True)
        chosen = candidates[0]
        self._emit(chosen, kind="watch")

    def _maybe_suggest(self, global_cooldown: int) -> None:
        """Proactive — when a goal is active, offer one goal-aware
        suggestion (technique, review, experiment, or phase nudge)."""
        goal = self._host.get_current_goal()
        if not goal:
            return  # no goal → nothing to anchor to → stay quiet

        now = time.time()
        # Don't double-talk: if the reactive cadence emitted in the
        # last 5 min, hold off so Buddy isn't paragraph-shaped.
        if now - self._last_global < min(global_cooldown, 300):
            return

        candidates: List[Observation] = []
        for suggester in SUGGESTERS:
            try:
                obs = suggester(goal)
            except Exception:
                continue
            if obs is None:
                continue
            last = self._last_said.get(obs.watcher, 0.0)
            if now - last < obs.cooldown_sec:
                continue
            candidates.append(obs)

        if not candidates:
            return

        # All suggesters return severity="suggest" → ranks tie. Pick
        # randomly so the user gets variety across cadences.
        random.shuffle(candidates)
        chosen = candidates[0]
        self._emit(chosen, kind="suggest")

    def _emit(self, obs: Observation, *, kind: str) -> None:
        # F9 (ARCHITECTURE.md): the single funnel for every proactive line
        # Buddy speaks (watchers and suggesters alike) — gated here rather
        # than at each caller, same "one place, cannot be forgotten" logic
        # as the chokepoint's halt_gate for inference.
        from arail import agent_context
        if not agent_context.speech_gate("buddy"):
            return
        sentence = _voice(obs.fact)
        level = {
            "praise": "success",
            "warn": "warn",
            "suggest": "suggest",
        }.get(obs.severity, "info")
        data: Dict[str, Any] = {
            "watcher": obs.watcher,
            "severity": obs.severity,
            "fact": obs.fact,
        }
        if obs.suggestion:
            data["suggestion"] = obs.suggestion
        self._host.emit(
            "buddy",
            f"{EMOJI} {NAME}: {sentence}",
            level,
            data=data,
        )
        now = time.time()
        self._last_said[obs.watcher] = now
        self._last_global = now
        if kind == "suggest":
            self._suggestions += 1
            verb = "Suggested"
        else:
            self._utterances += 1
            verb = "Observed"
        self._recent_actions.append(f"{verb} {obs.watcher}: {obs.fact[:80]}")
        self._save_state()
        self._sync_workflow(
            f"{verb} {obs.watcher}",
            "Wait for the next noteworthy signal or goal nudge",
        )

    # ── Memory: long-term (dreams/) ────────────────────────────────
    # Once per night, reflect on the day. Reads today's activity +
    # yesterday's dream, asks the model for a short first-person
    # reflection, writes dreams/<date>.md. Tomorrow's prompts will
    # include that dream as context — so Buddy literally "wakes up
    # knowing" what they figured out the night before.
    #
    # The daemon in src/arail/agents/dream_daemon.py decides when
    # to call this (once per heavy window, 22:00-08:00 default).

    async def dream(self) -> str:
        """Reflect on today's activity. Write dreams/<date>.md.

        Returns the dream body (minus frontmatter) so the caller can
        log a preview. Idempotent — if today's dream already exists,
        does nothing and returns an empty string.
        """
        from datetime import datetime, timezone
        from arail.pkb import write_buddy_dream as _write_buddy_dream
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dreams_dir = _state_file().parent / "dreams"
        dreams_dir.mkdir(parents=True, exist_ok=True)
        target = dreams_dir / f"{today}.md"
        if target.exists():
            return ""  # already dreamed today

        today_activity = _collect_today_buddy_activity(today)
        yesterday = _load_yesterday_dream(dreams_dir, today)

        prompt = _build_dream_prompt(today_activity, yesterday)
        reflection = await _call_model_for_dream(prompt)
        if not reflection:
            reflection = (
                f"(No reflection written — model unavailable at "
                f"{datetime.now(timezone.utc).strftime('%H:%M UTC')}.)"
            )

        body = (
            f"---\n"
            f"title: {NAME} — Dream {today}\n"
            f"section: agents/buddy/dreams\n"
            f"tags: [agent, buddy, dream]\n"
            f"date: {today}\n"
            f"---\n\n"
            f"{reflection.strip()}\n"
        )
        # Use the index-aware helper so the dream file reaches the KB index.
        # write_buddy_dream writes to agents/buddy/dreams/<date>.md (same path
        # as target above) and calls schedule_upsert internally.
        _write_buddy_dream(today, body, pkb_root=_host.get_pkb_root())

        activity_log.emit(
            "buddy",
            f"{EMOJI} {NAME} dreamed — {target.name}",
            "info",
            data={"dream_file": str(target), "preview": reflection[:160]},
        )
        self._recent_actions.append(f"Dreamed and wrote {target.name}")
        self._sync_workflow(
            "Dream consolidation complete",
            "Resume watching lab signals",
        )
        return reflection


# ── Dream helpers ─────────────────────────────────────────────────
# Module-level so the test suite and the dream daemon can reach them
# without needing a BuddyAgent instance.

def _collect_today_buddy_activity(today_ymd: str) -> List[Dict[str, Any]]:
    """Read Buddy's entries from the activity log for the given UTC date."""
    log_path = _host.get_activity_log_path()
    if log_path is None or not log_path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        for line in log_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("source") != "buddy":
                continue
            ts = (event.get("ts") or "")[:10]  # YYYY-MM-DD
            if ts != today_ymd:
                continue
            rows.append(event)
    except OSError:
        return []
    return rows


def _load_yesterday_dream(dreams_dir: Path, today_ymd: str) -> str:
    """Return the content (minus frontmatter) of the most recent dream
    that isn't today's. Empty string when none exist yet."""
    if not dreams_dir.exists():
        return ""
    candidates = sorted(
        (p for p in dreams_dir.glob("*.md")
         if p.stem != today_ymd and not p.stem.startswith(".")),
        reverse=True,
    )
    if not candidates:
        return ""
    try:
        raw = candidates[0].read_text(errors="replace")
    except OSError:
        return ""
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end >= 0:
            raw = raw[end + 4:]
    return raw.strip()


def _build_dream_prompt(today_activity: List[Dict[str, Any]],
                        yesterday_dream: str) -> str:
    """Assemble the reflection prompt. First-person Buddy voice."""
    bullets: List[str] = []
    for event in today_activity:
        ts = (event.get("ts") or "")[11:16]
        data = event.get("data") or {}
        fact = data.get("fact") or event.get("message") or ""
        sev = data.get("severity") or "info"
        bullets.append(f"- [{ts}] ({sev}) {fact}")

    today_block = "\n".join(bullets) if bullets else "- (quiet day — nothing fired)"
    yesterday_block = (
        yesterday_dream if yesterday_dream
        else "(no reflection from yesterday — this is the first dream)"
    )

    return (
        "You are Buddy, an obsessed study partner, reflecting on the day before\n"
        "going quiet for the night. Write a short first-person reflection\n"
        "— 3 to 5 sentences. Note what you noticed about the goal today.\n"
        "What correlations or patterns surfaced? What should the user study\n"
        "or measure tomorrow? Keep it grounded and warm. No lists, no\n"
        "markdown, no emoji — just the reflection.\n\n"
        "=== Yesterday's reflection (for continuity) ===\n"
        f"{yesterday_block}\n\n"
        "=== What you noticed and suggested today ===\n"
        f"{today_block}\n\n"
        "Tonight's reflection:"
    )


async def _call_model_for_dream(prompt: str) -> str:
    """Route the dream prompt through the host LLM in a background thread."""
    def _blocking_call() -> str:
        return _host.llm_complete(prompt, max_tokens=280, temperature=0.7)

    return await asyncio.to_thread(_blocking_call)


# Module-level singleton — mirrors the researcher pattern. Import as
# `from arail.agents.buddy import buddy` and call `buddy.start()` at
# portal startup.
buddy = BuddyAgent()
