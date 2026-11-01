---
title: Agents, Explained
description: "The on-ramp to understanding how agents work in this lab — what they do, how they talk to each other, and how to create your own."
category: Concepts
order: 5
tags:
  - agents
  - buddy
  - getting-started
audience: beginner
related:
  - agents
  - BUDDY
  - agent-loop
buddy_prompt: "Walk me through what an agent loop does, using the lab I'm in as the example."
---
# Agents, explained

> **See also:** [The lab, end-to-end](the-lab.md) — the 12-minute runbook
> tour. Agents are one of five surfaces; the runbook puts them in context.

This is the on-ramp. If you want the reference manual, see
[docs/agents.md](agents.md). If you want to *understand* what an
agent actually is in about five minutes, start here.

## The one-sentence version

**An agent is a loop that notices things and speaks up.**

That's it. Everything else — personalities, skills, dreams, folders,
goal-aware suggestions — is just detail hanging off that sentence.

## The five pieces

Every agent has exactly five parts. Once you see them, the code
stops being magic.

| Piece | What it is | In Buddy |
|---|---|---|
| **Personality** | A few sentences of voice + rules | "Warm, observant, actively helpful lab partner. One short sentence." |
| **Watchers** | Tiny functions that look at the lab | "Is RAM over 92%?", "Is the inbox piling up?" |
| **Memory** | What the agent remembers | Cooldowns on disk, nightly dreams in markdown |
| **Skills** | Procedural knowledge, in markdown | `observe-lab` — how to phrase, what's worth saying |
| **Voice** | One LLM call that turns facts into a sentence | `router.complete(prompt)` |

Buddy adds one piece on top of the canonical five — **suggesters**:
goal-anchored proposers that fire on a slower cadence than watchers
and only when a goal is set. They share the same personality + voice
plumbing; they just propose actions instead of reporting state.

Everything else is plumbing around these five-plus-one. The folder
structure, the loader, the dream daemon — they're all there to make
sure these pieces run reliably and stay editable.

## One tick, traced end to end

Pretend you're Buddy. Every 90 seconds, you wake up. Here's what
happens:

```text
  ┌──────────────────────────────────────────────────────┐
  │  1. Am I allowed to speak?                           │
  │     └─ Check the global cooldown (5 min minimum      │
  │        between utterances). If too soon, go back     │
  │        to sleep for 90s.                             │
  │                                                      │
  │  2. What's going on in the lab?                      │
  │     └─ Run all watchers (GPU, inbox, researcher      │
  │        wins, plateau, goal staleness, study          │
  │        streak, airgap events). Each returns a        │
  │        fact or None.                                 │
  │                                                      │
  │  3. Anything worth saying?                           │
  │     └─ Filter out facts I already said recently      │
  │        (per-watcher cooldowns). If nothing's left,   │
  │        stay quiet.                                   │
  │                                                      │
  │  4. Pick the juiciest one.                           │
  │     └─ Praise beats warn beats info beats suggest.   │
  │                                                      │
  │  5. Phrase it.                                       │
  │     └─ Build an LLM prompt:                          │
  │         • personality (SYSTEM_PROMPT)                │
  │         • yesterday's dream (if any)                 │
  │         • skills (observe-lab's body)                │
  │         • the raw fact                               │
  │        Call router.complete(). Get one sentence.    │
  │                                                      │
  │  6. Say it.                                          │
  │     └─ activity_log.emit("buddy", sentence) → SSE →  │
  │        every dashboard subscriber lights up.         │
  │                                                      │
  │  7. Remember.                                        │
  │     └─ Update cooldowns. Write state.json. Sleep.    │
  │                                                      │
  │  8. (Every 15 min) Anything to suggest?              │
  │     └─ Only if a goal is set: poll SUGGESTERS for    │
  │        a phase nudge / review / skill / experiment   │
  │        proposal, paraphrase, emit. Same cooldown     │
  │        bookkeeping as the reactive cadence.          │
  └──────────────────────────────────────────────────────┘
```

That whole cycle is ~80 lines of Python. Read
[`_builtin_buddy.py`](../src/arail/agents/_builtin_buddy.py) and
you'll recognize every step.

## Where everything lives

The five pieces map cleanly onto files in one folder:

```text
lab/pkb/agents/buddy/
├── AGENT.md        personality, rules, skill list, opt-in toggles
├── buddy.py        thin shim that re-exports from the canonical body
├── state.json      memory: cooldowns, counts (auto-saved)
├── decisions.md    "why I changed X" — human-authored
└── dreams/
    ├── 2026-05-04.md   last night's reflection
    └── 2026-05-03.md   the night before
```

The separation is intentional:

- **Edit AGENT.md** to change voice, intervals, which skills are active.
- **Add or remove watchers/suggesters** in
  [`src/arail/agents/_builtin_buddy.py`](../src/arail/agents/_builtin_buddy.py).
  That file is the canonical body; the PKB `buddy.py` is a shim
  re-exporting from it. Editing the PKB shim has no effect — it just
  re-points readers at the canonical. Fork your own version by
  replacing the shim's contents with your own watchers.
- **Don't touch state.json** — it's memory, not config.
- **Write to decisions.md** when you make a big change so the agent
  (and future-you) knows what happened and why.
- **Read dreams/** when you want to know what the agent was thinking.

And for skills:

```text
lab/pkb/skills/observe-lab/
└── SKILL.md        procedural knowledge the agent loads into its prompt
```

Skills live next door. An agent composes its voice from **its own
personality + any skills it has loaded**. Edit a skill's markdown,
the next tick uses the new version. No restart.

## The hello-world agent

Strip away the personality, the skills, the dreams. Here's an agent
that does exactly one useful thing — say "hello" once a minute:

```python
# lab/pkb/agents/hello/hello.py
import asyncio
from arail.activity import activity_log


class HelloAgent:
    def __init__(self):
        self._task = None
        self.status = "idle"

    def start(self):
        self.status = "running"
        self._task = asyncio.create_task(self._tick())

    async def _tick(self):
        while True:
            await asyncio.sleep(60)
            activity_log.emit("hello", "👋 hi there", "info")


hello = HelloAgent()  # <-- must match the folder name
```

With a two-line `AGENT.md` next to it:

```yaml
---
name: Hello
auto_start_env: LAB_HELLO
---

# Hello — minimum viable agent
```

Restart the portal. Now `/agents` has a fourth activity source
called `hello`, saying "👋 hi there" every minute. That's the whole
mechanism.

**From here, everything else is just layers:**

- Add **watchers** (like Buddy does) so it speaks only when something
  interesting happens.
- Add a **personality** to shape what "hi there" becomes.
- Add **skills** to give it domain expertise it can consult.
- Add **suggesters** to propose actions when a goal is set.
- Add **state.json** to remember cooldowns across restarts.
- Add **dream()** to reflect nightly.

Each layer is optional. You can ship a useful agent with just the
hello-world skeleton above.

## When one agent isn't enough

Buddy is one loop. Useful, but not how you'd plan a trip to Japan.

Real goals usually want a small team. A travel goal might want one agent
sweating the seasonality, another scoping routes, another shortlisting
hotels, another modeling the budget envelope. A research goal wants a
literature scout, a measurement designer, a variants planner. The same
goal-shaped lab supports both because the *agents* don't change — only
the **roster** that gets compiled around the goal does.

### How a goal becomes a roster

When you set a goal in the portal,
[`swarm_goals.compile_swarm_plan()`](../src/arail/swarm_goals.py)
runs first. It does three things:

1. **Detect the archetype** — `travel`, `research`, `operations`, or
   `general` — by scanning the goal text for keywords. "Tokyo" + "rail"
   + "lodging" lands you in `travel`.
2. **Pick the worker roster** for that archetype. The roster is a list
   of dicts with an `id`, `role`, `purpose`, `deliverable`, and a
   `depends_on` list. The lead is always the `researcher` agent;
   workers are the lead's lanes.
3. **Lay out phases** so workers run in dependency order — Scout maps
   the option space, Critic stress-tests assumptions, then the
   archetype-specific lanes light up.

For a travel goal the compiled roster is exactly:

| Worker | Role | Depends on |
|---|---|---|
| **Scout** | Map the search space | — |
| **Critic** | Stress-test assumptions | scout |
| **Seasonality** | Model travel windows + crowd pressure + weather | scout |
| **Routing** | Plan flights, rail, transfers | scout |
| **Lodging** | Shortlist neighborhoods + stay types | seasonality, routing |
| **Budget** | Frame price-vs-comfort tradeoffs | routing, lodging |

That's the **N+1**: one **Lead Researcher** orchestrating + N
specialized workers per archetype. Default scale is `balanced` (4
workers); set `ARAIL_SWARM_SCALE=expanded` for 6 or `compact` for 3.
The plan is reviewable before run — operators can disable workers
they don't want.

The `research` archetype gets Literature, Eval, Variants, Synthesizer.
The `operations` archetype gets Signals, Runbooks, Capacity, Reviewer.
The `general` archetype gets Mapper, Evaluator, Synthesizer. None of
this is hard-coded into the agent loader — it's a roster description
the lead consumes. To add an archetype (cooking, parenting, code
migration), edit `_ARCHETYPE_WORKERS` in `swarm_goals.py`.

### How agents communicate

Agents don't call each other directly. There's no RPC, no message bus,
no orchestrator-as-process. They share three surfaces, and that's
enough:

1. **`agent_workflows.json`** — every agent persists its state row
   (`status`, `objective`, `current_task`, `next_step`,
   `completed_steps`, `pause_reason`, `chatter`) via
   [`update_agent_workflow()`](../src/arail/agent_workflows.py).
   Any agent can read everyone's row. Buddy reads this to know what
   the Researcher is up to without asking.
2. **`activity_log.emit(source, message, level, data)`** — a broadcast
   SSE channel. Whatever an agent says shows up on every dashboard
   subscriber AND in the JSON event log. A second agent can watch the
   log and react to what the first one said. This is how Buddy's
   "researcher wins" watcher fires — it sees the Researcher emit a
   "win" event and decides whether to praise.
3. **PKB / LanceDB** — shared semantic memory. One agent writes a
   note into the knowledge base; another agent's vector search finds
   it on the next tick. The compiled swarm plan literally pins
   `"shared_collections": ["agent_workflows", "pkb"]` as the team's
   coordination substrate.

Three channels, three latencies: workflow rows are *current state*,
the activity log is *events as they happen*, and the PKB is
*durable knowledge*. A worker that wants to influence another
worker writes into whichever surface fits the half-life of what it's
saying.

### What "lane" means today vs. "process" tomorrow

Today the workers are **lanes inside the Lead Researcher's loop** —
the lead drives them through phases, syncs each lane's status into
`agent_workflows.json`, and the dashboard surfaces them as if they
were independent agents. The visual is multi-agent; the runtime is
one agent juggling lanes.

That's a deliberate choice and not a forever choice. The architecture
already speaks the multi-agent dialect (one row per lane in
workflows, one source per lane in the activity log). Splitting a lane
into its own `lab/pkb/agents/<id>/` folder with its own loop is the
same shape the hello-world skeleton above shows — drop the files in,
restart, the loader finds it. Workers that want their own
personality, dream, or cadence graduate to real agents the same way
Buddy did.

That's the path: write the goal, watch the lanes, promote a lane to
its own agent when it earns it.

## Why it works this way

Three design choices, each intentional:

### 1. Agents live in markdown + Python, not a database

Everything about an agent — config, code, memory, decisions, dreams
— is a plain file under the PKB. The wiki indexes them, `/dac`
browses them, `git` tracks them if you want, `./arailctl reset pkb`
wipes them cleanly. No hidden state, no mystery DB rows.

### 2. Agents are Python, not JSON schemas

Claude's API has a tool-calling protocol where the model says "I'd
like to use tool X" and the harness routes it. We don't need that
layer because agents *are* Python — they import `activity_log`,
`pkb.search`, `router.complete` directly. Skills ship procedural
**knowledge** (markdown); tools ship procedural **capability**
(Python). Both are first-class; neither needs a JSON schema.

### 3. One loop, not many tasks

Buddy is one asyncio task that polls a list of watchers (and, on a
slower cadence, a list of suggesters). You could imagine every
watcher being its own task — more "proper" async, more moving parts.
We don't do that. Watchers are cheap, simple functions; running
them in sequence once a minute is fine, and the single-loop
structure is easier to reason about, cancel, and debug.

## The "magic" is three steps of indirection

If agents still feel magical after reading this, it's usually
because three indirections compound:

1. **Dynamic import.** The file at `lab/pkb/agents/buddy/buddy.py`
   gets loaded into Python at runtime by
   [`loader.py`](../src/arail/agents/loader.py) using
   `importlib.util.spec_from_file_location`. The shipped PKB file is a
   small shim that re-exports from
   [`_builtin_buddy.py`](../src/arail/agents/_builtin_buddy.py); replace
   the shim's contents with your own watchers and the loader picks them
   up on next restart. Same `importlib` mechanism, no package
   reinstall, no restart in the middle of a tick.

2. **SSE fan-out.** When Buddy calls `activity_log.emit`, that event
   goes to every dashboard page currently watching the
   `/api/activity/stream` endpoint. The "message appeared on my
   screen" magic is just a server-sent-events subscription — one
   message in, N clients out.

3. **LLM paraphrase.** The step "turn a fact into one sentence" is
   one `router.complete()` call. The prompt is three deterministic
   strings concatenated (personality + skills + fact); the output
   is whatever the local model feels like saying that tick. That's
   where the personality comes alive — but also where debugging
   gets tricky. When Buddy sounds weird, check the prompt first.

That's the whole magic. Everything else is code you can read in
one sitting.

## Go deeper

- [`src/arail/agents/_builtin_buddy.py`](../src/arail/agents/_builtin_buddy.py) —
  the canonical agent. Read this second.
- [`src/arail/agents/loader.py`](../src/arail/agents/loader.py) —
  how the folder structure turns into running code.
- [`src/arail/skills_loader.py`](../src/arail/skills_loader.py) —
  how SKILL.md becomes part of an agent's prompt.
- [`src/arail/agents/dream_daemon.py`](../src/arail/agents/dream_daemon.py) —
  the nightly reflection scheduler.
- [`docs/agents.md`](agents.md) — the full reference (architecture,
  contracts, rationale). Read this when you're ready to ship an agent.

## The research loop

The **Researcher** agent runs the Autoresearch page. Its loop is deliberately
small and honest:

1. **Plan** — turn the goal into a handful of hypotheses (with a visible
   planning trace showing which were considered vs chosen).
2. **Design** — each hypothesis is mapped to a measurable *archetype*:
   - `model_throughput` — measures real TTFT / decode tokens-per-second of the
     locally-resolved model.
   - `prompt_variant` — compares prompt phrasings, scored by deterministic,
     code-computed proxies (format compliance, latency, output consistency) —
     never a model self-score.
   - `retrieval_quality` — measures how well the **approved** Knowledge Base
     answers goal-related probes (needs no model).
3. **Run** — the experiment runs for real on your machine
   (`src/arail/research/mini_experiments.py`). Every number shown was measured,
   or the experiment reports an honest **could-not-run** (e.g. no local model,
   or no approved knowledge) — it never fabricates a result.
4. **Report** — results are written to the Knowledge Base with a **provenance**
   line (`measured` / `NOT RUN` / `unmeasured`). Any model-written narrative is
   labeled *model-narrated*; the metrics themselves are code-measured.

Where the artifacts land: raw records in `lab/data/experiments/`, KB candidates
in `lab/pkb/agents/experiments/` (raw until you promote them through the
Compiled-KB gate), and the report in `lab/pkb/agents/research/`. See
[`docs/models-on-disk.md`](models-on-disk.md).

This is distinct from the **Tuning** page's git-branch-per-experiment loop,
which tunes inference *speed* rather than running research experiments.
