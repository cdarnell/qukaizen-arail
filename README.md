# ARAIL — Autoresearch AI Labs
**A rail gun for AI.**

ARAIL is a blueprint, not a product. Clone it, pick a tier, and you have a
local AI research bench with some visibility. RAG'd up chat tab that can connect
to your machine or any cloud vendor, an agent-driven knowledge base, and a
loop that runs experiments while you sleep.


ARAIL evolves with Autoresearch from Karpathy.   Draft, review and approve a plan to begin experimenting and researching  
<img width="852" height="639" alt="Screenshot 2026-05-01 at 8 01 48 PM" src="https://github.com/user-attachments/assets/17a0eb3a-2160-4c5f-b980-2289a75424b0" />

<img width="1512" height="954" alt="Screenshot 2026-07-14 at 10 20 38 PM" src="https://github.com/user-attachments/assets/0ce31f7a-c3b7-487f-bffe-bf3b9c42c146" />
<img width="1512" height="969" alt="Screenshot 2026-07-14 at 10 21 17 PM" src="https://github.com/user-attachments/assets/47596455-f99d-4997-8e79-da72108a6b92" />


>
>
> *A learn-by-doing AI research lab for friends, family, and the curious.*
> Default name is **Autoresearch AI Lab**. Rename it to whatever you want in one
> line of `.env` — "Sam's AI Lab", "gentoofoo's ai lab", "PeanutLab". It's
> your lab.
>
>
>


<img width="1398" height="783" alt="Screenshot 2026-05-01 at 8 08 10 PM" src="https://github.com/user-attachments/assets/c6eeff47-975f-4ed9-bf0b-13080e8d76a7" />


Inference from an AI Engineer built by Nucleus
*
<img width="1423" height="727" alt="image" src="https://github.com/user-attachments/assets/4a6be689-a5cc-432f-a514-796d07f1938c" />








---

> **New here?** [The lab, end-to-end](docs/the-lab.md) is the 12-minute
> runbook tour — what ARAIL is, every surface, and how to set it up.

## Quick start

```bash
git clone https://github.com/qukaizen/arail.git
cd arail
./arailctl setup      # pick a tier, install deps, download a starter model
./arailctl start      # open http://127.0.0.1:8080
```

Three keystrokes: `./arailctl setup`, `./arailctl start`, and a browser. (If you
prefer a shorter alias, `./qkz` is symlinked to `./arailctl`.)

---

## Pick a tier

Two tiers. Pick one; upgrade later.

| Tier  | What you get                                                                                                            | Good for                                         |
| ----- | ----------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| `minimalist` | Dashboard · Chat · Autoresearch · Knowledge Base · Agents · LanceDB vectors · **Llama AI Engineer** — an AI engineering assistant **built with Llama** (Llama-3.2-1B-Instruct, ~0.9 GB, runs on 16 GB) | The everyday lab. One default model. No bloat. |
| `maximus`    | + Admin · Docs · Notebooks · **QueueLLM** deep-mode runtime · Anthropic SDK · LangChain · full cloud SDKs · **AI Engineer (deep, 7B)** — a deep AI engineer persona (offered, not forced; ~4.7 GB) | The full bench. The heaviest model that runs *well* on your machine — with cloud frontier models one click away in the Chat Compute Source. |

> **Same expert, two sizes.** Both tiers give you the *same* AI-engineer persona; they differ only in
> the model behind it. `minimalist` runs it on Llama-3.2-1B (fast, fits 16 GB). `maximus` additionally
> *offers* a deeper 7B variant (`ai-engineer`, Qwen2.5-7B, Apache-2.0) for harder reasoning — opt-in, not forced.

> **Built with Llama.** The default model (`llama-ai-eng`) is built on Llama-3.2-1B-Instruct
> (Meta Platforms, Inc.) under the [Llama 3.2 Community License](licenses/LLAMA-3.2-COMMUNITY-LICENSE.txt).
> See [NOTICE](NOTICE) for attribution details.

> **Coming: a stronger default brain (Built with Gemma).** A ~2B Gemma generalist
> (`qkz-project-aware-2b`) is staged to become the minimalist default-floor — better at technical
> domains (physics/math) than the 1B, still 16 GB-friendly. It is **dormant** until its weights land;
> when armed it ships **built with Gemma** under the [Gemma Terms of Use](licenses/GEMMA-TERMS-OF-USE.txt)
> (see [NOTICE](NOTICE)). Until then the default stays `llama-ai-eng`.

> **On a tight budget or CPU-only?** Before the 1 GB default, you can browse-and-pull an even
> smaller starter from the Chat catalog: `qwen2.5:0.5b-instruct-q4_K_M` (~0.4 GB, Apache-2.0) runs
> on CPU and well under 8 GB RAM — the same on-box small model [qukaizen.com](https://qukaizen.com)
> serves on CPU. It's less capable (weak at hard code/reasoning), so treat it as a starting point
> and move up to `llama-ai-eng` when your hardware allows. This is just a pullable option — the two
> tiers above are unchanged.

`llama-ai-eng` is the only model that ships pre-installed. The chat catalog
includes ~20 other models (Qwen, Gemma, Phi, DeepSeek-R1, etc.) you can
browse and pull on demand. AirLLM is opt-in via `ARAIL_INSTALL_AIRLLM=1`.

> **QueueLLM deep mode is Apple-Silicon-only today (CUDA in progress).**
> The `maximus` deep-mode "2nd inference" runtime, [QueueLLM](https://github.com/cdarnell/qukaizen-queuellm)
> (formerly AeroLLM — commands, env vars and the `aerollm-api` package keep the
> old name for now; Apache-2.0, now open source), runs on **MLX / Apple Silicon** only — the
> published wheel is `macosx_arm64`. The **CUDA backend is in active
> development** and not built yet, so on Linux/x86 `maximus` the deep runtime
> is skipped and AirLLM (opt-in) is the fallback. QueueLLM is **not** a setup
> dependency — `./arailctl setup` never blocks on it. Install or refresh it
> out-of-band with `./arailctl deep install` (a checksummed prebuilt binary
> from ARAIL's own GitHub Releases — no source repo or credentials needed;
> this is what `./arailctl setup` at tier `maximus` uses), or, if you're a
> maintainer, `./arailctl deep rebuild` (source build from a local QueueLLM
> checkout) or `./arailctl deep update` (release wheel from the private
> index); all three fail soft, so the lab runs fine without the 2nd
> inference until one succeeds. **`deep install`'s binary is unsigned,
> prebuilt native code that executes on import** — it's integrity-checked
> against the release manifest (same-origin trust: GitHub TLS + repo
> access control), not signature-verified or sandboxed. See
> [SECURITY.md](SECURITY.md) and [docs/cli.md](docs/cli.md) for the full
> trust-boundary disclosure.

Upgrade any time:

```bash
./arailctl tier maximus
```

(`./arailctl upgrade maximus` still works — `upgrade` is a permanent
alias for `tier`. Bare `./arailctl tier` prints the tier you're on.)

Knowledge Base and Agents are part of `minimalist` on purpose — research
needs memory to work. Both tiers ship with the embedded LanceDB-backed KB;
`maximus` adds the heavier operator surfaces and orchestration extras.

For the list of models that have been validated against ARAIL's
correctness harness — what's **Certified**, **Compatible**, **Beta**,
or has a **Known Issue** — see
[docs/CERTIFIED_MODELS.md](docs/CERTIFIED_MODELS.md).

External providers (Claude, NVIDIA NIM, OpenRouter, HuggingFace) are
reachable in both tiers via plain HTTP — `maximus` just adds the official
SDKs and LangChain/LangGraph for heavier orchestration. **Airgapped mode
is the default: agents cannot collect information from the public
internet.** Local services on this machine and your private network
(loopback, RFC1918, link-local) stay reachable so a LAN GPU box keeps
working. Flip `LAB_MODE=hybrid` in `.env` to allow agent fetches to cloud
vendors.

See [docs/INSTALL.md](docs/INSTALL.md) for the long walkthrough.

---

## The main surfaces

### 🌍 Worlds *(every tier)*

**Start here.** A World is what your lab studies — a knowledge base of
terms, categories, and how they relate, plus a matching look. Name any
subject — *"5th grade mathematics"*, *"differential equations"*,
*"botany"*, *"indoor plants & their care"* — pick a size (25/50/100
terms), and your local model **forges** a World in a couple of minutes:
categories, seeded terms, a discovered association graph, definitions,
all honestly labeled `model-asserted` until curated. Preview it, then
**Accept & Mount** — the whole lab's identity, theme, and agent context
flip to match. Edit terms afterward in **Knowledge → World Terms** (every
edit re-seals the World and updates its provenance honestly), or ask the
**Curator** to review it — a model judges terms for miscategorization or
bad associations and flags them for you to fix. A World is the objective
starting point; a **goal** (set on the Dashboard) is what you study
*within* it — Buddy and the autoresearch loop gear toward both together.
Ships with two pre-curated Worlds — **AI & Machine Learning** and
**QuKaiZen** (the lab explaining itself) — vendored *inside this repo* as
sealed, integrity-checked bundles: a single ARAIL download contains every
World dependency, nothing is fetched. An airgapped lab needs nothing else —
research material is whatever you drop into the Knowledge Base, and the
fundamental domain terms come from these in-box Worlds (see
`lab/worlds/README.md`).

**Run more than one World at once.** `./arailctl start --world <slug>`
launches a World as its own process, on its own port, with its own
isolated knowledge base and secrets — a *work* lab and a *personal* lab
that genuinely cannot see each other, side by side, up to
`LAB_MAX_INSTANCES` (default 3). `./arailctl status` shows every running
instance; `./arailctl stop --world <slug>` stops just one. See
[`docs/concurrent-worlds.md`](docs/concurrent-worlds.md). In-place
World-switching (mounting a *different* World over one already mounted in
this lab) has been removed — `/worlds`' Mount button now only performs the
first bind or an unmount; use instances to run more than one World at once.

### 📊 Dashboard *(every tier)*

The lab's home screen. Shows the **current mission** (set from
Autoresearch), what the agents are doing, the simulated cloud cost of
today's work, and an activity stream so nothing happens out of view.
Mission text and breakdown update live when a goal is set or changed
elsewhere.

### 💬 Chat — with a Compute Source pivot *(every tier)*

The Chat tab is built for crappy machines and beefy ones alike. A
**Compute Source** row at the top lets you choose in one click:

- **My Machine** — runs the model locally (MLX on Apple Silicon, CUDA on
  NVIDIA GPUs, llama.cpp on CPU-only boxes). No key needed.
- **Claude** — Anthropic's API.
- **NVIDIA NIM** — free credits at build.nvidia.com.
- **OpenRouter** — paid catalogue of hosted open-weight models.
- **HuggingFace** — the HF Inference API.
- **Custom endpoint** — any OpenAI-compatible URL (LM Studio, Ollama,
  your own vLLM server).

**Settling "My Machine."** The first time a lab boots, a banner under the
statusbar (every page, not just Chat) asks two questions in plain
language: which model should load into GPU/memory right now, and which
model should QueueLLM reference for deep answers. Each candidate shows its
download size, whether it's actually on this machine's disk/Ollama store
yet, and whether it fits — with the exact `ollama pull` / `hf download`
command (plus an HF link) when it isn't installed. Confirm once and the
banner is gone for good, reappearing only if something breaks later — the
configured model gets uninstalled, no longer fits, or `.env` drifts away
from what you settled. `./arailctl start`'s terminal banner shows the
same read-only summary. See `model_defaults.yaml.example` and
`docs/cli.md`'s `start` section for the mechanics.

The **⚙ Manage providers** button opens a modal where you save each key,
**test** it (the lab pings the vendor's `/models` endpoint), **list** the
available models, or **remove** the token. Tokens persist to
`lab/data/secrets.env` with `chmod 0600` and are git-ignored. Tokens are
never echoed back to the UI after saving and never printed to logs.

**Airgapped guard.** By default `LAB_MODE=airgapped`. Agent-originated
outbound calls through `requests` and `urllib` are denied unless the
destination resolves to loopback, RFC1918, or link-local. Denials raise
`EgressBlocked` and append one line to `lab/data/egress.jsonl` for audit.
The Compute Source row shows a banner, cloud radios grey out, and the
save/test/models endpoints refuse. Click the **Airgapped** badge in the
nav to see the operational definition and the most recent blocks.

**Toggling LAB_MODE from the UI.** When the lab is bound to loopback
(the default, `BIND_ADDR=127.0.0.1`), the Network Policy modal shows a
toggle button. Click it, read the confirmation copy, wait 3 seconds for
the confirm button to enable, then click **Confirm**. The portal rewrites
`.env` atomically and updates the running process immediately — no
restart needed. Each toggle appends one line to
`lab/data/airgap_audit.jsonl` (chmod 0600). The toggle is disabled when
the portal is bound to a non-loopback address (`BIND_ADDR=0.0.0.0`, for
example) — in that case the modal shows a static note to edit `.env`
directly, because a UI toggle on a LAN-exposed portal would be a
CSRF attack surface.

### 🔬 Autoresearch *(every tier)*

**This is where you set the goal.** Tell the lab a measurable goal —
*"make our chat responses land under 400 ms on my MacBook"* — and the
Researcher designs small experiments it can actually measure on your
machine, runs them, and writes up what it found in the knowledge base.
Every result is labeled by provenance (`measured` / `cannot_run` /
`unmeasured`), so an experiment it couldn't run says so instead of
inventing a number. The goal is lab-wide: once set here, the Dashboard
mirrors it live.

The Researcher writes only into the knowledge base — it never edits your
source tree and makes no commits. The separate git-branch-per-experiment
loop lives on the **Tuning** page (`maximus`); see
[docs/tuning-loop.md](docs/tuning-loop.md).

### 📚 Knowledge Base *(every tier)*

The lab's long-term memory. Agents ingest papers, notes, web pages, and
your uploaded PDFs into a LanceDB vector index. Searchable from Chat and
Autoresearch — the answers you get start referring back to your own
materials. Ingest with `./arailctl pkb ingest <file>`.

### 🤖 Agents *(every tier)*

Three built-in personalities:

- **Buddy** — your lab partner. Observes, nudges, writes warm two-line summaries.
- **SRE** — the crash watcher. Surfaces recurring errors so you don't miss a
  broken loop.
- **Researcher** — the engine behind Autoresearch. Reads goals, designs
  small experiments, measures them on your machine, and reports what it
  found to the knowledge base. It writes no code and makes no commits —
  that's the Tuning loop.

Use the Agent Forge, or add `lab/pkb/agents/<id>/AGENT.md` plus
`lab/pkb/agents/<id>/<id>.py`; the loader discovers them on start.

### 🛠 Admin *(max)*

System health, plugin manager, tier status, diagnostics. It's where the lab
admits what's broken.

### 📖 Docs *(every tier)*

Curated operator docs rendered inside the lab. Use it for setup notes,
agent architecture, design specs, and the platform-porting manifest without
leaving the local UI.

---

## What "local-first" means

By default `LAB_MODE=airgapped` — agents in the lab cannot collect
information from the public internet. Calls to loopback and your private
network still work, so a LAN GPU box (Ollama, vLLM, a QueueLLM node)
keeps inferring without changes. Cloud-provider APIs are blocked at the
HTTP layer. The dashboard's **Airgapped** badge is clickable — it shows
what is and isn't enforced, the recent blocks, and the known gaps
(raw sockets, subprocess `curl`) the Python-level guard
doesn't cover — `requests`, `urllib`, and `httpx` (incl. the cloud
SDKs built on it) are all wrapped. The threat model is well-meaning agent code, not an
adversary on this host — for that, run a host firewall.

Flip to `hybrid` and cloud vendors become fallbacks when the local model
isn't enough. The Chat tab makes this explicit: you see which provider is
active, and you can pivot back with a click.

---

## Make it yours

Edit `.env`:

```bash
LAB_NAME="The AI Research Lab"
LAB_TAGLINE="Our family AI bench"
```

Restart, and every banner, nav logo, activity line, and wiki landing page
now says "Sam's AI Lab". The Python package stays named `arail` internally
so imports don't break — only the display rebrands.

---

## Where to read next

- [docs/cli.md](docs/cli.md) — the full `arailctl` reference: every verb,
  flag, exit code, and tty/non-tty behavior.
- [docs/INSTALL.md](docs/INSTALL.md) — tier choice, platform setup, upgrades.
- [docs/PUBLISH.md](docs/PUBLISH.md) — publishing your lab to the public internet (nginx/Caddy, Cloudflare Access, hardening checklist).
- [docs/MACOS.md](docs/MACOS.md) — Apple Silicon specifics (MLX).
- [docs/LINUX.md](docs/LINUX.md) — Linux + CUDA.
- [docs/WSL.md](docs/WSL.md) — Windows via WSL2 with GPU passthrough.
- [docs/PRIVACY.md](docs/PRIVACY.md) — exactly what data leaves the box.
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — first-run gotchas.
- [docs/world-forge.md](docs/world-forge.md) — *(design)* dream a World with the local model, then let QueueLLM forever curate it into sourced truth — speculative authoring.
- [docs/agents-explained.md](docs/agents-explained.md) — the quick tour of Buddy, SRE, Researcher, and custom agents.
- [docs/agents.md](docs/agents.md) — the agent architecture and loader contract.
- [AGENTS.md](AGENTS.md) — the platform-porting manifest for coding agents.
- [BLUEPRINTS.md](BLUEPRINTS.md) — how this repo thinks of itself as a blueprint.
- [CONTRIBUTING.md](CONTRIBUTING.md) — contribute back upstream.
- [SECURITY.md](SECURITY.md) — threat model, how to report issues.

---

## Philosophy

ARAIL exists because AI research shouldn't feel like a closed club. If you
have a computer and curiosity, you can run your own experiments, curate
your own knowledge, and talk to real frontier models — on terms you
understand, with code you can read.

Every tab teaches something. Every loop you run leaves notes behind. That's
the lab.

— start with `./arailctl setup`.

## License

MIT. See [LICENSE](LICENSE).
