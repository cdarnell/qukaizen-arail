# Model Forge — architecture reference

This is the durable developer reference for `src/arail/nucleus/`. The
full design record — assumptions, failure modes, test strategy, the
commit-by-commit build order — lives in
`sprints/2026-09-23-nucleus-sprint-1/ARCHITECTURE.md`; this page
extracts the parts worth finding without digging through a sprint
ledger.

## Package layout

```
src/arail/nucleus/
  cli.py                 arailctl nucleus <verb> dispatch, tier gate, exit codes
  runtime_names.py        the ONLY file allowed to spell aerollm/AERO_/QUEUELLM_
  errors.py               NucleusError, DomainConfigError, RefusedByPolicy, ...
  domain.py                domain.yaml load/validate (nucleus.domain/v1)
  paths.py                  layout, id/slug/version validation, git-ignore guard, build lock
  models.py                  local model resolution, identity hashing, teacher auto-select
  tokenizer_parity.py         exact | superset | none
  preflight.py                  memory plan + Buddy reserve + capability rows
  residency.py                   Buddy residency sampler + classifier
  phases.py                       subprocess-per-phase runner, D3 enforcement
  worker.py                        python -m arail.nucleus.worker <phase> <build_id>
  arbitrage.py                      dev-only training loop controller
  build.py plan.py certify.py spike.py   verb implementations
  corpus/stage.py corpus/sources/{git_kernel,cve_vulns}.py
  providers/{base,stub,queuellm,fallback_ollama,gateway,mixed}.py
  train/{kd_loss,mlx_kd}.py
  evals/{splits,contamination,closed,composite,hash,open_lc_judge,executable_kernel}.py
  evals/tasks/linux_kernel.py
  cards/{dna_v2,seal,build_report,certified_models}.py
src/arail/world_catalog.py         (salvaged from build/world_corpus.py)
spec/dna-card-v2.schema.json spec/nucleus-domain-v1.schema.json
configs/domains/linux-kernel.yaml + .eyeball.txt
src/arail/portal/forge_api.py + templates/forge.html
docs/nucleus.md docs/nucleus-gateway-contract.md (this file)
```

## The queuellm ↔ frozen aerollm surface

`runtime_names.py` maps the user-facing runtime name `queuellm` to the
frozen backend id `aerollm`, module `aerollm_api`, registry id
`tier1-aerollm`. A grep test
(`tests/nucleus/test_runtime_names.py::test_no_frozen_names_leak_outside_runtime_names_module`)
enforces that no other file under `src/arail/nucleus/**` spells
`aerollm`/`AERO_`/`QUEUELLM_` — including in comments and f-strings; it
caught several near-misses during the build (see BUILD_LOG.md). Nucleus
never writes an environment variable for the runtime; every knob is a
`Runtime(...)` constructor kwarg.

## Phase pipeline

```
plan (write domain.yaml)  ─►  stage (snapshot, never fetches)  ─►
build --profile local:
    P0 preflight (in-process)
 ─► PA extract (worker: teacher top-N logprobs on train prompts)
 ─► PA2 extract-on-cert (worker: teacher baseline on cert set)
 ─► PB train (worker: Arbitrage LoRA cycles, dev-only, no cert access)
 ─► fuse (worker: adapter → loadable MLX dir)
 ─► PC eval (worker: student vs base on cert)
 ─► certify: contamination gate → DNA card → Ed25519 seal → build report → ledger
 ─► verify: recompute + re-check, independent of what the build claimed
```

Each worker phase is its own subprocess (`python -m arail.nucleus.worker
<phase> <build_id>`, niced to 10), so model memory is reclaimed by
process exit rather than trusted GC. `phases.RunLedger` records
`{phase, pid, start, end, status}` in `run.json` and structurally
refuses to start a train phase (`PB`) while a teacher-resident phase
(`PA`/`PA2`) pid is still alive (D3).

## Data flow / storage

- **Private intermediates** (corpus snapshot, cert set, teacher logits,
  signing key): `NUCLEUS_DATA = $ARAIL_DATA_DIR/nucleus/`, 0700 dirs.
- **Shard output**: `FORGE_ROOT = $ARAIL_MODELS_DIR/forge/<shard>/<version>/`.
- Both are guarded: if either resolves inside a git worktree, the path
  must be `git check-ignore`-clean or the build refuses (never risk a
  committable shard or private key).

## Eval hashing (what "verifiable" means)

`eval_hash` covers exactly six closed-world fields — harness version,
prompts, few-shot, scoring (metric defs, composite formula id+string,
decision rule, judge rubric+identity, LC method/params, bootstrap
seed/n, position seed, executable checks, contamination method/params),
decoding, and cert-set version. It deliberately excludes timestamps,
paths, run/build ids, host, training seeds, top-N, and student identity
— none of those change what's being measured.
`pipeline_hash` covers the training-side yardstick (nucleus source
bytes, domain canonical bytes, corpus manifest sha, teacher/student-base
identity, distill params, training hyperparams) and is independent of
`eval_hash`.

## Seal format (why it's 5 fields, not the newer 10)

`cards/seal.py` signs exactly the legacy 5-field payload (`dna_id`,
`pipeline_run_id`, `chain_hash`, `gate_results`, `timestamp`) because
that's what the Nucleus Rust `qkz isotope verify` binary actually
checks — confirmed against the real binary during the build (see
`sprints/2026-09-23-nucleus-sprint-1/BUILD_LOG.md`). Nucleus's own
current Python signer produces a 10-field payload that its own Rust
verifier already rejects; that mismatch is a filed Nucleus-side
follow-up (`sprints/BACKLOG.md`), not something this repo works around.
Signed bytes are `json.dumps(payload, sort_keys=True)` with Python's
*default* separators (`", "`/`": "`) — the one place this package
doesn't use its usual compact canonical-JSON convention, because the
Rust verifier's formatter matches exactly that default.

## Composite formulas and decision bands

`composite/v1` = `0.4·closed.mean_f1 + 0.3·open.lc_win_rate +
0.3·executable.compiles`; `composite/v1-nc` substitutes
`mean(patch_applies, checkpatch_clean)` for `compiles` and is selected
automatically whenever `compiles` is `not_run` (this sprint, always — no
Linux build host). `decision_rule/v1`, first match wins: `KNOWN_ISSUE`
(doesn't beat base) → `BETA` (>5pts under target) → `COMPATIBLE` (under
target, or a Buddy-residency violation caps it here regardless of the
number) → `CERTIFIED`.

## Gateway / mixed

See `docs/nucleus-gateway-contract.md`. `teacher.profile: gateway` or
`mixed` always refuses this sprint (`ProfileRefused`) — the contract is
fixed, the live gateway conforming to it is `nucleus-sprint-2`.

## Known seams (deferred, not forgotten)

`arail-ops` MCP tools and the Buddy `nucleus-partner` skill (brief items
3–4) were deferred per VISION.md — no `arail-ops` server exists yet, and
the Buddy brief sequences guard rails first. GGUF export,
`hallucination_rate`, and `build_energy_est` are `not_run`/`not_built`
placeholders in the DNA card schema. The real MLX training loop's
multi-cycle wiring, and item 10's real `qkz-linux-kernel` build, are
gated on the operator's `unstable-api` rebuild decision (commit 28) and
tracked in `sprints/BACKLOG.md`.
