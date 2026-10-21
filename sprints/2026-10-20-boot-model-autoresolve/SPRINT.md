# Sprint: boot-model-autoresolve

**ID:** 2026-10-20-boot-model-autoresolve
**Started:** 2026-10-20
**Product:** arail

## Task
Replace the two-slot picker banner + settle flow (shipped in PR #186) with boot-time
auto-resolution. At boot, resolve slot A (fast/resident, Ollama) and slot B (deep/streaming,
QueueLLM via the `aerollm` backend) from installed models + hardware fit, reusing the existing
resolve_slots/_gather_slot_facts logic, and warm both up front. `model_defaults.yaml` becomes a
last-resolved cache, not a "settled" gate, so env/file drift banners disappear. Keep a
non-blocking manual override. Also fix the "fast: ai-engineer @ 127.0.0.1:11434/v1 unhealthy —
400 Bad Request on /api/chat" banner (suspected model-name/config mismatch).

Base: PR #186 (qukaizen/model-selection-boot-f0316f) is MERGED to main; this branch
(qukaizen/model-inference-alignment-38a991) is at main.

## Phases

| Phase | Subagent | Artifact | Status | Started | Finished | Verdict |
|---|---|---|---|---|---|---|
| think | visionary | VISION.md | skipped | — | — | — |
| plan | architect (design) | ARCHITECTURE.md | done | 2026-10-20 | 2026-10-20 | n/a |
| build | builder | BUILD_LOG.md | done | 2026-10-20 | 2026-10-20 | n/a |
| review | architect (review) | REVIEW.md | done (round 2) | 2026-10-20 | 2026-10-21 | WEAK_PASS (R1 BLOCK fixed) |
| test | qa | TEST_REPORT.md | QA round 2 in progress (R1 FAIL fixed in build round 3) | 2026-10-21 | — | R1 FAIL |
| ship | — | PR | pending | — | — | — |

## Decisions log

| Date | Decision | Rationale |
|---|---|---|
| 2026-10-20 | Operator chose boot-time auto-resolve over picker/settle | "Squash all of this and the models load when ARAIL loads" |

## Skipped phases

| Phase | Reason |
|---|---|
| think | Direction and win condition set by the operator (no picker, models load at boot, no drift banners) |

## Notes
- Operator runs Worlds serially (see memory: worlds-serial-usage) — optimize single-instance boot, not multi-instance coordination.
- ARAIL gating: setup-on-clean-machine, failure-mode grace. Boot must not hard-fail if a model is missing.
