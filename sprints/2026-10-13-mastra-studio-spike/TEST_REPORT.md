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
