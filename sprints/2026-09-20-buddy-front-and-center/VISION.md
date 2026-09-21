# Vision: P1 — observability + guardrails over the agents that already exist

**Date:** 2026-09-21
**Product:** arail
**Wedge size:** one sprint — **after the cuts in §Wedge**. As written in the
ledger, P1 is three sprints. See §"Is P1 one sprint or three".

This VISION pressure-tests `OPERATOR_BRIEF.md` (decisions D1–D18). Every code
claim below was read in this worktree on 2026-09-21, not taken from the brief.
Where the brief and the code disagree, the code wins and the disagreement is
named.

---

## What I verified (and where the brief is optimistic)

| # | Verified fact | Where | Consequence |
|---|---|---|---|
| V1 | **The chokepoint already exists.** Every one of the ad-hoc acquisition paths ends at `ModelRouter.complete()` / `.stream_complete()`, and `cost_tracker.track(...)` is already called from *both*. | `src/arail/router/core.py:145-170`, `:172-200` | The gateway is not required to *observe*. Tracing at the existing chokepoint gets most of the value. This is the single biggest scope reduction available. |
| V2 | **Buddy's inference is 100% invisible.** `llm_complete` → `deep_policy.complete_preferring_deep` → `router.complete`. No `activity_log.emit`, no model name, no latency, no deep-vs-fast decision, no fallback notice. `deep_policy.py` imports no activity log at all. | `agents/_builtin_buddy.py:175-188`; `agents/deep_policy.py` (whole file) | The agent the whole programme is about is the one agent we cannot see. This is the sharpest "cannot see today" baseline. |
| V3 | **Agent calls never enter `inference_slot`.** Zero hits across `src/arail/agents/`, `librarian_scout.py`, `skills/`. Buddy's call runs via `asyncio.to_thread`, i.e. genuinely concurrent with a chat stream holding the semaphore. | `grep inference_slot src/arail/agents/` → empty; `agents/_builtin_buddy.py:1569` | The brief frames priority as *re-ordering a queue*. There is no ordering to fix — agents are outside the queue. Bigger problem than stated, and **not** cheap: `inference_slot` is an async CM, agent calls are sync-in-thread. |
| V4 | **The kill switch does not currently hold the talkers.** Only 4 of 19 modules in `agents/` reference `jobs_halted()`: `researcher.py`, `job_daemon.py`, `dream_daemon.py`, `_builtin_librarian.py`. Buddy, SRE, browser, drafter, presence, curator, debt_advisor, consolidation_analyzer do not. Halt only downgrades Buddy deep→fast via `_background_gate`. | `agents/deep_policy.py:69`; per-module grep | "Hold all agents" is **substantive**, not cosmetic. Today there is no way to make Buddy stop. |
| V5 | **Bodies are already captured, always-on, unredacted, to disk.** `prompt[:3000]` + `response[:2000]` per call from researcher and browser into `activity_log`, which persists to `lab/data/activity.jsonl` (10 MB rotating). Served by `GET /api/agents/prompts` on a portal with **no auth** anywhere. | `agents/researcher.py:246-257`; `agents/browser.py:310,375`; `activity.py:22,102`; no `Depends(`/`LAB_TOKEN` in `portal/app.py` | Deliverable 6 is **not an addition** — it is closing an existing hole and *reducing* default capture. It moves up, not out. ARAIL runs on other people's machines. |
| V6 | **No TTFT exists on the agent path.** TTFT is implemented only in `research/mini_experiments.py`, `experiments/mlx_backend.py`, `experiments/bench.py`. `stream_complete` calls `track` only on the terminal `ModelResponse`; `complete()` has `latency_ms` (total) and nothing else. | `router/core.py:186-199`; grep `ttft` | The operator's one explicit metric ask ("time to first token is important") is unmeasured for agents. New code required — and it must not be faked from `latency_ms`. |
| V7 | **An existing UI lie.** `/api/agents/status` reports a per-agent `tokens` figure summed from `prompt_trace.max_tokens` — the *requested ceiling*, not usage. | `portal/app.py:5088` | The Agents page already shows the operator a number that means something other than its label. Fix or relabel; truth-in-UI (2026-07-23 sprint). |
| V8 | **The brief undercounts the acquisition paths and misses that one is out-of-process.** Not ~5 — ~11 sites: `librarian_scout.py:387`, `dictionary.py:393,420`, `world_routes.py:136,847,989,1001`, `skills/goal_parser/__init__.py:112`, `skills/goal_parser/_subprocess_runner.py:50`, `lab/tools/model_router.py:12,182`, plus `agents/{_builtin_drafter,browser,researcher}` `_get_router` and `deep_policy`. Four of them are not agents at all (world routes, dictionary, goal parser). | grep `ModelRouter(` / `_get_router` | The gateway's blast radius is roughly double the brief's estimate, spans a **process boundary**, and would drag in non-agent surfaces. Strongest argument for deferring it. |
| V9 | **A `/metrics` Prometheus endpoint already exists**, fed by `scheduler.per_label_snapshot()` which says in its docstring it is "for Prometheus /metrics exposition". | `portal/app.py:10774`; `portal/scheduler.py:284` | D16 ("no Prometheus endpoint") must be read as *don't build a metrics product*, **not** as "remove this". Flagged for the architect so nobody deletes a working surface in the name of a decision record. |
| V10 | **`inference_slot` already records what the "single-lane bridge" wants** — per-label `wait_ms`/`run_ms` p50/p95, `in_flight`, `pending`, `completed_5m` — keyed by call-site label (`chat-stream`, `world-forge`, `term-draft`, `tier0-keepwatch`), not agent id. | `portal/scheduler.py:179-281` | Half the bridge visual is free. Agent lanes are absent from it only because of V3. |

---

## User

**Not the friends-and-family audience. The operator.** Being honest about this
is the point of the section.

Charles, on the M5 Max / 36 GB maximus lab, with the Admin tab open in a second
window, about to give an always-on agent a voice, a budget and a pair of hands
on machines he does not control. Concretely, today he cannot answer any of these
questions without reading `activity.jsonl` by hand or attaching a debugger:

- "Buddy just said something. Which brain answered — the 1B or the deep model?
  How long did the first token take? If it was the 1B, *why* was deep declined?"
  (V2: nothing is emitted at all.)
- "My chat turn just stalled for four seconds. Was an agent holding the
  allocator?" (V3: agent calls are not in the slot, so the contention snapshot
  cannot see them.)
- "Which agent spent those tokens?" (Everything is one `source="agent"` bucket —
  `router/core.py:162` and `costs.py:354` `calls_by_source`.)
- "I want the lab to shut up for ten minutes." (V4: there is no switch that
  makes Buddy stop.)

Admin is maximus-only (`_TIER_SURFACES`, `portal/app.py:191`). The lane view
will be looked at by approximately one person. **That is fine for a first
slice, and it is also the reason to cut UI ambition hard** — polish spent on a
one-viewer screen is polish not spent on P3, which every user sees. I am
accepting an operator-serving sprint on the condition that it is one sprint and
that its win condition is "the operator can answer a question he cannot answer
today", *not* "the lab feels alive to a visitor". Feeling alive to a visitor is
P3–P4 and must not be smuggled into P1 as a justification for a scrubbing
swimlane.

## Problem

The operator asked for observability. The underlying pain is narrower and
sharper than that: **he is about to put a voice, a budget and hands on an agent
he cannot see, cannot time, cannot attribute and cannot silence.**

Not "there are no metrics" — there are a `/metrics` endpoint (V9), a cost
tracker, a 200-event activity ring, per-label slot percentiles (V10) and a
Prompt Inspector. The failure is that none of them are keyed to *an agent
making a decision*, and the one agent that matters emits nothing (V2).

The second-order pain, and the real reason D18 ordered this first: the next
three sprints all make Buddy *louder*. Event-driven voice (P4) removes his
300 s cooldown. The prep queue (P6) spends deep-model compute while the user is
away. Hands (P5) let him act. Each of those is a change in how much compute and
how much of the user's attention an unobserved, unbounded, unsilenceable agent
consumes. The first time a proactive line makes a user's chat turn stall, the
operator will debug it by guessing — slot? model? memory pressure? the 1B being
slow? — because none of the four leave a trace today.

And one pain the operator did not raise, which the code does (V5): the lab
already writes every researcher and browser prompt and response to disk in the
clear and serves them over an unauthenticated localhost endpoint. ARAIL is a
blueprint that runs on other people's machines. That is a live defect, in scope,
and it is why the flight recorder moves *up* the list rather than off it.

## Win condition

Pre-committed, measured on **maximus** in this worktree, on the operator's
machine, at end of sprint. Five thresholds; all five are pass/fail.

**W1 — Every agent model call is visible, within 2 s, with seven fields.**
A scripted run that triggers (a) one Buddy proactive line, (b) one Researcher
LLM call, (c) one Librarian/dictionary call produces, on `Admin → Agents`,
**one lane entry per call within 2 seconds of the call returning**, each
carrying all seven of: agent id · model · backend · brain+effort label ·
TTFT *or* an explicit `n/a — non-streamed` · tokens in/out · the
`deep_policy.explain()` reason code **verbatim** when deep was declined.
Seven non-null fields, 3/3 calls. A blank is a fail; an honest `n/a` is a pass;
a TTFT synthesised from `latency_ms` is a fail (V6).
*Baseline today: 0/3 calls produce any lane entry; Buddy produces no event of
any kind (V2).*

**W2 — Per-agent attribution replaces the single bucket.**
After one hour of ordinary lab uptime, the cost surface shows **≥ 2 distinct
agent ids** and **0 calls** in a generic `"agent"` bucket.
*Baseline today: exactly one bucket, `"agent"`, for all agents
(`router/core.py:162`).*

**W3 — The kill switch actually holds.**
Flip the visible "Hold all agents" control in Admin; observe 60 s. **Zero** new
agent-sourced inference traces recorded in that window. The SRE crash watcher is
the one permitted exemption **only if** the operator says so (open question OQ3)
and the UI states the exemption on the switch itself.
*Baseline today: 15 of 19 agent modules ignore `jobs_halted()` entirely; Buddy
keeps speaking on the fast model (V4).*

**W4 — Default capture shrinks.**
With the flight recorder **off** (the default on a fresh lab), a 30-minute run
with the researcher active writes **0 prompt bodies and 0 response bodies** to
`lab/data/activity.jsonl`. With it on, bodies are captured, size-capped, and
`secrets.env` material is redacted (asserted by a QA test that plants a
key-shaped string in an agent prompt and greps the log).
*Baseline today: up to 5000 chars of prompt+response per researcher/browser
call, always on, unredacted, on disk (V5).*

**W5 — The witness test (this is the "alive, not boring" one).**
The operator opens the lane view on an otherwise idle lab and, **without
opening a log file or a terminal**, narrates out loud for 10 minutes what each
agent is doing and why deep was or wasn't used — then records pass/fail in
`SPRINT.md` in his own words. One named user, one sentence, signed.

This is deliberately the least rigorous of the five and the only one that
tests D16's "fun views versus boring metrics". "Fun" is not measurable; "can be
narrated from the screen alone" is. If W1–W4 pass and W5 fails, we shipped a
metrics product — exactly what the operator said he didn't want — and the next
sprint's first task is a rewrite of the view, not more instrumentation.

**Not in the win condition, on purpose:** any latency or throughput improvement;
any new Buddy behaviour; any priority ordering between agents and chat (W1 only
*records* overlap — see §Wedge).

## Wedge

### Is P1 one sprint or three?

**Three, as written.** Deliverable 2 alone is ~11 call sites across 8 modules,
one of which is a separate process, four of which are not agents (V8).
Deliverable 3 says "every agent … including user-defined agents from the
loader" — unbounded UI over a roster that `/api/agents/status` currently
hardcodes to five. Deliverable 4's per-agent budgets cannot be enforced without
2, and cannot be *numbered* without the data 1 produces. Deliverable 5 is a
scaffold with no consumer until P5.

### The wedge: instrument the chokepoint that already exists, then let the data decide about the gateway

**Keep (four of six, one of them reshaped):**

1. **Trace + per-agent attribution at `ModelRouter.complete/stream_complete`.**
   The spine. V1 says this is one place, not eleven. A call-scoped context
   carrying `(agent_id, brain, effort, foreground, trace_id)` that the chokepoint
   stamps onto the trace and onto `billing_source`. Includes the TTFT
   first-chunk timestamp in `stream_complete` (V6) and an honest `n/a` for
   non-streamed calls.
2. **One Admin lane view, shrunk.** Fixed roster of the agents that actually
   call a model. Live lanes, current state, brain + effort, last call's TTFT and
   tokens, `explain()` reason code verbatim, and "why?" drill-in to the trace.
3. **The kill switch, made real and visible.** `scheduler.halt_all_jobs()` and
   `POST /api/jobs/halt` already exist; the work is compliance (V4) and a
   control in Admin. Cheapest and strongest guardrail in the six.
4. **Flight recorder — reframed as reducing default capture, not adding it**
   (V5). Off by default, size-capped, redacted. This is a security fix, and it
   is the one deliverable with a user-visible cost (see OQ1).

**Plus one thing the brief does not ask for, which earns the deferral of the
gateway:** at each agent call, read `scheduler.snapshot()` and record whether
the inference slot was held by someone else at that moment. Read-only, a few
microseconds, no ordering attempted. **This is the measurement that decides
whether the gateway is urgent or theoretical** (see DE1).

**Cut / defer, with reasons:**

| Deliverable | Verdict | Why |
|---|---|---|
| **(2) The agent-inference gateway** | **Defer** to a follow-on sprint, gated on DE1's data | Highest blast radius in the programme: ~11 sites, 4 non-agent, 1 cross-process (V8). And the value it claims — observe + bound — is ~80% available at a chokepoint that already exists (V1). Building it first means the riskiest refactor in the programme lands with **no telemetry to tell you whether it regressed anything**, which is precisely the inversion D18 exists to prevent. Instrument first, refactor second: that is D18 applied to the gateway itself. |
| **(4a) Per-agent token/compute budgets** | **Defer** one sprint | Cannot be enforced without (2), and cannot be *numbered* without a week of the per-agent data (1) produces. Any number picked today is invented. Keep the kill switch (4b), which needs neither. |
| **(5) Typed-action allow-list scaffold** | **Cut entirely** | No consumer until P5. A scaffold with no caller is unfalsifiable — nothing can prove it right or wrong — and will be rewritten the moment the first real action arrives. Zero cost to defer; the *constraint* ("model output can propose, never execute") lives in the brief and needs no code this sprint. |
| **(3) Swimlane scrub, prep-queue conveyor, memory gauges, user-defined-agent lanes** | **Cut from (3)** | Timeline scrubbing is a P4 want. There is no prep queue until P6 — a conveyor for an empty queue is a mock. Gauges duplicate the existing admin Performance card. And per V-note: one viewer. Ship lanes that update live; earn the scrub. |
| **Priority ordering (agents into `inference_slot`)** | **Cut; record only** | V3: this is not a re-ordering, it is inserting sync-called-from-thread work into an async semaphore — a real design problem, not a config change. P1 *measures* the overlap; the fix is scoped by the measurement. |

**Shippable in one sprint, on the operator's own machine, with no cloud
account** — the whole thing runs against the local lab at `127.0.0.1:8080`.

## Disconfirming evidence

Pre-committed. Each names the decision it would reverse.

**DE1 — Is the gateway premature?** *(the operator asked me to answer this; I am
answering it with a measurement rather than an opinion)*
From the overlap counter in the wedge, over one week of the operator's ordinary
use:
- **< 5 %** of agent calls overlap a slot held by another caller → the gateway
  and priority work are **deferred indefinitely**; P3 proceeds without them. The
  contention risk was theoretical.
- **5–25 %** → gateway stays where I put it: the sprint after P2's bake-off.
- **> 25 %** → gateway is **promoted ahead of the Buddy panel (P3)**, and I was
  wrong to defer it.

**DE2 — Does the chokepoint theory hold?** If traced agent calls cover **< 80 %**
of non-`ui` calls (compare trace count against the `cost_tracker` delta for
non-ui sources over the same window), V1 is wrong, the acquisition paths leak,
and the gateway is required rather than deferred. The known leak is the
goal-parser subprocess (V8) — which must render as an explicit "untraced" lane,
never silently omitted (truth-in-UI).

**DE3 — Was observability the problem at all?** If the operator opens the lane
view **fewer than 3 times** in the first week post-ship, and W5 fails, then
"the lab feels dead" was never an observability problem. We stop investing in
views, go straight to P3, and accept flying partly blind on the Buddy panel.
This is the one that would falsify the whole of P1.

**DE4 — Is the instrumentation too heavy?** If the trace adds **> 5 ms** to
agent-call p95, or **> 2 %** to the portal fast-path p95
(`scheduler.snapshot()["fast_path_ms"]`), cut to counters only and drop the
per-decision trace. An observability layer that changes what it observes is
worse than none.

**DE5 — Did the flight recorder break the operator's daily tool?** If, with
bodies off by default, the operator flips the recorder on within the first
three sessions and leaves it on, then off-by-default is wrong for his workflow
and the right answer is redact-and-truncate-on-by-default (OQ1). Not a kill —
a correction.

## Displacement

**Within arail — the honest cost:**

- **P3 slips by the length of this sprint.** P3 *is* the operator's stated pain:
  "right now it feels dead and boring", and D11's fresh-install-to-goal-in-5-min.
  **P1 does not fix that for any user.** It fixes it for the operator, on a
  maximus-only screen. Anyone who says P1 "makes the lab visibly alive" for a
  *user* is overselling it, and I will not write that sentence in a win
  condition.
- P2's brain bake-off slips one sprint — but it gains P1's instruments, which is
  the trade D18 already made and which V6 justifies: **the bake-off's headline
  measure is TTFT, and TTFT does not exist on the agent path today.** Running
  the bake-off before P1 would mean hand-timing it.
- Ingress Track C3/C4 stays parked (already in the brief; QA: do not build on
  the keyword router).
- ROADMAP's Whisper toast scaffold stays superseded by the panel.
- The Agents page's existing Prompt Inspector changes behaviour (V5/W4) — a
  regression in the operator's own daily tool, traded for a security fix. OQ1.

**Cross-product:** nothing in QueueLLM, Nucleus, DDaC or geoai is touched — no
new cross-repo edge, so no versioning obligation under the workspace rule. But
the operator's attention is one person's attention: QueueLLM's `GA-GATES.md`
scorecard and DDaC's half-landed rename get none of it this sprint. The P7
dependencies (QueueLLM `gemma4_assistant` drafters, vision tower) are correctly
not started.

**The cost of NOT doing P1 first** — the question I was asked, answered with
the verified facts rather than the brief's framing:

Building the Buddy panel on unobserved agents means shipping an agent that, on
the day it first misbehaves, is (a) emitting **nothing** about its own inference
(V2), (b) contending for the Metal allocator **outside** the queue that exists
to serialise it (V3), (c) **unstoppable** by the switch labelled "hold all jobs"
(V4), and (d) writing user content to disk in the clear (V5). Four independent
holes, all verified, all in the exact path P4–P6 are about to load. The
programme's next three phases each increase Buddy's compute and attention
footprint. D18 is right, and it is right for stronger reasons than the brief
states.

**What I am NOT willing to trade for that:** P1 running long. If the cuts above
are re-litigated back in, the sprint becomes three, P3 slips a quarter, and the
operator spends a quarter building instruments for a lab whose central feature
still does not exist. The cuts are the condition of the proceed.

## Open questions that genuinely need the operator

Recorded here rather than blocking, per instruction.

- **OQ1 (needs an answer before build).** The flight recorder means the Prompt
  Inspector goes **empty by default** on a fresh lab (V5/W4). Accept that, with
  an empty state reading "flight recorder off — flip to capture prompt bodies"
  (my recommendation), or do you want a third setting — redacted + hard-truncated
  bodies, on by default? This is a call about your own daily tool and I should
  not make it for you.
- **OQ2 (needs a number, or accept the deferral).** Per-agent budgets are
  deferred because there is no data to set a number from. If you want a hard
  ceiling in P1 regardless, name N tokens/hour now; otherwise P1 ships
  observation-only bounds (kill switch + halt compliance) and budgets land next
  sprint with real numbers behind them.
- **OQ3 (needs an answer before build).** Does "hold all agents" silence the
  **SRE crash watcher**? Halting the one agent whose job is to tell you the lab
  is broken is a defensible choice *and* a dangerous one. Either answer is fine;
  the switch must state which it does.
- **OQ4 (can be decided during build).** The lane roster: fixed list of
  model-calling built-ins for P1, with user-defined loader agents rendering as a
  generic lane? Or must user-defined agents be first-class now? I recommend the
  former; the latter is unbounded.

---

## Recommended next step

**PROCEED to `/architect` design mode with this as the spec — conditional on the
four cuts.**

The wedge is four deliverables, not six: trace + attribution at the existing
`ModelRouter` chokepoint · one shrunken Admin lane view · the kill switch made
real · the flight recorder as a default-capture *reduction*. Plus the overlap
counter, which is what buys the gateway's deferral. Cut: the gateway, per-agent
budgets, the typed-action scaffold, and the control room's scrub/conveyor/gauges.

The condition is not decorative. If the gateway comes back into this sprint, the
recommendation reverts to **defer** — because the riskiest refactor in the
programme would then land with no telemetry to tell us whether it broke
anything, which is D18 violated in the name of D18.

---

## Notes for the architect

Concerns noticed while verifying. Not designs — yours to solve or reject.

1. **The chokepoint and how context reaches it.** `ModelRouter.complete`
   (`router/core.py:145`) and `.stream_complete` (`:172`) are the funnel.
   `from_backend` (`:101`) already carries optional `provider`/`entry_id`/`tab`
   attributes forwarded to `cost_tracker` — that is the existing precedent for
   attaching call metadata, and extending it may beat a contextvar. If you do
   choose a contextvar: `asyncio.to_thread` copies the current context, so
   Buddy's `_call_model_for_dream` path (`_builtin_buddy.py:1569`) should carry
   it — **verify, don't assume**, and note it does *not* cross (2).
2. **One inference path is out-of-process.**
   `skills/goal_parser/_subprocess_runner.py:50` constructs its own
   `ModelRouter` in a child process. Its stdin protocol is already JSON, so a
   `trace_id` rides along cheaply. If you choose not to, the lane must render it
   **"untraced"** rather than omit it — truth-in-UI, 2026-07-23 sprint. This is
   also DE2's known leak.
3. **TTFT must be honest.** `stream_complete` calls `cost_tracker.track` only on
   the terminal `ModelResponse` (`:185-199`); a first-chunk timestamp is new
   code. Non-streamed `complete()` has no TTFT — do **not** derive one from
   `latency_ms`. Prior art for the measurement exists in
   `experiments/mlx_backend.py:224` and `research/mini_experiments.py:269`;
   note `mlx_backend.py:261-262` already renamed its own TTFT to `prefill_ms`
   internally while keeping `ttft_ms` for UI compat — don't repeat that
   ambiguity in a new field.
4. **Existing UI lie to fix or relabel.** `/api/agents/status` (`app.py:5088`)
   sums `prompt_trace.max_tokens` and presents it as per-agent `tokens`. It is
   the requested ceiling. Real usage is available as `resp.tokens_used`.
5. **Halt compliance is per-agent work unless it moves.** Only `researcher.py`,
   `job_daemon.py`, `dream_daemon.py`, `_builtin_librarian.py` check
   `jobs_halted()`. Adding the check to each of the other 15 loops is N edits
   and N chances to miss one. Consider instead **refusing an agent-sourced call
   at the chokepoint while halted** — one edit, cannot be forgotten by a future
   agent author, and it composes with the loader contract (user-defined agents
   get it for free). Trade-off: an agent mid-loop will see a failure rather than
   a clean skip, so the refusal needs a distinguishable error the agents already
   tolerate (they all swallow broadly today — check each). OQ3 decides the SRE
   exemption.
6. **Do not put agent calls in `inference_slot` this sprint.** `inference_slot`
   (`portal/scheduler.py:179`) is an async CM with a 300 s acquire timeout;
   agent calls are sync, often from `to_thread`. Bridging is a real design
   problem (sync-from-thread acquisition, plus a second capacity semantics
   question: capacity is 1 by default, so admitting agents *will* serialise them
   behind chat by construction — which is the desired priority, and also a
   latency change to every agent). P1 records overlap via
   `scheduler.snapshot()`; DE1 scopes the fix.
7. **Prefer one persistence path.** `activity_log` is a 200-event ring plus a
   10 MB rotating jsonl (`activity.py:39,92-103`), and a `prompt_trace`
   convention is **already in use** by researcher and browser with two consumers
   (`app.py:5088`, `:5171`). If you introduce a second trace store, the design
   must say why two persistence paths beat extending one. Note the ring is 200
   events — a chatty trace will evict the operator's activity history, which is
   an argument *for* a separate bounded store. Your call; state it.
8. **`/metrics` already exists** (`app.py:10774`, fed by
   `scheduler.per_label_snapshot()`, `portal/scheduler.py:284`). D16's "no
   Prometheus endpoint" means don't build a metrics product. It does **not**
   authorise removing this. Neither extend nor delete without asking.
9. **Roster boundary.** `/api/agents/status` hardcodes five agents; the loader
   supports arbitrary `lab/pkb/agents/<id>/`. Whatever roster you choose, an
   unknown agent id must render as a generic lane rather than vanish — a
   silently-missing agent is worse than an ugly one. OQ4.
10. **Per-World isolation is free, and must stay free.** Concurrent Worlds run
    one process per World (`docs/concurrent-worlds.md`), and `PKB_ROOT` is a
    module constant never rebound in-process — the invariant `pkb_index.py`
    documents. A module-global trace store is therefore per-World by
    construction. Do not add a cross-World aggregate; it would break the same
    invariant for the same reason.
11. **Portal has no auth.** No `Depends(`, no token check anywhere in
    `portal/app.py`. Whatever the flight recorder captures is readable by any
    local process via `/api/agents/prompts`. In-scope for the redaction design;
    out of scope to fix auth this sprint, but the trace endpoints should not
    widen what is already exposed.
12. **QA allocation note for the orchestrator.** arail's standing split is
    30 % setup / 30 % Buddy / 20 % security / 10 % happy / 10 % regression. This
    sprint ships **no new Buddy behaviour**, and its riskiest change is W4's
    redaction. Recommend tilting toward security + regression for this sprint
    only, and documenting the tilt in `SPRINT.md` rather than silently
    reallocating.
