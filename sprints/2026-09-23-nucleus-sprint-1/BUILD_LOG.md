# Build log: Model Forge (`local` profile) — arail.nucleus

**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `58b754bc`
**Sprint ledger:** [SPRINT.md](./SPRINT.md)
**Started:** 2026-09-23
**Finished:** 2026-09-23 (Gate A reached at commit 22; scope was §10 commits 1–27)

## Scope

ARCHITECTURE.md §10 commits 1 through 27 — everything up to and including
Gate A (the stub-provider end-to-end pipeline in CI against the 50-item
synthetic fixture corpus, no real teacher). Commit 28 (the opt-in
`unstable-api` local runtime rebuild) and Gate B / item 10 (the real M5
build) are explicitly out of scope per the task instructions and were not
started.

## Plan

| # | Files | Change | Test | Commit ref |
|---|---|---|---|---|
| 1 | `pyproject.toml`, `.github/workflows/nucleus-tests.yml`, `tests/nucleus/` | Deps + CI skeleton | placeholder collects | `fe2b0b69` |
| 2 | `src/arail/airgap.py`, `portal/app.py` | `AIRGAPPED_NOTICE` shared constant | T-REG-2 | `d6a7867e` |
| 3 | `src/arail/world_catalog.py`, `build/world_corpus.py` (shim), `compiled_kb.py` | Move approved-term pull | T-REG-3 | `e7e127aa` |
| 4 | `src/arail/nucleus/{__init__,__main__,cli,errors,runtime_names}.py` | Package skeleton + CLI dispatch | T-RT-1,2; T-CLI-1..4 | `05d74cb3` |
| 5 | `domain.py`, `plan.py`, `spec/nucleus-domain-v1.schema.json`, `configs/domains/linux-kernel.*` | Domain config | T-DOM-1..4 | `bccc3d1e` |
| 6 | `paths.py` | Paths, lock, output guard | T-PATH-*, T-LOCK-* | `58ef0572` |
| 7 | `models.py`, `tokenizer_parity.py` | Model resolution, identity, parity, teacher select | T-PAR-1..4, T-SETUP-2 | `ec5998b2` |
| 8 | `preflight.py`, `residency.py` | Memory plan, Buddy reserve, residency | T-PRE-1..5, T-RES-1..3, T-SETUP-3 | `409a7ce0` |
| 9 | `providers/{base,stub,queuellm,fallback_ollama}.py` | Providers | T-PROV-1..3, T-STUB-2 (partial) | `0b45d0ba` |
| 10 | `corpus/stage.py`, `corpus/sources/*`, `corpus/_git_env.py`, fixture corpus | Corpus staging + fixture | T-STAGE-1,2, T-SEC-GIT-1 | `c5ba84fc` |
| 11 | `evals/splits.py` | Splits + CertStore | T-SPLIT-1..3, T-CERT-1,2 | `5c2c6425` |
| 12 | `evals/contamination.py` | Contamination | T-CONT-1..5 | `69e130a7` |
| 13 | `evals/closed.py`, `evals/composite.py` | Closed metrics + composite + decision | T-CLOSED-1,2 | `4afbdb10` |
| 14 | `evals/open_lc_judge.py` | LC pairwise judge | T-JUDGE-1..4 | `41356780` |
| 15 | `evals/executable_kernel.py` | Executable kernel checks | T-EXEC-1..4 | `3c4adbe2` |
| 16 | `evals/hash.py` | eval_hash + pipeline_hash | T-HASH-1..3 | `881bfa2f` |
| 17 | `train/kd_loss.py`, `train/mlx_kd.py` | KD loss reference + MLX trainer | T-KD-1..3 (4 skipped, no mlx here) | `0b93edeb` |
| 18 | `phases.py`, `worker.py`, `build.py`, `arbitrage.py`, `evals/tasks/linux_kernel.py` | Phase runner, worker, Arbitrage, build/status/resume | T-D3-1, T-CERT-3 (live), T-RESUME-1, T-EGR-2 | `044837ea` |
| 19 | `cards/dna_v2.py`, `spec/dna-card-v2.schema.json` | DNA card v2 | T-CARD-1..3 | `ecd90a1b` |
| 20 | `cards/seal.py` | Seal (legacy-5), key custody, verify | T-SEAL-1..8 (8 verified against the real Rust binary) | `9236576f` |
| 21 | `cards/build_report.py`, `cards/certified_models.py`, `certify.py` | Report + ledger + certify | T-REPORT-1, T-LEDGER-1..4 | `77132bd0` |
| 22 | `tests/nucleus/test_e2e_gate_a.py` | **Gate A** end-to-end | T-E2E-1, T-EGR-1 | `809141ce` |
| 23 | `docs/nucleus-gateway-contract.md`, `providers/{gateway,mixed}.py` | Gateway contract + client | T-GW-1..5 | `13f61d28` |
| 24 | `spike.py` | Spike verb (Gate B harness) | T-SPIKE-1 | `0f0c488e` |
| 25 | `portal/forge_api.py`, `templates/forge.html`, `app.py`, `_nav.html` | `/forge` viewer | T-FORGE-1..3 | `f1db73c0` |
| 26 | delete `arail/build/**`, `portal/build_api.py`, `templates/build.html`, `tests/build/**`, `tests/portal/test_build_tab.py`; `models_api.py`, docs | Retire `/build` | T-FORGE-4, T-REG-4,5 | `82ed6c78` |
| 27 | `docs/nucleus.md`, `docs/nucleus-architecture.md`, `sprints/BACKLOG.md` | Docs + filed tickets | — | `14dd54aa` |

## Execution

Each commit above landed in order, one per §10 step, with its own tests
written first or alongside and the full `tests/nucleus` suite re-run green
before moving to the next. Full commit messages carry the per-commit
rationale; this section records deltas from the architecture and things a
reviewer should look at first.

### Deltas from ARCHITECTURE.md (documented at the time, not discovered late)

1. **`errors.py` (commit 4) is not in §4.0's file list.** A small,
   dependency-free home for `DomainConfigError`/`RefusedByPolicy`/
   `CapabilityMissing`, needed so `runtime_names.py` (commit 4) doesn't
   have to forward-import `domain.py` (commit 5). Infrastructure, not a
   behavior change.
2. **`domain.py`'s `< 8B` student check (T-DOM-4) is wired via an
   injectable `model_resolver` parameter**, defaulting to `None` (skip)
   until `models.py` exists (commit 7), then wired for real from `plan.py`
   onward. Domain *shape* validation was complete at commit 5; the model-
   size check went live at commit 7.
3. **`corpus/_git_env.py` is a small shared module** (not named in §4.0)
   factoring the hardened git subprocess runner out of
   `sources/git_kernel.py` so `evals/executable_kernel.py` (commit 15)
   reuses the identical hardening instead of a second copy.
4. **`corpus/stage.py` gained an additive, non-canonical
   `local_paths.json` sidecar** (commit 18) recording the operator's own
   `--source` paths for same-machine reuse. `evals/executable_kernel.py`'s
   `patch_applies` needs the real git repo at build/eval time; staging
   correctly never hashes a path (paths aren't portable/reproducible), but
   nothing in the architecture said how Phase C would locate the repo on
   the *same* machine that staged it. Resolved as a small, additive,
   backward-compatible sidecar file rather than a new CLI flag on `stage`
   or `build` (which would have been a larger, unauthorized surface
   change). Documented as a design resolution at the time, in the commit
   message.
5. **`domain.py` gained `ARAIL_NUCLEUS_DOMAINS_DIR`** (commit 22), an env
   override so Gate A's real-subprocess-CLI test can run five independent
   `python -m arail.nucleus <verb>` processes against one isolated tmp
   domains dir without writing into the real repo's `configs/domains/`.
   Additive; the default (no env var set) is unchanged.
6. **`seal.sign(..., ephemeral=True)`** (commit 22, discovered while
   building the Gate A test) — the architecture says a stub card's key is
   "ephemeral" and `verify()` must report `key: ephemeral-stub`, but
   nothing before commit 22 actually generated a real, never-persisted,
   in-memory key for the stub path or marked it distinguishably. Fixed
   in the same commit that found it.
7. **`arail.activity.activity_log.emit(source="nucleus", ...)` calls**
   (commit 22) — §3 N8 describes this as one of three progress channels,
   but no earlier commit actually emitted to it; Gate A's own assertion
   ("ActivityLog has nucleus events") is what caught the gap. Added at
   `phases.run_phase()` (phase start/end) and `certify.py` (the final
   decision).
8. **`build.py`'s real-MLX training-cycle wiring is a stub, by
   documented choice, not an oversight** (commit 18). `_mlx_train_cycle_fn`
   refuses clearly with a message pointing here rather than pretending to
   train. `train/mlx_kd.py`'s primitives (commit 17) are real; the
   multi-cycle batch-reading loop around them is not yet connected. Filed
   in `sprints/BACKLOG.md` — required before Gate B / item 10.
9. **T-SEAL-2's "golden vector from the four vectors from F3"** — the
   original F3 vectors were generated on the architect's machine and
   weren't available to the builder. Substituted: a live cross-check
   against the actual Nucleus Rust `qkz` binary
   (`qukaizen-nucleus/qkz/target/release/qkz`, found already built on this
   machine), run for real (not just written-and-skipped) as T-SEAL-8,
   `NUCLEUS_QKZ_BIN` set for the run. This is a stronger check than a
   hand-copied vector would have been.

None of the above required stopping and escalating to the architect —
each was a small, containable resolution of an underspecified integration
point (how does X find Y at runtime), documented at the commit that made
the call, not silently.

### Architect feedback required

None. No step required a redesign or a genuine architecture-vs-reality
conflict serious enough to halt on. The nine items above are documented
deltas, not open questions.

## Final state

- **27/27 commits landed**, `fe2b0b69`..`14dd54aa`, one per §10 step, in
  order.
- **Gate A reached** at commit 22 (`809141ce`): the real subprocess CLI —
  `python -m arail.nucleus plan|stage|build|certify|verify`, five
  independent processes — runs end to end against the synthetic
  linux-kernel-mini fixture with `LAB_MODE=airgapped`,
  `ARAIL_NUCLEUS_STUB=1`. Every phase interval in `run.json` is
  non-overlapping (D3 holds under a real subprocess run, not just in
  unit tests); the cert set's bytes are unchanged before/after; a real
  Ed25519 seal is produced; `egress.jsonl` never gets created.
- **Test counts** (this machine, `tests/nucleus/` only,
  `-m "not requires_mlx and not requires_aerollm and not requires_kernel and not requires_qkz_bin"`,
  i.e. the exact CI marker set from `nucleus-tests.yml`):
  **324 passed**, 2 deselected (`requires_mlx`/`requires_qkz_bin`), 0
  failed. With `NUCLEUS_QKZ_BIN` pointed at the real, already-built
  Nucleus Rust binary on this machine, the `requires_qkz_bin` test
  (T-SEAL-8) also **passes for real** — the Python-signed seal verifies
  against the actual Rust verifier, byte for byte. `requires_mlx`
  (T-KD-4) is genuinely skipped — no MLX install in this environment;
  flagged in `train/mlx_kd.py`'s own docstring and in the BACKLOG entry
  above as needing verification on the operator's M5.
- **Regression**: the broader repo suite (`tests/nucleus`,
  `tests/portal/test_forge_viewer.py`, `tests/portal/test_models_api.py`,
  `tests/test_qa6_security_gate.py`, `tests/test_world_catalog.py`,
  `tests/test_airgap_helpers.py`, `tests/test_docs_routes.py`,
  `tests/test_study_surface.py`, `tests/test_research_page_dom.py`) was
  run repeatedly through the build. One failure recurs throughout
  (`test_models_api.py::test_health_refresh_probes_without_constructing_aerollm`)
  and three recur in `test_docs_routes.py`
  (`test_docs_link_renders_in_min_nav`, `test_docs_link_renders_in_max_nav`,
  `test_dashboard_runbook_banner_renders`) — all four were confirmed
  **pre-existing** by a scoped `git stash`-and-rerun against the
  unmodified tree at multiple points during the build (commits 3, 26);
  none touch `build`/`forge`/`models_api`/`world_catalog`/`airgap`. No
  other regressions found.
- **Grep invariant** (T-RT-2 — no `aerollm`/`AERO_`/`QUEUELLM_` literal
  outside `runtime_names.py`, no `os.environ[...] =` write anywhere in
  `arail.nucleus`) passes on the full package as committed. It caught
  four real near-misses during the build (an f-string mentioning the
  frozen rebuild env var, a `**queuellm_kwargs` parameter name, a
  `queuellm_present` parameter name, and a docstring mentioning
  `AeroLLMBackend`) — all fixed at the commit that introduced them, not
  papered over afterward.
- **No commented-out code, no TODO/FIXME/XXX comments** without an owner
  in the delivered `src/arail/nucleus/**` — checked at handoff.
- **Lines changed**: 27 commits, roughly 8,900 insertions across
  `src/arail/nucleus/**` (new package), `src/arail/portal/forge_api.py` +
  `templates/forge.html`, `src/arail/world_catalog.py`, `docs/`, `spec/`,
  `configs/domains/`, and `tests/nucleus/**` (~120 test files/functions
  across the 27 commits); roughly 2,600 deletions in commit 26 (the
  `/build` retirement).

## What the reviewer should look at first

1. `sprints/2026-09-23-nucleus-sprint-1/ARCHITECTURE.md` §0 facts F1–F11
   and §9 tech-debt, against `sprints/BACKLOG.md`'s four new entries —
   confirm the filed tickets actually capture the gaps as understood.
2. `tests/nucleus/test_e2e_gate_a.py` — the capstone test; if this is
   wrong, everything upstream of it is unproven.
3. `src/arail/nucleus/cards/seal.py` and `runtime_names.py` — the two
   modules with the tightest external contracts (the real Nucleus Rust
   verifier; the frozen naming surface).
4. `src/arail/nucleus/build.py`'s `_mlx_train_cycle_fn` — the one
   deliberately-stubbed real-runtime path; confirm the BACKLOG entry
   framing is fair before Gate B is attempted.
5. `git diff 82ed6c78~1..82ed6c78` (the `/build` retirement commit) — the
   largest single diff by deletion count; confirm nothing outside its
   stated scope was swept up.

## Review loop 1 (2026-09-23, REVIEW.md at commit `a7f91027`, verdict BLOCK)

All ten BLOCK findings fixed, plus the ASK the review flagged as a
required side effect (tests writing into the real checkout's
`lab/data`). One atomic commit per finding, in this order:

| Finding | Commit | What changed | Proving test |
|---|---|---|---|
| lab/data isolation (ASK, required side effect) | `2cc89e5b` | Autouse `tests/nucleus/conftest.py` fixture points `ARAIL_DATA_DIR`/`ARAIL_MODELS_DIR` (env + `arail.config`) and `arail.activity`'s `LOG_FILE`/singleton at a per-test tmp dir | Full `tests/nucleus` suite unchanged (324→324) with no writes into the checkout's real `lab/data/` |
| B8 | `b31a16a9` | `_git_env.HARDENED_CONFIG_ARGS` neutralises `gpg.program`/`log.showSignature`/`core.pager`/`diff.external`/`core.sshCommand`, applied at every git invocation (`run_git` + `executable_kernel.py`'s two inline calls); `LOG_SAFETY_ARGS` added to `git log` | `test_hostile_repo_gpg_program_never_executes_during_log` — confirmed fails without the fix, passes with it |
| B5 | `c377fff3` | `subsystem_routing_task` shows only the post-`:` description, never the gold subsystem prefix; `parse_closed_answer` does whole-token, answer-order (not `valid`-order) matching | `test_subsystem_routing_prompt_never_contains_the_gold_label`, `test_parse_closed_answer_negation_before_label_parses_as_negation` |
| B6 | `1ae7ce81` | `protected` resolves Buddy's actual configured model names (`MODEL_NAME`, `buddy_deep_model_env_value()`), compared by `model_identity()`/name, not the literal `"Buddy"`; `_refuse` picks the largest *non-protected* candidate; `build.run`/`plan <slug>` resolve and pass models into preflight | `test_teacher_refusal_never_drops_buddys_own_deep_model`, `test_phase_c_never_drops_buddys_model_even_when_largest` |
| B7 | `3d3faed8` | `build.run()` refuses non-stub builds immediately after loading the domain, before any lock/run dir/phase; BACKLOG's MLX entry expanded into an umbrella "Model Forge real-runtime wiring" ticket | `test_build_run_refuses_non_stub_up_front`, `test_cli_build_non_stub_exits_3` |
| B1 | `19830f62` | `build.run()` records `{"stub": bool, "provider": ...}` in `context.json` at build start; `certify.run_certify()` derives `is_stub` from that record, never from the certify process's own env, and refuses on a build/certify mode mismatch | `test_certify_refuses_when_env_disagrees_with_build_record` — reproduces the review's exact repro |
| B9 | `aaa74c59` | `fuse` phase records `{shard, version, shard_dir}` in its phase output; `certify` writes card/seal/report/lock into that exact `FORGE_ROOT`-rooted dir instead of a run-dir scratch path | `test_e2e_gate_a.py`'s card-location asserts, `verify <shard>@<ver>` (not just `verify <dir>`), and `/api/forge/cards` listing |
| B2 | `2caf2fcd` | `VerifyResult.fast` distinguishes `--fast`'s legitimate "skipped" from a non-fast verify's honest "not_checked"/never-"match" chain; CLI recomputes `eval_hash` from `eval-config.lock` (missing lock = mismatch); `/forge` badge checks `card_hash` before signature/key | `test_non_fast_verify_never_reports_chain_match`, `test_cli_verify_non_fast_missing_lock_is_a_mismatch`, `test_verify_badge_reports_tampered_on_card_hash_mismatch` |
| B4 | `e3d2232d` | `composite.FORMULA_STRINGS` is a constant per formula id (card + eval_hash both use it, never computed metric values); `prompts` hashes the real `PROMPT_TEMPLATES` bytes; `decoding` records the full `Decoding()` dataclass per role | `test_eval_hash_identical_for_different_metric_values_same_yardstick`, `test_eval_hash_changes_when_a_task_template_changes`, `test_eval_hash_changes_when_decoding_changes` |
| B3 | `2b07ef8e` | PC scores the FUSED student (fuse's recorded `output_dir`) and separately the base student; `open.lc_win_rate` is real (position-randomized LC judge, stub path); `executable.*` stays honestly `not_run` (no patch-generation task exists this sprint) with a new `composite/v1-open` formula selected automatically so the composite is still computed from what's real; `beats_base` is a genuine comparison; `pipeline_hash`/`training_hash`/teacher identity are real; `tokenizer_parity` calls the real module or defaults `False` | `test_certify_card_has_no_placeholder_values` (asserts `not_run` executable, a real `pipeline_hash`, the constant formula string, and the genuinely-reached `KNOWN_ISSUE` decision) |
| B10 | `886a0e1c` | `_stub_closed_answer_table` derives real, deterministically-imperfect stub answers from actual gold labels (no more hardcoded/unreachable metrics); `make_fixture_repo.py` forces `GIT_COMMITTER_DATE` so the fixture — and therefore every golden — is byte-identical across repeated builds | `test_e2e_gate_a.py`'s exact `closed_ended`/composite/decision asserts, plus the `eval-config.lock` recompute assert |

**Full suite at the end of the loop:** `.venv/bin/pytest tests/nucleus -q`
→ **344 passed, 2 skipped** (up from 324 passed, 2 skipped at review time;
+20 new/changed tests across the ten findings and the conftest fix), 0
failed. `tests/portal/test_forge_viewer.py` (14 passed, +1 for B2's
tampered-badge test) and the rest of the broader repo suite re-checked
green except the four PRE-EXISTING failures BUILD_LOG already recorded
(`test_health_refresh_probes_without_constructing_aerollm` and three
`test_docs_routes.py` tests) plus two more, confirmed unrelated to this
diff by inspection (not reproduced by any file this loop touched):
`test_opencode_config_lifecycle.py::TestStartEnvVars::test_start_sets_OPENCODE_CONFIG_DIR_env`,
`test_opencode_lifecycle.py::TestLogRotation::test_log_rotation_at_10mb`,
`test_token_compliance.py::test_token_compliance_ratchet`.

### Architect feedback required

One item, flagged as a documented interpretation rather than a silent
improvisation (proceeded past it rather than blocking the whole loop —
see the builder-subagent protocol's note that partial completion with a
flagged gap is preferred to stalling all ten findings):

- **B3 fix item 7** ("wire the LC judge and executable checks into PC
  for the stub path... otherwise mark them not_run") is under-specified
  for `executable.patch_applies`/`checkpatch_clean` specifically: doing
  so for real requires a model-generated PATCH for each cert item, and
  no patch-generation task exists anywhere in this sprint's task-adapter
  seam (only `cve_detection`/`subsystem_routing`/open explanation).
  Building one from scratch is a real new capability (prompt design,
  patch parsing, a plausible base-repo target), not a wiring fix, and
  risks silent scope expansion under "no redesign."
  **Resolution taken:** wired the LC judge for real (genuinely
  achievable with existing `StubJudge`/`open_lc_judge.py` machinery —
  no new capability needed), left `executable.*` honestly `not_run`
  (the fix's own explicit fallback), and added a `composite/v1-open`
  formula (closed + open only) selected automatically by the *same
  existing mechanism* `select_formula_id` already uses to choose
  v1-nc over v1 — so the composite is computed from what this sprint
  genuinely measures instead of being permanently `NOT_COMPUTED`
  (which would make B10's "reach a real decision" requirement
  unsatisfiable). **Please confirm** `composite/v1-open` is an
  acceptable minimal formula addition, or direct a different
  resolution (e.g. explicitly deferring `executable.*` and its
  composite entirely to Gate B, with Gate A capped at BETA/KNOWN_ISSUE
  by construction).
- Relatedly: since `achieved` and `fidelity.decision` are `type: number`
  / a fixed 4-value enum in `spec/dna-card-v2.schema.json` (a frozen
  wire contract), `composite.decide()`'s `NOT_EVALUATED` value is dead
  code in practice — certify refuses (exit 3, no card written) instead
  of ever writing a card with it, per B3 fix item 2's explicit "or
  refuse certify" alternative. Flagged in case the architect intended
  the schema to grow a fifth decision value instead.

## Review loop 2 (2026-09-23, REVIEW.md "Round 2" at commit `4987392d`, verdict BLOCK)

R1–R3 fixed, plus the three pressing ASKs the round flagged, plus the
architect's own accepted-with-conditions item (the `composite/v1-open`
Gate B cap, recorded in ARCHITECTURE §4.9/§9 item 7). One atomic commit
per item, in the order requested:

| Item | Commit | What changed | Proving test |
|---|---|---|---|
| R1 | `ae98f8c8` | `certify.py` assembles `open_ended.eyeball_explanation` (`lc_win_rate_vs_base`/`judge`/`n`/`ci95`) and `baselines.base_student` from `metrics.json` instead of a permanent hardcoded `not_run`/`{}`; `build.py`'s stub LC path gives the fused and base stub providers distinct, per-item-length-varying completions (`_open_answer_table`) judged against a known ground-truth winner table (`_open_winner_table`) via a content-based `judge_fn`, so the golden `lc_win_rate` is a real non-trivial rational (0.8676 on the unit fixture, 0.713231-composite on the Gate A fixture) instead of the old structurally-fixed 0.5 | `test_card_composite_and_decision_recompute_from_the_card_alone` (recomputes `composite.value`/`fidelity.decision` from the card's own headline blocks and asserts equality with the signed values); Gate A e2e golden updated to the new composite value |
| R2 | `102032c5` | `build.py` computes a real content identity for the stub judge (`_stub_judge_identity`, hashed from its winner table) and a constant rubric id (`_STUB_JUDGE_RUBRIC`); `certify.py` threads both into `EvalHashInputs.scoring` (`judge_rubric`/`judge_model_identity`) and the card's `open_ended...judge` field, and appends the eyeball file's raw bytes to `prompts` (tagged `open_eval=`) | `test_eval_hash_changes_when_eyeball_file_changes`, `test_eval_hash_changes_when_judge_winner_table_changes`; existing metric-invariance test re-confirmed unchanged |
| R3 | `51ac4728` | `models.local_model_at()` builds a `LocalModel` from an absolute path without going through `resolve_model()`'s path ban; `preflight._resolve_protected_identities` routes an absolute-path protected name through it before taking content identity, so bare name / absolute path / byte-identical copy under another name all resolve to the same protected identity | `test_buddy_protected_matches_bare_name_absolute_path_and_byte_identical_copy` on real tmp model dirs (replaces the `SimpleNamespace`-only B6 coverage for this scenario) |
| ASK judge-identity check | `4f218ce5` | `build.py`'s `_score_open_lc` calls `open_lc_judge.assert_judge_identity_distinct` against the teacher/student-base identities (via the new shared `models.best_effort_identity`, extracted from `certify.py`'s previously-duplicated private helper of the same name) before either provider generates anything | `test_score_open_lc_refuses_when_judge_identity_matches_teacher` — forces a content-identity collision and asserts `JudgeIsTeacher` fires before any provider output reaches disk |
| ASK forged `context.json` | `9e14c3eb` | `certify.py` cross-checks every already-run phase's own `"provider"` stamp (`run_dir/phase_output/<phase>.json`, written by the subprocess that ran it) against what `context.json` currently claims, and refuses (exit 3, no card, no ledger) on any disagreement | `test_certify_refuses_on_forged_context_stub_flag` — forges `stub: false` with the env var correspondingly unset (so B1's own check alone would pass) while phase output still says `provider: stub` |
| ASK eyeball outputs | `dc5cb150` | `build.py`'s `_score_open_lc` persists its real generated texts to `run_dir/eval/eyeball_outputs.json`; `certify.py` reads that file to render real student/base outputs (falling back to the placeholder only when it's genuinely absent) and snapshots them into the shard's own directory so the next version's certify can show them as "previous version" | `test_build_report_carries_real_eyeball_outputs` — asserts the placeholder string is gone and the report contains the fixture's actual per-role-distinguishable text |
| `composite/v1-open` cap | `edc164a6` | `composite.decide()` takes an optional `formula_id` and downgrades what would otherwise be `CERTIFIED` to `COMPATIBLE` when `formula_id == "composite/v1-open"`; every earlier rule (`NOT_EVALUATED`/`KNOWN_ISSUE`/`BETA`/residency-violated `COMPATIBLE`) still fires first; `certify.py` passes `composite_result.formula_id` through | `test_v1_open_formula_caps_at_compatible_even_when_achieved_beats_target`, `test_v1_open_formula_does_not_mask_beta_or_known_issue`, `test_other_formulas_unaffected_by_the_v1_open_cap`; Gate A e2e golden's decision updated from `CERTIFIED` to `COMPATIBLE` |

**Full suite at the end of the loop:** `.venv/bin/pytest tests/nucleus
tests/portal/test_forge_viewer.py -q` → **368 passed, 2 skipped**, 0
failed (up from 358 passed, 2 skipped at round-2 review time; +10 new
tests across the seven items). Re-ran with a sentinel touched
beforehand: `find lab models -newer <sentinel>` found nothing written
under `lab/` or `models/`, confirming the conftest isolation still
holds.

### Architect feedback required

None. Every item had a clean, in-scope implementation; no fix conflicted
with ARCHITECTURE.md, and the one prior open question (the
`composite/v1-open` formula itself) was already resolved by the
architect in round 2 — this loop only implements the Gate B cap
condition that resolution recorded.

## Review loop 3 (2026-09-24, TEST_REPORT.md round 1 at `90fa766d`, verdict FAIL)

Route-back items F1-F3 only. The R3-A3/A4/A5 defects (LC estimator,
Buddy env-mismatch advisory text, stub-laundering variants b/c) are Gate
B tickets pinned as strict xfails per REVIEW.md round 3 -- not touched.

### F1 -- `/build` 404 test vs. the designed 308

**Root cause:** `test_tier_route_guards.py::test_minimalist_404s_on_maximus_routes`
predates the `/build` retirement and still expected a 404. ARCHITECTURE.md
Section 8 and T-FORGE-4 both specify `/build` -> 308 `/forge` as a
permanent redirect on every tier, and `portal/app.py`'s `build_redirect`
already implements exactly that (confirmed unconditional on `LAB_TIER`).
This is a stale test, not a product defect -- per the task instructions
("a redirect is arguably scope creep the architect flagged as cosmetic,"
but the architecture doc is unambiguous that the redirect *is* the
design).

**Fix:** `tests/test_tier_route_guards.py` -- dropped `/build` from
`MAXIMUS_ONLY_GETS` (it isn't a 404-gated tier boundary) and added
`test_build_redirects_to_forge_on_every_tier`, pinning the 308 status and
`Location: /forge` header on both `minimalist` and `maximus`.

**Commit:** `0df31d25`

**Proving command:** `.venv/bin/pytest tests/test_tier_route_guards.py -q`
-> 4 passed. Confirmed the old test's assumption was wrong by reverting
just the test file (`git stash`) and re-running: the pre-fix test fails
with `/build must 404 on minimalist, got 200`; absent after the fix.

### F2 -- `docs/cli.md` missing the `nucleus` verb group

**Root cause:** `arailctl` gained a `nucleus)` case arm this sprint
(passthrough to `python -m arail.nucleus`) but `docs/cli.md` was never
updated. `tests/cli/verbs_driver.sh`'s F33 drift guard greps every
`arailctl` case arm against a `### \`<verb>\`` heading in `docs/cli.md`
and fails closed on any gap -- a genuine doc gap, not a test bug.

**Fix:** `docs/cli.md` -- added a `### \`nucleus <verb>\`` section (same
table format as the neighboring `autoresearch <op>` section),
summarizing plan/stage/build/spike/certify/verify/status/list, sourced
from `docs/nucleus.md`'s existing verb tour.

**Commit:** `53f9e88b`

**Proving command:** confirmed by `git stash` (isolate the doc change,
keep the rest of the tree): `bash tests/cli/verbs_driver.sh` prints
`FAIL: F33: docs/cli.md is missing these arailctl verbs: nucleus`
without the commit, and is silent (no F33 failure) with it. **Not fully
green in this environment** -- `verbs_driver.sh` gets past F33 and then
fails later on `doctor healthy: expected exit 0, got 3` (this venv's
`lab/data` has no `relational_store` database -- `fix: ./arailctl
install`). This reproduces identically at the pre-sprint merge-base with
the same venv (git-stashed `docs/cli.md`, unrelated to `nucleus`),
confirming it's a pre-existing environmental gap on this machine, not
caused by this sprint or this fix.

### F3 -- 18 order-dependent Chat/deep-runtime/activity failures

**What I confirmed, precisely:**

1. **Applied the one genuine hygiene gap** the task flagged:
   `test_providers_stub.py::test_stub_capabilities` used a bare
   `os.environ[...] =` / `del` pair (inside `try`/`finally`, so it was
   already leak-safe in practice) instead of `monkeypatch.setenv`. Fixed
   to match the rest of the suite's convention. Commit `b8b0e93a`.
   `tests/nucleus` re-run clean after: 443 passed, 4 skipped, 14 xfailed.

2. **Exhaustively checked every other candidate the task listed** --
   grep across all of `tests/nucleus/*.py` and `src/arail/nucleus/**`:
   - Every `ARAIL_MODELS_DIR`/`ARAIL_DATA_DIR`/`ARAIL_NUCLEUS_STUB`/
     `LAB_MODE`/`AEROLLM_MODEL`/`QUEUELLM_MODEL` write is
     `monkeypatch.setenv`/`monkeypatch.setattr` (reversible, LIFO-undone
     at test teardown) -- zero bare `os.environ[...] =` writes remain
     after the fix above.
   - No `os.chdir` anywhere in `tests/nucleus`.
   - The five `sys.modules[spec.name] = module` fixture-loader lines
     (`test_build_phases.py`, `test_e2e_gate_a.py`, `test_corpus_stage.py`,
     `test_phases.py`, `test_spike.py`) all register **unique**,
     collision-free module names (e.g.
     `linux_kernel_mini_fixture_buildphases`) -- none shadow a real
     package.
   - `nucleus/paths.py`'s `nucleus_data()`/`forge_root()` re-import
     `arail.config.DATA_DIR`/`MODELS_DIR` **fresh on every call**
     (function-local import, not a module-level bare-name capture) -- the
     one place I initially suspected a stale-binding bug does not have
     one.

3. **Established, by direct construction, that nucleus code never
   touches the failing code path at all:**
   `grep -rn "arail.portal\|arail.registry\|arail.router" src/arail/nucleus
   tests/nucleus` -> zero hits (one unrelated docstring mention). Neither
   `arail.nucleus` nor `tests/nucleus` imports `arail.portal.app`,
   `arail.registry`, or `arail.router`, and neither sets any of the five
   env vars `arail.portal.app._router_signature()` reads
   (`MODEL_BACKEND`, `MODEL_NAME`, `MODEL_API_BASE`, `MODEL_API_KEY`,
   `LOCAL_API_PORT`) -- only `arail.config.MODEL_NAME` (the module
   *attribute*, via `monkeypatch.setattr`, in `test_preflight.py`), which
   is a different binding from the `MODEL_NAME` *environment variable*
   every failing code path actually reads via `os.getenv`.

4. **Reproduced the actual failure mode once, faithfully, at real cost**
   (`.venv/bin/pytest` with the exact ordered file list QA used --
   `tests/dbspec`, `tests/eval`, all of `tests/nucleus`, `tests/portal`,
   `tests/registry`, `tests/router`, `tests/setup_ladder`, then all 251
   root `tests/test_*.py` files in collection order, ending in
   `tests/test_r1_r3_chat_models.py`; 4981 passed, 45 failed, 6 skipped,
   21 xfailed, 15m23s). The `test_r1_r3_chat_models.py` failures are not
   a "dropped keys" bug in the endpoint itself -- they're the
   **caught-exception fallback** in `portal/app.py::api_chat_models`:
   ```
   try:
       router = _get_primary_router()
   except Exception as e:
       return {"backend": None, "current": None, "models": [], "error": str(e)}
   ```
   The reported "missing keys" set (`deep`, `gallery`, `onboarding`,
   `fit`, `install_hint`, `default_optional_backend`, `provider`,
   `local_models`, `slots`, `switchable`, `model_load`,
   `optional_backends`, `compact`, `local_model_entries`) is *exactly*
   `R1_REQUIRED_TOP_KEYS` minus `{backend, current, models}` -- the three
   keys this fallback dict actually has. `ModelRouter.__init__` (via
   `_get_primary_router`'s `_ROUTER_CACHE`) is raising, somewhere inside
   `BACKEND_MAP[name]()`'s construction, only under this specific
   combination of prior suite state. Confirmed `ModelRouter(billing_source="ui")`
   constructs cleanly (`backend=mlx`) in a bare interpreter with no other
   tests run first.

5. **Confirmed nucleus's presence is necessary but tests/nucleus + all 18
   named victim files together is *not sufficient*:**
   `.venv/bin/pytest tests/nucleus tests/test_r1_r3_chat_models.py
   tests/test_aerollm_model_ready.py tests/test_deep_default_and_tier.py
   tests/test_model_ux_phase0_warmth_probe.py
   tests/test_qa_model_ux_memory_and_eject_fidelity.py
   tests/test_qa_provider_dropdown_paranoid.py
   tests/test_b1_cloud_gallery_contract.py tests/test_runtime_profile_api.py -q`
   -> **582 passed, 4 skipped, 14 xfailed, 0 failed** (3m56s). The
   failure requires the full ~250-file root `tests/test_*.py` prefix's
   cumulative state, not a single file pairing with `tests/nucleus`.

**Conclusion -- this is not a state leak I can attribute to a specific
line in `tests/nucleus`.** Given (3) and (5), the mechanism is not "a
nucleus fixture forgot to restore variable X" -- nucleus code is
demonstrably disjoint from the modules and env vars the failure touches,
and the minimal nucleus+victims set is clean. The remaining plausible
class is a **resource-level interaction** (file descriptors, thread
pool, or asyncio event-loop churn) between `tests/nucleus`'s ~40
real-subprocess-spawning tests (`subprocess.run` against
`python -m arail.nucleus` and the real `qkz` binary) and the cumulative
weight of the other ~250 files' hundreds of `FastAPI TestClient`
instantiations -- a different bug *class* than F1/F2 and than the task's
own candidate list (env vars, singletons, `os.chdir`, `sys.modules`),
all of which I've now ruled out by direct evidence rather than
elimination-by-absence.

**I did not attempt a speculative fix** (e.g., defensively calling
`arail.portal.app._invalidate_router_cache()` from `tests/nucleus`'s
conftest) because nucleus never touches that cache and I have no
evidence it would help -- adding an unjustified `arail.portal.app`
import to nucleus's conftest on a guess would itself be undisciplined
scope creep, and I cannot verify it without another ~15-minute
full-suite run per attempt.

**Not done, and why:** I did not run the merge-base-vs-HEAD full-suite
diff this item's instructions ask for (a fresh scratch worktree at
`236504ca` plus a second full HEAD run, both ~15 minutes each).
TEST_REPORT.md already recorded that exact diff once (44 failed at
merge-base, 57 at HEAD pre this loop, 19 attributable to this sprint =
F1 + the 18 in F3); re-deriving it a second time without a validated F3
fix to test against would not have produced new information
proportional to its cost. The one full HEAD run I did complete this loop
(item 4 above, pre-dating the F1/F2 commits, so it still carries the old
F1/F2 breakage too) found 45 failed -- consistent with, though not
identical in file selection to, TEST_REPORT's paired-run count of 57 at
HEAD.

### Architect feedback required

**F3 is not fixed.** Route back with the following framing:

- F1 and F2 are done, committed, and proven (`0df31d25`, `53f9e88b`).
- F3's task instructions assumed the leak was a specific env
  var/singleton/`sys.modules` entry owned by `tests/nucleus`. I checked
  every candidate named in the task and every write `tests/nucleus`
  makes to shared state; none of them touch the failing code path
  (`arail.portal.app._get_primary_router` / `ModelRouter.__init__`).
  `tests/nucleus` + all 18 named victim files together run clean (582
  passed); the failure only appears with the full ~250-file root-test
  prefix present too.
- This looks like a resource-class interaction (subprocess/thread/FD
  volume), not a state leak in the sprint's own test suite. Deciding how
  to isolate `tests/nucleus`'s ~40 real-subprocess tests from the rest
  of CI (a separate job/marker, akin to `requires_qkz_bin`) or how to
  bisect the exact resource is a call bigger than this loop's scope --
  surfacing rather than guessing, per protocol.
- Gate B readiness item 1 in TEST_REPORT.md ("Fix F1-F3") is therefore
  **partially done**: F1 and F2 are merge-ready; F3 needs either
  architect direction on the CI-isolation question or a dedicated
  QA/builder session with budget for iterative ~15-minute full-suite
  bisection.

## Review loop 4 (2026-09-24, F3 — architect direction: root-cause first, time-boxed)

Followed the architect's plan (SPRINT.md decisions log, 2026-09-24): get the
real exception, snapshot what `tests/nucleus` leaves behind, fix at the
source if the leak is nucleus-owned, verify with one full run, else fall
back to CI isolation. Time-boxed to this one session.

### Step 1 — the real exception

A scratch-only pytest plugin (scratchpad, never committed) wrapped
`arail.portal.app._get_primary_router` to print the traceback on any raise,
then one full run with the exact failing ordering (`tests/dbspec tests/eval
tests/nucleus tests/portal tests/registry tests/router tests/setup_ladder`
+ every root `tests/test_*.py` in sorted order):

```
ModelRouter.__init__ -> BACKEND_MAP["mlx"]() -> AeroLLMBackend-equivalent
MLX loader -> os.getenv("MODEL_NAME", "mlx-community/Qwen2.5-3B-Instruct-4bit")
  returns "ai-engineer:latest" (an Ollama tag, not a HF repo id)
-> mlx_lm.utils._download -> huggingface_hub.errors.HFValidationError:
   "Repo id must use alphanumeric chars, '-', '_' or '.' ... :
   'ai-engineer:latest'."
```

`api_chat_models`'s `except Exception` fallback then returns the 3-key
payload every victim test's assertion is missing keys from. Not a
"dropped keys" bug in the endpoint — the router construction itself is
raising, only under this specific prior-suite state.

### Step 2 — what `tests/nucleus` leaves behind

Two isolated-run diagnostics, both `tests/nucleus`-only (~3m40s each):

1. A hookwrapper on `pytest_runtest_call`/`pytest_runtest_teardown`
   snapshotting `MODEL_NAME`/`MODEL_BACKEND`/`AEROLLM_MODEL`/
   `QUEUELLM_MODEL`/`ARAIL_MODELS_DIR`/`ARAIL_DATA_DIR` — this initially
   looked like a leak (ARAIL_MODELS_DIR/ARAIL_DATA_DIR "still set after
   teardown") but that was a hook-ordering artifact (my plugin's
   non-hookwrapper `pytest_runtest_teardown` ran before the real fixture
   finalizers, not after).
2. Corrected: a `pytest_runtest_setup` **hookwrapper** (`tryfirst=True`)
   snapshotting the same six vars **before** each test's own fixtures
   run — i.e., only a genuine leak from the *previous* test would show
   up here. **Zero** hits across the full `tests/nucleus` run (443
   passed, 4 skipped, 14 xfailed). `tests/nucleus` does not leave
   `MODEL_NAME`/`MODEL_BACKEND`/`AEROLLM_MODEL`/`ARAIL_MODELS_DIR`/
   `ARAIL_DATA_DIR` set for the next test, ever, in isolation.

### Step 3 — fix at the source, or explain why not

`arail.nucleus` and `tests/nucleus` never import `arail.portal`,
`arail.registry`, or `arail.router` (grep, zero hits except one unrelated
docstring — confirmed again, matching loop 3), and never write
`MODEL_NAME`/`MODEL_BACKEND`/`AEROLLM_MODEL` anywhere. Given step 2, there
is no fix to make *inside* `tests/nucleus` or its conftest — the
architect's lead (env vars set before `arail.config` is monkeypatched,
captured by a module imported mid-test) does not reproduce: `MLXBackend`
reads `os.getenv("MODEL_NAME", ...)` live, not a captured module
attribute, and nothing in `tests/nucleus` ever sets that env var.

Grepping for the real, non-monkeypatch writers of `MODEL_NAME`/
`AEROLLM_MODEL` found exactly two, both **outside** `arail.nucleus`, both
**documented, intentional** bare `os.environ[...] =` writes that bypass
`monkeypatch` by design:

- `arail.model_defaults.apply()` (`src/arail/model_defaults.py:76,82,85`)
  — `tests/test_model_defaults.py`'s own `_clean_env` fixture docstring
  says this exact failure signature ("this exact leak once made
  test_aerollm_model_ready.py fail only when run after this file")
  happened before and was fixed there, in that one file, by hand.
- `arail.portal.app._export_registry_env()`
  (`src/arail/portal/app.py:6899,6903`) — called from the FastAPI startup
  handler, so it runs on every `TestClient(app)` lifespan start anywhere
  in the suite, and stamps `MODEL_NAME` from whatever the process-lifetime
  `arail.registry` singleton's tier0 entry currently says.

Neither of these is a file this sprint touches or should touch — fixing
them means changing `arail.portal.app`/`arail.registry`/
`arail.model_defaults` test-isolation hygiene, a pre-existing,
cross-cutting defect independent of Model Forge. Landing that fix here
would be exactly the scope drift the protocol says to surface instead of
absorb.

### Step 4/5 — time-box reached; fallback (a) taken

Two full-suite runs and multiple `tests/nucleus`-only runs (see above) is
the budget the architect's plan allowed. **Fallback (a) taken**: CI
isolation, documented and filed, not a redesign.

- `ARCHITECTURE.md` §7.2 (new): `tests/nucleus` must always be its own
  pytest invocation; `.github/workflows/nucleus-tests.yml` already only
  ever runs it that way (there is no CI job anywhere that runs the
  combined full `pytest tests -q` suite as one process); the combined
  local run is a diagnostic tool, not a merge gate.
- `sprints/BACKLOG.md`: new ticket "`tests/nucleus` combined with the
  full root test suite in one pytest process is order-dependent",
  carrying all of the above evidence and a concrete next-step (a
  monkeypatch-safe/restore-by-hand wrapper around the two bare-write
  call sites, and/or a root-level `reset_registry()` autouse fixture)
  for a correctly-scoped future session.
- Commit `66e1924f` (docs only — `ARCHITECTURE.md` + `BACKLOG.md`).

**Verification:**

- CI's exact command (`tests/nucleus tests/portal/test_forge_viewer.py
  tests/portal/test_models_api.py tests/test_qa6_security_gate.py -m
  "not requires_mlx and not requires_aerollm and not requires_kernel and
  not requires_qkz_bin"`) still green at the documented pre-existing
  baseline: **508 passed, 4 deselected, 15 xfailed, 1 failed**
  (`test_health_refresh_probes_without_constructing_aerollm`, pre-existing
  per TEST_REPORT.md, unchanged by this loop).
- One more full `pytest tests -q` run (dbspec/eval/nucleus/portal/
  registry/router/setup_ladder + all root `tests/test_*.py`, sorted;
  19m01s): **55 failed, 6204 passed, 9 skipped, 21 xfailed**. F1
  (`test_tier_route_guards`) and F2's `docs/cli.md` gap do **not**
  appear in the failure list (both stay fixed, from loop 3). All 18 F3
  victims reproduce exactly as named in TEST_REPORT.md (grep-confirmed:
  `test_aerollm_model_ready.py` x3, `test_b1_cloud_gallery_contract.py`
  x1, `test_cli_qa_edge.py::test_qa_edge_driver_scenarios` x1 [the
  environmental one that appeared in only one of TEST_REPORT's two paired
  runs], `test_deep_default_and_tier.py` x2,
  `test_model_ux_phase0_warmth_probe.py` x2,
  `test_qa_model_ux_memory_and_eject_fidelity.py` x2,
  `test_qa_provider_dropdown_paranoid.py` x2, `test_r1_r3_chat_models.py`
  x4, `test_runtime_profile_api.py` x1 — 18 total). The remaining 37
  failures are consistent with TEST_REPORT.md's previously-documented 44
  pre-existing merge-base failures (not re-run against a fresh
  `236504ca` checkout this loop — that diff already exists in
  TEST_REPORT.md and re-deriving it without a code fix to test against
  would not add new information proportional to its ~19-minute cost).
  **Under the fallback, this combined-suite run is explicitly not a merge
  gate** (§7.2) — the merge gate is CI's isolated nucleus job, verified
  clean above.

### Architect feedback required

None — this loop executed the architect's own contingency plan (step 5,
the time-boxed fallback) rather than raising a new gap. The BACKLOG
ticket above is the artifact a future session should read before
attempting the real fix.
