---
title: Optimize QueueLLM — research program
id: optimize-aerollm
name: Optimize QueueLLM
domain: research
version: 1.0.0
tags: [skill, aerollm, optimization, research-methodology, frontier-models]
when_to_use:
  - When the lab's goal is improving QueueLLM performance
  - When designing an experiment that measures QueueLLM tokens/minute
  - When deciding which optimization to try next
when_not_to_use:
  - For questions unrelated to layer-streamed inference
  - For general model evaluation (see evaluate-llm instead)
---

# Optimize QueueLLM — research program

This is the research-methodology skill for the lab's signature goal:
make QueueLLM noticeably faster on our hardware, then contribute the
wins back upstream at github.com/cdarnell/qukaizen-queuellm.

## The metric

**Primary:** tokens-per-minute (t/min) on a fixed model with a
fixed prompt length. Captured automatically by the portal in
`lab/data/aerollm-bench.jsonl` for every deep-model call.

**Secondary:** quality delta vs a FP16 baseline, measured on a
held-out task set from `prepare.py`. An optimization that gains
30% throughput but loses 3% on HumanEval is a trade, not a win.

## The five candidates (graded by effort/impact)

1. **Prefetch lookahead tuning** — low effort, 15-40% gain. More
   layers in flight hides more disk I/O until memory pressure flips
   the curve. QueueLLM's prefetcher is the baseline; we're looking
   for the knee on each hardware profile.
2. **Persistent KV cache** — high effort, 3-10× on conversational
   use. Cache per-layer K/V on disk keyed by prompt prefix hash;
   follow-up messages skip most re-compute.
3. **Speculative decoding with the fast SLM** — hard, 3-5× gain.
   The 8B already loaded in RAM drafts tokens; QueueLLM validates
   in batch. Lab-specific advantage — nobody else has a fast + slow
   model coexisting in one process.
4. **Mixed-precision per-layer** — easy, 30-50% disk shrink (→ same
   throughput gain). Attention at INT8/FP16, FFN at INT4. Uses the
   "sensitivity rule" from the precision primer.
5. **Concurrent-prompt batching depth** — QueueLLM's reason for
   existing. Push N up and measure where the per-prompt latency
   curve flattens on your hardware.

Pick one per research cycle. Trying multiple at once confounds
measurement.

## Research-cycle shape

Each cycle is 5-10 experiments over roughly a week. The researcher
agent decomposes the lab goal into this structure automatically.

1. **Baseline.** Wipe `aerollm-bench.jsonl` (or filter by date).
   Run 10+ messages through the current QueueLLM. Capture median
   t/min + quality scores.
2. **Hypothesis.** "Increasing prefetch lookahead from 1 to 3 on
   Qwen3-235B-A22B will increase t/min by ≥ 20% without blowing the
   memory budget."
3. **Falsify first.** Before coding: what observation would change
   my mind? List three alternatives (maybe compute dominates on my
   SSD; maybe deeper prefetch thrashes the page cache; maybe CPU
   cores saturate). Design the experiment to distinguish.
4. **Implement.** Ship the change against the QueueLLM clone.
   Rebuild via `./arailctl setup && ./arailctl restart`.
5. **Measure.** Run the same 10+ messages. Compare against baseline.
6. **Write up.** One markdown file under
   `lab/pkb/agents/research/` with baseline, delta, side-effects,
   and a recommendation (ship upstream / keep experimental / drop).

## Where to look for bottlenecks

Before optimizing, measure. Three questions:

1. **Is it disk-bound or compute-bound?** Time one full token. If
   layer-load time > layer-compute time, optimizations #1 and #4
   are the winners. If compute dominates, #3 and #5 matter more.
2. **Is memory pressure a factor?** Watch `psutil.virtual_memory`
   during a run. Prefetch depth × layer size determines working
   set; over-provisioning thrashes the OS page cache.
3. **Where's wallclock going?** Instrument the forward loop with
   timestamps. Often one surprising step dominates (e.g., Python
   dict lookups on weight keys, or a lock on the prefetch queue).

## Reading for the week

Before claiming a throughput win, understand why it's true. Pre-work:

- [frontier-local-models](../frontier-local-models/SKILL.md) — what
  models are worth targeting and why.
- [understanding-precision](../understanding-precision/SKILL.md) —
  the quantization math that candidate #4 uses.
- [falsify-hypothesis](../falsify-hypothesis/SKILL.md) — the
  methodology for "did my change actually help or am I fooling
  myself."
- [evaluate-llm](../evaluate-llm/SKILL.md) — how to measure quality
  delta rigorously.
- QueueLLM source at https://github.com/cdarnell/qukaizen-queuellm. Read the
  layer iterator and the prefetch worker first.

## Contribution pathway

Every validated optimization goes through this gate before the
researcher writes a "ship it" recommendation:

- Measurement delta reproduced across ≥ 3 separate runs.
- Quality delta under 0.5% on benchmark tasks.
- Description of the change fits in 500 words.
- A human would understand the before/after by looking at the
  graph in the research report.

If all four pass: open a PR upstream with the numbers attached. If
only 1-3 pass: keep it in a local branch as an experiment and
revisit.
