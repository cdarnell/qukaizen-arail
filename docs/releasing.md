# Releasing ARAIL

This is a checklist doc, not a comprehensive process guide — add sections
as new release-time steps show up.

## Refreshing the bundled QueueLLM binary

The BUNDLED install channel (`./arailctl deep install`,
`scripts/build-aerollm.sh bundle`) fetches a prebuilt `aerollm_api.abi3.so`
from a GitHub Release asset on `cdarnell/qukaizen-arail`. This is a
**manual, maintainer-only step** — deliberately not automated (no
GitHub Actions workflow builds it, because CI has no access to the private
QueueLLM source; plumbing a deploy key for that into a public repo's CI
would be a materially worse security posture than a documented manual
step). See `sprints/2026-08-05-arail-bundled-aerollm/ARCHITECTURE.md` §10
for the full rationale.

Refresh the bundle whenever QueueLLM ships something worth carrying, or at
minimum once per ARAIL release that bumps `LAB_TIER=maximus`'s expected
capability:

```sh
# 1. Pull the latest QueueLLM source.
cd ~/ProJects/qukaizen-aerollm && git pull

# 2. Build + package. Refuses a dirty QueueLLM worktree — commit or stash
#    first, or ALLOW_DIRTY=1 if you understand the modification-disclosure
#    consequence (F11 in ARCHITECTURE.md).
cd ~/ProJects/arail
ARAIL_RELEASE_TAG=<next-arail-tag> bash scripts/package-aerollm-bundle.sh

# 3. Upload the produced tarball + sidecar to the ARAIL release, UNDER THE
#    SAME NAME the producer wrote them — do not rename.
#
#    Filename convention (must match resolve_bundle_url() in
#    scripts/build-aerollm.sh exactly):
#        aerollm-api-<ARAIL release tag>-macos-arm64.tar.gz
#        aerollm-api-<ARAIL release tag>-macos-arm64.tar.gz.sha256
#    e.g. for ARAIL_RELEASE_TAG=v1.1.0:
#        aerollm-api-v1.1.0-macos-arm64.tar.gz
#        aerollm-api-v1.1.0-macos-arm64.tar.gz.sha256
#    The name is derived ONLY from the ARAIL release tag — not QueueLLM's
#    own version or commit hash — so it's identical every time this
#    checklist runs for the same release, and identical to what the
#    installer's resolve_bundle_url() constructs at install time. If
#    <next-arail-tag> above doesn't match AEROLLM_BUNDLE_TAG (the pin in
#    pyproject.toml, step 4 below), the bundled channel 404s for every
#    outside user — this is the single most important invariant in this
#    checklist.
gh release upload <next-arail-tag> dist/aerollm-bundle/*.tar.gz dist/aerollm-bundle/*.tar.gz.sha256

# 4. Bump the pin and refresh the compliance manifest so F10's drift test
#    stays green:
#      - pyproject.toml: [tool.arail.package-sources] aerollm_bundle_tag
#      - pyproject.toml: [tool.arail.package-sources] aerollm_bundle_sha256
#        — the sha256 of the TARBALL (dist/aerollm-bundle/*.tar.gz.sha256),
#        NOT the .so digest in BUNDLE.json below. This is the out-of-band
#        pin ./arailctl setup forwards as AEROLLM_BUNDLE_SHA256; forgetting
#        it doesn't break installs (falls back to the same-origin sidecar)
#        but leaves the genuinely-independent pin stale.
#      - THIRD-PARTY-LICENSES/aerollm/BUNDLE.json (copy from
#        dist/aerollm-bundle/stage/MANIFEST.json, or dist/aerollm-bundle/*.tar.gz)
#      - THIRD-PARTY-LICENSES/aerollm/NOTICE — refresh from upstream if it
#        changed (byte-diff against ~/ProJects/qukaizen-aerollm/NOTICE).
#      - THIRD-PARTY-LICENSES/aerollm/LICENSE — DO NOT sync this from
#        upstream. Upstream's own LICENSE file is only the Apache-2.0
#        header boilerplate + copyright line (17 lines), not the full
#        ~200-line license text Apache-2.0 §4(a) requires redistributors to
#        include (see REVIEW.md finding A1). This bundle's LICENSE is
#        deliberately ARAIL's own full-text copy and must stay that way —
#        byte-syncing it from upstream reinstalls the stub and breaks
#        `test_license_is_full_apache2_text_not_upstream_stub` in
#        tests/test_aerollm_bundle_compliance.py, which exists specifically
#        to catch this regression. Only touch LICENSE if the Apache-2.0
#        *license text itself* changes (it doesn't, in practice).
#
# 5. Add a "Bundled third-party components" section to the GitHub Release
#    body: component, version, commit, licence, link to
#    THIRD-PARTY-LICENSES/aerollm/ (ARCHITECTURE.md §7).
#
# 6. Run: pytest tests/test_aerollm_bundle_compliance.py
#    (F10 drift assertion — arail_release must match the pinned tag)
```

Staleness is visible, not prevented: `./arailctl deep status` always
prints the installed bundle's QueueLLM version + short commit + build date
for any channel, so "which QueueLLM am I actually running?" is one command
away.
