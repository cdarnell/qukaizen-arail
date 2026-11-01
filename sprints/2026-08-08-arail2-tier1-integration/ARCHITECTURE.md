# Architecture: Tier 1.2 as a measurement — nomic vs `hash_embedding`

**Date:** 2026-08-08
**Spec:** [VISION.md](./VISION.md) at `e79a3b9` (binding)
**Sprint ledger:** [SPRINT.md](./SPRINT.md) at `4c9f6ca`
**Baseline commit:** `8cb5760`
**Predecessor evidence:** [`../2026-08-08-arail2-declarative-persistence/PHASE1_AUDIT.md`](../2026-08-08-arail2-declarative-persistence/PHASE1_AUDIT.md), [`INTEGRATION.md`](../2026-08-08-arail2-declarative-persistence/INTEGRATION.md)

---

## Restatement

ARAIL's PKB "semantic search" is a 128-dimension SHA1 token-hash projection — lexical
overlap wearing a semantic label. `arail.dbspec.embed` already exists and serves
nomic-embed-text at 768 dimensions through local Ollama, but nothing in the running lab
calls it. The obvious move — swap the provider — has never been measured against the
thing it would replace, and shipping it makes Ollama plus a 274 MB model a hard
requirement of PKB ingest on every machine a friend clones this blueprint onto. So this
sprint does not swap anything by default. It builds one committed, hand-labelled
relevance fixture over this repo's live 716-row corpus and one A/B harness that scores
both embedders on it, and it publishes the number. nomic ships **only** if it beats
`hash_embedding` by ≥15 percentage points pooled recall@5 with zero rank-1 losses on a
separate exact-token fixture; otherwise no dependency ships and an ADR records hash
embeddings as a measured choice rather than an accident. The conditional integration
path is designed here in full — error contract, explicit resumable re-embed, provenance,
clean-machine setup — so that if the gate passes, the builder is implementing a decided
design rather than improvising one under the momentum of a good number.

Tier 1.1 (`world_id` query scoping), the cutover to the consolidated 2.0 store, and the
A25/A26 unmount defect are **out of scope**. Do not design or implement them.

---

## Assumptions

Each is stated so that a false one is visible rather than fatal.

**A1.** The live corpus at `/Users/netsushi/ProJects/qukaizen-arail/lab/` is the corpus
worth measuring on: 716 `pkb_pages` rows across five physically separate stores (root 72,
`ai` 381, `video-games` 116, `debt-finance` 79, `qukaizen` 68 — PHASE1_AUDIT §2.2). This
worktree carries no runtime data; the harness reads the primary checkout's `lab/`.
*If false:* the harness takes `--pkb-root` per world and works anywhere; only the
committed numbers are machine-specific, and they are stamped with a corpus manifest hash
(see Interface contract H2) so a reviewer can detect a different corpus.

**A2.** The corpus is rich enough to support 30 honestly-labelled questions. VISION.md
pre-commits the opposite outcome as a finding: if we cannot label them, that *is* the
result ("retrieval quality is not the bottleneck at 716 rows"), and the sprint stops.

**A3.** Ollama is reachable on loopback with `nomic-embed-text` pulled on the
measurement machine. Verified 2026-08-08: `nomic-embed-text:latest`, 137M params, F16,
`embedding_length: 768`, present at `127.0.0.1:11434`. Matches
`generated/models_registry.py:103-118` (`EMBEDDING_DIM = 768`).

**A4.** Embedding is the only variable. Both arms use the **same** row set, the **same**
embedding input string (`f"{name} {rel} {text[:4096]}"`, `pkb.py:524`), the same
LanceDB version, the same top-k, and the same score transform. Nothing else may differ.

**A5.** `hash_embedding` stays in the tree either way. `wiki_vectors.py:22`,
`agent_workflows`, and `experiments` still use hash vectors and are out of scope, so it
is live code, not a preserved fallback.

**A6.** The 15pp bar is a bar over roughly 30 queries, so one query ≈ 3.3pp and the bar
is ~5 queries. This is a small sample and the design says so out loud (see "Where the
visionary underspecified", below). It is not a reason to widen scope.

**A7.** `LAB_MODE=airgapped` is the default and must survive. `embed.py:54-67`
(`ollama_root`) honours an operator-set `MODEL_API_BASE`, which today could point at a
remote host — a corpus-text egress path in airgapped mode. Treated as a defect to close
in this sprint regardless of the gate (see W0).

**A8.** The 28 pre-existing test failures across 21 files at `8cb5760` are baseline. The
builder neither causes nor fixes them.

---

## Data flow

### Measurement path (always ships)

```
 live lab (READ ONLY)                      committed to git
 ────────────────────                      ─────────────────
 lab/pkb/                     ┌────────►  eval/retrieval/corpus_manifest.json
 lab/instances/ai/pkb/        │            (path, name, source_kind, sha256(embed_input),
 lab/instances/video-games/   │             len) per row per world — NO document text
 lab/instances/debt-finance/  │
 lab/instances/qukaizen/      │
        │                     │
        │ pkb._iter_pkb_files │           eval/retrieval/queries.yaml      (≥30 NL)
        │ + pkb._build_docs_  │           eval/retrieval/exact_tokens.yaml (≥8 literal)
        │   rows              │                    │  hand-authored, committed BEFORE
        ▼                     │                    │  any result exists
  row[] per world ────────────┘                    │
        │                                          │
        ├──── hash_embedding(text) ──┐             │
        │     (vector_index.py:35)   │             │
        │                            ▼             ▼
        │                    scratch LanceDB   scoring
        │                    ARAIL_EVAL_       recall@5 · MRR@10 · rank-1
        │                    WORKDIR/          per world + pooled (micro-avg)
        │                    {hash,nomic}/     + lexical-overlap strata
        │                    <world>/          + paired bootstrap 95% CI
        │                            ▲         + embed latency (rows/s, p50/p95)
        ├──── embed_documents(text) ─┘             │
        │     (dbspec/embed.py:159)                ▼
        │            │                    eval/retrieval/results.json  (machine)
        │            └── raises ─► abort, write nothing
        │                                  sprints/.../RESULTS.md      (human)
        ▼                                          │
   NEVER writes to <pkb_root>/.cache/lancedb       ▼
                                            GATE: ≥15pp pooled recall@5
                                                  AND zero rank-1 losses
                                        ┌─────────┴─────────┐
                                     PASS                 FAIL
                                        │                   │
                                 integration           docs/adr/0004-
                                 (below)               hash-embeddings-
                                                       measured.md, stop
```

### Conditional integration path (ships only if the gate passes)

```
  ./arailctl pkb reembed [--world <slug>|--root|--all] [--resume] [--dry-run]
        │
        │  explicit · resumable · progress-reporting · interruptible
        ▼
  read rows ──► embed_documents (batched, ARAIL_EMBED_BATCH)
        │            │
        │            ├── EmbeddingError ─► checkpoint flushed, exit 4, actionable message,
        │            │                     EXISTING pkb_pages untouched
        │            ▼
        │      <pkb_root>/.cache/lancedb.next/pkb_pages.lance   (shadow build)
        │      <pkb_root>/.cache/reembed-state.json             (checkpoint)
        ▼
  atomic-ish swap: pkb_pages.lance → pkb_pages.lance.bak-<ts>; .next → live
        │           provenance sidecar written LAST (see C4)
        ▼
  <pkb_root>/.cache/lancedb/pkb_pages.provenance.json
     {embedding_model, embedding_dim, spec_sha256, rows, written_at}

  ingest / upsert                    query
  ───────────────                    ─────
  pkb.index_all ─┐                   pkb._semantic_search
  pkb_index._flush┘                        │ embed_query
        │ embed_documents                  ├─ ok ─► LanceDB kNN
        ├─ ok ─► write                     └─ EmbeddingError ─► degraded status,
        └─ EmbeddingError ─► NO WRITE,                          regex fallback,
           degraded status, ERROR log,                          honest UI label
           activity event, banner
```

---

## Interface contracts

### F1 — Natural-language fixture: `eval/retrieval/queries.yaml`

**Promises:** ≥30 questions (≥6 per world × 5 stores — stricter than VISION.md's ≥20/≥4,
because at 20 queries one query is 5pp and the bar is three queries). Every entry:

```yaml
- id: dfin-003                      # <world-prefix>-<seq>, unique, stable
  world: debt-finance               # one of: root | ai | video-games | debt-finance | qukaizen
  query: "what order should I pay down cards in if the rates are close?"
  relevant:
    - path: skills/blended-apr-calc/SKILL.md     # pkb-root-relative POSIX, as stored in pkb_pages.path
      evidence: "…verbatim ≤200-char excerpt from that file…"
  author_note: "written from the SKILL body before either embedder was run"
```

**Requires:** `path` exists in that world's `pkb_pages` corpus manifest; `evidence`
occurs **verbatim** in the file at `path`; ≥1 relevant doc per query.

**On bad input:** `tests/eval/test_retrieval_fixture.py` fails the build. It is a lint,
not a warning.

**Honesty mechanics — how a reviewer checks the labels were not stacked:**

1. **Ground truth comes from the document, not the retriever.** The labeller runs
   `scripts/eval/retrieval_ab.py --dump-corpus --world <slug>`, which prints
   `path · name · first 800 chars` for every row, reads documents, and writes questions
   *about what the document says*. Questions are never authored by running a query and
   labelling whatever came back, and never generated by an LLM from the documents it
   will be scored against (VISION.md win-condition 1).
2. **Every label carries a verbatim quote.** You cannot mark a document relevant without
   pasting a passage from it that answers the question. The fixture lint re-reads the
   file and asserts the quote is present byte-for-byte. This is the cheap, mechanical
   check a reviewer repeats in one command: `pytest tests/eval/test_retrieval_fixture.py`.
3. **Pre-registration by commit order.** The fixture lands in its **own commit**, before
   any commit that contains a result. The BUILD_LOG records both shas. `git log --follow
   eval/retrieval/queries.yaml` must show no edit after the first results commit. If a
   label must change (e.g. a quote was mistranscribed), the edit is a separate commit
   with a written reason in BUILD_LOG **and** all results are regenerated. Silent
   post-hoc relabelling is the one thing that voids this sprint.
4. **Lexical-overlap strata are published, not constrained.** Requiring paraphrase would
   stack the deck for nomic; requiring overlap would stack it for hash. So the harness
   *computes* per-query Jaccard overlap between the query's content tokens (same
   `_TOKEN_RE` as `vector_index.py:31`, minus a fixed 40-word stoplist committed
   alongside) and its relevant docs, bins each query as `zero` / `low` (<0.05) /
   `high` (≥0.05), and reports recall@5 per stratum plus the stratum histogram. A
   fixture that is 90% zero-overlap is visibly a nomic-friendly fixture; the number is
   right there in RESULTS.md. **Lint floor:** at least 25% of queries in the `zero`
   stratum and at least 25% outside it, so neither side can be shut out. The gate still
   uses the pooled number.
5. **Privacy lint (arail runs on other people's machines).** `evidence` excerpts are
   committed to a public MIT repo. The lint rejects any excerpt containing an email
   address, a digit run ≥6, a currency amount with ≥4 significant digits, or a string
   matching the committed `eval/retrieval/pii_deny.txt` patterns. The labeller must not
   quote personal financial content from `debt-finance` (its `research/program.md` is
   the file to watch); prefer the generated skill/agent/world-term pages. BUILD_LOG
   carries a one-line human sign-off that the excerpts are safe to publish.

### F2 — Exact-token fixture: `eval/retrieval/exact_tokens.yaml`

≥8 literal queries (file names, error strings, URLs, `dac.*/vN` schema strings, env var
names) each with exactly **one** `expected_path`. **Promise:** this is the class where
lexical hashing legitimately wins and it is a real ARAIL usage pattern. Scored on rank-1
only. Same lint, same pre-registration rule.

### H1 — Harness: `scripts/eval/retrieval_ab.py`

```
usage: retrieval_ab.py [--lab-root PATH] [--world SLUG ...] [--arm hash|nomic|both]
                       [--workdir PATH] [--dump-corpus] [--json OUT] [--md OUT]
```

**Promises:**
- Reads corpora **read-only** through `pkb._iter_pkb_files` + `pkb._build_docs_rows`, so
  the embedding input string is byte-identical to production (`pkb.py:524`).
- Builds each arm's index in `--workdir` (default `$ARAIL_EVAL_WORKDIR`, default
  `lab/.eval-cache/`, git-ignored). **Asserts at startup that the resolved workdir is not
  under any `*/pkb/.cache/lancedb` or `*/.wiki-cache/` path and exits 2 if it is.**
- Emits `results.json` (schema `arail.retrieval_ab/v1`) and a RESULTS markdown block
  containing: per-world and pooled recall@5, MRR@10, per-query win/loss diff,
  overlap-stratum breakdown, exact-token rank-1 table, embed throughput (rows/s, p50/p95
  per-batch latency, total wall-clock per world), corpus manifest sha256, fixture
  sha256s, model name + dim, LanceDB version, git sha.
- **Definitions, fixed here so they cannot be chosen after seeing the data:**
  `recall@5` = fraction of queries with ≥1 labelled-relevant path in the top 5 hits.
  `pooled` = micro-average over **all** queries (not mean of per-world means).
  `Δ` = nomic recall@5 − hash recall@5, in percentage points.
  `rank-1 loss` = an exact-token query whose `expected_path` is rank 1 under hash and not
  rank 1 under nomic. Ties broken by ascending path — deterministic.
  Search uses `k=5` for recall@5 and `k=10` for MRR, `min_score=0.0`, no `where` clause,
  `approved` gate off (raw corpus — the gate is a separate concern and would shrink the
  sample).
- Prints a **verdict line**: `PASS` / `FAIL` / `PASS_INCONCLUSIVE` (see below), plus the
  paired-bootstrap 95% CI on Δ (10,000 resamples, seed committed).

**Requires:** for `--arm nomic`, a reachable Ollama with the spec's embedding model.
**On bad input:** an `EmbeddingError` aborts the run with the provider's own message and
writes **no** partial results file. A missing/unreadable fixture exits 2. A world with
zero rows is reported and excluded from pooled, not silently skipped.

**Never:** touches the live `.cache/lancedb`, mutates any PKB file, calls `index_all`,
imports `pkb_index`, or reads the network other than loopback Ollama.

### H2 — Corpus manifest: `eval/retrieval/corpus_manifest.json`

Per row: `world`, `path`, `name`, `source_kind`, `bytes`, `sha256` of the exact embedding
input string. **No document text.** Purpose: a reviewer on another machine can run
`--verify-manifest` and be told precisely which rows differ from the corpus the published
numbers were measured on, without the repo carrying anyone's notes.

### C1 — Error contract (conditional integration; designed now, per SPRINT concern 1)

The single rule: **an embedding outage must be impossible to mistake for an empty
corpus.** Three classes, and every call site must place itself in exactly one:

| Class | Examples | Behaviour |
|---|---|---|
| **LOUD — must reach the user** | `EmbeddingUnavailable` (Ollama down, model not pulled), `DimensionMismatch`, non-loopback base in airgapped mode | Raise out of `index_all`; at the `pkb_index` boundaries log at **ERROR**, emit `activity_log` severity `error`, set the module degraded flag, surface in the search API payload and the `/knowledge` banner, and make `./arailctl doctor` exit 3 |
| **DEGRADE — honest, usable** | LanceDB not importable; index absent because it has not been built yet | Return empty vector results, fall back to regex, but tag the response `degraded` with the reason and the exact command to fix it. Never present keyword results as semantic |
| **SKIP — per-item, counted** | one unreadable file (`OSError` in `_build_row`), `docs_registry.all_docs()` raising (`pkb.py:455-462`) | Existing warning behaviour is fine; the count appears in the `index_all` return dict |

Concrete required changes (all conditional on the gate):

- `pkb.index_all` (`pkb.py:488-540`) — **compute every vector before any write.** The
  current shape (build `rows`, then `idx.replace(all_rows)`) already does this; it must
  be preserved and pinned by test, because `VectorIndex.replace` is a
  `mode="overwrite"` drop (`vector_index.py:162`). Partial embedding must never reach
  `replace()`. `EmbeddingError` propagates; the return dict gains `"error"` and
  `"skipped"`.
- `pkb_index.py:178`, `:326`, `:347`, `:403` — the four `except Exception: _log.warning`
  wrappers around `index_all` must catch `EmbeddingError` **separately** first:
  `_log.error`, `activity_log.emit(..., "error")`, and
  `pkb_index.set_degraded(reason)`. Non-embedding exceptions keep today's warning.
  New public `pkb_index.embedding_status() -> tuple[bool, str]`.
- `pkb_index._flush` (`:223`) — an `EmbeddingError` on one path must **not** be recorded
  as a per-path failure and retried in a tight loop. It aborts the flush, keeps
  `_pending` intact for the next arm, sets degraded, and backs the debounce off to 60 s
  until a probe succeeds. (Otherwise a dead Ollama becomes a retry storm.)
- `pkb._semantic_search` (`pkb.py:563-617`) — `EmbeddingError` from `embed_query` must
  not become `[]`. It sets the degraded status and returns `[]` so `search()` still runs
  the regex sweep; `search()` returns results whose `source` is `"keyword"` and the
  caller can read `pkb.retrieval_status()`.
- `search_for_agents` (`pkb.py:673`) — same status is available to the agent context
  builder. An agent must not be handed keyword-only results while the UI claims semantic
  retrieval; the Buddy context header says so. (Wiring the header text is in the
  conditional scope; the contract is fixed here.)

### C2 — Explicit, resumable re-embed (conditional; per SPRINT concern 2)

New verb `./arailctl pkb reembed [--world <slug>|--root|--all] [--resume] [--dry-run]
[--yes]`, dispatched in `arailctl` alongside `ingest|compile|browse|prune`
(`arailctl:1030-1047`). **Not** under `db` — this touches per-instance LanceDB, and the
consolidated store is explicitly not being cut over.

- **Explicit.** Nothing else may trigger a network re-embed. `pkb._semantic_search`'s
  lazy `if idx.count() == 0: index_all(root)` (`pkb.py:580-583`) is **removed** on the
  integration branch and replaced with a degraded status naming the command. Under the
  no-ship branch the lazy path is offline and cheap and **stays exactly as it is**.
- **No silent drop on dimension change.** `pkb_index._schema_ok` /
  `ensure_ready` (`pkb_index.py:117-136`, `:329-348`) currently drop the table and
  rebuild on a vector-dim mismatch — which a 128→768 change triggers on **every**
  existing lab. New behaviour: distinguish *missing columns* (keep drop-and-rebuild;
  it is cheap and offline-safe only when the embedder is available — otherwise degrade)
  from *dimension mismatch* (**never drop**: set degraded status "index was built with
  128-dim hash vectors; run `./arailctl pkb reembed` to upgrade", leave the rows in
  place). Deleting a user's index because a config changed is not a recovery.
- **Resumable + interruptible.** Checkpoint at
  `<pkb_root>/.cache/reembed-state.json`: `{schema, model, dim, spec_sha256, started_at,
  total, completed_paths[], batch}`. Written after each batch (default 32,
  `ARAIL_EMBED_BATCH`, `embed.py:39`). SIGINT installs a handler that flushes the
  checkpoint and exits 130 with "resume with `--resume`". `--resume` refuses to continue
  if `model`/`dim`/`spec_sha256` differ from the current spec — start over instead of
  mixing spaces.
- **Shadow build + swap.** Rows are written to `<pkb_root>/.cache/lancedb.next/`. Only
  after all rows succeed: `pkb_pages.lance` → `pkb_pages.lance.bak-<ts>`, then
  `.next/pkb_pages.lance` → live (`os.replace` on the same filesystem), then the
  provenance sidecar. A crash between steps leaves either the old index or the old index
  plus a `.next` dir — never a half-embedded live table. `--dry-run` prints row counts
  and an ETA from a 32-row timing probe, and writes nothing.
- **Progress.** One line per batch to stdout (`rows/total, rows/s, ETA`), and an
  `activity_log` event at start/finish. VISION.md's 5-second first-embed threshold for
  the 381-row world is measured by the harness first; this verb exists precisely because
  we expect a several-second operation and refuse to hide it inside a search request.

### C3 — Airgapped guard (**ships regardless of the gate** — W0)

`embed.py:ollama_root()` returns whatever `MODEL_API_BASE` says. New: `_assert_local(base)`
called by `_post`. When `LAB_MODE` is not `hybrid` (i.e. the default `airgapped`), a base
whose host is not `127.0.0.1`, `::1`, or `localhost` raises `EmbeddingError` naming the
offending value and the env var. In `hybrid`, allowed, logged once at INFO. This closes a
corpus-text egress path that exists today in a module the harness is about to start
calling in bulk, and it is ~15 lines with a pure-unit test. Confirmed for the record: the
provider is loopback Ollama over `urllib` to `/api/embed`; it makes no other network call.

### C4 — Provenance, so hash vectors can never masquerade as nomic (conditional)

The 2.0 `content_refs` table (`dbspec/repo.py:380-470`, `doctor.py:127`) records
`embedding_model`/`embedding_dim` per row — but the cutover to that store is rejected, so
it is not available to the 1.x per-instance tables. Therefore: `index_all` and
`pkb reembed` write
`<pkb_root>/.cache/lancedb/pkb_pages.provenance.json` =
`{schema: "arail.vector_provenance/v1", embedding_model, embedding_dim, spec_sha256,
rows, written_at}`, **last**, after the data write. Read by `pkb_index.ensure_ready` and
by `./arailctl doctor`:

- sidecar missing but table present → treated as **hash/128 legacy** → degraded, "run
  `pkb reembed`".
- sidecar model ≠ `embedding_model().name`, or dim ≠ `EMBEDDING_DIM` → degraded, doctor
  exits 3. **No query is served from a table whose provenance disagrees with the spec.**
- `wiki_nodes` / `agent_workflows` / `experiments` keep hash vectors and get their own
  sidecar declaring `hash-sha1-128`, so "two vector spaces in one lab" is recorded fact
  rather than a discovery (accepted debt, see below).

### C5 — Clean-machine setup (conditional)

`scripts/setup.sh` gains `ollama pull nomic-embed-text` (~274 MB) in the model ladder,
following the `llama3.2:1b` pattern at `setup.sh:913-925`: **warn and continue** on
failure, never fail setup, print the exact manual command. `./arailctl doctor` calls
`embed.probe()` (`embed.py:106`) and reports reachable/dimension or the actionable
message. First PKB ingest on a machine without the model fails loudly with `EmbeddingUnavailable`'s
message, writes **zero** vectors, and leaves any existing index untouched (C1 + C2).

### C6 — Both embedders side by side, without a production fallback (per SPRINT concern 5)

The A/B is confined to the harness, which imports both `vector_index.hash_embedding` and
`dbspec.embed.embed_documents` **directly** and owns its own scratch indexes.
**Production code gets no embedder-selection switch.** Specifically forbidden: any
`ARAIL_EMBEDDING=hash|nomic` env var, any `try nomic except: hash` branch, any
`dim` parameter threaded from config into `index_all`. Reproducibility later = re-run
`scripts/eval/retrieval_ab.py`, which is committed and self-contained.
`hash_embedding` survives in the tree because three other tables still use it (A5), so no
preservation hack is needed.

---

## Failure modes

Every row has a test in the strategy below.

| # | Failure | Detection | Recovery |
|---|---|---|---|
| FM1 | Fixture labelled to favour nomic (question paraphrases a doc the labeller cherry-picked after seeing results) | Pre-registration commit order (F1.3) + overlap-stratum floor (F1.4) + reviewer re-runs the lint | Results void; regenerate after re-labelling in a separate commit with written reason |
| FM2 | Evidence quote does not exist in the labelled document | `test_retrieval_fixture.py` re-reads each file and asserts verbatim presence | Build fails; fix the label |
| FM3 | Fixture edited after results published | `git log --follow` on the fixture vs the results commit; BUILD_LOG must record both shas | REVIEW blocks; results regenerated |
| FM4 | Personal data committed in an evidence excerpt | PII lint (F1.5) + human sign-off line in BUILD_LOG | Excerpt replaced before merge; if already pushed, treat as a secret-leak incident |
| FM5 | Harness writes into a live `.cache/lancedb` and corrupts a real index | Startup workdir assertion (H1); test asserts exit 2 for a workdir under a PKB cache | Exit before any write. Recovery from an actual corruption is `pkb reembed` / `index_all` |
| FM6 | Arms differ in something other than the embedder | Both arms built from one shared `rows` list in one pass; test asserts the two arms' non-vector fields are identical row-for-row | Harness raises; results not written |
| FM7 | Ollama dies mid-measurement → partial nomic index scored as a poor result | `EmbeddingError` aborts and writes **no** results file (H1) | Re-run; `results.json` never exists in a partial state |
| FM8 | Corpus changed between the labelling and the run | `corpus_manifest.json` sha comparison; `--verify-manifest` reports differing rows | Re-label affected queries or re-snapshot; recorded in RESULTS.md |
| FM9 | 15pp cleared by a 30-query fixture that is really a 5-query coin flip | Paired-bootstrap 95% CI on Δ printed with the verdict; `PASS_INCONCLUSIVE` when the point estimate clears but the CI lower bound ≤ 0 | Operator sign-off required before integration lands (see "underspecified") |
| FM10 | *(integration)* Embedding outage yields a silently empty index — the exact 1.x failure mode `embed.py` exists to prevent | C1: `EmbeddingError` never swallowed at `pkb_index.py:178/326/347/403`; degraded flag → search payload → banner → `doctor` exit 3 | No write occurs; existing index untouched; user is told the `ollama pull` |
| FM11 | *(integration)* A user typing in the search box triggers hundreds of synchronous Ollama round trips | Lazy `index_all` at `pkb.py:580-583` removed on the integration branch; test asserts `_semantic_search` on an empty index performs **zero** embed calls | Degraded status naming `./arailctl pkb reembed` |
| FM12 | *(integration)* 128→768 dim change drops every existing lab's index | `_schema_ok` distinguishes dim mismatch from missing columns; test asserts the table is **not** dropped on a dim mismatch | Rows preserved; degraded status; explicit `pkb reembed` |
| FM13 | *(integration)* `pkb reembed` interrupted → half-embedded live index | Shadow build + swap (C2); checkpoint; test kills mid-run and asserts the live table is unchanged and `--resume` completes | `--resume`, or `.bak-<ts>` restore |
| FM14 | *(integration)* Hash vectors silently written into an index that claims nomic | Provenance sidecar (C4) + `doctor` exit 3 on disagreement; test writes a mismatched sidecar and asserts degrade | No query served from a disagreeing table |
| FM15 | *(integration)* Clean machine without the model: setup breaks, or ingest silently writes nothing | C5; test with `MODEL_API_BASE` pointed at a closed port asserts zero rows written, existing index intact, message contains `ollama pull nomic-embed-text` | Warn-and-continue setup; actionable ingest failure |
| FM16 | Airgapped egress via `MODEL_API_BASE` pointed off-box | C3 guard; unit test with `MODEL_API_BASE=http://evil.example` + default `LAB_MODE` asserts raise before any socket | Raise, name the env var |
| FM17 | Dead Ollama turns the debounce timer into a retry storm | `_flush` backs off to 60 s on `EmbeddingError` (C1); test asserts a single failure does not re-arm at 2 s | Back-off until `probe()` succeeds |
| FM18 | CI has no Ollama and the whole suite turns red | `@pytest.mark.requires_ollama`, skipped when `embed.probe()` fails | Suite green without Ollama; live tests run locally |

---

## Test strategy

Runner: `PYTHONPATH=src /Users/netsushi/ProJects/qukaizen-arail/.venv/bin/python -m pytest`.
Product gating (arail): 30% setup / 30% Buddy / 20% security / 10% happy / 10% regression —
here that maps to setup+clean-machine (C5/FM15), retrieval quality as the Buddy proxy
(the whole measurement), security (C3/FM16, FM4, FM5), and regression against the
`8cb5760` baseline.

**Unit — always ships**
- `tests/eval/test_retrieval_fixture.py`: schema validity; ≥30 NL queries; ≥6 per world
  across 5 worlds; ≥8 exact-token queries with exactly one `expected_path`; unique ids;
  every `relevant.path` present in the corpus manifest; every `evidence` verbatim in its
  file (FM2); PII lint (FM4); overlap-stratum floor ≥25%/≥25% (FM1).
- `tests/eval/test_retrieval_ab.py`: scoring functions on a synthetic 12-row corpus with
  a stub embedder — `recall@5`, `MRR@10`, pooled micro-average vs mean-of-means (assert
  the harness uses micro), deterministic tie-break, paired-bootstrap CI reproducible from
  the committed seed. **No Ollama.**
- Workdir guard: a `--workdir` under `*/pkb/.cache/lancedb` exits 2 before any write (FM5).
- Arm parity: both arms share one row list; non-vector fields identical (FM6).
- `tests/dbspec/test_embed_airgapped.py`: non-loopback `MODEL_API_BASE` raises under
  default `LAB_MODE`; allowed under `hybrid`; loopback forms accepted (FM16). Asserts the
  raise happens before `urlopen` is reached (monkeypatched to explode).

**Integration — always ships**
- `@pytest.mark.requires_ollama` end-to-end: 12-row temp PKB → both arms → `results.json`
  validates against `arail.retrieval_ab/v1` → verdict line present (FM18 skip path).
- `--dump-corpus` on a temp PKB produces one line per row and mutates nothing (file
  mtimes + a directory sha compared before/after).
- `EmbeddingError` mid-run writes no `results.json` (FM7).

**Integration — conditional branch only (gate passes)**
- Empty index + `_semantic_search` performs zero embed calls (counting stub) (FM11).
- 128-dim table + 768-dim spec: `ensure_ready` does **not** drop; degraded status set;
  rows still present (FM12).
- `index_all` with an embedder that raises on the 5th batch: zero rows written,
  pre-existing table byte-identical, `EmbeddingError` propagates, ERROR logged,
  `activity_log` severity `error` emitted (FM10).
- `pkb reembed` SIGINT at ~50%: live table unchanged, checkpoint written, `--resume`
  completes to the full row count, provenance sidecar written last (FM13).
- Mismatched provenance sidecar → degraded + `doctor` exit 3 (FM14).
- Closed-port `MODEL_API_BASE`: ingest writes zero vectors, existing index intact,
  message contains `ollama pull nomic-embed-text` (FM15).
- `_flush` back-off after one `EmbeddingError` (FM17).

**Regression**
- Full suite vs the `8cb5760` baseline: **28 failures over 21 files**. Any 29th failure,
  or any newly-failing file, is this sprint's regression. The builder does not fix the 28.
- No-ship branch specifically: `pkb.py`, `vector_index.py`, `pkb_index.py`,
  `wiki_vectors.py`, `world_mount.py`, `scripts/start.sh` must be **byte-identical** to
  baseline. `git diff --stat 8cb5760 -- <those files>` is empty. This is the strongest
  regression guarantee available and it is checked in REVIEW.

**Performance**
- Harness reports embed throughput per world (rows/s, per-batch p50/p95, total wall-clock).
  VISION.md's disconfirming threshold: >5 s first-embed for the 381-row `ai` world on a
  warm Ollama. Recorded either way; it is an input to the re-embed UX, not a gate on the
  recall number.
- Both arms' query latency at k=10 over 716 rows (flat scan; no ANN index exists —
  PHASE1_AUDIT §3.1), so we know whether 768 dims measurably slows search.

**Security**
- C3 airgapped guard (above).
- PII lint on committed excerpts (FM4).
- The harness accepts no `where` clause and interpolates no user string into a LanceDB
  predicate. (`pkb_index.py:196` escapes quotes for `delete`; the harness must not add a
  second, weaker escaping site.)
- No secret, key, or `lab/data/secrets.env` content is read or logged by anything here.

---

## Tech debt assessment

**Added**
- `eval/retrieval/` fixtures encode *this* corpus. They rot as the lab changes; the
  corpus manifest makes the rot detectable (`--verify-manifest`) rather than silent.
  Owner action: re-snapshot when a world's row count moves >20%.
- One more evaluation surface (`scripts/eval/`) with its own scoring code that is not
  reused by production.
- *(conditional)* A provenance sidecar is a second-best `content_refs` — a JSON file next
  to the data, not a transactional record. It exists because the cutover was rejected;
  it should be retired when/if the consolidated store lands. **Filed as a follow-up in
  `sprints/BACKLOG.md` at build time.**
- *(conditional)* Two vector spaces in one lab (`pkb_pages` nomic, `wiki_nodes` /
  `agent_workflows` / `experiments` hash). Safe because the tables are physically
  separate, and now *recorded* by C4 — but it is a real inconsistency and a future
  sprint's work. **Also filed.**
- *(conditional)* A 274 MB model becomes a soft requirement of the clean-machine path.

**Repaid**
- The repo gains a retrieval evaluation harness it has never had — the thing that lets
  any future retrieval claim be checked instead of asserted.
- `hash_embedding` stops being an accident and becomes either a measured, ADR-recorded
  choice or a measured, retired one. Either outcome removes a standing unknown.
- C3 closes a real airgapped-egress hole in `embed.py` that exists today.
- *(conditional)* C1 converts four silent `_log.warning` swallow points around
  `index_all` into a user-visible status — a truth-in-UI repair of the same family as
  the 2026-07-23 clean-experience sprint, and worth having independent of embeddings.

**Net:** negative (debt repaid) on the no-ship branch — a harness and an ADR for one
egress fix and no production change. Roughly neutral on the ship branch: real new
operational surface (sidecar, reembed verb, model dependency) bought with a real repair
of the silent-failure plumbing.

---

## Rollback plan

**No-ship branch (gate fails).** Nothing in `src/arail/` changes except C3's ~15-line
airgapped guard in `dbspec/embed.py`, which is dead code in production today (no caller).
Rollback = revert one commit. The fixture, harness, RESULTS.md and ADR stay; they are
inert.

**Ship branch (gate passes).** Rollback has two layers:

1. **Code.** The integration is a contiguous set of commits behind no feature flag (C6
   forbids a selection switch). Rollback = `git revert` that range. The reverted code
   reads 128-dim hash vectors again.
2. **Data.** Each re-embedded lab has `<pkb_root>/.cache/lancedb/pkb_pages.lance.bak-<ts>`
   — the pre-re-embed 128-dim table. Restore = stop the lab, move the `.bak-<ts>` dir
   back over `pkb_pages.lance`, delete the provenance sidecar, restart. Documented in the
   `pkb reembed` help text and in BUILD_LOG. `.bak-<ts>` dirs are **not** auto-pruned by
   this sprint; `db optimize` may claim them later.
   Worst case with no backup: `pkb_pages` is a **derived** index over files that are
   still on disk. `./arailctl pkb reembed` (or the reverted `index_all`) rebuilds it. No
   user content is at risk in either direction — that is why this is a rollback-safe
   change and the store cutover was not.

**Abort mid-sprint.** If A2 fails (we cannot honestly label 30 questions), the sprint
stops at the fixture and RESULTS.md records that as the finding, per VISION.md's third
disconfirming criterion. That is a legitimate ship.

---

## What the builder must NOT touch

Hard boundaries. Violating any of these is an automatic BLOCK in review.

1. **`src/arail/world_mount.py`, `scripts/start.sh`, `scripts/lib/instances.sh`,
   `src/arail/portal/app.py:3529`** — Tier 1.1 and the A25/A26 unmount defect are out of
   scope this sprint. Not "small fix while I'm here."
2. **`pkb.search` / `search_for_agents` signatures** — no `world_id` parameter, no
   `where` clause, no scoping change of any kind.
3. **`src/arail/dbspec/migrate.py`, `reconcile.py`, `repo.py`, `spec.py`, and the
   consolidated 2.0 store** — the cutover is rejected. Do not point anything at it.
4. **`src/arail/dbspec/generated/*`** — generated files. If the spec must change, change
   `spec/models/models.hcl` and run `./arailctl db apply`. It should not need to change.
5. **The live `lab/` tree** — the harness is read-only against it. No `index_all`, no
   ingest, no reembed against the operator's real labs without an explicit operator ask.
   Scratch goes in `lab/.eval-cache/` (add to `.gitignore`).
6. **`pkb.py`, `vector_index.py`, `pkb_index.py`, `wiki_vectors.py`** — **untouched
   unless and until the gate passes**, and then only as specified in C1/C2/C4. VISION.md
   is explicit: "Nothing under `src/arail/pkb.py`, `vector_index.py`, `world_mount.py`,
   or `scripts/start.sh` is touched until the number exists."
7. **The 28 baseline test failures across 21 files at `8cb5760`** — do not fix them, do
   not delete them, do not mark them skip. They are the comparison baseline. Fixing one
   makes the regression check ambiguous.
8. **Machine-surface names** — `dac.*/vN` schema strings, module paths, env vars, CLI
   verb names are frozen (workspace `CLAUDE.md`).
9. **Any production embedder-selection flag** (C6). No `ARAIL_EMBEDDING`, no silent
   fallback, no `dim=` threaded through config.
10. **Chat memory / `docs/adr/0002` boundary** — `lab/pkb/conversations/` stays excluded
    from indexing (`pkb.py:405`). Do not add it to the fixture corpus.

---

## Recommended implementation order

1. **W0 — C3 airgapped guard** in `dbspec/embed.py` + unit test. Small, independent,
   ships either way, and it must land before the harness starts making bulk embed calls.
2. **W1 — corpus manifest + `--dump-corpus`.** Read-only harness skeleton, workdir guard,
   manifest emitter. Commit. This is what the labeller reads from.
3. **W2 — the fixtures, hand-authored, plus the lint.** Own commit(s), **before any
   result exists**. `queries.yaml` (≥30), `exact_tokens.yaml` (≥8),
   `tests/eval/test_retrieval_fixture.py`. Record the sha in BUILD_LOG. If A2 fails here,
   stop and write the finding.
4. **W3 — scoring + both arms.** `retrieval_ab.py` complete: hash arm, nomic arm, recall@5
   / MRR / rank-1 / strata / bootstrap CI / latency. Unit tests with a stub embedder
   (no Ollama). Commit.
5. **W4 — run it, publish.** `results.json` + `RESULTS.md` appended to this sprint dir,
   with corpus/fixture shas, model, LanceDB version, git sha, and the verdict line.
   Commit. **This is the sprint's deliverable.**
6. **W5 — the fork.**
   - **FAIL (Δ < 15pp, or ≥1 rank-1 loss):** write `docs/adr/0004-hash-embeddings-are-a-measured-choice.md`
     citing the numbers and VISION.md's "do not reopen without a corpus an order of
     magnitude larger / revisit 2026-11-01". Stop. `pkb.py` et al. remain byte-identical
     to `8cb5760`.
   - **PASS_INCONCLUSIVE (Δ ≥ 15pp but CI lower bound ≤ 0):** publish, stop, and put the
     decision to the operator. Do not integrate on the architect's authority.
   - **PASS:** proceed to W6.
7. **W6 (conditional) — error contract first.** C1 in `pkb_index.py` and `pkb.py`:
   degraded status, ERROR logging, activity events, search-payload status, back-off.
   Tests FM10/FM11/FM17. This lands **before** any embedder swap, so the loudness exists
   the moment there is something to be loud about.
8. **W7 (conditional) — provenance sidecar (C4)** + `doctor` reporting. Tests FM14.
9. **W8 (conditional) — `./arailctl pkb reembed` (C2)** with checkpoint, shadow build,
   swap, `--dry-run`, progress. Tests FM13, FM12.
10. **W9 (conditional) — the swap itself**: `embed_documents` at `pkb.py:524`,
    `pkb.py:481` (`_build_docs_rows`), `pkb_index.py:82` (`_build_row`); `embed_query` in
    `_semantic_search`; `_VECTOR_DIM` from `EMBEDDING_DIM`. Remove the lazy `index_all`.
    Tests FM11, FM15.
11. **W10 (conditional) — `scripts/setup.sh` model pull (C5)** + doctor probe + README /
    `docs/` truth-in-UI updates + backlog entries for the two conditional debts.

Phases 7–11 are one reviewable chunk each; the review-mode pass at the end of W5 is the
one that decides whether 7–11 happen at all.
