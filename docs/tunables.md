---
title: Tunables — Chat Studio Controls
category: Reference
order: 30
tags:
  - chat
  - tunables
  - reference
  - inference
audience: operator
related:
  - tuning-loop
  - api-conventions
---
# Tunables — what every knob actually does

> A working glossary for the **Chat Studio** at `/chat`. Every parameter the UI exposes is here with: what it does, when to change it, and what it costs. Read top to bottom for an intro to GenAI control surfaces, or jump to the slider you're staring at right now.

This page is served live at `/docs/tunables.md` — edit `docs/tunables.md` in the repo and refresh.

---

## Mental model (read this first)

A language model takes a **single string of tokens** as input and produces **one token at a time** as output. Every tunable on the chat page changes one of three things:

1. **What goes into the input** (system prompt, history, context window)
2. **How the next token is picked** from the model's predicted probability distribution (temperature, top_p, top_k, penalties, seed)
3. **When generation stops** (max_tokens, stop sequences, JSON mode)

Plus a fourth bucket — **how the local runtime physically loads the model** (offload strategy, KV cache location, prefetch depth, quantization). These don't change *what* the model says, only how fast/big it can say it.

---

## System prompt

A persistent message sent as `role: system` at the start of every turn — before your latest message and before the conversation history.

**Yes, it always goes into the context window.** It is part of the input the model sees on every single inference call. Long system prompts eat tokens that could otherwise hold conversation history; a 500-token system prompt with an 8K context window leaves you ~7.5K for actual chat.

Use it for:
- **Persona**: "You are a senior backend engineer reviewing PRs."
- **Format rules**: "Always answer in three bullet points."
- **Constraints**: "Never invent function signatures; quote them verbatim from the docs I paste."
- **Tone**: "Be concise. No preamble."

Bad use:
- Anything time-sensitive (today's date, current ticker price) — put that in the user message instead so it doesn't stale across turns.
- Conversation memory — the model already gets the history; restating it just wastes tokens.

---

## Sampling

These three knobs control **how the model picks the next token** from its predicted probability distribution.

### temperature (range 0–2, default 0.7)
Scales the probability distribution before sampling. Conceptually:
- `0` — pure greedy. Always picks the single most-likely next token. Same input → same output.
- `0.5–0.8` — natural conversational sweet spot.
- `1.0` — sample from the model's raw distribution (no scaling).
- `>1.0` — flatten the distribution; rarer tokens get a real shot. Creative, sometimes incoherent.
- `2.0` — close to random; usually gibberish.

**Use case → pick:**
| Task | temp |
|---|---|
| Code, JSON, structured extraction | `0.0` – `0.2` |
| Q&A, summarization, RAG | `0.3` – `0.6` |
| Chat, drafting, explanation | `0.6` – `0.9` |
| Brainstorming, naming, ideation | `1.0` – `1.4` |

### top_p (range 0–1, default 0.95)
Nucleus sampling. Before picking, sort tokens by probability and only consider the smallest set whose cumulative probability is ≥ `p`. Cuts the long tail of garbage tokens without rigidly capping count.

- `1.0` — no filter, consider everything.
- `0.95` — typical default, drops the lowest-probability ~5%.
- `0.85` — tighter, more focused.
- `<0.5` — very restrictive, rarely useful.

**Stack with temperature:** high temp + low top_p = creative but on-topic; high temp + high top_p = wild.

### top_k (range 0–200, default 40)
Hard cap on the number of candidate tokens before sampling. Considers only the top-K most-likely.

- `1` = greedy (same as `temperature 0`).
- `40` = sensible default.
- `100+` = effectively no filter.
- `0` = disable (no top_k filter, only top_p applies).

**Heuristic:** if you only know one of `top_p` and `top_k`, set the other loosely. They both filter; setting both tight at once stacks aggressively.

---

## Length & memory

How much the model can read, and how much it can write.

### max response tokens (range 16–16384, default 1024)
Hard cap on the response. Generation stops when either max_tokens is reached or a stop sequence is hit.

- Smaller = faster, less memory pressure, can truncate mid-thought.
- Larger = pay for what you might not use; reasoning models like deepseek-r1 need 4096+ to get past the `<think>` block to an answer.

**Common gotcha:** if a reasoning model (deepseek-r1, o1-style) returns "(model returned no text — try rephrasing)" — bump max_tokens to 4096+. The model burned its budget thinking and never reached the visible answer.

### context window (range 512–131072, default 8192)
How many tokens of input (system prompt + history + your message) the model can attend to in one shot.

- Cost rises **roughly quadratically** with context length — a 32K window is ~16× more expensive than 2K.
- Memory rises linearly with context (KV cache).
- Quality rises until the model's training cap, then plateaus or degrades ("lost in the middle").

**Heuristic:** match your context to your actual need. A coding session needs 16K+; casual Q&A is fine at 4K.

---

## Penalties

These nudge the model away from repeating itself.

### repetition penalty (range 1.0–2.0, default 1.05)
Multiplicative penalty applied to tokens that have already appeared. `1.0` = off.

- `1.05`–`1.15` — gentle, kicks in when the model loops.
- `>1.2` — prose starts feeling stiff and over-corrected.
- Use higher for chat with small models that loop; lower for code.

### presence penalty (range −2 to +2, default 0.0)
Additive bonus/penalty applied **once** to tokens already present in the response. Pushes the model to introduce new topics.

- `+0.5` — encourages broader topical coverage.
- `−0.5` — locks the model onto the current subject.
- `0` is fine for most chat.

### frequency penalty (range −2 to +2, default 0.0)
Like presence, but **scaled by frequency**. Each repeat of a token gets a stronger penalty.

- `+0.2`–`0.5` — reduces verbatim repetition without hurting natural cadence.
- Negative values are rarely useful and mostly cause loops.

**presence vs frequency:** presence flips a switch ("seen yet?"), frequency dials a knob ("seen how many times?"). Use frequency to fight repetition; use presence to widen scope.

---

## Format & stop

Control where the response ends and what shape it has.

### JSON mode (toggle)
Forces the model to emit valid JSON. Handy for:
- Structured extraction (parse a doc → `{name, address, phone}`)
- Agent function-calling shaping
- Eval harnesses that need machine-parseable output

When on, set `temperature 0.0` for max determinism, and remember to put your schema in the system prompt: *"Reply with JSON matching `{title: string, summary: string, tags: string[]}`. No other text."*

Not all backends respect this flag — local MLX backends do their best; cloud providers (Claude, OpenAI) honor it natively.

### stop sequences (textarea, newline-separated)
Strings that, when generated, halt the response immediately. Each line in the textarea is one stop sequence.

Common uses:
- `###` — clip a runaway agent at a marker
- `</answer>` — stop after a tagged section
- `User:` — stop the model from impersonating the user in chat templates
- `\n\n\n` — stop on three consecutive newlines (a runaway list trigger)

**Limit:** most backends accept up to 4 stops. Avoid stops that could appear naturally in the response (e.g., never use `.` as a stop).

---

## Reproducibility

### seed (range 0–999999, default 0)
PRNG seed for sampling. `0` means fresh randomness on every call.

- Pin a seed (e.g., `42`) when running benchmarks or eval suites — same input + same seed + same model + same params = same output (give or take backend determinism).
- Useful for reproducing a "good" response to share or debug.
- **Warning:** seed alone doesn't guarantee determinism. Greedy sampling (temp=0) is more reliable for that. Some backends ignore seed entirely.

---

## Local runtime (only matters for local models)

These don't change the model's *output*; they change *how the model gets loaded* on your hardware.

### prefetch depth (range 0–8 layers, default 2)
For streaming-load backends (AirLLM, QueueLLM): how many model layers to prefetch from disk while compute runs on the current layer.

- Higher = smoother streaming, more RAM/VRAM held.
- Lower = saves memory at the cost of IO stalls between layers.
- `2`–`4` is the sweet spot for NVMe SSDs ≥ 4 GB/s.

### offload strategy (auto / full / shard / stream-hot / stream-all)
How the runtime decides what lives in VRAM vs RAM vs disk.

- **auto** — let the loader pick based on a fit estimate (recommended).
- **full** — entire model in VRAM. Fastest. Requires the model to fit.
- **shard** — split across multiple GPUs.
- **stream-hot** — pin frequently-accessed layers in VRAM; stream the rest from disk. Best for 70B+ on a 24 GB card.
- **stream-all** — everything streams; minimum VRAM, maximum latency. Last-resort for huge models.

### KV cache location (auto / gpu / ram / nvme)
Where the attention cache (key/value tensors per token) lives during a chat turn.

- **gpu** — fast, but the cache grows with context length and squeezes the model.
- **ram** — slower, much more capacity. Reasonable for long contexts on small VRAM.
- **nvme** — slow, near-infinite. Only for context windows that won't fit anywhere else.
- **auto** picks based on free VRAM at inference time.

### quantization (off / 8-bit / 4-bit)
Lower-precision weights shrink the model.

- **off** — preserve full precision (FP16 / BF16). Best quality.
- **8-bit** — ~half the VRAM, near-lossless on most tasks.
- **4-bit** — ~quarter VRAM, lossy on tiny / specialized tasks but usually fine for chat.

**Default**: off for frontier models (we don't auto-compress), on if the model on disk is already a quantized variant (e.g., MLX `*-4bit` folders).

---

## Compute source (one line under the picker)

Switches which backend serves your request:

- **Local (default)** — your installed local models (MLX, Ollama, HF folders).
- **Claude** — Anthropic API. Requires `ANTHROPIC_API_KEY` token.
- **NVIDIA** — NIM endpoints. Requires `NVIDIA_API_KEY`.
- **OpenRouter** — proxy to many cloud models. Requires `OPENROUTER_KEY`.
- **HF** — HuggingFace Inference API. Requires `HF_TOKEN`.
- **Custom** — any OpenAI-compatible URL + key (LM Studio, vLLM, etc.)

Tunables apply per-model and persist in `localStorage`. Switching source doesn't reset them.

---

## Compare mode (A + B)

Click **+ Compare** in Column A's quickbar. Column B appears beside Column A.

- Column A is your **primary on-GPU model** — typically a 7B–14B local model.
- Column B is restricted to **deep layer-streaming backends** (AirLLM / QueueLLM) so it can coexist with Column A without oversubscribing VRAM.
- Each column has its own quickbar and tunables — you can run the same prompt at temp 0.2 in A and temp 1.4 in B to compare deterministic vs creative responses from two different models.

Why the constraint? Loading two competing local models on one GPU thrashes VRAM. AirLLM streams its layers from NVMe so it shares the GPU politely.

---

## Troubleshooting

**"(model returned no text — try rephrasing)"** — usually a reasoning model (deepseek-r1, o1) burned its budget inside `<think>` tags. Bump `max_tokens` to 4096+ and try again.

**Output cuts off mid-sentence** — `max_tokens` too low.

**Model loops or repeats** — bump `repetition_penalty` to 1.10–1.15, or `frequency_penalty` to 0.3–0.5.

**Output is too random/creative for code** — drop `temperature` to 0.0–0.2 and `top_p` to 0.9.

**Output is too short** — check `max_tokens` AND check for stop sequences that might be triggering.

**JSON mode returns invalid JSON** — backend may not honor it. Add explicit "Output must be valid JSON, no preamble." to the system prompt as backup, and lower `temperature` to 0.

**Streaming feels choppy on a streaming-load backend** — increase `prefetch_depth` to 4 or 6 (memory permitting).

---

## Where these are defined in the code

- UI / defaults / ranges: `src/arail/portal/templates/chat.html` → `TUNABLES_DEF`
- Server endpoint: `src/arail/portal/app.py` → `/api/chat/stream` accepts `temperature`, `top_p`, `max_tokens`, etc.
- Per-model spec sheets (params/license/strengths): `src/arail/model_specs.py`
- Backend adapters (how each runtime maps tunables to its API): `src/arail/router/backends.py`
