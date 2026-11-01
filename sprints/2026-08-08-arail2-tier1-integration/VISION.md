# Vision: ARAIL 2.0 Tier 1 integration — real embeddings, world-scoped retrieval, cutover

**Date:** 2026-08-08
**Product:** arail
**Wedge size:** one sprint

**Verdict: PROCEED, narrowed to one third of the proposal.**

- **Tier 1.2 (real embeddings) — PROCEED, but as a measurement, not a swap.** The
  decisive number has never been measured.
- **Tier 1.1 (world-scoped retrieval) — DEFER.** Its premise does not survive
  contact with the code: per-World isolation is already enforced by the
  filesystem. The WHERE clause is not an upgrade over that; it is the price of a
  consolidation nobody has justified.
- **Cutover to the consolidated 2.0 store — REJECT for this sprint.** It trades
  a filesystem isolation boundary for a query-predicate one, in a product whose
  gating says it runs on other people's machines, in exchange for a
  cross-world queryability the operator has explicitly said he does not want.

---

## The finding that reorders the sprint

`pkb._vector_db_path(root)` (`pkb.py:414`) derives the LanceDB path from the PKB
root, and each World instance runs as its own process with its own env-frozen
`LAB_PKB`. The census in PHASE1_AUDIT §2.2 confirms it on disk: five physically
separate `pkb_pages.lance` datasets, one per lab. **A running World cannot see
another World's rows today. There is no query path to leak through.**

The migration (`dbspec/migrate.py:225-275`) merges all five into one target
data dir and one PKB root, discriminated by a `world_id` column. So Tier 1.1
does not "retire the `rm -rf` scoping model" for the surface where the operator
actually spends his time. It is the **precondition for the consolidation** —
mandatory work created by the cutover, not value delivered by it.

That inverts the value case. Ask the question directly: what does merging five
isolated stores into one buy an operator who, per the recorded memory, runs
Worlds **one at a time and never concurrently**? Cross-world search is the only
candidate, and it is precisely the thing Tier 1.1 then spends effort suppressing.
Isolation goes from "a different directory" to "a string comparison in a
predicate that one forgetful call site can omit" — and the audit already
documents that exactly one such predicate exists in the codebase and that no
production caller has ever passed it (A31).

The real defect the audit found is narrower and survives: **A25/A26** —
`unmount(remove_staged=False)` is the default and the portal's `"default"`
branch (`app.py:3529`) calls it with no arguments, so an unmounted world's rows
stay searchable in the **root lab**; and `_sweep_other_worlds` (`world_mount.py:1407`)
scopes by `shutil.rmtree`, so a directory that fails to delete stays searchable
forever. That is a genuine bug on the root-lab mount path, and it is a
stop-and-switch bug — the exact path the operator memory says matters. It does
not require a database consolidation to fix. It requires changing a default and
checking a return value.

## The other correction

The margin cited as evidence for Tier 1.2 — **+0.053 cosine** — is
nomic-with-prefixes vs **nomic-without-prefixes**. It measures the *prefix*
decision. It says nothing about the decision actually on the table, which is
nomic vs `hash_embedding`. Nobody has run that comparison. The entire Tier 1.2
case currently rests on an argument from first principles ("hashed bag-of-words
is not semantic") plus a measurement of a different variable.

That argument from first principles is probably right. But "probably right" is
what a one-afternoon measurement is for, and we are proposing to make it a hard
runtime dependency of ingest on every machine a friend clones this onto.

---

## User

The operator (and the friend/family forker the blueprint is written for) sitting
in the **Chat** tab of a single running World — say `debt-finance`, 79 pkb rows
— asking Buddy a natural-language question whose answer is in a document that
shares few or no literal tokens with the question. Buddy's context comes from
`search_for_agents` → `pkb.search` → `_semantic_search`, which is a 128-dim SHA1
token-hash projection: lexical overlap wearing a semantic label. When the query
and the document say the same thing in different words, Buddy gets the wrong
context, or nothing, and answers ungrounded — with no signal to the user that
retrieval missed.

This user is not managing five Worlds at once. He starts one, works, stops it,
starts another.

## Problem

**The lab's retrieval is advertised as semantic and is lexical.** That is a
truth-in-UI defect of the same family the 2026-07-23 clean-experience sprint
was fought over. The user-visible cost is Buddy quality: the answer is
confidently wrong-sourced, and the failure is silent — `pkb.py:186-190` and
`vector_index.py:126-129` turn any retrieval error into `[]`, and the regex
fallback then returns something plausible-looking.

The problem is *not* cross-world contamination. Measurement corrected that twice:
`compiled/` is 0 rows in the live `pkb_pages` index, and off-domain material is
50 of 681 rows (7%) — 10 generic seed docs per world. An off-topic query
returning those seeds at distance >1.0 is a correctly-ranked miss, not
corruption. I am not funding a store consolidation on a 7% figure whose own
authors have now downgraded it twice.

## Win condition

A committed harness and a committed number, measured on **this repo's live
corpus** (`lab/` — 716 `pkb_pages` rows across root + 4 instances):

1. **A labelled query set exists:** ≥20 natural-language questions, ≥4 per
   world, each with ≥1 hand-labelled relevant document in that world's actual
   index. Committed as a fixture, not generated by an LLM from the documents it
   will be scored against.
2. **The decisive number is published:** recall@5 for `hash_embedding` vs
   nomic-embed-text-with-prefixes on that set, per world and pooled.
3. **The pre-committed bar: nomic must beat hash by ≥15 percentage points
   pooled recall@5.** Below that, we do not ship the dependency.
4. **The exact-token class does not regress:** a second fixture of ≥8 literal
   queries (file names, error strings, URLs, `dac.*/vN` schema strings) shows
   the post-change path still returns them at rank 1. This class is where
   lexical hashing legitimately wins, and it is a real ARAIL usage pattern.
5. **Clean-machine honesty, if and only if we ship the swap:** on a machine
   without `nomic-embed-text` pulled, PKB ingest fails with a message naming the
   exact `ollama pull` to run, writes **zero** vectors, and leaves any existing
   index untouched. Verified by test, not by inspection. And `./arailctl setup`
   on a machine with no network still completes with a warning, as the
   `llama3.2:1b` ladder already does (`setup.sh:913-925`).

"Search is better" is not on this list, deliberately. Item 3 is the whole sprint.

## Wedge

**Measure before you integrate. Nothing under `src/arail/pkb.py`,
`vector_index.py`, `world_mount.py`, or `scripts/start.sh` is touched until the
number exists.**

The wedge is one committed script plus one committed fixture:

- `scripts/eval/retrieval_ab.py` (or equivalent) reads a copy of the live corpus,
  embeds every row twice — once with `hash_embedding`, once with
  `arail.dbspec.embed` — runs the labelled query set against both, and prints
  recall@5, MRR, and the per-query diff.
- The labelled query set, hand-written, committed.
- A short results section appended to this sprint's ledger.

716 rows × 2 embeddings on a local Ollama is minutes of compute. It requires no
schema change, no migration, no cutover, and it runs on the developer's own
machine with no cloud account — the friction profile this product serves.

**Then, gated on the number clearing 15pp:** swap the provider at the two ingest
call sites (`pkb.py:519-527` via `vector_index`, `wiki_vectors.py:22`) for the
**per-instance stores as they exist today**. No consolidation. No `world_id`
column in the query path. Each World keeps its own directory and re-embeds its
own ≤381 rows. This is the version of Tier 1.2 that ships without Tier 1.1,
because with directory isolation intact, Tier 1.1 is not required for it —
contradicting INTEGRATION.md's "1.1 is only worth having once vectors are real,"
which had the dependency backwards.

If the number misses the bar, the sprint still shipped something durable: a
retrieval evaluation harness this repo has never had, and a defensible ADR
recording `hash_embedding` as a *measured* choice rather than an accident.

## Disconfirming evidence

Pre-committed, in order of what each kills:

- **Pooled recall@5 gain < 15 percentage points** → do not ship the swap. Close
  Tier 1.2 permanently and write `docs/adr/` recording hash embeddings as a
  measured decision. Do not reopen without a new corpus an order of magnitude
  larger.
- **Nomic loses ≥1 of the 8 exact-token queries from rank 1 and the regex
  fallback does not recover it** → do not ship, regardless of the recall number.
  Losing "find me that error string" to gain fuzzy recall is a bad trade for
  this user.
- **We cannot honestly label 20 questions against this corpus** — because the
  documents are too few, too generic, or too seeded to have a defensible
  "correct answer" — → that is itself the finding: retrieval quality is not the
  bottleneck at 716 rows. Stop, and revisit when any single World exceeds 2,000
  documents.
- **First-embed latency exceeds 5 s on a warm Ollama for the largest World (381
  rows)** → the lazy `index_all` paths make this a user-facing hang; redesign
  before shipping (see architect concern 2).
- **For the deferred consolidation:** revisit only when the operator states a
  concrete cross-World query he wants answered, or when two Worlds actually need
  to run concurrently. Neither is true today, and the memory record says the
  second is explicitly not the workflow. Revisit date: **2026-11-01**, or
  earlier on either trigger.

## Displacement

Saying yes to a narrowed one-sprint measurement displaces roughly a week:

- **Within arail:** the `2026-08-06-lab-integrity-review` follow-ups and the
  world-forge PDF-extraction successor (#173) both slip. The A25/A26 unmount
  defect — which I judge a *more* certain user-facing bug than embedding
  quality, and far cheaper — should be considered for the same sprint precisely
  because it is small; if it does not fit, it is the top of the next one.
- **Across QuKaiZen:** aeroLLM's GA gates are the company's public bet and get
  no attention this week. arail bundles aeroLLM as the maximus deep backend, so
  aeroLLM slipping is arail slipping later.
- **The honest cost of saying yes to the *full* proposal** (which I am rejecting)
  would have been 2–3 sprints, because a store consolidation is not done when the
  code lands — it is done when the migration has survived a month of the
  operator's real use, and any rollback is a data-migration in reverse.

The answer here is not "nothing." Phase 2 already spent a sprint building
machinery that is load-bearing on nothing; a second sprint making it
load-bearing on a case this thin would be sunk-cost reasoning wearing a roadmap.

## Recommended next step

**Proceed to `/architect` with a spec scoped to the wedge only:** the A/B
retrieval harness, the labelled fixture, and — conditional on the 15pp bar — the
provider swap at the ingest path with per-instance stores left intact.

Explicitly **out of scope for this sprint**, and to be recorded as such in
ARCHITECTURE.md: the `world_id` query-path threading (Tier 1.1) and the cutover
to the consolidated 2.0 store. The Phase 2 layer stays additive and unused for
now. That is an acceptable state; it is a rehearsal that has not yet earned its
performance.

### Concerns the architect must address at design time

1. **Silent-failure collision.** `dbspec/embed.py`'s central guarantee is "never
   fall back, fail loudly." Every call site it would land in does the opposite:
   `pkb_index.py:178, 326, 347, 403` wrap `index_all` in `except → _log.warning`,
   and `pkb.py:186-190` / `vector_index.py:126-129` turn errors into `[]`. Drop
   the loud provider into that swallowing plumbing and the observable behaviour
   is an index that silently stays empty — the exact 1.x failure mode the module
   docstring says it exists to prevent. The loudness has to reach the user, or
   the guarantee is decorative.

2. **Lazy re-index becomes a network call inside a request.**
   `_semantic_search` (`pkb.py:579-583`) calls `index_all(root)` when the table
   is empty, and `pkb_index.py:329-347` drops and rebuilds on schema mismatch —
   which a 128→768 dim change **will** trigger on every existing lab. Today
   that is instant and offline; after the swap it is hundreds of HTTP round
   trips to Ollama fired from inside a user's search. Design the re-embed as an
   explicit, resumable, progress-reporting operation, not a side effect of
   someone typing in the search box.

3. **The regex fallback is not scoped and never will be by a WHERE clause.**
   `pkb.py:650-671` sweeps the filesystem. Any future world-scoping that covers
   only the vector path produces a surface that *looks* scoped and is not —
   strictly worse than today's honest directory isolation. If Tier 1.1 is ever
   revived, this is its first design constraint.

4. **`LAB_MODE=airgapped` must hold.** The provider is loopback-Ollama only;
   `MODEL_API_BASE` (read at `embed.py:ollama_root`) is operator-settable and
   must not become a silent egress path for corpus text in airgapped mode.
