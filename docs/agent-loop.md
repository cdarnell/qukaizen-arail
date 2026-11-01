---
title: "What an Agent Is: Observe, Think, Act"
description: "The three-stage loop underneath every agent in the lab, and how much weight 'Act' carries in each one."
category: Concepts
order: 6
tags:
  - agents
  - concepts
  - getting-started
audience: beginner
related:
  - agents-explained
  - agents
buddy_prompt: "Walk me through Observe, Think, Act using a real agent in this lab as the example."
---
# What an agent is: Observe, Think, Act

> **See also:** [Agents, explained](agents-explained.md) is the on-ramp for
> Buddy specifically — "an agent is a loop that notices things and speaks
> up." This doc is the more general shape underneath that sentence, and
> underneath every other agent in the lab too.

## The one-sentence version

**An agent is a loop with three stages: Observe, Think, Act.**

Every agent in this lab — the ones that only talk, and the ones that
touch files — runs some version of this. What changes between them isn't
the shape. It's how much weight sits in the third stage.

```text
  ┌───────────┐      ┌───────────┐      ┌───────────┐
  │  OBSERVE  │  →   │   THINK   │  →   │    ACT    │  →  (loop)
  │ read      │      │ decide    │      │ say it,   │
  │ context   │      │ what      │      │ write it, │
  │           │      │ matters   │      │ run it    │
  └───────────┘      └───────────┘      └───────────┘
```

- **Observe** — read whatever's true right now: GPU load, the current
  goal, a git diff, the activity log, a file on disk.
- **Think** — turn that observation into a decision: is this worth
  reacting to, and if so, what's the plan?
- **Act** — do the thing that changes the world (or at least the
  screen): emit a message, write a file, run a command, edit code.

Then it loops. What the next Observe sees includes what the last Act
did — that's what makes it a loop and not a pipeline.

## Three weights of "Act," all live in this lab today

The lab doesn't have one kind of agent. It has a spectrum, and the
spectrum is exactly "how much does Act get to do."

### 1. Notice and say — Buddy, SRE

[`_builtin_buddy.py`](../src/arail/agents/_builtin_buddy.py) and
[`_builtin_sre.py`](../src/arail/agents/_builtin_sre.py) are the
lightest weight. Every tick:

- **Observe** — run a list of watcher functions against GPU state, the
  PKB, the activity log, recent crashes.
- **Think** — filter out anything already said recently, pick the
  juiciest fact, and turn it into one sentence with a single
  `router.complete()` call.
- **Act** — `activity_log.emit(...)`. That's it. Nothing on disk
  changes; nothing runs. The blast radius of a bad decision here is
  "Buddy said something dumb."

Full trace: [Agents, explained → "One tick, traced end to end"](agents-explained.md#one-tick-traced-end-to-end).

### 2. Notice, measure, and write — the Researcher (Autoresearch)

[`mini_experiments.py`](../src/arail/research/mini_experiments.py) is
heavier. Its loop is named directly after the stages it's built from —
**Plan → Design → Run → Report**, which is Observe/Think split into
two and Act split into two:

- **Observe** — read the active goal and what's already in the PKB.
- **Think (Plan)** — turn the goal into a handful of testable
  hypotheses, with a visible trace of which ones were considered vs.
  chosen.
- **Think (Design)** — map each hypothesis to something concretely
  measurable (model throughput, prompt quality, retrieval quality, a
  user-supplied benchmark command).
- **Act (Run)** — actually execute the measurement locally. For the
  `game_config_optimization` archetype this is a real
  `asyncio.create_subprocess_exec` call
  ([mini_experiments.py:482](../src/arail/research/mini_experiments.py:482))
  — a genuine "run a command," not a simulation.
  If nothing can be measured yet, it says so — it never invents a
  number.
- **Act (Report)** — write the result to the Knowledge Base labeled
  `measured` / `not run` / `unmeasured`, so a reader always knows what
  was real.

This loop can run a command, but it can't touch your source tree — it
only ever writes into the PKB. See the in-app explainer for the
layman's version of this exact loop (Autoresearch page → "❓ How does
this work?").

### 3. Notice, edit, run, keep-or-revert — the Tuning loop

`arail.experiments` (the **/tuning** page — the loop is
[autoresearch.py](../src/arail/experiments/autoresearch.py), the git is
[git_ops.py](../src/arail/experiments/git_ops.py)) is heavier still, and
closest to what "an autonomous coding agent" usually means. Its shape,
stripped to the loop:

```text
LOOP:
  Observe → what branch/commit are we on, what did the last run measure?
  Think   → what's one experimental change worth trying?
  Act     → branch, edit the config, run it, read the real result
          → if it beats baseline by the threshold: commit on the branch
          → if it's equal or worse: discard the working-tree changes and
            check the original branch back out
```

The revert is `git checkout -- .` followed by `git checkout <original
branch>` — **not** `git reset`. The losing branch ref is left in place
on purpose so you can go look at what was tried
([git_ops.py:134-142](../src/arail/experiments/git_ops.py)). The loop
can only ever write four whitelisted files, it never pushes, every
commit it makes lands inside the `autoresearch/` namespace (the
baseline included), and it puts you back on your original branch when
it's done.

This is the one loop in the lab where Act includes **editing a file and
running arbitrary code** — the same category of action a coding agent
takes. The keep-or-revert step is a second, smaller Observe→Think
cycle nested inside Act: observe the measured result, decide whether it
earned the change, act again (keep or discard). Every accepted change
becomes a real git commit — nothing here is invented or
simulated after the fact.

### 4. The whole loop, literally — opencode (Model Building)

The lab also runs an actual general-purpose coding agent as a
subprocess:
[`src/arail/portal/services/opencode.py`](../src/arail/portal/services/opencode.py)
manages `opencode serve`, embedded as an iframe at `/opencode`
(maximus tier — see `_TIER_SURFACES` in
[app.py](../src/arail/portal/app.py)). This is the same Observe → Think
→ Act loop as this document describes, running with the fullest
version of Act there is: read the repo, plan, call a tool, edit a
file, run a command. It's not a simplified in-house version of the
pattern — it's the pattern, running for real, inside the lab.

## Why the spectrum is deliberate, not accidental

Each tier's Act is scoped to match what a mistake there would cost:

| Agent | Act can... | A bad decision costs |
|---|---|---|
| Buddy / SRE | emit a message | an annoying sentence |
| Researcher | run a measurement, write to the PKB | a wrong-but-labeled note in the Knowledge Base |
| Tuning loop | edit one of four whitelisted files, run it, commit or discard | an abandoned branch (never your working tree) |
| opencode | anything a coding agent can do | whatever a coding agent could do — hence maximus-tier gating |

Notice the loader contract itself enforces the first boundary: personality
agents (`lab/pkb/agents/<id>/<id>.py`) only ever import `activity_log`,
`pkb.search`, and `router.complete` — see
[Agents — Architecture Reference § Why in-process and not subprocess](agents.md#why-in-process-and-not-subprocess).
There's no code path from "Buddy noticed something" to "a file on disk
changed." The heavier the Act, the more visibly it's git-tracked,
provenance-labeled, or tier-gated. That's the actual safety model: not
"agents can't act," but "how much an agent's Act is allowed to touch is
sized to the agent."

## The same shape, elsewhere

This isn't an ARAIL invention. It's the general shape behind any system
that gets called an "agent" — Claude Code itself runs Observe → Think →
Act (read the repo and the conversation, decide what to do, call a
tool). QueueLLM's inference loop and DDaC's declare→gate→version pipeline
aren't agent loops in this sense — they're not making act-or-don't-act
judgment calls on a cadence — but paperagents' declarative agent configs
and any future qukaizen.com agent surface would be expected to fit the
same three stages, for the same reason ARAIL's four tiers all do: it's
the minimum shape a "notice something, decide, do something" loop can
have. (That's a structural claim about the pattern, not a status report
on those repos' code — verify against their own docs before citing
specifics.)

## Go deeper

- [Agents, explained](agents-explained.md) — the friendly Buddy walkthrough this doc generalizes from.
- [Agents — Architecture Reference](agents.md) — full contracts: folder shape, memory tiers, the dynamic loader.
- [`src/arail/agents/_builtin_buddy.py`](../src/arail/agents/_builtin_buddy.py) — lightest-weight Act, read this first.
- [`src/arail/research/mini_experiments.py`](../src/arail/research/mini_experiments.py) — the Plan→Design→Run→Report loop.
- [`src/arail/experiments/autoresearch.py`](../src/arail/experiments/autoresearch.py) — the git-branch-per-experiment loop itself.
- [`src/arail/experiments/git_ops.py`](../src/arail/experiments/git_ops.py) — every git call that loop makes, and the safety rails around them.
- [Autoresearch integration audit](plans/autoresearch-integration.md) — how the two loops differ, and the long-term hazards of the git one.
- [`src/arail/portal/services/opencode.py`](../src/arail/portal/services/opencode.py) — the fully general version, running as a real subprocess.
