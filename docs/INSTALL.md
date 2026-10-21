---
title: Install Guide
description: "A friendly walkthrough for getting the lab running on your machine — no prior experience needed."
category: Getting Started
order: 1
tags:
  - install
  - setup
  - getting-started
audience: beginner
related:
  - MACOS
  - LINUX
  - WSL
  - TROUBLESHOOTING
---
# Install Guide — Autoresearch AI Lab

> **See also:** [The lab, end-to-end](the-lab.md) — the 12-minute runbook
> tour of every surface. Read it after you finish setup (or first, if
> you want to know what you're installing).

A friendly walkthrough. If you've never run a Python app from a terminal
before, this is for you.

If you're adapting Arail for someone else's machine or translating the same lab
between machines, pair this guide with [vibe-integrate.md](vibe-integrate.md).
That companion explains how to keep the setup path the same while tuning the
first-run choices to the target hardware.

---

## 0. What you need

- **macOS**, **Linux**, or **Windows + WSL2**. Native Windows shells are not
  supported by `./arailctl setup`.
- ~8 GB free disk for the `minimalist` tier, ~60 GB for the `maximus` tier.
- Git, a terminal, and a browser.

`./arailctl setup` bootstraps everything else for you. If Homebrew, Python
3.11, or Node.js are missing, it'll prompt once and then install them via
your platform's package manager. Pass `ARAIL_NONINTERACTIVE=1` (the
agent-driven flag) to skip every prompt, or `ARAIL_AUTO_INSTALL=0` to
fall back to the old "tell me what to install, I'll do it myself"
behavior.

---

## 1. Clone and enter the repo

```bash
git clone https://github.com/qukaizen/arail.git
cd arail
```

---

## 2. Decide on a tier

Two tiers. You can change your mind later with `./arailctl tier maximus`
(`./arailctl upgrade maximus` still works — `upgrade` is a permanent
alias for `tier`).

Legacy `min`/`max` tier names are accepted with a deprecation warning
(compat shim removed in v1.1.0).

### 🟢 `minimalist` — the everyday lab

- **Tabs**: Dashboard, Chat, Autoresearch, Knowledge Base, Agents, Docs.
- **Packages**: core lab + embedded **LanceDB** recall. No heavyweight
  deep backend installed by default.
- **Default model**: `llama-ai-eng` — an AI engineering assistant built
  with Llama-3.2-1B-Instruct (~0.9 GB, runs on 16 GB), wrapped with the
  AI-engineer persona and served via Ollama. This is the only model that
  auto-installs. The chat catalog lists ~20 other models you can browse
  and pull on demand. (Built with Llama — see [NOTICE](../NOTICE).)
- **Memory**: KB ships with embedded semantic recall out of the box.
  You get the same knowledge surface in `minimalist`; `maximus` is
  about heavier operator tools, not a different memory backend.
- **External providers**: Claude / NVIDIA / OpenRouter / HuggingFace are
  still reachable in `minimalist` — they go over plain HTTP. The only
  thing `maximus` adds here is the official SDKs for heavier
  orchestration.
- **Good for**: a first taste; an older laptop; the blueprint you hand
  to a friend who wants to learn.

### 🔴 `maximus` — the full bench

- **Tabs**: everything in `minimalist` + Admin + Docs + Notebooks +
  Tuning + Plugins.
- **Adds**:
  - `jupyterlab` — browser notebooks.
  - `anthropic` SDK — first-class Claude integration.
  - `langchain` + `langgraph` — for operators who want to compose agents
    with the community ecosystem on top of the built-ins.
  - **QueueLLM** — Arail's own Rust streaming runtime, the deep-mode
    backend. Apple Silicon: native. CUDA hosts: fall back to AirLLM with
    a notice until QueueLLM CUDA ships (set `ARAIL_FORCE_AEROLLM=1` to
    disable fallback).
  - Hardware-specific extras (MLX, CUDA, or CPU) install automatically
    based on what `./arailctl setup` detects.
- **Good for**: real experiments, frontier models when you pull them
  yourself from the catalog, the full kitchen sink.

> **Heads up — AirLLM is opt-in.** v1.0.0 removed AirLLM from the
> default install path. Power users on CUDA/Linux who want
> layer-streaming 405B inference can enable it with
> `ARAIL_INSTALL_AIRLLM=1 ./arailctl setup`. Llama weights are gated on
> Hugging Face and require `huggingface-cli login` (or `HF_TOKEN`).

---

## 3. Run `./arailctl setup`

```bash
./arailctl setup
```

The script prints a numbered banner before each phase so you always
know where you are in the flow. Every phase is **idempotent** — re-run
setup any time without fear; already-done work is skipped.

### Step 1/11 — Detecting hardware

Reads `uname` to classify your machine:

- **Apple Silicon** (`Darwin` + `arm64`) → `PLATFORM=macos`, `ACCEL=mlx`
- **Intel Mac** → `PLATFORM=macos`, `ACCEL=cpu` (with a warning — MLX
  requires Apple Silicon)
- **Linux + Nvidia + `nvidia-smi` works** → `PLATFORM=linux`,
  `ACCEL=cuda`
- **Linux without GPU** → `PLATFORM=linux`, `ACCEL=cpu`
- **WSL2** (Microsoft kernel signature) → `PLATFORM=wsl`, then same
  GPU detection as Linux
- **Distro flavor** (`gentoo`/`fedora`/`arch`) detected from `/etc/*-release`

Hard-fails on **Windows-native shells** (PowerShell, MSYS, Git Bash) —
ARAIL requires WSL2 on Windows hosts.

### Step 2/11 — System packages

Installs supporting CLIs via your platform package manager:

- **`ttyd`** — powers the embedded `/terminal` tab in the portal.
  Without it, the Terminal tab shows install help instead of a broken
  iframe.
- **`tmux`** — keeps the browser-terminal scrollback alive across
  iframe reloads.
- **`agent-browser`** — Node.js CLI used by the DaC tab's web
  research agent. Bootstraps Node.js via the platform package manager
  if `npm` is missing.
- **Ollama** — installed on Linux/CUDA and Intel Macs (Apple Silicon
  uses native MLX, so Ollama is skipped unless `ARAIL_ENABLE_OLLAMA=1`
  is set). Used to serve the `ai-eng` default model.

Each install is opportunistic — failures log a hint and continue, they
don't kill setup.

### Step 3/11 — Python environment

Creates `./.venv/` and installs:

- Base ARAIL package — `pip install -e .` pulls FastAPI, uvicorn,
  Jinja2, LanceDB, huggingface-hub, and the other always-on deps from
  `pyproject.toml`.
- **Tier extras** — `pip install -e ".[dev,<tier>]"`. Minimalist adds
  nothing extra (the base deps cover the everyday lab). Maximus adds
  `jupyterlab`, `anthropic`, `langchain`, `langgraph`, `pip-audit`.
- **Accelerator extras** — based on Step 1 detection:
  - `mlx` → `mlx`, `mlx-lm`
  - `cuda` → `vllm`, `torch`
  - `cpu` → `llama-cpp-python`
- **QueueLLM** — installed only when `LAB_TIER=maximus` on Apple
  Silicon. On CUDA Maximus, QueueLLM CUDA is pending; AirLLM serves as
  the fallback (opt-in via `ARAIL_INSTALL_AIRLLM=1`).
- **AirLLM** — opt-in only as of v1.0.0. Set `ARAIL_INSTALL_AIRLLM=1`
  to enable layer-streaming 70B/405B inference on CUDA/Linux.

### Step 4/11 — Name your lab

Asks for a `LAB_NAME` and one-line tagline. These thread through every
visible surface (dashboard title, nav logo, activity log, wiki landing
page). Defaults: `"Autoresearch AI Lab"` and `"A learn-by-doing AI
research lab"`. Examples of what folks have picked:

```text
Sam's AI Lab
gentoofoo's ai lab
PeanutLab
```

The Python package name stays `arail` so imports never break when you
rebrand.

### Step 4b/11 — Pick an install tier

Shows the two-tier menu and reads your choice:

- **`minimalist`** (default) — Dashboard, Chat, Autoresearch, Knowledge
  Base, Agents, Docs. The everyday lab. Ships `ai-eng` as the only
  default model.
- **`maximus`** — everything in Minimalist + Admin, Notebooks, QueueLLM
  deep-mode runtime, Anthropic SDK, LangChain/LangGraph, full cloud
  catalog. Targets 32 GB+ machines.

Legacy `min`/`max` are accepted with a deprecation warning (shim
removed in v1.1.0).

### Step 5/11 — Lab passphrase

One secret covers every authenticated surface:

- The browser IDE (`code-server`) login
- Open Notebook data encryption key
- Future auth proxy

Setup generates a 32-char random passphrase by default; you can also
type your own. The value writes to `.env`, `lab.conf`, and
`~/.config/code-server/config.yaml` with `chmod 0600`.

### Step 6/11 — Configuration files

Writes (or upserts into) two files:

- **`.env`** — `LAB_NAME`, `LAB_TIER`, `LAB_MODE=airgapped`, model
  defaults, ports, hardware flags. Hand-edit only if you know what
  you're doing.
- **`lab.conf`** — runtime ports + service flags consumed by
  `./arailctl start`.

Existing values are preserved — re-running setup never silently
clobbers your customisations.

### Step 7/11 — Knowledge base scaffold

Creates the `lab/pkb/` tree the lab agents read from and write to:

```text
lab/pkb/
├── inbox/        # drop docs here; the curator agent ingests them
├── sources/      # original PDFs / HTML / video transcripts
├── notes/        # human + agent-written notes
├── agents/       # per-agent AGENT.md + <id>.py loader contracts
└── compiled/     # auto-built wiki + LanceDB vector index
```

Plus `lab/data/` (activity log, goals, experiments, audit trail) and
`lab/models/` (downloaded weights — git-ignored).

### Step 8/11 — AI models (llama-ai-eng)

The **only** model that auto-installs. Two-step persona-wrap install
(works on a clean machine today, no uploaded artifact required):

1. `ollama pull llama3.2:1b` — fetches Meta Llama-3.2-1B-Instruct Q4_K_M
   (~0.9 GB). License: Llama 3.2 Community License.
2. `ollama create llama-ai-eng -f models/ai-eng/Modelfile.default` — wraps
   it with the AI-engineer SYSTEM prompt ("Built with Llama").

The installed model is named `llama-ai-eng` (begins with "Llama" as
required by the Llama 3.2 Community License). See `NOTICE` and
`licenses/` for the full attribution.

**Dormant self-hosted lane** (`ARAIL_AI_ENG_SELFHOSTED=1`): the old HF
mirror ladder (HF primary → GitHub mirror sha256-verified → CDN → preview
net) is still available for the future Nucleus-distill lane. Off by default.
The `Modelfile.preview` fallback is kept for this lane.

**Maximus deep persona** (`ai-engineer`, Qwen2.5-7B, 4.7 GB, Apache-2.0):
offered on maximus setup with the exact install command; auto-runs only with
`ARAIL_INSTALL_DEEP_PERSONA=1`.

No other models pre-install — the chat catalog (~20 entries) is a
browse-and-pull gallery accessed from the Chat tab.

Skip the model pull with `ARAIL_SKIP_OLLAMA=1` if bandwidth is tight.

### Step 8b/11 — Coder starter model (optional)

Only runs when you pass `--with-coder` (or set `ARAIL_WITH_CODER=1`).
Downloads `Qwen2.5-Coder-3B-Instruct` (~2 GB Q4) to `lab/models/` so
the Maximus tier's opencode Workbench has something to point at.

### Step 9/11 — Lab intent & first research goal

Asks two questions:

- **`LAB_INTENT`** — the domain context the researcher agent thinks
  in: `ai` | `farming` | `ml` | `business` | `education` | `health` |
  `culinary`. Defaults to `ai`.
- **Initial research goal** — one sentence describing what you want
  the autoresearch loop to chew on first. The goal seed lands in
  `lab/data/goals.json`; you can change it any time from the
  Autoresearch tab.

### Step 10/11 — Install `arailctl` to your PATH

Offers to symlink `arailctl` into `~/.local/bin` so you can run it
from any directory. After this step:

```bash
arailctl status      # works from anywhere
qkz status           # short alias (symlinked to arailctl)
```

Skipped if `arailctl` is already on `PATH`. The `qkz` PATH install is
suppressed automatically if you already have a `qkz` binary or shell
function (e.g. from the QuKaiZen knowledge-base repo); override with
`ARAIL_INSTALL_QKZ=1`.

### Step 11/11 — Verification

Runs a smoke test:

- Imports `arail` and `arail.portal.app` in the venv.
- Resolves `_pkb_root()` to confirm the KB scaffold is reachable.
- Checks `uvicorn`, `jupyter`, `ttyd`, `code-server` are on `PATH`
  (warns but doesn't fail on optional ones).

Prints a final `setup: OK` and a one-liner showing the next command:
`./arailctl start`.

---

## 4. Start the lab

```bash
./arailctl start
```

Open [http://127.0.0.1:8080](http://127.0.0.1:8080). You're in.

---

## 5. Upgrade later

When you're ready for more:

```bash
./arailctl tier maximus        # installs QueueLLM + adds notebook/cloud orchestration extras
./arailctl restart             # pick up the new nav
```

(`./arailctl upgrade maximus` is the same thing — `upgrade` is a
permanent alias for `tier`.) `./arailctl tier minimalist` is a downgrade
— it doesn't uninstall packages, it just hides the extra tabs. Hit
`tier maximus` any time to get them back. Legacy `min`/`max` tier names
still work via a one-release compat shim (removed in v1.1.0).

To refresh an already-set-up lab to the latest source, dependencies,
components, and (optionally) models in one step:

```bash
./arailctl install             # source -> deps -> components -> models -> verify
./arailctl install --check     # detect-only: what WOULD change, nothing mutated
./arailctl install --models    # also apply the detected model drift fix
```

(`./arailctl update` is the same thing — `update` is a permanent alias
for `install`.) See [docs/cli.md](cli.md#install) for the full phase
breakdown and exit-code contract.

---

## 6. Connect an outside model (Chat → Manage providers)

The Chat tab is where every operator — `minimalist` or `maximus` —
configures outside model vendors.

1. First: in `.env`, set `LAB_MODE=hybrid` and restart. This is the airgapped
   guard — by default the lab refuses every cloud provider operation
   (save, test, switch) until you explicitly opt in.
2. Open Chat. Click **⚙ Manage providers**.
3. For each vendor you want to use — Claude, NVIDIA NIM, OpenRouter,
   HuggingFace, or a custom OpenAI-compatible endpoint — paste the API key
   and click **Save**. The key writes to `lab/data/secrets.env` with
   `chmod 0600` (readable only by you, git-ignored).
4. Click **Test** to verify — the lab pings the vendor's `/models` endpoint
   with the saved token and reports OK or the error.
5. Click **List models** to see the vendor's current model catalogue. Handy
   for typing the exact model ID into your Autoresearch goal.
6. **Remove** deletes the saved token; the vendor goes back to "no token".
7. Close the modal. On the Chat tab, pick the provider from the
   **Compute Source** row and send a message.

Tokens are never echoed back to the UI after saving and never logged. If
you prefer managing keys in `.env` directly, the relevant vars are
`ANTHROPIC_API_KEY`, `NVIDIA_API_KEY`, `OPENROUTER_API_KEY`, `HF_TOKEN`,
and `MODEL_API_BASE` — `secrets.env` just keeps them out of `.env` so
they don't mix with non-sensitive config.

---

## 7. Rename your lab

The whole point of the blueprint. Edit `.env`:

```bash
LAB_NAME="Sam's AI Lab"
LAB_SHORT_NAME="sams-lab"
LAB_TAGLINE="Our family AI bench"
```

`./arailctl restart` and every banner, title, log line, and wiki landing page
now says "Sam's AI Lab".

---

## 8. Troubleshooting

```bash
./arailctl doctor          # end-to-end validation
./arailctl status          # what's running, on which ports
./arailctl logs            # live activity tail
./arailctl reset help      # ways to wipe state if something went very wrong
```

See [docs/TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common first-run gotchas.

---

## 9. The `qkz` alias

If typing `./arailctl` gets old, there's a symlink at `./qkz` that does the same
thing. Or alias it in your shell:

```bash
alias qkz='./arailctl'  # then: qkz setup, qkz start, qkz restart
```

Happy researching.
