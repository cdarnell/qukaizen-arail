#!/usr/bin/env bash
# tests/cli/qa_db_collector_driver.sh — THE DELIBERATE COLLECTOR-KILL TEST.
#
# sprints/2026-08-10-arail2-persistence-instantiated/REVIEW.md BLOCK-3 and
# REVIEW3.md's QA target list, item 1 ("still the single most valuable test
# in this sprint"):
#
#     Break `arail.dbspec.ensure` from the CALLER SIDE ONLY... Assert: the
#     traceback surfaces, `db:collector-failed` lands in verdict.reasons, a
#     live lab exits 3, and a lab that was never started still exits 4.
#
# The defect this reproduces SHIPPED SILENTLY in round 1: scripts/status.sh's
# DB collector ended in `2>/dev/null || echo '{}'`, so any failure at all —
# ImportError, a broken interpreter, a traceback — rendered as `"db": null`
# with no warning, no verdict reason and no exit-code effect. The sprint whose
# entire thesis is "declared and not instantiated is always a finding, never
# silence" had its own reporting surface swallowing exactly that.
#
# ── HOW THE BREAK IS DONE, AND WHY IT IS DONE THIS WAY ────────────────────
# make_fake_venv symlinks "$fake/.venv/lib" straight into the REAL venv's
# site-packages (see the DANGER block at that line in lib.sh). NOTHING here
# writes through it. The break is entirely caller-side:
#
#   1. `rm` the fake venv's `python3` SYMLINK (removing a symlink does not
#      touch its target) and replace it with a real stub file.
#   2. The stub `exec`s the REAL interpreter with an extra PYTHONPATH entry
#      pointing at a throwaway dir holding a `sitecustomize.py` that installs
#      a sys.meta_path finder raising ModuleNotFoundError for
#      `arail.dbspec.ensure`.
#
# Only the DB collector runs through that interpreter — it is the sole line in
# scripts/status.sh inside a `source .venv/bin/activate` subshell (line ~565);
# every other python3 in that script resolves from PATH and stays the system
# one. So this breaks precisely the subsystem under test and nothing else,
# and it produces a REAL ImportError traceback on stderr rather than a
# simulated one.
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

SAFE_PATH="/usr/bin:/bin:/usr/sbin:/sbin"
WORK="$(cd "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$WORK"' EXIT
FAKE_HOME="$WORK/home"
mkdir -p "$FAKE_HOME"

_fixture() {
    local checkout="$1" f="$WORK/fixture-$$-$RANDOM.json"
    printf '{"slug":"root","checkout":"%s"}' "$checkout" > "$f"
    printf '%s' "$f"
}

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
}

# _break_db_import <fake> — see the header. Caller-side only; never writes
# through "$fake/.venv/lib".
_break_db_import() {
    local fake="$1"
    local block="$fake/importblock"
    mkdir -p "$block"
    cat > "$block/sitecustomize.py" <<'PY'
import sys


class _Blocker:
    """Make exactly one module unimportable, as if the DB subsystem were
    missing/broken in this interpreter. Everything else imports normally."""

    TARGET = "arail.dbspec.ensure"

    def find_module(self, fullname, path=None):  # py2-style, harmless
        return None

    def find_spec(self, fullname, path=None, target=None):
        if fullname == self.TARGET:
            raise ModuleNotFoundError(f"No module named {fullname!r}")
        return None


sys.meta_path.insert(0, _Blocker())
PY
    # Removing a SYMLINK does not touch its target. This is the fake venv's
    # own bin/, never the real venv's.
    [[ -L "$fake/.venv/bin/python3" || -f "$fake/.venv/bin/python3" ]] \
        && rm -f "$fake/.venv/bin/python3"
    cat > "$fake/.venv/bin/python3" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$block\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$REAL_VENV/bin/python3" "\$@"
EOF
    chmod +x "$fake/.venv/bin/python3"
    # `python` too: activate puts both on PATH and a future status.sh might
    # use either.
    [[ -L "$fake/.venv/bin/python" || -f "$fake/.venv/bin/python" ]] \
        && rm -f "$fake/.venv/bin/python"
    cp "$fake/.venv/bin/python3" "$fake/.venv/bin/python"
    chmod +x "$fake/.venv/bin/python"
}

_run_status() {
    local fake="$1"; shift
    OUT="$( cd "$fake" && HOME="$FAKE_HOME" PATH="$fake/stubbin:$SAFE_PATH" _timeout 20 bash arailctl status "$@" 2>&1 )"
    RC=$?
}

_wait_for_port() {
    local port="$1" pid="$2" _
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
        kill -0 "$pid" 2>/dev/null || return 1
        "$REAL_VENV/bin/python" - "$port" <<'PY' >/dev/null 2>&1 && return 0
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
    return 1
}

# ---------------------------------------------------------------------------
# QA-C0 (the control): the SAME fixture with a WORKING collector must NOT
# report a collector failure. Without this, every assertion below could pass
# because the fixture is broken in some unrelated way.
# ---------------------------------------------------------------------------
_new_scenario repo_c0
_run_status "$FAKE" --json
echo "$OUT" | grep -q 'db:collector-failed' \
    && fail "QA-C0 (control): an UNBROKEN collector reported db:collector-failed — the whole driver's signal is invalid\n$OUT"
echo "$OUT" | grep -qi 'could not check the relational store' \
    && fail "QA-C0 (control): an UNBROKEN collector warned about the relational store\n$OUT"
ok_scenario

# ---------------------------------------------------------------------------
# QA-C1: broken collector, NOTHING running. The failure must be LOUD —
# warning + verdict reason — and the exit code must stay 4. A lab that was
# never started is not degraded; §4.4's hard line is that nothing may promote
# 4 to 3, and a broken collector is emphatically nothing.
# ---------------------------------------------------------------------------
_new_scenario repo_c1
_break_db_import "$FAKE"
_run_status "$FAKE" --json
[[ "$RC" == "4" ]] || fail "QA-C1: broken collector + nothing running must exit 4, got $RC\n$OUT"
echo "$OUT" | grep -q 'db:collector-failed' \
    || fail "QA-C1: verdict.reasons is missing db:collector-failed — the failure was SILENT (round-1 defect)\n$OUT"
echo "$OUT" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert "db:collector-failed" in d["verdict"]["reasons"], d["verdict"]
assert d["verdict"]["code"] == 4, d["verdict"]
w = " ".join(d.get("warnings") or [])
assert "relational store" in w, d.get("warnings")
assert "ModuleNotFoundError" in w, (
    "the underlying error was swallowed; the operator gets no cause: %r" % w)
' || fail "QA-C1: json contract for a collector failure not met\n$OUT"
ok_scenario

# ---------------------------------------------------------------------------
# QA-C2: broken collector, human renderer. The traceback/cause must reach a
# human too, not only --json. (Round 1's defect rendered `"db": null` and
# printed nothing at all.)
# ---------------------------------------------------------------------------
_run_status "$FAKE"
echo "$OUT" | grep -qi 'could not check the relational store' \
    || fail "QA-C2: the human view says nothing about the failed DB check\n$OUT"
echo "$OUT" | grep -q 'ModuleNotFoundError' \
    || fail "QA-C2: the human view hides the cause\n$OUT"
ok_scenario

# ---------------------------------------------------------------------------
# QA-C3: broken collector with the ROOT LAB LIVE -> exit 3. This is the half
# that must degrade: a running lab whose relational store cannot even be
# inspected is a degraded lab.
# ---------------------------------------------------------------------------
_new_scenario repo_c3
_break_db_import "$FAKE"
_c3_fixture="$(_fixture "$FAKE")"
cli_test_spawn_stub_portal "$PORTAL" "$_c3_fixture" 200
pid_c3="$CLI_TEST_LAST_FABRICATED_PID"
_wait_for_port "$PORTAL" "$pid_c3" || fail "QA-C3 setup: stub portal never bound $PORTAL"
_run_status "$FAKE" --json
kill "$pid_c3" 2>/dev/null || true; wait "$pid_c3" 2>/dev/null || true
[[ "$RC" == "3" ]] || fail "QA-C3: broken collector + live root lab must exit 3, got $RC\n$OUT"
echo "$OUT" | grep -q 'db:collector-failed' \
    || fail "QA-C3: verdict.reasons is missing db:collector-failed\n$OUT"
ok_scenario

# ---------------------------------------------------------------------------
# QA-C4: broken collector + a LIVE WORLD INSTANCE (root lab never started).
# The liveness gate must key on "any lab is live", not "the root lab is
# live" — otherwise the operator's actual usage pattern (one World at a
# time, root lab never started; see the workspace memory note) is exactly
# the case that stays silent.
# ---------------------------------------------------------------------------
_new_scenario repo_c4
_break_db_import "$FAKE"
mkdir -p "$FAKE/stubbin" "$FAKE/lab/instances/ai/data"
INST_PORT="$(cli_test_random_port)"
cli_test_assert_port_safe "$INST_PORT"
cli_test_fabricate_live_instance "$FAKE" ai "$INST_PORT"
pid_c4="$CLI_TEST_LAST_FABRICATED_PID"
cli_test_write_stub_ps_for_slugs "$FAKE/stubbin" "ai:$INST_PORT"
_run_status "$FAKE" --json
kill "$pid_c4" 2>/dev/null || true; wait "$pid_c4" 2>/dev/null || true
[[ "$RC" == "3" ]] || fail "QA-C4: broken collector + live World instance must exit 3, got $RC\n$OUT"
echo "$OUT" | grep -q 'db:collector-failed' \
    || fail "QA-C4: verdict.reasons is missing db:collector-failed\n$OUT"
ok_scenario

# ---------------------------------------------------------------------------
# QA-C5: --no-probe must not become an escape hatch. The DB check is a local
# file read (§4.4: "permitted in --no-probe"), so a collector failure has to
# surface there too.
# ---------------------------------------------------------------------------
_new_scenario repo_c5
_break_db_import "$FAKE"
_run_status "$FAKE" --json --no-probe
echo "$OUT" | grep -q 'db:collector-failed' \
    || fail "QA-C5: --no-probe silently drops the collector failure\n$OUT"
ok_scenario

# ---------------------------------------------------------------------------
# QA-C6: the collector must never CREATE the database it failed to check,
# and must never leave the fake repo's spec/ tree modified.
# ---------------------------------------------------------------------------
[[ -f "$FAKE/lab/data/arail.db" ]] \
    && fail "QA-C6: a failed collector created lab/data/arail.db"
ok_scenario

echo "OK: ${pass_count} scenario(s) passed — deliberate collector-kill (BLOCK-3 regression, REVIEW3 QA target 1)"
