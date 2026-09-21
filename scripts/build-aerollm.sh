#!/usr/bin/env bash
# build-aerollm.sh — install ARAIL's 2nd (deep) inference, aerollm_api.
#
# Three install channels:
#   • DEV (local sibling repo present): build from source via cargo so your
#     in-progress aerollm changes flow straight into the lab.
#   • RELEASE: pip install the published wheel from the self-hosted index
#     (https://pypi.qukaizen.com/simple/), with public PyPI as a fallback.
#     Wheels are macOS-arm64-only; off-Mac pip reports "no matching
#     distribution" (expected — there's no Linux/CUDA build yet). Needs
#     private-index credentials in practice.
#   • BUNDLED (no sibling repo, no index credentials): fetch a prebuilt,
#     checksummed aerollm_api.abi3.so from an ARAIL GitHub Release asset and
#     install it directly — the channel an outside user actually gets.
#
# Modes:
#   auto    (default)  sibling present → cargo build; AEROLLM_CHANNEL=release
#                       or index creds configured → pip; else → bundle
#   build              force a local cargo build from $ARAIL_AEROLLM_REPO
#   update             pip install --upgrade from the index (release channel)
#   bundle             force the BUNDLED channel (see bundle_install() below)
#   status             report importability, version, resolved paths, channel
#
# AEROLLM_CHANNEL=dev|release|bundle forces a channel from any mode.
#
# IMPORTANT: the source build uses `cargo build`, NOT `maturin develop`. maturin
# perturbs the cargo fingerprint (PYO3_ENVIRONMENT_SIGNATURE /
# CARGO_ENCODED_RUSTFLAGS), forcing a fresh Metal kernel compile that fails on
# macOS arm64 (mlx-sys program-scope device variables, Metal Toolchain cryptexd).
set -euo pipefail

MODE="${1:-auto}"
[[ $# -gt 0 ]] && shift || true

# ── minimal logging (no dep on setup.sh's helpers) ───────────────────────────
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    BOLD=$'\033[1m'; RED=$'\033[31m'; GRN=$'\033[32m'; YLW=$'\033[33m'; RST=$'\033[0m'
else
    BOLD=""; RED=""; GRN=""; YLW=""; RST=""
fi
info() { printf '%s\n' "${GRN}•${RST} $*"; }
warn() { printf '%s\n' "${YLW}!${RST} $*" >&2; }
err()  { printf '%s\n' "${RED}✗${RST} $*" >&2; }

AEROLLM_REPO="${ARAIL_AEROLLM_REPO:-$HOME/ProJects/qukaizen-queuellm}"
CRATE_DIR="$AEROLLM_REPO/crates/aerollm-api"
PY="${PYTHON:-python3}"
# Release channel — setup.sh overrides these from pyproject
# [tool.arail.package-sources] (aerollm = pin, aerollm_index = URL).
AEROLLM_INDEX_URL="${AEROLLM_INDEX_URL:-https://pypi.qukaizen.com/simple/}"
AEROLLM_PIP_SPEC="${AEROLLM_PIP_SPEC:-aerollm-api}"

# Bundled channel — tag + sha256 pin come from pyproject
# [tool.arail.package-sources] (aerollm_bundle_tag / aerollm_bundle_sha256).
# setup.sh forwards them as env vars; when invoked standalone (`arailctl deep
# install` → `bash scripts/build-aerollm.sh bundle`) we read pyproject
# ourselves so the standalone route gets the same pinned digest instead of an
# unpinned download (QA R2-1). Env vars always win; the literals below are
# last-resort fallbacks for a checkout whose Python lacks tomllib.
# Resolve the repo's pyproject.toml relative to this script, not $PWD.
# AEROLLM_PYPROJECT overrides the location (tests point it at a nonexistent
# path to exercise the genuinely-no-pin fallback behavior).
_ARAIL_PYPROJECT="${AEROLLM_PYPROJECT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/pyproject.toml}"
_read_bundle_pin() {  # $1 = pyproject key; empty output on any failure
    # shellcheck disable=SC2016  # single quotes are deliberate: Python source, not shell expansion
    "$PY" -c '
import sys
try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        # Pre-3.11 interpreter without tomli (e.g. macOS system python3):
        # fall back to a line parse. The pins are simple `key = "value"`
        # lines in [tool.arail.package-sources]; first match wins.
        import re
        with open(sys.argv[2], encoding="utf-8") as fh:
            for line in fh:
                m = re.match(
                    r"^\s*" + re.escape(sys.argv[1]) + r"\s*=\s*\"([^\"]*)\"", line
                )
                if m:
                    print(m.group(1))
                    break
        sys.exit(0)
from pathlib import Path
data = tomllib.loads(Path(sys.argv[2]).read_text())
print(str(data.get("tool", {}).get("arail", {}).get("package-sources", {}).get(sys.argv[1], "")))
' "$1" "$_ARAIL_PYPROJECT" 2>/dev/null || true
}
AEROLLM_BUNDLE_REPO="${AEROLLM_BUNDLE_REPO:-cdarnell/qukaizen-arail}"
AEROLLM_BUNDLE_TAG="${AEROLLM_BUNDLE_TAG:-$(_read_bundle_pin aerollm_bundle_tag)}"
AEROLLM_BUNDLE_TAG="${AEROLLM_BUNDLE_TAG:-v1.1.0}"
AEROLLM_BUNDLE_URL="${AEROLLM_BUNDLE_URL:-}"        # full override (mirrors, forks)
AEROLLM_BUNDLE_FILE="${AEROLLM_BUNDLE_FILE:-}"      # local tarball — the offline path
AEROLLM_BUNDLE_SHA256="${AEROLLM_BUNDLE_SHA256:-$(_read_bundle_pin aerollm_bundle_sha256)}"
FORCE=0
for _arg in "$@"; do
    [[ "$_arg" == "--force" ]] && FORCE=1
done
unset _arg

site_dir()  { "$PY" -c "import sysconfig; print(sysconfig.get_path('platlib'))"; }
# The interpreter's platlib can be missing or read-only (macOS system
# Python: /Library/Python/3.9/site-packages). Fail with a route, not a raw
# cp error (observed on a fresh machine, 2026-08-06).
require_writable_site_dir() {  # $1 = dir
    if [[ ! -d "$1" || ! -w "$1" ]]; then
        err "Cannot install into ${BOLD}$1${RST} — directory is missing or not writable."
        warn "This usually means \$PYTHON resolved to the macOS system interpreter."
        warn "Run via ${BOLD}./arailctl deep install${RST} (targets the lab's .venv), or set"
        warn "PYTHON to the interpreter ARAIL actually runs."
        exit 1
    fi
}
import_ok() { "$PY" -c "import aerollm_api" >/dev/null 2>&1; }
aerollm_version() {
    "$PY" -c "import aerollm_api; print(getattr(aerollm_api,'__version__','unknown'))" 2>/dev/null || echo "unknown"
}

clone_hint() {
    err "No aerollm sibling repo at ${BOLD}${AEROLLM_REPO}${RST}."
    warn "For a source (dev) build, either:"
    warn "  1) clone it next to arail:"
    warn "       git clone https://github.com/cdarnell/qukaizen-queuellm \"${AEROLLM_REPO}\""
    warn "  2) or point ARAIL_AEROLLM_REPO at your checkout."
    warn "Or install the published wheel instead: ./arailctl deep update"
}

cargo_build() {
    if [[ ! -d "$CRATE_DIR" ]]; then clone_hint; exit 1; fi
    if ! command -v cargo >/dev/null 2>&1; then
        err "cargo not found — install the Rust toolchain (https://rustup.rs) and retry."
        exit 1
    fi
    info "Building aerollm_api from ${BOLD}${AEROLLM_REPO}${RST} (cargo --release)…"
    ( cd "$AEROLLM_REPO" && cargo build --release -p aerollm-api --features extension-module )
    local built=""
    for cand in \
        "$AEROLLM_REPO/target/release/libaerollm_api.dylib" \
        "$AEROLLM_REPO/target/release/libaerollm_api.so"; do
        [[ -f "$cand" ]] && built="$cand" && break
    done
    if [[ -z "$built" ]]; then
        err "cargo build succeeded but no libaerollm_api.{dylib,so} under target/release."
        exit 1
    fi
    local dest; dest="$(site_dir)"
    require_writable_site_dir "$dest"
    dest="$dest/aerollm_api.abi3.so"
    info "Installing → ${dest}"
    cp -f "$built" "$dest"
    verify_or_die
    info "${GRN}AeroLLM ready${RST} (source build) — the deep-mode 2nd inference."
}

pip_install() {  # $* = extra pip args (e.g. --upgrade)
    info "Installing aerollm_api from index ${BOLD}${AEROLLM_INDEX_URL}${RST}…"
    if ! "$PY" -m pip install "$@" \
            --index-url "$AEROLLM_INDEX_URL" \
            --extra-index-url "https://pypi.org/simple/" \
            "$AEROLLM_PIP_SPEC"; then
        err "pip could not install ${AEROLLM_PIP_SPEC} from the index."
        warn "AeroLLM wheels are macOS-arm64-only; on other platforms there's no"
        warn "matching wheel yet (CUDA backend pending). The lab runs without the"
        warn "2nd inference until then."
        exit 1
    fi
    verify_or_die
    info "${GRN}AeroLLM ready${RST} (release wheel $(aerollm_version)) — the 2nd inference."
}

verify_or_die() {
    if ! import_ok; then
        err "Installed the extension but \`import aerollm_api\` still fails."
        warn "Check that $PY is the same interpreter ARAIL runs (venv mismatch?)."
        exit 1
    fi
}

# ── BUNDLED channel ───────────────────────────────────────────────────────
# Fetches a prebuilt, checksummed aerollm_api.abi3.so from an ARAIL GitHub
# Release asset (or a local tarball via AEROLLM_BUNDLE_FILE — the offline
# path) and installs it directly into platlib. No sibling source repo, no
# pip index credentials. See ARCHITECTURE.md §4.1 for the full contract.
resolve_bundle_url() {
    if [[ -n "$AEROLLM_BUNDLE_URL" ]]; then
        printf '%s\n' "$AEROLLM_BUNDLE_URL"
        return
    fi
    printf 'https://github.com/%s/releases/download/%s/aerollm-api-%s-macos-arm64.tar.gz\n' \
        "$AEROLLM_BUNDLE_REPO" "$AEROLLM_BUNDLE_TAG" "$AEROLLM_BUNDLE_TAG"
}

bundle_install() {
    # F4: platform guard, before any network call.
    if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
        err "The bundled AeroLLM channel is macOS-arm64-only."
        warn "The lab runs without the 2nd inference on this platform."
        exit 1
    fi

    local dest_dir dest_so dest_marker
    dest_dir="$(site_dir)"
    require_writable_site_dir "$dest_dir"
    dest_so="$dest_dir/aerollm_api.abi3.so"
    dest_marker="$dest_dir/aerollm_api.bundle.json"

    # F7: never shadow a maintainer's DEV/RELEASE install that got there by
    # some other channel — only a channel that left the provenance marker
    # (i.e. a previous bundled install) is safe to silently overwrite.
    if [[ -f "$dest_so" && ! -f "$dest_marker" && "$FORCE" != "1" ]]; then
        err "aerollm_api.abi3.so is already installed at ${BOLD}${dest_so}${RST} without a"
        err "bundle provenance marker — looks like a DEV or RELEASE install owns it."
        warn "Refusing to overwrite. Re-run with --force to install the bundled channel anyway."
        exit 1
    fi

    # Idempotence: same release already installed via this channel → no-op.
    if [[ -f "$dest_marker" && "$FORCE" != "1" ]]; then
        local installed_tag
        installed_tag="$("$PY" -c "import json; print(json.load(open('$dest_marker')).get('arail_release','?'))" 2>/dev/null || echo '?')"
        if [[ "$installed_tag" == "$AEROLLM_BUNDLE_TAG" ]] && import_ok; then
            info "Bundled AeroLLM ${AEROLLM_BUNDLE_TAG} already installed (use --force to reinstall)."
            return 0
        fi
    fi

    # Not `local` — an EXIT trap set inside this function still fires after
    # the function returns (bash's trap table is global), so the cleanup
    # target must still be a live variable at actual script-exit time.
    BUNDLE_TMP="$(mktemp -d "${TMPDIR:-/tmp}/arail-aerollm-bundle.XXXXXX")"
    tmp="$BUNDLE_TMP"
    trap 'rm -rf "$BUNDLE_TMP"' EXIT

    local tarball sha_expected
    if [[ -n "$AEROLLM_BUNDLE_FILE" ]]; then
        info "Using local bundle tarball: ${BOLD}${AEROLLM_BUNDLE_FILE}${RST} (offline path)."
        if [[ ! -f "$AEROLLM_BUNDLE_FILE" ]]; then
            err "AEROLLM_BUNDLE_FILE=${AEROLLM_BUNDLE_FILE} does not exist."
            exit 1
        fi
        tarball="$tmp/bundle.tar.gz"
        cp -f -- "$AEROLLM_BUNDLE_FILE" "$tarball"
        sha_expected="$AEROLLM_BUNDLE_SHA256"
        if [[ -z "$sha_expected" && -f "${AEROLLM_BUNDLE_FILE}.sha256" ]]; then
            sha_expected="$(awk '{print $1}' "${AEROLLM_BUNDLE_FILE}.sha256")"
        fi
    else
        local url; url="$(resolve_bundle_url)"
        # Security: reject any non-https scheme before curl ever runs.
        if [[ "$url" != https://* ]]; then
            err "Refusing a non-https bundle URL: ${url}"
            exit 1
        fi
        info "Downloading AeroLLM bundle from ${BOLD}${url}${RST}…"
        tarball="$tmp/bundle.tar.gz"
        if ! curl -fsSL --retry 2 -o "$tarball" -- "$url"; then
            err "Could not download the bundle asset: ${url}"
            warn "Either this ARAIL release has no bundled AeroLLM (run: ./arailctl deep status),"
            warn "or you're offline — set AEROLLM_BUNDLE_FILE to a local tarball instead."
            exit 1
        fi
        sha_expected="$AEROLLM_BUNDLE_SHA256"
        if [[ -z "$sha_expected" ]]; then
            local sha_url="${url}.sha256"
            if curl -fsSL -o "$tmp/bundle.tar.gz.sha256" -- "$sha_url" 2>/dev/null; then
                sha_expected="$(awk '{print $1}' "$tmp/bundle.tar.gz.sha256")"
            fi
        fi
    fi

    # F2: verify BEFORE any copy/install — never install unverified bytes.
    if [[ -z "$sha_expected" ]]; then
        err "No sha256 digest available (no .sha256 sidecar, no AEROLLM_BUNDLE_SHA256)."
        warn "Refusing to install an unverified artifact."
        exit 1
    fi
    local sha_actual
    if command -v shasum >/dev/null 2>&1; then
        sha_actual="$(shasum -a 256 "$tarball" | awk '{print $1}')"
    else
        sha_actual="$("$PY" -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$tarball")"
    fi
    if [[ "$sha_actual" != "$sha_expected" ]]; then
        err "Checksum mismatch — refusing to install."
        warn "  expected: ${sha_expected}"
        warn "  actual:   ${sha_actual}"
        warn "Retry, or set AEROLLM_BUNDLE_FILE to a known-good tarball."
        exit 1
    fi
    info "Checksum verified (sha256 ${sha_actual:0:12}…)."

    # Extract into a fresh dir; copy out only the four expected filenames so
    # a tarball with unexpected/`../` entries can't escape.
    local extract_dir="$tmp/extract"
    mkdir -p "$extract_dir"
    if ! tar xzf "$tarball" -C "$extract_dir"; then
        err "Could not extract the bundle tarball (see tar output above)."
        warn "Likely a truncated/corrupt download or a disk-full mid-extract."
        warn "Retry, or set AEROLLM_BUNDLE_FILE to a known-good tarball."
        exit 1
    fi
    for f in aerollm_api.abi3.so MANIFEST.json LICENSE NOTICE; do
        if [[ ! -f "$extract_dir/$f" ]]; then
            err "Bundle tarball is missing expected member: ${f}"
            exit 1
        fi
    done

    # Q1/Q5: verify the extracted .so against MANIFEST.json's own sha256
    # (travels inside the sha256-verified tarball, so this catches a
    # tarball-vs-member substitution the tarball-level checksum alone can't
    # — the two objects are digested independently). Fail closed if the
    # manifest doesn't carry one; a bundle producer must always emit it.
    local so_sha_expected so_sha_actual
    so_sha_expected="$("$PY" -c "import json; print(json.load(open('$extract_dir/MANIFEST.json')).get('sha256',''))" 2>/dev/null || echo '')"
    if command -v shasum >/dev/null 2>&1; then
        so_sha_actual="$(shasum -a 256 "$extract_dir/aerollm_api.abi3.so" | awk '{print $1}')"
    else
        so_sha_actual="$("$PY" -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$extract_dir/aerollm_api.abi3.so")"
    fi
    if [[ -z "$so_sha_expected" || "$so_sha_actual" != "$so_sha_expected" ]]; then
        err "aerollm_api.abi3.so does not match MANIFEST.json's recorded sha256 — refusing to install."
        warn "  manifest:  ${so_sha_expected:-<missing>}"
        warn "  extracted: ${so_sha_actual}"
        exit 1
    fi

    # Q5: cheap format sanity check before copying into site-packages —
    # this is NOT an authenticity or codesign check (the binary is
    # unsigned; see docs/cli.md). It only catches a payload that isn't
    # even the right kind of file. Mach-O 64-bit magic, either byte order
    # (MH_MAGIC_64 / MH_CIGAM_64) — no `file`/`lipo` dependency required.
    local magic
    magic="$(od -An -tx1 -N4 "$extract_dir/aerollm_api.abi3.so" | tr -d ' \n')"
    case "$magic" in
        cffaedfe|feedfacf) : ;;  # Mach-O 64-bit, little/big-endian
        *)
            err "aerollm_api.abi3.so is not a Mach-O arm64 binary (got magic bytes: ${magic}) — refusing to install."
            exit 1
            ;;
    esac

    # F5: quarantine xattr (browser-downloaded tarball) — best-effort strip
    # before verify, since a quarantined .so fails dlopen under Gatekeeper.
    xattr -d com.apple.quarantine "$extract_dir/aerollm_api.abi3.so" >/dev/null 2>&1 || true

    info "Installing → ${dest_so}"
    warn "aerollm_api.abi3.so is prebuilt native code, downloaded and executed on"
    warn "import. It is integrity-checked against the release manifest (same-origin"
    warn "trust), NOT signature-verified or sandboxed — see docs/cli.md."
    cp -f "$extract_dir/aerollm_api.abi3.so" "$dest_so"
    cp -f "$extract_dir/MANIFEST.json" "$dest_marker"

    if ! import_ok; then
        # F1: never leave a broken artifact shadowing a future good install.
        rm -f "$dest_so" "$dest_marker"
        err "Installed the bundled extension but \`import aerollm_api\` failed."
        warn "Removed the broken artifact. Run ./arailctl deep status for details."
        warn "If this was a browser download, quarantine may be the cause (F5) — retry via setup."
        exit 1
    fi
    local ver; ver="$("$PY" -c "import json; print(json.load(open('$dest_marker')).get('aerollm_version','unknown'))" 2>/dev/null || echo unknown)"
    info "${GRN}AeroLLM ready${RST} (bundled ${ver}) — the deep-mode 2nd inference."
}

# ── channel detection (for status + auto's rule 2) ──────────────────────────
_release_creds_configured() {
    [[ -n "${PIP_INDEX_URL:-}" || -n "${PIP_EXTRA_INDEX_URL:-}" ]] && return 0
    [[ "$AEROLLM_INDEX_URL" != "https://pypi.qukaizen.com/simple/" ]] && return 0
    [[ -f "$HOME/.netrc" ]] && grep -q "pypi.qukaizen.com" "$HOME/.netrc" 2>/dev/null && return 0
    return 1
}

bundle_marker_path() { printf '%s\n' "$(site_dir 2>/dev/null)/aerollm_api.bundle.json"; }

installed_channel() {
    if ! import_ok; then
        printf 'none\n'
        return
    fi
    local marker; marker="$(bundle_marker_path)"
    if [[ -f "$marker" ]]; then
        printf 'bundled\n'
    elif [[ -d "$CRATE_DIR" ]]; then
        # Ambiguous without a marker; a sibling repo being present is the
        # closest signal we have that this was a source (dev) build.
        printf 'dev\n'
    else
        printf 'release\n'
    fi
}

case "$MODE" in
    status)
        info "AeroLLM (2nd inference) status"
        printf '    repo:         %s\n' "$AEROLLM_REPO"
        if [[ -d "$CRATE_DIR" ]]; then
            printf '    crate:        %s (found → source/dev channel)\n' "$CRATE_DIR"
        else
            printf '    crate:        %s %s(missing → release/bundled channel)%s\n' "$CRATE_DIR" "$YLW" "$RST"
        fi
        printf '    index:        %s\n' "$AEROLLM_INDEX_URL"
        printf '    site-packages:%s\n' " $(site_dir 2>/dev/null || echo '?')"
        if import_ok; then
            printf '    aerollm_api:  %simportable ✓%s (version %s)\n' "$GRN" "$RST" "$(aerollm_version)"
        else
            printf '    aerollm_api:  %snot installed — run: ./arailctl deep install (or: deep rebuild / deep update)%s\n' "$YLW" "$RST"
        fi
        printf '    channel:      %s\n' "$(installed_channel)"
        marker="$(bundle_marker_path)"
        if [[ -f "$marker" ]]; then
            "$PY" -c "
import json
try:
    m = json.load(open('$marker'))
    ver = m.get('aerollm_version', 'unknown')
    sha = m.get('aerollm_commit', '?')[:7]
    built = m.get('built_at', 'unknown')
    print(f'    bundle:       aerollm {ver} ({sha}, built {built})')
except Exception:
    print('    bundle:       channel: unknown (installed, provenance not recorded)')
" 2>/dev/null || printf '    bundle:       %sunknown (installed, provenance not recorded)%s\n' "$YLW" "$RST"
        fi
        printf '    bg pressure:  %s\n' "${ARAIL_AEROLLM_BG_PRESSURE_PCT:-0.60 (default)}"
        exit 0
        ;;
    build)
        cargo_build
        ;;
    update)
        pip_install --upgrade
        ;;
    bundle)
        bundle_install
        ;;
    auto)
        case "${AEROLLM_CHANNEL:-}" in
            dev)     cargo_build ;;
            release) pip_install ;;
            bundle)  bundle_install ;;
            "")
                if [[ -d "$CRATE_DIR" ]]; then
                    info "Local sibling repo found → building from source (dev channel)."
                    cargo_build
                elif _release_creds_configured; then
                    info "Release-index credentials configured → installing the published wheel (release channel)."
                    pip_install
                else
                    info "No sibling repo, no release credentials → installing the bundled binary (bundled channel)."
                    bundle_install
                fi
                ;;
            *)
                err "AEROLLM_CHANNEL must be one of: dev | release | bundle"
                exit 2
                ;;
        esac
        ;;
    *)
        err "mode must be one of: auto | build | update | bundle | status"
        exit 2
        ;;
esac
