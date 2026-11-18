# Review: the house model as an opt-in Buddy brain, plus the Buddy brain A/B harness

**Date:** 2026-11-18
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) at `e2c47bca` (range `34c4c286..e2c47bca`, 17 commits)
**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `34c4c286`
**Reviewer runs** (main checkout's venv, read-only; no Ollama, no harness run, `eval/buddy_brain/runs/` untouched):

| Run | Result |
|---|---|
| Targeted list (ARCHITECTURE "Commands" + `test_router_think_sampling`, `test_host_edge`, `test_host_edge_plumbing`, `test_qa_buddy_edge_routes`), known pre-existing `test_backends_raises_on_sentinel_before_any_load` deselected | **729 passed, 1 skipped** |
| Egress gate (CI `EGRESS_TESTS` list) + `test_imports` | **437 passed, 1 skipped** |
| Spot-check of the builder's 10 "head-only" failures (see §Test coverage) | 1 caused by this branch, 1 made worse by it |

## Verdict: BLOCK

One BLOCK, and it is small. The branch edits `NOTICE`. A frozen-surface guard pins `NOTICE`'s bytes, so that test is now red, and BUILD_LOG reports it as pre-existing. Everything the brief asked me to check about the default model, the pin, `think`, isolation and egress holds up. The fix for the BLOCK touches no harness code, so the full harness run now in progress can stay valid as step-14 evidence. The report can be re-rendered from `results.json` with `python -m arail.brain_bench report`.

## Spec adherence

All contracts C1–C8 are implemented as designed. Checked by reading the code and its tests:

- **Default unchanged on every path.** `scripts/setup.sh`, `arailctl`, `scripts/status.sh`, `model_defaults.py` and `registry/` are not in the diff. `SHIPPED_DEFAULT` is unchanged. The `llama-ai-eng` catalog row is unchanged and still `recommended`. The new row is `optional` and sits after `qwen3:8b`. Boot warm goes through the same resolver, so it is unchanged too.
- **The opt-in family is never auto-picked.** `model_resolve.py:437` filters the family out of `ok` before `rank()`. If it is the only installed model, the result is `none` with the plain reason (`:438-444`). With `llama-ai-eng` missing, the stock `qwen3:1.7b` is chosen and never control-merge (T-R2).
- **No fallback to another tag.** A family pin or env value goes through `model_pins.verify` using the exact tag (`model_pins.py:337`, no bare-name step). On any state other than `match` it is skipped with a `missing` or `pin_mismatch` note, and `continue` falls through to auto. It never reaches `_match_installed`. With Ollama down, a family pin, cache or env value is never honoured (`:359-375`). `_validate_pin` and `/api/chat/default` refuse with the C2 reason before anything is written to `secrets.env` (`models_api.py:277-284`, `app.py:8510-8516`).
- **The pin record is `pending` with null identity fields** (`chat/model_pins.json`). T-P1 locks this. Nothing pins control-merge, and every test that needs a pin uses a synthetic, test-only file.
- **No sibling path.** Nothing reads `qukaizen-nucleus`, `qukaizen-buddy` or any `../` path at runtime or in tests. The vendored Buddy prompt carries `arail.vendor-stamp/v1` with the source commit, a sha256 for each source file and a file digest. `stamp_ok()` is enforced at harness start (`run.py:546`) and asserted in `test_brain_bench_grade.py:34`. Drift against upstream is TD2, by design.
- **`think:false`** is added only when `probe_thinking(entry)` reports the `thinking` capability (`brain.py:239-246`). T-B2 shows the llama body is byte-identical to before. The edge always passes `sampling=None` (T-B4). Failure direction: if the probe fails, the model is treated as non-thinking. That keeps llama's bytes unchanged and leaves a qwen3 brain in today's broken state. Acceptable.
- **Frozen `aerollm` surface.** No `AERO_*`/`AEROLLM_*` token counts changed in modified files (`app.py`: 14/20 at `845f9b25` and at HEAD). No ids, scripts or package names were renamed. One exception, the `NOTICE` byte pin, is finding 1.
- **Security.** The airgapped default is not touched. The harness child gets an env built from scratch (`run.py:528-537`): `ARAIL_ENV_FILE`, `ARAIL_SECRETS_FILE` and `LAB_ROOT` all point into a temp dir, it runs with `LAB_MODE=airgapped`, and it refuses to run if `egress.install_guard()` raises (`:468-473`). Ollama access is loopback only through `_ollama_base()` (`ollama.py:28-32`). T-H5 shows a planted `OPENAI_API_KEY` does not reach the child. Error output is fixed text. The `FROM` home path is dropped and only the 64-hex value is kept (T-P3 checks there is no `/Users/` in the reason). The egress gate is green.
- **Harness honesty.** `verdict.decide` withholds a verdict unless every self-check has `passed is True`, the run is complete and it is not a subset run (`verdict.py:116-122`; "not run" also withholds). The SC8 isolation check is appended by the parent after the child exits, so the child cannot fake it. `rules.v1.json` has `"status": "proposal"` and the report heading says PROPOSAL. The gaps are findings 2–4.
- **Implementation order** was followed. Tasks were frozen in `7fc94749`, before the runner (`14025676`) and before any arm ran.

### Rulings on the builder's deviations and "architect feedback" items

| Item | Ruling |
|---|---|
| O split 6 + 26 instead of 16 + 16 (`ai` World carries a `qukaizen` category) | **Accepted.** The design rule (reject a gold that exists in the offered World) was right, and the builder kept it. Side effect: M2 now mostly measures "AI question, qukaizen cards". The report should say so (finding 9). |
| Control-merge bound without the `describe()` family gate | **Accepted, and the spec is amended by this ruling.** The bypass only works inside the harness, on a temp registry, for a non-`pin:` family tag. A `pin:` candidate still goes through `describe()`, and SC5 requires `verify == match` (`run.py:140-151`, `:229-231`). The edge gate is unchanged. |
| SC5 fallback when `/api/ps` has no `context_length` (ggml runner) | **Accepted.** Checking evaluated prompt tokens against 4096 − 512 is a sound proxy, and it is recorded as a warning. |
| `ModelResponse.done_reason` / `prompt_tokens` | **Accepted.** Optional fields, `None` for every other backend. |
| `revoked` / `invalid` states; malformed file marks the known family invalid | **Accepted.** Fails closed. |
| NOTICE section 5 instead of 4 | The numbering is fine. The edit itself breaks a frozen pin: finding 1. |
| `--limit-per-category` | **Accepted.** `decide()` withholds the verdict for a subset run (tested). |
| `ARAIL_MODEL_PINS_FILE` override | **Not accepted as written.** It is honoured in production: finding 6. |
| Step 14 (reference run) not done by the builder | Acknowledged. W5/W6 are unproven until the in-progress run lands. QA must read its `report.md` against findings 2–3. |

## Code quality findings

1. **[BLOCK] `NOTICE:135-145`. The branch changes a byte-pinned frozen file, and BUILD_LOG says the result is pre-existing.**
   - `tests/test_queuellm_rename_frozen_surface.py::test_bundled_notice_and_bundle_manifest_are_byte_identical` has no `needs_base` marker. It asserts `sha256(NOTICE) == 8cc64663…`. That holds at `845f9b25` and `34c4c286` and fails at HEAD (`64c0bbea…`).
   - BUILD_LOG §Final state groups it under "already red at `845f9b25` … the archive copy has no `.git`, so they skip there". That is wrong for this test. Commit `298d5898` caused it.
   - The ARCHITECTURE asked for the NOTICE section (§4.4) and missed this pin. The design error is mine. The misreport is the builder's.
   - **Required fix:** pick one.
     - (a) Revert the NOTICE §5 block and put the Apache-2.0 base disclosure somewhere that is not frozen. ARAIL does not redistribute the weights, so ARCHITECTURE §4.4 says no Apache §4 duty lands on ARAIL. The catalog row comment, `model_pins.json` `model.base/license` and `docs/buddy-brain-bench.md` already carry it.
     - (b) Get the owner's explicit sign-off to re-pin the hash in that test, with a comment saying why. The test docstring reserves NOTICE changes for the bundle re-pin sprint.
   - Either way, correct the BUILD_LOG failure attribution.
   - `test_catalog_pinned_row.py:75` (`test_notice_records_the_opt_in_base…`) must follow whichever option is chosen.
2. **[ASK] `brain_bench/metrics.py:10-49`, `report.py:57-69`, `report.py:101-105`. Not every number has its threshold beside it (VISION W5).**
   - The side-by-side metrics table prints meaning and rationale but no threshold.
   - The registry has no `threshold` field, though ARCHITECTURE §5.6 specified `{…, rule_id, threshold, rationale}`.
   - The latency table has neither meaning nor threshold. R7's 1.5× / 2× appear only in the rules table.
   - `M1`'s `rule_id` is `"R3"`, which is wrong: R3 is M8, and M1 has no rule.
   - **Fix:** add `threshold` (or "no threshold: informational") to each registry entry and render it as a column. Give the latency rows the R7 thresholds and a one-line meaning. Fix M1's `rule_id`. This needs no re-run: `python -m arail.brain_bench report <results.json>` re-renders.
3. **[ASK] `brain_bench/run.py:646`. Exit code 0 when the verdict is withheld.**
   - The exit is `EXIT_UNTRUSTED` only when some self-check has `passed is False`.
   - A full run where SC2 or SC4 did not run (`passed: None`, for example without `--near-aa`) exits 0 with no verdict printed. The contract says 0 = "all self-checks passed, verdict printed".
   - **Fix:** exit 3 when `verdict.printed` is false on a full, complete run. Keep 0 for `--limit-per-category` smoke runs, or document a separate code for them.
4. **[ASK] `brain_bench/run.py:548`, `:625`. The promotion rules are not pre-registered by hash.**
   - `--rules` takes any file, and the verdict is computed from it.
   - `results.json` hard-codes `rules.path = "eval/buddy_brain/rules.v1.json"` even when another file was used. Only its sha256 is the real one.
   - The tasks file is hash-pinned through the manifest. The rules are not. That leaves "choose thresholds after seeing results" open (FM19's intent).
   - **Fix:** pin the `rules.v1.json` sha256, either in `tasks.v1.manifest.json` or in a sibling `rules.v1.sha256`. Withhold the verdict (or refuse) when the rules digest differs. Record the real path.
5. **[ASK] `brain_bench/run.py:600-604`. An interrupt skips the residency restore and temp cleanup.**
   - `KeyboardInterrupt` during `subprocess.run` propagates. `ollama.restore`, the SC8 snapshot and `rmtree(tmp)` never run.
   - FM17/C7 promise a restore on exit and exit 5 on interrupt.
   - **Fix:** wrap the child run in `try/finally` for the restore and cleanup, and map `KeyboardInterrupt` to `EXIT_ABORTED`.
6. **[ASK] `model_pins.py:92-94`. `ARAIL_MODEL_PINS_FILE` is honoured in production.**
   - BUILD_LOG says "production reads only the in-repo file". In fact any `.env` or shell can point the pin record at any path, including a sibling repo (against §4.5 and FM12's "reviewed PR" control).
   - **Fix:** honour it only under pytest (`PYTEST_CURRENT_TEST`), or file it as debt with a doc line. Preferred: test-only.
7. **[ASK] `model_resolve.py:143-155` (`_add_pin_state`). The slot-A picker shows an installed control-merge as "not released yet, so there is nothing to install".**
   - `verify` returns `pending` before it looks at the tag, so the row reads `present: true` and "nothing to install" at once.
   - `gallery_view` appends "Installed but not the pinned build: `…`" (`chat/__init__.py:335-338`). `slot_a_candidates` does not.
   - W3 asks for "not the pinned build" wording for this exact tag.
   - **Fix:** in `_add_pin_state`, when `row["present"]` and the check is not `match`, append the same suffix the gallery uses.
8. **[INFO] `model_pins.py:193-198`.** `family_of()` re-reads and re-parses `model_pins.json` on every call. `_resolve_a` calls it several times per installed tag, and the edge calls it twice per turn (`describe`, `_served_attrs`). The cost is small but unbounded in the tag count. An mtime-keyed memo would remove it. This is not a correctness issue.
9. **[INFO] Report text.** `report.py:68` always appends "Contamination unknown" to M8, even once held-out slugs are pinned. Make it conditional on `candidate_pin.heldout_n`. Also add one line under "What this run cannot tell you" about the 6/26 O split.

## Security findings

10. **[ASK] `model_defaults.py:142-171`, `:193-194` (existing code; the residual is new because the family is new).**
    - At `arail.config` import, `apply()` stamps the cached or pinned slot-A value into `MODEL_NAME` for non-portal processes without re-verifying it.
    - `stamp_env` never erases `MODEL_NAME` when A is unresolved. So `.env` `MODEL_NAME=qkz-aiml-qwen3-1.7b:control-merge`, together with "only control-merge installed" or "Ollama down", leaves the unverified family tag in `MODEL_NAME`, where registry tier 0 and the chat tab can use it.
    - The Buddy edge is protected (`describe()` re-verifies). The chat tab is not.
    - Reachable today only through an explicit operator `.env` edit, so this is not a BLOCK.
    - **Fix:** file it as TD7 in `sprints/BACKLOG.md` ("family value in MODEL_NAME bypasses verify outside the resolver"), and have QA probe the scenario once.
11. **[INFO] Checked and clean:**
    - No new non-loopback request site. `model_pins._fetch_*`, `probe_thinking` and `brain_bench/ollama.py` all derive the root from loopback-only helpers. `probe_thinking` runs only after `entry_is_local`.
    - No `/api/pull` or `/api/create` (T-H2/T-H8 assert the path set).
    - No secret or home path in any log, error or trace. `trace_attrs` carries tag, digests and state only.
    - The pin file is parsed strictly (unknown keys rejected), and `load()` cannot raise.
    - The `chat_default` refusal happens before `_write_secrets`.
    - The child's `cwd` is the repo, but every writable path is redirected to the temp root.
    - SC8 snapshots only `lab/pkb`, the data dir and `model_defaults.yaml`, not all of `lab/` as SC8 specified. That is acceptable because the child's `LAB_ROOT` is the temp dir.

## Test coverage assessment

- Every FM1–FM25 row has a matching test (T-R1–R6, T-P1–P8, T-A1–A3, T-B1–B5, T-C1–C2, T-H1–H11). FM24 (W1 clean-machine before/after) and FM26 (the owner's `MODEL_BACKEND`) are QA's, by design and still open.
- New tests: about 115, all green.
- I did not measure coverage on changed lines. Reading the tests, every branch of `verify`, `_resolve_a`'s family paths, `stream_turn` and `decide` is exercised. The report renderer is exercised only for its guard and banner, not for the threshold columns (finding 2).
- **The 65 full-suite failures. I spot-checked the 10 "head-only" ones:**

| Test | Reviewer result | Caused by this branch? |
|---|---|---|
| `test_egress_docs::test_the_guard_is_installed_exactly_where_privacy_says` | passes at HEAD | Was caused by the branch. Fixed in `8d7e38e1`. |
| `test_queuellm_rename_frozen_surface::test_bundled_notice_and_bundle_manifest_are_byte_identical` | **fails** | **Yes.** NOTICE edit in `298d5898` (finding 1). It does not depend on `.git`, so the builder's explanation does not apply. |
| `…::test_sealed_and_frozen_paths_are_byte_identical_to_base` | fails | Already red against `5f775f1c` (mastra licences, `pyproject.toml`). **This branch adds `NOTICE` and `eval/buddy_brain/*` to the diff.** The frozen list includes `eval` wholesale. ASK below. |
| `…::test_env_vars_and_frozen_ids_unchanged_in_every_modified_file` | fails | No. `app.py` token counts are identical at `845f9b25` and HEAD (14 `AEROLLM_MODEL`, 20 `AeroLLMBackend`), and `build-aerollm.sh` is untouched. |
| `test_qa_retest_fix_loop::test_f9_…costs_json…` | fails | No. `lab/data/costs.json` is git-ignored, dated Jul 30. |
| `test_qa6_failclosed_and_bootstrap::test_perf_mount_regression_under_10pct` | fails | No. Timing on a loaded machine (a harness run is in progress). |
| `test_queuellm_rename_qa::test_every_parameter_free_get_route_renders_no_user_visible_aerollm` | passes | No. Flaky. |
| `test_cli_picker`, `test_cli_status`, `test_cli_verbs` | **not re-run by me** | Unverified. They drive `start.sh`'s readiness phase, which can warm Ollama models, and I was told not to load models during the harness run. `test_cli_picker` uses the worktree's stale `.venv` (`_find_test_venv`). QA must re-run them at `845f9b25` and HEAD after the harness run finishes. |

- **[ASK] `tests/test_queuellm_rename_frozen_surface.py:71-73`.** `eval/buddy_brain/` lands inside a path that test freezes wholesale. The rename sprint meant to freeze the benchmark ledgers, not every future eval file. Either narrow the frozen entry to the ledger paths it meant to protect (owner sign-off, since it is a frozen-surface guard) or move the task set. Record the decision in SPRINT.md.

## Performance assessment

- Edge hot path: at most one extra `/api/show` per model per 60 s (`probe_thinking`, T-B1 counts it). Family verification is cached for 60 s. Pin-file reads are finding 8.
- Harness: the budget is 1,288 calls, with the estimate checked against a 45-minute cap before the run starts. The actual wall time and SC1/SC7 determinism come from the in-progress run. They were not observed here.

## Tech debt delta

ARCHITECTURE predicted TD1–TD6, and all six are filed in `sprints/BACKLOG.md` (`77669f64`). Unanticipated debt, to be filed by the builder (I am not editing ARCHITECTURE.md product sections, per the review-only instruction; these are its additions):

- **TD7** `MODEL_NAME` / `apply()` can carry an unverified family tag outside the resolver (finding 10).
- **TD8** `ARAIL_MODEL_PINS_FILE` production override, if it is kept (finding 6).
- **TD9** The frozen-surface test freezes all of `eval/`. Scope it to the ledgers (§Test coverage ASK).

Net: as predicted, slightly positive. The repaid items (F1/A1 closed, the thinking-model brain path fixed, one shared call site, pin flips traced) all landed.

## Required actions before merge

1. **(BLOCK, finding 1)** Restore `NOTICE` to `8cc64663…` and move the disclosure, or get owner sign-off to re-pin the hash. Correct BUILD_LOG's attribution of that failure.
2. (Finding 2) Add the threshold column and latency meaning/threshold, and fix M1's `rule_id`. Re-render the in-progress run's `report.md` before committing it as evidence.
3. (Finding 3) Make the exit code 3 when a full run's verdict is withheld.
4. (Finding 4) Hash-pin `rules.v1.json` and record the real rules path.
5. (Finding 5) Restore residency and clean up the temp dir on interrupt, and exit 5.
6. (Finding 6) Restrict `ARAIL_MODEL_PINS_FILE` to tests, or file TD8.
7. (Finding 7) Picker wording for an installed non-pinned family tag.
8. File TD7 (and TD8/TD9 as applicable) in `sprints/BACKLOG.md`. Decide the `eval/` frozen-path scope with the owner.
9. QA: re-run `test_cli_picker`/`test_cli_status`/`test_cli_verbs` at base and head once Ollama is free. Run W1 (clean-machine before/after). Check FM26 (`MODEL_BACKEND` on the owner's lab). Read the reference run's self-checks.

Items 2–7 are ASKs. If the builder files them as follow-up tickets instead, a re-review can come back WEAK_PASS once item 1 is resolved. Item 2 should land before the reference `report.md` is committed, because W5 is a win condition.
