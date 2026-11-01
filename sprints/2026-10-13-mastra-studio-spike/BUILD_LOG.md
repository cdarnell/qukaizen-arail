# Build log: Mastra Studio spike

**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md)
**Started:** 2026-10-13  **Status:** stopped at gate D1

## Plan

| # | Files | Change | Test | Commit ref |
|---|---|---|---|---|
| 1 | integrations/mastra-studio/{package.json,package-lock.json,src/mastra/index.ts}, scripts/mastra/check-no-ee.sh, license-census.mjs | B0.1 pin + ee/licence check | scripts | gate commit |
| 2 | scripts/mastra/gate-d1-egress.sh | B0.2 lsof harness + negative control, B0.3 headers | scripts | gate commit |
| 3 | B0.4 D2, steps 6-9 | not started (stop rule) | - | - |

## Execution
- B0.1: exact pins installed with --ignore-scripts (no lifecycle scripts needed). Resolved: @mastra/libsql 1.26.0, @mastra/observability 1.19.0.
  ee dirs present (see gates/ee-dirs.txt); loader-hook reachability NOT run (needs run-once.mjs). Licence census: one non-allowlisted (caniuse-lite CC-BY-4.0).
- B0.2: FAIL on lsof (2 non-loopback :443 sessions, telemetry off); control saw PostHog; tcpdump NOT RUN (no root); browser log not run.
- B0.3: no X-Frame-Options/CSP; iframe permitted (decision moot while stopped).
- Deltas: minimal C1 MCP server and run-once.mjs not built (Python mcp pkg absent, Ollama not running, gate already failed on server-only run).
  `mastra dev` has no --host flag and binds *:4111.

## Architect feedback required
1. C3 assumes `mastra dev --host 127.0.0.1`; flag does not exist, server binds all interfaces.
2. Licence allowlist omits CC-BY-4.0 (caniuse-lite data).

## Final state
No portal/Python code changed. No researcher.py change. Python tests not run (no Python code touched).

---
## Resume 2026-10-21 (after owner approval; D1 server leg passed)

### Executed
- `src/arail/mcp/researcher_tool.py` (C1): stdio-only FastMCP server, one tool `researcher_run_once`; validation, `wrong_tier`, `researcher_busy`
  (checks in-process status and `run_state.json`), wraps `ResearcherAgent._run` after `goal_store.set_goal` (archives the previous goal like POST /api/goal).
  researcher.py NOT modified (0 lines).
- `integrations/mastra-studio/run-once.mjs`, `src/mastra/{config,run-probe}.mjs`, `index.ts`: exits 3 unless
  `MASTRA_TELEMETRY_DISABLED=1`, `MASTRA_OFFLINE=1`, `MASTRA_AUTO_REFRESH_PROVIDERS=false` and no platform token; loopback-only scorer endpoint.
- Tests: `tests/test_researcher_tool.py` 12 passed (validation, tier, busy leaves GoalStore untouched, steps mapping/cap, stdout is MCP frames only).
- Real run: `scripts/mastra/run-once-env.sh` produced a Researcher run, a Mastra trace and the one-line JSON (score 0 from llama3.2:1b).

### Gate results
| Gate | Verdict | Evidence |
|---|---|---|
| B0.1 ee/licence | STOP: owner decision. No `/ee/` URL, but `core/dist/ee-*.js` chunk loads. Licence census passes with CC-BY-4.0 allowed | gates/B0.1-result.md, loaded-urls.txt |
| B0.2 D1 | server leg PASS (owner); browser leg FAIL: Studio page requests registry.npmjs.org (x15) and models.dev | gates/D1-egress.json, D1-full-browser-requests.txt |
| B0.3 | iframe permitted (no XFO/CSP) | studio-headers-test.txt |
| B0.4 D2 | PASS via public span API (8 children, non-empty I/O, read from Studio API) | gates/D2-spans.json |

### Drift from ARCHITECTURE.md (for the architect)
1. Three-flag child env (`MASTRA_OFFLINE=1`, `MASTRA_AUTO_REFRESH_PROVIDERS=false` besides telemetry) is required; `_mastra_env()` was not yet written (stopped before step 6).
2. A6 wrong: `agent_trace.trace_id` is per router call, not per run. Steps are scoped by time window from run start (exact, because the MCP process only runs this one job) and the returned `trace_id` is a minted run id. Ring records exist even with the recorder off (only bodies are stripped), so steps are not empty when the recorder is off.
3. Driver calls the MCP tool deterministically inside a root `agent_run` span; no LLM tool-calling (1b model is not reliable). `researcher-driver` Agent is registered for Studio only.
4. `mastra dev` has no `--host`; bind via `server.host` (done, verified `127.0.0.1:4111`).
5. `addScore` is not persisted by LibSQL; the score lives in the ARAIL ledger.

### Stopped
(superseded by "Completion 2026-10-21" below)

---
## Completion 2026-10-21 (owner decisions: block browser egress; accept ee chunk with a guard)

### Built (steps 6-8)
- `src/arail/mastra_bridge.py`: `mastra_env()` allowlist (always sets the three flags; drops platform token, *_API_KEY, *_TOKEN, *LICENSE_KEY*, MASTRA_EE_*), strict one-line child parser, `write_back` through `ExperimentTracker.observe` (kind `probe`, "not a 3DEval measurement"), Studio start/stop with a loopback LISTEN check (lsof), page-state resolver (5 states), restricted-browser args, `run_probe` (15 min timeout, process-group kill, shell=False, stdin JSON).
- `src/arail/mastra_cli.py` + `./arailctl mastra install|start|stop|open|run-once|status` (documented in docs/cli.md; F33 guard satisfied). `install` = `npm ci --ignore-scripts`; start uses `npx --no-install mastra dev` (never the registry).
- Portal: `_TIER_SURFACES["maximus"] += "mastra"`, `_OPTIONAL_SERVICES["mastra-studio"] = "maximus"`, `_build_services_dict(..., mastra_studio_up=False)` (defaulted kwarg so existing callers/tests are unchanged), `GET /mastra` (404 on minimalist, no iframe, no link to Studio, shows `./arailctl mastra open` + copy button and the ~16-request warning), `POST /api/mastra/{start,stop,run-once}` behind tier 404 + `_check_local_mutation_request`.
- `run-once.mjs`: stdout is exactly one JSON line (library logging rerouted to stderr; the first CLI run exposed `bad_child_output` from stray lines), exits 3 unless all three flags set, no platform token, no EE vars, and ARAIL_ROOT set; MCP server runs with `cwd=ARAIL_ROOT` and absolute LAB_ROOT/LAB_PKB (first real run wrote into integrations/mastra-studio/lab/ because ARAIL paths are cwd-relative).
- EE guard: `ee-chunk.pin`, `check-ee-loads.mjs`; `THIRD-PARTY-LICENSES/mastra/` (README + the five LICENSE files). No EE feature used or redistributed.
- Reset: `scripts/reset.sh` `reset_data` does `rm -rf "$DATA_DIR"` (lab/data), which contains lab/data/mastra incl. the browser profile; no edit needed. T-I-4 was verified by reading, not executed (a real reset is destructive).

### Architecture amendments (drift)
1. Child env needs `MASTRA_OFFLINE=1` and `MASTRA_AUTO_REFRESH_PROVIDERS=false` besides telemetry; `mastra dev` has no `--host`, bind via `server.host` in config (asserted by test).
2. A6 corrected: agent_trace trace_id is per router call. Steps are scoped by a time window from run start; returned `trace_id` is a minted run id; ring records exist with the recorder off (bodies only are stripped).
3. The driver calls the MCP tool deterministically inside a root `agent_run` span (a 1b model is not reliable at tool-calling); `researcher-driver` Agent is for Studio only.
4. Span input/output must be objects (bare strings are stored as `{}`); child end times are export wall-clock.
5. `addScore` is not persisted by LibSQL; the score lives in the ARAIL ledger only.
6. `set_goal` inside run_once archives the owner's current goal like POST /api/goal. Safe behaviour chosen: refuse with `goal_in_progress` when the current goal is active and progress < 1.0 (and `researcher_busy` while a run is active in any process via run_state.json); otherwise proceed (the previous goal is archived, not lost).
7. Studio must not be opened in a normal browser (npm version lookups x15 + models.dev logo x1). The portal has no direct link; `arailctl mastra open` uses a restricted profile. Request attempts still appear in the browser request log, they are refused locally.
8. ee: one inert bundled chunk (`ee-*.js`) loads; accepted by owner with the pin guard.

### Gate verdicts (final)
| Gate | Verdict |
|---|---|
| B0.1 ee/licence | PASS with guard (no `/ee/` URL; only the pinned inert chunk; census all allowed incl. CC-BY-4.0) |
| B0.2 D1 server leg | PASS (owner pcap, 2026-10-21) |
| B0.2 D1 browser leg | INCONCLUSIVE: restricted launch yields no resolver jobs / no non-loopback TCP in Chrome netlog and no non-loopback sockets in lsof, but Studio's JS still attempts ~16 non-loopback requests (visible in the request log, refused locally); owner pcap + rule reading needed (command in gates/D1-egress.json) |
| B0.3 framing | iframe would be allowed, but the portal does not embed Studio at all (egress) |
| B0.4 D2 | PASS |

### Tests
- tests/test_researcher_tool.py + tests/test_mastra_bridge.py: 63 passed (T-U-1,2,3,4,5,6,7,8,9,10, T-SEC-3/5, EE guard, loopback-bind, restricted-browser args, service registry).
- Not executed (need owner/Mac with real hardware or are destructive): T-I-2 timeout kill (unit-tested via mocked Popen), T-I-3 clean-machine setup (setup.sh and ensure_node untouched, `git diff` shows neither), T-I-4, T-I-5 (stop verified manually: no node/mastra process or :4111 listener afterwards), T-SEC-1 final-build pcap (owner).
- Regression: existing portal + experiment + tier suites: 8 failures in tests/portal (test_build_tab x4, test_models_api, opencode x2, token_compliance) fail identically on the base commit 20f334b4; test_cli_verbs "doctor healthy" and test_cli_status T34 (timing) also fail on/independent of this change. New failures: none.

---
## Fix round 2026-10-21 (architect WEAK_PASS, REVIEW.md e4114bb5)

| Finding | Fix | Test |
|---|---|---|
| 1 race | `run_once` takes an exclusive `flock` on `lab/data/mastra/run-once.lock` BEFORE the busy / goal_in_progress checks and the goal parse, holds it for the whole run, releases in `finally`. flock (not O_EXCL) so a dead holder's lock is simply free (kernel releases it) and a live holder's lock is never deleted or stolen; the file is never unlinked. | lock exclusive/released, killed holder not stale, two concurrent `run_once` -> exactly one enters, released after failure |
| 2 stop_studio | pid file now `pid pgid`; before any signal require pid == pgid, `getpgid` match and `mastra` in the live command line; otherwise clear the pid file, signal nothing (`stale`). | reused unrelated pid, pgid mismatch, garbage, dead pid; genuine group killed with nothing left |
| 3 browser | added `--force-webrtc-ip-handling-policy=disable_non_proxied_udp` and `--webrtc-ip-handling-policy=...`; page copy softened to what was measured. Assumption recorded: a Chrome enterprise policy forcing a proxy would override `--proxy-server` (not tested; owner pcap decides). | flags test |
| 4 scorer | unusable reply (not JSON, non-number, outside [0,1]) throws `bad_score`; run-once exits with `{"error":"bad_score"}`; ledger untouched. Observed live: llama3.2:1b sometimes gives an unusable reply, so probes can fail with bad_score instead of recording a made-up 0. | 8 reply cases against a loopback stub; bad_score writes nothing |
| 5 NODE_* | prefix passthrough removed; no NODE_* names pass (child or Python grandchild). | env tests incl. a real `node` env dump |
| cheap | install() runs `check-ee-loads.mjs --installed` (version + only the pinned chunk stem) and fails on mismatch; studio.log handle closed; `MASTRA_STUDIO_PORT` no longer read (fixed 4111); timeouts 12 min tool + <=2 min scorer < 15 min kill; caller `experiment_id` must match `[A-Za-z0-9_-]{1,64}` and must equal the experiment the run created (`experiment_mismatch`), child ids charset-checked; `reset.sh` stops a live Studio (pid==pgid and command mentions mastra) before wiping `DATA_DIR`. | T-I-4 and T-I-5 now real tests driving the real `reset.sh data --yes` in a sandbox copy |

Residual window (documented, not widened): the portal's own `POST /api/goal` does not honour the run lock (it only checks its in-process `researcher.status`), so a goal set from the UI during a probe can still archive the probe's goal. Making it honour the lock would touch unrelated portal code; the /mastra page now says so. Mitigation: do not set a goal while a probe runs.

Tests: tests/test_mastra_bridge.py + tests/test_researcher_tool.py 95 passed. Wider run (tier, system-health, trust boundary, route guards, experiments, qa_security_surface, reset_paths, tests/portal): 616 passed, 8 failed, identical to the 8 base failures (test_build_tab x4, test_models_api, opencode x2, token_compliance, verified on 20f334b4). D1 browser leg remains INCONCLUSIVE pending the owner pcap.

---
## QA fix round 2026-10-21 (TEST_REPORT.md FAIL at b51286d0)

| Finding | Fix |
|---|---|
| F-1 HIGH | `mastra_db_path()` and `mastra_env()` now always emit ABSOLUTE `ARAIL_MASTRA_DB`, `ARAIL_DATA_DIR`, `LAB_ROOT`, `LAB_PKB`, `ARAIL_ROOT`, overriding any relative value in the parent env. Verified end to end with an empty environment: `env -i PATH HOME ./arailctl mastra run-once "..."` returned an experiment id, trace id and a real score (was `bad_db_path`). |
| F-2 MED | `stop_studio` reasons on the process group: a pgid cannot be reused while a member lives, so group alive + leader dead = orphaned Studio members, which are stopped; a live leader must match the Studio argv shape and cwd. Also falls back to stopping a listener on :4111 that is verifiably ours. `reset.sh` uses the same rule. Verified live: `kill -9` of the npm leader, then `stop` -> `was_running: true`, no listener, no mastra process. |
| F-3 MED | `listener_state()` = none / ours / foreign (lsof: every listening pid's cwd must be under `integrations/mastra-studio`). New page state `port_in_use` (so six states now; amendment to the five in ARCHITECTURE C3), `start` returns `port_in_use` 409, `stop` never touches a foreign listener, health excludes a foreign listener. Verified live with `python -m http.server 4111`: status `port_in_use`, start refused, stop left it running. |
| F-4 LOW | `start_studio`/`run_probe` return `data_dir_not_writable`; the CLI prints a plain message and exits 1 on any OSError. |
| F-5 LOW | Leader identity = argv shape `^(npm|npx|node) ... mastra dev` AND cwd under `integrations/mastra-studio` AND pid == pgid (Python and reset.sh). |
| F-6 LOW | Node counts code points (`[...goal].length`), matching Python; a 2000-emoji goal is accepted. |
| F-7 MED | The MCP tool's refusal (`researcher_busy`, `goal_in_progress`, `wrong_tier`, `invalid_goal`, `run_failed:*`) is extracted from the MCP error and returned verbatim by run-once.mjs, the CLI and the portal (HTTP 409 for refusals); the page maps codes to plain sentences. |
| F-8 LOW | `uv.lock` NOT regenerated (unrelated churn: drops the dac-world git dep, rewrites nvidia markers). Known follow-up: re-lock in a dedicated change so the `mastra = ["mcp>=1.0,<2"]` extra is locked; until then do not run plain `uv run`/`uv sync` in this branch (use `--frozen`/`--no-sync`). Nothing in the tests or `arailctl mastra` calls uv; `git diff -- uv.lock` is empty after the full run. |
| docs | `docs/cli.md`: `experiment_id` is a must-match check, not "attach to"; `port_in_use` state listed. |

Test correction (evidence): `tests/test_mastra_qa.py::test_concurrent_real_probes_exactly_one_runs` identified losers as `run_failed:*`, the lossy form F-7 removed. After the fix the live output is `[{'error':'researcher_busy'}, {'error':'researcher_busy'}, {experiment_id, mastra_trace_id, score}]` (exactly one ran). I changed only the filter to also treat `researcher_busy` as a loser; the assertions on count, ledger and leftovers are untouched. None of the 7 `test_defect_*` tests were edited.

Also: `test_health` parity test (tests/test_system_health_stream_tier_filter_qa.py) caught my first health change (identity check bypassed the patched `_port_open`); health is now "port open and not identified as foreign".

Results: tests/test_mastra_qa.py + test_mastra_bridge.py + test_researcher_tool.py + health/tier/CSRF/experiments/reset suites + tests/portal: all pass except the 10 known base failures (test_build_tab x4, test_models_api, opencode x2, token_compliance, test_cli_verbs doctor-healthy, test_cli_status T34 timing). `node --test integrations/mastra-studio/test/qa-run-once.test.mjs`: 6 pass, 0 fail. D1 browser leg remains INCONCLUSIVE (owner pcap).

---
## Re-test fix round 2026-10-21 (TEST_REPORT re-test at 15913d3e: R-1, R-2)

Identity now has four outcomes instead of a guess: **ours / foreign / unknown / none**, and process checks return `None` (unknown) rather than `""` when lsof is missing or denied.

- **R-1 (MEDIUM).**
  - A live leader is "ours" when pid == pgid, its argv has Studio's shape (`npm|npx|node ... mastra dev`) and its cwd is under `integrations/mastra-studio`, OR its cwd cannot be determined (no lsof): shape plus the recorded `pid pgid` pair then proves it. An orphaned group (leader dead, members alive) is still stopped (a pgid cannot be reused while a member lives).
  - Positively something else (other argv, cwd elsewhere) -> `stale`, nothing signalled. `ps` giving nothing, or a listener that cannot be identified -> `{"ok": false, "error": "cannot_verify_identity", "message": "cannot verify Studio identity: lsof is required ..."}`, the pid file is kept, nothing is signalled (HTTP 409 from the portal).
  - Without lsof, `status` of ARAIL's own Studio is `running` (recorded group proves it); an unidentifiable listener is the new page state `status_unknown` (not `port_in_use`); `start` refuses it. Seven page states now.
- **R-2 (LOW).** A listener is "ours" only if its cwd is under the Studio dir AND (it is `node .../integrations/mastra-studio/.mastra/output/index.mjs` OR it is in the group recorded in `studio.pid`). A process group is never killed on cwd alone. `reset.sh` now anchors the cwd to its own repo's Studio dir (`cd -P` + `pwd -P` prefix match, not an unanchored glob) and treats an unknown cwd the same way as the Python side.
- Side fix: `listener_state` asks lsof first and only falls back to a TCP connect when lsof sees nothing (a probe connect had exhausted a backlog-1 test socket).
- My reset test now builds the fake Studio under the sandbox repo's own `integrations/mastra-studio` to match the anchored rule.

Live checks (real Studio, `lsof` hidden from PATH): `start` with lsof, then `status` -> running, `start` -> already_running, `stop` -> was_running true, 0 listeners, pid file removed. A foreign `http.server` on 4111 without lsof: `status_unknown`, `stop` -> `cannot_verify_identity`, the server survived. `http.server` started from inside `integrations/mastra-studio`: `port_in_use`, `stop` -> was_running false, survived.

Tests: test_mastra_qa.py + test_mastra_bridge.py (91) + test_researcher_tool.py: 239 passed; node qa-run-once: 6/6; wider portal/tier/CSRF/health/experiments/reset suites: only the known base failures (test_build_tab x4, test_models_api, opencode x2, token_compliance, test_cli_verbs doctor-healthy, test_cli_status T34). D1 browser leg remains INCONCLUSIVE (owner pcap).

---
## Re-test 2 fix 2026-10-21 (R2-1, R2-2)
- R2-1: `arailctl mastra stop` exits 1 whenever the result is not ok (e.g. `cannot_verify_identity`); exit 0 for a real stop and for `was_running:false`. Checked the other verbs: `start` and `run-once` already exit 1 on errors, `status`/`open`/`install` were correct.
- R2-2: `reset.sh` no longer aborts under `set -euo pipefail` when lsof is absent (`command -v lsof` guard, empty cwd = unknown). Behaviour conforms to the QA test: a live leader with Studio's argv shape and unknown cwd is proven by shape + recorded pid==pgid, stopped, and the wipe proceeds (rc 0). If the recorded leader looks live but its command line cannot be read at all, reset refuses (option a): prints "Studio (pid N) could not be verified ... Stop it first with ./arailctl mastra stop", returns nonzero, signals nothing and wipes nothing (own test with `ps` and `lsof` hidden).
- Results: 784 passed, 10 failed = the known base failures only; node qa test 6/6; no leftover processes or listeners; uv.lock untouched. D1 browser leg remains INCONCLUSIVE (owner pcap).
