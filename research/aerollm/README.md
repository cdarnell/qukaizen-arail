# AeroLLM — Research & Optimization Workbench

> **Renamed.** AeroLLM became **QueueLLM** on 2026-08-21 — upstream is now
> [github.com/cdarnell/qukaizen-queuellm](https://github.com/cdarnell/qukaizen-queuellm),
> locally `~/ProJects/qukaizen-queuellm`. These five origin docs keep the name
> they were written under; read "AeroLLM" here as QueueLLM.

**Project name.** **AeroLLM** — the Rust runtime with MLX and CUDA
backends that Arail uses for deep inference. Multi-threaded prefetched
layer streaming optimized for concurrent research prompts.
Upstream: [github.com/cdarnell/qukaizen-aerollm](https://github.com/cdarnell/qukaizen-aerollm).

**What this is.** The engineering workbench for AeroLLM — the inference layer of Arail's distillation product. Layer streaming + batching is *how* we run frontier open-weights teachers on consumer hardware cheaply enough to make symbolic chain-of-thought distillation into task-specific SLMs economically viable.

**The product this serves.** See [`00-product-vision.md`](./00-product-vision.md). The one-liner: distill task-specific Small Language Models by running the world's largest LLMs as teachers on commodity hardware, using symbolic CoT traces filtered by a swarm of adversarial agents. Everything in this folder is in service of that.

**Backends.**

- **MLX (Apple Silicon).** Unified memory; the prefetcher overlaps NVMe reads with Metal compute. Primary daily-driver for the lab.
- **CUDA (Nvidia).** PCIe-bound; the prefetcher hides disk→host→VRAM latency behind compute. Same Rust core, different accel.

**Started.** 2026-04-18. Living docs — edit freely.

---

## The engineering thesis, short form

> **On a single consumer box, disk bandwidth is a hard ceiling for per-token latency on layer-streamed dense models — you cannot out-engineer physics. But the per-token cost is dominated by a one-time-per-layer load, so batching N prompts through the same layer pass yields near-linear N-way speedup in tokens/sec. That makes layer-streamed inference a terrible chat engine and an excellent *offline training-pair factory*. For distillation, a terrible chat engine is exactly what you want: latency is free, throughput-per-watt-per-dollar is the whole game.**

Evidence: verdagon.dev + the Arail infographic show **35.35 s/token single-prompt → 5.32 s/token at batch 50 → 4.85 s/token at batch 500 on a 70B model, 16 GB RAM, consumer laptop** — a 7.3× throughput gain with zero change to AeroLLM internals, just an application-level scheduler. See [`02-batching-strategy.md`](./02-batching-strategy.md).

Economic translation: at 4.85 s/token × batch 500 = ~100 tokens/sec aggregate. Over 24 hours that's ~8.6M teacher tokens/day from a single laptop. At ~500 tokens per training example (prompt + CoT + answer), that's ~17k training examples/day on a box we already own. Frontier-quality synthetic distillation corpora have historically required cloud GPU clusters. AeroLLM is the argument that they don't have to.

---

## How to use this folder

0. **[`00-product-vision.md`](./00-product-vision.md)** — *Start here.* The north-star doc. What we're actually building (SLM distillation), why AeroLLM + batching is the economic foundation, and how the rest of this folder ladders up.

1. **[`01-pipeline-map.md`](./01-pipeline-map.md)** — *If you're asking "which stage is slow?":* canonical pipeline map for the teacher inference stage. Every stage of a token's life, what it runs on (CUDA path vs MLX path), which knobs from `config/tuning.yml` and `config/tuning-mlx.yml` apply, and a column to fill in measured ms from your last run.

2. **[`02-batching-strategy.md`](./02-batching-strategy.md)** — *If you're asking "how do we get to millions of training tokens/day?":* the batching-first thesis re-framed for distillation workloads. The math (tokens/sec as a function of layer load time, compute per token per prompt, batch size), the memory wall at each hardware tier, continuous-batching-for-streamed-weights scheduler design, and the experiments to run first.

3. **[`03-parallel-work.md`](./03-parallel-work.md)** — *If you're asking "has someone already built this?":* competitive scan covering both sides. **Inference engines:** AeroLLM upstream, vLLM, SGLang, KTransformers, llama.cpp MoE offload, SwiftLM (MLX SSD streaming), Flash-MoE, HybriMoE, BlendServe, MoE-Gen, PIPO, MoE-SpeQ, DualPath. **Distillation & synthetic data:** SCoTD, Orca 2, Phi-4, Orca-AgentInstruct, Lion, GAD, Mentor-KD, Agent Distillation, Distribution-Aligned Sequence Distillation. Where our lane is clean on each axis.

4. **[`04-measurement-log.md`](./04-measurement-log.md)** — *If you just ran a sweep:* append one row per experiment. Schema matches `lab/data/aerollm-bench.jsonl` + `lab/data/mlx-bench.jsonl` so we can cross-reference. Includes both inference metrics (tokens/sec at batch N) and pipeline metrics (examples-per-day, swarm rejection rate, downstream student eval).

---

## Related docs

- [`github.com/cdarnell/qukaizen-aerollm`](https://github.com/cdarnell/qukaizen-aerollm) — upstream AeroLLM repo: Rust runtime, MLX + CUDA backends, optimization roadmap
- [`docs/tuning-loop.md`](../../docs/tuning-loop.md) — autoresearch supervisor loop
- [`research/1tb-inference-streaming.md`](../1tb-inference-streaming.md) — 1 TB-class model feasibility analysis (dense vs MoE, bandwidth ceilings, 20+ citations)
- [`config/tuning.yml`](../../config/tuning.yml) / [`config/tuning-mlx.yml`](../../config/tuning-mlx.yml) — the autoresearch-editable knob schemas

---

## Current open questions

Things this workbench is designed to answer. Each is a candidate experiment; measurements land in `04-measurement-log.md`. Since MLX is the primary track, MLX-shaped questions are listed first.

1. **Can AeroLLM load one frontier model at all on MLX?** This is the first binary gate for the MLX streaming track. Until `mlx_lm.load(...)` returns without OOM on DeepSeek-V3 / Kimi K2 / GLM-4.6 on a 24 GB M5, every other MLX question is hypothetical.
2. **On MLX (unified memory), does the batching math change?** No PCIe transfer. Does disk→unified remain the bottleneck so batching still wins, or does the load cost collapse and make batching less critical?
3. **What's the batch-size sweet spot on 16 / 24 / 96 / 512 GB unified memory?** Bounded above by KV cache + activation memory per prompt × N. Bounded below by where the per-token amortization curve flattens. For distillation, we want the highest N that doesn't crash — latency is free.
4. **Does AeroLLM's native CUDA code support concurrent prompts in a single layer pass?** (CUDA track.) Single-prompt per layer → a one-line forward-loop patch unlocks the full batching win. Multi-prompt-capable → what's the practical N limit? Answering this also tells us whether the batching scheduler is orthogonal to the backend, or whether each backend needs its own batching work.
5. **What's the token-output quality at batch 500 vs batch 1?** Batching shouldn't change output quality (each prompt is independent), but we need to verify — any numerical drift, cache-bleed, or sampler interaction would be fatal for a distillation corpus.
6. **What's the right scheduler shape for distillation workloads?** Not continuous batching in the vLLM sense — we have a fixed queue of seed prompts, no streaming arrivals. A pure offline batcher with adaptive N based on memory headroom is probably the right shape. Confirm.
7. **How close can a speculative-decoding draft model get the teacher while batching prompts in the background?** Probably irrelevant for pure distillation (we don't need interactivity), but interesting if we ever want to run a live "teach me" demo.
8. **Does teacher output quality on CoT-style prompts degrade under SSD streaming vs a same-model reference run?** Rare but possible numerical drift from quantization + streaming order. Must measure against a known-good cloud run on a held-out set.
