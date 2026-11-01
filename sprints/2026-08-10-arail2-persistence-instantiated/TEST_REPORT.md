# Test report: ARAIL 2.0 persistence, instantiated

**Date:** 2026-08-10
**Build:** round 5 — [BUILD_LOG.md](./BUILD_LOG.md) at `e1301d9`
**Verdict: WEAK_PASS — ship it.** See §00. Rounds 4 and 3 are preserved below
from §0 onward, unedited.

---

## 00. Round 5 — the gate

**Verdict: WEAK_PASS.** Everything blocking is fixed and verified by execution
on both interpreters. WEAK_PASS rather than PASS because two known findings
(QA-11, QA-13) ship as filed debt by explicit ruling and one residual (F17's
read/write window) is closed empirically rather than structurally. **My
recommendation is unambiguous: merge this. There should not be a round 6.**

### 00.1 Verification of the round-5 fixes

| Finding | Status | Evidence |
|---|---|---|
| QA-12a zero-byte wedge | **Fixed** | `0-byte arail.db` → `apply=False: pending`, `apply=True: created`. Both anti-overshoot pins still assert `blocked`, unmodified and passing: a non-empty bad-magic file and a valid-magic-but-truncated file |
| QA-12b concurrency | **Fixed empirically** | 8-process race **0/30** (round 4: 3/20); my header-read module **0/30**; both interpreters |
| QA-10 wrong return type | **Fixed** | a `None`-returning predicate is now a finding at its registered tier; `relational_store` and `vector_backend` are still evaluated and recorded; `to_json` no longer raises |
| QA-14 docstring | **Fixed** | now states the staleness is persistent after an abnormal exit, names the healing agent (the next `apply=True`), and warns against "fixing" it by reading through SQLite (which would reopen QA-1) |
| QA-11, QA-13 | **Filed** | `sprints/BACKLOG.md:1143` and `:1178` |

**The single remaining test failure is the filed one and nothing hides behind
it.** Sprint-scope suite, both interpreters: `740 passed, 1 failed` (venv) /
`732 passed, 1 skipped, 1 failed` (system python) — the one failure is
`test_a_predicate_cannot_impersonate_another_mechanisms_key`, QA-11. I have
converted it to `xfail(strict=True)` so the suite is green at merge while the
repro stays executable and flips **loudly** the moment the back door closes,
rather than rotting into a skipped line. Same treatment for the two new latent
findings below.

**Driver set, operator `.venv`:** `status_driver` **16/16**,
`qa_db_collector_driver` **7/7**, `qa_db_seamless_driver` **9/9**,
`qa_db_ledger_driver` **5/6**. The sixth is A2's third assertion — QA-13, the
filed human-view item — and only that: A2's `--json` assertions (exit 3,
`diverged` in `verdict.reasons`) pass, as do controls A0/A1/A3/A4.

**Full suite:** `5363 passed, 62 failed, 32 skipped, 7 errors`. Against round
3's `5344 passed, 68 failed`: **all six sprint-owned failures are gone**, the
62 are the pre-existing `main` set proven against a merge-base clone in §9a,
and the file histogram is identical to that baseline minus the sprint files.

### 00.2 Ruling requested: does 0/20 retire F17, or just make it rarer?

**Ruling: the race is empirically closed and structurally still present. I
accept the builder's deviation and file the residual. Do not add locking to the
read path.**

I did not accept 0/20. I measured the window directly. Across **200 concurrent
database creations, ~100,000 stat samples** taken by a reader polling the file
while a real `ensure_db(apply=True)` subprocess created it, the main database
file was observed in exactly two states:

```
size == 0        : 28,433 observations
size >= 100      : 71,967 observations   (0 of them with a non-SQLite magic)
```

**No intermediate 1–99-byte state was ever observed**, and no `>=100`-byte
sample ever had a bad header. SQLite writes page 1 in a single 4096-byte
`write()`, so on this filesystem the 0 → full-header transition is atomic from
a reader's point of view. The stat short-circuit therefore covers the *only*
state a reader can actually catch, which is why 0/30 is not luck.

What I will not claim is that it is impossible. The read still takes no lock,
so correctness rests on a filesystem property (single-write page flush on APFS)
rather than on mutual exclusion. On a filesystem where a partial write is
visible — NFS, a network mount, a short write after a signal — a reader could
still see a torn header.

**Three reasons that is file-not-fix, and why the builder's judgement was
right:**

1. **The consequence is now transient, not permanent.** The wedge was QA-12a,
   and it is gone. A torn-header read yields one `blocked` from one `status`
   invocation; the next call sees a complete file. That is a cosmetic blip, not
   a lab that cannot start.
2. **Locking the read path would be a real regression.** `_apply_lock` is
   `LOCK_EX` and blocking. Putting it on the read path makes `status` and
   `doctor` **hang** behind an `install` replaying six roots — turning a
   read-only health check that currently completes in **0.88 ms/root** into
   something that can block indefinitely, against F9's budget and §4.4's
   local-read promise. Trading a rare cosmetic blip for a hang is the wrong
   trade, and it is exactly the class of "fix makes it worse" this sprint
   already learned once, in round 4.
3. **The cheap structural close, if anyone wants it later, is not a lock.** A
   single retry on `DatabaseError` in `_read_user_version_readonly` (re-stat,
   re-read once) closes the torn-header window with no blocking and no lock
   ordering. Filed as the follow-up shape; not worth a sixth round.

### 00.3 One more escape attempt — two latent holes, both filed

Asked to try again to construct a mechanism that escapes. The type check holds
against `None` and every wrong-type return I tried. Two holes remain, and
**neither is reachable today**: `provisioning.register` has **no caller outside
`src/arail/provisioning.py`** (verified by grep across `src/` and `lab/`), so
every registered predicate is in-repo code reviewed by a human. They matter when
ARAIL 2.1 adds a mechanism — which is the registry's entire purpose — so both
are pinned as `xfail(strict=True)` repros rather than prose:

- **QA-15 (LOW)** — `evaluate_all` catches `Exception`, not `BaseException`. A
  predicate raising `SystemExit`/`KeyboardInterrupt` propagates through
  `evaluate_all` *and* through `doctor.check_provisioning`'s outer
  `except Exception`, aborting the whole checkup. Measured.
- **QA-16 (LOW)** — no tier validation. An `Assertion` returning an
  unrecognized tier (`"urgent"`) is a finding that degrades **nothing**:
  `doctor`'s exit code counts only `level == "required"`. A typo'd tier in a
  future mechanism is a finding nobody's exit code reads — QA-6's shape,
  one layer out.

### 00.4 The specified test nobody had written — now written, and it passes

`ARCHITECTURE.md` §7 test 4 (F16) required proving that the **Atlas-free replay
reproduces the declared schema**. It was never written, and five build rounds
shipped on that assumption unverified. `atlas` is installed on this machine, so
I ran it:

```
$ atlas schema diff --from sqlite://<ensured>/arail.db \
      --to file://spec/schema/schema.hcl --dev-url "sqlite://dev?mode=memory"
Schemas are synced, no changes to be made.
```

Committed as `tests/test_qa_schema_parity.py` (skips cleanly where `atlas` is
absent — every user machine and CI), together with a test naming Assumption 4
directly: no `_arail_migration*` table exists, so the `PRAGMA user_version`
cursor is genuinely invisible to `atlas schema diff`. **This converts the single
largest trust item in the sprint into evidence.**

### 00.5 Performance — measured, not asserted

The §4.5/§4.4 budgets had never been measured. Operator `.venv`, 20–50
iterations each:

| Path | Budget | Median | p95 |
|---|---|---|---|
| `ensure_db(apply=True)` cold create (first `start`) | ≤150 ms | **2.2 ms** | 2.8 ms |
| `ensure_db(apply=True)` healthy (every later `start`) | ≤150 ms | **1.2 ms** | 1.5 ms |
| `ensure_db(apply=False)` (`status`/`doctor`, per root) | <50 ms | **0.88 ms** | 0.97 ms |

Two orders of magnitude inside budget; six roots cost ~5 ms of `status`. No
BENCHMARK.md — there is no baseline to regress against and nothing here is on
the retrieval or inference hot path.

### 00.6 Operator-lab safety, round 5

- Full stat listing of `lab/` (21,659 entries) before and after the round:
  **`diff` empty**. Zero `arail.db` anywhere beneath it. `.venv` `site-packages`
  still **454** entries. **19** stash entries intact. No `instance.env`
  anomaly this round.
- No bare `git stash`; nothing written through `make_fake_venv`'s `.venv/lib`
  symlink; every fixture under `mktemp -d`.

---

## 00A. The three things asked for, plainly

### 1. What remains UNPROVEN — what we take on trust at merge

Not "what is broken" — what **no test covers**:

1. **That the store is useful.** Nothing reads `arail.db` at runtime (§0 of
   ARCHITECTURE, still true). We have proven the schema is created, correct
   against its declaration, and honestly reported. We have **not** proven it
   serves any feature, because no feature consumes it yet. The value delivered
   is "the dependent service comes up", nothing more, and the docs must keep
   saying so.
2. **The win condition's positive half.** Buddy retrieving through
   `search_for_agents` on a *provisioned* lab rests on the architect's manual
   measurement (12 semantic hits, gated), not on a committed test. Only the
   honest-failure half is tested. This is the same gap I named in round 3 and it
   is still open — the largest untested surface in the sprint.
3. **Every measurement is macOS/APFS on two SQLite versions** (3.51.0, 3.53.4).
   Linux, WSL, network filesystems: unexercised. The F17 empirical closure in
   particular is a statement about APFS write atomicity.
4. **No clean-machine run.** `install.sh`/`start.sh` control flow is exercised
   only through the fake-repo harness. Nobody has done fresh clone → `setup` →
   `start` on a machine without a `.venv`, which is the product's actual first
   five minutes.
5. **`doctor`'s exit code end to end.** Asserted only through `provisioning`
   unit tests; there is no `doctor_driver.sh` sibling to `status_driver.sh`.
6. **Concurrency beyond 8 local processes**, and `flock` semantics on network
   filesystems.
7. **`.arail_ensure_state.json`** — written next to every database, read by
   nothing. It is itself an instance of this sprint's own defect class, sitting
   inside the sprint that named it.

### 2. Should anything filed be promoted to a merge blocker?

**One, and it is the architect's.**

- **CI-runnable driver path — the evidence requirement is DISCHARGED; the
  workflow entry is not, and I keep it as a merge blocker.** The architect
  promoted this and named my run as the unblocking evidence. My runs discharge
  the *question*: the path exists, all four DB drivers plus `status_driver` run
  green and unattended against a real `.venv` (16/16, 7/7, 9/9, 5/6-by-design),
  the longest takes ~40 s, none needs a GPU, a model or the network, and the
  wrong-tree resolution that made a green driver meaningless (QA-8) is fixed.
  What remains is adding them to `.github/workflows/`. I will not sign off on
  merging a sprint whose central shell surface has no automated guard when the
  guard is now a five-line workflow addition — **but it is a five-line change,
  not another round.** Ship it in the merge commit.
- **QA-13 (human view silent on a tampered ledger): keep filed, do not
  promote.** `--json` reports it, `status` exits 3 whenever any lab is live, and
  `start` warns at boot — which is the moment the SQL would actually run. The
  contract text (§4.4 "up **or** the state is not ok") and the code disagree;
  fix the text or the code in a follow-up, but this does not hold a release.
- **QA-11, QA-15, QA-16: keep filed.** All three require a hostile or buggy
  predicate, and there is no registration path outside the repo. All three are
  now `xfail(strict=True)`, so they announce themselves.
- **`make_fake_venv` hardening: keep filed.** Unchanged reasoning — shared
  harness infrastructure, unverifiable without a real `.venv`, danger documented
  at the site. My drivers proved you can break an import safely from the caller
  side, which lowers the pressure further.
- **§8 debt (the `ADD COLUMN … ON DELETE CASCADE` residual, the readiness-gate
  promotion when a runtime reader lands): keep filed.** Both are correctly
  described and both are pinned by tests.

### 3. Honest shippability read — including whether five rounds made it worse

**Ship it.**

The part that can hurt a user is the part that has been attacked hardest and
has not moved: what SQL runs automatically at boot. Across five rounds it has
absorbed a 600-candidate grammar fuzz judged by a row-equality oracle rather
than by the classifier's own opinion, seven closed bypasses, four
ledger-tampering shapes that all refuse to create a database, a filename gate
that rejects traversal-shaped names, and — as of today — an Atlas diff proving
the replay reproduces the declared schema exactly. I have tried to breach that
boundary in three separate QA passes and have not.

**On whether the fixes made the code harder to reason about: mostly no, with
one exception.** `provisioning.py` is *better* than it started — the registry
now carries tiers, refuses duplicates, and validates returns; each rule is one
guard clause with a named reason. `status.sh`'s suppression is *simpler* after
ASK-6: keyed on a two-element state set instead of a boolean whose meaning had
to be traced back through two call sites.

The exception is `_read_user_version_readonly`. It is now 5 lines of code under
45 lines of docstring, and the docstring is load-bearing: it encodes three
separate near-misses (don't read through SQLite → reopens QA-1; don't remove the
`close()` → staleness; don't treat empty as corrupt → the wedge). A future
maintainer who reads only the code will get it wrong, in whichever direction
they push. That is a real fragility and the honest way to describe it is that
the comment *is* the design. I would rather have that than a clean-looking
function that reintroduces one of the three, but it should be understood as a
place the next person must slow down — which is precisely what the docstring
now says.

Five rounds is a lot. It bought a genuinely closed data-safety boundary, a
reporting layer that survived a deliberate collector-kill, a class check that
generalizes to mechanisms it was not written around, and a test harness that no
longer silently exercises the wrong source tree. **What would change my answer:
a runtime reader of `arail.db` landing before the readiness gate is promoted.
Until that happens, the worst case here is a lab that reports a state
inaccurately for one `status` invocation — and the best case, which is the
common one, is that the thing this sprint exists to fix simply works.**

---

## 0. Round 4 — re-test after the builder's fixes (superseded by §00)

**Verdict: FAIL.** Five of the six things I asked for are genuinely fixed and
verified by execution on **both** interpreters. One fix — QA-1's — introduced a
new defect that is worse in one specific way than the bug it replaced: it
creates a lab state with **no recovery path through any documented verb**.

| Round-3 finding | Round-4 status | Verified how |
|---|---|---|
| QA-1 write-free | **Fixed** — and **regressed** (QA-12) | 5/5 write-free tests pass on SQLite 3.51.0 *and* 3.53.4; `qa_db_seamless_driver.sh` S5 clean; builder's own `test_user_version_ahead_of_ledger` passes |
| QA-2 / ASK-6 | **Fixed for `--json`; human view still silent** (QA-13) | ledger driver A2: exit 3 ✅, `diverged` in `verdict.reasons` ✅, human render ❌ |
| QA-5 six roots | **Fixed** | passes on the real six-root shape (empty `registry.d`, 5 on-disk dirs) |
| QA-6 crash tier | **Fixed** | a crashing `required` check stays `required` |
| QA-7 silent overwrite | **Fixed** | duplicate `register()` refused + logged; `overwrite=True` is explicit |
| QA-8 wrong source tree | **Fixed, sweep confirmed** | proved by execution, §0.4 |

### 0.1 QA-12 (MEDIUM–HIGH, **new in round 4**) — the header read wedges a lab

The fix is right in principle and I still endorse it: `_read_user_version_readonly`
now reads bytes 60–63 of the SQLite header via `open(path,"rb")` and never
invokes SQLite, so `apply=False` is genuinely zero-write on every version. What
changed as a side effect is the treatment of a file that exists but has no
header yet. The old code asked SQLite, and **SQLite treats a zero-length file as
a valid empty database** (`PRAGMA user_version` → 0). The new code raises
`DatabaseError`, which `ensure_db` maps to `blocked`.

**QA-12a — a zero-byte `arail.db` is a permanent wedge.** Measured, side by
side, same fixture:

```
round 3 (d161ac3):  0-byte arail.db -> apply=False: pending   apply=True: updated   (heals)
round 4 (d461a1d):  0-byte arail.db -> apply=False: blocked   apply=True: blocked   (forever)
```

`install` refuses, `start` refuses, `status`/`doctor` degrade to exit 3, and
`ensure` correctly never deletes a database — so nothing in the product clears
it. A zero-byte `arail.db` is what a crash, `kill -9`, full disk, or a laptop
losing power between file creation and the first commit leaves behind. The
operator's only escape is deleting a file no documentation mentions.

**QA-12b — a concurrency regression on F17.** The version is read *before*
`_apply_lock` is taken, so a second process reading while the first has created
the file but not yet written its header now gets `blocked`. Hammered, 20 runs
each of the existing 8-process race:

```
round 3 (d161ac3):  0 failures / 20
round 4 (d461a1d):  3 failures / 20   exit 3, "does not look like a SQLite database file"
```

F17's promise is "both end ok, no corruption". `install` looping six roots
alongside a booting `start` is exactly this shape. New tests:
`tests/test_qa_ensure_header_read.py` — `test_a_zero_byte_database_is_not_a_permanent_wedge`
and `test_a_concurrent_reader_never_sees_a_half_created_database`, both failing
on both interpreters.

**The fix is small and the line is already understood:** an *empty* file is not
corrupt — it is what SQLite itself calls a new database, so treat length 0 as
version 0. A *non-empty* file with a bad magic stays `blocked` (F18, never
auto-delete). Both halves are pinned by passing tests in the same module so a
fix cannot overshoot.

### 0.2 The staleness claim — probed, not accepted, and it is subtler than documented

I built the case the builder reasoned about instead of taking the reasoning: a
process that commits a `user_version` bump in WAL mode and then `os._exit(0)`
without closing. On **both** interpreters the header then reads `7` while the
database's true `user_version` is `42` — and with the writer dead, that is not
"transient", it persists on disk until something opens the file with SQLite
again. The docstring attributes the effect to "a live concurrent WRITER" and
calls it transient; both words are wrong.

**But the consequence is bounded and I am not blocking on it.** The apply path
reads the PRAGMA through SQLite, not the header (`ensure.py:596` vs `:514`), so
it always sees truth, and its `close()` heals the header. Verified end to end:

```
status after crash-stale header:  ok  v1     (under-reports)
apply=True sees:                  ok  v42    (truth)
status after heal:                ahead v42  (correct)
```

The read path can only ever *under*-report, which yields `ok`/`pending` — never
a skipped migration, never a wrong write. Filed as **QA-14 (LOW)** with a pinned
test asserting the *direction* of the error (never ahead of truth) rather than
that staleness always occurs; whether it does depends on when SQLite happened to
checkpoint, and a QA test may not be a coin flip. **The docstring should be
corrected** — it currently tells the next maintainer this cannot persist.

### 0.3 QA-10 / QA-11 (round 4) — two more escapes from "never silence"

Asked to construct a fifth mechanism that still slips through. Two do:

- **QA-10 (MEDIUM).** `evaluate_all`'s per-key `try/except` catches a predicate
  that *raises* (QA-6, now correctly tiered) but not one that returns the wrong
  *type*. A `None` return sails through; the `AttributeError` lands later, in
  `to_json` or in `doctor.check_provisioning`'s render loop, both inside an
  **outer** try that swallows the entire section. Measured in `doctor`: with one
  such mechanism registered, the run aborts partway and **neither
  `relational_store` nor `vector_backend` is evaluated or recorded** — the two
  mechanisms this sprint exists for. Output is one vague line
  (`provisioning check failed: AttributeError`), and if the surviving checks are
  healthy, `doctor` exits 0. QA-7 was locked at the front door; this is one
  registration silencing all the others.
- **QA-11 (LOW, cosmetic).** A predicate may *return* an `Assertion` carrying a
  different mechanism's `key`, producing two rows for one mechanism — one of
  them healthy — in the table and in `arail.provisioning/v1`. It cannot flip an
  exit code (`_FINDINGS` is a list, `degraded` is `any(...)`, so the genuine
  failing row still degrades), which is the only reason it is LOW.

### 0.4 QA-8 sweep — verified by execution, not by reading

The builder's sweep is correct and the drivers now genuinely exercise this
branch. Proved rather than argued — inside a harness-built fake repo, through
the same `source .venv/bin/activate` subshell shape `status.sh`, `install.sh`
and `start.sh` all use:

```
python3 -c 'import arail.dbspec.ensure as e; print(e.__file__)'
 -> …/worktrees/eloquent-lederberg-6aeb3b/src/arail/dbspec/ensure.py
python  -c … (the interpreter start.sh/install.sh actually call)
 -> …/worktrees/eloquent-lederberg-6aeb3b/src/arail/dbspec/ensure.py
```

Both spellings resolve to the branch, not the main checkout. I also confirmed
the claim about which embedded-python blocks import `arail` at all: only
`status.sh:569` (`ensure_db`) and `start.sh`'s three `world_mount` imports;
every other `python3 -c` in `status.sh`, `install.sh` and `instances.sh` is
stdlib-only (`json`, `datetime`, `sys`). `install.sh` and `start.sh` reach
`ensure` via `python -m arail.dbspec.ensure` inside the activated venv, which
the same `PYTHONPATH` pin covers.

### 0.5 QA-13 (MEDIUM) — ASK-6's remaining half: the human view

The `--json` half is fixed and the controls hold: A0 (tampered ledger, nothing
running → exit 4, `root.db.state=diverged`), A1 (live instance with a real data
root → exit 3 + `instance:ai:db:diverged`), A3 (`atlas.sum` deleted), A4
(unlisted migration) all pass. A2 now exits **3** with `diverged` in
`verdict.reasons` — previously 0 and silent.

What still fails is A2's third assertion, and I checked it in an independent
topology so the driver's own killed-PID confound could not explain it. On a
tampered checkout with **nothing running**:

```
--json : root.db.state = "diverged", detail names the hash mismatch
human  : nothing.  "root lab: not running — ./arailctl start"
```

`status.sh` gates the human `db:` line on `state == "live"` / `root_is_live`,
but §4.4 specifies it is printed "only when the lab is up **or the state is not
`ok`**". This is pre-existing shape, not a round-4 regression, and it is
mitigated: `start` warns on `diverged` at boot (`start.sh:747-756`), which is
the moment the SQL would actually be replayed, and the exit code is a truthful
`4` (nothing is running). **My recommendation is to file this, not to fix it in
round 5** — but the contract text and the code should be made to agree either
way, because right now one of them is wrong.

### 0.6 Round-4 numbers

| | |
|---|---|
| Sprint-scope Python, operator `.venv` | **738 passed, 3 failed** (QA-12a, QA-10, QA-11) |
| Same, system `python3` (SQLite 3.51.0) | same 3, plus QA-12b's race reproducing 1–2 of 6 parametrized attempts |
| Previously failing round-3 tests | **all 6 now pass on both interpreters** |
| Shell drivers | `status_driver` 16/16 ✅ · `qa_db_collector_driver` 7/7 ✅ · `qa_db_seamless_driver` **9/9 ✅** (S5 fixed) · `qa_db_ledger_driver` 5/6 (A2 human view) |
| `bash -n` | clean on all modified shell scripts, confirmed |
| Operator's lab | **byte-identical** before/after (21,659-entry stat listing, empty `diff`); zero `arail.db` anywhere under it; venv `site-packages` still 454 entries. No `instance.env` anomaly this round |

### 0.7 What I would need to see to sign this off

Round 5 is small — two code changes and two decisions:

1. **QA-12** (blocking) — treat a zero-length `arail.db` as version 0; keep
   `blocked` for a non-empty file with a bad magic. Consider also moving the
   version read inside `_apply_lock` so QA-12b closes structurally rather than
   by luck. Both tests are written and will flip.
2. **QA-10** (blocking, 3 lines) — validate the predicate's return type inside
   the per-key `try`, so one malformed mechanism cannot silence the rest.
3. **QA-14** (docs) — correct the staleness docstring: after a crash it is
   persistent, not transient, and the healing agent is the next `apply=True`.
4. **QA-11, QA-13** — file. Neither is worth another build round.

**Honest read on shippability.** The data-safety boundary — which SQL runs
automatically at boot — has now survived four rounds of deliberate attack and I
have not breached it: 600 generated migrations against a row-equality oracle,
seven known bypasses closed, four ledger-tampering shapes refusing to create a
database. That is the part that could hurt someone, and it is solid. Everything
still open is a *reporting or robustness* defect. The one I will not wave
through is QA-12: "your lab is permanently blocked and no verb fixes it" is a
worse operator experience than the silent-store bug this sprint set out to fix,
and it did not exist before round 4. Fix that one, file the rest, and I expect
to return PASS or WEAK_PASS on round 5 without a fifth round after it.

---

## 1. Round 3 — the original FAIL (preserved)

Four findings block merge. Two of them are the sprint's own thesis recurring
inside the code written to prevent it, and one — ASK-6 — the architect
predicted analytically in round 3 and is now reproduced by execution.

This pass was assembled after a crash interrupted an earlier QA run. The eight
files it left on disk were read, verified, finished and committed rather than
restarted; every finding below is reproduced from a clean run of the committed
tests.

---

## 1a. Round-3 headline

| | |
|---|---|
| Python tests, sprint scope, operator `.venv` (SQLite 3.53.4) | **718 run, 1 pre-existing builder test failing** (`test_user_version_ahead_of_ledger`) |
| New QA Python tests | **663 run, 6 failing — all six are real defects, none is a bad test** |
| Shell drivers, operator `.venv` | `status_driver` 16/16 ✅ · `qa_db_collector_driver` 7/7 ✅ · `qa_db_seamless_driver` 9/10 ❌ · `qa_db_ledger_driver` 5/8 ❌ |
| CI-feasibility of `status_driver.sh` (merge blocker) | **Settled: a CI-runnable path exists.** Fixed in this pass; see §5 |
| ASK-6 | **Real. Reproduced by execution.** See QA-2 |
| Operator's real lab mutated | **No** — byte-identical across all four driver runs (§7) |
| Full suite, operator `.venv` | 5344 passed / 68 failed / 32 skipped / 7 errors (11m55s). **61 of the 68 are pre-existing**, proven against a scratch clone of the merge-base `d5c592a` — see §9a |

---

## 2. Adjudication of the six failing QA tests

The brief asked whether each is a real defect or a bad test. **All six are real
defects.** Evidence per finding; no test was weakened.

### QA-1 (HIGH) — `apply=False` is not write-free, and on a healthy DB it can report `blocked`

Three of the six. `ensure.py:399 _read_user_version_readonly` promises

> Open strictly read-only (`mode=ro`) — never creates the file, never writes a
> byte, even a `-wal`/`-shm` sidecar.

`db.connect` puts the database in WAL journal mode (`db.py:58`), and a
`mode=ro` open of a WAL database is not a pure read. Measured, both directions:

```
SQLite 3.53.4 (operator .venv):  apply=False on a healthy db -> creates arail.db-wal, arail.db-shm
SQLite 3.51.0 (system python3):  apply=False on a healthy db -> state='blocked',
                                 detail='unable to open database file'
```

Reproduce in one line each:

```
PYTHONPATH=src .venv/bin/python -m pytest tests/test_qa_ensure_write_free.py
PYTHONPATH=src      python3      -m pytest tests/test_qa_ensure_write_free.py
```

Three independent confirmations that this is the code, not the test:

1. **The builder's own test fails on the operator's interpreter.**
   `tests/test_dbspec_ensure.py::test_user_version_ahead_of_ledger` — written
   by the builder, not by QA — fails under `.venv/bin/python` with
   `Extra items in the left set: ('arail.db-shm', 32768), ('arail.db-wal', 0)`.
   It passes on the worktree's system python only because that SQLite fails the
   open instead of writing.
2. **The CLI reproduces it end to end.** `qa_db_seamless_driver.sh` S5 (F6/F19:
   "status and doctor leave the tree byte-identical") fails with
   `status modified the tree:` and five new `arail.db-wal` + five new
   `arail.db-shm` files, one pair per instance data dir. This is *exactly* the
   defect §4.1 was written against ("a read-only check that creates the thing
   it is checking is the exact bug `doctor` hit in the previous sprint").
3. **`blocked` is a degrading state.** It is in `status.sh`'s
   `_DB_DEGRADING_STATES`, so a perfectly healthy database in a data dir the
   process cannot write degrades a live lab to exit 3 and tells the operator to
   run `./arailctl doctor`. Verified directly: a healthy DB in a `0o500` dir
   reports `blocked: attempt to write a readonly database` (3.53) /
   `unable to open database file` (3.51).

Real triggers: a data dir on a read-only mount; a restore that copies only
`arail.db` (the documented way to back up an idle SQLite database — i.e.
*without* the sidecars); a lab created under one SQLite and inspected under
another.

**Why three review rounds missed it:** the architect's round-2 execution proof
ran `apply=True` and then `apply=False` in the same fixture, where `-wal`/`-shm`
were *already present* from the write path (they persist — `ensure_db` leaves
its connection unclosed, so SQLite never removes them). Nothing new appeared, so
the check passed. Delete the sidecars — which any clean process exit, backup, or
fresh restore does — and the promise breaks.

**Suggested fix (builder's call):** read `user_version` from bytes 60–63 of the
database header instead of opening SQLite at all. Verified equivalent
(`header user_version: 1` on a freshly ensured DB). That is a genuinely
zero-write read. `immutable=1` is *not* a safe substitute — it licenses stale
reads of a concurrently written file.

### QA-5 (MEDIUM) — the class check only ever looks at one root

`doctor.check_provisioning` calls
`evaluate_all(repo_root=…, data_dir=str(config.DATA_DIR))` — the **root lab's**
data dir, one value — and `check_relational_store` takes a single `data_dir`.
Reproduced: root lab provisioned, five World instances on disk with no database
at all, `resolve_data_dirs` returning all six —

```
relational_store: instantiated=True, detail='ok'   (5 of 6 roots have no arail.db)
```

This is §4.3's own six-roots defect recurring *inside* §5's class check, on the
operator's measured usage pattern (one World at a time, root lab never started).
Mitigation that keeps it MEDIUM rather than HIGH: `status.sh` does iterate
`inst_resolve_data_dirs` and does report per-root db state, so the fact is not
invisible product-wide — but `doctor`, the surface this sprint built to make
"declared and not instantiated" impossible to miss, misses it.

### QA-6 (MEDIUM) — a *required* check that crashes is silently demoted to *info*

`provisioning.evaluate_all`'s except-handler hardcodes the tier:

```python
out.append(Assertion(key, "info", True, False, f"check raised …", ""))
```

So a mechanism registered `required` whose predicate raises — an `ImportError`
from a broken dependency, an `OSError` on an unreadable data dir, a bug in the
predicate — becomes an `info` finding, and `doctor` exits 0 on it. The
mechanism most likely to be genuinely broken is the one whose check blew up, and
that is precisely the case whose exit code nobody reads. The tier belongs to the
mechanism, not to the outcome of evaluating it — `register()` should carry it.

### QA-7 (LOW) — `register()` silently replaces a built-in

`_REGISTRY[key] = fn` on a plain dict. A 2.1 mechanism, a plugin, or a bad merge
that reuses a key silently replaces the built-in predicate — including replacing
a real check with one that always reports healthy. Demonstrated: overwriting
`relational_store` with a "everything is fine, trust me" predicate takes effect
with no error and no warning. The registry that exists to make omissions loud
has a silent overwrite at its front door.

---

## 3. QA-2 — ASK-6 is REAL (HIGH, merge blocker)

Confirmed by execution, not by reading. `tests/cli/qa_db_ledger_driver.sh`
scenario A2: a **tampered migration ledger** (committed SQL altered so
`_verify_ledger` yields `diverged`) plus one live World instance whose data root
is missing:

```
FAIL: A2/ASK-6: expected exit 3 on a tampered ledger with a live lab, got 0
FAIL: A2/ASK-6: a TAMPERED ledger produced NO verdict reason — the whole db
      object was suppressed and with it the checkout-global 'diverged'
FAIL: A2/ASK-6: the HUMAN status view never mentions the diverged ledger
```

An operator running `./arailctl status` on a checkout whose committed SQL has
been altered is told **everything is fine, exit 0**. `diverged` is BLOCK-2's
condition and is a fact about the *checkout*, not about the missing directory;
BLOCK-5's suppression is keyed on the `data_root_missing` flag rather than on
the state, so it swallows `diverged` along with the benign `pending`.

The controls in the same driver pass, which is what makes this signal:
A0 (tampered ledger, nothing running → stays exit 4), A1 (tampered ledger, live
instance *with* a data root → exit 3 + `diverged`), A3 (`atlas.sum` deleted),
A4 (unlisted extra migration) all behave correctly.

The architect's recommended one-line fix — suppress only the states *derived
from* the missing directory (`pending`, `unavailable`) and let
`diverged`/`ahead`/`blocked` through — is what A2 asserts. The test is written
against the correct behaviour and will pass when the fix lands.

**Security framing (this sprint's 20% is data safety):** this is the one
reporting path where altered, auto-replayable SQL in the checkout produces a
clean bill of health. It does not itself apply the SQL — `ensure_db` still
refuses and never creates the file — but the operator is told nothing.

---

## 4. What held up under attack

Reported at least as prominently as the failures, because these were the
highest-risk surfaces.

**The statement-splitting allowlist held.** REVIEW3 said "assume a third
bypass exists." I did not find one. `tests/test_qa_ensure_statement_safety.py`
generates **600 candidates** — 28 destructive and 8 benign statement forms drawn
from SQLite's grammar, crossed with 15 lexical wrappers (semicolons inside line
and block comments, quoted identifiers containing `;`, string literals
containing `;`, no trailing semicolon, doubled semicolons, tabs, case folding,
statements before/after safe DDL), plus 60 deterministically seeded
multi-statement compositions. The oracle is not the classifier's opinion: each
candidate is applied as a real migration to a seeded database and **every
pre-existing row is compared for row-for-row equality**. Zero SAFE-FORWARD
classifications mutated a pre-existing row. All seven known bypasses stay
closed: `ALTER TABLE t DROP c`, `ALTER TABLE t DROP COLUMN c`, `REPLACE INTO`,
`INSERT OR REPLACE INTO`, `UPDATE OR REPLACE`, `DROP VIEW`, `DROP TRIGGER`, and
`INSERT … ON CONFLICT DO UPDATE SET` — plus `CREATE TEMP TABLE`,
`CREATE VIRTUAL TABLE … fts5`, `PRAGMA writable_schema`, `ATTACH DATABASE`,
`VACUUM`, and a data-modifying CTE.

**The deliberate collector-kill passes 7/7** (REVIEW3's "single most valuable
test"). Broken caller-side only — the fake venv's `python3` *symlink* is removed
(never followed) and replaced by a stub that `exec`s the real interpreter with a
`sitecustomize.py` installing a `sys.meta_path` finder that raises
`ModuleNotFoundError` for `arail.dbspec.ensure`. Nothing was written through
`make_fake_venv`'s `.venv/lib` symlink. Asserted and passing: a real traceback
surfaces in `warnings`; `db:collector-failed` lands in `verdict.reasons`; a live
root lab exits 3; a **live World instance with the root lab never started** also
exits 3 (the operator's real usage pattern); nothing running stays **4 and is
never promoted to 3**; the human renderer names the cause, not just `--json`;
`--no-probe` is not an escape hatch; and the failed collector never creates the
database it could not check. A control scenario proves an unbroken collector
reports none of it.

**Ledger tampering holds** (5 of 8 scenarios; the 3 failures are all ASK-6):
tampered migration, deleted `atlas.sum`, corrupt `atlas.sum`, unlisted extra
migration — every one yields `diverged` **with the database file never
created**, and the filename gate (`^\d{14}_[a-z0-9_]+\.sql$`) refuses
traversal-shaped and off-pattern names without making the ledger diverge.

**Seamless, six roots, isolation: 9 of 10.** Fresh checkout → `install` over an
**empty `registry.d` with five on-disk instances** creates 6 databases;
`status` reports the unregistered ones as `origin=ondisk` findings rather than
skipping them; no `arail.db` at `lab/` or the repo root (F5); a second
`install` is quiet and idempotent; a second `start` prints no `db:` line (F10);
starting the root lab touches no sibling data dir (F4); and no code path in the
sprint reads, writes or enumerates any `secrets.env`. The only failure is S5 —
QA-1.

**Cross-process concurrency holds.** Two concurrent `ensure_db(apply=True)`
processes on one data dir: both end healthy, the database passes
`PRAGMA integrity_check`, a stale lock file does not wedge a later run, and the
lock file never escapes the data dir.

**Defect B's honesty fix holds.** With the backend absent, the *first* search in
a fresh process is already stamped degraded (no warm-up window), the
`X-Retrieval-Status` header names a fix the operator can act on, a successful
`available()` observation clears **only** the `backend` code (F12 discipline
intact), a second failing search does not clear it, and `retrieve_for_agents`
never labels a keyword hit `semantic`. A hostile `sys.executable` cannot inject
a response header (CRLF containment).

---

## 5. CI-feasibility ruling (the merge blocker) — and QA-8, what settled it

**Ruling: a CI-runnable path exists, and it is one line. It is applied in this
pass. `status_driver.sh` now runs green, 16/16, unattended.**

Getting there surfaced a finding of its own, and it is the most important thing
I learned this sprint:

### QA-8 (HIGH, harness — fixed here) — the drivers were testing the wrong source tree

`make_fake_venv` symlinks the discovered venv's `site-packages` into every fake
repo. The only usable venv on this machine is the operator's **main checkout's**
`.venv`, whose `__editable__.arail-1.0.0.pth` contains

```
/Users/netsushi/ProJects/qukaizen-arail/src
```

— the **`main` checkout**, which is on branch `main` and contains neither
`src/arail/dbspec/ensure.py` nor `src/arail/provisioning.py`. So a driver run
from a worktree exercised `main`'s `arail`, not the branch's. Measured:

```
$ ARAIL_TEST_VENV=…/.venv bash tests/cli/status_driver.sh
FAIL: T10: expected exit 0, got 3
  ⚠ db: … ModuleNotFoundError: No module named 'arail.dbspec.ensure'
```

Every DB assertion in every driver was reachable in a state where it could pass
or fail for reasons having nothing to do with the code under review. This is the
literal mechanism of the accusation REVIEW.md §8 made against this layer twice.

Fix, in `tests/cli/lib.sh` beside `REAL_VENV`:

```sh
export PYTHONPATH="$CLI_TEST_REPO/src${PYTHONPATH:+:$PYTHONPATH}"
```

Prepended, so a driver that deliberately shadows a module (`qa_db_ledger_driver.sh`)
still wins. With it, `status_driver.sh` goes from FAIL to **16/16 with no
operator intervention**, and the collector driver's control scenario — which had
been failing, invalidating the entire driver's signal — passes.

**What CI needs, concretely:** a checkout, `python -m venv .venv`,
`pip install -e .`, then `bash tests/cli/status_driver.sh`. In that topology
`REAL_VENV` is the checkout's own venv and the editable path already points at
the code under test, so CI would have worked without the fix — the blind spot is
specific to *worktree* review, which is exactly how all three rounds of this
sprint were reviewed. Both halves matter: CI is achievable **and** the line above
is what stops a green driver from meaning nothing on a reviewer's machine.

**Recommendation:** add the four `qa_db_*` drivers plus `status_driver.sh` to
the CI workflow. Nothing in them needs a GPU, a model, or the network; the
longest is ~40 s.

---

## 6. Test inventory

| # | Test | Category | Covers | Status |
|---|---|---|---|---|
| 1 | `qa_ensure_statement_safety`: 600-candidate grammar fuzz, row-equality oracle | security (data safety) | F3, BLOCK-1, BLOCK-4, "assume a third" | ✅ |
| 2 | …: every known destructive form in isolation | regression | 7 closed bypasses | ✅ |
| 3 | …: a lossy statement anywhere blocks the whole file | security | F3 | ✅ |
| 4 | …: a lossy migration leaves later ones unapplied | edge | F2 | ✅ |
| 5 | …: `ADD COLUMN … ON DELETE CASCADE` changes no existing row | edge | REVIEW3 residual | ✅ |
| 6 | …: commented-out DDL after an in-comment `;` is still executed | edge | splitter has no lexer | ✅ *(documented finding QA-9, fails closed)* |
| 7 | …: tampered / missing / corrupt `atlas.sum`, unlisted migration | security | ledger integrity; DB never created | ✅ |
| 8 | …: migration filename gate rejects traversal-shaped names | security | test 36 | ✅ |
| 9 | …: a deleted committed migration is not detected | edge | **QA-3 (MEDIUM)** — pinned as current behaviour | ⚠️ finding |
| 10 | …: `status` calls a pending run safe when a later file is lossy | edge | **QA-4 (LOW)** — wrong advice | ⚠️ finding |
| 11 | `qa_ensure_write_free`: apply=False on a healthy DB | happy | §4.1, F6 | ❌ **QA-1** |
| 12 | …: apply=False on an AHEAD DB | edge | §4.1, F6 | ❌ **QA-1** |
| 13 | …: healthy DB in a read-only data dir | edge | consequence: false `blocked` → exit 3 | ❌ **QA-1** |
| 14 | …: apply=False never creates the DB / never creates the data dir | happy | F6 | ✅ |
| 15 | `qa_ensure_concurrency`: 2 processes, one data dir | concurrency | F17 | ✅ |
| 16 | …: `PRAGMA integrity_check` after concurrent appliers | concurrency | F17, corruption | ✅ |
| 17 | …: stale lock file does not wedge a later run | edge | `_apply_lock` | ✅ |
| 18 | …: lock file never escapes the data dir | security | F19 | ✅ |
| 19 | `qa_retrieval_honesty`: first search in a fresh process is already degraded | Buddy | defect B | ✅ |
| 20 | …: header names an actionable fix; control has no header | Buddy | §4.7 | ✅ |
| 21 | …: empty query is not reported as a retrieval failure | edge | false-positive honesty | ✅ |
| 22 | …: hostile interpreter path cannot inject a response header | security | CRLF | ✅ |
| 23 | …: `available()` clears only `backend`; failure survives a 2nd search | regression | F12 | ✅ |
| 24 | …: `retrieve_for_agents` never labels a keyword hit `semantic` | Buddy | honesty | ✅ |
| 25 | `qa_provisioning_generalize`: a fifth, unrelated mechanism is caught | happy | §5 generalization | ✅ |
| 26 | …: an "off" mechanism stays silent; JSON shape stable | happy | three-state rule | ✅ |
| 27 | …: a crashing *required* check stays required | edge | **QA-6** | ❌ |
| 28 | …: duplicate `register()` does not replace a built-in | security | **QA-7** | ❌ |
| 29 | …: `relational_store` asserted for every resolved root | setup | **QA-5** | ❌ |
| 30 | `qa_db_collector_driver` C0–C6 | setup / regression | BLOCK-3, exit-code contract | ✅ 7/7 |
| 31 | `qa_db_ledger_driver` A0, A1, A3, A4 | security | ledger integrity vs. live labs | ✅ |
| 32 | `qa_db_ledger_driver` A2 (ASK-6) ×3 assertions | security | **QA-2** | ❌ |
| 33 | `qa_db_seamless_driver` S1–S4, S6–S9 | setup | six roots, F4, F5, F10, secrets | ✅ |
| 34 | `qa_db_seamless_driver` S5 | setup | F6/F19 tree-immutability | ❌ **QA-1** |
| 35 | `status_driver` T3/T8/T10–T12/T27–T29/T34/F2/F18/F20 | regression | existing contract | ✅ 16/16 |

Allocation achieved: ~30% setup (drivers, six roots, fresh-clone), ~20% Buddy
(retrieval honesty), ~30% security/data-safety (the fuzz corpus, ledger
tampering, secrets, header injection), ~10% happy, ~10% regression. Buddy is
under the 30% target — see §9.

---

## 7. Security review

| Surface | What I actually checked | Findings |
|---|---|---|
| SQL executed automatically at boot | 600 generated migrations applied against a seeded DB, comparing every pre-existing row before/after. The allowlist (`CREATE TABLE`/`INDEX`/`VIEW`, `ALTER TABLE ADD COLUMN`) admits nothing that mutates existing data, through 15 lexical wrappers designed to break a naive `;` split | none — held |
| Migration ledger integrity | Tampered file body, deleted `atlas.sum`, unparseable `atlas.sum`, unlisted extra `.sql` dropped in (bad merge / attacker). All → `diverged`, database **never created**, nothing executed | QA-2 (reporting), QA-3 (deletion not detected) |
| Path traversal in migration names | `..%2f..%2fetc%2fpasswd.sql`, `00_x.sql`, uppercase variants, short timestamps — all ignored by the `^\d{14}_[a-z0-9_]+\.sql$` gate, and their absence from `atlas.sum` does not falsely diverge the ledger | none |
| File I/O scope | `ensure` writes only inside `data_dir`; the `_apply_lock` file is inside the data dir; the repo tree (incl. `spec/`) is hash-identical after install+start (F19); no `os.remove` of a database on any path | QA-1 (writes on the *read* path) |
| Per-instance secrets | Grepped the sprint's whole diff surface and asserted in `qa_db_seamless_driver` S9 that no code path reads, writes, copies or enumerates any `secrets.env`; no data dir is shared or symlinked between roots; each root gets its own `arail.db` | none |
| Header injection | `X-Retrieval-Status` built from a message containing `sys.executable`; asserted a hostile interpreter path with CR/LF cannot split the response | none |
| Deserialization | The sprint parses `atlas.sum` (line-based, hash-compared) and registry JSON via `json.load`. No `pickle`, no `yaml.load`, no `eval` anywhere in the diff | none |
| Crypto | No new crypto. `atlas.sum` uses SHA-256 (`h1:` base64 digests), compared with a plain `==` — acceptable: this is an integrity check against accidental/committed drift, not a secret comparison, and a timing side channel on a locally readable file grants nothing | none |
| Privilege / exit-code contract | A broken collector can never promote a not-running lab's `4` to `3`; a read-only data dir must not be treated as a security failure (it currently degrades — QA-1) | QA-1 |
| Dependencies | None added by this sprint | none |

---

## 8. Operator-lab safety (constraint 1)

Explicitly checked, because the brief required it.

- Every driver run used `LAB_ROOT`/fake-repo fixtures under `mktemp -d`. No
  `install.sh` or `start.sh` was ever pointed at `/Users/netsushi/ProJects/qukaizen-arail`.
- Full stat listing of the operator's `lab/` tree (21,659 entries: name, size,
  mtime) captured before and after **all four** driver runs:
  **`diff` is empty — byte-for-byte identical.**
- `find lab -name 'arail.db*'` on the operator's checkout: **no matches**. This
  sprint's code has never created a database there.
- The real venv's `site-packages` still has **454 entries**; nothing was written
  through `make_fake_venv`'s `.venv/lib` symlink. The collector-kill breaks the
  import from the caller side (stub `python3` + `sitecustomize` meta-path
  finder), per the warning at that line in `lib.sh`.
- No bare `git stash` was used; every git operation named explicit paths.
- **One anomaly, reported rather than smoothed over:** the four
  `lab/instances/*/instance.env` files show `mtime == ctime == 13:14:39`, 16
  seconds after my first snapshot, which is inside the window of my first
  (pre-fix, early-aborting) collector-driver run. I could not reproduce it: a
  clean re-run of that driver, and of all four, leaves the tree identical, and
  no file under `lab/` has an mtime later than 13:14:39. Twelve worktrees exist
  under `.claude/worktrees/` and a concurrent session wrote
  `lab/.opencode/` in this worktree at 13:30, so a parallel session is the
  likelier author. Contents are intact and plausible (sizes 719–802 B,
  birth times from the original instance creations). Flagging it because "I
  cannot prove which process touched it" is the honest state, and the operator
  should know.

---

## 9a. Regression baseline — the full suite's other 61 failures

Running the **full** suite (not just the new tests) surfaced 68 failures. Rather
than wave at them, I established a baseline: a scratch clone at the merge-base
`d5c592a` in `/tmp` (never the operator's checkout), same interpreter, same
subset of the 13 most sprint-adjacent failing files — `world_forge_api`,
`w9_embedder_swap`, `loader_skills_only_agents`, `docs_ingest`, `cache_prewarm`,
`autochecks_boot`, `reset_stop_scope`, `instance_isolation_audit`,
`shell_source_safety`, `tests/portal/`, and the model/chat files:

```
merge-base d5c592a : 27 failed, 427 passed, 7 errors
branch HEAD        : 23 failed, 431 passed, 7 errors
```

**The branch introduces no regression in that set and fixes four.** The
sprint-owned failures are exactly the ones enumerated in §10 —
`test_qa_ensure_write_free` (3), `test_qa_provisioning_generalize` (3), and
`test_dbspec_ensure::test_user_version_ahead_of_ledger` (1, the builder's own).
The remaining ~61 are pre-existing debt on `main` and are out of this sprint's
scope; they should not be used to argue this branch is worse or better than it
is.

## 9. Performance

N/A as a formal benchmark. Two budget assertions from §4.4/§4.5 were observed
rather than measured statistically: the DB check is a local file read and
`status_driver.sh`'s existing timing scenario (F20, <2 s over the fixture)
passes 16/16 with the DB collector in the path, and the seamless driver's
second-`start` scenario shows no added boot output. No BENCHMARK.md — nothing
here is on the inference or retrieval hot path (`ensure` imports neither
`lancedb` nor the embedder, asserted).

---

## 10. Findings table

| # | Test | Symptom | Minimal repro | Severity |
|---|---|---|---|---|
| QA-1 | `qa_ensure_write_free` ×3, `qa_db_seamless` S5, `test_dbspec_ensure::test_user_version_ahead_of_ledger` | `apply=False` writes `-wal`/`-shm` (SQLite ≥3.53) or returns `blocked` on a healthy DB (SQLite 3.51); `status` degrades a fine lab to exit 3 | `ensure_db(d, apply=True)`; delete `arail.db-wal`/`-shm`; `ensure_db(d, apply=False)` | **HIGH** |
| QA-2 | `qa_db_ledger_driver` A2 (ASK-6) | Tampered ledger + live instance with a missing data root → exit **0**, no verdict reason, human view silent | driver scenario A2 | **HIGH** |
| QA-3 | `..._statement_safety::test_deleting_a_committed_migration_is_not_detected` | `_verify_ledger` checks disk→ledger but never ledger→disk; delete migration 1 of 2 and the ledger still verifies, `user_version` silently means something else | 2 migrations, `unlink` the first, `ensure_db(apply=True)` → `created`, v1, table `one` absent | MEDIUM |
| QA-5 | `..._provisioning_generalize::test_relational_store_is_asserted_for_every_resolved_root` | The class check asserts one root; 5 of 6 roots with no DB report `relational_store: OK` | provision root lab only, 5 instance dirs, `check_relational_store(...)` | MEDIUM |
| QA-6 | `...::test_a_required_check_that_raises_stays_required` | A `required` check whose predicate raises is demoted to `info`; `doctor` exits 0 | `register("k", lambda **kw: 1/0)`; `evaluate_all()` | MEDIUM |
| QA-8 | `status_driver` (pre-fix) | Drivers imported `main`'s `arail`, not the branch's — green could mean nothing | run any driver from a worktree with the main checkout's `.venv` | HIGH *(fixed in this pass)* |
| QA-4 | `...::test_status_calls_a_pending_run_safe_when_a_later_file_is_lossy` | `status` says "pending — run install"; `install` actually ends `blocked` | 2 migrations, 2nd `DROP TABLE`; `apply=False` then `apply=True` | LOW |
| QA-7 | `...::test_registering_a_duplicate_key_does_not_silently_replace_a_builtin` | `register()` silently overwrites a built-in predicate | re-register `relational_store` | LOW |
| QA-9 | `...::test_commented_out_allowlisted_ddl_is_still_executed` | A `;` inside a line comment ends the comment for the splitter; commented-out DDL executes | `CREATE TABLE a (x);\n-- disabled; CREATE TABLE ghost (y)\n` | LOW (fails closed) |

QA-3, QA-4 and QA-9 are asserted as *current* behaviour with a comment saying
which way to invert them when fixed, so the suite stays green on the branch
while the repro stays executable. QA-1, QA-2, QA-5, QA-6, QA-7 are asserted
against the **correct** behaviour and fail today. That is deliberate: they are
the FAIL.

---

## 11. Required before this can go back to review

1. **QA-1** — make the read path genuinely write-free (header-byte read of
   `user_version` recommended). Then `tests/test_dbspec_ensure.py::test_user_version_ahead_of_ledger`
   and `qa_db_seamless_driver.sh` S5 go green on the operator's interpreter too.
2. **QA-2 / ASK-6** — key suppression on the db *state*, not the
   `data_root_missing` flag.
3. **QA-5** — `check_relational_store` over every row of `resolve_data_dirs()`.
4. **QA-6** — carry the tier on the registration, not the exception handler.
5. **QA-3** — `_verify_ledger` should also assert every filename `atlas.sum`
   lists still exists.
6. QA-4, QA-7, QA-9 — fix or file with an explicit decision.

QA-8 is fixed here; the CI merge blocker is discharged by §5 and needs only the
workflow entry.

---

## 12. Notes for the next QA pass

- **Never trust a shell driver run from a worktree again without checking which
  `arail` it imported.** QA-8 is the third time this layer hid something. The
  `lib.sh` line closes it, but the *class* — a fixture that resolves a
  dependency from outside the code under test — deserves a standing check. A
  driver that asserted `python3 -c "import arail.dbspec.ensure"` in its control
  scenario would have caught it in round 1.
- **Two SQLite versions, two opposite symptoms, one defect.** Every future
  contract assertion about "no writes" should be run under at least the system
  python *and* the venv python. The suite currently silently means different
  things on each.
- **Under-tested:** the Buddy half (allocation target 30%, achieved ~20%).
  Nothing here exercises Buddy end-to-end through `search_for_agents` on a
  *provisioned* lab with the DB present — the win condition's positive half rests
  on the architect's manual measurement (12 semantic hits, gated) rather than on
  a committed test. That is the gap I would close first.
- `doctor`'s own exit-code path is asserted only indirectly, through
  `provisioning` unit tests. A `doctor_driver.sh` sibling to `status_driver.sh`
  does not exist and should.
- The `.arail_ensure_state.json` sidecar next to each database is untested by
  anything in this sprint. Nothing reads it yet — which is precisely the
  "declared and not instantiated" shape this sprint is about.
