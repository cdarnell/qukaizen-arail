---
title: Understanding precision — INT vs FP, high vs low
id: understanding-precision
name: Precision Primer
domain: ai
version: 1.0.0
tags: [skill, precision, quantization, int, fp, aerollm]
when_to_use:
  - When planning or analyzing a quantization experiment
  - When comparing model variants (FP16 vs INT8 vs INT4)
  - When explaining why a model's disk footprint shrank at the cost of quality
when_not_to_use:
  - For questions about training precision (this is inference-focused)
---

# Understanding precision

Procedural knowledge for reasoning about numerical precision in
model weights. Critical skill for anyone optimizing QueueLLM or
picking a quantization strategy.

## Two representations

**Floating-point (FP)** stores a number in three pieces:
- **Sign bit** — positive or negative
- **Exponent** — how big or small (scale)
- **Mantissa** — the significant digits

FP16 is 1 sign + 5 exponent + 10 mantissa = 16 bits. Represents
values from ~1e-5 to ~65,000. Graceful with weird distributions.

**Integer (INT)** is just a bitfield. INT8 is -128 to +127.
Fast on most hardware, half the size of FP at the same bit count,
but no decimals and no dynamic range. To use INT for a weight
that's actually fractional, multiply by a per-block **scale factor**
so the weight range fits inside the integer range.

## The ladder

| Format | Bits | Bytes/weight | 70B model | Quality loss |
|---|---|---|---|---|
| FP32 | 32 | 4 | 280 GB | none |
| BF16 | 16 | 2 | 140 GB | none for inference |
| FP16 | 16 | 2 | 140 GB | ~none |
| FP8  |  8 | 1 |  70 GB | trace (emerging) |
| INT8 |  8 | 1 |  70 GB | trace |
| INT4 |  4 | 0.5 | 35 GB | small chat / noticeable math |
| INT2-3 | 2-3 | 0.25-0.4 | <25 GB | only for "does it fit" |

## Three axes of precision (independent knobs)

1. **Bits** — 4 vs 8 vs 16. Halves directly translate to disk +
   load time.
2. **Type** — FP vs INT. FP keeps dynamic range; INT is smaller
   and usually faster per-bit but needs calibration.
3. **Granularity** — per-tensor / per-channel / per-block scale
   factors. Finer = better quality at low bits, more metadata.

## Why this matters for QueueLLM

QueueLLM reads every layer from disk on every token. Layer size
scales with precision. Halving bits halves read time.

- 400B FP16 = 800 GB → 800 GB/token of disk read
- 400B INT4 =  200 GB → 4× faster tokens, same model
- Mixed precision (attention FP16, FFN INT4) = near-INT4 speed,
  near-FP16 quality

## The sensitivity rule

Not all layers are equally sensitive to precision loss. Empirically:

- **Attention Q/K/V/O projections** — mildly sensitive. INT8 is
  usually safe; INT4 starts to wobble on math.
- **FFN up/down projections** — very robust. INT4 or INT2-3 often
  fine.
- **Embeddings + LM head** — most sensitive. Keep at FP16 / INT8.
- **LayerNorm / RMSNorm** — scalars, keep at FP32.

Mixed-precision schemes exploit this: put the sensitive parts at
high precision and the robust parts at low precision. End result
is near-INT4 size + near-FP16 quality.

## Measuring precision-quality tradeoffs

1. Pick the validation metric from `prepare.py`. Never change it
   between quantization experiments.
2. Run the baseline model at FP16 and record the score.
3. Swap to your candidate quantization (e.g., INT4 per-block, 64-
   weight groups). Re-run the same evals.
4. Report as a delta: "INT4 per-block-64: +0.3% loss vs FP16,
   -4× disk, -3.5× QueueLLM tokens-per-minute."
5. A quality delta under 0.5% on relevant benchmarks is usually
   indistinguishable from run-to-run noise. Anything over 2% is
   user-visible.

## Practical defaults for Arail

- **MLX fast path (laptop)** — INT4 per-block. Great chat
  throughput, acceptable quality.
- **QueueLLM deep path (frontier)** — INT4 per-block is the ceiling
  you can run on a MacBook's disk. Mixed precision is the next
  lever in the QueueLLM optimization ladder.
- **Training** — FP32 or BF16 exclusively. Don't train at INT.

## Source links

- llama.cpp's GGUF spec — https://github.com/ggerganov/ggml/blob/master/docs/gguf.md
- AWQ (activation-aware quantization) paper — https://arxiv.org/abs/2306.00978
- GPTQ paper — https://arxiv.org/abs/2210.17323
