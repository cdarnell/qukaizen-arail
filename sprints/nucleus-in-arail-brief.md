# Sprint Brief — Nucleus in ARAIL (`nucleus-sprint-1`)

**Repo:** `cdarnell/qukaizen-arail` · local checkout `/Users/netsushi/ProJects/arail`
**Owner:** Charlie · **Build partner:** Buddy (via `arail-ops` MCP) · **Executor:** Claude Code (`/sprint`)
**Profile proven this sprint:** `local` (QueueLLM teacher) · **Deferred:** `gateway`, `mixed`

---

## 0. One paragraph

Project Nucleus stops being a standalone pipeline and becomes a first-class ARAIL surface: **Model Forge**. A user names a domain, Buddy interviews them for intent, a `domain.yaml` lands in `configs/domains/`, and `./arailctl nucleus build` runs the distillation loop to a fidelity target using either the machine's own QueueLLM teacher (`local`) or the QuKaiZen gateway holding Anthropic credentials (`gateway`). Every build emits a **DNA card v2** and build report that follow the CS224N benchmarking practices (split dev/cert evals, contamination guard, eval hash, baselines, executable checks, eyeball prompts). This sprint proves `local` end-to-end on the M5 and reworks the gateway *contract* — not the gateway itself.

---

## 1. Decisions (locked for this sprint)

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | Nucleus code lives at `src/arail/nucleus/` as an importable package; no separate repo | "ARAIL picks up this development integration." No public Nucleus repo exists; vendoring removes a cross-repo dependency for solo maintenance. Pipeline stays importable standalone (`python -m arail.nucleus`). |
| D2 | Two user-facing provider profiles: `local`, `gateway` (plus `mixed` = gateway teacher, local judge) | The existing six-backend abstraction becomes implementation detail behind two choices. Mirrors the Chat tab's Compute Source pivot so users already know the mental model. |
| D3 | Teacher and student never run concurrently | Already a Nucleus principle ("teacher is a one-time factory"). Also the only way the M5's 24 GB holds Buddy's resident SLM + a streamed teacher **or** an MLX training run. |
| D4 | Buddy's 8–14B SLM is never the teacher, never the judge, never evicted | Locked in `buddy-embodied`. Nucleus preflight enforces it. |
| D5 | `local` profile distills **logit-level** (soft labels via QueueLLM); `gateway` profile distills **sequence-level** (SCoTD rationales) | Anthropic's API returns no logprobs. The DNA card records `distillation.mode` so the two are never compared as if equivalent. |
| D6 | Certification eval set is frozen, quarantined, and never visible to Arbitrage | Prevents Goodharting the fidelity score (CS224N item 1). |
| D7 | Gateway work this sprint = contract + client stub + mock server; the live gateway rework is `nucleus-sprint-2` | Keeps sprint 1 airgapped-provable. |
| D8 | **QueueLLM is the default local runtime** for teacher extraction, judge inference, and shard inference. MLX is used only for the training step. Ollama / AirLLM are fallbacks selected only when the QueueLLM deep runtime is absent, and the DNA card records which runtime produced every number. | One runtime to certify against; QueueLLM is the product. Layer-streaming is also what makes a 70B teacher fit beside Buddy on 24 GB. |

Open question for Charlie (does not block sprint 1): does the private Nucleus code get moved wholesale into `src/arail/nucleus/`, or re-implemented against this brief with the old code as reference? Recommend the latter — the old code predates QueueLLM's public release and the ARAIL 2.0 DaC/HCL architecture.

---

## 2. Where it plugs into ARAIL

| ARAIL surface | Nucleus use |
|---|---|
| **Compute Source** (Chat) | Reused as **Teacher Source**: *My Machine* → QueueLLM; *QuKaiZen Gateway* → new radio, hybrid-mode only. Same `Manage providers` modal stores the gateway build token in `lab/data/secrets.env` (0600). |
| **`LAB_MODE`** | `airgapped` ⇒ only `local` is selectable; `gateway` radio greyed with the standard banner. `hybrid` ⇒ gateway URL must resolve through the existing egress guard; add `NUCLEUS_GATEWAY_URL` to the allow-list logic, logged to `lab/data/egress.jsonl` like any other call. |
| **Agents → Buddy** | New Buddy skill `nucleus-partner`: interviews for domain intent (Phases 0–2), writes `domain.yaml`, launches build, watches Grafana, narrates the DNA card. Steerable mid-run exactly as in the embodied demo. |
| **`arail-ops` MCP** | Five new tools: `nucleus.plan`, `nucleus.build`, `nucleus.eval`, `nucleus.certify`, `nucleus.publish`. GBNF-constrained schemas per the embodied-Buddy decision. |
| **Tuning page** (maximus) | Nucleus builds appear as tuning runs; the git-branch-per-experiment loop is *not* used — Nucleus writes to `models/`, never the source tree. |
| **`configs/domains/`** | Home of `domain.yaml` files (already exists). |
| **`models/`** | Output: `models/<shard-slug>/<version>/` with weights (MLX + GGUF), `dna-card.yaml`, `build-report.md`, `eval-config.lock`. |
| **`docs/CERTIFIED_MODELS.md`** | `nucleus.certify` appends the shard row automatically (Certified / Compatible / Beta / Known Issue). |
| **Dashboard** | Build progress + fidelity trend in the existing mission/activity stream (Prometheus already scraped). |
| **Docs Hub** | `docs/nucleus.md` (user tour) + `docs/nucleus-architecture.md` (contract). Featured card after `the-lab`. |
| **Admin** | Preflight report: memory plan, residency check, contamination check, tokenizer parity. |

New route: `/forge` (Model Forge). Tier: **maximus** for build/eval (needs the QueueLLM deep runtime); **minimalist** can run `nucleus.plan` and view cards.

---

## 3. The user experience ("accessible, easy, powerful")

**Easy path — one command, one file.**

```bash
cd /Users/netsushi/ProJects/arail
./arailctl nucleus plan "Linux kernel maintainer assistant"   # Buddy interview → configs/domains/linux-kernel.yaml
./arailctl nucleus build linux-kernel --profile local           # runs to fidelity target, emits DNA card
./arailctl nucleus certify linux-kernel                          # scores frozen cert set, signs card
```

Or the same three steps from `/forge` with Buddy driving.

**`domain.yaml` (everything else has defaults):**

```yaml
name: linux-kernel
intent: "Answer maintainer-level questions about kernel subsystems, patches, and CVEs."
corpus:
  sources: [git:linux, lkml, lwn, cve]
  cutoff: 2026-06-30            # temporal split boundary — cert set is post-cutoff only
runtime: queuellm               # default for all local inference; ollama | airllm only as fallback
teacher:
  profile: local                # local | gateway | mixed
  model: auto                   # local: largest QueueLLM-streamable model on disk
student:
  base: qwen2.5-3b-instruct     # must be < 8B (ARAIL 2.0 ceiling)
  method: lora                  # lora | full
fidelity:
  target: 0.85                  # Arbitrage stops here — absolute, not relative
  metric: cert.composite        # see DNA card v2 §5
eval:
  dev_fraction: 0.10
  cert_set: frozen              # frozen | refresh:<days>
  eyeball_prompts: configs/domains/linux-kernel.eyeball.txt
```

**Power path** — every knob above is overridable; `mixed` profile; `refresh:` for dynamic cert sets; custom judge; custom executable evals (see §5.4).

---

## 4. Provider profiles

### 4.1 `local` (this sprint)

- **Teacher:** QueueLLM, layer-streamed from SSD. Candidate: Llama-3.1-70B or Qwen2.5-72B in the `models/` catalog. Extraction "ants" read logits per bounded window → soft labels → LanceDB.
- **Judge:** a second local model ≠ teacher (default: the 7B `ai-engineer` already in the maximus catalog), served by QueueLLM, length-controlled pairwise.
- **Student:** MLX LoRA on a ≤3B base for the training step; the finished shard is served by QueueLLM for eval, eyeball prompts, and the `efficiency.inference` numbers.
- **Memory plan (24 GB M5), enforced by preflight:**
  - Phase A *extract*: Buddy SLM (≈6–9 GB @4-bit) + QueueLLM window (≤6 GB) + LanceDB writer. Student not loaded.
  - Phase B *train*: Buddy SLM + MLX LoRA on 3B (≈7 GB). Teacher unloaded.
  - Phase C *eval*: Buddy SLM + student + judge (judge loaded on demand, unloaded after).
  - If any phase exceeds budget, preflight refuses with the exact model to drop — never evicts Buddy.
- **Network:** zero egress. Works in `airgapped`.

### 4.2 `gateway` (contract now, rework next sprint)

The QuKaiZen gateway exists and needs rework. This sprint fixes the **contract** it must satisfy; sprint 2 makes the live gateway conform.

**Contract (`docs/nucleus-gateway-contract.md`):**

| Aspect | Requirement |
|---|---|
| Endpoint | `POST /v1/nucleus/teach` (rationale generation) and `POST /v1/nucleus/judge` (pairwise, length-controlled). Anthropic key held server-side only. |
| Auth | Per-build **build token**, scoped to one `domain` + `build_id`, with a token budget and expiry. Issued by `qkz gateway token --domain linux-kernel --budget 5M`. |
| Provenance | Every response carries `build_id`, `pipeline_hash`, upstream `model` and `request_id`. Gateway log is the audit trail for the DNA card's `teacher.provenance`. |
| Distillation mode | `sequence` only (SCoTD chains). No logprobs — documented, not worked around. |
| Failure | Budget exhaustion → clean `402`; Arbitrage checkpoints and pauses, never silently degrades to a weaker teacher. |
| Egress | Single allow-listed host in `hybrid`; blocked in `airgapped`. |
| Client | `src/arail/nucleus/providers/gateway.py` against the contract, tested with a mock server in `tests/`. |

### 4.3 `mixed`

Gateway teacher + local judge. Recommended production default once the gateway is reworked: frontier-quality distillation, certification independent of the teacher. Config only — no new code beyond `local` + `gateway`.

---

## 5. DNA card v2 + build report (the output contract)

`models/<slug>/<version>/dna-card.yaml` — machine-readable, signed. `build-report.md` — the human narrative Buddy reads aloud. Both generated by `nucleus.certify`.

### 5.1 Identity & provenance
```yaml
shard: qkz-linux-kernel
version: 0.1.0
built: 2026-10-03T14:22:11Z
pipeline_hash: sha256:…          # code + domain.yaml + corpus manifest
eval_hash: sha256:…              # harness version + prompts + few-shot + scoring + decoding + cert-set version
signed: ed25519:…                # QuKaiZen signature
distillation:
  mode: logit                    # logit | sequence
  teacher: {profile: local, model: llama-3.1-70b, tokenizer: llama3, provenance: queuellm@1.x}
  student: {base: qwen2.5-3b-instruct, tokenizer: qwen2, method: lora}
  tokenizer_parity: false        # if false, PPL is reported as bits-per-byte only
```

### 5.2 Splits & contamination
```yaml
splits:
  corpus_cutoff: 2026-06-30
  dev:  {n: 1200, seen_by_arbitrage: true}
  cert: {n: 800,  seen_by_arbitrage: false, frozen: true, version: cert-v1}
contamination:
  method: 13-gram + sha256 exact
  overlap_train_vs_cert: 0.0021   # must be < 0.01 to certify
  temporal_leak: none
```

### 5.3 Metrics — reported separately, never averaged silently
```yaml
inner_loop:                       # dev signal only — not a headline
  val_loss: 1.83
  teacher_student_kl: 0.41        # logit mode only
  bits_per_byte: 0.92             # tokenizer-independent
closed_ended:                     # F1, not accuracy — classes are imbalanced
  cve_detection:      {precision: 0.81, recall: 0.74, f1: 0.77, n: 300}
  subsystem_routing:  {macro_f1: 0.69, n: 400}
open_ended:                       # length-controlled, position-randomized, rubric in eval_hash
  patch_explanation:  {lc_win_rate_vs_base: 0.71, judge: ai-engineer-7b, n: 100, ci95: [0.62, 0.79]}
executable:                       # the numbers kernel people trust
  patch_applies:      {rate: 0.88, n: 50}
  compiles:           {rate: 0.62, n: 50}
  checkpatch_clean:   {rate: 0.54, n: 50}
hallucination_rate: 0.06          # unsupported-claim rate on cert Q&A, judge-scored
irreducible_floor: 0.11           # teacher's own error on cert set — the student cannot beat this
```

### 5.4 Baselines (a number without a baseline is noise)
```yaml
baselines:
  teacher:        {cert.composite: 0.89, executable.compiles: 0.71}
  base_student:   {cert.composite: 0.41, executable.compiles: 0.23}
  previous_shard: null             # first build
```

### 5.5 Composite & decision
```yaml
composite:
  formula: "0.4*closed.mean_f1 + 0.3*open.lc_win_rate + 0.3*executable.compiles"   # published, versioned
  value: 0.74
fidelity:
  target: 0.85
  achieved: 0.74
  decision: BETA                   # CERTIFIED | COMPATIBLE | BETA | KNOWN_ISSUE
  arbitrage_stop_reason: fidelity_plateau_3_cycles
```

### 5.6 Efficiency (MLPerf-style — quality is not the only axis)
```yaml
efficiency:
  time_to_fidelity: 6h41m
  build_energy_est: 0.9 kWh
  inference: {tok_s_m5: 48, rss_gb: 2.1, ttft_ms: 210}
```

### 5.7 Eyeball set
`build-report.md` ends with the same 10 fixed prompts from `linux-kernel.eyeball.txt` and the student's verbatim outputs, side by side with the previous version's. Dubois's closing point: Alpaca scored poorly on benchmarks and was good in practice — you only catch that by reading outputs.

---

## 6. Scope

### In (sprint 1)
1. `src/arail/nucleus/` package skeleton: `plan`, `build`, `eval`, `certify`, `publish` + `providers/{queuellm,fallback_ollama,gateway,mixed}.py`.
2. `arailctl nucleus <verb>` subcommands; `qkz` alias inherits.
3. `arail-ops` MCP: five `nucleus.*` tools with GBNF schemas.
4. Buddy `nucleus-partner` skill (interview → `domain.yaml`; run narration).
5. `/forge` route: plan form, build progress, DNA card viewer.
6. Preflight: memory plan, residency guard, tokenizer parity, contamination check.
7. Eval harness: dev/cert split, temporal split, F1 for closed tasks, LC pairwise judge, three executable kernel checks, eval hash.
8. DNA card v2 + build report generators; `CERTIFIED_MODELS.md` appender.
9. Gateway contract doc + client stub + mock server tests.
10. One real end-to-end `local` build of `qkz-linux-kernel` on the M5 with a **reduced corpus slice** (target: complete in < 8 h wall clock).

### Out
- Live gateway rework (sprint 2).
- `qkz pull` registry publication (sprint 3).
- CUDA path (QueueLLM CUDA backend not built).
- Human-eval tooling beyond the eyeball set.

---

## 7. Acceptance criteria

- [ ] `./arailctl nucleus build linux-kernel --profile local` completes on the M5 with `LAB_MODE=airgapped` and zero lines added to `lab/data/egress.jsonl`.
- [ ] Buddy's SLM RSS is unchanged (±5 %) across all three build phases; preflight refuses a config that would exceed budget and names the offending model.
- [ ] `dna-card.yaml` validates against `spec/dna-card-v2.schema.json`; `eval_hash` changes when any of harness version / prompt / scoring / decoding / cert-set version changes, and only then.
- [ ] Contamination check blocks certification when overlap ≥ 1 %.
- [ ] Cert set is byte-identical before and after an Arbitrage run (hash asserted in tests).
- [ ] Closed-ended metrics report F1; a test asserts `accuracy` is absent from the headline block.
- [ ] Judge is never the teacher model (asserted); position randomization and length control are on by default.
- [ ] Gateway client passes contract tests against the mock; selecting `gateway` in `airgapped` is refused with the standard banner.
- [ ] `docs/CERTIFIED_MODELS.md` gains one row from `nucleus.certify` with the correct status.
- [ ] `build-report.md` includes the 10 eyeball prompts with outputs.
- [ ] From `/forge`, Buddy walks a first-time user from blank page to a running build without touching `.env`.
- [ ] With the deep runtime installed, every local inference call (teacher, judge, shard) routes through QueueLLM; a test asserts no Ollama/AirLLM provider is instantiated unless QueueLLM import fails, and the DNA card's `runtime` field reads `queuellm`.

## 8. Test plan (weighted per ARCHITECTURE.md §8)

- **Security 30 %** — build token never logged/echoed; gateway host allow-list exact-match; `domain.yaml` path traversal in `corpus.sources`; secrets file mode 0600; MCP tool inputs rejected outside GBNF schema.
- **Regression 25 %** — Chat Compute Source untouched for non-Nucleus flows; Buddy residency; existing `CERTIFIED_MODELS.md` rows untouched; egress guard unchanged.
- **Happy 20 %** — plan → build → certify on a 50-example fixture corpus in CI (no real teacher; a stub provider returns canned logits).
- **Setup 15 %** — `arailctl nucleus` on minimalist gives a clear "needs maximus deep runtime" for `build`, works for `plan`.
- **Buddy voice 10 %** — interview prompts and card narration match the two-line warm-summary style; no jargon leaks from the pipeline internals.

## 9. Files touched (expected)

```
src/arail/nucleus/{__init__,plan,build,eval,certify,publish,preflight}.py
src/arail/nucleus/providers/{base,queuellm,fallback_ollama,gateway,mixed}.py   # local == queuellm
src/arail/nucleus/evals/{splits,contamination,closed,open_lc_judge,executable_kernel,hash}.py
src/arail/nucleus/cards/{dna_v2,build_report,certified_models}.py
src/arail/portal/templates/forge.html
src/arail/portal/app.py                      # /forge routes, nav
src/arail/agents/buddy/skills/nucleus_partner.py
lab/mcp/arail_ops/tools/nucleus_*.py
arailctl                                     # nucleus subcommand
configs/domains/linux-kernel.yaml
configs/domains/linux-kernel.eyeball.txt
spec/dna-card-v2.schema.json
docs/{nucleus,nucleus-architecture,nucleus-gateway-contract}.md
tests/test_nucleus_*.py
```

---

## `/sprint` invocation

```
/sprint nucleus-sprint-1 --brief sprints/nucleus-in-arail-brief.md --profile local --agents visionary,architect,builder,qa
```

Drop this file at `/Users/netsushi/ProJects/arail/sprints/nucleus-in-arail-brief.md`.
