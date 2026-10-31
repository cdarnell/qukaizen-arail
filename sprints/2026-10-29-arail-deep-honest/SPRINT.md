# Sprint: ARAIL deep mode honest

**ID:** 2026-10-29-arail-deep-honest
**Started:** 2026-10-29
**Product:** arail (`qukaizen-arail`); touches qukaizen-queuellm only to file an issue (operator OK needed)
**Branch:** `qukaizen/arail-deep-honest` (from arail main `62d5ebdf`)
**Worktree:** `<session>/arail-deep-wt`
**Spec of record:** Buddy S6 cut `/deep` at its day-1 gate (buddy PR #6, `DAY1.json`, debt D-S6-8/10/11). This sprint fixes the ARAIL side of why.

## Task

Make ARAIL's deep mode report failure the way it reports success, and make the rebuild path tell the truth. See VISION.md for the single pass/fail win and the cut rules.

## Phases

| Phase | Subagent | Artifact | Status | Started | Finished | Verdict |
|---|---|---|---|---|---|---|
| think | visionary | VISION.md | done | 2026-10-29 | 2026-10-29 | proceed (`942cbda8`): `deep probe` through ARAIL's own backend, a failure that stays visible until a probe passes, Curator never says "All clear" after a failed call, honest build script; cut the opt-in fall-back switch; building main only if a half-day scratch check gets 5 of 5; revisit 2026-11-19 |
| plan | architect (design) | ARCHITECTURE.md | done | 2026-10-29 | 2026-10-29 | complete (`e3f47816`). Day-1 check run in scratch: main `e730d5fe` builds (1 min 57 s) but answers 1 of 5; the 2026-10-11 build 1/5; bundle v1.1.0 1/5; live wheel 1/5. So the build-main item is cut; only the honest refusal ships. Curator grow already lied 20 times across 3 Worlds (18 in `ai`, 1 `debt-finance`, 1 `sophie-school`: "+0 terms" then reseal) and hid 496 failed deep calls since 2026-08-15. Deviation D1: failure stored per binary in one checkout-wide file, cleared only by a 5/5 `deep probe`. DAY1.json and ISSUE.md are builder step 0 (Appendix A, B). |
| build | builder | BUILD_LOG.md | done | 2026-10-29 | 2026-10-31 | complete (code at `ebdbaa23`; not pushed). 29 builder commits for steps 0 to 9. Full suite against the untouched-main baseline: 0 new failures (the same 127 failing ids; 8,085 passed, +288 new tests). Live check on today's broken wheel (QueueLLM 0.1.0-rc.2, sha256 d62e1e16): 9 of 9 rows pass, exit 0, the probe says 1 of 5 (exit 1), 0 silent outcomes, four runs, the live venv unchanged. Six product mutations all caught. Nothing needs a redesign; six notes for the architect in BUILD_LOG (the fast-tier environment, W7 against the no-`deep rebuild` rule, an ASGI client instead of `TestClient`, two traces per failed fast call). TD1 to TD13 filed in `sprints/BACKLOG.md`. |
| review | architect (review) | REVIEW.md | done | 2026-10-31 | 2026-10-31 | **BLOCK** (`46ebcc30`). B1: the backend hook files ARAIL's own teardown (Eject, a model switch, interpreter shutdown, a queued call cancelled by `_close`) as a failure of the binary, which locks deep checkout-wide until a 5/5 probe, and it misses a native panic (a `BaseException`), which leaves a Curator review stuck at "running". B2: the no-checkout hint in `build-aerollm.sh` tells users to clone QueueLLM, a build this ARAIL refuses; TD13's four portal hints are ruled fix-now with it. WEAK W1 to W5 and notes N1 to N12 are in REVIEW.md. 31 of 33 product mutations caught; 332 tests pass; architect feedback 1 to 5 accepted. Live check not re-run by the reviewer (a native abort would write a crash report). |
| test | qa | TEST_REPORT.md | pending | | | |
| ship | — | PR | pending | | | |

## Decisions log

| Date | Decision | Why |
|---|---|---|
| 2026-10-29 | Architect: item 2 (build QueueLLM main) cut by the day-1 evidence; items 1, 3, 4 ship. | No binary passes call 2, so a rebuild cannot help; the honest refusal can. |
| 2026-10-29 | Operator: "complete the development and integration and instruct me on what to do". The orchestrator runs the pipeline to merge; the operator-only steps are listed at the end. | Standing instruction to keep going. |
| 2026-10-31 | Builder: three things the architecture left open were settled inside the live check, not in the product: an ASGI client instead of `TestClient`, a non-existent model name for the fast tier, and `build-aerollm.sh build` instead of `./arailctl deep rebuild`. | The Curator's background task needs one event loop with no lifespan; a four-key `.env` leaves no fast router; this build's rules forbid running `deep rebuild`. See BUILD_LOG, Deltas 5 and Architect feedback 1 to 3. |
| 2026-10-31 | Orchestrator: the fix pass is B1, B2 with TD13, and W1 (the review's required list), plus W3, W4, W5, N3, N4 and N8. W2, N1, N2, N5, N6, N7 and N9 to N12 go to the backlog with their triggers. Re-review covers only the fix-pass diff. QA runs the full suite against a baseline measured in the same pinned venv. | The extras are small and sit in this sprint's own new code. W4 is the operator's standing rule that runs must teach. N3 is a check that fails open and N4 writes a false `queuellm_dirty: false`; both are the defect class this sprint exists to remove. |
| 2026-10-31 | The reviewer's commit trailer names Claude Opus 5.5, the model that ran it, not the Sonnet 5.5 line the orchestrator asked for. Kept as written. | A trailer should name the model that did the work. |

## Notes

- Needs the operator, not blockers: any change to `~/ProJects/qukaizen-arail/.venv`; filing the QueueLLM issue; the Metal repair only if it fails again; the model choice on 2026-11-19.
