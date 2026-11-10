# Review: Mastra Studio as an ARAIL surface (spike)

**Date:** 2026-10-21
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) at 9e75e2a3
**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at 03d8bee1 (plus the 8 amendments in BUILD_LOG "Completion 2026-10-21")

## Verdict: WEAK_PASS (conditional: no merge until the owner-run D1 browser pcap is clean)

There are no BLOCK findings in the code. The ASKs below are either small fixes for the builder to make before QA, or follow-ups. The verdict covers the code only. One condition is independent of the code: the D1 browser leg is still INCONCLUSIVE until the owner runs the pcap. If that capture shows any non-loopback packet from the restricted-browser PIDs, this verdict becomes BLOCK (see finding 3).

## Spec adherence

- C1 (stdio MCP tool), C2 (deterministic driver), C3 (bridge plus portal) and C4 (arailctl verbs) are implemented as amended. I accept amendments 1 to 8. Two of them are real design changes and are written down honestly. Amendment 6 means `set_goal` archives the current goal, so the build adds a `goal_in_progress` guard. Amendment 7 means Studio is opened only through the restricted browser and the portal has no link to it.
- Owner decision 1 (block browser egress) is honoured. `mastra.html` has no href, src or iframe pointing at :4111, and the test at tests/test_mastra_bridge.py:258 asserts this. The page shows `./arailctl mastra open` only as text to copy. Nothing on the page opens Studio automatically.
- Owner decision 2 (accept the ee chunk with a guard) is honoured in two parts:
  - **Licence-key env vars.** They are kept out of the child twice. The env is built from an allowlist (mastra_bridge.py:43-50), and a deny regex is applied on top of it. run-once.mjs:20-23 also exits with code 3 if any of them is present.
  - **The pin.** check-ee-loads.mjs fails on any other ee-named module or on a core version change.
  - **Weakness.** The pin is a gate script only. Nothing at install or start time runs it (finding 7).
- Drift, acknowledged: the integration tests T-I-4 (reset wipes the DB) and T-I-5 (no zombie processes after stop) do not exist. Only the unit and security tests do (37 tests, 63 parametrized cases, all passing).

## Code quality findings

1. [ASK] **Check-then-act race in run_once.** Location: src/arail/mcp/researcher_tool.py:130-147. The `researcher_busy` and `goal_in_progress` checks run first. `GoalParser.parse(goal)` comes next, and it can make an LLM call lasting several seconds. Only after that does `set_goal`/`_run` write run_state.json.
   - Race 1: a second run-once (CLI and portal together, or two portal tabs) can pass both checks inside that window.
   - Race 2: the portal's own POST /api/goal can do the same, because it only checks its in-process `researcher.status`.
   - Result: the owner's goal is archived in the middle of a run. It is archived, not lost, but this is exactly what amendment 6 says must not happen.
   - Fix: in `run_probe`, take an exclusive lock file under `lab/data/mastra/` (O_CREAT|O_EXCL, holding the pid) and hold it for the whole child run. Alternatively, move the busy check to after the parse and write run_state as "running" before parsing.
2. [ASK] **stop_studio can kill an unrelated process group.** Location: src/arail/mastra_bridge.py:223-242. It trusts `studio.pid` with no check that the process is still alive. If Studio crashed and its pid was reused, `getpgid` and `killpg` send SIGTERM and then SIGKILL to whatever process group now owns that pid. Fix: before signalling, check that the pid's command line contains `mastra`, or record the pgid together with the process start time.
3. [ASK, becomes BLOCK if the pcap is dirty] **The restricted-browser flags do not cover UDP.** Location: mastra_bridge.py:142-153.
   - What is covered: `--host-resolver-rules` with `MAP * ~NOTFOUND` plus a dead proxy stops DNS, all TCP and QUIC to origins.
   - What is not covered: WebRTC/STUN UDP to IP literals, which the proxy does not carry.
   - Fix: add `--force-webrtc-ip-handling-policy=disable_non_proxied_udp` and `--webrtc-ip-handling-policy=disable_non_proxied_udp`.
   - Copy problem: until the pcap proves it, the page copy overclaims. mastra.html:42 says "every address other than 127.0.0.1 / localhost fails locally". Soften it to what was measured.
   - Also: Chrome enterprise policy (a managed proxy) overrides `--proxy-server`. Note this as an assumption.
4. [ASK] **The scorer invents a score of 0 when the model's reply is unusable.** Location: integrations/mastra-studio/src/mastra/run-probe.mjs:21-22.
   - A non-numeric reply gives `NaN` and is turned into 0. A reply that isn't valid JSON throws, which is better.
   - A fabricated 0 then gets recorded as a probe score. That breaks the honesty rule (cite only what was measured).
   - Fix: throw `bad_score`. F8 then covers it, and nothing is written.
5. [INFO] **The timeout budget doesn't add up.** The MCP client timeout is 14 min (run-once.mjs:51), the scorer adds up to 2 min more (run-probe.mjs:17), and the bridge kills at 15 min (mastra_bridge.py:32). A slow run can therefore be killed while scoring, with a correct `timeout` result and nothing written. Harmless, but make the numbers consistent.
6. [INFO] **A user-supplied `experiment_id` takes priority over the experiment the run created.** Location: mastra_bridge.py:348. The score of run X can be attached to an unrelated experiment Y. This matches the contract, but the UI label should say "attach to experiment".
7. [ASK, follow-up OK] **The ee pin is not enforced at runtime.** `install()` (mastra_bridge.py:372-379) runs `npm ci` against the committed lockfile, so versions cannot drift silently, but `check-ee-loads.mjs` only runs by hand.
   - Fix: run `check-no-ee.sh` after `npm ci` in `install()`, and fail the install if it fails.
   - At minimum, file a ticket so a lockfile bump cannot skip the guard.
8. [INFO] **Small cleanups.**
   - `start_studio` opens `studio.log` and never closes the parent's handle (mastra_bridge.py:200).
   - `node_version()` is called twice (mastra_bridge.py:326).
   - `_spawn_child` reads all of stdout into memory before the 64 KiB check. This is bounded in practice.
   - `MASTRA_STUDIO_PORT` is read by the bridge but never passed to Studio, so a non-default port makes `start` time out (mastra_bridge.py:99-103).
9. [INFO] **Reset does not stop Studio first.** `scripts/reset.sh` wipes `DATA_DIR`, which includes `lab/data/mastra/` (F16 satisfied, and the directory is gitignored via `lab/data/`). A running Studio keeps the deleted LibSQL file open. Reset should call `mastra_cli stop` first.

## Security findings

10. [INFO, checked] **Command and argument injection: none found.**
    - Every spawn is an argv list with `shell=False`: `npx --no-install mastra dev`, `node run-once.mjs`, `npm ci --ignore-scripts`, the browser launch, and `lsof`.
    - The goal text travels as JSON on stdin (mastra_bridge.py:338, 361), never as argv. It is validated in three places: Python, the Node side (run-once.mjs:33) and the MCP tool.
    - The arailctl `mastra` case runs `exec python -m arail.mastra_cli "$@"` with the arguments quoted.
    - `experiment_id` is length-checked and then only used as a tracker key. The builder should confirm that `ExperimentTracker._load` does not build a path from an id like `../../x`. QA should probe this.
11. [INFO, checked] **CSRF and tier gating.** Every mutating route (start, stop, run-once at app.py:2614-2650) goes through `_mastra_gate`: the tier 404 first, then `_check_local_mutation_request`, which enforces loopback bind, Sec-Fetch-Site and an exact Origin/Host match. Studio on :4111 is same-site but a different origin, so it gets a 403. Tests cover all three routes for both the cross-site and the Origin cases, plus the minimalist 404.
12. [ASK] **`NODE_*` is passed through by prefix.** Location: mastra_bridge.py:48.
    - The deny regex strips `NODE_AUTH_TOKEN`. It does not strip `NODE_OPTIONS`, which allows `--require`/`--import` code injection and `--inspect`, a debugger listening on a port. It also does not strip `NODE_EXTRA_CA_CERTS`.
    - None of these leak secrets, but `NODE_OPTIONS=--inspect=0.0.0.0` would open a non-loopback listener.
    - Fix: drop `NODE_OPTIONS`, or allowlist exact `NODE_*` names. Add a test.
13. [INFO, checked] **Secret leakage into the Node child: none found beyond finding 12.** `PATH`, `HOME`, `TMPDIR` and `ARAIL_*` paths are all that pass. The MCP grandchild gets an even narrower set (run-once.mjs:58-61), and the three airgap flags are forced last. `ANTHROPIC_API_KEY`, `*_TOKEN`, `MASTRA_PLATFORM_*` and licence keys are all excluded (test at tests/test_mastra_bridge.py:31).
14. [INFO, checked] **Loopback bind.** `server.host` is set to 127.0.0.1 (config.mjs:26). After start, lsof checks the listen address and stops Studio if it is wrong (mastra_bridge.py:216-219). If lsof is missing, the check is silently skipped. That is acceptable on macOS but should be noted.
15. [INFO, checked] **Missing Chrome.** `open` exits 2 with a plain message and does not fall back to the default browser (mastra_cli.py:26-30). Correct.

## Test coverage assessment

- **All 20 failure-mode rows have a check except these:**
  - F16 (reset) and F18 (zombie processes after stop): no integration tests.
  - F12 (clean machine): only the page state is tested.
  - F1: rests on the gate artifacts. The browser pcap is still pending.
- **Tests that are missing for the findings above:**
  - Concurrency/race test for finding 1
  - Stale-pid test for finding 2
  - `NODE_OPTIONS` test for finding 12
  - Unusable-scorer-reply test for finding 4
- **Coverage on changed lines:** not measured. QA should run `pytest --cov=arail.mastra_bridge --cov=arail.mcp.researcher_tool`. Expect `start_studio`, `stop_studio` and `install` to be under 80%, because they are only exercised through subprocesses.

## Performance assessment

Not on a hot path. The new `_port_open` probe on :4111 in /api/system/health (app.py:11317) adds at most one loopback connect attempt per health call. Acceptable.

## Setup, naming, honesty

- **Setup on a clean machine:** `setup.sh` and `ensure_node` are untouched. Node is optional, `mcp` is an opt-in extra, and the page explains a missing or too-old Node without a traceback (test at tests/test_mastra_bridge.py:67).
- **Naming freeze:** the diff adds no `aerollm`/`AERO_*` changes.
- **Honesty of user-facing text:**
  - "not a 3DEval measurement" appears consistently: in the observation text, the scorer reason, the UI result line and docs/cli.md.
  - The "about 16 non-loopback requests" claim is backed by gates/D1-full-browser-requests.txt.
  - The only overclaim is mastra.html:42 (finding 3).
  - Finding 4 is the other honesty gap.

## Tech debt delta

As the architecture predicted (an isolated Node project plus about 6.3k lines of lockfile, with a disposal rule), plus some new items:
- The ee pin is enforced only by hand (finding 7).
- A pid-file-based process manager (finding 2).
- The restricted-browser launcher is a second egress-control mechanism that the "generalize egress harness" follow-up should absorb.

These are recorded here as follow-ups. They are not added to ARCHITECTURE.md because this review makes no other changes.

## Required actions before merge

1. The owner runs the D1 browser-leg pcap against `arailctl mastra open` with the final flags, and it shows zero non-loopback packets from the browser PIDs. Otherwise the verdict is BLOCK.
2. Fix finding 1 (lock around run-once) and finding 4 (no fabricated score), with tests.
3. Fix finding 12 (drop `NODE_OPTIONS`) and finding 3 (the WebRTC flags and the softened copy), with tests.
4. Fix finding 2 (stale-pid guard), or ticket it.
5. Ticket finding 7 (wire the ee check into install) and the missing T-I-4/T-I-5 tests.

## QA targets

- **Concurrency:** run two run-once calls at the same time (CLI and portal together), and run-once at the same time as POST /api/goal. Check that the owner's goal is never archived mid-run.
- **Process lifecycle:**
  - Stop after `kill -9` of Studio with a reused pid.
  - Portal exit while Studio is running (zombies? F18).
  - A 15-minute timeout with a stalled Ollama: the whole process group dies, including the Python MCP grandchild, and nothing is written.
- **Egress:**
  - Repeat the pcap with the restricted browser.
  - Check that Chrome isn't already running with the same profile.
  - Check behaviour when the "Google Chrome" binary is missing.
- **Env:**
  - Set `NODE_OPTIONS`, `ANTHROPIC_API_KEY`, `MASTRA_LICENSE_KEY` and `MASTRA_PLATFORM_ACCESS_TOKEN` in the portal env, then dump the env of the child and grandchild.
- **Input:**
  - A goal of 2000 or 2001 characters, unicode/RTL, shell metacharacters, and JSON-breaking quotes.
  - `experiment_id` set to `../../etc`.
  - A non-JSON body.
- **Clean machine:**
  - No Node, or Node 20: page state correct, setup passes.
  - Then `arailctl reset` while Studio is running.
- **ee guard:** run `check-ee-loads.mjs` against a fresh trace of `mastra dev` plus run-once on the final build.
