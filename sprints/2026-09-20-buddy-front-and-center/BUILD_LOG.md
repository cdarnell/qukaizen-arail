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

(filled in per slice as work completes)

## Architect feedback required

(empty unless a slice's assumption breaks)

## Final state

(filled in at handoff)
