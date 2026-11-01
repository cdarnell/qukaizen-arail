# Architecture: ARAIL 2.0 persistence, instantiated

**Date:** 2026-08-10
**Spec:** [SPRINT.md](./SPRINT.md) (no VISION.md — think phase skipped)
**Branch:** `qukaizen/arail2-persistence-instantiated`, based on `d5c592a`
**Precedent deviated from:** [`sprints/2026-08-09-compiled-kb-bootstrap/ARCHITECTURE.md`](../2026-08-09-compiled-kb-bootstrap/ARCHITECTURE.md) (bootstrap-on-start = refuse), per the operator's 2026-08-10 ruling.

---

## 0. RULING: A and B are TWO independent defects

**Requested first. Answer: two defects, one *class*. Defect A does not cause defect B.**

This is not an inference from reading imports. It is measured, twice, on the
operator's own machine at `origin/main`.

### B does not reproduce in a provisioned environment

Run against the operator's real `ai` World (422 rows, 339 approved), from
`/Users/netsushi/ProJects/qukaizen-arail` with `.venv/bin/python`, with **no
`arail.db` present anywhere on the machine**:

```
pkb._semantic_search('attention', ai_root)             -> 12 hits, top score 0.7737
pkb._semantic_search('how does attention work', ...)   -> 12 hits, top 0.7786 (attention.md)
pkb._semantic_search('what is a transformer', ...)     -> 12 hits, top 0.7416 (transformer.md)
pkb.retrieve_for_agents('how does attention work')     -> 12 hits, empty_reason=None,
                                                          source='semantic' (GATED)
```

The natural-language win condition **already works** on the shipped code when
the process is correctly provisioned, and it works with zero `arail.db` files
in existence. The relational store is not on the retrieval path at all.

### The mechanism, confirmed

`grep` for `dbspec` outside `src/arail/dbspec/` returns only `dbspec.embed`,
`dbspec.generated.models_registry`, and one `dbspec.spec.load_spec` call.
**Nothing outside `src/arail/dbspec/` imports `db`, `repo`, `migrate`, or
`reconcile`.** `src/arail/dbspec/repo.py` (500 lines) has zero runtime
consumers. The search path cannot depend on a file it never opens.

### What B actually is

Exact reproduction, in this sprint's worktree (`.claude/worktrees/eloquent-lederberg-6aeb3b`,
which has **no `.venv`**), using system `python3`:

```
arail.vector_index.available()          -> False        (lancedb not importable)
pkb._semantic_search('attention', root) -> 0
pkb.search('attention')                 -> 49 hits, every one source='keyword'
pkb_index.embedding_status()            -> (True, '')   <-- reports HEALTHY
```

Symptom-for-symptom identical to the sprint's evidence appendix: single words
match by regex, natural-language questions return 0, every hit is
`source='keyword'`, `_semantic_search` returns 0 even ungated, and the health
surface says everything is fine. The `lab/pkb/.cache/lancedb/pkb_pages.lance`
table *exists and is populated* in that tree; the interpreter simply cannot
import LanceDB.

The code defect is `pkb.py:717-718`:

```python
if not available():
    return []
```

This is the **only** early return in `_semantic_search` that does not call
`pkb_index.set_degraded(...)`. Every other failure path — empty table,
unopenable table, dimension mismatch, provenance mismatch, provider outage,
backend error — sets a degraded code. The "the vector backend is not installed
in this interpreter" path is silent, so `embedding_status()` returns
`(True, "")`, `retrieval_status()` reports healthy, `/api/pkb/search` stamps no
`X-Retrieval-Status: degraded`, and `doctor` says OK. Semantic retrieval is
dead and every honesty surface in the product agrees it is alive.

Whether the operator's measurement was taken in a worktree, a non-`.venv`
shell, or a stale env does not change the ruling: **the product's job is to
say so, and it does not.** Defect B is an honesty defect on the "backend
absent" branch, not a ranking or embedding defect. **Do not touch scoring,
`min_score`, prefixes, or the LanceDB distance conversion — they are measured
correct (nomic vectors are unit-norm; `sqL2` 1.089 between unrelated strings
→ score 0.456, well above `min_score=0.05`).**

### Recommendation on splitting

**Do not split.** A and B are independent in mechanism but identical in class,
and the class is this sprint's first-class deliverable:

> A mechanism ships correct, is gated behind a step nothing performs, and
> every health surface reports OK.

QA-6 was instance one (gate on, nothing approved). A is instance two (store
specified, nothing creates it). B is instance three, and it was *already live*
while we were writing instance-two's sprint. Three instances in two releases.
Fixing them in one sprint with one shared assertion mechanism (§7) is the
correct scope; splitting would produce two sprints that each build half of the
same `status`/`doctor` surface.

**Consequence for scope:** the sprint's stated win condition "Buddy retrieves
on a natural-language query" is *already true* on a provisioned lab. The real
win condition is restated below.

---

## 1. Restatement

ARAIL 2.0 shipped a per-data-dir SQLite store (`<data_dir>/arail.db`) that no
code path ever creates, and a semantic retrieval path that silently falls back
to regex — reporting itself healthy — whenever the running interpreter cannot
import LanceDB. This sprint makes the relational store come up automatically
as a dependent service (create + forward-apply the checked-in migrations,
Atlas-free, on `install` and on `start`, reported not silent), makes both the
store and the vector backend first-class, honest lines in `status` and
`doctor`, and — the durable deliverable — adds a *provisioning assertion*
layer so that any future "declared but never instantiated" mechanism fails a
check instead of passing one. Anything lossy or data-altering (Lance
destructive reconcile, the one-shot 1.x→2.0 `db migrate`, a migration whose
recorded history diverges) stays behind an explicit verb and is *reported*,
never performed, by `start`.

## 2. Assumptions

1. **`atlas` is a developer tool, not a user dependency.** `db apply` shells
   out to the `atlas` binary (`brew install ariga/tap/atlas`), generates a new
   migration file into the repo's `spec/schema/migrations/`, runs lint, and
   regenerates code into the *source tree*. A user who clones and runs
   `./arailctl setup` has none of that and must never need it. Therefore the
   seamless path **must not be `db apply`** — see §4.1.
2. **`spec/schema/migrations/` is a committed, ordered, hash-summed ledger**
   (`atlas.sum` + `20260808155711_baseline.sql`, 18 statements). Replaying it
   in lexical order into an empty SQLite file reproduces the declared schema
   exactly. Verified by reading the baseline.
3. **The checked-in migrations were lint-gated at authoring time.** Their
   safety was decided by a human running `db apply`; the runtime path replays,
   it never authors.
4. **`PRAGMA user_version` is invisible to `atlas schema diff`.** Using it as
   the applied-migration cursor adds no table and therefore introduces no
   drift against `spec/schema/schema.hcl`. (If the builder finds this false,
   fall back to a `_arail_migrations` table and add it to the spec — do not
   silently create an unspecced table.)
5. **The relational store currently has no runtime reader.** Creating it
   satisfies "the dependent service is up"; it does not by itself make any
   feature work. This must be stated in the docs rather than implied away.
6. **Six PKB roots, an empty registry.** `lab/instances/registry.d/` is
   *literally empty* on the operator's machine while five instance dirs exist
   on disk (`ai`, `qukaizen`, `video-games`, `debt-finance`, `finance`) plus
   the root lab. Any "all instances" walk driven by `inst_list_slugs()` alone
   reaches **zero** of them.
7. **One process per World**; `pkb_index`'s degraded state stays
   process-global. Nothing here rebinds `PKB_ROOT` in-process.
8. **The embedder is local and stays off the search path.** Nothing in this
   design adds an embed call, a network call, or an index build to a search
   request or to `status`.

## 3. Data flow

```
                        spec/schema/migrations/       (committed, ordered, atlas.sum)
                                 │  read-only, hashed
                                 ▼
  ┌───────────────────────────────────────────────────────────────────┐
  │ arail.dbspec.ensure  (NEW — Atlas-free, codegen-free, read-only   │
  │                       w.r.t. the repo)                            │
  │   ensure_db(data_dir, apply=bool) -> EnsureReport                 │
  │     · database_path(data_dir)                                     │
  │     · PRAGMA user_version = N migrations applied                  │
  │     · classify pending: SAFE-FORWARD | LOSSY | DIVERGED | AHEAD   │
  │     · apply=True: replay SAFE-FORWARD only, one txn per file      │
  │     · never: atlas, codegen, reconcile.apply, migrate(1.x)        │
  └───────────────────────────────────────────────────────────────────┘
       ▲ apply=True          ▲ apply=True           ▲ apply=False (never writes)
       │                     │                      │
  ./arailctl install    ./arailctl start        ./arailctl status
  (all resolved roots)  (this process's         ./arailctl doctor
                         data_dir only)         (all resolved roots)
                              │                      │
                              ▼                      ▼
                    <data_dir>/arail.db      arail.status/v2 . services[]
                                                + doctor findings

  resolve_data_dirs()  ──  union of:  registry.d/*.json
   (NEW, shared)                     ∪ lab/instances/*/  with a data/ dir on disk
                                     ∪ the root lab's ARAIL_DATA_DIR
                       ──  each row carries origin=registry|ondisk|root
```

Retrieval path (defect B) — no structural change, one added honesty edge:

```
pkb.search(q)
   └─ pkb._semantic_search(q, root)
        ├─ vector_index.available() == False
        │     └─ NEW: pkb_index.set_degraded("backend",
        │              "LanceDB is not importable in this interpreter (…) — run ./arailctl install")
        │        return []                      ← unchanged behaviour, now audible
        └─ … (unchanged: empty / health / provider / backend-error paths)
   └─ regex fallback  → hits carry source='keyword'
```

## 4. Interface contracts

### 4.1 `arail.dbspec.ensure` (new module)

```python
@dataclass(frozen=True)
class EnsureReport:
    schema: str            # "arail.db-ensure/v1"
    data_dir: str
    db_path: str
    present: bool          # the file existed before this call
    applied: list[str]     # migration filenames applied by THIS call
    pending: list[str]     # safe-forward migrations still unapplied (apply=False)
    version: int           # user_version after the call
    spec_version: int
    spec_sha256: str
    state: str             # "ok" | "created" | "updated" | "pending"
                           #   | "blocked" | "ahead" | "diverged" | "unavailable"
    detail: str            # empty iff state in {"ok","created","updated"}
    action: str            # the exact command that fixes a non-ok state; "" if none

def ensure_db(data_dir: Path, *, apply: bool = False,
              spec_dir: Path | None = None) -> EnsureReport: ...
```

**Promises**
- `apply=False` performs **zero writes** — no file creation, no directory
  creation, no `PRAGMA` writes, no `connect(create=True)`. `status` and
  `doctor` use only this mode. (A read-only check that creates the thing it is
  checking is the exact bug `doctor` hit in the previous sprint; do not
  reintroduce it.)
- `apply=True` creates the file if absent and replays **only** migrations
  classified SAFE-FORWARD, in lexical order, **one transaction per file**,
  bumping `PRAGMA user_version` inside the same transaction. A failure mid-way
  leaves the DB at the last fully-applied migration, never half-applied.
- Records `schema_version` (`spec.version`, `spec.sha256`, ISO-8601 UTC) after
  a successful apply, via the existing `db.record_version`.
- Never invokes `atlas`, `codegen.generate_all`, `reconcile.apply`, or
  `migrate` — and never writes anywhere except inside `data_dir`.
- Never raises to a caller: every failure becomes a state + detail + action.
- Idempotent: a second call with `apply=True` on a healthy DB returns
  `state="ok"`, `applied=[]`.

**Requires**
- `data_dir` is a path the caller is entitled to write (its own instance).
- `spec/schema/migrations/` is readable. If absent → `state="unavailable"`.

**On bad input** — unwritable dir → `state="blocked"`, action names
`./arailctl doctor`; corrupt SQLite → `state="blocked"` with the sqlite error;
never `os.remove` anything, ever.

### 4.2 The seamless line — exactly where it falls

| Class | Definition | `start` / `install` behaviour |
|---|---|---|
| **SAFE-FORWARD** | A checked-in migration, hash matches `atlas.sum`, index > `user_version`, and its SQL contains **no** `DROP TABLE`, `DROP INDEX`, `DROP COLUMN`, `DELETE`, `UPDATE`, or the `..._new`/`ALTER TABLE … RENAME` table-rebuild pattern | **Applied automatically**, and reported: `db: applied 1 migration (schema v2)` |
| **LOSSY** | A pending migration containing any statement above | **Never applied.** `state="blocked"`, `action="./arailctl db apply --allow-destructive"`. `start` continues with a warning; `status`/`doctor` degrade (exit 3) |
| **AHEAD** | `user_version` > number of checked-in migrations | Never touched. `state="ahead"`, action: "this database was written by a newer ARAIL — update this checkout" |
| **DIVERGED** | An already-applied migration's file hash no longer matches the ledger | Never touched. `state="diverged"`, action `./arailctl db plan` |
| **Lance reconcile** | `reconcile.plan(...)` destructive changes (metric change, dim change, drop) | **Out of scope for `ensure` entirely.** Not consulted, not applied. Remains `db apply` |
| **1.x → 2.0 import** | `dbspec.migrate` | **Never automatic.** Reads and rewrites a user's whole corpus; explicit verb only |

Rationale for the line: creating an empty schema and applying additive DDL
decides nothing on the operator's behalf and destroys nothing — it is
infrastructure. The moment a statement can remove or rewrite a row the
operator put there, it is a consent decision, and the Compiled-KB precedent
governs: refuse, name the verb, stay degraded until a human runs it.

### 4.3 `resolve_data_dirs()` — the six-roots fix

Shell (`scripts/lib/instances.sh`) and Python mirror. Returns records
`{slug, data_dir, pkb_root, origin}` where `origin ∈ {root, registry, ondisk}`:

- `root` — the root lab's `ARAIL_DATA_DIR` (always exactly one row).
- `registry` — every slug from `inst_list_slugs()`.
- `ondisk` — every `lab/instances/<slug>/` directory containing `data/` or
  `instance.env` that produced **no** registry record.

**Promise:** the union is never smaller than either input. An `ondisk` row is
a *finding*, not just a row: `status` renders it and `doctor` records it as an
info finding ("instance `finance` exists on disk with no registry record"),
because the QA-6 bootstrap reported success after reaching 2 of 6 roots and
nothing noticed. `--all-instances` consumers switch to this resolver.

**Non-promise:** this does not merge, share, or copy anything between roots.
Each `data_dir` gets its own `arail.db`. No root-level DB. No secrets are
read, written, or enumerated by any code path in this sprint.

### 4.4 `status` — additive only

`arail.status/v2` gains, per instance row and for the root lab, one object:

```json
"db": {"state": "ok", "version": 1, "spec_version": 1, "present": true, "detail": ""}
```

and each resolved root gains `"origin": "registry"|"ondisk"|"root"`.

**Contract:** additive keys only; no existing key is renamed, removed, or
retyped. `--json=instances` (the documented byte-compatible form) is
**unchanged** — it keeps emitting exactly the v1 rows array; the `db` object
appears only in `--json`/`--json=full`. `--no-probe` still makes zero HTTP
calls (the DB check is a local file read and is permitted in `--no-probe`; it
is skipped under neither flag but must complete in <50 ms/root).

**Exit codes:** unchanged 0/3/4. Mapping onto existing degraded semantics:

| DB state | Contribution |
|---|---|
| `ok`, `created`, `updated` | none |
| `pending` (safe-forward unapplied, seen by a read-only `status`) | **degraded → 3** ("a service is down") |
| `blocked`, `ahead`, `diverged` | **degraded → 3** |
| absent while that lab is *running* | **degraded → 3** |
| absent while nothing is running | no contribution — cannot promote `4` to `3`. A lab that was never started is not degraded, and `status` must still exit `4`. |

Human render: one `db:` line per lab, printed **only** when the lab is up or
the state is not `ok` — quiet when everything is fine.

### 4.5 `start` — dependent-service readiness

Order: after env-pack resolution and `.venv` check, **before** the portal
binds. Calls `ensure_db(this_instance_data_dir, apply=True)` for **its own
data dir only** — never for siblings; a `--world ai` start must not touch
`qukaizen`'s DB.

- `ok` → print nothing (quiet boot).
- `created` / `updated` → one line: `db: created lab/instances/ai/data/arail.db (schema v1)`.
  Reporting a write is required; the write is not silent.
- `blocked` / `ahead` / `diverged` / `unavailable` → **warn, name the exact
  verb, and continue booting.** Do **not** refuse. Nothing on the runtime read
  path consumes `arail.db` today (§0), so refusing to start a working lab over
  an inert store would trade a real outage for a theoretical one. This is a
  deliberate, revisitable call: **when the first runtime reader of `arail.db`
  lands, this becomes a hard readiness gate and `start` must exit non-zero.**
  Record that as a `sprints/BACKLOG.md` entry in this sprint.
- Budget: ≤150 ms added to boot on a healthy lab. `ensure_db` must not import
  `lancedb`, `atlas`, or the embedder.

### 4.6 `install` / `update`

Runs `ensure_db(apply=True)` over **every** root from `resolve_data_dirs()`,
prints a one-line-per-root summary, and reports the `ondisk` findings. This is
the "seamless" promise's real home: after `install`, every lab on the machine
has a DB.

### 4.7 `pkb_index` — the defect-B fix

New degraded code `"backend"`:

- Set by `pkb._semantic_search` when `vector_index.available()` is False, with
  the message naming the interpreter (`sys.executable`) and the fix
  (`./arailctl install`, or "you are running a non-`.venv` python").
- Cleared **only** by evidence about that code: a successful `available()`
  observation on a later call, or a full rebuild (`clear_degraded(None)`).
  A successful embed call is **not** evidence about it (BLOCK-1 discipline).
- Classified **required** in `doctor` (exit 3), same tier as `dimension` and
  `provenance` — with one carve-out: `doctor` in a CI/clean-machine context
  legitimately has no LanceDB only if LanceDB is genuinely not installed as a
  dependency; since it is a hard dep in both tiers, its absence is a broken
  environment and *should* be exit 3. Verify `.github/workflows/blueprint-smoke.yml`
  still passes; if it installs the package, LanceDB is present and this is safe.
- Surfaced through the existing `retrieval_status()` → `X-Retrieval-Status`
  header, so the portal search box already tells the truth once the code is set.

**Explicitly out of scope:** ranking, `min_score`, embedding prefixes, the
distance→score conversion, and the regex fallback's behaviour. All measured
correct.

## 5. The class check (first-class deliverable)

Three instances of "declared but never instantiated" in two releases. Two of
them passed `doctor`. The structural answer is a single assertion family:

**Every declared mechanism must name its own instantiation predicate, and
`doctor` must evaluate it.**

New module `arail/provisioning.py`, one registry, evaluated by `doctor` and
summarized by `status`:

```python
@dataclass(frozen=True)
class Assertion:
    key: str          # "relational_store", "vector_backend", "kb_gate",
                      # "embedding_provenance", "instance_registry"
    tier: str         # "required" | "info"
    declared: bool    # the spec/config says this mechanism is on
    instantiated: bool
    detail: str
    action: str       # the verb that instantiates it
```

The rule `doctor` enforces, and the reason this catches a fourth instance:

> **declared and not instantiated ⇒ a finding, never silence.**
> A mechanism may be off (not declared) or on-and-working. "On, and nothing
> has ever performed the step that makes it real" is a *third* state and is
> always reported.

Initial registrations:

| key | declared | instantiated | catches |
|---|---|---|---|
| `relational_store` | `spec/schema/*` exists | `ensure_db(apply=False).state in {ok}` per root | defect A |
| `vector_backend` | LanceDB is a hard dep | `vector_index.available()` | defect B |
| `kb_gate` | `gate_enabled()` | `approved_count > 0` **or** manifest present with an explicit empty decision | QA-6 |
| `embedding_provenance` | spec declares a model+dim | sidecar matches spec, per root | existing C4 |
| `instance_registry` | instance dirs exist on disk | every on-disk instance has a registry record | the 2-of-6 miss |

`doctor` gains a closing block that prints the table and, under `--json`,
emits `{"schema": "arail.provisioning/v1", "assertions": [...]}`. Adding a
mechanism to ARAIL 2.1 without registering an assertion should feel like an
omission — the registry is the checklist.

**Anti-goal:** this is not a health monitor and must not run anything
expensive. Every predicate is a file-stat, an import check, or a `PRAGMA` —
no embeds, no HTTP, no index builds.

## 6. Failure modes

| # | Failure | Detection | Recovery |
|---|---|---|---|
| F1 | `atlas` absent on a user machine; a boot path tries `db apply` | `ensure` never imports `atlas`; unit test asserts `arail.dbspec.atlas` is not in `sys.modules` after `ensure_db` | Design forbids it; test is the guard |
| F2 | Migration replay fails halfway, leaving a half-schema | Per-file transaction; `user_version` bumped inside the txn | Rollback leaves the last good version; state `blocked`, action `db plan` |
| F3 | A lossy migration is auto-applied on boot | Static SQL classifier + test corpus of lossy statements | Classified LOSSY → never applied; `start` warns, names `db apply --allow-destructive` |
| F4 | `start` writes into a sibling World's `data_dir` | `ensure_db` called with exactly one dir; test asserts sibling dirs' mtimes unchanged | Per-instance scope is enforced at the call site and tested |
| F5 | A root-level or shared DB creeps in | Test: no `arail.db` at `lab/` or repo root after install+start | Fail the test; `resolve_data_dirs` never emits a parent of another row |
| F6 | `status` creates the DB it is checking (previous sprint's bug) | `apply=False` is write-free; test snapshots the tree before/after `status` and `doctor` | Contract 4.1; test |
| F7 | `status` JSON change breaks a script | `--json=instances` byte-compatibility test against a committed golden | Additive-only rule |
| F8 | `status` exit-code contract shifts (e.g. `4` becomes `3`) | Table-driven exit-code test over all state combinations | A DB-absent-and-nothing-running case must still exit `4` |
| F9 | `status` gets slow on 6 roots | Timing assertion (<2 s total budget, existing) | Local reads only; no HTTP in the DB check |
| F10 | `start` becomes chatty | Test: healthy second `start` prints no `db:` line | Report only on change or on a problem |
| F11 | Unregistered on-disk instance silently skipped again | `resolve_data_dirs` union test with an empty `registry.d` and 5 on-disk dirs | Must return 6 rows; `origin=ondisk` reported |
| F12 | `backend` degraded code is cleared by unrelated evidence | Test: successful `embed_query` does not clear `backend` | Code-scoped clear discipline |
| F13 | Fix for B adds an embed/network call to the search path | Test: monkeypatch `urllib.request.urlopen` to raise; a search with a populated index performs exactly one embed (the query) and zero index builds | Egress-honesty constraint |
| F14 | `backend` promotion to required breaks CI's `doctor` exit 0 | Run `blueprint-smoke` workflow locally / assert LanceDB present in both tiers | Demote to info if CI legitimately lacks it, and say so in the finding |
| F15 | The DB comes up but is inert; users believe a feature landed | Docs + `provisioning` assertion detail explicitly states "no runtime reader yet" | Honesty in the finding text; BACKLOG entry for the readiness-gate promotion |
| F16 | `PRAGMA user_version` collides with a future Atlas-managed concept | `db plan` on a freshly ensured DB reports **in sync** (test) | If it drifts, switch to a spec'd `_arail_migrations` table |
| F17 | Two processes ensure the same DB concurrently | `busy_timeout=5000` + per-file txn; test two concurrent `ensure_db(apply=True)` | Last writer wins at the same version; both end `ok`, no corruption |
| F18 | Corrupt/truncated `arail.db` | `sqlite3.DatabaseError` on connect | `state="blocked"`, detail names the file; **never auto-delete** |
| F19 | `ensure` writes outside `data_dir` (e.g. regenerates code into `src/`) | Test: repo tree hash unchanged after `install` on a temp lab | `ensure` calls no codegen |

## 7. Test strategy

Every row above has a test here. QA executes this as written.

### Unit — `arail.dbspec.ensure`
1. `ensure_db(tmp, apply=False)` on an empty dir → `state="pending"` (or
   `"unavailable"` if no migrations), `present=False`, and **the directory is
   byte-identical afterwards** (F6).
2. `ensure_db(tmp, apply=True)` on an empty dir → `state="created"`,
   `arail.db` exists, `user_version == len(migrations)`, `schema_version` row
   present with `spec.sha256`.
3. Idempotence: second `apply=True` → `state="ok"`, `applied == []`, file
   mtime of the DB may change but `user_version` does not.
4. Schema fidelity: after `ensure_db(apply=True)`, `atlas schema diff` (dev-only
   test, skipped if `atlas` absent) reports **no statements** — proves the
   Atlas-free replay reproduces the declared schema (F16).
5. Lossy classifier, table-driven: `DROP TABLE x;`, `DROP COLUMN`,
   `DELETE FROM worlds;`, `UPDATE worlds SET ...`, the
   `CREATE TABLE new_x … INSERT … DROP TABLE x … RENAME` rebuild pattern → all
   LOSSY; pure `CREATE TABLE`/`CREATE INDEX`/`ALTER TABLE … ADD COLUMN` →
   SAFE-FORWARD (F3). Include a lossy statement inside a comment and inside a
   string literal to prove the classifier does not false-positive… and if it
   does false-positive, that is acceptable (fail closed) — assert it fails
   *closed*, never open.
6. `user_version` ahead of the ledger → `state="ahead"`, zero writes.
7. Mutating an already-applied migration file's bytes → `state="diverged"`,
   zero writes.
8. Failure isolation: a migration file with a syntax error at position 2 of 3
   → migration 1 applied, `user_version == 1`, `state="blocked"` (F2).
9. `ensure_db` leaves `sys.modules` free of `atlas`, `lancedb`, and
   `arail.dbspec.embed` (F1, perf).
10. Unwritable `data_dir` (chmod 0500) → `state="blocked"`, no exception.
11. Truncated/garbage `arail.db` → `state="blocked"`, file untouched (F18).
12. Concurrency: two threads/processes `apply=True` on one dir → both finish,
    DB valid, `user_version` correct (F17).

### Unit — `resolve_data_dirs`
13. Empty `registry.d` + 5 on-disk instance dirs + root → **6 rows**, five with
    `origin="ondisk"` (F11). This is the operator's exact machine state.
14. Registry record with no on-disk dir → row present, flagged.
15. No row is a parent directory of another row (F5).

### Unit — `pkb_index` / `pkb` (defect B)
16. Monkeypatch `vector_index.available()` → False; `_semantic_search` returns
    `[]` **and** `embedding_status()` returns `(False, ...)` with the message
    naming `./arailctl install`. *This is the regression test for the exact
    measured symptom.*
17. With `backend` set, a successful `embed_query` does **not** clear it (F12).
18. A full `index_all` success clears every code including `backend`.
19. `retrieval_status()` / `/api/pkb/search` stamps `X-Retrieval-Status: degraded`
    in the state of test 16.

### Integration
20. **Fresh-clone → setup → start (the seamless case).** In a temp `LAB_ROOT`:
    run the install path, then `start`; assert `<data_dir>/arail.db` exists,
    `status --json` reports `db.state == "ok"`, exit `0`, and **no `arail.db`
    exists at `lab/` or the repo root** (F5).
21. **The win condition, end to end.** With a seeded PKB (≥20 pages, real
    embedder or a deterministic stub at 768 dim) and a Compiled-KB manifest
    approving a subset: `pkb.retrieve_for_agents("how does attention work")`
    returns ≥1 hit with `source == "semantic"` and `empty_reason is None`, and
    the top hit is the attention page. Then re-run under
    `available()`→False and assert the *honest* failure: 0 semantic hits,
    `empty_reason` set, `embedding_status()` degraded — **not** a silent
    keyword result claiming health.
22. **Buddy-level check.** Drive the agent-facing entry point Buddy actually
    calls (`search_for_agents`) with three natural-language questions and
    assert semantic hits under the gate.
23. `start` on a lab whose DB is already `ok` prints no `db:` line (F10);
    `start` on a lab with no DB prints exactly one creation line.
24. `start --world ai` leaves every sibling instance's `data_dir` mtime and
    content unchanged (F4) — and touches no `secrets.env` anywhere.
25. `install` over the 6-root fixture creates 6 DBs, one per data dir.
26. `status` and `doctor` over the same fixture produce a byte-identical tree
    before/after (F6, F19).

### Regression / contract
27. `status --json=instances` matches the committed golden byte-for-byte (F7).
28. Exit-code matrix (F8): {nothing running, root up, world up} × {db ok, db
    absent, db blocked, db ahead} → asserted `0`/`3`/`4`. Specifically:
    nothing running + db absent ⇒ **4**; world up + db blocked ⇒ **3**.
29. `arail.status/v2` schema test: all pre-existing keys present and same
    types.
30. `doctor` on a healthy clean machine still exits `0`; `doctor` with a
    declared-but-uninstantiated mechanism exits `3` for `required` tiers.
31. Provisioning assertions (§5), one test per row of the table, each
    constructed in the declared-not-instantiated state and asserted to
    produce a finding. **Including a synthetic "instance four"**: register a
    dummy mechanism declared-and-not-instantiated and assert `doctor` reports
    it — proving the mechanism generalizes rather than hardcoding three
    known bugs.

### Performance
32. `ensure_db(apply=False)` < 20 ms per root; `status` over 6 roots stays
    within the existing <2 s budget (F9).
33. `start` boot-time delta on a healthy lab ≤ 150 ms (measure 5 runs, report
    median).

### Security / honesty
34. No code path in this sprint reads, writes, copies, or enumerates any
    `secrets.env` — grep-based assertion over the diff plus a runtime test
    that a sibling's `secrets.env` is never opened (audit via `strace`-free
    approach: monkeypatch `open`/`Path.open` in the integration harness).
35. Egress: with `LAB_MODE=airgapped`, `status`, `doctor`, `install`, and
    `start` perform **zero** non-loopback network calls; a search performs at
    most one loopback embed call and zero index builds (F13).
36. `ensure` never executes SQL from anywhere but `spec/schema/migrations/`;
    a migration file with a path-traversal-ish name is ignored (only
    `^\d{14}_[a-z0-9_]+\.sql$` is eligible).

## 8. Tech debt

**Added**
- A second schema-application path (`ensure` replay) alongside `db apply`
  (Atlas). They must not diverge — mitigated by test 4 (`atlas schema diff`
  clean after replay), but that test is dev-only/skipped where `atlas` is
  absent, so CI does not fully guard it. **File a follow-up:** add an
  `atlas`-bearing CI job that runs test 4.
- `PRAGMA user_version` as the migration cursor is a convention with no
  in-band documentation for a DBA reading the file. Mitigated by module
  docstring; still debt.
- `start` warns-and-continues on a blocked DB rather than gating. Deliberate
  (§4.5) and must be promoted to a hard gate when the first runtime reader
  lands. **File a BACKLOG entry.**
- `resolve_data_dirs` exists in shell and Python — two implementations of one
  rule. Mitigated by a shared fixture test asserting they agree; still debt.

**Added — discovered during review, not anticipated at design time**

These were folded in by the architect after review rounds 1 and 2 (REVIEW.md
required action 5). Each is debt the design's own wording hid.

- **§4.2's phrase "hash-verified committed migration" concealed two separate
  mechanisms.** It was implemented first as a post-apply self-consistency
  sidecar only, which verifies nothing on a fresh clone — the exact case this
  sprint serves. Ledger verification against `atlas.sum` (pure Python,
  `b64(sha256(filename + content))`) and the sidecar are *both* required and
  are not substitutes. Future spec language must name each gate separately
  rather than collapsing them into one adjective.
- **An allowlist classifier is only as good as its statement splitter.**
  `_split_statements` splits naively on `;`, so a semicolon inside a string
  literal or comment fragments a statement. Every fragmentation case observed
  fails *closed* (a fragment stops matching the allowlist), so this is
  tolerable — but it is load-bearing safety resting on an accident of
  tokenization, not on a parser. If the allowlist ever grows a keyword whose
  statements legitimately contain semicolons (`CREATE TRIGGER … BEGIN … END;`
  is already on it), that accident stops holding.
- **Allowlisting a leading keyword does not bound what the statement does.**
  `INSERT INTO` admits `ON CONFLICT … DO UPDATE SET` (an upsert that rewrites
  existing rows) and `CREATE TRIGGER` admits a body containing `DELETE`/
  `UPDATE` that executes later. The safe/lossy line is defined over *effects*;
  the classifier approximates it over *prefixes*. That gap is permanent debt
  for as long as the classifier is regex-based.
- **`ALTER TABLE … ADD COLUMN … REFERENCES … ON DELETE CASCADE` classifies
  SAFE-FORWARD and installs a future cascade** (round 3). Second-order — the
  cascade only fires on a later `DELETE`, which is itself LOSSY and never
  auto-applied — but it is the same shape of reasoning BLOCK-4 punished:
  a prefix that does not bound a statement's eventual effect on existing rows.
- **Two surfaces independently decide what "degrading" means** — `status.sh`'s
  `_DB_DEGRADING_STATES` and `ensure.main()`'s exit-code set. Reconciled by
  hand (ASK-2); nothing enforces that they stay reconciled.
- **`tests/cli/status_driver.sh` self-skips without a `.venv`, so the entire
  CLI-contract layer is invisible in CI and in any worktree.** This sprint
  added four scenarios to it, increasing what is hidden. It hid a broken
  assertion in round 1 (T28b/T28c) and a real behaviour regression in round 2
  (T10 flipped 0→3 undetected). Every finding at this layer in both rounds was
  found by hand-running the driver against an external venv. **This is now the
  highest-value untracked debt in the sprint** — file it.
- **`tests/cli/lib.sh`'s `make_fake_venv` symlinks `$fake/.venv/lib` straight
  into the real venv's `site-packages`.** Any scenario that writes, renames, or
  deletes through that path mutates the operator's actual installation. The
  builder drafted and discarded exactly such a scenario before running it — a
  near-miss, recorded here so the next person does not complete the mistake.
  The hazard is currently documented in a comment at the *end of
  `status_driver.sh`*, which is not where someone editing `make_fake_venv`
  will be standing; the note belongs at the symlink site in `lib.sh`, and the
  helper should be hardened so a fake venv is not writable into the real one.

**Repaid**
- `arail.db` goes from "specified, zero instances on any machine" to
  "created on every lab, verified by `status`".
- The silent `available()==False` branch — a three-release-old honesty hole in
  the product's most-trusted surface — is closed.
- The `--all-instances` 2-of-6 blind spot is closed for every consumer of
  `resolve_data_dirs`.
- The "declared but never instantiated" class gets a standing check instead of
  a fourth incident.

**Net:** still negative (debt repaid), but less so than designed — the review
rounds converted three pieces of assumed-correct design into named debt.
Follow-up tickets required before merge: the Atlas CI job, the
`start`-hard-gate promotion, a CI-runnable path for `status_driver.sh`, and
hardening `make_fake_venv` against writes into the real venv.

## 9. Recommended implementation order

1. `arail/dbspec/ensure.py` + its unit tests (1–12). No callers yet. This is
   the whole risk surface; land it proven.
2. `resolve_data_dirs` in `scripts/lib/instances.sh` + Python mirror + tests
   (13–15). No behaviour change to existing callers yet.
3. **Defect B fix**: the `backend` degraded code in `pkb.py`/`pkb_index.py` +
   tests 16–19, 21, 22. Small, independent, and it closes the honesty hole
   first — ship-blocking value lands early.
4. Wire `install`/`update` to `ensure_db(apply=True)` over resolved roots
   (test 25).
5. Wire `start` (tests 20, 23, 24, 33).
6. `status` DB line + JSON + exit-code mapping (tests 27–29, 32).
7. `arail/provisioning.py` + `doctor` block (tests 30, 31).
8. Docs: `docs/cli.md` (status `db` object, `origin` field, exit-code table
   unchanged; `install` and `start` behaviour), a note that `arail.db` has no
   runtime reader yet, `CHANGELOG.md`, and the two BACKLOG entries.
9. Switch remaining `--all-instances` consumers to `resolve_data_dirs`.

## 10. CI findings — architect rulings (2026-08-10, PR #181)

Two CI failures on the merge-blocking driver wiring. Both real, neither a
flake. Rulings below; both block merge, both are fixable in this sprint.

### Finding 1 — `T3/daemon-a` fails on Linux: gate it, and file the real gap it sits next to

**The coordinator's reading is confirmed on the "why now," and corrected on the
"what."** This is not a latent product defect that CI just caught. It is a test
that encodes a macOS-only fixture with no platform gate:

- `scripts/lib/instances.sh:464` — `daemon_active()` opens with
  `[[ "$(uname -s)" == "Darwin" ]] || return 1`.
- `scripts/install-daemon.sh:35` — refuses on non-Darwin outright:
  *"launchd supervision is macOS-only."*
- `T3/daemon-a` stubs `launchctl` and plants
  `$FAKE_HOME/Library/LaunchAgents/io.arail.portal.plist`.

On Linux the scenario cannot pass and the product is behaving correctly:
launchd supervision cannot be installed there, so "not daemon" is the truth.

**Ruling: platform-gate `T3/daemon-a` and `T3/daemon-b` on Darwin, and file the
adjacent gap.** The gate is legitimate here for one specific reason, and the
distinction is worth stating because it is the one this sprint has been
enforcing all along: **a platform gate on a test is legitimate when the product
is gated at the same boundary and says so; it is illegitimate when the gate is
chosen to turn a red test green.** The product gate (`install-daemon.sh:35`,
`daemon_active():464`) predates the test and is documented. Nothing is being
hidden.

Two conditions on the gate:

1. **The skip must be loud.** The driver must print which scenarios it skipped
   and why, so a Linux CI run never reports full coverage it did not have.
   A silent skip would re-create this sprint's own defect class at the test
   layer.
2. **File the real gap it sits beside.** `scripts/gentoo-bootstrap.sh:182`
   installs OpenRC services (`rc-update add arail-portal default`), so ARAIL
   *can* be supervised on Gentoo — and `daemon_active()`'s Darwin-only guard
   makes `status` report `supervision.mode: "foreground"` for a lab that is
   actually supervised. That is a genuine, pre-existing observability defect,
   out of scope here (this sprint is persistence, not supervision), and it must
   be a filed ticket rather than a footnote. It is also the reason the ruling
   above is scoped to "gate the launchd scenarios," not "Linux has no
   supervision."

### Finding 2 — `doctor` exits 3 after `setup`: fix the lab, not the detector

**The check is correct and is reporting the truth.** After `setup`, that lab's
relational store genuinely is not instantiated. The golden path is
`setup → doctor → start`, and `ensure_db` was wired into `install` and `start`
only.

**Ruling: option (a) — wire `ensure_db` into `setup.sh`. Reject (b). Reject (c).**

- **(b), making `relational_store` non-degrading before first start, is
  rejected outright.** It is severity-tuning the one check whose entire purpose
  is "declared and not instantiated ⇒ always a finding," and it would need a
  lifecycle marker to know what "before first start" means — more
  written-and-never-read state, when QA has already flagged
  `.arail_ensure_state.json` as this sprint's own defect class appearing inside
  it. Two instances is a pattern.
- **(c), splitting "never provisioned" from "provisioned and broken" at the
  tier layer, is a real option and is rejected as unnecessary here.** It is not
  new severity-tuning — `doctor` already has exactly this convention (`ttyd`,
  `code-server`, "no model installed" are `info`, degrading only under
  `--strict`), and `Assertion.tier` already exists to express it. But adopting
  it *would* mean a lab whose `setup` silently failed to create the DB reports
  `pending` at `info` and exits 0 — defect A, invisible again. With (a) in
  place, (c) buys nothing and costs exactly the blindness this sprint removed.
  Keep `relational_store` at `required`.
- **(a) is the correct fix because the gap is real and it is mine.** `CLAUDE.md`
  documents `./arailctl setup && ./arailctl start` as the first-run path, and
  the operator's ruling was "make this seamless and easy." §4.5 covered `start`
  and §4.6 covered `install`; **there is no §4.6b for `setup`, and that is a
  design omission in this document, not builder drift.** A user following the
  documented first-run sequence currently gets a degraded `doctor` between the
  two commands. That is the headline ruling failing, and the answer is to fix
  the lab, not to mute the reporter.

**Constraints on the `setup` wiring** (it is a call site, not a new mechanism —
no new policy, no new code path):

1. Reuse the existing shim verbatim:
   `python -m arail.dbspec.ensure <data_dir> --apply --quiet-ok`. Same
   allowlist classifier, same `atlas.sum` ledger verification, same
   safe-forward-only line. **The create-vs-lossy distinction holds identically
   in `setup`** — nothing about the caller changes what may be auto-applied.
2. Sequence it **after the deps phase** (the package must be importable) and
   after the lab directories exist.
3. Drive it from `resolve_data_dirs()`, not a hardcoded root, so re-running
   `setup` on an existing multi-World lab behaves like `install`.
4. **`setup` must not fail on this.** Warn, name the verb, continue — matching
   `install` (degrades, never hard-fails) and `start` (warns, continues), and
   honoring the standing "setup never fails on this" promise.
5. No network, no embedder, no Atlas — `ensure_db` is local file + SQLite only,
   so the airgapped default is unaffected.

"`setup` becomes a writer" is not a real cost: `setup` already creates the
`.venv`, pip-installs, writes `.env`, creates the lab tree, and pulls models.
Creating an empty schema inside a directory it just made is the least invasive
thing it does.

### Merge status

Both findings block merge. Neither is a follow-up sprint: Finding 1 is a
platform gate plus a filed ticket, Finding 2 is a call site reusing a shim that
has already been through three review rounds. Two tickets to file: the
OpenRC/`daemon_active` supervision gap, and (still open from REVIEW3) the
CI-runnable driver path, which this very episode is the argument for.

### Finding 3 — `kb_gate: MISSING` on a clean CI checkout: the predicate is under-scoped

**Coordinator's read confirmed.** `check_kb_gate`
(`src/arail/provisioning.py:162-186`) decides on `len(approved_paths()) > 0`
alone and ignores `compiled_kb.gate_state()` entirely — even though QA-6 built
that function's four states (`off` / `unbootstrapped` / `empty` / `populated`)
to stop exactly this collapse. A lab with zero source documents and no World
ever mounted reports identically to the operator's `ai` World with 351
approvable pages and none approved.

**Why this is not the severity-tuning I rejected for `relational_store`, and
why "fix the workflow" is not available here.** The asymmetry is the whole
ruling:

- `relational_store` can be instantiated **unconditionally, by the machine** —
  an empty schema decides nothing on the operator's behalf. So the fix was to
  make `setup` create it (§10 Finding 2).
- `kb_gate` cannot. Approving knowledge is a **consent decision**, and the
  2026-08-09 compiled-kb-bootstrap sprint ruled that it must never happen
  automatically. Making `blueprint-smoke.yml` approve something before calling
  `doctor` would be CI fabricating consent — the precise thing that sprint
  forbade. There is no workflow fix that isn't a policy violation.

It follows that on a lab with **nothing that could be approved**, "not
instantiated" is not a defect — it is the correct resting state. Reporting it
as a required finding makes `doctor` un-greenable on any clean machine, which
teaches operators to ignore it.

**Ruling: fix the predicate, keep the tier.** Do not touch `required`; do not
add a lifecycle marker. Use the `declared` term, which exists in `Assertion`
for exactly this and makes `finding = declared and not instantiated` come out
right on its own:

| Gate condition | `declared` | `instantiated` | Result |
|---|---|---|---|
| gate off (`ARAIL_APPROVED_ONLY=off`) | False | — | not a finding (unchanged) |
| no corpus: `approved_count + pending_count == 0` | **False** | — | **not-applicable, not a finding** |
| corpus exists, nothing approved/live | True | False | **required finding — loud** |
| anything approved and live | True | True | OK |

The boundary the coordinator named is preserved by construction: the operator's
351-approvable/0-approved World has `pending_count > 0`, so it stays `declared`
and stays loudly degraded. A mounted World with unapproved pages cannot go
quiet under this rule.

Two constraints on the implementation:

1. Call `gate_state(cheap=False)`. `cheap=True` returns `pending_count = -1`
   ("not computed"), and a `-1` must be treated as **unknown ⇒ assume a corpus
   exists ⇒ stay `required`**. Fail loud on ignorance, never quiet. `doctor` is
   not a hot path; the tree walk is affordable.
2. The `detail` string must distinguish the two zero states in words, not just
   in the exit code — "no corpus to approve yet" vs "N pages await approval".

In scope for this sprint: `provisioning.py` is this sprint's own §5 deliverable
and this is a predicate correction inside it, not new surface.

### Finding 4 — `stat -f` in `qa_db_seamless_driver.sh`: portable fix, agreed

**No objection — the coordinator's call is right, and the contrast with
Finding 1 is the reason.** `T3/daemon-a` was gated because the *product* is
platform-gated at the same boundary and says so. Here the asserted property —
a sibling instance's `secrets.env` is never read, written, or copied — is
platform-independent and is a **security** invariant (CLAUDE.md: per-instance
secrets are never shared or auto-copied). Gating it would make a security
assertion vacuous on the platform most likely to run CI. `stat -f '%Sp %m'` is
BSD; GNU wants `stat -c '%A %Y'`.

One addition worth making while it is open: the driver should **assert its own
`stat` invocation produced output** before comparing before/after. As written,
the comparison is assembled from a `find -exec` pipeline, so a `stat` that
errors still yields a non-empty string from the `shasum` half — the permission
half of the check (that `0600` was preserved) can silently drop out while the
assertion still appears to pass. That is this sprint's own defect class
appearing for a third time, in a test. A one-line non-empty guard closes it.

### Merge status (updated)

Finding 3 blocks merge and is a small in-sprint predicate fix, not a follow-up
sprint. Finding 4 blocks merge and is a straight portability fix. Neither
requires re-opening the persistence design.
