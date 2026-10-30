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
#   auto    (default)  a pre-rename sibling checkout → cargo build; a renamed or
#                       unrecognised checkout → refuse (it never switches channel on
#                       its own); no checkout → AEROLLM_CHANNEL=release or index creds
#                       configured → pip; else → bundle
#   build              force a local cargo build from $ARAIL_AEROLLM_REPO
#   update             pip install --upgrade from the index (release channel)
#   bundle             force the BUNDLED channel (see bundle_install() below)
#   status             report what import aerollm_api loads, where it came from (only
#                       when that can be proven) and what the last deep probe found
#
# AEROLLM_CHANNEL=dev|release|bundle forces a channel from any mode.
#
# This script never says QueueLLM is "ready". An import is not a generation test;
# every install ends with "Generation is not tested yet: run ./arailctl deep probe"
# (sprints/2026-10-29-arail-deep-honest). Every install is verified by the sha256 of
# the file `import aerollm_api` actually loads, and a copy the import would never
# load (a package directory or a PYTHONPATH entry wins) is refused before it is made.
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
CRATE_DIR="$AEROLLM_REPO/crates/aerollm-api"          # the pre-rename crate: the only one this script builds
NEW_CRATE_DIR="$AEROLLM_REPO/crates/queuellm-api"     # the renamed crate: detected, refused, never built
PY="${PYTHON:-python3}"
SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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
    err "No QueueLLM checkout at ${BOLD}${AEROLLM_REPO}${RST}."
    warn "For a source (dev) build, either:"
    warn "  1) clone it next to arail:"
    warn "       git clone https://github.com/cdarnell/qukaizen-queuellm \"${AEROLLM_REPO}\""
    warn "  2) or point ARAIL_AEROLLM_REPO at your checkout."
    warn "Or install the published wheel instead: ./arailctl deep update"
}

# arail.deep_verdict is stdlib only; PYTHONPATH makes it importable under any $PY.
verdict_py() {  # $* = arguments for `python -m arail.deep_verdict`
    PYTHONPATH="$SCRIPT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" "$PY" -m arail.deep_verdict "$@"
}

# What is at $ARAIL_AEROLLM_REPO: no-repo | old (builds here) | new (renamed crate) | none
repo_state() {
    if [[ ! -d "$AEROLLM_REPO" ]]; then printf 'no-repo\n'
    elif [[ -f "$CRATE_DIR/Cargo.toml" ]]; then printf 'old\n'
    elif [[ -f "$NEW_CRATE_DIR/Cargo.toml" ]]; then printf 'new\n'
    else printf 'none\n'
    fi
}

# The true reason a checkout cannot be built here. Prints nothing and builds nothing.
refuse_repo() {  # $1 = state (no-repo | new | none)
    case "$1" in
        no-repo) clone_hint ;;
        new)
            err "${AEROLLM_REPO} has the renamed crate crates/queuellm-api (module queuellm_api). This ARAIL can build only the pre-rename crate crates/aerollm-api, so deep rebuild stops here. Nothing was built or installed."
            warn "For the prebuilt QueueLLM: ./arailctl deep install."
            ;;
        none)
            err "${AEROLLM_REPO} has neither crates/aerollm-api nor crates/queuellm-api, so it is not a QueueLLM checkout this ARAIL can build. Nothing was built or installed."
            ;;
    esac
}

sha256_of() {  # $1 = file
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        "$PY" -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$1"
    fi
}

# Refuse before any copy if `import aerollm_api` would not load a file at $1.
check_import_target() {  # $1 = destination <site>/aerollm_api.abi3.so
    local verdict fields free winner owner
    verdict="$(verdict_py target "$1" 2>/dev/null)" || verdict=""
    if [[ -z "$verdict" ]]; then
        err "Could not tell what \`import aerollm_api\` loads in ${PY}. Nothing was installed."
        exit 1
    fi
    # one JSON object in, three tab-separated fields out
    fields="$("$PY" -c 'import json,sys; d=json.loads(sys.argv[1]); print("\t".join([str(d.get("free")), str(d.get("winner")), str(d.get("owner"))]))' "$verdict")"
    IFS=$'\t' read -r free winner owner <<<"$fields"
    if [[ "$free" != "True" ]]; then
        err "import aerollm_api in ${PY} loads ${winner} (${owner}). A copy at ${1} would never load. Nothing was installed."
        warn "To use this build, remove that first, for example: ${PY} -m pip uninstall aerollm-api (this changes your venv)."
        exit 1
    fi
}

# After an install: the file `import aerollm_api` loads must be the file we put there.
# $1 = dest, $2 = expected sha256, $3 = backup of the previous file (may be empty/missing),
# $4 = the install-record marker we wrote (may be empty), $5 = backup of the previous marker.
# On a mismatch the previous state is put back and the temp backups are removed.
verify_loaded() {
    local ident loaded_sha loaded_path
    ident="$(verdict_py identity --json 2>/dev/null)" || ident=""
    [[ -n "$ident" ]] || ident='{}'
    loaded_sha="$("$PY" -c 'import json,sys; print(json.loads(sys.argv[1]).get("sha256",""))' "$ident" 2>/dev/null || true)"
    loaded_path="$("$PY" -c 'import json,sys; print(json.loads(sys.argv[1]).get("native_path",""))' "$ident" 2>/dev/null || true)"
    if [[ "$loaded_sha" != "$2" ]]; then
        rm -f -- "$1"
        if [[ -n "${3:-}" && -f "$3" ]]; then cp -f -- "$3" "$1"; fi
        if [[ -n "${4:-}" ]]; then
            rm -f -- "$4"
            if [[ -n "${5:-}" && -f "$5" ]]; then cp -f -- "$5" "$4"; fi
        fi
        if [[ -z "$loaded_sha" ]]; then
            err "Installed ${1}, but \`import aerollm_api\` fails in ${PY}. Removed the copy."
        else
            err "Installed ${1}, but import aerollm_api loads ${loaded_path} (sha256 ${loaded_sha:0:12}…). Removed the copy."
        fi
        [[ -n "${3:-}" && -f "$3" ]] && warn "The file that was there before was put back."
        rm -f -- "${3:-}" "${5:-}"
        exit 1
    fi
}

GENERATION_HINT="Generation is not tested yet: run ./arailctl deep probe."

cargo_build() {
    local state; state="$(repo_state)"
    if [[ "$state" != "old" ]]; then refuse_repo "$state"; exit 1; fi
    if ! command -v cargo >/dev/null 2>&1; then
        err "cargo not found — install the Rust toolchain (https://rustup.rs) and retry."
        exit 1
    fi
    local dest; dest="$(site_dir)"
    require_writable_site_dir "$dest"
    dest="$dest/aerollm_api.abi3.so"
    # Fail before the (minutes-long) build if a copy at dest could never be loaded.
    check_import_target "$dest"
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
    # QueueLLM's own preamble check: a build made while the Metal toolchain was down can exit 0
    # and still fail at runtime. The marker string must not be in the artifact.
    local poison; poison="$(LC_ALL=C grep -a -c downloadComponent "$built" || true)"
    if [[ "${poison:-0}" != "0" ]]; then
        err "The build carries the Metal toolchain error marker (downloadComponent). It would fail at runtime. Not installed. See QueueLLM docs/self-hosted-runner-setup.md."
        exit 1
    fi
    local sha; sha="$(sha256_of "$built")"
    local backup=""
    if [[ -f "$dest" ]]; then backup="$(mktemp "${TMPDIR:-/tmp}/arail-aerollm-prev.XXXXXX")"; cp -f -- "$dest" "$backup"; fi
    info "Installing → ${dest}"
    cp -f "$built" "$dest"
    verify_loaded "$dest" "$sha" "$backup"
    rm -f -- "$backup"
    write_build_marker "$(dirname "$dest")" "$sha" "$built"
    info "QueueLLM installed (source build, sha256 ${sha:0:12}…). ${GENERATION_HINT}"
}

# The same role as THIRD-PARTY-LICENSES/aerollm/BUNDLE.json: where this binary came from.
write_build_marker() {  # $1 = site dir, $2 = artifact sha256, $3 = artifact path
    local commit dirty
    commit="$(git -C "$AEROLLM_REPO" rev-parse HEAD 2>/dev/null || echo unknown)"
    if [[ -n "$(git -C "$AEROLLM_REPO" status --porcelain 2>/dev/null)" ]]; then dirty=true; else dirty=false; fi
    "$PY" - "$1/aerollm_api.build.json" "$commit" "$dirty" "$2" "$(wc -c < "$3" | tr -d ' ')" <<'PYMARK'
import json, sys, time
path, commit, dirty, sha, size = sys.argv[1:6]
json.dump({
    "schema": "arail.aerollm-build/v1", "queuellm_commit": commit, "queuellm_dirty": dirty == "true",
    "crate": "crates/aerollm-api", "artifact_sha256": sha, "artifact_bytes": int(size),
    "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "built_by": "scripts/build-aerollm.sh",
}, open(path, "w"), indent=2)
PYMARK
}

pip_install() {  # $* = extra pip args (e.g. --upgrade)
    info "Installing aerollm_api from index ${BOLD}${AEROLLM_INDEX_URL}${RST}…"
    if ! "$PY" -m pip install "$@" \
            --index-url "$AEROLLM_INDEX_URL" \
            --extra-index-url "https://pypi.org/simple/" \
            "$AEROLLM_PIP_SPEC"; then
        err "pip could not install ${AEROLLM_PIP_SPEC} from the index."
        warn "QueueLLM wheels are macOS-arm64-only; on other platforms there's no"
        warn "matching wheel yet (CUDA backend pending). The lab runs without the"
        warn "2nd inference until then."
        exit 1
    fi
    verify_or_die
    # The file that loads must be the one pip's RECORD lists, not a different copy that wins the import.
    local ch; ch="$(verdict_py channel 2>/dev/null || true)"
    case "$ch" in
        "pip ("*) : ;;
        *)
            err "pip installed ${AEROLLM_PIP_SPEC}, but import aerollm_api does not load the file pip installed: ${ch:-unknown}."
            warn "Nothing here is a QueueLLM you can rely on yet. See ./arailctl deep status."
            exit 1
            ;;
    esac
    local ident sha
    ident="$(verdict_py identity --json 2>/dev/null || echo '{}')"
    sha="$("$PY" -c 'import json,sys; print(json.loads(sys.argv[1]).get("sha256",""))' "$ident")"
    info "QueueLLM installed (release wheel $(aerollm_version), sha256 ${sha:0:12}…). ${GENERATION_HINT}"
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
        err "The bundled QueueLLM channel is macOS-arm64-only."
        warn "The lab runs without the 2nd inference on this platform."
        exit 1
    fi

    local dest_dir dest_so dest_marker
    dest_dir="$(site_dir)"
    require_writable_site_dir "$dest_dir"
    dest_so="$dest_dir/aerollm_api.abi3.so"
    dest_marker="$dest_dir/aerollm_api.bundle.json"

    # A copy that `import aerollm_api` would never load is not an install. This comes first and
    # --force does not skip it: a package directory or a PYTHONPATH entry already wins.
    check_import_target "$dest_so"

    # F7: never shadow a maintainer's DEV/RELEASE install that got there by
    # some other channel — only a channel that left the provenance marker
    # (i.e. a previous bundled install) is safe to silently overwrite.
    if [[ -f "$dest_so" && ! -f "$dest_marker" && "$FORCE" != "1" ]]; then
        err "an aerollm_api.abi3.so with no install record is already at ${BOLD}${dest_so}${RST}."
        warn "Refusing to overwrite it. Re-run with --force to install the bundled channel anyway."
        exit 1
    fi

    # Idempotence: same release already installed via this channel → no-op.
    if [[ -f "$dest_marker" && "$FORCE" != "1" ]]; then
        local installed_tag
        installed_tag="$("$PY" -c "import json; print(json.load(open('$dest_marker')).get('arail_release','?'))" 2>/dev/null || echo '?')"
        if [[ "$installed_tag" == "$AEROLLM_BUNDLE_TAG" ]] && import_ok; then
            info "Bundled QueueLLM ${AEROLLM_BUNDLE_TAG} already installed (use --force to reinstall)."
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
        info "Downloading QueueLLM bundle from ${BOLD}${url}${RST}…"
        tarball="$tmp/bundle.tar.gz"
        if ! curl -fsSL --retry 2 -o "$tarball" -- "$url"; then
            err "Could not download the bundle asset: ${url}"
            warn "Either this ARAIL release has no bundled QueueLLM (run: ./arailctl deep status),"
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
    local backup="" mbackup=""
    if [[ -f "$dest_so" ]]; then backup="$(mktemp "${TMPDIR:-/tmp}/arail-aerollm-prev.XXXXXX")"; cp -f -- "$dest_so" "$backup"; fi
    if [[ -f "$dest_marker" ]]; then mbackup="$(mktemp "${TMPDIR:-/tmp}/arail-aerollm-prevm.XXXXXX")"; cp -f -- "$dest_marker" "$mbackup"; fi
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
    # The file that loads must be the file we verified against MANIFEST.json.
    verify_loaded "$dest_so" "$so_sha_actual" "$backup" "$dest_marker" "$mbackup"
    rm -f -- "$backup" "$mbackup"
    local ver; ver="$("$PY" -c "import json; print(json.load(open('$dest_marker')).get('aerollm_version','unknown'))" 2>/dev/null || echo unknown)"
    info "QueueLLM installed (bundled ${ver}, sha256 ${so_sha_actual:0:12}…). ${GENERATION_HINT}"
}

# ── channel detection (for auto's rule 2) ──────────────────────────────────
_release_creds_configured() {
    [[ -n "${PIP_INDEX_URL:-}" || -n "${PIP_EXTRA_INDEX_URL:-}" ]] && return 0
    [[ "$AEROLLM_INDEX_URL" != "https://pypi.qukaizen.com/simple/" ]] && return 0
    [[ -f "$HOME/.netrc" ]] && grep -q "pypi.qukaizen.com" "$HOME/.netrc" 2>/dev/null && return 0
    return 1
}

# The refusal an `auto` run prints for a checkout it cannot build: no silent switch of channel.
auto_refuse() {  # $1 = state (new | none)
    refuse_repo "$1"
    warn "Not switching to another channel on my own. Prebuilt instead: ./arailctl deep install (or AEROLLM_CHANNEL=bundle)."
    exit 1
}

case "$MODE" in
    status)
        state="$(repo_state)"
        info "QueueLLM (2nd inference) status"
        printf '    repo:         %s\n' "$AEROLLM_REPO"
        case "$state" in
            old)     printf '    crate:        crates/aerollm-api, the pre-rename crate; deep rebuild can build it\n' ;;
            new)     printf '    crate:        crates/queuellm-api, the renamed crate; deep rebuild cannot build it yet\n' ;;
            none)    printf '    crate:        neither crates/aerollm-api nor crates/queuellm-api; deep rebuild cannot build this checkout\n' ;;
            no-repo) printf '    crate:        no checkout at that path\n' ;;
        esac
        printf '    index:        %s\n' "$AEROLLM_INDEX_URL"
        printf '    python:       %s\n' "$PY"
        verdict_py status || printf '    import:       could not run arail.deep_verdict under %s\n' "$PY"
        pressure="${ARAIL_AEROLLM_BG_PRESSURE_PCT:-0.60 (default)}"
        printf '    bg pressure:  %s\n' "$pressure"
        # the number is taught: what it is, its bar and why, what out of range means, where the fault lives
        printf '                  %s: the Metal memory-pressure ceiling above which background deep work waits. Bar: below it background deep may run; ARAIL_AEROLLM_BG_PRESSURE_PCT changes it (clamped 0.30 to 0.75, because the hard chat guard sits at 0.75). Out of range: above it agents use the fast model (reason deferred_now). Fault lives in: memory pressure on this Mac, not QueueLLM.\n' "$pressure"
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
                state="$(repo_state)"
                case "$state" in
                    old)
                        info "Local sibling repo found → building from source (dev channel)."
                        cargo_build
                        ;;
                    new|none)
                        auto_refuse "$state"
                        ;;
                    *)
                        if _release_creds_configured; then
                            info "Release-index credentials configured → installing the published wheel (release channel)."
                            pip_install
                        else
                            info "No QueueLLM checkout, no release credentials → installing the bundled binary (bundled channel)."
                            bundle_install
                        fi
                        ;;
                esac
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
