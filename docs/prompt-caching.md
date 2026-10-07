# Prompt caching in ARAIL

> **One-line summary:** When you run the lab against **Claude** (hybrid mode),
> ARAIL reuses the stable part of each prompt instead of paying to re-read it
> every call — cutting cost and latency on repeated context. It does **nothing**
> for local inference or in airgapped mode; this is a Claude-only feature.

This doc explains what prompt caching is, how it works, why it matters, and
exactly how ARAIL applies it — including, honestly, where it *doesn't* help.

---

## When this applies (read this first)

Prompt caching is an **Anthropic API feature**. It only does anything when:

1. The lab is in **hybrid** mode (`LAB_MODE=hybrid`) — the airgapped default
   blocks all cloud providers, so caching never even gets a chance to run; and
2. The active **Compute Source is Claude** (the `claude` backend, with an
   `ANTHROPIC_API_KEY`).

For **My Machine** (MLX / CUDA / llama.cpp / Ollama / QueueLLM) and every other
provider, nothing changes — those backends keep their existing behavior
byte-for-byte. Caching is purely additive and Claude-scoped.

---

## What it is

**Prompt caching is a prefix match.** The Claude API hashes the bytes of your
prompt up to a marked point (a *cache breakpoint*). If a later request begins
with the **exact same bytes**, the API serves that shared prefix from cache
instead of reprocessing it.

The mental model that matters:

> **Any byte change anywhere in the prefix invalidates everything after it.**

A single different character near the front — a timestamp, a reordered JSON key,
a per-request ID — means the cache can't match, and you pay full price again.
So caching is really a discipline about **prompt ordering**: stable content
first, volatile content last.

## How it works

- **Render order is `tools` → `system` → `messages`.** A breakpoint on the
  `system` block caches `tools + system`; a breakpoint on a message caches
  everything up to and including that message.
- **Economics (5-minute TTL):** a cache **read** costs ~**0.1×** the normal
  input price; a cache **write** costs ~**1.25×**. So two requests that share a
  prefix already come out ahead (1.25× + 0.1× = 1.35× vs. 2.0× uncached).
- **The cache expires after 5 minutes** of no reads (the default "ephemeral"
  TTL). Bursty, back-to-back calls benefit most.
- **There's a minimum cacheable prefix, and it depends on the model.** Below it,
  caching *silently* no-ops — no error, just no savings:

  | Model | Minimum cacheable prefix |
  |---|---:|
  | Sonnet 4.x / 3.7 | 1024 tokens |
  | **Sonnet 4.6** (ARAIL's default) | **2048 tokens** |
  | Opus 4.x, Haiku 4.5 | 4096 tokens |

- **You verify it worked** by reading the response usage:
  `cache_read_input_tokens` (served from cache) and
  `cache_creation_input_tokens` (written this request). If reads stay `0` across
  repeated requests with the "same" prefix, something is silently invalidating
  it.

## Why it matters

A research lab re-sends a lot of identical context: the same lab-aware system
prompt on every chat turn, the same domain/intent framing across every step of
an autoresearch run. Without caching you pay to re-read all of it every call.
With caching you pay for it once and read it back at a tenth of the price — and
the model skips reprocessing it, so the response also starts sooner. The win
scales with how *large* and how *repeated* the stable context is.

---

## How ARAIL applies it

### The constraint: one flat backend interface

Every backend in [`src/arail/router/backends.py`](../src/arail/router/backends.py)
implements the same `complete(prompt: str, ...)` contract — a single string in,
text out. That uniformity is deliberate (the Compute Source pivot is the
integration seam). To add caching without breaking it, the interface gained two
**optional, keyword-only** params: `system` (a stable prefix) and `messages`
(structured turns). Local backends ignore `messages` and simply prepend
`system` to the prompt — identical bytes to before. Only `ClaudeBackend` builds
a native cached request.

### What we cache vs. what we deliberately don't

The lab-aware system prompt is split (see
[`build_system_prompt_parts`](../src/arail/lab_brain.py)) into:

- **Frozen prefix (cached):** identity + the capabilities reference + the static
  "how to answer" guidance. Byte-stable within a session.
- **Volatile remainder (never cached):** the live state block — current goal,
  cost-to-date, work window, **and a per-second timestamp** — plus any retrieved
  Knowledge Base context. This used to sit in the *middle* of the system prompt,
  which silently broke caching; it now lives in the final chat turn, after the
  breakpoint.

For chat, [`build_chat_payload`](../src/arail/lab_brain.py) returns the frozen
system prefix plus structured turns (history + a final turn carrying the
volatile context and the question). `ClaudeBackend` sends the frozen prefix as a
cached `system` block and marks the last turn so a **growing conversation**
reuses its `[system + history]` prefix on the next request.

For the **Researcher** agent, the intent system context
(`_get_system_context(intent)`) is passed as the cached `system` prefix, so the
3–5 model calls in one autoresearch run reuse it instead of re-sending it each
time.

### Verifying hits

Cache counters flow into the cost tracker
([`src/arail/costs.py`](../src/arail/costs.py)) and appear in the cost summary as
`cache_read_tokens` / `cache_creation_tokens`. After a multi-turn Claude chat or
a Researcher run, non-zero `cache_read_tokens` means it's working.

---

## When it's a no-op (by design)

Be honest about the boundaries — caching does nothing in these cases, and that's
expected, not a bug:

- **Airgapped mode / any local backend.** No network, no Anthropic, no caching.
- **Prefix below the model's floor.** On the Sonnet 4.6 default (2048-token
  floor), the ~1.2K-token chat system prefix won't cache *on its own* — chat
  caching kicks in once the conversation grows (system + a few turns crosses
  2048, typically around turn 3), or for the Researcher when its intent context
  plus skills clears the floor. ARAIL only attaches the `system` breakpoint when
  the prefix is large enough; below the floor it sends a plain prefix rather
  than pretending caching is active.
- **Tiny, one-shot prompts** (e.g. the Buddy/SRE agents' short personas). Far
  below the floor and never repeated — not wired for caching.
- **A different model with a higher floor.** Switching `MODEL_NAME` to an Opus
  model raises the floor to 4096, which will push more chat turns below the
  threshold. The breakpoint logic adapts automatically; the *payoff* shrinks.

## Tuning notes

- **Don't pad prompts just to clear the cache floor.** You'd pay for tokens you
  don't need. Let caching kick in naturally as conversations/context grow.
- **Keep the frozen prefix frozen.** If you add per-request data (a new
  timestamp, a user ID, an unsorted `json.dumps`) to the identity/capabilities
  blocks, you'll silently kill caching. There's a regression test that asserts
  the frozen prefix is byte-identical across calls with different state — keep it
  green.
- The default TTL (5 min) is fine for a personal lab. The 1-hour TTL and
  startup cache pre-warming exist in the API but are overkill here.
