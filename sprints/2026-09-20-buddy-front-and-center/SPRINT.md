# Sprint: buddy-front-and-center — P1: observability + guardrails

**ID:** 2026-09-20-buddy-front-and-center
**Started:** 2026-09-21 12:49 CDT
**Product:** arail
**Branch:** `qukaizen/arail-buddy-front-and-center` (cut from main `236504ca`)
**Worktree:** `~/ProJects/arail-buddy-wt` — all work happens here. The main
`qukaizen-arail` checkout holds the operator's unrelated in-flight work and
must not be touched.

## Task

Phase **P1** of `OPERATOR_BRIEF.md` only: put observability and guardrails up
over the agents that already exist, before any new Buddy behaviour. Six
deliverables: (1) one trace per agent decision with a trace id, and per-agent
attribution replacing the blanket `billing_source="agent"`; (2) a single
agent-inference gateway that the ~5 ad-hoc model-acquisition paths collapse
behind, enforcing priority (user turn > Chat tab > proactive line > prep),
budgets and tracing; (3) a maximus-only Admin "Agents control room" showing
every agent as a living lane — state, brain + effort, last TTFT, tokens,
timeline, the inference slot, `deep_policy.explain()` reason codes verbatim;
(4) per-agent budgets and a visible "hold all agents" kill switch; (5) the
typed-action allow-list scaffold, with no new actions yet; (6) a
flight-recorder toggle for prompt/response bodies — off by default, local-only,
redacted.

Operator steer: "observability and guard rails go up first"; fun, living views
of agents — **not** a metrics product (no Prometheus endpoint, no OTel export,
no new CLI).

**Out of scope:** the Buddy panel, goal interview, event-driven voice, prep
queue, guided demo, brain bake-off, screen sense, and any rename of the
aerollm-named integration surface.

## Inputs

- `OPERATOR_BRIEF.md` — operator interview, decisions D1–D18, verified code
  facts (re-checked against main 2026-09-21), constraints, kill criteria. The
  visionary pressure-tests this rather than re-asking the operator.

## Phases

| Phase | Subagent | Artifact | Status | Started | Finished | Verdict |
|---|---|---|---|---|---|---|
| think | visionary | VISION.md | done | 2026-09-21 12:49 | 2026-09-21 13:00 | **proceed** — conditional on four cuts (commit `2884569f`) |
| plan | architect (design) | ARCHITECTURE.md | done | 2026-09-21 13:10 | 2026-09-21 13:30 | complete — 12 sections, slices S0–S7 (commit `600c9541`) |
| build | builder | BUILD_LOG.md | done | 2026-09-21 13:35 | 2026-09-21 16:05 | S0–S7 complete (`ffcb1ba3`…`0d1de0b8`) + two orchestrator-found defect loops (`fc9311a6`, `1bedf165`) |
| build (fix loop) | builder | BUILD_LOG.md | in progress | 2026-09-21 16:40 | — | REVIEW.md must-fix list + operator answers below |
| review | architect (review) | REVIEW.md | done → loop back to build | 2026-09-21 16:10 | 2026-09-21 16:35 | **BLOCK** (commit `0c13a176`) — ten must-fix items, no redesign |
| test | qa | TEST_REPORT.md | pending | — | — | — |
| ship | — | PR | pending | — | — | — |

## Decisions log

| Date | Decision | Rationale |
|---|---|---|
| 2026-09-21 | Sprint scoped to brief phase P1 only | Operator D18: "observability and guard rails go up first." Nothing new speaks, acts, or spends compute until it can be seen and bounded. |
| 2026-09-21 | Work in a dedicated worktree cut from main, not the main checkout | Main checkout is on the QA-failed `qukaizen/arail-ingress-spine` branch with the operator's uncommitted work; main also carries the `_normalize_keep_alive` resident-pin fix that older branches lack. |
| 2026-09-21 | No push, no PR until the operator says so | Ship is an explicit confirmation step. |
| 2026-09-21 | Visionary narrowed P1 from six deliverables to four + one counter | Wedge = trace + per-agent attribution at the existing `ModelRouter.complete/stream_complete` chokepoint · one shrunken Admin lane view · the kill switch made real · the flight recorder as a *reduction* of default capture · a read-only slot-overlap counter. **Cut/deferred:** the inference gateway (≈11 acquisition sites not ≈5, four non-agent, one out-of-process — the riskiest refactor would land with no telemetry, "D18 violated in the name of D18"), per-agent budgets (unenforceable without the gateway, no data to set numbers), the typed-action scaffold (no consumer until P5), and the control room's scrub/conveyor/gauges. Recommendation reverts to *defer* if the gateway re-enters this sprint. |
| 2026-09-21 | **Operator accepted the gateway cut** | P1 = tracing + attribution at the existing chokepoint, one Admin lane view, a real kill switch, flight recorder, overlap counter. The gateway becomes its own sprint, built with telemetry already in place. Visionary's "proceed" stands. |
| 2026-09-21 | **OQ1 — flight recorder defaults OFF** (operator) | Fresh lab captures metadata only; the Prompt Inspector shows an empty state ("flight recorder off — flip to capture prompt bodies"). No redacted-by-default middle setting, no per-operator env override. |
| 2026-09-21 | **OQ3 — "hold all agents": SRE keeps watching but stops calling models** (operator) | Hold stops every agent's inference and speech, including SRE's LLM-written summaries; SRE's non-LLM crash detection keeps running and may raise a plain template alert. The switch must state this in the UI. |
| 2026-09-21 | **OQ2 — per-agent budgets deferred** (operator) | No hard ceiling in P1. Budgets land next sprint with traced usage behind the numbers. |
| 2026-09-21 | OQ4 — lane roster: fixed list of model-calling built-ins; user-defined loader agents render as a generic lane | Visionary's recommendation; first-class user-defined lanes are unbounded. Orchestrator decision, revisitable in build. |
| 2026-09-21 | **One halt switch, wider meaning** (operator) | The existing dashboard "Halt jobs" control (`scheduler.jobs_halted()`) becomes "hold all agents": background jobs, agent inference and proactive speech all stop. Relabelled so the UI states exactly what it holds. No second flag. |
| 2026-09-21 | **Legacy bodies: disclose and offer a purge** (operator) | Admin shows a one-time notice of bodies captured before the flight recorder existed, with a Purge button. Nothing is rewritten without the owner pressing it. No auto-purge, no silent leave. |
| 2026-09-21 | **`ARAIL_AGENT_STREAM_FAST` on by default** (operator) | Buddy's fast path streams from Ollama (same endpoint, `stream:true`, deltas joined to the identical string) so agents get a true TTFT; env flag disables it. The deep QueueLLM path shows an honest `n/a` — that backend has no `stream_complete`. |
| 2026-09-21 | Architect's spec corrections accepted into scope | (1) no agent calls `stream_complete` today, so TTFT needs the `deep_policy` fast branch to stream; (2) `AeroLLMBackend` cannot stream → deep TTFT is `n/a`; (3) W2 ("0 generic `agent` calls") needs a legacy-key migration because `calls_by_source` is persisted. Also noted: SRE calls no model today, so OQ3's "stops calling models" is currently vacuous but stays as the contract. |
| 2026-09-21 | **Build looped back twice before review (orchestrator verification)** | Loop 1: the new suite passed on a clean tree and failed on the second run — four new test files drove the instrumented chokepoint without redirecting `DATA_DIR`, leaking 2,972 trace records into the worktree's real git-ignored `lab/data/`; because every `ModelRouter.complete()` now writes a trace, the pre-existing suite leaked too. Fixed with one autouse conftest guard + a hermeticity regression test (`fc9311a6`). Loop 2: a differential sweep against pristine main (181 files) showed the guard itself caused 3 regressions — it pre-seeded first-run state for every test (broke `test_dashboard_unblocks_after_onboarding`), and two new flight-recorder tests were order-dependent and could pass vacuously on an empty event list (`importlib.reload(arail.activity)` in a boot test rebinding the `ActivityLog` singleton). Fixed without weakening any assertion (`1bedf165`). |
| 2026-09-21 | "Pre-existing failure" now means "fails on main too, verified" | Final differential vs `main@236504ca`: 17 fail on both, **0 fail only on this branch**. Sprint test files: 256 passed, twice back-to-back; real data root clean. Orchestrator re-ran all of this independently rather than accepting the builder's report. |
| 2026-09-21 | Review verdict BLOCK — loop back to build | Spine judged sound (chokepoint overhead 0.048 ms p95, TTFT honesty as specified, new endpoints tier-gated before body parse, CSRF inherited, no scope creep, `/metrics` untouched, no frozen-name renames). Blocking: redaction degrades open on `UnicodeDecodeError` (F11 not mitigated); legacy purge leaves bodies in the in-memory buffer served by unauthenticated `GET /api/activity/recent`; the halt control's label unchanged despite the binding decision; legacy-bodies notice + Purge button have endpoints but no UI; LAN-bind × recorder banner dropped silently; F17 copy test compares against a duplicate; W1 fields in JSON but not on screen; two unguarded imports can raise into an inference; no test pins the `speech_gate` sites. |
| 2026-09-21 | **Hold = widen Buddy's gating + narrow the copy to the truth** (operator) | Gate Buddy's remaining proactive lines (online notice `_builtin_buddy.py:1263`, dream announcement `:1473`) and make the copy exact: "agents stop calling models and stop posting findings, suggestions and announcements. Operational/error lines and SRE crash alerts continue." Resolves REVIEW D4 / B3. |
| 2026-09-21 | **Build the LAN-bind × live-recorder banner now** (operator) | Portal has no auth; a friend/family lab bound beyond loopback with the recorder on exposes prompt bodies to the network. Resolves REVIEW B5. |
| 2026-09-21 | **Recorder off = stop capturing, stop showing, offer a purge** (operator) | Bodies captured while on are no longer served once off; Admin offers the same Purge used for legacy bodies to delete them from disk and memory. One purge mechanism. Resolves REVIEW S1 (promoted from ASK to must-fix). |

## Skipped phases

| Phase | Reason |
|---|---|

## Notes

- Visionary findings the brief did not have (VISION.md V1–V10), all verified in
  code: Buddy's inference is 100% invisible (no emit, no model, no latency);
  agent calls never enter `inference_slot` (there is no queue to re-order —
  agents are outside it); only 4 of 19 agent modules honour `jobs_halted()`, so
  today nothing can make Buddy stop; researcher + browser already write
  unredacted prompt/response bodies to `lab/data/activity.jsonl`, served by an
  unauthenticated `GET /api/agents/prompts`; no TTFT exists on the agent path;
  `/api/agents/status` reports `max_tokens` (the requested ceiling) labelled as
  token usage; a Prometheus `/metrics` endpoint already exists and must not be
  deleted in the name of D16.
- Open questions routed to the operator before plan: OQ1 (Prompt Inspector
  empty by default), OQ2 (budget number or accept deferral), OQ3 (does "hold
  all agents" silence the SRE crash watcher). OQ4 (lane roster) decided during
  build per the visionary's recommendation: fixed built-in list, user-defined
  agents as a generic lane.

- Known pre-existing test failures on main, not introduced by this sprint:
  `test_notice_byte_identical_to_sibling_when_available` (bundled NOTICE vs
  rebranded engine NOTICE — clears when the bundle is re-cut) and
  `test_backends_raises_on_sentinel_before_any_load`. In a fresh worktree
  `test_cross_link_audit_all_internal_links_resolve` also fails because
  `lab/pkb/skills/` is git-ignored state seeded by setup.
- Tests in this worktree run with the main checkout's interpreter:
  `/Users/netsushi/ProJects/qukaizen-arail/.venv/bin/python -m pytest …` —
  `tests/conftest.py` pins `sys.path` so `arail` imports from this worktree
  (verified 2026-09-21).
