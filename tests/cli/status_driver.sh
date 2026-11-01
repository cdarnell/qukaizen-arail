#!/usr/bin/env bash
# tests/cli/status_driver.sh — unified status model + schema v2 + verdict
# codes regression driver (sprints/2026-07-29-elite-cli/ARCHITECTURE.md §7,
# §8.1, §12, WP5). Gates: T3, T8, T10-T12, T34, F2, F18, F20.
#
# Drives the REAL scripts/status.sh (and, for T19-style fixtures, the real
# scripts/lib/instances.sh registry helpers) against a throwaway fake repo
# — never a reimplementation of the collector under test.
#
# F26/F27: every port here is randomized >= 18000 and never 8080/8090.
set -uo pipefail

DRIVER_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$DRIVER_DIR/lib.sh"

if [[ -z "$REAL_VENV" ]]; then
    echo "SKIP: no usable .venv found (tried \$ARAIL_TEST_VENV, $CLI_TEST_REPO/.venv, sibling checkout) — cannot import arail.*"
    exit 0
fi

fail() { echo "FAIL: $1" >&2; exit 1; }
pass_count=0
ok_scenario() { pass_count=$((pass_count + 1)); }

# skip_scenario <name> <reason> — a LOUD skip (ARCHITECTURE.md §10 finding
# 1's condition 1): printed to both stdout (visible in a normal run) and the
# final summary line, so a non-Darwin CI run never reports full coverage it
# did not have. Never use this to turn a red assertion green — only for a
# scenario whose fixture cannot even be constructed on this platform because
# the PRODUCT itself is gated at the same boundary (see the two call sites
# below for the specific citation each one rests on).
skip_count=0
skip_reasons=()
skip_scenario() {
    local name="$1" reason="$2"
    skip_count=$((skip_count + 1))
    skip_reasons+=("$name: $reason")
    echo "SKIP: $name — $reason"
}

SAFE_PATH="/usr/bin:/bin:/usr/sbin:/sbin"
WORK="$(cd "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$WORK"' EXIT
FAKE_HOME="$WORK/home"
mkdir -p "$FAKE_HOME"

_fixture() {
    # _fixture <checkout> -> path to a JSON file {"slug":"root","checkout":<checkout>}
    local checkout="$1" f="$WORK/fixture-$$-$RANDOM.json"
    printf '{"slug":"root","checkout":"%s"}' "$checkout" > "$f"
    printf '%s' "$f"
}

_new_scenario() {
    # _new_scenario <name> -> sets FAKE, PORTAL/LANCE/TERMINAL/NOTEBOOK/IDE
    local name="$1"
    FAKE="$WORK/$name"
    make_fake_repo "$FAKE" >/dev/null
    make_fake_venv "$FAKE"
    PORTAL="$(cli_test_random_port)"
    LANCE="$((PORTAL + 1))"
    TERMINAL="$((PORTAL + 2))"
    NOTEBOOK="$((PORTAL + 3))"
    IDE="$((PORTAL + 4))"
    cli_test_assert_port_safe "$PORTAL"
    write_lab_conf "$FAKE" "$PORTAL" "$LANCE" "$TERMINAL" "$NOTEBOOK" "$IDE"
}

_run_status() {
    # _run_status <fake> [args...] -> stdout+stderr on stdout; sets RC.
    # Always prepends <fake>/stubbin — harmless when a scenario never
    # created one (a nonexistent PATH entry is silently skipped during
    # command lookup), but load-bearing for every scenario that DID build
    # a stub `ps` there (cli_test_write_stub_ps_for_slugs): without it,
    # inst_alive()'s `ps -p <pid> -o command=` check falls through to the
    # REAL system ps, which reports "sleep 30" for a fabricated instance —
    # never matching the uvicorn pattern — silently turning every intended
    # "live" fixture into "stale" and making a scenario pass for the wrong
    # reason (found the hard way while building this driver: T8c's
    # "live + stale" scenario was actually testing "stale + stale").
    local fake="$1"; shift
    OUT="$( cd "$fake" && HOME="$FAKE_HOME" PATH="$fake/stubbin:$SAFE_PATH" _timeout 10 bash arailctl status "$@" 2>&1 )"
    RC=$?
}

# ---------------------------------------------------------------------------
# T8a / F20: nothing running at all (fresh repo, empty registry) -> exit 4,
# and the zero-instance path never aborts under set -e (F20's exact class).
# ---------------------------------------------------------------------------
_new_scenario repo8a
_run_status "$FAKE"
[[ "$RC" == "4" ]] || fail "T8a: expected exit 4 (nothing running), got $RC — output:\n$OUT"
echo "$OUT" | grep -qi "not running" || fail "T8a: no 'not running' line — output:\n$OUT"
ok_scenario

_run_status "$FAKE" --json
[[ "$RC" == "4" ]] || fail "F20/T8a(json): expected exit 4, got $RC — output:\n$OUT"
echo "$OUT" | python3 -m json.tool >/dev/null 2>&1 || fail "F20: --json on a zero-instance repo is not valid JSON — output:\n$OUT"
ok_scenario

# ---------------------------------------------------------------------------
# T8b / T10: exactly one LIVE World instance, no root lab -> exit 0, exactly
# one "root lab: not started" line, zero "not running" root-service rows.
#
# BLOCK-5 (REVIEW2.md round 2): cli_test_fabricate_live_instance registers
# a registry record without ever creating lab/instances/ai/data — this
# scenario has ALWAYS had a missing data root; adding the db object made
# that latent fact newly visible and flipped this scenario's exit code
# from 0 to 3 (a live instance's db reads "pending" against a directory
# that doesn't exist, and pending on a live lab degrades per §4.4).
# DELIBERATE RESOLUTION (not the alternative of updating this expectation
# to 3): suppress the derived db object when data_root_missing is true —
# "pending" is the wrong word for a database that can't exist because its
# whole containing directory doesn't, and the missing root is already a
# reported (deliberately non-degrading) fact via data_root_missing/the
# "⚠ data root missing" line. Reporting it a second time as a db-specific
# complaint would be noise pointing at the wrong subsystem. This keeps
# T10's pre-existing, documented exit-0 contract — asserted here
# EXPLICITLY (db suppressed, data_root_missing true) rather than the test
# merely continuing to pass by accident.
# ---------------------------------------------------------------------------
_new_scenario repo10
cli_test_write_stub_ps_for_slugs "$FAKE/stubbin" "ai:${LANCE}"
cli_test_fabricate_live_instance "$FAKE" ai "$LANCE"
pid_ai10="$CLI_TEST_LAST_FABRICATED_PID"
OUT="$( cd "$FAKE" && HOME="$FAKE_HOME" PATH="$FAKE/stubbin:$SAFE_PATH" _timeout 10 bash arailctl status </dev/null 2>&1 )"; RC=$?
kill -0 "$pid_ai10" 2>/dev/null || fail "T10 setup: fabricated instance died before status ran"
[[ "$RC" == "0" ]] || fail "T10: expected exit 0, got $RC — output:\n$OUT"
_not_started_count="$(echo "$OUT" | grep -c "root lab: not started" || true)"
[[ "$_not_started_count" == "1" ]] || fail "T10: expected exactly one 'root lab: not started' line, got $_not_started_count — output:\n$OUT"
echo "$OUT" | grep -qi "not running" && fail "T10: a root-service 'not running' row leaked through — output:\n$OUT"
echo "$OUT" | grep -qi "db.*pending" && fail "T10/BLOCK-5: a derived 'db pending' line leaked through for a missing data root — output:\n$OUT"
OUT_JSON="$( cd "$FAKE" && HOME="$FAKE_HOME" PATH="$FAKE/stubbin:$SAFE_PATH" _timeout 10 bash arailctl status --json </dev/null 2>&1 )"
echo "$OUT_JSON" | python3 -c '
import json, sys
d = json.load(sys.stdin)
row = next(r for r in d["instances"] if r.get("slug") == "ai")
assert row.get("data_root_missing") is True, row
assert row.get("db") is None, ("db must be suppressed, not \"pending\": " + repr(row.get("db")))
' || fail "T10/BLOCK-5: db suppression check failed — output:\n$OUT_JSON"
kill "$pid_ai10" 2>/dev/null || true; wait "$pid_ai10" 2>/dev/null || true
ok_scenario

# ---------------------------------------------------------------------------
# T8c: live instance + a stale record (dead pid, record still on disk) ->
# exit 3.
# ---------------------------------------------------------------------------
_new_scenario repo8c
cli_test_write_stub_ps_for_slugs "$FAKE/stubbin" "live:${LANCE}" "dead:$((LANCE + 10))"
cli_test_fabricate_live_instance "$FAKE" live "$LANCE"
pid_live8c="$CLI_TEST_LAST_FABRICATED_PID"
cli_test_fabricate_live_instance "$FAKE" dead "$((LANCE + 10))"
pid_dead8c="$CLI_TEST_LAST_FABRICATED_PID"
kill "$pid_dead8c" 2>/dev/null || true; wait "$pid_dead8c" 2>/dev/null || true
_run_status "$FAKE"
[[ "$RC" == "3" ]] || fail "T8c: expected exit 3 (live + stale), got $RC — output:\n$OUT"
echo "$OUT" | grep -qi "stale" || fail "T8c: no 'stale' row — output:\n$OUT"
kill "$pid_live8c" 2>/dev/null || true; wait "$pid_live8c" 2>/dev/null || true
ok_scenario

# ---------------------------------------------------------------------------
# T8d / T11 / F2: a real process answers the root portal port from a
# DIFFERENT checkout -> root.state == "foreign", exit 3, an lsof hint in
# the human view, url present (it IS listening) but identity mismatched.
# ---------------------------------------------------------------------------
_new_scenario repo_foreign
_foreign_fixture="$(_fixture "/some/other/checkout")"
cli_test_spawn_stub_portal "$PORTAL" "$_foreign_fixture" 200
pid_foreign="$CLI_TEST_LAST_FABRICATED_PID"
for _ in 1 2 3 4 5 6 7 8 9 10; do
    kill -0 "$pid_foreign" 2>/dev/null || break
    "$REAL_VENV/bin/python" - "$PORTAL" <<'PY' >/dev/null 2>&1 && break
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.connect(("127.0.0.1", int(sys.argv[1])))
    sys.exit(0)
except OSError:
    sys.exit(1)
PY
    sleep 0.2
done
_run_status "$FAKE"
kill "$pid_foreign" 2>/dev/null || true; wait "$pid_foreign" 2>/dev/null || true
[[ "$RC" == "3" ]] || fail "T8d/T11/F2: expected exit 3 (foreign), got $RC — output:\n$OUT"
echo "$OUT" | grep -qi "DIFFERENT checkout" || fail "T11: no 'DIFFERENT checkout' warning — output:\n$OUT"
echo "$OUT" | grep -qi "lsof" || fail "T11: no lsof hint — output:\n$OUT"
ok_scenario

# ---------------------------------------------------------------------------
# F2: the SAME scenario shape, but the root portal answers with OUR OWN
# checkout while status is invoked through a SYMLINKED path — must render
# "up" (0), never "foreign", proving `pwd -P` (not logical `pwd`) is what
# status.sh's own REPO_ROOT resolves to.
# ---------------------------------------------------------------------------
_new_scenario repo_f2
SYMLINK_REPO="$WORK/symlink-to-repo_f2"
ln -s "$FAKE" "$SYMLINK_REPO"
_f2_fixture="$(_fixture "$FAKE")"   # the REAL (physical) checkout path
cli_test_spawn_stub_portal "$PORTAL" "$_f2_fixture" 200
pid_f2="$CLI_TEST_LAST_FABRICATED_PID"
for _ in 1 2 3 4 5 6 7 8 9 10; do
    kill -0 "$pid_f2" 2>/dev/null || break
    "$REAL_VENV/bin/python" - "$PORTAL" <<'PY' >/dev/null 2>&1 && break
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.connect(("127.0.0.1", int(sys.argv[1])))
    sys.exit(0)
except OSError:
    sys.exit(1)
PY
    sleep 0.2
done
OUT="$( cd "$SYMLINK_REPO" && HOME="$FAKE_HOME" PATH="$SAFE_PATH" _timeout 10 bash arailctl status --json 2>&1 )"; RC=$?
kill "$pid_f2" 2>/dev/null || true; wait "$pid_f2" 2>/dev/null || true
root_state_f2="$(echo "$OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["root"]["state"])' 2>/dev/null || echo "PARSE_FAILED")"
# F2's actual claim is narrower than "everything is healthy" — only the
# portal (memory/etc. were never spawned by this scenario) matters: the
# identity check must resolve to OURS (up/degraded), never "foreign". A
# false "foreign" here is exactly F2's bug (logical vs. physical pwd).
case "$root_state_f2" in
    up|degraded) : ;;
    *) fail "F2: expected root.state in {up, degraded} (never 'foreign') through a symlinked checkout, got '$root_state_f2' — output:\n$OUT" ;;
esac
echo "$OUT" | python3 -c '
import json, sys
doc = json.load(sys.stdin)
portal = next(s for s in doc["root"]["services"] if s["name"] == "portal")
got = portal["state"]
assert got == "up", "portal service state should be up, got " + repr(got)
' || fail "F2: portal service state was not '"'"'up'"'"' — output:\n$OUT"
ok_scenario

# ---------------------------------------------------------------------------
# T12: --json schema key set + jq .instances == --json=instances byte-for-
# byte + --json=bogus -> 2 + sizes absent from JSON.
# ---------------------------------------------------------------------------
_new_scenario repo12
cli_test_write_stub_ps_for_slugs "$FAKE/stubbin" "ai:${LANCE}"
cli_test_fabricate_live_instance "$FAKE" ai "$LANCE"
pid_ai12="$CLI_TEST_LAST_FABRICATED_PID"
full_json="$( cd "$FAKE" && HOME="$FAKE_HOME" PATH="$FAKE/stubbin:$SAFE_PATH" _timeout 10 bash arailctl status --json 2>&1 )"
instances_json="$( cd "$FAKE" && HOME="$FAKE_HOME" PATH="$FAKE/stubbin:$SAFE_PATH" _timeout 10 bash arailctl status --json=instances 2>&1 )"
kill "$pid_ai12" 2>/dev/null || true; wait "$pid_ai12" 2>/dev/null || true
python3 -c '
import json, sys
doc = json.loads(sys.argv[1])
required = {"schema","generated_at","checkout","provisioned","lab","bind","probe",
            "warnings","supervision","instances","root","external","verdict"}
missing = required - set(doc.keys())
assert not missing, f"missing top-level keys: {missing}"
assert doc["schema"] == "arail.status/v2"
assert isinstance(doc["instances"], list)
' "$full_json" 2>"$WORK/t12err.txt" || fail "T12: schema key-set check failed: $(cat "$WORK/t12err.txt") — output:\n$full_json"
# sprints/2026-08-10-arail2-persistence-instantiated §4.4/test 27: --json's
# .instances is now db/origin-AUGMENTED (additive-only); --json=instances
# must stay byte-identical to the pre-existing v1 rows — those are no
# longer expected to be equal verbatim. What must still hold: (a)
# --json=instances carries NEITHER "db" nor "origin" on any row (proving
# it is untouched by this sprint), and (b) stripping "db"/"origin"/any
# unregistered-only synthetic rows back out of --json's .instances
# reproduces --json=instances exactly (proving the augmentation is purely
# additive, not a reshuffle).
python3 -c '
import json, sys
full_instances = json.loads(sys.argv[1])["instances"]
bare_instances = json.loads(sys.argv[2])

for row in bare_instances:
    assert "db" not in row, f"--json=instances leaked a db key: {row}"
    assert "origin" not in row, f"--json=instances leaked an origin key: {row}"

stripped = []
for row in full_instances:
    if row.get("state") == "unregistered":
        continue  # synthetic on-disk-unregistered row, only in --json=full
    row = dict(row)
    row.pop("db", None)
    row.pop("origin", None)
    stripped.append(row)

assert stripped == bare_instances, (
    f"stripping db/origin from --json .instances != --json=instances\n"
    f"stripped={stripped}\nbare={bare_instances}")
' "$full_json" "$instances_json" 2>"$WORK/t27err.txt" \
    || fail "T27: $(cat "$WORK/t27err.txt")"
OUT="$( cd "$FAKE" && HOME="$FAKE_HOME" PATH="$SAFE_PATH" _timeout 10 bash arailctl status --json=bogus 2>&1 )"; RC=$?
[[ "$RC" == "2" ]] || fail "T12: --json=bogus expected exit 2, got $RC — output:\n$OUT"
echo "$full_json" | grep -qi "lab/pkb\|Runtime state" && fail "T12: sizes leaked into --json output"
ok_scenario

# ---------------------------------------------------------------------------
# T34: performance — status (default, 3 registered instances, none live)
# completes in < 2s; --no-probe and --json measured separately; --json
# never runs `du` (a stubbed du that would otherwise silently blend into
# the timing budget is asserted NOT invoked, not just "fast enough").
# ---------------------------------------------------------------------------
_new_scenario repo34
cli_test_write_stub_ps_for_slugs "$FAKE/stubbin" "a:${LANCE}" "b:$((LANCE + 10))" "c:$((LANCE + 20))"
for slug in a b c; do
    case "$slug" in
        a) p="$LANCE" ;; b) p="$((LANCE + 10))" ;; c) p="$((LANCE + 20))" ;;
    esac
    cli_test_fabricate_live_instance "$FAKE" "$slug" "$p"
    eval "pid_${slug}34=\$CLI_TEST_LAST_FABRICATED_PID"
    kill "$CLI_TEST_LAST_FABRICATED_PID" 2>/dev/null || true   # stale (not live) — "3 registered, none live"
    wait "$CLI_TEST_LAST_FABRICATED_PID" 2>/dev/null || true
done
DU_MARKER="$WORK/du-was-called"
rm -f "$DU_MARKER"
mkdir -p "$FAKE/stubbin"
cat > "$FAKE/stubbin/du" <<EOF
#!/usr/bin/env bash
touch "$DU_MARKER"
exec /usr/bin/du "\$@"
EOF
chmod +x "$FAKE/stubbin/du"
_t34_start="$("$REAL_VENV/bin/python" -c 'import time; print(time.time())')"
_run_status "$FAKE"
_t34_end="$("$REAL_VENV/bin/python" -c 'import time; print(time.time())')"
[[ "$RC" == "3" ]] || fail "T34: expected exit 3 (3 registered, none live -> all stale), got $RC — output:\n$OUT"
_t34_elapsed="$("$REAL_VENV/bin/python" -c "print(${_t34_end} - ${_t34_start})")"
"$REAL_VENV/bin/python" -c "import sys; sys.exit(0 if ${_t34_elapsed} < 2.0 else 1)" \
    || fail "T34: default status took ${_t34_elapsed}s (>= 2s) with 3 registered instances"
rm -f "$DU_MARKER"
OUT="$( cd "$FAKE" && HOME="$FAKE_HOME" PATH="$FAKE/stubbin:$SAFE_PATH" _timeout 10 bash arailctl status --json 2>&1 )"; RC2=$?
[[ -f "$DU_MARKER" ]] && fail "T34: --json invoked du — it must never walk lab/ for sizes"
ok_scenario

# ---------------------------------------------------------------------------
# T8e / F18a: registry.d exists but is UNREADABLE (chmod 000) -> exit 1,
# and --json still emits VALID JSON (never a bare human error line — F18).
# ---------------------------------------------------------------------------
_new_scenario repo_unreadable
mkdir -p "$FAKE/lab/instances/registry.d"
echo '{"schema":"arail.instance-registry/v1","slug":"x"}' > "$FAKE/lab/instances/registry.d/x.json"
chmod 000 "$FAKE/lab/instances/registry.d"
_run_status "$FAKE" --json
chmod 755 "$FAKE/lab/instances/registry.d"   # restore so cleanup (rm -rf $WORK) can actually delete it
[[ "$RC" == "1" ]] || fail "T8e/F18a: expected exit 1 (unreadable registry.d), got $RC — output:\n$OUT"
echo "$OUT" | python3 -m json.tool >/dev/null 2>&1 || fail "F18a: --json on an unreadable registry.d is not valid JSON — output:\n$OUT"
echo "$OUT" | grep -qi '"code": *1' || fail "T8e: verdict.code is not 1 — output:\n$OUT"
ok_scenario

# ---------------------------------------------------------------------------
# F18b: no .venv at all -> --json still emits valid JSON (provisioned:
# false), never a raw bash crash.
# ---------------------------------------------------------------------------
_new_scenario repo_no_venv
rm -rf "$FAKE/.venv"
OUT="$( cd "$FAKE" && HOME="$FAKE_HOME" PATH="$SAFE_PATH" _timeout 10 bash arailctl status --json 2>&1 )"; RC=$?
echo "$OUT" | python3 -m json.tool >/dev/null 2>&1 || fail "F18b: --json with no .venv is not valid JSON — output:\n$OUT"
echo "$OUT" | grep -q '"provisioned": false' || fail "F18b: provisioned should be false with no .venv — output:\n$OUT"
ok_scenario

# ---------------------------------------------------------------------------
# T3: --probe still detects a checkout/token mismatch on an otherwise-live
# instance (probe_mismatch_checkout populated, verdict degrades to 3) —
# the existing instance-table behavior this WP must not regress.
# ---------------------------------------------------------------------------
_new_scenario repo_probe_mismatch
cli_test_write_stub_ps_for_slugs "$FAKE/stubbin" "mismatch:${LANCE}"
cli_test_fabricate_live_instance "$FAKE" mismatch "$LANCE"
pid_mismatch="$CLI_TEST_LAST_FABRICATED_PID"
_mismatch_fixture="$(_fixture "/a/totally/different/checkout")"
cli_test_spawn_stub_portal "$LANCE" "$_mismatch_fixture" 200
pid_mismatch_server="$CLI_TEST_LAST_FABRICATED_PID"
for _ in 1 2 3 4 5 6 7 8 9 10; do
    kill -0 "$pid_mismatch_server" 2>/dev/null || break
    "$REAL_VENV/bin/python" - "$LANCE" <<'PY' >/dev/null 2>&1 && break
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.connect(("127.0.0.1", int(sys.argv[1])))
    sys.exit(0)
except OSError:
    sys.exit(1)
PY
    sleep 0.2
done
_run_status "$FAKE" --probe --json
kill "$pid_mismatch" 2>/dev/null || true; wait "$pid_mismatch" 2>/dev/null || true
kill "$pid_mismatch_server" 2>/dev/null || true; wait "$pid_mismatch_server" 2>/dev/null || true
[[ "$RC" == "3" ]] || fail "T3/--probe: expected exit 3 (checkout mismatch), got $RC — output:\n$OUT"
echo "$OUT" | grep -q "different/checkout" || fail "T3/--probe: probe_mismatch_checkout not populated — output:\n$OUT"
ok_scenario

# ---------------------------------------------------------------------------
# T3: daemon-active vs. plist-installed-but-inactive — the distinction this
# WP must preserve exactly (status.sh:42's original row). Placed LAST
# (mirrors root_start_driver.sh's/restart_driver.sh's own T17/F9 ordering):
# once the plist file exists in the shared $FAKE_HOME, a later scenario
# using a bare $SAFE_PATH (the real system launchctl) would otherwise see
# whatever this dev machine's REAL launchd actually reports.
#
# ARCHITECTURE.md §10 finding 1 (PR #181 CI): these two scenarios stub
# launchd/plant a LaunchAgents plist, which is meaningless on a platform
# where daemon supervision cannot exist in the first place —
# scripts/lib/instances.sh:464's daemon_active() opens with
# `[[ "$(uname -s)" == "Darwin" ]] || return 1`, and
# scripts/install-daemon.sh:35 refuses non-Darwin outright ("launchd
# supervision is macOS-only"). On Linux the scenario cannot pass and the
# PRODUCT is behaving correctly — gating the test at the same boundary the
# product already documents is legitimate; it would NOT be legitimate to
# gate a test whose failure reflects an actual product gap. This is
# specifically NOT "Linux has no supervision" — Gentoo's OpenRC path
# (scripts/gentoo-bootstrap.sh:182, `rc-update add arail-portal default`)
# genuinely CAN supervise ARAIL, and daemon_active()'s Darwin-only guard
# means `status` currently misreports that lab as "foreground" — a real,
# separate, pre-existing observability gap, filed in sprints/BACKLOG.md,
# not fixed here (out of scope for this sprint).
# ---------------------------------------------------------------------------
if [[ "$(uname -s)" == "Darwin" ]]; then
    _new_scenario repo_daemon_a
    mkdir -p "$FAKE_HOME/Library/LaunchAgents"
    : > "$FAKE_HOME/Library/LaunchAgents/io.arail.portal.plist"
    mkdir -p "$FAKE/stubbin"
    cat > "$FAKE/stubbin/launchctl" <<'EOF'
#!/usr/bin/env bash
case "$1 $2" in
    "list io.arail.portal") printf '{\n\t"PID" = 4242;\n\t"LastExitStatus" = 0;\n};\n'; exit 0 ;;
esac
exit 0
EOF
    chmod +x "$FAKE/stubbin/launchctl"
    OUT="$( cd "$FAKE" && HOME="$FAKE_HOME" PATH="$FAKE/stubbin:$SAFE_PATH" _timeout 10 bash arailctl status --json 2>&1 )"; RC=$?
    echo "$OUT" | grep -q '"mode": "daemon"' || fail "T3/daemon-a: supervision.mode should be 'daemon' — output:\n$OUT"
    echo "$OUT" | grep -q '"pid": 4242' || fail "T3/daemon-a: agents[].pid should be 4242 — output:\n$OUT"
    ok_scenario

    _new_scenario repo_daemon_b
    mkdir -p "$FAKE/stubbin"
    cat > "$FAKE/stubbin/launchctl" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
    chmod +x "$FAKE/stubbin/launchctl"
    OUT="$( cd "$FAKE" && HOME="$FAKE_HOME" PATH="$FAKE/stubbin:$SAFE_PATH" _timeout 10 bash arailctl status --json 2>&1 )"; RC=$?
    echo "$OUT" | grep -q '"mode": "foreground"' || fail "T3/daemon-b: supervision.mode should be 'foreground' — output:\n$OUT"
    echo "$OUT" | grep -q '"plists_installed": true' || fail "T3/daemon-b: plists_installed should be true (installed but inactive) — output:\n$OUT"
    OUT_H="$( cd "$FAKE" && HOME="$FAKE_HOME" PATH="$FAKE/stubbin:$SAFE_PATH" _timeout 10 bash arailctl status 2>&1 )"
    echo "$OUT_H" | grep -qi "installed but inactive" || fail "T3/daemon-b: human view lost the 'installed but inactive' line — output:\n$OUT_H"
    ok_scenario
else
    skip_scenario "T3/daemon-a" "launchd supervision is Darwin-only (scripts/install-daemon.sh:35, scripts/lib/instances.sh:464 daemon_active()) — the product refuses non-Darwin daemon install, so this scenario's fixture cannot represent a reachable state here"
    skip_scenario "T3/daemon-b" "same Darwin-only product gate as T3/daemon-a"
fi

# ---------------------------------------------------------------------------
# T28a (load-bearing): nothing running at all AND arail.db has never been
# created anywhere -> exit 4, NEVER promoted to 3. This is the exact rule
# ARCHITECTURE.md §4.4 draws a hard line on: "a lab that was never started
# is not degraded."
# sprints/2026-08-10-arail2-persistence-instantiated §7 test 28.
# ---------------------------------------------------------------------------
_new_scenario repo28a
cp -R "$CLI_TEST_REPO/spec" "$FAKE/spec"
_run_status "$FAKE" --json
[[ "$RC" == "4" ]] || fail "T28a: expected exit 4 (nothing running, db absent), got $RC — output:\n$OUT"
echo "$OUT" | grep -q '"code": 4' || fail "T28a: verdict.code is not 4 — output:\n$OUT"
[[ -f "$FAKE/lab/data/arail.db" ]] && fail "T28a: status must never CREATE the db it's checking (apply=False contract)"
ok_scenario

# ---------------------------------------------------------------------------
# T28b: root lab live, arail.db never applied (state "pending") -> exit 3,
# reason names root:db:pending. Uses the stub-portal trick (T8d/F2) to get
# root_state == "up" without a real uvicorn.
# ---------------------------------------------------------------------------
_new_scenario repo28b
cp -R "$CLI_TEST_REPO/spec" "$FAKE/spec"
_28b_fixture="$(_fixture "$FAKE")"
cli_test_spawn_stub_portal "$PORTAL" "$_28b_fixture" 200
pid_28b="$CLI_TEST_LAST_FABRICATED_PID"
for _ in 1 2 3 4 5 6 7 8 9 10; do
    kill -0 "$pid_28b" 2>/dev/null || break
    "$REAL_VENV/bin/python" - "$PORTAL" <<'PY' >/dev/null 2>&1 && break
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.connect(("127.0.0.1", int(sys.argv[1])))
    sys.exit(0)
except OSError:
    sys.exit(1)
PY
    sleep 0.2
done
_run_status "$FAKE" --json
kill "$pid_28b" 2>/dev/null || true; wait "$pid_28b" 2>/dev/null || true
[[ "$RC" == "3" ]] || fail "T28b: expected exit 3 (root up, db pending), got $RC — output:\n$OUT"
echo "$OUT" | grep -q 'root:db:pending' || fail "T28b: verdict.reasons missing root:db:pending — output:\n$OUT"
echo "$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["root"]["db"]["state"]=="pending", d["root"]["db"]' \
    || fail "T28b: root.db.state is not pending — output:\n$OUT"
[[ -f "$FAKE/lab/data/arail.db" ]] && fail "T28b: status must never CREATE the db it's checking"
ok_scenario

# ---------------------------------------------------------------------------
# T28c/T29: root lab live AND its db already applied (ensure_db ran with
# apply=True out-of-band, simulating a prior `install`/`start`) -> exit 0,
# root.db.state == "ok", root.origin == "root" present in the schema.
# ---------------------------------------------------------------------------
_new_scenario repo28c
cp -R "$CLI_TEST_REPO/spec" "$FAKE/spec"
( cd "$FAKE" && "$REAL_VENV/bin/python" -m arail.dbspec.ensure "$FAKE/lab/data" --apply --spec-dir "$FAKE/spec" >/dev/null 2>&1 ) \
    || fail "T28c setup: ensure_db --apply failed to pre-create the db"
_28c_fixture="$(_fixture "$FAKE")"
cli_test_spawn_stub_portal "$PORTAL" "$_28c_fixture" 200
pid_28c="$CLI_TEST_LAST_FABRICATED_PID"
for _ in 1 2 3 4 5 6 7 8 9 10; do
    kill -0 "$pid_28c" 2>/dev/null || break
    "$REAL_VENV/bin/python" - "$PORTAL" <<'PY' >/dev/null 2>&1 && break
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.connect(("127.0.0.1", int(sys.argv[1])))
    sys.exit(0)
except OSError:
    sys.exit(1)
PY
    sleep 0.2
done
_run_status "$FAKE" --json
kill "$pid_28c" 2>/dev/null || true; wait "$pid_28c" 2>/dev/null || true
# REVIEW.md BLOCK-3 (fix required alongside it): this fixture's stub
# portal never spawns memory/MLX/etc, so root_services legitimately
# reports those as down and root_state degrades for THAT reason —
# unrelated to the DB claim this scenario exists to test. Asserting
# RC == 0 here was wrong (it can never pass); the actual claim is
# narrower: the db subsystem itself reports healthy and contributes NO
# db: reason to the verdict, regardless of what else on this fixture is
# degraded for unrelated reasons.
if echo "$OUT" | grep -qi 'root:db:\|instance:.*:db:\|db:collector-failed'; then
    fail "T28c: verdict.reasons contains a db: reason on a healthy db — output:\n$OUT"
fi
echo "$OUT" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d["root"]["db"]["state"] == "ok", d["root"]["db"]
assert d["root"]["origin"] == "root", d["root"].get("origin")
' || fail "T29: root.db.state/origin schema check failed — output:\n$OUT"
ok_scenario

# BLOCK-3 note: "kill the collector deliberately" (rename/break
# ensure.py, assert status is loud) is explicitly QA's assignment per
# REVIEW.md's "What QA should hammer" — NOT added here. make_fake_venv's
# ".venv/lib" is a symlink straight into the REAL venv's site-packages;
# a scenario that edits/renames anything under it would mutate the
# operator's actual installation, which the coordinator's constraints
# forbid, and no safe equivalent (a malformed registry record, etc.) was
# available to build and verify without a real .venv in this pass — see
# BUILD_LOG.md's round-2 section. The required fix itself (status.sh no
# longer swallows a collector failure) was verified functionally by
# feeding the extracted python doc-builder DB_COLLECTOR_FAILED=1 with
# real env vars and confirming exit 3 (live) / exit 4-never-promoted
# (nothing running), matching the architect's own reproduction exactly.

if [[ "$skip_count" -gt 0 ]]; then
    echo "SKIPPED: ${skip_count} scenario(s) not run on this platform ($(uname -s)):"
    for r in "${skip_reasons[@]}"; do
        echo "  - $r"
    done
fi
echo "OK: ${pass_count} scenario(s) passed, ${skip_count} skipped — unified status + schema v2 + verdict codes (T3, T8, T10-T12, T27-T29, T34, F2, F18, F20)"
