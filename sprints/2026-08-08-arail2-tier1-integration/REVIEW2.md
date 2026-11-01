# Review 2: the conditional integration (W6–W10)

**Date:** 2026-08-08
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) at `a84146e`
**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `3987f71`
**Review 1 (W0–W5, PASS + binding amendments):** [REVIEW.md](./REVIEW.md) at `e40c99e`
**Reviewed commits:** `c94281a`..`a84146e` — `90b56ce` (required actions) · `4af4691` (W6) ·
`5718dc5` (W7) · `4a6b726` (W9) · `a69ff2a` (W8+W10)

## Verdict: BLOCK

Two findings, both reproduced by execution against real code (not read-only inference),
each independently sufficient under this review's stated block conditions:

- **BLOCK-1** — an embedding/provenance problem is silently un-degraded by the first
  search, and queries *are* served from a table whose provenance disagrees with the spec.
  C4's central promise is not enforced on the read path at all.
- **BLOCK-2** — `pkb reembed` can swap a **truncated** index into place and report
  success, and an empty corpus deletes a healthy live index. The shadow build's
  completeness is never verified before the swap.

Everything else in this chunk is good work — genuinely good. `index_all`'s
compute-before-`replace()` ordering is right and structurally enforced, FM12 holds on the
operator's real `debt-finance` data, FM15 holds, the CLI's exit-code contract is honoured,
`setup.sh` degrades correctly offline, and scope discipline is clean. The two blocks are
narrow and each is a small fix. But both are in the class the sprint exists to prevent.

---

## What I executed (nothing below is taken on BUILD_LOG's word)

Real Ollama with `nomic-embed-text` at 768d. Scratch PKB roots under the session
scratchpad. The operator's real `lab/` was touched **read-only** (one `--dry-run`, which
writes nothing — verified) per boundary #5.

| # | Scenario | Result |
|---|---|---|
| 1 | Legacy 128-dim hash table + `ensure_ready` | degrades, does **not** drop, 6/6 rows intact — **FM12 holds** |
| 2 | …then one `pkb.search()` | **degraded flag cleared**, `doctor` → "vector index status: ok", exit 0. BLOCK-1 |
| 3 | Sidecar rewritten to a foreign model (same dim) | `ensure_ready` degrades, `doctor` exits 3 — then a search returns **12 hits, `source="semantic"`**, from the disagreeing table, and clears the flag. BLOCK-1 |
| 4 | `run(resume=True)` with a checkpoint whose shadow build is gone | **38 of 78 rows** swapped live; result `{"completed": 78}`; sidecar claims `rows: 78`; exit 0. BLOCK-2 |
| 5 | Empty corpus + existing live table | live `pkb_pages.lance` renamed to `.bak-<ts>`, nothing swapped in, sidecar written `rows: 0`, "done: 0/0 rows re-embedded", exit 0. BLOCK-2 |
| 6 | Two concurrent `pkb reembed` on one root (238 rows) | A died with a raw `lance error: Incompatible transaction … conflict_resolver.rs:855` (exit 1); B finished correctly. Outcome was luck of interleaving; there is no lock |
| 7 | `index_all` with provider down (closed loopback port) | raises `EmbeddingUnavailable`, **writes nothing**, 20/20 rows intact, message names `ollama pull nomic-embed-text` — **FM10/FM15 hold** |
| 8 | `pkb reembed` with provider down | exit **4**, actionable message, live index untouched — correct |
| 9 | `_semantic_search` with provider down | degrades, flag **stays** set, `search()` falls through to regex, `source="keyword"` — correct |
| 10 | `MODEL_API_BASE=http://evil.example`, `LAB_MODE=airgapped` | `EmbeddingError` before any socket — C3 holds (see note on the operator's `.env` below) |
| 11 | Real `lab/instances/debt-finance/pkb` | 79 rows @ 128d, no sidecar; `ensure_ready` degrades, does not drop; `--dry-run` → 120 rows, ~77 rows/s, ~2 s, **zero writes** |
| 12 | Scope | `git diff 8cb5760 -- vector_index.py world_mount.py scripts/start.sh wiki_vectors.py scripts/lib/instances.sh` → **empty**. Independently confirmed |
| 13 | Embedder-selection flag / fallback (C6) | `grep` for `ARAIL_EMBEDDING` and any `except EmbeddingError … hash_embedding` → **none**. C6 holds |
| 14 | Regression spot-check | `pytest -k "pkb or docs or wiki or knowledge or dac or doctor or agent"` → 4 failed / 572 passed. All 4 (`test_dac_rename`, `test_docs_routes` ×3) are in the builder's declared pre-existing clusters and touch no file this sprint changed. Sprint's own suites: **114 passed** |

---

## BLOCK-1 — the degraded flag is erased by the read path, and provenance is not enforced there

**Where.** `pkb.py:_semantic_search` calls `pkb_index.clear_degraded()` unconditionally
as soon as `embed_query()` succeeds, *before* the kNN. `pkb_index._flush` does the same
after each successful upsert. But `_degraded` is a single module-global set for **five
different reasons**, only one of which `embed_query` succeeding says anything about:

| Reason set by | Cleared by a successful `embed_query`? | Should be? |
|---|---|---|
| `embed_query`/`embed_documents` outage | yes | yes |
| dimension mismatch (`ensure_ready`) | **yes** | no |
| provenance disagreement (`ensure_ready`) | **yes** | no |
| missing provenance / legacy index (`ensure_ready`) | **yes** | no |
| index empty (`_semantic_search`) | **yes** | no |

**Consequence, on every lab that exists today** — including the operator's five real
Worlds, all of which are 128-dim with no sidecar (scenario 11):

1. Portal boots → `ensure_ready` degrades correctly, message names `pkb reembed`. Good.
2. The user types one thing into search (or saves one PKB file) → flag cleared.
3. `./arailctl doctor` now prints `vector index status: ok` and exits **0** (scenario 2).
4. Semantic search returns nothing, forever, until someone happens to run `pkb reembed`.
   The regex fallback fires, so the lab *looks* like it works.

That is precisely FM10 restated: an embedding-subsystem problem presenting as an empty
corpus, with the diagnostic the sprint built going green on top of it.

**And the C4 read-side check is not a read-side check.** It lives only in
`ensure_ready`, which runs once per process behind an `_initialized` guard.
`_semantic_search` never consults `pkb_provenance` and never reads `embedding_status()`
before serving. Scenario 3 is the proof: with a sidecar claiming a *different model at the
same dimension*, the search returns **12 semantic hits** out of a foreign vector space and
clears the warning that said not to. ARCHITECTURE.md C4: *"No query is served from a table
whose provenance disagrees with the spec."* It is served.

Two secondary contributors, both worth fixing in the same pass:

- `pkb._table_search_by_vector` catches bare `Exception` and returns `[]`. On a legacy
  128-dim table the LanceDB dimension error lands here and becomes silence — a 16th
  swallow point, added by the commit whose purpose was removing the other 15.
- `doctor`'s exit-3 trigger is `"provenance" in embed_reason` — a substring match on a
  human-readable sentence. The dimension-mismatch reason (the *most common* legacy state,
  and the one on all five real Worlds) does not contain that word, so a 128-dim index is
  INFO-only and doctor exits 0 even before the flag gets cleared. Couple the exit code to
  a structured reason code, not to prose.

**And C1's user-visible half was never built.** `pkb.retrieval_status()` has **zero
callers** in the tree. `/api/pkb/search` (`app.py:11223`) still returns a bare list — no
status envelope. No `/knowledge` banner. No Buddy context-header line. C1 required all
three ("surface in the search API payload and the `/knowledge` banner"; "an agent must not
be handed keyword-only results while the UI claims semantic retrieval"). BUILD_LOG's own
docstrings say "(once wired)" — it was not wired, and the ledger does not record the
deferral as a decision. With BLOCK-1 in place, `doctor` was the *only* surface, and
BLOCK-1 turns it off. Net effect: an embedding outage is invisible in the portal.

**Required fix.** Reason-scoped degraded state: `set_degraded(code, reason)` /
`clear_degraded(code)`, where a successful embed clears only the `provider` code and
leaves `provenance` / `dimension` / `empty` alone (those clear only on a successful
`reembed`/`index_all` — `pkb_reembed` already calls `clear_degraded()` at the right
moment). `_semantic_search` must refuse to serve vector hits while a
provenance/dimension code is set, and fall through to keyword with `source="keyword"`.
Wire `retrieval_status()` into the search payload and the `/knowledge` banner, or record
an explicit, operator-visible deferral in SPRINT.md.

---

## BLOCK-2 — `pkb reembed` can swap a truncated index in and call it success

The shadow-build-and-swap shape is right. What is missing is any check that the shadow
build actually contains what the checkpoint claims before the swap fires.

**Scenario 4 (reproduced).** Checkpoint says 40/78 paths done; `.cache/lancedb.next` is
absent. `--resume` opens no table (`if completed_paths:` finds none to open), embeds only
the 38 `remaining` rows, `create_table(mode="overwrite")` on the first batch, swaps, and
reports `completed: 78 / total: 78`, sidecar `rows: 78`, exit 0, "done". The live table
holds **38 rows**. Provenance agrees with the spec, so nothing downstream ever notices.
Half the user's KB is gone and every health check says fine.

Reachable by: a cleanup or `reset` path removing `.next` (it is an obviously-disposable
"leftover" directory), a `.next` write failing where the checkpoint write succeeds, or a
second run's `shutil.rmtree(_shadow_dir(...))` racing a first run's checkpoint (scenario
6 — there is no lock; `run()` deletes the shadow dir of any concurrent run that did not
pass `--resume`).

**Scenario 5 (reproduced).** `total == 0` and a live table exists → the live table is
renamed to `.bak-<ts>`, nothing replaces it, the sidecar is written `rows: 0`, exit 0.
A World whose PKB dir is momentarily empty or not yet populated loses its index to a
command that reports success. `test_empty_corpus_writes_zero_rows` asserts the counters
and never checks what happened to the live table.

**Also in this area, non-blocking but load-bearing:**

- `_current_spec_sha256()` returns `""` in every invocation I ran (`load_spec` fails from
  a non-repo cwd). The checkpoint's `spec_sha256` guard and the sidecar's field are
  therefore vacuous today — `--resume`'s refusal rests on `model` and `dim` alone.
- `.bak-<ts>` dirs accumulate (I produced three in one test root) with no pruning and no
  mention in the CLI help. The rollback plan depends on the user finding them.
- Concurrent runs surface as a raw Rust conflict-resolver string. Not user-facing English.

**Required fix.** (a) Before the swap, assert the shadow table's row count equals
`total`; on mismatch, discard the shadow build and the checkpoint and exit non-zero
telling the user to re-run without `--resume`. (b) On `--resume`, if the shadow table is
missing or its row count ≠ `len(completed_paths)`, discard the checkpoint and start over.
(c) Refuse the swap when `total == 0` and a live table exists. (d) An `O_EXCL` lock file
under `.cache/` so a second concurrent run exits with a sentence rather than a Lance
traceback.

---

## Spec adherence

Honoured, and in several places better than the letter:

- **C1 ordering** — W6 landed the error contract before the swap, exactly as REVIEW.md
  required. The four `_log.warning` swallow points around `index_all` are now one shared
  `_index_all_reporting_embedding_errors` with `EmbeddingError` caught first, ERROR log,
  `activity_log` severity `error`, degraded set. `_flush` aborts rather than per-path
  retries and re-arms at 60 s (FM17). All correct.
- **`index_all` ordering** — every vector computed by one batched `embed_documents` call
  before `replace()` is reached. There is no code path that constructs a row without its
  vector; the zip is over an already-materialised `vectors` list. Structurally enforced,
  as claimed, and scenario 7 confirms it behaviourally (existing table byte-intact after
  a mid-flight provider failure).
- **FM11** — the lazy `index_all` inside `_semantic_search` is gone; the empty-index path
  makes zero embed calls and names the command.
- **FM12** — verified on the operator's real `debt-finance` index: degraded, not dropped.
- **C5** — `setup.sh`'s pull is idempotent, inherits every existing skip guard, is
  timeout-bounded, and warns-and-continues with the exact manual command. An offline
  clean machine gets a working lab with a degraded KB and an actionable message, not a
  failed setup.
- **C6** — no selection flag, no fallback branch. Verified by grep and by reading.
- **Boundaries** — `vector_index.py`, `world_mount.py`, `scripts/start.sh`,
  `wiki_vectors.py`, `scripts/lib/instances.sh` byte-identical to `8cb5760`. `dbspec/`
  migrate/reconcile/repo/spec/generated untouched by this sprint. Chat-memory exclusion
  intact and now joined by a correct, narrowly-scoped `.cache` exclusion.

**Drifts, both acknowledged and both accepted:**

1. Chunk ordering W6 → W7 → W9 → W8+W10 rather than the architecture's numbering.
   Provenance *writing* genuinely cannot precede a writer. Fine.
2. The `.cache`-in-`_iter_pkb_files` fix is a `pkb.py` edit not named by C1/C2/C4 — but
   C2's own new state files created the bug, so fixing it is in scope. Correctly refused
   to generalise it to the filed `.wiki-cache` defect. Good discipline.

**One drift not acknowledged:** C1's search-payload / banner / agent-header wiring was
dropped without a note. That belongs in the ledger.

---

## On the `_table_search_by_vector` duplication (the orchestrator's question)

**The boundary produced the duplication, and it should be resolved now, not filed.**

The builder's reasoning is honest and the docstring is exemplary. But the boundary it
honoured was written for the *pre-gate* phase — "untouched **unless and until the gate
passes**, and then only as specified in C1/C2/C4". C1/C2/C4 do not name a
`vector_index.py` change for the same reason I did not anticipate it: I did not foresee
that swapping the embedder requires an entry point that accepts a precomputed query
vector. That is a gap in my design, not a prohibition the builder should have routed
around. The byte-identity of `vector_index.py` was valuable as evidence that the *no-ship*
branch changed nothing; post-gate it buys nothing.

What the workaround actually cost:

- The score transform `1 - dist/2` and the `_distance`/`vector` field-stripping now exist
  in two places. `pkb_pages` and `wiki_nodes`/`experiments` will silently diverge in
  ranking semantics the first time one is touched.
- The copy lost `where` support (unused today, a trap tomorrow) and — materially —
  reintroduced a bare `except Exception: return []`, which is how the dimension mismatch
  in BLOCK-1 becomes silence.
- `pkb.py` now reaches into `idx._table()`, a private attribute, across a module boundary.

**Amendment to ARCHITECTURE.md (binding):** boundary #6 is relaxed for
`vector_index.py` to permit exactly one addition — a `search_vector(vector, *, k,
min_score, where=None)` method (or a `vector=` parameter on `search`) with the existing
`search()` delegating to it, so there is one post-processing implementation.
`_table_search_by_vector` is deleted and `_semantic_search` calls the new entry point.
Any error the underlying search raises must be distinguishable from "no hits" at that call
site — that is what BLOCK-1's fix needs.

---

## Code quality findings

- [BLOCK] `pkb.py:_semantic_search` — unconditional `pkb_index.clear_degraded()`; see
  BLOCK-1.
- [BLOCK] `pkb_index._flush` — same unconditional `clear_degraded()` on each successful
  upsert; a single file save clears a provenance warning.
- [BLOCK] `pkb_reembed.run` — swap fires without verifying the shadow build's row count;
  swap fires on `total == 0`; see BLOCK-2.
- [BLOCK] `pkb._table_search_by_vector` — bare `except Exception: return []` on the
  agent-facing retrieval path.
- [ASK] `doctor.check_knowledge_base` — `is_provenance_issue = "provenance" in
  embed_reason`. Substring matching on a user-facing sentence to decide an exit code.
  Use a reason code. (The *decision* — provider-unreachable is INFO, provenance is
  required — is right, and the builder's reasoning against contradicting C5 is correct.)
- [ASK] `pkb_index` degraded state is a module global while PKB roots are per-World. In a
  process that touches more than one root (`ensure_ready` caches `_pkb_root_cache` and
  early-returns on `_initialized`), one World's status describes another's. Serial World
  usage makes this survivable today; it is a landmine for the concurrent-Worlds path.
- [ASK] `pkb_reembed.run` is ~140 lines doing arg handling, checkpoint policy, shadow
  build, swap, and provenance. The swap block specifically wants to be its own function
  with its own preconditions — that is where both BLOCK-2 halves live.
- [ASK] `tests/conftest.py`'s autouse `_stub_embedding_provider` is the right call for
  FM18, but it stubs the provider with `hash_embedding` at 768d for the **entire** suite.
  A production path that fell back to hash vectors would now be invisible to every test.
  Add one non-stubbed guard test asserting `index_all` calls the real `embed_documents`
  symbol.
- [INFO] `--yes` is accepted and documented as a no-op. Prefer not shipping a flag that
  does nothing.
- [INFO] `pkb_provenance.py` is clean: temp-file + rename, never raises on read, missing
  sidecar explicitly is *not* "agrees". The module is right; its read-side consumer is not.

## Security findings

- [INFO] **C3 holds.** `MODEL_API_BASE=http://evil.example` under `LAB_MODE=airgapped`
  raises before any socket, from the `pkb reembed` CLI as well as in-process.
- [ASK] **Worth telling the operator:** this machine's `.env` sets `LAB_MODE=hybrid`
  (line 57), so on *this* box the corpus-egress guard is off by configuration — a
  mistyped `MODEL_API_BASE` would ship PKB text (including `debt-finance`) off-box with
  only an INFO log. Not a code defect; the guard behaves exactly as designed. It is a
  configuration fact the operator should know now that bulk embedding is live.
- [ASK] REVIEW.md's ASK-4 (document that a LAN-hosted Ollama is refused under the default
  `airgapped`) was not addressed in the W10 docs pass. Still a QA-phase item.
- [INFO] No new LanceDB predicate interpolation; `_table_search_by_vector` takes no
  `where`. `_flush`'s existing quote-escaping is unchanged. No secrets read or logged.
- [INFO] Sidecar and checkpoint contain no user content — model name, dim, counts,
  timestamps, and paths only in the checkpoint (which stays under the PKB root, never
  committed, and is now excluded from indexing).

## Test coverage assessment

114 passing across the sprint's suites; 36 new tests this chunk. The FM table maps well
where it maps — and the gaps are exactly where the blocks are.

| FM | Covered | Where / gap |
|---|---|---|
| FM10 no silent empty index | partial | `index_all` half fully covered. The **search-path** half (BLOCK-1) has no test |
| FM11 no lazy embed from search | yes | `test_semantic_search_never_calls_index_all_on_empty_index` |
| FM12 dim mismatch never drops | yes | plus verified on real `debt-finance` data |
| FM13 interrupt/resume | partial | SIGINT + resume covered. **No test for an inconsistent checkpoint/shadow pair** (BLOCK-2) |
| FM14 provenance can't masquerade | **no** | `ensure_ready` is tested in isolation; nothing asserts a query is *refused*, and the actual behaviour is that it is served (scenario 3) |
| FM15 clean machine | yes | closed-port test uses the real `embed_documents`, correctly |
| FM17 back-off | yes | `test_flush_single_failure_does_not_rearm_at_normal_debounce` |
| FM18 no-Ollama CI | yes | global stub — see the ASK above |

Missing tests QA must add regardless of how the blocks are fixed:

1. degraded-for-provenance survives a successful search, and that search serves no
   semantic hits;
2. degraded-for-dimension survives a successful `_flush`;
3. `--resume` with checkpoint/shadow disagreement never swaps;
4. `total == 0` with an existing live table never swaps;
5. two concurrent `reembed` processes on one root — one must lose cleanly;
6. `doctor` exits 3 on a legacy 128-dim index (today it exits 0).

## Performance assessment

Measured on this machine with real Ollama: 78-row root ≈ 40–175 rows/s depending on batch
composition; real `debt-finance` dry-run probe 77 rows/s over 120 rows (~2 s). Consistent
with REVIEW.md's 75–134 rows/s planning figure, which `docs/cli.md` correctly documents.
`--dry-run`'s live 32-row probe is the right design. No hot-path regression: search still
does one `embed_query` round trip plus a flat scan.

## Tech debt delta vs prediction

The four predicted items are filed in `sprints/BACKLOG.md` as required. Unpredicted debt
this chunk added, to be filed before PASS:

1. **A second copy of `VectorIndex.search`'s post-processing** — resolve rather than file
   (see the amendment above).
2. **Module-global degraded state against per-World roots** — file.
3. **A suite-wide embedding stub** that makes the real provider path untestable by
   default — file, with the one guard test as the mitigation.
4. **`.bak-<ts>` accumulation with no pruning and no user-facing mention** — file.

## debt-finance as a first-class verification target

Confirmed genuinely uniform: no code in this chunk branches on a World slug, name, or
bundle status; every entry point is parameterised by `pkb_root: Path`. I exercised the
real `debt-finance` World read-only — 79 rows @ 128d, no sidecar, `ensure_ready` degrades
without dropping, `--dry-run` reports 120 rows / ~2 s and writes nothing. It is not
special-cased, and it is squarely inside BLOCK-1's blast radius: it is one of the five
Worlds that will look healthy while returning nothing.

## Required actions before re-review

1. **Fix BLOCK-1.** Reason-scoped degraded state; `_semantic_search` must not clear codes
   it knows nothing about, must not serve vector hits under a provenance/dimension code,
   and must not swallow the underlying search error.
2. **Fix BLOCK-2.** Verify the shadow build's row count against `total` before the swap;
   discard an inconsistent checkpoint on `--resume`; refuse the swap on `total == 0` with
   a live table present; add a lock file.
3. **Take the `vector_index.py` amendment** (boundary #6 relaxed for one additive
   method); delete `_table_search_by_vector`.
4. **Wire `retrieval_status()`** into the `/api/pkb/search` payload and the `/knowledge`
   banner — or record the deferral explicitly in SPRINT.md as an accepted C1 gap with a
   backlog entry. Silence is not an option; it is the whole point of the chunk.
5. **Decouple doctor's exit code from prose** and make a legacy 128-dim index exit 3.
6. **File the four unpredicted debts** above.
7. **Add the six tests** listed in the coverage section.

ASK-4 (LAN-Ollama doc), the `LAB_MODE=hybrid` note to the operator, and the
`spec_sha256 == ""` observation are QA-phase items; they do not gate re-review.
