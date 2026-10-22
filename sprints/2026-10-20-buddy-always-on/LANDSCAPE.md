# Landscape: always-on personal bots, and where Buddy stands (2026-10-20)

Research artifact for the visionary. Two halves: what the open-source
"always-on bot" field looks like today, and a verified map of Buddy as it
exists on ARAIL main (PR #199, `ca0a8f81`). Everything in §2 was checked
against the code on 2026-10-20; file:line refs are relative to this repo.

## 0. Operator ask (verbatim intent, 2026-10-20)

> I want to take my buddy agent and make an always on bot. I want to expand
> Buddy's capabilities and look for open-source grok-bot-like programs to
> begin developing similar functionality into Buddy. Not sure where the code
> is, I think it's in ARAIL. May want a new repo for this.

Correction to the premise: **xAI's Grok Bot is proprietary and cloud-only**
(persistent xAI-managed VM, $200/mo standalone or bundled with SuperGrok
Heavy / Cursor Ultra; no self-host, no source). The things to learn from are
the open-source projects that do the same job on your own machine.

## 1. The field

### 1.1 What "Grok Bot" is (the target behaviour, not the code)

- Persistent cloud computer: files, browser sessions, logins and memory carry
  over; tasks keep running with the laptop closed.
- You *assign tasks* (desktop + iPhone app), you don't chat. It does not reach
  you over email/Slack/Telegram.
- Actions: browser (sites without APIs), files, terminal, connectors (Gmail,
  Google Calendar/Drive, Outlook, Teams, SharePoint, Salesforce), remote MCP.
- Approvals: Allow once / Deny / Always allow, plus "Auto Review" rules;
  sensitive steps hand control back.
- Multi-Bot: 2–6 named bots in one group chat on one shared VM; xAI says
  separate bots are *not* a security boundary.
- Scheduling: cron-style and Cursor-event triggers.
- Stated gaps: no self-host, xAI models only, no memory transparency
  (cannot inspect/correct/export/delete memories).

### 1.2 Open-source analogs (verified against each repo's README)

| Project | Lang / runtime | License | Channels | Scheduling | Memory | Sandboxing | Local models |
|---|---|---|---|---|---|---|---|
| **OpenClaw** (ex-Clawdbot/Moltbot; creator joined OpenAI 2026-02, project under a 501(c)(3)) | Node/TypeScript (pnpm workspace, some Rust `crates`, native `apps`); >1 GB RAM | MIT | WhatsApp, Telegram, Slack, Discord, Google Chat, Signal, iMessage, Teams, "20+" | cron inside the Gateway, jobs survive restart | per-session + agent memory | "trusted gateway, untrusted execution, policy in code"; DM pairing of unknown senders; tools run on host unless a sandbox is configured; 2026 security releases made components stop trusting each other by default | "hosted and local providers"; harnesses are swappable plugins |
| **Hermes Agent** (Nous Research) | Python core + JS/TS UIs | MIT | CLI, Telegram, Discord, Slack, WhatsApp, Signal, Email, Home Assistant | built-in natural-language cron delivering to any channel | three layers: session, persistent fact store (FTS5 + LLM summaries), skills it writes itself; Honcho user modelling | 7 terminal backends (local, Docker, SSH, Singularity, Modal, Daytona, Vercel); command approval; DM pairing | "your own endpoint" (OpenAI-compatible) |
| **nanobot** (HKUDS) | Python ≥3.11, "small core"; WebUI + TUI ship in the wheel | MIT | Telegram, Discord, Slack, WeChat, Feishu, Email, Mattermost, Linear, Teams | cron + "Tasks/Calendar" views; missed runs not replayed after restart; `/goal` keeps working across turns | `USER.md` + `memory/MEMORY.md`, `/dream` consolidation | security policy in the gateway; shell tool is on | OpenAI-compatible, Ollama, vLLM |
| ZeroClaw | Rust, ~3.4 MB binary | — | Discord, CLI | — | SQLite | — | local-capable (sources disagree) |
| PicoClaw | Go, <1 MB RAM targets | — | — | — | — | no screen/GUI | — |

Shared architecture across the three serious ones (OpenClaw, Hermes,
nanobot): **one long-lived gateway process** that owns sessions, tool
execution, memory and the cron; **channels** are thin adapters into it;
**skills** are markdown-first (agentskills.io standard for Hermes; ClawHub for
OpenClaw/nanobot); **MCP** is the plugin bus; nightly **memory consolidation**
("dream" in nanobot, learning loop in Hermes); **DM pairing** as the default
answer to "who may talk to the bot".

Things none of them have, which ARAIL already does: airgap-by-default with an
egress guard that patches `requests`/`urllib`/`httpx`; per-domain consent;
a kill switch over all agents; per-agent trace attribution; a deep/fast
model policy keyed to operator presence and Metal memory pressure.

Things all of them have, which Buddy does not: a reply path, tools, channels,
a scheduler the user can talk to, inspectable memory, an approvals model.

### 1.3 Sources

- https://github.com/openclaw/openclaw (README, read 2026-10-20)
- https://github.com/NousResearch/hermes-agent (README)
- https://github.com/HKUDS/nanobot (README)
- https://aimultiple.com/openclaw-alternatives ; https://www.vellum.ai/blog/official-grok-bot-breakdown (vendor-adjacent; Grok Bot facts only)
- https://github.com/xai-org (grok-1 weights, grok-prompts, grok-build: no bot runtime)

## 2. Buddy as it exists on ARAIL main

### 2.1 Where

- Core, 1,602 lines, Python only: `src/arail/agents/_builtin_buddy.py`
  (`BuddyHost` Protocol :68, `ArailHost` :98–189, `SYSTEM_PROMPT` :216–231,
  `WATCHERS` :601, `SUGGESTERS` :1046, `BuddyAgent` :1184–1504, dream :1511–1596,
  singleton :1602). Back-compat shim `src/arail/agents/buddy.py`.
- Editable PKB copy seeded at boot: `lab/pkb/agents/buddy/{buddy.py,AGENT.md,
  state.json,decisions.md,dreams/}` via `builtin_seed.py:521`.
- Entry: portal startup (`src/arail/portal/app.py:1212–1220`) → `loader.load_all()`
  / `start_all_auto()`. No CLI, no Buddy route, no server of its own.
- UI is one-way: dashboard toasts, agents-page card, admin agent lanes
  (`/api/admin/agent-lanes`, trace stream). **The user cannot reply to Buddy**;
  the only "chat" is a `/chat?agent=buddy&seed=…` deep link into the Chat tab.
- Docs: `docs/BUDDY.md` (describes a "Buddy Tunnel" and says the code does not
  exist), `docs/agents-explained.md`.

### 2.2 How it runs

In-process asyncio loop (`_run` :1302): sleep `LAB_BUDDY_INTERVAL_SEC`
(90 s default, 30 s floor) → run watchers → every 900 s also run suggesters
*only if a goal is set*. Global cooldown 300 s, per-watcher cooldowns. Output:
one paraphrased sentence ≤200 chars → `activity_log` → SSE to UI.

LLM: `ArailHost.llm_complete` → `deep_policy.complete_preferring_deep(
foreground=False)`. Deep = QueueLLM via `ModelRouter(backend="aerollm")` on
maximus, gated by: not halted, outside active window, operator absent, profile
allows, Metal pressure <60 %. Otherwise fast = registry `"fast"` entry
(`tier0-local`, Ollama/MLX resident). No cloud, no Gemma in Buddy code.

Capabilities: **no tool-calling**. Read-only watchers (RAM ≥92 %, inbox
pile-up, researcher wins/plateau, goal staleness, study streak, airgap events
from `egress.jsonl`) and suggesters (phase, pending review, skill, next
experiment, metric, hybrid nudge, HF daily-papers correlation). The HF fetch is
the only network call: fixed URL, hybrid mode only, after `ConsentStore`
consent. Suggestions carry links, never executable actions.

Memory: `state.json` (cooldowns, counts, airgap offset); nightly
`dreams/YYYY-MM-DD.md` via `dream_daemon.py` (15-min poll, heavy window),
indexed into the KB and fed back into prompts.

### 2.3 Already "always-on", already there

- Long-running loops inside the portal: Buddy, `dream_daemon`, `job_daemon`
  ("light cron", `LAB_SCHEDULER`), SRE, presence, librarian agents. A
  `logs/scheduler/qukaizen-lab-ceiling-check/` job is firing hourly today.
- Process supervision: `./arailctl install-daemon` → launchd, start at login,
  respawn on crash (`arailctl:251,286–410`, `scripts/install-daemon.sh`).
- P1 (PR #196, merged 2026-09-24): `agent_context` (trace ids, `halt_gate`,
  `speech_gate`), `agent_trace` (ring buffer + jsonl, lanes, flight recorder
  off by default), `redact`, `jsonl_purge`; tracing at the `ModelRouter`
  chokepoint; "hold all agents" kill switch (SRE exempt).

### 2.4 Not built

Prep queue (planned P6), page sense, event-driven voice (fixed cadence today),
OS notifications (P5), **any messaging integration** (grep: no Telegram,
Discord, Slack, iMessage, Twilio, ntfy), reply path, typed actions (scaffold
cut from P1), per-agent budgets (cut), inference gateway (cut), Buddy Tunnel.

### 2.5 Prior plan: `sprints/2026-09-20-buddy-front-and-center/OPERATOR_BRIEF.md`

D1–D18: slide-over panel on every page; goal interview → proposal → one-tap
confirm; typed actions only after a tap; event-driven speech with a
quiet/normal/chatty dial; catch-up card; page sense now, screen sense later;
QueueLLM MoE as the maximus voice with the 1B resident as "reflex" brain.
"Never caught unprepared" (:74–103): background prep queue through
`deep_policy`'s background gate on maximus; on minimalist uses World
templates, `parse_offline`, hardware fit, KB inventory with no LLM; Buddy
opens with a specific prepared observation, every claim cited.
Phases: P1 obs+guardrails (shipped, WEAK_PASS, W5 witness still open) → P2
brain bake-off → P3 panel + 5-minute goal interview → P4 "alive" → P5 typed
actions + OS notifications → P6 prep queue → P7 screen sense.

### 2.6 Security posture relevant to always-on

Airgapped by default; `allow_egress` raises when airgapped (`egress.py:536–556`).
Brief rules: model output / page / KB / World text may propose, never
execute; actions only from a typed allow-list after a user tap with CSRF;
containment structural, not prompt-based; no shell/file-write/command tools.
**Gaps:** portal has no authentication (`sprints/BACKLOG.md:1418+`), 17
`/api/admin/*` endpoints not tier-gated, no OS-level sandbox, F1/F6 xfail.
ARAIL runs on friends'/family machines.

### 2.7 Size and coupling

~1,660 lines Buddy-specific; ~1,380 lines shared P1 infra + `deep_policy`
(296). 11 `tests/test_*buddy*.py` files, 98 test functions. `BuddyHost`
Protocol was built as an extraction seam (~80 % clean), but 12 direct
`arail.*` imports remain (airgap/egress, consent, world_mount, agent_context,
pkb, activity) and the safety layer (hold/halt, tracing, deep/fast policy,
egress guard) lives in ARAIL's router/registry/scheduler. An extracted Buddy
would re-implement or depend on all of it.

## 3. Repo question, framed for the visionary

Three shapes are on the table; the visionary should pick one and say why:

1. **Stay in ARAIL.** Buddy keeps the airgap, consent, kill switch, tracing,
   deep/fast policy for free. Cost: always-on channels (Telegram etc.) are
   egress, which ARAIL refuses while airgapped; the portal has no auth.
2. **New repo (e.g. `qukaizen-buddy`): the gateway/channel/scheduler/tool
   layer**, with ARAIL as one "host" behind the existing `BuddyHost` seam
   (versioned edge, per workspace rule). Cost: the safety layer must be
   re-homed or depended on; two repos to keep in step.
3. **Adopt an open-source gateway** (OpenClaw / Hermes / nanobot) as the
   always-on shell and make Buddy a skill/persona inside it. Cost: their
   security model is "trusted gateway, host-executed tools", the opposite of
   ARAIL's "propose, never execute"; MIT lets us vendor, but the runtime
   (Node for OpenClaw) is foreign to the stack.
