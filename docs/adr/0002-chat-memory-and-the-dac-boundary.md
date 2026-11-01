---
title: "ADR-0002: Conversation Memory and the DaC Boundary"
description: "Chat memory adopts DaC's declare→gate→version discipline in an ARAIL-native store, but does not route conversation data through DaC's pipeline — DaC is the control plane, ARAIL is the data plane."
category: Architecture
order: 2
tags:
  - adr
  - memory
  - chat
  - dac
  - pkb
  - privacy
audience: architect
related:
  - agents
  - conversation-memory
---

# ADR-0002: Conversation Memory and the DaC Boundary

> **⚠ IMPLEMENTATION STATUS (2026-07-23):** the boundary decision holds and
> Tier-1 chat memory is shipped, but the **Tier-2 user-understanding fact
> store** this ADR references (`recall_user_facts`, fact distillation,
> `lab/pkb/understanding/`) is **not yet built**. Treat Tier-2 mentions as the
> ratified design, not current behavior.

**Status:** Accepted (Tier-2 fact store: design ratified, not yet implemented)
**Date:** 2026-07-16
**Deciders:** QuKaiZen
**Relates:** `docs/conversation-memory.md` (the schema contract), `docs/agents.md` (memory model),
`sprints/2026-07-16-arail-chat-memory/ARCHITECTURE.md` (the design)

## Context

ARAIL's Chat tab loses its conversation on every tab switch or reload, and the lab retains no
record of past conversations at all. Two things are wanted: live continuity (the current
conversation survives navigation, including an in-flight response), and durable understanding
(a retained record the lab's agents can use to understand the user across sessions).

The request was that this data be "governed by DaC" — the sibling QuKaiZen project
(`~/ProJects/qukaizen-dac`), which the workspace describes as the declarative layer for what
agents know and can say. Taking that literally would mean routing per-user conversation
history through DaC's pipeline. Investigation showed that would fight DaC's design rather than
extend it, so the boundary needs to be recorded explicitly — otherwise the next sprint
re-litigates it, or worse, quietly builds the wrong thing.

**What DaC actually is today** (verified against the DaC working tree, 2026-07-16): an
offline, build-time authoring pipeline. It compiles curated Knowledge World content from
OKF markdown through a gate/provenance step into static artifacts. It has no runtime, no
client library, and no write API.

## Decision

**Adopt DaC's discipline. Do not route conversation data through DaC's pipeline. Do not claim
DaC governs chat memory at runtime.**

**DaC is the control plane; ARAIL is the data plane.** DaC declares contracts and compiles
their enforcement; it does not store instance data. Conversation transcripts and per-user
facts are instance data, and they live in ARAIL, under the PKB root.

### Why not route through DaC

1. **DaC defines itself against this exact use case.** `CONTEXT_VM.md:208-210` draws the
   contrast deliberately: MemGPT "pages *conversation history* in and out of a mutable store";
   CONTEXT_VM pages "a gate-verified, immutable, symbolic graph." Chat memory is mutable,
   private, per-user, high-volume, and runtime-written. DaC artifacts are immutable, shared,
   curated, low-volume, and build-time-written. The substrate is opposite on every axis.
2. **Core invariants would break.** DaC's `CLAUDE.md` states "source of truth is OKF markdown"
   (chat turns are not authored markdown) and "never commit compiled TOON/BTOON as source"
   (private conversation data must never be committed). DaC's gate requires every term to cite
   a locatable *corpus* source; a user saying "I prefer Rust" has no corpus to cite.
3. **The existing seam points the other way.** DaC's mount contract
   (`0004-dac-arail-mount-contract.md`), Decision 4: "DaC owns the format; ARAIL only reads…
   ARAIL never writes or edits them." Chat memory requires ARAIL to write at runtime,
   inverting the one contract that already exists between these repos.
4. **DaC has no runtime.** Build-time scripts plus a git post-commit hook. DaC's `README.md:55`
   marks write paths "deferred/ROADMAP".
5. **Claiming it would breach DaC's own honesty rail.** DaC's `README.md:57` marks the
   ARAIL←DaC link "declared, narrative only; there is **no artifact yet**", and `:65` states it
   "must never be rendered as a live capability until a real data artifact exists." Shipping
   "chat memory governed by DaC" while DaC does nothing at runtime is exactly what that
   sentence forbids.

DaC's positioning record — `docs/adr/0006-dac-positioning-declarative-control-plane.md` —
canonicalizes it as "the declarative control plane for an agent's I/O boundary." A control
plane governs a boundary by declaring and enforcing a contract. It does not hold the instances
that cross it. The split recorded here *is* that positioning applied honestly.

> **Citation caveat (rev. 2026-07-17; supersedes the 2026-07-16 note).** Treat the filename above
> as a snapshot. Resolve that record by its **slug**:
> `git -C ~/ProJects/qukaizen-dac ls-files 'docs/adr/*dac-positioning*'`.
>
> The prior note said the record was untracked, and advised citing it "by *filename*, not by
> number." Both halves have been overtaken. It was committed on 2026-07-16 (DaC `dc729cc`), and it
> landed **renumbered 0005 → 0006** — the 0005 slot is held by the committed
> `0005-inference-engines-manifest-and-dispatch.md`. So the advice failed on its own terms: DaC's
> ADR filenames are number-prefixed, which means the number *is* in the filename, and "by
> filename" bought no independence from the numbering it was warning about. The slug is the half
> that held — unchanged across the renumber, and the reason the record stayed findable at all.
>
> What stands from that note: DaC's numbering is demonstrably unstable — two committed `0004-*`
> records collide today — so treat every DaC number in this document as a snapshot too. And the
> identity does not rest on this file regardless: it is independently load-bearing in DaC's
> `CLAUDE.md:3` and `PRODUCT.md:1`.

### Why the discipline nonetheless transfers

This is not a polite gesture toward a sibling project. `DAC_ENGINE.md:153-155` names the
failure mode an autonomous knowledge loop invites: an agent that updates its world from its
own output "hallucinates a world and then believes itself. The gate is what keeps the loop
tethered to reality."

That risk is *identical* here. If Buddy distills "facts about the user" from its own generated
text and reads them back as truth next session, the lab confabulates a user and then believes
it. DaC's loop — declare → reconcile → gate → version → no drift (`DAC_ENGINE.md:65`) — is
the control for precisely the failure mode this feature introduces. The discipline transfers
because the risk is the same; the pipeline does not transfer because the substrate is opposite.

### ARAIL already runs this loop

The discipline is not an import. `pkb.py:666-672` — `search_for_agents` applies the Compiled-KB
gate (`ARAIL_APPROVED_ONLY`) so that "agents build ONLY on approved knowledge." Distilled user
facts are therefore written as PKB markdown notes, which inherit the existing LanceDB index,
`search_for_agents`, the approval gate, and the "wipe the PKB = forget me" contract, with no
new machinery. Markdown-as-source with provenance and a gate before promotion is DaC-shaped by
construction.

### The seam we are deliberately not building

The *schema declaration* — the contract defining what a conversation and a turn are — is the
one artifact that could legitimately become DaC-emitted later (DaC declares, ARAIL
instantiates), consistent with the mount contract's DaC-emits/ARAIL-reads direction. It is
named here as ROADMAP and left unbuilt. We do not fake the wire.

## Consequences

- Chat memory is ARAIL-native. There is **no runtime dependency on DaC**, and none should be
  added without superseding this ADR.
- DaC's `README.md:57` ROADMAP row stays accurate. Nothing in this work makes the ARAIL←DaC
  link live, and no DaC file changes.
- Conversation data lives under the **PKB root**, not `DATA_DIR`, because `docs/agents.md:142-143`
  and `_builtin_buddy.py:198-201` establish "wipe the PKB = wipe memory" as a real privacy
  contract. Siting the most sensitive data in the lab outside that contract would silently
  break it.
- Agents may only build on **approved** user facts. The gate is the reality anchor, not a
  formality: facts are distilled **only from user turns, never from assistant output**, and any
  fact without a locatable verbatim span is rejected.
- The two tiers must not be conflated. The raw transcript is never authoritative about the
  user and is never injected wholesale into agent context.
- If a future sprint wants DaC to emit the conversation schema, that is an additive change to
  the control plane and does not require moving instance data.

## Alternatives considered

**Extend DaC to govern chat memory (build a runtime write path).** Rejected. It breaks four
DaC invariants (OKF-markdown-as-source, never-commit-compiled, the corpus-sourcing gate, and
the mount contract's ARAIL-only-reads direction) and requires building a runtime DaC has never
had — all to ship a chat fix. It would also make DaC's own honesty rail (`README.md:65`) false.

**Have DaC emit the schema now, and codegen ARAIL's store from it.** Rejected for this pass,
retained as ROADMAP. It is the honest version of the integration and would create the real
artifact `README.md:65` demands, but it adds a cross-repo build dependency to ship a chat fix,
and the contract should stabilize in use before it is frozen into a compiled declaration.

**A separate SQLite store outside the PKB.** Rejected. Technically the better substrate for
append-heavy concurrent writes, but it is a wholly new pattern in a repo whose conventions are
append-only JSONL (`activity.py`) and JSON+LanceDB dual-write (`agent_workflows.py`) — and,
decisively, siting conversation data outside the PKB root breaks the documented "wipe the PKB =
wipe memory" contract.

> **Superseded in part** by `docs/adr/0005-sqlite-as-the-relational-store.md` (`sqlite-as-the-relational-store`,
> Accepted 2026-08-10): the "wholly new pattern" objection above no longer holds — SQLite is now
> ARAIL's declared relational store for worlds/entities/state. The "breaks wipe-the-PKB" objection
> is **upheld and promoted to a binding constraint** in that ADR: conversation data still must not
> land in `<data_dir>/arail.db` unless `reset pkb` is taught to clear it. Read this rejection as
> scoped to conversation memory specifically, not as a general prohibition on SQLite.

## References

- `docs/conversation-memory.md` — the schema contract and invariants
- `docs/agents.md:126-143` — the agent memory model (scratchpad / state.json / dreams)
- `src/arail/pkb.py:666-672` — `search_for_agents` and the Compiled-KB gate
- `src/arail/agents/_builtin_buddy.py:198-201` — "wipe the PKB genuinely wipes Buddy's memory"
- DaC `CONTEXT_VM.md:208-210` — the deliberate contrast with conversation-history paging
- DaC `DAC_ENGINE.md:65`, `:153-155` — the discipline, and the reality-anchor argument
- DaC `README.md:55`, `:57`, `:63`, `:65` — no runtime; the ARAIL←DaC ROADMAP row; propose-only
  verifiers; the honesty rail
- DaC `CONTEXT_VM.md:207-212` — MemGPT: "adopt the framing; differ on the substrate", and the
  RAW-tier seam this design independently converged on
- DaC `docs/adr/0004-dac-arail-mount-contract.md` — Decision 4, DaC owns the format (resolve by
  the `dac-arail-mount-contract` slug; the `0004` number is shared with a second committed
  record, `0004-leveled-worlds-and-cartographer.md`, so "DaC's ADR-0004" alone is ambiguous)
- DaC `docs/adr/0006-dac-positioning-declarative-control-plane.md` — DaC as control plane
  (committed 2026-07-16 as DaC `dc729cc`, renumbered 0005 → 0006 on landing; resolve by the
  `dac-positioning` slug, not the number — see the citation caveat above)
