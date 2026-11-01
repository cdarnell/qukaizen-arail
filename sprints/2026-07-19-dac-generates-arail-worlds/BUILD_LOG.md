# Build log: `validate_bundle_content` hotfix (Alternative #4 / step 1)

**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `qukaizen-dac@38aa690`
**Scope:** ONLY Alternative #4 / step 1 of the "Recommended implementation order" —
the standalone content validator in ARAIL's existing `world_forge.py`. The `dac_world`
package migration (steps 2–9) is explicitly out of scope for this build and requires
the human sign-off on Assumption #1 (reversing the "no cross-repo runtime imports"
stance) called out in the architecture doc — not attempted here.
**Started:** 2026-07-19
**Repo actually modified:** `qukaizen-arail` (working tree at `~/ProJects/qukaizen-arail`,
branch `qukaizen/arail-world-corpus-nucleus`) — a separate repo from qukaizen-dac. This
BUILD_LOG lives in qukaizen-dac's sprint folder per the architecture doc's own
cross-repo bookkeeping note ("mirror this ARCHITECTURE.md into
`qukaizen-arail/sprints/...`" — the build artifact here is the mirror-equivalent for
the build phase).

## Plan

| # | Files | Change | Test | Commit ref |
|---|---|---|---|---|
| 1 | `qukaizen-arail/src/arail/world_forge.py` | Add `ContentInvalid` exception (mirrors `GateRefused`); add `validate_bundle_content(face, spec, terms) -> None`; wire into `write_bundle` (before any `sealed_bytes` write) and `reseal_bundle` (on preserved `overrides`, before calling `write_bundle`) | New unit tests in the same PR | `2eb41ea` |
| 2 | `qukaizen-arail/tests/test_world_forge_seal.py` | Regression test reproducing the actual incident (XXXX/YYYY in face overrides) for both `write_bundle` and `reseal_bundle`; parametrized negative cases (TODO/TBD/lorem ipsum/placeholder/empty/repeated-char-run); positive-path case with legitimate SI/unit prose + a term named "X-ray" | Test-first is N/A here (validator and tests were written together per the architecture's "Unit (new, the fix)" table) | `2eb41ea` |

Single atomic commit — the validator and its tests are one logical change (a
production function with no test coverage isn't "done," and the tests have no
meaning without the function they exercise).

## Execution

### Step 1: `ContentInvalid` exception + `validate_bundle_content`

Added directly below `GateRefused` in `world_forge.py`, following the same
constructor-carries-detail pattern (`self.violations: list[str]` instead of
`self.gate`).

`validate_bundle_content(face, spec, terms)` checks:
- `face`'s four free-text display keys: `name`, `tagline`, `domain_framing`,
  `vocabulary_register` (the `_FACE_DISPLAY_KEYS` allow-list minus `palette_hint`/
  `theme`, which aren't prose — `palette_hint` is a token like `"slate-violet"` and
  `theme` is a structured, separately-validated block, not display text).
- each term's `short` (required), `definition` (required), `example` (optional —
  the forge already legitimately leaves `example` blank on a bad model call, per
  the existing `forge_world` DEFINE stage; making it required would make the
  validator reject output the forge itself already tolerates).
- `spec` is accepted as a parameter for interface parity with the architecture
  doc's exact signature (`validate_bundle_content(face, spec, terms) -> None`) but
  is not read today — no field on `spec` (categories, knowledge_sources) is
  free-text prose that could carry placeholder content the way `face`/`terms`
  fields can.

Rules implemented, in check order (first match wins per field, but all fields
are checked before raising — one `ContentInvalid` carries every violation found,
not just the first):

1. `^[XY]{3,}$` (case-insensitive), matched against the **full stripped string**,
   not searched as a substring. This was the deliberate design point flagged in
   the task: anchoring to the whole string is what lets `"X-ray"` pass (it has
   non-X/Y characters) while `"XXXX"`/`"yxyxyx"` fails.
2. `\bTODO\b` (case-sensitive, per the architecture doc's literal pattern).
3. `\bTBD\b` (case-sensitive).
4. `lorem ipsum` (case-insensitive).
5. `\bplaceholder\b` (case-insensitive).
6. empty-after-strip, only flagged where the field is required (`short`/
   `definition`/all four face keys); `example` is exempt.
7. `(\W)\1{3,}` — a run of ≥4 of the same non-word character (e.g. `"----"`,
   `"...."`). `\W` (not-a-word-char) rather than a hand-picked set of symbols so
   the check generalizes, and because it only fires on a *repeated single
   character*, it does not trip on legitimate short-run prose like `"kg, J·s"`
   (no 4-run of one non-word char there) or normal punctuation.

### Step 2: wiring

- `write_bundle`: `validate_bundle_content(face, spec, terms)` inserted
  immediately after `face = _build_face(...)` and before `agenda = {...}` /
  `out_dir.mkdir(...)` / any `sealed_bytes` write — confirmed by re-reading the
  function body that `mkdir` genuinely happens after the validator call, so a
  raise leaves the target directory absent, not partially populated.
- `reseal_bundle`: `validate_bundle_content(overrides, spec, terms)` inserted
  immediately after `overrides` is built from the existing `face.json` and
  before the `tmp`/`old` sibling-directory dance — so a raise happens before
  any temp directory is created, before `os.rename`, i.e. strictly before the
  atomic-swap machinery starts. This directly targets Failure F1: reseal used
  to preserve `_FACE_DISPLAY_KEYS` verbatim with no content check, which is
  exactly how the incident's XXXX/YYYY survived a `reseal_bundle` call.

### Step 3: tests

Added to `tests/test_world_forge_seal.py` (matched existing style: module-level
`SPEC`/`_terms()` fixtures, `tmp_path`, `pytest.raises`, no new fixtures
introduced):

- `test_write_bundle_refuses_placeholder_face_content` — the incident
  reproduction for `write_bundle`: `face_overrides` with long `XXXX...`/`YYYY...`
  runs in `domain_framing`/`vocabulary_register`; asserts `ContentInvalid` and
  that the target directory was never created.
- `test_reseal_bundle_refuses_placeholder_face_content` — the incident
  reproduction for `reseal_bundle`: writes a valid bundle, hand-corrupts
  `face.json` on disk with the same XXXX/YYYY runs (simulating a prior bad
  write or hand-edit), then calls `reseal_bundle` on it; asserts
  `ContentInvalid`, that `manifest.json` bytes are unchanged (no swap occurred),
  and that no `.{name}.reseal-tmp` / `.{name}.reseal-old` sibling directories
  were left behind.
- `test_write_bundle_refuses_placeholder_term_content` (parametrized, 6 cases) —
  `TODO`, `TBD`-with-"placeholder"-in-the-same-string, `lorem ipsum`, the word
  `placeholder`, a `"----"` repeated-char run, and a whitespace-only required
  field — each on a term's `definition`/`short`, one case per parametrization,
  asserting `ContentInvalid` and that nothing was written.
- `test_write_bundle_accepts_legitimate_scientific_content` — the positive-path
  regression the task called out explicitly: a term literally named `X-ray`
  with realistic definition/short/example text, plus `face_overrides` containing
  `"kg, J·s"`-style unit strings in `domain_framing`/`vocabulary_register`;
  asserts `write_bundle` succeeds and the bundle still passes ARAIL's own
  `verify_seal`.

### Test run

```
$ ./.venv/bin/python -m pytest tests/test_world_forge_seal.py -v
...
23 passed in 0.06s
```

All 9 pre-existing tests pass unchanged; all 14 new tests pass. No existing
test needed modification.

**Full-suite run scoped down.** `qukaizen-arail`'s complete `tests/` directory
(hundreds of tests, several minutes, multi-GB RSS in this environment) has
pre-existing, unrelated failures visible in this worktree independent of this
change — the working tree already carries unrelated uncommitted edits to
`lab/tools/benchmark_models.py`, `lab/worlds/ai/evolution.json`,
`src/arail/portal/static/graph.css`, several portal templates, and
`tests/test_world_qa_probes.py` (none touched by this build; left untouched and
unstaged per the "no scope drift" rule). Running the entire suite is not part
of this scoped hotfix's regression net and was not required to validate this
change; the targeted regression net is `tests/test_world_forge_seal.py`
(the file the architecture doc names as the regression net for this exact
fix), which is green.

Commit: `2eb41ea` — `fix(world-forge): validate_bundle_content refuses
placeholder content before sealing` (in `qukaizen-arail`, branch
`qukaizen/arail-world-corpus-nucleus`).

## Architect feedback required

None. The plan as scoped (Alternative #4 only) matched the code as found —
`_FACE_DISPLAY_KEYS`, `write_bundle`, and `reseal_bundle` all existed exactly as
the architecture doc's Interface Contracts section described them, and the
insertion points were unambiguous.

## Final state

- Files changed: 2 (`src/arail/world_forge.py`, `tests/test_world_forge_seal.py`)
  in `qukaizen-arail`.
- Lines changed: +160 / -0 (pure addition — no existing line was modified).
- New exception: `ContentInvalid`.
- New public function: `validate_bundle_content(face, spec, terms) -> None`.
- Tests: 9 pre-existing + 14 new = 23, all passing in
  `tests/test_world_forge_seal.py`.
- Failure modes closed: F1 (per the architecture doc's Failure-modes table) —
  both the `write_bundle` and `reseal_bundle` paths now refuse
  placeholder-shaped content before any file write.
- Not done (by design, per scope): the `dac_world` package migration (steps
  2–9 of the architecture doc's recommended order), any cross-repo dependency
  wiring, and the two repos' `CLAUDE.md`/`VISION.md` bookkeeping updates that
  migration would require. Those remain gated on the human sign-off on
  Assumption #1.

---

# Build log part 2: `dac_world` migration (steps 3–8)

**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `qukaizen-dac@38aa690`
**Scope:** steps 3–8 of the "Recommended implementation order." Step 1 (hotfix,
above) and step 2 (human sign-off on Assumption #1) are done. Step 9
(CLAUDE.md/VISION.md/ADR bookkeeping, TS-forge fate) is explicitly deferred.
**Started:** 2026-07-19

## Plan

| # | Files | Change | Test | Commit ref |
|---|---|---|---|---|
| 3 | `qukaizen-dac/pyproject.toml` (new), `qukaizen-dac/dac_world/__init__.py` (new, empty skeleton), `.gitignore` | Package skeleton so `dac_world` is pip-installable (editable) without touching `dac_compiler.py`/`lancedb_sink.py` | none (skeleton only) | TBD |
| 4 | `qukaizen-dac/dac_world/{parsing,gate,provenance,forge,skill,seal,validate,reconcile}.py`, `qukaizen-dac/dac_world/__init__.py` | Move `world_forge.py`'s pure core verbatim, split across the named modules; inject `theme_validator` into `_build_face`/`write_bundle`/`reseal_bundle`; remove the `from arail.router import ModelRouter` fallback in `forge_world` (replaced with `ValueError` — see gap note below) | none yet (ported in step 5) | TBD |
| 5 | `qukaizen-dac/tests/python/test_world_forge_seal.py` (new), `qukaizen-dac/.venv-dacworld` (local, gitignored) | Port all 23 seal tests to import from `dac_world`, adapted for the injected `theme_validator` | run via pytest | TBD |
| 6 | `qukaizen-arail/src/arail/world_forge.py` (rewritten as shim), `qukaizen-arail/src/arail/portal/world_routes.py` (theme_validator wiring at 3 call sites), `qukaizen-arail/pyproject.toml` (dep), `qukaizen-arail/tests/test_dac_world_shim_smoke.py` (new), `qukaizen-dac/tests/python/test_no_arail_backimport.py` (new) | Shim + smoke test + static F4 check | pytest both repos | TBD |
| 7 | `qukaizen-dac/tests/python/fixtures/golden-bundle/*`, `qukaizen-dac/tests/python/test_golden_bundle_parity.py`, `qukaizen-arail/tests/fixtures/golden-bundle/*` (same files), `qukaizen-arail/tests/test_golden_bundle_parity.py` | Byte-identical golden bundle round-trips both repos | pytest both repos | TBD |
| 8 | none (operational sweep) | `reseal_all` sweep of `qukaizen-arail/lab/worlds/*` through the shared path | manual verify_seal check per bundle | TBD (no commit if no files changed; a commit only if bundles' bytes changed) |

## Scope note / non-blocking deltas from the architecture doc (not a "stop the
build" gap — documented per the "when the plan is wrong" protocol, but the
core promise of the doc is intact for all of these)

1. **Package layout has more files than the doc's illustrative list.** The
   doc names 6 files (`forge.py`, `gate.py`, `provenance.py`, `seal.py`,
   `skill.py`, `validate.py`). `world_forge.py`'s actual public surface is
   larger — ARAIL's portal/librarian_scout/tests use `wf.<name>` for the
   Curator judge and growth loop (`reconcile_terms`, `apply_corrections`,
   `propose_new_terms`, `ReviewFlag`, `goal_suggestions`), tolerant parsing
   helpers (`loose_json`, `first_array`, `slugify`), and constants
   (`MAX_SHORT` etc). All of these are pure/model-free/stdlib-only and follow
   the exact same injectable-router pattern the doc already sanctions for
   `forge_world`. Moving them alongside the named 6 (into an added
   `dac_world/parsing.py` and `dac_world/reconcile.py`) is the only way the
   shim stays genuinely thin per step 6's own instruction ("re-exporting
   exactly the public names ARAIL's portal currently imports"). Leaving them
   duplicated in the shim would recreate the drift problem this migration
   exists to kill. No interface contract or failure mode in the doc is
   violated by this — flagging it here rather than treating it as silent
   scope expansion.
2. **`forge_world`'s `router=None` fallback constructed `arail.router.ModelRouter`
   inline.** This is a literal `import arail` inside the code being moved,
   which directly violates Failure F4 ("no `import arail` under `dac_world/`").
   No architecture text sanctions or forbids this specific fallback — it's a
   pre-existing convenience default, not a call site the doc discusses.
   Resolution: `router` becomes a required parameter in `dac_world.forge_world`
   (raises `ValueError` if `None`) instead of defaulting to ARAIL's router.
   Verified no test or call site in ARAIL relies on the `None`-constructs-
   `ModelRouter` behavior (`grep forge_world(` — every call site passes
   `router=` explicitly).
3. **Theme-validator injection requires updating 3 ARAIL call sites, not
   just the shim.** `write_bundle`/`reseal_bundle` now take an optional
   `theme_validator` kwarg. ARAIL's portal (`world_routes.py`) calls
   `wf.write_bundle` once and `wf.reseal_bundle` twice (via `_reseal_and_swap`
   and the grow-loop); all three now pass
   `theme_validator=arail.world_theme.parse_world_theme` explicitly. If no
   validator is injected and a `theme` override is present, `write_bundle`
   raises `ValueError` (fail closed, matching the existing "theme validated
   HARD before sealing" stance) rather than silently skipping validation.

## Execution

### Step 3: `dac_world` package skeleton

`pyproject.toml` (new, qukaizen-dac root) exposes `dac_world` as an
installable package (`[tool.setuptools.packages.find] include =
["dac_world", "dac_world.*"]`), stdlib-only, Apache-2.0. `dac_world/__init__.py`
started as a documented-empty skeleton. `dac_compiler.py`/`lancedb_sink.py`/
`world_to_toon.py` untouched, confirmed still importable directly.
Verified `pip install -e .` into a scratch venv (`.venv-dacworld/`, gitignored)
succeeds and `import dac_world` resolves.

Commit: `a463eca` (qukaizen-dac) — `feat(dac_world): stand up package
skeleton (step 3)`.

### Step 4: move the pure core

Moved `world_forge.py`'s model-free, stdlib-only code into
`dac_world/{parsing,gate,provenance,forge,skill,seal,reconcile,validate}.py`,
verbatim except for the two deltas required by Failure F4 (documented in
the "Scope note" above and repeated in each affected module's docstring):
`forge_world`'s `router=None` fallback no longer constructs
`arail.router.ModelRouter` (raises `ValueError` instead), and `_build_face`'s
theme hard-validation takes an injected `theme_validator` callable instead
of `from arail.world_theme import parse_world_theme`. `dac_world/__init__.py`
re-exports the full surface (grepped from every ARAIL call site, not
guessed — see step 6). `grep -rn "import arail\|from arail" dac_world/`
returns only docstring prose, confirmed by eye.

Commit: `19a0430` (qukaizen-dac) — `feat(dac_world): move
world_forge.py's pure core into dac_world (step 4)`.

### Step 5: port the seal test suite

Ported all 23 tests from `qukaizen-arail/tests/test_world_forge_seal.py`
into `qukaizen-dac/tests/python/test_world_forge_seal.py`, importing from
`dac_world`. One behavioral adaptation (documented in the file's own
docstring): `test_face_theme_override_validated_hard` now supplies a small
fake `theme_validator` rather than `arail.world_theme.parse_world_theme`
directly, since `dac_world` never imports `arail`. Added one new test for
the F4 delta itself (`test_face_theme_override_without_injected_validator_
fails_closed`) — 24 tests total.

The round-trip-against-ARAIL's-own-consumer assertions are a genuine
cross-repo test-only dependency (not a `dac_world` runtime dependency):
`tests/python/conftest.py` adds the `~/ProJects/qukaizen-arail/src`
sibling onto `sys.path` and skips (`requires_arail` marker) if ARAIL isn't
importable, so `dac_world`'s own unit tests never require ARAIL to be
present.

Ran and confirmed 24/24 passing via two interpreters: (a) a bare
`python3.11 -m venv` with only `pytest` installed (`.venv-dacworld/` —
proves `dac_world`'s own tests don't secretly need ARAIL), and (b) ARAIL's
own `.venv` with `dac_world` editable-installed into it (proves the
cross-repo round-trip assertions are real, not skipped).

Commit: `6f1a6d4` (qukaizen-dac) — `test(dac_world): port the seal test
suite into DaC CI (step 5)`.

### Step 6: ARAIL shim + theme-validator wiring + F3/F4 checks

- **qukaizen-arail** `src/arail/world_forge.py` rewritten as a thin
  re-export shim over `dac_world`. The re-export list was built by
  grepping every `wf.<name>` attribute access and every
  `from arail.world_forge import <name>` across `src/` and `tests/` —
  not guessed (see the module's own docstring for the audit trail).
- `src/arail/portal/world_routes.py`: added
  `from arail.world_theme import parse_world_theme` and passed
  `theme_validator=parse_world_theme` explicitly at its 3 call sites
  (`write_bundle` in `api_forge_confirm`, `reseal_bundle` in
  `_reseal_and_swap`, `reseal_bundle` in the grow loop).
- `pyproject.toml` (qukaizen-arail): added `dac_world` to `dependencies`
  as a pinned git dependency on the in-progress migration branch
  (`qukaizen/hungry-bouman-d0761f`), documented as superseded locally by
  `pip install -e ~/ProJects/qukaizen-dac` for dual-repo dev. **Flagged
  for follow-up:** this pin must move to a tag once the branch merges to
  `main` — noted as documentation, not left as an unowned TODO.
- `tests/test_dac_world_shim_smoke.py` (new, qukaizen-arail): asserts
  `import dac_world` succeeds, the shim re-exports every expected name,
  and `forge_world(router=None)` raises `ValueError` (the F4 fix, not a
  silent `arail.router` construction).
- `tests/python/test_no_arail_backimport.py` (new, qukaizen-dac): an
  AST-based (not substring) grep asserting no `import`/`from ... import`
  statement under `dac_world/` ever names `arail`/`arail.*` — permanent
  CI guard for Failure F4.

Test runs: `tests/test_world_forge_seal.py` + `test_world_forge_gate.py` +
`test_world_forge_pipeline.py` + the new smoke test = 62/62 passing
(ARAIL's `.venv`). `test_world_forge_api.py` + `test_world_growth.py` +
`test_world_bootstrap_wikipedia.py` = 34/34 passing. DaC's
`tests/python/` (25 tests incl. the new F4 check) = 25/25 passing.

Commits: `092e8ec` (qukaizen-dac) — `test(dac_world): static CI check
for Failure F4 (step 6)`; `a6dc2a0` (qukaizen-arail) —
`refactor(world-forge): turn world_forge.py into a dac_world re-export
shim (step 6)`.

### Step 7: cross-repo golden-bundle parity test

Generated one golden `dac.world-bundle/v1` fixture via `dac_world.write_bundle`
with a fixed spec/two-term corpus and pinned `created_at`
(`1970-01-01T00:00:00.000Z`), committed byte-identically to both repos
(`qukaizen-dac/tests/python/fixtures/golden-bundle/`,
`qukaizen-arail/tests/fixtures/golden-bundle/` — verified with
`diff -rq` before committing either copy). Each repo carries a test
asserting: (a) the committed fixture round-trips its own
`world_mount.load_bundle`/`verify_seal`/`check_compat`/`check_categories`,
and (b) a bundle freshly emitted with the same pinned inputs — via
`dac_world.write_bundle` directly on the DaC side, via the `world_forge`
shim on the ARAIL side — is byte-identical to the committed fixture.

Ran both: DaC side 3/3 passing, ARAIL side 2/2 passing.

Commits: `8cb0d74` (qukaizen-dac) — `test(dac_world): cross-repo
golden-bundle parity test (step 7)`; `859f577` (qukaizen-arail) —
`test(world-forge): cross-repo golden-bundle parity test (step 7)`.

### Step 8: `reseal_all` sweep — STOPPED, genuine gap found

Backed up `qukaizen-arail/lab/worlds/{ai,photography,physics,qukaizen}/`
(no `mapswipe-triage` directory exists on disk — the instruction's list
was hypothetical) to a scratch dir, confirmed all four currently
`verify_seal` cleanly, then ran each through `arail.world_forge.reseal_bundle`
(the shim, i.e. the shared `dac_world` path) with
`theme_validator=parse_world_theme` injected, exactly as ARAIL's own
call sites now do.

**Result:** `photography`, `physics`, and `qukaizen` resealed cleanly and
re-verified (`verify_seal().ok is True` for all three, byte-for-byte
sidecar preservation confirmed by diff against the backup). **`ai` lost
two files: `evolution.json` and `librarian-scout.json`.** Both are
legitimate seal-exempt sidecars (`evolution.json` is the World Growth
Engine's reversible-changes log; `librarian-scout.json` is the Librarian's
candidate-mining state) that `reseal_bundle`'s sidecar-preservation step
does not know about — it hardcodes exactly two names:

```python
for extra in ("model.json", "review.json"):
    if (bundle_dir / extra).exists():
        shutil.copy2(bundle_dir / extra, tmp / extra)
```

**This bug predates the migration** — verified present, byte-identical,
at `qukaizen-arail@2eb41ea` (the step-1 hotfix commit, before any code
moved) and traced to `5c45f58` ("World Growth Engine — agents evolve the
World"), which added `evolution.json` without extending this allow-list.
I moved this function **verbatim** per the architecture doc's instruction
("Move the pure core... verbatim where possible") and per Failure F5's
explicit recovery guidance ("preserve current atomic tmp/old/swap-with-
rollback logic exactly; do not 'simplify' it") — so the bug is faithfully
present in `dac_world/seal.py` too, not introduced by me, but reachable
for the first time in a *sweep* rather than a single edit, and I triggered
it for real against the live `ai` World in this environment.

**I restored `lab/worlds/ai/` from the pre-reseal backup immediately**
(`cp -R` from the scratch backup back over the resealed directory) and
confirmed via `git status`/`git diff` that the tree is back to exactly its
pre-step-8 state (the only diff is the pre-existing, pre-session
`evolution.json` timestamp edit already noted in this BUILD_LOG's step-1
section). **No data was lost; `git status` in qukaizen-arail after
restoration shows no trace of the sweep.**

**I am stopping step 8 here rather than improvising a fix**, because
closing this gap requires a design decision the architecture doc doesn't
make: should `reseal_bundle` (a) generalize to "preserve any file present
in the bundle dir that isn't one of the sealed/derived outputs" (simple,
but silently preserves anything — including a stray malicious or
accidental file, forever), or (b) maintain an explicit, growing allow-list
that every new sidecar-producing feature (Growth Engine, Librarian scout,
whatever comes next) must remember to extend (safer by default, but is
exactly the kind of "forgot to update a list" bug that caused this), or
(c) something else (a sidecar manifest/contract file)? This is a real
product/security tradeoff, not an implementation detail — it belongs to
the architect, not to a builder mid-sweep.

## Architect feedback required

**New finding (step 8, not present in the original ARCHITECTURE.md):**
`dac_world.seal.reseal_bundle`'s seal-exempt-sidecar preservation is an
incomplete, hardcoded allow-list (`model.json`, `review.json`) that
silently drops any OTHER sidecar file a bundle happens to carry —
concretely, `evolution.json` (World Growth Engine log) and
`librarian-scout.json` (Librarian candidate-mining state) on the `ai`
World. This is a pre-existing bug (present before this migration,
introduced by `qukaizen-arail@5c45f58`), faithfully carried forward
verbatim per this migration's own "move verbatim" / Failure-F5 "don't
simplify the reseal logic" instructions. It was not caught by the
existing `test_reseal_preserves_seal_exempt_sidecars` test because that
test only exercises `review.json`.

**Needs an architect decision before step 8 (the `reseal_all` sweep) can
safely proceed on the `ai` World specifically** (the other three bundles —
`photography`, `physics`, `qukaizen` — have no extra sidecars today and
resealed/verified cleanly; I did not leave them resealed, since re-running
the sweep asymmetrically across bundles pending a decision seemed like
exactly the kind of undocumented judgment call this protocol asks me to
avoid — everything in `lab/worlds/` is back to its pre-step-8 state):

1. Should `dac_world.seal.reseal_bundle`'s sidecar-preservation generalize
   to "copy every file present in `bundle_dir` that isn't one of the
   6 sealed files, `SKILL.md`, `capabilities.json`, or `arail-plugin.json`"
   (closes this bug class permanently, but preserves unknown files by
   default — a containment-posture change worth the architect's paranoid
   review given `dac_world`'s security stance elsewhere), or should the
   allow-list simply be extended to include `evolution.json` and
   `librarian-scout.json` explicitly (matches today's incident exactly,
   but leaves the "forgot to add a new sidecar name" failure mode open
   for the next feature)?
2. Once that's decided and implemented (with a test — at minimum,
   parametrize `test_reseal_preserves_seal_exempt_sidecars` over
   `evolution.json`/`librarian-scout.json` in addition to `review.json`),
   step 8's sweep should be re-attempted for `ai` specifically (the other
   three bundles can be swept immediately once step 8 resumes, since they
   already demonstrated a clean reseal+verify).

## Final state (through step 7; step 8 blocked — see above)

**qukaizen-dac commits (this build, in order):**
- `c7a3969` docs(sprint): BUILD_LOG plan for dac_world migration (steps 3-8)
- `a463eca` feat(dac_world): stand up package skeleton (step 3)
- `19a0430` feat(dac_world): move world_forge.py's pure core into dac_world (step 4)
- `6f1a6d4` test(dac_world): port the seal test suite into DaC CI (step 5)
- `092e8ec` test(dac_world): static CI check for Failure F4 (step 6)
- `8cb0d74` test(dac_world): cross-repo golden-bundle parity test (step 7)

**qukaizen-arail commits (this build, in order, on branch
`qukaizen/arail-world-corpus-nucleus`):**
- `a6dc2a0` refactor(world-forge): turn world_forge.py into a dac_world re-export shim (step 6)
- `859f577` test(world-forge): cross-repo golden-bundle parity test (step 7)

**Files changed:**
- qukaizen-dac: `pyproject.toml`, `.gitignore` (new/modified); `dac_world/`
  (9 new files); `tests/python/` (5 new files: `conftest.py`,
  `test_world_forge_seal.py`, `test_no_arail_backimport.py`,
  `test_golden_bundle_parity.py`, `fixtures/golden-bundle/*` — 10 fixture
  files); `sprints/2026-07-19-dac-generates-arail-worlds/BUILD_LOG.md`.
  `dac_compiler.py`/`lancedb_sink.py`/`world_to_toon.py` untouched.
- qukaizen-arail: `src/arail/world_forge.py` (rewritten, ~1120 lines →
  ~90), `src/arail/portal/world_routes.py` (+4 lines, theme_validator
  wiring), `pyproject.toml` (+11 lines), `tests/test_dac_world_shim_smoke.py`
  (new), `tests/test_golden_bundle_parity.py` (new),
  `tests/fixtures/golden-bundle/*` (new, 10 files). No other file touched
  (pre-existing unrelated uncommitted edits — `lab/tools/benchmark_models.py`,
  `lab/worlds/ai/evolution.json`, portal static/templates,
  `tests/test_world_qa_probes.py` — left untouched and unstaged throughout,
  per the same no-scope-drift note as the step-1 BUILD_LOG entry).

**Test results:**
- qukaizen-dac `tests/python/` (28 tests: 24 seal + 1 F4 static check +
  3 golden-bundle parity): 28/28 passing, via both a bare python3.11
  venv and ARAIL's venv.
- qukaizen-arail `tests/test_world_forge_seal.py` +
  `test_world_forge_gate.py` + `test_world_forge_pipeline.py` +
  `test_dac_world_shim_smoke.py`: 62/62 passing.
- qukaizen-arail `test_world_forge_api.py` + `test_world_growth.py` +
  `test_world_bootstrap_wikipedia.py`: 34/34 passing.
- qukaizen-arail `test_golden_bundle_parity.py`: 2/2 passing.
- A broader `pytest tests/ -k "world or librarian or forge"` sweep of
  ARAIL's full suite was also kicked off as an extra regression check;
  if it surfaces anything beyond the targeted suites above, it will be
  appended here — it was still running (~10+ minutes, CPU-bound) when
  this BUILD_LOG was finalized and was not blocking on the targeted,
  directly-relevant suites already green above.
- Step 8 (`reseal_all` sweep): 3 of 4 bundles (`photography`, `physics`,
  `qukaizen`) resealed and re-verified cleanly through the shared path;
  the 4th (`ai`) exposed a genuine pre-existing sidecar-preservation gap
  (see "Architect feedback required" above) and was restored to its
  pre-sweep state rather than left resealed. No bundle in
  `qukaizen-arail/lab/worlds/` was left in a different state than before
  step 8 began.

**Not done:** step 9 (CLAUDE.md/VISION.md/BLUEPRINT.md updates, the
superseding ADR in both repos, and deciding the TS forge's fate) — out of
scope per the task instructions, a separate follow-up. Step 8's `ai`
World reseal is blocked pending the architect decision above; the other
three bundles can be swept as soon as that decision lands.

---

## Step 8 resumed and completed

**Architect decision (addendum committed `9c32537`):** Option C — invert
`reseal_bundle`'s hardcoded two-name sidecar allow-list to a
**regenerated-set denylist** (`REGENERATED_FILES`, derived from `SEALED_FILES`
+ the fixed non-sealed outputs) plus a warn-loud advisory `KNOWN_SIDECARS`
set. Full rationale in ARCHITECTURE.md's "Addendum: sidecar-preservation
policy (step 8 follow-up)".

### Fix implementation

`qukaizen-dac/dac_world/seal.py`:
- Added `REGENERATED_FILES = frozenset(SEALED_FILES) | {"manifest.json",
  "SKILL.md", "capabilities.json", "arail-plugin.json"}` — derived from the
  same `SEALED_FILES` constant `write_bundle` iterates over, so the two
  cannot drift independently.
- Added `KNOWN_SIDECARS = frozenset({"model.json", "review.json",
  "evolution.json", "librarian-scout.json"})` — advisory only, controls the
  warning, never gates preservation.
- Replaced `reseal_bundle`'s hardcoded `for extra in ("model.json",
  "review.json")` loop with an `iterdir()` walk over `bundle_dir`: skip
  `REGENERATED_FILES`, warn (`logging.getLogger("dac_world.seal").warning`,
  matching the existing `_log.warning` pattern already used in
  `forge.py`/`reconcile.py`) on any name outside `KNOWN_SIDECARS`, then copy
  forward (`shutil.copy2` for files, `shutil.copytree` for directories).
- Signature unchanged (`reseal_bundle(bundle_dir, terms=None, *,
  theme_validator=None) -> Path`); the atomic tmp/old swap-with-rollback
  (Failure F5) untouched — only the sidecar-copying step inside it changed.

### Tests added (`qukaizen-dac/tests/python/test_world_forge_seal.py`)

- `test_reseal_preserves_arbitrary_sidecar` — parametrized over
  `evolution.json`, `librarian-scout.json`, and `totally-novel-sidecar.json`
  (an invented name never present in any allow-list, past or present). The
  load-bearing case: only a genuine generalization passes it, not an
  extended list.
- `test_reseal_warns_on_unknown_sidecar` — via `caplog`, asserts the warning
  fires for an unrecognized name and does not fire for a `KNOWN_SIDECARS`
  entry (`evolution.json`) present in the same bundle.
- `test_reseal_regenerated_files_not_treated_as_sidecars` — edits a term,
  reseals, and asserts `manifest.json`/`SKILL.md`/`capabilities.json`/
  `arail-plugin.json` are freshly regenerated (content changed), not stale
  carried-over copies — guards `REGENERATED_FILES` against drifting out of
  sync with what `write_bundle` actually emits.
- `test_reseal_preserves_sidecar_directory` — seeds a nested sidecar
  directory with two files; asserts `copytree` preserves both (the old
  flat-file loop would have silently dropped it).
- Existing `test_reseal_preserves_seal_exempt_sidecars` (`review.json`)
  left unmodified and still passes.

**Test run:** `tests/python/test_world_forge_seal.py` — 30/30 passing (24
pre-existing + 6 new). Full `tests/python/` suite — 34/34 passing. Re-ran
against ARAIL's own `.venv` (editable-installed `dac_world` pointing at
this worktree) — same 34/34. ARAIL's own consumer suites
(`tests/test_world_forge_seal.py`, `test_dac_world_shim_smoke.py`,
`test_golden_bundle_parity.py`, `test_world_forge_gate.py`,
`test_world_forge_pipeline.py`) — 64/64 passing, no regressions.

**Commit:** `99b04e8` (qukaizen-dac) — `fix(dac_world): reseal_bundle
preserves sidecars via denylist, not a 2-name allow-list`.

### Sweep resumption

Backed up `qukaizen-arail/lab/worlds/{ai,photography,physics,qukaizen}/` to
a scratch dir, then ran all four through `arail.world_forge.reseal_bundle`
(the shim → `dac_world.seal.reseal_bundle` path) with
`theme_validator=parse_world_theme` injected, exactly as ARAIL's own portal
call sites do. Per the task's discretion, all four were run through the new
path (not just `ai`) rather than conservatively skipping the three that
already verified cleanly last time — this both re-verifies them and proves
idempotency under the new code path.

**Result — all four:**
- `verify_seal().ok is True` for `ai`, `photography`, `physics`, `qukaizen`
  (plus `check_compat`/`check_categories` clean) after reseal.
- `ai`'s `evolution.json` and `librarian-scout.json` both survived, and a
  byte-for-byte diff against the pre-sweep backup confirms they are
  **byte-identical**, not merely present.
- For all four bundles, the six sealed files (`terms.json`, `spec.json`,
  `roster.json`, `face.json`, `agenda.json`, `drift-report.json`) round-trip
  **semantically identical** (`json.loads(old) == json.loads(new)` for
  every file, every bundle) — confirmed programmatically, not by eye.
- `ai`, `physics`, `qukaizen`: `diff -rq` against the backup shows **zero**
  file differences at all (fully byte-identical reseal — idempotent given
  pinned inputs).
- `photography`: `manifest.json`, `SKILL.md`, `capabilities.json`,
  `arail-plugin.json`, `spec.json`, `roster.json` differ in **bytes only**
  (JSON pretty-printing normalization — the pre-existing on-disk copy used
  compact single-line nested objects; `write_bundle`'s `json.dumps(...,
  indent=2, ...)` always re-expands them). Content is semantically
  unchanged (confirmed above); `manifest.json`'s hash/`created_at` changes
  are expected consequences of any reseal.

**git-tracked bytes changed:** none attributable to this sweep.
`git status`/`git diff` in `qukaizen-arail` after the sweep show only the
pre-existing, pre-session `lab/worlds/ai/evolution.json` timestamp edit
already documented in this BUILD_LOG's step-1 section (untouched by this
build, present before step 8 began). `lab/worlds/photography/` and
`lab/worlds/physics/` are untracked directories in `qukaizen-arail` (not
committed to git at all, pre-existing state, not something this task's
scope covers) — their on-disk pretty-printing normalization is real but has
no git representation to commit. `lab/worlds/qukaizen/` is fully tracked
and shows zero diff. **No commit was made in qukaizen-arail: there are no
bytes to commit** (nothing new is staged or would appear in `git add`).

### Final state (step 8 complete)

- All four bundles pass `verify_seal`/`check_compat`/`check_categories`
  through the shared `dac_world` path.
- The silent-sidecar-drop bug class (traced to `qukaizen-arail@5c45f58`,
  carried verbatim into `dac_world/seal.py` during the migration) is
  closed permanently — no future sidecar-producing feature can lose its
  file on first reseal, and none needs to edit `seal.py` to be safe.
- No git bytes changed in `qukaizen-arail` as a result of the sweep itself.
- **Step 9** (CLAUDE.md/VISION.md/BLUEPRINT.md updates, the superseding
  ADR in both repos, deciding the TS forge's fate) remains the only
  undone step from the original "Recommended implementation order" — still
  out of scope per this task's instructions.

