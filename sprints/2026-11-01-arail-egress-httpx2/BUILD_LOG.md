# Build log: the egress guard covers the httpx family (TD50, runtime half of TD51)

**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `ec2a12be` (accepted by the ledger at `982da749`)
**Branch:** `qukaizen/arail-egress-httpx2` (base `origin/main` `5997363e`, which is #210). Not pushed.
**Started:** 2026-11-01
**This pass:** commits 1 to 4 of the "Atomic commit plan" (the guard and its proof). Commits 5 to 7 are a later builder pass; commit 8 is the orchestrator's, after #211 merges. Nothing below row 4 has been started.
**Status:** in progress (steps 0 to 2 done).

## Hard rules this pass keeps

- The airgapped default and the `allow_egress` ratchet are not relaxed. The audit schema (`url_host`, `caller`, `reason`, `lab_mode`, `ts`) is not changed.
- Not touched: `src/arail/deep_*.py`, `src/dac_world/`, `src/arail/buddy_host/contract/`, `tests/conftest.py`, `pyproject.toml`, `uv.lock`, `CHANGELOG.md`, `NOTICE`, `THIRD-PARTY-LICENSES`, `licenses/`, `lab/worlds`, `models/graduated`, `eval/`, `research/aerollm`, `docs/archive`; no `aerollm` / `AERO_*` name. `README.md`, `docs/` and the modal are not edited in this pass (commit 6).
- No `importorskip` and no skip for the thing under test in anything written here. No real host is contacted: a URL that must be blocked uses a `.invalid` name.
- The home clone `~/ProJects/qukaizen-arail` is not touched. Nothing is pushed or merged.
- Nothing is created in the worktree except committed files. Venvs, junit XML, logs and scratch trees live in the session scratch directory.

## Plan

| # | Files | Change | Test | Commit ref |
|---|---|---|---|---|
| 0 | `sprints/2026-11-01-arail-egress-httpx2/BUILD_LOG.md` | This skeleton: the contract with the reviewer. | none (docs) | `052a6217` |
| 1 | `src/arail/httpx_family.py` (new); `tests/test_httpx_family.py` (new); `tests/egress_family_support.py` (new, first part: `LITERAL_FAMILY`, `installed_independently`) | The one place that names httpx and httpx2: `FAMILY`, `is_installed`, `installed`, `preferred`. Pure addition. | HF-1 to HF-6, plus three small ones (`is_installed` never imports; a module whose `__spec__` is None; stdlib-only imports). Written first and seen failing (the module does not exist). | `da2057b6` |
| 2 | `src/arail/egress.py`; `tests/egress_family_support.py` (extend: `declared_family`, `PROBE_URL`, `PUBLIC_TEST_IP`); `tests/test_egress_httpx.py` (rewritten); `tests/test_qa_airgap_bypass_attempts.py` (BY-1, docstrings) | The TD50 fix: the family patch keyed by (class, attribute); `coverage()`, `uncovered()`, `GUARDED_CLIENTS`, `EgressGuardIncomplete`; RLock; idempotent saving of the `requests` adapter; exact or dotted-prefix `_SKIP_MODULES`; `*args, **kwargs` pass-through; docstrings. | EG-0 to EG-22 and BY-1. **Run against the unmodified `egress.py` first**; the reds are recorded per venv in Execution. | pending |
| 3 | `tests/test_egress_httpx_fresh.py` (new); `tests/test_egress_sdk_clients.py` (new); `tests/egress_family_support.py` (extend: `run_fresh`) | The brief's probe in a fresh interpreter, huggingface_hub, and the SDKs' default clients. No source change. | FR-1 to FR-5, SDK-1. Shown failing against commit 1's `egress.py` (a scratch tree), not only passing against commit 2's. | pending |
| 4 | `tests/test_egress_httpx.py` (+EG-23, EG-24); `tests/test_qa_airgap_bypass_attempts.py` (+BY-2, BY-3, BY-4) | Tripwires for an unknown httpx-family module or transport, and pins for the documented lower-layer and proxy gaps. | EG-23, EG-24, BY-2 to BY-4. Each shown to fail on a planted change. | pending |
| 5 | `src/arail/open_notebook_seed.py`; `src/arail/doctor.py`; `tests/test_httpx_family.py` | TD51 runtime consumers. | ON-1, ON-2, DR-1 to DR-3 | later builder pass |
| 6 | README, `docs/PRIVACY.md`, modal, `airgap.py`, `router/core.py`, `portal/app.py`, `tests/test_egress_docs.py`, `tests/test_qa_airgap_happy_setup.py` | Docs say what is wrapped; `KNOWN_GAPS`; status endpoint. | DOC-1 to DOC-5 | later builder pass |
| 7 | `.github/workflows/egress-guard.yml`; `tests/egress_ci_check.py`; `tests/test_egress_ci_check.py` | The five-leg workflow and its checker. | CI-1 to CI-4 | later builder pass |
| 8 | `sprints/BACKLOG.md` + ledger | Close TD50, re-scope TD51, file TD52 to TD55. | none | orchestrator, after #211 merges |

A commit cannot contain its own SHA, so the "Commit ref" of step N is filled in by the next commit's edit of this file. Step 4's is filled in by the next builder pass (and is in this pass's report).

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
Commit: pending (a commit cannot contain its own SHA; step 3's edit of this file fills it in).

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

### Step 3
pending

### Step 4
pending

## Architect feedback required

**AF-1 (non-blocking; worked around with the same intent; please correct ARCHITECTURE.md).** "Test strategy" says the in-process tests monkeypatch `socket.gethostbyname` to return a TEST-NET-3 address (`203.0.113.7`) for names, so that the local check is deterministic. `ipaddress` calls the documentation ranges private: `ip_address("203.0.113.7").is_private` is `True` on CPython 3.10.22 and 3.11.17 (also 192.0.2.0/24 and 198.51.100.0/24). `arail.airgap.is_local_ip` returns `addr.is_private or ...`, so a name that resolves there is **local** and the guard lets it through. With the literal as written, EG-1 to EG-4 could never see `EgressBlocked`. Intent kept (a deterministic answer for names, no DNS query, no real host): the in-process tests patch only `socket.gethostbyname`, and return a globally routable address, `151.101.64.81`, the one `tests/test_egress_guard.py` and `tests/test_qa_airgap_bypass_attempts.py` already use. `getaddrinfo` stays real, so a guard regression is still a fast `ConnectError` on a `.invalid` name and never a connection to the patched address. The constant is `PUBLIC_TEST_IP` in `tests/egress_family_support.py`, with this reason in a comment (landed in step 2). No design decision, contract or failure mode changes.

## Final state
pending
