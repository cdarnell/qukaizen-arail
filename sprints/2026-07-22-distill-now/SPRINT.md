# Sprint: distill-now

> **Superseded by [2026-09-23-nucleus-sprint-1](../2026-09-23-nucleus-sprint-1/SPRINT.md) (Model Forge).** The `/build` tab this sprint targeted is retired; the local `domain.yaml` -> `arailctl nucleus build` -> signed DNA card loop replaces it. This record is kept for history, not as live guidance.

**ID:** 2026-07-22-distill-now
**Started:** 2026-07-22
**Product:** arail (touches qukaizen-dac / qukaizen-nucleus / AeroLLM as integration points)

## Task

Ship a manual, one-shot "Distill now" trigger in ARAIL's existing `/build` (or `/dac`) tab that runs the full chain for real, once, on Charlie's own lab: DaC-compiled World → Nucleus bake/seal → AeroLLM load-and-answer sanity check → human-gated, reversible knowledge-base compaction with a written receipt. Explicitly NOT in scope: any scheduler/cron, the PaperAgents `Pipeline` CRD, the "docent rail" UX, or generalizing beyond Charlie as the user. See VISION.md for full context and the Fable-authored roadmap it builds on (`qukaizen-nucleus/docs/PAPERAGENTS_ARAIL_NUCLEUS_GEOAI_ROADMAP.md`).

## Phases

| Phase | Subagent | Artifact | Status | Started | Finished | Verdict |
|---|---|---|---|---|---|---|
| think | visionary | VISION.md | done | 2026-07-22 | 2026-07-22 | proceed, conditioned on day-one certifier spike |
| plan | architect (design) | ARCHITECTURE.md | on hold — awaiting go-ahead | — | — | — |
| build | builder | BUILD_LOG.md | pending | — | — | — |
| review | architect (review) | REVIEW.md | pending | — | — | — |
| test | qa | TEST_REPORT.md | pending | — | — | — |
| ship | — | PR | pending | — | — | — |

## Decisions log

| Date | Decision | Rationale |
|---|---|---|
| 2026-07-22 | Manual one-shot only, no scheduler in this wedge | User confirmed explicitly: "1000% this is a manual sequence first" — see [[manual-before-scheduled-automation]] memory |
| 2026-07-22 | ARAIL already has ~70% of the spine (`/api/build/world/start` in `build_api.py`) | Visionary verified against the actual repo rather than trusting the roadmap sketch |
| 2026-07-22 | Nucleus seal-wiring is real net-new work; certifier docker-compose stand-up is the pivotal day-one unknown | The World-corpus training path currently returns `"seal": None` — deliberately bypasses the certifier today |
| 2026-07-22 | AeroLLM comparative smoke-eval descoped to load-and-answer sanity check | No turnkey comparative eval exists, and there's no prior model to compare against on a first-ever run |
| 2026-07-22 | No PaperAgents Pipeline CRD — v1 runs the chain as a script/run-spec, not `pactl apply` | Shipped CRD kinds confirmed in `types.ts`; adding a new typed CRD is its own sprint |
| 2026-07-22 | Compaction is human-gated and reversible (pointer-swap + JSON receipt addressed by corpus_sha256), or not shipped at all this sprint | Roadmap's top named trust risk; visionary's pre-committed descope: if reversibility can't be demonstrated, ship the bake without compaction rather than ship something irreversible-feeling |

## Skipped phases

(none yet)

## Notes

- Full context: [VISION.md](VISION.md)
- Roadmap this sprint executes against: [PAPERAGENTS_ARAIL_NUCLEUS_GEOAI_ROADMAP.md](../../../qukaizen-nucleus/docs/PAPERAGENTS_ARAIL_NUCLEUS_GEOAI_ROADMAP.md)
- Precedent sprint (first real PaperAgents↔DaC gate): [qukaizen-nucleus/sprints/2026-07-22-paperagents-dac-okf-real/](../../../qukaizen-nucleus/sprints/2026-07-22-paperagents-dac-okf-real/)
