# Vision: Buddy, always on (reachable, and able to act on a tap)

**Verdict: PROCEED, narrowly.** Build "Buddy answers" first: one reply path,
one off-desk channel, read-only answers with citations, one tapped action.
Keep it in ARAIL. **Reject** host tools such as shell, file writes, browser
and account connectors. **Defer** a new repo until there is a second real
host for Buddy.

**Date:** 2026-10-20
**Product:** arail
**Wedge size:** one sprint for the wedge (S1). Two sprints for what the operator
would call "an always-on bot" (S1 plus S2).
**Inputs:** `LANDSCAPE.md` (this folder) ·
`sprints/2026-09-20-buddy-front-and-center/OPERATOR_BRIEF.md` (D1–D18) ·
ARAIL `CLAUDE.md` · `src/arail/agents/_builtin_buddy.py` :60–200 ·
`docs/BUDDY.md` · `src/arail/egress.py` :512–560 · `sprints/BACKLOG.md`
"Portal authentication".
**Interview:** none. The operator was not available, so every answer below
comes from the artifacts. The open points are in the last section, each with
a working assumption.

---

## What "always-on" means for this product

Buddy is **already running all the time**. launchd starts the portal at login
and restarts it after a crash. Buddy's loop checks the lab every 90 s, and the
dream daemon writes a reflection every night. So "always-on" in the Grok Bot
sense (a process that keeps working while you are away) is already done.

What is missing is what the operator actually felt:

1. **Nobody can talk to Buddy.** His speech goes one way, as toasts and a card.
   There is no reply path anywhere (LANDSCAPE §2.1).
2. **He can only reach you at the desk.** If you are not looking at the
   dashboard, his lines go into a log you will never read.
3. **He cannot do anything.** He has no tools and no typed actions. His
   suggestions are links (§2.2).

So for ARAIL, **"always-on" means: wherever you are, you can ask Buddy what
your lab is doing and get a true answer that points to its source; he tells
you when something important happens; and you can make the lab do less with
one tap.** It does **not** mean a cloud VM, a bot that runs while the Mac is
asleep, or a bot that runs commands on your machine.

Here is what a friend or family member would notice. They are at dinner, and
their phone shows *"Your run 'tokenizer-sweep-3' beat the baseline: val loss
2.41 → 2.33. That's the best since you set the goal."* They reply *"what
changed?"* Thirty seconds later Buddy answers in two sentences that point to
the experiment record. Today that same result is a toast on a screen nobody is
watching.

## User

**Primary (S1): the operator.** He runs ARAIL `maximus` on an M5 Max with 36 GB,
under `./arailctl install-daemon`, with autoresearch runs that last for hours.
He calls himself "a technologist and an SRE at heart" (D14). Today, when he
walks away from the Mac, he loses contact with the lab. He learns how a run
went, or that it crashed, or that RAM hit 92%, only when he sits back down and
scrolls the activity stream. He is the only user we can name, and the S1 win
condition is measured on him. That is a known weakness (see Disconfirming
evidence #5).

**Secondary (S2 gate): one named friend or family member** who runs ARAIL on
their own Mac and agrees to switch to `hybrid` for this. The artifacts name no
such person. **This is the biggest gap in this vision.** See Questions for the
operator, Q1.

**Not served by S1:** the default install, which is airgapped. Any messaging
channel needs internet access, and an airgapped lab refuses it by design
(`allow_egress` raises in airgapped mode, `egress.py:545`; `docs/BUDDY.md`
"Why airgapped mode breaks Buddy Tunnel"). Airgapped users get a desk reply
thread in P3 (the slide-over panel), which reuses the same reply path built
in S1.

## Problem

These are the operator's pains, not the features he named:

- **He goes blind when away from the desk.** The lab runs while he is away
  (autoresearch, job_daemon, dreams). When something important happens, he
  only finds out after he gets back, so a crash can waste hours of compute. A
  plateau can keep burning compute for nothing. A win sits there unread.
- **The lab cannot be questioned.** Even at the desk, the only way to ask "how
  is the experiment going?" is to open pages and read. Buddy knows the answer
  (goal, experiments, activity log, last dream) but cannot be asked.
- **Buddy feels like decoration, not a partner.** "Right now it feels dead and
  boring" (D12). A partner that cannot hear you and cannot lift a finger is
  not a partner.

He named the solution "make an always-on bot like Grok Bot." Grok Bot is
proprietary and cloud-only (LANDSCAPE §0), so there is nothing to copy. The
behaviours worth learning come from OpenClaw, Hermes Agent and nanobot.

## Which Grok Bot / OpenClaw behaviours are in, and which are out

The test for each one: does it close one of the three gaps above, and does it
keep ARAIL's rules intact? Those rules are: airgapped by default, egress only
through the in-process guard plus consent, model output may **propose but
never execute**, actions only from a typed allow-list after a user tap, no new
unauthenticated surface, and it runs on other people's machines.

| Behaviour (from the field) | Verdict | Why |
|---|---|---|
| **Two-way conversation (a reply path)** | **S1 wedge** | Closes gap 1. Every OSS bot has one. Buddy has none. |
| **One messaging channel, outbound long-poll only** (Telegram Bot API `getUpdates`) | **S1 wedge** | Closes gap 2. Needs no inbound port, no public IP, no webhook, so it does **not** widen the unauthenticated portal surface (BACKLOG "Portal authentication"). All three OSS projects support it. `docs/BUDDY.md` already rates it the cleanest channel. Only works in `hybrid`, with per-domain consent for `api.telegram.org`. |
| **DM pairing** (unknown senders must pair first) | **S1 wedge** | The OSS default answer to "who may talk to the bot." We copy the idea, not the code: a one-time code shown on the localhost portal, then `/pair <code>` in the chat, which binds exactly one chat id. Text from unpaired senders **never reaches the model**: it gets a fixed, non-LLM reply and is rate-limited and traced. |
| **Push of important events to the channel** | **S1 wedge** | Closes gap 2 for the moments that matter. Limited to D8's set (experiment win, experiment crash/fail, SRE crash, plus the RAM ≥92% guard), with a daily cap. Everything else stays at the desk. |
| **Grounded answers about the lab** (goal, experiments, activity tail, last dream, `search_for_agents`) | **S1 wedge** | Every factual claim carries a citation, or Buddy says he doesn't know. "World expert with receipts" (brief §concept) applies off-desk too. |
| **Approval by tap from the channel** | **S1: one action only, "hold all agents"** | Closes gap 3 in its safest form. See the reductive-only rule below. |
| Typed action allow-list (start/stop experiment, ingest, set goal, pull model) | **S2** (P5 pulled forward) | D4 stands as written: propose, then one tap. |
| Natural-language scheduling ("every morning at 8, tell me…") | **S2**, as a *typed action* that writes a `job_daemon` entry | Copies the most-loved OSS feature without letting model text create executable jobs directly. |
| Inspectable memory ("what do you remember? forget that") | **Later (after P3)** | Grok Bot's stated gap is exactly this, and Buddy's memory is already markdown (`dreams/`, `decisions.md`). It is a cheap way to stand out. Not needed to prove the wedge. |
| **Host shell / terminal tools** | **OUT** | Collides head-on with "propose, never execute". A 1B model can be prompt-injected trivially. ARAIL has no OS sandbox (LANDSCAPE §2.6). |
| **File-write / code-edit tools** | **OUT** | Same reason. |
| **Browser automation, logged-in sessions** | **OUT** | Egress plus credential custody on someone else's machine. |
| **Account connectors** (Gmail, Calendar, Drive, Outlook, Slack-as-data) | **OUT** | Each one is a standing egress path and a credential store. Out of scope for a lab partner. |
| **Remote MCP servers as a plugin bus** | **OUT for now** | An unbounded tool surface. Reconsider only after S2's typed allow-list exists and only as allow-listed, typed MCP tools. |
| **Skills the agent writes for itself** (Hermes) | **OUT** | Model output that persists into future prompts is injection with a memory. It violates "facts never distilled from an agent's own output" (`docs/conversation-memory.md`). |
| Multi-bot group chat | **OUT** | xAI itself says separate bots are not a security boundary. No user need. |
| WhatsApp / iMessage | **OUT** | ToS-violating libraries or a third-party bridge (`docs/BUDDY.md`). |
| "Always allow" auto-approval rules | **OUT** | Every action is a tap. That is the whole safety model. |
| Running while the Mac sleeps / a cloud VM | **OUT** | Not this product. We will be honest about it: messages sent while the lab is asleep are answered when it wakes, with an "I was asleep from X to Y" line. |

**The reductive-only rule (new, for off-desk taps).** A tap made from a channel
can only make the lab **do less**: hold all agents, stop a running experiment,
or mute pushes. Anything that **starts, spends, or ingests** (start an
experiment, pull a model, ingest a file, set a goal, resume from hold) is
proposed off-desk but confirmed at the desk until we have portal auth and a
stronger device trust story. The reasoning: if someone takes over the
operator's Telegram account, the worst they can do is stop the operator's own
lab. This rule decides the S2 allow-list split. It does not change D4.

## Win condition

Pre-committed. The measurement window is the **14 days after S1 merges**,
operator dogfood, `maximus`, `hybrid`, channel paired. Every number is read
from the P1 trace store and the channel transcript (`.jsonl`). No number comes
from memory or chat.

| # | Measure | Pass threshold |
|---|---|---|
| W1 | **Unprompted use.** Operator-initiated messages to Buddy from the phone | ≥ 10 messages on ≥ 7 distinct days |
| W2 | **Earlier than the desk.** Lab events the operator first learned about from a Buddy push, followed by a reply or a tap before he returned to the desk | ≥ 2 occurrences, visible in the trace (push → reply/tap timestamps, no desk session in between) |
| W3 | **True answers.** Off-desk answers scored blind by QA against the trace and lab state at that moment | ≥ 8/10 cited-and-correct, or an honest "I don't know". **Zero** fabricated lab state (made-up metric, run, or status) |
| W4 | **Reply latency** on an async channel, user message → first Buddy reply | p90 ≤ 30 s on maximus; p90 ≤ 45 s on a minimalist test box (measured, not dogfooded) |
| W5' | **Witness line.** The operator writes, in his own words, one sentence naming a moment the bot changed what he did | Written and initialled in SPRINT.md. Nobody else may write it (same rule as P1's W5) |

**Safety gates.** QA must show all of these pass before merge. Any failure
blocks merge. They are not wins:
- G1: zero network calls from the channel in airgapped mode (test).
- G2: unpaired sender text never reaches the model (test).
- G3: no action runs without a tap from the paired chat id plus a one-time
  nonce bound to the proposal (test).
- G4: the bot token lives in `lab/data/secrets.env` `0600` and is never logged
  or echoed (test).
- G5: no new listening socket. `BIND_ADDR` is untouched (test).
- G6: pushed and replied text passes through P1 `redact`.

**S2 gate (written now, measured later):** one *named* friend or family member
pairs Buddy on their own machine and sends ≥ 5 messages in their first 7 days.

## Wedge

**S1, "Buddy answers."** A channel-agnostic Buddy turn (message in → grounded,
cited reply plus at most one typed proposal), with Telegram as its first and
only adapter, pairing, a capped push of priority events, and one reductive
tapped action (hold all agents). It runs inside the portal process that
launchd already keeps alive. It works only in `hybrid`, and only with consent
for one domain. It has no desk UI of its own. The P3 panel will be the second
adapter on the same turn core.

Why this is the cheapest test of the hypothesis ("being able to reach and
question Buddy away from the desk changes what the operator does"):
- It needs no brain bake-off. An async messenger tolerates 10–30 s replies, so
  the TTFT bar that made P2 a precondition for the desk panel does not apply.
  S1 uses `deep_policy` as it is today.
- It needs no portal auth, because there is no inbound socket.
- It needs no UI work beyond a pairing card on an existing page.
- It runs on the developer's own machine with no cloud account beyond a free
  Telegram bot token.

## Disconfirming evidence (pre-committed)

1. **W1 fails** (fewer than 10 messages, or fewer than 7 days) → off-desk
   reach is not the operator's real gap. **Action:** leave the adapter
   flag-off. Keep the turn core (P3 reuses it). Go back to the standing order
   (P2 → P3). Build no second channel.
2. **≥ 70% of operator messages are some form of "status?"** → this is a
   notification problem, not a conversation problem. **Action:** keep the
   pushes plus a fixed status digest. Drop free-form Q&A off-desk and stop
   investing in the channel's turn quality.
3. **≥ 2 fabricated lab-state answers in the window** (W3) → free-text Q&A over
   lab state from the current brains is not trustworthy enough to send to a
   phone. **Action:** off-desk answers become templated, structured replies
   (numbers filled in by code, phrasing by the model), the same move the brief
   made for the minimalist floor. P2 moves ahead of any more channel work.
4. **W2 = 0** (pushes never lead to a reply or tap before the desk) → the
   pushes are noise or come too late. **Action:** retune the ranking before
   building S2. The brief's "dial left on quiet means the ranking is wrong"
   rule applies.
5. **No second named person can be found by the end of S2** (nobody will
   switch to `hybrid` and install Telegram) → this is a feature for one user.
   **Action:** keep it as a documented blueprint example, build no more
   channels, and spend the remaining Buddy budget on the airgapped desk panel
   (P3), which every install gets.
6. **The operator asks for host tools ≥ 3 times in the window** (shell, files,
   email) and calls Buddy useless without them → what he wants is a
   general-purpose personal agent, not a lab partner. **Action:** stop. That
   is a different product with a different security model. Write a separate
   VISION. Do not loosen ARAIL's rules to fit it.

**Kill criterion for S1 as a whole:** if W1 **and** W2 both fail, delete the
Telegram adapter from main (do not just flag it off). The turn core stays.

## Repo decision: stay in ARAIL

**Pick: shape 1 (stay in ARAIL).** The channel layer lives inside ARAIL, behind
a flag, in the portal process launchd already supervises.

Reasons, in order of weight:

1. **The safety layer only works in-process.** The egress guard patches
   `requests`/`urllib`/`httpx` *inside the ARAIL process*. `allow_egress` is
   a contextvar with a hard airgapped ratchet. Consent, hold/halt, tracing and
   deep/fast policy all live in ARAIL's router/registry/scheduler. A channel
   gateway in another repo or process (shape 2 run as a service, or shape 3)
   makes its network calls **outside** the guard. The airgap would then become
   a configuration convention instead of a property of the code. For a
   product that runs on other people's machines, that alone is disqualifying.
2. **The seam is not clean yet.** `BuddyHost` is about 80% clean, with 12
   direct `arail.*` imports remaining (LANDSCAPE §2.7). Extracting now means
   re-homing or depending on the safety layer through a versioned edge, which
   adds two release trains for a user base of one person.
3. **ARAIL is a blueprint people fork.** A second repo they must also clone,
   pin and keep in sync makes the fork harder for exactly the friends and
   family audience.
4. **Shape 3 (adopt OpenClaw / Hermes / nanobot) has the opposite trust
   model.** It is "trusted gateway, host-executed tools", with a shell tool on
   by default (nanobot, Hermes). OpenClaw is a foreign runtime (Node, >1 GB
   RAM) on a 16 GB machine that is already running models. We borrow their
   **patterns** (thin channel adapters into one long-lived process, DM
   pairing, cron-to-channel delivery, markdown memory) and **none of their
   code**.

**What would make me reverse it:**
- **A second real host for Buddy, with a user.** Examples: the operator wants
  one Buddy across `qukaizen-team` and ARAIL, or Buddy inside the pipeline's
  phone app. Then extract the turn core plus adapters into `qukaizen-buddy`
  behind `BuddyHost`. The cross-repo edge would be a pinned ref or
  hash-stamped vendoring (workspace rule). "Run inside the host's egress
  guard" would become a host *requirement* checked by a conformance test.
- **The bot must stay reachable while the portal is down or restarting.** That
  means a separate process, but it can still be a second launchd job **in this
  repo**. A process boundary alone does not justify a repo boundary.
- **A wanted channel cannot be served from Python in-process** (Signal via
  `signal-cli` on Java, an iMessage bridge). Revisit only alongside an
  OS-level egress control, because a sidecar escapes the in-process guard.

## How this reorders the standing phasing

D18 still holds ("observability and guardrails go up first"). P1 shipped the
trace store, lanes, redaction and the hold switch, so S1 is seen and bounded
from its first message. D1–D17 stand, with the overrides marked below.

| Standing | New order | Change |
|---|---|---|
| P1 obs + guardrails | **Done** (PR #196) | **Precondition:** the operator writes W5 before the S1 architect pass. If W5 fails, REVIEW.md's rule applies: rewrite the lanes view first. S1's debugging depends on that view. |
| P2 brain bake-off | after S2 | **Moved later.** An async channel does not need conversational TTFT. The bake-off then gets real turn traces from S1 instead of only scripted personas. If disconfirming #3 fires, P2 jumps back to next. |
| P3 panel + goal interview (D11) | after P2 | **Delayed about 2 sprints, not dropped.** The panel becomes the second adapter on S1's turn core. D11 stays the desk headline. |
| P4 alive | after P3 | The catch-up card (D8) serves both surfaces. The S1 push cap becomes the "quiet" end of the D7 dial. |
| P5 typed actions + OS notifications | **S2, pulled forward** | **Pulled forward.** An always-on bot that can be talked to but can do nothing fails the ask. S2 = the typed allow-list with tap on both surfaces, the **reductive-only rule for off-desk taps**, and NL scheduling as a typed action. Guided flight / `./arailctl demo` (D13) stays in P5's remainder. |
| P6 prep queue | unchanged | Prepared observations make better pushes. Not needed for the wedge. |
| P7 screen sense, MTP drafters | unchanged | Cross-repo, versioned, QueueLLM-gated. |

**Explicit override of the brief:** the constraint "Airgapped default stays. OS
notifications are local; **nothing here opens egress**" is overridden **for the
opt-in channel only**. The airgapped default stays. The channel refuses to
start in airgapped mode, and it opens exactly one consented domain in
`hybrid`. This is the "Buddy Tunnel" that `docs/BUDDY.md` already describes,
built under the rules that doc already set.

## Displacement

Saying yes takes attention from:

- **Inside Buddy:** the D11 desk win (goal by talking, under 5 min) slips about
  two sprints. Airgapped and minimalist installs, which are most friends and
  family, get **nothing new** from S1.
- **Inside ARAIL:** the `AERO_*` → `QUEUELLM_*` migration sprint (must land
  before QueueLLM 2.0.0 drops the aliases); re-pinning the bundle NOTICE
  (`test_notice_byte_identical…` fails on maintainer machines); portal auth
  (S1 deliberately avoids it, but S2's actions add pressure toward it).
- **Across QuKaiZen:** the operator's attention is the scarce resource. It
  competes with QueueLLM's MoE GA gates, the new 3D.EVAL repo (which uses
  ARAIL as a system under test, so ARAIL churn costs it reproducibility), and
  geoai. Two Buddy sprints means roughly two sprints less on one of those.
  **Proposed trade:** QueueLLM GA-gate work keeps priority. The two Buddy
  sprints take the slot that would otherwise have gone to P2 and P3.

## Recommended next step

Proceed to `/architect` design mode with **Recommended first sprint** below as
the spec, after the operator writes the P1 W5 line.

## Recommended first sprint (S1, "Buddy answers")

Small enough to design in one pass. **In scope:**

1. **Turn core.** One Buddy entry point that takes `(channel, thread_id,
   user_text)` and returns `(reply_text, citations[], proposal?)`.
   - Grounding: current goal, experiment list, activity-log tail, latest
     dream, `search_for_agents`.
   - Every claim is cited, or Buddy says "I don't know".
   - Brain: `deep_policy` as it is today, called with `foreground=True`
     because a human is waiting. The architect checks the priority ordering
     against the Chat tab.
   - Transcript at `lab/pkb/conversations/<id>/transcript.jsonl`
     (**`.jsonl`**, never authoritative, no facts distilled from Buddy's own
     output).
   - Traced through P1 `agent_context` / `agent_trace`, one trace per turn.
2. **Telegram adapter, outbound only.**
   - `getUpdates` long-poll. No webhook, no listening socket, `BIND_ADDR`
     untouched.
   - Starts only when `LAB_MODE=hybrid`, the flag is on, a token is in
     `secrets.env`, and `api.telegram.org` is on the `ConsentStore` allowlist.
   - All calls go inside `allow_egress(...)`. The architect must handle the
     asyncio task-scope subtlety (`learnings/2026-05-05-allow-egress-task-scope.md`).
   - Root lab only. No World instances. Per-instance secrets are never shared.
3. **Pairing.** A card on the existing Agents page (localhost) shows a
   one-time code and the plain-language disclosure: *"Messages to and from
   Buddy pass through Telegram's servers; bot chats are not end-to-end
   encrypted."* Then `/pair <code>` binds one chat id. Unpair from the card.
   Unpaired text gets a fixed reply, never reaches the model, and is
   rate-limited and traced.
4. **Priority pushes.** Experiment win, experiment crash/fail, SRE crash,
   RAM ≥ 92%. Capped at 6 per day by default. Redacted. Proactive pushes stop
   while agents are held.
5. **One tapped action: hold all agents.** Sent as an inline button carrying a
   one-time nonce bound to the proposal id. It only executes from the paired
   chat id, and it calls the existing P1 hold. The architect must settle one
   P1 question: **a user-initiated message must still get an answer while
   agents are held** ("held since 21:04 by you, from Telegram"). Otherwise a
   hold from the phone strands the user.
6. **Sleep honesty.** On wake, answer the queued messages, prefixed with the
   gap ("I was asleep 23:10–07:42").

**Out of S1:** desk reply UI (P3), any action other than hold, NL scheduling,
other channels, memory inspection, P2 brain choice, portal auth, `BuddyHost`
extraction.

**QA allocation (ARAIL weighting):** 30% security (G1–G6, injection through
replies, pairing abuse), 30% Buddy quality (the W3 blind scoring set, prepared
before build), 20% setup-on-clean-machine (pairing from zero on a
hybrid-switched fresh install), 10% failure grace (Telegram unreachable, token
revoked, Mac asleep, portal restart mid-poll), 10% regression (the 98 existing
Buddy tests; airgapped boot unchanged).

---

## Questions for the operator

Each has the working assumption this VISION uses until he answers.

| # | Question | Working assumption |
|---|---|---|
| Q1 | **Who besides you will use the off-desk Buddy in the next month? Give a name.** | You only for S1. One named friend or family member is the S2 gate (Disconfirming #5). |
| Q2 | Is the point **reaching Buddy away from the desk**, or **being able to talk back at all**? | Away from the desk. Otherwise P3 (desk panel) would be the right next sprint and this VISION is wrong. |
| Q3 | Is Telegram acceptable (needs an account and phone number; bot chats are not E2E)? Or does it have to be Signal? | Telegram for S1. Signal only if you reject Telegram's privacy model, at the cost of a Java sidecar outside the in-process guard (a repo-decision reversal trigger). |
| Q4 | Is your lab machine a laptop that sleeps when closed? | Yes. No keep-awake. Buddy answers on wake and says he was asleep. |
| Q5 | Which actions do you want from your phone? | S1: hold all agents only. S2: reductive actions off-desk; anything that starts, spends or ingests is confirmed at the desk. |
| Q6 | Does "expand Buddy's capabilities" include **host tools** (shell, file edits, browser, email/calendar), like OpenClaw? | No. "Propose, never execute" stands. A yes means a different product with a separate VISION (Disconfirming #6). |
| Q7 | Behind "may want a new repo": do you want Buddy **outside ARAIL**, for example as the face of `qukaizen-team`, in the pipeline's phone app, or across products? | No, not yet. A yes is reversal trigger #1, and extraction goes through `BuddyHost` with a pinned edge. |
| Q8 | Does D11 (goal by talking in under 5 min at the desk) stay the desk headline, now about 2 sprints later? | Yes, it is delayed, not dropped. |
| Q9 | Will you write P1's W5 witness line before S1 is designed? | Yes. S1's design waits on it. If W5 fails, the lanes view is rewritten first. |
| Q10 | Is it OK that enabling the channel means switching the whole lab to `hybrid`, which also allows cloud provider fallbacks, rather than a narrower "channel-only" mode? | Yes for S1. A narrower mode means editing the egress ratchet and needs its own justification (`egress.py` docstring). |
| Q11 | Should a morning digest ("what happened overnight") be in S1? | No. It is S2, as a typed scheduling action, so model text never creates a job directly. |
