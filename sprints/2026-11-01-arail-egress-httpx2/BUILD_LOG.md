# Build log: the egress guard covers the httpx family (TD50, runtime half of TD51)

**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `ec2a12be` (accepted by the ledger at `982da749`)
**Branch:** `qukaizen/arail-egress-httpx2` (base `origin/main` `5997363e`, which is #210). Not pushed.
**Started:** 2026-11-01
**This pass:** commits 1 to 4 of the "Atomic commit plan" (the guard and its proof). Commits 5 to 7 are a later builder pass; commit 8 is the orchestrator's, after #211 merges. Nothing below row 4 has been started.
**Status:** in progress (skeleton).

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
| 1 | `src/arail/httpx_family.py` (new); `tests/test_httpx_family.py` (new); `tests/egress_family_support.py` (new, first part: `LITERAL_FAMILY`, `installed_independently`) | The one place that names httpx and httpx2: `FAMILY`, `is_installed`, `installed`, `preferred`. Pure addition. | HF-1 to HF-6, plus three small ones (`is_installed` never imports; a module whose `__spec__` is None; stdlib-only imports). Written first and seen failing (the module does not exist). | pending |
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
Commit: pending (a commit cannot contain its own SHA; step 2's edit of this file fills it in).

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

### Step 2
pending

### Step 3
pending

### Step 4
pending

## Architect feedback required

**AF-1 (non-blocking; worked around with the same intent; please correct ARCHITECTURE.md).** "Test strategy" says the in-process tests monkeypatch `socket.gethostbyname` to return a TEST-NET-3 address (`203.0.113.7`) for names, so that the local check is deterministic. `ipaddress` calls the documentation ranges private: `ip_address("203.0.113.7").is_private` is `True` on CPython 3.10.22 and 3.11.17 (also 192.0.2.0/24 and 198.51.100.0/24). `arail.airgap.is_local_ip` returns `addr.is_private or ...`, so a name that resolves there is **local** and the guard lets it through. With the literal as written, EG-1 to EG-4 could never see `EgressBlocked`. Intent kept (a deterministic answer for names, no DNS query, no real host): the in-process tests patch only `socket.gethostbyname`, and return a globally routable address, `151.101.64.81`, the one `tests/test_egress_guard.py` and `tests/test_qa_airgap_bypass_attempts.py` already use. `getaddrinfo` stays real, so a guard regression is still a fast `ConnectError` on a `.invalid` name and never a connection to the patched address. The constant is `PUBLIC_TEST_IP` in `tests/egress_family_support.py`, with this reason in a comment. No design decision, contract or failure mode changes.

## Final state
pending
