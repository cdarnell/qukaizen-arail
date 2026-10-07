---
title: Certified & Compatible Models
category: Reference
order: 20
tags:
  - models
  - reference
  - hardware
audience: operator
related:
  - INSTALL
  - MACOS
  - LINUX
---
# Certified & Compatible Models

This page lists the models we've tested with ARAIL and the status of each
on the local-inference path (QueueLLM and AirLLM backends). Use this as
your shopping list when picking what to download via `./arailctl pkb ingest`
or the **Knowledge** tab's reveal-folder buttons.

Status is updated as we run the [aerollm-correctness](https://github.com/qukaizen/aerollm)
harness against each model on Apple Silicon hardware. Cloud providers
(Anthropic, OpenAI-compatible NIM, OpenRouter, HF Inference) are
backend-agnostic — every model the provider hosts works through ARAIL
chat as long as your token is valid.

## Status legend

| Badge | Meaning |
|---|---|
| **Certified** | Passes 19/19 numeric correctness tests against `mlx_lm` reference. Bit-identical greedy decoding. Recommended. |
| **Compatible** | Loads, runs, produces sensible output. Not yet bit-equivalent under the harness, or harness coverage is partial. |
| **Beta** | Loads with a known fix in flight. Use only if you want to help shake out bugs. |
| **Known Issue** | Documented failure mode. See linked issue. Don't use as a daily driver. |

## Local inference — QueueLLM (MLX-native)

The MLX-native backend lives in `aerollm-backend-mlx-native`. It runs
quantized Qwen2.5 and Llama-family checkpoints on Apple Silicon Metal
with no Python in the hot path.

| Model | Quantization | Status | RAM (active) | Disk | Notes |
|---|---|---|---|---|---|
| `mlx-community/Qwen2.5-0.5B-Instruct` | 4-bit | **Certified** | ~1 GB | ~0.4 GB | Fastest local model. Tied embeddings keep parameter tree small. |
| `mlx-community/Qwen2.5-1.5B-Instruct` | 4-bit | **Certified** | ~2 GB | ~1 GB | Best size/quality trade for chat on a 16 GB Mac. |
| `mlx-community/Qwen2.5-7B-Instruct-4bit` | 4-bit | **Beta** | ~6 GB | ~4.5 GB | Fix landed in [aerollm@cc5485a](https://github.com/qukaizen/aerollm/commit/cc5485a). See [DEBUG_QWEN25_7B_CASE_STUDY.md](DEBUG_QWEN25_7B_CASE_STUDY.md). |
| `mlx-community/Llama-3.2-1B-Instruct` | bf16 | **Compatible** | ~3 GB | ~2.5 GB | Llama tokenizer path. Useful as a second-opinion model. |
| `mlx-community/Qwen3-8B-4bit` | 4-bit | **Compatible** | ~6 GB | ~5 GB | Newer Qwen3 architecture. Loads via Qwen2 path; emits coherent text. Correctness harness pending. |

### Download

```bash
hf download mlx-community/Qwen2.5-1.5B-Instruct \
  --local-dir lab/models/Qwen2.5-1.5B-Instruct
```

Replace the repo id and target dir per the table above. The
**Knowledge** tab's `[Reveal models folder]` button takes you straight
to `lab/models/`.

## Local inference — AirLLM fallback

AirLLM is the layered-loading runtime that backs ARAIL's `min` and `max`
tiers when QueueLLM doesn't ship a fast path for the architecture. It
trades latency for the ability to run very large models on a single Mac.

| Model | Quantization | Status | Tier | Notes |
|---|---|---|---|---|
| `meta-llama/Llama-3.1-70B-Instruct` | bf16 | **Compatible** | `min` default | Layered load. Slow but works. Default when QueueLLM has no fast path. |
| `meta-llama/Llama-3.1-405B-Instruct` | bf16 | **Compatible** | `max` only | Frontier-scale bench model. Expect minutes-per-token without aggressive caching. |
| `meta-llama/Llama-4-Maverick-17B-128E-Instruct-fp8` | fp8 | **Compatible** | either | MoE model. Loads through AirLLM; routing latency is high but stable. |

These are downloaded automatically by `./arailctl setup` based on tier
selection — you should not need to fetch them by hand.

## Cloud providers (backend-agnostic)

In `LAB_MODE=hybrid`, every chat request can target a cloud provider
through the **Compute Source** pivot in the Chat tab. The model list is
provider-driven; we don't gate on it.

| Provider | Models | Status |
|---|---|---|
| Anthropic | Claude Opus 4.7, Sonnet 4.6, Haiku 4.5 | **Certified** (every chat path tested) |
| OpenAI-compatible (NIM, vLLM, OpenRouter) | Provider-defined | **Compatible** (any model exposed via OpenAI Chat Completions) |
| HF Inference | Provider-defined | **Compatible** |
| Custom endpoint | Whatever you point at | **Compatible** if it speaks OpenAI Chat Completions |

In `LAB_MODE=airgapped` (the default), all four are blocked at the
provider level — the Chat tab shows a banner, save/test/models endpoints
refuse. Flip to `hybrid` in `.env` to enable.

## Hardware tiers

Pair the model with a machine that can host it:

| Tier | RAM | Recommended models |
|---|---|---|
| **Light** | 8–16 GB Apple Silicon | Qwen2.5-0.5B, Qwen2.5-1.5B, Llama-3.2-1B |
| **Standard** | 24–32 GB Apple Silicon | Add Qwen2.5-7B (once Beta clears), Qwen3-8B |
| **Heavy** | 64 GB+ Apple Silicon | Add Llama-3.1-70B via AirLLM |
| **Frontier** | 128 GB+ Apple Silicon | Add Llama-3.1-405B, Llama-4-Maverick (`max` tier) |

If a model OOMs on your machine, ARAIL's SRE agent will surface the
crash and the **Compute Source** pivot in Chat lets you fall back to a
cloud provider for the affected session without restarting the lab.

## How a model graduates from Beta → Certified

1. Loads cleanly via the relevant backend (QueueLLM or AirLLM).
2. Passes 19/19 numeric correctness tests in `aerollm-correctness`.
3. Runs through the ARAIL portal's Chat tab end-to-end (prefill +
   streaming decode + EOS).
4. No regressions in `cargo test --workspace --release` on Apple Silicon.

When a model lands as **Beta** with an open fix, the table row links to
the relevant commit and case study so you know what's in flight.

## Reporting a new model

If you've run a model through ARAIL and want it listed:

1. Run `./arailctl benchmark_models <model_id>` (or `aerollm` for the
   alias).
2. Copy the output JSON into a Knowledge Base note.
3. Open an issue on
   [qukaizen/arail](https://github.com/qukaizen/arail/issues) with the
   benchmark log attached and your hardware details.

We update this page when each new entry passes the correctness gate.
