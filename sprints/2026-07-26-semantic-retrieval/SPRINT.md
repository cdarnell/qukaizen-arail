# SPRINT — 2026-07-26-semantic-retrieval

> **STATUS: PARTIALLY SUPERSEDED (banner added 2026-08-17).** This plan was
> written 2026-07-26 and never run as its own sprint. Roughly half of it
> shipped anyway, through the ARAIL 2.0 persistence / `dbspec` line rather
> than through the work packages below — so the Ledger at the bottom has been
> reconciled against the tree instead of being left reading all-TODO.
>
> **Landed:** the eval harness (WP0) and the embedding provider (WP1). The
> owner decisions held: backend is Ollama, model is `nomic-embed-text` at 768
> dims (`src/arail/dbspec/embed.py`, with the asymmetric document/query
> prefixes that model needs), `hash_embedding` survives as the failsoft
> fallback, and the change was measured before it landed —
> `eval/retrieval/results.json` records **+40.6 pp** over hash
> (95% CI 25.0–56.25, 10k resamples) with exact-token rank-1 going
> **1/10 → 9/10**. Decision 4 ("measure first") was honored.
>
> **Still open:** WP0.5 quick wins (unverified), WP2 chunking, WP3 hybrid +
> RRF (no BM25 or reciprocal-rank fusion exists in `src/`), WP5 turbovec, and
> the sprint's learning deliverable — the ADR. Note the number in decision 5
> is stale: `0004` was taken twice by the dac_world work, so the retrieval ADR
> should be written as **0006** (and the existing 0004 collision is worth
> fixing separately).
>
> Kept rather than deleted because the five owner decisions below are the
> reasoning behind what shipped, and the ADR is still an open learning goal.

**Product:** arail
**Scope:** Upgrade the PKB retrieval layer from 128-dim hash embeddings (one
vector per file, first 4KB only) to real semantic retrieval: an Ollama-backed
embedding provider behind the existing `hash_embedding` interface, document
chunking, and hybrid dense+BM25 search. **This is a learning sprint** — each
work package pairs a build deliverable with a concept Charlie should be able
to explain afterwards. Resource cost is accepted; understanding is a first-class
deliverable.

## Owner decisions (2026-07-26, Charlie — binding)

1. **Backend: Ollama.** The embedding API (`/api/embed`) is identical on macOS
   (Metal) and Linux (CUDA), so the retrieval layer stays portable across every
   platform ARAIL targets. No torch, no sentence-transformers.
2. **Model: `nomic-embed-text`** (~270 MB, 768-dim) as the default. Pulled once
   at setup like `llama3.2:1b`; zero runtime egress after that. Airgap mode
   untouched — embedding calls go to localhost Ollama only.
3. **`hash_embedding` survives** as the failsoft fallback and the deterministic
   test path. If Ollama is down or the model is missing, retrieval degrades to
   hash — never breaks. This mirrors the existing `available()` failsoft pattern.
4. **Measure first.** No retrieval change lands without the eval harness (WP0)
   showing before/after numbers. If hash turns out good enough on real queries,
   that is a legitimate finding, not a failure.
5. **Learning artifact:** the sprint closes with Charlie writing **ADR 0004 —
   semantic retrieval and the embedding-model boundary**, in his own words,
   reviewed against the eval numbers.

## Concepts ledger (what this sprint teaches)

| WP  | Concept to own by the end |
|-----|---------------------------|
| WP0 | Retrieval evaluation: recall@k, MRR, why you need a golden set before touching anything |
| WP0.5 | Retrieval as token budgeting: snippets vs full files vs chunks as context-injection units; why one oversized hit can crowd out four good ones |
| WP1 | What an embedding actually is; cosine vs L2 on normalized vectors; why model version pins the index |
| WP2 | Tokenization; chunk size/overlap trade-offs; why passage granularity beats file granularity |
| WP3 | BM25 term weighting; why lexical and dense search fail differently; Reciprocal Rank Fusion |
| WP4 | Reading your own eval honestly; writing an ADR that a future you can trust |

## Work packages

### WP0 — Eval harness (build the ruler before the thing you measure)

- `experiments/retrieval-eval/queries.jsonl` — 15–20 real queries Charlie would
  actually ask the KB, each with the file(s)/passage(s) that *should* come back
  (hand-labelled golden set).
- `experiments/retrieval-eval/run_eval.py` — runs a query set against the live
  index, reports recall@1/5/10 and MRR per config.
- Record the **hash-embedding baseline** numbers into
  `experiments/retrieval-eval/results/baseline-hash.json`.
- **Gate:** baseline numbers committed before WP1 starts.
- **Learning checkpoint:** explain why recall@5 (not @1) is the metric that
  matters for agent context injection.

### WP0.5 — Quick wins on the existing system (no new dependencies)

Ship value from the current hash+LanceDB stack before any embedding model
enters the picture — and learn how far the simple system stretches.
Grounding facts (measured 2026-07-26): 751 PKB files, median 833 bytes,
94% under 4KB, largest 48KB. Agents currently consume only ~300-char
snippets from the top 5 hits; nothing reads the full file after retrieval.

- **Full-file injection with a token budget.** After ranking, retrieval
  consumers (Researcher context assembly, chat RAG in `lab_brain.py`) inject
  whole file contents — not just snippets — walking hits in rank order until a
  budget cap (`ARAIL_RAG_CONTEXT_TOKENS`, default ~3000 tokens ≈ 12KB) is
  spent. Oversized files are truncated to the remaining budget, never allowed
  to evict every other hit. Token estimate: `len(text) / 4` — a real tokenizer
  is deliberately out of scope here.
- **Fix regex shadowing.** Today the full-text regex fallback in
  `pkb.search()` runs only when the semantic path returns *zero* hits, so one
  mediocre fuzzy match can hide a perfect exact match buried deep in a file.
  Change: run both paths and merge (exact regex hits ranked above weak
  semantic hits), or trigger the regex sweep whenever the top semantic score
  is below a floor. Decide which while implementing; record why in BUILD_LOG.
- Add 2–3 golden-set queries that specifically exercise both fixes (an answer
  living mid-file past the snippet window; an exact token shadowed by a fuzzy
  match) so WP0's eval captures the before/after.
- **Gate:** eval shows improvement on those queries; median-case context cost
  stays under the budget cap; no regression elsewhere.
- **Learning checkpoint:** state today's per-query context cost in tokens
  (snippets vs full-file-with-cap) and explain the fat-tail problem the cap
  solves — this is the intuition chunking later formalizes.

### WP1 — Embedding provider abstraction

- New `src/arail/embeddings.py`: `EmbeddingProvider` protocol with two
  implementations — `HashProvider` (wraps existing `hash_embedding`) and
  `OllamaProvider` (POST `http://127.0.0.1:11434/api/embed`, model from
  `ARAIL_EMBED_MODEL`, default `nomic-embed-text`).
- Provider selection: `ARAIL_EMBED_BACKEND` env (`ollama` | `hash`), default
  `ollama` with automatic fallback to hash when Ollama is unreachable
  (log once, never raise — same contract as `vector_index.available()`).
- `vector_index.py` and `pkb_index.py` take vectors from the provider; table
  schema gains `embed_model` metadata and the dim check in `_schema_ok`
  becomes provider-aware (768 for nomic, 128 for hash). Model or dim mismatch
  → drop and rebuild, exactly like the existing schema-upgrade path.
- `scripts/setup.sh` pulls `nomic-embed-text` alongside `llama3.2:1b`.
- Tests keep using `HashProvider` (deterministic); one integration test is
  skipped unless Ollama is up.
- **Gate:** `index_all` completes with Ollama up AND with Ollama down (fallback).
- **Learning checkpoint:** explain why changing the embedding model requires a
  full re-embed, and where the code enforces that.

### WP2 — Chunking

- Chunker in `embeddings.py` (or `chunking.py`): ~800-token chunks, ~100-token
  overlap, split on paragraph boundaries where possible. Row schema becomes
  chunk-level: `path`, `chunk_id`, `chunk_index`, `text_snippet`, `vector`,
  `mtime`, `source_kind`.
- The 4KB truncation in `_build_row` is deleted — whole documents index.
- `schedule_upsert`/`_flush` delete-then-insert all chunks for a changed file
  (chunk counts change as files grow; merge_insert on `path` alone no longer
  suffices).
- Search results dedupe to best-chunk-per-file by default, with an option to
  return raw chunks (that's what agent context injection wants).
- **Gate:** a query answered only on page 30 of a long PKB doc now retrieves it;
  eval re-run shows no regression on short files.
- **Learning checkpoint:** explain the failure mode of chunks that are too
  small, and of chunks that are too large.

### WP3 — Hybrid search (dense + BM25, RRF fusion)

- Enable LanceDB native FTS index on the chunk text column.
- `VectorIndex.search` grows a `mode` parameter: `dense` | `fts` | `hybrid`
  (default `hybrid`), fusing the two rankings with Reciprocal Rank Fusion
  (k=60, no tuning knobs to start).
- Exact-identifier queries (`ARAIL_MODELS_DIR`, `aerollm-speculative`, error
  strings) added to the eval golden set — this is the regime dense retrieval
  loses and BM25 wins.
- **Gate:** hybrid ≥ max(dense, fts) on recall@5 across the golden set.
- **Learning checkpoint:** explain in one paragraph why RRF works despite
  ignoring the raw scores of both rankers.

### WP4 — Eval, verdict, ADR

- Re-run the full eval: hash baseline vs dense vs hybrid; results committed to
  `experiments/retrieval-eval/results/`.
- Charlie writes `docs/adr/0004-semantic-retrieval-embedding-boundary.md`:
  context, decision, consequences, the numbers, and explicitly what was given
  up (determinism, zero-ML-runtime purity, index/model coupling).
- Claude reviews the ADR draft as critic, not author.
- **Gate:** ADR merged; CLAUDE.md's conventions section updated to reflect the
  new retrieval contract.

### WP5 (stretch, optional) — turbovec spike

- Only if WP0–WP4 land and curiosity remains: benchmark turbovec `IdMapIndex`
  (4-bit) against LanceDB on the now-real 768-dim corpus using the WP0 harness.
  Connects this sprint to the scale-out story; no integration commitment.

## Constraints (unchanged from repo conventions)

- Airgap default holds: Ollama is localhost; the only network event is the
  one-time model pull at setup, same consent surface as the chat model.
- No auto-checks at boot: provider probe happens lazily on first embed call,
  not at portal start (`ARAIL_AUTOCHECKS` contract untouched).
- Failsoft everywhere: no code path may raise because Ollama is absent.
- Internal package name stays `arail`; env vars are the rebrand surface.

## Ledger

Reconciled against the tree 2026-08-17 — see the status banner at the top.
Statuses below describe what is actually in `src/`, not what this plan
intended.

| Phase | Status | Notes |
|---|---|---|
| plan (this doc) | DONE | Owner decisions 1–5 binding, and they held |
| WP0 — eval harness | **SHIPPED** | `scripts/eval/retrieval_ab.py`, `eval/retrieval/`, `tests/eval/` — A/B arms, bootstrap CI, exact-token probe |
| WP0.5 — quick wins (full-file + budget cap, shadowing fix) | **UNVERIFIED** | not confirmed either way in the 2026-08-17 pass |
| WP1 — embedding provider | **SHIPPED** | `nomic-embed-text` @ 768d in `src/arail/dbspec/embed.py`; `hash_embedding` retained as failsoft |
| WP2 — chunking | **OPEN** | no chunking in `src/`; still one vector per document |
| WP3 — hybrid + RRF | **OPEN** | no BM25 and no reciprocal-rank fusion in `src/` |
| WP4 — eval + ADR | **PARTIAL** | measurement done (+40.6 pp, CI 25.0–56.25); ADR never written — renumber to 0006 |
| WP5 — turbovec spike | STRETCH | not started |
