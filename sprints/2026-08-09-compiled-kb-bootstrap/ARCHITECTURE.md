# Architecture: Compiled-KB bootstrap (QA-6)

**Date:** 2026-08-09
**Sprint:** [SPRINT.md](./SPRINT.md)
**Spec:** no VISION.md — think phase skipped by ledger decision (win condition not in question)

## Restatement

ARAIL ships a human-approval gate between the raw PKB corpus and what agents may
retrieve. The gate is on by default, it fails closed, and it has never been
opened: no `compiled/kb/approved.json` exists on any of the six PKB roots, so
`approved_paths()` returns `set()`, so `pkb.search()` short-circuits to `[]`
before searching, so `search_for_agents()` has returned nothing for every query
on every World since the gate shipped. The code is behaving exactly as written —
the defect is that nothing ever performs the first approval, and nothing tells
anyone that this is why the lab looks empty. This sprint decides the bootstrap
policy, makes the empty state legible to every caller, backfills the six
existing roots, and gives the operator a bulk-approve path that does not require
351 individual clicks — without loosening the gate for anything that is not
sealed World vocabulary.

## Decision: policy (3) as the floor, plus (1) re-hooked to mount time

**Chosen:** keep fail-closed (option 3), add a loud empty state and a bulk-approve
path, **and** auto-approve exactly one narrow class — per-term pages
reconciled against a **seal-verified** World bundle's `terms.json` (option 1),
hooked at **mount**, not at seal.

This ratifies the operator's lean with one correction the operator should read
before the builder starts.

### Why not (2), bootstrap-open

Bootstrap-open ("an empty manifest means pass-through to raw") is the option
that ships fastest and is the one to refuse. It makes the gate's security
property depend on a state that is indistinguishable from failure. Today a
corrupt manifest, an unreadable `compiled/` dir, a `_load_json` exception, and a
World-switch that danglinged every pointer all converge on the same value:
`approved_paths() == set()`. Under (2) every one of those becomes "expose the
entire raw corpus, including `notes/`, `inbox/`, `conversations/`, and agent
output, to whatever agent asked." That is a fail-**open** path reachable by
file corruption. `prune_dangling` can already legitimately empty a live
manifest. Rejected on the fail-closed constraint alone.

### Why (3) alone is not sufficient

(3) is correct and insufficient. It is what the code already does; adding a
banner and a bulk-approve button leaves the *default* experience of a
freshly-mounted World as "Buddy knows nothing until you click a button you have
not been told about." The `ai` World is 351 pages; `video-games` is 81. A
first-run experience whose first required act is a bulk approval of content the
user did not author and cannot meaningfully review is a consent ritual, not
consent. It converts the gate into a nag, and the predictable operator response
is `ARAIL_APPROVED_ONLY=off` — which the sprint explicitly forbids becoming the
de facto default. (3) alone *causes* the outcome it is meant to prevent.

### Why (1), scoped, is legitimate — and where the sprint's wording is wrong

A World bundle's term pages are not unvetted candidates. They passed DaC's
compile-time gate (sourced, closed, categorized), they are cryptographically
sealed, and `world_mount.mount()` refuses on `SealMismatch` before touching
disk. The human act of choosing and mounting a sealed bundle *is* an approval
decision, made once over a curated set, rather than 351 times over its members.
Approving them individually adds no information.

**The correction:** the sprint wording says "auto-approve at seal time." That
hook is unreachable for the case that matters. Term pages do not exist at seal
time — `world_mount._write_term_pages()` synthesizes them from `bundle.terms`
during `_stage_files()` at **mount**. Sealing writes bundle JSON; the four
bundles already in `lab/worlds/` are sealed and would never re-enter a seal
path. A seal-time hook would fix zero of the six roots. The hook must be
mount-time, and the historical roots need an explicit backfill (below).

### The scope line, stated as an invariant

Auto-approval admits a path if and only if **all** of:

1. it matches `sources/world-<slug>/terms/<term-slug>.md` for the slug of the
   World being mounted, and
2. `<term-slug>` is in `{_safe_term_slug(t["slug"]) for t in bundle.terms}`
   from the bundle whose seal just verified, and
3. `_is_candidate(rel)` already passes, and
4. the file exists and is readable.

Everything else — `notes/`, `inbox/`, `conversations/`, `agents/**` (research,
experiments, synthesis, recommendations, dreams), `sources/scout/`,
`sources/seeds/`, `research/`, `skills/`, `SKILL.md`, `world-<slug>.md`, and any
hand-dropped `.md` inside `terms/` that is not in `terms.json` — stays behind
the review queue. Condition (2) is doing real work: it means dropping a file
into a staged `terms/` directory does not get it approved.

### On debt-finance specifically

I read the corpus rather than assuming. `lab/instances/debt-finance/pkb` holds
42 term pages under `sources/world-debt-finance/terms/`, which are public
domain vocabulary with `Source:` lines pointing at irs.gov and equivalents
(verified on `401k-loan.md`). The operator's own record ("debt-finance World is
public — its glossary is domain vocabulary, not personal data") agrees. The
personal financial material is not in `terms/`; it arrives via `notes/`,
`inbox/`, `conversations/`, and agent output — none of which auto-approval can
reach under the invariant above.

So: uniform auto-approval, including debt-finance's glossary, with two
escape hatches and a hard test. The security claim QA proves is not "the
debt-finance World stays gated" in the vague sense — it is the precise,
falsifiable statement:

> After mount and after bootstrap, on the debt-finance root, `approved_paths()`
> contains only paths matching `sources/world-debt-finance/terms/<s>.md` where
> `<s>` appears in the bundle's `terms.json`; and a planted personal note is
> neither approved nor returned by `search_for_agents()` for a query that
> matches its text verbatim.

Escape hatches (both fail toward *less* approval):

- `ARAIL_AUTO_APPROVE_WORLD_TERMS=off` disables the mount hook globally.
- A sentinel file `compiled/kb/no-auto-approve` under a PKB root disables it for
  that root. Presence disables; any read error on the check is treated as
  present (disabled). This is the per-World opt-out and it is deliberately a
  file, not a spec field, so it lives with the data and survives a re-mount.

If the operator disagrees with including debt-finance, dropping that one
sentinel file is the whole remediation — no code change, no re-review.

## Assumptions

- A sealed, seal-verified bundle's `terms.json` is a human-curated artifact and
  mounting it is an intentional operator act. If mounting ever becomes
  automatic or agent-triggered, this design's premise fails and the mount hook
  must be revisited.
- `verify_seal()` is trustworthy for content integrity. Auto-approval runs only
  after `mount()` has already refused on `SealMismatch`; we add no new trust.
- Term pages contain no personal data on any World. True today by inspection of
  all four catalog bundles; enforced going forward only by the fact that terms
  come from a DaC-compiled bundle, not from lab runtime writes.
- `compiled_kb.approve()` remains the single write path. The bootstrap adds no
  second way to mutate the manifest, so its candidacy checks, traversal
  rejection (`_clean_rel`), existence check, and sha256 stamping apply
  unchanged.
- The six roots enumerated in SPRINT.md are the complete set on this machine;
  the backfill must nonetheless discover roots rather than hardcode them.
- `PKB_ROOT` resolution is per-process and correct for the running instance;
  the backfill CLI operates on one root at a time unless `--all-instances`.
- Callers listed in SPRINT.md tolerate an *additive* dict key. No caller
  destructures search results positionally.

## Data flow

**Two mount-family entry points, not one** (corrected in review round 1,
BLOCK-1). `mount()` fires only on a lab that has never mounted a World —
`portal/world_routes.py:452` is `wm.swap(catalog) if wm.current_mount() is not
None else wm.mount(catalog)`, so every World *switch*, and every reseal via
`_reseal_and_swap` (world-forge, term editor, librarian), goes through
`swap()`. Both verify the seal and refuse on `SealMismatch` before staging, so
both carry a real `seal.computed_sha256` and both stamp `world-seal:<sha12>`.
The hook must exist in both or the mechanism fires roughly once per lab.

Placement is ordering-sensitive in both: the hook goes **after**
`_write_record` and **before** `_refresh_kb_surfaces`, because
`_refresh_kb_surfaces` → `_prune_swept_approvals` → `prune_dangling` reconciles
the manifest against disk. Approve-then-prune drops the outgoing World's swept
approvals and keeps the incoming World's (their files exist). Prune-then-approve
would be a no-op prune and would let corpses accumulate on the hot path.

```
                       ┌──────────────────────────────────────┐
  sealed bundle ──────►│ world_mount.mount()   [first mount]  │
  (lab/worlds/<slug>)  │ world_mount.swap()    [every switch] │
                       │  1 load + verify_seal  (refuses here)│
                       │  2 _stage_files → _write_term_pages  │
                       │  2b _sweep_other_worlds              │
                       │  3 index                             │
                       │  4 write pointer                     │
                       │ ►4.5 auto_approve_world_terms(       │──┐
                       │        verified_seal=True)           │  │
                       │  5 _refresh_kb_surfaces → prune       │  │
                       └──────────────────────────────────────┘  │
                                                                 │ reconcile
   ./arailctl pkb bootstrap [--dry-run] [--world <slug>|--all-instances]
        │  (explicit verb; also called once by `install`;         │ terms.json
        │   verified_seal=False → stamps "world-terms:<sha12>",   │ ∩ staged
        │   because resolve_world_bundle() does NOT verify_seal)  │ terms/*.md
        └───────────────────────────────────────────────────────► │
                                                                 ▼
                                        compiled_kb.approve(paths, approver=
                                          "world-seal:<sha12>")
                                                 │  (unchanged write path:
                                                 │   _clean_rel, _is_candidate,
                                                 │   is_file, sha256, atomic tmp+replace)
                                                 ▼
                                 lab/…/pkb/compiled/kb/approved.json
                                   {schema, updated_at, bootstrapped_at, items}
                                                 │
                    ┌────────────────────────────┼────────────────────────────┐
                    ▼                            ▼                            ▼
        approved_paths() (unchanged,   gate_state()  (NEW, read-only)   list_pending()
        fail-closed to set())          {enabled, manifest_present,      (unchanged)
                    │                   approved_count, pending_count,
                    │                   state}
                    ▼                            │
     pkb.search(approved_only=True)              │
                    │                            │
                    ▼                            ▼
     pkb.retrieve_for_agents()  ──► {"hits": [...], "gate": <gate_state>,
                    │                "empty_reason": null|"gate_empty"
                    │                 |"no_match"|"gate_off_no_match"}
                    ▼
     pkb.search_for_agents()  ──► list (unchanged shape, back-compat)
                    │
    ┌───────────────┴────────────────┬─────────────────┬──────────────────┐
    ▼                                ▼                 ▼                  ▼
 lab_brain (Buddy)             researcher        goal drafter        debt advisor
```

Manifest state machine:

```
  NOFILE ──bootstrap/mount──► PRESENT+EMPTY ──approve──► PRESENT+POPULATED
  (never bootstrapped)        (bootstrapped,             (agents have truth)
                               nothing qualified)
        │                            │                        │
        └──── unreadable/corrupt ────┴────────────────────────┘
                        ▼
                  reads as PRESENT? no → treated as NOFILE-with-error;
                  approved_paths() == set() in every case (fail-closed)
```

## Interface contracts

### `compiled_kb.manifest_present(pkb_root=None) -> bool`

- **Promises:** True iff `compiled/kb/approved.json` exists *and* parsed to a
  dict/list shape. False on missing, unreadable, or unparseable.
- **Requires:** nothing. Never raises.
- **Bad input:** a corrupt manifest returns False — deliberately conflated with
  "never bootstrapped", because both mean "do not tell the user the gate is
  merely empty; tell them to run bootstrap/doctor."

### `compiled_kb.gate_state(pkb_root=None) -> dict`

```
{"schema": "arail.kb-gate/v1",
 "enabled": bool,             # gate_enabled()
 "manifest_present": bool,
 "approved_count": int,       # len(approved_paths())
 "live_count": int,           # approved minus dangling
 "pending_count": int,        # pending_paths() — see perf note
 "state": "off" | "unbootstrapped" | "empty" | "populated",
 "hint": str}                 # one operator-facing sentence, may be ""
```

- `state` derivation, in order: `not enabled` → `"off"`; `not manifest_present`
  → `"unbootstrapped"`; `live_count == 0` → `"empty"`; else `"populated"`.
- **Promises:** never raises; every field has a defined value on total failure
  (`enabled` from env, everything else 0/False, `state="unbootstrapped"`).
- **Requires:** callers must not treat `"off"` as a security-relevant success.
- **Perf:** `pending_count` walks the tree. `gate_state(..., cheap=True)` skips
  it and reports `pending_count = -1` (meaning "not computed"). Hot callers
  (`lab_brain` per turn, `researcher` per query) pass `cheap=True`. `doctor`,
  the `/api/pkb/review` endpoint, and `lab_brief` (already TTL-cached) do not.

### `pkb.retrieve_for_agents(query, pkb_root=None) -> dict` (NEW)

```
{"hits": list[dict],          # exactly what search_for_agents returns today
 "gate": <gate_state cheap=True>,
 "empty_reason": None | "gate_empty" | "no_match" | "gate_off_no_match"}
```

- `empty_reason` is `None` iff `hits` is non-empty.
- `"gate_empty"` iff gate enabled and `approved_paths()` was empty — i.e. the
  search never ran. Distinguishing this from `"no_match"` is the whole point.
- **Promises:** never raises; on internal error returns `hits=[]`,
  `empty_reason="gate_empty"` (fail-closed and fail-loud, not fail-silent).

### `pkb.search_for_agents(query, pkb_root=None) -> list` (UNCHANGED)

Kept as `retrieve_for_agents(...)["hits"]`. Existing callers and
`tests/test_pkb_gate.py` keep working with no edit. Gate semantics unchanged.

### `compiled_kb.auto_approve_world_terms(world_slug, *, bundle_terms, seal_sha, pkb_root=None) -> list[dict]`

- **Requires:** `bundle_terms` comes from a bundle whose seal has already
  verified. The function does not verify seals and must never be called with
  unverified terms.
- **Promises:** approves only paths satisfying the four scope conditions above;
  delegates every write to `approve()`; stamps `approved_by =
  f"world-seal:{seal_sha[:12]}"` and `auto = True` on each record so auto and
  operator approvals are distinguishable and separately revocable.
- **Bad input:** unknown slug, empty `bundle_terms`, missing staged dir, opt-out
  sentinel present, `ARAIL_AUTO_APPROVE_WORLD_TERMS=off` → returns `[]`,
  writes nothing.
- **Never raises.** Called best-effort from `mount()` after the pointer write;
  an exception must not fail a mount.

### `compiled_kb.bootstrap(pkb_root=None, *, dry_run=False) -> dict`

- Resolves the mounted/known World for that root, loads `terms.json` from the
  **catalog copy** (`WORLDS_DIR/<slug>/terms.json`, per `world_corpus`'s
  reasoning — it survives a switch), and delegates to
  `auto_approve_world_terms`.
- Always writes the manifest when not `dry_run`, **even when zero terms
  qualify**, so `manifest_present()` flips and the state moves from
  `"unbootstrapped"` to `"empty"`. This is what makes a fresh lab honest.
- Returns `{"root", "world", "approved": n, "skipped_reason": str|None,
  "dry_run": bool}`.
- **Never raises**; a per-root failure is reported in `skipped_reason`.

### Mutation of `approve()`

Additive only: accepts `approver` (already present) and a new
`extra: dict | None` merged into each record (`{"auto": True}`). No change to
validation, ordering, or the fail-closed read path.

## What each caller does with the empty state

| Caller | Change | Behavior on `state != "populated"` |
|---|---|---|
| `pkb.search_for_agents` | none | unchanged list return |
| `lab_brain.py:527` (Buddy) | switch to `retrieve_for_agents` | on `gate_empty`, inject a one-line system note: "The Compiled KB has no approved knowledge, so I could not search it — approve items on the Knowledge page or run `./arailctl pkb bootstrap`." Buddy must say this rather than answer as if the KB were searched and empty. On `no_match`, current behavior. |
| `agents/researcher.py:767,1297` | `_kb_search` uses `retrieve_for_agents` | on `gate_empty`, record the reason on the experiment/observation ("KB gate empty — hypothesis not KB-measurable") instead of silently concluding "not measurable" |
| `portal/app.py:3160` (goal drafter) | use `retrieve_for_agents` | draft proceeds; response carries `kb_gate` so the UI can show "drafted without KB grounding — gate empty" |
| `agents/_builtin_debt_advisor.py:228,242,324` | none | already reads `approved_paths()` directly for *metadata counts*; empty is a legitimate zero there. Do **not** wire auto-approval into scout findings. |
| `lab_brief.py:208` | add `gate_state` to the KB section | brief markdown gains one line when state is `unbootstrapped`/`empty`; Buddy and Researcher already ingest the brief |
| `doctor.py:166` | use `gate_state` | distinguish "unbootstrapped" (recommend `./arailctl pkb bootstrap`) from "empty" (recommend approving on /dac). Escalate `unbootstrapped` from info to **warn** when a World is mounted. |
| `portal/wiki_routes.py:320` | none (graph scope) | unchanged; brain scope with nothing approved already ghosts nodes |
| `research/agenda_watch.py:714` | none | pruning heuristic; empty means "nothing approved, prune freely" — correct today |
| `build/world_corpus.py:114,163` | none | `pull_approved_terms` will now find terms after bootstrap. This is a **behavior change worth naming**: builds that previously raised "no approved terms found" will now succeed. Intended. |
| `GET /api/pkb/review` | add `"gate": gate_state()` | UI renders an explicit banner per `state` |
| `/dac` template | new empty-state block | `unbootstrapped` → "Bootstrap the Compiled KB" button; `empty` → "Approve knowledge to let agents read it"; `off` → "The approval gate is disabled (`ARAIL_APPROVED_ONLY=off`) — agents read the raw corpus." The `off` banner is persistent and non-dismissible; that is the anti-drift measure keeping `off` from becoming the de facto default. |

## Migration / backfill

**Trigger policy — decided:**

- **Not on `start`.** The 2026-07-23 clean-experience sprint made boot quiet
  with no auto-checks. A backfill that writes to the manifest on every start
  violates that and would silently re-approve terms an operator revoked.
- **On `mount`.** Step 3.5, best-effort, after the pointer write. This is the
  forward-looking fix; every future World is correct without operator action.
- **On `install`** (`./arailctl install`, alias `update`): one call to
  `pkb bootstrap` for the root lab, non-fatal, output summarized.
- **Explicit verb** `./arailctl pkb bootstrap [--dry-run] [--world <slug>]
  [--all-instances]`, which is how the six existing roots get fixed and how
  anything that failed gets retried. `--dry-run` prints the exact path list and
  writes nothing.

**Re-approval semantics.** `approve()` is idempotent per path and overwrites the
record (refreshing `sha256`/`approved_at`). Two consequences to handle:

- A path the operator explicitly **revoked** must not be silently re-approved by
  the next mount. `auto_approve_world_terms` skips any path in a new
  `compiled/kb/unapproved.json` "sticky revocation" set. `revoke()` adds each
  path to that set; a later explicit `approve()` (operator action) removes it.
  Without this, revoke is meaningless for World terms.
- A path in `rejected.json` is likewise skipped by auto-approval (an explicit
  human dismissal outranks the bundle).

**Content but no manifest** (all six roots today): bootstrap resolves the World
from `lab/worlds/<slug>/terms.json`. If the root has staged `sources/world-*/`
content but no matching catalog bundle, approve nothing, write the empty
manifest, and set `skipped_reason="no bundle in catalog for <slug>; mount it
once"`. Never fall back to "approve every .md under terms/" — that is exactly
the unverified path the invariant exists to exclude.

**Genuinely fresh lab** (no worlds, no content): writes
`{"schema", "updated_at", "bootstrapped_at", "items": {}}`. `state` becomes
`"empty"`, doctor stays quiet (no World mounted), the /dac page shows the
"approve knowledge" empty state rather than the bootstrap prompt.

**`finance` root** (0 source docs) is exactly this case and is a required
backfill test case.

**Rollback:** `./arailctl pkb revoke --auto` (or the /dac "Revoke
auto-approved" control) removes every record with `auto: True` in one step. The
raw files are untouched; the sprint is fully reversible without a code revert.

## Bulk approve

Reviewing 351 items one at a time is not review. Two mechanisms:

1. **Bootstrap covers the common case.** After the mount hook + backfill, World
   terms need no manual approval at all. The bulk path is for the remainder.
2. **Scoped bulk approve** — `POST /api/pkb/promote_bulk`:

```
{"scope": {"kind": "world_term", "world": "world-ai", "category": "…"},
 "expected_count": 351,
 "csrf": …}
```

- CSRF-gated via the existing `_pkb_write_csrf`.
- Server recomputes the selection from `list_pending()` under the scope. If the
  recomputed count `!= expected_count`, refuse with `409 scope_drift` and
  return the new count. This prevents a stale UI from approving items that
  appeared between render and click (agent output landing mid-review is the
  realistic case).
- `scope.kind` is restricted to an allowlist: `world_term`, `source`,
  `scout_finding`. `agent_research` / `agent_experiment` / `agent_synthesis` /
  `agent_recommendation` / `agent_dream` / `note` are **not** bulk-approvable
  and must be selected individually. Agent output and inbox ingest stay behind
  per-item review, per the operator's constraint.
- Hard cap `_BULK_MAX = 1000` per request.
- Records carry `approved_by="operator"`, `auto=False` — a bulk operator
  approval is a real approval, distinct from a seal-derived one.
- UI: a "Select all in scope (351)" control on /dac with the scope and count
  spelled out in the confirm dialog, plus the always-available per-item path.

## Failure modes

| Failure | Detection | Recovery |
|---|---|---|
| Manifest missing (today's bug) | `gate_state.state == "unbootstrapped"`; doctor warns when a World is mounted | `./arailctl pkb bootstrap`; mount hook prevents recurrence |
| Manifest corrupt / unparseable JSON | `_load_json` swallows → `approved_paths() == set()`, `manifest_present() == False` | reads as `unbootstrapped`; agents get nothing; operator re-bootstraps. **Never** reads as "everything approved" |
| `compiled/kb/` unwritable (perms, full disk) | `approve()`'s `_save_json` raises → caught by `auto_approve_world_terms`/`bootstrap`, surfaced in `skipped_reason` | mount still succeeds; operator sees the reason; gate stays closed |
| Partial write / crash mid-save | `_save_json` writes `.tmp` then `replace()` — atomic | previous manifest intact |
| Auto-approval reaches a non-term path | scope invariant + tests; `auto: True` records are auditable and bulk-revocable | `pkb revoke --auto` |
| Hand-dropped .md inside staged `terms/` | condition (2): not in `terms.json` → not approved | stays in the review queue |
| Personal note in `notes/`/`inbox/` becomes agent-visible | debt-finance non-regression test (below) | scope invariant; no code path can reach it |
| Operator revokes a term, next mount re-approves it | sticky `unapproved.json` set consulted by auto-approval | revocation persists across mounts |
| World switch dangles every approval → gate silently empties | existing `prune_dangling` at mount/unmount; now also `state == "empty"` distinct from `"unbootstrapped"` | mount hook re-approves the incoming World's terms in the same operation |
| `ARAIL_APPROVED_ONLY=off` becomes the norm | persistent non-dismissible /dac banner + `gate_state.state == "off"` in `/api/pkb/review`, lab brief, and doctor | visible in three surfaces; not silenceable |
| `pending_count` walk on a hot path (Buddy per turn) | `cheap=True` returns `-1` and skips the walk | benchmark case in test strategy |
| `retrieve_for_agents` raises inside an agent | wrapped; returns `hits=[]`, `empty_reason="gate_empty"` | agent reports the gate, never fabricates |
| Bulk approve races with agent writes | `expected_count` mismatch → `409 scope_drift` | UI refreshes and re-confirms |
| `bootstrap --all-instances` hits an unreadable instance root | per-root `skipped_reason`; other roots continue | re-run after fixing perms |
| World-corpus builds that used to fail now succeed on auto-approved terms | intentional; noted in tech debt | operator can revoke before building |

## Test strategy

QA executes this. Every failure-mode row above maps to a case here.

### Security (the sprint's 20% — run these first)

- **S1 — debt-finance non-regression (the named case).** Build a temp PKB root
  mirroring `lab/instances/debt-finance/pkb`: real `sources/world-debt-finance/
  terms/*.md` from the catalog bundle, plus planted files
  `notes/personal-balances.md` (containing a unique token, e.g.
  `ACCT-XYZ-4417`), `inbox/statement.md`, `conversations/c1/transcript.jsonl`,
  and `agents/research/2026-01-01_x_report.md`. Run mount + `bootstrap`. Assert:
  (a) every approved path matches `^sources/world-debt-finance/terms/.+\.md$`;
  (b) `approved_paths()` ∩ {planted paths} == ∅;
  (c) `search_for_agents("ACCT-XYZ-4417")` returns `[]`;
  (d) `retrieve_for_agents("ACCT-XYZ-4417")["empty_reason"] == "no_match"`
  (proving the search *ran* and still did not surface it — not merely that the
  gate was closed);
  (e) every approved term slug is present in the bundle's `terms.json`.
- **S2 — extra file in terms/.** Write `terms/not-in-bundle.md` into the staged
  dir, re-run bootstrap, assert it is not approved.
- **S3 — traversal.** Feed `../../etc/passwd`, `/etc/passwd`,
  `sources/world-x/terms/../../../notes/secret.md` through
  `auto_approve_world_terms` and `promote_bulk`; assert nothing is approved and
  nothing raises.
- **S4 — opt-out sentinel.** Create `compiled/kb/no-auto-approve`; mount;
  assert zero auto-approvals and a written empty manifest. Repeat with the
  sentinel unreadable (chmod 000) — assert still disabled.
- **S5 — `ARAIL_AUTO_APPROVE_WORLD_TERMS=off`** disables the mount hook while
  the explicit `bootstrap` verb still works.
- **S6 — bulk-approve kind allowlist.** `promote_bulk` with
  `kind="agent_research"`, `"note"`, `"agent_dream"` → 400, nothing approved.
- **S7 — CSRF.** `promote_bulk` without the token → rejected.
- **S8 — scope drift.** Render count 5, add a candidate, submit
  `expected_count=5` → 409, nothing approved.
- **S9 — sticky revocation.** Approve → revoke → re-mount → assert not
  re-approved. Then explicit `approve()` → re-mount → assert it persists.

### Fail-closed (must survive)

- **F1** Corrupt `approved.json` (truncated JSON, a bare `"x"`, a JSON list of
  strings, `null`): `approved_paths() == set()`,
  `search_for_agents(...) == []`, `gate_state.state == "unbootstrapped"`, no
  exception.
- **F2** `compiled/kb/` unreadable: same assertions.
- **F3** `compiled/kb/` unwritable during mount: mount succeeds, manifest
  absent, `skipped_reason` set, gate closed.
- **F4** No code path anywhere produces `approved_paths()` returning a
  superset of the manifest. Static: grep the diff for any `return` in
  `approved_paths`/`_approved_map` other than the manifest-derived set, and any
  `if not approved:` that falls through to unfiltered results.

### Unit

- `manifest_present`: missing / empty file / `{}` / `{"items":{}}` / corrupt /
  list shape.
- `gate_state`: all four states, `cheap=True` sets `pending_count == -1`, total
  failure still returns a complete dict.
- `auto_approve_world_terms`: happy path count == len(terms.json); slug
  sanitizer parity with `_safe_term_slug` for unusual slugs (unicode, leading
  digits, >80 chars, empty); terms present in JSON but missing on disk are
  skipped; `rejected.json` entries skipped; idempotent across two calls
  (same count, `sha256` refreshed, no duplicates).
- `bootstrap`: fresh root writes an empty manifest and flips
  `manifest_present`; `--dry-run` writes nothing; content-without-bundle sets
  `skipped_reason`.
- `retrieve_for_agents`: the four `empty_reason` values, each asserted against
  a constructed state; `hits` byte-identical to `search_for_agents`.

### Integration

- **I1** Full mount of a real catalog bundle (`ai`) into a temp root: assert
  351 approved (or exactly `len(terms.json)`), `search_for_agents("<a term
  from the bundle>")` non-empty. *This is the regression test for QA-6 itself.*
- **I2** Backfill all six root shapes (`lab/pkb`, `ai`, `debt-finance`,
  `finance` (0 docs), `qukaizen`, `video-games`) via
  `bootstrap --all-instances --dry-run`, then for real in temp copies; assert
  per-root counts and that `finance` produces an empty-but-present manifest.
- **I3** World switch: mount `ai`, mount `video-games`; assert `ai` approvals
  are pruned (existing `prune_dangling` behavior), `video-games` terms are
  approved, and `search_for_agents` returns `video-games` hits.
- **I4** `GET /api/pkb/review` returns a well-formed `gate` block in each state.
- **I5** `build/world_corpus.pull_approved_terms` returns non-empty after
  bootstrap for a bundle whose categories match.
- **I6** Buddy end-to-end: with an empty gate, the response contains the
  "no approved knowledge" note; after bootstrap, it cites a term.

### Regression

- `tests/test_pkb_gate.py` unchanged and passing (`search_for_agents` shape and
  env honoring).
- `tests/test_compiled_kb.py`, `test_compiled_kb_sweep_prune.py`,
  `test_pkb_review_api.py`, `test_lab_brief.py`, `test_wiki_graph_scope.py`,
  `test_debt_finance_agents.py` unchanged and passing.
- `tests/test_scouting.py::test_scouting_never_imports_compiled_kb` still
  passes — the bootstrap must not add a `compiled_kb` import to scouting.
- No new cross-repo import: assert the diff introduces no `import` from outside
  `arail`/`dac_world`.

### Performance

- `gate_state(cheap=True)` on the 351-page `ai` root: **< 5 ms**, and asserted
  to perform no `rglob` (monkeypatch `pending_paths` to raise; `cheap=True`
  must not call it).
- `bootstrap` on the `ai` root: **< 3 s** wall.
- `mount()` wall-clock regression vs baseline: **< 10%** added.

## Tech debt

**Added**

- A second approval provenance class (`auto: True`) and a third manifest file
  (`unapproved.json`, sticky revocations). Three JSON files under
  `compiled/kb/` is the ceiling — a fourth means this should become a single
  state file with a schema version.
- `retrieve_for_agents` alongside `search_for_agents` is a transitional
  two-function API. Follow-up ticket: migrate remaining callers and make
  `search_for_agents` a documented thin alias, or deprecate it.
- The opt-out sentinel is an undocumented-by-default file convention. Must be
  documented in `docs/` in this sprint, not deferred.
- `gate_state` is a new thing every surface can call; risk of it becoming a
  per-request tree walk. Mitigated by `cheap=True` + the perf assertion, not
  by convention.

**Repaid**

- The KB gate's silent-zero, which produced a two-week window of "the
  Researcher kept concluding its hypotheses were not measurable" (already
  documented in `doctor.py`'s comment) and the present universal-zero bug.
- `unbootstrapped` vs `empty` closes the diagnostic gap doctor could only
  guess at.
- Removes the standing incentive to set `ARAIL_APPROVED_ONLY=off`, which was
  the real threat to the gate.
- `world_corpus`'s "no approved terms found — mount + approve first" dead end
  becomes reachable-by-default.

**Net:** negative (debt repaid) — the added debt is three small, bounded
mechanisms; the repaid debt is a security control that was inert in practice.

## Recommended implementation order

1. `compiled_kb.manifest_present` + `gate_state` (+ unit tests). Pure addition,
   no behavior change. Land and verify the full existing suite still passes.
2. `pkb.retrieve_for_agents`; `search_for_agents` becomes its `["hits"]`.
   Existing tests must pass untouched.
3. `compiled_kb.auto_approve_world_terms` + the sticky `unapproved.json` in
   `revoke()`/`approve()`. **Write S1–S3 before wiring any caller.**
4. `compiled_kb.bootstrap` + `python -m arail.compiled_kb bootstrap` +
   `./arailctl pkb bootstrap`.
5. `world_mount.mount()` step 3.5 (best-effort, after pointer write).
6. Caller updates per the table: doctor, lab_brief, `/api/pkb/review`, Buddy
   (`lab_brain`), researcher, goal drafter.
7. `promote_bulk` endpoint + /dac empty-state and bulk-select UI, including the
   persistent `ARAIL_APPROVED_ONLY=off` banner.
8. `./arailctl install` calls bootstrap once, non-fatally.
9. Docs: `docs/` note on the gate lifecycle, the sentinel file, and
   `pkb bootstrap` / `pkb revoke --auto`.
