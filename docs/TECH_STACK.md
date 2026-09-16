---
title: "Tech Stack"
description: "One interface, ten backends: how ARAIL's Model Mesh routes between local runtimes and cloud APIs, and what sits behind it."
category: "Architecture"
order: 10
tags:
  - architecture
  - stack
  - router
  - mesh
  - backends
  - reference
audience: architect
related:
  - the-lab
  - CERTIFIED_MODELS
  - REPOSITORY_LAYOUT
  - PRIVACY
---

# Tech Stack

ARAIL doesn't run on one model. It runs on a **mesh**: a single interface
in front of ten interchangeable inference backends, a hard security
boundary around every outbound call, and cost/latency accounting on
every completion — regardless of which backend answered it. This page
is the map of that stack: what's actually running, where, and why.

> *The first version of this instinct was a Kubernetes service mesh,
> wired up so observability and security could be plugged into any AI
> component without that component knowing it was there. The Model
> Mesh is the same idea one layer up the stack: swap the sidecar for a
> Python router, swap the cluster for a laptop, keep the contract —
> every call is observed, every call is governed, and nothing upstream
> has to care which backend is behind the interface.*

---

## The Model Mesh — `arail.router`

`src/arail/router/core.py` is the single entry point. Everything in
ARAIL that needs a completion calls `ModelRouter.complete()` or
`.stream_complete()` — never a provider SDK directly. The router owns
backend selection, and callers never need to know which of the ten is
answering.

```
                         ┌──────────────────────────┐
callers (agents, chat,   │      ModelRouter          │
Canvas, Autoresearch) ──▶│  .complete() / .stream()  │
                         └────────────┬──────────────┘
                                      │ backend_name = config | env | auto-detect
                                      ▼
                    ┌─────────────────────────────────────┐
                    │            BACKEND_MAP               │
                    ├───────────────┬───────────────────────┤
                    │  local         │  cloud (airgap-gated) │
                    │  mlx           │  claude               │
                    │  cuda          │  openrouter           │
                    │  cpu           │  huggingface          │
                    │  ollama_native │  openai_compat (NIM…) │
                    │  airllm        │                        │
                    │  aerollm ──────┼─▶ QueueLLM (sibling repo)│
                    └───────────────┴───────────────────────┘
                                      │
                                      ▼
                     cost_tracker.track(backend, model, tokens,
                                        latency, cache stats)
```

### The ten backends (`BACKEND_MAP` in `src/arail/router/backends.py`)

| Key | Class | Where it runs | Notes |
|---|---|---|---|
| `mlx` | `MLXBackend` | Apple Silicon, in-process | Primary local lane on Mac |
| `cuda` | `CUDABackend` | NVIDIA GPU | |
| `cpu` | `CPUBackend` | Any machine | Slow-path fallback |
| `ollama_native` | `OllamaNativeBackend` | LAN / loopback | Native `/api/chat`, `num_ctx` control |
| `airllm` | `AirLLMBackend` | Local, layered load | Runs models too big to fit resident; trades latency for reach (Llama-3.1-70B/405B, MoE) |
| `aerollm` | `AeroLLMBackend` | Local, streamed | The **QueueLLM** engine (renamed from AeroLLM, Aug 2026) — see [Sibling engines](#sibling-engines-plugged-into-the-mesh) |
| `claude` | `ClaudeBackend` | Cloud | Anthropic |
| `openrouter` | `OpenRouterBackend` | Cloud | Multi-provider aggregator |
| `huggingface` | `HuggingFaceBackend` | Cloud | HF Inference |
| `openai_compat` | `OpenAICompatBackend` | Cloud | Anything that speaks OpenAI Chat Completions — vLLM, NVIDIA NIM, custom endpoints |

### What the router does that a plain adapter pattern doesn't

- **Auto-detection.** `backend=auto` (the default) probes hardware, not
  just config: Apple Silicon with `mlx_lm` importable → `mlx`; Apple
  Silicon without it → `ollama_native`; `nvidia-smi` present → `cuda`;
  else `cpu`. It runs a real import, not `find_spec`, so a half-broken
  install is never reported as available (`_is_importable`, `core.py`).
- **Airgap gating.** `claude`, `huggingface`, and `openrouter` are
  refused at construction time — before any network call — when the
  lab is airgapped. See [Security boundary](#the-security-boundary-airgap-by-default) below.
- **Cost and latency accounting.** Every call — local or cloud — logs
  backend, model, tokens in/out, latency, and prompt-cache stats
  through `cost_tracker`, tagged with billing source, provider, entry,
  and tab. One accounting path regardless of which of the ten answered.
- **Live backend switching.** `switch_backend()` swaps the active
  backend at runtime — this is what powers the **Compute Source** pivot
  in the Chat tab (see [the-lab.md](the-lab.md#compute-source--local-first-cloud-when-you-want-it)).

### Two more routers in the mesh — don't confuse these

The word "router" shows up three times in this codebase for three
different jobs. Keep them separate:

1. **`arail.router.core.ModelRouter`** (above) — picks *which backend*
   answers a completion, at call time.
2. **`lab/tools/model_router.py`** — a budget-aware *scheduler*, not a
   request router. Given an expected token count and a time budget, it
   picks the best model+backend+batching combination from benchmarked
   `model_profiles.json` data (cost vs. latency), for experiment
   runners deciding what to launch — not for answering a single chat
   turn.
3. **`core/knowledge-canvas/backend/app/services/llm_router.py`** — not
   a router at all, a thin proxy. The Knowledge Canvas backend never
   talks to an LLM directly; this module imports the lab's
   `model_router` from the lab root so a backend swap (Ollama →
   OpenRouter → Claude → local MLX) is one `.env` change, not a code
   change in Canvas.

(A fourth sense — Mixture-of-Experts *token* routing, i.e. which
experts inside one model a token activates — belongs to QueueLLM, not
to any of the above. See below.)

---

## The security boundary — airgap by default

This is the part of the mesh that isn't optional, and it's the direct
descendant of the service-mesh instinct: every outbound call is
governed at one layer, regardless of what's calling it.

- **`LAB_MODE=airgapped` is the default.** Agent-originated outbound
  HTTP (`requests`, `urllib`) is denied unless the destination is
  loopback, RFC1918, or link-local. LAN boxes (Ollama, vLLM, an
  `aerollm` node) keep working — only the public internet is sealed.
- **Every denial is audited**, appended to `lab/data/egress.jsonl`.
- **`LAB_MODE=hybrid`** turns cloud backends into fallbacks rather than
  blocking them outright — the **Compute Source** row in Chat is the
  user-facing pivot, and the Network Policy modal toggles the mode from
  the UI (disabled once the portal is exposed on a LAN, to avoid CSRF).
- The mental model the docs state directly: *"there is no 'sometimes
  online' middle state."* You always know which side of the line a
  given call is on.

Full enforcement detail and known gaps: [PRIVACY.md](PRIVACY.md).

---

## Sibling engines plugged into the mesh

These aren't part of ARAIL's source tree — they're consumed as
backends or dependencies, each its own repo:

- **QueueLLM** (`qukaizen-queuellm`, formerly **AeroLLM** — renamed
  2026-08-21; the `aerollm` backend key and `AERO_*` env vars still
  carry the old name deliberately, per the renamed repo's own
  deprecation notes). A Rust, mmap-streaming inference runtime: it
  streams model weights from NVMe layer-by-layer, and for
  Mixture-of-Experts models *expert-by-expert* — fetching only the
  experts the model's internal router actually selects for a given
  token — so peak memory is bounded by one layer plus the KV cache
  instead of the full parameter count. MLX is the production lane,
  CUDA a real second lane. This is where "router" means MoE gating,
  not request routing.
- **AirLLM** — the layered-loading fallback used when QueueLLM has no
  fast path for an architecture yet, or the model is larger than fits
  resident even with streaming (Llama-3.1-405B, MoE-17B-128E).
- **Cloud APIs** — Claude, OpenRouter, HuggingFace Inference, and
  anything OpenAI-compatible (NVIDIA NIM, vLLM, a custom endpoint) —
  all gated by the airgap boundary above.

---

## The data layer the mesh feeds

Covered in depth in the graph-vs-vector discussion earlier, summarized
here for completeness:

| Store | Role |
|---|---|
| **Neo4j** (`core/knowledge-canvas`) | The `Source` graph — structural relationships, traversal, provenance |
| **LanceDB** | Vector search over the same sources |
| **SQLite** | ARAIL's relational store (see [ADR 0005](adr/0005-sqlite-as-the-relational-store.md)) |

Graph RAG queries combine the first two: a Cypher traversal picks
candidate node IDs first (structural anchor), then a vector search is
restricted to that ID set — see
[`GRAPH_RAG.md`](../core/knowledge-canvas/docs/GRAPH_RAG.md).

---

## Adjacent Qukaizen components (not embedded in ARAIL)

Worth knowing about, not part of this mesh:

- **`qukaizen-gateway`** — a separate intent-routing inference gateway
  (photo in, classify content + intent, route to the matching "World")
  for the QuKaiZen app. Routing in the HTTP-gateway sense, unrelated to
  the Model Mesh.
- **`project-nucleus`** — a NATS JetStream + LangGraph agent
  orchestrator for a teacher→student model distillation pipeline
  (NVIDIA NIM as teacher). Its Docker network is literally named
  `nucleus-mesh`, which reads like a naming coincidence with this doc's
  title but is a service-mesh-in-the-networking-sense (a Docker bridge
  network), not a multi-LLM router.
- **`qukaizen-ddac`** — the Data-as-Code compiler; unrelated to model
  routing.
- **`qukaizen-app`** — the Expo/React Native mobile client.

---

## Open question worth deciding

Nothing in the code currently calls this pattern a "mesh" — that name
lives in conversation and now in this doc. If it's worth making
official, the natural next step (given ARAIL already tracks decisions
as ADRs — see [docs/adr/](adr/README.md)) is a short ADR: *why "Model
Mesh" is the name, what it does and doesn't imply versus a literal
service mesh, and whether the term should propagate into code
(module/variable names) or stay documentation-only.* Say the word and
I'll draft it as ADR 0006 (0006 is open — see the numbering caveat at
the top of the ADR index).
