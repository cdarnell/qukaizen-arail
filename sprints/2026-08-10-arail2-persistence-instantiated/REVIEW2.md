# Review round 2: ARAIL 2.0 persistence, instantiated

**Date:** 2026-08-10
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) at `74eaef5`
**Round 1:** [REVIEW.md](./REVIEW.md) at `6328112` — verdict BLOCK
**Diff reviewed:** `6328112..74eaef5`, 5 commits, 9 files, +858/−116

## Verdict: BLOCK

One new finding, on the same boundary, found by attacking the new classifier
rather than re-running the old attacks. It is a one-line fix. **All three
round-1 BLOCKs are genuinely fixed — verified by execution, not by reading —
and none of that work needs re-review.** Round 3 should be a spot-check of two
things, not a re-do.

I am blocking rather than passing-with-notes for consistency: I told the
coordinator this boundary outranks everything, and the finding is a verified,
executable statement that rewrites operator rows and auto-applies at boot. The
same "only a developer can author a migration" mitigation was true of round
1's holes, and the classifier exists precisely to catch developer error.

---

## BLOCK-4 (new) — `INSERT INTO … ON CONFLICT DO UPDATE` classifies SAFE-FORWARD

`ensure.py:174-182`. The allowlist admits bare `INSERT INTO`. SQLite's upsert
suffix turns that into a row-rewriting statement, and the prefix still matches:

```
SAFE-FORWARD  INSERT INTO worlds VALUES (1) ON CONFLICT(id) DO UPDATE SET status='x';
SAFE-FORWARD  INSERT INTO worlds (id) VALUES (1)
              ON CONFLICT DO UPDATE SET status = 2;      (multi-line form too)
```

Verified to mutate real data on sqlite 3.51.0:

```
before: [('w1', 'active')]
INSERT INTO worlds VALUES ('w1','WIPED') ON CONFLICT(id) DO UPDATE SET status='WIPED'
after : [('w1', 'WIPED')]
```

This is the same consequence as round 1's `UPDATE OR REPLACE` — an operator's
existing row silently rewritten by a migration `start` applied on its own. The
22-case test table does not cover it; `INSERT INTO` appears there only inside
the table-rebuild case (`test_dbspec_ensure.py:95`).

The root cause is structural and worth naming: **allowlisting a leading
keyword does not bound what the statement does.** The same gap admits
`CREATE TRIGGER … BEGIN DELETE FROM y; END` — a trigger whose body deletes
rows later. That one happens to classify LOSSY today, but only by accident:
the naive `;` split leaves a trailing `END` fragment that fails the allowlist.
Safety resting on a tokenization accident is not safety.

**Required:** either drop bare `INSERT INTO` from the allowlist entirely
(schema migrations do not need data DML; a seeding migration can go through
`db apply` like any other non-seamless change) — my recommendation — or keep it
with a negative guard that rejects any `ON CONFLICT` / `RETURNING` / `SELECT`-
sourced form. Add to the test table: the two upsert forms above, and the
`CREATE TRIGGER` body case asserted LOSSY *for a stated reason* rather than by
accident.

## BLOCK-5 (new) — a pre-existing CLI scenario regressed, undetected

Running the driver by hand (the only way it runs), T10 — a long-standing
scenario, live `ai` instance with a missing data root — flipped from exit 0 to
exit 3:

```
● ai  ai  :27386  pid 9280
      ⚠ data root missing
      data  .../lab/instances/ai/data
      db    pending — safe-forward migration(s) not yet applied
FAIL: T10: expected exit 0, got 3
```

Two things to decide, and this must not ship undecided:

1. **Is degrading correct here?** Arguably yes — a live World with no
   `arail.db` *is* defect A, and §4.4 says a live lab with a pending DB
   degrades to 3. If so, T10's expectation is stale and should be updated with
   a comment saying why the contract changed.
2. **But the data root does not exist.** Status is reporting "db pending" for a
   directory that isn't there, on a fixture that already had its own
   `⚠ data root missing` warning and deliberately did not degrade. Reporting a
   *second*, derived complaint about a missing directory is noise, and
   "pending" is the wrong word for it. Consider: when the data root is absent,
   report the missing root and suppress the derived db state.

Either resolution is defensible. Shipping without choosing is not, because
this is a documented exit-code contract (`docs/cli.md`) changing silently.

Note the meta-pattern: **this is the second consecutive round where the shell
layer's only real test hid something, and both times I found it by hand-running
the driver against an external venv.** Round 1 it hid two broken assertions;
round 2 it hid a live behaviour regression. Now folded into ARCHITECTURE §8 as
the sprint's highest-value untracked debt.

---

## Round-1 BLOCKs: all three verified fixed

### BLOCK-1 (classifier) — fixed, and it holds under fresh attack

The denylist is gone, replaced by a per-statement allowlist with a fail-closed
default. I attacked the *new* surface as instructed — statement splitting,
comments, whitespace, case, CTEs, wrappers, unparseables — rather than
re-running round 1's four. Results (`✓` = correct):

```
✓ LOSSY  ALTER TABLE worlds DROP slug        (round-1 bypass, closed)
✓ LOSSY  UPDATE OR REPLACE worlds SET …      (round-1 bypass, closed)
✓ LOSSY  REPLACE INTO worlds VALUES (1)      (round-1 bypass, closed)
✓ LOSSY  INSERT OR REPLACE INTO worlds …     (round-1 bypass, closed)
✓ LOSSY  DROP VIEW / DROP TRIGGER
✓ LOSSY  INSERT INTO t VALUES ('; DROP TABLE x --')   semicolon-in-string
✓ LOSSY  CREATE TABLE t (a text DEFAULT 'a;b')        fails closed (false pos)
✓ LOSSY  -- a;b \n CREATE TABLE …                     fails closed (false pos)
✓ LOSSY  WITH c AS (…) INSERT INTO …                  CTE, fails closed
✓ LOSSY  BEGIN; CREATE TABLE …; COMMIT;               wrapper, fails closed
✓ LOSSY  /* x */ DROP TABLE t                         comment-led destructive
✓ LOSSY  /* DROP TABLE x                              unterminated comment
✓ LOSSY  PRAGMA writable_schema=1
✓ LOSSY  @@@ not sql at all ###                       unparseable, no throw
✓ LOSSY  CREATE TABLE a…; DELETE FROM b; CREATE INDEX… one lossy taints file
✓ SAFE   /* note */ CREATE TABLE …    -- note \n CREATE TABLE …
✓ SAFE   leading whitespace/newlines · mixed case · no trailing semicolon
✓ SAFE   empty file · comments-only file
✓ SAFE   the real committed baseline (regression pin holds)
```

The unparseable→LOSSY default genuinely holds and does not throw. Every
ambiguous case errs toward LOSSY. This is a correct design, with the one
residual hole in BLOCK-4.

### BLOCK-2 (ledger verification) — fixed, and the ordering claim is true

`_verify_ledger` runs at `ensure.py:419`, before the cursor read and before
`dbmod.connect(create=True)`. I verified the ordering the only way that
counts — by checking whether the database file exists after each failure. It
never does, which proves no SQL ran:

```
baseline (untampered)          state=created    db_file=True  tables=6
atlas.sum MISSING              state=diverged   db_file=False tables=0
atlas.sum TRUNCATED to 0 bytes state=diverged   db_file=False tables=0
atlas.sum header line only     state=diverged   db_file=False tables=0
atlas.sum corrupt/malformed    state=diverged   db_file=False tables=0
migration file TAMPERED        state=diverged   db_file=False tables=0
unlisted extra migration added state=diverged   db_file=False tables=0
```

A missing or corrupt `atlas.sum` fails closed as "cannot verify," not as
"nothing to verify" — the specific trap I asked about. And round 1's sidecar
bypass is closed: deleting `.arail_ensure_state.json` and then tampering now
yields `diverged`, where round 1 yielded `ok`.

The `_atlas_file_hash` implementation matches the real `atlas.sum` byte-for-
byte, and the docstring correcting the earlier "undocumented, binary-only"
claim is accurate.

### BLOCK-3 (collector silence) — fixed, and I re-ran my accidental discovery deliberately

Same conditions as round 1 (an interpreter where `arail.dbspec.ensure` does not
import). Round 1: total silence, `"db": null`, exit 3 for an unrelated reason.
Round 2:

```
⚠ db: status could not check the relational store: Traceback (most recent
  call last): File "<string>", line 4, in <module> ModuleNotFoundError:
  No module named 'arail.dbspec.ensure'
```

Loud, with the traceback, plus `db:collector-failed` in `verdict.reasons`, and
exit 3 because a lab is live. The `4`-never-promoted guarantee is preserved
structurally (`any_live` gates only the `candidates.append(3)`; the reason and
warning are unconditional, which is the right split — honest without lying
about severity).

With a working collector the driver passes end to end — **16 scenarios,
including T28a (exit 4 preserved, DB never created), T28b (`root:db:pending` →
3), T28c (assertion correctly rewritten to test the db claim rather than an
unrelated memory-degraded fixture), T12/T27 (`--json=instances` carries no
`db`/`origin`)** — with T10 as the sole deviation (BLOCK-5).

### ASKs

- **ASK-1 — fixed and verified.** `DEFAULT_SPEC_DIR` resolves from
  `Path(__file__).resolve().parents[3]`. From `/tmp`, a healthy DB now reports
  `ok` where round 1 reported a false, silently non-degrading `unavailable`.
  `doctor` derives `repo_root` the same way. The recommended `unavailable`
  split was not done and no longer needs to be — with the CWD conflation gone,
  the state has one meaning again.
- **ASK-2 — fixed.** The CLI's exit set now matches `_DB_DEGRADING_STATES`
  exactly. (Nothing enforces they stay matched; recorded in §8.)
- **ASK-4 — fixed.** The `if True:` scaffold is gone.
- **ASK-5 — fixed.** `record_version_skipped` is now reported in `detail` with
  an action, instead of a silent success.
- **ASK-3 — filed** in `sprints/BACKLOG.md`. Correct call.

## Required action 5 (mine) — closed

I folded round 1 and round 2's unanticipated debt into ARCHITECTURE §8 myself
in this commit; the builder correctly declined to edit my artifact. Six new
entries, including the two structural ones this round exposed (the phrase
"hash-verified" concealing two mechanisms; prefix-matching approximating an
effects-based rule) and the `make_fake_venv` near-miss.

## The `make_fake_venv` footgun — ruling

`tests/cli/lib.sh:231-236` symlinks `$fake/.venv/lib` directly into the real
venv's `site-packages`. A scenario that renames or writes through that path
mutates the operator's live installation. The builder drafted such a scenario,
recognized it, and discarded it before running — the right call, and worth the
record.

**The harness should be hardened, and the note is currently in the wrong
place.** It sits at the tail of `status_driver.sh`; the person who will
complete this mistake is the person editing `make_fake_venv` or writing a new
driver, and they will never be reading the end of the status driver. Required
before merge: move/duplicate the warning to the symlink site in `lib.sh`.
Recommended follow-up (ticket, not this sprint): make the fake venv's `lib` a
directory of per-package symlinks, or mark the tree read-only, so a stray
write fails loudly instead of landing in the real install.

I confirmed no collateral damage from my own runs: the operator's `lab/` tree
hash is identical before and after (`7669d82c…` both times), and the real
venv's `site-packages` still has 454 entries.

## Required actions before merge

1. **BLOCK-4** — remove bare `INSERT INTO` from the allowlist (preferred), or
   guard it against `ON CONFLICT`/`RETURNING`. Add both upsert forms and the
   `CREATE TRIGGER`-body case to the test table.
2. **BLOCK-5** — decide T10 deliberately: update the stale expectation with a
   documented reason, or suppress the derived db state when the data root is
   missing. Update `docs/cli.md` if the exit-code contract changed.
3. Move the `make_fake_venv` hazard note to `lib.sh`, at the symlink.
4. File the two new §8 tickets: a CI-runnable path for `status_driver.sh`, and
   hardening `make_fake_venv`.

Nothing else from round 1 or round 2 needs re-review.

## What QA should hammer (unchanged priorities, now sharper)

- **The statement-splitting surface**, now that it is load-bearing. Fuzz from
  SQLite's grammar; seed a database, apply every "SAFE-FORWARD" migration, and
  assert row-for-row equality. Any statement that mutates a pre-existing row
  while classifying SAFE-FORWARD is a defect regardless of how it is spelled.
  BLOCK-4 was found this way and there may be more.
- **Kill the collector deliberately** — still the single most valuable test in
  this sprint. Break the import, assert `status` is loud, assert a
  not-running lab still exits `4`. **Do not build this by writing through
  `make_fake_venv`'s `.venv/lib` symlink** (see above); break it from the
  caller side — a `PYTHONPATH` that shadows `arail.dbspec.ensure`, or a stub
  `python3` earlier on `PATH`.
- **Run `status_driver.sh` at all.** It self-skips without a `.venv`, and it
  has now concealed a finding in two consecutive rounds. Run it against a real
  venv and treat T10 as a result, not a nuisance.
- Fresh-clone → setup → start on a scratch `LAB_ROOT`; the six-roots case with
  an empty `registry.d`; cross-process concurrency on `_apply_lock`.
- `install.sh`/`start.sh` shell control flow remains unexercised end to end —
  use `LAB_ROOT`/`ARAIL_DATA_DIR` and the fake-repo harness, never the
  operator's real lab.
