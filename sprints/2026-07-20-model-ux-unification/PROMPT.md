# PROMPT: Fix ARAIL's model selection UX — for real, this time

**Read this entire document before writing any code or design doc.** This is
attempt #6 on this exact problem. The first five are cited below with
receipts. If you skip straight to designing, you will very likely re-derive
a plan someone already wrote — and re-leave the same gaps open.

## Why this document exists

The operator has tried to fix this "half a dozen times," including once
with a large multi-agent effort (Fable/ultracode-style), and it still isn't
right. That is not a coincidence and it is not a "throw more agents at it"
problem. The investigation below found a specific, repeating failure
pattern across five real prior sprints: **real backend machinery gets
built, the wiring to the frontend breaks or never happens, it gets
correctly filed as a "follow-up," and the next sprint re-scopes the whole
problem from scratch instead of closing that follow-up.** Naming churns.
Env-flag "dormant lanes" pile up. Nobody goes back.

The single most important instruction in this document is therefore not a
design instruction: **before adding anything new, close the specific
already-open gaps listed in §2.** They are cheap (mostly wiring, not new
features) and every one of them directly undermines the "intuitive, honest
memory picture" the operator is asking for. Shipping new UI on top of them
is sprint #7.

## §0 — What the operator actually asked for (verbatim intent)

- A genuinely intuitive model selection screen. A dedicated tab is fine if
  the design calls for it, but **start in the Chat tab** and get that
  right first.
- The "always-on" local GPU model: simple selection, explicit **Load**,
  explicit **Unload**, and when loaded, the user should *understand the
  memory situation* (not just see a status dot).
- The "2nd" model, served via **aeroLLM**: the operator's own mental model
  is "you don't really load it, you stream it in" — the UI must not lie
  about this by reusing the resident-model's Load/Unload/WARM affordances
  if they aren't true for the streaming case. (§3 below tests this
  assumption against reality — it's *partly* true and partly not; read it
  before designing.)
- **Agents** must consume model tiers too — the operator's framing is
  "symbolic chain of thought" / "tiering of knowledge." (§4 tells you what
  tiering mechanism already exists and is already consumed by agents —
  don't rebuild it, extend/surface it.)
- **Nucleus** (the build pipeline in the sibling `qukaizen-nucleus` repo)
  should eventually participate in this model story too, in addition to
  the Chat tab. (§4 tells you exactly how much of that exists today: less
  than the CLAUDE.md docs imply.)

## §1 — The five prior sprints: what shipped, what's still open

Chronological. Each entry: problem → what shipped → what's still open today
(confirmed against current code, not just old sprint notes).

**1. `sprints/2026-05-03-models-admin-dashboard`** — hardware-floor
enforcement + a duplicate admin "Models" section (load/unload/set-default
endpoints) alongside the chat picker. Explicitly filed Phase-2 items
("Phase-2 callouts, do NOT expand" — ARCHITECTURE.md:1123-1133): live
default-model swap without restart, multi-GPU pinning,
`MODEL_METADATA_OVERRIDES` → hot-reloadable YAML. None ever picked up.

**2. `sprints/2026-05-10-chat-model-sync`** — made the picker read live
Ollama state instead of a stale UI-truth list. Follow-ups filed and never
done: "Router should expose a `live_model()` method" (the real fix, vs. the
narrow patch that shipped), "consolidate backend availability checks into
a BackendRegistry," "define canonical location for custom Ollama
Modelfiles" (ARCHITECTURE.md:283-285).

**3. `sprints/2026-05-30-model-hosting-reframe`** — collapsed to today's
two-tier default (`llama-ai-eng` / `ai-engineer:latest`). Left the *deep*
model identity as a literal `__TODO_DEEP_MODEL__` sentinel "for a
follow-up sprint" — **never spun up**. Also flagged a three-way legacy-name
back-compat resolver (`ai-eng`/`ai-engineer`/`llama-ai-eng`) to "drop one
release after migration" — still present today.

**4. `sprints/2026-06-14-arail-two-tier-models`** — re-confirmed sprint
3's picks (visionary caught a proposed regression and blocked it — this
one worked as designed). Filed **F8**: `backend_notice` (an "honest AirLLM
label" string) is built server-side but the template never renders it
(REVIEW.md:12-117, "documented follow-up, not a stop-ship"). **Confirmed
still broken today**: `backend_notice` is produced at
`src/arail/portal/app.py:6128-6137` and does not appear anywhere in
`chat.html`.

**5. `sprints/2026-06-14-world-model-hint`** — shipped Phase A only (a
World can *suggest* a better model via a dismissible banner; still present
today, `chat.html:2091-2102`, `src/arail/chat/__init__.py:303-344`).
**Explicitly did not build Phase B** — the Gemma-2B default-floor swap —
gated on two cross-repo items (weights handoff, license disclosure) that
were never confirmed. **This worktree's Gemma-4-26B-MoE task is very
likely the sixth attempt at that same unresolved deep-model-identity
question.**

**The throughline:** the deep/streamed-model slot has never had a closed
identity across three sprints in a row (35B threshold → 30B threshold →
`__TODO_DEEP_MODEL__` sentinel → re-confirm Qwen2.5-7B → World-hint
sidestep). Every sprint also leaves at least one cheap, load-bearing wiring
gap "documented" and unfixed. **Do not repeat this.** If phase 0 below
can't be finished, say so explicitly and stop — don't design on top of it.

## §2 — Already-open gaps to close FIRST (before any new UI work)

All confirmed live in current code (`src/arail/portal/app.py`,
`src/arail/portal/templates/chat.html`, `src/arail/chat/__init__.py`,
`src/arail/router/backends.py`). These are the actual reason "Session
Telemetry" showed dashes and every model card showed a fake "good" fit
chip when this was tested live in-browser this session.

1. **`compact.hardware` is computed and never wired to the payload the
   frontend reads.** `_local_memory_snapshot()`
   (`app.py:8145-8204`, real `psutil`/`nvidia-smi` free RAM/VRAM) is
   returned as a **top-level** `"hardware"` key (`app.py:7865`), but
   `compact_selector` (`app.py:7811-7839`) never nests it. The frontend
   reads `d.compact.hardware` (`chat.html:3765`) — always `undefined`, so
   `tele-hw`/`tele-vram` never leave `—`. **One-line fix**: nest the
   existing snapshot into `compact`.

2. **Real per-model fit verdicts exist but land in a list the rail never
   reads.** `_build_local_model_entry` / `_fit_verdict_label`
   (`app.py:8243-8307`, `8116-8123`, uses real `free_gb`) populate
   `compact.local_models.items`. The chat rail (`renderModelRail()`,
   `chat.html:3280-3324`) instead reads `State.models`, sourced from
   `d.gallery.installed` (`chat.html:3707` ← `gallery_view()` /
   `detect_installed_models()` in `src/arail/chat/__init__.py:105-217`) —
   a **different list that never carries a `fit` field**. Line 3296
   defaults every missing verdict to `'good'`. Result: every local model
   — including a 26B MoE model that should show "requires streaming" or
   at minimum a real memory-fit warning — shows a green "good" chip
   unconditionally. **This is the root cause of the ≤8B-bucket mislabel
   discovered this session**: `gemma-4-26b-a4b` (26.5B total / 13.4GB on
   disk) rendered under a "LOCAL · GPU (≤ 8B)" section header with a fake
   "good" chip, because nothing in that code path actually checks size
   against free memory. Reconcile these two lists into one, or make the
   rail read the list that has real fit data.

3. **AeroLLM "eject" is a lie.** `/api/chat/eject`
   (`app.py:6874-6880`) only does `del
   _OPTIONAL_CHAT_BACKEND_CACHE[runtime]` for `airllm`/`aerollm` — it never
   touches `AeroLLMBackend._shared` (`backends.py:1488-1506`), which is
   the actual process-wide singleton holding the multi-GB `Runtime`
   (weights + KV cache). There's even a dead `elif` branch at
   `app.py:6902-6904` that correctly admits "in-process backend cannot
   hot-eject; restart the portal" — but it's unreachable, because the
   `if` above it already intercepts `airllm`/`aerollm` and returns a false
   `"ok": true` first. **The UI shows the WARM dot clear and reports
   success while the real memory stays pinned until the whole portal
   process restarts.** This directly contradicts the operator's explicit
   ask ("ability to UNLOAD it") for the one case (streamed model) they
   called out by name. Either make eject honest (actually free the
   singleton — check if the underlying Rust runtime supports it) or make
   the UI honest (no Eject button for aeroLLM; show "frees on next portal
   restart" instead). Do not ship a button that lies either way.

4. **`backend_notice` (F8, six weeks open).** Built at `app.py:6128-6137`,
   never rendered in `chat.html`. If the design still wants an "honest
   backend label," wire it. If it's superseded by whatever this sprint
   ships, delete the dead server-side code instead of leaving a seventh
   unread field.

5. **`src/arail/chat/gallery.py` doesn't exist.** `chat.html:1810,3811`
   (the in-UI "References" panel, which is supposed to help *future
   sessions* orient) cites it as the model-scanning source. The real code
   is `gallery_view()` in `src/arail/chat/__init__.py`. Fix the pointer —
   this exact wrong pointer is why past sessions (including earlier this
   one) had to rediscover the real path by grep instead of trusting the
   UI's own documentation of itself.

6. **Load state machine is spec'd but not built.**
   `docs/maximus.plan.md` §5 defines a six-state machine
   (`unloaded → loading-prep → loading-weights → warming → resident →
   unloading → unloaded`) with real ETA math from
   `on_disk_bytes / measured_throughput_mbps`. The actual implementation
   (`_prepare_chat_model_load`, `app.py:7191-7237`) collapses this to
   `loading`/`ready`/`error` with a **hardcoded** `eta_seconds=15,
   progress=0.15`. Either the spec is right and the implementation needs
   to catch up (this sprint is a natural place to do it, since "let the
   user understand the memory situation" during load is explicitly asked
   for), or the spec is over-engineered for what's needed and should be
   trimmed to match reality. Pick one and update the doc to match — don't
   leave a third, different, undocumented behavior.

Each of these is small. Together they are the entire reason the existing
UI *looks* like it should be trustworthy (real backend code exists!) but
*isn't* (none of it reaches the screen). Fixing them is not optional
scope — it is the floor the new design has to stand on.

## §3 — Ground truth: what "load"/"unload" actually means per backend

Don't design a single mental model and force both backends into it. They
are genuinely different, confirmed at the code and research-doc level:

**Ollama-resident models** (`OllamaNativeBackend`, everything in `ollama
list`, including `gemma-4-26b-a4b`, `llama-ai-eng`, `deepseek-r1:14b`,
etc.): binary resident/not, governed by Ollama's own `keep_alive`. Load =
Ollama reads weights into GPU/RAM (real time cost, e.g. ~30s cold for a
14GB q4 model, confirmed live this session). Unload = `ollama stop` /
0 keep_alive, genuinely frees memory. **The existing Load/Unload/WARM
affordance is architecturally correct for this backend.** The gap here is
purely §2 (fit chips, memory visibility during load), not the load/unload
model itself.

**AeroLLM-backed models** (`AeroLLMBackend`): this is two different
regimes wearing one brand name, and the UI currently can't tell them
apart:

- *Models that fit in memory* (today's actual production case —
  `gpt-oss-20b-MLX-4bit` default, Qwen2.5-7B-4bit deep default): held
  fully resident in a process-wide singleton (`_shared`,
  `backends.py:1488-1506`) with a real background preload loop
  (`src/arail/portal/model_warmth.py`, gated on `metal_memory_pressure()
  < 0.60`, confirming production genuinely treats this as
  resident/preloadable). "Load" is a real, one-time, heavy event — the
  operator's "cold → WARM" mental model basically holds here. The
  *only* thing broken is Unload (§2.3).
- *True frontier layer-streaming* (the research vision — `research/aerollm/
  00-04`, a 671B model on 24GB by never holding more than one layer +
  KV cache at a time): explicitly has **no warm-up / resident concept in
  the design** — every call pays the same per-layer disk cost
  (`research/aerollm/02-batching-strategy.md:11`). This is the case the
  operator is actually describing ("you stream it in... you don't
  actually load a model"). It is real in the sibling `aerollm` repo
  (`Init → Loading → Running → ShuttingDown` lifecycle,
  `qukaizen-aerollm/CLAUDE.md:33-36`) but **not the mode ARAIL currently
  runs by default** — `AERO_MOE_SELECT` (true selective/streaming expert
  loading) is opt-in and default-off; ARAIL never sets it
  (confirmed — grep the whole ARAIL repo for `AERO_MOE_SELECT`, it isn't
  set anywhere). The model catalog's claim that gpt-oss-20b uses
  "AeroLLM's native selective expert-streaming backend"
  (`models_catalog.yaml:111-129`) **overstates current reality** — it's
  running the resident whole-layer path.

**Decision this sprint must make explicitly** (don't let it stay implicit
— that's exactly how sprint 3 left the deep model identity unresolved for
three cycles): is "2nd model" in this UI the *today* case (resident
singleton, needs an honest-but-different badge from Ollama, and a real
Unload) or the *frontier streaming* case (no load state at all, just a
"streaming" indicator, ETA-per-token instead of load progress)? Given the
catalog and production code both currently point at the resident case,
the pragmatic default is: **design for the resident case now, but make
the badge/copy say "resident (aeroLLM)" not "streaming"** so it stops
overselling a capability (`AERO_MOE_SELECT`) that isn't turned on — and
leave an explicit, scoped hook for the true-streaming case rather than
pretending today's UI already handles it.

## §4 — Ground truth: agent tiering and Nucleus (don't rebuild what exists)

**Agent tiering is real and already consumed** — this is good news, don't
rebuild it. `ModelRegistry` (`src/arail/registry/core.py`) binds five
task profiles — `fast`, `reasoning`, `long_context`, `tool_use`, `build`
(`core.py:12`) — to model entries via `ModelRegistry.bind()`
(`core.py:229-255`), exposed at `POST /api/models/bind`
(`src/arail/portal/models_api.py:37-51`). Resolution
(`ModelRegistry.resolve`, `core.py:355-400`) gates on availability and
falls back through `FALLBACK_CHAIN`, always emitting a `FallbackEvent` —
degradation is never silent. Real, live consumers: `researcher.py:173-197`
(fast + reasoning profiles), `deep_policy.py:130-146`
(`_get_fast_router`, used by every built-in agent's fast path, cached by
`config_version` so a rebind reaches agents without a portal restart),
`browser.py:165`, `_builtin_drafter.py:107`, `forge.py:306`. Buddy
consumes it transitively through `deep_policy.complete_preferring_deep()`.
SRE doesn't use an LLM at all (pure log-scanner) — not a gap, just not
applicable.

There is **no existing "symbolic chain of thought" or "knowledge tiering"
concept** anywhere in docs or code — that framing is the operator's own
mental model for where this *should* go, not something already built and
hidden. If this sprint wants to build toward it, treat it as new scope on
top of the real profile-resolution system above (extend `resolve()`/the
five profiles, don't invent a parallel mechanism), and get a visionary
pass on whether it's in scope for this sprint at all versus a named
follow-up (with an owner and a date, unlike every follow-up in §1).

**Nucleus** (`~/ProJects/qukaizen-nucleus`): today, Nucleus uses **AirLLM**
for teacher inference, not aeroLLM — the "aeroLLM once it lands HTTP
bindings" line in ARAIL's CLAUDE.md is aspirational, confirmed by grepping
Nucleus's actual source (no `aerollm`/`arail` imports anywhere in
`nucleus/`, `nucleus-prototype/`, `qkz/`). The one place aeroLLM is
imported directly is a standalone ops script
(`ansible/roles/test_bench/files/1_synthesize_aero.py:34`) that bypasses
ARAIL's registry/tiering entirely. The real, working cross-repo
integration goes the **other direction**: ARAIL drives Nucleus's pipeline
over HTTP (`src/arail/build/nucleus_client.py`, used by
`src/arail/portal/build_api.py`, `src/arail/build/world_corpus.py`) and
registers graduated Nucleus artifacts back into ARAIL's own model registry
(`POST /api/models/register-artifact`,
`src/arail/portal/models_api.py:90-143`, tags the result `"fast"`).

**What this means for scope**: "Nucleus building pipeline will also use
this" is not a small UI hookup — it's new integration work on the Nucleus
side that doesn't exist today. Scope it as an explicit, separate,
named phase (own sprint or clearly-flagged follow-up with an owner),
**not** a checkbox in this sprint's execution plan. If it lands unscoped
"in addition to" the Chat tab work, it will be the seventh unfinished
item in a growing list — exactly the pattern §1 documents.

## §5 — Suggested execution plan

Route this through the existing `/sprint` pipeline
(visionary → architect → builder → qa → ship,
per `~/ProJects/CLAUDE.md`), seeded with everything above so no phase
re-derives it. Suggested phase breakdown:

**Phase 0 — Close the ledger (builder, no architect needed).** Fix §2
items 1, 2, 5 (wiring/pointer fixes, low risk, no design decisions). Fix
or honestly disable §2 item 3 (aeroLLM eject) — this one needs a decision
first: can the singleton actually be freed, or does the UI need to stop
promising it can? Decide §2 item 4 and item 6 (fix vs. delete vs.
re-scope) rather than leaving them ambiguous. This phase alone should
make the *existing* UI stop lying, even before any redesign — ship it
first, separately, so the redesign isn't blocked on it and its value
isn't hidden inside a bigger PR.

**Phase 1 — Vision (`visionary` subagent).** Force explicit answers to:
tab vs. unified list (note: `chat-studio.spec.md` §3 currently prescribes
*no* tab split — a single list with fit chips; if this sprint wants a
dedicated tab, that's an explicit deviation from the existing spec and
the doc needs updating, not silent drift); what "2nd model" means per §3's
decision point; whether agent-tiering surface work and Nucleus
integration are in-scope for *this* sprint or explicitly deferred with an
owner. Output VISION.md.

**Phase 2 — Architecture (`architect`, design mode).** Concrete design
for: the resident-model card (Load/Unload, real memory numbers from the
now-wired `compact.hardware`, real fit chips from the now-wired
per-model data); the aeroLLM card (honest badge/copy per §3's decision,
Unload that either really works or doesn't exist); surfacing the
already-real tiering system (§4) somewhere the operator can actually see
and use it, since right now it's a global settings panel two clicks deep
that nothing in this conversation's testing ever surfaced as "here's how
agents pick models"; explicit non-goals for Nucleus (link to a follow-up
sprint stub if deferred). Output ARCHITECTURE.md, including a named test
strategy for "does this survive a portal restart / cold start / actually
freeing memory" — the exact class of bug found and fixed twice already in
this session (Ollama `num_predict`/`think` hangs, aeroLLM's fake eject).

**Phase 3 — Build (`builder`).** Implement per ARCHITECTURE.md. No scope
drift — if something in §1–§4 turns out to need more than the phase 0/2
scope, stop and surface it rather than quietly expanding (that quiet
expansion is how sprint 1's "Phase-2, do NOT expand" note happened and
then got ignored anyway).

**Phase 4 — Review (`architect`, review mode).** In addition to the
normal paranoid pass, explicitly check: does this PR leave any new
"documented follow-up, not a stop-ship" item? If yes, that item needs
either a committed date/owner in the sprint ledger or it needs to not
ship as a follow-up — fold it in or cut the feature, don't defer it into
the void the way F8, `__TODO_DEEP_MODEL__`, and the router
`live_model()` consolidation all were.

**Phase 5 — QA.** Test allocation should weight toward exactly the two
things that broke user trust historically: (a) does the memory/fit
information shown on screen match reality under real load (cold start,
warm, near-OOM), verified against `ollama ps` / actual process RSS, not
just "the UI didn't crash"; (b) does Unload actually free memory for
every model type the UI offers an Unload button for — if a button exists,
it must work, full stop.

**Phase 6 — Retro.** This is explicitly sprint #6 on this problem area.
The retro should record, in `learnings/`, specifically *why* this attempt
closed the loop where the first five didn't (or, if it also fails to
close it, exactly which part didn't land and why) — so a seventh attempt,
if ever needed, doesn't start from zero either.

## §6 — Guardrails (do not repeat these five sprints' specific mistakes)

- No new "dormant lane" behind an env flag without a committed date to
  either activate or delete it. §1 sprint 3's self-hosted GGUF ladder and
  §1 sprint 5's Phase B are still sitting there.
- No catalog/UI copy that claims a capability that isn't actually turned
  on (§3's `AERO_MOE_SELECT` overstatement). If a capability is
  opt-in-and-off, say so, don't market the opt-in path as the default.
- No button that reports success without the underlying effect happening
  (§2.3's aeroLLM eject). If it can't be made honest this sprint, remove
  the button rather than ship a lie.
- No "follow-up" filed in a REVIEW.md that isn't also either fixed this
  sprint or given an explicit owner + next sprint slot. Undated
  follow-ups are how F8 sat for six weeks.
- Don't design a brand-new mental model for aeroLLM without first reading
  §3 — the two regimes (resident-because-it-fits vs. true frontier
  streaming) are real and conflating them again is the most likely way
  this sprint quietly becomes attempt #7.
