# Sprint: ARAIL egress guard covers the httpx family

**ID:** 2026-11-01-arail-egress-httpx2
**Started:** 2026-11-01
**Product:** arail (`qukaizen-arail`)
**Branch:** `qukaizen/arail-egress-httpx2` (cut from `origin/main` `5997363e`, which is #210)
**Worktree:** `<session>/qukaizen-arail-egress-wt`
**Debt:** TD50 (the egress guard patches `httpx` only) and its sibling TD51 (code and tests that import `httpx` by name). Both were filed by PR [#211](https://github.com/cdarnell/qukaizen-arail/pull/211) (TD48's repair), so until #211 merges they exist in `sprints/BACKLOG.md` only on that branch.

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
| build | builder | BUILD_LOG.md | pending | |
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

## Skipped phases

| Phase | Reason |
|---|---|
| think (visionary) | The win condition is stated by the operator's brief. |

## Notes

- The suite binds `127.0.0.1:8089` (Buddy's default edge port) while some tests run, and a run that is killed part-way leaves `real-boot` labs behind. The full-suite runs here are one pytest process per test file, a hard timeout, a scratch `HOME` and `TMPDIR`, one run at a time.
- Targeted egress tests do not bind a port and are safe to run alongside.
