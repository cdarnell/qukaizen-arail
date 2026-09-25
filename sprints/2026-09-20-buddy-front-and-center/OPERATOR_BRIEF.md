# Operator brief: Buddy, front and center

**Date:** 2026-09-20 (amended 2026-09-21) · **Status:** pre-VISION input (operator
interview, 4 rounds) · **Branch:** `qukaizen/arail-buddy-front-and-center`, cut
from main `236504ca`. Code references below were first gathered on
`qukaizen/arail-ingress-spine` and **re-verified against main on 2026-09-21**.

This file is the artifact handoff for the design phase. The visionary /
architect / designer read THIS, not chat memory.

## The operator's words

> "Agent integration ARAIL. I feel like that's honestly what's missing. ARAIL's
> agents have multiple models to inference, the always in memory AI SLM."

> "I want buddy to be front and center with an interface to chat with. One that
> is actively chatting to you. Not waiting for you to hit enter. But telling you
> things about important stuff going on in the lab. Positive experiments or no
> experiments or no GOAL set yet. Let's work on setting a goal. And have the
> agent actually set that goal for the person."

> "An agent should always be within reach. I think about how powerful Gemini is
> in the browser… It's an AI lab for god sakes but chat is archaic feeling…
> Have an inference opening to an LLM but has to be clean. And a bonus where
> buddy the agent can read your desktop / screen."

> "Surprise me. A buddy contained with all the knowledge of your lab and becomes
> an expert on your world. Suggests, guides, entertains? or just shows the power
> of AI right there and one that does things in the background to prepare."

## What exists today (verified 2026-09-20)

| Fact | Where |
|---|---|
| Buddy is already a notice-and-speak loop: watchers + goal-anchored suggesters + daily reflection | `src/arail/agents/_builtin_buddy.py` (1575 lines) |
| His speech is one-way: activity SSE → dashboard toasts + an Agents-page card. **No Buddy chat endpoint; the user cannot reply.** | `templates/dashboard.html:393,1511`, `templates/agents.html` |
| **With no goal he goes silent** — "no goal → nothing to anchor to → stay quiet" | `_builtin_buddy.py:1349` |
| Global cooldown: one line / 300 s, floor 60 s | `_builtin_buddy.py:1276,1299` (`LAB_BUDDY_GLOBAL_COOLDOWN_SEC`) |
| Goal setting exists but is form-driven; Buddy never calls it | `src/arail/goals.py:64` `GoalStore.set_goal(parsed, source=…)` |
| A goal is structured (objective, success_metrics, constraints, sub_objectives, domain…); an LLM-free heuristic parser exists | `parser.parse_offline` (used at `portal/app.py:~1095`), `swarm_goals.compile_goal_dossier` |
| A mounted World already yields goal suggestions | `GET /api/worlds/goal-suggestions` → `wf.goal_suggestions(spec, tier)` (`portal/world_routes.py:1175`) |
| Boot rule: staging a goal must NOT auto-start research | `portal/app.py:~1105` comment |
| Two model slots shipped: resident `tier0-local` (pinned, keepwatch re-warm) + deep `tier1-aerollm` | `sprints/2026-08-11-two-slot-chat-models/SPRINT.md` |
| Agent↔model policy is a binary per-call "deep if idle/allowed, else fast"; the resident SLM has no role of its own | `src/arail/agents/deep_policy.py` |
| Background-deep gating (window / presence / profile / memory pressure) already exists and is reusable for prep work | `deep_policy._background_gate` |
| ~5 separate "how does an agent get a model" implementations | buddy/debt_advisor/consolidation → `deep_policy`; `_builtin_drafter.py:97`, `browser.py:203`, `researcher.py:165` own `_get_router`; `lab/tools/model_router.py` orphan |
| Consent + reflection machinery exists | `agents/consent.py`, `agents/dream_daemon.py` |
| Deep model is **maximus-only**; minimalist has the 1B resident (`llama-ai-eng`) alone | `deep_policy.explain` → `tier_locked` |

## Decisions the operator made

| # | Question | Decision |
|---|---|---|
| D1 | Buddy's home | Always within reach on every page. A **modernized slide-over side panel thread**, explicitly "way cooler than Gemini's side panel". Chat-bubble UX is "archaic". |
| D2 | Signature elements | **Replies are live widgets, not text** · **visible attention + thinking** (what he's watching, which brain is active) · **lab pulse header, toggleable**. *Not selected for v1:* spotlighting page elements. |
| D3 | Goal authority | **Interview → propose → one-tap confirm.** Buddy does the work; the human owns the commit. |
| D4 | Hands | **Propose any lab action, one-tap confirm** — a fixed allow-list of typed actions (start/stop experiment, ingest inbox file, navigate, pull model, set goal). |
| D5 | Voices | **Buddy leads, others can cut in** — SRE crashes and consent prompts appear as their own agent in the same thread. |
| D6 | Brain | **Resident SLM for every turn and proactive line; deep for heavy drafting**, with an honest "give me a sec". *Amended by D17: on maximus the default voice is a QueueLLM MoE; the 1B becomes the reflex brain.* |
| D7 | Chattiness | **Event-driven (no fixed cooldown) + a quiet / normal / chatty dial**, with priority ranking and per-topic dedupe. |
| D8 | While away | **One ranked catch-up card on return** (wins → problems → asks) **+ opt-in OS notification** for wins and crashes only. |
| D9 | Page awareness | Operator leaned "VLM, whatever modern agents do, simplest, here to learn". **Recommendation given and not objected to:** pluggable *senses*. v1 = **page sense** (each surface publishes a structured context contract; scraped DOM text as fallback). Modern agents read their own app's pages as text/accessibility tree; pixels are for surfaces you don't own. |
| D10 | Screen/desktop reading | **Later phase — design the seam now.** The VLM belongs here: a *screen sense*, loaded on demand, consent + visible indicator, maximus-first. |
| D11 | v1 win condition | **Fresh install → confirmed, measurable goal, purely by talking to Buddy, no forms, < ~5 min. Buddy opens the conversation himself.** Pass/fail. *Amended by D12: measured on **maximus**; minimalist must pass a reduced floor version.* |
| D12 | Tier distinction | Operator: "the minimalist model is not a recommended tier 🙂 But this could be the distinction. I feel like this model integration will bring ARAIL alive. Right now it feels dead and boring." → **The living Buddy is the maximus differentiator.** Maximus = resident voice + deep-backed preparation, World expertise, the full "alive" experience. Minimalist = an honest floor: Buddy still opens, still interviews from World templates, still sets a goal with one tap — and the brain indicator shows truthfully what upgrading unlocks (`./arailctl tier maximus`). Never a fake or a nag. |

| D13 | Demo | **One engine, two scripts.** Buddy follows a declarative "guided flight" (stops, things to point out, actions to propose). First-run onboarding = one script over the real lab; `./arailctl demo` = another over a seeded World with staged experiments. Operator: "ARAIL demos with buddy guiding, seeing what you see." |
| D14 | Visibility | Operator: "all of his and any other sub agents or agents logging and updating the admin tab. Full visibility, I'm a technologist and an SRE at heart." → **Agent console in Admin + a "why?" on every Buddy line** that opens its trace. |
| D15 | Trace depth | **Metadata always, bodies on a switch.** Every decision traced (sense fired, candidates + scores, winner, suppressed-and-why, brain + effort, tokens, TTFT, latency, queue wait, action proposed, tapped/ignored). Full prompt/response bodies only under an Admin "flight recorder" toggle — local-only, size-capped, secrets redacted. |
| D16 | Telemetry style | Operator: "not trying to create a product… I wanted **fun views of agents and what they are doing versus boring metrics**, but things like time to first token is important, what model they are using and at what effort." → No Prometheus endpoint, no OTel export, no new CLI. One internal trace store feeds visual, living views. **Plus: SLOs Buddy watches on himself**, with the SRE agent cutting into the thread on a breach. |
| D17 | Brains (amends D6) | Operator: three options — "in mem 1B for speed, or **preferably the default should be QueueLLM on a 7-8B MoE model**. A modern one if possible and like these that do speculative decoding as well." → Buddy's **default voice on maximus is a QueueLLM-served MoE**; the 1B resident is the *reflex* brain (instant lines, fallback, minimalist floor); the third option is the heavy path (large streamed model, or cloud when `LAB_MODE=hybrid`). Brain **and effort** are per-call choices, always shown in the indicator and the trace. |
| D18 | Order of work | Operator: "**observability and guard rails go up first.**" Nothing new speaks, acts, or spends compute until it can be seen and bounded. |

## The concept: Buddy is never caught unprepared

The tension: a conversational turn cannot wait on a deep model, and a 1B model
cannot invent a good measurable goal from a blank page. D12 settles which tier
carries the full experience (maximus); the mechanism below is what makes it
work on both — full on maximus, floor on minimalist.

Resolution, and the "surprise": **Buddy does his thinking before you arrive.**

- A **prep queue** runs in the background. On maximus it uses the deep model
  through the existing `deep_policy` background gate (idle window, no operator
  presence, memory pressure OK). On minimalist it uses World templates
  (`wf.goal_suggestions`), `parse_offline`, hardware fit, and KB inventory — no
  LLM required.
- What he prepares: **goal candidates** fitted to the mounted World + the
  machine; **next-experiment proposals**; an **inbox digest** ("three files
  landed, here's what's in them"); a **"what I learned about your World"** card.
- In the live conversation the resident SLM only **presents, interviews, tunes**.
  The heavy reasoning already happened. The goal card streams in as a widget;
  the user taps *Set it*.
- He never opens with "How can I help?". He opens with a specific observation he
  already worked out: *"You've got the AI World mounted and 16 GB. I drafted
  three goals that fit — this one's my pick."*
- **World expert with receipts.** Everything he asserts about the lab comes
  through `search_for_agents` + the Compiled-KB gate + mounted World terms, and
  carries a citation chip. When retrieval returns nothing he says so (see
  BACKLOG "agent must not be handed keyword-only…" honesty item).
- **Entertain, lightly, and only from your own World.** "Term of the day" from
  the mounted World at the *chatty* dial setting. No canned jokes; personality
  stays "obsessed best-friend study partner".

## Proposed shape (for the architect to challenge, not a spec)

```
senses ──► attention/ranker ──► speaker (resident SLM) ──► widget thread
  page sense (context contract + DOM fallback)   │             ▲
  lab events (activity SSE, experiments, goals)  │             │ cut-ins (SRE, consent)
  knowledge (search_for_agents, World terms)     ▼             │
  [later] screen sense (VLM, consent)        prep queue ───────┘
                                          (deep when idle / templates on minimalist)
hands: typed action allow-list ──► proposal widget ──► one tap ──► existing lab API
```

## Brains: what "QueueLLM on a 7–8B MoE with speculative decoding" means today

Verified 2026-09-20 against `qukaizen-aerollm` README and the HF hub.

- **No 7–8B-*total* MoE runs on QueueLLM today.** Shipped MoE archs: Qwen3-MoE,
  gpt-oss, Gemma 4 (`26B-A4B`, text decoder; vision tower not ported). The
  modern 8B-total MoE on the hub is LiquidAI `LFM2.5-8B-A1B` — a hybrid
  conv/attention architecture QueueLLM has not ported, under a non-Apache
  license. Treat it as a QueueLLM roadmap question, not an ARAIL default.
- **But "7–8B-class" is the wrong axis for MoE.** Decode speed tracks *active*
  params; memory is what QueueLLM exists to relieve (expert streaming). The
  supported candidates decode like a 3–4B dense model with 20–30B quality:
  `gemma-4-26b-a4b-it-4bit` (4B active), `Qwen3-30B-A3B-Instruct-2507-4bit`
  (3B active), `gpt-oss-20b-MXFP4-Q4` (3.6B active). **All three are already in
  `/Users/Shared/models/`.** Operator machine: M5 Max, 36 GB — any of them fits
  resident beside the 1B. On a 16 GB machine they expert-stream; conversational
  TTFT under streaming is **unmeasured** and is a P2 bake-off question.
- **Lean: Gemma-4-26B-A4B as Buddy's default brain**, pending the bake-off.
  QueueLLM-shipped, on disk, Google publishes an official speculative drafter
  for it, and it is natively multimodal — so the later *screen sense* (D10) can
  be the same model family rather than a third resident model (blocked on
  QueueLLM porting the vision tower). License + "Built with Gemma" disclosure
  must be verified against the Gemma 4 terms before it becomes a default
  (arail CLAUDE.md Gemma disclosure section was written for the 2B floor).
- **Speculative decoding — the "all part of one model" thing.** Google ships
  `google/gemma-4-<size>-it-assistant` checkpoints (E2B, E4B, 12B, 26B-A4B,
  31B): small **MTP (multi-token-prediction) drafters** trained for that exact
  target. Mechanically it is still draft-then-verify — the target checks K
  proposed tokens in one forward pass, output is identical to non-speculative —
  but the drafter is purpose-trained against its target (EAGLE/MTP-style
  drafters read the target's own hidden state rather than re-deriving context
  from scratch), so acceptance is far higher than pairing an unrelated small
  model. Per the MLX conversion notes it loads as a separate small model
  (`model_type: gemma4_assistant`), not tensors inside the main checkpoint.
  Contrast: `queuellm-speculative` today is classic two-independent-model
  Leviathan-2022, and its one recorded A/B got **0.178 acceptance**
  (Qwen 0.5B → 7B) — i.e. little or no lift. **Supporting `gemma4_assistant`
  drafters is a QueueLLM sprint, not an ARAIL one.** It reaches ARAIL through
  the versioned `aerollm-api` package; Buddy must not block on it.
- **Effort** is a per-call dial alongside brain: *reflex* (1B, one line, no
  retrieval) · *standard* (MoE, retrieval, widgets) · *deep* (MoE with extended
  reasoning, or the heavy path; background/prep only unless the user asks).

- **Resident reliability is already handled on main.** `_normalize_keep_alive`
  (`router/backends.py`) sends Ollama an integer `-1` for the pinned resident
  model; a bare string `"-1"` is rejected by Ollama's duration parser and made
  every resident chat turn 400 on older branches. Anything building Buddy on
  the resident model must start from main, not from a pre-fix branch.

## Observability (goes up first — D14/D15/D16/D18)

What exists: flat `{ts, source, message, level, data?}` events, 200-event ring +
rotating `activity.jsonl` (`src/arail/activity.py`); per-inference
`cost_tracker.track` with tokens/latency/entry_id/tab — but
`billing_source="agent"` for every agent, so inference cannot be attributed to
Buddy vs. Researcher; Admin has a flat Activity Log and no agent view.

What's needed, in the operator's spirit (fun, living views — not a metrics product):

- **One trace per decision**, with a trace id threaded sense → ranker → brain
  call → line/widget → user tap. This is the only new plumbing; everything
  below is a view over it. Per-agent attribution replaces the blanket
  `billing_source="agent"`.
- **Admin → Agents control room** (maximus): every agent — Buddy, SRE,
  Researcher, Librarian, Curator, Browser, prep workers, user-defined agents
  from the loader — as a living lane: state (idle / sensing / thinking /
  speaking / waiting-for-tap / deferred-and-why), current brain + effort, last
  TTFT, tokens, a swimlane timeline you can scrub. The **inference slot drawn as
  a single-lane bridge** — who holds it, who's queued, who got bumped. The
  **prep queue as a conveyor**. Model residency + memory pressure as gauges.
  `deep_policy.explain()` reason codes surfaced verbatim ("deferred_now:
  operator present").
- **"why?" on every Buddy line** → the trace, in place. This is also the demo's
  best trick: show the machinery on the spot.
- **SLOs Buddy watches on himself** (numbers set after the P2 bake-off): TTFT
  for a user turn by brain; a proactive line never delays a user turn; action
  tap → effect success; prep work never trips the memory-pressure guard. Breach
  → SRE agent cuts into the thread.
- **Flight recorder** toggle for bodies: local-only, capped, redacted; off by
  default; never captures `secrets.env` material; visibly "recording".

## Guardrails (go up with observability — D18)

- **One agent-inference gateway.** The ~5 ad-hoc model-acquisition paths
  collapse behind a single call that takes (agent id, brain, effort,
  foreground?) and enforces priority (user turn > Chat tab > proactive line >
  prep), budgets, and tracing. You cannot observe or bound five paths — so this
  moves from "someday" to first.
- **Hands are typed and tapped.** Fixed allow-list of actions, each with a
  schema; executed only after a user tap; CSRF on the endpoints; every proposal
  and outcome traced. Model output and retrieved/page/World text can *propose*
  but never *execute*.
- **Untrusted-input containment.** Page context, KB text, World terms, and later
  screen text are data. Small models are trivially injectable — so the
  containment is structural (allow-list + tap), not prompt-based.
- **Budgets + kill switch.** Per-agent token/compute budgets per hour; a single
  visible "hold all agents" switch (extends `scheduler.jobs_halted()`); dial
  position respected by every agent that speaks.
- **Demo isolation.** `./arailctl demo` runs as its own instance/data-root
  (Concurrent Worlds machinery) so seeded data never touches the real lab, and
  seeded content is labeled as demo in the UI (truth-in-UI rule from the
  2026-07-23 clean-experience sprint — no staged research passed off as real).

## Phasing (re-ordered per D18)

- **P1 — observability + guardrails, over the agents that already exist.**
  Trace store + per-agent attribution · the inference gateway · Admin Agents
  control room · kill switch + budgets · typed-action scaffold (no new actions
  yet). Ships value alone: the lab that "feels dead" becomes visibly alive
  before Buddy says a single new word.
- **P2 — brain bake-off, measured with P1's instruments** (this is the old P0,
  now with real telemetry). Candidates: 1B resident, Gemma-4-26B-A4B,
  Qwen3-30B-A3B, gpt-oss-20b. Measures: interview gate, TTFT, tok/s, memory
  beside the 1B, cold-start. Sets D17's default and the SLO numbers.
- **P3 — the wedge (D11):** panel + no-goal opening + prepared candidates +
  interview → goal-card → one tap. "why?" on every line from day one.
- **P4 — alive:** event-driven voice + dial · catch-up card · cut-ins · pulse
  header · self-SLOs wired to SRE.
- **P5 — hands + guided flight:** generalized actions · first-run script ·
  `./arailctl demo` · opt-in OS notifications.
- **P6 — prepared:** deep-backed prep queue · World-expert with citations.
- **P7 — screen sense** (needs QueueLLM vision tower) · **MTP drafters** (needs
  QueueLLM `gemma4_assistant` support). Both are cross-repo, versioned edges.

### Superseded phasing (kept for the record)

- **P0 — spike (before any sprint).** Two measurements, ~20 scripted personas,
  **authored blind** (QA's standing rule from ingress-spine), pre-registered
  gate ≥16/20 producing a goal the parser accepts with a real success metric:
  (a) **maximus path** — resident SLM runs the interview, deep drafts the goal
  card; also measure how long "give me a sec" really is on a cold vs. warm deep
  model, since that pause is the make-or-break moment of the first run;
  (b) **minimalist floor** — resident SLM *given prepared World-template
  candidates*, no deep. (a) gates the sprint; (b) decides how much of the floor
  is conversational vs. a structured widget flow. Also measure resident
  time-to-first-token with the portal open.
- **P1 — the wedge (D11).** Slide-over panel on every page · no-goal opening
  move · prepared candidates from World + hardware · interview → goal-card
  widget → one-tap → `set_goal(source="buddy")`. Research does not auto-start
  (existing boot rule); the first-experiment proposal is P3.
- **P2 — alive.** Event-driven voice + dial + dedupe/priority · catch-up card ·
  other agents cut in · attention/brain indicator · toggleable pulse header.
- **P3 — hands.** Generalized typed-action allow-list · opt-in OS notifications.
- **P4 — prepared.** Deep-backed prep queue on maximus · World-expert answers
  with citation chips · term-of-the-day.
- **P5 — screen sense.** On-demand VLM, consent, indicator. Also the moment to
  collapse the ~5 model-acquisition paths behind one agent-inference contract.

## Constraints the design must not break

- **Chat memory rules.** Buddy's thread is `lab/pkb/conversations/<id>/transcript.jsonl`
  (`.jsonl`, never `.json`); transcripts are never authoritative; facts are
  approved-only and never distilled from an agent's own output
  (`docs/conversation-memory.md`, ADR-0002).
- **Airgapped default stays.** OS notifications are local; nothing here opens egress.
- **Security — it runs on other people's machines.** A 1B model is trivially
  prompt-injectable, and page/KB/World text is untrusted data. Actions execute
  **only** from the typed allow-list **after a user tap**, never from model
  output or retrieved content alone. Action endpoints need CSRF like the
  existing proposal writes.
- **Inference contention.** Buddy shares the resident model and `inference_slot`
  with the Chat tab. Priority: user-initiated turn > Chat tab > proactive line.
  A proactive line must never make the user wait.
- **No double deep residency** (OOM) — prep work goes through the single shared
  deep router in `deep_policy`.
- **Llama disclosure** — "Built with Llama" stays in Buddy's persona prompt and
  anywhere the resident model is named in the panel.
- **Tier honesty.** The brain indicator must say truthfully when deep is
  unavailable (`deep_policy.explain` reason codes already exist for this).
- **Don't edit built-in agents as a shortcut** around the loader contract;
  the page-context contract becomes part of the surface contract for new pages.

## Kill criteria

- P0(a) fails (<16/20 on maximus with deep drafting) → the resident/deep split
  itself doesn't work; rethink D6 before building any UI.
- P0(b) fails → not a kill: the minimalist floor becomes a structured widget
  flow with the SLM only doing phrasing (consistent with D12).
- Cold deep-model "give me a sec" on first run is long enough that a new user
  leaves → the first-run goal must come from prepared candidates even on
  maximus, with deep refinement arriving after the tap.
- Resident time-to-first-token with the portal open is not conversational on a
  16 GB machine → "always within reach" is false on the target hardware.
- Dogfood users turn the dial to *quiet* and leave it there → event ranking is
  wrong, not the cooldown.

## Displaces

- Ingress Track C3/C4 stays parked (QA: do not build on the keyword router).
  The resident-SLM-as-ingress-router idea is a separate, later wedge that would
  reuse P5's agent-inference contract.
- ROADMAP "Whisper toast component (scaffold; agent integration pending)" is
  superseded by the panel.
