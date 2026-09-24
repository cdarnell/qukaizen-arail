# Test report: Model Forge (`local` profile, Gate A = stub provider)

**Date:** 2026-09-24. QA ran from the evening of 2026-09-23 into the morning of 2026-09-24.
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) "Review loop 2" at `d56bc9a9`. The review is [REVIEW.md](./REVIEW.md) round 3 (`1d41e455`, WEAK_PASS).
**QA commits:** `479b9275..90fa766d` (6 commits). They touch tests and `sprints/BACKLOG.md` only; no production code changed.
**Verdict:** **FAIL**

## Why FAIL, in brief

The Nucleus pipeline is in the shape the architect described:
- Gate A's signed card is self-consistent.
- The R1–R3 fixes hold.
- Every security finding QA added is LOW.

It fails on the wider test suite. The claim that "the pre-existing four failures … must remain the only failures" does not hold. This sprint makes **20 tests fail** that pass at the pre-sprint merge-base (`236504ca`). Two fail deterministically, even alone:
- `test_tier_route_guards.py::test_minimalist_404s_on_maximus_routes` still expects `/build` to 404 on minimalist. That route is now a 308 to `/forge`.
- `test_cli_verbs.py::test_verbs_driver_scenarios` fails on F33: `docs/cli.md` doesn't list the new `nucleus` verb.

The other 18 are **order-dependent**: 17 in both full HEAD runs, plus 1 in one run only. They pass in isolation and fail in the full run. QA isolated the trigger to the builder's `tests/nucleus` suite running before them; the QA test file is not involved.

All of these are builder fixes. None needs a redesign. See **Failures**.

The builder's "four pre-existing failures" number came from a hand-picked subset of test files. The full `tests/` suite has never been run for this sprint before now. At the merge-base the full suite already has 41–44 failures, and none of them is attributable to this sprint.

---

## Counts (exact)

| Scope | Command | Result |
|---|---|---|
| Baseline (before QA, `bd1855ac`) | `.venv/bin/pytest tests/nucleus tests/portal/test_forge_viewer.py -q` | 368 passed, 2 skipped |
| Nucleus scope, with the real `qkz` | same, plus `NUCLEUS_QKZ_BIN=…/qukaizen-nucleus/qkz/target/release/qkz` | **460 passed, 1 skipped (`requires_mlx`), 14 xfailed** |
| Nucleus scope, no `qkz` | same command, `NUCLEUS_QKZ_BIN` unset | **457 passed, 4 skipped, 14 xfailed** |
| CI marker set (`nucleus-tests.yml`) | `tests/nucleus tests/portal/test_forge_viewer.py tests/portal/test_models_api.py tests/test_qa6_security_gate.py -m "not requires_mlx and not requires_aerollm and not requires_kernel and not requires_qkz_bin"` | 508 passed, 4 deselected, 15 xfailed, **1 failed** (`test_models_api::test_health_refresh_probes_without_constructing_aerollm`, pre-existing, also fails at the merge-base) |
| Full `tests/` at the merge-base `236504ca` (fresh tree) | `pytest tests -q` | **44 failed**, 5763 passed, 5 skipped, 7 xfailed |
| Full `tests/` at HEAD (fresh tree, run concurrently with the above) | `pytest tests -q` | **57 failed**, 6201 passed, 9 skipped, 21 xfailed |
| Full `tests/` at HEAD (this worktree, earlier run) | `pytest tests -q` | 56 failed, 6202 passed, 9 skipped, 21 xfailed |

About the 14 xfails:
- Every one is `strict=True` and asserts the *correct* behaviour of a ticketed defect.
- I checked with `--runxfail`: each fails on its intended assertion, not incidentally.
- Each one flips to a hard failure the moment its fix lands.

**Net new tests:** +105 items in the nucleus scope. That is 102 in `tests/nucleus/test_qa_round3.py`, plus a net +3 across `test_preflight.py` (+2/−1) and `test_certify.py` (+2).

**Writes to the checkout:**
- The nucleus scope run in isolation, with a sentinel touched first, gives `find lab models -newer <sentinel>` = **0**, and `git status` shows only the pre-existing untracked `.venv` and `logs/`.
- The *wider* suite is different. Pre-existing, non-nucleus tests write into the checkout's `lab/`: `lab/instances/finance/**`, `lab/data/egress.jsonl`, `lab/data/model_registry.json`, `lab/data/hardware.json`, `lab/.opencode/opencode.json`, LanceDB versions, and a Buddy dream file.
- One of them also leaked an orphaned `opencode serve --port 4096`. QA stopped it and removed every file those runs created (by birth time). QA left the pre-existing LanceDB tables alone.
- This sprint did not introduce any of this; see Notes.

---

## Required-before-merge items from REVIEW round 3 (done by QA)

| Item | Commit | Proof |
|---|---|---|
| **R3-A1**: the R3 proving test must reach Phase C and fail with the fix reverted | `479b9275` | `test_buddy_protected_at_phase_c_in_every_config_form` sets `student_model=None` and asserts `phase == "C"`. It covers 7 real-dir config forms and checks the invariant by content identity. A control test (`test_phase_c_control_unprotected_judge_is_the_drop`) shows that the judge *is* the drop when no Buddy is configured. **Mutation check:** I applied `git revert --no-commit 51ac4728` (src hunk only; test file kept at HEAD), ran the test, then ran `git revert --abort`. It failed on exactly the 5 absolute-path forms (absolute path, trailing slash, symlink, copy outside the models dir, `QUEUELLM_MODEL` absolute). The 2 bare-name forms still passed, matching the architect's table. The tree was clean after the abort. |
| **R3-A2**: the card-alone recompute passes `formula_id` and pins LC 0.8676 | `04a6dd51` | A shared `assert_card_recomputes_from_itself()` recomputes formula selection, formula string, composite, and `decide(…, formula_id=…)`. It runs on three cards: the unit card (KNOWN_ISSUE), a **new COMPATIBLE-by-cap unit card**, and the **Gate A card**. On the cap card, dropping `formula_id` gives CERTIFIED, which proves the helper depends on it. `LC_GOLDEN = 0.8676` and ci95 `[0.5, 1.0]` are pinned exactly. **Mutation check:** `test_broken_position_unswap_moves_the_lc_golden` swaps in a non-un-swapping `randomize_and_judge`. LC becomes 0.0 and does not match the golden. |
| **R3-A10**: refresh the BACKLOG umbrella | `1c1aa28b`, `10901304` | Stale lines were dropped (tokenizer parity, `pipeline_hash`, `training_hash`, LC unwired) and the MLX paragraph deduplicated. I added §9 item 10's three parts, R3-A3/A4/A5, round-2 A4/A5/A6/A7/A9, residency-unmeasured-permits-CERTIFIED, the spike harness, contamination scope, open eval on cert items, and QA findings F-QA-3..7. Every item names the test that tracks it. |

---

## Test inventory

Category weights follow ARCHITECTURE §7: Security 35 / Regression 25 / Happy 25 / Setup 15. The QA file's 102 new items break down as Security 67, Happy 17, Regression 10, Setup 8.

Security is deliberately overweight: the round-3 target list is security-first. Regression's weight was spent on the full-suite differential rather than on new test count: two paired full runs, isolation reruns, and a prefix bisection.

| # | Test (file `tests/nucleus/test_qa_round3.py` unless noted) | Category | Covers | Status |
|---|---|---|---|---|
| 1 | `test_preflight.py::test_buddy_protected_at_phase_c_in_every_config_form` (+ control) | Security | R3 on real dirs in 7 forms; phase == C; identity invariant | pass (fails with R3 reverted) |
| 2 | `test_laundering_a_forged_context_flag_refused_exit_3_no_card_no_ledger` | Security | Variant (a) via CLI: exit 3, names all 5 phases, no card, no ledger | pass |
| 3 | `test_laundering_reverse_stub_true_over_real_looking_stamps_refused` | Security | `stub: true` over real-looking stamps | pass |
| 4 | `test_certify_refuses_stub_false_while_b7_stands` | Security | Variant (b): context plus all stamps forged | **xfail**, R3-A5 |
| 5 | `test_laundering_b_current_behaviour_signs_and_ledgers_a_real_looking_card` | Security | Characterises (b): exit 0, `runtime: queuellm`, lab key, ledger row | pass (delete when #4 flips) |
| 6 | `test_certify_refuses_when_phase_stamps_deleted` | Security | Variant (c): PA/PA2/PB/PC stamps deleted, fuse forged | **xfail**, R3-A5 |
| 7 | `test_certify_missing_fuse_output_refuses_exit_3_no_card` | Security | Missing `fuse.json` | pass |
| 8 | `test_certify_missing_metrics_json_fails_closed_without_a_card` | Security/grace | Missing `metrics.json`: exit 1, no card, no traceback | pass (F-QA-7) |
| 9 | `test_certify_context_missing_on_disk_fails_closed` | Security | Missing `context.json` | pass |
| 10 | `test_certify_rejects_non_canonical_build_ids_exit_3` [5 cases] | Security | `../../etc`, `kernel/../../x`, uppercase, `/..` | pass |
| 11 | `test_certify_without_build_id_…`, `test_certify_unknown_but_well_formed_build_id_…` | Setup | Usage and unknown-run messages | pass (F-QA-7 pins exit 1) |
| 12 | `test_validators_reject_trailing_newline` | Security | `re.match` + `$` accepts a trailing `\n` in 5 validators | **xfail**, F-QA-3 |
| 13 | `test_preflight_protects_aerollm_model_when_queuellm_model_differs` (+ characterisation) | Security | R3-A4 env divergence | **xfail**, R3-A4 |
| 14 | `test_preflight_protects_backend_default_when_env_unset` | Security | R3-A4 unset env on maximus | **xfail**, R3-A4 |
| 15 | `test_aerollm_model_alone_…`, `test_both_env_vars_…`, `test_empty_string_env_…`, `test_relative_or_tilde_…` [3], `test_buddy_path_pointing_at_missing_dir_…` | Security | Edge configs never crash; protected where resolvable | pass |
| 16 | `test_phase_c_property_buddy_never_dropped_over_200_combos` | Security | T-PRE-3 only sized a teacher (Phase A). This reaches C ≥ 50/200 times; the drop is always the largest non-protected candidate | pass |
| 17 | `test_plan_refusal_text_is_plain_and_never_names_buddy_in_every_form` (+ control, writes-nothing) | Setup | `nucleus plan` wording on a clean lab, 5 Buddy forms | pass |
| 18 | `test_plan_output_never_advises_dropping_buddy_under_env_mismatch` | Setup/Security | User-visible R3-A4 text | **xfail**, R3-A4 |
| 19 | `test_untampered_trusted_card_fast_verify_exit_0_and_badge_trusted` | Security | Baseline for tamper tests | pass |
| 20 | `test_cli_verify_non_fast_exits_3_even_when_everything_checkable_matches` | Setup | A9: `chain: not_checked` gives exit 3 | pass (pinned) |
| 21 | `test_hand_edited_card_is_caught_by_verify_both_modes_and_badge` [8 tampers] | Security | Metric, open_ended, baselines, decision, composite, runtime, executable, extra key: `card_hash: mismatch`, exit 3 (fast and non-fast), badge `tampered` | pass |
| 22 | `test_transplanted_seal_from_another_card_is_caught` | Security | Valid trusted seal moved onto another card | pass |
| 23 | `test_edited_eval_config_lock_…`, `test_malformed_eval_config_lock_…` | Security | Non-fast catches it; fast and badge don't read the lock (by design) | pass |
| 24 | `test_editing_domain_eyeball_file_after_certify_does_not_change_verify` | Security | The sealed artifact is independent of the live domain file | pass |
| 25 | `test_edited_build_report_is_not_covered_by_the_seal` | Security | The report shows under a `trusted` badge after an edit | pass (pins F-QA-4) |
| 26 | `test_nucleus_verify_reads_the_card_seal_not_seal_json` | Security | A corrupted `seal.json` is unnoticed by `nucleus verify` | pass (pins F-QA-5) |
| 27 | `test_malformed_signature_hex_reports_invalid_exit_3` | Security | Handled correctly | pass |
| 28 | `test_malformed_public_key_hex_reports_invalid_exit_3` (+ current behaviour) | Security | Round-1 ASK: exit 1 instead of `invalid` | **xfail** |
| 29 | `test_stub_card_verify_exits_3_and_badge_is_stub`, `test_verify_usage_and_missing_card_exit_codes` | Setup | Exit codes 2/3; tamper outranks STUB | pass |
| 30 | `test_verify_shard_at_version_rejects_traversal` | Security | `../../x@y` escapes FORGE_ROOT | **xfail**, F-QA-6 |
| 31 | `test_lc_{equal_lengths,constant_delta,all_wins_equal_lengths,quasi_separation}_degenerate_*` | Happy | R3-A3 four probes; current outputs 0.5 / 0.5 / 0.5 / 1.0 (unreliable=False) | **4 × xfail**, R3-A3 |
| 32 | `test_lc_generic_…`, `test_lc_single_valid_item_…`, `test_lc_empty_input_…` | Happy | Non-degenerate controls | pass |
| 33 | `test_certify.py::test_card_composite_and_decision_recompute_from_the_card_alone`, `…_on_a_compatible_by_cap_card`, `test_e2e_gate_a.py` recompute | Happy | R3-A2 | pass |
| 34 | `test_certify.py::test_broken_position_unswap_moves_the_lc_golden` | Happy | Mutation sensitivity of the LC golden | pass |
| 35 | `test_eval_hash_invariant_to_build_id_version_and_signing_key`, `…_to_fidelity_target_and_top_n` | Regression | eval_hash is unchanged when non-yardstick inputs change | pass |
| 36 | `test_eval_hash_changes_when_judge_rubric_changes`, `…_contamination_params_change` | Regression | eval_hash changes when these yardstick inputs change | pass |
| 37 | `test_eval_config_lock_carries_no_path_build_id_host_or_metric` | Regression | The lock holds no tmp path, build id, hostname, `/Users/`, or metric values | pass |
| 38 | `test_eval_hash_follows_eyeball_file_edited_between_pc_and_certify` | Regression | Round-3 INFO: eyeball bytes read at certify, not PC | **xfail** |
| 39 | `test_real_qkz_verifies_certify_produced_stub_seal`, `…_lab_key_seal_and_rejects_tampering` [4 tampers] | Security | The real Rust `qkz isotope verify` accepts certify's actual `seal.json` and rejects signature, gate, run-id, and chain edits | pass (with `NUCLEUS_QKZ_BIN`) |
| 40 | `test_cli_build_with_gateway_profile_in_airgapped_…` [gateway, mixed], `…_in_hybrid_…` | Security | T-GW-1 at the CLI: exit 3, `AIRGAPPED_NOTICE` verbatim, 0 connects, egress.jsonl unchanged, no run dir | pass |
| 41 | `test_zero_egress_across_every_phase_and_certify_in_process` | Security | Round-1 T-EGR-1 scope ASK: a connect **and** DNS guard over PA/PA2/PB/fuse/PC/certify | pass |
| 42 | `test_nucleus_private_dirs_are_0700_…`, `test_signing_key_is_0600_and_its_bytes_never_leave_the_key_file`, `test_gateway_token_from_secrets_env_never_echoed` | Security | Dirs 0700; key 0600; raw/hex/b64 key never in any output; token from `secrets.env` never in repr, exception, logs, or egress | pass |
| 43 | `test_frozen_names_only_in_runtime_names_across_all_sprint_surfaces`, `test_no_env_writes_in_forge_surfaces` | Regression | T-RT-2 widened to `forge_api.py` and `forge.html` | pass |
| 44 | `test_build_retirement_leaves_no_live_route_nav_or_import`, `…_known_cosmetic_leftovers_…` | Regression | Only the 308 route; no nav, import, or module; pins the 2 reviewed leftovers | pass |
| 45 | `test_cli_plan_rejects_unsafe_domain_slugs_…` [8], `test_plan_intent_with_newlines_cannot_inject_domain_keys`, `test_domain_yaml_bomb_and_oversize_rejected`, `test_domain_python_yaml_tags_are_not_executed`, `test_plan_create_refuses_to_overwrite_…` | Security/Setup | Slug traversal, YAML injection via intent, billion-laughs, size cap, `!!python` tags | pass |
| 46 | `test_decide_unmeasured_residency_does_not_cap`, `test_decide_boundaries_under_v1_open` [5] | Happy | Decision boundaries; pins unmeasured → CERTIFIED on non-v1-open | pass |
| 47 | `test_first_build_without_a_cert_set_fails_at_pa2_after_phase_a` | Setup | Pins the undocumented `--new-cert-version` trap | pass |
| 48 | `test_contamination_exactly_one_percent_blocks`, `…_just_under_…`, `…_after_cutoff_is_a_temporal_leak` | Happy | Brief §7 boundary: 8/800 blocks | pass |
| 49 | `test_contamination_timestamp_on_cutoff_day_is_not_a_leak` | Happy | Round-1 ASK: string date compare | **xfail** |

---

## Failures

Failures in the product itself. These are wider-suite tests that fail at HEAD and pass at the merge-base under identical conditions: fresh trees, `.venv` symlinked, `PYTHONPATH` pinned to each tree's own `src/`, run side by side.

The paired run's HEAD-only set is 19: F1 plus the 18 in F3.

F2 fails in both trees in that run, but for different reasons:
- At the merge-base, the driver stops earlier on an environmental "doctor healthy" check.
- At HEAD, it gets past that and fails on F33.

Run alone at the merge-base, F2 passes.

| # | Test | Symptom | Minimal repro | Severity |
|---|---|---|---|---|
| F1 | `tests/test_tier_route_guards.py::test_minimalist_404s_on_maximus_routes` | `/build must 404 on minimalist, got 200` | `pytest tests/test_tier_route_guards.py::test_minimalist_404s_on_maximus_routes`. It fails alone, deterministically. `/build` is now a 308 to every-tier `/forge`. The test's maximus-route list (line 16) still includes `/build`, so T-REG-5 ("tier-gating tests updated") is incomplete. | **MEDIUM**: failing test; no product defect. Fix the test: `/build` must 308 to `/forge` on both tiers. |
| F2 | `tests/test_cli_verbs.py::test_verbs_driver_scenarios` | `FAIL: F33: docs/cli.md is missing these arailctl verbs: nucleus` | `pytest tests/test_cli_verbs.py`. `arailctl` gained `nucleus` (lines 264, 1284). `docs/cli.md`, the canonical CLI reference per repo CLAUDE.md, has 0 mentions. | **MEDIUM**: a setup/onboarding doc gap enforced by an existing test. |
| F3 | 18 order-dependent failures: 17 in both HEAD runs, plus `test_cli_qa_edge.py::test_q1_restart_…` in the paired run only. `test_r1_r3_chat_models.py` ×4; `test_aerollm_model_ready.py` ×3; `test_deep_default_and_tier.py` ×2; `test_model_ux_phase0_warmth_probe.py` ×2; `test_qa_model_ux_memory_and_eject_fidelity.py` ×2; `test_qa_provider_dropdown_paranoid.py` ×2; `test_b1_cloud_gallery_contract.py` ×1; `test_runtime_profile_api.py::test_post_emits_activity_event` ×1. | `/api/chat/models` drops most of its payload (`gallery missing keys: {'installed','runtime_counts','catalog'}`, "went to wrong branch, dropped: {deep, optional_backends, …}"). The deep-info and warmth probes misreport. The activity event is missing. | Reproduced in 2 independent full runs at HEAD; 0 at the merge-base. Each passes in isolation. `pytest <every file collected before test_r1_r3_chat_models.py> tests/test_r1_r3_chat_models.py` gives **4/4 fail**. The same list **minus `tests/nucleus/`** gives **0 fail**. Minus only `test_qa_round3.py`, still 4 fail. Non-nucleus prefix plus only `test_qa_round3.py`: 0 fail. So the trigger is the **builder's** `tests/nucleus` files together with later root-level suites. `tests/nucleus + tests/portal + victim` alone passes, and so does 5 minutes of idle time. Likely mechanism, **unconfirmed**: a module first imported inside a nucleus test, while `tests/nucleus/conftest.py` has pointed `arail.config.MODELS_DIR`/`DATA_DIR` and `arail.activity.LOG_FILE` at a tmp dir, captures those values at import time and keeps them after monkeypatch undo. | **MEDIUM**: test-isolation defect in the sprint's test suite. It makes the Chat, deep-runtime and activity suites unreliable in CI. No evidence of a product defect: the victims pass alone. |

No failure among the sprint's own tests. All 14 xfails are the intended, ticketed defects below.

### Pre-existing (not this sprint)

Merge-base and HEAD share 38 full-suite failures. All reproduce at `236504ca` with base `src/` pinned, including the environmental `test_cli_qa_edge::test_qa_edge_driver_scenarios` (QA-7 real-boot banner). The builder's four named pre-existing failures break down as:
- `test_models_api::test_health_refresh_probes_without_constructing_aerollm`: **confirmed** pre-existing (fails at both).
- The **three `test_docs_routes.py` tests: did not fail in any of my full runs**, at base or HEAD. They look dependent on the builder's local environment.

---

## Defects and tickets surfaced (severity, for routing)

| ID | Finding | Severity | Test | Home |
|---|---|---|---|---|
| R3-A5 | Stub laundering (b) and (c): forging `context.json` plus stamps (or deleting stamps) signs a **lab-key, trusted, ledgered** card claiming `runtime: queuellm` | LOW for Gate A (key owner only, no privilege boundary, per round 2); **Gate B blocker** | #4, #5, #6 | BACKLOG umbrella |
| R3-A4 | Preflight protects `QUEUELLM_MODEL` first, but the backend loads `AEROLLM_MODEL`. `nucleus plan` then prints "Drop: Qwen2.5-7B-Instruct-4bit … Protected, never evicted: Qwen2.5-3B-Instruct-4bit": Buddy's model, as advice. With both vars unset, the backend default is unprotected. | LOW (advisory text only); **fix before users migrate `.env` to `QUEUELLM_*`** | #13, #14, #18 | BACKLOG |
| R3-A3 | `open_lc_judge.score` returns 0.5 / 0.5 / 0.5 / 1.0 (unreliable=False) on the four degenerate probes | LOW now (unreachable); **Gate B blocker** | #31 | BACKLOG |
| INFO→ticket | eval_hash hashes eyeball bytes at certify time, not what PC judged | LOW | #38 | BACKLOG |
| Round-1 | Malformed `public_key_hex`: exit 1 "internal error", not `signature: invalid` (a malformed `signature_hex` is fine) | LOW | #28 | BACKLOG |
| Round-1 | Contamination: a train date on the cutoff day with a timestamp reads as a temporal leak | LOW (blocks certify falsely, fails closed) | #49 | BACKLOG |
| **F-QA-3** (new) | `$`-anchored `re.match` validators (build id, shard, semver, domain slug, `/forge`) accept a trailing `\n`. No traversal. | LOW | #12 | BACKLOG |
| **F-QA-4** (new) | `build-report.md` isn't covered by the seal; an edited report still renders under a `trusted` badge on `/forge` | LOW | #25 | BACKLOG |
| **F-QA-5** (new) | `nucleus verify` checks only the card's embedded seal, never `seal.json` (which `qkz` reads); the two can diverge unnoticed | LOW | #26 | BACKLOG |
| **F-QA-6** (new) | `verify <shard>@<ver>` doesn't validate either half, so `../../x@y` reads outside FORGE_ROOT (read-only) | LOW; fix before MCP tools pass model-chosen targets | #30 | BACKLOG |
| **F-QA-7** (new) | Unknown build id or missing `metrics.json`: exit 1 "internal error: [Errno 2] …" instead of a plain exit-3 refusal naming the fix | LOW (grace) | #8, #11 | BACKLOG |

---

## Security review

| Surface | Checked | Findings |
|---|---|---|
| Stub laundering | Seven variants: (a) forged flag; (b) forged flag plus all 5 stamps; (c) forged flag, deleted PA/PA2/PB/PC stamps, forged fuse; stub:true over real stamps; missing fuse.json, metrics.json, context.json. All through `cli.main(["certify", …])`, checking exit code, stderr, card presence under FORGE_ROOT, and the local ledger. | (a), reverse, and all missing-file cases fail closed. (b) and (c) sign with the lab key and ledger (R3-A5). |
| Buddy guard | Real on-disk model dirs; Phase C reached (asserted); 7 config forms; `QUEUELLM_MODEL`/`AEROLLM_MODEL` divergence; unset on maximus; empty, relative, tilde, and missing-dir values; 200-combo Phase-C property; `nucleus plan` stdout in 5 forms plus a control; the R3 revert mutation. | Correct in all 7 forms. R3-A4 divergence and unset are open. `PreflightRefusal`'s own assert is still name-based (checked by identity in the test instead). |
| Tamper / verify | 8 hand-edit kinds, a transplanted seal, eval-config.lock edit and corruption, eyeball edit, report edit, seal.json corruption, malformed hex; fast and non-fast verify exit codes; `/forge` badge. | card_hash catches every card edit; badge shows `tampered`. Report and `seal.json` are outside the seal (F-QA-4, F-QA-5). |
| Crypto | Ed25519 (`cryptography`); a real Rust `qkz isotope verify` accepts certify's actual `seal.json` (stub ephemeral and lab key) and rejects 4 tampers; 32-byte raw key 0600; 0700 dirs; key bytes (raw, hex, HEX, b64) absent from card, seal, report, lock, ledger, activity log, and CLI output. | No MD5/DES/ECB. `nucleus verify` doesn't compare `seal.json` to the card (F-QA-5). Key-owner check is still open (round 1). |
| File I/O | build_id traversal (5 forms); `verify <shard>@<ver>` traversal; domain slug traversal (8 forms) via the CLI; eyeball traversal, absolute and symlink (existing T-DOM-3); `plan` overwrite refusal. | F-QA-6 (verify), F-QA-3 (newline). Everything else is refused. |
| Deserialization | Domain YAML: 2 MB oversize, billion-laughs alias bomb, `!!python/object/apply` tag with a marker file; `plan` intent with an embedded newline injecting `teacher:`/`runtime: stub`/`shard:`/`refresh:`. | `safe_load` everywhere. The bomb and the tag are refused and the marker is never created. Injected keys are overridden by the template or rejected by the schema. |
| Network I/O | Socket `connect` plus `getaddrinfo` guard over every phase and certify; CLI `build` with `gateway`/`mixed` under airgapped (verbatim `AIRGAPPED_NOTICE`, 0 connects, egress.jsonl unchanged, no run dir) and under hybrid (sprint-2 refusal); gateway token read from `secrets.env` with DNS and connect blocked. | Zero egress. The token appears in none of repr, the exception, caplog, or egress.jsonl. |
| Frozen names | `git grep -niE "aerollm\|AERO_\|QUEUELLM_"` over `src/arail/nucleus`, `forge_api.py`, `forge.html` excluding `runtime_names.py` → 0 hits; no `os.environ[...]=`/`putenv` writes. | Clean. It is now a test (#43). |
| Dependencies | No new dependencies in the QA commits. `pytest-cov`/`coverage` were deliberately **not** installed, because the venv is shared (below). | n/a |

---

## Brief §7 acceptance criteria

| Criterion | Status at Gate A | Evidence |
|---|---|---|
| `build linux-kernel --profile local` completes on the M5, airgapped, 0 egress lines | **Not provable at Gate A.** Real builds are refused (B7). Stub analogue proven. | `test_e2e_gate_a.py` (egress absent); #41 zero-egress guard over all phases and certify |
| Buddy SLM RSS unchanged ±5 % across phases | **Not provable at Gate A.** The sampler never runs; residency is always `unmeasured`. | Classifier unit tests T-RES-1..3; #46 pins that `unmeasured` doesn't cap |
| Preflight refuses an over-budget config and names the offending model | **Proven, with R3-A4 gaps** | T-PRE-1, #1, #16, #17; xfails #13, #14, #18 |
| Card validates against `dna-card-v2`; eval_hash changes iff harness/prompt/scoring/decoding/cert-set change | **Proven**, except the eyeball-timing gap | Gate A `validate_card`; T-HASH-1..3; `test_certify.py` eval_hash tests; #35–#37; xfail #38 |
| Contamination blocks certification at ≥ 1 % | **Proven** (exact boundary) | T-CONT-1..5, `test_certify_refuses_on_contamination`, #48; xfail #49 (false positive, fails closed) |
| Cert set byte-identical before/after Arbitrage | **Proven** | T-CERT-1..3; Gate A sha plus byte compare |
| F1 reported; `accuracy` absent from the headline | **Proven** | T-CLOSED-1,2 |
| Judge never the teacher; position randomization and LC on by default | **Proven for the stub path.** LC math is degenerate in edge cases. | T-JUDGE-1..4, `test_score_open_lc_refuses_when_judge_identity_matches_teacher`, R3-A2 golden plus the un-swap mutation (#33, #34); xfails #31 |
| Gateway passes contract tests vs the mock; `gateway` in airgapped refused with the standard banner | **Proven** | T-GW-1..5; #40 at the CLI |
| `docs/CERTIFIED_MODELS.md` gains one row from certify with the correct status | **Proven at unit level only.** Gate A stub cards are never ledgered by design. | T-LEDGER-1..4; no real card exists at Gate A |
| `build-report.md` includes the 10 eyeball prompts with outputs | **Proven** | T-REPORT-1; `test_build_report_carries_real_eyeball_outputs`; Gate A 10 sections |
| From `/forge`, Buddy walks a first-time user to a running build without touching `.env` | **Not in scope.** Item 4 (Buddy skill) was deferred; `/forge` is read-only. | Decisions log 2026-09-23 |
| With the deep runtime installed, every local inference call routes through QueueLLM; no Ollama/AirLLM unless the import fails; card `runtime: queuellm` | **Provider selection proven (unit); card field not provable at Gate A** (stub cards read `stub`) | T-PROV-1..3 |

---

## Performance

N/A for Gate A. The stub pipeline is not a hot path and no production code changed, so no BENCHMARK.md. #17 ran five `nucleus plan` preflights in 0.42 s in total, against a 5 s target. Capacity and the Buddy reserve probe were stubbed; the real loopback Ollama probe has a 1 s timeout. The ARCHITECTURE §7 M5 targets are **Gate B** items: contamination on 1 M lines < 60 s / < 1 GB (known to miss by design today, O(train) memory), plus B0–B3.

## Coverage delta

`coverage.py` is not installed in the shared venv, and QA did not install it (see Notes). Instead, a stdlib `sys.settrace` pytest plugin (session scratchpad, not committed) measured executed lines. The denominator is every line in the file's code objects (`co_lines()`), not coverage.py's definition. **In-process only**: the Gate A subprocess CLI and worker are not traced.

| File | Before QA (`bd1855ac` tests) | After QA (`90fa766d` tests) |
|---|---|---|
| `nucleus/build.py` | 350/393 (89.1 %) | 350/393 (89.1 %) |
| `nucleus/certify.py` | 250/293 (85.3 %) | 264/293 (90.1 %) |
| `nucleus/preflight.py` | 257/292 (88.0 %) | 258/292 (88.4 %) |
| `nucleus/models.py` | 159/171 (93.0 %) | 160/171 (93.6 %) |
| `nucleus/evals/composite.py` | 82/87 (94.3 %) | 82/87 (94.3 %) |
| **Total** | **1098/1236 (88.8 %)** | **1114/1236 (90.1 %)** |

Notable lines still unexecuted:
- `certify.py:309-319`: the real tokenizer-parity path. It only runs with a concrete teacher.
- `certify.py:187-188`: the `base_closed is None` branch.
- `preflight.py:196-207`: `_declared_buddy_reserve` walking the deep model dir. Buddy's declared reserve is never exercised by any test.
- `build.py:625-639`: the real MLX cycle stub.

---

## Gate B readiness list (in order; each blocks item 10)

1. **Fix F1–F3** (merge-gating): update `test_tier_route_guards` for the `/build` retirement, add `nucleus` to `docs/cli.md`, and fix the `tests/nucleus` isolation leak so the full suite is order-independent.
2. **R3-A5:** refuse missing phase stamps, and refuse `stub: false` while B7 stands. Flip xfails #4 and #6, and delete #5.
3. **R3-A4:** protect both env values plus the backend default. Check the invariant by identity inside `PreflightRefusal`. Flip #13, #14, #18.
4. **R3-A3:** intercept-only fallback plus regularisation or an `unreliable` flag in `open_lc_judge.score`. Flip #31.
5. **Real-runtime wiring** (BACKLOG umbrella): teacher auto-select, logprob probe, MLX training cycle, residency sampler (and decide whether `unmeasured` caps), real judge, patch generation and executable checks, and retiring `composite/v1-open`.
6. **Hash precision:** A4 (`pipeline_hash` hyperparams, `training_hash` adapter and streaming), A5 (decoding from phases), eyeball bytes hashed at PC (flip #38), runtime provenance hashing the `.so`.
7. **Contamination** on prompts plus teacher outputs with O(cert) memory; fix the date compare (flip #49); meet the 1 M-line target.
8. **Spike harness** real provider path; B3 must not "pass" on `unmeasured`.
9. **Seal hygiene:** key-owner check, malformed `public_key_hex` (flip #28), F-QA-4/5/6.
10. **Docs:** `--new-cert-version` plus a preflight "cert set present" row (#47), A9 `verify` exit semantics (#20), `/forge` `unchecked` badge without `cryptography`.
11. **Environment:** restore the shared venv's editable install (Notes) before anyone runs the M5 spike from the main checkout.

---

## Notes for the next QA pass

- **The shared venv's editable install points at this worktree.** `qukaizen-arail/.venv/lib/python3.11/site-packages/__editable__.arail-1.0.0.pth` contains `/Users/netsushi/ProJects/arail-nucleus-wt/src` (written 2026-09-23 19:33). That venv is shared by the main checkout (`qukaizen/arail-ingress-spine`, uncommitted work) and about 14 other worktrees. Under pytest, `tests/conftest.py` pins each tree's own `src/`, so test results were valid. But any plain `python -m arail…` or `./arailctl` from those trees, and any subprocess-driven test there, currently runs **this sprint's code**. After merge, re-run `pip install -e /Users/netsushi/ProJects/qukaizen-arail` from the main checkout's venv. QA did not touch it.
- **Pre-existing wider-suite hygiene.** Non-nucleus tests write into the checkout's `lab/` (instances, egress.jsonl, model registry, LanceDB versions, Buddy dreams), and one leaks an orphaned `opencode serve --port 4096` with the checkout as cwd. Whoever runs the full suite on a user's machine pollutes that user's lab. Worth a sprint of its own; it is not this sprint's regression.
- **"Pre-existing failures" claims need the full suite and a paired base run.** The builder's four came from a curated file list; three of them don't reproduce, and 19 real new failures were outside that list. Run `pytest tests` at the merge-base and at HEAD in fresh trees, side by side, and diff.
- **Under-tested areas:** `_declared_buddy_reserve` (0 coverage); the real tokenizer-parity path in certify; `status`/`list` verbs; `/forge` detail rendering of a tampered report; resume after a crash mid-PC.
- **Commit attribution:** the orchestrator asked for `Co-Authored-By: Claude Fable 5.1`. QA's commits carry this session's actual model attribution instead (`Claude Opus 5.5 (1M context)`), per the session's attribution instruction.
