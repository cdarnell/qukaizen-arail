# Review 4: BLOCK-3 remediation + ORCH-1

**Date:** 2026-08-08
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) build4/build5
**Reviewed commits:** `548c204`..`4718f15` — `1382c69` (BLOCK-3) · `8af64a3` (also-fixes) ·
`6d77ac6` (log) · `8dcc335`/`29a2f61` (ORCH-1) · `4718f15` (ledger)
**Prior:** [REVIEW3.md](./REVIEW3.md) (BLOCK) · [REVIEW2.md](./REVIEW2.md) (BLOCK) ·
[REVIEW.md](./REVIEW.md) · [ARCHITECTURE.md](./ARCHITECTURE.md)

## Verdict: WEAK_PASS

Every defect I have blocked on is dead, and I killed each one by execution against the
real code, not by reading the diff. BLOCK-1, BLOCK-2 and BLOCK-3 do not reproduce, and
they fail to reproduce for the right reason each time. ORCH-1 is genuinely fixed — all
four invariants I was asked to check hold, including the two the coordinator did not
name (root isolation both directions, and second-build idempotence).

I am not issuing a third BLOCK. Two new findings came out of the pattern hunt; both are
real, both are one-line-to-ten-line fixes, and neither is reachable through a shipped
call path. Neither meets my own bar of "fired on first contact with the operator's real
data" — which is what BLOCK-1, BLOCK-2 and BLOCK-3 each did.

WEAK_PASS rather than PASS for three reasons, in descending weight:

1. REVIEW3's required action #3 (`_table()` opened three times per query) was
   **downgraded to a backlog filing** rather than done. The deferral is well-argued and
   I accept it, but a required action that becomes a ticket is the definition of "ship
   with notes."
2. C1's Buddy context-header is still unwired. On four of the operator's five real
   Worlds, Buddy is silently keyword-only. REVIEW3 said this should not ship to a
   friend's machine in that state; I hold to that, and it is now the oldest unpaid item
   in the sprint.
3. The two new findings below need QA eyes before this reaches anyone else's machine.

### Disclosure — read-only this time

Unlike REVIEW3, this review wrote nothing to the operator's lab. Every reproduction ran
against scratch roots under the session scratchpad with `LAB_PKB` pointed at them. I
re-checked all five real Worlds' index mtimes after finishing: `ai` Aug 3, `debt-finance`
Aug 2, `qukaizen` Aug 7, `video-games` Aug 6, and `finance` Aug 8 20:02 — which is
REVIEW3's already-disclosed accidental build, unchanged. Nothing new was touched.

---

## What I executed

Real Ollama, `nomic-embed-text` @ 768d, plus a logging HTTP stub on `127.0.0.1:18434`
standing in for the embedding provider so I could count requests rather than infer them.

| # | Check | Result |
|---|---|---|
| 1 | legacy 128d root + `ensure_ready` | degrades `{dimension}`, 6/6 rows intact |
| 2 | …then `pkb.search()` | codes still `['dimension']`, `source=keyword`, `retrieval_status` False — **BLOCK-1 stays dead** |
| 3 | …then a successful `schedule_upsert` + `flush_now` | codes still `['dimension']`, 6 rows — the `_flush` half stays dead too |
| 4 | `doctor` subprocess, unindexed 40-file World, provider = logging stub | **exactly one** embed request, 5 bytes (`/api/embed`, the reachability probe). Not a corpus pass — **BLOCK-3 dead** |
| 5 | same, filesystem after | no `.cache/`, no `lancedb/`, no sidecar. Exit 0 |
| 6 | `doctor` on the legacy 128d root | exit **3**, correct message, rows still 6 — the `build=False` path did not lose doctor's teeth |
| 7 | ORCH-1: `build=False` then `build=True`, one process | 0 rows → **41 rows**; identical end state to `build=True` alone (41) — **fixed** |
| 8 | root isolation: read-only on C, build on D | C stays unindexed, D builds 41 — no cross-root suppression |
| 9 | reverse isolation: build on A, then first build on B | both build — the per-root key is real, not "any build yet" |
| 10 | second `build=True`, same root | `index_all` calls: `[]` — one-shot contract preserved, not overcorrected |
| 11 | `_pkb_root_cache` after a read-only call on a different root | **redirected** (`E` → `F`) — see ASK-1 |
| 12 | stale-lock recovery, forced interleaving | **mutual exclusion broken**: both processes hold; lock file carries the second one's PID during the first one's write phase — see ASK-2 |
| 13 | TOCTOU window width, 200 samples | median **1.3 µs**, p95 1.8 µs, max 6.5 µs — real but narrow |
| 14 | 25 touched + adjacent suites, isolated | **379 passed, 0 failed** |
| 15 | 1237-test targeted selection | 11 failed / 7 errors, all byte-identical at `8cb5760` |

---

## 1. No regression of BLOCK-1, BLOCK-2, BLOCK-3

**BLOCK-1 — dead.** Rows 1–3. A dimension code survives both a successful search and a
successful incremental flush; the search falls through to keyword and `retrieval_status()`
stays False. The one behavioural change in this area — `_semantic_search` now clearing
`"empty"` — is correctly evidence-scoped: it fires only after `idx.count() > 0` *and* a
successful `_table()` open, and it is placed before `check_read_path_health`, so a
provenance failure still returns `[]` with the right code set. That is the fix REVIEW3
asked for, done at the right place.

**BLOCK-2 — dead.** REVIEW3 verified all four sub-fixes behaviourally and none of them
was touched this pass; the shadow-count guard, the `--resume` discard, the `total == 0`
refusal and the lock acquisition are byte-identical apart from the stale-recovery
addition inside `acquire()`. That addition is ASK-2 below.

**BLOCK-3 — dead, and I measured it rather than trusting the absence of a directory.**
Pointing `MODEL_API_BASE` at a request-logging stub, a full `python -m arail.doctor`
sweep against a 40-file World with no index produced exactly one `POST /api/embed`
carrying 5 characters — the provider reachability probe. Not 40 documents, not one
batched corpus call. No `.cache/` was created. Under `LAB_MODE=hybrid` the egress
surface of `doctor` is now a 5-byte probe string, which is the correct answer.

Critically, `build=False` did not cost doctor its diagnostic value: on the legacy 128-dim
root it still exits **3** with the actionable message, because the dimension and
provenance checks both sit *above* the `if not build: return` that skips the staleness
sweep. The read-only path degrades on four distinct conditions (`empty` for no index
directory, `empty` for no table, `dimension` for missing columns, `dimension` for a dim
mismatch) and never drops, never rebuilds, never schedules. The ordering of that function
is now load-bearing in a way it wasn't before — worth a comment, since anyone moving the
provenance check below the early return silently blinds `doctor`.

## 2. ORCH-1 — genuinely fixed, not relocated

Rows 7–10. The stated invariant holds exactly: `ensure_ready(root, build=False)` followed
by `ensure_ready(root, build=True)` in one process builds the index and reaches the same
end state (41 rows) as `build=True` alone (41 rows). Root isolation holds in both
directions — a read-only check on A does not suppress a build on B, and a genuine build
on A does not suppress B's first build, which is the overcorrection I was watching for.
A second `build=True` on the same root still makes zero `index_all` calls.

The fix is the right shape: `_initialized_roots: set[Path]` keyed by `root.resolve()`,
claimed only inside `if build:`, and `_reset_for_tests` clears it. `build=False` neither
reads nor writes it. `tests/test_ensure_ready_build_isolation.py` pins all four
properties plus the doctor zero-embeds guarantee, so the fix cannot silently rot.

The builder's reachability correction is right and I want it on the record: `pkb.py`
has no `ensure_ready` reference, `build=False` has exactly one caller (`doctor`), and
`arailctl` dispatches doctor as a separate `python -m arail.doctor` process, so module
globals never span the two. I verified all four shipped `ensure_ready` call sites
(`app.py:1179` startup, `app.py:12063` voice note, `app.py:12209` OCR note,
`world_mount.py:1278` mount) and every one of them resolves its root through
`arail.config.PKB_ROOT`. Fixing an unreachable contract defect anyway was the correct
call, and correcting the coordinator with evidence rather than complying silently is the
behaviour I want from this role.

**But the fix did leave one thing behind, in the same `with _lock:` block.** See ASK-1.

## 3. The pattern hunt

`pkb_index` carries five pieces of process-wide mutable state: `_pending`, `_timer`,
`_initialized_roots`, `_pkb_root_cache`, `_degraded_codes`. One is now per-root. Four
are not.

**The containment is real, but it is an unwritten invariant.** `arail.config.PKB_ROOT` is
a module constant resolved once at import; I grepped `src/` and found **zero** in-process
rebindings of it (no `importlib.reload`, no assignment, no runtime `LAB_PKB` mutation).
Concurrent Worlds run process-per-World by design. So one process = one PKB root, always,
and every one of these globals is therefore unambiguous today. That single invariant is
what makes `_degraded_codes`, `_pending` and `_pkb_root_cache` safe — and it is written
down nowhere in `pkb_index.py`.

**Ruling on the `_degraded_codes` deferral: safe, and I am satisfied with the reasoning.**
The BACKLOG entry (line 774) is a good one — it names the mechanism, names the
survivability condition ("one process, one root, one meaningful global"), names the
operator's serial-World usage, and lists `_pending`, `_timer` and `_pkb_root_cache`
alongside `_degraded_codes` so a future sprint fixes the whole family rather than one
member. The builder also shipped a test that *reproduces* the leak rather than leaving it
theoretical. That is the right way to defer.

It is not the next instance waiting to happen, because the tripwire is architectural
rather than incidental: someone would have to introduce in-process multi-root access,
which contradicts the shipped concurrent-Worlds design. What I want is that tripwire
made visible at the code, not only in a backlog file nobody reads before editing
`pkb_index.py` — a module-header invariant note (`this module assumes one PKB root per
process; see BACKLOG`) is the whole ask.

### ASK-1 — a read-only call still mutates the process-wide write target

`ensure_ready` sets `_pkb_root_cache = root` **outside** the `if build:` guard, on the
line immediately below the ORCH-1 fix. Verified (row 11): after
`ensure_ready(E, build=True)` then `ensure_ready(F, build=False)`, `_pkb_root_cache` is
`F`. The inline comment two lines above says "`build=False` never reads or writes it" —
true of `_initialized_roots`, false of the variable on the next line.

This matters because `_flush` reads `root = _pkb_root_cache` and joins it against
`_pending`, which holds **root-relative** POSIX paths. A flipped cache means World A's
pending paths resolve under World B — writing A's content into B's index, or deleting B's
rows for files that don't exist there. That is a worse payload than ORCH-1's (a silent
no-op); it is a cross-World data write.

It is not reachable today, for exactly the same reason ORCH-1 wasn't: the only
`build=False` caller is a separate process. But the ORCH-1 fix half-removed a guard that
was doing double duty — pre-fix, `_initialized` also pinned `_pkb_root_cache` to the
first root for the life of the process; post-fix, every distinct root reassigns it. That
pin was removed without a replacement. `build=False` never reads `_pkb_root_cache`
(it uses its local `root`), so the fix is free:

```python
        _pkb_root_cache = root      →      if build:
                                               _pkb_root_cache = root
```

### ASK-2 — the new stale-lock recovery can break mutual exclusion

The stale-lock fix (`8af64a3`) reintroduces, under a narrow precondition, the defect
BLOCK-2(d) closed. Row 12: with a dead-PID lock present and the interleaving forced, two
holders run concurrently, and during the first holder's write phase the lock file
contains the *second* holder's PID.

The mechanism is a TOCTOU between `_is_stale()` and `unlink()`:

- B reads the stale PID and decides to recover.
- A recovers first: unlinks, `O_EXCL`-creates, and starts writing.
- B, still acting on its stale observation, unlinks **A's live lock** and creates its own.
- Both run. Worse, `release()` unlinks `self.path` unconditionally without checking that
  the file it is deleting is still the one it created, so A's exit removes B's lock and
  admits a third process.

Honest sizing: I measured the window at **1.3 µs median / 6.5 µs max** over 200 samples,
and it requires a pre-existing stale lock (a prior hard crash) *plus* two invocations
arriving within that window after ~1.5 s of variable interpreter startup. Order 10⁻⁶ per
double-invocation. I could only demonstrate it by widening the window 250,000×. That is
why this is an ASK and not a third BLOCK — but it is a correctness defect in the one
command the degraded-KB message tells users to run, guarded by a test
(`test_second_concurrent_run_is_refused_by_lock`) that cannot see it because it never
sets up the stale precondition.

The correct fix removes the heuristic rather than tightening it: `fcntl.flock(fd,
LOCK_EX | LOCK_NB)` on the open descriptor. The kernel drops the lock when the process
dies, so SIGKILL recovery becomes automatic and race-free, and `_read_lock_pid`,
`_pid_alive` and `_is_stale` all disappear. Failing that, `release()` must verify
ownership before unlinking, and `acquire()` must re-read the file after creating it.

One smaller note on the same code: with PID reuse, a recycled PID makes a genuinely stale
lock look live and the message says "held by a live process," which is then false. The
under-recover direction is the right choice (the builder argued this correctly) but the
message should hedge.

### Nothing else in the family

I looked for other "a check substitutes for real work" surfaces and found none:
`check_read_path_health` clears only codes it has just re-verified; `clear_degraded("empty")`
fires only on positive evidence; `_index_all_reporting_embedding_errors`'s unconditional
`clear_degraded(None)` is still screened by `ensure_ready`'s earlier branches (REVIEW3's
ASK, unchanged, still unreachable); `--dry-run` still takes no lock and writes nothing.

## 4. The `_initialized = True` test seam

**Confirmed harmless, and I checked the failure mode the coordinator was worried about
rather than the one the builder checked.** 29 assignments across 6 files. No source file
reads `pkb_index._initialized` (the `costs.py` and `router/backends.py` hits are unrelated
instance attributes). The assignments now create a stray module attribute and nothing
reads it.

The builder's argument — that `_flush` and `schedule_upsert` don't read it — is true but
is not the interesting question, because `ensure_ready` *did* read it. The seam's real
purpose was "pretend startup already ran," and the load-bearing half of that idiom is the
adjacent `pki._pkb_root_cache = isolated_pkb`, which still works. So a test whose seam
went dead would show up as a test that now calls `ensure_ready` and gets a real build
where it used to get an early return. I scanned every function containing the assignment
for a later `ensure_ready` call and found exactly one:
`test_pkb_index_qa.py:335::test_pending_write_recovered_by_next_boot_sweep` — and it
calls `_reset_for_tests()` first, deliberately, to simulate process death. So its
`ensure_ready` was always meant to run fully.

No test's assertion is weakened. It is still 29 lines of dead, misleading seam that
implies a guard that no longer exists; sweep them in the QA pass or the next touch.

## 5. Regression numbers — verified, and stronger than claimed

Per-file isolated runs at HEAD across the 25 touched-and-adjacent suites:
**379 passed, 0 failed.** (11 + 41 + 29 + 4 + 20 + 9 + 15 + 11 + 11 + 33 + 28 + 7 + 18 +
6 + 7 + 9 + 9 + 4 + 37 + 6 + 27 + 3 + 11 + 12 + 11.) The builder's 322 is a subset of the
same set; with zero failures at HEAD there is nothing a baseline comparison could reveal
in these files.

For the surrounding blast radius I ran the 1237-test targeted selection
(`-k "pkb or docs or wiki or knowledge or dac or doctor or agent or vector or embed or
world"`): 11 failed, 1226 passed, 7 errors. All 18 are in three pre-existing clusters —
`test_world_forge_api.py` (5F/7E), `portal/test_build_tab.py` (4F), `test_dac_rename.py`
(1F), `test_docs_routes.py` (1F under selection, 3F standalone). I ran each against a
clean `8cb5760` worktree in isolation: `test_world_forge_api.py` gives
**5 failed / 7 passed / 7 errors at both revisions**, and the other three files give an
**identical 8-failed / 45-passed set with identical test IDs** at both. No new failure,
no deletion, no new skip.

REVIEW3's warning stands and QA should carry it: the full-suite count (52) and the
isolated count (34) differ by ~18 tests of pre-existing order-dependence. Only the
per-file isolated comparison means anything here.

## Security findings

- [INFO] **BLOCK-3's security teeth are gone.** Measured, not inferred: `doctor` under
  `LAB_MODE=hybrid` against an unindexed 40-file World emits one 5-byte embed request.
  A diagnostic is no longer a corpus-egress path.
- [ASK] ASK-1 is a cross-World-write hazard if it ever becomes reachable — worth fixing on
  isolation grounds, not just correctness ones. Per-instance isolation is a stated
  product rule in this repo (`CLAUDE.md`, the secrets-never-shared convention); a
  process-global write target sits in the same family.
- [INFO] The lock file still contains only a PID. Sidecar and checkpoint still carry no
  user content. `X-Retrieval-Reason` truncation unchanged.
- [ASK] REVIEW2's ASK-4 (document that a LAN-hosted Ollama is refused under the default
  `airgapped`) is now three reviews old and still unaddressed. QA-phase.

## Test coverage assessment

21 new tests this pass, 71 passing across the five directly-touched files in 2.8 s. Every
test REVIEW3 required exists, and each maps to the scenario that motivated it. Two are
better than what I asked for: `test_shadow_verification_is_cardinality_only_documented_limitation`
and `test_degraded_empty_code_from_root_a_readonly_check_leaks_into_root_b_status`
*reproduce* deferred limitations rather than merely describing them, so the day someone
fixes them the test fails loudly and points at the ticket. I want more of that.

Remaining gaps, all new:
1. no test that the stale-lock recovery preserves mutual exclusion (ASK-2);
2. no test that `ensure_ready(build=False)` leaves `_pkb_root_cache` alone (ASK-1);
3. no test pinning the *ordering* inside `ensure_ready` — that the dimension and
   provenance checks stay above the `if not build: return`. Move that return up and
   `doctor` silently stops exiting 3 on legacy Worlds, with every existing test green.

## Performance assessment

Unchanged from REVIEW3, because the code it measured is unchanged: `_table()` ≈ 7.5 ms of
a 20.8 ms `pkb.search()` on a 116-row index, three opens per query, and `open_table` cost
grows with fragment count. `check_read_path_health` remains 0.105 ms and is not the
problem. `build=False` adds no measurable cost — it does strictly less work than
`build=True` on every branch.

## Tech debt delta

All three debts REVIEW3 required are filed with real "what a future sprint must do"
sections (`BACKLOG.md` lines 774, 892, 929). The `_table()` entry correctly argues why it
is a hot-path change deserving its own sprint rather than a rider on a remediation
commit — I accept the downgrade, while noting it as a required action that became a
ticket.

New debt this pass: ASK-1 (read-only call mutates the write target) and ASK-2 (stale-lock
recovery breaks mutual exclusion). Neither is filed. ASK-1 is partially covered by the
line-774 entry's "if concurrent in-process multi-root access is ever introduced" framing,
but that entry does not capture the new fact — that a *read-only* call is now a writer of
that state.

## Required actions before merge

1. **Guard `_pkb_root_cache` with `if build:`** (ASK-1). One line. `build=False` never
   reads it.
2. **Fix or file ASK-2.** Preferred: replace the PID-staleness heuristic with
   `fcntl.flock` on the lock fd, which deletes `_read_lock_pid`, `_pid_alive` and
   `_is_stale` and makes crash recovery a kernel guarantee. If deferred instead, it must
   be filed with the measured window, the reproduction, and `release()`'s unconditional
   unlink named explicitly — not filed as "lock hardening."
3. **Write the one-root-per-process invariant into `pkb_index.py`'s module header**,
   pointing at the BACKLOG entry. This is the thing that makes three separate deferrals
   safe and it currently exists only in a backlog file.

Not gating: the `_initialized` test-seam sweep, the ordering comment in `ensure_ready`,
the "held by a live process" message hedge, `--yes`, the duplicate stderr line, ASK-4's
LAN-Ollama doc, the `_table()` memoisation, and the Buddy context header — but the last
one must appear in TEST_REPORT in plain words: **on four of the operator's five Worlds,
Buddy is keyword-only and says nothing about it until `pkb reembed` is run.**

## QA attack list

REVIEW3's list, updated. Three items are now closed by verification rather than
assumption; the rest carry forward with two additions.

**Closed — do not spend QA time here.** "Doctor performs zero embeds" (measured: one
5-byte probe). "Sticky `empty` code" (fixed and pinned; clears on evidence in
`_semantic_search`). "Unexercised `VectorSearchError` branch" (now covered by
`test_semantic_search_vector_search_error_after_health_check_passes_degrades`, which I
consider adequate — it asserts both the degrade and the keyword fall-through).

**Carry forward:**
1. **Buddy silently keyword-only on four of five real Worlds.** Still the highest-value
   item and the one that reaches a friend's machine. Verify what Buddy actually receives
   from `search_for_agents` on a 128-dim World and say so plainly in TEST_REPORT.
2. **`_table()` triple-open latency**, measured on a real World's fragment count, not a
   116-row scratch index. Establish whether the 7.5 ms grows to something that matters.
3. **Stale `reembed.lock` after SIGKILL** — now with a different question than REVIEW3
   asked. Recovery works (I verified the happy path); the question is now ASK-2's race
   and PID reuse.
4. **Two-process reembed under load.** REVIEW3 flagged the theoretical flake where the
   winner finishes before the loser reaches the lock; still unexercised under CPU
   pressure.
5. **ASK-4:** document that a LAN-hosted Ollama is refused under the default `airgapped`.

**New — add these:**
6. **`ensure_ready` ordering.** Move the `if not build: return` above the provenance
   check in a scratch copy and confirm the whole suite stays green. If it does, that is
   the coverage gap: doctor's exit-3 behaviour on legacy Worlds is unpinned against
   reordering, and legacy Worlds are the operator's actual state on four of five.
7. **The `build=False` degrade codes on a real World.** Doctor's read-only path can now
   set `empty` for two different conditions (no index directory, no table) and
   `dimension` for two more (missing columns, dim mismatch). Run `doctor` against all
   five real Worlds and confirm every message names a remedy the user can actually
   execute, and that exit codes are `3/3/3/3/0` as REVIEW3 recorded — read-only this time.
8. **Cross-World contamination probe.** Even though I could not reach it: in one process,
   `ensure_ready(A)`, `schedule_upsert` a file under A with no explicit `pkb_root`,
   `ensure_ready(B, build=False)`, `flush_now()`. Confirm the row lands in A. If ASK-1 is
   fixed this passes trivially; if it is deferred, this is the test that documents the
   hazard the way the builder documented the others.
