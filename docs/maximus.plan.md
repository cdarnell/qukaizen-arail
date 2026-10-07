# ARAIL — Build, Model Management & Fine-Tuning Pipeline

> **⚠ STATUS: DESIGN PLAN — NOT BUILT.** This document describes an intended
> model-management subsystem (`src/arail/models/`, `src/arail/jobs/`, a
> `scripts/arail-model` CLI, `/models`/`/jobs`/`/infer` endpoints). **None of
> that exists on disk yet** — do not go looking for those files. What ships
> today is: the persona-wrap default model, the CLI `scripts/build_ai_eng.sh`
> distillation pipeline, and the Nucleus-backed `/build` tab (which needs the
> separate `qukaizen-nucleus` pipeline running). For where real artifacts land,
> see [`models-on-disk.md`](./models-on-disk.md); for the shipped build
> surfaces, see the `/build` tab explainer. Keep this as the forward design.

> Implementation plan for the local model lifecycle: download → register → fit-check → load (full / streaming / hybrid) → infer → fine-tune → re-register. Owns the contract between QueueLLM, the QuKaizen swarm, and the portal UI.
>
> Companion docs: [`design.md`](./design.md) (visual contract for the loader UI), [`tuning-loop.md`](./tuning-loop.md) (chat-side speed work), [`agents.md`](./agents.md) (orchestrator agents).

---

## 0. Design principles

1. **Local first, cloud opt-in.** Every primary path runs offline; cloud providers (Claude / NVIDIA / OpenRouter / HF) are pluggable adapters behind the same router contract.
2. **Explicit over magical.** Streaming offload, sharding, and quantization are user choices surfaced in the UI — never auto-applied to frontier models without consent.
3. **Block, don't fake.** UI surfaces don't show a model as "ready" until it's resident. ETA is a first-class field, not a guess.
4. **Reproducible artifacts.** Every fine-tune produces a manifest: input model SHA, dataset SHA, hyperparams, env hash, git commit, eval scorecard. Loadable by ID forever.
5. **Secrets are encrypted at rest and never logged.** HF tokens, OpenAI keys, etc. live in an age-encrypted store; redaction in all log paths.
6. **One contract.** All long-running work (download, load, fine-tune) shares the same Job model with the same status-stream protocol (SSE today, WebSocket if/when needed).

---

## 1. Architecture at a glance

```
┌──────────────────────────────────────────────────────────────────────┐
│                          ARAIL Portal (FastAPI)                     │
│  ─────────────────────────────────────────────────────────────────  │
│  /models  /jobs  /tokens  /infer  /telemetry  /admin                │
└────────────────┬───────────────┬──────────────────┬─────────────────┘
                 │               │                  │
                 ▼               ▼                  ▼
        ┌────────────────┐ ┌─────────────┐  ┌──────────────────┐
        │ ModelRegistry  │ │   Router    │  │   JobOrchestrator│
        │  (FS watcher)  │ │ (adapters)  │  │  (queue + worker)│
        └───────┬────────┘ └──────┬──────┘  └────┬─────────────┘
                │                 │              │
                ▼                 ▼              ▼
        ┌─────────────────┐  ┌──────────────────────────────────┐
        │ /models/local/  │  │  Workers (subprocess / k8s pod)  │
        │ /models/finetuned│  │  • download_worker              │
        └─────────────────┘  │  • loader_worker  (QueueLLM API)  │
                             │  • finetune_worker (fsdp_qlora) │
                             │  • eval_worker                   │
                             └─────────┬───────────────────────┘
                                       ▼
                                 ┌──────────┐
                                 │  QueueLLM  │ ← Rust inference
                                 │   /HF py  │   layer-streaming
                                 └──────────┘
```

Single FastAPI process exposes the API + serves the portal. Workers are subprocesses on a single-node dev box, k8s pods in production. State (jobs, registry, tokens) is in SQLite for dev, Postgres for prod — accessed through a thin repository layer.

---

## 2. Repository layout (new + extended)

```
src/arail/
├── models/
│   ├── __init__.py
│   ├── registry.py        # FS watcher + DB-backed registry
│   ├── manifest.py        # Pydantic schema for ModelManifest
│   ├── fit.py             # Fit estimator (VRAM/RAM/NVMe math)
│   ├── loader.py          # Loader orchestrator + state machine
│   ├── download.py        # HF download w/ checksum + license gate
│   └── adapters/
│       ├── base.py        # Provider protocol
│       ├── aerollm.py     # Local Rust backend (primary)
│       ├── hf.py          # Transformers fallback
│       ├── claude.py
│       ├── nvidia.py
│       └── openrouter.py
├── jobs/
│   ├── __init__.py
│   ├── orchestrator.py    # Queue, scheduler, status streams
│   ├── worker_proto.py    # Worker subprocess protocol
│   ├── workers/
│   │   ├── download.py
│   │   ├── load.py
│   │   ├── finetune.py    # QLoRA / LoRA via fsdp_qlora
│   │   └── eval.py
│   └── artifacts.py       # Reproducible manifest writer
├── tokens/
│   ├── store.py           # age-encrypted secret store
│   ├── rbac.py
│   └── api.py
├── telemetry/
│   ├── metrics.py         # prometheus_client
│   └── tracing.py
└── portal/
    └── routers/
        ├── models.py
        ├── jobs.py
        ├── tokens.py
        ├── infer.py
        └── telemetry.py

scripts/
├── arail-model            # CLI helper (download/list/inspect)
├── arail-job              # CLI helper (submit/watch/cancel)
└── seed-test-model.sh

deploy/
├── docker/
│   ├── Dockerfile.portal
│   ├── Dockerfile.finetune-worker
│   └── Dockerfile.loader-worker
├── compose/
│   └── docker-compose.yml
├── helm/
│   └── arail/
│       ├── Chart.yaml
│       ├── values.yaml
│       └── templates/
└── grafana/
    └── dashboards/

openapi/
└── arail.openapi.yaml     # generated + hand-curated examples
```

---

## 3. Model registry & auto-detect

### Data model

```python
# src/arail/models/manifest.py
from pydantic import BaseModel
from datetime import datetime
from typing import Literal, Optional

ModelFormat = Literal["safetensors", "gguf", "bnb-4bit", "awq", "exl2"]
ModelKind   = Literal["base", "instruct", "chat", "finetuned", "lora-adapter"]

class ModelManifest(BaseModel):
    id: str                          # uuid
    name: str                        # llama-3.1-70b-instruct-q4
    hf_repo: Optional[str]           # meta-llama/Meta-Llama-3.1-70B-Instruct
    revision: Optional[str]          # commit sha
    kind: ModelKind
    format: ModelFormat
    quantized: bool
    bits: Optional[int]              # 4, 8, 16
    param_count: int                 # in billions * 1e9
    fp16_bytes: int                  # estimated full-precision footprint
    on_disk_bytes: int               # actual size on disk
    required_vram_full_gb: float
    required_vram_streaming_gb: float
    license: str                     # apache-2.0, llama3-community, custom
    license_accepted: bool
    license_accepted_by: Optional[str]
    license_accepted_at: Optional[datetime]
    sha256: str                      # of weights manifest, for integrity
    path: str                        # /models/local/<name>
    registered_at: datetime
    source: Literal["hf", "manual", "finetune"]
    finetune_parent_id: Optional[str]  # if source==finetune
    eval_scorecard: Optional[dict]     # populated by eval_worker
    tags: list[str]
```

### FS watcher

`registry.py` runs a `watchdog.Observer` on `MODELS_LOCAL` and `MODELS_FINETUNED` (configurable via `ARAIL_MODELS_DIR`). On any new directory containing a recognized weights file (`*.safetensors`, `*.gguf`, `model.bin`), it:

1. Hashes the weight files (chunked, async — emits progress event).
2. Reads any sibling `arail.manifest.json` (preferred) or infers metadata from `config.json` + filename heuristics.
3. Inserts/updates a row in `models` table.
4. Emits `model.registered` event on the internal event bus → SSE clients refresh.

Manual ingestion uses the same path (`POST /models/register` just drops a marker file and waits for the watcher to pick it up — single source of truth).

### Endpoints

```yaml
# openapi excerpt
/models:
  get:
    summary: List registered models
    parameters: [ {name: kind, in: query}, {name: q, in: query} ]
    responses: { "200": { content: { application/json: { schema: { type: array, items: { $ref: "#/components/schemas/ModelManifest" } } } } } }
  post:
    summary: Register a model already on disk
    requestBody: { content: { application/json: { schema: { $ref: "#/components/schemas/RegisterRequest" } } } }
    responses: { "201": { content: { application/json: { schema: { $ref: "#/components/schemas/ModelManifest" } } } } }
/models/{id}:
  get:    { responses: { "200": { ... } } }
  delete: { summary: Unregister + delete weights, responses: { "202": { ... } } }
/models/{id}/accept_license:
  post:
    summary: Mark license accepted (verifies HF_TOKEN against gated repo)
    responses: { "200": { ... }, "403": { description: token lacks license access } }
```

---

## 4. Model download CLI

```bash
# scripts/arail-model — usage
arail model list
arail model search llama-3.1
arail model download meta-llama/Meta-Llama-3.1-70B-Instruct \
    --dest /models/local/llama-3.1-70b-instruct \
    --revision main \
    --format safetensors \
    --quant none

# Gated repo flow
arail model download meta-llama/Meta-Llama-3.1-70B-Instruct
# → stderr: "Gated repository. Have you accepted the license at <url>? [y/N]"
# → on y, verifies HF_TOKEN can list files, otherwise --login flow

arail model verify <id>          # rehash + compare to manifest
arail model rm <id>              # confirms then deletes
```

### Implementation sketch

```python
# src/arail/models/download.py
async def download(
    hf_repo: str,
    dest: Path,
    revision: str = "main",
    token: Optional[str] = None,
    on_progress: Callable[[Progress], Awaitable[None]] = None,
) -> ModelManifest:
    api = HfApi(token=token or token_store.get("hf"))

    # Preflight: license gate
    try:
        info = api.model_info(hf_repo, revision=revision)
    except GatedRepoError:
        raise LicenseRequiredError(hf_repo, license_url=...)

    # Disk preflight
    needed = sum(f.size for f in info.siblings)
    free = shutil.disk_usage(dest.parent).free
    if needed > free * 0.9:
        raise InsufficientDiskError(needed, free)

    # ETA based on a 5-second NVMe probe + HF mirror RTT.
    eta = estimate_download_eta(needed)
    await on_progress(Progress(stage="download", eta=eta, bytes_total=needed))

    # snapshot_download w/ resume + chunked sha-256
    snapshot_download(
        repo_id=hf_repo, revision=revision, local_dir=dest,
        local_dir_use_symlinks=False, token=api.token,
    )

    sha = await hash_dir(dest, on_chunk=on_progress)
    manifest = build_manifest(dest, hf_repo, revision, sha)
    write_arail_manifest(dest / "arail.manifest.json", manifest)
    return manifest  # FS watcher picks it up and registers
```

Checksums: per-file SHA-256 from HF metadata API verified, then a single rolling SHA over the sorted `(filename, file_sha)` list stored as the manifest's `sha256` (so re-hashing one file rather than every byte verifies integrity).

---

## 5. Fit estimator & loader orchestrator

### Fit estimator

```python
# src/arail/models/fit.py
@dataclass
class SystemSnapshot:
    gpus: list[GPUInfo]              # name, total_vram, free_vram
    ram_total_gb: float
    ram_free_gb: float
    nvme_throughput_mbps: float      # from rolling probe

@dataclass
class FitReport:
    verdict: Literal["good", "marginal", "streaming", "infeasible"]
    primary_strategy: Literal["full", "shard", "stream-hot-pinned", "stream-all"]
    estimated_load_seconds: float
    headroom_gb: float
    notes: list[str]

def fit(model: ModelManifest, sys: SystemSnapshot, ctx_tokens: int = 8192) -> FitReport:
    kv_cache = kv_bytes(model, ctx_tokens)
    weights  = model.required_vram_full_gb * 1024**3
    overhead = 1.5 * 1024**3       # CUDA, activations
    needed   = weights + kv_cache + overhead
    free     = max((g.free_vram_gb for g in sys.gpus), default=0) * 1024**3

    if free >= needed * 1.1:
        return FitReport("good", "full", weights / sys.nvme_throughput_mbps / (1024**2 / 1e6), free - needed)
    if sum(g.free_vram_gb for g in sys.gpus) * 1024**3 >= needed:
        return FitReport("good", "shard", ..., ...)
    if free >= (kv_cache + overhead + 0.3 * weights):
        return FitReport("marginal", "stream-hot-pinned", ..., ...)
    if sys.ram_free_gb * 1024**3 >= weights:
        return FitReport("streaming", "stream-all", weights / sys.nvme_throughput_mbps, 0,
                         ["full streaming via NVMe — expect 2-4× latency"])
    return FitReport("infeasible", "stream-all", float("inf"), 0,
                     ["model exceeds RAM+VRAM; quantize or shrink context"])
```

### Loader state machine

**What actually ships today (`/api/chat/model-load`, `arail/portal/app.py`):**
a four-state machine, `idle → loading → ready | error` — no `canceled`
terminal state. The load runs in a non-cancellable `asyncio.to_thread`
(the executor can't kill it once started), so Cancel is honest absence
rather than a fake interrupt, and a wall-clock timeout
(`ARAIL_LOAD_MAX_SEC`) flips the *reported* state to `error` without
actually stopping the background thread — the in-flight lock stays held
until it genuinely settles, so a timeout can't be followed by a second,
doubly-resident load. See
`sprints/2026-07-20-model-ux-unification/ARCHITECTURE.md` §C6 for the
full contract (concurrency, model-identity refusal, real-bytes ETA,
click-time re-fit, friendly errors) and its Phase 0b build log.

**The six/seven-state design below is a future plan, not shipped
behavior.** It described a fuller job/SSE-driven loader
(`/models/{id}/load`, `/models/{id}/status/stream`) with sharding,
multi-GPU assignment, and a progress-ring UI — none of which exists in
`src/` today. Presenting it without that caveat previously implied a
richer, resumable-progress loader than what's actually live; trimmed
here so this doc doesn't outrun the code (the six/seven-state machine
was never built — do not treat it as documenting current behavior):

```
unloaded ─► loading-prep ─► loading-weights ─► warming ─► resident
                │                │                │
                ▼                ▼                ▼
            failed           failed           failed
                                                 │
                                                 ▼ (idle timeout / unload)
                                            unloading ─► unloaded
```

Each transition would emit a `loader.state` event with: state, progress (0..1), bytes_loaded, eta_seconds, strategy, gpu_assignment. The portal's loader UI (per `design.md` §4 — `progress-ring` + `step-context`) would subscribe via SSE and **block the chat input** until `resident`. This remains the long-term direction for a job-queue-backed, multi-GPU loader; the endpoints below are that future design, not a current API surface.

### Endpoints (future design — not implemented)

```yaml
/models/{id}/load:
  post:
    requestBody:
      content:
        application/json:
          schema:
            type: object
            properties:
              strategy:    { type: string, enum: [auto, full, shard, stream-hot-pinned, stream-all] }
              ctx_tokens:  { type: integer, default: 8192 }
              gpus:        { type: array, items: { type: integer } }
    responses:
      "202": { description: load job started, content: { application/json: { schema: { $ref: "#/components/schemas/Job" } } } }
      "409": { description: another model resident on requested GPUs }
/models/{id}/status:
  get:
    responses:
      "200":
        content:
          application/json:
            schema:
              type: object
              properties:
                state:        { type: string }
                progress:     { type: number, minimum: 0, maximum: 1 }
                eta_seconds:  { type: number, nullable: true }
                strategy:     { type: string }
                resident_on:  { type: array, items: { type: integer } }
/models/{id}/status/stream:
  get:
    summary: SSE — state transitions until resident or failed
    responses: { "200": { content: { text/event-stream: {} } } }
/models/{id}/unload:
  post:
    responses: { "202": { ... } }
```

---

## 6. Fine-tuning factory

### Job schema

```python
# src/arail/jobs/orchestrator.py
class FinetuneRequest(BaseModel):
    base_model_id: str
    method: Literal["qlora", "lora", "full"]
    dataset: DatasetRef             # local path or hf_id
    hyperparams: Hyperparams        # epochs, lr, batch, lora_r, lora_alpha, target_modules
    target_gpus: list[int]
    output_name: str
    eval: EvalConfig                # prompts, perplexity_set, sample_count
    seed: int = 42

class JobStatus(BaseModel):
    id: str
    kind: Literal["download", "load", "finetune", "eval"]
    state: Literal["queued", "preflight", "running", "evaluating", "succeeded", "failed", "cancelled"]
    progress: float
    started_at: datetime | None
    finished_at: datetime | None
    artifact_id: str | None
    log_tail: list[str]
    metrics: dict                   # loss, lr, throughput, gpu_util
```

### Worker container

```dockerfile
# deploy/docker/Dockerfile.finetune-worker
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y python3.11 python3-pip git git-lfs && \
    git lfs install
RUN pip install --no-cache-dir \
      torch==2.4.* \
      transformers==4.44.* \
      bitsandbytes==0.43.* \
      peft==0.12.* \
      accelerate==0.33.* \
      datasets==2.20.* \
      "fsdp_qlora @ git+https://github.com/AnswerDotAI/fsdp_qlora@v0.2"
COPY src/arail /app/arail
WORKDIR /app
ENTRYPOINT ["python", "-m", "arail.jobs.workers.finetune"]
```

### Preflight checks (run before queue accepts the job)

| Check | Action on fail |
|---|---|
| Disk free ≥ 3× LoRA estimated bytes | reject with `INSUFFICIENT_DISK` |
| Sum(target_gpus.free_vram) ≥ qlora estimate | reject with `INSUFFICIENT_VRAM` |
| Dataset readable + ≤ 10 GB (configurable) + format detected (jsonl/parquet/csv) | reject with `BAD_DATASET` |
| Base model registered + license accepted | reject with `LICENSE_NOT_ACCEPTED` |
| User RBAC has `jobs:submit:finetune` | 403 |
| Total queued bytes for user ≤ quota | reject with `QUOTA` |

### Reproducibility manifest

Every successful run writes `/models/finetuned/<output_name>/arail.manifest.json` containing:

- base model SHA + manifest
- dataset SHA (computed during preflight; large datasets get a sample sha)
- hyperparams (canonical JSON)
- env hash: `sha256(pip freeze + nvidia-smi -q + cuda_version + torch.version.git_version)`
- git commit of ARAIL repo at submit time
- training metrics curve (loss, lr, throughput) as parquet
- eval scorecard (see §6.4)
- worker container image digest

The same manifest is written into the model registry — opening the model in the UI shows the full lineage.

### Eval hooks

After training, `eval_worker` runs:

1. **Perplexity** on a held-out validation slice (always).
2. **Sample prompts** (configurable; defaults pulled from `arail/jobs/eval_prompts.yaml`) — runs through both base and tuned model, diffs side by side.
3. **Smoke chat** — 3-turn conversation, records latency.
4. Writes `eval_scorecard` to manifest. UI surfaces a Δ‑perplexity badge in the model picker.

### CLI

```bash
arail job submit finetune \
  --base llama-3.1-8b-instruct \
  --method qlora \
  --dataset /data/customer-support.jsonl \
  --epochs 3 --lr 2e-4 --lora-r 16 --lora-alpha 32 \
  --gpus 0,1 \
  --output llama-3.1-8b-cs-v1

arail job watch <job-id>          # streams logs + progress
arail job cancel <job-id>
arail job ls --kind finetune --since 24h
```

---

## 7. Credentials & secrets

- **Backing store:** age-encrypted file (`~/.arail/secrets.age`) with a key generated at first run, OS-keyring-protected. Production deployment swaps for HashiCorp Vault or k8s `Secret` mounts via the same `TokenStore` interface.
- **Never logged:** every log handler runs through a `RedactionFilter` that masks any string matching the live token set.
- **In-process cache** keyed by token-id with TTL 5 min; hot path doesn't decrypt every call.

```yaml
/tokens/hf:
  post:
    summary: Store HF token (verifies against /api/whoami first)
    requestBody:
      content:
        application/json: { schema: { type: object, properties: { token: { type: string } }, required: [token] } }
    responses:
      "201": { content: { application/json: { schema: { $ref: "#/components/schemas/TokenStatus" } } } }
      "401": { description: token rejected by HF }
/tokens/hf/status:
  get:
    responses:
      "200":
        content:
          application/json:
            schema:
              type: object
              properties:
                present:        { type: boolean }
                username:       { type: string, nullable: true }
                gated_repos:    { type: array, items: { type: string } }
                last_verified:  { type: string, format: date-time }
/tokens/{name}:
  delete: { responses: { "204": {} } }
```

### RBAC

Three baked-in roles; extensible via config:

| Role | Permissions |
|---|---|
| `viewer` | `models:read`, `jobs:read`, `telemetry:read` |
| `operator` | viewer + `models:load/unload`, `jobs:submit`, `tokens:read-status` |
| `admin` | operator + `tokens:write`, `models:delete`, `jobs:cancel-others`, `rbac:write` |

Local single-user mode auto-binds the local OS user to `admin`. Multi-user mode requires an OIDC provider (config: `ARAIL_OIDC_ISSUER`).

---

## 8. Router & provider abstraction

```python
# src/arail/models/adapters/base.py
class ProviderAdapter(Protocol):
    name: str

    async def status(self, model_id: str) -> AdapterStatus: ...
    async def load(self, model_id: str, opts: LoadOpts) -> AsyncIterator[LoadEvent]: ...
    async def unload(self, model_id: str) -> None: ...
    async def infer(self, req: InferRequest) -> InferResponse: ...
    async def stream(self, req: InferRequest) -> AsyncIterator[Token]: ...
```

### Routing rules

```yaml
/infer:
  post:
    summary: Run inference with routing policy
    requestBody:
      content:
        application/json:
          schema:
            type: object
            required: [messages]
            properties:
              messages:   { type: array, items: { $ref: "#/components/schemas/Message" } }
              policy:
                type: object
                properties:
                  primary:   { type: string, example: "local:llama-3.1-70b-instruct" }
                  secondary: { type: string, nullable: true, example: "claude:claude-sonnet-4.6" }
                  mode:      { type: string, enum: [primary-only, fallback, compare] }
              params:
                type: object
                properties:
                  max_tokens: { type: integer }
                  temperature:{ type: number }
                  stream:     { type: boolean, default: true }
    responses:
      "200":
        content:
          application/json: { schema: { $ref: "#/components/schemas/InferResponse" } }
          text/event-stream: {}
```

Provenance metadata returned on every response:

```json
{
  "provider": "local",
  "model_id": "llama-3.1-70b-instruct",
  "strategy": "stream-hot-pinned",
  "compare_with": { "provider": "claude", "model_id": "claude-sonnet-4.6", "diff_summary": "..." },
  "latency_ms": { "ttft": 412, "total": 2890 },
  "tokens": { "prompt": 1284, "completion": 542 }
}
```

---

## 9. OpenAPI spec

Source of truth lives at `openapi/arail.openapi.yaml`. Generated from FastAPI annotations (`app.openapi()`) **and** hand-curated for examples + descriptions; CI fails if generated diff drifts from committed file (forces conscious updates).

Key sections — full file is too large to inline; structure:

```yaml
openapi: 3.1.0
info:
  title: ARAIL
  version: 0.4.0
  description: A rail gun for AI — local model lifecycle, fine-tuning, routing.
servers:
  - url: http://arail.local
security:
  - bearerAuth: []
components:
  securitySchemes:
    bearerAuth: { type: http, scheme: bearer }
  schemas:
    ModelManifest:    { ... }
    FitReport:        { ... }
    LoadOpts:         { ... }
    Job:              { ... }
    JobStatus:        { ... }
    FinetuneRequest:  { ... }
    InferRequest:     { ... }
    InferResponse:    { ... }
    TokenStatus:      { ... }
paths:
  /models:                         { get, post }
  /models/{id}:                    { get, delete }
  /models/{id}/accept_license:     { post }
  /models/{id}/load:               { post }
  /models/{id}/status:             { get }
  /models/{id}/status/stream:      { get }
  /models/{id}/unload:             { post }
  /jobs:                           { get, post }
  /jobs/finetune:                  { post }
  /jobs/{id}:                      { get, delete }
  /jobs/{id}/logs:                 { get }
  /jobs/{id}/stream:               { get }
  /tokens/hf:                      { post }
  /tokens/hf/status:               { get }
  /tokens/{name}:                  { delete }
  /infer:                          { post }
  /telemetry/metrics:              { get }
  /admin/health:                   { get }
```

API contract tests (`pytest --schemathesis openapi/arail.openapi.yaml`) run in CI against a real (mocked-storage) instance.

---

## 10. CI/CD

### `.github/workflows/ci-test.yml`

```yaml
name: ci/test
on:
  pull_request:
  push: { branches: [main] }
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install ruff mypy
      - run: ruff check .
      - run: mypy src/arail
  unit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -e ".[test]"
      - run: pytest -q --cov=arail --cov-fail-under=80
  integration:
    needs: [lint, unit]
    runs-on: ubuntu-latest
    services:
      arail:
        image: arail/portal:ci
        ports: ["8000:8000"]
        env:
          ARAIL_MODELS_DIR: /tmp/models
          ARAIL_FAKE_GPU: "1"
    steps:
      - uses: actions/checkout@v4
      - run: pip install schemathesis httpx pytest
      - run: pytest tests/integration -q
      - run: schemathesis run openapi/arail.openapi.yaml --base-url http://localhost:8000
  openapi-drift:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -e .
      - run: python -m arail.tools.dump_openapi > openapi/arail.openapi.generated.yaml
      - run: diff -u openapi/arail.openapi.yaml openapi/arail.openapi.generated.yaml
```

### `.github/workflows/ci-deploy.yml`

```yaml
name: ci/deploy
on:
  push:
    tags: ["v*"]
jobs:
  build-images:
    runs-on: ubuntu-latest
    permissions: { packages: write }
    strategy:
      matrix:
        component: [portal, loader-worker, finetune-worker]
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v6
        with:
          context: .
          file: deploy/docker/Dockerfile.${{ matrix.component }}
          push: true
          tags: |
            ghcr.io/${{ github.repository }}/${{ matrix.component }}:${{ github.ref_name }}
            ghcr.io/${{ github.repository }}/${{ matrix.component }}:latest
          cache-from: type=gha
          cache-to:   type=gha,mode=max
  helm-publish:
    needs: build-images
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: azure/setup-helm@v4
      - run: helm package deploy/helm/arail
      - run: helm push arail-*.tgz oci://ghcr.io/${{ github.repository }}/charts
```

### `deploy/compose/docker-compose.yml` (single-node dev)

```yaml
version: "3.9"
services:
  portal:
    image: ghcr.io/qukaizen/arail/portal:latest
    ports: ["8000:8000"]
    environment:
      ARAIL_MODELS_DIR: /models
      ARAIL_DB_URL: sqlite:///data/arail.db
    volumes:
      - ./data:/data
      - ./models:/models
      - /var/run/docker.sock:/var/run/docker.sock   # to spawn workers
    depends_on: [prometheus]
  finetune-worker:
    image: ghcr.io/qukaizen/arail/finetune-worker:latest
    deploy:
      resources:
        reservations:
          devices: [{ driver: nvidia, count: all, capabilities: [gpu] }]
    volumes:
      - ./models:/models
      - ./data:/data
    profiles: ["gpu"]   # docker compose --profile gpu up
  prometheus:
    image: prom/prometheus:latest
    ports: ["9090:9090"]
    volumes: [ "./prometheus.yml:/etc/prometheus/prometheus.yml" ]
  grafana:
    image: grafana/grafana:latest
    ports: ["3000:3000"]
    volumes: [ "./grafana/dashboards:/var/lib/grafana/dashboards" ]
```

### Helm (production)

`deploy/helm/arail/values.yaml` exposes:

```yaml
portal: { replicas: 2, resources: {...} }
finetuneWorker:
  enabled: true
  nodeSelector: { nvidia.com/gpu.present: "true" }
  resources: { limits: { nvidia.com/gpu: 1 } }
storage:
  models: { storageClass: nvme-fast, size: 4Ti }
  data:   { storageClass: standard, size: 100Gi }
secrets:
  hfTokenSecretName: arail-hf-token
oidc: { issuer: "", clientId: "" }
```

---

## 11. Monitoring & telemetry

### Metrics (Prometheus, exposed at `/telemetry/metrics`)

| Metric | Type | Labels |
|---|---|---|
| `arail_model_load_seconds` | Histogram | model_id, strategy |
| `arail_model_resident` | Gauge | model_id, gpu |
| `arail_prefetch_hit_ratio` | Gauge | model_id |
| `arail_gpu_util_ratio` | Gauge | gpu, model_id |
| `arail_gpu_vram_used_bytes` | Gauge | gpu |
| `arail_nvme_throughput_mbps` | Gauge | device |
| `arail_job_queue_length` | Gauge | kind |
| `arail_job_duration_seconds` | Histogram | kind, state |
| `arail_finetune_loss` | Gauge | job_id, step |
| `arail_infer_ttft_ms` | Histogram | provider, model_id |
| `arail_infer_tokens_per_second` | Histogram | provider, model_id |
| `arail_license_accept_total` | Counter | model_id |
| `arail_token_redactions_total` | Counter | (sanity check) |

### Grafana dashboards (committed JSON)

1. **Loader** — load times, resident heatmap, prefetch hit-rate.
2. **Fine-tune factory** — queue, loss curves, GPU utilization per job.
3. **Inference** — TTFT / tokens-per-second by provider, fallback rate.
4. **Storage & I/O** — NVMe throughput, models dir size, dataset cache size.

### Tracing

OpenTelemetry SDK with OTLP exporter; spans for `download`, `load.transition`, `finetune.step`, `infer.request`. Traces linked to job IDs so the UI's job detail panel can render the trace inline.

---

## 12. Security & safety

- **License gate.** `download` refuses gated repos until `accept_license` returns OK; UI shows the upstream license URL and the user's accept-record.
- **Dataset validation.** `finetune.preflight` checks: file types in allowlist (jsonl/parquet/csv/txt), total size ≤ `ARAIL_DATASET_MAX_GB` (default 10), per-row schema validation, and a profanity/PII flag (warn-only by default; configurable to block).
- **Sandboxed workers.** Workers run in containers with `--read-only` root FS, `--cap-drop=ALL`, `--security-opt no-new-privileges`, and only the `/models` + `/data` volumes mounted. Network egress restricted to HF + configured registries.
- **Token redaction.** A `logging.Filter` scans every log record's message + args against the in-memory token set; matches are replaced with `***[hf_token]***`-style placeholders.
- **RBAC default deny.** All endpoints require explicit role mapping; the OpenAPI spec carries `x-arail-required-role` extensions and CI verifies handlers enforce them.
- **Audit log.** Every state-changing call (token write, license accept, model delete, job submit) writes an append-only NDJSON audit record signed with the instance's age key.

---

## 13. Acceptance tests

### Integration (Python / pytest)

```python
# tests/integration/test_lifecycle.py
@pytest.mark.integration
async def test_register_load_infer_unload(client, fake_gpu):
    seed_test_model("/tmp/models/tiny-llama")          # 100M-param toy
    await wait_until(lambda: client.get("/models").json(), key="name", value="tiny-llama")

    r = client.post("/models/tiny-llama/load", json={"strategy": "full"})
    job = r.json()
    await stream_until(client, f"/jobs/{job['id']}/stream", state="succeeded")

    r = client.post("/infer", json={
        "messages": [{"role": "user", "content": "hello"}],
        "policy":   {"primary": "local:tiny-llama", "mode": "primary-only"},
        "params":   {"stream": False, "max_tokens": 8},
    })
    assert r.status_code == 200 and r.json()["provenance"]["model_id"] == "tiny-llama"

    client.post("/models/tiny-llama/unload").raise_for_status()
```

### Fine-tune smoke

```python
# tests/integration/test_finetune_smoke.py
@pytest.mark.integration
async def test_qlora_smoke(client, gpu_required):
    r = client.post("/jobs/finetune", json={
        "base_model_id": "tiny-llama",
        "method": "qlora",
        "dataset": {"type": "local", "path": "tests/fixtures/toy.jsonl"},
        "hyperparams": {"epochs": 1, "lr": 2e-4, "lora_r": 4, "lora_alpha": 8,
                        "target_modules": ["q_proj", "v_proj"]},
        "target_gpus": [0],
        "output_name": "tiny-llama-toy-v1",
        "eval": {"perplexity_set": "tests/fixtures/toy-eval.jsonl", "sample_count": 3},
    })
    job = r.json()
    final = await stream_until(client, f"/jobs/{job['id']}/stream", state="succeeded", timeout=600)
    assert final["artifact_id"]
    m = client.get(f"/models/{final['artifact_id']}").json()
    assert m["finetune_parent_id"] == "tiny-llama"
    assert m["eval_scorecard"]["perplexity"] < 1e6   # sanity, not quality
```

### Contract tests

Schemathesis runs against `openapi/arail.openapi.yaml`; failures break CI. The OpenAPI generated-vs-committed diff job (above) prevents silent drift.

---

## 14. Operator playbook (excerpt)

```
SCENARIO: Model load stuck > 5 min
  1. arail job watch <load-job-id>          → look at last log line
  2. Check Grafana → Loader → "load times"  → outliers vs baseline
  3. If NVMe throughput collapsed: `arail telemetry probe nvme`
  4. If GPU lost: `nvidia-smi` from inside loader-worker container
  5. arail model unload <id>                → reset state
  6. arail model load <id> --strategy=stream-all   → fallback path

SCENARIO: Fine-tune OOM at step N
  1. arail job logs <id> | tail -200
  2. Grafana → Fine-tune → "GPU VRAM" panel for the job
  3. Re-submit with: --batch-size halved, --grad-accum doubled, --lora-r reduced
  4. If still OOM: switch base to a lower-bit quant or shrink ctx_tokens

SCENARIO: HF token rotated
  1. arail tokens delete hf
  2. arail tokens set hf  → paste new token; CLI verifies via /tokens/hf
  3. Re-run any failed downloads — they pick up the new token automatically
```

---

## 15. Rollout phases

| Phase | Scope | Exit criteria |
|---|---|---|
| **0 — scaffolding** | Repo layout, manifest schema, registry FS-watcher, CLI shells | `arail model list` returns seeded models; tests pass |
| **1 — download + load (full only)** | HF download w/ license gate, full-load via QueueLLM, SSE status, UI block-until-resident | tiny-llama lifecycle e2e green |
| **2 — fit estimator + streaming** | Fit math, streaming + hybrid loaders, strategy chooser UI | 70B model loads on a 24GB card via stream-hot-pinned |
| **3 — fine-tune factory** | Job orchestrator, finetune worker, eval hooks, artifact registration | QLoRA smoke test green; manifest reproducible |
| **4 — router + adapters** | Provider abstraction, /infer with primary/fallback/compare, provenance | local + Claude side-by-side compare in chat UI |
| **5 — telemetry + ops** | Prometheus, Grafana dashboards, tracing, audit log, RBAC | Operator playbook validated against staging |
| **6 — multi-user + helm** | OIDC, k8s/Helm chart, prod secrets backend | First non-local-user successfully fine-tunes |

---

## 16. PR checklist

For any PR touching the model/job/infer surfaces, the reviewer requires:

- [ ] OpenAPI spec updated (`openapi/arail.openapi.yaml`); `ci/openapi-drift` is green.
- [ ] New endpoints have `x-arail-required-role` and unit tests asserting the RBAC check.
- [ ] Long-running operations emit `Job` events compatible with the SSE `/jobs/{id}/stream` schema.
- [ ] Any new persisted artifact writes an `arail.manifest.json` with full lineage (base model SHA, dataset SHA, env hash, git commit).
- [ ] No raw token values reach `logging` — verified by the `RedactionFilter` test.
- [ ] Grafana dashboard JSON updated if a new metric was added; metric name follows `arail_*` convention.
- [ ] Operator playbook updated for any new failure mode.
- [ ] Integration test added (or extended) covering the happy path AND one failure path.
- [ ] CHANGELOG entry under `## Unreleased` describing user-visible behavior.
- [ ] Touches the loader/finetune UI? Cite the relevant `design.md` section in the PR body.
