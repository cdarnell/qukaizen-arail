---
title: "ADR-0003: Why ARAIL Does Not Wrap Letta (MemGPT)"
description: "Adopt MemGPT's framing, not its substrate. Letta is a Postgres-backed server whose OSS lane is legacy and whose memory is self-edited and ungated — the inverse of the gate ARAIL's memory design is built on."
category: Architecture
order: 3
tags:
  - adr
  - memory
  - letta
  - memgpt
  - dependencies
  - airgap
audience: architect
related:
  - conversation-memory
  - agents
---

# ADR-0003: Why ARAIL Does Not Wrap Letta (MemGPT)

**Status:** Accepted
**Date:** 2026-07-16
**Deciders:** QuKaiZen
**Relates:** [ADR-0002](0002-chat-memory-and-the-dac-boundary.md) (the DaC boundary),
[conversation-memory.md](../conversation-memory.md) (the design this replaces Letta for)

## Context

While designing persistent chat memory ([ADR-0002](0002-chat-memory-and-the-dac-boundary.md)),
the reasonable question came up: **why build memory at all — why not wrap MemGPT/Letta?** The
instinct is sound. Agent memory is a solved-looking problem with a well-known OSS project behind
it, and reinventing it would be waste.

The founder framed the constraint precisely: *"I don't want to reinvent the wheel nor do I want
to include something that isn't the right component… I don't want to introduce a whole new
component."*

This ADR records why the answer is no, so it isn't re-litigated in six months.

## Decision

**Do not wrap Letta. Adopt MemGPT's *framing*; keep ARAIL's substrate.**

This is not a new decision — it is the one DaC's `CONTEXT_VM.md:207-212` already made:

> "**Virtual-context agents — MemGPT (arXiv:2310.08560).** Same OS metaphor (main/external
> context, paging). **Adopt the framing; differ on the substrate:** MemGPT pages *conversation
> history* in and out of a mutable store; CONTEXT_VM pages a *gate-verified, immutable, symbolic
> graph*… The seam: their self-directed memory edits ≈ our RAW tier — but nothing here is
> promoted without the gate."

The paper's *ideas* are the wheel. The *server* is a car you would buy to get the wheel.

## The case for wrapping (steelmanned)

Recorded honestly, because it is real and because it is what would justify reversing this:

- Memory management is genuine research — what to remember, when to compact, how to summarize
  under token pressure. ARAIL's design hand-waves this as "an LLM call behind a gate."
- Letta is **Apache-2.0**, compatible with ARAIL's MIT (we already carry `NOTICE` and
  `licenses/`). **License is not an objection** and should not be presented as one.
- Memory blocks and core/recall/archival tiering are good, proven concepts.
- Wrapping would give ARAIL their ongoing memory improvements for free.

If Letta were a small library that did "conversations on disk with smart recall," wrapping would
be the obvious call.

## Why not, concretely

### 1. Letta *is* the "whole new component" we're trying to avoid

Self-hosted Letta is a **FastAPI server process + PostgreSQL with pgvector** — 42 tables, Alembic
migrations on startup, REST API on :8283, ~500 MB server + ~300 MB Postgres, growing 50–200 MB
per agent per month.

ARAIL has **zero** relational databases (verified: no psycopg, postgres, pgvector, or alembic
anywhere in `src/` or `pyproject.toml`). Its conventions are append-only JSONL (`activity.py`)
and LanceDB.

> **Premise amended (2026-08-10), conclusion intact.** The "zero relational databases" line above
> was accurate as of 2026-07-16 and is superseded by `docs/adr/0005-sqlite-as-the-relational-store.md`
> (`sqlite-as-the-relational-store`): ARAIL now embeds SQLite. The rejection of Letta below does
> not weaken, because it never rested on "no database" — it rested on *dependency cost*. Letta
> requires a ~500 MB server process plus a ~300 MB Postgres, on a network port, with Alembic
> migrations on startup; ARAIL's SQLite is embedded, in-process, single-file, and stdlib. Those are
> different claims about different things.

The requirement — *"a nice disk-based technique where we can save off conversations for
reference"* — is roughly 150 lines of JSONL append. Wrapping Letta means standing up a Postgres
server to get it. **It fails the stated constraint harder than building fails "don't reinvent."**

### 2. The OSS lane is legacy, and the vendor is pivoting to cloud

From the repo, verbatim:

> "This repository contains the **legacy** Letta server (the API server behind the Letta V1 API
> and SDKs). Active development has moved to the Letta Agent repo, and self-hosting an API server
> is now done via the App Server."

Active development moved to `letta-code`; the vendor steers toward **Constellation, their agent
cloud**. Wrapping a legacy component from a vendor mid-pivot *toward hosted* — inside a lab whose
core promise is `LAB_MODE=airgapped` ("blocks every cloud provider… **Don't relax this
default**", `CLAUDE.md`) — points the dependency permanently against us. Their archival memory
has already shipped bugs hardcoding OpenAI embeddings while ignoring the configured provider
(letta-ai/letta#3210). Individually fixable; directionally hostile.

### 3. We already own three of the four spokes

| What Letta provides | What ARAIL already has |
|---|---|
| Recall / archival vector memory | LanceDB + `vector_index.py` + the `pkb_pages` index — already a **core** dependency |
| Agent loop and tools | The agent loader contract (`lab/pkb/agents/<id>/AGENT.md` + `<id>.py`) |
| **Sleep-time compute** | **The dream daemon** — *"the memory-consolidation layer of the agent architecture"*; agents "wake up knowing" yesterday's reflection (`dream_daemon.py`, `docs/agents.md`) |
| Self-editing memory blocks | — the only genuinely new capability |

The overlap is most of the product. What remains is the one thing we must not adopt:

### 4. It is philosophically inverted — the deepest objection

Letta's memory blocks are, by design, **"editable by agents via memory tools"** — self-directed
and ungated. ARAIL's memory design is built on the opposite: nothing an agent believes about the
user is authoritative until it passes the Compiled-KB gate with a locatable verbatim source
(`pkb.py:666-672`, [ADR-0002](0002-chat-memory-and-the-dac-boundary.md)).

DaC's `DAC_ENGINE.md:153-155` names the failure mode: an agent that updates its world from its
own output *"hallucinates a world and then believes itself. The gate is what keeps the loop
tethered to reality."*

**Wrapping Letta would import ungated self-editing memory into a lab whose differentiator is the
gate** — spending the integration budget fighting the dependency's headline feature.

### 5. The only argument that ever favored wrapping is now dead

Hosted memory services win on multi-user: tenancy, durability, and operations you don't want to
build. **The founder ruled on 2026-07-16 that ARAIL will never have multi-user hosted memory** —
never, not "not yet."

That removes the sole scenario in which Letta/Constellation pays for itself. This ADR therefore
rejects **unconditionally**; it does not defer with a revisit trigger. For a single-user local
lab, Letta is a Postgres server to solve `open(path, "a")`.

## Consequences

- ARAIL builds conversation memory natively per
  [conversation-memory.md](../conversation-memory.md). No Letta, no Zep, no Mem0, no Postgres.
- **No `user_id` in any memory schema**, ever. `conversation_id` is the only identity.
  `identity.py` remains *lab* identity (branding), not user identity.
- **Single-user is a permanent design assumption, not a temporary one.** The module-level turn
  registry and single-worker uvicorn shape need no sharding story. Concurrent *tabs* (one user,
  two windows) remain real and are handled explicitly.
- What we knowingly give up: Letta's compaction/summarization research. **Mitigation:** that
  knowledge lives in one swappable distillation prompt, and the dream daemon is already the same
  shape (nightly consolidation → markdown → read back into the prompt).
- The concepts stay welcome. Memory blocks ≈ our gated Tier-2 facts; core/recall/archival ≈ our
  two tiers + LanceDB. Steal the vocabulary; keep the substrate.

## Alternatives considered

**Wrap Letta as a server (Docker + Postgres).** Rejected — §1, §2, §5.

**Vendor a subset of Letta's memory code as a library.** Rejected. There is no supported
library-only, store-less mode; the Agent SDK's "fully locally" path still means Letta owns the
agent loop, and ARAIL already has one. Forking a legacy lane to extract the ~20% we lack, then
gating the part it exists to leave ungated, costs more than writing it.

**Mem0 / Zep.** Not separately evaluated in depth, and the same three objections apply: hosted-first
direction, a store we don't need, and ungated auto-extracted memory. Reopen only against the same
disconfirming evidence below.

## Disconfirming evidence

What would reverse this:

- ARAIL goes multi-user or hosted. **Ruled out permanently on 2026-07-16** — so this is recorded
  for completeness, not as a live trigger.
- A memory library appears that is store-less, runtime-less, air-gap-clean, does not own the agent
  loop, and permits an external promotion gate. That is a different product from Letta, and if it
  exists, wrapping it is right.
- The native distillation ships and is measurably worse than Letta's at deciding what to remember,
  *and* the gap is attributable to the memory machinery rather than the prompt. Then vendor the
  algorithm — still not the server.

## References

- `CONTEXT_VM.md:207-212` (DaC) — "adopt the framing; differ on the substrate"; the RAW-tier seam
- `DAC_ENGINE.md:153-155` (DaC) — hallucinates a world and then believes itself
- `src/arail/agents/dream_daemon.py` — sleep-time compute, already built
- `src/arail/pkb.py:666-672` — `search_for_agents` and the Compiled-KB gate
- `CLAUDE.md` — "Local-first by default… Don't relax this default"
- [letta-ai/letta](https://github.com/letta-ai/letta) — Apache-2.0; "legacy Letta server"
- [letta-ai/letta#3210](https://github.com/letta-ai/letta/issues/3210) — archival memory
  hardcoding OpenAI embeddings
- [Letta memory docs](https://docs.letta.com/guides/agents/memory/) — memory blocks are
  agent-editable
