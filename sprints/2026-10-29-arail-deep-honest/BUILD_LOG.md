# Build log: ARAIL deep, honest

**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `e3f47816`
**Vision:** [VISION.md](./VISION.md) at `942cbda8`
**Branch:** `qukaizen/arail-deep-honest` (from arail main `62d5ebdf`). Not pushed.
**Started:** 2026-10-29
**Finished:** 2026-10-31
**Status:** done. All steps 0 to 9 are committed. The live check passes on today's broken wheel.

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
8. **TD7 is reworded** in the backlog (an ASGI client, not `TestClient`) and **TD13 is added**: four hints still say "run ./arailctl deep rebuild" (`portal/app.py:2110`, `:8821`, `:10134`, `portal/templates/chat.html:2803`). Against a renamed checkout that command now refuses. They are outside the architecture's list, so they are filed, not changed.

## Architect feedback required

No gap stopped the build, and nothing here needs a new design pass. These are the places where the text of ARCHITECTURE.md and the machine disagreed; each was settled inside the harness, and the architect may want the text to match.

1. **W2 needs a fast router, and the four-key `.env` does not give one.** "Fast calls stamped `deep_failing`, whether or not Ollama answers" holds only if a fast call is made. Delta 5, second bullet. Suggested text for "Inputs": the row environment also sets `MODEL_BACKEND=ollama_native` and a model name that does not exist.
2. **W7 names `deep rebuild`; this build's rules forbid running it.** Delta 5, third bullet. Suggested text: "`build-aerollm.sh build` (what `deep rebuild` execs) and `auto`".
3. **"Drives the app with `TestClient` and no lifespan" (the live-check text and TD7) is not possible for W3 and W4.** Delta 5, first bullet.
4. **A failed fast call writes two stamped trace records, not one.** The agents' fast path streams first (`ARAIL_AGENT_STREAM_FAST`, default on) and, when the stream raises, calls `complete()` as before. Both go through the router chokepoint, so a reader counting traces sees two per failed fast call (W2: 8 stamped records for 4 fast calls). Every one is stamped; the count of records per call is not fixed. The tests assert "every fast record is stamped", never a count.
5. **The live wheel matches `DAY1.json` exactly.** Same sha256 (`d62e1e16...`), same version (0.1.0-rc.2), first error at call 2 with the same `rms_norm` text. W5 checks all three against `DAY1.json` itself, so no hash is typed in twice.
6. **TD13** (above) is a find the architecture's cut list did not cover.


## Final state

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
