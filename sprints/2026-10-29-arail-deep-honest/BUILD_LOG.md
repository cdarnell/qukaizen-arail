# Build log: ARAIL deep, honest

**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `e3f47816`
**Vision:** [VISION.md](./VISION.md) at `942cbda8`
**Branch:** `qukaizen/arail-deep-honest` (from arail main `62d5ebdf`). Not pushed.
**Started:** 2026-10-29
**Finished:** 2026-10-31 (the original build); the fix pass after the review was done the same day
**Status:** done. All steps 0 to 9 are committed. The live check passes on today's broken wheel. The architect's review (`REVIEW.md`) returned BLOCK on B1 and B2; "Fix pass" at the end of this file records what was fixed and how it was checked. The numbers above that section are as of the original build (`fdc069bd`). The re-review of that pass returned WEAK_PASS; "Fix pass 2" at the very end records what was fixed from it.

## Hard rules this build kept

- **The operator's live install was never written.** `~/ProJects/qukaizen-arail/.venv` is read by one thing only: the live check (step 8), which uses its interpreter read only with `PYTHONDONTWRITEBYTECODE=1`. Before and after four live runs, the venv's `aerollm` entries (names, sizes, mtimes, sha256), its top-level site-packages listing, the QueueLLM checkout (HEAD `e730d5fe`, clean tree) and the crash-report folder were identical, and no file anywhere in `.venv` was newer than the runs. Every test run used a scratch venv (Python 3.11.17).
- **One crash report was written outside the repo, and removed.** On 2026-10-29, while testing the first version of the fake runtime's `crash3` mode, a test sent itself SIGABRT and macOS wrote one report for that test's Python process into `~/Library/Logs/DiagnosticReports`. I deleted that one file and changed the fake to SIGKILL by default (SIGABRT is opt-in with `ARAIL_FAKE_DEEP_CRASH_SIGNAL=ABRT`). Nothing else in that folder was touched; its newest report is still the operator's own from 2026-10-25, and the four live runs added none.
- **`./arailctl deep rebuild` was never run.** Row W7 runs `scripts/build-aerollm.sh build`, the one line `deep rebuild` execs, with a scratch empty venv and a stub `cargo` that records and fails; the stub was called 0 times. See Deltas.
- **QueueLLM was not edited and `ISSUE.md` was not filed.** `ISSUE.md` is the draft the operator approves.
- **No frozen name was renamed.** For every modified file, no occurrence count of `aerollm_api`, `AERO_`, `AEROLLM_`, `tier1-aerollm`, `AeroLLMBackend`, `build-aerollm.sh` or `backend="aerollm"` went down.
- **`src/dac_world/` is byte-identical to `62d5ebdf`** (`git diff --quiet 62d5ebdf -- src/dac_world`; also row W8). **Buddy's contract bytes did not change** (`tests/test_host_contract_vendor.py` passes).
- **Tests use fixtures.** The operator's real World instances were not touched: every row of the live check runs in scratch folders, and the suite's per-test `ARAIL_DEEP_STATE_DIR` plus a session guard (F23) keep the checkout's real verdict file out of every test.
- **Cost text says "token-free", never "free".** A scan of the added lines found "Token-free" and nothing else about cost.
- **Every number the probe, the Curator, `deep status` and the live check print carries its meaning, bar and why, out-of-range, and where the failure lives.** Tests TQ1 to TQ4 fail the build if one does not.
- **Nothing was pushed.** Every scratch venv, scratch root, process and temp file was removed (see Final state).

## Baseline (untouched `origin/main` `62d5ebdf`)

Measured before any code change, in a scratch venv (Python 3.11.17), twice: on a `git archive` copy and on a `git clone` of `origin/main`. The scratch venv needed `openai`, `anthropic` and `tomli` added (the first attempt stopped at collection because `openai` was missing); both runs below collected cleanly.

| Run | Failed | Passed | Skipped | xfailed | Errors | Time |
|---|---|---|---|---|---|---|
| `git archive` copy | 123 | 7,791 | 31 | 28 | 7 | 9 min 30 s |
| `git clone` | 120 | 7,797 | 28 | 28 | 7 | 23 min 27 s (machine busy) |

The clone is the baseline of record, because it is the same kind of tree as this branch: 127 failing or erroring test ids, compared with the branch by id (listed by module under Final state). The two runs differ by eight ids: six fail only in the archive copy, which has no `.git` (one of them is a `git check-ignore` test), and two fail only in the clone (`test_queuellm_rename_frozen_surface.py`). The 7 errors are all `tests/test_aerollm_defaults.py` fixtures: `ModuleNotFoundError: No module named 'aerollm_api'` at line 65 (the scratch venv has no wheel).

## Plan

Order follows ARCHITECTURE "Recommended implementation order". Tests landed in the same commit as the code they test. Where it was cheap, a test was shown to fail by breaking the code on purpose; for the live check this was done on six product lines (see "Can the check fail?").

| # | Files | Change | Test | Commit |
|---|---|---|---|---|
| 0a | `sprints/2026-10-29-arail-deep-honest/DAY1.json` | Appendix A, verbatim. The day-1 evidence. | parses as JSON | `818ea0bf` |
| 0b | `sprints/2026-10-29-arail-deep-honest/ISSUE.md` | Appendix B. QueueLLM issue draft. Not filed. | none (docs) | `3f443fcf` |
| 0c | `sprints/2026-10-29-arail-deep-honest/BUILD_LOG.md` | The skeleton (this file, before any code). | none (docs) | `dafd34fb` |
| 1a, 1b | `tests/fakes/{__init__,aerollm_api_template,fake_deep_runtime}.py`, `tests/conftest.py`, `src/arail/deep_verdict.py`, `tests/test_deep_verdict.py`, `tests/test_fake_deep_runtime.py` | The fake deep runtime; C1 and C2: identity, the per-binary store with flock and atomic replace, fail-closed corrupt store, `note_call`, `note_load_failure`, `record_probe`, `current`, the autouse isolation. | U1 to U13, SEC1 | `bc28c84f` |
| 1c | `src/arail/deep_verdict.py`, `tests/test_deep_verdict_cli.py` | Provenance (`shadowed`, `channel`, `import_target`), the `status` text and the CLI. Split from 1b to keep it reviewable. | TQ4 (first half) | `6ebabfbd` |
| 2 | `src/arail/router/backends.py`, `tests/test_deep_backend_hook.py` | C4 backend hook. | B1 to B5 | `573a6b6e` |
| 3 | `src/arail/agent_context.py`, `src/arail/agents/deep_policy.py`, `tests/test_deep_policy_failing.py`, `tests/test_qa_buddy.py` | `deep_failing` in `explain()`; `complete_preferring_deep()` walks `explain()` once and stamps the fast call. | P1 to P5 | `1ea9ec53` |
| 4a | `src/arail/registry/core.py`, `src/arail/registry/health.py`, `tests/registry/test_deep_verdict_overlay.py` | C5 tier-1 overlay on read; announce once. | H1 to H3, H5, H6, H8 to H12 | `1d4dcc49` |
| 4b | `src/arail/portal/model_warmth.py`, `src/arail/deep_verdict.py` (the generation phrase), `tests/registry/test_deep_loaded_lines.py` | C8 boot and preload lines. | H4, H7 | `1b62986b` |
| 4c | `src/arail/portal/app.py`, `templates/research.html`, `static/research.css`, `tests/test_research_truth_deep_failing.py` | C5 truth strip. | T1, T2, SEC2 | `5cec24b6` |
| 4d | `tests/test_chat_deep_stays_loud.py`, `tests/fakes/fake_deep_runtime.py` (a `model` argument) | Chat stays loud. | the chat row | `697a6575` |
| 5a | `src/arail/deep_probe_prompts.py`, `tests/test_deep_probe_prompts.py` | Appendix C, pinned by hash. | Q16 | `69190117` |
| 5b | `src/arail/deep_probe.py`, `tests/test_deep_probe.py`, `tests/fakes/` (a stub tokenizer) | C3 supervisor and child, the teaching renderer. | Q1 to Q15, Q17 to Q19, TQ1, TQ2, SEC3, SEC4, SEC6 | `58b6d2a3` |
| 5c | `arailctl`, `docs/cli.md`, `tests/test_deep_probe_cli.py` | `deep probe` dispatch, help, usage, exit code 5. | Q20 | `41fe0c69` |
| 6a | `src/arail/portal/world_routes.py`, `tests/test_curator_honest.py`, `tests/test_world_forge_api.py` | C6 `_CountedRouter`. Review, grow and forge report what happened. | C1 to C9, TQ3 | `6c4bed9d` |
| 6b | `static/js/world-terms.js`, `static/knowledge.css`, `tests/test_world_terms_notices.py` | One notice for `partial`, `failed`, `not_applied`. | C10, SEC2 | `ebd29738` |
| 7a | `scripts/build-aerollm.sh`, `src/arail/deep_verdict.py` (marker and target helpers), `tests/test_build_aerollm_honest.py`, `tests/test_deep_verdict_cli.py` | C7: detect once, refuse the renamed crate, preamble check, shadow check, sha256 verification, build marker, honest `status`. | S1 to S11 | `4b5c768c` |
| 7b | `scripts/setup.sh`, `src/arail/router/backends.py` (hint), `tests/test_install_hints_honest.py` | `setup.sh` and the install hint stop claiming readiness. | S10 | `ea5aa3e7` |
| 8a | `scripts/deep-honest-win.sh`, `scripts/deep_honest_win.py`, `tests/test_deep_honest_win.py` | The live check and its dry run on the fake runtime. | TQ4 (second half), 30 tests | `6979950a` |
| 8b | `scripts/deep_honest_win.py` | Time bar and fast-model note, from the first live runs. | the same 30 | `06e58b9a` |
| 8c | `sprints/2026-10-29-arail-deep-honest/LIVE_RUN.txt` | The verbatim output of the live check on the broken wheel (first recorded run). | rows W0 to W8 | `857a173f` |
| 8d | `scripts/deep_honest_win.py`, `tests/test_deep_honest_win.py` | The live check ends the processes a timed-out row leaves behind. | 2 new tests, both fail with the reaper off | `f1dff0ec` |
| 8e | `sprints/2026-10-29-arail-deep-honest/LIVE_RUN.txt` | The recorded output again, from the final driver. | rows W0 to W8 | `ebdbaa23` |
| 9a | `sprints/BACKLOG.md` | TD1 to TD13. | none (docs) | `8671c955` |
| 9b | this file, `SPRINT.md` | Final numbers and the ledger row. | full suite | this commit |

Added beyond the table, each its own commit: `0f3e7ec7` drops three unused imports from the step 1 tests; `b15b0b6c` fixes a leak of the fake `aerollm_api` into later tests (found in the step 5 full run; adds `tests/test_fake_deep_isolation.py` and the purge fixture in `tests/conftest.py`); `ef2f6798` (6c) removes two old route handlers that step 6a left behind under the same names; `77cf8b75` (6d) hardens the Curator tests after one full-suite failure (see Final state).


## Execution

Each step names what landed, how it was tested, and anything that differed from the plan. Test ids are the architecture's (U, B, P, H, T, Q, TQ, C, S, SEC); the test names carry them.

### Step 0 (`818ea0bf`, `3f443fcf`, `dafd34fb`)

`DAY1.json` is Appendix A, verbatim. `ISSUE.md` is Appendix B: the QueueLLM issue, with the four builds' evidence pins and the call-2 error text. It is a draft; nothing was filed. This file was committed as a skeleton before any code.

### Step 1: the verdict store (`bc28c84f`, `6ebabfbd`)

`src/arail/deep_verdict.py` is the one place that knows whether deep generation works on the binary this process loaded. One file, `<checkout>/lab/data/deep/verdict.json` (schema `arail.deep-verdict/v1`), keyed by the sha256 of the native file the import loaded. Writes take a flock and go through a temp file and `os.replace`; the folder is 0700, the files 0600, and the lock is opened with `O_NOFOLLOW` (a symlink planted at the path is replaced, not followed). A missing file reads as "never tested". A corrupt one reads as failing, and the next writer moves it aside. A failure is cleared by one thing: a probe that passes 5 of 5 and started after the failure's last timestamp. A later single success clears nothing. `ARAIL_DEEP_STATE_DIR` moves the folder. The probe's child keeps its failures in memory (`ARAIL_DEEP_PROBE_CHILD=1`); the supervisor is the only writer.

The fake deep runtime (`tests/fakes/`) is a pure-Python `aerollm_api` with modes `pass`, `fail2`, `empty2`, `crash3`, `hang2`, `loadfail`, `slow:<s>` and `stdout-noise`, and a `fake_deep` factory fixture. `crash3` kills with SIGKILL by default: a real SIGABRT makes macOS write a crash report into the operator's DiagnosticReports folder (opt in with `ARAIL_FAKE_DEEP_CRASH_SIGNAL=ABRT`). `tests/conftest.py` gets three guards: a per-test `ARAIL_DEEP_STATE_DIR` with clean caches, a purge of any leaked fake `aerollm_api` at the start of every test, and a session check (F23/U13) that fails the run if the checkout's real verdict file changed.

Step 1c adds what the build script and `deep status` need: `shadowed()` (copies the import never loads), `channel()` (`bundled`, `source build` or `pip` only when a marker or a RECORD hash proves it, else `unknown - <why>`), `import_target()` (what `import aerollm_api` would load for a destination) and the `status | identity --json | target | channel` CLI. Tests U1 to U13 and SEC1 are in `tests/test_deep_verdict.py` (36 test functions); `tests/test_deep_verdict_cli.py` (25) holds the status text and the first half of TQ4; `tests/test_fake_deep_runtime.py` (11) pins what each fake mode does, so a test double cannot drift silently.

### Step 2: the backend hook (`573a6b6e`)

`AeroLLMBackend` files every deep outcome in the verdict: `_note_deep` after each `complete()`, `_note_deep_load` for a load failure (a missing model folder fails that process only and is not written), and `health_check()` returns false while failing. A raising `note_call` never breaks `complete()`. B1 to B5.

### Step 3: the policy (`1ea9ec53`)

`explain()` gains the additive code `deep_failing`, after `wheel_missing` and before the schedule gate, for foreground and background alike; `prefer_deep()` stays its boolean projection. `complete_preferring_deep()` now walks `explain()` once. On `deep_failing` it makes no deep attempt at all. A fast call made because deep is failing (or because this call's deep attempt just failed) runs inside the new `agent_context.with_deep_reason`, so its trace carries `deep_reason_code="deep_failing"` and the detail. `AgentHeldError` is unchanged. P1 to P5. Two stubs in `tests/test_qa_buddy.py` moved from `prefer_deep` to `explain` (see Deltas).

### Step 4: the surfaces (`1d4dcc49`, `1b62986b`, `5cec24b6`, `697a6575`)

4a: tier 1 cannot read healthy while generation fails. The overlay is applied on read in `to_state()`, `_gate_reason()` and `_probe_aerollm`, from the verdict's static identity (a registry read never imports the runtime), so no stored write (Check, preload, boot warm, `report_success`) can show healthy over it. A stored `unhealthy` that carries the overlay's own text is re-probed once a pass clears it. One announcement per change. H2 pins the Check-erase regression. 4b: the "deep model loaded" lines say "weights resident" and name the generation state; they never say "ready" while failing. 4c: the research truth strip gets an additive `generation` block and a danger class. 4d: Chat stays loud, in a regression test: with `fail2`, turn 2 replies `Inference failed:` and there is no silent fall-back. H1 to H12, T1, T2, SEC2.

### Step 5: the probe (`69190117`, `58b6d2a3`, `41fe0c69`)

`./arailctl deep probe [--json] [--force]` makes five sequential deep calls through ARAIL's own `AeroLLMBackend` in one fresh process and records the result. The supervisor never imports `aerollm_api`. The child parks native stdout on stderr and talks in JSON lines marked `"probe": 1`, so native noise cannot forge a result. Per-call and load deadlines are 300 s (env overridable); a stuck child's whole process group is killed. A memory check (weights plus 2 GiB headroom) refuses a second copy on a small Mac unless `--force`. One lock allows one probe at a time. Exit codes: 0 five of five, 1 fewer, 2 usage, 5 not run (nothing recorded), 130 interrupted. The JSON (`arail.deep-probe/v1`) carries a `teaching` object with four parts for every number that is not an identifier. The prompt set (Appendix C) is pinned by hash `b5bcb562...8641e`, so the shipped probe is provably the one that produced `DAY1.json`. Q1 to Q20, TQ1, TQ2, SEC3, SEC4, SEC6; `docs/cli.md` documents it.

### Step 6: the Curator (`6c4bed9d`, `ebd29738`, `ef2f6798`)

`_CountedRouter` sits at ARAIL's call site, because `dac_world` is vendored and unchanged: it counts attempted and failed calls (an exception, or a response with no text), and re-raises unchanged, so `dac_world` skips the call exactly as before. A review reports `complete`, `partial` or `failed`; `judged` is what the judge answered, not what it was asked; "All clear" appears only for a complete review. A grow pass with any failed call is `not_applied`: no gate, no reseal, no swap, no evolution entry, no "evolved via" line. A forge counts its failures but still shows the draft. When the fast model stood in because deep is failing, the report names it and says why. The World Terms page shows one notice line for `partial`, `failed` and `not_applied`. C1 to C10 and TQ3. Step 6c removed two old route handlers (`api_review_get`, `api_grow_get`) that step 6a left behind under the same names without a decorator; found by a duplicate-definition scan of every changed file against the base, which now reports none. Step 6d (`77cf8b75`) is a fix to this step's tests, not its code: see Final state.

### Step 7: the build script (`4b5c768c`, `ea5aa3e7`)

`scripts/build-aerollm.sh` decides what the checkout is once (`repo_state`: `old`, `new`, `none` or `no-repo`). The renamed crate `crates/queuellm-api` is refused with the true reason and "Nothing was built or installed."; `auto` never switches channel on its own. A built artifact is thrown away unprobed if its `downloadComponent` count is above 0 (Metal toolchain down at build time). Before any copy it asks `find_spec` what `import aerollm_api` would load; a package folder or a `PYTHONPATH` entry that wins is refused, with its owner named. After the copy it verifies the sha256 of the file that actually loads, and writes `aerollm_api.build.json` (QueueLLM commit, dirty flag, artifact sha256). It never says "QueueLLM ready": every install ends with "Generation is not tested yet: run ./arailctl deep probe". `status` prints the `deep_verdict` block. 7b: `setup.sh` and the install hint (`backends.py`) stop claiming readiness. S1 to S11 and the numbers of TQ4. No existing test needed to change.

### Step 8: the live check (`6979950a`, `06e58b9a`, `857a173f`, `f1dff0ec`, `ebdbaa23`)

`scripts/deep-honest-win.sh` and `scripts/deep_honest_win.py`, described in the next section. The 32 tests in `tests/test_deep_honest_win.py` cover the teaching on every number (the second half of TQ4), the exit rule, the wrapper's isolation, the detectors against the old build script, the finder and reaper of stray processes, and dry runs of the nine rows on the fake runtime, which also prove the check can fail (a good wheel exits 1; a stale passing verdict gives W6 silent outcomes; a hung probe fails its row and leaves nothing running). 8d came from reading the timeout path: a row stopped at its deadline was killed, but the probe it started lives in a session of its own and would have run for up to 300 s more. The driver now finds processes started for its scratch root by a marker every row carries (`ARAIL_DEEP_STATE_DIR=<root>/...`, read with `ps -E` on macOS) and ends them on a timeout, on an interrupt and at the end. 8c and 8e are the recorded output (first run and final driver).

### Step 9 (`8671c955`, this commit)

TD1 to TD13 are in `sprints/BACKLOG.md`. The full suite, `tests/test_no_user_visible_aerollm.py`, the byte-identity of `src/dac_world/` and Buddy's contract test were run; numbers are under Final state.

## The live check, run on this Mac on today's broken wheel

Run on 2026-10-31 at 12:40 EDT, from the worktree, as an operator would run it, on the driver as committed in `f1dff0ec`. The operator's venv python was used read only.

```
ARAIL_WIN_PYTHON=~/ProJects/qukaizen-arail/.venv/bin/python \
ARAIL_WIN_ENV_FROM=~/ProJects/qukaizen-arail/.env \
ARAIL_WIN_QUEUELLM=~/ProJects/qukaizen-queuellm \
./scripts/deep-honest-win.sh
```

Result: **exit 0, nine of nine rows pass, 60 s.** The wheel is `aerollm_api` 0.1.0-rc.2, native sha256 `d62e1e16e93c...` (the day-1 wheel of `DAY1.json`), QueueLLM checkout `e730d5fe`. The verbatim output, with the full teaching block of every row, is [LIVE_RUN.txt](./LIVE_RUN.txt). It was run four times (64 s, 141 s, 55 s and 60 s) and printed the same text each time, apart from the scratch name and the total time. After the runs the venv, the QueueLLM checkout and the crash-report folder were identical to before the first.

| Row | Check | Deep calls attempted | Deep calls failed | Silent outcomes | Result |
|---|---|---|---|---|---|
| W0 | preflight | 0 | 0 | - | PASS |
| W1 | Chat x5 | 5 | 4 | 0 | PASS |
| W2 | agents x5 | 2 | 1 | 0 | PASS |
| W3 | Curator review | 6 | 5 | 0 | PASS |
| W4 | Curator grow | 7 | 6 | 0 | PASS |
| W5 | deep probe | 5 | 4 | 0 | PASS |
| W6 | fresh process | 0 | 0 | 0 | PASS |
| W7 | build script | - | - | 0 | PASS |
| W8 | postflight | - | - | - | PASS |

### What the numbers mean (shared by every row that shows them)

Each number has four parts. The bar changes by row (next section); the other three do not.

**deep calls attempted**

- What it is: deep (QueueLLM) generation calls the row's consumers made in the row's own fresh process, counted by ARAIL's per-process deep counters, not by this script.
- Out of range: more than the bar: ARAIL kept calling a build it already knew was failing, so a gate did not close. fewer: a call that should have run did not, so the row did not test what it claims.
- The fault lives in: ARAIL: the gate (deep_policy.explain) and the backend hook (router/backends.py), not QueueLLM.

**deep calls failed**

- What it is: of those calls, the ones that raised an error or returned no text.
- Out of range: fewer than the bar: the wheel behaves differently from DAY1.json (re-baseline it) or an error was swallowed before it was counted. more: a new fault, such as call 1 failing too.
- The fault lives in: the failures themselves live in the QueueLLM binary (DAY1.json: mlx-native Qwen2 prefill, an rms_norm weight of 1 element); a failure that was not counted lives in ARAIL's backend hook.

**silent outcomes**

- What it is: places where ARAIL said deep was fine, or the work done, when it was not: a surface that does not say failing, a reply or a log line that hides an error.
- Out of range: 1 or more: the check lines of the row name the row and the surface that said it. Any one is the bug this sprint fixes.
- The fault lives in: the code of the surface a failed check names, not QueueLLM.

### Row by row: what it showed, and the bar for each number

**W0. preflight: what is installed and what the model folder holds** (PASS)

- scratch .env holds only: AEROLLM_KV_BUDGET_PCT, AEROLLM_MODEL, ARAIL_MODELS_DIR, LAB_TIER
- aerollm_api resolves to /Users/netsushi/ProJects/qukaizen-arail/.venv/lib/python3.11/site-packages/aerollm_api/__init__.py
- site-packages: /Users/netsushi/ProJects/qukaizen-arail/.venv/lib/python3.11/site-packages (11 aerollm or queuellm files recorded, 455 top-level entries)
- QueueLLM checkout: /Users/netsushi/ProJects/qukaizen-queuellm, HEAD e730d5fe3f6b, working tree clean
- 4 checks, all ok.
- deep calls attempted = 0. Bar and why: 0: the preflight reads files and git state only. A deep call here would be recorded as a failure and spoil the rows after it.
- deep calls failed = 0. Bar and why: 0, for the same reason: no call, no failure.
- silent outcomes = -. Bar and why: the preflight only records what is installed; it states nothing about deep that could be wrong.

**W1. Chat, five turns on the deep backend** (PASS)

- turn 1 (short): answered
- turn 2 (long): Inference failed: backend `mlx-native` failed: apply_pre_block: Qwen2Model::forward (prefill head): "[rms_n...
- turn 3 (short-repeat): Inference failed: backend `mlx-native` failed: apply_pre_block: Qwen2Model::forward (prefill head): "[rms_n...
- turn 4 (long-shared-prefix): Inference failed: backend `mlx-native` failed: apply_pre_block: Qwen2Model::forward (prefill head): "[rms_n...
- turn 5 (short-2): Inference failed: backend `mlx-native` failed: apply_pre_block: Qwen2Model::forward (prefill head): "[rms_n...
- 18 checks, all ok.
- deep calls attempted = 5. Bar and why: 5: Chat is the operator's own pick, so every turn calls deep, even after a failure. It never falls back to the fast model on its own.
- deep calls failed = 4. Bar and why: 4: calls 2 to 5. The day-1 wheel answers once per process (DAY1.json).
- silent outcomes = 0. Bar and why: 0, because any one is the bug this sprint fixes: ARAIL said deep was fine, or the work done, when it was not.

**W2. background agents, five calls (Buddy 2, debt advisor 2, consolidation analyzer 1)** (PASS)

- opened the background gate with the portal's own endpoints: window override heavy accepted, profile throughput accepted
- call 1, buddy: ok; the agent got text
- call 2, buddy: error then error [stamped deep_failing] then error [stamped deep_failing]; the agent got no text
- call 3, debt_advisor: error [stamped deep_failing] then error [stamped deep_failing]; the agent got no text
- call 4, debt_advisor: error [stamped deep_failing] then error [stamped deep_failing]; the agent got no text
- call 5, consolidation_analyzer: error [stamped deep_failing] then error [stamped deep_failing]; the agent got no text
- the fast model did not answer (HTTPError). Tier 0 is pointed at a model name that does not exist, on purpose: it answers nothing and Ollama never loads one of yours
- 14 checks, all ok.
- deep calls attempted = 2. Bar and why: 2: call 1 answers, call 2 fails and is recorded, then deep is marked failing and calls 3 to 5 do not try it. 5 would mean agents kept calling a build ARAIL knew was failing.
- deep calls failed = 1. Bar and why: 1: only call 2 meets the bug; after it the agents stop calling deep.
- silent outcomes = 0. Bar and why: 0, because any one is the bug this sprint fixes: ARAIL said deep was fine, or the work done, when it was not.

**W3. Curator review of a scratch 6-term World** (PASS)

- the Curator says: Curator review of 'Math World' is partial: 1 of 6 terms judged. A full review judges all 6; the 5 unjudged terms have no flags, so their silence means nothing. QueueLLM failed on 5 calls: RuntimeError: backend `mlx-na...
- 15 checks, all ok.
- deep calls attempted = 6. Bar and why: 6: one judge call per term, and the World has 6 terms (the Curator reviews up to 16 per pass).
- deep calls failed = 5. Bar and why: 5: calls 2 to 6. The review must say judged 1 of 6, not 'All clear'.
- silent outcomes = 0. Bar and why: 0, because any one is the bug this sprint fixes: ARAIL said deep was fine, or the work done, when it was not.

**W4. Curator grow of the same World** (PASS)

- the Curator says: Growth pass on 'Math World' not applied: QueueLLM failed on 6 of 7 calls (RuntimeError: backend `mlx-native` failed: apply_pre_block: Qwen2Model::forward (prefill head): "[rms_norm] (*weight) must have the same size a...
- 15 checks, all ok.
- deep calls attempted = 7. Bar and why: 2 or more: a growth pass reviews terms and then proposes new ones, so the count depends on the World. The bar is that every call is counted.
- deep calls failed = 6. Bar and why: the calls minus 1: the wheel answers once per process, so every call after the first fails, and a pass with any failed call must change nothing (state not_applied).
- silent outcomes = 0. Bar and why: 0, because any one is the bug this sprint fixes: ARAIL said deep was fine, or the work done, when it was not.

**W5. ./arailctl deep probe --json on a fresh state folder** (PASS)

- the probe said: fail; first error: RuntimeError: backend `mlx-native` failed: apply_pre_block: Qwen2Model::forward (prefill head): "[rms_norm] (*weight) must have the same size as th...
- 9 checks, all ok.
- deep calls attempted = 5. Bar and why: 5: the probe makes five sequential calls in one fresh process (its bar and its reason are in the probe's own output).
- deep calls failed = 4. Bar and why: 4: calls 2 to 5, as in DAY1.json. The probe must exit 1 and record it.
- silent outcomes = 0. Bar and why: 0, because any one is the bug this sprint fixes: ARAIL said deep was fine, or the work done, when it was not.

**W6. a fresh process on W5's state: every surface, then Check** (PASS)

- 10 checks, all ok.
- deep calls attempted = 0. Bar and why: 0: a fresh process that only reads state and presses Check must not generate. A call here would spend tokens on a build known to fail.
- deep calls failed = 0. Bar and why: 0, for the same reason: no call, no failure.
- silent outcomes = 0. Bar and why: 0, because any one is the bug this sprint fixes: ARAIL said deep was fine, or the work done, when it was not.

**W7. deep status, and the build script's refusals (deep rebuild and auto)** (PASS)

- channel line: unknown — pip package aerollm-api 0.1.0 (editable, file:///Users/netsushi/ProJects/aerollm/crates/aerollm-api), but its binary was replaced outside pip (RECORD 80bfe0f...
- ./arailctl deep rebuild is only `exec bash scripts/build-aerollm.sh build`, so this row runs that line; the rule for this build is not to run ./arailctl deep rebuild itself
- 20 checks, all ok.
- deep calls attempted = -. Bar and why: the build and status scripts make no deep call, and a call made in a subprocess cannot be counted from here.
- deep calls failed = -. Bar and why: the build and status scripts make no deep call, and a call made in a subprocess cannot be counted from here.
- silent outcomes = 0. Bar and why: 0: none of five false claims may appear in the three outputs: the old 'No QueueLLM sibling repo' and 'QueueLLM ready' lines, the two '(... channel)' guesses, and a channel line that names release or dev without proof.

**W8. postflight: nothing of the operator's changed** (PASS)

- 5 checks, all ok.
- deep calls attempted = -. Bar and why: the postflight compares files and git state; it generates nothing.
- deep calls failed = -. Bar and why: the postflight compares files and git state; it generates nothing.
- silent outcomes = -. Bar and why: the postflight is a comparison with the preflight, not a statement about deep.

### Total time

60 s. What it is: wall time of the whole check, from the first row to the last. Bar and why: under 5 minutes: nine rows, and the longest are the model loads and the probe (the first two live runs took 64 s and 141 s). Out of range: much longer: a row waited for a deadline, which means a call hung. The fault lives in: the slowest row; its log is in the scratch root when ARAIL_WIN_KEEP=1.

### Can the check fail? Six product mutations

The dry run (fake runtime, a copy of the repo with one product line broken) must exit 1 in the rows that watch that line. Run by hand against a copy; not part of the suite.

| Mutation | Broken line | Rows that failed (silent outcomes) |
|---|---|---|
| M1 | `deep_policy.explain()` never returns `deep_failing` | W1 (3), W2 (5), W3 (3), W4 (3), W6 (3) |
| M2 | the tier-1 health overlay returns nothing (Check shows healthy again) | W1 (2), W2 (1), W3 (2), W4 (2), W6 (2) |
| M3 | `_run_grow` reseals and logs even after failed calls | W4 (3) |
| M4 | `_review_report` treats every review as complete | W3 (2) |
| M5 | the build script prints "QueueLLM ready" on a refusal | W7 (1) |
| M6 | `deep_verdict.note_call` records nothing | W1 (8), W2 (9), W3 (9), W4 (9) |


## Deltas from the plan

1. **Step 1a and 1b are one commit** (`bc28c84f`): the fake runtime and the store's tests need each other. **Step 1c is its own commit** (`6ebabfbd`), a row the plan did not have, so the 1,200-line store stays reviewable.
2. **Commits the plan did not list:** `0f3e7ec7` (unused imports), `b15b0b6c` (a leak of the fake runtime into `tests/test_aerollm_defaults.py`, found by diffing the step 5 full run against the baseline: a `monkeypatch.delitem` restores `sys.modules` entries after the fake's own teardown, so the fake stayed importable; fixed by purging it at the start of every test, and pinned by `tests/test_fake_deep_isolation.py`) and `ef2f6798` (step 6c: two dead route handlers left behind by 6a, found while writing the live check; a duplicate-definition scan of every changed file now reports none), `77cf8b75` (step 6d: the Curator tests name their activity line by its text; see Final state), `f1dff0ec` (step 8d: stray processes) and `ebdbaa23` (step 8e: the final recorded run).
3. **Files touched that the plan table does not list**, each for a reason:
   - `src/arail/portal/static/research.css` and `static/knowledge.css`: the danger class the truth strip uses for `deep_failing`, and the style of the World Terms notice.
   - `tests/test_qa_buddy.py` (two stubs): `complete_preferring_deep()` now takes its decision from one `explain()` walk, so that the UI's explanation and the agents' decision cannot drift (the architecture's rule). A stub of `prefer_deep` no longer reaches it; the stubs now replace `explain`. What the tests assert is unchanged.
   - `tests/test_world_forge_api.py` (two tests): forge is handed `_CountedRouter` (C6), so the assertion checks the wrapper and the router behind it; the review stand-in makes the judge calls a real review makes, because a review now counts as done only when those calls returned.
   - `tests/conftest.py`: the per-test verdict folder, the purge of a leaked fake runtime, and the session guard (F23).
   - New test files the table does not name: `test_fake_deep_runtime.py`, `test_fake_deep_isolation.py`, `test_deep_verdict_cli.py`, `test_deep_probe_cli.py`, `test_deep_probe_prompts.py`, `test_world_terms_notices.py`, `test_install_hints_honest.py`, `registry/test_deep_loaded_lines.py`, `test_deep_honest_win.py`.
4. **No edit to `tests/test_aerollm_*` was needed.** The plan expected assertions that look for "QueueLLM ready" to change; none exist in those files.
5. **The live check: three choices the architecture left open** (all in `6979950a`):
   - The portal is driven by an httpx ASGI client on one event loop with no lifespan, not `TestClient`. A plain `TestClient` gives every request its own loop and the Curator's background task dies with the request; `with TestClient` keeps a loop but runs the lifespan, which starts the portal's own deep callers and breaks "the consumer under test is the only deep caller". `httpx.ASGITransport` (what `TestClient` is built on) on one loop has neither problem, and requests still pass the real middleware: host allow-list, bearer check, CSRF.
   - Tier 0 (the fast model) is set to Ollama's native backend with a model name that does not exist (`MODEL_BACKEND=ollama_native`, `MODEL_NAME=arail-deep-honest-no-such-model`), as constants in the row environment, not copied from `.env`. With only the four copied keys, tier 0 seeds as an `mlx` backend that has no router on many machines (the dry run showed it): no fast call is made and the `deep_failing` stamp can never be seen, so W2 could not run. Now a fast call is always made and traced, never answers, and Ollama never loads one of the operator's models.
   - W7 runs `bash scripts/build-aerollm.sh build` and `auto`, not `./arailctl deep rebuild`, because this build's rules forbid running that command. The row checks that `arailctl` dispatches `rebuild` to exactly that line. A stub `cargo` (records and fails; called 0 times), an empty scratch venv with no pip and an absent bundle file make a build, an install or a download impossible even if the script were wrong.
6. **Two driver flags exist only for the dry run:** `--extra-env KEY=VALUE` and `--pythonpath-prepend DIR` (how the fake runtime gets in front). The wrapper does not use them.
7. **The time bar moved.** The architecture said "about 3 to 5 minutes". The four live runs took 64 s, 141 s, 55 s and 60 s, so the teaching now says "under 5 minutes" and names the first two runs (`06e58b9a`).
8. **TD7 is reworded** in the backlog (an ASGI client, not `TestClient`) and **TD13 is added**: four hints still say "run ./arailctl deep rebuild" (`portal/app.py:2110`, `:8821`, `:10134`, `portal/templates/chat.html:2803`). Against a renamed checkout that command now refuses. They are outside the architecture's list, so they are filed, not changed. (The review ruled TD13 "fix now"; it is repaid in the fix pass, item 2.)

## Architect feedback required

No gap stopped the build, and nothing here needs a new design pass. These are the places where the text of ARCHITECTURE.md and the machine disagreed; each was settled inside the harness, and the architect may want the text to match.

1. **W2 needs a fast router, and the four-key `.env` does not give one.** "Fast calls stamped `deep_failing`, whether or not Ollama answers" holds only if a fast call is made. Delta 5, second bullet. Suggested text for "Inputs": the row environment also sets `MODEL_BACKEND=ollama_native` and a model name that does not exist.
2. **W7 names `deep rebuild`; this build's rules forbid running it.** Delta 5, third bullet. Suggested text: "`build-aerollm.sh build` (what `deep rebuild` execs) and `auto`".
3. **"Drives the app with `TestClient` and no lifespan" (the live-check text and TD7) is not possible for W3 and W4.** Delta 5, first bullet.
4. **A failed fast call writes two stamped trace records, not one.** The agents' fast path streams first (`ARAIL_AGENT_STREAM_FAST`, default on) and, when the stream raises, calls `complete()` as before. Both go through the router chokepoint, so a reader counting traces sees two per failed fast call (W2: 8 stamped records for 4 fast calls). Every one is stamped; the count of records per call is not fixed. The tests assert "every fast record is stamped", never a count.
5. **The live wheel matches `DAY1.json` exactly.** Same sha256 (`d62e1e16...`), same version (0.1.0-rc.2), first error at call 2 with the same `rms_norm` text. W5 checks all three against `DAY1.json` itself, so no hash is typed in twice.
6. **TD13** (above) is a find the architecture's cut list did not cover.


## Final state

This is the state at the end of the original build (`ebdbaa23`, before the review). The fix pass has its own numbers and its own pre-handoff checks at the end of this file.

### The full suite, against the baseline

Scratch venv (Python 3.11.17), working directory the worktree, `run-suite.sh` (a scratch `HOME` and `TMPDIR`, `ARAIL_SKIP_OLLAMA=1`), the same command for all three runs.

| Run | Failed | Passed | Skipped | xfailed | Errors | Time |
|---|---|---|---|---|---|---|
| `origin/main` `62d5ebdf` (baseline of record, a clone) | 120 | 7,797 | 28 | 28 | 7 | 23 min 27 s (machine busy) |
| branch at `8671c955`, first full run | 121 | 8,082 | 28 | 28 | 7 | 11 min 55 s |
| **branch at `ebdbaa23`, the final tree** | **120** | **8,085** | **28** | **28** | **7** | **11 min 38 s** |

- **New failures against the baseline: 0.** The 127 failing or erroring ids (120 failed, 7 errors) are the same ids as the baseline's, compared by id. No baseline failure was fixed and none changed kind.
- The first full run had one new failure, mine: `test_c3_a_complete_review_with_a_flag_is_a_warning_without_all_clear` took the first "Curator reviewed" line in the activity ring and got a "success" line instead of its own "warn" line. It passed in 14 isolated reruns (8 of them under CPU load) and in 25 reruns of the whole old file, so **the source of the stale line was not found**. The cause class is plain: the ring is one per process and the test chose its line by position. Step 6d (`77cf8b75`) makes every Curator test name its line by the review's own message and wait for it, and assert an absence only after the line it should be absent beside exists; a miss now prints the newest lines it found. The final run is clean. If the failure comes back it will say what the ring held.
- **+288 passed** is exactly the 288 tests in the 18 new test files: `test_deep_probe` 47, `test_deep_verdict` 39, `test_deep_honest_win` 32, `test_deep_verdict_cli` 28, `test_curator_honest` 28, `test_build_aerollm_honest` 26, `registry/test_deep_verdict_overlay` 21, `test_deep_policy_failing` 14, `test_fake_deep_runtime` 11, `test_deep_backend_hook` 11, `test_research_truth_deep_failing` 9, `test_deep_probe_cli` 6, `test_deep_probe_prompts` 4, `test_install_hints_honest` 3, `test_fake_deep_isolation` 3, `test_world_terms_notices` 2, `test_chat_deep_stays_loud` 2, `registry/test_deep_loaded_lines` 2. Skipped and xfailed are unchanged.

### The baseline failures (reported, not hidden)

127 ids in 43 modules fail or error on untouched `origin/main` in the same scratch venv, and fail identically on this branch. I did not diagnose them; none is in code this sprint touched. By module (count): `setup_ladder/test_setup_ladder.py` 19, `test_portal_login.py` 16, `test_pkb_index_qa.py` 15, `test_aerollm_defaults.py` 9, `setup_ladder/test_setup_ladder_qa_nomic.py` 6, `test_portal_auth_routes.py` 5, `test_model_defaults.py` 4, `test_w9_embedder_swap.py` 4, `test_docs_sprint3_qa.py` 3, `test_loader_skills_only_agents.py` 3, `test_pkb_index_perf.py` 3, `test_build_qkz_corpus.py` 2, `test_mlx_memory_safety.py` 2, `test_pkb_index.py` 2, `test_queuellm_rename_frozen_surface.py` 2, `test_reset_stop_scope.py` 2, `test_s4_qa_paranoid.py` 2, `test_skills_fold_into_agents.py` 2, `test_swarm_goal_surfaces.py` 2, `nucleus/test_build_phases.py` 1, `nucleus/test_phases.py` 1, `portal/test_models_api.py` 1, `portal/test_opencode_config_lifecycle.py` 1, `portal/test_opencode_lifecycle.py` 1, `portal/test_token_compliance.py` 1, `test_aerollm_bundle_install.py` 1, `test_aerollm_compute_source.py` 1, `test_arailctl_auth.py` 1, `test_autochecks_boot.py` 1, `test_cache_prewarm.py` 1, `test_dac_rename.py` 1, `test_dashboard_layout_v2.py` 1, `test_default_deep_backend_resolver.py` 1, `test_docs_ingest.py` 1, `test_ensure_ready_build_isolation.py` 1, `test_instance_isolation_audit.py` 1, `test_model_hosting_reframe_qa.py` 1, `test_no_user_visible_aerollm.py` 1, `test_pkb_reembed.py` 1, `test_qa_retest_fix_loop.py` 1, `test_qa_retrieval_honesty.py` 1, `test_recap_core.py` 1, `test_runtime_profile_api.py` 1.

Three are worth naming because the architecture lists their neighbours. `tests/test_no_user_visible_aerollm.py` has one violation on the baseline and the same one on the branch (`src/arail/model_resolve.py:290: aerollm runtime is not installed`, not this sprint's). `tests/test_aerollm_bundle_install.py::test_package_script_refuses_dirty_worktree_without_allow_dirty` fails on the baseline too. The 7 errors are `ModuleNotFoundError: No module named 'aerollm_api'` at `tests/test_aerollm_defaults.py:65`: the scratch venv has no wheel.

### The regression list in ARCHITECTURE.md

| Check | Result |
|---|---|
| `tests/test_deep_policy*.py` (codes unchanged), `tests/test_world_growth.py`, `tests/test_aerollm_bundle_*.py`, `tests/test_aerollm_local_sibling_build.py` | no new failure; the one baseline failure in `test_aerollm_bundle_install.py` is the same id |
| `tests/test_no_user_visible_aerollm.py` | the same single pre-existing violation, nothing new |
| `tests/test_host_contract_vendor.py` (Buddy's contract bytes) | passes (54 passed, 1 skipped, 1 xfailed with `test_qa_win_conditions_and_hotpath.py`, alone) |
| the three load-sensitive tests in `test_qa_win_conditions_and_hotpath.py` | pass in the full runs and alone on a quiet machine |
| `src/dac_world/` byte-identical to `62d5ebdf` | `git diff --quiet 62d5ebdf -- src/dac_world` passes; row W8 checks it too |
| F23: no test wrote the checkout's real verdict | the session guard did not fire |

### Every failure mode in ARCHITECTURE.md has a test

F1 to F31 each map to tests that exist and pass: F1 U5, P2, C1, C4, Q2 and live rows W1 to W6; F2 Q3; F3 Q4; F4 U3; F5 U4, Q13; F6 Q7; F7 U1, Q12, S4; F8 S4, S6, S7; F9 S2, S8 and W7; F10 S5; F11 U2; F12 U8, Q17; F13 H1 to H6, H8; F14 U6; F15 U5; F16 C6; F17 C4; F18 C1b; F19 Q18, U12; F20 Q9; F21 Q19; F22 Q8; F23 U13; F24 SEC3; F25 SEC2; F26 `test_no_user_visible_aerollm.py`; F27 U5, U6; F28 Q1; F29 TQ1 to TQ4; F30 W8; F31 H10. A script matched every id in the architecture's table against the test names: none missing. TD4 (F15) is filed, not tested, by design.

### Size

| | Files | Added | Removed |
|---|---|---|---|
| Product code and docs (`src/`, `arailctl`, `docs/cli.md`, `build-aerollm.sh`, `setup.sh`) | 19 | 3,257 | 149 |
| The live check (`scripts/deep-honest-win.sh`, `scripts/deep_honest_win.py`) | 2 | 1,353 | 0 |
| Tests (24 files; 18 new test files and the fakes, plus `conftest.py` and two adapted tests) | 24 | 4,856 | 4 |

Builder commits: 29 with this one (28 before it), from `818ea0bf` to this commit, on top of the four planning commits. Coverage delta: not measured; the suite has no coverage run.

### Pre-handoff checks

- All steps 0 to 9 are in the plan table with a commit, and every added commit is listed.
- No commented-out code and no TODO, FIXME or HACK in the added lines (the only "XXX" is the `mktemp` template). `git diff --check` is clean. `pyflakes` reports no new warning in any product file (the one in `deep_probe.py` is an import kept for its side effect, `import arail.config  # noqa: F401`, which loads `.env` as the portal does) and, in tests, only the fixture-import pattern (`# noqa: F401`). `shellcheck` is clean on `deep-honest-win.sh`. Both new scripts are mode 755 in git.
- No frozen name lost an occurrence in any modified file. The word "free" appears only in "token-free" and as a verb ("free memory").
- Nothing was pushed.

### Cleanup

Removed: the scratch venv and its package cache, the baseline source copies and their output, every scratch root the live runs and dry runs made, the mutation-check copy of the repo, and every temp file; the git-ignored files the suite and the runs wrote into the worktree (`lab/`, `logs/`, `__pycache__`); no `yes`, probe, pytest or harness process was left running. The operator's `.venv`, `.env`, Worlds and the QueueLLM checkout were read, never written, as shown by the before/after comparison of the live runs. The scratch folder was removed after this commit.

## Fix pass (after the review)

The architect's review (`REVIEW.md`, commit `46ebcc30`) returned **BLOCK** on B1 and B2. It also listed five weak points (W1 to W5) and twelve notes (N1 to N12). This pass fixes the items the coordinator's fix-pass message named and nothing else. Each fix is its own commit with its test in the same commit. The pass starts at `0f2f8d25` and adds 12 commits, then one more that writes this section. The 12 change 26 files: 10 product, script and doc files (+210 and -60 lines), 14 test files (+606 and -19), and two sprint records. No new test file was needed. Nothing was pushed. `SPRINT.md` was not edited. No frozen name was renamed.

| # | Item | Commit | New tests (collected) |
|---|---|---|---|
| 1 | B1: the hook files runtime faults only, and files a native panic | `82f57206` | 18: fake pin 1, hook 11, Curator 6 |
| 2 | B2 and TD13: no hint sends a user without a checkout to `deep rebuild` | `13cd7fac`, follow-up `66d1fc5d` | 3; four existing tests follow the new wording |
| 3 | W1: a bundle that fails to import no longer deletes the working install | `d2893be0` | 3 |
| 4 | W3: the probe's tag check and its getppid check each get a test | `857bc0ee` | 2 |
| 5 | W5: the research toggle no longer hides `deep_failing` | `e32efda3` | 1 (one test became two) |
| 6 | N3: a build the marker check cannot read is refused | `f57ebcec` | 1 |
| 7 | N4: a marker says "tree state unknown" when git cannot say | `bfa6e932` | 1, and one test extended |
| 8 | N8: a probe that passed 5 of 5 but could not save exits 1 | `d5a4de46` | 1; two existing tests follow |
| 9 | W4: every number is taught, and the texts teach the right thing | `ef8aec48` | 5; TQ1 and TQ2 extended |
| 10 | Backlog: TD14 to TD23 | `4c5386cd` | none |
| - | `LIVE_RUN.txt` from the second live run | `58af5cb9` | none |

That is 35 new tests in 13 test files (261 collected before, 296 now). The order of the commits is the order in the table, except that the follow-up `66d1fc5d` was made last, after a check of my own (item 2). Two commits were amended while the pass was running, before any hash was written down: B2 once for a neighbouring test it left red, and again after the referencing run found a second one. The hashes here are final.

### 1. B1: what the hook records (`82f57206`)

- **Changed.** `router/backends.py`: a new `_DeepBackendClosed(RuntimeError)`. `complete()` raises it before `submit()` when `self._closed` is true. `_generate_on_worker` raises it when `_close()` dropped the Runtime from under a call that was already queued. `submit()` moved out of the `try`, so an error from the executor itself (a closed executor, interpreter exit) is not filed. The `try` around `.result()` now catches `BaseException`. It files the failure unless the exception is `KeyboardInterrupt`, `SystemExit`, `GeneratorExit`, `concurrent.futures.CancelledError` or `_DeepBackendClosed`, and it re-raises the very object it caught. `portal/world_routes.py`: `_CountedRouter.complete` counts a non-`Exception` `BaseException` as a failed call and raises `RuntimeError(...) from exc`. `dac_world` skips that call, so the pass ends `partial` and `_review_state` does not stay at `running`. The three interruptions pass through untouched.
- **Tests (18).** The fake runtime has a `panic2` mode: call 1 answers, later calls raise a `PanicException` that derives from `BaseException`, as PyO3's does. It has its own pin test in `test_fake_deep_runtime.py`. `test_deep_backend_hook.py` has five tests of what is not filed or is: a call after `_close()`, a call whose Runtime was dropped, an error from `submit()` and a call cancelled by `shutdown(cancel_futures=True)` leave the verdict untested, and an empty Runtime on a backend that is still open is filed. A `BaseException` from `generate` is filed and re-raised as the same object (checked with `is`). The five excluded exceptions are not filed and are re-raised unchanged (one test, five cases). `test_curator_honest.py` has the counting wrapper on a panic, on each of the three interruptions, a review whose judge panics from call 2 (6 terms planned, 1 judged, 5 failed calls, state `partial`) and a grow pass whose model panics (`not_applied`, no file written).
- **Settled.** The discriminator is `self._closed`. `_runtime` starts as `None` and is filled on the worker thread by `_init_runtime`; `_close()` sets `_closed` first and then empties it on the worker. So an empty `_runtime` alone cannot tell ARAIL's teardown from a lost runtime. A Runtime that is missing while the backend is open is still filed ("the deep runtime is not running"), as a fault of this path. A load failure is still filed by `note_load_failure`, unchanged. The set that is not filed is the review's five, no more.
- **Can the tests fail?** Nine one-line breakages of the fix, made in a scratch copy, were each caught by at least one of them.
- **Not done.** Four things next to it, listed under "Seen and not changed" (1 to 4).

### 2. B2 and TD13: no hint sends a user without a checkout to `deep rebuild` (`13cd7fac`, `66d1fc5d`)

- **Changed.** `scripts/build-aerollm.sh`, `clone_hint` (the no-checkout message): "No QueueLLM checkout at X." then "A source build here needs a checkout that still has crates/aerollm-api; QueueLLM main has the renamed crates/queuellm-api, which this ARAIL refuses. Nothing was built or installed." then "For the prebuilt QueueLLM: ./arailctl deep install." The clone advice and the `deep update` suggestion (it needs private-index credentials) are gone. The four portal hints of TD13 now say `./arailctl deep install`: the compute-source error and the Chat error reply's `install_command` (with its comment) and the `/api/chat/models` `install_command` in `portal/app.py`, and the compare-mode notice in `chat.html` (now "isn't installed" where it said "isn't built"). `sprints/BACKLOG.md` marks TD13 repaid.
- **One more hint, found by the grep and fixed.** `scripts/upgrade.sh` printed "1. Build QueueLLM: ./arailctl deep rebuild" as the first step after an upgrade to maximus. It now says "1. Install QueueLLM: ./arailctl deep install". The message's grep scope was docs, templates and JS; this is a shell script. It is the same defect on the path a new user takes, so I fixed it here and say so.
- **Tests.** S1 and `test_build_fails_clearly_when_sibling_missing`, which both pinned the clone advice, now assert the new text and the absence of "git clone". `test_aerollm_compute_source.py:73` asserts `deep install`. `test_aerollm_model_ready.py` follows the new `chat.html` wording (it pinned "QueueLLM isn't built"). `test_install_hints_honest.py` has 3 new tests: the portal's two other hints, the Chat page's hint, and the upgrade hint.
- **Settled.** Text that describes `rebuild` as the maintainer's dev-build verb is left alone; the places are listed under "Seen and not changed" (8).
- **Follow-up `66d1fc5d`, found late by my own check.** Review item 12 says no frozen-name count may go down in a modified file. A script over the 25 modified files, compared with `62d5ebdf`, found one: the comment I rewrote in `portal/app.py` had dropped its only mention of the env var `ARAIL_AEROLLM_REPO`, so the count of `AEROLLM_` in that file was 23 against 24 on main. The comment names the variable again. It is a comment, so there is no test. The same script now finds no drop in any of `aerollm_api`, `AERO_`, `AEROLLM_`, `tier1-aerollm`, `AeroLLMBackend`, `build-aerollm.sh` or `backend="aerollm"`.

### 3. W1: a bundle that fails to import (`d2893be0`)

- **Confirmed first.** `verify_loaded` already handles "the import fails". With an empty `loaded_sha` it removes the new `.so` and its install record, puts the previous ones back from their backups, says "The file that was there before was put back." and deletes the temp copies. With no previous install it only removes the new files.
- **Changed.** The `if ! import_ok` block in `bundle_install` ran first, deleted both files and exited. It is gone, with a comment that says why.
- **Tests (3, darwin arm64 like the other bundle-path tests).** A new bundle whose `.so` the importer rejects is installed over a working one, once by a moved pin and once by `--force`: the old `.so` and its install record are back byte for byte, the message says the earlier file was put back, and no temp copy is left. A first install that fails to import leaves the site folder exactly as it was. The fake importer rejects a file that carries a marker; a helper builds a bundle tarball the script accepts (Mach-O magic, a matching MANIFEST). All three failed on the script as it was, and pass now.
- **Seen.** The removed block also printed "If this was a browser download, quarantine may be the cause (F5) — retry via setup." That line is gone with it ("Seen and not changed", 5).

### 4. W3: two layers that had no test of their own (`857bc0ee`)

- **(a) The `"probe": 1` tag check.** A unit test gives `_drive_child` a protocol stream with a forged `{"ev": "call", ...}` line that has no tag, one with the wrong tag and one that is not JSON, and asserts that none of them becomes an event or a result.
- **(b) The `getppid()` check: kept, and tested with the protocol reader kept alive.** The reason it is kept and not deleted: ARCHITECTURE F21 names that check as the mechanism, and EPIPE works only while no other process holds the read end of the pipe. The test holds the read end open in the test process (a middle process starts the child on the write end and is then killed). EPIPE cannot end the child. It asserts that the child finishes the call it was in, starts no other, and has written only identity, loaded and one call.
- **Can they fail?** Each test fails with its line removed. Q8 and Q19 pass in both cases, which is why the review could not see the gap.
- **Not done.** W3(c): `tests/test_no_user_visible_aerollm.py` is red on `origin/main` and on this branch (`model_resolve.py:290`), so it cannot flag a new string. It was not in the coordinator's list.

### 5. W5: the research toggle (`e32efda3`)

- **Changed.** One condition in `_research_models_block` (and its docstring): the override that rewrites the reason to "disabled" now skips `deep_failing`, as it already skipped `tier_locked` and `wheel_missing`. The toggle's own state is still reported in `enabled_by_user`.
- **Tests.** The test that pinned the old precedence ("the toggle still wins") is split in two. Toggle off and deep not failing still says "disabled" with the toggle text. Toggle off and deep failing says `deep_failing`, carries the verdict's detail, does not say "flip the toggle", and the page paints that code in the danger class. The second test fails without the change.

### 6. N3, N4 and N8

- **N3 (`f57ebcec`).** The preamble check counts the Metal error marker (`downloadComponent`) with `grep -c`. If grep itself fails (an unreadable file) it prints nothing, and `${poison:-0}` turned that into 0, so a build the script had not checked was installed. Empty output is now a refusal that names the file ("Could not read ... to check it ... Not installed."). The test gives the fake cargo an artifact left at mode 000 (skipped when run as root, which can read it). Without the change the build is installed.
- **N4 (`bfa6e932`).** `write_build_marker` wrote `queuellm_dirty: false` whenever `git status --porcelain` printed nothing, including when git failed. A failed `git status` now writes `null`. The reader had the same blind spot: `deep status` showed ", dirty tree" for true and nothing otherwise. It now says ", tree state unknown" for `null` (a clean tree still says nothing extra). Tests: a checkout that is a plain folder gets commit "unknown" and dirty `null` (the fake-cargo harness now sets `GIT_CEILING_DIRECTORIES` so a parent repo cannot be found), and the channel line for clean, dirty and unknown markers. Each fails with its half of the change removed.
- **N8 (`d5a4de46`): exit 1, not "document 0". Why.** The review gave two options. Exit 1 is the smaller one: C3, `docs/cli.md` and the `exit_code` teaching all say exit 0 means "recorded passing", and "only 0 clears a recorded failure". Documenting 0 would have made that sentence false. Now the exit code is 0 only when the pass was recorded and no failure survives. The activity line for a 5 of 5 that could not be saved is a warning that says nothing was recorded and a failure already on record is not cleared (it used to say "Deep is cleared for agents and the Curator"). The NOT SAVED line says in words that the probe exits non-zero. `docs/cli.md` (exit 1 has a third case), the `exit_code` teaching (meaning, out of range, where the fault lives), the usage line and the module docstring say the same. The new test fails on both halves of the change; Q17 and the `--help` test follow the usage line.

### 7. W4: every number is taught (`ef8aec48`)

- **(a)** The live check said every row's counts come from "ARAIL's per-process deep counters, not by this script". For W5 they are the probe's own, read from its JSON (`total`, and `total` minus `passed`). W5 now has its own "what it is". A `COUNT_SOURCE` table says which rows count in process, which copy the probe's numbers and which count nothing, and a test holds each row's text to that source and the source to the row's code.
- **(b)** W0 printed "11 aerollm or queuellm files recorded, 455 top-level entries" with no meaning, bar or range. It now says what it recorded for W8 to compare, in words, with no bare count.
- **(c)** W4 printed "Curator 7/6, backend 7/6". It now says "the Curator said attempted 7, failed 6; the backend counted attempted 7, failed 6". The new guard found one more line the review did not name, W2's "Tier 0 is pointed at ..." (a bare 0). It now says "The fast tier is pointed at ...".
- **(d)** `deep status`'s detail and the probe's "recorded failing" line said only a probe of "a new build" clears a failure. A 5 of 5 probe of the same build clears it too (after a one-off failure, TD4, and after a false one). Both now say "until ./arailctl deep probe passes 5/5 on this build".
- **The legend.** The probe's legend was headed by JSON paths (`calls[].seconds`, `first_error.call`). It is now headed by the label printed on the lines ("seconds (the s column of each call line)", "first error at call", "tokens in", and so on). A `LABELS` table holds each heading and the words printed beside the number, and `_Nums` records which line each number landed on.
- **TQ1 to TQ4, kept green and extended so that a number printed without its parts fails the build.** TQ1 checks the label table is complete. TQ2 checks that every printed number sits on a line that carries its label's words, and that the legend uses the headings. TQ4 has four new tests. The live check's dry run now fails on a bare number in an evidence line (quoted product text, digests, paths and versions are exempt) and on an unlabelled ratio in a check (the product's own "last probe 1/5" is exempt). Each addition fails without its change. I also ran the guard over the real output of the second live run: no bare number and no unlabelled ratio.

### 8. Backlog (`4c5386cd`)

`sprints/BACKLOG.md` files the ten findings this pass does not fix, each with the event that should pull it: TD14 (W2, the in-place copy of the `.so`), TD15 (N1, the Curator's order of state and activity line), TD16 (N2, wall-clock comparison in `record_probe`), TD17 (N5, the verdict writer's lock), TD18 (N6, the probe's signals), TD19 (N7, stale temp files), TD20 (N9, identity from `__file__`), TD21 (N10, `./arailctl status`), TD22 (N11, the live check's fast stand-in) and TD23 (N12, the conftest bearer patch under a newer Starlette). TD13 was marked repaid in item 2's commit.

### Running the tests

- **Environment.** The scratch venv the reviewer pinned (fastapi 0.140.7, starlette 1.3.1, httpx 0.28.1, no `httpx2`), a scratch `HOME` and `TMPDIR`, `ARAIL_SKIP_OLLAMA=1`. I did not rebuild it, upgrade it or delete it. It has no `pytest-timeout`, and `openai` 3.42.0 in it imports `httpx2`, so `tests/test_host_edge_brain.py` fails at collection (on the base copy too); every run used `--continue-on-collection-errors`.
- **What was run.** Every new test file, every test file that names `router/backends.py`, `AeroLLMBackend`, `world_routes`, `build-aerollm`, `deep_probe`, `deep_verdict`, `deep_policy` or `deep_honest_win`, every `tests/test_aerollm_*`, and the test files named `test_deep_*` or `test_default_deep_*`: 99 files. (My first list missed 8 of them, among them `test_build_aerollm_honest.py`, which this pass changed. I found that by comparing the list with a grep before writing this section, and ran them. Their result is in the table.)
- **Baseline for this pass.** A `git archive` copy of `0f2f8d25` in the same venv. It has no `.git`, like the first baseline run of this build, so the tests that ask git something behave differently: 4 ids fail only there, and 2 (`test_queuellm_rename_frozen_surface.py`) fail only in the worktree. Both are known from the first baseline (see above).

| Run | Files | Failed | Passed | Skipped | xfailed | Errors | Time |
|---|---|---|---|---|---|---|---|
| base copy of `0f2f8d25`, the first list | 91 | 31 | 1,626 | 11 | 19 | 26 | 4 min 10 s |
| base copy, the 8 files the first list missed | 8 | 1 | 108 | 1 | 0 | 0 | 12 s |
| **final tree `66d1fc5d`, all 99 files in one run** | 99 | **30** | **1,775** | **8** | **19** | **26** | 4 min 25 s |

- **New failures against the base copy: 0.** The final run has 56 failing ids (30 failed, 26 errors). They are the same ids as in my earlier runs on this tree. Against the base copy's 58 ids they differ in two ways, both caused by the copy having no `.git`. Four ids fail only in the copy: `test_experiments.py` twice (`git rev-parse HEAD` exits 128) and `test_deep_honest_win.py` twice (the dry run's W8 cannot run `git diff --quiet`, so the dry run is inconclusive). Two ids fail only in the worktree: `test_queuellm_rename_frozen_surface.py` twice, which need git history and fail on `origin/main` too (see above and "Seen and not changed", 9). The other 54 ids are the same in both.
- **New tests: 35.** Collected, the 99 files held 1,821 tests before and hold 1,856 now. By file: `test_deep_backend_hook` 11 to 22, `test_build_aerollm_honest` 26 to 31, `test_curator_honest` 28 to 34, `test_deep_honest_win` 32 to 36, `test_deep_probe` 47 to 51, `test_install_hints_honest` 3 to 6, `test_fake_deep_runtime` 11 to 12, `test_research_truth_deep_failing` 9 to 10. All eight are among the 18 new files, which now hold 323 tests (288 and 35).
- **Not run.** The full suite (the coordinator asked for the referencing files). The run in the table is on the tree after the follow-up commit, which only changed a comment.

### The second live run

The check was re-run once at the end, under the same rules as before: the operator's venv interpreter read only with `PYTHONDONTWRITEBYTECODE=1`, four non-secret keys copied from the operator's `.env`, a scratch root, one fresh child process per row. **9 of 9 rows pass** and the total time is 58 s (the bar is under 5 minutes). `LIVE_RUN.txt` is refreshed (`58af5cb9`) because W4 changed what the check prints.

| Row | Deep calls attempted | Deep calls failed | Silent outcomes |
|---|---|---|---|
| W1 Chat, five turns | 5 | 4 | 0 |
| W2 background agents | 2 | 1 | 0 |
| W3 Curator review | 6 | 5 | 0 |
| W4 Curator grow | 7 | 6 | 0 |
| W5 deep probe | 5 | 4 | 0 |
| W6 fresh process | 0 | 0 | 0 |

These are the same numbers as the first live run. The fix pass changes what is filed in cases the live check never creates (a teardown, a native panic), so on today's wheel the counts should not move, and they did not: it answers call 1 and fails calls 2 to 5, as `DAY1.json` says. A silent outcome is a deep failure that some surface reported as fine or done; the bar is 0 in every row.

- **The operator's systems did not change.** A before and after snapshot taken outside the harness agrees with W8: the `aerollm` entries in the operator's site-packages (names, sizes, mtimes, sha256 `d62e1e16e93c...`), its top-level listing, the QueueLLM checkout (clean tree), the crash-report folder and the operator's arail checkout status were identical, no file in the operator's `.venv` was newer than the run's start, and no probe or harness process was left.
- **The QueueLLM checkout's HEAD moved between the two live runs**, from `e730d5fe3f6b` to `e6346967e537`. This build does not write to that checkout, so someone else's commit moved it. W8 compares within one run, and the line in `LIVE_RUN.txt` shows the new HEAD.

### Seen and not changed

These are outside the coordinator's list, so I only record them.

1. **A native panic skips the router's trace.** `ModelRouter.complete` records an error outcome only for `Exception` (`router/core.py:306`). A `BaseException` from the deep backend passes the chokepoint with no trace record. The verdict now records the panic (B1), so every surface says failing. The trace is the one place that does not.
2. **The first panicking background call still reaches the agent.** `complete_preferring_deep` catches `Exception` (`agents/deep_policy.py:343`). A panic is not one, so that call propagates. The verdict records it, so later calls skip deep.
3. **`asyncio.CancelledError` is not in the excluded set, on purpose.** It cannot come out of a blocking `.result()`. The review's doubt 1 says the same.
4. **"Never started" cannot reach `complete()` in production.** A backend whose `__init__` failed is never returned to a caller. The branch in `_generate_on_worker` is defensive, and files the fault if it is ever reached. A `KeyboardInterrupt`, `SystemExit` or `GeneratorExit` through `_CountedRouter` leaves `attempted` incremented: it was an attempt, not a failure, and it ends the pass anyway.
5. **The quarantine hint is gone** with the W1 block (item 3). The strip of the quarantine attribute still runs before the check, and no test pinned the hint. If you want the line back, it is one `warn` in the empty-`loaded_sha` branch of `verify_loaded`.
6. **`upgrade.sh`** was fixed with B2 though the message's grep scope was docs, templates and JS (item 2).
7. **The review's N-items I did not fix** are in the backlog (item 8). **W3(c)** (`test_no_user_visible_aerollm.py` is red on base) stays.
8. **`deep rebuild` text left alone**, because it describes the maintainer's dev-build verb or follows `deep install`: `README.md:106`, `docs/cli.md:407`, `CHANGELOG.md:136`, the comments at `pyproject.toml:101` and `:259`, `scripts/setup.sh:689` (names `deep install` first) and `:695` (the CUDA "once QueueLLM supports it" line), `deep_verdict.py:1185` and the `ImportError` text at `router/backends.py:1616` (both name `deep install` first), and the build script's own refusal lines, which are about a checkout that exists. (The re-review then found `setup.sh:695` and the unqualified alternatives at `setup.sh:689` and `deep_verdict.py:1185` wrong for a user with nothing installed, and `docs/cli.md:407` and `README.md:106` too vague; fix pass 2 changed all five: V2, V3 and V4.)
9. **`tests/test_queuellm_rename_frozen_surface.py` is stale and fails on `origin/main`.** Its base is `5f775f1c`, from the display-rename sprint. Since then main moved: `pyproject.toml` has 5 more lines, and `portal/app.py` has 3 fewer `AEROLLM_MODEL` and 1 fewer `AeroLLMBackend` token than at that base. On top of that, this branch removes two `aerollm_version` tokens from `build-aerollm.sh` (the original build's rewrite of the `status` output; the shell function and the marker key are still there and still read). Nothing machine-read was renamed: the env var `ARAIL_AEROLLM_REPO` and the marker key `aerollm_version` are still read by the scripts. The review's item 12 counts against main, and against main no count went down (item 2, follow-up). The test could be re-based or retired; that is not in this pass.
10. **Order-dependent failure.** `test_qa_provider_dropdown_paranoid.py::test_rf_ctx_override_flows_into_ollama_dispatch_num_ctx` fails only in combined runs, on the base copy too.

### Pre-handoff checks for the fix pass (`0f2f8d25..HEAD`)

- Every item in the table has a commit, and every commit is atomic: one finding each, the test in the same commit (B2 and TD13 are one commit by instruction; W4's four parts and the legend are one commit by instruction; `66d1fc5d` and `58af5cb9` are single changes).
- `git diff --check` is clean for `0f2f8d25..HEAD` and for `62d5ebdf..HEAD`. No TODO, FIXME, XXX or HACK and no commented-out code in the added lines of the pass (one prose comment starts with the word "import"). A duplicate-definition scan of every changed Python file of the sprint reports none.
- `pyflakes`: no new warning in any product file (`backends.py` 2, `world_routes.py` 0, `app.py` 26, `deep_probe.py` 1, `deep_verdict.py` 0, `deep_honest_win.py` 0, each equal to the count at `0f2f8d25`). In tests the only increase is the fixture-import pattern ("redefinition of unused 'fake_deep'"), one per new test that takes the fixture: 11 to 18 in `test_deep_backend_hook.py`, 34 to 36 in `test_deep_probe.py`, 12 to 13 in `test_fake_deep_runtime.py`. `shellcheck`: `build-aerollm.sh` has no finding (as at `0f2f8d25` and on main); `upgrade.sh` has the same 2 findings as on main. `bash -n` passes on both.
- No frozen name lost an occurrence in any modified file against `62d5ebdf`: checked by script over the 25 modified files for `aerollm_api`, `AERO_`, `AEROLLM_`, `tier1-aerollm`, `AeroLLMBackend`, `build-aerollm.sh` and `backend="aerollm"`. It found one drop after `13cd7fac`, which `66d1fc5d` repaired. Against the review head `0f2f8d25`, `build-aerollm.sh` has one fewer `aerollm_api` and two fewer `AEROLLM_` (display text and a shell variable; the file still has more than main).
- `src/dac_world/` and `src/arail/buddy_host/` are byte-identical to `62d5ebdf`. No added line sends SIGABRT or aborts. No added line has the word "free". All four scripts are mode 755 in git.
- `tests/fakes/aerollm_api_template.py` changed only by the `panic2` mode and `PanicException`, which are test code.
- Nothing was pushed.

### Cleanup of the fix pass

Removed after this commit: my scratch folder (`build-fix`, with its lint venv, the base copy, the mutation copies, every output and temp file) and the git-ignored files the runs wrote into the worktree (`lab/`, `logs/`, `__pycache__`), without touching `src/arail.egg-info/`. Kept: the reviewer's scratch venv. The operator's `.venv`, `.env`, `lab/` and Worlds and the QueueLLM checkout were read, never written, as the before and after snapshot of the second live run shows. No pytest, probe, harness or `yes` process was left running.

## Fix pass 2 (after the re-review)

The re-review of the fix pass (`REVIEW.md`, "Re-review of the fix pass", commit `30495f82`) returned **WEAK_PASS** with no blocker. The coordinator decided what to fix now and what to file. This pass does that list and nothing else: V1 to V4, M2, M4, M5 and M7 are fixed, and the rest is filed or accepted below. It starts at `5ff035aa` (the coordinator's ledger commit on top of the reviewer's `30495f82`; the last code commit before it is `d071dc6c`). Each fix is its own commit with its test in the same commit. Nothing was pushed. `SPRINT.md` and `REVIEW.md` were not edited. QueueLLM, the operator's `.venv`, `.env` and `lab/` were not touched, no `deep rebuild`, `deep install` or `deep update` was run, and the live check was not run.

| # | Item | Commit | New tests (collected) |
|---|---|---|---|
| 1 | V1 and M2, and the double full stop: a pass whose calls ARAIL closed does not blame the deep runtime | `b6144556` | 11: Curator 10, hook 1 |
| 2 | V2: setup does not send a host with no QueueLLM build to `deep rebuild` | `bf700157` | 3 |
| 3 | V3: the maintainer routes say who can use them | `baaa2f84` | 1; two existing tests extended |
| 4 | V4: `docs/cli.md` and the README describe `auto` and the channel line as the code does | `9e2aa208` | 2 |
| 5 | M4: the build marker takes git's answer only for the checkout's own repository | `15bf987a` | 2 |
| 6 | M5: `ARAIL_AEROLLM_REPO` back in the clone hint, "aerollm" back in the `app.py` comment | `e3f1ef88` | 0; S1 extended |
| 7 | M7: U9 no longer depends on how busy the machine is | `ea1fd3e9` | 1 (`test_u9b`) |
| - | Backlog: TD24, TD25, and M3 and M8 on TD10 | `8a4d3573` | none |
| - | This section | the commit that adds it | none |

That is 20 new tests in 6 test files (160 collected at the start of the pass, 180 now). No new test file.

### Where this pass differs from the message, and why

1. **V1, one definition of "closed by ARAIL".** `CLOSED_BY_ARAIL` (`_DeepBackendClosed` and `concurrent.futures.CancelledError`) is defined once in `router/backends.py`. The hook's not-a-runtime-fault set is built from it and `portal/world_routes.py` imports it, so the hook and the Curator cannot disagree about what "closed" means. The message named the two exceptions; a test pins the construction.
2. **V1, which error a report quotes.** In a pass with both kinds of failure, `first_error` (quoted in the message and stored as `deep_error`) is the first failure that was not a closed call. Without that, a closed call that came first would be quoted as the evidence for "the fault is in the deep runtime". A test has the closed call first.
3. **V1, the wording of the all-closed report.** The review says "QueueLLM failed on 4 calls." and then the closed sentence. It does not quote the raw error: that error is the closed message, and it would say the same thing twice. The grow text drops its parenthesised error for the same reason. `deep_error` in `review.json` still holds it. `closed` stays inside `_CountedRouter`; no new number is added to the JSON, so no new teaching entry is needed.
4. **V1, the teaching.** The teaching entries for `failed` and `calls.failed` said "for QueueLLM run ./arailctl deep probe". Next to a message that says "run the pass again" that would teach the wrong thing, which the operator's standing rule forbids, so both entries name the closed case. It is a text change in a table, and it is not in the message's list.
5. **V3, the shape of the sentence in `setup.sh`.** The message's parenthesis does not fit there, because the sentence already has one open for "(bundled binary, ...)". It reads "... no source repo or credentials needed). Maintainers: deep rebuild needs a pre-rename checkout; deep update needs the private index." The `deep status` line uses the message's words exactly. The message said to update the tests that pin the old text; a search found none that pinned "(or: deep rebuild / deep update)" or the old `setup.sh` lines, so three tests now pin the new words (`test_cli_status_when_the_runtime_is_not_importable`, `test_s9_status_with_nothing_installed`, and the new `test_setup_failure_message_says_who_can_use_the_maintainer_routes`).
6. **V4, two more stale claims, found while checking every claim against the code.** (a) The page said a bare `./arailctl deep install` runs `build-aerollm.sh auto`. `arailctl:806` runs `bundle`; only `setup.sh` runs `auto`. (b) It said `AEROLLM_CHANNEL` forces a channel "regardless of what's on disk". `dev` still refuses a checkout that has no `crates/aerollm-api`, and only `auto` reads the variable. Both sit in the paragraph V4 rewrites, so both are fixed there. I also added the `crate:` line of `deep status`, which is the other half of the same state. V4 is a docs item, but it has two doc-pin tests, because the staleness came from an original-build page that nothing checked.
7. **M4, the test harness.** `Env.run` in `tests/test_build_aerollm_honest.py` sets `GIT_CEILING_DIRECTORIES` for every run (a folder is not a repository). A `None` value now removes a variable, so the new tests can run without it. The N4 test is unchanged and keeps the ceiling.
8. **M5, where the hint goes.** "A checkout elsewhere: set ARAIL_AEROLLM_REPO." is the second line of the clone hint, straight after "No QueueLLM checkout at X.".
9. **M7, a second test.** U9 now raises `LOCK_TIMEOUT_S` in its writers (and its wait to 300 s), which keeps its claim exact. That removes the only (accidental) coverage of the 2-second cap, so `test_u9b` pins the cap directly: a lock held past the cap leaves the failure in memory, sets `store_error`, writes nothing, and the next write after release lands.

### Running the tests

- **Environment.** The reviewer's pinned scratch venv (fastapi 0.140.7, starlette 1.3.1, httpx 0.28.1, no `httpx2`), a scratch `HOME` and `TMPDIR`, `ARAIL_SKIP_OLLAMA=1`. It was not rebuilt or upgraded. Every run used `--continue-on-collection-errors`, because `tests/test_host_edge_brain.py` still cannot be collected in that venv.
- **The six test files this pass touches** (`test_curator_honest`, `test_deep_backend_hook`, `test_install_hints_honest`, `test_deep_verdict_cli`, `test_build_aerollm_honest`, `test_deep_verdict`): 180 tests, **180 passed**.
- **The referencing set: 144 files.** I rebuilt it by grepping the tests for the modules and pages this pass changes (`router/backends`, `AeroLLMBackend`, `world_routes`, `_CountedRouter`, `deep_verdict`, `deep_probe`, `deep_policy`, `deep_honest_win`, `build-aerollm`, `setup.sh`, `upgrade.sh`, `docs/cli.md`, `README.md`), then added every `test_aerollm_*`, `test_deep_*` and `test_default_deep_*`, and the families the re-review named: swap and eject, `tests/router/test_aerollm_*`, the bundle tests, the research tests, the Curator neighbours, `tests/registry`, the frozen-name tests, `test_host_contract_vendor` and `test_cli_qa_edge`. It includes the six files above and `test_deep_honest_win.py`, so the dry-run guard ran. Compared by id with a fresh `git archive` of `0f2f8d25`, in the same venv, in parallel:

| Run | Collected | Failed | Passed | Skipped | xfailed | Errors | Time |
|---|---|---|---|---|---|---|---|
| archive copy of `0f2f8d25` | 2,342 | 60 | 2,221 | 17 | 20 | 26 | 6 min 16 s |
| **tree** (the tip before one test docstring was reworded; see below) | 2,397 | 57 | 2,283 | 13 | 20 | 26 | 6 min 27 s |

- **No new failure.** 86 ids fail or error in the archive copy, 83 in the tree, and 81 are in both. Five fail only in the archive copy, which has no `.git`, and each one calls git: `test_deep_honest_win.py` twice (the dry run's W8 cannot run `git diff`), `test_experiments.py` twice (`git rev-parse HEAD`) and `test_instance_secrets.py::test_git_check_ignore_covers_instance_secrets` (`git check-ignore`). The fifth is new to this list because the set now holds more readers of `setup.sh`; it is the `git check-ignore` test the first baseline named. Two fail only in the tree, `test_queuellm_rename_frozen_surface.py` twice (they need git history, and they fail on `origin/main` too). `test_no_user_visible_aerollm` fails in both (`model_resolve.py:290`). Nothing else differs.
- **The wording amend.** After these runs I reworded one word in the docstring of `test_m4_a_plain_folder_...` ("free to find the parent" became "able to find the parent", so the scan for the word "free" finds nothing) and replayed the three commits after it. The difference between the old tip and the new one is that one line. The six touched files were run again afterwards: 180 passed. The hashes above are the final ones.
- **M7, U9 under load.** The machine has 18 cores.
  - 8 busy-loop processes, three runs of the new U9: 3 passed, 0.34 to 0.36 s each. That is no contention for 8 writers, so it is the case the message asked for and not a stress test.
  - A stress test that does reproduce the failure: 150 busy-loop processes, and the test at background priority (`taskpolicy -b`), load average 150 to 210. With plain busy loops, even 150 of them, the old test passed in about 1.5 s. At background priority the **old U9 failed both times: 283 and 276 of 400** (117 and 124 writes lost to the 2-second cap; 102 s and 91 s). The **new U9 passed with exactly 400 in both runs I let finish: 112 s and 467 s.** I stopped before a third. The slowness is the starved writers waiting for each other, which is the point of the longer cap.
  - With the lock removed (a mutation), the new U9 fails: **102 of 400** writes survive.

### Mutations

Every new or changed test was checked the same way. The product line (or the doc line) was broken in a scratch copy of the worktree, the named tests were run, the test had to fail, and the copy was discarded. The worktree itself was never mutated.

| Mutation | What was broken | Caught by |
|---|---|---|
| V1-1 | a closed call is not counted as closed | `test_v1_a_review_whose_calls_were_closed_by_arail_does_not_blame_the_runtime[DeepBackendClosed]` |
| V1-2 | all_closed is true when any call was closed | `test_v1_one_real_failure_among_closed_calls_keeps_the_probe_advice_and_quotes_the_real_error` |
| V1-3 | the first error is quoted even when it was a closed call | `test_v1_one_real_failure_among_closed_calls_keeps_the_probe_advice_and_quotes_the_real_error` |
| V1-4 | the double full stop is back | `test_v1_a_report_does_not_double_the_full_stop_of_an_error_that_ends_with_one` |
| V1-5 | the review report ignores all_closed | `test_v1_a_review_whose_calls_were_closed_by_arail_does_not_blame_the_runtime[DeepBackendClosed]` |
| V1-6 | the grow report ignores all_closed | `test_v1_a_grow_pass_whose_calls_were_closed_by_arail_is_not_applied_and_does_not_blame_the_runtime` |
| V1-7 | the teaching no longer names the closed case | `test_v1_the_teaching_for_failed_calls_names_the_closed_case` |
| V1-8 | CancelledError is not closed by ARAIL | `test_b8_an_interruption_or_arails_own_teardown_is_not_filed_and_is_reraised_unchanged[CancelledError]` |
| M2-1 | the closed message ends with the old advice | `test_b6_a_call_after_close_is_arails_own_teardown_not_a_binary_failure` |
| V2-1 | the old deep rebuild line is back | `test_setup_on_a_host_with_no_queuellm_build_does_not_send_the_user_to_deep_rebuild[linux-x86_64]` |
| V2-2 | the AirLLM route is not mentioned | `test_setup_on_a_host_with_no_queuellm_build_does_not_send_the_user_to_deep_rebuild[linux-x86_64]` |
| V2-3 | the notice does not say the lab runs without the 2nd inference | `test_setup_on_a_host_with_no_queuellm_build_does_not_send_the_user_to_deep_rebuild[linux-x86_64]` |
| V3-1 | deep_verdict status offers deep rebuild / deep update as alternatives again | `test_cli_status_when_the_runtime_is_not_importable` |
| V3-2 | the status line through the build script has the old tail | `test_s9_status_with_nothing_installed` |
| V3-3 | setup.sh offers the maintainer routes as alternatives again | `test_setup_failure_message_says_who_can_use_the_maintainer_routes` |
| V3-4 | setup.sh drops 'needs a pre-rename checkout' | `test_setup_failure_message_says_who_can_use_the_maintainer_routes` |
| V4-1 | the old auto paragraph is back | `test_the_cli_docs_describe_auto_as_the_script_does` |
| V4-2 | the old channel-line paragraph is back | `test_the_cli_docs_list_every_form_of_the_channel_line_the_code_prints` |
| V4-3 | the DEV row does not say pre-rename | `test_the_cli_docs_describe_auto_as_the_script_does` |
| V4-4 | auto is not said to refuse without switching | `test_the_cli_docs_describe_auto_as_the_script_does` |
| V4-5 | the README does not say pre-rename | `test_the_cli_docs_describe_auto_as_the_script_does` |
| V4-6 | the docs drop the 'unknown' form | `test_the_cli_docs_list_every_form_of_the_channel_line_the_code_prints` |
| M4-1 | git's answer is accepted whatever repository it comes from | `test_m4_a_plain_folder_inside_another_repository_does_not_get_that_repositorys_commit` |
| M4-2 | the checkout's path is compared as written, not resolved | `test_m4_a_symlink_to_a_checkout_still_records_the_real_repositorys_commit` |
| M4-3 | the commit is taken from the parent when the answer is refused | `test_m4_a_plain_folder_inside_another_repository_does_not_get_that_repositorys_commit` |
| M5-1 | the clone hint does not name ARAIL_AEROLLM_REPO | `test_s1_no_checkout_gets_the_true_message` |
| M5-2 | the clone hint names the wrong variable | `test_s1_no_checkout_gets_the_true_message` |
| M7-1 | the lock is removed (the writers never wait or exclude each other) | `test_u9_eight_processes_fifty_writes_each_keep_an_exact_count` |
| M7-2 | a failed write no longer says why (store_error is not set) | `test_u9b_a_lock_held_past_the_cap_keeps_the_failure_in_memory_and_says_so` |
| M7-3 | a failure that could not be written is not kept in memory | `test_u9b_a_lock_held_past_the_cap_keeps_the_failure_in_memory_and_says_so` |

30 mutations, all caught on the final tree: V1 8, M2 1, V2 3, V3 4, V4 6, M4 3, M5 2, M7 3. M7 has one more result that is a number rather than a pass: with the lock removed, the new U9 keeps 102 of 400 writes.

### Frozen names (M5)

The rule is the re-review's item 12 and M5: no occurrence count of `aerollm_api`, `AERO_`, `AEROLLM_`, `tier1-aerollm`, `AeroLLMBackend`, `build-aerollm.sh` or `backend="aerollm"` goes down in a modified file. The re-review also counted the lowercase word `aerollm`, so that is counted here too. The coordinator's reading is that the rule guards renames: a count may fall when a line is deleted on purpose, and each such drop is listed with its reason. A script compares the final tree with the blob at each base, for every file the branch modifies (files it adds have no base; `sprints/` is left out):

| Base | Modified files checked | Drops in the seven tokens | Drops in lowercase `aerollm` |
|---|---|---|---|
| `0f2f8d25`, the head the first fix pass started from | 26 | one: `scripts/build-aerollm.sh`, `aerollm_api`, 38 to 37 | none |
| `origin/main` `62d5ebdf` | 25 | none | none |

The one drop left against `0f2f8d25` is a deliberate deletion. It is the old import-failure message in `bundle_install` (`Installed the bundled extension but \`import aerollm_api\` failed.`), removed in the first fix pass with the W1 block it belonged to. It was display text. `verify_loaded` still prints its own message that names `import aerollm_api`, and the script still runs that import. Against `origin/main` nothing is lower.

The three drops the re-review listed are settled:

| File and token | `62d5ebdf` | `0f2f8d25` | after the first fix pass | now | Why |
|---|---|---|---|---|---|
| `scripts/build-aerollm.sh`, `AEROLLM_` | 63 | 73 | 71 | 75 | the clone hint names `ARAIL_AEROLLM_REPO` again (M5), and M4 reads `$AEROLLM_REPO` |
| `scripts/build-aerollm.sh`, `aerollm_api` | 29 | 38 | 37 | 37 | the deliberate deletion above |
| `src/arail/portal/app.py`, lowercase `aerollm` | 131 | 131 | 130 | 131 | the comment says again that local aerollm changes flow into the lab (M5) |

### Accepted, and filed

- **M1, accepted.** The `runtime is None` branch of `_generate_on_worker` while the backend is open is unreachable in production. `AeroLLMBackend.__new__` hands out only instances whose `__init__` finished (`_initialized` is set last), and only `_close()` empties `_runtime`, after it sets `_closed`. I read `__new__`, `__init__` and `_close()` to confirm this. If the branch were ever reached, the cause would be ARAIL's, not the binary's, and the hook would still file it; the B6 test that empties `_runtime` by hand pins that. The coordinator accepted it as low risk, so it is left as it is.
- **M6, accepted.** The W4 guard (`unlabelled_numbers()` and `RATIO`) runs only in tests, over dry-run output, so a false positive cannot fail a live run: it can only make a test red, which is the safe direction (an all-digit identifier, such as a pid, can trip it). Its misses are the ones the review lists: a ratio in an evidence line, a number with a unit attached ("58s"), and anything after a quoting prefix. They are limits of the guard, and I know of no number the live check prints that they let through.
- **Filed in `sprints/BACKLOG.md`.** TD24 (a native panic leaves no router trace; `router/core.py:306` and its streaming twin) and TD25 (a native panic in a background call kills that agent's loop with no activity line), both with the trigger "before the first QueueLLM build that passes `./arailctl deep probe` 5/5 is installed in a lab (TD5's sprint)" and with the one-site alternative (`AeroLLMBackend.complete` re-raises `RuntimeError(...) from exc` after filing a panic, which breaks B7's "same object" contract; decide when they are pulled). TD10 now also carries M3 (a closed call still stamps `deep_failing` and calls `report_failure`; give it its own code such as `deep_closed`) and M8 (a Chat turn cancelled by an Eject replies "Inference failed: " with no reason).
- **Seen, not changed.** `scripts/build-aerollm.sh:27` says `AEROLLM_CHANNEL` "forces a channel from any mode". Only `auto` reads it (V4 says so on the page). The script's comment is not in this pass's list.

### Pre-handoff checks for fix pass 2 (`5ff035aa..HEAD`)

- Every item in the table has a commit, and every commit is atomic: one finding each, the test in the same commit (V1 and M2 are one commit, as the message said). Each message names what it fixed and ends with the `Co-Authored-By` line.
- `git diff --check` is clean for `5ff035aa..HEAD` and for `62d5ebdf..HEAD`. No TODO, FIXME, XXX or HACK in the added lines outside `sprints/`, no commented-out code (the scan of added comment lines that look like code found none), no use of SIGABRT or abort. The word "free" does not appear in the added lines. A duplicate-definition scan of every changed Python file reports none.
- `pyflakes`: no new warning in any product file (`deep_verdict.py` 0, `world_routes.py` 0, `backends.py` 2, `app.py` 26, each equal to the count at the start of the pass). In tests the only increase is the fixture-import pattern ("redefinition of unused 'fake_deep'"): one more in `test_deep_verdict.py` (23 to 24), for the new `test_u9b`. `shellcheck`: `build-aerollm.sh` has no finding (0 before, 0 now); `setup.sh` has 19, the same as at the start of the pass and on main. `bash -n` passes on both.
- Frozen names: see the table above. No machine-read name changed.
- `src/dac_world/` and `src/arail/buddy_host/` are byte-identical to `62d5ebdf`. All five scripts are mode 755 in git. `SPRINT.md` and `REVIEW.md` are unchanged.
- No new test file: the 20 new tests are in the six files the pass touched.
- Nothing was pushed.

### Cleanup of fix pass 2

Removed after this commit: my scratch folder (`fix2`, with the base copies, the mutation copy, every output and helper) and the git-ignored files the runs wrote into the worktree (`lab/`, `logs/`, `__pycache__`), without touching `src/arail.egg-info/`. Kept: the reviewer's scratch venv. The 150 busy-loop processes of the M7 load runs stop themselves and were also stopped by PID; none is left. The operator's `.venv`, `.env`, `lab/` and Worlds and the QueueLLM checkout were not touched.

## Fix pass 3 (after QA)

QA (`TEST_REPORT.md`) returned **WEAK_PASS**: no blocker, no major, no regression. It found 11 defects in the sprint's own code (QA-1 to QA-8 and QA-11 to QA-13; QA-9 and QA-10 are confirmations of filed debt) and left 25 red tests. This pass fixes those 11 until the 25 are green, files what QA reported and the sprint does not own as TD26 to TD41, and does nothing else. It starts at `1998df3c` (QA's tests at `531c3728`, the coordinator's ledger on top). Each finding is its own commit with its tests in the same commit. QA's four test files were not edited. `TEST_REPORT.md`, `SPRINT.md` and `REVIEW.md` were not edited. Nothing was pushed. QueueLLM, Buddy, the operator's `.venv`, `.env` and `lab/` were not touched, and no `deep rebuild`, `deep install` or `deep update` was run.

| # | Finding | Commit | Tests of mine (collected) | Mutations |
|---|---|---|---|---|
| QA-1 | A verdict file the parser cannot finish (`RecursionError`) reads as unreadable, in every reader and writer | `5f9d587f` | 6 | 7 |
| QA-2 | An entry the readers cannot use fails closed; an empty failure object is a failure | `22eaf2fb` | 15 | 6 |
| QA-3 | A passing probe clears a failure that could not have landed while it ran | `096cf6d2` | 10 | 5 |
| QA-4 | No write through a symbolic-link or foreign-owned state folder | `d6f2c30b` | 8 | 8 |
| QA-4 follow-up | Retry the lock-file create when macOS loses a create race (a defect of my QA-4; see below) | `b0d86ad1` | 5 | 6 |
| QA-4 gap | Pin the writer's catch-all around opening the folder (found by the final mutation run; test only) | `a055e8b0` | 1 | 1 |
| QA-5 | Error text and stored fields never reach a terminal with their control characters | `f3c3225b`, docs wording `41fdcccc` | 11 | 13 |
| QA-6 | A passing probe clears a failure this lab could not save, without a restart | `f5c24224` | 14 | 7 |
| QA-7 | After a probe clears deep, the tier-1 dot turns green without a Check | `4dee2d14` | 19 | 8 |
| QA-8 | Install over a source build says it is a source build | `1c2a85c0` | 12 | 6 |
| QA-11 | A digest comes from the file's content, at three sites | `0110ea62` | 1 | 3 |
| QA-12 | The probe supervisor never holds a whole stderr line in memory | `282f80e1` | 51 | 7 |
| QA-13 | Before setup, `deep probe` exits 5 | `4e158e68` | 12 | 8 |
| - | Backlog: TD26 to TD41 | `5a496f57` | none | |
| - | This section | the commit that adds it | none | |

That is 165 new tests in 6 existing test files, with no new test file (`test_deep_verdict.py` 69, `test_deep_probe.py` 59, `test_build_aerollm_honest.py` 13, `test_deep_probe_cli.py` 12, `registry/test_deep_verdict_overlay.py` 9, `test_deep_verdict_cli.py` 3), and 85 mutations. The counts are from `pytest --collect-only -k` over the six files, not from memory: a few commit messages in this pass give a count by function where this table counts cases (QA-5's says ten tests; it is ten functions and 11 cases). Fifteen commits before this section.

### Choices the message left to me

1. **QA-3, the clearing rule: a clock window, not TD16's count snapshot.** `record_probe` clears a failure unless `started_ts <= last_ts <= now + CLOCK_SLACK_S` (5 s). A failure stamped later than now (the clock was stepped back after it) or not a finite number (a hand edit; every comparison with NaN is false) cannot belong to this probe, so the pass clears it. Chosen over the count snapshot because it is smaller: the snapshot needs a new field in the probe's result and a second read of the store, and this needs neither. A failure that really lands during the probe still survives the pass (the existing D1 tests are unchanged and green), and the line the probe prints is true in both cases. What it does not cover is TD16's own case, a clock stepped back *during* a probe; TD16 stays open for that.
2. **QA-7, fixed at the read, not at the source.** Four places store the raw `unhealthy: RuntimeError: ...` for tier 1 through `report_failure` (`complete_preferring_deep`, `Resolution.router`, and the binding's two wrappers), so a fix at the source would leave three of them, and the record is not wrong when it is made: it is the visible degradation line. `_refresh_cleared_overlay` now also re-probes a stored `unhealthy` that was recorded before the stored passing probe *started*, using `deep_verdict.passed_after`, which is QA-6's predicate for a record the registry keeps. **At most once per clear, with no key to remember:** the re-probe stores a fresh health stamped now, which is later than that probe's start, so the next read finds nothing stale. It is not repeated per read even when it finds another problem. Left alone: an `unhealthy` recorded at or after the probe's start, a `not_installed` entry (a missing model folder, the example in the message), an entry with no passing probe, a non-QueueLLM entry, a disabled one. A residency check, it never generates. One limit: a wall clock stepped back after the pass makes the re-probe repeat per read until the clock catches up (find_spec, isdir, a dict lookup; it ends by itself).
3. **QA-4, the folder is opened once and everything goes through that descriptor.** `lstat` refuses a link; the open uses `O_NOFOLLOW|O_DIRECTORY` (macOS answers ENOTDIR, Linux ELOOP; both are read as "a link"); `fstat` on that descriptor refuses a folder the user does not own; `fchmod` runs on the descriptor, never on a path; and the lock, the temp file, the replace, the move-aside and the stale-temp unlink all use `dir_fd`. A link planted after the check cannot redirect any of them (a test swaps the folder mid-update). A link *above* the folder (a lab on another disk) works. **Residual:** reads of `verdict.json` stay path-based (a read writes nowhere), and a folder another user owns is refused for writes but not for reads, so a lab still shows what is in it. Whoever can edit that file can already say "passing"; that is TD20's same-user assumption.
4. **QA-5, three boundaries, not one.** `_clip` replaces what is left after whitespace collapses (C0, DEL, and the C1 range) with `?`. But `deep status` also prints fields that never pass through `_clip` (the lab and consumer names, timestamps, paths, the version, the channel text), and a hand-edited store supplies all of them, so `status_lines()` runs every row through `_printable`, the `channel` command does the same, and the probe's `say()` does it for every probe line (a model path, a shadowed copy). `_printable` also replaces a newline, so a name cannot forge a second row. The probe's `--json` uses `ensure_ascii=False`, which writes DEL and C1 raw (json escapes only C0); those are written as `\u00XX` there, which loses nothing. **Not covered, by the finding's own spec:** Unicode format characters such as the bidirectional overrides (U+202A to U+202E, U+2066 to U+2069). They can reorder text but are not terminal control sequences.
5. **QA-6, the read does it, and NOT SAVED goes with the failure.** `_assemble` ignores an in-memory failure that is strictly older than the stored passing probe's `started_ts` for that binary. With the failure gone there is nothing unsaved, so the verdict's `store_error` (the "NOT SAVED ... will not see this result" line of `deep status`) is dropped too; left behind it would sit next to "passing" and say something false. The read has no side effects: `_P.mem` is not edited.
6. **QA-12, `os.read` on the descriptor, the head of each line kept.** 64 KiB at a time (the pattern `_drive_child` uses for stdout), at most 8 KiB of one line kept, the rest read and dropped up to its newline. The old code showed the first 300 characters of a line, so the start is what is kept. Lines under the cap give exactly what `readline()` gave (a test uses it as an oracle over ten kinds of input at four chunk sizes).
7. **QA-13, the op is read first.** That also means an unknown op is rejected before the venv check, so a typo gets "deep op must be one of ..." whether or not the lab is set up. Same exit code, 1. With no `.venv` the probe prints nothing on stdout, `--json` included, because there is no interpreter yet to build the document with; the docs say so.

### What I found that QA did not

1. **A lost write that my QA-4 introduced, fixed in `b0d86ad1`.** After QA-4, `test_u9_eight_processes_fifty_writes_each_keep_an_exact_count` began to fail about one run in twenty with `assert 399 == 400`. Run 40 times on each commit it failed 0 times at `1998df3c` (the head before this pass), 0 at QA-3, 1 at QA-4 and 2 at the head before the fix, and 0 in 80 runs after it. A reproduction with a fresh state folder per round (8 processes, 50 writes each) lost a write in 8 of 120 rounds, always with `<folder>: No such file or directory`; a traceback of four of those put the failure at the lock-file open. The cause is a macOS quirk: when several processes create the same file in one folder at the same instant, `openat(dir_fd, name, O_CREAT)` lets one win and fails the rest with ENOENT. A micro-test of eight processes creating one file, 150 rounds: by path 1,200 of 1,200 succeeded; through a directory descriptor 1,048 failed (one winner a round); the same without `O_NOFOLLOW`; with one retry every loser succeeded on its second try. QA-4 moved the lock file onto the descriptor, which is what stops a folder swapped for a link after the check, so the fix keeps the descriptor and retries on ENOENT, at most 5 tries over about 20 ms (`_open_in_dir`), for the verdict lock and the probe lock. Five tests, written first and failing on the code before the fix, including 8 processes released at the same instant into 3 fresh folders (24 of 24 writes each). *Teaching:* Measures: writes lost out of 400 when 8 processes write at once. Bar: 0, because the test's claim is that every write lands. Outside: any loss means a writer reported a failure that was not real, and another process never sees the deep failure it was meant to record. Fault lives in: `_open_in_dir` and its two callers, `_locked_update` and `_take_probe_lock`.
2. **QA-11 was three defects, not one.** `build-aerollm.sh` hashed by file name at three places: `sha256_of`, and, in `bundle_install`, the script's own copy of the tarball and the `.so` it extracts, both under `$TMPDIR`. A `TMPDIR` with a backslash in it would have refused a correct bundle with "Checksum mismatch". All three now go through `sha256_of` (I checked by hand that `shasum` puts a `\` before the digest for such a name and not for stdin). My first test for the bundle sites, a bundle kept in a folder with a backslash, passed on the old code too, because the script hashes its own copy; it was vacuous and is not in the tree. The kept test sets `TMPDIR`.
3. **QA-5, the probe's `--json`.** `ensure_ascii=False` writes DEL and the C1 range raw (`json` escapes only C0). `_json_text` in `deep_probe.py` writes them as `\u00XX`, which loses nothing. The verdict module's own JSON prints use `ensure_ascii=True` and were already safe: a mutation that put plain `json.dumps` back at `identity --json` changed nothing, so those call sites were reverted to the original code and the test that could not fail was deleted.
4. **QA-6, a leftover line.** Ignoring the stale in-memory failure was not enough: the `NOT SAVED (...): other labs and the next process will not see this result` line of `deep status` is built from `_P.write_error`, which only a successful write by this lab clears. It would have stood next to "passing". It goes with the failure it was about.
5. **A gap in my own QA-4 tests, found by the final mutation run (`a055e8b0`).** The catch-all around opening the state folder in `_locked_update` keeps the promise that a writer never raises into a deep call. Removing it left every test green. A test now makes the open raise a `RuntimeError` and checks that the failure is kept and explained.
6. **Two differences between my runner and QA's baseline, neither a product fault.** See "Running the tests": one test needs `cargo` on `PATH`, and twelve baseline ids pass when the venv's `bin` is first on `PATH`.

### Mutations

| Finding | Mutations | Notes |
|---|---|---|
| QA-1 | 7 | |
| QA-2 | 6 | |
| QA-3 | 5 | the non-finite and the future-stamp branches each have a mutation |
| QA-4 | 8, and 1 added at the end | QA-4-h (the folder re-permissioned by path again) first survived: my swap test was triggered by `os.fchmod`, so a mutant that called `os.chmod` never sprang it. The test now swaps the folder right after it is opened, whatever happens next, and checks the planted folder's mode. QA-4-i is the catch-all gap above |
| QA-4 follow-up | 6 | never retry, a plain open at each of the two locks, retry every `OSError`, a cap of 1, a cap of 2 |
| QA-5 | 13 | a 14th (plain `json.dumps` at `identity --json`) is equivalent, see finding 3 |
| QA-6 | 7 | |
| QA-7 | 8 | |
| QA-8 | 6 | |
| QA-11 | 3 | one per hashing site |
| QA-12 | 7 | |
| QA-13 | 8 | |

Every test of mine was checked this way: the product line was broken (or the old code put back) in a scratch copy of the worktree, the named tests were run, they had to fail, and the copy was discarded. The worktree was never mutated. **All 85 were caught on the final tree.** I repeated the whole set after the last product commit: 81 of the 84 specs matched and were caught; three (QA-1-e, QA-4-e, QA-4-g) had match text that later commits had reshaped, were rewritten against the final code and were caught; QA-4-i, added then, survived until the test in `a055e8b0`.

### Running the tests

- **Environment.** The reviewer's pinned scratch venv (not rebuilt or upgraded), `env -i`, a scratch `HOME` and `TMPDIR`, `ARAIL_SKIP_OLLAMA=1`, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, `--continue-on-collection-errors`, a junit XML. `PATH` had the venv's `bin` first and `~/.cargo/bin` last (see the two differences below).
- **QA's four files: 81 passed, 8 xfailed, 0 failed.** Before the pass: 25 failed, 56 passed, 8 xfailed. The same eight xfails, and none flipped to XPASS.
- **The full suite**, on `5a496f57`: 8,527 testcases, 8,325 passed, 113 failed, 26 errors, 63 skipped or xfailed, in 11 min 30 s. After it came the docs wording (`41fdcccc`) and one test (`a055e8b0`); the product code is the same, and the doc-pinning files, QA's four files and `tests/registry` were run again on the final tree: green. Compared by id with the 151 in `TEST_REPORT.md`: **0 new failures**, and every baseline id is accounted for (below). *Teaching:* Measures: ids that fail now and are not in QA's list, and the other way round. Bar: 0 new, because any one is a regression this pass caused; every baseline id must still fail for QA's reason, or its difference must be shown to be the environment. Outside: a new id names the test, and the test names the code. Fault lives in: the cause code per id in `TEST_REPORT.md`.
- **Two environment differences.**
  - `tests/test_aerollm_bundle_install.py::test_package_script_refuses_dirty_worktree_without_allow_dirty` runs `package-aerollm-bundle.sh`, which looks for `cargo` before it looks at the work tree. With a `PATH` that lacks `~/.cargo/bin` it fails with "cargo not found". It is not in QA's list, so QA's run had cargo; the script is untouched. With cargo on `PATH` it passes, and the run above had it.
  - 12 baseline ids, all in `tests/test_qa6_bootstrap_cli.py` (cause C7, a script that runs a `python3` without python-dotenv), **passed** in my full run because the venv's `bin` was first on `PATH`, so `python3` had python-dotenv. Run again with the venv off `PATH`, exactly those 12 ids fail, 8 of them with `No module named 'dotenv'` and 4 as knock-ons (`assert 1 == 0`, `is_file()`) of the same script. So the 139 failing ids of the full run plus these 12 are the 151, with no other difference.
- **The concurrency fix.** 80 runs of U9 after it: 0 failures. The 120-round reproduction: 0 lost writes.
- **QA-12, memory.** Measured with QA's own harness (the fake runtime, `ru_maxrss` of the supervisor before and after a probe): growth is 5.5 MB with no long stderr line, 5.5 MB with a 64 MB line and 5.6 MB with a 256 MB line. QA measured +283 MB for 64 MB before the fix. *Teaching:* Measures: the supervisor's peak resident memory growth over a probe whose child writes one stderr line without a newline. Bar: below 48 MB for a 64 MB line, because the supervisor keeps only the start of a line. Outside: growth that scales with the line (it was about 4.4 times the line) means the line was held whole. Fault lives in: `_gather_stderr`.

### Frozen names

The rule is the one in fix pass 2: no occurrence count of `aerollm_api`, `AERO_`, `AEROLLM_`, `tier1-aerollm`, `AeroLLMBackend`, `build-aerollm.sh` or `backend="aerollm"` goes down in a modified file, and lowercase `aerollm` is counted too. A script reads each modified file at the base and at HEAD from git objects (files the pass adds have no base; `sprints/` is left out):

| Base | Modified files checked | Drops in the seven tokens | Drops in lowercase `aerollm` |
|---|---|---|---|
| `1998df3c`, the head this pass started from | 13 | none | none |
| `0f2f8d25`, the head the first fix pass started from | 35 | none | none |
| `62d5ebdf`, `origin/main` | 26 | none | none |

QA-11 replaced the two duplicated hashing branches in `bundle_install` (the `.so` one names `aerollm_api.abi3.so`) with calls to `sha256_of`, and QA-8 added references to the build record beside them, so the counts in `build-aerollm.sh` did not fall. One wider token, the quoted `"aerollm"`, does fall once against `0f2f8d25` and `62d5ebdf`, in `tests/test_aerollm_local_sibling_build.py`: the first fix pass (B2) rewrote a loose `assert "aerollm" in out` to check the new message, which still asserts `crates/aerollm-api`. It is a word check on output, not a machine-read name, and it is not from this pass.

### The third live run

The check was run once at the end, under the same rules as before: the operator's venv interpreter read only (`PYTHONDONTWRITEBYTECODE=1`), four non-secret keys copied from the operator's `.env`, a scratch root, one fresh child process per row. **9 of 9 rows pass**, exit 0, and the total time is 59 s (the bar is under 5 minutes).

| Row | Deep calls attempted | Deep calls failed | Silent outcomes |
|---|---|---|---|
| W1 Chat, five turns | 5 | 4 | 0 |
| W2 background agents | 2 | 1 | 0 |
| W3 Curator review | 6 | 5 | 0 |
| W4 Curator grow | 7 | 6 | 0 |
| W5 deep probe | 5 | 4 | 0 |
| W6 fresh process | 0 | 0 | 0 |

These are the same numbers as the first two live runs: on today's wheel it answers call 1 and fails calls 2 to 5, and nothing this pass changes is a case the live check creates. A silent outcome is a deep failure that some surface reported as fine or done; the bar is 0 in every row.

- **The operator's systems did not change.** A before and after snapshot taken outside the harness is identical: the `aerollm` entries in the operator's site-packages (names, sizes, mtimes, sha256 `d62e1e16e93c...` for the live binary), its top-level listing, the QueueLLM checkout (HEAD `87ff50053bb5`, clean tree), the crash-report folder, the operator's arail checkout status, its `.env` and its `lab/data/deep`. No file in the operator's `.venv` is newer than the run's start, no crash report appeared, and no probe or harness process or scratch root was left.
- **`LIVE_RUN.txt` is unchanged.** After normalising numbers, this run's output differs from it only in the QueueLLM checkout's HEAD (`e6346967e537` then, `87ff50053bb5` now: someone else's commits moved it; this build does not write to that checkout, and the before and after snapshots of this run agree) and one second of runtime.

### Accepted, filed, and not done

- **Filed in `sprints/BACKLOG.md` (`5a496f57`).** TD26 to TD41, in one commit, grouped by area: QA notes N1 to N9, N11 and N12 (N10 is by design), the four pre-existing behaviours QA pinned with a strict xfail (a cancelled review task leaves the Curator at "running"; a cancelled grow pass is still resealed and logged as "+0 new terms"; `HOME` unset stops every mode of `build-aerollm.sh`, `status` included; `AEROLLM_INDEX_URL` is echoed with credentials in it) and QA's suite-hygiene note. Each has a trigger.
- **TD16 stays open.** QA-3 uses a clock window, which covers a failure stamped in the future or with no usable time. It does not cover a clock stepped back *during* a probe; that is TD16's case and its row stands.
- **Not filed, for the coordinator to decide** (the backlog commit was specified as one): a state folder another user owns is refused for writes but not for reads, so a lab still shows what that folder holds. Whoever can edit that file can already say "passing"; it is TD20's same-user assumption, and it matters on a checkout shared by two accounts.
- **Left out on purpose.** Unicode format characters (the bidirectional overrides) in `_clip`, which the finding's spec does not include. A memo for "at most once per clear" in QA-7: the stateless rule is enough, and a memo alone would re-probe a newer failure. A JSON document when `deep probe` is run before setup: there is no interpreter to build it, and the docs now say so. A wall clock stepped back after a pass makes the QA-7 re-probe repeat per read until the clock catches up; it is a residency check and it ends by itself.
- **No command to clear a stuck failure.** The passing probe is the clear.

### Pre-handoff checks for fix pass 3 (`1998df3c..HEAD`)

- Every item in the table has a commit, and every commit is atomic: one finding each, the tests in the same commit (the follow-up, the docs wording, the test-only commit and the backlog are each their own). Each message names what it fixed and ends with the `Co-Authored-By` line.
- `git diff --check` is clean for `1998df3c..HEAD` and for `62d5ebdf..HEAD`. No TODO, FIXME, XXX or HACK in the added lines outside `sprints/` (the docs wording commit exists because the old spelling of an escape in `docs/cli.md` tripped that scan), no commented-out code (a scan of added comment lines that look like code found none), no use of SIGABRT or abort, and the word "free" does not appear in the added lines.
- `pyflakes` (a scratch venv, not the reviewer's): no new warning in any product file (`deep_probe.py` 1 before and after, `deep_verdict.py` 0, `registry/core.py` 0). In tests the only increase is the fixture-import pattern ("redefinition of unused 'fake_deep'", one per new test that takes the fixture): `test_deep_probe.py` 36 to 42 and `test_deep_verdict.py` 25 to 48 (94 of the 100 warnings left in the changed files are this pattern; the other 6 are the one fixture import per file, which pytest uses). `test_deep_probe_cli.py` goes from 4 to 3, because its new tests use the `pytest` import it did not use before.
- `shellcheck -S warning`: `build-aerollm.sh` 0 before and after; `arailctl` 2 before and after (both on lines this pass did not touch). Both parse under the macOS `/bin/bash` (3.2.57). All the scripts are mode 755 in git.
- `deep_verdict.py` is still stdlib only and runs under `/usr/bin/python3` 3.9.6 (QA's guard test runs it, and passes).
- Frozen names: see the table above. `src/dac_world/` and `src/arail/buddy_host/` are byte-identical to `62d5ebdf`. QA's four test files, `TEST_REPORT.md`, `SPRINT.md` and `REVIEW.md` are unchanged.
- Every `docs/cli.md` change has a test that pins it or sits next to a test that does (the exit-5 row, the "What it prints" paragraph, the clearing rule).
- Nothing was pushed.

### Cleanup of fix pass 3

Removed after this commit: my scratch folder (`fix3`, with the snapshots of earlier commits, the mutation copy, the lint venv, the instrumented copy, the reproductions, every output and helper) and the git-ignored files the runs wrote into the worktree (`lab/`, `logs/`, `__pycache__`), without touching `src/arail.egg-info/`, and the five `__KMP_REGISTERED_LIB_<pid>` files in `/tmp` that this pass's live run left (TD34; the 29 older ones are not from this pass and were left). Kept: the reviewer's scratch venv. The waiters and runners of this pass were stopped; none is left. The operator's `.venv`, `.env`, `lab/` and Worlds and the QueueLLM and Buddy checkouts were not touched.

## Fix pass 4 (after the QA re-test)

QA's re-test of fix pass 3 (`TEST_REPORT.md`, "Re-test after fix pass 3") kept the verdict at **WEAK_PASS**: all 11 first-pass defects are fixed, and attacking the fixes found six new ones (QA-14 to QA-19; four MINOR, two NOTE) plus a widening of filed debt (TD36). The coordinator decided: fix QA-14 to QA-18, file QA-19, the TD36 widening and two more rows. This pass does that and nothing else. It starts at `d0547e72` (QA's tests at `2f78a9e9`, QA's report at `9d9a36b8`, the coordinator's ledger on top). Each fix is its own commit, tests first, with a mutation check. Nothing was pushed. QueueLLM, Buddy, the operator's `.venv`, `.env` and `lab/` were not touched, and no `deep rebuild`, `deep install` or `deep update` was run. `TEST_REPORT.md`, `SPRINT.md` and `REVIEW.md` were not edited. **The one edit to a QA test file** is the one the coordinator allowed: `test_qa19_a_folder_swapped_at_the_writers_read_does_not_cost_the_stored_record` is now `xfail(strict=True)` with a reason that names TD43 (the project's rule: a strict xfail marks filed debt only). Three added lines, nothing else in `tests/test_qa_deep_honest_retest.py`; `git diff --numstat` says `3 0`.

| # | Finding | Commit | Tests of mine (collected) | Mutations |
|---|---|---|---|---|
| QA-14 | A `verdict.json` that is not a regular file never hangs a reader or a writer | `fcdc726f` | 12 | 5 |
| QA-15 | A lock that cannot be taken is not reported as another probe running | `469272ec` | 13 | 8 |
| QA-16 | Control characters are cleaned where each sprint string is made | `65dbbfbb` | 16 | 12 |
| QA-17 | `deep probe` with an interpreter that cannot run it exits 5, not 1 | `37874ae3` | 8 | 9 |
| QA-18 | After a pass, the next failure that could not be saved starts a record of its own | `2f0cacdb` | 6 | 5 |
| - | Filed: QA-19 as TD43, TD36 widened, TD44, TD45; QA-19 a strict xfail | `05908920` | none (3 added lines in a QA file) | |
| - | This section | the commit that adds it | none | |

That is 55 new tests in 7 existing test files, with no new test file (`test_deep_verdict.py` 30, `test_deep_probe_cli.py` 11, `test_deep_probe.py` 8, `test_curator_honest.py` 3, and one each in `registry/test_deep_verdict_overlay.py`, `registry/test_deep_loaded_lines.py` and `test_deep_policy_failing.py`), and 39 mutations. The counts come from `pytest --collect-only -k` over those files. Before each fix the new tests were run against the unfixed code and were red for the reason they state: QA-14 8 red (four of them timing out in `open()`), QA-15 6 red, QA-16 16 red, QA-17 7 red, QA-18 3 red (QA's own tests are in each number).

### One of QA's tests contradicts the design it belongs to: architect feedback required

`test_qa14_a_fifo_named_verdict_json_does_not_hang_readers_or_writers[write]` still fails, and I did not bend the product to make it pass. The hang is gone: the test now returns in **0.02 s** (it timed out at 15 s). What fails is its last assertion. After `note_call(False, ...)` has run (the writer), it asserts `source == "store_unreadable"`. A writer that meets an unreadable store moves it aside and writes a fresh record, as it does for every unreadable store since QA-2 and as QA-14's own "smallest fix" says ("fail closed; the next writer moves it aside"). So after the writer the store is readable again and the verdict is `failing` with source `call`, which is what the run printed: `0.02 failing call`. `store_unreadable` after a write is only true if the writer does **not** repair, and a symbolic link named `verdict.json` is repaired (moved aside) today, so leaving a FIFO alone would be an inconsistent rule. I ran that case to be sure: a symbolic link named `verdict.json` reads `failing`/`store_unreadable`; one write later it reads `failing`/`call`, `verdict.json` is a regular file, the link has been moved aside and is still a link, and its target is untouched.

- **Evidence:** `tests/test_deep_verdict.py::test_qa14_the_next_writer_moves_a_non_regular_verdict_aside_and_records_the_failure[fifo]` and `[dir]` pass: the writer returns in under 5 s, the verdict reads `failing`/`call`, `verdict.json` is a regular file again, the FIFO is the single `verdict.json.corrupt-*` entry and a FIFO still, and the stored count is 1.
- **If QA agrees:** change that one assertion to accept the repaired state (for `[write]`: `source == "call"`, or `source in ("store_unreadable", "call")`). The read variant needs nothing.
- **If you want the other design** (a writer never touches a special file, so the store stays unreadable until a person removes it): about six lines in `_locked_update`, and the FIFO case would then differ from the symbolic-link case. I did not choose it because a stuck-failing store is a worse default than a repaired one, and because it makes one kind of odd file sticky and another not.

The full-suite comparison below therefore has exactly one id beyond the 151: this one.

### Choices the message left to me

1. **QA-14, the order of the checks.** `lstat` refuses anything that is not a regular file *before* the open ("opening a device can have effects of its own"; a test makes `os.open` fail the test if it is called), then the open is `O_RDONLY|O_NOFOLLOW|O_NONBLOCK`, and a `with os.fdopen(fd)` block owns the descriptor from the moment it exists, so every way out closes it, and `fstat`s it. The `fstat` is for the swap between the lstat and the open: a FIFO that already holds a valid passing record is not believed (a test does exactly that). A swapped-in **folder** or **socket** is refused by the OS before our check (`fdopen` of a folder, `open` of a socket), so the reason carries the OS's words; the realistic case, one that is already there, has ours. I first wrote the open with a hand-rolled `try/except ... os.close(fd)` around the `fstat`; a mutation showed that branch no test could reach, which is why the `with` form replaced it.
2. **QA-14, the lock files, checked and not changed.** The message asked me to fix them in the same commit if they hang. Six cases, each in a child with a timeout, on the code before the fix: `verdict.lock` as a FIFO, a folder and a socket, and `probe.lock` as the same three. **None hangs.** The lock opens read-write, which does not wait on a FIFO, and `flock` on a FIFO answers at once ("Operation not supported"), so the writer reports it in 0.0 s and does not wait out the 2 s cap. A folder or a socket fails at the open. The one wrong answer was `probe.lock` as a FIFO, which the probe called "another probe is running": that is QA-15. Two guards pin the `verdict.lock` cases.
3. **QA-15, what the line says.** "...cannot be taken (flock: <reason>) on <state>/probe.lock: that filesystem probably does not support locks, or probe.lock is not a regular file; set ARAIL_DEEP_STATE_DIR to a folder on a local disk, or remove probe.lock". The message said "this folder's filesystem does not support locks". A FIFO named `probe.lock`, QA's other input, is the file's type and not the filesystem, so the line names both causes and the file, and says "probably". `_flock_acquire` (the writer's lock) needed no change: it already returns `flock: <reason>` at once for anything that is not busy, and still waits for a busy lock up to the cap. Both halves are now pinned.
4. **QA-16, at the source of each string.** The runtime's error in `_CountedRouter._note_failure`; the stored lab, consumer and the probe's call number in `_what_failed`; the whole sentence in `_failing_detail`; the slice of a stored stamp in `_utc_minute` (which also covers `health_detail`, the dot's text and the warm-up line); the wheel's version where the binary is made; the lab's name where it is read; the stored key and the OS's words where the unreadable-store reason is made and where the Verdict hands it out; and the exception text in `deep_policy._failed_detail`, the plain sentence a trace carries. The router's cap is now 160 for the whole line (type name included); it used to be 160 for the message after the type name. Left raw on purpose: the research block's `since`, `consumer` and `lab`, returned as JSON over HTTP (the encoder escapes C0 and the page escapes HTML; a browser is not a terminal sink).
5. **QA-17, find_spec.** `command -v` first (a missing path, a folder and a non-executable file all fail it, checked under bash 3.2), then `find_spec("arail.deep_probe")` under that interpreter. A module that counts its own imports proves the check does not run it. Not covered: an interpreter that finds the module but cannot run it (too old, a broken dependency); `find_spec` cannot see that and running it would be the probe.
6. **QA-18, the read's own test.** `_mem_note` asks `_passed_since` against the stored probe, so the writer and the read cannot disagree about what a pass superseded. The new record is a new transition and announces itself, as the saved path does.

### What I found that QA did not

1. **The QA-16 harm is still reachable through the registry, and I did not change it.** `registry.report_failure` stores `f"{type(exc).__name__}: {str(exc)[:160]}"` raw in the tier-1 health and emits it to the activity log under source `registry` (`Model '...' failed: ...`). I measured it with a runtime error holding ESC and BEL: both reach `health.detail` and an activity line. It is pre-existing code (`report_failure` is not in the sprint's diff; the call from `complete_preferring_deep` is from `49419057`) and it is called from four places, one of them the registry-built router that chat calls go through (`binding.py`, on a failed call). It is the same runtime error text QA-16 is about, so the Curator and `explain()` are clean and the Agents and Chat path is not. Fix: one `deep_verdict._clip` in `report_failure`. I am reporting it because the message said not to fix what my change did not cause; it needs a TD row or a go-ahead.
2. **A FIFO where the provenance records are read hangs `deep status`, the same way QA-14 did.** `_read_json` (the build and bundle records in site-packages) uses `Path.read_text`; a FIFO named `aerollm_api.build.json` did not return in 8 s. The precondition is write access to the venv's site-packages, which is much stronger than write access to `lab/data`, so I left it. The fix is QA-14's (`O_NONBLOCK`, `fstat`).
3. **The lock-file question, answered with measurements** (Choices, point 2): none of the six cases hangs.
4. **QA-16 had more sources than the two tests reach.** The wheel's version string (three details and every status row), the lab's name (stored and quoted in an activity line), the stored key quoted in the reason a store is unreadable, the reasons as the Verdict hands them out, and the exception text in `deep_policy._failed_detail` (a trace's plain sentence). Each has a test and a mutation.
5. **The `[write]` contradiction** above: found by running the prototype fix before writing my own tests, and kept visible instead of bent.
6. **Corrected along the way, all before the commit that would have carried them.** My mutation tool called a mutant that did not compile "NOT CAUGHT" (a deleted `if` left an empty `except`); it now rejects such a mutant as invalid. A mutation I wrote for a descriptor leak was not a leak on CPython (the file object closes itself by refcount); the real one is a rewrite on the raw descriptor, and the other mutation exposed a branch no test could reach, so the code was simplified. Three test files ended with a literal backslash and `n` from a heredoc and were repaired and compiled before commit. A commit message went in with an unexpanded `$(echo 16)` and was amended. A shell variable named `path` shadowed zsh's `PATH` and broke one command. None of these reached the tree.

### Mutations

| Finding | Mutations | What they break |
|---|---|---|
| QA-14 | 5 | no `O_NONBLOCK`; no `fstat` (a FIFO that holds a valid record is believed); no `lstat` type check (the file is opened to find out); no reason in the message; a rewrite on the raw descriptor that forgets to close it |
| QA-15 | 8 | every flock error "held" again; `EACCES` and `EAGAIN` each no longer "held"; a descriptor leak; no special catch (the generic text); no way out; no reason; the writer's lock waiting out the cap |
| QA-16 | 12 | one per place a string is made: `_utc_minute`, the lab and consumer in `_what_failed`, the call number, `_failing_detail`, the binary's version, the stored key, both reasons in `_assemble`, `_lab()`, the Curator's error text (kept raw, and cleaned but uncapped), the trace's fallback sentence |
| QA-17 | 9 | the check ignored; only half of it (each half); exit 1; stdout; no "nothing was recorded"; the whole traceback; an import instead of `find_spec`; no way out |
| QA-18 | 5 | the old behaviour; the test inverted; any later probe, a failed one too; the wrong binary's probe; a restart that is not announced |

Every test of mine was checked this way: the product line was broken (or the old code put back) in a scratch copy of the worktree, the named tests were run, they had to fail, and the copy was discarded. The worktree was never mutated. **All 39 were caught on the final tree**, run again after the last product commit: 0 not caught, 0 invalid.

### Running the tests

- **Environment.** QA's runner configuration: the reviewer's pinned scratch venv (not rebuilt or upgraded) called by absolute path, `env -i`, a scratch `HOME` and `TMPDIR`, `ARAIL_SKIP_OLLAMA=1`, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, `--continue-on-collection-errors`, a junit XML, the venv's `bin` **not** on `PATH`, `~/.cargo/bin` on it. Nothing was written into the checkout while the full suite ran. **The order of `PATH` matters, and my first full run had it wrong:** with `/usr/bin` before `/opt/homebrew/bin`, `python3` is Apple's 3.9.6, which has no `tomllib`, and `test_shell_source_safety::test_shell_sourced_configs_are_injection_safe` failed (153 ids: the 151, `[write]`, and this one). Proof that it is the runner and not the code: the same test fails identically on an untouched `git archive` of `d0547e72` with that `PATH` (1 failed) and passes on my tip with Homebrew first (1 passed). The second full run has Homebrew first, as QA's evidently had it (their 151 do not include this test).
- **`tests/test_qa_deep_honest_retest.py`: 20 passed, 4 xfailed, 1 failed.** The eight red tests that can be satisfied now pass (QA-14 `[read]`, QA-15 x2, QA-16 x2, QA-17 x2, QA-18); the twelve guards pass; QA-19 and the three TD36 cases are strict xfails; the one failure is `[write]` (see the section above). *Teaching:* Measures: outcomes of QA's own file. Bar: nine formerly red tests green, four xfails, twelve guards green, because that is the re-test's definition of fixed. Outside: a different split would be a regression in a guard or an xfail that flipped to XPASS. Fault lives in: the test id.
- **QA's four first-pass files: 81 passed, 8 xfailed**, as before the pass. The same eight xfails; none flipped to XPASS.
- **The full suite** (second run, on `05908920`): 8,608 testcases, 8,389 passed, 126 failed, 26 errors, 29 skipped, 38 xfailed, in 12 min 27 s. Compared by id with the 151 of `TEST_REPORT.md`: **152 failing ids = the 151 exactly, plus `[write]`. 0 missing, 0 other new.** *Teaching:* Measures: ids that fail now and are not in QA's list, and the other way round. Bar: 0 and 0 apart from the one id explained above, because a new red id is a regression of the pass and a missing one means a baseline test changed. Outside: the id names the test and the test names the code. Fault lives in: the cause code per id in `TEST_REPORT.md`; the one extra id's cause is the contradiction above. The wall time (12 min 27 s, against 11 min 30 s in pass 3) is context, not a gate: this tree has 81 more testcases, 25 of them QA's.
- **Concurrency.** U9 (`test_u9_eight_processes_fifty_writes_each_keep_an_exact_count`) ran **45 times with 0 failures** (the bar is 40). The 120-round cold-start reproduction (8 processes, 50 writes each, a fresh state folder per round) **lost no write: 0 of 120 rounds**. *Teaching:* Measures: runs that lost a write out of 400, and rounds out of 120 with a count short of 400. Bar: 0 each, because the claim is that every write lands and pass 3 lost one in 20. Outside: any loss is a writer that reported a failure it did not have; none of this pass's changes touches the lock open, and it still holds. Fault lives in: `_open_in_dir` and its callers.
- **The system Python.** `/usr/bin/python3` 3.9.6 imports `arail.deep_verdict` (stdlib only: no third-party module is loaded) and `python -m arail.deep_verdict status` runs. QA's guard test for it passes.

### Frozen names

The rule is the one in fix passes 2 and 3: no occurrence count of `aerollm_api`, `AERO_`, `AEROLLM_`, `tier1-aerollm`, `AeroLLMBackend`, `build-aerollm.sh` or `backend="aerollm"` goes down in a modified file, and lowercase `aerollm` is counted too. A script reads each modified file at the base and at HEAD from git objects (files the pass adds have no base; `sprints/` is left out):

| Base | Modified files checked | Counts that rose | Counts that fell |
|---|---|---|---|
| `d0547e72`, the head this pass started from | 14 | 2 | **0** |
| `5bc1f920`, QA's build | 13 | 2 | **0** |
| `62d5ebdf`, `origin/main` | 25 | 15 | **0** |

`src/dac_world/` and `src/arail/buddy_host/` (the Buddy contract bytes) are byte-identical to `62d5ebdf`. No QueueLLM file changed: the checkout is clean at `87ff5005` before and after, and no path of this pass is under it.

### The fourth live run

The check was run once at the end, under the same rules as before: the operator's venv interpreter read only (`PYTHONDONTWRITEBYTECODE=1`), four non-secret keys copied from the operator's `.env`, a scratch root, one fresh child process per row. **9 of 9 rows pass**, exit 0, total time 56 s (the bar is under 5 minutes).

| Row | Deep calls attempted | Deep calls failed | Silent outcomes |
|---|---|---|---|
| W1 Chat, five turns | 5 | 4 | 0 |
| W2 background agents | 2 | 1 | 0 |
| W3 Curator review | 6 | 5 | 0 |
| W4 Curator grow | 7 | 6 | 0 |
| W5 deep probe | 5 | 4 | 0 |
| W6 fresh process | 0 | 0 | 0 |

The same numbers as every earlier live run: on today's wheel it answers call 1 and fails calls 2 to 5, and the fixes of this pass change what is filed and said in cases the live check does not create (a FIFO, a lock, an escape sequence, an unusable interpreter). A silent outcome is a deep failure that some surface reported as fine or done; the bar is 0 in every row.

- **The operator's systems did not change.** A manifest taken outside the harness just before and just after is identical, line for line: a digest of every file's path, size and mtime under the operator's `.venv` (45,054 files) and `lab/` (34,340 files), the sha256 of `.env` (not printed), the sha256 of the `aerollm` native files in site-packages (the live binary is `d62e1e16e93c...`), the QueueLLM checkout (HEAD `87ff5005`, clean), the operator's arail checkout status and HEAD, the crash-report folder (31 reports, one digest) and the process list. No file under `.venv`, `lab/` or `.env` is newer than the run's start, no crash report appeared, and no probe or harness process or scratch root was left.
- **`LIVE_RUN.txt` is unchanged.** After normalising numbers, this run's output differs from it only in the QueueLLM checkout's HEAD (`e6346967e537` then, `87ff50053bb5` now; this build does not write to that checkout, and the before and after of this run agree) and one second of runtime.

### Seen and not changed

- **`registry.report_failure`** and **`_read_json`**: found item 1 and 2 above, measured, not changed.
- **`world_routes`'s own error states** (`_review_state`, `_forge_state` and `_grow_state` with `str(e)[:200]`, and the lines `Curator review of '...' failed: {e}` and `Forge of '...' failed: {e}`): pre-existing, not in the sprint's diff, raw exception text for an exception that escaped a pass. Same class as TD45; not changed.
- **The research block's `since`, `consumer` and `lab`** go out as JSON over HTTP, raw: the encoder escapes C0 and the page escapes HTML, so a browser is not a terminal sink. If a command line ever prints that block it must go through `_printable`.
- **A swapped-in folder or socket** is refused by the OS before the `fstat`, so its reason carries the OS's words, not ours. Fail-closed and fast; the realistic case has our words.
- **QA-17's limit:** an interpreter that finds `arail.deep_probe` but cannot run it (too old a Python, a broken dependency) still ends in Python's own exit 1. `find_spec` cannot see that and running the module would be the probe.
- **`deep probe --json` before setup** prints nothing on stdout (documented since QA-13): there is no interpreter yet to build the document with.
- **TD16 stays open** (a clock stepped back during a probe), and **TD36, TD43, TD44 and TD45** are filed, not fixed, as asked.

### Pre-handoff checks for fix pass 4 (`d0547e72..HEAD`)

- Every item in the table has a commit, and every commit is atomic: one finding each, tests first and in the same commit, with a mutation check. Each message names what it fixed and ends with the `Co-Authored-By` line.
- `git diff --check` is clean for `d0547e72..HEAD` and for `62d5ebdf..HEAD`. No TODO, FIXME, XXX or HACK in the added lines outside `sprints/`, no commented-out code (a scan of added comment lines that look like code found none), no use of SIGABRT or abort, and the word "free" does not appear in the added lines.
- `pyflakes` (a scratch venv, not the reviewer's): no new warning in any product file (`deep_verdict.py` 0, `deep_probe.py` 1, `deep_policy.py` 1, `world_routes.py` 0, each equal to the count at the start of the pass). In tests the only increase is the fixture-import pattern ("redefinition of unused 'fake_deep'"): 124 to 140 across the changed files; the count of "imported but unused" is 17 before and after.
- `shellcheck -S warning` on `arailctl`: 2 findings before and after (the unused `CYAN`, and `SC2155` on a line this pass did not touch). It parses under the macOS `/bin/bash` (3.2.57). All 18 scripts are mode 755 in git.
- `deep_verdict.py` is stdlib only and runs under `/usr/bin/python3` 3.9.6. Frozen names: see the table above. `src/dac_world/` and `src/arail/buddy_host/` are byte-identical to `62d5ebdf`. `TEST_REPORT.md`, `SPRINT.md` and `REVIEW.md` are unchanged; the only edit to a QA test file is the three-line marker on QA-19.
- Every `docs/cli.md` change has a test that pins it (the exit-5 row three times, the "What it prints" paragraph, the clearing paragraph).
- Nothing was pushed.

### Cleanup of fix pass 4

Removed after this commit: my scratch folder (`fix4`, with the untouched copy of `d0547e72`, the mutation copy, the lint venv, the reproductions, both full-suite logs and junit files, every output and helper), the git-ignored files the runs wrote into the worktree (`lab/`, `logs/`, `__pycache__`) without touching `src/arail.egg-info/`, and the five `__KMP_REGISTERED_LIB_<pid>` files in `/tmp` that this pass's live run left (TD34; the 29 older ones are not from this pass and were left). Kept: the reviewer's scratch venv. The waiters and runners of this pass were stopped; none is left. The operator's `.venv`, `.env`, `lab/` and Worlds and the QueueLLM and Buddy checkouts were not touched.
