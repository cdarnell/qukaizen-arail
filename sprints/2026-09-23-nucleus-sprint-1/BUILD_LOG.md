# Build log: Model Forge (`local` profile) — arail.nucleus

**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `58b754bc`
**Sprint ledger:** [SPRINT.md](./SPRINT.md)
**Started:** 2026-09-23
**Finished:** 2026-09-23 (Gate A reached at commit 22; scope was §10 commits 1–27)

## Scope

ARCHITECTURE.md §10 commits 1 through 27 — everything up to and including
Gate A (the stub-provider end-to-end pipeline in CI against the 50-item
synthetic fixture corpus, no real teacher). Commit 28 (the opt-in
`unstable-api` local runtime rebuild) and Gate B / item 10 (the real M5
build) are explicitly out of scope per the task instructions and were not
started.

## Plan

| # | Files | Change | Test | Commit ref |
|---|---|---|---|---|
| 1 | `pyproject.toml`, `.github/workflows/nucleus-tests.yml`, `tests/nucleus/` | Deps + CI skeleton | placeholder collects | `fe2b0b69` |
| 2 | `src/arail/airgap.py`, `portal/app.py` | `AIRGAPPED_NOTICE` shared constant | T-REG-2 | `d6a7867e` |
| 3 | `src/arail/world_catalog.py`, `build/world_corpus.py` (shim), `compiled_kb.py` | Move approved-term pull | T-REG-3 | `e7e127aa` |
| 4 | `src/arail/nucleus/{__init__,__main__,cli,errors,runtime_names}.py` | Package skeleton + CLI dispatch | T-RT-1,2; T-CLI-1..4 | `05d74cb3` |
| 5 | `domain.py`, `plan.py`, `spec/nucleus-domain-v1.schema.json`, `configs/domains/linux-kernel.*` | Domain config | T-DOM-1..4 | `bccc3d1e` |
| 6 | `paths.py` | Paths, lock, output guard | T-PATH-*, T-LOCK-* | `58ef0572` |
| 7 | `models.py`, `tokenizer_parity.py` | Model resolution, identity, parity, teacher select | T-PAR-1..4, T-SETUP-2 | `ec5998b2` |
| 8 | `preflight.py`, `residency.py` | Memory plan, Buddy reserve, residency | T-PRE-1..5, T-RES-1..3, T-SETUP-3 | `409a7ce0` |
| 9 | `providers/{base,stub,queuellm,fallback_ollama}.py` | Providers | T-PROV-1..3, T-STUB-2 (partial) | `0b45d0ba` |
| 10 | `corpus/stage.py`, `corpus/sources/*`, `corpus/_git_env.py`, fixture corpus | Corpus staging + fixture | T-STAGE-1,2, T-SEC-GIT-1 | `c5ba84fc` |
| 11 | `evals/splits.py` | Splits + CertStore | T-SPLIT-1..3, T-CERT-1,2 | `5c2c6425` |
| 12 | `evals/contamination.py` | Contamination | T-CONT-1..5 | `69e130a7` |
| 13 | `evals/closed.py`, `evals/composite.py` | Closed metrics + composite + decision | T-CLOSED-1,2 | `4afbdb10` |
| 14 | `evals/open_lc_judge.py` | LC pairwise judge | T-JUDGE-1..4 | `41356780` |
| 15 | `evals/executable_kernel.py` | Executable kernel checks | T-EXEC-1..4 | `3c4adbe2` |
| 16 | `evals/hash.py` | eval_hash + pipeline_hash | T-HASH-1..3 | `881bfa2f` |
| 17 | `train/kd_loss.py`, `train/mlx_kd.py` | KD loss reference + MLX trainer | T-KD-1..3 (4 skipped, no mlx here) | `0b93edeb` |
| 18 | `phases.py`, `worker.py`, `build.py`, `arbitrage.py`, `evals/tasks/linux_kernel.py` | Phase runner, worker, Arbitrage, build/status/resume | T-D3-1, T-CERT-3 (live), T-RESUME-1, T-EGR-2 | `044837ea` |
| 19 | `cards/dna_v2.py`, `spec/dna-card-v2.schema.json` | DNA card v2 | T-CARD-1..3 | `ecd90a1b` |
| 20 | `cards/seal.py` | Seal (legacy-5), key custody, verify | T-SEAL-1..8 (8 verified against the real Rust binary) | `9236576f` |
| 21 | `cards/build_report.py`, `cards/certified_models.py`, `certify.py` | Report + ledger + certify | T-REPORT-1, T-LEDGER-1..4 | `77132bd0` |
| 22 | `tests/nucleus/test_e2e_gate_a.py` | **Gate A** end-to-end | T-E2E-1, T-EGR-1 | `809141ce` |
| 23 | `docs/nucleus-gateway-contract.md`, `providers/{gateway,mixed}.py` | Gateway contract + client | T-GW-1..5 | `13f61d28` |
| 24 | `spike.py` | Spike verb (Gate B harness) | T-SPIKE-1 | `0f0c488e` |
| 25 | `portal/forge_api.py`, `templates/forge.html`, `app.py`, `_nav.html` | `/forge` viewer | T-FORGE-1..3 | `f1db73c0` |
| 26 | delete `arail/build/**`, `portal/build_api.py`, `templates/build.html`, `tests/build/**`, `tests/portal/test_build_tab.py`; `models_api.py`, docs | Retire `/build` | T-FORGE-4, T-REG-4,5 | `82ed6c78` |
| 27 | `docs/nucleus.md`, `docs/nucleus-architecture.md`, `sprints/BACKLOG.md` | Docs + filed tickets | — | `14dd54aa` |

## Execution

Each commit above landed in order, one per §10 step, with its own tests
written first or alongside and the full `tests/nucleus` suite re-run green
before moving to the next. Full commit messages carry the per-commit
rationale; this section records deltas from the architecture and things a
reviewer should look at first.

### Deltas from ARCHITECTURE.md (documented at the time, not discovered late)

1. **`errors.py` (commit 4) is not in §4.0's file list.** A small,
   dependency-free home for `DomainConfigError`/`RefusedByPolicy`/
   `CapabilityMissing`, needed so `runtime_names.py` (commit 4) doesn't
   have to forward-import `domain.py` (commit 5). Infrastructure, not a
   behavior change.
2. **`domain.py`'s `< 8B` student check (T-DOM-4) is wired via an
   injectable `model_resolver` parameter**, defaulting to `None` (skip)
   until `models.py` exists (commit 7), then wired for real from `plan.py`
   onward. Domain *shape* validation was complete at commit 5; the model-
   size check went live at commit 7.
3. **`corpus/_git_env.py` is a small shared module** (not named in §4.0)
   factoring the hardened git subprocess runner out of
   `sources/git_kernel.py` so `evals/executable_kernel.py` (commit 15)
   reuses the identical hardening instead of a second copy.
4. **`corpus/stage.py` gained an additive, non-canonical
   `local_paths.json` sidecar** (commit 18) recording the operator's own
   `--source` paths for same-machine reuse. `evals/executable_kernel.py`'s
   `patch_applies` needs the real git repo at build/eval time; staging
   correctly never hashes a path (paths aren't portable/reproducible), but
   nothing in the architecture said how Phase C would locate the repo on
   the *same* machine that staged it. Resolved as a small, additive,
   backward-compatible sidecar file rather than a new CLI flag on `stage`
   or `build` (which would have been a larger, unauthorized surface
   change). Documented as a design resolution at the time, in the commit
   message.
5. **`domain.py` gained `ARAIL_NUCLEUS_DOMAINS_DIR`** (commit 22), an env
   override so Gate A's real-subprocess-CLI test can run five independent
   `python -m arail.nucleus <verb>` processes against one isolated tmp
   domains dir without writing into the real repo's `configs/domains/`.
   Additive; the default (no env var set) is unchanged.
6. **`seal.sign(..., ephemeral=True)`** (commit 22, discovered while
   building the Gate A test) — the architecture says a stub card's key is
   "ephemeral" and `verify()` must report `key: ephemeral-stub`, but
   nothing before commit 22 actually generated a real, never-persisted,
   in-memory key for the stub path or marked it distinguishably. Fixed
   in the same commit that found it.
7. **`arail.activity.activity_log.emit(source="nucleus", ...)` calls**
   (commit 22) — §3 N8 describes this as one of three progress channels,
   but no earlier commit actually emitted to it; Gate A's own assertion
   ("ActivityLog has nucleus events") is what caught the gap. Added at
   `phases.run_phase()` (phase start/end) and `certify.py` (the final
   decision).
8. **`build.py`'s real-MLX training-cycle wiring is a stub, by
   documented choice, not an oversight** (commit 18). `_mlx_train_cycle_fn`
   refuses clearly with a message pointing here rather than pretending to
   train. `train/mlx_kd.py`'s primitives (commit 17) are real; the
   multi-cycle batch-reading loop around them is not yet connected. Filed
   in `sprints/BACKLOG.md` — required before Gate B / item 10.
9. **T-SEAL-2's "golden vector from the four vectors from F3"** — the
   original F3 vectors were generated on the architect's machine and
   weren't available to the builder. Substituted: a live cross-check
   against the actual Nucleus Rust `qkz` binary
   (`qukaizen-nucleus/qkz/target/release/qkz`, found already built on this
   machine), run for real (not just written-and-skipped) as T-SEAL-8,
   `NUCLEUS_QKZ_BIN` set for the run. This is a stronger check than a
   hand-copied vector would have been.

None of the above required stopping and escalating to the architect —
each was a small, containable resolution of an underspecified integration
point (how does X find Y at runtime), documented at the commit that made
the call, not silently.

### Architect feedback required

None. No step required a redesign or a genuine architecture-vs-reality
conflict serious enough to halt on. The nine items above are documented
deltas, not open questions.

## Final state

- **27/27 commits landed**, `fe2b0b69`..`14dd54aa`, one per §10 step, in
  order.
- **Gate A reached** at commit 22 (`809141ce`): the real subprocess CLI —
  `python -m arail.nucleus plan|stage|build|certify|verify`, five
  independent processes — runs end to end against the synthetic
  linux-kernel-mini fixture with `LAB_MODE=airgapped`,
  `ARAIL_NUCLEUS_STUB=1`. Every phase interval in `run.json` is
  non-overlapping (D3 holds under a real subprocess run, not just in
  unit tests); the cert set's bytes are unchanged before/after; a real
  Ed25519 seal is produced; `egress.jsonl` never gets created.
- **Test counts** (this machine, `tests/nucleus/` only,
  `-m "not requires_mlx and not requires_aerollm and not requires_kernel and not requires_qkz_bin"`,
  i.e. the exact CI marker set from `nucleus-tests.yml`):
  **324 passed**, 2 deselected (`requires_mlx`/`requires_qkz_bin`), 0
  failed. With `NUCLEUS_QKZ_BIN` pointed at the real, already-built
  Nucleus Rust binary on this machine, the `requires_qkz_bin` test
  (T-SEAL-8) also **passes for real** — the Python-signed seal verifies
  against the actual Rust verifier, byte for byte. `requires_mlx`
  (T-KD-4) is genuinely skipped — no MLX install in this environment;
  flagged in `train/mlx_kd.py`'s own docstring and in the BACKLOG entry
  above as needing verification on the operator's M5.
- **Regression**: the broader repo suite (`tests/nucleus`,
  `tests/portal/test_forge_viewer.py`, `tests/portal/test_models_api.py`,
  `tests/test_qa6_security_gate.py`, `tests/test_world_catalog.py`,
  `tests/test_airgap_helpers.py`, `tests/test_docs_routes.py`,
  `tests/test_study_surface.py`, `tests/test_research_page_dom.py`) was
  run repeatedly through the build. One failure recurs throughout
  (`test_models_api.py::test_health_refresh_probes_without_constructing_aerollm`)
  and three recur in `test_docs_routes.py`
  (`test_docs_link_renders_in_min_nav`, `test_docs_link_renders_in_max_nav`,
  `test_dashboard_runbook_banner_renders`) — all four were confirmed
  **pre-existing** by a scoped `git stash`-and-rerun against the
  unmodified tree at multiple points during the build (commits 3, 26);
  none touch `build`/`forge`/`models_api`/`world_catalog`/`airgap`. No
  other regressions found.
- **Grep invariant** (T-RT-2 — no `aerollm`/`AERO_`/`QUEUELLM_` literal
  outside `runtime_names.py`, no `os.environ[...] =` write anywhere in
  `arail.nucleus`) passes on the full package as committed. It caught
  four real near-misses during the build (an f-string mentioning the
  frozen rebuild env var, a `**queuellm_kwargs` parameter name, a
  `queuellm_present` parameter name, and a docstring mentioning
  `AeroLLMBackend`) — all fixed at the commit that introduced them, not
  papered over afterward.
- **No commented-out code, no TODO/FIXME/XXX comments** without an owner
  in the delivered `src/arail/nucleus/**` — checked at handoff.
- **Lines changed**: 27 commits, roughly 8,900 insertions across
  `src/arail/nucleus/**` (new package), `src/arail/portal/forge_api.py` +
  `templates/forge.html`, `src/arail/world_catalog.py`, `docs/`, `spec/`,
  `configs/domains/`, and `tests/nucleus/**` (~120 test files/functions
  across the 27 commits); roughly 2,600 deletions in commit 26 (the
  `/build` retirement).

## What the reviewer should look at first

1. `sprints/2026-09-23-nucleus-sprint-1/ARCHITECTURE.md` §0 facts F1–F11
   and §9 tech-debt, against `sprints/BACKLOG.md`'s four new entries —
   confirm the filed tickets actually capture the gaps as understood.
2. `tests/nucleus/test_e2e_gate_a.py` — the capstone test; if this is
   wrong, everything upstream of it is unproven.
3. `src/arail/nucleus/cards/seal.py` and `runtime_names.py` — the two
   modules with the tightest external contracts (the real Nucleus Rust
   verifier; the frozen naming surface).
4. `src/arail/nucleus/build.py`'s `_mlx_train_cycle_fn` — the one
   deliberately-stubbed real-runtime path; confirm the BACKLOG entry
   framing is fair before Gate B is attempted.
5. `git diff 82ed6c78~1..82ed6c78` (the `/build` retirement commit) — the
   largest single diff by deletion count; confirm nothing outside its
   stated scope was swept up.
