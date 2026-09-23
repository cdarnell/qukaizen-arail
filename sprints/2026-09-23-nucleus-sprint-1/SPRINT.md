# Sprint: nucleus-sprint-1

**ID:** 2026-09-23-nucleus-sprint-1
**Started:** 2026-09-23
**Product:** arail
**Branch:** `qukaizen/arail-nucleus-sprint-1` (worktree `~/ProJects/arail-nucleus-wt`, cut from origin/main)
**Brief:** `sprints/nucleus-in-arail-brief.md` (committed 11826fc3)

## Task
Make Project Nucleus a first-class ARAIL surface ("Model Forge"): `src/arail/nucleus/` package, `arailctl nucleus <verb>`, five `arail-ops` MCP tools, Buddy `nucleus-partner` skill, `/forge` route, preflight (memory plan / Buddy residency / tokenizer parity / contamination), eval harness (dev/cert + temporal split, F1, LC pairwise judge, executable kernel checks, eval hash), DNA card v2 + build report + `CERTIFIED_MODELS.md` appender, gateway contract doc + client stub + mock-server tests. Profile proven this sprint: `local`, QueueLLM as the default local runtime (Ollama/AirLLM fallback only when the deep runtime is absent). Scope items 1–9 first; item 10 (real reduced-corpus M5 build of `qkz-linux-kernel`) last, only after stub-provider tests pass. Gateway rework itself is sprint 2.

## Phases

| Phase | Subagent | Artifact | Status | Started | Finished | Verdict |
|---|---|---|---|---|---|---|
| think | visionary | VISION.md | done | 2026-09-23 | 2026-09-23 | proceed (narrower wedge; 3 operator questions before plan) |
| plan | architect (design) | ARCHITECTURE.md | done | 2026-09-23 | 2026-09-23 | complete (58b754bc); operator questions Q1–Q9 open, Q1 blocks Gate B only |
| build | builder | BUILD_LOG.md | done | 2026-09-23 | 2026-09-23 | Gate A reached (commits 1-27, 14dd54aa); commit 28 / Gate B / item 10 out of scope per operator instruction |
| review | architect (review) | REVIEW.md | pending | — | — | — |
| test | qa | TEST_REPORT.md | pending | — | — | — |
| ship | — | PR | pending | — | — | — |

## Decisions log

| Date | Decision | Rationale |
|---|---|---|
| 2026-09-23 | Sprint runs in a fresh worktree on a branch cut from origin/main | Main arail checkout is on `qukaizen/arail-ingress-spine` with uncommitted work; keep the sprint clean |
| 2026-09-23 | Brief decisions D1–D8 are locked inputs, not up for re-litigation | Operator set them in the brief; visionary validates the wedge and win, not the decisions |
| 2026-09-23 | Item 10 (real M5 build) is sequenced last, gated on stub-provider tests passing | Operator instruction |
| 2026-09-23 | Scope = narrow wedge: items 1, 2, 6–9 + read-only `/forge` DNA-card viewer; Gate A (stub tests) → Gate B (half-day M5 spike) → item 10. Items 3 (MCP tools) and 4 (Buddy skill) deferred to a follow-on sprint, with the "first-time user via Buddy" criterion | Operator, per VISION.md: no `arail-ops` server exists; Buddy brief sequences guard rails first |
| 2026-09-23 | DNA card v2 `signed:` verifies with Nucleus's existing `qkz isotope verify` seal format — no new ARAIL key | Operator: continuity with certified models |
| 2026-09-23 | `2026-07-22-distill-now` is superseded; the existing `/build` tab is retired, `/forge` is new and separate | Operator |
| 2026-09-23 | Q1: logprobs via an opt-in local `aerollm-api` build with `unstable-api` (ARCHITECTURE commit 28); shipped bundle stays pinned. Logit mode = teacher-generated sequences with top-N logprobs | Operator |
| 2026-09-23 | Q4: fallback composite formula and decision thresholds approved as designed (ARCHITECTURE §4); formula string is published in the card | Operator |
| 2026-09-23 | Q5: `certify` writes a local ledger by default; tracked `docs/CERTIFIED_MODELS.md` is appended only with `--publish-row` | Operator |
| 2026-09-23 | Q9: builder may update repo `CLAUDE.md` for the `/build` → `/forge` retirement | Operator |
| 2026-09-23 | Q2, Q3, Q7 (lineage key, 24 GB cap, network prep) deferred to Gate B | Not needed for Gate A |
| 2026-09-23 | Re-implement Nucleus against the brief; salvage `src/arail/build/preflight.py`, `world_corpus.py`, and the seal format selectively — do not port the old private pipeline wholesale | Operator; agrees with brief §1 and VISION.md |

## Skipped phases

| Phase | Reason |
|---|---|

## Notes
- ARCHITECTURE.md (58b754bc): 27-commit build order. Findings that change the plan: (a) pinned v1.1.0 bundle is AeroLLM 1.0.0 without `unstable-api` — no logprobs, so logit mode = teacher-generated sequences with top-N logprobs; (b) `qkz isotope verify` accepts only the 5-field ASCII/no-float seal and trusts the embedded key — design signs 5-field + trusted-key check in `arailctl nucleus verify`; (c) teacher `auto` = Qwen3-30B-A3B (MoE, tokenizer-matched to Qwen2.5 student); Llama-70B excluded. Buddy-voice 10 % reallocated +5 security / +5 happy.
- VISION.md (c8b1cefc): proceed on items 1, 2, 6–9 + read-only card viewer; defer 3–4 (no `arail-ops` MCP server exists; Buddy brief says guard rails first). Operator questions pending: scope, seal continuity (`qkz isotope verify` vs new ARAIL key), supersede distill-now / fold or retire `/build`.
- Open question from brief §1 (move old private Nucleus code wholesale vs re-implement against the brief): brief recommends re-implement. Architect should surface if this needs the operator before build.
