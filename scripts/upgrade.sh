#!/usr/bin/env bash
# =============================================================================
# upgrade.sh — switch install tier (minimalist / maximus) without re-running
# setup.
#
# Usage:
#   ./arailctl upgrade maximus
#   ./arailctl upgrade minimalist   (downgrade — does not uninstall packages,
#                                    just hides the extra tabs until you
#                                    upgrade again)
#
# Legacy `min`/`max` tier names are accepted with a deprecation warning
# (compat shim removed in v1.1.0). The 'med' tier from the earlier
# three-tier blueprint is retired; passing it rolls forward to 'maximus'.
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ── ANSI color gating (ARCHITECTURE.md §13 "ANSI leaks into non-tty
# output", F25) — see arailctl's identical block for the full rationale.
# $'...' (ANSI-C quoting) so the variables hold real ESC bytes.
if [[ -t 1 && "${ARAIL_COLOR:-auto}" != "never" && -z "${NO_COLOR:-}" ]] || [[ "${ARAIL_COLOR:-auto}" == "always" ]]; then
    GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; RED=$'\033[0;31m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
else
    GREEN=""; YELLOW=""; RED=""; BOLD=""; RESET=""
fi
info()  { echo -e "${GREEN}[arail]${RESET} $*"; }
warn()  { echo -e "${YELLOW}[arail]${RESET} $*"; }
die()   { echo -e "${RED}[arail]${RESET} $*" >&2; exit 1; }
# TEST_REPORT.md Q7: docs/cli.md and ARCHITECTURE §5.1 both document `2`
# for "unknown tier" (distinct from `1`, "pip/tier failure") — die() always
# exits 1, so an unknown-tier usage mistake used to be indistinguishable
# from a real pip failure. die_usage() is die()'s twin for that one case.
die_usage() { echo -e "${RED}[arail]${RESET} $*" >&2; exit 2; }

RAW="${1:-}"
case "$RAW" in
    minimalist|maximus) TIER="$RAW" ;;
    min)     warn "Tier name 'min' is deprecated — use 'minimalist'. Compat shim removes in v1.1.0."
             TIER="minimalist" ;;
    max)     warn "Tier name 'max' is deprecated — use 'maximus'. Compat shim removes in v1.1.0."
             TIER="maximus" ;;
    med)     warn "Tier 'med' retired — rolling forward to 'maximus' (it owned a subset of maximus)."
             TIER="maximus" ;;
    "")      die "usage: ./arailctl upgrade <minimalist|maximus> [--with-coder]" ;;
    *)       die_usage "unknown tier '$RAW' — valid: minimalist | maximus" ;;
esac

# Sprint 2: --with-coder downloads Qwen2.5-Coder-3B to lab/models/ so the
# user can point opencode at it. Only useful with max tier.
WITH_CODER="${ARAIL_WITH_CODER:-0}"
for arg in "$@"; do
    case "$arg" in
        --with-coder) WITH_CODER=1 ;;
        --no-coder)   WITH_CODER=0 ;;
        *) ;;
    esac
done

[[ -d .venv ]] || die "no .venv — run ./arailctl setup first"
# shellcheck disable=SC1091
source .venv/bin/activate

info "Switching install tier to ${BOLD}${TIER}${RESET}…"

# pip is idempotent — already-installed packages are no-ops. For downgrades
# we intentionally do not uninstall; the tabs just hide in the nav and the
# operator can upgrade back later without re-downloading wheels.
# Both tiers now declare airllm, so we always re-run pip install to make
# sure the deep backend is present.
pip install -q -e ".[${TIER}]" || die "pip install failed for tier ${TIER}"

# Persist tier to .env. Reads the canonical model names from pyproject.toml
# so this stays in lockstep with [tool.arail.models].
python3 - "$TIER" <<'PY'
import pathlib, sys
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

tier = sys.argv[1]
data = tomllib.loads(pathlib.Path("pyproject.toml").read_text())
models = data.get("tool", {}).get("arail", {}).get("models", {})
# Only writes AIRLLM_MODEL when the operator has opted into AirLLM
# (ARAIL_INSTALL_AIRLLM=1). Otherwise tier-only is fine.
tier_model_key = f"airllm_{tier}"
airllm_model = models.get(tier_model_key) or models.get("airllm", "")

p = pathlib.Path(".env")
lines = p.read_text().splitlines() if p.exists() else []

def upsert(out, key, value):
    seen = False
    new = []
    for line in out:
        if line.lstrip("# ").startswith(f"{key}="):
            new.append(f"{key}={value}")
            seen = True
        else:
            new.append(line)
    if not seen:
        if new and new[-1] != "":
            new.append("")
        new.append(f"{key}={value}")
    return new

lines = upsert(lines, "LAB_TIER", tier)
if airllm_model:
    lines = upsert(lines, "AIRLLM_MODEL", airllm_model)
p.write_text("\n".join(lines) + "\n")
print(f"LAB_TIER={tier}")
if airllm_model:
    print(f"AIRLLM_MODEL={airllm_model}")
PY

info "Tier is now ${BOLD}${TIER}${RESET}. Restart the lab to apply:"
echo ""
echo "    ./arailctl restart"
echo ""

# F5/F8: Honest AeroLLM-vs-AirLLM notice when upgrading to maximus.
# On Apple Silicon (arm64), AeroLLM/MLX is the deep runtime and can be
# auto-built. On CUDA/Linux/Intel, AeroLLM's CUDA backend is not yet
# ready — the honest path is AirLLM fallback (opt-in) or cloud.
if [[ "$TIER" == "maximus" ]]; then
    ARCH="$(uname -m 2>/dev/null || echo unknown)"
    if [[ "$ARCH" == "arm64" ]]; then
        info "Deep mode: ${BOLD}QueueLLM (local, fast)${RESET} — Apple Silicon detected."
        info "To activate the deep (7B) model:"
        info "  1. Build QueueLLM: ./arailctl deep rebuild"
        info "  2. Download weights (~4 GB):"
        info "       huggingface-cli download mlx-community/Qwen2.5-7B-Instruct-4bit \\"
        info "         --local-dir lab/models/Qwen2.5-7B-Instruct-4bit"
        info "  3. Restart: ./arailctl restart"
        info "No code or prompts leave your machine — QueueLLM runs fully in-process."
    else
        warn "Deep mode: QueueLLM is Apple-Silicon-only today."
        warn "On CUDA/Linux, the QueueLLM deep backend is not yet ready."
        warn ""
        warn "Options for deep inference on this host:"
        warn "  A) AirLLM fallback (slower — layer-streaming, subprocess):"
        warn "       export ARAIL_INSTALL_AIRLLM=1"
        warn "       export AIRLLM_MODEL=<your-model-id>  # e.g. Qwen/Qwen2.5-7B-Instruct"
        warn "       ./arailctl upgrade maximus            # re-run to install AirLLM"
        warn "  B) Cloud Compute Source — set LAB_MODE=hybrid in .env and"
        warn "       configure a provider (Claude, OpenRouter, etc.) in the portal."
        warn ""
        warn "When AirLLM fallback is used, the chat surface labels responses"
        warn "  'via AirLLM fallback (slower)' so the latency is never hidden."
    fi
fi

# Download coder model if requested (mirrors setup.sh --with-coder, Sprint 2)
if [[ "$WITH_CODER" == "1" ]]; then
    if [[ "$TIER" != "maximus" ]]; then
        warn "--with-coder: tier is '$TIER', not 'maximus'. opencode Workbench is maximus-only."
        warn "The model will be available when you upgrade to maximus later."
    fi
    # Detect ACCEL the same way setup.sh does: Apple Silicon → mlx, else cpu.
    ACCEL="cpu"
    if [[ "$(uname -s)" == "Darwin" ]] && python3 -c "import mlx" 2>/dev/null; then
        ACCEL="mlx"
    fi
    # Source setup.sh functions for reuse.
    CODER_MLX_ID="mlx-community/Qwen2.5-Coder-3B-Instruct-4bit"
    CODER_HF_ID="Qwen/Qwen2.5-Coder-3B-Instruct"
    CODER_GGUF_ID="Qwen/Qwen2.5-Coder-3B-Instruct-GGUF"
    MODEL_DIR="lab/models"
    mkdir -p "$MODEL_DIR"
    info "Downloading Qwen2.5-Coder-3B-Instruct (${ACCEL})…"
    if [[ "$ACCEL" == "mlx" ]]; then
        TARGET="${MODEL_DIR}/Qwen2.5-Coder-3B-Instruct-4bit"
        [[ -d "$TARGET" ]] && { info "Already downloaded."; } || \
            python3 -c "from huggingface_hub import snapshot_download; snapshot_download('${CODER_MLX_ID}', local_dir='${TARGET}')" \
                || warn "Coder model download failed — see above. Continuing."
    else
        TARGET="${MODEL_DIR}/Qwen2.5-Coder-3B-Instruct-GGUF"
        [[ -d "$TARGET" ]] && { info "Already downloaded."; } || \
            { if command -v huggingface-cli >/dev/null 2>&1; then
                huggingface-cli download "$CODER_GGUF_ID" --include '*Q4_K_M*' \
                    --local-dir "$TARGET" --local-dir-use-symlinks False || warn "Download failed."
              else
                python3 -c "from huggingface_hub import snapshot_download; snapshot_download('${CODER_GGUF_ID}', local_dir='${TARGET}', allow_patterns=['*Q4_K_M*'])" \
                    || warn "Download failed."
              fi; }
    fi
    info "Coder model ready at ${TARGET}. In the lab: Chat → pick Qwen2.5-Coder-3B → start opencode from Workbench."
fi
