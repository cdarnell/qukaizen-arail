# Architecture: DaC generates ARAIL Worlds

> **Mirror notice:** this is a mirrored copy for ARAIL's own sprint history. The canonical copy
> lives in `qukaizen-dac` at
> `.claude/worktrees/trusting-knuth-9b6917/sprints/2026-07-19-dac-generates-arail-worlds/ARCHITECTURE.md`
> — resolve any discrepancy in favor of that copy.

**Date:** 2026-07-19
**Spec:** no VISION.md for this sprint; motivated by a placeholder-corruption incident in
`qukaizen-arail/lab/worlds/physics/face.json` and the user's decision "worlds live in ARAIL but
are generated from the DaC framework, via ARAIL importing `dac_compiler` as a library."
**Repo pin:** qukaizen-dac @ `38aa690`; qukaizen-arail sibling read at `~/ProJects/qukaizen-arail`.

> **Read this section first — the stated integration mechanism does not match the code.**
> The user asked for "ARAIL imports `dac_compiler`." I verified both repos. `dac_compiler.py`
> contains **zero** world/bundle/forge/face logic — it is only OKF-markdown → TOON/trie/GBNF.
> The world-bundle generator that produced the corrupted `face.json` lives entirely in ARAIL's
> `src/arail/world_forge.py`, which is a hand-written **Python port of DaC's TypeScript forge**
> (`scripts/forge-world.mts`, `scripts/export-bundle.mts`, `src/gate.ts`, `src/provenance.ts`,
> `src/arail-export/*.ts`). So "import `dac_compiler`" as literally written buys ARAIL nothing:
> the module has none of the code that matters here. This document therefore designs the
> **intent** ("one DaC-owned generator, ARAIL imports it") rather than the literal seam, and is
> explicit about the substitution. It also flags that the intent **reverses a documented
> VISION-level stance in both repos** (see Assumptions #1). This needs an explicit human OK
> before build.

## Restatement

Today there are three separate code paths that touch "a World": (1) `dac_compiler.py` — a small
Apache-2.0 Python tool that compiles OKF markdown into TOON/trie/GBNF, unrelated to bundles;
(2) DaC's canonical **TypeScript** forge+sealer under `scripts/` and `src/`, which drafts a
World from a subject and seals a `dac.world-bundle/v1`; (3) ARAIL's `world_forge.py`, a Python
re-implementation of (2) that ARAIL actually runs, whose sealer docstring explicitly declares
"byte-parity with DaC's sealer is a NON-goal." A physics World was sealed with literal
`XXXX`/`YYYY` placeholder text in `face.json`'s `domain_framing`/`vocabulary_register`, with
valid sha256 hashes over the garbage, and was hand-patched this session. The user wants to
collapse the duplicate generators so ARAIL's Worlds are produced by a single DaC-owned
generator (bundles still physically land under `qukaizen-arail/lab/worlds/<slug>/`), so drift
and corruption are caught in one place. The honest realization of that intent is **not** "import
`dac_compiler`" — it is "promote the pure forge/seal core to a shared, DaC-owned Python package
that both repos import, and add a pre-seal content validator" (the placeholder bug is a content
bug the sealer will faithfully seal no matter how unified serialization becomes).

## Assumptions

1. **[LOAD-BEARING, needs human sign-off] This reverses a documented stance.** Both
   `qukaizen-dac/VISION.md` ("World generation is an **ARAIL capability, not a separate hosted
   service**"; "the boundary is load-bearing") and ARAIL's `world_forge.py` docstring ("the repo
   boundary is the portable compiled artifact — **no cross-repo runtime imports**") assert that
   ARAIL owns generation and the two repos communicate only via the sealed artifact. ARAIL's
   `CLAUDE.md` further points at `docs/adr/0002-chat-memory-and-the-dac-boundary.md` guarding the
   DaC boundary. Making ARAIL import DaC code at runtime is a deliberate reversal. We assume the
   user accepts superseding that stance with a new ADR in *both* repos. If not, the fallback
   design (a shared spec + a parity test, no shared code) in "Alternatives" is the correct one.
2. We assume "generated from the DaC framework" means **shared generator code**, not "author
   ARAIL Worlds as OKF markdown." ARAIL Worlds are *forged* (model-call-driven, `forge_world`)
   into `spec.json`+`terms.json`; they are not authored as `docs/*.md`. Forcing OKF authoring
   would break the forge UX entirely. See "Source of truth" below.
3. We assume DaC may take a runtime dependency on a model **router only inside ARAIL** — the
   pure forge/seal core must stay model-free (it already is in `world_forge.py`: `forge_world`
   takes an injectable `router`; the sealer functions are pure). The shared package must not pull
   ARAIL's `arail.router` or portal into DaC.
4. We assume Python-version and dependency compatibility: `dac_compiler.py` is stdlib + optional
   PyYAML; the forge/seal core is stdlib-only (`hashlib`, `json`, `re`, `shutil`). No heavy
   transitive deps enter ARAIL from this.
5. We assume the already-sealed bundles under `qukaizen-arail/lab/worlds/{ai,photography,physics,
   qukaizen}/` must keep loading (`world_mount.verify_seal`) unchanged — this is a generator
   swap, not a bundle-schema change. `dac.world-bundle/v1` and `compat:{bundle_schema:1,
   terms_schema:1}` stay fixed.
6. **[License]** `dac_compiler.py`/DaC is Apache-2.0; ARAIL is MIT. Apache-2.0 → MIT consumption
   is fine. But code flows the *other* way here: MIT `world_forge.py` logic moves *into* the
   Apache-2.0 DaC repo. We assume the user (sole author of both) consents to relicensing that
   moved code under Apache-2.0; a one-line provenance note in the new module covers it.

## Source of truth (the load-bearing content-model question)

Investigated both inputs directly:

- **`dac_compiler.py` input contract:** a directory of OKF markdown (`docs/*.md`), each with YAML
  frontmatter + GFM tables; output is flat tabular records → TOON/trie/GBNF. It has **no notion**
  of `slug`/`related`/`category`/`source`/gate/provenance/face/bundle.
- **`world_forge.py` data model:** a World is `spec` (`slug`, `display_name`, `categories`,
  `knowledge_sources`) + `terms` (list of `{slug, term, category, short, definition, example,
  related[], source}`). Forged from a subject via ~model calls; gated by
  `assert_closed_sourced_graph`; provenance derived, never asserted.

These two models do **not** align, and unifying them onto OKF markdown is out of scope and against
the forge UX. **Decision: dac_compiler is the wrong seam; do not route ARAIL Worlds through OKF.**
The shared unit is the **forge/seal core** (the `world_forge.py` data model), promoted to DaC.
`dac_compiler.py` stays exactly as-is for its OKF→TOON job. The two can later share the
`ToonSerializer` if/when a World wants a TOON projection — that is the *only* place the
`dac_compiler` `Serializer` invariant becomes relevant, and it is optional (see Interface
contracts, note on the Serializer invariant).

## Data flow

### Before

```
qukaizen-dac (TS)                         qukaizen-arail (Python)
  scripts/forge-world.mts   ── porting ──►  src/arail/world_forge.py
  scripts/export-bundle.mts    (by hand)      forge_world()  (model router)
  src/gate.ts, provenance.ts                  write_bundle() / reseal_bundle()
  src/arail-export/*.ts                       render_world_skill()
        │                                            │
        ▼                                            ▼
  data/worlds/<slug>/ (TS output)            lab/worlds/<slug>/  ◄── the sealed bundle
                                             (face.json domain_framing came from
  dac_compiler.py  (OKF md → TOON/trie/GBNF; UNRELATED to any of the above)
```
Two independent generators; no shared correctness guarantee. A bad `face.json` (placeholder text
supplied as a `face_override`, or hand-edited then `reseal_bundle`d — reseal preserves display
fields *verbatim*) is sealed with valid hashes and nothing catches it.

### After (recommended)

```
qukaizen-dac  (new shared Python package: `dac_world`)          qukaizen-arail (Python)
  dac_world/forge.py    forge_world(), ForgeParams          import dac_world as the
  dac_world/gate.py     assert_closed_sourced_graph()   ◄── generator; ARAIL keeps only
  dac_world/provenance.py                                   the router + portal wiring:
  dac_world/seal.py     write_bundle/reseal_bundle          - injects arail.router
  dac_world/skill.py    render_world_skill()                - async slot / locking
  dac_world/validate.py reject_placeholder_content()        - endpoint + progress UI
        │  (pure, model-free, stdlib-only)                        │
        └───────────── installed as a dependency ────────────────┘
                                                                  ▼
                                              lab/worlds/<slug>/  (still lives in ARAIL)
                                              — one code path, one gate, one validator
  dac_compiler.py  unchanged (OKF → TOON/trie/GBNF; may lend ToonSerializer if a World
                   wants a TOON projection, optional)
```
`world_forge.py` becomes a thin shim: `from dac_world import forge_world, write_bundle, ...` plus
ARAIL's own async/router glue and any Buddy-plugin specifics that are genuinely ARAIL-only.

## Interface contracts

**New package `dac_world` (in qukaizen-dac), moved verbatim from `world_forge.py`'s pure core:**

- `forge_world(params, *, router, progress_cb=None, cancel=None) -> ForgeResult`
  - **Requires:** `router.complete(prompt, ...) -> obj with .text/.model`; `params.subject`
    non-empty. **Promises:** in-memory `ForgeResult`, no disk writes, provenance derived from
    corpus, never raises on a single bad model call (tolerant `loose_json`). **Bad input:** empty
    subject → `ValueError`; empty corpus → `GateRefused`.
- `write_bundle(out_dir, spec, terms, *, face_overrides=None, roster=None, created_at=None)
  -> Path`
  - **Requires:** `terms` pass `assert_closed_sourced_graph` against `spec.categories`, **and
    (NEW) pass `validate_bundle_content`** (see below). **Promises:** a sealed
    `dac.world-bundle/v1` that round-trips ARAIL's `load_bundle + verify_seal + check_compat +
    check_categories`; `manifest.world_sha256 == files["terms.json"]`; `SKILL.md`,
    `capabilities.json`, `arail-plugin.json` stay seal-exempt (absent from `manifest.files`);
    integrity fields (`provenance_tier`, `provenance_counts`, `schema`, `world`) force-derived
    last so authored overrides can never assert provenance. **Bad input:** invalid gate →
    `GateRefused` (no files written); invalid theme override → `ValueError`; **(NEW)**
    placeholder-shaped content → `ContentInvalid` (no files written).
- `reseal_bundle(bundle_dir, terms=None) -> Path`
  - **Requires:** an existing bundle dir. **Promises:** re-derives everything downstream of
    `terms.json` while preserving authored display fields verbatim; atomic swap with rollback.
    **CHANGE (see Failure F1):** must run `validate_bundle_content` on the preserved display
    fields *before* re-sealing — today it preserves garbage verbatim, which is exactly how the
    XXXX survived a reseal.
- `validate_bundle_content(face, spec, terms) -> None` **(NEW, the actual fix)**
  - **Promises:** raises `ContentInvalid` if any display/definition string matches a
    placeholder shape (`^[XY]{3,}$`, `\bTODO\b`, `\bTBD\b`, `lorem ipsum`, `\bplaceholder\b`,
    empty-after-strip where a value is required, or runs of a single repeated non-word char
    ≥4). Pure, deterministic, no I/O. Called by both `write_bundle` and `reseal_bundle`.

**Stays in ARAIL (`world_forge.py` shim + portal):** `arail.router.ModelRouter` construction,
`inference_slot`/`asyncio.to_thread` wrapping, cancel-event plumbing, progress→SSE, and any
`arail.world_theme.parse_world_theme` hard-validation (ARAIL owns the mount-time theme schema, so
the theme validator stays ARAIL-side and is passed *in* to `write_bundle` as an injected
validator to avoid a DaC→ARAIL back-import — a small refactor of the current inline
`from arail.world_theme import parse_world_theme`).

**On the DaC `Serializer` invariant (CLAUDE.md: "Add backends there; never inline a format
elsewhere"):** this invariant governs *wire formats emitted by `dac_compiler`* (TOON/BTOON). A
world-*bundle* is a directory of JSON + a SKILL.md, not a `dac_compiler` wire format, so it does
**not** belong as a `Serializer` backend — forcing it there would be a category error. The
invariant becomes relevant only if we add a TOON projection of a World's terms, in which case the
shared package reuses `dac_compiler.ToonSerializer` rather than re-implementing TOON. Documented
here so the builder does not "helpfully" cram bundle sealing into `get_serializer`.

## Failure modes

| Failure | Detection | Recovery |
|---|---|---|
| **F1 Placeholder/garbage content sealed** (the actual incident: XXXX in `face.json`; survives even `reseal_bundle` because display fields are preserved verbatim) | `validate_bundle_content` run in **both** `write_bundle` and `reseal_bundle` before any file write | Raise `ContentInvalid`, write nothing; operator fixes source content. **Centralizing serialization does NOT fix this by itself — this validator is required regardless of the migration.** |
| **F2 Partial migration state** — some Worlds sealed by old ARAIL sealer, some by new `dac_world` | Compare `manifest.schema`/`compat` (unchanged) + a golden-bundle byte test across both paths | Keep schema fixed; add a one-time `reseal_all` sweep so every existing bundle is re-emitted by the shared path; no bundle-format change means old bundles still verify |
| **F3 Shared dependency breaks ARAIL's build** (bad DaC release, import error) | ARAIL CI import smoke test + pinned dependency version | Pin `dac_world` to a git tag/SHA in ARAIL; rollback = revert the pin; the `world_forge.py` shim can temporarily re-vendor the last-known-good core |
| **F4 DaC→ARAIL back-import leak** (e.g. `from arail.world_theme import ...` inside the moved code) | Static grep in DaC CI: no `import arail` under `dac_world/` | Inject the theme validator as a parameter; keep the core model-free and ARAIL-free |
| **F5 Reseal loses seal-exempt sidecars / breaks atomicity** | Existing `test_reseal_preserves_seal_exempt_sidecars`; add crash-mid-swap test | Preserve current atomic tmp/old/swap-with-rollback logic exactly; do not "simplify" it |
| **F6 Provenance laundering** — a dreamed World relabeled `sourced` via authored face override | `test_face_integrity_fields_force_derived`; integrity fields force-derived last | Keep force-derive-last ordering; override allow-list stays `_FACE_DISPLAY_KEYS` only |
| **F7 SKILL.md injection** via adversarial term/face fields | Existing `test_skill_md_adversarial_fields_contained` (parametrized) must pass against the moved renderer | Keep `sanitize_frontmatter_scalar`/`sanitize_body_field` byte-for-byte |
| **F8 Non-determinism** (created_at, dict ordering) breaks reproducible seals | Existing `test_write_bundle_is_deterministic_with_pinned_created_at` | Keep pinned-`created_at` path; deterministic JSON (`indent=2, ensure_ascii=False`) |
| **F9 Version skew** — ARAIL and DaC disagree on bundle shape after a DaC change | Contract/parity test (below) run in both repos' CI against a shared golden bundle | Bundle schema versioned; breaking changes bump `compat` and require both repos to move together |

## Test strategy

- **Unit (DaC, moved with the code):** port `tests/test_world_forge_seal.py` into DaC's test
  suite against `dac_world` — all 9 existing cases must pass unchanged
  (round-trip verify, determinism, gate-refusal-blocks-sealing, theme-hard-validation,
  integrity-force-derived, reseal-flips-tier, sidecar-preservation, SKILL contract, adversarial
  containment). These are the regression net for the move.
- **Unit (new, the fix):** `validate_bundle_content` — table of positive/negative cases:
  `XXXX`/`YYYY`, `TODO`, `TBD`, `lorem ipsum`, empty-required, repeated-char runs, and
  legitimate strings that must NOT trip (e.g. "kg, J·s", a genuine SI sentence, a term literally
  named "X-ray" must pass). Assert `write_bundle` and `reseal_bundle` both raise `ContentInvalid`
  and write nothing (mirror `test_gate_refusal_blocks_sealing`).
- **Regression (the incident):** a fixture reproducing the physics `face.json` with placeholder
  `domain_framing`/`vocabulary_register`; assert the old path would seal it and the new path
  refuses. This is the "this specific bug never recurs" test.
- **Integration / parity (cross-repo, F2/F9):** a golden `dac.world-bundle/v1` committed to both
  repos; ARAIL's `world_mount.load_bundle + verify_seal + check_compat + check_categories` must
  accept a bundle freshly emitted by `dac_world` and vice-versa. Byte-identical output for a
  pinned `created_at` + fixed spec/terms.
- **Integration (ARAIL side):** ARAIL import smoke test (F3) — `import dac_world` succeeds with
  the pinned version; `world_forge.py` shim re-exports the same public names the portal imports
  (`forge_world`, `write_bundle`, `reseal_bundle`, `render_world_skill`, `GateRefused`).
- **Security:** SKILL.md injection (F7) parametrized set must pass against the moved renderer;
  static check that `dac_world/` contains no `import arail` (F4); confirm `router` is injected and
  the core makes no network/model calls on its own.
- **No performance tier** — forge is model-bound and interactive; sealing is trivial I/O. Not a
  hot path. (Explicitly out of scope.)

## Alternatives considered (for the human sign-off in Assumption #1)

1. **Import `dac_compiler` literally** — rejected: that module has none of the world/bundle logic;
   it would not touch the bug.
2. **Shared `dac_world` package, both repos import (RECOMMENDED).** Kills the duplicate generator,
   one gate + validator. Cost: reverses the "no cross-repo runtime imports" stance; adds a
   cross-repo dependency-pinning workflow.
3. **Keep two generators, add a shared spec + a cross-repo parity/golden test only** (no shared
   code). Honors the existing VISION boundary; still catches drift via CI. Cheaper, weaker — two
   codebases still rot independently, and it does *not* by itself fix F1 (each side needs its own
   validator). Correct fallback if the user declines #2.
4. **Add `validate_bundle_content` to ARAIL's `world_forge.py` only, defer the merge.** This alone
   fixes the actual reported incident with near-zero blast radius. **Recommended as an immediate
   hotfix regardless of whether #2 proceeds** — the migration is the larger, optional structural
   win; the validator is the bug fix.

## Packaging / dependency mechanism (recommendation)

Recommend **an installable package published from `qukaizen-dac` (`dac_world`), consumed by ARAIL
two ways depending on context**, over submodule or vendoring:

- **Local dual-repo dev (this worktree setup):** `pip install -e ~/ProJects/qukaizen-dac` in
  ARAIL's env. Edits in either worktree are live; no sync step. Best for someone editing both.
- **ARAIL reproducible builds / CI:** pin a git dependency to a tag or SHA
  (`dac_world @ git+https://.../qukaizen-dac@<tag>`). Rollback = move the pin (F3).
- **Rejected: git submodule** — brittle with the worktree layout, easy to forget to bump, mixes
  two repos' histories. **Rejected: vendoring/copy-with-sync-script** — recreates exactly the
  drift problem we are trying to kill (it *is* the current state, just automated).

DaC needs a minimal `pyproject.toml` exposing `dac_world` as a package (it already has
`package.json`/`tsconfig.json` for the TS side; the Python side is currently loose top-level
modules). Keep `dac_compiler.py` importable as before.

## Tech debt

**Added:**
- A cross-repo runtime dependency where there was a clean artifact-only boundary (reverses a
  VISION stance; needs an ADR in both repos).
- A new `pyproject.toml`/package boundary in DaC for the previously-loose Python modules.
- Two dependency-consumption modes (editable local vs pinned git) to keep documented.
- The DaC **TypeScript** forge (`scripts/forge-world.mts` et al.) becomes a *fourth* copy of the
  logic unless it is also retired or explicitly designated the "reference/other-language" impl.
  Flag: decide its fate, don't leave three Python + one TS.

**Repaid:**
- Eliminates the duplicate Python generator (`world_forge.py` core) — one gate, one sealer, one
  place for correctness.
- Adds the `validate_bundle_content` gate that closes the actual incident class (and is worth
  shipping even standalone).
- Removes the "byte-parity is a NON-goal" divergence risk between DaC and ARAIL bundles.

**Net:** roughly neutral-to-negative *if* the shared package lands with the validator and the TS
forge's fate is decided; **positive (bad)** if the shared dependency lands without retiring/renaming
the other copies, leaving more implementations than we started with. The validator-only hotfix
(Alternative #4) is unambiguously net-negative debt (pure repaid).

**Cross-repo bookkeeping this change requires (call-outs, per workspace convention):**
- Update `qukaizen-dac/CLAUDE.md` (new `dac_world` package + note that the `Serializer` invariant
  does *not* absorb bundle sealing).
- Update `qukaizen-arail/CLAUDE.md` (the DaC boundary now has a runtime-import exception) and add
  a superseding ADR alongside `docs/adr/0002-chat-memory-and-the-dac-boundary.md`.
- Update DaC `VISION.md` / `BLUEPRINT.md` lines asserting "World generation is an ARAIL
  capability … no cross-repo runtime imports."
- Mirror this ARCHITECTURE.md into `qukaizen-arail/sprints/2026-07-19-dac-generates-arail-worlds/`
  since the build touches both repos.

## Recommended implementation order

1. **Ship the hotfix first (Alternative #4):** add `validate_bundle_content` to ARAIL's
   `world_forge.py`, wired into `write_bundle` + `reseal_bundle`, with the incident regression
   test. This fixes the reported bug immediately, independent of the migration decision.
2. **Get the human sign-off on Assumption #1** (reversing the boundary). If declined, stop here and
   do Alternative #3 (shared golden/parity test) instead.
3. Stand up `dac_world` package skeleton in qukaizen-dac (`pyproject.toml`, empty package).
4. Move the pure core (`gate`, `provenance`, `forge`, `seal`, `skill`, plus the new `validate`)
   from `world_forge.py` into `dac_world`, injecting the theme validator to keep it `arail`-free.
5. Port `tests/test_world_forge_seal.py` into DaC CI against `dac_world`; all 9 pass.
6. Turn ARAIL `world_forge.py` into a re-export shim; add the ARAIL import smoke test.
7. Add the cross-repo golden-bundle parity test in both CIs.
8. One-time `reseal_all` sweep of existing `lab/worlds/*` through the shared path; confirm every
   bundle still `verify_seal`s.
9. Decide the TS forge's fate; update both `CLAUDE.md`s, the DaC VISION/BLUEPRINT lines, the ADRs,
   and mirror this doc into ARAIL's sprints.

## Addendum: sidecar-preservation policy (step 8 follow-up)

**Date:** 2026-07-19 · **Trigger:** BUILD_LOG "Step 8: reseal_all sweep — STOPPED, genuine gap
found." `reseal_bundle` preserves seal-exempt sidecars via a hardcoded two-name allow-list
(`model.json`, `review.json`); the `reseal_all` sweep silently dropped `evolution.json` (Growth
Engine log) and `librarian-scout.json` (Librarian mining state) on the `ai` World. Pre-existing
bug, traced to `qukaizen-arail@5c45f58`, carried verbatim into `dac_world/seal.py`.

### Chosen option: C — invert the allow-list to a **regenerated-set denylist**, plus warn-loud

Preserve **every** file present in `bundle_dir` that `write_bundle` does **not** itself regenerate;
emit a warning naming any preserved file outside a known-sidecar set. This is Option A's
"generalize" with the warn-and-preserve hybrid bolted on so the operation fails *loud*, never
silent — in either direction (dropped data or an unexpected survivor).

### Why not B (extend the explicit allow-list)

B re-arms the exact trap that just fired. The failure mode here is **silent data loss on first
reseal for the next sidecar-producing feature**, and B leaves it fully open: Growth Engine already
tripped it once by forgetting to edit this list; the Librarian scout is a second instance in the
same directory. A defense that requires every future feature author to remember to edit a list in a
*different* module (the sealer) than the one they're working in is a defense that will be forgotten
again. This is a maintenance-burden failure mode with a realized incident — weight it as such.

### Why generalizing is acceptable here (threat model)

The stated cost of A/C is a containment-posture change: an unrecognized file now survives reseal
indefinitely instead of being dropped. Assessed against ARAIL's actual stance:

- **Single-user, local, non-multi-tenant by charter.** ARAIL Worlds are forged and resealed on the
  user's own machine; there is no `user_id` and, per the workspace's "arail-never-multi-user"
  constraint, never will be. A file in one's own bundle directory is not an adversary-supplied
  artifact crossing a trust boundary — it is the operator's own filesystem.
- **Reseal does not execute or interpret sidecars.** It byte-copies them across the atomic swap.
  A "malicious" survivor gains nothing from surviving that it didn't already have by sitting in the
  directory; there is no privilege escalation, no deserialization, no code path fed by it.
- **Content correctness is already gated elsewhere.** The sealed, load-bearing files
  (`terms.json`/`face.json`/…) are the ones that flow into Buddy's context; those go through
  `validate_bundle_content` and `verify_seal`. Sidecars are auxiliary state, not context payload.
- **The competing risk is worse and already realized.** Silently *deleting* the Growth Engine's
  reversible-changes log is concrete data loss that happened in this sprint; "a stray file
  survives" is hypothetical and low-consequence. Between a false-negative that destroys legitimate
  state and a false-positive that keeps an inert file, keep the file.

The warn-loud step recovers the only real benefit B had (visibility of unexpected files) without
its data-loss cost: a stray/misnamed file is surfaced in logs rather than silently made permanent.

### Function contract change (`dac_world/seal.py`)

Add a module constant naming exactly what `write_bundle` regenerates (derive it from the sealer's
own output set so the two cannot drift):

```python
# Files write_bundle emits itself; everything else in a bundle dir is a sidecar to carry over.
REGENERATED_FILES = frozenset(SEALED_FILES) | {
    "manifest.json", "SKILL.md", "capabilities.json", "arail-plugin.json",
}
# Sidecars we know about — presence is expected, no warning. Unknown survivors are still
# preserved, but warned about so a stray/misnamed file fails loud instead of silent.
KNOWN_SIDECARS = frozenset({"model.json", "review.json", "evolution.json", "librarian-scout.json"})
```

Replace the hardcoded loop in `reseal_bundle`:

```python
# Carry over every file the sealer does not regenerate (sidecars + any nested state).
for entry in bundle_dir.iterdir():
    if entry.name in REGENERATED_FILES:
        continue
    if entry.name not in KNOWN_SIDECARS:
        logging.getLogger("dac_world.seal").warning(
            "reseal_bundle: preserving unrecognized bundle file %r in %s "
            "(not a regenerated output; carried over verbatim)", entry.name, bundle_dir.name)
    dest = tmp / entry.name
    if entry.is_dir():
        shutil.copytree(entry, dest)
    else:
        shutil.copy2(entry, dest)
```

- **Signature:** unchanged — `reseal_bundle(bundle_dir, terms=None, *, theme_validator=None) -> Path`.
- **Behavior change:** sidecar preservation goes from "copy the 2 named files if present" to
  "copy every non-regenerated entry, warning on any not in `KNOWN_SIDECARS`." Handle nested dirs
  (`copytree`) so a future sidecar *directory* is preserved too; the old two-name loop only handled
  flat files.
- **Ordering unchanged:** still runs after `write_bundle(tmp, …)` and before the atomic
  `os.rename` swap — F5's atomicity and rollback are untouched (do not "simplify" the swap, per F5).
- **`KNOWN_SIDECARS` is advisory only** (controls the warning), not a gate — a name missing from it
  never causes data loss, which is the whole point. Adding `evolution.json`/`librarian-scout.json`
  there just silences the warning for the two known-legitimate cases.

### Test coverage that would not have missed this

The gap survived because `test_reseal_preserves_seal_exempt_sidecars` only exercised `review.json` —
a name already in the old list. The new tests must assert the **policy**, not a fixed name set:

1. **`test_reseal_preserves_arbitrary_sidecar`** — seed a sidecar with a name the code has *never*
   heard of (e.g. `totally-novel-sidecar.json`), reseal, assert it survives byte-for-byte. This is
   the load-bearing one: it fails under Option B and every future variant of this bug, and passes
   only under a generalized policy. Parametrize it over `evolution.json`, `librarian-scout.json`,
   and at least one invented name so the test cannot be satisfied by extending an allow-list.
2. **`test_reseal_warns_on_unknown_sidecar`** — with `caplog`, assert the warning fires for the
   invented name and does **not** fire for a `KNOWN_SIDECARS` entry.
3. **`test_reseal_regenerated_files_not_treated_as_sidecars`** — assert `manifest.json`/`SKILL.md`/
   `capabilities.json`/`arail-plugin.json` are the freshly regenerated versions after reseal, not
   stale carried-over copies (guards against `REGENERATED_FILES` drifting out of sync with what
   `write_bundle` actually emits — if the sealer adds a new output, this test catches the omission).
4. **`test_reseal_preserves_sidecar_directory`** — seed a nested sidecar *directory*, assert it
   survives (the old flat-file loop would silently drop it; the new `copytree` branch covers it).

Update the existing `test_reseal_preserves_seal_exempt_sidecars` to keep passing (it stays valid as
a `review.json`-specific case) — no existing assertion regresses.

### Sweep resumption

Once implemented and green, step 8's `reseal_all` sweep can proceed on all four bundles, `ai`
included. Expect a benign warning-free reseal for `ai` after adding the two names to
`KNOWN_SIDECARS`; `evolution.json` and `librarian-scout.json` must be present and byte-identical in
the resealed `ai` bundle (diff against the pre-sweep backup to confirm).

### Tech-debt delta

**Repaid:** closes the silent-sidecar-drop bug *class* permanently — no future feature can lose its
sidecar on first reseal, and none needs to edit the sealer to be safe. **Added:** near-zero;
`KNOWN_SIDECARS` is a cosmetic warning-suppression list whose staleness is harmless by construction
(worst case: a spurious but truthful warning). Net: negative (good). No follow-up ticket required.
