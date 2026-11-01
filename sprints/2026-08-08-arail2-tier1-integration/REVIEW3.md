# Review 3: the BLOCK remediation

**Date:** 2026-08-08
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) build3 at `548c204`
**Reviewed commits:** `44b3981` (BLOCK-1) · `6e6e0f2` (C1 partial) · `237e630` (BLOCK-2) ·
`657e34d`/`548c204` (docs/ledger). Tree at `4c9795f`.
**Prior:** [REVIEW2.md](./REVIEW2.md) at `a843f9c` (BLOCK) · [REVIEW.md](./REVIEW.md) ·
[ARCHITECTURE.md](./ARCHITECTURE.md)

## Verdict: BLOCK

Both original blocks are **genuinely dead** — I re-ran every reproduction from REVIEW2
against the real code with real Ollama, and each one now fails to reproduce for the right
reason, not because the symptom moved. The remediation is good work: precise, scoped,
honest in its ledger, and the read-path check is a *stronger* property than I asked for
(it catches a table that goes bad mid-session, on the very next query).

I am blocking on a third finding, which is **my miss in REVIEW2, not a build3 regression**:

- **BLOCK-3 — `./arailctl doctor` performs an implicit, unbounded embedding run and
  writes an index into the user's World.** I reproduced it by accident, on the operator's
  real `finance` World, while running the read-only doctor sweep for this review. That
  contradicts C2's central claim, and under `LAB_MODE=hybrid` it makes a *diagnostic
  command* a corpus-egress path.

---

## Disclosure — I wrote to the operator's real lab

While checking doctor's exit code across the five real Worlds
(`lab/instances/<slug>/pkb`), my invocation against **`finance`** caused
`pkb_index.ensure_ready()` → `pkb.index_all()` to build a brand-new 41-row,
768-dim nomic index plus provenance sidecar at
`lab/instances/finance/pkb/.cache/lancedb/` (written `2026-08-09T00:02:23Z`).
`finance` was the one World with no existing index, so it took the "table missing" branch.

The write is internally consistent and harmless in content (correct model, correct
dimension, correct sidecar) and no other World was touched — `ai`, `debt-finance`,
`qukaizen`, `video-games` all still carry their original `pkb_pages.lance` with mtimes
days old, verified. It is nonetheless a breach of my own read-only boundary, and the
operator should know a `.cache/lancedb` now exists under `finance` that did not before.
Delete it if unwanted; a later `pkb reembed` would rebuild it identically.

It is also, precisely, the evidence for BLOCK-3.

---

## What I executed

Real Ollama, `nomic-embed-text` @ 768d. Scratch roots under the session scratchpad;
`w1` = 6-file legacy 128-dim hash table, `w2` = genuine 768-dim nomic index, `w3` =
116-row corpus, `w4` = live table + empty PKB dir.

| # | REVIEW2 scenario | Result at `4c9795f` |
|---|---|---|
| 1 | legacy 128d + `ensure_ready` | degrades (`{"dimension": ...}`), 6/6 rows intact — FM12 still holds |
| 2 | …then one `pkb.search()` | **flag survives**; codes unchanged; `retrieval_status()` still `False`; no semantic hits — **fixed** |
| 2b | `doctor` on that root | **exit 3** (was 0) — fixed |
| 3 | sidecar = foreign model @ same dim | search returns **0 semantic hits**, falls to keyword, `provenance` code stands — was 12 hits labelled `semantic`. **Fixed** |
| 3b | repair sidecar mid-process → search | 12 hits, `source="semantic"`, codes empty — the check clears on real evidence, not blanket |
| 3c | re-break sidecar mid-process → search | 0 hits, `provenance` re-set on the **very next query** — stronger than start-up-only enforcement |
| 3d | `embed_query()` success clears only `provider` | confirmed by reading + execution: the health check runs *before* `embed_query`, and the only clear on that path is `clear_degraded("provider")` |
| 4 | checkpoint 40/116 + `.next` gone + `--resume` | checkpoint **discarded** with a stderr sentence, full 116 re-embedded, live table 116, sidecar 116 — was 38 rows reported as 78. **Fixed** |
| 4b | legit SIGINT at 96/116, then `--resume` | resumes correctly to 116/116; the new guard does not false-positive on the honest path |
| 5 | `total == 0` + populated live table | `EmptyCorpusRefused`, exit 1, live table untouched (still 38 rows), **no** `.bak-<ts>` created — **fixed** |
| 6 | two real `pkb reembed` processes, one root | A exit 0 / B exit **1** with an English sentence naming the lock file; no Lance/Rust traceback; lock removed afterwards. Test repeated 5×, stable. **Fixed** |
| 7 | `VectorIndex.search()` for non-PKB consumers | missing table → `[]`, dim mismatch → `[]`, `where=` works, `min_score` works, `vector`/`_distance` stripped, scores identical — **backward compatible** |
| 8 | doctor on all five real Worlds | `ai`/`debt-finance`/`qukaizen`/`video-games` → **exit 3**, correct message. `finance` → exit 0 **after doctor built it an index** (BLOCK-3) |
| 9 | per-query health-check cost | `check_read_path_health` = **0.105 ms**, one sidecar read per query. Not the cost problem — see the ASK on `_table()` |
| 10 | sidecar `chmod 000` / garbage mid-session | never raises; degrades to keyword; `provenance` code set. Fail-closed |
| 11 | SIGKILL mid-run | lock file survives; **every subsequent `pkb reembed` is refused** until manually deleted (ASK) |
| 12 | full suite | **52 failed / 4341 passed / 18 skipped / 3 xfailed / 7 errors**, 868 s — matches the builder's report exactly |
| 13 | regression provenance | the 29 failing files run in isolation give **byte-identical failing-test-ID sets at `8cb5760` and HEAD** (34 each). Collection 4193 → 4421, no deletions, no new skips |

---

## BLOCK-1 — dead

The fix is the right one and it is implemented at the right layer.

`_degraded_codes: dict[str, str]` replaces the single global. Every mutation site now names
the code it has evidence about: `_flush`'s per-row success clears `"provider"` only;
`_semantic_search`'s successful `embed_query` clears `"provider"` only;
`check_read_path_health` clears `"dimension"`/`"provenance"` only when it has just
re-verified them; `clear_degraded(None)` survives at exactly three sites, all full
rebuilds. I audited all 13 call sites by grep and by reading — none of them clears a code
it has no evidence about.

`check_read_path_health(table, db_path)` is the shared implementation `ensure_ready` and
`_semantic_search` both call, so the two enforcement points cannot drift — which was the
root cause (one real copy and a missing one). Because it runs per query rather than per
process, scenario 3c now works: a sidecar rewritten *after* startup is caught on the next
search, which the architecture never asked for and should have.

`doctor`'s exit code reads `degraded_codes() & {"dimension", "provenance"}`. No substring
matching on prose. Verified on four real 128-dim Worlds: exit 3, actionable message.
`provider`/`empty` correctly stay INFO — C5's clean-machine promise is not contradicted.

`pkb._table_search_by_vector` is deleted, with its bare `except Exception: return []`.

**Ruling on the `vector_index.py` scope question (asked explicitly).**
**Within the amendment. Approve; no narrowing required.**

The amendment I granted read: *"a `search_vector(vector, *, k, min_score, where=None)`
method (or a `vector=` parameter on `search`) with the existing `search()` delegating to
it, so there is one post-processing implementation. Any error the underlying search raises
must be distinguishable from 'no hits' at that call site."* The 58/16 delta is exactly
that and nothing else: the 16 deletions are the moved body of `search()`, the insertions
are `search_vector` + its docstring, the re-declared `search()` wrapper, and
`VectorSearchError` — which is not scope creep, it is the second half of the amendment's
own sentence. There is no third change in the file. (One cosmetic edit: `k`→`kk` in the
row comprehension. Python 3 comprehensions have their own scope so the original never
leaked; no behaviour change either way.)

Backward compatibility for the hash-embedded consumers is genuinely preserved, and I
tested it rather than reasoning about it (row 7 above). The only consumer of
`VectorIndex.search()` outside the PKB path is
`skills/experiment_tracker` (`experiments` table); `wiki_vectors.py` never used it (it
calls `table.search()` itself) and `agent_workflows` has no search caller. For that
consumer: missing table still returns `[]`, a dimension mismatch still returns `[]`,
`where=`/`min_score` still work, scores are identical, `vector`/`_distance` are still
stripped. The one micro-difference is that `search()` now computes `hash_embedding`
before discovering the table is missing — irrelevant.

---

## BLOCK-2 — dead

All four required sub-fixes are present and each is verified behaviourally, not by
reading:

- **(a)** the shadow table's row count is re-read from LanceDB and compared to `total`
  before anything moves; mismatch discards both artefacts and raises.
- **(b)** `--resume` verifies the shadow's actual row count against
  `len(completed_paths)` — with "the dir doesn't exist" reading as 0 — and restarts from
  scratch with a visible reason on stderr rather than resuming a lie.
- **(c)** `total == 0` against a live table raises `EmptyCorpusRefused` *before* any
  shadow or checkpoint work; `total == 0` with no live table is still a legitimate no-op.
- **(d)** `O_EXCL` lock at `.cache/reembed.lock`, held for the write phase only,
  released on both the success and the exception path (`with` on the context manager);
  `--dry-run` never acquires it.

All three new exceptions subclass `RuntimeError`, so `main()`'s existing handler gives
exit **1** with the message and no traceback — verified end-to-end through the CLI, not
just through `run()`.

Two residual notes, neither blocking:

- The shadow verification is **cardinality-only**, not a path-set comparison. I tried to
  construct a silent-corruption case around it and could not without external tampering:
  a stale checkpoint against a changed corpus inflates the count and trips the guard
  rather than sliding under it. A `set(shadow.paths) == completed_paths` check would be
  strictly stronger and costs one `to_pandas()["path"]`.
- The discard reason is printed twice (once from `_log_stderr` inside `run`, once from
  `main`). Cosmetic.

---

## BLOCK-3 (new) — `doctor` implicitly embeds and writes an index

**Reproduced on the operator's real lab, unintentionally.** `doctor.check_knowledge_base`
calls `pkb_index.ensure_ready()` (doctor.py:137-138). On a World whose `pkb_pages` table
does not exist yet, `ensure_ready` takes the "table missing" branch →
`_index_all_reporting_embedding_errors` → `pkb.index_all()` → one batched
`embed_documents()` over the entire corpus → `VectorIndex.replace()` → provenance write.

At `8cb5760` that path was **local and free**: `index_all` used `hash_embedding`. This
sprint (W9) swapped it to the network embedder without revisiting who calls it. The result:

1. **It contradicts C2 and the module's own docstring.** `pkb_reembed.py`'s header says
   *"This is the only path that (re)writes `pkb_pages` with the spec-declared embedder."*
   That sentence is false as shipped. FM11 removed the lazy embed from *search*; the same
   hazard remains on `ensure_ready`, and `doctor` is now a trigger for it.
2. **A diagnostic mutates the thing it diagnoses.** Running `./arailctl doctor` on a new
   World writes an index and burns an embedding pass proportional to corpus size. On the
   operator's `finance` World that was 41 rows; on a populated World it is hundreds to
   thousands of embed calls from a command whose contract is "print a health report".
3. **Security teeth: under `LAB_MODE=hybrid` this is a corpus-egress path.** REVIEW2
   already noted this machine's `.env` sets `LAB_MODE=hybrid` (line 57), so the W0
   airgapped guard is off by configuration here. A mistyped or hostile `MODEL_API_BASE`
   plus one `doctor` invocation ships the World's PKB text off-box. `doctor` is the
   command a user runs *when something is already wrong* — the worst moment for it to
   start bulk-sending their knowledge base to whatever endpoint is configured.

This is not a build3 regression; it landed in W9 and I did not catch it in REVIEW2. I am
blocking on it anyway, because the verdict rule for PASS is "I would run this on the
operator's five real Worlds" — I did, and it wrote to one of them without asking.

**Required fix (small, one of):**
(a) `doctor` stops calling `ensure_ready()` and uses a read-only probe (open the table,
run `check_read_path_health`, report — never build); **or**
(b) `ensure_ready` grows an explicit `build: bool = True` parameter and doctor passes
`build=False`, so the portal keeps its startup build and diagnostics stay inert.
Either way: correct `pkb_reembed.py`'s "only path" docstring, and add a test asserting
`doctor.check_knowledge_base()` on a World with no index performs **zero** `embed_documents`
calls and creates no `.cache/lancedb`.

---

## On the deferred C1 surfaces

REVIEW2 permitted either wiring or an explicit ledger deferral. The builder took the
deferral and recorded it properly: SPRINT.md decisions row, a BACKLOG entry naming both
remaining surfaces and what a future sprint must do, and the BUILD_LOG's
"what I could not do" section. That satisfies the letter of what I asked, and the
`/knowledge` justification checks out — it is a 307 redirect to `/dac` (app.py:11109).

My judgment on whether it is *adequate*: **partially, with a caveat QA must carry.**

- The `X-Retrieval-Status` header is real, tested, and correct — but it has **zero
  consumers**. No template or JS reads it. So in the surface the operator actually looks
  at, the visible signal for a degraded KB is `./arailctl doctor` and nothing else. That
  is materially better than REVIEW2's state (where BLOCK-1 turned doctor off too), and
  materially worse than C1 promised.
- The half that matters most is the one still missing: C1's *"an agent must not be handed
  keyword-only results while the UI claims semantic retrieval."* Buddy today receives
  keyword results from `search_for_agents` with no honesty line. On four of five real
  Worlds that is the live state right now.

Not a block — I said deferral was acceptable and I hold to that — but it should not
ship to a friend's machine without the Buddy header, and QA should say so in TEST_REPORT.

---

## Code quality findings

- [BLOCK] `doctor.check_knowledge_base` → `ensure_ready()` → implicit `index_all` with the
  network embedder. See BLOCK-3.
- [ASK] **The `"empty"` code is sticky within a process.** `_semantic_search` sets it when
  `count() == 0`; nothing clears it except a full rebuild. Reproduced: with `"empty"` set
  and a populated table, search serves 12 semantic hits correctly while
  `retrieval_status()` reports **degraded** forever, so `/api/pkb/search` stamps
  `X-Retrieval-Status: degraded` on healthy responses. Incremental `_flush` that makes the
  table non-empty should clear `"empty"`, or `check_read_path_health` should own it.
- [ASK] **+1 redundant `open_table` per query on the agent path.** `_semantic_search` now
  calls `idx.count()`, then `idx._table()` for the health check, then `search_vector`
  opens the table a third time (`VectorIndex._table()` is not memoised). Measured on the
  116-row `w3` index: `_table()` = **7.5 ms**, `count()` = 0.4 ms, whole `pkb.search()` =
  **20.8 ms**. So roughly a third of query latency is a table open the previous code did
  not do. The health check itself is *not* the cost (0.105 ms, one small JSON read) — the
  fix is to pass the already-opened `table` into `search_vector`, or cache `_table()`.
- [ASK] **A stale lock wedges the recovery verb.** After `SIGKILL` (or OOM, or a closed
  laptop), `.cache/reembed.lock` survives and every subsequent `pkb reembed` on that root
  exits 1 — including the run the degraded-KB message is telling the user to perform. The
  message does explain the manual remedy, and the PID is written into the file but never
  read. A liveness check (PID + `kill(pid, 0)`, or an mtime staleness window) closes it.
- [ASK] `_index_all_reporting_embedding_errors` calls `clear_degraded(None)` without
  checking `index_all`'s `ok` flag. `index_all` returns `{"ok": False}` without writing
  when the root is missing or LanceDB is unavailable — in which case all codes get cleared
  on no evidence. Unreachable today (both conditions are screened earlier in
  `ensure_ready`), but it is one `if result.get("ok")` away from being safe by
  construction rather than by luck.
- [ASK] `check_read_path_health` sets the `"dimension"` code for a *missing-columns*
  condition, whose message talks about schema, not dimension. Doctor then exits 3 for a
  state `ensure_ready` would have silently repaired. Minor code/prose mismatch; a third
  code (`"schema"`) would be cleaner.
- [INFO] Shadow verification is cardinality-only (above).
- [INFO] `--yes` is still an accepted no-op flag. Third review, third mention.
- [INFO] The `X-Retrieval-Status` header is present only when degraded, so a client cannot
  distinguish "healthy" from "old server". Emit `ok` explicitly when you wire a consumer.

## Security findings

- [BLOCK] **`doctor` as an egress path under `LAB_MODE=hybrid`.** See BLOCK-3 point 3.
- [INFO] C3 still holds where it applies: the airgapped guard is unchanged by build3 and
  fires before any socket.
- [INFO] The reembed lock lives at `.cache/reembed.lock`, inside the `.cache` exclusion
  added in chunk 2, so it is not indexed and does not leak into the wiki. Correct.
- [INFO] The lock file contains only a PID. Sidecar and checkpoint still carry no user
  content. `X-Retrieval-Reason` is truncated to 200 chars and carries only the degraded
  message (model names, dimensions, a command) — no paths, no corpus text.
- [ASK] REVIEW2's ASK-4 (document that a LAN-hosted Ollama is refused under the default
  `airgapped`) is still unaddressed. QA-phase item, carried forward.

## Test coverage assessment

21 new tests; 48 pass across the five directly relevant files in 2.8 s. All six tests
REVIEW2 required exist and each maps to the scenario that motivated it.

**The real-embedder guard genuinely defeats the suite-wide stub.** Verified three ways:
`conftest.py:262` exempts `requires_ollama` before installing the monkeypatch; the marker
is registered in `pyproject.toml:284` with an auto-skip when `probe()` fails (so FM18
holds on a machine with no Ollama); and the test asserts both that the real
`embed_documents` object was called *and* that the stored vector differs from
`hash_embedding` of the same input — which is what a silent fallback would look like. It
passes here against live Ollama.

**The concurrency test is a true two-process race.** Two real `python -m arail.pkb_reembed`
subprocesses, `Popen` back-to-back, asserting `sorted(codes) == [0, 1]` and that the
loser's stderr contains neither `lance error` nor `traceback`. The builder's stated reason
for not using threads (`signal.signal` only works on the main thread) is correct. I ran it
5× consecutively: 5/5 pass, ~1.8 s each. The theoretical flake — the winner finishing
before the loser reaches the lock — did not occur; with 40 rows the margin is comfortable
but not enormous, so QA should run it under load.

Remaining gaps QA must close:
1. no test that `doctor` performs zero embeds / creates no index (BLOCK-3);
2. no test for the `"empty"` code's stickiness;
3. no test that `search_vector`'s error path is reachable *after* `check_read_path_health`
   passes — the `VectorSearchError` branch in `_semantic_search` is currently unexercised;
4. no test for stale-lock recovery;
5. shadow path-set (not just count) equality.

## Performance assessment

`check_read_path_health` per query: **0.105 ms** plus one small sidecar read — the
per-query I/O I flagged as a possible relocated hazard is not one. The relocated cost is
elsewhere: the extra un-memoised `open_table` (~7.5 ms of a 20.8 ms query on a 116-row
index; `open_table` cost grows with fragment count, so it will get worse on a large
World). Re-embed throughput unchanged: 38–212 rows/s on `w3` depending on batch, in line
with the 75–134 rows/s planning figure documented in `docs/cli.md`.

## Regression claim — verified real

The builder's "52 failed / 4341 passed, fewer than the 53 baseline" is **not** a
collection artefact and **not** newly-skipped tests:

- My own full run reproduces it exactly: **52 failed, 4341 passed, 18 skipped, 3 xfailed,
  7 errors** in 868 s, across 29 distinct failing files, none of which this sprint touches.
- Collection: `8cb5760` = **4193** tests, HEAD = **4421**. Growth only. No test file was
  deleted; the only edits to pre-existing test files are the mechanical 128→768 dimension
  and `_build_docs_rows`→`_collect_docs_rows` adaptations from chunk 2, none of which
  weaken an assertion (two of them replace a `VectorIndex.search()` lookup with a direct
  table read for tests that were never about retrieval — legitimate).
- The decisive check: running the 29 failing files **in isolation** at `8cb5760` and at
  HEAD produces `27 failed + 7 errors` in both cases, and the failing test-ID sets are
  **byte-identical** (`diff` clean). Nothing this sprint did makes any of them fail.
- Worth telling QA: the full suite shows **52** failures where those same files in
  isolation show **34**, so roughly 18 are cross-test pollution / ordering effects that
  pre-date this sprint. "52 vs 53" is therefore a noisy metric and should not be leaned on
  as evidence of anything either way; the per-file isolated comparison is the real one.

## Tech debt delta

The four debts REVIEW2 required are filed in `sprints/BACKLOG.md`, each with a real
"what a future sprint must do" section rather than a title. The fifth (duplicated
post-processing) was resolved rather than filed, as instructed. Good.

Unpredicted debt this pass adds, to be filed before PASS:

1. **`doctor` mutates** (BLOCK-3) — fix, don't file.
2. `VectorIndex._table()` re-opens the table on every call; three opens per PKB query.
3. `"empty"` has no incremental clearing path.
4. `reembed.lock` has no staleness/liveness handling.

## Required actions before re-review

1. **Fix BLOCK-3.** `doctor` (and any other non-portal caller of `ensure_ready`) must not
   trigger an implicit embedding run or an index write. Correct `pkb_reembed.py`'s
   "only path that (re)writes `pkb_pages`" docstring. Add the zero-embeds test.
2. **Clear `"empty"` on evidence** — an incremental flush that leaves the table non-empty,
   or fold it into `check_read_path_health`.
3. **Stop re-opening the table three times per query** — pass the opened `table` to
   `search_vector`, or memoise `_table()`.
4. **File the three remaining debts** above.

Not gating: the stale-lock liveness check, the shadow path-set comparison, `--yes`, the
duplicate stderr line, ASK-4's LAN-Ollama doc, and the Buddy context header — but QA
should exercise all of them and TEST_REPORT should say plainly that on four of the
operator's five Worlds, Buddy is silently keyword-only until `pkb reembed` is run.
