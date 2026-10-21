# Sprint: mastra-studio-spike

**ID:** 2026-10-13-mastra-studio-spike
**Started:** 2026-10-13
**Product:** arail

## Task
Embed Mastra Studio as an ARAIL surface (like terminal/notebook), not a merge. Expose the Researcher agent as an MCP/HTTP tool, drive it from a Mastra project, and land one trace + score in the autoresearch experiment ledger. Manual one-shot only (no scheduler). Airgapped default; no Mastra cloud/telemetry. ARAIL/PaperAgents stays source of truth for agents. Verify Mastra license + current Studio features before relying on them.

## Phases

| Phase | Subagent | Artifact | Status | Started | Finished | Verdict |
|---|---|---|---|---|---|---|
| think | visionary | VISION.md | done | 2026-10-13 | 2026-10-13 | proceed (time-boxed; D1/D2 kill gates first) |
| plan | architect (design) | ARCHITECTURE.md | done | 2026-10-13 | 2026-10-13 | gates-first design; ledger (a) ExperimentTracker; stdio MCP |
| build | builder | BUILD_LOG.md | built; one gate open (D1 browser leg INCONCLUSIVE, owner pcap) | 2026-10-13 | — | D1 server PASS; D1 browser INCONCLUSIVE; D2 PASS; ee accepted w/ guard |
| review | architect (review) | REVIEW.md | done | 2026-10-21 | 2026-10-21 | WEAK_PASS, conditional on clean owner D1 browser pcap (else BLOCK); ASKs 1,2,3,4,12 before QA |
| test | qa | TEST_REPORT.md | pending | — | — | — |
| ship | — | PR | pending | — | — | — |

## Decisions log

| Date | Decision | Rationale |
|---|---|---|
| 2026-10-13 | Worktree `../arail-mastra-wt` on `qukaizen/arail-mastra-studio-spike` cut from main | Main checkout is dirty on `qukaizen/arail-ingress-spine` |

## Skipped phases

| Phase | Reason |
|---|---|

## Notes
Origin: owner's idea that Mastra Studio could be tied into all ARAIL agents; embed-not-merge recommended in chat 2026-10-12.
