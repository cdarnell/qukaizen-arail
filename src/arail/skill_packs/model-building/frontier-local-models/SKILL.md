---
title: Frontier models, running locally
id: frontier-local-models
name: Frontier Local Models
domain: ai
version: 1.0.0
tags: [skill, frontier, aerollm, local-inference, open-models]
when_to_use:
  - When reasoning about which deep model to pick for QueueLLM
  - When the task deserves frontier-tier intelligence and can tolerate slow throughput
  - When documenting a result that came from a specific deep model
when_not_to_use:
  - For interactive chat (use the fast model instead)
  - For throughput-sensitive batch work
---

# Frontier models, running locally

Arail can run models up to and beyond 700B parameters on a laptop
via QueueLLM — multi-threaded, prefetched layer streaming off disk
that overlaps I/O and compute across concurrent prompts.
Throughput at that scale is tokens-per-minute, not per-second, but
the model itself is frontier-class. This skill covers the mental
model and which deep model to pick for what.

## The QueueLLM trick in one sentence

Instead of loading all of a model's layers into RAM, QueueLLM
streams transformer blocks from disk with a prefetch worker
overlapping the next block's load against the current block's
compute — so RAM use stays small (a working set of blocks) while
time-per-token approaches the compute ceiling instead of sitting
at disk latency.

## Open models worth knowing about

All run on Arail via QueueLLM by setting `AEROLLM_MODEL=` in `.env`
to the HuggingFace repo ID. Dashboard's Frontier chip shows the
Spec Sheet for the currently-configured one.

### Elite generalists
- **Qwen3-235B-A22B** — Alibaba's flagship MoE. 22B active per
  token, so inference is cheaper than dense 235B. Arail default.
  Apache 2.0.
- **DeepSeek-V3** — 671B MoE, 37B active. MIT license. Rivals
  GPT-4o on code and math. Incredible cost-efficiency.
- **Llama-3.1-405B** — Meta's dense flagship. Solid across the
  board, widely supported, slower via QueueLLM because dense.

### Code + agent specialists
- **GLM-4.6** (Zhipu AI) — ~357B MoE. Strives to be declarative,
  strong on structured output and function-call pipelines.
  Near-frontier HumanEval scores at open-weight license.
- **GLM-5.1** — Zhipu's 2025+ flagship line. Scales up to
  ~754B MoE variants aiming at GPT-4-class capability while
  staying open-weight. Fits on any disk via QueueLLM.
- **DeepSeek-R1** — reasoning-tuned derivative of V3. Shows its
  chain-of-thought. Rivals o1 on AIME + MATH-500 benchmarks.

### Smaller "deep" options
- **Qwen3-32B** / **Llama-3.1-70B** — still MoE or dense
  heavyweights, but small enough that QueueLLM is seconds-per-token
  rather than minutes.

## Choosing the right deep model

Three questions:

1. **What's the disk budget?** 4-bit quantized:
   - 32B → ~20 GB
   - 70B → ~40 GB
   - 235B MoE → ~120 GB
   - 400B+ → 200 GB+
   - 700B+ → 400 GB+
2. **What's the task?** Code/agent → GLM-4.6 or DeepSeek-V3.
   Math/reasoning → DeepSeek-R1 or Qwen3-235B. Generalist →
   Qwen3-235B or Llama-3.1-70B.
3. **How patient are you?** MoE routes cheaper per token than
   dense at the same total params. A 400B MoE (37B active) is
   ~11× faster than 400B dense through QueueLLM.

## Workflow for a deep-model research run

1. Configure: set `AEROLLM_MODEL=<repo-id>` in `.env`, restart.
2. Frame the question. Deep runs are expensive — write down the
   exact question you want answered before you send the chat.
3. Use the dashboard's **Deep model** toggle to flip for one
   message. Watch the Frontier chip for confirmation.
4. Give it 1-15 minutes depending on model size. The UI shows
   latency + tokens/sec so you'll know when it lands.
5. Save the reply to the PKB (`notes/` or `agents/research/`)
   with the model name + date — repro matters for frontier runs.

## Why this matters

Frontier-grade intelligence used to live behind expensive APIs.
Open-weight releases from Qwen, Zhipu, DeepSeek, and Meta closed
the gap. QueueLLM made the hardware barrier vanish. The only thing
stopping a determined user from running GPT-4-class inference on
a MacBook is disk space — and that's a solvable problem.

Treat deep-model runs like you'd treat a batch job on a shared
cluster: frame the question carefully, write down the result,
learn from each turn.
