# Test report: the opt-in house-model slot, think:false on Buddy's path, and the Buddy brain A/B harness

**Date:** 2026-11-18
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) at `31990788` (base `845f9b25`). QA tests are in `c347a561`.
**Verdict:** WEAK_PASS

The clean-machine user is unaffected. I showed this on this Mac against the live Ollama, comparing base with head.
The default stays `llama-ai-eng`. The pin record is still `pending`. NOTICE and the frozen `aerollm` surface are
byte-identical to base. Every failing test in the suites I ran also fails at base, or fails only because of this
worktree's local lab state. I found nine gaps (F1–F9). None is above Low. Each one is pinned by a strict-xfail test,
so the test will start failing the moment a fix lands. That is why the verdict is WEAK_PASS and not PASS.

Interpreter: the main checkout's venv (`/Users/netsushi/ProJects/qukaizen-arail/.venv/bin/python`, `PYTHONPATH=src`),
as BUILD_LOG describes. For the base comparison I used a `git archive 845f9b25` copy in the scratchpad. Where a test
needs git, the copy was backed by a scratch index built from `845f9b25` (`GIT_INDEX_FILE`), so this worktree's index
was never touched. I did not run the whole 49-minute suite: the targeted runs gave no reason to.

## Test inventory

New file: `tests/test_qa_aiml_slm_engine.py`, 58 tests: **48 pass and 10 are strict xfail** (each xfail is a filed
finding). Allocation follows the ARAIL QA split.

| # | Test (or check) | Category | Covers | Status |
|---|---|---|---|---|
| 1 | W1 live before/after (scratch script, real Ollama, read-only: `/api/tags`, `/api/show`) | Setup | Fresh minimalist data root at base and head. Slot A = `llama-ai-eng:latest` in both runs (with `MODEL_NAME` set and with auto). Cache yaml identical apart from the timestamp. Registry tier 0 identical. Dialled set identical in both: `127.0.0.1:11434`, `127.0.0.1:11435`, `dns:127.0.0.1`. Zero non-loopback connections. The only gallery difference is the new catalog row. | pass |
| 2 | `arailctl status --json` before/after (fake repos, same cache) | Setup | `arail.status/v2` JSON identical except the `checkout` path. Exit 4 in both. | pass |
| 3 | `test_clean_machine_boot_picks_llama_and_never_touches_the_pin_network` | Setup | Clean tag set: auto = llama. 0 `model_pins` Ollama reads. | pass |
| 4 | `test_clean_machine_pickers_add_no_network_call_and_offer_nothing_to_install` | Setup | Picker and gallery family row: pending, not pickable, no install command, 0 Ollama reads | pass |
| 5 | `test_default_model_and_setup_are_untouched_by_the_opt_in_row` | Setup | `SHIPPED_DEFAULT`, llama row `recommended`, family row `optional`, setup.sh has no family reference | pass |
| 6 | Setup ladder, `test_qa_setup_fresh_lab`, `test_qa_tier1_clean_machine`, `test_setup_extras`, `test_imports`, CI egress list | Setup / Security | Named gates (in the 590-test gate run) | pass |
| 7 | `test_cli_picker`, `test_cli_status`, `test_cli_verbs` at base and head (same `ARAIL_TEST_VENV`) | Setup | The builder's "fails identically at base" claim | see Failures |
| 8 | `test_tag_variants_fail_closed_never_fall_back` ×5 | Security | Uppercase, leading/trailing whitespace, `:latest`, truncated semver: all `not_installed`, family still guarded | pass |
| 9 | `test_latest_copy_is_judged_by_content_not_by_name` ×2 | Security | A `:latest` copy matches only when its weights do | pass |
| 10 | `test_control_merge_renamed_to_the_pinned_tag_is_refused_at_the_pin_api` | Security | `ollama cp` of control-merge to `:1.0.0`, given as the bare id or padded with spaces: 400 "not the pinned build" | pass |
| 11 | `test_wrong_serving_parameters_mismatch_and_name_the_field` ×4, `test_wrong_template_mismatches` | Security | temperature, num_ctx, extra stop token, a declared parameter that is not served, template | pass |
| 12 | `test_malformed_pin_files_never_raise_and_keep_the_family_guarded` ×7 | Security | 200k-deep nesting, non-UTF-8, BOM, top-level array, pins as object, null record, pinned with nothing else: `invalid`, 0 Ollama reads | pass |
| 13 | `test_oversized_but_valid_pin_file_loads_in_bounded_time` | Security | 8 MB file loads in under 5 s | pass |
| 14 | `test_uppercase_record_id_does_not_unguard_the_known_family` | Security | `KNOWN_FAMILIES` fallback | pass |
| 15 | `test_finding_16_override_is_ignored_in_a_portal_process_on_every_env_route` ×3 | Security | `.env`, process env and `secrets.env` each set `PYTEST_CURRENT_TEST` + `ARAIL_MODEL_PINS_FILE`. A full `arail.portal.app` import leaves `pytest` unimported, and `_path()` returns the in-repo file | pass |
| 16 | `test_td7_*` ×3, plus a live TD7 probe (scratch script, real Ollama) | Security | See "TD7, characterised" below | pass (characterisation) |
| 17 | `test_auto_with_llama_missing_and_only_family_tags_installed`, `..._picks_a_stock_model_never_the_family` | Buddy / Setup | Three family tags plus an embedder: `none` with the plain reason. Stock qwen3 is chosen over control-merge | pass |
| 18 | `test_finding_7_picker_row_for_an_installed_control_merge_says_not_the_pinned_build` | Happy | Finding 7 re-verified | pass |
| 19 | Cross-tree request-bytes probe (scratch, loopback stub) | Buddy | `/api/chat` body to `llama-ai-eng` through the real `binding` + router + edge call site: sha256 `cfbedf2b…` **byte-identical at base and head**. qwen3 at head gets `"think": false` and nothing else | pass |
| 20 | `test_a_raising_capability_probe_sends_no_think_and_still_answers` | Buddy | Probe exception: no `think`, no `sampling`, trace `think: None` | pass |
| 21 | `test_ollama_down_during_the_probe_reads_as_not_thinking_and_is_retried_soon` | Buddy | Down means not thinking. The failure is cached, so there is one call, not one per turn | pass |
| 22 | `test_pinned_family_brain_is_unavailable_while_ollama_is_down` | Buddy | `describe()` → `brain_unavailable` | pass |
| 23 | `test_the_edge_never_sends_sampling_or_think_to_llama` | Buddy | Real `_drain` | pass |
| 24 | `test_think_tags_in_content_pass_through_the_edge_unfiltered`, `test_harness_grader_counts_a_think_leak_as_a_format_failure` | Buddy | F5 characterised; the grader catches the leak, including inside JSON strings | pass |
| 25 | `test_child_env_is_an_allowlist_whatever_the_parent_carries` | Security | `LAB_MODE=hybrid`, provider keys, `AERO_*`/`QUEUELLM_*`, `MODEL_NAME`, `PYTHONSTARTUP`, `DYLD_INSERT_LIBRARIES` and the pin override in the parent all fail to reach the child. Every path points into the temp root | pass |
| 26 | `test_ollama_absent_refuses_cleanly_and_leaves_nothing_behind` | Harness | Exit 4, `refused: ollama …`, no traceback, no port echoed, no out dir, no temp root | pass |
| 27 | `test_a_corrupted_rules_pin_refuses_with_exit_4_not_a_traceback` ×4 | Harness | Non-JSON and unknown `active` give exit 4. `[]` and `{}` crash (**F8**) | 2 pass, 2 xfail |
| 28 | `test_a_truncated_tasks_file_refuses` | Harness | `tasks_hash_mismatch`, exit 4 | pass |
| 29 | `test_sigterm_to_the_parent_does_not_orphan_the_child` | Harness | Real subprocess against the stub Ollama (**F6**) | xfail |
| 30 | Committed report re-rendered with `python -m arail.brain_bench report` | Harness | Byte-identical to `bench/buddy_brain/runs/20261118T175243Z-620ebcfa/report.md`. 9/9 self-checks `True`. `rows.jsonl` is git-ignored | pass |
| 31 | `test_committed_metrics_table_has_meaning_and_threshold_in_every_row`, `test_committed_results_never_print_a_verdict_for_the_stand_in_as_evidence` | Harness | W5 metrics table; verdict lines name the UNTRAINED stand-in (finding 20 re-verified) | pass |
| 32 | `test_committed_deltas_table_has_a_threshold_in_every_row` | Harness | **F7** | xfail |
| 33 | F1, F2 (×2), F3, F9 strict xfails | Security | See Findings | xfail |
| 34 | NOTICE sha256 and frozen-token scan | Regression | NOTICE `8cc64663…` at base and head. No changes to `THIRD-PARTY-LICENSES/`, `licenses/`, `pyproject.toml` or the `*-aerollm*.sh` scripts. In every file this branch modified that existed at base, the `AERO_*`/`aerollm`/`AeroLLMBackend`/`tier1-aerollm`/`aerollm_api` token counts are unchanged | pass |
| 35 | Regression suites at head and base (counts below) | Regression | ARCHITECTURE targeted list, sprint suites, gates | see Failures |

### Suite counts (this pass)

| Run | Head | Base `845f9b25` |
|---|---|---|
| Sprint suites (11 files) + QA file | 193 passed, 10 xfailed | n/a (files are new) |
| ARCHITECTURE targeted regression list (15 files) | 615 passed, 1 skipped, **1 failed** | 615 passed, 1 skipped, **1 failed** (same test) |
| Gates: CI egress list, setup ladder, clean machine, setup extras, imports, frozen surface, rename QA | 587 passed, 1 skipped, **3 failed** | 588 passed, 1 skipped, **2 failed** |
| Same gate list in a clean `git archive HEAD` copy | 587 passed, 4 skipped, 0 failed | n/a |
| CLI drivers (picker, status, verbs; the worktree's `.venv` in both) | 3 passed, 2 failed | 3 passed, 2 failed (same tests, same causes) |

## Failures

No failure is caused by this branch.

| # | Test | Symptom | Minimal repro / attribution | Severity |
|---|---|---|---|---|
| 1 | `test_model_hosting_reframe_qa.py::test_backends_raises_on_sentinel_before_any_load` | assertion | Fails identically at base | pre-existing |
| 2 | `test_queuellm_rename_frozen_surface.py::test_env_vars_and_frozen_ids_unchanged_in_every_modified_file` | diff vs `5f775f1c` | Fails identically at base (base copy backed by a git index of `845f9b25`) | pre-existing |
| 3 | `test_queuellm_rename_frozen_surface.py::test_sealed_and_frozen_paths_are_byte_identical_to_base` | diff vs `5f775f1c` | Fails identically at base | pre-existing |
| 4 | `test_queuellm_rename_qa.py::test_every_parameter_free_get_route_renders_no_user_visible_aerollm` | `/knowledge-canvas/api/graph/snapshot` returns "AeroLLM" web_page items | Deterministic in the combined gate run in **this worktree**. Passes alone (35/35, twice), passes in the same combined run in a clean `git archive HEAD` copy, and passes at base. Cause: untracked runtime lab state in this worktree that the snapshot reads, not the code. | environmental |
| 5 | `test_cli_status.py` | `T34: default status took 2.51s (>= 2s)`; at base 2.46s | Timing bound on a loaded machine. Same at base. `scripts/` and `arailctl` are untouched by this branch | pre-existing |
| 6 | `test_cli_verbs.py` | `doctor healthy: expected exit 0, got 3` | Same at base, caused by the worktree's stale July `.venv` (optional tools missing) | pre-existing |
| — | `test_cli_picker.py` | **passes** at base and at head today (222 s) | The builder recorded it failing "identically" earlier. It is load-dependent, not a branch effect. | — |

The builder's claim, verified: status and verbs fail identically at base. Picker is not failing at either today.

## Findings (new; all filed as strict xfails in `c347a561`)

| # | Finding | Where | Severity |
|---|---|---|---|
| F1 | **A namespaced copy of the family bypasses the family guard.** `family_of()` compares the bare name before `:`, so `cdarnell/qkz-aiml-qwen3-1.7b:1.0.0` or `registry.ollama.ai/library/qkz-aiml-qwen3-1.7b:control-merge` is treated as an ordinary model. Auto-resolve picks it when `llama-ai-eng` is absent (repro'd), a pin of it is not digest-verified, and `describe()` serves it to Buddy without a pin check. Ollama names registry pulls this way, so this is the likely form if the mint is ever published to a registry. | `model_pins.family_of`, `model_resolve._resolve_a`, `chat._apply_pin_state` | Low (needs llama absent or an explicit pin; not labelled as the house model) |
| F2 | **`verify()` returns `match` for a model that serves the pinned weights plus something else.** It reads only the first `FROM`, so an `ADAPTER` layered on the pinned GGUF passes. `serving.system` is parsed into the record but never compared, so a different `SYSTEM` passes. W3 asks that what Ollama serves be checked. | `model_pins.verify` | Low (needs a deliberately built local model). Fix before the pin is filled. |
| F3 | **A `pinned` record's `ollama_tag` need not belong to its own family.** `id=qkz-aiml-qwen3-1.7b` with `ollama_tag=qkz-other-model:1.0.0` loads as `pinned`, and that tag is then outside the family guard (auto-eligible). | `model_pins._validate` | Low (needs a reviewed PR to the pin file) |
| F4 | **The chat tab can route to an unverified family tag.** `gallery_view()["installed"]`, which drives the chat dropdown and send routing, lists `…:control-merge` with no pin state, and `/api/chat`'s per-request model override applies it (`app.py:7902-7915`, ceiling check only). The catalog row says ARAIL "uses it only when you pin it and the installed weights match". That is true for boot and Buddy, not for a chat-tab pick. Characterised (passing test). It belongs with TD1/TD7. | `chat/__init__.py:gallery_view`, `app.py` chat override | Low (truth-in-UI) |
| F5 | **Nothing on ARAIL's side strips `<think>` from a brain reply.** Prevention is `think:false` plus the harness's R5 grading. A model that leaks under `think:false` (the mint's own notes saw `<think></think>`) would reach Buddy verbatim in template mode. Characterised (passing test). Recommendation: decide whether the edge or Buddy owns the strip before a P1 pin. | `brain.stream_turn`, `host_edge_routes._drain` | Low |
| F6 | **SIGTERM to the harness parent orphans the child.** On a CI timeout, `kill`, or a closed terminal, the child keeps calling Ollama with no residency restore, and its `brain-bench-*` temp root (child config, lab trace) is left behind. Interrupt (Ctrl-C) is handled (finding 5); SIGTERM/SIGHUP are not. Repro'd with a real subprocess against the stub. | `brain_bench/run.py:684-691` | Low |
| F7 | **W5 partially unmet in the committed report.** 9 of the 15 rows in the "Deltas" table print a delta and CI with empty Rule/Threshold/Meets cells and no meaning column. The metrics table above it is complete. (Also: M5's meaning text still omits "duplicate cite", which is now its only cause.) | `brain_bench/report.py` | Low (re-render only) |
| F8 | **A `rules.pin.json` that is valid JSON but not the expected object crashes the harness** (`[]` → TypeError, `{}` → KeyError) with a traceback instead of exit 4. | `brain_bench/tasks.py:_rules_pin/active_rules_path` | Low |
| F9 | **The resolver's boot note for an installed control merge** named in `.env` or a pin reads "not released yet, so there is nothing to install". Finding 7 fixed the pickers but not this note (seen live on this Mac, below). | `model_pins.verify` (pending short-circuit) + `_resolve_a` note | Low |

### TD7, characterised (the opt-in tag in `MODEL_NAME` through `.env`)

- **Live on this Mac** (fresh data root, `.env` `MODEL_BACKEND=ollama_native`, `MODEL_NAME=qkz-aiml-qwen3-1.7b:control-merge`):
  - At **base**, slot A = control-merge (`source: env`), `MODEL_NAME` = control-merge, tier 0 = control-merge. The untrained control becomes the brain.
  - At **head**, slot A = `llama-ai-eng:latest` (`auto`), `MODEL_NAME` is re-stamped to llama, and tier 0 = llama, with a plain note (F9 wording).
  - On a machine that has llama, the user gets the default.
- **Without llama-ai-eng, or with Ollama down** (unit tests): the resolver returns `none`, but `stamp_env` never erases `MODEL_NAME`, so tier 0 keeps the unverified tag and the chat tab can use it. The Buddy edge reports `brain_unavailable`.
- The Admin route `/api/admin/models/set-default` cannot write a family tag: it accepts only directories under `ARAIL_MODELS_DIR`. TD7 stays an operator `.env` edit, plus F4.

### Test-hygiene leak: attributed

- **What leaks:** `tests/cli/picker_driver.sh`, through `tests/cli/pty_run.py`. It is **pre-existing**: both files are unchanged since `da10eddf` (#161), and this branch does not touch `tests/cli`.
- **Path shape:** its scenarios are named `s1`…`s13` under `mktemp -d`, which is exactly `/var/folders/…/T/tmp.*/s*/`. Each scenario starts stub `uvicorn arail.portal.app` and `uvicorn arail.memory_service` processes.
- **Mechanism:**
  - `pty.fork()` puts `start.sh` and its stubs in a **new session**. `_timeout`'s `killpg` and a terminal Ctrl-C reach only the caller's group.
  - Normal teardown relies on SIGHUP when `pty_run` closes the master.
  - If the test's process group is interrupted (Ctrl-C, or a tool or CI timeout that signals the group), the `_timeout` wrapper dies before its `killpg`. `pty_run` survives, reparented to PID 1. Its `select` gives up after 60 s, then `os.waitpid` blocks forever on a `start.sh` that never exits. The master never closes, so nothing gets SIGHUP.
- **Repro:** SIGINT to the process group of `pytest tests/test_cli_picker.py` while S1 is up. That leaks **7 processes**: `pty_run`, 2 × `start.sh`, 2 stub wrappers, 2 python stubs. 136 is about 19 interrupted scenarios.
- **Normal runs:** a normal run, and SIGKILL of pytest + driver only, leak 0.
- **Fix belongs to the builder:** give `pty_run` a bounded `waitpid` plus `killpg` of its child session on exit or signal.
- **My runs:** they leave **0** processes. I checked with `ps` after every run. The 7 processes from my deliberate repro and its temp dir were stopped and removed. No `brain-bench-*` temp roots remain.

## Security review

| Surface | Checked | Findings |
|---|---|---|
| User input (pin file) | Strict loader under 7 malformed shapes, an 8 MB file, 200k-deep nesting, non-UTF-8 and BOM: always `invalid` and never raises; the family stays guarded via `KNOWN_FAMILIES`. Tag variants (case, whitespace, `latest`, short semver) fail closed with no bare-name fallback. Renamed control-merge refused at `/api/models/pin` (400). | F3 |
| Pin override (finding 16) | `ARAIL_MODEL_PINS_FILE` + `PYTEST_CURRENT_TEST` via `.env`, process env and `secrets.env` in a subprocess that imports the full portal: `pytest` never enters `sys.modules`, and `_path()` is the in-repo file. No `import pytest` anywhere in `src/`. Fix holds. | none |
| Model identity | `verify` compares weights (`FROM`), template and declared parameters: the wrong value of each gives `mismatch`, and the reason names the field and carries no `/Users/` path. | F1, F2 |
| Auth / pin flips | `/api/models/pin` and `/api/chat/default` refuse unverified family tags (existing tests). `/api/admin/models/set-default` is directory-only, so it cannot carry a family tag. The chat per-request override is ungated. | F4 |
| Network I/O | Live W1: dialled set identical at base and head, loopback only. The edge body to llama is byte-identical. `OLLAMA_PORT=11434@host` userinfo injection: urllib (model_pins, harness) fails with InvalidURL; `requests` (probe/backend) would dial the host, but the edge rejects it first via `entry_is_local` (urlparse hostname). Pre-existing `_ollama_base` parse; INFO. | INFO |
| Harness isolation | Child env is exactly the 17-key allowlist whatever the parent carries (keys, `LAB_MODE=hybrid`, `DYLD_*`, `PYTHONSTARTUP`). Egress guard is required. SC8a/SC8b passed on the committed run. Ollama absent gives exit 4 with nothing left behind. | F6, F8 |
| Deserialization | JSON only (`json.loads`), no pickle; recursion bomb caught. | none |
| Crypto | sha256 for identity only; no secrets compared. N/A beyond that. | none |
| Dependencies | None added (`git diff 845f9b25 -- pyproject.toml` empty). | none |
| Ollama residency (other clients) | `ollama.restore` re-pins only models pinned for more than 24 h. A model another client had warm with a short expiry is evicted by the rerun unload, and `restored` still reports True (run 2: `qwen3:1.7b`). This is within FM17 and covered by the documented "Ollama exclusive" precondition. INFO. | INFO |

Not checked, by instruction: **FM26 / O10** (the owner's `MODEL_BACKEND`). It needs reading `.env`. The owner can run
`grep -c '^MODEL_BACKEND=ollama_native' .env` himself; W4 depends on it.

## Performance

N/A for this pass. I did not run the live harness smoke: `qwen3:1.7b` is resident for another client right now
(`/api/ps` runner on blob `3d0b7905…`), and the harness unloads every arm before its rerun sample, so even
`--limit-per-category 2` would evict the nucleus session's model. Harness correctness was exercised against the
stub Ollama. The committed run's R7 is already marked not assessable. The edge adds at most one `/api/show` per
model per 60 s (existing T-B1, plus the failure-cache test above).

## Coverage delta

Not measured. There is no `coverage`/`pytest-cov` in the venv, and installing one is a download. Instead: 58 new
tests over `model_pins`, `_resolve_a`'s family paths, `stream_turn`/`describe`, `child_env`, the refusal paths, and
the committed report.

## Notes for the next QA pass

- When the pin is filled: re-run F1–F3 first, and test the real release record's `stop` list and template sha256
  against what `ollama create` produces on a second machine. That is the one place `_param_mismatch`'s set
  comparison could be wrong.
- `pty_run.py` is the most likely source of future process leaks. Any new pty-based driver inherits it.
- This worktree's untracked lab state makes `test_queuellm_rename_qa` fail in combined runs. Use a clean archive
  copy before calling that test a regression.
- Live W4 (pin → Buddy turn → clear) is still unproven on the owner's lab. It needs O10 answered and the real pin.
