# Review: ARAIL 2.0 persistence, instantiated

**Date:** 2026-08-10
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) at `829d13f`
**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `4f8ae97`
**Diff reviewed:** `4f8ae97..829d13f`, 12 commits, 22 files, +2744/−18

## Verdict: BLOCK

Three BLOCKs, all in the same place: the boundary that decides what SQL runs
automatically at boot, and whether a failure of that machinery is audible.
Everything else in this sprint is good work — the exit-code contract holds
under live test, `apply=False` is genuinely write-free, the provisioning check
generalizes, and defect B's fix is exactly right. But the sprint's headline
mechanism auto-executes developer-authored SQL on `start`, and its two
safety gates are both breachable, one of them for a reason the builder
believed was unavoidable and is not.

I verified findings by execution, not by reading. Commands and outputs below.

---

## BLOCK-1 — the lossy classifier has four verified executable bypasses

`src/arail/dbspec/ensure.py:130-159`. The module docstring states:

> "It fails closed, never open (test 5)."

That claim is false. `_LOSSY_RE` is a denylist of six patterns. I attacked it
with the forms the coordinator named plus the ones SQLite actually accepts:

```
LOSSY         DROP TABLE worlds;
LOSSY         ALTER TABLE worlds DROP COLUMN slug;
SAFE-FORWARD  ALTER TABLE worlds DROP slug;          <-- data loss
SAFE-FORWARD  UPDATE OR REPLACE worlds SET status=0; <-- rewrites every row
SAFE-FORWARD  REPLACE INTO worlds VALUES (1);        <-- overwrites rows
SAFE-FORWARD  INSERT OR REPLACE INTO worlds VALUES (1);
SAFE-FORWARD  DROP VIEW v1;   /  DROP TRIGGER t1;
```

All four SAFE-FORWARD classifications are **executable, data-destroying
SQLite** — confirmed against sqlite 3.51.0:

```
ALTER TABLE t DROP b   -> accepted, column and its data gone: [(1,)] cols ['a']
REPLACE INTO t         -> (1,2) becomes (1,99)
INSERT OR REPLACE INTO -> (1,99) becomes (1,7)
UPDATE OR REPLACE t SET-> b rewritten to 42 on every row
```

`ALTER TABLE … DROP <col>` (no `COLUMN` keyword) is not exotic — it is the
idiomatic short form and the single most likely destructive migration a
developer writes. `_apply_locked` would apply it during `start`, inside the
"seamless, decides nothing on the operator's behalf" path, on every lab on the
machine.

The failure is structural: a denylist cannot fail closed, because failing
closed means "anything I cannot prove safe is lossy" and a denylist's default
is the opposite. The existing test (`tests/test_dbspec_ensure.py:82-100`) only
feeds it the six forms the regex already matches, so it confirms the regex
matches its own patterns.

**Required:** invert to an allowlist. Classify per statement (after
`_split_statements`), SAFE-FORWARD only if the statement's leading keywords
match an explicit set — `CREATE TABLE`, `CREATE [UNIQUE] INDEX`, `CREATE VIEW`,
`CREATE TRIGGER`, `ALTER TABLE … ADD COLUMN`, `INSERT INTO` (bare, no `OR
REPLACE`) — and LOSSY for **everything else, including anything unrecognized**.
Extend the test table with all seven bypasses above plus at least one
deliberately unparseable statement asserted LOSSY. Correct the docstring only
once the claim is true.

## BLOCK-2 — migrations are executed without ever verifying the ledger, and the stated reason is factually wrong

This is interpretive call (a), which the coordinator asked me to rule on.

`ensure.py:34-51` justifies self-consistency sidecar hashing over `atlas.sum`
parity by asserting Atlas's digest is "undocumented, binary-only" and therefore
out of reach without the `atlas` binary (Assumption 1).

I reproduced `atlas.sum`'s per-file hash in pure Python, in three lines, with
no `atlas` binary:

```
sha256(b"20260808155711_baseline.sql" + file_bytes), base64
  -> yapG1acbpycqWN0YcDjURATANCKembuvV3/KrCUFCLk=
atlas.sum says
  -> yapG1acbpycqWN0YcDjURATANCKembuvV3/KrCUFCLk=      exact match
```

The premise is wrong, so the conclusion drawn from it doesn't hold. That
matters because of what the sidecar structurally cannot do: it records a hash
*the first time this module applies a file*, so on a fresh clone — the exact
scenario this sprint exists to serve — `ensure_db(apply=True)` reads
`spec/schema/migrations/*.sql` and **executes it having verified nothing at
all**. Verified behaviour:

```
delete .arail_ensure_state.json on a diverged DB -> state "ok"   (divergence gone)
tamper a migration file with the sidecar intact  -> state "diverged"  (works)
```

So integrity checking is present only for files this DB has already applied,
and is defeated by deleting one file next to the database. ARAIL is a blueprint
other people clone and run on their own machines (CLAUDE.md); combined with
BLOCK-1, a modified or corrupted migration file yields arbitrary SQL executed
at next `start`, and neither gate stops it.

**Ruling:** the sidecar is sound and worth keeping — it catches post-apply
edits, which `atlas.sum` alone would not. It is **not** sound as the only
gate. **Required:** parse `atlas.sum` (pure Python, no binary) and verify every
migration file's hash *before* executing any of it; mismatch or a file absent
from the ledger ⇒ `state="diverged"`, zero statements executed. Keep the
sidecar as the second, complementary check. This is not a follow-up ticket —
it is the precondition for auto-executing SQL at boot.

## BLOCK-3 — the status DB collector swallows every failure, silently

`scripts/status.sh:546-571`. The collector ends with:

```
' 2>/dev/null || echo '{}')"
```

Any failure — ImportError, traceback, a broken interpreter — becomes `{}`, and
`{}` renders as `"db": null` with no warning, no reason in `verdict.reasons`,
and no effect on the exit code. I reproduced this live, by accident, and it is
the cleanest possible demonstration:

```
$ ARAIL_TEST_VENV=<operator venv> bash tests/cli/status_driver.sh
FAIL: T28b: verdict.reasons missing root:db:pending
  root.db  = None
  verdict  = {'code': 3, 'reasons': ['root:degraded:degraded: memory']}
```

The DB subsystem failed to import **entirely** (that venv resolves `arail` to
the main checkout, which has no `ensure.py`), and `status` said nothing about
it — exiting 3 for an unrelated memory reason. A user in that state sees a
status surface reporting on everything except the subsystem that is completely
dead.

This is this sprint's own thesis, shipped inside the sprint that exists to
eliminate it: *a mechanism gated behind a step that didn't happen, with the
health surface reporting nothing*. `src/arail/provisioning.py:10-12` states the
rule — "on, and nothing has ever performed the step that makes it real is a
*third* state, and it is always reported" — and `status.sh` breaks it three
files away.

**Required:** capture stderr rather than discarding it; on a non-zero collector
exit, emit a `db:collector-failed` warning into `warnings`, add a reason to
`verdict.reasons`, and degrade a live lab to 3. `"db": null` must never be the
representation of "the check crashed" — it should mean only "not evaluated
because this checkout has no `.venv`", and even that deserves a line.

**Also required (same area):** `tests/cli/status_driver.sh`'s T28c asserts
`RC == 0` for a fixture whose stub portal reports degraded memory, so it can
never pass — the DB half of it is correct (`root.db.state == "ok"`,
`root.origin == "root"`), the assertion is wrong. Fix the fixture or assert on
`verdict.reasons` not containing a `db:` reason.

---

## What I verified as correct

I ran the driver with the worktree's own source on `PYTHONPATH`, which is the
first time these scenarios have executed anywhere:

```
$ ARAIL_TEST_VENV=<venv> PYTHONPATH=<worktree>/src bash tests/cli/status_driver.sh
T28a  nothing running + db absent -> exit 4, db never created   PASS
T28b  root live + db pending      -> exit 3, root:db:pending    PASS
T28c                                                            FAIL (test bug, above)
T12/T27  --json=instances carries no db/origin; stripping them
         from --json .instances reproduces it exactly           PASS
```

**Exit-code contract (priority 3): holds.** The `4`→`3` promotion the spec
forbids is correctly prevented by gating the DB contribution on
`root_is_live` / `state == "live"` (`status.sh:692-720`). The coordinator's
concern that this was verified only against an extracted doc-builder was
warranted — but T28a/T28b are genuine end-to-end runs of `bash arailctl
status`, and they pass once the code under test is actually importable. The
`--json=instances` branch was moved *above* the augmentation and prints the
pristine list, which is stronger than the spec asked for.

**`apply=False` is genuinely write-free (priority 2): confirmed**, including
the case the coordinator asked about:

```
ensure_db('/tmp/.../nope/deeper/data', apply=False)
  -> state "pending";  os.path.exists('/tmp/.../nope') == False
ensure_db(<existing empty dir>, apply=False)
  -> directory listing byte-identical before and after: []
```

`_read_user_version_readonly` uses `file:...?mode=ro` (no file creation, no
`-wal`/`-shm`), `_apply_lock`'s `mkdir` is reachable only under `apply=True`,
and `status.sh` calls `apply=False` unconditionally. The previous sprint's bug
is not reintroduced.

**Other verified-correct behaviour:**
- `version` rolled back under the cursor → `blocked` with an honest message,
  no silent re-apply. Forced `user_version=99` → `ahead`. Both fail closed.
- Per-file transaction with `PRAGMA user_version` bumped inside it (F2);
  idempotent second apply returns `ok`/`applied=[]`; `schema_version` recorded
  with the real spec sha256.
- `start` scoping (F4): `_instance_db_ensure` takes exactly one `data_dir`
  and both call sites pass this instance's own. No sibling path, no secrets
  path anywhere in the diff.
- `inst_resolve_data_dirs` is bash-3.2-safe (no `declare -A`), handles the
  empty-registry case, and is CWD-safe because every caller `cd "$REPO_ROOT"`
  first.
- 52 sprint tests pass under `PYTHONPATH=src`; no `lab/` contamination
  (independently re-confirmed — the operator's real lab was byte-identical
  before and after all my runs).

## Priority 5 — the provisioning class check: generalizes, and would have caught all three

`src/arail/provisioning.py` is a real registry (`register()` / `evaluate_all()`
over an arbitrary dict, `finding` as a pure property, `_reset_for_tests`), not
three hardcoded checks. A synthetic fourth mechanism registers and is evaluated
identically. Answering the two questions directly:

- **QA-6?** Yes. `check_kb_gate` returns `declared=True, instantiated=False`
  when the gate is on and `approved_paths()` is empty — required tier, exit 3.
- **Defect A on a fresh clone?** Yes. `check_relational_store` calls
  `ensure_db(apply=False)`; a fresh clone with no DB returns `pending ≠ ok` ⇒
  finding.
- **Defect B?** Yes — `check_vector_backend` would have fired in this very
  worktree, which is where the defect was reproduced.

One thing done well and worth preserving: `check_relational_store` derives
`spec_dir` from the passed `repo_root` instead of relying on CWD. See ASK-1 for
why that matters.

## Priority 6 — defect B's fix: correct

`pkb.py:718-736` sets `"backend"` with a message naming `sys.executable` and
the fixing verb, and clears it only on a later successful `available()`
observation — a successful embed does **not** clear it, preserving BLOCK-1
code-scoped-evidence discipline. `doctor.py:187` promotes `"backend"` into
`required_codes` alongside `dimension`/`provenance`.

**No network added to the search path:** the `set_degraded` call is a dict
write that occurs strictly *before* `embed_query` in the function body, on a
branch that returns immediately. `tests/test_pkb_semantic_backend_absent.py`
passes and this worktree's LanceDB-absent state is a genuine fixture, not a
mock. The state propagates through `embedding_status()` →
`retrieval_status()` → `X-Retrieval-Status` → `doctor` — all four of which
previously reported healthy while retrieval was dead.

Minor, non-blocking: `doctor` records the backend failure under the
pre-existing `_record("embedding_provenance", ...)` key, so a LanceDB-absent
environment reports a finding named "embedding_provenance". The exit code is
right; the label is misleading. `provisioning_vector_backend` reports it
correctly in parallel, so this is cosmetic.

---

## ASKs

**ASK-1 (ruling on interpretive call (b) — `"unavailable"` excluded from
degrading states).** The exclusion is **sound as specified** and does not hide
defect A. I checked the state machine: with ≥1 migration present, an absent DB
yields `pending` (`ensure.py:298-315`), never `unavailable` — the branch at
line 303 that could return `unavailable` for a present-but-not-pending DB is
unreachable. So `unavailable` really does mean "this checkout has no
`spec/schema/migrations/`", which for a stripped fork is legitimately
not-applicable, and `check_relational_store` agrees by reporting
`declared=False`.

But `unavailable` currently *also* encodes a second, real defect:
`ensure_db`'s `spec_dir` defaults to the **relative** `Path("spec")`
(`ensure.py:229`), so a caller with the wrong CWD gets a silent, non-degrading
`unavailable` on a perfectly healthy database:

```
$ cd /tmp && ensure_db('/tmp/.../e', apply=False)
   -> unavailable | no migrations directory at spec/schema/migrations
```

Every current caller happens to `cd "$REPO_ROOT"` first, so this is latent,
not live — except `doctor.check_provisioning`, which passes
`repo_root=os.getcwd()` (`doctor.py:252`) and therefore mis-reports if anyone
runs `./arailctl doctor` from a subdirectory. **Required before merge:**
resolve the default `spec_dir` from the package location rather than CWD, and
have `doctor` derive `repo_root` the same way. **Recommended:** split
`unavailable` into `not-applicable` (no spec tree — non-degrading) and
`spec-missing` (spec tree expected but unreadable — degrading), so the state
name stops carrying two opposite meanings.

**ASK-2 — `install` and `status` disagree about `unavailable`.**
`_install_db_ensure` sets `PHASE_DEGRADED=1` on any non-zero exit from the CLI
shim, and `main()` returns 3 for `unavailable`; `status.sh` deliberately
excludes it. The same state degrades one surface and not the other. Pick one
(ASK-1's split resolves it cleanly).

**ASK-3 — F7's committed golden was replaced by an internal-consistency
check.** ARCHITECTURE F7/test 27 called for `--json=instances` compared against
a committed golden. The implementation asserts (a) no `db`/`origin` keys leak
and (b) stripping them from `--json`'s `.instances` reproduces
`--json=instances`. That is a good property and I'm satisfied it catches this
sprint's risk, but it is self-referential: a change that broke both modes
identically would pass. Not worth blocking; file it.

**ASK-4 — dead scaffolding.** `ensure.py:349` is a bare `if True:` wrapping the
whole body of `_apply_locked`, with the body indented under it. Harmless,
but it reads like a removed condition and will confuse the next reader.

**ASK-5 — `record_version` is skipped when the spec fails to load.**
`_apply_locked:378` guards on `spec_version` truthiness, and `_load_spec_meta`
returns `(0, "")` on any exception. A DB can therefore reach `state="created"`
with an empty `schema_version` table, which `dbspec.db.applied_version()` reads
as "never applied". Report it (`detail`) rather than silently succeeding.

## Tech debt delta

Matches ARCHITECTURE §8's prediction, with the two required follow-up tickets
actually filed in `sprints/BACKLOG.md` (the `atlas`-bearing CI job, and the
`start` hard-gate promotion) — both well written and honest about scope. Debt
the architect did not anticipate, to be added to ARCHITECTURE §8 before PASS:

- The denylist→allowlist inversion (BLOCK-1) and ledger verification (BLOCK-2)
  were assumed done by "hash-verified committed migration" in §4.2; that phrase
  turned out to hide two separate mechanisms.
- Two sources of truth for what "degrading" means (ASK-2).
- `tests/cli/status_driver.sh` cannot run in any CI or worktree without a
  `.venv`, so the entire CLI-contract layer self-skips. Pre-existing, but this
  sprint added four scenarios to it and thereby increased what is invisible.

## Ruling on the known gap (shell control flow never run end-to-end)

**Partially closed by this review, and acceptable to pass to QA for the
remainder — not a blocker on its own.** I executed `scripts/status.sh`'s full
control flow live (T28a/T28b/T12/T27 pass; T28c is a test bug). What remains
unexercised is `install.sh`'s `_install_db_ensure` loop and `start.sh`'s
`_instance_db_ensure` call sites.

**Do not run these against the operator's real lab, and do not ask for
authorization to.** Both are exercisable without touching it: `LAB_ROOT` and
`ARAIL_DATA_DIR` are env-driven, and `tests/cli/lib.sh` already has
`make_fake_venv` plus a fake-repo harness (`install_driver.sh`,
`root_start_driver.sh`) built for exactly this. QA should extend those rather
than borrow the operator's lab. My own runs left the operator's lab
byte-identical, verified before and after.

## Required actions before merge

1. **BLOCK-1** — invert the classifier to an allowlist; add all seven verified
   bypasses (`ALTER TABLE t DROP c`, `REPLACE INTO`, `INSERT OR REPLACE`,
   `UPDATE OR REPLACE`, `DROP VIEW`, `DROP TRIGGER`, plus one unparseable
   statement) to the test table; correct the docstring's fail-closed claim.
2. **BLOCK-2** — verify every migration against `atlas.sum` (pure Python:
   `b64(sha256(name_bytes + content_bytes))`) *before* executing any statement;
   keep the sidecar as the complementary post-apply check; correct the
   docstring's "undocumented, binary-only" premise.
3. **BLOCK-3** — stop swallowing the status DB collector's stderr; a collector
   failure must produce a warning, a `verdict.reasons` entry, and a degraded
   exit for a live lab. Fix T28c's assertion.
4. **ASK-1** — resolve `spec_dir` (and `doctor`'s `repo_root`) from the package
   location, not CWD.
5. Add the unanticipated debt above to ARCHITECTURE.md §8.
6. ASK-2 through ASK-5: fix in place if cheap, otherwise file. None block.

## What QA should hammer

- **Fuzz the classifier**, don't table-test it. Generate statements from SQLite's
  actual grammar and assert that anything mutating a pre-seeded row is LOSSY.
  Seed a DB, run every "SAFE-FORWARD" migration, assert row-for-row equality.
- **Fresh-clone → setup → start on a scratch `LAB_ROOT`**, asserting the DB
  exists, `status` exits 0, and no `arail.db` appears at `lab/` or repo root.
- **The six-roots case with an empty `registry.d`** — the operator's real state.
  Assert `install` creates 6 DBs and `status` shows 5 `origin=ondisk` rows.
- **Kill the collector deliberately** (rename `ensure.py`, break the import) and
  assert `status` is *loud*. This is the BLOCK-3 regression test and it is the
  single most valuable test in this sprint.
- **Concurrency (F17)** — two `ensure_db(apply=True)` processes on one dir; the
  flock path is implemented but untested across processes.
- **The `2>&1` capture in `_install_db_ensure`** merges tracebacks into install
  output lines; check what a failure actually renders as.
