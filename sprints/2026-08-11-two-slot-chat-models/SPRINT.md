# Sprint: two-slot chat model selection (redesign attempt #7)

**Started:** 2026-08-11 · **Branch:** `qukaizen/chat-inference-model-selection-f18c87`

## Origin

The Chat tab's model-selection UI is the most-reworked surface in ARAIL —
`sprints/2026-07-20-model-ux-unification/VISION.md` calls itself attempt
#6. This sprint is attempt #7, triggered directly by the operator:

> "I want this model in the GPU at all times, ie., some 1B easy and
> efficient inference engine. Probably never more than 3B unless user
> overrides it. Then the 2nd model is there for aeroLLM to respond to...
> This is the 3rd time with Fable on fixing this. Simply make it better."

Full plan: `~/.claude/plans/it-s-time-for-another-adaptive-papert.md`
(preserved outside the repo per the planning tool's convention).

## What shipped

A registry-backed two-slot model: **resident** (`tier0-local` — small,
always-warm, pinned) and **deep** (`tier1-aerollm` — AeroLLM-served,
resident-once-loaded). Five commits, one per phase:

| Phase | Commit | What |
|---|---|---|
| 0 | `2a206a5` | COR-1 inflight-lock leak fix; `.compare-strip[hidden]` CSS clobber |
| 1 | `3e88ca8` | `slots` block + per-row `params_b`/`ceiling`/`attribution` on `/api/chat/models` |
| 2 | `9624386` | Registry "env wins only when env moved"; worktree test-resolution fix (`tests/conftest.py` sys.path pin); tier1 `enabled` decoupled from `AEROLLM_RESEARCH` |
| 3 | `993b93e` | `ARAIL_RESIDENT_PIN` (keep_alive `-1` for the registry's tier0 model); `tier0_keepwatch_loop()` re-warms after an external eviction |
| 4 | `06170e0` | `AEROLLM_RING_DEPTH` profile wiring; real in-process deep-model swap (`_swap_optional_chat_backend`); real aeroLLM eject (was refuse-only) |
| 5 | *(this commit)* | `chat.html` UI rebuild — two slot chips replace five overlapping affordances; docs |

## The design

Two header chips in the Chat tab, each 1:1 on a registry entry:

- **Resident** — click arms it, `▾` opens a picker defaulting to ≤3B
  models (`slot_default_visible`), "show larger" reveals up to the <8B
  primary ceiling. Every row's eligibility comes from the SAME chokepoint
  the send path enforces (`registry/ceiling.py::resolve_answering_model`)
  — a row can never promise something send-time refuses. Llama-family
  rows carry "Built with Llama" (license disclosure, NOTICE:36-46).
- **Deep** — picker lists the configured aeroLLM model plus every other
  installed MLX-runtime model, checked against
  `hardware.secondary_model_cap_b()`. Selecting a different model swaps
  in-process (Phase 4) — no portal restart.
- **Compare** (`+ Compare`) runs both slots side by side — column A ≡
  resident, column B ≡ deep, always. Per-column chat history
  (`aHistory`/`bHistory`) fixes a real bug where both columns shared one
  history array.
- Compute-source pills: **honest-disable** — a non-wired source (Claude,
  NVIDIA, OpenRouter, HF, Custom — send-path wiring is a follow-up) shows
  disabled with the reason at click time instead of erroring at send
  time.
- Model install: **minimal rows**, not a full catalog gallery (both
  scope decisions were explicit operator picks via AskUserQuestion before
  implementation started).

Kill list executed: dead `#model-picker` shell, `renderPicker()`, the
below-the-fold model rail (`renderModelRail`) + its markup + CSS
(`.model-card`/`.mc-*`/`.rail-list` — ~100 lines of now-orphaned CSS
removed in the same commit), the separate "active model" strip
(`renderActiveCard`), `renderPickerB`/`selectModelB`'s freeform "any
model in column B", the "swap →" scroll-flash, client-synthesized deep
virtual entries in `init()`, the `prefetch_depth` fake tunable.

## Decisions and reversals worth recording

- **B ≡ deep, always** (this sprint) deliberately reverses commit
  `dc32370`'s "any model in column B" fix. That earlier fix answered a
  real complaint ("I can't change the models") by un-restricting column
  B to any installed model — but with only one deep backend ever
  installed in practice, it didn't give real choice, just removed a
  guardrail. The actual fix was always "give the deep slot its own real
  candidate list" (the Deep picker's "other installed models" section),
  not "let column B be anything." Both fixes were responding to the same
  underlying complaint; this one addresses it correctly.
- **F-OVERSELL residency stance, reaffirmed.** AeroLLM keeps its model
  fully resident once loaded — this sprint's own `regime` copy
  ("Resident once loaded... does not stream") repeats that stance rather
  than walking it back. `ring_depth` (Phase 4) caps resident
  transformer-block slots but does not make the model itself
  layer-streamed; no copy anywhere claims otherwise (verified by
  `tests/test_model_ux_phase0_oversell_copy.py`).
- **HON-1 closure.** The rail-card/active-card duplicate-implementation
  risk HON-1 existed to catch (warm-dot clearing gated on `d.ok` in one
  copy but not the other) is structurally gone — Phase 5 unified both
  into a single `ejectModel(side)` handler both chips call. One
  implementation can't drift from its own twin.
- **A3 restriction closed.** The earlier sprint's "aeroLLM singleton
  cannot be hot-freed" limitation is gone — Phase 4 wired real teardown
  (`AeroLLMBackend._close()`, `_swap_optional_chat_backend`). The deep
  chip's eject is now gated on residency alone, no runtime carve-out,
  unlike the resident chip's eject (ollama-runtime-only, since that's the
  only runtime `/api/chat/eject` can actually free in-process).
- **F-FAKEFIT, last site closed.** `makeOpt()`'s verdict fallback
  (`m.fit.verdict || 'good'`) was flagged as a known, out-of-scope
  survivor when `renderModelRail`/`renderActiveCard` were fixed in an
  earlier sprint (BUILD_LOG.md "Findings for follow-up"). With those two
  functions now deleted, `makeOpt()` is the only row renderer left —
  fixed to `'Unknown'` in this sprint, closing the last fake-"good" site.
- **One honesty correction to the operator's framing** (surfaced in the
  approved plan and in the resident-picker copy): "hardly any loading" /
  "just load the initial layer" is not what's wired. AeroLLM keeps the
  deep model fully resident once loaded; `ring_depth` (the nearest real
  primitive) caps resident transformer-block slots, it doesn't defer
  loading them. True first-layer-only preload doesn't exist in the
  aerollm API — out of scope here, would be an aerollm-repo feature
  request.

## Test suite

68 test functions across 7 files needed updating after the UI rewrite —
almost all were pinning markup Phase 5 deliberately deleted
(`renderModelRail`, `renderActiveCard`, the old rail-card eject gating,
`deepEntries`) or a property Phase 4 deliberately reversed (aeroLLM
ejectability). Each was rewritten against the new two-slot markup with a
docstring explaining the relocation/reversal, not silently deleted — see
the commit diff for `tests/test_model_ux_phase0_headers.py`,
`test_model_ux_phase0_oversell_copy.py`, `test_model_ux_phase0_warmth_probe.py`,
`test_model_ux_phase0_wiring.py`, `test_model_ux_phase0b_full_suite_checkpoint.py`,
`test_qa_model_ux_memory_and_eject_fidelity.py`,
`test_qa_provider_dropdown_paranoid.py`, `test_chat_no_autowarm.py`.

Full-suite triage method: git-stash the chat.html diff, re-run the
suspect files against unmodified HEAD to get a real baseline, pop the
stash, re-run against the new code. Confirmed via this method that the
large majority of "new" full-suite failures on a first pass (world-forge
egress blocks, instance-port allocation races, shell-injection timing,
etc.) were pre-existing and environment-dependent, not caused by this
sprint — see the verification section below for the final count.

## Follow-ups (owner + date required — attempt-#7 tripwire)

Dateless follow-ups are exactly the failure pattern this sprint exists to
close. Every item below needs an owner and a target date before it's
considered tracked, not just noted:

- `_resilient_chat_default()`'s "ai-engineer:latest" back-compat alias
  resolves to 7.0B via `MODEL_METADATA_OVERRIDES`, contradicting its own
  docstring's claim of being a historical alias for the ~1B default —
  flagged via `spawn_task` during Phase 1, not yet triaged. **Needs an
  owner.**
- AirLLM stays out of the two-slot model by design (a third backend with
  a genuinely different regime — real layer-streaming). If AirLLM usage
  grows, it may deserve its own slot rather than living only in
  `optional_backends`. **No owner, no date — explicitly deferred, not
  forgotten.**
- Cloud compute-source send-path wiring (the pills are honest-disabled,
  not wired) — explicit operator scope decision for this sprint, real
  follow-up. **No owner, no date yet.**
- QA measurement of `ring_depth`'s real RSS effect (1/2/4 vs unset) on a
  real checkpoint was in the original plan's verification section but
  not run this sprint (no live large checkpoint available in this
  environment). Copy stays conservative ("resident once loaded") until
  someone measures it. **No owner, no date.**
