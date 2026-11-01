# ARAIL 2.0 — Where to leverage the declarative persistence layer

**Date:** 2026-08-08
**Status:** recommendation, ordered by value. Phase 2 built the machinery; this
is the map of what it should replace and in what order.

Every claim below traces to a numbered finding in
[`PHASE1_AUDIT.md`](PHASE1_AUDIT.md) Appendix A.

---

## The one-line version

The persistence layer's value to ARAIL is not "we have SQLite now." It is that
four things that were previously **unrepresentable** became representable:

| Now representable | Was |
|---|---|
| Which world a piece of content belongs to | Nothing recorded it (A32) |
| Which model embedded a vector | Nothing recorded it (§2.2) |
| A world identity that is not positional | `catalog[0]`, alphabetical sort (A8, A10) |
| A model that is too big to answer | A runtime check nobody called |

Everything below follows from those four.

---

## Tier 1 — Do these; they retire whole classes of defect

### 1.1 World-scoped retrieval (retires the `rm -rf` scoping model)

**Where:** `src/arail/pkb.py:563` (`_semantic_search`), `:620` (`search`),
`:673` (`search_for_agents`); `src/arail/vector_index.py:171`.

**Today:** `pkb_pages` has no world column, so `VectorIndex.search(where=...)`
has nothing to filter on and no caller passes it. A world is scoped by
physically deleting the other worlds' staged files
(`world_mount.py:1407 _sweep_other_worlds`). Two consequences the audit
confirmed on disk: unmounting a world leaves its rows searchable
(A26 — `unmount(remove_staged=False)` is the default and the portal's
"default" branch calls it with no arguments), and a world dir that fails to
delete stays searchable forever (A25).

**Change:** `world_id` is now a declared column on every vector table, and
`repo.row_keys_for_world` gives the scoping primitive. `search` takes a
`world_id` and passes `where=f"world_id = '{...}'"`. Deletion stops being a
scoping mechanism.

**Why it is worth the churn:** this is the difference between "the other
world's data is gone" and "the other world's data is not in this answer." The
first is destructive and unreliable; the second is a WHERE clause.

### 1.2 Real embeddings (retires "semantic search" that was lexical)

**Where:** `src/arail/vector_index.py:34` (`hash_embedding`),
`src/arail/wiki_vectors.py:22`, `src/arail/pkb_index.py:49`.

**Today:** every vector in the lab is a 128-dim SHA1 token-hash projection — a
hashed bag of words. It is deterministic and dependency-free, and the module
docstring is honest that it is "good enough" for small corpora. But it means
retrieval is lexical overlap wearing a semantic label, and with 128 dimensions
over a ~380-row corpus, token collisions put off-topic pages in the top-k
routinely. That is the mechanism behind contamination finding 3b in the audit:
a generated `env-vars.md` whose first 4 KB is a list of intent keywords is a
plausible hit for a wide range of queries.

**Change:** `arail.dbspec.embed` serves nomic-embed-text at 768 dims through
Ollama, with the task prefixes the model expects (declared in the spec, since
which prefix a model wants is a property of the model). Measured on the
audit's own example, prefixing widens the relevant-vs-irrelevant margin about
15%.

**The non-negotiable part:** the module refuses to substitute another
embedding when the model is unavailable. A fallback here looks helpful and is
not — vectors from two models occupy unrelated spaces, so mixing them does not
degrade recall gracefully, it makes distance meaningless while every query
still returns confident-looking results. `content_refs` records the model and
dimension per row, and `db doctor` reports any row that disagrees.

### 1.3 Generated world resolver (retires six positional fallbacks)

**Where:** `scripts/start.sh:392-533`, `scripts/lib/instances.sh:257-437`,
`src/arail/world_mount.py:679-825`, `src/arail/portal/app.py:3529`.

**Today:** the resolution ladder has a positional pick when exactly one world
exists (A10, `catalog[0]`), an alphabetical catalog order that every
positional consumer inherits (A8), a "first live instance" in glob order
(A15), a picker default of option 0 (A12), a corrupt mount pointer that
silently degrades to no-world (A1), and a missing `ARAIL_DATA_DIR` that falls
back to the **root** lab's pointer (A2).

**Change:** `generated/world_resolver.py` accepts an explicit id or slug only,
scoped to a user, and raises `WorldNotFound` naming the request, the reason,
and the valid alternatives. There is no fallback branch in the generator to
emit, and the spec loader refuses to load a spec that asks for one — verified
by test.

**Migration note:** archived worlds are resolvable but not selectable.
Resolving one by explicit slug is correct; silently substituting a different
world would not be. That distinction is the whole design in miniature.

### 1.4 Generated model registry (makes the 8B ceiling real)

**Where:** `src/arail/model_defaults.py`, `src/arail/router/`,
`lab/data/model_registry.json`.

**Today:** model selection is spread across a JSON file, env vars, and router
logic. There is no ceiling anywhere.

**Change:** `generated/models_registry.py` is the only resolution path, and a
spec declaring an answering model at or above 8B **fails to build** — no
override flag exists, deliberately. A model whose parameter count cannot be
determined is ineligible by name rather than assumed small; filenames are
never evidence.

---

## Tier 2 — Worth doing, lower blast radius

### 2.1 Instance registry to SQLite

`lab/instances/registry.d/*.json` currently loses a corrupt record by renaming
it to `.json.bad` (A14), leaving the instance invisible to liveness checks;
and the live check picks the first instance in glob order (A15). The `worlds`
table with `UNIQUE(user_id, slug)` plus `world_state` replaces both. The audit
found the on-disk evidence: four instance directories, one registry record,
and its PIDs two days stale.

### 2.2 High-churn state into `world_state`

`agent_workflows` holds 2-19 rows but accumulated 276 versions in one instance
and 150 in another, because nothing ever compacted. `world_state` is isolated
from content tables precisely so this churn stops fragmenting them, and
`db optimize` now has a retention window to enforce (5 versions for that
table, vs the 20 default).

### 2.3 `db drift` as a CI gate

Non-zero exit when actual does not match spec, including stale generated code.
This is what stops the spec and the database from quietly diverging between
sprints.

---

## Tier 3 — Do NOT do these

- **Do not migrate chat memory into this store.** `docs/adr/0002` draws that
  boundary deliberately and DaC defines itself against storing conversation
  history. Superseding that ADR is a decision, not a refactor.
- **Do not use the entities table for the Compiled-KB approval gate.** The
  gate's fail-closed behaviour (`compiled_kb.py:109`) is load-bearing for
  agent safety; moving it is a separate, carefully-tested change.
- **Do not rename the machine surface.** `dac.*/vN` schema strings, module
  paths, env vars, CLI names are frozen (workspace `CLAUDE.md`, dac ADR-0011).

---

## Known debt carried out of Phase 2

1. **`reconcile` uses a module-level table-spec registry** between `plan()` and
   `apply()`. Safe today because schema versioning is global — every world sees
   identical table specs — and it raises clearly rather than failing silently
   if a plan is applied without an in-process `plan()`. Worth folding onto the
   plan object when the contract next changes.
2. **`atlas migrate lint` requires an Atlas Pro login** and exits non-zero
   *without linting*. We detect that exact condition and run a local
   destructive-statement gate instead, labelled as such in the output. The
   local gate is narrower than real lint: it catches DROP/TRUNCATE/DELETE but
   not backward-incompatible or data-dependent changes. `atlas login` upgrades
   the gate with no code change.
3. **The doctor's NaN check is defense-in-depth.** lancedb 0.30.2 rejects NaN
   vectors at both `add()` and `create_table()`, so one cannot enter through
   our write path. A test pins that guard, so if a future lancedb stops
   rejecting them we find out.
4. **Contamination cleanup is not done.** Generated repo docs under
   `compiled/` are still indexed into every world's PKB (audit 3b), and the
   shipped `ai` / `qukaizen` bundles both contain the farm-flavoured `tice`
   term (audit 3c). World-scoping (1.1) contains the blast radius; actually
   removing the contamination is a behaviour change and was out of Phase 2
   scope.

---

## Suggested order

1. Migration (`db migrate`) — everything else assumes rows carry `world_id`.
2. 1.2 embeddings, then 1.1 world-scoped retrieval — 1.1 is only worth having
   once vectors are real.
3. 1.4 model registry — self-contained, no data dependency.
4. 1.3 resolver — touches the most call sites; do it when the rest is stable.
5. Tier 2 as capacity allows.
