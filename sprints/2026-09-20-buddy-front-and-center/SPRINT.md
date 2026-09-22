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
| build (fix loop) | builder | BUILD_LOG.md | done | 2026-09-21 16:40 | 2026-09-21 23:20 | 10 commits `da70792d`…`8e943ca5`; all must-fix items + 3 operator decisions; rate-limit interruption recovered |
| review | architect (review) | REVIEW.md | done → loop back to build | 2026-09-21 16:10 | 2026-09-21 16:35 | **BLOCK** (commit `0c13a176`) — ten must-fix items, no redesign |
| re-review | architect (review) | REVIEW.md (appended) | done | 2026-09-21 23:25 | 2026-09-21 23:40 | **WEAK_PASS** (commit `051eac4a`) — six residual ASKs R1–R6 from 17 mutation tests |
| build (R-loop) | builder | BUILD_LOG.md | done | 2026-09-21 23:45 | 2026-09-22 00:05 | R1–R6 fixed, each mutation-verified red-then-reverted (`ef593c1c`…`63484814`) |
| test | qa | TEST_REPORT.md | done → loop back to build | 2026-09-22 00:10 | 2026-09-22 00:55 | **FAIL** (commit `9d0e083f`) — W1–W4 PASS, hot path confirmed; 5 must-fix defects, 1 regression |
| build (QA loop) | builder | BUILD_LOG.md | in progress | 2026-09-22 01:00 | — | TEST_REPORT.md must-fix list |
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
| 2026-09-21 | Fix loop interrupted by an API rate limit; recovered without loss | Builder was terminated mid-loop (2 of 10 fixes committed, 5 files uncommitted). Orchestrator found the uncommitted D6 fix coherent and two test files passing, but a new SSE test (`client.stream()` on `/api/admin/agent-trace-stream`) hung every subsequent test because the server-side generator never terminated on close — HEAD's version of the file passed in 0.8 s. A stale pytest process was killed. Builder resumed with that diagnosis; fixed the test and checked the production generator's disconnect handling. |
| 2026-09-21 | Fix loop verified independently by the orchestrator | Sprint's 22 test files: 316 passed + 1 skipped, twice back-to-back, no hang, no stray process, real `lab/data` clean. 181-file differential vs `main@236504ca`: 0 failing only here, same 17 pre-existing. B1 live-checked: `_redact_strict` propagates, `capture_body` → `None` on a pass failure, test patches `_known_values` to raise. Halt copy present in `_nav.html` and the Admin card; W1's four fields rendered; LAN-bind × recorder banner wired. No frozen renames; `/metrics` intact. **Flagged for re-review, not ruled on:** an *existing-but-unreadable* `secrets.env` (OSError) yields an empty known-values list and the body is still captured — the reviewer's B1 criteria asked for that catch, so whether that is acceptable is the reviewer's call. |
| 2026-09-21 | Re-review WEAK_PASS — gate to QA passes | Reviewer mutation-tested 17 production edits: 14 red as claimed, 3 green → new findings. Satisfied: B2, B3, B5, D6, F9, F13, D8, S1 (recorder-off read-gate sits inside `subscribe()`, so the SSE stream cannot bypass it). SSE hang ruling: only the test was wrong — production ends the generator via ASGI task cancellation reaching `subscribe()`'s `finally`; D7 correctly stays debt. Unreadable-`secrets.env` ruling: a residual fail-open that must return `None` (R1). |
| 2026-09-21 | **Orchestrator: fix R1–R6 BEFORE QA, not after** (deviation from the reviewer's "before the PR, not before QA") | Two of the six are things QA's blind tests would simply re-discover (R1 fail-open — the reviewer's own "QA first" list names a `chmod 000 secrets.env` variant; R6 — `test_reachable_on_maximus` now passes with the admin gate broken, a test-strength regression introduced while fixing a test-strength finding). Running QA against known defects spends its budget re-finding them; one short builder loop is cheaper than a second QA pass. R2 (not-held copy still says "proactive speech" — operator decision (a) half-implemented), R3 (F17 test green under a rewritten admission clause), R4 (B6 header check satisfiable by a comment) ride along. R5 (`dream()` NameError fix activates a never-run path, making S3's 160-char unredacted preview live) → BACKLOG S3 entry corrected to "newly live", and QA is directed to exercise the dream path end-to-end. |
| 2026-09-22 | R-loop verified independently; gate to QA open | Orchestrator live-checked R1's three states (`secrets.env` unreadable → `None`; readable → redacted; absent → captured, shape pass only). Sprint's 21 test files 321 passed + 1 skipped twice; 181-file differential vs main: 0 failing only here; real `lab/data` clean; no stray processes. Builder observed one timing flake in `test_observability_under_load.py` (<50 ms wall-clock assertion) on one of three sweeps — passed in isolation and on the next sweep; same class as the existing wall-clock-test debt entry, not a regression. |
| 2026-09-22 | QA verdict FAIL — loop back to build | 263 QA tests / 7 files, allocation 29/30/21/10/10 vs the 30/30/20/10/10 target; QA-BLIND-1/2/3 authored from ARCHITECTURE.md contracts before reading the builder's tests (3 of 8 defects came from them). **W1–W4 all PASS** (7 fields 3/3 with `explain()` compared to a live call; 0 bare `agent` / 0 unattributed; 132 hold refusals / 0 admitted on a simulated clock; 0 bodies on a whole-tree grep). Hot path: `record()` p95 46–49 µs over three runs, matching the builder's 46.8 µs; full ring 50–58 µs; ENOSPC 7 µs — 20× under budget. **Must fix:** (1) `jsonl_purge.py:44-72` a failed `os.replace` still reports `{"purged": N}` — the operator is told secrets were deleted while every body remains; (2) `activity.py:264-270` the same failed purge clears the in-memory buffer anyway — evidence hidden, not removed; (3) `goal_parser/__init__.py:250` 80 chars of the child's raw exception text land in `error_class` — an `Authorization: Bearer …` fragment reaches `agent_traces.jsonl` with the recorder off (S2, now demonstrated); (4) `router/core.py:282-283, 371-373` unguarded `from arail import redact` + `capture_body` can raise into an inference (D6's third and fourth instances); (5) **23-test regression** — every `tests/test_recap_*` fails here only because `cost_tracker` is un-isolated and the suite has billed $6.37 of fake usage past recap's $5 ceiling into this worktree's git-ignored `lab/data/costs.json`; `ARAIL_DATA_DIR=$(mktemp -d)` → 174/174 pass. Orchestrator reproduced (1) and (5). Regression differential re-established on the whole 366-file tree: 40 fail on both main and branch, 23 only here (one root cause), 1 only on main (fixed by this branch) — supersedes the earlier 181-file "17/0". |
| 2026-09-22 | Polluted `lab/data/costs.json` in `arail-buddy-wt` is test pollution and will be deleted once `cost_tracker` is isolated | The worktree was created by the orchestrator on 2026-09-21 from main; its `lab/data/` never held operator data. The operator's main checkout is untouched. |

## W5 — operator's witness line (open)

VISION.md's fifth win condition is the only test of "fun vs boring metrics": the
operator opens the Admin agent-lanes view on an idle lab and narrates what the
agents are doing for ten minutes **without a terminal or a log file**. It passes
only if the operator writes the line below in their own words and signs it.
Nobody else may write it, and it may not be inferred from other evidence. If W5
fails, the next sprint's first task is a rewrite of the view, not more
instrumentation (REVIEW.md).

> _(operator's line, date, initials — unwritten)_

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
