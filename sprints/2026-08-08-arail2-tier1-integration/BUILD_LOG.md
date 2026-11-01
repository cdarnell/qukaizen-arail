# Build log: Tier 1.2 as a measurement — nomic vs `hash_embedding`

**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `3987f71`
**Review:** [REVIEW.md](./REVIEW.md) — verdict **PASS**, three design
amendments (binding, see W6–W10 section below)
**Started:** 2026-08-08
**W0–W5 finished:** 2026-08-08
**W6–W10 finished:** 2026-08-08

**Invocation 1 scope: W0–W5 only.** The measurement. See the "W0–W5"
section below (unchanged from the original build).

**Invocation 2 scope: W6–W10, plus REVIEW.md's three required actions.**
The gate PASSED (Δ +40.6pp, CI [+25.0, +56.2], zero rank-1 losses) and
REVIEW.md's verdict was PASS — the conditional integration is now live
work, per the coordinator's instruction. See the "W6–W10" section below.

## Plan

| # | Files | Change | Test | Commit ref |
|---|---|---|---|---|
| W0 | `src/arail/dbspec/embed.py` | `_assert_local(base)` airgapped-egress guard on `_post` | `tests/dbspec/test_embed_airgapped.py` | `a992b30` |
| W1 | `scripts/eval/retrieval_ab.py`, `eval/retrieval/corpus_manifest.json`, `eval/retrieval/stopwords.txt`, `eval/retrieval/pii_deny.txt`, `.gitignore`, `pyproject.toml` | Read-only harness: `--dump-corpus`, workdir-safety assert (exit 2), corpus manifest emitter (H2), `requires_ollama` marker + auto-skip conftest | `tests/eval/test_retrieval_ab.py` (24 tests, stub embedder, no Ollama) | `4f1cb58` |
| W2 | `eval/retrieval/queries.yaml`, `eval/retrieval/exact_tokens.yaml`, `tests/eval/test_retrieval_fixture.py` | Hand-authored fixtures (F1/F2), read from `--dump-corpus` output only, before any measurement exists | `tests/eval/test_retrieval_fixture.py` (16 tests: schema, verbatim-quote lint, PII lint, overlap-stratum floor) | `9362acd` |
| W3 | `scripts/eval/retrieval_ab.py` | Complete harness: hash arm, nomic arm, recall@5/MRR@10/rank-1/strata/bootstrap CI/latency, `results.json` + RESULTS markdown emitter | folded into W1 commit — see delta note below | `4f1cb58` |
| W4 | `eval/retrieval/results.json`, `sprints/2026-08-08-arail2-tier1-integration/RESULTS.md`, `tests/eval/test_retrieval_ab_live_ollama.py` | Run the harness against the live `lab/` corpus with real Ollama; publish the number | `tests/eval/test_retrieval_ab_live_ollama.py` (real Ollama, `@pytest.mark.requires_ollama`) | `5a444d1` |
| W5 | `sprints/2026-08-08-arail2-tier1-integration/BUILD_LOG.md` | Evaluate against the 15pp / zero-rank-1-loss gate; write the verdict | n/a | this commit |

Order follows ARCHITECTURE.md "Recommended implementation order" §1–6.

**Delta from plan:** W1 and W3 landed as one commit (`4f1cb58`) rather than
two. `scripts/eval/retrieval_ab.py` is a single file; splitting the
"read-only skeleton" half from the "scoring" half into two commits would
have meant committing a script with dead/unused scoring code paths in the
first commit and no meaningful boundary a reviewer could exercise
independently (the workdir guard and `--dump-corpus` genuinely need no
scoring code, but the scoring code needs the same row-reading plumbing the
skeleton provides — there's no clean cut). What matters for the
pre-registration rule (F1.3) held regardless: the harness (with full
scoring capability) was committed and usable to author fixtures via
`--dump-corpus` *before* the fixture commit, and the fixture commit
(`9362acd`) landed before any commit containing a measurement result
(`5a444d1`). `git log --follow -- eval/retrieval/queries.yaml` shows
exactly one commit, `9362acd`.

## Execution

### W0 — airgapped-egress guard

`src/arail/dbspec/embed.py`: added `_assert_local(base)`, called at the top
of `_post()` before any `urllib.request.Request`/`urlopen` call. Under the
default `LAB_MODE=airgapped`, a non-loopback `MODEL_API_BASE` raises
`EmbeddingError` naming the offending value and the env var, before any
socket is touched (asserted in tests by monkeypatching `urlopen`/`Request`
to explode). Under `LAB_MODE=hybrid`, non-loopback is allowed and logged
once at INFO. `127.0.0.1`, `::1`, `localhost` are always accepted.

11 unit tests, no Ollama required. Commit `a992b30`.

### W1 + W3 — harness (H1) and corpus manifest (H2)

`scripts/eval/retrieval_ab.py` reads both worlds' corpora **read-only**
through `pkb._iter_pkb_files` (I deliberately did **not** also call
`pkb._build_docs_rows()` — see "Design decisions" below), builds an
in-process `Row` per file with the byte-identical embedding input string
`f"{name} {rel} {text[:4096]}"` (A4, `pkb.py:524`), and:

- `--dump-corpus` prints `world · path · name · 800-char preview` per row
  and mutates nothing (tested: file mtimes + name set unchanged before/after).
- `--write-manifest` / `--verify-manifest` emit and check
  `eval/retrieval/corpus_manifest.json` (H2): `world`, `path`, `name`,
  `source_kind`, `bytes`, `sha256(embed_input)` per row — no document text
  anywhere, checked by a dedicated unit test.
- `assert_safe_workdir()` resolves `--workdir` and exits 2 if it lands under
  any `*/pkb/.cache/lancedb` or `*/.wiki-cache` path (FM5), before any
  scratch table is created.
- Both arms build their own scratch LanceDB tables under `--workdir` (never
  the live `.cache/lancedb`) directly via the `lancedb` API — **not** via
  `vector_index.VectorIndex.search()`, because that method hardcodes
  `hash_embedding` for the *query* vector, which would make it impossible to
  score the nomic arm's queries with `embed_query()`'s prefix. The harness
  imports `hash_embedding` and `dbspec.embed.embed_documents/embed_query`
  directly per C6.
- Scoring: `recall_at_k`, `reciprocal_rank`, `rank1_path` (deterministic
  ascending-path tie-break), `jaccard_overlap`/`overlap_stratum` (using the
  committed 40-word stoplist, same `_TOKEN_RE` as `vector_index.py:31`),
  `paired_bootstrap_ci` (seed `20260808`, 10,000 resamples, fixed in the
  module — not chosen after seeing data).
- `embed_arm_vectors()` raises straight through on `EmbeddingError`; `run()`
  catches it, prints to stderr, returns 1, and writes **no** `--json`/`--md`
  output (FM7) — verified by a test that monkeypatches `embed_documents` to
  raise and asserts `results.json` does not exist.
- Arm parity (FM6): both arms are built from the *same* `rows`/`texts`
  object per world in one loop iteration — there is no separate per-arm row
  list to diverge, so the parity is structural rather than checked at
  runtime. `assert_arm_parity()` still exists as an importable function and
  is exercised directly by two unit tests against synthetic mismatched
  inputs, pinning the invariant it encodes for any future caller.

24 unit tests (`tests/eval/test_retrieval_ab.py`), synthetic corpora and a
stub embedder, no Ollama required. `pyproject.toml` gained a
`requires_ollama` marker; `tests/eval/conftest.py` auto-skips
`@pytest.mark.requires_ollama` tests when `embed.probe()` fails (FM18).

`.gitignore` gained `lab/.eval-cache/` (harness scratch, never committed).

Commit `4f1cb58`.

**Design decision — docs registry rows excluded from the harness corpus.**
H1's prose says the harness reads through "`pkb._iter_pkb_files` +
`pkb._build_docs_rows`", but the architecture's own data-flow diagram lists
only the five `lab/pkb*` PKB trees as measurement sources, with no mention
of the docs registry. `_build_docs_rows()` pulls from a *global*
`docs_registry.all_docs()` call with no `root` parameter — in production
every world's `index_all()` call appends the *same* global docs rows,
which would make "per-world" fixture attribution incoherent (a docs page
would be simultaneously "in" all five worlds' corpora) and would embed the
docs corpus twice per arm for no measurement value, since the question this
sprint answers is about the embedder, not about docs-vs-PKB content. I
followed the diagram, not the prose, and did not call `_build_docs_rows()`
anywhere in the harness. This is a fixture-construction judgment call
within W1's scope, not a redesign of an interface contract — flagging it
here rather than treating it as silent.

**Observed, not fixed:** `pkb._iter_pkb_files()` does not exclude files
under hidden directories (only filenames starting with `.` are skipped, not
ancestor directory names), so `.wiki-cache/manifest.json` — a ~1.1 MB
machine-generated index file — is indexed as an ordinary PKB row in every
world today, in production, independent of this sprint. The harness
faithfully reproduces this (per A4), and no fixture question references
that file. This is pre-existing behaviour in `pkb.py`, which is out of
scope to touch (see ARCHITECTURE.md §"What the builder must NOT touch" #6).
Worth a line in `sprints/BACKLOG.md` for a future sprint; not filed here to
avoid scope drift in this build.

### W2 — fixtures (F1/F2), pre-registered

`eval/retrieval/queries.yaml`: 32 natural-language questions (root 7, ai 7,
video-games 6, debt-finance 6, qukaizen 6 — every world clears the raised
floor of ≥6). `eval/retrieval/exact_tokens.yaml`: 10 literal-token queries,
2 per world (floor ≥8).

**Labelling method.** Every question was written after reading
`scripts/eval/retrieval_ab.py --dump-corpus --world <slug> --lab-root
/Users/netsushi/ProJects/qukaizen-arail/lab` output, then confirmed against
the full file on disk (the 800-char preview is sometimes too short to see
a good quotable sentence). No query was written by running a search and
labelling the result, and none was LLM-generated from the scored documents.

**Lexical-overlap stratification (F1.4).** I did not target a stratum
before writing a question — I wrote the most natural phrasing of each
question first, computed the Jaccard overlap against the full labelled
document's embed input (title + path + first 4 KB of text, matching what
both arms actually embed), and only *afterward* checked the distribution
against the ≥25%/≥25% floor. The first pass landed at 7/32 zero-overlap
(21.9%) — one short of the floor — so I converted one more query
(`root-006`, "how does a companion program decide if a passing detail
deserves a spoken remark", vs. `skills/observe-lab/SKILL.md`) to a
zero-overlap paraphrase by iterating the wording against the harness's own
`jaccard_overlap()` function until it hit exactly `0.0000`, the same
mechanical process used for the other seven zero-overlap queries. This
iteration changed *wording only*, never which document was marked
relevant, and happened entirely before any embedder was run against the
real corpus (no `--arm` run had occurred yet, only `--dump-corpus`).
Final distribution: zero 8/32 (25.0%), low 3/32, high 21/32 — both floor
conditions clear exactly at 25%, not padded.

**PII sign-off.** `tests/eval/test_retrieval_fixture.py` runs the
email/digit-run(≥6)/currency(≥4 sig-digits)/`pii_deny.txt` lint over every
`evidence` excerpt and `author_note`. Zero violations found across all 32
+ 10 entries. I read `lab/instances/debt-finance/pkb/research/program.md`
directly as part of the labelling pass (per the sprint instructions'
explicit flag on that file) and found it currently holds the generic
default AeroLLM research-program seed content ("SSD-hosted model
inference — lab research program") — **not** personal financial data, as
of 2026-08-08. I excluded it from the fixture regardless, per instruction,
and added a dedicated test
(`test_debt_finance_research_program_not_used_as_evidence_source`) that
fails the build if any future edit adds it back as an evidence source. **I
sign off that no evidence excerpt or author_note in this fixture contains
personal data**, checked both by the automated lint and by direct reading.

16 unit tests (`tests/eval/test_retrieval_fixture.py`). Commit `9362acd`
— this is the pre-registration commit. `git log --follow -- eval/retrieval/
queries.yaml` shows exactly one commit (`9362acd`), which precedes the
results commit (`5a444d1`).

### W4 — the measurement

Ran `scripts/eval/retrieval_ab.py --lab-root
/Users/netsushi/ProJects/qukaizen-arail/lab --arm both --json
eval/retrieval/results.json --md sprints/2026-08-08-arail2-tier1-
integration/RESULTS.md`. Corpus: 889 rows (root 37, ai 381, video-games
318, debt-finance 82, qukaizen 71) — larger than PHASE1_AUDIT's 716-row
snapshot; A1's fallback applies (this repo's corpus moves; the manifest
hash is the drift check). The corpus manifest sha256 computed at
measurement time (`034940c3...`) matches the one committed in W1
byte-for-byte, so nothing drifted between labelling and measurement.

See `RESULTS.md` for the full table. Summary in the next section. Commit
`5a444d1`, plus one new integration test
(`tests/eval/test_retrieval_ab_live_ollama.py`, `@pytest.mark.requires_ollama`,
real Ollama, no stub) validating the same code path end-to-end on a
12-row synthetic corpus.

## Verdict (W5)

| Gate condition | Requirement | Measured | Pass? |
|---|---|---|---|
| Pooled recall@5 delta | ≥ 15.0 pp | **+40.6 pp** | yes |
| Exact-token rank-1 losses | 0 | **0** | yes |
| Bootstrap CI lower bound (informational, PASS_INCONCLUSIVE trigger) | > 0 | **+25.0 pp** | yes — not inconclusive |

**VERDICT: PASS.**

- Pooled recall@5: hash **50.0%**, nomic **90.6%**. Δ = **+40.6 pp**.
- 95% paired-bootstrap CI on Δ (seed `20260808`, 10,000 resamples):
  **[+25.0, +56.2] pp** — the lower bound is comfortably above zero, so
  this is not a coin-flip result riding a small sample (FM9's concern).
- Exact-token rank-1: hash **1/10**, nomic **9/10**, rank-1 losses: **[]**.
  This is worth flagging honestly rather than editorializing away: the
  architecture's working assumption ("this is the class where lexical
  hashing legitimately wins") did **not** hold on this corpus — hash's
  128-dim SHA1 projection scored worse than nomic even on literal-token
  queries. The gate only requires zero *losses* (cases where hash beat
  nomic and nomic lost it), and there were none, so the gate clears; but
  the assumption that motivated F2's design was wrong for this corpus and
  that's worth carrying into any future fixture revision.
- Per-world recall@5 (nomic): root 100%, ai 85.7%, video-games 83.3%,
  debt-finance 100%, qukaizen 83.3% — no world is an outlier dragging the
  pooled number.
- Overlap-stratum recall@5 (nomic): zero-overlap queries still recover
  62.5%, vs. hash's 0.0% on that same stratum — this is close to the core
  claim being tested (paraphrase recall where lexical overlap is absent)
  and it is the strongest single number in this result.
- Embed throughput: the 381-row `ai` world embedded in 2.85s wall-clock on
  a warm Ollama — well under VISION.md's 5-second first-embed disconfirming
  threshold. `root`'s smaller 37-row world took 0.65s.

**A2 (can we honestly label 30 questions) held.** The corpus supported
32 NL + 10 exact-token questions without resorting to generic seed
material for evidence, except where the seed material was itself germane
(the `sources/seeds/model-building/` primers, which are real installed
KB content, not filler).

**FM9 (coin-flip result) does not apply.** CI lower bound is +25.0pp, well
clear of zero.

## Architect feedback required

None. The architecture's plan executed as specified through W5, with one
documented, in-scope construction decision (docs-registry exclusion, see
above) and one honest finding that contradicts a stated assumption
(hash's exact-token performance) rather than a gap requiring a redesign.

## What invocation 1 did NOT do (by design, that invocation)

Per that invocation's scope, **W6–W10 were not attempted**, regardless of
the PASS verdict — confirmed at the time by `git diff --stat 8cb5760 --
src/arail/pkb.py src/arail/vector_index.py src/arail/pkb_index.py
src/arail/wiki_vectors.py` being empty, and no embedder-selection flag of
any kind existing anywhere in production code. **This restriction has
since been lifted** — see the "W6–W10" section below, which is invocation
2, resumed after REVIEW.md's PASS verdict per the coordinator's explicit
instruction.

## Final state (invocation 1, W0–W5)

- Tests added this build: 11 (W0) + 24 (W1/W3) + 16 (W2) + 1 (W4 live
  integration) = **52 new tests, all passing.**
- `PYTHONPATH=src .venv/bin/python -m pytest tests/eval/ tests/dbspec/ -q`:
  52 passed.
- Full suite regression check: ran the complete `pytest -q` suite
  (972s). Result: **52 failed, 4240 passed, 18 skipped, 3 xfailed, 7
  errors**, against the predecessor sprint's documented baseline on this
  same branch (`sprints/2026-08-08-arail2-declarative-persistence/
  SPRINT.md`) of **53 failed / 4228 passed**, both runs dominated by the
  same unrelated pre-existing clusters (world-forge API, swarm-goal
  surfaces, reset-stop-scope, shell-source-safety, runtime-profile,
  recap-core — none of which import `dbspec.embed`, reference
  `MODEL_API_BASE`, or touch anything this build changed). Passed count
  rose by 12 net (4240 vs 4228) — consistent with the ~52 new tests I
  added landing in files pytest already collected, minus a couple of
  pre-existing flakes that didn't reproduce this run. Failed count is
  *lower* than the documented baseline (52 vs 53), not higher. I did not
  additionally re-run the full suite at a clean `8cb5760` checkout in this
  invocation (a 16-minute run) given the predecessor sprint's own
  documented baseline on this exact branch already provides the
  comparison point and no code path I touched (`embed.py`'s `_post`,
  called only from inside `dbspec.embed`) is imported by any of the
  failing files. Per ARCHITECTURE.md's stronger regression guarantee: `git
  diff --stat 8cb5760 -- src/arail/pkb.py src/arail/vector_index.py
  src/arail/pkb_index.py src/arail/wiki_vectors.py src/arail/world_mount.py
  scripts/start.sh` is empty — confirmed.
- Lines changed: 8 commits, `src/arail/dbspec/embed.py` +33/-1,
  ~10 new files under `eval/retrieval/`, `scripts/eval/`, `tests/eval/`,
  `tests/dbspec/`.
- No TODO comments left in any new file. No commented-out code.

---

# W6–W10: the conditional integration (invocation 2)

**Ordering, per REVIEW.md's endorsement:** C1 error contract (W6) first —
"the loudness must exist before there is anything to be loud about" — then
C2 (`pkb reembed`, W7), then the embedder swap (W9), then C4's doctor
read-side + C5 setup (W8+W10, one commit). This is the coordinator's stated
order, not architecture's original W6→W7→W8→W9→W10 numbering (provenance
*writing* is inseparable from W7/W9 themselves — C2's shadow-build-and-swap
and W9's `index_all` both write the sidecar as their own last step — so
"provenance" as a separate chunk collapsed into the *read-side* check,
which genuinely can't exist before there's a writer to check against).

## Design amendments from REVIEW.md (binding, applied as written)

1. **"Preserve hash for exact-token lookup" framing dropped.** Nowhere in
   this build's code, comments, or commit messages does `hash_embedding`'s
   survival get justified by retrieval quality. Every place it's
   mentioned (pkb_index.py docstrings, `sprints/BACKLOG.md`'s new "two
   vector spaces" entry, this log) states the actual reason: A5 —
   `wiki_nodes`/`agent_workflows`/`experiments` still call it directly and
   swapping those three call sites was out of scope.
2. **W9 states the docs-registry gap as a written assumption.**
   `collect_pending_rows()`'s docstring in `pkb.py` (landed in W7, since
   that's where the function was introduced) says explicitly: the A/B
   never scored the docs-registry slice; `index_all`/`pkb_reembed`
   re-embed it anyway "on the strength of the general result... rather
   than a slice-specific measurement." Also recorded as a `sprints/
   BACKLOG.md` item (filed in the first required-actions commit,
   `90b56ce`, before W6 started).
3. **`pkb reembed`'s documented throughput is the measured 75–134 rows/s**
   (`docs/cli.md`'s new `reembed` section), not the best-case 380+ rows/s
   the `ai` world hit. The `--dry-run` ETA itself is never hardcoded to
   either figure — it always runs a live 32-row timing probe against
   whatever the real embedder does on the machine it's run on — so this
   amendment is really about not misleading the reader of the docs, which
   is where I applied it.

## Required actions from REVIEW.md

1. **Manifest path redaction — withdrawn before any code changed.** The
   coordinator's correction (recorded in the sprint ledger at `c94281a`)
   arrived while I had only run read-only `git ls-files` checks; no edit
   to `retrieval_ab.py`'s manifest generator, `corpus_manifest.json`, or
   `RESULTS.md`'s recorded manifest sha was ever made. Nothing to revert;
   the W4 commit's provenance chain is intact.
2. **Two carried debts filed in `sprints/BACKLOG.md`** (commit `90b56ce`,
   before W6): the `.wiki-cache/manifest.json` dot-directory indexing
   defect, and the docs-registry coverage gap. Two more filed in this
   invocation once W7/W9 created them (see "Debt filed this invocation"
   below): the provenance-sidecar-is-second-best item and the two-vector-
   spaces item, both required by ARCHITECTURE.md's own tech-debt section
   to be filed "at build time."
3. **Exact-token collision diagnostic appended to `RESULTS.md`** (commit
   `90b56ce`, before W6) — the corrected conclusion ("hash has no stratum
   on this corpus where it is the better retriever") now lives in the
   published artifact, not only in REVIEW.md.

## W6 — C1 error contract (commit `4af4691`)

`pkb_index.py` gained the degraded-state primitives
(`set_degraded`/`clear_degraded`/`embedding_status`), a shared
`_index_all_reporting_embedding_errors()` wrapper used by all four
`index_all()` call sites, and a restructured `_flush()` per-row loop that
aborts (not per-path-retries) on `EmbeddingError` and re-arms the retry
timer at a 60s back-off instead of the normal 2s debounce (FM17). The
`_schema_ok` dimension check was split into `_schema_column_status()` so
`ensure_ready` can tell "missing columns" (still safe to drop-and-rebuild)
from "wrong dimension" (never drops — the exact failure the architecture
built C2 to prevent, FM12) apart, and a new C4 read-side check compares
the `pkb_provenance` sidecar against the current spec, degrading (not
dropping) on disagreement or absence.

New leaf module `pkb_provenance.py` (write/read/agrees_with_spec) so
`pkb.py`, `pkb_index.py`, and the not-yet-written `pkb_reembed.py` share
one sidecar implementation.

13 new tests (`tests/test_c1_error_contract.py`); 5 pre-existing
`pkb_index` tests updated because they assumed the *old* "any dimension
mismatch triggers rebuild" contract, which C2/FM12 deliberately reverses.

**This was the first commit in the sprint where `pkb.py`/`pkb_index.py`
were touched at all** — every line in it is authorized by C1/C4, per
ARCHITECTURE.md's "untouched unless C1/C2/C4 says so" boundary.

## W7 — `./arailctl pkb reembed` (commit `5718dc5`)

New module `src/arail/pkb_reembed.py`: shadow build in
`.cache/lancedb.next/`, one checkpoint write per batch, SIGINT sets a flag
(checked between batches, so an in-flight batch always finishes and
checkpoints before the process exits 130), `--resume` refuses on a
model/dim/spec_sha256 mismatch, the live table only replaces the old one
(renamed to `.bak-<ts>`) after every row succeeds, and the provenance
sidecar is written last.

`pkb.py` gained `collect_pending_rows()`/`_collect_docs_rows()` — row
construction split from embedding, so `index_all` and `pkb_reembed` can
never diverge on the embed-input string (A4). This landed as a pure
refactor: `index_all`'s own behaviour was **unchanged** in this commit
(still `hash_embedding`, still row-at-a-time) — the actual embedder swap
is W9, one commit later, exactly as REVIEW.md's ordering specifies.

**A real bug found by writing this code, not by review:** `pkb_reembed`'s
own checkpoint (`reembed-state.json`) and provenance sidecar
(`pkb_pages.provenance.json`) both live under `.cache/` with a `.json`
suffix, and `_iter_pkb_files` only excludes files whose own *name* starts
with a dot — not files under a dot-prefixed *directory*. Without a fix,
every `pkb reembed` run would re-embed its own bookkeeping files on the
next pass. Fixed narrowly: `_iter_pkb_files` now excludes any path with
`.cache` as a path component. Deliberately did **not** generalize this to
the already-filed, deliberately-deferred `.wiki-cache` defect — that stays
exactly as REVIEW.md required it to be filed (a separate, pre-existing,
out-of-scope bug), while `.cache` needed fixing because C2's own new state
recreates the bug on every run otherwise. New regression test:
`test_cache_dir_contents_never_indexed_by_iter` in
`tests/test_pkb_index_qa.py`.

`arailctl` gained the `pkb reembed` sub-verb (`--world <slug> | --root |
--all`, `--resume`, `--dry-run`, `--yes`), dispatched one
`python -m arail.pkb_reembed` call per resolved pkb root, reusing
`scripts/lib/instances.sh`'s `inst_pkb_dir`/`inst_list_slugs` (already
sourced at the top of `arailctl`) rather than re-deriving path logic.
**Found and fixed a real bash 3.2 bug** by hand-testing the dispatch
against a stubbed `.venv` (never against the operator's real lab — see
"What was and wasn't run against real data" below): `"${_reembed_extra[@]}"`
on a legitimately-empty array aborts under `set -euo pipefail` on macOS's
default bash 3.2 (fixed only in bash 4.4+). Fixed with the same
`${arr[@]+"${arr[@]}"}` guard `restart`'s `_switch_live` already uses
(`arailctl:708`) — a pattern this codebase has hit and fixed before.
`docs/cli.md` documents the new sub-verb.

12 new tests (`tests/test_pkb_reembed.py`): happy path + provenance,
backup-on-second-run, dry-run writes nothing, empty corpus, FM13 (SIGINT
mid-run leaves the live table untouched, checkpoint written, `--resume`
completes to the full row count), checkpoint spec-mismatch refusal,
`EmbeddingError` propagation, CLI exit codes.

## W9 — the production embedder swap (commit `4a6b726`)

`index_all()`: computes every vector via one batched `embed_documents()`
call over the whole pending set **before** `VectorIndex.replace()` is
touched (`replace()` is `mode="overwrite"` — this ordering is the
non-negotiable the architecture named, and it is structurally impossible
to violate now, not just tested-for: the vectors list is built, then
zipped into rows, then written — there is no code path that writes a row
before its vector exists). `EmbeddingError` propagates untouched (LOUD).
Provenance is written last, after `replace()` returns.

`_semantic_search()`: the lazy `if idx.count() == 0: index_all(root)` call
is **removed** (FM11). An empty index degrades honestly, naming
`./arailctl pkb reembed` in the message, instead of firing a synchronous
rebuild from inside a search request. The query is now embedded via
`embed_query()`; an `EmbeddingError` there is caught, logged at ERROR,
activity-logged at severity `error`, and degrades — `search()` already
fell through to the regex sweep and already labelled those results
`source="keyword"`, so **no change was needed to `search()` or
`search_for_agents()` at all** — the existing structure was already
correct for this contract, it just needed `_semantic_search` to actually
return `[]` on a real failure instead of a hash-embedding call that could
never fail.

**A genuine, disclosed design decision: `vector_index.py` was not
touched, at all.** `VectorIndex.search()` always computes the query
vector itself via `hash_embedding` at a fixed dimension — correct for
hash-embedded tables, wrong for a 768-dim nomic one, and it offers no way
to hand it a precomputed vector. The architecture's own boundary list
names `pkb.py`/`vector_index.py`/`pkb_index.py`/`wiki_vectors.py` as
"untouched unless C1/C2/C4 says so," and none of C1/C2/C4's bullet lists
name a change to `vector_index.py` specifically (only line-number
references to `pkb.py`/`pkb_index.py` call sites, plus context citations
into `vector_index.py` that don't propose editing it). I read that as
deliberate: the swap works by having `pkb.py` do its own kNN lookup
against the already-open LanceDB table (`_table_search_by_vector()`, ~15
lines, the same post-processing `VectorIndex.search()` already does)
rather than by extending `VectorIndex` itself. `vector_index.py`'s `git
diff` against `8cb5760` is empty as of this commit — confirmed the same
way REVIEW.md confirmed it for W0–W5.

`pkb_index.py`'s `_build_row()` (used by `_flush`'s incremental upsert)
now embeds via `embed_documents` too, matching `index_all`. It raises
`EmbeddingError` deliberately uncaught — the one caller, `_flush`, already
catches it separately (landed in W6, before this swap existed to trigger
it) to abort the whole flush rather than retry-storm a dead provider.

**Test-fixture fallout, all mechanical, no behavioural gap:** ~13
pre-existing tests across `test_docs_sprint3_qa.py`, `test_docs_ingest.py`,
and `test_pkb_index_qa.py`/`test_pkb_index.py`/`test_pkb_index_perf.py`
either called `VectorIndex(...).search()` directly at its default 128-dim
(now dimension-incompatible with a nomic-populated table — switched to
reading the LanceDB table directly, since those tests are about row
presence/dedup, not retrieval) or pre-seeded a table with a 128-dim
`hash_embedding("seed")` vector and then exercised `_flush`'s incremental
upsert, which now writes new rows at 768-dim into the *same* table — a
hard LanceDB schema conflict, not a soft one. Seed vectors bumped to
`dim=768` to match what the (globally stubbed, see below) embedder
actually produces. None of these changes altered what the test was
actually verifying.

**`tests/conftest.py` gained an autouse `_stub_embedding_provider` fixture
in W6**, ahead of this swap landing — it stubs `embed_documents`/
`embed_query`/`embed` with a deterministic, network-free fake (reusing
`hash_embedding` at `EMBEDDING_DIM`) for every test not marked
`@pytest.mark.requires_ollama`. Without it, this commit would have turned
every test that exercises `index_all`/`_semantic_search` into a live-
Ollama-required integration test — a generalized version of FM18 the
architecture only explicitly named for the eval harness.

8 new tests (`tests/test_w9_embedder_swap.py`): provenance-written-after-
swap, `EmbeddingError`-writes-nothing-and-leaves-existing-table-untouched,
empty-corpus-makes-no-embed-call, FM11 (zero `embed_query` *and* zero
`index_all` calls from an empty-index search), query-`EmbeddingError`-
degrades-and-falls-back, a real-hit mechanics sanity check, and FM15
(closed-port `MODEL_API_BASE` — using the **real** `embed_documents`,
restored via a pre-stub-captured reference, not the stub — writes zero
vectors and leaves the existing index intact, message names
`ollama pull nomic-embed-text`).

## W8 + W10 — doctor exit-3 wiring + setup.sh pull (commit `a69ff2a`)

`doctor.py`'s `check_knowledge_base()` now reports `embed.probe()`'s
result and `pkb_index.embedding_status()`. **Only a provenance
disagreement is a required (exit-3-degrading) finding** — every other
degraded reason (Ollama unreachable, index not built yet) stays
INFO-only, matching the existing precedent for "no model configured" and
preserving the CI smoke job's documented exit-0 contract on a runner with
no Ollama pulled (A8). I checked this distinction against the actual
architecture text before implementing it: C4 says "no query is served
from a table whose provenance disagrees with the spec" and "doctor exits
3" specifically for that disagreement — not for the provider being
unreachable in general, which is the everyday clean-machine state C5 is
explicitly designed to tolerate ("warn and continue, never fail setup").
Making the *general* degraded flag required would have contradicted C5 in
the same commit that implements it.

`scripts/setup.sh` gained `ollama pull nomic-embed-text` in
`install_services()`, right after the ai-eng persona block, following the
`llama3.2:1b` pattern exactly: idempotent skip if present, warn-and-
continue on failure, placed after every early-return guard already in the
function so it inherits the same `ARAIL_SKIP_OLLAMA`/
`ARAIL_SKIP_MODEL_DOWNLOAD`/daemon-unreachable skip semantics rather than
re-probing them.

3 new tests (`tests/test_doctor_embedding_status.py`); 2 pre-existing
`setup_ladder` idempotency tests were asserting "no PULL of any kind" when
the ai-eng persona was already installed — too broad now that
nomic-embed-text is a second, independent pull in the same ladder — scoped
to the ai-eng-specific pull commands they actually meant to test; 2 new
setup-ladder tests pin the nomic pull itself (pulled when absent, skipped
when present).

## Debt filed this invocation

Two more items required by ARCHITECTURE.md's tech-debt section to be
filed "at build time" (conditional — only apply once the integration
ships, which it now has), added to `sprints/BACKLOG.md`:
- `pkb_provenance.py`'s JSON sidecar is a second-best `content_refs` — to
  be retired if/when the rejected 2.0 consolidated store cutover is
  revisited.
- Two vector spaces in one lab (`pkb_pages` nomic vs.
  `wiki_nodes`/`agent_workflows`/`experiments` hash) — now a recorded,
  provenance-checkable fact rather than a silent inconsistency, with the
  REVIEW.md-corrected framing (hash survives only because those three
  call sites weren't swapped, not because it's competitive) carried into
  the filing.

## debt-finance as a first-class verification target

Per the coordinator's addition: no code in this invocation special-cases
any World by name, tracked-bundle status, or slug. `pkb_reembed.py`,
`index_all`, `_semantic_search`, `ensure_ready`, and the doctor check are
all parameterized purely by `pkb_root: Path` — a World's identity never
enters the logic. This means `debt-finance` is exercised by exactly the
same code every other World is: `./arailctl pkb reembed --world
debt-finance` resolves to `inst_pkb_dir debt-finance` and runs the
identical `pkb_reembed.run()` function used for `ai`/`video-games`/
`qukaizen`. I did not run that command against the operator's real
`debt-finance` data (see boundary note below), but I did verify —
per the W4 measurement already on record in `RESULTS.md` — that
`debt-finance` already scores 100% recall@5 under nomic on its real
corpus, jointly the best of the five Worlds. There is no finding to
report: uniform code path, already-measured strong result. If a future
sprint ever needs debt-finance-specific behaviour (e.g. because its
bundle is untracked), that would be a new, explicit design decision, not
something this integration quietly assumed away.

## What was and wasn't run against real data

Per ARCHITECTURE.md boundary #5 ("no `index_all`, no ingest, no reembed
against the operator's real labs without an explicit operator ask"), no
`pkb reembed`, `index_all`, or `ensure_ready`-triggered rebuild was ever
run in this invocation against `/Users/netsushi/ProJects/qukaizen-arail/
lab/`'s five real Worlds. All functional testing used either pytest's
`tmp_path`-isolated fixtures (the automated test suites) or hand-created
scratch directories under `/tmp` (manual smoke tests of `pkb_reembed.py`'s
CLI and `arailctl`'s bash dispatch, all cleaned up afterward). One
`python -m arail.doctor` invocation was run from this worktree's own
`lab/pkb/` (a worktree-local `.cache/lancedb` this worktree happens to
carry, contradicting ARCHITECTURE.md's A1 assumption that this worktree
"carries no runtime data") — it found a pre-existing 128-dim hash table,
correctly degraded via the new dimension-mismatch path, and **did not
modify it**, which is exactly the safety property C2/FM12 exists to
guarantee. No write occurred.

## Final state (invocation 2, W6–W10)

- Tests added: 13 (W6) + 12 (W7) + 8 (W9) + 3 (W8/W10) = **36 new tests,
  all passing.**
- Combined with invocation 1's 52, this sprint's test suites now carry
  **88 new tests**, all passing as of the last commit.
- Full-suite regression run (after all W6–W10 commits landed):
  **53 failed, 4319 passed, 18 skipped, 3 xfailed, 7 errors, 796s.**
  Failed count matches the predecessor sprint's documented baseline
  exactly (53); passed count rose by 91 (4319 vs the 4228 baseline),
  consistent with this sprint's ~88 new tests (52 invocation-1 + 36
  invocation-2) landing in files pytest already collects. The full list
  of 28 distinct failing files (`test_recap_core`, `test_reset_stop_scope`,
  `test_runtime_profile_api`, `test_shell_source_safety`,
  `test_swarm_goal_surfaces`, `test_world_forge_api`, the aerollm/
  portal-opencode/onboarding/model-ux/dashboard/dac-rename clusters, etc.)
  contains **zero** files this sprint touched (`pkb.py`, `pkb_index.py`,
  `pkb_reembed.py`, `pkb_provenance.py`, `doctor.py`, `dbspec/embed.py`,
  `scripts/setup.sh`, `arailctl`, `scripts/eval/`, `eval/`) — confirmed by
  diffing the failing-file list against `git diff --name-only e1f2ef7..HEAD`.
- 5 commits this invocation: `90b56ce` (required actions 2+3),
  `4af4691` (W6), `5718dc5` (W7), `4a6b726` (W9), `a69ff2a` (W8+W10).
- No production embedder-selection flag exists anywhere (C6 still holds).
- `vector_index.py`, `world_mount.py`, `scripts/start.sh`,
  `src/arail/dbspec/migrate.py`/`reconcile.py`/`repo.py`/`spec.py`, and
  `src/arail/dbspec/generated/*` remain untouched by this sprint (both
  invocations) — confirmed by `git diff --stat e1f2ef7..HEAD` (the
  sprint's own first commit through the current HEAD) against each,
  which is empty. (`git diff --stat 8cb5760` against these same files is
  *not* empty — the dbspec/ layer and its generated modules are new files
  from the predecessor `2026-08-08-arail2-declarative-persistence` sprint,
  landed before baseline `8cb5760` was cut for *this* sprint; that diff
  reflects the predecessor's legitimate work, not anything from this
  sprint.)
- No TODO comments left in any new/modified file. No commented-out code.

---

# Build3: REVIEW2.md BLOCK remediation

**Verdict being remediated:** BLOCK (both findings reproduced by
execution against real code, including the operator's real `debt-finance`
World). See `sprints/2026-08-08-arail2-tier1-integration/REVIEW2.md` for
the full reproduction log (scenarios 1–14).

## BLOCK-1 — degraded state erased by a successful search; C4 not enforced on the read path

**Fixed in commit `44b3981`.**

Root cause: `pkb_index._degraded`/`_degraded_reason` was a single
module-global pair covering five independent facts (provider outage,
dimension mismatch, provenance disagreement, empty index, "nothing wrong
today"), so a successful `embed_query()` call — evidence about the
*provider* only — was unconditionally clearing all five. And the C4
provenance check existed only inside `ensure_ready`, which runs once per
process behind an `_initialized` guard; `_semantic_search` never
consulted it.

Fix:
- Reason-scoped degraded state: `set_degraded(code, reason)` /
  `clear_degraded(code=None)` / new `degraded_codes()`. Every call site
  audited and given the specific code it has evidence about — `_flush`'s
  per-row success now clears only `"provider"`, not the blanket clear it
  had before.
- `pkb_index.check_read_path_health(table, db_path)` — dimension then
  provenance, the one shared implementation `ensure_ready` and
  `pkb._semantic_search` both call, so the two enforcement points can
  never drift apart again. Called on **every** `_semantic_search`
  invocation, not just at startup.
- `doctor.py`'s exit-3 decision now reads `degraded_codes()` (a
  structured set) instead of substring-matching `"provenance"` in a
  prose message — the dimension-mismatch reason didn't contain that
  word, so every one of the operator's five real (128-dim, no-sidecar)
  Worlds was INFO-only and `doctor` exited 0. Both `"dimension"` and
  `"provenance"` are now required (exit 3).

**Boundary amendment taken.** REVIEW2.md relaxed boundary #6 to permit
one additive method on `vector_index.py`: `VectorIndex.search_vector()` +
`VectorSearchError`, with `search()` now delegating to it. This is the
**first and only edit to `vector_index.py` in this entire sprint** — I
took the amendment as specified (one additive method, `search()`'s
existing failsoft contract for `wiki_nodes`/`experiments` preserved
unchanged) and did not use the opening to make any other change to the
file. `pkb._table_search_by_vector` — my own W9 workaround, with its bare
`except Exception: return []` that REVIEW2.md correctly identified as the
mechanism by which the dimension error became silence — is **deleted**,
not kept as debt, per the explicit instruction.

Commit `6e6e0f2` separately wires `pkb.retrieval_status()` into
`/api/pkb/search` (required action 4) via response headers
(`X-Retrieval-Status`/`X-Retrieval-Reason`) rather than a wrapped JSON
body — the endpoint's three frontend consumers
(`dashboard.html`/`agents.html`/`docs_hub.html`) all call
`.then(r => r.json()).then(hits => hits.forEach(...))` on the bare array,
and changing that shape without a matching frontend pass would be a
breaking change I was not going to make inside a BLOCK-remediation
commit. The `/knowledge` banner and Buddy's context-header line — the
other two C1-named surfaces — are **explicitly deferred**, recorded in
`SPRINT.md`'s decisions log and filed in `sprints/BACKLOG.md`, per
REVIEW2's own stated alternative to silence ("wire it, or record the
deferral explicitly... as an accepted C1 gap with a backlog entry").
`/knowledge` is itself now a 307 redirect to `/dac` — the surface moved
since C1 was written, which is part of why I didn't attempt a rushed fix
there.

New tests: `tests/test_block1_read_path_provenance.py` (4, reproducing
REVIEW2's scenarios 1–3 directly as regression tests), 3 more in
`tests/test_c1_error_contract.py` for the reason-scoped primitives
themselves, 1 more in `tests/test_doctor_embedding_status.py` (legacy
128-dim → exit 3), 3 in `tests/test_pkb_search_api_status.py`.

## BLOCK-2 — `pkb reembed` could swap a truncated or empty result in

**Fixed in commit `237e630`.**

Root cause: the shadow build's completeness was never verified against
what the checkpoint (or the corpus scan) expected before the live-table
swap fired.

Fix, matching REVIEW2's four required sub-fixes exactly:
- **(a) Verify before swap.** After the batch loop completes normally,
  the shadow table's actual row count is re-read from LanceDB (not
  trusted from the in-memory `completed_count`) and compared against
  `total`. A mismatch discards the shadow build and checkpoint and raises
  `ShadowBuildIncomplete` — never swaps.
- **(b) `--resume` discards an inconsistent checkpoint.** Before
  resuming, the checkpoint's claimed `completed_paths` count is checked
  against the shadow table's *actual* row count (including "the shadow
  dir doesn't exist at all", which reads as count 0). Any disagreement
  discards the checkpoint and restarts the run from scratch — the
  discard reason is threaded through the result dict and printed by
  `main()`, so the operator sees it rather than a silently "successful"
  resume that only did 38 of what it claimed.
- **(c) Refuse `total == 0` against a live table.** Checked first, before
  any shadow-dir or checkpoint work — a corpus scan of zero rows never
  touches an existing populated index. `total == 0` with *no* existing
  table still succeeds as a no-op (there's nothing to protect, and this
  is a legitimate first-run-on-an-empty-World state).
- **(d) `O_EXCL` lock.** `<pkb_root>/.cache/reembed.lock`, held for the
  write phase only (`--dry-run` never acquires it — it touches no shared
  state). A second concurrent run gets `ReembedLocked` — one sentence —
  instead of racing LanceDB's transaction conflict resolver into the raw
  `lance error: Incompatible transaction … conflict_resolver.rs:855`
  REVIEW2.md's scenario 6 produced.

New tests: 9 in `tests/test_pkb_reembed.py` covering all four sub-fixes,
including a **real two-subprocess race** (`test_two_concurrent_reembed_
processes_one_loses_cleanly`, `@pytest.mark.requires_ollama`) rather than
an in-process thread race — `run()` installs a `SIGINT` handler via
`signal.signal()`, which only works on a process's main thread, so a
thread-based race would hit an unrelated `ValueError` instead of
exercising the lock at all. Discovering this while writing the test is
itself evidence the lock design is sound for its actual (single-process
CLI) usage; the subprocess test is the faithful reproduction of REVIEW2's
scenario 6.

## Required action 6 — non-stubbed guard against a silent hash-vector regression

**Fixed as part of the BLOCK-2 commit** (bundled — the test belongs with
neither block specifically; see the commit message for why).
`tests/test_w9_embedder_swap.py::test_index_all_calls_the_real_embed_
documents_symbol` (`@pytest.mark.requires_ollama`, NOT stubbed) asserts
by identity that `index_all` calls the real
`arail.dbspec.embed.embed_documents` function object, and that the
stored vector differs from what `hash_embedding` would have produced for
the same input — the shape a silent fallback regression would take.

## Debts filed (REVIEW2.md required, before PASS)

All four added to `sprints/BACKLOG.md`:
1. `pkb_index`'s degraded state is a module global against per-World
   roots (survivable today because Worlds run as separate processes; a
   landmine if concurrent-Worlds ever shares one process).
2. `tests/conftest.py`'s suite-wide embedding stub hides a hash-vector
   regression from every test except the one new guard test.
3. `.bak-<ts>` accumulation with no pruning and no `docs/cli.md` mention
   of the accumulation itself (the rollback *use* of `.bak-<ts>` is
   documented; the fact that they pile up was not).
4. C1's `/knowledge` banner / Buddy context-header wiring — the
   explicit-deferral entry described above.

The fifth item REVIEW2.md listed ("a second copy of `VectorIndex.
search`'s post-processing") was **resolved, not filed** — that's
`search_vector()`, per the boundary amendment.

## What I could not / did not do

- Full UI wiring for the `/knowledge` banner and Buddy's context header
  (see above — explicit, documented deferral, not a gap I'm claiming is
  closed).
- Did not extend the "real embedder" guard-test pattern to `_build_row`
  or `pkb_reembed.run`'s own `embed_documents` call sites — filed as part
  of backlog item 2's "what a future sprint could do" rather than done
  here, to keep this remediation pass scoped to what REVIEW2.md actually
  required (one guard test, required action 6, not three).
- Did not implement `.bak-<ts>` pruning (filed as debt, not attempted —
  REVIEW2.md's tech-debt section asked for it to be *filed*, not fixed,
  alongside the other three).

## Regression check

Targeted suites (pkb/pkb_index/pkb_reembed/vector_index/doctor/eval/
dbspec/setup_ladder/cli_verbs — everything touched by any commit in this
sprint): **301 passed**, 0 failed, working tree clean before each commit.

Full-suite regression run (after all three build3 commits landed):
**52 failed, 4341 passed, 18 skipped, 3 xfailed, 7 errors, 798s.**
Verified by set intersection (same method as invocation 2's final check)
that none of the 29 distinct failing files overlap with
`git diff --name-only e1f2ef7..HEAD` (every file this sprint has touched,
across all three invocations) — empty intersection, confirmed.

**Correction (per REVIEW3.md and the coordinator's note): "52 vs 53" is
not evidence of anything and I should not have framed it as an
improvement.** The architect ran the 29 failing files in isolation at
both `8cb5760` and HEAD and got **27 failed + 7 errors in both cases**,
with byte-identical failing-test-ID sets — nothing this sprint touched
makes any of them fail. The full-suite number (52) includes roughly 18
failures that are cross-test pollution / ordering effects predating this
sprint entirely, not a stable count either baseline or this sprint
controls. The per-file isolated comparison is the real regression check
and it is clean; the raw full-suite failure count is noise and I retract
the earlier "52 vs 53, fewer failures" framing.

## Commits this invocation

- `44b3981` — BLOCK-1 fix (reason-scoped degraded state, read-path C4
  enforcement, the `vector_index.py` boundary amendment, deletion of the
  duplicate post-processing).
- `6e6e0f2` — C1 search-payload wiring (partial, with explicit deferral
  for the remaining two surfaces) + the SPRINT.md/BACKLOG.md filings.
- `237e630` — BLOCK-2 fix (shadow-completeness verification, empty-corpus
  refusal, checkpoint/shadow-mismatch discard, the reembed lock) + the
  non-stubbed real-embedder guard test.

---

# Build4: REVIEW3.md BLOCK-3 remediation + "also fix" items

**Verdict being remediated:** BLOCK on a new finding (BLOCK-3), which
REVIEW3.md states plainly is its own miss in REVIEW2, not a build3
regression. Both original blocks (BLOCK-1, BLOCK-2) were confirmed
genuinely dead by the architect re-running every REVIEW2 reproduction
against real code with real Ollama, and the `vector_index.py` change was
ruled fully within the amendment — no narrowing required on either.

## BLOCK-3 — `./arailctl doctor` implicitly embedded and wrote an index

**Fixed in commit `1382c69`.**

Reproduced by the architect, by accident, on the operator's real
`finance` World: `doctor.check_knowledge_base()` called
`pkb_index.ensure_ready()`, whose default `build=True` took the "table
missing" branch and ran a full `embed_documents()` pass plus a table +
provenance write — from a command whose whole contract is "print a
health report". At baseline (`8cb5760`) this path was local
`hash_embedding` and merely impolite; W9 swapped the network embedder in
without revisiting who calls `ensure_ready`, so it became a genuine
corpus-egress path under `LAB_MODE=hybrid` (this development machine's
setting) and an unbounded-cost operation on any populated World.

**Fix:** `ensure_ready(pkb_root, *, build: bool = True)`. Every branch
that would call `index_all()`, drop a table, or run the staleness sweep
(which schedules upserts — an eventual debounced embed call) is gated on
`build`; `build=False` reports the identical degraded status (same codes,
same messages) without mutating anything, and skips even creating
`.cache/lancedb` if it doesn't already exist. `doctor.py` now calls
`ensure_ready(build=False)`.

**Audit of every `ensure_ready()`/`index_all()` call site (grep, 5 total,
stated as required):**

| Call site | Verdict | Why |
|---|---|---|
| `doctor.check_knowledge_base` | **Fixed** — `build=False` | A diagnostic; must never mutate. |
| `portal/app.py`'s `_kb_index_ready()` (portal startup) | Unchanged, `build=True` correct | This IS the one-time startup build the module docstring describes. |
| `portal/app.py` STT note capture | Unchanged, `build=True` correct | Genuine content-write path — a voice note was just written to disk; indexing it is the expected behavior. |
| `portal/app.py` OCR note capture | Unchanged, `build=True` correct | Same reasoning as STT. |
| `world_mount.py`'s `_index_staged()` (World mount) | Unchanged, `build=True` correct | Mounting a World is expected to index its staged content; also on this sprint's protected "must not touch" list regardless. |

No other caller needed a change. This audit is the full answer to "assume
there may be others" — there were three others, and all three are
legitimate write paths, not disguised diagnostics.

**Docstring reconciliation (also required):** `pkb_reembed.py`'s header
claimed to be *"the only path that (re)writes `pkb_pages` with the
spec-declared embedder."* That was false as shipped — the three
`ensure_ready(build=True)` callers above also do. Corrected to state what
`pkb_reembed` actually and uniquely provides: the shadow-build + verified
swap, safe against an already-populated table without a lazy
drop-and-rebuild, plus the explicit statement that diagnostic callers
must use `build=False`.

New tests: 4 in `tests/test_c1_error_contract.py` (`build=False` on
no-index/missing-columns/dim-mismatch/healthy-index, each asserting zero
embed calls and no mutation), 2 in `tests/test_doctor_embedding_status.py`
including the REVIEW3.md-required one (`test_doctor_never_builds_or_
embeds` — zero `embed_documents` calls, no `.cache/lancedb` created).
One pre-existing test (`test_no_ollama_yet_stays_info_only`) was rewritten
as `test_no_index_yet_stays_info_only` — its premise (mocking `index_all`
to raise) can no longer be exercised via `ensure_ready(build=False)`,
which never calls `index_all` in that branch at all; rewritten to assert
the same real invariant via the code path that actually runs now.

## "Also fix" items (commit `8af64a3`)

1. **The `"empty"` code was sticky in-process.** Fixed: `_semantic_search`
   now clears it the moment it observes a non-empty, openable table —
   reaching that point is itself the evidence. New test:
   `test_empty_code_clears_once_the_index_is_populated`.
2. **A stale `reembed.lock` after SIGKILL wedged the recovery verb.**
   Fixed: `_ReembedLock.acquire()` checks the existing lock's PID via
   `os.kill(pid, 0)`; a dead PID or an unreadable/corrupt lock file is
   recovered automatically (removed, retried once); a genuinely live
   process's lock is still refused with the same message as before. New
   tests: dead-PID recovery, corrupt-lock recovery, live-PID-is-NOT-
   recovered (the negative case, so the recovery can't be exploited to
   steal a live lock), plus direct unit tests of the two new helper
   functions (`_pid_alive`, `_read_lock_pid`).
3. **Missing coverage, added without changing behavior** (per the explicit
   instruction — "add the missing coverage", not "fix the underlying
   gap"): `search_vector`'s `VectorSearchError` branch in
   `_semantic_search` was unexercised — now covered
   (`test_semantic_search_vector_search_error_after_health_check_passes_
   degrades`), reproducing a genuine post-health-check backend failure and
   asserting it degrades and falls through to keyword search rather than
   propagating or silently vanishing into `[]`. The shadow-build
   verification's cardinality-only nature is now pinned by
   `test_shadow_verification_is_cardinality_only_documented_limitation`,
   which reproduces the concrete blind spot (a `--resume` checkpoint
   correctly naming real paths as completed, backed by a shadow table at
   those paths carrying a stale, unverified vector, trusted verbatim into
   the swapped-in live table) — filed as debt, not fixed, matching the
   coordinator's explicit scope.

## Deliberately NOT fixed this sprint (per the coordinator's explicit instruction)

- **`VectorIndex._table()` re-opened three times per PKB query** (7.5ms of
  a 20.8ms total query on a 116-row index). Measured, filed in
  `sprints/BACKLOG.md`, not optimized — the coordinator's own words:
  "File it; do not optimize now."
- **The two remaining C1 UI surfaces** (`/knowledge` banner, Buddy's
  context-header line) — already filed in `sprints/BACKLOG.md` from
  build3. Stating the user-visible consequence plainly, as instructed:
  **Buddy is silently keyword-only on four of the operator's five real
  Worlds right now** (every World except whichever one has actually been
  re-embedded), with no honesty line telling the user or the agent that
  retrieval degraded to keyword-only. `X-Retrieval-Status` (wired in
  build3) currently has **zero consumers** — no template or JS reads it —
  so the only place this is visible today is `./arailctl doctor`'s output
  and this sprint's own test suite. This is a real, live gap in what a
  friend cloning this blueprint would experience, not a hypothetical one.

## Regression check

Targeted suites (everything touched by any commit across all four
invocations): **315 passed**, 0 failed, working tree clean before each
commit.

Per the coordinator's explicit correction, I am not running or reporting
a fresh full-suite "N failed" comparison as evidence of anything this
round — REVIEW3.md already established that the full-suite failure count
is dominated by pre-existing cross-test ordering effects (~18 of the
prior run's 52) that neither this sprint nor `8cb5760` controls, and that
the per-file-isolated comparison (byte-identical failing-test-ID sets at
both commits) is the only regression signal that means anything. That
comparison was already run by the architect against build3's diff and
came back clean; build4's changes are confined to `pkb_index.py`,
`pkb.py`, `pkb_reembed.py`, `doctor.py`, and their own test files — the
same files (plus tests) build3 already touched, with the identical
isolated-failure-set property holding by construction (no line in any
pre-existing, non-test production file outside those four was touched).

## Commits this invocation

- `1382c69` — BLOCK-3 fix (`ensure_ready(build=False)`, `doctor.py` uses
  it, the five-call-site audit, the `pkb_reembed.py` docstring
  correction).
- `8af64a3` — the three "also fix" items (empty-code clearing, stale-lock
  recovery, the two missing-coverage tests) + two new debt filings.

---

# Build5: the BLOCK-3 fix's own regression (coordinator finding)

**Not a review-phase finding this time — the coordinator caught it directly
by reproducing it, before sending build4 to re-review.** The mechanism is
exactly what they described: `ensure_ready`'s one-shot guard
(`_initialized`, a single process-global bool) was set unconditionally
near the top of the function, before any `build`-gated branching — so a
`build=False` (read-only) call consumed the same one-shot slot a later
genuine `build=True` call depended on, and the later call silently no-op'd
without building anything.

## Reachability finding (verified by reading the code and grepping every call site, not assumed)

**Not reachable through any currently-shipped call path.** `ensure_ready
(build=False)` has exactly one caller in the entire tree —
`doctor.check_knowledge_base()` — and it is invoked only via a fresh
`python -m arail.doctor` subprocess (`arailctl`'s `doctor)` case execs a
new Python process after activating the venv; there is no in-process call
from the portal into `doctor.py` anywhere — grepped
`src/arail/portal/*.py` for any import of `arail.doctor`, found none).
Module-level globals do not survive across processes, so the exact
sequence the coordinator's reproduction script runs in one process
(`ensure_ready(build=False)` then `ensure_ready(build=True)`) cannot
occur today between doctor and the portal — they are always different
OS processes with independently-fresh module state.

I also checked the more specific worry in the coordinator's message —
that `pkb._semantic_search`'s per-query health check might be an
in-process path into this guard, since it now runs on every query.
`pkb.py` does not call `ensure_ready` at all (confirmed by grep:
zero matches); `_semantic_search` calls `pkb_index.check_read_path_
health()` directly, which never reads or writes `_initialized`/
`_initialized_roots`. So the per-query health check is not a vector into
this bug either.

**This does not mean the bug wasn't real or wasn't worth fixing.** It is
a genuine contract defect in `ensure_ready` itself — the function's own
docstring invites exactly the usage that breaks ("call once at portal
startup, or from any genuine content-write path" — nothing in the stated
contract restricts mixing that with a read-only call), and the next
caller to combine them in-process (an admin diagnostics endpoint that
calls `doctor`'s logic directly instead of shelling out, a future refactor
that merges doctor into the portal process) would hit it silently, with
the exact symptom the coordinator named: new content stops getting
indexed and the user is told to run `pkb reembed` to fix something that
should have just worked. Fixed regardless of today's non-reachability,
per the explicit instruction.

## Fix

`_initialized: bool` → `_initialized_roots: set[Path]`, keyed by each
root's *resolved* path. The guard is now claimed **only** by a `build=True`
call (`if build: ... _initialized_roots.add(root_key)`); a `build=False`
call never reads or writes it, so it always re-executes its own
inspection fresh (cheap and idempotent by design — there was never a
correctness reason to memoize a read-only check across calls, only a
correctness reason to memoize a *build*). This directly satisfies the
stated invariant: `build=False` then `build=True` builds; the reverse
order still works; two different roots in one process don't interfere,
because a genuinely different root has a genuinely different key.
`_pkb_root_cache` (the separate single-slot cache `_flush`/`schedule_
upsert` use to find "the" active root) is unchanged — it is a pre-existing,
already-filed, single-root-per-process assumption for the *debounce*
machinery specifically, not implicated in this regression, and widening
it was not required to satisfy the stated invariant.

**~29 pre-existing tests set `pki._initialized = True` by hand** (a
defensive pattern to bypass `ensure_ready`'s old guard in tests that call
`schedule_upsert`/`_flush` directly, never `ensure_ready` itself). Checked
each: none of them actually depends on `ensure_ready`'s guard for
correctness (neither `_flush` nor `schedule_upsert` ever reads
`_initialized`/`_initialized_roots`), so renaming the attribute leaves
those lines as harmless dead assignments (Python modules accept arbitrary
attribute assignment; `pki._initialized = True` now just creates an
inert, never-read attribute) rather than breaking anything. Verified: all
of them still pass, unmodified.

## The "also re-check" — degraded-code cross-root leak

The coordinator asked me to re-check whether the `"empty"` code clearing
added in build4 could mislabel a different root, given the same class of
bug. It can, but it is a **pre-existing, already-filed** limitation, not
something this fix or the empty-code-clearing change introduced: the
`_degraded_codes` dict (added in W6/BLOCK-1, before `build=False` existed
at all) is process-global, not keyed by root — a search or health check
against root A that sets any code already mislabels a status read for
root B, with or without `build=False` in the picture. I wrote
`test_degraded_empty_code_from_root_a_readonly_check_leaks_into_root_b_
status` in `tests/test_ensure_ready_build_isolation.py` to prove this
concretely rather than leave it asserted, and it reproduces exactly as
expected.

I deliberately did **not** widen `_degraded_codes` to be root-keyed in
this pass, even though the coordinator's message explicitly permitted it
("if the clean fix is to key both on the root, do that and close both").
Reasoning: (1) doing so would ripple `set_degraded`/`clear_degraded`/
`degraded_codes`/`embedding_status` signature changes into every caller
outside `pkb_index.py` — `pkb.py`, `doctor.py`, `pkb_reembed.py` — which
is a materially larger and riskier change than the `_initialized` fix,
under the same time pressure that produced this regression in the first
place; (2) the instruction's own "do not widen beyond `pkb_index.py`'s
state handling" reads most naturally as a caution against exactly that
kind of cross-file ripple; (3) like the `_initialized` bug before this
fix, it is not reachable through any shipped call path today — both real
callers (`doctor`, the portal) only ever handle one root per process; and
(4) it is already filed in `sprints/BACKLOG.md` ("`pkb_index`'s degraded
state is a module global; PKB roots are per-World") with its own "what a
future sprint needs to do" section. I'm flagging this explicitly rather
than silently declining it — if the coordinator wants it done now instead
of filed, that's a one-line instruction away and the test I wrote gives a
concrete before/after to verify against.

## Regression check

Targeted suites (everything touched by any commit across all five
invocations): **322 passed**, 0 failed, working tree clean before the
commit. New: `tests/test_ensure_ready_build_isolation.py` (7 tests) —
required tests 1–4 exactly as specified, plus the same-root-second-call
no-op guard (protects against overcorrecting into "build=True always
re-runs"), the different-root-both-build sanity check, and the
degraded-code cross-root documentation test.

## Commit this invocation

- (this commit) — `_initialized` → `_initialized_roots` (per-root,
  build=True-only guard), `_reset_for_tests` updated, new test file, this
  BUILD_LOG entry.

# Build6: REVIEW4.md WEAK_PASS remediation (ASK-1, ASK-2, documentation)

Two required fixes plus one documentation task, no fifth full review
planned — QA is next.

## ASK-1: `_pkb_root_cache` set outside the `if build:` guard

**Fix.** Moved `_pkb_root_cache = root` inside `ensure_ready`'s
`if build:` block (it was one line below the ORCH-1 per-root guard from
Build5, still unconditional). Confirmed via grep that within
`ensure_ready`, `_pkb_root_cache` is now write-only — the function never
reads it, so there was no read-side behavior to preserve or break by
gating the write.

**Test.** Added three tests to
`tests/test_ensure_ready_build_isolation.py`:
- `test_readonly_call_does_not_redirect_pkb_root_cache` — build A, then
  read-only-check B, assert the cache still points at A.
- `test_readonly_call_on_same_root_does_not_disturb_cache` — plants a
  sentinel object in the cache slot, calls `ensure_ready(root, build=False)`
  on the SAME root, asserts the sentinel is untouched (proves `build=False`
  doesn't even no-op-write its own root's value).
- `test_cross_world_contamination_probe` — QA attack-list item 8, the
  reviewer's own suggested reproduction verbatim: `ensure_ready(A,
  build=True)`, `schedule_upsert` a new file under A with no explicit
  `pkb_root`, `ensure_ready(B, build=False)` in between, `flush_now()`,
  assert the row landed in A's table and that B's `.cache/lancedb` was
  never even created.

`tests/test_ensure_ready_build_isolation.py`: 10 tests (was 7), all
passing.

## ASK-2: PID-heuristic stale-lock recovery → `fcntl.flock`

**Fix.** Deleted `_read_lock_pid`, `_pid_alive`, and `_ReembedLock._is_stale`
entirely from `src/arail/pkb_reembed.py`. `_ReembedLock.acquire()` now
opens the lock file with `os.open(path, O_CREAT | O_RDWR)` and calls
`fcntl.flock(fd, LOCK_EX | LOCK_NB)`; an `OSError` from that call raises
`ReembedLocked` (same exception type, same exit-1/English-message/
no-traceback contract `main()` already provided). PID is still written
into the file after acquiring, but purely for human inspection —
`acquire()`/`release()` never read it back or branch on it.

**Design decision beyond the literal instruction: `release()` no longer
unlinks the lock file.** The reviewer's instruction was "replace the
heuristic with `fcntl.flock`" — it didn't specify whether `release()`
should still unlink. I determined unlinking is actively unsafe once
`flock` is the sole exclusion mechanism: if process A holds the flock and
process B calls `release()` and unlinks the path (e.g. a bug, or a race
during shutdown), a third process C opening the same path with `O_CREAT`
gets a *fresh inode* and can acquire a lock on it immediately — C now
believes it holds "the" lock while A still holds a lock on the old,
orphaned inode. Two processes proceed simultaneously: the exact defect
class (broken mutual exclusion) this whole remediation exists to close.
The fix: never unlink in `release()`; the lock file persists harmlessly
under `.cache/` (already git-ignored, already excluded from PKB indexing)
for the life of the PKB root. Documented in `_ReembedLock`'s class
docstring so a future editor doesn't "fix" this back in.

**Operator-facing behavior preserved, verified:**
- Loser still exits 1 with an English message, no Lance/Rust traceback —
  `ReembedLocked` remains a `RuntimeError` subclass; `main()`'s existing
  exception handling is untouched.
- SIGKILLed holder does not wedge the recovery verb — proven by
  `test_sigkilled_holder_releases_lock_automatically`, a real subprocess
  that acquires the flock, blocks, gets SIGKILLed, and a fresh acquire in
  the test process succeeds within the polling window with zero
  staleness logic involved (the kernel released it).
- PID reuse is no longer a hazard — nothing reads or branches on the PID
  the file contains.

**Test rewrites.** Removed 5 tests that directly exercised the deleted
PID-heuristic API (`test_stale_lock_from_dead_pid_is_recovered_
automatically`, `test_corrupt_lock_file_is_recovered_automatically`,
`test_live_pid_lock_is_not_recovered`, `test_pid_alive_helper`,
`test_read_lock_pid_helper`), replaced with:
- `test_lock_file_with_garbage_content_is_irrelevant_to_flock` — an
  empty/unparseable lock file is not special; a fresh acquire against it
  succeeds immediately (nobody holds the flock on it).
- `test_lock_file_naming_a_pid_that_still_exists_is_not_special` —
  content naming a genuinely live PID (this test process) is likewise
  decorative; a run proceeds normally since nothing flocked the file.
- `test_holding_flock_directly_refuses_a_concurrent_run` — the actual
  mutual-exclusion guarantee: a second fd genuinely holding the flock
  (bypassing `_ReembedLock` to simulate "some other process has it")
  causes `run()` to raise `ReembedLocked`; releasing it lets a normal run
  proceed.
- `test_sigkilled_holder_releases_lock_automatically` — described above.

Also updated `test_lock_released_after_run_completes` and
`test_lock_released_after_run_raises`, which used to assert
`not lock_path.exists()` after `release()`; under the new "never unlink"
design that assertion is now false by design, so both were changed to
assert the file still exists but is no longer *held* (a fresh
`_ReembedLock().acquire()`/`release()` on it succeeds immediately).

`tests/test_pkb_reembed.py`: 26 tests (was net effect: -5 removed, +4
added, 2 rewritten in place — 27 before this round, 26 after; the two
merged single-assertion PID tests became one flock-focused test each
rather than a 1:1 swap). All 26 passing.

## Documentation task: the "one PKB root per process" invariant

Added a "Load-bearing invariant: one PKB root per process" section to
`src/arail/pkb_index.py`'s module docstring, naming the globals it
protects (`_pending`, `_timer`, `_initialized_roots`, `_pkb_root_cache`,
`_degraded_codes`), stating the two facts that make them safe
(`arail.config.PKB_ROOT` is a module constant never rebound in-process;
concurrent Worlds run process-per-World), what breaks if either half is
violated (cross-root degraded-code leaks; the exact `_pkb_root_cache`
hazard ASK-1 just closed, reintroduced by a different cause), and a
pointer to the `sprints/BACKLOG.md` entry.

Added the same invariant, phrased for the conventions list, to this
repo's own `CLAUDE.md` under "Conventions worth knowing" (the ARAIL-repo
CLAUDE.md, not the workspace-root one — this invariant is specific to
`pkb_index.py`'s module-global state and the concurrent-Worlds
process-per-World model, both ARAIL-repo concepts covered by that
section already).

## Regression check

Targeted suites (`test_ensure_ready_build_isolation.py`,
`test_pkb_index.py`, `test_pkb_index_integration.py`,
`test_pkb_index_qa.py`, `test_pkb_index_perf.py`,
`test_c1_error_contract.py`, `test_pkb_reembed.py`): all passing.
Broader `-k "pkb or reembed"` selection across the full `tests/` tree:
**154 passed**, 0 failed.

Full unfiltered suite: `52 failed, 4364 passed, 18 skipped, 3 xfailed,
7 errors` (501s). All 52 failures + 7 errors are in
`test_recap_core.py`, `test_reset_stop_scope.py`,
`test_runtime_profile_api.py`, `test_shell_source_safety.py`,
`test_swarm_goal_surfaces.py`, and `test_world_forge_api.py` — none of
which this round (or any commit in this sprint) touches. Confirmed
pre-existing and not something this round introduced: checked out
baseline `8cb5760` into a scratch worktree and ran the same six files in
isolation — byte-identical failure/error list (10 failed, 33 passed,
7 errors) both there and on this branch. Not investigated further; out
of this round's scope (ASK-1, ASK-2, documentation only) and pre-dates
this entire sprint.

Boundary check (`git diff --stat e1f2ef7..HEAD -- src/arail/world_mount.py
scripts/start.sh scripts/lib/instances.sh src/arail/wiki_vectors.py`)
empty, as required.

## Commit this invocation

- ASK-1 fix (`pkb_index.py` guard move + 3 new tests)
- ASK-2 fix (`pkb_reembed.py` flock rewrite + test rewrites)
- documentation task (`pkb_index.py` docstring + `CLAUDE.md` line)
- this
  BUILD_LOG entry.

# Build7: TEST_REPORT.md FAIL remediation (QA-4/QA-5, QA-6 constraint)

QA committed 72 tests and left five red as the acceptance criterion:
`tests/test_qa_tier1_search_header_safety.py`'s
`test_a_hostile_provider_message_cannot_split_the_response[non-latin1]`
and `test_the_real_degraded_messages_can_actually_be_served` ×4.

## QA-5 (ship-blocker) + QA-4 (shares the call site)

**Fix.** `api_pkb_search` (`src/arail/portal/app.py`) stamped
`X-Retrieval-Reason: reason[:200]` directly. Every degraded reason
`pkb_index`/`pkb` produce contains an em dash (U+2014); Starlette encodes
response headers as latin-1, so the header construction itself raised
`UnicodeEncodeError` and the endpoint 500'd on four of five real Worlds
plus the root lab (legacy dimension), any unbuilt index (empty), and
every clean machine that hasn't pulled `nomic-embed-text` yet (provider).
At `8cb5760` this endpoint was `return pkb_search(q.strip())` and could
not 500 — a straight regression from this sprint's own C1 work.

Added `_header_safe()`: strips CR/LF/NUL first (QA-4 — the same call
site reflects up to 400 bytes of a provider's raw HTTP error body via
`EmbeddingError`, a response-header-injection surface in `LAB_MODE=hybrid`
where the provider is off-box), then ASCII-folds (`encode("ascii",
"replace")`) anything left outside latin-1's safe range, so nothing can
ever reach Starlette's latin-1 header encoder. One call site, one
sanitisation pass, closes both findings.

**The test-design fix (the more valuable half, per QA's framing).**
`tests/test_pkb_search_api_status.py` missed this because its fixture
reason was a hand-written ASCII string the product never emits.
Rewrote `test_the_real_degraded_messages_can_actually_be_served` in
`tests/test_qa_tier1_search_header_safety.py` to *trigger the real code
paths* instead of hand-copying reason text: a real legacy 128-dim table
with no sidecar (dimension mismatch via `pkb.search`'s real
`check_read_path_health` call), a real 768-dim table with no provenance
sidecar (`provenance-missing`), a real from-scratch root with no index at
all inspected via `ensure_ready(build=False)` (the doctor/diagnostic
"empty" message) and via `pkb.search` itself (the search-path "empty"
message — a different call site, different string). Each scenario reads
the real reason back through `pkb.retrieval_status()` rather than a
literal, and asserts it still contains an em dash (the hazard this test
exists to exercise) before feeding it through `api_pkb_search`. This
class of test can no longer silently drift out of sync with what
`pkb_index`/`pkb` actually say.

Also removed the inline `pytest.xfail(...)` branch that was marking the
CRLF/LF/CR/NUL parameters of
`test_a_hostile_provider_message_cannot_split_the_response` as expected
failures (QA-4) — the same `_header_safe()` fix closes those too.

**Result:** `tests/test_qa_tier1_search_header_safety.py`: 11 tests, all
passing (was 5 pass / 4 xfail(inline) / 2 fail... — net: the 5 previously
red tests plus the 4 previously-inline-xfailed CRLF/LF/CR/NUL parameters
are now green, nothing newly skipped).

## QA-6 — not fixed, per explicit instruction; BACKLOG constraint recorded

Pre-existing (Phase 1 audit A36, `compiled_kb.py:109` failing closed with
the gate on by default): `search_for_agents`/`pkb.search(approved_only=
True)` returns `[]` before `_semantic_search` ever runs when nothing is
approved yet (all six real PKB roots today), so no degraded code is ever
set and `retrieval_status()` reports `(True, "")` — healthy — while Buddy
gets zero hits. Out of scope; not touched.

Updated the "C1's `/knowledge` banner and Buddy context-header wiring —
deferred" entry in `sprints/BACKLOG.md` to record the hard constraint QA
surfaced: the deferred Buddy context-header surface must NOT be built as
a naive `retrieval_status()` read, or it will print a false "retrieval
healthy" right next to zero results. Whoever builds it must first make
the agent path consult the gate/approved-count state. Points at
`test_agent_retrieval_returns_nothing_and_reports_healthy_on_a_legacy_world`,
which already pins the hazard.

## `_table()` latency — BACKLOG correction, not a fix

Per the coordinator's explicit instruction, did not optimise
`VectorIndex._table()`. Corrected the BACKLOG entry's measurement: QA
measured the real call directly at **0.39–1.21 ms**, not REVIEW3's
original 7.5 ms estimate (roughly an order of magnitude lower) — the
triple-open shape is still real and still filed, but at a materially
lower urgency than the original framing implied.

## Regression check

`tests/test_qa_tier1_search_header_safety.py` (11), plus the rest of the
sprint's QA suite (`test_pkb_search_api_status.py`,
`test_qa_tier1_buddy_retrieval.py`, `test_qa_tier1_egress_guard.py`,
`test_qa_tier1_reembed_robustness.py`, `test_qa_tier1_clean_machine.py`,
`tests/setup_ladder/test_setup_ladder_qa_nomic.py`): **72 passed, 3
xfailed** (QA-1/QA-2/QA-3, `xfail(strict=True)`, unaffected by this round
and out of scope), 0 failed. Broader `-k "qa_tier1 or pkb or reembed"`
across the full `tests/` tree: 215 passed, 3 xfailed, 0 failed.

Boundary check (`git diff --stat e1f2ef7..HEAD -- src/arail/world_mount.py
scripts/start.sh scripts/lib/instances.sh src/arail/wiki_vectors.py`)
empty, as required.

## Commit this invocation

- fix (`app.py` `_header_safe` + test rewrite for QA-4/QA-5)
- docs (`BACKLOG.md`: QA-6 constraint + `_table()` correction)
- this BUILD_LOG entry.

# Build8: QA round 2 WEAK_PASS — last pass, QA-1/QA-2/QA-3/QA-7

QA round 2 confirmed QA-5 genuinely dead (200 in all five degraded states
against scratch copies of real Worlds) and the rewritten test honest
(four independent mutations turn it red). One item left: file or fix
QA-1, QA-2, QA-3, QA-7. Fixed all four; this is the last build pass.

## QA-7 — `_header_safe` denylist → allowlist

**Fix.** The QA-4/QA-5 fix denylisted CR/LF/NUL and ASCII-folded above
0x7f. Every other C0 control (VT, FF, ESC, BEL, ...) passed through
unchanged — not inert: `h11` rejects VT/FF outright, uvicorn's
httptools transport rejects 29 of the 31, the identical 500 QA-5 was,
through the identical route (up to 400 bytes of a provider's raw HTTP
error body, off-box in `LAB_MODE=hybrid`). Switched `_header_safe` to an
**allowlist** (printable ASCII `0x20`–`0x7e`, plus tab and the space a
folded line break leaves behind) — closes the whole class rather than
another denylist entry.

**Also folded in, per instruction, non-blocking:** em dash → `--` before
filtering, so the common degraded-reason prose reads naturally instead
of just losing the punctuation to the filter; and line breaks now become
a space rather than vanishing outright, so the multi-line `provider`
message no longer runs words together at the joins (`...refused).Start
it with:...` → `...refused). Start it with:...`). Order: line-break →
space, then em dash → `--`, then allowlist-filter — the allowlist step
is what actually guarantees safety; the first two are readability-only
and run before it so their output survives the filter.

Dropped the `xfail(strict=True)` marker on
`test_other_control_characters_are_also_removed` (5 params) now that the
fix closes it.

## QA-1 — same-second backup name collision

**Fix.** `pkb_pages.lance.bak-<int(time.time())>` is second-resolution;
two reembeds completing in the same wall-clock second computed the same
backup name and the second `os.replace()` raised a bare
`OSError(ENOTEMPTY)` past every handler in `main()`. The backup-naming
step now loops an incrementing suffix until it finds a name nothing is
using, rather than assuming the timestamp alone is unique. (The test
does not expect the second run to raise at all — a normal same-second
re-run must simply succeed, which this now does.)

## QA-2 — unwritable `.cache` raised a bare `PermissionError`

**Fix.** Added `ReembedIOError(RuntimeError)`. `run()`'s call into the
locked write phase (`_run_locked`) is now wrapped in `except OSError as
e: raise ReembedIOError(...) from e` with a message naming the directory
to check and the exact recovery command
(`./arailctl pkb reembed`). `ReembedIOError` is a `RuntimeError`
subclass, so `main()`'s existing `except RuntimeError as e: print(f"error:
{e}", ...); return 1` already reports it the same actionable way as every
other failure mode in this verb — no change needed there. Verified this
doesn't shadow the four more-specific RuntimeError subclasses
(`CheckpointSpecMismatch`, `ShadowBuildIncomplete`, `EmptyCorpusRefused`,
`ReembedLocked`): none of those is ever raised as an `OSError`, so the
`except OSError` clause only ever catches genuine filesystem failures.

## QA-3 — empty-corpus sidecar with no table

**Fix.** An empty corpus with no pre-existing live table never creates
`table` in the batch loop (nothing to iterate) and never populates
`shadow_table`, so there was nothing to swap into the live dir — but
`pkb_provenance.write()` ran unconditionally regardless, leaving a
sidecar describing a table that was never written. Tracked
`live_table_exists` through both the backup-and-swap step and the
shadow-swap step, and gated the provenance write on it actually being
`True` afterward.

## Zero strict-xfail surprises

Dropped all four markers (`test_two_reembeds_completing_in_the_same_
second_do_not_crash`, `test_reembed_on_an_unwritable_cache_reports_
actionably`, `test_empty_corpus_does_not_leave_a_sidecar_without_a_table`,
`test_other_control_characters_are_also_removed` ×5 params) at the same
time as their fixes, in the same commits — none left behind to XPASS-fail
on QA's re-run.

## Regression check

`tests/test_qa_tier1_reembed_robustness.py` + `tests/test_qa_tier1_
search_header_safety.py`: **31 passed**, 0 xfailed, 0 failed (was 26 + 11
inc. 3 + 5 xfail entries — every one of them now runs as a plain pass).
Broader `-k "qa_tier1 or pkb or reembed"` across the full `tests/` tree:
**224 passed**, 0 xfailed, 0 failed (up from 215 passed / 3 xfailed —
exactly the three QA markers dropped). Full unfiltered suite pending at
time of writing (background job; final total to be confirmed before
handoff, expected to match Build6/Build7's pre-existing 52F/7E in the
same six unrelated files, now with 4364+ passed given the +9 tests
converted from xfail/fail to plain pass across this and the prior round).

Boundary check (`git diff --stat e1f2ef7..HEAD -- src/arail/world_mount.py
scripts/start.sh scripts/lib/instances.sh src/arail/wiki_vectors.py`)
empty, as required.

## Commit this invocation

- fix (`app.py` `_header_safe` allowlist + em-dash/line-break readability
  + QA-7 test)
- fix (`pkb_reembed.py` `ReembedIOError` + backup-name loop + sidecar
  gating + QA-1/QA-2/QA-3 tests)
- this BUILD_LOG entry.
