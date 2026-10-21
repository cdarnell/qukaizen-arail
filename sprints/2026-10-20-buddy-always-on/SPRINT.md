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
| build | builder | BUILD_LOG.md | done (through Phase I) | 2026-10-20 13:20 | 2026-10-21 | builder declares done; answer-quality bar NOT met on the dev set (template 12/20 + 9/12; structured 6–10/20 + 8–9/12); blind set untouched |
| review | architect (review) | REVIEW.md | in progress | 2026-10-21 | — | — |
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
| 2026-10-20 | **Build stop point (by design).** ARAIL A1–A9 landed (`36349f7f` … `2ab0f244`, A5c `4395ca50`); Buddy B1, B3–B6, B8, B9, B11a, P0 (`f84aa75a`), P2 (`f696874d`) landed on `qukaizen/buddy-always-on`. Tests: upstream nanobot 7678 passed + 3 deselected (G3); ARAIL R-BUDDY-98 unchanged (100 passed, 1 xfailed); new ARAIL tests 406 incl. 127 conformance cases; whole ARAIL suite fails the same 70 tests on branch and main (none branch-only); Buddy 1070 passed. | BUILD_LOG.md steps 1–23. Nothing pushed. |
| 2026-10-20 | Blocked: B2/B7 need the sealed World on ddac main (C2 operator approval → C3); B7/B10/B11b/Phase I also need the architect's answer to **G1** (a `before_iteration` hook cannot rewrite `messages[0]` at the pin: `runner.py:443-447`, `context_governance.py:139-173`), plus ratification of G2/G4 interpretations, G3 (P0 deselects 3 upstream WebUI smoke tests) and G5 (tap removes the keyboard, so a lost edge answer cannot be retried by tap). | BUILD_LOG.md "Architect feedback required". Architect addendum requested. |
| 2026-10-20 | **C2 (operator): qa-practice cards approved.** Operator's words, verbatim: "approved please go". 20 cards at ddac branch `qukaizen/ddac-qa-practice-world` commit `796c13d`. | Written by the operator in chat; recorded verbatim. Unblocks C3 → B2/B7. |
| 2026-10-20 | **Harness alignment** (operator: "see qukaizen.com/harness and update and align accordingly"): S1 mapped onto Registry/Model/Context/Guardrails/Loop; two recorded differences (8–14B resident vs the 1B S1 brain; "nothing leaves the machine" → proposed gate G13: off-box messages carry World rules, fixed texts and the operator's own words, never lab data). `qukaizen-harness` extraction = later sprint (O8). | `HARNESS_ALIGNMENT.md`; architect ratifies G13 in Addendum A. |
| 2026-10-20 | **C3 done, World merged.** ddac PR #53 merged; ddac `main` = `da2fb18` is the `qa-practice` pin (20 terms, gate ok, roster drift 0, tests 1377/1385 pass = main). Operator's blanket go: "do not stop and you have my approval to make this work." | Unblocks B2/B7. |
| 2026-10-20 | **Addendum A (architect, `162d9d22`):** G1 = no fourth patch; `install()` R8 wraps `agent.context.build_system_prompt` (header + persona, fail-closed) and `agent._build_transcript_input` (window + scrub); edge requires `messages[0]` system, no other system message, non-empty persona; test U-HDR-03. G3 = P0 stays, deselection is debt D23 paid at each pin move. G5 = code never printed; one automatic resend of the stored approval record 2 s after a lost answer (I-CRASH-02a–d). G2 all ratified; A8 deltas: edge never reports `length`, atomic budget re-check after the slot. G4: `/stop` survives a closed gate; unlabelled replies default to `error`. Contract changed additively → A5 re-run. New failure modes F46–F55. | BUILD_LOG "Architect feedback required" answered. Harness subsection (G13) being appended. |
| 2026-10-20 | **§A11 harness alignment landed (`4fe3acf6`); G13 accepted** under the operator's instruction "see qukaizen.com/harness and update and align accordingly" + "you have my approval to make this work": off-box messages carry sealed World rules, fixed texts and the operator's own words, never lab data. Built code breaks it in three places (`commands.py:85-90` `/status` lab-mode/research/brain lines; `confirm.py:93` card "Research now" line): builder fixes them in the resumed build. | ARCHITECTURE §A11; HARNESS_ALIGNMENT.md. If the operator rejects G13, those three lines return and the ledger records it. |
| 2026-10-20 | Build resumed in Addendum A §A6 order: B1d → A5d → A8b; B4b → B8b → B9b; B7a → B10 → B11b → I1–I3; then B2 → B7b → I1 again after C4 (QA's blind set, sha256 into this ledger). | Builder relaunched; qa writes C4 in parallel and seals it. |
| 2026-10-20 | **C4 pre-registration (qa): blind QA-SME set sealed.** `~/.qa-sealed/2026-10-20-buddy-always-on/blind.jsonl` (0600; path moved from `~/ProJects/.qa-sealed/…` because the app's write hook guards `~/ProJects`), 28 lines (Q01–Q20: 16 answer, 4 abstain; P01–P08: 6 refuse, 2 escalate). **sha256 `db7c9827a19911ffcdfb2b94bfac133320bec2d1c58d456d96df776cc208bad2`.** World pin: ddac `da2fb181816875b26a861dda4f73db2eec186fce`, `terms.json` blob `a9eac3a2…` sha256 `767286098f3ca633524be103c1f22c25efa5dd595f2f0c49e216e998c7ff9731`. Builder may start B7b. | ARCHITECTURE §8.4 steps 1–2. After grading, QA commits the file and its sha256 must equal this one. Note: `spec.json` at the pin still carries the "C1 DRAFT" status line (cosmetic; fixing it reseals the World, deferred). |
| 2026-10-21 | **Build complete through Phase I.** Buddy: 40 commits on `qukaizen/buddy-always-on` (B1d, B4b, B8b/c, B9b, G13 fixes, B7a, B10+P1, B1e, B11b, B2, B7b, I1, I3, debts D29–D32); ARAIL: 6 (A8b, A5d–A5f, A9b, log). `nanobot/` changed only in P0–P2 (3 files, +9/−3). Tests: upstream 7678 passed; Buddy 1644 passed; R-BUDDY-98 100 passed/1 xfailed; ARAIL new 428 passed; ARAIL whole suite 64 failed / 7350 passed with ONE branch-only failure (`test_ollama_unload…live_daemon`, passes alone on branch, fails alone on main; overlapped the builder's live Ollama runs). I1 29/29 against real ARAIL with real pins; I2 contract sha256 equal both sides (`48481c1d…a785516`); I3 10 min airgapped, 1076 lsof samples, zero non-loopback rows (plain subprocess, no launchctl, no nettop). Nothing pushed. | BUILD_LOG.md Steps 24–26, Final state. |
| 2026-10-21 | **Answer-quality bar not met on the builder's 32-case dev set** (bar: 16/20 + 6/8): template mode 12/20 + 9/12, structured 6–10/20 + 8–9/12. Expected: S1 ships in template mode (VISION §2.3 "if the bar fails, S1 still ships with template answers; disconfirming #2 fires") after QA's blind run in both modes (`[answer] mode` in `buddy.toml`, default `structured`). Builder decisions H1–H9 (code decides reply label / citable cards / whether the model sentence shows; refusals hold 10 min; persona no longer names `pause_research`; Addendum A C6.2 vs C8 scrub-placeholder contradiction fixed in `2364fbe0`) go to the architect's review for ratification. | Review phase started. |
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
| O3 | **Done 2026-10-20** ("approved please go", decisions log). | — |
| O4 | Accept the budget defaults: 300 turns and 600,000 tokens per day; 10/min and 200/day per person. | nothing (defaults apply) |
| O5 | Accept that proposal cards go only to the confirmer who asked. | nothing |
| O6 | Accept sha256-pinned sources for unversioned files such as `~/.claude/agents/qa.md`. | C1 |
| O7 | **Done 2026-10-20** (operator: "Allow groups is off in BotFather"). | — |
| O8 | Is `qukaizen-buddy` the seed of `qukaizen-harness` (page: "five modules, zero product imports"), or does Buddy later consume a separate harness repo? Working assumption: separate repo, extracted when the TEAM crew needs the same floor. | nothing in S1 |

## Notes

- Frozen names: `aerollm_api`, `AERO_*`/`AEROLLM_*`, backend id `aerollm`,
  `tier1-aerollm` (workspace CLAUDE.md naming status).
- Cross-repo edges (nanobot, DDaC World, 3D.EVAL, paperagents formats) must
  be pinned or hash-stamped (`INTEGRATION_AUDIT.md` §4).
- P1's W5 witness line is still unwritten (REVIEW/TEST_REPORT of the
  2026-09-20 sprint). Flag for the operator before the architect pass.
