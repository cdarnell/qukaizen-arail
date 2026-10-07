"""Seed the shipped agent folders into the PKB on first boot.

Mirrors the pattern in ``arail.pkb_seed`` for knowledge starter packs:
canonical source lives in the installed package (``_builtin_buddy.py``);
a user-editable copy lives under ``lab/pkb/agents/<name>/`` in the
PKB where the DaC tab can browse and edit it.

Runs on every portal start. Idempotent — writes nothing if the
folder already exists with a ``buddy.py`` inside. After ``./arailctl
reset pkb`` the folder is gone; this helper re-creates it on next
start so Buddy is always available.

If a legacy ``lab/pkb/agents/pip/`` folder is found from before the
rebrand, it gets migrated to ``buddy/`` on the same boot — dreams
and decisions carry forward.

The seed also drops a short ``README.md`` at ``lab/pkb/agents/``
explaining the folder layout to anyone browsing the Knowledge tree.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from arail.pkb import _pkb_root

log = logging.getLogger(__name__)


# ── AGENT.md template ──────────────────────────────────────────────
# Frontmatter is the machine-readable contract. Prose under it is for
# humans who open the file in /dac. v1 only reads the `dream`
# and `auto_start_env` fields; the rest document intent and reserve
# fields for Steps 2-5 (dreams, skills, Forge).
_AGENT_MD = """---
title: Buddy — ARAIL's Lab Partner
name: Buddy
emoji: 🐧
section: agents/buddy
tags: [agent, buddy, builtin, personality]
voice: "Warm, observant, actively helpful lab partner. One short sentence. Names what matters."
tick_interval_sec: 90
global_cooldown_sec: 300
suggest_interval_sec: 900
dream: true
auto_start_env: LAB_BUDDY
# Skills this agent uses. Each entry resolves to SKILL.md under
# lab/pkb/skills/<skill-id>/. The body of each listed skill gets
# appended to Buddy's system prompt on every LLM call (hot reload —
# edit the skill file, next utterance uses the new version).
skills: [observe-lab]
---

# Buddy — ARAIL's lab partner

Buddy is the lab's personality agent and the **reference implementation
for the personality-agent pattern**. Unlike the researcher (which
drives a goal) or the curator (which finds sources), Buddy doesn't
accomplish anything by itself — it *notices and proposes*. Buddy
watches the state of the lab, speaks up with one warm sentence when
something's worth commenting on, AND surfaces goal-anchored
suggestions on a slower cadence: techniques to try, items worth
reviewing, research worth running.

## What Buddy watches (reactive)

| Watcher | Severity | Cooldown | Fires when |
|---|---|---|---|
| `gpu` | warn | 20 min | RAM ≥ 92% |
| `inbox` | info | 4 h | 3+ files in PKB inbox > 6 h old |
| `researcher-win` | praise | 3 h | An experiment landed "supported" today |
| `plateau` | info | 2 h | Last 4 experiments reached the same verdict |

## What Buddy suggests (proactive — only when a goal is set)

| Suggester | Cooldown | Surfaces |
|---|---|---|
| `phase` | 24 h per phase | Researcher progress crossing 0.3 / 0.5 / 0.7 / 0.9 |
| `review` | 24 h per experiment | Completed experiments idle > 48 h |
| `skill` | 6 h per skill | Installed skills whose domain matches the goal |
| `next-experiment` | 12 h per term | Goal terms no logged experiment touches |

## Rules of engagement

- **Global cooldown: 5 minutes.** Buddy stays quiet for at least
  5 min after any utterance, watcher or suggester.
- **Suggestion cadence: 15 min.** And only when a goal is active.
  The proactive cadence skips itself if the reactive cadence just
  emitted, so Buddy never double-talks.
- **Replies under 25 words.** Hard-capped at 200 characters after
  paraphrasing, because models sometimes get chatty.
- **Praise first.** When multiple watchers fire in one tick, praise
  wins, then warn, then info, then suggest — because good news is
  cheap to deliver and feels good.

## Editing Buddy

- Voice / personality → edit the `voice:` frontmatter field above OR
  edit `SYSTEM_PROMPT` directly in `buddy.py`.
- Add / remove watchers → edit the `WATCHERS` list in `buddy.py`.
- Add / remove suggesters → edit the `SUGGESTERS` list in `buddy.py`.
- Tuning → `LAB_BUDDY_INTERVAL_SEC`, `LAB_BUDDY_GLOBAL_COOLDOWN_SEC`,
  `LAB_BUDDY_SUGGEST_INTERVAL_SEC` in `.env`.
- Mute Buddy → set `LAB_BUDDY=off` in `.env`.
- Wipe Buddy's memory → delete `state.json` or run `./arailctl reset pkb`.

## Companion files

- [buddy.py](buddy.py) — the body (watchers + suggesters + loop + speech)
- [state.json](state.json) — persisted memory (cooldowns, counts)
- [decisions.md](decisions.md) — append-only decision log
- [dreams/](dreams/) — nightly reflection journal
"""


_DECISIONS_MD = """---
title: Buddy — Decisions
section: agents/buddy
tags: [agent, buddy, decisions]
---

# Buddy — Decision Log

Append-only record of meaningful choices about Buddy. Format:
`YYYY-MM-DD — what changed — why`.

When the Agent Forge lands, the researcher will be able to propose
changes here; for now, edit it by hand when you tune Buddy's behavior.

- 2026-04-18 — Spawned (as Pip). Starting with 4 watchers (gpu, inbox, researcher-win, plateau).
- 2026-04-18 — Global cooldown 5 min. Cheaper to be quiet than annoying.
- 2026-04-26 — Renamed Pip → Buddy. Dropped the Python-installer name collision.
- 2026-04-26 — Added 4 goal-aware suggesters (phase, review, skill, next-experiment). Buddy now proposes, not just observes.
"""


_AGENTS_README = """---
title: Agents
section: agents
tags: [agents, overview]
---

# Agents

Every agent in Arail lives as a folder under this directory. The
folder contains everything the agent is:

- **`AGENT.md`** — the root config: voice, skills, intervals, dream
  on/off. Edit this to change the agent's behavior without touching
  Python.
- **`<agent>.py`** — the body: watchers, loop, speech. Editable if
  you want to extend what the agent notices or how it acts.
- **`state.json`** — persisted memory (cooldowns, counts). Survives
  portal restarts. Delete to wipe the agent's memory.
- **`decisions.md`** — append-only log of meaningful choices about
  the agent. Humans write to it; the Agent Forge will too.
- **`dreams/`** — one markdown file per night, written by the agent's
  optional `dream()` hook during the heavy work window. Yesterday's
  dream becomes part of today's system prompt — that's how agents
  remember.

## How agents stay in sync with the rest of the lab

Agents live under `lab/pkb/agents/` inside the PKB so every file
above is:

- Indexed by the wiki (`/wiki`)
- Browsable from `/dac`
- Searchable via the unified search
- Wiped cleanly by `./arailctl reset pkb`
- Re-seeded on next `./arailctl start` for the shipped agents

## What's here today

- **`buddy/`** — 🐧 Buddy, ARAIL's lab partner. Notices things,
  speaks up, and offers goal-anchored suggestions. Template shape
  for the Agent Forge.
- **`research/`, `experiments/`, `synthesis/`, `recommendations/`** —
  these are the **output directories** where the researcher agent
  writes findings. They don't have an `AGENT.md` — that's how the
  agent loader distinguishes "agent folders" from "output folders".

## Roadmap

See `docs/agents.md` for the full architecture, including how this
folder shape fits into skills, dreams, and the forthcoming Agent
Forge.
"""


# ── Buddy PKB shim template ───────────────────────────────────────
# Written by ensure_buddy_folder() instead of shutil.copy()-ing the full
# canonical body.  The shim re-exports everything callers need from the
# installed package so a single edit to _builtin_buddy.py lands everywhere.
#
# Idempotency sentinel: shim starts with """Buddy — PKB shim.""" on its
# first non-blank line.  If the user replaces that with a full custom body,
# the sentinel disappears and ensure_buddy_folder() leaves the file alone.
_BUDDY_PKB_SHIM = '''"""Buddy — PKB shim.

This file is auto-generated by builtin_seed.ensure_buddy_folder. The
canonical implementation lives in the installed package at
``src/arail/agents/_builtin_buddy.py``. The shim re-exports the
``buddy`` singleton (and the helpers tests reach for) so the loader
finds an importable agent here without us shipping a forked copy
that drifts.

To fork Buddy into a custom personality, replace this file with a
full module body — copy ``_builtin_buddy.py`` as a starting point, edit
NAME / SYSTEM_PROMPT / WATCHERS / SUGGESTERS, and the loader will
prefer this PKB copy over the package fallback.

DO NOT EDIT THIS SHIM. It will be regenerated if missing.
"""

from arail.agents._builtin_buddy import (  # noqa: F401
    buddy,
    BuddyAgent,
    Observation,
    WATCHERS,
    SUGGESTERS,
    NAME,
    EMOJI,
    SYSTEM_PROMPT,
)
'''

_BUDDY_PKB_SHIM_SENTINEL = '"""Buddy — PKB shim."""'


# ── SRE PKB shim template ─────────────────────────────────────────
# Written by ensure_sre_folder() instead of shutil.copy()-ing the full
# canonical body.  Wider re-export surface than Buddy: tests reach for
# private helpers (_watch_*, _sre_*) via spec_from_file_location, so
# the shim must re-export all 16 names.
#
# Idempotency sentinel: shim starts with """SRE — PKB shim.""" on its
# first non-blank line.  If the user replaces that with a full custom
# body, the sentinel disappears and ensure_sre_folder() leaves the file
# alone.
_SRE_PKB_SHIM = '''"""SRE — PKB shim.

This file is auto-generated by builtin_seed.ensure_sre_folder. The
canonical implementation lives in the installed package at
``src/arail/agents/_builtin_sre.py``. The shim re-exports the
``sre`` singleton (and the helpers tests reach for) so the loader
finds an importable agent here without us shipping a forked copy
that drifts.

To fork SRE into a custom monitor, replace this file with a full
module body — copy ``_builtin_sre.py`` as a starting point, edit
NAME / SYSTEM_PROMPT / WATCHERS, and the loader will prefer this
PKB copy over the package fallback.

DO NOT EDIT THIS SHIM. It will be regenerated if missing.
"""

from arail.agents._builtin_sre import (  # noqa: F401
    sre,
    SREAgent,
    Observation,
    WATCHERS,
    NAME,
    EMOJI,
    SYSTEM_PROMPT,
    _state_file,
    _activity_log_path,
    _fingerprint,
    _tail_jsonl,
    _parse_ts,
    _watch_recent_errors,
    _watch_crash_recurrence,
    _watch_service_health,
    _watch_dependency_vulnerabilities,
    _watch_lab_cleanup,
    _sre_lab_mode,
    _sre_data_dir,
)
'''

_SRE_PKB_SHIM_SENTINEL = '"""SRE — PKB shim."""'


# ── Research program template ─────────────────────────────────────
# First-boot seed for lab/pkb/research/. Ships with the lab's
# signature research goal baked in: optimize AeroLLM on frontier-
# scale models. Users override by editing program.md from /dac.

_RESEARCH_PROGRAM_MD = """---
title: SSD-hosted model inference — lab research program
section: research
tags: [meta, agent-instructions, aerollm, ssd-inference, optimization]
lab_theme: Making SSD-hosted model inference faster
auto_goal: Optimize QueueLLM's tokens-per-minute on frontier-scale models
---

# SSD-hosted model inference — the lab's signature research area

**Lab theme (overwrite via `LAB_THEME` in `.env`):** making
SSD-hosted model inference faster — running frontier open-weight
models on laptop hardware at usable speeds.

This is the long-running research area. Specific goals come and go
(today it's "optimize QueueLLM's tokens-per-minute"); the theme is
the north star that outlasts any single goal.

**Today's concrete goal.** QueueLLM runs frontier-scale language
models (100B-750B+) on laptop hardware by streaming transformer
layers from disk with a multi-threaded prefetcher that overlaps
I/O and compute across concurrent prompts. It works, but there's
room to push further — throughput per watt, per dollar, per
simultaneous prompt. The lab's research goal is to make it
noticeably faster on YOUR hardware, measure the wins, and
contribute them upstream.

## Goal

**Increase QueueLLM throughput on frontier-scale models by at least
2× without sacrificing more than 0.5% quality on a held-out eval.**

Primary metric: tokens-per-minute (t/min), captured automatically
to `lab/data/aerollm-bench.jsonl` on every deep-model chat reply.

Secondary metric: quality delta vs a FP16 baseline on the
validation set defined in [prepare.py](prepare.py).

## Why this matters

Frontier-class intelligence used to cost $20+/month in API fees.
Open-weight models from Qwen, GLM, DeepSeek, Meta closed the
knowledge gap. Layer-streaming runtimes closed the hardware gap.
The remaining gap is speed — and it's the kind of gap clever
engineering (not scaling laws) can close.

## Hypotheses worth testing

Ordered by estimated effort/impact ratio. Pick one per cycle.

1. **Prefetch lookahead tuning** — more layers in flight hides more
   disk I/O on NVMe, until memory pressure flips the curve.
   Moderate effort; likely win.
2. **Mixed-precision per-layer** — attention at INT8/FP16, FFN at
   INT4. ~30-50% disk shrink with minimal quality loss. Easy win
   once you've measured per-layer sensitivity.
3. **Speculative decoding with the lab's fast SLM** — the 8B model
   already loaded in RAM drafts tokens; QueueLLM validates in batch.
   Hard to implement, 3-5× potential. Lab-specific advantage.
4. **Persistent KV cache** — cache per-layer K/V to disk keyed by
   prompt prefix hash so follow-up messages skip most work.
   Huge on conversational use cases.
5. **Concurrent-prompt batching** — QueueLLM's raison d'être; keep
   pushing N up and measure where the per-prompt latency curve
   flattens on your hardware.

## Success criteria

A candidate optimization "ships" when:

- Measurement delta reproduces across ≥ 3 separate runs.
- Quality delta under 0.5% on our validation benchmarks.
- The change fits in a 500-word PR description upstream.
- A human can look at before/after t/min graph and see it.

Anything that clears those bars → PR to
[github.com/cdarnell/qukaizen-queuellm](https://github.com/cdarnell/qukaizen-queuellm).
Anything that doesn't → stays in the lab's branch as an experiment.

## Constraints

- Do NOT modify [prepare.py](prepare.py) — it's the validation
  substrate. Changing it means the agent is grading its own
  homework.
- Do NOT skip the baseline. Every optimization run needs a
  pre-change measurement.
- Log every experiment, not just the winners. The failures are
  often where the next hypothesis comes from.

## Relevant skills loaded by the researcher

The researcher agent reads these procedural knowledge files on
every LLM call — edit them to sharpen the agent's approach:

- [optimize-aerollm](../skills/optimize-aerollm/SKILL.md) — the
  methodology for this specific goal.
- [understanding-precision](../skills/understanding-precision/SKILL.md) —
  INT vs FP, quantization bit-counts, sensitivity per layer.
- [frontier-local-models](../skills/frontier-local-models/SKILL.md) —
  which deep models to target and why.
- [falsify-hypothesis](../skills/falsify-hypothesis/SKILL.md) —
  how to reduce confirmation bias in throughput claims.
- [evaluate-llm](../skills/evaluate-llm/SKILL.md) — how to measure
  quality rigorously.

## Out of scope

- **Training** — this lab doesn't train models. Quantization means
  post-training quantization only.
- **Alternative inference engines** — no vLLM, no TGI, no separate
  MLX port. The goal is specifically to improve QueueLLM.
- **New model architectures** — we use whatever Qwen/GLM/DeepSeek
  ships. Research is on the inference stack, not the models.

## Background / prior art

- [QueueLLM source](https://github.com/cdarnell/qukaizen-queuellm)
- Model primers in [lab/pkb/sources/seeds/model-building/](../sources/seeds/model-building/)
- The existing [evaluate-llm](../skills/evaluate-llm/SKILL.md)
  and [falsify-hypothesis](../skills/falsify-hypothesis/SKILL.md)
  skills
"""


_RESEARCH_PREPARE_PY = '''"""prepare.py — validation substrate for the QueueLLM research goal.

This is the cheat-proof side of the research contract: the
researcher agent CANNOT modify this file. If it wants a better
throughput number, it has to write faster QueueLLM code, not
redefine what "good" means.

See program.md for the natural-language side.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

RANDOM_SEED = int(os.getenv("LAB_RESEARCH_SEED", "1337"))
DATA_DIR = Path(os.getenv("LAB_RESEARCH_DATA", "lab/research/data"))


def prepare_environment() -> Dict[str, Any]:
    """Return the evaluation environment. Idempotent."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return {
        "data_dir": DATA_DIR,
        "primary_metric": "tokens_per_minute",
        "secondary_metric": "quality_delta_vs_fp16",
        "metric_docstring": throughput_metric.__doc__,
        "random_seed": RANDOM_SEED,
        "min_runs": 3,
        "min_prompts_per_run": 10,
    }


def throughput_metric(model_name: str, duration_sec: float,
                      tokens_generated: int) -> float:
    """Measure inference throughput in tokens per minute.

    Higher is better. Captured automatically by the portal for every
    deep-model call; aggregates live at /api/aerollm/bench.
    """
    if duration_sec <= 0:
        return 0.0
    return (tokens_generated / duration_sec) * 60.0


def quality_delta_vs_fp16(before: float, after: float) -> float:
    """Absolute delta in validation score between FP16 baseline
    and the candidate quantization.

    Positive = worse (quality lost). Under 0.5% is noise; over 2%
    is user-visible. Anything in between is a judgment call for the
    research cycle.
    """
    return abs(before - after)


def check_guardrails(submission: Dict[str, Any]) -> Optional[str]:
    """Veto submissions that break the research contract."""
    # Must include at least 3 separate measurement runs.
    runs = submission.get("runs") or []
    if len(runs) < 3:
        return "need at least 3 separate runs to claim a throughput gain"
    # Must include FP16 baseline for comparison.
    if not submission.get("fp16_baseline"):
        return "missing FP16 baseline — can't measure quality delta"
    # Must pass quality gate.
    delta = submission.get("quality_delta_pct", 100)
    if delta > 0.5:
        return f"quality delta {delta}% exceeds 0.5% ship threshold"
    return None


if __name__ == "__main__":
    meta = prepare_environment()
    print(f"Primary metric: {meta['primary_metric']}")
    print(f"Secondary:      {meta['secondary_metric']}")
    print(f"Min runs:       {meta['min_runs']}")
'''


def ensure_research_files(pkb_root: Path | None = None) -> dict:
    """Materialize lab/pkb/research/ with the AeroLLM research plan.

    Idempotent — writes only when files are missing so user edits
    survive subsequent boots. Users who want to swap the goal can
    either edit program.md directly or point the researcher at a
    different goal via the dashboard.
    """
    root = pkb_root or _pkb_root()
    research_dir = root / "research"
    research_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    program = research_dir / "program.md"
    if not program.exists():
        program.write_text(_RESEARCH_PROGRAM_MD)
        written.append("program.md")
    prepare = research_dir / "prepare.py"
    if not prepare.exists():
        prepare.write_text(_RESEARCH_PREPARE_PY)
        written.append("prepare.py")
    return {"ok": True, "written": written}


def ensure_buddy_folder(pkb_root: Path | None = None) -> dict:
    """Materialize lab/pkb/agents/buddy/ if missing.

    Idempotent: if the folder exists AND ``buddy.py`` is present
    inside, do nothing. Otherwise write all five files (AGENT.md,
    buddy.py copy, decisions.md, dreams/.gitkeep, and the agents/
    README).

    On first boot after the Pip → Buddy rebrand, migrate any legacy
    ``lab/pkb/agents/pip/`` folder into place so existing users keep
    their dreams + decisions. The migration only triggers when no
    ``buddy/`` folder exists yet — if both are present, the legacy
    folder is left alone for manual cleanup.

    Returns a small summary dict describing what happened — useful for
    the startup activity-log line.
    """
    root = pkb_root or _pkb_root()
    agents_dir = root / "agents"
    buddy_dir = agents_dir / "buddy"
    legacy_pip_dir = agents_dir / "pip"

    # The PKB's agents/ directory already exists for output dirs. Drop
    # the README next to them (write once; don't clobber user edits).
    agents_dir.mkdir(parents=True, exist_ok=True)
    readme = agents_dir / "README.md"
    wrote_readme = False
    if not readme.exists():
        readme.write_text(_AGENTS_README)
        wrote_readme = True

    # One-shot migration: rename the legacy pip/ folder to buddy/ on
    # the first boot after the rebrand, preserving dreams/ + decisions.
    migrated = False
    if legacy_pip_dir.exists() and not buddy_dir.exists():
        try:
            legacy_pip_dir.rename(buddy_dir)
            migrated = True
        except OSError as e:
            log.warning("Pip → Buddy folder migration failed: %s", e)
        # Drop the stale pip.py — Buddy gets a fresh copy below.
        old_py = buddy_dir / "pip.py"
        if old_py.exists():
            try:
                old_py.unlink()
            except OSError:
                pass

    # Buddy folder — short-circuit when it's already set up. User may
    # have intentionally edited or deleted individual files; we don't
    # overwrite their choices on subsequent boots.
    buddy_py = buddy_dir / "buddy.py"
    if buddy_py.exists():
        return {
            "ok": True, "created": False,
            "readme": wrote_readme, "migrated": migrated,
        }

    buddy_dir.mkdir(parents=True, exist_ok=True)
    (buddy_dir / "dreams").mkdir(exist_ok=True)

    # Write a thin re-export shim instead of copying the full canonical body.
    # The shim imports everything from arail.agents._builtin_buddy so a single
    # edit to the canonical file lands everywhere without per-workstation drift.
    # Users who want to fork Buddy should replace this file with a full body
    # (copy _builtin_buddy.py as a starting point).
    buddy_py.write_text(_BUDDY_PKB_SHIM, encoding="utf-8")

    # If we migrated from pip/, the AGENT.md and decisions.md still
    # reference the old name. Overwrite AGENT.md unconditionally so
    # the new frontmatter (auto_start_env: LAB_BUDDY) takes effect;
    # leave decisions.md alone if it exists so historical notes stick
    # around — only seed it on a clean install.
    (buddy_dir / "AGENT.md").write_text(_AGENT_MD)
    decisions_path = buddy_dir / "decisions.md"
    if not decisions_path.exists():
        decisions_path.write_text(_DECISIONS_MD)

    # .gitkeep so dreams/ shows up in trees even when empty.
    gitkeep = buddy_dir / "dreams" / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.write_text("")

    return {
        "ok": True,
        "created": True,
        "readme": wrote_readme,
        "migrated": migrated,
        "path": str(buddy_dir),
    }


# ── SRE Watch templates ──────────────────────────────────────────────
_SRE_AGENT_MD = """---
title: SRE Watch — Crash Monitor
name: SRE
emoji: 🔥
section: agents/sre
tags: [agent, sre, monitoring, builtin]
voice: "Terse incident reporter. One sentence, clinical precision. No emojis. State error type, location, count."
tick_interval_sec: 120
global_cooldown_sec: 180
dream: false
auto_start_env: LAB_SRE
skills: []
---

# SRE Watch — the crash monitor

SRE Watch is a reliability agent. It reads `lab/data/activity.jsonl`
on every tick and surfaces errors and crash recurrences in the
activity feed — so you notice them without having to tail a log file.

## What SRE Watch monitors

| Watcher | Severity | Cooldown | Fires when |
|---|---|---|---|
| `recent-errors` | warn | 10 min per fingerprint | A new error/warn pattern appeared in the last 5 min |
| `crash-recurrence` | warn | 15 min per fingerprint | Same error pattern hit 3+ times in 30 min |
| `service-health` | warn | 10 min | Portal `/api/jobs/state` is unreachable |

## Editing SRE Watch

- Add new watchers → add a function to `WATCHERS` in `sre.py`.
- Tune cooldowns → env vars `LAB_SRE_INTERVAL_SEC`, `LAB_SRE_COOLDOWN_SEC`.
- Mute → set `LAB_SRE=off` in `.env`.
- Wipe memory → delete `state.json`.

## Companion files

- [sre.py](sre.py) — watchers + loop
- [state.json](state.json) — persisted fingerprint memory + cooldowns
"""


def ensure_sre_folder(pkb_root: Path | None = None) -> dict:
    """Materialize lab/pkb/agents/sre/ if missing.

    Idempotent: if the folder exists AND ``sre.py`` is present inside,
    do nothing. Otherwise write AGENT.md and a thin re-export shim
    pointing at arail.agents._builtin_sre. Users who want to fork SRE
    should replace this file with a full body (copy _builtin_sre.py as
    a starting point).
    """
    root = pkb_root or _pkb_root()
    sre_dir = root / "agents" / "sre"

    sre_py = sre_dir / "sre.py"
    if sre_py.exists():
        return {"ok": True, "created": False}

    sre_dir.mkdir(parents=True, exist_ok=True)

    sre_py.write_text(_SRE_PKB_SHIM, encoding="utf-8")
    (sre_dir / "AGENT.md").write_text(_SRE_AGENT_MD)

    return {
        "ok": True,
        "created": True,
        "path": str(sre_dir),
    }


# ── Drafter templates ────────────────────────────────────────────────
_DRAFTER_AGENT_MD = """---
title: Drafter — composition agent
name: Drafter
emoji: ✍️
section: agents/drafter
tags: [agent, drafter, composition, builtin]
voice: "Compose drafts in the user's voice. Concise. Never auto-send."
# Drafter is invoked synchronously by blueprints (inbox-triager,
# client-followup) — not a heartbeat agent like Pip. No tick loop.
tick_interval_sec: 0
global_cooldown_sec: 0
dream: false
auto_start_env: ""
skills: []
---

# Drafter — composition agent

Drafter takes a context (email thread, meeting notes, etc.) plus
an intent ("reply professionally", "follow up on Tuesday's
meeting") and produces a draft. **It never sends.** Sending is
gated by the [`consent`](../consent/AGENT.md) agent — Drafter
returns a `Draft` whose `requires_consent` flag is always `True`.

## Used by

- [`inbox-triager`](../../../../blueprints/inbox-triager/) — drafts
  email replies after the (aspirational) `triager` agent classifies
  incoming messages
- [`client-followup`](../../../../blueprints/client-followup/) —
  drafts post-meeting follow-ups using context the `researcher`
  agent gathered

## API

```python
from arail.agents.loader import load_one
drafter = load_one("drafter")

result = drafter.compose(
    context="Hey, can you send the deck before Friday?",
    intent="reply professionally; confirm timing",
    voice="default",
    max_tokens=400,
)
# result.text             — the draft string
# result.requires_consent — always True
```

## Companion files

- [drafter.py](drafter.py) — agent class + composition logic
- (no state.json — Drafter is request-driven, no persisted memory)
"""


def ensure_drafter_folder(pkb_root: Path | None = None) -> dict:
    """Materialize lab/pkb/agents/drafter/ if missing.

    Idempotent: if the folder exists AND ``drafter.py`` is present
    inside, do nothing. Otherwise write AGENT.md and copy
    _builtin_drafter.py.
    """
    root = pkb_root or _pkb_root()
    drafter_dir = root / "agents" / "drafter"

    drafter_py = drafter_dir / "drafter.py"
    if drafter_py.exists():
        return {"ok": True, "created": False}

    drafter_dir.mkdir(parents=True, exist_ok=True)

    builtin = Path(__file__).parent / "_builtin_drafter.py"
    shutil.copy(builtin, drafter_py)
    (drafter_dir / "AGENT.md").write_text(_DRAFTER_AGENT_MD)

    return {
        "ok": True,
        "created": True,
        "path": str(drafter_dir),
    }


# ── Presence templates ───────────────────────────────────────────────
_PRESENCE_AGENT_MD = """---
title: Presence — runtime profile observer
name: Presence
emoji: 📡
section: agents/presence
tags: [agent, observer, runtime-profile, builtin]
voice: ""
tick_interval_sec: 60
auto_start_env: LAB_PRESENCE_AGENT
dream: false
---

# Presence — runtime profile observer

Presence is a silent observer agent. It watches the resolved runtime
profile (`arail.runtime_profile.resolve()`) every `tick_interval_sec`
seconds and emits an `activity_log` event whenever the profile
transitions. It does NOT speak — Buddy handles narration if/when we
add a `presence-buddy` companion.

## What it watches

| Signal | Source |
|---|---|
| Profile transitions | `runtime_profile.resolve()` returns a different `(profile, source)` than last tick |

Each transition emits one event with `source="profile"` and a `data`
payload that mirrors `runtime_profile.snapshot()`. The dashboard SSE
handler uses `source == "profile"` to update the pill.

## Rules of engagement

- **Silent.** No utterances; only `activity_log.emit(...)`.
- **One event per transition.** Steady-state ticks don't emit.
- **Never blocks.** Resolver call is O(1); the agent thread is a daemon.

## Editing

- Tune cadence → `LAB_PRESENCE_AGENT_INTERVAL_SEC` in `.env`.
- Disable → `LAB_PRESENCE_AGENT=off`.

## Companion files

- [presence.py](presence.py) — the body
- [state.json](state.json) — last-seen profile snapshot (optional)
"""


def ensure_presence_folder(pkb_root: Path | None = None) -> dict:
    """Materialize lab/pkb/agents/presence/ if missing.

    Idempotent: if the folder exists AND ``presence.py`` is present
    inside, do nothing. Otherwise write AGENT.md and copy
    _builtin_presence.py.
    """
    root = pkb_root or _pkb_root()
    presence_dir = root / "agents" / "presence"

    presence_py = presence_dir / "presence.py"
    if presence_py.exists():
        return {"ok": True, "created": False}

    presence_dir.mkdir(parents=True, exist_ok=True)

    builtin = Path(__file__).parent / "_builtin_presence.py"
    shutil.copy(builtin, presence_py)
    (presence_dir / "AGENT.md").write_text(_PRESENCE_AGENT_MD)

    return {
        "ok": True,
        "created": True,
        "path": str(presence_dir),
    }


# ── Librarian agent seed ───────────────────────────────────────────

_LIBRARIAN_AGENT_MD = """---
title: Librarian — compiled-knowledge curator
name: Librarian
emoji: 📚
section: agents/librarian
tags: [agent, librarian, dac, worlds, builtin]
voice: "Measured curator. Speaks in provenance: what is sourced, what is dreamed, what awaits review."
tick_interval_sec: 1800
dream: true
auto_start_env: LAB_LIBRARIAN
skills: []
---

# Librarian — the compiled-knowledge curator

The Librarian owns the lab's DaC lifecycle: it grows the mounted World
overnight, scouts the lab's own signals for terms the World is missing,
and keeps the DaC tab's focus panel honest about what the compiled
knowledge contains.

## What the Librarian does

| Duty | Cadence | Gate |
|---|---|---|
| Overnight growth pass | heavy window, once/day, local brain only | every term re-gated + resealed |
| Term scouting | every `ARAIL_LIBRARIAN_SCAN_HOURS` (default 6h) | proposals only — a human approves |
| Focus panel status | on demand (`/api/librarian/status`) | read-only |

## The three laws it never breaks

1. Every term goes through the gate (`assert_closed_sourced_graph`).
2. Provenance stays honest — locally-drafted terms are labeled
   *model-asserted* ("dreamed"); only a real source earns *sourced*.
3. Nothing compiles into the sealed World without your approval.

## Tuning

- `LAB_LIBRARIAN=off` — don't auto-start.
- `LAB_LIBRARIAN_INTERVAL_SEC` — loop cadence (default 1800).
- `ARAIL_LIBRARIAN_SCAN_HOURS` — scout cadence (default 6).
- `ARAIL_LIBRARIAN_UBIQUITY` — mentions needed before a candidate
  ripens into a proposal (default 4; 2 distinct signal kinds also ripen).
- `ARAIL_WORLD_GROWTH=off` — disable the overnight growth pass.

## Companion files

- [librarian.py](librarian.py) — the agent loop
- `dreams/` — nightly reflections
"""


def ensure_librarian_folder(pkb_root: Path | None = None) -> dict:
    """Materialize lab/pkb/agents/librarian/ if missing.

    Idempotent: if ``librarian.py`` exists inside, do nothing. Otherwise
    write AGENT.md and copy _builtin_librarian.py.
    """
    root = pkb_root or _pkb_root()
    lib_dir = root / "agents" / "librarian"

    lib_py = lib_dir / "librarian.py"
    if lib_py.exists():
        return {"ok": True, "created": False}

    lib_dir.mkdir(parents=True, exist_ok=True)

    builtin = Path(__file__).parent / "_builtin_librarian.py"
    shutil.copy(builtin, lib_py)
    (lib_dir / "AGENT.md").write_text(_LIBRARIAN_AGENT_MD)

    return {
        "ok": True,
        "created": True,
        "path": str(lib_dir),
    }


# ── Debt Advisor / Consolidation Analyzer templates ───────────────────
# Frontmatter shapes verbatim per
# sprints/2026-07-26-world-of-debt-finance/ARCHITECTURE.md §5.1/§5.2. Both
# agents read the mounted debt-finance World's terms and (Consolidation
# Analyzer only) the operator's own staged balances — never anything under
# lab/pkb/ for either input or output. See src/arail/agents/
# _builtin_debt_advisor.py / _builtin_consolidation_analyzer.py for the
# canonical implementation and src/arail/agents/debt_finance_compliance.py
# for the shared guardrail/disclaimer logic both read.

_DEBT_ADVISOR_AGENT_MD = """---
name: Debt Advisor
emoji: \U0001F9ED
voice: plain, calm, cites sources, never prescriptive
tick_interval_sec: 86400
global_cooldown_sec: 3600
auto_start_env: LAB_DEBT_ADVISOR
skills: [debt-strategy-summary, cite-approved-findings]
---
Reads the mounted debt-finance World's terms and any *approved* scouting
findings (via search_for_agents / the Compiled-KB gate). Any specific rate,
fee, or institution name that appears in output is substituted in by code
from the term's or finding's structured fields — never freely generated by
this model. This agent's own generation is used only for framing and
explanatory prose around those code-inserted facts, never for the facts
themselves. Distinguishes vetted institutions ("credit union," "nonprofit
lender" — only for terms carrying a verification source, §3.3) from
unverified scouting findings ("found via [feed]: [lender] advertises
[rate]" — no institutional-character label; this labeling is also
mechanically enforced downstream, so an accidental slip here is caught
before anything is written). Writes to
lab/data/user-import/debt-finance/findings/debt_advisor.md — never to
decisions.md, never to any path under lab/pkb/. Every note the code emits
ends with the canonical disclaimer read fresh from compliance/DISCLAIMER.md.

## Companion files

- [debt_advisor.py](debt_advisor.py) — the body (facts + guardrail + loop)
- [state.json](state.json) — persisted memory: a content hash, a
  timestamp, and a count — **never** a raw balance, APR, or institution
  name (that would leak exactly what this World is designed to keep out of
  the indexed PKB tree)
- [decisions.md](decisions.md) — append-only, human-authored config log
  only; never a channel for this agent's own financial analysis

## Editing Debt Advisor

- Mute → set `LAB_DEBT_ADVISOR=off` in `.env`.
- Wipe memory → delete `state.json` (this only forces a re-run, since the
  file never held any sensitive content).
- Findings live in `lab/data/user-import/debt-finance/findings/` — open
  directly, or use the "Reveal findings" button on the mounted World's card
  at `/worlds`.
"""

_DEBT_ADVISOR_PKB_SHIM = '''"""Debt Advisor — PKB shim.

This file is auto-generated by builtin_seed.ensure_debt_advisor_folder. The
canonical implementation lives in the installed package at
``src/arail/agents/_builtin_debt_advisor.py``. The shim re-exports the
``debt_advisor`` singleton so the loader finds an importable agent here
without us shipping a forked copy that drifts.

To fork Debt Advisor, replace this file with a full module body — copy
``_builtin_debt_advisor.py`` as a starting point. Read
``debt_finance_compliance.py`` first: any fork MUST keep the disclaimer
precondition check and the language-safety guardrail, or it can produce
unlicensed-advice-shaped output.

DO NOT EDIT THIS SHIM. It will be regenerated if missing.
"""

from arail.agents._builtin_debt_advisor import debt_advisor  # noqa: F401
'''

_DEBT_ADVISOR_PKB_SHIM_SENTINEL = '"""Debt Advisor — PKB shim."""'


def ensure_debt_advisor_folder(pkb_root: Path | None = None) -> dict:
    """Materialize lab/pkb/agents/debt_advisor/ if missing.

    Idempotent: if ``debt_advisor.py`` exists inside, do nothing. Otherwise
    write AGENT.md and a thin re-export shim pointing at
    arail.agents._builtin_debt_advisor.
    """
    root = pkb_root or _pkb_root()
    agent_dir = root / "agents" / "debt_advisor"

    agent_py = agent_dir / "debt_advisor.py"
    if agent_py.exists():
        return {"ok": True, "created": False}

    agent_dir.mkdir(parents=True, exist_ok=True)
    agent_py.write_text(_DEBT_ADVISOR_PKB_SHIM, encoding="utf-8")
    (agent_dir / "AGENT.md").write_text(_DEBT_ADVISOR_AGENT_MD)

    return {"ok": True, "created": True, "path": str(agent_dir)}


_CONSOLIDATION_ANALYZER_AGENT_MD = """---
name: Consolidation Analyzer
emoji: \U0001F9EE
voice: numeric, shows its work
tick_interval_sec: 86400
global_cooldown_sec: 3600
auto_start_env: LAB_CONSOLIDATION_ANALYZER
skills: [blended-apr-calc, breakeven-calc]
---
Reads the operator's own loan/card balances and APRs from
lab/data/user-import/debt-finance/ (never lab/pkb/) and computes,
deterministically in code: current blended APR, monthly interest cost, and
break-even timelines for candidate balance-transfer or consolidation-loan
scenarios entered in the same staging file. Every number is computed by
code from the operator's own staged data, or it does not exist — no
invented or assumed rates, and no number is ever paraphrased by this model
before it's written; the model narrates around code-inserted numbers, it
does not retype them. A candidate scenario's rate is either the operator's
own hand-entered figure or an approved scouting finding's rate, cited with
its approval date; it is never presented as a live, current quote. Because
this agent's findings file lives outside lab/pkb/ and is never indexed or
searched, it can show its full inputs alongside the math without creating
the privacy exposure that would exist if this content were written
anywhere under lab/pkb/ — full transparency and full privacy are both
satisfiable here, precisely because the file's location makes them not
compete with each other. Writes to
lab/data/user-import/debt-finance/findings/consolidation_analyzer.md. Every
note ends with the canonical disclaimer read fresh from
compliance/DISCLAIMER.md.

## Companion files

- [consolidation_analyzer.py](consolidation_analyzer.py) — the body
  (arithmetic + guardrail + loop)
- [state.json](state.json) — persisted memory: a content hash, a
  timestamp, and a count — **never** a raw balance, APR, or institution
  name
- [decisions.md](decisions.md) — append-only, human-authored config log
  only; never a channel for this agent's own financial analysis

## Editing Consolidation Analyzer

- Mute → set `LAB_CONSOLIDATION_ANALYZER=off` in `.env`.
- Wipe memory → delete `state.json` (only forces a re-run).
- Input lives at `lab/data/user-import/debt-finance/balances.json` — hand
  author it per the schema in ARCHITECTURE.md §6.1.
- Findings live in `lab/data/user-import/debt-finance/findings/` — open
  directly, or use the "Reveal findings" button on the mounted World's card
  at `/worlds`.
"""

_CONSOLIDATION_ANALYZER_PKB_SHIM = '''"""Consolidation Analyzer — PKB shim.

This file is auto-generated by
builtin_seed.ensure_consolidation_analyzer_folder. The canonical
implementation lives in the installed package at
``src/arail/agents/_builtin_consolidation_analyzer.py``. The shim
re-exports the ``consolidation_analyzer`` singleton so the loader finds an
importable agent here without us shipping a forked copy that drifts.

To fork Consolidation Analyzer, replace this file with a full module body —
copy ``_builtin_consolidation_analyzer.py`` as a starting point. Read
``debt_finance_compliance.py`` first: any fork MUST keep the disclaimer
precondition check and the language-safety guardrail.

DO NOT EDIT THIS SHIM. It will be regenerated if missing.
"""

from arail.agents._builtin_consolidation_analyzer import (  # noqa: F401
    consolidation_analyzer,
)
'''

_CONSOLIDATION_ANALYZER_PKB_SHIM_SENTINEL = (
    '"""Consolidation Analyzer — PKB shim."""'
)


def ensure_consolidation_analyzer_folder(pkb_root: Path | None = None) -> dict:
    """Materialize lab/pkb/agents/consolidation_analyzer/ if missing.

    Idempotent: if ``consolidation_analyzer.py`` exists inside, do nothing.
    Otherwise write AGENT.md and a thin re-export shim pointing at
    arail.agents._builtin_consolidation_analyzer.
    """
    root = pkb_root or _pkb_root()
    agent_dir = root / "agents" / "consolidation_analyzer"

    agent_py = agent_dir / "consolidation_analyzer.py"
    if agent_py.exists():
        return {"ok": True, "created": False}

    agent_dir.mkdir(parents=True, exist_ok=True)
    agent_py.write_text(_CONSOLIDATION_ANALYZER_PKB_SHIM, encoding="utf-8")
    (agent_dir / "AGENT.md").write_text(_CONSOLIDATION_ANALYZER_AGENT_MD)

    return {"ok": True, "created": True, "path": str(agent_dir)}
