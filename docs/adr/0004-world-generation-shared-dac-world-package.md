---
title: "ADR-0004: World Generation Moves from ARAIL-Only to a Shared dac_world Package"
description: "world_forge.py's model-free forge/seal core is promoted to a DaC-owned package (dac_world) that ARAIL imports as a runtime dependency, ending three duplicate implementations of the same World generator. Scoped exception to the ADR-0002 DaC boundary — applies only to dac_world."
category: Architecture
order: 4
tags:
  - adr
  - dac
  - world-forge
  - world-generation
  - dependencies
  - boundary
audience: architect
related:
  - conversation-memory
  - agents
---

# ADR-0004: World Generation Moves from ARAIL-Only to a Shared `dac_world` Package

**Status:** Accepted
**Date:** 2026-07-19
**Deciders:** QuKaiZen
**Relates:** [ADR-0002](0002-chat-memory-and-the-dac-boundary.md) (the DaC boundary this ADR
carves a scoped exception into), [ADR-0003](0003-why-not-letta-memgpt.md) (unrelated but same
"borrow discipline, not substrate" style of reasoning), `qukaizen-dac`'s
`docs/adr/0007-world-generation-shared-dac-world-package.md` (the corresponding DaC-side ADR —
separate git history, so referenced by title/path, not commit SHA),
`qukaizen-dac/sprints/2026-07-19-dac-generates-arail-worlds/ARCHITECTURE.md` (the full design),
mirrored into this repo at
`sprints/2026-07-19-dac-generates-arail-worlds/{ARCHITECTURE,BUILD_LOG,REVIEW}.md`.

## Context

A World bundle at `lab/worlds/physics/face.json` was sealed with literal `XXXX`/`YYYY`
placeholder text in `domain_framing`/`vocabulary_register` — valid sha256 hashes computed over
garbage, hand-patched during this sprint. The investigation surfaced a structural cause: three
independent implementations of "generate and seal a World" existed at once — DaC's canonical
TypeScript forge+sealer (`scripts/forge-world.mts` et al. in `qukaizen-dac`), and this repo's
`src/arail/world_forge.py`, a **hand-written Python port** of it whose own sealer docstring
explicitly declared "byte-parity with DaC's sealer is a NON-goal." (`dac_compiler.py` in
`qukaizen-dac` is unrelated — OKF markdown → TOON only, no world/bundle logic.)

This repo's own `world_forge.py` docstring asserted the governing boundary: "the repo boundary
is the portable compiled artifact — no cross-repo runtime imports." [ADR-0002](0002-chat-memory-and-the-dac-boundary.md)
guards the same boundary for a different feature (chat memory), concluding "DaC is the control
plane; ARAIL is the data plane" and "there is no runtime dependency on DaC, and none should be
added without superseding this ADR." World generation is not chat memory, but it crosses the
same boundary, so this decision is recorded as its own ADR rather than silently exercising
ADR-0002's escape hatch.

## Decision

**Promote `world_forge.py`'s pure, model-free forge/seal core to a shared, DaC-owned package
(`dac_world`, in `qukaizen-dac`) and import it here as a runtime dependency.**

- `src/arail/world_forge.py` is rewritten as a thin re-export shim over `dac_world`
  (`forge_world`, `write_bundle`, `reseal_bundle`, `render_world_skill`, `validate_bundle_content`,
  `GateRefused`, `ContentInvalid`, plus the Curator-judge/growth-loop surface the portal and
  Librarian scout depend on).
- `dac_world` is added to `pyproject.toml`'s `dependencies` (currently pinned to the
  in-progress migration branch `qukaizen/hungry-bouman-d0761f` in `qukaizen-dac` — **tracked as
  an open follow-up**, not resolved here: this pin must move to a tag or commit SHA once that
  branch merges to `qukaizen-dac`'s `main`, per Failure F3 in the DaC-side ARCHITECTURE.md).
- ARAIL retains everything that makes the forge *ARAIL's*: `arail.router.ModelRouter`
  construction, `inference_slot`/`asyncio.to_thread` wrapping, cancel-event plumbing,
  progress→SSE, and the theme-schema validator (`arail.world_theme.parse_world_theme`), which
  is injected *into* `dac_world.write_bundle`/`reseal_bundle` as a callable parameter rather than
  imported by `dac_world` — this is what keeps `dac_world` itself ARAIL-free (no `import arail`
  anywhere under it, enforced by a static AST-based check in DaC's own CI).
- Sealed bundles keep living exactly where they always have —
  `qukaizen-arail/lab/worlds/<slug>/` — and the bundle schema (`dac.world-bundle/v1`) is
  unchanged. This is a generator swap, not a bundle-format change or a hosting change.
- New: `validate_bundle_content`, gating both `write_bundle` and `reseal_bundle` against
  placeholder-shaped content before any file write — the actual fix for the reported incident,
  shipped first as a standalone hotfix (`2eb41ea`) independent of this migration, then carried
  into `dac_world` when the core moved.

### Scope of the boundary exception

This reverses "no cross-repo runtime imports" **only for `dac_world`**, and only because:

1. `dac_world` is verified model-free and ARAIL-free by static analysis, not by convention —
   the same discipline ADR-0002 already asks for ("declare→gate→version"), applied to code
   rather than to data.
2. Unlike chat memory (ADR-0002's subject), World-generation code is not per-user instance data;
   it is a shared, versioned, Apache-2.0 algorithm with no privacy surface. ADR-0002's core
   argument — mutable, private, per-user, runtime-written data does not belong in DaC's
   build-time, immutable, curated pipeline — does not apply to a stateless function library.
3. The alternative (Alternative #3 in the DaC-side ARCHITECTURE.md: keep two generators, add
   only a shared parity test) was available and rejected specifically because it does not, by
   itself, close the incident class (each side would still need its own content validator) and
   leaves the codebases free to rot independently, as they demonstrably already had.

This ADR does **not** reopen ADR-0002. Chat memory stays ARAIL-native with no DaC runtime
dependency; that decision is unaffected.

## Consequences

- **A new runtime dependency exists where there was previously an artifact-only boundary.**
  Tracked via `pyproject.toml`; ARAIL CI includes an import smoke test
  (`tests/test_dac_world_shim_smoke.py`) so a broken or missing `dac_world` install fails loud,
  not silent.
- **`world_forge.py` shrinks from a ~1120-line implementation to a ~90-line shim.** Anyone
  extending World-generation logic now edits `dac_world` in `qukaizen-dac`, not this file.
- **Dependency-pin discipline is now this repo's responsibility to maintain.** The current git
  branch pin is mutable and known-fragile (Failure F3 in the DaC-side ARCHITECTURE.md) — a
  force-push, rebase, or branch deletion in `qukaizen-dac` could silently change or break this
  repo's reproducible build. **Open follow-up, not resolved by this ADR:** move the pin to a tag
  or SHA once `qukaizen/hungry-bouman-d0761f` merges to `qukaizen-dac@main`.
- **The theme validator is now an injected dependency at 3 call sites** in
  `src/arail/portal/world_routes.py` (`api_forge_confirm`, `_reseal_and_swap`, the grow loop),
  rather than an inline import inside the sealer. If no validator is injected and a `theme`
  override is present, sealing fails closed (`ValueError`) rather than silently skipping
  validation.
- **A pre-existing sidecar-preservation bug surfaced and was fixed.** `reseal_bundle`'s
  seal-exempt-sidecar handling used a hardcoded 2-name allow-list that silently dropped
  `evolution.json` (Growth Engine log) and `librarian-scout.json` (Librarian mining state) —
  traced to a pre-migration commit, carried forward verbatim per the "move the core verbatim"
  instruction, caught during a full `reseal_all` sweep, and resolved via a documented
  regenerated-set-denylist + warn-loud policy (DaC-side ARCHITECTURE.md addendum) rather than
  improvised mid-build.
- **Open, unresolved by this ADR: the DaC TypeScript forge's fate.** `qukaizen-dac`'s
  `scripts/forge-world.mts` et al. become a *fourth* copy of this logic unless retired or
  explicitly designated a reference/other-language implementation. This is a product decision
  requiring separate human input on the DaC side and is not decided here.

## Alternatives considered

Same set evaluated in the DaC-side ARCHITECTURE.md (not re-litigated in full here):

1. **Import `dac_compiler` literally** (the originally-stated ask) — rejected; that module has
   no world/bundle logic.
2. **(this decision — accepted)**
3. **Keep two generators, add only a shared spec + cross-repo parity/golden test, no shared
   code** — honors ADR-0002's boundary language most literally, cheaper, but does not close F1
   by itself and leaves the codebases to rot independently. Would have been the correct fallback
   had the boundary reversal been declined.
4. **Fix `validate_bundle_content` in this repo's `world_forge.py` only, defer any merge** —
   shipped first, standalone, as the immediate hotfix (`2eb41ea`), regardless of this larger
   decision.

## References

- `qukaizen-dac/docs/adr/0007-world-generation-shared-dac-world-package.md` — the corresponding
  DaC-side decision record (separate repo/history; referenced by title, not commit SHA).
- `qukaizen-dac/sprints/2026-07-19-dac-generates-arail-worlds/ARCHITECTURE.md` — full design,
  interface contracts, failure modes F1–F9, test strategy, alternatives, sidecar-preservation
  addendum. Mirrored into this repo:
  `sprints/2026-07-19-dac-generates-arail-worlds/ARCHITECTURE.md` (mirror; the DaC copy is
  canonical).
- `sprints/2026-07-19-dac-generates-arail-worlds/BUILD_LOG.md`,
  `sprints/2026-07-19-dac-generates-arail-worlds/REVIEW.md` — mirrored build and review record.
- [ADR-0002](0002-chat-memory-and-the-dac-boundary.md) — the DaC boundary this ADR carves a
  narrow, `dac_world`-scoped exception into; unaffected for chat memory.
- Commits: this repo — `2eb41ea` (hotfix), `a6dc2a0` (shim), `859f577` (golden-bundle parity
  test). `qukaizen-dac` — `a463eca`, `19a0430`, `6f1a6d4`, `092e8ec`, `8cb0d74`, `99b04e8`.
