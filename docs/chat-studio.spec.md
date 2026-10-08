# ARAIL Chat Studio — Redesign Spec

> A world-class developer-friendly chat surface. Reads like LM Studio, behaves like QueueLLM. Built on the ARAIL design system ([`design.md`](./design.md)) and the model/job backend ([`maximus.plan.md`](./maximus.plan.md)). Implementable by a small team in 4–6 weeks.

---

## 0. Principles

1. **Response area first.** The chat transcript is the page. Everything else is chrome and can collapse.
2. **One-line provenance.** Every response carries a single, parseable stats line directly beneath it.
3. **Explicit loading.** A model load is a noticeable, blocking event with progress + ETA + cancel — never silent.
4. **No silent compression.** Frontier models load full-precision unless the user opts into streaming or quantization.
5. **Two models is a first-class mode**, not an Easter egg. Compare/fallback is built in.
6. **Tokens are local secrets.** Stored encrypted, masked in the UI, never echoed.
7. **Tokens & themes obey [design.md](./design.md).** No hex literals in components; no theme-incompatible accents.

---

## 1. Page layout

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ⟨Autoresearch⟩  ◎ Mission · …          [12:04:33]              ● Default ● ⏸ │  ← shared nav
├──────────────────────────────────────────────────────────────────────────────┤
│  Chat Studio                       [Fast]  [Deep]  [Compare]   ⚙   ⤢  Logs ▸ │  ← header + presets
├──────────────────────────────────────────────────────────────────────────────┤
│ ▓ load: Qwen3-8B-4bit  ████████░░░░░░░  42 %  · eta 18 s  · stream-hot   ✕   │  ← loader bar (only when loading)
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   you   ▍ explain speculative decoding in two paragraphs                     │
│                                                                              │
│   mlx-openai · Qwen3-8B-4bit                                                 │
│   ▍ Speculative decoding pairs a small "draft" model with a larger          │
│   "verifier"…                                                                │
│   mlx · Qwen3-8B-4bit · 18.4 t/s · 1873 ms · 412 tok                         │  ← stats line
│                                                                              │
│   you   ▍ now compare to medusa heads                                        │
│   …                                                                          │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │ ▍ ask anything…                                              ⏎ Send   │  │  ← input
│  │ + secondary model (compare)                                            │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│  Model: [Qwen3-8B-4bit ▾]   Source: [Local ▾]   Preset: [Coder ▾]   ◯ stream │  ← compact controls
└──────────────────────────────────────────────────────────────────────────────┘
                                                                       ▲
              right sidebar (collapsible) ───────────────────────────► │
              ▾ Logs   ▾ Tunables (advanced)   ▾ Telemetry             │
```

### Regions

| Region | Behavior |
|---|---|
| **Nav** | Existing portal nav (logo + mission + clock + theme picker + mode badge). Untouched. |
| **Header** | Page title, **preset toggle** (Fast / Deep / Compare), settings cog, expand-to-fullscreen, **Logs ▸** drawer button. |
| **Loader strip** | Full-width 36 px bar, only present while a model load is `loading-prep|loading-weights|warming`. Carries progress, ETA, strategy, cancel. Shifts content down — never overlays. |
| **Transcript** | Full-bleed messages. User on right, assistant on left, tight rhythm. Long content scrolls; controls and input stay pinned. |
| **Input** | Pinned bottom. Single textarea by default. **`+ secondary model`** chip expands to dual-input mode (see §5). |
| **Compact controls** | Single row immediately under the input: Model · Source · Preset · streaming toggle · Send. |
| **Right sidebar** | Hidden by default. Drawer with three accordions: **Logs** (live SSE), **Tunables** (per-model panel, see §4), **Telemetry** (live tok/s, GPU, KV). Width 380 px. Toggled via `Logs ▸`. |

The terminal pop-out from the current chat is **removed** — its functionality moves into the Logs drawer (read-only) and the `/terminal` page (interactive).

---

## 2. Stats line format

One line, monospace, immediately beneath each assistant response. Muted color; click any segment for detail.

```
mlx · Qwen3-8B-4bit · 18.4 t/s · 1873 ms · 412 tok
```

### Transform rules

| Source | Transform |
|---|---|
| `mlx-community/Qwen3-8B-4bit` | provider=`mlx`, model=`Qwen3-8B-4bit` (strip vendor namespace, preserve casing) |
| `Qwen/Qwen2.5-7B-Instruct` | provider=`hf`, model=`Qwen2.5-7B-Instruct` |
| `claude-sonnet-4.6` (no namespace) | provider=`claude`, model=`claude-sonnet-4.6` |
| `openrouter:meta/llama-3.1-70b` | provider=`openrouter`, model=`llama-3.1-70b` |
| Local file `models/local/llama-3.1-8b-instruct/` | provider=`aerollm`, model=`llama-3.1-8b-instruct` |

### Field semantics

| Field | Source | Format |
|---|---|---|
| `provider` | `provenance.provider` | lowercase, no namespace |
| `model` | `provenance.model_id` | original case preserved |
| `t/s` | `tokens.completion / latency.total_s` | 1 decimal |
| `ms` | `latency.total_ms` | integer |
| `tok` | `tokens.completion` | integer |

### Click → overlay (small popover, ~320 px)

```
┌──────────────────────────────────────┐
│ Qwen3-8B-4bit                        │
│ ───────────────────────────────────  │
│ compute source   mlx-openai          │
│ gpu used         Apple M5 Pro        │
│ vram used        4.9 GB              │
│ kv cache         system memory       │
│ prefetch depth   2 layers            │
│ ttft             412 ms              │
│ strategy         stream-hot-pinned   │
│ endpoint         127.0.0.1:11435/v1  │
│                                      │
│        Show more →   /tuning         │
└──────────────────────────────────────┘
```

---

## 3. Model selection — the two-slot picker

> **Shipped as of sprints/2026-08-11-two-slot-chat-models** (Phase 5). This
> section describes the actual chat.html implementation, not an aspirational
> React redesign — unlike most of this spec (see §11's component list),
> the two-slot model is real and running. The single free-form "Model ▾"
> dropdown below was superseded by two named slot chips because a single
> dropdown can't express the resident-vs-deep distinction that's the whole
> point of the redesign: "this model in the GPU at all times" (resident,
> ~1–3B by default) plus "a second model for QueueLLM" (deep, served
> resident-once-loaded by QueueLLM). See the sprint's VISION.md for the full
> rationale and the five-overlapping-affordances problem it replaced.

Two header chips, each mapped 1:1 onto a registry entry (`tier0-local` /
`tier1-aerollm` — see `src/arail/registry/`), replace the single "Which
Brain"/"Model" dropdown:

```
 ● RESIDENT  ollama · ai-engineer:latest   eject  ▾        ● DEEP · QUEUELLM  queuellm · Qwen2.5-7B-Instruct-4bit   eject  ▾
```

Clicking a chip's `▾` opens that slot's picker:

```
Resident chip ▾                                    Deep chip ▾
┌─────────────────────────────────────────┐        ┌─────────────────────────────────────────┐
│ RESIDENT MODEL      Apple M5 Max·17GB free│        │ DEEP MODEL · QUEUELLM  ~32B cap on this HW│
│                                           │        │                                           │
│ llama-ai-eng:latest ·ollama NEW           │        │ Qwen2.5-7B-Instruct-4bit ·queuellm DEEP   │
│   Built with Llama          1.2 GB  good  │        │   ~7B params                    resident  │
│ llama3.2:1b ·ollama NEW           1.2 GB  good│        │                                           │
│ Qwen2.5-0.5B-Instruct-4bit ·mlx   0.3 GB  good│        │ OTHER INSTALLED MODELS  estimate — server │
│                                           │        │                          confirms on pick  │
│ [ show larger (19) — up to <8B ]         │        │ Llama-3.1-70B-Instruct-4bit ·mlx  STREAMED│
│                                           │        │   37.0 GB          requires streaming     │
│ PROVIDER · RUN ON      where this runs   │        │   ~70B is over the ~32B this machine can  │
│ [Local] QueueLLM Claude NVIDIA OpenRouter…│        │   hold as a resident deep model.          │
└─────────────────────────────────────────┘        │                                           │
                                                     │ Resident once loaded — QueueLLM keeps its  │
                                                     │ model fully in memory; it does not stream.│
                                                     │ PROVIDER · RUN ON ...                     │
                                                     └─────────────────────────────────────────┘
```

### Behavior

- **Resident picker** defaults to ≤3B-params rows (`slot_default_visible`);
  "show larger" reveals up to the primary-model ceiling (<8B, enforced
  server-side by `src/arail/registry/ceiling.py::resolve_answering_model` —
  never re-derived client-side). A row that would violate the ceiling still
  renders, marked ineligible, with the ceiling's own refusal string inline
  (reason-strings-first) instead of failing silently at send time.
- **Deep picker** lists the configured QueueLLM model plus every other
  installed MLX-runtime model as a real alternative, each checked against
  `hardware.secondary_model_cap_b()`; over-cap rows are shown-but-marked,
  never hidden. Selecting a different deep model swaps it in-process
  (`_swap_optional_chat_backend`) — no portal restart.
- Both pickers carry the same **Provider · run on** compute-source pivot;
  a source without send-path wiring renders disabled with the reason
  (`sources[].wired`) instead of erroring at send time.
- **Compare** runs both slots side by side rather than picking two
  arbitrary models — see §5.
- Auto-detect from the model registry watcher ([`maximus.plan.md` §3](./maximus.plan.md)). Files dropped into `ARAIL_MODELS_DIR` appear with a `NEW` badge for 24 h.
- Each row carries a **fit verdict** chip (Good / Marginal / Unknown / Streaming) computed by the fit estimator (`maximus §5`); missing data reads Unknown, never a fabricated Good.
- Picking a model that requires loading triggers the §6 load flow.
- Custom endpoint accepts an OpenAI-compatible URL; on save it's added to the registry as a `manual` source.

---

## 4. Per-model LM Studio-style tunables

Lives in the right sidebar's **Tunables** accordion. Persists per-model in localStorage (key `arail.tunables.<model_id>`); a cog in the model dropdown opens it directly. Hidden behind a single click — never in the user's face by default.

Every tunable row renders the same scaffold:

```
┌────────────────────────────────────────────────────────────────────────┐
│ context window (CTX)                                            8192   │
│ how many tokens of history the model can attend to.                    │
│ ─ 512 ────●──────── 131072      recommended: 4096–32768                │
│ impact: ⏱ latency ↑   📦 memory ↑↑   ✦ quality ↑   [expert ⓘ]   docs ↗ │
└────────────────────────────────────────────────────────────────────────┘
```

### Tunables (required)

| Tunable | Default | Range | Impact |
|---|---|---|---|
| **CTX (context window)** | 8192 | 512 – model-max | latency ↑ · memory ↑↑ · quality ↑ |
| **temperature** | 0.7 | 0.0 – 2.0 | creativity ↑ |
| **top_p** (nucleus) | 0.95 | 0.0 – 1.0 | diversity ↑ |
| **top_k** | 40 | 0 – 200 | diversity ↑ |
| **repetition_penalty** | 1.05 | 1.0 – 2.0 | reduces loops |
| **max_tokens** (response) | 1024 | 1 – ctx-prompt | latency ↑ |
| **streaming chunk size** | 4 tok | 1 – 64 | smoothness vs network overhead |
| **prefetch depth** | 2 layers | 0 – 8 | hides IO; memory ↑ |
| **double buffer size** | 1 | 1 – 4 | smoother streaming; memory ↑ |
| **KV cache location** | auto | gpu · ram · nvme | memory pressure trade |
| **offload strategy** | auto | full · shard · stream-hot · stream-all | latency vs fit |
| **quantization** | off (frontier) / model-default | off · 8-bit · 4-bit | quality ↓ · memory ↓↓ |
| **batch size** (local) | 1 | 1 – 8 | throughput vs latency |
| **tokenization** | model default | model · tiktoken-cl100k | tool/code-friendliness |
| **stop sequences** | [] | list | hard cutoffs |
| **safety filters** | provider default | off · light · strict | blocks unsafe outputs |

### Expert tooltip

Each row's `[expert ⓘ]` opens an inline drawer with: math behind the parameter, common pitfalls, and 2–3 example settings for typical workloads (code, chat, RAG, agents).

### "Learn more" link

Every row's `docs ↗` deep-links to `/docs/tunables.md#<id>` — a single doc per tunable, kept in sync via doc-link CI check.

---

## 5. Dual-model input (compare / fallback / route)

> **Shipped subset (sprints/2026-08-11-two-slot-chat-models Phase 5):**
> `+ Compare` runs the resident and deep slots side by side — column A is
> always the resident slot, column B is always the deep slot (a deliberate
> reversal of an earlier "any model in column B" design; real per-column
> choice lives inside each slot's own picker instead, see §3). Only the
> `compare` routing mode below is implemented: both slots always receive
> the same prompt and both responses render. `primary only` /
> `secondary only` / `both`-with-fallback, the free-form PRIMARY/SECONDARY
> provider dropdowns, and side-by-side vs. stack layout choice are not
> implemented — the two columns are always side by side. Per-column chat
> history is real (`aHistory`/`bHistory`, routed by the persisted
> `msg.branch`).

Default: single textarea. Click the `+ secondary model` chip beneath the textarea to expand into dual mode.

```
┌────────────────────────────────────────────────────────────────────────┐
│ PRIMARY     local · Qwen3-8B-4bit ▾          stream ◯                  │
│ ▍ ask anything…                                                        │
│                                                                        │
│ SECONDARY   claude · claude-sonnet-4.6 ▾     [stack | side-by-side]    │
│ ▍ (mirror primary)   [edit independently]                              │
│                                                                        │
│ Routing:  ◉ compare   ○ primary only   ○ secondary only   ○ both       │
│                                                       Send ⏎          │
└────────────────────────────────────────────────────────────────────────┘
```

### Layouts

- **Stack (default)**: primary response above secondary, both full-width, each with its own stats line.
- **Side-by-side**: 50/50 columns; for diff comparisons.

### Routing modes

| Mode | Behavior | Stats |
|---|---|---|
| `compare` | Both models receive same prompt; both responses rendered with provenance | two stats lines + a `Δ tok / Δ ms / Δ first-difference-at-token-N` summary |
| `primary only` | Secondary slot collapsed | one stats line |
| `secondary only` | Primary slot collapsed | one stats line |
| `both` | Both run, primary is "official"; secondary is shown muted as fallback (used iff primary errors) | one stats line by default; "show fallback" reveals the second |

### Mirror toggle

By default the secondary input mirrors the primary. The `[edit independently]` link splits the inputs so the user can ask two different things and watch them race.

---

## 6. Model load UX

### Fit verdict (pre-load)

When a user picks a model that isn't resident, immediately show the fit verdict inline:

```
Fit: Requires streaming
  reason   8 GB free VRAM, 14 GB needed for full load
  ETA      ~ 22 s (NVMe 4.8 GB/s)
  strategy stream-hot-pinned (recommended)  ▾
                                                Cancel  Load anyway →
```

### Load progress (full-width strip)

While loading, the loader strip is rendered between header and transcript (see §1 ASCII). It's a 36 px tall band — visually present, not modal.

```
▓ load: Qwen3-8B-4bit  ████████░░░░░░░  42 %  · eta 18 s  · stream-hot-pinned   ✕ Cancel
```

States from [`maximus §5`](./maximus.plan.md): `loading-prep → loading-weights → warming → resident`. Each transition updates the bar's caption: `prep · loading weights · warming up · ready`.

### Blocking behavior

- The transcript is **read-only** while a load is in progress (you can scroll, can't send).
- The Send button is disabled and shows the load state in its tooltip.
- Switching to another model from the dropdown while a load is in progress queues the request behind the current load and shows a single warning.
- Cancel asks for confirmation; on cancel the model returns to `unloaded`.

### ETA accuracy

ETA is computed from `model.on_disk_bytes / measured_throughput_mbps + warmup_offset`. A rolling NVMe throughput probe runs every 60 s; warmup_offset is the median of the last 5 loads of the same family. Acceptance criterion: ETA within ±20 % for common models (see §10).

---

## 7. Credentials & router

Single in-page settings drawer (cog → **Connections**). No external admin page needed for the common path.

```
Connections
─────────────────────────────────────────────────────────────
HF              ●  charlesadarnell  · 14 gated repos accepted   [revoke]
Claude          ○  paste token…  ────────────────────────────   [verify]
NVIDIA          ○  paste token…  ────────────────────────────   [verify]
OpenRouter      ○  paste token…  ────────────────────────────   [verify]
Custom OpenAI   ○  https://…/v1   key ────────────                [save]

How tokens are stored
  Encrypted at rest under ~/.arail/secrets.age. Never logged. Never sent
  to any service except the one they belong to. Revoke any time.

Login with HF →   (opens HF auth in a new tab)
```

### Behavior

- Token fields are **password-masked**; an eye toggle reveals.
- On `[verify]`, the backend round-trips a no-op call (`/me` for Claude, `/whoami` for HF, `/me` for OpenRouter, `/listMyModels` for NVIDIA) and surfaces success/failure inline.
- On success, the row badge turns green and shows the verified username (when the provider returns one).
- **Login with HF** opens `https://huggingface.co/login?next=…` in a new tab and uses the redirect handler in [`maximus §7`](./maximus.plan.md).

### Router defaults

Same drawer, separate accordion:

```
Router defaults
  primary       local · Qwen3-8B-4bit
  secondary     claude · claude-sonnet-4.6
  on local fail fall back to secondary   ◉
                show error                ○
  per-model overrides   →   manage…
```

---

## 8. Fine-tune tab (per-model)

Each model card in the dropdown has a `Fine-tune` link → opens a dedicated tab that wraps the [`maximus §6` factory](./maximus.plan.md) in a guided wizard.

### Wizard steps

1. **Dataset** — drop a JSONL/Parquet/CSV; preview first 5 rows; show row count and detected schema. Server runs preflight (size, format, allowlist). Inline errors block "next".
2. **Method** — `qlora` (recommended) / `lora` / `full`. Each pick shows: VRAM estimate for current GPUs, expected duration, output size on disk.
3. **Hyperparams** — `epochs`, `lr`, `batch_size`, `lora_r`, `lora_alpha`, `target_modules`. Defaults sensible per method. Each field has the same scaffold as §4 tunables.
4. **Hardware** — pick GPUs; show live `Σ free VRAM`. Refuses if below preflight threshold.
5. **Estimate** — final summary panel: time, VRAM peak, disk write, eval suite that will run.
6. **Submit** — opens the job stream view; redirects to chat with the new model selected on success.

### Skills marketplace

Inside the wizard, step 2 has a **Skills** tab listing curated LoRA recipes:

```
Skills (LoRA recipes you can apply)
  ☐  Customer-support QA pack    20 MB    ~12 min on M5    rating ★★★★☆
  ☐  Cypher → SQL translator     6 MB     ~5 min          rating ★★★★★
  ☐  ARAIL agent self-tune        45 MB    ~28 min          rating ★★★★☆
  ─────────────────────────────────────────────────────────────────────
  Each skill ships its own dataset, hyperparams, and eval set. Clicking
  one prefills steps 1–3 and runs steps 4–6 unchanged.
```

Skills live in `skill_packs/finetune/<id>/skill.yaml` (existing convention extended with a `finetune:` block).

---

## 9. OpenAPI (chat surface)

Subset of the full ARAIL spec ([`maximus §9`](./maximus.plan.md)) — these are the endpoints the chat studio touches.

```yaml
openapi: 3.1.0
info: { title: ARAIL Chat Studio, version: 0.1.0 }
paths:
  /api/chat/models:
    get:
      summary: Unified gallery (installed + catalog + provenance)
      responses: { "200": { content: { application/json: { schema: { $ref: "#/components/schemas/ChatGallery" } } } } }
  /api/chat/stream:
    post:
      summary: Stream a chat completion (SSE)
      requestBody: { content: { application/json: { schema: { $ref: "#/components/schemas/ChatRequest" } } } }
      responses: { "200": { content: { text/event-stream: {} } } }
  /api/chat/dual-stream:
    post:
      summary: Stream both primary + secondary in parallel (SSE multiplexed by `slot`)
      requestBody: { content: { application/json: { schema: { $ref: "#/components/schemas/DualChatRequest" } } } }
      responses: { "200": { content: { text/event-stream: {} } } }
  /api/chat/model-load:
    get:
      summary: Current load state for the chat surface (snapshot)
      responses: { "200": { content: { application/json: { schema: { $ref: "#/components/schemas/LoadState" } } } } }
  /api/chat/model-load/stream:
    get:
      summary: SSE stream of load-state transitions
      responses: { "200": { content: { text/event-stream: {} } } }
  /api/chat/model-load/cancel:
    post: { responses: { "202": {} } }
  /api/chat/tunables/{model_id}:
    get:  { responses: { "200": { content: { application/json: { schema: { $ref: "#/components/schemas/Tunables" } } } } } }
    put:  { requestBody: { content: { application/json: { schema: { $ref: "#/components/schemas/Tunables" } } } }, responses: { "204": {} } }
  /api/tokens/{provider}:
    post: { requestBody: { content: { application/json: { schema: { type: object, properties: { token: { type: string } } } } } }, responses: { "201": {} } }
    delete: { responses: { "204": {} } }
  /api/tokens/{provider}/status:
    get:  { responses: { "200": { content: { application/json: { schema: { $ref: "#/components/schemas/TokenStatus" } } } } } }
  /api/jobs/finetune:
    post: { requestBody: { content: { application/json: { schema: { $ref: "#/components/schemas/FinetuneRequest" } } } }, responses: { "202": { content: { application/json: { schema: { $ref: "#/components/schemas/Job" } } } } } }
components:
  schemas:
    ChatGallery: # excerpt; full shape matches today's /api/chat/models response
      type: object
      properties:
        installed: { type: array, items: { $ref: "#/components/schemas/InstalledModel" } }
        catalog:   { type: array, items: { $ref: "#/components/schemas/CatalogModel" } }
        compact:   { $ref: "#/components/schemas/CompactPicker" }
    ChatRequest:
      type: object
      required: [messages]
      properties:
        messages:    { type: array, items: { $ref: "#/components/schemas/Message" } }
        model_id:    { type: string }
        provider:    { type: string }
        tunables:    { $ref: "#/components/schemas/Tunables" }
        stream:      { type: boolean, default: true }
    DualChatRequest:
      allOf:
        - $ref: "#/components/schemas/ChatRequest"
        - type: object
          properties:
            secondary:
              type: object
              properties:
                model_id: { type: string }
                provider: { type: string }
                prompt_override: { type: string, nullable: true }
            mode: { type: string, enum: [compare, primary-only, secondary-only, both] }
    LoadState:
      type: object
      properties:
        state:        { type: string, enum: [unloaded, loading-prep, loading-weights, warming, resident, failed, unloading] }
        progress:     { type: number, minimum: 0, maximum: 1 }
        eta_seconds:  { type: number, nullable: true }
        strategy:     { type: string }
        model:        { type: string }
        runtime:      { type: string }
        error:        { type: string, nullable: true }
    Tunables:
      type: object
      properties:
        ctx:                  { type: integer, minimum: 512 }
        temperature:          { type: number, minimum: 0, maximum: 2 }
        top_p:                { type: number, minimum: 0, maximum: 1 }
        top_k:                { type: integer }
        repetition_penalty:   { type: number, minimum: 1, maximum: 2 }
        max_tokens:           { type: integer, minimum: 1 }
        streaming_chunk:      { type: integer, minimum: 1, maximum: 64 }
        prefetch_depth:       { type: integer, minimum: 0, maximum: 8 }
        double_buffer:        { type: integer, minimum: 1, maximum: 4 }
        kv_cache_location:    { type: string, enum: [auto, gpu, ram, nvme] }
        offload_strategy:     { type: string, enum: [auto, full, shard, stream-hot, stream-all] }
        quantization:         { type: string, enum: [off, "8-bit", "4-bit"] }
        batch_size:           { type: integer, minimum: 1, maximum: 8 }
        tokenization:         { type: string }
        stop:                 { type: array, items: { type: string } }
        safety:               { type: string, enum: [off, light, strict] }
```

### Plugin author guide (≤ 1 page)

A plugin adds a provider by implementing `ProviderAdapter` ([`maximus §8`](./maximus.plan.md)) and registering it in `pyproject.toml`:

```toml
[project.entry-points."arail.providers"]
my_thing = "my_pkg.adapter:MyAdapter"
```

The adapter must:
- accept `Tunables` and pass them through (or document which it ignores),
- emit `LoadState` events on the SSE protocol,
- return `provenance` on every inference response so the stats line renders.

---

## 10. Acceptance criteria & tests

### Playwright suite (`tests/e2e/chat-studio.spec.ts`)

```ts
test("stats line renders under each response", async ({ page }) => {
  await page.goto("/chat");
  await page.fill("textarea[name=prompt]", "say hi");
  await page.keyboard.press("Enter");
  const stats = await page.locator(".message.assistant .stats-line").last();
  await expect(stats).toContainText(/^\w+ · [\w.\-/]+ · [\d.]+ t\/s · \d+ ms · \d+ tok$/);
});

test("model dropdown shows local models with fit verdict", async ({ page }) => {
  await page.goto("/chat");
  await page.click(".model-picker");
  await expect(page.locator(".local-models .row")).toHaveCountGreaterThan(0);
  await expect(page.locator(".fit-chip").first()).toHaveText(/Good|Marginal|Requires streaming/);
});

test("loader bar blocks send", async ({ page, mockGallery }) => {
  await mockGallery({ resident: false });
  await page.goto("/chat");
  await page.click("text=deepseek-r1:14b");
  await expect(page.locator(".loader-strip")).toBeVisible();
  await expect(page.locator("button.send")).toBeDisabled();
  await page.click(".loader-strip .cancel");
  await expect(page.locator(".loader-strip")).toBeHidden();
});

test("preset Coder applies tunables", async ({ page }) => {
  await page.goto("/chat");
  await page.selectOption(".preset", "Coder");
  const ctx = await page.locator("[data-tunable=ctx] input").inputValue();
  expect(parseInt(ctx)).toBeGreaterThanOrEqual(16384);
});

test("dual-model compare shows two responses + delta", async ({ page }) => {
  await page.goto("/chat");
  await page.click("text=+ secondary model");
  await page.selectOption(".secondary .model", "claude:claude-sonnet-4.6");
  await page.fill("textarea[name=prompt]", "explain raft in one sentence");
  await page.keyboard.press("Enter");
  await expect(page.locator(".message.assistant")).toHaveCount(2);
  await expect(page.locator(".compare-delta")).toContainText(/Δ tok|Δ ms/);
});

test("tunables persist per model", async ({ page }) => {
  await page.goto("/chat");
  await page.click(".tunables-toggle");
  await page.fill("[data-tunable=temperature] input", "0.2");
  await page.reload();
  await expect(page.locator("[data-tunable=temperature] input")).toHaveValue("0.2");
});
```

### Performance acceptance

| Check | Target |
|---|---|
| Model load ETA accuracy | within ±20 % for any model used ≥ 3 times |
| Streaming prefetch hit-rate (NVMe ≥ 4 GB/s) | ≥ 0.95 |
| TTFT for resident local 8B | ≤ 600 ms p95 |
| Stats line rendered before response is fully complete | `t/s` updates live |
| Dual-stream multiplex latency overhead vs single | ≤ 8 % |

---

## 11. React component list

```
src/portal/chat-studio/
├── index.tsx                  // root, wires routes/state
├── layout/
│   ├── Header.tsx             // title + presets + cog + logs button
│   ├── LoaderStrip.tsx        // §6 loader bar (SSE-driven)
│   ├── Transcript.tsx         // virtualized scroll
│   └── Sidebar.tsx            // collapsible drawer
├── messages/
│   ├── MessageUser.tsx
│   ├── MessageAssistant.tsx   // body + StatsLine + provenance overlay
│   ├── StatsLine.tsx
│   └── ProvenanceOverlay.tsx
├── input/
│   ├── ComposerSingle.tsx
│   ├── ComposerDual.tsx       // §5
│   └── ControlsRow.tsx        // model / source / preset / stream
├── model/
│   ├── ModelPicker.tsx        // dropdown w/ local + sources + custom
│   ├── FitChip.tsx
│   └── AddLocalModelDialog.tsx
├── tunables/
│   ├── TunablesPanel.tsx
│   ├── TunableRow.tsx         // generic scaffold (label, slider, impact, expert)
│   └── presets.ts             // Fast / Deep / Coder / Compare
├── connections/
│   ├── ConnectionsDrawer.tsx
│   ├── TokenRow.tsx
│   └── HFLoginButton.tsx
├── finetune/
│   ├── FinetuneTab.tsx        // wizard host
│   └── steps/{Dataset,Method,Hyperparams,Hardware,Estimate,Submit}.tsx
└── lib/
    ├── sse.ts                 // typed EventSource helpers
    ├── transformProvider.ts   // §2 transform rules
    └── store.ts               // tunables localStorage + router store
```

A pragmatic build path: keep the existing Jinja chat as the `?legacy=1` fallback for one release while the React island is iterated; the React app is a single Vite-built bundle mounted at `/static/chat-studio/`. The current `templates/chat.html` shrinks to a 30-line shell (`<div id="chat-root">`).

---

## 12. CSS snippets (token-bound)

All tokens come from [`design.md` §2](./design.md). No hex literals. Themes work for free.

```css
/* Loader strip — full-width band beneath the header */
.loader-strip {
  display: flex;
  align-items: center;
  gap: var(--s-3);
  height: 36px;
  padding: 0 var(--s-5);
  background: var(--green-a08);
  border-bottom: 1px solid var(--green-a28);
  font-size: var(--fs-sm);
  color: var(--text-hi);
}
.loader-strip .bar {
  flex: 1;
  height: 4px;
  background: var(--surface2);
  border-radius: 2px;
  overflow: hidden;
}
.loader-strip .bar > i {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, var(--green), var(--blue));
  box-shadow: var(--glow-green);
  transition: width 0.4s ease;
}
.loader-strip .eta { color: var(--muted); font-variant-numeric: tabular-nums; }
.loader-strip .cancel {
  margin-left: var(--s-2);
  color: var(--red);
  background: transparent;
  border: 1px solid var(--red-a28);
  border-radius: var(--radius);
  padding: var(--s-1) var(--s-3);
  cursor: pointer;
}

/* Stats line under each response */
.stats-line {
  margin-top: var(--s-2);
  font-size: var(--fs-xs);
  color: var(--muted);
  letter-spacing: 0.02em;
  display: flex;
  flex-wrap: wrap;
  gap: var(--s-2);
  align-items: center;
}
.stats-line .seg { white-space: nowrap; }
.stats-line .seg.model {
  color: var(--text);
  cursor: pointer;
  border-bottom: 1px dotted var(--border-hi);
}
.stats-line .sep { color: var(--border-hi); }

/* Fit chip on model rows */
.fit-chip {
  font-size: var(--fs-xs);
  padding: 2px var(--s-2);
  border-radius: 999px;
  border: 1px solid currentColor;
  text-transform: lowercase;
}
.fit-chip.good      { color: var(--green); background: var(--green-a08); }
.fit-chip.marginal  { color: var(--amber); background: var(--amber-a08); }
.fit-chip.streaming { color: var(--blue);  background: var(--blue-a08); }

/* Tunable row scaffold */
.tunable {
  padding: var(--s-3) var(--s-4);
  border-bottom: 1px solid var(--border);
}
.tunable .name {
  display: flex;
  justify-content: space-between;
  font-weight: 600;
  color: var(--text-hi);
  font-size: var(--fs-md);
}
.tunable .desc { color: var(--muted); font-size: var(--fs-sm); margin-top: 2px; }
.tunable .impact {
  margin-top: var(--s-2);
  font-size: var(--fs-xs);
  color: var(--muted);
  display: flex;
  gap: var(--s-3);
}
.tunable .impact .arrow { color: var(--amber); }
.tunable .expert {
  cursor: help;
  border-bottom: 1px dotted var(--border-hi);
  color: var(--blue);
}

/* Dual composer — side-by-side mode */
.composer-dual.side-by-side {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--s-3);
}
.composer-dual .slot {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: var(--s-3);
  background: var(--surface);
}
.composer-dual .slot.secondary { border-left: 3px solid var(--purple); }
```

---

## 13. JSON fixtures

### Fixture: `/api/chat/models` (excerpt — what the React app expects)

```json
{
  "current": "mlx-community/Qwen3-8B-4bit",
  "gallery": {
    "installed": [
      {
        "id": "mlx-community/Qwen3-8B-4bit",
        "label": "Qwen3-8B-4bit",
        "runtime": "mlx-openai",
        "estimated_vram_gb": 4.9,
        "fit": { "verdict": "Requires streaming", "summary": "8 GB free VRAM, 14 GB needed" },
        "endpoint": "http://127.0.0.1:11435/v1",
        "spec": { "params": "8B", "context": 131072, "license": "Apache-2.0" }
      }
    ],
    "catalog": [],
    "runtime_counts": { "mlx-openai": 1, "ollama": 1, "mlx": 2 }
  },
  "compact": {
    "compute_sources": [
      { "id": "my_machine", "label": "Local",       "active": true,  "available": true  },
      { "id": "claude",     "label": "Claude",      "active": false, "available": false },
      { "id": "nvidia",     "label": "NVIDIA",      "active": false, "available": false },
      { "id": "openrouter", "label": "OpenRouter",  "active": false, "available": false },
      { "id": "huggingface","label": "HF",          "active": false, "available": false }
    ],
    "model_load": {
      "state": "ready", "blocking": false, "progress": 1.0, "eta_seconds": 0
    },
    "hardware": { "label": "Apple M5 Pro", "total_gb": 24.0, "free_gb": 4.2 }
  }
}
```

### Fixture: SSE event from `/api/chat/dual-stream`

```
event: token
data: {"slot":"primary","text":"Speculative ","tokens_so_far":1}

event: token
data: {"slot":"secondary","text":"In speculative ","tokens_so_far":1}

event: done
data: {"slot":"primary","stats":{"provider":"mlx","model":"Qwen3-8B-4bit","tps":18.4,"latency_ms":1873,"tokens":412}}

event: done
data: {"slot":"secondary","stats":{"provider":"claude","model":"claude-sonnet-4.6","tps":42.1,"latency_ms":612,"tokens":287},"compare":{"first_difference_at_token":4,"jaccard_top_k":0.71}}
```

### Fixture: `/api/chat/tunables/<model_id>` (PUT body)

```json
{
  "ctx": 16384,
  "temperature": 0.4,
  "top_p": 0.9,
  "top_k": 40,
  "repetition_penalty": 1.05,
  "max_tokens": 2048,
  "streaming_chunk": 4,
  "prefetch_depth": 3,
  "double_buffer": 2,
  "kv_cache_location": "gpu",
  "offload_strategy": "stream-hot",
  "quantization": "off",
  "batch_size": 1,
  "tokenization": "model",
  "stop": [],
  "safety": "light"
}
```

---

## 14. Migration plan (5 milestones)

| M | Scope | Exit |
|---|---|---|
| 1 | Stats line + provenance overlay on existing Jinja chat | every response shows the new line; click → overlay opens |
| 2 | Loader strip + blocking send + cancel; new ModelPicker dropdown | non-resident model triggers strip; transcript read-only during load |
| 3 | Right-sidebar drawer with Tunables + Logs; presets (Fast/Deep/Coder/Compare) | tunables persist per model; presets apply visibly |
| 4 | Dual composer + `/api/chat/dual-stream` backend | compare mode renders two responses + delta |
| 5 | Connections drawer + Fine-tune wizard | tokens stored encrypted; QLoRA smoke job runs from the wizard |

The legacy Jinja chat is retained behind `?legacy=1` for one release after M5.

---

## 15. PR checklist

- [ ] CSS uses **only** `design.md` tokens (no hex literals; verified by `scripts/check-no-hex.sh` in CI).
- [ ] Every new endpoint added to the OpenAPI surface; `ci/openapi-drift` green.
- [ ] Every new tunable: row in §4 table, doc anchor in `docs/tunables.md`, UI scaffold (label/desc/range/impact/expert/docs link).
- [ ] Loader strip uses the `LoadState` SSE schema verbatim — no shadow protocol.
- [ ] Stats-line transform passes the §2 unit-tests for all 5 namespace cases.
- [ ] Dual-stream tested with one local + one cloud model; compare summary populated.
- [ ] Tokens never logged (verified by `RedactionFilter` tests, see [`maximus §7`](./maximus.plan.md)).
- [ ] Theme-correct: page renders cleanly under `default` AND `laser-blue`.
- [ ] Playwright suite from §10 green.
- [ ] Performance acceptance numbers measured and committed under `bench/chat-studio/`.
- [ ] CHANGELOG entry under `## Unreleased` describing user-visible behavior.
- [ ] If touching the loader UI, cite `design.md §4 (progress-ring + step-context)` in the PR body.
