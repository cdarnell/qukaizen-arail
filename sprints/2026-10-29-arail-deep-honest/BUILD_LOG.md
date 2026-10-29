# Build log: ARAIL deep, honest

**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `e3f47816`
**Vision:** [VISION.md](./VISION.md) at `942cbda8`
**Branch:** `qukaizen/arail-deep-honest` (from arail main `62d5ebdf`)
**Started:** 2026-10-29
**Status:** in progress (skeleton committed before any code)

## Hard rules this build keeps

- The operator's live install at `~/ProJects/qukaizen-arail/.venv` is never written. Its interpreter is used read-only, and only by the live check (step 8). Every test run uses a scratch venv.
- `./arailctl deep rebuild` is never run. The QueueLLM repo is never edited. `ISSUE.md` is a draft; nothing is filed.
- No frozen name is renamed: `aerollm`, `AERO_*`, `aerollm_api`, `build-aerollm.sh`, backend id `aerollm`, `tier1-aerollm`, `AeroLLMBackend`.
- `src/dac_world/` is not edited. Buddy's contract bytes do not change.
- Tests use fixtures. The operator's real World instances are not touched.
- Cost text says "token-free", never "free".
- Every number the probe, the Curator, `deep status` and the win script print carries meaning, bar and why, out-of-range, and where the failure lives. Tests TQ1 to TQ4 fail the build if one does not.

## Baseline (untouched `origin/main` `62d5ebdf`)

Measured before any code change, in a scratch venv (Python 3.11.17) on an `git archive` copy of `origin/main`. See "Baseline result" under Execution.

## Plan

Order follows ARCHITECTURE "Recommended implementation order". Test-first applies to the high-risk pieces: the verdict store (U), the probe supervisor (Q), the Curator wrapper (C), the build script (S), and the Check-erase regression (H2). Their tests are written before or in the same commit as the code, and are run red where that is cheap.

| # | Files | Change | Test | Commit ref |
|---|---|---|---|---|
| 0a | `sprints/2026-10-29-arail-deep-honest/DAY1.json` | Appendix A, verbatim. The day-1 evidence. | parses as JSON | `818ea0bf` |
| 0b | `sprints/2026-10-29-arail-deep-honest/ISSUE.md` | Appendix B. QueueLLM issue draft. Not filed. | none (docs) | `3f443fcf` |
| 0c | `sprints/2026-10-29-arail-deep-honest/BUILD_LOG.md` | This skeleton. | none (docs) | this commit |
| 1a | `tests/fakes/__init__.py`, `tests/fakes/fake_deep_runtime.py`, `tests/conftest.py` | The fake deep runtime fixture (modes `pass`, `fail2`, `empty2`, `crash3`, `hang2`, `loadfail`, `slow:<s>`, `stdout-noise`). One autouse fixture: per-test `ARAIL_DEEP_STATE_DIR`, verdict caches reset. | the fake is exercised by 1b and later steps | pending |
| 1b | `src/arail/deep_verdict.py`, `tests/test_deep_verdict.py` | C1 and C2. Identity (loaded and static), pinning, `state_dir()`, the per-binary store with flock and atomic replace, fail-closed corrupt store, `note_call`, `note_load_failure`, `record_probe`, `current`, `TEACHING`, the `status` and `identity` CLI. | U1 to U13, SEC1 | pending |
| 2 | `src/arail/router/backends.py`, `tests/test_deep_backend_hook.py` | C4 backend hook. `note_call` in `complete()`, load failures in `__init__`, `health_check()` false while failing. | B1 to B5 | pending |
| 3 | `src/arail/agent_context.py`, `src/arail/agents/deep_policy.py`, `tests/test_deep_policy_failing.py` | C4 policy. `deep_failing` code in `explain()`. `complete_preferring_deep()` calls `explain()` once and stamps fast calls. New `with_deep_reason()`. | P1 to P5 | pending |
| 4a | `src/arail/registry/core.py`, `src/arail/registry/health.py`, `tests/registry/test_deep_verdict_overlay.py` | C5 tier-1 overlay in `to_state()`, `_gate_reason()` and `_probe_aerollm`. Announce once per change in `run_preflight`. Hints fixed (`health.py:124-125`). | H1 to H3, H5, H6, H8 to H12 (H2 first, red then green) | pending |
| 4b | `src/arail/portal/model_warmth.py`, test additions | C8 boot and preload lines. Never "ready" while failing. | H4, H7 | pending |
| 4c | `src/arail/portal/app.py`, `src/arail/portal/templates/research.html`, `tests/test_research_truth_deep_failing.py` | C5 truth strip: additive `generation` block, danger class for `deep_failing`. | T1, T2, SEC2 | pending |
| 4d | `tests/test_chat_deep_stays_loud.py` | Regression: Chat stays loud. | chat row | pending |
| 5a | `src/arail/deep_probe_prompts.py` | Appendix C. The day-1 prompt set. | Q16 (hash pinned) | pending |
| 5b | `src/arail/deep_probe.py`, `tests/test_deep_probe.py` | C3. Supervisor and child, the teaching renderer, exit codes 0, 1, 2, 5, 130. | Q1 to Q19, TQ1, TQ2, SEC3, SEC4, SEC6 | pending |
| 5c | `arailctl`, `docs/cli.md` | `deep probe` dispatch, help line, usage error, exit code 5, status lines, what Check does. | Q20 | pending |
| 6a | `src/arail/portal/world_routes.py`, `tests/test_curator_honest.py` | C6 `_CountedRouter`. Review, grow and forge report what happened. Grow does not reseal after a failed call. | C1 to C9, TQ3 | pending |
| 6b | `src/arail/portal/static/js/world-terms.js` | One notice line for `partial`, `failed`, `not_applied`. | C10 (static) | pending |
| 7a | `scripts/build-aerollm.sh`, `tests/test_build_aerollm_honest.py`, edits to `tests/test_aerollm_*` | C7. Detect once, refuse the renamed crate, preamble, shadow check, sha256 verification, build marker, honest `status`. | S1 to S11, TQ4 | pending |
| 7b | `scripts/setup.sh`, `src/arail/router/backends.py` (hint only) | `setup.sh:678` and `:685` stop claiming readiness. `backends.py:1570-1575` hint corrected. | S10 | pending |
| 8 | `scripts/deep-honest-win.sh`, `scripts/deep_honest_win.py` | The one live check. Run on this Mac on the broken wheel. | rows W0 to W8 | pending |
| 9 | `sprints/BACKLOG.md`, `sprints/2026-10-29-arail-deep-honest/SPRINT.md`, this file | TD1 to TD12 filed. Ledger build row. Final numbers. | full suite, `test_no_user_visible_aerollm.py`, `dac_world` byte-identical | pending |

Files not in this table are not touched. Any addition is listed under "Deltas from the plan" below with the reason.

## Execution

(filled in per step as the work lands)

## Deltas from the plan

(none yet)

## Architect feedback required

(none yet)

## Final state

(filled in at the end)
