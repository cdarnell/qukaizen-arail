---
title: "ADR-0004: Vendor dac_world Instead of a Pinned Git Dependency"
description: "dac_world moves from a pinned git+ssh dependency on the private qukaizen-ddac repo to a vendored copy under src/dac_world, so ./arailctl setup works for operators who were never given access to qukaizen-ddac."
category: Architecture
order: 4
tags:
  - adr
  - dac
  - packaging
  - setup
audience: architect
related:
  - world-generation
---

# ADR-0004: Vendor `dac_world` Instead of a Pinned Git Dependency

**Status:** Accepted
**Date:** 2026-07-26
**Deciders:** QuKaiZen
**Relates:** `qukaizen-ddac/sprints/2026-07-19-dac-generates-arail-worlds/ARCHITECTURE.md`
(the design that introduced the git dependency), [ADR-0002](0002-chat-memory-and-the-dac-boundary.md)
(the earlier, broader "no runtime dependency on DaC" stance this partially re-reverses)

## Context

`pyproject.toml` declared `dac_world` as:

```
dac_world @ git+ssh://git@github.com/cdarnell/qukaizen-dac.git@qukaizen/hungry-bouman-d0761f
```

`qukaizen-ddac` is a **private** repository. `./arailctl setup` runs `pip install -e .[maximus]`,
which shells out to `git clone` over SSH to resolve that dependency. On any machine whose SSH
key is not a `qukaizen-ddac` collaborator, that clone fails with `ERROR: Repository not found`
and setup step 3/11 aborts.

This is not a hypothetical: it reproduced on a real setup run from an operator who had valid
GitHub SSH auth (confirmed via `ssh -T git@github.com`) but no grant on `qukaizen-ddac`.
ARAIL's own positioning (`CLAUDE.md`: "arail — shareable AI Lab for friends/family") makes this
the common case, not an edge case — the people ARAIL is built for are, by construction, people
who were never added as collaborators on QuKaiZen's other private repos.

The git-dependency approach was a deliberate choice, not an oversight. The sprint that
introduced it (`qukaizen-ddac/sprints/2026-07-19-dac-generates-arail-worlds/ARCHITECTURE.md`)
explicitly considered and rejected vendoring:

> **Rejected: vendoring/copy-with-sync-script** — recreates exactly the drift problem we are
> trying to kill (it *is* the current state, just automated).

That rejection was reasoned correctly for the problem it was solving: a maintainer actively
editing both repos in a dual-repo dev setup, where drift between two copies of the same logic
is a real, recurring cost. It did not consider — because it wasn't in scope — a downstream
consumer who has ARAIL's repo and nothing else.

## Decision

**Vendor `dac_world` into ARAIL at `src/dac_world/`, as a plain copy of
`qukaizen-ddac/dac_world/`. Drop the git dependency from `pyproject.toml`.**

`src/dac_world` is package-discovered the same way `src/arail` is
(`[tool.setuptools.packages.find] where = ["src"]`, no include/exclude filter), so no build
config changed beyond removing the git URL line. `arail.world_forge`'s `from dac_world import
...` re-export shim is unchanged — it doesn't know or care whether `dac_world` came from an
editable sibling checkout or a vendored copy.

This knowingly accepts the drift risk the 2026-07-19 sprint rejected. The trade is: a friend or
family member with zero QuKaiZen repo access beyond `qukaizen-arail` can run `./arailctl setup`
and get a working lab, versus a maintainer having to manually re-sync a copy when `dac_world`
changes upstream. For ARAIL's actual audience, the first failure mode (setup does not work at
all) is strictly worse than the second (a stale copy of pure, stdlib-only logic).

### What did not change

- Local dual-repo dev is still better served by `pip install -e ~/ProJects/qukaizen-ddac`, which
  takes precedence over the vendored copy on `sys.path` when both are present. Nothing here
  removes that option for maintainers who have the sibling repo.
- `dac_world`'s own invariants (pure, stdlib-only, no `import arail`) are unaffected — vendoring
  copies files, it doesn't change what's in them.

### Resync obligation

Until this is automated, whoever changes `qukaizen-ddac/dac_world` and wants ARAIL to pick it up
must manually re-copy `qukaizen-ddac/dac_world/` over `qukaizen-arail/src/dac_world/`. There is no
CI check today that catches drift between the two copies. That is accepted debt (see
Consequences), not an oversight.

## Consequences

- `./arailctl setup` no longer requires SSH access to `qukaizen-ddac`. This closes the actual
  incident: `pip install .[maximus]` (and `.[minimalist]`, since `dac_world` was a base
  dependency) now resolves entirely from `qukaizen-arail`.
- `src/dac_world` can silently drift from `qukaizen-ddac/dac_world`. No automated sync, no CI
  parity check exists yet. A future sprint should either add one (e.g., a CI job that diffs the
  two trees and fails on divergence) or replace vendoring with a published wheel — see
  Alternatives.
- The comments in `pyproject.toml`, `world_forge.py`, and `dac_world/__init__.py` that described
  the git-dependency consumption modes are stale and should be read historically, not as current
  behavior, until updated.
- This does not touch `qukaizen-ddac`. `dac_world` remains the source of truth there; ARAIL's copy
  is a snapshot as of 2026-07-26.

## Alternatives considered

**Keep the git dependency, tell operators to request `qukaizen-ddac` access.** Rejected. Defeats
the point of a shareable lab — the whole premise is that friends and family run it without
becoming QuKaiZen collaborators.

**Publish `dac_world` as a wheel to the private index (`pypi.qukaizen.com`), same pattern as
`aerollm-api`.** Preferred long-term fix, deferred for now. It keeps one source of truth and
removes SSH from the critical path (the private index is read over HTTPS, no auth needed for
installs the way `aerollm-api` already works for `arail`'s `maximus` tier). Deferred because it
needs CI/publish tooling `dac_world` doesn't have yet, and the immediate incident needed a fix
that doesn't wait on that. Revisit this ADR when that tooling exists.

**Make `qukaizen-ddac` (or a subtree of it) public.** Rejected for this pass — a call about the
whole repo's visibility, not something to decide as a side effect of an ARAIL setup bug.

**Git submodule.** Rejected for the same reasons the 2026-07-19 sprint gave: brittle with this
workspace's worktree layout, easy to forget to bump — and it still requires SSH access to
`qukaizen-ddac` to clone the submodule, so it doesn't even fix the reported problem.

## References

- `qukaizen-ddac/sprints/2026-07-19-dac-generates-arail-worlds/ARCHITECTURE.md` — the design that
  chose git-dependency over vendoring, for the dual-repo-dev case
- [ADR-0002](0002-chat-memory-and-the-dac-boundary.md) — the earlier, broader stance this
  partially reverses (that ADR is about runtime *data*, not code; `dac_world` was already a named
  exception to it before this ADR)
- `src/arail/world_forge.py` — the re-export shim that consumes `dac_world`
- `CLAUDE.md` — "arail — shareable AI Lab for friends/family," the positioning this fix serves
