---
title: Agents — Architecture Reference
category: Reference
order: 5
tags:
  - agents
  - architecture
  - reference
audience: operator
related:
  - agents-explained
  - BUDDY
  - api-conventions
  - agent-loop
---
# Agents — Architecture

This is the reference. If you're here to **understand** what an
agent is before diving into contracts and file paths, read
[docs/agents-explained.md](agents-explained.md) first — it's the
friendly walkthrough, ~5 minutes. This file is the manual: every
contract, every tradeoff, every file that matters. Use it when
you're building, debugging, or extending.

The user-facing summary lives at [`lab/pkb/agents/README.md`](../lab/pkb/agents/README.md) —
that's what appears in the DaC tab for non-developers.

## What's an agent?

An **agent** in Arail is a long-running Python object that:

1. Runs as an asyncio task inside the portal process.
2. Observes the lab — GPU state, researcher progress, PKB contents,
   activity stream, goal — via cheap local probes.
3. Decides, on a cadence, whether to *do something* or *say
   something*.
4. Emits events to the activity log (`source="<agent-name>"`) that
   fan out to every SSE subscriber.

Four agents ship today:

| Agent | Purpose | Pattern |
|---|---|---|
| [researcher.py](../src/arail/agents/researcher.py) | Drives a goal: hypotheses → experiments → report | Goal-driven |
| [curator.py](../src/arail/agents/curator.py) | Finds sources for the researcher, gates on consent | Goal-driven |
| [browser.py](../src/arail/agents/browser.py) | Web research via agent-browser, capture to PKB | On-demand |
| [_builtin_buddy.py](../src/arail/agents/_builtin_buddy.py) | Lab buddy — notices things, speaks up | **Personality** |

Buddy is the first "personality" agent — it doesn't drive a goal; it
watches and comments. This document focuses on that pattern because
it's the template for the upcoming Agent Forge.

## The folder shape

Every personality agent is a **folder**, not a single file. The
folder lives under the PKB so the wiki indexes it, `/dac` can
browse it, and `./arailctl reset pkb` wipes it cleanly.

```
lab/pkb/agents/buddy/
├── AGENT.md          root config — voice, skills, intervals
├── buddy.py          the body — watchers + suggesters + loop + speech
├── state.json        persisted memory (cooldowns, utterance count)
├── decisions.md      append-only log of meaningful choices
└── dreams/
    └── YYYY-MM-DD.md nightly reflection (Step 2)
```

Three contracts distinguish an agent folder from the shared output
dirs (`agents/research/`, `agents/experiments/`, …):

- Presence of **`AGENT.md`**. The eventual loader uses this to
  discover agents; folders without it are output, not agents.
- A **Python module** sibling to AGENT.md exporting a `buddy`-style
  singleton (conventionally named after the agent).
- All **persistence** lives inside the folder — `state.json`,
  `dreams/`, decisions — so wiping or moving an agent is one
  directory operation.

## Why a folder, not a file?

Five reasons:

1. **Inspectable.** A new user reading `/dac/agents/buddy/` sees
   everything the agent is in one tree. No hidden state elsewhere.
2. **Editable without redeploying.** The user-visible `buddy.py` is
   the file the portal actually runs (via dynamic import). Edit it
   from the DaC tab, restart, your changes take effect.
3. **Wiki-indexed.** AGENT.md, decisions.md, and every dream entry
   appear in the wiki automatically. Backlinks work. Search covers
   them.
4. **Reset-friendly.** `./arailctl reset pkb` wipes the folder. Next
   `./arailctl start` re-seeds it from the builtin copy. No manual
   rebuild.
5. **Forge-ready.** The Agent Forge (Step 5) generates one of these
   folders per user-created agent. Keeping the shape uniform means
   nothing is special about the shipped agents — they're just the
   ones seeded on first boot.

## The two-copy pattern (shipped vs. user-editable)

To reconcile "canonical source in the installed package" with
"user edits survive updates," every shipped agent has two copies:

| File | Role |
|---|---|
| [src/arail/agents/_builtin_buddy.py](../src/arail/agents/_builtin_buddy.py) | **Canonical source.** What ships with the release. Read-only by convention (leading underscore). Also used as the fallback if the PKB copy fails to import. |
| `lab/pkb/agents/buddy/buddy.py` | **User-editable copy.** Materialized on first boot by [builtin_seed.py](../src/arail/agents/builtin_seed.py). Dynamically imported by the shim at [src/arail/agents/buddy.py](../src/arail/agents/buddy.py) at startup. |

The shim resolves `from arail.agents.buddy import buddy` by:

1. Calling `ensure_buddy_folder()` to materialize the PKB copy if
   missing (idempotent; writes a thin re-export shim → `buddy/buddy.py`
   that imports from `_builtin_buddy.py`; to fork Buddy into a custom
   personality, replace `buddy/buddy.py` with a full body copied from
   `_builtin_buddy.py`).
2. Using `importlib.util.spec_from_file_location` to load the PKB
   copy dynamically, picking up any user edits.
3. Falling back to `_builtin_buddy` with an activity-log warning if
   the PKB copy has a syntax error or other import failure.

The fallback means a user who breaks their copy while editing from
the DaC tab doesn't take the portal down — Buddy stays online,
the error is visible in the activity feed, and the user fixes the
file from `/dac` with no restart needed.

## Memory model

Three tiers, in order of durability:

1. **Scratchpad** — attributes on the agent instance (`self._task`,
   `self._status`). Gone when the portal process exits.
2. **state.json** — per-agent JSON at `lab/pkb/agents/<name>/state.json`.
   Loaded on `start()`, saved after every emit. For Buddy it tracks
   `last_said` (per-watcher cooldowns), `last_global`, and
   `utterances`. Small, durable, survives restarts.
3. **Dreams** — markdown at `lab/pkb/agents/<name>/dreams/YYYY-MM-DD.md`.
   Written by the optional `async def dream(self)` hook during the
   heavy work window. Each dream becomes context on the next wake —
   agents literally re-read their own dreams in the morning. Stub
   lands in this PR; the scheduler wiring comes in Step 2.

4. **User understanding** — markdown at `lab/pkb/understanding/<fact_id>.md`.
   Durable facts about *the person using the lab*, distilled from chat and
   carried across sessions. Unlike the three tiers above — which are an agent's
   memory of *itself* — these are claims about someone else, so they are gated
   before any agent can see them.
   **⚠ ROADMAP / not yet built (2026-07-23):** this fourth tier is design only —
   no `lab/pkb/understanding/` writer, no `recall_user_facts`, no fact
   distillation exists in code. Tiers 1–3 (agent self-memory) and Tier-1 chat
   transcripts are real; this one is not yet.

Wiping memory is always one command: delete the file/dir, or run
`./arailctl reset pkb`. That includes conversation transcripts and user
understanding, which is why both live under the PKB root.

### User understanding is gated, and dreams are not

Dreams are an agent reflecting on its own day; a wrong dream is a bad mood. A
wrong *fact about the user* is the lab being confidently wrong about a person,
and it compounds — an agent that learns from its own generated text hallucinates
a user and then believes itself. So the fourth tier has two rules the others
don't:

1. Facts are distilled **only from user turns, never from assistant output**.
2. A fact with no locatable verbatim quote in a real user turn is **rejected**.

Facts start `raw` and are invisible to agents. Only `approved` facts pass
`search_for_agents`, which honors the Compiled-KB gate (`ARAIL_APPROVED_ONLY`) —
the same gate the rest of the lab's knowledge already goes through. Facts are
superseded, never rewritten, so the lab can tell "changed their mind" from
"was always true".

Agents reach them through one host method:

```python
# BuddyHost
def recall_user_facts(self, kinds: list[str] | None = None, limit: int = 8) -> list[dict]: ...
```

It is backed by `search_for_agents`, so the gate applies for free. Buddy folds
the result into `_compose_prompt` alongside the dream block.

**Agents never read the raw transcript.** It is a log, not knowledge, and
injecting it wholesale would grow the prompt without bound. Schema and
invariants live in [conversation-memory.md](conversation-memory.md); the
reasoning — including why this is *not* routed through DaC — is
[ADR-0002](adr/0002-chat-memory-and-the-dac-boundary.md).

## How agents interact with the rest of the lab

```
                 ┌────────────────────────────┐
                 │  portal startup (app.py)   │
                 │  • seeds agents            │
                 │  • .start() each agent     │
                 └────────────┬───────────────┘
                              │
     ┌────────────────────────┼─────────────────────────┐
     │                        │                         │
┌────▼────┐             ┌─────▼─────┐             ┌─────▼─────┐
│ Buddy     │             │ Researcher│             │ Curator   │
│ (tick)  │             │ (goal)    │             │ (sources) │
└────┬────┘             └─────┬─────┘             └─────┬─────┘
     │                        │                         │
     │                        ▼                         │
     │               ┌─────────────────┐                │
     ├──────reads────│ state.json      │                │
     │               │ dreams/         │                │
     │               │ PKB + wiki      ├──── writes to ─┘
     │               │ activity_log    │
     │               │ goal_store      │
     │               │ router (LLM)    │
     │               └─────────────────┘
     │                        ▲
     │                        │
     └────── emits ───────────┘
             (activity_log → SSE → /agents UI)
```

Agents never call each other directly. They coordinate through:

- **activity_log** — the lab's shared nervous system. Any agent can
  watch another's events by subscribing to the stream.
- **PKB** — the shared filesystem. Researcher writes findings,
  Buddy watches the `agents/experiments/` directory for new outcomes.
- **goal_store** — the current mission; agents check `get_current()`
  to tailor behavior.

## Dreams — nightly memory consolidation

Agents that opt in (via `dream: true` in their `AGENT.md`) reflect
once per heavy work window. The dream hook reads:

- **Today's activity** — every event the agent emitted, plus the
  raw facts stashed in each event's `data` field. Pulled from
  `lab/data/activity.jsonl` (the full persistent log, not the 200-
  event RAM buffer), filtered by `source == agent_id` and UTC date.
- **Yesterday's dream** — the most recent markdown file under
  `dreams/` that isn't today's, frontmatter stripped.

It sends both to the local model with a first-person reflection
prompt ("Note what you noticed. Flag what was interesting. Name one
thing you'll watch for tomorrow."), then writes the result to
`lab/pkb/agents/<id>/dreams/YYYY-MM-DD.md` with standard
frontmatter so the wiki indexes it.

### The "wake up knowing" loop

Each agent's prompt composer (e.g. `_compose_prompt()` in
`_builtin_buddy.py`) loads yesterday's dream at the top of every LLM
call. The local model treats it as continuity — "here's what you
were thinking last night" — before the skill block and the
observation. This is the memory consolidation layer of the
architecture, implemented entirely with markdown files and one
LLM call per day.

### The daemon

[`src/arail/agents/dream_daemon.py`](../src/arail/agents/dream_daemon.py)
is the scheduler. It:

- Polls every 15 min (`LAB_DREAM_POLL_SEC`).
- Only fires when `current_window() == "heavy"` (22:00-08:00 by
  default). Override with `LAB_DREAM_WINDOW=any` for testing.
- Respects the global halt flag — if the user hits "Halt jobs" on
  the dashboard, no background model calls.
- Iterates a registry of `(agent_id, agent_instance)` and calls
  `await agent.dream()` on each. The agent itself checks
  idempotency: if `dreams/<today>.md` exists, skip.
- A failing `dream()` on one agent is logged as a warning and the
  daemon continues to the next — one bad reflection doesn't silence
  the whole lab.

### Opt-out

- `LAB_DREAMS=off` — silences the whole daemon. Personality layer
  still runs; just no nightly reflection.
- Delete `dream: true` from an agent's AGENT.md — that agent opts
  out individually.

## Skills — domain expertise as editable markdown

Skills are live. Every agent's `AGENT.md` lists the skills it uses;
the skill loader reads each skill's `SKILL.md` from
`lab/pkb/skills/<id>/` and appends the body to the agent's system
prompt on every LLM call. Edit a skill, the next utterance uses
the new version — no restart, no redeploy.

### The split that makes it work

| | Skill | Tool |
|---|---|---|
| **Where** | `lab/pkb/skills/<id>/SKILL.md` | `src/arail/*.py` functions |
| **Form** | Markdown — procedural knowledge | Python callable |
| **Editable by** | User (or agent, via review queue) | Developer |
| **Versioned in** | Wiki + CHANGELOG.md + git if user commits | Git |
| **Example** | "How to evaluate a local LLM" | `system.gpu_pct()` |
| **Loaded** | Injected into system prompt | Imported at runtime |

Skills are prose; tools are code. Skills describe *what to do*;
tools *do it*. Agents compose both — a researcher imports tools
(`pkb.write_note`, `router.complete`) and lists skills
(`evaluate-llm`, `falsify-hypothesis`). Changing the skill list
changes what the agent knows; changing the tool list changes what
the agent can do.

### SKILL.md shape

```yaml
---
title: Evaluate a local LLM
id: evaluate-llm
name: Evaluate LLM
domain: ai
version: 1.0.0
tags: [skill, evaluation, ai]
when_to_use:
  - When the goal is model selection or tuning
  - When a new model arrives and we want to know if it's better
when_not_to_use:
  - For one-off smoke tests (no need for full rigor)
---

# Evaluate a local LLM

Procedural knowledge for benchmarking a model that runs on the
lab's hardware. Produces comparable numbers rather than impressions.

## The minimum viable benchmark
...
```

The frontmatter is the machine-readable contract (id must match the
folder name); the body is the procedural knowledge the agent reads.
Authoring is pure markdown — anyone who can edit a README can edit
a skill.

### How an agent composes a prompt

For each LLM call, the agent:

1. Reads `AGENT.md` from its folder, pulls the `skills:` list.
2. For each skill id, reads `lab/pkb/skills/<id>/SKILL.md` and
   strips its frontmatter.
3. Concatenates the bodies under a `# Procedural knowledge`
   heading, each skill in its own `## Skill: <name>  ·  v<version>`
   section.
4. Prepends the agent's base system prompt; appends the task-
   specific observation or user message.

The composer lives at [`src/arail/skills_loader.py`](../src/arail/skills_loader.py).
Loading is deliberately cheap: skills are tiny markdown files, reads
happen on every emit. No caching, no invalidation logic, no restart
needed to pick up edits — that's the whole payoff.

### Shipped starter skills

Seeded on first boot by
[`src/arail/skill_seed.py`](../src/arail/skill_seed.py):

| Skill | Domain | Used by |
|---|---|---|
| [`observe-lab`](../lab/pkb/skills/observe-lab/SKILL.md) | meta | Buddy (what to notice, when to stay quiet, how to phrase) |
| [`evaluate-llm`](../lab/pkb/skills/evaluate-llm/SKILL.md) | ai | Researcher when lab intent is AI engineering |
| [`falsify-hypothesis`](../lab/pkb/skills/falsify-hypothesis/SKILL.md) | research | Researcher for bias reduction |

### Eager loading today, lazy later

v1 loads skills **eagerly** — every listed skill gets appended to
the system prompt on every call. This is simple and predictable,
and it matches the "user edits markdown, behavior changes
immediately" promise. Cost: a few hundred extra tokens per call.

**Planned v2 — self-sufficient agents.** When an agent's skill list
grows past what fits in a system prompt, we'll add a dispatch layer
so the agent picks which skill to consult per task (closer to the
tool-calling pattern in Claude's API). The SKILL.md shape doesn't
change; only the loading mechanism evolves. Noted here so the
roadmap is explicit — if you start depending on every-skill-is-in-
context, flag it.

### Authoring a new skill

1. Create `lab/pkb/skills/<your-skill-id>/SKILL.md` with the
   frontmatter shape above.
2. List your skill in any agent's `AGENT.md` (`skills: [your-skill-id]`).
3. On the next LLM call from that agent, the skill is live.

No loader registration, no restart, no code change. Drop the file,
list it, go.

## Roadmap

This PR shipped **skills** (Step 3 below). Remaining steps:

| Step | Capability | Status |
|---|---|---|
| 1 | Buddy as folder, `state.json`, `dream()` stub, shim with fallback | ✓ shipped |
| 2 | Dream loop — scheduler daemon calls `dream()` once per heavy window; yesterday's dream feeds today's system prompt | ✓ shipped |
| 3 | **Skills** as first-class markdown; agents compose them into the system prompt | ✓ shipped |
| 4 | Dynamic agent loader — discover every `lab/pkb/agents/*/AGENT.md` at startup, register each singleton | ✓ shipped |
| 5 | Agent Forge UI — two-panel editor on `/agents`: AGENT.md ↔ generated `.py`, skill toggles, Deploy | ✓ shipped |
| v2 | Self-sufficient / lazy skill dispatch | deferred |
| v2 | Watcher builder in the Forge (toggleable library + natural-language → watcher code) | deferred |

## Forging an agent

The Agent Forge is the point-and-click way to mint a new agent
without touching Python. Open `/agents`, expand the "+ Forge an
agent" panel, fill in the form, click Deploy.

### What the form asks for

| Field | Purpose |
|---|---|
| Name | Display name. Becomes `NAME` in the generated code. |
| Emoji | One symbol; prepended to every utterance the agent makes. |
| Short role | One-line descriptor for the file docstring. |
| Voice | The personality system-prompt. ~40 words, second-person. Change this, the agent sounds different. |
| Tick interval | Seconds between loop iterations (30-3600). |
| Cooldown | Minimum seconds between two utterances (60-86400). |
| Dream nightly | Checkbox — enables the nightly reflection loop for this agent. |
| Skills | Multi-select from installed skills. Each checked skill's body gets appended to the system prompt on every LLM call. |

### What Deploy does

1. **Validates** the form server-side — required fields, ranges,
   agent-id doesn't collide with an existing one.
2. **Generates** `AGENT.md` + `<agent_id>.py` from the same
   template shape as Buddy.
3. **Parses** the generated Python with `ast.parse` before writing
   anything to disk — a template bug can't ship.
4. **Writes** `lab/pkb/agents/<agent_id>/` with AGENT.md, the .py,
   decisions.md, and an empty dreams/ directory.
5. **Hot-loads** via `loader.load_one(<agent_id>)` — if the import
   succeeds, the agent is live in the activity feed without a
   portal restart. `start_all_auto()` calls `.start()` and
   registers with the dream daemon if opted in.

### Backend

- `src/arail/agents/forge.py` — code generator + deploy helper.
  Split into: `slugify` (name → Python id), `validate` (form
  sanity), `generate_agent_md` / `generate_agent_py`, `deploy`.
- `GET /api/skills/list` — feeds the skills picker.
- `GET /api/agents/forge/preview?…` — server-side preview of the
  exact bytes Deploy would write. The UI uses this for the right
  panel so the preview is canonical, not a client-side guess.
- `POST /api/agents/forge` — Deploy. Returns
  `{ok, agent_id, hot_loaded, started}` on success.

### Post-deploy editing

Forged agents live under `lab/pkb/agents/<id>/` like every other
agent. To edit:

- **Voice / schedule / skills** — edit `AGENT.md` from `/dac`.
- **Watchers** (what the agent notices) — edit `<id>.py` and add
  functions to the `WATCHERS` list. The generated file has
  commented examples showing the shape.
- **Mute** — set `LAB_<AGENT_ID>=off` in `.env`.
- **Remove** — delete the folder from `/dac` and restart.

### What the Forge doesn't do (yet)

- **Watcher builder.** v1 generates agents with an empty
  `WATCHERS = []` list and comments explaining how to add one.
  Natural-language → watcher code is on the v2 roadmap.
- **Template picker.** Only one template ships (voice-only with
  skills). "Start from Buddy," "watchdog," etc. come later.
- **Code-level editing in the Forge.** The right panel is a
  preview, not a textarea. Post-deploy edits happen via the
  Knowledge editor.
- **Undo.** Deploy writes a folder. To undo, delete the folder.

## The dynamic loader

[`src/arail/agents/loader.py`](../src/arail/agents/loader.py) walks
`lab/pkb/agents/*/AGENT.md` at portal startup, instantiates every
agent it finds, starts the ones that opt in, and registers dream-
capable ones with the nightly scheduler. The same path handles
shipped agents (Buddy), forged agents (eventually, from the Forge
UI), and anything a developer drops in by hand.

### Discovery

A folder under `lab/pkb/agents/` is an agent iff it contains an
`AGENT.md`. Folders without it (`research/`, `experiments/`,
`synthesis/`, `recommendations/`) are output — the loader walks
past them. This is the same rule the user-facing README spells
out, enforced in code.

### Import contract

For each agent folder:

- `<agent_id>/<agent_id>.py` — the Python module (must exist).
- Must export a module-level singleton named `<agent_id>` (exactly
  the folder name, lowercase).
- The singleton provides `.start()` and optionally `async def
  dream(self)`. That's the whole contract.

The loader imports via `importlib.util.spec_from_file_location`
with a unique module name (`arail.agents._folder_<id>`) so the
bundled `_builtin_<id>.py` never clashes with the PKB copy in
`sys.modules`.

### Attribution and hold contract

Since sprint 2026-09-20-buddy-front-and-center, every model call your agent
makes is traced and can be held — and for anything reachable through the
loader's own entry points, this happens **without you writing a line of
attribution or halt-checking code.**

**Attribution, for free, if you go through `.start()`.** The loader wraps
`instance.start()` in `arail.agent_context.agent_call(agent_id)` before
calling it. Because `asyncio.create_task` copies the calling
`contextvars.Context`, an agent that spawns its own async loop *inside*
`start()` — every shipped agent does this — has that whole loop, and every
model call made from it (directly, or via `asyncio.to_thread`), attributed
to your agent id for its entire life. You do not call `agent_call()`
yourself for this path. This is deliberate: a new agent author cannot
forget something they never had to write.

**If your model call happens somewhere the loader doesn't reach** — a
helper module invoked from outside `start()`/`dream()`, a background thread
you spawn yourself, or anything similar — it will not be attributed
automatically, and it will not be silently mislabeled either: it lands in
the Admin lane view's **Unattributed** lane, named by its exact
`module:lineno` call site. That is a visible, ugly signal that something
needs a wrapper — never a wrong agent id. Two ways to fix it:

- Wrap the call site yourself: `with arail.agent_context.agent_call
  (your_agent_id): resp = router.complete(...)`.
- If you spawn a bare `threading.Thread` (rather than relying on
  `create_task`/`to_thread`, both of which already copy context), use
  `arail.agent_context.spawn_thread(target, *args, **kwargs)` instead of
  `threading.Thread(...)` directly — a bare `Thread` does **not** copy the
  context, and the shim exists specifically for this case.

**Hold ("Hold all agents" in Admin) refuses your inference, not your
process.** `ModelRouter.complete()`/`.stream_complete()` raise
`arail.agent_context.AgentHeldError` (a `RuntimeError`) when the lab is
held and the active context is agent-scoped. This means:

- **Your agent must already tolerate a `RuntimeError` from a model call**
  the same way it tolerates a network failure or a cold model. If your
  code already wraps `router.complete()`/`.stream_complete()` in a broad
  `except Exception`, you get graceful degradation for free — the same
  path an unreachable model already takes. If it doesn't, add one; an
  agent that lets `AgentHeldError` propagate into its own loop will crash
  that loop the first time the operator holds the lab.
- Hold is admission control, not cancellation — a call already inside a
  backend when the switch flips runs to completion. Only *new* calls are
  refused at the door.
- If your agent speaks proactively (writes to the activity feed off its
  own initiative, not in response to a request), gate that announcement
  too: `if arail.agent_context.speech_gate(your_agent_id): emit(...)`.
  Inference and speech are held separately on purpose — silencing your
  agent's voice must not require silencing whatever non-model bookkeeping
  it still safely does while held.

See `sprints/2026-09-20-buddy-front-and-center/ARCHITECTURE.md` and
`docs/agent-observability.md` for the full design and the trace schema.

### Cache

`load_one(agent_id)` and `load_all()` are idempotent — the first
call materializes the instance, every subsequent call returns the
same one. The backcompat shim at `src/arail/agents/buddy.py` now
delegates to `load_one("buddy")`, so `from arail.agents.buddy import
buddy` and `load_all()["buddy"]` resolve to the same object. No double-
ticking.

### Shipped fallback

Agents listed in `_SHIPPED` (today: `buddy` + `sre`) get an automatic
fallback to `src/arail/agents/_builtin_<id>.py` if the PKB copy
fails to import. User-forged agents don't have this safety net —
a broken `.py` means the agent doesn't load and a warning lands
in the activity feed. The Forge UI will surface these explicitly.

### Opt-in behavior from `AGENT.md`

Two frontmatter fields drive loader behavior:

- `auto_start_env: LAB_BUDDY` — name of an env var whose value
  gates `.start()`. Set to `off` / `0` / `false` / `no` to keep
  the agent loaded but quiet. Omit the field to always start.
- `dream: true` — register with the dream daemon. Omit or set to
  `false` to opt out of the nightly reflection loop.

### Portal startup

```python
from arail.agents.loader import load_all, start_all_auto
agents = load_all()            # {"buddy": <BuddyAgent>, ...}
start_all_auto(agents)         # .start() + dream-register each
```

That's the whole wiring. Adding a new agent = drop a folder with
an `AGENT.md` + `.py` under `lab/pkb/agents/`, restart the portal,
the loader finds it. No code changes to `app.py` for shipped or
user-forged agents.

## Why dynamic imports over a plugin registry

Arail has a plugin manager ([manager.py](../src/arail/plugins/manager.py))
but it's metadata-only — it installs GitHub repos and reads
manifests; it doesn't execute plugin code. Agents need live code
execution, so the shim at `src/arail/agents/buddy.py` uses
`importlib.util.spec_from_file_location` to load the PKB copy.
The loader in Step 4 generalizes this to walk every agent folder.

## Why in-process and not subprocess

This lab targets a single-user workstation. In-process means:

- Zero startup cost per agent (Buddy's overhead: one asyncio task).
- Shared memory — agents read the same goal_store, PKB, router.
- A single process to monitor and restart.

The cost is weak isolation — a bad agent can crash the portal. For
this lab that's an acceptable tradeoff (see
[SECURITY.md](../SECURITY.md)). The schools-donation variant will
switch to subprocess-per-agent with a reduced API surface; that's a
separate product, not a retrofit.

## For developers who want to build an agent today

**Easiest path (today, before the Forge lands):**

1. Copy `src/arail/agents/_builtin_buddy.py` → a new file in the same
   directory (e.g., `_builtin_owl.py`).
2. Rewrite the `NAME`, `EMOJI`, `SYSTEM_PROMPT`, and the watcher
   functions.
3. Add a shim `src/arail/agents/owl.py` modelled on
   `src/arail/agents/buddy.py` (copy-paste, rename the folder).
4. Update `builtin_seed.py` to seed the new folder.
5. Wire `from arail.agents.owl import owl` into `app.py` startup.

**Once the Forge lands:** the above becomes a form + a click of
Deploy.

## References

- [src/arail/agents/_builtin_buddy.py](../src/arail/agents/_builtin_buddy.py) — canonical Buddy source, best-documented agent
- [src/arail/agents/builtin_seed.py](../src/arail/agents/builtin_seed.py) — how agent folders get materialized
- [src/arail/agents/buddy.py](../src/arail/agents/buddy.py) — the shim pattern
- [src/arail/activity.py](../src/arail/activity.py) — the shared event stream
- [src/arail/scheduler.py](../src/arail/scheduler.py) — work windows + halt flag
- [src/arail/pkb.py](../src/arail/pkb.py) — PKB root resolver + ingest
- [lab/pkb/agents/README.md](../lab/pkb/agents/README.md) — user-facing overview seeded into the DaC tab
