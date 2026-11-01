# Phase 1b — the JSON layer, the governance gate, and the write-path races

**Date:** 2026-08-08
**Complements:** `PHASE1_AUDIT.md` (commit `60d8d53`, branch
`qukaizen/arail-2-declarative-persistence-819030`)
**Scope:** read-only. No mutations of any kind.

`PHASE1_AUDIT.md` measured the **Lance datasets** and traced the **world
resolution read path**. This document covers the three things it did not,
each of which changes Phase 2's scope:

1. the **flat-JSON layer** — 30+ distinct stores, which are atomic and which
   are not, and where the concurrent writers actually are;
2. a **governance gate** — an accepted ADR explicitly rejected SQLite, and a
   second ADR uses "ARAIL has zero relational databases" as its load-bearing
   argument. Both must be resolved before the schema lands;
3. **write-path races** — Phase 1's Appendix A is a read/resolution-path
   inventory. The lost-update races are on the write path and are not in it.

---

## 0. TL;DR

1. **SQLite is not a universal fix for "changes not reflecting."** The
   complaint spans at least three unrelated root-cause families. SQLite
   addresses one of them. Sizing the effort as if it fixes all three will
   disappoint. §4.
2. **`ConsentStore` is the single highest-risk store in the codebase** —
   read-modify-write, **no lock of any kind**, instantiated fresh in **nine**
   modules, and it is the durable record of *where agents are permitted to
   reach the network*. Lost updates here are a security-audit gap, not a UX
   glitch. §2.1.
3. **`GoalStore` is naive and genuinely concurrent** — instantiated
   independently in six modules including the researcher's tick loop and the
   portal's HTTP handlers, every mutator doing read→mutate→`write_text` with
   zero locking. This is the most plausible mechanical explanation for
   operator-visible "my change didn't stick." §2.2.
4. **A confirmed cross-process lost-update race on shared World bundles**,
   with no locking primitive available anywhere in the repo to fix it in the
   current architecture. §3.
5. **ADR-0002 explicitly rejected SQLite**; ADR-0003 depends on ARAIL having
   no relational database. Phase 2 needs a superseding ADR before more code
   lands, or the repo's own decision record contradicts its implementation.
   §5.
6. **The Phase 2 schema does not yet model the subsystems where the
   2026-08-06 bugs actually lived** — no `goals`, `approvals`, `experiments`,
   or `agent_workflows` table. §6.

---

## 1. Method

`grep -rn "json\.dump\|json\.load\|\.write_text(json\|_save_json\|_read_json"
src/arail --include="*.py"` (57 files), every hit traced to a concrete on-disk
path. Pure IPC/wire-protocol uses (subprocess framing in
`router/airllm_worker.py`, `skills/goal_parser/_subprocess_runner.py`,
`capabilities/backends/*`, SSE chunking in `mlx_openai_server.py`) are
serialization-over-pipes, not persistence, and are excluded.

Every store below is replicated **once per World instance** under
`lab/instances/<slug>/`. Within one instance there is one uvicorn process, so
"concurrent writer" usually means multiple async handlers and agent tick-loops
in the *same* process — except §3, which is genuinely cross-process.

---

## 2. The JSON layer

### 2.0 What is already correct

Worth stating first, because Phase 2 should preserve rather than rewrite
these. Twelve stores already use tmp-file + `os.replace`:

`compiled_kb.py` (approved/rejected), `world_mount.py` (mount pointer +
capabilities + model-hint sidecars), `librarian_scout.py` (per-World
sidecar), `dictionary.py`, `registry/store.py`, `portal/security_scan.py`
(tmp+replace **plus** `chmod 0600` before rename), `build/jobs.py`,
`scripts/lib/instances.sh` (registry records + last-target, with corrupt
files quarantined to `<slug>.json.bad` rather than crashing readers), and
`portal/services/opencode.py` (tmp+replace **and** `fsync`).

The best example in the repo is `research/agenda_watch.py`'s
`_safe_write_atomic()`: `os.open(tmp, O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW,
0o600)` then `tmp.replace(path)` — hardened against a symlink-swap attack on
the `.tmp` staging path that two other agent modules had already been patched
for. **If Phase 2 keeps any file-based store, this is the pattern to
standardize on.**

Five more stores are non-atomic but hold a `threading.Lock`, so they are safe
against in-process races though not against crash-torn writes:
`agent_workflows.py`, `agent_redirects.py`, `runtime_profile.py`,
`scheduler.py` (window-override and halt flag), `whispers.py`.

### 2.1 `ConsentStore` — highest risk found

`src/arail/agents/consent.py`. Backs
`lab/data/consent/{pending,allowlist,history}.json`.

- `request_access()` / `approve()` / `deny()` / `add_domain()` /
  `remove_domain()` each do **read-all → mutate-list → write-all**, naive
  `write_text`, `chmod 0600` after.
- **No lock at all** — not even the `threading.Lock` that
  `agent_workflows.py` and `agent_redirects.py` have.
- `ConsentStore()` is **freshly instantiated** (no singleton, no shared lock)
  in nine modules: `portal/app.py`, `egress.py`, `librarian_scout.py`,
  `research/agenda_watch.py`, `portal/world_routes.py`, `research/scouting.py`,
  `agents/browser.py`, `agents/_builtin_buddy.py`, `agents/curator.py`.

Two agents requesting network access in the same tick window can silently
lose one of the two pending entries. This file is the durable evidence trail
for the airgap/consent system — the thing that answers "what was this lab
ever allowed to reach?" A lost update is a hole in a security record.

**Phase 2 relevance:** strongest single argument for a transactional store,
and it is *not* in the current schema.

### 2.2 `GoalStore` — naive, hot, and genuinely multi-writer

`src/arail/goals.py`. Backs `lab/data/goals/{current,preview,run_state}.json`
+ `history/<id>.json`.

Every mutator — `update_current()`, `link_experiment()`, `add_finding()`,
`set_report()`, `update_progress()` — does read → mutate dict →
`write_text`, **no tmp+replace, no lock**.

`GoalStore()` is instantiated independently in **six** places, all pointing
at the same module-level `CURRENT_FILE`:

| Call site | When it writes |
|---|---|
| `portal/app.py` (module singleton) | every goal-mutating HTTP handler |
| `agents/researcher.py` (`self.goal_store`, per run) | every researcher tick |
| `world_mount.py` (`archive_if_world_mismatch`) | every mount/unmount/swap |
| `lab_brain.py`, `lab_brief.py`, `agents/_builtin_buddy.py` | reads + incidental writes |

A researcher tick calling `update_progress()` concurrently with a portal
handler calling `link_experiment()` **silently drops one of the two
updates** — classic lost update. A crash mid-`write_text` leaves a truncated
`current.json` (unlike the atomic stores, which cannot tear).

**This is the most plausible mechanical explanation for the operator-visible
"I changed it and it didn't stick."** It is also the store this session's
PR #170 just added a `world` field to — that field is correct, but it inherits
the same write hazard.

### 2.3 `costs.py` — hottest write path in the app

Process-wide singleton (`__new__`), so no cross-object race, but `track()` —
called on **every single inference call** — synchronously rewrites the entire
dict including a 500-entry `history` list, with **no lock and no
tmp+replace**, from what can be multiple concurrent async inference tasks.

`history` is capped at 500, but `calls_by_backend` / `tokens_by_backend` grow
unboundedly with distinct backend/model names over the process lifetime.
Confirms the earlier "global, never reset" observation: loaded once at import,
persisted forever, `_started_at` used for subscription-accrual math.

### 2.4 `ExperimentTracker` — O(n) index rebuild on every write

`src/arail/skills/experiment_tracker/__init__.py`. One JSON file per
experiment under `lab/data/experiments/<exp_id>.json`.

**Correction to a prior assumption:** `EXPERIMENTS_DIR` defaults to
`lab/data/experiments/`, **not** `lab/pkb/agents/researcher/experiments/`.

The per-experiment JSON writes are naive but low-blast-radius (one file each,
no cross-experiment race). The real cost is that `_save()` calls
`_rebuild_index()` on **every** `create`/`start`/`observe`/`complete`, and
`_rebuild_index()` does `self.base_dir.rglob("*.json")` — reading and
re-embedding the **entire** experiment corpus after every single small write.
**O(n) per write, n = total experiments ever.** With 78+ experiments already,
this compounds silently.

Instantiated independently in `portal/app.py`, `agents/researcher.py`, and
`agents/_builtin_buddy.py` — so HTTP handlers and the researcher loop are
genuine concurrent writers against the same shared Lance index.

### 2.5 `secrets.env` — naive write, same lost-update shape

`portal/app.py`'s `_write_secrets()` is a plain `p.write_text(...)` +
`chmod 0600`, no tmp+replace, called from many handlers (provider-token save,
GPU-model default, per-model ctx overrides). Two settings saves racing lose
one. Not JSON, but the same hazard class, and it holds provider credentials.

### 2.6 An inverted source-of-truth worth confirming

`agent_workflows.py`'s docstring frames **LanceDB as primary and JSON as the
disaster-recovery copy** — the inverse of every other dual-write in the repo
(`experiment_tracker`, `pkb_index`, `wiki_vectors` all treat the file as
canonical and Lance as a rebuildable index). Phase 2 should confirm which is
actually intended before migrating either.

### 2.7 Confirmed in-memory only

`_forge_state` / `_forge_result` (`portal/world_routes.py`, module globals)
never touch disk — grepped the whole file. **A World forge in progress is lost
on portal restart.** Relevant to Phase 2 only as a decision: is that
acceptable, or does a multi-minute LLM job deserve durable state?

---

## 3. The cross-process write race (not in Phase 1's Appendix A)

Appendix A is a read/resolution-path inventory. This is a write-path race,
and it is the one place where flat files fail in a way no amount of
careful single-process coding fixes.

`docs/concurrent-worlds.md` states `lab/worlds/` is *"shared, read-write…
safe only because at most one live instance can serve a given World slug at a
time."* **That is an assumption, not an enforcement.** Verified: the instance
registry never cross-checks against the root lab's mounted World, so the root
lab (`:8080`) can have `debt-finance` mounted while
`./arailctl start --world debt-finance` runs it as a separate OS process.

Two unsynchronized write paths into that shared directory:

- **`world_mount.py:1439` `_adopt_into_catalog()`** — non-atomic rename dance
  using **fixed, slug-keyed temp names** (`.adopting-<slug>`, `.old-<slug>`).
  Two processes adopting the same slug concurrently step on each other's temp
  dirs mid-copy.
- **`world_routes.py:875` `_run_grow()`** — reads `terms.json`, runs a
  multi-minute LLM pass, then `reseal_bundle()` overwrites the whole bundle.
  Its only guard is `_grow_state`, a **module-level dict — per-process**. Two
  concurrent growth passes read the same baseline and the second write wins;
  the first process's additions are **silently and permanently lost**, no
  error anywhere.

**There is no `flock`, `fcntl`, or `FileLock` anywhere in `src/arail/` or
`scripts/lib/*.sh`.** The repo has no cross-process locking primitive at all.

SQLite's write lock would serialize this class for free. This is the
strongest *technical* argument for the direction — and it is independent of
the schema-design argument.

---

## 4. Does SQLite fix "changes not reflecting"? Partly.

The complaint spans three unrelated families. Honest attribution:

| Family | Instances | SQLite? |
|---|---|---|
| **Missing schema / referential integrity / tenancy** | #163 dangling approvals (554/556); #166/#170 goal+experiment scoping; global `costs.json` | **Yes** — FK CASCADE and a `world_id` column make these structurally impossible rather than something a human must remember to prune |
| **Unsynchronized writes** | `GoalStore` lost updates (§2.2); `ConsentStore` (§2.1); cross-process `_run_grow` (§3) | **Yes** — transactions and a write lock |
| **In-process races, client state, TTL caches** | `F-CACHERACE`, `F-LOADRACE` (asyncio); chat model picks lost on reload (client `localStorage`, never wired up); `lab_brief` 30s TTL; `_MODELS_SCAN_CACHE` / `_LLM_READY_CACHE` 5s TTL | **No** — orthogonal to storage format entirely |

The third family was **the majority of historically-fixed instances** of this
bug class (`dc092d8`, `a40e837`, `5d27f77`, `4286ba2`, `160ce4b`). A
migration that lands perfectly will still leave those untouched.

Two caches specifically cleared of suspicion, so Phase 2 does not chase them:
`lab_brief.get_cached_brief()` keys on world slug **plus the mtime of every
source file**, rebuilding whenever any changes regardless of TTL — the only
stale window is a same-second write with identical size (negligible on APFS)
or a new agent-output file within the 30s TTL. `_MODELS_SCAN_CACHE` and
`_LLM_READY_CACHE` (5s) both have `force=1` bypasses already wired into their
mutation paths. Neither is a bug.

---

## 5. The governance gate — resolve before more code lands

**`docs/adr/0002-chat-memory-and-the-dac-boundary.md` (Accepted, 2026-07-16)
explicitly rejected SQLite**, under "Alternatives considered":

> **"A separate SQLite store outside the PKB."** Rejected. *"Technically the
> better substrate for append-heavy concurrent writes, but it is a wholly new
> pattern in a repo whose conventions are append-only JSONL (`activity.py`)
> and JSON+LanceDB dual-write (`agent_workflows.py`) — and, decisively,
> siting conversation data outside the PKB root breaks the documented 'wipe
> the PKB = wipe memory' contract."*

Note the ADR conceded the *technical* point ("technically the better
substrate") and rejected on **convention** and a **privacy contract**. Phase 2
answers the convention objection by making the new pattern the declared one.
The privacy contract is the part that still binds:

> **Wherever `arail.db` lands, `./arailctl reset pkb` must still mean "wipe
> the PKB = forget me."** If conversation data or PKB-derived rows live in a
> SQLite file outside the PKB root, that contract silently breaks. This is a
> hard constraint on file placement, not a preference.

**`docs/adr/0003-why-not-letta-memgpt.md` (Accepted)** uses the absence of a
relational DB as its central argument:

> *"ARAIL has **zero** relational databases (verified: no psycopg, postgres,
> pgvector, or alembic anywhere in `src/` or `pyproject.toml`)… Wrapping Letta
> means standing up a Postgres server to get it. It fails the stated
> constraint harder than building fails 'don't reinvent.'"*

Once SQLite ships, that argument is no longer true as written. ADR-0003 does
not necessarily *reverse* — "embedded SQLite in-process" is a different claim
from "stand up a Postgres server" — but it must be amended or it reads as
self-contradictory to the next person who cites it.

**Recommended:** one ADR that supersedes 0002's rejection, amends 0003's
premise, and states the placement constraint. Cheap to write, and it is the
difference between "an architecture decision" and "code that contradicts the
repo's own decision record."

Precedent in favor, worth citing in that ADR: `docs/maximus.plan.md` already
proposes `ARAIL_DB_URL: sqlite:///data/arail.db` for a future jobs/registry
subsystem — flagged "DESIGN PLAN — NOT BUILT," but it shows SQLite was
already the intended direction elsewhere.

---

## 6. Coverage gap in the Phase 2 schema

`spec/schema/schema.hcl` (commit `6e3eb60`) defines six tables —
`schema_version`, `worlds`, `entities`, `relations`, `world_state`,
`content_refs` — with 7 FK constraints (6 CASCADE, 1 SET_NULL). The doctrine
is right and `content_refs` directly retires the "no embedding provenance"
finding.

But **none of the subsystems where the 2026-08-06 bugs actually lived are
modeled yet**:

| Missing | Bug it would have prevented |
|---|---|
| `goals` (+ `world_id` FK) | #170 — goal outliving its World |
| `approvals` (FK → terms, CASCADE) | **#163 — 554/556 dangling pointers**, the flagship case |
| `experiments` (+ `world_id`, `goal_id` FK) | #166 — experiment leakage across goals |
| `consent` | §2.1, the highest-risk store found |
| `agent_workflows` | §2.6's inverted source-of-truth |

Whether these become first-class tables or rows in the generic
`entities`/`relations` model is a real design question — but it should be an
explicit decision, not an omission.

---

## 7. Recommended Phase 2 sequencing

Ordered by (evidence of harm) ÷ (cost), not by architectural tidiness:

1. **Write the superseding ADR.** §5. Gates everything else; costs an hour.
2. **Rebase the branch onto `main`.** It is 5 commits behind and `main` now
   carries changes to the *same functions* the schema supersedes —
   `_prune_swept_approvals()` and `_switch_goal_for_world()` in
   `world_mount.py`, and `goals.py`'s new `world` field. Decide deliberately
   which JSON-layer fixes become redundant rather than discovering it in a
   conflict.
3. **Model `approvals` + `consent` + `goals` first.** Highest evidence of
   real harm (§2.1, §2.2, and #163 is the proven case).
4. **Adopt `_safe_write_atomic()` for every file-based store that survives.**
   §2.0. Cheap, mechanical, independent of the migration — and if the
   migration slips, the codebase is still better off.
5. **Fix the two O(n)/hot-path issues regardless of storage:**
   `ExperimentTracker._rebuild_index()` (§2.4) and `costs.track()` (§2.3).
   Both are wrong at any storage layer.
6. **Decide the cross-process story explicitly** (§3) — SQLite's write lock,
   or a real advisory lock, or enforce the "one instance per slug" assumption
   the docs already claim.
7. **Set expectations on the third bug family** (§4) — it will survive the
   migration untouched and needs its own, separate work.

---

## 8. What this document does not claim

- No recommendation on `entities`/`relations` (generic) vs. first-class
  tables — that is Phase 2's design call, and both are defensible.
- No measurement of migration risk for existing operator data. The live lab
  has real goal history and a completed research report; a migration plan
  needs its own dry-run and rollback story, not covered here.
- No position on whether LanceDB should absorb more scalar querying instead
  of SQLite. `PHASE1_AUDIT.md` §2 notes `.where()` is already used narrowly
  (`wiki_vectors.py:111`, `pkb_index.py`); whether that generalizes is
  untested and worth one spike before committing to two engines.
