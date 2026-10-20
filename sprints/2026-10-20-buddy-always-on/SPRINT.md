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
| plan | architect (design) | ARCHITECTURE.md | done | 2026-10-20 12:30 | 2026-10-20 13:15 | complete, 1,741 lines (commit `b2af01d7`) |
| build | builder | BUILD_LOG.md | in progress | 2026-10-20 13:20 | — | — |
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
| 2026-10-20 | **Architect overrides of the VISION on code facts (builder follows ARCHITECTURE.md):** no nanobot `--home` (use `--config`/`--workspace`); no tool calls through the edge (proposal = structured output, grammar-constrained `propose ∈ {none, pause_research}`); patch set is P0 `__main__.py` (+1), P1 `gateway_runtime.py` (+4/~2), P2 Telegram regex (~1), with the VISION's `base.py`/username patches replaced by a `BuddyTelegramChannel` subclass behind a seam lock; confirms handled at the channel boundary so the nonce never enters nanobot; ARAIL-unreachable window 40 s (not 120 s) to keep G1's 60 s; `presence_meter` skip and a per-step lanes list added in ARAIL; idle auto-compact off; cards only to the requesting confirmer. | ARCHITECTURE.md §2.4, §0. |
| 2026-10-20 | Edge transport: loopback HTTP on ARAIL's existing portal port under `/api/host/v1/*`; credential `bhc1_`+32 random bytes (ARAIL keeps the sha256 only); contract `buddy.host.v1.json` owned by Buddy, vendored byte-identical into ARAIL with a provenance stamp, sha256 on every request (mismatch → 412, Buddy closes his gate). Egress guard = CPython audit hook installed before any nanobot import. | ARCHITECTURE.md §5.1–5.3, §5.15. |
| 2026-10-20 | Build runs as one builder across both repos in ARCHITECTURE §9 order (B1 → A1–A9 → B3–B11), with B2/B7 last because they need the sealed `qa-practice` World (Phase C) on ddac main. Phase C drafting (C1) runs in parallel in a ddac worktree inside the session folder; C2 is the operator's approval line. | Dependency order in §9; sprint skill says phases are sequential, builders on disjoint repos are one phase. |
| 2026-10-20 | VISION v1 pass ran first and recommended staying in ARAIL; superseded, not deleted. | The brief changed afterwards (fork base, QA-SME, eval surface). |

## Skipped phases

| Phase | Reason |
|---|---|

## Preconditions before plan (VISION §5)

- (a) **Done 2026-10-20:** operator chose "QuKaiZen QA practice" (the qa persona's rules, sprint gates, witness lines, xfail-strict debt, per-product QA allocations, the vault hard gate), sourced to his own files at pinned commits.
- (b) **Done 2026-10-20:** `cdarnell/qukaizen-buddy` created private; `main` = full clone of HKUDS/nanobot at tag `v0.3.5` (`1bb712d3`), `upstream` remote kept, plus `UPSTREAM.md` (pin + patch rules) and `CLAUDE.md` (laws, G1–G12, QA allocation). Local clone: `<session>/qukaizen-buddy`.
- (c) Operator writes P1's W5 witness line before QA.

## Open items for the operator (ARCHITECTURE §12)

| # | Item | Blocks |
|---|---|---|
| O1 | Write P1's W5 witness line (precondition c). | QA step 7 |
| O2 | Provide Telegram accounts B (talker-only QA account) and C (no list) for the win run (§8.8). | QA |
| O3 | Approve the `qa-practice` cards in one line, in your own words, here in the decisions log (C2). | B2, B7, QA blind set |
| O4 | Accept the budget defaults: 300 turns and 600,000 tokens per day; 10/min and 200/day per person. | nothing (defaults apply) |
| O5 | Accept that proposal cards go only to the confirmer who asked. | nothing |
| O6 | Accept sha256-pinned sources for unversioned files such as `~/.claude/agents/qa.md`. | C1 |
| O7 | In BotFather, turn "Allow groups" off for Buddy's bot. | setup |

## Notes

- Frozen names: `aerollm_api`, `AERO_*`/`AEROLLM_*`, backend id `aerollm`,
  `tier1-aerollm` (workspace CLAUDE.md naming status).
- Cross-repo edges (nanobot, DDaC World, 3D.EVAL, paperagents formats) must
  be pinned or hash-stamped (`INTEGRATION_AUDIT.md` §4).
- P1's W5 witness line is still unwritten (REVIEW/TEST_REPORT of the
  2026-09-20 sprint). Flag for the operator before the architect pass.
