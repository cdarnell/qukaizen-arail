# Review: the egress guard covers the httpx family (TD50, runtime half of TD51)

**Date:** 2026-11-01
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) at `97fe5ede` (code identical to `f2218b1c`; HEAD adds ledger text only, checked with `git diff f2218b1c HEAD --stat`: 1 file, `SPRINT.md`)
**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `ec2a12be`, with the errata this review adds (same commit as this file)
**Diff reviewed:** `git diff origin/main...HEAD`, `origin/main` = `de1ce285` (contains #211), 24 files, +5,420 / -214
**Reviewer:** architect (review mode), Claude Opus 5.5. Every result below marked *run* was executed in this session in the scratch venvs; the probe scripts are in the session scratch directory (`<scratch>/rev/*.py`, not committed).

## Verdict: BLOCK
Superseded by the re-review at the end of this file (verdict: WEAK_PASS).

The guard itself is right, and I re-derived that rather than read it: the brief's probe ends in `EgressBlocked` in all 20 cells (5 venvs, both libraries, sync/async, client built before/after), huggingface_hub is refused once per call on 2.4.0 and 1.25.1, the five CI legs pass from the workflow's own text with leg 5 green because its sentinel failed, and five of my eight new mutants are caught. One finding blocks, and it is on the airgap invariant: in the one state this PR newly detects (a family member installed but unpatchable), the portal and the agent loader keep serving in airgapped mode with that library open, the PR never states that as a decision, and every operator-facing surface contradicts what the guard knows: the modal's pill stays green, its definition says "Cloud-provider APIs are blocked", its paragraph says httpx2 is caught, and the only trace is a bullet inside a collapsed `<details>` plus one activity-log line that no test pins (I measured an async httpx2 request reach the network layer in exactly that state). No supported library version reaches that state today and `main` is strictly worse (silent), so the fix is small: make the UI stop contradicting itself, pin the portal's signal with a behavioural test, and write the decision down (with fail-closed filed for the operator). Five WEAK findings are documentation over-claims and test gaps the same fix pass should close. Severity scale used here: **BLOCK** = must change before QA; **WEAK** = change in this fix pass (or file, where the item says so); **NIT** = optional, or file.

## How to read the numbers

Every number below carries four parts. For the tables, the columns are: what it means, the bar and why, what out of range means, and where a failure lives.

## What I re-derived (run, not read)

| Check | Result | Means | Bar and why | Out of range | Where |
|---|---|---|---|---|---|
| The brief's probe, fresh interpreter, `after_probes.sh` (ci 2.16.0, lock 0.28.1, both, py310 2.14.0, neither) | 20 of 20 cells `EgressBlocked`, 0.00 to 0.01 s; neither: no library, nothing to probe | `EgressBlocked` = refused before any socket I/O | Every cell, because the airgap promises cloud calls are refused at the HTTP layer | Any other exception = that client shape left the guard behind | `egress._install_httpx_family_guard`; FR-1 |
| huggingface_hub, same script (2.4.0 ci, 2.4.0 py310, 1.25.1 lock) | 9 of 9 calls `EgressBlocked`, exactly 1 refusal each, at most 0.02 s | One audit line = one refused attempt | Exactly 1, so a refusal spends no retry budget and floods no log (before: 0 refusals, 23 s of retries) | More than 1 = hf retries a `RuntimeError`; `ConnectError` = unguarded | FR-5 |
| Adjacent libraries, same script | `urllib3` `NameResolutionError`, `httpcore2` `ConnectError`, 0 audit lines | The documented lower-layer gap is still open | Must stay open until TD53, and be documented | `EgressBlocked` = the gap closed; the BY-2/BY-3 tripwires say update the docs | BY-2, BY-3 |
| The five CI legs, `emulate.py` (the YAML's own `run:` text, install steps skipped, prebuilt venvs) | Legs 1 to 4: 320 / 320 / 320 / 356 cases, 0 failed, 0 skipped, all 13 files counted (per file, leg 1: 17, 63, 6, 20, 43, 18, 22, 20, 28, 46, 17, 14, 6); SDK step 3 cases; leg 5: pytest `1 failed, 1 passed`, checker ok, step green | Each leg's tests pass in its library state, and the proof can fail | 0 failed, 0 skipped, every file at least 1 case (a skip is how TD50 hid); leg 5's sentinel must fail | A skip names the test; leg 5 green with a passing sentinel = vacuous | `.github/workflows/egress-guard.yml`; `tests/egress_ci_check.py` |
| Order independence (E13): the 13 files, one process, **reversed**, both venv | 356 passed, checker ok (forward: 356) | Patch state does not leak between files | Identical counts both ways, because the suite shares one process | A difference names the file whose autouse reset is incomplete | the file whose count moved |
| `install_guard()` start-up cost (E11), 5 fresh interpreters each | medians: ci 8.6 ms, lock 10.3 ms, both 16.2 ms, py310 8.2 ms | What portal, doctor and worker start pay for eager import | Under 50 ms per library (portal start takes seconds; doctor and worker are CLIs) | Near 50 ms = revisit lazy import for the CLI paths | `egress._install_httpx_family_guard` |
| `tests/test_queuellm_rename_frozen_surface.py` on HEAD vs the orchestrator's saved run on main | 2 failed, 3 passed; the 13 `E ` lines identical after normalising the tree path | Pre-existing failures, not this PR's | The failure text must not change (it is red on main) | New text = this PR touched a frozen path or lost a frozen token | that test's `E ` lines |
| Scope greps on the diff | No frozen path touched (`deep_*.py`, `dac_world/`, `buddy_host/contract/`, `conftest.py`, `pyproject.toml`, `uv.lock`, `CHANGELOG.md`, `NOTICE`, licences); no removed line names `aerollm`/`AERO_`; no added line contains the word "free" | The brief's constraints hold | Zero of each | Any hit is a constraint break | `git diff origin/main...HEAD` |
| My mutants (scratch export of HEAD, the 13 files, ci venv, control 0 failing ids in 320) | 5 caught, 3 survived (table under "Test coverage") | Whether a mitigation has a test that fails without it | At least 1 new failing id per mutant | A survivor = a mitigation nothing pins | named per mutant |

## Findings

### B1 (BLOCK). An incomplete guard leaves an airgapped lab serving with the library open, the UI says the opposite, and the PR does not state the decision

- **Where:** `src/arail/portal/app.py:901-906` (startup catches `EgressGuardIncomplete`, emits one activity line, continues); `src/arail/agents/loader.py:313-317` (logs, continues loading agents); `src/arail/portal/app.py:11962-11969` (airgapped definition always says "Cloud-provider APIs are blocked."); `src/arail/portal/static/nav.js:90-94` (pill `ok` for airgapped; `guard_installed` is read nowhere: `grep -rn guard_installed src/` finds only its producer, `app.py:12000`); `src/arail/portal/templates/_airgap_modal.html:132-148` (the new entry lands in a `<details>` that is collapsed by default, next to a paragraph that says the guard catches `httpx2`); `docs/PRIVACY.md:68-73`.
- **Evidence (run):** `<scratch>/rev/probe_incomplete_portal.py httpx2`, ci venv, airgapped, `httpx2.AsyncHTTPTransport` deleted first (the EG-20 simulation of a renamed class): `install_guard raised: EgressGuardIncomplete`; `status.lab_mode: airgapped` (nav.js paints it `ok`); `status.guard_installed: False`; `status.definition: ... Cloud-provider APIs are blocked. ...`; `status.known_gaps[-1]: httpx2: installed, but the guard could not patch it (AsyncHTTPTransport.handle_async_request is missing); its requests are NOT checked`; then `httpx2 sync client: EgressBlocked`, **`httpx2 async client: ConnectError`** (the request reached the network layer while the status said blocked). Mutant R6 (startup swallows `EgressGuardIncomplete` silently) left all 320 cases green: the portal's only signal is pinned by a source-string match (`tests/test_egress_docs.py:314-322`), not by behaviour.
- **Why it blocks:** `CLAUDE.md` says of `LAB_MODE=airgapped`: "Don't relax this default." In this state the default is relaxed for one library while the badge, pill, definition and modal paragraph say it is not. ARCHITECTURE decision 1 (mine) chose "raise" on the assumption that every caller turns the exception into a visible signal (A9); for the portal that signal is one activity line and a hidden bullet. What keeps this from blocking the mechanism itself: no supported version reaches the state today (huggingface-hub, anthropic and openai pin `httpx2<3`; the lock pins httpx 0.28.1; the daily legs go red the day a release renames a transport), a member whose import raised cannot be used by anyone, and `main` is strictly worse (the same state is silent there, and the CI-style default *is* that state for httpx2).
- **Fix (before QA):**
  1. `get_airgap_status`: when `uncovered()` is non-empty, the airgapped definition names each unguarded library and says its requests are **not** blocked (the rest of the text stays).
  2. `nav.js`: when `data.guard_installed === false`, paint the pill `warn` and show the unguarded entries outside the collapsed `<details>` (or open it); keep `textContent` (DOC-6).
  3. `docs/PRIVACY.md:68-73`: say plainly that the portal and the agents keep running with that library unguarded, and that only Model Forge's worker stops.
  4. Move the startup block into a small function (for example `_install_egress_guard()` in `portal/app.py`) and test it behaviourally: incomplete guard: one activity warning naming the library and the fix; whole guard: none. Extend DOC-5 to assert the definition is qualified when `uncovered()` is non-empty. Re-run R6: it must go red.
  5. Record the decision in `SPRINT.md`'s Decisions log and in the PR description: "With an incomplete guard the portal and the agent loader keep serving with that library unguarded; only Model Forge's worker exits." File **TD56** (fail closed: refuse to start in airgapped mode, and refuse the toggle to airgapped, while `uncovered()` names a library whose import succeeded). My recommendation is to take TD56 before the next release; it is the operator's call because it trades availability for the invariant.

### W1 (WEAK). README's "and so everything built on them" over-claims for `requests` and `urllib.request`

- **Where:** `README.md:302-305`. `docs/PRIVACY.md:59-60` scopes it correctly ("the adapter every new `Session` gets", "the default opener") but its gap list (`:75-94`) never names what falls outside that scope.
- **Evidence (run):** `<scratch>/rev/probe_overclaim.py`, ci and lock venvs, airgapped, `.invalid` host: `requests.Session()` built **before** install: `ConnectionError`, 0 audit lines; a session built after install that mounts an `HTTPAdapter` imported **before** install: `ConnectionError`, 0; `urllib.request.build_opener().open(...)`: `URLError`, 0; lancedb's `lance_namespace_urllib3_client` (installed in both venvs): `MaxRetryError`, 0 (the documented urllib3 gap). The httpx family has no such hole (class-level patch: built-before clients are refused). Exposure today, measured: importing `arail.portal.app` creates 0 `requests.Session` and 0 `OpenerDirector` objects before `_startup` installs the guard (`probe_portal_preinstall.py`), and after install no loaded module holds the stock adapter class (`probe_stock_refs.py`, 1,122 modules in ci, 1,105 in lock). So no live path in the portal today; any other process, or code imported before the guard that keeps such an object, can hit it.
- **Fix:** README: scope the sentence, for example "wraps `requests` (new sessions), `urllib.request` (the default opener) and every client of `httpx` and its successor `httpx2`, such as the Hugging Face downloader and the Anthropic and OpenAI SDKs". PRIVACY: add a gap bullet: "A `requests` session or adapter created before the guard was installed, and a urllib opener built with `build_opener()`: the guard swaps the defaults, it does not reach objects that already exist (the httpx family is patched on the class and has no such gap)." Add a documented-gap tripwire (BY-5) for both shapes, in the style of BY-2.

### W2 (WEAK). "Other processes" omits the lab's own Python subprocesses; README omits other processes; the router comment is unscoped

- **Where:** `docs/PRIVACY.md:90-94`; `README.md:306-309` (the builder's AF-2 item 6); `src/arail/router/core.py:14-17`.
- **Evidence (read; DOC-9 run):** `install_guard()` is called in exactly four files (DOC-9 green; my grep agrees). The lab spawns `python -m arail.skills.goal_parser._subprocess_runner` (`skills/goal_parser/__init__.py:236-243`, runs the ModelRouter) and `python -m arail.router.airllm_worker` (`router/backends.py:1091-1101`, `from airllm import AutoModel`; `airllm_worker.py:74` falls back to the repo id when the local model directory is missing); neither installs the guard. Setup's and upgrade's Hugging Face downloads run in separate interpreters (`scripts/setup.sh:1939`, `scripts/upgrade.sh:177`), yet README says the guard covers "the Hugging Face downloader" with no process scope. In the goal-parser subprocess the router comment's "the egress guard ... would block the eventual API call anyway" is false (the router's own refusal is the only line there).
- **Fix:** PRIVACY bullet: "... run outside it, including the lab's own Python subprocesses (the goal parser's model subprocess, the AirLLM worker) and every other `./arailctl` command." README parenthetical: add "other processes, such as setup's and upgrade's model downloads". Router comment: "the egress guard, in a process that installed it (the portal), would block ...". File **TD58** (decide whether lab-spawned Python subprocesses install the guard; the AirLLM worker's hub fallback in airgapped mode was read, not run).

### W3 (WEAK). A family member installed after `install_guard()` is unguarded, the guard calls it "absent", and a second install is a no-op

- **Where:** `src/arail/egress.py:488-489` ("A member that is NOT installed is "absent": nothing in this process can use it") and `:574`; `:630-631` (`_INSTALLED` short-circuit); `docs/PRIVACY.md:67-68` (the doctor "prints which of the four this lab's guard covered"). Reachable: `/api/plugins/install` (`portal/app.py:5059-5083`) runs `pip install -r requirements.txt` into the running portal's interpreter (`plugins/manager.py:78-81`); subprocess pip is itself a documented gap, so it works airgapped.
- **Evidence (run):** `<scratch>/rev/probe_late_install.py`, lock venv (httpx only), httpx2 2.16.0 made importable after the guard: `coverage = {... 'httpx2': 'absent', 'httpx': 'guarded'}`, `_INSTALLED = True`; then `import httpx2`: `handle_request guarded: False`, outcome **`ConnectError`** (reached the network layer); a second `install_guard()`: still unguarded, `coverage[httpx2] = absent`. Pre-existing class of gap (main behaves the same for httpx), but this PR's new docstrings and doctor line assert the opposite.
- **Fix (this PR):** correct both docstrings and the PRIVACY sentence ("... covered when the process started; a library installed while the lab runs is not guarded until the lab restarts"); add "restart the lab" to the plugin installer's success line. File **TD57** (a post-import hook for members absent at install time, applied in the loader's `exec_module`, with a fresh-interpreter test).

### W4 (WEAK). The realistic broken install is untested: an `ImportError` treated as "absent" survives (mutant R5)

- **Where:** `tests/test_egress_httpx.py:586-625` (EG-A3) and `tests/test_egress_httpx_fresh.py:237-292` (FR-4) simulate the broken import with `RuntimeError` only.
- **Evidence (run):** R5 inserts `except ImportError: result[name] = "absent"` before the generic branch: 0 new failing ids in 320 cases. A real broken install raises `ModuleNotFoundError` (httpx2 present, httpcore2 missing, which is exactly the `neither` venv's huggingface_hub state).
- **Fix:** parametrize EG-A3's import mode and FR-4 over `RuntimeError` and `ModuleNotFoundError`. Re-run R5: it must go red.

### W5 (WEAK). A green CI run does not show its evidence (the workspace's "runs must teach" rule)

- **Where:** `.github/workflows/egress-guard.yml:117` and `:132` use `-rs` only; the SDK step (`:120-133`) installs the SDKs after "What this leg installed" (`:97-109`) and never prints their versions.
- **Evidence (run):** the emulated leg-1 logs contain no `EVIDENCE` table (FR-1 seconds, FR-5 refusals, SDK chains) and no SDK version.
- **Fix:** add `-rP` to both pytest lines (it prints captured output only for tests that print, which are the evidence tables) and print the `anthropic`, `openai` and `httpx2` versions after the SDK install.

### NITs (optional, or file)

- **N1.** `docs/PRIVACY.md:43-49` says the IP is checked against loopback, RFC1918 and link-local; `airgap.is_local_ip` uses `ipaddress.is_private`, which also passes `192.0.2.7`, `198.51.100.7`, `203.0.113.7`, `198.18.0.1`, `240.0.0.1`, `0.0.0.0`, `192.0.0.8`, `fc00::1`, `2001:db8::1` (run; `100.64.0.1` and `8.8.8.8` are refused). Pre-existing; AF-1 is the same fact. One sentence: "... or another range Python's `ipaddress` treats as private (documentation, benchmarking and reserved ranges, IPv6 unique-local)".
- **N2.** `src/arail/nucleus/worker.py:39` lets `EgressGuardIncomplete` escape as a traceback, and `nucleus/phases.py:166` inherits the worker's stderr, so a Model Forge user sees a raw traceback (arail's checklist: no tracebacks for non-experts). Catch it in `main`, write `worker: <message>` to stderr, return 1 (still fail closed).
- **N3.** The audit `caller` for a refused huggingface_hub or openai call names a library frame (run: `huggingface_hub.hf_api.HfApi.model_info`, `openai._base_client.SyncAPIClient._send_request`; anthropic is special-cased and gives the lab function). That is what the modal's Recent activity will show for every fresh-install refusal. Pre-existing design; add `huggingface_hub` and `openai` to `_SKIP_MODULES` with EG-21 cases, or accept.
- **N4.** The RLock has no test that fails without it (mutant R4 survived). That is because nothing observable breaks without it: the `_arail_guarded` marker and the conditional adapter save already make concurrent installs safe, and EG-19 pins those. Keep the lock (it is re-entrant, so a nested install cannot deadlock); no action.
- **N5.** `.github/workflows/egress-guard.yml:71` (`pip install --upgrade pip`) and `:79` (`pip install httpx`, leg 4) have no retry; a PyPI blip turns a leg red. The repo's other workflows have the same pattern.
- **N6.** `airgap.is_local_host` (`airgap.py:136-144`) sets the process-wide default socket timeout around each DNS lookup; that timeout does not bound `gethostbyname`, and another thread creating a socket in that window inherits 1.5 s. Pre-existing, but every httpx2 request to a hostname (including `localhost`) now takes that path. It also means refusal latency depends on the resolver (fast here and on GitHub; slow on a host whose configured DNS server is unreachable). File as **TD59**.
- **N7.** `src/arail/portal/app.py:773-775` says the Knowledge Canvas backend "can't import on a stock lab venv"; it imports in ci and lock (run). Pre-existing comment, not in the diff.
- Pre-existing, not counted: `nav.js:124-127` writes `url_host` and `caller` from the audit log with `innerHTML`. The PR's new entry uses `textContent` (DOC-6); the old path is outside this diff.

## Spec adherence

The implementation follows ARCHITECTURE decisions 1 to 8 and the atomic commit plan; commits 1 to 7 map one to one. Deltas, all logged in BUILD_LOG and all acceptable: AF-1 (`PUBLIC_TEST_IP`), extra contract tests (EG-A1 to EG-A7, ON-3, DOC-6, DOC-8, DOC-9, CI-5, CI-6), two guard branches the design did not spell out but that serve F2 (a lookup that raises, a `setattr` that raises), `EGRESS_TESTS` shellcheck directives. The one place the build is faithful to a design assumption that turned out weaker than designed is B1 (decision 1 and A9); that is the design's defect, corrected in the errata.

## Code quality findings

- [INFO] `egress.py`: each new function is short and single-purpose; `install_guard` is about 30 lines of logic under a long docstring. Names say what they do. Comments explain why. The three copies of the decision tree (adapter, `_check_egress_or_raise`, `_check_httpx_url`) are pre-existing.
- [INFO] `httpx_family.py` is stdlib-only and pinned so by test; `preferred()` refuses to fall back past a broken member (mutant caught in step 1).
- [INFO] `doctor._guard_summary`, the status-endpoint change and the seed change are minimal.

## Security findings (what I checked, by name)

- [INFO] Secrets in URLs: EG-1 asserts `SECRET_DO_NOT_LEAK` is in neither the audit line nor the exception; my R2 (audit records the whole URL, 9 red) and R3 (exception carries the whole URL, 1 red) prove those assertions can fail.
- [INFO] The `allow_egress` ratchet (EG-8) and the bootstrap host allowlist (EG-9) hold for httpx2; the airgapped default is not relaxed by any code path in the diff.
- [INFO] The decision is on the destination: a loopback proxy carrying a public request is refused (EG-11, 3 shapes; R8 turns all three red); a public proxy carrying a local destination is the documented TD54 gap (BY-4).
- [INFO] In-process transports stay unguarded on purpose; my R1 (patch Mock and ASGI transports too) turns EG-13 red.
- [INFO] The new status entry carries a library's exception text and is rendered with `textContent` (DOC-6).
- [INFO] Workflow: `permissions: contents: read`; no secrets; no `${{ github.event.* }}` interpolated into any `run:` (matrix values are static strings), so no script injection; actions pinned by major tag, as in the repo's other workflows; the SDK step installs the latest SDKs into a throwaway CI venv only.
- [INFO] Dependencies: none added to `pyproject.toml` or `uv.lock`.
- [BLOCK] B1 (fail-open in the incomplete state). [WEAK] W1, W2, W3 (coverage over-claims in privacy docs).

## Test coverage assessment

Line coverage was not measured (no coverage tool in the plan). Mutation evidence stands in: the builder ran 16 + 6 + 7 + 9 + 14 + 15 mutants and 5 planted CI faults, the orchestrator 2 + 10, and I ran 8 more on a scratch export (`<scratch>/rev/mutants_rev.py`, results `<scratch>/results/rev-mutants.json`):

| Mutant (mine) | Result | Named tests |
|---|---|---|
| R1 Mock and ASGI transports patched too | caught, 6 | EG-13 (for the reason), plus EG-A5, EG-A6, EG-18, EG-19, FR-3 (structural) |
| R2 audit line records the whole URL | caught, 9 | EG-1, EG-2, EG-9, EG-10, FR-5, `test_egress_guard.py::TestAuditLog` |
| R3 refusal message carries the whole URL | caught, 1 | EG-1 |
| R4 no lock in `install_guard` | **survived** | none (see N4: nothing observable breaks) |
| R5 an `ImportError` counts as absent | **survived** | none (W4) |
| R6 portal startup swallows an incomplete guard | **survived** | none (B1 item 4) |
| R7 raise only when airgapped | caught, 1 | EG-20 [hybrid] |
| R8 check after the library's own I/O | caught, 19 | EG-1, EG-11 (3 shapes), EG-14, EG-7, ... |

### F1 to F29: the test that fails if the mitigation is removed

| # | Failure | Test that fails without the mitigation | Shown failing by | Tied? |
|---|---|---|---|---|
| F1 | Member installed, not patched | FR-1, EG-1 to EG-4, BY-1 | M1, M2 (checkpoint 1), `FAMILY=("httpx",)` (step 2: 27 red), R8 | yes |
| F2 | Importable but unpatchable | EG-20, EG-A3, FR-4, DR-3, DOC-5 | "no raise" (8), "`_INSTALLED` true when incomplete" (6), R7 | partly: R5 survived (W4) |
| F3 | New family module | EG-23 | planted `httpx3/`, `httpcore3/` (step 4) | yes |
| F4 | New transport class | EG-24 | planted stray transport and exported name (step 4) | yes |
| F5 | Double wrap | EG-18, FR-3 | "marker check removed" (steps 2 and 3) | yes |
| F6 | Reset does not restore | EG-16, EG-17, EG-A5; E13 | "reset skips the sync restore" (7), "always setattr" (1); my reverse-order run | yes |
| F7 | Retried install overwrites the saved adapter | EG-20, EG-19 | "adapter saved unconditionally" (4) | yes |
| F8 | Attribution by `startswith` | EG-21, EG-22, EG-1's caller | "`_SKIP_MODULES` without the family" (9), "`startswith` back" (4) | yes (hf/openai frames: N3) |
| F9 | A vacuous pass | EG-0, EG-0b, `test_run_fresh_rejects_a_child_that_imported_another_checkouts_guard`, CI-1 to CI-4, leg 5 | "sentinel can no longer fail" (checkpoint 2), "`run_fresh` stops checking" (step 3), E10(f) | yes |
| F10 | Only built-before, async or module-level regresses | FR-1 (4 variants), EG-3, EG-4 | "async wrapper skips the check" (8) | yes |
| F11 | Install before import | FR-2 | "lazy guard" (3) | yes |
| F12 | huggingface_hub not stopped, or retried | FR-5 | M1; `FAMILY` mutant in a fresh interpreter (5) | yes (a retry needs an hf change; `refused == 1` would catch it) |
| F13 | SDK default client moves off the guarded transport | SDK-1 (leg 1's SDK step only) | `FAMILY` mutant with SDKs declared (2) | yes, in CI leg 1 only |
| F14 | In-process transports blocked by mistake | EG-13 | R1 | yes |
| F15 | Redirect second hop leaves | EG-10 | M1 and `FAMILY` mutant (the hop check is inherent to the class patch) | yes for the TD50 shape |
| F16 | Loopback proxy carries a public request | EG-11 (3 shapes) | `FAMILY` mutant, R8 | yes |
| F17 | Public proxy, local destination (gap) | BY-4 | planted proxy check in `GuardedHTTPAdapter.send` (step 4) | yes (tripwire) |
| F18 | urllib3 / httpcore direct (gap) | BY-2, BY-3 | planted pool-level guard (step 4) | yes (tripwire) |
| F19 | Docs drift from code | DOC-1 to DOC-4 and the step-6 additions | 14 doc mutants (step 6), 4 (checkpoint 2) | yes for names; not for semantic over-claims (W1 to W3) |
| F20 | Portal swallows `EgressGuardIncomplete` | DOC-5 (endpoint), DR-3 (doctor) | doctor mutants (step 5) | **no** for the portal's signal: R6 survived (B1) |
| F21 | Threads | EG-14, EG-19 | `FAMILY` mutant and R8 (EG-14); "no lock + unconditional save" (EG-19, 5 of 5) | partly: the RLock alone is not tied (N4) |
| F22 | Python 3.10 | CI leg 2 | emulated green on 3.10.22 / httpx2 2.14.0, checker run under 3.10 | yes, at CI level |
| F23 | Ecosystem drift | EG-0b + daily schedule | planted wrong declaration (step 7) | yes |
| F24 | Negative control passes for the wrong reason | CI-4, leg 5 | 6 checker mutants (step 7) | yes |
| F25 | Seeding fails on httpx2-only | ON-1, ON-2, ON-3 | "seed back to `import httpx`" (3) | yes |
| F26 | Doctor says "httpx: not installed" | DR-1, DR-2 | "doctor lists httpx only" (1) | yes |
| F27 | Secret in URL leaks | EG-1 (security assertions) | R2, R3 | yes |
| F28 | Wider-suite regression | none (QA's E8 full-suite before/after, baseline 2 pending) | the builder's 33 neighbour files: same failing ids as main | **no** unit test, by design |
| F29 | Broken library false alarm | FR-4, EG-A3 | "import error reported as absent" (steps 2 and 3, `RuntimeError`) | partly: `ImportError` untested (W4) |

## Performance assessment

Not a hot-path change beyond what lock installs already ran for httpx: one wrapper call, `urlparse`, `is_local_host`. E11 re-derived above (8.2 to 16.2 ms). Refusal latency measured at 0.00 to 0.02 s here; it depends on the resolver because `is_local_host` resolves non-IP names before refusing (N6).

## Doc claims checked: my verdict on the builder's 33 rows

| # | Builder | Mine | Basis |
|---|---|---|---|
| 1 | true | true for the httpx family; for `requests`/`urllib.request` only within PRIVACY's scope | probes; W1 |
| 2 | true | true within processes that install the guard; README must scope it | FR-5, my hf probe; W2 |
| 3 | true | true (default clients) | SDK step re-run, 3 cases; lock-version one-off not re-run |
| 4 | true | true, but in the incomplete state the modal contradicts itself | nav.js:83-140; B1 |
| 5 | true | true | DOC-2 green in every leg |
| 6 | true | the pointer is true; the list it points to is incomplete | W1, W2, W3 |
| 7 | true | **imprecise** (pre-existing): the code passes Python's `is_private` set | N1, run |
| 8 | true | true, and its scope is exactly where the unlisted gap is | probe; W1 |
| 9 | true | true; custom openers unlisted | probe; W1 |
| 10 | true | true | metadata re-read: hf 2.4.0, anthropic 2.3.0, openai 3.42.1 require `httpx2` only |
| 11 | true | true | FR-1 built-before variants; probe |
| 12 | true | true | FR-5; probe 9 of 9 |
| 13 | true, scoped | true, scoped | ON-2 |
| 14 | true | true at install time; stale after an in-process install | W3, run |
| 15 | true | true | EG-A2; the incomplete message in my probe |
| 16 | design wrong, rewritten | rewrite accepted and true; incomplete without B1's fix | B1 |
| 17 | reworded | true; not pinned by behaviour | app.py:905-906; R6 |
| 18 | true | true | DR-3; doctor `main` returns 3 |
| 19 | true | true, inside a collapsed `<details>` | DOC-5; my probe |
| 20 | true | true | no aiohttp import in `src`, `scripts`, `core` (grep); `DefaultAioHttpClient` existence taken from the orchestrator's check |
| 21 | true | true | BY-2, BY-3; probe |
| 22 | true, scoped | true, scoped to `src/arail/`; lancedb's urllib3 client runs in lab processes | probe |
| 23 | true | true | BY-4 |
| 24 | unchanged | accepted as pre-existing | not re-audited |
| 25 | moved byte-identical | true | test pins it |
| 26 | true, reworded | true | DOC-9; grep |
| 27 | true | true | setup.sh:1939, upgrade.sh:177 read |
| 28 | true | true | app.py:2579-2583, opencode.py:363-364 read |
| 29 | dropped; design reason incomplete | agreed; the backend imports in ci and lock and the portal mounts it in-process | run; AF-3 |
| 30 | true as reworded | true | the grep runs (168 lines); opencode.py:268 and app.py:2512 are network calls it does not match |
| 31 | true | true | rows 3 and 10 |
| 32 | true | true | read |
| 33 | true | true in the portal; false in the goal parser's model subprocess | W2 |

## Workflow notes

Read as the runner executes it:

- `on:` `pull_request` (default types, no path filter), push to `main`, daily `41 6 * * *`, `workflow_dispatch`. Scheduled runs come from the default branch only, so the first one is the day after merge.
- Concurrency group `egress-guard-${{ github.event_name }}-${{ github.event.pull_request.number || github.ref }}`: a pull request keys on its number; a scheduled run keys on `egress-guard-schedule-refs/heads/main`; push, dispatch and schedule never cancel each other. `cancel-in-progress` cancels superseded pushes (visible as "cancelled", not red).
- The matrix has only `include` entries, so each is one job. All five keys are in every leg and every value is a string (CI-6 pins this; `"3.10"` stays 3.10).
- `if:` expressions compare exact strings (`matrix.install != 'lock'`, `matrix.sdks != ''`); emulate.py evaluates them the same way.
- `EGRESS_TESTS: >-` folds to one space-separated line; pytest and the checker both expand it (CI-5).
- The heredoc in "What this leg installed" uses a quoted delimiter and dedents correctly under the YAML literal block.
- `shell: bash` runs `bash --noprofile --norc -eo pipefail`; the install and test steps add `set -euo pipefail`; leg 5 uses `set +e`, captures `rc`, then `set -e`. No `|| true` and no `continue-on-error` (CI-6 reads keys, not comments).
- `uv sync --frozen --extra dev`: `uv.lock` holds httpx 0.28.1 and no httpx2 package; `pyproject.toml` has a `dev` extra (pytest, cryptography) and no `[dependency-groups]`, so uv's default group adds nothing.
- `pip uninstall -y httpx httpcore httpx2 httpcore2` exits 0 for packages that are absent (run), so leg 5 cannot fail on that line by accident.
- No private dependency: `nucleus-tests.yml` installs `.[dev]` without SSH and its latest runs are green (`gh run list`).
- Timeout: in DB-Ensure's latest run on GitHub (run 41370051614, 2026-11-02) `pip install -e ".[dev]"` took 27 s, so 15 minutes is about 30 times the install. The lock leg's `uv sync` time on GitHub is unknown until the first run.
- Can a step pass while its claim did not run? Only "What this leg installed", which claims nothing (EG-0b does the checking). The SDK step proves whatever SDK versions pip picked, which W5 makes visible.

## What could not be verified here, and what the first real CI run would show

| Not verifiable here | What the first run shows |
|---|---|
| The install steps on `ubuntu-latest` with PyPI as of the day (legs 1, 2, 4, 5) | Each leg's "What this leg installed" block; a mismatch fails EG-0b with "declared X but the environment has Y" |
| pip's resolution on Python 3.10 (uv said httpx2 2.14.0) | Leg 2's block shows `httpx2 2.14.x`; if pip picks a version on `httpx`, EG-0b fails and the decision is recorded, not hidden |
| `uv sync --frozen` on Linux and its duration | Leg 3's block shows httpx 0.28.1, no httpx2; the step time |
| `setup-python`'s pip cache across five legs | At most a "Unable to reserve cache" warning on the legs that share Python 3.11; never red |
| DNS for `.invalid` on the runner (FR-1, FR-5 children resolve for real) | FR-1 and FR-5 green; a resolver that answered `.invalid` would show `NO EXCEPTION` or `ConnectError` with the test's explanation |
| Ten of the 13 files have never run on Linux (no workflow ran them before) | The checker's 13 per-file lines with the same counts as here (17, 63, 6, 20, 43, 18, 22, 20, 28, 46, 17, 14, 6 in legs 1 to 3) |
| Leg 5 on a real `neither` venv | "1 failed, 1 passed" and the checker's ok line, with the sentinel's `UNPROVEN` message in the log |
| The schedule and the 60-day inactivity rule | Only after merge: the first scheduled run at 06:41 UTC the next day |

## Rulings

- **The design point (portal and loader continue after `EgressGuardIncomplete`).** Not acceptable as an unstated default: it is a fail-open of the airgap invariant in the one state this PR detects, and I measured traffic leave while the status said "blocked". Acceptable as a stated, visible decision for this PR, because no supported version reaches the state, an import-failed member is unusable, and `main` is worse. B1 is the minimum; TD56 (fail closed) is the stronger design and my recommendation; the operator decides.
- **AF-1.** Accepted. `ipaddress` calls `203.0.113.7`, `198.51.100.7` and `192.0.2.7` private on 3.10.22 and 3.11.17 (run); `151.101.64.81` is not. Only `gethostbyname` is patched, so a regression is still a `.invalid` `ConnectError`, never a connection to that address. ARCHITECTURE.md corrected.
- **AF-2.** Items 1 to 4: the rewrites are true and accepted (verified at `app.py:901-906`, `loader.py:313-317`, `activity_log.emit`, `load_all` with one caller at `app.py:1233`, and the grep's two unmatched network calls). Item 5: accepted; DOC-1 cannot go red for `FAMILY = ("httpx",)` because `GUARDED_CLIENTS` derives from `FAMILY`, and the mutant that does turn it red is "a member added without docs". Item 6: add "other processes" to the README parenthetical (W2). Item 7: accepted; with explicit node ids a module that fails to import exits 4 (run: explicit node id rc 4, file path rc 2). All corrected in ARCHITECTURE.md.
- **AF-3.** The design's Knowledge Canvas rationale was wrong (corrected in ARCHITECTURE.md). The three `import httpx` sites join **TD52's inventory** as an "outside `src/arail` and `tests`" sub-list; they are not fixed here. Why: none is an egress hole (a failed import sends nothing, and where httpx is installed it is guarded in a process that installed the guard); the canvas is a separate app whose own `requirements.txt` declares `httpx>=0.27`, so `import httpx` is correct in its own environment; `core/knowledge-canvas/client.py` is not even importable under its documented module path (`core.knowledge_canvas`, the directory is hyphenated); `scripts/deep_honest_win.py:717` is operator tooling; widening a security PR into a second app is scope creep. TD52's row must say that `llm_router._ollama_fallback` is a runtime fault in the portal process: the portal mounts the canvas backend in-process (`app.py:760-766`) and on an httpx2-only venv that fallback raises `ModuleNotFoundError: No module named 'httpx'` (run), with the trigger "the first canvas NLQ call on a fresh install".
- **`tests/test_qa_airgap_happy_setup.py` edit.** Accepted. httpx and httpx2 are no longer gaps, so asserting them as gaps would pin a false statement; urllib3 and httpcore are added; the test names are unchanged; the strength moved to DOC-1 and DOC-3 in `tests/test_egress_docs.py`.

## Tech debt delta (against ARCHITECTURE.md)

ARCHITECTURE predicted TD52 to TD55. The review adds four rows, filed in ARCHITECTURE.md's errata (numbers provisional, confirmed at commit 8 like the others): **TD56** fail closed when the guard is incomplete (B1), **TD57** libraries installed after the guard ran (W3), **TD58** lab-spawned Python subprocesses run without the guard (W2), **TD59** `is_local_host` mutates the global socket timeout (N6). TD52 grows by AF-3's three sites. Net is still negative: TD50, the runtime half of TD51, the two silent skips and the missing CI are repaid.

## Required actions before QA

1. **B1:** qualify the airgapped definition when `uncovered()` is non-empty; `nav.js` paints the pill `warn` and shows the unguarded entries outside the collapsed `<details>` when `guard_installed` is false; PRIVACY says the portal and agents keep running with that library unguarded; move the portal's guard-install block into a function with a behavioural test (and extend DOC-5), so mutant R6 goes red; record the decision in `SPRINT.md` and the PR description and file TD56.
2. **W1:** scope README's sentence; add the PRIVACY gap bullet for pre-install `requests` sessions/adapters and `build_opener()` openers; add tripwire BY-5.
3. **W2:** PRIVACY's "Other processes" names the lab's Python subprocesses and other `./arailctl` commands; README's parenthetical adds other processes; scope the router comment; file TD58.
4. **W3:** correct `egress.py:488-489` and `:574` and PRIVACY's doctor sentence; the plugin installer says restart; file TD57.
5. **W4:** parametrize EG-A3 and FR-4 over `RuntimeError` and `ModuleNotFoundError`, so mutant R5 goes red.
6. **W5:** `-rP` on both pytest lines; print the SDK versions after the SDK install.
7. Re-run, and record in BUILD_LOG: the 13 files in ci, lock, both and py310, leg 5's negative control, `test_egress_sdk_clients.py` in an SDK venv, the frozen-surface text, and mutants R5 and R6 (both must now be red).
8. Commit 8 (BACKLOG) adds TD56 to TD59 and AF-3's three sites to TD52, alongside TD52 to TD55.

NITs N1, N2, N3 and N5 are at the builder's discretion in the same pass; N6 is filed as TD59; N4 needs no action; N7 is outside the diff.

## Re-review (after pass 3, QA and pass 4)

**Date:** 2026-11-06
**Build:** HEAD `0472dc49` (code head `8fe63fbc`), compared against my first review's `97fe5ede` and against `origin/main` `de1ce285`
**Reviewer:** architect (review mode), Claude Opus 5.5. *Ran* = executed in this session, on a `git archive HEAD` export (and an `origin/main` export) in the session scratch directory; probes are in `<scratch>/rev2/` (`decision_ast.py`, `states.py`, `doctor_lines.py`, `mutants2.py`, results `mutants2-ci.log` / `mutants2-*.json`). *Read* = read in the code or docs at HEAD; the BUILD_LOG tables were not taken as evidence. The orchestrator runs the five CI legs, QA's lazy-attribute probe, the frozen-surface failure text, the nav badge under node, and its own mutants; I did not repeat those.

### Verdict: WEAK_PASS

B1 is fixed where it mattered. In every incomplete state I could reach, the surfaces an operator checks for the airgap state now agree with the guard: the status endpoint, the definition sentence, the known-gaps entry, the activity warning, `/api/system/mode`, the doctor line and the worker's exit. All of them name the library that is open, and the start-up signal is now pinned by behaviour (R6 is red, where before it survived). W1 to W5, QA-W1, QA-W2 and the NITs that were taken are fixed in code or docs, and each fix I mutated has a test that goes red. The three egress decision sites, the `allow_egress` ratchet, the bootstrap allowlist and the locality checks are AST-identical to `origin/main`. No frozen path is touched.

It is not a PASS, for three reasons:
- Buddy's mode-toggle line still says "Sealed back up. Agents can't reach the public internet." in a state where the guard knows a library is open (R-W1).
- The doctor prints the incomplete message twice, under a line that says "no cloud egress" (R-N1).
- The ledger work my first review required before the PR has not been done: commit 8, which files TD52 to TD61 in BACKLOG, and the B1 decision row in SPRINT.md.

None of the three is a fail-open in the guard itself, so none blocks.

### What I re-derived (ran)

| Check | Result | Means | Bar and why | Out of range | Where a failure lives |
|---|---|---|---|---|---|
| Decision sites, AST with docstrings stripped, HEAD vs `origin/main` (`decision_ast.py`) | 12 of 12 identical: `_check_egress_or_raise`, `GuardedHTTPAdapter.send`, `_check_httpx_url`, `allow_egress`, `_bootstrap_allows`, `allow_bootstrap_fetch`, both urllib handlers, `lab_mode`, `is_airgapped`, `is_local_ip`, `is_local_host` | The airgapped default and the allow ratchet decide exactly as on main | Identical, because the brief forbids relaxing the default, and QA-W3's fix is deliberately not in this PR | Any `DIFFERENT` line = this PR changed policy, not coverage | `src/arail/egress.py`, `src/arail/airgap.py` |
| Scope greps on `git diff origin/main...HEAD` | 0 frozen paths (`deep_*.py`, `dac_world/`, `buddy_host/contract/`, `tests/conftest.py`, `pyproject.toml`, `uv.lock`, `CHANGELOG.md`, `NOTICE`, licences, `THIRD-PARTY`); 0 removed lines naming `aerollm`/`AERO_`; 0 added lines with the word "free"; `app.py` hunks only at the start-up function, `/api/system/mode` and `/api/airgap/status` (the toggle is untouched) | The brief's constraints hold | Zero of each | A hit is a constraint break | the diff |
| Every operator surface per guard state (`states.py`; ci, both, lock; the portal's real `_install_egress_guard()`, `get_mode()`, `get_airgap_status()`, Buddy's `_watch_airgap_events()`, then real sync and async requests to a `.invalid` host) | **complete**: everything green, every installed library `EgressBlocked` sync and async. **unpatchable** (`AsyncHTTPTransport` deleted): 1 activity warning naming the library; mode `guard_installed: False`; status `False`, definition "…blocked, EXCEPT requests made through httpx2: … NOT blocked", 1 extra gap; async `ConnectError` (the documented hole); in both, httpx stays `EgressBlocked`. **lazy_attr** (QA-W2 shape): the same consistent set, `patch raised ImportError` named, and httpx stays guarded in both and lock. **late_install** (lock + httpx2 after start): every surface green, httpx2 `ConnectError` sync and async. **Buddy, every state: "Sealed back up. Agents can't reach the public internet."** | The B1 contradiction is gone from the endpoint, modal data, badge data and activity log; one surface (Buddy) was never wired | Every surface the operator reads agrees with `coverage()`, because B1's point was that the UI must not say "blocked" while the guard knows otherwise | A surface saying "blocked"/"sealed" while `uncovered()` is non-empty = B1 again | R-W1 (Buddy); late_install is W3/TD57 (the guard does not know, and PRIVACY and the plugin installer say so) |
| Doctor in the unpatchable state (`doctor_lines.py`, ci) | stdout: `lab mode : airgapped (airgapped — no cloud egress)`, then `egress guard : INCOMPLETE — egress guard incomplete — httpx2: …`; stderr: `arail.egress: egress guard incomplete — …` (logging's last-resort handler) | QA-N4's doctor half is fixed (INCOMPLETE, not `?`); the worker's duplicate-line fix was not applied to the doctor | One line per fact, and no sentence that contradicts the next | Two copies of the message, and "no cloud egress" one line above "NOT checked" | R-N1 |
| Mutants, ci venv, the 13 `EGRESS_TESTS` files one process per file, `CI=true`, declared `httpx2` (control: 402 cases, 0 skipped, 0 failing) | 16 of 17 caught (table below); 1 survives in ci and in both | Whether each fix has a test that fails without it | At least 1 new failing id per mutant | A survivor = a fix nothing pins | named per mutant |
| The same, both venv (control 455, 0 skipped, 0 failing) | `DEF_names_only_first` caught (1); `GAPS_from_coverage_all` **survives**; `QW2_revert` 7; `R6` 5 | The two-library cases need the both leg | Same | Same | R-N2 |

| Mutant (re-review) | ci | both | Caught by |
|---|---|---|---|
| R5 an `ImportError` counts as absent (W4) | **2** (was 0) | | EG-A3 `[ModuleNotFoundError]`, FR-4 `[ModuleNotFoundError]` |
| R6 portal start-up swallows silently (B1) | **3** (was 0) | 5 | `test_the_portal_says_so_once…`, `…the_failure_privacy_quotes…`, `…attribute_lookup_raises_is_reported…` |
| QW2_revert: the containment around `_patch_family_member` removed | 7 | 7 | EG-A8, the stub-member cases, the remedy test, DOC-5 lazy |
| LOOP_break after an unguarded member | 3 | | stub-member cases, remedy test |
| MODE_always_true (`/api/system/mode`) | 3 | | the mode-endpoint tests |
| STATUS_gi_true (`/api/airgap/status`) | 4 | | DOC-5, mode/status agreement |
| DEF_unqualified | 3 | | DOC-5 description tests |
| DEF_names_only_first | 0 | 1 | `test_the_description_names_every_unguarded_library[broken0]` (both leg only, by construction) |
| GAPS_from_coverage_all (gap list keeps only the first unguarded library) | 0 | **0** | none (R-N2) |
| WK_no_nullhandler / WK_propagate_false / WK_traceback / WK_exit0 | 1 / 1 / 4 / 4 | | the worker tests, including the real-process one |
| DOCTOR_question / DOCTOR_records_ok | 1 / 2 | | DR tests |
| PLUGIN_no_restart / REMEDY_uninstall_back | 1 / 1 | | installer test, EG-A2 |

### My findings, decided from the code at HEAD

| Finding | Fixed? | Evidence (ran / read) | Residual |
|---|---|---|---|
| B1 incomplete guard, UI contradicts, decision unstated | **Yes**, for the surfaces named | Ran: `states.py` (3 venvs × 2 incomplete states), R6 red 3/5, MODE/STATUS/DEF mutants red. Read: `app.py:896-921` (`_install_egress_guard`), `:11926-11935`, `:11985-12019`; `nav.js:68-100,108-120,164-170`, `pillClass` at every pill paint; `_airgap_modal.html:132-148`; PRIVACY's "The lab does not stop" paragraph | The decision is written in PRIVACY and BUILD_LOG, **not yet in SPRINT.md's Decisions log**, and TD56 is not yet in BACKLOG (commit 8 not made). Buddy's line (R-W1) and the doctor's mode suffix (R-N1) were not wired |
| W1 requests/urllib over-claim | Yes | Read: README `:226-232`, `:306-315`; PRIVACY `:44-45`, new gap bullets; BY-5 at `tests/test_qa_airgap_bypass_attempts.py:398`. The builder's mutant rows W1-m4…m9 are claims; I did not re-run them | none |
| W2 other processes | Yes | Read: PRIVACY "Other processes" bullet; README parenthetical; `router/core.py:14-19` | TD58 to file. Wording: "every other `./arailctl` command (apart from `start`)" sits next to a list that includes `doctor` and Model Forge's worker, so "other" has to mean "other than those". Acceptable |
| W3 late install | Yes (docs + installer line) | Ran: `states.py late_install` shows exactly the documented behaviour; PLUGIN mutant red. Read: docstrings `egress.py:489-501`, `:581-588`, `:651-652`; PRIVACY | TD57 to file; the state is still all-green while open, by design |
| W4 `ImportError` untested | Yes | Ran: R5 red (2) | none |
| W5 green run shows no evidence | Yes | Read: workflow `-rsP` on both pytest lines, SDK versions printed, the note on what `-rP` shows | none |
| N1 `is_private` set | Yes | Read: PRIVACY bullet; the range tests in `test_egress_docs.py:175-200` | none |
| N2 worker traceback | Yes | Ran: WK_traceback red 4 | none |
| N3 audit caller names library frames | Not taken (optional) | Read: `_SKIP_MODULES` unchanged | Acceptable as decided |
| N4 RLock untied | No action, as ruled | | none |
| N5 workflow pip retries | Not taken (optional) | Read | Acceptable |
| N6 global socket timeout | File as TD59 | | commit 8 |
| N7 Knowledge Canvas comment | Outside the diff | | none |

### QA's findings, decided from the code at HEAD

| Finding | Fixed? | Evidence (ran / read) | Residual |
|---|---|---|---|
| QA-W2 attribute lookup raises | **Yes** | Ran: `states.py lazy_attr` in ci, both and lock. httpx is still `EgressBlocked` sync and async; the definition, gap entry and activity warning all name httpx2. QW2_revert red 7 (ci and both); LOOP_break red 3. Read: `egress.py:516-522`, and `except Exception` also covers a metaclass `__getattr__` and a `vars()` failure inside `_patch_family_member` | none |
| QA-W1 urllib past the opener | Yes (docs) | Read: `airgap.py` docstring and the `KNOWN_GAPS` entry; the PRIVACY bullet; the modal names `http.client` and scopes urllib; tripwire `test_no_lab_code_goes_around_the_opener_the_guard_installs` with its can-fire twin | Closing the gap is TD53 work |
| QA-N1 method reference | Yes | Read: PRIVACY bullet | none |
| QA-N2 re-imported classes | Yes | Read: PRIVACY "or imported again after the guard ran" | none |
| QA-N3 spawn/forkserver | Yes | Read: PRIVACY "Other processes" | none |
| QA-N4 worker twice, doctor `?` | Worker yes; doctor half | Ran: WK_no_nullhandler, WK_propagate_false, DOCTOR mutants all red; `doctor_lines.py` | **The doctor still prints the message twice** (R-N1) |
| QA-N5 remedy | Yes | Ran: REMEDY mutant red. Read: `egress.py:602-623`. `.venv/bin/pip` exists on a setup install (`scripts/setup.sh:485` uses `python -m venv`) | R-N3: the range given is a ceiling, not "a version the lab was tested with" |
| QA-N6 older paragraphs | Yes | Read: README `:225-232`, `the-lab.md:299-308` | none |
| QA-N7 nav badge | Yes | Ran: MODE mutant red 3. Read: `nav.js:68-100`. In CI the badge is pinned only by source regex; behavioural evidence is the orchestrator's node run | R-N4 |
| QA-N8 test comments | Yes | Read: `test_egress_httpx.py:590-594` | none |

### New findings (in what the fix passes added)

#### R-W1 (WEAK). Buddy tells the operator the lab is "Sealed back up" while the guard knows a library is open

- **Where:** `src/arail/agents/_builtin_buddy.py:541` (`_watch_airgap_events`, on a flip from hybrid to airgapped). The text is not in the diff, but this PR is what made the state detectable, and B1's fix wired every other surface to it.
- **Evidence (ran):** `states.py`, unpatchable and lazy_attr states in ci, both and lock. The modal, badge and status all say httpx2 (or httpx) is NOT blocked, and the async request reaches the network layer, yet Buddy's observation is "Sealed back up. Agents can't reach the public internet."
- **Why it matters here:** arail's paranoid checklist names Buddy over-confidence. Buddy is the surface this product puts front and center, and this is the one sentence the operator gets at the moment they turn the airgap back on.
- **Why not BLOCK:**
  - The state is unreachable on every supported library version, and the daily legs go red the day it becomes reachable.
  - The modal, badge, status and activity log, which an operator would check, all say the opposite.
  - Buddy's line already over-claims on `main` for every documented gap.
- **Fix (about 5 lines and one test):** in `_watch_airgap_events`, when `arail.egress.uncovered()` is non-empty, the fact names the library and says its requests are not blocked. For example: "Sealed back up, except httpx2: the egress guard could not patch it, so its requests still go out. The Network Policy modal has the details." Test: in `tests/test_egress_docs.py`, delete `AsyncHTTPTransport` from an installed member, flip the Buddy state from hybrid to airgapped, and assert the fact names the library and does not say "Sealed back up." on its own. If not fixed in this PR, file it under TD56 (the same decision), together with R-N1's mode suffix and `admin.html:923` ("Airgapped: zero network calls, fully local.", which is pre-existing and static).

#### R-N1 (NIT). The doctor prints the incomplete message twice, under a line that says "no cloud egress"

- **Where:** `src/arail/doctor.py:91` (the `lab mode` line's suffix) and `:100-106`. The guard's `_log.error` reaches stderr through logging's last-resort handler, because the doctor configures no logging. This is the exact mechanism QA-N4 fixed in the worker.
- **Evidence (ran):** `doctor_lines.py`, ci. One stderr line `arail.egress: egress guard incomplete — …`, plus the stdout `INCOMPLETE — …` line, plus `(airgapped — no cloud egress)` two lines above it.
- **Fix:** reuse the worker's `_guard_log_kept_off_stderr` around the doctor's `install_guard()` (or move it to `egress`). When `egress.uncovered()` is non-empty after the install, the mode suffix says "(airgapped — except the libraries below)". Test: a fresh-interpreter doctor run, in the style of `test_a_real_worker_process_prints_the_incomplete_guard_message_once`.

#### R-N2 (NIT). With two unguarded libraries, a gap list that drops one survives every test

- **Where:** `tests/test_egress_docs.py:334-345` (`test_the_description_names_every_unguarded_library`) checks the definition but not `known_gaps`.
- **Evidence (ran):** mutant `GAPS_from_coverage_all` (keep only the first unguarded entry) gives 0 new failing ids in ci and 0 in both.
- **Fix:** in that test, assert `len(status["known_gaps"]) - len(airgap.KNOWN_GAPS) == len(broken)`. The mutant then goes red in the both leg.

#### R-N3 (NIT). The remedy calls a ceiling "a version the lab was tested with"

- **Where:** `src/arail/egress.py:604` (`_TESTED_RANGES`), `:620`; `docs/PRIVACY.md:89`.
- **Evidence (read):** PRIVACY's own example of the trigger is "a future release renamed its transport classes". Such a release inside 2.x satisfies `httpx2<3`, so `pip install 'httpx2<3'` would report "Requirement already satisfied" and change nothing.
- **Fix:** say "the range the lab supports (`httpx2<3`); if a release inside that range is the one that broke, pin the previous release, for example `pip install 'httpx2==<previous>'`", or record the last versions the daily legs saw. The state is unreachable today, so this is wording only.

#### R-N4 (NIT). In CI, nav.js's incomplete-guard behaviour is pinned by source text only

- **Where:** `tests/test_egress_docs.py:824-960` (regexes over `nav.js`). `tests/test_airgap_modal_dom.py` renders the template and runs no JS.
- **Evidence (read):** the regexes are exact and the builder's N7 mutants turn them red. But a later statement that repaints the pill or badge would pass them. The only behavioural run is the orchestrator's node harness, outside CI.
- **Fix:** file it: move the node DOM-stub harness into a test that is skipped only when `node` is absent, and is declared in the CI legs so that the skip is red.

#### R-N5 (NIT, defence in depth). The definition is qualified from `uncovered()`, not from `guard_installed`

- **Where:** `src/arail/portal/app.py:11985-11999`.
- **Evidence (read):** if `install_guard()` raised anything other than `EgressGuardIncomplete` before `_COVERAGE` is set, `guard_installed` would be False (amber pill and badge) while the definition still said "Cloud-provider APIs are blocked." I found no reachable path: the requests and urllib steps cannot raise in practice, and the family step is now fully contained.
- **Fix (optional):** also qualify the definition when `not _INSTALLED` ("the guard did not finish installing; see the activity log").

No new way for the guard to fail open silently was found in the added code:
- The new `except Exception` reports the member as unguarded and moves on to the next member.
- `_INSTALLED` stays False on any incomplete install.
- The worker still fails closed in both modes.
- The mode endpoint reads the live flag.
- No test relies on an `importorskip`. The only empty-parameter case (`[INSTALLED] if INSTALLED else []`) is in a file that leg 5 does not run. Legs 1 to 4 ran 0 skipped in my controls (ci 402, both 455).

### Item 2: QA-W3 and PX-1 left out of this PR

- **QA-W3 (an `allow_egress` scope that survives a flip to airgapped; TD60): defensible to leave out.**
  - It is pre-existing on `main` for requests, urllib and httpx.
  - For httpx2, this PR moves from "never checked" to "checked, except inside a scope that outlives a flip", which is strictly better.
  - The fix changes the three decision sites. I verified that this PR keeps those sites AST-identical to main, which is a property worth keeping in a coverage PR.
  - QA validated the fix only on the 13 files, not on the wider suite.
  - It is still a fail-open of the airgapped invariant. TD60 should be the next egress PR, with QA's test, a shared decision helper (QA's note on the three copies), and the PRIVACY sentence "the change takes effect immediately" qualified in the same commit.
- **PX-1 (innerHTML rows in the modal's Recent activity; TD61): defensible, less comfortably.**
  - For leaving it out: it is pre-existing. QA measured that requests, httpx and httpx2 percent-encode the host, so this PR adds no route to the sink. The only route is urllib with a host string containing markup, in a guarded process, and nothing in the tree does that.
  - Against: this PR edits the same `.then(function (data)` block, `nav.js:144-158`. The fix is about six lines in the DOC-6 pattern already used beside it. The sink's reasoned impact is a same-origin `POST /api/airgap/toggle`, which turns the airgap off.
  - If the builder opens another pass for R-W1, I would take PX-1 in it. Otherwise file TD61 at high priority, with QA's node-harness check as its test.

### Item 4: the operator-decision point

The decision is "with an incomplete guard the portal and the agent loader keep serving with that library unguarded; only Model Forge's worker exits". It is now stated in `docs/PRIVACY.md` and pinned per caller: portal R6, the loader test, the worker tests, and the doctor DR tests.

**Is there still a reachable state where an operator-facing surface contradicts what the guard knows? Yes, one live one, plus two static lines:**
- Buddy's toggle observation ("Sealed back up…", R-W1). This is the live one.
- The doctor's `(airgapped — no cloud egress)` suffix, printed directly above its own INCOMPLETE line (R-N1).
- `admin.html:923`'s static help text, "zero network calls".

The modal pill, definition, known-gaps list, nav badge, `/api/system/mode`, `/api/airgap/status`, activity log and worker all agree with `coverage()` in every incomplete state I reached (ran). The late-install state (all green while open) is not a contradiction of what the guard knows: the guard does not know. It is documented, and it is W3/TD57.

**Would I still BLOCK? No.** B1 blocked because the decision was unstated and the surfaces an operator uses to check the airgap said the opposite of the guard. Both are fixed and mutation-tested. What remains are generic sentences, pre-existing on `main`, in a state no supported version reaches, and the daily legs go red the day one does. My recommendation for TD56 (fail closed: refuse to start airgapped, and refuse the toggle to airgapped, while `uncovered()` names a library whose import succeeded) stands. It is the operator's call, and the PR should say so in those words.

### Required actions before push (WEAK_PASS: each fixed in this PR or filed, as stated)

1. **Commit 8 (orchestrator), before push.** In `sprints/BACKLOG.md`, file:
   - TD52 to TD55, with AF-3's three sites in TD52;
   - TD56 to TD59;
   - TD60 (QA-W3, with QA's validated fix and test, and the PRIVACY "takes effect immediately" sentence);
   - TD61 (PX-1, with QA's fix);
   - and, if R-W1 is not fixed, R-W1, R-N1's mode suffix and `admin.html:923`, under TD56 or as TD62.

   Proof: `git diff origin/main...HEAD -- sprints/BACKLOG.md` shows every row (today the PR leaves the file untouched).
2. **SPRINT.md's Decisions log and the PR description (orchestrator).** Add the B1 decision row, verbatim from PRIVACY, and the TD56 recommendation. Proof: `grep -n "keep serving" sprints/2026-11-01-arail-egress-httpx2/SPRINT.md` returns the row (today it returns nothing).
3. **R-W1: fix, or file per action 1.** If fixed: `src/arail/agents/_builtin_buddy.py:541`, with the test described under R-W1 in `tests/test_egress_docs.py`. Prove the test can fail by reverting the Buddy line and seeing it go red. Then re-run the 13 files in ci, lock and both.
4. **NITs R-N1 to R-N5:** at the builder's discretion in the same pass, or file. R-N2 is a one-line assertion and the cheapest of them.

### What I could not verify here

- The five CI legs, QA's lazy-attribute probe, the frozen-surface failure text and the nav badge under node: these are the orchestrator's runs this time, and I did not repeat them.
- Any GitHub-hosted runner behaviour (installs, pip's resolution on the day, the schedule). The first real run would show it, as tabled in my first review.
- A real browser rendering the amber badge and the opened `<details>`, and PX-1's script execution (reasoned, not executed).
- The full suite after pass 4 (the orchestrator's "after" run).
- Buddy's observation rendered in the Buddy panel: I called the watcher function directly and read the `fact` it returns, not the UI that shows it.
- Python 3.12 and 3.13.
