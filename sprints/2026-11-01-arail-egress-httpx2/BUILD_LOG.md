# Build log: the egress guard covers the httpx family (TD50, runtime half of TD51)

**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `ec2a12be` (accepted by the ledger at `982da749`)
**Branch:** `qukaizen/arail-egress-httpx2` (base `origin/main` `5997363e`, which is #210). Not pushed.
**Started:** 2026-11-01
**Pass 1:** commits 1 to 4 of the "Atomic commit plan" (the guard and its proof): `052a6217` (skeleton), `da2057b6`, `96d87fb2`, `e7f868a7`, `a02ba141`.
**This pass (pass 2):** commits 5 to 7 (the consumers, the docs, the CI workflow), starting from head `5ea83b9f` (pass 1 plus the orchestrator's checkpoint 1). Commit 8 is the orchestrator's, after #211 merges. Pass 1's log is kept below, unedited except for back-filled SHAs.
**Status:** pass 2 done: steps 5, 6 and 7 are committed (`d626a1cf`, `ac1bb82b`, `95c70aea`). Step 8 is the orchestrator's, after #211 merges.
**Pass 3 (review fixes):** the fix pass after the architect's BLOCK (REVIEW.md). Done: commits `81548cd0` to `55107aeb` (W4, B1, N2, W3, W1, W2, B1 docs, N1, W5 and its follow-up), plus this file. Its own section is at the end of this file, "Pass 3 (review fixes)". The step 7 SHA above was back-filled by pass 3 (a commit cannot contain its own SHA).
**Pass 4 (QA fixes):** the fix pass after QA's WEAK_PASS (TEST_REPORT.md): QA-W2, QA-W1 and the nits N1 to N8 (QA-W3 and PX-1 are filed as debt by the orchestrator, not built). Done: commits `df90517b` (plan), `b2fb7f3e` (W2), `3cb17af4` (N8), `66b39468` (W1), `61535641` (N1), `a64d9f5a` (N2), `259ff646` (N3), `4501336a` (N5), `399e6ff8` and `2eb75e65` (N4), `2e48ad23` (N6), `843d25e8` (N7), `54af2aab` and `8fe63fbc` (two test follow-ups), plus this file. Its plan and results are in the section at the end of this file, "Pass 4 (QA fixes)".

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
| 6 | `README.md` (298-307); `docs/PRIVACY.md` (43-44, 57-73, 301); `src/arail/portal/templates/_airgap_modal.html` (137-146); `src/arail/airgap.py` (docstring, new `KNOWN_GAPS`); `src/arail/router/core.py` (comment, 14-17); `src/arail/portal/app.py` (the `/api/airgap/status` handler only); `tests/test_egress_docs.py` (new); `tests/test_qa_airgap_happy_setup.py` (the two gap lists, on purpose) | The docs say what is wrapped and list what is not. `KNOWN_GAPS` moves to `arail.airgap`; the status endpoint reads it and appends `egress.uncovered()`. Every factual sentence is checked against the tree before it is written ("Doc claims checked", step 6). | DOC-1 to DOC-5, written first and seen failing against the current docs. | `ac1bb82b` |
| 7 | `.github/workflows/egress-guard.yml` (new); `tests/egress_ci_check.py` (new); `tests/test_egress_ci_check.py` (new) | The five-leg workflow and the junit checker it calls. | CI-1 to CI-4, written first (the checker does not exist yet), plus a mutant that makes `no-skips` ignore skips: CI-2 must go red. The workflow is validated three ways (see below). | `95c70aea` |
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
Commit: `ac1bb82b`.

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

### Step 7: the five-leg workflow and the checker that lets it fail
Commit: `95c70aea`.

**Done as planned.** `.github/workflows/egress-guard.yml` is the design's YAML. Diffed against the fenced block in ARCHITECTURE.md's "CI design", the committed file has **three extra lines and none removed**: a comment and two `# shellcheck disable=SC2086` directives before the two commands that expand `$EGRESS_TESTS` unquoted on purpose (actionlint reported SC2086 twice without them; with them it exits 0; a directive only takes effect immediately before the command, which I found by trying three placements). Action versions are the repo's own (`actions/checkout@v4`, `actions/setup-python@v5`). `tests/egress_ci_check.py` is the checker (stdlib only, runs as a script, runs under Python 3.10). `tests/test_egress_ci_check.py` has 43 tests.

**Deltas from the plan**

1. **More than CI-1 to CI-4**, because the checker is only half of what keeps leg 5 honest. CI-1 to CI-4 are as designed and go further than the design lists: an expected failure counts as a skip; a failed or errored case is named; one run lists every problem; a listed file whose name only *starts* like another's (`test_egress_httpx` against `test_egress_httpx_fresh`) does not count; a collection error is an error; a class or a subdirectory counts for its file; unreadable, missing and empty junit files fail; leg 5 fails on each of pytest exits 0, 2, 3, 4 and 5 even when the XML looks right, on a passed sentinel, on a sentinel that failed for another reason, on a missing, skipped or errored sentinel, and on a declaration that is missing, failed, skipped or errored; a misused command line is exit 2; and the script, run as the workflow runs it, exits with the checker's code. **CI-5 and CI-6 are new**: they read the workflow as YAML and require that the matrix keys it expands exist in every leg, that no step has `continue-on-error` or `|| true` (read from keys and commands, not comments), that every file in `EGRESS_TESTS` exists and that pytest and the checker are given the same `$EGRESS_TESTS`, and that leg 5's two node ids, the checker's two constants and the two functions in `tests/test_egress_httpx.py` are the same names (F24: a renamed sentinel would otherwise make leg 5 collect nothing or look for a test that is gone).
2. **The sentinel's failure is matched on the failure's message, not its text.** pytest prints a failing test's whole source in the failure text, and the sentinel's source contains `UNPROVEN`, so matching the text would accept any failure inside it. A test plants exactly that.
3. **The checker prints each file's counts** (`tests/test_x.py: 17 passed`) before its findings, so the CI log carries the per-file numbers E6 asks for.
4. **Exit status 4, not 2 or 5, is what a module that cannot import gives in leg 5** (AF-2, item 7). Found by planting the fault (run F5 below); the checker's message for 4 says what it can mean.
5. Not added: `-rP` on the pytest lines (pass 1's observation 4, to put the `EVIDENCE` tables in the CI log). The design's commands are kept as written; adding it is one flag if the reviewer wants those tables on every green run.

**Red first.** Against an absent checker the new test file fails at collection (1 error). Against a checker whose `main` is `return 0` (a checker that always says fine, the failure this sprint exists to remove), **35 of its 43 tests are red** (the other 8 do not depend on a verdict: the six workflow-structure tests, the script run that expects exit 0, and the clean-run case in `test_classes_and_subdirectories_count_for_their_file`).

**Can they fail? Thirteen more mutations**, each in a scratch copy of the tree, each turned named tests red:

| Mutation | Red | Named tests |
|---|---|---|
| `no-skips` ignores skips (E10(e)) | 4 | CI-2: a skipped case, an expected failure, one run lists every problem, the script's exit status |
| a listed file that ran nothing is accepted | 3 | CI-3 |
| the module match is `startswith` again | 1 | CI-3 (the lookalike file) |
| leg 5 accepts any pytest exit status | 6 | CI-4 (exits 0, 2, 3, 4, 5 and the all-problems case) |
| leg 5 accepts a sentinel that passed | 1 | CI-4 |
| leg 5 accepts any failure of the sentinel | 1 | CI-4 (another reason) |
| leg 5 accepts a failed declaration | 4 | CI-4 (failed, skipped, errored, all-problems) |
| one leg loses a matrix key | 1 | CI-6 |
| `\|\| true` after the checker | 1 | CI-6 |
| a typo in leg 5's node id | 1 | CI-5 |
| the sentinel is renamed | 1 | CI-5 |
| a typo in `EGRESS_TESTS` | 1 | CI-5 |
| the checker is given fewer files than pytest runs | 1 | CI-5 |

**Green** (passed / failed / skipped; each venv declares its leg): `test_egress_ci_check.py` 43 / 0 / 0 in ci, lock, both, py310 (Python 3.10.22) and neither.

**The workflow, validated three ways**

*(a) Structure.* `python -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]))" .github/workflows/egress-guard.yml` loads. The matrix keys that `${{ matrix.* }}` expands are `expect`, `install`, `leg`, `python`, `sdks`, and all five legs have all five (checked in this session and, permanently, by CI-6). `python` is a string in every leg (`"3.10"` would be the float 3.1 otherwise). The only other expressions are `github.event_name`, `github.event.pull_request.number || github.ref`.

*(b) actionlint.* Installed in a throwaway venv under the scratch directory (`pip install actionlint-py shellcheck-py`: actionlint 1.7.12, shellcheck 0.11.0). It first reported SC2086 (info) twice, for the unquoted `$EGRESS_TESTS`, which is intentional; with the three comment lines above it exits **0** on this workflow and on every workflow in `.github/workflows` (the repo's other workflows exited 0 before). It checks the `${{ }}` expressions, the matrix and `if:` contexts, the step names and every `run:` script with shellcheck.

*(c) Every leg, emulated.* `emulate_workflow.py` (scratch directory, not committed) loads the YAML and runs each leg's test steps exactly as the workflow writes them: the `run:` text, matrix expressions substituted, `bash -eo pipefail`, `CI=true`, `GITHUB_ACTIONS=true`, the leg's declaration, `cwd` the worktree. The only substitutions: `.venv/bin/python` becomes the leg's scratch venv, the junit file names go under the scratch directory, and `.venv/bin/pip install` becomes `uv pip install --python` in the SDK step. Exit codes (the step is `set -euo pipefail`, so a step that exits 0 means pytest and the checker both did):

| Leg | Venv | Python | `What this leg installed` | pytest | checker | Step |
|---|---|---|---|---|---|---|
| 1 httpx2 only (pip, floating) | ci | 3.11.17 | httpx2 2.16.0, httpcore2 2.16.0, hf 2.4.0, starlette 1.10.0 | 320 passed, exit 0 | `ok - 13 files, 320 cases, none skipped, failed or missing`, exit 0 | exit 0 |
| 1, SDK step | a fresh venv: ci plus the step's own `pip install anthropic openai` (anthropic 2.3.0, openai 3.42.1, installed by the step) | 3.11.17 | | 3 passed, exit 0 | `ok - 1 files, 3 cases, ...`, exit 0 | exit 0 |
| 2 httpx2 only, Python 3.10 | py310 | **3.10.22** (also runs the checker) | httpx2 2.14.0, httpcore2 2.14.0, hf 2.4.0, starlette 1.7.0 | 320 passed, exit 0 | ok, 13 files, 320 cases, exit 0 | exit 0 |
| 3 httpx only (uv.lock) | the venv the literal `uv sync --frozen --extra dev --python "$(command -v python)"` produced in a scratch copy of the project (uv 0.13.5; 5 min of downloads) | 3.11.17 | httpx 0.28.1, httpcore 1.0.9, hf 1.25.1, starlette 1.3.1, no httpx2 | 320 passed, exit 0 | ok, 13 files, 320 cases, exit 0 | exit 0 |
| 4 both | both | 3.11.17 | httpx 0.28.1 and httpx2 2.16.0 | 356 passed, exit 0 | ok, 13 files, 356 cases, exit 0 | exit 0 |
| 5 neither (negative control) | neither (ci with httpx2 and httpcore2 removed, as the leg's `pip uninstall` does) | 3.11.17 | no httpx, httpx2, httpcore, httpcore2 | **exit 1** (`1 failed, 1 passed`: the sentinel failed, the declaration passed) | `ok - the sentinel failed with UNPROVEN, as it must with neither library, and the declaration passed`, exit 0 | exit 0 |

*What the pipeline does when something is wrong* (the same step text, one planted fault in a scratch copy of the tree each time):

| Fault | Leg | Result |
|---|---|---|
| **E10(f): the sentinel is made to always pass** | 5 | pytest exit 0 (`2 passed`); the checker: `pytest exited 0, but leg 5 must see exactly 1 ...` and `the sentinel ... passed in a venv with neither library: it can pass vacuously ...`; **step exit 1** |
| a silent skip planted on a test in `test_airgap_helpers.py` (the TD50 shape: pytest exits 0) | 1 | the checker: `SKIPPED tests.test_airgap_helpers.TestLabMode::test_default_is_airgapped: ... a skip is how TD50 stayed hidden`; **step exit 1** |
| every test in `test_airgap_modal_dom.py` renamed so the listed file collects nothing | 1 | pytest exit 0 for everything else; the checker: `NO TESTS from tests/test_airgap_modal_dom.py ...`; **step exit 1** |
| the leg declares `httpx2,httpx` but the venv has only httpx2 | 2 | pytest `1 failed` (EG-0b: `this leg declared ['httpx', 'httpx2'] but the environment has ['httpx2']`); **step exit 1** |
| a module-level `import httpx` in `test_egress_httpx.py` (a collection problem) | 5 | pytest **exit 4**; the checker: three findings (the exit status, the sentinel and the declaration are not in the junit file); **step exit 1** |

*The install steps cannot be run here as written* (they need GitHub's runner, PyPI as of the run, `actions/checkout` and `actions/setup-python`), and I say so. What I did instead: (1) each leg's venv was built by an equivalent route in pass 1 (pip floating, `uv sync --frozen --extra dev`, plus httpx, minus httpx and httpx2), and for leg 3 I also ran the literal `uv sync` command above and ran the leg against its result; (2) the two install scripts were run with stubbed `python`, `pip`, `uv` and `sleep`, to check their shell logic and not PyPI: the commands and arguments per mode (floating: `pip install -e ".[dev]"` and nothing else; both: plus `pip install httpx`; neither: plus `pip uninstall -y httpx httpcore httpx2 httpcore2`; lock: `pip install --upgrade pip "uv>=0.8"` then `uv sync --frozen --extra dev --python <python>`), a retry after one and two failures with the `::warning::` lines and the 20 s and 40 s waits, and exit 1 with `::error::dependency install failed after 3 attempts` (and `uv sync failed after 3 attempts`) after three; (3) the SDK step's install loop ran for real, with `uv pip install` for `.venv/bin/pip install`.

**Evidence the sprint asked for**

- *E6, the targeted list per leg*: the table above (320 / 320 / 320 / 356 cases, 0 failed, 0 skipped, every one of the 13 files at least one case; the checker prints each file's count). Order independence (E13): the 13-file CI list in ONE process, forward and reverse: 320 passed in ci, lock and py310, 356 in both, 359 in sdk with the SDK file; 0 failed and 0 skipped in all, identical both ways.
- *E7, negative control*: leg 5's exit 1 with `UNPROVEN`, and the five faults above. Before this work the same venv ran the egress files green.
- *E10*: (e) and (f) are shown (mutant `no-skips` ignoring skips turns CI-2 red; the always-passing sentinel turns the negative control red). (a) to (d) were shown in steps 2 and 4.
- *E11, the import cost `install_guard()` adds* (median of 5 fresh interpreters that had already imported `arail.egress`, libraries not yet imported; the process also installs the `requests` adapter and the urllib opener): ci (httpx2) 8.9 ms, lock (httpx) 10.5 ms, both 17.0 ms, py310 (httpx2 2.14.0) 7.7 ms. *Bar:* under 50 ms for each library. *Means:* what a portal, doctor or worker start pays. *Out of range:* a figure near 50 ms for one library would mean revisiting eager import for the CLI paths. *Where:* `egress._install_httpx_family_guard`.

**Not verified here** (the PR's own CI run is the check for each): the install steps on GitHub's runner; whether leg 2's `pip` resolves httpx2 2.14.0 on Python 3.10 (uv did; if pip differs, the declaration test says so and the decision is for the reviewer); `actions/checkout` and `actions/setup-python` with `cache: pip` (the glob `**/requirements.txt` matches `core/knowledge-canvas/backend/requirements.txt`, as for the other workflows); the `schedule` trigger; the concurrency group; ubuntu-latest, since these 13 files have only run on this Mac (nothing in them names a platform: `grep` found a zone-id string and a `win32` skipif); the wall time (the four full test steps took 6 to 31 s here and leg 5's 0.1 s; the budget is 15 minutes per leg); that the real runner has no stray `*_proxy` variable or DNS wildcard (the tests clear proxy variables; a wildcard answering `.invalid` would show as `NO EXCEPTION` in FR-1).

**Self-check.** Scope: the three new files, in the plan, plus this file; the workflow differs from the design only by the three comment lines. Atomic: one idea, "run the guard's tests in every httpx-family state, daily, and prove the proof can fail". The message names the five legs, the checker and the daily schedule.

## Architect feedback required

**AF-1 (non-blocking; worked around with the same intent; please correct ARCHITECTURE.md).** "Test strategy" says the in-process tests monkeypatch `socket.gethostbyname` to return a TEST-NET-3 address (`203.0.113.7`) for names, so that the local check is deterministic. `ipaddress` calls the documentation ranges private: `ip_address("203.0.113.7").is_private` is `True` on CPython 3.10.22 and 3.11.17 (also 192.0.2.0/24 and 198.51.100.0/24). `arail.airgap.is_local_ip` returns `addr.is_private or ...`, so a name that resolves there is **local** and the guard lets it through. With the literal as written, EG-1 to EG-4 could never see `EgressBlocked`. Intent kept (a deterministic answer for names, no DNS query, no real host): the in-process tests patch only `socket.gethostbyname`, and return a globally routable address, `151.101.64.81`, the one `tests/test_egress_guard.py` and `tests/test_qa_airgap_bypass_attempts.py` already use. `getaddrinfo` stays real, so a guard regression is still a fast `ConnectError` on a `.invalid` name and never a connection to the patched address. The constant is `PUBLIC_TEST_IP` in `tests/egress_family_support.py`, with this reason in a comment (landed in step 2). No design decision, contract or failure mode changes.

**AF-2 (non-blocking; found in step 6; the true version is what is written; please correct ARCHITECTURE.md).** Sentences in "Documentation (exact wording)" and in the test notes that the tree does not support. Evidence for each is in step 6's "Doc claims checked".

1. PRIVACY, Known gaps: "the lab says so **instead of running unguarded**". False: the portal catches `EgressGuardIncomplete` and keeps serving (`portal/app.py:900-906`) and the loader logs and continues (`agents/loader.py:313-317`), so the library stays unguarded and what changes is that the lab says so. Written: "instead of staying silent". Only the Model Forge worker stops (it has no handler, exit 1; run) and the doctor degrades.
2. Same sentence: "the portal **logs** 'Egress guard install failed'". It is an activity-log event at warn level (`activity_log.emit("system", ...)`), and the loader's different line goes to the Python log. Written: "the portal's activity log records".
3. PRIVACY, "Other processes": "the Python processes that install it (the portal, **the agent loader**, ...)". The loader is not a process; `load_all()` has one caller in tree, the portal's startup. Written: "any process that loads agents through the agent loader".
4. PRIVACY, audit grep: "# Find **every network call** in the Python code". False for this pattern and for the old one: `socket.create_connection` (`portal/services/opencode.py:268`) and `asyncio.open_connection` (`portal/app.py:2512`) are network calls it does not match, and SDK calls name no httpx-family library at the call site. Written: "Find where the Python code uses the HTTP clients and raw sockets this doc names".
5. Evidence E10(a) expects `FAMILY = ("httpx",)` to turn DOC-1 red. It cannot (the docs still name everything `GUARDED_CLIENTS` then lists); the guard's own tests go red for it, and DOC-1 goes red when a member is added without docs (shown).
6. Not changed, for you to decide: the README's parenthetical of gaps does not include "other processes" (setup's and upgrade's model downloads run outside the guard, per PRIVACY), so "everything built on them, such as the Hugging Face downloader" is true for the processes that install the guard and the README sends the reader to PRIVACY for the full list. Adding "other processes" to the parenthetical is a one-clause edit if you want it.
7. E7 says that exit 2 or 5 in the neither venv means a collection problem. With explicit node ids (what leg 5 runs) a test module that fails to import makes pytest exit **4** ("found no collectors"), not 2 (shown in step 7, run F5). The checker rejects any exit status but 1, and its message for 4 says what it can mean.

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

## Final state (pass 2, steps 5 to 7)

- **Commits:** `24ea80a0` (the pass-2 plan), `d626a1cf` (step 5), `ac1bb82b` (step 6), and `95c70aea` (step 7). Branch `qukaizen/arail-egress-httpx2`, not pushed. Step 8 is the orchestrator's.
- **Diff of pass 2, `src/`, `tests/`, README, `docs/` and `.github/` only, against `5ea83b9f`:** 14 files, +1,526 / -44. Source: 6 files, +67 / -22 (`airgap.py` +23 / -5, `doctor.py` +19 / -2, `portal/app.py` +9 / -7 inside `get_airgap_status`, `open_notebook_seed.py` +7 / -1, the modal paragraph +6 / -4, `router/core.py` +3 / -3). Docs: README +10 / -6 and `PRIVACY.md` +37 / -10. Tests: `test_httpx_family.py` +283 / -2, `test_egress_docs.py` new (322), `egress_ci_check.py` new (249), `test_egress_ci_check.py` new (403), `test_qa_airgap_happy_setup.py` +8 / -4 (the two gap lists). Workflow: `.github/workflows/egress-guard.yml` new (147 lines).
- **Tests now (passed / failed / skipped), per venv, for the egress and airgap list** (the 13 files of the workflow's list plus `test_egress_sdk_clients.py`):

| File | ci | lock | both | py310 | neither | sdk |
|---|---|---|---|---|---|---|
| `test_airgap_helpers.py` | 46 / 0 / 0 | 46 / 0 / 0 | 46 / 0 / 0 | 46 / 0 / 0 | 46 / 0 / 0 | 46 / 0 / 0 |
| `test_airgap_modal_dom.py` | 6 / 0 / 0 | 6 / 0 / 0 | 6 / 0 / 0 | 6 / 0 / 0 | 6 / 0 / 0 | 6 / 0 / 0 |
| `test_egress_bootstrap.py` | 22 / 0 / 0 | 22 / 0 / 0 | 22 / 0 / 0 | 22 / 0 / 0 | 22 / 0 / 0 | 22 / 0 / 0 |
| `test_egress_ci_check.py` | 43 / 0 / 0 | 43 / 0 / 0 | 43 / 0 / 0 | 43 / 0 / 0 | 43 / 0 / 0 | 43 / 0 / 0 |
| `test_egress_docs.py` | 20 / 0 / 0 | 20 / 0 / 0 | 21 / 0 / 0 | 20 / 0 / 0 | 19 / 0 / 1 | 21 / 0 / 0 |
| `test_egress_guard.py` | 18 / 0 / 0 | 18 / 0 / 0 | 18 / 0 / 0 | 18 / 0 / 0 | 18 / 0 / 0 | 18 / 0 / 0 |
| `test_egress_httpx.py` | 63 / 0 / 0 | 63 / 0 / 0 | 89 / 0 / 0 | 63 / 0 / 0 | 36 / **1** / 26 | 89 / 0 / 0 |
| `test_egress_httpx_fresh.py` | 6 / 0 / 0 | 6 / 0 / 0 | 9 / 0 / 0 | 6 / 0 / 0 | 2 / **1** / 3 | 9 / 0 / 0 |
| `test_egress_sdk_clients.py` | 1 / 0 / **2** | 1 / 0 / **2** | 1 / 0 / **2** | 1 / 0 / **2** | 1 / 0 / **2** | 3 / 0 / 0 |
| `test_httpx_family.py` | 17 / 0 / 0 | 17 / 0 / 0 | 20 / 0 / 0 | 17 / 0 / 0 | 13 / 0 / 4 | 20 / 0 / 0 |
| `test_qa_airgap_bypass_attempts.py` | 20 / 0 / 0 | 20 / 0 / 0 | 23 / 0 / 0 | 20 / 0 / 0 | 17 / 0 / 2 | 23 / 0 / 0 |
| `test_qa_airgap_happy_setup.py` | 17 / 0 / 0 | 17 / 0 / 0 | 17 / 0 / 0 | 17 / 0 / 0 | 17 / 0 / 0 | 17 / 0 / 0 |
| `test_qa_airgap_ipv6_edge_cases.py` | 14 / 0 / 0 | 14 / 0 / 0 | 14 / 0 / 0 | 14 / 0 / 0 | 14 / 0 / 0 | 14 / 0 / 0 |
| `test_qa_tier1_egress_guard.py` | 28 / 0 / 0 | 28 / 0 / 0 | 28 / 0 / 0 | 28 / 0 / 0 | 28 / 0 / 0 | 28 / 0 / 0 |

  The only failures anywhere are the two pre-existing `test_queuellm_rename_frozen_surface.py` failures (identical to untouched main after every commit of this pass) and, in neither, EG-0 on purpose and FR-5 (huggingface_hub installed without its HTTP library; leg 5 never runs it). The one TD48 failure in `tests/test_qa_deep_honest_egress_happy.py` is unchanged (ci and py310 fail the same id as the baseline; lock and both pass; the file is not touched and not in the workflow's list).
- **Neighbours:** the 33 files that read what step 6 edits have the same failing ids as untouched main in both ci and lock. `test_ensure_ready_build_isolation.py::test_cross_world_contamination_probe` (`No module named 'pandas'`) fails in every scratch venv and on untouched main.
- **Mutations shown to turn a named test red in this pass:** 9 (step 5), 14 (step 6), 15 (step 7); 5 more faults planted in the real pipeline's steps.
- **Not touched:** every path the brief lists as frozen; no `aerollm` / `AERO_*` token; `src/arail/egress.py` and `src/arail/httpx_family.py` unchanged in pass 2 (no test exposed a defect in either). Nothing was created in the worktree by this pass except committed files and the runtime files the existing suite writes to its own gitignored `lab/data` and `logs/scheduler` (they date from the baseline run; `lab/data/egress.jsonl` grows from `tests/test_egress_bootstrap.py`, which does not redirect its data dir).
- **Environment:** scratch only. New this pass: `ci-sdk` (ci's freeze, httpx2 only; the SDK step installed the SDKs into it), `oa2` (openai 2.49.0 on httpx 0.28.1, for the doc-claim probe), `actionlint` (actionlint-py, shellcheck-py), a scratch copy of the project in which `uv sync` ran, and `lock-uvsync` (a link to it).

**Observations for the review (not defects)**

1. `tests/test_egress_bootstrap.py` writes its audit lines into the repository's own `lab/data/egress.jsonl` (gitignored) because it does not set `ARAIL_DATA_DIR`. Harmless in CI, untidy locally; not touched (it is not this work's file).
2. With an incomplete guard the status endpoint reports `guard_installed: False` while the portal keeps serving: that is the honest reading of "not completely installed", and the modal's list says which library is open. A UI that wants a stronger signal (a banner) would be new work.
3. The status entry carries a library's own exception text (the reason), and the endpoint is authenticated; `nav.js` writes it with `textContent` (pinned by DOC-6).
4. Three more TD51-class `import httpx` sites exist outside TD52's inventory: AF-3.

## Pass 3 (review fixes)

The fix pass after the architect's review ([REVIEW.md](./REVIEW.md), verdict BLOCK). The spec is REVIEW.md "Required actions before QA", items 1 to 7, read in the file, not this summary. Start: head `98e9e98a`, tree clean, branch `qukaizen/arail-egress-httpx2`, not pushed.

### Scope (set by the orchestrator)

- **In:** B1, W1, W2, W3, W4, W5, and the nits N1 and N2.
- **Out, by decision:** N3, N5 and N7 are skipped; N4 needs no action (the RLock stays); N6 is only filed as debt (TD59), by the orchestrator.
- **Filed, not built:** TD56 (fail closed), TD57 (post-import hook), TD58 (subprocess guarding) and TD59 (socket-timeout change). The orchestrator files them in the BACKLOG commit (item 8). Nothing here implements any of them.
- **Not mine:** `SPRINT.md` and `sprints/BACKLOG.md`. The B1 decision text is written below and in `docs/PRIVACY.md`; the orchestrator copies it into the ledger and the PR description.
- **W3's installer change is message text only** (tell the user to restart the lab); nothing else in that code path changes.
- **The workflow edit is limited to W5.** New tests go into files already listed in `EGRESS_TESTS`, so `tests/egress_ci_check.py`, CI-5 and CI-6 need no change.
- **Frozen, untouched:** `src/arail/deep_*.py`, `src/dac_world/`, `src/arail/buddy_host/contract/`, `tests/conftest.py`, `pyproject.toml`, `uv.lock`, `CHANGELOG.md`, any `aerollm` / `AERO_*` name. The airgapped default is not relaxed by anything here. The wording rule of pass 2 (the "Wording" bullet) holds.

### The decision B1 asks to record (verbatim; for the ledger and the PR description)

> With an incomplete guard the portal and the agent loader keep serving with that library unguarded; only Model Forge's worker exits.

Why this stands for this PR (REVIEW.md "Rulings"): no supported library version reaches the state today, a member whose import raised cannot be used by anyone, and `main` is strictly worse (the same state is silent there). What changes in this pass is that the UI stops contradicting the guard: the modal's pill turns `warn`, its description names the library and says its requests are NOT blocked, the known-gaps list opens, and the portal's startup signal is pinned by a behavioural test. Fail-closed (refuse to start airgapped, and refuse the toggle, while `uncovered()` names a library whose import succeeded) is TD56, the operator's call, because it trades availability for the invariant.

### Plan

A commit cannot contain its own SHA, so each row's "Commit ref" was filled in by the final BUILD_LOG commit (row 10), whose own SHA is the orchestrator's to record. Tests come first wherever the failure mode is new (rows 1, 2, 3, 4, 5), and are run against the unmodified source before the fix; docs come after the behaviour they describe (rows 6 to 8).

| # | Files | Change | Test | Commit ref |
|---|---|---|---|---|
| 0 | `sprints/2026-11-01-arail-egress-httpx2/BUILD_LOG.md` | This plan; step 7's SHA back-filled. | none (docs) | `7ce4d2ff` |
| 1 | `tests/test_egress_httpx.py`; `tests/test_egress_httpx_fresh.py` | **W4.** EG-A3 (in process) and FR-4 (fresh interpreter) simulate the broken import with `RuntimeError` and with a real `ModuleNotFoundError` (the dependency of the library is missing, which is the `neither` venv's huggingface_hub state). | Mutant R5 (an `ImportError` counts as absent) must go red. | `81548cd0` |
| 2 | `src/arail/portal/app.py` (`_install_egress_guard`, the status definition); `src/arail/portal/static/nav.js`; `src/arail/portal/templates/_airgap_modal.html`; `tests/test_egress_docs.py` | **B1, code.** The portal's guard-install block moves into a function. With an unguarded library the airgapped definition names it and says its requests are NOT blocked; `nav.js` paints the pill `warn` and opens the known-gaps list when `guard_installed` is false (still `textContent`); the modal's static paragraph no longer says httpx2 is caught without qualification. | DOC-5 extended (definition) plus new behavioural tests of the function (incomplete: one warning naming the library and the fix; whole: none), of `_startup` calling it first, and of `nav.js`; the source-string match of the portal's warning becomes behavioural. Mutant R6 must go red. | `c14b2dd4` |
| 3 | `src/arail/nucleus/worker.py`; `tests/test_egress_docs.py` | **N2.** The worker catches `EgressGuardIncomplete` in `main`, writes `worker: <message>` to stderr, returns 1 (still fail closed; no traceback). | A test that the worker exits 1 with one line and never reaches the phase. | `dc6a246f` |
| 4 | `src/arail/egress.py` (two docstrings); `src/arail/plugins/manager.py` (message text); `docs/PRIVACY.md` (the doctor sentence); `tests/test_egress_docs.py`; `tests/test_qa_airgap_bypass_attempts.py` | **W3.** "Absent" means absent when the guard ran: a library installed while the lab runs is not guarded until the lab restarts. The plugin installer's success line says to restart. | A documented-gap tripwire for the late install (BY-6) and a test that the installer's line says restart. | `a71bc309` |
| 5 | `README.md`; `docs/PRIVACY.md`; `tests/test_qa_airgap_bypass_attempts.py`; `tests/test_egress_docs.py` | **W1.** README scopes `requests` (new sessions) and `urllib.request` (the default opener); PRIVACY gets the gap bullet for a session or adapter created before the guard and for `build_opener()`. | BY-5, a documented-gap tripwire for the three shapes (a pre-install session; a pre-install adapter class mounted after install; `build_opener()`), in the style of BY-2. | `d1383674` |
| 6 | `docs/PRIVACY.md`; `README.md`; `src/arail/router/core.py` (comment); `tests/test_egress_docs.py` | **W2.** "Other processes" names the lab's own Python subprocesses (the goal parser's, the AirLLM worker) and the `./arailctl` commands that do not run a guarded process; README's parenthetical adds other processes; the router comment says the guard blocks the call only in a process that installed it. | DOC-9 extended: both subprocess modules exist, neither installs the guard, the bullet names both. | `d6271948` |
| 7 | `docs/PRIVACY.md`; `tests/test_egress_docs.py` | **B1, docs.** PRIVACY says plainly that with an incomplete guard the portal and the agents keep running with that library unguarded and only Model Forge's worker stops; and what the modal shows. | The decision is pinned per caller: portal and loader continue and warn, the worker exits 1, the doctor degrades (DR-3); a test that PRIVACY states it. | `b2fdb908` |
| 8 | `docs/PRIVACY.md`; `tests/test_egress_docs.py` | **N1.** One sentence: the local-address check is Python's `ipaddress` private set (documentation, benchmarking and reserved ranges, IPv6 unique-local). | `is_local_ip` on one address of each range named; `100.64.0.1` and `8.8.8.8` refused. | `192e616e` |
| 9 | `.github/workflows/egress-guard.yml` | **W5.** `-rsP` on both pytest lines (the evidence tables reach the log of a green run); the SDK step prints the `anthropic`, `openai` and `httpx2` versions it installed. | CI-5 and CI-6 stay green; actionlint; all five legs through `emulate.py`. | `bf80d184` |
| 10 | `sprints/2026-11-01-arail-egress-httpx2/BUILD_LOG.md` | Pass 3 results and final state. | none (docs) | the orchestrator |

### Verification protocol (REVIEW.md item 7)

Per venv, with `ARAIL_TEST_EXPECT_HTTPX_FAMILY` set: the 13 `EGRESS_TESTS` files in ci, lock, both and py310 (one pytest process per file, one at a time); leg 5's negative control in neither; `tests/test_egress_sdk_clients.py` in `ci-sdk`; `tests/test_queuellm_rename_frozen_surface.py` against the failure text on `origin/main` (it fails 2 cases there; the criterion is that its text does not change); mutants R5 and R6 in a scratch export of the final head (never in the worktree); all five legs through `emulate.py` once the workflow edit is in; `actionlint` on the workflow. Every number recorded gets what it means, the bar and why, what out of range means, and where a failure lives. `git status` is clean between commits.

### Execution

Each step: the test first where the failure mode is new, run against the unmodified source (the reds are listed per step); then the change; then the files the commit touches and the whole 13-file list in ci, lock, both and py310, the neither venv, the neighbours of the change against the orchestrator's untouched-main baseline (`results/full-before2-{ci,lock}`, post-#211, 511 files, failing ids compared file by file), and the frozen-surface text against `origin/main`. `git status` was clean after every commit. Counts below are cases in the ci venv unless a venv is named; the 13-file total in ci went 322 (after step 1), 330, 333, 338, 343, 347, 349, 361, 362 (after step 9), against 320 in the review (333 is derived: step 3 ran only the docs file, 28 to 31 cases; the others are measured).

#### Step 1: W4, a broken install that raises ModuleNotFoundError. Commit `81548cd0`

Tests only, no source change. EG-A3 (in process) now fails the import three ways: lookup, `RuntimeError`, and a real `ModuleNotFoundError` (the simulated library runs `import <a module that does not exist>`, so the exception is Python's own, not a hand-built one); FR-4 (fresh interpreter) does `RuntimeError` and `ModuleNotFoundError`. EG-A3 also asserts the operator is told what is missing.

- **Can it fail?** Mutant R5 (`except ImportError: ... "absent"`) went from 0 new failing ids to **2**: EG-A3 `[ModuleNotFoundError]` and FR-4 `[ModuleNotFoundError]`.
- Pass-before: the new cases pass on the real source (it was right; the tests were missing), so the proof is the mutant, not a red run.

#### Step 2: B1, code. Commit `c14b2dd4`

`portal/app.py`: the install block is `_install_egress_guard()`, called first in `_startup()` (the `try` and the two lines R6 mutates are unchanged, so the reviewer's anchor applies as written). `/api/airgap/status` builds the airgapped description from one `uncovered()` snapshot: with an unguarded library it says `Cloud-provider APIs are blocked, EXCEPT requests made through <libs>: the guard could not patch it|them, so those requests are NOT blocked.` in the place of the old sentence; the rest of the text, and the hybrid text, are unchanged. `nav.js`: the pill is `warn` unless the mode is airgapped and the guard is whole; the known-gaps `<details>` (given an id) is opened; entries still go in with `textContent`. The modal's static paragraph now reads "(...built on them), unless the list above names one of them as a library the guard could not patch."

- **Red first** (ci, old source, 28 tests in `tests/test_egress_docs.py`): 20 passed, **8 failed**: the description (2), the new function (3: the start-up warning, the silent whole guard, the PRIVACY quote), the wiring (the first call in `_startup` was `_egress.install_guard()`), `nav.js` (does not read `guard_installed`), the modal paragraph.
- **Can it fail?** R6 (the reviewer's own text, applied unchanged to the refactored function): 0 new failing ids before, **2** now (the behavioural test and the PRIVACY quote). X3 (description left unqualified) 2; X4 (pill from the mode alone) 1; X5 (list not opened) 1; X6 (a call before the guard install) 1.
- **nav.js under node** (scratch harness with a DOM stub, `pass3/nav_harness.js`, not committed; CI has no node test infrastructure): the old file fails **5 of 14** checks (pill stays `ok`, no tooltip, list closed; and after a flip hybrid to airgapped the pill turns green again, on success and on a failed flip); the new file passes **14 of 14**. The commit message of `c14b2dd4` says "4 of 14"; that is a counting slip in the message, the run says 5.
- Beyond the plan row: reading `nav.js` showed four more places that paint the pill from the mode alone (the optimistic flip, the confirmed flip, and two reverts). Left alone, a flip to airgapped would have turned the pill green again with an incomplete guard. All five paints now go through one helper, `pillClass(mode)`, which remembers `guard_installed` from the last status fetch. Also: a pill tooltip, and the `get_airgap_status` docstring.
- Neighbours: 44 files (everything that reads the endpoint, the modal, `nav.js`, `_startup`, or `install_guard`) fail the same ids as main in ci and in lock. The token-compliance ratchet fails on main with the same text (`static/nav.js: color_literals 1 > baseline 0` is already there).

#### Step 3: N2, the Model Forge worker. Commit `dc6a246f`

`nucleus/worker.py`: `main()` catches `EgressGuardIncomplete`, writes `worker: <message>` to stderr, returns 1; the module docstring says the worker is the one caller that stops. Anything else still propagates.

- **Red first**: the old worker let the exception escape (2 failures, airgapped and hybrid); the whole-guard test passes before and after (it pins that the guard is already in when the phase begins).
- **Run as a process** (`python -m arail.nucleus.worker PA build-1`, `httpx2.AsyncHTTPTransport` deleted first, ci venv): exit status **1** in airgapped and in hybrid, no traceback. A real process prints two lines, not one: the guard's own `_log.error` line reaches stderr through logging's last-resort handler (the worker configures no logging), then the worker's `worker: ...` line. The test, which runs in pytest's captured logging, sees the worker's one line.
- **Can it fail?** X7 (exception escapes) 2; X8 (swallowed, the phase would run) 2.
- Neighbours: the 37 `tests/nucleus/` files (including the real end-to-end worker test in `test_phases.py`) fail the same ids as main in ci and in lock.

#### Step 4: W3, a library installed after the guard. Commit `a71bc309`

`egress.py`: the `coverage()` and `_install_httpx_family_guard()` docstrings no longer say "absent: nothing in this process can use it" (true only when the guard ran); `install_guard()`'s docstring says it looks once. `docs/PRIVACY.md`: the doctor sentence is as of when that process started, and a library installed while the lab runs is not guarded until the lab restarts. `plugins/manager.py`: the success line is `Plugin '<name>' installed successfully. Restart the lab so the egress guard covers any HTTP library it added.` (message text only; nothing else in that path changed).

- **BY-6** (new, `TestDocumentedGaps`): a documented-gap tripwire. The library is hidden (`sys.modules[lib] = None`) while the guard installs (`coverage()[lib] == "absent"`), then made visible; a second `install_guard()` is a no-op; the library's transport is not guarded and a request ends in the library's own `ConnectError`, not `EgressBlocked`, with no audit line. It passes before and after (it pins today's behaviour); the proof it can fail is mutant X10 (`install_guard` looks again on every call): **1** new failing id, BY-6.
- **Red first**: the installer's line, the PRIVACY sentence, and the two docstrings (4 failed); X9 (the installer's line loses "restart") 1.

#### Step 5: W1, README and PRIVACY scope. Commit `d1383674`

README: `wraps `requests` (new sessions), `urllib.request` (the default opener) and every client of `httpx` and its successor `httpx2`, such as ...` (the reviewer's wording). PRIVACY: a gap bullet for a session or adapter created before the guard and for `build_opener()`; the httpx family has no such gap.

- **BY-5** (new): three shapes, each past the guard on purpose: a `Session()` built before `install_guard()`; a session built after it that mounts the `HTTPAdapter` class held from before it; `urllib.request.build_opener().open(...)`. Each ends in the library's own connection error (`.invalid`, made unresolvable at `getaddrinfo`), never `EgressBlocked`, with no audit line.
- **Can it fail?** X1 (the stock adapter's `send` is patched too) turns the two `requests` shapes red; X2 (the stock `HTTPHandler.http_open` is patched too) turns the opener shape red; X14 (README claims "everything built on them" again) 1. The two doc pins were red against the old text first.
- Neighbours (10 doc-reading files) identical to main in ci and lock.

#### Step 6: W2, the processes the guard does not reach. Commit `d6271948`

PRIVACY "Other processes": "Everything else runs outside it: setup's and upgrade's model downloads, Docker containers such as Open Notebook's, opencode, the lab's own Python subprocesses (the goal parser's model subprocess and the AirLLM worker), and the Python behind every other `./arailctl` command (apart from `start`, which runs the portal)." README's parenthetical adds "other processes such as setup's and upgrade's model downloads". `router/core.py` (comment only): the guard blocks the eventual call "only in a process that installed it (the portal does; the goal parser's model subprocess does not, so there this refusal is the only line)".

- Delta from the review's text: the review said "every other `./arailctl` command". `./arailctl start` runs the portal, which is guarded, so the sentence needed the exception; `nucleus` verbs spawn the guarded worker, but the `nucleus` CLI process itself is not guarded, which "Model Forge's worker" in the guarded list already says.
- **Tests** (red against the old text first): for each of the two subprocesses, the spawn site still names the module, the module exists, contains no `install_guard`, is not one of DOC-9's four sites, importing it in a fresh interpreter leaves the guard uninstalled (the AirLLM worker takes over `sys.stdout` on import, so the child prints through the saved stream), and the bullet names it; the "every other `./arailctl` command" sentence; README's clause; the router comment. **X15** (the goal parser's subprocess calls `install_guard()`) turns **2** red.

#### Step 7: B1, docs. Commit `b2fdb908`

PRIVACY, in the paragraph on a library the guard cannot patch: where the lab says so (the activity log, the doctor, and the modal: its pill is a warning, its known-gaps list opens with the library in it, its description says requests made through that library are NOT blocked), then the decision verbatim ("With an incomplete guard the portal and the agent loader keep serving with that library unguarded; only Model Forge's worker exits (with a message that names the library)"), then the remedy (a supported version, `httpx2<3` or `httpx<1`, or uninstall, and restart the lab; until then airgapped mode does not hold for it).

- Tests: a pin that PRIVACY states each sentence (red first; X13, "the portal stops too", 1), and the loader's half of the decision (`load_all` logs `Egress guard install failed in load_all: ...` and returns; seeding and discovery faked). The loader test passes before and after (it pins today's behaviour); X11 (the loader stops logging) 1 and X12 (the loader re-raises) 1 turn it red. With the portal (step 2), the worker (step 3) and the doctor (DR-3) every caller's behaviour is now pinned, so TD56 (fail closed) would change tests, not silently the docs.

#### Step 8: N1, the private ranges. Commit `192e616e`

One bullet: "Or another range Python's `ipaddress` treats as private (documentation, benchmarking and reserved ranges, IPv6 unique-local): the guard uses its `is_private` test as it is." Checked first with `airgap.is_local_ip` on Python 3.10.22 and 3.11.17: `192.0.2.7`, `198.51.100.7`, `203.0.113.7`, `198.18.0.1`, `240.0.0.1`, `0.0.0.0`, `192.0.0.8`, `fc00::1` and `2001:db8::1` are local; `100.64.0.1` and `8.8.8.8` are refused.

- Tests: one address per range named is local (7 cases), a carrier-grade-NAT address and three public ones are refused (4), and the sentence is there (red first). **X18** (the documentation ranges are refused) turns the range cases red (5 of its 11 failing ids; the other 6 are collateral, because the mutant is crude and also refuses `192.168.x`).

#### Step 9: W5, a green run shows its evidence. Commits `bf80d184` and `55107aeb`

`bf80d184`: both pytest lines: `-rs` became `-rsP` (not an extra `-rP`: pytest keeps only the last `-r`, so `-rs -rP` would have dropped the skip names). The SDK step prints the `anthropic`, `openai` and `httpx2` versions between the install and the tests. Nothing else in the workflow changed. A new CI-6 test pins both (red against the old workflow first); X16 (`-rs` back) 1, X17 (no version print) 1. `actionlint` 1.7.12 with shellcheck 0.11.0 exits 0 on the workflow and on every workflow in the repo; removing the existing `SC2086` directive from a scratch copy makes it report two issues, so the check is live. In the emulated leg 1: the SDK step's log now starts with `anthropic 2.3.0`, `openai 3.42.1`, `httpx2 2.16.0`; the egress step's log has the two EVIDENCE tables (FR-1 for httpx2 2.16.0, FR-5 for huggingface_hub 2.4.0), 148 lines in all.

`55107aeb` (the follow-up, found by reading that first emulated green log): `-rP` shows more than the evidence tables. It also shows the captured **log and stderr** of passing tests. The first green leg-1 log carried: 19 `ERROR` and 2 `WARNING` log lines (the guard logs at ERROR when incomplete, and the tests simulate that on purpose); 3 `[arail.egress] record failed` stderr lines (the tests of an unwritable audit log) and 1 lance `WARN` from an unrelated test; and, the one that matters, the checker's own failure verdict, `NO TESTS from ...` and `egress ci check (no-skips): 4 finding(s); this leg is red.`, because a unit test called the checker three times and read the output of only the first. A line saying "this leg is red" in a green log is what the "runs must teach" rule exists to prevent.

- Fixed at the source: that test reads and asserts every call's output (it now also asserts what the missing-file and the empty-run messages say), and an autouse fixture in `tests/test_egress_ci_check.py` fails any test in that file that leaves checker output unread. **X20** (the fix reverted) is caught by that fixture. After the fix, the PASSES section of leg 1's egress log holds no verdict-like line (`this leg is red`, `NO TESTS`, `SKIPPED`, `FAILED`, `UNPROVEN`, `Traceback`: none); what remains is the 21 ERROR/WARNING log lines, the 3 `record failed` lines and the lance warning, which the log now explains.
- The explanation sits where the reader meets the output: one `echo` before the pytest line says that `-rP` also shows the captured log and stderr of passing tests and what tests of failure paths put there on purpose. The CI-6 pin requires it (**X19**, the note removed, is caught). `--show-capture=stdout` would remove the noise but also hide the log of FAILING tests, which is what a red run needs, so it was not used. The review's expectation that `-rP` prints only the evidence tables was slightly off; if the noise is unwanted, the cleaner fix is to have the evidence written to a file and printed after the run, which is new work.

### Deltas from the plan

1. **`nav.js` helper (step 2).** The plan says "the pill is `warn` when `guard_installed` is false". The file paints the pill in five places, not one; all five now go through `pillClass()`. Without that, flipping the mode in the modal turned the pill green with an incomplete guard.
2. **Three docstrings, not two (step 4).** The review names `egress.py:488-489` and `:574`; `install_guard()`'s own docstring is where the short-circuit lives, so it gets one sentence.
3. **W2's "every other `./arailctl` command"** got an exception for `start` (see step 6).
4. **W5 is `-rsP`**, not an added `-rP` (pytest keeps only the last `-r`).
5. **More tests than the review asked for**, each pinning a sentence that a docs or UI change now makes: step 2 (hybrid left alone, every library named, the wiring of `_startup`, the modal paragraph), step 3 (the whole guard still starts the phase), step 4 (BY-6, the installer's line, the docstrings), step 5 (two doc pins), step 6 (the subprocess tie, the `./arailctl` sentence, the router comment), step 7 (the PRIVACY pin and the loader), step 8 (the range cases), step 9 (the CI-6 pin).
6. **No new test file, no change to `tests/egress_ci_check.py`.** Every test is in a file already in `EGRESS_TESTS`; the only edit to `tests/test_egress_ci_check.py` is one added CI-6 test. CI-5 and the other CI-6 tests are unchanged and green. The workflow differs only by W5.
7. **Not written:** no technical-debt numbers in README or PRIVACY (user-facing); the "no supported release is in that state today" sentence that the review's rulings support was left out of PRIVACY, because it would age; the extra N1 clauses (carrier-grade NAT, "none of them is routed") were written and then cut back to the reviewer's one sentence.
8. **W5 has a follow-up commit (`55107aeb`)** for what `-rP` really prints (step 9). It stays inside W5's scope: the same two pytest lines' output, one `echo`, and one existing test file.
9. **The commit message of `c14b2dd4`** says the old `nav.js` fails 4 of 14 node checks; it is 5 of 14 (see step 2). History was not rewritten for a counting slip in a message.

### Findings and where each is fixed

| REVIEW.md | Fix | Commit | Shown by |
|---|---|---|---|
| B1 (code) | `_install_egress_guard()`; the airgapped description names the unguarded library; the pill is `warn` and the list opens; the paragraph is qualified | `c14b2dd4` | R6 red (2); X3 to X6 red; node harness 5 red to 14 of 14 green |
| B1 (docs, decision) | PRIVACY states what the lab does and does not do | `b2fdb908` | PRIVACY pin (X13); the four callers' behaviours pinned (X11, X12, X7, X8, R6) |
| W1 | README scope; PRIVACY gap bullet; BY-5 | `d1383674` | X1, X2, X14 |
| W2 | PRIVACY "Other processes"; README parenthetical; router comment | `d6271948` | X15; the fresh-interpreter ties |
| W3 | three docstrings; PRIVACY; the installer's line; BY-6 | `a71bc309` | X10, X9 |
| W4 | EG-A3 and FR-4 over `RuntimeError` and `ModuleNotFoundError` | `81548cd0` | R5 red (2) |
| W5 | `-rsP` on both pytest lines; SDK versions printed; a note in the log; no test prints a verdict into a green log | `bf80d184`, `55107aeb` | X16, X17, X19, X20; CI-6 pin; all five legs emulated |
| N1 | one PRIVACY bullet | `192e616e` | the range cases; X18 |
| N2 | the worker catches `EgressGuardIncomplete` | `dc6a246f` | X7, X8; run as a process |
| N3, N5, N7 | skipped by decision | | |
| N4 | no action (the RLock stays); R4 still survives, by design | | |
| N6, B1 item 5, W2 and W3 "file TD..." | TD56 to TD59 and AF-3's sites: the orchestrator's commit 8 | | |

### Verification (REVIEW.md item 7), tree clean

V1 to V6 were measured on `bf80d184` (every code, test and doc change of the pass). The two commits after it change only `.github/workflows/egress-guard.yml` and `tests/test_egress_ci_check.py` (still 44 cases); R5, R6 and the W5 mutants were re-run on the final head, and the emulation (V7) re-ran all 13 files in all five legs on it.

Every table says what the number means, the bar and why, what out of range means, and where a failure lives. Environment: scratch venvs (editable installs of this worktree), scratch HOME and TMPDIR, scrubbed environment, `CI=true`, one pytest process per file and one at a time, each venv declaring its leg through `ARAIL_TEST_EXPECT_HTTPX_FAMILY`. Nothing bound `127.0.0.1:8089`, no full suite, no `docker`.

#### V1. The 13 `EGRESS_TESTS` files in ci, lock, both and py310 (passed; failed and skipped are 0 in every cell)

| File | review, ci | ci (httpx2 2.16.0) | lock (httpx 0.28.1) | both | py310 (httpx2 2.14.0) |
|---|---|---|---|---|---|
| `test_airgap_helpers.py` | 46 | 46 | 46 | 46 | 46 |
| `test_airgap_modal_dom.py` | 6 | 6 | 6 | 6 | 6 |
| `test_egress_bootstrap.py` | 22 | 22 | 22 | 22 | 22 |
| `test_egress_ci_check.py` | 43 | 44 | 44 | 44 | 44 |
| `test_egress_docs.py` | 20 | 55 | 55 | 63 | 55 |
| `test_egress_guard.py` | 18 | 18 | 18 | 18 | 18 |
| `test_egress_httpx.py` | 63 | 64 | 64 | 90 | 64 |
| `test_egress_httpx_fresh.py` | 6 | 7 | 7 | 10 | 7 |
| `test_httpx_family.py` | 17 | 17 | 17 | 20 | 17 |
| `test_qa_airgap_bypass_attempts.py` | 20 | 24 | 24 | 28 | 24 |
| `test_qa_airgap_happy_setup.py` | 17 | 17 | 17 | 17 | 17 |
| `test_qa_airgap_ipv6_edge_cases.py` | 14 | 14 | 14 | 14 | 14 |
| `test_qa_tier1_egress_guard.py` | 28 | 28 | 28 | 28 | 28 |
| **Total** | **320** | **362** | **362** | **406** | **362** |

- *Means:* the cases each leg's job runs, in that leg's library state. The review measured 320 / 320 / 320 / 356 at `98e9e98a`; the difference is this pass's new cases (+42 in ci: W4 +2, B1 +8, N2 +3, W3 +5, W1 +5, W2 +4, B1 docs +2, N1 +12, W5 +1).
- *Bar and why:* 0 failed and 0 skipped in every cell, every file at least 1 case. A skip is how TD50 stayed hidden, and the workflow's checker fails the leg on any of these.
- *Out of range:* a failure names a test; a skip means a test became conditional; a file with 0 cases means a path typo or a collection change.
- *Where:* the file named in the row; `.github/workflows/egress-guard.yml` and `tests/egress_ci_check.py` for how the leg judges it.

#### V2. The neither venv (leg 5's state): the negative control

The whole list in the neither venv, declared `none`: 314 passed, **2 failed**, 45 skipped. The two failures are the ones this venv is for: EG-0, the sentinel (`UNPROVEN`), which must fail, and FR-5, huggingface_hub without its HTTP library, which leg 5 never runs. The 45 skips are the `[lib]` tests having an empty parameter set. Leg 5's own command is in V6.

- *Means:* with no httpx-family library installed the proof must refuse to pass. *Bar and why:* EG-0 red with its message, EG-0b green; the vacuous pass on `main` (79 passed, 2 skipped, exit 0) is gone. *Out of range:* EG-0 green means the sentinel shares the guard's lookup; any other red means this pass's tests assumed a library. *Where:* `tests/test_egress_httpx.py`.

#### V3. `tests/test_egress_sdk_clients.py` in an SDK venv (`ci-sdk`: httpx2 only, plus anthropic and openai, both declared)

3 passed, 0 failed, 0 skipped, 4.4 s.

- *Means:* the SDKs' default clients are refused in airgapped mode. *Bar and why:* `EgressBlocked` in the chain, no network-error type, one audit line at `max_retries=0`; skipped is red when the SDK is declared. *Out of range:* `ConnectError` or `gaierror` in a chain means an SDK's default client left the guarded transports (an architect decision, not a test edit). *Where:* SDK-1.

#### V4. The frozen-surface failure text against `origin/main` (`de1ce285`)

`tests/test_queuellm_rename_frozen_surface.py` fails 2 and passes 3 on `origin/main` and here. The 13 `E ` lines are **identical** in ci, lock, both, py310 and neither (the same four drift lines: `pyproject.toml | 5 +++++`, `scripts/build-aerollm.sh` losing `aerollm_version` x2, and `src/arail/portal/app.py` losing `AEROLLM_MODEL` x3 and `AeroLLMBackend` x1; the counts for `app.py` did not grow).

- *Means:* the two pre-existing failures are not hiding a new one. *Bar and why:* identical text, because the test is red on main and a changed line would be a frozen token lost or a frozen path touched by this pass. *Out of range:* a new or changed line names the file and token. *Where:* the test's `E ` lines.

#### V5. Order independence (E13): the 13 files in one process, forward and reversed

ci 362 / 362, lock 362 / 362, both 406 / 406, py310 362 / 362; 0 failed, 0 skipped.

- *Means:* patch state does not leak between files. *Bar and why:* the same counts both ways, because the suite shares one process. *Out of range:* a difference names a file whose autouse reset is incomplete. *Where:* the file whose count moved.

#### V6. Mutants (scratch export of `bf80d184`; R5, R6 and the W5 rows re-run on the final head; 13 files, ci venv, control 362 cases and 0 failing ids; a mutant is caught when it adds at least one failing id)

| Mutant | New failing ids | Named tests |
|---|---|---|
| **R5** an `ImportError` counts as absent | **2** (was 0) | EG-A3 `[ModuleNotFoundError]`, FR-4 `[ModuleNotFoundError]` |
| **R6** the portal's start-up swallows an incomplete guard | **2** (was 0) | the start-up warning test, the PRIVACY quote test |
| R1 in-process transports patched too | 6 | EG-13, EG-18, EG-19, FR-3, and two structural |
| R2 audit line records the whole URL | 9 | EG-1, EG-2, EG-9, EG-10, FR-5, `TestAuditLog` |
| R3 refusal message carries the whole URL | 1 | EG-1 |
| R4 no lock in `install_guard` | 0 (survives, by design) | N4: nothing observable breaks without it |
| R7 raise only when airgapped | 3 (was 1) | EG-20 `[hybrid]`, the hybrid description, the worker `[hybrid]` |
| R8 check after the library's own I/O | 19 | EG-1, EG-11, EG-14, FR-2, and others |
| X1 stock `requests` adapter's `send` patched too (BY-5) | 2 | BY-5 session shapes |
| X2 stock urllib `HTTPHandler` patched too (BY-5) | 1 | BY-5 opener shape |
| X3 description left unqualified (B1) | 2 | DOC-5 description tests |
| X4 pill from the mode alone (B1) | 1 | DOC-6 `nav.js` |
| X5 known-gaps list not opened (B1) | 1 | DOC-6 `nav.js` |
| X6 a call before the guard in `_startup` (B1) | 1 | DOC-5 wiring |
| X7 worker lets the exception escape (N2) | 2 | worker test, airgapped and hybrid |
| X8 worker swallows it and runs the phase (N2) | 2 | worker test, airgapped and hybrid |
| X9 installer's line loses "restart" (W3) | 1 | the installer test |
| X10 `install_guard` looks again on every call (W3) | 1 | BY-6 |
| X11 loader stops logging (B1 decision) | 1 | the loader test |
| X12 loader re-raises (B1 decision) | 1 | the loader test |
| X13 PRIVACY says the portal stops too (B1 docs) | 1 | the PRIVACY pin |
| X14 README claims "everything built on them" (W1) | 1 | the README pin |
| X15 goal parser's subprocess installs the guard (W2) | 2 | the subprocess tie, DOC-9 |
| X16 workflow loses `-rP` (W5) | 1 | the CI-6 pin |
| X17 SDK step stops printing versions (W5) | 1 | the CI-6 pin |
| X18 documentation ranges refused (N1) | 11 | the range cases (5) and RFC1918 tests (collateral) |
| X19 the egress step stops saying what the ERROR lines are (W5) | 1 | the CI-6 pin |
| X20 a checker test leaves the checker's verdict unread (W5) | 1 | the autouse fixture, on that test |

- *Means:* whether a mitigation has a test that fails without it. *Bar and why:* at least 1 new failing id per mutant, except R4, which the review accepts. R5 and R6 are the two the review required to go red. *Out of range:* a survivor is a mitigation nothing pins. *Where:* the test named in the row.

#### V7. All five legs through `emulate.py`, on the final head `55107aeb` (the workflow's own `run:` text; tree status `''` before and after each leg)

| Leg | Library state (venv, Python) | Egress step | Checker | SDK step | Leg |
|---|---|---|---|---|---|
| 1 httpx2 only (pip, floating) | httpx2 2.16.0, hf 2.4.0, starlette 1.10.0 (ci, 3.11.17) | 362 passed, exit 0 | `ok - 13 files, 362 cases, none skipped, failed or missing` | `ci-sdk`: 3 passed; the step printed `anthropic 2.3.0`, `openai 3.42.1`, `httpx2 2.16.0`; checker `ok - 1 files, 3 cases` | green |
| 2 httpx2 only, Python 3.10 | httpx2 2.14.0, hf 2.4.0, starlette 1.7.0 (py310, 3.10.22; the checker also runs under 3.10) | 362 passed, exit 0 | ok, 13 files, 362 cases | not in this leg | green |
| 3 httpx only (uv.lock) | httpx 0.28.1, hf 1.25.1, starlette 1.3.1 (lock, 3.11.17) | 362 passed, exit 0 | ok, 13 files, 362 cases | not in this leg | green |
| 4 both | httpx 0.28.1 and httpx2 2.16.0 (both, 3.11.17) | 406 passed, exit 0 | ok, 13 files, 406 cases | not in this leg | green |
| 5 neither (negative control) | no httpx, httpx2, httpcore, httpcore2 (neither, 3.11.17) | not run (the leg's `if:`) | `ok - the sentinel failed with UNPROVEN, as it must with neither library, and the declaration passed` | not in this leg | green *because* the sentinel failed: pytest `1 failed, 1 passed` |

- *Means:* each leg's job, run as GitHub would run its test steps (the install steps are skipped: the venvs are prebuilt, see "Not verified here"). *Bar and why:* legs 1 to 4 pass every case with none skipped and every file counted; leg 5 is green only because its sentinel failed with `UNPROVEN` and its declaration passed, so the proof can fail. *Out of range:* a skip or a file with 0 cases fails the checker; a passing sentinel in leg 5 fails the control. *Where:* `.github/workflows/egress-guard.yml`; `tests/egress_ci_check.py`.
- What the green logs now show (leg 1): the SDK step's versions; two EVIDENCE tables (the probe, huggingface_hub); the note; 21 ERROR/WARNING log lines and 4 `record failed` mentions (3 stderr lines and the note itself); no line that reads like a verdict (`this leg is red`, `NO TESTS`, `SKIPPED`, `UNPROVEN` outside leg 5, `Traceback`).
- Order of events worth knowing: my first emulation of these legs passed a relative `--out`, so the junit files were written into the worktree (`results/`, untracked). It was removed and the legs were re-run with absolute paths (the `''`/`''` above). The emulation was then re-run after each of the two W5 commits; the table is the last run.

#### V8. `actionlint` on the workflow

`actionlint` 1.7.12 with shellcheck 0.11.0 on `PATH`: exit 0 on `.github/workflows/egress-guard.yml` and on every workflow in the repo. Negative control: with the `SC2086` directives deleted from a scratch copy, it reports two `SC2086` issues, so shellcheck is active. *Means:* the workflow's expressions, matrix, `if:` conditions and every `run:` script are well-formed. *Bar and why:* exit 0, because a malformed workflow fails on GitHub with no test having run. *Out of range:* a finding names the line. *Where:* the workflow.

#### V9. Neighbours of every change, against the untouched-main baseline

The standing 44 files (everything that reads the status endpoint, the modal, `nav.js`, `_startup`, `install_guard`, README or PRIVACY), the 37 `tests/nucleus/` files and the loader test, in ci and in lock, compared file by file with `results/full-before2-{ci,lock}` (the orchestrator's post-#211 full-suite baseline on `origin/main`):

| Venv | Files | Cases | Failing ids (all also failing on main) | Files whose failing ids differ from main |
|---|---|---|---|---|
| ci | 82 | 1229 | 21 (in 6 files) | 0  |
| lock | 82 | 1229 | 20 (in 5 files) | 0  |

- *Means:* no test that this pass did not write changed its verdict. *Bar and why:* the same failing ids as main, because this pass changes the guard's callers (the portal's start-up, the worker, the installer) and a regression elsewhere would show here. *Out of range:* an id that fails only here is a regression until a serial re-run on both trees says otherwise. *Where:* the file named. The token-compliance ratchet fails on main and here with the same text (`static/nav.js: color_literals 1 > baseline 0` is already in it).

### Doc claims checked (pass 3)

Every factual sentence added to README, PRIVACY, the modal, a docstring, a comment or a user-visible string in this pass, with how it was checked. "Run" means run in this session (probes are scratch scripts under `pass3/`, not committed); "test" names the committed test that now ties the sentence to the code.

| # | Where | Claim | Verdict | Evidence |
|---|---|---|---|---|
| 1 | README | The guard wraps `requests` (new sessions) and `urllib.request` (the default opener) | true | BY-5 (3 shapes); `TestPreGuardSession`; `egress.install_guard` swaps `requests.adapters.HTTPAdapter` and installs an opener; README pin |
| 2 | README | ...and every client of `httpx` and `httpx2` | true | EG-3 and FR-1 (clients built before install), FR-2, SDK-1, FR-5 |
| 3 | PRIVACY | A session or adapter created before the guard, and a `build_opener()` opener, are not reached; the httpx family has no such gap | true | BY-5 (run, three cases, no audit line); the class-level patch (EG-3) |
| 4 | PRIVACY, docstrings, installer line | The guard looks once at start; a library installed while the lab runs is not guarded until it restarts | true | BY-6 (run); `portal/app.py:5093` calls `plugin_mgr.install`, which runs `pip install -r` with `sys.executable` (`plugins/manager.py:78-81`) into the running interpreter |
| 5 | PRIVACY | `./arailctl doctor` prints which of the four the guard covered when that process started | true | `doctor._guard_summary` (DR-2); the doctor is its own process |
| 6 | PRIVACY | The portal's activity log records "Egress guard install failed" | true | run: DOC-5 calls the start-up and reads the emitted line |
| 7 | PRIVACY | `./arailctl doctor` fails its egress check | true | DR-3 (pass 2) |
| 8 | PRIVACY | The modal shows the pill as a warning, opens the known-gaps list with the library in it, and its description says requests made through it are NOT blocked | true | run: `nav.js` under node with a DOM stub, 14 of 14 checks (the old file fails 5); DOC-5 for the description; not run: a browser |
| 9 | PRIVACY | With an incomplete guard the portal and the agent loader keep serving with that library unguarded; only Model Forge's worker exits (with a message that names the library) | true | tests: portal (DOC-5), loader (new), worker (new); the worker run as a process exits 1 in airgapped and hybrid |
| 10 | PRIVACY | Until a supported version (`httpx2<3`, `httpx<1`) is installed, or the library is uninstalled, and the lab is restarted, airgapped mode does not hold for it | true | the `EgressGuardIncomplete` message (EG-A2) says the same fix; the review's probe: an async httpx2 request reached the network layer in that state |
| 11 | PRIVACY | The goal parser's model subprocess and the AirLLM worker run outside the guard | true for "do not install it"; the worker's hub fallback is read, not run | run: importing either module in a fresh interpreter leaves `_INSTALLED` False and `coverage()` empty; AST: no `install_guard()` in either; `skills/goal_parser/__init__.py:237` and `router/backends.py:1091` spawn them; `router/airllm_worker.py:74` falls back to the repo id when the local model directory is missing (read) |
| 12 | PRIVACY | The Python behind every other `./arailctl` command (apart from `start`) runs outside the guard | true | the four `install_guard()` sites (DOC-9) and one caller of `load_all()` (`portal/app.py`, grep); `arailctl:1058` and following run `python -m arail.*` entry points that are not among them |
| 13 | README | Other processes, such as setup's and upgrade's model downloads, are not covered | true | `scripts/setup.sh:1939` and `scripts/upgrade.sh:177` run `python3 -c "from huggingface_hub import snapshot_download..."` in separate interpreters |
| 14 | `router/core.py` comment | The guard blocks the call only in a process that installed it; the goal parser's subprocess does not, so there the router's refusal is the only line | true | row 11; the router refuses cloud backends while airgapped before any call (pre-existing, unchanged) |
| 15 | PRIVACY | The local-address check also passes documentation, benchmarking and reserved ranges and IPv6 unique-local, because it uses `is_private` | true | run on 3.10.22 and 3.11.17: nine addresses local, `100.64.0.1` and `8.8.8.8` refused; `airgap.is_local_ip` is `is_loopback or is_private or is_link_local`; the range tests |
| 16 | `worker.py` docstring | The parent inherits the worker's stderr | true | `nucleus/phases.py:166` `subprocess.Popen(args, cwd=..., env=env)` with no redirection |
| 17 | `portal/app.py` docstring | `./arailctl doctor` fails its egress check; the modal names the library | true | rows 7 and 8 |
| 18 | `nav.js` tooltip | "The egress guard could not patch every HTTP library -- see below." | true | the string is set only when `guard_installed` is false (node harness) |
| 19 | modal paragraph | The guard catches the four, "unless the list above names one of them as a library the guard could not patch" | true | DOC-5 and the harness: the list gets that entry |

### Architect feedback required

None blocking. Three places where the review's text needed a refinement, applied and logged above rather than escalated: W2's "every other `./arailctl` command" (an exception for `start`, step 6); W5's "`-rP`" (written `-rsP`, step 9); and W5's expectation that `-rP` "prints captured output only for tests that print, which are the evidence tables": it also prints the captured log and stderr of passing tests, and one checker unit test printed a failure verdict into a green log (fixed at the source, pinned, and the log now carries a note; step 9). Whether that remaining noise is acceptable, or the evidence should instead be written to a file and printed after the run, is the architect's or the operator's call. One item for the architect's judgement, not a defect: B1's fix list asks to "show the unguarded entries outside the collapsed `<details>` (or open it)". The description (outside the `<details>`) names each unguarded library and the list opens by itself; I did not add a second element that repeats the entries, because that needs either a new field in the status payload or inline styles (the token-compliance ratchet counts both).

### Observations for the review (not defects)

1. The nav bar's own **Airgapped** badge (`#mode-badge`) still shows the mode only: it reads `/api/system/mode`, which carries no guard state, so it stays "Airgapped" with an incomplete guard. The modal it opens is where the state is. Changing the badge needs a field on that endpoint or a second fetch at load; not in B1's list.
2. After a mode flip in the modal, the description paragraph is not refreshed until the modal is reopened (pre-existing, for every mode). With an incomplete guard the pill stays amber across the flip (step 2), and the next open shows the right description.
3. If `install_guard()` fails for a reason other than an unpatchable family member, `uncovered()` is empty: the pill is `warn` and the list opens, but the description keeps the unqualified sentence. B1 asks for the qualification when `uncovered()` is non-empty; widening it to "the guard is not installed" is a small follow-up the architect may want.
4. A real worker process prints two lines on stderr (the guard's own log line and the worker's), see step 3.
5. Carrier-grade NAT addresses (`100.64.0.0/10`, which some VPNs assign) are refused in airgapped mode, because `ipaddress` does not call them private. PRIVACY now says what is allowed; it does not list this. Pre-existing.
6. In the neither venv the docs file skips 9 cases for an empty parameter set (the `[lib]` tests); leg 5 never runs that file.
7. `-rP` is noisier than the review expected (step 9): a green log carries the 21 ERROR/WARNING log lines and 3 `record failed` lines of tests that provoke them on purpose, now explained by a note and without any verdict-like line. If the operator wants a quieter log, the options are in step 9.
8. The B1 decision text, for the ledger and the PR: **"With an incomplete guard the portal and the agent loader keep serving with that library unguarded; only Model Forge's worker exits."** It is written in `docs/PRIVACY.md` and pinned per caller by tests; TD56 (fail closed) is the operator's call.

### Not verified here

The real GitHub run: the install steps on `ubuntu-latest` with PyPI as of the day (including whether leg 2's `pip` resolves httpx2 2.14.0 on Python 3.10), `uv sync --frozen` there, `setup-python`'s pip cache, the daily schedule, and the first run's wall time. A browser rendering of the modal (the `nav.js` behaviour was run under node with a DOM stub instead). The AirLLM worker's hub fallback (read, not run: `airllm` is not installed). Anything under TD56 to TD59.

## Final state (pass 3)

- **Commits:** `7ce4d2ff` (this pass's plan), `81548cd0` (W4), `c14b2dd4` (B1 code), `dc6a246f` (N2), `a71bc309` (W3), `d1383674` (W1), `d6271948` (W2), `b2fdb908` (B1 docs), `192e616e` (N1), `bf80d184` (W5), `55107aeb` (W5 follow-up), and the commit of this file (the orchestrator records its SHA). Branch `qukaizen/arail-egress-httpx2`, not pushed. `55107aeb` was amended twice before anything referenced it (the leak guard, then its message); no other commit was rewritten.
- **Diff of the code commits** (`98e9e98a..55107aeb`, `sprints/` excluded): 15 files, +769 / -70. Source, docs and workflow: 10 files, +160 / -45 (`portal/app.py` +40/-8, `docs/PRIVACY.md` +32/-9, `nav.js` +22/-5, `egress.py` +16/-9, the workflow +16/-2, `worker.py` +14/-1, `plugins/manager.py` +6/-1, README +6/-5, `router/core.py` +5/-3, the modal +3/-2). Tests: 5 files, +609 / -25 (`test_egress_docs.py` alone +408/-11).
- **Tests now** (13 files, `CI=true`, declared per venv): ci 362, lock 362, both 406, py310 362, all with 0 failed and 0 skipped, against 320 / 320 / 320 / 356 at `98e9e98a`. neither: 314 passed, 2 failed on purpose (EG-0 and FR-5), 45 skipped for empty parameter sets. `ci-sdk`: 3 passed. The frozen-surface text is identical to `origin/main`'s in five venvs. Order independence holds in four.
- **Mutants:** R5 and R6 are red (2 new failing ids each; both were 0). R1, R2, R3, R7 and R8 are caught (R7 by 3, up from 1); R4 survives by the review's own ruling (N4). The 20 mutants written for this pass are all caught.
- **Not touched:** `src/arail/deep_*.py`, `src/dac_world/`, `src/arail/buddy_host/contract/`, `tests/conftest.py`, `pyproject.toml`, `uv.lock`, `CHANGELOG.md`, `NOTICE`, the licences, `SPRINT.md`, `sprints/BACKLOG.md`; no `aerollm` / `AERO_*` name removed or added; the pass-2 wording rule holds (`grep -iw` for the forbidden cost word over the diff, this file included, finds nothing new). The airgapped default and the `allow_egress` ratchet are not relaxed.
- **Not built, by decision:** TD56 (fail closed), TD57 (post-import hook), TD58 (subprocess guarding), TD59 (socket timeout); filing them, and AF-3's three sites, is the orchestrator's commit 8.
- **Environment:** scratch only. New this pass: `pass3/` (harnesses, the mutant runner with the reviewer's R1 to R8 copied verbatim and 20 of mine, the node harness, the drivers), a pristine `origin/main` checkout in `base-wt` (already at `de1ce285`) for the frozen-surface and baseline comparisons. Nothing is created in the worktree except committed files; `git status` is clean.

## Pass 4 (QA fixes)

The fix pass after QA's WEAK_PASS ([TEST_REPORT.md](./TEST_REPORT.md)). The spec is that file ("Findings" and "The exact list the builder must change"), read in the file, not a summary of it. Start: head `15e3abc6`, tree clean, branch `qukaizen/arail-egress-httpx2`, not pushed.

### Scope (set by the orchestrator)

- **In:** QA-W2 (before merge), QA-W1 (docs), the nits N1, N2, N3, N4, N5, N6 and N8 as written in the report, and N7 (carry the guard state to the always-visible nav badge).
- **Out, by decision:** QA-W3 (an `allow_egress` scope surviving a flip to airgapped: the three decision sites in `egress.py` are not changed) and PX-1 (the `innerHTML` rows of the Recent-activity list in `nav.js` are not changed). The orchestrator files both as debt in the BACKLOG commit, with QA's validated fixes recorded there.
- **Not mine:** `SPRINT.md` and `sprints/BACKLOG.md`. The CI workflow is not edited unless a test I touch forces a pin to move; if that happens the log says exactly why.
- **Frozen, untouched:** `src/arail/deep_*.py`, `src/dac_world/`, `src/arail/buddy_host/contract/`, `tests/conftest.py`, `pyproject.toml`, `uv.lock`, `CHANGELOG.md`, `NOTICE`, the licences, any `aerollm` / `AERO_*` name. The airgapped default and the `allow_egress` ratchet are not relaxed. The pass-2 wording rule for cost claims holds.
- **Limits:** no full-suite run, nothing that binds `127.0.0.1:8089`, no `docker`, the home clone untouched. Mutation experiments run on scratch exports, never in the worktree. `git status` is clean between commits.

### Baseline on `15e3abc6` (a scratch export, the 13 `EGRESS_TESTS` files, one pytest process per file, `CI=true`, each venv declaring its leg)

| Venv | Cases | Failed | Skipped |
|---|---|---|---|
| ci (httpx2 2.16.0) | 372 | 0 | 0 |
| lock (httpx 0.28.1) | 372 | 0 | 0 |
| both | 421 | 0 | 0 |
| py310 (httpx2 2.14.0) | 372 | 0 | 0 |
| neither | 370 | 2 (EG-0 and FR-5, on purpose) | 49 (empty parameter sets) |

*Means:* the starting point every later number is compared with; it equals QA's own counts (372 / 372 / 421 / 372). *Bar and why:* 0 failed and 0 skipped in the four library states, because a skip is how TD50 stayed hidden; neither must fail exactly the two tests that need a library. *Out of range:* a difference from QA's counts means the tree is not the one QA tested. *Where:* the per-file table in the scratch results (`b4/results/base-*`).

### Plan

A commit cannot contain its own SHA, so the "Commit ref" column is filled in by the final BUILD_LOG commit (row 12). Tests come first wherever the failure mode is new, and are run against the unmodified source (rows 1, 3, 5 to 8, 11); each row is green on its own in the four library states before it is committed.

| # | Files | Change | Test | Commit ref |
|---|---|---|---|---|
| 0 | `sprints/2026-11-01-arail-egress-httpx2/BUILD_LOG.md` | This plan. | none (docs) | `df90517b` |
| 1 | `src/arail/egress.py` (`_install_httpx_family_guard`, two docstrings); `tests/test_egress_httpx.py`; `tests/test_egress_docs.py` | **QA-W2.** An exception raised by `_patch_family_member` (a module whose attribute lookup raises, the PEP 562 lazy-module shape) is contained as `unguarded: patch raised <Type>: <message>`, exactly as the report shows. The next library is still patched, and the failure reaches `coverage()`, `uncovered()`, `EgressGuardIncomplete` and so every surface the other incomplete states reach. | EG-A8 is QA's proposed test, next to EG-A3. EG-A9 adds either member broken, only the async lookup raising (the sync class is patched and a reset puts it back), and a healthy stub for the other member so "still patched" is not vacuous in a venv with one library. One status-level test (definition qualified, entry listed, one portal warning). **Run on HEAD first: they must be red**; then green. | `b2fb7f3e` |
| 2 | `tests/test_egress_httpx.py` | **QA-N8.** Two comments say a missing httpcore makes `import httpx2` fail. It does not (the import is lazy; the client constructor fails). Reworded. | none (comments); the cases are unchanged | `3cb17af4` |
| 3 | `docs/PRIVACY.md` (enforcement sentence, one gap bullet); `README.md`; `src/arail/airgap.py` (`KNOWN_GAPS`, docstring); `src/arail/portal/templates/_airgap_modal.html`; `tests/test_egress_docs.py`; `tests/test_qa_airgap_bypass_attempts.py` (docstring text only) | **QA-W1.** `urllib.request` is guarded through the opener the guard installs and only through it: `urlopen(url, context=...)` (and, before Python 3.13, `cafile=`, `capath=`, `cadefault=`), `http.client` used directly and a later `install_opener()` skip it. PRIVACY and README scope the claim, `KNOWN_GAPS` gains one entry, the modal paragraph names `http.client`. | DOC pins written first (red on HEAD); DOC-2 gains the word `http.client` for all four surfaces; a tripwire that nothing in `src/arail/` does any of the three things; Q2 (QA's pin) is unchanged. | `66b39468` |
| 4 | `docs/PRIVACY.md`; `tests/test_egress_docs.py`; `tests/test_qa_airgap_bypass_attempts.py` (Q1 docstring) | **QA-N1.** The httpx family has no gap for objects built before the guard, but a reference to the method taken before it (a bound `handle_request`, a subclass that copies it) is outside the class patch. | A PRIVACY pin (red first); Q1 already pins the behaviour. | `61535641` |
| 5 | `docs/PRIVACY.md`; `tests/test_egress_docs.py`; `tests/test_qa_airgap_bypass_attempts.py` (Q3 docstring) | **QA-N2.** A library imported again after the guard ran has new, unpatched classes. | A PRIVACY pin (red first); Q3 already pins the behaviour. | `a64d9f5a` |
| 6 | `docs/PRIVACY.md`; `tests/test_egress_docs.py` | **QA-N3.** Children started with multiprocessing's `spawn` or `forkserver` method do not inherit the guard; a forked child does. | A PRIVACY pin (red first). | `259ff646` |
| 7 | `src/arail/egress.py` (`_incomplete_message`); `docs/PRIVACY.md` (the remedy sentence); `tests/test_egress_httpx.py` (EG-A2); `tests/test_egress_docs.py` (the portal and worker assertions that quote the old words) | **QA-N5.** The remedy no longer says "or uninstall": on a standard install httpx2 is what the Hugging Face downloader and the cloud SDKs run on. It names a command and says to restart. | EG-A2 and the two quoting assertions move in the same commit, written first and red on HEAD. | `4501336a` |
| 8 | `src/arail/nucleus/worker.py`; `tests/test_egress_docs.py` | **QA-N4, worker.** A real worker process printed the message twice (the guard's own log line through logging's last-resort handler, then `worker: ...`). | A fresh-interpreter test that counts the lines (red first); in-process tests cannot see the last-resort handler. | `399e6ff8` |
| 9 | `src/arail/doctor.py`; `tests/test_httpx_family.py` (DR-3) | **QA-N4, doctor.** `egress guard : ? (EgressGuardIncomplete: ...)` becomes `INCOMPLETE — <message>`; a `?` reads as unknown for a state that is known. | DR-3 asserts the new line (red first). | `2eb75e65` |
| 10 | `README.md` (225-228); `docs/the-lab.md` (299-301); `tests/test_egress_docs.py` | **QA-N6.** Two older paragraphs still say the guard covers `requests` and `urllib` and lists three address ranges. Aligned with the README's later paragraph and PRIVACY. | A pin that both name every guarded client and the `ipaddress` rule (red first). | `2e48ad23` |
| 11 | `src/arail/portal/app.py` (`get_mode` only); `src/arail/portal/static/nav.js`; `docs/PRIVACY.md`; `tests/test_egress_docs.py` | **QA-N7.** `GET /api/system/mode` also carries `guard_installed`; the badge reads it, so it cannot say plain "Airgapped" while the guard is incomplete: amber (the existing `hybrid` style), "Airgapped, guard incomplete", a title that points to the Network Policy modal, `textContent` throughout. | The endpoint test (behavioural, red first) and source-level pins of `nav.js` in the style of DOC-6. The badge is also run under node with a DOM stub; that run is recorded here, not committed (no skip is allowed for the thing under test, and CI has no node harness). | `843d25e8` |
| 12 | `sprints/2026-11-01-arail-egress-httpx2/BUILD_LOG.md` | Pass 4 results and final state. | none (docs) | the orchestrator |

### Where the plan differs from the report's text (decided before building, checked again in Execution)

1. **QA-W1, "none in tree".** The report's grep says nothing in `scripts/` uses `http.client`. `scripts/s4_win_checks.py` does (an operator script, loopback only). The new sentences say "nothing in `src/arail/` does any of these today", which a tripwire test checks, instead of "none in tree".
2. **QA-W1, two extra words.** `cadefault=` also builds its own opener and `xmlrpc.client` is built on `http.client`; both were run in this session (ci 3.11.17 and py310 3.10.22) before they are written down.
3. **QA-N5.** The report's remedy is written for httpx2 alone; the message is generic over whichever libraries are unguarded, so each gets its own pin (`httpx2<3`, `httpx<1`, the ranges the old message already named). `docs/PRIVACY.md` carries the same "or uninstall it" advice, so it changes in the same commit: leaving it would keep the advice QA found harmful and break the sentence the earlier doc-claims table ties to the message.
4. **QA-N4, worker.** The report suggests a `NullHandler` with `propagate=False`. A `NullHandler` alone is enough (logging's last-resort handler fires only when no handler is found in the whole hierarchy) and does not hide the record from a root handler someone configures later. It is added around `install_guard()` and removed afterwards, so an in-process caller does not inherit it.
5. **QA-N7.** The badge reuses the `hybrid` amber style rather than adding CSS, as instructed; `guard_installed` is the same field the modal's pill reads, so the two always agree.

### Verification protocol (the orchestrator's list, each result recorded below)

The 13 `EGRESS_TESTS` files in ci, lock, both and py310 with `ARAIL_TEST_EXPECT_HTTPX_FAMILY` set per venv, after every commit and at the end (also neither, whose two failures must stay the two intended ones); leg 5's negative control; `tests/test_egress_sdk_clients.py` in `ci-sdk`; all five legs through `emulate.py`; `tests/test_queuellm_rename_frozen_surface.py` against `origin/main`'s failure text (it fails 2 cases there; the text must not change); QA's `state_status.py lazy-attr` in the `both` venv (httpx must now be `EgressBlocked`, the status qualified); the badge under node with a DOM stub; the doctor and the worker in an incomplete state; mutants for what this pass adds, run on a scratch export; the neighbours of each change against the untouched-main baseline. Every number recorded gets what it means, the bar and why, what out of range means, and where a failure lives.

### Execution

Commits are listed in plan order; every count is cases in the `ci` venv unless a venv is named; "red first" means the test was run against the unmodified source (or docs) before the change. Environment for every number: scratch venvs (editable installs of this worktree), scratch HOME and TMPDIR, scrubbed environment, `CI=true`, one pytest process per file, one at a time within a given tree (several exports were verified concurrently in the background, each its own tree), each venv declaring its leg through `ARAIL_TEST_EXPECT_HTTPX_FAMILY`. Nothing bound `127.0.0.1:8089`, no full suite, no `docker`, the home clone untouched.

**What interrupted this pass, and what it cost.** The pass was cut off twice by usage limits and, between the two, the machine rebooted (2026-11-05 20:14), which cleared `/private/tmp`. The worktree survived (the commits, and the then-uncommitted N5 change, which was verified and committed afterwards); the whole scratch area was lost: every venv, the runner, the CI-leg emulator, QA's probes, the pristine `origin/main` checkout and the saved untouched-main full-suite baseline. What was rebuilt and how: the six venvs with uv, offline from its wheel cache, so the versions are the ones used before the reboot (ci: httpx2 2.16.0, huggingface-hub 2.4.0, starlette 1.10.0; lock: httpx 0.28.1, hf 1.25.1, starlette 1.3.1; both; neither; py310: CPython 3.10.22, httpx2 2.14.0, hf 2.4.0, starlette 1.7.0; ci-sdk: anthropic 2.5.0, openai 3.46.0); the runner and the emulator were rewritten; QA's `state_status.py`, `state_cli.py`, `fork.py`, `stdlib_gaps.py` and `nav_harness.js` were recreated from the copies read earlier; `origin/main` is a real clone (see below). The saved baseline is gone, so the neighbour comparison now runs `origin/main` and this tree side by side. The numbers of the steps below that were measured before the reboot (steps 1 to 7, the red runs) were re-measured after it where a number is quoted in the verification tables.

**One trap found on the way.** `tests/test_queuellm_rename_frozen_surface.py` reads git history ("base commit 5f775f1c not in this clone"). Against a `git archive` export it skips both of its failing tests (3 passed, 2 skipped on both sides, zero `E` lines), and a comparison of two zero-line outputs looks identical. The frozen-surface comparison below is therefore run on real checkouts only: a clone of `origin/main` (`de1ce285`) and this worktree. The per-commit verification of the other tests runs on exports.

#### Step 1: QA-W2, a family member whose attribute lookup raises. Commit `b2fb7f3e`

`egress.py`: the call to `_patch_family_member` in the family loop is contained, exactly as the report shows (`unguarded: patch raised <Type>: <message>`), and two docstrings say so. Tests: EG-A8 is QA's proposed test, added verbatim next to EG-A3. EG-A9 is mine: either member as the broken one (2) times both lookups raising or only the async one (2), built from stubs so "the other member is still patched" is not vacuous in a venv with one library (QA's version has nothing to look at in ci and py310: its loop is over installed libraries other than the broken one), and it also pins the half-patched member (when only the async lookup raises, the sync class is patched, the member is still reported unguarded, and a reset puts the sync class back exactly). One status-level test in `test_egress_docs.py`: one portal warning that names the library, the airgapped definition qualified ("EXCEPT requests made through httpx2 ... NOT blocked"), the known-gaps entry.

- **Red before the fix: yes.** Against the unmodified source, **6 of 6** new cases failed in ci, lock, both and py310. EG-A8 and EG-A9 end in the raw `ImportError: simulated: a lazy attribute whose import fails`, raised from `egress.py:515` through `:464`, the path the report names. The status test fails because the portal warning reads "Egress guard install failed: simulated: a lazy attribute whose import fails" (no library named), so `uncovered()` was empty and the definition stayed unqualified.
- **Green after:** the 6 pass in all four venvs. 13 files: ci 378, lock 378, both 427, py310 378 (each +6), neither 376 with its two intended failures.
- QA's own probe, `state_status.py lazy-attr` in the **both** venv (httpx 0.28.1 + httpx2 2.16.0, airgapped), after the fix: the start-up function does not raise and writes one warning naming httpx2; `coverage()` is `httpx2: "unguarded: patch raised ImportError: cannot import name 'HTTPTransport' from partially initialized module 'httpx2._transports'"`, `httpx: "guarded"`; **httpx sync and async requests: `EgressBlocked`** (QA measured `ConnectError` on the unfixed tree: they reached the network layer); the definition reads "Cloud-provider APIs are blocked, EXCEPT requests made through httpx2: the guard could not patch it, so those requests are NOT blocked."; `known_gaps` has the httpx2 entry.

#### Step 2: QA-N8, two test comments. Commit `3cb17af4`

Run (ci, py310, lock): with `httpcore2` (or `httpcore`) made unimportable, `import httpx2` (2.14.0, 2.16.0) and `import httpx` (0.28.1) **succeed**, `HTTPTransport()`, `Client()` and `AsyncClient()` raise `ModuleNotFoundError`, and `install_guard()` reports the member `guarded` (no client can be built). A package whose absence does break the import is `idna` (run for both libraries). Both comments were mine (pass 3), and one claimed the opposite "exactly what huggingface_hub reports in a venv with httpx2 but no httpcore2". They now say what happens. The file's code is unchanged (AST comparison with docstrings removed); its cases are unchanged.

#### Step 3: QA-W1, urllib requests that skip the guard's opener. Commit `66b39468`

Run first, ci (3.11.17) and py310 (3.10.22), airgapped, guard installed, `.invalid` host: `urlopen(url)`, `urlopen(Request)` and `urlretrieve` end in `EgressBlocked` (+1 audit line each); `urlopen(url, context=...)` (https and http), `cafile=`, `capath=` and `cadefault=True`, `http.client.HTTPConnection` and `HTTPSConnection`, `xmlrpc.client.ServerProxy(...).ping()`, and `urlopen` after another library's `install_opener(build_opener(...))` end in the stdlib's own resolution error (`URLError` or `gaierror`) with **0** audit lines; with the guard's opener put back, `EgressBlocked` again. Source read (3.11.17): `urlopen` builds `build_opener(HTTPSHandler(context=...))` whenever `context`, `cafile`, `capath` or `cadefault` is given; `urllib.request` builds its connections with `http.client.HTTPConnection` / `HTTPSConnection`; `xmlrpc.client` imports `http.client`. The `cafile`, `capath` and `cadefault` parameters exist in 3.10, 3.11 and 3.12 and not in 3.14 and 3.15 (run; 3.13 is not installed here, the removal in 3.13 is from CPython's documentation and is not verified here).

Docs: PRIVACY's enforcement sentence and README scope `requests` (new sessions) and `urllib.request` (`urlopen` without an SSL context); one PRIVACY gap bullet names the three shapes; `airgap.KNOWN_GAPS` gains one entry (the status endpoint and the modal list it) and the module docstring one bullet; the modal paragraph names `<code>http.client</code>` and scopes `urllib.request`; README's gap list names `http.client`. QA's pin (Q2) holds the behaviour and is unchanged apart from a docstring that described the old docs.

- **Where the plan departs from the report.** The report's text says "none in tree today". `scripts/s4_win_checks.py` uses `http.client` (against `127.0.0.1`, an operator script), so the sentences say "nothing in `src/arail/` does any of these today", and a tripwire (`test_no_lab_code_goes_around_the_opener_the_guard_installs`, an AST scan of `src/arail/` apart from `egress.py`) checks it the way DOC-9 checks the install sites. `cadefault=` and `xmlrpc.client` are added to the report's text because they were run.
- **Red first:** 10 failing cases per venv against the old docs (DOC-2 for the three surfaces and `KNOWN_GAPS` once `http.client` joins the gap words, the count and last entry of `KNOWN_GAPS`, the README and PRIVACY scopes, the PRIVACY bullet, the modal). The tripwire and its positive control pass on a clean tree by design; their proof is the mutants (m6 to m8).
- 13 files: ci 384, lock 384, both 433, py310 384.

#### Steps 4 to 6: QA-N1, N2, N3 (PRIVACY wording). Commits `61535641`, `a64d9f5a`, `259ff646`

Each is one sentence with a pin written first (red against the old text in all four venvs), and the behaviour behind each is held by QA's pins or by a probe:

- **N1** (`61535641`): "The httpx family is patched on its classes, so every client and transport object is covered whenever it was built; a reference to the method itself taken before the guard (a bound `handle_request`, or a subclass that copies it into its class body) is not." Behaviour: QA's Q1 (3 shapes per library, with the covered contrast asserted) passes in ci, lock, both and py310. Q1's docstring now describes the new wording. 13 files: ci 385.
- **N2** (`a64d9f5a`): "...or imported again after the guard ran (its classes are new objects), is not guarded until the lab restarts." Behaviour: Q3 (a fresh interpreter) passes in the four venvs. Q3's docstring says nothing about the docs, so it is untouched. ci 386.
- **N3** (`259ff646`): "Other processes" names child processes started with `spawn` or `forkserver` (the default on macOS and, from Python 3.14, on Linux) and says a `fork` child inherits the guard. Run (ci, lock, py310; QA's `fork.py`): `fork` and `os.fork()` children `EgressBlocked`, `spawn` and `forkserver` children `ConnectError` (unguarded). Defaults: `spawn` on macOS (3.11.17, 3.14.9 here); from 3.14.9's `multiprocessing/context.py` the other POSIX platforms take `forkserver` (or `spawn` without a writable temp dir); not run on Linux. In tree, `research/agenda_watch.py:525` spawns a worker whose calls are `_extract_candidates`, `conn.send`, `patterns.append` and `re.compile` (AST), so it makes no network call. ci 387.

#### Step 7: QA-N5, the remedy text. Commit `4501336a`

`egress.py`: `_incomplete_message` offers "reinstall a version the lab was tested with (for example `.venv/bin/pip install 'httpx2<3'`) and restart the lab; uninstalling httpx2 would also disable what is built on it, such as the Hugging Face downloader and the cloud SDKs." The report writes it for httpx2 alone; the message is generic over the unguarded libraries, so each gets its pin (`_TESTED_RANGES`: `httpx2<3`, `httpx<1`, the ranges the old text named) and the wording is plural for two. The claim behind "disable": huggingface-hub 2.4.0, anthropic 2.5.0 and openai 3.46.0 require `httpx2<3` (installed metadata, run), huggingface-hub 1.25.1 requires `httpx<1`; huggingface-hub is a core dependency (`pyproject.toml`); the lab's venv is `.venv` (`scripts/setup.sh:485`). `docs/PRIVACY.md` carried the same "or uninstall it" advice, so it changes in the same commit (a delta from the report, decided in the plan). Tests moved with the text: EG-A2, the portal's warning assertion and the worker's line assertion (all three quoted "uninstall"), plus a plural-library test (from stubs, runs in every venv) and a PRIVACY pin. **Red first** in all four venvs (6, 6, 10, 6 failing before the change). 13 files: ci 389.

#### Step 8: QA-N4, the worker prints the message once. Commit `399e6ff8`

A real `python -m arail.nucleus.worker` process printed two lines for the same message: the guard's own `_log.error` through logging's last-resort handler (nothing configures logging in that process), then `worker: ...`. **Red first**, in a fresh interpreter (in pytest the last-resort handler never fires, so the existing in-process test saw one line): 2 lines before. The fix is a `NullHandler` on the guard's logger around `install_guard()`, taken off afterwards. **Delta from the report:** it suggests a `NullHandler` with `propagate=False`; the `NullHandler` alone is enough (the last-resort handler fires only when no handler is found anywhere up the hierarchy) and does not hide the record from a handler someone configures on the root logger. A second test pins that (the record still reaches a handler configured on the root logger, the logger is left as it was); it passes before and after and is what mutants m17 and m18 break (see step 12: its first version let m17 survive). 13 files: ci 391, lock 391, both 442, py310 391. The real worker with the guard unpatchable (ci) and in QA's lazy-attribute state (both): exit **1**, **one** stderr line, nothing on stdout, no traceback (QA's lazy-attr run on the unfixed tree ended in a raw `ImportError` traceback).

#### Step 9: QA-N4, the doctor says INCOMPLETE. Commit `2eb75e65`

`egress guard : ? (EgressGuardIncomplete: ...)` becomes `egress guard : INCOMPLETE — <the guard's message>`; the required finding is still recorded as failed and the exit status stays 3. DR-3 asserts the line (red first), and a second test pins that any other install failure still reads `? (<Type>: <message>)`. Run: the real doctor (`state_cli.py ... doctor`) with the guard unpatchable (ci) and in the lazy-attribute state (both) prints `egress guard      : INCOMPLETE — egress guard incomplete — httpx2: ...` and `doctor: degraded`, exit **3**. (The report asks for `INCOMPLETE — <message>` and the message starts "egress guard incomplete — ", so the line says it twice; kept as written.) 13 files: ci 392.

#### Step 10: QA-N6, the two older paragraphs. Commit `2e48ad23`

README ("Airgapped guard") and `docs/the-lab.md` ("Default mode is airgapped") name `requests` (new sessions), `urllib.request` (`urlopen` without an SSL context), `httpx` and `httpx2`, say the allowed addresses are loopback, the private network or another range Python's `ipaddress` treats as private, and point at PRIVACY. A pin for both paragraphs (every `GUARDED_CLIENTS` entry, the scope, the `ipaddress` rule, the pointer, neither stale phrase) was red against the old text. The tests that read README and the docs (`test_docs_cross_links`, `test_llama_disclosure`, `test_docgen`, `test_docs_registry_qa`) pass in the four venvs. The PRIVACY and README paragraphs about the removed two-step confirm (`PRIVACY.md` "Toggling LAB_MODE from the UI", `README.md` 233-236) are pre-existing and outside this change. ci 394.

#### Step 11: QA-N7, the nav badge. Commit `843d25e8`

`GET /api/system/mode` also returns `guard_installed` (`get_mode` only; nothing else in `app.py` changed). `nav.js`: `updateBadge` consults the flag the modal's pill already uses; airgapped with an incomplete guard reads "Airgapped, guard incomplete" in the hybrid badge's amber (the existing `.mode-badge.hybrid` rule, no new CSS; the class on the element is `hybrid`), with a title that points to the Network Policy modal; `textContent` throughout; the page-load fetch sets the flag before it paints, so the badge and the pill share one variable; an older server that sends no `guard_installed` reads as before (unknown is not claimed as incomplete). PRIVACY lists the badge among the places an incomplete guard shows. Tests, red first (**6 of 8 red**; the route test and the amber-style pin describe existing structure and pass): the endpoint in both modes and its agreement with `/api/airgap/status`, the route, source-level pins of `nav.js` in the style of DOC-6, the amber style, the PRIVACY sentence.

The real `nav.js` run under node (v26.12) with a DOM stub (`nav_harness_b4.js`, after QA's; not committed, because the sprint allows no skipped test for the thing under test and CI has no node harness):

| State (venv) | Badge at load | After the modal opens | Pill | Known-gaps list | After a flip to the other mode, and back |
|---|---|---|---|---|---|
| Whole guard, airgapped (ci) | `⬤ Airgapped`, class `mode-badge airgapped` | same | `ok` | closed | `⬤ Hybrid` (`hybrid`), then `⬤ Airgapped` (`airgapped`) |
| **Incomplete** (httpx2 `AsyncHTTPTransport` missing; ci), airgapped | **`⬤ Airgapped, guard incomplete`**, class `mode-badge hybrid`, title "Airgapped, but the egress guard could not patch every HTTP library — click to see which, in the Network Policy modal." | same | `warn` | **opened** | `⬤ Hybrid`, then **`⬤ Airgapped, guard incomplete`** |
| **Incomplete** (QA's lazy-attribute state; both), airgapped | as above | as above | `warn` | opened | as above |
| Incomplete, lab starts **hybrid** (ci) | `⬤ Hybrid` | same | `warn` | opened | flip to airgapped: **`⬤ Airgapped, guard incomplete`**; back: `⬤ Hybrid` |
| Control: the `nav.js` from before this commit, incomplete, airgapped | `⬤ Airgapped`, class `mode-badge airgapped` | same (pill `warn`, list open) | `warn` | opened | `⬤ Hybrid`, then `⬤ Airgapped` (green): QA-N7 reproduced |
| New `nav.js` against an older server without `guard_installed`, incomplete status | `⬤ Airgapped` | same | `warn` | opened | n/a |

*Means:* the badge, which is on every page, can no longer contradict the modal; *bar and why:* in every state where the guard is incomplete and the mode is airgapped the text must say so and the class must be amber, because a green "Airgapped" is the claim the guard knows is false; *out of range:* a plain "Airgapped" in an incomplete row is QA-N7 again; *where:* `nav.js` `updateBadge`, `portal/app.py` `get_mode`.

#### Steps 12 and 13: two follow-ups the pass found in its own tests. Commits `54af2aab` and `8fe63fbc`

- **`54af2aab`, the tripwire's import helper.** Reading the tripwire of step 3 again, its helper ignored `ImportFrom.level`, so `from .http import client` would read as `http.client`. Red first (the positive control gained a relative-import case), then fixed. Test-only; the case counts do not change.
- **`8fe63fbc`, a mutant that survived.** The first mutation run caught 26 of 27; **m17**, the report's suggested variant (a `NullHandler` plus `propagate = False`, never put back), **survived the whole test file** although the test written to catch it is red when run alone. Cause, read in pytest 9.2.0's `catching_logs`: at the start of a test pytest attaches its capture handler directly to every logger that is not propagating, so the flag leaked by the first in-process worker test made the record visible to `caplog` in the next test, and "propagate is what it was" compared the leaked value with itself. The test now puts the guard's logger in a known state (no handlers, propagating, restored by `monkeypatch`) and uses its own handler on the root logger instead of `caplog`. Re-run on the final head: m16, m17 and m18 are all caught (1 new failing id each; m17 is red in the whole file and alone). Test-only.

### Verification (the orchestrator's list), tree clean

Every table says what the number means, the bar and why, what out of range means, and where a failure lives. Environment: as under "Execution". Exports are `git archive` copies of the commit named, so nothing was created in the worktree; `git status` was clean after every commit.

#### V1. The 13 `EGRESS_TESTS` files in ci, lock, both and py310, on the final head `8fe63fbc` (passed; failed and skipped are 0 in every cell)

| File | baseline (ci) | ci | lock | both | py310 |
|---|---|---|---|---|---|
| `test_airgap_helpers.py` | 46 | 46 | 46 | 46 | 46 |
| `test_airgap_modal_dom.py` | 6 | 6 | 6 | 6 | 6 |
| `test_egress_bootstrap.py` | 22 | 22 | 22 | 22 | 22 |
| `test_egress_ci_check.py` | 44 | 44 | 44 | 44 | 44 |
| `test_egress_docs.py` | 55 | 78 | 78 | 90 | 78 |
| `test_egress_guard.py` | 18 | 18 | 18 | 18 | 18 |
| `test_egress_httpx.py` | 64 | 70 | 70 | 96 | 70 |
| `test_egress_httpx_fresh.py` | 7 | 7 | 7 | 10 | 7 |
| `test_httpx_family.py` | 17 | 18 | 18 | 21 | 18 |
| `test_qa_airgap_bypass_attempts.py` | 34 | 34 | 34 | 43 | 34 |
| `test_qa_airgap_happy_setup.py` | 17 | 17 | 17 | 17 | 17 |
| `test_qa_airgap_ipv6_edge_cases.py` | 14 | 14 | 14 | 14 | 14 |
| `test_qa_tier1_egress_guard.py` | 28 | 28 | 28 | 28 | 28 |
| **Total** | **372** | **402** | **402** | **455** | **402** |

- *Means:* the cases each leg's job runs, in that leg's library state. The baseline column is `15e3abc6` in ci (QA's 372); lock and py310 were 372 and both 421 there. The difference is this pass's tests: ci +30 = W2 +6, W1 +6, N1 +1, N2 +1, N3 +1, N5 +2, N4 worker +2, N4 doctor +1, N6 +2, N7 +8; by file: `test_egress_docs.py` +23, `test_egress_httpx.py` +6, `test_httpx_family.py` +1. The library-parametrized ones count once per installed library, so both is +34 (455); the neighbours of the 13 are not counted here.
- *Bar and why:* 0 failed and 0 skipped in every cell, every file at least 1 case. A skip is how TD50 stayed hidden, and the workflow's checker fails the leg on any of these.
- *Out of range:* a failure names a test; a skip means a test became conditional; a file with 0 cases means a path typo or a collection change.
- *Where:* the file in the row; `.github/workflows/egress-guard.yml` and `tests/egress_ci_check.py` for how the leg judges it.
- Per commit, cases in ci / lock / both / py310 (0 failed and 0 skipped in each cell): `b2fb7f3e` 378 / 378 / 427 / 378 and `66b39468` 384 / 384 / 433 / 384, `61535641` 385 / 385 / 434 / 385, `a64d9f5a` 386 / 386 / 435 / 386, `259ff646` 387 / 387 / 436 / 387 (all five measured in the worktree before the reboot); `3cb17af4` (N8) changes comments only and its file keeps its cases (69 / 69 / 95 / 69 at that commit); `4501336a` (N5) 389 in ci after the reboot, its two files in all four venvs before it (136 / 136 / 170 / 136); then each on its own export: `399e6ff8` 391 / 391 / 442 / 391, `2eb75e65` 392 / 392 / 443 / 392, `2e48ad23` 394 / 394 / 445 / 394, `843d25e8` 402 / 402 / 455 / 402; `54af2aab` and `8fe63fbc` change test bodies only (402 / 402 / 455 / 402, the table above).

#### V2. The neither venv (leg 5's state): the negative control

The whole list in the neither venv (declared `none`) at the final head: 400 cases, **345 passed, 2 failed, 53 skipped**. The two failures are the ones this venv is for: EG-0, the sentinel (`UNPROVEN`), which must fail, and FR-5 (huggingface_hub without its HTTP library), which leg 5 never runs. Leg 5's own command, emulated on the final head (V5): pytest `1 failed, 1 passed` (EG-0 failed, EG-0b passed) and the checker's `ok - the sentinel failed with UNPROVEN, as it must with neither library, and the declaration passed`.

- *Means:* with no httpx-family library installed the proof must refuse to pass. *Bar and why:* EG-0 red with its message and EG-0b green; the two failures are the ones this venv is for (EG-0, the sentinel, and FR-5, huggingface_hub without its HTTP library, which leg 5 never runs); the skips are the library-parametrized tests having an empty parameter set (53 at the final head: the new library-parametrized tests add to them). *Out of range:* EG-0 green means the sentinel shares the guard's lookup; any other red means this pass's tests assumed a library. *Where:* `tests/test_egress_httpx.py`.

#### V3. `tests/test_egress_sdk_clients.py` in `ci-sdk` (httpx2 2.16.0, anthropic 2.5.0, openai 3.46.0, declared)

3 passed, 0 failed, 0 skipped; the workflow's checker: `ok - 1 files, 3 cases, none skipped, failed or missing`.

| SDK | Case | Final error | Refusals | Seconds |
|---|---|---|---|---|
| anthropic 2.5.0 | built before / after install, `max_retries=0` | `APIConnectionError <- EgressBlocked` | 1 | 0.127 / 0.007 |
| anthropic 2.5.0 | built before / after install, default retries | `APIConnectionError <- EgressBlocked` | 3 | 1.457 / 1.398 |
| openai 3.46.0 | all four cases | `EgressBlocked` | 1 each | at most 0.074 |

*Means:* the SDKs' default clients are refused in airgapped mode, and the SDK wraps and retries the refusal. *Bar and why:* `EgressBlocked` in the chain, no network-error type, exactly one refusal at `max_retries=0` (no attempt left); the default retry count is the SDK's own business. *Out of range:* `ConnectError` or `gaierror` in a chain means an SDK's default client left the guarded transports. *Where:* SDK-1.

#### V4. The frozen-surface failure text against `origin/main` (`de1ce285`), on real checkouts

`tests/test_queuellm_rename_frozen_surface.py`, run on a clone of `origin/main` (`de1ce285`) and on this worktree at `8fe63fbc`, in ci, lock, both, py310 and neither: **2 failed, 3 passed on both sides in all five**, and the 13 `E ` lines are **identical** after normalising the tree path. They are the same four drift lines as in pass 3 (`scripts/build-aerollm.sh` losing `aerollm_version` x2, `src/arail/portal/app.py` losing `AEROLLM_MODEL` x3 and `AeroLLMBackend` x1, and `pyproject.toml | 5 +++++`); the counts for `app.py` did not grow. Also identical at `b2fb7f3e` (step 1) and at `843d25e8` (step 11).

- *Means:* the two pre-existing failures are not hiding a new one. *Bar and why:* identical text, because the test is red on main and a changed line would be a frozen token lost or a frozen path touched. *Out of range:* a new or changed line names the file and token. *Where:* the test's `E ` lines. See also "One trap found on the way": on exports this test skips, and an empty comparison proves nothing.

#### V5. All five legs through `emulate.py` (the workflow's own `run:` text; install steps skipped because the venvs are prebuilt)

On the final head `8fe63fbc` (`emulate.py`, the workflow's own `run:` text, `CI=true`, scratch HOME; the install steps are skipped because the venvs are prebuilt):

| Leg | Library state (venv, Python) | Egress step | Checker | SDK step | Leg |
|---|---|---|---|---|---|
| 1 httpx2 only (pip, floating) | httpx2 2.16.0, hf 2.4.0, starlette 1.10.0 (ci, 3.11.17) | 402 passed, exit 0 | `ok - 13 files, 402 cases, none skipped, failed or missing` | `ci-sdk`: the step printed `anthropic 2.5.0`, `openai 3.46.0`, `httpx2 2.16.0`; 3 passed; checker `ok - 1 files, 3 cases` | green |
| 2 httpx2 only, Python 3.10 | httpx2 2.14.0, hf 2.4.0, starlette 1.7.0 (py310, 3.10.22; the checker runs under 3.10 too) | 402 passed, exit 0 | ok, 13 files, 402 cases | not in this leg | green |
| 3 httpx only (uv.lock) | httpx 0.28.1, hf 1.25.1, starlette 1.3.1 (lock, 3.11.17) | 402 passed, exit 0 | ok, 13 files, 402 cases | not in this leg | green |
| 4 both | httpx 0.28.1 and httpx2 2.16.0 (both, 3.11.17) | 455 passed, exit 0 | ok, 13 files, 455 cases | not in this leg | green |
| 5 neither (negative control) | no httpx, httpx2, httpcore, httpcore2 (neither, 3.11.17) | not run (the leg's `if:`) | `ok - the sentinel failed with UNPROVEN, as it must with neither library, and the declaration passed` | not in this leg | green *because* the sentinel failed: pytest `1 failed, 1 passed` |

A first emulation on `54af2aab` (before the last test-only commit) gave the same verdicts and counts. The emulator was rewritten in this pass (the first was lost with the scratch area): it runs each `run:` text with `bash --noprofile --norc -eo pipefail`, from a `git archive` copy with `.venv` pointing at the prebuilt venv, and replaces only the `pip install anthropic openai` line of the SDK step (the `ci-sdk` venv has both).

- *Means:* each leg's job, run as GitHub would run its test steps. *Bar and why:* legs 1 to 4 pass every case with none skipped and every file counted; leg 5 is green only because its sentinel failed with `UNPROVEN` and its declaration passed. *Out of range:* a skip or a file with 0 cases fails the checker; a passing sentinel in leg 5 fails the control. *Where:* `.github/workflows/egress-guard.yml`; `tests/egress_ci_check.py`. The CI workflow was not edited in this pass.

#### V6. Mutants (scratch export of the final code; the files named in each mutant, ci venv; control 0 failing ids; a mutant is caught when it adds at least one failing id)

| Mutant | New failing ids | Named tests (first run) |
|---|---|---|
| W2-m1 containment removed | 7 | test_a_library_whose_attribute_lookup_raises_is_reported_like_any_othe; test_a_member_whose_attribute_lookup_raises_is_loud_and_the_next_membe; test_a_member_whose_lookup_raises_does_not_stop_the_other_member_from_ |
| W2-m2 contained, reported guarded | 7 | test_a_library_whose_attribute_lookup_raises_is_reported_like_any_othe; test_a_member_whose_attribute_lookup_raises_is_loud_and_the_next_membe; test_a_member_whose_lookup_raises_does_not_stop_the_other_member_from_ |
| W2-m3 only AttributeError contained | 7 | test_a_library_whose_attribute_lookup_raises_is_reported_like_any_othe; test_a_member_whose_attribute_lookup_raises_is_loud_and_the_next_membe; test_a_member_whose_lookup_raises_does_not_stop_the_other_member_from_ |
| W1-m4 KNOWN_GAPS entry removed | 3 | test_the_first_three_known_gaps_are_the_strings_the_endpoint_always_li; test_the_last_known_gap_is_the_urllib_requests_that_skip_the_guards_op; test_the_status_endpoints_gap_list_names_the_same_gaps |
| W1-m5 README scope reverted | 1 | test_readme_scopes_what_requests_and_urllib_are_wrapped_for |
| W1-m6 lab code imports http.client | 1 | test_no_lab_code_goes_around_the_opener_the_guard_installs |
| W1-m7 lab code calls install_opener | 1 | test_no_lab_code_goes_around_the_opener_the_guard_installs |
| W1-m8 lab code passes context= to urlopen | 1 | test_no_lab_code_goes_around_the_opener_the_guard_installs |
| W1-m9 modal drops http.client | 2 | test_each_surface_lists_the_gaps_the_guard_does_not_cover[modal]; test_the_modal_paragraph_scopes_urllib_and_names_http_client |
| N1-m10 method-reference sentence gone | 1 | test_privacy_says_the_httpx_family_covers_objects_but_not_a_method_ref |
| N2-m11 re-import clause gone | 1 | test_privacy_says_a_library_imported_again_after_the_guard_is_not_guar |
| N3-m12 spawn/forkserver clause gone | 1 | test_privacy_says_spawned_and_forkserver_children_do_not_inherit_the_g |
| N5-m13 old remedy back | 5 | test_the_model_forge_worker_stops_with_one_readable_line_when_the_guar; test_the_model_forge_worker_stops_with_one_readable_line_when_the_guar; test_the_portal_says_so_once_when_the_guard_is_incomplete[httpx2] |
| N5-m14 tested range changed | 2 | test_the_incomplete_install_error_says_what_is_unchecked_and_how_to_fi; test_the_remedy_names_a_tested_version_for_every_unguarded_library_and |
| N5-m15 PRIVACY remedy reverted | 1 | test_privacy_gives_the_same_remedy_as_the_incomplete_guard_message |
| N4w-m16 no NullHandler (two lines) | 1 (re-run on the final head: 1) | test_a_real_worker_process_prints_the_incomplete_guard_message_once[ht |
| N4w-m17 propagate=False added | 1 (**survived** the first run, **caught** after the test fix: 1) | test_the_worker_keeps_the_guards_log_record_for_any_handler_that_is_co |
| N4w-m18 handler never removed | 1 (re-run on the final head: 1) | test_the_worker_keeps_the_guards_log_record_for_any_handler_that_is_co |
| N4d-m19 doctor prints ? again | 1 | test_an_installed_library_the_guard_cannot_patch_fails_the_doctor_and_ |
| N4d-m20 incomplete not recorded as failed | 2 | test_an_installed_library_the_guard_cannot_patch_fails_the_doctor_and_; test_an_unpatchable_library_degrades_the_doctor_exit_code[httpx2] |
| N6-m21 README older paragraph: ipaddress rule gone | 1 | test_the_older_airgap_paragraphs_agree_with_the_readme_and_privacy[REA |
| N6-m22 the-lab.md drops httpx2 | 1 | test_the_older_airgap_paragraphs_agree_with_the_readme_and_privacy[doc |
| N7-m23 badge ignores the flag | 1 | test_the_nav_badge_does_not_read_plain_airgapped_while_the_guard_is_in |
| N7-m24 load fetch does not set the flag | 1 | test_the_nav_badge_is_given_the_guard_state_before_it_is_painted |
| N7-m25 endpoint omits guard_installed | 3 | test_the_mode_endpoint_carries_the_guard_state_the_badge_reads; test_the_mode_endpoint_says_the_guard_is_incomplete_and_agrees_with_th; test_the_mode_endpoint_says_the_guard_is_incomplete_and_agrees_with_th |
| N7-m26 badge text via innerHTML | 1 | test_the_nav_badge_does_not_read_plain_airgapped_while_the_guard_is_in |
| N7-m27 badge not amber | 1 | test_the_nav_badge_does_not_read_plain_airgapped_while_the_guard_is_in |

**caught:27 of 27**

- *Means:* whether each change has a test that fails without it. *Bar and why:* at least 1 new failing id per mutant. *Out of range:* a survivor is a change nothing pins; m17 was one (step 13). *Where:* the test named in the row. Mutants m1 to m3 include the state of the code before step 1 (m1 is the unmodified source).

#### V7. Neighbours of the changes, against untouched `origin/main`, side by side

108 neighbour files (the ones that mention the guard, the airgap, `nav.js`, the modal, the mode or status endpoints, the doctor, the worker, README, PRIVACY or the-lab, outside the 13; the list is the output of a grep, `test_host_link.py` excluded). Each venv runs them on an export of this tree, then re-runs only the files that have a failing id on the tree against a clone of `origin/main` (`de1ce285`) in the same venv:

| Venv | Tree | Files failing on the tree | Failing ids tree / main | Ids only on the tree | Case counts that changed |
|---|---|---|---|---|---|
| ci | `54af2aab` (code identical to the final head apart from two test bodies) | 12 of 108 (1 timeout) | 33 / 32 | **1**, `test_models_boot_endpoint.py::test_pinned_model_missing_falls_through_and_is_degraded`: see below | 0 |
| lock | `8fe63fbc` (the final head) | 11 of 108 (1 timeout) | 34 / 33 | **1**, `nucleus/test_qa_round3.py` as a *timeout* on the tree only: see below | 1 (a timed-out file leaves no junit: 103 cases on main, 0 on the tree) |

- **The one id only on the tree is nondeterministic on `origin/main` itself.** Six serial runs of `tests/test_models_boot_endpoint.py` on main (ci) failed 2, 3, 2, 1, 2 and 2 tests, four different tests among them, this one in runs 1, 2, 4 and 5; six on this tree failed 1, 2, 1, 2, 3 and 3 (the same four tests). It is not a regression; it is the same file noted in "Observations".
- **The timeout is `tests/nucleus/test_qa_round3.py`.** In ci it timed out on both trees (tree 470 s, main 554 s against a 420 s limit); in lock on the tree (466 s) while main's run in the same job finished (103 cases). The machine was under load from macOS system processes and from this pass's own background jobs (load average 8 to 16), and the file runs the worker as a subprocess, so it is the one place a hang in the N4 change would show. Re-run **serially, one process, on both trees in lock** once the load was gone: **86 passed, 3 skipped, 14 xfailed on each** (98.6 s on the tree, 110.2 s on main). It did not time out in the first neighbour run of this pass either, at lower load.
- The other failing files are the ones that fail on untouched `origin/main` (`portal/test_models_api`, `test_build_qkz_corpus`, `test_cache_prewarm`, `test_claude_cache` 7, `test_ensure_ready_build_isolation` (`No module named 'pandas'`), `test_host_edge_brain`, `test_instance_isolation_audit`, `test_model_hosting_reframe_qa`, `test_pkb_index_qa` 15, and in ci `test_portal_login`), with the same counts on both trees.

- *Means:* no test this pass did not write changed its verdict. The files are every test that mentions the guard, the airgap, `nav.js`, the modal, the mode or status endpoints, the doctor, the worker, README, PRIVACY or the-lab (108 files outside the 13; `test_host_link.py` is left out because it binds `127.0.0.1:8089`). The earlier untouched-main baseline was lost with the scratch area, so the files that fail on this tree are re-run on `origin/main` in the same venv. *Bar and why:* the same failing ids as main, because this pass changes the guard's callers (the portal's mode endpoint, the worker, the doctor, the nav bar) and a regression elsewhere would show here. *Out of range:* an id that fails only on this tree is a regression until a serial re-run on both trees says otherwise. *Where:* the file named.

#### V8. Order independence (E13): the 13 files in one process, forward and reversed

| Venv | Forward | Reversed | Checker (both orders) |
|---|---|---|---|
| ci | 402 passed | 402 passed | ok, 13 files, 402 cases |
| lock | 402 passed | 402 passed | ok, 13 files, 402 cases |
| both | 455 passed | 455 passed | ok, 13 files, 455 cases |
| py310 | 402 passed | 402 passed | ok, 13 files, 402 cases |

0 failed and 0 skipped in all eight runs, on an export of the final head `8fe63fbc`.

- *Means:* patch state does not leak between files or tests (this pass adds tests that replace `sys.modules` entries, add a logging handler, and change the nav). *Bar and why:* identical counts both ways, because the suite shares one process. *Out of range:* a difference names the file or test whose reset is incomplete. *Where:* the file whose count moved.

### Doc claims checked (pass 4)

Every factual sentence added to README, PRIVACY, the-lab, the modal, `airgap.KNOWN_GAPS`, a docstring, a comment or a user-visible string, with how it was checked. "Run" means run in this session; "test" names the committed test that now ties the sentence to the code.

| # | Where | Claim | Verdict | Evidence |
|---|---|---|---|---|
| 1 | PRIVACY enforcement sentence, README, the-lab, modal | The guard blocks `requests` (new sessions) and `urllib.request` (`urlopen` without an SSL context), `httpx`, `httpx2` | true | BY-5 and Q2 (green in four venvs); the probe in claim 2; tests `test_privacy_scopes_...`, `test_readme_scopes_...`, `test_the_older_airgap_paragraphs_...`, `test_the_modal_paragraph_scopes_urllib_...` |
| 2 | PRIVACY gap bullet, `KNOWN_GAPS`, modal | `urlopen(url, context=...)` builds an opener of its own; so do `cafile=`, `capath=`, `cadefault=` before Python 3.13 | true on 3.10, 3.11 (run); 3.13 removal not run | run on 3.11.17 and 3.10.22: https and http with a context, `cafile=`, `capath=`, `cadefault=True`: `URLError`, 0 audit lines; `urlopen(url)`, `urlopen(Request)`, `urlretrieve`: `EgressBlocked`, +1. Source (3.11.17): `urlopen` builds `build_opener(HTTPSHandler(context=...))` when any of the four is given. Signatures: the three parameters exist on 3.10, 3.11, 3.12 and not on 3.14, 3.15 (run); 3.13 is not installed here |
| 3 | same | `http.client` used directly (and `xmlrpc.client`, built on it) is the layer under `urllib`, as `urllib3` is under `requests` | true | run: `HTTPConnection` and `HTTPSConnection` `.request` and `xmlrpc.client.ServerProxy(...).ping()` end in `gaierror`, 0 audit lines. Source: `urllib.request` calls `do_open(http.client.HTTPConnection, req)` and `do_open(http.client.HTTPSConnection, req, ...)`; `xmlrpc.client` imports `http.client` |
| 4 | same | a later `urllib.request.install_opener(...)` by any library replaces the guarded opener for everyone | true | run: after `install_opener(build_opener(ProxyHandler({})))`, `urlopen` ends in `URLError`, 0 audit lines; with the guard's opener put back, `EgressBlocked`, +1 |
| 5 | same | Nothing in `src/arail/` does any of these today | true; "none in tree" would be false | grep over `src`, `scripts`, `core`, `examples`, `blueprints`: the only `http.client` use is `scripts/s4_win_checks.py:5,32` (loopback, an operator script outside `src/arail/`); every `urlopen(` in `src/arail/` passes no `context`, `cafile`, `capath` or `cadefault`; no `install_opener`, `build_opener`, `xmlrpc`. Test `test_no_lab_code_goes_around_the_opener_the_guard_installs` (AST scan, with a positive control); mutants m6 to m8 |
| 6 | PRIVACY | A reference to the method taken before the guard (bound `handle_request`, the function, a subclass that copies it) is not covered, while every client and transport object is | true | QA's Q1: 3 shapes per library, with the covered contrast (the pre-install transport object, method looked up now: refused) asserted, pass in ci, lock, both and py310 |
| 7 | PRIVACY | A library imported again after the guard ran has new classes and is not guarded until the lab restarts | true | QA's Q3 (fresh interpreter) passes in the four venvs |
| 8 | PRIVACY | A child started with multiprocessing's `fork` method inherits the guard; `spawn` and `forkserver` children do not; those are the default on macOS and, from Python 3.14, on Linux | true on macOS and for the children; Linux 3.14 default read, not run | run (ci, lock, py310; QA's `fork.py`): fork and `os.fork()` children `EgressBlocked`; spawn and forkserver `ConnectError`. Default start method `spawn` on macOS for 3.11.17 and 3.14.9. 3.14.9's `multiprocessing/context.py`: non-macOS POSIX takes `forkserver` (`spawn` without a writable temp dir) |
| 9 | commit message of step 6 | `research/agenda_watch.py:525` spawns a child that makes no network call | true | `mp.get_context("spawn")` at line 525; the worker's calls (AST) are `_extract_candidates`, `conn.send`, `patterns.append`, `re.compile` |
| 10 | the incomplete-guard message, PRIVACY | Uninstalling httpx2 (or httpx) disables what is built on it: the Hugging Face downloader and the cloud SDKs | true | installed metadata (run): huggingface-hub 2.4.0, anthropic 2.5.0 and openai 3.46.0 require `httpx2<3`; huggingface-hub 1.25.1 requires `httpx<1`; huggingface-hub is in `pyproject.toml`'s core dependencies |
| 11 | the message | `.venv/bin/pip install 'httpx2<3'` is the lab's way to reinstall | true as an example | `scripts/setup.sh:483-488` creates `.venv` and activates it; the ranges are the ones the old message and PRIVACY already named |
| 12 | `worker.py` docstring | With logging unconfigured, the last-resort handler would print the guard's record as a second stderr line; a NullHandler on the logger prevents it | true | run: 2 lines before, 1 after, in a fresh interpreter (test) and in the real worker with the guard unpatchable and in the lazy-attribute state |
| 13 | `doctor.py` comment | The line for a known state reads INCOMPLETE; a `?` reads as unknown | true | run: the real doctor prints `egress guard : INCOMPLETE — ...` and exits 3; other failures still `? (...)` (test) |
| 14 | README, the-lab | The allowed destinations are loopback, the private network, or another range Python's `ipaddress` treats as private | true | pass 3's run on 3.10.22 and 3.11.17 (nine ranges local, `100.64.0.1` and `8.8.8.8` refused); tests `test_the_local_check_lets_through_every_range_python_calls_private` and the refusal test |
| 15 | PRIVACY, `nav.js`, `get_mode` | The nav badge reads "Airgapped, guard incomplete" in amber when the guard is incomplete; its title points to the Network Policy modal | true | run: the real `nav.js` under node with a DOM stub, table in step 11; the endpoint and the source-level pins |

### Deltas from the plan

1. **Two extra commits, `54af2aab` and `8fe63fbc`** (steps 12 and 13): both fix my own tests, the first a tripwire that could cry wolf, the second a test that let a mutant survive. **`54af2aab`, in detail:** Reading my own tripwire (step 3) after the badge was done, I found its import helper ignored `ImportFrom.level`, so `from .http import client` would have been read as `http.client`. Fixed test-first (the positive control gained a relative-import case and was red) in a separate small commit, not folded into step 3, because step 3 had already been verified and recorded.
2. **EG-A9 and the status test beyond QA's EG-A8** (step 1), a plural-library remedy test (step 7), a second worker test and a second doctor test that pin the other side of each fix (steps 8 and 9), and the tripwire for "nothing in `src/arail/` does this" (step 3). None changes code outside the plan's file list.
3. **`docs/PRIVACY.md` also changed in step 7** (the remedy sentence) and **step 11** (the badge), as the plan's table says.
4. **QA's Q3 docstring is untouched** (the plan listed it as a possible edit): it says nothing about the docs.
5. **Verification on exports.** Because the machine was slow and the scratch area had been rebuilt, each commit's 13 files were verified on a `git archive` export of that commit, in the background, while the next step was built. Steps 1 to 7 were verified in the worktree before the reboot (their numbers are in each step) and the final head was verified again after it.

### Architect feedback required

None blocking. Three notes for the architect or the orchestrator, none a defect of this pass:

1. **The doctor still prints the guard's own log line to stderr** in addition to its `INCOMPLETE — ...` line on stdout (visible in the doctor run in step 9: an `arail.egress: egress guard incomplete ...` line on stderr). QA-N4 named the worker's duplicate and the doctor's `?`, and both are fixed as written; the same `NullHandler` would remove the doctor's duplicate, which is new scope.
2. **The badge is pinned at source level, plus an endpoint test, and run once under node** (step 11). A committed node harness would pin the behaviour itself, but the sprint rule is no skip for the thing under test, and a test that fails without node needs a decision about the CI runners and developer machines. The harness (`nav_harness_b4.js`, after QA's) was run, not committed.
3. **Closing the gaps QA-W1 documents is new work** (QA names patching `http.client.HTTPConnection.connect`, mutant M2's shape) and belongs with TD53, filed by the orchestrator.

### Observations for the review (not defects)

1. `tests/test_models_boot_endpoint.py` is nondeterministic on untouched `origin/main`: serial runs on the same tree failed `test_operator_v1_yaml_shape_reports_no_drift`, then `test_pinned_model_missing_falls_through_and_is_degraded`, then two tests, then none. It was the only "new" failing id in an early neighbour run of this pass and is not caused by it.
2. The `ci` venv's first import after the reboot took 27 s (cold disk cache plus compiling), 0.9 s afterwards; the venvs are now byte-compiled, and the per-file times in the early tables of this pass (5 to 10 times slower than in pass 3) came from macOS system daemons using the machine (load average 8 to 16), not from the tests.
3. `test_ensure_ready_build_isolation.py::test_cross_world_contamination_probe` fails with `No module named 'pandas'` on both trees (pre-existing, environment).
4. The status endpoint's `known_gaps` now has six static entries; a client that indexed past the old five (QA's probe did, with `[5:]`) needs to use `len(airgap.KNOWN_GAPS)`.

### Not verified here

The real GitHub run (install steps, `uv sync --frozen` on Linux, pip's resolution on the day, the schedule). Python 3.13 (the removal of `cafile=`, `capath=`, `cadefault=`) and Linux's default multiprocessing start method on 3.14 (read from 3.14.9's source, not run). A browser rendering of the modal and the badge (the real `nav.js` was run under node with a DOM stub). The full suite (the orchestrator's). QA-W3 and PX-1 (out by decision).

## Final state (pass 4)

- **Commits:** `df90517b` (this pass's plan), `b2fb7f3e` (QA-W2), `3cb17af4` (N8), `66b39468` (W1), `61535641` (N1), `a64d9f5a` (N2), `259ff646` (N3), `4501336a` (N5), `399e6ff8` (N4, worker), `2eb75e65` (N4, doctor), `2e48ad23` (N6), `843d25e8` (N7), `54af2aab` and `8fe63fbc` (two test follow-ups, steps 12 and 13), and the commit of this file (the orchestrator records its SHA). Branch `qukaizen/arail-egress-httpx2`, not pushed. No commit was rewritten.
- **Diff of the code commits** (`15e3abc6..8fe63fbc`, `sprints/` excluded): 14 files, +738 / -84. Source, docs and templates: 10 files, +171 / -63 (`docs/PRIVACY.md` +33/-14, `egress.py` +27/-6, `worker.py` +28/-3, `nav.js` +23/-10, `README.md` +15/-10, `doctor.py` +15/-8, `_airgap_modal.html` +9/-7, `portal/app.py` +8/-1 (`get_mode` only), `the-lab.md` +8/-4, `airgap.py` +5). Tests: 4 files, +567 / -21 (`test_egress_docs.py` +400/-8, `test_egress_httpx.py` +141/-8, `test_httpx_family.py` +21/-1, `test_qa_airgap_bypass_attempts.py` +5/-4, docstrings only).
- **Tests now** (13 files, `CI=true`, declared per venv): ci 402, lock 402, both 455, py310 402, all with 0 failed and 0 skipped, against 372 / 372 / 421 / 372 at `15e3abc6`. neither: 345 passed, 2 failed on purpose (EG-0 and FR-5), 53 skipped for empty parameter sets. `ci-sdk`: 3 passed. The frozen-surface text is identical to `origin/main`'s in five venvs. Order independence holds in four. All five emulated legs are green on the final head.
- **QA-W2:** the new tests were red on the unmodified source (6 of 6 in ci, lock, both and py310) and are green with the three-line containment; QA's `lazy-attr` probe in the both venv now ends in `EgressBlocked` for httpx and a qualified status.
- **Mutants:** 27 of 27 caught; one of them (m17) only after a test fix (step 13).
- **Not touched:** the three decision sites of QA-W3 (`egress.py` hunks are the family step, two docstrings and the message only), PX-1's innerHTML rows in `nav.js` (the only `nav.js` hunk is the badge), `SPRINT.md`, `sprints/BACKLOG.md`, the CI workflow (no test forced a pin to move), `src/arail/deep_*.py`, `src/dac_world/`, `src/arail/buddy_host/contract/`, `tests/conftest.py`, `pyproject.toml`, `uv.lock`, `CHANGELOG.md`, `NOTICE`, the licences; no `aerollm` / `AERO_*` name added or removed; no line added to code, tests or docs contains the pass-2 forbidden cost word or an unowned to-do marker (scanned over the diff). The airgapped default and the `allow_egress` ratchet are not relaxed: nothing in this pass changes a decision, only what the guard reports and says.
- **Not built, by decision:** QA-W3 (an `allow_egress` scope surviving a flip to airgapped) and PX-1 (the `innerHTML` rows), both filed as debt by the orchestrator; closing the urllib and `http.client` gaps QA-W1 documents (TD53).
- **Environment:** scratch only, rebuilt after the reboot (see "Execution"); `git status` is clean.
