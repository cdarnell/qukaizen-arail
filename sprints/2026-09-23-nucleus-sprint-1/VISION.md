# Vision: Model Forge — Nucleus as a first-class ARAIL surface (`local` profile)

**Date:** 2026-09-23
**Product:** arail (absorbs the Nucleus distillation pipeline per brief D1)
**Wedge size:** two sprints as briefed (items 1–10). One sprint if items 3–5 are cut as recommended below.
**Inputs:** `sprints/nucleus-in-arail-brief.md` (D1–D8 locked, not re-litigated here), `SPRINT.md`.
**Verified against:** this worktree at `f4f84ca5`, plus `qukaizen-queuellm` at `v1.1.0`/HEAD and the Buddy
operator brief on `qukaizen/arail-buddy-front-and-center`. Every "does not exist" claim below comes from a `git grep` across
all local branches, not from the brief.

---

## User

**The first user is Charlie, the operator.** He runs ARAIL maximus on his own M5 Max (36 GB, which this
worktree's host reports, `hw.memsize` = 38654705664). He has `LAB_MODE=airgapped` set and QueueLLM (pinned
`aerollm_api` bundle v1.1.0) installed as the deep runtime, and the MoE checkpoints are in `/Users/Shared/models`.
He wants to type one command, walk away, and come back to a small domain model plus a card that tells him,
with numbers he would defend in public, whether the model is any good.

**The second user is aspirational and has no evidence yet:** a prosumer maximus user who has never distilled a
model and gets there with Buddy's help. The brief's acceptance criterion "Buddy walks a first-time user from blank
page to a running build" is written for this user. Nobody fitting this description has asked for it, and no
first-time user will touch this in sprint 1. So this VISION scores the sprint against Charlie and treats the
first-time-user criterion as a later wedge (see Wedge).

**About the domain:** `linux-kernel` was picked because it can be evaluated. Patch-applies, compiles, and
checkpatch give objective checks that most domains lack. Nobody was waiting for a kernel assistant. That makes
it a good first domain for proving the pipeline, but it means a win here does not show anyone *wants* a kernel
shard. The win condition is written with that in mind.

## Problem

Charlie cannot produce a small domain model on his own machine and trust what it scores. Two separate facts
cause this.

1. **The distillation that exists is not local-first and has never finished.** ARAIL already has a Model
   Building tab (`/build`, `src/arail/build/{nucleus_client,preflight,world_corpus,jobs,manifest}.py`,
   commits `1396ae3f`, `a3f2ffbb`, `c1162cb4`). It depends on the private `qukaizen-nucleus` docker-compose
   stack (orchestrator :8000, synthesizer :8005, trainer :8006). The last sprint that tried to finish the chain,
   `2026-07-22-distill-now`, stopped at "plan: on hold — awaiting go-ahead" and has sat there for two months.
   Its day-one blocker was standing up that certifier stack, and it was never resolved. The World-corpus build
   path still returns `"seal": None`. No QueueLLM teacher appears anywhere in it.
2. **A number without controls is noise.** Even a finished build today would produce a score with no
   baseline, no split between dev and cert data, no contamination guard, and no eval hash. So a "fidelity 0.85"
   could not be compared across builds or defended. The brief's CS224N discipline (§5) is the real product. It
   turns "I trained a LoRA" into "this shard beats its base by X on data it never saw, and this hash proves the
   yardstick didn't move."

The requested feature is a `/forge` page with Buddy. The underlying problem is a local, reproducible,
honestly-scored distillation loop. The UI only has value once that loop exists.

## Win condition

The operator asked what must be true at the end of items 1–9 for item 10 to be worth running. There are two
gates. Both thresholds are pre-committed.

### Gate A: items 1–9 done (stub provider, CI, no real teacher)

All of these must hold. A single failure means item 10 does not run.

- **A1.** `./arailctl nucleus plan → build → certify` runs on the 50-example fixture corpus using a stub
  provider that returns canned top-N logprobs. It emits a `dna-card.yaml` that validates against
  `spec/dna-card-v2.schema.json`, plus a `build-report.md` containing the 10 eyeball prompts.
- **A2.** `eval_hash` property test: changing any one of harness version / prompt / scoring / decoding /
  cert-set version changes the hash. Changing anything else (seed of an unrelated step, output path,
  timestamp) leaves it unchanged. Both directions are asserted.
- **A3.** A cert-set byte hash is asserted identical before and after an Arbitrage run. A contamination fixture
  with overlap ≥ 1 % makes certify refuse.
- **A4.** A synthetic over-budget config makes preflight refuse, and the refusal names the model to drop. A
  test asserts preflight never proposes evicting Buddy.
- **A5.** The provider-selection test: with the QueueLLM runtime importable, no Ollama/AirLLM provider is
  instantiated, and the card's `runtime` reads `queuellm`.
- **A6.** The gateway client passes contract tests against the mock server, and `gateway` under `airgapped`
  is refused with the standard banner.
- **A7.** The whole stub run adds zero lines to `lab/data/egress.jsonl`.

### Gate B: go/no-go spike before item 10 (real M5, at most half a day)

Item 10 can run for up to 8 hours, so a cheap measurement has to justify it first.

- **B1.** Through the `local` provider, QueueLLM extracts top-N logprobs from the chosen teacher for 20 real
  corpus windows. Measured throughput × reduced-corpus size must extrapolate to **≤ 4 h of extraction**,
  leaving the rest of the 8-hour budget for training and eval. If it doesn't fit, shrink the corpus slice
  before the run. Do not start a run you already expect to blow the budget.
- **B2.** The teacher's own score on a 50-item cert sample is **≥ 0.6 composite**. Below that, the ceiling
  (`irreducible_floor`) is so low that the student result means nothing and the eval or domain is broken.
- **B3.** During B1, Buddy's resident RSS stays within ±5 %.

### Item 10 win (the sprint's headline)

- **W1.** `./arailctl nucleus build linux-kernel --profile local` completes on the M5 in **< 8 h wall clock**
  with `LAB_MODE=airgapped` and zero new egress lines. It emits a signed, schema-valid DNA card v2.
- **W2. The student beats its own base on the frozen cert set.** Either the `lc_win_rate_vs_base` 95 % CI lower
  bound is > 0.50, **or** `cert.composite(student) − cert.composite(base_student)` is ≥ 0.10. **Reaching the
  0.85 fidelity target is NOT the win condition.** A first build honestly marked BETA at 0.74 is a success if
  W2 holds. A CERTIFIED stamp with no baseline gap would be a failure.
- **W3. Witnessed.** Charlie reads the 10 eyeball pairs (student vs base) and marks at least 6 as better from
  the student. He records the verdict in the sprint ledger before looking at the composite, to prevent
  anchoring.

## Wedge

**Recommended wedge (one sprint): the loop without the new chrome.** It covers items 1, 2, 6, 7, 8, and 9. For
item 5, build only a read-only DNA-card viewer at `/forge`: no plan form and no live progress beyond what the
existing activity stream already shows. Item 10 then runs behind Gate B.

**Defer items 3 (MCP tools) and 4 (Buddy `nucleus-partner` skill) to a follow-on sprint**, and with them the
acceptance criterion "Buddy walks a first-time user … without touching `.env`". The reasons, all checked in
the repo:

- **The `arail-ops` MCP server does not exist** on any branch in this repo. "Five new tools" really means
  building a new MCP server, its transport, and a GBNF schema layer. That is a sprint of its own.
- **"`buddy-embodied`" (cited as the lock for D4) is not recorded anywhere** in this repo. There is no
  `src/arail/agents/buddy/skills/` directory either, since Buddy is `src/arail/agents/buddy.py` plus
  `_builtin_buddy.py`.
- **It conflicts with the operator's own Buddy sequencing.** `OPERATOR_BRIEF.md` (2026-09-20) says
  "observability and guard rails go up first": P1 observability + kill switch, then P2 brain bake-off, and only
  at P3 the panel + goal-interview wedge. A Buddy interview skill here would either jump that queue or be built
  twice.

The operator could instead keep items 3–5 in scope. The honest wedge size is then **two sprints**, and the
ledger should say so up front instead of finding out mid-build.

The wedge runs entirely on the developer's own machine with no cloud account, which fits the QuKaiZen friction
profile. `local` is the only profile that executes, and `gateway` is exercised only against a mock.

## Disconfirming evidence

Pre-committed. When one of these fires, take the named action without re-arguing it.

1. **Gate B1 fails even at the smallest useful corpus slice** (under ~500 cert-eligible examples). Logit-level
   distillation through a streamed teacher is then too slow on this hardware for the `local` profile to be the
   product's default. **Action:** item 10 does not run. The sprint ships items 1–9. The operator re-decides
   between D5 and D8 (a smaller teacher, or sequence-level locally) before sprint 2. Do not quietly switch the
   mode during the sprint.
2. **W2 fails**: the student does not beat its base on cert. The pipeline works but distillation adds nothing
   at this scale. **Action:** sprint 2 (live gateway) **does not start** on the assumption that a better teacher
   fixes it. Run one follow-up that varies only corpus size before investing in the gateway.
3. **Gate B2 fails**: the teacher scores < 0.6. The eval or domain is broken, not the student. **Action:** fix
   the eval, since shipping a card graded against a broken yardstick is worse than shipping none.
4. **The executable kernel checks cannot run honestly on macOS.** A full `compiles` check needs a Linux kernel
   tree and a working cross-toolchain, and the macOS case-insensitive filesystem breaks kernel trees. **Action:**
   ship `patch_applies` + `checkpatch_clean` only. Mark `compiles` as `not_run` in the card, which also changes
   `eval_hash` and the composite formula version. Do not substitute a proxy and keep calling it "compiles".
5. **The behavioral kill signal.** Within 14 days of item 10, Charlie does not load the resulting shard in Chat
   for even one real question, and does not start a second `nucleus build` on a domain he actually cares about.
   Model Forge is then a pipeline demo and not a surface. **Action:** the gateway rework (sprint 2) and the
   registry (sprint 3) are deferred until that changes.

## Displacement

"Yes" costs these things. None of them is "nothing".

- **The Buddy front-and-center program (operator priority as of 2026-09-20).** This is the direct conflict.
  It competes for the same builder time and the same M5. The P2 brain bake-off and an 8-hour distillation run
  cannot share the box. If items 3–4 stay in scope, it also pre-empts P1's "guard rails first" rule for Buddy.
- **QueueLLM's MoE GA work.** The brief's candidate teachers (Llama-3.1-70B, Qwen2.5-72B) and default judge
  (`ai-engineer`, Qwen2.5-7B) are all **dense**. QueueLLM moved dense to a legacy lane on 2026-08-05
  (MoE-exclusive, `qukaizen-queuellm/CLAUDE.md`). If Model Forge becomes QueueLLM's flagship consumer on dense
  models, it pulls QueueLLM effort back into that lane. See the architect notes for the MoE-teacher option.
- **The existing `/build` tab and `2026-07-22-distill-now`.** This sprint either supersedes them or creates a
  second, parallel model-building surface (`/build` against a docker Nucleus, plus `/forge` against in-process
  Nucleus). Two surfaces would be a regression in product clarity. **The ledger should state explicitly that
  distill-now is superseded**, and the architect must decide `/build`'s fate (fold in, redirect, or retire)
  rather than leave it running beside `/forge`.
- **`qukaizen-nucleus` as a repo.** Under D1 its SSDP pipeline becomes a donor and stops being developed.
  Nucleus also carries the company-hub docs and the Rust `qkz` CLI, so its README/CLAUDE.md need a status note
  once this lands, or the workspace ends up with two "Nucleus"es.
- **Other in-flight arail work.** The main checkout has uncommitted `qukaizen/arail-ingress-spine` work, and
  the unmerged `qukaizen/arail-queuellm-local-rename` and `qukaizen/ddac-rename` branches are still waiting.
  They stay parked for another sprint.

## Open question: move the old Nucleus code or re-implement it

**Recommendation: re-implement against the brief, and salvage selectively.** This agrees with the brief. The
old SSDP pipeline is a service mesh (NATS + orchestrator + certifier + trainer containers), and that stack is
exactly what stalled distill-now. D1's in-process `src/arail/nucleus/` package is a different shape. Two things
are worth salvaging deliberately rather than rewriting from memory:

- ARAIL's own `src/arail/build/preflight.py` (resource and wall-clock estimator) and `world_corpus.py`. These
  are in-repo prior art for items 6 and 1.
- The Nucleus certifier's Ed25519 seal format.

**This needs the operator's answer before plan (it blocks item 8's `signed:` field):** should DNA card v2
signatures stay **continuous with Nucleus "Knowledge Isotope" seals**, meaning verifiable with the existing
`qkz isotope verify`? Or should they be a new ARAIL-held key and format? The first choice means porting the seal
code and its key custody. The second is simpler, but it orphans the Nucleus verification path. Where the signing
key lives (on the user's machine, versus a QuKaiZen key for published shards) also comes into play in sprint 3.

## Notes for the architect (concerns only, not design)

1. **The `runtime: queuellm` naming collides with the frozen AERO surface.** `runtime: queuellm` in
   `domain.yaml` and `runtime: queuellm` in the card are new user-facing names. ARAIL's backend id is `"aerollm"`,
   the registry id is `tier1-aerollm`, the class is `AeroLLMBackend`, and the import is `aerollm_api`. All of
   these are frozen and must not be renamed in this sprint. Map `queuellm` to the existing backend in one place.
   **More importantly:** the pinned bundle is **v1.1.0, which predates PR #298**, so it reads only `AERO_*` env
   vars. Nucleus code that sets `QUEUELLM_*` knobs would be silently ignored by the binary ARAIL actually ships.
   Use the `AERO_*` names through ARAIL's existing helpers until the bundle is re-pinned. The card's
   `teacher.provenance` should record the real bundle tag and sha256 (`aerollm_bundle_tag`/`_sha256` in
   `pyproject.toml`), not a hand-typed `queuellm@1.x`.
2. **Logprobs exist but have limits.** v1.1.0 does ship Phase R.2 logprobs (`Runtime.generate(..., logprobs=<path>)`,
   commit `e1731415`). However, the capture is **top-N per token, written to a file, and only on the
   StreamingBackend/MlxNative target**. The `'mlx'` subprocess shim refuses it. "Soft labels" are therefore
   truncated top-N distributions. Pick N and a renormalization method, and record both in the card (and in
   `eval_hash` where they affect scoring). Preflight must check that the backend target can emit logprobs
   before Phase A starts.
3. **Choose the teacher with D5 and QueueLLM's direction in mind.** Logit KD needs `tokenizer_parity`. A Qwen3
   **MoE** teacher already on disk (Qwen3-30B-A3B) paired with a Qwen2.5/Qwen3-family student may give tokenizer
   parity *and* stay on QueueLLM's MoE lane. The architect should verify the vocab match rather than assume it.
   `teacher.model: auto` ("largest streamable on disk") could pick a dense 70B with a mismatched tokenizer. Make
   `auto` prefer parity first and MoE second.
4. **The memory plan is written for 24 GB, but the box has 36 GB.** Also, per Buddy D17, Buddy's maximus voice
   is itself a *QueueLLM-served MoE*, not only a resident Ollama SLM. If Buddy and the teacher share the QueueLLM
   runtime or `inference_slot`, then D3/D4 residency and the ±5 % RSS criterion need a defined contention rule.
   Preflight should read actual memory and the actual Buddy brain rather than assume either. Whether 24 GB is a
   deliberate target floor is a question for the operator.
5. **Corpus acquisition vs airgapped.** `sources: [git:linux, lkml, lwn, cve]` cannot be fetched in airgapped
   mode, yet W1 requires zero egress. Corpus staging must be a separate, explicit step: a pre-staged snapshot
   with a manifest hash that goes into `pipeline_hash`. **Licensing:** LWN articles are copyrighted and the
   kernel is GPL-2.0. This doesn't block a local build, but it must be settled before sprint 3's `qkz pull`
   publication. The card should record per-source licenses now.
6. **`docs/CERTIFIED_MODELS.md` auto-append.** That file is a tracked, hand-curated, docs-hub-rendered
   reference. If every user's local build appends to it, every fork gets a dirty tree and the public list fills
   with unverified rows. Consider limiting the append to an explicit publish or QuKaiZen-signed path, or a
   separate local ledger.
7. **Output location.** ARAIL's convention is `ARAIL_MODELS_DIR` (env-driven, default `lab/models`), with
   `/Users/Shared/models` as the per-machine opt-in. The brief's repo-root `models/<slug>/` sits next to *tracked*
   files (`models/ai-eng/Modelfile.*`). Pick one location and make sure generated weights cannot be committed.
8. **"Watches Grafana."** No Grafana exists in `src/`. The Buddy brief explicitly rejects adding a metrics
   product ("not Prometheus/OTel"). Progress should flow through the existing activity stream.

## Recommended next step

**PROCEED to `/architect` with this VISION as the spec, on the reduced wedge.** That means items 1, 2, 6–9 plus a
read-only card viewer, Gate A → Gate B → item 10, and items 3–4 deferred. **The operator must first answer three
questions:**

1. **Scope:** accept deferring items 3–4 (MCP server + Buddy skill) and the "first-time user via Buddy"
   criterion? Or keep them and relabel the sprint as two sprints? Keeping them also means an explicit override
   of the Buddy brief's "guard rails first" sequencing.
2. **Seal continuity:** should DNA card v2 signatures verify with Nucleus's `qkz isotope verify`, or use a new
   ARAIL key and format?
3. **Superseding:** confirm that `2026-07-22-distill-now` is superseded, and say whether the existing `/build`
   tab is folded into `/forge` or retired.

Two lower-stakes questions can be answered during plan: whether the 24 GB memory plan is a deliberate floor
(the box is 36 GB), and whether the operator will accept an MoE teacher under D5/D8 if the dense 70B misses Gate B1.
