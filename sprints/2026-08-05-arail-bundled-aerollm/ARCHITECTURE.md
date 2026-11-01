# Architecture: Bundled AeroLLM (Option B) — a third install channel

**Date:** 2026-08-05
**Sprint:** `2026-08-05-arail-bundled-aerollm`
**Branch:** `qukaizen/arail-bundled-aerollm` (fresh, off `origin/main` — the
`qukaizen/arail-model-defaults` branch is unrelated scope)
**Ledger:** SPRINT.md
**Primary surfaces:** `scripts/build-aerollm.sh`, `scripts/setup.sh` (AeroLLM
block, ~line 632), `arailctl` (`deep` verb, ~line 776),
`src/arail/router/backends.py` (`AeroLLMBackend.__new__` ImportError message,
~line 1531), `pyproject.toml` `[tool.arail.package-sources]`
**Repo conventions:** `CLAUDE.md` (root) — ARAIL is MIT and a *blueprint*
people fork; bash 3.2 compatibility; model/artifact paths stay repo-relative
or env-driven, never a home dir.

---

## 1. Restatement

Today an outside user who clones only ARAIL gets **zero** deep-mode inference.
`scripts/build-aerollm.sh` offers exactly two channels and both are
maintainer-only in practice: **DEV** needs the aeroLLM sibling *source* repo
(which is not public and is deliberately staying that way), and **RELEASE**
needs pip credentials for `https://pypi.qukaizen.com/simple/` (a private
self-hosted index; the documented public-PyPI fallback has nothing published on
it). The lab degrades gracefully — that part of the script's design is correct —
but "graceful degradation" for 100% of non-maintainer users is a capability
gap, not a fallback. This sprint adds a **third, additive channel — `BUNDLED`** —
that fetches a *prebuilt, checksummed `aerollm_api.abi3.so`* published as a
binary asset on **ARAIL's own** GitHub Releases (the repo is already public:
`cdarnell/qukaizen-arail`, latest `v1.0.0`), installs it into the active
interpreter's `platlib`, and carries the Apache-2.0 compliance material ARAIL
now owes as a redistributor. aeroLLM's own repo and release pipeline are not
touched, aerollm-api is not published to public PyPI, and DEV/RELEASE keep
their exact current behavior. Scope stays macOS-arm64.

---

## 2. Assumptions

Each is a way this design can be wrong.

- **A1.** `cdarnell/qukaizen-arail` is and stays **public**, and GitHub Release
  assets on it are downloadable **anonymously** (no token, no rate-limit
  gate for a plain `https://github.com/<o>/<r>/releases/download/...` URL).
  Verified: `gh repo view --json visibility` → `PUBLIC`.
- **A2.** The maintainer is willing to publish a *compiled aeroLLM binary* on a
  public ARAIL release. This is redistribution, and it is permitted by
  aeroLLM's Apache-2.0 LICENSE — but it is a real disclosure decision distinct
  from "aeroLLM is not going public." **Not** a source release, **not** a
  package-index listing, **not** discoverable by anyone who isn't already
  downloading ARAIL. If the maintainer rejects even that, the whole design
  collapses to option (c) in §5 (private bucket) and the "no credentials"
  acceptance bar cannot be met. **This assumption must be confirmed before
  build starts.**
- **A3.** The extension is `abi3-py39` (`crates/aerollm-api/Cargo.toml:47`:
  `pyo3 = { version = "0.22", features = ["abi3-py39"] }`), so **one** `.so`
  is valid for CPython 3.9+ — no per-Python-version matrix. Verified.
- **A4.** The artifact is self-contained apart from OS frameworks. `otool -L`
  on the current build shows only `/usr/lib/*` and
  `/System/Library/Frameworks/{Metal,Foundation,Accelerate,IOKit,CoreFoundation}` —
  MLX/Metal kernels are statically linked. No `@rpath` to a Cargo target dir,
  no Homebrew dylib. If a future aeroLLM build adds a dynamic dep, the bundle
  breaks silently at `import` time; §8-F1 covers detection.
- **A5.** Size is ~22 MB (`libaerollm_api.dylib`, 22,242,624 bytes on
  2026-07-22). Well inside GitHub's 2 GB per-asset limit; far too big to want
  in git history.
- **A6.** macOS arm64 requires every loadable binary to be at least ad-hoc
  signed. `cargo build --release` produces an ad-hoc signature; the signature
  is embedded in the file bytes and survives HTTP download + `cp`. It does
  **not** survive a byte-level rewrite. We never rewrite the binary.
- **A7.** `curl` is present on macOS by default; `shasum` (or `python3
  hashlib`) is available for checksum verification. `python3` is already a
  hard assumption everywhere in `scripts/`.
- **A8.** The user's target interpreter is whatever
  `sysconfig.get_path('platlib')` resolves to for `${PYTHON:-python3}` — the
  same target the existing `cargo_build()` writes to. Unchanged.
- **A9.** aeroLLM's `NOTICE` file exists and states "This is a from-scratch
  codebase. No third-party source code is vendored." Verified — this
  materially simplifies §7: we owe aeroLLM's LICENSE + NOTICE, not a
  transitive dependency ledger.

---

## 3. Data flow

### 3.1 Producer side (maintainer, manual, ~5 min per refresh)

```
~/ProJects/qukaizen-aerollm  (private source, untouched)
        │  cargo build --release -p aerollm-api --features extension-module
        ▼
 target/release/libaerollm_api.dylib   (22 MB, ad-hoc signed, abi3-py39)
        │
        │  scripts/package-aerollm-bundle.sh   ← NEW (maintainer-only)
        ▼
 dist/aerollm-bundle/
   ├── aerollm_api.abi3.so            (verbatim copy — bytes never rewritten)
   ├── MANIFEST.json                  (see §6.1)
   ├── LICENSE                        (copied from aeroLLM)
   └── NOTICE                         (copied from aeroLLM)
        │  tar czf → aerollm-api-<ver>-macos-arm64.tar.gz  + .sha256
        ▼
 gh release upload <arail-tag> aerollm-api-<ver>-macos-arm64.tar.gz{,.sha256}
        ▼
 https://github.com/cdarnell/qukaizen-arail/releases/download/<tag>/...
```

### 3.2 Consumer side (outside user, automatic)

```
./arailctl setup  (tier=maximus, Darwin/arm64)
        │
        ▼
scripts/setup.sh ── bash scripts/build-aerollm.sh auto
        │
        ▼
   ┌──────────────── mode dispatch (§6.2) ─────────────────┐
   │  sibling crate dir present?  ──yes──► cargo_build()   │  DEV   (unchanged)
   │            │ no                                       │
   │  AEROLLM_CHANNEL=release or index creds set? ──►      │  RELEASE (unchanged)
   │            │ no                                       │
   │            └──────────────► bundle_install()          │  BUNDLED (NEW)
   └───────────────────────────────────────────────────────┘
                                     │
   curl -fL <release-asset-url> ──► $TMPDIR/aerollm-bundle.tar.gz
                                     │  shasum -a 256 -c  (fail ⇒ abort, no install)
                                     ▼
                              tar xzf into $TMPDIR
                                     │  xattr -d com.apple.quarantine (best-effort)
                                     ▼
   cp aerollm_api.abi3.so ──► $(python3 -c 'sysconfig…platlib')/aerollm_api.abi3.so
   cp MANIFEST.json       ──► <platlib>/aerollm_api.bundle.json     (provenance marker)
                                     │
                                     ▼
                        verify_or_die()  →  python3 -c 'import aerollm_api'
                                     │
                                     ▼
        src/arail/router/backends.py :: AeroLLMBackend.__new__
              from aerollm_api import Runtime   → deep mode live
```

Repo-side static material shipped in-tree (not downloaded):

```
arail/
  THIRD-PARTY-LICENSES/aerollm/{LICENSE,NOTICE,README.md,BUNDLE.json}
  NOTICE                       ← ARAIL's own, gains an AeroLLM paragraph
```

---

## 4. Interface contracts

### 4.1 `scripts/build-aerollm.sh bundle` (new mode) / `bundle_install()`

- **Requires:** Darwin + arm64; network reachability to `github.com` (or
  `AEROLLM_BUNDLE_FILE` pointing at a pre-downloaded tarball for the offline
  path); `curl`, `tar`, and `shasum`-or-`python3`; write access to `platlib`.
- **Promises:** on exit 0, `python3 -c "import aerollm_api"` succeeds and
  `<platlib>/aerollm_api.bundle.json` records the exact aeroLLM
  version + commit + sha256 installed.
- **On bad input / failure:** installs **nothing** and exits non-zero with a
  one-screen actionable message. Specifically: checksum mismatch ⇒ abort
  before any `cp` (never install unverified bytes); non-Darwin or non-arm64 ⇒
  refuse with the existing "macOS-arm64-only, the lab runs without the 2nd
  inference" wording; 404 on the asset ⇒ "this ARAIL release has no bundled
  AeroLLM; run `./arailctl deep status`".
- **Idempotence:** re-running with the same manifest version is a no-op that
  prints the installed version; `--force` reinstalls.
- **Never:** rewrites the `.so` bytes (§A6), touches DEV/RELEASE code paths,
  writes outside `platlib` + `$TMPDIR`, or requires credentials.

### 4.2 `scripts/build-aerollm.sh status`

- **Promises (extended):** existing four lines unchanged, plus a `channel:`
  line (`dev` | `release` | `bundled` | `none`) and, when bundled, `bundle:
  aerollm <ver> (<short-sha>, built <date>)` read from
  `<platlib>/aerollm_api.bundle.json`. Missing marker ⇒ `channel: unknown
  (installed, provenance not recorded)` — never a crash.

### 4.3 `scripts/package-aerollm-bundle.sh` (new, maintainer-only)

- **Requires:** `$ARAIL_AEROLLM_REPO` present with a clean worktree.
- **Promises:** a reproducible-named tarball + `.sha256` + a `MANIFEST.json`
  whose `aerollm_commit` is the sibling repo's `HEAD` sha and whose
  `aerollm_dirty` is `true` if the worktree was dirty.
- **On bad input:** refuses to package from a dirty worktree unless
  `ALLOW_DIRTY=1` (a dirty bundle is unattributable, which is a licence
  problem, not just a hygiene one).

### 4.4 `AeroLLMBackend.__new__` ImportError message

- **Requires:** nothing new.
- **Promises:** the guidance string now names three routes in the order an
  outside user should try them: `./arailctl deep install` (bundled) first,
  `deep rebuild` (source) and `deep update` (wheel) after, flagged as
  maintainer paths. **No behavioral change** — still `ImportError`, still
  caught by the same caller, lab still runs without deep mode.

### 4.5 `arailctl deep <op>`

- Adds `install` (bundled) to the existing `rebuild | update | status`.
  Unknown op still exits 2 with the enumerated list. `deep` with no op still
  means `status`.

---

## 5. Decision: where the artifact lives

| Option | Repo bloat | Outside-user creds | Offline story | Refresh cost | Verdict |
|---|---|---|---|---|---|
| **(a) ARAIL's own GitHub Release asset** | none (out of git) | none — anonymous HTTPS | good: tarball can be sideloaded via `AEROLLM_BUNDLE_FILE` | one `gh release upload` per ARAIL release | **RECOMMENDED** |
| (b) Git LFS in-repo | 22 MB per revision, permanently, and **every fork inherits the LFS bandwidth cost** — fatal for a repo whose whole thesis is "fork me" | none | best (clone gets it) | a commit per refresh; history grows monotonically | reject |
| (c) Private bucket (S3/R2) | none | **requires creds or a signed-URL rotation scheme** — fails the acceptance bar outright | poor | infra to own | reject |

**Recommendation: (a).** It is the only option that satisfies all three
constraints simultaneously (no source access, no credentials, no
separately-installable public package) while keeping ARAIL's git history
clean. Critically, a release *asset* is **not** a package listing: it is not
resolvable by `pip install aerollm-api`, not indexed by PyPI, not
`pip search`-able, and not discoverable except by someone already on ARAIL's
releases page — which is exactly the disclosure surface the maintainer asked
for. The cost is that the asset is *publicly downloadable* (§A2) — confirm
before building.

Rejecting (b) deserves one extra sentence: ARAIL's `CLAUDE.md` already records
47 MB of history bloat the maintainer decided to live with rather than
rewrite. Adding a 22 MB binary that re-uploads on every refresh repeats that
mistake deliberately.

---

## 6. Design detail

### 6.1 `MANIFEST.json` (inside the tarball; installed as `aerollm_api.bundle.json`)

```json
{
  "schema": "arail.aerollm-bundle/v1",
  "aerollm_version": "1.0.0",
  "aerollm_commit": "9e08230f0bebfe5eeca5a2da3191fa4a96f24d2d",
  "aerollm_dirty": false,
  "built_at": "2026-08-05T00:00:00Z",
  "built_by": "scripts/package-aerollm-bundle.sh",
  "platform": "macos-arm64",
  "python_abi": "abi3-cp39",
  "sha256": "<sha256 of aerollm_api.abi3.so>",
  "license": "Apache-2.0",
  "modifications": "none — verbatim cargo --release build of the named commit",
  "arail_release": "v1.1.0"
}
```

`schema` is versioned for the same reason `arail.status/v2` is: `status`
parses it, and a future field addition must not break an old `status`.

### 6.2 Mode dispatch (additive; DEV/RELEASE untouched)

New explicit mode `bundle`. `auto` gains one branch, inserted **between** the
existing two so neither existing branch changes meaning:

```
auto:
  1. crate dir present                     → cargo_build()      [DEV, unchanged]
  2. AEROLLM_CHANNEL=release, OR
     AEROLLM_INDEX_URL overridden non-default, OR
     a pip index credential is configured  → pip_install()      [RELEASE, unchanged intent]
  3. otherwise                             → bundle_install()   [BUNDLED, new]
```

Rule-2's trigger deserves care: today `auto`'s else-branch is unconditionally
`pip_install`, and the maintainer's own machines rely on that when the sibling
repo is absent. Making rule 3 the new default *does* change what those machines
do — so rule 2 keeps an explicit escape hatch (`AEROLLM_CHANNEL=release`, and
`setup.sh` continues to export `AEROLLM_INDEX_URL`/`AEROLLM_PIP_SPEC` from
`pyproject.toml`). `AEROLLM_CHANNEL=dev|release|bundle` forces any channel
from any mode. **This is the one place existing behavior shifts; it is
intentional, it is the point of the sprint, and it must be called out in the
BUILD_LOG and CHANGELOG.**

### 6.3 New env knobs (all optional, all documented in `docs/cli.md`)

| Var | Default | Meaning |
|---|---|---|
| `AEROLLM_CHANNEL` | unset (auto) | force `dev` \| `release` \| `bundle` |
| `AEROLLM_BUNDLE_URL` | derived from `AEROLLM_BUNDLE_TAG` | full asset URL override (mirrors, forks) |
| `AEROLLM_BUNDLE_TAG` | pinned in `pyproject.toml` `[tool.arail.package-sources] aerollm_bundle_tag` | which ARAIL release carries the bundle |
| `AEROLLM_BUNDLE_FILE` | unset | use a local tarball; **the offline / airgapped install path** |
| `AEROLLM_BUNDLE_SHA256` | from the `.sha256` sidecar | pin/override the expected digest |

The pinned tag lives in `pyproject.toml` next to the existing `aerollm` /
`aerollm_index` keys, so all three channels are configured from one block.

### 6.4 Airgap doctrine

`LAB_MODE=airgapped` blocks *cloud inference providers*, not package installs —
`setup.sh` already runs `pip` and `ollama pull` under it. Downloading a release
asset during `setup` is in the same class as those and does not violate the
doctrine. But `bundle_install()` must never run at *lab runtime* (only from
`setup.sh` / an explicit `./arailctl deep install`), and
`AEROLLM_BUNDLE_FILE` exists so a genuinely disconnected machine has a path.

---

## 7. License compliance (Apache-2.0)

aeroLLM ships `LICENSE` (Apache-2.0 verbatim) and `NOTICE`. Verified content
of the NOTICE: from-scratch codebase, no vendored third-party source. ARAIL
becomes a **redistributor of a compiled Object form** of that work, which
triggers Apache-2.0 §4(a)/(b)/(c)/(d). Concretely ARAIL must carry:

**In-repo (committed, present in every clone/fork):**

```
THIRD-PARTY-LICENSES/aerollm/
  LICENSE      verbatim copy of aeroLLM's Apache-2.0            [§4(a)]
  NOTICE       verbatim copy of aeroLLM's NOTICE                [§4(d)]
  README.md    what the binary is, which commit, that it is
               unmodified, where the source can be obtained,
               and that Apache-2.0 §7/§8 warranty & liability
               disclaimers apply
  BUNDLE.json  the same manifest shape as §6.1, for the tag
               currently pinned in pyproject.toml
```

**In the release tarball** (so the compliance material travels with the
binary, not just with the repo): `LICENSE` + `NOTICE` + `MANIFEST.json`
alongside the `.so`, as drawn in §3.1.

**In ARAIL's root `NOTICE`:** one added paragraph naming AeroLLM, its
copyright line ("Copyright 2026 Charles Darnell and AeroLLM contributors"),
its licence, and a pointer to `THIRD-PARTY-LICENSES/aerollm/`. This mirrors
exactly what the repo already does for Llama 3.2 and Gemma in `licenses/`.

**In the GitHub Release body:** a short "Bundled third-party components"
section — component, version, commit, licence, link to
`THIRD-PARTY-LICENSES/aerollm/`.

**Modification disclosure [§4(b)]:** the artifact is a *verbatim* release
build of a named commit with **no** patches. `MANIFEST.json.modifications`
states `"none — verbatim cargo --release build of the named commit"`, and
`package-aerollm-bundle.sh` refuses to package from a dirty worktree
(§4.3) so that claim cannot silently become false.

**Source availability:** Apache-2.0 imposes **no** source-provision
obligation on Object-form redistribution (this is not the GPL). aeroLLM
staying private is fully compatible. The `README.md` should say where source
*may* be obtained rather than promising it.

**Licence coexistence:** ARAIL is MIT, aeroLLM is Apache-2.0. Apache-2.0
material shipped as a clearly-delineated third-party component under
`THIRD-PARTY-LICENSES/` does not relicense ARAIL and does not conflict. Do
not blur the two — no Apache headers in ARAIL sources.

---

## 8. Failure modes

Every row has a test in §9.

| # | Failure | Detection | Recovery |
|---|---|---|---|
| F1 | Downloaded `.so` won't `import` (missing dynamic dep, ABI drift, corrupt) | `verify_or_die()` runs `python3 -c "import aerollm_api"` immediately after install | Print the real `ImportError`, tell the user to run `./arailctl deep status`; **remove the just-installed `.so`** so a broken artifact never shadows a future good install; exit 1. Lab still runs (deep off). |
| F2 | Checksum mismatch (truncated download, tampered mirror) | `shasum -a 256 -c` against the `.sha256` sidecar / `AEROLLM_BUNDLE_SHA256`, **before** any `cp` | Abort with the expected-vs-actual digests; nothing installed; suggest retry, then `AEROLLM_BUNDLE_FILE`. |
| F3 | Asset 404 / release has no bundle / offline | `curl -fL` non-zero exit, HTTP status captured | Message names the resolved URL and the `AEROLLM_BUNDLE_FILE` sideload path; exit non-zero; setup.sh's existing `if …; then … else warn` keeps setup green. |
| F4 | Wrong platform (Intel Mac, Linux, CUDA host) | `uname -s`/`uname -m` guard at the top of `bundle_install()` | Refuse before any network call, reusing the existing macOS-arm64-only wording; setup.sh's non-Darwin branch already handles this above us. |
| F5 | Quarantine xattr blocks `dlopen` (user downloaded the tarball via a browser) | `import` fails with a Gatekeeper/`code signature` error | `xattr -d com.apple.quarantine` best-effort *before* verify; if verify still fails, F1's message names the quarantine remedy explicitly. |
| F6 | Bundle is stale vs. the aeroLLM commit the maintainer expects | `MANIFEST.aerollm_commit` vs the pinned `aerollm_bundle_tag`; `status` prints both | `status` shows `bundle: <ver> (<sha>)`; a mismatch is a maintainer-side signal to re-run `package-aerollm-bundle.sh`. Not auto-healed — §10. |
| F7 | Bundled install silently shadows a maintainer's DEV source build | `bundle_install()` refuses to overwrite when `aerollm_api.bundle.json` is **absent** but `aerollm_api.abi3.so` is **present** (⇒ some other channel owns it) unless `--force` | Refuse + explain + name `--force`; protects the maintainer's in-progress cargo build. |
| F8 | Two interpreters (system python3 vs venv) — installed to the wrong `platlib` | Existing `verify_or_die` venv-mismatch warning, retained verbatim | Unchanged message ("Check that $PY is the same interpreter ARAIL runs"). |
| F9 | `auto` now picks BUNDLED on a maintainer machine that wanted RELEASE | `status` prints `channel:` | `AEROLLM_CHANNEL=release` escape hatch (§6.2); documented in CHANGELOG as a behavior change. |
| F10 | Licence material drifts from the shipped binary (bundle refreshed, `THIRD-PARTY-LICENSES/` not) | A repo-level test asserts `THIRD-PARTY-LICENSES/aerollm/BUNDLE.json.aerollm_commit` matches the tag pinned in `pyproject.toml` | Test fails in CI ⇒ compliance drift is a build break, not a surprise. |
| F11 | Dirty-worktree bundle ⇒ "unmodified" claim is false | `package-aerollm-bundle.sh` refuses dirty (§4.3) | Maintainer commits or sets `ALLOW_DIRTY=1`, which stamps `aerollm_dirty: true` and forces a modification note. |

---

## 9. Test strategy

Bash-level tests follow the repo's existing `tests/` pattern for script
surfaces; Python tests use pytest. **No test may hit the real network by
default** — all download tests point `AEROLLM_BUNDLE_URL` at a `file://` URL
or use `AEROLLM_BUNDLE_FILE`.

**Unit (script):**
- `bundle_install()` refuses on non-Darwin / non-arm64 before any network call (F4).
- Checksum mismatch aborts with nothing written to a fake `platlib` (F2).
- `curl` failure surfaces the resolved URL and exits non-zero (F3).
- Manifest with an unknown extra field still parses in `status` (schema v1 forward-compat).
- Missing `aerollm_api.bundle.json` ⇒ `status` prints `channel: unknown`, exit 0 (§4.2).
- F7 refuse-to-shadow, and `--force` overriding it.
- `package-aerollm-bundle.sh` refuses a dirty worktree; `ALLOW_DIRTY=1` stamps `aerollm_dirty: true` (F11).

**Unit (Python):**
- `AeroLLMBackend.__new__` with `aerollm_api` absent raises `ImportError` whose
  message contains `deep install` — assert the *contract*, not the prose.

**Integration — the acceptance bar (§9.1 below).**

**Regression (must stay green, unchanged):**
- `auto` with the sibling crate dir present still runs `cargo_build` (DEV).
- `AEROLLM_CHANNEL=release` still runs `pip_install` against `AEROLLM_INDEX_URL` (RELEASE).
- `deep rebuild` / `deep update` / `deep status` dispatch unchanged; unknown op still exits 2.
- `setup.sh` with `ARAIL_SKIP_AEROLLM_PROBE=1` still skips everything.
- A failing `bundle_install()` leaves `./arailctl setup` exiting 0 (graceful degradation preserved).

**Compliance:**
- `THIRD-PARTY-LICENSES/aerollm/{LICENSE,NOTICE,README.md,BUNDLE.json}` all exist and are non-empty.
- `LICENSE` is byte-identical to `~/ProJects/qukaizen-aerollm/LICENSE`; `NOTICE` likewise.
- Root `NOTICE` mentions AeroLLM and points at the directory.
- `BUNDLE.json.aerollm_commit` matches `pyproject.toml`'s pinned tag manifest (F10).

**Performance:** not applicable (a one-time ~22 MB download); assert only that
`bundle_install()` streams to `$TMPDIR` and does not buffer the tarball in a
shell variable.

**Security:**
- Download URL is `https://` and the scheme is asserted before `curl` runs (reject `http://`, reject non-URL schemes in `AEROLLM_BUNDLE_URL`).
- The `.so` is installed only after digest verification (ordering test, F2).
- `AEROLLM_BUNDLE_FILE` path is not `eval`'d and is quoted throughout.
- No credential, token, or index URL is ever printed by `bundle_install()` or `status`.
- Extraction happens into a fresh `mktemp -d`, and only the four expected filenames are copied out (a tarball with `../` entries cannot escape).

### 9.1 Acceptance test — the "outside user" simulation

This is the bar; if it doesn't pass, the sprint doesn't ship. On a macOS-arm64
box, in a clean environment:

1. Clone **only** ARAIL into a scratch dir.
2. Guarantee no aeroLLM source: `ARAIL_AEROLLM_REPO=/nonexistent`.
3. Guarantee no private-index creds: unset any `PIP_*`/netrc/keyring entry for
   `pypi.qukaizen.com`, and **leave `AEROLLM_INDEX_URL` unset** (do not
   override it to an unreachable host — `_release_creds_configured()`
   treats *any* non-default `AEROLLM_INDEX_URL` as configured credentials
   by design, so overriding it here selects RELEASE and never reaches
   BUNDLED at all; this step originally said the opposite and was wrong.
   See TEST_REPORT.md Q2, 2026-08-05).
4. Fresh interpreter: a new venv, `aerollm_api` provably not importable.
5. Run `./arailctl setup` at tier `maximus` (or `./arailctl deep install`).
6. **Assert:** exit 0; `python3 -c "import aerollm_api"` succeeds;
   `./arailctl deep status` reports `channel: bundled` with a version + commit;
   and a real chat turn routed through `AeroLLMBackend` returns non-empty text.

Step 6's last clause is the one that matters — "imports" is not "works." A
model checkpoint must be present for that turn; if hardware/checkpoint is
unavailable at QA time, record it as a **hardware-pending** sub-gate rather
than silently downgrading the assertion to import-only.

---

## 10. Versioning & staleness — deliberately manual

The refresh loop is **one documented maintainer step**, not CI:

```
cd ~/ProJects/qukaizen-aerollm && git pull        # new aeroLLM
cd ~/ProJects/arail
bash scripts/package-aerollm-bundle.sh            # build + manifest + tarball + sha256
gh release upload <next-arail-tag> dist/aerollm-bundle/*.tar.gz{,.sha256}
# bump aerollm_bundle_tag in pyproject.toml + refresh THIRD-PARTY-LICENSES/aerollm/BUNDLE.json
```

Written up in `docs/releasing.md` (or a new §  if that file doesn't exist) as
a release-checklist item. **No GitHub Actions workflow is added** — CI cannot
build the artifact anyway without access to the private aeroLLM source, so
automation here would mean plumbing a deploy key into a public repo's CI to
check out a private repo. That is a materially worse security posture than a
manual step, and it was not asked for.

The only automation added is the **F10 drift test**, which is a plain pytest
assertion that runs in the existing suite: if the pinned tag and the committed
compliance manifest disagree, the build breaks. That is the right amount.

Staleness is *visible* rather than *prevented*: `deep status` always prints the
installed bundle's aeroLLM version + short commit + build date, so "which
aeroLLM am I actually running?" is one command, on any machine, for any
channel.

---

## 11. Non-goals (explicit)

- **No change to aeroLLM's repo or release pipeline.** The staged public-1.0-cut
  sprint over there stays exactly as reviewed and stays deferred. This sprint
  touches zero files under `~/ProJects/qukaizen-aerollm` (it *reads* LICENSE,
  NOTICE, and builds from it — nothing more).
- **No public-PyPI publication of `aerollm-api`.** A release asset is not a
  package listing (§5).
- **No CUDA, no Linux, no Intel Mac.** macOS-arm64 only, matching current scope.
  The platform guard (F4) makes that a refusal, not a mystery.
- **No behavioral change to DEV or RELEASE** beyond `auto`'s new third branch
  and the `AEROLLM_CHANNEL` escape hatch (§6.2, flagged as the one deliberate
  shift).
- **No new CI workflow.** §10.
- **No Compute-Source UI change.** The pivot already exposes deep mode; this
  sprint makes the backend *present*, not *differently presented*.
- **No model weights bundled.** The runtime is 22 MB; checkpoints stay on the
  existing `ARAIL_MODELS_DIR` path and are the user's own download.

---

## 12. Tech debt

**Added:**
- A binary artifact whose provenance depends on a maintainer running a script
  correctly. Mitigated by the manifest + F10 + F11, not eliminated.
- A fourth thing to remember at ARAIL release time (§10 checklist).
- A public download URL ARAIL now depends on for a working maximus tier.
  `AEROLLM_BUNDLE_FILE` is the escape hatch; there is no mirror.
- Compliance material that must be refreshed in lockstep with the binary (F10
  converts this from silent drift into a test failure).

**Repaid:**
- The "outside users get zero deep mode" gap — the actual point.
- `build-aerollm.sh status` becomes channel-aware, so "which aeroLLM is
  installed and where did it come from" stops being unanswerable on any machine.
- ARAIL's third-party-licence story gains a `THIRD-PARTY-LICENSES/` convention
  that the next bundled component can reuse (`licenses/` today covers model
  weights only).

**Net:** slightly positive debt, concentrated in the manual refresh loop, and
deliberately so — automating it would require a private-repo deploy key in a
public repo's CI (§10). Track the refresh step in `sprints/BACKLOG.md`.

---

## 13. Recommended implementation order

1. **Confirm A2 with the maintainer** — publishing a compiled aeroLLM binary on
   a public ARAIL release. Nothing else is worth building until this is a yes.
2. `THIRD-PARTY-LICENSES/aerollm/` + root `NOTICE` paragraph + the F10 drift
   test. Compliance first: it is the only part that is a legal obligation
   rather than a feature, and doing it first means no unlicensed binary ever
   exists in the pipeline.
3. `scripts/package-aerollm-bundle.sh` (producer) + its dirty-worktree refusal.
   Produce one real tarball locally and inspect it.
4. `bundle_install()` in `build-aerollm.sh`, driven entirely by
   `AEROLLM_BUNDLE_FILE` against the local tarball from step 3 — no network yet.
   Unit tests F2/F4/F7 land here.
5. URL resolution + `curl` download path + `.sha256` sidecar (F3, F5) and the
   `https`-only scheme guard.
6. `auto` dispatch third branch + `AEROLLM_CHANNEL` escape hatch + regression
   tests proving DEV/RELEASE are untouched (§9 regression block).
7. `status` channel-awareness; `arailctl deep install`; `setup.sh` messaging.
8. `AeroLLMBackend` ImportError message (three routes, ordered) + its contract test.
9. Docs: `docs/cli.md` (`deep install`, the env table), release checklist (§10),
   CHANGELOG entry that names the `auto` behavior shift (F9).
10. Cut a real ARAIL pre-release with the asset attached; run §9.1 end-to-end.
