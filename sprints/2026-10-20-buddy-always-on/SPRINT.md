# Sprint: buddy-always-on — Buddy reachable from the phone through a nanobot gateway

**ID:** 2026-10-20-buddy-always-on
**Started:** 2026-10-20 10:05 CDT
**Product:** arail
**Branch:** `qukaizen/arail-buddy-always-on` (cut from `origin/main` `ca0a8f81`)
**Worktree:** `~/ProJects/.claude/worktrees/sprint-command-error-060a85/arail-buddy-wt`
(the app's write hook refuses edits under `~/ProJects` outside the session
folder; `~/ProJects/arail-buddy-wt` is back on `main` and untouched).

## Task

Turn Buddy, ARAIL's default agent, into an always-on bot the operator can
reach from the phone through a gateway built on nanobot (HKUDS, MIT). Buddy is
a specialised QA subject-matter expert who cites a DDaC World. The operator's
brief (nine forcing questions: shape A/B, trust, mode vs airgapped, control,
process supervision, QA-SME World, first channel, one pass/fail win test, the
gateway as 3D.EVAL's test surface) is reproduced in the visionary's prompt and
in `RESEARCH_NOTES.md` §1. Do not design or code until VISION.md is in.

## Inputs

- `LANDSCAPE.md` — OSS field survey (Grok Bot is closed; OpenClaw / Hermes /
  nanobot / OpenBot) + code-verified map of Buddy on main.
- `RESEARCH_NOTES.md` — operator steers in order, fork comparison, the
  paperagents agent-control-plane rule model ("the gateway we designed"),
  verified brain facts (llama-ai-eng resident; nucleus-baked = roadmap;
  QueueLLM has no HTTP server), nanobot docs facts.
- `VISION.md` v1 (`83c5589a`, "stay in ARAIL") — superseded by this pass.
- `../2026-09-20-buddy-front-and-center/OPERATOR_BRIEF.md` D1–D18 stand.

## Phases

| Phase | Subagent | Artifact | Status | Started | Finished | Verdict |
|---|---|---|---|---|---|---|
| think | visionary | VISION.md | done | 2026-10-20 11:40 | 2026-10-20 12:05 | proceed (3 preconditions; commit `6a9cbc21`) |
| plan | architect (design) | ARCHITECTURE.md | pending | — | — | — |
| build | builder | BUILD_LOG.md | pending | — | — | — |
| review | architect (review) | REVIEW.md | pending | — | — | — |
| test | qa | TEST_REPORT.md | pending | — | — | — |
| ship | — | PR | pending | — | — | — |

## Decisions log

| Date | Decision | Rationale |
|---|---|---|
| 2026-10-20 | Grok Bot is a behaviour reference, not a code source. | Proprietary, cloud-only (LANDSCAPE §0). |
| 2026-10-20 | Fork base: **nanobot**, over Hermes Agent and OpenClaw. | Operator: "I like the nanobot footprint… we build what we need but will be more configurable; otherwise, why not just use Hermes?" Hermes stays the reference for approval UX and pairing hardening (RESEARCH_NOTES §2). |
| 2026-10-20 | "The gateway we designed" = the paperagents agent control plane rule model; it carries into Buddy's tool path. | Operator steer 4; RESEARCH_NOTES §3. |
| 2026-10-20 | Two brains: ~1B resident always on (Ollama `llama-ai-eng` today, nucleus-baked later), QueueLLM on demand via ARAIL's `deep_policy`. | Operator steer 6; nucleus has no usable 1B yet; QueueLLM has no server (RESEARCH_NOTES §4). |
| 2026-10-20 | Buddy is a specialised QA SME on a DDaC World; the gateway is 3D.EVAL's test surface. | Operator's final brief. |
| 2026-10-20 | Shape A vs B (gateway-only vs re-host on nanobot) is the visionary's call; the earlier "break Buddy out" steer is an input, not a constraint. | Final brief, question 1. The `<session>/qukaizen-buddy` scaffold waits on it. |
| 2026-10-20 | **Buddy is its own app outside ARAIL; ARAIL is the initial target.** Deployment: feel like an agent on the Mac, or its own container / Linux system ("AI operating system"); the visionary recommends one for S1. | Operator steer during the VISION pass. Overrides shape A of the brief's question 1; the earlier "break Buddy out" steer stands. |
| 2026-10-20 | **Shape B′ (visionary).** Buddy is a nanobot app outside ARAIL; ARAIL is his only brain (via a `brain.chat` edge through `ModelRouter`), kill-switch authority and trace store; Buddy's code calls ARAIL actions, the model cannot; no MCP in S1. | VISION §3.1: nanobot cannot be a channel-only gateway (always builds an `AgentLoop`); B as stated loses hold/trace/airgap and breaks "no facts from the agent's own output" (Dream → raw `MEMORY.md` in prompt). |
| 2026-10-20 | **Deployment (i): user-level launchd process on the Mac for S1.** Container before the first tool that runs a command or writes outside Buddy's home; separate Linux box rejected for now. | VISION §3.1 deployment table; S1 gives the model no shell/file/web tools. |
| 2026-10-20 | First channel: Telegram, long-poll, DM only. Hybrid only; airgapped always wins. | VISION §3.3, §3.7. |
| 2026-10-20 | QA-SME = a sealed DDaC World `qa-practice` (working name) of the operator's own QA rules, sourced to pinned files; chips rendered by code from sealed terms. | VISION §3.6. Scope (what "QA" means) is precondition (a), operator's one-line answer. |
| 2026-10-20 | S1 cuts: no unsolicited messages (no pushes, cron, Dream); no QueueLLM on the phone path; one reductive action ("pause research"); no container. | VISION §0, §2.4. |
| 2026-10-20 | VISION v1 pass ran first and recommended staying in ARAIL; superseded, not deleted. | The brief changed afterwards (fork base, QA-SME, eval surface). |

## Skipped phases

| Phase | Reason |
|---|---|

## Preconditions before plan (VISION §5)

- (a) Operator confirms the QA World's scope in one line (Q-A).
- (b) `cdarnell/qukaizen-buddy` exists, private, with a `CLAUDE.md` carrying G1–G12 and the S1 QA allocation (35/25/15/15/10) before the builder runs.
- (c) Operator writes P1's W5 witness line before QA.

## Notes

- Frozen names: `aerollm_api`, `AERO_*`/`AEROLLM_*`, backend id `aerollm`,
  `tier1-aerollm` (workspace CLAUDE.md naming status).
- Cross-repo edges (nanobot, DDaC World, 3D.EVAL, paperagents formats) must
  be pinned or hash-stamped (`INTEGRATION_AUDIT.md` §4).
- P1's W5 witness line is still unwritten (REVIEW/TEST_REPORT of the
  2026-09-20 sprint). Flag for the operator before the architect pass.
