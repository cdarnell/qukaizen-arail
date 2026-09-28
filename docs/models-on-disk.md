# Where your models (and experiments) land on disk

> Short version: **chat models live in Ollama's own store** (outside this repo),
> **downloaded weights live in `lab/models/`**, and **anything you build** lands
> in `build/`, `$ARAIL_MODELS_DIR/forge/` (Model Forge shards), or
> `models/graduated/` depending on which path you used. This page is the map.
> Nothing here is guessed — each location is the real path the code writes to.

ARAIL has several different notions of "a model," and they don't all live in the
same place. That's the single most confusing thing about model building here, so
start with the table, then read the path that matches what you did.

## The map

| What | Where it lands | Set by |
|---|---|---|
| **Chat personas** (`llama-ai-eng`, `ai-engineer`) — the default lab models | **Ollama's own store**, `~/.ollama/models` (outside this repo) | `ollama create` in `scripts/setup.sh` |
| **Downloaded weights** (GGUF / safetensors for AirLLM / AeroLLM streaming) | `lab/models/` | `ARAIL_MODELS_DIR` (`src/arail/config.py`) — git-ignored |
| **`build_ai_eng.sh` output** (the real local distillation pipeline) | `build/` at the repo root (e.g. `build/ai-eng-1.5b-v2.1.Q4_K_M.gguf`) | `ARAIL_BUILD_DIR` (default `./build`) — git-ignored |
| **Graduated LoRA adapters** (from a Nucleus run) | `models/graduated/<id>/` | committed via git-lfs |
| **Model Forge shards** (`arailctl nucleus build` output — DNA card, seal, build report, fused weights) | `$ARAIL_MODELS_DIR/forge/<shard>/<version>/` | `ARAIL_MODELS_DIR` (`src/arail/config.py`); private intermediates (cert set, teacher logits, keys) live in `lab/data/nucleus/`, 0700 |
| **Experiment records** (Autoresearch measured runs) | `lab/data/experiments/<id>.json` (raw) + `lab/pkb/agents/experiments/*.md` (KB, until you promote them) | the Researcher agent |

> **"Wipe the PKB = forget me"** covers `lab/pkb/` (knowledge + chat memory).
> It does **not** wipe `lab/models/`, `build/`, or `lab/data/experiments/` —
> those are model/build artifacts, not knowledge. Delete them by hand if you
> want the disk back.

## The four ways to "build a model" (and where each one puts things)

There are four different surfaces that all sound like "build a model." They are
not the same, and only one runs automatically at setup:

1. **The default persona (`llama-ai-eng`).** This is a *system-prompt wrap* over
   Meta's `llama3.2:1b` — `ollama pull llama3.2:1b` then `ollama create
   llama-ai-eng -f models/ai-eng/Modelfile.default`. It's two commands, it runs
   at setup, and it lands in **Ollama's store**. This is not weight training —
   it's giving a stock model a persona. To make your own, edit a `Modelfile` in
   `models/ai-eng/` and run `ollama create <name> -f <modelfile>`.

2. **`scripts/build_ai_eng.sh`** — the one genuine, runnable **distillation**
   pipeline today. It downloads a LoRA adapter, fuses it, benchmarks, converts
   to GGUF, and registers with Ollama. Output lands in **`build/`**. It's
   CLI-only (not in the portal), needs a `.venv` with `peft`/`mlx_lm`, llama.cpp,
   ~16 GB RAM and ~30 GB disk, and some lanes point at placeholder HF repos
   today — read the script's header before running:
   `./scripts/build_ai_eng.sh --help`.

3. **Model Forge (`arailctl nucleus <verb>`, viewed at `/forge`).** This
   replaced the old `/build` tab and its separate-program dependency
   (2026-09-23, sprints/2026-09-23-nucleus-sprint-1). It's an in-repo,
   local-first distillation pipeline: `nucleus plan` writes a
   `configs/domains/<slug>.yaml`, `nucleus stage` snapshots a local corpus,
   `nucleus build` runs preflight -> extract -> train -> eval, and
   `nucleus certify` writes a signed DNA card, a seal, a build report, and
   (locally by default) a ledger row. `/forge` is a **read-only viewer** —
   it lists and renders cards; it does not trigger builds. There is no
   sibling-repo dependency and no separate program to install.

4. **The `/tuning` tab** — this is **not** model building at all. It tunes the
   *inference throughput* of a very large (≥1 TB) model by sweeping QueueLLM
   runtime knobs and committing the winners to `config/tuning.yml`. No weights
   are trained.

## Shared checkpoints (machine-level convention, not an ARAIL default)

On machines shared across QuKaiZen products, large MLX checkpoints live in one
world-readable location instead of per-account copies:

| | |
|---|---|
| Canonical path | `/Users/Shared/models/` |
| Back-compat | `~/models` is a symlink to it, so existing `~/models/...` paths keep working |
| Permissions | world-readable (`chmod -R a+rX`) — any macOS account can read them |
| Why | these are openly-licensed weights (Qwen, Llama, gpt-oss …), and aeroLLM's GA gate #6 (cross-user bit-identical replay, ADR 0007/0013) needs a second macOS account reading the *same* checkpoints rather than duplicating 400+ GB |

**This is not ARAIL's default, and shouldn't become one.** ARAIL is a blueprint
other people clone onto their own machines, so its default stays repo-relative
(`ARAIL_MODELS_DIR` → `lab/models`). To use the shared checkpoints on a machine
that has them, point the env var at the canonical path:

```bash
# .env
ARAIL_MODELS_DIR=/Users/Shared/models
```

`AEROLLM_MODEL` then resolves against it — e.g. `AEROLLM_MODEL=Qwen2.5-7B-Instruct-4bit`
finds `/Users/Shared/models/Qwen2.5-7B-Instruct-4bit`.

**When downloading new checkpoints on such a machine**, prefer
`/Users/Shared/models/` (or via the `~/models` symlink) over a private
home-directory path, so the convention stays consistent across products sharing
the box. Nothing in ARAIL hardcodes a home-directory model path, so the move
required no code changes.

## Frequently asked

- **"I ran setup — what model do I have?"** `llama-ai-eng` in Ollama (the
  minimalist default). Check with `ollama list` or `./arailctl doctor`.
- **"Where's the `/build` tab?"** Retired 2026-09-23; replaced by Model
  Forge (`arailctl nucleus <verb>`, viewed read-only at `/forge`). See
  surface 3 above.
- **"Where did my Autoresearch experiment results go?"** `lab/data/experiments/`
  (raw JSON) and `lab/pkb/agents/experiments/` (markdown you can promote into the
  Knowledge Base). See `docs/agents-explained.md`.

The forward plan for a one-click local distillation (bake → seal → compact) is
tracked in `sprints/2026-07-22-distill-now/`; it is not shipped yet.
