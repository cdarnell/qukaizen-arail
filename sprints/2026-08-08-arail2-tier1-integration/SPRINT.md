# Sprint: arail2-tier1-integration

**ID:** 2026-08-08-arail2-tier1-integration
**Started:** 2026-08-08T18:56:52Z
**Product:** arail
**Branch:** `qukaizen/arail-2-declarative-persistence-819030`

## Task

Make the ARAIL 2.0 persistence layer load-bearing. Two changes plus a cutover:
replace the 128-dim SHA1 `hash_embedding` with the spec-declared
nomic-embed-text provider (`arail.dbspec.embed`) at the PKB ingest path
(INTEGRATION.md Tier 1.2); thread `world_id` through `pkb.search` /
`search_for_agents` so retrieval is scoped by a WHERE clause instead of by
`rm -rf` of other worlds' files (Tier 1.1); then point the running lab at the
2.0 store. Until one of these lands, the persistence layer is a rehearsal —
nothing in the running lab reads it.

## Predecessor sprint

`sprints/2026-08-08-arail2-declarative-persistence/` — the layer itself:
PHASE1_AUDIT.md (the evidence), INTEGRATION.md (Tier 1.1 / 1.2 rationale),
SPRINT.md (what shipped, known debt). 107 tests, verified end-to-end on real
data. Nothing in `pkb.py`, `vector_index.py`, `world_mount.py`, or
`scripts/start.sh` has been modified yet.

## Phases

| Phase | Subagent | Artifact | Status | Started | Finished | Verdict |
|---|---|---|---|---|---|---|
| think | visionary | VISION.md | done | 2026-08-08T18:56:52Z | 2026-08-08T19:03Z | **proceed (narrowed)** |
| plan | architect (design) | ARCHITECTURE.md | done | 2026-08-08T19:06Z | 2026-08-08T19:13Z | complete (W0–W10) |
| build | builder | BUILD_LOG.md | done (W0–W5) | 2026-08-08T19:15Z | 2026-08-08T19:47Z | **PASS** (Δ +40.6pp) |
| build2 | builder | BUILD_LOG.md | done (W6–W10 + actions) | 2026-08-08T20:01Z | 2026-08-08T21:00Z | 88 tests, baseline-clean |
| review2 | architect (review) | REVIEW2.md | done | 2026-08-08T21:02Z | 2026-08-08T21:14Z | **BLOCK** |
| build3 | builder | BUILD_LOG.md | done (BLOCK remediation) | 2026-08-08T21:16Z | 2026-08-08T21:52Z | 21 tests; suite 52F/4341P |
| review3 | architect (review) | REVIEW3.md | done | 2026-08-08T21:54Z | 2026-08-08T22:26Z | **BLOCK** (new: BLOCK-3) |
| build4 | builder | BUILD_LOG.md | done (BLOCK-3) | 2026-08-08T22:28Z | 2026-08-08T22:43Z | fixed; regression found by orchestrator |
| build5 | builder | BUILD_LOG.md | done (ORCH-1) | 2026-08-08T22:46Z | 2026-08-08T22:54Z | 322 tests |
| review4 | architect (review) | REVIEW4.md | done | 2026-08-08T22:56Z | 2026-08-08T23:20Z | **WEAK_PASS** |
| build6 | builder | BUILD_LOG.md | done (ASK-1, ASK-2, invariant docs) | 2026-08-08T23:22Z | 2026-08-08T23:45Z | 154 targeted tests |
| review | architect (review) | REVIEW.md | done | 2026-08-08T19:48Z | 2026-08-08T19:56Z | **PASS** |
| test | qa | TEST_REPORT.md | done | 2026-08-08T23:47Z | 2026-09-09T00:18Z | **FAIL** (QA-5) |
| build7 | builder | BUILD_LOG.md | done (QA-4/QA-5) | 2026-09-09T00:20Z | 2026-09-09T00:29Z | 11/11 green |
| test2 | qa | TEST_REPORT.md | done | 2026-09-09T00:31Z | 2026-09-09T00:56Z | **WEAK_PASS** |
| build8 | builder | BUILD_LOG.md | done (QA-1/2/3/7) | 2026-09-09T00:58Z | 2026-09-09T01:10Z | 366 sprint tests green |
| ship | — | PR | **done** | — | 2026-08-09 | [#175](https://github.com/cdarnell/qukaizen-arail/pull/175) |

## Decisions log

| Date | Decision | Rationale |
|---|---|---|
| 2026-08-08 | Start at `think`, not `plan` | The orchestrator's own evidence for Tier 1.2 is thin: prefixed nomic widened the relevant/irrelevant margin only +0.053, and the scoped-query demo was ambiguous. The contamination severity was also overstated twice and corrected (7% of rows, not "stuffed"). A visionary pass that demands disconfirming evidence is warranted before touching the agent-facing search path. |
| 2026-08-08 | Cutover is in scope, but sequenced last | Embeddings and scoping are only useful together; cutover before them gains nothing. The predecessor migration wrote to a temp dir, so the live lab is untouched and cutover remains an unmade decision. |
| 2026-08-08 | **Visionary narrows scope: Tier 1.2 as a measurement; Tier 1.1 deferred; cutover rejected.** | Verified by the orchestrator: `pkb._vector_db_path()` (pkb.py:414) derives the LanceDB path from the env-frozen per-instance PKB root, so in 1.x a running World already cannot see another World's rows — five physically separate `pkb_pages.lance` datasets. Tier 1.1 is therefore the *precondition of consolidation*, not value delivered by it. The orchestrator's "same file from four worlds" demo was **circular**: it ran against the consolidated store the migration had just created, demonstrating a problem the migration introduced rather than one that exists in 1.x. |
| 2026-08-08 | **Operator accepted the narrowing.** Sprint scope is now: measure nomic vs `hash_embedding` against a committed labelled fixture with the >=15pp recall@5 kill criterion. Tier 1.1 deferred, cutover dropped. A25/A26 explicitly NOT in scope this sprint (offered, not taken). | Answers the open question with data instead of assumption, and does not spend the install-burden of a hard Ollama dependency on an unmeasured improvement. |
| 2026-08-08 | Architect raised the fixture floor to >=30 queries and added a paired-bootstrap 95% CI on the delta | 15pp over 20 queries is 3 queries — a coin flip wearing a percentage sign. Floor raised to >=6 per world x 5 stores. A point estimate clearing 15pp with a CI crossing zero yields `PASS_INCONCLUSIVE`, which publishes and stops for operator sign-off rather than auto-integrating. |
| 2026-08-08 | Build is scoped to W0–W5 (through the measurement), not the full W0–W10 | W5 is the gate. Whether the conditional integration (W6–W10) happens depends on a number that does not exist yet. The builder publishes the number and stops; the orchestrator re-gates. |
| 2026-08-08 | The `+0.053` number measured the wrong thing | It compared nomic-with-prefixes vs nomic-without. The decision actually on the table — nomic vs `hash_embedding` — has never been measured. Hence Tier 1.2 ships as an A/B with a kill criterion, not as an assumed improvement. |
| 2026-08-08 | **REVIEW2.md BLOCK-1/BLOCK-2 remediated in build3.** Reason-scoped degraded state (`set_degraded(code, reason)`/`clear_degraded(code=None)`) replaces the single module-global bool; `pkb._semantic_search` now runs `pkb_index.check_read_path_health()` (dimension + provenance) on every call, not just at `ensure_ready`; `pkb_reembed.run()` verifies the shadow build's row count against `total` before swapping, refuses `total==0` against a live table, discards an inconsistent `--resume` checkpoint, and takes an `O_EXCL` lock. Boundary #6 amended per REVIEW2.md: `vector_index.py` gained one additive `search_vector()` method; `pkb._table_search_by_vector`'s duplicate (with its bare `except: return []`) is deleted. | Both blocks were reproduced by execution against real code, including the operator's actual `debt-finance` World (79 rows @ 128d, no sidecar) — this was the exact failure class (outage/staleness presenting as healthy) the sprint exists to prevent. |
| 2026-08-08 | **C1's search-payload wiring is partial, explicitly.** `/api/pkb/search` now sets `X-Retrieval-Status`/`X-Retrieval-Reason` response headers when degraded (via `pkb.retrieval_status()`) — real, tested. The `/knowledge` banner and Buddy's context-header line (`search_for_agents`' caller-side "an agent must not be handed keyword-only results while the UI claims semantic retrieval") are **not** wired in build3 — `/knowledge` itself is a 307 redirect to `/dac` today, and both remaining surfaces are template/JS-layer changes across `dashboard.html`/`agents.html`/the DaC UI that need their own scoped pass, not a rushed addition inside a BLOCK-remediation commit. Filed in `sprints/BACKLOG.md`. | REVIEW2.md explicitly permits "wire it, or record the deferral explicitly in SPRINT.md as an accepted C1 gap with a backlog entry" — this is that record. Not silence: the search API payload, the one surface with the most callers (dashboard, agents, docs_hub), is wired; doctor already surfaces the same status; only the two UI-template surfaces are deferred. |

## W5 gate result (2026-08-08)

Pooled recall@5: hash **50.0%** vs nomic **90.6%**, Δ **+40.6pp** against a
>=15pp bar. 95% bootstrap CI on Δ **[+25.0, +56.2]pp** — lower bound well
above zero, so not `PASS_INCONCLUSIVE`. Exact-token rank-1 losses: **0**.
Zero-lexical-overlap stratum: hash **0.0%**, nomic **62.5%**.

**Verdict: PASS.** W6–W10 (conditional integration) become live work.

Orchestrator correction recorded: when briefing the visionary, the orchestrator
framed nomic as making "Ollama a hard requirement." That was wrong and
contradicted its own spec comment (`spec/models/models.hcl:40`). `setup.sh`
already does `brew install ollama` + `ollama pull llama3.2:1b` (1.3 GB). The
true marginal cost is 274 MB, a new requirement that Ollama be *running* during
PKB ingest, and ~100x slower indexing (10,000 rows/s -> 100 rows/s; 2.85s for
the 381-row `ai` world). The error inflated the cost side but did not change
the outcome — the bar was cleared by 25pp beyond the CI's lower bound. The bar
was **not** revised after results existed; doing so would be post-hoc tuning.

## Review verdict (2026-08-08) — PASS

The reviewer reproduced the number independently (hash 50.0%, nomic 90.6%,
Δ +40.6pp, CI [+25.0, +56.2], zero rank-1 losses) and, because the builder both
authored the fixture and ran the measurement, corroborated it with a
**label-free probe**: 60 randomly sampled documents per world, each document's
own title as the query, no human labelling anywhere. Pooled over 120 probes,
**+29.2pp** — clears the bar with the fixture removed entirely, and the probe is
biased *toward* hash since the title text sits inside the embedded document.

Also established:
- 13 queries nomic wins / hash loses; **0** the other way.
- On the 21 high-overlap (lexically friendly) queries — hash's home stratum —
  hash 66.7% vs nomic 100%, Δ +33.3pp. The gate clears where hash should be
  strongest.
- The exact-token anomaly is a **genuine 128-dim collision failure**, not a bad
  fixture: all 10 literal tokens verified present in their target documents,
  yet hash ranks `LAB_BUDDY`'s document **51st of 381** because 8 of 10 queries
  reduce to a single token and 46 distinct corpus tokens collide into that one
  bucket in the `ai` world alone.
- **Corollary to record: `hash_embedding` has no domain on this corpus where it
  is the better retriever.** The architecture's "preserve hash for exact-token
  lookup" framing is measured false.
- All 32 evidence quotes verbatim byte-for-byte; `git log --follow` confirms
  pre-registration; `git diff 8cb5760` empty on all six protected files; both
  arms produce unit-norm vectors so neither gets a metric handicap.

### Required actions before merge

1. File two carried debts in `sprints/BACKLOG.md`: `pkb._iter_pkb_files`
   indexes `.wiki-cache/manifest.json` (1.15 MB) as a PKB row in every world;
   and the docs-registry corpus slice is unmeasured but will be re-embedded.
2. **PRIVACY (ASK-2).** `eval/retrieval/corpus_manifest.json` is committed and
   publishes the file inventory of the **private** `debt-finance` world —
   `penfed-credit-union.md`, `greenpath-financial-wellness.md`,
   `hardship-program.md`, `nonprofit-credit-counseling.md`,
   `agents/debt_advisor/`. Orchestrator verified `lab/worlds/debt-finance/` is
   **untracked** (0 files in `git ls-files`) while `lab/worlds/ai/` has 11 —
   the operator deliberately kept that world out of the repo. No text and no
   PII leak, but the topic inventory supports the inference that the operator
   is working with a nonprofit credit-counselling agency. Nothing is pushed
   (branch absent from remote), so this is latent. **Fix before any push.**
3. Append the exact-token collision diagnostic to `RESULTS.md`.

### Design amendments for W6–W10
(a) drop the "preserve hash for exact-token lookup" framing — measured false;
hash survives only because `wiki_nodes`/`agent_workflows`/`experiments` still
use it. (b) W9 must state the docs-registry coverage gap as a written
assumption. (c) size `pkb reembed` progress/ETA on ~75–134 rows/s, not the best
case — a 5,000-row lab is a ~60s operation.

## Build chunk 2 (W6–W10) — spec precedence

`ARCHITECTURE.md` remains the spec. Where `REVIEW.md` § "Design amendments"
conflicts with it, **REVIEW.md wins** — the amendments are recorded there
rather than by re-running the design phase, because they are three specified
bullet points, not a redesign.

Operator decision (2026-08-08): **ASK-2 withdrawn — no path hashing.**
`debt-finance` is a first-class World the operator specifically wants proven
out, not hidden. The orchestrator's privacy inference was **wrong** and is
recorded as such: `greenpath-financial-wellness.md` opens "A specific, named,
NFCC-member nonprofit credit-counseling agency" — a glossary *definition of the
institution*, sitting beside `payday-loan.md` and `credit-utilization-ratio.md`.
It is domain vocabulary, not a disclosure about the operator. A taxonomy was
mistaken for a personal record.

The distinction that survives, and that the lint still enforces: domain
vocabulary is publishable; the operator's actual balances, account numbers, or
personal correspondence are not. Only the latter is PII. The content-level PII
lint stays.

Added in scope: `debt-finance` is a **first-class verification target** for
W9/W10 — the re-embed path, provenance sidecar, and setup/degradation messaging
must be exercised against it. It already scores 100% recall@5 under nomic,
joint best with `root`.

## Review 2 verdict (2026-08-08) — BLOCK

Both blocks reproduced by execution.

**BLOCK-1 — a successful search erases the degraded state, and provenance is
never enforced on the query path.** `pkb._semantic_search` calls
`pkb_index.clear_degraded()` unconditionally once `embed_query()` succeeds, and
`_degraded` is a single module-global covering five distinct causes. Reproduced
on a legacy 128-dim table: `ensure_ready` degrades correctly, one `pkb.search()`
clears it, `doctor` then prints `vector index status: ok` and exits 0 while
semantic search returns nothing **forever**. That describes **all five of the
operator's real Worlds today** (`debt-finance` verified: 79 rows @ 128d, no
sidecar). Worse, with a sidecar naming a different model at the same dimension,
search returned **12 hits labelled `source="semantic"` from a foreign vector
space** — C4's "no query is served from a table whose provenance disagrees with
the spec" is not implemented on the query path at all. Compounding: 
`pkb.retrieval_status()` has **zero callers**, so C1's user-visible half was
silently dropped and `doctor` was the only surface — which BLOCK-1 turns off.

**BLOCK-2 — `pkb reembed` can swap in a truncated index and report success.**
Shadow-build completeness is never verified before the swap. A checkpoint
listing 40 of 78 paths whose `.next` dir is gone → `--resume` embeds the
remaining 38, swaps, reports `completed: 78`, writes a sidecar claiming
`rows: 78`, exits 0 — live table has **38 rows**, every health check agrees it
is fine. An empty corpus renames a healthy live table to `.bak-<ts>` and swaps
in nothing. No lock file; concurrent runs produced a raw
`lance error: Incompatible transaction`.

**Boundary ruling reversed.** "Don't touch `vector_index.py`" was a *pre-gate*
guarantee; C1/C2/C4 omit a precomputed-query-vector entry point because the
architect missed it, not because it was forbidden. The duplicated
`pkb._table_search_by_vector` reintroduced a bare `except Exception: return []`
on the agent-facing path — which is *how* BLOCK-1's dimension error becomes
silence. Boundary #6 amended to permit one additive `search_vector()` method on
`VectorIndex`; the duplicate is to be **deleted, not filed as debt**.

**Confirmed sound and kept:** `index_all` compute-before-`replace()` ordering
(structurally enforced), FM12 verified on real `debt-finance` data (degrades,
never drops), FM15, FM17, C6 (no flag, no fallback — grepped), `setup.sh`
offline grace, and scope (protected files byte-identical to `8cb5760`).

## Review 3 verdict (2026-08-08) — BLOCK on a new finding

**Both original defects are genuinely dead**, verified by re-execution, not by
reading the diff. BLOCK-1: on a legacy 128-dim table the degradation now
survives a search and `doctor` exits 3 — confirmed on four of the operator's
five real Worlds; a sidecar naming a foreign model at 768d now returns **0**
hits instead of 12 labelled `semantic`. The reviewer went further than the
spec required: it repaired the sidecar mid-process, re-broke it, and the very
next query caught it. BLOCK-2: an inconsistent checkpoint is discarded and the
full corpus re-embedded; an empty corpus against a live table raises
`EmptyCorpusRefused` and leaves the table untouched with no `.bak-`; two real
processes give exit 0 / exit 1 with English, 5/5 stable.

**`vector_index.py` scope ruling: within the amendment, approve, no
narrowing.** The 16 deletions are the moved body of `search()`; backward
compatibility was tested rather than inferred against `experiments`, the only
other `search()` consumer (`wiki_vectors` calls `table.search()` directly).

### BLOCK-3 — a diagnostic mutates its subject

`doctor.check_knowledge_base` calls `ensure_ready()`, which on a World with no
index runs `index_all()` — a full embedding pass **and an index write**. At
baseline that was local `hash_embedding`; W9 swapped in the network embedder
without revisiting callers. Consequences: a read-only diagnostic writes data;
it contradicts `pkb_reembed.py`'s own "only path that (re)writes `pkb_pages`"
docstring; and under `LAB_MODE=hybrid` it makes `doctor` a corpus-egress path.

**This was reproduced accidentally on the operator's real `finance` World** —
the reviewer's doctor sweep built it a 41-row, 768-dim index at
`lab/instances/finance/pkb/.cache/lancedb/`. Orchestrator verified the blast
radius: exactly one World written (`finance`, the abandoned scaffold that the
migration deliberately skipped), no pre-existing data overwritten, content
correct, and **no egress occurred** — `MODEL_API_BASE` is unset so every
embedding call went to loopback. The reviewer disclosed it against its own
interest.

### Regression claim verified as real
52F/4341P reproduced exactly; collection 4193→4421 is growth with no new skips.
Decisive check: the 29 failing files run in isolation at `8cb5760` and at HEAD
produce **byte-identical failing-test-ID sets**. Note for QA: 52 in a full run
vs 34 in isolation — ~18 failures are pre-existing order-dependence, so the
"52 vs 53" delta is noise, not improvement.

### QA attack list (carried forward)
1. `doctor` must perform zero embeds and create no index — no test exists.
2. The `"empty"` code is sticky in-process: a populated table serves fine while
   `X-Retrieval-Status: degraded` is stamped forever.
3. Latency: `_table()` is 7.5 ms of a 20.8 ms query and is opened three times
   per search. The health check itself is 0.105 ms — not the cost.
4. A stale `reembed.lock` after SIGKILL wedges the very verb the degrade
   message tells the user to run.
5. Buddy is silently keyword-only on four of five real Worlds — the deferred
   C1 surface is the half that matters, and `X-Retrieval-Status` has zero
   consumers.
6. `search_vector`'s `VectorSearchError` branch is unexercised; shadow
   verification is cardinality-only.

## ORCH-1 — regression introduced by the BLOCK-3 fix (found by the orchestrator)

Caught during orchestrator verification of the BLOCK-3 fix, *before* re-review.
Verifying rather than trusting the build report is what surfaced it.

`ensure_ready` short-circuits on a module-global `if _initialized: return`. The
new `build=False` path sets that flag exactly as `build=True` does — while
deliberately doing no work. So the first read-only check permanently disables
index building for the rest of the process.

Reproduced in one process on a temp root: `ensure_ready(root, build=False)`
then `ensure_ready(root, build=True)` leaves **no index**; `build=True` alone
in a fresh process builds correctly.

`_initialized` is per-process, so `doctor` (separate process) is unaffected.
The hazard is inside the **portal** process: `pkb._semantic_search` now runs
the health check on every query, so after any search a later genuine
content-write path (World mount, voice/OCR note capture) would silently fail
to index its new content — and then tell the user to run `pkb reembed` to fix
something that should have just worked.

**Orchestrator's reachability sketch was WRONG, corrected by the builder with
evidence.** I claimed the portal process could hit this via
`pkb._semantic_search`'s per-query health check. It cannot: `pkb.py` contains
**zero** references to `ensure_ready` (it calls `check_read_path_health()`
directly), and `ensure_ready(build=False)`'s only caller is
`doctor.check_knowledge_base()`, invoked solely as a fresh
`python -m arail.doctor` subprocess. Module globals do not cross processes, so
no shipped call path combines them. It remains a genuine contract defect and
was fixed on those grounds, not on my incorrect severity claim. Verified after
the fix: read-only-then-build now builds.

Invariant handed to the builder: `ensure_ready(root, build=False)` followed by
`ensure_ready(root, build=True)` in one process must build the index and reach
the same end state as `build=True` alone. Noted that `_initialized` is a
sibling of the already-filed module-global degraded-state debt (process-wide
flags vs per-World roots); closing both together is acceptable, widening beyond
`pkb_index.py` state handling is not.

## Review 4 verdict (2026-08-08) — WEAK_PASS

All three prior BLOCKs dead, verified by execution. BLOCK-3 measured
definitively: `MODEL_API_BASE` pointed at a request-logging stub, a real
`python -m arail.doctor` subprocess against an unindexed 40-file World made
**exactly one embed request of 5 bytes** (the reachability probe) and created
no `.cache/`. `build=False` did not cost doctor its teeth — still exit 3 on a
legacy 128-dim World. ORCH-1 fixed, not relocated; root isolation holds both
directions. The review was fully read-only against the operator's lab (all five
Worlds' index mtimes unchanged; `finance` still shows only REVIEW3's disclosed
accident).

**Shared root cause: contained, but by an invariant nobody wrote down.**
`arail.config.PKB_ROOT` is a module constant with **zero** in-process
rebindings anywhere in `src/`, and concurrent Worlds are process-per-World.
That single fact is what makes `_degraded_codes`, `_pending`, and
`_pkb_root_cache` unambiguous — and it currently exists only in a backlog file.
**It must be written down as an invariant.**

The `_degraded_codes` deferral was ruled **safe**: the tripwire is
architectural rather than incidental, the BACKLOG entry names the mechanism and
the whole family of globals, and the builder shipped a test that *reproduces*
the leak. That is the right way to defer.

### Two new findings, same class, neither reachable
- **ASK-1** — `ensure_ready` sets `_pkb_root_cache` **outside** the `if build:`
  guard, one line below the ORCH-1 fix, whose own comment claims `build=False`
  writes nothing. A read-only call on World F redirects the process-wide flush
  target from E to F; since `_pending` holds root-*relative* paths the payload
  would be a **cross-World write** — worse than ORCH-1's no-op. One-line fix.
- **ASK-2** — the new stale-lock recovery reintroduces BLOCK-2(d) under a narrow
  precondition. Mutual exclusion broken with real code: both processes hold, and
  the lock file carries the second's PID during the first's write phase. TOCTOU
  window measured at **1.3 µs median / 6.5 µs max** over 200 samples, needing a
  250,000× widening to demonstrate — hence ASK, not BLOCK. `fcntl.flock`
  deletes the entire heuristic.

**Regression:** 379 passed / 0 failed across 25 isolated suites; the 11F/7E in a
1237-test targeted selection are byte-identical against a clean `8cb5760`
worktree.

WEAK_PASS rather than PASS because `_table()` triple-open became a ticket and
Buddy remains silently keyword-only on four of five real Worlds.

### Orchestrator decision
Send ASK-1 and ASK-2 back to the builder **before** QA — both are cheap, both
are in the family that has already produced three defects, and QA's attack list
covers the same ground (stale-lock behaviour, cross-World contamination probe),
so fixing first avoids a re-loop. No fifth full review: the fixes are surgical
and QA verifies them.

## QA verdict — FAIL

72 tests committed, weighted 30% setup / 30% Buddy / 20% security / 10% happy /
10% regression.

### QA-5 (HIGH, blocks ship) — `/api/pkb/search` 500s on every degraded World
`api_pkb_search` stamps `X-Retrieval-Reason: reason[:200]`. Starlette encodes
header values as **latin-1**, and every degraded message `pkb_index` emits
contains an **em dash**:
`UnicodeEncodeError: 'latin-1' codec can't encode character '\u2014'`.
Reproduced end to end against a scratch copy of the real `qukaizen` World. Hits
four of five Worlds plus the root lab (legacy 128-dim -> `dimension`), any
unbuilt index (`empty`), and every clean machine that has not pulled the model
(`provider`). It backs the KB search box in `dashboard.html`, `agents.html`,
`docs_hub.html`. At `8cb5760` the endpoint was `return pkb_search(q.strip())`
and could not 500 — **this sprint introduces it**. The mechanism added to make
degradation honest is what breaks the surface. The sprint's own test missed it
because the fixture reason is a hand-written ASCII string rather than one the
product actually emits. Fix is ASCII-folding at one call site, which also
closes QA-4 (CR/LF/NUL from a hostile provider body).

### QA-6 (MEDIUM, pre-existing, NOT a regression) — Buddy has zero retrieval
`approved_paths()` is **empty on all six real PKB roots** and the Compiled-KB
gate ships on, so `pkb.search(approved_only=True)` returns `[]` *before*
`_semantic_search` runs. Buddy gets 0 hits on a legacy World **and** on a
re-embedded one — and `retrieval_status()` returns `(True, "")`, so no degraded
code is ever set on that path. This matches Phase 1 audit finding **A36**
(`compiled_kb.py:109` fails closed to `set()` with the gate default on) and the
historical 554/556-corpse-approvals bug recorded in `world_mount.py:1316`.

**Consequence for this sprint:** the deferred Buddy context header, built
exactly as C1 specifies, would print a **false "retrieval healthy."** QA's read,
which the orchestrator accepts: the deferral does not block ship, but *building
that surface as designed* must not happen until the agent path consults the
health check. Corrects the orchestrator's repeated claim that Buddy is
"keyword-only on four of five Worlds" — it is worse than that, and it is
pre-existing rather than caused here.

### The upgrade is real and measured end to end
Same World, before/after a real reembed, through `lab_brain.retrieve_chat_context`:
relevant top-1 went **0/6 -> 6/6** (`.wiki-cache/manifest.json` ->
`terms/upscaling.md`, `terms/gpu-driver.md`, `terms/force-feedback.md`), latency
unchanged (349 ms -> 332 ms). The +40.6pp is not overstated — it simply cannot
reach the operator until QA-5 and QA-6 are resolved.

### Real-lab boundary honoured
A 17,015-entry `stat` inventory of `lab/` before and after the pass: **`diff` is
empty**. Six `doctor` runs against the real roots gave **3/3/3/3/0** plus root 3,
with zero writes and zero embeds beyond the 5-byte probe.

### What QA could not break
Reverting ASK-1 fails 3 tests; removing `flock` fails 5. Two OS processes under
six CPU spinners, five trials: exactly one winner every time. SIGKILL at 24/40
rows left the live table byte-identical with `--resume` completing. The egress
allowlist refused all 10 adversarial host forms, both `OLLAMA_HOST` shapes,
every non-`hybrid` `LAB_MODE`, and 301/302/307 redirects. `_table()` measured
**0.39–1.21 ms**, not 7.5 — the deferral is justified and REVIEW3's latency
concern was overstated. Reembed incidentally collapses `ai`'s 2,421 fragments
to 1.

## QA round 2 — WEAK_PASS ("would I ship this to someone's family? Yes")

QA-5 confirmed **genuinely dead**: the original repro re-run against scratch
copies of the real `video-games` and `qukaizen` Worlds in all five degraded
states returned **200 every time**, and `h11.Connection.send` — the exact call
that rejected the old value — accepts the new `raw_headers`. QA-4 closed: a
hostile body with CRLF, NUL, and emoji produced no injected header.

**The new test is honest.** Four mutations confirm it: removing the fold → 5
red; removing the strip → 4 red; removing the em dash *from the product
message* → red; making the dimension check stop degrading → red. It cannot pass
vacuously and cannot drift from what the product emits.

**QA disclosed a defect of its own.** Three round-1 fixtures called
`importlib.reload(arail.dbspec.embed)`, minting a fresh `EmbeddingError` class
and silently downgrading C1's LOUD branch to SKIP for whatever ran next —
breaking three `test_c1_error_contract` tests order-dependently. Rebuilt on the
un-stubbed `embed_texts`; three consecutive combined runs give 196 passed /
8 xfailed / 0 failed. Recorded because "it is exactly what I'd have filed
against the builder."

**Real lab unchanged**: 17,015-entry `stat` inventory, empty diff at round-2
start and end.

### Operator guidance before the first real `pkb reembed`
- **Fast, safe with the lab running.** Largest World (`ai`, 419 rows) = **4.3 s**.
  94 searches through a live swap: zero exceptions; in-flight ones degraded to
  keyword and recovered.
- **Doubles the index dir until cleaned up.** `.bak-<ts>` is never auto-pruned —
  79 MB beside a 2 MB new table on `ai`. It is also the rollback. Deleting it
  reclaims ~77 MB, because the re-embed defragments 2,421 fragments to one.
- **Run from the primary checkout, not a worktree** — the docs-registry slice is
  global (38 of `video-games`' 356 rows), so a worktree's `docs/` would land in
  the World.
- **Do all five (`--all`)** — until re-embedded, natural-language questions
  return *nothing*, because the keyword fallback is a whole-query literal
  substring sweep.
- **It will not fix Buddy** — QA-6: zero approved paths on all six roots.

## Final regression verification (orchestrator, 2026-09-09)

The pytest cache is cumulative across targeted runs, so it listed three sprint
files as failing even though they pass in isolation. That is exactly the shape
of the order-dependence QA found in ~18 of the baseline failures, so it was
resolved rather than assumed: cache cleared, clean full-suite run with
`-p no:cacheprovider`, failure list captured directly.

**Result: 60 failures/errors, NONE from any sprint file.** The cache entries
were stale. No order-dependence was introduced. This is the fourth independent
confirmation the failing set matches baseline `8cb5760` — orchestrator per-file
comparison, builder scratch-worktree comparison, QA isolation comparison, and
this run.

## Skipped phases

| Phase | Reason |
|---|---|
| — | none skipped; scope narrowed rather than phases dropped |

## Visionary verdict (2026-08-08)

**proceed**, narrowed to roughly one third of the original proposal:

- **Tier 1.2 — proceed, as a measurement.** Build a committed labelled fixture
  (>=20 questions, >=4 per world, hand-labelled against the live 716-row
  corpus) and an A/B harness. nomic must beat `hash_embedding` by **>=15pp
  pooled recall@5** with zero rank-1 losses on an exact-token fixture. Below
  the bar we do not ship the dependency, and we file an ADR recording hash
  embeddings as a *measured* choice.
- **Tier 1.1 — defer.** No query path exists to leak through today.
- **Cutover — reject.** Consolidating five isolated stores into one buys
  cross-world queryability this operator does not want (he runs Worlds one at
  a time) and pays for it by demoting isolation from a directory boundary to
  a WHERE clause that audit finding A31 says no production caller has ever
  passed.
- **Separately:** the surviving real defect is A25/A26 —
  `unmount(remove_staged=False)` as the default on the root-lab mount path.
  It is a stop-and-switch bug, it is cheap, and it needs no database.

### Concerns the architect must address at design time

1. **The fail-loud guarantee dies in the plumbing.** `embed.py` never
   substitutes an embedding — but `pkb_index.py` has **15** `_log.warning`
   swallow points, including three around `index_all`
   (`pkb_index.py:178, 326, 347, 403`). An embedding outage would surface as a
   silently empty index, which is worse than the old behaviour.
2. **Re-embedding must be explicit and resumable.** The 128 -> 768 change hits
   the drop-and-rebuild path (`pkb_index.py:329-347`), and `_semantic_search`
   (`pkb.py:579-583`) calls `index_all` lazily when the table is empty — so a
   user typing in the search box would trigger hundreds of synchronous Ollama
   round trips.

Orchestrator verified claims 1 and 2 and the per-instance isolation finding
directly against the source before accepting the verdict.

## Product gating (arail)

Per `CLAUDE.md`: setup-on-clean-machine, Buddy quality, security (it runs on
other people's machines), onboarding clarity, failure-mode grace. QA
allocation shifts to 30% setup / 30% Buddy / 20% security / 10% happy /
10% regression.

Specific to this sprint:
- Ollama + `nomic-embed-text` becomes a hard requirement of PKB ingest. The
  clean-machine path must either pull it during setup or degrade with an
  actionable message — never silently write hash vectors.
- `LAB_MODE=airgapped` must still hold: the embedding provider is local-only.

## Notes

Baseline for regression comparison: 28 pre-existing failures over the 21
suspect test files at commit `8cb5760` (see predecessor SPRINT.md). Any new
failure against that baseline is a regression introduced by this sprint.
