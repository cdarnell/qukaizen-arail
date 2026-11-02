# Sprint: ARAIL CI auth repair

**ID:** 2026-11-01-arail-ci-auth-repair
**Started:** 2026-11-01
**Product:** arail (`qukaizen-arail`)
**Branch:** `qukaizen/arail-ci-auth-repair` (cut from `origin/main` `dca03c28`, fast-forwarded to `5997363e`, which is #210)
**Worktree:** `<session>/qukaizen-arail-ci-auth-wt`
**Debt:** TD48 (`sprints/BACKLOG.md`, CI)

## Task

Bring `DB-Ensure CLI Drivers`, `DaC Feature Tests` and `Blueprint Smoke` (the golden path) back to green on main, without weakening portal auth and without adding an unauthenticated route. Diagnose first. This is a fix, not a feature: a short ledger, no persona pipeline.

## Root cause

CI installs with an unpinned `pip install -e ".[dev]"`, so it resolves what PyPI serves that day; local development follows `uv.lock`. They have drifted: the lock pins starlette 1.3.1 and huggingface-hub 1.25.1, which depends on `httpx` 0.28.1; CI got starlette 1.10.0 and huggingface-hub 2.4.0, which depends on `httpx2` instead, a successor package with its own module name. Starlette does not declare its test client's HTTP library, so in a base-plus-`dev` install huggingface-hub was the only thing that brought `httpx` in; with that gone there is no `httpx` module in CI at all (the last green run, 2026-10-25, already looked like that), and starlette's `TestClient` is now built on `httpx2.Client`. The operator's local venv matches the lock (starlette 1.3.1, `httpx` 0.28.1, no `httpx2`), which is why the same tests pass there. #205 gave ~7k portal tests their credential from a wrapper that does `import httpx` and patches `httpx.Client.send`. With no `httpx` module that raised `ModuleNotFoundError` in every test (756 errors in the DB-ensure job on 2026-10-26), and #206 "fixed" it by catching the `ImportError` and returning, on the belief that only minimal CI jobs lack `httpx`. That turned a loud failure into a silent one: from then on every portal test in a `.[dev]` job reached the portal with no credential and got a bare 401 (3 ids in DB-ensure, 52 in DaC). The golden path is a separate and simpler miss: its inline health script predates portal auth and sends no credential, so `/api/jobs/state` answered 401 for the whole ten-minute window, and because urllib's `HTTPError` is an `OSError` the loop retried a deterministic refusal as if it were a boot race. The portal's auth was correct throughout and is unchanged.

## Phases

| Phase | Subagent | Artifact | Status | Verdict |
|---|---|---|---|---|
| diagnose | none (the orchestrating session) | this file | done | Reproduced in a venv built the way CI builds it: the same 3 and the same 52 ids, byte for byte. The cause is in the install log, not in the portal. |
| build | none | 3 code commits | done | conftest wrapper, bearer canary, golden-path health check. |
| review, test | none | none | skipped, on purpose | No portal code changed (`src/` is untouched), so the brief's trigger for an architect or QA pass is not met. The risk is in the CI wiring, which only a CI run can prove: see Evidence. |
| ship | none | PR [#211](https://github.com/cdarnell/qukaizen-arail/pull/211) | open, not merged | All 5 checks green on head `9f557138` (the three target jobs, Import and route guardrail, Model Forge). The operator merges. |

## Decisions log

| Date | Decision | Why |
|---|---|---|
| 2026-11-01 | Fix the harness once, not the 55 tests one by one. The wrapper no longer imports an HTTP library by name; it wraps the `send` that `TestClient` inherits (`orig_send = TestClient.send`). | Which library sits under `TestClient` is starlette's choice and it has changed once already. The inherited `send` is right for `httpx`, for `httpx2`, and for both installed. No test is skipped and none needed an edit. |
| 2026-11-01 | The guard stays, but narrowed to "no `TestClient` can be built": `except (ImportError, RuntimeError)` around `from starlette.testclient import TestClient`. | Starlette raises `RuntimeError`, not `ImportError`, when it has no HTTP library. The old guard also hid the real fault, which is why the canary below exists. |
| 2026-11-01 | Add `tests/test_conftest_portal_bearer.py` (5 tests) and list it first in the DB-ensure and DaC jobs. | The failure mode that kept three jobs red from 2026-10-26 was "a harness fault looks like 55 unrelated 401s". Two of the five fail on the old conftest in a CI-faithful venv, with a message that names the library `TestClient` stands on. The other three pin behaviour the rewrite must keep (an explicit `Authorization` is kept; `/api/host/v1/*` is left to its own `bhc1_` credential; `real_auth` opts out). |
| 2026-11-01 | Golden path: send the bearer from `<ARAIL_DATA_DIR>/portal.token` to the two protected checks only. Keep `/api/system/health` and the memory service anonymous. | The anonymous list is part of the contract (the SRE watcher probes `/api/system/health`); checking it anonymous keeps it so. The bearer never goes to the memory service, which is another listener. |
| 2026-11-01 | Golden path: a 401/403 to a request that carried the bearer ends the wait at once (same diagnostics dump as a timeout), and an unauthenticated `GET /` that lands on `/login` is not a healthy dashboard. | A refusal cannot be fixed by waiting; the old loop spent 600 s on it. `urllib` follows the 302 from `/` to the login page and gets a 200, so the old dashboard check would have passed on the login form. |
| 2026-11-01 | Golden path: after the checks, the same protected route with no credential must answer 401, and no response body may contain the token before `ci-artifacts/` is written. | Without the negative control a bearer that "works" proves nothing: a portal with default-deny off would pass every other check. The artifact is uploaded, so the bodies must be clean. |
| 2026-11-01 | No persona pass (architect, QA). | No portal code changed. |
| 2026-11-01 | Not fixed here, filed as TD49 to TD51: CI vs `uv.lock` drift; the egress guard not covering `httpx2`; code and tests that import `httpx` by name. | Each is its own decision. TD50 is an airgap question and needs an architect and QA pass. |

## Evidence

Numbers are from this PR's CI run (head `9f557138`, all 5 checks green) unless a block says "this Mac". "A venv built as CI builds its own" means `python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"`, which resolved starlette 1.10.0, fastapi 0.162.0, `httpx2` 2.16.0 and no `httpx`, the set CI printed on 2026-11-01.

**1. DaC job, pytest step: `298 passed, 4 deselected`** (61.9 s). Before, on main: `52 failed, 241 passed, 4 deselected`. In a venv built as CI builds its own, before: the same 52 ids as CI, no more and no fewer.
- *Means:* every selected test passed with the bearer reaching the portal. 298 is the 293 tests selected before plus the 5 canary tests. The 4 deselected are the `live_stt`/`live_ocr` hardware tests, excluded by design.
- *Bar and why:* 0 failed. The job guards the DaC feature set, and while it stays red a real regression hides among the known reds.
- *Out of range:* a failure in `test_conftest_portal_bearer.py` means the harness broke, and its message names the library `TestClient` stands on. A feature test failing alone with `401` or `auth_required` is the same family. Anything else is a feature regression.
- *Where:* `tests/conftest.py::_patch_testclient_send_once`, then `tests/test_conftest_portal_bearer.py`.

**2. DB-ensure job, pytest step: `761 passed, 2 skipped, 3 xfailed`** (29.8 s). Before: `3 failed, 753 passed, 2 skipped, 3 xfailed`.
- *Means:* the 3 ids in `tests/test_qa_retrieval_honesty.py` pass. 761 is the 756 that ran before plus the 5 canary tests. The 2 skipped are the Atlas schema-parity tests (`atlas` is a developer tool, never installed in CI, by design); the 3 xfailed are marked expected failures. Also in that file, `test_a_hostile_interpreter_path_cannot_inject_a_response_header` was "passing" before for the wrong reason (a 401 carries no injected header and an empty reason string); it now exercises the header sanitizer for real.
- *Bar and why:* 0 failed, same reason as block 1.
- *Out of range:* same family as block 1. A pass that is new here deserves the same suspicion: ask what the test was asserting when it passed on a 401.
- *Where:* `tests/test_qa_retrieval_honesty.py`; the harness as in block 1.

**3. DB-ensure job, the four CLI drivers** (they had not run on main since #205: the pytest step failed first and skipped every step after it). `status_driver.sh` `OK: 14 scenario(s) passed, 2 skipped`; `qa_db_collector_driver.sh` `OK: 7`; `qa_db_seamless_driver.sh` `OK: 9`; `qa_db_ledger_driver.sh` `FAILED: 1 scenario(s) failed, 5 passed`.
- *Means:* the three gating drivers are green. The ledger driver shows its one known failing scenario, QA-13 ("filed, deliberately unfixed", behind `continue-on-error`), identical to the last green run on 2026-10-25. The 2 skips in `status_driver.sh` are scenarios that do not run on Linux.
- *Bar and why:* 0 failures in the three gating drivers, and exactly `1 failed, 5 passed` in the ledger driver. That count is a tripwire: a second failing scenario would be a new defect hiding behind `continue-on-error`.
- *Out of range:* a gating driver exits non-zero with a `FAIL:` line that names the scenario. Any ledger count other than `1 failed, 5 passed` is a new defect, not QA-13.
- *Where:* `tests/cli/*_driver.sh`; QA-13 in `sprints/2026-08-10-arail2-persistence-instantiated/BACKLOG.md`.

**4. The canary, `tests/test_conftest_portal_bearer.py`: 5 tests.** On the old conftest in a venv built as CI builds its own: 2 fail, 3 pass. On the new conftest: 5 pass with `httpx2` only (CI), with `httpx` only (the lockfile: starlette 1.3.1, `httpx` 0.28.1), and with both installed.
- *Means:* the two that fail on the old harness are the ones that detect the fault (`no portal bearer reached the request (it carried None). TestClient is built on httpx2.Client`; and `401 {"error":"auth_required"}` from the real portal). The other three pin what the rewrite must keep (an explicit `Authorization` is kept; `/api/host/v1/*` is left to its own `bhc1_` credential; `real_auth` opts out), so they pass either way.
- *Bar and why:* the two must fail on the old harness and all five must pass on the new one. A canary that cannot fail proves nothing.
- *Out of range:* `carried None` means the wrapper did not install. `auth_invalid` or `token_file_unsafe` in place of `auth_required` means the header arrived and the portal refused it: look at `auth.check_bearer` and the fixture's token file.
- *Where:* the file itself.

**5. Golden path, health step: 5 s** (it timed out after 600 s before). It printed all five lines of its table and ended `bearer: token id 372dff61 (the id only)`. The public log has 0 occurrences of an `arl1_` token, 0 uploaded files contain one, and the saved dashboard is 30,178 bytes with 0 password inputs.
- *Means:* the dashboard renders for a program holding `portal.token` (not the login form); a protected API answers with the bearer; the health probe and the memory service answer with no credential; and the same protected API answers 401 with no credential, which is the proof auth is on.
- *Bar and why:* all five lines, in seconds. A refusal now ends the wait at once, so a slow green means a slow boot and never an auth problem.
- *Out of range:* `health check refused: <endpoint>: HTTP 401 (<reason>)` is the portal's own reason (`auth_invalid`, `token_file_unsafe`, `token_scope`): the auth contract or the token file changed. `health checks timed out: ... (no portal.token ever appeared at <path>)`: the portal booted without minting one (not onboarded, or the data dir moved). `auth is not enforced: ... answered HTTP 200`: default-deny is off; stop and read `portal_auth`. `a portal response body contains the bearer token`: a page echoes the header; stop.
- *Where:* step "Wait for local health surfaces" in `blueprint-smoke.yml`; on failure, `ci-artifacts/diagnostics.txt` in the uploaded artifact.

**6. Health-script drills (this Mac: a real portal and memory service started the way `start.sh` starts them, on ports 18080 and 18084, not CI).** The original script: `health checks timed out: HTTP Error 401: Unauthorized` (12 s with a shortened deadline; 600 s in CI). The new script: green in 0.07 s. Token file at mode 0644: stops in 0.125 s with `HTTP 401 (token_file_unsafe)`. Token file missing: times out and names the path. Started with both ports closed: green at 2.1 s. Token withheld for 6 s: green at 6.1 s. Pack path single-quoted: read correctly, green. Pack path wrong: falls back to the default path, green. A stand-in server with no auth: `auth is not enforced`. A stand-in that echoes the bearer into a page: refuses, and writes nothing.
- *Means:* each failure names its own cause, and the success path is not luck of timing.
- *Bar and why:* each drill fails with the message for its cause, and none waits out the deadline. The original script reproduces the CI failure on the live portal, which is the evidence the cause is the script and not the portal.
- *Out of range:* a drill that waits for the deadline instead of failing at once means the fail-fast path regressed.
- *Where:* the same step.

**7. The other jobs.** `Import and route guardrail`: `21 passed` (the whole job took 32 s). `Model Forge stub-provider suite`: `444 passed, 1 skipped, 4 deselected, 14 xfailed`, identical to main's last CI run. `Buddy floor` did not run on this PR (path-filtered; the PR touches none of its paths); its command in a minimal venv (Python 3.12, no starlette or httpx) gives `3 passed, 1 skipped` on this branch and on main.
- *Means:* the narrowed guard is still a clean no-op where no `TestClient` can exist, and the Nucleus job, which now gets the bearer for real, gives the same outcome as before.
- *Bar and why:* identical to main, because this PR is not meant to change them.
- *Out of range:* any difference from main.
- *Where:* the three workflow files, `tests/conftest.py`.

**8. Full local suite, before vs after: the same 78 failing ids on both sides.** This Mac, a venv built from `uv.lock` with the `dev` and `maximus` extras (starlette 1.3.1, `httpx` 0.28.1); one pytest process per test file, 8 at a time, 600 s per file; `tests/test_host_edge_brain.py` is left out on both sides because that venv has no `openai`. Untouched `origin/main` (`5997363e`): 81 failing ids (73 failed, 7 errors, 1 file timed out). This branch: 79 (71 failed, 7 errors, 1 file timed out), and the 5 new canary tests pass. 78 ids are common. The 4 that differ are noise, and each was re-run serially on both trees: `test_two_concurrent_allocations_for_different_slugs_can_pick_the_same_block` and `test_status_command_under_two_seconds_with_three_instances` failed only on main under 8-way load and pass 3 of 3 on both trees; `tests/test_warm_up.py` (hung on main) and `tests/test_boot_models.py` (hung on the branch) pass 2 of 2 on both. A hang also shows up in `tests/test_autoresearch_e2e_fake_aerollm.py` on both trees, about 1 run in 10, so hangs are a standing flake and not something this change adds.
- *Means:* in an environment where `httpx` already existed, the harness change moves nothing: there the old and new wrappers are the same function (`TestClient.send is httpx.Client.send`). The only new results are the 5 canary tests passing.
- *Bar and why:* the same failing ids on both sides. This PR changes test-harness code only, so nothing outside the 5 new tests should move.
- *Out of range:* an id that fails only on the branch is a regression until a serial re-run on both trees says otherwise; that is how the 4 above were cleared. The brief's 151 failing ids were measured in the operator's own venv; this venv gives 81 on main, so only the before/after within one venv is comparable.
- *Where:* the per-file results are not committed (session scratch). To reproduce: `uv sync --frozen --extra dev --extra maximus` into a scratch environment, then run each tree one test file at a time.

## Follow-ups (filed in `sprints/BACKLOG.md`)

- **TD49: CI floats, local follows `uv.lock`.** The lock pins starlette 1.3.1, fastapi 0.140.7, huggingface-hub 1.25.1 and `httpx` 0.28.1; an unpinned install resolved 1.10.0, 0.162.0, 2.4.0 and `httpx2` 2.16.0. A CI-only red cannot be seen locally until someone rebuilds a venv the way CI does. Trigger: the next dependency refresh, or any red that passes locally.
- **TD50: the egress guard covers `httpx` only, and `httpx2` is what huggingface-hub 2.x and starlette 1.x use.** Verified: with `LAB_MODE=airgapped` and the guard installed, an `httpx2` request to `http://egress-probe.invalid/` fails at the network layer (`ConnectError`), where `requests` gets `EgressBlocked`. `tests/test_egress_httpx.py` skips itself where `httpx` is absent, so nothing is red; `README.md:305` and `docs/PRIVACY.md:59` promise `httpx` coverage and name no `httpx2` gap. This is the airgap invariant, so it needs its own architect and QA pass. Not fixed here. Trigger: before the next release or any repeat of the airgap claim.
- **TD51: code and tests that import `httpx` by name.** `tests/test_host_edge.py`, `tests/test_host_edge_brain.py` and `tests/portal/test_opencode_routes.py` do not collect where only `httpx2` is installed; `src/arail/open_notebook_seed.py` imports it at module level. No workflow runs them, so nothing is red and nothing guards them. Trigger: when any is added to a workflow, or with TD50.

## Notes

- The brief and BACKLOG TD48 name `./arailctl auth mint`. The verb is `./arailctl auth token [--rotate] [--world SLUG]`; it mints the file if missing and prints the token's id and path, never its value. The health script reads the file the portal's startup hook already wrote.
- The home clone (`~/ProJects/qukaizen-arail`) was not touched: its branch, working tree and `.venv` are as they were. All runs used venvs in this session's scratch directory and in the worktree.
- `X-Retrieval-Reason` is cut to 200 characters (`app.py`: `_header_safe(reason)[:200]`) and starts with the interpreter's path, so a venv path of about 150 characters or more pushes the "run `arailctl install`" hint out of the header. One DB-ensure test asserts that hint, so it fails in a deep scratch venv and passes at CI's `/home/runner/work/...` path. Not changed here.
- The full suite binds `127.0.0.1:8089`, Buddy's default edge port, while some of its tests run, and a run that is killed part-way leaves `real-boot` labs behind (`start.sh --root` under a `tmp.*/real-boot` directory) and the port held. Do not run it beside a live lab, and look for orphans after an interrupted run. This session killed two such runs and removed what they left.
- Other open PRs (#207, #209) run the same three workflows and go green once they pick this up from main.
