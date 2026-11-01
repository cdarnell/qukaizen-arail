# BRIEF — ARAIL end-to-end integrity review: World persistence and the "crash fest" perception

> The orchestrator's briefing artifact for every persona in this sprint. All
> file:line anchors and counts below were verified on disk 2026-08-06 on
> branch `qukaizen/arail-world-selection-ui-6603eb` (worktree
> `eloquent-lederberg-6aeb3b`) and against the operator's real checkout at
> `/Users/netsushi/ProJects/qukaizen-arail`; re-verify any anchor you build
> an argument on, especially line numbers, since three PRs landed on `main`
> in the days immediately before this brief was written.

## The operator's ask, verbatim (paraphrased from two related messages)

> "Let's do another full review of ARAIL with respects to the world and
> management of. We just went through a series of bug fixes and want a plan
> to review ARAIL end to end. Ensure general integrity. Unfortunately ARAIL
> has been a crash fest and people are getting frustrated. Allow Worlds to
> load and be persistent."

And, immediately before that, a concrete field report: the operator mounted
the Debt Finance World, set an Autoresearch goal ("find the best rates on
loans to consolidate my debt"), and asked whether the Researcher agent would
actually go find real loan rates — while the Autoresearch page showed the
new goal's experiment list populated with five experiments from two
unrelated, months-old goals ("make aeroLLM the most efficient inference
engine", "learn math"), all marked "✓ supported."

## What this brief is not

This is not a bug report for a single defect. Three defects **already found
and fixed** in the four days leading up to this brief are folded in as
context, not as open work:

1. **`ModuleNotFoundError: dac_world`** — a 27-hour, ~3,570-restart crash
   loop on 2026-07-28/29 (portal died on import, something respawned it
   every 15-30s, `portal.err.log` grew to 18MB). Fixed
   `e157048` (vendor `dac_world`), 2026-07-31. **This is almost certainly
   the majority of what "crash fest" refers to** — it is the single largest
   number in the evidence (§1 below) by two orders of magnitude.
2. **The World picker was invisible on macOS**, and had no memory of the
   last-launched lab. Fixed in `#161` (merged `da10edd`, 2026-08-02) — the
   picker's prompt now renders (a stderr-pipe/no-newline interaction was
   swallowing it), it defaults to whatever you launched last, and a new
   `./arailctl switch` verb collapses the stop→pick→start dance into one
   command.
3. **The Compiled-KB gate silently went to zero** after a World switch —
   554 of 556 approvals pointed at files `_sweep_other_worlds()` had
   deleted, and because the retrieval gate fails closed, `search_for_agents`
   returned nothing for ANY query in ANY World for two weeks, with no error
   anywhere. Fixed in `#163` (merged `411cd21`, 2026-08-03) — mount/swap/
   unmount now reconcile the manifest, `./arailctl pkb prune` is the manual
   door, `doctor` reports dangling counts.
4. A same-week cluster of three smaller crash fixes also landed:
   `#162` (experiment tracker sort crash on missing `created_at`), `#165`
   (dashboard crash on missing `hypothesis`), `#164` (chat hardcoded portal
   port 8080). All merged.
5. **The Autoresearch goal-scoping display bug** from the operator's own
   field report above — `GET /api/experiments` returns the whole corpus,
   unfiltered, and the "Experiments testing your goal" section rendered the
   raw top-5 regardless of which goal designed them. Fixed in `#166` (open,
   not yet merged as of this brief) using data that already existed
   (`goal.experiments`, populated by `goal_store.link_experiment()` all
   along) — no backend change needed for the display; see §4 for the
   backend gap this uncovers.

**Every one of these five was found by the operator hitting it in normal
use, not by CI or a test.** That pattern — not any single defect — is the
actual thing this sprint should fix: ARAIL has no regression net around
World-switching, so every cross-cutting piece of state (KB approvals,
experiment records, goal records, now confirmed: also cost counters,
possibly others not yet audited) gets to independently discover, in
production, whether it survives a mount/unmount/swap.

## The operator's direct question, answered (context for VISION, not open work)

**Will the Researcher agent actually go find real loan rates for a
finance goal? No — and this is by design, not a bug.**

- The Autoresearch experiment engine (`src/arail/research/mini_experiments.py`)
  recognizes exactly four measurable archetypes — inference throughput,
  prompt phrasing, retrieval quality, game-config optimization — and has
  **zero network imports** (its own docstring: "all airgapped-safe"). A
  finance hypothesis lands in `"unmeasured"` and is honestly reported as
  "isn't measurable on-device."
- The Debt Finance World's `capabilities.json` declares knowledge-grounding
  only — no `scout.*` capability the way the Video Games World has one.
- The one path that *can* touch a real lender page — the Librarian agent's
  horizon watch (`agenda_watch.py`, driven by its own ~24h tick, not by any
  goal) — requires per-URL operator consent in the portal, runs at most
  once per feed per day, and even then the code that reads an APR out of
  the fetched page (`_builtin_debt_advisor.py`) is explicitly written to
  **never state a number**: found values land in an unverified human-review
  queue, cited only by "World/watch/feed/date," never as a claimed rate.

This is a correct safety design (never assert an unverified financial
number as fact) that is **advertised in a way the UI doesn't fully own up
to** — the World-of-Debt-Finance PR that shipped this (#159) is titled
"deal-finding," and nothing in the goal-setting flow tells the operator
"this loop measures your local hardware, it does not shop for rates."
§5 below scopes closing that expectation gap, either by messaging honestly
or by building the real thing later — the visionary should decide which.

---

## §1 — Crash evidence, quantified (for QA's regression-test target list)

Surveyed `lab/logs/*.log`, `lab/data/activity.jsonl*`, and `git log` on the
operator's real checkout. Full method and counts are reproducible; the
headline numbers:

| Signal | Count | Window | Status |
|---|---|---|---|
| `ModuleNotFoundError: dac_world` tracebacks | 4,077 | one file, ends Jul 31 08:24 | **fixed**, `e157048` |
| Portal boot events (`Agent loader discovered 4…`) | 2,851 on Jul 28 alone (avg one restart per 15-30s, all day) | Jul 28-29, ~27h | **fixed** (same root cause) |
| `"portal may be down"` SRE watchdog fires | 5,441 historical + 289 current (both post-fix Worlds) | ongoing, lower rate | **investigate** — see below |
| `Agent 'browser'/'curator'/'researcher' failed to load` | 3,910 historical, 9+9+9 in the current 2-day log | every single boot since **2026-05-01** | **not a crash** — chronic, see §3 |
| `neo4j driver not installed` skip | 4,876+ | every boot | graceful skip, not urgent |
| OOM / `Killed` / `MemoryError` | 0 hits | — | no evidence, deprioritize |

**Open item for QA:** 289 "portal may be down" fires in the *current*
(post-`dac_world`-fix) activity log is not zero. Before this sprint closes,
determine whether that's the SRE watchdog's normal false-positive rate
(brief network hiccups, polling races) or a live, still-unfixed flapping
issue. This brief doesn't have that answer yet — it's the single largest
unresolved number in the evidence and should be the first thing the
architect's failure-mode pass looks at.

**Git evidence corroborates a World-switching-specific cluster, not a
diffuse one:** every crash-shaped fix commit from Aug 2-3 (five of them,
listed above) is downstream of the Concurrent-Worlds/World-selection
architecture landing Jul 28-29. Nothing in the last three weeks points at
portal boot, the model backend, or memory pressure as an *independent*
source of instability once `dac_world` is excluded.

## §2 — The pattern behind items 3 and 5: World-scoped state has no owner

This is the throughline the visionary should frame the sprint around.
`world_mount.py`'s `_sweep_other_worlds()` deletes the previous World's
staged files on every mount — correct, deliberate, and the whole point of
"a World IS the lab's dataset." But at least two other subsystems keep
their own state that a mount/switch should also touch, and didn't, until
each was fixed by hand after being reported in the field:

- **Compiled-KB approvals** (fixed, #163) — pointed at files the sweep had
  deleted; nothing reconciled them.
- **Autoresearch goals/experiments** (display fixed, #166; **data model
  still global** — see §4) — not swept at all; a goal set under one World
  keeps running, and its experiments keep accumulating, after you've
  switched to a different World entirely.

**Confirmed NOT yet audited in this session** (candidates for the same
class of bug, unverified either way):

- `lab/data/costs.json` (the header's TOKENS/CLOUD EQUIV/NET SAVED/
  INFERENCES numbers) — confirmed to be a **single global lifetime
  counter**, never reset, never scoped to a World. `NET SAVED` in
  particular accrues ~$0.67/day from wall-clock alone
  (`_subscription_accrued_usd`, a `$20/mo` estimate prorated by uptime)
  regardless of whether the lab does anything. Whether this *should* be
  per-World is a real product question (§5), but even leaving it global,
  the fact that "NET SAVED $67" looks like a measurement and is actually
  dominated by a subscription-cost estimate accruing since April is a
  separate honesty concern worth the architect's attention.
- Chat conversation memory (`lab/pkb/conversations/`) — CLAUDE.md states
  it is PKB-rooted and gated; not verified in this session whether a World
  switch touches, should touch, or correctly avoids touching it.
- `lab/data/agent_redirects.json`, any other `lab/data/*.json` singletons —
  not inventoried. The architect's first job should be a complete list of
  everything under `lab/data/` and `lab/pkb/` that is *not* already known
  to be per-World-instance (per `docs/concurrent-worlds.md`'s own
  inventory) or already fixed, so nothing else is discovered by an
  operator instead of by this review.

## §3 — Chronic, non-crash gap: three dead agent-loader stubs

`lab/pkb/agents/{browser,curator,researcher}/` each contain only an
`AGENT.md`, no implementation — confirmed on disk. This is **unrelated to
the real Researcher** (`src/arail/agents/researcher.py`, imported directly
by the portal, which is what actually ran the operator's finance goal) —
it's a different loader (`src/arail/agents/loader.py`), logging "failed to
load" on every boot since **2026-05-01**, three months of pure log noise.

Not urgent, but cheap to close and worth doing in this sprint: either
finish these three (if they were meant to be real pkb-agent-loader
entries distinct from their `src/arail/agents/*.py` namesakes) or delete
the stub directories. Leaving three false "Agent failed to load" lines in
every boot log for three months is exactly the kind of thing that makes a
lab feel unstable even when nothing is actually broken.

## §4 — The product decision the operator has already made

Asked directly: **should a research goal be scoped to the mounted World,
the way the Compiled-KB gate now is?** Operator's answer: **yes** —
switching Worlds should switch (or clear) the active goal, matching the
single rule "the lab reflects the mounted World" that #163 already
established for knowledge.

This is bigger than the #166 display fix and should NOT be done as a
follow-up patch — it's a data-model change (`goals.py`'s `current.json` /
`history/` need a World dimension; `ExperimentTracker` records need to
either carry a `world` field or live in a per-World subdirectory the way
`lab/instances/<slug>/` already isolates other per-instance state) and
touches the goal-setting API, the Autoresearch page, and possibly the
dashboard's "Draft Swarm Plan" surfaces QA found failing in an unrelated
pre-existing way. This is architect-sized work, not builder-sized.

## Non-goals (explicit, so the sprint doesn't sprawl)

- **Not building real loan-rate fetching.** The safety design (never state
  an unverified number as fact) is correct and should not be weakened.
  §5's scope is *messaging*, not a new scout capability — building a real
  `scout.*` capability for Debt Finance, if wanted, is its own future
  sprint with its own threat model, same posture as `#159`'s and the
  concurrent-worlds sprint's explicit deferrals.
- **Not re-touching `_sweep_other_worlds()`.** It encodes the dataset
  invariant correctly; every fix so far (and the goal-scoping work ahead)
  reconciles OTHER state against it, never modifies it.
- **Not re-opening the browser-launcher threat-model line** (`ARCHITECTURE.md
  §5.3`, the concurrent-worlds sprint's refusal to let a loopback endpoint
  spawn processes). Nothing here touches that boundary.
- **Not a full security review.** This is an integrity/stability review —
  crash surface and cross-World state correctness — not a threat-model
  audit. If the visionary thinks one is warranted, that's a separate ask.

## Suggested phases for `/sprint`

1. **Visionary** — frame the win condition. Candidate framing: "an operator
   can mount, switch, and unmount Worlds all day without ever discovering a
   fourth piece of state that didn't survive the switch, and every boot log
   is honest about what's actually wrong." Decide whether §5 (messaging vs.
   building real deal-finding) is in scope for this sprint or deferred.
2. **Architect (design)** — (a) complete the `lab/data/`+`lab/pkb/`
   state inventory from §2 and classify each item World-scoped/global/
   needs-a-decision; (b) design the goal/experiment World-scoping data
   model from §4; (c) investigate the 289 residual "portal may be down"
   fires from §1 and determine if it's a live bug; (d) decide the fate of
   the three dead agent stubs from §3.
3. **Builder** — implement per the architect's spec. Expect this to span
   several PRs given the surface area (goals.py, ExperimentTracker, the
   research.html/dashboard templates again, possibly costs.json).
4. **Architect (review) → QA** — standard gate. QA should specifically add
   a regression class that's been entirely absent so far: an automated
   mount→switch→unmount cycle test that asserts *every* piece of
   World-scoped state (KB approvals, goals, experiments, whatever else the
   inventory finds) ends up correct afterward — the thing that would have
   caught #163, #166, and the goal-scoping gap before an operator did.

## Anchors worth re-verifying before design work starts

- `src/arail/world_mount.py:1343` `_sweep_other_worlds` — scope, unchanged
  by intent.
- `src/arail/goals.py:64` `set_goal`, `:216` `clear_current`, `:180`
  `link_experiment` — the data model §4 touches.
- `src/arail/skills/experiment_tracker/__init__.py:22` `create()` — no
  `world`/`goal_id` field currently.
- `src/arail/costs.py:187` persistence, `:286` subscription accrual,
  `:429` simulated billing — §2's cost-honesty item.
- `src/arail/agents/loader.py:247` — where the three dead stubs log their
  failure.
- `sprints/2026-07-28-concurrent-worlds/ARCHITECTURE.md` and
  `docs/concurrent-worlds.md` — the existing per-World-instance state
  inventory to extend, not duplicate.
