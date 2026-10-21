---
title: "World Forge — dream a world, then let research curate it"
description: "Draft a knowledge World fast with the local model; let QueueLLM + the autoresearch loop forever curate it into sourced truth. Speculative authoring."
category: "Concepts"
order: 6
tags:
  - worlds
  - queuellm
  - speculative-decoding
  - autoresearch
  - maximus
read_minutes: 6
audience: intermediate
status: design
related:
  - the-lab
  - agents-explained
  - tier-selection
buddy_prompt: "Explain World Forge like I'm excited but skeptical — how can a 1B model build a 'real' world, and what does QueueLLM actually add?"
---

# World Forge

> **Quickly get a base — forever curated by the power of QueueLLM.**

> **Status: shipped (v1).** The Forge lives on the **Worlds page** (`/worlds`,
> also "✦ Forge a World…" in the world switcher): name any subject, pick a
> size (25/50/100 terms), watch the local model draft it, preview, and
> Accept & Mount. Terms are then editable in **Knowledge → World Terms**
> (every edit re-seals the bundle honestly), and the **Curator review**
> ("Ask the Curator to review this world") flags suspect terms on demand.
> The always-on overnight curation loop remains the build-out described in ②③.

A **World** is the lab's identity layer — a sourced dictionary, a knowledge
graph, a palette, a framing — that you *mount* to turn the whole lab into a lab
*for that subject* (see [the-lab](the-lab.md)). Until now, authoring one meant
the CLI press. World Forge is the answer to: **"I just want to dream up a
subject and have the lab build the world around it — before I even set a goal."**

## The one idea: draft cheap, curate forever

You don't need a frontier model to *start*. You need one to get *good*. So split
the work the way nature already split it for inference — **speculative
decoding** — but one altitude up:

```
        ┌─────────────────────────────────────────────────────────────┐
        │  YOU: "build me a world about tide-pool ecology"              │
        └─────────────────────────────────────────────────────────────┘
                              │
                  ① DRAFT  (local model, in RAM, seconds)
                              │   seed → discover (BFS) → link → define
                              ▼
        ┌─────────────────────────────────────────────────────────────┐
        │  a whole World, fast.  tier: MODEL-ASSERTED (dreamed)        │
        │  honest, mountable, gated — but unverified                   │
        └─────────────────────────────────────────────────────────────┘
                              │
                  ② RECONCILE  (QueueLLM, deep model, overnight · batch)
                              │   "did the small model get it right?"
                              │   accept · correct · reject · ground-vs-source
                              ▼
        ┌─────────────────────────────────────────────────────────────┐
        │  terms that survive + cite a real source  →  PROMOTED        │
        │  tier climbs: model-asserted → mixed → SOURCED               │
        └─────────────────────────────────────────────────────────────┘
                              │
                  ③ REFINE  (the autoresearch loop, continuously)
                              │   Researcher gathers sources into the KB;
                              │   new evidence re-reconciles the World
                              ▼
                    a world that gets truer while you sleep
```

**① is speed to market.** A ~1B local model drafts a coherent, mountable World
in seconds — categories, terms, a linked association graph, definitions. It is
honestly stamped **model-asserted**: real enough to *use*, labeled clearly as
*dreamed, unverified* (it can never masquerade as sourced — the tier is derived
from the sealed corpus, not asserted).

**② is curation.** A bigger model **reconciles** the draft — the exact economics
of speculative decoding: you pay the deep model only to *verify*, which is cheap,
parallel, and batchable. Verification that survives a *retrieved source* gets
**promoted** from `model-asserted` toward `sourced`. The draft was the
speculation; QueueLLM is the accept/reject.

**③ is ARAIL's whole reason to exist.** The autoresearch loop never stops
gathering sources — so the World keeps getting *truer* over time. You dreamed a
base; the lab curates it forever.

### It's a *cousin* of speculative decoding — not literally it

Be honest about this, because it has a design consequence. World Forge borrows
the **economics** of speculative decoding (draft cheap, verify with a bigger
model) but not the **algorithm**:

| | Speculative decoding | World Forge (speculative *authoring*) |
|---|---|---|
| unit | tokens | whole terms / a World |
| verify | one parallel forward pass | semantic critique, term by term |
| acceptance | **exact** — matches the target's sample | **judgment** — is this right, is it sourced |
| guarantee | output **identical** to the big model | a *blend* — quality between the two |
| the small model buys… | **speed only** (can't change the answer) | the **scope** (the big model sets accuracy) |

The consequence: in real spec-decoding the small model can't hurt you. Here it
**bounds coverage** — a term the drafter never dreamed, the curator won't add
unless asked. So the loop needs an explicit *expand* step, not just *verify*.
QueueLLM does the real thing at the token level internally; World Forge rhymes
with it at the world level.

## What the deep model actually buys you

We measured it. Same subject ("espresso brewing"), same loop, different drafters,
then a deep-model reconcile pass:

- **Local 1B** drafts in ~20s, but mis-files *Espresso* under "kitchen-appliances"
  and writes over-broad definitions.
- **A 7B** judging that 1B draft **accepted only 2 of 16 terms** — and its catches
  were correct: *"Espresso — not an appliance, a coffee type"; "Brewing Method —
  too broad, not espresso-specific."*

That gap is **accuracy**, and it scales with parameters: a deeper model holds more
of the world and draws finer distinctions, so it makes a better *curator* than any
small model can be a *drafter*. The bigger the reconciler, the truer the world it
can pull a cheap draft up to. (Use **instruct** models to draft and reconcile;
reasoning models think instead of answering and stall the draft loop.)

## Where it lives in the tiers

**Both tiers get the whole loop.** Tier is about breadth of surfaces and
STARTING MODEL SIZE — never about gating core functionality. World creation,
term editing, Curator review, autoresearch, chat, and the knowledge base are
core on both tiers; minimalist concedes only non-critical tabs (notebooks,
admin extras).

| Tier | Same loop, different muscle |
|---|---|
| **minimalist** | Draft + curate with the ~1B starting model, fast, fully airgapped. Honest `model-asserted` worlds you can mount, study, and review today. |
| **maximus** | The same loop on a **3–7B starting model** (finer drafts, sharper Curator verdicts), plus QueueLLM's deep-mode backend for frontier-scale overnight reconcile passes (the scheduler's heavy window, 22:00–08:00). |

The upgrade story is quality, not capability: **every tier dreams and curates
worlds; a bigger model just makes them truer, faster** — culminating in QueueLLM
running deep models from disk on hardware that has no right to run them.

## See also

- [the-lab](the-lab.md) — what mounting a World does to every surface.
- [tier-selection](tier-selection.md) — minimalist vs maximus.
- [agents-explained](agents-explained.md) — the Researcher / autoresearch loop that does ③.
