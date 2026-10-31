# Test report: ARAIL deep, honest

**Date:** 2026-10-31
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) at `55051cb1` (code tip `8aed4ed8`; base arail main `62d5ebdf`; nothing pushed)
**Verdict:** **WEAK_PASS**
**QA tests added:** `tests/test_qa_deep_honest_verdict_store.py`, `tests/test_qa_deep_honest_buddy.py`, `tests/test_qa_deep_honest_setup.py`, `tests/test_qa_deep_honest_egress_happy.py`

## What this run shows

1. **No regression.** The full suite on HEAD fails exactly the same 151 test ids as an untouched `62d5ebdf` in the same pinned venv. Every one of the 343 new test ids passes. All 151 shared reds are environmental or predate the sprint (cause per id below).
2. **The win holds on the real wheel, run independently.** `scripts/deep-honest-win.sh`: 9 of 9 rows pass, exit 0, 0 silent outcomes, in 61 s. The operator's install, `lab/` and the QueueLLM checkout were byte-identical before and after. No crash report was written.
3. **No BLOCKER or MAJOR.** I found **11 defects in the sprint's own code**: 10 MINOR and 1 NOTE (QA-1 to QA-8 and QA-11 to QA-13; QA-9 and QA-10 are the TD14 and TD18 confirmations below). Each has a red test in the tree, 25 red tests in all. The weak side is the **clear path**: four ways a passing probe fails to clear deep, or leaves surfaces disagreeing or saying something false (QA-3, QA-6, QA-7, plus QA-1 and QA-2 for a malformed file). The failing side, the side the sprint exists for, held under every attack I made.
4. **Known debt confirmed by test:** TD10, TD14 and TD18 behave exactly as filed. I found 4 pre-existing behaviours, now pinned with `xfail(strict=True)`.

## How to read the numbers in this report

The operator's standing rule is that runs must teach. Every number below comes with four parts: **what it measures**, **the bar and why**, **what a value outside the bar means**, and **where the fault lives**. Where a table's numbers share their meaning, the four parts are given once, in a "Teaching" block under the table. Identifiers are labelled, not taught: test ids, line numbers, sha256 prefixes, commit hashes, pids, versions and dates. Severity words follow the orchestrator's scale: BLOCKER, MAJOR, MINOR, NOTE.

## Ship recommendation, and what you must decide

**Recommendation: ship after a short builder pass, or ship with the reds converted to tracked debt. Do not ship with the 25 red tests silently in place.** The core promise is proven, live and in the suite: on a broken wheel, every ARAIL surface says deep is failing, and none says it is fine or done. The defects are on edges: a malformed or symlinked store, a clock that steps back, a failed verdict write, a stale status dot after a clear, terminal escape characters, a pre-setup exit code, and two build-script messages. The fixes are all small (named per finding, most are one to five lines).

Decide (operator or orchestrator):
1. **Fix before merge or file as debt?** The 25 red tests (11 findings) turn the PR's CI red until each is fixed or re-marked `xfail(strict=True)` with a TD row. My suggestion: fix QA-3, QA-6 and QA-7 now (the clear path is what the operator will use after a QueueLLM fix lands) and QA-13 (a one-line exit code). File the rest as TD26 onward.
2. **Sprint records in a public repo.** `cdarnell/qukaizen-arail` is public. This sprint's records carry the local account name and paths: 23 lines in 5 files (LIVE_RUN.txt 12, ARCHITECTURE.md 5, BUILD_LOG.md 4, DAY1.json 1, REVIEW.md 1), including a private-index editable URL and Claude scratch paths. Product code has none. This follows precedent: 42 sprint files on main already do the same. Keep or scrub: your call.
3. **TD4 lockout plus QA-3.** Today the only recovery from a failure stamped in the future is to edit `lab/data/deep/verdict.json` by hand. If you want an operator-facing escape hatch (for example `deep probe --reset-failure`), that is a design choice, not a fix.

## Split of effort

| Area | ARAIL allocation | QA tests added (count, share of 89) | Effort spent (my estimate) | Why it differs from the allocation |
|---|---|---|---|---|
| Setup on a clean machine | 30% | 42 (47%) | about 30% | Many small, fast build-script cases. |
| Buddy (agents, edge, Curator, cycle) | 30% | 15 (17%) | about 25% | The builder's Buddy-side coverage was already deep (34 Curator, 22 hook, 14 policy tests). I spent on the gaps: concurrency on Eject, 60-tick spam, the clear cycle across processes, and live rows W1 to W4. The Buddy tests are the longest (one runs 12 s). |
| Security | 20% | 24 (27%) | about 25% | Hostile store files, symlinks, modes, escapes, secrets, egress, stderr bound. |
| Happy path | 10% | 1 (1%) | about 5% | Already covered by Q1, C3, C5, S6. Mine is one cross-process pass, fail, re-pass cycle. The live check is the honest acceptance run. |
| Regression | 10% | 7 (8%) | about 15% | Three full-suite runs (base, HEAD, HEAD plus QA), a by-id diff of 8,274 ids, 151 causes, frozen counts, benchmarks, packaging. |

**Teaching (all numbers in this table).** *Measures:* the share of this QA pass spent per area, as tests added and as my estimate of time. *Bar and why:* the ARAIL split in the workspace CLAUDE.md (30/30/20/10/10). Setup and Buddy carry the most weight because ARAIL runs on other people's machines and Buddy is the differentiator. *Outside the bar:* an area below its share got less new testing than the product's risk profile asks; the last column says why. *Fault lives in:* this QA pass's planning, not the product.

## Baseline and full suite

**Method.** Both runs used the pinned venv (Python 3.11.17, fastapi 0.140.7, starlette 1.3.1, httpx 0.28.1; nothing installed, upgraded or rebuilt). The baseline ran on a `git worktree add --detach 62d5ebdf` in my scratch folder, which is removed now. The venv's editable install names one checkout, so the baseline ran from an APFS clone of the pinned venv whose only change is the one-line editable `.pth` pointing at the baseline worktree; `pip freeze` is identical. Without that, test subprocesses that scrub `PYTHONPATH` would have imported HEAD's code during the baseline run. Both runs used the same command: `env -i`, a scratch `HOME` and `TMPDIR`, `ARAIL_SKIP_OLLAMA=1`, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider --continue-on-collection-errors`, and junit XML for comparison by id. The runs were sequential, not concurrent.

| Run | Test ids | Passed | Failed | Errors | Skipped | xfailed | Wall time | Load at start / end (1-min avg, 18 cores) |
|---|---|---|---|---|---|---|---|---|
| base `62d5ebdf` | 7,931 | 7,725 | 125 | 26 | 29 | 26 | 9 min 49 s | 0.94 / 3.97 |
| HEAD `55051cb1` | 8,274 | 8,068 | 125 | 26 | 29 | 26 | 11 min 47 s | 3.74 / 3.11 |
| HEAD plus the 4 QA files (final run) | 8,363 | 8,124 | 150 | 26 | 29 | 34 | 11 min 28 s | 1.96 / 2.33 |

**Teaching.**
- *Test ids, passed, failed, errors, skipped, xfailed.* Measures: pytest outcomes per collected test id. Bar: every id that passes on base passes on HEAD, and a new id passes unless it is a QA red. That is the only bar that separates this sprint's effect from the pinned venv's gaps. Outside the bar: a new failure is a regression, located in the code its test names. A new pass would mean the sprint fixed something on the way, which must be explained.
- *Wall time.* Measures: one sequential suite run. Bar: none. It is recorded so a hang would show (the builder saw 9.5 to 23 min). Outside: much longer means a test waited on a deadline. That fault lives in the slowest tests, listed by `--durations`.
- *HEAD plus the QA files.* Measures: the same outcomes with my 89 tests added. Bar: the HEAD run plus exactly my intended states: failed 125 + 25 (the QA-n reds), xfailed 26 + 8, passed 8,068 + 56, and no other id changing outcome. That shows the QA tests leak nothing into the rest of the suite. Result: met, with 0 changes on the 8,270 ids the two runs share (the 4 timestamped ids differ by name only). Outside: any other change would be a QA test disturbing the suite, and the fault would be in my test files.
- *Load.* Measures: the machine's 1-minute load average. Bar: under about 6 on 18 cores is "normal load" for this check. U9 failed at 13 before its fix. Outside: a failure seen only under higher load is a load-sensitive test, which is a finding. Both runs stayed under 4.

**My own tests in the full run.** The first HEAD-plus-QA run showed three of my guards failing only in the full suite, not alone: the 60-tick test and both Eject tests. These were defects in my tests, not the product. `tests/test_boot_security_scan.py` reloads `arail.activity`, so the `activity_log` my spy had patched was no longer the one the product writes to. And other tests leave `aerollm-rt` worker threads alive, which my thread check counted. I fixed both: the spy now covers every live `activity_log`, and the check now looks only at this backend's own workers. I re-ran the Buddy file after the reloading test and the backend-heavy deep tests in one session (only the two intended reds failed), then ran the full suite again. The table row above is that second run.

**Differences by id (base vs HEAD).**
- **New failures: 0.** **New passes: 0.**
- **347 ids exist only on HEAD.** 343 are the sprint's new tests, all passing; that equals the builder's 288 + 35 + 20. The other 4, together with 4 ids that exist only on base, are one test, `test_boot_autoresolve_qa::test_odd_sentinels_never_raise_and_never_wedge`. It puts `time.time()` into its parametrized ids, so its ids change on every run. It passed in both runs.
- *Teaching for these counts:* Measures: ids whose outcome differs between the two runs. Bar: 0 new failures, because any one is a regression this sprint caused. 0 unexplained new passes. Outside: the id names the test, and that test names the code.

**Fail on both: 151 ids** (125 failed + 26 errors). *Teaching:* Measures: ids red in both runs. Bar: each must have a cause outside this sprint's diff. Outside: a red with a cause inside the diff would be a sprint defect hiding as a baseline red. I found none. Fault lives in: the cause code per id below. The expected known reds are all present: `test_no_user_visible_aerollm` (C11) and the two `test_queuellm_rename_frozen_surface.py` ids (C10).

| Code | One line of cause (verified by the failure text in junit, or by a targeted re-run where marked) |
|---|---|
| C1 | `cryptography` (the maximus extra) is not in the pinned venv. Nucleus signing raises `SealError: signature signing/verification needs the maximus extra`; `assert 3 == 0` is the CLI exiting 3 for that reason. |
| C2 | Two strict xfails in `nucleus/test_qa_round3` (R3-A5) now pass and report `XPASS(strict)`. That is main's state, not this sprint's. |
| C3 | No `aerollm_api` wheel in the pinned venv (deliberate: test venvs never hold the real wheel), so tests that need it error or take the not-installed path. |
| C4 | `openai` 3.42.0 / the anthropic client in the pinned venv import `httpx2`, which was removed to pin Starlette's TestClient (REVIEW N12). `test_host_edge_brain.py` cannot be collected. |
| C5 | `pandas` is not in the pinned venv, so LanceDB writes fail. The perf and degrade tests then time out or see the wrong branch. |
| C6 | `transformers` is not in the pinned venv. `build_qkz_corpus` refuses to guess a chat template. |
| C7 | A bootstrap script runs a `python3` without `python-dotenv` (`ModuleNotFoundError: No module named 'dotenv'`). |
| C8 | `ARAIL_SKIP_OLLAMA=1`, which these QA rules require, makes `setup.sh` skip the Ollama ladder these tests assert on. **Re-run verified:** `test_default_path_pulls_llama_1b_and_creates_llama_ai_eng` passes without it. |
| C9 | The scratch `TMPDIR` sits under `/private/tmp`, and the test requires `DATA_DIR` to start with `/tmp` or `/var`. **Re-run verified.** |
| C10 | `test_queuellm_rename_frozen_surface` pins counts against a stale base (`5f775f1c`). It fails on main too (BUILD_LOG "Seen and not changed" 9). |
| C11 | One user-visible "AeroLLM" string on main: `src/arail/model_resolve.py:290`. Not this sprint's. |
| C12 | `scripts/reset.sh` calls `_ollama_pid_if_we_started_it: command not found`. A pre-existing script defect on main. |
| C13 | The test pins page text, layout or a code fact that main has since changed (dashboard cards, nav order, swarm surfaces, token ratchet, prompt text, the `egress.py` getenv audit, the loader's error log, the backend sentinel, recap depth, the profile activity event). Pre-existing on main. |
| C14 | In a full run, a `model-registry-health` thread that an earlier test started is still alive (order-dependent). Pre-existing on main. |
| C15 | Tier 1 is not enabled in this venv (no wheel), so the refresh never probes it and its health stays `unknown` (read, not traced). |
| C16 | The opencode lifecycle tests expect an environment and log rotation this machine does not provide. Pre-existing on main. |

| Test id (fails on base and on HEAD) | Cause |
|---|---|
| `nucleus.test_certify :: test_broken_position_unswap_moves_the_lc_golden` | C1 |
| `nucleus.test_certify :: test_build_report_carries_real_eyeball_outputs` | C1 |
| `nucleus.test_certify :: test_card_composite_and_decision_recompute_from_the_card_alone` | C1 |
| `nucleus.test_certify :: test_card_recompute_holds_on_a_compatible_by_cap_card` | C1 |
| `nucleus.test_certify :: test_certify_card_has_no_placeholder_values` | C1 |
| `nucleus.test_certify :: test_certify_end_to_end_produces_signed_card` | C1 |
| `nucleus.test_certify :: test_eval_hash_changes_when_a_task_template_changes` | C1 |
| `nucleus.test_certify :: test_eval_hash_changes_when_decoding_changes` | C1 |
| `nucleus.test_certify :: test_eval_hash_changes_when_eyeball_file_changes` | C1 |
| `nucleus.test_certify :: test_eval_hash_changes_when_judge_winner_table_changes` | C1 |
| `nucleus.test_certify :: test_eval_hash_identical_for_different_metric_values_same_yardstick` | C1 |
| `nucleus.test_e2e_gate_a :: test_gate_a_stub_pipeline_end_to_end` | C1 |
| `nucleus.test_qa_round3 :: test_cli_verify_non_fast_exits_3_even_when_everything_checkable_matches` | C1 |
| `nucleus.test_qa_round3 :: test_edited_build_report_is_not_covered_by_the_seal` | C1 |
| `nucleus.test_qa_round3 :: test_edited_eval_config_lock_caught_by_non_fast_verify_only` | C1 |
| `nucleus.test_qa_round3 :: test_editing_domain_eyeball_file_after_certify_does_not_change_verify` | C1 |
| `nucleus.test_qa_round3 :: test_eval_config_lock_carries_no_path_build_id_host_or_metric` | C1 |
| `nucleus.test_qa_round3 :: test_eval_hash_changes_when_contamination_params_change` | C1 |
| `nucleus.test_qa_round3 :: test_eval_hash_changes_when_judge_rubric_changes` | C1 |
| `nucleus.test_qa_round3 :: test_eval_hash_invariant_to_build_id_version_and_signing_key` | C1 |
| `nucleus.test_qa_round3 :: test_eval_hash_invariant_to_fidelity_target_and_top_n` | C1 |
| `nucleus.test_qa_round3 :: test_hand_edited_card_is_caught_by_verify_both_modes_and_badge[baselines]` | C1 |
| `nucleus.test_qa_round3 :: test_hand_edited_card_is_caught_by_verify_both_modes_and_badge[closed metric]` | C1 |
| `nucleus.test_qa_round3 :: test_hand_edited_card_is_caught_by_verify_both_modes_and_badge[composite value]` | C1 |
| `nucleus.test_qa_round3 :: test_hand_edited_card_is_caught_by_verify_both_modes_and_badge[decision upgraded]` | C1 |
| `nucleus.test_qa_round3 :: test_hand_edited_card_is_caught_by_verify_both_modes_and_badge[executable filled in]` | C1 |
| `nucleus.test_qa_round3 :: test_hand_edited_card_is_caught_by_verify_both_modes_and_badge[extra top-level key]` | C1 |
| `nucleus.test_qa_round3 :: test_hand_edited_card_is_caught_by_verify_both_modes_and_badge[open_ended lc]` | C1 |
| `nucleus.test_qa_round3 :: test_hand_edited_card_is_caught_by_verify_both_modes_and_badge[runtime relabelled]` | C1 |
| `nucleus.test_qa_round3 :: test_laundering_b_current_behaviour_signs_and_ledgers_a_real_looking_card` | C1 |
| `nucleus.test_qa_round3 :: test_malformed_eval_config_lock_is_a_mismatch_not_a_crash` | C1 |
| `nucleus.test_qa_round3 :: test_malformed_public_key_hex_current_behaviour_fails_closed` | C1 |
| `nucleus.test_qa_round3 :: test_malformed_signature_hex_reports_invalid_exit_3` | C1 |
| `nucleus.test_qa_round3 :: test_nucleus_verify_reads_the_card_seal_not_seal_json` | C1 |
| `nucleus.test_qa_round3 :: test_signing_key_is_0600_and_its_bytes_never_leave_the_key_file` | C1 |
| `nucleus.test_qa_round3 :: test_stub_card_verify_exits_3_and_badge_is_stub` | C1 |
| `nucleus.test_qa_round3 :: test_transplanted_seal_from_another_card_is_caught` | C1 |
| `nucleus.test_qa_round3 :: test_untampered_trusted_card_fast_verify_exit_0_and_badge_trusted` | C1 |
| `nucleus.test_qa_round3 :: test_zero_egress_across_every_phase_and_certify_in_process` | C1 |
| `test_queuellm_rename_frozen_surface :: test_env_vars_and_frozen_ids_unchanged_in_every_modified_file` | C10 |
| `test_queuellm_rename_frozen_surface :: test_sealed_and_frozen_paths_are_byte_identical_to_base` | C10 |
| `test_no_user_visible_aerollm :: test_no_user_visible_aerollm` | C11 |
| `test_reset_stop_scope :: test_foreign_uvicorn_survives` | C12 |
| `test_reset_stop_scope :: test_port_scoped_helpers` | C12 |
| `portal.test_token_compliance :: test_token_compliance_ratchet` | C13 |
| `test_cache_prewarm :: test_prewarm_warms_claude_with_frozen_system` | C13 |
| `test_dac_rename :: test_nav_order_dashboard_dac_chat` | C13 |
| `test_dashboard_layout_v2 :: test_template_mission_status_and_activity_feed_are_paired` | C13 |
| `test_instance_isolation_audit :: test_egress_is_the_only_getenv_data_dir_bypass_of_config` | C13 |
| `test_loader_skills_only_agents.TestLoadOneSkillsOnly :: test_a_broken_py_still_logs_normally[browser]` | C13 |
| `test_loader_skills_only_agents.TestLoadOneSkillsOnly :: test_a_broken_py_still_logs_normally[curator]` | C13 |
| `test_loader_skills_only_agents.TestLoadOneSkillsOnly :: test_a_broken_py_still_logs_normally[researcher]` | C13 |
| `test_model_hosting_reframe_qa :: test_backends_raises_on_sentinel_before_any_load` | C13 |
| `test_recap_core.TestCostCeiling :: test_calls_by_recap_depth_populated` | C13 |
| `test_runtime_profile_api :: test_post_emits_activity_event` | C13 |
| `test_swarm_goal_surfaces :: test_dashboard_renders_swarm_preview_surface` | C13 |
| `test_swarm_goal_surfaces :: test_research_page_renders_swarm_review_surface` | C13 |
| `test_autochecks_boot :: test_health_interval_zero_is_one_shot` | C14 |
| `portal.test_models_api :: test_health_refresh_probes_without_constructing_aerollm` | C15 |
| `portal.test_opencode_config_lifecycle.TestStartEnvVars :: test_start_sets_OPENCODE_CONFIG_DIR_env` | C16 |
| `portal.test_opencode_lifecycle.TestLogRotation :: test_log_rotation_at_10mb` | C16 |
| `nucleus.test_qa_round3 :: test_certify_refuses_stub_false_while_b7_stands` | C2 |
| `nucleus.test_qa_round3 :: test_certify_refuses_when_phase_stamps_deleted` | C2 |
| `test_aerollm_compute_source :: test_active_provider_accepts_aerollm` | C3 |
| `test_aerollm_defaults :: test_absolute_model_path_passes_through` | C3 |
| `test_aerollm_defaults :: test_aerollm_model_env_overrides_default` | C3 |
| `test_aerollm_defaults :: test_default_model_is_qwen25_7b_4bit` | C3 |
| `test_aerollm_defaults :: test_kv_budget_pct_boundary_values` | C3 |
| `test_aerollm_defaults :: test_kv_budget_pct_invalid_value_skips_kwarg` | C3 |
| `test_aerollm_defaults :: test_kv_budget_pct_translates_to_bytes` | C3 |
| `test_aerollm_defaults :: test_kv_budget_pct_unset_leaves_aerollm_default` | C3 |
| `test_aerollm_defaults :: test_missing_model_dir_suggests_correct_hf_repo` | C3 |
| `test_aerollm_defaults :: test_missing_model_dir_uses_overridden_model_in_hint` | C3 |
| `test_default_deep_backend_resolver :: test_macos_arm64_with_aerollm_returns_aerollm` | C3 |
| ` :: test_host_edge_brain` | C4 |
| `test_claude_cache :: test_claude_caches_system_when_above_floor` | C4 |
| `test_claude_cache :: test_claude_marks_last_message_breakpoint` | C4 |
| `test_claude_cache :: test_claude_no_system_key_when_none` | C4 |
| `test_claude_cache :: test_claude_old_sdk_sends_plain_system` | C4 |
| `test_claude_cache :: test_claude_sends_only_top_p_when_set` | C4 |
| `test_claude_cache :: test_claude_sends_temperature_when_no_top_p` | C4 |
| `test_claude_cache :: test_claude_system_plain_when_below_floor` | C4 |
| `test_docs_ingest :: test_index_all_includes_docs_rows` | C5 |
| `test_docs_sprint3_qa :: test_index_all_idempotent_no_duplicates_on_double_call` | C5 |
| `test_ensure_ready_build_isolation :: test_cross_world_contamination_probe` | C5 |
| `test_pkb_index :: test_ensure_ready_staleness_sweep` | C5 |
| `test_pkb_index :: test_flush_handles_missing_file_as_delete` | C5 |
| `test_pkb_index_perf :: test_single_write_latency_under_4s[0]` | C5 |
| `test_pkb_index_perf :: test_single_write_latency_under_4s[1]` | C5 |
| `test_pkb_index_perf :: test_single_write_latency_under_4s[2]` | C5 |
| `test_pkb_index_qa :: test_debouncer_coalesces_two_writes_10ms_apart` | C5 |
| `test_pkb_index_qa :: test_deeply_nested_path_round_trips` | C5 |
| `test_pkb_index_qa :: test_file_deleted_after_upsert_then_sweep_drops_row` | C5 |
| `test_pkb_index_qa :: test_filename_with_single_quote_does_not_break_delete_clause` | C5 |
| `test_pkb_index_qa :: test_filename_with_spaces_round_trips` | C5 |
| `test_pkb_index_qa :: test_merge_insert_absent_falls_back_to_delete_add` | C5 |
| `test_pkb_index_qa :: test_merge_insert_absent_idempotent_on_repeat` | C5 |
| `test_pkb_index_qa :: test_pending_write_recovered_by_next_boot_sweep` | C5 |
| `test_pkb_index_qa :: test_pkb_root_with_spaces_and_unicode_works` | C5 |
| `test_pkb_index_qa :: test_schedule_upsert_from_non_main_thread` | C5 |
| `test_pkb_index_qa :: test_schedule_upsert_on_directory_does_not_crash` | C5 |
| `test_pkb_index_qa :: test_staleness_sweep_cap_exceeded_falls_back_to_index_all` | C5 |
| `test_pkb_index_qa :: test_two_connections_to_same_db_can_both_query` | C5 |
| `test_pkb_index_qa :: test_unicode_filename_round_trips_through_upsert` | C5 |
| `test_pkb_index_qa :: test_wrong_vector_dim_never_drops_table` | C5 |
| `test_pkb_reembed :: test_shadow_verification_is_cardinality_only_documented_limitation` | C5 |
| `test_qa_retrieval_honesty :: test_the_header_names_a_fix_the_operator_can_act_on` | C5 |
| `test_w9_embedder_swap :: test_closed_port_ingest_writes_zero_vectors_leaves_existing_index_intact` | C5 |
| `test_w9_embedder_swap :: test_index_all_calls_the_real_embed_documents_symbol` | C5 |
| `test_w9_embedder_swap :: test_index_all_embedding_error_writes_nothing_and_leaves_existing_table` | C5 |
| `test_w9_embedder_swap :: test_semantic_search_query_embedding_error_degrades_and_falls_back` | C5 |
| `test_build_qkz_corpus :: test_fingerprint_records_the_markers_actually_used` | C6 |
| `test_build_qkz_corpus :: test_formatter_uses_the_checkpoints_own_template` | C6 |
| `test_qa6_bootstrap_cli :: test_all_instances_covers_root_plus_every_instance` | C7 |
| `test_qa6_bootstrap_cli :: test_bootstrap_exit_code_when_a_root_fails` | C7 |
| `test_qa6_bootstrap_cli :: test_bootstrap_on_a_completely_fresh_lab_dir` | C7 |
| `test_qa6_bootstrap_cli :: test_bootstrap_on_a_lab_with_no_worlds_at_all` | C7 |
| `test_qa6_bootstrap_cli :: test_corrupt_registry_record_does_not_stop_the_run` | C7 |
| `test_qa6_bootstrap_cli :: test_dry_run_writes_no_manifest_anywhere` | C7 |
| `test_qa6_bootstrap_cli :: test_empty_world_slug_falls_back_to_the_root_lab` | C7 |
| `test_qa6_bootstrap_cli :: test_install_bootstrap_hook_runs_and_is_non_fatal` | C7 |
| `test_qa6_bootstrap_cli :: test_per_instance_secrets_are_never_read_written_or_linked` | C7 |
| `test_qa6_bootstrap_cli :: test_root_only_bootstrap_leaves_instances_alone` | C7 |
| `test_qa6_bootstrap_cli :: test_stale_registry_entry_without_a_pkb_dir_is_skipped_not_fatal` | C7 |
| `test_qa6_bootstrap_cli :: test_world_slug_bootstraps_only_that_instance` | C7 |
| `setup_ladder.test_setup_ladder :: test_all_hosts_fail_falls_to_preview_net` | C8 |
| `setup_ladder.test_setup_ladder :: test_cdn_fires_when_url_set` | C8 |
| `setup_ladder.test_setup_ladder :: test_cdn_skipped_when_url_empty` | C8 |
| `setup_ladder.test_setup_ladder :: test_deep_7b_pull_failure_is_graceful` | C8 |
| `setup_ladder.test_setup_ladder :: test_deep_persona_auto_install_still_installs_1b_default` | C8 |
| `setup_ladder.test_setup_ladder :: test_deep_persona_flag_auto_installs_7b_via_modelfile_deep` | C8 |
| `setup_ladder.test_setup_ladder :: test_default_path_pulls_llama_1b_and_creates_llama_ai_eng` | C8 |
| `setup_ladder.test_setup_ladder :: test_hf_404_then_github_mirror_digest_match_creates` | C8 |
| `setup_ladder.test_setup_ladder :: test_hf_primary_success_respects_env_repo_and_quant` | C8 |
| `setup_ladder.test_setup_ladder :: test_hf_primary_success_single_pull_no_create_no_mirror` | C8 |
| `setup_ladder.test_setup_ladder :: test_legacy_ai_engineer_aliased_not_repulled` | C8 |
| `setup_ladder.test_setup_ladder :: test_legacy_ai_engineer_not_aliased_to_1b_default` | C8 |
| `setup_ladder.test_setup_ladder :: test_llama_pull_ok_but_create_fails_warns_and_continues` | C8 |
| `setup_ladder.test_setup_ladder :: test_mirror_digest_mismatch_fails_closed_no_create` | C8 |
| `setup_ladder.test_setup_ladder :: test_nomic_embed_text_pulled_when_absent` | C8 |
| `setup_ladder.test_setup_ladder :: test_placeholder_digest_disables_mirror_no_download` | C8 |
| `setup_ladder.test_setup_ladder :: test_preview_net_narrative_has_no_qwen_marketing` | C8 |
| `setup_ladder.test_setup_ladder :: test_selfhosted_flag_activates_hf_ladder` | C8 |
| `setup_ladder.test_setup_ladder :: test_total_failure_exits_clean_and_prints_manual_command` | C8 |
| `setup_ladder.test_setup_ladder_qa_nomic :: test_default_model_failure_does_not_stop_the_embedding_pull` | C8 |
| `setup_ladder.test_setup_ladder_qa_nomic :: test_embedding_pull_is_tier_independent` | C8 |
| `setup_ladder.test_setup_ladder_qa_nomic :: test_embedding_pull_runs_at_most_once` | C8 |
| `setup_ladder.test_setup_ladder_qa_nomic :: test_nomic_pull_failure_does_not_stop_the_default_model_from_installing` | C8 |
| `setup_ladder.test_setup_ladder_qa_nomic :: test_nomic_pull_failure_never_fails_setup` | C8 |
| `setup_ladder.test_setup_ladder_qa_nomic :: test_nomic_pull_failure_prints_the_exact_recovery_command` | C8 |
| `test_qa_retest_fix_loop :: test_f9_every_test_starts_from_a_zeroed_cost_tracker_under_tmp` | C9 |

## Live check (run independently)

**Command**, from the worktree, with a scratch `HOME` and `TMPDIR` and `ARAIL_SKIP_OLLAMA=1`, bounded by a 1,200 s process-group timeout:
```
env -i PATH=$PATH HOME=<scratch>/home/live TMPDIR=<scratch>/tmp/live ARAIL_SKIP_OLLAMA=1 \
  ARAIL_WIN_PYTHON=~/ProJects/qukaizen-arail/.venv/bin/python \
  ARAIL_WIN_ENV_FROM=~/ProJects/qukaizen-arail/.env \
  ARAIL_WIN_QUEUELLM=~/ProJects/qukaizen-queuellm \
  ARAIL_WIN_JSON_OUT=<scratch>/live/win.json  bash ./scripts/deep-honest-win.sh
```
**Result: exit 0, 9 of 9 rows PASS, total time 61 s.** The wheel was `aerollm_api` 0.1.0-rc.2, native sha256 `d62e1e16e93c…` (the `DAY1.json` wheel). The first error came at call 2 with the `rms_norm` text. The same rows and counts as the builder's two runs.

| Row | What it checks | Deep calls attempted | Deep calls failed | Silent outcomes | Result |
|---|---|---|---|---|---|
| W0 | preflight | 0 | 0 | not applicable | PASS |
| W1 | Chat, 5 turns on deep | 5 | 4 | 0 | PASS |
| W2 | background agents, 5 calls | 2 | 1 | 0 | PASS |
| W3 | Curator review, 6-term World | 6 | 5 | 0 | PASS |
| W4 | Curator grow, same World | 7 | 6 | 0 | PASS |
| W5 | `./arailctl deep probe --json` | 5 | 4 | 0 | PASS |
| W6 | fresh process: every surface, then Check | 0 | 0 | 0 | PASS |
| W7 | `deep status` and the build-script refusals | not counted | not counted | 0 | PASS |
| W8 | postflight: nothing of the operator's changed | not applicable | not applicable | not applicable | PASS |

**Teaching.**
- *Deep calls attempted.* Measures: deep generations the row's consumers made in the row's own fresh process. For W5 it is the probe's own `total`, copied from its JSON. Bar: W1 5, because Chat is the operator's pick and never falls back. W2 2, because call 2 records the failure and calls 3 to 5 must not try deep. W3 6, one judge call per term. W4: every call counted. W5 5. W0 and W6 0, because reading must not generate. Outside: more than the bar in W2 means a gate did not close, so the fault is in `deep_policy.explain` or the backend hook. Fewer means a row did not test what it claims.
- *Deep calls failed.* Measures: of those calls, the ones that raised or returned no text. Bar: every call after the first, because this wheel answers once per process (`DAY1.json`). Outside: fewer means the wheel changed, or an error was swallowed before the hook counted it (`router/backends.py`). More means a new fault, such as call 1 failing.
- *Silent outcomes.* Measures: places where ARAIL said deep was fine, or the work done, when it was not. Bar: 0, because any one is the defect this sprint removes. Outside: the row's check lines name the surface, and the fault is in that surface's code.
- *Total time 61 s.* Measures: wall time, first row to last. Bar: under 5 minutes. The live check's own bar comes from nine rows of model loads (the builder saw 55 to 141 s). Outside: much longer means a row waited on a deadline (a hung call), and the fault is in the slowest row.

**The operator's install, before and after** (read-only snapshot script, run outside the harness):

| What was snapshotted | Before | After |
|---|---|---|
| `site-packages/aerollm_api/aerollm_api.abi3.so` (the binary `import aerollm_api` loads) | 21,894,128 bytes, mtime 2026-06-28 10:35:55, sha256 `d62e1e16…7579e` | identical |
| `site-packages/aerollm_api.abi3.so` (the shadowed copy, never loaded) | 17,411,888 bytes, mtime 2026-05-02 23:05:58, sha256 `b780d724…500a3` | identical |
| every file in `aerollm_api/` and `aerollm_api-0.1.0.dist-info/` (10 files, size, mtime, sha256), including `__pycache__/__init__.cpython-311.pyc` dated 2026-06-21 | recorded | identical |
| marker files `aerollm_api.bundle.json`, `aerollm_api.build.json` | absent | absent |
| top-level `site-packages` listing | 455 entries, listing sha256 `28002653…` | identical |
| `~/ProJects/qukaizen-arail/lab/data` (`ls -la`) | 21 entries; no `deep/` folder | identical; still no `deep/` |
| QueueLLM checkout | HEAD `87ff5005`, clean tree | identical |
| files under the operator's `.venv` or `lab/` newer than the run's start | (start marker taken) | none |

*Teaching for the sizes and counts above:* they are identifiers of the files, compared for equality only. Bar: identical, because the live check must never change the operator's machine (row W8 asserts the same thing from inside). Outside: any difference names the file, and the fault would be in `scripts/deep_honest_win.py`'s isolation. Note that the QueueLLM HEAD moved again since the builder's run (`e6346967` there, `87ff5005` now). Someone else is committing to that checkout; nothing in this sprint writes to it, and git ran with `--no-optional-locks`.

**Crash reports.** `~/Library/Logs/DiagnosticReports`, listed before and after: the newest report is still `python3.12-2026-10-25-095238.ips` (the operator's own, from before this sprint). `find -newer <start marker>` found **0 new reports**. *Teaching:* Measures: crash reports written while the live check ran. Bar: 0, because a native abort of the real wheel would write one, and QA must not cause one. Outside: a new `python*` report would mean a row or the probe crashed the runtime, a fault in the QueueLLM binary that ARAIL then reports as `CRASHED`.

**Observations from the live run.**
- The probe's "holders" check (`lsof -t -- <native file>`) sees **pid 5617, the OrbStack Virtualization VM**, as holding the live binary. It is a VM process (94 MB resident, not Python), so it most likely holds a file handle through a shared folder; it cannot have the model loaded. A probe run by hand will print "pid 5617 (…Virtualization…) already has this binary loaded; if it holds the model, this probe is a second copy". The hedge keeps the sentence true, but it will puzzle the operator. NOTE N7.
- W5's teaching block borrows the shared "out of range" text ("more than the bar: ARAIL kept calling a build it already knew was failing, so a gate did not close"). The probe has no gate and always makes 5 calls, so for W5 that sentence cannot apply. NOTE N8.

## Findings

Severity: **BLOCKER** stops the merge. **MAJOR** gives a weak pass. **MINOR** is a defect to fix or file. **NOTE** is small or cosmetic. Every finding below is in code this sprint added or rewrote, and has a red test that fails today.

### QA-1 (MINOR). A deeply nested `verdict.json` fails open, loses later failures, and a probe cannot repair it
- **Where:** `src/arail/deep_verdict.py:461` catches `OSError, ValueError, UnicodeDecodeError`. `json.loads` raises **`RecursionError`** on deep nesting, which escapes. `current()` (`:912`) turns it into `"untested"`. The writers `_locked_update` (`:540`) and `record_probe` (`:798`) raise the same error.
- **Input or state:** a `verdict.json` holding JSON nested 100,000 levels deep (hand-crafted; ARAIL never writes one).
- **What the user sees:** deep reads "has not been tested … the verdict could not be read (RecursionError …)". `explain()` says ok, so agents call the broken wheel again. Each failed call is lost: not stored, not kept in memory, no transition line. `deep probe` ends `recorded NOT SAVED (RecursionError …)` on every run, until someone deletes the file by hand. C1 and `docs/cli.md` both promise "a corrupt file reads as failing and is moved aside by the next writer".
- **Smallest fix:** add `RecursionError` to the `except` at `:461` (or catch `Exception` there). The move-aside path then works as designed.
- **Red tests:** `test_qa_deep_honest_verdict_store.py::test_qa1_a_deeply_nested_verdict_file_reads_as_failing_not_untested`, `::test_qa1_a_deep_call_that_fails_while_the_file_is_deeply_nested_is_still_recorded`, `::test_qa1_a_probe_repairs_a_deeply_nested_file`.

### QA-2 (MINOR). A schema-valid file whose `failure` is not a failure object fails open, and its writers break
- **Where:** `_validate_doc` (`deep_verdict.py:416`) checks only the top level. `_assemble` (`:868`) treats a falsy `failure` (`{}`) as none. For a string, number or list, `_failing_detail` (`:842`) raises and `current()` returns `"untested"` (`:912`). `_bump_failure` (`:608`) raises on a non-dict, so `note_call` loses the failure and `record_probe` returns NOT SAVED.
- **Input or state:** `"failure": "yes"`, `5`, `["x"]`, `{}`, or a failure whose `since` is a number. Hand-edited. An operator stuck by QA-3 is the likely editor.
- **What the user sees:** deep "untested" while the file says failing. For the string, number and list cases, every later deep failure goes unrecorded and every probe says NOT SAVED.
- **Smallest fix:** validate each entry in `_validate_doc`: `failure` and `last_probe` are `None` or a dict, and timestamps are finite numbers. Any other value fails closed (`store_unreadable`), and the next writer moves the file aside.
- **Red tests:** `::test_qa2_a_malformed_failure_entry_reads_as_failing[…]` (5 cases), `::test_qa2_a_failed_call_and_a_probe_still_land_on_a_malformed_entry[…]` (3 cases).

### QA-3 (MINOR). A failure stamped in the future cannot be cleared, and the probe gives a false reason
- **Where:** `record_probe` clears a failure only when `failure.last_ts < started_ts` (`deep_verdict.py:778`). The probe's message comes from `deep_probe.py:924`, `:934` and `:972`.
- **Input or state:** the clock was ahead when a failure was written and was later stepped back (an NTP correction, a VM or WSL clock jump). Or `last_ts` is `NaN` or `Infinity` (JSON allows both; hand edit).
- **What the user sees:** `deep probe` passes 5/5, then prints `recorded … a deep call failed elsewhere while the probe ran; still failing` and exits 1. No call failed during the probe, so the sentence is false. Deep stays locked until the wall clock passes the stamp: an hour in my test, never for `NaN` or `Infinity`. No command recovers; only a hand edit of `verdict.json` does. You asked about exactly this case ("can a probe clear it?"): no.
- **Smallest fix:** in `record_probe`, also clear when `last_ts` is not finite or is later than `now` at record time, because a failure from the future cannot have landed during this probe. The fuller fix is TD16's: snapshot `failure.count` when the probe starts and keep the failure only if the count grew. That handles both clock directions.
- **Red tests:** `::test_qa3_a_failure_with_a_future_or_non_finite_time_is_cleared_by_a_passing_probe[NaN|Infinity|one hour ahead]`, and `::test_qa3_after_the_clock_steps_back_the_real_probe_neither_clears_deep_nor_tells_the_truth` (the real `python -m arail.deep_probe`, end to end).

### QA-4 (MINOR, security). A symlink at the state folder sends ARAIL's writes, and a chmod, outside it
- **Where:** `_locked_update` (`deep_verdict.py:522-527`) runs `os.makedirs(..., exist_ok=True)`, `os.chmod(d, 0o700)` and `os.open(d / "verdict.lock", O_NOFOLLOW)`. `O_NOFOLLOW` guards only the last path component. `_take_probe_lock` (`deep_probe.py:429`) does the same.
- **Input or state:** `lab/data/deep` (or `ARAIL_DEEP_STATE_DIR`) is a symlink to another directory. Planting it takes write access to `lab/data`, which means the same user, or a checkout shared with someone else.
- **What the user sees:** nothing. ARAIL re-permissions the target directory to `0700` and writes `verdict.lock`, `verdict.json` and temp files into it (tested). By reading the code, it would also rename a foreign `verdict.json` there that it cannot parse to `.corrupt-*`. You set this requirement in the task ("a planted symlink must not make ARAIL write outside lab/data/deep"), and it does not hold for the folder itself.
- **Smallest fix:** `lstat` the state folder before every write. If it is a symlink, or is not owned by `os.getuid()`, refuse: keep the failure in memory and set `store_error` ("… is a symbolic link; the verdict store is never written through one"). Do the same in `_take_probe_lock` (exit 5).
- **Red test:** `::test_qa4_a_symlinked_state_folder_is_not_written_through`.

### QA-5 (MINOR, security). Control characters in error text reach the terminal raw
- **Where:** `_clip` (`deep_verdict.py:175`) collapses whitespace but keeps C0 controls (ESC, BEL). Its output is printed by `deep status` (`_generation_lines`), the probe's call and summary lines, and the stored `last_error`.
- **Input or state:** a runtime error, or a stored verdict, containing `ESC[2K ESC[1A … BEL`.
- **What the user sees:** the terminal executes the sequences. A line can be erased and the cursor moved up, so an attacker who shapes an error message can hide the FAILING line or forge text (CWE-150). The portal escapes HTML (F25); the CLI has no equivalent. Who can shape the text: the native runtime, or anyone who can write `verdict.json`. A runtime that echoes prompt fragments into an error would extend that to a shared World's term text (not verified).
- **Smallest fix:** in `_clip`, replace characters below `0x20` (and `0x7f`) with `?` or `\xNN`. Every CLI path goes through it.
- **Red tests:** `::test_qa5_deep_status_prints_no_raw_control_characters_from_a_stored_error`, `::test_qa5_deep_probe_prints_no_raw_control_characters_from_a_runtime_error`.

### QA-6 (MINOR). A failure this lab could not save outlives a passing probe, and the remedy it gives is false
- **Where:** `_note_failure` keeps a failure in `_P.mem` when the write fails (by design, F12). `record_probe` never clears `_P.mem`, and `_assemble` (`deep_verdict.py:866-871`) prefers it over a cleared file.
- **Input or state:** a deep call fails while the verdict write fails: the lock busy past its 2 s cap (the reviewer saw this happen under load in U9), a full disk, or a permissions problem. Then the operator runs `deep probe` and it passes 5/5.
- **What the user sees:** the probe prints "recorded passing · deep is cleared for agents and the Curator". That lab keeps `deep_failing` until it restarts, and its detail says "use the fast model until ./arailctl deep probe passes 5/5 on this build", which is the run that just passed.
- **Smallest fix:** in `_assemble`, ignore an in-memory failure whose `last_ts` is older than the stored `last_probe.started_ts` of a passing probe. Or drop the matching `_P.mem` entries when a pass is read.
- **Red test:** `test_qa_deep_honest_buddy.py::test_qa6_a_failure_this_lab_could_not_save_is_cleared_by_a_passing_probe`.

### QA-7 (MINOR). After a probe clears deep, the tier-1 dot stays red until Check, so the surfaces disagree
- **Where:** `complete_preferring_deep` still calls `registry.report_failure(TIER1_ID, exc)` (`agents/deep_policy.py:349`), which stores `unhealthy: RuntimeError: …`. `_refresh_cleared_overlay` (`registry/core.py:149-157`) re-probes only a stored health that starts with the overlay's own prefix.
- **Input or state:** an agent's deep call failed in this lab, and later `deep probe` passes, with no lab restart in between. That is the TD4 path after a one-off failure.
- **What the user sees:** `explain()`, the research strip and the probe all say deep is cleared. The status-bar dot, the degradation banner and the registry's `reasoning`/`build` bindings still say tier 1 is unhealthy, showing the old raw `rms_norm` error, until someone presses Check. ARCHITECTURE C5 and H12 promise "the dot turns green without a Check". That holds only when the stored health came from the overlay.
- **Smallest fix:** in `_refresh_cleared_overlay`, re-probe any stored `unhealthy` on an `aerollm` entry once the verdict stops failing, not only one with the overlay prefix. A residency probe is cheap and never generates.
- **Red test:** `test_qa_deep_honest_buddy.py::test_qa7_after_a_probe_clears_deep_the_tier1_dot_agrees_with_explain`.

### QA-8 (MINOR). After a source build, `deep install` says the file has "no install record"
- **Where:** `scripts/build-aerollm.sh:372-373` (F7) checks only for `aerollm_api.bundle.json`. This sprint made source builds write `aerollm_api.build.json`, the install record C7 asks for.
- **Input or state:** a maintainer runs `deep rebuild` (verified, marker written), then `deep install`.
- **What the user sees:** "an aerollm_api.abi3.so with no install record is already at … Refusing to overwrite it". Refusing is right. The reason is false, and VISION counts "a false line from scripts/build-aerollm.sh, in any mode" as a silent outcome.
- **Smallest fix:** when `aerollm_api.build.json` names the file's sha256, say "a source build (aerollm_api.build.json, QueueLLM commit <7>) is already at …".
- **Red test:** `test_qa_deep_honest_setup.py::test_qa8_install_over_a_source_build_names_the_build_record_instead_of_saying_there_is_none`.

### QA-11 (NOTE). A checkout path with a backslash or a newline can never be built, and the refusal misleads
- **Where:** `sha256_of` (`scripts/build-aerollm.sh:168-170`) takes field 1 of `shasum -a 256 <file>`. For a file name with `\` or a newline, `shasum` escapes the line and puts `\` before the digest.
- **Input or state:** `ARAIL_AEROLLM_REPO` under a folder whose name has a backslash or a newline. Very rare.
- **What the user sees:** a correct build is removed with "Installed …, but import aerollm_api loads <the same file> (sha256 …). Removed the copy.", which reads as if another binary won the import. It fails closed.
- **Smallest fix:** `shasum -a 256 < "$1" | awk '{print $1}'` (hash stdin, so there is no file name to escape).
- **Red test:** `::test_qa11_a_checkout_path_with_a_backslash_or_newline_builds_and_verifies[backslash|newline]`.

### QA-12 (MINOR, security). The probe supervisor holds a whole stderr line in memory
- **Where:** `_gather_stderr` (`src/arail/deep_probe.py:553-557`) uses `readline()` and clips each line to 300 characters only after reading all of it.
- **Input or state:** the child writes one long line with no newline: a native progress bar redrawn with `\r`, or a runaway log.
- **What the user sees:** measured with the fake runtime, a 300 MB line took the supervisor from 26 MB to 1,334 MB resident; a 64 MB line added 283 MB. The supervisor exists so that a misbehaving child cannot take it down. On a 16 GB Mac that already holds a model copy, a spike like that ends in swap. The stdout protocol has the same shape (`buf += chunk` until a newline), but only the child's own `emit` writes there.
- **Smallest fix:** read stderr with `os.read(fd, 65536)` and keep a bounded tail (for example the last 64 KB), splitting lines from that.
- **Red test:** `::test_qa12_a_long_stderr_line_from_the_child_does_not_balloon_the_supervisor`.
- **Teaching for 26, 1,334 and 283 MB:** Measures: the supervisor's peak resident memory (`ru_maxrss`) before and after a probe whose child wrote one long stderr line. Bar: growth below about 48 MB for a 64 MB line, because the supervisor keeps only 300 characters of it. Outside: growth that scales with the line (here about 4.4 times the line) means the line was held whole. Fault lives in `_gather_stderr`.

### QA-13 (MINOR). Before setup, `./arailctl deep probe` exits 1, the code that means "deep is failing"
- **Where:** `arailctl:801` dies (`die` always exits 1, `:179`) when `.venv` is missing, for every `deep` op, including the new `probe`.
- **Input or state:** a fresh clone, before `./arailctl setup`.
- **What the user sees:** the message is right ("missing .venv — run ./arailctl setup first"), but the exit code is 1. `docs/cli.md` gives 1 the meaning "the probe ran and fewer than 5 answered … deep is failing"; "not run, nothing recorded" is 5. A script, CI step or future `/deep` caller is told deep generation failed on a machine where nothing ran. The message also says "deep install targets the lab's venv" when the user asked for `probe`.
- **Smallest fix:** in the deep branch, `[[ "$op" == probe ]] && { echo "…not run: missing .venv — run ./arailctl setup first" >&2; exit 5; }` before the `die`.
- **Red test:** `::test_qa13_deep_probe_before_setup_exits_5_not_run`.

**About the numbering:** QA-9 and QA-10 are confirmations of filed debt, listed next. The ids are kept so test names stay stable.

### Known debt confirmed by test (`xfail(strict=True)`: green today, red the day the debt is paid)

| Id | Debt | What the test proved | Test |
|---|---|---|---|
| QA-9 | **TD14** (copy in place, no rollback) | Ctrl-C during the install's verification import leaves the new, never-verified `.so` and marker installed. The working pair survives only as `$TMPDIR/arail-aerollm-prev*.XXXXXX`, and no message says where. The EXIT trap removes the extract folder only. A disk-full during `cp -f` truncates the live `.so` in place for the same reason. | `test_qa_deep_honest_setup.py::test_qa9_td14_ctrl_c_during_verification_puts_the_previous_install_back` |
| QA-10 | **TD18** (SIGINT only) | SIGTERM or SIGHUP kills the supervisor. The child, in its own session, runs on, forever when its call hangs, holding a model copy. SIGKILL plus a hung call is the accepted F21 limit (A6: no cancel). | `::test_qa10_td18_a_stopped_probe_stops_its_child[SIGTERM|SIGHUP]` |
| — | **TD10 / re-review M3** | After an Eject, Buddy's fast call is stamped `deep_failing` and `report_failure` stores tier 1 unhealthy (`CancelledError: ` with no text), while the verdict correctly says untested. | `test_qa_deep_honest_buddy.py::test_qa_td10_a_call_closed_by_eject_is_not_stamped_deep_failing` |

### Pre-existing behaviour found (not this sprint's change; `xfail(strict=True)`)

| Behaviour | Why it matters | Test |
|---|---|---|
| An asyncio-cancelled review task leaves `_review_state` at `running` (`world_routes._run_review` catches `Exception`, and `CancelledError` is a `BaseException`). | Every later review gets 409 `review_busy` until restart. Production cancels these tasks only at shutdown, so the impact is low. | `test_qa_deep_honest_buddy.py::test_qa_preexisting_a_cancelled_review_task_does_not_leave_the_curator_running` |
| A growth pass the operator cancels is still resealed, swapped and logged as an evolution ("+0 new terms … (all N model calls answered)"). `dac_world` breaks out of its loops without raising. | It is the empty-pass pattern of TD12, reached by Cancel instead of by failure. The review path handles cancel (F18); grow does not. | `::test_qa_preexisting_a_cancelled_grow_pass_does_not_reseal_the_world` |
| With `HOME` unset and no `ARAIL_AEROLLM_REPO`, `set -u` stops every mode of `build-aerollm.sh`, `status` included (`HOME: unbound variable`). | `deep status` cannot report from a launchd or `env -i` context. | `test_qa_deep_honest_setup.py::test_qa_preexisting_deep_status_runs_without_HOME` |
| `deep status` echoes `AEROLLM_INDEX_URL` verbatim, so credentials written into that URL (`https://user:pass@…`) are printed. | A pip-style credentialed URL leaks to the terminal and to logs. | `test_qa_deep_honest_egress_happy.py::test_qa_preexisting_deep_status_does_not_print_credentials_from_the_index_url` |

### Notes (no red test)
- **N1.** A partial review that found a flag never mentions it ("5 of 6 terms judged … QueueLLM failed on 1 call"). The flag is in `review.json` and on World Terms. Grammar: "the 1 unjudged term have no flags, so their silence means nothing" (`world_routes._review_report`).
- **N2.** A review of a World with 0 terms reports "Curator reviewed all 0 of 0 terms … All clear." This is vacuously true, and nothing was judged.
- **N3.** `deep status` for a wheel whose import raises says "aerollm_api -> no native QueueLLM module is loaded" and hides the `ImportError` text; `deep probe` shows it. Base said "not installed", so this is not a regression.
- **N4.** A 50 MB valid `verdict.json` (hostile or edited): the first read takes 32 ms, and each failed-call write takes 0.53 s, because it rewrites the whole file under the lock. A same-process reader waits up to 94 ms (TD17). The store caps entries at 32 but not their size. Same-user only.
- **N5.** `--json` with SIGINT prints nothing (exit 130). The docs promise one JSON object on stdout. A non-run probe still creates `probe.lock` in the state folder; nothing is recorded in the verdict, as promised.
- **N6.** Pre-existing, not this sprint's: `package-data` (`static/*`) does not reach `static/js/`, so a wheel ships 0 JS files, `world-terms.js` included. ARAIL runs from a checkout, so this is moot today.
- **N7.** The holder line names the OrbStack VM as a second copy (see the live check).
- **N8.** W5's teaching reuses gate wording that cannot apply to the probe (see the live check).
- **N10. Do all surfaces agree across the cycle?** While failing: yes, in the same process and in a second one (`explain`, the research strip, the overlay, `deep status`; tests 28 and 89). After a clear, they agree except in QA-6 and QA-7. A restart or a second World instance carries neither stale state, because both are per-process memory. The Chat deep picker (`/api/chat/models`) does not read the verdict at all, by design (C8: "Chat can still pick it"), so it shows deep as available both while failing and after a clear. Chat's own failure stays loud (live W1).
- **N12. The real stack leaves a 1 KB file in `/tmp` per process that ends via `os._exit`.** After the live run, `/private/tmp` held 5 new `__KMP_REGISTERED_LIB_<pid>` files (the LLVM OpenMP runtime's registration). Their pids were gone. Another 29 were already there from the architect's day-1 probes (2026-10-29 09:35-09:40) and the builder's live runs (2026-10-31 12:02-12:09). The probe child (`deep_probe.py:360`) and the live-check rows (`scripts/deep_honest_win.py:1270`) end with `os._exit`, on purpose, which skips the runtime's cleanup. The files are harmless as files. A stale registration that matches a reused pid is a known trigger of OpenMP's "Error #15 … already initialized" abort; I did not verify that here. Fix if wanted: unlink `/tmp/__KMP_REGISTERED_LIB_<own pid>` just before `os._exit`. I removed my 5 and left the 29 that are not mine.
- **N11.** `build-aerollm.sh update` was not exercised; it is a pip install from the network, and the rules forbid `deep update`.
- **N9.** Pre-existing: `build-aerollm.sh` puts paths inside `python -c "…open('$dest_marker')…"`. A site or `TMPDIR` path with an apostrophe breaks the idempotence check and the version line (fails safe). `AEROLLM_BUNDLE_FILE` pointing at a directory says "does not exist".

## Security review

| Surface | What I actually checked | Findings |
|---|---|---|
| Verdict store: reads | A deeply nested file, NaN and Infinity, duplicate keys (by reading, not by test: Python's `json` keeps the last value, and every reader, `build-aerollm.sh status` included, parses through it), a non-UTF-8 byte, a key that looks like a path, 50 MB, another schema, a directory, mode 000, a symlink. | **QA-1, QA-2, QA-3** (fail-open or stuck). The non-UTF-8, schema, directory, mode-000 and symlink cases fail closed and are repaired (guards pass). N4. |
| Verdict store: writes | Symlinks at `verdict.json`, `verdict.lock`, the temp name `verdict.json.tmp-<pid>-<tid>` and the state folder. Modes after every write path over 0755/0644. | Files: no write through any symlink (guards pass). 0700 and 0600 restored after `note_call`, `note_load_failure` and `record_probe` (pass and fail). **QA-4**: the folder itself is followed. By reading the code: `probe.lock` (no `fchmod` in `_take_probe_lock`) and `.corrupt-*` (a rename keeps the mode) keep a looser earlier mode inside the 0700 folder (harmless). |
| Concurrency on the store | 8 processes writing for 6 s while this process read with `explain()`. | Exact: 25,585 of 25,585 writes counted, no lost update, no torn read (every read said `deep_failing`). |
| Probe child | Environment, binary choice, network, output bounds. | The child inherits the full environment plus three overrides (`deep_probe.py:803-804`). Secrets are inherited but never printed (canary test). `PYTHONPATH` or the working directory (`python -m`) can substitute `aerollm_api` and forge its identity: same user only, filed as TD20. No network (guard). **QA-12**: stderr is unbounded per line. |
| Secrets | Canaries in `ANTHROPIC_API_KEY`, `HF_TOKEN`, `OPENAI_API_KEY`, and `PIP_INDEX_URL=https://qa-user:…@…`. Ran `deep probe` (text and JSON), `deep_verdict status` and `build-aerollm.sh status`, then grepped every output, `verdict.json`, `activity.jsonl` and every file under the state and lab folders. | 0 hits. The live wrapper starts its driver under `env -i` with an explicit allow-list (`deep-honest-win.sh:69-74`) and copies 4 non-secret keys from `.env` (`:66`), so no caller secret reaches it. Pre-existing: `AEROLLM_INDEX_URL` is echoed (xfail). |
| Personal data in the diff | `/Users/`, account name, e-mail, tokens (`sk-ant-`, `hf_`, `ghp_`, `AKIA`), hostnames across `62d5ebdf..HEAD`. | Code: none (only the test placeholder `/Users/someone/...`; `pypi.qukaizen.com` is the project's own index, already on main). Sprint folder: see Decision 2. |
| `build-aerollm.sh` injection | Every variable it reads. `eval` and `curl \| sh` searched (none). `shellcheck -S warning` clean. Hostile `ARAIL_AEROLLM_REPO` values: spaces, `'`, `"`, `$(touch …)`, backticks, a newline, a leading dash. Tar extraction with a `../` member and with a symlink-then-file member. | No command ran from a path (guards pass). bsdtar refuses `..` and extraction through a symlink; nothing landed outside. Verify-then-install order holds (read, and exercised by the truncated-tarball and bad-file guards): the import-target check, then the tarball sha256, then the MANIFEST sha256 of the `.so`, then the Mach-O magic, then the copy, then the loaded-file sha256. **QA-11**, N9. |
| Network egress | A socket guard (connect, connect_ex, create_connection, getaddrinfo) around `note_call`, `note_load_failure`, `record_probe`, `current`, `status_lines`, `health_detail`, both `explain()` modes, the registry overlay and a full Curator review through the portal. A `sitecustomize` guard in subprocesses for `deep_verdict status` and `build-aerollm.sh status`. | 0 attempts. SEC3 already covers the probe. |
| Terminal output | ESC and BEL in a runtime error and in a stored error. | **QA-5**. |
| Contract and vendored bytes | `git diff --quiet 62d5ebdf..HEAD -- src/arail/buddy_host src/dac_world`. | Byte-identical, `contract/buddy.host.v1.json` included. `tests/test_host_contract_vendor.py`: 18 passed and 1 skipped in the HEAD run, exactly as on base. |

## Performance

| Measure | base `62d5ebdf` | HEAD | What it measures | Bar and why | Outside the bar means | Fault lives in |
|---|---|---|---|---|---|---|
| `explain(foreground=True)`, warm, µs per call (median of 5 × 10,000) | 0.93 | 8.51 | The policy walk with the module loaded and the identity pinned. HEAD adds one `current()` read (stat-cached). | ARCHITECTURE: 10,000 calls under 1 s, so under 100 µs per call. A request is milliseconds, so single-digit µs is noise. HEAD costs 85 ms per 10,000. | Over 100 µs: the read is no longer cached (re-parsing every call), or the lock is contended. | `deep_verdict._read_store` cache and `current()`'s verdict cache |
| `explain()` cold, first call, ms | 0.01 | 7.42 | The first call in a process: it hashes the 22 MB native file once. | The reviewer measured 8 ms; the budget is under 150 ms once per process. | Over 150 ms: a slow disk, or a hash repeated per call (the cache key changed). | `deep_verdict._sha256_file` |
| `current(import_if_needed=False)`, static (wheel not imported), µs per call | none (no verdict on base) | 38.1 | The registry overlay's read in a process that has not imported the wheel (`find_spec`, a listing, stat). | Same 100 µs budget. It runs once per tier-1 entry per `/api/models/state` or `resolve`. | Over 100 µs: `find_spec` scanning a long `sys.path` | `deep_verdict.static_native` |
| `explain()` under 8 writer processes: p50 / p99 / max, µs | not applicable | 9.5 / 57.5 / 687 | Latency while the file changes about 4,000 times per second, so most reads re-parse. | p99 under 1 ms: a reader never takes the lock, so cross-process writers cannot block it. | p99 over 1 ms: reads wait on a lock (TD17 is in-process only) | `_read_store` |
| Import `arail.router.backends`, ms (median of 7, fresh interpreter) | 35.2 | 38.3 | Module import cost. | Within about 5 ms: one more small block of code. | Much more: a heavy import was added at module level | `router/backends.py` imports |
| Import `arail.portal.world_routes`, ms | 115.1 | 133.4 | The same, for the Curator routes. | Under 50 ms more. +18 ms, because it now imports `arail.router.backends` at module level for `CLOSED_BY_ARAIL`. | Much more, or an `ImportError`: an import cycle (none: each module imports alone, by test) | `portal/world_routes.py:47` |
| Import `arail.portal.app`, ms | 689.5 | 670.6 | The portal's import. | Within noise (±5%): the app already imported the router. | A rise over 5%: a new import on the boot path | `portal/app.py` |

The persona rule says a regression over 5% needs a justification or a fix. `explain()` is 9 times slower in relative terms, but the absolute cost is +7.6 µs, against a 100 µs budget per call; that is the cost of the feature, reading the verdict, and it is justified. `world_routes`' +18 ms is paid once per process. **Within tolerance.**

`explain()` is not on the Chat path: no Chat handler calls it. The verdict is read per request by the registry overlay (`/api/models/state`, and `resolve`/`peek` of tier-1-bound profiles), by `/api/research/status`, and by each agent call. It is cached by `(mtime_ns, size, inode)` and re-parsed only after a write. Cost per Chat message: 0 from `explain()`. A registry overlay read, if the message resolves tier 1, costs 8.5 µs pinned or 38 µs static.

## Regression checks

| Check | Result |
|---|---|
| `git diff --check 62d5ebdf..HEAD` | clean |
| File modes | `arailctl`, `scripts/build-aerollm.sh`, `deep-honest-win.sh`, `deep_honest_win.py`, `setup.sh`, `upgrade.sh`: all 100755 in git |
| Stray or generated files in the diff (`lab/`, `logs/`, `__pycache__`, `.env`, `*.so`, `*.pyc`, `egg-info`, `.DS_Store`, binaries) | none |
| `git diff --stat` surprises | none beyond BUILD_LOG's list: 60 files, +13,513/−201 |
| Frozen names, count per modified file (`aerollm`, `aerollm_api`, `AERO_`, `AEROLLM_`, `tier1-aerollm`, `AeroLLMBackend`, `build-aerollm.sh`, `backend="aerollm"`) | Against `62d5ebdf`: 25 modified files checked, **0 drops**. Against `0f2f8d25`: one drop, `build-aerollm.sh` `aerollm_api` 38 → 37, the deliberate deletion BUILD_LOG discloses. No machine-read name was renamed. |
| Import hygiene | `arail.deep_verdict`, `arail.deep_probe`, `arail.agents.deep_policy`, `arail.router.backends`, `arail.portal.world_routes`, `arail.portal.app` each import alone in a fresh interpreter. `arail.router.backends` does not pull in `arail.portal`. `arail.deep_verdict` adds only stdlib modules, as its docstring promises (tests). |
| Packaging | `[tool.setuptools.packages.find] where = ["src"]`, no MANIFEST.in. A wheel built offline from a `git archive` of HEAD in scratch ships `deep_verdict.py`, `deep_probe.py`, `deep_probe_prompts.py`, both CSS files and both templates. `import arail.deep_probe` works from the unzipped wheel, and `state_dir()` falls back to `DATA_DIR/deep` outside a checkout. Pre-existing: `static/js/*` is not shipped (N6). |
| `docs/cli.md` commands the sprint added or changed | Run as written, with the fake runtime or scratch state: `deep probe` exits 0 (Q1, the happy-path test), 1 (Q2, live W5), 2 (`--bogus`), 5 (no wheel, missing model dir, read-only lab) and 130 (Q15). `--help` exits 0. `--force` skips the memory check only (Q9). `build-aerollm.sh auto` with `AEROLLM_CHANNEL=bogus` exits 2, and an unknown mode exits 2. `status` exits 0. **Exception: QA-13** (through `arailctl` before setup, `probe` exits 1). `deep rebuild`, `deep install` and `deep update` were not run, as the rules require; their dispatch is pinned by W7 and by the harness tests. |

## Test inventory (the 89 QA tests)

Status: **RED** = fails today, an open finding. **XFAIL** = known or pre-existing, strict. **PASS** = a guard.

| # | Test | Category | Covers | Status |
|---|---|---|---|---|
| 1-3 | `verdict_store::test_qa1_*` (3) | Security | Deeply nested store: read, write, repair | RED (QA-1) |
| 4-11 | `verdict_store::test_qa2_*` (8) | Security | A malformed failure entry: read; write and probe | RED (QA-2) |
| 12-15 | `verdict_store::test_qa3_*` (4) | Setup | Future, NaN and Infinity stamps; the clock stepped back, end to end | RED (QA-3) |
| 16 | `verdict_store::test_qa4_a_symlinked_state_folder_is_not_written_through` | Security | A symlinked state folder | RED (QA-4) |
| 17-18 | `verdict_store::test_qa5_*` (2) | Security | Control characters in `deep status` and `deep probe` | RED (QA-5) |
| 19-23 | `verdict_store::test_qa_guard_*` (5) | Security | Non-UTF-8, a path-like key, symlinks at the lock and the temp name, 0700/0600 after every write path | PASS |
| 24 | `buddy::test_qa_sixty_agent_ticks_while_failing_make_no_deep_call_and_say_nothing_new` | Buddy | 60 ticks across Buddy, the debt advisor and the consolidation analyzer: 0 deep calls, 0 new activity lines, every fast trace stamped, 1 transition line | PASS |
| 25 | `buddy::test_qa_buddys_voice_falls_back_to_the_fact_when_both_models_fail` | Buddy | Buddy says the plain fact; nothing invented | PASS |
| 26 | `buddy::test_qa6_*` | Buddy | An unsaved failure versus a passing probe | RED (QA-6) |
| 27 | `buddy::test_qa7_*` | Buddy | Tier-1 dot versus `explain()` after a clear | RED (QA-7) |
| 28 | `buddy::test_qa_guard_the_failing_side_of_the_cycle_agrees_everywhere_in_a_second_process` | Buddy | A second process reads failing everywhere, with no generation | PASS |
| 29-30 | `buddy::test_qa_eject_mid_flight_*` (2) | Buddy | Eject with Chat running and Buddy and Curator in flight, or queued and cancelled after the 10 s wait: nothing filed, no thread left, no deadlock, true messages | PASS |
| 31 | `buddy::test_qa_td10_*` | Buddy | A closed call stamped `deep_failing` | XFAIL (TD10) |
| 32-34 | `buddy::test_qa_a_review_that_loses_any_judge_call_is_partial[…]` (3) | Buddy | First call only, last call only, sometimes | PASS |
| 35 | `buddy::test_qa_a_grow_whose_last_call_fails_changes_no_file_of_the_world` | Buddy | Only the last call fails: not applied, World files byte-identical, seal valid | PASS |
| 36-37 | `buddy::test_qa_preexisting_*` (2) | Buddy | A cancelled review task; a cancelled grow | XFAIL (pre-existing) |
| 38 | `buddy::test_qa_guard_no_curator_state_stays_running_after_any_failure_shape` | Buddy | Always failing, a panic, a closed call, an empty answer, CancelledError: every pass ends in a terminal state | PASS |
| 39 | `setup::test_qa8_*` | Setup | The F7 sentence over a source build | RED (QA-8) |
| 40 | `setup::test_qa_guard_a_non_apple_host_is_refused_before_any_file_is_read` | Setup | A faked Linux/x86_64 `uname`: `bundle` and `auto` | PASS |
| 41-43 | `setup::test_qa_guard_a_bad_bundle_file_*` (3) | Setup | A directory, an empty file, an unreadable file: the previous install survives, no temp left | PASS |
| 44 | `setup::test_qa_guard_a_truncated_tarball_*` | Setup | A pinned but truncated tarball | PASS |
| 45-46 | `setup::test_qa_guard_a_tarball_member_cannot_write_outside_*` (2) | Setup | `../` and symlink-then-file members | PASS |
| 47 | `setup::test_qa9_td14_*` | Setup | Ctrl-C during verification | XFAIL (TD14) |
| 48-52 | `setup::test_qa_guard_a_checkout_at_a_hostile_path_*` (5) | Setup | Spaces, quotes, `$()`, backticks | PASS |
| 53-54 | `setup::test_qa11_*` (2) | Setup | A backslash or newline in the path | RED (QA-11) |
| 55-57 | `setup::test_qa_guard_no_usable_checkout_*` (3) | Setup | Unset, missing, a file: the true message, nothing built | PASS |
| 58 | `setup::test_qa_guard_a_relative_checkout_path_with_a_leading_dash_*` | Setup | Non-zero exit, nothing installed | PASS |
| 59 | `setup::test_qa_guard_a_checkout_with_both_crates_*` | Setup | Builds the pre-rename crate | PASS |
| 60 | `setup::test_qa_guard_a_read_only_site_packages_*` | Setup | Refused before cargo | PASS |
| 61-62 | `setup::test_qa_guard_the_build_marker_*` (2) | Setup | A detached HEAD; git unusable (commit `unknown`, dirty `null`) | PASS |
| 63 | `setup::test_qa_preexisting_deep_status_runs_without_HOME` | Setup | HOME unset | XFAIL (pre-existing) |
| 64 | `setup::test_qa_guard_an_unimportable_wheel_*` | Setup | Exit 1, a load failure recorded, the ImportError named | PASS |
| 65 | `setup::test_qa_guard_the_probe_runs_without_HOME` | Setup | The probe without HOME | PASS |
| 66 | `setup::test_qa_guard_a_read_only_lab_*` | Setup | Exit 5, says why, nothing created | PASS |
| 67-70 | `setup::test_qa_guard_a_passing_probe_repairs_a_store_*` (4) | Setup | A directory, mode 000, another schema, a symlink | PASS |
| 71 | `setup::test_qa_guard_a_replaced_binary_starts_untested_*` | Setup | A new sha256 means "never tested"; `explain` ok | PASS |
| 72 | `setup::test_qa13_*` | Setup | Exit code before setup | RED (QA-13) |
| 73 | `setup::test_qa_guard_deep_status_runs_under_the_system_python_*` | Setup | macOS `/usr/bin/python3` 3.9 | PASS |
| 74 | `setup::test_qa12_*` | Security | stderr bound | RED (QA-12) |
| 75-76 | `setup::test_qa10_td18_*` (2) | Setup | SIGTERM, SIGHUP | XFAIL (TD18) |
| 77 | `setup::test_qa_guard_no_user_facing_text_sends_*` | Setup | No unqualified `deep rebuild` and no QueueLLM clone hint anywhere user-facing | PASS |
| 78-79 | `egress_happy::test_qa_guard_*network*` (2) | Security | In-process and subprocess socket guards | PASS |
| 80 | `egress_happy::test_qa_guard_canary_secrets_*` | Security | Four canaries, every output and file | PASS |
| 81 | `egress_happy::test_qa_preexisting_*credentials*` | Security | `AEROLLM_INDEX_URL` echo | XFAIL (pre-existing) |
| 82-87 | `egress_happy::test_qa_guard_each_touched_module_imports_alone_*` (6) | Regression | Fresh-interpreter imports | PASS |
| 88 | `egress_happy::test_qa_guard_the_backend_does_not_pull_in_the_portal_*` | Regression | No cycle; the verdict is stdlib-only | PASS |
| 89 | `egress_happy::test_qa_guard_happy_path_*` | Happy | Pass, then a failure in World `ai`, then a re-probe, across 3 processes: probe, status, `explain`, research block | PASS |

*Teaching for the counts in this section (89 tests: 25 RED, 8 XFAIL, 56 PASS):* Measures: QA tests by state. Bar: every RED is a finding listed above, and every XFAIL names its debt. Outside: a RED with no finding would be a broken test; an XFAIL that turns XPASS means the debt was paid and the marker should go. Fault lives in: the finding each test names. The four files run in 45 s.

## Coverage delta

Lines covered before: not measured. Lines covered after: not measured. The pinned venv has no coverage tool, and the rules forbid installing one into it. *Teaching:* a coverage number would measure lines executed by the suite. The usual bar is no drop on changed lines. Without it, my evidence of reach is the by-id comparison, the 89 targeted tests and the live run. The gap lives in this QA environment, not the product.

## What I could not test, and why

- **A real Rust panic through PyO3, and a real SIGABRT of the real wheel.** Forbidden here: a native abort writes a crash report. The `BaseException` stand-ins cover the code paths.
- **A passing real build.** None exists (`DAY1.json`). The clear path was tested on the fake runtime only. That is where QA-3, QA-6 and QA-7 live, and why they matter before TD5's sprint.
- **A real disk-full** (TD14). I reasoned about the order of writes and reproduced the interrupted-verification half with Ctrl-C.
- **Linux CI** (`flock`, `ps`, GNU tar). Not available. The fake runtime and harness tests are written to run there, but I ran them only on macOS.
- **`deep rebuild`, `deep install`, `deep update` for real.** Forbidden. The harness tests drive the same script against a scratch venv.
- **Starlette's `httpx2` TestClient** (TD23) and the 151 environment-bound reds: the pinned venv was required, so those tests stay unproven in this run.

## Notes for the next QA pass

- The store's threat model stops at "the JSON parses". Validate each entry and catch every parser error (QA-1, QA-2); add a fuzz test.
- Time in the store is wall-clock. Any rule of the form "a later X clears Y" needs a monotonic or count-based guard (QA-3, TD16).
- In-process memory (`_P.mem`) and registry state are two more places where "only a probe clears it" is not true. Test the clear path across every holder of state (QA-6, QA-7).
- CLI exit codes are a contract once documented. Test them through `arailctl`, not only through `python -m` (QA-13).
- The live check needs the operator's Mac. Its dry-run tests are thorough, but its teaching text has drifted for W5 (N8).
- Suite hygiene: a full run leaves other tests' `aerollm-rt` worker threads alive. One way that happens (read, not traced): `tests/fakes/fake_deep_runtime.py` `FakeDeep.backend()` resets `AeroLLMBackend._shared` without closing what it held. Any test that counts threads by name will be order-dependent until that closes them.

## Files

- This report: `sprints/2026-10-29-arail-deep-honest/TEST_REPORT.md`
- QA tests: `tests/test_qa_deep_honest_verdict_store.py` (23, `532a11da`), `tests/test_qa_deep_honest_buddy.py` (15, `2beed41f`), `tests/test_qa_deep_honest_setup.py` (39, `e00babb6`), `tests/test_qa_deep_honest_egress_happy.py` (12, `caa0515c`). Not pushed.
- No product code, `SPRINT.md`, `REVIEW.md`, `BUILD_LOG.md` or `LIVE_RUN.txt` was edited. The scratch worktree, the venv clone and every scratch output were removed after the run, as were the git-ignored files my suite runs wrote into the worktree (`lab/`, `logs/`, `__pycache__`). I kept `src/arail.egg-info/` (it belongs to the pinned venv). I also left five ignored files under `lab/pkb/skills/` that were born on 2026-10-29, before this pass; they are not mine to delete. Outside the repo I removed what my runs left in `/private/tmp`: three pytest temp folders, four `arail-pytest-env-*` folders, and the five KMP files of N12.
