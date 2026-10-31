# Review: ARAIL deep, honest

**Date:** 2026-10-31
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) at `fdc069bd` (code at `ebdbaa23`)
**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `e3f47816`
**Vision:** [VISION.md](./VISION.md) at `942cbda8`
**Diff reviewed:** `62d5ebdf..fdc069bd`, 53 files, 33 commits.

## Verdict: BLOCK

The sprint is close. The store, the probe, the overlay, the Curator counting and the build-script refusals are sound. 31 of 33 product mutations fail a test. The live run on today's wheel is credible.

Two things block the merge. Both are small.

1. **B1.** The backend hook files ARAIL's own teardown as a failure of the binary, and it misses a native panic. D1 makes every recorded failure sticky and checkout-wide. So the hook must record runtime failures, and only those. I reproduced both directions with the fake runtime.
2. **B2.** With no QueueLLM checkout, `deep rebuild` tells the user to clone QueueLLM for a source build. This ARAIL refuses every current clone. The vision counts a false line from `build-aerollm.sh` "in any mode" as a silent outcome. The Chat tab sends users to this route (TD13).

The required actions are at the end. Re-review needs only the diff of those actions.

## Findings

### BLOCK

**B1. The hook records ARAIL's teardown as a binary failure, and misses a native panic.**
`src/arail/router/backends.py:1905-1911` (the `try` around `submit(...).result()`), with `_close` at `:1774-1786` and `_generate_on_worker` at `:1881-1884`. Also `src/arail/portal/world_routes.py:176-186` (`_CountedRouter.complete`).

- **Scenario 1 (reproduced).** A Curator review, a grow pass or a research run holds the shared deep router. The operator presses Eject for the deep model in Chat, or switches the deep model. Both call `old._close()` (`portal/app.py:9094`). The next call runs on the closed backend. `submit()` raises `RuntimeError: cannot schedule new futures after shutdown`. The hook persists it. verdict.json says failing. Every surface then says "deep generation is failing on this build … Re-test with ./arailctl deep probe after installing a new build." Agents and the Curator in every lab of the checkout skip deep until a 5/5 probe. The Curator report adds "The fault is in the deep runtime, not the World."
- **Scenario 2 (reproduced).** A deep call waits behind a long generation when Eject runs. `_close()` waits 10 s for the drop, then calls `shutdown(cancel_futures=True)`. The queued call gets `CancelledError`. The hook persists it, with the same text.
- **Scenario 3 (read, not reproduced).** The portal stops. `concurrent.futures` sets its interpreter-shutdown flag. A background agent then calls deep. `submit()` raises `RuntimeError: cannot schedule new futures after interpreter shutdown`. The hook persists it.
- **Scenario 4 (reproduced with a `BaseException` subclass; PyO3's `PanicException` derives from `BaseException`).** The runtime panics on call 2. `except Exception` does not catch it. Nothing is recorded: counters `ok 1, failed 0`, verdict `untested`. `explain()` stays `ok`. The agents keep calling deep. In the Curator, `_CountedRouter` does not count it, `dac_world` does not catch it, and `_run_review` dies with `_review_state` stuck at `running`. That is silent outcome 4 of the vision.
- **Why it blocks.** Scenarios 1 to 3 put a false claim about the binary on every surface and give the wrong remedy. Scenario 4 is a real failure that no surface shows. Today's wheel hides both, because it fails anyway. The first build that passes the probe will not hide them.
- **Smallest fix.**
  - In `complete()`, raise a distinct `_DeepBackendClosed(RuntimeError)` before `submit()` when `self._closed` is true. Raise it in `_generate_on_worker` when `self._runtime is None`. Do not note it.
  - Do not note an error that `submit()` itself raises (move `submit()` out of the `try`, or check the executor state).
  - Catch `BaseException` around `.result()`. Note it, unless it is `KeyboardInterrupt`, `SystemExit`, `GeneratorExit`, `concurrent.futures.CancelledError` or `_DeepBackendClosed`. Re-raise the same object.
  - In `_CountedRouter.complete`, count a non-`Exception` `BaseException` (except the first three above) as a failed call, and raise `RuntimeError(...) from exc`. `dac_world` then skips the call and the pass reports partial or failed.
  - Tests, with the fake: a call after `_close()` leaves the verdict untested; a call cancelled by `_close()` leaves it untested; a `BaseException` subclass from `generate` is recorded failing and re-raised as the same object; `_CountedRouter` counts it and the review reports `failed`, not `running`.

**B2. The no-checkout hint sends users to a source build that this ARAIL refuses.**
`scripts/build-aerollm.sh:133-140` (`clone_hint`). Pinned by `tests/test_build_aerollm_honest.py:175`.

- **Scenario.** A user has no wheel. Chat says "Run './arailctl deep rebuild' to enable it" (`portal/app.py:2110`). There is no checkout. The script prints, verbatim today: "No QueueLLM checkout at …. For a source (dev) build, either: 1) clone it next to arail: git clone https://github.com/cdarnell/qukaizen-queuellm … Or install the published wheel instead: ./arailctl deep update". A clone of QueueLLM has only `crates/queuellm-api` (ARCHITECTURE A20). The next `deep rebuild` refuses it. `deep update` needs private-index credentials. The route that works for most people, `deep install`, is not named.
- **Why it blocks.** The vision's win condition counts "a false line from scripts/build-aerollm.sh, in any mode" as a silent outcome. W7 cannot see this line, because this Mac has a checkout. ARCHITECTURE C7 called this hint "now true". That text was wrong. The builder followed it.
- **Smallest fix.** `clone_hint` says: "No QueueLLM checkout at <repo>. A source build here needs a checkout that still has crates/aerollm-api; QueueLLM main has the renamed crates/queuellm-api, which this ARAIL refuses. Nothing was built or installed. For the prebuilt QueueLLM: ./arailctl deep install." Update S1 to assert `deep install` and the absence of the bare clone advice. Make the TD13 edits in the same commit (see Rulings).

### WEAK

**W1. A bundle that fails to import deletes the previous working install.** `scripts/build-aerollm.sh:497-509`.
A working bundle sits at `<site>/aerollm_api.abi3.so` with its marker. The user runs `deep install --force`, or the pin moves to a new tag. The new `.so` fails `import aerollm_api`. Lines 502-508 delete both files and exit 1 before `verify_loaded` (`:511`) can restore the backups. The user has no deep runtime. Two temp copies (`arail-aerollm-prev.*`, about 22 MB) stay in `$TMPDIR`. The delete is pre-existing (base `:357-364`). This sprint added the backups, but this branch skips them. Not a BLOCK, because the sprint did not add the damage path. Fix: delete the `if ! import_ok` block. `verify_loaded` already handles "the import fails" (empty `loaded_sha`) and restores both files. Add a bundle-path test like S7.

**W2. The copies overwrite in place, and a failure mid-copy is not undone.** `scripts/build-aerollm.sh:261`, `:499-500`. Pre-existing (base `:153`, `:354`).
(a) The disk fills during `cp -f`. `set -e` exits. A truncated `.so` stays in site-packages and the backup stays in `$TMPDIR`. (b) A running lab has that `.so` mapped. `cp -f` writes the same inode, and macOS can kill the lab on the next page-in of changed signed code. Fix: copy to `"$dest.tmp-$$"` in the same folder, then `mv -f` (atomic, new inode). Restore from the backup in a trap until verification passes.

**W3. Two layers have no test of their own.** Found by mutation (31 of 33 caught).
(a) `src/arail/deep_probe.py:474`. Without the `"probe": 1` tag check, Q8 still passes, because `dup2` alone keeps native noise off the pipe. (b) `src/arail/deep_probe.py:367`. Without the `getppid()` check, Q19 still passes, because the orphaned child also dies on EPIPE at its next protocol write. F21's behaviour holds; the line is not pinned. (c) F26: `tests/test_no_user_visible_aerollm.py` is red on base and branch (`model_resolve.py:290`), so it cannot flag a new string; only a manual diff of its list can. Fix: a unit test that gives `_drive_child` an untagged `{"ev": "call", …}` line and asserts it is ignored; for (b), test the check with the protocol reader kept alive, or delete it and name EPIPE as the mechanism.

**W4. Teaching: two numbers are not taught, and two texts teach the wrong thing.**
(a) `scripts/deep_honest_win.py:133` says every row's counts come from "ARAIL's per-process deep counters, not by this script". For W5 they come from the probe's JSON (`:1070`: `total` and `total − passed`). (b) W0 prints "11 aerollm or queuellm files recorded, 455 top-level entries" with no meaning, bar or out-of-range. (c) W4 prints "Curator 7/6, backend 7/6" (`:1044-1046`) with no labels. A reader cannot tell "7 attempted, 6 failed". (d) `src/arail/deep_verdict.py:847`, `:860` and `src/arail/deep_probe.py:929-930` say only a probe of "a new build" clears a failure. A 5/5 probe of the same build also clears it. That matters after a one-off failure (TD4) and after B1's false failures. Minor: the probe legend uses JSON paths (`calls[].seconds`, `first_error.call`) as headings, not the labels on the lines. Fix: a W5-specific "what it is"; label the W0 and W4 numbers; say "until ./arailctl deep probe passes 5/5 on this build".

**W5. The research toggle hides `deep_failing`.** `src/arail/portal/app.py:4563`.
Research deep passes are off. Deep fails. The strip shows "deep passes are switched off — flip the toggle to use the QueueLLM deep model for planning and interpretation" in the dim class. The `generation` block says failing, but the text promises that the toggle makes deep usable. Fix: `code not in ("tier_locked", "wheel_missing", "deep_failing")`.

### NOTE

- **N1. The source of the C3 flake.** `src/arail/portal/world_routes.py:1169-1170` and `:1303-1304` set the terminal `_review_state` / `_grow_state` one statement before the activity line. The test saw `done` and read the process-wide ring before its own line existed. It found the previous test's "All clear" success line. No product path double-emits. A duplicate-def and duplicate-route scan of every changed Python file is clean. The only success lines are `_review_report` (complete) and grow applied (`failed == 0`). The 6d test change is correct. Optional: emit first, then set the state.
- **N2.** `src/arail/deep_verdict.py:778` compares wall-clock times. If the clock steps back during a probe, a failure recorded mid-probe can be cleared. Low risk. Fix: also keep the failure when `count` grew since the probe started.
- **N3.** `scripts/build-aerollm.sh:252`: if `grep` errors, it prints nothing, and `${poison:-0}` passes the preamble check. Treat empty as a refusal.
- **N4.** `scripts/build-aerollm.sh:272` writes `queuellm_dirty: false` when `git status` fails. Write unknown.
- **N5.** `src/arail/deep_verdict.py:649-663` holds the process `_lock` across `_locked_update` (flock wait up to 2 s, plus fsync). `current()` (`:901`) on the portal event loop waits behind it. Do the file write outside `_lock`.
- **N6.** `src/arail/deep_probe.py:767` handles SIGINT only. SIGTERM or SIGHUP (terminal closed) kills the supervisor. A child stuck in a hung call (its own session) keeps a model copy until someone kills it. Use the same handler for both.
- **N7.** `src/arail/deep_verdict.py:482-501`: a writer killed before `os.replace` leaves `verdict.json.tmp-<pid>-<tid>`. Nothing sweeps them. Harmless.
- **N8.** `src/arail/deep_probe.py:898`: 5/5 with `NOT SAVED` exits 0. C3 says 0 means "recorded passing". Exit 1, or document it.
- **N9.** Identity comes from `module.__file__`, not from the image the loader mapped. A fake `aerollm_api` on `PYTHONPATH` can claim the real path and record 5/5 for its sha256. Same user only; that user can edit verdict.json anyway. Record as known.
- **N10.** `scripts/status.sh:1015`: `./arailctl status` prints "deep Qwen… (auto, warm ok)" while generation fails. It is not in the vision's surface list and is literally true. Consider adding the verdict state.
- **N11.** W2's fast stand-in cannot show a fast answer stored or labelled as deep. Acceptable: no agent labels its output as deep (ARCHITECTURE F3).
- **N12 (pre-existing, for QA).** `tests/conftest.py:277-302` patches `TestClient.send` only when Starlette's TestClient is built on `httpx`. A fresh venv resolves FastAPI 0.162 / Starlette 1.10, whose TestClient imports `httpx2`. The patch then does nothing, and every un-headed portal request gets 401. H2 and the truth-strip tests failed this way in my first run. See "Environment note for QA".

## F1 to F31

"Catch" means the test fails if the code is broken. "(m)" means I broke the code in a scratch copy and saw the test fail.

| # | Code that closes it | Test that proves it | Catch |
|---|---|---|---|
| F1 | `router/backends.py:1905-1925` hook; `deep_verdict.note_call` `:703`; `deep_policy.explain` `:189`; `_CountedRouter` | U5, B1, P2, P3, C1, C4, Q2; live W1-W6 | Y (m: not persisted, hook off, empty as ok, explain never failing, review complete) |
| F2 | `deep_probe._drive_child` died branch `:494-503`; `_rows_from_result` `:589-593`; stderr tail | Q3 | Y (m: pass on 1/5 fails Q2/Q3 group) |
| F3 | deadlines `deep_probe.py:453-457`; `_kill_group` `:407` | Q4 (2 s deadline, no orphan) | Y (read; a broken deadline hits the 120 s subprocess timeout) |
| F4 | `deep_verdict._read_store` missing branch `:436-438` | U3 | Y (m) |
| F5 | `_assemble` store_err branch `:855-863`; `_move_aside` `:504` | U4, Q13, P1 store test | Y (m) |
| F6 | `_take_probe_lock` `deep_probe.py:386-404` | Q7 | Y (m) |
| F7 | `deep_verdict.shadowed` `:961`; record keyed by the loaded sha256 | U1, Q12, S9 | Y (m) |
| F8 | `check_import_target` `build-aerollm.sh:179`; `verify_loaded` `:200` | S4, S6, S7, S11 | Y (m: target always free; no verify) |
| F9 | `repo_state` / `refuse_repo` / `auto_refuse` | S2, S8; live W7 | Y (m) |
| F10 | preamble `build-aerollm.sh:252-256` | S5 | Y (m); fails open on a grep error (N3) |
| F11 | `_changed_on_disk` `deep_verdict.py:372` | U2 | Y (m) |
| F12 | `_note_failure` write error `:663-670`; probe NOT SAVED | U8, Q17 | Y (m) |
| F13 | `_deep_overlay` `registry/core.py:122`; `_probe_aerollm` overlay `registry/health.py:131-133` | H1-H6, H8 | Y (m, each layer alone) |
| F14 | `record_probe` clear rule `deep_verdict.py:778` | U6, probe F14 test | Y (m) |
| F15 | D1 by design; a later ok call clears nothing | U5 | Y (by design, TD4) |
| F16 | `_deep_skip` `world_routes.py:140`; `_skip_note` | C6 | Y (m) |
| F17 | `_run_grow` not-applied branch `world_routes.py:1298-1305` | C4 (bytes, seal, evolution unchanged) | Y (m) |
| F18 | `_review_report` `cancelled` | C1b | Y (m) |
| F19 | model-dir `note_load_failure(persist=False)` `backends.py:1642-1643`; probe exit 5 `deep_probe.py:732` | Q18, U12, B4 | Y (m) |
| F20 | memory refusal `deep_probe.py:754` | Q9 | Y (m) |
| F21 | child `getppid()` check `deep_probe.py:367` (also EPIPE) | Q19 | Y for the behaviour; N for the line (W3) |
| F22 | child `dup2` `:305`; supervisor tag check `:474` | Q8 | Y for `dup2` (m); N for the tag check (W3) |
| F23 | conftest per-test `ARAIL_DEEP_STATE_DIR` + session guard | U13 + `pytest_sessionfinish` | Y (read); no `lab/data/deep/` appeared in my runs |
| F24 | child env `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` `deep_probe.py:763` | SEC3 | Y (m) |
| F25 | `esc` in `research.html:897`; `textContent` in `world-terms.js` | SEC2, C10 | Y (m on the strip) |
| F26 | user-visible text says QueueLLM | `test_no_user_visible_aerollm.py` | N: red on base and branch; only a manual diff of its list sees a new string (W3) |
| F27 | the F1 path after a pass | U5, U6 | Y (m) |
| F28 | ring depth stored `deep_probe.py:877` | Q1 | Y (m) |
| F29 | `TEACHING` tables, `_Nums`, `attach_teaching` | TQ1-TQ4 | Y (m: 36 tests fail); W4 lists what TQ does not see |
| F30 | W0/W8 manifest | live W8 | Y in the live run; not in CI |
| F31 | announce-once `registry/health.py:245-254` | H10 | Y (m) |

## Unverified

- **The live check.** I did not re-run W0-W8. It loads the real model, and the runtime has aborted at load before (A7). A SIGABRT writes a crash report, which this review must not cause. LIVE_RUN.txt is taken as recorded evidence.
- **The full suite** and the 127-id baseline comparison. That is QA's pass.
- **Linux CI** (flock, `ps axeww`). Not run.
- **A real Rust panic** through PyO3. Scenario 4 of B1 used a Python `BaseException` subclass.
- **Eject during a real Curator pass**, end to end. I reproduced the hook on `AeroLLMBackend` directly and read the Curator wiring.
- **Scenario 3 of B1** (interpreter shutdown), N2 (clock step), W1 (bundle import failure), W2 (disk full, code-signing kill). Read, not reproduced.
- **Q3 with a real SIGABRT**, and a mutation of the Q4 deadline (it would leave a hung fake child). Read only.
- **The `ps -E` reaper.** Read only; BUILD_LOG says its two tests fail with the reaper off.
- **Coverage of changed lines.** No coverage run exists.

## The twelve doubts, answered

1. **What is recorded as a failure.** Wrong in two directions: B1. Correct: a client disconnect, an outer timeout, `GeneratorExit`, `asyncio.CancelledError` and Buddy `/stop` record nothing (the blocking call finishes, or the exception is not caught). `MemoryError` and a Metal refusal are recorded; that is D1's accepted class (TD4). The streaming path is covered: `BaseBackend.stream_complete` calls `complete()` (`backends.py:158-169`). Preload and boot-warm load failures are recorded (`backends.py:1734-1738`). Check never generates (H2 never-generates test).
2. **`_CountedRouter` and empty text.** The assumption is right. The vendored judge always asks for a JSON object per term (`src/dac_world/reconcile.py:202-212`). An accepted term is `{"correct": true, …}`, not an empty string. Before this sprint, empty text became a silent "accepted" (`:218-220`). The test fake answers accepted terms with that JSON (`tests/test_curator_honest.py:66-71`). The grow prompts also always ask for JSON.
3. **The flake.** N1. No double emit, no late task, no duplicate handler.
4. **Identity and cost.** Measured with a 22 MB file: first hash 8 ms; static `current()` 0.10 ms per call; pinned 0.007 ms; `explain()` 0.008 ms. The hash is cached by (path, size, mtime_ns, inode). Identity is pinned once the module is loaded. Registry reads use static identity and never import. A swapped binary keeps the old key and sets `binary_changed_on_disk` (U2). A symlink is followed for the hash and the signature. A missing file reads "changed" in a pinned process; in a static one it reads untested and `explain()` returns `wheel_missing`. All fail safe.
5. **The store.** flock, atomic replace, 0700/0600 and `O_NOFOLLOW` hold (U9: 8 processes x 50 writes, exact count; SEC1; U4; U8). A crash mid-write leaves the real file intact (N7). Disk full and a read-only folder keep the failure in memory with `store_error`. Clock: N2. The probe protocol is sound: native stdout goes to stderr, lines must carry `"probe": 1`, and model text is never echoed. The tag layer has no own test (W3). Forgery by a fake module: N9.
6. **Changed behaviour for callers.** `complete_preferring_deep` makes no deep attempt on `deep_failing` (intended). `AeroLLMBackend.health_check()` has no production caller outside two delegating wrappers. The switcher does not block deep on `unhealthy`, so Chat can still pick it (W1: `backend=aerollm` on every turn). The researcher resolves with `allow_fallback=False`, gets no router, and says "Deep passes off". Buddy's brain checks only `fast` on the `agents` tab (`buddy_host/brain.py:71-72`); `src/arail/buddy_host/` is unchanged. The old "resident and ready" wording has no other reader. Gaps: W5, N10.
7. **The two edited tests.** Not weakened. `test_qa_buddy.py`: the stub moved from `prefer_deep` to `explain`, because `complete_preferring_deep` now reads `explain()`. The asserts are unchanged (two traces, `agent:buddy`, `error` then `ok`). `test_world_forge_api.py`: the forge assert is stronger (wrapper, inner router, pass-through). The review stand-in now makes the judge calls a real review makes; its asserts are unchanged.
8. **The scripts.** `set -euo pipefail` use is correct in the new code. `$PY` is `PYTHON` (the lab venv via `arailctl`) or `python3` behind the writable-site guard. `find_spec` runs through `verdict_py target` and fails closed. The renamed crate is refused before cargo and before any site-dir access. `auto` never switches channel (S8). The sha256 is verified after the copy, with restore on a mismatch (S7). Every check runs before the copy. No new path can damage a working install. Pre-existing damage paths: W1, W2. Small gaps: N3, N4.
9. **The live check.** Isolation holds in code: `env -i`, a built row env, scratch HOME, TMPDIR and state, four non-secret keys from `.env`, `PYTHONDONTWRITEBYTECODE=1`, and a reaper keyed on the scratch root. `deep rebuild` is exactly `exec bash "$REPO_ROOT/scripts/build-aerollm.sh" build` (`arailctl:807`). The three choices are faithful for what the rows check (Rulings 1-3).
10. **TD13.** Fix in this sprint (Rulings).
11. **Teaching.** The probe text and LIVE_RUN.txt teach every table number. W4 lists the exceptions.
12. **Frozen names and boundaries.** No count of `aerollm_api`, `AERO_`, `AEROLLM_`, `tier1-aerollm`, `AeroLLMBackend`, `build-aerollm.sh` or `backend="aerollm"` went down in any modified file. `src/dac_world/` is byte-identical to `62d5ebdf`. `src/arail/buddy_host/` is unchanged and `test_host_contract_vendor.py` passes. Cost text says "Token-free" only; every other "free" is a JSON key or "Free memory". Commits are atomic and tests land with their code (two small fixes, 6c and 8b, have no test of their own).

## Rulings

**Architect feedback 1 (W2 needs a fast router).** Accepted. The row env sets `MODEL_BACKEND=ollama_native` and `MODEL_NAME=arail-deep-honest-no-such-model` as constants. A fast call is always made and traced, Ollama never loads one of the operator's models, and nothing leaves the Mac. This ruling replaces the "Inputs" text of ARCHITECTURE. Limit: N11.

**Architect feedback 2 (W7 runs `build-aerollm.sh build`).** Accepted. `arailctl:807` is exactly that line, and W7 pins it with a regex. The only differences (`arailctl` sources `.env` and sets `PYTHON`) cannot reach the refusal, which happens in `repo_state` before `$PY` is used.

**Architect feedback 3 (an ASGI client, not `TestClient`).** Accepted. The real middleware runs (host allow-list, bearer, CSRF). No lifespan keeps the consumer under test the only deep caller. TD7 as reworded stands.

**Architect feedback 4 (two stamped records per failed fast call).** Accepted, no change. It is the existing stream-then-complete fallback (F20). The tests assert "every fast record is stamped". A reader that counts calls from traces must group records by call.

**Architect feedback 5 (the live wheel matches DAY1.json).** Confirmed. W5 checks sha256 `d62e1e16…`, version `0.1.0-rc.2` and the first error at call 2 against DAY1.json itself.

**Architect feedback 6 and TD13.** Fix in this sprint, in the same commit as B2. Reason: it is the defect class C7 fixed in two other hints. The portal is where a user without the wheel starts. After B2 alone, these hints still send users one hop the wrong way. The edits:
- `src/arail/portal/app.py:2110`: "The QueueLLM engine isn't installed on this machine. Run './arailctl deep install' to enable it." Update `tests/test_aerollm_compute_source.py:73` to assert `deep install`.
- `src/arail/portal/app.py:8821`: `"install_command": "./arailctl deep install"`, and correct the comment at `:8818-8820`.
- `src/arail/portal/app.py:10134`: `"install_command": "./arailctl deep install"`.
- `src/arail/portal/templates/chat.html:2803`: "compare on, but QueueLLM isn't installed — run ./arailctl deep install".

No frozen name changes. `deep install` and `deep rebuild` are verbs; ids and env vars stay.

## Spec adherence

C1-C8 are implemented as written. D1-D3 hold. Item 2 stays cut. Two drifts: the clone hint (a C7 error, B2) and the three live-check choices (accepted). The implementation order was followed.

## Security

Checked: the store is 0700/0600; the lock, the reads and the temp file open with `O_NOFOLLOW`; a symlink at `verdict.json` is moved aside, not followed (SEC1). Subprocesses are argv lists (`lsof`, `ps`; SEC4). The probe and status use no network; the child runs with HF offline (SEC3; mutation caught). Error text is escaped in the truth strip, the switcher and World Terms (SEC2; mutation caught). A canary env value never reaches the output (SEC6). The live wrapper copies four non-secret keys and scrubs the environment. No new dependency. Open: N9 (same-user only).

## Test coverage

- The 18 new test files plus the 2 edited ones: 332 passed, 1 xfailed (scratch venv pinned as below).
- 33 product mutations in a scratch copy: 31 caught, 2 missed (W3).
- Neighbour tests (`test_host_contract_vendor`, `test_world_growth`, `test_deep_policy*`, `test_aerollm_backend_install_message`, `test_aerollm_compute_source`): pass. `test_no_user_visible_aerollm` fails on the same pre-existing string as base.
- Coverage of changed lines: not measured.

## Performance

No hot-path cost of note (doubt 4). Check adds no generation. A probe on a broken build takes 12-23 s (DAY1). The live check took 60 s.

## Tech debt delta

TD1-TD12 as the architecture predicted. TD13 added by the builder; ruled "fix now". B1 is a defect, not debt. If W1-W5 are not fixed in this pass, file each with its trigger: W1 the next bundle re-pin; W2 the next change to an install path; W3 the next probe change; W4 the next live-check run; W5 the next truth-strip change; N12 the next FastAPI or Starlette bump in the lab venv. This review commits only REVIEW.md, so the builder or the orchestrator files them.

## Environment note for QA

Reuse the scratch venv at `/private/tmp/claude-501/-Users-netsushi-ProJects--claude-worktrees-sprint-command-error-060a85/b8dbbfe8-4399-41ee-b2d5-83e731cff931/scratchpad/arail-review-venv`. I pinned it to the operator venv's web stack: `fastapi==0.140.7`, `starlette==1.3.1`, `httpx==0.28.1`, `httpcore==1.0.9`, and removed `httpx2`. Without that, `tests/conftest.py`'s bearer patch does nothing and hundreds of portal tests get 401 (N12). `src/arail.egg-info/` in the worktree belongs to that venv's editable install.

## Required actions before merge

1. **B1.** Fix the exception classification in `router/backends.py` and `_CountedRouter`. Add the four tests.
2. **B2 and TD13.** Fix `clone_hint` and the four portal hints. Update S1 and `tests/test_aerollm_compute_source.py:73`.
3. **W1**, in the same pass if possible (delete one block, add one test). If not, file it.
4. Re-run the new test files. QA compares the full suite with the 127-id baseline.
5. Re-review covers only the diff of actions 1-3.
