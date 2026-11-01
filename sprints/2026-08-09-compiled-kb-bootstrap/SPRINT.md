# Sprint: compiled-kb-bootstrap

**ID:** 2026-08-09-compiled-kb-bootstrap
**Started:** 2026-08-09T15:39:56Z
**Product:** arail

## Task

QA-6: agents get zero knowledge-base results on every World. The Compiled-KB
gate ships on (`compiled_kb.gate_enabled()` defaults to on) while
`approved_paths()` is empty on all six PKB roots — no `compiled/kb/approved.json`
exists anywhere, across 540 source docs. `pkb.search()` hard short-circuits to
`[]` before any search runs (`pkb.py:644`), so `search_for_agents()` returns
nothing for Buddy, chat RAG, Researcher grounding, the goal drafter, and the
debt advisor. Pre-existing; orthogonal to re-embedding. The fix is a bootstrap
*policy* decision, which is why it goes through architect design rather than
straight to a patch.

## Evidence at sprint open

```
lab/pkb                          approved=NOFILE  source_md=10
lab/instances/ai/pkb             approved=NOFILE  source_md=351
lab/instances/debt-finance/pkb   approved=NOFILE  source_md=54
lab/instances/finance/pkb        approved=NOFILE  source_md=0
lab/instances/qukaizen/pkb       approved=NOFILE  source_md=44
lab/instances/video-games/pkb    approved=NOFILE  source_md=81
```

Chain: `compiled_kb.py:452` (gate defaults on) → `compiled_kb.py:117`
(fail-closed to `set()`) → `pkb.py:644` (`if not approved: return []`).

## Phases

| Phase | Subagent | Artifact | Status | Started | Finished | Verdict |
|---|---|---|---|---|---|---|
| think | visionary | VISION.md | skipped | — | — | — |
| plan | architect (design) | ARCHITECTURE.md | done | 2026-08-09T15:39:56Z | 2026-08-09T15:45:21Z | policy chosen (3)+(1)@mount |
| build | builder | BUILD_LOG.md | done | 2026-08-09T15:45:21Z | 2026-08-09T15:57:30Z | 8 commits, 80 tests pass |
| review | architect (review) | REVIEW.md | done (round 1) | 2026-08-09T15:57:30Z | 2026-08-09T16:02:10Z | **BLOCK** — 3 findings |
| build (round 2) | builder | BUILD_LOG.md | done | 2026-08-09T16:02:10Z | 2026-08-09T16:10:40Z | 3 BLOCKs + ASK-1 fixed, 100 tests pass |
| review (round 2) | architect (review) | REVIEW.md | done | 2026-08-09T16:10:40Z | 2026-08-09T16:16:05Z | **PASS** (ade527c) |
| test | qa | TEST_REPORT.md | done | 2026-08-09T16:16:05Z | 2026-08-09T16:38:20Z | **WEAK_PASS** (d03eeff) — 142 tests added |
| ship | — | PR | done | — | 2026-08-09T16:52:00Z | PR #176 open to main |

## Decisions log

| Date | Decision | Rationale |
|---|---|---|
| 2026-08-09 | Skip the visionary phase | Win condition is not in question: Buddy must be able to read the knowledge base. Buddy quality is 30% of arail's ship gating. |
| 2026-08-09 | Route through architect design rather than patching directly | The code behaves as designed — fail-closed is a deliberate, documented safety property. Changing the bootstrap state is a policy decision with three materially different answers. |
| 2026-08-09 | Policy = option (3) floor + option (1) narrowly scoped, re-hooked from **seal time to mount time** | Option (2) rejected: it makes the gate fail *open* on corruption, since a corrupt manifest, an unreadable dir, and a legitimately-emptied manifest are indistinguishable from "never bootstrapped". Option (3) alone rejected: a first run whose first required act is bulk-approving 351 pages you didn't author is a consent ritual, and it drives users to `ARAIL_APPROVED_ONLY=off`. |
| 2026-08-09 | Correction to the sprint's framing: seal-time auto-approval is unreachable | Term pages don't exist at seal time — `world_mount._write_term_pages()` (world_mount.py:1187, called from `_stage_files` at :1092) synthesizes them from `bundle.terms` at **mount**. The sealed bundles in `lab/worlds/` will never re-enter a seal path, so a seal-time hook would fix zero of the six roots. Verified independently by the orchestrator. |
| 2026-08-09 | Backfill runs on `install` and via explicit `./arailctl pkb bootstrap` — never on `start` | Running on `start` would violate the quiet-boot rule from the 2026-07-23 clean-experience sprint and would silently re-approve revoked items. |

## Open policy question for the architect

Choose the bootstrap policy among:

1. **Auto-approve at seal time**, scoped to `sources/world-*/terms/*.md` — forging
   and sealing a World bundle is itself the human approval act.
2. **Bootstrap-open** — an empty manifest means pass-through to raw; the gate
   only bites once something has been approved.
3. **Keep fail-closed, make the empty state loud** — plus a bulk-approve path so
   the review queue is not a per-item slog through 351 pages.

**Operator's lean:** (3) as the floor, with (1) scoped to world-term pages only.
Agent research output and inbox ingest stay behind the review queue.

Also in scope for the design:

- The **empty-state contract**: how agents and the UI distinguish "gate is empty"
  from "no match found". Today both are a silent zero.
- A **migration/backfill** for the six existing roots.

## Security constraint (outranks convenience)

The `debt-finance` World must not become ungated as a side effect. QA must prove
this, not assert it. arail's QA allocation puts 20% on security and this is the
security half of the sprint.

## Skipped phases

| Phase | Reason |
|---|---|
| think | Bug fix with an obvious win condition — agents must be able to read the KB. |

## Notes

- Callers of the gate to keep in view: `pkb.search_for_agents`,
  `lab_brief.py:209`, `research/agenda_watch.py:714`, `doctor.py:166`,
  `portal/wiki_routes.py:320`, `agents/_builtin_debt_advisor.py` (228/242/324),
  `agents/researcher.py:1303`, `build/world_corpus.py` (114/163).
- `ARAIL_APPROVED_ONLY=off` is the existing escape hatch; whatever ships should
  not make that the de facto default.
