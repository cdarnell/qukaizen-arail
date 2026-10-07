# Sprint: queuellm-display-rename

**ID:** 2026-10-07-queuellm-display-rename
**Started:** 2026-10-07
**Product:** arail
**Branch:** qukaizen/arail-queuellm-display-rename (worktree `~/ProJects/arail-queuellm-rename-wt`, cut from origin/main 5f775f1c)

## Task
(1) Every user-visible "AeroLLM"/"aeroLLM"/"aerollm" display string in arail (portal templates — chat.html deep-model picker header "DEEP · AEROLLM", provider chip "AeroLLM", tooltip "served by AeroLLM"; arailctl help and start banner "B (aeroLLM)"; user-facing error/status messages such as "AeroLLM isn't ready on this lab", "AeroLLM model dir not found") reads QueueLLM.
(2) Local deep-model answers report a fabricated `cloud_cost_usd` (~$0.008 for a local Qwen3-30B answer via /api/chat/stream). Local inference must not show a cloud cost; every cost must carry a source.

**Frozen — never rename (workspace CLAUDE.md, "Naming status: AeroLLM → QueueLLM"):** `AERO_*`/`AEROLLM_*` env var names (keep verbatim when quoted in messages), `aerollm-api` package / `aerollm_api` module, backend id `"aerollm"`, registry id `tier1-aerollm`, `AeroLLMBackend`, `provider_type`/JSON/API field values, script names (`build-aerollm.sh`, `package-aerollm-bundle.sh`), `THIRD-PARTY-LICENSES/aerollm/`, bundled NOTICE, hash-pinned bundle, historical `sprints/` and `docs/archive/`. Display strings only.

**Ship = PR opened, not merged.**

## Phases

| Phase | Subagent | Artifact | Status | Started | Finished | Verdict |
|---|---|---|---|---|---|---|
| think | visionary | VISION.md | skipped | — | — | — |
| plan | architect (design) | ARCHITECTURE.md | done | 2026-10-07 | 2026-10-07 | complete (20726778) |
| build | builder | BUILD_LOG.md | pending | — | — | — |
| review | architect (review) | REVIEW.md | pending | — | — | — |
| test | qa | TEST_REPORT.md | pending | — | — | — |
| ship | — | PR | pending | — | — | — |

## Decisions log

| Date | Decision | Rationale |
|---|---|---|
| 2026-10-07 | Owner picked this sprint over a speed investigation | Owner repeatedly saw "AeroLLM" after being told the rename was done |
| 2026-10-07 | Work in a fresh worktree, not `arail-buddy-wt` | `arail-buddy-wt` runs the owner's live lab and has uncommitted world files |

## Skipped phases

| Phase | Reason |
|---|---|
| think | Win condition is concrete and owner-stated: zero user-visible "AeroLLM" in arail; no fabricated cloud cost on local answers. |

## Notes — UX traps observed 2026-09-29/30 (context for architect; out of scope unless cheap)
- `model_defaults.yaml` (`default_b`) silently overrides `.env` `AEROLLM_MODEL`, and the registry re-seeds from it on every start.
- The `arailctl start` model banner is a one-time snapshot and can contradict the live portal.
- The "requires streaming" fit chip reads like a block but is advisory.
- Measured: Qwen3-30B-A3B deep answer, 36 tokens in 73 s (0.5 tok/s), reported `cloud_cost_usd: 0.008267`, `role: secondary`, `backend: aerollm`.
