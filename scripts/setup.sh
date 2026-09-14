#!/usr/bin/env bash
# =============================================================================
# Arail — Setup Script
# Detects your platform, installs dependencies, downloads a starter model.
# =============================================================================
set -euo pipefail

# shellcheck disable=SC2024
# Every `sudo <cmd> >>"$log"` below deliberately runs the REDIRECT as the
# invoking (non-root) user — only <cmd> itself is elevated. That is the
# correct behavior, not a bug: setup.log must stay user-owned so later
# user-mode appends (this script re-run, `./arailctl doctor`, etc.) can
# still write it. The "obvious" shellcheck fix (`sudo sh -c '... >>log'`)
# would create a ROOT-owned log that a later non-sudo run can no longer
# append to (ARCHITECTURE.md sprints/2026-07-29-elite-cli §13, F23) — do
# not "fix" this warning by restructuring the redirects.

# ── ANSI color gating (ARCHITECTURE.md §13 "ANSI leaks into non-tty
# output", F25) — see arailctl's identical block for the full rationale.
# $'...' (ANSI-C quoting), not "...": several of this file's help
# banners below (capture_tier/capture_mode) interpolate ${BOLD}/${RESET}
# inside a plain `cat <<EOF` heredoc, which does no escape processing at
# all — a double-quoted "\033[1m" would render as the literal 4
# characters backslash/0/3/3 there, not a color code. This was a latent
# bug in those heredocs before this sprint; ANSI-C quoting fixes it at
# the source instead of routing every call site through `printf`.
if [[ -t 1 && "${ARAIL_COLOR:-auto}" != "never" && -z "${NO_COLOR:-}" ]] || [[ "${ARAIL_COLOR:-auto}" == "always" ]]; then
    BOLD=$'\033[1m'
    GREEN=$'\033[0;32m'
    YELLOW=$'\033[0;33m'
    RED=$'\033[0;31m'
    RESET=$'\033[0m'
else
    BOLD=""
    GREEN=""
    YELLOW=""
    RED=""
    RESET=""
fi

info()  { echo -e "${GREEN}[arail]${RESET} $*"; }
warn()  { echo -e "${YELLOW}[arail]${RESET} $*"; }
error() { echo -e "${RED}[arail]${RESET} $*"; exit 1; }

# ARCHITECTURE.md §10 finding 2 (PR #181 CI, sprints/2026-08-10-arail2-
# persistence-instantiated): resolve_data_dirs() lives in
# scripts/lib/instances.sh, needed below by setup_db_ensure(). It reads
# $REPO_ROOT directly (no CWD fallback of its own), so pin it as a real
# global here rather than only ever using the ${REPO_ROOT:-$PWD} inline
# fallback the rest of this file uses. Sourced the same way install.sh
# does — a no-op if the file is somehow missing rather than a hard
# failure, since this whole step is itself required to never fail setup.
REPO_ROOT="${REPO_ROOT:-$PWD}"
# shellcheck disable=SC1091
[[ -f "$REPO_ROOT/scripts/lib/instances.sh" ]] && source "$REPO_ROOT/scripts/lib/instances.sh"

# Self-sufficient install policy:
#   ARAIL_NONINTERACTIVE=1  → never prompt; default-yes everything (agent-driven)
#   ARAIL_AUTO_INSTALL=0    → never auto-install; preserve old "tell user to install" behavior
#   default                 → prompt once, default-yes, install on Enter
confirm() {
    local prompt="$1" default="${2:-y}"
    if [[ "${ARAIL_NONINTERACTIVE:-0}" == "1" || ! -t 0 ]]; then
        [[ "$default" == "y" ]]
        return
    fi
    local hint="[Y/n]"
    [[ "$default" == "n" ]] && hint="[y/N]"
    local answer
    read -r -p "  $prompt $hint " answer || answer=""
    answer="${answer:-$default}"
    [[ "$answer" =~ ^[Yy] ]]
}

auto_install_enabled() {
    [[ "${ARAIL_AUTO_INSTALL:-1}" == "1" ]]
}

# PYTHON_BIN — resolved by ensure_python() to the absolute path of the
# Python interpreter we'll use to create the venv. Anything that runs
# python BEFORE the venv is activated must use "$PYTHON_BIN", not bare
# python3 (which may still be the system's outdated 3.9 even after we
# brew-install python@3.11).
PYTHON_BIN=""

ollama_default_enabled() {
    # Gate on TIER, not on ACCEL. The minimalist default model (llama-ai-eng)
    # is an Ollama persona-wrap (llama3.2:1b); it REQUIRES Ollama regardless of
    # accelerator. The "Apple Silicon prefers MLX" behavior applies only to the
    # maximus deep path (AeroLLM/MLX). Do NOT revert this to ACCEL-gating — that
    # re-introduces F1: minimalist setup on M-series skips Ollama, leaving
    # llama-ai-eng uninstalled. (Two-tier sprint 2026-06-14.)
    if [[ "${LAB_TIER:-minimalist}" == "minimalist" ]]; then
        # Minimalist always needs Ollama — llama-ai-eng is an Ollama model.
        return 0
    fi
    # Maximus tier: on Apple Silicon + MLX, AeroLLM is the deep runtime;
    # Ollama is optional (enabled by ARAIL_ENABLE_OLLAMA=1).
    [[ "$PLATFORM" == "macos" && "$ACCEL" == "mlx" ]] && return 1
    return 0
}

# Numbered checkpoint banner — every major section prints one so the
# user has a visible progress spine ("━━━ 3/10  Python environment").
step()  { echo ""; echo -e "${BOLD}━━━ $*${RESET}"; echo ""; }

MODEL_MLX_ID="mlx-community/Qwen3-8B-4bit"
MODEL_HF_ID="Qwen/Qwen3-8B"
MODEL_GGUF_ID="Qwen/Qwen3-8B-GGUF"
# Coder starter model — downloaded with --with-coder flag (Sprint 2).
# Qwen2.5-Coder-3B is ~2 GB Q4; fits on any tier machine. Set
# ARAIL_WITH_CODER=1 to download automatically without the flag.
CODER_MLX_ID="mlx-community/Qwen2.5-Coder-3B-Instruct-4bit"
CODER_HF_ID="Qwen/Qwen2.5-Coder-3B-Instruct"
CODER_GGUF_ID="Qwen/Qwen2.5-Coder-3B-Instruct-GGUF"
WITH_CODER="${ARAIL_WITH_CODER:-0}"
# Deep backend — AirLLM is opt-in (ARAIL_INSTALL_AIRLLM=1). The 70B/405B
# Llama defaults are deprecated: they are the wrong weight class for the
# 36 GB Apple Silicon target (OOM or crawl). AIRLLM_MODEL_ID is set to the
# sentinel; operators who want AirLLM set AIRLLM_MODEL in .env explicitly.
#
# TODO(deep-model): set the 20–30B open deep model id here. See ARCHITECTURE
#   sprint 2026-05-30-model-hosting-reframe § Part 1. Until set, deep mode
#   shows a "configure your deep model" notice — it does NOT download anything.
AIRLLM_MODEL_ID="__TODO_DEEP_MODEL__"
AIRLLM_MODEL_MIN_ID="__TODO_DEEP_MODEL__"
AIRLLM_MODEL_MAX_ID="__TODO_DEEP_MODEL__"
AIRLLM_PACKAGE_SPEC="airllm>=2.0"
# AeroLLM = Arail's own Rust runtime, the deep-mode default on Apple
# Silicon as of 0.1.0 alpha. minimalist ships Qwen2.5-7B-Instruct-4bit
# (~4 GB resident, fits 16 GB Macs); maximus ships the SAME 7B by default
# so a fresh clone "just works" — operators upgrade after setup by
# downloading frontier weights and setting AEROLLM_MODEL in .env (see
# pyproject.toml [tool.arail.models] block for the recipe). Pre-7180a8a
# the maximus default was 72B (~40 GB, needs 48 GB+); that footgunned
# every clone without prebuilt weights, which is what this default fixes.
# CUDA hosts still default to AirLLM until the aerollm CUDA backend lands.
# KV budget defaults to 60% of system RAM so portal + browser keep headroom.
AEROLLM_MODEL_ID="mlx-community/Qwen2.5-7B-Instruct-4bit"
AEROLLM_MODEL_MIN_ID="mlx-community/Qwen2.5-7B-Instruct-4bit"
AEROLLM_MODEL_MAX_ID="mlx-community/Qwen2.5-7B-Instruct-4bit"
AEROLLM_KV_BUDGET_PCT="0.60"
# Release channel for the aerollm_api wheel — used when no local sibling repo is
# present. Overridden by [tool.arail.package-sources] in pyproject.toml.
AEROLLM_PIP_SPEC="aerollm-api>=0.1,<0.2"
AEROLLM_INDEX_URL="https://pypi.qukaizen.com/simple/"
# Bundled channel — the GitHub Release tag whose assets carry the prebuilt
# aerollm_api.abi3.so. Overridden by [tool.arail.package-sources]
# aerollm_bundle_tag in pyproject.toml (see load_pyproject_metadata below).
AEROLLM_BUNDLE_TAG="v1.1.0"
# sha256 of the bundle TARBALL (not the .so) — the out-of-band pin
# build-aerollm.sh compares AEROLLM_BUNDLE_SHA256 against. Overridden by
# [tool.arail.package-sources] aerollm_bundle_sha256 in pyproject.toml.
AEROLLM_BUNDLE_SHA256=""

# Unified password — set by capture_password() below. One secret covers:
#   - code-server (IDE) login
#   - Open Notebook data encryption key
#   - future auth proxy
ARAIL_PASSWORD=""

load_pyproject_metadata() {
    local assignments
    assignments="$(python3 - <<'PY'
from pathlib import Path
import shlex

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

data = tomllib.loads(Path("pyproject.toml").read_text())
tool = data.get("tool", {}).get("arail", {})
models = tool.get("models", {})
sources = tool.get("package-sources", {})
values = {
    "MODEL_MLX_ID": str(models.get("mlx", "")),
    "MODEL_HF_ID": str(models.get("cuda", "")),
    "MODEL_GGUF_ID": str(models.get("cpu", "")),
    "AIRLLM_MODEL_MIN_ID": str(models.get("airllm_minimalist", models.get("airllm_min", models.get("airllm", "")))),
    "AIRLLM_MODEL_MAX_ID": str(models.get("airllm_maximus", models.get("airllm_max", models.get("airllm", "")))),
    "AIRLLM_PACKAGE_SPEC": str(sources.get("airllm", "")),
    "AEROLLM_MODEL_ID": str(models.get("aerollm", "")),
    "AEROLLM_MODEL_MIN_ID": str(models.get("aerollm_minimalist", models.get("aerollm_min", models.get("aerollm", "")))),
    "AEROLLM_MODEL_MAX_ID": str(models.get("aerollm_maximus", models.get("aerollm_max", models.get("aerollm", "")))),
    "AEROLLM_PIP_SPEC": str(sources.get("aerollm", "")),
    "AEROLLM_INDEX_URL": str(sources.get("aerollm_index", "")),
    "AEROLLM_BUNDLE_TAG": str(sources.get("aerollm_bundle_tag", "")),
    "AEROLLM_BUNDLE_SHA256": str(sources.get("aerollm_bundle_sha256", "")),
}
for key, value in values.items():
    if value:
        print(f"{key}={shlex.quote(value)}")
PY
)" || error "Could not read pyproject.toml metadata. Make sure Python can import tomllib or tomli."
    eval "$assignments"
    info "Loaded package and model metadata from ${BOLD}pyproject.toml${RESET}."
}

install_pyproject_extra() {
    local extra_name="$1"
    local label="$2"
    local log="${REPO_ROOT:-$PWD}/setup.log"
    info "Installing ${label} from pyproject extra '${extra_name}'…"
    pip install -q -e ".[${extra_name}]" 2>>"$log" || {
        warn "Install failed for pyproject extra '${extra_name}'. Last 20 lines of setup.log:"
        tail -n 20 "$log" | sed 's/^/    /' >&2
        error "Install failed for '${extra_name}'. See setup.log, then re-run: ./arailctl setup"
    }
}

# -----------------------------------------------------------------------------
# Detect OS / platform
# -----------------------------------------------------------------------------
detect_platform() {
    step "1/11  Detecting hardware"

    # Guard: PowerShell / Git-Bash / MSYS shells running on Windows itself.
    # These aren't supported — users must install WSL2 Ubuntu and run from
    # there. Detect via environment variables those shells set.
    if [[ -n "${MSYSTEM:-}" ]] || [[ -n "${WT_SESSION:-}" && "$(uname -s)" == MINGW* ]]; then
        error "Windows native shell detected. Install WSL2 + Ubuntu (wsl --install in PowerShell), then run ./arailctl setup from inside the Ubuntu app."
    fi

    local os kernel
    os="$(uname -s)"
    kernel="$(uname -r)"

    case "$os" in
        Darwin)
            PLATFORM="macos"
            # Check for Apple Silicon
            if [[ "$(uname -m)" == "arm64" ]]; then
                ACCEL="mlx"
            else
                ACCEL="cpu"
                warn "Intel Mac detected — using CPU backend (slower inference). MLX requires Apple Silicon."
            fi
            ;;
        Linux)
            # Check if running inside WSL
            if grep -qiE '(microsoft|wsl)' /proc/version 2>/dev/null; then
                PLATFORM="wsl"
                # WSL1 is unsupported — the kernel lacks /dev/dxg and
                # several syscalls pip + torch need.
                if ! grep -qi 'WSL2' /proc/version 2>/dev/null; then
                    error "WSL1 detected — Arail requires WSL2. From PowerShell (admin):  wsl --set-version Ubuntu 2"
                fi
            # Check for Gentoo
            elif [[ -f /etc/gentoo-release ]]; then
                PLATFORM="gentoo"
            elif [[ -f /etc/fedora-release ]]; then
                PLATFORM="fedora"
            elif [[ -f /etc/arch-release ]]; then
                PLATFORM="arch"
            else
                PLATFORM="linux"
            fi

            # GPU detection
            if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
                ACCEL="cuda"
                # WSL-specific: flag if the user installed the Linux
                # Nvidia driver inside WSL instead of relying on the
                # Windows-side driver.
                if [[ "$PLATFORM" == "wsl" ]] && [[ ! -f /usr/lib/wsl/lib/libcuda.so && ! -f /usr/lib/wsl/lib/libcuda.so.1 ]]; then
                    warn "CUDA detected but /usr/lib/wsl/lib/libcuda.so* is missing."
                    warn "If you installed 'nvidia-drivers' inside WSL, remove them —"
                    warn "WSL gets CUDA from the Windows-side driver only."
                fi
            else
                ACCEL="cpu"
            fi
            ;;
        *)
            error "Unsupported OS: $os. ARAIL is a blueprint — point an AI coding agent at scripts/setup.sh + AGENTS.md and it'll add a case branch for your distro in ~20 lines. See docs/LINUX.md."
            ;;
    esac

    info "Platform: ${BOLD}${PLATFORM}${RESET}  |  Accelerator: ${BOLD}${ACCEL}${RESET}"

    # Port resolution — auto-detect free ports for every lab service.
    # Bumps from the default (or the value already in lab.conf, so
    # bookmarks survive re-runs) until a free port is found.
    resolve_ports
    # Sudo-cache pre-flight for platforms that need it — warns only, so
    # the user doesn't get surprised by a password prompt 90 seconds in.
    check_sudo
    # Homebrew bootstrap — on macOS, every install_services call needs
    # brew, and python@3.11 / node ride on it too. Install it now (with
    # confirmation in interactive mode) so the rest of setup just works.
    ensure_brew
}

# ensure_brew — bootstrap Homebrew on macOS if missing. No-op on Linux/WSL.
# Idempotent. Honors ARAIL_NONINTERACTIVE (auto-install) and
# ARAIL_AUTO_INSTALL=0 (refuse to install, fall back to original error).
ensure_brew() {
    [[ "$PLATFORM" == "macos" ]] || return 0
    if command -v brew &>/dev/null; then
        return 0
    fi
    if ! auto_install_enabled; then
        error "Homebrew is required on macOS and ARAIL_AUTO_INSTALL=0. Install it manually, then re-run:
  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    fi
    info "Homebrew not found — required on macOS for python, ttyd, tmux, ollama."
    if ! confirm "Install Homebrew now (downloads the official installer from brew.sh)?"; then
        error "Homebrew install declined. Install it manually, then re-run ./arailctl setup."
    fi
    info "Installing Homebrew (the installer may prompt for your sudo password)…"
    local log="${REPO_ROOT:-$PWD}/setup.log"
    NONINTERACTIVE=1 /bin/bash -c \
        "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
        >>"$log" 2>&1 || error "Homebrew install failed — see setup.log."
    # Make brew visible in this shell for the rest of setup. The official
    # installer drops brew at /opt/homebrew on Apple Silicon and
    # /usr/local on Intel; pick whichever exists.
    if   [[ -x /opt/homebrew/bin/brew ]]; then eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [[ -x /usr/local/bin/brew     ]]; then eval "$(/usr/local/bin/brew shellenv)"
    fi
    command -v brew &>/dev/null || error "Homebrew installed but not on PATH — see setup.log."
    info "Homebrew installed."
}

# --- Port resolver ----------------------------------------------------------
# Picks free ports for every service the lab binds. On a clean install,
# uses the documented defaults (8080 / 7681 / 8888 / 8443 / 11435). If a
# default is already taken (Jupyter on 8080, dev server on 8443, etc.) we
# auto-bump to the next free port instead of failing.
#
# On re-runs we prefer whatever lab.conf already holds — that way the
# user's bookmarked URLs (and any docs they wrote) survive across
# `./arailctl setup` invocations even if the original conflict has cleared.
#
# Resolved values land in PORT_BUMPS (for the end-of-run banner) and in
# the shell vars PORTAL_PORT / TERMINAL_PORT / NOTEBOOK_PORT / IDE_PORT /
# MLX_OPENAI_PORT, which setup_runtime_files() and the final banner read.
PORT_BUMPS=()
PORTAL_PORT=""
TERMINAL_PORT=""
NOTEBOOK_PORT=""
IDE_PORT=""
MLX_OPENAI_PORT=""

_port_in_use() {
    local p="$1"
    if command -v lsof &>/dev/null; then
        lsof -iTCP:"$p" -sTCP:LISTEN -P -n &>/dev/null
    elif command -v ss &>/dev/null; then
        ss -ltn "sport = :$p" 2>/dev/null | tail -n +2 | grep -q .
    else
        return 1  # No detection tool available — assume free.
    fi
}

# Walk forward from $start until a free port is found. Bails after 20
# tries so a runaway scan doesn't hang setup.
_find_free_port() {
    local start="$1" label="$2"
    local port="$start"
    local tries=0 max_tries=20
    while (( tries < max_tries )); do
        if _port_in_use "$port"; then
            tries=$((tries + 1))
            port=$((port + 1))
        else
            echo "$port"
            return 0
        fi
    done
    error "Could not find a free port for ${label} after ${max_tries} attempts. Free a port in the ${start}-$((start + max_tries)) range, or pre-set ${label} in lab.conf."
}

# Resolve one named port: prefer lab.conf if present, else the supplied
# default. Auto-bumps if the chosen value is occupied. Sets the
# corresponding shell var ($1) and appends to PORT_BUMPS when the value
# diverges from the documented default.
_resolve_port() {
    local key="$1" default="$2"
    local start_at="$default" existing resolved
    if [[ -f lab.conf ]]; then
        existing="$(grep -E "^${key}=" lab.conf | head -n1 | cut -d= -f2- | tr -d '"' | tr -d ' ')"
        if [[ -n "$existing" ]] && [[ "$existing" =~ ^[0-9]+$ ]]; then
            start_at="$existing"
        fi
    fi
    resolved="$(_find_free_port "$start_at" "$key")"
    eval "${key}=${resolved}"
    if [[ "$resolved" != "$default" ]]; then
        PORT_BUMPS+=("${key}: default ${default} → using ${resolved}")
    fi
}

resolve_ports() {
    _resolve_port PORTAL_PORT      8080
    _resolve_port TERMINAL_PORT    7681
    _resolve_port NOTEBOOK_PORT    8888
    _resolve_port IDE_PORT         8443
    _resolve_port MLX_OPENAI_PORT  11435

    if (( ${#PORT_BUMPS[@]} > 0 )); then
        warn "Auto-bumped one or more lab ports because the defaults were in use:"
        for line in "${PORT_BUMPS[@]}"; do
            warn "  $line"
        done
        warn "Pinned in lab.conf — future ./arailctl setup runs reuse the bumped values."
    else
        info "Lab ports free: portal=${PORTAL_PORT}  ide=${IDE_PORT}  notebook=${NOTEBOOK_PORT}  terminal=${TERMINAL_PORT}  mlx=${MLX_OPENAI_PORT}"
    fi
}

# Sudo-cache warning — brew/apt/dnf branches in install_services all
# call sudo. Tell the user up front so the password prompt in the
# middle of a long install doesn't surprise them.
check_sudo() {
    case "$PLATFORM" in
        linux|wsl|fedora|arch|gentoo)
            if ! sudo -v -n 2>/dev/null; then
                warn "sudo cache is empty — setup may prompt for your password mid-run."
            fi
            ;;
    esac
}

# -----------------------------------------------------------------------------
# Python environment
#
# Strategy (self-sufficient): probe a candidate list for any python that
# satisfies >=3.10. If found, use it. If none — and auto-install is
# enabled — bootstrap python@3.11 via the platform package manager,
# then re-probe. Sets $PYTHON_BIN to the absolute path of the chosen
# interpreter; venv creation and any pre-venv python call uses it.
# -----------------------------------------------------------------------------
_probe_python_bin() {
    # Walks candidate names and sets PYTHON_BIN to the first one that
    # satisfies major>=3 and minor>=10. Returns 0 if found, 1 if not.
    local cand v maj min
    PYTHON_BIN=""
    for cand in python3.12 python3.11 python3.10 python3; do
        command -v "$cand" &>/dev/null || continue
        v="$("$cand" -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)" || continue
        maj="${v%%.*}"; min="${v##*.}"
        if [[ "$maj" == "3" && "$min" =~ ^[0-9]+$ ]] && (( min >= 10 )); then
            PYTHON_BIN="$(command -v "$cand")"
            PYTHON_VERSION="$v"
            return 0
        fi
    done
    return 1
}

ensure_python() {
    step "3/11  Python environment (.venv + core deps)"

    if _probe_python_bin; then
        info "Python ${PYTHON_VERSION} ready at ${PYTHON_BIN}"
        local minv="${PYTHON_VERSION##*.}"
        if (( minv >= 13 )); then
            warn "Python ${PYTHON_VERSION} is newer than we test (3.10-3.12). May work; mlx/vllm/torch wheels can lag."
        fi
    else
        info "No Python 3.10+ found on PATH."
        if ! auto_install_enabled; then
            case "$PLATFORM" in
                gentoo) error "Install Python, then re-run ./arailctl setup:  emerge -av dev-lang/python:3.11" ;;
                macos)  error "Install Python, then re-run ./arailctl setup:  brew install python@3.11" ;;
                *)      error "Install Python 3.10-3.12 and re-run ./arailctl setup." ;;
            esac
        fi
        if ! confirm "Install Python 3.11 now via your system package manager?"; then
            error "Python install declined. Install Python 3.10-3.12 manually, then re-run ./arailctl setup."
        fi
        install_python_for_platform
        # Re-probe; if still nothing, the install silently failed.
        if ! _probe_python_bin; then
            error "Python install ran but no usable interpreter on PATH — see setup.log."
        fi
        info "Python ${PYTHON_VERSION} ready at ${PYTHON_BIN}"
    fi

    if [[ ! -d ".venv" ]]; then
        info "Creating virtual environment with ${PYTHON_BIN}…"
        "$PYTHON_BIN" -m venv .venv
    fi
    # shellcheck disable=SC1091
    source .venv/bin/activate
    pip install --upgrade pip -q
}

# install_python_for_platform — fourth case-statement port hook (parallel
# to detect_platform / install_services / install_accel_deps). External
# agents porting to a new distro extend this `case`. See AGENTS.md.
install_python_for_platform() {
    local log="${REPO_ROOT:-$PWD}/setup.log"
    case "$PLATFORM" in
        macos)
            ensure_brew
            info "Installing python@3.11 via Homebrew…"
            brew install python@3.11 >>"$log" 2>&1 || error "brew install python@3.11 failed — see setup.log."
            # python@3.11 is keg-only, but brew symlinks python3.11 into
            # the brew bin dir, which ensure_brew already put on PATH.
            # Belt-and-suspenders: prepend the keg's bin too.
            local pfx
            pfx="$(brew --prefix python@3.11 2>/dev/null || true)"
            [[ -n "$pfx" && -x "$pfx/bin/python3.11" ]] && export PATH="$pfx/bin:$PATH"
            ;;
        wsl|linux|ubuntu|debian)
            check_sudo
            info "Installing python3.11 via apt…"
            sudo apt-get update -qq >>"$log" 2>&1 || true
            if ! apt-cache show python3.11 &>/dev/null; then
                info "python3.11 not in default repos — enabling deadsnakes PPA…"
                sudo apt-get install -y -q software-properties-common >>"$log" 2>&1 \
                    || error "apt install software-properties-common failed — see setup.log."
                sudo add-apt-repository -y ppa:deadsnakes/ppa >>"$log" 2>&1 \
                    || error "add-apt-repository deadsnakes failed — see setup.log."
                sudo apt-get update -qq >>"$log" 2>&1 || true
            fi
            sudo apt-get install -y -q python3.11 python3.11-venv python3.11-dev >>"$log" 2>&1 \
                || error "apt install python3.11 failed — see setup.log."
            ;;
        fedora)
            check_sudo
            info "Installing python3.11 via dnf…"
            sudo dnf install -y python3.11 python3.11-devel >>"$log" 2>&1 \
                || error "dnf install python3.11 failed — see setup.log."
            ;;
        arch)
            check_sudo
            info "Installing python via pacman (Arch ships current Python)…"
            sudo pacman -S --noconfirm python python-pip >>"$log" 2>&1 \
                || error "pacman -S python failed — see setup.log."
            ;;
        gentoo)
            check_sudo
            info "Emerging dev-lang/python:3.11 (this can take a while)…"
            sudo emerge --quiet --ask=n dev-lang/python:3.11 >>"$log" 2>&1 \
                || error "emerge dev-lang/python:3.11 failed — see setup.log."
            ;;
        *)
            error "Don't know how to install Python on $PLATFORM. Add a branch to install_python_for_platform in scripts/setup.sh and re-run."
            ;;
    esac
}

# ensure_node — bootstrap Node.js/npm if missing. Used by the
# agent-browser branch in install_services. Default-prompt; non-fatal
# on platforms with no recipe (warn + return 1, agent-browser is optional).
ensure_node() {
    if command -v npm &>/dev/null; then
        return 0
    fi
    if ! auto_install_enabled; then
        warn "npm missing and ARAIL_AUTO_INSTALL=0; skipping Node.js install."
        return 1
    fi
    if ! confirm "Install Node.js (needed for the agent-browser web research tool)?"; then
        warn "Skipping Node.js install. Knowledge tab browse will be unavailable."
        return 1
    fi
    local log="${REPO_ROOT:-$PWD}/setup.log"
    case "$PLATFORM" in
        macos)
            ensure_brew
            info "Installing node via Homebrew…"
            brew install node >>"$log" 2>&1 || { warn "brew install node failed — see setup.log."; return 1; }
            ;;
        wsl|linux|ubuntu|debian)
            check_sudo
            info "Installing nodejs + npm via apt…"
            sudo apt-get install -y -q nodejs npm >>"$log" 2>&1 || { warn "apt install nodejs failed — see setup.log."; return 1; }
            ;;
        fedora)
            check_sudo
            sudo dnf install -y nodejs npm >>"$log" 2>&1 || { warn "dnf install nodejs failed — see setup.log."; return 1; }
            ;;
        arch)
            check_sudo
            sudo pacman -S --noconfirm nodejs npm >>"$log" 2>&1 || { warn "pacman -S nodejs failed — see setup.log."; return 1; }
            ;;
        gentoo)
            check_sudo
            sudo emerge --quiet --ask=n net-libs/nodejs >>"$log" 2>&1 || { warn "emerge nodejs failed — see setup.log."; return 1; }
            ;;
        *)
            warn "No Node.js install recipe for $PLATFORM. Install nodejs + npm manually for agent-browser."
            return 1
            ;;
    esac
    command -v npm &>/dev/null
}

# -----------------------------------------------------------------------------
# Install core Python deps
# -----------------------------------------------------------------------------
install_core_deps() {
    local tier="${LAB_TIER:-minimalist}"
    case "$tier" in
        minimalist|maximus) ;;
        min) warn "Legacy tier 'min' — migrating to 'minimalist' (compat shim removes in v1.1.0)."; tier="minimalist" ;;
        max) warn "Legacy tier 'max' — migrating to 'maximus' (compat shim removes in v1.1.0)."; tier="maximus" ;;
        med) warn "Legacy tier 'med' retired — promoting to 'maximus'."; tier="maximus" ;;
        *)   tier="minimalist" ;;
    esac
    info "Installing Python packages (tier=${BOLD}${tier}${RESET}) from ${BOLD}pyproject.toml${RESET}…"
    local log="${REPO_ROOT:-$PWD}/setup.log"
    pip install -q -e ".[dev,${tier}]" 2>>"$log" || {
        warn "Core deps install failed. Last 20 lines of setup.log:"
        tail -n 20 "$log" | sed 's/^/    /' >&2
        error "pip install failed. See setup.log, then re-run: ./arailctl setup"
    }
    info "Core dependencies installed for tier '${tier}'."
}

# -----------------------------------------------------------------------------
# Platform-specific accelerator deps
# -----------------------------------------------------------------------------
install_accel_deps() {
    case "$ACCEL" in
        mlx)
            install_pyproject_extra "mlx" "MLX accelerator runtime"
            ;;
        cuda)
            install_pyproject_extra "cuda" "CUDA / vLLM runtime"
            ;;
        cpu)
            warn "No GPU detected — installing CPU inference runtime from pyproject."
            install_pyproject_extra "cpu" "CPU inference runtime"
            ;;
    esac

    # AirLLM — v1.0.0 made AirLLM opt-in (was bundled in both tiers
    # before). Power users on CUDA/Linux can enable it with
    # ARAIL_INSTALL_AIRLLM=1 to get layer-streaming inference for
    # 70B/405B models. Default install path skips AirLLM entirely.
    if [[ "${ARAIL_INSTALL_AIRLLM:-0}" == "1" ]]; then
        local airllm_pkg="${ARAIL_AIRLLM_PACKAGE_OVERRIDE:-$AIRLLM_PACKAGE_SPEC}"
        info "Installing AirLLM (${airllm_pkg}, opt-in via ARAIL_INSTALL_AIRLLM=1)…"
        if pip install -q "$airllm_pkg" 2>&1 | tail -5; then
            info "AirLLM ready. Dashboard chat card has a 'Deep model' toggle."
            if [[ -n "${ARAIL_AIRLLM_PACKAGE_OVERRIDE:-}" ]]; then
                warn "Using ARAIL_AIRLLM_PACKAGE_OVERRIDE for this run only."
            fi
        else
            warn "AirLLM install failed — check [tool.arail.package-sources] in pyproject.toml or ARAIL_AIRLLM_PACKAGE_OVERRIDE."
            warn "Continuing without AirLLM."
        fi
    else
        info "Skipping AirLLM (opt-in via ARAIL_INSTALL_AIRLLM=1)."
    fi

    # AeroLLM — the Maximus tier deep-mode backend (the "2nd inference").
    # Gated to tier=maximus so Minimalist installs stay lean (ai-eng is enough
    # for the everyday lab; deep streaming is a Maximus escalation).
    # scripts/build-aerollm.sh auto picks one of three channels, in order:
    #   DEV      — LOCAL sibling repo present ($ARAIL_AEROLLM_REPO, default
    #              ~/ProJects/qukaizen-aerollm) → cargo build (NOT `maturin
    #              develop`, which breaks the Metal kernel compile on macOS
    #              arm64). Maintainer-only in practice.
    #   RELEASE  — AEROLLM_CHANNEL=release or private-index credentials
    #              configured → pip install from the self-hosted index.
    #              Maintainer-only in practice (needs creds).
    #   BUNDLED  — otherwise (the outside-user default as of 2026-08):
    #              fetch a checksummed prebuilt binary from an ARAIL GitHub
    #              Release asset. No source repo, no credentials. See
    #              sprints/2026-08-05-arail-bundled-aerollm/ARCHITECTURE.md.
    # On CUDA Maximus, AeroLLM's CUDA backend isn't ready yet; AirLLM
    # (opt-in) is the fallback.
    if [[ "${LAB_TIER:-minimalist}" != "maximus" ]]; then
        info "Skipping AeroLLM install — tier is '${LAB_TIER:-minimalist}', not 'maximus'."
        info "  Upgrade with: ./arailctl upgrade maximus"
    elif [[ "${ARAIL_SKIP_AEROLLM_PROBE:-0}" == "1" ]]; then
        info "Skipping AeroLLM build (ARAIL_SKIP_AEROLLM_PROBE=1)."
    elif [[ "$(uname -s)" == "Darwin" ]] && [[ "$(uname -m)" == "arm64" ]]; then
        if python3 -c "import aerollm_api" 2>/dev/null; then
            info "aerollm_api already present — AeroLLM is the deep-mode 2nd inference."
        else
            info "Installing AeroLLM (source → release → bundled, first that applies)…"
            if AEROLLM_INDEX_URL="$AEROLLM_INDEX_URL" AEROLLM_PIP_SPEC="$AEROLLM_PIP_SPEC" \
               AEROLLM_BUNDLE_TAG="$AEROLLM_BUNDLE_TAG" \
               AEROLLM_BUNDLE_SHA256="$AEROLLM_BUNDLE_SHA256" \
               bash "${REPO_ROOT:-$PWD}/scripts/build-aerollm.sh" auto; then
                info "AeroLLM ready — the deep-mode 2nd inference on Apple Silicon."
            else
                warn "AeroLLM not installed (see above). The lab runs without the"
                warn "2nd inference until you run: ./arailctl deep install (bundled binary,"
                warn "no source repo or credentials needed — or: deep rebuild / deep update"
                warn "if you're a maintainer with the aerollm source or private-index access)"
            fi
        fi
    else
        info "AeroLLM auto-build is Apple-Silicon-only today; on this host run"
        info "  ./arailctl deep rebuild  once aerollm supports it (CUDA backend WIP)."
    fi
}

# -----------------------------------------------------------------------------
# Portable timeout wrapper.
# `timeout(1)` is GNU coreutils — present on Linux, but NOT on stock macOS
# (where it's `gtimeout` only if `brew install coreutils` ran). Without this
# shim, every `timeout 900 ollama pull …` in the ai-eng install ladder fails
# instantly on a clean Mac, so setup finishes with NO model installed — the
# exact opposite of "everyone gets ai-eng on first setup". This wrapper uses
# whichever real timeout exists, and if neither does, runs the command with
# no time limit (correctness over the cap) rather than failing closed.
# Usage: _arail_timeout <seconds> <cmd> [args…]
# -----------------------------------------------------------------------------
_arail_timeout() {
    local _dur="$1"; shift
    if command -v timeout &>/dev/null; then
        timeout "$_dur" "$@"
    elif command -v gtimeout &>/dev/null; then
        gtimeout "$_dur" "$@"
    else
        # No timeout binary (typical stock macOS). Run uncapped so the model
        # still installs; warn once so slow/hung pulls are explainable.
        if [[ "${_ARAIL_TIMEOUT_WARNED:-0}" != "1" ]]; then
            warn "No 'timeout'/'gtimeout' found — running model fetches without a time cap."
            warn "  (Optional on macOS: 'brew install coreutils' to enable timeouts.)"
            _ARAIL_TIMEOUT_WARNED=1
        fi
        "$@"
    fi
}

# -----------------------------------------------------------------------------
# Optional services — the embedded browser terminal.
# ttyd powers /terminal on the portal. Without it, the terminal tab shows
# install instructions instead of a broken iframe. We try to install it
# opportunistically using whatever package manager is available; failures
# are logged and the rest of setup continues.
# -----------------------------------------------------------------------------
install_services() {
    step "2/11  System packages (ttyd, tmux, agent-browser, optional ollama)"
    # ttyd — the browser terminal.
    if ! command -v ttyd &>/dev/null; then
        case "$PLATFORM" in
            macos)
                if command -v brew &>/dev/null; then
                    info "Installing ttyd via Homebrew…"
                    brew install ttyd 2>&1 | tail -3 || warn "ttyd install failed — /terminal will show install help instead."
                else
                    warn "Homebrew not found — skipping ttyd. Install later: brew install ttyd"
                fi
                ;;
            wsl|ubuntu|debian)
                if command -v apt &>/dev/null; then
                    info "Installing ttyd via apt…"
                    sudo apt-get install -y -q ttyd 2>&1 | tail -3 || warn "ttyd install failed — install later: sudo apt install ttyd"
                fi
                ;;
            gentoo)
                command -v emerge &>/dev/null && info "ttyd on Gentoo: sudo emerge -av www-apps/ttyd"
                ;;
            *)
                warn "Unknown platform — install ttyd manually for browser terminal support."
                ;;
        esac
    else
        info "ttyd already installed ($(ttyd --version 2>&1 | head -1))"
    fi

    # tmux — terminal persistence. Without it, the browser terminal
    # iframe loses scrollback on every nav click. With it, ttyd attaches
    # to a named tmux session that survives reconnects.
    if ! command -v tmux &>/dev/null; then
        case "$PLATFORM" in
            macos)
                command -v brew &>/dev/null && info "Installing tmux via Homebrew…" \
                    && brew install tmux 2>&1 | tail -3 \
                    || warn "tmux not installed — terminal scrollback won't persist across iframe reloads."
                ;;
            wsl|ubuntu|debian)
                command -v apt &>/dev/null && info "Installing tmux via apt…" \
                    && sudo apt-get install -y -q tmux 2>&1 | tail -3 \
                    || warn "tmux not installed — install later: sudo apt install tmux"
                ;;
            *)
                warn "tmux not installed — terminal sessions won't persist across reloads."
                ;;
        esac
    else
        info "tmux already installed ($(tmux -V 2>&1))"
    fi

    # agent-browser — web research agent for the Knowledge tab. Bootstraps
    # Node.js via the platform package manager if npm is missing.
    if ! command -v agent-browser &>/dev/null; then
        if ensure_node; then
            info "Installing agent-browser…"
            npm install -g agent-browser 2>&1 | tail -3 || warn "agent-browser install failed — Knowledge tab browse will be unavailable."
            command -v agent-browser &>/dev/null && agent-browser install 2>&1 | tail -3 || true
        fi
    else
        info "agent-browser already installed"
    fi

    # Ollama — optional local OpenAI-compatible LLM server. On Apple
    # Silicon, Arail's primary local inference path is direct MLX via
    # mlx-lm, so we skip Ollama by default unless explicitly enabled.
    # It remains useful for surfaces that want an HTTP API, like Open
    # Notebook or other OpenAI-compatible tools.
    local ollama_enabled=1
    if ! ollama_default_enabled && [[ "${ARAIL_ENABLE_OLLAMA:-0}" != "1" ]]; then
        ollama_enabled=0
        info "Apple Silicon detected — MLX/mlx-lm is the default local runtime."
        info "Skipping Ollama install by default. Enable it with ARAIL_ENABLE_OLLAMA=1 if you want a local OpenAI-compatible API too."
    fi

    if [[ "$ollama_enabled" == "0" ]]; then
        if command -v ollama &>/dev/null; then
            info "Ollama already installed ($(ollama --version 2>&1 | head -1))"
        fi
        return
    fi

    if ! command -v ollama &>/dev/null; then
        case "$PLATFORM" in
            macos)
                if command -v brew &>/dev/null; then
                    info "Installing Ollama…"
                    brew install ollama 2>&1 | tail -3 || warn "Ollama install failed — Open Notebook AI features will require manual setup."
                else
                    warn "Homebrew not found — install Ollama manually: https://ollama.com"
                fi
                ;;
            *)
                info "Install Ollama for local AI features: https://ollama.com"
                ;;
        esac
    else
        info "Ollama already installed ($(ollama --version 2>&1 | head -1))"
    fi

    # Two-tier persona-wrap model strategy (MODEL-TIERS-V2, 2026-05-31):
    #
    #   DEFAULT PATH (minimalist):
    #     ollama pull llama3.2:1b → ollama create llama-ai-eng -f Modelfile.default
    #     License: Llama 3.2 Community License. Model name begins with "Llama".
    #
    #   DORMANT self-hosted GGUF ladder (opt-in: ARAIL_AI_ENG_SELFHOSTED=1):
    #     HF primary → GitHub mirror (sha256-verified) → CDN → preview net.
    #     This is the future Nucleus-distill lane. Off by default.
    #
    #   MAXIMUS DEEP (offered, not forced):
    #     Prints the command to install ai-engineer (Qwen2.5-7B-Instruct + persona).
    #     Auto-runs only with ARAIL_INSTALL_DEEP_PERSONA=1.
    #
    # Skip the entire block with ARAIL_SKIP_OLLAMA=1 on slow/offline setups.

    if command -v ollama &>/dev/null; then
        if [[ "${ARAIL_SKIP_OLLAMA:-0}" == "1" ]]; then
            warn "ARAIL_SKIP_OLLAMA=1 — skipping model install. Run later:"
            warn "  ollama pull llama3.2:1b && ollama create llama-ai-eng -f models/ai-eng/Modelfile.default"
            return
        fi

        # Fail-fast, don't hang. Skip the (multi-minute) model download when:
        #   • ARAIL_SKIP_MODEL_DOWNLOAD=1 is set explicitly, OR
        #   • the ollama daemon isn't reachable (a pull would otherwise hang
        #     until the timeout instead of failing immediately).
        # In both cases we print the one command to run later — setup never
        # appears to freeze for 15 minutes on a slow/offline machine. The pull
        # timeouts below are also capped at 180s (was 900s) as a second guard.
        if [[ "${ARAIL_SKIP_MODEL_DOWNLOAD:-0}" == "1" ]]; then
            warn "ARAIL_SKIP_MODEL_DOWNLOAD=1 — skipping model install. Run later:"
            warn "  ollama pull llama3.2:1b && ollama create llama-ai-eng -f models/ai-eng/Modelfile.default"
            return
        fi
        if ! _arail_timeout 5 ollama list &>/dev/null; then
            warn "Ollama daemon not reachable — skipping model download (would hang)."
            warn "Start ollama, then run:"
            warn "  ollama pull llama3.2:1b && ollama create llama-ai-eng -f models/ai-eng/Modelfile.default"
            return
        fi

        # ── Phase B (gated, DORMANT): Gemma 2B default-floor — qkz-project-aware-2b ──
        # Make the minimalist default brain a ~2B Gemma generalist (a stronger floor
        # than the 1B for technical Worlds like physics). OFF by default — fires only
        # with ARAIL_DEFAULT_GEMMA=1 AND once BOTH gates land:
        #   G1: a real models/ai-eng/Modelfile.gemma (no __PLACEHOLDER__) + the Gemma
        #       base, from the qukaizen-ddac / Gemma POC session.
        #   G2: the "Built with Gemma" disclosure
        #       (sprints/2026-06-14-world-model-hint/handoff/gemma-disclosure-checklist.md).
        # Until then this is a NO-OP and the default stays llama-ai-eng (below).
        if [[ "${ARAIL_DEFAULT_GEMMA:-0}" == "1" ]]; then
            local _gemma_modelfile="${REPO_ROOT:-$PWD}/models/ai-eng/Modelfile.gemma"
            if [[ -f "$_gemma_modelfile" ]] && ! grep -q "__PLACEHOLDER__" "$_gemma_modelfile"; then
                if ollama show qkz-project-aware-2b &>/dev/null; then
                    info "qkz-project-aware-2b (Gemma 2B default) already present — skipping."
                    return
                fi
                info "ARAIL_DEFAULT_GEMMA=1 — installing the Gemma 2B default (qkz-project-aware-2b, Built with Gemma)…"
                if _arail_timeout 300 ollama create qkz-project-aware-2b -f "$_gemma_modelfile" 2>&1 | tail -5; then
                    info "qkz-project-aware-2b ready (Gemma 2B generalist, Built with Gemma)."
                    return
                fi
                warn "Gemma default install failed — falling back to llama-ai-eng."
            else
                warn "ARAIL_DEFAULT_GEMMA=1 but models/ai-eng/Modelfile.gemma is missing/placeholder"
                warn "(Phase B gate G1 not landed) — keeping the llama-ai-eng default."
            fi
        fi

        local _modelfile_dir="${REPO_ROOT:-$PWD}/models/ai-eng"

        # ── Idempotency: if any ai-eng family model is already present, skip ──
        # Candidate order: llama-ai-eng (new default) > ai-eng (v1.0.0) > ai-engineer (pre-v1.0.0)
        if ollama show llama-ai-eng &>/dev/null; then
            info "llama-ai-eng already present in Ollama — skipping default install."
        elif ollama show ai-eng &>/dev/null; then
            info "ai-eng model already present in Ollama — skipping default install."
        elif ollama show ai-engineer:latest &>/dev/null; then
            # Legacy pre-v1.0.0 install: adopt ai-engineer:latest as the deep persona.
            # Do NOT alias it to llama-ai-eng — ai-engineer:latest is the 7B deep model
            # (Qwen2.5-7B, Apache-2.0). Aliasing it to the 1B default would mislabel
            # a 7B as the 1B default. Instead: note it as the deep model and install
            # the 1B default fresh.
            info "Found legacy ai-engineer:latest (7B deep persona) — this is the maximus deep model."
            info "Installing the 1B default (llama-ai-eng) separately…"
            _install_llama_ai_eng() {
                info "Pulling llama3.2:1b (Llama-3.2-1B-Instruct, ~0.9 GB)…"
                if _arail_timeout 180 ollama pull llama3.2:1b 2>&1 | tail -5; then
                    info "Creating llama-ai-eng persona from Modelfile.default…"
                    if _arail_timeout 300 ollama create llama-ai-eng -f "${_modelfile_dir}/Modelfile.default" 2>&1 | tail -5; then
                        info "llama-ai-eng ready (AI engineer persona, built with Llama)."
                        return 0
                    else
                        warn "ollama create llama-ai-eng failed. Run manually:"
                        warn "  ollama create llama-ai-eng -f ${_modelfile_dir}/Modelfile.default"
                        return 1
                    fi
                else
                    warn "ollama pull llama3.2:1b failed (offline or network issue). Run manually:"
                    warn "  ollama pull llama3.2:1b && ollama create llama-ai-eng -f ${_modelfile_dir}/Modelfile.default"
                    return 1
                fi
            }
            _install_llama_ai_eng || true
        else
            # ── DEFAULT PATH: persona-wrap install ────────────────────────────
            # Primary and ONLY default path (MODEL-TIERS-V2 §2):
            #   1. ollama pull llama3.2:1b   — fetches Meta Llama-3.2-1B-Instruct Q4_K_M
            #   2. ollama create llama-ai-eng — wraps it with the AI-engineer SYSTEM prompt
            # No HF artifact, no sha256, no ladder. Works today. License: Llama 3.2 Community.
            #
            # DORMANT self-hosted ladder is gated behind ARAIL_AI_ENG_SELFHOSTED=1 (below).

            if [[ "${ARAIL_AI_ENG_SELFHOSTED:-0}" == "1" ]]; then
                # ── DORMANT self-hosted GGUF ladder (opt-in) ──────────────────
                # This is the future Nucleus-distill lane. Not the default path.
                info "ARAIL_AI_ENG_SELFHOSTED=1 — using dormant self-hosted GGUF ladder…"

                local _gguf_tmp="${TMPDIR:-/tmp}/arail_ai_eng_download.gguf"
                local _ai_eng_ok=0
                local _hf_repo="${ARAIL_AI_ENG_HF_REPO:-qukaizen/ai-eng-1.5b-gguf}"  # __PLACEHOLDER__
                local _quant="${ARAIL_AI_ENG_QUANT:-Q4_K_M}"                          # __PLACEHOLDER__
                local _gh_url="${ARAIL_AI_ENG_GH_URL:-https://github.com/qukaizen/arail/releases/download/ai-eng-1.5b/ai-eng-1.5b-Q4_K_M.gguf}"  # __PLACEHOLDER__
                local _cdn_url="${ARAIL_AI_ENG_CDN_URL:-}"
                local _sha256="${ARAIL_AI_ENG_SHA256:-__PLACEHOLDER_SHA256__}"

                info "Fetching ai-eng (self-hosted HuggingFace primary)…"
                if _arail_timeout 180 ollama pull "hf.co/${_hf_repo}:${_quant}" 2>&1 | tail -5; then
                    info "ai-eng fetched from HuggingFace. Done."
                    _ai_eng_ok=1
                else
                    warn "HuggingFace pull failed — trying mirror…"
                fi

                _install_from_gguf() {
                    local _url="$1" _label="$2"
                    if [[ "$_sha256" == "__PLACEHOLDER_SHA256__" ]]; then
                        warn "ai_eng_sha256 is still a placeholder — mirror path (${_label}) is disabled"
                        warn "until a real digest is pinned (run scripts/build_ai_eng.sh publish, then"
                        warn "set ARAIL_AI_ENG_SHA256 or ai_eng_sha256 in pyproject.toml)."
                        return 1
                    fi
                    info "Downloading ai-eng from ${_label}…"
                    rm -f "$_gguf_tmp"
                    if ! _arail_timeout 900 curl -fL -m 900 -o "$_gguf_tmp" "$_url" 2>&1 | tail -3; then
                        warn "${_label} download failed or timed out."
                        rm -f "$_gguf_tmp"
                        return 1
                    fi
                    local _got_sha256
                    _got_sha256="$(sha256sum "$_gguf_tmp" 2>/dev/null | awk '{print $1}')"
                    if [[ "$_got_sha256" != "$_sha256" ]]; then
                        warn "sha256 mismatch on ${_label} download (got ${_got_sha256}, expected ${_sha256})."
                        warn "Discarding unverified file — skipping ollama create."
                        rm -f "$_gguf_tmp"
                        return 1
                    fi
                    info "sha256 verified. Creating ai-eng from local GGUF…"
                    local _gen_mf="${TMPDIR:-/tmp}/arail_ai_eng_gen.Modelfile"
                    printf 'FROM %s\n' "$_gguf_tmp" > "$_gen_mf"
                    if _arail_timeout 300 ollama create ai-eng -f "$_gen_mf" 2>&1 | tail -5; then
                        rm -f "$_gguf_tmp" "$_gen_mf"
                        return 0
                    else
                        warn "ollama create failed from ${_label}."
                        rm -f "$_gguf_tmp" "$_gen_mf"
                        return 1
                    fi
                }

                if [[ "$_ai_eng_ok" -eq 0 ]]; then
                    if _install_from_gguf "$_gh_url" "GitHub Release"; then
                        info "ai-eng installed from GitHub Release mirror. Done."
                        _ai_eng_ok=1
                    else
                        warn "GitHub Release mirror failed…"
                    fi
                fi

                if [[ "$_ai_eng_ok" -eq 0 && -n "$_cdn_url" ]]; then
                    if _install_from_gguf "$_cdn_url" "qukaizen.com CDN"; then
                        info "ai-eng installed from qukaizen.com CDN mirror. Done."
                        _ai_eng_ok=1
                    else
                        warn "CDN mirror failed — falling through to preview net…"
                    fi
                fi

                if [[ "$_ai_eng_ok" -eq 0 ]]; then
                    warn "Self-hosted ai-eng artifact not reachable — falling back to preview net."
                    local _preview_base="qwen2.5:1.5b"
                    local _preview_mf="${_modelfile_dir}/Modelfile.preview"
                    if _arail_timeout 180 ollama pull "$_preview_base" 2>&1 | tail -5; then
                        if [[ -f "$_preview_mf" ]]; then
                            if _arail_timeout 300 ollama create ai-eng -f "$_preview_mf" 2>&1 | tail -5; then
                                info "ai-eng (preview) ready. Re-run setup after the GGUF is uploaded."
                            else
                                warn "ai-eng preview create failed."
                            fi
                        fi
                    else
                        warn "Preview base fetch failed. Run manually:"
                        warn "  ollama pull qwen2.5:1.5b && ollama create ai-eng -f ${_preview_mf}"
                    fi
                fi

                rm -f "$_gguf_tmp"
            else
                # ── DEFAULT PATH: simple persona-wrap (MODEL-TIERS-V2 §2) ────
                info "Installing Llama AI Engineer (built with Llama-3.2-1B-Instruct)…"
                info "Pulling llama3.2:1b (~0.9 GB) — this may take a minute…"
                if _arail_timeout 180 ollama pull llama3.2:1b 2>&1 | tail -5; then
                    info "Creating llama-ai-eng persona (AI engineer, Built with Llama)…"
                    if _arail_timeout 300 ollama create llama-ai-eng -f "${_modelfile_dir}/Modelfile.default" 2>&1 | tail -5; then
                        info "llama-ai-eng ready. Default AI engineer is installed."
                    else
                        warn "ollama create llama-ai-eng failed. Run manually:"
                        warn "  ollama create llama-ai-eng -f ${_modelfile_dir}/Modelfile.default"
                    fi
                else
                    warn "ollama pull llama3.2:1b failed (offline or network issue). Run manually:"
                    warn "  ollama pull llama3.2:1b && ollama create llama-ai-eng -f ${_modelfile_dir}/Modelfile.default"
                fi
            fi
        fi

        # ── Maximus deep persona OFFER ─────────────────────────────────────────
        # The 7B deep persona (ai-engineer, Qwen2.5-7B, Apache-2.0) is offered,
        # not forced. Auto-installs only with ARAIL_INSTALL_DEEP_PERSONA=1.
        if [[ "${LAB_TIER:-minimalist}" == "maximus" ]] || [[ "${ARAIL_INSTALL_DEEP_PERSONA:-0}" == "1" ]]; then
            if ollama show ai-engineer:latest &>/dev/null; then
                info "ai-engineer (deep 7B persona) already present — skipping."
            elif [[ "${ARAIL_INSTALL_DEEP_PERSONA:-0}" == "1" ]]; then
                info "ARAIL_INSTALL_DEEP_PERSONA=1 — installing deep AI engineer persona (7B)…"
                if _arail_timeout 180 ollama pull qwen2.5:7b 2>&1 | tail -5; then
                    if _arail_timeout 300 ollama create ai-engineer -f "${_modelfile_dir}/Modelfile.deep" 2>&1 | tail -5; then
                        info "ai-engineer (deep 7B persona) ready."
                    else
                        warn "ollama create ai-engineer failed. Run manually:"
                        warn "  ollama create ai-engineer -f ${_modelfile_dir}/Modelfile.deep"
                    fi
                else
                    warn "ollama pull qwen2.5:7b failed. Run manually:"
                    warn "  ollama pull qwen2.5:7b && ollama create ai-engineer -f ${_modelfile_dir}/Modelfile.deep"
                fi
            else
                info "Maximus deep AI engineer (7B, Qwen2.5-7B-Instruct) not installed."
                info "To install, run:"
                info "  ollama pull qwen2.5:7b && ollama create ai-engineer -f ${_modelfile_dir}/Modelfile.deep"
                info "  (or set ARAIL_INSTALL_DEEP_PERSONA=1 and re-run setup)"
            fi
        fi
    fi

    # ── Embedding model for PKB semantic search (C5, arail2-tier1-integration) ──
    # nomic-embed-text (~274 MB), the spec-declared provider arail.dbspec.embed
    # serves. Every code path above this point either returned early
    # (ARAIL_SKIP_OLLAMA / ARAIL_SKIP_MODEL_DOWNLOAD / daemon unreachable — this
    # pull inherits the same skip intent) or confirmed Ollama is installed and
    # its daemon is reachable, so this reuses that same gate rather than
    # re-probing. Same pattern as the llama3.2:1b pull above: warn and
    # continue, never fail setup. First PKB ingest on a machine without this
    # model fails loudly with an actionable `ollama pull` message and writes
    # zero vectors (see arail.dbspec.embed.EmbeddingUnavailable) — it does not
    # silently write hash vectors instead.
    if ollama show nomic-embed-text &>/dev/null; then
        info "nomic-embed-text already present in Ollama — skipping."
    else
        info "Pulling nomic-embed-text (~274 MB, PKB semantic search)…"
        if _arail_timeout 180 ollama pull nomic-embed-text 2>&1 | tail -5; then
            info "nomic-embed-text ready."
        else
            warn "ollama pull nomic-embed-text failed (offline or network issue)."
            warn "PKB semantic search will degrade with an actionable message until"
            warn "you run:  ollama pull nomic-embed-text"
        fi
    fi
}

# -----------------------------------------------------------------------------
# Gentoo-specific guidance
# -----------------------------------------------------------------------------
gentoo_notes() {
    if [[ "$PLATFORM" != "gentoo" ]]; then return; fi

    echo ""
    info "${BOLD}Gentoo-specific notes${RESET}"
    echo "  Recommended USE flags for CUDA:"
    echo "    echo 'dev-util/nvidia-cuda-toolkit -profiler' >> /etc/portage/package.use/cuda"
    echo ""
    echo "  Key packages:"
    echo "    emerge -av dev-lang/python dev-python/pip sci-libs/pytorch"
    echo "    emerge -av dev-util/nvidia-cuda-toolkit  # if Nvidia GPU"
    echo ""
    echo "  Kernel: enable CONFIG_DRM, CONFIG_DRM_NOUVEAU or nvidia-drivers."
    echo "  See docs/LINUX.md for Gentoo notes."
    echo ""
}

# -----------------------------------------------------------------------------
# WSL-specific guidance
# -----------------------------------------------------------------------------
wsl_notes() {
    if [[ "$PLATFORM" != "wsl" ]]; then return; fi

    echo ""
    info "${BOLD}WSL notes${RESET}"
    echo "  For Nvidia GPU passthrough, ensure:"
    echo "    1. Windows Nvidia driver >= 525.x installed on the HOST"
    echo "    2. WSL2 (not WSL1): wsl --set-version <distro> 2"
    echo "    3. nvidia-smi works inside WSL"
    echo "  See docs/WSL.md for details."
    echo ""
}

# -----------------------------------------------------------------------------
# Brand — name your lab. Captured once, persisted to .env, every future run
# reuses it. Fork → rename → you have your own product.
# -----------------------------------------------------------------------------
capture_brand() {
    # If .env already has a LAB_NAME we respect it and move on.
    if [[ -f .env ]] && grep -q '^LAB_NAME=' .env; then
        local existing
        existing="$(grep -E '^LAB_NAME=' .env | head -n1 | cut -d= -f2- | tr -d '"')"
        if [[ -n "$existing" ]]; then
            LAB_NAME="$existing"
            info "Lab name: ${BOLD}${LAB_NAME}${RESET} (from .env)"
            return
        fi
    fi

    if [[ ! -t 0 ]] || [[ "${ARAIL_NONINTERACTIVE:-0}" == "1" ]]; then
        LAB_NAME="Autoresearch AI Lab"
        LAB_SHORT_NAME="$(_slugify "$LAB_NAME")"
        LAB_TAGLINE="A learn-by-doing AI research lab"
        info "Non-interactive — using default lab name: ${LAB_NAME}"
        return
    fi

    step "4/11  Name your lab"
    echo "  This is how the dashboard, portal, wiki, and every banner will"
    echo "  refer to your lab. Pick something that feels like yours —"
    echo "  ${BOLD}Sam's AI Lab${RESET}, ${BOLD}gentoofoo's ai lab${RESET}, ${BOLD}PeanutLab${RESET}, or keep the default."
    echo ""
    read -rp "  Lab name [Autoresearch AI Lab]: " LAB_NAME
    LAB_NAME="${LAB_NAME:-Autoresearch AI Lab}"

    # Lowercase, hyphenated, alphanumeric — used as the prefix in
    # log lines AND as the tmux session name in ttyd. Spaces here
    # break tmux ("session name can't contain whitespace") and look
    # ugly in process listings.
    LAB_SHORT_NAME="$(_slugify "$LAB_NAME")"

    echo ""
    read -rp "  One-line tagline [A learn-by-doing AI research lab]: " LAB_TAGLINE
    LAB_TAGLINE="${LAB_TAGLINE:-A learn-by-doing AI research lab}"

    info "Lab name: ${BOLD}${LAB_NAME}${RESET}"
}

# Lowercase + hyphenate + drop non-alphanumerics. Reused for default
# tmux session names so they survive shell parsing. Uses printf (not
# echo) so the trailing newline doesn't get converted to a stray dash
# by the tr pipeline; also collapses any leading/trailing dashes.
_slugify() {
    local s
    s="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -s '[:space:]' '-' | tr -cd 'a-z0-9-')"
    s="${s##-}"  # drop leading dashes
    s="${s%%-}"  # drop trailing dashes
    printf '%s' "$s"
}

# -----------------------------------------------------------------------------
# Install tier — minimalist / maximus (legacy min/med/max still accepted). Captured once, persisted to .env.
#
#   min → Dashboard, Chat, Autoresearch
#   med → + Knowledge Base, Agents, LanceDB vectors
#   max → + Admin, Notebooks, AirLLM (deep), full cloud vendor catalog
#
# Upgrade later with `./arailctl upgrade med` (or max).
# -----------------------------------------------------------------------------
LAB_TIER=""
capture_tier() {
    # Respect an existing .env value. Legacy min/max/med fold to the new
    # names (min → minimalist, max/med → maximus). Compat shim removed in
    # v1.1.0.
    if [[ -f .env ]] && grep -q '^LAB_TIER=' .env; then
        local existing
        existing="$(grep -E '^LAB_TIER=' .env | head -n1 | cut -d= -f2- | tr -d '"')"
        case "$existing" in
            minimalist|maximus)
                LAB_TIER="$existing"
                info "Install tier: ${BOLD}${LAB_TIER}${RESET} (from .env)"
                return
                ;;
            min)
                warn "Legacy tier 'min' in .env — migrating to 'minimalist'."
                LAB_TIER="minimalist"
                return
                ;;
            max)
                warn "Legacy tier 'max' in .env — migrating to 'maximus'."
                LAB_TIER="maximus"
                return
                ;;
            med)
                warn "Legacy tier 'med' — rolling forward to 'maximus'."
                LAB_TIER="maximus"
                return
                ;;
        esac
    fi

    if [[ ! -t 0 ]] || [[ "${ARAIL_NONINTERACTIVE:-0}" == "1" ]]; then
        LAB_TIER="${ARAIL_TIER:-minimalist}"
        case "$LAB_TIER" in
            min) LAB_TIER="minimalist" ;;
            max) LAB_TIER="maximus"    ;;
        esac
        info "Non-interactive — using tier: ${LAB_TIER}"
        return
    fi

    step "4b/11  Pick an install tier"
    cat <<EOF
  Two tiers — upgrade later with ./arailctl upgrade maximus.

    ${BOLD}minimalist${RESET}  Dashboard + Chat + Autoresearch + Knowledge Base
                + Agents + LanceDB vector recall. The everyday lab.
                Ships the Llama AI Engineer (llama-ai-eng) — an AI engineering
                assistant built with Llama-3.2-1B-Instruct (~0.9 GB, runs on
                16 GB) — as the only default model. External providers
                (Claude, NVIDIA, OpenRouter,
                HuggingFace) reachable over plain HTTP when
                LAB_MODE=hybrid.
    ${BOLD}maximus${RESET}     Everything in minimalist + Admin, Notebooks,
                AeroLLM deep-mode runtime, Anthropic SDK,
                LangChain/LangGraph, full cloud catalog. Targets 32GB+
                machines.

  ${BOLD}LanceDB ships in both tiers${RESET} — KB and autoresearch are too central
  to be split across optional installs.
EOF
    echo ""
    local choice
    read -rp "  Tier [minimalist]: " choice
    LAB_TIER="${choice:-minimalist}"
    case "$LAB_TIER" in
        minimalist|maximus) info "Install tier: ${BOLD}${LAB_TIER}${RESET}" ;;
        min) warn "Legacy 'min' — using 'minimalist'."; LAB_TIER="minimalist" ;;
        max) warn "Legacy 'max' — using 'maximus'.";    LAB_TIER="maximus"    ;;
        med) warn "'med' retired in the two-tier blueprint — rolling forward to 'maximus'."; LAB_TIER="maximus" ;;
        *)   warn "Unknown tier '$LAB_TIER' — falling back to minimalist."; LAB_TIER="minimalist" ;;
    esac
    # Resolve which AirLLM deep model applies for this tier (opt-in via
    # ARAIL_INSTALL_AIRLLM=1). Defaults are __TODO_DEEP_MODEL__ — operators
    # set AIRLLM_MODEL in .env to a concrete id when they are ready.
    # The 70B/405B Llama defaults are deprecated (wrong weight class for
    # 36 GB Apple Silicon; see pyproject [tool.arail.models] airllm_* comment).
    case "$LAB_TIER" in
        maximus) AIRLLM_MODEL_ID="${AIRLLM_MODEL_MAX_ID:-__TODO_DEEP_MODEL__}" ;;
        *)       AIRLLM_MODEL_ID="${AIRLLM_MODEL_MIN_ID:-__TODO_DEEP_MODEL__}"  ;;
    esac
    if [[ "${ARAIL_INSTALL_AIRLLM:-0}" == "1" ]]; then
        info "AirLLM deep model for ${LAB_TIER}: ${BOLD}${AIRLLM_MODEL_ID}${RESET}"
    fi

    # AeroLLM model selection (deep mode on Apple Silicon, and on CUDA
    # once the aerollm CUDA backend ships). Operators can override via
    # AEROLLM_MODEL in .env after setup.
    case "$LAB_TIER" in
        maximus) AEROLLM_MODEL_ID="${AEROLLM_MODEL_MAX_ID:-mlx-community/Qwen2.5-7B-Instruct-4bit}" ;;
        *)       AEROLLM_MODEL_ID="${AEROLLM_MODEL_MIN_ID:-mlx-community/Qwen2.5-7B-Instruct-4bit}"  ;;
    esac

    # RAM detection (both tiers — used below for the MoE upgrade decision
    # and, on maximus, for the frontier-model headroom hint).
    local ram_bytes=0
    if [[ "$(uname -s)" == "Darwin" ]]; then
        ram_bytes="$(sysctl -n hw.memsize 2>/dev/null || echo 0)"
    elif [[ -f /proc/meminfo ]]; then
        # MemTotal is in kB; convert to bytes
        local ram_kb
        ram_kb="$(awk '/^MemTotal:/{print $2}' /proc/meminfo 2>/dev/null || echo 0)"
        ram_bytes=$(( ram_kb * 1024 ))
    fi
    # 16 GB = 17179869184 bytes.
    local _ram_16gb_floor=17179869184

    info "AeroLLM deep model for ${LAB_TIER}: ${BOLD}${AEROLLM_MODEL_ID}${RESET}"

    # RAM-headroom hint for operators who manually upgrade AEROLLM_MODEL in .env
    # to a frontier (32B+) checkpoint. With the 7B default the warning
    # is informational, not a footgun — it fits comfortably on 16 GB+.
    if [[ "$LAB_TIER" == "maximus" ]]; then
        # F7: 16 GB floor for maximus. 7B-4bit is ~4 GB resident; combined
        # with portal + browser, 8 GB cannot reliably run maximus without
        # swapping.
        if [[ "$ram_bytes" -gt 0 && "$ram_bytes" -lt "$_ram_16gb_floor" ]]; then
            warn "This machine has < 16 GB RAM. maximus (7B-4bit ~4 GB resident)"
            warn "may cause swapping or slowness on this hardware."
            warn "The minimalist tier (llama-ai-eng, ~1 GB) runs fine on 8 GB."
            warn "Staying on minimalist is recommended: ./arailctl upgrade minimalist"
            warn "Or use a cloud Compute Source in hybrid mode (LAB_MODE=hybrid)."
        # 48 GB threshold — informational for frontier-model users
        elif [[ "$ram_bytes" -gt 0 && "$ram_bytes" -lt 51539607552 ]]; then
            info "System RAM < 48 GB detected. Default deep model fits easily."
            info "Upgrading AEROLLM_MODEL to a 70B+ model on this box may OOM —"
            info "stay at 32B or below, or use the default, for safety."
        fi
    fi
}

# -----------------------------------------------------------------------------
# Network mode — airgapped (default) vs hybrid.
#
# Contract (mirrors capture_tier):
#   - Existing .env LAB_MODE wins (anything ≠ hybrid folds to airgapped,
#     matching arail.airgap.lab_mode()'s fail-closed collapse)
#   - Non-TTY / ARAIL_NONINTERACTIVE=1 → LAB_MODE="${ARAIL_MODE:-airgapped}"
#     (ARAIL_MODE is the existing legacy alias — no third name)
#   - Interactive → educate, then prompt with airgapped as the default
# -----------------------------------------------------------------------------
capture_mode() {
    if [[ -f .env ]] && grep -q '^LAB_MODE=' .env; then
        local existing
        existing="$(grep -E '^LAB_MODE=' .env | head -n1 | cut -d= -f2- | tr -d '"')"
        if [[ "$existing" == "hybrid" ]]; then
            LAB_MODE="hybrid"
        else
            LAB_MODE="airgapped"
        fi
        info "Network mode: ${BOLD}${LAB_MODE}${RESET} (from .env)"
        return
    fi

    if [[ ! -t 0 ]] || [[ "${ARAIL_NONINTERACTIVE:-0}" == "1" ]]; then
        LAB_MODE="${ARAIL_MODE:-airgapped}"
        [[ "$LAB_MODE" == "hybrid" ]] || LAB_MODE="airgapped"
        info "Non-interactive — network mode: ${LAB_MODE}"
        return
    fi

    step "4c/11  Choose your network mode"
    cat <<EOF
  How should your lab touch the network? Flip anytime later from the
  shield badge in the dashboard nav, or by editing LAB_MODE in .env.

    ${BOLD}airgapped${RESET}  (recommended, default) Agents never fetch from the
               public internet. Research runs on material YOU provide —
               drop papers, notes, and docs into the Knowledge Base and
               the lab studies those. The fundamental domain terms come
               from Worlds that ship inside ARAIL itself: sealed,
               pre-curated term bundles vendored into this repo. No
               extra downloads, no second install.
    ${BOLD}hybrid${RESET}     Cloud model providers become reachable and agents
               may fetch from the web — per-domain, with your consent,
               and every outbound call lands in the egress audit log.
EOF
    echo ""
    local choice
    read -rp "  Mode [airgapped]: " choice
    LAB_MODE="${choice:-airgapped}"
    case "$LAB_MODE" in
        airgapped|hybrid) info "Network mode: ${BOLD}${LAB_MODE}${RESET}" ;;
        *) warn "Unknown mode '$LAB_MODE' — falling back to airgapped."; LAB_MODE="airgapped" ;;
    esac
}

# -----------------------------------------------------------------------------
# Unified passphrase — one secret for IDE + Open Notebook + future auth.
#
# Contract:
#   - Interactive TTY + no existing passphrase → silent prompt w/ confirm
#   - Interactive TTY + existing passphrase     → ask "keep or rotate"
#   - Non-TTY / ARAIL_NONINTERACTIVE=1          → auto-generate, warn loudly
#   - Empty final value                         → hard-fail (caller aborts)
#
# The generated token and the final ARAIL_PASSWORD are echoed in the
# end-of-setup banner so users never have to grep .env to find it.
# -----------------------------------------------------------------------------
capture_password() {
    local existing=""
    if [[ -f .env ]]; then
        existing="$(grep -E '^ARAIL_PASSWORD=' .env | head -n1 | cut -d= -f2-)"
        # Guard against placeholders that aren't a real password.
        case "$existing" in
            change-me|__needs_setup__) existing="" ;;
        esac
    fi

    # Defer-to-browser path — operator explicitly opted out of CLI prompt.
    # Setup writes a placeholder; the portal middleware redirects the
    # first browser hit to /welcome where the user picks a passphrase.
    if [[ "${ARAIL_DEFER_PASSWORD:-0}" == "1" ]]; then
        ARAIL_PASSWORD="__needs_setup__"
        step "5/11  Lab passphrase (deferred)"
        info "Deferring passphrase to first browser load (ARAIL_DEFER_PASSWORD=1)."
        info "Open ${BOLD}http://127.0.0.1:${PORTAL_PORT:-8080}${RESET} after ./arailctl start —"
        info "the lab will land on /welcome and ask you to set one."
        return
    fi

    # Reuse path — ask the user explicitly instead of silent reuse.
    if [[ -n "$existing" ]]; then
        if [[ ! -t 0 ]] || [[ "${ARAIL_NONINTERACTIVE:-0}" == "1" ]]; then
            ARAIL_PASSWORD="$existing"
            info "Reusing existing passphrase from .env (non-interactive)."
            return
        fi
        step "5/11  Lab passphrase"
        echo "  An existing passphrase is already configured in .env."
        echo "  Press Enter to keep it, or type ${BOLD}new${RESET} to rotate it."
        echo ""
        local choice
        read -rp "  Keep existing? [Y/new]: " choice || choice=""
        # Convert to lowercase (POSIX-compatible — bash 3.2 on macOS lacks ${var,,})
        choice="$(printf '%s\n' "$choice" | tr '[:upper:]' '[:lower:]')"
        case "$choice" in
            ""|y|yes|keep)
                ARAIL_PASSWORD="$existing"
                info "Keeping existing passphrase."
                return
                ;;
            *)
                info "Rotating passphrase — you'll set a new one now."
                ;;
        esac
    fi

    local generated
    generated="$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_urlsafe(18))')"

    # Non-interactive path — auto-generate, but warn loudly so the line
    # survives terminal scrollback. The final banner echoes the value.
    # (Set ARAIL_DEFER_PASSWORD=1 instead if you want browser-side setup.)
    if [[ ! -t 0 ]] || [[ "${ARAIL_NONINTERACTIVE:-0}" == "1" ]]; then
        ARAIL_PASSWORD="$generated"
        warn "Non-interactive shell — passphrase auto-generated."
        warn "The final setup banner will print the value — do not miss it."
        warn "(Want the browser to ask instead? Re-run with ARAIL_DEFER_PASSWORD=1.)"
        return
    fi

    step "5/11  Lab passphrase"
    echo "  One passphrase secures every surface in the lab:"
    echo "    • code-server IDE   (http://127.0.0.1:8443)"
    echo "    • Open Notebook     (encrypts your research data at rest)"
    echo "    • any future auth   (portal, wiki, agents)"
    echo ""
    echo "  Press Enter to accept a generated value, or type your own."
    echo "  ${BOLD}Input is hidden.${RESET} You will be asked to confirm."
    echo ""
    local typed="" confirm=""
    while true; do
        read -rsp "  Passphrase [generated]: " typed; echo
        if [[ -z "$typed" ]]; then
            ARAIL_PASSWORD="$generated"
            info "Using generated passphrase."
            return
        fi
        read -rsp "  Confirm passphrase      : " confirm; echo
        if [[ "$typed" == "$confirm" ]]; then
            ARAIL_PASSWORD="$typed"
            info "Passphrase set."
            return
        fi
        warn "Passphrases did not match — try again (or press Enter to accept generated)."
    done
}

# -----------------------------------------------------------------------------
# .env file
# -----------------------------------------------------------------------------
setup_env() {
    step "6/11  Configuration files (.env + lab.conf)"
    if [[ ! -f .env ]]; then
        cp .env.example .env
        # Patch detected backend. Match any existing value (the .env.example
        # default has changed before — `auto`, `cpu`, `mlx` — so don't bind
        # the regex to a specific source value).
        # Bind the everyday model to what setup ACTUALLY installs. On
        # minimalist that's llama-ai-eng (an Ollama model, auto-pulled above);
        # the ~5 GB MLX/CUDA/CPU Qwen3-8B is a maximus-only asset that
        # download_model() deliberately does NOT fetch on minimalist. Writing
        # MODEL_BACKEND=mlx + MODEL_NAME=Qwen3-8B here would point a fresh
        # minimalist lab at a model that was never downloaded — the chat tab
        # opens onto a broken default that contradicts setup's own
        # "your everyday model is llama-ai-eng" message. So: tier-gate it.
        local _env_tier="${LAB_TIER:-minimalist}"
        [[ "$_env_tier" == "min" ]] && _env_tier="minimalist"
        [[ "$_env_tier" == "max" || "$_env_tier" == "med" ]] && _env_tier="maximus"
        if [[ "$_env_tier" != "maximus" ]]; then
            # Minimalist: everyday model is the installed Ollama persona.
            sed -i.bak 's|^MODEL_BACKEND=.*|MODEL_BACKEND=ollama_native|' .env
            sed -i.bak 's|^MODEL_NAME=.*|MODEL_NAME=llama-ai-eng|' .env
        else
            # Maximus: the accel-native Qwen3-8B streaming model, which
            # download_model() fetches for this tier.
            case "$ACCEL" in
                mlx)  sed -i.bak 's|^MODEL_BACKEND=.*|MODEL_BACKEND=mlx|'  .env ;;
                cuda) sed -i.bak 's|^MODEL_BACKEND=.*|MODEL_BACKEND=cuda|' .env ;;
                cpu)  sed -i.bak 's|^MODEL_BACKEND=.*|MODEL_BACKEND=cpu|'  .env ;;
            esac
            case "$ACCEL" in
                mlx)  sed -i.bak "s|^MODEL_NAME=.*|MODEL_NAME=${MODEL_MLX_ID}|" .env ;;
                cuda) sed -i.bak "s|^MODEL_NAME=.*|MODEL_NAME=${MODEL_HF_ID}|" .env ;;
                cpu)  sed -i.bak "s|^MODEL_NAME=.*|MODEL_NAME=${MODEL_GGUF_ID}|" .env ;;
            esac
        fi

        sed -i.bak "s|^AIRLLM_MODEL=.*|AIRLLM_MODEL=${AIRLLM_MODEL_ID}|" .env
        sed -i.bak "s|^AEROLLM_MODEL=.*|AEROLLM_MODEL=${AEROLLM_MODEL_ID}|" .env

        # AeroLLM weights-present guard. AEROLLM_MODEL_ID is a HF repo path
        # like "mlx-community/Qwen2.5-7B-Instruct-4bit"; AeroLLMBackend
        # resolves it as the basename under lab/models/. If those weights
        # are not on disk, the deep box will RuntimeError on first warm.
        # Surface a concrete download command up-front so the operator
        # never opens the chat tab to a silently broken Box B.
        local _aero_basename="${AEROLLM_MODEL_ID##*/}"
        local _aero_dir="lab/models/${_aero_basename}"
        if [[ ! -d "$_aero_dir" ]]; then
            warn "AeroLLM weights missing at ${_aero_dir}."
            warn "The deep (2nd inference) box won't load until you run:"
            warn "  hf download ${AEROLLM_MODEL_ID} --local-dir ${_aero_dir}"
            warn "Or downgrade AEROLLM_MODEL in .env to a model whose weights you have."
        else
            info "AeroLLM weights present at ${_aero_dir} ✓"
        fi

        # AeroLLM KV budget — fraction of system RAM the runtime may
        # claim for KV cache. 0.60 leaves ~40% for portal + browser; the
        # aerollm runtime auto-detects 80% if this is unset, which is too
        # aggressive for a lab box also running a UI. Operators dial up
        # via AEROLLM_KV_BUDGET_PCT in .env (or unset to fall back to
        # aerollm's auto-detect).
        if grep -q '^AEROLLM_KV_BUDGET_PCT=' .env; then
            sed -i.bak "s|^AEROLLM_KV_BUDGET_PCT=.*|AEROLLM_KV_BUDGET_PCT=${AEROLLM_KV_BUDGET_PCT}|" .env
        else
            printf '\n# AeroLLM KV-cache budget as a fraction of system RAM. 0.60 by\n# default; raise carefully — exceeding ~0.85 risks portal evictions.\nAEROLLM_KV_BUDGET_PCT=%s\n' "$AEROLLM_KV_BUDGET_PCT" >> .env
        fi

        rm -f .env.bak
        info ".env created with MODEL_BACKEND=${ACCEL}"
    else
        info ".env already exists — preserving model settings."
    fi

    # Passphrase + add-on keys: always ensure they match ARAIL_PASSWORD.
    # Idempotent — safe to re-run on an existing .env.
    if [[ -z "$ARAIL_PASSWORD" ]]; then
        error "Passphrase capture failed — ARAIL_PASSWORD is empty. Re-run: ./arailctl setup"
    fi
    _set_env_var ARAIL_PASSWORD "$ARAIL_PASSWORD"
    _set_env_var OPEN_NOTEBOOK_ENCRYPTION_KEY "$ARAIL_PASSWORD"
    info "Passphrase written to .env (ARAIL_PASSWORD + OPEN_NOTEBOOK_ENCRYPTION_KEY)"

    # Persist brand fields so every subsequent run reads the user's choice.
    if [[ -n "${LAB_NAME:-}" ]]; then
        _set_env_var LAB_NAME "$LAB_NAME"
        _set_env_var LAB_SHORT_NAME "${LAB_SHORT_NAME:-$(_slugify "$LAB_NAME")}"
        _set_env_var LAB_TAGLINE "${LAB_TAGLINE:-A learn-by-doing AI research lab}"
    fi

    # Persist install tier — the portal reads this to gate the nav.
    if [[ -n "${LAB_TIER:-}" ]]; then
        _set_env_var LAB_TIER "$LAB_TIER"
    fi

    # Persist network mode — chosen in capture_mode (airgapped by default).
    if [[ -n "${LAB_MODE:-}" ]]; then
        _set_env_var LAB_MODE "$LAB_MODE"
    fi
}

# Set KEY=VALUE in .env, replacing (or uncommenting) any existing entry.
# Uses a python helper so arbitrary characters in VALUE don't break sed.
#
# IMPORTANT: ``./arailctl start`` sources .env via
# ``set -a && source .env && set +a``, so values containing whitespace
# or shell-special characters MUST be quoted. Without quoting,
# ``LAB_NAME=Autoresearch AI Lab`` is parsed by bash as
# ``LAB_NAME=Autoresearch`` followed by the commands ``AI`` and ``Lab``
# — both error with "command not found" on every start. The python
# helper below quotes any value that needs it.
_set_env_var() {
    local key="$1" value="$2" file="${3:-.env}"
    python3 - "$key" "$value" "$file" <<'PY'
import pathlib, re, sys
key, value, file = sys.argv[1], sys.argv[2], sys.argv[3]
prefix = f"{key}="

# Quote any value containing characters bash would reinterpret when
# the file is sourced.
#
# QA-9 (sprints/2026-07-28-concurrent-worlds/TEST_REPORT.md, A32.5):
# double-quote escaping (\$, \`) is a BASH-only convention — python-dotenv
# does not recognise those two escapes (its double-quote decode table is
# \\ \' \" \a \b \f \n \r \t \v only) and leaves the backslash in place,
# so bash and dotenv disagreed for any value containing $ or a backtick.
# Single quotes are fully literal in BOTH readers for those two
# characters (bash never expands inside '...'; dotenv's single-quote
# decode table is \\ and \' only), so prefer single quotes whenever the
# value itself contains no single quote — the two mechanisms can then
# agree with ZERO escaping instead of a bash-specific one.
# (python-dotenv unconditionally interpolates a literal ${NAME} substring
# on read — regardless of quote style or escaping, there is no escape
# hatch for it in this library — so a value containing that exact brace
# syntax could diverge, and worse, expand to a real environment secret.
# QA-18 closes this below: shell_safe() rejects the "${" adjacency before
# it ever reaches quoting, so no pack value can trigger it.)
_NEEDS_QUOTE = re.compile(r"[\s\"'$`\\#&|;<>(){}*?!~\[\]]")

# QA-18 (sprints/2026-07-28-concurrent-worlds/TEST_REPORT.md, hardening
# micro-pass): python-dotenv's interpolation regex (variables.py's
# ``\$\{...\}``) fires on ANY value containing a literal "${" substring,
# for EITHER quote style, with no escape hatch — it runs on the
# already-decoded text with no awareness of what quoted or escaped it.
# Verified: even the pre-existing ``\$``-escaping in the double-quote
# branch below does not help, because dotenv's double-quote escape table
# (``\\ \' \" \a \b \f \n \r \t \v``) does not include ``\$``, so the
# backslash survives literally in the decoded text and the "$" it
# precedes is still immediately followed by "{" — the regex only cares
# about that adjacency, not what comes before the "$". And bash's
# single-quote path performs ZERO character transformation between the
# quotes, so nothing at the quoting layer can neutralise dotenv's read
# without identically changing bash's read — they parse the same bytes.
# A World display_name (or any other pack value) has no legitimate need
# to contain "${" (ARCHITECTURE.md never specifies brace-interpolation
# syntax for any pack field), so reject the adjacency at the source
# instead of trying to out-escape a library with no escape mechanism for
# it: a literal "${" can never survive into the pack.
def _reject_brace_interpolation(v: str) -> str:
    return v.replace("${", "$")


def shell_safe(v: str) -> str:
    v = _reject_brace_interpolation(v)
    if v == "":
        return '""'
    if not _NEEDS_QUOTE.search(v):
        return v
    if "'" not in v:
        return "'" + v + "'"
    # Contains a literal single quote — single-quoting can't represent it
    # without the '\'' dance, so fall back to the double-quote scheme
    # (pre-existing behaviour; the QA-9 divergence is latent here too, but
    # this combination — a $ / backtick AND a literal single quote in the
    # same value — is outside QA-9's reported repro set).
    escaped = v.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
    return f'"{escaped}"'

# A real assignment is either "KEY=..." or a single-#-prefixed
# commented-out default like "#KEY=...". Anything with whitespace
# between the # and the key (e.g. "#   KEY=example") is
# documentation — leave those lines alone.
def is_assignment(line: str) -> bool:
    if line.startswith(prefix):
        return True
    if line.startswith("#") and not line.startswith("# "):
        return line.lstrip("#").startswith(prefix)
    return False

quoted = shell_safe(value)
new_line = f"{key}={quoted}"

p = pathlib.Path(file)
lines = p.read_text().splitlines() if p.exists() else []
out, replaced = [], False
for line in lines:
    if not replaced and is_assignment(line):
        out.append(new_line)
        replaced = True
    else:
        out.append(line)
if not replaced:
    if out and out[-1] != "":
        out.append("")
    out.append(new_line)
p.write_text("\n".join(out) + "\n")
PY
}

# Read KEY from an env-style file (default .env), reversing the double-quote
# escaping _set_env_var applies, so a round-trip (read here → write via
# _set_env_var) is faithful for passwords with whitespace/metacharacters.
# Prints the unquoted value (empty if absent).
_get_env_var() {
    local key="$1" file="${2:-.env}"
    python3 - "$key" "$file" <<'PY'
import pathlib, sys
key, file = sys.argv[1], sys.argv[2]
prefix = f"{key}="
val = ""
p = pathlib.Path(file)
if p.exists():
    for line in p.read_text().splitlines():
        if line.startswith(prefix):
            v = line[len(prefix):]
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                body = v[1:-1]
                if v[0] == "'":
                    val = body
                else:
                    out, i = [], 0
                    while i < len(body):
                        c = body[i]
                        if c == "\\" and i + 1 < len(body) and body[i + 1] in '\\"$`':
                            out.append(body[i + 1]); i += 2
                        else:
                            out.append(c); i += 1
                    val = "".join(out)
            else:
                val = v
sys.stdout.write(val)
PY
}

# -----------------------------------------------------------------------------
# Runtime config files
# -----------------------------------------------------------------------------
setup_runtime_files() {
    # Resolved ports come from resolve_ports() (called during
    # detect_platform). Fall back to documented defaults so a direct
    # invocation of this function in isolation still works.
    local portal_port="${PORTAL_PORT:-8080}"
    local terminal_port="${TERMINAL_PORT:-7681}"
    local notebook_port="${NOTEBOOK_PORT:-8888}"
    local ide_port="${IDE_PORT:-8443}"
    local mlx_port="${MLX_OPENAI_PORT:-11435}"

    cat > lab.conf << CONF
# Arail runtime config — regenerated by ./arailctl setup on every run.
# Ports were chosen automatically (the next free port from each default).
# To pin a different value, edit it here AND restart the lab; setup will
# preserve your choice on the next run unless that port is also taken.
PORTAL_PORT=${portal_port}
TERMINAL_PORT=${terminal_port}
NOTEBOOK_PORT=${notebook_port}
IDE_PORT=${ide_port}
MLX_OPENAI_PORT=${mlx_port}
BIND_ADDR=127.0.0.1
CONF
    # IDE_PASSWORD is written through the quoter, NOT the heredoc above:
    # lab.conf is shell-sourced (scripts/start.sh, scripts/status.sh), so a
    # user passphrase with a space / $ / backtick would break `source` or
    # execute. _set_env_var double-quotes and escapes it.
    _set_env_var IDE_PASSWORD "$ARAIL_PASSWORD" lab.conf
    info "lab.conf written (portal=${portal_port}, ide=${ide_port}, notebook=${notebook_port}, terminal=${terminal_port}, mlx=${mlx_port})"

    local cs_dir="${HOME}/.config/code-server"
    local cs_cfg="${cs_dir}/config.yaml"
    mkdir -p "$cs_dir"
    if [[ -f "$cs_cfg" ]]; then
        local prev
        prev="$(grep -E '^password:' "$cs_cfg" | head -n1 | cut -d' ' -f2- || true)"
        if [[ -n "$prev" && "$prev" != "$ARAIL_PASSWORD" ]]; then
            warn "Overwriting existing code-server password in $cs_cfg"
        fi
    fi
    cat > "$cs_cfg" << YAML
bind-addr: 127.0.0.1:${ide_port}
auth: password
password: ${ARAIL_PASSWORD}
cert: false
YAML
    info "code-server config written (binds 127.0.0.1:${ide_port})"

    mkdir -p lab/data/goals lab/data/goals/history lab/data/consent lab/data/experiments lab/models
}

# -----------------------------------------------------------------------------
# Relational store readiness (ARCHITECTURE.md §10 finding 2, PR #181 CI —
# sprints/2026-08-10-arail2-persistence-instantiated)
# -----------------------------------------------------------------------------
# CLAUDE.md documents `./arailctl setup && ./arailctl start` as the first-run
# path; §4.5/§4.6 of the sprint's ARCHITECTURE.md wired ensure_db into
# `start` and `install` but never `setup` — a design omission, not builder
# drift (architect's own ruling). Without this, `doctor` between `setup` and
# the first `start` honestly reports relational_store as not instantiated
# and exits 3, which is the check working correctly on a real gap, not a
# check to be muted.
#
# This is a CALL SITE, not a new mechanism: reuses the exact shim
# install.sh's own `_install_db_ensure` already uses, verbatim — the same
# allowlist classifier, the same atlas.sum ledger verification, the same
# safe-forward-only line (nothing about calling it from `setup` changes what
# may be auto-applied). Sequenced after the deps phase (install_core_deps,
# so `arail.dbspec.ensure` is importable) and after setup_runtime_files (so
# `lab/` exists) — both already true by the time this runs in the main
# sequence below. Driven by resolve_data_dirs(), not a hardcoded root, so
# re-running `setup` on an existing multi-World lab behaves like `install`.
# Local file + SQLite only — no network, no embedder, no Atlas — so the
# airgapped default is unaffected.
#
# MUST NOT FAIL SETUP: warns and continues on any non-ok root, matching
# `install` (degrades, never hard-fails) and `start` (warns, continues) —
# never calls `error()`.
setup_db_ensure() {
    [[ -d "${REPO_ROOT:-$PWD}/.venv" ]] || return 0
    if ! declare -F inst_resolve_data_dirs >/dev/null 2>&1; then
        warn "db: skipped (scripts/lib/instances.sh not available) — run ./arailctl install later"
        return 0
    fi
    local slug data_dir origin line rc
    while IFS=$'\t' read -r slug data_dir origin; do
        [[ -n "$data_dir" ]] || continue
        line="$(cd "${REPO_ROOT:-$PWD}" && source .venv/bin/activate && \
            python -m arail.dbspec.ensure "$data_dir" --apply --quiet-ok 2>&1)"
        rc=$?
        if [[ -n "$line" ]]; then
            if [[ "$rc" == "0" ]]; then
                info "db: ${line}"
            else
                warn "db: ${line}"
            fi
        fi
    done < <(inst_resolve_data_dirs)
    return 0
}

# -----------------------------------------------------------------------------
# Personal knowledge management scaffold
# -----------------------------------------------------------------------------
setup_pkb() {
    step "7/11  Knowledge base scaffold (lab/pkb/)"
    # Honor LAB_PKB / ARAIL_MODELS_DIR overrides — same precedence as
    # arail.config does at runtime — so the tree we create matches the
    # tree the running portal will resolve.
    local pkb_root="${LAB_PKB:-${LAB_PKM:-lab/pkb}}"
    local models_dir="${ARAIL_MODELS_DIR:-lab/models}"
    mkdir -p "$pkb_root"/{inbox,sources/{papers,articles,datasets},agents/{research,experiments,synthesis,recommendations},notes/scratch,compiled/{reports,summaries,exports},inference/{prompts,completions,chains}}
    mkdir -p "$models_dir"
    [[ -f "$pkb_root/sources/bookmarks.md" ]] || cat > "$pkb_root/sources/bookmarks.md" << 'BOOKMARKS'
# Bookmarks
# Save URLs with a one-line description. One per line.
# Format: URL — description
BOOKMARKS
    [[ -f "$pkb_root/notes/journal.md" ]] || cat > "$pkb_root/notes/journal.md" << JOURNAL
# Lab Journal

A running log of what happened, what you learned, and what you're thinking.
Newest entries at the top.

---

## $(date +%Y-%m-%d)

Lab initialized with setup.sh.
JOURNAL
    [[ -f "$pkb_root/notes/ideas.md" ]] || cat > "$pkb_root/notes/ideas.md" << 'IDEAS'
# Ideas

Quick captures. Hunches. What-ifs. No pressure — just write.

- 
IDEAS
    info "PKB ready at $pkb_root"
    info "Drop documents/screenshots here:    $(cd "$pkb_root/inbox" 2>/dev/null && pwd || echo "$pkb_root/inbox")"
    info "Drop downloaded model weights here: $(cd "$models_dir" 2>/dev/null && pwd || echo "$models_dir")"
}

# -----------------------------------------------------------------------------
# Start script
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Download starter model (airgapped prep)
# -----------------------------------------------------------------------------
download_model() {
    step "8/11  AI models (starter model for ${ACCEL})"
    if [[ "${ARAIL_SKIP_MODEL_DOWNLOAD:-0}" == "1" ]]; then
        warn "Skipping model download because ARAIL_SKIP_MODEL_DOWNLOAD=1"
        return
    fi

    local model_dir="lab/models"
    mkdir -p "$model_dir"

    # The ~5 GB Qwen3-8B streaming model is a DEEP/maximus asset, not the
    # everyday minimalist model. Keep minimalist's footprint honest (~0.9 GB =
    # llama-ai-eng only, installed via ollama earlier). On minimalist, print the
    # command to fetch it later instead of silently pulling 5 GB.
    local _tier_norm="${LAB_TIER:-minimalist}"
    [[ "$_tier_norm" == "min" ]] && _tier_norm="minimalist"
    [[ "$_tier_norm" == "max" || "$_tier_norm" == "med" ]] && _tier_norm="maximus"
    if [[ "$_tier_norm" != "maximus" ]]; then
        info "Minimalist tier: your everyday model is llama-ai-eng (~0.9 GB, already set up)."
        info "The larger streaming model is a maximus asset. Fetch it later with:"
        echo "  python3 -c \"from huggingface_hub import snapshot_download; snapshot_download('${MODEL_MLX_ID}', local_dir='lab/models/Qwen3-8B-4bit')\""
        echo "  # or just: ./arailctl upgrade maximus"
        return
    fi

    if [[ "$ACCEL" == "mlx" ]]; then
        local model="$MODEL_MLX_ID"
        if [[ -d "${model_dir}/Qwen3-8B-4bit" ]]; then
            info "Model already downloaded."
        else
            info "Downloading MLX model: $model (this takes a few minutes)…"
            python3 -c "from huggingface_hub import snapshot_download; snapshot_download('${model}', local_dir='${model_dir}/Qwen3-8B-4bit')"
        fi
    elif [[ "$ACCEL" == "cuda" ]]; then
        warn "For CUDA, download a model with:"
        echo "  python3 -c \"from huggingface_hub import snapshot_download; snapshot_download('${MODEL_HF_ID}', local_dir='lab/models/Qwen3-8B')\""
    else
        warn "For CPU, download a GGUF model with:"
        echo "  huggingface-cli download ${MODEL_GGUF_ID} --include 'Q4_K_M*' --local-dir lab/models/Qwen3-8B-GGUF --local-dir-use-symlinks False"
    fi

    echo ""
    warn "Deep-chat model for AirLLM (${LAB_TIER} tier): ${AIRLLM_MODEL_ID}"
    warn "Meta Llama is gated — accept the Hugging Face license first, then authenticate with huggingface-cli login or HF_TOKEN."
    local model_short="${AIRLLM_MODEL_ID##*/}"
    echo "  huggingface-cli download ${AIRLLM_MODEL_ID} --local-dir lab/models/${model_short} --local-dir-use-symlinks False"
    echo "  # then set AIRLLM_MODEL=${AIRLLM_MODEL_ID} in .env and run ./arailctl restart"
}

# -----------------------------------------------------------------------------
# Download curated coder starter model (--with-coder flag, Sprint 2)
# -----------------------------------------------------------------------------
download_coder_model() {
    if [[ "$WITH_CODER" != "1" ]]; then
        return 0
    fi

    step "8b/11  Coder starter model (Qwen2.5-Coder-3B-Instruct, ~2 GB Q4)"

    # Log a notice when on min tier — the model downloads but opencode is max-only.
    if [[ "${LAB_TIER:-min}" != "max" ]]; then
        warn "Tier is '${LAB_TIER:-min}', not 'max'. The Workbench tab (opencode) is max-tier only."
        warn "Downloading the coder model anyway — it will be unused until you run:"
        warn "  ./arail upgrade max"
    fi

    local model_dir="lab/models"
    mkdir -p "$model_dir"

    if [[ "$ACCEL" == "mlx" ]]; then
        local target="${model_dir}/Qwen2.5-Coder-3B-Instruct-4bit"
        if [[ -d "$target" ]]; then
            info "Coder model already downloaded (${target})."
            return 0
        fi
        info "Downloading ${CODER_MLX_ID} → ${target}"
        python3 -c "from huggingface_hub import snapshot_download; snapshot_download('${CODER_MLX_ID}', local_dir='${target}')" \
            || { warn "Coder model download failed — see error above. Continuing without coder model."; return 0; }
    elif [[ "$ACCEL" == "cuda" ]]; then
        local target="${model_dir}/Qwen2.5-Coder-3B-Instruct"
        if [[ -d "$target" ]]; then
            info "Coder model already downloaded (${target})."
            return 0
        fi
        info "Downloading ${CODER_HF_ID} → ${target}"
        python3 -c "from huggingface_hub import snapshot_download; snapshot_download('${CODER_HF_ID}', local_dir='${target}')" \
            || { warn "Coder model download failed. Continuing without coder model."; return 0; }
    else
        # CPU — download Q4_K_M GGUF
        local target="${model_dir}/Qwen2.5-Coder-3B-Instruct-GGUF"
        if [[ -d "$target" ]]; then
            info "Coder model already downloaded (${target})."
            return 0
        fi
        info "Downloading ${CODER_GGUF_ID} (Q4_K_M) → ${target}"
        if command -v huggingface-cli >/dev/null 2>&1; then
            huggingface-cli download "$CODER_GGUF_ID" --include '*Q4_K_M*' \
                --local-dir "$target" --local-dir-use-symlinks False \
                || { warn "Coder model download failed. Continuing without coder model."; return 0; }
        else
            python3 -c "from huggingface_hub import snapshot_download; snapshot_download('${CODER_GGUF_ID}', local_dir='${target}', allow_patterns=['*Q4_K_M*'])" \
                || { warn "Coder model download failed. Continuing without coder model."; return 0; }
        fi
    fi

    info "Coder starter model downloaded. In the lab: Chat tab → pick Qwen2.5-Coder-3B, then start opencode from the Workbench tab."
}

# -----------------------------------------------------------------------------
# Capture intent + goal (interactive; writes bootstrap_goal.json)
# -----------------------------------------------------------------------------
capture_goal() {
    # Skip if non-interactive (CI, Docker, pipe)
    if [[ ! -t 0 ]] || [[ "${ARAIL_NONINTERACTIVE:-0}" == "1" ]]; then
        info "Non-interactive shell — skipping goal capture. Set LAB_INTENT + goal via portal later."
        return
    fi

    local goal_path="lab/data/goals/bootstrap_goal.json"
    if [[ -f "$goal_path" ]]; then
        info "Bootstrap goal already set — skipping. (Delete $goal_path to re-capture.)"
        return
    fi

    step "9/11  Lab intent & first research goal"
    echo "  What kind of lab is this?"
    echo ""
    echo "    1) ai         — AI engineering, models, inference, toolchains"
    echo "    2) ml         — Machine learning, training, datasets, benchmarks"
    echo "    3) farming    — Crop science, soil, regional growing"
    echo "    4) business   — Market research, unit economics, competitive intel"
    echo "    5) education  — Learning science, curriculum, mastery"
    echo "    6) health     — Exercise, nutrition, sleep, wellness protocols"
    echo "    7) culinary   — Cooking technique, flavor chemistry, recipe dev"
    echo "    8) trade      — Skilled trades — woodworking, electrical, plumbing, welding, HVAC"
    echo "    9) other      — Fill in the blank — your own field of study"
    echo ""
    read -rp "  Choice [1-9, default 1]: " choice
    local intent intent_name intent_description=""
    case "${choice:-1}" in
        1|"") intent=ai;        intent_name="AI Engineer" ;;
        2)    intent=ml;        intent_name="ML Researcher" ;;
        3)    intent=farming;   intent_name="Farmer" ;;
        4)    intent=business;  intent_name="Analyst" ;;
        5)    intent=education; intent_name="Educator" ;;
        6)    intent=health;    intent_name="Health Researcher" ;;
        7)    intent=culinary;  intent_name="Culinary Scientist" ;;
        8)    intent=trade;     intent_name="Tradesperson" ;;
        9)    intent=other;     intent_name="" ;;
        *)    intent=ai;        intent_name="AI Engineer" ;;
    esac

    # "other" — capture a free-form label and optional one-line description.
    # The label becomes intent_name (used in the dashboard header); the
    # description becomes intent_description (used to compose the
    # researcher's system prompt). Strip control chars and cap lengths.
    if [[ "$intent" == "other" ]]; then
        echo ""
        echo "  Tell us about your lab — one short label, then an optional"
        echo "  one-line focus statement."
        echo ""
        local raw_label raw_desc
        read -rp "  What's your field?  (e.g., \"Beekeeping\", \"Astronomy\", \"Cabinetmaking\"): " raw_label
        read -rp "  One line about what your lab focuses on (optional, press Enter to skip): " raw_desc
        # Strip control chars; collapse whitespace; cap lengths.
        intent_name="$(printf '%s' "$raw_label" | tr -d '\000-\037' | awk '{$1=$1; print}' | cut -c1-40)"
        intent_description="$(printf '%s' "$raw_desc" | tr -d '\000-\037' | awk '{$1=$1; print}' | cut -c1-200)"
        if [[ -z "$intent_name" ]]; then
            intent_name="Researcher"
            info "No label given — defaulting to \"Researcher\"."
        fi
    fi

    echo ""
    echo -e "  ${BOLD}─── Research goal ───${RESET}"
    echo ""

    # For the AI Engineer intent we ship a signature goal —
    # "optimize AirLLM tokens/sec" — pre-filled as the default. Press
    # Enter to accept it; otherwise type a custom goal. Other intents
    # still get the free-form prompt.
    local default_goal=""
    if [[ "$intent" == "ai" ]]; then
        # An inviting first goal that the Researcher can ACTUALLY measure on a
        # fresh laptop (the model_throughput archetype), not an expert AirLLM
        # sweep about a backend that isn't installed.
        default_goal="Find the best small model for my laptop — measure the speed and responsiveness of the model(s) I have installed."
        echo "  Press Enter to accept a friendly starter goal —"
        echo "  ${BOLD}Find the best small model for my laptop${RESET} (the lab"
        echo "  measures real speed/responsiveness of what you have), or type a custom one."
    else
        echo "  What do you want the lab to research? One sentence is fine."
        echo "  Examples:"
        echo "    • Find the best 8B model for code generation on a 32 GB Mac"
        echo "    • Grow peanuts in USDA zone 7 with minimal irrigation"
        echo "    • Master French pastry lamination technique"
    fi
    echo ""
    read -rp "  Goal${default_goal:+ [Enter for default]}: " goal
    if [[ -z "${goal// }" && -n "$default_goal" ]]; then
        goal="$default_goal"
        info "Using the starter goal (find the best small model for your laptop)."
    elif [[ -z "${goal// }" ]]; then
        warn "Empty goal — skipping capture. You can set one from the dashboard after ./arailctl start."
        return
    fi

    echo ""
    echo -e "  ${BOLD}─── Work windows ───${RESET}"
    echo ""
    echo "  The lab scheduler has two modes:"
    echo "    ☀  active — light work only, lab stays responsive"
    echo "    🌙  heavy — experiments + deep GPU work (while you're away)"
    echo ""
    echo "  Press enter to accept the default active window, or type a"
    echo "  range like ${BOLD}08:00-22:00${RESET} (24-hour time, local timezone)."
    echo ""
    read -rp "  Active hours [08:00-22:00]: " active_hours
    read -rp "  Heavy  hours [22:00-08:00]: " heavy_hours
    active_hours="${active_hours:-08:00-22:00}"
    heavy_hours="${heavy_hours:-22:00-08:00}"

    mkdir -p "$(dirname "$goal_path")"
    python3 - "$goal_path" "$goal" "$intent" "$intent_name" "$intent_description" <<'PY'
import json, sys, datetime
path, goal, intent, intent_name, intent_description = sys.argv[1:6]
payload = {
    "goal": goal,
    "intent": intent,
    "intent_name": intent_name,
    "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
}
if intent_description:
    payload["intent_description"] = intent_description
with open(path, "w") as f:
    json.dump(payload, f, indent=2)
PY

    # Persist intent + work windows to .env so the researcher honors them
    # on every run. Uses the file-scope _set_env_var (Python-backed,
    # handles commented forms and arbitrary values correctly).
    if [[ -f .env ]]; then
        _set_env_var LAB_INTENT "${intent}"
        _set_env_var LAB_INTENT_NAME "${intent_name}"
        if [[ -n "$intent_description" ]]; then
            _set_env_var LAB_INTENT_DESCRIPTION "${intent_description}"
        fi
        _set_env_var LAB_ACTIVE_HOURS "${active_hours}"
        _set_env_var LAB_HEAVY_HOURS "${heavy_hours}"
    fi

    info "Goal saved → $goal_path"
    info "Researcher will auto-start when you run ${BOLD}./arailctl start${RESET}"
}

# -----------------------------------------------------------------------------
# validate_env — sanity-check the .env we just wrote. Catches the exact
# failure mode the user reported: a stale .env missing ARAIL_PASSWORD,
# or a divergent IDE_PASSWORD in lab.conf. Called from main() after
# setup_env + setup_runtime_files.
# -----------------------------------------------------------------------------
validate_env() {
    local missing=()
    local required=(MODEL_BACKEND ARAIL_PASSWORD OPEN_NOTEBOOK_ENCRYPTION_KEY LAB_NAME)
    for key in "${required[@]}"; do
        if ! grep -q "^${key}=" .env 2>/dev/null; then
            missing+=("$key")
        fi
    done
    if (( ${#missing[@]} > 0 )); then
        error "Missing required keys in .env: ${missing[*]}. Re-run: ./arailctl setup"
    fi

    # Detect passphrase drift between .env and lab.conf — the current
    # user's case (IDE_PASSWORD=Austin34$, OPEN_NOTEBOOK_ENCRYPTION_KEY=Auatin34$).
    local env_pw conf_pw
    # Read through the quote-aware reader so a quoted password (one with
    # whitespace/metacharacters) compares and re-syncs faithfully.
    env_pw="$(_get_env_var ARAIL_PASSWORD .env)"
    if [[ -f lab.conf ]]; then
        conf_pw="$(_get_env_var IDE_PASSWORD lab.conf)"
        if [[ -n "$env_pw" && -n "$conf_pw" && "$env_pw" != "$conf_pw" ]]; then
            warn "Passphrase drift detected between .env and lab.conf — resyncing."
            _set_env_var IDE_PASSWORD "$env_pw" lab.conf
        fi
    fi
    info "Environment validated."
}

# -----------------------------------------------------------------------------
# install_path_shim — drop arail + qkz into ~/.local/bin so the user can
# run `arail start` (and `qkz <cmd>`) from any directory, not just from
# the repo root with `./arailctl`.
#
# Strategy:
#   • Symlink REPO/arail   → ~/.local/bin/arail
#   • Symlink REPO/qkz     → ~/.local/bin/qkz
#   • If ~/.local/bin isn't on PATH, append `export PATH=...` to the
#     user's shell rc (~/.zshrc or ~/.bashrc).
#   • Refuse to clobber non-symlink files at the targets — surface a
#     warning instead so manually-installed tools aren't lost.
#   • Skippable with ARAIL_SKIP_PATH=1 for users who manage PATH
#     themselves (Nix profiles, Home Manager, dotfiles repos, etc.).
#
# Resolved values land in PATH_INSTALLED (banner prints them) and
# SHELL_RC_TOUCHED (warns the user to source it).
# -----------------------------------------------------------------------------
PATH_INSTALLED=""
SHELL_RC_TOUCHED=""
QKZ_SKIPPED=""
install_path_shim() {
    step "10/11  Install 'arailctl' to your PATH"
    if [[ "${ARAIL_SKIP_PATH:-0}" == "1" ]]; then
        info "Skipping PATH install (ARAIL_SKIP_PATH=1)."
        info "Run from the repo with ${BOLD}./arailctl <cmd>${RESET} or symlink manually."
        return
    fi

    # Pick a target bin dir. Prefer something already on PATH so the
    # command works in this very shell — no rc edit, no source step.
    # Order: ~/bin (common on macOS / older dotfiles) → ~/.local/bin
    # (XDG-ish, Linux default) → fall back to ~/.local/bin and append
    # to shell rc.
    local bin_dir=""
    for cand in "$HOME/bin" "$HOME/.local/bin"; do
        if [[ -d "$cand" ]] && [[ ":$PATH:" == *":$cand:"* ]]; then
            bin_dir="$cand"
            break
        fi
    done
    if [[ -z "$bin_dir" ]]; then
        bin_dir="$HOME/.local/bin"
        mkdir -p "$bin_dir"
    fi

    local installed_any=0
    # Install order matters: arailctl first (canonical), then arail and qkz
    # as back-compat symlinks. arail used to be the primary name; we keep
    # it on PATH for muscle memory and any external scripts that already
    # call `arail <cmd>`. The repo-side files: arailctl is the real script;
    # arail and qkz are symlinks to arailctl (see ./arailctl --help).
    for name in arailctl arail qkz; do
        local source="$REPO_ROOT/$name"
        local target="$bin_dir/$name"
        if [[ ! -e "$source" ]]; then
            warn "Source missing: $source — skipping $name shim."
            continue
        fi

        # qkz collision guard — many users (and the QuKaiZen knowledge
        # base) already define a `qkz` shell function or binary.
        # Don't shadow / get shadowed silently. Skip qkz unless the
        # user explicitly opts in via ARAIL_INSTALL_QKZ=1.
        if [[ "$name" == "qkz" ]] && [[ "${ARAIL_INSTALL_QKZ:-0}" != "1" ]]; then
            local existing_qkz=""
            # `command -v` finds binaries, builtins, functions, aliases —
            # but not zsh functions that are only loaded interactively.
            # Probe both ways to keep the false-negative rate low.
            if command -v qkz &>/dev/null; then
                existing_qkz="$(command -v qkz)"
            elif [[ -e "$target" ]] && [[ ! -L "$target" ]]; then
                existing_qkz="$target"
            fi
            if [[ -n "$existing_qkz" ]]; then
                QKZ_SKIPPED="$existing_qkz"
                info "Skipping qkz shim — existing 'qkz' detected (${existing_qkz})."
                info "Set ARAIL_INSTALL_QKZ=1 to override and link this repo's qkz."
                continue
            fi
        fi

        if [[ -L "$target" ]]; then
            local existing
            existing="$(readlink "$target")"
            if [[ "$existing" == "$source" ]]; then
                info "$name → $target (already linked to this repo)"
                installed_any=1
                continue
            fi
            warn "$target → $existing (different repo) — replacing."
            ln -sf "$source" "$target"
            installed_any=1
        elif [[ -e "$target" ]]; then
            warn "$target exists and is not a symlink — leaving it alone."
            warn "Remove it manually if you want this repo's $name on PATH."
            continue
        else
            ln -s "$source" "$target"
            info "Linked $target → $source"
            installed_any=1
        fi
    done

    if [[ "$installed_any" == "1" ]]; then
        PATH_INSTALLED="$bin_dir"
    fi

    # PATH detection — only touch shell rc if the bin dir really isn't reachable.
    if [[ ":$PATH:" == *":$bin_dir:"* ]]; then
        info "$bin_dir is already on PATH — ${BOLD}arailctl${RESET} works in any new shell."
        return
    fi

    local rc_file=""
    case "${SHELL:-}" in
        */zsh)  rc_file="$HOME/.zshrc" ;;
        */bash)
            # Prefer .bashrc on Linux; .bash_profile on macOS (login shells).
            if [[ "$PLATFORM" == "macos" && -f "$HOME/.bash_profile" ]]; then
                rc_file="$HOME/.bash_profile"
            else
                rc_file="$HOME/.bashrc"
            fi
            ;;
        *)
            warn "Unknown shell (\$SHELL=${SHELL:-unset}). Add this to your shell config:"
            warn "    export PATH=\"${bin_dir}:\$PATH\""
            return
            ;;
    esac

    # Don't double-append if the rc already references the chosen bin dir.
    local literal_dir="${bin_dir/#$HOME/\$HOME}"
    if [[ -f "$rc_file" ]] && grep -Fq "$literal_dir" "$rc_file"; then
        info "$rc_file already references ${literal_dir} — leaving it alone."
        warn "If 'arail' isn't found in a fresh shell, the reference may be commented out."
        return
    fi

    {
        printf '\n# Added by Arail setup — makes the arail command runnable from any directory\n'
        printf 'export PATH="%s:$PATH"\n' "$literal_dir"
    } >> "$rc_file"
    SHELL_RC_TOUCHED="$rc_file"
    info "Appended PATH export to ${BOLD}${rc_file}${RESET}"
    warn "Open a new terminal — or run: ${BOLD}source ${rc_file}${RESET} — to use 'arail' from anywhere."
}

# -----------------------------------------------------------------------------
# Verify
# -----------------------------------------------------------------------------
verify() {
    step "11/11  Verification"
    info "Running smoke tests…"
    local log="${REPO_ROOT:-$PWD}/setup.log"
    if python3 -c "from arail.router import ModelRouter; import arail.portal.app; from arail.pkb import scaffold; scaffold(); print('OK')" >>"$log" 2>&1; then
        info "Smoke tests passed."
    else
        warn "Smoke test failed. Last 20 lines of setup.log:"
        tail -n 20 "$log" | sed 's/^/    /' >&2
        error "Inspect setup.log and re-run: ./arailctl setup"
    fi

    # Integrity check of the vendored World bundles (sealed qukaizen-ddac
    # exports committed into lab/worlds/ — the single-repo install includes
    # them; nothing is fetched). A corrupt bundle must not brick setup: the
    # lab runs fine World-less and the picker omits broken bundles.
    info "Verifying shipped World bundles…"
    if python3 -m arail.world_mount verify-shipped >>"$log" 2>&1; then
        info "Shipped Worlds verified (sealed bundles intact)."
    else
        warn "A shipped World bundle failed its seal check. Recent log lines:"
        tail -n 10 "$log" | sed 's/^/    /' >&2
        warn "Restore with: git checkout -- lab/worlds/<slug>  (sealed files must not be hand-edited)."
        warn "The lab still works — the World picker simply omits broken bundles."
    fi
}

# =============================================================================
# Main
# =============================================================================
main() {
    # ── Argument parsing (must be first) ──────────────────────────────────
    # ARCHITECTURE.md §5.1: --yes|-y is sugar for ARAIL_NONINTERACTIVE=1
    # (confirm()/capture_*() above already default-yes off a set variable
    # — this flag only sets it, it does not duplicate their logic);
    # --quiet masks the passphrase in the end-of-setup banner (also via
    # ARAIL_QUIET=1, or automatically whenever stdout is not a tty — see
    # main()'s final block). Unknown flags now exit 2 instead of being
    # silently ignored (previously the trailing `*) ;;` swallowed a typo
    # like `--wtih-coder` with no feedback at all).
    for arg in "$@"; do
        case "$arg" in
            --with-coder)  WITH_CODER=1 ;;
            --no-coder)    WITH_CODER=0 ;;
            --yes|-y)      ARAIL_NONINTERACTIVE=1 ;;
            --quiet)       ARAIL_QUIET=1 ;;
            *)
                echo -e "${RED}[arail]${RESET} unknown flag: $arg" >&2
                echo "  usage: ./arailctl setup [--with-coder|--no-coder] [--yes|-y] [--quiet]" >&2
                exit 2
                ;;
        esac
    done

    echo ""
    echo -e "${BOLD}🧪 Autoresearch AI Lab Setup${RESET}"
    echo ""
    echo "  Local-first AI lab. Pick a name, capture a goal, start researching."
    echo ""
    echo "============================================="
    echo ""

    # Ordering matches the 1/11 → 11/11 banner sequence:
    #   1/11 detect_platform
    #   2/11 install_services     (OS packages — needs brew/apt, no python)
    #   3/11 ensure_python + install_core_deps + load_pyproject_metadata + install_accel_deps
    #   4/11 capture_brand
    #   5/11 capture_password
    #   6/11 setup_env + setup_runtime_files + setup_db_ensure + validate_env
    #   7/11 setup_pkb
    #   8/11 download_model
    #   9/11 capture_goal
    #  10/11 install_path_shim    (puts arail + qkz on PATH so ./ isn't required)
    #  11/11 verify
    detect_platform
    install_services
    ensure_python
    capture_tier
    capture_mode
    install_core_deps
    load_pyproject_metadata
    install_accel_deps
    capture_brand
    capture_password
    setup_env
    setup_runtime_files
    setup_db_ensure
    validate_env
    setup_pkb
    gentoo_notes
    wsl_notes
    download_model
    download_coder_model
    capture_goal
    install_path_shim

    echo ""
    verify

    echo ""
    echo -e "${BOLD}━━━ ✓ Setup complete${RESET}"
    echo ""

    # Pick the start command based on whether the PATH shim landed.
    # If install_path_shim succeeded *and* ~/.local/bin is reachable in
    # the current shell, the user can just type `arail start`.
    # Otherwise they need the in-repo `./arailctl` form.
    local start_cmd="./arailctl start"
    if [[ -n "$PATH_INSTALLED" ]] && [[ ":$PATH:" == *":$PATH_INSTALLED:"* ]]; then
        start_cmd="arail start"
    fi

    echo -e "  Your assistant: ${BOLD}llama-ai-eng${RESET} — an AI engineer built with Llama-3.2-1B (~0.9 GB)."
    echo "  Pick it in the Chat tab; it's also what the lab's agents use by default."
    echo ""
    echo "  Next steps:"
    echo -e "    1) Start the lab:      ${BOLD}${start_cmd}${RESET}"
    echo -e "    2) Open the dashboard: ${BOLD}http://127.0.0.1:${PORTAL_PORT:-8080}${RESET}"
    echo -e "    3) Open ${BOLD}Autoresearch${RESET}, set your goal with ${BOLD}Set Research Goal${RESET}, then press ${BOLD}▶ Run${RESET}"
    echo ""

    if [[ -n "$PATH_INSTALLED" ]]; then
        echo "  Commands installed:"
        echo -e "    ${BOLD}arail${RESET}  →  ${PATH_INSTALLED}/arail   (run from any directory)"
        if [[ -L "${PATH_INSTALLED}/qkz" ]] && [[ "$(readlink "${PATH_INSTALLED}/qkz")" == "${REPO_ROOT}/qkz" ]]; then
            echo -e "    ${BOLD}qkz${RESET}    →  ${PATH_INSTALLED}/qkz     (alias — same script)"
        elif [[ -n "$QKZ_SKIPPED" ]]; then
            echo -e "    ${BOLD}qkz${RESET}    →  not installed (existing qkz at ${QKZ_SKIPPED})"
            echo "             override with: ARAIL_INSTALL_QKZ=1 ./arailctl setup"
        fi
        echo ""
        if [[ -n "$SHELL_RC_TOUCHED" ]]; then
            echo -e "  ${BOLD}One-time:${RESET} open a new terminal — or run:"
            echo -e "        ${BOLD}source ${SHELL_RC_TOUCHED}${RESET}"
            echo "  so the new PATH takes effect in this shell."
            echo ""
        fi
    fi
    if (( ${#PORT_BUMPS[@]} > 0 )); then
        echo -e "  ${BOLD}Heads-up — some default ports were taken, so we picked these:${RESET}"
        echo -e "    Dashboard : http://127.0.0.1:${PORTAL_PORT}"
        echo -e "    IDE       : http://127.0.0.1:${IDE_PORT}"
        echo -e "    Notebook  : http://127.0.0.1:${NOTEBOOK_PORT}"
        echo -e "    Terminal  : http://127.0.0.1:${TERMINAL_PORT}"
        echo -e "    MLX API   : http://127.0.0.1:${MLX_OPENAI_PORT}/v1"
        echo "  Pinned in lab.conf — bookmarks survive future ./arailctl setup runs."
        echo ""
    fi
    if [[ "$ARAIL_PASSWORD" == "__needs_setup__" ]]; then
        echo -e "  ${BOLD}Passphrase deferred to first browser load.${RESET}"
        echo "  Open the dashboard above; the lab will land on /welcome and"
        echo "  ask you to pick one. Saves to .env, lab.conf, and"
        echo "  ~/.config/code-server/config.yaml automatically."
        echo ""
    else
        echo "  Your lab passphrase (unlocks the IDE at :${IDE_PORT:-8443}"
        echo "  and encrypts Open Notebook data):"
        echo ""
        # ARCHITECTURE.md §13 "setup prints the passphrase to stdout" /
        # F24: mask it when --quiet / ARAIL_QUIET=1 / stdout is not a tty
        # (the real case — CI redirects setup's stdout to a file and then
        # runs a separate redaction step; masking at the source makes that
        # belt-and-braces). The masked line still names both files that
        # hold the real value, so it's never unrecoverable.
        if [[ "${ARAIL_QUIET:-0}" == "1" ]] || [[ ! -t 1 ]]; then
            echo -e "        ${BOLD}********${RESET}  (masked — see .env / lab.conf below)"
        else
            echo -e "        ${BOLD}${ARAIL_PASSWORD}${RESET}"
        fi
        echo ""
        echo "  Also saved in:"
        echo "    .env        →  ARAIL_PASSWORD, OPEN_NOTEBOOK_ENCRYPTION_KEY"
        echo "    lab.conf    →  IDE_PASSWORD"
        echo ""
        echo "  Treat it like any password — don't commit .env to git."
        echo "  To rotate later: ./arailctl setup  (answer 'new' when prompted)"
        echo ""
    fi

    echo -e "  Network mode: ${BOLD}${LAB_MODE:-airgapped}${RESET} — flip via the shield badge"
    echo "  in the dashboard nav, or edit LAB_MODE in .env."
    echo ""
}

main "$@"
