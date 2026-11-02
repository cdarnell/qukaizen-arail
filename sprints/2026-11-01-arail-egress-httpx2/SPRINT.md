# Sprint: ARAIL egress guard covers the httpx family

**ID:** 2026-11-01-arail-egress-httpx2
**Started:** 2026-11-01
**Product:** arail (`qukaizen-arail`)
**Branch:** `qukaizen/arail-egress-httpx2` (cut from `origin/main` `5997363e`, which is #210)
**Worktree:** `<session>/qukaizen-arail-egress-wt`
**Debt:** TD50 (the egress guard patches `httpx` only) and its sibling TD51 (code and tests that import `httpx` by name). Both were filed by PR [#211](https://github.com/cdarnell/qukaizen-arail/pull/211) (TD48's repair), which merged 2026-11-02 00:59 UTC as `de1ce285`; until then they existed in `sprints/BACKLOG.md` only on that branch, and the BACKLOG commit waited for it.

## Resume here (rewritten at every checkpoint)

- **State:** Checkpoint 2, head `f2218b1c` (builder pass 2, plus the merge of `origin/main`). Done: design, build passes 1 and 2, the orchestrator's two re-runs. Next, in order: architect review (`REVIEW.md`) and its fix loop; qa (`TEST_REPORT.md`); the full-suite "after" runs against baseline 2; commit 8 (BACKLOG); push and open the PR (this session does not merge it).
- **Checked by re-running, not by reading a report:** the brief's probe in every venv; the five CI legs from the workflow's own `run:` text; ten mutants of the pass-2 pieces; the frozen-surface failure text; six of the builder's 33 documentation claims.
- **Taken on the builder's word so far:** the other 27 documentation claims; the `actionlint` and `shellcheck` runs; the dry-run of the install steps.
- **Cannot be checked on this machine:** anything on a GitHub-hosted Linux runner (the install steps, `setup-python`'s cache, pip's resolution on the day, the 15-minute timeout). The first real run of the five legs is the proof. Watch two things in it: leg 2 declares `httpx2` (Python 3.10 resolves 2.14.0), and leg 5 is green because its sentinel failed.
- **Goes stale if `origin/main` moves:** baseline 2 (taken on `de1ce285`). Merge main again and re-run it, before commit 8.
- **Open for the architect (builder's feedback AF-2 and AF-3):** four rewritten documentation sentences; the Knowledge Canvas rationale (the design's reason for dropping that claim is wrong, but no single true sentence replaces it); three `import httpx` sites outside TD52's inventory (`core/knowledge-canvas/backend/app/services/llm_router.py:32`, `core/knowledge-canvas/client.py:25`, `scripts/deep_honest_win.py:717`).

## Task

Close the airgap gap TD50: with `LAB_MODE=airgapped` and `arail.egress.install_guard()` run, an `httpx2` client (and so a `huggingface_hub` 2.x download, a current `anthropic` or `openai` client, or anything else built on `httpx2`) reaches the network layer, where a `requests` call to the same URL is refused with `EgressBlocked`. This is the airgap invariant (`CLAUDE.md`: "Don't relax this default"), so it runs as a sprint: architect (design), builder, architect (review), qa. The visionary phase is skipped because the win condition is stated by the operator's brief (below). Also decide how much of TD51 belongs in the same PR, and close or re-scope TD50 and TD51 in `sprints/BACKLOG.md`.

## Win condition (from the brief)

In a CI-style venv (`httpx2` only), in the lock venv (`httpx` only) and with both installed, an airgapped lab blocks outbound requests made by `httpx` AND `httpx2` clients, sync and async, including clients built before `install_guard`. Proven by tests that cannot pass vacuously: no `importorskip` that silently skips the very thing under test, and a CI job that has neither library must fail loudly, not skip. The probe `LAB_MODE=airgapped` + `install_guard()` + `<lib>.Client(timeout=3).get("http://egress-probe.invalid/")` ends in `EgressBlocked` for both libraries. `README.md` and `docs/PRIVACY.md` then say what is actually wrapped.

Constraints: never relax the airgapped default; no `aerollm` / `AERO_*` rename; do not touch `src/arail/deep_*.py`, `src/dac_world/` or `src/arail/buddy_host/contract/`; say "token-free", never "free"; every reported number carries what it means, the bar and why, what out of range means, and where a failure lives.

## Root cause

`arail.egress` guards `httpx` by importing that module by name and patching `HTTPTransport.handle_request` and `AsyncHTTPTransport.handle_async_request` on the class; when the import fails it returns silently ("missing httpx is a silent no-op"). `httpx2` is a successor package with its own module name (`httpx2` over `httpcore2`), and `huggingface-hub` 2.x, `anthropic` 2.x and `openai` 3.x depend on it and not on `httpx`. A fresh unpinned `pip install -e ".[dev]"` therefore has no `httpx` module and the guard patches nothing; a mixed install patches the old library while the client that is actually used runs unguarded. Everything that was meant to notice stayed quiet: the tests pinning the guard skip themselves when `httpx` is absent (`importorskip` in `tests/test_egress_httpx.py`, `pytest.skip` in `tests/test_qa_airgap_bypass_attempts.py`), no CI workflow runs any egress or airgap test at all, and the README, `docs/PRIVACY.md`, the Network policy modal and the `egress.py` docstring all promise `httpx` coverage "including the cloud SDKs" without naming `httpx2`.

## Phases

| Phase | Subagent | Artifact | Status | Verdict |
|---|---|---|---|---|
| think | none | none | skipped, on purpose | The win condition is stated by the operator's brief. |
| baseline | none (the orchestrating session) | this file | done | Reproduced in three venvs before any change: see Baseline. |
| plan | architect (design) | ARCHITECTURE.md | done | The design is the build contract. 13 of its claims were run against the real libraries (V1 to V13). The six open questions are answered in the Decisions log. |
| build, pass 1 (commits 1 to 4) | builder | BUILD_LOG.md | done, checked by the orchestrator | The guard, its proof and the tripwires. Re-run independently, see Checkpoint 1. |
| build, pass 2 (commits 5 to 7) | builder | BUILD_LOG.md | done, checked by the orchestrator | The TD51 consumers, the documentation and the CI workflow. Re-run independently, see Checkpoint 2. |
| integrate #211 | none (the orchestrating session) | this file | done | #211 merged 2026-11-02 00:59 UTC; `origin/main` merged into the branch as `f2218b1c`, clean. |
| review | architect (review) | REVIEW.md | pending | |
| test | qa | TEST_REPORT.md | pending | |
| ship | none | PR | pending | |

## Baseline (before any change)

Three venvs, all editable installs of this worktree, built in the session's scratch directory. "CI-style" is `python3.11 -m venv V && V/bin/pip install -e ".[dev]"` (what the workflows run; resolved on 2026-11-01). "Lock" is `uv sync --frozen --extra dev` (what local development follows). "Both" is CI-style plus `pip install httpx`.

| Venv | `httpx` | `httpx2` | `huggingface-hub` | `starlette` |
|---|---|---|---|---|
| CI-style | not installed | 2.16.0 (`httpcore2` 2.16.0) | 2.4.0 | 1.10.0 |
| Lock | 0.28.1 (`httpcore` 1.0.9) | not installed | 1.25.1 | 1.3.1 |
| Both | 0.28.1 | 2.16.0 | 2.4.0 | 1.10.0 |

**B1. The probe, airgapped, guard installed, host `egress-probe.invalid` (a reserved name that never resolves).**

| Venv | `requests.get` | `httpx` sync / async | `httpx2` sync / async |
|---|---|---|---|
| CI-style | `EgressBlocked` | not installed | `ConnectError` / `ConnectError` |
| Lock | `EgressBlocked` | `EgressBlocked` / `EgressBlocked` | not installed |
| Both | `EgressBlocked` | `EgressBlocked` / `EgressBlocked` | `ConnectError` / `ConnectError` |

- *Means:* `EgressBlocked` is the guard refusing before any socket I/O. `ConnectError` is the request having reached the network layer (here a failed DNS lookup, because the name is reserved and never resolves; to a real host it would have connected). The `httpx2` row is the gap, and it is present whether or not `httpx` is also installed.
- *Bar and why:* `EgressBlocked` in every cell that has a library. The airgap promises that cloud-provider calls are refused at the HTTP layer; a library that is not guarded breaks the promise for everything built on it.
- *Out of range:* any other exception type in a cell means the request left the guard behind.
- *Where:* `src/arail/egress.py::_install_httpx_guard`.

**B2. `huggingface_hub` 2.4.0 in the CI-style venv (a core dependency, the lab's model downloader), `endpoint="http://egress-probe.invalid"`.** `HfApi.model_info` ended in `ConnectError`, and `hf_hub_download` retried 5 times ("Retrying in 8s [Retry 5/5]") before failing with `LocalEntryNotFoundError`. Neither was the guard. *Means:* a model download is not stopped by the airgap in a fresh install, and it spends the retry budget on the network first. *Where:* the same function.

**B3. The cloud SDKs.** `pip install --dry-run` on 2026-11-01 resolves `anthropic` 2.3.0 (requires `httpx2<3,>=2.0.0`) and `openai` 3.42.1 (requires `httpx2<3,>=2.12.0`); neither requires `httpx`. The lock holds `anthropic` 0.120.0 and `openai` 2.49.0, which require `httpx`. *Means:* the README's "incl. the cloud SDKs built on it" is true only on a lock-pinned install; a fresh `maximus` install runs both SDKs on the unguarded library. (The router also refuses to construct a cloud backend while airgapped, `router/core.py`, so a chat request does not reach the SDK; the guard is the second line, for any other caller.)

**B4. Adjacent libraries the guard has never patched** (same probe, CI-style venv): `urllib3.PoolManager().request(...)` ended in `NameResolutionError` and `httpcore2.request(...)` (the layer under `httpx2`) in `ConnectError`. Neither is `EgressBlocked`. *Means:* direct use of either is outside the guard, and the README/PRIVACY known-gaps lists name neither. Not part of TD50; recorded so the documentation can say it.

**B5. The existing egress tests on untouched code** (`tests/test_egress_httpx.py`, `test_egress_guard.py`, `test_egress_bootstrap.py`, `test_qa_airgap_bypass_attempts.py`, `test_qa_tier1_egress_guard.py`, `test_airgap_helpers.py`, `test_qa_airgap_happy_setup.py`, `test_qa_deep_honest_egress_happy.py`):

| Venv | `test_egress_httpx.py` | `test_qa_airgap_bypass_attempts.py` | others |
|---|---|---|---|
| CI-style | 1 test, skipped (the whole module: 7 tests never collected) | 16 tests, 1 skipped (`test_httpx_is_guarded_in_airgapped`) | all pass, except 1 failure |
| Lock | 7 passed | 16 passed | all pass |
| Both | 7 passed | 16 passed | all pass |

- *Means:* the two skips are exactly the checks that would have caught this, and they are silent. The single CI-style failure is `test_curator_honest.py:150` through `test_qa_deep_honest_egress_happy.py` (`assert 401 == 202`): the TD48 harness fault that #211 repairs, not an egress fault.
- *Bar and why:* none of the guard's tests may skip because a library is absent; a skip is how this stayed hidden.
- *Where:* `tests/test_egress_httpx.py:19`, `tests/test_qa_airgap_bypass_attempts.py:214`.

**B6. No workflow runs any egress or airgap test.** `grep -rn egress .github/` finds no test list that includes one: the DB-ensure, DaC, Nucleus, Buddy-floor and Blueprint Smoke jobs each run a fixed list of files, and none is an egress or airgap file. *Means:* even with every skip removed, a regression of the guard would not turn any check red. A CI leg that runs them, in each library state, is part of the fix.

**B7. Claims that name `httpx`:** `README.md:305`, `docs/PRIVACY.md:59`, `src/arail/portal/templates/_airgap_modal.html:138-141` ("catches `requests`, `urllib.request`, and `httpx` (which covers the Anthropic/OpenAI SDKs)"), the `egress.py` module and function docstrings, `src/arail/airgap.py:12` (which says the opposite: "`httpx` — not wrapped; localhost-only in tree", left over from before the guard patched it), and the comment at `src/arail/router/core.py:15`.

**B8. TD51 inventory.** `src/arail/open_notebook_seed.py:19` (`import httpx` at module level; every call targets localhost), `src/arail/doctor.py:278` (lists `httpx` among component versions, so it prints "not installed" in a CI-style venv), `tests/test_host_edge.py:22`, `tests/test_host_edge_brain.py:18` (also needs `openai`), `tests/portal/test_opencode_routes.py:22`, `tests/buddy_conformance.py:346` (lazy, inside a helper), `tests/conftest.py:284` (rewritten by #211; leave to it). `core/knowledge-canvas/` is a separate app with its own `requirements.txt`.

**B9. The full suite on untouched `origin/main` (`5997363e`), the "before" for the final comparison.** One pytest process per test file (510 files), 3 at a time, 420 s cap per file, a scratch `HOME` and `TMPDIR`, one venv after the other.

| Venv | Files with a failure or error | Failed | Errors | Timeouts | Wall time |
|---|---|---|---|---|---|
| Lock (`uv sync --frozen --extra dev --extra maximus`) | 31 | 78 | 8 | 0 | 566 s |
| CI-style (`.[dev]`, versions held at the 2026-11-01 resolution) | 156 | 1207 | 17 | 0 | 479 s |

- *Means:* the set of failing ids on each side is what the "after" run is compared with. The CI-style count is large because main still carries TD48 (portal tests reach the portal with no credential and get 401, repaired by #211) and TD52's test files, and because `dev` alone has no `maximus` extras.
- *Bar and why:* the same failing ids after the change, in the same venv. This PR changes the guard in every process that boots the portal, and in a CI-style venv `httpx2` is now patched there for the first time, so a test that boots the portal and then calls a public host through `httpx2` is the regression to look for.
- *Out of range:* an id that fails only after the change is a regression until a serial re-run on both trees says otherwise (the sibling repair cleared 4 such ids that way). If #211 merges and main moves, the "before" is re-run on the new main.
- *Where:* session scratch (`results/full-before-lock`, `results/full-before-ci`, junit per file); not committed. Reproduce with one pytest process per file in a venv built as described in Baseline.

## Decisions log

| Date | Decision | Why |
|---|---|---|
| 2026-11-01 | Plan to build gate: passed. `ARCHITECTURE.md` (`ec2a12be`) is the contract; the builder works from the file, not from a summary. | All sections populated; the load-bearing claims were run, not assumed. |
| 2026-11-01 | Open question 1, TD55 (hybrid mode's audit claim is false): file it, do not fix it here. | A third debt in different docs; the brief asks for a focused PR. |
| 2026-11-01 | Open question 2, required checks: not changed here. The PR says the five "Egress guard" legs can be marked required after two green runs, and that their names then become a contract. | A GitHub repository setting is the operator's. |
| 2026-11-01 | Open question 3, cadence: daily, as designed, and flagged in the PR as a choice the operator can change in one line. | The repo is public (checked: `gh repo view` says PUBLIC), so minutes cost nothing; Blueprint Smoke already runs daily at 09:17; the dependency state can change on PyPI with no commit here. |
| 2026-11-01 | Open question 4, numbering: TD52 to TD55 are confirmed at the BACKLOG commit, after #211 merges. | Another PR may take TD52 first. |
| 2026-11-01 | Open question 5, the nucleus worker exits non-zero on an incomplete guard even in hybrid: accepted. | Fail closed; the toggle can flip to airgapped in-process, and the worker has no handler. |
| 2026-11-01 | Open question 6: the PR description states the behaviour change (a fresh install now refuses `httpx2` traffic when airgapped, as a lock install already refused `httpx`). | It is the point of the fix, and the operator should not meet it first as a surprise. |
| 2026-11-01 | The build runs as two builder passes, commits 1 to 4 (the guard and its proof) and then 5 to 7 (consumers, docs, CI). Commit 8 (BACKLOG) is made by the orchestrator after #211 merges. | A checkpoint after the security-critical core, where the orchestrator re-runs the probe and the targeted tests in every venv before the rest is built. |
| 2026-11-01 | New tests are written and seen failing against the unmodified guard before the fix is applied (recorded in BUILD_LOG). | A test that never failed proves nothing. |
| 2026-11-01 | Commit trailers name the model that wrote the commit: the architect's commit says Opus 5.5, the orchestrator's and the builder's say Sonnet 5.5. | Attribution should be true. |
| 2026-11-01 | Skip the visionary. | The brief states the win condition. |
| 2026-11-01 | Cut from `origin/main` as told, not from #211's branch. | The brief says so; it keeps this PR's diff to this PR's work. Consequence: until #211 merges, this PR's DB-Ensure, DaC and Blueprint Smoke checks are red for TD48's reason and are compared with main's, not read as green. |
| 2026-11-01 | The BACKLOG closure of TD50 and TD51 is the last commit, made after #211 merges. | TD49 to TD51 exist only on #211's branch. Rewriting rows that main does not have yet would conflict with #211 in either merge order. |
| 2026-11-01 | Verification venvs live in the session scratch directory and are editable installs of this worktree (and, for the baseline, of a detached worktree of `origin/main`). | The home clone `~/ProJects/qukaizen-arail` (its branch, working tree and `.venv`) stays untouched. |
| 2026-11-01 | #211 merged (2026-11-02 00:59:09 UTC, `de1ce285`). `origin/main` is merged into this branch with a merge commit, not rebased. | Rebasing would rewrite 13 commits and invalidate the SHAs that BUILD_LOG and this file quote. The merge was clean and shares no file with #211, so the PR's diff against main is unchanged. |
| 2026-11-01 | Full-suite baseline 2: the "before" is re-run on `de1ce285`. The first baseline (`5997363e`, B9) stays as a record only. | #211 repaired the 401s that made up most of the CI-style failures, so the first baseline can no longer isolate this change. |
| 2026-11-01 | Commit 8 (BACKLOG) stays last, after review, qa and the "after" runs. | #211 has merged, so it is unblocked; keeping it last keeps the review about the guard. |
| 2026-11-01 | The session was resumed from a context summary at Checkpoint 2. This file, not the summary, is the source of truth, and it now opens with a "Resume here" block that is rewritten at every checkpoint. | The summary was stale on three facts within hours of being written: #211 had merged, `origin/main` had moved from `5997363e` to `de1ce285`, and builder pass 2 had finished. |

## Checkpoint 1: the orchestrator's re-run after builder pass 1 (head `a02ba141`)

Nothing below is taken from the builder's report; each item was run again by the orchestrator in the same venvs as Baseline.

- **The brief's probe, fresh interpreter, after:** `EgressBlocked` in every cell. CI-style (`httpx2` 2.16.0), both-installed and Python 3.10 (`httpx2` 2.14.0): all four `httpx2` variants (sync and async, client built after and before `install_guard`); lock (`httpx` 0.28.1) and the `httpx` rows of both-installed: unchanged, still `EgressBlocked`. Before, every `httpx2` cell was `ConnectError` (Baseline B1).
- **`huggingface_hub`, after:** `model_info`, `hf_hub_download` and `snapshot_download` each end in `EgressBlocked` with exactly 1 refusal written, in at most 0.02 s, on 2.4.0 (CI-style, Python 3.10) and 1.25.1 (lock). Before (2.4.0, untouched code): 0 refusals, a `ConnectError` chain, and `hf_hub_download` spent 23.06 s in retries before `LocalEntryNotFoundError`.
- **The targeted egress and airgap list (13 files), per venv, passed / failed / skipped:** CI-style 260/1/3, lock 261/0/3, both 293/0/3, Python 3.10 260/1/3. The 1 failure is `tests/test_qa_deep_honest_egress_happy.py` (`assert 401 == 202`), the TD48 fault that #211 repairs and that was red before this work (Baseline B5). Of the 3 skips, 2 are `tests/test_egress_sdk_clients.py`'s SDK cases where the SDK is not installed and not declared (that file runs with both SDKs declared in its own CI step, where a skip fails the step; the `sdk` venv gives 3 passed, 0 skipped), and 1 is a pre-existing `xfail` in `tests/test_qa_deep_honest_egress_happy.py` (a credential echo in `deep status`, marked expected; junit counts an `xfail` as skipped; that file is not in the CI list).
- **The negative control, venv with neither library:** the sentinel fails with the `UNPROVEN` message and the declaration test passes under `none` (and fails under a wrong declaration). Before this work the same venv ran the egress files green.
- **Mutants, final tests against a deliberately broken guard (restored afterwards, worktree clean):** M1, `origin/main`'s original `egress.py` put back: 37 tests red in CI-style (31 + 5 + 1 across the three files) and 46 in both-installed, including the brief's probe (`httpx2: sync-after -> ConnectError`, all four variants) and the `huggingface_hub` test. M2, `FAMILY = ("httpx",)` and nothing else changed: 37 red in CI-style (27 + 5 + 4 + 1). The fresh-interpreter tests exercised the mutated file, so the pinned `PYTHONPATH` works.
  - *Means:* the proof fails when the bug is present, for the reason it is meant to catch.
  - *Bar and why:* at least the brief's probe red under both mutants. A proof that cannot fail proves nothing.
  - *Out of range:* a mutant with nothing red means a vacuous test.
  - *Where:* `tests/test_egress_httpx.py`, `tests/test_egress_httpx_fresh.py`, `tests/test_qa_airgap_bypass_attempts.py`.
- **`tests/test_queuellm_rename_frozen_surface.py`: 2 failures, identical on pristine `origin/main` and on this branch** (failure text diffed): token drift in `scripts/build-aerollm.sh` and `src/arail/portal/app.py` and changed frozen paths, both from earlier sprints. Not caused by this work; it cannot be used as a green gate, so the criterion is "the failure text does not change".
- **Erratum to `ARCHITECTURE.md` (builder's AF-1, verified):** the test strategy says to point `socket.gethostbyname` at the TEST-NET-3 address `203.0.113.7` so a `.invalid` name has a deterministic, non-local address. Python's `ipaddress` calls that whole documentation range private (`203.0.113.7`, `198.51.100.7` and `192.0.2.7` are all `is_private`), and the guard treats a private address as local, so the guard would let the request through and no `EgressBlocked` could ever be seen. The builder used the globally routable `151.101.64.81`, which existing tests already use; only `gethostbyname` is patched, so a regression is still a fast `ConnectError`. To be corrected in `ARCHITECTURE.md` by the architect in the review pass.

## Checkpoint 2: the orchestrator's re-run after builder pass 2 (head `f2218b1c`)

Nothing below is taken from the builder's report; each item was run again by the orchestrator, on the tree that includes #211, in the same venvs as Baseline. The session resumed from a context summary between Checkpoint 1 and here, so the first action was to reconcile that summary with the live state (see the last Decisions row).

- **Integration.** #211 merged 2026-11-02 00:59:09 UTC as `de1ce285` (`gh pr view`). `origin/main` is merged into the branch as `f2218b1c`: clean, no file in common (#211 changed seven files, this branch twenty-four), so the BACKLOG commit is unblocked. #211's change to `tests/conftest.py` (no `import httpx`; it wraps `TestClient.send`; it catches `RuntimeError` as well as `ImportError`) does not touch the guard, and the `neither` venv still reaches its sentinel (leg 5 below). Baseline 2 on `de1ce285` is running; its numbers are recorded at the next checkpoint.
- **The brief's probe, merged tree, fresh interpreter:** `EgressBlocked` in every cell, as at Checkpoint 1. `httpx2` 2.16.0 (Python 3.11) and 2.14.0 (3.10), sync and async, client built after and before `install_guard`; `httpx` 0.28.1 (lock, and both-installed) likewise. `huggingface_hub` 2.4.0 (ci, 3.10) and 1.25.1 (lock): `model_info`, `hf_hub_download` and `snapshot_download` each ended in `EgressBlocked` with exactly 1 refusal written, in at most 0.02 s. Direct `urllib3` and `httpcore2` calls still end in `NameResolutionError` and `ConnectError`, the documented gap.
- **The five CI legs, run from the workflow's own `run:` text** (each step whose `if:` holds for the leg, with `.venv/bin/python` pointed at a prebuilt venv, `CI=true`, the leg's declaration, a scratch `HOME`; the install steps are skipped because the venvs are prebuilt; the worktree was clean before and after):

  | Leg | Venv | Declared | Result |
  |---|---|---|---|
  | 1 | `httpx2` 2.16.0 only, Python 3.11.17 | `httpx2` | 13 files, 320 cases, none skipped, failed or missing. The SDK step, in a venv with `httpx2` only plus anthropic 2.3.0 and openai 3.42.1: 3 cases, none skipped. |
  | 2 | `httpx2` 2.14.0 only, Python 3.10.22 | `httpx2` | 320 cases. |
  | 3 | `httpx` 0.28.1 only (lock) | `httpx` | 320 cases. |
  | 4 | both | `httpx,httpx2` | 356 cases. |
  | 5 | neither | `none` | pytest exit 1: the sentinel failed with its `UNPROVEN` message, the declaration test passed, and `egress_ci_check.py negative-control` accepted it; the step is green. |

  - *Means:* the workflow's steps pass in each library state a lab can be in. 320 against 356 is the parametrization over a second library in leg 4, not a lost file: the checker counts per file.
  - *Bar and why:* in legs 1 to 4, no failed, skipped or missing case, because a skip is how TD50 stayed hidden; in leg 5, the sentinel must fail, because a sentinel that passes with neither library installed can pass vacuously and would make every other leg's green mean less.
  - *Out of range:* a skipped or missing case names the file (the checker prints it); a leg 5 whose sentinel passes means the venv is not the `neither` state or the sentinel is vacuous.
  - *Where:* `.github/workflows/egress-guard.yml` (matrix and steps); `tests/egress_ci_check.py`; `tests/test_egress_httpx.py` (EG-0 and EG-0b).
  - *Not covered:* GitHub-hosted runners. Everything here ran on macOS (arm64) in prebuilt venvs, so the install steps, `setup-python`'s pip cache (the other four workflows already use it with the same `checkout@v4` and `setup-python@v5`), pip's resolution on the day and the 15-minute timeout are untested until the PR's first run.
- **Mutants of the pass-2 pieces.** Each was applied to a scratch export of `f2218b1c` (never the worktree) and run through 18 test files in the `ci` venv (the 13 egress files plus 5 that read `/api/airgap/status` or the doctor); the unmutated export gave 0 failing ids. The CI-shape mutants were also run through the leg-5 emulation.

  | Mutant | Caught by (new failing ids) | Leg 5 emulation |
  |---|---|---|
  | README says only `httpx` is wrapped | `test_egress_docs.py` (1) | |
  | PRIVACY known-gaps bullet loses `urllib3` | `test_egress_docs.py` (1) | |
  | `airgap.KNOWN_GAPS` loses the proxy entry | `test_egress_docs.py` (2) | |
  | `/api/airgap/status` stops listing a library the guard could not patch | `test_egress_docs.py` (1) | |
  | the CI checker stops counting skipped cases | `test_egress_ci_check.py` (4) | |
  | leg 5 declares `httpx2` instead of `none` | `test_egress_ci_check.py` (1) | red at the negative-control step |
  | the egress-tests step may run in the `neither` leg | `test_egress_ci_check.py` (1) | red at the egress-tests step |
  | the sentinel can no longer fail | `test_egress_ci_check.py` (1) | red at the negative-control step |
  | `open_notebook_seed` goes back to `import httpx` | `test_httpx_family.py` (3) | |
  | the doctor lists `httpx` only | `test_httpx_family.py` (1) | |

  - *Means:* each piece pass 2 added fails a test when it is broken, for its own reason.
  - *Bar and why:* at least one new failing id per mutant. A mutant nothing notices is a test that cannot fail for that reason.
  - *Out of range:* a mutant that nothing catches is a piece with no guard; none was found.
  - *Where:* the files named in the table. The 10 mutants do not exercise the guard itself again; Checkpoint 1's M1 and M2 did.
- **Documentation claims.** Six of the builder's 33 rows were checked against the tree, not copied: the four processes that install the guard are exactly the four `docs/PRIVACY.md` names (portal `app.py:903`, the agent loader `loader.py:315`, Model Forge's worker `nucleus/worker.py:39`, `doctor.py:100`); "Egress guard install failed" is written to the portal's activity log (`app.py:906`); nothing in `src/arail/` imports `urllib3`, `httpcore` or `httpcore2`; `DefaultAioHttpClient` exists in anthropic 2.3.0 and openai 3.42.1; the doctor records a failed `install_guard()` as a failed required check; the `grep` PRIVACY tells the reader to run is valid and runs (exit 0, 168 hits). The other 27 are for the architect.
- **`tests/test_queuellm_rename_frozen_surface.py`: the same 2 failures on `de1ce285` and on this branch, failure text identical** after normalising the tree path (diffed). Pass 2 edited `portal/app.py`, `airgap.py`, `doctor.py` and the modal, and the text still did not move. The criterion stays "the failure text does not change".
- **Open for the architect:** the builder's AF-2 and AF-3 (see Resume here), and AF-1 still to be written into `ARCHITECTURE.md`.

## Skipped phases

| Phase | Reason |
|---|---|
| think (visionary) | The win condition is stated by the operator's brief. |

## Notes

- The suite binds `127.0.0.1:8089` (Buddy's default edge port) while some tests run, and a run that is killed part-way leaves `real-boot` labs behind. The full-suite runs here are one pytest process per test file, a hard timeout, a scratch `HOME` and `TMPDIR`, one run at a time.
- Targeted egress tests do not bind a port and are safe to run alongside.
