# Build log: Bundled AeroLLM (Option B) — third install channel

**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at c83db2f
**Started:** 2026-08-05

A2 (publishing a compiled aeroLLM binary on a public ARAIL release) was
confirmed by the maintainer before this build started — public binary
distribution is acceptable; aeroLLM's source repo and a public-PyPI package
listing stay off the table. This build follows §13's recommended order.

## Plan

| # | Files | Change | Test | Commit ref |
|---|---|---|---|---|
| 1 | `THIRD-PARTY-LICENSES/aerollm/{LICENSE,NOTICE,README.md,BUNDLE.json}`, root `NOTICE` | Compliance material first (§7, §13 step 2) | compliance tests (F10) | TBD |
| 2 | `scripts/package-aerollm-bundle.sh` | Producer script: cargo build → tarball + sha256 + MANIFEST.json, dirty-worktree refusal | manual local run against sibling | TBD |
| 3 | `scripts/build-aerollm.sh` | `bundle_install()` driven by `AEROLLM_BUNDLE_FILE`, no network yet (F1/F2/F4/F7) | unit tests | TBD |
| 4 | `scripts/build-aerollm.sh` | URL resolution + curl download + sha256 sidecar + https-only guard (F3/F5) | unit tests | TBD |
| 5 | `scripts/build-aerollm.sh` | `auto` third branch + `AEROLLM_CHANNEL` escape hatch | regression tests | TBD |
| 6 | `scripts/build-aerollm.sh` (`status`), `arailctl`, `scripts/setup.sh` | channel-aware status, `deep install`, setup messaging | tests | TBD |
| 7 | `src/arail/router/backends.py` | ImportError message — 3 ordered routes | contract test | TBD |
| 8 | `pyproject.toml`, `docs/cli.md`, `docs/releasing.md`, `CHANGELOG.md` | env knobs, release checklist, behavior-shift note | n/a (docs) | TBD |
| 9 | tarball build + local `AEROLLM_BUNDLE_FILE` acceptance run | §9.1 acceptance simulation (network-asset step not published — see Architect feedback) | manual | TBD |

## Execution

### Step 1 — compliance material
`THIRD-PARTY-LICENSES/aerollm/{LICENSE,NOTICE,README.md,BUNDLE.json}` +
root `NOTICE` paragraph. `LICENSE`/`NOTICE` are byte-identical copies read
from `~/ProJects/qukaizen-aerollm/{LICENSE,NOTICE}` (verified with `diff`).
`BUNDLE.json` was written with placeholder values, then overwritten with
the real manifest produced by step 2 so nothing in it is fabricated.
Commit: `c631bdc`.

### Step 2 — `scripts/package-aerollm-bundle.sh`
Producer script, run for real against `~/ProJects/qukaizen-aerollm` (a
READ-ONLY `cargo build --release -p aerollm-api --features extension-module`
— confirmed via `git status` in the sibling repo before/after: only the
pre-existing, pre-task-start dirty state (`M CLAUDE.md`, `?? Untitled.md`)
remained; nothing under `~/ProJects/qukaizen-aerollm` was created,
modified, or committed by this build). The sibling worktree was already
dirty from unrelated prior work, so `ALLOW_DIRTY=1` was used and
`MANIFEST.json.aerollm_dirty` honestly records `true` — the "verbatim"
claim in `THIRD-PARTY-LICENSES/aerollm/BUNDLE.json.modifications` reflects
that (see the note there). Produced
`dist/aerollm-bundle/aerollm-api-0.1.0-9e08230-macos-arm64.tar.gz` (22 MB
range; `dist/` is gitignored) at commit `9e08230f0bebfe5eeca5a2da3191fa4a96f24d2d`.
Commit: `b8e1e97`.

### Step 3-6 — `bundle_install()`, dispatch, status
Landed together (§13 steps 4-7 combined into one script commit for
reviewability — the failure-mode tests exercise each piece independently).
`AEROLLM_CHANNEL=dev|release|bundle` escape hatch; `auto`'s new third
branch; `status` channel-awareness. Verified live against the real
tarball from step 2, in a fresh throwaway venv acting as an "outside
user" interpreter (see §Acceptance below) — not just via the automated
test suite. Commit: `da4153a`.

### Step 6 (cont.) — `arailctl deep install`, `AeroLLMBackend` message, `setup.sh`
Three small, separately-reviewable commits: `arailctl` verb (`e634d7d`),
`AeroLLMBackend.__new__` ImportError three-route message (`92b4971`,
with its own contract test), `setup.sh` AeroLLM block comment/messaging
update — no dispatch-logic change there, `build-aerollm.sh auto` already
carries the real logic (`c18855a`).

### Step 6 (cont.) — tests
`tests/test_aerollm_bundle_install.py` (bash-level: F2/F3/F4/F7/F11 +
https-only guard + status channel line) and
`tests/test_aerollm_bundle_compliance.py` (F10 drift + compliance-file
existence). Commit: `1450a57`.

One test-authoring bug surfaced and was fixed before commit: the first
draft of `test_aerollm_backend_install_message.py` used
`importlib.reload(backends)` to force a clean `ImportError`, which
polluted other test files holding references to the pre-reload module
object (`test_aerollm_model_ready.py`,
`test_model_ux_phase0_warmth_probe.py` started failing when run in the
same session, despite passing standalone). Fixed by using
`monkeypatch.setitem(sys.modules, "aerollm_api", None)` instead — no
reload needed. Verified the fix against the *baseline* (pre-sprint) test
suite too, via `git stash`, to confirm a separate, pre-existing 9-test
ordering-pollution issue (kv_budget tests + `test_aerollm_model_ready.py`
sharing a singleton cache across test files) is unrelated to this sprint
— it reproduces identically with or without these changes.

### Step 9 — docs + CHANGELOG
`docs/cli.md` (`deep <op>` section rewritten with the channel table + env
knobs), `docs/releasing.md` (new — didn't exist; architecture allowed
either a new file or a new section), `CHANGELOG.md` (names the `auto`
behavior shift per F9, per the explicit instruction in ARCHITECTURE.md
§6.2). Commit: `aa77d6a`.

### Acceptance simulation (§9.1)

Ran the full "outside user" path for real, end to end, in this session:

1. Fresh venv (`python3 -m venv`), `import aerollm_api` confirmed absent.
2. `ARAIL_AEROLLM_REPO=/nonexistent` (no sibling source).
3. `AEROLLM_INDEX_URL=https://unreachable-index.invalid/simple/` (no
   silent RELEASE fallback possible).
4. `AEROLLM_BUNDLE_FILE=<the real tarball from step 2>` — standing in for
   the GitHub Release download, since the asset is **not yet published**
   (§9.1's network-asset step is therefore validated at the
   `bundle_install()` mechanism level, not the live-URL level — see
   "Not done" below).
5. `./arailctl deep install` → exit 0.
6. **Assert:** `python3 -c "import aerollm_api"` succeeds (confirmed);
   `./arailctl deep status` reports `channel: bundled` with version
   `0.1.0`, commit `9e08230`, build date (confirmed, output captured
   above in the session transcript).

**Not done, and explicitly flagged rather than silently downgraded per
§9.1's own instruction:**
- **The real curl-from-`github.com` download path is untested** — the
  release asset was never uploaded (task instruction: do not run
  `gh release upload`). `resolve_bundle_url()`'s URL construction and the
  404/offline failure path (F3) were tested against a real, deliberately
  nonexistent GitHub repo/tag (`tests/test_aerollm_bundle_install.py::
  test_curl_failure_names_resolved_url_and_exits_nonzero`), which
  exercises the same `curl` call — the only untested leg is "does a real
  asset at a real URL download successfully," which requires the asset
  to exist.
- **"a real chat turn routed through `AeroLLMBackend` returns non-empty
  text" (§9.1 step 6, last clause) is hardware-pending** — no model
  checkpoint was downloaded in this environment. Recorded here as a
  hardware-pending sub-gate, matching how the same caveat is handled
  elsewhere in this repo's aeroLLM correctness gates, rather than
  silently treating "imports" as "works."

### Publish step — intentionally not run

Per the task's explicit instruction, no GitHub Release was created or
modified. The maintainer runs, when ready:

```sh
gh release upload <arail-tag> \
    dist/aerollm-bundle/aerollm-api-0.1.0-9e08230-macos-arm64.tar.gz \
    dist/aerollm-bundle/aerollm-api-0.1.0-9e08230-macos-arm64.tar.gz.sha256
```

(the exact tarball this sprint built and verified end-to-end is staged
locally at that path — `dist/` is gitignored, so it did not enter git
history). Before running it: confirm the target `<arail-tag>` matches
`pyproject.toml`'s `aerollm_bundle_tag` (currently pinned to `v1.1.0` as
a placeholder for the next real ARAIL release), and add the "Bundled
third-party components" section to the release body per
`docs/releasing.md` step 5. **Note:** the bundle used for this sprint's
verification was built from a dirty sibling worktree
(`aerollm_dirty: true`) because the sibling repo already had unrelated
uncommitted changes (`CLAUDE.md`, `Untitled.md`) at task start; before
actually publishing, the maintainer should re-run
`scripts/package-aerollm-bundle.sh` from a clean sibling worktree to get
a strictly verbatim-commit bundle, and refresh
`THIRD-PARTY-LICENSES/aerollm/BUNDLE.json` accordingly.

## Architect feedback required

None. No part of the architect's plan needed revision — every interface
contract in §4, failure mode in §8, and test in §9 was implementable as
specified. The one operational wrinkle (sibling repo dirty at build time)
was anticipated by the architecture itself (§4.3, F11) and handled exactly
as designed.

## Final state

- **Commits:** 10 atomic commits on `qukaizen/arail-bundled-aerollm`
  (`351d5bf` BUILD_LOG skeleton → `aa77d6a` docs), all pushed.
- **New tests:** 3 files, 12 test functions
  (`test_aerollm_backend_install_message.py`,
  `test_aerollm_bundle_compliance.py`, `test_aerollm_bundle_install.py`) —
  all passing.
- **Regression:** `tests/` filtered to `-k aerollm` (150 tests): 141
  passing, 9 pre-existing failures confirmed unrelated to this sprint
  (reproduce identically on the pre-sprint baseline via `git stash`;
  root cause is unrelated cross-file singleton-cache pollution in
  `AeroLLMBackend._shared`, tracked separately — not introduced or
  touched by this build).
  `tests/test_aerollm_local_sibling_build.py` (DEV/RELEASE regression
  suite) and `tests/test_aerollm_compute_source.py`: 15/15 passing,
  unmodified.
- **Lint:** `shellcheck -x scripts/build-aerollm.sh scripts/package-aerollm-bundle.sh`
  clean. `bash -n` clean on all three touched shell scripts.
- **Lines changed:** `scripts/build-aerollm.sh` +244/-17;
  `scripts/package-aerollm-bundle.sh` new (155 lines); 3 new test files
  (~230 lines); compliance material (4 new files); doc/changelog updates.
- **Sibling repo (`~/ProJects/qukaizen-aerollm`):** confirmed untouched by
  this build beyond its own gitignored `target/` build output — no
  commits, no working-tree changes attributable to this sprint.

## Round 2 fixes

Response to [REVIEW.md](./REVIEW.md)'s BLOCK verdict. Four commits,
`85aa946` → `cd50850`.

### B1 — producer/consumer filename mismatch (BLOCK)

Adopted the tag-only convention `aerollm-api-<ARAIL release tag>-macos-arm64.tar.gz`
on both sides — it was already what `resolve_bundle_url()` requested, so
the fix is entirely in `package-aerollm-bundle.sh`: `ARAIL_RELEASE_TAG` is
now a required input (was optional, silently defaulting to `unreleased`
and a version+commit-based filename), and the output filename derives
from it directly. `docs/releasing.md` now spells out the exact filename
convention rather than saying "attach the tarball." Removed the dead
`AEROLLM_BUNDLE_ASSET` variable REVIEW.md flagged as tech debt.

Regression test added: `test_producer_filename_matches_consumer_resolved_filename`
in `tests/test_aerollm_bundle_install.py` — runs the real producer script
against a fake clean sibling repo (stubbed `cargo` on `PATH`) and the real
consumer's `bundle_install()` against an unreachable host, and asserts the
producer's output filename appears in the URL the consumer actually
requested. This is the exact gap REVIEW.md named as missing.

### B2 — `aerollm_bundle_tag` never reached `AEROLLM_BUNDLE_TAG` (BLOCK)

`setup.sh`'s `load_pyproject_metadata()` now reads
`[tool.arail.package-sources] aerollm_bundle_tag` into `AEROLLM_BUNDLE_TAG`
(mirroring the existing `AEROLLM_INDEX_URL`/`AEROLLM_PIP_SPEC` pattern
exactly), and the AeroLLM install block in `main()` forwards it to
`build-aerollm.sh auto` alongside those two. Verified both link points
directly rather than trusting the wiring by inspection: (1) a bumped
`aerollm_bundle_tag` pin is read correctly by the same `tomllib`
extraction `load_pyproject_metadata()` uses; (2) `AEROLLM_BUNDLE_TAG`
changes the URL `resolve_bundle_url()` constructs, confirmed against a
deliberately nonexistent GitHub repo/tag.

### A1 — LICENSE was the upstream stub, not the full text (ASK)

Read `~/ProJects/qukaizen-aerollm/LICENSE` directly, per instruction —
it turns out to be only the Apache-2.0 header boilerplate + copyright
line (17 lines), not the full ~200-line license. The task's assumption
that upstream had the full text was wrong; fixing upstream was
explicitly out of scope (no writes to the sibling repo beyond a
read-only `cargo build`). `THIRD-PARTY-LICENSES/aerollm/LICENSE` now
carries the full, standard Apache-2.0 text verbatim from
`apache.org/licenses/LICENSE-2.0.txt` with aeroLLM's copyright line —
this is the definitive, unambiguous published text, not a guess.
`NOTICE` is unchanged (byte-identical to upstream's, whose claim was
already accurate). `package-aerollm-bundle.sh` now copies the tarball's
`LICENSE` from ARAIL's own `THIRD-PARTY-LICENSES/aerollm/LICENSE`
instead of the sibling's stub, so what ships matches what's committed.
`tests/test_aerollm_bundle_compliance.py`'s byte-identity assertion is
split: `NOTICE` keeps the sibling-comparison test; `LICENSE` gets a
full-text-marker test (checks for section headers + line count) instead
of a byte-equality test that would have re-encoded the same bug.

### A2 — `BUNDLE.json` shipped `aerollm_dirty: true` (ASK)

The sibling worktree at `~/ProJects/qukaizen-aerollm` had unrelated
uncommitted changes (from a different, concurrent task) at build time —
genuinely dirty, not a bug in the dirty-check. Rather than hardcode
`false`, re-cut from an actually-clean tree: `git worktree add` against
the pinned commit `9e08230f0be...` (a separate, temporary checkout — no
writes to the sibling's real working tree), ran the real
`package-aerollm-bundle.sh` against it, confirmed `aerollm_dirty: false`
in the resulting `MANIFEST.json`, and copied that into
`THIRD-PARTY-LICENSES/aerollm/BUNDLE.json`. The worktree was removed
afterward (`git worktree remove --force`); `git -C
~/ProJects/qukaizen-aerollm status --porcelain` confirmed only the
original, pre-existing unrelated dirt remained.

### A3 — sha256 sidecar overclaimed as authenticity control (INFO)

Added an explicit "what this does and doesn't guarantee" paragraph to
`docs/cli.md` next to the `AEROLLM_BUNDLE_SHA256` knob: same-origin sidecar
catches corruption, not tampering; the real trust boundary is GitHub's TLS
+ repo access, not the checksum.

### Re-verification

- Regenerated the tarball from the clean worktree with
  `ARAIL_RELEASE_TAG=v1.1.0`: `dist/aerollm-bundle/aerollm-api-v1.1.0-macos-arm64.tar.gz`,
  `aerollm_dirty: false`, commit `9e08230f0bebfe5eeca5a2da3191fa4a96f24d2d`.
- §9.1 acceptance re-run: fresh venv, `ARAIL_AEROLLM_REPO=/nonexistent`, no
  PIP_INDEX_URL/PIP_EXTRA_INDEX_URL/netrc creds,
  `AEROLLM_BUNDLE_FILE=<the corrected-filename tarball>` → `auto` selects
  the bundled channel, checksum verifies, `import aerollm_api` succeeds,
  `deep status` reports `channel: bundled` with the right commit/version.
- Full suite: `pytest -k aerollm` → **144 passed, 9 failed** (same 9
  pre-existing failures as REVIEW.md's baseline — cross-file singleton-
  cache pollution in `AeroLLMBackend._shared`, unrelated to this sprint;
  zero regressions, 3 new tests added this round).
- `shellcheck -x scripts/build-aerollm.sh scripts/package-aerollm-bundle.sh`
  clean. `bash -n` clean on all three touched shell scripts.
- No `gh release` executed. Sibling repo confirmed untouched beyond the
  temporary worktree (removed) and its own gitignored `target/` output.

## Architect feedback required (round 2)

None. Both BLOCK findings and all ASK/INFO findings had clean, in-scope
fixes; nothing required a design change.

## Round 3 fixes

REVIEW.md round 2 BLOCKed on three findings: B3 (the B1 regression test
`rm -rf`s the real release artifact), B4 (the true test count was 140/13,
not the claimed 144/9, because four tests are ambient-venv-dependent), and
B5 (`docs/releasing.md` step 4 tells a future maintainer to byte-sync
LICENSE from upstream, reintroducing the stub).

### B3 — isolate the producer's output directory

`package-aerollm-bundle.sh` hardcoded `OUT_DIR="$REPO_ROOT/dist/aerollm-bundle"`
and unconditionally `rm -rf`s it on every run. The regression test added
in round 2 (`test_producer_filename_matches_consumer_resolved_filename`)
invoked the real script with `cwd=REPO` and no way to redirect that
directory, so running the test destroyed the real `v1.1.0` tarball round 2
had built — exactly as the reviewer demonstrated with a sentinel file.

Fix: `OUT_DIR="${OUT_DIR:-$REPO_ROOT/dist/aerollm-bundle}"` — overridable
via env, default unchanged for real maintainer runs. The test now sets
`OUT_DIR` to a `tmp_path` subdirectory, runs the script with `cwd=tmp_path`
(belt-and-suspenders against any accidental relative-path resolution), and
asserts the real `dist/aerollm-bundle/*.tar.gz` listing is byte-identical
before and after the test runs — so a future regression here fails loudly
instead of silently wiping the artifact again.

### B4 — isolate `_run()`'s target interpreter

`bundle_install()`'s F7 provenance guard checks the *ambient* `python3`'s
site-packages for an existing `aerollm_api.abi3.so` with no bundle marker,
and aborts before reaching the code path most of the round-1 regression
tests intend to exercise. This repo's own ambient `python3` (and the
project's dev `.venv`) both carry a DEV/RELEASE install, so four tests
(`test_checksum_mismatch_aborts_before_any_write`,
`test_curl_failure_names_resolved_url_and_exits_nonzero`,
`test_https_only_scheme_guard`,
`test_producer_filename_matches_consumer_resolved_filename`) failed on a
normal dev machine — reproduced independently before the fix.

Fix: `_run()` now defaults every invocation to a session-cached, empty
venv (`_isolated_python()`) unless the caller already supplies `PYTHON` in
`env_extra` or explicitly passes a `python=` override — no test asserts
anything about the ambient machine's site-packages anymore. The existing
`isolated_python` fixture (function-scoped, its own venv per test) is kept
for the one test that actually completes a partial install into it
(`test_bundle_install_from_local_file_succeeds`), so its state never
leaks into the shared cache.

### B5 — `docs/releasing.md` step 4 no longer instructs syncing LICENSE from upstream

Split the instruction: NOTICE still syncs from upstream (accurate, no
compliance risk); LICENSE is called out explicitly as ARAIL's own
full-Apache-2.0-text copy that must never be byte-synced from upstream's
17-line stub, with a one-line why and a pointer at the specific compliance
test that already catches the regression
(`test_license_is_full_apache2_text_not_upstream_stub` — verified this
test would fail on a stub: it asserts five full-text section markers and
`len(lines) > 150`; a 17-line stub trips both). No test change was needed
for B5 — the existing compliance test already has the teeth to catch this.

### Re-cut the real `v1.1.0` artifact

B3's regression had destroyed `dist/aerollm-bundle/`. Re-cut it the same
way round 2 did: `git worktree add --detach <tmp> 9e08230f0bebfe5eeca5a2da3191fa4a96f24d2d`
against the sibling repo (no writes to its real working tree),
`ARAIL_RELEASE_TAG=v1.1.0 ARAIL_AEROLLM_REPO=<tmp worktree> bash
scripts/package-aerollm-bundle.sh`, confirmed `aerollm_dirty: false` in
the resulting `MANIFEST.json`, copied it over
`THIRD-PARTY-LICENSES/aerollm/BUNDLE.json`, then `git worktree remove
--force`. Sibling repo: `HEAD` still `9e08230f0be…`, `git status
--porcelain` shows only the same pre-existing unrelated
`aerollm-grammar`/`CLAUDE.md` dirt from before this sprint touched
anything, `git worktree list` shows no leftover worktree for this sprint.

Verified sha256 chain: tarball sha `d3a7a9dd19987350963422ab7647a2d4ad607e78397b57f70c55362c0b95ecce`
matches its `.sha256` sidecar exactly; `BUNDLE.json.sha256` matches the
`.so`'s digest from the same build (`2776d188f71c98bfb46b1e87f2f5e8aa4f30146541d0748e3321cd0364650f9a`).

### Re-verification (round 3)

- `pytest -k aerollm` run twice for determinism: **144 passed / 9 failed**
  both times, identical failure set both times (`test_aerollm_defaults.py`
  ×4, `test_aerollm_model_ready.py` ×3, `test_model_ux_phase0_warmth_probe.py`
  ×2) — the same pre-existing, unrelated failures round 1/2 identified and
  independently reproduced against the pre-sprint baseline. This is now a
  genuinely reproducible number, run on this machine's real ambient state
  (dev `.venv` with a DEV `aerollm_api` install present), not an
  approximation.
- All 14 tests in `tests/test_aerollm_bundle_install.py` +
  `tests/test_aerollm_bundle_compliance.py` pass individually and as part
  of the full `-k aerollm` run.
- `shellcheck -x scripts/package-aerollm-bundle.sh scripts/build-aerollm.sh`
  clean; `bash -n scripts/package-aerollm-bundle.sh` clean.
- No `gh release` executed. Sibling `~/ProJects/qukaizen-aerollm` has zero
  new commits (`HEAD` unchanged at `9e08230f0be…`) and its working tree
  shows only the pre-existing, unrelated dirt that predates this sprint.

## Architect feedback required (round 3)

None. All three round-2 BLOCKs had clean, in-scope fixes; nothing required
a design change.

---

## Round 4 fixes (QA Q1/Q2/Q5/Q7)

Source: `TEST_REPORT.md` (`c228597`), verdict FAIL. Plan before touching code:

| # | Files | Change | Test | Commit ref |
|---|---|---|---|---|
| 1 | `pyproject.toml`, `THIRD-PARTY-LICENSES/aerollm/BUNDLE.json`, `scripts/build-aerollm.sh`, `docs/cli.md`, `docs/releasing.md` | Q1: add `aerollm_bundle_sha256` (tarball digest) pin next to `aerollm_bundle_tag`; verify extracted `.so` against `MANIFEST.json.sha256` before install; fix the `docs/cli.md` guidance that pointed users at the wrong digest | `test_committed_bundle_json_sha256_is_the_so_digest_not_the_tarball_digest` (test 20), `test_bundle_install_from_local_file_succeeds` (updated fixture) | pending |
| 2 | `scripts/build-aerollm.sh` | Q5 (a): Mach-O-magic sanity check on the extracted `.so` before copying into site-packages, dependency-free (raw magic bytes) | `test_unloadable_so_is_rolled_back_leaving_no_shadowing_artifact` (test 8, unaffected — crafted Mach-O magic), updated `test_bundle_install_from_local_file_succeeds` | pending |
| 3 | `scripts/build-aerollm.sh`, `README.md`, `SECURITY.md`, `docs/cli.md` | Q5 (b): one-liner install-time disclosure + same caveat surfaced in README's maximus section and SECURITY.md, honestly scoped (integrity not authenticity, unsigned binary) | `test_cli_docs_disclose_the_sha256_trust_boundary` (test 29, unchanged) | pending |
| 4 | `sprints/2026-08-05-arail-bundled-aerollm/ARCHITECTURE.md` | Q2: dated correction to §9.1 step 3 — leave `AEROLLM_INDEX_URL` unset instead of pointing it at an unreachable host | `test_auto_selects_release_when_aerollm_index_url_is_overridden` (test 14, pins the real dispatch so the corrected recipe is checked against it) | pending |
| 5 | `scripts/setup.sh` | Q7: name `./arailctl deep install` in the AeroLLM failure warning (both the Apple-Silicon branch and the non-Apple-Silicon branch) | `test_setup_failure_message_names_the_outside_user_route` (test 30, currently red) | pending |
| 6 | n/a | Q4 (tar error wrap): non-blocking, evaluate after the above | — | pending/skip |
| 7 | n/a | Q6 (F7 guard over-broad): non-blocking; skip — narrowing it changes the behaviour `test_f7_guard_message_on_an_interrupted_bundle_install` (test 27) deliberately pins as a "regression anchor for a follow-up fix", and that follow-up would need its own review round, not a drive-by in this one | — | skip (documented) |

Order: Q1 → Q5(a) sanity check (same file/region, do together) → Q5(b)
disclosure strings → Q2 (docs-only) → Q7 (docs-only, turns the one red
test green) → re-run full suite → Q4 if still cheap.

### Step 1 — Q1 + Q5(a)/(b): digest pin, MANIFEST verify, Mach-O sanity check, disclosure

Delta from plan: the digest-pin fix and the Mach-O/MANIFEST sanity check
landed in the same commit as this plan (`13b8d64`) — a staging mistake
(re-staged a file without unstaging the rest), not intentional bundling.
Noted here rather than silently left out of the log.

- `pyproject.toml`: added `aerollm_bundle_sha256` under
  `[tool.arail.package-sources]`, holding the **tarball** digest
  (`d3a7a9dd1998…`, verified against `dist/aerollm-bundle/*.tar.gz.sha256`).
  `BUNDLE.json`'s `sha256` field is left as-is (it's internally consistent
  — it's the `.so` digest, verified against the in-tarball
  `MANIFEST.json`, and `test_committed_bundle_json_sha256_is_the_so_digest_not_the_tarball_digest`
  already pins that meaning) — no field rename; instead `docs/cli.md` now
  says explicitly which file to use for which purpose and why they can
  never match.
- `scripts/setup.sh`: loads `aerollm_bundle_sha256` from pyproject,
  forwards it as `AEROLLM_BUNDLE_SHA256` to `build-aerollm.sh auto`
  exactly like `AEROLLM_BUNDLE_TAG` already does. Standalone
  `./arailctl deep install` does **not** forward it (goes straight to
  `build-aerollm.sh bundle`, bypassing `setup.sh`) — documented as a gap
  in `docs/cli.md`, not silently glossed over.
- `scripts/build-aerollm.sh` `bundle_install()`: after extraction and the
  four-member presence check, added (a) MANIFEST.json `.so` sha256
  verification (fail-closed on mismatch or missing field) and (b) a
  Mach-O 64-bit magic-byte check (`od -An -tx1 -N4`, no new dependency),
  both before the `cp` into site-packages. Added a one-line install-time
  disclosure (`warn`) naming the trust boundary honestly: integrity-checked,
  not signature-verified or sandboxed.
- `docs/cli.md`: corrected the out-of-band pin guidance (use
  `aerollm_bundle_sha256`, not `BUNDLE.json.sha256`), documented the new
  post-extraction checks, and the honest "no codesign, no notarization,
  unsigned binary, same-origin trust" caveat.
- `docs/releasing.md`: step 4 gained a bullet to bump
  `aerollm_bundle_sha256` alongside `aerollm_bundle_tag`.
- `tests/test_aerollm_bundle_install.py`: `test_bundle_install_from_local_file_succeeds`'s
  fixture now writes a real sha256 into `MANIFEST.json` (previously
  `"unused-in-test"` — now genuinely used, since it's verified). The
  fixture's `so_bytes` (`b"fake-so-bytes"`) isn't Mach-O, so the test now
  hits the new pre-copy sanity check instead of the old import-failure/F1
  path; assertion updated to match (`"Mach-O arm64"` instead of `"import
  aerollm_api"`/`"Removed the broken artifact"`). This is a legitimate
  behavior change required by Q5 (validate before copy), not scope drift.
- Verified: `pytest tests/test_aerollm_bundle_install.py
  tests/test_aerollm_bundle_compliance.py
  tests/test_aerollm_bundle_qa_hardening.py -q` → **43 passed, 1 failed**
  (only test 30, Q7, expected red at this point). `shellcheck -x
  scripts/build-aerollm.sh` clean.

### Step 2 — Q2: correct ARCHITECTURE.md §9.1's acceptance recipe

Dated correction (this workspace's convention — don't silently rewrite):
§9.1 step 3 now says to **leave `AEROLLM_INDEX_URL` unset**, with an
inline note explaining why the old instruction (point it at an
unreachable host) backfired — `_release_creds_configured()` treats any
non-default `AEROLLM_INDEX_URL` as configured credentials by design (a
real user who overrides the index URL does mean "use RELEASE"), so the
old instruction guaranteed a RELEASE run and never reached BUNDLED. Fixed
the recipe rather than the function: the function's behavior is correct
for its real callers, and changing it risked a regression in genuine
RELEASE-channel selection.

Re-ran the corrected recipe for real (temp dir, not the repo, no writes
to the sibling repo):

```
$ env -i HOME=<empty> PATH=/usr/bin:/bin:/usr/local/bin PYTHON=<fresh venv> \
    ARAIL_AEROLLM_REPO=/nonexistent \
    AEROLLM_BUNDLE_FILE=dist/aerollm-bundle/aerollm-api-v1.1.0-macos-arm64.tar.gz \
    bash scripts/build-aerollm.sh auto
• No sibling repo, no release credentials → installing the bundled binary (bundled channel).
• Using local bundle tarball: .../aerollm-api-v1.1.0-macos-arm64.tar.gz (offline path).
• Checksum verified (sha256 d3a7a9dd1998…).
• Installing → .../site-packages/aerollm_api.abi3.so
! aerollm_api.abi3.so is prebuilt native code, downloaded and executed on
! import. It is integrity-checked against the release manifest (same-origin
! trust), NOT signature-verified or sandboxed — see docs/cli.md.
• AeroLLM ready (bundled 0.1.0) — the deep-mode 2nd inference.
EXIT=0
```

Reaches BUNDLED, EXIT 0, as the acceptance bar requires. `test_auto_selects_release_when_aerollm_index_url_is_overridden`
(test 14) stays green — it pins the real (unchanged) dispatch behavior so
the *next* person reads code, not assumption.

### Step 3 — Q7: name `./arailctl deep install` in the AeroLLM failure warning

Fixed `scripts/setup.sh`'s Apple-Silicon `auto`-install failure message to
name `./arailctl deep install` (bundled binary, the outside-user route)
ahead of `deep rebuild` / `deep update` (maintainer-only, gated behind
"if you're a maintainer with..."). Turns `test_setup_failure_message_names_the_outside_user_route`
(test 30) green.

**Left unchanged, deliberately:** the non-macOS-arm64 `else` branch two
lines below (`AeroLLM auto-build is Apple-Silicon-only today...`). TEST_REPORT.md
flagged it as "the same problem," but it isn't: that branch only runs on
non-macOS-arm64 hosts, where `bundle_install()`'s F4 platform guard would
refuse the bundled channel outright. Naming `deep install` there would be
actively wrong advice, not a fix — `deep rebuild` (once CUDA lands) is the
only route that could ever apply. Read the code before extending the
pattern rather than pattern-matching the finding.

Verified: `pytest tests/test_aerollm_bundle_qa_hardening.py -q` →
**30 passed** (was 29/30). `shellcheck -x scripts/setup.sh` unchanged
(pre-existing SC2034/SC2024 warnings only, none in the touched region).

### Step 4 — Q5(c): surface the trust-boundary disclosure outside docs/cli.md

TEST_REPORT.md's Q5 finding: the "integrity, not authenticity" caveat
lived only in `docs/cli.md`'s CLI reference — nowhere a user reads before
running `./arailctl setup`. Added, honestly scoped (no tamper-proof claim,
no codesign-verification claim — the binary is unsigned and that's stated
plainly):

- `README.md`'s maximus AeroLLM paragraph: names `deep install` (also
  fixing the same class of Q7 omission there) and links to SECURITY.md +
  docs/cli.md for the trust-boundary disclosure.
- `SECURITY.md`: new "Out of scope" bullet describing the bundled channel's
  RCE-at-import reality, what the sha256/Mach-O checks do and don't
  guarantee, and the same-origin trust boundary.

No test pins these (they're prose, not contract), but
`test_cli_docs_disclose_the_sha256_trust_boundary` (test 29) confirms the
`docs/cli.md` version of the caveat is unchanged.

### Step 5 — Q4 (non-blocking, done): wrap the raw `tar` failure

Genuinely quick — wrapped `tar xzf` in the same `if ! ...; then err/warn;
fi` shape `curl` already uses, one screen, no new dependency. Verified
`test_truncated_archive_with_matching_digest_installs_nothing` and
`test_non_archive_bytes_install_nothing` still pass (they only assert
`returncode != 0` and nothing installed, not the exact message) and both
now get an actionable `✗`/`!` message instead of raw `tar: Error exit
delayed from previous errors.`

### Step 6 — Q6 (non-blocking, skipped)

Not done. `test_f7_guard_message_on_an_interrupted_bundle_install` (test
27) explicitly pins the *current* over-broad behavior as a "regression
anchor" for a follow-up fix — narrowing the guard now (skip F7 when
`import_ok` already fails, and/or atomic `.so`+marker install) would flip
that test's expected outcome and needs its own review round, not a
drive-by in a QA-remediation sprint whose scope is Q1/Q2/Q5/Q7. Left as
open debt, as TEST_REPORT.md itself frames it ("Low, grace" — the user is
not stuck; `--force` is named).

### Final verification (round 4)

- `pytest tests/test_aerollm_bundle_install.py tests/test_aerollm_bundle_compliance.py tests/test_aerollm_bundle_qa_hardening.py -q`
  → **44 passed** (30/30 hardening + 14/14 pre-existing).
- `shellcheck -x scripts/build-aerollm.sh scripts/setup.sh` clean (no new
  warnings in touched regions; pre-existing SC2034/SC2024 in unrelated
  `setup.sh` code untouched).
- sha256 chain on `dist/aerollm-bundle/` unchanged — no field rename
  required this round, so no re-cut was needed. Tarball sha
  `d3a7a9dd19987350963422ab7647a2d4ad607e78397b57f70c55362c0b95ecce`
  matches the new `aerollm_bundle_sha256` pin in `pyproject.toml` exactly.
- No `gh release` run. Sibling `~/ProJects/qukaizen-aerollm` untouched
  (no writes, no worktree created this round — the Q2 re-verification
  used only `AEROLLM_BUNDLE_FILE` against the already-committed tarball).
- Full `-k aerollm` slice: **174 passed / 9 failed**, both before and
  after this round's changes (`git stash` bisection) — same 9 pre-existing
  failures TEST_REPORT.md documented (`test_aerollm_defaults.py` x4,
  `test_aerollm_model_ready.py` x3, `test_model_ux_phase0_warmth_probe.py`
  x2, the `AeroLLMBackend._shared` cross-file singleton-cache pollution
  class). **Zero regressions introduced by round 4.**

## Architect feedback required (round 4)

None. All four required fixes were small, local, and matched
TEST_REPORT.md's own recommended fix shape. Q6 was deliberately deferred
rather than improvised — see Step 6.

## Round 5 fix (QA R2-1) — orchestrator-direct, 2026-08-05

**R2-1** (QA round-2 residual): standalone `./arailctl deep install` bypassed
`setup.sh` and therefore never received the `aerollm_bundle_sha256` pin —
the exact route recommended to outside users was the one un-pinned path.

**Fix:** `scripts/build-aerollm.sh` now self-serves both
`aerollm_bundle_tag` and `aerollm_bundle_sha256` from the repo's own
`pyproject.toml` when the corresponding env vars are unset (env always
wins; `setup.sh` forwarding unchanged). The reader tries `tomllib`, then
`tomli`, then a line-parse fallback for pre-3.11 interpreters (macOS
system python3 is 3.9 — verified this path resolves the pin correctly).
`AEROLLM_PYPROJECT` overrides the pyproject location so tests can
exercise the genuinely-no-pin path (`/nonexistent`).

**Verified by execution:** standalone run with env unset resolves
tag `v1.1.0` + the pinned tarball digest on both system python (regex
fallback) and venv python (tomllib); explicit env override wins; a
temporary pyproject pin bump flows through to the resolved value.
Two QA-hardening tests updated to neutralize the ambient pin via
`AEROLLM_PYPROJECT=/nonexistent` (their subjects were marker semantics
and the no-digest-anywhere path — intent preserved, assertions
unchanged); one NEW test added
(`test_standalone_route_self_serves_pyproject_pin`) locking in R2-1's
fix: a fabricated tarball matching its own sidecar but not the repo pin
is refused before install on the standalone route.

Suites: bundle install/compliance/qa-hardening 45/45; full `-k aerollm`
175 passed / 9 failed (identical 9 pre-existing). `shellcheck -x` clean.
`docs/cli.md`'s stale "standalone does not forward the pin" disclosure
corrected. No `gh release` run; sibling repo untouched.

## Round 6 fix (field report) — orchestrator-direct, 2026-08-06

**Field failure (maintainer, fresh shell):** `arailctl deep install`
downloaded + checksum-verified fine, then died with a raw
`cp: /Library/Python/3.9/site-packages/aerollm_api.abi3.so: No such
file or directory` — the `deep` dispatch was the only arailctl branch
that never activates the repo venv, so bare `python3` (macOS system
3.9) was the install target, whose platlib doesn't exist.

**Fixes:**
1. `arailctl deep` now exports `PYTHON=$REPO_ROOT/.venv/bin/python`
   when unset (explicit `$PYTHON` still wins; missing `.venv` dies with
   "run ./arailctl setup" instead of falling through to system Python).
2. `build-aerollm.sh` gained `require_writable_site_dir` — both the
   bundle and cargo install paths now abort pre-copy with an actionable
   message when the interpreter's platlib is missing/unwritable.

**Verified:** system-python repro now aborts gracefully with the
routed message; `arailctl deep install` targets the venv (F7 guard
correctly refused to clobber this machine's DEV build); bundle suites
46/46; `shellcheck -x` clean.
