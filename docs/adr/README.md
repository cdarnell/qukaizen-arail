---
title: Architecture Decision Records
description: "Index of ARAIL's architecture decision records (ADRs)."
category: Architecture
order: 0
tags:
  - adr
audience: architect
---

# Architecture Decision Records (ADRs)

Short, durable records of significant architecture decisions — the *why*
behind a choice, the alternatives weighed, and the consequences accepted.
Each ADR is immutable once accepted; revisit by writing a new ADR that
supersedes it.

| ADR | Title | Status |
|-----|-------|--------|
| [0001](0001-ai-engineer-domain-specialization.md) | AI-Engineer Domain Specialization of the Default Model | Accepted |
| [0002](0002-chat-memory-and-the-dac-boundary.md) | Conversation Memory and the DaC Boundary | Accepted |
| [0003](0003-why-not-letta-memgpt.md) | Why ARAIL Does Not Wrap Letta (MemGPT) | Accepted |
| [0004](0004-vendor-dac-world-for-offline-friendly-setup.md) | Vendor `dac_world` Instead of a Pinned Git Dependency | Accepted |
| [0004](0004-world-generation-shared-dac-world-package.md) | World Generation Moves from ARAIL-Only to a Shared `dac_world` Package | Accepted |
| [0005](0005-sqlite-as-the-relational-store.md) | SQLite as ARAIL's Relational Store | Accepted |

> **ADR numbers are not unique across this workspace.** ARAIL's own sequence starts
> at 0001. A bare `ADR-0005` / `ADR-0006` in some build scripts and research notes
> belongs to the sibling **aerollm** repo. The sibling **qukaizen-ddac** repo runs a
> third sequence that collides with both — two of its committed records even share
> `0004`. So cite across repos by **repo + filename slug**, never by number alone:
> DaC renumbered its positioning record 0005 → 0006 on landing, which silently
> invalidated every citation that had pinned the number (see
> [0002](0002-chat-memory-and-the-dac-boundary.md)).
>
> **ARAIL's own `0004` slot is claimed twice, on main, for the same reason:**
> [vendor-dac-world](0004-vendor-dac-world-for-offline-friendly-setup.md) and
> [world-generation-shared-dac-world-package](0004-world-generation-shared-dac-world-package.md)
> are two different records with the same number. Cite both by slug, never by
> number.
