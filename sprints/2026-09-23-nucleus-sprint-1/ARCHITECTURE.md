# Architecture: Model Forge — Nucleus as an ARAIL surface (`local` profile, QueueLLM runtime)

**Date:** 2026-09-23
**Spec:** [VISION.md](./VISION.md) at `c8b1cefc`; brief [`../nucleus-in-arail-brief.md`](../nucleus-in-arail-brief.md) at `11826fc3`; ledger decisions at `d7387d50`
**Scope (from ledger):** brief §6 items 1, 2, 6, 7, 8, 9 + a read-only `/forge` DNA-card viewer (item 5 reduced). Gate A → Gate B → item 10.
Items 3 (MCP tools) and 4 (Buddy skill) are deferred; this doc leaves seams for them and designs nothing else.

---

## 0. Verified facts that change the plan (read this first)

The following were checked on this machine on 2026-09-23. They are *not* in the brief or VISION, and three of them
change what "done" means.

| # | Fact | Evidence | Consequence |
|---|---|---|---|
| F1 | **The runtime ARAIL ships cannot emit logprobs.** The pinned bundle (`aerollm_bundle_tag = "v1.1.0"`) is the *ARAIL* release tag. The binary inside is **AeroLLM 1.0.0 @ `2ae56afb`** (`THIRD-PARTY-LICENSES/aerollm/BUNDLE.json`), built with `--features extension-module` only. `Runtime.generate(logprobs=…)` and `Runtime.score()` are both `#[cfg(feature = "unstable-api")]`. | Live probe of the wheel installed in `qukaizen-arail/.venv` (reports `0.1.0-rc.2`, backend `mlx-native`): `ValueError: logprobs is unstable per STABILITY.md; rebuild aerollm-api with --features unstable-api`. `score` gives the same error. | VISION note 2 ("v1.1.0 ships R.2 logprobs") is wrong: it conflated the ARAIL tag with a QueueLLM version. **Logit-level distillation (D5) through QueueLLM (D8) needs a maintainer-local `unstable-api` build.** Gate A is unaffected because it uses the stub. Gate B gets a new precondition, B0. **Operator decision Q1.** |
| F2 | **QueueLLM has no teacher-forced top-N API, at any version.** `score()` returns only the realized token's logprob (`Vec<f32>`). Top-N exists only on `generate(logprobs=path, logprobs_top_n=N)`, i.e. for tokens the teacher *generates*. | `crates/queuellm-api/src/lib.rs` at v1.1.0 and main `343a3a86`. | "Extraction ants read logits per bounded corpus window" (brief §4.1) is not implementable. **Logit mode is designed as word-level KD on teacher-generated sequences:** the teacher answers task prompts built from corpus windows, top-N logprobs are captured per generated token, and the student is trained on those token ids with CE + top-N KL. This still satisfies D5's "soft labels via QueueLLM". The card records it as `distillation.logit_source: teacher_generated_topn`. |
| F3 | **`qkz isotope verify` verifies only the legacy 5-field payload.** The Rust verifier rebuilds `{dna_id, pipeline_run_id, chain_hash, gate_results, timestamp}`. Nucleus's current Python signer (`_build_signed_payload`, schema 1.0) signs **10** fields, so Nucleus's own current seals fail its own verifier. Floats in `gate_results` and any non-ASCII string also fail, because Rust's float formatting and non-escaping differ from Python's `json.dumps`. | Signed four payloads with Ed25519 and ran `qukaizen-nucleus/qkz/target/release/qkz isotope verify x --from-file`. Result: 5-field ✓, 10-field ✗, float ✗, non-ASCII ✗. | To honour "verifiable with `qkz isotope verify`", we sign the **5-field legacy payload**, and every value in it is ASCII with no floats (scores are serialized as strings). The upstream Nucleus signer/verifier mismatch is filed as a follow-up in Nucleus, not fixed here. |
| F4 | **`qkz isotope verify` trusts the key embedded in the seal.** It checks self-consistency, not identity. | `verify_dna_seal` reads `public_key_hex` from the seal itself. | A seal that passes `qkz` proves nothing about *who* signed it. ARAIL's own `nucleus verify` adds a trust-anchor check (`trusted_keys.txt`) and recomputes the card hash. |
| F5 | **`qkz` on this Mac's PATH is ARAIL's `arailctl`, not the Nucleus Rust CLI.** `~/.local/bin/qkz → ~/ProJects/arail/qkz → arailctl`. | `ls -la ~/.local/bin/qkz` | "`qkz isotope verify`" in docs must say "the Nucleus Rust `qkz` binary" and give its path. `arailctl nucleus verify` is the supported verifier. |
| F6 | **Tokenizer parity: Qwen3 MoE ⊇ Qwen2.5, and Llama/Gemma have none.** Qwen3-30B-A3B-Instruct-2507 and Qwen3-235B-A22B have the identical 151 643-entry base vocab, and all 22 Qwen2.5 added tokens sit at identical ids. Qwen3 adds 4 ids (151665–151668). Llama-3.1-70B (128 k vocab) and Gemma-4 (262 k) share nothing with Qwen2.5. | `tokenizer.json` comparison over `/Users/Shared/models`. | This resolves VISION note 3 with evidence. The dense Llama-70B **cannot** be a logit teacher for a Qwen2.5 student. `teacher.model: auto` selects **Qwen3-30B-A3B-Instruct-2507-4bit** (MoE, 128 experts/8 active, 16 GB, `qwen3_moe` is supported at the pinned commit), which keeps QueueLLM on its MoE lane. Parity is "superset": teacher alternatives with ids > 151664 are dropped and renormalized, and the dropped mass is recorded. |
| F7 | **The operator's `.env` says `LAB_MODE=hybrid`**, not airgapped as VISION assumes. | `~/ProJects/arail/.env` | W1 requires setting `LAB_MODE=airgapped` explicitly for the item-10 run. `nucleus build` prints the effective mode in its first line, and the card records it. |
| F8 | **Nothing item 10 needs is on disk yet:** no Linux clone, no kernel `vulns` repo, no `Qwen2.5-3B-Instruct` student base (only 0.5B and 7B). | `ls /Users/Shared/models`, home-dir search | Staging requires network *before* the airgapped run. That is an explicit operator step (§11, Q7), never done by `nucleus build`. |
| F9 | **`AeroLLMBackend` is a process-wide singleton keyed by the `AEROLLM_MODEL` env var**, and it has no model parameter. | `router/backends.py:1503–1700` | Nucleus needs three different models (teacher, judge, student), and it cannot reuse `AeroLLMBackend` without mutating env. The Nucleus QueueLLM provider constructs `aerollm_api.Runtime(model_path, **kwargs)` directly in a phase subprocess. |
| F10 | `cryptography` is **not installed** in the maximus venv. `jsonschema` and `numpy` are present only transitively. | import probe; `pyproject.toml` | New declared dependencies are required (§4.12). |
| F11 | `src/arail/build/manifest.py` writes into the sibling repo at the hardcoded default `~/ProJects/qukaizen-nucleus/configs`. That is an unversioned cross-repo edge (workspace CLAUDE.md calls this a defect). | `manifest.py:27–31` | Retiring `/build` deletes it. This is debt repaid. |

---

## 1. Restatement

We are turning distillation into a local, reproducible, honestly scored loop inside ARAIL. A `domain.yaml` plus a
pre-staged, hash-pinned corpus snapshot goes in. `arailctl nucleus build` then runs these phases:

- **preflight:** memory plan, Buddy protection, tokenizer parity, runtime capability.
- **extract:** the QueueLLM teacher generates task answers with top-N logprobs.
- **train:** MLX LoRA with a KD loss, driven by an Arbitrage loop that only ever sees the dev split.
- **eval:** student, base student, and teacher, all served by QueueLLM, are scored on a frozen, quarantined,
  temporally split cert set. Scoring uses F1 for closed tasks, a length-controlled pairwise judge that is never
  the teacher, and executable kernel checks. Everything is bound by an `eval_hash`.

`certify` then refuses on contamination ≥ 1 %. Otherwise it writes a schema-valid DNA card v2, a build report
with 10 eyeball pairs, and a seal that the Nucleus Rust `qkz isotope verify` accepts. The whole loop must run in
CI against a stub provider on a 50-item synthetic fixture, with zero egress, before anyone spends up to 8 hours
of M5 time on the real `qkz-linux-kernel` build.

The deliverable is the *yardstick* (splits, contamination, hash, baselines). A LoRA is a side effect. `/build`
and its docker-Nucleus client are deleted. `/forge` is a read-only card viewer.

---

## 2. Assumptions

Each assumption names what happens if it turns out false.

1. **The operator approves an `unstable-api` local build of `aerollm-api` for Gate B / item 10 (F1).** If not:
   item 10 cannot run in logit mode, and disconfirming evidence #1's action applies (re-decide D5/D8). Gate A is
   unaffected.
2. The `unstable-api` generate-with-logprobs path at the QueueLLM commit we build is numerically the same as the
   plain generate path for greedy decoding, so logprob capture does not change the teacher's tokens. *If false:*
   teacher text differs between capture and non-capture runs. The B1 spike therefore compares greedy outputs with
   and without capture on 5 prompts.
3. Top-N = 20 captures ≥ 90 % mean probability mass for Qwen3-30B-A3B on kernel text. *Detected:* the mean
   `captured_mass` is recorded per build and the spike reports it. If it's < 0.9, raise N to 40 before item 10.
4. MLX LoRA on a 3B-4bit base plus our custom KD loss fits comfortably beside Buddy on 36 GB. The salvaged
   estimator predicts ~7 GB.
5. `mlx_lm` internals used for the custom training loop (model load, `linear_to_lora_layers`, fuse) are stable
   within `mlx-lm>=0.31,<0.32`. Installed today: 0.31.3. *If false:* preflight's version check refuses with the
   supported range.
6. `git apply --check --cached` against a temp index built with `read-tree <base>` is a faithful "patch applies"
   check. It needs no working-tree checkout, which sidesteps macOS case-insensitivity (VISION disconfirming #4).
   `checkpatch.pl --no-tree` is a faithful "checkpatch clean" check without a tree.
7. A full `compiles` check is **not** possible on the M5 this sprint (no Linux build host or toolchain).
   `compiles` is `not_run`, and composite formula `composite/v1-nc` applies. This was pre-authorized by
   VISION disconfirming #4; the replacement formula is Q4.
8. Buddy runs in the portal process (plus Ollama). Nucleus phases run in **separate** processes, so Nucleus can
   never evict Buddy in-process. The only remaining risk is OS-level memory pressure, and preflight budgets
   against that.
9. The kernel `vulns` repo gives a commit → CVE mapping good enough for `cve_detection` ground truth, and
   `MAINTAINERS` file patterns give a deterministic `subsystem_routing` label.
10. `ARAIL_MODELS_DIR` is either repo-relative and git-ignored (`lab/models`) or outside the repo
    (`/Users/Shared/models`). Anything else is refused (§4.3).
11. Canonical JSON via CPython `json.dumps(..., sort_keys=True)` is stable across CPython 3.10–3.13 for
    str/int/bool/None/float, which is all our hash inputs contain.
12. The judge `ai-engineer` (Qwen2.5-7B-Instruct) is the same family as the student. Self-family preference is
    symmetric between student and base student (both Qwen2.5), so it cancels in `lc_win_rate_vs_base`. It does
    **not** cancel in the teacher baseline (Qwen3 vs Qwen2.5). The card notes this.

---

## 3. Resolutions of the VISION architect notes and brief concerns

| Concern | Resolution |
|---|---|
| **N1 `runtime: queuellm` vs frozen AERO surface** | One module, `src/arail/nucleus/runtime_names.py`, is the **only** place in `arail.nucleus` where the strings `aerollm` / `aerollm_api` / `AERO` appear. It maps the user-facing name `queuellm` → backend id `"aerollm"`, import module `"aerollm_api"`, and registry id `"tier1-aerollm"`. The card always reads `runtime: queuellm`. **Nucleus sets no env vars for the runtime at all.** Every knob (`ring_depth`, `kv_memory_budget`, `draft_model`) is passed as a `Runtime(...)` constructor kwarg, so the `AERO_*` vs `QUEUELLM_*` question never arises, whatever the bundle version. A grep test enforces both rules. Provenance records the **actual loaded `.so` sha256**, compared against `BUNDLE.json.sha256`, giving `source: bundle` or `source: local-build`, plus `features` and `module_version`. Nothing is hand-typed. |
| **N2 top-N only** | Top-N = 20 (configurable `distill.top_n`, 1–64). Renormalization is `topn_softmax`: softmax over the captured top-N logprobs after dropping ids outside the student vocab. `captured_mass` and `dropped_mass` are recorded. These are **training** parameters, so they go in `pipeline_hash`, **not** `eval_hash` (they do not change scoring). Preflight probes the runtime with a 1-token `generate(logprobs=…)` inside the worker before Phase A. On `ValueError` it refuses with the exact rebuild instruction. The `'mlx'` shim target is refused the same way. |
| **N3 dense vs MoE teacher** | Resolved by F6. `auto` sorts candidates by tokenizer parity (required), then MoE, then fits resident in the Phase-A budget, then total params, then dir name. On this box that picks Qwen3-30B-A3B. Llama-70B is excluded (no parity). Qwen3-235B-A22B is streamable but ranks below 30B-A3B on "fits resident", and it would blow B1. The selection rationale is written to the card (`teacher.selection`). |
| **N4 24 GB plan vs 36 GB box; Buddy sharing the runtime** | Preflight reads **actual** RAM. `--memory-budget-gb N` / `NUCLEUS_MEMORY_BUDGET_GB` caps it to simulate a floor, and the 24 GB plan is a unit test. Whether Gate B must also pass at 24 is **Q3**. Nucleus never shares Buddy's runtime or `scheduler.inference_slot`. It runs its own `aerollm_api.Runtime` in a phase subprocess. The contention rule: Buddy's memory is *reserved* (`buddy_reserve_gb = max(measured, declared)`), GPU time is shared by Metal (Buddy latency may degrade and is sampled, not guaranteed), and phase workers run at `nice 10`. |
| **N5 corpus vs airgapped** | `nucleus stage` is a separate verb that **only reads local paths the operator passes on the CLI**. It never fetches (not even via `git fetch`). It writes a snapshot with a manifest sha256 that feeds `pipeline_hash`. Sources not staged are recorded as `absent`. Each staged source carries a license row in the card (`git:linux` GPL-2.0-only, `redistributable: true`; `lwn` would be `redistributable: false`, absent this sprint). Acquisition (git clone) is the operator's explicit, networked, pre-run step. |
| **N6 `CERTIFIED_MODELS.md` auto-append** | By default `certify` appends to a **local, untracked ledger** `lab/data/nucleus/CERTIFIED_SHARDS.md`. Only `certify --publish-row` writes `docs/CERTIFIED_MODELS.md`, and only inside a delimited generated section (`<!-- nucleus:shards:begin/end -->`). Rows are idempotent per (shard, version). Stub, unsigned, or untrusted-key cards are never written. The acceptance criterion is met on item 10 with `--publish-row` (**Q5**). |
| **N7 output location** | Shards go to `$ARAIL_MODELS_DIR/forge/<shard>/<version>/`. That is git-ignored `lab/models/…` by default, or `/Users/Shared/models/forge/…` on the operator box. Private intermediates (cert set, teacher logits, keys) live in `lab/data/nucleus/` (0700). The brief's repo-root `models/<slug>/` is **not** used. |
| **N8 Grafana** | Dropped. Progress goes to three places: `ActivityLog.emit(source="nucleus", …)` (lands in `activity.jsonl`; SRE reads it), `runs/<build_id>/run.json`, and `arailctl nucleus status`. The portal SSE does **not** see CLI-process events live (ActivityLog is a per-process singleton). That is debt, recorded. |
| Brief §2 GGUF output | **Not built this sprint.** `mlx_lm` GGUF export does not cover Qwen2. The card records `weights.gguf: not_built`. The fused MLX dir is loadable by QueueLLM (`AEROLLM_MODEL=<abs path>`), and the report prints that line. |
| Brief §4.1 "soft labels → LanceDB" | Replaced by sharded `.npz` (`ids int32[T,N]`, `logprobs float16[T,N]`, `sampled int32[T]`) plus `index.jsonl`. KD training needs sequential memory-mapped reads, not vector search. This is not a locked decision. |
| Brief §5.3 `hallucination_rate`, §5.6 `build_energy_est` | Schema allows `{status: not_run, reason}`. Both are `not_run` in sprint 1 (no reference-grounded claim checker; energy needs `sudo powermetrics`). Filed as debt. |
| Tier split (brief §2) | `plan`, `list`, `status`, `verify`, and `/forge` view work on **minimalist**. `stage`, `build`, `eval`, `certify`, and `spike` require **maximus** plus the deep runtime, else exit 3 with "needs the maximus deep runtime — `./arailctl tier maximus`, then `./arailctl deep install`". |
| `plan` without Buddy | `plan "<intent>" --name <slug>` writes `configs/domains/<slug>.yaml` from a template with defaults, then prints the preflight memory plan. There is no interview. The Buddy `nucleus-partner` skill (item 4) will later call the same `plan` API. |
| Arbitrage vs cert-only fidelity | Arbitrage stops on `dev.proxy_composite`: closed F1 plus executable checks, no judge. That avoids a judge load every cycle. `fidelity.achieved` is **always** `cert.composite`, measured once in Phase C. Between-cycle dev evals run the student through MLX (part of the training step, allowed by D8), and the card records `arbitrage.dev_eval_runtime: mlx`. Every headline number is produced by QueueLLM. |
| Naming overload | "Agent Forge" (`agents/forge.py`) and "World Forge" already exist. The nav label is **"Model Forge"**, and the route is `/forge` (currently unused). |

---

## 4. Components and interface contracts

### 4.0 Package layout

```
src/arail/nucleus/
  __init__.py            __main__.py (python -m arail.nucleus → cli.main)
  cli.py                 verbs: plan stage build spike eval certify verify status list publish
  runtime_names.py       queuellm ↔ frozen aerollm surface (ONLY place 'aerollm' appears)
  domain.py              domain.yaml load/validate (spec/nucleus-domain-v1.schema.json)
  paths.py               layout, id/slug/version validation, git-ignore guard, locking
  models.py              local model resolution, identity hashing, aliases, <8B check
  tokenizer_parity.py    exact | superset | none
  preflight.py           memory plan + Buddy reserve + capability checks (salvaged estimator)
  residency.py           Buddy residency sampler + violation classifier
  phases.py              phase runner: one subprocess per phase, D3 enforcement
  worker.py              python -m arail.nucleus.worker <phase> <build_id>
  arbitrage.py           dev-only training loop controller + stop rules
  build.py plan.py eval.py certify.py publish.py spike.py   (verb implementations)
  corpus/stage.py        snapshot writer; corpus/sources/{git_kernel,cve_vulns}.py
  providers/base.py      Provider protocol + capabilities
  providers/queuellm.py  aerollm_api.Runtime adapter (worker-side)
  providers/fallback_ollama.py   eval-only generation fallback
  providers/stub.py      deterministic canned provider + stub trainer (CI)
  providers/gateway.py   contract client (sprint-2 seam)
  providers/mixed.py     config composition only
  train/kd_loss.py       numpy reference KD loss (CI-testable)
  train/mlx_kd.py        MLX LoRA + KD training loop, fuse (requires_mlx)
  evals/splits.py        temporal + dev split, CertStore
  evals/contamination.py
  evals/closed.py        precision/recall/F1/macro-F1
  evals/open_lc_judge.py pairwise, position-randomized, length-controlled
  evals/executable_kernel.py
  evals/composite.py     formula registry + decision rule registry
  evals/hash.py          EvalHashInputs + eval_hash; pipeline_hash
  evals/tasks/linux_kernel.py   task adapters (prompt templates, labels)
  cards/dna_v2.py        card assembly + canonical hash + schema validation
  cards/seal.py          legacy-5 Nucleus seal sign/verify, key custody, trust anchors
  cards/build_report.py
  cards/certified_models.py
src/arail/world_catalog.py        (moved from build/world_corpus.py — see §8)
spec/dna-card-v2.schema.json
spec/nucleus-domain-v1.schema.json
configs/domains/linux-kernel.yaml, linux-kernel.eyeball.txt
src/arail/portal/forge_api.py, templates/forge.html
docs/nucleus.md, docs/nucleus-architecture.md, docs/nucleus-gateway-contract.md
tests/nucleus/…, tests/fixtures/nucleus/…
.github/workflows/nucleus-tests.yml
```

### 4.1 `runtime_names.py`

- **Promises:**
  - `USER_RUNTIME = "queuellm"`, `BACKEND_ID = "aerollm"`, `PY_MODULE = "aerollm_api"`, `REGISTRY_ID = "tier1-aerollm"`.
  - `resolve_user_runtime(name) -> RuntimeBinding` for `queuellm|ollama|airllm|stub`.
  - `runtime_provenance(module) -> dict`: `{runtime, backend_id, module, module_version, so_sha256, bundle_tag, bundle_so_sha256, source: bundle|local-build, features: [..]|unknown}`. `features` is the list of `unstable-api`-gated capabilities found by the capability probe.
- **Requires:** nothing. Pure constants and a file hash.
- **Bad input:** unknown runtime name → `DomainConfigError("runtime must be one of queuellm|ollama|airllm")` (`stub` is accepted only when `ARAIL_NUCLEUS_STUB=1`).
- **Invariant (grep test):** outside this file, `src/arail/nucleus/**` contains no `aerollm`, `AERO_`, or `QUEUELLM_` literals. No `os.environ[...] =` writes appear anywhere in `arail.nucleus`.

### 4.2 `domain.py` (`nucleus.domain/v1`)

- **Promises:** `load_domain(name) -> DomainConfig` (frozen dataclass, defaults applied, canonical bytes available for `pipeline_hash`).
- **Requires:**
  - `name` matches `^[a-z0-9][a-z0-9-]{0,62}$`. It resolves to `configs/domains/<name>.yaml` only (no user-supplied paths).
  - File ≤ 64 KiB, loaded with `yaml.safe_load`.
  - Unknown keys are **rejected**, so a typo fails loudly.
- **Field rules:**
  - `student.base`: a model-dir name or known alias, with no `/` except a known alias form. It must resolve under `ARAIL_MODELS_DIR`, and its estimated parameter count (from `config.json`) must be < 8e9.
  - `teacher.profile ∈ {local, gateway, mixed}`.
  - `runtime ∈ {queuellm, ollama, airllm}`, default `queuellm`.
  - `eval.cert_set`: `frozen` only. `refresh:<d>` → "not supported until a later sprint".
  - `eval.eyeball_prompts`: must realpath-resolve inside `configs/domains/`, be a regular file, and contain exactly 10 prompts.
  - `corpus.sources`: each entry is an **identifier** `kind:name` with `kind ∈ {git, cve, lkml, lwn, world}`, never a path.
  - `corpus.cutoff`: an ISO date string. The YAML must quote it or it is coerced. The loader accepts `datetime.date` and normalizes it.
  - `fidelity.target ∈ (0,1]`.
  - `eval.dev_fraction ∈ (0, 0.5]`.
  - Optional `eval.cert_n` (default 800), `distill.top_n` (default 20), `shard` (default `qkz-<name>`).
- **Bad input:**
  - Legacy Nucleus superskill manifests (top-level `superskill:` key, e.g. `qukaizen-project-aware.yaml`) → `DomainConfigError("this is a legacy Nucleus superskill manifest, not a Model Forge domain")`.
  - Every error names the key and the allowed values. No traceback reaches the CLI user.

### 4.3 `paths.py`

- **Layout:**
  - Private root: `NUCLEUS_DATA = DATA_DIR/"nucleus"` (dirs 0700). Contains `keys/`, `corpus/`, `domains/<d>/cert/<cert-vN>/`, `domains/<d>/dev/`, `runs/<build_id>/`, `CERTIFIED_SHARDS.md`, `build.lock`, `hash-cache.json`.
  - Shard root: `FORGE_ROOT = ARAIL_MODELS_DIR/"forge"`.
- **Promises:**
  - `build_id` = `<domain>-<YYYYmmddTHHMMSSZ>-<4 hex>`, matching `^[a-z0-9-]{1,63}-\d{8}T\d{6}Z-[0-9a-f]{4}$`.
  - `shard_dir(shard, version)` returns a realpath strictly under `FORGE_ROOT`. Version is strict semver. **An existing version dir is never overwritten** (`--version` required to pick another; the default is next patch).
- **Git-ignore guard:** if `FORGE_ROOT` or `NUCLEUS_DATA` resolves inside the repo worktree, then `git check-ignore -q <path>` must succeed, otherwise refuse (exit 3): "output would be committable".
- **Locking:** `build_lock()` takes a `fcntl.flock` exclusive non-blocking lock on `build.lock` and writes `{pid, build_id, started}`. A second build → exit 3, naming the running `build_id`. A stale lock (pid dead) is taken over, with a warning.

### 4.4 `models.py` + `tokenizer_parity.py`

- `resolve_model(name_or_alias) -> LocalModel{name, path, config, params_est_b, is_moe, weights_bytes}`.
  - Resolves only under `ARAIL_MODELS_DIR`. Absolute paths are allowed only via CLI flag, never from YAML.
  - Aliases table: `qwen2.5-3b-instruct → Qwen2.5-3B-Instruct-4bit`, `ai-engineer → Qwen2.5-7B-Instruct-4bit`.
  - Missing → error with the exact `hf download` command plus "needs network — do this before switching to airgapped".
- `model_identity(model) -> sha256`: over `config.json`, `tokenizer.json`, and the `*.safetensors` index plus each shard's (size, first 1 MiB sha). This is the **cheap identity** used for judge ≠ teacher and for `eval_hash`. `content_hash(model)`: a full sha256 of all weight files, cached by (path, size, mtime) in `hash-cache.json`. It is used for the seal's `teacher_hash`/`training_hash`.
- `parity(student, teacher) -> Parity{kind: exact|superset|none, student_max_id, extra_teacher_ids, detail}`:
  - **exact** means identical `model.vocab`, identical added-token id map, and equal `normalizer`/`pre_tokenizer`.
  - **superset** means the student map ⊆ the teacher map with identical ids, and the teacher's extras are all > `student_max_id`.
  - Anything else is **none**.
  - `none` in logit mode → preflight refuse (never silently switch mode — VISION disconfirming #1).
- `select_teacher(auto, student, budget, exclude) -> (LocalModel, rationale)`: ordering per §3 N3. `exclude` is the Buddy models ∪ judge ∪ student base. It is deterministic.

### 4.5 `preflight.py` (salvaged from `build/preflight.py`)

- **Salvaged verbatim or near-verbatim:** `_capacity()` (psutil RAM, 0.75 Metal ceiling, disk free), `active_params_b()`, `_status()` green/amber/red, the `Requirement`/`PreflightReport` dataclasses, and the LoRA memory estimator body (weights + optimizer + activations + 20 %).
- **Dropped:** Anthropic pricing, teacher amplification, remote rows. They belong to sprint 2 and a gateway-shaped estimator.
- **Promises:** `run_preflight(domain, *, capacity=None, buddy=None) -> PreflightReport`. `capacity`/`buddy` are injectable for tests.
  - `budget_gb = min(ram_total × 0.75, --memory-budget-gb) − buddy_reserve_gb − reserve_gb(2.0)`.
  - `buddy_reserve_gb = max(measured, declared)`:
    - *measured* = portal process RSS (portal pid from the instance registry) + Ollama `/api/ps` `size_vram` for Buddy's models (loopback; no egress line).
    - *declared* = on-disk size of the configured Buddy models (Ollama tier model + `AEROLLM_MODEL` dir if deep is enabled).
    - Buddy not running ⇒ declared only. The plan must hold for when Buddy *is* running.
  - Per-phase requirement:
    - A = teacher resident (weights × 1.10 + KV budget) **or**, if QueueLLM is present and the resident size exceeds the budget, the streamed window (≤ min(6 GB, budget − 1 GB), with `ring_depth` computed).
    - A2 (teacher on cert) = same as A.
    - B = the student LoRA estimate.
    - C = max(student, base_student, judge), since they are loaded **sequentially**.
  - Capability rows:
    - deep runtime importable
    - logprobs capability (probe in a worker)
    - tokenizer parity
    - `mlx_lm` version range
    - student < 8B
    - corpus snapshot staged
    - cert-eligible items after cutoff ≥ `cert_n`
    - `checkpatch.pl` present in the staged snapshot
    - disk: logits (≈ 170 B/token × est. teacher tokens) + adapter + fused weights + 20 %
    - effective `LAB_MODE`
- **Refusal contract:** any red row → `PreflightRefusal(rows, phase, needed_gb, budget_gb, drop: list[str], protected: list[str])`, printed as:
  > Phase A needs 23.4 GB but the budget is 18.1 GB. Drop: teacher `Qwen3-235B-A22B-4bit` (try `Qwen3-30B-A3B-Instruct-2507-4bit`). Protected, never evicted: Buddy (`llama-ai-eng`, `Qwen2.5-7B-Instruct-4bit`).
  - `drop` is the largest non-Buddy model in the failing phase. **`drop ∩ protected = ∅` is an invariant.**
  - Exit 3. No override flag exists for memory refusals in sprint 1.

### 4.6 `residency.py`

- Samples every 10 s, from the parent orchestrator during every phase: portal RSS, Ollama `/api/ps` entries (name, size, `expires_at`), and system available memory. Writes `runs/<id>/residency.jsonl`.
- `classify(samples) -> {status: ok|violated|unmeasured, max_drift_pct, events}`:
  - **violated** if portal RSS drops > 5 % from its phase-start value, or if a Buddy Ollama model disappears **before** its recorded `expires_at`. A keep-alive TTL lapse (default `ARAIL_OLLAMA_KEEP_ALIVE=2h`) is not an eviction and is reported as `ttl_expired`.
  - **unmeasured** if Buddy wasn't running.
- Recorded in the card as `residency:`. B3 reads it. A violation does not abort the build. It is printed prominently, and it blocks the `CERTIFIED` decision (capped at `COMPATIBLE`, `decision_rule/v1`).

### 4.7 Providers

```python
class Provider(Protocol):
    runtime: str                          # 'queuellm' | 'ollama' | 'stub' | 'gateway'
    def capabilities(self) -> Caps        # {generate, logprobs_topn, max_top_n}
    def generate(self, prompts: list[Prompt], decoding: Decoding) -> list[Generation]
    def generate_with_topn(self, prompts, decoding, top_n) -> list[TopNGeneration]   # logit mode only
    def close(self) -> None               # must release model memory
```

- **`providers/queuellm.py` (worker-side only):**
  - Imports `runtime_names.PY_MODULE` lazily.
  - `Runtime(model_path, backend="mlx-native", ring_depth=…, kv_memory_budget=…)` is constructed and used on one pinned thread (same rationale as `AeroLLMBackend`, F9).
  - `generate_with_topn` calls `generate(prompt, logprobs=<tmp>.jsonl, logprobs_top_n=N, temperature=0, max_new_tokens=…, seed=…)`. The tmp file lives in `runs/<id>/extract/tmp/` (0600, written by the runtime). It is parsed into arrays and **deleted immediately**; only the `.npz` shard persists, flushed per batch of 64 windows to limit SSD writes.
  - `ValueError` containing `unstable-api` → `CapabilityMissing("logprobs")`.
  - Also refused: backend `mlx` shim.
  - Prompts use the model's own chat template via its tokenizer (mirror `AeroLLMBackend._wrap_prompt` semantics).
- **`fallback_ollama.py`:** `generate` only, via loopback Ollama native API; `capabilities().logprobs_topn = False`. Used **only** when `import aerollm_api` raises `ImportError`, and only for eval-only roles (judge, student/base generation in `nucleus eval`). A local-profile `build` with no QueueLLM → preflight refuses Phase A (no fallback teacher). There is no AirLLM provider this sprint.
- **Selection:** `select_local_provider(role) -> Provider`. It tries QueueLLM first. `ImportError` → fallback, and the card's `runtime` field for that role reads `ollama`. A **test asserts Ollama/AirLLM classes are never constructed when QueueLLM imports** (A5).
- **`stub.py`:**
  - Deterministic: tokens and top-N are derived from `sha256(prompt)`. Generations for student/base/teacher come from fixture answer files keyed by item id and role.
  - The stub judge prefers per a fixture table.
  - The stub trainer writes a small fake adapter plus a `quality` marker that selects which fixture answers the "student" returns, so metrics are known exactly and golden-testable.
  - Enabled only when `ARAIL_NUCLEUS_STUB=1`. Every card it touches has `runtime: stub` everywhere.
  - Signing is an **ephemeral key** (never the lab key). It is never appended to any ledger, and `/forge` shows a red "STUB — not a real model" badge.
- **`gateway.py`** — see §4.11. **`mixed.py`** is a config composition (gateway teacher + local judge) with no new transport.

### 4.8 Phases, worker, Arbitrage (D3)

```
stage (explicit, before build)
build:  P0 preflight → PA extract(teacher, train prompts) → PA2 teacher-on-cert (baseline + floor)
        → PB train (Arbitrage: LoRA cycles, dev-only) → fuse → PC eval (student, base on cert; then judge)
        → certify (contamination gate → card → seal → report → ledger)
```

- **One subprocess per phase:** `python -m arail.nucleus.worker <phase> <build_id>`, niced to 10. The parent waits for exit **and** confirms the child pid is gone before starting the next phase. Model memory is reclaimed by process exit, not by trusting GC or Metal.
- **D3 enforcement:** `phases.py` records `{phase, pid, start, end}` in `run.json`. An invariant check refuses to start phase B while any PA/PA2 pid is alive. The stub e2e asserts the phase intervals are non-overlapping.
- **Worker environment:** `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `HF_DATASETS_OFFLINE=1`, `TOKENIZERS_PARALLELISM=false`. It calls `egress.install_guard()` at entry. Models load **only** from resolved local dirs, never repo ids. This is how zero egress lines is achieved, not just asserted.
- **Resume:** each phase writes `phase_done` with its output hashes to `run.json`. `build --resume <build_id>` skips completed phases after re-verifying those hashes. Phase A also skips windows already in `extract/index.jsonl`.
- **Arbitrage:**
  - Signature: `run(dev: DevSet, train: TrainShards, cfg) -> ArbitrageResult`. There is **no cert parameter**, and `evals.splits.CertStore` is not importable from `arbitrage.py`. An import-lint test enforces both.
  - Stop rules, first to fire wins: `dev.proxy_composite ≥ target` → `target_reached`; no improvement ≥ 0.005 for 3 cycles → `fidelity_plateau_3_cycles`; `max_cycles` or `max_hours` → `budget`.
  - Each cycle appends to `arbitrage.jsonl`.

### 4.9 Evals

- **`splits.py`:**
  - Train/dev pool = items with `date ≤ cutoff`. Dev = the items where `int(sha256(item_id)[:8],16) % 1000 < dev_fraction×1000`, which is deterministic.
  - Cert = items with `date > cutoff`, sampled by sorted sha to `cert_n`. Fewer than `cert_n` eligible items → refuse, with the count.
  - Cert items are never in the train pool, by construction.
- **`CertStore`:**
  - `create(domain, snapshot) -> CertVersion`: writes `cert.jsonl` + `manifest.json{version, sha256, n, cutoff, snapshot_sha}` and sets files to 0444.
  - `frozen`: an existing `cert-vN` for the domain is **reused** across builds. A new version is only minted by explicit `stage --new-cert-version`.
  - `open(access: CertAccess)`: `CertAccess` is minted only in `eval.py`/`certify.py`/`spike.py`. It re-hashes the file on every open, and a mismatch raises `CertTampered`.
- **`contamination.py`:**
  - Normalize: lowercase, collapse whitespace, split on `\W+`.
  - 13-gram 64-bit hashes of the **cert** items are held in memory, and the student's actual training material (prompts + teacher outputs) is streamed past them. Memory is O(cert).
  - Boilerplate stop-list: 13-grams present in > 5 % of train documents are ignored (license headers, SoB trailers).
  - A cert item is contaminated if its sha256 exactly matches a train item, or if ≥ 50 % of its 13-grams occur in train.
  - `overlap = contaminated/n_cert`. Also `temporal_leak` = any train item dated > cutoff.
  - `check() -> ContaminationReport{method, params, overlap, temporal_leak, top_offenders[≤10 ids]}`.
  - **`certify` refuses when `overlap ≥ 0.01` or `temporal_leak != "none"`** (exit 3). No signed card is written; the report explains why.
- **`closed.py`:**
  - Binary precision/recall/F1 (positive class declared per task) and macro-F1 (unweighted mean over classes present in gold).
  - Zero-division → 0.0 with `warn`.
  - Returns only `{precision, recall, f1, n, support}` or `{macro_f1, per_class, n}`. **No `accuracy` key is ever produced**, and the schema forbids it under `closed_ended`.
- **`open_lc_judge.py`:**
  - Pairwise (A vs B). Position is randomized per item by `Random(seed ^ item_idx)`, with the order recorded.
  - The judge outputs `A|B` with a constrained parse. Anything else counts as `invalid`, and an invalid rate > 10 % marks the metric `unreliable: true`.
  - Length control is AlpacaEval-2-style: logistic regression `win ~ β0 + β1·tanh(Δlen/σ)`, and `lc_win_rate = σ(β0)`.
  - `ci95` comes from a seeded bootstrap (1000 resamples).
  - **Judge identity assertion:** `model_identity(judge) ∉ {identity(teacher), identity(student_base)}` and ≠ the fused student. This compares by content identity, not by name, so aliases can't sneak through. A violation raises `JudgeIsTeacher` before any generation.
- **`executable_kernel.py`:**
  - Input is an untrusted model-generated patch plus a base commit. Patches are capped at 64 KiB, must be text only, and a 30 s timeout applies.
  - `patch_applies`: `GIT_INDEX_FILE=<tmp> git read-tree <base>`, then `git apply --check --cached -` (stdin) with a hardened environment:
    - `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`, `HOME=<tmp>`
    - `-c core.hooksPath=/dev/null -c core.fsmonitor=false -c protocol.allow=never`
    - `--unsafe-paths` never passed
  - Nothing is written to any working tree.
  - `checkpatch_clean`: `perl <snapshot>/scripts/checkpatch.pl --no-tree --terse -` from the operator-staged snapshot. It is never vendored, because it is GPL-2.0 and ARAIL is MIT. Same timeout and a `setrlimit` CPU/AS cap.
  - `compiles`: returns `NotRun("requires a Linux build host; not available in sprint 1")`. **Model output is never executed.**
- **`composite.py`:** a registry of versioned formulas.
  - `composite/v1` = `0.4*closed.mean_f1 + 0.3*open.lc_win_rate + 0.3*executable.compiles`
  - `composite/v1-nc` = `0.4*closed.mean_f1 + 0.3*open.lc_win_rate + 0.3*mean(patch_applies, checkpatch_clean)`. It is selected automatically when `compiles` is `not_run` (**Q4**).
  - `composite/v1-open` = `0.6*closed.mean_f1 + 0.4*open.lc_win_rate` (added in review loop 1, accepted in review
    round 2). It is selected automatically when **all three** executable checks are `not_run`, which is every run this
    sprint because no patch-generation task exists. The weights are published, not a proportional renormalisation of
    v1 (that would be 4/7 and 3/7). **Gate B precondition:** before B7's non-stub refusal is lifted, a card scored
    under `composite/v1-open` must be capped at `COMPATIBLE`, because a kernel shard with no executable evidence
    cannot be `CERTIFIED`. Retire the formula once `patch_applies`/`checkpatch_clean` are wired.
  - **Open-ended eval set (drift, recorded in review round 2).** For the Gate A stub path, PC judges fused vs base on
    the domain's 10 eyeball prompts, not on cert items as brief §5.3 intends (`patch_explanation`, n≈100). This is
    acceptable only for Gate A, and only if those prompt bytes and the judge identity/rubric are inside `eval_hash`.
    Gate B moves the open eval onto cert items so it is covered by `CertStore`'s freeze, tamper check, and
    contamination check.
  - `decision_rule/v1`, evaluated in order:
    1. KNOWN_ISSUE if the student does not beat base (the W2 test fails).
    2. BETA if `achieved < target − 0.05`.
    3. COMPATIBLE if `achieved < target` **or** residency was violated.
    4. CERTIFIED otherwise.
- **`hash.py`:**
  - `EvalHashInputs` is a frozen dataclass whose fields are **exactly**:
    - `harness_version`: a constant bumped by hand.
    - `prompts`: task template bytes.
    - `few_shot`: exemplar bytes + k.
    - `scoring`: metric defs, positive classes, composite formula id + string, decision rule id, judge rubric bytes, judge `model_identity`, LC method + params, bootstrap n/seed, position-randomization seed, executable check list + checkpatch.pl sha256 + git version, contamination method/params.
    - `decoding`: student/base/teacher eval decoding (temperature, top_p, max_new_tokens, stop, seed).
    - `cert_set_version`: cert manifest sha256.
  - `eval_hash = "sha256:" + sha256(canonical_json(asdict(inputs)))`, where `canonical_json = json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=True, allow_nan=False)`.
  - **Excluded by construction:** timestamps, paths, run/build ids, host, training seeds, top-N, student identity.
  - `eval-config.lock` (the canonical inputs JSON) is written beside the card so anyone can recompute the hash.
  - `pipeline_hash` = sha256(canonical{nucleus source hash (sorted file bytes of `src/arail/nucleus/**/*.py`), domain canonical bytes, corpus manifest sha, teacher identity, student base identity, distill params (top_n, renorm, teacher decoding), training hyperparams}).

### 4.10 Cards, seal, report, ledger

- **`dna_v2.py`:**
  - Builds the card dict per brief §5 with these deviations:
    - `signed` is an object (below).
    - `distillation.teacher.provenance` is `runtime_provenance()` (§4.1).
    - Adds `distillation.logit_source`, `top_n`, `renorm`, `captured_mass`, `dropped_mass`.
    - Adds `tokenizer_parity` (bool) + `tokenizer_parity_detail`.
    - Adds `teacher.selection`, `corpus.sources[{id, license, redistributable, origin_commit, items}]`, `lab_mode`, `residency`, `arbitrage{stop_metric, dev_eval_runtime}`, `composite.formula_id`, `fidelity.decision_rule`, and `weights{mlx, gguf: not_built}`.
    - Every metric may be `{status: not_run, reason}`.
  - All timestamps are **quoted strings** (the YAML dumper forces this), so `yaml.safe_load` never turns them into `datetime` and breaks the hash.
  - `card_sha256` = canonical_json of the card **minus `signed`**, computed on the *YAML-reloaded* dict (round-trip tested).
  - It is validated against `spec/dna-card-v2.schema.json` (draft 2020-12, `additionalProperties:false` on top-level and headline blocks) **before** signing. Invalid → no card.
- **`seal.py`:**
  - Key: `NUCLEUS_DATA/keys/signing.ed25519` is 32 raw bytes, mode 0600. This is Nucleus's `from_key_file` format, so `NUCLEUS_SIGNING_KEY_PATH` may point at the Nucleus lineage key (**Q2**). It is auto-generated on first maximus `certify`, with a loud "back this up" line. `signing.pub` holds the hex, and `trusted_keys.txt` is auto-seeded with the local pubkey.
  - Payload is **exactly** the legacy 5 fields:
    - `dna_id` = uuid4
    - `pipeline_run_id` = build_id
    - `chain_hash` = `sha256("|".join([corpus_hash, teacher_hash, config_hash, training_hash]))`, the same order and separator as Nucleus. Here `config_hash` = pipeline_hash hex and `training_hash` = content hash of adapter + fused weights.
    - `gate_results` = a list of `{"gate_name": str, "passed": bool, "value": str}` covering `card_sha256`, `eval_hash`, `decision`, `contamination` (value `"0.0021"` as a **string**), and `schema` = `"dna-card/v2"`.
    - `timestamp` = `datetime.now(UTC).isoformat()`
  - Signed bytes are `json.dumps(payload, sort_keys=True).encode()` (Python defaults, matching the Rust formatter).
  - **Guards:** every string is ASCII (`str.isascii()`, else `SealError`), there are no floats anywhere in the payload, and the domain name is already constrained to `[a-z0-9-]`.
  - Output: card `signed: {format: "nucleus-seal/legacy-5", dna_id, pipeline_run_id, chain_hash, gate_results, timestamp, public_key_hex, signature_hex, key_fingerprint}`, plus `seal.json = {"dna_seal": {…}}` so that `<nucleus>/qkz/target/release/qkz isotope verify <id> --from-file seal.json` works (F3/F5).
  - `verify(card_dir) -> VerifyResult{signature: valid|invalid, key: trusted|untrusted|ephemeral-stub, card_hash: match|mismatch, eval_hash: match|mismatch (recomputed from eval-config.lock), chain: match|mismatch|skipped(--fast)}`. `arailctl nucleus verify` exits 0 only if all are valid/trusted/match.
- **`build_report.md`:**
  - A two-line plain-language summary (the decision, whether the student beat its base, and by how much), then metric tables. The composite appears only in the table (per W3: the eyeball section is listed before the composite).
  - The 10 eyeball prompts come with verbatim outputs: student | base_student | previous version (if any).
  - The report is markdown with outputs in fenced blocks. The `/forge` renderer escapes them (§4.13).
  - It ends with "To chat with this shard: `AEROLLM_MODEL=<abs fused dir>`".
- **`certified_models.py`:**
  - `append(card, *, publish_row: bool)`. The default target is `NUCLEUS_DATA/CERTIFIED_SHARDS.md`. With `publish_row`, the target is `docs/CERTIFIED_MODELS.md` between `<!-- nucleus:shards:begin -->`/`end` markers; the markers are created once in a "Model Forge shards" section at the end of the file.
  - Rows are upserted by (shard, version).
  - Cell text is escaped for `|`, `<`, `>`, `` ` ``, newlines.
  - Refused: stub, unsigned, untrusted key, or `verify` not all-green.
  - **Bytes outside the markers are unchanged**, and a test asserts it.

### 4.11 Gateway contract (item 9)

- `docs/nucleus-gateway-contract.md` holds brief §4.2 verbatim, plus a concrete wire schema: request and response JSON for `/v1/nucleus/teach` and `/v1/nucleus/judge`; the required `build_id`, `pipeline_hash`, `model`, and `request_id` fields; error model `402 budget_exhausted`, `401`, `403 scope`, `429`; and no logprobs.
- **`GatewayClient(base_url, token)` contract:**
  - Before anything else: `airgap.is_airgapped()` → raise `ProfileRefused(AIRGAPPED_NOTICE)` **before** constructing any session. No socket and no `egress.jsonl` line.
  - `https` is required, except loopback (for the mock).
  - Exact host match against `NUCLEUS_GATEWAY_URL`.
  - `allow_redirects=False`: a 3xx is an error.
  - Every call runs inside `egress.allow_egress(f"nucleus-gateway:{build_id}")`, so hybrid calls are audit-logged. The token never appears in the reason.
  - The token is sent only in `Authorization: Bearer`. It is read from env or `secrets.env` and never logged. `__repr__` redacts it, and exception messages carry only host + status.
  - `402` → `BudgetExhausted` (the caller checkpoints and pauses; never falls back to another teacher). A response missing provenance fields → `ContractViolation`.
- **Profile gate:** `domain.teacher.profile ∈ {gateway, mixed}` under airgapped → exit 3 with `AIRGAPPED_NOTICE`. Under hybrid → exit 3: "the live gateway lands in nucleus-sprint-2". That is a clean seam.
- **`AIRGAPPED_NOTICE`:** moved into `arail/airgap.py` as a constant, reusing the Chat text at `app.py:1863`. Chat and Nucleus import the same string.

### 4.12 CLI (`arailctl nucleus <verb>`; `qkz nucleus` inherits via symlink)

- `arailctl`: new `nucleus)` case → `source .venv/bin/activate; exec python -m arail.nucleus "$@"`. Help text is listed in `usage`.
- **Verbs:**
  - `plan "<intent>" --name <slug>` | `plan <slug>` (preflight only)
  - `stage <slug> --source git:linux=<path> --source cve=<path> [--since D] [--max-commits N] [--new-cert-version]`
  - `build <slug> --profile local [--memory-budget-gb N] [--resume ID] [--version V]`
  - `spike <slug> [--windows 20] [--cert-sample 50]`
  - `eval <shard>@<ver>`
  - `certify <slug|build_id> [--publish-row]`
  - `verify <shard>@<ver>|<dir>`
  - `status [ID]`
  - `list`
  - `publish` → "registry publication lands in nucleus-sprint-3", exit 3
- **Exit codes:** `0` ok · `1` internal error · `2` usage / invalid config · `3` refused by policy (tier, airgap, preflight, contamination, lock, capability). Errors are one plain-language paragraph; tracebacks appear only with `--debug`.
- **Dependencies** (`pyproject.toml`): `numpy>=1.26` and `jsonschema>=4.18` go into core `dependencies` (the viewer and `plan` need them on minimalist). `cryptography>=42` goes into `maximus` and `dev`. On minimalist, `verify` prints "signature not checked (install maximus)" and exits 3. `mlx-lm` stays in the `mlx` extra; preflight checks the range `>=0.31,<0.32`.

### 4.13 `/forge` viewer (item 5 reduced)

- `forge` is added to `_TIER_SURFACES` for **both** tiers, with nav label "Model Forge".
- **Routes:**
  - `GET /forge` lists cards under `FORGE_ROOT/*/*/dna-card.yaml` plus in-progress runs from `runs/*/run.json` (static on page load; no SSE).
  - `GET /forge/{shard}/{version}` renders the card, the verify status badge (valid / untrusted / invalid / STUB), and the build report.
  - `GET /api/forge/cards` returns JSON.
- **Security:**
  - Path params are regex-validated (`shard ^[a-z0-9-]{1,64}$`, `version` semver), and the realpath must be under `FORGE_ROOT`.
  - `yaml.safe_load` with a ≤ 256 KiB cap.
  - Markdown rendered with `markdown-it-py` `html=False`. Model outputs are fenced and Jinja-autoescaped.
  - Verify runs with `--fast` (no weight hashing on page load).
- `/build` → `308` to `/forge`. `/api/build/*` → 404 (router removed).

---

## 5. Data flow

```
 operator (networked, BEFORE the run)          ─── git clone linux, vulns; hf download student base
        │ local paths only
        ▼
 arailctl nucleus stage ──► corpus/sources/{git_kernel,cve_vulns}  (git log -c hooks off; no fetch)
        │                         │ items.jsonl + manifest{sources, licenses, origin_commits, sha256}
        ▼                         ▼
 configs/domains/<d>.yaml ─► domain.py ──► splits.py ──► train/dev pool (≤cutoff) ──┐
        │                                   └────────► CertStore cert-vN (>cutoff, 0444, sha)──┐ (quarantined)
        ▼                                                                                    │
 arailctl nucleus build ─► paths.build_lock ─► P0 preflight (capacity, Buddy reserve, parity,│
        │                                        logprobs probe, LAB_MODE) ──refuse──► exit 3 │
        ▼                                                                                    │
  [worker PA]  QueueLLM Runtime(teacher) generate(logprobs top-N) on train prompts           │
        │         └─► extract/*.npz + index.jsonl   (tmp jsonl deleted per call)             │
  [worker PA2] QueueLLM Runtime(teacher) generate on CERT prompts ◄── CertAccess ────────────┤
        │         └─► eval/teacher.jsonl (baseline + irreducible_floor)                      │
  [worker PB]  Arbitrage(dev only) ⇄ mlx_kd LoRA cycles ⇄ dev proxy eval (MLX)               │
        │         └─► train/adapter/ → fuse → forge/<shard>/<ver>/weights/mlx                │
  [worker PC]  QueueLLM Runtime(student) → Runtime(base) → close → Runtime(judge) ◄──────────┘
        │         └─► eval/{student,base}.jsonl, judge.jsonl, executable.jsonl
        ▼
 certify: contamination(train material vs cert) ─≥1%─► REFUSE (no card)
        │ ok
        ▼
 metrics → composite(formula_id) → decision_rule/v1 → dna_v2 card (schema-validate)
        → eval_hash (eval-config.lock) → seal legacy-5 (Ed25519, key 0600) → seal.json
        → build-report.md (10 eyeball pairs) → ledger (local | --publish-row docs/)
        ▼
 $ARAIL_MODELS_DIR/forge/<shard>/<ver>/  ──read-only──►  /forge viewer (both tiers)

 side channels: residency sampler (parent, 10 s) → residency.jsonl ; ActivityLog(source="nucleus")
 never: network from any phase (HF_*_OFFLINE, egress guard installed, local model dirs only)
```

---

## 6. Failure modes

Every row has a test id from §7 (`T-…`).

| # | Failure | Detection | Recovery | Test |
|---|---|---|---|---|
| 1 | Shipped runtime lacks `unstable-api` (logprobs) — **the default state today** | Worker capability probe: `ValueError … unstable-api` | Preflight refuses Phase A with the exact rebuild line; no partial run | T-PROV-3 |
| 2 | QueueLLM not importable | `ImportError` in selection | local `build` refuses; `eval` falls back to Ollama; card `runtime: ollama` | T-PROV-1,2 |
| 3 | Code writes `QUEUELLM_*` env that pinned binary ignores | grep test; no env writes in `arail.nucleus` | Knobs via ctor kwargs only | T-RT-2 |
| 4 | Teacher/student tokenizer mismatch (e.g. Llama-70B) | `parity()==none` | Preflight refuses; `auto` never selects it; no silent switch to sequence mode | T-PAR-1..4 |
| 5 | Teacher ids beyond student vocab (Qwen3 extras) | `superset` parity | Drop + renormalize; `dropped_mass` recorded | T-KD-2 |
| 6 | Top-N mass too low | `captured_mass` mean < 0.9 | Spike flags; raise N | T-KD-3, spike |
| 7 | Over memory budget in any phase | Preflight plan per phase | Refuse naming largest non-Buddy model + alternative | T-PRE-1,2 |
| 8 | Preflight proposes evicting Buddy | Invariant `drop ∩ protected = ∅` | Structural; assertion in code + test | T-PRE-3 |
| 9 | Buddy not running at plan time → plan too optimistic | `buddy_reserve = max(measured, declared)` | Plan holds when Buddy starts later | T-PRE-4 |
| 10 | Buddy evicted by memory pressure during build | Residency sampler: RSS −5 % or model gone before `expires_at` | Recorded; decision capped at COMPATIBLE; printed | T-RES-1,2 |
| 11 | Ollama keep-alive TTL lapse mistaken for eviction | `expires_at` comparison | Classified `ttl_expired`, not violation | T-RES-3 |
| 12 | Teacher and student resident concurrently (D3) | Phase pid ledger; invariant check | Refuse next phase while prior pid alive | T-D3-1 |
| 13 | Two builds at once | `flock` on `build.lock` | Second exits 3 naming running build; stale lock takeover | T-LOCK-1,2 |
| 14 | Crash mid-Phase-A after hours | `run.json` phase + `index.jsonl` | `--resume` skips done windows after hash re-check | T-RESUME-1 |
| 15 | Any network attempt (HF hub lookup, gateway) in airgapped | Worker env offline + guard; tests assert `egress.jsonl` unchanged and no non-loopback connect | Load local dirs only; refuse before connect | T-EGR-1,2 |
| 16 | Corpus not staged / sources absent | Preflight row; manifest `absent` | Refuse with the `stage` command to run | T-STAGE-2 |
| 17 | Malicious/odd local git repo (hooks, fsmonitor) during stage/checks | Hardened git env | Hooks/fsmonitor disabled; `protocol.allow=never` | T-SEC-GIT-1 |
| 18 | Not enough post-cutoff items for cert | Count check | Refuse with count; suggest earlier cutoff | T-SPLIT-3 |
| 19 | Cert set modified (bitrot, hand-edit, bug) | sha re-verified on every `CertAccess` open + before/after Arbitrage | `CertTampered` → certify refuses | T-CERT-1,2 |
| 20 | Arbitrage reads cert | No cert param; import-lint; access counter | Structural | T-CERT-3 |
| 21 | Contamination ≥ 1 % / temporal leak | `contamination.check` | Certify refuses; report lists offenders | T-CONT-1..4 |
| 22 | Boilerplate n-grams trigger false contamination | Stop-list (>5 % doc freq) | Ignored n-grams recorded in method params | T-CONT-5 |
| 23 | `eval_hash` fails to change when yardstick changes / changes when it shouldn't | Closed-world field classification + mutation tests | Test failure blocks merge | T-HASH-1..3 |
| 24 | Accuracy sneaks into headline | Schema `additionalProperties:false`; test | Card invalid → not signed | T-CLOSED-2 |
| 25 | Judge is the teacher (or student) under another alias | `model_identity` comparison | `JudgeIsTeacher` before any generation | T-JUDGE-1 |
| 26 | Judge position bias / length bias | Randomized order; LC regression | Balanced by construction; LC reported | T-JUDGE-2,3 |
| 27 | Judge unparseable outputs | Invalid rate | > 10 % → `unreliable: true` in card | T-JUDGE-4 |
| 28 | Hostile model patch (path traversal, absolute, symlink, huge, binary) | Size/type caps; `git apply --check --cached` only; no `--unsafe-paths` | Scored as not-applies; nothing written | T-EXEC-2 |
| 29 | Model output executed by `compiles` | `compiles` is `NotRun`; no Makefile execution path exists | Structural | T-EXEC-3 |
| 30 | checkpatch hangs / explodes | Timeout + rlimit | Scored `error`, counted, reported | T-EXEC-4 |
| 31 | `qkz isotope verify` rejects our seal (fields, floats, non-ASCII) | Golden vector test vs Python port of Rust formatter; optional real binary test | 5-field payload, string values, ASCII guard | T-SEAL-1..4 |
| 32 | Seal signed by an arbitrary key passes `qkz` | Trust-anchor check in `nucleus verify` | `key: untrusted` → verify exit 3; ledger refuses | T-SEAL-5 |
| 33 | Card edited after signing | `card_sha256` in signed `gate_results` | verify `card_hash: mismatch` | T-SEAL-6 |
| 34 | YAML turns `built:` into datetime → hash drift | Forced quoting; round-trip test | Canonicalize on reloaded dict | T-CARD-2 |
| 35 | Signing key leaked / wrong perms | Mode check on load (must be 0600, owner) | Refuse to sign; print fix | T-SEAL-7 |
| 36 | Stub numbers masquerade as real | `runtime: stub`, ephemeral key, badge | Never ledgered; verify `key: ephemeral-stub` | T-STUB-2 |
| 37 | Generated weights committable | git-ignore guard | Refuse output path | T-PATH-1,2 |
| 38 | Overwrite an existing shard version | Existence check | Refuse; next patch default | T-PATH-3 |
| 39 | `docs/CERTIFIED_MODELS.md` dirtied / existing rows changed | Default local ledger; markers; byte-diff test | Only `--publish-row`; idempotent upsert | T-LEDGER-1..3 |
| 40 | Markdown/HTML injection via domain/shard text into docs hub or `/forge` | Slug regex; cell escaping; `html=False`; autoescape | Escaped | T-LEDGER-4, T-FORGE-3 |
| 41 | `/forge` path traversal | Regex + realpath under `FORGE_ROOT` | 404 | T-FORGE-2 |
| 42 | `gateway` selected in airgapped | Profile gate before session | `AIRGAPPED_NOTICE`, exit 3, zero egress lines | T-GW-1 |
| 43 | Build token leaks (log, exception, URL, egress reason, repr) | caplog + file scans | Header-only; redaction | T-GW-2 |
| 44 | Gateway redirect → SSRF / off-host | `allow_redirects=False`, exact host | Error | T-GW-3 |
| 45 | Gateway budget exhausted | `402` | `BudgetExhausted`; no fallback teacher | T-GW-4 |
| 46 | Gateway response lacks provenance | Field check | `ContractViolation` | T-GW-5 |
| 47 | Domain YAML path traversal (eyeball file, sources) | realpath under `configs/domains/`; sources are ids not paths | `DomainConfigError` | T-DOM-3 |
| 48 | Legacy superskill manifest passed as domain | `superskill:` key detection | Clear message | T-DOM-2 |
| 49 | Student ≥ 8B | Param estimate from config | Refuse | T-DOM-4 |
| 50 | Minimalist user runs `build` | Tier check | "needs maximus deep runtime" exit 3, no traceback | T-CLI-1 |
| 51 | `mlx_lm` version drift breaks training loop | Version range check in preflight | Refuse with supported range | T-SETUP-3 |
| 52 | `cryptography` missing | Import check in certify/verify | Plain message: install maximus extra | T-SETUP-4 |
| 53 | Retiring `/build` breaks importers (`compiled_kb`, `models_api`, QA6 parity test) | Grep + full suite | Move `world_catalog`; rewire | T-REG-3,4 |
| 54 | `/build` bookmarks | 308 → `/forge` | — | T-FORGE-4 |
| 55 | Disk fills during Phase A (logits) | Preflight disk row; per-batch free-space check | Pause + clear error; resumable | T-PRE-5 |
| 56 | Portal SSE doesn't show CLI build progress | Known limitation | `status` verb + run.json; debt ticket | — (documented) |
| 57 | Teacher greedy output differs with logprob capture on | Spike compares 5 prompts with/without | Block item 10 if different | spike (B0) |

---

## 7. Test strategy

**Weights** (brief §8, adjusted): Buddy-voice 10 % is deferred with item 4. It is reallocated **+5 → security** (the
new code-exec, signing, and egress surfaces are the highest-consequence risks here) and **+5 → happy**, because
metric math correctness (F1, LC judge, composite) lives in golden happy-path tests and is what W2 stands on.

| Bucket | Weight | Contents |
|---|---|---|
| Security | **35 %** | T-GW-1..5, T-SEC-GIT-1, T-EXEC-2..4, T-SEAL-1..7, T-DOM-3, T-FORGE-2,3, T-LEDGER-4, T-EGR-1,2, T-PATH-1,2, T-STUB-2, secrets 0600 (T-SEAL-7), MCP-schema item N/A (deferred with item 3) |
| Regression | **25 %** | T-REG-1..6, T-RT-1,2, T-LEDGER-2, egress guard suite unchanged, Chat Compute Source suite unchanged |
| Happy | **25 %** | T-E2E-1 (stub plan→stage→build→certify), T-HASH-*, T-CLOSED-*, T-JUDGE-2,3, T-KD-*, T-CARD-*, T-REPORT-1, T-SPIKE-1 |
| Setup | **15 %** | T-CLI-1..4, T-SETUP-1..4, T-DOM-1,2,4, T-STAGE-1,2 |

**CI:** new `.github/workflows/nucleus-tests.yml` on ubuntu-latest, Python 3.11, `pip install -e ".[dev]"`, then
`pytest tests/nucleus tests/portal/test_forge_viewer.py tests/portal/test_models_api.py tests/test_qa6_security_gate.py -m "not requires_mlx and not requires_aerollm and not requires_kernel"`.
New markers are registered in `pyproject`/`pytest.ini`: `requires_mlx`, `requires_aerollm`, `requires_kernel`, and
`requires_qkz_bin`. They run locally on the M5 and are reported in TEST_REPORT.

### Unit

- **T-RT-1** `runtime_names` mapping. **T-RT-2** grep: no `aerollm|AERO_|QUEUELLM_` literals and no `os.environ[` writes in `src/arail/nucleus/**` except `runtime_names.py`.
- **T-PROV-1** A5: a fake `aerollm_api` module in `sys.modules`. Spy the `OllamaFallbackProvider`/`AirLLMBackend` constructors; assert they're not called and every role reports `runtime: queuellm`. **T-PROV-2** `sys.modules["aerollm_api"]=None` → fallback for eval roles, refusal for local build. **T-PROV-3** the fake raises the real `unstable-api` `ValueError` → preflight refusal text contains the rebuild instruction.
- **T-PAR-1..4** Tiny `tokenizer.json` fixtures: exact; superset (+extra ids above max); none (different vocab); none (same vocab, different pre_tokenizer).
- **T-KD-1** numpy reference KD loss: CE + T²·KL on hand-computed 3-token example. **T-KD-2** dropped-id renormalization. **T-KD-3** `captured_mass` computation. **T-KD-4** (`requires_mlx`) MLX loss equals reference within 1e-4.
- **T-DOM-1** valid domain loads with defaults. **T-DOM-2** legacy superskill message. **T-DOM-3** eyeball path `../../etc/passwd`, absolute, symlink escaping → error; sources containing `/` → error. **T-DOM-4** student ≥ 8B refused. Also: unknown key rejected; `refresh:` refused; eyeball count ≠ 10 refused.
- **T-PRE-1** A4: synthetic capacity 24 GB + teacher 37 GB, not streamable → refusal names teacher. **T-PRE-2** streamed window path chosen when QueueLLM present. **T-PRE-3** property over 200 randomized capacity/model combos: `drop ∩ protected = ∅` and Buddy is never in `drop`. **T-PRE-4** Buddy not running → declared reserve used. **T-PRE-5** disk row red when logits estimate exceeds free space.
- **T-RES-1..3** classifier: RSS −6 % → violated; model vanished before `expires_at` → violated; after → `ttl_expired`.
- **T-SPLIT-1** temporal split: no cert item ≤ cutoff, no train item > cutoff. **T-SPLIT-2** dev split deterministic across runs and process restarts. **T-SPLIT-3** insufficient cert items → refusal with count.
- **T-CERT-1** A3: cert sha identical before/after a stub Arbitrage run. **T-CERT-2** flip one byte → `CertTampered`, certify refuses. **T-CERT-3** import-lint: `arbitrage.py` does not import `CertStore`/`CertAccess`; access counter stays 0 during Arbitrage.
- **T-CONT-1** overlap 0.0125 (10/800) → refuse. **T-CONT-2** 0.00875 → pass. **T-CONT-3** exact-sha duplicate counts. **T-CONT-4** train item dated after cutoff → `temporal_leak` → refuse. **T-CONT-5** shared license header does not trigger.
- **T-HASH-1** A2 positive: mutating each of the six included fields (and each `scoring` sub-field: judge identity, formula id, rubric, decision rule, checkpatch sha) changes the hash. **T-HASH-2** A2 negative: timestamp, output path, build id, training seed, top-N, student identity, host leave it unchanged. **T-HASH-3** closed world: `fields(EvalHashInputs)` equals the declared set; a new field without a classification fails.
- **T-CLOSED-1** F1/macro-F1 against hand-computed confusion matrices, including an imbalanced 95/5 case where accuracy would be 0.95 and F1 is 0. **T-CLOSED-2** card headline block contains no `accuracy` key anywhere (recursive), and the schema rejects one.
- **T-JUDGE-1** judge identity equals teacher identity via alias → `JudgeIsTeacher`; also judge == student base. **T-JUDGE-2** position randomization ≈ 50/50 over 1000 seeded items and recorded. **T-JUDGE-3** synthetic judge that always prefers the longer answer, with equal-quality pairs → LC win rate within 0.5 ± 0.05 while the raw win rate is ≫ 0.5. **T-JUDGE-4** invalid outputs → `unreliable`. Plus a bootstrap CI determinism check.
- **T-EXEC-1** fixture git repo built at test time: good patch applies; conflicting patch doesn't. **T-EXEC-2** hostile patches: `../` path, absolute path, symlink creation, 1 MiB, binary, NUL bytes → not-applies and the worktree unchanged (hash tree before/after). **T-EXEC-3** `compiles` returns `not_run`, and there's no subprocess call. **T-EXEC-4** fake checkpatch that sleeps → timeout recorded.
- **T-SEAL-1** signed-payload keys == the 5 legacy fields exactly. **T-SEAL-2** golden vector: a Python port of the Rust `PythonJsonFormatter` reproduces the signed bytes and verifies, using the four vectors from F3. **T-SEAL-3** non-ASCII → `SealError`. **T-SEAL-4** float in `gate_results` → `SealError`. **T-SEAL-5** a valid signature with an untrusted key → verify `key: untrusted`, exit 3. **T-SEAL-6** edit one metric in the card → `card_hash: mismatch`. **T-SEAL-7** key file 0644 → refuse to sign; a new key is created 0600. **T-SEAL-8** (`requires_qkz_bin`, `NUCLEUS_QKZ_BIN`) the real Rust binary verifies our `seal.json`.
- **T-CARD-1** golden stub card validates against the schema. **T-CARD-2** YAML dump → load → canonical hash is stable, `built` stays a string. **T-CARD-3** `not_run` metrics are allowed and rendered.
- **T-REPORT-1** report contains exactly 10 eyeball sections with three columns, and the summary comes before the composite.
- **T-LEDGER-1** default append goes to the local ledger; docs untouched. **T-LEDGER-2** `--publish-row`: bytes outside the markers are identical, and the existing rows are untouched. **T-LEDGER-3** re-certify the same version → upsert, no duplicate; stub → refused. **T-LEDGER-4** shard/intent containing `|`, `<script>`, newline → escaped.
- **T-PATH-1** the default `lab/models/forge/...` passes `git check-ignore`. **T-PATH-2** `ARAIL_MODELS_DIR` pointed at a tracked repo dir → refuse. **T-PATH-3** existing version → refuse.
- **T-LOCK-1/2** a concurrent build is refused; a stale-pid lock is taken over.
- **T-STUB-2** `ARAIL_NUCLEUS_STUB` unset → `runtime: stub` rejected; stub card → `verify` says `ephemeral-stub`, the ledger refuses, `/forge` shows the badge.

### Integration

- **T-E2E-1 (Gate A1 + A7 + D3):** in a tmp lab root with `LAB_MODE=airgapped` and `ARAIL_NUCLEUS_STUB=1`, the subprocess CLI runs `plan` → `stage --source git:linux=<fixture repo> --source cve=<fixture>` → `build --profile local` → `certify` on the **50-item synthetic fixture** (§7.1). Assertions:
  - exit 0
  - the card validates, and golden metric values match
  - the report has 10 eyeballs
  - `verify` reports valid + ephemeral-stub
  - `egress.jsonl` is absent or byte-identical
  - the phase intervals don't overlap
  - the cert sha is unchanged
  - the ActivityLog has `nucleus` events
- **T-EGR-1:** the same pipeline run in-process with `socket.socket.connect` patched to raise on non-loopback → no raise. **T-EGR-2:** the worker env contains the `HF_*_OFFLINE` vars.
- **T-GW-1 (A6):** `LAB_MODE=airgapped`, domain `teacher.profile: gateway` → exit 3, stderr contains `AIRGAPPED_NOTICE` verbatim (compared to `airgap.AIRGAPPED_NOTICE`), `egress.jsonl` unchanged, and no socket opened.
- **T-GW-2..5:** a mock gateway (stdlib `http.server` on 127.0.0.1 in a thread) implements the contract: teach/judge happy path with provenance, 402, missing-provenance, a 302 to another host, and a token-echo trap. The token is absent from caplog, exception strings, `egress.jsonl`, and `repr(client)`.
- **T-RESUME-1:** kill the stub build after Phase A batch 2 → `--resume` completes and batches 1–2 are not recomputed (counter).
- **T-SPIKE-1:** the stub `spike` produces `spike-report.json` with B0–B3 fields and pass/fail against thresholds.
- **T-FORGE-1:** `/forge` renders on minimalist and maximus. **T-FORGE-2:** `/forge/..%2f..%2fetc/passwd`, bad semver → 404. **T-FORGE-3:** an eyeball output containing `<script>` renders escaped. **T-FORGE-4:** `/build` → 308 `/forge`; `/api/build/jobs` → 404.
- **T-CLI-1:** `LAB_TIER=minimalist` → `build` exits 3 with the maximus message, and `plan` works. **T-CLI-2:** an unknown verb → exit 2 with usage. **T-CLI-3:** an internal error without `--debug` → no `Traceback` in stderr. **T-CLI-4:** `publish` → sprint-3 message, exit 3.
- **T-SETUP-1:** a fresh-clone default layout (`lab/models` absent) → `plan` creates nothing outside `lab/`. **T-SETUP-2:** missing student base → the message includes the `hf download` command and "needs network". **T-SETUP-3:** the fake `mlx_lm.__version__="0.40.0"` → refusal with range. **T-SETUP-4:** `cryptography` import blocked → plain message.
- **T-STAGE-1:** staging the fixture repo produces a manifest with licenses and origin commit, and a second stage of the same input yields an identical snapshot sha. **T-STAGE-2:** `build` without staging → refusal naming the `stage` command. **T-SEC-GIT-1:** a fixture repo with a `post-checkout` hook and `core.fsmonitor=touch /tmp/pwned` config → stage + checks never create the marker.

### Regression

- **T-REG-1:** Chat Compute Source suites (`tests/test_aerollm_compute_source.py`, `tests/router/test_router_airgap_gate.py`, airgap helper and toggle suites) pass unchanged.
- **T-REG-2:** the egress suite passes unchanged, and `AIRGAPPED_NOTICE` is byte-identical to the previous Chat string.
- **T-REG-3:** `compiled_kb` works after the `world_catalog` move (`tests/test_compiled_kb*.py`), and the QA6 slug-parity test now imports `arail.world_catalog._safe_term_slug`.
- **T-REG-4:** `models_api` `register-artifact` requires `gguf_path` (422 without it) and no longer imports `arail.build`.
- **T-REG-5:** `_TIER_SURFACES` contains `forge` in both tiers and not `build`, and `test_tier_gating`-style tests are updated.
- **T-REG-6:** `docs/CERTIFIED_MODELS.md` rows above the markers are byte-identical to `main`.
- Preflight estimator numbers ported from `tests/build/test_preflight_estimator.py` produce the same values from `nucleus.preflight` (salvage fidelity).

### Performance (M5, local, reported in TEST_REPORT; not CI)

- Preflight wall time < 5 s, since it uses cheap identities and no full weight hashing.
- Contamination check on 1 M train lines × 800 cert items: < 60 s, RSS < 1 GB.
- **Gate B thresholds** (from VISION):
  - **B0:** the logprobs probe passes on the unstable-api build, and greedy text is identical with and without capture on 5 prompts.
  - **B1:** throughput × reduced corpus ≤ 4 h of extraction.
  - **B2:** teacher composite ≥ 0.6 on a 50-item cert sample.
  - **B3:** Buddy residency is `ok` (±5 %).
  - Also report `captured_mass` (want ≥ 0.9).

### 7.1 Fixture corpus (CI)

`tests/fixtures/nucleus/linux-kernel-mini/` is **fully synthetic**, with no real kernel text (GPL-2.0 must not enter
this MIT repo's fixtures). It contains:

- A `make_fixture_repo.py` that builds a tiny git repo at test time (a fake `MAINTAINERS`, 6 subsystems, 50 commits
  spanning a fake cutoff, 20 of them after it).
- A fake `vulns` mapping (8 CVE commits, so the class imbalance exercises F1).
- `fake_checkpatch.py`, standing in for `checkpatch.pl`.
- Per-role canned answers and judge preferences chosen so the golden metrics are exact rationals.
- `linux-kernel-mini.yaml` + `.eyeball.txt` (10 prompts).

Split: cert_n = 20, dev 10 %, train ≈ 27.

### 7.2 `tests/nucleus` runs in its own CI invocation, never merged into a whole-repo `pytest tests` run

**Decided 2026-09-24** (SPRINT.md decisions log; TEST_REPORT.md finding F3; BUILD_LOG.md "Review loop 4"),
**fallback (a)** taken after a time-boxed root-cause attempt did not land a fix inside `tests/nucleus`'s own
conftest/tests.

**What's true today.** `.github/workflows/nucleus-tests.yml` already only ever runs
`tests/nucleus tests/portal/test_forge_viewer.py tests/portal/test_models_api.py tests/test_qa6_security_gate.py`
in one job — it has never mixed `tests/nucleus` into a single `pytest tests` invocation with the whole repo's
~350 other root test files. **There is no existing CI job anywhere in `.github/workflows/` that runs the full
`pytest tests -q` suite as one invocation**; only QA and local developers do that by hand. So this fallback
formalizes and future-proofs an isolation that already holds in CI, rather than fixing a currently-broken gate.

**The rule going forward:** `tests/nucleus` MUST always be its own pytest invocation (its own CI job, or its own
`pytest tests/nucleus ...` command locally) and MUST NOT be concatenated into one `pytest` process with the
~350-file root `tests/test_*.py` prefix. Running `pytest tests -q` (the whole repo, one process) is a **local
diagnostic tool only** — its result is not a merge gate, and 17–18 order-dependent failures in Chat/deep-runtime/
activity tests are a **known, filed** interaction (see `sprints/BACKLOG.md`, "`tests/nucleus` combined with the
full root test suite in one pytest process is order-dependent"), not a regression to chase on every PR.

**Why this is a fallback, not a fix.** A one-session root-cause attempt (BUILD_LOG.md "Review loop 4 (F3)")
traced the failure to a real, reproducible exception (`ModelRouter` construction failing inside
`arail.portal.app._get_primary_router`, with `MODEL_NAME` resolving to a stray `ai-engineer:latest` and the
`mlx` backend then trying to treat it as a HuggingFace repo id) and found the mechanism is **not** inside
`tests/nucleus` or its conftest: `arail.nucleus` never imports `arail.portal`/`arail.registry`/`arail.router`
and never touches `MODEL_NAME`/`AEROLLM_MODEL`, and an isolated leak-detection run confirmed `tests/nucleus`
never leaves any of those variables set between its own tests, or into the tests immediately following it.
The two confirmed non-monkeypatch, non-nucleus writers of `MODEL_NAME`/`AEROLLM_MODEL`
(`arail.model_defaults.apply()` and `arail.portal.app._export_registry_env()`, both intentionally bare
`os.environ[...] =` writes that bypass `monkeypatch`, per those modules' own docstrings) plus the process-lifetime
`arail.registry` singleton are the likely real culprits, combined with the wall-clock/thread/subprocess load of
`tests/nucleus`'s ~40 real-subprocess tests shifting scheduling enough to expose the pre-existing race — this
matches TEST_REPORT.md F3's own "resource-class interaction, not a state leak `tests/nucleus` owns" finding.
Fixing that root cause means changing `arail.portal.app`/`arail.registry` test-isolation hygiene, which is
outside this sprint's file list and risks exactly the kind of scope drift the builder protocol forbids — filed
instead as its own BACKLOG ticket carrying this evidence.

---

## 8. Salvage and retire plan (`/build`)

| Current | Fate |
|---|---|
| `build/preflight.py` | **Salvage into `nucleus/preflight.py`:** `_capacity`, `active_params_b`, `_status`, `Requirement`, `PreflightReport`, LoRA memory estimate. Drop Anthropic pricing, teacher amplification, remote rows. Port the relevant cases of `tests/build/test_preflight_estimator.py` into `tests/nucleus/test_preflight_estimator_port.py`, then delete the original. |
| `build/world_corpus.py` | **Salvage into `src/arail/world_catalog.py`:** `_safe_term_slug`, `resolve_world_bundle`, `all_categories`, `category_breakdown`, `pull_approved_terms`, `CRAFT_CATEGORIES`. These are deterministic approved-term pulls, reused by `compiled_kb` today and by a future `world:` corpus source (the id kind is reserved in the domain schema; the adapter is deferred). **Delete:** `_infer_layer`, `term_to_kice_example`, `chunk`, `tag_source`, `build_world_corpus`, `_synthesize_all` (KICE and docker-Nucleus bound). Keep the pull tests from `tests/build/test_world_corpus.py` (moved to `tests/test_world_catalog.py`); delete the synth tests. |
| Nucleus certifier seal format | **Salvage as a format, not code:** re-implemented in `nucleus/cards/seal.py` (legacy-5 payload, the same `chain_hash` join, the same raw-key file format). Nucleus Python is not imported (no cross-repo runtime import). |
| `build/nucleus_client.py`, `build/manifest.py`, `build/jobs.py`, `build/__init__.py` | **Delete.** `manifest.py` is the unversioned sibling-repo write (F11). |
| `portal/build_api.py`, `templates/build.html`, `/build` route, nav link, `"build"` in `_TIER_SURFACES` | **Delete.** `/build` → 308 `/forge`. |
| `portal/models_api.py` `register-artifact` NucleusClient fallback | **Remove the fallback.** `gguf_path` becomes required (422 otherwise). The endpoint and its test otherwise stay. |
| `tests/build/*`, `tests/portal/test_build_tab.py` | Delete (after the ports above). |
| `docs/models-on-disk.md` rows/§ on `/build` | Rewrite for Model Forge paths. |
| `sprints/2026-07-22-distill-now/SPRINT.md` | Add a "Superseded by 2026-09-23-nucleus-sprint-1" banner (ledger decision). |
| `lab/data/build_jobs.json` on user machines | Left in place, never read. Mentioned in `docs/nucleus.md`. |

Repo `CLAUDE.md` still lists "Model Building (`/build`)" among the maximus surfaces. Updating that line is a doc edit
the builder should make **only with the operator's OK** (Q9).

---

## 9. Tech debt

**Added**

1. The logit path depends on a **non-bundled `unstable-api` build** of `aerollm-api`, so the default install cannot
   run `nucleus build`. Ticket: re-pin the bundle with logprobs once QueueLLM promotes R.2 to stable, or ship a
   second "forge" bundle.
2. **Legacy-5 seal payload:** it carries no `schema_version` and is not JCS. Ticket (qukaizen-nucleus): the Python
   signer (10 fields) and the Rust verifier (5 fields) disagree today (F3), so move both to seal v2/JCS and then
   re-point ARAIL.
3. **The Nucleus worker constructs `aerollm_api.Runtime` directly**, duplicating a slice of `AeroLLMBackend` init
   (thread pinning, KV budget). Ticket: extract a `queuellm_runtime_factory(model_path, **kw)` shared by both.
4. The **stub provider ships in the package** (gated by env). This is accepted because A1 requires the CLI to run
   against it.
5. **CLI-process ActivityLog events are not live in the portal SSE.** Ticket: an activity file-tail bridge, which
   the Buddy P1 observability work wants anyway.
6. `compiles`, `hallucination_rate`, `build_energy_est`, and GGUF export are all `not_run`/`not_built`, with one
   ticket each.
7. `composite/v1-nc` and `composite/v1-open` exist alongside `composite/v1`. Three formulas mean cards are only
   comparable within a formula id, and the `/forge` viewer must show the id. `v1-open` carries a Gate B cap
   (COMPATIBLE at most) and is retired once patch generation exists (§4.9).
8. The `refresh:<days>` cert set, the `world:`, `lkml`, and `lwn` source adapters, and the `mixed` runtime path are
   seams only.
9. **The orchestration layer (`build.py`/`certify.py`) doesn't wire several real-mode paths into the pipeline yet:**
   teacher auto-select, tokenizer parity, the logprob probe, the residency sampler, the LC judge and executable
   checks in the generic (non-fixture) certify path, `pipeline_hash`/`training_hash`/`teacher_hash`, and runtime
   provenance. Found during review-loop-1 (2026-09-23); filed as a single umbrella ticket, "Model Forge real-runtime
   wiring", in `sprints/BACKLOG.md` (expands the existing MLX-training-cycle entry). Required before Gate B / item
   10, not before Gate A.
10. **Found in review round 3 (2026-09-23), not anticipated at design time:**
    - The LC estimator (`open_lc_judge.score`) degenerates. With a zero-variance Δlen (all equal lengths or a
      constant delta), the 2×2 Newton-Raphson Hessian is singular and `lc_win_rate` returns 0.5 whatever the
      outcomes. The correct fallback is the intercept-only fit, which equals the raw win rate. Under
      quasi-separation at n = 10 it returns 0 or 1. The stub fixture sidesteps this with per-item length padding.
      Required before Gate B.
    - Preflight's protected Buddy model is read from `QUEUELLM_MODEL` first, but arail's `AeroLLMBackend` loads
      only `AEROLLM_MODEL` (or its built-in default when unset). The protected set can therefore miss the model
      Buddy actually runs.
    - `run_certify` has grown to 428 lines across three review loops. Extract `_eval_hash_inputs`,
      `_assemble_card`, `_provenance_hashes`.

    Ticketed in the BACKLOG umbrella per REVIEW.md round 3 (R3-A3, R3-A4, R3-A10).

**Repaid**

1. The docker-Nucleus client and the hardcoded sibling-repo write (`manifest.py`, F11) are deleted, which removes an
   unversioned cross-repo edge.
2. There are no longer two model-building surfaces: `/build` is retired and distill-now is closed.
3. The airgapped banner becomes one constant (`airgap.AIRGAPPED_NOTICE`) instead of five near-duplicates starting
   with Chat's.
4. World bundle reading moves out of a build-specific module into a neutral `world_catalog` that `compiled_kb`
   already needed.
5. `jsonschema`/`numpy` become declared dependencies instead of transitive accidents.

**Net: positive (significant), revised 2026-09-23 after review loop 1.** Items 1–3 of Added need tickets in
`sprints/BACKLOG.md` before the review PASS (the builder files them in the final commit). Item 2 is also filed in
qukaizen-nucleus. Item 9 was not anticipated at design time; see REVIEW.md's "Tech debt delta" for the reviewer's
accounting.

---

## 10. Recommended implementation order (atomic commits)

Each commit leaves the suite green. `[CI]` means covered by `nucleus-tests.yml` from that point on.

1. **chore(deps,ci):** declare `numpy`, `jsonschema` (core) and `cryptography` (maximus, dev); add pytest markers; add a `.github/workflows/nucleus-tests.yml` skeleton running `tests/nucleus` (empty is ok). `[CI]`
2. **refactor(airgap):** `AIRGAPPED_NOTICE` constant in `airgap.py`; Chat status endpoint uses it. T-REG-2.
3. **refactor(world):** move the approved-term pull to `src/arail/world_catalog.py`; update `compiled_kb` and the QA6 parity import; `build/world_corpus.py` re-exports temporarily. T-REG-3.
4. **feat(nucleus): skeleton + CLI dispatch.** Package, `runtime_names.py`, `cli.py` with all verbs (stubs return "not implemented yet" exit 1), `arailctl nucleus)` case, exit-code and no-traceback handling, tier gate. T-RT-1/2, T-CLI-1..4.
5. **feat(nucleus): domain config.** `spec/nucleus-domain-v1.schema.json`, `domain.py`, `configs/domains/linux-kernel.yaml` + `.eyeball.txt`, `plan` (template write). T-DOM-*.
6. **feat(nucleus): paths, lock, output guard.** T-PATH-*, T-LOCK-*.
7. **feat(nucleus): model resolution, identity, tokenizer parity, teacher auto-select.** T-PAR-*, T-SETUP-2.
8. **feat(nucleus): preflight + residency.** Salvaged estimator port + memory plan + Buddy reserve + refusal contract + capability rows + sampler/classifier. T-PRE-*, T-RES-*, T-SETUP-3, preflight port tests.
9. **feat(nucleus): providers.** `base`, `stub`, `queuellm` (worker-side, capability probe), `fallback_ollama`, selection. T-PROV-*, T-STUB-2 (partial).
10. **feat(nucleus): corpus staging + synthetic fixture.** `stage`, git-kernel and cve-vulns adapters, hardened git runner, manifest + licenses, `tests/fixtures/nucleus/linux-kernel-mini/`. T-STAGE-*, T-SEC-GIT-1.
11. **feat(nucleus): splits + CertStore.** T-SPLIT-*, T-CERT-1,2.
12. **feat(nucleus): contamination.** T-CONT-*.
13. **feat(nucleus): closed metrics + composite + decision rule.** T-CLOSED-*.
14. **feat(nucleus): LC pairwise judge.** T-JUDGE-*.
15. **feat(nucleus): executable kernel checks.** T-EXEC-*.
16. **feat(nucleus): eval_hash + pipeline_hash + eval-config.lock.** T-HASH-*.
17. **feat(nucleus): KD loss reference + MLX trainer + fuse.** Covers `train/kd_loss.py` and `train/mlx_kd.py` (`requires_mlx`). T-KD-*.
18. **feat(nucleus): phase runner, worker, Arbitrage, `build` + `eval` + `status` + `--resume`.** Worker offline env + egress guard. T-D3-1, T-CERT-3, T-RESUME-1, T-EGR-2.
19. **feat(nucleus): DNA card v2 schema + generator.** T-CARD-*.
20. **feat(nucleus): seal (legacy-5), key custody, trust anchors, `verify`.** T-SEAL-1..8.
21. **feat(nucleus): build report + certified-models ledger + `certify`.** T-REPORT-1, T-LEDGER-*.
22. **test(nucleus): Gate A end-to-end.** T-E2E-1, T-EGR-1.
23. **feat(nucleus): gateway contract doc + client + mixed config + mock-server tests.** T-GW-*.
24. **feat(nucleus): `spike` verb (Gate B harness).** T-SPIKE-1.
25. **feat(portal): `/forge` read-only viewer.** T-FORGE-1..3.
26. **refactor!: retire `/build`.** Delete the files in §8, `/build` redirect, `models_api` rewire, remove the `world_corpus` shim, update `docs/models-on-disk.md`, distill-now banner. T-FORGE-4, T-REG-4,5.
27. **docs:** `docs/nucleus.md` (user tour: stage → plan → build → certify → verify; airgapped prerequisites) and `docs/nucleus-architecture.md` (contracts from this doc). File the §9 tickets in `sprints/BACKLOG.md`.

**Gate A (ledger checkpoint):** CI green on the branch, plus a local run of the `requires_*` markers on the M5,
recorded in BUILD_LOG. Then architect review, then QA.

28. **(Only if Q1 = yes) chore(deep):** `ARAIL_AEROLLM_FEATURES=unstable-api ./arailctl deep rebuild`. Add opt-in feature passthrough in `scripts/build-aerollm.sh`; the default build is unchanged. Test: the default invocation's cargo args are unchanged.

**Gate B** (operator, M5, ≤ half a day): preconditions are §11 Q7. Then run `nucleus spike linux-kernel`. B0–B3 pass
→ record in the ledger → **item 10**, with `LAB_MODE=airgapped ./arailctl nucleus build linux-kernel --profile local`,
then `certify --publish-row`, then `verify`. W3 is recorded in the ledger before the composite is read.

---

## 11. Needs the operator

**Q1 is blocking for Gate B.** The rest can be answered by the time Gate A lands.

1. **Q1 — logprobs runtime (F1/F2).** Pick one:
   - **(a) Recommended:** a maintainer-local `aerollm-api` build with `--features unstable-api` via a new opt-in
     env var (commit 28). The card honestly records `source: local-build, features: [unstable-api]`.
   - **(b)** Re-pin ARAIL's bundle with `unstable-api`. That is an outward-facing QueueLLM stability decision.
   - **(c)** Change D5 so `local` uses sequence-level KD.

   Please also confirm that "logit mode" = top-N KD on **teacher-generated** sequences (F2) is acceptable. Teacher-forced
   corpus logits do not exist in QueueLLM.
2. **Q2 — which key signs item 10's card.** Either a fresh ARAIL-local key (`lab/data/nucleus/keys/`, the default)
   or the existing Nucleus lineage key (`NUCLEUS_SIGNING_KEY_PATH=<nucleus>/data/keys/nucleus-signing.key`). Also,
   ack two things: `qkz isotope verify` only checks self-consistency (F4), and it needs the full path to the Rust
   binary, because `qkz` on PATH is `arailctl` (F5). Separately, Nucleus's own current seals fail its own verifier
   (F3); that needs a ticket in qukaizen-nucleus.
3. **Q3 — 24 GB floor.** The default plans against actual RAM (36 GB). Should Gate B also pass with
   `--memory-budget-gb 24`?
4. **Q4 — composite without `compiles`.** Approve `composite/v1-nc` (0.3 weight on the mean of patch_applies and
   checkpatch_clean) and the `decision_rule/v1` bands: COMPATIBLE ≥ target − 0.05, BETA below that but beats base,
   KNOWN_ISSUE if it doesn't beat base, and residency violation caps at COMPATIBLE.
5. **Q5 — `CERTIFIED_MODELS.md`.** The default is a local ledger. `certify --publish-row` writes a marked section of
   `docs/CERTIFIED_MODELS.md`. Confirm this satisfies the acceptance criterion.
6. **Q6 — output location (informational).** Shards go to `$ARAIL_MODELS_DIR/forge/…` rather than repo `models/`,
   and GGUF is not built this sprint.
7. **Q7 — item-10 preconditions (networked, before the airgapped run).**
   - Clone `torvalds/linux` (or a shallow slice) and the kernel `vulns` repo.
   - `hf download mlx-community/Qwen2.5-3B-Instruct-4bit` into `/Users/Shared/models`.
   - Set `LAB_MODE=airgapped` (the `.env` currently says `hybrid`, F7).
   - Stop other GPU-heavy work (e.g. the Buddy P2 bake-off) for the run window.
8. **Q8 — teacher (informational).** The dense Llama-3.1-70B has no tokenizer parity with a Qwen2.5 student, so it
   cannot be a logit teacher. `auto` picks the MoE Qwen3-30B-A3B. This answers VISION's "accept an MoE teacher?"
   with evidence.
9. **Q9 — may the builder edit repo `CLAUDE.md`?** Its surfaces paragraph still describes `/build`.
