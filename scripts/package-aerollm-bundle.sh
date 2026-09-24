#!/usr/bin/env bash
# package-aerollm-bundle.sh — maintainer-only producer for the BUNDLED
# install channel (scripts/build-aerollm.sh bundle).
#
# Builds aerollm_api from the local sibling source repo, then packages a
# verbatim copy of the compiled extension plus the Apache-2.0 compliance
# material into a tarball + sha256 sidecar, ready for
# `gh release upload <arail-tag> dist/aerollm-bundle/*.tar.gz{,.sha256}`.
#
# This script is NEVER run in CI (see ARCHITECTURE.md §10 — CI has no
# access to the private aeroLLM source). It is a manual, documented
# maintainer step, run from ~/ProJects/arail against a sibling checkout of
# ~/ProJects/qukaizen-queuellm.
#
# Usage:
#   bash scripts/package-aerollm-bundle.sh
#   ALLOW_DIRTY=1 bash scripts/package-aerollm-bundle.sh   # dirty worktree override
#
# Env:
#   ARAIL_AEROLLM_REPO   sibling aeroLLM checkout (default ~/ProJects/qukaizen-queuellm)
#   OUT_DIR              output directory for the tarball + sidecar (default
#                         $REPO_ROOT/dist/aerollm-bundle). Override in tests
#                         so the regression suite NEVER touches the real
#                         release output directory — this script `rm -rf`s
#                         OUT_DIR on every run (REVIEW.md round-2 B3).
#   ARAIL_RELEASE_TAG    the ARAIL GitHub Release tag this bundle is built for
#                         (e.g. v1.1.0). REQUIRED — this is also the tag the
#                         consumer (scripts/build-aerollm.sh resolve_bundle_url)
#                         requests, so the two MUST match at publish time. The
#                         output filename is derived from this tag, not from
#                         aeroLLM's own version/commit, so a rebuild at the same
#                         ARAIL release always produces the same asset name.
#   ALLOW_DIRTY          "1" to package from a dirty aeroLLM worktree anyway;
#                         MANIFEST.json.aerollm_dirty is stamped true and a
#                         modification note is appended.
set -euo pipefail

if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    BOLD=$'\033[1m'; RED=$'\033[31m'; GRN=$'\033[32m'; YLW=$'\033[33m'; RST=$'\033[0m'
else
    BOLD=""; RED=""; GRN=""; YLW=""; RST=""
fi
info() { printf '%s\n' "${GRN}•${RST} $*"; }
warn() { printf '%s\n' "${YLW}!${RST} $*" >&2; }
err()  { printf '%s\n' "${RED}✗${RST} $*" >&2; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AEROLLM_REPO="${ARAIL_AEROLLM_REPO:-$HOME/ProJects/qukaizen-queuellm}"
CRATE_DIR="$AEROLLM_REPO/crates/aerollm-api"
OUT_DIR="${OUT_DIR:-$REPO_ROOT/dist/aerollm-bundle}"
PY="${PYTHON:-python3}"

if [[ -z "${ARAIL_RELEASE_TAG:-}" ]]; then
    err "ARAIL_RELEASE_TAG is required — it names the GitHub Release this bundle"
    err "publishes to, and the output filename is derived from it so it matches"
    err "what scripts/build-aerollm.sh's resolve_bundle_url() requests."
    warn "Example: ARAIL_RELEASE_TAG=v1.1.0 bash scripts/package-aerollm-bundle.sh"
    exit 1
fi
if [[ ! -d "$CRATE_DIR" ]]; then
    err "No aerollm sibling repo at ${BOLD}${AEROLLM_REPO}${RST} — this is a maintainer-only script."
    exit 1
fi
if ! command -v cargo >/dev/null 2>&1; then
    err "cargo not found — install the Rust toolchain (https://rustup.rs) and retry."
    exit 1
fi

# ── dirty-worktree refusal (§4.3, F11) — an unattributable build is a
# licence problem, not just a hygiene one. Apache-2.0 §4(b) requires ARAIL
# to state any modifications; "verbatim build of commit X" is only true if
# X is exactly what's on disk.
cd "$AEROLLM_REPO"
DIRTY="false"
if [[ -n "$(git status --porcelain)" ]]; then
    if [[ "${ALLOW_DIRTY:-0}" != "1" ]]; then
        err "aeroLLM worktree at ${BOLD}${AEROLLM_REPO}${RST} is dirty."
        warn "A dirty bundle can't honestly claim 'verbatim build of commit X'."
        warn "Commit or stash first, or override with ALLOW_DIRTY=1 (stamps aerollm_dirty: true)."
        exit 1
    fi
    warn "Worktree is dirty — packaging anyway (ALLOW_DIRTY=1). MANIFEST.json.aerollm_dirty=true."
    DIRTY="true"
fi
COMMIT="$(git rev-parse HEAD)"
SHORT_COMMIT="$(git rev-parse --short HEAD)"
cd "$REPO_ROOT"

info "Building aerollm_api from ${BOLD}${AEROLLM_REPO}${RST} @ ${SHORT_COMMIT} (cargo --release)…"
( cd "$AEROLLM_REPO" && cargo build --release -p aerollm-api --features extension-module )

BUILT=""
for cand in \
    "$AEROLLM_REPO/target/release/libaerollm_api.dylib" \
    "$AEROLLM_REPO/target/release/libaerollm_api.so"; do
    [[ -f "$cand" ]] && BUILT="$cand" && break
done
if [[ -z "$BUILT" ]]; then
    err "cargo build succeeded but no libaerollm_api.{dylib,so} under target/release."
    exit 1
fi

# aeroLLM's own workspace version (Cargo.toml [workspace.package] version).
AEROLLM_VERSION="$("$PY" - "$AEROLLM_REPO/Cargo.toml" <<'PYEOF'
import re, sys
text = open(sys.argv[1]).read()
m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
print(m.group(1) if m else "unknown")
PYEOF
)"

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR/stage"
STAGE="$OUT_DIR/stage"

cp -f "$BUILT" "$STAGE/aerollm_api.abi3.so"
# LICENSE ships from ARAIL's own THIRD-PARTY-LICENSES copy, NOT upstream's
# LICENSE file — upstream's is only the Apache-2.0 header boilerplate (17
# lines), not the full license text Apache-2.0 §4(a) requires redistributors
# to include. NOTICE is a verbatim copy of upstream's (its claim is
# accurate). See docs at THIRD-PARTY-LICENSES/aerollm/README.md.
cp -f "$REPO_ROOT/THIRD-PARTY-LICENSES/aerollm/LICENSE" "$STAGE/LICENSE"
cp -f "$AEROLLM_REPO/NOTICE" "$STAGE/NOTICE"

SHA256="$(shasum -a 256 "$STAGE/aerollm_api.abi3.so" | awk '{print $1}')"
BUILT_AT="$("$PY" -c 'import datetime; print(datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))')"
ARAIL_RELEASE="$ARAIL_RELEASE_TAG"
MODIFICATIONS="none — verbatim cargo --release build of the named commit"
if [[ "$DIRTY" == "true" ]]; then
    MODIFICATIONS="worktree was dirty at build time (ALLOW_DIRTY=1) — not a strictly verbatim build of the named commit; see aerollm_commit for the base"
fi

cat > "$STAGE/MANIFEST.json" <<JSONEOF
{
  "schema": "arail.aerollm-bundle/v1",
  "aerollm_version": "${AEROLLM_VERSION}",
  "aerollm_commit": "${COMMIT}",
  "aerollm_dirty": ${DIRTY},
  "built_at": "${BUILT_AT}",
  "built_by": "scripts/package-aerollm-bundle.sh",
  "platform": "macos-arm64",
  "python_abi": "abi3-cp39",
  "sha256": "${SHA256}",
  "license": "Apache-2.0",
  "modifications": "${MODIFICATIONS}",
  "arail_release": "${ARAIL_RELEASE}"
}
JSONEOF

# Filename convention (must match resolve_bundle_url() in build-aerollm.sh):
# aerollm-api-<ARAIL release tag>-macos-arm64.tar.gz — tag-only, no aeroLLM
# version or commit hash. A GitHub Release is already tagged by version;
# baking aeroLLM's commit into the filename too would produce a new,
# unresolvable filename on every rebuild at the same ARAIL release. The
# commit and version are still recorded in MANIFEST.json for provenance.
TARBALL="$OUT_DIR/aerollm-api-${ARAIL_RELEASE_TAG}-macos-arm64.tar.gz"
( cd "$STAGE" && tar czf "$TARBALL" aerollm_api.abi3.so MANIFEST.json LICENSE NOTICE )
shasum -a 256 "$TARBALL" | awk -v f="$(basename "$TARBALL")" '{print $1"  "f}' > "${TARBALL}.sha256"

info "${GRN}Bundle packaged${RST}: ${TARBALL}"
info "  sha256:  $(cat "${TARBALL}.sha256")"
info "  commit:  ${COMMIT} (dirty=${DIRTY})"
info "Next: gh release upload <arail-tag> ${TARBALL} ${TARBALL}.sha256"
