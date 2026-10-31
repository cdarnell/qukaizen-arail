---
title: Autoresearch Tuning Loop
category: Operating
order: 10
tags:
  - autoresearch
  - tuning
  - inference
audience: operator
related:
  - tunables
  - missions
---
# Autoresearch Tuning Loop — `/tuning`

The `/tuning` page is the single-pane view for measuring and
improving Arail's 1 TB research model. It ties three things
together:

1. A **bench runner** that times the model, records every run as
   JSONL, and tags each record with the git SHA that produced it.
2. A **tuning config** (`config/tuning.yml`) that is the complete
   surface area an autoresearch agent is allowed to modify.
3. An **autoresearch loop** that enumerates known-safe variants,
   runs the bench for each, and commits the winners on dedicated
   `autoresearch/<id>` branches.

The whole module sits under `src/arail/experiments/` and is
wired into the portal at `/tuning`.

## The model under test

Exactly one "research model" is tuned at a time. It must be a
model whose weights are ≥ 1 TB on disk — this is the whole
premise of the loop. The default is DeepSeek-R1 (671B MoE, FP16 ≈
1.3 TB). Swap it by editing `config/tuning.yml`:

```yaml
research_model:
  name: "meta-llama/Meta-Llama-3.1-405B"
  precision: "fp32"
  expected_disk_gb: 1620
  family: "dense"
  ...
```

Small models (Qwen3-0.6B, Phi-3.5-mini, etc.) are listed
separately under `small_models` for the fast/draft paths. They
are not tuned by this loop.

## Safety rails

The loop makes commits. That means every safety rail here is
load-bearing.

- **Working tree must be clean.** The loop aborts with a clear
  error if `git status --porcelain` returns anything. Commit or
  stash first.
- **`ARAIL_AUTORESEARCH_ENABLED` env var required.** Without it,
  the `/api/tuning/autoresearch/start` endpoint returns a 200
  with `{ok: false, error: ...}`. Belt + braces so an accidental
  click can't make commits.
- **Only four files are ever writable by the loop.** Enforced in
  `git_ops.ALLOWED_WRITABLE_FILES` — two per backend:
  - `config/tuning.yml` + `lab/data/aerollm-bench.jsonl` (QueueLLM/CUDA)
  - `config/tuning-mlx.yml` + `lab/data/mlx-bench.jsonl` (QueueLLM MLX/Apple)

  If the agent somehow proposes a different edit, `commit_experiment`
  raises `GitSafetyError` before anything is staged. A test pins this
  set small (`tests/test_experiments.py`); adding a backend means adding
  exactly two entries, with justification.
- **Schema validator rejects off-schema knob values.** Each knob
  in `tuning.yml` declares a type and bounds/choices. Candidates
  that propose invalid values are skipped as errors without
  touching git.
- **Variants never touch main.** Each variant runs on its own
  `autoresearch/<timestamp>-<slug>` branch. Losing variants are
  left on-branch for human inspection; winners are committed with
  a structured message, still on their branch — human reviews &
  cherry-picks. A losing variant is undone with `git checkout -- .`
  plus a checkout of the original branch — **not** `git reset`; the
  branch ref survives on purpose.

- **The baseline gets its own branch too.** The baseline capture is
  committed on `autoresearch/baseline-<timestamp>`, created before
  anything is staged. Variants then branch from *that*, so each variant
  carries the baseline record it's being measured against. Nothing the
  loop does ever commits to the branch you started from.

  (This was a real bug until 2026-08-16: the baseline was committed
  before any branch existed, so running the loop from `main` put a
  commit on `main` — and `./arailctl update`'s `git pull --ff-only`
  would later refuse, with an error you couldn't trace back to the
  loop. See [the integration audit](plans/autoresearch-integration.md)
  hazards H1/H2.)

- **You end up back where you started.** When the pass finishes — win,
  loss, or crash — the loop checks your original branch back out. It
  will not leave you sitting on a winning variant branch.
- **No pushes, no force, no rebases.** This module never shells
  out to any git operation that rewrites history or sends anything
  over the network.

## Interpreting the page

- **Baseline** — the frozen reference measurement on the commit
  captured by the "Capture baseline" action. Sets the denominator
  for Δ%.
- **Champion** — the best `decode_tok_per_sec` observed across
  all runs in `aerollm-bench.jsonl`, with a link to its commit.
- **Throughput over time** — sparkline of every successful run's
  `decode_tok_per_sec`, ordered by timestamp. Winning commits
  lift the line visibly.
- **Variants table** — the results of the most recent loop. A
  "win" row is committed; "loss" and "error" rows are left on
  branch for inspection.
- **Bench history table** — full detail from JSONL, newest first.

## The candidate list

`autoresearch.CANDIDATES` is a hand-curated list of variants
mirroring the knobs QueueLLM exposes. Adding a new candidate
requires two edits:

1. `config/tuning.yml` — if the variant needs a new knob or a new
   allowed `choices` value.
2. `src/arail/experiments/autoresearch.py` — add an entry to
   `CANDIDATES`.

Two-file discipline keeps the search space from expanding behind
the maintainer's back. Agents that propose variants at runtime
must pass them via the `candidates` parameter; they cannot extend
`CANDIDATES` in-place.

## Extending with upstream QueueLLM knobs

Today `AeroLLMBackend` honors two env-var knobs directly:
`AEROLLM_COMPRESSION` and `AEROLLM_MAX_LENGTH`. The other four —
`prefetch_enabled`, `prefetch_lookahead`, `expert_cache_size_mb`,
and `aerollm_package` — land when the upstream Rust runtime exposes
them. (The install ref that is actually used lives in the
`aerollm_package` knob in `config/tuning.yml` — treat that value as the
source of truth for where the runtime is fetched from, not any URL
written in prose here.) The workflow to wire a new one:

1. Land the knob upstream in the Rust runtime (or a branch of it)
   so it's read at engine init.
2. Add the install ref to `aerollm_package.schema.choices` in
   `config/tuning.yml` if the knob ships on a non-main branch.
3. Update `bench._apply_knob_env` to translate the new knob into
   whatever the runtime reads (env var, CLI flag, or config blob).
4. Let the loop run. The knob is now under experimental control.

## Tests

`tests/test_experiments.py` covers the safety-critical parts:

- Knob validator rejects off-schema values (string choices, int
  bounds, bool type).
- `ALLOWED_WRITABLE_FILES` stays small.
- YAML round-trips preserve schema.
- `config/tuning.yml` in the repo always has a ≥ 1 TB
  research_model and every knob's current value passes its own
  schema.
- The loop refuses to run without the env flag.
- Invalid candidates get skipped as errors instead of partially
  applied.

101 tests pass (88 existing + 13 new). Run with:

```bash
./arailctl test   # or: pytest tests/
```
