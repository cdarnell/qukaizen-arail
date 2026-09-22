# Build log: P1 — observability + guardrails over the agents that already exist

**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `600c9541`
**Sprint ledger:** [SPRINT.md](./SPRINT.md)
**Started:** 2026-09-21
**Worktree:** `~/ProJects/arail-buddy-wt`, branch `qukaizen/arail-buddy-front-and-center`

## Baseline (recorded before any change)

Test interpreter: `/Users/netsushi/ProJects/qukaizen-arail/.venv/bin/python -m pytest -q -p no:cacheprovider`.

- Suites directly adjacent to this sprint's touch surface — `tests/test_costs_persistence.py`,
  `tests/test_halt_persistence.py`, `tests/test_inference_scheduler.py`, `tests/test_scheduler.py`,
  `tests/test_activity_rotation.py`, `tests/test_loader_skills_only_agents.py`,
  `tests/test_agent_redirects.py`, `tests/test_agent_workflows.py`, `tests/test_agents_instruct.py`,
  `tests/test_debt_finance_agents.py`, `tests/test_debt_finance_agents_seed.py`,
  `tests/test_opencode_log_redaction.py`, `tests/test_pkb_retrieve_for_agents.py`,
  `tests/test_researcher_planning_trace.py`, `tests/test_skills_fold_into_agents.py`, `tests/router/`
  — **292 passed**, 0 failed.
- Full suite collects **5819 tests** (too large to run wall-to-wall per change; re-run the
  adjacent set plus each slice's own new tests after every slice, and the three known-failing
  tests explicitly before claiming "no regression").
- Known pre-existing failures, confirmed present and failing for the documented reasons, not
  introduced by this sprint and not to be fixed here:
  - `tests/test_aerollm_bundle_compliance.py::test_notice_byte_identical_to_sibling_when_available` — FAILED (confirmed)
  - `tests/test_model_hosting_reframe_qa.py::test_backends_raises_on_sentinel_before_any_load` — FAILED (confirmed)
  - `tests/test_docs_cross_links.py::test_cross_link_audit_all_internal_links_resolve` — **passed** in
    this worktree today (the "fresh worktree only" failure mode did not reproduce here — noted, not
    chased; not this sprint's concern either way).

## Plan

| # | Files | Change | Test | Commit ref |
|---|---|---|---|---|
| S0 | `tests/test_agent_context_propagation.py` (new, throwaway harness) | Prove A1-A4: contextvar survives `create_task`, `to_thread`, a `copy_context`-wrapped `Thread`, and a subprocess JSON round-trip. No production code. | itself | pending |
| S1 | `src/arail/agent_context.py` (new), `src/arail/agent_trace.py` (new), `src/arail/portal/scheduler.py` (+`slot_pressure()`), `src/arail/router/core.py` (chokepoint trace + billing_source rewrite), `src/arail/costs.py` (legacy `calls_by_source["agent"]` migration) | The spine: context + bounded trace store + chokepoint recording, no agent edits. Every agent call is `unattributed` after this slice (deliberate). | `tests/test_agent_context.py`, `tests/test_agent_trace.py`, `tests/test_router_trace_chokepoint.py`, `tests/test_costs_legacy_migration.py` (new) | pending |
| S2 | `src/arail/agents/loader.py` (L1), `src/arail/agents/dream_daemon.py`, `src/arail/agents/job_daemon.py` (L2), `src/arail/agents/researcher.py`, `src/arail/agents/browser.py`, `src/arail/librarian_scout.py`, `src/arail/agents/_builtin_drafter.py`, `src/arail/agents/forge.py`, `src/arail/agents/recap/router_adapter.py` (L3), `src/arail/world_routes.py` or equiv, `src/arail/dictionary.py`, `src/arail/skills/goal_parser/__init__.py` + `_subprocess_runner.py` (system_call + subprocess protocol) | Attribution wiring — after this slice `unattributed` should be 0 in a normal session | `tests/test_agent_attribution_wiring.py` (new), QA-BLIND-3 harness pieces | pending |
| S3 | `src/arail/router/backends.py` (verify stream-first-item contract only, no behaviour change unless needed), `src/arail/router/core.py` (TTFT fields in `stream_complete`), `src/arail/agents/deep_policy.py` (`ARAIL_AGENT_STREAM_FAST` fast-branch streaming) | Honest TTFT + one real streamed agent path | `tests/test_router_ttft.py` (new), `tests/test_deep_policy_stream_fast.py` (new) | pending |
| S4 | `src/arail/agent_context.py` (`halt_gate`/`speech_gate`/`AgentHeldError`/`HOLD_EXEMPT_SPEAKERS`), `src/arail/router/core.py` (halt_gate at chokepoint), `src/arail/agents/deep_policy.py` (early return, no double refusal), `src/arail/agents/_builtin_buddy.py`, `_builtin_librarian.py`, `_builtin_presence.py`, `_builtin_debt_advisor.py`, `_builtin_consolidation_analyzer.py` (speech_gate at proactive emit), `src/arail/agents/_builtin_drafter.py` (explicit `AgentHeldError` branch, F8) | The kill switch made real | `tests/test_halt_gate.py` (new), per-module survival tests, F17 copy+behaviour test | pending |
| S5 | `src/arail/redact.py` (new), `src/arail/agent_trace.py` (`capture_body` wiring, latching per F10), `src/arail/agents/researcher.py` / `browser.py` (stop writing bodies to `activity.jsonl`), `src/arail/portal/app.py` (`/api/agents/prompts` shape change, flight-recorder state + legacy-notice/purge), `src/arail/portal/templates/agents.html` | Flight recorder off by default, redacted, capped; legacy-bodies disclosure + purge | `tests/test_redact.py` (new), QA-BLIND-1 harness, `tests/test_flight_recorder.py` (new), F12 purge test | pending |
| S6 | `src/arail/portal/app.py` (4 admin endpoints, `_TIER_SURFACES`-gated), `src/arail/portal/templates/admin.html`, `src/arail/portal/static/` (lane JS, no `setInterval` poll), `/api/agents/status` V7 fix | Admin surface over the trace store | `tests/test_admin_agent_lanes_endpoints.py` (new), F13 gating test, no-poll static check | pending |
| S7 | `docs/agent-observability.md` (new), `docs/agents.md` (attribution + hold contract), `sprints/BACKLOG.md` | Docs + filed debt | none (docs) | pending |

Deviations from this table, if any, are recorded per-slice below, not silently absorbed.

## Execution

### S0 — Prove the attribution mechanism

**Result: A1-A4 all hold. Mechanism confirmed, no gap to surface.** On this
worktree's interpreter (CPython 3.11.15, macOS/Darwin arm64):

- A1 `asyncio.create_task` copies the calling `contextvars.Context` — confirmed,
  including that the copy is a point-in-time snapshot (a parent mutation after
  spawn does not leak into the already-spawned task).
- A2 `asyncio.to_thread` copies the context into its worker thread — confirmed.
- A3 a bare `threading.Thread` does **not** copy the context, and a
  `contextvars.copy_context().run(...)`-wrapped `Thread` **does** — both halves
  confirmed. The `spawn_thread` shim ARCHITECTURE.md prescribes is necessary
  and works.
- A4 `loop.run_in_executor` does **not** copy the context, and a static grep
  confirms zero uses of `run_in_executor` under `src/arail/agents/` today — no
  silent unattributed hop already exists.
- Subprocess: no automatic context sharing across a process boundary (expected,
  not an assumption to prove) — the JSON round-trip design (payload in via
  stdin, child sets its own contextvar, reports back) works end to end via an
  actual `subprocess.run` of a throwaway child script.

**Deviation from plan:** the architecture doc's own draft used
`@pytest.mark.asyncio`-shaped examples implicitly; this repo has no
`pytest-asyncio` installed and its existing async tests (e.g.
`tests/test_inference_scheduler.py`) use the `async def _scenario(): ...`
+ `asyncio.run(_scenario())` idiom instead. S0's tests follow that existing
convention, not a new one — no dependency added.

Tests: `tests/test_agent_context_propagation.py` — 9 new tests, all passing.
Adjacent regression check (`test_costs_persistence`, `test_halt_persistence`,
`test_inference_scheduler`, `test_scheduler`): 44 passed, 0 failed.

Commit: `ffcb1ba3`

### S1 — The spine: context + trace store + chokepoint, no agent edits

**Delivered:**

- `src/arail/agent_context.py` (new) — `AgentCall`, `current()`, `agent_call()`,
  `system_call()`, `spawn_thread()`, `to_subprocess_payload()`,
  `from_subprocess_payload()`, `billing_source()`, `call_site()`.
- `src/arail/agent_trace.py` (new) — `SCHEMA`, `record()`, `ring()`, `stats()`,
  `subscribe()`, `lanes_snapshot()`, `FIXED_LANES`, `_reset_for_tests()`.
- `src/arail/portal/scheduler.py` — added `slot_pressure()` (three-global read,
  no percentile computation; `snapshot()`/`per_label_snapshot()`/`/metrics`
  untouched).
- `src/arail/router/core.py` — chokepoint now calls `agent_trace.record()`
  exactly once per `complete()`/`stream_complete()` call (success, failure,
  or — once S4 lands — refusal), rewrites `cost_tracker.track(source=...)` via
  `agent_context.billing_source()` unless `self.billing_source == "ui"` (F19),
  and records the `held_by_other` slot reading. TTFT for `stream_complete` is
  deliberately left `ttft_status=None` (not yet measured) — S3's job, not
  S1's; `complete()`'s TTFT is unconditionally `ttft_status="non_streaming"`.
- `src/arail/costs.py` — one-time idempotent `calls_by_source["agent"]` ->
  `"agent:pre-p1-legacy"` migration on load (W2's precondition).

**Deviations from ARCHITECTURE.md, each with reason:**

1. **`AgentCall` gained a field not in the interface-contract's dataclass
   listing: `parent_agent_id`.** The contract's prose promises "a different
   agent_id nests ... and the record carries `parent_agent_id`" but the
   dataclass snippet shown doesn't list that field. Added it additively
   (default `None`) — required to keep the documented reentrancy promise;
   does not remove or repurpose any listed field. Flagged for architect
   review, not treated as a blocking gap (the promise is explicit prose,
   the omission reads as an oversight in the snippet, not a contradiction).
2. **`from_subprocess_payload()` gained an optional `default_label` kwarg**
   (contract shows a single positional `d: dict` param). The wire payload
   per contract #9 carries only `{trace_id, agent_id, brain, effort}` — no
   label — so a child with no `agent_id` (the common case for the
   goal-parser, which is a system caller, not an agent) has nothing to name
   itself with unless the real call site supplies one. Additive, defaults
   to `"subprocess"`; existing single-argument calls are unaffected.
3. **`agent_trace.lanes_snapshot()`'s `SYS_LANES` roster (`world-forge`,
   `dictionary`, `goal-parser`, `recap`) is inferred, not read verbatim from
   ARCHITECTURE.md.** The doc says "sys:* lanes for the four non-agent
   callers" but names only three explicitly (`world_routes`, `dictionary`,
   `skills/goal_parser`) in the L3 attribution section; the fourth is
   inferred from the `recap/router_adapter.py` L3 site (which sets a
   depth contextvar, not an agent id, so it reads as a system caller).
   **Flagged for architect review** — not a blocker: an unconfirmed label
   only affects a display string, never hides a lane (the mandatory
   unattributed/generic fallbacks still catch anything unnamed).
4. **`lanes_snapshot()`'s `librarian` empty-reason is the generic "no calls
   yet this session", not the doc's own claimed "does not call a model".**
   Read `_builtin_librarian.py` directly: its `scout_once()` calls
   `librarian_scout.scout_mounted_world()` which calls `draft_proposal()`,
   which does reach `router.complete()` when a World is mounted with
   candidate terms — i.e. librarian *can* call a model, unlike `sre`
   (zero router references, verified) and `presence` (a runtime-profile
   observer with no router reference, verified). Kept the accurate,
   conservative label rather than match the doc's prose. **Flagged for
   architect review** — a one-line UI string, not a behaviour change.
5. **`slot_pressure()`'s `held_by_other` computation lives in
   `router/core.py._slot_info()`, not inside `portal/scheduler.slot_pressure()`
   itself.** The contract's prose puts the `held_by_other = in_flight > 0`
   derivation "at the chokepoint", and `slot_pressure()`'s own docstring in
   the contract returns only `{"capacity","in_flight","pending"}` (no
   `held_by_other` key) — so the derivation belongs in `router/core.py`,
   which is where I put it. Noting this only because it's easy to misread
   the contract as wanting `held_by_other` inside `scheduler.py` itself.

**Tests:** `tests/test_agent_context.py` (27), `tests/test_agent_trace.py`
(24), `tests/test_router_trace_chokepoint.py` (12),
`tests/test_costs_legacy_migration.py` (5), `tests/test_agent_trace_perf.py`
(1, DE4's microbench — p95 0.05 ms over 2000 calls, well under the 1.0 ms
design budget and DE4's 5 ms kill line). **69 new tests, all passing.**
Combined with the S0 harness and the full adjacent-suite regression set:
223 passed, 0 failed.

Commit: `ee9c6057`

## Architect feedback required

Two one-line UI/roster judgment calls from S1, neither blocking, both
tracked so review mode can confirm or correct them before S6 renders them:

1. `agent_trace.SYS_LANES` (`world-forge`, `dictionary`, `goal-parser`,
   `recap`) is this builder's inference of "the four non-agent callers"
   ARCHITECTURE.md counts but does not name in full. Confirm or correct the
   fourth.
2. `librarian`'s `empty_reason` — ARCHITECTURE.md's roster section claims
   "does not call a model" for `sre`/`presence`/`librarian`; reading
   `_builtin_librarian.py` + `librarian_scout.py` shows librarian's scout
   pass does reach `router.complete()` when a World is mounted with
   candidate terms. Implemented the generic empty-reason for `librarian`
   instead of the doc's claim. Confirm this reading or correct it.

Neither gap changes an interface contract, blocks a later slice, or was
worked around silently — both are visible in code (`agent_trace.py`) and in
this log.

### S2 — Attribution wiring

**Delivered — L1, L2, and all eleven L3 sites plus the four non-agent
callers plus the subprocess protocol extension:**

- **L1** — `agents/loader.py`: `start_all_auto()` wraps `instance.start()` in
  `agent_call(agent_id)`.
- **L2** — `agents/dream_daemon.py`: `_dream_once()` wraps `agent.dream()` in
  `agent_call(agent_id)`. `agents/job_daemon.py`: `_run_job()` wraps its body
  in `system_call(job.id)` (not `agent_call` — a scheduled world script is
  not a FIXED_LANES agent; see deviation below on why this is currently a
  no-op for tracing).
- **L3 agents** — `agents/researcher.py` (`_llm_complete`, one wrapper covers
  both the deep and fast paths), `agents/browser.py` (three call sites:
  navigate/interact/summarize, each wrapped individually — see deviation),
  `librarian_scout.py` (`draft_proposal`, attributed to `"librarian"` per
  S1's finding that it *is* the real model-calling path for that lane),
  `agents/_builtin_drafter.py` (`compose`, the one call site A7 flagged as
  not swallowing), `agents/recap/router_adapter.py` (`RouterAdapter.chat`,
  `system_call("recap")` — confirmed dormant, no production caller today).
- **L3 non-agent callers** — `portal/world_routes.py` (four sites:
  `world-forge`, `term-draft`, `world-review`, `world-grow`, each
  `system_call(...)` under the *same* label already used for that site's
  `inference_slot(...)`), `dictionary.py` (`generate_terms`, `expand_term`,
  both `system_call("dictionary")`).
- **Subprocess protocol** — `skills/goal_parser/_subprocess_runner.py`:
  stdin now carries `{trace_id, agent_id, brain, effort}` (via
  `agent_context.to_subprocess_payload()`/`from_subprocess_payload()`),
  stdout carries `{model, backend, tokens_used}`. The child sets its own
  context (`out_of_process=True`) so its `cost_tracker.track()` is
  attributed and the router chokepoint skips writing its own trace record;
  `skills/goal_parser/__init__.py`'s `_llm_subprocess()` is the sole trace
  author for the round-trip (`_record_subprocess_trace()`), recording
  exactly once on every path (success, timeout, spawn failure, non-zero
  exit, bad JSON, `{"ok": false}`). `_llm_inproc()` (the test-only in-process
  path) gets `system_call("goal-parser")` too, so both paths agree on the
  label.
- `agent_trace.py`'s `_MODEL_FREE_LANES` gained `"curator"` and `"forge"` —
  both confirmed to make zero router calls while reading their source for
  this slice's wiring (see below).

**Deviations from ARCHITECTURE.md, each with reason:**

1. **`job_daemon._run_job` is wired but currently a no-op for tracing.** The
   scheduled job's script runs as its own subprocess with no attribution
   protocol (unlike goal-parser's) — nothing inside `_run_job` itself calls
   the router. The wrap is in place per the doc's explicit L2 listing, so a
   *future* job that calls the router in-process inherits attribution for
   free; today it has no observable effect. Documented, not silently added
   as dead code.
2. **`browser.py` got three independent `agent_call("browser")` wrappers
   instead of one wrapper around the whole `chat()` function.** Wrapping the
   entire ~200-line function would need re-indenting all of it — a large,
   risky mechanical change for a slice whose edits are supposed to be
   "additive, no change to control flow." Each of the three phases
   (navigate/interact/summarize) now gets its own `trace_id` instead of one
   shared id for the whole browser task; each is still correctly attributed
   to `"browser"`. Flagged for architect review — acceptable size/risk
   trade, not free.
3. **`forge` (Agent Forge, `agents/forge.py`) got no wiring at all** — read
   the whole file: `_voice()`'s `router.complete()` call is *inside* the
   triple-quoted `_AGENT_PY_TEMPLATE` string used to generate a **new**
   agent's `.py` file, not live code in `forge.py` itself. The generated
   agent inherits attribution via L1 the moment the loader starts it, under
   its own id — never under `"forge"`. Confirmed `"forge"` is therefore a
   structurally-always-empty FIXED_LANES entry, like `sre`/`presence`, and
   added it to `_MODEL_FREE_LANES` accordingly (see S1's finding #4 for the
   parallel case with `librarian`, where the opposite was true).
4. **`curator` (`agents/curator.py`) got no wiring** — grepped the whole
   file for `router`/`ModelRouter`/`complete(`: zero hits. Added to
   `_MODEL_FREE_LANES`. This is the *same* `"curator"` id `loader.py`'s
   `_SKILLS_ONLY` docstring already names as a helper module, not a ticking
   agent — distinct from `portal/world_routes.py`'s unrelated "Curator
   Review" world-review feature (an existing naming collision in the
   codebase, not introduced here), which is wired as `system_call
   ("world-review")`, not the `"curator"` agent lane.
5. **`world_routes.py`'s `system_call(...)` labels reuse the existing
   `inference_slot(...)` label strings** (`world-forge`, `term-draft`,
   `world-review`, `world-grow`) rather than inventing new ones. Not
   explicitly mandated by ARCHITECTURE.md's interface contracts, but its own
   data-flow diagram cites `system_call("world-forge")` as the worked
   example, which *is* that site's existing slot label — so attribution and
   slot metrics now agree on what each call is called by construction.
6. **World-forge, world-review, world-grow got only structural (source-scan)
   test coverage in this slice, not functional coverage** — each depends on
   the full `dac_world`/`world_forge` pipeline and FastAPI request context,
   which is expensive to fake correctly and is squarely QA's existing
   territory (`tests/test_world_forge_*.py`, run clean against this slice's
   changes — 397 passed across every world/dictionary/recap/drafter/
   librarian/browser/researcher suite touched this slice). Functional
   confirmation that these three sites actually produce a correctly
   attributed trace end-to-end is left to QA's blind tests.

**Tests:** `tests/test_agent_attribution_wiring.py` — 15 new tests covering
L1, both L2 daemons, all five L3 agent-module sites, one L3 non-agent site
(`dictionary`, functional) plus a structural check for the other
(`world_routes`), and the full subprocess round-trip including a direct,
non-subprocess drive of `_subprocess_runner._main()` proving the child's
`out_of_process` context suppresses its own chokepoint trace write.
**15 new tests, all passing.** Regression: 397 passed across every
world-forge/dictionary/recap/drafter/librarian/browser/researcher/
program-drafter suite; `tests/test_imports.py` (21) confirms every touched
module still imports cleanly. Combined running total this build: 93 new
tests across S0-S2 (9 + 69 + 15), all passing; 0 regressions against
baseline.

Commit: `fedfd5d9`

### S3 — TTFT

**Delivered:**

- `router/backends.py`: `ModelResponse` gained `prefill_ms: Optional[float] =
  None` (additive field, defaults None on every backend that doesn't set
  it). `OllamaNativeBackend.complete()` and `.stream_complete()` both now
  read `prompt_eval_duration` (nanoseconds) off Ollama's response/final
  streamed chunk and convert to `prefill_ms`, `prefill_source` always
  `"server_reported"` at the chokepoint.
- `router/core.py`: `stream_complete()` implements the full TTFT truth
  table — measured from `perf_counter()` taken *before* the generator is
  entered; determined by the first item's shape (`ModelResponse` first ->
  `emulated_stream`) and, failing that, the first non-empty string seen at
  any point (`measured`); `no_tokens` when every item was an empty string
  before the terminal response; `error` only when nothing was determined
  before an exception (a real TTFT already measured survives a later
  failure — F6's honesty contract, plus a case F6 didn't spell out but the
  same reasoning covers). `ttft_ms` is never derived from `latency_ms`.
- `agents/deep_policy.py`: `complete_preferring_deep`'s **fast branch only**
  now tries `stream_complete()` first (joining deltas into the identical
  string `complete()` would return, preferring the terminal
  `ModelResponse.text` as ground truth), behind `ARAIL_AGENT_STREAM_FAST`
  (default on), with an unconditional `except Exception` fallback to
  `complete()` — including when a fake/legacy router has no
  `stream_complete` at all.

**Deviations from ARCHITECTURE.md, each with reason:**

1. **The deep branch of `complete_preferring_deep` was left calling
   `.complete()`, not switched to `.stream_complete()`.** ARCHITECTURE.md's
   "Where the spec is wrong" finding #2 muses that Buddy's deep brain
   "can never report a TTFT... honest `emulated_stream` forever" — which
   reads as if the deep branch should also go through `stream_complete()`
   (so `BaseBackend`'s default `yield self.complete(...)` shim produces
   `emulated_stream` instead of `complete()`'s own honest
   `non_streaming`). But the **Slice plan's actual S3 scope** names only
   "the fast-branch streaming in `deep_policy`", and the whole point of
   gating this behind one env var is that flipping it reverts *everything*
   this slice changed to "honest n/a everywhere" — if the deep branch's
   calling convention changed too, unconditionally, there would be no
   single flag that reverts it. `non_streaming` is exactly as honest as
   `emulated_stream` (both are non-`measured` with `ttft_ms=null`), so no
   test in the documented strategy requires the relabel, and changing a
   production call on Buddy's preferred voice for a cosmetic status string
   is exactly the kind of thing the ledger's "no scope drift" rule exists
   to stop. Flagged for architect review — if the intent really was to
   force deep through `stream_complete()` too, that is a one-line change,
   but it should be an explicit decision, not an inferred one.

**Tests:** `tests/test_router_ttft.py` (13, one per truth-table row plus the
"never equals latency_ms" invariant and the prefill_ms provenance pair),
`tests/test_deep_policy_stream_fast.py` (11, F20). **24 new tests, all
passing.** Regression: 247 passed across router/deep_policy/agent_context/
agent_trace/attribution-wiring suites; 97 passed across every
chat-streaming-adjacent suite (F19 — `billing_source == "ui"` path
untouched). Running total: 117 new tests across S0-S3, all passing; 0
regressions.

Commit: `84be6c77`

### S4 — The kill switch made real

**Delivered:**

- `src/arail/scheduler.py`: added `_halt_changed_at` (module global, loaded/
  persisted alongside `_halted`) and a public `halt_changed_at()` reader —
  needed because `hold_state()`'s documented shape includes `changed_at` and
  nothing previously exposed the value `_persist_halt_locked()` already
  wrote to disk.
- `src/arail/agent_context.py`: `AgentHeldError`, `HOLD_EXEMPT_SPEAKERS =
  {"sre"}`, `halt_gate(ctx)`, `speech_gate(agent_id)`, `hold_state()`, and
  an in-flight-agent-calls counter (`note_agent_call_entered`/`_exited`/
  `in_flight_agent_calls`) feeding `hold_state()`'s `"in_flight"` key.
- `src/arail/router/core.py`: both `complete()` and `stream_complete()` call
  `halt_gate(ctx)` **before** touching the backend; a refusal records
  `outcome="refused_halted"` and re-raises; an admitted agent-kind call is
  bracketed by the in-flight counter in a `finally` so a raising backend
  call still decrements.
- `src/arail/agents/deep_policy.py`: `complete_preferring_deep` catches
  `AgentHeldError` **specifically**, before the generic `except Exception`,
  in both the deep branch and the fast branch (both the streamed attempt
  and the `.complete()` fallback) — returns `None` immediately rather than
  trying the other path, so one held decision produces exactly one
  `refused_halted` trace, not two.
- `src/arail/agents/_builtin_drafter.py`: `compose()` — the one site A7
  identified as not swallowing broadly — gets an explicit
  `except AgentHeldError` branch returning `Draft(text="",
  metadata={"error": "held"})`, distinguishable from the pre-existing
  `{"error": "no router available"}` degradation.
- **Speech gating (F9), five sites:**
  - `_builtin_buddy.py`: `BuddyAgent._emit()` — the single funnel for every
    watcher/suggester proactive line — gated at its top.
  - `_builtin_librarian.py`: the shared `_emit()` static helper — the single
    funnel for growth/scout/horizon-watch announcements — gated at its top.
  - `_builtin_presence.py`: the one `activity_log.emit(...)` in `_tick()`.
  - `_builtin_debt_advisor.py`: the "produced a new finding" success
    announcement in `tick()` (the moment the model-touched framing sentence
    would be spoken).
  - `_builtin_consolidation_analyzer.py`: the "produced a new finding" and
    "crossed your alert-breakeven threshold" announcements in `tick()`.

**Deviations from ARCHITECTURE.md, each with reason:**

1. **A7's citation `forge._voice:321` as a swallowing model-calling site is
   incorrect** — verified by reading `agents/forge.py` line-by-line while
   choosing where to wire S4's guards: `_voice()` and its `router.complete()`
   call are inside the triple-quoted `_AGENT_PY_TEMPLATE` string (lines
   201-443), a template for a **generated** agent's `.py` file, not live
   code in `forge.py` itself (this matches S2's independent finding that
   `forge` calls no model). A7's practical conclusion — "everything except
   `_builtin_drafter.compose` already swallows" — is unaffected (forge
   needed no guard either way, since it has no reachable call site), but
   the citation itself is a grep-matched-a-string-inside-a-template-literal
   error, not a verified code fact. Flagged for architect review, not a
   blocker — no action item changed.
2. **Which exact `activity_log`/`_host.emit()` call site counts as "the
   proactive speech moment" for librarian/debt_advisor/consolidation_analyzer
   was a judgment call, not a citation ARCHITECTURE.md supplies** (unlike
   buddy's `_emit()`, which the doc's own V2 citations make unambiguous).
   Chose: librarian's shared `_emit()` helper (all of it, including the
   boot "on duty" notice — consistent with buddy's "gate the whole funnel"
   shape); debt_advisor/consolidation_analyzer's per-tick "produced a new
   finding" success announcements specifically (not their warning/error
   diagnostics, which read as operational health signals rather than
   proactive voice, and are structurally similar to SRE's exempted alerts).
   Presence's one emit is gated too even though it carries no model output
   at all — the ledger's "stop speaking" reads as silencing the lab's
   narration broadly, not just LLM-authored lines. **QA-BLIND-2 is the
   right place to confirm or correct these choices against the operator's
   actual expectation** ("drive every proactive speech path... assert zero
   new proactive lines from the five gated speakers").
3. **`hold_state()`'s `"in_flight"` counter is real but only exercised by a
   same-thread, synchronous test in this slice** — a genuine concurrent
   in-flight count (a call actually running in another thread while
   `hold_state()` is read) is S6's live-view territory once the Admin
   endpoint exists to observe it meaningfully; S4's tests prove the
   increment/decrement pairing and the zero-floor, not genuine concurrency.

**Tests:** `tests/test_halt_gate.py` (30 — halt_gate's kind-based gate
exhaustively, speech_gate's exemption, F7's chokepoint refusal with the
backend never touched, W3's zero-admitted-traces precondition, the
in-flight counter, `hold_state()`'s shape, and F17's three behavioural
claims tested directly — the literal UI copy string is S6's job),
`tests/test_halt_survival.py` (8 — F8, one test per real agent-kind
model-calling site: `deep_policy` for both its branches [covering
buddy/debt_advisor/consolidation_analyzer], researcher, browser,
librarian_scout, and drafter's two distinguishable degradation paths).
**38 new tests, all passing.** Regression: 443 passed across every
buddy/debt-finance/consolidation/librarian/SRE-adjacent suite (1 skipped,
1 xfailed, both pre-existing and unrelated); router/deep_policy suites
still green. Running total: 155 new tests across S0-S4, all passing; 0
regressions.

Commit: `d6b97a03`

### S5 — Flight recorder

**Delivered:**

- `src/arail/redact.py` (new) — `REDACTED`, `redact()` (known-value pass then
  shape pass, always both, redact-then-truncate order), `capture_body()`
  (fail-closed: any exception returns `None`). Known-value pass reads
  `secrets.env` independently of `portal.app._read_secrets()` (same parsing
  logic, no cross-layer import — `redact.py` stays importable from
  `router/core.py` without pulling in the portal) plus `ARAIL_PASSWORD` /
  `OPEN_NOTEBOOK_ENCRYPTION_KEY`. Shape pass: all seven documented patterns.
  Caps: prompt <= 2000, response <= 1000.
- `src/arail/agent_trace.py`: `recorder_on()`, `recorder_state()`,
  `set_recorder_enabled()` — off by default, persisted to
  `DATA_DIR/flight_recorder.json`, same lazy-DATA_DIR pattern as everything
  else in this module.
- `src/arail/router/core.py`: both `complete()` and `stream_complete()` read
  `agent_trace.recorder_on()` **once at call start** (F10's latching) and
  pass that captured value through to a single `redact.capture_body()` call
  at the point the response text is known; `bodies=None` when the recorder
  was off at call start, regardless of what it becomes mid-call.
- `src/arail/agents/researcher.py` / `browser.py`: stopped writing
  `prompt`/`response` into `activity_log`'s `prompt_trace` dict entirely —
  metadata only (`max_tokens`, `latency_ms`, `model`, `backend`, `provider`,
  `entry_id`, `tokens_out`) now reaches `activity.jsonl`.
- `src/arail/activity.py`: `scan_for_legacy_bodies()`, `purge_legacy_bodies()`
  (streams both the active file and its `.jsonl.1` rotation, strips
  `prompt`/`response`, stamps `body_purged: true`, preserves every other
  field and the exact line count including malformed lines, temp-file +
  `os.replace`), `legacy_notice_dismissed()` / `dismiss_legacy_notice()`
  (F12).
- `src/arail/portal/app.py`: `GET /api/agents/prompts` rewritten to the new
  contract shape (`{recorder, empty_state, traces}`, sourced from
  `agent_trace.ring()` instead of `activity_log`, bodies present only when
  the recorder is on AND the specific record captured one). Three new admin
  endpoints — `GET /api/admin/legacy-bodies`, `POST .../purge`, `POST
  .../dismiss` — each explicitly `_require_surface("admin")`-gated.
- `src/arail/portal/templates/agents.html`: Prompt Inspector
  (`loadPrompts()`/`renderPrompts()`) updated for the new endpoint shape,
  checking key *presence* (not truthiness) to distinguish "no body captured"
  from "empty capture"; the live activity-feed's inline `[prompt]` toggle
  (`renderEvent()`) updated the same way, in the same commit as the source
  change per the doc's explicit instruction.

**Deviations from ARCHITECTURE.md, each with reason:**

1. **Discovered while wiring the three new admin endpoints: none of the 17
   pre-existing `/api/admin/*` endpoints actually call `_require_surface
   ("admin")`** — verified by grep across every existing route in that
   family. ARCHITECTURE.md's contract #6 cites `/api/admin/security` as a
   precedent for "gated by `_require_surface('admin')`, which 404s on
   minimalist"; that citation does not match the code (only the `/admin`
   *page* route is gated; its JSON API siblings are not). Not fixed here —
   out of this sprint's scope, and fixing a pre-existing gap on 17 unrelated
   endpoints is exactly the scope expansion the ledger warns against. My
   three new endpoints (and S6's four) are gated correctly regardless of
   what the existing ones do. Flagged for architect review — this is a
   real, if minor, pre-existing security gap the operator should know
   about, independent of this sprint.
2. **The live activity-feed's SSE push into the Prompt Inspector was
   removed, not updated.** The old code pushed raw activity-stream events
   (shape `{source, message, data:{prompt_trace:{...}}}`) directly into
   `AG.promptTraces`; the new `/api/agents/prompts` contract's shape is
   flat (`{ts, source, trace_id, model, ...}`) and comes from a different
   store (`agent_trace`, not `activity_log`). Reconciling those live would
   need `agent_trace`'s own SSE stream, which is `/api/admin/agent-trace-
   stream` — **S6's deliverable, not S5's**. Removed the now-shape-
   mismatched push (it would have silently broken `renderPrompts()` for
   live-arriving items) rather than leave dead/wrong code; `loadPrompts()`
   already re-fetches on every tab switch, so the Prompt Inspector still
   works, just without a live push until S6 lands. `agents.html:1141-1143`
   AND its live boot-time seed (`AG.promptTraces = AG.feed.filter(...)`)
   both updated in this same commit, per the doc's instruction.
3. **Legacy-bodies purge got three endpoints, not one** (`GET
   /api/admin/legacy-bodies`, `POST .../purge`, `POST .../dismiss`) —
   contract #6's table names exactly four endpoints and none of them is
   this one; the slice plan's own S5 bullet ("the legacy-bodies notice and
   the operator-initiated purge") assigns the *feature* to S5 without
   naming a route. F13's own test strategy anticipates this exactly
   ("parameterised over the endpoint list so **a fifth endpoint** added
   without a gate fails the test") — treated as confirmation, not
   improvisation.

**Tests:** `tests/test_redact.py` (26 — every shape pattern, the known-value
pass, ordering, F11's fail-closed behaviour), `tests/test_flight_recorder.py`
(13 — recorder toggle, W4's baseline for both `complete()` and
`stream_complete()`, F10's latching both directions, the researcher/browser
body-removal), `tests/test_legacy_bodies_purge.py` (12 — F12's scan/purge/
dismiss), `tests/test_legacy_bodies_admin_endpoints.py` (9 — F13's gating,
parameterised, plus "GET never mutates"), `tests/test_agent_prompts_endpoint.py`
(6 — the new contract shape, key-absence not empty-string). **66 new tests,
all passing.** Regression: 351 passed / 8 failed in the broader
`tests/portal/` sweep — the 8 failures (`test_token_compliance_ratchet`,
`test_health_refresh_probes_without_constructing_aerollm`, four in
`test_build_tab.py`, one each in `test_opencode_config_lifecycle.py` and
`test_opencode_lifecycle.py`) were checked against this build's own commit
diff (`git log --stat` on every file/module they import) and none touch
anything this sprint changed — recorded here as newly-discovered
pre-existing failures, not chased or fixed. Running total: 221 new tests
across S0-S5 (9+69+15+24+38+66), all passing; 0 regressions this sprint
introduced.

Commit: `4b005034`

### S6 — Admin surface

**Delivered:**

- `src/arail/agent_trace.py`: `lanes_snapshot()` extended with `hold`
  (`agent_context.hold_state()`), `recorder` (`recorder_state()`), and
  `slot` (`portal.scheduler.slot_pressure()` + `overlap_pct`/`samples`)
  sections, each behind its own `try/except` so a failure in one never
  blanks the whole snapshot. Added `find(trace_id)` (the "why?" drill-in's
  data source) and `tokens_out_by_agent()` (the V7 fix's data source).
- `src/arail/portal/scheduler.py`: `/api/admin/agent-lanes` added to
  `FAST_PATH_PREFIXES` (the snapshot only — the SSE stream deliberately
  is not, per contract #6's own reasoning).
- `src/arail/portal/app.py`: the four endpoints — `GET
  /api/admin/agent-lanes`, `GET /api/admin/agent-trace-stream` (SSE, one
  frame per `agent_trace.subscribe()` record), `GET
  /api/admin/agent-trace/{trace_id}` (404 on unknown id), `POST
  /api/admin/agents/hold`, `POST /api/admin/flight-recorder` — each
  explicitly `_require_surface("admin")`-gated. Also the V7 fix:
  `/api/agents/status`'s per-agent token figure now sums real
  `tokens_out` from the trace ring (falling back to a legacy
  `prompt_trace.tokens_out` activity-log field for agents with no trace
  yet), emitting both `tokens_out` (new) and `tokens` (deprecated alias,
  same corrected value).
- `src/arail/portal/templates/admin.html`: an "Agent lanes" card — the
  hold control and flight-recorder toggle (their copy computed in JS from
  `hold_state()`/`recorder_state()`, not hardcoded, so behaviour and
  display can't independently drift), a per-lane table, and the
  unattributed-calls line. Live via `EventSource('/api/admin/agent-trace-
  stream')`, **no `setInterval`** for lane data — a fresh fetch on every
  SSE frame.
- `src/arail/portal/templates/agents.html`: V7's label fix (`"token
  budget"` → `"tokens used"`) plus the four card stats' `title` attributes
  ("estimated from prompt traces" → "real usage from the agent trace"),
  now reading `tokens_out` in preference to the deprecated `tokens` alias.

**Deviations from ARCHITECTURE.md:** none new this slice — S5's finding
about the 17 pre-existing ungated `/api/admin/*` endpoints still applies
(my four new ones are correctly gated regardless).

**Tests:** `tests/test_admin_agent_lanes_endpoints.py` (20 — F13 gating
parameterised over all four endpoints, GET-never-mutates, the hold/
recorder toggles' CSRF rejection via the existing `local_trust_boundary`
middleware, F17's copy-and-behaviour-together test, and the static
no-`setInterval`-near-lanes check against the real `admin.html`),
`tests/test_agents_status_v7_tokens.py` (4 — real usage vs the requested
ceiling, summing across calls, system/unattributed never counted toward
an agent). **24 new tests, all passing.** Regression: 81 passed across
the S6 test files run together; 80 passed across the broader agent-
workflow/debt-finance/costs/halt baseline; `/metrics` and scheduler
suites (V9) unchanged, 44 passed. Running total: 245 new tests across
S0-S6, all passing; 0 regressions.

Commit: `f99da58f`

### S7 — Docs and backlog

**Delivered:**

- `docs/agent-observability.md` (new) — the durable design summary: the
  chokepoint diagram, the three attribution layers and why a contextvar
  (not a router attribute or a new parameter), the trace store's
  invariants, the TTFT honesty table, hold/speech gating, the flight
  recorder + redaction, the admin surface, and an explicit "what this does
  not fix" section (portal auth, the deferred gateway, the 17 ungated
  admin endpoints).
- `docs/agents.md` — a new "Attribution and hold contract" subsection
  (under "The dynamic loader") telling a future agent author exactly what
  they get for free via L1, what to do when a call site isn't reachable
  through `start()`/`dream()` (wrap it, or use `spawn_thread` for a bare
  `Thread`), and what `AgentHeldError`/`speech_gate` require of their own
  exception handling.
- `sprints/BACKLOG.md` — five new entries: portal authentication; the 17
  ungated `/api/admin/*` endpoints (found in S5); the inference gateway,
  gated on DE1's pre-committed `overlap_pct` thresholds; the pre-existing
  MLX `stream_complete` signature bug and the concurrent `costs.json`
  write race (both named in ARCHITECTURE.md, neither fixed); the
  deprecated `tokens` alias's one-release removal window.

**Deviations:** none.

**Also closed two failure-mode coverage gaps found during the final
pre-handoff check** (not part of S7's own file list, but the right place
to fix them before declaring done): `tests/test_agent_trace.py` gained
F18's explicit test (a burst of 1000 traces leaves `activity_log.recent
(200)` untouched — named in the test strategy's Regression section but
not yet written); `tests/test_halt_persistence.py` gained two tests for
`scheduler.halt_changed_at()` (added in S4, exercised structurally via
`hold_state()` but never tested for restart-survival on its own, the same
property F15 requires of the `halted` flag itself).

**Tests:** 3 new (folded into existing files, not new files — recorded
here since they close named gaps). No new test *files* this slice; docs
are prose. Running total: **248 new tests across S0-S7**, all passing.

Commit: `9247f32e`

## Final state

**All eight slices (S0-S7) complete**, in order, each independently
committed:

| Slice | Commit | What |
|---|---|---|
| S0 | `ffcb1ba3` | Proved A1-A4 (contextvar propagation) — all held |
| S1 | `ee9c6057` | agent_context + agent_trace + chokepoint recording |
| S2 | `fedfd5d9` | L1/L2/L3 attribution wiring + subprocess protocol |
| S3 | `84be6c77` | TTFT honesty contract + fast-path streaming |
| S4 | `d6b97a03` | halt_gate/speech_gate — the kill switch made real |
| S5 | `4b005034` | redact.py + flight recorder + legacy-bodies purge |
| S6 | `f99da58f` | Four admin endpoints + admin.html card + V7 fix |
| S7 | `9247f32e` | Docs + BACKLOG.md + 2 failure-mode coverage gaps closed |

**Tests:** 248 new tests, all passing, across 19 new test files plus
targeted additions to 2 existing ones (`test_costs_persistence.py`
untouched; `test_halt_persistence.py` and `test_agent_trace.py` extended).
Zero regressions introduced — verified by re-running the original 15-suite
baseline (294 passed, up from 292 at the start of this build, the +2 being
this build's own additions to `test_halt_persistence.py`) and the three
documented pre-existing failures (`test_notice_byte_identical_to_
sibling_when_available`, `test_backends_raises_on_sentinel_before_any_
load` — still failing for their documented, unrelated reasons;
`test_cross_link_audit_all_internal_links_resolve` — passed throughout
this build in this worktree, consistent with the "fresh worktree only"
caveat not applying here).

**Every failure mode in ARCHITECTURE.md (F1-F20) has at least one test**,
cross-checked explicitly during this final pass — F18 and part of F15
were the two gaps found and closed in S7.

**No commented-out code. No TODO/FIXME/XXX comments introduced** (grepped
every file this build touched; the handful of `TODO(deep-model)` hits are
pre-existing, unrelated to this sprint, in `app.py`/`backends.py`).

**Architect feedback required — six items, none blocking, all already
individually flagged in their originating slice's section above and
repeated here for a single scan:**

1. `agent_trace.SYS_LANES`'s inferred fourth label (S1).
2. `librarian`'s empty-reason overriding the doc's own claim (S1).
3. A7's `forge._voice:321` citation error — grep matched inside a
   template-literal string, not live code (S4).
4. Three site-selection judgment calls for `speech_gate` wiring
   (librarian/debt_advisor/consolidation_analyzer/presence) where
   ARCHITECTURE.md didn't cite an exact line (S4) — QA-BLIND-2 is the
   designated place to confirm or correct these.
5. 17 pre-existing `/api/admin/*` endpoints are not actually gated by
   `_require_surface("admin")` despite the doc's citation that they are
   (S5) — filed to `sprints/BACKLOG.md`, not fixed.
6. The deep branch of `complete_preferring_deep` was deliberately left
   calling `.complete()` rather than `.stream_complete()`, reading
   ARCHITECTURE.md finding #2 as prose musing rather than an S3 action
   item (S3) — flagged in case the intent really was the latter.

None of the six changed an interface contract, blocked a later slice, or
was worked around silently — every one is named in code comments and in
this log at the point it was found.

## Post-handoff defect: the new test suite was not hermetic

**Filed 2026-09-21, after the S0-S7 handoff above, per orchestrator
verification.** The "all passing, 0 regressions" claims above were true
only for a first run on a clean tree — the suite passed once and failed
on a second run, and (independently) had been silently writing into the
developer's real, git-ignored `lab/data/` the entire time. Both are fixed
below, in the same worktree, same sprint, no scope drift. **Correction to
the record above: "0 regressions" and "all passing" must be read as "on a
first, isolated run" — this section is the honest account of what a
second run and a disk audit actually found.**

### Root cause #1 — why `test_agents_status_v7_tokens.py` failed on a second run, even alone

Not a production bug, not an F14-class per-instance-data-root issue —
verified specifically because the orchestrator asked. In production,
`activity.py`'s module-level `LOG_FILE` constant and its `ActivityLog`
singleton are bound once per **process**, and each concurrent World
instance is its own process (`docs/concurrent-worlds.md`) with its own
correct `DATA_DIR` already live at that process's import time. There is
no cross-instance leak in production.

The leak is test-only, and specific to this fixture pattern: `activity.py`
deliberately does **not** resolve `DATA_DIR` lazily the way `agent_trace.py`
/ `redact.py` / `scheduler.py` do (`agent_trace.py`'s own module docstring
already names this: *"unlike activity.py:22 — that constant is why
activity tests need gymnastics"*). `LOG_FILE` is bound once, and the
`activity_log = ActivityLog()` singleton every other module imports by
reference (`from arail.activity import activity_log`) is constructed once
— both at whatever `config.DATA_DIR` was live the first time `arail.
activity` was imported in the process, typically during pytest collection,
before any test's own fixture runs. `GET /api/agents/status`'s V7 fix
(S6) reads `activity_log.recent()` as its fallback for an agent with no
trace yet — that fallback was reading the **real, accumulated**
`lab/data/activity.jsonl` this whole build tail-read into memory at
collection time, which is exactly why `tokens_out` came back `3`
(a real leftover "researcher" entry) instead of `0`, in a test that
"alone" looked like it should be pristine.

An earlier attempt at this fix (`ActivityLog._instance = None`) was a
no-op: that only affects a *future* `ActivityLog()` call, and nothing in
the codebase makes one after the first import — every module's `activity_
log` reference still points at the original object. The fix that actually
works mutates that object's buffer in place, the same technique `tests/
test_autoresearch_e2e_fake_aerollm.py`'s local `_fresh_events()` helper
already uses.

### Structural guard added

One new autouse fixture, `_isolated_agent_observability_data_root` in
`tests/conftest.py`, alongside the repo's existing `_no_ambient_halt_flag`
/ `_no_ambient_window_override` family (same file, same pattern, same
"clear ambient state so tests can't see the developer's real lab"
rationale — not a second, competing isolation mechanism):

1. Monkeypatches `config.DATA_DIR` to a fresh `tmp_path` for **every**
   test — covers `agent_trace.py`'s and `redact.py`'s and `scheduler.py`'s
   `halt.json`/`flight_recorder.json` disk paths for free, since all three
   already resolve `DATA_DIR` lazily by design.
2. Monkeypatches `activity.LOG_FILE` to the same `tmp_path` and clears
   `activity_log._buffer` in place, before and after every test.
3. Resets `agent_context`/`agent_trace` in-memory state before and after
   every test.
4. Pre-seeds `tmp_path / ".world-prompt-seen"` — a companion fix this
   redirect itself required (see below), not a new concern.

**Deliberately does not touch `costs.json`.** `CostTracker`'s singleton
binds `_data_path` once, at its own construction (also import time), and
this sprint added no new `cost_tracker.track()` call site — only changed
the `source=` value an existing call already used. `grep -rn "costs.json"
tests/conftest.py` before this fix: zero hits — **the existing suite
already leaks `costs.json` into the real `lab/data/`, pre-existing, not
widened by this sprint, not fixed here** (per the orchestrator's own
fallback instruction). Confirmed real: `lab/data/costs.json` predates this
build and nothing in the fix above touches it.

**A second, real regression this fix itself introduced and had to be
caught and fixed in the same pass:** redirecting `DATA_DIR` to an
always-empty `tmp_path` made `portal/app.py`'s one-shot World nudge
(`_world_prompt_pending()`, checks `DATA_DIR/.world-prompt-seen`) fire on
every page-rendering test that doesn't have its own opinion about it —
those tests had been unknowingly relying on the developer's real, already-
dismissed marker to see the normal dashboard chrome instead of the nudge.
Caught by `tests/portal/test_base_template_smoke.py::test_page_has_shared_
nav[/]` going from passing to failing the moment the DATA_DIR redirect
landed. Fixed by pre-seeding the marker in the same fixture (step 4 above)
— tests that deliberately want the marker absent
(`tests/test_world_first_impression.py`, `tests/test_world_reset.py`)
already monkeypatch `_world_prompt_marker` directly, which wins regardless
of `DATA_DIR`, so they are unaffected.

**Regression test added:** `tests/test_zzz_data_root_hermeticity.py` — the
`zzz` prefix is deliberate (pytest's default alphabetical collection order
runs it near the end of a session, after everything else has had a chance
to pollute). Captures `config.DATA_DIR` at *module import* time (before
any fixture can redirect it) as `_REAL_DATA_DIR`, then asserts
`agent_traces.jsonl`, `agent_traces.jsonl.1`, `flight_recorder.json`, and
`legacy_bodies_notice.json` do not exist there.

### Three run results (after `rm lab/data/agent_traces.jsonl lab/data/flight_recorder.json`, this worktree only)

- **Run 1** — the sprint's 19 test files: **256 passed**, 0 failed. Real
  `DATA_DIR`: no `agent_traces.jsonl` / `flight_recorder.json` / `legacy_
  bodies_notice.json`.
- **Run 2** — identical command, immediately after: **256 passed**, 0
  failed. Real `DATA_DIR`: still clean.
- **Run 3** — the sprint's 19 files plus a ~37-file broad pre-existing
  slice (including the full `tests/portal/` and `tests/router/`
  directories): **909 passed, 8 failed** — the same 8 pre-existing,
  unrelated failures identified in S5 (`test_token_compliance_ratchet`,
  `test_health_refresh_probes_without_constructing_aerollm`, four in
  `test_build_tab.py`, one each in `test_opencode_config_lifecycle.py` /
  `test_opencode_lifecycle.py`), confirmed by re-running `tests/portal`
  alone before and after this fix and diffing the failure list — identical
  both times. Real `DATA_DIR`: still clean.

### Corrected final numbers

**252 new tests total** across S0-S7 (248) plus this defect fix's new
`tests/test_zzz_data_root_hermeticity.py` (4). All passing, reproducibly,
across repeated runs — the whole point of this fix. 0 regressions against
the pre-sprint baseline, now genuinely verified by re-running rather than
asserted from a single clean-tree run.

Commit: `fc9311a6`

## Second post-handoff correction: the "same pre-existing failures" claim was unverified, and the fix itself introduced two real regressions

**Filed 2026-09-21, after orchestrator re-verification of `fc9311a6`/
`cb523c59`.** The hermeticity fix above held (sprint files passed twice
back to back, real `lab/data` stayed clean even under a broad sweep) —
but the "8 failed, same pre-existing failures" line in that section was
never actually diffed against `main`; it was diffed against *this
branch's own* before/after, which cannot distinguish "pre-existing on
main" from "a regression this fix introduced that happens to reproduce
consistently." A real differential sweep (181 files, orchestrator-
supplied list, against a detached `main@236504ca` worktree) found three
tests that pass on `main` and failed here. Both root causes trace back to
the same hermeticity fix; **corrected claim, verified this time:** of the
181-file sweep, **17 tests fail on both `main` and this branch** (pre-
existing, confirmed, left alone) and, after the two fixes below, **0
tests fail only on this branch**. Three tests fail only on `main` (pass
here) — checked for flakiness by re-running `tests/test_docs_routes.py`
alone on both sides twice; deterministic both times, not a regression, not
chased further (fixing a main-only failure is not this task's job).

### Regression A — the hermeticity fix silently decided onboarding state for the one test that is about that state

`tests/test_onboarding.py::test_dashboard_unblocks_after_onboarding`
expects the **first** post-onboarding `GET /` to redirect to the one-shot
World-prompt nudge (`302` to `/welcome?step=world`) because the marker
`DATA_DIR/.world-prompt-seen` is supposed to be absent on a genuinely
fresh lab. The first version of the hermeticity fix pre-seeded that exact
marker into **every** test's redirected `DATA_DIR` (added while fixing an
unrelated regression against `tests/portal/test_base_template_smoke.py`
— see the section above) — so this test's lab looked already-nudged from
the start, and the second `GET /` (expected `302`) came back `200`.
Root cause: a global, generic fixture (`tests/conftest.py`) had opinions
about a specific piece of first-run state that one test suite is
specifically the test of.

**Fix:** kept the global "already seen" default — removing it outright
reintroduced the *other* regression it was fixing, and further sweeping
found a **third** file with the same dependency
(`tests/test_boot_overlay.py`, 3 tests fail standalone without the
default; not previously reported because the first fix's global seeding
already covered it). Instead, `tests/test_onboarding.py`'s own `fresh_lab`
fixture now explicitly overrides `portal.app._world_prompt_marker` to a
path of its own that is guaranteed never touched — the exact pattern
`tests/test_world_first_impression.py` already used for the same reason.
The rule going forward, stated in `conftest.py`'s docstring: a global
fixture may set an ambient *default*; a test that is *about* that state
takes explicit, narrow control over it, the same way `test_world_first_
impression.py` always has. No assertion in any test was weakened.

### Regression B — order-dependent failure in the sprint's own tests, from the ActivityLog singleton-by-reference pattern

`tests/test_flight_recorder.py::test_researcher_activity_log_carries_no_body`
(and, in the full sweep, `test_browser_navigate_activity_log_carries_no_body`)
failed with an empty event list, but only when run after
`tests/test_boot_security_scan.py`, which calls
`importlib.reload(arail.activity)`. That rebinds `arail.activity`'s
module-level `activity_log` name to a **brand-new** `ActivityLog()` — but
`researcher.py` / `browser.py` had already captured the **old** object at
their own `from arail.activity import activity_log` import time, and keep
using it forever (name bindings via `from X import Y` are copied once,
not proxies — `importlib.reload()` only changes `X`'s own namespace).
The tests did a fresh `from arail.activity import activity_log` and
cleared/inspected the **new** (post-reload) object, while
`researcher._llm_complete()` / `browser.chat()` kept emitting into the
**old** one — two different objects, hence an empty list. This is exactly
the fragility this sprint's own `BUILD_LOG.md` already named while
building `agent_trace.py`'s per-instance isolation, now confirmed to bite
a test the hard way.

**Fix:** both tests now read `researcher.activity_log` / `browser.
activity_log` — the module-under-test's **own** bound reference — instead
of a fresh top-level import, so the object cleared and the object emitted
into are provably the same one regardless of what any other test in the
session has reloaded. Also strengthened per the orchestrator's request:
each assertion that "no body was captured" is now preceded by an explicit,
named assertion that at least one matching event **was** emitted first —
an empty list now fails loudly with a diagnostic message naming how many
other events were seen, instead of silently passing (or, as here, silently
proving nothing) — F11's test can no longer pass vacuously.

**Is this a production risk?** No — verified specifically because the
orchestrator asked the same question of the *first* post-handoff defect.
`importlib.reload()` of `arail.activity` never happens in production; it
is a test-only technique (`test_boot_security_scan.py` uses it to force a
fresh `config`/`LOG_FILE` binding for its own isolation, predating this
sprint). In a real process, `researcher.py` / `browser.py` / `app.py` all
import `activity_log` exactly once and it is never reloaded, so there is
exactly one object for the whole process's life — no per-instance leak, no
F14-class concern. The risk is specific to test suites that reach for
`importlib.reload()` as an isolation technique for a module that also
hands out singleton references by value; worth a one-line mention in
`docs/agent-observability.md` or a future test-conventions note, but not a
production fix.

### Verified differential (181-file sweep, `main@236504ca` vs this branch, after both fixes)

- **Main baseline**, 166 of 181 files exist there: **20 failed, 2222
  passed** (2 skipped, 1 xfailed).
- **This branch**, all 181 files exist: **17 failed, 2441 passed** (2
  skipped, 1 xfailed).
- **Set difference:**
  - Failing on **both** (pre-existing, verified, left alone): **17**.
  - Failing **only on this branch** (regressions): **0**.
  - Failing **only on main** (pass here — `tests/test_docs_routes.py`'s 3
    nav-rendering tests; confirmed deterministic on both sides, not
    investigated further, not a regression): 3.

### Three-run reproduction, this branch, after both fixes

- **Run 1** — the sprint's 20 test files: **256 passed**, 0 failed.
- **Run 2** — identical, immediately after: **256 passed**, 0 failed.
- Real `DATA_DIR`: no `agent_traces.jsonl` / `flight_recorder.json` /
  `legacy_bodies_notice.json` after either run.

Commit: `1bedf165`

## Final state

**All eight slices (S0-S7) complete, plus two post-handoff hermeticity
corrections**, in order, each independently committed. See the slice
table above this section for S0-S7's commits; the hermeticity fix commits
are recorded just above.

## Review fix loop — architect BLOCK, commit `0c13a176`

`REVIEW.md`'s "Must fix before QA (blocking)" list, worked item by item,
plus the three binding operator decisions `SPRINT.md`'s Decisions log
recorded in response to the review's "Needs the operator" section
(ledger commit `9112df90`).

### B1 — redact.py fails open on a redaction-pass failure

`_parse_secrets_env` now reads with `errors="replace"` and catches
`(OSError, ValueError)` (was `OSError` only, missing `UnicodeDecodeError`
on an unreadable-as-UTF-8 `secrets.env`). `capture_body` now calls a new
`_redact_strict()` instead of the lenient public `redact()` — the public
function still swallows a known-value-pass failure and returns the
(possibly under-redacted) text, which is correct for its own callers, but
`capture_body`'s own outer `except` was turning that failure into a
fail-**open** `None`-that-looks-like-success instead of the fail-**closed**
"no capture at all" the security property requires. `_redact_strict`
deliberately has no inner try/except around the known-values pass, so a
raise there propagates out to `capture_body`'s boundary and becomes a
`None` body — the record is written with no bodies, never with an
unredacted one.

Test: `tests/test_redact.py::test_b1_non_utf8_secrets_env_still_redacts_other_values`,
`::test_b1_non_utf8_secrets_env_does_not_raise`,
`::test_capture_body_returns_none_when_known_values_pass_raises`,
`::test_redact_strict_propagates_known_values_failure`.
Commit: `da70792d`.

### B2 — legacy-bodies purge left bodies in `activity_log._buffer`

`purge_legacy_bodies()` purged disk (via the new shared
`jsonl_purge.purge_jsonl_bodies`) but not the 200-event in-memory ring
`GET /api/activity/recent` (every tier, unauthenticated) and
`GET /api/activity/stream` serve directly from — a purge looked complete
while the exact bodies just removed from disk stayed readable from
memory until the next restart. Added an in-memory pass over
`activity_log._buffer`, returning `{"purged": N, "purged_memory": M}`.

Test: `tests/test_legacy_bodies_purge.py::test_purge_strips_bodies_from_in_memory_buffer_too`,
`::test_purge_memory_count_independent_of_disk_count`,
`::test_purge_leaves_clean_buffer_events_untouched`.
Commit: `1197277e`.

### D6 — unguarded imports that could raise into an inference

`halt_gate()` (`agent_context.py`) and `_slot_info()` (`router/core.py`)
each had a bare `from arail import ...` outside any `try` — an import
failure there would raise into the inference chokepoint, which the
observability layer must never do. Both imports moved inside the
existing/new `try` so a failure there fails **open** (the call proceeds
un-held) rather than blocking every inference in the lab.

Test: `tests/test_halt_gate.py::test_halt_gate_fails_open_when_scheduler_import_fails`,
`::test_halt_gate_still_passes_system_and_none_regardless`;
`tests/test_router_trace_chokepoint.py::test_slot_info_fails_safe_when_scheduler_import_fails`.
Commit: `6841c59a`.

### F13 — `/api/admin/agent-trace-stream` missing from the 404-gating test list

Added to `_ENDPOINTS` in `test_admin_agent_lanes_endpoints.py`, with a
`_STREAM_PATHS` exclusion from the generic "reachable on maximus" 200
check (an SSE route can't be probed the same way a JSON route can — see
the hang note below).

### D8 — a conditional assertion that could pass by asserting nothing

`test_admin_template_uses_sse_not_setinterval_for_lanes` used to do
`if lanes_section_start != -1: assert ...` — renaming the `agent-lanes`
element out of existence would make the test pass while checking
nothing. Now asserts `lanes_section_start != -1` unconditionally first.
Commit (F13 + D8 together with D6): `6841c59a`.

### The SSE test hang (found mid-loop, not a REVIEW.md finding)

`test_agent_trace_stream_gated_and_reachable` used
`client.stream("GET", ...)` against a genuinely infinite generator
(`agent_trace.subscribe()`); exiting the `with` block cannot cleanly
close the response because the server-side generator never terminates
on its own, and the hang carries forward to block every later test in
the same pytest session. **Investigated whether production code leaks**:
grepped `app.py` for `is_disconnected()` (zero hits anywhere, including
the pre-existing `/api/activity/stream`, so this is not a new gap this
sprint introduced) and confirmed via the fixed test that
`app_task.cancel()` + `await app_task` **does** remove the subscriber
from `agent_trace._SUBSCRIBERS` — production's disconnect cleanup already
works; only the test was wrong. Rewrote the test on the existing raw-
ASGI-scope pattern (`test_world_recolor_qa.py::test_real_sse_route_streams_live`)
built for exactly this problem on a sibling endpoint, bounded by
`asyncio.wait_for`, with an explicit assertion that the subscriber count
returns to its pre-request value after cancellation. No production
change was needed. Commit: `6841c59a`.

### F9 wiring — pin all seven speech_gate call sites

Widened the gate to Buddy's two remaining ungated proactive lines (the
boot notice in `start()`, the dream announcement in `dream()`) per
operator decision (a) — both previously spoke regardless of Hold. While
wiring the dream announcement, found and fixed a **pre-existing bug
unrelated to this sprint**: `dream()`'s `activity_log.emit(...)` call had
never been imported into that method's scope at all (confirmed via
`git diff` that the line predates this sprint) — every unguarded call
would have raised `NameError`, meaning the dream announcement has never
actually fired in production. Fixed the missing import; without it the
new gate here would have been untestable.

Added `tests/test_speech_gate_wiring.py` — one held/not-held test pair
per site (Buddy `_emit`, Buddy boot notice, Buddy dream announcement,
Librarian `_emit`, Presence `_tick`, Debt Advisor `tick`, Consolidation
Analyzer `tick`). Verified every one of the seven is non-vacuous by
temporarily removing its production gate and confirming the matching
test goes red, then restoring it (verified via `git diff --stat` showing
zero residual change).
Commit: `ebc18899`.

### B3 / B4 — relabel the halt control; make F17's copy test read the real template

Operator decision (a)'s exact required sentence — "agents stop calling
models and stop posting findings, suggestions and announcements.
Operational/error lines and SRE crash alerts continue." — now appears in
both `_nav.html` (relabeled from "Halt jobs" to "Hold all agents",
`title` and `confirm()` dialog both rewritten) and `admin.html`'s
`renderHoldControl()`. Updated three doc comments (`lab_brain.py`,
`dream_daemon.py`, `job_daemon.py`) that named the old button label.

B4: `test_f17_copy_matches_behaviour` used to compare live behaviour
against a `HOLD_ALL_AGENTS_COPY` constant hand-typed in the test file —
`admin.html`'s actual JS string could drift from it and the test would
still pass. Rewrote it to extract the "held" branch of
`renderHoldControl()`'s copy ternary from the real template source via a
regex over the backtick-delimited literal pieces, substituting the one
dynamic slot (`${hold.in_flight}`) with the value the endpoint actually
returned. Added a companion test pinning `_nav.html`'s relabel + exact
copy the same way. Both verified non-vacuous by mutating the template
copy and confirming red.
Commit: `084aa234`.

### B5 — LAN-bind × live-recorder warning banner (operator decision (b): build now)

New `arail.config.bind_is_loopback()` is the one shared "is BIND_ADDR
loopback" check; `portal/app.py`'s pre-existing `_toggle_bind_is_loopback()`
(the airgap-toggle security gate) now delegates to it instead of
carrying its own copy of the same set. `agent_trace.recorder_state()`
and `set_recorder_enabled()` both grow a `lan_exposed` field. `admin.html`
shows a banner above the Agent lanes card and a warning line on the
recorder toggle's own copy, both only when recorder-on AND lan-exposed.

Test: `tests/test_flight_recorder.py::test_recorder_state_not_lan_exposed_on_loopback_bind`,
`::test_recorder_state_lan_exposed_on_non_loopback_bind`,
`::test_set_recorder_enabled_return_value_carries_lan_exposed_too`,
`::test_lan_exposed_false_when_bind_addr_unreadable`.
Commit: `f0bb5221`.

### B6 — render W1's four missing per-call fields

`model`, `backend`, `tokens_in` hoisted onto `lanes_snapshot()`'s
per-lane dict the same way `brain`/`effort`/`tokens_out` already were;
`admin.html`'s `renderAgentLanes()` grows four columns (Model, Backend,
Effort, Tokens in) so all seven of W1's mandatory fields are on screen.

Test: `tests/test_admin_agent_lanes_endpoints.py::test_w1_seven_fields_all_render_in_admin_lane_table`
(reads the real rendered `admin.html` source, not a duplicate),
`::test_lanes_snapshot_carries_all_seven_w1_fields` (the data-side half).
Commit: `a66b9f0f`.

### S1 — recorder-off must mean stop showing too, plus a permanent Purge (operator decision (c): both halves)

New shared `agent_trace._strip_bodies_if_recorder_off()` re-checks
*current* recorder state (the same check `/api/agents/prompts` already
had right) before `find()`, `lanes_snapshot()`'s embedded `last`, and the
admin SSE stream (`subscribe()`) serve a record's bodies — a record's
`bodies` field is latched at call start (F10) and never changes
afterward, so without this a body captured while the recorder was on
stayed servable forever after the operator turned it off. This read-gate
is deliberately reversible (turning the recorder back on re-exposes an
old record, since the record itself isn't mutated).

The permanent half: new `agent_trace.purge_flight_recorder_bodies()`,
reusing `jsonl_purge.purge_jsonl_bodies()` — the SAME mechanism
`activity.py`'s legacy-bodies purge uses, not a second one — for
`agent_traces.jsonl` + its rotation, plus an in-memory ring pass mirroring
B2's `activity_log._buffer` fix. Added `bodies_purged` to `agent_trace`'s
fixed field list so it's always-present like every other documented
field. `POST /api/admin/flight-recorder` grows an optional
`{purge: true}`, run in the same request as an `enabled` flip, per
REVIEW.md's own suggested shape.

UI: a "Purge captured bodies" button on the recorder toggle, and —
separately, the other half of operator decision (c) — the legacy-bodies
notice + Purge/Keep buttons that never had a UI before now (three
endpoints existed, zero UI), populated by a boot-time
`loadLegacyBodiesNotice()` call alongside the existing agent-lanes boot
call.

Test: `tests/test_flight_recorder.py::test_find_hides_bodies_once_recorder_turned_off`,
`::test_find_reveals_bodies_again_if_recorder_turned_back_on`,
`::test_lanes_snapshot_last_hides_bodies_once_recorder_turned_off`,
`::test_subscribe_hides_bodies_once_recorder_turned_off`,
`::test_strip_bodies_if_recorder_off_no_op_when_no_bodies`,
`::test_purge_flight_recorder_bodies_strips_ring_and_disk`,
`::test_purge_flight_recorder_bodies_is_permanent_even_if_recorder_turned_back_on`,
`::test_purge_flight_recorder_bodies_count_is_zero_when_nothing_captured`,
`::test_purge_flight_recorder_bodies_leaves_bodyless_records_untouched`;
`tests/test_admin_agent_lanes_endpoints.py::test_flight_recorder_endpoint_purge_option`,
`::test_flight_recorder_endpoint_without_purge_flag_does_not_purge`;
`tests/test_legacy_bodies_admin_endpoints.py::test_legacy_bodies_notice_and_buttons_exist_in_admin_ui`.
All read-gate and purge behaviours verified non-vacuous by mutating the
production code and confirming the matching tests go red.
Commit: `35729007`.

### Filed as debt, not fixed here

S2, S3, D1, D2, D3, D5, D7, F2, W1's wall-clock test, the conftest split,
and D8's remainder — see `sprints/BACKLOG.md` ("Buddy-front-and-center's
BLOCK-review fix loop — filed as debt, not fixed"), commit `f9deb6e5`.

### Not the builder's call

**W5's witness line** (REVIEW.md "Needs the operator" #4) must be the
operator's own words, signed, in `SPRINT.md` — not something the builder
writes on his behalf. Not present in `SPRINT.md` as of this fix loop;
left open for the operator, not fabricated here.

## Re-review index

One row per `REVIEW.md` finding worked in this fix loop, its commit, and
the test that proves it.

| Finding | Commit | Proving test |
|---|---|---|
| B1 | `da70792d` | `tests/test_redact.py::test_b1_non_utf8_secrets_env_still_redacts_other_values`, `::test_capture_body_returns_none_when_known_values_pass_raises` |
| B2 | `1197277e` | `tests/test_legacy_bodies_purge.py::test_purge_strips_bodies_from_in_memory_buffer_too` |
| D6 | `6841c59a` | `tests/test_halt_gate.py::test_halt_gate_fails_open_when_scheduler_import_fails`, `tests/test_router_trace_chokepoint.py::test_slot_info_fails_safe_when_scheduler_import_fails` |
| F13 | `6841c59a` | `tests/test_admin_agent_lanes_endpoints.py::test_f13_404_on_minimalist[get-/api/admin/agent-trace-stream-None]` |
| D8 | `6841c59a` | `tests/test_admin_agent_lanes_endpoints.py::test_admin_template_uses_sse_not_setinterval_for_lanes` |
| F9 wiring (7 sites) | `ebc18899` | `tests/test_speech_gate_wiring.py` (all 14 tests) |
| B3 | `084aa234` | `tests/test_admin_agent_lanes_endpoints.py::test_nav_halt_control_relabeled_to_hold_all_agents` |
| B4 | `084aa234` | `tests/test_admin_agent_lanes_endpoints.py::test_f17_copy_matches_behaviour` |
| B5 | `f0bb5221` | `tests/test_flight_recorder.py::test_recorder_state_lan_exposed_on_non_loopback_bind` |
| B6 | `a66b9f0f` | `tests/test_admin_agent_lanes_endpoints.py::test_w1_seven_fields_all_render_in_admin_lane_table` |
| S1 (promoted to must-fix by the operator) | `35729007` | `tests/test_flight_recorder.py::test_find_hides_bodies_once_recorder_turned_off`, `::test_purge_flight_recorder_bodies_strips_ring_and_disk` |
| D4 (resolved by operator decision (a)) | `ebc18899`, `084aa234` | `tests/test_admin_agent_lanes_endpoints.py::test_f17_copy_matches_behaviour` (Claim 2) |

## Final state (after the review fix loop)

- 10 commits since REVIEW.md's BLOCK (`9112df90` ledger entry through
  `f9deb6e5` debt filing): `da70792d`, `1197277e`, `6841c59a`, `ebc18899`,
  `084aa234`, `f0bb5221`, `a66b9f0f`, `35729007`, `f9deb6e5`.
- Every item in REVIEW.md's "Must fix before QA (blocking)" list (10
  items) is fixed and pinned with a test that was verified non-vacuous by
  mutation.
- All three binding operator decisions (a), (b), (c) implemented in full
  (S1 got both halves of its either/or, per decision (c)).
- 181-file differential vs `main@236504ca`, re-run after every commit in
  this fix loop: failures-only-on-this-branch stayed at **0** throughout.
- No commented-out code; no unowned TODOs added.
- `sprints/BACKLOG.md` carries every deferred item with its filing sprint
  named, so a future sprint can pick any of them up without re-deriving
  the reasoning.

## Re-review fix loop — WEAK_PASS, commit `051eac4a`

Six residual findings (R1-R6) from the architect's re-review of the
previous fix loop, worked before QA by orchestrator decision (recorded
in `SPRINT.md`) because two of them (R1, R6) QA would just re-discover.

### R1 — an existing-but-unreadable secrets.env still yielded an under-redacted body

`_parse_secrets_env` swallowed a read-time `OSError`/`ValueError` into an
empty list — the same shape as B1's bug, narrower: "can't read it"
collapsed into "found nothing", so `capture_body` proceeded with a
shape-pass-only body and `redactions: 0`, indistinguishable from a clean
prompt. Now raises `_SecretsUnreadable` instead of returning `[]`;
`redact()` keeps swallowing it (lenient, unchanged), `_redact_strict`'s
no-try policy lets it propagate to `capture_body`'s fail-closed `None`.
`errors="replace"` decode behaviour (B1's other half) untouched.

Test: `tests/test_redact.py::test_r1_existing_but_unreadable_secrets_env_fails_capture_closed`
(chmod 000 on a real file, skips under root),
`::test_r1_missing_secrets_env_still_captures`,
`::test_r1_mangled_but_readable_secrets_env_still_redacts_other_lines`.
Mutation: reverted `_parse_secrets_env` to its exact original
`except (OSError, ValueError): return values` — reproduced the
reviewer's own repro byte-for-byte (`{'redactions': 0, ...}` with the
secret still present, `body is not None`) — then restored the fix.
Commit: `ef593c1c`.

### R2 — the not-held hold-copy branch still overclaimed

`renderHoldControl()`'s not-held branch — what the operator reads
*before* deciding — still said "proactive speech immediately" and never
named findings/suggestions/announcements or that operational/error lines
and SRE crash alerts continue, even though the held branch had already
been narrowed. Rewritten to carry the same clauses. Checked `_nav.html`
too: it has one button whose title only appears in the not-held state
(no second branch to go stale), confirmed by grep and pinned with an
explicit absence check.

Test: `tests/test_admin_agent_lanes_endpoints.py::test_f17_copy_matches_behaviour`
(not-held assertions), `::test_nav_halt_control_relabeled_to_hold_all_agents`
("proactive speech" absence in `_nav.html`).
Mutation: reverted the not-held branch to its pre-fix text — red — then
restored.
Commit: `bf3e38f1`.

### R3 — F17's test could not catch a lying admission-control clause

The copy test asserted four phrases but never the one REVIEW.md's F17
row cares about most — "Calls already running finish (N in flight)",
the clause that stops the UI implying cancellation. Added the fifth
assertion, plus a check that the `${hold.in_flight}` slot itself is
present in the raw literal (so the substitution line can't silently
become a no-op).

Test: `tests/test_admin_agent_lanes_endpoints.py::test_f17_copy_matches_behaviour`
(Claim 5).
Mutation **M4** (the reviewer's exact mutation): `"Calls already running
finish "` → `"All running calls are cancelled immediately. "` — red
(before this fix loop's M4 ran green, all 27 admin tests passed) — then
reverted.
Commit: `bf3e38f1` (same commit as R2 — both are the same copy-pinning
test and touch the same extractor function).

### R4 — B6's header assertions were satisfiable by the function's own comment

`renderAgentLanes()`'s own explanatory comment contains the word "Model"
— asserting the bare word against the whole function's source let the
comment satisfy the check on the header's behalf. Now asserts the literal
`<th>{header}</th>` markup.

Test: `tests/test_admin_agent_lanes_endpoints.py::test_w1_seven_fields_all_render_in_admin_lane_table`.
Mutation **M15** (the reviewer's exact mutation): deleted `<th>Model</th>`
from the markup, left the comment intact — red (before this fix loop's
M15 ran green) — then reverted (no production change needed;
`admin.html`'s markup was already correct, only the test was too
lenient).
Commit: `c82d7a7e`.

### R5 — the dream() fix activates a path that has never run in production; S3 is now live

No code change — REVIEW.md's ruling was explicit that the `dream()`
NameError fix is correct and in scope, and no further code change was
required beyond what is already filed. `sprints/BACKLOG.md`'s S3 entry
rewritten to say plainly that the 160-char unredacted preview leak is now
**live** on an unheld lab (previously theoretical — `dream()` crashed
with `NameError` before reaching that line on every call, on pristine
main), not merely "gated this fix loop", and that everything in
`dream()` after the emit is newly-reachable, untested-in-practice code.
No mutation applicable (a docs-only fix).
Commit: `64eb165a`.

### R6 — the fix loop weakened test_reachable_on_maximus into vacuity

Adding the `_STREAM_PATHS` skip for the new SSE endpoint deleted the
pre-existing tightening branch (exactly 404 for the unknown-trace-id
case, exactly 200 for everything else), leaving only
`status_code in (200, 404)` — a broken admin gate that 404s on maximus
too would have satisfied this test just as well as the correct 200 did.
Restored the exact-status tightening plus a body-shape assertion
(`_EXPECTED_200_KEY` per path).

Test: `tests/test_admin_agent_lanes_endpoints.py::test_reachable_on_maximus`.
Mutation **M1b** (the reviewer's exact mutation): `admin_agent_lanes`'s
`_require_surface("admin")` → `_require_surface("no_such_surface")` — red
(`4 passed, 1 skipped` before this fix loop's M1b ran green) — then
reverted (no production change needed; the gate itself was never broken,
only this test had stopped checking it).
Commit: `59768bce`.

### Re-review index — R1-R6

| Finding | Commit | Proving test | Mutation run, result |
|---|---|---|---|
| R1 | `ef593c1c` | `tests/test_redact.py::test_r1_existing_but_unreadable_secrets_env_fails_capture_closed` | Reverted `_parse_secrets_env`'s except-clause to its original `return values` — reproduced the exact repro (`redactions: 0`, secret present) — reverted mutation |
| R2 | `bf3e38f1` | `tests/test_admin_agent_lanes_endpoints.py::test_f17_copy_matches_behaviour` (not-held assertions) | Reverted the not-held branch text — red — reverted mutation |
| R3 | `bf3e38f1` | `tests/test_admin_agent_lanes_endpoints.py::test_f17_copy_matches_behaviour` (Claim 5) | **M4**: admission-control clause rewritten to a cancellation lie — red — reverted mutation |
| R4 | `c82d7a7e` | `tests/test_admin_agent_lanes_endpoints.py::test_w1_seven_fields_all_render_in_admin_lane_table` | **M15**: `<th>Model</th>` deleted, comment kept — red — reverted mutation |
| R5 | `64eb165a` | n/a (docs-only) | n/a |
| R6 | `59768bce` | `tests/test_admin_agent_lanes_endpoints.py::test_reachable_on_maximus` | **M1b**: `admin_agent_lanes`'s gate surface swapped to a nonexistent one — red (`1 failed, 3 passed, 1 skipped`) — reverted mutation |

### Final verification

- Sprint test files (21 files, including `test_speech_gate_wiring.py`)
  run twice back to back: **321 passed, 1 skipped** both times, identical.
- Real `lab/data`: no `agent_traces.jsonl` / `flight_recorder.json` /
  `legacy_bodies_notice.json` / `secrets.env` after either run.
- No stray pytest processes after completion.
- 181-file differential vs `main@236504ca`, run three times total this
  fix loop: **17 failed** twice (identical set to the previous fix
  loop's verified baseline — failures-only-on-this-branch **0**), and
  once **18 failed** — the extra one,
  `tests/test_observability_under_load.py::test_metrics_latency_under_50ms_while_slot_held`,
  is a hard-coded wall-clock assertion (its own docstring notes "CI tol
  100 ms") that passed in isolation immediately after and passed again
  on the very next full 181-file run with no code change in between —
  a machine-load timing flake from running this sweep repeatedly back
  to back in the same session, not a regression from any commit in this
  fix loop. Not filed as new debt: it is the same class of flake
  `sprints/BACKLOG.md`'s existing "W1's wall-clock SSE test" entry
  already describes for a sibling test.
