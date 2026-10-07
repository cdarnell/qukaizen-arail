# Test report: QueueLLM display rename + honest local cost

**Date:** 2026-10-07
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) at `14d2a02b` (review `c586e41d`)
**Verdict:** FAIL

The cost fix is correct and survives every case I added. That covers stream and non-stream, the deep, runtime and router branches, local, cloud and unpriced backends, legacy `costs.json`, unattributed records, odd backend spellings and prompt-text leakage. The frozen identifiers and `AEROLLM_MODEL` compatibility hold.

The verdict is FAIL because two real tests fail on user-visible "AeroLLM" that the build missed. Both are small:

1. **Medium.** On a lab that existed before the upgrade, the installed skill packs keep "Optimize AeroLLM". That is exactly the owner's lab. The text shows in the Skills tab and the Forge picker, and it is injected into the chat system prompt. This is the F7 registry-note problem again, on a surface nobody checked.
2. **Low.** Two inventory rows that ARCHITECTURE.md lists explicitly (`lab/tools/benchmark_models.py:340`, `:362`) were never renamed. The guard does not scan `lab/tools/`.

## Test inventory

New files: `tests/test_queuellm_rename_qa.py` (34 tests + 1 strict xfail) and `tests/registry/test_tier1_note_reseed_qa.py` (3). Run on base `5f775f1c`, 32 of the 34 non-xfail tests in the first file fail. That shows they are not vacuous. The 2 that pass on base are invariants meant to hold on both sides (#4, #24).

| # | Test | Category | Covers | Status |
|---|---|---|---|---|
| 1 | `test_every_parameter_free_get_route_renders_no_user_visible_aerollm` | Setup / onboarding (edge) | Hits all 100+ parameter-free GET routes through TestClient on a maximus lab with a fresh tmp PKB and scans the rendered HTML/JSON. Repo-doc and seeded-research routes are excluded and covered by #2 and F9. | PASS |
| 2 | `test_portal_rendered_repo_docs_have_no_user_visible_aerollm` | Setup / onboarding | `/docs/<path>` renders every `docs/**/*.md` in the portal. 23 docs, plus `BLUEPRINTS.md` and the `/docs` index card (world-forge.md frontmatter), still say AeroLLM. | XFAIL (strict, accepted debt; REVIEW follow-up 4) |
| 3 | `test_existing_lab_installed_skill_pack_does_not_keep_aerollm` | Setup (upgrade edge) | A lab seeded before the rename (base `SKILL.md` text), then the new build boots: `/api/skills/list` and `/api/chat/system-prompt` | **FAIL** |
| 4 | `test_user_edited_installed_skill_survives_upgrade` | Regression guard for any fix to #3 | A hand-edited skill is never overwritten | PASS |
| 5 | `test_benchmark_tool_messages_say_queuellm` | Spec adherence | ARCHITECTURE.md inventory rows `benchmark_models.py:340,362` | **FAIL** |
| 6 | `test_arailctl_help_mentions_old_name_only_as_the_cli_alias` | Setup | Real `arailctl help` output under a temp HOME | PASS |
| 7 | `test_deep_status_output_says_queuellm` | Setup | Real `build-aerollm.sh status` output under a temp HOME | PASS |
| 8 | `test_not_ready_message_uses_new_name_and_quotes_frozen_env_var` | Buddy / failure grace | "QueueLLM isn't ready…" quotes `AEROLLM_MODEL` verbatim, never `QUEUELLM_MODEL`; backend id stays `aerollm` | PASS |
| 9 | `test_not_ready_reaches_the_stream_and_non_stream_endpoints` | Failure grace | The not-ready message as relayed by `/api/chat/stream` (final event) and by the non-stream path | PASS |
| 10 | `test_missing_model_dir_honours_aerollm_model_and_ignores_queuellm_model` | Env compatibility (edge) | `AEROLLM_MODEL` drives the model path; a guessed `QUEUELLM_MODEL` is ignored, and the error names the var that is actually read | PASS |
| 11-12 | `test_ceiling_rejection_messages_say_queuellm[secondary/primary]` | Failure grace | Oversize/dense rejection (`registry/ceiling.py`). There is no separate "dense model rejected" path in arail; the ceiling is the closest. | PASS |
| 13-18 | `test_runtime_branch_cost[{ollama_native,openai_compat,claude}×{stream,non_stream}]` | Cost (edge) | Runtime branch: local → null/`local`, openai_compat → null/`unpriced`, claude → number/`billed_estimate`; every non-null `*_usd` has a source | PASS |
| 19-24 | `test_router_branch_cost_attributes_its_own_record[{mlx,aerollm,openrouter}×{stream,non_stream}]` | Cost (edge) | Router branch (tracks internally): match-attributed; the cloud value equals the tracked record | PASS |
| 25 | `test_router_branch_local_never_reports_charge_when_unattributed` | Cost / concurrency | A prior claude record is last in the tracker; a local router turn does not borrow it | PASS |
| 26-32 | `test_cost_source_is_exact_match_and_unknown_spellings_are_unpriced[...]` | Cost (adversarial) | `AeroLLM`, `AEROLLM`, whitespace, NUL, `Claude`, `queuellm` → `unpriced` (never `local` or `billed`) | PASS |
| 33 | `test_zero_token_cloud_call_still_reports_a_sourced_number` | Cost (boundary) | 0/0 tokens on claude still gives a float with a source | PASS |
| 34 | `test_legacy_costs_json_from_older_build_loads_and_is_regated` | Regression (upgrade) | A pre-sprint `costs.json` on disk with the fabricated 0.008267: `/api/system/costs`, the router fallback, and a legacy cloud record keeping its figure | PASS |
| 35 | `test_cost_fields_carry_no_prompt_text` | Security (privacy) | A canary prompt never appears in the cost fields or the history record | PASS |
| 36 | `test_legacy_note_on_disabled_tier1_is_refreshed` | Setup (upgrade edge) | Non-maximus lab (Tier 1 disabled): the persisted legacy note is still refreshed | PASS |
| 37 | `test_legacy_note_refresh_is_persisted_and_idempotent` | Regression | Raw legacy JSON on disk: refreshed, written once, a second boot is byte-stable | PASS |
| 38 | `test_aerollm_model_env_still_drives_tier1_and_note_is_new` | Env compatibility | Changing `AEROLLM_MODEL` re-seeds Tier 1; the id and backend stay frozen; the new note | PASS |

Allocation (arail: 30 setup / 30 Buddy / 20 security / 10 happy / 10 regression): this sprint has no Buddy-behaviour surface, so the Buddy share went to failure-grace and error-path messages (#8-12) and to the cost contract the chat UI and API emit (#13-34).

## Full suite: base vs HEAD (clean checkouts, temp HOME, run one after the other)

| Checkout | Result |
|---|---|
| `5f775f1c` (base) | 6791 passed, **66 failed**, 37 skipped, 29 xfailed |
| `14d2a02b` (HEAD, before QA tests) | 6860 passed, **67 failed**, 37 skipped, 29 xfailed |
| HEAD + QA tests (sprint and QA files only) | 35 passed, 2 failed (#3, #5), 1 xfailed |

- **The id sets are identical except for one test.** `diff base.failed head.failed` shows a single extra id: `tests/test_mini_experiments.py::test_no_legacy_fabricated_constants`.
  - That is a **pre-existing time-of-day flake**. It asserts `"0.15" not in str(payload)`, and the payload carries a microsecond timestamp (`16:42:10.159244`).
  - Re-run in isolation it failed 1 of 3 runs on HEAD and 1 of 6 on base. The sprint touches no file it exercises (only a string in `experiments/levers.py`).
- **BUILD_LOG's "54" does not reproduce under these conditions (66 here).** The count depends on the machine and environment: I ran with an isolated temp HOME, and 12 of the 66 are `test_qa6_bootstrap_cli.py`.
- **The claim that matters holds:** the branch introduces no new deterministic failure. Pre-existing clusters: `test_qa6_bootstrap_cli` 12, `test_bench_ai_eng_harness` 5, `test_aerollm_defaults` 4 (`KeyError: 'kwargs'`), `test_r1_r3_chat_models` 4, `test_aerollm_model_ready` 3, and others.

## Failures

| # | Test | Symptom | Minimal repro | Severity |
|---|---|---|---|---|
| 3 | `test_existing_lab_installed_skill_pack_does_not_keep_aerollm` | `/api/skills/list` returns `"name":"Optimize AeroLLM"`, and `/api/chat/system-prompt` carries the full old skill body ("make AeroLLM noticeably faster…"). | Install packs into a PKB, write base `optimize-aerollm/SKILL.md` over the copy, call `ensure_starter_skills()` again. `install_pack(force=False)` never refreshes an existing file, and `packs_with_status` has no staleness signal, so the user gets no "update" prompt either. Observed live in this worktree's own `lab/pkb/skills/`, seeded at 10:07 before the rename commits. | **Medium.** The owner's lab predates the rename. They will see "Optimize AeroLLM" in Skills/Forge, and the deep model is prompted with "AeroLLM", which defeats the sprint's win condition. A fix in the same shape as F7: refresh an installed SKILL.md only when it is byte-equal to a previously shipped version; #4 guards user edits. |
| 5 | `test_benchmark_tool_messages_say_queuellm` | `lab/tools/benchmark_models.py:340` `log.warning("AeroLLM benchmarking not yet implemented")`, `:362` `"aerollm backend not yet wired"` | `scan_text("py", benchmark_models.py)` | **Low.** Explicit ARCHITECTURE.md rows, unimplemented. `arailctl benchmark` prints them. The guard's `scan_targets()` omits `lab/tools/`; add it. |

Observations that are not test failures:

- **Low, not tested.** `app.py:7505` activity-log line `f"{optional_backend_name} init failed: …"` renders as "aerollm init failed: …" in the activity feed. The same applies to the `"{exc.backend_name} resident with …"` refusal at `:7486`. The id is interpolated into prose, so the static guard cannot see it.
- **Pre-existing, not this sprint.** `build-aerollm.sh:46` `CRATE_DIR=…/crates/aerollm-api`, but the engine crate is now `crates/queuellm-api` (engine PR #247). On a maintainer machine, `deep status` always reports the crate as missing (release channel), and `deep rebuild` looks in the wrong directory. It is identical on base. File it for the arail integration-surface sprint.
- **Accepted, re-confirmed.** `/api/lab/brief` and `/api/pkb/review` echo the seeded research program ("auto_goal: Optimize AeroLLM's tokens-per-minute…") on existing labs (F9).

## Security review

| Surface | Checked | Findings |
|---|---|---|
| User input | `/api/chat/stream` and `/api/chat` bodies are unchanged. The new fields come from `cost_tracker` records, not from request data. `cost_source()` is an exact dict lookup: mixed case, whitespace, NUL and non-str values all give `unpriced` (#26-32, plus the builder's `7`/`None`). | None |
| Privacy (metadata only) | A canary prompt through the runtime stream branch is absent from every `*cost*`/`*source*`/`*_usd` field and from `get_last_record()` (#35). History gained only numeric and source-literal keys plus `tokens_in/out` integers. | None |
| File I/O | Registry note migration: exact-string match only on `tier1-aerollm`. Written once, then byte-stable (#37). A user-edited note survives (builder test). Legacy `costs.json` loads with the new keys absent (#34). No new paths or tempfiles. `/docs/{path}` traversal guard unchanged (`..`, absolute, non-.md all rejected; code read, not modified). | None |
| `.env` writes | `git diff 5f775f1c HEAD -- scripts/setup.sh`: the only `.env`-bound change is the comment text inside the `printf` at the KV-budget line. The env var name is unchanged (T-ENV set equality, builder). | None |
| Network I/O | No new calls. `build-aerollm.sh` https-only URL check and sha256 pin are byte-equal to base (T-PIN). | None |
| Deserialization | `json.loads` on the registry and `costs.json` as before; no `pickle`/`yaml.load`. | None |
| Crypto | Bundle sha256 constants untouched (T-PIN). Nothing else. | N/A |
| Dependencies | `pyproject.toml` and `uv.lock` have an empty diff vs base. | None |
| Frozen surface | `NOTICE`, `THIRD-PARTY-LICENSES/`, `lab/worlds/`, `BUNDLE.json` empty diff. Ids `aerollm`/`tier1-aerollm`/`AeroLLMBackend` unchanged (#8, #38, builder T-IDS). | None |

## Performance

N/A. Not a hot path: one dict lookup and one dict build per chat turn. The QA files run in about 6 s; the route sweep takes about 4 s.

## Coverage delta

Not measured: `pytest-cov` is not run in this repo's suite, and I did not add it. The new code paths (`cost_source`, `record_to_fields`, `_resolve_chat_cost` match/unattributed/legacy/forced-null branches, the registry note refresh in both enabled and disabled states) are each reached by at least one named test above.

## Notes for the next QA pass

- **"Seeded once, persisted forever" is the recurring trap.** It has now hit three surfaces: registry note (F7, fixed), research program (F9, accepted), and skill packs (#3, open). Before declaring any display rename done, grep every first-run writer (`skill_seed`, `pkb_seed`, `builtin_seed`, `install_pack`, `model_defaults` header) and test an upgraded lab, not only a fresh one.
- **The guard is a static scan.** It misses ids interpolated into prose at runtime (activity log) and anything outside `scan_targets()` (`lab/tools/`, `docs/`, repo-root markdown served by the portal). Test #1's route sweep is the runtime complement; keep it.
- **The full-suite failure count depends on the environment** (54 in BUILD_LOG vs 66 here). Always compare id sets base-vs-HEAD on the same machine and HOME, never counts against a number in a log.
- `test_mini_experiments.py::test_no_legacy_fabricated_constants` is flaky by design (substring match against a timestamp). File it separately.
- The owner witness still has to be run: picker header, chip, and raw final event. Add a fourth check: open the Skills tab and confirm "Optimize AeroLLM" is gone.
