# Sprint: arail-bundled-aerollm

**ID:** 2026-08-05-arail-bundled-aerollm
**Started:** 2026-08-05
**Shipped:** 2026-08-05
**Product:** arail

## Task

Bundle a prebuilt aeroLLM `aerollm_api` native extension into ARAIL's own
distribution ("Option B") so outside users get the deep-mode 2nd-inference
backend without aeroLLM source access, private-index credentials, or a
public-PyPI aeroLLM package. Maintainer decision context: aeroLLM's public
1.0 cut is deferred (see qukaizen-aerollm
`sprints/2026-07-28-public-1.0-cut/`, ship phase deferred 2026-08-02);
compiled-binary public distribution explicitly approved by the maintainer
("totally acceptable to have a published compiled binary… I just don't
want to open the project up publicly yet but happy to share with anyone").

## Phases

| Phase | Subagent | Artifact | Status | Verdict |
|---|---|---|---|---|
| think | visionary | — | skipped | Win condition fixed by maintainer decision; execution sprint |
| plan | architect (design) | ARCHITECTURE.md | done | GitHub-Release-asset host recommended over LFS/private bucket |
| build | builder | BUILD_LOG.md | done | BUNDLED channel, producer script, compliance files, real tarball |
| review | architect (review) | REVIEW.md r1 | done | BLOCK — B1 filename mismatch (default path 404s), B2 tag pin never forwarded |
| build (fix r2) | builder | BUILD_LOG.md | done | B1/B2 + license-stub + dirty-manifest + sha-claim fixes |
| review (r2) | architect (review) | REVIEW.md r2 | done | BLOCK — B3 test destroyed the release artifact, B4 inflated test counts, B5 doc reintroduces stub license |
| build (fix r3) | builder | BUILD_LOG.md | done | OUT_DIR isolation, deterministic tests, doc fix, artifact re-cut |
| review (r3) | architect (review) | REVIEW.md r3 | done | PASS — everything reproduced personally, nothing on trust |
| test | qa | TEST_REPORT.md | done | FAIL — Q1 unusable checksum-pin docs, Q2 acceptance recipe selects wrong channel, Q5 no pre-import validation (PoC code exec), Q7 wrong onboarding hint |
| build (fix r4) | builder | BUILD_LOG.md | done | Q1/Q2/Q5/Q7 + Q4 fixed; Q6 deliberately deferred (test-pinned anchor) |
| test (re-verify) | qa | TEST_REPORT.md r2 | done | WEAK_PASS — all blockers closed by execution; residual R2-1 |
| fix (r5, orchestrator) | — | BUILD_LOG.md r5 | done | R2-1 closed: standalone route self-serves the pyproject sha256 pin |
| ship | maintainer + orchestrator | GitHub Release v1.1.0 | **done 2026-08-05** | Merged to main (ff, 32 commits), tag `v1.1.0` pushed, release created with both assets by the maintainer, post-publish E2E verified (see below) |

## Post-publish verification (2026-08-05)

Real-download acceptance run — fresh venv, `ARAIL_AEROLLM_REPO=/nonexistent`,
no bundle env vars set:

- Downloaded `aerollm-api-v1.1.0-macos-arm64.tar.gz` from the live
  GitHub Release (the one path QA could not test pre-upload).
- Checksum verified against the **pyproject-self-served pin**
  (`d3a7a9dd1998…`) — i.e. R2-1's fix fired on the real path.
- Security disclosure line printed; install + import OK; **EXIT 0**.

## Open items / follow-ups

- **~~Bundled runtime reports version `0.1.0`~~ — RESOLVED 2026-08-06:**
  bundle re-cut from aerollm `main` @ `2ae56af` (v1.0.0, includes all
  merged MoE runtime work), v1.1.0 release assets swapped in place
  (`--clobber`), pyproject sha pin + BUNDLE.json refreshed, live-download
  E2E re-verified (checksum OK, imports, reports 1.0.0). During the CDN
  propagation window the installer correctly REFUSED the stale asset on
  checksum mismatch — the pin's fail-closed design observed working
  against real infrastructure. Note: aerollm's `1.0.1` release branch
  remains unmerged (20-conflict divergence vs main — deliberately left
  as its own future integration sprint, not auto-resolved here);
  `1.0.1` is a PyPI-version-reuse artifact and does not apply to the
  bundle. The earlier tarball's `d3a7a9dd…` digest is superseded by
  `57f30364…`.
- **Q3**: stale `aerollm-api` `0.1.0rc1`/`0.1.0rc2` wheels on public
  PyPI — maintainer decided to **yank**; done via the PyPI web UI by the
  maintainer (not automatable here).
- **Q6** (carried): over-broad F7 provenance guard misreports an
  interrupted bundle install; QA's test pins current behavior as the
  regression anchor for a dedicated follow-up.
- Carried minor ASKs from review/QA: Q8, A7, A5, A4, R2-2 (wording) —
  see TEST_REPORT.md / REVIEW.md.
