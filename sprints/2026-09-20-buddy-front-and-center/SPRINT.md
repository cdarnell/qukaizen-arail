# Sprint: buddy-front-and-center — P1: observability + guardrails

**ID:** 2026-09-20-buddy-front-and-center
**Started:** 2026-09-21 12:49 CDT
**Product:** arail
**Branch:** `qukaizen/arail-buddy-front-and-center` (cut from main `236504ca`)
**Worktree:** `~/ProJects/arail-buddy-wt` — all work happens here. The main
`qukaizen-arail` checkout holds the operator's unrelated in-flight work and
must not be touched.

## Task

Phase **P1** of `OPERATOR_BRIEF.md` only: put observability and guardrails up
over the agents that already exist, before any new Buddy behaviour. Six
deliverables: (1) one trace per agent decision with a trace id, and per-agent
attribution replacing the blanket `billing_source="agent"`; (2) a single
agent-inference gateway that the ~5 ad-hoc model-acquisition paths collapse
behind, enforcing priority (user turn > Chat tab > proactive line > prep),
budgets and tracing; (3) a maximus-only Admin "Agents control room" showing
every agent as a living lane — state, brain + effort, last TTFT, tokens,
timeline, the inference slot, `deep_policy.explain()` reason codes verbatim;
(4) per-agent budgets and a visible "hold all agents" kill switch; (5) the
typed-action allow-list scaffold, with no new actions yet; (6) a
flight-recorder toggle for prompt/response bodies — off by default, local-only,
redacted.

Operator steer: "observability and guard rails go up first"; fun, living views
of agents — **not** a metrics product (no Prometheus endpoint, no OTel export,
no new CLI).

**Out of scope:** the Buddy panel, goal interview, event-driven voice, prep
queue, guided demo, brain bake-off, screen sense, and any rename of the
aerollm-named integration surface.

## Inputs

- `OPERATOR_BRIEF.md` — operator interview, decisions D1–D18, verified code
  facts (re-checked against main 2026-09-21), constraints, kill criteria. The
  visionary pressure-tests this rather than re-asking the operator.

## Phases

| Phase | Subagent | Artifact | Status | Started | Finished | Verdict |
|---|---|---|---|---|---|---|
| think | visionary | VISION.md | in progress | 2026-09-21 12:49 | — | — |
| plan | architect (design) | ARCHITECTURE.md | pending | — | — | — |
| build | builder | BUILD_LOG.md | pending | — | — | — |
| review | architect (review) | REVIEW.md | pending | — | — | — |
| test | qa | TEST_REPORT.md | pending | — | — | — |
| ship | — | PR | pending | — | — | — |

## Decisions log

| Date | Decision | Rationale |
|---|---|---|
| 2026-09-21 | Sprint scoped to brief phase P1 only | Operator D18: "observability and guard rails go up first." Nothing new speaks, acts, or spends compute until it can be seen and bounded. |
| 2026-09-21 | Work in a dedicated worktree cut from main, not the main checkout | Main checkout is on the QA-failed `qukaizen/arail-ingress-spine` branch with the operator's uncommitted work; main also carries the `_normalize_keep_alive` resident-pin fix that older branches lack. |
| 2026-09-21 | No push, no PR until the operator says so | Ship is an explicit confirmation step. |

## Skipped phases

| Phase | Reason |
|---|---|

## Notes

- Known pre-existing test failures on main, not introduced by this sprint:
  `test_notice_byte_identical_to_sibling_when_available` (bundled NOTICE vs
  rebranded engine NOTICE — clears when the bundle is re-cut) and
  `test_backends_raises_on_sentinel_before_any_load`. In a fresh worktree
  `test_cross_link_audit_all_internal_links_resolve` also fails because
  `lab/pkb/skills/` is git-ignored state seeded by setup.
- Tests in this worktree run with the main checkout's interpreter:
  `/Users/netsushi/ProJects/qukaizen-arail/.venv/bin/python -m pytest …` —
  `tests/conftest.py` pins `sys.path` so `arail` imports from this worktree
  (verified 2026-09-21).
