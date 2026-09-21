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

Commit: `pending`

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

## Final state

(filled in at handoff)
