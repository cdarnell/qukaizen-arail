# Retrieval A/B results

- git sha: `9362acdb5c8d43dd3557701a86cd952afcda5890`
- embedding model: `nomic-embed-text` (768d)
- LanceDB: `0.30.2`
- corpus manifest sha256: `034940c342fb98264ad99cf1e3b747ecf729db84c91a916cc15cb6fcd1851208`
- queries.yaml sha256: `546bf567ef1484bd12cbceb88b165ed7f393981d0563a1dec12b5e00b1975a96`
- exact_tokens.yaml sha256: `a659be0c54eba2d061bf314491ae17841b4925629d449d4252049f9d629dca8e`
- row counts: {'root': 37, 'ai': 381, 'video-games': 318, 'debt-finance': 82, 'qukaizen': 71}

## Pooled recall@5
- hash: 50.0%
- nomic: 90.6%
- **delta (nomic - hash): 40.6pp**
- 95% bootstrap CI on delta: [25.0, 56.2]pp (seed 20260808, 10000 resamples)
- **verdict: PASS**

## Per-world recall@5
### hash
- root: 57.1%
- ai: 28.6%
- video-games: 33.3%
- debt-finance: 66.7%
- qukaizen: 66.7%
### nomic
- root: 100.0%
- ai: 85.7%
- video-games: 83.3%
- debt-finance: 100.0%
- qukaizen: 83.3%

## Pooled MRR@10
- hash: 0.428
- nomic: 0.718

## Overlap strata
counts: {'zero': 8, 'low': 3, 'high': 21}
- zero: hash=0.0%, nomic=62.5%
- low: hash=66.7%, nomic=100.0%
- high: hash=66.7%, nomic=100.0%

## Exact-token rank-1
- hash: 1/10
- nomic: 9/10

## Throughput
- hash/root: 37 rows, 7214.9 rows/s, wall 0.01s, batch p50=0.00s p95=0.00s
- hash/ai: 381 rows, 17893.9 rows/s, wall 0.02s, batch p50=0.00s p95=0.00s
- hash/video-games: 318 rows, 9458.5 rows/s, wall 0.03s, batch p50=0.00s p95=0.00s
- hash/debt-finance: 82 rows, 13188.6 rows/s, wall 0.01s, batch p50=0.00s p95=0.00s
- hash/qukaizen: 71 rows, 11375.5 rows/s, wall 0.01s, batch p50=0.00s p95=0.00s
- nomic/root: 37 rows, 57.1 rows/s, wall 0.65s, batch p50=0.57s p95=0.57s
- nomic/ai: 381 rows, 133.6 rows/s, wall 2.85s, batch p50=0.22s p95=0.43s
- nomic/video-games: 318 rows, 75.6 rows/s, wall 4.21s, batch p50=0.43s p95=0.85s
- nomic/debt-finance: 82 rows, 104.0 rows/s, wall 0.79s, batch p50=0.25s p95=0.40s
- nomic/qukaizen: 71 rows, 98.2 rows/s, wall 0.72s, batch p50=0.25s p95=0.40s

## Exact-token collision diagnostic (appended, not harness-generated)

*Hand-appended per REVIEW.md's required action 3. Everything above this
line is emitted by `scripts/eval/retrieval_ab.py --md`; re-running the
harness will not reproduce or overwrite this section — if you regenerate
this file, re-paste it back in.*

`hash_embedding` scored only 1/10 on exact-token rank-1, contradicting
ARCHITECTURE.md's stated assumption that literal-token lookup is "the
class where lexical hashing legitimately wins." REVIEW.md determined why:
this is a genuine 128-dimension SHA1 bucket-collision failure, not a
fixture defect. For all 10 exact-token queries the literal query token(s)
were confirmed present in the expected document's embedded text
(`literal_in_doc=True`, 10/10 — these are real lexical lookups). Diagnostic
run over the live corpus, hash arm, rank of the expected document:

| query | non-zero buckets in query vector | rank of expected doc |
|---|---|---|
| `LAB_BUDDY` | 1 | 51 / 381 |
| `LAB_SRE` | 1 | 19 / 381 |
| `2026-08-07_c83e0a76` | 1 | 12 / 318 |
| `blended-apr-calc` | 1 | 11 / 82 |
| `LAB_LIBRARIAN` | 1 | 11 / 71 |
| `falsify-hypothesis` | 1 | 8 / 37 |
| `…/how-debt-snowball-method-works` | 4 | 8 / 82 |
| `https://github.com/lyogavin/airllm` | 3 | 3 / 37 |
| `wisdom-per-watt` | 1 | 4 / 71 |
| `dlss` | 1 | 1 / 318 (the only hit) |

**Mechanism.** 8 of 10 queries tokenize to a single token, so the query
vector is one signed spike in 1 of the 128 hash buckets, and ranking is
decided entirely by that bucket's signed weight in each document — a
weight every other document also populates from unrelated colliding
tokens. In the `ai` world alone, 46 distinct corpus tokens collide into
`LAB_BUDDY`'s bucket (`fp16`, `when_to_use`, `preference`, `probability`,
…). A 128-dimension SHA1 projection cannot do reliable literal lookup on a
corpus of this size.

**Corrected conclusion.** `hash_embedding` has **no** stratum measured in
this sprint — not high-overlap, not low-overlap, not zero-overlap, and not
the exact-token class it was assumed to win — where it is the better
retriever on this corpus. The "preserve `hash_embedding` for exact-token
lookup" argument that motivated F2's design is measured false and should
be dropped from any future design doc. `hash_embedding` remains in the
tree only because `wiki_nodes`, `agent_workflows`, and `experiments`
(A5) still use it for unrelated tables — not because it retains any
retrieval-quality advantage anywhere this sprint measured.
