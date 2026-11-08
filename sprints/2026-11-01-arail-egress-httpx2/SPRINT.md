# Sprint: ARAIL egress guard covers the httpx family

**ID:** 2026-11-01-arail-egress-httpx2
**Started:** 2026-11-01
**Product:** arail (`qukaizen-arail`)
**Branch:** `qukaizen/arail-egress-httpx2` (cut from `origin/main` `5997363e`, which is #210)
**Worktree:** `<session>/qukaizen-arail-egress-wt`
**Debt:** TD50 (the egress guard patches `httpx` only) and its sibling TD51 (code and tests that import `httpx` by name). Both were filed by PR [#211](https://github.com/cdarnell/qukaizen-arail/pull/211) (TD48's repair), which merged 2026-11-02 00:59 UTC as `de1ce285`; until then they existed in `sprints/BACKLOG.md` only on that branch, and the BACKLOG commit waited for it.

## Resume here (rewritten at every checkpoint)

- **State:** Shipped: PR [#212](https://github.com/cdarnell/qukaizen-arail/pull/212), merged 2026-11-08 23:26 UTC as `83a81951` (Checkpoint 6 has the first run on a GitHub runner). Branch head `65579b9a`, code head `b6ae5073`. Done: design; build passes 1 to 5; architect review (BLOCK) and re-review (WEAK_PASS); QA (WEAK_PASS); the orchestrator's re-runs at every checkpoint; commit 8 (BACKLOG); the full-suite "after" runs (record under Checkpoint 5). Next: nothing on this sprint. The operator's decisions are listed below.
- **Checked by re-running, not by reading a report:** the brief's probe in every venv; the five CI legs from the workflow's own `run:` text at Checkpoints 1 to 5 (the last on `b6ae5073`); QA's lazy-attribute probe; the frozen-surface failure text; the real `nav.js` under node in six states; mutants of my own at Checkpoints 1, 2, 4 and 5 (2, 10, 10 and 6; one of the ten at Checkpoint 4 survived and is now pinned by EG-A10); `shellcheck` over the workflow's run steps; the doc claims I could check against the tree.
- **Taken on a subagent's word:** the builder's 27 mutants and its doc-claim tables (pass 4); QA's 30 client shapes and its clean-machine run; the architect's own 17 mutants (16 caught; the survivor is R-N2, now asserted).
- **Run earlier and not repeated:** the install steps of the workflow in Linux containers (Debian 13, aarch64), on the pass-2 workflow. The install steps are unchanged since; pass 3 changed only the pytest flags (`-rsP`), a note line and the SDK-version print, which the emulation and `shellcheck` cover.
- **Cannot be checked on this machine:** anything on a GitHub-hosted runner (the install steps, `setup-python`'s cache, pip's resolution on the day, the 15-minute timeout, the schedule). The first real run of the five legs is the proof. Watch two things in it: leg 2 declares `httpx2` (Python 3.10 resolved 2.14.0 here), and leg 5 is green because its sentinel failed. Both held on the real run (Checkpoint 6). Also not run: a real browser (the badge and modal were run under node with a DOM stub), Python 3.12 and 3.13, Linux's default multiprocessing start method on 3.14.
- **Goes stale if `origin/main` moves:** the full-suite baseline (taken on `de1ce285`; `git ls-remote` still said `de1ce285` when the PR was prepared). Merge main again and re-run before merging if it moves.
- **For the operator (put in the PR):** (1) TD56, fail closed when the guard is incomplete (recommended by the architect and by me; it changes whether a lab starts); (2) TD61 / PX-1, a six-line fix to a stored-markup sink in `nav.js` that QA found and that this PR left alone on purpose; (3) the five `Egress guard` legs can become required checks after two green runs, and the daily schedule is one line.

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
| review | architect (review) | REVIEW.md | done | BLOCK on B1 (an incomplete guard left the portal serving with a library open and the UI saying otherwise, and the decision unstated), five WEAK findings, nits. |
| build, pass 3 (review fixes) | builder | BUILD_LOG.md | done, checked by the orchestrator | B1, W1 to W5 fixed. Re-run independently, see Checkpoint 3. |
| test | qa | TEST_REPORT.md | done | WEAK_PASS: QA-W1 (urllib shapes that skip the opener), QA-W2 (a lazy attribute that raises aborts the family step), QA-W3 and PX-1 (pre-existing, filed), eight nits. |
| build, pass 4 (QA fixes) | builder | BUILD_LOG.md | done, checked by the orchestrator | QA-W1, QA-W2 and the nits N1 to N8 fixed (QA-W3 and PX-1 out by decision). Re-run independently, see Checkpoint 4. |
| review (re-review) | architect (review) | REVIEW.md | done | WEAK_PASS, supersedes the BLOCK: R-W1 (Buddy), R-N1 to R-N5. |
| build, pass 5 (re-review fixes) | the orchestrating session | git log | done | R-W1 fixed, R-N2 asserted, and EG-A10 added for a surviving orchestrator mutant. R-N1, R-N3, R-N4, R-N5 and `admin.html:923` filed (TD61, TD63). Small enough (28 lines of source, 135 of tests, 3 of docs) and specified to the line by the architect, so the session did it rather than resume a builder context of about 760K tokens. |
| ship | none | PR [#212](https://github.com/cdarnell/qukaizen-arail/pull/212) | merged 2026-11-08 23:26 UTC as `83a81951` | 10 of 10 checks green on head `65579b9a`, including the first run of the five Egress guard legs on a GitHub runner (Checkpoint 6). The operator asked for the merge ("merge those changes"); the session that had started this one did it. |

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
| 2026-11-01 | **The B1 decision: with an incomplete guard the lab keeps serving, visibly. Fail closed is filed as TD56 and recommended; it is the operator's call.** When a family library is installed but the guard cannot patch it, `install_guard()` patches everything else and raises `EgressGuardIncomplete`. The portal and the agent loader catch it and keep serving with that library unguarded; only Model Forge's worker exits (non-zero, one readable line). Every surface an operator reads says so: the activity log, the doctor, `/api/system/mode`, `/api/airgap/status` (`guard_installed: false`, the definition qualified with "EXCEPT requests made through httpx2 ... NOT blocked" (for httpx2), one known-gaps entry), the modal's pill and gap list, the nav badge, and (since R-W1) Buddy's note on a switch back to airgapped. `docs/PRIVACY.md` says it in these words: "With an incomplete guard the portal and the agent loader keep serving with that library unguarded; only Model Forge's worker exits (with a message that names the library)." | The architect's first review (B1, BLOCK) found the decision unstated and the UI contradicting the guard; the fix was to make every surface agree and to write the decision down, not to change whether a lab starts. No supported version of either library reaches the state today (the tests simulate it), and the daily legs go red the day one does. Failing closed would stop a lab from starting over a library its operator may need, which is a product decision. |
| 2026-11-06 | QA-W3 (an `allow_egress` scope that survives a flip to airgapped) and PX-1 (the modal's Recent-activity rows are built with `innerHTML`) are not fixed in this PR. Filed as TD60 and TD61, with QA's validated fixes recorded. Decided at the start of pass 4; recorded here today. | Both are pre-existing on `main`. QA-W3 changes a decision at the three egress sites, which this PR keeps AST-identical to `main` (the architect checked 12 of 12), and this PR's job is coverage; for `httpx2` it moves from "never checked" to "checked, except inside a scope that outlives a flip". PX-1 is not about egress coverage, and `requests`, `httpx` and `httpx2` percent-encode the host, so only a `urllib` caller that passes markup reaches the sink. The architect called leaving PX-1 out "defensible, less comfortably" (this PR edits the same block of `nav.js`); it is a six-line fix and the operator can ask for it. |
| 2026-11-06 | QA-N7 (the always-visible nav badge) is in this PR. | It is the same fault as B1 on the one surface that is on every page: a green "Airgapped" next to a pill that says otherwise. |
| 2026-11-06 | Pass 5 is done by the orchestrating session, not a builder: R-W1 (Buddy's line, `severity` raised to `warn` when a library is open), R-N2 (one assertion), EG-A10 (the breadth of the containment, found by an orchestrator mutant), and a PRIVACY sentence with its pin. Each test was seen failing: against the unmodified code for R-W1 and the PRIVACY sentence, against a mutant of the code for EG-A10 and R-N2 (what they pin already held); each fix has mutants, and `git log` has one commit per item. | 28 lines of source, 135 of tests and 3 of docs, specified to the line by the architect. Resuming a builder context of about 760K tokens, which had already hit two usage limits, would have cost more than the change. The architect's re-review covers the code up to `0472dc49`; the pass-5 commits after it are the orchestrator's and are checked by the legs, the mutants and the full suite below, not by a second review. |
| 2026-11-06 | R-N1 (the doctor prints the incomplete message twice, under a "no cloud egress" suffix), R-N3 (the remedy calls a ceiling "tested"), R-N5 (the definition is qualified from `uncovered()`, not `guard_installed`), `admin.html:923` ("zero network calls") and R-N4 (the badge is pinned in CI only by regexes) are filed, not fixed: TD63 and TD61. | The architect rated them NIT and none is reachable on a supported library version; each touches wording or a test harness decision (node in CI) that deserves its own change. |
| 2026-11-06 | BACKLOG numbering: TD52 to TD63. The PR number in the TD50 and TD51 rows is 212, the next number after #211 when the PR was prepared (`gh api` listed #211 as the highest). | TD52 and up did not exist on `main` (`origin/main` is still `de1ce285`). If the PR gets another number, the two rows are corrected in a follow-up commit. |
| 2026-11-08 | The sprint session ended on an API safeguard error right after its last ledger commit, before it pushed. The session that had started it pushed the branch, opened PR #212 (the number the TD50 and TD51 rows already say) and, when the operator said "merge those changes", merged it after 10 of 10 checks passed. | The ledger's own next step was "push the branch and open the PR"; the merge was the operator's call and they made it. |
| 2026-11-08 | One scrub before the push (`65579b9a`): a machine-local scratchpad path named the macOS account in `TEST_REPORT.md`. | The repo is public. The scrub covers the tree, not the 51 earlier commits of history. |

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

## Checkpoint 3: the orchestrator's re-run after builder pass 3 (head `46099a64`)

Run by this session before QA started; the record below is from the hand-over note written at the time (2026-11-04), and the numbers were not re-measured for this file.

- **The five CI legs, from the workflow's own `run:` text:** 362 / 362 / 362 / 406 cases (httpx2-only Python 3.11, httpx2-only Python 3.10, httpx-only lock, both), none skipped, failed or missing; leg 5 failed its sentinel with `UNPROVEN` and the checker accepted that. The SDK step proved anthropic 2.5.0 and openai 3.46.0 refused.
  - *Means:* pass 3's fixes (B1, W1 to W5) kept every library state green. *Bar and why:* as Checkpoint 2. *Out of range:* as Checkpoint 2. *Where:* `.github/workflows/egress-guard.yml`, `tests/egress_ci_check.py`.
- **B1 re-probe:** with `httpx2.AsyncHTTPTransport` deleted, the status definition read "Cloud-provider APIs are blocked, EXCEPT requests made through httpx2 ... NOT blocked", where the first review had measured an unqualified "blocked" next to an async request that reached the network layer. *Where:* `portal/app.py::get_airgap_status`.
- **Frozen-surface failure text:** identical to `origin/main`'s (the same two failures, as at Checkpoints 1 and 2).
- **The install steps, for real:** all five legs' install steps ran in Linux containers (Docker, Debian 13, aarch64) on the pass-2 workflow; Python 3.10 resolved `httpx2` 2.14.0. The first attempt failed four legs with `bad substitution` because the emulator did not expand `${{ matrix.* }}` as GitHub does before the shell sees the text; that was the emulator's fault, not the workflow's, and all four went green once it did. The install steps have not changed since.
- **Baseline 2 on `de1ce285` and an early "after" on `46099a64`** (one pytest process per file, 3 at a time, 420 s cap; lock = `uv sync --frozen --extra dev --extra maximus`, CI-style = `pip install -e ".[dev]"`): baseline lock 511 files, 78 failed + 8 errors in 31 files; CI-style 511 files, 224 failed + 10 errors in 36 files. After, on 516 files (the 511 plus this branch's 5 new test files): the failing-id sets were identical to the baseline's in both venvs (86 and 234 ids, 0 only after, 0 only before), 0 timeouts.
  - *Means:* a warning check before QA, on the code as it stood. *Bar and why:* the same failing ids as `main`, because this PR changes the guard in every process that boots the portal. *Out of range:* an id only in "after" is a regression until a serial re-run on both trees says otherwise. *Where:* the baseline file `arail-egress-httpx2-baseline-de1ce285.json` in the session's notes folder (outside the repo).

## Checkpoint 4: the orchestrator's re-run after builder pass 4 (head `0472dc49`)

Nothing below is taken from the builder's or the architect's report; each item was run by this session today in the scratch venvs (the machine rebooted on 2026-11-05 and cleared `/private/tmp`; the builder rebuilt the venvs with the same versions: `ci` Python 3.11.17 with `httpx2` 2.16.0, `lock` `httpx` 0.28.1, `py310` Python 3.10.22 with `httpx2` 2.14.0, `both`, `neither`, `ci-sdk` with anthropic 2.5.0 and openai 3.46.0).

- **The five CI legs, from the workflow's own `run:` text:** leg 1 402 cases (the SDK step 3 cases), leg 2 402, leg 3 402, leg 4 455, each "none skipped, failed or missing"; leg 5 pytest `1 failed, 1 passed` and the checker `ok - the sentinel failed with UNPROVEN, as it must with neither library, and the declaration passed`. The builder's numbers were the same.
  - *Means:* every library state a lab can be in passes every case the egress job selects. 402 against 455 is the second library in leg 4. *Bar and why:* no failed, skipped or missing case in legs 1 to 4 (a skip is how TD50 stayed hidden); in leg 5 the sentinel must fail. *Out of range:* the checker names the file; a passing sentinel in leg 5 means the venv is not the `neither` state or the sentinel is vacuous. *Where:* `.github/workflows/egress-guard.yml`; `tests/egress_ci_check.py`.
- **QA's lazy-attribute probe** (the `both` venv, airgapped; `httpx2` replaced by a module whose `__getattr__` raises `ImportError` for the transport classes): the start-up function raised nothing and wrote one warning naming `httpx2`; `coverage()` said `httpx2: unguarded: patch raised ImportError: ...` and `httpx: guarded`; `/api/system/mode` said `{"mode": "airgapped", "guard_installed": false}`; the status definition read "EXCEPT requests made through httpx2: the guard could not patch it, so those requests are NOT blocked" and the gap list gained one `httpx2` entry; **`httpx` sync and async requests ended in `EgressBlocked`** (QA measured `ConnectError` on the tree before the fix: the abort left `httpx` unpatched while the UI said "blocked").
  - *Means:* QA-W2 is fixed where it hurt: the next library is still patched and every surface agrees. *Bar and why:* `EgressBlocked` for the healthy library and a qualified status, because the bug was a healthy library left open behind a "blocked" claim. *Out of range:* a `ConnectError` for `httpx` brings QA-W2 back. *Where:* `egress.py::_install_httpx_family_guard`.
- **`tests/test_queuellm_rename_frozen_surface.py`, on real checkouts** (this tree and a detached worktree of `origin/main` at `de1ce285`; an export skips both failing tests and would compare two empty outputs): 2 failed, 3 passed on both, and the 13 `E ` lines are identical after normalising the tree path.
  - *Means:* the two pre-existing failures hide nothing new. *Bar and why:* identical text; the test is red on `main`, so a changed line would be a frozen token lost or a frozen path touched. *Out of range:* a new or changed line names the file and token. *Where:* the test's `E ` lines.
- **The real `nav.js` under node** (a DOM stub; the status and mode JSON are what the real endpoint functions returned in each state):

  | State | Badge at load | Pill | Gap list | After a flip to hybrid, and back |
  |---|---|---|---|---|
  | whole guard, airgapped | `Airgapped`, green | `ok` | closed | `Hybrid`, then `Airgapped` (green) |
  | `httpx2.AsyncHTTPTransport` deleted, airgapped | **`Airgapped, guard incomplete`**, amber, title points at the modal | `warn` | **open** | `Hybrid`, then **`Airgapped, guard incomplete`** |
  | the lazy-attribute state, airgapped | as above | `warn` | open | as above |
  | incomplete, lab starts hybrid | `Hybrid` | `warn` | open | to airgapped: `Airgapped, guard incomplete`; back: `Hybrid` |
  | control: the `nav.js` of `15e3abc6` (before pass 4), incomplete, airgapped | `Airgapped`, **green** | `warn` | open | `Hybrid`, then `Airgapped` (green): QA-N7 reproduced |
  | the new `nav.js` against a server that sends no `guard_installed` | `Airgapped` | `warn` | open | n/a |

  - *Means:* the badge, which is on every page, cannot contradict the modal. *Bar and why:* in every incomplete state with the mode airgapped the text must say so and the class must be amber; a green "Airgapped" is the claim the guard knows is false. The last row is on purpose: a field that is missing is not claimed to mean "incomplete". *Out of range:* a plain "Airgapped" in an incomplete row is QA-N7 again. *Where:* `nav.js::updateBadge`, `portal/app.py::get_mode`. *Not covered:* a real browser; in CI the badge is pinned by regexes over the source (the architect's R-N4, filed in TD61).
- **Mutants of my own against pass 4's code** (ten, each a one-string change on an export of `0472dc49`; the 13 egress files through the runner in the `ci` venv; the unmutated export gave 402 cases and 0 failing ids; a control that removes the whole family patch gave 52 failing ids, so the harness runs the exported code):

  | Mutant | New failing ids |
  |---|---|
  | the containment of a failing lookup narrowed from `Exception` to `ImportError` | **0 (survived)**; pinned afterwards by EG-A10, which then fails 10 ids |
  | the contained failure loses the `unguarded:` prefix `uncovered()` keys on | 7 |
  | `_INSTALLED` set before `EgressGuardIncomplete` is raised | 16 |
  | the remedy no longer says to restart | 1 |
  | `/api/system/mode` always says the guard is whole | 3 |
  | `/api/system/mode` always says it is incomplete | 1 |
  | the badge reads a missing `guard_installed` as incomplete | 1 |
  | the modal pill ignores the guard again | 1 |
  | the doctor says `INCOMPLETE` for every install failure, not only for that one | 1 |
  | the worker writes its line to stdout | 3 |

  - *Means:* whether each pass-4 piece has a test that fails without it. *Bar and why:* at least one new failing id per mutant; a survivor is a change nothing pins. *Out of range:* the survivor was a real gap (nothing pinned how broad the catch is), now closed. *Where:* `tests/test_egress_httpx.py` (EG-A8 to EG-A10), `tests/test_egress_docs.py`, `tests/test_httpx_family.py`.
- **`shellcheck -s bash -S warning` over each `run:` step of the workflow:** clean. Its only warning (a constant `case` word) is an artefact of substituting `${{ matrix.* }}` for the check; the workflow's text has the expression.
- **The verdicts.** QA: WEAK_PASS (`TEST_REPORT.md`). The architect's re-review at this head: **WEAK_PASS** (`REVIEW.md`, "Re-review"), which supersedes the BLOCK: B1 is fixed on every surface an operator checks except Buddy's line (R-W1); W1 to W5, QA-W1 and QA-W2 are fixed; the three egress decision sites, the `allow_egress` ratchet, the bootstrap allowlist and the locality checks are AST-identical to `origin/main` (12 of 12); no frozen path is touched; 16 of its 17 mutants are caught. It required: commit 8, the B1 decision row (above), R-W1 fixed or filed, and left the nits to discretion.

## Checkpoint 5: after pass 5 (code head `b6ae5073`)

- **The five CI legs, from the workflow's own `run:` text:** leg 1 417 cases (the SDK step 3), leg 2 417, leg 3 417, leg 4 473, each "none skipped, failed or missing"; leg 5 green because its sentinel failed.
  - *Means:* as at Checkpoint 4, with pass 5's cases: +12 in every library state (EG-A10, two members times six exception types) and +3 (+6 in `both`) for Buddy. *Bar and why, out of range, where:* as at Checkpoint 4.
- **R-W1, red first.** The three new Buddy tests (the whole-guard line unchanged, the open-library line, the "Door's open" line left alone) were run against the unmodified watcher: the open-library case failed 1 id in `ci`, `lock` and `py310` and 3 in `both` (`httpx2`, `httpx`, both), and the other two passed. After the fix: 0 failing in all four venvs, and the three existing Buddy watcher files (22 cases) pass unchanged.
- **Mutants of my own against pass 5's code** (six; the 13 files in the `both` venv unless said): the architect's R-N2 mutant (the gap list keeps only the first unguarded library): 1 failing id in `both`, and by construction none in a one-library venv; Buddy: severity left at `info` (3), only the first library named (1), the "still go out" clause dropped (3), the helper always empty (3), the qualification never applied (3).
  - *Means:* each pass-5 change has a test that fails without it. *Bar and why:* at least one new failing id per mutant. *Out of range:* a survivor is a change nothing pins. *Where:* `tests/test_egress_docs.py`, `tests/test_egress_httpx.py`.
- **Full-suite "after" on the final tree** (`e2e20e02`: the code of `b6ae5073` plus the BACKLOG commit, in a clean detached checkout so the frozen-surface test can read git history as it does on `main`; one pytest process per file, 3 at a time, 420 s cap, scratch `HOME` and `TMPDIR`, the baseline's venv recipes; 516 files against the baseline's 511, because this branch adds five test files):

  | Venv | Cases run | Failing ids after / before | Only after | Only before | Timeouts |
  |---|---|---|---|---|---|
  | CI-style (`.[dev]`, floating) | 8,777 | 234 / 234 (224 failed + 10 errors, 36 files) | 0 | 0 | 0 |
  | Lock (`uv sync --frozen --extra dev --extra maximus`) | 8,876 | 87 / 86 (79 failed + 8 errors in 32 files, against 78 + 8 in 31) | 1, `tests/test_qa6_failclosed_and_bootstrap.py::test_perf_mount_regression_under_10pct` | 0 | 0 |

  - *The one id only after* is a wall-clock test: it compares the median of five timed mounts with a hook on against five with it off and allows 10 % plus 10 ms, and this PR does not touch `world_mount`. Re-run serially, one process, in the lock venv, three rounds on this tree and three on `origin/main`: this tree failed, passed, passed; `origin/main` passed, failed, passed. It flips on untouched `main` as well, so it is timing noise, not a regression.
  - *The wall times* (13.2 h for lock, 6.9 h for CI-style, against 591 s and 406 s at the baseline) are not the tests: the run started on the morning of 2026-11-06 and ended on the morning of 2026-11-07 on a machine shared with other sessions' work. No file timed out.
  - *Means:* the guard now patches `httpx2` in every process that boots the portal, so a test that boots the portal and then calls a public host through `httpx2` is the regression to look for; no test outside this branch's own changed its verdict. *Bar and why:* the same failing ids as `main` in the same venv. *Out of range:* an id only after is a regression until a serial re-run on both trees says otherwise, which is what was done for the timing test. *Where:* the baseline file `arail-egress-httpx2-baseline-de1ce285.json` in the session's notes folder (outside the repo); the junit files are in the session scratch directory.

## Checkpoint 6: the first run on a GitHub runner (PR #212, branch head `65579b9a`)

Read from the runner's logs on 2026-11-08, not from a report.

- **10 of 10 checks passed on the PR head:** the five `Egress guard` legs, DB-ensure, DaC, Golden path smoke, Model Forge and Import and route guardrail.
  - *Means:* the new workflow works on a hosted runner, and the three workflows #211 repaired stay green with this change. *Bar and why:* every check green, because `main` was green after #211, so a red here would be this PR's. *Out of range:* a red leg names its file. *Where:* the PR's checks.
- **Each leg ran in the state it declared** (versions printed by the runner): `httpx2` 2.16.0 only on Python 3.11, `httpx2` 2.14.0 only on Python 3.10, `httpx` 0.28.1 only from the lock (with `huggingface-hub` 1.25.1), both, and neither. The checker said "none skipped, failed or missing" for 417 cases in legs 1 to 3 and 473 in leg 4.
  - *Means:* Checkpoint 5's numbers, now on the runner; the 56 extra cases in leg 4 are the second library. *Bar and why:* no failed, skipped or missing case, because a skip is how TD50 stayed hidden. *Out of range:* the checker names the file. *Where:* `.github/workflows/egress-guard.yml`, `tests/egress_ci_check.py`.
- **The SDK step in leg 1:** `anthropic` 2.6.0 and `openai` 3.46.0 on `httpx2` 2.16.0, 3 cases, none skipped, failed or missing.
  - *Means:* the current cloud SDKs, which need `httpx2` and not `httpx`, are guarded; this is the claim the README makes. *Bar and why:* the SDK versions the run proves are printed, so a green run says which SDKs it covered. *Out of range:* a case that did not run fails the leg. *Where:* the same workflow, SDK step.
- **Leg 5 (neither library):** the sentinel failed with UNPROVEN and the checker said "the sentinel failed with UNPROVEN, as it must with neither library, and the declaration passed".
  - *Means:* the proof cannot pass vacuously. *Bar and why:* the sentinel must fail here. *Out of range:* a passing sentinel means the venv is not the "neither" state or the sentinel is empty. *Where:* `tests/test_egress_httpx.py::test_httpx_family_coverage_is_provable_in_this_environment`.
- **The two things "Resume here" said to watch both held:** leg 2 declared and ran `httpx2` (2.14.0 on Python 3.10), and leg 5 was green because its sentinel failed.

## Skipped phases

| Phase | Reason |
|---|---|
| think (visionary) | The win condition is stated by the operator's brief. |

## Notes

- The suite binds `127.0.0.1:8089` (Buddy's default edge port) while some tests run, and a run that is killed part-way leaves `real-boot` labs behind. The full-suite runs here are one pytest process per test file, a hard timeout, a scratch `HOME` and `TMPDIR`, one run at a time.
- Targeted egress tests do not bind a port and are safe to run alongside.
