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
| build | builder | BUILD_LOG.md | pending | | | |
| review | architect (review) | REVIEW.md | pending | | | |
| test | qa | TEST_REPORT.md | pending | | | |
| ship | — | PR | pending | | | |

## Decisions log

| Date | Decision | Why |
|---|---|---|
| 2026-10-29 | Architect: item 2 (build QueueLLM main) cut by the day-1 evidence; items 1, 3, 4 ship. | No binary passes call 2, so a rebuild cannot help; the honest refusal can. |
| 2026-10-29 | Operator: "complete the development and integration and instruct me on what to do". The orchestrator runs the pipeline to merge; the operator-only steps are listed at the end. | Standing instruction to keep going. |

## Notes

- Needs the operator, not blockers: any change to `~/ProJects/qukaizen-arail/.venv`; filing the QueueLLM issue; the Metal repair only if it fails again; the model choice on 2026-11-19.
