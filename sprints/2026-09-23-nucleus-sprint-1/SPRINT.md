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
| review | architect (review) | REVIEW.md | done | 2026-09-23 | 2026-09-23 | round 1 BLOCK → loop 1 (12 commits); round 2 BLOCK → loop 2 (8 commits); round 3 WEAK_PASS |
| test | qa | TEST_REPORT.md | FAIL → loop 3 | 2026-09-23 | 2026-09-24 | round 1: FAIL — Gate A pipeline holds (460 passed / 14 strict xfail); full suite regresses 44 → 57 failures vs merge-base 236504ca |
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
| 2026-09-24 | F3: root-cause fix required before ship, time-boxed to one builder session; fallback = `tests/nucleus` in its own CI invocation (like `requires_qkz_bin`), documented in ARCHITECTURE §7 + CI, with a BACKLOG ticket carrying the bisection evidence | Architect (round 3 agent): QA showed the prefix without `tests/nucleus` is green, so it is nucleus-owned; lead is `conftest::_isolated_lab_data` env vars set before `arail.config` patch → import-time capture of tmp paths (`arail.activity.LOG_FILE`) |
| 2026-09-23 | Re-implement Nucleus against the brief; salvage `src/arail/build/preflight.py`, `world_corpus.py`, and the seal format selectively — do not port the old private pipeline wholesale | Operator; agrees with brief §1 and VISION.md |

## Skipped phases

| Phase | Reason |
|---|---|

## Notes
- Build loop 3 (79f22ed4): F1 fixed (test corrected — `/build` is a 308 → `/forge` per ARCHITECTURE §8/T-FORGE-4); F2 fixed (`docs/cli.md` nucleus verb group). F3 NOT fixed: `tests/nucleus` + the 18 victim files run clean together (582 passed); failures appear only with the full ~250-file root prefix — not a fixture-owned state leak; builder requests architect direction on CI isolation.
- TEST_REPORT.md round 1 (QA commits 479b9275..): FAIL on wider-suite regressions: F1 `test_tier_route_guards::test_minimalist_404s_on_maximus_routes` expects `/build` 404, now redirects to `/forge`; F2 `test_cli_verbs` F33 — `docs/cli.md` lacks `nucleus`; F3 18 Chat/deep-runtime/activity tests fail only when `tests/nucleus` runs first (state leak, unconfirmed cause). Builder's 'four pre-existing failures' claim was from a hand-picked file list; only `test_models_api` confirmed pre-existing. Gate B blockers pinned as strict xfails: stub laundering via forged stamps, LC estimator degenerate cases, Buddy guard env mismatch. Shared venv warning: `qukaizen-arail/.venv` editable install now points at this worktree's `src/` — re-run `pip install -e` from the main checkout after merge.
- REVIEW.md round 3: WEAK_PASS. All 7 loop-2 items confirmed fixed by re-running. Required before merge (tests/backlog only): R3 proving test must reach the eval step and fail with fix reverted; card-alone recompute test must pass `formula_id` and pin lc_win_rate 0.8676; refresh BACKLOG umbrella (ASK A10). Ticketed before Gate B: `open_lc_judge.score` returns 0.5 when all length deltas equal and 1.0 when wins/losses separate by length (must fall back to raw win rate); forged all-stamps / deleted-stamps variants still certify; `QUEUELLM_MODEL` vs `AEROLLM_MODEL` mismatch in preflight advisory text.
- Build loop 2 (d56bc9a9): R1–R3 + judge-identity in PC + forged context.json cross-check + real eyeball outputs + v1-open COMPATIBLE cap, 8 commits, 368 passed / 2 skipped. Finding: constant length delta made the LC regression fit singular (lc_win_rate stuck at 0.5); fixed with per-item varying stub completion length (golden 0.8676).
- REVIEW.md round 2: BLOCK on R1 (card hardcodes `open_ended: not_run` while composite uses lc_win_rate 0.5; baselines empty; stub texts identical so golden can't catch a swap bug), R2 (`eval_hash` omits judge identity/rubric/eyeball prompts), R3 (Buddy guard misses `AEROLLM_MODEL=<abs path>`). `composite/v1-open` accepted with conditions: capped at COMPATIBLE until non-stub builds allowed; retired once patch checks are wired (ARCHITECTURE §4.9, §9 item 7). Pressing ASKs: PC never calls judge-identity check; forged `context.json stub:false` still certifiable; eyeball outputs in build report are placeholders.
- Build loop 1 (f17828eb): B1–B10 + lab/data isolation fixed in 12 commits; nucleus suite 344 passed / 2 skipped. Flagged for architect: `executable.patch_applies`/`checkpatch_clean` left `not_run` on the stub path (no patch-generation task in scope) with a `composite/v1-open` formula auto-selected — needs architect confirmation.
- REVIEW.md round 1: BLOCK. Core defect: `build.py`/`certify.py` seal placeholder or hardcoded values as measured (B1 stub build certifiable as real — reproduced; B2 `verify` reports unrun checks; B3 hardcoded lc_win_rate/base_composite; B4 eval_hash hashes scores; B5 broken eval tasks; B6 tautological Buddy guard; B7 non-stub build not refused up front; B8 git runner honours repo config `gpg.program` — reproduced; B9 certified cards never reach `$ARAIL_MODELS_DIR/forge/`; B10 Gate A e2e asserts no metric values). Naming freeze, seal format, cert-set/contamination/F1 tests, airgapped refusal, secrets handling, `/build` retirement all checked out.
- BUILD_LOG.md (803d7d99): real MLX multi-cycle training loop (`build.py::_mlx_train_cycle_fn`) is a deliberate stub — must be completed before Gate B; T-SEAL-8 passed against the real Rust `qkz` verifier. Four pre-existing failures outside `tests/nucleus` (test_models_api health refresh, 3× test_docs_routes) confirmed pre-existing, not touched by this sprint.
- ARCHITECTURE.md (58b754bc): 27-commit build order. Findings that change the plan: (a) pinned v1.1.0 bundle is AeroLLM 1.0.0 without `unstable-api` — no logprobs, so logit mode = teacher-generated sequences with top-N logprobs; (b) `qkz isotope verify` accepts only the 5-field ASCII/no-float seal and trusts the embedded key — design signs 5-field + trusted-key check in `arailctl nucleus verify`; (c) teacher `auto` = Qwen3-30B-A3B (MoE, tokenizer-matched to Qwen2.5 student); Llama-70B excluded. Buddy-voice 10 % reallocated +5 security / +5 happy.
- VISION.md (c8b1cefc): proceed on items 1, 2, 6–9 + read-only card viewer; defer 3–4 (no `arail-ops` MCP server exists; Buddy brief says guard rails first). Operator questions pending: scope, seal continuity (`qkz isotope verify` vs new ARAIL key), supersede distill-now / fold or retire `/build`.
- Open question from brief §1 (move old private Nucleus code wholesale vs re-implement against the brief): brief recommends re-implement. Architect should surface if this needs the operator before build.
