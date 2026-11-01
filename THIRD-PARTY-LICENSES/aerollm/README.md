# Third-party component: AeroLLM

ARAIL's `deep` (maximus-tier) inference backend can be satisfied by a
prebuilt, checksummed AeroLLM Python extension (`aerollm_api.abi3.so`)
published as a binary asset on **ARAIL's own** GitHub Releases
(`https://github.com/cdarnell/qukaizen-arail/releases`). This directory
carries the Apache-2.0 compliance material that redistribution requires.

- **Component:** AeroLLM Runtime (`aerollm-api` Python extension)
- **Upstream:** a private source repository (not publicly browsable at this
  time — the compiled binary is what's redistributed, not the source; the
  maintainer may open the source repository in the future)
- **License:** Apache License, Version 2.0 — see [`LICENSE`](./LICENSE).
  This is the full, standard Apache-2.0 license text (verbatim from
  `apache.org/licenses/LICENSE-2.0.txt`), not a copy of upstream's own
  `LICENSE` file — upstream's is only the header boilerplate + copyright
  line, not the full ~200-line license, so a byte-for-byte copy of it would
  not satisfy Apache-2.0 §4(a)'s "give recipients a copy of this License."
- **Notice:** see [`NOTICE`](./NOTICE) (verbatim copy) — AeroLLM is a
  from-scratch codebase; no third-party source is vendored in it
- **What's shipped:** a *verbatim*, unmodified `cargo build --release`
  Object-form build of a single named commit. `BUNDLE.json` in this
  directory records exactly which commit, and whether the build tree was
  clean, for the version currently pinned in `pyproject.toml`'s
  `[tool.arail.package-sources] aerollm_bundle_tag`.
- **Modifications:** none. ARAIL never rewrites the `.so` bytes — the
  ad-hoc code signature embedded by `cargo build --release` on macOS arm64
  would not survive a rewrite, and doing so would also break the
  Apache-2.0 §4(b) "no modifications" claim.
- **Where source may be obtained:** Apache-2.0 imposes no source-provision
  obligation on Object-form redistribution. Source access, if and when it
  opens up, will be announced from the AeroLLM project itself, not from
  this file — do not assume a URL here is current.
- **Warranty / liability:** as with all Apache-2.0 software, AeroLLM is
  provided "AS IS", WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, express
  or implied. See `LICENSE` §7 and §8 for the full disclaimer and
  limitation of liability.

## How this material stays in sync with the shipped binary

`BUNDLE.json` in this directory must match the `MANIFEST.json` baked into
the tarball attached to the ARAIL release named by
`[tool.arail.package-sources] aerollm_bundle_tag` in `pyproject.toml`. A
repo-level test (`tests/test_aerollm_bundle_compliance.py`) asserts the two
agree on `aerollm_commit`; if a maintainer refreshes the binary without
refreshing this directory, that test fails the build rather than silently
drifting. See `docs/releasing.md` for the manual refresh checklist.

## Licence coexistence

ARAIL itself is MIT-licensed (see the repo root `LICENSE`). AeroLLM is
Apache-2.0. This directory is a clearly-delineated third-party component;
shipping it does not relicense ARAIL and the two licenses do not conflict.
