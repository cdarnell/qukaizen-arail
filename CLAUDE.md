# CLAUDE.md — ARAIL

> Orientation file for Claude sessions working on this repo. Read this
> first; it's the fastest way to ground yourself in what ARAIL is, how
> it relates to the sibling repos, and what the conventions are. The
> human-facing entry point is `README.md`; the platform-porting manifest
> for external coding agents is `AGENTS.md` (different intent).

## What ARAIL is, in one paragraph

ARAIL — Autoresearch AI Labs — is a local-first AI research lab
blueprint. Clone the repo, run `./arailctl setup && ./arailctl start`, pick a
tier, and you have a dashboard, a chat tab with a Compute Source pivot
(local MLX/CUDA/llama.cpp or any cloud vendor), an autoresearch loop
that runs experiments while you're away, an agent-driven knowledge base
backed by LanceDB, and three built-in agents (Buddy, SRE, Researcher).
Default mode is `airgapped`; flip to `hybrid` and cloud providers
become fallbacks. ARAIL is positioned as a blueprint, not a product:
users are expected to fork, rename, and adapt.

## Where ARAIL sits in this workspace

ARAIL is the umbrella project. Two sibling repos in `~/ProJects` hang
off it:

- **QueueLLM** (`~/ProJects/qukaizen-queuellm`, GitHub
  `cdarnell/qukaizen-queuellm`; formerly AeroLLM, renamed 2026-08-21 — the
  old local path `qukaizen-aerollm` survives as a compat symlink) —
  Streaming inference runtime, a product of ARAIL. Origin lives here in
  `research/aerollm/` (00 through 04 design docs) before it was extracted
  to its own repo. As of v1.0.0, QueueLLM is the deep-mode backend for the
  `maximus` tier. CUDA hosts fall back to AirLLM with a notice until its
  CUDA backend ships. The README's Compute Source pivot in the Chat tab
  is the surface where alternative backends slot in. See
  `~/ProJects/qukaizen-queuellm/CLAUDE.md` for runtime internals.
- **qukaizen-nucleus** (`~/ProJects/qukaizen-nucleus`) — Nucleus, the
  Super Skill Distillation Pipeline. Independent today (its own pipeline, its own
  CLI also called `qkz`), but a planned future consumer of QueueLLM for
  teacher inference. The shared `qkz` name is convergent: in this repo
  `./qkz` is symlinked to `./arailctl` as a shorthand alias; in qukaizen
  `qkz` is a Rust CLI binary. They're not the same program.

**Naming: QueueLLM, formerly AeroLLM.** The product and the sibling repo are
QueueLLM. ARAIL's *integration surface* still carries the old name on purpose
and must not be renamed in passing: the `aerollm-api` package / `aerollm_api`
module (and this repo's `aerollm = "aerollm-api>=1.0,<2.0"` pin),
`AEROLLM_*` / `AERO_*` env vars (they live in users' `.env` files), backend
id `"aerollm"`, registry id `tier1-aerollm`, `AeroLLMBackend`, the
`*-aerollm*.sh` scripts, `THIRD-PARTY-LICENSES/aerollm/`, the `aerollm` CLI
alias, and the `research/aerollm/` origin docs. Renaming those is a
coordinated release with deprecation aliases (the legacy `min`/`max` tier
values are the pattern) — its own sprint, not a sweep. See
`~/ProJects/CLAUDE.md` § Naming status for the full picture.

ARAIL is where the user's research **happens** — the lab. QueueLLM is
the inference substrate the lab will run on. qukaizen is a separate
research effort that also needs that substrate.

## Two tiers, two surfaces, one CLI

The product is intentionally simple at the entry point. Two tiers
(renamed in v1.0.0 — legacy `min`/`max` env values are still accepted
with a deprecation warning for one release):

| Tier         | What's in it                                                                                                                 |
|--------------|------------------------------------------------------------------------------------------------------------------------------|
| `minimalist` | Dashboard · Chat · Autoresearch · Knowledge Base · Agents · LanceDB vectors · **Llama AI Engineer** (`llama-ai-eng`, built with Llama-3.2-1B-Instruct, ~0.9 GB, runs on 16 GB) — the everyday lab |
| `maximus`    | + Admin · Docs · Notebooks · **QueueLLM** deep-mode runtime · Anthropic SDK · LangChain · full cloud SDKs · **AI Engineer (deep, 7B)** (Qwen2.5-7B-Instruct deep persona, Apache-2.0) — the full local bench, cloud frontier one click away |

Tier upgrade is a single `./arailctl upgrade maximus` away; downgrade
likewise. Knowledge Base and Agents are part of `minimalist`
deliberately — research without memory is a non-starter.

**v1.1 default model:** `llama-ai-eng` is the only model that auto-installs
during setup. Setup does `ollama pull llama3.2:1b` then
`ollama create llama-ai-eng -f models/ai-eng/Modelfile.default` — no
uploaded artifact required, works on a clean machine today. The chat catalog
(`src/arail/chat/models_catalog.yaml`) keeps ~20 other models as a
browse-and-pull gallery — nothing else ships pre-installed. The maximus deep
persona (`ai-engineer`, Qwen2.5-7B, Apache-2.0) is offered on maximus setup
but not forced. AirLLM is opt-in via `ARAIL_INSTALL_AIRLLM=1`.

**Llama disclosure exception (required for any session working on the default model):**
The default model (`llama-ai-eng`) is built on Llama-3.2-1B-Instruct under
the **Llama 3.2 Community License**, which REQUIRES disclosure — do NOT
hide this base. The name MUST begin with "Llama" (`llama-ai-eng`), "Built
with Llama" MUST be displayed in README/catalog/persona system prompt, and
NOTICE MUST bundle the license + AUP (`licenses/`). The hide-the-base rule
applies ONLY to the Apache-2.0 deep/qwen lineage (`ai-engineer`, 7B).

**Gemma disclosure exception (Phase B — DORMANT; applies once the Gemma default-floor is armed):**
A ~2B Gemma generalist (`qkz-project-aware-2b`) is staged to become the minimalist
default-floor (see `models/ai-eng/Modelfile.gemma`, the `ARAIL_DEFAULT_GEMMA=1`
gate in `scripts/setup.sh`, dormant until G1). It is **built with Gemma** under the
**Gemma Terms of Use** — when armed, disclosure is REQUIRED: "Built with Gemma" in
README/catalog/persona prompt, NOTICE + `licenses/GEMMA-TERMS-OF-USE.txt` +
`licenses/GEMMA-PROHIBITED-USE-POLICY.txt` bundled, and the verbatim §3.1(4) notice
("Gemma is provided under and subject to the Gemma Terms of Use found at
ai.google.dev/gemma/terms"). UNLIKE Llama, Gemma does **not** require "Gemma" in the
model name (confirmed from the live Terms) — so `qkz-project-aware-2b` needs no rename.

The CLI is `./arailctl` (also reachable via `./qkz`). The main verbs —
full reference in `docs/cli.md` (every flag, exit code, tty/non-tty
behavior):

- `./arailctl setup` — pick a tier, install deps, download a starter model.
- `./arailctl start` — open `http://127.0.0.1:8080` (`--world <slug>` /
  `--root` for Concurrent Worlds; `--warm` to report boot-time model
  warm-up).
- `./arailctl install` — refresh an already-provisioned lab: source, deps,
  components, models, verify (`update` is a permanent alias).
- `./arailctl tier {minimalist|maximus}` — change the feature-set tier
  (`upgrade` is a permanent alias).
- `./arailctl status` — what's running, World instances + root lab; exits
  `0`/`3`/`4` (up / degraded / nothing running).
- `./arailctl pkb ingest <file>` — push a doc into the LanceDB-backed KB.
- `./arailctl benchmark_models` (alias `aerollm`) — local model benchmark.

`scripts/setup.sh` is the platform-porting surface. `AGENTS.md` is the
manifest for external coding agents that want to add a new platform
branch.

## The five surfaces in the portal

`src/arail/portal/` is the FastAPI app that renders the lab (Jinja2 templates on a base.html shell). The five
surfaces a user sees are described in `README.md` § "The main surfaces"
and (more deeply) `docs/agents-explained.md`:

1. **Dashboard** — what the agents are doing, current goal, simulated
   cloud cost, activity stream.
2. **Chat** — Compute Source pivot (My Machine / Claude / NIM /
   OpenRouter / HF / Custom endpoint); ⚙ Manage providers modal for
   key save / test / list / remove. Tokens persist to
   `lab/data/secrets.env` `chmod 0600`, git-ignored, never echoed back.
3. **Autoresearch** — measurable-goal experiment loop; the Researcher
   agent is the engine.
4. **Knowledge Base** — LanceDB vector index over papers, notes,
   uploaded PDFs.
5. **Agents** — Buddy (lab partner), SRE (crash watcher), Researcher
   (autoresearch engine). User-defined agents drop into
   `lab/pkb/agents/<id>/` with `AGENT.md` + `<id>.py`.

`maximus` adds **Admin** (system health, plugin manager, diagnostics),
**Notebooks/Workbench**, **Tuning**, and **Plugins**.
**Docs**, the **Knowledge (`/dac`)** and **Worlds** surfaces, and
**Model Forge (`/forge`)** are every-tier (both minimalist and maximus —
the source of truth is `_TIER_SURFACES` in `src/arail/portal/app.py`).
These maximus-only surfaces are now server-side tier-gated (a minimalist
user who types the URL gets a 404), not just hidden in the nav.

**Model Forge** (`arailctl nucleus <verb>`; read-only viewer at `/forge`)
replaced the old **Model Building** `/build` tab 2026-09-23
(sprints/2026-09-23-nucleus-sprint-1) — a local, in-repo distillation
pipeline (`plan` → `stage` → `build` → `certify`) instead of a thin
client for a separate `qukaizen-nucleus` process. `stage`/`build`/
`eval`/`certify`/`spike` need the maximus deep runtime; `plan`/`verify`/
`status`/`list` and the `/forge` viewer work on minimalist. See
`docs/nucleus.md`.

## Repo layout (orientation)

The top of the tree is dense; the parts that matter:

| Path                          | What's there                                                                       |
|-------------------------------|------------------------------------------------------------------------------------|
| `arailctl` (file)             | Main shell entry point — `./arailctl setup`, `./arailctl start`, etc.                    |
| `arail` → `arailctl` (symlink)| Back-compat alias for the entry point                                              |
| `qkz` → `arailctl` (symlink)  | Shorthand alias (NOT the qukaizen-nucleus Rust `qkz`)                              |
| `scripts/setup.sh`            | Platform-detect → service install → model download. `AGENTS.md` is the porting doc |
| `src/arail/`                  | Python package (portal app, agents, knowledge base, pipelines)                     |
| `src/arail/portal/`           | FastAPI app + templates (dashboard, chat, agents, knowledge, tuning, research)       |
| `lab/`                        | Runtime state — `lab/pkb/` is the agent-facing knowledge base, `lab/data/` is secrets, `lab/models/` is downloaded weights. All git-ignored except contracts |
| `blueprints/`                 | Four reference blueprints: `autoresearch`, `client-followup`, `inbox-triager`, `status-digest` |
| `core/knowledge-canvas/`      | The Knowledge Canvas frontend (TS/React)                                           |
| `compose/open-notebook/`      | Surreal-backed notebook integration (the surrealdb log file in git history is from here) |
| `research/aerollm/`           | **Five-doc design study where QueueLLM started.** Read these to understand QueueLLM's origin: `00-product-vision.md`, `01-pipeline-map.md`, `02-batching-strategy.md`, `03-parallel-work.md`, `04-measurement-log.md` |
| `research/speculative-decoding/` | Spec-decode research (now realised in `queuellm-speculative`)                    |
| `examples/peanut_farmer/`     | A canonical "PeanutLab" example of forking + renaming the lab                      |
| `docs/`                       | INSTALL, MACOS, LINUX, WSL, PRIVACY, TROUBLESHOOTING, agents architecture          |
| `BLUEPRINTS.md`               | How this repo thinks of itself as a blueprint, not a product                       |
| `ROADMAP.md`                  | Forward plan                                                                       |
| `design.md`                   | Design philosophy; complements README                                              |
| `pyproject.toml`              | Python package metadata. Internal package name stays `arail`; only the display rebrands |

## Current state

650+ commits, latest 2026-07-23. Recent work has been on **Worlds / DaC**
(world-forge, world-mount, the `/dac` "Knowledge" surface), **chat memory**
(Tier-1 transcripts; the Tier-2 fact store is designed but not built), **lab
persistence**, and the **2026-07-23 "clean experience" sprint** (quiet boot with
no auto-checks, egress honesty, tier-gate hardening, a real on-device experiment
engine replacing simulated research, and truth-in-UI for the model surfaces —
see `sprints/2026-07-23-clean-experience/`), and the **2026-07-28 concurrent
Worlds sprint** (`./arailctl start --world <slug>` runs a World as its own
isolated process/data-root, side by side with others — see
`docs/concurrent-worlds.md` and `sprints/2026-07-28-concurrent-worlds/`).
The **2026-07-29 elite-cli sprint** turned the rest of `./arailctl` into
the same caliber of surface: an honest, readiness-gated root-lab `start`
(`--root`, `--warm`), a scoped `restart` that can no longer stop a
sibling World, a unified `status` with a documented `arail.status/v2`
schema and a real exit-code contract (`0`/`3`/`4`), and the new `install`
verb (`update` alias) + `tier` verb (`upgrade` alias) consolidating what
used to be three overlapping version-management verbs — see
`docs/cli.md` (the canonical reference) and
`sprints/2026-07-29-elite-cli/`. Cross-repo tooling still auto-generates
qukaizen-style branch names in this repo.

The code is more mature than QueueLLM's (it predates the extraction);
treat the portal and the agent loader as stable surfaces, the
autoresearch loop and the knowledge-base ingest paths as the moving
parts.

## Conventions worth knowing

- **License: MIT.** (Different from QueueLLM's Apache-2.0.)
- **Local-first by default.** `LAB_MODE=airgapped` blocks every cloud
  provider. The Compute Source row in Chat shows a banner; the
  save/test/models endpoints refuse. `LAB_MODE=hybrid` opens the door.
  Don't relax this default.
- **Internal package name stays `arail`.** Display name (LAB_NAME,
  LAB_TAGLINE) is rebrand-able via `.env`. Imports must not break when
  someone calls their lab "PeanutLab".
- **Compute Source pivot is the integration seam.** When QueueLLM lands
  HTTP bindings, it slots in as a new Compute Source option — same UX,
  no UI changes. Don't bake AirLLM into the surface; bake "local
  inference backend" with AirLLM as one implementation.
- **Tokens to `lab/data/secrets.env` `chmod 0600`, git-ignored, never
  echoed back, never logged.** This rule shows up in the README; it
  shows up in the code; treat any drift from it as a bug.
- **Agent loader contract.** New agents go in
  `lab/pkb/agents/<id>/AGENT.md` + `lab/pkb/agents/<id>/<id>.py`. The
  loader discovers them on start. Don't shortcut by editing the
  built-in agents.
- **Conversation memory is PKB-rooted and gated.** Chat transcripts live at
  `lab/pkb/conversations/<id>/transcript.jsonl` — **`.jsonl`, never `.json`**,
  because `_PKB_TEXT_SUFFIXES` (`pkb.py:376`) includes `.json` and would
  vector-index every chat turn into the wiki. Under the PKB root, not
  `lab/data/`, so "wipe the PKB = wipe memory" stays true. The transcript is a
  raw log and is **never** authoritative about the user: agents read only
  *approved* distilled facts, via `search_for_agents` and the Compiled-KB gate.
  Facts are sourced to a verbatim user quote and never distilled from an
  agent's own output. See `docs/conversation-memory.md`.
- **Chat memory is not DaC-governed at runtime, deliberately.** DaC is a
  build-time pipeline with no write API that explicitly defines itself against
  storing conversation history; we borrow its declare→gate→version discipline,
  not its pipeline. Don't wire this to DaC without superseding
  `docs/adr/0002-chat-memory-and-the-dac-boundary.md`.
- **Model checkpoint paths stay relative / env-driven — never a home dir.**
  ARAIL's model location is `ARAIL_MODELS_DIR` (default `lab/models`,
  repo-relative). Don't hardcode absolute paths; ARAIL is a blueprint other
  people run on their own machines. *Machine-level convention:* on boxes shared
  across QuKaiZen products, MLX checkpoints are pooled world-readable at
  `/Users/Shared/models/` (with `~/models` symlinked to it) so multiple macOS
  accounts read one copy — required by QueueLLM's GA gate #6 cross-user replay.
  Opt in per-machine with `ARAIL_MODELS_DIR=/Users/Shared/models`; prefer that
  location when downloading new checkpoints there. Do **not** make it a product
  default. See `docs/models-on-disk.md`.
- **The "no cross-repo runtime imports" DaC boundary has one scoped exception:
  `dac_world`.** World generation's core code (`forge_world`/`write_bundle`/
  `reseal_bundle`/`render_world_skill`/`validate_bundle_content`) moved out of
  this repo's `src/arail/world_forge.py` into a vendored copy of DaC-owned code
  (`src/dac_world/`, copied from `qukaizen-dac`'s `dac_world/`, per ADR-0004);
  `world_forge.py` is a thin re-export shim over it. This is a deliberate,
  narrow reversal of the boundary ADR-0002 guards for chat memory — it applies
  **only** to `dac_world` (World forging/sealing), not to chat memory or any
  other DaC surface, and `dac_world` itself is model-free and ARAIL-free (no
  `import arail`, enforced by DaC's own CI). The copy has no drift check today
  (see `docs/adr/0004-vendor-dac-world-for-offline-friendly-setup.md` and
  `INTEGRATION_AUDIT.md` in the workspace root). ARAIL still owns the router,
  portal, async plumbing, and where sealed bundles are hosted (`lab/worlds/`)
  — only the generator code is shared.
- **`.gitignore` is comprehensive** (`models/`, `lab/models/`,
  `node_modules/`, `__pycache__/`, runtime state under `lab/pkb/`).
  The 47M `.git/` history bloat is from a single 42M PDF and
  accidentally-committed `node_modules/` before the ignore rules
  caught them; current state is clean. Don't try to clean history
  without coordinating — the user has decided to leave it.
- **The `qkz` symlink** in this repo points at `./arailctl`. It is **not**
  the qukaizen-nucleus Rust CLI; that lives in `~/ProJects/qukaizen-nucleus/qkz/`.
  Don't confuse the two.
- **`lab/instances/` is the runtime home for concurrently-running World
  instances** (`./arailctl start --world <slug>`) — registry, per-instance
  `data/`/`pkb/`/env-pack. **Not the same thing as** repo-root
  `instances/`, which `./arailctl blueprint create` scaffolds
  (config-only, nothing under `src/arail/` reads it, never itself
  instantiated into a running process). See `docs/concurrent-worlds.md`
  and `sprints/BACKLOG.md`'s "Unify blueprint instances with runtime
  instances" entry — a known, tracked, not-yet-scheduled unification.
  `scripts/lib/instances.sh` is the single source of truth for the
  registry/liveness logic; `arailctl`, `scripts/start.sh`,
  `scripts/status.sh`, `scripts/reset.sh`, and
  `scripts/install-daemon.sh` all source it rather than re-deriving a
  liveness check locally — don't add a sixth implementation.
- **Per-instance secrets are never shared or auto-copied.** Each World
  instance's `secrets.env` lives in its own `data/` dir, `0600`, created
  only when a key is first saved there. A shared/symlinked secrets file
  across instances would silently let one lab read another's provider
  keys — treat any code path that copies or links a `secrets.env` between
  instances (or from the root lab) as a bug, not a convenience.
- **One PKB root per process is a load-bearing invariant, not an
  accident.** `src/arail/pkb_index.py`'s degraded-state tracking
  (`_degraded_codes`, `_pending`, `_timer`, `_initialized_roots`,
  `_pkb_root_cache`) is process-global, not per-root — safe only because
  `arail.config.PKB_ROOT` is a module constant never rebound in-process
  anywhere in `src/`, and because concurrent Worlds run one process per
  World. Rebinding `PKB_ROOT` in-process, or running two Worlds in one
  process, would let one root's degraded codes (or pending upserts) leak
  into another's. See the module docstring in `pkb_index.py` and the
  `sprints/BACKLOG.md` entry ("pkb_index's degraded state is a module
  global; PKB roots are per-World") before touching either half of this.

## Where to start when you pick a task

1. **Skim this file**, then `README.md`, then `BLUEPRINTS.md` and
   `design.md` for philosophy.
2. **For a portal change**: `src/arail/portal/app.py` is the FastAPI
   entry; templates in `src/arail/portal/templates/`; static assets
   in `src/arail/portal/static/`.
3. **For an agent change**: `docs/agents.md` has the loader contract.
   `lab/pkb/agents/<id>/` is where agents live. The three built-ins
   (Buddy, SRE, Researcher) are reference implementations.
4. **For setup / port work**: `scripts/setup.sh` is the only file you
   need to touch. `AGENTS.md` walks an external agent through what each
   `case` statement does and what to add.
5. **For QueueLLM context**: `research/aerollm/00-product-vision.md`
   through `04-measurement-log.md` are the design docs. The actual
   runtime is `~/ProJects/qukaizen-queuellm`.
6. **Knowledge Base ingest**: easiest path is the portal —
   `/knowledge` has folder-reveal buttons (`lab/pkb/inbox` for docs,
   `lab/models` for model weights), full-page drag-drop, and a
   per-file post-upload toast with `[Open]` / `[Reveal]` links. CLI
   equivalent is `./arailctl pkb ingest <file>`. Wiki + embedded graph
   auto-refresh on a "Wiki rebuilt" SSE event from
   `wiki.schedule_rebuild()`; the reveal endpoint is
   `POST /api/system/reveal` with whitelisted slots
   (`inbox`/`models`/`pkb_root`/`sources`/`compiled`/`user_data`). LanceDB
   index lives at `lab/pkb/.wiki-cache/lancedb/`.

## Files to read first

In rough priority order:

- `README.md` — user-facing project orientation; tier descriptions; surfaces.
- `BLUEPRINTS.md` — philosophy: ARAIL is a blueprint, not a product.
- `design.md` — design choices (3 KB, dense).
- `docs/agents.md` — agent architecture and loader contract.
- `AGENTS.md` — external-agent porting manifest. (Different from
  Claude-onboarding; keep separate.)
- `research/aerollm/README.md` — the bridge to the QueueLLM repo.
- `ROADMAP.md` — forward plan; check before proposing significant changes.

## What this file is not

This is a Claude-orientation doc. `AGENTS.md` is the *external coding
agent* porting manifest — different audience, different scope. The
README is for users. CLAUDE.md is for me.
