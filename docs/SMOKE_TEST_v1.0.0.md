---
title: ARAIL v1.0.0 — Clean-Machine Smoke Test
section: docs
audience: operator
category: Releases
created: 2026-05-17
---

# ARAIL v1.0.0 — Clean-Machine Smoke Test

A 20-minute walkthrough to verify the v1.0.0 blueprint installs and
boots cleanly on a fresh machine. Run this once before publicising the
release, then again whenever `arailctl` or `setup.sh` changes.

The point is to catch the things that don't break on a developer's
already-warm machine: missing system packages, gated model downloads,
PATH assumptions, idempotency violations.

## What "clean machine" means

Pick whichever you have access to:

- **Fresh VM** (Multipass, UTM, Parallels, OrbStack VM, GCE
  `e2-medium`) — most realistic.
- **Fresh user account** on your existing machine — `sudo dscl . -create
  /Users/araitest` on macOS, `sudo useradd -m arailtest` on Linux.
- **Container** — `docker run -it --rm ubuntu:24.04` for the Linux/CPU
  path. Skip MLX/CUDA paths in containers; they need GPU passthrough.

Whichever you pick, the box must NOT already have ARAIL deps installed
(`mlx`, `airllm`, `aerollm_api`, `ollama`, the ARAIL venv, etc.). If
you reuse a box you've tested on before, blow away `~/.cache/pip`,
`~/.ollama/models/`, and any `arail/` checkout first.

## Matrix

Cover at least one row from each block before shipping. Skip rows you
can't reach (no CUDA box, no Windows host) — note the gap in the
results table at the bottom.

### Tier × accelerator

| Tier        | Accelerator  | Why test it                                                     |
|-------------|--------------|------------------------------------------------------------------|
| minimalist  | MLX          | Default path on Apple Silicon — most users will see this        |
| minimalist  | CPU          | Intel Mac / Linux without GPU / locked-down corporate laptop    |
| minimalist  | CUDA         | Linux desktop with NVIDIA — verify Ollama-on-Linux path         |
| maximus     | MLX          | QueueLLM install on Apple Silicon — the deep-mode happy path     |
| maximus     | CUDA         | QueueLLM CUDA absent → AirLLM fallback notice fires              |

### Compat shim

| Scenario                                           | Why test it                                              |
|-----------------------------------------------------|----------------------------------------------------------|
| Pre-1.0 `.env` with `LAB_TIER=min`                  | v1.1.0 deprecation shim still migrates silently          |
| Pre-1.0 `.env` with `LAB_TIER=max`                  | Same, max → maximus                                      |
| Pre-1.0 install with `models/ai-engineer/` weights | Setup creates new `ai-eng`, leaves old `ai-engineer` alone |

## The walkthrough

For each row in the matrix:

### 1. Clone v1.0.0 exactly

```bash
git clone --branch v1.0.0 https://github.com/cdarnell/arail.git
cd arail
```

**Expect:** clean clone, `git status` shows nothing modified, `cat
src/arail/__init__.py` shows `__version__ = "1.0.0"`.

**Watch for:** any "file too large" warning from git LFS — there's a
42 MB PDF in history that we've decided to leave; the clone should
still succeed.

### 2. Run setup non-interactively

For automation, run with no prompts. For a human-eye check, run
interactively so you see every prompt:

```bash
# Interactive (recommended for first run — read every prompt)
./arailctl setup

# Non-interactive (for CI / automation)
ARAIL_NONINTERACTIVE=1 ARAIL_TIER=minimalist ./arailctl setup
```

**Expect:** each of the 11 numbered banners (`━━━ 1/11 …` through
`━━━ 11/11 …`) prints in order. Final line is `setup: OK` (or
similar) and prints the next-step hint.

**Watch for:**

- **Step 1** classifies the hardware correctly. Apple Silicon shows
  `ACCEL=mlx`; check `uname -m` returns `arm64`.
- **Step 2** Ollama is **skipped on Apple Silicon by default** — the
  log line should say so explicitly. On Linux/CUDA, Ollama installs
  via Homebrew/apt and the version prints.
- **Step 3** `pip install -e ".[dev,minimalist]"` succeeds without
  pulling AirLLM. The Minimalist extras list is empty — that's
  intentional, not a bug.
- **Step 8** Probes the self-hosted GGUF via HuggingFace (`hf.co/qukaizen/ai-eng-1.5b-gguf`) first, then the GitHub Release mirror. **Expected behaviour today:** both probes fail (GGUF not yet uploaded), the script logs the fallback message, pulls `qwen2.5:1.5b`, and creates `ai-eng` from `Modelfile.preview`. Verify with `ollama list | grep ai-eng`.

**Common failures to grep for:**

```text
ERROR: Could not find a version that satisfies …    # network/proxy issue
brew: command not found                              # macOS without Homebrew
nvidia-smi: command not found                        # Linux box, no NVIDIA driver
Permission denied: setup.log                         # cloned to a path the user can't write
```

### 3. Start the lab

```bash
./arailctl start
```

**Expect:** portal starts in under ~10 s, prints
`Lab is at http://127.0.0.1:8080`. Open that URL in a browser.

**On first paint:**

- The warmup overlay shows for up to 10 s, then dismisses itself.
- The `/welcome` page asks for a passphrase if one wasn't generated
  by setup. Pick something.
- The Dashboard loads with the lab name in the header, the mode badge
  shows **● Airgapped** (green dot).

**Tier-gated nav check** (this is the rename validation):

- **Minimalist tier nav** must show: Dashboard · Chat · Autoresearch ·
  Knowledge · Docs · Agents. **Must NOT show**: Admin, Workbench.
- **Maximus tier nav** (after `./arailctl upgrade maximus`) must add:
  Admin, Workbench.

### 4. Chat with ai-eng

- Click **Chat**.
- Confirm the model picker shows `ai-eng:latest` as the default
  selected entry.
- Type a one-line prompt (e.g. "Explain what ARAIL is in one
  sentence"). Verify a response streams.
- Click **⚙ Manage providers**. With `LAB_MODE=airgapped`, every cloud
  provider row should show a banner explaining the airgap; the **Save
  token** button should refuse (and surface a clean error, not a stack
  trace).
- Edit `.env` → `LAB_MODE=hybrid`, restart, retry. The provider rows
  should now accept tokens.

### 5. Run an autoresearch goal

- Click **Autoresearch**.
- Set a one-line goal ("Compare token throughput of 4-bit MLX vs
  GGUF").
- Click **Start**. The activity stream should show the Researcher
  agent picking up the goal within ~30 s.

### 6. Upgrade to Maximus

```bash
./arailctl upgrade maximus
./arailctl restart
```

**Expect:**

- pip installs `jupyterlab`, `anthropic`, `langchain`, `langgraph`,
  `pip-audit`.
- On Apple Silicon: QueueLLM probe runs; either finds `aerollm_api`
  importable or prints the sibling-repo build hint. Setup completes
  either way.
- On CUDA: QueueLLM CUDA absent → fallback notice fires, AirLLM stays
  uninstalled unless `ARAIL_INSTALL_AIRLLM=1`.
- Portal restart shows the additional nav items (Admin, Workbench).

### 7. Verify idempotency

Re-run `./arailctl setup`. **Expect:**

- Hardware detection re-prints, but no re-install of system packages.
- pip extras run but report "already installed".
- `ollama show ai-eng` succeeds → setup logs `ai-eng model already
  present in Ollama — skipping.`
- No prompts (the existing `.env` values are respected).

### 8. Verify the compat shim

```bash
# Simulate a pre-1.0 .env
sed -i.bak 's/^LAB_TIER=.*/LAB_TIER=min/' .env
./arailctl restart
```

**Expect:** `/api/system/health` returns Minimalist surfaces. The
portal log shows exactly one `LAB_TIER='min' is deprecated — use
'minimalist' instead` warning per process.

Repeat with `LAB_TIER=max`.

## Results table

Fill this in as you go; paste it into the v1.0.0 GitHub Discussion
when done.

| Row                                    | Tester    | Date       | Pass / Fail | Notes |
|----------------------------------------|-----------|------------|-------------|-------|
| Minimalist × MLX (Apple Silicon)       |           |            |             |       |
| Minimalist × CPU (Intel Mac or Linux)  |           |            |             |       |
| Minimalist × CUDA (Linux + NVIDIA)     |           |            |             |       |
| Maximus × MLX                          |           |            |             |       |
| Maximus × CUDA                         |           |            |             |       |
| Compat shim (`LAB_TIER=min` migration) |           |            |             |       |
| Compat shim (`LAB_TIER=max` migration) |           |            |             |       |
| Idempotent re-run                      |           |            |             |       |

## Known acceptable failures

These are expected at v1.0.0; they're tracked but not blockers:

- Self-hosted ai-eng GGUF not yet reachable — until QuKaiZen uploads
  the 1.5B GGUF to HuggingFace (`hf.co/qukaizen/ai-eng-1.5b-gguf`) or
  the GitHub Release mirror, setup falls back to `qwen2.5:1.5b` and
  logs why. Run `scripts/check_ai_eng_artifact.sh` to test artifact
  availability.
- QueueLLM CUDA absent on Linux Maximus — until QueueLLM ships the CUDA
  backend; AirLLM (opt-in) is the documented fallback.
- A handful of pre-existing test failures in `pytest tests/`
  (opencode lifecycle kwarg, dashboard layout v2, swarm goal
  surfaces). See the v1.0.0 release notes; not user-visible.

## What to do if something fails

1. Capture `./arailctl logs all 200` and `cat setup.log | tail -100`.
2. Run `./arailctl doctor` and note any warnings.
3. File a [bug report](https://github.com/cdarnell/arail/issues/new?template=bug.yml)
   with the row from the matrix that failed, the captured logs, and
   the exact platform string from Step 1.
