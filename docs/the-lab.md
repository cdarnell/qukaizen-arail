---
title: "The lab, end-to-end"
description: "A 12-minute runbook — what ARAIL is, how to set it up, every surface, and all the goodness."
category: "Getting Started"
order: 0
tags:
  - tour
  - runbook
  - setup
  - surfaces
  - overview
  - getting-started
read_minutes: 12
audience: beginner
related:
  - INSTALL
  - agents-explained
  - BUDDY
  - BLUEPRINTS
buddy_prompt: "Walk me through the lab as if I just opened it for the first time. What should I look at first, and what's the one thing that'll make me say 'oh, that's cool'?"
---

# The lab, end-to-end

A 12-minute read. By the end you'll know what ARAIL is, what each tab does,
how to set it up, and where the goodness is. It is the doc you keep open the
first week.

> *Every tab teaches something. Every loop you run leaves notes behind.
> That's the lab.*

---

## What this is

ARAIL — Autoresearch AI Labs — is a **blueprint, not a product**. Clone it,
pick a tier, and you have a local AI research bench: a dashboard that watches
your agents work, a chat tab that can route to your machine or any cloud
vendor, an autoresearch loop that runs experiments while you sleep, a
LanceDB-backed knowledge base your agents read from and write to, and three
built-in agents (Buddy, SRE, Researcher) you can extend or replace.

It runs **on your hardware, on your terms**. Airgapped by default. Rename
it `Sam's AI Lab` in one line of `.env`. Fork it, change the agents, swap
the models, share what you build. The Python package stays named `arail`;
the lab itself is yours.

---

## Why this matters

The default move with AI is to rent it from someone else — a chat box, a
SaaS, an API key. That's fine for one-off questions. It is not fine for
*learning*. Learning needs **a place you control**, **a goal you set**, and
**a loop that doesn't forget**.

ARAIL is that place. The lab is **local-first** so your notes, your data,
and your weights stay on your machine. It's **agent-driven** so the work
compounds — Buddy remembers what you read last week, the Researcher carries
your goal across restarts, the Knowledge Base accumulates instead of
resetting. And it's **a blueprint**, so when you outgrow it you don't beg a
vendor for a feature — you fork.

> *"A rail gun for AI."* — A learn-by-doing AI lab for friends, family,
> and the curious.

---

## The two tiers at a glance

Two tiers. Pick one; upgrade later. The choice is mostly about how much
disk and orchestration you want, not about what the lab can think about.

| Tier  | What you get                                                                                                  | Good for |
|-------|---------------------------------------------------------------------------------------------------------------|----------|
| `min` | Dashboard · Chat · Autoresearch · Knowledge Base · Agents · LanceDB vectors · **AirLLM 70B** (Llama-3.1-70B)   | The everyday lab. Real models on small hardware. A blueprint to hand a friend. |
| `max` | Everything in `min` + Admin · Docs · Notebooks · **AirLLM 405B** (Llama-3.1-405B) · Anthropic SDK · LangChain | Frontier-scale local inference, full bench, heavy orchestration. |

Knowledge Base and Agents are in `min` deliberately — research without
memory is a non-starter. Upgrade with `./arailctl upgrade max` any time;
downgrade with `./arailctl upgrade min` (it doesn't uninstall packages, it
just hides the extra tabs).

For the list of models that have been validated against ARAIL's correctness
harness — **Certified**, **Compatible**, **Beta**, or **Known Issue** — see
[CERTIFIED_MODELS.md](CERTIFIED_MODELS.md).

---

## Setup in five minutes

Three keystrokes and a browser. If you've never run a Python app from a
terminal, this is for you — `./arailctl setup` carries most of the weight.

### 1. Clone and enter

```bash
git clone https://github.com/qukaizen/arail.git
cd arail
```

### 2. Setup

```bash
./arailctl setup
```

This walks you through 10 idempotent steps:

1. Detect your platform (macOS / Linux / WSL2) and accelerator (MLX / CUDA / CPU).
2. Install OS packages if needed (brew, apt, dnf, pacman, emerge).
3. Create a Python venv and install tier-specific deps.
4. Name your lab — default *Autoresearch AI Lab*, rename to taste.
5. Pick a passphrase (protects the in-browser IDE + notebook encryption).
6. Write `.env`.
7. Scaffold the knowledge base.
8. Download a starter model (~5 GB for Qwen3-8B).
9. Capture a research goal.
10. Smoke-test.

Re-run any time. Nothing breaks if you change your mind.

### 3. Start

```bash
./arailctl start
```

Open <http://127.0.0.1:8080>. You're in.

> **Try this in your lab:** finish setup, open the dashboard, and just
> watch for 30 seconds. The activity stream tells you Buddy is awake, the
> mission box shows the goal you typed in step 9, and the cost meter
> (always zero unless you've added a cloud provider) reminds you nothing
> is leaving the box.

### Platform deep links

- **macOS** — [MACOS.md](MACOS.md) (Apple Silicon + MLX).
- **Linux** — [LINUX.md](LINUX.md) (CUDA, AMD, CPU fallback).
- **Windows / WSL2** — [WSL.md](WSL.md) (GPU passthrough).
- **Long form** — [INSTALL.md](INSTALL.md) (the full version of this section).
- **Adapting for someone else** — [vibe-integrate.md](vibe-integrate.md).
- **First-run problems** — [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

---

## The five surfaces

The portal is five tabs in `min`. Each one does one thing well; together
they form a loop — set a goal, ingest the materials, ask the model, watch
the agents work, check the dashboard. The lab is designed so you can
spend a whole afternoon without leaving the browser.

### 📊 Dashboard

The lab's home screen. The mission box across the top shows the current
goal (set from Autoresearch — they stay in sync). Underneath, the
activity stream is a live tail of what every agent is doing: Buddy noticed
something, the Researcher started an experiment, the SRE saw a recurring
error. The cost meter on the right is honest about cloud spending — zero
in airgapped mode, real numbers in hybrid. The dashboard is your
peripheral vision; you glance at it the way a pilot glances at instruments.

> **The magic moment:** set a goal in Autoresearch, switch back to the
> dashboard, and watch the mission appear there within a second. Two
> surfaces, one source of truth — the lab is built around the goal.

**Go deeper:** [missions.md](missions.md) explains how goals become
swarms of worker agents.

### 💬 Chat — with a Compute Source pivot

Built for crappy machines and beefy ones alike. A **Compute Source** row
at the top lets you pick — in one click — where the model runs:

- **My Machine** — local (MLX on Apple Silicon, CUDA on NVIDIA, llama.cpp
  on CPU). No key needed.
- **Claude** — Anthropic's API.
- **NVIDIA NIM** — free credits at build.nvidia.com.
- **OpenRouter** — paid catalogue of hosted open-weight models.
- **HuggingFace** — HF Inference API.
- **Custom endpoint** — any OpenAI-compatible URL (LM Studio, Ollama,
  your own vLLM).

The **⚙ Manage providers** modal saves keys, **tests** them (the lab
pings each vendor's `/models` endpoint), **lists** available models, and
**removes** tokens. Saved keys live in `lab/data/secrets.env` with
`chmod 0600`, never echoed back to the UI, never logged.

> **The magic moment:** start a chat with a local 70B, switch the
> Compute Source dropdown to Claude mid-conversation, and the *next*
> message goes through Anthropic. Same window, same memory, different
> brain. You learn what each model actually feels like.

**Go deeper:** [PRIVACY.md](PRIVACY.md) for the airgap guard;
[CERTIFIED_MODELS.md](CERTIFIED_MODELS.md) for what works where.

### 🔬 Autoresearch

**This is where you set the goal.** Tell the lab a measurable goal —
*"make our chat responses land under 400ms on my MacBook"* — and it runs
an experiment loop: propose a change, measure, compare against the
baseline, write up what it learned. The goal is lab-wide; once set here,
the Dashboard mirrors it live, and the Researcher agent picks it up.

You watch from the dashboard and steer from the Autoresearch cockpit when
the direction drifts. The loop is the whole point of the lab — it's why
you let your machine think while you sleep.

> **The magic moment:** set the goal, walk away, come back in an hour,
> and read what the lab did. You'll find a baseline, an experiment, a
> diff, and a one-paragraph summary in Buddy's voice. That's a research
> session that ran without you.

**Go deeper:** [missions.md](missions.md), [tuning-loop.md](tuning-loop.md).

### 📚 Knowledge Base

The lab's long-term memory. Drop papers, notes, web pages, or PDFs into
`lab/pkb/inbox/` (the **Reveal inbox** button in the DaC tab opens
the folder for you), and agents ingest them into a LanceDB vector index.
Both the Chat tab and Autoresearch search the KB — the answers you get
start citing your own materials. CLI ingest works too:
`./arailctl pkb ingest <file>`.

The embedded wiki graph at the top of the DaC tab updates live
whenever a doc is ingested or a cross-link is added. Click any node to
jump to that page; click again to see what cites it.

> **The magic moment:** ingest a 30-page paper, switch to Chat, ask
> *"what's the central claim?"* — the answer comes back with a quote and
> a link to the page in your KB. You just built RAG on yourself.

**Go deeper:** see the Hub category **Operating** for the KB operator
guides as they land.

### 🤖 Agents

Three built-in personalities, each a loop that watches the lab and
speaks up:

- **Buddy** — your lab partner. Observes everything, writes warm two-line
  summaries, surfaces what you'd otherwise miss.
- **SRE** — the crash watcher. Notices recurring errors and posts to the
  activity stream so you don't lose a day to a silent broken loop.
- **Researcher** — the engine behind Autoresearch. Reads goals, writes
  code, runs experiments, commits the winners.

Bring your own by dropping `lab/pkb/agents/<id>/AGENT.md` plus
`lab/pkb/agents/<id>/<id>.py` — the loader discovers it on the next
start. No registration, no extra config.

> **The magic moment:** write a 40-line agent that watches your inbox
> and tells Buddy when an email mentions the current research goal.
> Restart. It's already showing up in the activity stream.

**Go deeper:** [agents-explained.md](agents-explained.md) — the on-ramp;
[agents.md](agents.md) — the loader contract and full architecture.

---

## Buddy, your lab partner

Buddy is the lab's resident agent. He runs in airgapped mode by default,
loads his skills and memory entirely from local disk, and talks to the
local model through the `my_machine` provider. Conversations never leave
the box.

What Buddy actually does for you:

- **Tracks the current mission.** Whatever you set in Autoresearch,
  Buddy keeps it in working memory and brings it up when relevant.
- **Watches the activity stream.** When the Researcher commits a result,
  the SRE catches an error, or you ingest a new doc, Buddy is the one
  who writes the human-readable line on the dashboard.
- **Answers from the KB.** Ask Buddy about a paper you ingested and he
  pulls the relevant section, cites it, and offers to dig further.
- **Stays in his lane.** Buddy doesn't try to be everything. SRE
  handles crashes; Researcher runs experiments; Buddy talks to you.

You'll see "Ask Buddy about this doc" buttons throughout the docs —
including at the bottom of this one. Hit it, and Buddy opens in the Chat
tab with the doc you were reading already loaded as context. He'll talk
to you about it in the lab's voice.

> **The magic moment:** on day three, open Buddy and ask *"what did I
> work on Monday?"* — and he tells you, because he was watching.

**Go deeper:** [BUDDY.md](BUDDY.md) — the full Buddy doc, including the
honest version of Buddy Tunnel.

---

## Compute Source — local first, cloud when you want it

This is the single most important mental model in the lab.

**Default mode is airgapped.** Cloud providers are blocked at the HTTP
layer — agent-originated outbound calls through `requests` and `urllib`
are denied unless the destination is loopback, RFC1918, or link-local.
LAN GPU boxes (Ollama, vLLM, a QueueLLM node) keep working — only the
public internet is sealed. Denials append one line to
`lab/data/egress.jsonl` for audit.

**Flip to `LAB_MODE=hybrid` and cloud vendors become fallbacks.** The
Compute Source row in Chat is the user-facing pivot: choose which brain
answers the next message. You'll see which provider is active; you can
pivot back with a click. The Network Policy modal in the nav lets you
toggle the mode from the UI when the lab is bound to loopback (it's
disabled when the portal is exposed on a LAN, to avoid CSRF).

**The mental model:** the lab is *yours*, and any cloud reach is a
deliberate, visible, audited opt-in. You always know which side of the
line you're on — there is no "sometimes online" middle state.

> *"Cloud vendors become fallbacks when the local model isn't enough.
> Pivot back with a click."*

**Go deeper:** [PRIVACY.md](PRIVACY.md) — exactly what is and isn't
enforced, and the known gaps.

---

## Make it yours — blueprint mentality

ARAIL ships as one blueprint: **Autoresearch**. It's a researcher agent,
a curator, an experiment tracker, and a default goal-capture prompt. If
that's your work, great. If it isn't, fork it. Change the agents. Swap
the models. Rewire the integrations. The Python package stays named
`arail`; your lab looks different.

Three other reference blueprints already ship under `blueprints/`:

| Blueprint        | Tier | Default model | Goal                                                                  |
|------------------|------|---------------|-----------------------------------------------------------------------|
| `autoresearch`   | min  | Qwen3-8B      | Researcher + curator + experiment tracker on a topic you set          |
| `status-digest`  | min  | Qwen2.5-3B    | Monday-morning brief — what shipped, blocked, needs attention         |
| `inbox-triager`  | min  | Qwen2.5-7B    | Email classification + reply-drafting (consent-gated, never auto-sends) |
| `client-followup`| max  | Qwen2.5-7B    | Post-meeting follow-up + relationship cadence for consultants         |

Spin one up with `./arailctl blueprint create <name> --from <template>`
— it scaffolds an isolated instance under `instances/<name>/` (its own
`.env`, `lab.conf`, port range). The default lab at the repo root is
untouched.

Rebrand the lab in one line of `.env`:

```bash
LAB_NAME="Sam's AI Lab"
LAB_TAGLINE="Our family AI bench"
```

`./arailctl restart` and every banner, nav logo, activity line, and wiki
landing page now says "Sam's AI Lab".

> *Here is a blueprint. Build on top of it, or replace it.*

**Go deeper:** [BLUEPRINTS.md](../BLUEPRINTS.md) — the principle in
full, plus the schema and authoring guide.

---

## Where to go next

Three guided paths, depending on what you came here for.

### *"I want to understand agents deeply."*

You're the operator-engineer who wants to know how the loop ticks.

1. **[agents-explained.md](agents-explained.md)** — the on-ramp. What an
   agent actually is in five minutes.
2. **[agents.md](agents.md)** — the loader contract, the full
   architecture, what every file in `lab/pkb/agents/<id>/` does.
3. **[BUDDY.md](BUDDY.md)** — Buddy in depth, including the honest
   version of Buddy Tunnel.

### *"I want to publish my lab to the public internet."*

You want others to reach this lab — friends, a small community, a
business use case.

1. **[PUBLISH.md](PUBLISH.md)** — the full playbook: nginx/Caddy,
   Cloudflare Access, the hardening checklist.
2. **[PRIVACY.md](PRIVACY.md)** — exactly what data leaves the box and
   what doesn't. Read this *before* you expose anything.
3. **[SECURITY.md](../SECURITY.md)** — the threat model and how to
   report issues.

### *"I want to contribute back upstream."*

You want to fix something, add something, or share a blueprint with
the ARAIL community.

1. **[CONTRIBUTING.md](../CONTRIBUTING.md)** — how to send a patch back.
2. **[ROADMAP.md](../ROADMAP.md)** — what's coming, where to find a
   good first issue.
3. **[BLUEPRINTS.md](../BLUEPRINTS.md)** — how to author and share a
   new blueprint.

---

## Appendix — Ask Buddy about this doc

At the bottom of every doc in the lab — including this one — there's an
**Ask Buddy about this doc** button. Click it, and Buddy opens in the
Chat tab with the doc you were reading already loaded as context. He
talks to you about it in the lab's voice.

For this runbook specifically, the seed prompt is:
*"Walk me through the lab as if I just opened it for the first time.
What should I look at first, and what's the one thing that'll make me
say 'oh, that's cool'?"*

Try it after you finish setup. The fastest way to *learn* the lab is to
*talk* to the lab.

— start with `./arailctl setup`.
