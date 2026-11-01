#!/usr/bin/env bash
# tests/cli/reset_full_models_driver.sh — regression driver for
# `./arailctl reset full`'s models handling.
#
# `full_wipe()` used to unconditionally `rm -rf` lab/models/ alongside
# everything else — the one step in a "full wipe" with a real cost to
# undo (often several GB, slow to re-download), and the only one an
# operator is unlikely to expect gone just because they asked for "full."
# Models are now kept unless explicitly requested: `--include-models` on
# the command line, or a "yes" at a dedicated interactive prompt. A bare
# `--yes` (which already confirms the wipe as a whole) does NOT imply
# "and delete my models too."
#
# Drives the REAL scripts/reset.sh against a throwaway fake repo — same
# pattern as the other tests/cli/*_driver.sh scripts.
set -uo pipefail

DRIVER_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$DRIVER_DIR/lib.sh"

fail() { echo "FAIL: $1" >&2; exit 1; }
pass_count=0
ok_scenario() { pass_count=$((pass_count + 1)); echo "  ok: $1"; }

WORK="$(cd "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$WORK"' EXIT

# _new_scenario <name> -> a fake repo with a dummy 1MB "model" file, some
# data/pkb content, and no real services running (stop_services is a
# no-op — nothing to stop).
_new_scenario() {
    local name="$1"
    local fake="$WORK/$name"
    make_fake_repo "$fake" >/dev/null
    mkdir -p "$fake/lab/models/some-model" "$fake/lab/data" "$fake/lab/pkb"
    head -c 1048576 /dev/zero > "$fake/lab/models/some-model/weights.bin"
    echo '{"id":"g1"}' > "$fake/lab/data/marker.json"
    echo "$fake"
}

echo "reset_full_models_driver: starting"

# ---------------------------------------------------------------------------
# S1: `reset full --yes` (no --include-models) — models survive.
# ---------------------------------------------------------------------------
fake="$(_new_scenario s1)"
( cd "$fake" && _timeout 15 bash scripts/reset.sh full --yes >/tmp/s1.out 2>&1 )
[[ -f "$fake/lab/models/some-model/weights.bin" ]] \
    || fail "S1: --yes alone must NOT wipe models — output:\n$(cat /tmp/s1.out)"
[[ -f "$fake/lab/data/marker.json" ]] \
    && fail "S1: data must still be wiped by a full reset — output:\n$(cat /tmp/s1.out)"
grep -qi "Keeping.*models" /tmp/s1.out \
    || fail "S1: must say out loud that models were kept — output:\n$(cat /tmp/s1.out)"
ok_scenario "S1: reset full --yes keeps models, still wipes data"

# ---------------------------------------------------------------------------
# S2: `reset full --yes --include-models` — models ARE removed.
# ---------------------------------------------------------------------------
fake="$(_new_scenario s2)"
( cd "$fake" && _timeout 15 bash scripts/reset.sh full --yes --include-models >/tmp/s2.out 2>&1 )
[[ -d "$fake/lab/models/some-model" ]] \
    && fail "S2: --include-models must remove lab/models/ — output:\n$(cat /tmp/s2.out)"
ok_scenario "S2: reset full --yes --include-models removes models"

# ---------------------------------------------------------------------------
# S3: interactive full wipe, answering 'n' at the models prompt (and 'y'
#     to confirm_and_run's own "Confirm reset FULL WIPE?" ahead of it) —
#     models survive, matching the non-interactive default.
# ---------------------------------------------------------------------------
fake="$(_new_scenario s3)"
# bash's `read -p` only writes its prompt when stdin is a real terminal
# (per the bash manual) — piped stdin here still delivers 'y'/'n'
# correctly to each `read`, it just suppresses the prompt TEXT, same as
# it already does for confirm_and_run's own "Confirm reset FULL WIPE?"
# ahead of it. So this checks the functional outcome, not prompt text.
( cd "$fake" && printf 'y\nn\n' | _timeout 15 bash scripts/reset.sh full >/tmp/s3.out 2>&1 )
[[ -f "$fake/lab/models/some-model/weights.bin" ]] \
    || fail "S3: answering 'n' to the models prompt must keep them — output:\n$(cat /tmp/s3.out)"
[[ -f "$fake/lab/data/marker.json" ]] \
    && fail "S3: data must still be wiped — output:\n$(cat /tmp/s3.out)"
ok_scenario "S3: interactive full wipe defaults models to No"

# ---------------------------------------------------------------------------
# S4: interactive full wipe, answering 'y' to BOTH prompts — models ARE
#     removed. Proves the prompt is a real, honored opt-in, not a no-op.
# ---------------------------------------------------------------------------
fake="$(_new_scenario s4)"
( cd "$fake" && printf 'y\ny\n' | _timeout 15 bash scripts/reset.sh full >/tmp/s4.out 2>&1 )
[[ -d "$fake/lab/models/some-model" ]] \
    && fail "S4: answering 'y' to the models prompt must remove them — output:\n$(cat /tmp/s4.out)"
ok_scenario "S4: interactive full wipe removes models when explicitly confirmed"

# ---------------------------------------------------------------------------
# S5: `reset models` (the standalone mode) is untouched by this change —
#     it must still unconditionally wipe models, --yes or not.
# ---------------------------------------------------------------------------
fake="$(_new_scenario s5)"
( cd "$fake" && _timeout 15 bash scripts/reset.sh models --yes >/tmp/s5.out 2>&1 )
[[ -d "$fake/lab/models/some-model" ]] \
    && fail "S5: standalone 'reset models' must still remove models — output:\n$(cat /tmp/s5.out)"
ok_scenario "S5: standalone 'reset models' mode is unaffected"

echo "OK: reset_full_models_driver — $pass_count scenarios passed"
