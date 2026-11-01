# ARAIL 2.0 — Declarative Persistence

**Sprint id:** 2026-08-08-arail2-declarative-persistence
**Branch:** `qukaizen/arail-2-declarative-persistence-819030`
**Status:** shipped — [PR #175](https://github.com/cdarnell/qukaizen-arail/pull/175). Tier 1.2 shipped in the successor sprint `2026-08-08-arail2-tier1-integration`; Tier 1.1 and the cutover were deferred/rejected there on evidence.

## Artifacts

| Doc | What |
|---|---|
| [PHASE1_AUDIT.md](PHASE1_AUDIT.md) | Read-only audit: inventory, resolution trace, farm-bug diagnosis |
| [INTEGRATION.md](INTEGRATION.md) | Where to leverage the new layer in ARAIL, ordered by value |

## What shipped

| Commit | What |
|---|---|
| `60d8d53` | Phase 1 audit report |
| `f4c02fb` | Farm bug fix — domain inference tie-break |
| `6e3eb60` | Spec tree, HCL parser, compile-time validation |
| `084f123` | Atlas compiler, SQLite runtime, repository, real embeddings |
| `c8098a4` | Reconciler, doctor, CLI, migration, `arailctl db` verb |

**Test count:** 107 dbspec tests, all passing.

**Regression check.** Full suite on this branch: 53 failed / 4228 passed.
Compared against a worktree at the pre-branch commit (`8cb5760`) over the
same 21 suspect files: **28 failures on both sides, zero regressions.** The
53 are pre-existing (world-forge API, dispatch-35b, aerollm defaults, swarm
surfaces, and similar), unrelated to persistence. One real regression was
found and fixed during this check: adding the `db` verb to `arailctl` tripped
the repo's F33 gate requiring every verb to appear in `docs/cli.md`.

## Decisions taken

1. **nomic-embed-text via Ollama** (operator-approved). No new runtime
   dependency: Ollama is already required. 768 dims, global.
2. **HCL parser vendored, not `python-hcl2`.** Build-time only, four files we
   author, and ARAIL installs airgapped on other people's machines. Safety
   comes from strictness — anything outside the subset is a hard error naming
   file and line.
3. **`user_id` is synthesized, not derived from instance slug.** The Phase 1
   audit found no user concept at all; the operator is one human running
   several Worlds. Instances map to *worlds*, not users. (This corrects an
   earlier reading in the audit's §7.)
4. **Embedding never falls back.** If the model is unavailable, ingest fails
   loudly. Mixing vector spaces makes distance meaningless while every query
   still looks confident.
5. **The abandoned `lab/instances/finance/` scaffold is skipped, not deleted.**
   Zero rows in every table is not a world. Source left on disk for the
   operator to dispose of.

## Verified end to end on the real 1.x lab

```
migrate --apply   5 worlds, 1,617 rows re-embedded to 768-dim nomic
                  'finance' scaffold skipped, sources untouched
apply             IVF_PQ indexes created on pkb_pages and wiki_nodes
optimize          fragments 5 -> 1 per table; agent_workflows versions 6 -> 1
doctor            clean — 0 errors, 0 warnings across 9 checks
drift             exit 0
```

## Bugs found by running it against real data

1. **content_refs identity was world-blind.** `UNIQUE(lance_table, row_key)`
   but row keys are only unique within a world — 36 paths are shared across
   the live lab's four worlds. Migration would have silently reassigned rows.
   Now `UNIQUE(world_id, lance_table, row_key)`.
2. **argparse subparser defaults clobbered pre-subcommand flags**, so
   `db --data-dir X drift` silently targeted the ambient lab. `SUPPRESS` fixes
   it; both orders tested.
3. **The lint gate flagged SQLite's data-preserving table rebuild as data
   loss**, which would have made every index change unshippable.
4. **`apply` did not converge in one pass** — a freshly created table never
   got its declared scalar indexes.

## Known debt

Carried in [INTEGRATION.md](INTEGRATION.md) § "Known debt". Summary:

- `reconcile` uses a module-level spec registry between `plan()` and `apply()`.
- `atlas migrate lint` needs an Atlas Pro login; a narrower local gate runs
  instead and says so. `atlas login` upgrades it with no code change.
- The doctor's NaN check is defense-in-depth (lancedb rejects NaN at write);
  a test pins that guard.
- **Corpus contamination is unfixed.** Generated repo docs are still indexed
  into every world's PKB (audit 3b). World-scoping contains the blast radius;
  removing the contamination is a behaviour change and was out of scope. This
  is visible in the retrieval demo: a scoped query in `debt-finance` still
  returns `sources/seeds/model-building/*` at distances above 1.0.

## Next

Per INTEGRATION.md, in order: Tier 1.2 (swap `hash_embedding` for the real
provider at the PKB ingest path), then 1.1 (world-scoped retrieval in
`pkb.search`), then 1.4 (model registry), then 1.3 (resolver). Nothing in
`src/arail/pkb.py`, `vector_index.py`, `world_mount.py`, or `scripts/start.sh`
has been changed yet — the new layer is additive so far.
