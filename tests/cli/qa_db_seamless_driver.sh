#!/usr/bin/env bash
# tests/cli/qa_db_seamless_driver.sh — the seamless promise, driven through
# the REAL scripts/install.sh and scripts/start.sh control flow.
#
# sprints/2026-08-10-arail2-persistence-instantiated/REVIEW3.md QA targets
# 4, 5 and 6:
#
#     Fresh clone -> setup -> start on a scratch LAB_ROOT ... assert the DB
#     exists, status exits 0, and no arail.db appears at lab/ or the repo
#     root.
#     The six-roots case with an empty registry.d — the operator's real
#     measured state. install must create 6 DBs; status must show the
#     unregistered instances as origin=ondisk findings, not skip them.
#     install.sh / start.sh shell control flow, still never run end to end.
#
# Everything runs against a throwaway fake repo under mktemp with an
# isolated HOME and randomized high ports. The operator's real lab is never
# touched, and no scenario writes through make_fake_venv's ".venv/lib"
# symlink into the real venv (see the DANGER block at that line in lib.sh).
set -uo pipefail

DRIVER_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$DRIVER_DIR/lib.sh"

if [[ -z "$REAL_VENV" ]]; then
    echo "SKIP: no usable .venv found (tried \$ARAIL_TEST_VENV, $CLI_TEST_REPO/.venv, sibling checkout) — cannot import arail.*"
    exit 0
fi

FAILED=0
fail() { echo "FAIL: $1" >&2; FAILED=$((FAILED + 1)); }
pass_count=0
ok_scenario() { pass_count=$((pass_count + 1)); }

SAFE_PATH="/usr/bin:/bin:/usr/sbin:/sbin"
WORK="$(cd "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$WORK"' EXIT
FAKE_HOME="$WORK/home"
mkdir -p "$FAKE_HOME"

SLUGS=(ai qukaizen video-games debt-finance finance)

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
    printf 'LAB_MODE=airgapped\nLAB_TIER=minimalist\n' > "$FAKE/.env"
}

# The operator's exact measured machine state (ARCHITECTURE.md Assumption 6):
# registry.d/ literally EMPTY while five instance dirs exist on disk.
_six_roots_fixture() {
    local fake="$1" slug
    mkdir -p "$fake/lab/instances/registry.d"
    for slug in "${SLUGS[@]}"; do
        mkdir -p "$fake/lab/instances/$slug/data" "$fake/lab/instances/$slug/pkb"
    done
}

_run_install() {
    local fake="$1"; shift
    OUT="$( cd "$fake" && HOME="$FAKE_HOME" PATH="$SAFE_PATH" \
        _timeout 90 bash scripts/install.sh "$@" </dev/null 2>&1 )"
    RC=$?
}

# _run_start <fake> <fixture-json> <outvar-file> — run the REAL start.sh
# against the serving stub, capturing output to a FILE rather than a
# command substitution. Command substitution waits for EOF on stdout, and
# start.sh's service children inherit that fd; an orphaned child then hangs
# the driver long past _timeout's own expiry (lib.sh's
# write_stub_uvicorn_serving header documents the same class of hazard).
# A file redirect decouples us from every child's fd lifetime.
_run_start() {
    local fake="$1" fixture="$2" log="$WORK/start-$$-$RANDOM.log"
    printf '{"slug":"root","checkout":"%s"}' "$fake" > "$fixture"
    ( cd "$fake" && HOME="$FAKE_HOME" PATH="$SAFE_PATH" ARAIL_NO_BROWSER=1 \
        STUB_FIXTURE="$fixture" STUB_STATUS=200 \
        _timeout 10 bash scripts/start.sh >"$log" 2>&1 )
    OUT_START="$(cat "$log")"
    # Reap anything the stub left listening on this scenario's ports.
    cli_test_kill_port_listener "$PORTAL" || true
}

_run_status() {
    local fake="$1"; shift
    OUT="$( cd "$fake" && HOME="$FAKE_HOME" PATH="$SAFE_PATH" \
        _timeout 20 bash arailctl status "$@" 2>&1 )"
    RC=$?
}

# ---------------------------------------------------------------------------
# S1: the six-roots case. `install` over an EMPTY registry.d with five
# on-disk instance dirs must create SIX databases — root + five — because
# any inst_list_slugs()-driven walk reaches zero of them.
# ---------------------------------------------------------------------------
_new_scenario repo_s1
_six_roots_fixture "$FAKE"
# The resolver itself, first: if this is not 6, nothing downstream can be.
resolved="$( cd "$FAKE" && REPO_ROOT="$FAKE" bash -c \
    'source scripts/lib/instances.sh; inst_resolve_data_dirs' | wc -l | tr -d ' ' )"
[[ "$resolved" == "6" ]] \
    || fail "S1: inst_resolve_data_dirs returned $resolved rows, expected 6 (root + 5 on-disk)"

_run_install "$FAKE" --only verify
created=0
for slug in "${SLUGS[@]}"; do
    if [[ -f "$FAKE/lab/instances/$slug/data/arail.db" ]]; then
        created=$((created + 1))
    else
        fail "S1: install did not create lab/instances/$slug/data/arail.db (origin=ondisk, empty registry) — the 2-of-6 blind spot"
    fi
done
[[ -f "$FAKE/lab/data/arail.db" ]] \
    || fail "S1: install did not create the root lab's lab/data/arail.db\n$OUT"
[[ "$created" == "5" ]] && ok_scenario

# ---------------------------------------------------------------------------
# S2 (F5): no root-level or shared database ever creeps in. Exactly one
# arail.db per data dir, and none at lab/ or the repo root.
# ---------------------------------------------------------------------------
[[ -f "$FAKE/arail.db" ]] && fail "S2: an arail.db appeared at the repo root"
[[ -f "$FAKE/lab/arail.db" ]] && fail "S2: an arail.db appeared at lab/"
dbcount="$(find "$FAKE" -name arail.db | wc -l | tr -d ' ')"
[[ "$dbcount" == "6" ]] \
    || fail "S2: expected exactly 6 databases, found $dbcount: $(find "$FAKE" -name arail.db)"
# Every one of them must sit in a directory named data/.
while IFS= read -r p; do
    [[ "$(basename "$(dirname "$p")")" == "data" ]] \
        || fail "S2: a database landed outside a data/ dir: $p"
done < <(find "$FAKE" -name arail.db)
ok_scenario

# ---------------------------------------------------------------------------
# S3: status over the same fixture reports the unregistered instances as
# origin=ondisk findings rather than skipping them, and every one of the six
# db objects is healthy after install.
# ---------------------------------------------------------------------------
_run_status "$FAKE" --json
echo "$OUT" | python3 -c '
import json, sys
d = json.load(sys.stdin)
rows = d.get("instances") or []
ondisk = {r["slug"] for r in rows if r.get("origin") == "ondisk"}
expected = {"ai", "qukaizen", "video-games", "debt-finance", "finance"}
assert ondisk == expected, "origin=ondisk rows were %r, expected %r" % (ondisk, expected)
bad = {r["slug"]: r.get("db") for r in rows
       if (r.get("db") or {}).get("state") not in (None, "ok")}
assert not bad, "instances with a non-ok db after install: %r" % (bad,)
assert d["root"]["db"]["state"] == "ok", d["root"]["db"]
' || fail "S3: status did not report the six roots correctly\n$OUT"
ok_scenario

# ---------------------------------------------------------------------------
# S4 (idempotence / quiet): a second install must not re-apply anything and
# must not print a db creation line for an already-healthy root.
# ---------------------------------------------------------------------------
_run_install "$FAKE" --only verify
echo "$OUT" | grep -q '\[db\].*created' \
    && fail "S4: a second install re-reported 'created' on healthy databases\n$OUT"
ok_scenario

# ---------------------------------------------------------------------------
# S5 (F6/F19): status and doctor over the fixture leave the tree
# byte-identical — no file created, no file modified, nothing written into
# the checkout.
# ---------------------------------------------------------------------------
_snapshot_tree() {
    ( cd "$1" && find . -type f ! -path './.venv/*' -exec shasum -a 256 {} \; \
        | LC_ALL=C sort )
}
before_tree="$(_snapshot_tree "$FAKE")"
_run_status "$FAKE" --json
_run_status "$FAKE"
after_tree="$(_snapshot_tree "$FAKE")"
if [[ "$before_tree" != "$after_tree" ]]; then
    fail "S5: status modified the tree:\n$(diff <(echo "$before_tree") <(echo "$after_tree") | head -20)"
fi
ok_scenario

# ---------------------------------------------------------------------------
# S6: fresh clone -> install -> start, on a scratch checkout with NO
# instances at all. The root lab's database must be created by install, and
# `start`'s own readiness step must then be silent about it (quiet boot,
# F10) — while a checkout where install never ran must have start create it
# and SAY SO (a write is never silent).
# ---------------------------------------------------------------------------
_new_scenario repo_s6
write_stub_uvicorn_serving "$FAKE"
[[ -f "$FAKE/lab/data/arail.db" ]] && fail "S6: precondition — db already exists"
_run_start "$FAKE" "$WORK/s6-fixture.json"
[[ -f "$FAKE/lab/data/arail.db" ]] \
    || fail "S6: start did not create the root lab's database\n$OUT_START"
echo "$OUT_START" | grep -q 'db: created' \
    || fail "S6: start created a database and did not report the write\n$OUT_START"
ok_scenario

# ---------------------------------------------------------------------------
# S7 (F10, quiet boot): a SECOND start on the now-healthy lab prints no db:
# line at all.
# ---------------------------------------------------------------------------
_run_start "$FAKE" "$WORK/s6-fixture.json"
OUT_START2="$OUT_START"
echo "$OUT_START2" | grep -q '^\s*db:' \
    && fail "S7: a healthy second start is chatty about the db\n$OUT_START2"
ok_scenario

# ---------------------------------------------------------------------------
# S8 (F4): starting the ROOT lab must not create or touch any sibling
# World instance's database. Per-instance scope is a data-isolation
# property, not a tidiness one.
#
# EXEMPTION (ARCHITECTURE.md §10, fix 1 — CI feedback on PR #181):
# lab/instances/last-target.json is explicitly excluded from this
# comparison. It is genuine, documented, whole-CHECKOUT bookkeeping —
# scripts/lib/instances.sh's inst_write_last_target(), described in this
# repo's own CLAUDE.md as "the picker's memory... records what the
# operator last STARTED successfully" — and it is written on EVERY root
# or World start, by design, predating this sprint entirely. It is not
# per-instance state, even though it happens to live in the same
# directory as per-instance data/secrets. Name it explicitly here, never
# widen this to the whole directory or to "any" change: the property S8
# exists to protect — that starting the root lab does not disturb a
# World INSTANCE's own state — must still be asserted for everything
# else under lab/instances/.
# ---------------------------------------------------------------------------
_new_scenario repo_s8
write_stub_uvicorn_serving "$FAKE"
_six_roots_fixture "$FAKE"
sib_before="$(find "$FAKE/lab/instances" -type f ! -name 'last-target.json' | LC_ALL=C sort)"
_run_start "$FAKE" "$WORK/s8-fixture.json"
OUT_START3="$OUT_START"
sib_after="$(find "$FAKE/lab/instances" -type f ! -name 'last-target.json' | LC_ALL=C sort)"
[[ "$sib_before" == "$sib_after" ]] \
    || fail "S8: a root-lab start touched sibling instance dirs (last-target.json excluded — see comment above):\n$(diff <(echo "$sib_before") <(echo "$sib_after"))"
[[ -f "$FAKE/lab/data/arail.db" ]] || fail "S8: root db was not created\n$OUT_START3"
ok_scenario

# ARCHITECTURE.md §10 Finding 4: portable content+permission+mtime
# snapshot of every secrets.env under a root. `stat -f '%Sp %m'` (the
# form this used to use) is BSD/macOS-only syntax — GNU stat (every
# Linux CI runner) rejects it outright, which means the PERMISSION half
# of S9's assertion (that 0600 survived) was silently vacuous on Linux:
# `find -exec` still produced non-empty output from the shasum half even
# though the stat half errored, so the overall snapshot string was
# never empty and the comparison appeared to pass regardless. The
# asserted property — a sibling's secrets.env is never read, written, or
# copied (CLAUDE.md) — is a SECURITY invariant and platform-independent;
# gating it away (the way T3/daemon-a's launchd scenarios legitimately
# are, because that mechanism truly doesn't exist on Linux) would make a
# security assertion vacuous on the platform most likely to run CI. Uses
# python3's os.stat (identical mode/mtime semantics on every platform
# this harness targets) instead of shelling out to a platform-specific
# `stat` binary at all.
_secrets_snapshot() {
    python3 -c '
import hashlib, os, stat, sys

root = sys.argv[1]
rows = []
for dirpath, _dirnames, filenames in os.walk(root):
    if "secrets.env" not in filenames:
        continue
    p = os.path.join(dirpath, "secrets.env")
    st = os.stat(p)
    digest = hashlib.sha256(open(p, "rb").read()).hexdigest()
    rows.append(
        f"{os.path.relpath(p, root)} {digest} "
        f"{oct(stat.S_IMODE(st.st_mode))} {int(st.st_mtime)}"
    )
rows.sort()
print("\n".join(rows))
' "$1"
}

# ---------------------------------------------------------------------------
# S9 (security, §7 test 34): no code path in this sprint reads, writes or
# creates a secrets.env anywhere. Plant one per instance and assert its
# content and mtime survive install + start + status untouched.
# ---------------------------------------------------------------------------
_new_scenario repo_s9
_six_roots_fixture "$FAKE"
for slug in "${SLUGS[@]}"; do
    printf 'ANTHROPIC_API_KEY=sk-do-not-touch-%s\n' "$slug" \
        > "$FAKE/lab/instances/$slug/data/secrets.env"
    chmod 0600 "$FAKE/lab/instances/$slug/data/secrets.env"
done
printf 'ANTHROPIC_API_KEY=sk-root\n' > "$FAKE/lab/data/secrets.env"
chmod 0600 "$FAKE/lab/data/secrets.env"
sec_before="$(_secrets_snapshot "$FAKE")"
# ARCHITECTURE.md §10 Finding 4's required addition: assert the snapshot
# itself actually produced output BEFORE comparing before/after. The old
# find-exec pipeline could have its stat half fail silently while the
# shasum half still yielded a non-empty string — the exact "this sprint's
# own defect class inside a test" pattern found a third time. A snapshot
# that comes back empty here means the FIXTURE (7 secrets.env files just
# planted above) is broken, not that the property being checked holds.
[[ -n "$sec_before" ]] \
    || fail "S9 setup: secrets snapshot came back empty — expected 7 planted secrets.env files"
_run_install "$FAKE" --only verify
_run_status "$FAKE" --json
sec_after="$(_secrets_snapshot "$FAKE")"
[[ -n "$sec_after" ]] \
    || fail "S9: post-install/status secrets snapshot came back empty — a stat/hash invocation may have failed silently rather than the files genuinely vanishing"
[[ "$sec_before" == "$sec_after" ]] \
    || fail "S9: a secrets.env changed content, permissions or mtime:\n$(diff <(echo "$sec_before") <(echo "$sec_after"))"
echo "$OUT" | grep -qi 'sk-do-not-touch\|sk-root' \
    && fail "S9: a secret value was echoed into status output"
ok_scenario

if [[ "$FAILED" -gt 0 ]]; then
    echo "FAILED: $FAILED check(s) failed, ${pass_count} scenario(s) passed" >&2
    exit 1
fi
echo "OK: ${pass_count} scenario(s) passed — seamless install/start over the six-roots fixture"
