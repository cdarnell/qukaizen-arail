# Review: Tier 1.2 as a measurement — nomic vs `hash_embedding` (W0–W5)

**Date:** 2026-08-08
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) at `a115d90`
**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `3987f71`
**Vision:** [VISION.md](./VISION.md) at `e79a3b9` (binding gate)
**Baseline:** `8cb5760`
**Reviewed commits:** `a992b30` (W0) · `4f1cb58` (W1+W3) · `9362acd` (W2) · `5a444d1` (W4) · `a115d90` (W5)

## Verdict: PASS

I would bet the integration on this number. It reproduces exactly on a fresh run,
it survives every adversarial check I could construct in-session, and — most
importantly — it is corroborated by a **label-free** probe that does not touch the
builder's fixture at all. Two ASK findings and one required backlog filing, none
of which change the number or the go/no-go.

---

## What I re-ran myself (nothing below is taken on BUILD_LOG's word)

| Check | Command / method | Result |
|---|---|---|
| Fixture lint | `pytest tests/eval/ tests/dbspec/test_embed_airgapped.py -q` | **52 passed, 0 skipped** (fixture lint and the live-Ollama test both actually executed, not skipped) |
| Verbatim evidence, independently of the builder's test | own script: re-read each `relevant.path` from `lab/`, assert `evidence in text` | **32/32 labels verbatim byte-for-byte.** Also: every excerpt falls inside the first 4096 chars, i.e. inside the window both arms actually embed |
| Pre-registration order | `git log --follow` on both fixtures | `queries.yaml` and `exact_tokens.yaml` each have **exactly one commit, `9362acd`**, which precedes the only results commit `5a444d1`. No post-results edit |
| Scope | `git diff 8cb5760 -- pkb.py vector_index.py pkb_index.py wiki_vectors.py world_mount.py scripts/start.sh` | **empty.** Per-commit `--stat` on all six sprint commits confirms only `dbspec/embed.py`, `eval/retrieval/`, `scripts/eval/`, `tests/`, `.gitignore`, `pyproject.toml` were touched |
| Measurement reproducibility | re-ran the harness against live `lab/` into a scratch workdir | **identical**: hash 50.0%, nomic 90.6%, Δ +40.6pp, CI [+25.0, +56.2], rank-1 losses `[]`, manifest sha `034940c3…` unchanged |
| PII | regex sweep (email / digit-run≥6 / currency / manual read) over `queries.yaml`, `exact_tokens.yaml`, `results.json`, `RESULTS.md` | **clean.** The only digit runs are float scores and sha256 hashes. I read `lab/instances/debt-finance/pkb/research/program.md` directly: it holds generic AeroLLM seed content, no personal finances, and it is excluded from the fixture and pinned excluded by a test |

Working tree is clean; nothing uncommitted.

---

## Spec adherence

Executed W0–W5 in the specified order and stopped at the fork, exactly as
instructed. Interface contracts H1, H2, F1, F2, C3 are all honoured. Definitions
(recall@5, pooled = micro-average, Δ, rank-1 loss, k=5/k=10, seed) are fixed in
the module docstring and in code, not chosen after the data. Failure modes FM1–FM9
and FM16, FM18 all have the tests the strategy called for.

**Two acknowledged drifts, both accepted:**

1. **W1 and W3 landed in one commit.** The builder's reasoning holds: the
   pre-registration property that matters (harness before fixture, fixture before
   results) is intact, and splitting would have committed dead scoring code. No
   integrity consequence.
2. **Docs-registry rows excluded from the harness corpus** (H1's prose said
   `_iter_pkb_files` **+** `_build_docs_rows`; the data-flow diagram said the five
   PKB trees only). The builder followed the diagram. I agree with the call —
   `_build_docs_rows()` pulls a *global* `docs_registry.all_docs()` with no `root`,
   so the same rows would appear in all five worlds and per-world attribution would
   be incoherent. **Consequence to carry forward:** the measurement therefore does
   not cover the docs-registry slice of the production corpus, which W9's swap
   *will* re-embed. Note it in W9; do not re-litigate it here.

---

## The question that matters: is the number trustworthy?

### 1. Fixture honesty — the structural conflict is materially defused

The builder authored the fixture and ran the measurement. That is a real conflict,
and commit-order pre-registration is weak evidence on its own (nothing stops a
builder running the harness in a dirty tree before committing the fixture). So I
did not rely on it. Three independent lines of evidence:

**(a) A label-free corroboration probe I ran myself.** I built a query set that
involves no human labelling and no fixture: for 60 randomly-sampled documents per
world (seed 7), use the document's own `title:`/`# ` heading as the query and
require the document itself in the top 5. Same corpus, same embed inputs, both
arms:

| World | probes | hash recall@5 | nomic recall@5 |
|---|---|---|---|
| `ai` | 60 | 83.3% | 100.0% |
| `video-games` | 60 | 48.3% | 90.0% |
| **pooled** | **120** | **65.8%** | **95.0%** |

**Δ = +29.2pp on 120 fixture-free probes**, comfortably clearing the 15pp bar with
zero human judgement in the loop. This probe is, if anything, *biased toward hash*
(the title string is literally inside the embedded text). The direction and
magnitude of the published result survive without the fixture.

**(b) Strict dominance, and the win comes from where hash should be strong.**
From `results.json`: **13 queries nomic wins and hash loses; 0 queries hash wins
and nomic loses**; 16 both, 3 neither. There is no query anywhere in the fixture
where hash is the better retriever. And restricting to the **high-overlap
stratum** — the 21 lexically-friendly queries where hash has every advantage —
hash 66.7% vs nomic 100.0%, **Δ = +33.3pp**. The gate clears on the
hash-friendly stratum alone. "The fixture is a nomic-friendly paraphrase set"
therefore does not explain the result.

**(c) The questions read as document-derived, not retriever-derived.** I read all
32. They ask what the document *says* ("what optimizer tracks a running average of
both the gradient and the squared gradient"), each is answered by the quoted
evidence line, and none carries the tell of a question written from a ranked
result list (no query is phrased around a distinctive incidental string that only
a top-5 list would surface). Evidence quotes are drawn from term/skill/agent pages,
not from personal content.

**Stratification is honest, with one caveat.** The bins are computed by the
harness, not asserted, and the histogram is published (zero 8 / low 3 / high 21).
It lands at exactly the 25% floor — see ASK-1.

### 2. Harness correctness — the baseline gets a fair shot

Read `retrieval_ab.py` line by line. Both arms:

- consume **one shared `rows`/`texts` list per world**, built once in the loop —
  parity is structural, not asserted-after-the-fact (`retrieval_ab.py:479-494`);
- use the byte-identical production embed input `f"{name} {rel} {text[:4096]}"`;
- use the same `k=10` search, `hits[:5]` for recall@5, no `where`, no `min_score`,
  no approval gate;
- query with their **own** natural query vector: `hash_embedding(query)` — which
  is exactly what production `VectorIndex.search` does (`vector_index.py:195`) —
  and `embed_query()` with nomic's trained query prefix. This is the fair
  comparison, not a handicap.

**Metric fairness — checked empirically, not assumed.** LanceDB's default is L2.
I measured the norms: hash vectors are unit-norm by construction, and Ollama's
`/api/embed` returns unit-norm nomic vectors (I verified `‖v‖ = 1.000000` for both
a document and a query). On unit vectors L2 ranking is exactly cosine ranking, so
neither arm is advantaged by the metric choice.

**Bootstrap is paired and seeded.** `paired_bootstrap_ci` draws **one** index list
per resample and applies it to both arms (`retrieval_ab.py:355-360`) — that is a
correct paired bootstrap. `BOOTSTRAP_SEED = 20260808` and 10,000 resamples are
module constants committed in W1, before the fixture existed. Percentile CI at
2.5/97.5. Reproduced identically.

**FM7 holds:** `EmbeddingError` returns 1 before any `--json`/`--md` write, tested.
**FM5 holds:** workdir guard exits 2 before any table creation, tested both for
`*/pkb/.cache/lancedb` and `*/.wiki-cache`.

I found **no unfairness to the hash arm.**

### 3. The exact-token anomaly — resolved, and it strengthens the result

The architecture predicted hash would win here; it scored 1/10 to nomic's 9/10.
I determined which of the two explanations is true, and it is the second one.

**The fixture tests what it claims.** For all 10 exact-token queries I confirmed
the literal query token(s) are present in the expected document's embedded text
(`literal_in_doc=True`, 10/10). These are real lexical lookups.

**Hash fails them by collision, structurally.** Diagnostic run over the live
corpus, hash arm, rank of the expected document:

| query | non-zero buckets in query vector | rank of expected doc |
|---|---|---|
| `LAB_BUDDY` | 1 | **51 / 381** |
| `LAB_SRE` | 1 | 19 / 381 |
| `2026-08-07_c83e0a76` | 1 | 12 / 318 |
| `blended-apr-calc` | 1 | 11 / 82 |
| `LAB_LIBRARIAN` | 1 | 11 / 71 |
| `falsify-hypothesis` | 1 | 8 / 37 |
| `…/how-debt-snowball-method-works` | 4 | 8 / 82 |
| `https://github.com/lyogavin/airllm` | 3 | 3 / 37 |
| `wisdom-per-watt` | 1 | 4 / 71 |
| `dlss` | 1 | **1 / 318** (the only hit) |

The mechanism: 8 of 10 queries tokenise to a **single** token, so the query vector
is one signed spike in one of 128 buckets, and ranking is decided entirely by that
one bucket's signed weight in each document — a weight every other document also
populates from unrelated colliding tokens. In the `ai` world alone, **46 distinct
corpus tokens collide into `LAB_BUDDY`'s bucket** (`fp16`, `when_to_use`,
`preference`, `probability`, …). A 128-dimension SHA1 projection cannot do literal
lookup on a corpus of this size. That is a genuine, quantified property of the
incumbent — not a weak fixture — and it makes the case against `hash_embedding`
stronger, not weaker.

Corollary the sprint should record: **`hash_embedding` has no domain where it is
the better retriever on this corpus.** The "preserve hash for exact-token lookup"
argument is dead.

---

## Code quality findings

- [INFO] `retrieval_ab.py` is 779 lines with one long `run()` (~200 lines). It is a
  single-purpose eval script with a linear, readable flow and every scoring
  primitive factored out and unit-tested separately; I am not asking for a split.
- [INFO] `run()` re-derives `worlds`/`lab_root` that `main()` already computed;
  `__import__("os")` inline at `retrieval_ab.py:451` is ugly. Cosmetic.
- [INFO] `rank1_path`'s tie-break is dead in practice — exact-token search uses
  `k=1`, so there is never more than one hit to break. It is unit-tested and
  harmless; the deterministic contract is honoured trivially.
- [INFO] `_score_from_distance` rounds to 4 dp before the tie-break comparison,
  which could manufacture ties at `k>1`. No effect on any published number.
- [INFO] `_LOGGED_HYBRID_EGRESS` is a module global; the test file resets it via an
  autouse fixture, so ordering is safe. Fine.

## Security findings

**W0 airgapped guard (`dbspec/embed.py:75-101`) — reviewed as production code.**

- [INFO] **It does prevent corpus-text egress.** `_assert_local` is called at the
  top of `_post` (`embed.py:104-106`), which is the module's **only** network call
  site — `embed_texts`, `embed`, `embed_documents`, `embed_query` and `probe` all
  funnel through it. The raise happens before `urllib.request.Request` is even
  constructed, and the test suite pins that by monkeypatching both `Request` and
  `urlopen` to explode.
- [INFO] **It is not trivially bypassable.** I probed the parser edge cases:
  `urlparse().hostname` is lowercased, so case tricks fail; userinfo tricks
  (`http://127.0.0.1@evil.com`) resolve to `evil.com` and are blocked; an
  unparseable base yields `hostname = None → ""`, which is **not** in the allow-set,
  so it **fails closed**; `[::1]` and `[::ffff:1.2.3.4]` are handled correctly (the
  latter blocked). `OLLAMA_HOST=<remote>` is also covered, because the guard checks
  the *resolved* base rather than the env var.
- [INFO] It reads `LAB_MODE` at call time, so it cannot be defeated by import
  ordering. Setting `LAB_MODE=hybrid` disables it — that is the designed operator
  opt-in and is consistent with how the rest of ARAIL gates cloud egress, not a
  bypass.
- [ASK] **ASK-3:** no test for the `OLLAMA_HOST=<non-loopback>` path (the guard
  covers it; the coverage is implicit). One parametrised case would close it.
- [ASK] **ASK-4:** the guard is dead code today (no production caller). At W9 it
  becomes user-visible, and a user legitimately running Ollama on a LAN box under
  the default `airgapped` will get a hard `EmbeddingError`. That is the correct
  behaviour, but it needs a line in the W10 docs pass so it reads as a policy, not
  a bug.

**PII — no BLOCK.** `queries.yaml`, `exact_tokens.yaml`, `results.json` and
`RESULTS.md` contain no amounts, balances, account details, or personal names.
Verified by regex sweep and by reading all 42 entries. The `debt-finance` evidence
excerpts are all from generated glossary term pages (avalanche method, credit
utilization ratio, credit union, …) — generic financial definitions, no operator
data. `research/program.md` is excluded and the exclusion is pinned by a test.

- [ASK] **ASK-2 (see below)** concerns the corpus manifest's *path listing*, not
  the excerpts. It is an inference surface, not PII, and it is not a BLOCK.

- [INFO] The harness interpolates no user string into a LanceDB predicate and
  accepts no `where` clause, as required. No secrets are read or logged.

## Test coverage assessment

52 new tests, all passing on my run, 0 skipped. Mapping against the failure-mode
table:

| FM | Covered | Where |
|---|---|---|
| FM1 stratum floor | yes | `test_overlap_stratum_floor` (+ published histogram) |
| FM2 verbatim evidence | yes | `test_evidence_is_verbatim_in_the_labelled_file` — **and independently re-verified by me** |
| FM3 post-hoc relabelling | yes (reviewer-side) | `git log --follow`, verified above |
| FM4 PII | yes | `test_no_pii_in_evidence_excerpts` / `_author_notes` / `_debt_finance_research_program_not_used…` |
| FM5 workdir | yes | two exit-2 tests |
| FM6 arm parity | partial | `assert_arm_parity` unit-tested against synthetic inputs; the production path relies on structural sharing, which I confirmed by reading `run()`. Acceptable |
| FM7 no partial results | yes | `test_embedding_error_writes_no_results` |
| FM8 corpus drift | yes | `test_verify_manifest_detects_drift`; manifest sha matched on my re-run |
| FM9 coin-flip CI | yes | CI printed with the verdict; `PASS_INCONCLUSIVE` branch exists and is asserted reachable |
| FM16 airgapped egress | yes | 11 tests incl. pre-socket assertion |
| FM18 no-Ollama CI | yes | `requires_ollama` marker + `conftest` auto-skip |

Gaps, all minor:

- [ASK] **ASK-5:** no test asserts the bootstrap is *paired* (that both arms use the
  same resample indices). Determinism is tested; pairedness is not. I verified it by
  reading the code. A test that feeds perfectly correlated vs anticorrelated arms
  and asserts different CI widths would pin it. QA should add this.
- [INFO] The bootstrap resamples queries i.i.d., ignoring clustering by world.
  With Δ = +40.6pp and a lower bound of +25.0pp there is no plausible clustering
  correction that reaches zero, so this does not affect the verdict.
- [INFO] Every query has exactly one labelled relevant doc. Under-labelling
  penalises the *better* retriever (nomic's other top-5 hits, some of which are
  plausibly relevant, score as misses), so the published Δ is conservative.

**Regression.** `git diff 8cb5760` on the six protected files is **empty** — the
strongest guarantee the architecture asked for, and it holds. The builder did not
re-run the full suite at a clean `8cb5760`; I accept the substitution (the
predecessor sprint's documented baseline on this same branch, 53 failed → 52 failed
now, and nothing in the failing clusters imports `dbspec.embed`). The pre-existing
~28/52-failure baseline is not this builder's and did not affect this verdict.

## Performance assessment

Reported and within thresholds. `ai` (381 rows) embedded in 2.85 s wall-clock, under
VISION.md's 5 s disconfirming threshold. `video-games` (318 rows) took 4.21 s — also
under, but close enough that the ~75–134 rows/s range should be treated as the
planning number for `pkb reembed` UX, not the best case. hash is ~100× faster to
embed, which is exactly why C2's explicit re-embed verb exists.

## Tech debt delta vs the ARCHITECTURE.md prediction

As predicted (fixtures encode this corpus; a second eval surface with its own
scoring code). Net negative on the no-ship half, as designed. Two items the
architecture did not anticipate:

1. **`.wiki-cache/manifest.json` is indexed as an ordinary PKB row in every world**
   — 1.15 MB in `ai`, 230 KB in `debt-finance` — because `pkb._iter_pkb_files`
   skips dot-*files* but not dot-*directories*. 19 of the 889 rows are
   machine-generated JSON. The builder found this, correctly refused to fix it
   (out of scope), and correctly reproduced it in the harness (A4). It is neutral
   to the measurement (both arms see the same distractor). **It is a real
   production defect and it is not yet filed** — see required action 1.
2. **The corpus manifest publishes a private world's file listing** — see ASK-2.

---

## ASK findings (documented; none blocks the verdict)

- **ASK-1 — the stratum floor was reached by tuning one query's wording.**
  BUILD_LOG discloses this honestly: the first pass landed at 7/32 zero-overlap
  (21.9%), one short of the 25% floor, so `root-006`'s wording was iterated against
  the harness's own `jaccard_overlap()` until it returned exactly `0.0000`. The
  document label never changed and no embedder had been run. I accept the account,
  and I note the direction of the tuning is not neutral: the zero stratum is where
  hash scores 0.0%. **Materiality: nil.** Deleting all three `root` zero-overlap
  queries entirely leaves 29 queries at hash 51.7% / nomic 89.7% = **+37.9pp**, and
  the high-overlap stratum alone gives +33.3pp. The result does not depend on the
  zero stratum. QA should treat this as a fixture-provenance note, not a defect.

- **ASK-2 — `eval/retrieval/corpus_manifest.json` publishes the operator's private
  `debt-finance` world file listing to a public MIT repo.** No document text (H2's
  promise is kept, and a test pins it) and no PII. But the 82 committed paths
  include specific institutions and programs — `penfed-credit-union.md`,
  `greenpath-financial-wellness.md`, `hardship-program.md`,
  `debt-settlement-services.md`, `401k-loan.md` — plus `agents/debt_advisor/`,
  `agents/buddy/dreams/2026-08-03.md`. These are auto-generated world glossary
  pages, so they are weak evidence of anything; still, the *set* supports
  inferences about the operator's finances that the architecture's PII lint was
  never designed to catch (the lint only inspects excerpts). **Not a BLOCK** — no
  amounts, balances, account details, or names of people. **Required before this
  branch is pushed public:** either explicit operator sign-off on the listing, or
  replace `path`/`name` with their sha256 for the `debt-finance` world only
  (`--verify-manifest` works identically on hashed paths). Route to the operator.

- **ASK-3** — add an `OLLAMA_HOST=<non-loopback>` case to the guard tests.
- **ASK-4** — document the LAN-Ollama consequence of the guard during W10.
- **ASK-5** — add a test that pins the bootstrap as *paired*.

---

## Required actions before merge

1. **File the two carried debts in `sprints/BACKLOG.md`** (BUILD_LOG named both and
   deliberately deferred filing to avoid scope drift; the architecture requires
   unanticipated debt to have a home before PASS):
   - `pkb._iter_pkb_files` indexes `.wiki-cache/manifest.json` and other
     dot-directory contents as PKB rows in every world (1.15 MB of
     machine-generated JSON in `ai`). Pre-existing, production, not this sprint's.
   - The docs-registry corpus slice is unmeasured by this A/B and will be
     re-embedded by W9 — note it as a known coverage gap on the W9 work item.
2. **Route ASK-2 to the operator** before this branch is pushed to a public remote.
   Sign-off or hash the `debt-finance` paths; either is fine.
3. **Append to RESULTS.md** (one short section, no re-measurement): the exact-token
   collision diagnostic above, and the corrected conclusion that
   `hash_embedding` has **no** stratum on this corpus where it is the better
   retriever. This is the single most load-bearing correction to the
   architecture's stated assumption and it should live in the published artifact,
   not only in a review file.

ASK-1, ASK-3, ASK-4 and ASK-5 are QA-phase items; they do not gate merge.

---

## Should W6–W10 proceed as designed?

**Yes — proceed, with three design amendments.** The gate is cleanly met (Δ +40.6pp
against a 15pp bar, CI lower bound +25.0pp, zero rank-1 losses), it reproduces, and
it survives a fixture-free corroboration. `PASS`, not `PASS_INCONCLUSIVE`, so no
operator sign-off is required on the statistics themselves.

Amendments to ARCHITECTURE.md before the builder resumes:

1. **Drop the "preserve hash for exact-token lookup" framing wherever it appears.**
   F2's premise is now measured false: hash ranks the correct document 51st of 381
   for `LAB_BUDDY`. `hash_embedding` still stays in the tree because `wiki_nodes`,
   `agent_workflows` and `experiments` use it (A5) — that is the only reason, and
   C4's sidecar should say so.
2. **W9 must state the docs-registry gap explicitly.** The swap re-embeds rows the
   A/B never scored. Low risk (same embedder, same input shape), but it should be
   an acknowledged, written assumption rather than a silent one.
3. **Use ~75–134 rows/s, not the best case, when sizing `pkb reembed`'s progress
   and ETA UX** (C2). `video-games` at 4.21 s for 318 rows is the realistic figure;
   a 5,000-row lab is a ~60 s operation, which is exactly the kind of wait C2's
   explicit verb exists to make visible.

W6 (the C1 error contract) remains the correct next chunk, and it should land
before any embedder swap, unchanged from the original ordering.
