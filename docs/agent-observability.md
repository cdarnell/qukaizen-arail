---
title: Agent observability
description: "How the lab sees, attributes, times, holds, and (optionally) records every agent-sourced model call — the chokepoint design from sprint 2026-09-20-buddy-front-and-center."
category: Reference
order: 7
tags:
  - agents
  - observability
  - security
  - admin
audience: architect
related:
  - agents
  - agent-loop
---

# Agent observability

Before this sprint: Buddy's inference emitted nothing about itself, every
agent call was billed to one generic `"agent"` bucket, fifteen of nineteen
agent modules ignored the halt flag, and two agents wrote full prompt and
response bodies to an unauthenticated on-disk log by default. This document
describes what replaced that, and why it is built as one instrumented
chokepoint rather than eleven separate features.

Full design record: `sprints/2026-09-20-buddy-front-and-center/ARCHITECTURE.md`
(interface contracts, failure modes, test strategy) and `BUILD_LOG.md`
(what actually shipped, slice by slice, with every deviation from the
design named). This doc is the durable summary; those are the history.

## The chokepoint

Every agent-inference path in the lab — however it starts — ends at exactly
one place: `ModelRouter.complete()` / `.stream_complete()`
(`src/arail/router/core.py`). Eleven-plus acquisition sites across the
codebase all construct or resolve a router and then call one of these two
methods. That single funnel is why this sprint could add attribution,
tracing, TTFT, a kill switch, and body capture as one instrumented
chokepoint instead of eleven separate, driftable features.

```
  agents/loader.py            dream_daemon / job_daemon    top-level modules
  start_all_auto()            _dream_once / _run_job       (researcher, browser,
        │                            │                      librarian_scout, ...)
        │  L1                        │  L2                        │  L3
        ▼                            ▼                             ▼
  agent_context.agent_call(id) ──────┴─────────────────────────────┘
        │  contextvars.ContextVar
        ▼
┌───────────────────────── ModelRouter.complete / .stream_complete ─────────┐
│ 1. ctx = agent_context.current()                                          │
│ 2. halt_gate(ctx)            — refuses agent-kind calls while held        │
│ 3. slot = slot_pressure()    — was the inference slot held by someone else│
│ 4. recorder_on() at call start — F10 latching                             │
│ 5. backend.complete()/.stream_complete()                                 │
│ 6. cost_tracker.track(source=billing_source(ctx))                        │
│ 7. agent_trace.record(...)   — exactly once, success/failure/refusal      │
└─────────────────────────────────────────────────────────────────────────┘
```

## Attribution — three layers, one contextvar

`src/arail/agent_context.py` defines a single `contextvars.ContextVar`
carrying an `AgentCall` (agent id, kind, trace id, brain/effort hints, deep
reason code). It is set by whichever of three layers is closest to where a
call actually originates:

- **L1 — the loader (`agents/loader.py`).** Wraps every agent's
  `instance.start()` in `agent_call(agent_id)`. Because `asyncio.create_task`
  copies the calling context, an agent that spawns its loop inside `start()`
  — every built-in does — is attributed for its whole life, with no
  per-agent code. This is the "cannot be forgotten" layer; see
  `docs/agents.md`'s "Attribution and hold contract" for what it means for
  an agent author.
- **L2 — daemons that call agents from outside `start()`.**
  `dream_daemon._dream_once` and `job_daemon._run_job` set the context per
  agent/job before invoking.
- **L3 — modules the loader never touches.** `researcher`, `browser`,
  `librarian_scout`, `_builtin_drafter`, and `recap/router_adapter` each get
  one explicit wrapper at their own model-acquisition call site;
  `world_routes`, `dictionary`, and the goal-parser subprocess protocol are
  not agents at all and use `system_call(label)` instead — same mechanism,
  billed as `sys:<label>` rather than `agent:<id>`.

A call with no active context is **unattributed** — never billed as a
plausible-looking agent id, never dropped. It carries the exact
`module:lineno` call site so a missed wrapper is a visible, ugly signal
rather than a silently wrong number.

**Why a contextvar and not a router attribute or a new parameter.** Routers
are cached and shared across calls (`deep_policy._deep_router`,
`researcher._router_cache`, ...); stamping an id on a shared router would be
an aliasing bug and a race the moment two calls interleave. A contextvar is
call-scoped by construction — prior art in this codebase is
`costs._recap_depth_tls`.

## The trace store

`src/arail/agent_trace.py` is a third, bounded, append-only persistence path
— beside `activity.jsonl` (a 200-event ring the trace volume would evict)
and `costs.json` (which rewrites its whole file on every call). One
`record()` call happens per chokepoint invocation: ring append first (so a
disk failure never costs the live view), then SSE fan-out, then a disk
append inside its own exception boundary (`dropped_writes` counts loss,
never hides it).

Every record carries the same ~28 fields (`schema": "arail.agent_trace/v1"`)
with `null` for absent data — never `0`, `""`, or `"n/a"`. See the module
docstring for the exhaustive field list.

**Per-World isolation is free.** `DATA_DIR` is resolved lazily, per call,
never a module constant — so two `ARAIL_DATA_DIR` roots (two concurrent
World instances) never interleave, and nothing in this module enumerates
`lab/instances/` or aggregates across roots. This mirrors the same
invariant `pkb_index.py` documents for `PKB_ROOT`.

## TTFT — the honesty contract

`ttft_ms` is measured from `perf_counter()` taken *before* the backend's
stream generator is entered, and is **never** derived from `latency_ms`.
`ttft_status` is an explicit enum, not a number that might be fake:

| First real signal | `ttft_ms` | `ttft_status` |
|---|---|---|
| First yielded item is a non-empty string | measured | `measured` |
| First yielded item is the terminal response (backend emulated the stream) | `null` | `emulated_stream` |
| Every item was an empty string before the terminal response | `null` | `no_tokens` |
| The stream raised before anything was determined | `null` | `error` |
| `complete()` (never streams) | `null` | `non_streaming` |

`prefill_ms` is a *different* field — server-reported only (Ollama's
`prompt_eval_duration`), `prefill_source="server_reported"` always, and a
client must not render it as TTFT.

To make agent-path TTFT non-vacuous (no agent called `stream_complete`
before this sprint), `deep_policy.complete_preferring_deep`'s **fast
branch only** now streams and joins deltas, behind `ARAIL_AGENT_STREAM_FAST`
(default on) with an unconditional fallback to `complete()`. The deep
branch (`aerollm`) still calls `complete()` — `AeroLLMBackend` has no real
streaming yet, so its TTFT is honestly `non_streaming` until QueueLLM ships
one.

## Hold — "Hold all agents"

One flag (`arail.scheduler.jobs_halted()`, unchanged from before this
sprint) now has wider teeth. `agent_context.halt_gate(ctx)` raises
`AgentHeldError` at the chokepoint when held **and** the active context is
agent-scoped — `kind="system"`, `"ui"`, and unattributed calls always pass,
so holding agents never breaks the operator's own chat turn or a system
job. This is **admission control, not cancellation**: a call already inside
a backend when the switch flips runs to completion.

`agent_context.speech_gate(agent_id)` is the separate, narrower control for
proactive *speech* (an agent announcing something on its own initiative,
not in response to a request) — silenced for everyone except
`HOLD_EXEMPT_SPEAKERS = {"sre"}`, wired into buddy, librarian, presence,
debt_advisor, and consolidation_analyzer's own announcement points.

`complete_preferring_deep` catches `AgentHeldError` specifically (before
its generic `except Exception`) so a held decision produces exactly one
refused trace, not two from a deep-then-fast retry.

## Flight recorder

Off by default. `agent_trace.recorder_on()` is read **once at call start**
and used for the whole call — turning it on mid-call does not retroactively
capture that call; turning it off mid-call still captures what was already
running (the safer half either way). When on, `src/arail/redact.py`'s
`capture_body()` is the only function in the codebase permitted to put a
body into a trace record: two always-both redaction passes (every value in
`secrets.env` plus `ARAIL_PASSWORD`/`OPEN_NOTEBOOK_ENCRYPTION_KEY`, then
seven shape patterns for secrets that were never in `secrets.env`),
redact-then-truncate, fail-closed on any exception.

`researcher.py` and `browser.py` no longer write prompt/response bodies
into `activity.jsonl` at all. `GET /api/agents/prompts` sources from the
trace store instead of `activity_log`, and omits the `prompt`/`response`
keys entirely (never empty strings) when the recorder is off or a
particular record has no captured body.

**Legacy bodies** already on disk from before this sprint are disclosed
(`GET /api/admin/legacy-bodies`) and purged only on operator action
(`POST .../purge` strips bodies, stamps `body_purged: true`, preserves
every other field and the exact line count) — never automatically.

## Admin surface

Four endpoints, all `maximus`-tier only (`_require_surface("admin")`):
`GET /api/admin/agent-lanes` (the snapshot — fixed roster of eleven
built-in agents plus a generic user-defined lane plus the mandatory
unattributed lane, each with an honest `empty_reason` when it has never
been called), `GET /api/admin/agent-trace-stream` (SSE, deliberately a
different route prefix from the snapshot so it is never fast-path-timed),
`GET /api/admin/agent-trace/{trace_id}` (the "why?" drill-in), `POST
/api/admin/agents/hold`, `POST /api/admin/flight-recorder`. The
`admin.html` "Agent lanes" card renders live from the SSE stream — no poll
interval for lane data.

## What this does not fix

- **The portal has no authentication.** `onboarding_gate` blocks every
  surface only until a passphrase exists; there is no per-request
  credential check after that. This sprint reduces *what* is exposed
  (bodies off by default, redacted, capped, admin-gated) but does not add
  auth. Filed to `sprints/BACKLOG.md`.
- **The inference gateway** (a single admission point that also *orders*
  chat ahead of agents) is deferred, gated on the `overlap_pct` this sprint
  now measures at every agent call. See `sprints/BACKLOG.md`.
- **17 pre-existing `/api/admin/*` endpoints do not call
  `_require_surface("admin")`** despite one being cited as this sprint's
  own precedent for the pattern — discovered while wiring S5/S6's new
  endpoints, not fixed (out of scope), filed to `sprints/BACKLOG.md`.
