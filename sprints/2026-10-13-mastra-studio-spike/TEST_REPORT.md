# Test report: Mastra Studio as an ARAIL surface (spike)

**Date:** 2026-10-21
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) at c42716a9 (fix round), reviewed in [REVIEW.md](./REVIEW.md) (WEAK_PASS)
**Verdict:** FAIL

There is one HIGH finding. On a default install, every real `run-once` fails before it does any work (F-1). Two MEDIUM lifecycle findings follow (F-2, F-3). Security held up under every probe I could run: CSRF and tier gating, environment isolation of the child and the grandchild (both dumped for real), injection, XSS, and the server-leg egress. The D1 browser-leg pcap is still the owner's to run and is **not** marked passed.

Weighting used (arail spike): security 40 / setup and lifecycle 30 / failure-mode grace 20 / happy path and regression 10.

## Findings

| # | Severity | Finding | Minimal repro | Failing test |
|---|---|---|---|---|
| F-1 | **HIGH** | **`run-once` is broken in the default configuration.** When neither `LAB_ROOT` nor `ARAIL_DATA_DIR` is set (no `.env` and no `.env.example` sets them), `mastra_bridge.mastra_db_path()` is the relative path `lab/data/mastra/mastra.db`, while `ARAIL_DATA_DIR` is resolved to an absolute path. `run-once.mjs` resolves the DB path against its own cwd (`integrations/mastra-studio`), so the DB lands outside the data dir and the script exits `bad_db_path`. The CLI and the portal button both always fail. The builder's real runs went through `scripts/mastra/run-once-env.sh`, which sets absolute paths, so this was never seen. The unit tests mock `spawn`. | In the worktree, with no LAB_ROOT: `./arailctl mastra run-once "x"` prints `{"error": "bad_db_path"}` in 0.1 s. Setting `LAB_ROOT=<abs>` makes the same command succeed. | `tests/test_mastra_qa.py::test_defect_default_config_db_path_is_relative_and_child_refuses_it` |
| F-2 | MEDIUM | **Studio is orphaned when its group leader dies.** The real tree is `npm exec mastra dev` (the leader) → `mastra dev` → esbuild plus `node .mastra/output/index.mjs` (the :4111 listener), all in one pgid. If the leader is `kill -9`'d or crashes:<br>- `stop_studio` treats the pid as dead and calls it `stale`.<br>- It deletes `studio.pid` and signals nothing.<br>- Studio keeps listening, the page still says "running", Stop does nothing, and nothing in ARAIL can stop it any more.<br>`scripts/reset.sh` has the same pid-only check, so it would wipe `lab/data/mastra` under a live LibSQL handle. | `arailctl mastra start`, then `kill -9 <pid in studio.pid>`, then `arailctl mastra stop`. The result is `{"stale": true}`, yet `lsof -iTCP:4111` still shows `node … LISTEN` (observed live; pgid 84959). | `test_defect_stop_after_leader_crash_orphans_studio` |
| F-3 | MEDIUM | **Any listener on 127.0.0.1:4111 is reported as Studio.** `resolve_page_state` and `/api/system/health` use only a TCP connect. A foreign process on 4111 gets:<br>- page state "running", with the Run-once card shown<br>- `start` returning `{"ok": true, "already_running": true}`<br>- `stop` returning `was_running: false`<br>- `mastra-studio` reported as up in health<br>The documented "port 4111 occupied" state does not exist. The loopback LISTEN check runs only after a fresh start. | `python3 -m http.server 4111 --bind 127.0.0.1 &`, then `arailctl mastra status`, which prints `running`. | `test_defect_foreign_listener_on_4111_is_not_reported_as_studio` |
| F-4 | LOW | **A read-only `lab/data` produces a raw traceback.** `start_studio` and `run_probe` raise `PermissionError` from `mkdir`. The CLI prints a Python traceback. The portal returns a bare 500, with no traceback in the body (verified). | `chmod 555 $LAB_ROOT/data; arailctl mastra start` | `test_defect_readonly_data_dir_gives_plain_error_not_traceback` |
| F-5 | LOW | **The reused-pid guard is a substring match on "mastra".** If a recycled pid belongs to a group leader whose argv merely contains a path like `integrations/mastra-studio/...` (a shell job such as `vim …`), that process gets SIGTERM/SIGKILL. The `reset.sh` grep has the same weakness. Suggested fix: match the exact Studio argv, or record the process start time. | Unit test with an injected `command_of`. | `test_defect_stop_kills_unrelated_group_leader_whose_argv_mentions_mastra` |
| F-6 | LOW | **Python and Node measure the goal length differently.** Python counts code points; `run-once.mjs` counts UTF-16 units. A 2000-emoji goal passes `validate_goal` and is then refused by Node with `bad_request`, which the portal maps to a 502. | `"🧪"*2000` | `test_defect_goal_length_counted_differently_by_python_and_node` |
| F-7 | MEDIUM (failure-mode grace) | **Refusal codes never reach the user.** When the MCP tool refuses with `researcher_busy`, `goal_in_progress` or `wrong_tier`, `run-once.mjs` turns it into `run_failed:Error`. Observed live:<br>- Three concurrent probes: the two losers got `run_failed:Error`.<br>- A probe while an owner goal was active also got `run_failed:Error`, although the owner goal was correctly preserved.<br>The safety behaviour is right, but the user is told "run failed" with no reason. | Hold `$LAB_ROOT/data/mastra/run-once.lock`, then `arailctl mastra run-once x`. | `test_defect_busy_code_is_lost_and_surfaces_as_run_failed` |
| F-8 | LOW | **`uv.lock` is stale for the `mastra` extra.** `uv lock --check` fails: the lock has no `mastra = [mcp]` entry, and there is unrelated `dac-world` drift. Any `uv run` silently rewrites the lock. I restored it and did not commit the change. | `uv lock --check` | none (build hygiene) |
| F-9 | INFO | **Ordering of 403 and 404 on minimalist.** On minimalist, a request with `Sec-Fetch-Site: cross-site` gets a 403 from a global guard before the route's tier 404. Without the hostile header it gets a 404. Both are refusals and the bridge is never reached. Recorded so the spec can say "refusal" rather than "404 first". | | `test_tier_404_comes_before_csrf` (accepts either) |
| F-10 | INFO | **Two doc and behaviour details.** `docs/cli.md` still describes `[experiment_id]` as the experiment the score is attached to; since the fix round it is only a must-match check. Separately, when Node is missing, `run-once` says `not_installed` rather than `node_missing`. | | |
| F-11 | INFO (accepted residual) | **Probe goals can still be archived from the UI.** `POST /api/goal` does not honour the run lock, so a goal set from the UI during a probe archives the probe's goal. This is documented on the page and in BUILD_LOG. I did not re-test it. | | |

Each failing test is real and red, and it turns green when the builder fixes the issue. I did not change any product code.

## Test inventory

New files:
- `tests/test_mastra_qa.py`: 129 cases. 122 pass and 7 are `test_defect_*`, which fail by design.
- `integrations/mastra-studio/test/qa-run-once.test.mjs`: 6 node:test cases, all passing. Run with `node --test integrations/mastra-studio/test/qa-run-once.test.mjs`. I did not add a script entry to package.json.

| # | Test | Category | Covers | Status |
|---|---|---|---|---|
| 1 | forged Origin (Studio :4111, localhost:4111, `null`, userinfo trick, suffix trick), `Sec-Fetch-Site` cross-site/none, same-site + Studio origin, Referer-laundering × 3 routes (27) | security | CSRF on start/stop/run-once; the bridge is never reached | pass |
| 2 | minimalist tier on mutating routes; minimalist `/mastra` | security | tier gate | pass (see F-9) |
| 3 | `BIND_ADDR=0.0.0.0` × 3 routes | security | bind_not_loopback | pass |
| 4 | GET on mutating routes | security | no GET side effects (405) | pass |
| 5 | malformed bodies (non-JSON, list, string, empty, text/plain, invalid UTF-8) | security/grace | plain JSON 400/502, no traceback | pass |
| 6 | the page in all 5 states | security | no iframe/frame/embed/object, no `:4111` anywhere, no window.open | pass |
| 7 | XSS in `detail`; result rendering in the page script | security | Jinja escaping; `textContent` only | pass |
| 8 | hostile `mastra_trace_id` written to the ledger | security | research UI renders only the constant `observation` text, escaped | pass |
| 9 | experiment_id: `../../etc`, `..`, `a/b`, `a\b`, 65 chars, NUL, `é`, empty, blank, `;rm`, `$(id)`, newline, non-strings (15) | security | refused before any spawn | pass |
| 10 | invalid goals: None, empty, whitespace, 2001, 10k, int, list, dict, bytes | security/input | refused before spawn | pass |
| 11 | hostile goals: 1999/2000, RTL plus bidi override, NUL/BEL/ANSI, JSON injection `"}, "exp_id": "victim"`, shell metacharacters, astral characters | security/input | sent verbatim as JSON on stdin; injection cannot set exp_id | pass |
| 12 | malformed child stdout (NaN, Infinity, bool, string score, oversize id, empty id, nested error, 200-char error, `[]`, `null`, BOM, two lines, 70 KiB, invalid bytes) | security/grace | ledger untouched; error code ≤128 chars | pass |
| 13 | child `bad_score` | honesty | nothing written | pass |
| 14 | **real** node child + **real** Python-grandchild env dump with NODE_OPTIONS, NODE_PATH, NODE_EXTRA_CA_CERTS, MASTRA_LICENSE_KEY, MASTRA_EE_*, MASTRA_PLATFORM_ACCESS_TOKEN, ANTHROPIC_API_KEY, HF_TOKEN, AWS_* set in the parent | security | nothing leaks; the three airgap flags are present; on timeout the **whole pgid** (node + shim + sleep) is gone | pass |
| 15 | real `run-once.mjs`: each licence/platform var set; each airgap flag missing or wrong | security/ee | exit 3, one JSON line | pass (py + node) |
| 16 | real `run-once.mjs`: DB path traversal (`../evil.db`), relative path, the data dir itself, sibling prefix (`datax/`) | security | `bad_db_path` | pass (node) |
| 17 | real `run-once.mjs`: malformed stdin | grace | one `bad_request` line | pass (py + node) |
| 18 | scorer with a non-loopback `ARAIL_OLLAMA_URL` | egress | `scorer_endpoint_not_loopback` | pass (node) |
| 19 | `unwrap` with every MCP result shape and garbage | grace | | pass (node) |
| 20 | default-config DB path | setup | F-1 | **FAIL** |
| 21 | foreign listener on 4111 | setup | F-3 | **FAIL** |
| 22 | leader crash, then stop | lifecycle | F-2 | **FAIL** |
| 23 | reused pid with "mastra" in argv | lifecycle/security | F-5 | **FAIL** |
| 24 | read-only data dir (bridge) / (portal) | grace | F-4 / no traceback over HTTP | **FAIL** / pass |
| 25 | `open` with no Chrome; `open` never falls back to a default browser or remote debugging | setup/security | exit 2, plain message, nothing launched | pass |
| 26 | clean machine: no Node on PATH; fake Node v20.11.0 (status/start/install/run-once) | setup | plain JSON or message, no traceback; install exits 2 | pass |
| 27 | Ollama down | grace | `model_unavailable`, nothing spawned | pass |
| 28 | lock held by another live process, then that process is `kill -9`'d | concurrency | refused while held, free afterwards, file never unlinked | pass |
| 29 | `run_once` while another process holds the lock | concurrency | `researcher_busy`, `_run_locked` never entered | pass |
| 30 | **real** 3 simultaneous `run-once` (Ollama llama3.2:1b, sandbox lab) | concurrency | exactly one reaches scoring; ledger probe count equals successes; no leftover `researcher_tool` | pass (4/4 runs after fixing a test bug: the 1b model sometimes gives `bad_score`, which is correct) |
| 31 | real probe with the lock held | grace | F-7 | **FAIL** |
| 32 | real probe while the owner goal is active | safety | owner goal id and status unchanged | pass |
| 33 | **real** Studio start/stop | lifecycle | LISTEN only on 127.0.0.1:4111; after stop the pgid is gone, the port is free, the pid file is removed | pass |
| 34 | user-facing text (/mastra visible text, the docs/cli.md mastra section) | honesty | no "guarantee", "fully airgapped", "zero egress", "proven", "100%" or "never leaves"; "not a 3DEval measurement" is present; the pending pcap is stated | pass |
| 35 | probe label in the Python bridge and the Node scorer | honesty | | pass |
| 36 | `scripts/setup.sh`, `pyproject.toml`, `tests/setup_ladder` unchanged vs 20f334b4 | setup | | pass |

Manual live checks, beyond the automated tests:
- **Studio process tree env** (`ps eww` on all 4 processes, secrets set in the parent): only the MASTRA_* flags plus Mastra's own internal vars (`NODE_ENV`, `NODE_CHANNEL_*` set by mastra dev itself). No secrets and no NODE_OPTIONS.
- **CLI clean-machine runs:** see rows 26 and 27.
- **Normal start/stop:** no leftover `mastra dev`, `.mastra/output` or esbuild processes, and no :4111 listener.

## Security review

| Surface | Checked | Findings |
|---|---|---|
| User input | Goal: type, length 1999/2000/2001/10k, whitespace, NUL/control/ANSI/RTL/bidi, JSON and shell injection. The goal travels as JSON on stdin and never as argv. experiment_id is refused by the `[A-Za-z0-9_-]{1,64}` regex before any tracker or spawn. The child's ids are length- and charset-checked. | F-6 (length unit mismatch, LOW) |
| Auth / CSRF | 27 forged-origin cases. A minimalist-tier request is refused. A non-loopback bind gives 403. GET has no side effects. Origin `null` and Studio's own origin (`127.0.0.1:4111`) both get 403. | F-9 (INFO) |
| File I/O | DB path traversal is refused in Node. The lock uses flock and is never unlinked, so a dead holder's lock is free. Read-only data dir tested. Default relative paths tested. | F-1 (HIGH), F-4 (LOW) |
| Process control | pid/pgid/argv guard; a real leader crash; a group leader with "mastra" in its argv; the timeout kills the whole group including the Python grandchild. | F-2 (MED), F-5 (LOW) |
| Network I/O | The server-leg harness was re-run (`MODE=test SECS=60 GATES=<scratch>`). lsof saw only `127.0.0.1:4111 LISTEN` plus loopback ESTABLISHED pairs, with **zero non-loopback endpoints** and no leftovers afterwards. The scorer refuses a non-loopback endpoint. **Browser leg: NOT RUN by QA.** It needs the owner's root pcap against `arailctl mastra open` with the final flags, including the WebRTC flags. | Pending (owner) |
| Env / secrets | Real dumps of the node child, the Python MCP grandchild and the full Studio tree. NODE_*, MASTRA_LICENSE_KEY, MASTRA_EE_*, MASTRA_PLATFORM_ACCESS_TOKEN, ANTHROPIC_API_KEY, HF_TOKEN and AWS_* are all absent. | none |
| XSS | Page `detail` is escaped. The result line uses `textContent`. The page's own markup has no innerHTML. Errors are returned as application/json. The ledger only ever renders the constant observation text, escaped. | none |
| Deserialization | `json.loads` with NaN/Infinity rejected, bools rejected, 64 KiB cap, exactly one line. | none |
| Crypto | N/A. The run id is `secrets.token_hex(8)` and is not a secret. | N/A |
| Dependencies / ee | `check-ee-loads.mjs --installed` passes: core 1.76.0, only the pinned `ee-btY6vXIW` chunk (4 files: js/cjs plus maps). `check-ee-loads.mjs gates/loaded-urls.txt` passes: 463 URLs, pinned chunk loaded, no problems. `license-census.mjs` reports ALL ALLOWED (CC-BY-4.0 ×1). I did **not** take a fresh loader trace of `mastra dev` plus run-once; the check used the committed trace. | F-8 (lock staleness, LOW) |
| Setup | `git diff 20f334b4 HEAD -- scripts/setup.sh pyproject.toml tests/setup_ladder` is empty. `ensure_node` lives in `scripts/setup.sh` and is untouched. There is no `scripts/ensure_node*` file. | none |

## Performance

N/A. Not on a hot path. The health endpoint adds one loopback connect.

## Regression

The wider suites were run at HEAD: tier helper and route guards, deep-default tier, system-health ×3, local trust boundary, airgap-toggle CSRF ×4, experiments ×3, qa_security ×3, tier1 clean-machine and egress guard, reset_paths, cli_verbs, cli_status, tests/portal, plus the sprint's own bridge and researcher_tool tests.

Result: **764 passed, 10 failed, 1 skipped, 2 xfailed.**
- All 10 failures are known base failures: test_build_tab ×4, test_models_api, opencode ×2, token_compliance, test_cli_verbs "doctor healthy", and test_cli_status T34.
- I confirmed **T34 and doctor-healthy on base 20f334b4** by running a `git archive` copy of the base tree with the same venv. T34 timed 2.508 s on base and 2.502 s on HEAD, so it is a pre-existing timing failure.

No new regressions.

## Coverage delta

Measured with `uv run --frozen --with pytest-cov`. Code that runs only inside subprocesses is not counted.

| Module | Before (sprint tests only) | After (+ QA tests) |
|---|---|---|
| `arail.mastra_bridge` | 75% | 82% |
| `arail.mcp.researcher_tool` | 64% | 64% (its real execution happens in the MCP subprocess) |
| `arail.mastra_cli` | not imported (0%) | 53% |
| Total | 71% | 74% |

## What I could not run

- **The D1 browser-leg pcap.** It needs root and is owner-run. It is NOT passed.
- **`npm ci` offline.** `npm ci` deletes `node_modules` first, so running it offline would have destroyed the working install. From the code, a failed `npm ci` returns nonzero and the page drops to `not_installed`. That path is unverified.
- **A truly full disk.** I approximated it with a read-only `lab/data` (F-4).
- **Real Node 20.** I used a shim that prints `v20.11.0`.
- **Portal process exit while Studio runs.** Studio is started with `start_new_session` and outlives the portal by design. `studio.pid` stays valid, so a later `stop` works, unless F-2 also happens.
- **A fresh loader trace for the ee check.** I used the committed `gates/loaded-urls.txt`.

## Conditions required before ship

1. Fix F-1. `mastra_env` must pass absolute `ARAIL_MASTRA_DB` and `ARAIL_DATA_DIR`, and a real default-config `./arailctl mastra run-once` must succeed. Its test must go green.
2. Fix F-2 (stop and reset must signal the recorded pgid while any member is alive) and F-3 (identify Studio before reporting it as running or "already running"), with their tests green. F-7 should be fixed in the same round, by surfacing the tool's refusal code.
3. **The owner runs the D1 browser-leg pcap** against `./arailctl mastra open` with the final flags (including the two WebRTC flags). It must show zero non-loopback packets from the restricted-browser PIDs. If it is dirty, REVIEW.md says the verdict becomes BLOCK.
4. F-4, F-5, F-6 and F-8 can be ticketed if not fixed. F-10 is a doc touch-up.
5. QA re-runs `tests/test_mastra_qa.py`, the node tests and the regression set after the fixes.

## Notes for the next QA pass

- Mocked `spawn` hid F-1. Every integration seam (Python → Node → Python) needs at least one real-process test **in the default configuration**, not only through the gate scripts' explicit env.
- The process manager is pid-file based and is the weakest area (F-2, F-3, F-5). If the spike graduates, it should be replaced, for example with a supervisor that holds the Popen handle, or with a start-time-stamped pid.
- The 1b scorer's `bad_score` rate is high enough to make probes flaky. Report it as a measured rate before anyone reads probe scores as signal.

---

## Re-test 2026-10-21 against 80ebd218 (builder's QA fix round)

**Verdict: FAIL** (narrow).
- All eight original findings are fixed and re-verified with fresh, real repros.
- Security is still clean, and there are no regressions.
- One new MEDIUM defect remains in the new stop logic (R-1), plus one LOW (R-2). Each has a failing test.
- If R-1 is fixed (or ticketed with Studio formally declared macOS-only and lsof required), this becomes WEAK_PASS, still conditional on the owner's D1 browser pcap.

### Original findings: fresh repros, not just the defect tests

| # | Fresh repro (real processes) | Result |
|---|---|---|
| F-1 | `env -i PATH HOME ./arailctl mastra run-once "List two facts about the moon"` | `{"experiment_id":"da3e76a0", …, "score":0.5}` in 6.8 s. **Fixed.** Also run from a foreign cwd: succeeded, with the lab resolved relative to the cwd (ARAIL's normal semantics). |
| F-2 | `start`, then `kill -9 <npm leader>`, then `stop` | `was_running: true`; no mastra/esbuild/output process left and :4111 free. **Fixed.** Also: deleting `studio.pid` while Studio runs, then `stop`, recovers through the listener path. |
| F-3 | `python3 -m http.server 4111` (bound to 127.0.0.1, and also to 0.0.0.0) | status `port_in_use`; start returns `port_in_use` 409 with rc 1; stop returns `was_running: false` and the foreign server stays alive. **Fixed.** |
| F-4 | `chmod 555 lab/data`, then start / run-once | `{"error":"data_dir_not_writable"}` and a plain one-line CLI message, rc 1, no traceback. **Fixed.** |
| F-5 | stale pid file pointing at a live, unrelated group leader: (a) `perl -e sleep(300) integrations/mastra-studio/notes.md`; (b) `node -e … mastra dev` run outside the mastra dir | Both give `stale` and both processes stay alive. **Fixed** (argv shape AND cwd). |
| F-6 | `run-once "🧪"×2000` vs ×2001 | 2000 is accepted and reached the scorer (llama3.2:1b returned `bad_score`, so nothing was written). 2001 gives `invalid_goal`. **Fixed.** |
| F-7 | CLI with the lock held; CLI with an owner goal active; LAB_TIER=minimalist; portal (TestClient, real bridge and child) with the lock held | `researcher_busy` / `goal_in_progress` / `wrong_tier`; portal `409 {"error":"researcher_busy"}`. **Fixed.** |
| F-8 | `git diff -- uv.lock` after the full run (all runs used `uv run --frozen`) | Empty. Not re-locked (ticketed follow-up per BUILD_LOG). **Accepted as follow-up.** |

All 7 original `test_defect_*` tests now pass, and the builder did not edit them.

**Review of the builder's test edit** (`test_concurrent_real_probes_exactly_one_runs`): **accepted.** The old filter identified losers by `run_failed:*`, which was exactly the lossy form F-7 removed. The new filter adds `researcher_busy` as a loser. The invariants are unchanged:
- exactly one run reached scoring
- the ledger probe count equals the number of successes
- no leftover `researcher_tool`

The edit does not weaken the test. A loser with any other code would still fail it.

### New defects (re-test)

| # | Severity | Finding | Minimal repro | Failing test |
|---|---|---|---|---|
| R-1 | **MEDIUM** | **Without `lsof`, ARAIL cannot see or stop its own Studio.** `_process_cwd` and `listener_pids` return nothing when lsof is not on PATH. Effects:<br>- `listener_state` falls back to a connect and calls ARAIL's own Studio `foreign`, so status and start report `port_in_use`.<br>- `pid_is_our_studio` fails on the empty cwd, so `stop` judges the live, genuine leader "recycled". It returns `stale`, **deletes `studio.pid`** and signals nothing.<br>- Studio keeps listening, and only a run with lsof present recovers it. This is F-2 again, on any machine without lsof (minimal Linux). | `./arailctl mastra start`, then with `PATH=<node dir>:/usr/bin:/bin` (no `/usr/sbin`) run `./arailctl mastra stop`. Observed: `{"stale": true}`, and `node …/.mastra/output/index.mjs` was still on 127.0.0.1:4111. | `test_defect_stop_without_lsof_orphans_genuine_studio` |
| R-2 | LOW | **"Ours" means only "listener cwd is under integrations/mastra-studio".** Any program a user starts from that directory on :4111 is reported as Studio "running" and is killed with its whole group by `stop`. Separately, `reset.sh`'s cwd test is an unanchored glob (`*/integrations/mastra-studio*`), so it also accepts `mastra-studio-x` or another checkout's copy. That one is only reachable with a matching `npm/node … mastra dev` argv, so its risk is negligible. | `cd integrations/mastra-studio && python3 -m http.server 4111` (own session), then `arailctl mastra stop`: the server is killed. | `test_defect_stop_kills_non_studio_listener_whose_cwd_is_in_mastra_dir` |
| R-3 | INFO | **Possible pgid reuse after a leader dies.** "Group alive and leader dead means our orphans" assumes the recorded pgid still belongs to the group ARAIL started. That pgid could only belong to another group if the pid was recycled into a new session leader that has since died while its members live. That is theoretically possible, so I record it, but it is not practically reachable. | | none |
| R-4 | INFO | **Page states and the spec.** There are now six page states (`port_in_use` was added), which amends the five in ARCHITECTURE C3. BUILD_LOG records this, and docs/cli.md lists it. In that state the page offers no start, stop, open or run controls, and the routes keep their 404 / 403 gates. Tested. | | `test_port_in_use_page_offers_no_controls_and_routes_stay_gated` (pass) |

Also verified:
- **reset.sh with a space in the repo path:** an orphaned group (leader dead) and a node-shaped `… mastra dev` leader in `fake repo/integrations/mastra-studio` are both stopped, with exit 0 and quoting correct. Test: `test_reset_stops_orphaned_studio_group_in_path_with_spaces` (pass).
- **Portal refusal mapping:** `researcher_busy`, `goal_in_progress`, `wrong_tier` and `experiment_mismatch` all return 409 verbatim (pass).
- **Page XSS with the new `REASONS` map:** still `textContent` only (pass).

### Suites (re-test)

- `tests/test_mastra_qa.py`: **133 passed, 2 failed** (R-1 and R-2, both by design).
- node `qa-run-once.test.mjs`: 6/6 passed.
- **Wider regression set:** the same suites as before, plus bridge, researcher_tool and portal. **773 passed, 10 failed, 1 skipped, 2 xfailed.** The 10 are exactly the base-20f334b4 failures: build_tab ×4, models_api, opencode ×2, token_compliance, cli_verbs doctor-healthy, cli_status T34. No new regressions.
- **ee guard:**
  - `check-ee-loads.mjs --installed`: core 1.76.0, only the pinned `ee-btY6vXIW` chunk, no problems.
  - The run against the committed trace: pinned chunk loaded, no problems.
  - Licence census: ALL ALLOWED.
- **Server-leg egress harness** (`MODE=test SECS=60`, with GATES in scratch): lsof saw only `127.0.0.1:4111 LISTEN` plus loopback ESTABLISHED pairs. **Non-loopback endpoints: none.**
- **Hygiene at the end:**
  - No mastra dev, `.mastra/output`, esbuild, `researcher_tool` or http.server process is running, and nothing is listening on :4111. I checked with `pgrep` and `lsof` after the last run.
  - `uv.lock` is unmodified.
  - The tests write untracked `logs/scheduler/*.json` files on every suite run (pre-existing test pollution). I deleted them and did not commit them.

### Remaining ship conditions

1. Fix R-1, or ticket it with an explicit "Studio controls require lsof (macOS)" statement plus a plain error, instead of a silent stale result that deletes the pid file. The minimal fix: never unlink `studio.pid` or declare it stale when identity cannot be determined.
2. R-2: fix or ticket it (LOW).
3. Ticket F-8 (re-lock `uv.lock` for the `mastra` extra) in a dedicated change.
4. **The owner runs the D1 browser-leg pcap** against `./arailctl mastra open` with the final flags (including the WebRTC flags). It must show zero non-loopback packets from the restricted-browser PIDs. **Not run by QA and not marked passed.** A dirty capture makes the verdict BLOCK, per REVIEW.md. Nothing is left listening on :4111 for that run.

---

## Re-test 2 2026-10-21 against f58e2d37 (R-1 / R-2 fix round): targeted

**Verdict: FAIL** (strict rule: a test is failing).
- Both remaining failures are **LOW**. R-1 (MEDIUM) and R-2 are fixed and re-verified live, security is clean, and there are no regressions.
- To reach **WEAK_PASS**, fix R2-1 and R2-2. Alternatively, the owner records them as ticketed follow-ups in the sprint ledger, and the two `test_defect_*` tests are marked `xfail(strict=True)` with the ticket reference.
- Either way, ship stays conditional on the owner's D1 browser pcap.

### R-1 / R-2: fresh live repros

| Case | Result |
|---|---|
| R-1: real Studio started with lsof, then `status` / `start` / `stop` with lsof hidden from PATH | `running` / `already_running` / `was_running: true`. No process left, :4111 free, pid file removed. **Fixed.** |
| R-1: `kill -9` of the npm leader, then `status` and `stop` without lsof | `running`, then `was_running: true`, port free. **Fixed.** |
| Foreign `http.server` on 4111, no lsof | `status_unknown`; start refused with `status_unknown` (409); stop returns `cannot_verify_identity` with its message and signals nothing (the server survived). With lsof, the same server reads as `port_in_use`. **Correct.** |
| R-2: `http.server` started inside `integrations/mastra-studio` | `port_in_use`; stop returns `was_running: false`; the server survived. **Fixed.** |
| A process whose argv mimics `node …/integrations/mastra-studio/.mastra/output/index.mjs` but whose cwd is `/private/tmp` | `port_in_use`; it survived stop. **Correct.** |
| A process that mimics the argv AND has its cwd inside the Studio dir | Treated as Studio (`running`) and stopped. **By design.** Shape plus cwd plus loopback is the identity proof. Spoofing it requires local code execution in the repo dir, so this is INFO and not a finding. |
| A member of the recorded group (node-shaped `mastra dev` leader whose child listens) | `running`; stop kills the whole group; port free. **Correct.** |

### New code attacked

- **Seven page states.** I rendered all 7 with an XSS payload in `detail`:
  - Escaped everywhere. No iframe and no `:4111` in any state.
  - Start appears only in `not_running`. Stop and Run appear only in `running`. The open card appears only in `not_running` and `running`. `status_unknown` and `port_in_use` show no controls.
  - The CSRF and tier gates still apply to every route.
- **`cannot_verify_identity`.**
  - Portal stop returns `409 {"error","message"}` verbatim, and a forged Origin still gets 403.
  - Portal start in `status_unknown` returns `409 {"error":"status_unknown"}`.
  - When `ps` returns nothing, stop returns `cannot_verify_identity`, **keeps the pid file**, and signals nothing.
- **lsof hanging or timing out.** `TimeoutExpired` falls through to the connect fallback: `unknown` if something is listening, `none` if not. No exception.
- **Odd paths.** `_listener_shape` matches with spaces and tabs in the repo path.
- **Races.**
  - If the leader dies between the check and the signal, `killpg` still reaches the orphaned members (a pgid cannot be reused while any member lives).
  - Pid reuse is covered by the argv shape plus cwd, or by the recorded pair when lsof is missing.
  - The pgid-reuse corner from R-3 is unchanged (INFO).
- **`reset.sh` with the anchored cwd** (the repo reached through a **symlink**, with **spaces** in both the real and the link path):
  - An own Studio-shaped leader is stopped (rc 0).
  - An identical leader in **another repo's** `integrations/mastra-studio` survives (rc 0).
  - The anchoring works.

### New defects

| # | Severity | Finding | Minimal repro | Failing test |
|---|---|---|---|---|
| R2-1 | LOW | **`arailctl mastra stop` exits 0 even when it signalled nothing.** It prints `{"ok": false, "error": "cannot_verify_identity", …}` but exits 0 (`mastra_cli.py` returns 0 unconditionally for `stop`). Scripts read that as "stopped". | Run a foreign listener with no lsof, then `./arailctl mastra stop; echo $?`. It prints the error and `0`. | `test_defect_cli_stop_exits_zero_when_identity_cannot_be_verified` |
| R2-2 | LOW | **`reset.sh` aborts without lsof while a Studio-shaped leader is live.** The script runs under `set -euo pipefail`, and `studio_cwd="$(lsof … \| sed \| head)"` fails with 127. `reset.sh data --yes` therefore exits **127 before wiping anything**, and Studio keeps running. It fails closed, so nothing is destroyed, but reset is unusable in that state. The Python side handles the missing lsof; reset.sh does not. | Use a PATH without lsof, a live `node … mastra dev` leader recorded in `studio.pid`, then `bash scripts/reset.sh data --yes`. It exits with rc 127 and `lab/data/mastra/studio.pid` is still there. | `test_defect_reset_aborts_without_lsof_when_studio_leader_is_live` |

### Quick regression

- `tests/test_mastra_qa.py` + `test_mastra_bridge.py` + `test_researcher_tool.py`: **257 passed, 2 failed** (R2-1 and R2-2). All earlier `test_defect_*` tests (F-1…F-7, R-1, R-2) pass.
- Node `qa-run-once.test.mjs`: 6 of 6 pass.
- Wider portal / tier / CSRF / health / experiments / reset / cli set: **778 passed, 10 failed, 1 skipped, 2 xfailed.** The 10 are exactly the base-20f334b4 failures.
- **Hygiene:**
  - `uv.lock` is unmodified; all runs used `--frozen`.
  - No `logs/` files are tracked. The untracked `logs/scheduler/*.json` that the suites write were deleted and not committed.
  - At the end no mastra dev, `.mastra/output`, esbuild, http.server, test leader or `researcher_tool` process is running, and **nothing is listening on :4111**. Checked with `pgrep` and `lsof`.

### Remaining ship conditions

1. Fix R2-1 (stop exits nonzero when `ok` is false) and R2-2 (`|| true` on the lsof probe, or a `command -v lsof` guard, so reset falls back to shape plus the recorded pair like the Python side). Alternatively, ticket both and mark their tests `xfail(strict=True)`. Either way the verdict becomes WEAK_PASS.
2. Ticket F-8: re-lock `uv.lock` for the `mastra` extra in a dedicated change.
3. **The owner runs the D1 browser-leg pcap** against `./arailctl mastra open` with the final flags. It must show zero non-loopback packets from the restricted-browser PIDs. **Not run by QA and not marked passed.** A dirty capture makes the verdict BLOCK, per REVIEW.md.
