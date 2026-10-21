# Oracle (Frontier) — Ultra Plan

**Status:** Plan approved 2026-04-26. Implementation sequenced as 6 small PRs with explicit bail point after Sprint D.
**Repo:** Lives in [`qukaizen/arail`](https://github.com/qukaizen/arail). Backend dependency on [`qukaizen/aerollm`](https://github.com/qukaizen/aerollm) (Phase 4 MoE for v1.5 swap-back).
**Notes prepared:** 2026-04-26.

## TL;DR

Oracle is the page where slow is the point. You ask a hard question; the lab spins up a frontier-class model that *doesn't fit in your RAM*; weights stream from NVMe layer-by-layer; the streaming is visible; and the answer makes the wait obvious-in-hindsight. Built on AirLLM today (using our [#281](https://github.com/lyogavin/airllm/pull/281) fork), with QueueLLM Phase 4 swap-back as the v1.5 milestone for a ~3× speedup at zero UX cost.

**Default model v1:** `gpt-oss-120b` — 117B total, **5.1B active per token** (MoE), 234 GB on disk. The MoE structure is the engineering story: only ~10 GB working set on a 16 GB Mac, because only the active experts need to be resident at any moment.

**OSS launch positioning:** this is the marquee feature for ARAIL's first public release. The README's hero screenshot is a mid-run Oracle with the layer-streaming visualization at ~50%, the active-experts indicator showing 7/16, and tokens flowing into the answer panel.

## Why Oracle matters

Every other LLM UI on the planet treats latency as the enemy. Oracle inverts that frame. This is the page where:

- **Slow is intentional.** A spinner is the wrong UX. Live "Loading layer 47 of 80, active experts 7/16, NVMe 1.8 GB/s" turns the wait into the show.
- **Depth is the deliverable.** A 5.1B-active model at 117B total produces qualitatively different reasoning than a 7B fast model. Side-by-side comparison makes that undeniable.
- **You ran it locally.** No API call, no rate limit, no data leaves your machine. The OSS framing earns trust that hosted services can't.

The page is the demo. If we ship it well, three things become true:

1. The README screenshot stops people scrolling.
2. The 90-second demo video gets shared.
3. People understand SSD-streaming inference is real, not marketing.

## Naming + positioning

**Oracle (Frontier)** — Oracle is the name; "Frontier" in parens signals the technical category. Strong reasons:

- "Oracle" implies depth, deliberation, slow but profound
- "Frontier" tells technical readers it's frontier-class models (≥100B), not just a clever 7B
- Page renders as **"Oracle"** in nav; documentation and metadata use **"Oracle (Frontier)"** so search hits land

**The Oracle bar:** ≥100B parameters total. No exceptions. This is doctrine — Oracle exists to do what smaller models cannot. A 70B answering hard questions is "chat"; a 117B+ MoE answering hard questions is "Oracle."

**The Benchmark bar:** ≤1.5 TB on disk. Models above this cap (e.g., Kimi K2 at ~1.8 TB) are out of scope for first-party benchmarking until storage cost considerations change.

## Catalog tier formalization (Sprint A)

[`catalog/models.toml`](../../catalog/models.toml) gains two boolean fields per model:

```toml
[[model]]
id = "gpt_oss_120b_bf16"
# ...existing fields...
oracle_eligible    = true   # ≥ 100B parameters total
benchmark_eligible = true   # ≤ 1.5 TB on disk
```

Models meeting both bars today (in size order):

| Model | Total | Active | Disk | Oracle | Bench |
|---|---:|---:|---:|:-:|:-:|
| `gpt_oss_120b_bf16` | 117B | 5.1B (MoE) | 234 GB | ✅ | ✅ |
| `glm_4_5_bf16` | 355B | 32B (MoE) | 710 GB | ✅ | ✅ |
| `llama_3_1_405b_bf16` | 405B | 405B (dense) | 810 GB | ✅ | ✅ |
| `deepseek_v3_bf16` | 671B | 37B (MoE) | 1.3 TB | ✅ | ✅ |

Oracle's model picker resolves from `catalog.model[*].oracle_eligible == true`. The benchmark harness resolves from `benchmark_eligible == true`. Hardcoded model lists stay out of the page code — the catalog is the single source of truth.

## The user loop

```
1. Land at /oracle.

2. Ask a hard question.
   "Walk me through how speculative decoding actually works under the hood,
   including why it preserves the target distribution under temperature
   sampling, with rejection-sampling math."

3. Pre-flight cost panel surfaces:
   ┌──────────────────────────────────────────────────────────────────┐
   │ 🔮 Model: gpt-oss-120b   (117B params, 5.1B active per token)    │
   │ 🐌 Estimated time: ~9 minutes  (confidence: 75%)                 │
   │ 💾 Working RAM peak: ~10 GB    NVMe read: ~1.8 GB/s sustained    │
   │ 📦 Disk: model already on disk (234 GB)                          │
   │                                                                  │
   │ [✓ Run on Oracle]   [Quick answer instead (~15s on Qwen2.5-7B)]  │
   └──────────────────────────────────────────────────────────────────┘

4. Hit run. Page enters streaming mode:
   ┌──────────────────────────────────────────────────────────────────┐
   │ Layer streaming                                                  │
   │ ████████████████████░░░░░░░░░░░░░░░░░░░░  47 / 80 layers         │
   │ Active experts (this layer):  ▓▓▓▓▓▓▓░░░░░░░░░  7 / 16 active    │
   │ Prefetch: HIT 89% · MISS 11%                                     │
   │ RAM: 9.4 GB / 16 GB    NVMe: 1.8 GB/s                            │
   │                                                                  │
   │ Output (token-by-token):                                         │
   │ Speculative decoding works by having a small "draft" model       │
   │ propose K tokens at a time, which the larger "target" model      │
   │ then verifies in a single forward pass. The key insight is that  │
   │ a single forward of the target model produces logits for all     │
   │ K+1 positions simultaneously...                                  │
   │                                                                  │
   │                                            [⏹ Cancel] [⌨ Notify] │
   └──────────────────────────────────────────────────────────────────┘

5. When done, three things happen automatically:
   • Session saves to PKB as a study artifact
     (lab/pkb/study/2026-04-26-spec-decode-explained.md)
   • Replay bundle written to lab/pkb/oracle/replays/<run-id>.json
   • A "Compare with fast model" button appears

6. (Optional) Click compare. Page splits side-by-side:
   ┌─────────────────────────────────┬─────────────────────────────┐
   │ Fast (Qwen2.5-7B, 15s)          │ Oracle (gpt-oss-120b, 9m12s)│
   │ ─────────────────────────────── │ ─────────────────────────── │
   │ Speculative decoding speeds up  │ Speculative decoding works  │
   │ LLM inference by predicting     │ by having a small "draft"   │
   │ multiple tokens at once. A      │ model propose K tokens at a │
   │ smaller model proposes tokens   │ time, which the larger      │
   │ that the larger model verifies. │ "target" model then verifies│
   │ ...                             │ in a single forward pass... │
   │                                 │                             │
   │ 98 words · 0 citations          │ 847 words · 3 citations     │
   └─────────────────────────────────┴─────────────────────────────┘
```

That's the loop. Single-turn for v1 (multi-turn deferred to v1.5 — see Q1).

## Backend strategy — what we ship vs what we wait for

**Today's reality (2026-04-26):**

| Engine | gpt-oss-120b support | Notes |
|---|---|---|
| AirLLM (upstream main) | broken on Apple Silicon | crashes per [#280](https://github.com/lyogavin/airllm/issues/280); use our fork |
| AirLLM (`qukaizen/airllm@fix/mlx-torch-tensor-coerce`) | works on Apple Silicon | our fork with [#281](https://github.com/lyogavin/airllm/pull/281) patch applied |
| QueueLLM | not yet — Phase 4 (MoE) is the QueueLLM port that lights this up | swap-back happens automatically once Phase 4 lands |

**v1 backend: AirLLM via our fork.** Pinned in `.env`:

```bash
AIRLLM_PACKAGE_SPEC=git+https://github.com/qukaizen/airllm.git@fix/mlx-torch-tensor-coerce
ORACLE_MODEL=gpt-oss-120b
ORACLE_BACKEND=airllm
```

**v1.5 backend: QueueLLM Phase 4 swap-back.** When QueueLLM ships its MoE port, the runtime preference becomes `["aerollm", "airllm"]` and the page transparently uses QueueLLM with no UX change. Expected outcome: ~3× speedup (per the v0.1-alpha headline ratio of QueueLLM vs `mlx_lm`); a 9-minute Oracle becomes a 3-minute Oracle.

**Engine picker:** hidden behind an "Advanced" toggle; default + recommend AirLLM today, QueueLLM after Phase 4.

## The five "wow" features

These are what make Oracle unforgettable on first use. Engineered together, not as a list of TODOs.

### 1. Live layer-streaming visualization

Subscribe to AirLLM's layer-load events (or QueueLLM's `aero-bus` `PrefetchHit` / `PrefetchMiss` / `LayerInstalled` / `LayerEvicted` after swap-back) and render them as:

- A live progress bar (layer N / 80)
- A sparkline of recent prefetch hit-rate
- RAM and NVMe gauges updating at 4 Hz

This is the "magic happening in real time" moment. People who have never thought about how a model loads from disk see it for the first time.

### 2. Active-experts visualization (MoE-specific)

For MoE models, surface which experts are active per layer per token. For gpt-oss-120b at 16 experts per layer, render:

```
Layer 47:  ▓▓▓▓▓▓▓░░░░░░░░░  active experts: 7/16
```

This makes the MoE story tangible. "We're running a 117B model on your laptop because only 5.1B of weights are hot at any moment" stops being marketing and becomes a thing the user can *see*.

For dense models (Llama-3.1-405B at v1.x), this widget hides — there's no expert routing to show.

### 3. Cost-aware pre-flight estimator

Before the user commits to a 9-minute wait, show:

- Estimated wall-clock with confidence interval
- Working-RAM peak
- Sustained NVMe read rate
- Disk space check (model present? if not, one-click download with progress)

Powered by hand-tuned heuristics in v1 — `(model_total_b, prompt_tokens, max_new_tokens, available_ram_gb, nvme_read_speed_mbps)` → estimate. Records actual results to `lab/pkb/oracle/calibration.jsonl`. The estimator improves over time as data accumulates.

**This is the sprint with epistemic risk** — see "Bail criteria" below.

### 4. Compare-with-fast-model side-by-side

The marquee feature for the OSS launch. Same question, two engines, side by side:

- Left: Qwen2.5-7B via QueueLLM mlx-native (~15s)
- Right: gpt-oss-120b via AirLLM (our fork, ~9 min)

With word counts, citation counts, and time-to-first-token displayed for both. The visual contrast — short-and-confident vs long-and-substantive — is what gets shared on Twitter.

**UX choreography:** the fast model finishes first (~15s), gets cached as text. The Oracle continues running solo. The compare view fills in progressively as Oracle's output streams. Doubling RAM with two parallel runs would defeat the purpose.

### 5. Auto-save to PKB as study artifact

Every Oracle session becomes a Markdown file under `lab/pkb/study/<date>-<slug>.md` with:

- The question
- The answer (Oracle's, full)
- The fast-model answer (if compare was run)
- Citations extracted from the answer
- Model + seed + temperature + replay-bundle path
- Wall-clock timing breakdown

Reading the PKB is now reading your own personal lecture transcripts. Researchers who use Oracle for a week build up a real study library.

## Engineering sprints — 6 PRs, ~19 days

Each sprint is its own PR with a real test bar. Bail point after Sprint D if the cost estimator is hopelessly inaccurate (only sprint with real epistemic risk).

### Sprint A — Catalog tiering + backend integration (~3 days, ~250 LOC)

- Add `oracle_eligible` + `benchmark_eligible` boolean fields to every entry in `catalog/models.toml`
- Extend `arail.router.backends.AirLLMBackend` to support model selection from the catalog (currently hardcoded to one model)
- Add the threadpool-executor pattern (mirrors AeroLLMBackend from #13) — required because AirLLM's underlying torch ops aren't async-safe under FastAPI's worker pool
- Add `/api/oracle/run` endpoint that streams via Server-Sent Events
- Smoke test on real gpt-oss-120b checkpoint (download once, ~234 GB)

**Test bar:** `arail oracle ask "test"` returns coherent text via the AirLLM-fork backend; second call works (no thread-affinity panic).

### Sprint B — Page scaffold + token streaming UI (~4 days, ~500 LOC)

- New `/oracle` route in `src/arail/portal/app.py`
- Template `oracle.html` with the three-phase layout (ask → streaming → compare)
- Server-sent events stream for token-by-token output via async wrapper around `AirLLMBackend.stream()`
- Cancel-mid-inference button (graceful Runtime interrupt, releases the executor thread)

**Test bar:** smoke test browses to `/oracle`, asks a question, cancels mid-stream — no zombie threads, no leaked KV cache, page returns to idle state.

### Sprint C — Layer-streaming + active-experts visualization (~4 days, ~500 LOC)

- WebSocket subscription to AirLLM's layer-load events (need to instrument AirLLM-side or wrap calls with telemetry shims; investigate which is cleaner)
- Bridge: small Python wrapper around `AirLLMBackend` that exposes layer-load + active-expert events to the FastAPI side via an `asyncio.Queue`
- Frontend rendering: progress bar + prefetch-hit sparkline + RAM/NVMe gauges + active-experts widget (hidden for dense models)
- Re-uses existing `nav.js` + `style.css` patterns; no new frontend framework

**Test bar:** layer events flow to the browser at >= 4 Hz; cancellation correctly stops event flow; active-experts widget renders for MoE models, hides for dense.

### Sprint D — Cost-aware pre-flight estimator (~3 days, ~300 LOC)

- Calibration: hand-tuned heuristics over `(model_total_b, prompt_tokens, max_new_tokens, available_ram_gb, nvme_read_speed_mbps)`
- Records actual results to `lab/pkb/oracle/calibration.jsonl`
- Estimator surfaces: wall-clock estimate ± confidence interval, working-RAM peak, NVMe rate
- Disk-presence check + one-click download with progress UI for missing models

**Bail point:** if the estimator is consistently off by >50% after 20 measured runs, document the negative result in [`docs/plans/oracle-frontier-results.md`](oracle-frontier-results.md) and ship Oracle without it. The streaming visualization (Sprint C) is the consolation prize that already justifies the wait.

### Sprint E — Compare mode + PKB integration (~4 days, ~600 LOC)

- "Compare" button triggers a parallel run of Qwen2.5-7B (via QueueLLM mlx-native) on the same prompt
- Side-by-side rendering: word counts, citation counts, time-to-first-token, total wall-clock
- "Save to PKB" button creates `lab/pkb/study/<date>-<slug>.md` with the full session
- Replay-bundle integration: every session writes to `lab/pkb/oracle/replays/<run-id>.json`
- Citation extraction: parse the Oracle answer for `[1]`, `[2]` markers, link them to source URLs/PKB entries when possible

**Test bar:** running a real Oracle question with compare = on produces a PKB markdown file with both answers, both runtimes, and a working replay-bundle path; re-running the replay reproduces the Oracle answer bit-identically.

### Sprint F — Demo, docs, screenshots, README (~2 days, ~150 LOC + media)

- README hero screenshot (mid-streaming run with active-experts visible)
- 90-second demo video (or animated GIF) of the full loop
- `docs/oracle.md` explaining the SSD-streaming + MoE concept for technical readers, with a system diagram
- Update [`BLUEPRINTS.md`](../../BLUEPRINTS.md) with Oracle as a feature highlight (becomes the "what's possible" callout)
- README front-matter update: Oracle becomes the lead feature

**Test bar:** visual review of the README hero on github.com renders cleanly; demo video plays inline on mobile.

**Total: ~19 days of focused work, 6 PRs, ~2200 LOC + ~50 MB of media.**

## Risks and open questions

### R1 — gpt-oss-120b HF repo path verification

The catalog entry assumes `openai/gpt-oss-120b`. Sprint A's first task is verifying this resolves on HuggingFace; if the actual path differs (e.g., `openai/gpt-oss-120b-instruct`), update the catalog before downloading.

### R2 — Mac thermal throttling on extended runs

A 9-minute continuous SSD read + GPU compute will warm the laptop substantially. Mitigation: surface CPU/GPU temperature in the streaming view if available; document that sustained Oracle workloads are best on M-series Studios with active cooling.

### R3 — Pre-flight estimator accuracy (Sprint D's bail point)

If the estimator consistently misleads users (says "9 minutes" but Oracle takes 30), it's worse than no estimator. Honest bail: ship Oracle without it, or ship with a wide confidence interval and explicit "this is a rough estimate" framing.

### R4 — Compare-mode RAM contention

Running Qwen2.5-7B (via QueueLLM, ~14 GB resident) and gpt-oss-120b (via AirLLM, ~10 GB working set) in parallel hits ~24 GB RAM on a 16 GB Mac. Mitigation in Sprint E: serialize them — fast model first (~15s), then Oracle solo. Compare view fills in progressively.

### R5 — AirLLM-vs-QueueLLM swap-back UX continuity

When QueueLLM Phase 4 ships and we swap back to QueueLLM as the v1.5 backend, the Oracle UX must be identical. Mitigation: route through `arail.router.backends` (already supports backend_preference list) — Oracle code never references "AirLLM" or "QueueLLM" directly, only "the Oracle backend the router resolves."

### R6 — AirLLM upstream non-merge of #281

Until [#281](https://github.com/lyogavin/airllm/pull/281) merges upstream, users install our fork. Documented in [BLUEPRINTS.md](../../BLUEPRINTS.md). If upstream rejects the patch, our fork stays as the recommended install path indefinitely. Acceptable.

### Q1 — Multi-turn vs single-question for v1?

**My lean: single-question for v1.** Multi-turn adds KV-cache-management complexity (the 5.1B-active-expert KV cache is non-trivial; persisting it across turns means caching expert routing decisions which is hard) and dilutes the "this is a deep ask" framing. v1.5 adds multi-turn after the page is established.

### Q2 — File uploads (PDF, Markdown) as context?

**My lean: defer to v2.** v1 is "ask a deep question, get a deep answer." File uploads add file-parsing, tokenization-budget, and context-window-management work that doubles the v1 scope. v2 is "Oracle reads your stuff."

### Q3 — Telemetry persistence?

**My lean: live-only via WebSocket; nothing persisted from the bus.** The replay bundle captures user-facing details (question, answer, model, seed, citations); raw layer-load telemetry is voluminous (~80 events/token × 500 tokens = 40K events per run) and a debugging concern, not a study artifact.

## Acceptance bar — what "v1 done" means

Five things all true at the same time:

1. **`./arailctl oracle ask "<question>"` works end-to-end on a fresh checkout.** No "TODO" buttons, no broken UI states. Every error path has a friendly message + actionable next step.
2. **Streaming visualization actually streams.** Layer events arrive in the browser at ≥ 4 Hz. The progress bar updates smoothly. Active-experts widget renders for gpt-oss-120b.
3. **Compare mode produces a meaningful comparison.** Fast model and Oracle answer the same question; word counts and time-to-first-token are accurate; visual contrast is obvious.
4. **PKB persistence works.** Every Oracle session creates a study Markdown file with all metadata; replay bundle is bit-reproducible.
5. **README hero screenshot is committed.** The image, the demo video/GIF, and `docs/oracle.md` are all in the repo. The OSS launch is ready.

If any of those slip, v1 isn't done.

## Bail criteria

Oracle's bail point is Sprint D. If the cost estimator turns out to be hopelessly inaccurate (>50% off on 20 measured runs):

- Ship Sprints A/B/C/E/F without it
- Document the negative result in `docs/plans/oracle-frontier-results.md`
- Replace the cost-panel with an honest "Oracle takes minutes, not seconds — go grab coffee" message
- v1 still ships; the streaming visualization carries the wait UX

This is the same posture as the AirLLM-not-viable-on-MLX pivot in v0.1-alpha and the Phase C.3 bail criteria. **Documenting a negative result is a valid outcome.** Bailing leaves us with a working Oracle, just without one of the planned features.

## Reproducer (after all 6 sprints land)

```bash
# 1. Set up the lab.
./arailctl setup
# Answer prompts. Choose tier=max (Oracle is in the max surface set).

# 2. One-time: download gpt-oss-120b (~234 GB).
huggingface-cli download openai/gpt-oss-120b \
    --local-dir $ARAIL_MODELS_DIR/gpt-oss-120b

# 3. Install the AirLLM fork with the MLX patch.
pip install --user \
    git+https://github.com/qukaizen/airllm.git@fix/mlx-torch-tensor-coerce

# 4. Boot the lab.
./arailctl start

# 5. Open Oracle in the browser.
open http://localhost:8080/oracle

# 6. Ask a hard question; watch the streaming; compare with the fast model.
```

## Adjacent: 400B+ benchmarking sprint (separate plan)

The user's "≤ 1.5 TB benchmarking" requirement is a parallel effort, lives in [`qukaizen/aerollm`](https://github.com/qukaizen/aerollm) under `docs/benchmarks/frontier-bench-plan.md` (to be authored as a sibling plan). Scope:

- Extend `scripts/perf/airllm_baseline.py` to handle 400B+ checkpoints (long warmup, longer per-prompt timeout, reduced n_prompts to keep total runtime sane)
- Same comparison shape: QueueLLM (when Phase 4 lands) vs AirLLM-via-our-fork
- Catalog `benchmark_eligible == true` resolves the model set automatically
- Hardware requirements: NVMe ≥ 1 TB free for the larger models (Llama-3.1-405B = 810 GB, GLM-4.5 = 710 GB, DeepSeek-V3 = 1.3 TB)

Oracle and benchmarking share the catalog and the model fixtures; they have different sprints because Oracle is the user-facing page and benchmarking is the internal validation infra.

## See also

- [`BLUEPRINTS.md`](../../BLUEPRINTS.md) — blueprint concept; Oracle becomes a feature highlight after Sprint F
- [`catalog/models.toml`](../../catalog/models.toml) — gains `oracle_eligible` + `benchmark_eligible` fields in Sprint A
- [`qukaizen/aerollm` Phase 4](https://github.com/qukaizen/aerollm/blob/main/MILESTONES.md) — the QueueLLM MoE port that lights up v1.5 swap-back
- [`lyogavin/airllm#281`](https://github.com/lyogavin/airllm/pull/281) — the MLX patch our fork carries until upstream merges
- [`docs/plans/oracle-frontier-results.md`](oracle-frontier-results.md) — Sprint F deliverable; honest write-up of what the v1 Oracle measured (positive or negative)
