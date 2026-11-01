# ARAIL 2.0 — Phase 1 Audit Report

**Date:** 2026-08-08
**Branch:** `qukaizen/arail-2-declarative-persistence-819030`
**Scope:** Read-only audit per the Phase 1 brief. The only mutation performed was installing Atlas.
**Data root audited:** `/Users/netsushi/ProJects/qukaizen-arail/lab/` (the live checkout; this worktree carries no runtime data).

---

## 0. TL;DR

1. **Atlas installed:** `/opt/homebrew/bin/atlas`, version **v1.3.1-7257eec-canary**.
2. **There is no SQLite database anywhere** — persistence is Lance + JSON files + directory layout. Spec bootstrap via `atlas schema inspect` does not apply; Phase 2 HCL will be authored fresh.
3. **There is no embedding model.** All 16 Lance datasets store 128-dim **SHA1 token-hash projections** (`wiki_vectors.py:22`, `vector_index.py:34`), deterministic and model-free. Hypothesis (b) is structurally impossible in 1.x; Phase 3 "re-embed" is in fact "embed for the first time" — for **every** row in every table.
4. **There is no user.** No `user_id`, no auth, no tenant column in any table. The multi-tenancy unit is the World instance = one OS process with env-frozen data dirs. "Per user" in this report therefore means **per instance**.
5. **Farm bug root cause is (d) — a positional fallback — confirmed with two contributing contamination paths.** Primary: `infer_domain()` at `src/arail/skills/goal_parser/__init__.py:42-49` tie-breaks to `"farming"` because it is dict-key #1 and `max()` returns the first maximal element; its keyword list (`yield`, `crop`, `harvest`, `garden`, `corn`) collides with everyday finance/AI/games phrasing. Hypotheses (a), (b), (c) are all ruled out by direct measurement (§5).
6. **Index health:** zero ANN indexes exist (every search is an exact flat scan), and version retention is unbounded — worst case `instances/ai/pkb_pages`: **2,472 versions, 2,421 fragments, 56.7 MB on disk for 0.22 MB live (253×)**. Nothing ever calls compaction/cleanup.

---

## 1. Atlas

```
$ brew install ariga/tap/atlas
$ /opt/homebrew/bin/atlas version
atlas version v1.3.1-7257eec-canary
```

Binary confirmed at `/opt/homebrew/bin/atlas`.

---

## 2. Persistence inventory

### 2.1 What exists

Three table families, replicated per data root (root lab + one per World instance), plus one experiments table:

| Table | Schema (PyArrow) | Written by |
|---|---|---|
| `pkb_pages` | `path: string, name: string, vector: fsl<float>[128], mtime: double, source_kind: string` | `src/arail/pkb.py:519-527`, `src/arail/pkb_index.py` |
| `wiki_nodes` | `slug: string, section: string, title: string, vector: fsl<float>[128]` | `src/arail/wiki_vectors.py:81-88` |
| `agent_workflows` | `agent_id, status, objective, current_task, next_step, pause_reason, updated_at, summary: string, vector: fsl<float>[128]` | `src/arail/agent_workflows.py` |
| `experiments` | `id, domain, status: string, vector: fsl<float>[128]` | `src/arail/skills/experiment_tracker/` |

**No table carries a world, tenant, or user column.** Scoping is purely which directory the process was pointed at.

### 2.2 Full dataset census (per instance = the per-"user" breakdown)

All measurements taken live, read-only, 2026-08-08. `frags` = data files in the dataset (proxy for fragments; verified 1:1 for these tables). `live` = Arrow bytes of current rows.

| Dataset (under `lab/`) | rows | frags | versions | disk MB | live MB | bloat |
|---|--:|--:|--:|--:|--:|--:|
| `data/lance/agent_workflows.lance` (root) | 2 | 5 | 5 | 0.03 | 0.002 | 15× |
| `pkb/.cache/lancedb/pkb_pages.lance` (root) | 72 | 2 | 2 | 0.05 | 0.042 | 1× |
| `pkb/.wiki-cache/lancedb/wiki_nodes.lance` (root) | 33 | 1 | 1 | 0.02 | 0.020 | 1× |
| `instances/ai/data/lance/agent_workflows.lance` | 2 | **276** | 276 | 1.71 | 0.002 | 855× |
| `instances/ai/pkb/.cache/lancedb/pkb_pages.lance` | 381 | **2,421** | **2,472** | **56.74** | 0.224 | **253×** |
| `instances/ai/pkb/.wiki-cache/lancedb/wiki_nodes.lance` | 377 | 7 | 7 | 1.54 | 0.219 | 7× |
| `instances/debt-finance/data/lance/agent_workflows.lance` | 2 | 150 | 150 | 0.95 | 0.002 | 475× |
| `instances/debt-finance/pkb/.cache/lancedb/pkb_pages.lance` | 79 | 84 | 121 | 0.61 | 0.047 | 13× |
| `instances/debt-finance/pkb/.wiki-cache/lancedb/wiki_nodes.lance` | 78 | 2 | 2 | 0.09 | 0.047 | 2× |
| `instances/qukaizen/data/lance/agent_workflows.lance` | 2 | 6 | 6 | 0.04 | 0.002 | 20× |
| `instances/qukaizen/pkb/.cache/lancedb/pkb_pages.lance` | 68 | 36 | 74 | 0.31 | 0.040 | 8× |
| `instances/qukaizen/pkb/.wiki-cache/lancedb/wiki_nodes.lance` | 67 | 1 | 1 | 0.04 | 0.039 | 1× |
| `instances/video-games/data/lance/agent_workflows.lance` | 19 | 107 | 107 | 1.69 | 0.016 | 106× |
| `instances/video-games/data/experiments/.cache/lancedb/experiments.lance` | 5 | 21 | 21 | 0.10 | 0.003 | 33× |
| `instances/video-games/pkb/.cache/lancedb/pkb_pages.lance` | 116 | 159 | 197 | 1.40 | 0.069 | 20× |
| `instances/video-games/pkb/.wiki-cache/lancedb/wiki_nodes.lance` | 314 | 3 | 3 | 0.31 | 0.185 | 2× |

**Schema/dimension drift between instances: none.** Every dataset of a given family has the byte-identical schema; every vector column is `fixed_size_list<float>[128]`; every stored vector is exactly 128-dim (verified per-row, all 1,617 vectors scanned).

**Embedding provenance: none recorded.** No `embedding_model` / dim metadata column exists anywhere — nothing in 1.x records how a vector was produced. The `content_refs` drift detector planned for Phase 2 has no 1.x counterpart at all.

### 2.3 The "embeddings" are hash projections, not model embeddings

- `src/arail/vector_index.py:9` — "Embedding is a deterministic SHA1-based hash projection (128-dim)".
- `src/arail/wiki_vectors.py:22-34` (`_hash_embedding`) and `src/arail/vector_index.py:34-57` (`hash_embedding`) — tokens are SHA1-hashed into 128 buckets and L2-normalized: a hashed bag-of-words.

Consequences:
- Retrieval is lexical-overlap similarity, not semantic similarity. Low-dimension token collisions with a tiny corpus make off-topic top-k hits routine.
- Phase 3's re-embed step is a **full first-time embedding of the entire corpus** (1,617 vectors today — trivially small), plus choosing an actual embedding model, which the spec's global-version rule then freezes.

### 2.4 Stale/orphan artifacts (migration inputs)

- `lab/instances/finance/` — half-created instance: agents scaffold only, no Lance data, no `instance.env` registry entry. Abandoned; a migration should archive or delete it explicitly.
- `lab/instances/registry.d/` contains only `ai.json`, whose PIDs date to 2026-08-06 (stale). Four instance dirs exist; one registry record.
- `lab/data/` has **no** `world-mount.json` — the root lab is currently unmounted.
- `lab/instances/last-target.json` = `{kind: "root"}`.

---

## 3. LanceDB index health

### 3.1 Vector indexes

**No dataset has any index — vector or scalar.** `list_indices()` returns `[]` for all 16 datasets. Every `pkb.search()` / wiki lookup is a brute-force flat scan over ≤381 rows.

- vs the 100-fragment rule: 4 datasets exceed 100 fragments (`ai/pkb_pages` 2,421; `ai/agent_workflows` 276; `debt-finance/agent_workflows` 150; `video-games/pkb_pages` 159; `video-games/agent_workflows` 107) — but the cost lands on scan latency and file-handle churn, not recall.
- **Unindexed-row count is vacuously "all rows" everywhere.** With no ANN index there is no indexed/unindexed split and no recall degradation mechanism. (This matters for the bug diagnosis — see §5a.)

### 3.2 Version retention

Nothing in `src/arail/` ever calls `compact_files()`, `cleanup_old_versions()`, or `optimize()`. Every write since instance creation is retained:

- Worst: `instances/ai/pkb_pages` — 2,472 versions, 56.74 MB disk / 0.224 MB live.
- Pattern: `agent_workflows` tables hold 2 rows but accumulate hundreds of versions (heartbeat-style upserts, e.g. `ai`: 276 versions for 2 rows).
- Total across the lab: ~68 MB disk for ~0.96 MB live data (**~70× amplification**). Absolute numbers are small today; the growth is unbounded and write-frequency-driven, exactly what `arail db optimize` in the Phase 2 spec exists to fix.

---

## 4. World resolution trace

Full trace with every fallback follows in **Appendix A (60 numbered findings, each with path:line)**. The structural summary:

```
./arailctl start ──► TARGET_SLUG ladder (scripts/start.sh:392-533)
   --root → root · --world <slug> → slug · 0 worlds → root
   1 world → catalog[0]                       ◄ POSITIONAL (start.sh:417)
   --yes → last-target.json, else root · non-tty → exit 2 · tty → picker
        │  catalog order = display_name alphabetical (world_mount.py:779)
        ▼
instance.env freezes ARAIL_DATA_DIR / LAB_PKB / … per slug (instances.sh)
        ▼
portal process: arail.config reads env ONCE at import (config.py:83-90)
        ▼
world_mount.current_mount() reads $DATA_DIR/world-mount.json
   — the ONLY "which world" switch; corrupt/missing → silently None
        ▼
DATA READ PATH: pkb.search()/search_for_agents() → VectorIndex("pkb_pages")
   *** NO WORLD SCOPING — no world column, `where=` never passed ***
   scoping happens only by rm -rf in _sweep_other_worlds (world_mount.py:1407)
```

Highest-severity items (all in Appendix A):

| # | Path:line | What |
|---|---|---|
| A10 | `scripts/start.sh:417` | `catalog[0]` positional world pick when exactly one world exists |
| A8 | `src/arail/world_mount.py:779` | catalog sorted alphabetically by display name — the order every positional consumer inherits |
| A1 | `src/arail/world_mount.py:684-691` | corrupt `world-mount.json` silently → `None` (process degrades to "no world") |
| A2 | `src/arail/world_mount.py:679-682` | missing `ARAIL_DATA_DIR` in a child process → falls back to the **root** lab's data dir |
| A4 | `src/arail/config.py:26-31` | `.env` walk-up search can escape the checkout and repoint the tenant roots |
| A25/A26 | `src/arail/world_mount.py:1407,1587` | world scoping = physical deletion; default `unmount(remove_staged=False)` leaves the old world's rows searchable |
| A31/A32 | `src/arail/vector_index.py:171` / `pkb.py:519-527` | `where=` predicate exists but is never passed; no world column exists to filter on |
| A54 | `src/arail/build/world_corpus.py:38-40,153` | hardcoded **photography** category tuple is the default corpus scope for every world |
| A57 | `src/arail/skills/goal_parser/__init__.py:42-49` | `max()` over dict → first-key tie-break → `"farming"` (the bug, §5) |

**User model:** there is none. `app.py:636` — "The portal has no auth: loopback is the trust boundary." No table has an identity column; `source_kind == "user"` (`pkb.py:433`) is provenance ("human-authored"), not identity; the registry `token` is explicitly not auth (`app.py:3397-3402`). The tenant is the instance process. Phase 2's `user_id` column will be a **new concept**, not a migration of an existing one — for migration purposes, `user_id` maps 1:1 from instance slug.

---

## 5. Diagnosis: "farm world always wins"

First, a framing correction the evidence forces: **there is no farm world.** No bundle or instance named farm/peanut exists (`lab/worlds/`: ai, debt-finance, qukaizen, video-games; `examples/peanut_farmer/` is inert — never imported, never seeded). What "wins" is the farming **domain label and farm-flavored content**, injected by three mechanisms below. Hypotheses tested in the ordered sequence from the brief:

### (a) Vector index degradation / recall collapse — **RULED OUT**

No dataset has a vector index (§3.1), so there are no unindexed rows and no ANN recall to collapse; every search is an exact flat scan. Measured directly on all 16 datasets.

### (b) Embedding-model or dimension mismatch — **RULED OUT**

There is no embedding model to mismatch (§2.3): every vector in every instance is produced by the same deterministic SHA1 hash projection, and query vectors go through the identical function. All 1,617 stored vectors are exactly 128-dim; zero drift between instances.

### (c) Degenerate vectors — **RULED OUT**

Per-row scan of every vector column in every dataset: **0 zero vectors, 0 NaN vectors, 0 wrong-dimension vectors, 0 nulls** out of 1,617.

### (d) Hardcoded or positional fallback in resolution code — **CONFIRMED (primary cause)**

`src/arail/skills/goal_parser/__init__.py:42-49`:

```python
best = max(scores, key=scores.get, default="general")
return best if scores.get(best, 0) > 0 else "general"
```

- `DOMAIN_KEYWORDS` (`__init__.py:22-39`) lists `"farming"` **first**, and Python's `max()` returns the first maximal key — every tie resolves to farming.
- The farming keyword list is the most collision-prone in the file: `yield`, `crop`, `harvest`, `garden`, `corn`, `farm`. Substring matching makes it worse ("corn" ⊂ "cornerstone"). A debt-finance goal containing "yield" (yield curve, high-yield) scores farming=1, business=0 → domain = `"farming"` outright — no tie needed.
- This heuristic path fires on **every** LLM-parse failure or timeout (`__init__.py:116-118`), so a machine whose local parse model OOMs gets the heuristic — and its farming bias — on every goal. That is exactly a "one affected user, always" signature.

The `"farming"` string then drives visible behavior: curator source proposals (`agents/curator.py:107-110` — agriculture/weather/soil), browser suggested sources (`agents/browser.py:519-528` — USDA/NOAA), resource hints (`goal_parser/__init__.py:213` — "soil data, crop databases"), Buddy's metric suggestion (`agents/_builtin_buddy.py:839` — "yield per input cost ratio"), and the Researcher's agricultural system prompt (`agents/researcher.py:41-55`). To an operator this reads precisely as "the farm world always wins."

**Contributing cause 1 — repo docs contaminate every instance's index.** `docgen.generate_all()` (`src/arail/docgen.py:460-467`) renders repo docs into `{pkb_root}/compiled/docs/`, and `pkb.index_all` (`pkb.py:513-527`, `include_docs=True` default) indexes them with no world scoping. Verified on disk: 8 farm/peanut-bearing generated pages inside the **video-games** instance's PKB (incl. the full farming system prompt in `arail-agents-researcher.md` and "PeanutLab" in `arail-brand.md`), 3 inside **ai**'s. `_sweep_other_worlds` (`world_mount.py:1407`) never touches `compiled/`, so they are permanent, and `GET /api/pkb/search` (`app.py:11223`) serves them ungated. With 128-dim hashed bag-of-words vectors over a ≤381-row corpus, a keyword-list page like `env-vars.md` ("ai | farming | ml | …") is a plausible top-k hit for many queries.

**Contributing cause 2 — cross-world term contamination in shipped bundles.** `lab/worlds/ai/terms.json` and `lab/worlds/qukaizen/terms.json` both ship the `tice` term whose example is about "a farmer's unwritten rule of thumb about soil timing"; it is staged and indexed as `sources/world-<slug>/terms/tice.md` in both instances. A "farm" hit inside the *ai* world is thus a legitimate index hit on shipped content. The librarian scout additionally mines `compiled/docs/` (`librarian_scout.py:131-140`), which is how farming vocabulary shows up in `librarian-scout.json` evidence excerpts across ai, debt-finance, and video-games.

**Phase 2 mapping:** (d)-primary is exactly what the generated resolver kills (explicit id/slug only, no fallback branch); the contamination paths are what `content_refs` + world-scoped queries kill; the heuristic's dict-order tie-break must simply be deleted along with the heuristic (the generated registry/resolver replaces it, and domain inference — if kept — needs deterministic, bias-free tie handling).

---

## 6. SQLite bootstrap check

Searched the entire checkout and `lab/` tree: **no `.db`/`.sqlite` files, no `sqlite` imports in `src/arail/`**. (`compose/open-notebook/` uses SurrealDB — out of scope.) The Phase 1 brief's conditional — bootstrap the spec from `atlas schema inspect` — is therefore **inapplicable**. Phase 2 authors `spec/schema/` HCL fresh; migration state starts from an empty baseline, which is the cleaner position anyway.

---

## 7. Phase 2 implications (informational, no action taken)

- **`user_id` mapping:** instances → users is the only sane migration mapping (slug = user id). Flag: the abandoned `instances/finance/` and the registered-but-dead `ai.json` record need explicit disposition during migration.
- **First embedding, not re-embedding:** Phase 3's "re-embed mismatched content" is 100% of rows; a real embedding model must be chosen and pinned in `spec/models/` before the reconciler can declare dims.
- **`arail db optimize` has immediate real work:** 2,472 retained versions on one table today.
- **The resolver spec's "no fallback branch" directly retires findings A1, A2, A10, A11, A12, A57** (Appendix A).
- The 8B answering-model ceiling is unaffected by anything found here (no model registry exists in the persistence layer today; `lab/data/model_registry.json` is runtime state and will be superseded by the generated registry).

---

## Appendix A — every fallback / default / positional lookup / except-and-continue on the resolution path

(60 findings; full paths under `/Users/netsushi/ProJects/qukaizen-arail/`.)

**Mount-pointer resolution**

1. `src/arail/world_mount.py:684-691` — `current_mount()` swallows any JSON/parse error on `world-mount.json`, returns `None`; corrupt pointer silently degrades to "no world mounted".
2. `src/arail/world_mount.py:679-682` — `data_dir or _default_data_dir()`; child process without the instance env pack falls back to the **root** lab's pointer.
3. `src/arail/world_mount.py:653-666` — default dirs read `arail.config` module constants captured at import; later env changes invisible.
4. `src/arail/config.py:26-31` — `.env` walk-up search can escape the checkout (docstring admits it) and repoint `LAB_PKB`/`ARAIL_DATA_DIR`.
5. `src/arail/config.py:66-80` — legacy `LAB_PKM` accepted when `LAB_PKB` unset; two env names for the tenant root.

**Catalog / list resolution**

6. `src/arail/world_mount.py:739-745` — catalog scan failure → `subdirs = []`, silently zero worlds.
7. `src/arail/world_mount.py:777-786` — per-dir `except Exception` → bundle marked invalid, never raises; broken bundle vanishes from the picker.
8. `src/arail/world_mount.py:779` — catalog sorted **alphabetically by display name**; the order every positional consumer inherits.
9. `src/arail/world_mount.py:783-825` — mounted world appended with fallback display on manifest failure (two nested `except Exception`, :800, :804).
10. `scripts/start.sh:417` — `json.load(sys.stdin)[0]["slug"]`: single-world lab takes **catalog element 0**. Positional.
11. `scripts/start.sh:361-379` — remembered world missing from catalog → silently degrades to root lab.
12. `scripts/start.sh:504-508` — picker Enter-default = remembered row, else **option 0**.
13. `scripts/lib/instances.sh:257-289` — `inst_read_last_target`: all failure modes return 1 silently; caller defaults to root.
14. `scripts/lib/instances.sh:114-146` — corrupt registry record moved to `<slug>.json.bad`; instance vanishes from liveness.
15. `scripts/lib/instances.sh:429-437` — `inst_any_alive`: "first live instance", glob order = alphabetical.
16. `scripts/lib/instances.sh:499-523` — first-free-port-block scan; stale record still owns its ports.

**Portal HTTP resolution**

17. `src/arail/portal/app.py:3253-3263` — `_jailed()` swallows `is_dir()` exceptions → generic 400, indistinguishable from traversal.
18. `src/arail/portal/app.py:3529-3531` — `slug=="default"` → `unmount()` with unchecked result; staged content survives (see A26).
19. `src/arail/portal/world_routes.py:104-116` — catalog copy preferred over `record.bundle_dir`; drifted catalog wins for terms/goal routes.
20. `src/arail/portal/world_routes.py:92-101` — `_operator_source()` falls back to literal `"lab"` on brand-load failure.
21. `src/arail/portal/app.py:1038-1048` — startup mount announcement `except Exception` → boots with no world, warn only.

**Identity / intent resolution**

22. `src/arail/identity.py:97-106` — mount lookup failure → `_unmounted_identity()`.
23. `src/arail/identity.py:80` — `LAB_INTENT` default `"ai"` — the unmounted default intent driving researcher/curator behavior.
24. `src/arail/identity.py:113-135` — five layered per-field fallbacks, each `except Exception`-wrapped.

**Mount / sweep side effects (the actual scoping mechanism)**

25. `src/arail/world_mount.py:1407-1436` — `_sweep_other_worlds()`: world scoping via `shutil.rmtree`; per-dir failures skipped, an undeletable world stays searchable forever.
26. `src/arail/world_mount.py:1587-1633` — `unmount(remove_staged=False)` default; unmounted world's term pages + Lance rows stay searchable while `current_mount()` reports `None`.
27. `src/arail/world_mount.py:1287-1289` — `_index_staged` failure → mount succeeds with stale/partial index.
28. `src/arail/world_mount.py:1489-1495` — `_adopt_into_catalog` best-effort; failure leaves world un-reselectable.
29. `src/arail/world_mount.py:1316-1342` — `_prune_swept_approvals` fails to 0; docstring records the historical 554/556-corpse approvals bug.
30. `src/arail/world_mount.py:1364-1375` — `_switch_goal_for_world` skipped whenever `data_dir != _default_data_dir()`; world A's goal survives mounting world B.

**Vector / retrieval scoping**

31. `src/arail/vector_index.py:171-180` — `search(where=None)`; predicate exists, no production caller passes it.
32. `src/arail/pkb.py:519-527` — `pkb_pages` schema has **no world/tenant field**.
33. `src/arail/pkb.py:186-190` + `src/arail/vector_index.py:126-129` — any LanceDB error → `[]` silently; search drops to regex sweep.
34. `src/arail/pkb.py:581-583` — empty table → implicit full `index_all(root)` triggered by a plain search.
35. `src/arail/pkb.py:650-671` — semantic miss → whole-corpus regex sweep, also world-unscoped.
36. `src/arail/compiled_kb.py:109-118` (+ `:447-453`) — `approved_paths()` fails closed to `set()` with gate default on: unreadable manifest = zero agent search results.
37. `src/arail/portal/app.py:11223-11227` — `GET /api/pkb/search` without `approved_only`: raw corpus, incl. compiled repo docs.
38. `src/arail/lab_brain.py:526-529, 537-539` — chat RAG import failure → `[]`; per-term failure `continue`s; chat silently ungrounded.
39. `src/arail/pkb_index.py:207-213` — `_flush` on missing table → full `index_all()` (re-injects repo docs).
40. `src/arail/pkb_index.py:329-347` — schema mismatch → drop + `index_all()`.
41. `src/arail/pkb_index.py:399-414` — staleness sweep >200 files → full `index_all()`.
42. `src/arail/pkb_index.py:434-439` — `schedule_upsert` falls back to config-default PKB from unconfigured threads — writes into the wrong tenant.
43. `src/arail/pkb.py:451-461` — `_build_docs_rows` failure → `[]`, never blocks ingest.
44. `src/arail/portal/docs_registry.py:424-430` — `all_docs()` → `()` on any error.
45. `src/arail/portal/app.py:970` — `LANCE_PATH` default `./data/lance` is CWD-relative and disagrees with the doctor's `./lab/data/lance` (`app.py:10122`). Two defaults for one variable.

**Librarian scout**

46. `src/arail/librarian_scout.py:113-118` — catalog copy missing `manifest.json` → scout silently no-ops.
47. `src/arail/librarian_scout.py:99-102` — corrupt sidecar → fresh skeleton, losing `rejected` memory.
48. `src/arail/librarian_scout.py:315` — `cats[0]` — unknown category silently becomes the world's **first declared** category. Positional.
49. `src/arail/librarian_scout.py:245` — ripeness ties broken alphabetically by slug.
50. `src/arail/librarian_scout.py:131-140` — `_signal_files` mines `compiled/` (generated repo docs) → cross-domain vocabulary leaks into every world's proposals.
51. `src/arail/librarian_scout.py:365-370` — unparseable `last_scan` → full re-mine.

**Build path**

52. `src/arail/build/world_corpus.py:65-72` — `resolve_world_bundle` reads the **catalog copy**, not the mounted bundle; drift means the trainer pulls stale terms.
53. `src/arail/build/world_corpus.py:79` — missing `spec.json` → `{}` → silently zero approved terms.
54. `src/arail/build/world_corpus.py:38-40, 153` — hardcoded **photography** category tuple as default corpus scope; module docstring admits it is "wrong for every other World".
55. `src/arail/build/world_corpus.py:244-246` — `job_store.update` failure swallowed.
56. `src/arail/build/world_corpus.py:225-228` — "Explain {name} in photography…" prompt hardcoded for every world.

**Agent domain inference**

57. `src/arail/skills/goal_parser/__init__.py:42-49` — `max()` first-key tie-break → `"farming"` (dict key #1). **The farm bug.**
58. `src/arail/skills/goal_parser/__init__.py:116-118` — LLM parse failure/timeout → heuristic → `infer_domain`.
59. `src/arail/agents/researcher.py:92-95` — unknown intent key → `DEFAULT_SYSTEM_CONTEXT`.
60. `src/arail/agents/curator.py:73-79` — `_world_extra_sources()` failure → `{}`; world-declared trusted domains vanish.

---

*Report produced read-only. The only system mutation in Phase 1 was `brew install ariga/tap/atlas`. Awaiting sign-off before Phase 2.*
