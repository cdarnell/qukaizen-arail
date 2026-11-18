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

---

## Review 1 addendum: full-run findings

**Run:** `eval/buddy_brain/runs/20261118T170647Z-e2c47bca/`, at `e2c47bca`, 17:07:03–17:22:04 UTC (12:07–12:22 local). It exited 3 with no verdict: SC4 and SC8 failed, and SC1–SC3 and SC5–SC7 passed. I worked from `rows.jsonl`, `results.json` and the code, and started no model calls. The addendum verdict is unchanged: **BLOCK** (finding 1). The run also cannot serve as step-14 evidence until findings 12–14 are fixed and it is re-run on a quiet machine.

12. **[ASK] SC8: real writes to the lab happened during the run, and the harness child did not make them. SC8 cannot tell who did.**
    - **What changed.** All 282 entries are in the worktree's real `lab/data/`: about 277 under `lab/data/lance/agent_workflows.lance/` (plus their directories), `lab/data/agent_workflows.json` and `lab/data/egress.jsonl`.
    - **When.** The lance files and the json were modified between 12:09:14 and 12:09:34 local, and `egress.jsonl` at 12:10:40. Both times fall inside **my own review pytest runs** (the targeted suite, then the egress gate). The last `egress.jsonl` entries name their callers as `tests.test_egress_bootstrap.*`.
    - **Why it was not the child.** The child runs with `LAB_ROOT=<tmp>/lab`. `config.DATA_DIR` derives from that (`config.py:96-97`), and `agent_workflows` writes only under `DATA_DIR` (`agent_workflows.py:31-38`). The child cannot reach the real `lab/data`. No portal was running from this worktree; the only uvicorn processes were stubs in temp dirs.
    - **Ruling.** SC8 was right to fail: the guarded lab really changed during the run. Its flaw is that it cannot attribute writes. **Do not exclude `*.lance` or "index caches".** `agent_workflows.lance` is agent-memory lab state, not a harness cache. Excluding it would blind SC8 to exactly the writes it exists to catch.
    - **Required fix** (in `brain_bench/run.py`):
      - (a) **SC8a, attribution, hard pass/fail.**
        - At start and at exit, the child reports every write-capable path it resolved: `config.LAB_ROOT`, `DATA_DIR`, `PKB_ROOT`, `WORLDS_DIR`, `EXPERIMENTS_DIR`, `model_defaults._path()`, the registry file, the `agent_trace` file, `redact`'s secrets path and the egress audit file. The parent fails SC8a if any of them is outside `<tmp>`.
        - Add a positive canary. The child writes `<config.DATA_DIR>/.brain-bench-canary`, and its `agent_traces.jsonl` must exist under `<tmp>` after the run. The parent checks both before `rmtree`. This proves the child's writes landed in the temp root.
      - (b) **SC8b, quiet lab.** Keep today's mtime snapshot of the real roots, plus a residency restore.
        - If SC8a passed but the real roots changed, set `passed: None` with observed text `"inconclusive: another process wrote to the lab during the run"`. The verdict stays withheld, because `decide()` already treats `None` as not passed, and the exit is 3 (finding 3).
        - Group the detail by directory (for example `lab/data/lance/agent_workflows.lance: 277 files`) instead of the first 5 basenames.
        - If SC8a failed, SC8 is FAIL (an isolation bug).
      - (c) In `docs/buddy-brain-bench.md`, add a **"quiet lab" precondition** for any run that produces evidence: no portal, pytest or `arailctl` on the same checkout during the run.
    - **Filed debt (pre-existing, not this branch).** The test suite writes into the real `lab/data`. `agent_workflows.py:22` does `from arail.config import DATA_DIR`, binding the value at import time, so conftest's `monkeypatch.setattr(config, "DATA_DIR", …)` cannot redirect it. `test_egress_bootstrap` writes the real `egress.jsonl`. Running the suite file by file did not reproduce the `agent_workflows` write, so it depends on test order. **TD10** in `sprints/BACKLOG.md`: make `agent_workflows` (and the egress audit default) resolve `DATA_DIR` per call, and add a suite-level guard that fails if `lab/data` changes during a test session.

13. **[ASK] SC4 is mis-designed on two counts: the wrong arm and the wrong manipulation. M1 on the baseline measures slot position, not card content.**
    - **Wrong arm. `llama-ai-eng` is at chance.**
      - It cites slot 0 first on 77 of 132 D+G items (58 %), and 92 of 132 in the withheld arm.
      - Its accuracy is 14/27 when the gold card is in slot 0 and 20/105 elsewhere (19 %, which is the 1-in-5 chance rate).
      - A brain that does not read the cards cannot lose 15 pp when the cards are taken away. -3.8 pp is the expected result.
      - By contrast, both qwen arms are flat across slots (23/27, 22/27, 18/26, 20/26, 19/26) and do read something.
    - **Wrong manipulation. "Withheld" keeps slug and term, and 62 of the 132 D+G items are name-form ("What is X?").**
      - Matching the term string answers those items: the reference arm scores 30/30 on D name-form and 15/16 on G name-form.
      - Only the 70 example-form D items and 16 short-form G items need the card text. Even there, a model can map an example to a term name from its own knowledge.
    - **The finding about the metric.** For the default brain, M1 is a position-bias reading. For the qwen arms, M1 mixes recognising the term name with reading the card. As composed, no check separates "read the World" from "knew the name".
    - **Required fix: replace SC4 with this check (pre-registered in `rules.v1.json` / the manifest before the re-run):**
      - Arm: the `reference` arm. Rule: the non-candidate arm with the highest M1, fixed by role, not chosen after seeing results.
      - Items: example-form D plus short-form G, 86 items.
      - Two blocks, both with **opaque cite ids** in place of slugs (`t1`–`t5` in the card lines and in the schema `cite.items.enum`, mapped back to slugs before grading):
        - (i) `opaque+content`: the cards show `short` and `definition`, with no slug or term.
        - (ii) `opaque-empty`: the cards show only their id.
      - **Pass condition:**
        - M1(i) − M1(ii) ≥ 15 pp, with the paired 95 % CI upper bound < 0 for (ii) − (i).
        - M1(ii) lies within ±10 pp of chance (20 %).
        - M1(i) ≥ 35 %.
      - This proves the tasks can be answered from card content, and that the harness sees content removed. Cost: about 172 calls on the reference arm. The run took 901 s against a 45-minute cap, so this fits.
    - **Keep the current slug+term "withheld" block, but make it informational and not a gate.** Report it as each arm's "content dependence" (M1 full − M1 slug+term), and run it on the reference and candidate arms too.
    - **Add a chance guard to the report.** For every arm, show the first-cite position distribution and the M1 confidence interval against 20 % chance. Label any arm whose M1 CI includes chance with "M1 at chance: does not reflect card content for this arm".

14. **[ASK] M2 = 0/32 on all three arms is real model behaviour, not a grading or plumbing bug. As a comparison metric it carries no information.**
    - **Abstain is reachable.**
      - The composed schema allows `kind: "abstain"` with `cite: []` (`compose.py:37-49`: `kinds` includes abstain, and `cite` has no `minItems`).
      - The qwen arms emit `abstain` on 4 pressure items in this run, so the grammar and decoding path do produce it.
      - Not every O item was graded `cite.wrong_chip`. The reference arm answered O-005 and O-022 with `kind: "answer", cite: []`, which fails `abstain.correct` without being a wrong chip.
    - **The O items are genuinely out of World.**
      - I read all 32. At least 28 have no relevant offered card ("What is Learning Rate?" offered qukaizen cards such as `buddy` and `teacher`).
      - A few are borderline: O-004 QuKaiZen → `aerollm-runtime`, O-013 SFT → `build-time-teacher`, O-009 Guardrails → `adversarial-swarm`.
      - `absent()` checks only exact names and slugs. That is acceptable, but list the borderline items in the report.
    - **Cause.** Buddy's own persona (vendored verbatim) says "Pick the one term that is closest to what the question asks and put its slug first in cite" *before* "Use kind abstain only when no term is about the question". A closest term always exists, and no 1–2B brain overrides that. On Buddy's real path, abstain is mostly enforced by code: when retrieval finds no card, `answer_schema([])` allows only `abstain`. The O items model the case where retrieval passed unrelated cards. How often that happens in production depends on Buddy's retrieval threshold, which is unverified (Assumption 5).
    - **Required fix:**
      - (a) The report states under M2: "No tested brain abstains on its own when shown unrelated cards (0/32 each). Buddy's abstain therefore depends on code: retrieval must withhold cards. M2 cannot rank these arms."
      - (b) SC2 and the comparisons mark a metric where every arm is at 0 % or 100 % as "uninformative (zero variance)". Today SC2 counts M2 as one of "3 metrics inside", which is vacuous.
      - (c) Add a grader check `answer.uncited` (`kind == "answer"` and `cite == []`), report it separately in the M2o detail, and keep it a failure.
      - (d) Add a unit test that `{"kind":"abstain","answer":"","cite":[],"propose":"none"}` validates against `answer_schema(task["offered"])` for every O item. This is the static proof that abstain is reachable.
      - (e) Cross-repo feedback, not an ARAIL code change: send a one-paragraph finding to `qukaizen-buddy` that the persona's "closest term" sentence pre-empts the abstain rule. Record it in SPRINT.md.

15. **[ASK] Effect on the promotion-rule proposal. These findings change the proposal; owner question O11 to be added to ARCHITECTURE's owner questions.**
    - **R3 (M8 ≥ default + 10 pp) is uninformative while the default is at chance.** Even the UNTRAINED stand-in clears it (+46 pp). Proposal: measure R3 against `max(default, reference)`, or on example-form items only. R4 (vs stock qwen3) then carries the domain question, still gated by R11.
    - **R1 is vacuous against a chance-level default and the 32 = 32 O floor.** Proposal: bound M3 against the **reference** arm (CI upper ≤ +2 pp). Mark the O-count clause "uninformative while all arms are at the floor". Add a P2 note that a default brain's abstain must stay code-enforced (finding 14).
    - **New R12 (proposal):** first-cite position bias, the largest share of cites in any single slot, ≤ 35 % (gold position is uniform at 20 %). It catches a positional guesser directly.
    - **The new SC4 (finding 13)** becomes the R10 sensitivity requirement.
    - **Unchanged:** R2a, R2b, R5, R6, R7, R8, R9, R11. SC8 (finding 12) does not touch the rules.
    - **Process.** Commit these as `rules.v2.json`, PROPOSAL and hash-pinned (finding 4), **before** the re-run. Do not edit `rules.v1.json` after its results have been seen.

**Informational, not a verdict:** the stand-in and stock qwen3 are near-identical (M1 101 vs 102/132; SC2 passed). Both beat the default by about 50 pp on M1 because the default is at chance. This run says nothing about the house model. The report labels this correctly.

**Updated required actions:** add findings 12–15 to the list above (items 10–13). Before any re-run, findings 2, 3, 4, 12, 13 and 14(b–d) must land, and `rules.v2.json` must be committed and pinned. Then re-run once on a quiet machine.

---

## Re-review 1

**Date:** 2026-11-18
**Fix loop:** `a3940129..620ebcfa`. Includes `a3940129`, the move to `bench/buddy_brain/`.
**Evidence run:** `c6174cc4`, `bench/buddy_brain/runs/20261118T175243Z-620ebcfa/`, at `620ebcfa`, `git_dirty: false`. 9/9 self-checks pass, rules `rules.v2.json` (PROPOSAL, sha `99d3b2e1…`), 535 s.

**What I checked:** code and tests, not BUILD_LOG's summary.
- Targeted list, all `test_brain_bench_*`, frozen-surface, egress gate and `test_imports`: **1192 passed, 2 skipped, 3 failed.** All 3 failures are pre-existing and fail identically at `845f9b25`:
  - `test_backends_raises_on_sentinel_before_any_load`
  - `test_env_vars_and_frozen_ids_unchanged_in_every_modified_file` (token counts unchanged by this branch)
  - `test_sealed_and_frozen_paths_are_byte_identical_to_base` (its diff now lists only mastra licences and `pyproject.toml`; no NOTICE, no `eval/`)
- I did not start a harness run. Only targeted tests.

## Verdict: WEAK_PASS

No BLOCK remains. Findings 1–15 are fixed or acceptably filed. Five new ASKs (16–20) came out of the fix loop and the committed report. Two of them (16 and 20) are one-line fixes and should land in this branch. The other three can be filed as follow-up tickets in `sprints/BACKLOG.md` before merge. That is the WEAK_PASS condition. QA's own items (W1 clean-machine before/after, the FM26 `MODEL_BACKEND` check, the three CLI drivers) are still QA's.

### Status of findings 1–15 (verified in code and tests)

| # | Status | Evidence |
|---|---|---|
| 1 NOTICE | **Fixed** | `sha256(NOTICE)` = `8cc64663…`, byte-identical to `845f9b25`. `test_bundled_notice_and_bundle_manifest_are_byte_identical` passes. The base and licence are disclosed in the catalog comment, the pin record and the docs. BUILD_LOG corrects the attribution. |
| 2 thresholds | **Fixed** | Every metric has a `threshold` (or "none: informational"). M1 `rule_id` is None. The report has a Threshold column, and the latency table has a meaning line and the R7 ratios. |
| 3 exit code | **Fixed** | `run.exit_code` returns 3 for a full run with a withheld verdict. |
| 4 rules pin | **Fixed** | `bench/buddy_brain/rules.pin.json` lists both rules files, and the sha256s match the files on disk. `tasks.verify_rules` refuses an unlisted name or a wrong digest. The real path is recorded in results (`bench/buddy_brain/rules.v2.json`, `pinned: true`). `rules.v2.json` was committed (`cf7337f5`) before the run commit and carries `"status": "proposal"`. |
| 5 interrupt | **Fixed** | `try/finally` around the child: restore and snapshot always run, the temp root is removed, exit 5. |
| 6 pin-file override | **Partly fixed. See finding 16.** | Test-only on paper, but a `.env` line can switch it back on. |
| 7 picker wording | **Fixed** | `model_resolve.py:156-157`. |
| 8 re-parse | **Fixed** | `load()` memoised on (path, mtime_ns, size), and it returns a copy. |
| 9 report text | **Fixed** | Contamination note conditional on `heldout_n` (`report.py:60-69`). The O-split and borderline-item lines are in the report. |
| 10 TD7 | **Filed** | `sprints/BACKLOG.md`, section "aiml-slm-engine review follow-ups". |
| 11 | n/a | Informational. |
| 12 SC8 | **Fixed** | `brain_bench/isolation.py`. SC8a checks 11 resolved paths, the canary and the trace inside the temp root. SC8b gives `None` (inconclusive) for a foreign writer and groups changes by directory. The child now also gets `ARAIL_DATA_DIR`, which closes a real hole the builder found. The evidence keeps no absolute child paths. TD10 and the quiet-lab precondition are filed and documented. |
| 13 SC4 | **Fixed** | Opaque `t1`–`t5` ids on the reference arm over 86 example/short items. Thresholds come from the pinned rules `sc4` block. In the run, content is 62.8 % vs ids-only 23.3 % (CI −53.5..−24.4). M9 (content dependence) is informational. The chance guard is in place. |
| 14 M2 | **Fixed** (a–d). (e) **Filed** as an owner note. | The M2 wording is in the report. SC2 lists M2 as zero-variance. M2u is graded, and `test_14d` validates abstain for all 32 O items. Recording the persona note in BACKLOG instead of SPRINT.md is acceptable. |
| 15 rules v2 | **Fixed** | R1 vs reference, R3 vs the better of default and reference, R12 ≤ 35 %, a pre-registered `sc4` block, `rules.v1.json` kept unchanged. |

### Checked and clean: nothing new introduced

- **Default unchanged.** `SHIPPED_DEFAULT`, `scripts/setup.sh`, `arailctl`, `scripts/status.sh`, `model_defaults.py` and `registry/` are untouched since `845f9b25`. The `llama-ai-eng` row is unchanged.
- **Pin record.** `chat/model_pins.json` is unchanged since its first commit: `pending`, with null identity fields.
- **Frozen surface.** `tests/test_queuellm_rename_frozen_surface.py`, `THIRD-PARTY-LICENSES/`, `licenses/` and `pyproject.toml` are untouched by this branch. Moving the frozen task set to `bench/` changed no bytes, and its sha still matches the manifest.
- **The report labels the stand-in UNTRAINED** on every arm row that uses it, including `withheld_candidate`.

### New findings

16. **[ASK] `model_pins.py:95-97`. A `.env` line turns the pytest-only override back on.**
    - `arail.config` runs `load_dotenv()` at import, so a `.env` containing `PYTEST_CURRENT_TEST=x` and `ARAIL_MODEL_PINS_FILE=<any path>` makes a production process read that pin file.
    - Reproduced: run with `env -i`, `ARAIL_ENV_FILE` pointing at such a file, then `import arail.config` and `model_pins._path()`. It returned the scratch path.
    - The risk is the same class as TD7 (a deliberate operator edit), but finding 6 asked for "cannot be triggered in production".
    - **Fix (one line):** also require `"pytest" in sys.modules`. Add a subprocess test that does exactly the repro above and asserts `PINS_PATH`.

17. **[ASK] M5 / R5 hide the cause. Almost every format failure is a duplicate cite that Ollama's grammar does not prevent.**
    - The sent schema has `uniqueItems: true`, but Ollama 0.40.11's grammar-constrained decoding does not enforce it. Replies such as `"cite": ["quantization","quantization","quantization"]` fail schema validation and count as `format.parse`.
    - **Scale:** 43 of the baseline's 45 M5 failures are this. The candidate's single R5 failure (P-010) and the reference's single failure are the same.
    - **Effect:** R5 "does_not_meet" for the stand-in is caused only by this. The M5 meaning ("leaked a control token or `<think>`, invalid JSON, empty, or cut off") does not tell the reader.
    - The builder's smoke SC1 flake (`G-005`) was also a duplicate-cite reply.
    - **Fix:**
      - (a) Grade a duplicate cite as its own check, `format.duplicate_cite`. Keep it a format failure, because Buddy's grammar forbids it.
      - (b) Break M5 down by sub-check in the report: leak, unparseable, schema-invalid, duplicate cite, empty, truncated.
      - (c) Add one line under "What this run cannot tell you": the grammar does not enforce `uniqueItems`, and whether Buddy's `finalize` dedupes is unverified.
    - This can be re-rendered from `results.json` once (a) is computed from the stored content. No re-run needed.

18. **[ASK] `report.md` "Where the failures are" points at a file that is not committed.**
    - It prints 60 rows, then "801 more in rows.jsonl", but `rows.jsonl` is git-ignored (`.gitignore:176`).
    - The committed per-task locations live in `results.json` `failures[]` (861 entries).
    - **Fix:** point the overflow line at `results.json` → `failures[]`. Optionally order the 60 rows by candidate first, since they are the decision-relevant ones.

19. **[ASK] The R7 latency figures in this run are noisy, and must be reported as such.**
    - The nucleus mint session was using the same Ollama during the run. `ps_before` shows `qwen3:1.7b` (the reference arm's tag) and `nomic-embed-text` already resident, so another client was loaded on that Ollama. The harness's reload step unloaded `qwen3:1.7b` under that client.
    - The R7 ratios are TTFT ×1.52 (limit 1.5) and total p95 ×1.86. They differ from the limit by less than plausible contention noise, and the arms run on different runners (ggml vs llamacpp).
    - **Ruling:**
      - R7 from `20261118T175243Z-620ebcfa` is **not evidence**. Record that in SPRINT.md beside the run.
      - Correctness results are not affected: grading is deterministic per item, and SC1 0/82 and SC7 0/96 passed.
      - SC8b's quiet-lab check covers lab files, not Ollama.
    - **Fix (follow-up ticket):**
      - Sample `/api/ps` before, during (per block) and after.
      - If a model outside the arm set is loaded, or an arm tag was already resident at start, set R7 to `not_assessable: Ollama shared with another client` and add a warning.
      - Add "Ollama exclusive to the run" to the documented quiet-lab precondition.

20. **[ASK] The verdict lines do not say whose verdict it is.**
    - The report prints "P1 (your own pin): **does_not_meet**" and "P2 … **does_not_meet**" without naming the candidate. Read alone, they look like a ruling on the house model.
    - The arm table labels the stand-in, but the verdict line is the one people quote.
    - **Fix (one line in `report.py`):** append the candidate tag and its label to each verdict line, e.g. "for `qkz-aiml-qwen3-1.7b:control-merge` [UNTRAINED stand-in: plumbing only, never evidence about the house model]". Do the same in `summary_line`. Re-render the committed `report.md`; no re-run needed.

**Informational, not a finding:**
- The run's correctness numbers are internally consistent: near-A/A M1 −0.8 pp, CI −3.8..+2.3.
- Stock qwen3 and the untrained stand-in are indistinguishable, as expected. R4 "does_not_meet" correctly says the stand-in adds nothing over stock.
- The BACKLOG reuses the labels TD7/TD10 that other sprints' sections already use. Unambiguous inside its section, but a sprint prefix (`aiml-TD7`) would avoid confusion.

### Required before merge

1. Findings 16 and 20: fix in this branch, and re-render `report.md` for 20.
2. Findings 17, 18, 19: fix, or file as tickets in `sprints/BACKLOG.md` with this review's text. Record in SPRINT.md that R7 from this run is not evidence (finding 19).
3. QA: W1 clean-machine before/after, FM26, the three CLI drivers, and a TD7 probe.
