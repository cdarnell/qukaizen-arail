# Review: Bundled AeroLLM — a third install channel

**Date:** 2026-08-05
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) at `26228b5`
**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `c83db2f`
**Reviewed range:** `c83db2f^..26228b5` (10 commits)

## Verdict: BLOCK

Two defects break the headline user journey this sprint exists to deliver:
an outside user with no sibling repo and no index credentials getting the
2nd inference. Both are cheap to fix. Everything else in the sprint is
genuinely good work — the security posture of `bundle_install()` is sound
and I verified it by execution, not by reading.

---

## What I verified by running it, not by reading

| Check | Result |
|---|---|
| `THIRD-PARTY-LICENSES/aerollm/{LICENSE,NOTICE}` byte-identical to sibling repo | ✅ sha256 match (`608a1e3a…`, both 770 B / 384 B) |
| LICENSE/NOTICE embedded *inside* the tarball too | ✅ identical shas to repo copies |
| Tarball ↔ sidecar ↔ manifest internal consistency | ✅ sidecar = real tarball sha `0d602a69…`; manifest `sha256` = real `.so` sha `5e7c8e0c…` |
| sha256 verified **before** any copy | ✅ verify at L224–242, first `cp` at L261 |
| Fail-closed on tampered tarball | ✅ refused, exit 1, nothing installed |
| Fail-closed when **no** digest is obtainable | ✅ refused ("Refusing to install an unverified artifact") |
| F7 guard: existing `.so` with no provenance marker | ✅ refused without `--force` |
| Acceptance §9.1 (offline, no sibling, unreachable index) | ✅ **reproduced in a fresh venv** — `channel: bundled`, importable, exit 0 |
| Producer refuses a dirty aeroLLM worktree | ✅ code path correct (builder used `ALLOW_DIRTY=1`; see A2) |
| No new commits/pushes in sibling aeroLLM repo | ✅ HEAD still `9e08230`; dirt is pre-existing grammar work only |
| No `gh release` executed | ✅ appears only in doc text / an `info` echo |
| `shellcheck` both scripts | ✅ clean; `bash -n` clean |
| `AEROLLM_CHANNEL` leakage | ✅ confined to `build-aerollm.sh` + docs |
| New tests | ✅ 12/12 pass |
| Pre-existing-failure claim | ✅ **independently reproduced**: identical 9 failures at `c83db2f^`; 129→141 passed. Zero regressions. |

---

## Code quality findings

- **[BLOCK] B1 — the producer and the consumer disagree on the asset
  filename, so the default bundled channel 404s by construction.**
  `package-aerollm-bundle.sh:128-129` names the artifact
  `aerollm-api-${AEROLLM_VERSION}-${SHORT_COMMIT}-macos-arm64.tar.gz`
  → the real file on disk is `aerollm-api-0.1.0-9e08230-macos-arm64.tar.gz`.
  `resolve_bundle_url()` (`build-aerollm.sh:142-143`) requests
  `aerollm-api-${TAG}-macos-arm64.tar.gz`
  → `…/releases/download/v1.1.0/aerollm-api-v1.1.0-macos-arm64.tar.gz`.
  `docs/releasing.md:33` uploads the producer's name verbatim (`*.tar.gz`)
  and never instructs a rename. So the very first outside user to run
  `./arailctl deep install` gets a 404.
  This reclassifies the builder's disclosed gap: the GitHub path is not
  merely *untested*, it is *provably broken*. Fix by deriving one name from
  one source (simplest: have the producer emit the tag-named asset).

- **[BLOCK] B2 — `aerollm_bundle_tag` is never plumbed into
  `AEROLLM_BUNDLE_TAG`; three docs claim it is.**
  `pyproject.toml:258` pins `aerollm_bundle_tag = "v1.1.0"`, and
  `build-aerollm.sh:53-54`, `docs/cli.md:338` and
  `THIRD-PARTY-LICENSES/aerollm/README.md:21,39` all state that `setup.sh`
  overrides the env var from it. It does not — `setup.sh:659` forwards only
  `AEROLLM_INDEX_URL` and `AEROLLM_PIP_SPEC`. The only reader of the pin is
  the compliance *test*. Consequence: the hardcoded default at
  `build-aerollm.sh:57` is the true source of truth, and a maintainer
  following `docs/releasing.md` step 4 (bump the pin) will see the drift
  test go green while the download URL silently does not move. Either wire
  it through `setup.sh` or delete the claim and document the constant as
  authoritative.

- **[ASK] A5 — `_release_creds_configured()` under-detects, diverting
  maintainers to the bundled channel.** It checks `PIP_INDEX_URL`,
  `PIP_EXTRA_INDEX_URL`, a non-default `AEROLLM_INDEX_URL`, and `~/.netrc`.
  It misses `pip.conf` / `PIP_CONFIG_FILE` / keyring — the most common way
  to configure a private index. Such a machine now silently gets BUNDLED
  where it used to get RELEASE. The CHANGELOG discloses the fallthrough
  change honestly, but names `AEROLLM_CHANNEL=release` as the remedy
  without noting pip.conf users are affected.

- **[INFO] A4 — the extraction comment overclaims.** `build-aerollm.sh:246-248`
  says copying out four filenames means "a tarball with unexpected/`../`
  entries can't escape", but `tar xzf` has already extracted everything by
  then; the selective copy limits what is *installed*, not what is
  *written*. Real risk is low (post-checksum, maintainer-produced artifact),
  but the comment should say what the code does. `--no-same-owner
  --no-xattrs` and an explicit member list would make it true.

- **[INFO] Version-universe inconsistency (pre-existing, surfaced here):**
  `pyproject.toml` pins `aerollm = "aerollm-api>=1.0,<2.0"`, `setup.sh:133`
  uses `>=0.1,<0.2`, and the bundle ships `0.1.0`. Not introduced by this
  sprint, but the bundled channel now makes the disagreement user-visible.

## Security findings

- **[INFO] A3 — the digest is a corruption check, not an authenticity
  control.** When `AEROLLM_BUNDLE_SHA256` is unset, the expected digest is
  fetched from `${url}.sha256` — same origin, same transport, same trust
  boundary as the tarball (L216-219). Anyone who can serve the artifact can
  serve a matching digest. Combined with `AEROLLM_BUNDLE_URL` accepting any
  https origin, a malicious mirror is fully trusted. The real integrity
  guarantee here is GitHub's TLS, not the sha256. This is an acceptable
  posture for v1 — but `CHANGELOG.md` and `docs/releasing.md` should not
  let a reader infer supply-chain integrity. The committed
  `BUNDLE.json.sha256` is the trustworthy pin; recommend the installer
  prefer it over the sidecar.
- ✅ https-only scheme check before `curl` (L202-205). Good.
- ✅ Platform guard fires before any network call (L148-152). Good.
- ✅ F1 rollback removes a broken artifact rather than leaving it shadowing
  a future good install (L264-271). Verified by reading; matches design.
- ✅ No secrets, no credential handling, no new dependencies.

## Test coverage assessment

12 new tests, all passing; 141 passed / 9 failed under `-k aerollm`, with
the 9 failures independently reproduced on the pre-sprint tree. Coverage of
the design's failure table is good for F1/F2/F4/F5/F7/F10/F11.

**Gap:** no test asserts that the producer's output filename is the one the
consumer requests. That single assertion would have caught B1. It is the
required regression test for this fix.

## Tech debt delta

Roughly as ARCHITECTURE.md predicted (a manual, uncached release step), with
two unanticipated items: the dead `aerollm_bundle_tag` plumbing (B2) and the
unused `AEROLLM_BUNDLE_ASSET` variable declared at L58 but never read.

- **[ASK] A2 — the shipped pin has dirty provenance.**
  `THIRD-PARTY-LICENSES/aerollm/BUNDLE.json` is committed with
  `"aerollm_dirty": true` and a `modifications` string admitting it is not a
  verbatim build of `9e08230`. The mechanism worked exactly as designed —
  it told the truth — but a compliance manifest asserting an
  unattributable build should not be the committed pin for `v1.1.0`.
  Re-cut from a clean worktree before tagging.

- **[ASK] A1 — the bundled `LICENSE` is a stub, not the Apache-2.0 text.**
  This is the one my own design got wrong: I specified "byte-identical to
  upstream" as the acceptance criterion, and the builder met it exactly.
  But upstream's `LICENSE` is 17 lines — the Apache *header boilerplate*
  plus a copyright line — not the ~200-line license. Apache-2.0 §4(a)
  requires giving recipients "a copy of this License," and the adjacent
  `NOTICE` says "See the LICENSE file for the full license text," which is
  false as shipped. Since redistributing a compiled Object form is the
  entire premise of this sprint, byte-identity was the wrong test.
  Recommended: fix upstream in `qukaizen-aerollm` (full license text), then
  re-sync — that repairs both repos. My design defect, not the builder's.

## Required actions before merge

1. **B1** — make the producer emit, or `releasing.md` explicitly rename to,
   the exact filename `resolve_bundle_url()` requests. Derive both from one
   source.
2. **B1-test** — add a regression test asserting producer output name ==
   consumer requested name.
3. **B2** — either wire `aerollm_bundle_tag` → `AEROLLM_BUNDLE_TAG` through
   `setup.sh`, or delete the three claims that it already happens.
4. **A1** — replace the stub `LICENSE` with the full Apache-2.0 text
   (upstream first), or correct the NOTICE sentence.
5. **A2** — re-cut `BUNDLE.json` and the tarball from a clean aeroLLM
   worktree before tagging `v1.1.0`.
6. **A3/A5/A4** — acceptable as follow-ups if ticketed: prefer the committed
   `BUNDLE.json.sha256` over the same-origin sidecar; widen
   `_release_creds_configured()` to `pip.conf`/`PIP_CONFIG_FILE`; correct
   the extraction comment. Remove the unused `AEROLLM_BUNDLE_ASSET`.

Once 1–5 land, this is a PASS. The hardware-pending chat-turn gap is
acceptable to ship disclosed — the `.so` demonstrably loads and imports in a
clean interpreter, which is the part this sprint owns.

---

# Review — Round 2

**Date:** 2026-08-05
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) at `15e7021`
**Reviewed range:** `33fdc54..15e7021` (5 commits)

## Verdict: BLOCK

Round 1's two BLOCKs (B1, B2) are genuinely fixed and I verified both by
execution. The A1 licence fix is correct and I verified it word-for-word
against apache.org. But the *fix for B1* introduced a worse defect than B1
itself: the new regression test `rm -rf`s the real release output directory,
and it fails on a normal developer machine. The artifact this sprint exists
to ship no longer exists on disk, and `docs/releasing.md` step 3 as written
would today upload a fake test tarball.

## What I verified by running it, not by reading

| Check | Result |
|---|---|
| B1 — producer/consumer filename convergence | ✅ producer `package-aerollm-bundle.sh:151` and `resolve_bundle_url()` `build-aerollm.sh:141-142` both derive `aerollm-api-<tag>-macos-arm64.tar.gz` from `$ARAIL_RELEASE_TAG` / `$AEROLLM_BUNDLE_TAG` alone. Dead `AEROLLM_BUNDLE_ASSET` removed. |
| B2 — pin bump moves the URL | ✅ **traced end-to-end.** Bumped `aerollm_bundle_tag` to `v7.7.7-trace` in a copy, drove the real `load_pyproject_metadata()` (setup.sh:146-182) → `AEROLLM_BUNDLE_TAG=v7.7.7-trace` → consumer requested `…/download/v7.7.7-trace/aerollm-api-v7.7.7-trace-macos-arm64.tar.gz`. Empty-value skip at `setup.sh:175-177` correctly preserves the hardcoded default. Forwarded at `setup.sh:664-666`. |
| A1 — shipped LICENSE is genuine Apache-2.0 | ✅ fetched `apache.org/licenses/LICENSE-2.0.txt` and diffed: **word-for-word identical** (204 vs 202 lines; every delta is line re-wrapping) with the `[yyyy]/[name]` appendix placeholder filled in. Producer now stages ARAIL's copy (`:117`), so tarball == repo copy. |
| NOTICE accuracy given upstream's stub | ✅ "See the LICENSE file for the full license text" is now true for what ships. Correctly left byte-identical to upstream; upstream's own stub is out of scope, and `test_aerollm_bundle_compliance.py:61-79` documents exactly why byte-identity was dropped for LICENSE. |
| A3 — sha256 language softened | ✅ `docs/cli.md:346-355` now states plainly it is corruption-detection, not authenticity; names GitHub TLS + repo access as the real trust boundary; points at the committed `BUNDLE.json` for an out-of-band digest. Correct and honest. |
| Bundled channel works end-to-end under the corrected filename | ✅ fresh py3.9 venv, `ARAIL_AEROLLM_REPO=/nonexistent`, `HOME` redirected, no `PIP_*`/`AEROLLM_INDEX_URL` → `auto` chose bundled, checksum verified, installed, `import aerollm_api` OK, exit 0. |
| `shellcheck -x` on the two bundle scripts | ✅ clean. `setup.sh` has 16 findings, **all pre-existing** and none on the lines touched this round (137-138, 173, 664-666). |
| aeroLLM sibling repo untouched | ✅ `HEAD` still `9e08230`, reflog shows no new commits, working-tree dirt is the same pre-existing `aerollm-grammar` work. The temporary worktree is gone (`git worktree list` shows none for this sprint). |
| No `gh release` executed | ✅ only in doc text and an `info` echo (`package-aerollm-bundle.sh:8,158`). |
| `pytest -k aerollm` | ❌ **140 passed / 13 failed** — not the claimed 144/9. See B4. |

## Code quality findings

- **[BLOCK] B3 — the new regression test destroys the release artifact.**
  `package-aerollm-bundle.sh:107` does `rm -rf "$OUT_DIR"` on the hardcoded
  `$REPO_ROOT/dist/aerollm-bundle`, and
  `test_producer_filename_matches_consumer_resolved_filename` invokes the
  **real** producer with `cwd=REPO` and no `OUT_DIR` override. I demonstrated
  this: I placed a sentinel `dist/aerollm-bundle/aerollm-api-v1.1.0-macos-arm64.tar.gz`,
  ran that one test, and it was gone. Consequences, all live right now:
  1. The `v1.1.0` tarball BUILD_LOG.md says was re-cut from the clean
     worktree **does not exist anywhere on disk** (`find /` → nothing). Its
     `.so` digest `e2944cf6…` does not match the sibling's
     `target/release/libaerollm_api.dylib` (`5e7c8e0c…`, the round-1 dirty
     build), so I cannot verify `BUNDLE.json` against any artifact.
  2. `dist/aerollm-bundle/` today contains only
     `aerollm-api-v9.9.9-regression-test-macos-arm64.tar.gz` — a 4.9 KB
     tarball wrapping a **0-byte** `.so`. `docs/releasing.md:50`'s
     `gh release upload <tag> dist/aerollm-bundle/*.tar.gz` would upload
     precisely that.
  Fix: make `OUT_DIR` overridable (`OUT_DIR="${OUT_DIR:-$REPO_ROOT/dist/aerollm-bundle}"`)
  and have the test point it at `tmp_path`. Then re-cut and re-commit
  `BUNDLE.json` from a real clean-worktree build, and keep the artifact.

- **[BLOCK] B4 — the B1 regression test is ambient-venv-dependent and fails
  on a normal DEV machine.** `_run()` (`tests/test_aerollm_bundle_install.py:51-56`)
  neither isolates the target `site-packages` nor passes `--force`, so on any
  machine with a DEV or RELEASE `aerollm_api.abi3.so` already installed, the
  F7 provenance guard aborts `bundle_install()` **before** `resolve_bundle_url()`
  is ever reached. The test then asserts a filename against a "Refusing to
  overwrite" message and fails. Four tests in that file behave this way
  (`checksum_mismatch_aborts_before_any_write`, `curl_failure_names_resolved_url`,
  `https_only_scheme_guard`, `producer_filename_matches_consumer`). They
  passed for the builder only because the §9.1 acceptance run had left a
  bundle-marked `.so` in the dev venv. Net: the exact regression test round 1
  required does not actually assert anything on a clean DEV machine, and the
  count is **140/13**, not 144/9. Fix: `--force`, or install into a
  per-test `PYTHONPATH`/venv.

- **[BLOCK] B5 — `docs/releasing.md` step 4 instructs the maintainer to undo
  the A1 fix.** Lines 57-59 say to refresh `LICENSE` and `NOTICE` "if
  aeroLLM's upstream files changed (byte-diff against
  `~/ProJects/qukaizen-aerollm/{LICENSE,NOTICE}`)". Byte-syncing `LICENSE`
  from upstream reinstalls the 17-line stub, re-breaks the NOTICE sentence,
  and fails `test_aerollm_bundle_compliance.py:79`. The instruction must be
  split: `NOTICE` syncs from upstream; `LICENSE` is deliberately ARAIL's
  full Apache-2.0 text and must not be synced (with a one-line why).

- **[ASK] A6 — `BUNDLE.json`'s `aerollm_dirty: false` is plausible but not
  independently verifiable.** The value is computed by the script, not
  hardcoded (`package-aerollm-bundle.sh` derives it from `git status` in the
  build tree), and the recorded commit `9e08230f0be…` matches the sibling's
  HEAD, so I have no reason to doubt it. But because of B3 the artifact it
  describes is gone, so "genuinely clean" rests entirely on BUILD_LOG.md's
  narrative. Once B3 is fixed and the bundle re-cut, this resolves by
  construction — do not ship the current `BUNDLE.json`.

- **[ASK] A5, A4 (carried from round 1)** — `_release_creds_configured()`
  still misses `pip.conf`/`PIP_CONFIG_FILE`/keyring; the extraction comment
  still overclaims. Both remain acceptable as ticketed follow-ups.

## Security findings

- ✅ Re-verified: https-only scheme guard, platform guard before any network
  call, checksum-before-copy ordering, fail-closed on an unobtainable digest,
  F7 provenance guard (which I confirmed by *tripping* it, see B4).
- ✅ No new dependencies, no credential handling, no secrets.
- **[INFO]** A3's honest framing in `docs/cli.md` is the right posture.
  `CHANGELOG.md:15` says only "sha256 verification happens before any file is
  copied", which is accurate and does not overclaim — no change needed.

## Test coverage assessment

3 new tests this round. The compliance test's LICENSE marker + line-count
assertion (`>150` lines, section headers) is a sound replacement for the
byte-identity check and encodes the reasoning inline. But the headline new
test is defective in two independent ways (B3, B4), which is the opposite of
what a regression test for a shipping-blocker should be.

## Tech debt delta

Round 1's debt is repaid (dead `AEROLLM_BUNDLE_ASSET` gone, pin wired). New
debt: a non-overridable `OUT_DIR` in the producer and a test harness that
mutates the developer's real venv and real `dist/`.

## Required actions before merge

1. **B3** — make `OUT_DIR` overridable; point the test at `tmp_path`. Verify
   by running the test with a sentinel artifact in `dist/aerollm-bundle/`.
2. **B3b** — re-cut the `v1.1.0` bundle from a clean worktree, keep the
   artifact on disk, and re-commit `BUNDLE.json` to match it. Delete the
   stray `aerollm-api-v9.9.9-regression-test-*.tar.gz` from `dist/`.
3. **B4** — isolate `_run()`'s install target (or pass `--force`) so all
   four affected tests pass on a machine with a pre-existing DEV/RELEASE
   `aerollm_api`. Report the pass/fail count from a DEV-state venv.
4. **B5** — split `docs/releasing.md` step 4: sync `NOTICE` from upstream,
   never `LICENSE`.
5. **A5/A4** — ticket as follow-ups.

B1, B2, A1 and A3 are closed. Once 1-4 land this is a PASS; nothing here
requires a design change. Do not run `gh release upload` until B3b produces
a verified artifact.

---

# Review — Round 3

**Date:** 2026-08-05
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) at `d29e53b`
**Reviewed range:** `49d68d4..d29e53b` (4 commits)

## Verdict: PASS

All three round-2 BLOCKs (B3, B4, B5) are genuinely closed. Given that I was
burned twice by claimed-but-unreproduced results, **nothing below is taken on
trust** — every row was reproduced by execution on this machine.

## What I verified by running it, not by reading

| Check | Result |
|---|---|
| B3 — `OUT_DIR` overridable | ✅ `package-aerollm-bundle.sh:50` `OUT_DIR="${OUT_DIR:-$REPO_ROOT/dist/aerollm-bundle}"`; test sets it to `tmp_path/out` and runs with `cwd=tmp_path` |
| B3 — **sentinel proof** (the round-2 catch, repeated) | ✅ planted `dist/aerollm-bundle/SENTINEL.txt` + sha-pinned the real tarball, ran the **full** `-k aerollm` suite twice: sentinel present after both runs, tarball `shasum -c` OK both times. The round-2 defect is genuinely gone. |
| B3b — stray `v9.9.9-regression-test` tarball removed from `dist/` | ✅ `dist/aerollm-bundle/` holds only the real v1.1.0 tarball + sidecar + `stage/` |
| B4 — 144 passed / 9 failed | ✅ **reproduced twice**, identical both runs (9.42 s / 9.00 s), from a shell with `PYTHON`/`VIRTUAL_ENV`/`PIP_INDEX_URL`/`PIP_EXTRA_INDEX_URL`/`AEROLLM_INDEX_URL`/`AEROLLM_CHANNEL`/`AEROLLM_BUNDLE_TAG`/`OUT_DIR` all `env -u`'d. Second run with `-p no:randomly` — no order dependence. |
| B4 — the 9 failures are the exact set named in BUILD_LOG | ✅ `test_aerollm_defaults.py` ×4, `test_aerollm_model_ready.py` ×3, `test_model_ux_phase0_warmth_probe.py` ×2 — same set round 1 independently reproduced on the pre-sprint tree |
| B4 — `_isolated_python()` really isolates | ✅ read it: builds a genuine `sys.executable -m venv` in a `mkdtemp` dir and returns `venv/bin/python3`; `_run()` sets `PYTHON` to it unless the caller supplied one. Not a claim — a real empty venv. The per-test `isolated_python` fixture is correctly kept for the one test that completes an install. |
| B5 — `docs/releasing.md` step 4 | ✅ `:57-69` now says NOTICE syncs from upstream, **"LICENSE — DO NOT sync this from upstream"**, with the 17-line-stub rationale and a pointer at the guarding test |
| B5 — the guard test has teeth | ✅ **reproduced end-to-end**: copied upstream's 17-line `~/ProJects/qukaizen-aerollm/LICENSE` over the bundle copy → `test_license_is_full_apache2_text_not_upstream_stub` FAILED (`:78`); restored → 5/5 green. Tree left clean. |
| Artifact exists and is non-trivial | ✅ `dist/aerollm-bundle/aerollm-api-v1.1.0-macos-arm64.tar.gz`, 6,791,725 B |
| tarball sha == `.sha256` sidecar | ✅ `d3a7a9dd19987350963422ab7647a2d4ad607e78397b57f70c55362c0b95ecce` both |
| `BUNDLE.json.sha256` field == real `.so` digest | ✅ extracted `.so` hashes to `2776d188f71c98bfb46b1e87f2f5e8aa4f30146541d0748e3321cd0364650f9a` = `BUNDLE.json.sha256` field exactly |
| the `.so` is a real binary, not round-2's 0-byte fake | ✅ 22,187,632 B, `Mach-O 64-bit dynamically linked shared library arm64`; **and it imports** — dropped into a fresh venv's site-packages, `import aerollm_api` OK |
| in-tarball LICENSE/NOTICE == repo copies | ✅ `diff` clean both (11,389 B / 384 B) |
| in-tarball `MANIFEST.json` == committed `BUNDLE.json` | ✅ `diff` clean; `aerollm_dirty: false`, `arail_release: v1.1.0`, commit `9e08230f0be…` |
| sibling aeroLLM repo untouched | ✅ `HEAD` = `9e08230f0be…` (unchanged); reflog `HEAD@{0}` is the pre-existing grammar commit — **zero new commits**; working-tree dirt is the same pre-existing `aerollm-grammar`/`CLAUDE.md`/`Untitled.md` set; `git worktree list` shows **no** worktree from this sprint (the 10 listed all predate it) |
| No `gh release` executed | ✅ occurs only as doc text (`docs/releasing.md:50`), a script header comment, and an `info` echo (`package-aerollm-bundle.sh:8,163`). Unrelated `build_ai_eng.py` hits are printed strings. |
| `shellcheck -x` both touched scripts | ✅ clean, zero findings |
| arail working tree after all my probing | ✅ `git status --porcelain` empty |

## Code quality findings

- **[ASK] A7 — the in-test "real dist untouched" guard is ordered wrong and
  is therefore vacuous.** In `test_producer_filename_matches_consumer_resolved_filename`,
  `real_out_snapshot_before` is computed at `:232` — **after** the producer
  subprocess has already run at `:226`. If a future regression re-pointed
  `OUT_DIR` at the real directory, the wipe would happen before the "before"
  snapshot, and `before == after` would still hold. The assertion cannot
  detect the exact failure it documents. Move the snapshot above
  `subprocess.run`. Not a BLOCK: the `OUT_DIR` override is the real fix and I
  proved by sentinel that the directory survives a full-suite run — the guard
  is belt-and-suspenders that currently isn't fastened.
- **[INFO]** BUILD_LOG round 3 says "`BUNDLE.json.sha256` matches the `.so`'s
  digest". No such **file** exists; it means BUNDLE.json's `sha256` *field*.
  The underlying claim is true (verified above), only the wording is off.
- **[ASK] A5, A4 (carried, unchanged)** — `_release_creds_configured()` still
  misses `pip.conf`/`PIP_CONFIG_FILE`/keyring; the extraction comment still
  overclaims. Ticket as follow-ups; neither blocks.

## Security findings

- ✅ No new attack surface this round. The three fixes are an env-var default,
  a test-harness interpreter swap, and a docs edit.
- ✅ Re-confirmed no secrets, no credential handling, no new dependencies.
- ✅ A3's honest "corruption check, not authenticity" framing stands.

## Test coverage assessment

144 passed / 9 failed under `-k aerollm`, reproduced twice with identical
results and no ambient-state dependency (contrast round 2's 140/13). 14
bundle tests, all green. The regression tests round 1 demanded now genuinely
assert on a clean machine rather than tripping the F7 guard first.

## Tech debt delta

Round 2's new debt is repaid: `OUT_DIR` is overridable, the test harness no
longer mutates the developer's real venv or real `dist/`. One new minor item
(A7's misordered assertion). Net negative.

## Required actions before merge

None blocking. Follow-ups to ticket: **A7** (move the snapshot above the
subprocess call), **A5**, **A4**.

## Ready for QA, then maintainer release

This sprint is ready for `/qa`. After QA, the release upload is the
**maintainer's** call — I did not run it. `docs/releasing.md:50` documents it
crisply; the exact command for this artifact is:

```
gh release upload v1.1.0 \
  dist/aerollm-bundle/aerollm-api-v1.1.0-macos-arm64.tar.gz \
  dist/aerollm-bundle/aerollm-api-v1.1.0-macos-arm64.tar.gz.sha256
```

The tag argument **must** equal `aerollm_bundle_tag` in `pyproject.toml`
(`v1.1.0`), or the bundled channel 404s for every outside user.
