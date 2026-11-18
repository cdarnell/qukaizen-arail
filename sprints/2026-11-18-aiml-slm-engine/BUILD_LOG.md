# Build log: aiml-slm-engine (opt-in house-model slot + Buddy brain A/B harness)

**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `34c4c286`
**Started:** 2026-11-18
**Branch:** `qukaizen/arail-aiml-slm-engine-87c49e` (commits `31ef7a08` .. `8d7e38e1`, plus the BUILD_LOG commit). Nothing pushed.

Test interpreter (main checkout's venv, read-only): `PY=/Users/netsushi/ProJects/qukaizen-arail/.venv/bin/python`,
run from the worktree as `PYTHONPATH=src $PY -m pytest ... -q -p no:cacheprovider`.

## Plan and result

| # | Change | Test | Commit |
|---|---|---|---|
| 0 | BUILD_LOG skeleton | - | `31ef7a08` |
| 1 | Characterise resolver for the opt-in family; strict xfails for F1/F2 | T-R6 (+2 xfail) | `a9c758d3` |
| 2 | `chat/model_pins.json` (pending, empty identity), `model_pins.py` loader/verify/CLI; conftest stubs | T-P1..T-P8 (22) | `cf7675f3` |
| 3 | `model_resolve`: family is pin-only, exact tag, digest-verified; xfails flipped | T-R1..T-R6 (+9) | `012afe0c` |
| 4 | `models_api` / `/api/chat/default` refuse unverified family pins; `model_pin` trace event | T-A1..T-A3 | `37993f8d` |
| 5 | Router/backend `think`, `sampling`, `trace_attrs` pass-through (+ `done_reason`, `prompt_tokens`) | 5 body/router tests | `e895d10a` |
| 6 | `brain.stream_turn`, `probe_thinking`, family re-verify in `describe()`; edge `_drain` uses it | T-B1..T-B5 (10) | `852e72ae` |
| 7 | Dormant catalog row, `CatalogEntry.pin`, gallery/candidates `pin_state`, spec override, NOTICE | T-C1, T-C2 (7) | `298d5898` |
| 8 | Vendored Buddy prompt (stamped), compose, grade, stats, metrics registry | 28 units, T-H10 | `37c43d22` |
| 9 | Deterministic task generator | 9 generator tests, T-H6 | `78165065` |
| 10 | Freeze `tasks.v1` + manifest + `rules.v1` (PROPOSAL), data only | T-H1 (4) | `7fc94749` |
| 11 | Runner, isolation, SC1-SC8, residency restore, report, verdict | T-H1..T-H11 (15, fake Ollama) | `14025676` |
| 11b | Fix found by the live smoke: runner without `context_length` in `/api/ps` | live smoke | `94db50d4` |
| 12 | Live Ollama smoke (`requires_ollama`) | T-H8 | `6a7962b5` |
| 13 | `docs/buddy-brain-bench.md`, backlog TD1-TD6 | - | `77669f64` |
| 13b | `docs/PRIVACY.md` + `tests/test_egress_docs.py`: the harness child installs the egress guard (DOC-9 contract) | `test_egress_docs.py` (81) | `8d7e38e1` |
| 14 | Reference run committed as evidence | **not done by the builder**: the orchestrator runs the full proof (instruction) | - |

## Execution notes and deltas from the plan

- **Step 1.** Characterisation tests and two `xfail(strict)` tests documenting F1/F2, as planned.
- **Step 2.** `tests/conftest.py` (not in the plan) gained stubs so the new loopback reads in `model_pins` can
  never reach a developer's Ollama in the suite (same pattern as the resolver stubs). A malformed pin file
  marks the known family `invalid` (so the family stays guarded even when the file is unreadable). Pin states
  beyond the plan's five: `revoked` and `invalid`, both treated like `pending`. `ARAIL_MODEL_PINS_FILE` lets
  tests point at a pinned fixture; production reads only the in-repo file.
- **Step 3.** A family value never goes through the capability probe; verification spends one `/api/show` of
  the shared budget first. With Ollama down, a family pin, cache value or env value is skipped (plain reason).
- **Step 4.** `_validate_pin` stores the verified exact tag when the bare family id is pinned.
  `chat_default` refuses before anything is written to `secrets.env` or the environment.
- **Step 5 (small addition).** `ModelResponse` gained optional `done_reason` and `prompt_tokens` (None for every
  other backend); the harness needs `done_reason == "length"` for the truncation check and `prompt_eval_count`
  for SC5.
- **Step 6.** `ChatPlan` gained `entry` (default None). The edge always passes `sampling=None`; every
  `buddy.host/v1` conformance and edge test is green unchanged. The thinking probe is stubbed to False in the
  hermetic conftest fixture.
- **Step 7.** NOTICE section is **5**, not 4: section 4 is already the Gemma floor. The `qwen3:1.7b`
  override uses the GGUF's 2.03 B parameters (name regex alone says 1.7 B; both are far under the 8 B ceiling).
- **Step 8.** T-H11 (report header labels control-merge UNTRAINED) moved to step 11 with the report it tests.
- **Step 9 (data finding).** The `ai` World already holds a 22-term `qukaizen` category, so most `qukaizen`
  terms cannot be out-of-World questions against it (the design rule rejects a gold that exists in the offered
  World). Only 6 qualify, so O is 6 qukaizen-question items + 26 ai-question items offered qukaizen cards
  (total stays 32, rather than 16 + 16). Not a blocker; recorded for the architect.
- **Step 11.** Deviations: (a) the control merge cannot be bound through `brain.describe()` (its own family
  gate refuses an untrained tag by design), so the harness binds a non-pinned family tag through the same
  registry entry and `build_router()` and skips only that gate; a `pin:` candidate always goes through
  `describe()` and must `verify == match` (SC5). (b) When `/api/ps` does not report `context_length` (the
  stock qwen3 `ggml` runner), SC5 falls back to checking the evaluated prompt size and records a warning
  (commit 11b). (c) SC3 injects the -20 pp gap into the baseline D flags when there are at least 50 items and
  enough passes, else into a seeded synthetic baseline (a statistics-only check, stated in the report). (d) The
  rerun sample runs after an explicit unload of every arm. (e) `--limit-per-category N` (smoke) was added: a
  subset run never prints a verdict. (f) `--out` inside a guarded directory is excluded from the SC8 snapshot.
- **Step 13.** `docs/cli.md` is **untouched**, consistent with "no new arailctl verb" (§5.1); the harness is
  documented in `docs/buddy-brain-bench.md`.
- **Not done / deferred as instructed:** step 14 (the committed reference run) and the full ~40-minute run.
  `scripts/setup.sh`, `arailctl`, `arailctl status`, the default model, and every frozen `aerollm` name are
  untouched. The pin record is `pending` with empty identity fields; `qkz-aiml-qwen3-1.7b:control-merge` is
  never pinned (every test that pins uses a synthetic, test-only pin file).

## Full-harness command (for the orchestrator)

From the worktree, about 40 minutes (hard cap 45; refuses at estimate > cap), loopback Ollama only, $0:

```bash
cd /Users/netsushi/ProJects/qukaizen-arail/.claude/worktrees/aerollm-roadmap-review-bb6a86
PYTHONPATH=src /Users/netsushi/ProJects/qukaizen-arail/.venv/bin/python -m arail.brain_bench run \
  --baseline llama-ai-eng:latest \
  --candidate qkz-aiml-qwen3-1.7b:control-merge --candidate-label "UNTRAINED stand-in" \
  --reference qwen3:1.7b \
  --near-aa qwen3:1.7b,qkz-aiml-qwen3-1.7b:control-merge
```

Outputs land in `eval/buddy_brain/runs/<UTC>-<git sha8>/`: `results.json` and `report.md` (commit these as the
step-14 evidence) and `rows.jsonl` (git-ignored). Exit `0` ok, `3` a self-check failed (no verdict), `4` refused,
`5` aborted. `--dry-run` prints the plan without a model call (1,288 calls, 15 blocks).

## Architect feedback required

None blocking. Three items for the next design pass:

1. **O split (step 9).** The O-category rule "reject if the gold exists in the offered World" leaves only 6
   qukaizen terms usable as questions against the `ai` World (it carries a `qukaizen` category). Implemented as
   6 + 26. If a 16/16 split matters, the architect should name a different source.
2. **`describe()` gate vs the stand-in (step 11).** C5's family gate makes the untrained control merge
   unbindable through `describe()`, but §5.2/§5.5 run it as the plumbing stand-in. Resolved inside the harness
   (see step 11 (a)); worth stating in the spec.
3. **`/api/ps` context_length (step 11b).** The spec's SC5 assumes it is always reported; it is not for the
   `ggml` runner.

## Final state

### Test results (branch head, main checkout's venv)

| Run | Result |
|---|---|
| Targeted list (ARCHITECTURE "Commands" + the new suites) | **729 passed, 1 skipped, 1 failed**. The failure is the known pre-existing `tests/test_model_hosting_reframe_qa.py::test_backends_raises_on_sentinel_before_any_load` (fails identically at `845f9b25`). |
| Egress gate (CI `egress-guard.yml` list) + setup ladder + `test_qa_setup_fresh_lab` + `test_qa_tier1_clean_machine` + `test_setup_extras` + `test_imports` | **550 passed, 1 skipped, 0 failed** |
| Conformance (`buddy.host/v1`, cases 028-037) and edge suites | green, unchanged files |
| New tests in this sprint | `test_model_pins` 22, `test_model_resolve_pinned_family` 11, `test_models_pin_pinned_family` 3, `test_router_think_sampling` 5, `test_brain_stream_turn` 10, `test_catalog_pinned_row` 7, `test_brain_bench_grade` 28, `test_brain_bench_generator` 9, `test_brain_bench_tasks` 4, `test_brain_bench_isolation` 15, live smoke 1 |
| Live smoke (real Ollama, `llama-ai-eng` vs `qwen3:1.7b`, 2 items per category, 2.5 min) | passed: `think_sent=false` for qwen3, 0 empty replies, SC1/SC3/SC5/SC6/SC7/SC8 pass, SC4 fails on a 4-item sample (expected, no verdict on a subset); Ollama residency restored |
| Full suite `tests --ignore=tests/nucleus` (9,283 collected; `tests/nucleus` hangs on this machine for an unrelated reason, so it is excluded) | 8,696 passed, 34 skipped, 26 xfailed, **65 failed** in 49 min |

**The 65 full-suite failures are not caused by this sprint.** Re-run in isolation at `845f9b25` (a `git archive`
copy in the scratchpad, main checkout untouched) 44 of them fail identically (aerollm bundle/defaults, opencode,
qa6 bootstrap, deep probe/verdict, reset scope, swarm surfaces and similar: stale-venv and environment). Ten more
fail only at head in isolation, each diagnosed:

- `test_egress_docs::test_the_guard_is_installed_exactly_where_privacy_says`: **real, mine**, fixed in `8d7e38e1`.
- `test_queuellm_rename_frozen_surface` x3 (and the same class): diff the branch against the old base `5f775f1c`;
  already red at `845f9b25` because of earlier commits on this branch (the archive copy has no `.git`, so they skip there).
- `test_qa_retest_fix_loop::test_f9_the_real_lab_costs_json_is_not_recreated_by_the_suite`: a July 30 `lab/data/costs.json`
  exists in this worktree (untouched by this run; the archive has none).
- `test_cli_picker`, `test_cli_status` (status took 5.0 s against a 2 s bound), `test_cli_verbs` (doctor exit 3 with
  optional tools missing): driver scripts that depend on this machine's load and tools; `arailctl` is untouched.
- `test_qa6_failclosed_and_bootstrap::test_perf_mount_regression_under_10pct` (0.46 s to 0.54 s): timing noise.
- `test_queuellm_rename_qa::test_every_parameter_free_get_route_renders_no_user_visible_aerollm`: failed once, passed on rerun.

The other full-run failures that pass in isolation are order/state dependent in the full suite.

Lines changed (`845f9b25..HEAD`, before this log commit): see `git diff --stat 845f9b25 HEAD`.
