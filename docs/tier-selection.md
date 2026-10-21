# Which tier?

> **Start on minimalist** — it's the everyday lab. Its `llama-ai-eng` model
> (built with Llama-3.2-1B, ~0.9 GB) is fast, runs offline on 16 GB, and
> handles chat, quick code, lookups, and most Buddy back-and-forth without
> breaking a sweat. Flip to **maximus** (`./arailctl upgrade maximus`) when
> you hit a *reasoning wall the small model can't climb*: a multi-step
> refactor that has to hold several files in its head, a research-plan
> critique where you need the model to find the flaw rather than agree with
> you, architecture decisions with real tradeoffs, or thorough code review.
> The signal that minimalist isn't enough is concrete: it loops, hand-waves
> the hard step, or confidently gives a shallow answer to a question that
> needed depth. Maximus runs Qwen2.5-7B locally via QueueLLM on Apple Silicon
> (no cloud, no code leaves your machine). Don't default to maximus "just in
> case" — it's heavier, and on most days the 1B is the right tool. Use the
> heavy model when the problem is actually heavy.

## Quick reference

| | minimalist | maximus |
|---|---|---|
| **Default model** | `llama-ai-eng` (Llama-3.2-1B, ~0.9 GB) | `ai-engineer` (Qwen2.5-7B, ~4 GB) |
| **Runtime** | Ollama (local, native `/api/chat`) | QueueLLM / MLX (Apple Silicon); AirLLM fallback on CUDA |
| **RAM floor** | 8 GB (1 GB resident) | 16 GB (4 GB resident) |
| **Install** | Auto — `./arailctl setup` | Opt-in — `./arailctl upgrade maximus` then download weights |
| **Use when** | Chat, Buddy, quick code, daily lab work | Multi-step reasoning, deep code review, research planning |

## Switching tiers

```bash
# Upgrade to maximus (one command):
./arailctl upgrade maximus

# Downgrade back (does not uninstall packages — tabs just hide):
./arailctl upgrade minimalist
```

After upgrading to maximus, follow the printed instructions to build
QueueLLM and download the 7B weights (~4 GB, one-time).

## Disclosure

`llama-ai-eng` is built with **Llama-3.2-1B-Instruct**
(Meta Platforms, Inc. — Llama 3.2 Community License).
See [NOTICE](../NOTICE) and [licenses/](../licenses/) for the full license
text and Acceptable Use Policy.

`ai-engineer` (maximus deep) is built with **Qwen2.5-7B-Instruct**
(Alibaba Cloud — Apache 2.0).
