---
title: "ADR-0005: SQLite as ARAIL's Relational Store"
description: "Adopt SQLite for worlds, entities, relations, and mutable state — superseding ADR-0002's rejection on convention grounds, upholding its privacy constraint as binding, and amending ADR-0003's zero-relational-databases premise."
category: Architecture
order: 5
tags:
  - adr
  - persistence
  - sqlite
  - privacy
  - pkb
  - worlds
audience: architect
related:
  - conversation-memory
  - concurrent-worlds
---

# ADR-0005: SQLite as ARAIL's Relational Store

**Status:** Accepted (ratified 2026-08-10). Written and reviewed while Phase
2 was in flight on `qukaizen/arail-2-declarative-persistence-819030`,
precisely so the decision would be ratified before more code landed on it
rather than reverse-engineered from it afterward — that branch has since
merged to `main` (#175, `7bc2c64`), so this ADR is ratifying a decision
whose schema, `db.py`, and Atlas tooling are already live, not a proposal
for future code. Ratification required the ADR to close its own gap first:
`reset pkb` did not purge the PKB-derived `content_refs`/`entities` rows
this ADR's binding constraint (below) says must not survive it — fixed in
`scripts/reset.sh` (`reset_pkb()`) with `tests/test_reset_paths.py::test_reset_pkb_purges_pkb_derived_rows_from_arail_db`
pinning the two against drifting apart again, same day as ratification.
**Date:** 2026-08-08
**Deciders:** QuKaiZen
**Relates:** `sprints/2026-08-08-arail2-declarative-persistence/PHASE1_AUDIT.md`
(the Lance/resolution audit), `PHASE1B_JSON_LAYER_AUDIT.md` (the JSON layer,
the governance gate, the write-path races), `spec/schema/schema.hcl` (the
declarative schema), `docs/adr/0002-chat-memory-and-the-dac-boundary.md`
(superseded in part — see below)

## Context

ARAIL 1.x has no relational database. State lives in ~30 flat JSON/JSONL
stores plus four LanceDB tables, and the two Phase 1 audits found that this
is now the source of a recurring, operator-visible class of defect:

- **No referential integrity.** `world_mount._sweep_other_worlds()` deletes a
  World's staged term files; the Compiled-KB approval manifest is a separate
  JSON file of *pointers* to those paths, and nothing reconciled them. A real
  lab reached 554 of 556 approvals dangling, `search_for_agents()` returning
  zero hits for **every** query in **every** World for two weeks, with no
  error raised anywhere (fixed reactively in `#163`; the class remains).
- **No tenancy column.** No table — Lance or JSON — carries a world, tenant,
  or user field. Scoping is "which directory the process was pointed at,"
  enforced by `rm -rf`. `PHASE1_AUDIT.md` §4 traces the read path: the
  `where=` predicate exists in `vector_index.py:171` and is never passed,
  because there is no column to filter on.
- **Unsynchronized writes.** `ConsentStore` — the durable record of where
  agents may reach the network — does read-modify-write with **no lock of any
  kind**, instantiated fresh in nine modules. `GoalStore` does the same across
  six, including the researcher tick loop and portal HTTP handlers
  concurrently.
- **Identity by position.** `PHASE1_AUDIT.md` §5 traced the "farm world always
  wins" complaint to `max()` over a dict returning the first maximal key, with
  `"farming"` as key #1 — one of 60 catalogued positional/fallback resolutions
  on the identity path.

These are schema problems. They are not fixable by more careful file-writing.

## Decision

**Adopt SQLite as ARAIL's relational store**, declared in
`spec/schema/schema.hcl` and applied by Atlas. The storage split is:

| Store | Holds |
|---|---|
| **SQLite** | worlds, entities, relations, mutable state, resolution |
| **LanceDB** | embeddings, generated content, semantic retrieval |
| **Filesystem** | large binary artifacts, referenced by path |

Schema versioning is **global**, not per-world: all worlds share one entity
schema, one embedding model, one vector dimension at a given spec version.
Per-world variation in embedding model or dimension is corruption by
definition, and `db doctor` reports it as an integrity violation rather than
as configuration.

### The binding constraint: `reset pkb` must still mean "forget me"

This is the load-bearing half of this ADR and the reason it cannot be a
footnote in a build log.

`docs/adr/0002-chat-memory-and-the-dac-boundary.md` established, as a
*consequence* and not a preference:

> Conversation data lives under the **PKB root**, not `DATA_DIR`, because
> `docs/agents.md:142-143` and `_builtin_buddy.py:198-201` establish "wipe the
> PKB = wipe memory" as a real privacy contract. Siting the most sensitive
> data in the lab outside that contract would silently break it.

Phase 2 places the database at **`<data_dir>/arail.db`**
(`src/arail/dbspec/db.py`) — that is `lab/data/`, deliberately scoped to the
same directory boundary as a World instance's Lance tables and secrets. That
placement is correct for worlds, entities, and resolution state. It is
**directly across** the boundary ADR-0002 drew for conversation and
user-understanding data.

Concretely, and verifiable today:

| Command | Clears `lab/pkb/` | Clears `lab/data/arail.db` |
|---|---|---|
| `./arailctl reset pkb` | **yes** | **no** |
| `./arailctl reset data` | no | yes |
| `./arailctl reset full` | no (PKB is `full`'s one carve-out) | yes |

So: **an operator who runs `reset pkb` to be forgotten would not be forgotten**
for anything living in `arail.db`.

Therefore:

> **Conversation transcripts and distilled user facts MUST NOT be migrated
> into `<data_dir>/arail.db` unless `./arailctl reset pkb` is first taught to
> clear them.** Either keep that data under the PKB root (ADR-0002's
> placement, unchanged), or extend `reset pkb` to delete the corresponding
> rows — and cover it with a test that fails if the two ever drift apart.

This is not a stylistic constraint. `reset pkb` is the mechanism by which the
product's stated privacy promise is actually kept; a store that silently
survives it converts that promise into a false statement in the UI of a
product whose differentiator is truth-in-UI.

Note the schema's generic `entities` / `relations` / `world_state` tables make
this easy to violate by accident — nothing structurally prevents a future
phase from putting conversation rows there. Hence the explicit prohibition
rather than an assumption of good judgment.

**There is already precedent for the fix, and it is the pattern to copy.**
`reset_pkb()` in `scripts/reset.sh` handles exactly this problem for the
`ARAIL_CONVERSATIONS_DIR` override:

```bash
# Honor the ARAIL_CONVERSATIONS_DIR override: if chat memory lives OUTSIDE
# the PKB root, wiping the PKB alone would silently leave transcripts behind
# (breaking "wipe the PKB = forget me"). Wipe the override path too.
```

So the codebase has already met this hazard once, recognized it in those
words, and solved it by teaching `reset pkb` to reach outside the PKB root
for relocated memory. If conversation or user-fact data ever lands in
`arail.db`, the resolution is the same shape — a targeted delete in
`reset_pkb()`, plus a test — not a new argument.

## What this supersedes, precisely

### ADR-0002's SQLite rejection — superseded in part, upheld in part

The rejected alternative was, in full:

> **A separate SQLite store outside the PKB.** Rejected. Technically the
> better substrate for append-heavy concurrent writes, but it is a wholly new
> pattern in a repo whose conventions are append-only JSONL (`activity.py`)
> and JSON+LanceDB dual-write (`agent_workflows.py`) — and, decisively,
> siting conversation data outside the PKB root breaks the documented "wipe
> the PKB = wipe memory" contract.

Three parts, three different fates:

1. **"Technically the better substrate"** — *unchanged, and now the
   load-bearing argument.* ADR-0002 conceded this in 2026-07-16 and rejected
   anyway on other grounds. The evidence gathered since (§Context) is that the
   technical case was not merely correct but the decisive one.
2. **"A wholly new pattern in a repo whose conventions are…"** —
   **superseded.** This was an argument from convention, and it was sound when
   the convention was unexamined. `spec/schema/schema.hcl` makes the
   relational store *the declared convention* rather than an exception to one.
   A convention objection cannot survive the deliberate act of changing the
   convention.
3. **"Siting conversation data outside the PKB root breaks the contract"** —
   **upheld, and promoted to a binding constraint** (above). ADR-0002 was
   right, the risk is now larger rather than smaller, and nothing here
   weakens it.

Note the scope of the original: it rejected *"a separate SQLite store outside
the PKB"* **for conversation memory**. It was never a general prohibition on
SQLite, and it should not be cited as one.

### ADR-0003's premise — amended, conclusion intact

`docs/adr/0003-why-not-letta-memgpt.md` argues:

> ARAIL has **zero** relational databases (verified: no psycopg, postgres,
> pgvector, or alembic anywhere in `src/` or `pyproject.toml`). Its conventions
> are append-only JSONL (`activity.py`) and LanceDB. … Wrapping Letta means
> standing up a Postgres server to get it.

The first sentence stops being true when this ADR lands. **The conclusion does
not change**, because the argument never rested on "no database" — it rested
on *cost of the dependency*. Letta requires a **~500 MB server process plus a
~300 MB Postgres, on a network port, with Alembic migrations on startup.**
ARAIL is adopting an **embedded, in-process, single-file, zero-daemon**
library that ships in the Python standard library. Those are different claims
about different things.

Readers of ADR-0003 should treat its "zero relational databases" line as
accurate-as-of-2026-07-16 and superseded here; its rejection of Letta stands
on the dependency-cost argument alone.

## Consequences

**Accepted:**

- One new engine to reason about. Mitigated by SQLite being stdlib
  (`import sqlite3`, no dependency added to `pyproject.toml`) and embedded —
  no daemon, no port, no container. The *running* lab's airgap posture is
  unaffected: `db.py` opens a local file, nothing on a network port.
- **A build-time dependency this ADR should not understate: Atlas.**
  `spec/schema/schema.hcl` is compiled to SQL by the `atlas` binary
  (`src/arail/dbspec/atlas.py`) — a third-party Go tool, installed via
  `brew install ariga/tap/atlas` or `ARAIL_ATLAS_BIN`, not vendored, and not
  yet wired into `scripts/setup.sh` on any platform. It runs on the
  *developer's* machine for `./arailctl db plan/apply/migrate`, never on an
  operator's running lab, so the airgap claim above still holds — but "no
  dependency added" is true only of the runtime, and the ADR should say so
  rather than let that line imply the whole decision is free. Since Atlas
  v0.38, `atlas migrate lint` is additionally gated behind an Atlas Pro
  login; without one it aborts non-zero without linting, and
  `atlas.py:lint_migrations` degrades to a narrower local destructive-SQL
  grep and labels itself as such. Track "install Atlas in `setup.sh`, or
  document it as a maintainer-only tool" as follow-up; it is not a blocker
  for this ADR, but it is a real line item, not a zero.
- `foreign_keys=ON` is **per-connection** in SQLite and off by default. Every
  connection must set it or every FK cascade in the schema is silently lost.
  `db.py`'s `connect()` centralizes this; nothing may open the database
  outside it.
- Migration risk against real operator data. The live lab holds real goal
  history including a completed research report. Any migration needs a
  dry-run, a backup, and a rollback path — not covered by this ADR.
- Two query languages (SQL + LanceDB's filter API) for developers to hold.

**Explicitly NOT solved by this decision** — stated here so the migration is
not oversold:

- **In-process asyncio races** (`F-CACHERACE`, `F-LOADRACE`), **client-side
  state** (chat model picks lost on reload — `localStorage`, never wired up),
  and **TTL-cache staleness** (`lab_brief` 30s, `_MODELS_SCAN_CACHE` 5s).
  `PHASE1B` §4 found these were the **majority** of historically-fixed
  "changes not reflecting" instances. They are orthogonal to storage format
  and will survive this migration untouched.
- **The cross-process race on shared World bundles.** `_run_grow()`
  (`world_routes.py:875`) does a multi-minute read-modify-write of
  `lab/worlds/<slug>/terms.json` guarded only by a per-process dict, and
  `db.py` declares multi-process concurrent writers an **explicit non-goal**.
  SQLite's file lock would serialize writers *that go through the database* —
  but World bundles are files, so unless bundle content moves into SQLite,
  this race is unaffected. Decide it deliberately; do not assume the
  migration covers it.
- **`ExperimentTracker._save()`'s O(n) index rebuild** and **`costs.track()`'s
  full-file rewrite per inference call** are wrong at any storage layer and
  should be fixed independently.

## Alternatives considered

**Keep flat JSON; add locking and atomic writes everywhere.** Rejected as
insufficient, though partially worth doing anyway. It addresses the
unsynchronized-write family (§Context) but not referential integrity or
tenancy — no amount of careful writing gives `approvals` a foreign key to
`terms`, which is the mechanism that made `#163` possible. Worth doing for
whatever file stores survive: `research/agenda_watch.py`'s
`_safe_write_atomic()` (`O_NOFOLLOW`-hardened against a symlink swap on the
staging path) is the pattern to standardize on, and it is independent of this
decision.

**Push more querying into LanceDB instead of adding an engine.** Rejected for
now, but genuinely untested and worth a spike before the two-engine split
hardens. LanceDB already supports scalar predicates alongside vector search
(`wiki_vectors.py:111` filters by `slug`; `pkb_index.py` deletes by path
predicate), so some of what is wanted may already be reachable. It is rejected
because Lance has no foreign keys, no transactions across tables, and no
joins — which is exactly the missing capability — and because every current
Lance table is a *rebuildable derived index*, not a system of record.
Inverting that is a larger change than adding SQLite beside it.

**Postgres.** Rejected on the same grounds ADR-0003 rejected it for Letta: a
server process, a port, and an install step, in a product whose default
posture is airgapped and single-machine and which is distributed as a
blueprint other people run on their own hardware.

**Do nothing; fix each bug as it appears.** Rejected. This is the status quo,
and `PHASE1B` §4 documents its cost: the same bug shape recurring every few
weeks across unrelated subsystems, each fixed reactively after an operator
hit it in normal use.

## References

- `sprints/2026-08-08-arail2-declarative-persistence/PHASE1_AUDIT.md` — Lance
  census, 60-finding resolution-path appendix, the farming-bug diagnosis
- `sprints/2026-08-08-arail2-declarative-persistence/PHASE1B_JSON_LAYER_AUDIT.md`
  — the JSON-layer inventory, this ADR's gate, the write-path races
- `spec/schema/schema.hcl` — the declarative schema (storage-split doctrine in
  its header comment)
- `src/arail/dbspec/db.py` — `<data_dir>/arail.db`, `foreign_keys=ON`,
  single-writer assumption
- `docs/adr/0002-chat-memory-and-the-dac-boundary.md` — superseded in part
  (convention), upheld in part (the PKB-root privacy contract). Cite by slug:
  `chat-memory-and-the-dac-boundary`
- `docs/adr/0003-why-not-letta-memgpt.md` — premise amended, conclusion
  intact. Cite by slug: `why-not-letta-memgpt`
- `docs/maximus.plan.md` — prior, unbuilt proposal of
  `ARAIL_DB_URL: sqlite:///data/arail.db` for a jobs/registry subsystem;
  evidence the direction predates this ADR
- `src/arail/agents/consent.py`, `src/arail/goals.py` — the two
  highest-traffic unsynchronized stores motivating the transactional argument

> **Citation note.** ADR numbers are not unique across this workspace (see
> `docs/adr/README.md`); ARAIL's own `0004` slot is already claimed by two
> different records on different branches. Cite this record by **repo +
> slug** — ARAIL `sqlite-as-the-relational-store` — never by number alone.
