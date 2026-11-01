# Review round 3: ARAIL 2.0 persistence, instantiated

**Date:** 2026-08-10
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) at `ac845b4`
**Round 1:** [REVIEW.md](./REVIEW.md) `6328112` — BLOCK (3 findings)
**Round 2:** [REVIEW2.md](./REVIEW2.md) `e7efcc5` — BLOCK (2 findings)
**Diff reviewed:** `e7efcc5..ac845b4`, 4 commits, 8 files, +389/−16

## Verdict: WEAK_PASS — advance to QA

Both round-2 BLOCKs are fixed, verified by execution. The data-safety boundary
is now closed and I could not breach it. One residual observability edge
(ASK-6) is a one-line tightening that does not need to gate QA, and one §8 debt
item should be promoted to a blocking ticket before *merge* — but not before
QA, since QA is the surface that will exercise it.

This is the first round where I attacked the boundary and failed to get
through. That is the standard I was holding it to.

---

## BLOCK-4 (allowlist) — fixed, and the removal reasoning is sound

**The baseline claim the whole choice rests on is true.** I checked directly:

```
grep -ci insert  spec/schema/migrations/*.sql -> 0
grep -ci trigger spec/schema/migrations/*.sql -> 0
```

The committed baseline is 18 statements, all `CREATE TABLE` / `CREATE INDEX` /
`CREATE UNIQUE INDEX`, every one classifying SAFE-FORWARD, and the file still
classifies SAFE-FORWARD. Dropping the two keywords cost nothing real.

**Removal over a negative guard was the right call**, and for the reason the
builder gave: a guard against `ON CONFLICT` protects against the suffix named
today. That is a denylist wearing an allowlist's clothes, and BLOCK-1 already
established that this codebase cannot carry one safely. The builder generalized
my structural lesson instead of patching my example, which is the response I
wanted and did not ask for explicitly.

**I audited the four survivors independently rather than accepting the sweep**,
using the specific hazards raised:

| Keyword | Hazard tested | Result |
|---|---|---|
| `CREATE TABLE` | `AS SELECT` form | Creates a new table; cannot rewrite or delete an existing row. Sound. |
| `CREATE INDEX` | `UNIQUE` over duplicate data | Fails with an error, not data loss; caught by the per-file transaction → rollback → `blocked`. Sound. |
| `CREATE VIEW` | data-modifying CTE | **SQLite rejects it outright** — `WITH x AS (DELETE … RETURNING …)` and the `UPDATE` form both raise `OperationalError: near "DELETE": syntax error`. The hazard does not exist in this engine. A view is a stored SELECT. Sound. |
| `ALTER TABLE … ADD COLUMN` | `DEFAULT` rewriting existing rows | Verified: `ADD COLUMN b TEXT NOT NULL DEFAULT 'z'` leaves the pre-existing row intact (`[(1,'z')]`) — the new column simply did not exist before, so no prior data is altered. Sound. |

Two bonus exclusions I did not ask for and that are correct: `CREATE TEMP
TABLE` and `CREATE VIRTUAL TABLE` both classify **LOSSY** (the regex requires
`CREATE TABLE` adjacently). Excluding virtual tables matters — an `fts5`
external-content table has side effects a prefix cannot bound. And `CREATE
TRIGGER`'s exclusion also closes the `INSTEAD OF` trigger route into views.

Full re-attack of the round-2 table plus the new cases:

```
LOSSY  INSERT INTO worlds VALUES (1)                     (dropped)
LOSSY  INSERT INTO … ON CONFLICT … DO UPDATE SET …       (BLOCK-4, closed)
LOSSY  CREATE TRIGGER g … BEGIN DELETE FROM y; END       (dropped, no longer accidental)
LOSSY  CREATE TEMP TABLE · CREATE VIRTUAL TABLE … fts5
SAFE   the real committed baseline, all 18 statements
```

One residual worth naming, not fixing: `ALTER TABLE … ADD COLUMN p REFERENCES
worlds(id) ON DELETE CASCADE` classifies SAFE-FORWARD and installs a *future*
cascade. It is second-order — the cascade only fires on a later `DELETE`, which
is itself LOSSY and would never auto-apply — but it is the same shape of
reasoning that BLOCK-4 punished, so it belongs in the record. Recorded in §8.

## BLOCK-5 (T10) — fixed, and suppression is the right call

**Ruling: yes, suppression is correct here, and it is not the invisibility
this sprint exists to prevent.** The distinction that decides it:

- `data_root_missing` is *already reported*, at the right level, naming the
  right subsystem. Nothing is being hidden — one fact is being reported once
  instead of twice.
- The suppressed value would have been `pending`, which is a **false
  description**: it means "safe-forward migrations not yet applied," implying
  applying them would help. It would not; the directory does not exist.
  Reporting a wrong cause is worse than reporting the right one once.
- The sprint's recurring defect is a subsystem failing *silently while
  claiming health*. Here the failure is loud, at the correct layer, and the
  db object is `null` (absent) rather than `ok` (a false claim of health).
  Those are categorically different.

I verified the guarantee you asked for — a genuinely absent-but-should-exist DB
still surfaces. With a data root that **does** exist and no DB, a live lab
still reports `pending` and degrades to 3 (T28b, passing). Suppression is
reachable only via `data_root_missing`.

T10 now asserts the outcome explicitly (`data_root_missing is True`,
`db is None`, no leaked `db pending` line) rather than passing by accident —
which was the actual complaint. And `docs/cli.md` documents the exception, so
the exit-code contract is no longer changing silently.

**Full driver run, working collector: 16/16 scenarios pass**, including T10,
T28a (exit 4 preserved, DB never created), T28b (`root:db:pending` → 3), T28c,
T12/T27. **Broken collector: still loud** — the traceback and
`db:collector-failed` both surface, re-confirming BLOCK-3.

## ASK-6 (new, not blocking) — suppression is keyed on the wrong thing

Suppression is gated on `data_root_missing` alone, but `ensure_db`'s
`_verify_ledger` runs *before* the data dir is ever consulted (correctly — that
is BLOCK-2's fix). So a checkout-global ledger failure is reachable with a
missing data root:

```
missing data root, healthy ledger  -> pending    (correctly suppressed)
missing data root, TAMPERED ledger -> diverged   (also suppressed — should not be)
```

`diverged` is a fact about the *checkout*, not about the missing directory, and
it is the BLOCK-2 condition. In most topologies it still surfaces on every
other root (it is checkout-global, and the root lab's db object is never
suppressed), so the blind spot needs a specific shape: a tampered ledger, where
the only live lab is an instance whose data root is missing.

Narrow, and not data-destroying — hence not a BLOCK. **Recommended one-line
fix:** gate suppression on the state, not the flag — suppress only the states
*derived from* the missing directory (`pending`, `unavailable`) and let
`diverged`/`ahead`/`blocked` through, since those describe the checkout or the
file, not the absent dir. Do it before merge if cheap; otherwise file it.

## Footgun note and the filed tickets

**The note is now in the right place** — directly above the
`ln -s "$REAL_VENV/lib"` line in `tests/cli/lib.sh`, where someone editing the
helper or writing a new scenario will actually be standing. It names the
concrete forbidden operations ("rename ensure.py so an import fails," "patch a
module file"), records the near-miss with a pointer to REVIEW2.md, and gives
the caller-side alternative (PYTHONPATH shadow / stub `python3` on PATH). That
is exactly the handoff I asked for.

**Filing rather than implementing: accepted for the harness hardening,
promoted for the CI-runnable driver.** Both are in `sprints/BACKLOG.md`. My
ruling on the question you raised:

- **`make_fake_venv` hardening — filing is right.** It is shared harness
  infrastructure touching every CLI driver, unverifiable without a real
  `.venv`, and the danger is now documented at the site. Changing it blind, in
  a sprint about something else, is how you break six drivers at once.
- **The CI-runnable driver path — filing is right *for now*, but this must
  block merge, not QA.** My §8 finding stands: this layer hid a defect in two
  consecutive rounds and both were found only by hand-running against an
  external venv. But the fix is not verifiable from this worktree either, and
  QA — who will have a real `.venv` — is precisely the party who can prove out
  a runnable path. **Promote it to a blocking ticket before merge**, with QA's
  run as the evidence. Do not merge a sprint whose central shell surface is
  provably capable of hiding regressions with no automated way to catch them.

That is the one §8 item I want promoted from debt to a merge blocker. The rest
stay as debt.

## Verification hygiene

`75` sprint tests pass; `bash -n` clean on all six modified shell scripts. The
operator's `lab/` tree hash is identical before and after every run across all
three rounds (`7669d82c…`), and the real venv's `site-packages` still has 454
entries — I did not write through the symlink I flagged.

## Required before merge (not before QA)

1. **ASK-6** — key suppression on the db state, not the `data_root_missing`
   flag, so `diverged`/`ahead`/`blocked` survive it.
2. **Promote the CI-runnable-driver ticket to a merge blocker**, with QA's run
   as the evidence it is achievable.
3. Carry the `ADD COLUMN … REFERENCES … ON DELETE CASCADE` residual into §8.

---

## QA target list

**Two standing items, carried forward and unchanged in priority:**

1. **The deliberate collector-kill test — still the single most valuable test
   in this sprint.** Break `arail.dbspec.ensure` from the **caller side only**:
   a `PYTHONPATH` that shadows the module ahead of site-packages, or a stub
   `python3` earlier on `PATH`. **Never write through `make_fake_venv`'s
   `.venv/lib` symlink** — see the warning now at that line in `lib.sh`.
   Assert: the traceback surfaces, `db:collector-failed` lands in
   `verdict.reasons`, a live lab exits 3, and a lab that was never started
   still exits 4.
2. **The statement-splitting surface**, now that the allowlist rests on it.
   Fuzz from SQLite's grammar rather than a fixed table. The oracle that
   matters: seed a database, apply every migration the classifier calls
   SAFE-FORWARD, and assert **row-for-row equality** of the pre-existing data.
   Any statement that mutates a pre-existing row while classifying
   SAFE-FORWARD is a defect regardless of spelling. Both BLOCK-1 and BLOCK-4
   were found this way; assume a third exists.

**Also hammer:**

3. **Run `status_driver.sh` at all**, against a real `.venv`, and treat every
   result as a finding rather than a nuisance. It has concealed something in
   two of three rounds. Report whether a CI-runnable path is achievable — that
   evidence is what unblocks merge (see above).
4. **Fresh clone → setup → start on a scratch `LAB_ROOT`** — the seamless
   promise end to end. Assert the DB exists, `status` exits 0, and no
   `arail.db` appears at `lab/` or the repo root.
5. **The six-roots case with an empty `registry.d`** — the operator's real
   measured state. `install` must create 6 DBs; `status` must show the
   unregistered instances as `origin=ondisk` findings, not skip them.
6. **`install.sh` / `start.sh` shell control flow**, still never run end to
   end. Use `LAB_ROOT`/`ARAIL_DATA_DIR` and the fake-repo harness — **never
   the operator's real lab**, and no authorization for it should be sought.
7. **Ledger tampering** against a live lab: delete/corrupt `atlas.sum`, tamper
   a migration, add an unlisted one. Every case must yield `diverged` with the
   database file never created. Include the ASK-6 topology (missing data root
   + tampered ledger) so the fix is proven when it lands.
8. **Cross-process concurrency** on `_apply_lock` — two `ensure_db(apply=True)`
   processes on one data dir. The flock path is implemented but has never been
   exercised across processes.
9. **The win condition itself** — Buddy retrieving on a natural-language query
   through `search_for_agents`, on a provisioned lab. This sprint's origin
   story was a defect that only reproduced in an unprovisioned interpreter;
   confirm the honest-failure path too (LanceDB absent ⇒ loud, not a silent
   keyword fallback claiming health).
