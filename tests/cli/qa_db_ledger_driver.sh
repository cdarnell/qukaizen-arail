#!/usr/bin/env bash
# tests/cli/qa_db_ledger_driver.sh — ledger tampering against a live lab,
# including the ASK-6 topology.
#
# sprints/2026-08-10-arail2-persistence-instantiated/REVIEW3.md, QA target 7:
#
#     Ledger tampering against a live lab: delete/corrupt atlas.sum, tamper a
#     migration, add an unlisted one. Every case must yield `diverged` with
#     the database file never created. Include the ASK-6 topology (missing
#     data root + tampered ledger) so the fix is proven when it lands.
#
# ASK-6 (REVIEW3.md, "suppression is keyed on the wrong thing"): scripts/
# status.sh suppresses a live instance's whole `db` object whenever
# `data_root_missing` is true. That is right for `pending` (a derived,
# false description of a directory that isn't there) and wrong for
# `diverged`, which is a fact about the CHECKOUT — the BLOCK-2 condition,
# "the SQL on disk is not the SQL that was committed" — and has nothing to
# do with the missing directory.
#
# Scenario A2 below is the exact blind spot: a tampered ledger where the only
# live lab is an instance whose data root is missing. It is written to assert
# the CORRECT behaviour and is expected to FAIL until ASK-6 is fixed.
set -uo pipefail

DRIVER_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$DRIVER_DIR/lib.sh"

if [[ -z "$REAL_VENV" ]]; then
    echo "SKIP: no usable .venv found (tried \$ARAIL_TEST_VENV, $CLI_TEST_REPO/.venv, sibling checkout) — cannot import arail.*"
    exit 0
fi

fail() { echo "FAIL: $1" >&2; FAILED=$((FAILED + 1)); }
FAILED=0
pass_count=0
ok_scenario() { pass_count=$((pass_count + 1)); }

SAFE_PATH="/usr/bin:/bin:/usr/sbin:/sbin"
WORK="$(cd "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$WORK"' EXIT
FAKE_HOME="$WORK/home"
mkdir -p "$FAKE_HOME"

_new_scenario() {
    local name="$1"
    FAKE="$WORK/$name"
    make_fake_repo "$FAKE" >/dev/null
    make_fake_venv "$FAKE"
    cp -R "$CLI_TEST_REPO/spec" "$FAKE/spec"
    PORTAL="$(cli_test_random_port)"
    cli_test_assert_port_safe "$PORTAL"
    write_lab_conf "$FAKE" "$PORTAL" "$((PORTAL + 1))" "$((PORTAL + 2))" \
        "$((PORTAL + 3))" "$((PORTAL + 4))"
    mkdir -p "$FAKE/stubbin"
}

# _shadow_checkout <fake> — QA FINDING QA-2, and the reason this helper has
# to exist at all.
#
# scripts/status.sh's collector calls `ensure_db(data_dir, apply=False)` with
# NO spec_dir, so ensure_db falls back to DEFAULT_SPEC_DIR, which ASK-1 pinned
# to the INSTALLED PACKAGE's location (Path(ensure.__file__).parents[3]/spec)
# rather than the checkout being inspected. In production those coincide; in
# this harness they do not, so tampering "$fake/spec" changes NOTHING the
# collector reads. (That silently applies to the sprint's own T28a/T28b/T28c
# scenarios too — they copy spec/ into the fake repo, and the collector then
# reads the real checkout's spec/ instead. They pass, but not for the reason
# they claim to.)
#
# So the tamper has to happen where the collector will actually look: at a
# shadow CHECKOUT — a full copy of src/ + spec/ — placed on PYTHONPATH ahead
# of site-packages via a stub python3 in the fake venv's own bin. Nothing here
# writes through "$fake/.venv/lib" (the symlink into the REAL venv's
# site-packages); the python3 symlink is REMOVED and replaced, which does not
# touch its target.
_shadow_checkout() {
    local fake="$1"
    local shadow="$fake/shadow"
    mkdir -p "$shadow"
    cp -R "$CLI_TEST_REPO/src" "$shadow/src"
    cp -R "$CLI_TEST_REPO/spec" "$shadow/spec"
    rm -f "$fake/.venv/bin/python3" "$fake/.venv/bin/python"
    cat > "$fake/.venv/bin/python3" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$shadow/src\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$REAL_VENV/bin/python3" "\$@"
EOF
    chmod +x "$fake/.venv/bin/python3"
    cp "$fake/.venv/bin/python3" "$fake/.venv/bin/python"
    chmod +x "$fake/.venv/bin/python"
    SHADOW="$shadow"
}

# _tamper_migration <fake> — append a statement to a committed migration
# WITHOUT updating atlas.sum. This is BLOCK-2's exact threat model: SQL on
# disk that nobody committed, about to be auto-executed at boot.
_tamper_migration() {
    local fake="$1" f
    _shadow_checkout "$fake"
    f="$(ls "$SHADOW"/spec/schema/migrations/*.sql | head -n1)"
    printf '\nCREATE TABLE qa_injected (x INTEGER);\n' >> "$f"
}

_run_status() {
    local fake="$1"; shift
    OUT="$( cd "$fake" && HOME="$FAKE_HOME" PATH="$fake/stubbin:$SAFE_PATH" _timeout 20 bash arailctl status "$@" 2>&1 )"
    RC=$?
}

_assert_no_db_created() {
    local fake="$1" where
    where="$(find "$fake" -name 'arail.db' 2>/dev/null)"
    [[ -n "$where" ]] && fail "a diverged ledger still created a database: $where"
}

# ---------------------------------------------------------------------------
# A0 (control): tampered ledger, NOTHING running. Must stay exit 4 (a lab
# that was never started is not degraded) and must never create a db.
# ---------------------------------------------------------------------------
_new_scenario repo_a0
_tamper_migration "$FAKE"
_run_status "$FAKE" --json
[[ "$RC" == "4" ]] || fail "A0: tampered ledger + nothing running should stay exit 4, got $RC\n$OUT"
echo "$OUT" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d["root"]["db"]["state"] == "diverged", d["root"]["db"]
' || fail "A0: root.db.state is not diverged on a tampered ledger\n$OUT"
_assert_no_db_created "$FAKE"
ok_scenario

# ---------------------------------------------------------------------------
# A1 (the reachable case): tampered ledger, live World instance whose data
# root EXISTS -> exit 3 and instance:<slug>:db:diverged in verdict.reasons.
# This is the baseline that proves A2's failure is about the suppression
# rule and not about ledger verification itself.
# ---------------------------------------------------------------------------
_new_scenario repo_a1
_tamper_migration "$FAKE"
INST_PORT="$(cli_test_random_port)"
cli_test_assert_port_safe "$INST_PORT"
mkdir -p "$FAKE/lab/instances/ai/data" "$FAKE/lab/instances/ai/pkb"
cli_test_fabricate_live_instance "$FAKE" ai "$INST_PORT"
pid_a1="$CLI_TEST_LAST_FABRICATED_PID"
cli_test_write_stub_ps_for_slugs "$FAKE/stubbin" "ai:$INST_PORT"
_run_status "$FAKE" --json
kill "$pid_a1" 2>/dev/null || true; wait "$pid_a1" 2>/dev/null || true
[[ "$RC" == "3" ]] || fail "A1: tampered ledger + live instance should exit 3, got $RC\n$OUT"
echo "$OUT" | grep -q 'instance:ai:db:diverged' \
    || fail "A1: verdict.reasons is missing instance:ai:db:diverged\n$OUT"
_assert_no_db_created "$FAKE"
ok_scenario

# ---------------------------------------------------------------------------
# A2 (ASK-6): tampered ledger, live World instance whose data root is
# MISSING. `diverged` describes the checkout, not the absent directory, so
# it must survive the data_root_missing suppression: exit 3, and the fact
# must be reported somewhere an operator will see it.
#
# EXPECTED TO FAIL until ASK-6 is fixed (gate suppression on the db STATE —
# suppress only `pending`/`unavailable`, the states derived from the missing
# directory — rather than on the data_root_missing FLAG).
# ---------------------------------------------------------------------------
_new_scenario repo_a2
_tamper_migration "$FAKE"
INST_PORT2="$(cli_test_random_port)"
cli_test_assert_port_safe "$INST_PORT2"
# Deliberately do NOT create lab/instances/ai/data — that is the topology.
mkdir -p "$FAKE/lab/instances/ai"
cli_test_fabricate_live_instance "$FAKE" ai "$INST_PORT2"
pid_a2="$CLI_TEST_LAST_FABRICATED_PID"
cli_test_write_stub_ps_for_slugs "$FAKE/stubbin" "ai:$INST_PORT2"
_run_status "$FAKE" --json
kill "$pid_a2" 2>/dev/null || true; wait "$pid_a2" 2>/dev/null || true
A2_JSON="$OUT"
echo "$A2_JSON" | python3 -c '
import json, sys
d = json.load(sys.stdin)
reasons = d["verdict"]["reasons"]
assert any("diverged" in r for r in reasons), (
    "ASK-6: verdict.reasons has no diverged entry: %r" % (reasons,))
' || fail "A2/ASK-6: a TAMPERED migration ledger produced NO verdict reason — the only live lab's data root is missing, so the whole db object was suppressed and with it the checkout-global 'diverged' (BLOCK-2's condition)\n$A2_JSON"
[[ "$RC" == "3" ]] \
    || fail "A2/ASK-6: expected exit 3 on a tampered ledger with a live lab, got $RC — an operator running \`./arailctl status\` on a checkout whose committed SQL has been altered is told everything is fine"
# And the human view, which is what an operator actually reads.
_run_status "$FAKE"
echo "$OUT" | grep -qi 'diverged' \
    || fail "A2/ASK-6: the HUMAN status view never mentions the diverged ledger\n$OUT"
_assert_no_db_created "$FAKE"
ok_scenario

# ---------------------------------------------------------------------------
# A3: atlas.sum deleted entirely, live instance with a real data root ->
# diverged, exit 3, no db created. "Cannot verify" must never mean "assume
# it's fine."
# ---------------------------------------------------------------------------
_new_scenario repo_a3
_shadow_checkout "$FAKE"
rm -f "$SHADOW"/spec/schema/migrations/atlas.sum
INST_PORT3="$(cli_test_random_port)"
cli_test_assert_port_safe "$INST_PORT3"
mkdir -p "$FAKE/lab/instances/ai/data"
cli_test_fabricate_live_instance "$FAKE" ai "$INST_PORT3"
pid_a3="$CLI_TEST_LAST_FABRICATED_PID"
cli_test_write_stub_ps_for_slugs "$FAKE/stubbin" "ai:$INST_PORT3"
_run_status "$FAKE" --json
kill "$pid_a3" 2>/dev/null || true; wait "$pid_a3" 2>/dev/null || true
[[ "$RC" == "3" ]] || fail "A3: missing atlas.sum + live instance should exit 3, got $RC\n$OUT"
echo "$OUT" | grep -q ':db:diverged' \
    || fail "A3: a missing atlas.sum did not surface as diverged\n$OUT"
_assert_no_db_created "$FAKE"
ok_scenario

# ---------------------------------------------------------------------------
# A4: an unlisted extra migration dropped into the checkout (a bad merge, or
# an attacker with write access to the repo) -> diverged, no db created.
# ---------------------------------------------------------------------------
_new_scenario repo_a4
_shadow_checkout "$FAKE"
printf 'CREATE TABLE qa_unlisted (x INTEGER);\n' \
    > "$SHADOW/spec/schema/migrations/29990101000000_unlisted.sql"
_run_status "$FAKE" --json
echo "$OUT" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d["root"]["db"]["state"] == "diverged", d["root"]["db"]
' || fail "A4: an unlisted migration file did not make the ledger diverged\n$OUT"
_assert_no_db_created "$FAKE"
ok_scenario

if [[ "$FAILED" -gt 0 ]]; then
    echo "FAILED: $FAILED scenario(s) failed, ${pass_count} passed" >&2
    exit 1
fi
echo "OK: ${pass_count} scenario(s) passed — ledger tampering + ASK-6 topology"
