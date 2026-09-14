---
title: Repository Layout
category: Reference
order: 50
tags:
  - reference
  - architecture
  - layout
audience: operator
related:
  - agents-explained
---
# Repository Layout Plan

This is the staged cleanup plan for keeping ARAIL shareable while active
development continues.

## Goals

- Keep the repo root focused on shipped source, docs, and entrypoints.
- Keep first-run friend-and-family onboarding deterministic.
- Stop writable lab state from making the checkout feel "already used".
- Let AeroLLM evolve as a separate open-source initiative without making the
  main ARAIL release path look unstable.

## Current friction

- The root checkout mixes tracked source, seeded lab content, runtime state,
  models, notebooks, and experimental backend work.
- The default lab still writes into repo-root-adjacent locations like `.env`,
  `lab.conf`, `lab/data/`, `models/`, and `setup.log`.
- Root-level `pytest` can pick up non-product test trees when AeroLLM work is
  present in the same checkout.
- The blueprint system already knows how to materialize isolated instances,
  but the default lab still behaves like a special case.

## Target model

Separate the repo into four concerns:

1. Source tree
   Tracked code and docs only: `README.md`, `docs/`, `blueprints/`,
   `catalog/`, `compose/`, `config/`, `scripts/`, `src/`, and `tests/`.
2. Seed content
   Tracked templates and starter assets that setup can materialize into a lab:
   default PKB docs, builtin agents, blueprint definitions, sample notebooks.
3. Instance home
   Writable runtime state: `.env`, `lab.conf`, logs, models, notebooks, PKB,
   secrets, caches, uploads, and experiment output.
4. Optional backends
   AeroLLM and other deep backends consumed as optional packages, wheels, or
   sibling checkouts instead of as part of the default validation surface.

## Recommended migration path

### Phase 0 — immediate hygiene

- Keep the public contract on two tiers only: `minimalist` and `maximus`.
- Scope default pytest discovery to `tests/`.
- Treat AeroLLM as optional development work, not part of the default ARAIL
  release smoke test.

### Phase 1 — introduce `ARAIL_HOME`

- Add one runtime-root variable that all scripts and Python helpers resolve
  first.
- Default it to `instances/default/` for editable dev checkouts.
- Allow platform-native release defaults later:
  - macOS: `~/Library/Application Support/arail`
  - Linux: `${XDG_DATA_HOME:-~/.local/share}/arail`
  - WSL: keep the Linux default inside the Linux filesystem

### Phase 2 — make the default lab an instance

- Stop treating the repo root as a special runtime home.
- Materialize the default blueprint into `instances/default/` during setup.
- Keep a short compatibility window where root `.env` and `lab.conf` are still
  read if they already exist.

### Phase 3 — move seeds into the package

- Move tracked starter lab content into a package-owned seed directory.
- Make setup copy or render seeds into `ARAIL_HOME` instead of mutating files
  under the source tree.
- Keep `lab/` only if it is intentionally shipped as read-only example content;
  otherwise replace it with package seeds plus generated runtime content.

### Phase 4 — split AeroLLM cleanly

- Keep the integration contract in ARAIL limited to the backend adapter and
  install/health documentation.
- Consume AeroLLM from its own repo via wheel, editable install, or explicit
  sibling-path override.
- Keep AeroLLM benchmarks and runtime tests outside ARAIL's default pytest path.

## Backward-compatibility guardrails

- `./arailctl setup`, `./arailctl start`, and `./arailctl doctor` remain the primary UX.
- Existing root `.env` and `lab.conf` continue to work during the migration.
- `./arailctl blueprint create` remains the long-term primitive; the default lab
  just stops being a one-off implementation.
- Existing docs should describe runtime state as generated content, not as part
  of the source tree contract.

## Release checklist for the cleanup

- Fresh clone on macOS, Linux, and WSL2 completes `./arailctl setup` without
  manual repo surgery.
- `./arailctl doctor` passes.
- Root `pytest` matches `pytest tests`.
- The repo root looks like a product source tree, not a half-used lab.
- AeroLLM remains easy to co-develop, but its failures do not make ARAIL look
  broken to blueprint users.

## Vendored World bundles

The World catalog (`lab/worlds/` — the shipped `ai` and `qukaizen` defaults)
and the demos in `examples/worlds/` are **seed content committed to git**:
sealed qukaizen-ddac exports vendored into this repo so a single ARAIL
download carries every World dependency (no fetch, no submodule). Integrity
is checked by `./arailctl world verify-shipped`; see `lab/worlds/README.md`
for the full vendoring contract.
