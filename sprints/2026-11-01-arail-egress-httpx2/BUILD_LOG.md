# Build log: the egress guard covers the httpx family (TD50, runtime half of TD51)

**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `ec2a12be` (accepted by the ledger at `982da749`)
**Branch:** `qukaizen/arail-egress-httpx2` (base `origin/main` `5997363e`, which is #210). Not pushed.
**Started:** 2026-11-01
**Pass 1:** commits 1 to 4 of the "Atomic commit plan" (the guard and its proof): `052a6217` (skeleton), `da2057b6`, `96d87fb2`, `e7f868a7`, `a02ba141`.
**This pass (pass 2):** commits 5 to 7 (the consumers, the docs, the CI workflow), starting from head `5ea83b9f` (pass 1 plus the orchestrator's checkpoint 1). Commit 8 is the orchestrator's, after #211 merges. Pass 1's log is kept below, unedited except for back-filled SHAs.
**Status:** pass 2 planned (this commit). Steps 5, 6 and 7 are filled in as each lands; step 7's SHA stays for the orchestrator.

## Hard rules (both passes)

- The airgapped default and the `allow_egress` ratchet are not relaxed. The audit schema (`url_host`, `caller`, `reason`, `lab_mode`, `ts`) is not changed.
- Not touched: `src/arail/deep_*.py`, `src/dac_world/`, `src/arail/buddy_host/contract/`, `tests/conftest.py`, `pyproject.toml`, `uv.lock`, `CHANGELOG.md`, `NOTICE`, `THIRD-PARTY-LICENSES`, `licenses/`, `lab/worlds`, `models/graduated`, `eval/`, `research/aerollm`, `docs/archive`; no `aerollm` / `AERO_*` name. `README.md`, `docs/` and the modal were not edited in pass 1; commit 6 of pass 2 edits them.
- No `importorskip` and no skip for the thing under test in anything written here. No real host is contacted: a URL that must be blocked uses a `.invalid` name.
- The home clone `~/ProJects/qukaizen-arail` is not touched. Nothing is pushed or merged.
- Nothing is created in the worktree except committed files. Venvs, junit XML, logs and scratch trees live in the session scratch directory.

## Plan

| # | Files | Change | Test | Commit ref |
|---|---|---|---|---|
| 0 | `sprints/2026-11-01-arail-egress-httpx2/BUILD_LOG.md` | This skeleton: the contract with the reviewer. | none (docs) | `052a6217` |
| 1 | `src/arail/httpx_family.py` (new); `tests/test_httpx_family.py` (new); `tests/egress_family_support.py` (new, first part: `LITERAL_FAMILY`, `installed_independently`) | The one place that names httpx and httpx2: `FAMILY`, `is_installed`, `installed`, `preferred`. Pure addition. | HF-1 to HF-6, plus three small ones (`is_installed` never imports; a module whose `__spec__` is None; stdlib-only imports). Written first and seen failing (the module does not exist). | `da2057b6` |
| 2 | `src/arail/egress.py`; `tests/egress_family_support.py` (extend: `declared_family`, `PROBE_URL`, `PUBLIC_TEST_IP`); `tests/test_egress_httpx.py` (rewritten); `tests/test_qa_airgap_bypass_attempts.py` (BY-1, docstrings) | The TD50 fix: the family patch keyed by (class, attribute); `coverage()`, `uncovered()`, `GUARDED_CLIENTS`, `EgressGuardIncomplete`; RLock; idempotent saving of the `requests` adapter; exact or dotted-prefix `_SKIP_MODULES`; `*args, **kwargs` pass-through; docstrings. | EG-0 to EG-22 and BY-1. **Run against the unmodified `egress.py` first**; the reds are recorded per venv in Execution. | `96d87fb2` |
| 3 | `tests/test_egress_httpx_fresh.py` (new); `tests/test_egress_sdk_clients.py` (new); `tests/egress_family_support.py` (extend: `run_fresh`) | The brief's probe in a fresh interpreter, huggingface_hub, and the SDKs' default clients. No source change. | FR-1 to FR-5, SDK-1. Shown failing against the pre-fix `egress.py` (a scratch tree), not only passing against the fixed one. | `e7f868a7` |
| 4 | `tests/test_egress_httpx.py` (+EG-23, EG-24); `tests/test_qa_airgap_bypass_attempts.py` (+BY-2, BY-3, BY-4) | Tripwires for an unknown httpx-family module or transport, and pins for the documented lower-layer and proxy gaps. | EG-23, EG-24, BY-2 to BY-4, plus the declaration-parser tests (EG-A7). Each shown to fail on a planted change. | `a02ba141` |
| 5 | `src/arail/open_notebook_seed.py`; `src/arail/doctor.py`; `tests/test_httpx_family.py` | TD51 runtime consumers. The seeding takes its HTTP library from `httpx_family.preferred()` (a fresh install has no `httpx`, so the `import httpx` at line 19 raised right after `docker compose up`). The doctor lists every family member under Components and prints which clients the guard covered. | ON-1, ON-2, DR-1 to DR-3, written first and run against the unmodified source. DR-3 is expected to pass already (the doctor's existing `except` branch records the failure), so it is shown to fail on a planted change instead. | `d626a1cf` |
| 6 | `README.md` (298-307); `docs/PRIVACY.md` (43-44, 57-73, 301); `src/arail/portal/templates/_airgap_modal.html` (137-146); `src/arail/airgap.py` (docstring, new `KNOWN_GAPS`); `src/arail/router/core.py` (comment, 14-17); `src/arail/portal/app.py` (the `/api/airgap/status` handler only); `tests/test_egress_docs.py` (new); `tests/test_qa_airgap_happy_setup.py` (the two gap lists, on purpose) | The docs say what is wrapped and list what is not. `KNOWN_GAPS` moves to `arail.airgap`; the status endpoint reads it and appends `egress.uncovered()`. Every factual sentence is checked against the tree before it is written ("Doc claims checked", step 6). | DOC-1 to DOC-5, written first and seen failing against the current docs. | pending (the next commit fills it in) |
| 7 | `.github/workflows/egress-guard.yml` (new); `tests/egress_ci_check.py` (new); `tests/test_egress_ci_check.py` (new) | The five-leg workflow and the junit checker it calls. | CI-1 to CI-4, written first (the checker does not exist yet), plus a mutant that makes `no-skips` ignore skips: CI-2 must go red. The workflow is validated three ways (see below). | pending (the orchestrator fills it in) |
| 8 | `sprints/BACKLOG.md` + ledger | Close TD50, re-scope TD51, file TD52 to TD55. | none | orchestrator, after #211 merges |

A commit cannot contain its own SHA, so the "Commit ref" of step N is filled in by the next commit's edit of this file. Step 4's is filled in by this commit; 5 and 6 by the commits after them; 7's by the orchestrator.

## Verification protocol (every commit)

One pytest process at a time, targeted files only (the orchestrator runs the full suite). Scratch HOME and TMPDIR, scrubbed environment, `PYTHONDONTWRITEBYTECODE=1`, junit XML and logs in the session scratch directory. Each venv imports `arail` from this worktree and declares its leg with `ARAIL_TEST_EXPECT_HTTPX_FAMILY`.

| Venv | Library state | Declaration | Used from |
|---|---|---|---|
| ci | httpx2 2.16.0, no httpx (Python 3.11.17) | `httpx2` | commit 1 |
| lock | httpx 0.28.1, no httpx2 (3.11.17) | `httpx` | commit 1 |
| both | httpx 0.28.1 and httpx2 2.16.0 (3.11.17) | `httpx,httpx2` | commit 1 |
| neither | ci with httpx2 and httpcore2 removed (3.11.17) | `none` | commit 2: EG-0 must FAIL with `UNPROVEN`, EG-0b must PASS |
| py310 | httpx2 2.14.0, no httpx (CPython 3.10.22), leg 2 of the CI design | `httpx2` | commit 2 |
| sdk | both libraries plus anthropic 2.3.0, openai 3.42.1, h2, socksio | `httpx,httpx2`, `ARAIL_TEST_EXPECT_SDKS=anthropic,openai` | commit 3: SDK-1 and client shapes |

Files run per commit: the files the commit touches, plus `tests/test_egress_*.py`, `tests/test_qa_airgap_bypass_attempts.py` and `tests/test_queuellm_rename_frozen_surface.py`. At commit 2 (the only commit that changes guard code) also the rest of the CI design's list (`test_airgap_helpers`, `test_qa_tier1_egress_guard`, `test_qa_airgap_happy_setup`, `test_qa_airgap_ipv6_edge_cases`, `test_airgap_modal_dom`) and the three tests that install the guard (`test_kb_capture`, `test_stt_flow`, `test_ocr_flow`).

**Environment note (py310).** The `py310` venv handed over had only the runtime dependencies of `huggingface_hub` and `httpx2`: no pytest, no fastapi, no starlette, so the conftest could not run. I installed the architect's own pinned Python 3.10 `[dev]` resolution into it (`uv pip install --no-deps -r <scratch>/resolve/req-3.10.txt`). httpx2 stays at 2.14.0 and huggingface-hub at 2.4.0; pytest 9.2.0 and starlette 1.7.0 were added. It is a scratch venv, so nothing in the worktree changed.

## Pass 2: what is different (the contract for commits 5 to 7)

- **Erratum, already applied in pass 1 (AF-1):** in-process tests answer `socket.gethostbyname` with `PUBLIC_TEST_IP = "151.101.64.81"` and patch nothing else. The TEST-NET-3 address the design text names counts as private to `ipaddress`, so the guard would call it local and never refuse.
- **The frozen-surface test is not a green gate.** `tests/test_queuellm_rename_frozen_surface.py` fails 2 tests on untouched `origin/main`. The criterion for each pass-2 commit is that its failure text does not change: the `E ` lines of `pytest tests/test_queuellm_rename_frozen_surface.py -q` are diffed against pristine main's (`5997363e`, run once in the scratch worktree `base-wt`; the text is kept in the scratch directory). README.md, `docs/PRIVACY.md`, the modal template, `doctor.py`, `open_notebook_seed.py`, `airgap.py` and `router/core.py` must not appear in its "tokens lost" list, and the counts listed for `src/arail/portal/app.py` (`AEROLLM_MODEL: 3`, `AeroLLMBackend: 1`) must not grow. At the start of pass 2 the text at head `5ea83b9f` is identical to pristine main's.
- **Documentation claims must be true.** Before a factual sentence goes into README.md, `docs/PRIVACY.md`, the modal or a docstring, it is checked against the tree, and the evidence (file:line, grep, command output) goes into "Doc claims checked" (step 6). A design sentence that turns out false is not written; the true version is, and the difference is logged there and reported.
- **Wording:** "token-free", never "free", in any cost claim. README and PRIVACY keep the voice they already have. `CHANGELOG.md`, `pyproject.toml`, `uv.lock`, `tests/conftest.py`, `src/arail/deep_*.py`, `src/dac_world/`, `src/arail/buddy_host/contract/` and every `aerollm` / `AERO_*` name stay untouched.
- **`src/arail/egress.py` and `src/arail/httpx_family.py` are not changed** unless a test written here exposes a real defect in them. If that happens, work stops, the defect goes under "Architect feedback required" with the failing test and a diagnosis, and that is committed and reported.
- **Commit 6 touches a 12k-line module** (`portal/app.py`): only the `/api/airgap/status` handler changes. Every test file that exercises the endpoint, the airgap modal or the doctor is found by grep and run (the list is in step 6).
- **Commit 7 is validated three ways, each recorded:** (a) `yaml.safe_load` plus a check that every `${{ matrix.* }}` key is present in every matrix entry; (b) `actionlint`, if it installs into a throwaway venv under the scratch directory; (c) all five legs emulated locally with the exact test commands, the matching venv and declaration, and the exit codes (leg 5 with its exact negative-control command; Python 3.10 runs `tests/egress_ci_check.py` in the `py310` venv). The install steps cannot run here as written; the PR's own CI run is their check.
- **Each pass-2 commit** runs the files it touches plus `tests/test_egress_*.py`, `tests/test_httpx_family.py`, `tests/test_qa_airgap_bypass_attempts.py`, `tests/test_qa_airgap_happy_setup.py`, `tests/test_airgap_modal_dom.py` and `tests/test_airgap_helpers.py` in ci, lock, both and py310 with each venv's declaration, and EG-0 / EG-0b in the neither venv. Each commit is green on its own apart from the one TD48 failure in `tests/test_qa_deep_honest_egress_happy.py` that exists on main in ci and py310 (not touched).

## Execution

### Step 0: skeleton
This file. Commit: `052a6217`.

### Step 1: `arail.httpx_family`
Commit: `da2057b6`.

Done as planned: `src/arail/httpx_family.py` (stdlib only: `FAMILY = ("httpx2", "httpx")`, `is_installed`, `installed`, `preferred`) and `tests/test_httpx_family.py` (HF-1 to HF-6).

**Deltas from the plan**

- `tests/egress_family_support.py` is created here, with only `LITERAL_FAMILY` and `installed_independently()`; the plan creates it at step 2. HF-2 must compare `installed()` with a lookup that does not share the module's code, and the plan has that helper arrive one commit later. Creating it here keeps one independent lookup instead of an inline copy in the test file plus a second copy in the support module. The rest of the file is added by the step that first calls it (`declared_family`, `PROBE_URL`, `PUBLIC_TEST_IP` at step 2; `run_fresh` at step 3), so no commit carries a helper nothing uses. The finished file is the design's.
- Three small tests beyond HF-1 to HF-6 (the plan said two). Each pins a sentence of the interface contract: `is_installed` never imports (a throwaway package whose `__init__` raises); a module in `sys.modules` with `__spec__ = None` counts as installed (with the precondition that `importlib.util.find_spec` raises `ValueError` for it); `httpx_family` imports only the standard library (an AST scan of its imports against `sys.stdlib_module_names`).
- HF-3 is written to pass where nothing is installed (it asserts `preferred()` raises), instead of indexing an empty tuple, so the file passes in all five venvs.

**Red first.** Before the module existed, `tests/test_httpx_family.py` failed at collection (`ImportError: cannot import name 'httpx_family' from 'arail'`, 0 collected, rc 2).

**Can the tests fail?** Five mutations of the module, each in a scratch copy of the tree (never in the worktree), each turned at least one test red: `FAMILY` reversed (4 failed), a `None` entry in `sys.modules` read as installed (2), `preferred()` falling back past a broken import (1), `is_installed` importing (3), the module importing `requests` (1).

**Results** (passed / failed / skipped; each venv declares its leg):

| File | ci | lock | both | neither | py310 |
|---|---|---|---|---|---|
| `test_httpx_family.py` | 9 / 0 / 0 | 9 / 0 / 0 | 9 / 0 / 0 | 9 / 0 / 0 | 9 / 0 / 0 |
| `test_egress_bootstrap.py` | 22 / 0 / 0 | 22 / 0 / 0 | 22 / 0 / 0 | n/a | n/a |
| `test_egress_guard.py` | 18 / 0 / 0 | 18 / 0 / 0 | 18 / 0 / 0 | n/a | n/a |
| `test_egress_httpx.py` (still the old file) | 0 / 0 / **1** | 7 / 0 / 0 | 7 / 0 / 0 | n/a | n/a |
| `test_qa_airgap_bypass_attempts.py` | 15 / 0 / **1** | 16 / 0 / 0 | 16 / 0 / 0 | n/a | n/a |
| `test_queuellm_rename_frozen_surface.py` | 3 / **2** / 0 | 3 / **2** / 0 | 3 / **2** / 0 | n/a | n/a |

- The two skips in ci are the baseline's B5 skips, still there because the guard is not changed until step 2. They are what step 2 removes.
- **`test_queuellm_rename_frozen_surface.py` has 2 failures that are not this build's.** They fail the same way on untouched `origin/main` `5997363e` (the orchestrator's full-suite baseline lists the same two ids in both venvs). Because a red test can hide a new violation, each commit compares the failure text with pristine main, run in the scratch worktree of `5997363e`: the four drift lines (`pyproject.toml | 5 +++++`; `scripts/build-aerollm.sh` loses `aerollm_version` x2; `src/arail/portal/app.py` loses `AEROLLM_MODEL` x3 and `AeroLLMBackend` x1) must be identical, and no file this build modifies may appear. Step 1: identical.
- `neither` and `py310` ran only `test_httpx_family.py` here (the matrix starts at step 2 for the rest).

**Self-check.** Scope: the three source and test files are in the plan (the support module early, as above), plus this file. Atomic: one new module and its tests, nothing else. The message names the module and the reason it exists.

### Step 2: guard every installed httpx-family library (TD50)
Commit: `96d87fb2`.

**Done as planned.** `src/arail/egress.py`: the family patch keyed by (class, attribute) with `(owned, original)` state and a `setattr`/`delattr` reset; `coverage()`, `uncovered()`, `GUARDED_CLIENTS`, `EgressGuardIncomplete`; an RLock; the `requests` adapter saved only when the current class is not already the guarded one; `_SKIP_MODULES` matched by exact name or dotted prefix with `httpx2` and `httpcore2` named (from `httpx_family.FAMILY`); wrappers pass `*args, **kwargs`, carry `_arail_guarded` and `__wrapped__`, and do not use `functools.wraps`; the module, `install_guard`, `_reset_for_tests` and `_check_httpx_url` docstrings (the module docstring's first paragraph is the design's wording). Tests: `tests/egress_family_support.py` (+`declared_family`, `PROBE_URL`, `PUBLIC_TEST_IP`), `tests/test_egress_httpx.py` rewritten (EG-0 to EG-22), BY-1 and its docstrings in `tests/test_qa_airgap_bypass_attempts.py`.

**Names the plan left open.** EG-18 calls "the per-module patch step" without naming it: it is `_patch_family_member(name, module) -> str` (returns the member's coverage value; EG-18 and EG-A5 call it directly). Also `_install_httpx_family_guard() -> dict`, `_reset_httpx_family_guard()`, `_PATCHED`, `_COVERAGE` (rebound, never mutated, so a reader always sees a whole snapshot and `coverage()` needs no lock), `_LOCK`, `_FAMILY_TARGETS`, `_is_guard_frame`. `EgressGuardIncomplete.__init__(message, uncovered=None)` takes the finished message, so the exception still pickles and copies.

**Deltas from the plan**

1. **AF-1** (see "Architect feedback required"): `PUBLIC_TEST_IP = "151.101.64.81"`, not `203.0.113.7`, patched into `socket.gethostbyname` only.
2. **Six small tests beyond EG-0 to EG-22, EG-A1 to EG-A6**, each pinning a sentence of the interface contract that the plan states and does not list: A1 `coverage()` is `{}` before an install and after a reset, ordered `requests`, `urllib.request`, then `FAMILY`, and is a copy; `GUARDED_CLIENTS`. A2 the incomplete-install error is a `RuntimeError` and not an `EgressBlocked`, and its message names the library and the reason, says the requests are NOT checked against LAB_MODE, names what is still guarded and the fix. A3 a library whose lookup raises, or whose import raises, in-process (FR-4 repeats the import case in a fresh interpreter in step 3; this one is here because step 2 introduces the branch and must be green and complete alone). A4 the wrappers pass extra arguments through and carry the marker and `__wrapped__`. A5 reset `delattr`s an attribute the class only inherited (the real transports own theirs, so a stand-in module is the only way to reach that branch). A6 a class that refuses the patch is reported as unguarded and the other target is still patched.
3. **Two guard branches the plan did not spell out, both inside F2 and F29 ("never leaves a member silently unpatched")**: a family member whose *lookup* raises (a broken `sys.meta_path` finder) is recorded `unguarded: lookup raised ...` instead of escaping `install_guard()` before the members after it are patched; and a class whose `setattr` raises is recorded `unguarded: <Class>.<attr> could not be patched (...)`. Tests: A3 and A6.
4. **EG-19 is stronger than the plan**: after the eight concurrent installs it also resets and checks that `requests.adapters.HTTPAdapter` is the stock class again. The plan's assertion (`__wrapped__` is the library function) cannot see the real race: marker-based idempotency already prevents a double wrap, and the thing an unlocked install corrupts is the saved adapter (a second thread saves the first thread's `GuardedHTTPAdapter` as "the original").
5. **The module's `_env` fixture contains pollution** (restores `egress._ORIGINAL_HTTP_ADAPTER` and the two `requests` attributes to the stock class captured at import, after the guard's own reset). It exists because, on the unmodified guard, EG-19's race left the *guarded* class as "the original", and every later test then started from a polluted process and failed on its precondition: one red test became thirty. EG-19 and EG-20 also assert that they start clean.
6. **BY-1** uses `PROBE_URL` (a `.invalid` name) instead of `https://example.com`, as the design's "no real host" rule says, and patches `gethostbyname` as the other in-process tests do. The section banner above `TestDocumentedGaps` ("httpx / aiohttp / ...") is corrected as well as the two docstrings: it was the same stale claim in the same file.
7. EG-13's `TestClient` part runs once per library and rides on whichever library starlette itself prefers (httpx2 in ci, both and py310; httpx in lock). Starlette 1.3.1 in the lock venv prints a deprecation warning when it imports.

**Red first: the new tests against the unmodified `egress.py`** (passed / failed / skipped; the file had 46 tests at the time, A6 came after):

| Venv | `test_egress_httpx.py` | BY-1 file |
|---|---|---|
| ci | 16 / **30** / 0 | 15 / **1** / 0 |
| lock | 29 / **17** / 0 | 16 / 0 / 0 |
| both | 34 / **37** / 0 | 16 / **1** / 0 |
| neither | 10 / **11** / 25 | 15 / 0 / 1 |
| py310 | 16 / **30** / 0 | 15 / **1** / 0 |

What each red means (ci, which is httpx2 only; py310 is identical):

- **The request reached the network layer** (`httpx2.ConnectError`, or `Connection refused` at the loopback proxy): EG-1, EG-2, EG-3, EG-4, EG-10, EG-11 (3 shapes), EG-12, EG-14, and BY-1. This is TD50 reproduced, in the places the old suite skipped.
- **Nothing was refused**: EG-7 (no audit line) and EG-9 (the not-allowlisted host reached the stub).
- **httpx2 is not wrapped** (no `_arail_guarded`, no `__wrapped__`): EG-16, EG-17, EG-19, EG-A4.
- **Attribution by `startswith`**: the four lookalike modules of EG-21 and EG-22 (`httpx2` and `httpcore2` not named).
- **The new API does not exist yet** (`coverage`, `_PATCHED`, `EgressGuardIncomplete`, `_patch_family_member`): EG-15, EG-18, EG-20 (x2), EG-A1, EG-A2, EG-A3 (x2), EG-A5. These are the weak kind of red, which is why the behavior reds above matter.
- **lock is the independent oracle for the test expectations**: the old guard does patch httpx, and *every behavior test passes there on old code*. Its 17 reds are only the new API, the new `__wrapped__` structure and attribution. In `both`, httpx's 7 reds are the same kind; all 20 httpx2-specific reds include every behavior case.
- **neither**: EG-0 fails with its `UNPROVEN` message (as designed, it does not depend on the guard), EG-0b passes with `none`, and 25 tests are skipped for an empty parameter set (inherent to that venv; the CI leg selects only the two sentinel node ids).

**Can the tests fail? Sixteen mutations**, each applied in a scratch copy of the tree, each turned at least one named test red (the canary script is in the session scratch directory):

| Mutation | Red | Named tests |
|---|---|---|
| `FAMILY = ("httpx",)` (TD50 again) | 27 | every httpx2 behavior case: EG-1 to EG-4, EG-7, EG-9 to EG-12, EG-14, and the structure tests |
| `_SKIP_MODULES` without the family | 9 | EG-1, EG-2 (caller), EG-21 |
| reset skips the sync restore | 7 | EG-16, EG-17, EG-19 |
| reset always `setattr` (no `delattr`) | 1 | EG-A5 |
| no raise when a member is unguarded | 8 | EG-20, EG-A3 |
| `_INSTALLED = True` even when incomplete | 6 | EG-20, EG-A3 |
| adapter saved unconditionally (F7) | 4 | EG-20 |
| marker check removed (alias wrapped twice) | 2 | EG-18 |
| wrapper drops extra arguments | 2 | EG-A4 |
| `startswith` matching back | 4 | EG-21 lookalikes |
| no lock and unconditional adapter save (the old race) | 6 | EG-19, EG-20; EG-19 red in 5 of 5 repeats |
| async wrapper skips the check (httpx2) | 8 | EG-2, EG-3, EG-9 to EG-12 |
| lookup error reported as absent | 1 | EG-A3 |
| import error reported as absent | 1 | EG-A3 |
| `setattr` failure not caught | 1 | EG-A6 |
| `FAMILY = ("httpx",)`, run on BY-1 | 1 | BY-1 [httpx2] |

One canary was invalid on its first try (my replacement text landed inside an `except` chain: a syntax error, so the only red was a collection error). It was rewritten and rerun, and the table counts only the rerun.

**Green: after the change** (passed / failed / skipped; each venv declares its leg):

| File | ci | lock | both | neither | py310 |
|---|---|---|---|---|---|
| `test_httpx_family.py` | 9 / 0 / 0 | 9 / 0 / 0 | 9 / 0 / 0 | 9 / 0 / 0 | 9 / 0 / 0 |
| `test_egress_bootstrap.py` | 22 / 0 / 0 | 22 / 0 / 0 | 22 / 0 / 0 | 22 / 0 / 0 | 22 / 0 / 0 |
| `test_egress_guard.py` | 18 / 0 / 0 | 18 / 0 / 0 | 18 / 0 / 0 | 18 / 0 / 0 | 18 / 0 / 0 |
| `test_egress_httpx.py` | 47 / 0 / 0 | 47 / 0 / 0 | 72 / 0 / 0 | 21 / **1** / 25 | 47 / 0 / 0 |
| `test_qa_airgap_bypass_attempts.py` | 16 / 0 / 0 | 16 / 0 / 0 | 17 / 0 / 0 | 15 / 0 / 1 | 16 / 0 / 0 |
| `test_queuellm_rename_frozen_surface.py` | 3 / 2 / 0 | 3 / 2 / 0 | 3 / 2 / 0 | 3 / 2 / 0 | 3 / 2 / 0 |

- The baseline's two silent skips are gone (ci: `test_egress_httpx.py` 0 passed + 1 skipped became 47 passed; the bypass file 15 + 1 skipped became 16 passed).
- neither: the one failure is EG-0 (required). **The CI leg-5 selection** (the two node ids, declared `none`, in one process): exit code 1, EG-0 fails with the `UNPROVEN` message, EG-0b passes. Collection succeeds, so no module-level HTTP import crept in (E7: exit 2 or 5 would mean it had). The 26 skips are the `[lib]` tests and BY-1 having an empty parameter set.
- `test_queuellm_rename_frozen_surface.py`: the same 2 failures as step 1 and as untouched main. The four drift lines are identical to pristine main in all five venvs, and no file this build modifies appears in them.
- **Order independence (E13)**: the ten-file egress and airgap list in ONE pytest process, forward and in reverse file order, gives identical counts and 0 failed, 0 skipped: 222 passed in ci, lock and py310, 248 in both.

**Other checks at this step**

- The rest of the CI design's list and the three tests that install the guard (`test_airgap_helpers`, `test_qa_tier1_egress_guard`, `test_qa_airgap_happy_setup`, `test_qa_airgap_ipv6_edge_cases`, `test_airgap_modal_dom`, `test_kb_capture`, `test_stt_flow`, `test_ocr_flow`), and eight portal-booting airgap files (`test_airgap_toggle_endpoint`, `test_qa_airgap_toggle_security`, `test_qa_airgap_onetap_paranoid`, `test_airgap_toggle_concurrency`, `test_qa_airgap_toggle_setup_happy`, `test_airgap_sec_fetch_site`, `test_buddy_airgap_watcher`, `test_qa_deep_honest_egress_happy`), in ci and lock: **the failing ids are identical to the orchestrator's untouched-main full-suite baseline in every file** (this is failure mode F28, checked early). In ci some of these files fail (TD48's 401, and TD52 files), exactly as on main; in lock all pass.
- Every constraint in the brief holds for this commit: no `importorskip`; the only `pytest.skip` is EG-0b's "undeclared and not CI" case that the design allows; no HTTP library or huggingface_hub is imported at module level in the two modules the neither leg imports; no real host is named.

**Not verified here**: socks5 and `http2=True` client shapes (need `socksio` and `h2`; QA's E5); the cloud SDKs and huggingface_hub (step 3); a fresh interpreter, import order and aliasing end to end (step 3); the CI workflow (step 7); the full suite (the orchestrator's).

**Self-check.** Scope: `egress.py`, the support module, the two test files and this file, all in the plan. Atomic: it is one change (the guard plus the tests that prove it); the docstrings are part of it because they describe the new behaviour. The message names TD50, what is guarded, and the loud failure.

### Step 3: the brief's probe, huggingface_hub and the SDKs, in a fresh interpreter
Commit: `e7f868a7`.

**Done as planned, with no source change** (`git diff --stat` for this commit touches only tests and this file): `tests/test_egress_httpx_fresh.py` (FR-1 to FR-5), `tests/test_egress_sdk_clients.py` (SDK-1), and `run_fresh` (with `CHECKOUT` and `CHECKOUT_SRC`) in `tests/egress_family_support.py`.

**Deltas from the plan**

1. **Two tests beyond FR-1 to FR-5 and SDK-1.** `test_run_fresh_rejects_a_child_that_imported_another_checkouts_guard` points a child at a stand-in package outside this checkout and expects `run_fresh` to refuse it: the F9 assertion would otherwise be a check nobody has seen fail. `test_the_sdk_declaration_names_only_sdks_this_file_knows`: a typo in `ARAIL_TEST_EXPECT_SDKS` (`antropic`) would silently stop declaring an SDK, the TD50 shape again; it never skips.
2. **Evidence is printed, not recorded with `record_property`.** The default junit family (xunit2) does not carry test-case properties. Each probe prints an `EVIDENCE` table: pytest shows it on a failure and with `-s` or `-rP`. Step 7's workflow can add `-rP` to put it in the CI log.
3. **No time assertion on hf or the probe.** The refusal count (exactly 1 per call) is the real F12 check; a wall-clock bar would fail on a runner whose resolver is slow, which is not a guard failure (`is_local_host` does a real DNS lookup in the fresh interpreter, as the brief's literal probe does). The seconds are reported (below).
4. **Failing messages aggregate every failing cell** and carry the sprint's four parts (what it means, the bar and why, what out of range means, where a failure lives), so a red run teaches by itself (E1, E3, E4).
5. **FR-3 uses the real `httpx2.alias_httpx()`** when the library is httpx2, and `sys.modules[other] = module` (the plan's stand-in) in the other direction.
6. `run_fresh(timeout=180)` for FR-5 and SDK-1: on an unguarded library hf and the SDKs retry with backoff, so a red run reports its failure instead of hitting the 60 s default.
7. **The `sdk` venv needed the same extension as `py310`**: it had pytest but not fastapi, starlette or the rest of what the conftest's autouse fixtures import. I installed the pinned CI-style dev set into it with `--no-deps`; httpx 0.28.1, httpx2 2.16.0, anthropic 2.3.0, openai 3.42.1, h2 and socksio are unchanged.

**Red first: the new tests against the pre-fix `egress.py`** (a scratch tree holding this commit's tests and the guard from before step 2; passed / failed):

| Venv | Result |
|---|---|
| ci | 0 / **5**: FR-1 all four shapes `ConnectError`; FR-2 `ConnectError`; FR-3 the class is not tracked (no `_PATCHED`); FR-4 "install_guard() returned quietly although httpx2 is installed and cannot be imported"; FR-5 `model_info` ends `ConnectError <- ConnectError <- gaierror`, `hf_hub_download` and `snapshot_download` end `LocalEntryNotFoundError <- ConnectError ...`, **0 refusals**, 23.9 s (B2 reproduced) |
| py310 | 0 / **5**, same shapes |
| both | 1 / **7**: the httpx2 cells as in ci; FR-1 for httpx is red only because `coverage()` does not exist; FR-2 for httpx passes |
| lock | 2 / **3**: FR-2 and FR-5 PASS on the old guard (httpx 0.28.1 and hf 1.25.1 were guarded); FR-1 is red only for `coverage()`; FR-3, FR-4 red. Again the old httpx guard is an oracle for the expectations |
| sdk (SDK-1) | 1 / **2**: both SDKs end `ConnectError <- ConnectError <- gaierror` with no `EgressBlocked` in the chain |

**Can they fail? Six more mutations**, each in a scratch copy, each turned named tests red:

| Mutation | Red | Named tests |
|---|---|---|
| install patches only libraries that are already imported (a lazy guard) | 3 | FR-2 (both libraries), FR-4 |
| the marker check removed (alias wrapped twice) | 2 | FR-3 (both directions) |
| an import error reported as absent | 1 | FR-4 |
| `FAMILY = ("httpx",)`, seen from a fresh interpreter | 5 | FR-1, FR-2, FR-3, FR-4, FR-5 |
| `run_fresh` stops checking which checkout the child imported | 1 | the `run_fresh` rejection test |
| `FAMILY = ("httpx",)`, SDKs declared | 2 | SDK-1 (anthropic, openai) |

**Green: after the change** (passed / failed / skipped):

| File | ci | lock | both | neither | py310 | sdk |
|---|---|---|---|---|---|---|
| `test_httpx_family.py` | 9 / 0 / 0 | 9 / 0 / 0 | 9 / 0 / 0 | 9 / 0 / 0 | 9 / 0 / 0 | 9 / 0 / 0 |
| `test_egress_bootstrap.py` | 22 / 0 / 0 | 22 / 0 / 0 | 22 / 0 / 0 | 22 / 0 / 0 | 22 / 0 / 0 | 22 / 0 / 0 |
| `test_egress_guard.py` | 18 / 0 / 0 | 18 / 0 / 0 | 18 / 0 / 0 | 18 / 0 / 0 | 18 / 0 / 0 | 18 / 0 / 0 |
| `test_egress_httpx.py` | 47 / 0 / 0 | 47 / 0 / 0 | 72 / 0 / 0 | 21 / **1** / 25 | 47 / 0 / 0 | 72 / 0 / 0 |
| `test_egress_httpx_fresh.py` | 6 / 0 / 0 | 6 / 0 / 0 | 9 / 0 / 0 | 2 / **1** / 3 | 6 / 0 / 0 | 9 / 0 / 0 |
| `test_egress_sdk_clients.py` | 1 / 0 / **2** | 1 / 0 / **2** | 1 / 0 / **2** | 1 / 0 / **2** | 1 / 0 / **2** | 3 / 0 / 0 |
| `test_qa_airgap_bypass_attempts.py` | 16 / 0 / 0 | 16 / 0 / 0 | 17 / 0 / 0 | 15 / 0 / 1 | 16 / 0 / 0 | 17 / 0 / 0 |
| `test_queuellm_rename_frozen_surface.py` | 3 / 2 / 0 | 3 / 2 / 0 | 3 / 2 / 0 | 3 / 2 / 0 | 3 / 2 / 0 | 3 / 2 / 0 |

- **The 2 skips in the SDK file are the allowed ones**: in a venv with no SDK that does not declare one, SDK-1 skips because the SDKs are optional. In `sdk`, with both declared, there are none. (Declared but missing: the same test fails; shown by hand with the SDKs declared in the ci venv.)
- **neither**: `test_egress_httpx.py` has its one required failure (EG-0). In `test_egress_httpx_fresh.py` the one failure is FR-5: that venv has huggingface_hub installed with its HTTP library removed, so hf cannot import (`No module named 'httpcore2'`); leg 5 never runs it. FR-4 and the `run_fresh` test pass there. The CI leg-5 selection is unchanged: exit 1, EG-0 failing with `UNPROVEN`, EG-0b passing.
- The frozen-surface failure text is identical to pristine main in all six venvs.
- **Order independence**: the twelve-file egress and airgap list in ONE process, forward and in reverse, gives identical counts: 230 passed and the 2 allowed skips in ci, lock and py310, 259 in both, 261 with no skips in sdk.

**Evidence the sprint asked for** (all after the fix; fresh interpreters; `LAB_MODE=airgapped`; `egress-probe.invalid`):

*E1, the brief's probe: library x client shape, outcome (seconds)*

| Venv, library | sync-after (the literal probe) | sync-before | async-after | async-before |
|---|---|---|---|---|
| ci, httpx2 2.16.0 | EgressBlocked (0.011) | EgressBlocked (0.001) | EgressBlocked (0.002) | EgressBlocked (0.002) |
| lock, httpx 0.28.1 | EgressBlocked (0.013) | EgressBlocked (0.001) | EgressBlocked (0.004) | EgressBlocked (0.002) |
| py310, httpx2 2.14.0 (CI leg 2) | EgressBlocked (0.014) | EgressBlocked (0.002) | EgressBlocked (0.002) | EgressBlocked (0.002) |
| both (sdk venv), httpx2 and httpx | EgressBlocked in all four cells for each library (0.001 to 0.014) | | | |

- *Means:* `EgressBlocked` is the guard refusing before any socket I/O; `ConnectError` would be the request reaching the network layer. *Bar and why:* `EgressBlocked` in every cell, because the airgap promises that cloud calls are refused at the HTTP layer. *Before the fix* the same probe ended `ConnectError` in every httpx2 cell (the red table above), so it can fail. *Out of range:* any other outcome means the guard missed that client shape; `NO EXCEPTION` on a `.invalid` host means a DNS wildcard answers reserved names. *Where:* `arail.egress._install_httpx_family_guard`; FR-1.
- FR-2 (install first, then import): EgressBlocked, sync and async, for every installed library in every venv above.

*E3, huggingface_hub: call, final exception, refusals, seconds*

| Venv | `HfApi(endpoint).model_info` | `hf_hub_download` | `snapshot_download` | `model_info` through a session built before install |
|---|---|---|---|---|
| ci, hf 2.4.0 | EgressBlocked, 1, 0.014 | EgressBlocked, 1, 0.002 | EgressBlocked, 1, 0.002 | EgressBlocked, 1, 0.001 |
| lock, hf 1.25.1 | EgressBlocked, 1, 0.015 | EgressBlocked, 1, 0.002 | EgressBlocked, 1, 0.002 | EgressBlocked, 1, 0.001 |
| py310, hf 2.4.0 | EgressBlocked, 1, 0.017 | EgressBlocked, 1, 0.003 | EgressBlocked, 1, 0.002 | EgressBlocked, 1, 0.002 |

- *Means:* one audit line is one attempt the guard refused. *Bar and why:* final `EgressBlocked` and exactly 1 refusal per call, so a refusal never spends hf's retry budget on the network or floods the audit log (before the fix: 0 refusals and `ConnectError`, then `LocalEntryNotFoundError`, 23.9 s). *Out of range:* more than 1 refusal means hf started retrying a `RuntimeError`; a final `LocalEntryNotFoundError` with `EgressBlocked` in its chain means hf started wrapping it (decide on purpose, do not edit the test quietly); `ConnectError` means the library hf rides on is unguarded. *Where:* FR-5; `huggingface_hub.utils._http`.

*E4, the SDKs (sdk venv): case, final exception, refusals, seconds*

| SDK | built before install, `max_retries=0` | built after install, `max_retries=0` | built before, default retries | built after, default retries |
|---|---|---|---|---|
| anthropic 2.3.0 (on `httpx2.HTTPTransport`) | `APIConnectionError <- EgressBlocked`, 1, 0.075 | same, 1, 0.004 | same, **3**, 1.406 | same, **3**, 1.341 |
| openai 3.42.1 (on `httpx2.HTTPTransport`) | `EgressBlocked`, 1, 0.041 | `EgressBlocked`, 1, 0.003 | `EgressBlocked`, 1, 0.002 | `EgressBlocked`, 1, 0.002 |

- *Means:* anthropic wraps the refusal in `APIConnectionError` and retries it; openai lets `EgressBlocked` through. *Bar and why:* `EgressBlocked` in the chain, no network-error type in it, and exactly 1 refusal at `max_retries=0`, which is proof no attempt left the process; the retry count with defaults is the SDK's own business and is reported. *Out of range:* `ConnectError` or `gaierror` in the chain means the SDK's default client left the guarded transports (for example an aiohttp default): an architect decision, not a test edit. *Where:* SDK-1; the SDK's `_base_client`.

**Not verified here**: client shapes that need `h2` or `socksio` (QA's E5); the SDKs' opt-in aiohttp client (a documented gap); the CI workflow (step 7); the full suite (the orchestrator's).

**Self-check.** Scope: only the two new test files, the support module and this file; no source file. Atomic: the fresh-interpreter evidence for the guard and nothing else. The message names the three things proven (the brief's probe, huggingface_hub, the SDKs).

### Step 4: tripwires and pins
Commit: `a02ba141`.

**Done as planned:** EG-23 and EG-24 in `tests/test_egress_httpx.py`; BY-2, BY-3 and BY-4 in `TestDocumentedGaps` in `tests/test_qa_airgap_bypass_attempts.py`. No source file changed.

**Deltas from the plan**

1. **EG-A7, the declaration parser, three tests** (fourteen cases). `declared_family()` was added in step 2 and exercised only through its happy path by EG-0b. These pin every documented form (`httpx2`, `httpx`, `httpx,httpx2` in any order and with spaces, `none`, `NONE`), "nothing declared", and that a mistake (empty, unknown name, `none,httpx`, a doubled comma, a semicolon) raises and does not guess. A leg whose declaration is mis-typed must fail, or the declaration checks nothing. They live in `test_egress_httpx.py`, which this step edits anyway.
2. **The three gap pins make `.invalid` names unresolvable at `socket.getaddrinfo`** (what urllib3 and httpcore call). They send requests PAST the guard on purpose; on a network whose resolver answers reserved names (a wildcard DNS) a plain `.invalid` request could reach a real host. With `getaddrinfo` failing for `.invalid`, every one ends in a resolution error. `gethostbyname` is left alone: the destination in BY-4 is an IP literal.
3. **BY-3 uses `<core>.request(...)`** for `httpcore` 1.0.9 and `httpcore2` 2.16.0, which both export it (checked). BY-4 runs once for `requests` and once per installed family member.
4. **Each pin also asserts the call raised** (a request to a `.invalid` name that *succeeds* means the premise is false and fails with that message) and that **no audit line was written**, as the plan says. The tripwire message names TD53/TD54 and the four places to update: "update docs/PRIVACY.md, README, the Network Policy modal and airgap.KNOWN_GAPS". `airgap.KNOWN_GAPS` does not exist until step 6; the message is text for the person who closes the gap.
5. The module docstring and the `TestDocumentedGaps` docstring of the bypass file now list urllib3, httpcore/httpcore2 and the proxy hop among the documented gaps.
6. **EG-24 was checked against `httpx2.websockets`.** That module imports only with `wsproto`, which the `sdk` venv has, and it defines `ASGIWebSocketTransport`. It derives from `ASGITransport`, so the rule (derive from a patched class or an in-process one) accepts it; the sdk run exercises that branch. The other venvs lack `wsproto`, and `httpx` has no such module, so there the branch is a no-op.

**Red first.** These are tripwires, so "red" is the world changing. Nothing exists to run them against, so each was shown to fail on a planted change (scratch copies only):

| Planted change | Red | Named tests |
|---|---|---|
| an empty `httpx3/` package on `sys.path` | 1 | EG-23 |
| an empty `httpcore3/` package on `sys.path` | 1 | EG-23 |
| a library defines a transport deriving only from the bases (class kept alive; see below) | 2 | EG-24 (httpx2 and httpx), naming `httpx2._stray.StrayTransport` |
| a library exports a transport name nobody has seen | 2 | EG-24 |
| the guard is extended to `urllib3` (pool `urlopen`) | 2 | BY-2, and BY-4 for `requests` (the proxy hop is a pool hop) |
| the guard is extended to `httpcore` and `httpcore2` | 4 | BY-3 (both cores), and BY-4 for both family members |
| the guard starts checking the configured proxy in `GuardedHTTPAdapter.send` | 1 | BY-4 for `requests` |

One canary reported "nothing went red" on its first try. It was the canary: it created the stray class without keeping a reference, and `__subclasses__()` only lists classes that are still alive, so the collector had removed it. With the reference kept, EG-24 flags it by name. (EG-24 scans loaded classes; a class that no longer exists is not a transport anyone can use.)

**Green** (passed / failed / skipped; each venv declares its leg):

| File | ci | lock | both | neither | py310 | sdk |
|---|---|---|---|---|---|---|
| `test_httpx_family.py` | 9 / 0 / 0 | 9 / 0 / 0 | 9 / 0 / 0 | 9 / 0 / 0 | 9 / 0 / 0 | 9 / 0 / 0 |
| `test_egress_bootstrap.py` | 22 / 0 / 0 | 22 / 0 / 0 | 22 / 0 / 0 | 22 / 0 / 0 | 22 / 0 / 0 | 22 / 0 / 0 |
| `test_egress_guard.py` | 18 / 0 / 0 | 18 / 0 / 0 | 18 / 0 / 0 | 18 / 0 / 0 | 18 / 0 / 0 | 18 / 0 / 0 |
| `test_egress_httpx.py` | 63 / 0 / 0 | 63 / 0 / 0 | 89 / 0 / 0 | 36 / **1** / 26 | 63 / 0 / 0 | 89 / 0 / 0 |
| `test_egress_httpx_fresh.py` | 6 / 0 / 0 | 6 / 0 / 0 | 9 / 0 / 0 | 2 / **1** / 3 | 6 / 0 / 0 | 9 / 0 / 0 |
| `test_egress_sdk_clients.py` | 1 / 0 / **2** | 1 / 0 / **2** | 1 / 0 / **2** | 1 / 0 / **2** | 1 / 0 / **2** | 3 / 0 / 0 |
| `test_qa_airgap_bypass_attempts.py` | 20 / 0 / 0 | 20 / 0 / 0 | 23 / 0 / 0 | 17 / 0 / 2 | 20 / 0 / 0 | 23 / 0 / 0 |
| `test_queuellm_rename_frozen_surface.py` | 3 / 2 / 0 | 3 / 2 / 0 | 3 / 2 / 0 | 3 / 2 / 0 | 3 / 2 / 0 | 3 / 2 / 0 |

- Skips: only the 2 "SDK not installed and not declared" cases in venvs with no SDK (allowed: the SDKs are optional; with both declared in `sdk` there are none), and, in `neither`, the `[lib]` tests having an empty parameter set (26 in `test_egress_httpx.py`, 3 in the fresh file, 2 in the bypass file). Nothing is skipped for a present library.
- neither: the failures are EG-0 (required) and FR-5 (huggingface_hub installed without its HTTP library; leg 5 never runs it). **The CI leg-5 selection again: exit 1, EG-0 failing with `UNPROVEN`, EG-0b passing.**
- The frozen-surface text is identical to pristine main in all six venvs.
- **Order independence**: the twelve-file egress and airgap list in ONE process, forward and in reverse: 250 passed and the 2 allowed skips in ci, lock and py310 (identical both ways), 282 in both, 284 with none skipped in sdk.
- **Stability**: `test_egress_httpx.py`, `test_egress_httpx_fresh.py` and `test_qa_airgap_bypass_attempts.py` run five times in a row in ci (89 passed each time) and in both (121 each time).

**Not verified here**: any socks5 or `http2=True` client shape (QA's E5); that the gap tripwires' wording matches the docs, which do not exist until step 6.

**Self-check.** Scope: `test_egress_httpx.py`, `test_qa_airgap_bypass_attempts.py` and this file; no source change. Atomic: one idea, "the guard's list of libraries is fixed, so tell us when the world outgrows it, and pin what it does not cover". EG-A7 sits beside that because it is the same kind of guard on the test side. The message names the tripwires and the pins.

### Step 5: the Open Notebook seeding and the doctor resolve the library through `arail.httpx_family` (TD51, runtime)
Commit: `d626a1cf`.

**Done as planned.** `src/arail/open_notebook_seed.py`: `import httpx` (line 19) became `httpx = httpx_family.preferred()  # noqa-airgap: localhost-only (...)`, the original audit comment kept; nothing else in the module changed. `src/arail/doctor.py`: `check_components` lists `*httpx_family.FAMILY` (httpx2, httpx) in place of `httpx`; `check_environment` prints `installed — guards requests, urllib.request, httpx2 (not installed: httpx)`, built by a new small helper `_guard_summary(egress)` from `egress.coverage()`. On `EgressGuardIncomplete` the existing `except` branch is untouched: it prints the error and records `egress_guard` as a failed required check with the message as detail (the message names the library). Tests: ON-1, ON-2 and DR-1 to DR-3 in `tests/test_httpx_family.py`.

**Deltas from the plan**

1. **More than the plan's six tests, every one pinning a sentence of the contract.** ON-1 is two tests: the real environment (`seed.httpx.__name__` is the first installed member, or the import fails where none is) and a simulated neither that runs in every leg (both members blocked in `sys.modules`; the message must name both and say `pip install`, where the old code said only `import of httpx halted`). **ON-2 does more than `_wait_for_api`**: it runs `_wait_for_api`, `_already_seeded` and `_create_notebook` (a `post` with `json=`, `auth=`, `timeout=`; a 201, and a 500 with a text body for `r.text`) against a loopback stand-in bound to port 0, with the guard installed and airgapped; it asserts that the library the seeding bound carries the guard's marker, that the Basic credentials and the JSON body arrive, that no loopback call was audited as refused, and that the same library is refused for a public name. **ON-3** (new) reads the `httpx.get/post/put` calls and their keyword arguments out of the module's source and checks each against every installed library's signature, because `put` is only reached after the AI-provider setup, which needs a running Open Notebook and cannot be driven here. **DR-3 is two tests**: the failed required finding that names the library, and `doctor.main([])` returning 3 with a broken library and 0 with a whole guard (the other checks stubbed, so only the egress finding decides). They are parametrized over the independently computed installed set; in the neither environment that set is empty and they are skipped for an empty parameter set, as in pass 1.
2. **A `guard_state` fixture** (not autouse: the resolver tests above do not install the guard) resets the guard before and after, points the audit log at `tmp_path`, removes `*_proxy` variables, sets `LAB_MODE=airgapped` and answers `socket.gethostbyname` with `PUBLIC_TEST_IP` (the AF-1 erratum, applied).
3. **`_record` is unchanged**: `egress_guard` still carries no detail on success. The summary is printed, not recorded.
4. **DR-3 passes before the change.** The doctor's existing `except` branch already records the failure now that pass 1's guard raises, so its red-first run is not red. It is shown to fail on planted changes instead (mutants e and f below).

**Red first: the new tests against the unmodified `open_notebook_seed.py` and `doctor.py`** (passed / failed / skipped; `tests/test_httpx_family.py` had 9 tests before):

| Venv | Result | Red, and why |
|---|---|---|
| ci | 12 / **5** / 0 | ON-1 and ON-2: `ModuleNotFoundError: No module named 'httpx'` (the real TD51 fault); ON-1 simulated: the message is `import of httpx halted; None in sys.modules`, no `httpx2`; DR-1: `httpx: not installed` and no httpx2 line; DR-2: `egress guard : installed` and nothing else |
| lock | 14 / **3** / 0 | ON-1 simulated, DR-1, DR-2. ON-1 and ON-2 pass: httpx is the only library, so the old import was right |
| both | 15 / **5** / 0 | as ci, and ON-1 / ON-2 because the seeding bound `httpx` where `httpx2` is preferred (`'httpx' == 'httpx2'`) |
| py310 | 12 / **5** / 0 | as ci |
| neither | 10 / **3** / 4 | ON-1 simulated, DR-1, DR-2; 4 skipped for an empty parameter set (ON-2, ON-3, DR-3 x2) |

**Can the tests fail? Nine mutations**, each in a scratch copy of the tree (never in the worktree), each turned at least one named test red:

| Mutation | Venv | Red | Named tests |
|---|---|---|---|
| the seeding binds the last installed member (httpx where httpx2 is preferred) | both | 3 | ON-1 (real and simulated), ON-2 |
| the seeding passes a keyword the library does not accept | both | 3 | ON-2, ON-3 (httpx2 and httpx) |
| the seeding posts a form instead of JSON | ci | 1 | ON-2 |
| the seeding sends other credentials | ci | 1 | ON-2 |
| the doctor records an incomplete guard as passed | ci | 2 | DR-3 (finding), DR-3 (exit code) |
| the doctor records an incomplete guard as an info finding | both | 4 | DR-3 x2, per library |
| the doctor prints a hard-coded coverage line | ci | 1 | DR-2 |
| the doctor lists httpx alone again | ci | 1 | DR-1 |
| `install_guard` patches no family member | ci | 4 | ON-2 (the library the seeding uses is open), DR-2, DR-3 x2 |

**Green: after the change** (passed / failed / skipped; each venv declares its leg):

| File | ci | lock | both | py310 | neither | sdk |
|---|---|---|---|---|---|---|
| `test_httpx_family.py` | 17 / 0 / 0 | 17 / 0 / 0 | 20 / 0 / 0 | 17 / 0 / 0 | 13 / 0 / 4 | 20 / 0 / 0 |
| `test_egress_bootstrap.py` | 22 / 0 / 0 | 22 / 0 / 0 | 22 / 0 / 0 | 22 / 0 / 0 | 22 / 0 / 0 | 22 / 0 / 0 |
| `test_egress_guard.py` | 18 / 0 / 0 | 18 / 0 / 0 | 18 / 0 / 0 | 18 / 0 / 0 | 18 / 0 / 0 | 18 / 0 / 0 |
| `test_egress_httpx.py` | 63 / 0 / 0 | 63 / 0 / 0 | 89 / 0 / 0 | 63 / 0 / 0 | 36 / **1** / 26 | 89 / 0 / 0 |
| `test_egress_httpx_fresh.py` | 6 / 0 / 0 | 6 / 0 / 0 | 9 / 0 / 0 | 6 / 0 / 0 | 2 / **1** / 3 | 9 / 0 / 0 |
| `test_egress_sdk_clients.py` | 1 / 0 / **2** | 1 / 0 / **2** | 1 / 0 / **2** | 1 / 0 / **2** | 1 / 0 / **2** | 3 / 0 / 0 |
| `test_qa_airgap_bypass_attempts.py` | 20 / 0 / 0 | 20 / 0 / 0 | 23 / 0 / 0 | 20 / 0 / 0 | 17 / 0 / 2 | 23 / 0 / 0 |
| `test_qa_airgap_happy_setup.py` | 17 / 0 / 0 | 17 / 0 / 0 | 17 / 0 / 0 | 17 / 0 / 0 | 17 / 0 / 0 | 17 / 0 / 0 |
| `test_airgap_modal_dom.py` | 6 / 0 / 0 | 6 / 0 / 0 | 6 / 0 / 0 | 6 / 0 / 0 | 6 / 0 / 0 | 6 / 0 / 0 |
| `test_airgap_helpers.py` | 46 / 0 / 0 | 46 / 0 / 0 | 46 / 0 / 0 | 46 / 0 / 0 | 46 / 0 / 0 | 46 / 0 / 0 |

- The skips are the same allowed ones as in pass 1: the SDK-not-installed-and-not-declared cases (venvs without an SDK), and, in neither, the `[lib]` tests having an empty parameter set. neither's two failures are EG-0 (required: it is the sentinel) and FR-5 (huggingface_hub installed without its HTTP library; leg 5 never runs it). **The CI leg-5 selection again: exit 1, EG-0 failing with `UNPROVEN`, EG-0b passing.**
- **Neighbours of the doctor** (ci, lock, both, py310): `test_doctor_embedding_status.py` 6 / 0 / 0, `test_compiled_kb_sweep_prune.py` 10 / 0 / 0, `test_qa_tier1_egress_guard.py` 28 / 0 / 0, `test_qa_airgap_ipv6_edge_cases.py` 14 / 0 / 0, all four venvs. `test_ensure_ready_build_isolation.py` is 9 / 1 / 0 in all four: `test_cross_world_contamination_probe` fails with `No module named 'pandas'` (the scratch venvs have no pandas), on untouched main too (run in `base-wt`) and in the orchestrator's full-suite baseline for both ci and lock. Not this work.
- **The frozen-surface text is identical to pristine main's** (the `E ` lines diffed): the same four drift lines, and none of `doctor.py`, `open_notebook_seed.py` or the tests appears in them.
- **Order independence**: the twelve-file egress and airgap list in ONE process, forward and in reverse: 258 passed and the 2 allowed skips in ci, lock and py310, 293 passed and 2 skipped in both, 0 failed in all, identical both ways.

**Evidence the sprint asked for**

*E12, what an operator sees (a fresh interpreter per venv, `doctor.check_environment()` and `check_components()`, `LAB_MODE` unset so airgapped; no lab state is read or written)*

| Venv | `egress guard` line | Components |
|---|---|---|
| ci | `installed — guards requests, urllib.request, httpx2 (not installed: httpx)` | `httpx2 : 2.16.0`, `httpx : not installed` |
| lock | `installed — guards requests, urllib.request, httpx (not installed: httpx2)` | `httpx2 : not installed`, `httpx : 0.28.1` |
| both | `installed — guards requests, urllib.request, httpx2, httpx` | `httpx2 : 2.16.0`, `httpx : 0.28.1` |
| neither | `installed — guards requests, urllib.request (not installed: httpx2, httpx)` | both `not installed` |
| ci, `httpx2.AsyncHTTPTransport` deleted | `? (EgressGuardIncomplete: egress guard incomplete — httpx2: AsyncHTTPTransport.handle_async_request is missing. Requests made through httpx2 are NOT checked against LAB_MODE. Still guarded: requests, urllib.request. Fix: install a supported version (httpx2<3, httpx<1) or uninstall httpx2.)`; the failing finding is `egress_guard`, level required, so the exit is 3 | |
| lock, `httpx.AsyncHTTPTransport` deleted | the same message naming `httpx` and the exit 3 | |

- *Means:* what `./arailctl doctor` prints for its Environment and Components sections; the exit is the module's (`arailctl` turns a non-zero module exit into `doctor: degraded`, exit 3, `arailctl:1058-1068`). *Bar and why:* every family member is listed with a version or `not installed`; the egress line names the clients the guard covered; a library the guard cannot patch fails a REQUIRED check and is named, because the doctor is where an operator learns the airgap is open. *Out of range:* `httpx: not installed` with no httpx2 line means `doctor.py` still has the old list. *Where:* DR-1 to DR-3; `doctor.check_components` and `check_environment`.
- Before the change the same probe printed `egress guard : installed` in every venv and `httpx : not installed` (and no httpx2 line) in ci: the red-first table above.

**Not verified here**: the portal's `/api/open-notebook/start` end to end (needs `docker compose` and the Open Notebook images); `seed()` as a whole and `_setup_ai_provider` (need a running Open Notebook; its `put` call is covered by ON-3 only, by signature); the real `./arailctl doctor` run (it walks the lab's PKB, models and database; the two checks this commit changes were run in isolation instead).

**Self-check.** Scope: `open_notebook_seed.py`, `doctor.py`, `tests/test_httpx_family.py` and this file, all in the plan. Atomic: one change, "the two runtime consumers of the HTTP library resolve it through the one place that names it". The message names TD51 (runtime) and says what each consumer does now.

### Step 6: the docs say what the guard wraps and list the gaps it does not
Commit: pending (a commit cannot contain its own SHA; the next commit fills it in).

**Done as planned.** `README.md` (the "What local-first means" paragraph), `docs/PRIVACY.md` (the enforcement sentence, the whole "Known gaps" section, the audit grep), the modal paragraph in `_airgap_modal.html`, the `airgap.py` docstring and the new `airgap.KNOWN_GAPS` (five strings, the first three byte-identical to the ones `portal/app.py` carried), the `router/core.py` comment, `/api/airgap/status` (only the `known_gaps` expression and the handler's docstring: it reads `airgap.KNOWN_GAPS` and appends one entry per `egress.uncovered()`; `guard_installed` is untouched), the deliberate edit of the two gap lists in `tests/test_qa_airgap_happy_setup.py` (httpx is no longer a gap; urllib3 and httpcore are; each gets the design's comment; the test names are unchanged), and `tests/test_egress_docs.py`. The `egress.py` docstrings the design lists were done in step 2.

**Deltas from the plan**

1. **Four sentences of the design were false or imprecise, and the true version is what is written** (details in "Doc claims checked", rows 16, 17, 26 and 30, and in "Architect feedback required", AF-2): "the lab says so instead of running unguarded" became "instead of staying silent" (the portal keeps running with that library open); "the portal logs" became "the portal's activity log records"; "the agent loader" is not a process, so the bullet says "any process that loads agents through the agent loader"; the audit grep's "Find every network call in the Python code" became "Find where the Python code uses the HTTP clients and raw sockets this doc names" (two network calls in the tree are not matched by the pattern).
2. **The design's reason for dropping the "knowledge-canvas clients" claim is incomplete** (row 29, AF-3). The claim stays dropped and nothing is written in its place, because no single sentence about those clients is true.
3. **More tests than DOC-1 to DOC-5**, each pinning a sentence the docs now make: the enforcement paragraph names the family (DOC-1); the README is held to the gap list too (DOC-2); `KNOWN_GAPS[:3]` are the old strings and there are five (DOC-2); the DOC-3 pattern is shown to match the stale sentence it hunts (a regex that cannot match proves nothing) and is run over six surfaces, not three; DOC-4 covers `airgap.__doc__` as well as `egress.__doc__`; the router comment names the four clients; DOC-6 pins the path `/api/airgap/status` to `nav.js` to the modal's list, including that `nav.js` writes each gap with `textContent` (the entry for an unpatchable library carries a library's own error text, so it must never become markup); DOC-8 runs the audit grep's pattern against a sample of every client the doc names; DOC-9 scans `src/arail` for `install_guard()` call sites and requires exactly the four the "Other processes" bullet lists; and the string PRIVACY.md quotes from the portal is the string the portal emits. DOC-5 has a baseline (a whole guard adds nothing) and calls the endpoint function directly (no TestClient, no server, no port).
4. **`tests/test_egress_docs.py` is parametrized over the independently computed installed set** for DOC-5, like the egress tests; in the neither environment that is one skip for an empty parameter set.

**Red first: the new tests against the current docs and source** (20 tests in ci; passed / failed / skipped):

| Venv | Result |
|---|---|
| ci | 3 / **17** / 0 |
| lock | 3 / **17** / 0 |
| both | 3 / **18** / 0 |
| py310 | 3 / **17** / 0 |
| neither | 3 / **16** / 1 |

Red in every venv: each surface lacks `httpx2` (README also lacks `urllib.request`); no surface lists urllib3, httpcore or the proxy hop; `airgap.KNOWN_GAPS` does not exist; the stale "httpx — not wrapped" line is found in `airgap.py`; `airgap.__doc__` does not name httpx2; the router comment does not name the family; the status endpoint has no extra entry and no baseline list; the audit command and the "Other processes" bullet do not exist; PRIVACY.md does not quote the portal's warning. The three that pass already are the regex's own positive control, the `nav.js` wiring (it exists) and `egress.__doc__` (done in step 2).

**Can the tests fail? Fourteen mutations**, each in a scratch copy of the tree (never in the worktree), each turned a named test red:

| Mutation | Red | Named tests |
|---|---|---|
| README stops naming httpx2 | 1 | DOC-1 [README] |
| the stale "httpx — not wrapped" line returns to `airgap.py` | 1 | DOC-3 |
| a new family member (`httpx3`) is added to `FAMILY` and nobody edits the docs | 5 | DOC-1 [README, PRIVACY, modal], the enforcement paragraph, the router comment |
| the status endpoint stops appending `uncovered()` | 1 | DOC-5 |
| the status endpoint lists only the first three documented gaps | 2 | DOC-5 (whole guard, incomplete guard) |
| `nav.js` writes a gap with `innerHTML` | 1 | DOC-6 |
| the audit grep loses `httpcore` | 1 | DOC-8 |
| a new module calls `install_guard()` | 1 | DOC-9 |
| the doctor stops installing the guard | 1 | DOC-9 |
| the first documented gap string changes | 1 | `KNOWN_GAPS[:3]` |
| the portal's warning is reworded | 1 | the quoted-string test |
| README drops urllib3 from the gaps | 1 | DOC-2 [README] |
| the modal stops naming httpx2 | 1 | DOC-1 [modal] |
| the enforcement sentence drops httpx2 | 1 | DOC-1 (enforcement) |

The design's E10(a) expects `FAMILY = ("httpx",)` to turn DOC-1 red. It cannot: the docs still name everything `GUARDED_CLIENTS` then lists, so DOC-1 stays green and the tests that go red for that mutation are the guard's own (EG-*, FR-*, `HF-1`, BY-1). The mutation that turns DOC-1 red is the one above, a member added without docs, which is the case DOC-1 exists for.

**Green: after the change** (passed / failed / skipped; each venv declares its leg):

| File | ci | lock | both | py310 | neither | sdk |
|---|---|---|---|---|---|---|
| `test_egress_docs.py` | 20 / 0 / 0 | 20 / 0 / 0 | 21 / 0 / 0 | 20 / 0 / 0 | 19 / 0 / 1 | 21 / 0 / 0 |
| `test_httpx_family.py` | 17 / 0 / 0 | 17 / 0 / 0 | 20 / 0 / 0 | 17 / 0 / 0 | 13 / 0 / 4 | 20 / 0 / 0 |
| `test_egress_bootstrap.py` | 22 / 0 / 0 | 22 / 0 / 0 | 22 / 0 / 0 | 22 / 0 / 0 | 22 / 0 / 0 | 22 / 0 / 0 |
| `test_egress_guard.py` | 18 / 0 / 0 | 18 / 0 / 0 | 18 / 0 / 0 | 18 / 0 / 0 | 18 / 0 / 0 | 18 / 0 / 0 |
| `test_egress_httpx.py` | 63 / 0 / 0 | 63 / 0 / 0 | 89 / 0 / 0 | 63 / 0 / 0 | 36 / **1** / 26 | 89 / 0 / 0 |
| `test_egress_httpx_fresh.py` | 6 / 0 / 0 | 6 / 0 / 0 | 9 / 0 / 0 | 6 / 0 / 0 | 2 / **1** / 3 | 9 / 0 / 0 |
| `test_egress_sdk_clients.py` | 1 / 0 / **2** | 1 / 0 / **2** | 1 / 0 / **2** | 1 / 0 / **2** | 1 / 0 / **2** | 3 / 0 / 0 |
| `test_qa_airgap_bypass_attempts.py` | 20 / 0 / 0 | 20 / 0 / 0 | 23 / 0 / 0 | 20 / 0 / 0 | 17 / 0 / 2 | 23 / 0 / 0 |
| `test_qa_airgap_happy_setup.py` | 17 / 0 / 0 | 17 / 0 / 0 | 17 / 0 / 0 | 17 / 0 / 0 | 17 / 0 / 0 | 17 / 0 / 0 |
| `test_airgap_modal_dom.py` | 6 / 0 / 0 | 6 / 0 / 0 | 6 / 0 / 0 | 6 / 0 / 0 | 6 / 0 / 0 | 6 / 0 / 0 |
| `test_airgap_helpers.py` | 46 / 0 / 0 | 46 / 0 / 0 | 46 / 0 / 0 | 46 / 0 / 0 | 46 / 0 / 0 | 46 / 0 / 0 |
| `test_qa_tier1_egress_guard.py` | 28 / 0 / 0 | 28 / 0 / 0 | 28 / 0 / 0 | 28 / 0 / 0 | n/a | n/a |
| `test_qa_airgap_ipv6_edge_cases.py` | 14 / 0 / 0 | 14 / 0 / 0 | 14 / 0 / 0 | 14 / 0 / 0 | n/a | n/a |

- Skips and the two neither failures are the same as in step 5 (the allowed SDK-not-installed cases, empty parameter sets, EG-0 by design, FR-5 for huggingface_hub without its HTTP library). **The CI leg-5 selection: exit 1, EG-0 failing with `UNPROVEN`, EG-0b passing.**
- **The files that read what this commit edits** (the 33 files found by grep for the README, `PRIVACY.md`, the modal, `/api/airgap/status`, `known_gaps`, `_CLOUD_BACKENDS`, plus the three tests that install the guard and the portal-booting airgap files; the list is `tests/portal/test_token_compliance.py`, `tests/router/test_router_airgap_gate.py`, `test_aerollm_bundle_compliance`, `test_airgap_sec_fetch_site`, `test_airgap_toggle_concurrency`, `test_airgap_toggle_endpoint`, `test_branch_browser`, `test_buddy_airgap_watcher`, `test_buddy_watcher_after_onetap_toggle`, `test_build_aerollm_honest`, `test_build_qkz_corpus`, `test_dbspec_repo`, `test_docgen`, `test_docs_cross_links`, `test_docs_registry_qa`, `test_host_contract_vendor`, `test_kb_capture`, `test_kb_world_scoping`, `test_llama_disclosure`, `test_model_boot_banner_dom`, `test_model_hosting_reframe_qa`, `test_ocr_flow`, `test_portal_login`, `test_qa_airgap_onetap_paranoid`, `test_qa_airgap_toggle_security`, `test_qa_airgap_toggle_setup_happy`, `test_qa_deep_honest_egress_happy`, `test_qa_deep_honest_setup`, `test_qa_security_hygiene_paranoid`, `test_setup_extras`, `test_skill_packs`, `test_stt_flow`, `test_window_modal_dom`): **the failing ids are identical to the orchestrator's untouched-main full-suite baseline, file by file, in both ci and lock** (0 new ids in either). In lock the endpoint tests really run (`test_airgap_toggle_endpoint` 16 / 0 / 0, `test_qa_airgap_toggle_setup_happy` 8 / 0 / 0, `test_qa_airgap_onetap_paranoid` 24 / 0 / 0); in ci most portal-booting files stop at TD48's 401 before the handler, exactly as on main, so DOC-5 (which calls the handler directly) is what exercises it there. The token-compliance ratchet fails on main for other templates; its failure text is identical before and after, and `_airgap_modal.html` is not in it.
- **The frozen-surface text is identical to pristine main's**: the four drift lines are the same; `README.md`, `docs/PRIVACY.md`, the modal, `doctor.py`, `open_notebook_seed.py`, `airgap.py` and `router/core.py` are not in the "tokens lost" list, and `src/arail/portal/app.py` still shows only `AEROLLM_MODEL: 3` and `AeroLLMBackend: 1`.
- **Order independence**: the thirteen-file egress and airgap list in ONE process, forward and in reverse: 278 passed and the 2 allowed skips in ci, lock and py310; 314 passed and 2 skipped in both; 316 passed and 0 skipped in sdk; 0 failed everywhere, identical both ways.

**What the modal shows** (the status endpoint called in a fresh interpreter, ci venv; the second run deletes `httpx2.AsyncHTTPTransport` first, as EG-20 does):

| Guard | `guard_installed` | `known_gaps` |
|---|---|---|
| whole | `True` | the five documented gaps |
| httpx2 cannot be patched | `False` | the five, then `httpx2: installed, but the guard could not patch it (AsyncHTTPTransport.handle_async_request is missing); its requests are NOT checked` |

### Doc claims checked

Every factual sentence that went into `README.md`, `docs/PRIVACY.md`, the modal, a docstring or a code comment, checked against the tree before it was written. "Run" means I ran it in this session; the commands and the test files are in the evidence column. Probes marked "one-off" are fresh-interpreter scripts in the scratch directory (not committed), run with `LAB_MODE=airgapped`, this branch's guard, `egress-probe.invalid` as the host and `socket.gethostbyname` answering a public address.

| # | Where | Claim | Verdict | Evidence |
|---|---|---|---|---|
| 1 | README, modal, PRIVACY | The guard wraps `requests`, `urllib.request`, `httpx` and `httpx2` | true | `egress.py:642` (requests adapter), `:650` (urllib opener), `:655` (family, `httpx_family.FAMILY == ("httpx2", "httpx")`). EG-1 to EG-4 and FR-1 (every client shape) green in ci, lock, both, py310. The claim is per installed library; the doctor prints `(not installed: ...)` for the other |
| 2 | README, modal | ...so also the Hugging Face downloader | true | FR-5: `HfApi.model_info`, `hf_hub_download`, `snapshot_download` and a session built before install end `EgressBlocked`, one audit line each, on hf 2.4.0 (ci, py310) and 1.25.1 (lock). One-off: `InferenceClient` (the router's `huggingface` backend) ends `EgressBlocked`, one audit line, in ci and lock |
| 3 | README, modal, PRIVACY | ...and the Anthropic and OpenAI SDKs (their default clients) | true | SDK-1 in the sdk venv: anthropic 2.3.0 and openai 3.42.1 (httpx2), built before and after install, `max_retries=0` ends with `EgressBlocked` in the chain and one audit line. One-off in throwaway venvs: anthropic 0.120.0 (lock-base venv) and openai 2.49.0 (the lock's versions, httpx 0.28.1): `APIConnectionError <- EgressBlocked`, one audit line each. Limit: the default clients; the opt-in aiohttp client is a stated gap (row 20) |
| 4 | README | The badge is clickable and shows what is and isn't enforced, the recent blocks and the known gaps | true (unchanged sentence) | `nav.js:83-84`; the modal has the definition, "Recent activity" and "Known gaps" (`_airgap_modal.html`) |
| 5 | README, PRIVACY, modal | The gaps named: `aiohttp`, `urllib3` or `httpcore` used directly, a proxy you configure, raw sockets, subprocess `curl` | true | each is in `airgap.KNOWN_GAPS` and in PRIVACY's list; DOC-2 holds README, PRIVACY, modal and the status list to the same four words |
| 6 | README | The full list is in `docs/PRIVACY.md` | true | PRIVACY.md "Known gaps"; the link resolves (`test_docs_cross_links.py` green, same ids as main) |
| 7 | PRIVACY | The guard blocks calls through those four to anything that is not loopback, RFC1918 or link-local | true | `airgap.is_local_ip` (`is_loopback or is_private or is_link_local`); EG-5 (127.0.0.1, 10.0.0.5 pass), EG-1 (public refused). The design dropped "agent-originated": the guard checks every call in a process that installed it, whoever made it |
| 8 | PRIVACY | `requests` (the adapter every new `Session` gets) | true | `egress.py:642-643` patch `requests.adapters.HTTPAdapter` and `requests.sessions.HTTPAdapter`; `test_egress_guard.py` green |
| 9 | PRIVACY | `urllib.request` (the default opener) | true | `egress.py:650` `install_opener`; a custom `build_opener()` without these handlers would bypass it, which the parenthesis ("the default opener") says |
| 10 | PRIVACY | huggingface-hub 2.x, anthropic 2.x and openai 3.x use `httpx2` in place of `httpx` | true | package metadata: hf 2.4.0 `Requires-Dist: httpx2<3,>=2.0.0` (no `httpx`), anthropic 2.3.0 `httpx2<3,>=2.0.0`, openai 3.42.1 `httpx2<3,>=2.12.0`. The older lock versions (hf 1.25.1, anthropic 0.120.0, openai 2.49.0) require `httpx` |
| 11 | PRIVACY | The check sits on the transport classes, so it covers clients built before the guard and the clients SDKs build for themselves | true | EG-3 and FR-1 (built before install), FR-2 (install first), SDK-1 (built before and after) |
| 12 | PRIVACY | ...Hugging Face downloads (`hf_hub_download`, `HfApi`) | true | row 2 |
| 13 | PRIVACY | ...and the Open Notebook seeding | true, scoped | ON-2: the library the seeding binds carries the guard's marker; its loopback calls pass; the same library is refused for a public name. Every call the seeding makes goes to localhost, so "covers" means its client is guarded |
| 14 | PRIVACY | `./arailctl doctor` prints which of the four this lab's guard covered | true | `doctor._guard_summary` prints `installed — guards requests, urllib.request, httpx2 (not installed: httpx)`: the covered ones, then the ones that cannot be used. DR-2 compares it with the independent lookup; E12 output in step 5 |
| 15 | PRIVACY | If one is installed but cannot be patched, requests made through it are not checked | true | the `EgressGuardIncomplete` message says `Requests made through X are NOT checked against LAB_MODE` (EG-A2); EG-20 |
| 16 | PRIVACY | ...the lab says so **instead of staying silent** | **design wrong, rewritten.** The design says "instead of running unguarded" | the portal catches the error and keeps running (`portal/app.py:900-906`), the loader logs and continues (`agents/loader.py:313-317`): the library stays unguarded, and what changed is that it is said. Only the Model Forge worker stops (`nucleus/worker.py:39` is outside any `try`: one-off, `httpx2.AsyncHTTPTransport` deleted, `LAB_MODE=hybrid`: exit 1 with the traceback) and the doctor degrades |
| 17 | PRIVACY | ...the portal's activity log records "Egress guard install failed" | **reworded** (design: "the portal logs") | `portal/app.py:903-906` `activity_log.emit("system", f"Egress guard install failed: {_eg_err}", "warn")`; the event goes to the activity stream and its jsonl file (`activity.py` `emit`). The loader writes a different line to the Python log: `Egress guard install failed in load_all: ...` (`loader.py:317`). The doc quotes the portal's; a test ties the quote to the source |
| 18 | PRIVACY | ...`./arailctl doctor` fails its egress check | true | `doctor.py:108-110` records `egress_guard` (required, ok=False); `main()` returns 3; `arailctl:1058-1068` turns that into `doctor: degraded`, exit 3. DR-3 twice; E12 run in step 5 |
| 19 | PRIVACY | ...the Network Policy modal lists the library under known gaps | true | `portal/app.py:11977-11981` appends `uncovered()` entries; `nav.js:133-139` writes each as an `<li>` with `textContent` into `#airgap-gaps-list`; `_airgap_modal.html:136` is that list, inside the "Known gaps" `<details>`. DOC-5 and DOC-6; the table above. Not run: a browser |
| 20 | PRIVACY | `aiohttp` is not used in tree today; the SDKs offer an opt-in aiohttp client (`DefaultAioHttpClient`) that is not covered | true (the "not covered" half by reading code) | `git grep` for `import aiohttp`: none in tracked files outside sprints and docs/archive. anthropic 2.3.0 `_base_client.py:1801-1828` and openai 3.42.1 `_base_client.py:1571-1590` define it, behind the `aiohttp` extra (it raises without it). `_vendor/httpx_aiohttp/transport.py:81` `AiohttpTransport(httpx.AsyncBaseTransport)` does its I/O through aiohttp; the guard patches `AsyncHTTPTransport`, a different class. Not run: aiohttp is not installed in any scratch venv |
| 21 | PRIVACY, modal, docstring | `urllib3`, `httpcore`, `httpcore2` called directly are not wrapped | true | BY-2 and BY-3 (pins: not `EgressBlocked`, no audit line) green in all four venvs |
| 22 | PRIVACY, `KNOWN_GAPS` | Nothing in `src/arail/` calls them directly today | true, scoped | `grep -rnE "urllib3\|httpcore" src/arail/` (every file type under `src/arail/`) finds, before this commit's text, seven lines: `egress.py:14` and `:145-153` (a docstring and `_SKIP_MODULES` names) and `httpx_family.py:4` (a docstring); with this commit's own docs, ten (`airgap.py:15,48` and the modal's paragraph, all prose). A second grep for import statements and `urllib3.x(` / `httpcore.x(` calls over `src/arail/` is empty. `git grep` of import statements over every tracked file outside sprints, docs/archive and uv.lock: only `tests/test_qa_airgap_bypass_attempts.py:338`, the BY-2 pin. Not covered: installed packages that use them (lancedb's urllib3 client, TD53), `src/dac_world/`, `core/`, `scripts/` |
| 23 | PRIVACY, modal, `KNOWN_GAPS` | The hop to a proxy you configure is not checked (`HTTP_PROXY`/`HTTPS_PROXY`, `proxy=`/`proxies=`), so a local request can go through a public proxy | true | BY-4 (`requests` with `proxies=`, each family member with `proxy=`) green in all four venvs. One-off with the env variable: `HTTP_PROXY` at a public name that never resolves, destination `127.0.0.1`: requests `ProxyError`, httpx2 `ConnectError`, httpx `ConnectError`, zero audit lines in each |
| 24 | PRIVACY | Raw sockets and subprocess shells | unchanged sentences | not re-audited |
| 25 | `KNOWN_GAPS` | the first three strings | moved byte-identical | a test pins them. Pre-existing claims ("BUDDY_EGRESS_PROBE is the only audited use", "curl/wget: none in tree today") not re-audited; a spot check found no subprocess call to curl or wget in `src/arail` (the `curl` strings are install hints shown to the user, `portal/services/opencode.py:1126-1143`, and a help text, `portal/app.py:10218`) and the raw sockets in `src/arail` are the probe (`egress.py:867`) and loopback port checks (`portal/edge_app.py:70,106`) |
| 26 | PRIVACY | Other processes: the guard lives in the portal, any process that loads agents through the agent loader, Model Forge's worker and `./arailctl doctor` | true, reworded (design: "the agent loader" as a process) | the four `install_guard()` call sites: `portal/app.py:903`, `agents/loader.py:315`, `nucleus/worker.py:39`, `doctor.py:100`; DOC-9 requires exactly these. `load_all()` has one caller in tree (`portal/app.py:1233`), so the loader is not a separate process today |
| 27 | PRIVACY | ...setup's and upgrade's model downloads run outside it | true | `scripts/setup.sh:1939,1984,1993,2008` and `scripts/upgrade.sh:177,186`: `python3 -c "from huggingface_hub import snapshot_download; ..."` in a separate interpreter that never imports `arail.egress`; `huggingface-cli download` in other branches |
| 28 | PRIVACY | ...Docker containers such as Open Notebook's, and opencode | true | `portal/app.py:2579-2583` `docker compose ... up -d` on `compose/open-notebook.yml` (`_COMPOSE_FILE`, `:2524`); `portal/services/opencode.py:363-364` `subprocess.Popen(["opencode", "serve", ...])` |
| 29 | PRIVACY (old sentence) | "...the open-notebook / knowledge-canvas clients" | **dropped, and the design's reason is incomplete.** The design says the Knowledge Canvas backend "is a separate container outside any in-process guard" | the portal also mounts it in-process (`portal/app.py:757-771`, with a skip warning if the import fails), and `app.main` imports in a stock ci and lock venv (run). In-process its `llm_router._ollama_fallback` is refused (one-off, lock and both: `EgressBlocked`, one audit line). The compose copy (`core/knowledge-canvas/docker-compose.yml`) is started by nothing in the lab (`git grep`), and `core/knowledge-canvas/client.py` runs wherever other skills import it. No single true sentence covers all three, so none is written (the Open Notebook half survives as "the Open Notebook seeding", row 13). New finding in AF-3 |
| 30 | PRIVACY | The audit grep (`grep -rnE "httpx\|httpcore\|urllib3\|requests\.\|urllib\.request\|aiohttp\|socket\.socket\(" src/arail/`) | true as reworded. The design's "Find every network call in the Python code" is **false** | the command ran on the tree after this commit's edits: 29 files, 168 lines (the old pattern: 156 lines). Two network calls in the tree are not matched: `socket.create_connection` (`portal/services/opencode.py:268`) and `asyncio.open_connection` (`portal/app.py:2512`); SDK calls (anthropic, openai, huggingface_hub) do not name an httpx-family library at the call site. The comment now says "Find where the Python code uses the HTTP clients and raw sockets this doc names". DOC-8 runs the pattern on a sample of every client the doc names |
| 31 | README, modal | The Anthropic and OpenAI SDKs are "built on them" | true | rows 3 and 10 |
| 32 | `airgap.py` docstring | The guard lives in `arail.egress`; the gaps are "documented in PRIVACY.md and listed by the Network Policy modal (`KNOWN_GAPS` below)" | true | `egress.py`; PRIVACY.md; row 19 |
| 33 | `router/core.py` comment | The egress guard (requests, urllib.request, httpx, httpx2) would block the eventual API call of a cloud backend anyway | true | the three `_CLOUD_BACKENDS`: `claude` builds `anthropic.Anthropic` (`router/backends.py:790-791`), `huggingface` builds `InferenceClient` (`:579-580`), `openrouter` uses `requests.Session` (`:625-626`); all three refused in the probes of rows 2 and 3 and by EG-1. The guard must be installed in that process, as it is in the portal |

The Model Forge worker, the agent loader and the doctor were checked as asked (rows 16 to 18): the portal and the loader report and keep running, the doctor fails its required check, the worker stops.

**Not verified here**: a browser rendering of the modal (the path is checked in source and by the endpoint's output); the aiohttp client of the SDKs (not installed); the compose copy of the Knowledge Canvas backend; any claim about a process outside `src/arail` beyond what rows 20 to 29 cite.

**Self-check.** Scope: every file is in the plan, and the one deliberate test edit is logged here. `portal/app.py` changed only inside `get_airgap_status` (two import lines, the gap list, the docstring). Atomic: one idea, "the words match the code, and the endpoint reports what the guard could not do". The message names what the guard wraps and the gaps.

## Architect feedback required

**AF-1 (non-blocking; worked around with the same intent; please correct ARCHITECTURE.md).** "Test strategy" says the in-process tests monkeypatch `socket.gethostbyname` to return a TEST-NET-3 address (`203.0.113.7`) for names, so that the local check is deterministic. `ipaddress` calls the documentation ranges private: `ip_address("203.0.113.7").is_private` is `True` on CPython 3.10.22 and 3.11.17 (also 192.0.2.0/24 and 198.51.100.0/24). `arail.airgap.is_local_ip` returns `addr.is_private or ...`, so a name that resolves there is **local** and the guard lets it through. With the literal as written, EG-1 to EG-4 could never see `EgressBlocked`. Intent kept (a deterministic answer for names, no DNS query, no real host): the in-process tests patch only `socket.gethostbyname`, and return a globally routable address, `151.101.64.81`, the one `tests/test_egress_guard.py` and `tests/test_qa_airgap_bypass_attempts.py` already use. `getaddrinfo` stays real, so a guard regression is still a fast `ConnectError` on a `.invalid` name and never a connection to the patched address. The constant is `PUBLIC_TEST_IP` in `tests/egress_family_support.py`, with this reason in a comment (landed in step 2). No design decision, contract or failure mode changes.

**AF-2 (non-blocking; found in step 6; the true version is what is written; please correct ARCHITECTURE.md).** Sentences in "Documentation (exact wording)" and in the test notes that the tree does not support. Evidence for each is in step 6's "Doc claims checked".

1. PRIVACY, Known gaps: "the lab says so **instead of running unguarded**". False: the portal catches `EgressGuardIncomplete` and keeps serving (`portal/app.py:900-906`) and the loader logs and continues (`agents/loader.py:313-317`), so the library stays unguarded and what changes is that the lab says so. Written: "instead of staying silent". Only the Model Forge worker stops (it has no handler, exit 1; run) and the doctor degrades.
2. Same sentence: "the portal **logs** 'Egress guard install failed'". It is an activity-log event at warn level (`activity_log.emit("system", ...)`), and the loader's different line goes to the Python log. Written: "the portal's activity log records".
3. PRIVACY, "Other processes": "the Python processes that install it (the portal, **the agent loader**, ...)". The loader is not a process; `load_all()` has one caller in tree, the portal's startup. Written: "any process that loads agents through the agent loader".
4. PRIVACY, audit grep: "# Find **every network call** in the Python code". False for this pattern and for the old one: `socket.create_connection` (`portal/services/opencode.py:268`) and `asyncio.open_connection` (`portal/app.py:2512`) are network calls it does not match, and SDK calls name no httpx-family library at the call site. Written: "Find where the Python code uses the HTTP clients and raw sockets this doc names".
5. Evidence E10(a) expects `FAMILY = ("httpx",)` to turn DOC-1 red. It cannot (the docs still name everything `GUARDED_CLIENTS` then lists); the guard's own tests go red for it, and DOC-1 goes red when a member is added without docs (shown).
6. Not changed, for you to decide: the README's parenthetical of gaps does not include "other processes" (setup's and upgrade's model downloads run outside the guard, per PRIVACY), so "everything built on them, such as the Hugging Face downloader" is true for the processes that install the guard and the README sends the reader to PRIVACY for the full list. Adding "other processes" to the parenthetical is a one-clause edit if you want it.

**AF-3 (non-blocking; found in step 6; a debt candidate, not fixed here).** The design drops PRIVACY's old "knowledge-canvas clients" claim because "the Knowledge Canvas backend is a separate container outside any in-process guard". That is only half true. The portal also mounts the backend in-process (`portal/app.py:757-771`), `app.main` imports in a stock ci and lock venv (run), and its Ollama fallback is refused by the guard there (run, lock and both: `EgressBlocked`). The claim stays dropped, because the compose copy (`core/knowledge-canvas/docker-compose.yml`, started by nothing in the lab) and `core/knowledge-canvas/client.py` are not provably guarded and no single sentence covers all three.

The same probe found a TD51-class fault outside TD52's inventory (B8 set `core/knowledge-canvas/` aside as "a separate app with its own requirements.txt", but the portal runs it in its own process): `core/knowledge-canvas/backend/app/services/llm_router.py:32` does `import httpx` inside `_ollama_fallback`, and in the ci venv (httpx2 only) that raises `ModuleNotFoundError: No module named 'httpx'` (run). Two more `import httpx` sites exist outside `src/arail`: `core/knowledge-canvas/client.py:25` (module level) and `scripts/deep_honest_win.py:717` (a witness script that needs the library starlette uses). Suggest adding all three to TD52's row at step 8, or filing them as their own debt. Nothing was changed there (`core/` and `scripts/` are not in the plan).

## Final state (pass 1, steps 0 to 4)

- **Commits:** `052a6217` (skeleton), `da2057b6` (step 1), `96d87fb2` (step 2), `e7f868a7` (step 3), and `a02ba141` (step 4). Branch `qukaizen/arail-egress-httpx2`, not pushed.
- **Diff, `src/` and `tests/` only, against the ledger commit `982da749`:** 8 files, +2,236 / -172 (step 4 accounts for +246 / -7 of that, in two test files). Source: `src/arail/egress.py` (+292 / -101) and the new `src/arail/httpx_family.py` (+79). Tests: `tests/egress_family_support.py` (new), `tests/test_httpx_family.py` (new), `tests/test_egress_httpx_fresh.py` (new), `tests/test_egress_sdk_clients.py` (new), `tests/test_egress_httpx.py` (rewritten), `tests/test_qa_airgap_bypass_attempts.py` (BY-1 and BY-2 to BY-4).
- **Tests now (passed / failed / skipped):** the commit-4 table above. In ci, lock, both, py310 and sdk: 0 failed. The only failures anywhere are the two pre-existing `test_queuellm_rename_frozen_surface.py` failures (identical to untouched main) and, in `neither`, EG-0 on purpose.
- **Coverage:** not measured (no coverage tool was asked for). The new code paths each have a test that was seen failing against a planted fault: 16 mutations in step 2, 6 in step 3 and 7 in step 4, listed above.
- **Not touched:** every path the brief lists as frozen. `README.md`, `docs/`, the modal, `airgap.py`, `router/core.py`, `portal/app.py`, `doctor.py` and `open_notebook_seed.py` belong to steps 5 and 6.
- **Environment:** the `py310` and `sdk` scratch venvs were extended as described (pinned dev set, `--no-deps`); `wsproto` was already in `sdk`. Nothing was created in the worktree except committed files (`git status` clean after every commit).

**Observations for the review (not defects)**

1. With an incomplete guard (`_INSTALLED` stays False by design), every `install_guard()` call retries and logs one error line. In a running portal that is twice per process start (the startup hook and `agents.loader.load_all`; `load_all` has one caller, the startup hook), and once per `./arailctl doctor` or Model Forge worker run, not per request. That is bounded, but it is two identical error lines at boot.
2. The strategy text calls the TEST-NET-3 address "public" (AF-1). Anything else in the design that relies on how `ipaddress` classifies an address is worth the same check.
3. `starlette` 1.3.1 (the lock venv) prints a deprecation warning when `starlette.testclient` is imported ("Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead"). EG-13 imports it; the warning is harmless and not an error.
4. For step 6 and 7: the gap tripwires' failure text already names `airgap.KNOWN_GAPS`; step 6 should make that name true. Step 7's workflow may want `-rP` on the pytest line so the `EVIDENCE` tables reach the CI log.
