# Architecture: Buddy, always on — S1 "Buddy answers, asks, and stops"

**Date:** 2026-10-20
**Spec:** [VISION.md](./VISION.md) at `6a9cbc21` (§2.4 is the spec; §2.3 is the win test and G1–G12).
**Inputs read:** VISION.md, RESEARCH_NOTES.md, LANDSCAPE.md, SPRINT.md (this folder);
`../2026-09-20-buddy-front-and-center/{OPERATOR_BRIEF,ARCHITECTURE,REVIEW,TEST_REPORT,SPRINT}.md`;
the ARAIL and nanobot source listed in §2; qukaizen-buddy `CLAUDE.md` and `UPSTREAM.md`;
DDaC `data/worlds/README.md`, `dac_world/{gate,provenance,seal}.py`; 3D.EVAL pin files;
paperagents control-plane ARCHITECTURE §7.8 and §7.12; site `gate.generated.ts`;
workspace `CLAUDE.md` and `INTEGRATION_AUDIT.md` §4.

**Pins (every `file:line` below is at these commits):**

| Repo | Commit | Note |
|---|---|---|
| nanobot (in `qukaizen-buddy`) | tag `v0.3.5` = `1bb712d3` | `main` = pin + 2 docs commits (`349477f2`); `git diff v0.3.5 main` touches only `CLAUDE.md`, `UPSTREAM.md` |
| qukaizen-arail | `ca0a8f81` | this branch adds only sprint docs (`git diff --name-only ca0a8f81 HEAD` lists only `sprints/`) |
| qukaizen-ddac | `1c38512` | |
| qukaizen-3d-eval | `85f1085` | |
| qukaizen-paperagents | `4af1f83` | |

**Rule for the builder.** On code facts, this file wins over VISION.md. Where I could
not check something, it says **not verified**. The builder verifies those first.

---

## 0. Decisions in brief

| Topic | Decision | Where |
|---|---|---|
| Edge transport | Loopback HTTP on the **existing** portal port, under `/api/host/v1/*`. No new socket on either side. The edge refuses unless `BIND_ADDR` is loopback, the peer is loopback, and the process is the root lab. | §5.1 |
| Credential | `bhc1_` + 32 random bytes (base64url). ARAIL stores only its sha256. Buddy stores the plaintext in a `0600` file. One link per lab. | §5.2 |
| Contract | One JSON file, `buddy.host.v1.json`, owned by Buddy. ARAIL vendors it byte-identical with a provenance stamp. Its sha256 rides on **every** request. A mismatch is HTTP 412; Buddy closes his gate. | §5.3 |
| Patch set | Three commits, three files: P0 `nanobot/__main__.py` (+1 line), P1 `nanobot/cli/gateway_runtime.py` (+4, ~2), P2 `nanobot/channels/telegram/runtime.py` (~1). The VISION's `base.py` and username patches are replaced by a Buddy subclass plus a **seam lock**. | §5.13 |
| Proposal mechanism | **Structured output**, grammar-constrained by Ollama `format`: `propose ∈ {"none","pause_research"}`. No tools reach the model at all. A code rule also proposes for the exact imperative "pause the research run". | §6 |
| Confirms | `/approve` and `/deny` are handled at the Telegram channel boundary. They never enter nanobot's bus, session store or model context. | §5.17, §5.19 |
| Trace | `arail.agent_trace/v2` adds `record_type`, `event`, `channel`, `identity`, `cost`, `origin`, `reason`, `attrs`. The Admin lanes view gains a per-step list for Buddy. | §5.10 |
| Egress guard | A CPython **audit hook** installed before any nanobot import. It refuses DNS, connect, bind and process spawn outside the allow-list. | §5.15 |
| Mode | Handshake every 20 s. The gate closes on airgapped, no consent, contract mismatch, or 40 s without a good handshake. Worst case to stop polling: under 60 s. | §4.8 |

---

## 1. Restatement

S1 builds a small, phone-reachable QA expert called Buddy. He is his own app,
`qukaizen-buddy`: a pinned nanobot `v0.3.5` with three tiny core patches and
everything else under `buddy/`. He runs as a LaunchAgent on the operator's Mac. He
talks only over Telegram long-poll, only in DMs, only to numeric ids listed in a
desk-only file, and only while ARAIL says the lab is `hybrid` and the operator has
consented to `api.telegram.org`. ARAIL is his only brain. Every model call goes
through a new loopback edge, `buddy.host/v1`, into ARAIL's `ModelRouter`, which
holds, traces, and budgets it, and runs the resident 1B (`llama-ai-eng`, Built with
Llama). Buddy answers from one sealed DDaC World, `qa-practice`, made of the
operator's own QA rules. Code, not the model, renders every citation chip from the
sealed bytes. With no valid chip he abstains. Buddy has exactly one action,
`arail.pause_research`, and it is reductive. The model can only propose it through a
closed enum. Code mints a one-time nonce. Only a confirmer's tap carries it. ARAIL
re-checks the approval and runs it once. `/stop` holds Buddy and ARAIL together.
Every step lands in ARAIL's trace and is visible in the Admin lanes view without a
terminal. The win is one scripted seven-step run on real Telegram. The gates G1–G12
are proven separately, mostly at the socket and file level.

---

## 2. Verification log

### 2.1 nanobot at `v0.3.5` (read in the local clone at the tag)

| VISION / RESEARCH claim | At the pin | Verdict |
|---|---|---|
| Gateway always builds `AgentLoop` (`gateway_runtime.py:544-560`) | `AgentLoop.from_config(...)` at `nanobot/cli/gateway_runtime.py:482-499` | True, **lines differ** |
| Unknown DM gets a pairing code (`channels/base.py:282-310`) | `_handle_message` `base.py:255-321`; pairing branch `:274-304`; it fires even when `allowFrom` is set | True, lines differ |
| Telegram matches usernames (`telegram/runtime.py:652-669`) | `is_allowed` `telegram/runtime.py:574-591`; username match at `:591`; `_sender_id` builds `id\|username` at `:1646-1649` | True, lines differ |
| Built-in commands registered (`builtin.py:1064-1094`) | `register_builtin_commands` `nanobot/command/builtin.py:1072-1102`; includes `/__shell` (`:43`, `:1101-1102`, gated to websocket at `:1037-1050`) | True |
| Runtime context provider seam (`loop.py:673-684`) | `register_runtime_context_provider` `nanobot/agent/loop.py:643-656`; resolved per turn `:762-778` | True, lines differ |
| Custom provider from extra key (`schema.py:292-298`) | `ProvidersConfig` `extra="allow"` `nanobot/config/schema.py:247`; `convert_extra_providers` `:296-310`; forced-provider lookup `:509-525` | True |
| `before_execute_tool` cannot deny (`execution.py:157-158`) | `_execute_tool_call` `nanobot/agent/tools/execution.py:114`; hook `:165`; `tool.execute` `:172` | True, lines differ. Moot in S1: no tools |
| Registered commands dispatch during a busy turn (`loop.py:834-867`) | Priority (exact-match) at `loop.py:1297-1302`; non-priority while busy at `:1339-1347` | True, lines differ |
| `callback_data` is the label, ≤ 64 bytes (`runtime.py:2254-2270`) | `_build_keyboard` `telegram/runtime.py:2175-2183`; `_safe_callback_data` `:2185-2191` | True, lines differ |
| A tap arrives as `is_callback` (`runtime.py:2277-2310`) | `_on_callback_query` `:2198-2231`; **removes the keyboard after the first tap** `:2214-2216` | True, plus a fact the win test must respect (§8.8) |
| Slash-command regex hard-coded (`runtime.py:617`) | `TELEGRAM_BUS_SLASH_COMMAND_RE` `:539-542`, used class-qualified at `:693`; text handler excludes commands `:713` | True. **Typed `/approve` is dropped by PTB today** |
| `/stop` only cancels a turn (`builtin.py:215-233`) | `cmd_stop` `builtin.py:213-231` | True |
| Pairing has no owner role (`builtin.py:938-948`, `store.py:305-313`) | `cmd_pairing` `builtin.py:946-956` calls `handle_pairing_command` with no sender check; `pairing/store.py:300-313`; TTL 600 s `:29` | True |
| `MEMORY.md` raw in prompt (`context.py:113-116`) | `nanobot/agent/context.py:127-130` | True, lines differ |
| launchd `RunAtLoad` + `KeepAlive{SuccessfulExit:false}` (`service.py:144-145`) | `nanobot/gateway/service.py:140-148` | True |
| systemd `Restart=always` (`service.py:271`) | `service.py:271` | True |
| Dream on every 2 h (`schema.py:63-75`) | `DreamConfig.enabled=True`, `interval_h=2` `schema.py:58-59` | True |
| Root `Config` has no `extra` (`schema.py:749`) | `Config(BaseSettings)` `schema.py:422`; settings `:664-667` (`env_prefix="NANOBOT_"`); file is 707 lines | True; pydantic-settings' default `extra` is not re-verified here |
| Multiple instances via **`nanobot --home <dir>`** | **No `--home` flag exists.** Instances are separated by `--config` and `--workspace` (`docs/multiple-instances.md:3-69`; `config/loader.py:35-39`) | **False** |
| LaunchAgent with "Buddy's own label" | Label is forced to `ai.nanobot.<suffix>` (`service.py:232-236`); logs always go to `~/.nanobot/logs/` (`service.py:137-139`) | **Partly false** |
| Webhook needs public URL (`runtime.py:526-541`) | `validate_webhook_config` `:447-465`; webhook start `:747-759` | True, lines differ |
| Separate polling and send pools, stall watchdog (`:680-755`) | `start` `:603-657`; pools `:665-685`; watchdog `:802-809` | True |
| Durable outbox `delivery/outbox.sqlite3` | No `outbox` string anywhere under `nanobot/` at the pin | **Not found at the pin** |

New facts at the pin that the VISION did not have:

- The provider is **always** called with streaming: `chat_stream_with_retry` in both
  branches (`nanobot/agent/runner.py:984-996`). The edge must speak OpenAI SSE.
- Provider retries: status 408, 409, 429 and ≥ 500 retry, and so does any error text
  containing "connection", "timeout", "rate limit", "500"… (`providers/base.py:625-647`,
  `:1011-1028`). Edge error codes and messages must avoid all of them (§5.4).
- nanobot's OpenAI client has no Unix-socket option (`providers/openai_compat_provider.py:566-611`).
- Commands that go through `_dispatch_command` are **persisted** to the session store
  with `_command=True` (`loop.py:1838-1858`). A nonce typed into nanobot would sit at
  rest in `sessions.sqlite3`.
- Idle auto-compact (`idleCompactAfterMinutes`, default 15, `schema.py:147-152`)
  schedules background model calls (`agent/autocompact.py:68-91`). It must be 0.
- The WebSocket channel (WebUI) is `default_enabled` and `always_enabled`
  (`channels/websocket/manifest.py:18-19`); config alone may not keep it off
  (**not verified**). Buddy removes it at install and the bind guard refuses its port.
- Outbound text always goes through markdown→HTML (`telegram/runtime.py:1160-1193`,
  `:1230-1264`). Chip byte-equality must be proven through that conversion.
- Log lines: replies are logged with a 120-char preview at INFO (`loop.py:1727-1731`);
  inbound text at DEBUG (`telegram/runtime.py:2011`); the tapped label (with nonce) at
  DEBUG (`:2217`).
- Media is downloaded before the message reaches the bus (`telegram/runtime.py:1989-1994`).
  Transcription is on by default (`schema.py:42-50`).
- `AgentHook.finalize_content` is a pipeline with no error isolation
  (`agent/hook.py:150-151`, `:157-163`); `before_iteration` gets the mutable message
  list before each model request (`runner.py:435-455`).
- Upstream tests call `_run_gateway` (`tests/cli/test_commands.py:3503`), so P1 must
  be inert under the upstream test run (§5.13).

### 2.2 ARAIL at `ca0a8f81`

| VISION claim | In code | Verdict |
|---|---|---|
| Chokepoint traces and refuses held agent calls (`agent_context.py:363-386`) | `halt_gate` `:363-386`; fails open on error `:380-381` | True |
| `speech_gate` fails open (`:400-401`) | `:397-401` | True |
| `agent_trace` v1 has no channel/identity/cost; `record()` drops unknown keys (`:43-57`, `:123-125`) | Same | True |
| `deep_policy` foreground goes deep at once (`:121-122`) | Same | True |
| Fast path streams (`deep_policy.py:200-209`) | Same | True |
| Research pause endpoint (`app.py:4254-4257`) | Same. `researcher.pause()` sets `paused` **even when idle** (`agents/researcher.py:776-782`) | True; edge must check status first |
| `/api/experiments` typed (`app.py:4715`); archetype runs a subprocess (`mini_experiments.py:515-556`) | Path is `src/arail/research/mini_experiments.py:515-556` | True, path differs |
| Host/CSRF checks (`app.py:685-706`) | `local_trust_boundary` `:685-706`; allowlist `:656-682` | True |
| OpenAI shim registered (`app.py:1390-1395`) | `:1388-1391`; shim drops tools (`openai_compat.py:84-140`) | True |
| No login (`app.py:904-912`) | Warning text `:900-913`; `onboarding_gate` 401s all `/api/*` until a passphrase exists `:397-451` | True |
| `halt_all_jobs` (`scheduler.py:282-288`) | Same; a **corrupt `halt.json` loads as not held** (`scheduler.py:233-245`) | True, plus a fail-open fact |
| `ConsentStore.is_allowed` / `is_approved` (`consent.py:45-47`, `:114-126`) | Same; `_load` raises on corrupt JSON `:147-150` | True |
| Egress ratchet and bootstrap exemption (`egress.py:516-521`, `:590-643`, `:291`) | `allow_egress` `:512-556`; `allow_bootstrap_fetch` `:591`; hybrid pass-through `:291` | True |
| 98 Buddy tests | 11 files, 98 `def test_` (counted) | True |
| `brain.chat` "OpenAI-compatible, **with tool calls**" through `ModelRouter` | `ModelRouter.complete/stream_complete` take `prompt, system, messages` and no tools (`router/core.py:229-233`, `:306-310`). `OllamaNativeBackend` ignores `messages`, `temperature`, `top_p` and folds system into one user turn (`router/backends.py:2030-2057`, `:2094-2121`) | **False as stated.** No tools cross the edge |

New facts the VISION did not have:

- `presence_meter` stamps operator presence on every request not in
  `_PRESENCE_SKIP_PREFIXES` (`app.py:489-517`). A 20-s handshake would hold the lab
  in the `interactive` profile forever and starve background deep work
  (`deep_policy.py:77-81`). The edge path must be skipped.
- The Admin lanes view shows **only the last record per lane**
  (`agent_trace.py:373-450`; `admin.html:1745-1777`). Win step 7 cannot be judged
  there today. §5.10 adds a per-step list.
- `cost_tracker.track()` returns a record with `cloud_equivalent_usd` (labelled
  simulated) (`costs.py:355-470`), but the chokepoint discards it (`core.py:266-279`).
- One global inference semaphore with a 300-s acquire timeout
  (`portal/scheduler.py:1-38`, `:180-210`).
- The registry's `fast` profile can be bound to any entry, including a cloud one
  (`registry/core.py:330-358`, `:361-412`; `_is_cloud_entry` `:144`).
- `ARAIL_INSTANCE` marks a World-instance process (`app.py:66-90`).
- ARAIL already decides "Built with Llama" from the catalog family
  (`app.py:10591-10597`).
- ARAIL declares `jsonschema>=4.18` (`pyproject.toml:40`).

### 2.3 DDaC, 3D.EVAL, paperagents, site

- DDaC gate laws: `assert_closed_sourced_graph` `dac_world/gate.py:61-90` (VISION
  said `:59-78`). Provenance tier `dac_world/provenance.py:16-31`. Sealing:
  `write_bundle` `dac_world/seal.py:171-258`; `world_sha256 = sha256(terms.json bytes)`.
- DDaC's exporter for authored Worlds is `scripts/export-bundle.mts`
  (`:1-35`); it writes to `dist/bundles/<slug>/`, and **`dist/` is git-ignored**
  (`.gitignore:8`). So sealed bytes are not in DDaC git. Buddy vendors the exported
  bundle and pins the DDaC commit plus hashes.
- ARAIL's own seal check: `world_mount.verify_seal` `world_mount.py:458-520`.
- 3D.EVAL pins: `engine.pin.json`, `ddac.pin.json` exist; PLAN reads
  `arail.agent_trace/v1` by name (`docs/PLAN.md:345`).
- paperagents `hold` v1 and its fail matrix: ARCHITECTURE.md §7.12.4–7.12.5
  (`:1000-1032`); event line v1: §7.8 (`:799-823`).
- site stamp pattern: `lib/dac/gate.generated.ts:1-21`.

### 2.4 Where the VISION is wrong or silent (the builder follows this file)

1. **No `--home`.** Buddy's instance is `--config <home>/nanobot.json --workspace
   <home>/workspace`. LaunchAgent label is `ai.nanobot.qukaizen.buddy`. Logs land in
   `~/.nanobot/logs/nanobot-qukaizen-buddy.launchd{,.err}.log`.
2. **No tool calls through the edge.** ARAIL's router has no tool API. The proposal
   is structured output (§6). This also removes the tool-policy question from S1.
3. **The patch set changes files.** The VISION's `base.py` and username patches
   become a Buddy subclass (`BuddyTelegramChannel`) behind a seam lock. P0
   (`__main__.py`) and P2 (Telegram regex) are new. Reasons in §5.13.
4. **Typed `/approve` needs P2.** Without it PTB drops the command before nanobot
   sees it (`runtime.py:539-542`, `:713`). Win step 3 sends typed commands.
5. **Confirms do not go through nanobot commands.** nanobot would persist the nonce
   (`loop.py:1838-1858`). They are handled at the channel boundary.
6. **The second tap.** nanobot removes the keyboard after the first tap
   (`runtime.py:2214-2216`). "Taps it again" is a fast double tap or a resend of
   the same command text. QA does both (§8.8).
7. **ARAIL down vs G1.** VISION §3.3 says "stop polling after 120 s unreachable".
   G1 says "stop within 60 s of a flip". If ARAIL dies right after a flip, 120 s
   breaks G1. This file uses 40 s, so the worst case stays under 60 s. The "lab not
   reachable since HH:MM" reply works only inside that window; afterwards Buddy is
   silent, and the backlog gets the stale-message reply on recovery.
8. **Presence and lanes.** The VISION missed `presence_meter` and the one-row lanes
   view. Both are fixed in ARAIL in this sprint.
9. **Cards only to the requesting confirmer.** A proposal card is rendered only in
   reply to a confirmer's own message, in that DM. A talker who asks to pause gets a
   fixed "needs a confirmer" reply. This removes cross-chat sends. It does not
   change any win step.
10. **Background model calls.** Idle auto-compact must be off (`schema.py:147-152`).
11. **A deterministic proposer.** The exact imperative "pause the research run"
    makes a card by code with no model call. The model's `propose` field covers
    other phrasings. Both need the same human tap.

---

## 3. Assumptions

> **Addendum A (2026-10-20):** A8 is false at the pin; the turn carrier is replaced, see §A1.

Each can be wrong. Each has a check.

- **A1** The operator's ARAIL resolves profile `fast` (tab `agents`) to a local
  `ollama_native` entry serving `llama-ai-eng`. Seeded so by `registry/store.py:225-245`;
  his live `lab/data` registry is **not verified**. The edge refuses anything else (§5.6).
- **A2** His Ollama supports JSON-schema `format` on `/api/chat` (Ollama ≥ 0.5,
  from release notes; **not verified** on his Mac). The handshake reports it; if
  false, Buddy runs in template mode (§6.4).
- **A3** Llama 3.2 1B can fill a four-field JSON object under constrained decoding
  and pick a sensible slug from ≤ 20 offered terms. **Inferred, not measured.** The
  blind set measures it (§8.4).
- **A4** One Ollama request from Buddy finishes in under 20 s while an autoresearch
  run is active. **Not measured.** Perf test P-1 checks it (§8).
- **A5** The Mac and ARAIL share a clock. Approval freshness uses ±60 s.
- **A6** Telegram keeps undelivered updates for about 24 hours (Bot API docs; **not
  re-verified**). Nothing depends on the exact number.
- **A7** `${VAR}` references in nanobot config resolve inside `channels.telegram.token`
  and `providers.<custom>.apiKey` (`config/loader.py:193-246`, `:296-326`; coverage of
  those two keys **not verified**). Fallback: `buddy configure` writes the values into
  `nanobot.json` (0600) directly.
- **A8** nanobot keeps `messages[0]` (the system message) verbatim through
  `context_governor.prepare_request` (`runner.py:876-881`). **Not verified.** Build
  step B1 captures the real request and pins it as a fixture.
- **A9** No dependency in Buddy's process opens sockets from native code (bypassing
  CPython's audit events). **Not verified.** The system-level test (§8.3 L3) is the
  backstop.
- **A10** On macOS, `time.monotonic()` stops while the Mac sleeps and wall time does
  not, so the gap measures sleep. **Platform behaviour, not verified on this Mac.**
- **A11** The operator can provide three Telegram accounts for the win run: his own,
  a QA test account, and one account on no list.

**Operator questions from VISION §6, taken on their working assumption:**
Q-B (talkers = you + one QA account; confirmers = you only) · Q-C (hybrid is fine
for S1) · Q-D (pause research is the first action) · Q-E (the Mac may sleep) ·
Q-F (container before the first command/file tool) · Q-G (retire the desk loop
after S2) · Q-H (Telegram for S1). Q-A is answered (SPRINT.md:68).

**New asks for the operator** (none blocks the build; §12 lists them):
precondition (c) W5 line (SPRINT.md:70); the two extra Telegram accounts; approval
of the qa-practice cards; the budget numbers in §5.6.

---

## 4. Data flow

### 4.1 Components

```
 Phone (Telegram app) ── Telegram servers (not end-to-end encrypted)
                               ▲  HTTPS long-poll getUpdates / sendMessage
                               │  outbound only; only while the gate is open
┌──────────────────────────────┴───────────────── Buddy process ─────────────────────────────┐
│ LaunchAgent ai.nanobot.qukaizen.buddy · runs as the operator · python -m nanobot gateway  │
│ P0 buddy.boot ─ audit-hook guard (DNS/connect/bind/exec) · log redaction · preflight      │
│ BuddyTelegramChannel (subclass) ── ModeGate ── Intake:                                    │
│   identity · DM-only · rate · stale · held · /stop · /approve|/deny → ConfirmService      │
│   unknown sender → drop + count (never published)                                          │
│        │ publish_inbound (talker text only, stamped with trace_id + identity)             │
│ nanobot AgentLoop (stock) ── CommandRouter (Buddy's six) ── AgentRunner                   │
│   context provider: World terms as JSON data · turn hook: persona + turn header           │
│   finalize: parse buddy.answer/v1 → chips | abstain · after_run: proposal card            │
│        │ provider "arailEdge" (nanobot's stock OpenAI client)                              │
│ EdgeClient: handshake every 20 s · trace.record (+ spool) · hold_all · pause_research     │
└────────┬───────────────────────────────────────────────────────┬───────────────────────────┘
         │ HTTP 127.0.0.1:<PORTAL_PORT>/api/host/v1/*            │
         │ Authorization: Bearer bhc1_… · X-Buddy-Contract: sha256:…
┌────────┴──────────────── ARAIL portal (LaunchAgent io.arail.service) ─────┴───────────────┐
│ middlewares: local_trust_boundary (Host/CSRF) · onboarding_gate · presence (skipped)      │
│ arail.portal.host_edge_routes → arail.buddy_host                                          │
│   admission: root lab · loopback bind · loopback peer · credential · contract · schema    │
│   handshake  → airgap.lab_mode · ConsentStore · hold · registry fast entry · researcher   │
│   brain.chat → agent_call("buddy", channel, identity) → registry "fast" → ModelRouter    │
│                .stream_complete(response_schema) → halt_gate · trace v2 · cost → Ollama  │
│   trace.record → agent_trace.record(record_type="event")                                 │
│   hold_all     → scheduler.halt_all_jobs()                                               │
│   pause_research → approvals ledger (once) → researcher.pause()                          │
│ agent_trace v2 ring + agent_traces.jsonl → /api/admin/agent-lanes (recent) → Admin UI    │
└───────────────────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 A question (win step 1)

> **Addendum A (2026-10-20):** step 5 is superseded: the header and persona are composed when the transcript is built, not by the hook (§A1); Intake gains rules (§A4, §A7).

1. Phone → Telegram → PTB `getUpdates` (gate open) → `_on_message` → ordered queue
   → `_process_message_update` (`telegram/runtime.py:1956-2049`).
2. `is_allowed` (Buddy override): numeric id ∈ talkers. Media is not downloaded
   (override). Metadata gains `date`.
3. `_handle_message` (Buddy override) → Intake (§5.17): DM, rate, stale, held,
   lab state, budget. Pass → metadata gets `buddy_trace_id`, `buddy_identity`,
   `buddy_received_mono` → `super()._handle_message` → `bus.publish_inbound`.
   Trace `inbound_accepted` (async, via `trace.record`).
4. `AgentLoop.run` → `_dispatch` → `_process_message` → build: the World context
   provider returns one `RuntimeContextBlock` (JSON lines, brackets escaped).
5. Runner iteration 0 → Buddy `before_iteration` hook replaces `messages[0]` with
   the persona prompt, prefixed by the turn header line (trace id, channel,
   identity, `buddy.answer/v1` schema with the World's slug enum).
6. Provider `arailEdge` → `POST /api/host/v1/brain/chat/completions` (SSE).
7. Edge admission → hold check (fail closed) → mode check → brain check (local,
   `ollama_native`) → budget → inference slot (8 s) → `agent_call("buddy",
   brain="reflex", foreground=True, trace_id, channel, identity, origin="edge")`
   → `ModelRouter.stream_complete(prompt, system, max_tokens≤400,
   response_schema)` → Ollama `/api/chat` with `format`.
8. The chokepoint writes one v2 `model_call` record: model, backend, brain,
   `ttft_ms`/`ttft_status`, tokens, `cost`, channel, identity.
9. Edge streams OpenAI chunks back. nanobot assembles the response.
10. `finalize_content` (Buddy hook): validate JSON → keep only slugs in the pinned
    World → render answer (≤ 3 sentences, links stripped) + chips from sealed bytes
    → or the fixed abstain text. Trace `citation_check`.
11. nanobot publishes the outbound. `BuddyTelegramChannel.send` checks the trigger
    and gate → sends → trace `reply_sent` with `e2e_ms`.

### 4.3 Proposal → card → confirm → pause (win steps 2–4)

> **Addendum A (2026-10-20):** a lost pause answer is resent once automatically, and the code is never printed (§A5).

**Rule path (the scripted step 2).** A confirmer's whole message matches
`^(please\s+)?(pause|hold)\s+(the\s+)?(auto)?research(\s+run)?[.!]?$` (case-insensitive).
Intake → `ProposalService.create(action="pause_research", by="rule")` → mint id
(8 hex) + nonce (16 hex) → store `sha256(nonce)` → render card (text + two buttons
`/approve <id> <nonce>`, `/deny <id> <nonce>`) → `self.send(...)` in the same DM.
No model call. Trace `proposal_created`.

**Model path (other phrasings).** Step 4.2 runs. The JSON says
`propose: "pause_research"`. In `finalize_content` the hook stores the intent. In
`after_run` it checks the requester is a confirmer, then the same
`ProposalService.create(by="model")` → `bus.publish_outbound(card)`. A talker gets
the fixed "needs a confirmer" line; trace `proposal_suppressed`.

**Confirm.**
1. Tap → `_on_callback_query` → `query.answer()`, keyboard removed
   (`runtime.py:2213-2216`) → `_handle_message(content="/approve <id> <nonce>",
   is_callback=True)`. Or typed → P2 regex → `_forward_command` → `_handle_message`.
2. Intake sees `/approve` → `ConfirmService.handle` (never published to the bus).
   Checks, in order: sender is a confirmer → chat is that sender's DM → proposal
   exists, is open, not expired, belongs to this confirmer → `bad_attempts < 3` →
   `compare_digest(sha256(nonce), stored)` → Buddy hold off → cached ARAIL hold off.
   A failure replies with a fixed line and traces `confirm_attempt` with a reason.
   A non-confirmer never touches the nonce or the attempt counter.
3. Pass → write-ahead: state `confirmed` + approval record, fsync → `POST
   /api/host/v1/pause_research` (5 s timeout).
4. Edge: admission → ledger lookup by `approval_id` (hit → stored outcome) → hold →
   freshness → role → insert row `executing` → `researcher.status` → `pause()` only
   if `running` → row `done` → own trace record (`event="action_executed"`,
   `origin="edge"`).
5. Buddy: state `executed` → fixed reply "Research paused. Resume it at the desk."
   → trace `confirm_attempt accepted` + `action_result`.
6. A second tap or resend: state `executed` → "Already done." No edge call. If the
   state is still `confirmed` (crash between 3 and 5), Buddy resends the same record;
   the edge answers from the ledger.

### 4.4 `/stop` (win step 5)

> **Addendum A (2026-10-20):** `/stop` also passes a closed gate, and a late stop names its time (§A4 item 1).

1. Any talker sends `/stop` → P2 regex → Intake.
2. Intake writes Buddy's hold file (`on:true`, atomic) **before** anything else.
3. Intake publishes `/stop` (metadata stamped) → `AgentLoop.run` sees a priority
   command (`loop.py:1297-1302`) → Buddy's `/stop` handler:
   nanobot `cmd_stop` cancels this session's turns (`builtin.py:213-231`) →
   `EdgeClient.hold_all` (3 s) → `scheduler.halt_all_jobs()` → fixed reply.
4. Reply text: "Held. Buddy stops answering and the lab's agents are on hold.
   Resume both at the desk." If `hold_all` fails: "Buddy held. Lab not reachable,
   so lab agents are not held."
5. Next question → Intake sees the hold file → fixed held reply. No bus, no model
   call. Messages already queued before the stop reach the edge, which refuses
   them (`refused_held`); `finalize_content` maps the error to the held reply.

### 4.5 Unknown sender (win step 6)

`_process_message_update` → `is_allowed` false → `_send_pairing_code_if_private`
(Buddy override) → `UnknownAggregator.add(salted hash)` → return. No reply, no
download, no log of content, no bus. The aggregator flushes at most one
`unknown_dropped` record per 60 s with `count` and `distinct`. Same path for
`/start`, `/help`, commands and callbacks from unknown ids.

### 4.6 Trace to the lanes view (win step 7)

Buddy events → `trace.record` → `agent_trace.record(record_type="event")`.
Model calls → chokepoint record. Edge actions → edge record. All share the turn's
`trace_id`. `lanes_snapshot()` adds `recent` (last 50 records with a channel) to
the `buddy` lane. `admin.html` renders them as "Buddy on the phone". The "why?"
drill-in returns `related` records with the same `trace_id`.

### 4.7 Startup and the gate

```
launchd → python -m nanobot gateway --foreground --port 18797 --workspace <home>/workspace --config <home>/nanobot.json
  P0 buddy.boot: guard (closed) → redaction → secrets → preflight S1–S12 (§5.14)
       fail → fixed message, exit 0 (launchd does not respawn on exit 0)
  nanobot CLI → _run_gateway → AgentLoop(hooks + Buddy turn hooks)       [P1]
  → ChannelManager → buddy install R1–R7 (§5.16)                          [P1]
       fail → fixed message, exit 0
  → run(): channels.start_all → BuddyTelegramChannel.start → ModeGate loop:
       handshake ok ∧ mode hybrid ∧ consent ∧ contract ok → guard opens api.telegram.org
       → super().start() (PTB polling, drop_pending_updates=False)
```

### 4.8 Mode flip to airgapped (G1)

1. The operator flips the lab to airgapped (ARAIL rewrites `LAB_MODE`; `lab_mode()`
   reads the env live, `airgap.py:59-69`).
2. Buddy's next handshake (≤ 20 s) returns `mode: airgapped`.
3. ModeGate closes: guard refuses DNS and connects to `api.telegram.org` at once →
   `super().stop()` tears down PTB and closes its pools (`runtime.py:836-858`)
   → outbound suppressed → trace `gate_change`.
4. Bound: 20 s detection + ≤ 15 s teardown (PTB finishes its current long-poll)
   < 60 s. If ARAIL is unreachable instead: two failed handshakes (40 s) close the
   gate; same teardown; < 60 s from the last good handshake.
5. In-flight turns: their replies are suppressed (gate closed). A `brain.chat` with
   `channel: telegram` is also refused by the edge in airgapped mode (`mode_airgapped`).

### 4.9 ARAIL down

Handshake fails → `lab_unreachable_since` is set. For ≤ 40 s Intake answers "The
lab is not reachable since HH:MM." with no model call. Trace records spool to
`<home>/state/trace-spool.jsonl` (≤ 1000 lines, drop-oldest, drop counter). Then
the gate closes (no polling). When the handshake succeeds again, the gate opens,
the spool flushes, and backlog messages older than 10 minutes get "I could not
reach the lab from HH:MM to HH:MM. Ask again if it still matters."

### 4.10 Sleep and wake

The gate task compares wall-clock and monotonic deltas every tick. A gap > 60 s is
recorded as a sleep interval. On wake: guard closes for Telegram until a fresh
handshake succeeds (≤ 3 s) → reopen. Messages whose Telegram `date` is older than
600 s get "I was asleep from 23:10 to 07:42. Ask again if it still matters." (local
time, no model call, trace `inbound_refused stale`). Card taps from before the
sleep hit `expired`.

---

## 5. Interface contracts

### 5.1 Edge transport: loopback HTTP on the portal port

**Decision.** The five methods are routes on the existing FastAPI app, under
`/api/host/v1/`, served on ARAIL's existing `BIND_ADDR:PORTAL_PORT`.

**Why not a Unix socket.** nanobot's OpenAI client cannot use one
(`openai_compat_provider.py:566-611`), so `brain.chat` would need a core patch or a
second transport. A Unix socket's `0600` gives a same-uid boundary. The credential
file is also `0600` and same-uid, so a socket adds no protection against the
processes that matter (anything running as the operator can read both).

**Why not a second TCP port.** A new listener means port allocation, a new row in
`arailctl status`, and a second uvicorn server for no extra safety.

**What protects the routes.**

| Threat | Control |
|---|---|
| LAN client | The edge refuses with 403 `bind_not_loopback` unless `config.bind_is_loopback()` (`config.py:46-55`), and with 403 `peer_not_loopback` unless `request.client.host ∈ {127.0.0.1, ::1}`. |
| Browser (DNS rebinding, cross-site POST) | Existing `local_trust_boundary` (`app.py:685-706`). Browsers cannot add `Authorization` cross-origin without a CORS preflight, which the portal never grants. |
| Other local processes as another user | They cannot read either credential file (`0600`). |
| A World instance | 403 `root_lab_only` when `ARAIL_INSTANCE` is set (`app.py:66-90`). |
| Before onboarding | `onboarding_gate` returns 401 `lab_not_onboarded` (`app.py:397-451`). Buddy maps it to a fixed message. |

**Middleware notes.** `/api/host/v1/` is added to `_PRESENCE_SKIP_PREFIXES`
(`app.py:489-498`): phone traffic is not desk presence. Handshake, trace, hold and
pause are added to `FAST_PATH_PREFIXES` (`portal/scheduler.py:60-75`; metrics only).
`/api/host/v1/brain/` is not (it is long-lived).

### 5.2 Credential

- **Format:** `bhc1_` + base64url(32 bytes from `secrets.token_bytes`), no padding;
  48 characters. The prefix makes leak greps and secret scanners exact.
- **Minted by:** `./arailctl buddy link` (§5.12), at the desk, root lab only.
- **ARAIL stores:** `lab/data/buddy/link.json` (0600, `O_NOFOLLOW`):
  `{"schema":"arail.buddy-link/v1","credential_sha256":"<64 hex>","credential_id":"<8 hex>","linked_at":"…Z","buddy_home":"<path>","edge_base_url":"http://127.0.0.1:<port>/api/host/v1","contract_sha256":"<64 hex>","consent_request_id":"<id>"}`.
  No plaintext credential on ARAIL's disk.
- **Buddy stores:** `BUDDY_EDGE_CREDENTIAL=<cred>` in `<home>/secrets.env` (0600),
  written by the same link command; plus `<home>/link.json` (0600) with the edge URL,
  `credential_id`, ARAIL's `contract_sha256`, and `linked_at`.
- **Check:** `hmac.compare_digest(sha256(presented), stored)`. `link.json` is
  re-read when its mtime changes, so rotate and unlink act at once.
- **Shown anywhere:** only `credential_id` (first 8 hex of the sha256).
- **Rotate:** `link --rotate` replaces the hash and Buddy's line. **Unlink:** deletes
  ARAIL's record (every call → 401 `not_linked`) and revokes the consent record.

### 5.3 Contract file, sha256 exchange, vendoring, conformance

- **Owner:** Buddy. Path `buddy/src/buddy/contracts/buddy.host.v1.json`. One JSON
  document: transport, the five method schemas (§5.5–§5.9), the turn header, the
  trace event allowlist, error codes, and limits. UTF-8, LF, trailing newline.
- **Contract sha256:** sha256 of those exact bytes.
- **Exchange:** every request carries `X-Buddy-Contract: sha256:<hex>`. nanobot sends
  it through `providers.arailEdge.extraHeaders` (`schema.py:204`); the EdgeClient
  sets it itself. `handshake` also carries it in the body and returns ARAIL's.
- **Fail closed:** missing → 412 `contract_missing`; different → 412
  `contract_mismatch` with ARAIL's sha in `error.lab_contract_sha256`. Buddy closes
  the gate, logs the fixed G10 message, and retries the handshake every 60 s. A
  mid-run ARAIL upgrade is caught on the next call, not only at startup.
- **Vendoring in ARAIL** (pattern: `qukaizen-site/lib/dac/gate.generated.ts:1-21`):
  `src/arail/buddy_host/contract/buddy.host.v1.json` (byte-identical),
  `contract/conformance/*.json` (byte-identical), and `contract/PROVENANCE.json`:
  ```json
  {"schema":"arail.vendor-stamp/v1","source_repo":"cdarnell/qukaizen-buddy",
   "source_commit":"<40 hex>","source_dir":"buddy/src/buddy/contracts",
   "files":{"buddy.host.v1.json":"sha256:<hex>","conformance/<name>.json":"sha256:<hex>"},
   "tree_digest":"sha256:<hex of sorted 'path sha' lines>","synced_on":"YYYY-MM-DD",
   "sync":"python scripts/sync-buddy-contract.py --from \"$QKZ_BUDDY_PATH\"",
   "check":"python scripts/sync-buddy-contract.py --check"}
  ```
  `--check` is sibling-free: it re-hashes the vendored files against the stamp.
  When `QKZ_BUDDY_PATH` (default `../qukaizen-buddy`) exists, it also compares bytes
  with the source and reports a source-commit drift. A pytest runs the sibling-free
  check. ARAIL computes `CONTRACT_SHA256` from the vendored bytes at import and
  asserts it equals the stamp.
- **Conformance suite:** `conformance/NN-<name>.json`, one case per file:
  `{"name","method","setup":{lab_mode,held,hold_readable,linked,research_status,brain,budget_used},"request":{"headers":{},"body":{}},"expect":{"status":int,"error_code":str|null,"body_subset":{}}}`.
  Buddy runs every case against its `FakeHost` (client tests). ARAIL runs every case
  against the real routes with a fake brain (`TestClient`). At least: one ok and one
  refusal per method, every error code once, the captured nanobot request
  (`brain-chat-nanobot-request.json`, build step B1), and a check that no error
  message contains a nanobot retry marker (`providers/base.py:629-645`).

### 5.4 Common request rules and error codes

- `Content-Type: application/json`; bodies ≤ 256 KiB (`brain.chat`) or 16 KiB (others).
- Admission order for every route: root lab → loopback bind → loopback peer →
  credential → contract header → JSON Schema (`jsonschema`, `pyproject.toml:40`).
- Error body (OpenAI-compatible so nanobot's client parses it):
  `{"error":{"type":"buddy_host_error","code":"<code>","message":"<fixed text>"}}`.
- Messages are fixed strings. None contains "connection", "timeout", "timed out",
  "rate limit", "overloaded", "server error", "quota", or a 3-digit 5xx number.
- No 408, 409, 429, or ≥ 500 except `internal` (500): nanobot retries those
  (`providers/base.py:646`, `:1018-1022`).

| HTTP | `code` | Meaning |
|---|---|---|
| 401 | `credential_missing`, `credential_invalid`, `not_linked` | Auth |
| 403 | `root_lab_only`, `bind_not_loopback`, `peer_not_loopback` | Placement |
| 403 | `held`, `hold_unreadable`, `mode_airgapped`, `budget_exhausted`, `identity_throttled`, `role_not_confirmer`, `stale_approval` | Policy refusal |
| 412 | `contract_missing`, `contract_mismatch` | Contract |
| 422 | `schema_invalid`, `turn_header_invalid`, `tools_not_allowed`, `content_not_allowed`, `prompt_too_large`, `approval_conflict` | Bad request |
| 424 | `brain_not_local`, `brain_unavailable`, `brain_busy`, `structured_output_unsupported` | Brain |
| 500 | `internal` | Bug. Logged with class name only. |

### 5.5 `handshake`

`POST /api/host/v1/handshake`. Read-only. Never mutates lab state.

Request:
```json
{"type":"object","additionalProperties":false,"required":["v","contract_sha256","buddy","world"],
 "properties":{
  "v":{"const":1},
  "contract_sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"},
  "buddy":{"type":"object","additionalProperties":false,
    "required":["version","nanobot_pin","instance_id","started_at"],
    "properties":{"version":{"type":"string","maxLength":40},
      "nanobot_pin":{"type":"string","maxLength":80},
      "instance_id":{"type":"string","pattern":"^[0-9a-f]{16}$"},
      "started_at":{"type":"string","format":"date-time"}}},
  "world":{"type":"object","additionalProperties":false,"required":["slug","world_sha256"],
    "properties":{"slug":{"type":"string","pattern":"^[a-z0-9][a-z0-9-]{0,63}$"},
      "world_sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"}}}}}
```

Response 200:
```json
{"type":"object","additionalProperties":false,
 "required":["v","contract_sha256","server_time","lab","consent","hold","brain","budget","research","poll_after_s"],
 "properties":{
  "v":{"const":1},
  "contract_sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"},
  "server_time":{"type":"string","format":"date-time"},
  "lab":{"type":"object","additionalProperties":false,"required":["mode","tier","root"],
    "properties":{"mode":{"enum":["airgapped","hybrid"]},
      "tier":{"enum":["minimalist","maximus"]},"root":{"const":true}}},
  "consent":{"type":"object","additionalProperties":false,"required":["api.telegram.org"],
    "properties":{"api.telegram.org":{"type":"boolean"}}},
  "hold":{"type":"object","additionalProperties":false,"required":["held","readable","changed_at"],
    "properties":{"held":{"type":"boolean"},"readable":{"type":"boolean"},
      "changed_at":{"type":["string","null"]}}},
  "brain":{"type":"object","additionalProperties":false,
    "required":["available","local","model_id","backend","structured_output","attribution","reason"],
    "properties":{"available":{"type":"boolean"},"local":{"type":"boolean"},
      "model_id":{"type":["string","null"],"maxLength":128},
      "backend":{"type":["string","null"],"maxLength":40},
      "structured_output":{"type":"boolean"},
      "attribution":{"enum":["Built with Llama",null]},
      "reason":{"type":["string","null"],"pattern":"^[a-z_]{0,40}$"}}},
  "budget":{"type":"object","additionalProperties":false,
    "required":["day","turns_used","turns_cap","tokens_used","tokens_cap"],
    "properties":{"day":{"type":"string","format":"date"},
      "turns_used":{"type":"integer"},"turns_cap":{"type":"integer"},
      "tokens_used":{"type":"integer"},"tokens_cap":{"type":"integer"}}},
  "research":{"type":"object","additionalProperties":false,"required":["status"],
    "properties":{"status":{"enum":["idle","running","paused","completed","error","unknown"]}}},
  "poll_after_s":{"type":"integer","minimum":5,"maximum":60}}}
```

Sources: `mode` = `airgap.lab_mode()` (`airgap.py:59-69`, fail-closed). `tier` =
`tier.get_current_tier()`. `consent` = `ConsentStore().is_allowed("https://api.telegram.org/")`
(`consent.py:45-47`); any exception → `false`. `hold.held` = `scheduler.jobs_halted()`;
`hold.readable` = false on any exception or when `scheduler.halt_load_failed()` (new,
§5.11). `brain` = the resolved `fast` entry (§5.6 rule) plus a cached Ollama
`/api/version` probe (≥ 0.5 → `structured_output`); `attribution` from the catalog
family, same rule as `app.py:10591-10597`. `research.status` = `researcher.status`
(`researcher.py:739-741`). The first handshake from a new `instance_id` writes one
`edge_link` event record; later handshakes write nothing.

**Buddy's gate opens iff** 200, contract equal, `lab.mode == "hybrid"`,
`consent["api.telegram.org"]`, and `world.world_sha256` equals Buddy's pin. Hold
does not close the gate: Buddy must still say "held".

### 5.6 `brain.chat`

> **Addendum A (2026-10-20):** the carrier rules are tightened (§A1.4); `length` is never reported and the budget is re-checked after the slot (§A2).

`POST /api/host/v1/brain/chat/completions` and `GET /api/host/v1/brain/models`
(returns one model, `buddy-fast`). nanobot's provider uses
`apiBase = http://127.0.0.1:<port>/api/host/v1/brain`.

Request (OpenAI subset):
```json
{"type":"object","required":["model","messages"],
 "properties":{
  "model":{"const":"buddy-fast"},
  "messages":{"type":"array","minItems":2,"maxItems":40,"items":{"$ref":"#/$defs/msg"}},
  "stream":{"type":"boolean"},
  "stream_options":{"type":"object"},
  "max_tokens":{"type":"integer","minimum":1,"maximum":400},
  "max_completion_tokens":{"type":"integer","minimum":1,"maximum":400},
  "temperature":{"type":"number"},
  "top_p":{"type":"number"},
  "tools":{"type":"array","maxItems":0},
  "tool_choice":{"enum":["none","auto"]}},
 "additionalProperties":false,
 "$defs":{"msg":{"type":"object","additionalProperties":false,"required":["role","content"],
   "properties":{"role":{"enum":["system","user","assistant"]},
     "content":{"oneOf":[{"type":"string","maxLength":16000},
       {"type":"array","maxItems":8,"items":{"type":"object","additionalProperties":false,
        "required":["type","text"],"properties":{"type":{"const":"text"},
        "text":{"type":"string","maxLength":16000}}}}]}}}}}
```
`additionalProperties:false` is final only after build step B1 captures nanobot's
real request. Every extra field nanobot sends is then listed here with a rule
(validate or ignore). `temperature` and `top_p` are accepted and **ignored**:
ARAIL's Ollama backend does not forward them (`backends.py:2030-2057`); the edge
says so in its docstring.

**Turn header.** The first line of `messages[0].content` (role `system`) must be:
`BUDDY-TURN/1 ` + one-line JSON:
```json
{"type":"object","additionalProperties":false,
 "required":["trace_id","channel","identity","response_schema"],
 "properties":{
  "trace_id":{"type":"string","pattern":"^[0-9a-f]{16}$"},
  "channel":{"enum":["telegram"]},
  "identity":{"type":"object","additionalProperties":false,"required":["pseudonym","role"],
    "properties":{"pseudonym":{"type":"string","pattern":"^p_[0-9a-f]{10}$"},
      "role":{"enum":["confirmer","talker"]}}},
  "response_schema":{"type":["object","null"]}}}
```
`response_schema` serialised ≤ 4 KiB, `type: object`, no `$ref` to remote URLs.
The edge strips the header line before prompting. Why a header: nanobot's provider
has no per-request metadata channel (its headers and `extra_body` are fixed per
provider, `openai_compat_provider.py:517-555`). Buddy's hook rewrites `messages[0]`
on every iteration, so user text can never be the first line of `messages[0]`.

**Edge steps (in order).** Each refusal after step 5 writes exactly one
`record_type="model_call"` trace record with `outcome="refused_<code>"`, channel and
identity. Refusals before step 5 have no trusted identity; they write one
`edge_refused` event with the code only.

1. Admission (§5.4). 2. Schema. 3. `tools` empty; only text parts. 4. Total
characters ≤ 24,000 (`prompt_too_large`). 5. Turn header valid. 6. Hold:
`jobs_halted()` raises or `halt_load_failed()` → `hold_unreadable`; held → `held`.
7. Mode: `channel == "telegram"` requires `lab_mode() == "hybrid"` → else
`mode_airgapped`. 8. Brain: `get_registry().resolve("fast", tab="agents")`; refuse
`brain_not_local` if the entry is cloud (`registry/core.py:144`, made public as
`is_cloud_entry`), its endpoint host is not loopback, or `router.backend_name !=
"ollama_native"` (`registry/binding.py:130-136`); `brain_unavailable` if no entry or
router; `structured_output_unsupported` if a schema is given and the Ollama probe
says no. 9. Budget (below). 10. `inference_slot("buddy-edge", acquire_timeout_s=8)`
→ else `brain_busy`. 11. `agent_context.agent_call("buddy", brain="reflex",
foreground=True, trace_id=header.trace_id, channel=..., identity=..., origin="edge")`.
12. `router.stream_complete(prompt, max_tokens, system=system,
response_schema=header.response_schema)` in a worker thread with the context copied
(`contextvars.copy_context().run`; P1's F3 lesson). The chokepoint writes the
record (halt gate, TTFT, tokens, cost).

**Prompt flattening** (ARAIL's Ollama backend sends one user message,
`backends.py:2041-2045`): `system` = header-stripped `messages[0]`. `prompt` =
for each later message `"### " + role + "\n" + content + "\n\n"`. The last message
must be `user`. Deterministic; pinned by a conformance case.

**Response.** `stream:true`: OpenAI `chat.completion.chunk` SSE — a role chunk,
content deltas, a final chunk with `finish_reason` (`stop` | `length`) and `usage`
(`prompt_tokens`, `completion_tokens`, `total_tokens`), then `data: [DONE]`
(pattern: `openai_compat.py:319-405`). `stream:false`: one `chat.completion`.
Failures after the first byte: one error chunk
`{"error":{"code":"brain_failed",...}}` then `[DONE]`.

**Budget (ARAIL's first enforced agent budget).** Per local day for agent `buddy`:
**300 turns** and **600,000 tokens** (in + out). Rationale: the per-identity limit
is 200/day (§5.17) and two talkers exist; a turn is about 2,000 tokens (persona
~400, World block ~1,500, history ≤ 4 turns, answer ≤ 400). The caps protect lab
memory and compute, not money. A turn counts at admission; tokens count at
completion. State: `lab/data/buddy/budget.json` (0600), reset at local midnight.
Backstop per pseudonym: 20 admitted calls per minute (`identity_throttled`).

### 5.7 `trace.record`

`POST /api/host/v1/trace/record`. Body `{"v":1,"records":[1..20 records]}`.
All-or-nothing: any invalid record → 422 `schema_invalid` with
`error.record_index`. Response 202 `{"accepted":n}`.

Record:
```json
{"type":"object","additionalProperties":false,
 "required":["event","trace_id","client_ts","channel","identity","outcome","reason","attrs"],
 "properties":{
  "event":{"enum":["startup","gate_change","inbound_accepted","inbound_refused","unknown_dropped",
    "reply_sent","citation_check","proposal_created","proposal_suppressed","confirm_attempt",
    "action_result","deny","stop","outbound_suppressed","self_check_failed","config_changed"]},
  "trace_id":{"type":"string","pattern":"^[0-9a-f]{16}$"},
  "client_ts":{"type":"string","format":"date-time"},
  "channel":{"enum":["telegram",null]},
  "identity":{"oneOf":[{"type":"null"},{"type":"object","additionalProperties":false,
    "required":["pseudonym","role"],"properties":{
      "pseudonym":{"type":["string","null"],"pattern":"^p_[0-9a-f]{10}$"},
      "role":{"enum":["confirmer","talker","unknown","system"]}}}]},
  "outcome":{"type":["string","null"],"pattern":"^[a-z_]{0,32}$"},
  "reason":{"type":["string","null"],"pattern":"^[a-z0-9_]{0,40}$"},
  "attrs":{"type":"object","maxProperties":8}}}
```
Per-event allowlists live in the contract (`trace_events`). The edge rejects any
other `reason` or `attrs` key. Attr values are integers, booleans, or strings
≤ 64 chars matching `^[A-Za-z0-9_.:@-]*$`; the one list is `valid_slugs` (≤ 3 World
slugs). No free text exists in any field.

| `event` | `reason` values | `attrs` keys |
|---|---|---|
| `startup` | — | `buddy_version`, `nanobot_pin`, `world_sha8`, `contract_sha8`, `persona_sha8`, `seam_ok` |
| `gate_change` | `open`, `airgapped`, `no_consent`, `lab_unreachable`, `contract_mismatch`, `not_linked`, `wake_recheck`, `world_mismatch` | `state` |
| `inbound_accepted` | — | `input_chars`, `tg_lag_s`, `is_callback` |
| `inbound_refused` | `rate_limited`, `not_dm`, `media`, `stale`, `held`, `lab_held`, `lab_unreachable`, `budget`, `gate_closed` | `count` |
| `unknown_dropped` | — | `count`, `distinct`, `window_s` |
| `reply_sent` | `answer`, `abstain`, `refuse`, `escalate`, `held`, `lab_unreachable`, `budget`, `stale`, `card`, `confirm`, `error`, `help`, `status` | `e2e_ms`, `chips`, `chars` |
| `citation_check` | `ok`, `invalid_json`, `no_valid_chip` | `offered`, `cited`, `valid`, `dropped`, `valid_slugs` |
| `proposal_created` | `rule`, `model` | `proposal_id`, `action`, `expires_at` |
| `proposal_suppressed` | `requester_not_confirmer`, `held`, `outside_enum`, `superseded` | `action` |
| `confirm_attempt` | `accepted`, `not_confirmer`, `not_dm`, `bad_nonce`, `used_nonce`, `expired`, `unknown_proposal`, `voided`, `held`, `lab_held`, `malformed`, `path_violation` | `proposal_id`, `via`, `attempts` |
| `action_result` | `paused`, `already_paused`, `not_running`, `already_done`, `indeterminate_restart`, `refused`, `lab_unreachable` | `proposal_id`, `action` |
| `deny` | — | `proposal_id` |
| `stop` | `lab_held`, `lab_unreachable` | — |
| `outbound_suppressed` | `no_trigger`, `gate_closed` | `count` |
| `self_check_failed` | check id (`r1`…`t4`) | — |
| `config_changed` | — | — |

The edge maps each record to `agent_trace.record(record_type="event", kind="agent",
agent_id="buddy", label="buddy", attribution="agent:buddy", origin="edge",
event=…, channel=…, identity=…, outcome=…, reason=…, attrs={…, "client_ts": …},
trace_id=…)`. All model-call fields stay `None`.

### 5.8 `hold_all`

`POST /api/host/v1/hold_all`. Reductive. Allowed for any talker. Allowed while held.

Request: `{"v":1,"trace_id":"<16 hex>","reason":"telegram_stop","by":{"pseudonym":"p_<10 hex>","role":"confirmer"|"talker"}}`
(`additionalProperties:false`; `reason` is `const`).
Response 200: `{"held":true,"already_held":bool,"changed_at":"<iso>"|null}`.
Effect: `scheduler.halt_all_jobs()` (`scheduler.py:282-288`), idempotent. The edge
writes one event record `hold_all` (origin edge).

### 5.9 `pause_research`

`POST /api/host/v1/pause_research`. Needs a complete approval record.

Request:
```json
{"type":"object","additionalProperties":false,
 "required":["v","approval_id","action","args","proposal","confirm","expires_at","trace_id"],
 "properties":{
  "v":{"const":1},
  "approval_id":{"type":"string","pattern":"^[0-9a-f]{8}$"},
  "action":{"const":"arail.pause_research"},
  "args":{"type":"object","maxProperties":0},
  "proposal":{"type":"object","additionalProperties":false,
    "required":["id","proposed_at","proposed_by","requested_by","channel"],
    "properties":{"id":{"type":"string","pattern":"^[0-9a-f]{8}$"},
      "proposed_at":{"type":"string","format":"date-time"},
      "proposed_by":{"enum":["rule","model"]},
      "requested_by":{"$ref":"#/$defs/who"},"channel":{"const":"telegram"}}},
  "confirm":{"type":"object","additionalProperties":false,
    "required":["confirmer","nonce_sha256","confirmed_at","via"],
    "properties":{"confirmer":{"$ref":"#/$defs/who"},
      "nonce_sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"},
      "confirmed_at":{"type":"string","format":"date-time"},
      "via":{"enum":["tap","typed"]}}},
  "expires_at":{"type":"string","format":"date-time"},
  "trace_id":{"type":"string","pattern":"^[0-9a-f]{16}$"}},
 "$defs":{"who":{"type":"object","additionalProperties":false,"required":["pseudonym","role"],
   "properties":{"pseudonym":{"type":"string","pattern":"^p_[0-9a-f]{10}$"},
     "role":{"enum":["confirmer","talker"]}}}}}
```

Edge rules, in order:
1. Admission and schema.
2. `approval_id == proposal.id`.
3. **Ledger lookup** (`lab/data/buddy/approvals.sqlite3`, 0600, WAL,
   `synchronous=FULL`; table `approvals(approval_id PK, nonce_sha256 UNIQUE,
   record_sha256, state, outcome, received_at, executed_at)`). Same id and same
   `record_sha256` → return the stored outcome with `"replayed": true`. Same id with a
   different body, or the same `nonce_sha256` under another id → 422
   `approval_conflict`. A row stuck in `executing` (crash) → outcome
   `indeterminate_restart`.
4. Hold readable and not held (else 403).
5. `confirm.confirmer.role == "confirmer"` (else `role_not_confirmer`).
6. Times: `proposed_at ≤ confirmed_at ≤ expires_at`; `expires_at − proposed_at ≤ 600 s`;
   `|now − confirmed_at| ≤ 60 s` (else `stale_approval`).
7. Insert row `executing` and commit.
8. Read `researcher.status`. `running` → `researcher.pause()` → `paused`.
   `paused` → `already_paused`. Anything else → `not_running` (never call `pause()`
   on an idle researcher, `researcher.py:776-782`).
9. Update the row to `done` with the outcome. Write one event record
   `action_executed` (origin edge, reason = outcome).

Response 200: `{"approval_id":"…","outcome":"paused"|"already_paused"|"not_running"|"indeterminate_restart","research_status":"…","executed_at":"…","replayed":bool}`.

Honest limit: ARAIL cannot verify a Telegram identity. It trusts Buddy's credential.
The reductive-only rule caps the damage at a paused run.

### 5.10 `agent_trace` v2 and the lanes view

**Schema.** `SCHEMA = "arail.agent_trace/v2"` (`agent_trace.py:37`). `_FIELDS`
(`:43-57`) keeps every v1 key, same name and meaning, and appends:

| Field | Type | Meaning |
|---|---|---|
| `record_type` | `"model_call"` \| `"event"` | Chokepoint records are `model_call`. |
| `event` | string \| null | Event name (§5.7) or `edge_link`, `edge_refused`, `hold_all`, `action_executed`. |
| `channel` | string \| null | `telegram` for Buddy on the phone; `null` for in-process callers. |
| `identity` | `{pseudonym, role}` \| null | Salted pseudonym + role. Never a raw id. |
| `cost` | object \| null | `{"tokens": in+out, "simulated_usd": float, "cash_usd": float, "cash_basis": "local"\|"estimate", "label": "simulated"}` |
| `origin` | `"in_process"` \| `"edge"` | Who wrote the record. |
| `reason` | string \| null | Refusal or event reason. |
| `attrs` | object \| null | Event allowlist only (§5.7). |

`record()` still drops unknown keys and still never raises (`:107-147`).

**Chokepoint changes** (`router/core.py`). `AgentCall` (`agent_context.py:40-59`) gains
optional `channel: str|None`, `identity: tuple[str,str]|None` (pseudonym, role),
`origin: str|None`. `agent_call(...)` accepts them; an inner same-agent call
inherits them (same rule as `trace_id`, `:170-176`). `_record` (`core.py:185-226`)
adds `record_type="model_call"`, `channel`, `identity`, `origin` (default
`"in_process"`). `complete` and `stream_complete` keep the `CostRecord` that
`cost_tracker.track(...)` returns (`core.py:266-279`, `:366-378`) and pass
`cost={"tokens":…, "simulated_usd": round(rec.cloud_equivalent_usd, 6),
"cash_usd": rec.cloud_cost_usd if rec.cloud_cost_source == "billed_estimate" else 0.0,
"cash_basis": …, "label": "simulated"}` (`costs.py:430-466`). Refusal and error
records carry `cost: null`.

**Backward compatibility.** v1 keys are unchanged. In-repo tests compare against
the `SCHEMA` constant, not the literal (`tests/test_agent_trace.py:36`,
`tests/test_qa_win_conditions_and_hotpath.py:509`). `agent_traces.jsonl` will hold v1
lines (before upgrade) and v2 lines (after). ARAIL reads only the ring, except the
purge, which is schema-agnostic (`agent_trace.py:556-596`). 3D.EVAL reads v1 by name
(`docs/PLAN.md:345`); its adapter must accept `v1 | v2` (debt D9). A v2 reader
treats a missing v2 key as `null`.

**Lanes view.** `lanes_snapshot()` (`agent_trace.py:373-450`) stays
`arail.agent_lanes/v1` (tests pin it: `tests/test_admin_agent_lanes_endpoints.py:211`)
and changes additively:
- `calls`, `last` and the per-lane columns count only `record_type ∈ {None,"model_call"}`.
- each lane gains `recent`: the last 50 records of that agent with `channel != null`,
  newest first, bodies stripped (`_strip_bodies_if_recorder_off`, `:216-239`).
- `GET /api/admin/agent-trace/{trace_id}` (`app.py:6303-6313`) adds `related`: up to
  20 other ring records with the same `trace_id`, bodies stripped.
- `tokens_out_by_agent` (`:200-213`) counts `model_call` records only.

**Admin UI** (`admin.html`, after `#agent-lanes-list`, `:738-739`): a block "Buddy on
the phone — last 50 steps". Columns: time · step (plain label per event, or "model
call") · channel · who (`p_…` · role) · brain (or "n/a") · TTFT (ms, or the
`ttft_status`, or "n/a") · tokens in/out (or "n/a") · cost ("$0.00 cash ·
$0.0004 simulated", or "n/a") · outcome/reason. A row click opens the record plus
`related`. Same SSE refresh as today (`:1793-1800`). `esc()` on every cell.

### 5.11 Other ARAIL changes (small, additive)

| Change | Where | Contract |
|---|---|---|
| `response_schema` pass-through | `ModelRouter.complete/stream_complete` (`core.py:229-233`, `:306-310`) | New kw-only `response_schema: dict|None = None`. Passed to the backend **only when not None**, so every existing caller's request is byte-identical. |
| Ollama `format` | `OllamaNativeBackend.complete/stream_complete` (`backends.py:2030-2167`) | New kw-only `response_schema=None`; when set, `body["format"] = response_schema`. Other backends unchanged; the edge never sends a schema elsewhere. |
| Slot timeout | `portal/scheduler.inference_slot` (`:180-210`) | New kw-only `acquire_timeout_s: float|None = None`; `None` keeps today's global timeout. |
| Halt load flag | `scheduler.py:233-245` | New `halt_load_failed() -> bool`: true when `halt.json` existed but did not parse at load, until the next successful write. ARAIL's own behaviour is unchanged (debt D10). |
| Presence skip | `app.py:489-498` | Add `"/api/host/v1/"`. |
| Fast-path metrics | `portal/scheduler.py:60-75` | Add the four non-brain edge paths. |
| Route registration | `app.py`, next to `:1388-1391` | `from arail.portal.host_edge_routes import register_routes as _register_host_edge; _register_host_edge(app)` |
| Public cloud check | `registry/core.py:144` | `is_cloud_entry = _is_cloud_entry` (alias). |

New modules: `src/arail/buddy_host/{__init__,link,admission,brain,budget,ledger,events,__main__}.py`,
`src/arail/buddy_host/contract/…` (vendored), `src/arail/portal/host_edge_routes.py`,
`scripts/sync-buddy-contract.py`. `_builtin_buddy.py` is **not touched**.

### 5.12 `./arailctl buddy`

`arailctl` gains a thin case, same pattern as `world` (`arailctl:1294-1299`):
`buddy) source .venv/bin/activate; exec python -m arail.buddy_host "$@" ;;`.

| Verb | Effect | Refuses when |
|---|---|---|
| `link --buddy-home PATH` | Prints the VISION §3.3 consent text verbatim; asks the operator to type `yes`; records `ConsentStore.request_access("https://api.telegram.org/", reason="Buddy on Telegram", agent="buddy")` + `approve(id, remember_domain=True)` (`consent.py:64-112`); mints the credential; writes ARAIL's `link.json` and Buddy's `link.json` + `secrets.env` line. | `ARAIL_INSTANCE` set; `BIND_ADDR` not loopback; PATH missing, not owned by the uid, not `0700`, or a symlink; an existing link without `--rotate`; stdin not a TTY and no `--yes`. |
| `link --rotate --buddy-home PATH` | New credential; old one dies at once. | Same placement checks. |
| `unlink [--buddy-home PATH]` | Deletes ARAIL's link; `remove_domain("api.telegram.org")`; with PATH, also Buddy's link and credential line. | — |
| `status [--json]` | Linked?, `credential_id`, `linked_at`, consent. Never the credential. | — |

Exit codes: 0 ok · 2 usage · 3 refused by policy. Never prints the credential. The
portal does not need to be running. `PORTAL_PORT` comes from the same `lab.conf`
resolution `scripts/status.sh` uses (exact helper **not verified**).

### 5.13 The nanobot patch set (core diff)

> **Addendum A (2026-10-20):** no new patch; the seam lock grows to 16 seam files plus the patched files (§A1.5).

Three commits on `qukaizen/buddy-always-on`, in this order. Each adds a row to
`UPSTREAM.md` (file, commit, sha256 after patch, reason). Pre-patch sha256 prefixes
at the tag, for the reviewer: `__main__.py` `c8e8dce8576f9ea1`,
`gateway_runtime.py` `5dda5bfb083580ec`, `telegram/runtime.py` `793e374682bf88cd`.

**P0 — `nanobot/__main__.py` (8 lines). +1 line after line 3:**
```python
import buddy.boot  # noqa: F401  qukaizen-buddy P0 (UPSTREAM.md): guards, redaction, preflight. Keep first.
```
Why: nanobot's own LaunchAgent installer runs `python -m nanobot gateway`
(`gateway/runtime.py:107-124`). This is the only place to install the guard before
any nanobot module, PTB or httpx is imported.

**P1 — `nanobot/cli/gateway_runtime.py` (1034 lines). +4 lines, 2 changed:**
- after line 44: `import buddy.nanobot_seam as _buddy_seam  # qukaizen-buddy P1`
- line 495: `hooks=[_MCPReadinessHook(mcp_provider)],` →
  `hooks=[_MCPReadinessHook(mcp_provider), *_buddy_seam.agent_hooks()],`
- line 496: `hook_factories=[create_file_edit_activity_hook],` →
  `hook_factories=[create_file_edit_activity_hook, *_buddy_seam.turn_hook_factories()],`
- after line 852 (after `cron_status`, before `_open_browser_when_ready`):
  ```python
      # qukaizen-buddy P1: install Buddy before any task starts; a refusal exits 0.
      _buddy_seam.install(agent=agent, channels=channels, cron=cron, bus=bus, config=config,
                          trigger_store=trigger_store, mcp_provider=mcp_provider)
  ```
Why here and not after `AgentLoop` (the VISION's spot): the install needs
`channels` (built at `:715-733`) and the cron state after Dream and heartbeat are
registered or removed (`:817-847`). Hooks must be passed at construction
(`AgentLoop` keeps them in private lists, `loop.py:371-372`).

**P2 — `nanobot/channels/telegram/runtime.py` (2231 lines). 1 line:**
line 693 `filters.Regex(TelegramChannel.TELEGRAM_BUS_SLASH_COMMAND_RE),` →
`filters.Regex(self.TELEGRAM_BUS_SLASH_COMMAND_RE),`.
Why: the forwarded command set is class-qualified, so a subclass cannot extend it.
Without this, typed `/approve` and `/deny` never reach any handler (`:539-542`,
`:713`). Upstream-PR candidate.

**Why not the VISION's `base.py` and username patches.** Buddy needs his own talker
file, DM-only, rate limit, stale rule, hold and confirm handling at the same seam.
Two `if` guards in `base.py` and `telegram/runtime.py` would not provide that, and a
subclass must exist anyway. `BuddyTelegramChannel` (§5.17) overrides `is_allowed`
(numeric id only), `_send_pairing_code_if_private` (silent drop) and
`_handle_message` (Intake). The pairing branch in `base.py:274-304` is never reached
for Buddy, because Buddy's `_handle_message` returns before calling `super()` for
any sender that is not a talker.

**Seam lock (fail closed on upstream drift).** `buddy/src/buddy/pins/nanobot.pin.json`:
```json
{"schema":"qukaizen-buddy.nanobot-pin/v1","upstream":"https://github.com/HKUDS/nanobot",
 "tag":"v0.3.5","commit":"1bb712d3488915ca4ed9ccc1a93067ff722f5ab9",
 "patches":[{"id":"P0","commit":"<sha>","file":"nanobot/__main__.py","sha256_after":"<64>"},
            {"id":"P1","commit":"<sha>","file":"nanobot/cli/gateway_runtime.py","sha256_after":"<64>"},
            {"id":"P2","commit":"<sha>","file":"nanobot/channels/telegram/runtime.py","sha256_after":"<64>"}],
 "seam_files":{"nanobot/channels/base.py":"<64>","nanobot/channels/manager.py":"<64>",
   "nanobot/command/router.py":"<64>","nanobot/command/builtin.py":"<64>",
   "nanobot/agent/hook.py":"<64>","nanobot/agent/runner.py":"<64>","nanobot/agent/loop.py":"<64>",
   "nanobot/runtime_context.py":"<64>","nanobot/providers/base.py":"<64>",
   "nanobot/gateway/service.py":"<64>"},
 "seams":{"nanobot/channels/telegram/runtime.py":["is_allowed","_send_pairing_code_if_private",
   "_handle_message (via base)","_on_start","_on_help","_build_message_metadata",
   "_download_message_media","_stop_typing","start","stop","send","BOT_COMMANDS"],
   "nanobot/agent/runner.py":["before_iteration before request","finalize_content on every response"],
   "nanobot/providers/base.py":["retry status set","transient markers"]}}
```
At startup Buddy hashes those 13 files from `nanobot.__file__`'s directory. Any
difference → refusal S6 with a fixed message. Moving the pin means re-reviewing each
changed seam file's upstream diff against `seams`, then updating the hashes. This
turns a silent upstream rename into a loud refusal. Its cost is debt D4.

**Upstream test mode.** Upstream tests call `_run_gateway`
(`tests/cli/test_commands.py:3503`). The rebase rule runs them as
`pytest -p buddy.testing.upstream_mode`. That pytest plugin sets
`buddy.nanobot_seam._UPSTREAM_TEST_MODE = True`, which makes `agent_hooks()` and
`turn_hook_factories()` return `[]` and `install()` a no-op. The module refuses to
set the flag unless `pytest` is imported and the plugin was loaded by pytest. In
every other process, `install()` without a completed `buddy.boot` preflight
**refuses** (fail closed).

### 5.14 `buddy.boot` (P0): guard, redaction, preflight

Runs at import, before nanobot's CLI. Steps:

1. Install the audit-hook guard (§5.15) in **closed** mode.
2. Wrap `sys.stdout` and `sys.stderr` in a redacting writer; register a loguru
   patcher. Both replace exact secret values (bot token, edge credential, pseudonym
   salt) and the patterns `\d{6,}:[A-Za-z0-9_-]{30,}` (bot token shape),
   `bhc1_[A-Za-z0-9_-]{43}`, and `/(approve|deny) [0-9a-f]{8} [0-9a-f]{16}`
   (→ `/approve <id> <nonce>`). This catches nanobot's reply preview
   (`loop.py:1727-1731`), the DEBUG tap log (`telegram/runtime.py:2217`), and
   tracebacks. The launchd log files are `~/.nanobot/logs/nanobot-qukaizen-buddy.launchd{,.err}.log`
   (`gateway/service.py:137-139`).
3. Read `argv`. For `gateway` (foreground run) the home is the parent of `--config`,
   which must be `<home>/nanobot.json` next to `<home>/buddy.toml`. Other nanobot
   subcommands: `gateway status|logs|stop` run with the guard closed; anything else
   exits 2 with "This nanobot is Buddy. Use `python -m buddy`."
4. Load `BUDDY_*` keys from `<home>/secrets.env` into the environment (only those keys).
5. Run the static preflight. Any failure prints one fixed line naming the check id
   (never a value) and exits **0**, so launchd's `KeepAlive{SuccessfulExit:false}`
   does not respawn (`service.py:145`).

| Id | Check |
|---|---|
| S1 | Python ≥ 3.11. |
| S2 | `<home>` is `0700`; `buddy.toml`, `people.toml`, `secrets.env`, `link.json`, `nanobot.json` are `0600`, owned by the uid, not symlinks. |
| S3 | `people.toml` valid (§5.18). |
| S4 | `link.json` valid; edge host is `127.0.0.1` or `::1`. |
| S5 | `BUDDY_TELEGRAM_TOKEN`, `BUDDY_EDGE_CREDENTIAL` (`bhc1_…`), `BUDDY_PSEUDONYM_SALT` (64 hex) present. |
| S6 | Seam lock (§5.13). |
| S7 | World pin and seal (§5.21). |
| S8 | Contract file sha256 equals the `X-Buddy-Contract` value in `nanobot.json`. |
| S9 | `nanobot.json` loads with nanobot's own loader and matches the allow-list (§5.22): one channel (telegram, polling, inline keyboards on, streaming off, rich off, no proxy); websocket off; progress, tool hints and reasoning off; one provider (`arailEdge`, loopback); one preset; no fallbacks; Dream off; heartbeat off; `idleCompactAfterMinutes: 0`; `maxToolIterations ≤ 2`; `providerRetryMode: standard`; `unifiedSession: false`; transcription off; `mcpServers: {}`; exec, web and file tools off; gateway host loopback. |
| S10 | Environment: no `NANOBOT_*` (pydantic-settings prefix, `schema.py:664-667`), no `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` (any case), no `LANGFUSE_*` (`openai_compat_provider.py:621-625`), no `OPENAI_LOG`, no `PYTHONPATH`, no `NANOBOT_MAX_CONCURRENT_REQUESTS`. |
| S11 | Exclusive `flock` on `<home>/state/buddy.lock` (one Buddy per home). |
| S12 | No `pairing.json` next to the config, or an empty one. |

### 5.15 Egress, bind and exec guard

One `sys.addaudithook` callback (CPython audit events; hooks cannot be removed).
State: `mode ∈ {closed, open}`; `loopback_ports = {edge_port, health_port}` (from
`link.json` and `buddy.toml`); `learned_ips` for `api.telegram.org` with a 1-hour TTL.
Refusals raise `BuddyEgressRefused(PermissionError)`, an `OSError`, so libraries treat
it as a network failure, not a crash. Each refusal increments a counter by kind and
logs at most one line per kind per minute (host and port only).

| Audit event | Allowed | Refused |
|---|---|---|
| `socket.getaddrinfo` | `localhost`, `127.0.0.1`, `::1`; `api.telegram.org` only while `open` | everything else (so no DNS query leaves for other names) |
| `socket.connect` | `AF_UNIX`; loopback IP **and** port ∈ `loopback_ports`; while `open`, an IP in `learned_ips` | all other IPv4/IPv6, including Ollama `:11434` and other loopback ports (G8) |
| `socket.sendto`, `socket.sendmsg` | `AF_UNIX`; loopback | everything else |
| `socket.bind` | `(127.0.0.1 or ::1, health_port)`; `AF_UNIX` | `0.0.0.0`, `::`, any other port (WebUI 8765, API 8900, webhook 8081) |
| `subprocess.Popen`, `os.system`, `os.exec`, `os.posix_spawn`, `os.spawn` | before `install()` finishes | after `install()` arms the exec guard |

`learned_ips` is filled by a Python-level wrapper on `socket.getaddrinfo` (asyncio
and anyio resolve through it): it records the results for `api.telegram.org` while
`open`. A caller that resolves through `_socket` directly gets its addresses refused
at connect: fail closed. ModeGate flips `open`/`closed` synchronously before it
starts or stops the channel.

Not covered: native code that calls libc `connect` without CPython (A9). The
system-level test (§8.3 L3) is the backstop.

### 5.16 `buddy.nanobot_seam`: hooks and `install()` (P1)

> **Addendum A (2026-10-20):** `before_iteration` no longer writes; `install()` gains R8 (two wraps) and the hook gains C5/C8 (§A1.3). G13 trims the `/status` template (§A11).

`agent_hooks()` → `[]` (reserved). `turn_hook_factories()` → `[BuddyTurnHook.factory]`.

`BuddyTurnHook(AgentHook)` (one per turn; `reraise=True` so a hook error aborts the
turn instead of being swallowed, `hook.py:174-183`):
- `before_run`: T1 tools count is 0; T2 `nanobot.json` sha256 equals the boot value
  (else close the gate, trace `config_changed`, and stop the process with SIGTERM to
  itself; the resulting exit code is **not verified**, builder confirms it is 0); T3 Buddy hold off.
- `before_iteration`: replace `context.messages[0]` with
  `"BUDDY-TURN/1 " + header_json + "\n" + persona`; scrub any
  `/(approve|deny)\s+\S+\s+\S+` from every message; keep the last 4 user/assistant
  pairs plus the current user message; T4 the persona carries "Built with Llama."
  whenever the cached brain attribution says so.
- `finalize_content`: §5.20. Must return non-blank text in every branch (a blank
  return triggers nanobot's empty-response retries, `runner.py:589-606`).
- `after_run`: publish a proposal card if `finalize_content` accepted one (§5.19).

`install(agent, channels, cron, bus, config, trigger_store, mcp_provider)` runs
these runtime checks (R-codes). Any failure → fixed message, exit 0.

| Id | Action and check |
|---|---|
| R1 | Remove `websocket` from `channels.channels` (public dict, `channels/manager.py:136`); swap `channels.channels["telegram"]` for `BuddyTelegramChannel(old.config, bus, services)` with progress, tool hints and reasoning off. Then assert the dict keys are exactly `{"telegram"}` and the type is Buddy's. |
| R2 | Unregister every tool (`agent.tools.unregister`, `agent/tools/registry.py:35`); assert `len(agent.tools) == 0`. |
| R3 | `cron.status()["jobs"] == 0` (Dream and heartbeat removed at `:817-847`). |
| R4 | The local trigger store holds no pending triggers (API **not verified**; builder confirms). |
| R5 | `agent.commands = CommandRouter()` with only Buddy's commands: priority `/stop`, priority `/status`, exact `/new`, exact `/help`, exact and prefix `/approve`, `/deny`. The `/approve` and `/deny` handlers here only refuse ("confirms are handled at the channel boundary") and trace `confirm_attempt path_violation`: a second line of defence if the boundary were bypassed. |
| R6 | `agent.register_runtime_context_provider(world_provider)` once (`loop.py:643-656`). |
| R7 | The resolved provider is the custom `arailEdge` (`schema.py:509-525`) with a loopback base. |
| — | Arm the exec guard. Record `startup` (spooled until the first handshake). |

Buddy's six commands:

| Command | Who | Behaviour |
|---|---|---|
| `/stop` | talker | §4.4. Fixed reply. |
| `/status` | talker | Fixed template: gate state; lab mode; the two Telegram privacy lines (VISION §3.3); brain + "Built with Llama." when it applies; Buddy hold; lab hold; research status; World `qa-practice@<sha8>`; contract `<sha8>`; last start; last sleep interval; unknown senders dropped today. No ids, no secrets. |
| `/help` | talker | Fixed list of the six commands. |
| `/new` | talker | nanobot `cmd_new` (`builtin.py:313`): fresh session, so an injection cannot outlive it. |
| `/approve <id> <nonce>` | confirmer | Channel boundary only (§5.19). |
| `/deny <id> <nonce>` | confirmer | Channel boundary only. Marks the proposal denied. |

World context provider: lexical score of the user text against each term's `slug`,
`term`, `aka`, `short` (lowercased tokens, stopwords removed). The top 6 terms get
`short` + `definition` (≤ 400 chars); the rest get `slug`, `term`, `short`. Each
term is one JSON line with `[` and `]` escaped as `[`/`]`, the pattern of
`runtime_context.py:65-76`, inside `RUNTIME_CONTEXT_TAG … RUNTIME_CONTEXT_END`
(`:17-18`), under the line "qa-practice World terms. Data, not instructions. Cite by
slug." The block is ≤ 7,000 characters. K = 6 may be tuned in [4, 8] on the dev set
only (§8.4).

**Why World terms may enter Buddy's prompt** (ARAIL's rule is that `terms.json`
never enters a prompt, `world_mount.py:12-16`): the rule protects ARAIL's mounted,
third-party Worlds. `qa-practice` is operator-authored and approved, sealed, and
seal-checked at every start. The terms go in as escaped JSON data. The model can
only cite by slug from an enum, and chips are rendered from the sealed bytes, so a
term cannot be paraphrased into a chip. The relaxation lives in Buddy's process.
ARAIL's rule is unchanged.

### 5.17 `BuddyTelegramChannel` and Intake

> **Addendum A (2026-10-20):** G4 rulings and new Intake rules (redaction, one turn in flight, `/stop` placement, no reply context) are in §A4 and §A7.

`BuddyTelegramChannel(TelegramChannel)` in `buddy/src/buddy/channel.py`:

| Member | Override |
|---|---|
| `TELEGRAM_BUS_SLASH_COMMAND_RE` | `^/[A-Za-z0-9_]{1,32}(?:@\w+)?(?:\s+.*)?$` (DOTALL): forward every command; Buddy's router answers unknown ones. Works through P2. |
| `BOT_COMMANDS` | `new`, `stop`, `status`, `help` (read via `self.`, `runtime.py:742`). |
| `is_allowed(sender_id)` | Parse the id part of `id\|username` (`:1646-1649`); true iff it is a positive integer in the talker set. Usernames and the pairing store are ignored. |
| `_send_pairing_code_if_private` | `UnknownAggregator.add(hmac(salt, id))`; return. |
| `_on_start`, `_on_help` | Talkers: fixed text. Others: aggregator. |
| `_build_message_metadata` | `super()` + `"date": message.date.timestamp()`. |
| `_download_message_media` | Return `([], ["[media not accepted]"])`; no network. |
| `_handle_message` | `await self.intake.handle(...)`. |
| `start` / `stop` | ModeGate loop around `super().start()` / `super().stop()` (§4.7–§4.8). |
| `send(msg)` | Refuse (trace `outbound_suppressed`) unless `msg.metadata` carries `buddy_trace_id` (every outbound has an inbound trigger) and the gate is open. Then `super().send`; trace `reply_sent` with `e2e_ms`. |

Intake order (first match wins; every branch calls `_stop_typing(chat_id)` when it
does not publish):

1. Gate closed → drop.
2. Not a talker → aggregator (G4: no reply, no log, no bus, no trace body).
3. Not a DM (`chat_id != user_id` or `is_group`) → drop; trace `inbound_refused not_dm`.
4. Rate: 10 per minute and 200 per day per id → one fixed "Slow down" per minute,
   then silent; trace aggregated per minute.
5. Media placeholder → "I read text only."
6. Stale (`date` older than 600 s) → sleep or unreachable reply (§4.9–§4.10).
7. `/approve`, `/deny` → `ConfirmService` (§5.19). Never published.
8. `/stop` → write the hold file, then publish.
9. Buddy held → held reply (except `/status`, `/help`, which publish).
10. Cached lab hold (or `readable:false`) → "The lab is on hold. Resume it at the desk."
11. Lab unreachable inside the grace window → "The lab is not reachable since HH:MM."
12. Cached budget exhausted → "Buddy has used today's budget. He answers again after midnight."
13. Confirmer + rule match → card (§4.3). Talker + rule match → "Pausing research
    needs a confirmer."
14. Otherwise stamp `buddy_trace_id`, `buddy_identity`, `buddy_received_mono` into
    metadata → `super()._handle_message(...)` → trace `inbound_accepted`.

### 5.18 Identity: `people.toml` and pseudonyms

`<home>/people.toml` (0600, desk-only; chat has no write path):
```toml
schema = "qukaizen-buddy.people/v1"
[[person]]
telegram_id = 123456789          # numeric Telegram user id
roles = ["confirmer", "talker"]  # confirmer implies talker
label = "operator"               # local only; never sent anywhere
[[person]]
telegram_id = 987654321
roles = ["talker"]
label = "qa-test"
```
Rules: ids are integers in `[1, 2^53)`; no duplicates; at least one confirmer; every
confirmer is a talker; unknown keys refused. Re-read when the mtime changes; an
invalid file on reload sets the talker set to **empty** (drop everyone) and logs a
fixed line until fixed.

Pseudonym: `"p_" + hex(HMAC-SHA256(salt, b"telegram:" + str(id)))[:10]`. The salt
is `BUDDY_PSEUDONYM_SALT` (32 random bytes, hex, made by `buddy setup`). Raw ids
never leave Buddy's process or files.

### 5.19 Proposals, nonce, approval record, card

> **Addendum A (2026-10-20):** automatic resend of a lost answer, and the code is never printed (§A5). G13 removes the card's "Research now" line (§A11).

Store: `<home>/state/proposals.sqlite3` (0600, WAL, `synchronous=FULL`):
`proposals(id TEXT PRIMARY KEY, action TEXT, requester_hash TEXT, nonce_sha256 TEXT,
proposed_by TEXT, created_at TEXT, expires_at TEXT, state TEXT, bad_attempts INT,
approval_json TEXT, edge_outcome TEXT, updated_at TEXT)`.
States: `open → confirmed → executed | edge_refused | edge_unknown`;
`open → denied | expired | voided` (3 bad nonces, or superseded by a newer card for
the same action and confirmer).

- **Action table** (code, frozen): `{"pause_research": "arail.pause_research"}`.
  Nothing else can be proposed or executed.
- **Id:** 8 hex (`secrets.token_hex(4)`), unique among open proposals.
- **Nonce:** 16 hex (`secrets.token_hex(8)`, 64 bits). Only `sha256(nonce)` is
  stored. The plaintext exists only in the card's button labels in memory and in
  Telegram. 64 bits with 3 attempts and a 10-minute life is enough.
- **Card** (code template; the model writes none of it):
  ```
  Request: pause the research run.
  Action: arail.pause_research · no arguments
  Asked by: p_3f9a1c2e7b (you) at 14:02 · expires 14:12
  Research now: running
  Tap the first button to approve, the second to cancel. Resume is at the desk.
  ```
  Buttons: `[["/approve 7f3a9c2e 0123456789abcdef", "/deny 7f3a9c2e 0123456789abcdef"]]`.
  nanobot sets `callback_data` = label (`runtime.py:2175-2191`): 34 and 31 bytes,
  under 64. Labels show the command text; that is cosmetic and accepted.
- **Confirm checks:** §4.3 step 2. Constant-time compare. A non-confirmer never
  touches the nonce or the counter. Forged `callback_data` is treated exactly like
  typed text (a Telegram client can send any `callback_data` for a message it can
  see).
- **Approval record:** §5.9 schema. `approval_id` = proposal id = idempotency key.
  `confirmed_at` = Buddy's receipt time of the tap.
- **Write-ahead:** set `confirmed` and store the record (fsync) before calling the
  edge. A crash between the two is resolved by resending the same record.
- **Expiry:** 10 minutes. No automatic resubmission at startup: an action needs a
  human event.

### 5.20 Answer schema, parsing, rendering, chips

`buddy.answer/v1`, sent as `response_schema` in the turn header and enforced by
Ollama `format`, then validated again by Buddy with `jsonschema`:
```json
{"type":"object","additionalProperties":false,"required":["kind","answer","cite","propose"],
 "properties":{
  "kind":{"enum":["answer","abstain","refuse","escalate"]},
  "answer":{"type":"string","maxLength":600},
  "cite":{"type":"array","maxItems":3,"uniqueItems":true,"items":{"enum":["<every slug in the pinned World>"]}},
  "propose":{"enum":["none","pause_research"]}}}
```
`finalize_content(context, content)`:
1. `finish_reason == "error"` → fixed text from Buddy's own state: held, lab held,
   lab unreachable, budget, otherwise "Something went wrong reaching the lab. Ask
   again." (`runner.py` uses this text, `:716-720`).
2. Parse and validate. Invalid → abstain text; trace `citation_check invalid_json`.
3. Drop cites not in the pinned World (cannot happen under `format`; checked anyway).
4. `kind=answer` with no valid cite → abstain. `refuse`/`escalate` with no valid
   cite → fixed fallback ("That is reserved to you at the desk." / "That is your
   call at the desk.").
5. Answer text: strip URLs and markdown links; collapse whitespace; cap at 3
   sentences; never parsed for commands.
6. Chips, one per valid cite, from the sealed `terms.json` values:
   `▸ <term> — <short> [qa-practice@<sha8> · <source>]`. `term`, `short`, `source`
   are inserted byte-for-byte after markdown-escaping for nanobot's converter
   (`telegram/runtime.py:267-355`). §8.5 proves the displayed text equals the sealed
   value for every card.
7. Abstain text: "The qa-practice World does not cover that. Ask at the desk, or add a card."
8. `propose == "pause_research"` → stash for `after_run` (§4.3 model path).
Trace `citation_check` with `offered`, `cited`, `valid`, `dropped`, `valid_slugs`.

### 5.21 `qa-practice` World pin and seal check

Vendored bundle: `buddy/src/buddy/worlds/qa-practice/` = the six sealed files plus
`manifest.json`, exported from DDaC (`scripts/export-bundle.mts --world=qa-practice
--out=<that dir>`). Pin: `buddy/src/buddy/worlds/qa-practice.pin.json` (pattern:
`qukaizen-3d-eval/ddac.pin.json`):
```json
{"schema":"qukaizen-buddy.world-pin/v1","world":"qa-practice",
 "repo":"cdarnell/qukaizen-dac","commit":"<40 hex>","source_dir":"data/worlds/qa-practice",
 "export":"node --experimental-strip-types scripts/export-bundle.mts --world=qa-practice",
 "world_sha256":"<64>","manifest_sha256":"<64>",
 "files":{"terms.json":"sha256:…","spec.json":"sha256:…","roster.json":"sha256:…",
   "face.json":"sha256:…","agenda.json":"sha256:…","drift-report.json":"sha256:…",
   "manifest.json":"sha256:…"},
 "pinned_on":"YYYY-MM-DD","approval":"operator line in SPRINT.md decisions log, YYYY-MM-DD"}
```
Startup check (S7), about 15 lines, no ARAIL import, no vendored `dac_world`:
every file's sha256 equals the pin; `sha256(terms.json) == pin.world_sha256 ==
manifest.world_sha256 == manifest.files["terms.json"]` (same law as
`world_mount.verify_seal`, `:458-520`); every sealed sibling equals its
`manifest.files` entry (stricter than ARAIL, which skips unlisted ones, `:487-490`);
`manifest.schema` is `dac.world-bundle/v1`; every term has `slug`, `term`, `short`,
`definition`, `source`, `category`; no source matches `^model:` (`provenance.py:16`);
`term` and `short` contain none of ``*_`[]<>``. Failure → the fixed G10 message
"Buddy's qa-practice World does not match its seal. Buddy is not running." and exit 0.

### 5.22 Buddy home, config files, LaunchAgent

> **Addendum A (2026-10-20):** `contextWindowTokens` is 200000, not 8192 (§A1.3 C7).

| Path (default home `~/.qukaizen-buddy`, `0700`) | Mode | What |
|---|---|---|
| `buddy.toml` | 0600 | Buddy's own config (below). Not nanobot's. |
| `nanobot.json` | 0600 | Generated by `python -m buddy configure`; validated by S9 at every start. |
| `people.toml` | 0600 | §5.18. |
| `secrets.env` | 0600 | `BUDDY_TELEGRAM_TOKEN`, `BUDDY_EDGE_CREDENTIAL`, `BUDDY_PSEUDONYM_SALT`. |
| `link.json` | 0600 | Written by `./arailctl buddy link`. |
| `workspace/` | 0700 | nanobot workspace; sessions under the config dir. |
| `state/` | 0700 | `hold.json`, `proposals.sqlite3`, `trace-spool.jsonl`, `buddy.lock`, `sleep.json`. |

`buddy.toml`:
```toml
schema = "qukaizen-buddy.config/v1"
[gate]      handshake_every_s = 20   unreachable_close_after_s = 40   stale_after_s = 600
[limits]    per_identity_per_min = 10   per_identity_per_day = 200   unknown_trace_every_s = 60
[proposals] ttl_s = 600   max_bad_attempts = 3
[ports]     health = 18797
```
(one key per line in the real file.)

`nanobot.json` (generated; tool flag names to be filled from the tool config
classes, **not verified**):
```json
{"agents":{"defaults":{"workspace":"~/.qukaizen-buddy/workspace","modelPreset":"buddy",
   "fallbackModels":[],"maxToolIterations":2,"idleCompactAfterMinutes":0,"unifiedSession":false,
   "providerRetryMode":"standard","botName":"Buddy","disabledSkills":["<all>"],"dream":{"enabled":false}}},
 "modelPresets":{"buddy":{"model":"buddy-fast","provider":"arailEdge","maxTokens":400,
   "contextWindowTokens":8192,"temperature":0.1}},
 "providers":{"arailEdge":{"apiBase":"http://127.0.0.1:<PORTAL_PORT>/api/host/v1/brain",
   "apiKey":"${BUDDY_EDGE_CREDENTIAL}","extraHeaders":{"X-Buddy-Contract":"sha256:<hex>"}}},
 "channels":{"sendProgress":false,"sendToolHints":false,"showReasoning":false,
   "websocket":{"enabled":false},
   "telegram":{"enabled":true,"token":"${BUDDY_TELEGRAM_TOKEN}","mode":"polling","allowFrom":[],
     "inlineKeyboards":true,"streaming":false,"richMessages":false,"replyToMessage":false}},
 "transcription":{"enabled":false},
 "gateway":{"host":"127.0.0.1","port":18797,"heartbeat":{"enabled":false}},
 "tools":{"mcpServers":{},"restrictToWorkspace":true}}
```

**LaunchAgent.** `python -m buddy service install` calls nanobot's
`GatewayServiceInstaller` (`gateway/service.py:129-169`) with
`name="nanobot-qukaizen-buddy"`, `--config <home>/nanobot.json`,
`--workspace <home>/workspace`, port 18797. Label `ai.nanobot.qukaizen.buddy`
(`:232-236`); plist `~/Library/LaunchAgents/ai.nanobot.qukaizen.buddy.plist`;
`RunAtLoad`, `KeepAlive{SuccessfulExit:false}` (`:140-148`). ARAIL's is
`io.arail.service` (`scripts/install-daemon.sh:28`). `arailctl` does not supervise
Buddy. Linux: the same installer writes a systemd user unit (`:80-107`); documented,
not tested in S1.

**Packaging.** Buddy is a separate distribution in `buddy/pyproject.toml`
(`qukaizen-buddy`, import `buddy`, `src` layout) so upstream `pyproject.toml`
(wheel packages = `["nanobot"]`, `:145-146`) stays untouched. Dependencies:
`python-telegram-bot[socks,webhooks]>=22.6,<23.0` (as `telegram/manifest.py:42`),
`jsonschema`. Install: `uv pip install -e . -e ./buddy` in one venv, from
`buddy/requirements.lock` with hashes.

### 5.23 "Built with Llama" (G11)

- Handshake `brain.attribution` is computed by ARAIL from the catalog family
  (`app.py:10591-10597`; `models_catalog.yaml` `llama-ai-eng` family `llama`).
- When it is "Built with Llama": `/status` shows "Brain: llama-ai-eng via ARAIL
  (local). Built with Llama." and the persona prompt ends with "Built with Llama."
  (T4 refuses the turn otherwise).
- `buddy/README.md` states the S1 brain and "Built with Llama". Buddy ships no Llama
  weights; ARAIL's NOTICE and licences cover the model it serves.

### 5.24 Buddy's hold file

`<home>/state/hold.json`, paperagents `hold` v1 shape (ARCHITECTURE §7.12.4):
`{"v":1,"on":true,"since":"…Z","reason":"telegram_stop","scope":"agent:buddy","by":"p_…","stopTurn":true}`.
Missing → off. `on:false` → off. Unreadable, unparseable or shape-invalid → **on**
(reason `hold-corrupt`). Written atomically (temp file, fsync, `os.replace`), 0600.
Only the desk clears it: `python -m buddy hold off`. S2's paperagents `arail-agent`
runtime points `haltPath` here.

---

## 6. Model proposal mechanism

### 6.1 Pick: structured output, not a tool

**Structured output wins.** A one-field tool would need tool definitions to reach the
model. ARAIL's `ModelRouter` has no tool parameter (`core.py:229-233`) and its Ollama
backend drops `messages` (`backends.py:2041-2045`). Passing tools would mean changing
every backend signature. A tool would also put nanobot's tool dispatch
(`execution.py:114-172`, no deny path) on the action path. Structured output keeps
the model's only power to one field in one JSON object, parsed by code.

### 6.2 How a 1B model is kept from proposing other actions

1. **No tools exist.** Buddy unregisters every tool (R2), checks it every turn (T1),
   and the edge refuses any request with `tools` (`tools_not_allowed`).
2. **The decoder cannot write another action.** Ollama `format` is the
   `buddy.answer/v1` schema; `propose` is `enum ["none","pause_research"]`. Grammar-
   constrained decoding cannot emit any other value. `cite` items are an enum of the
   World's slugs, so a fabricated slug cannot be decoded either.
3. **Code validates again** with `jsonschema` and a frozen action table. Anything
   else is dropped and traced (`proposal_suppressed outside_enum`).
4. **Model text is never a command.** Commands are parsed only from inbound user
   text at the channel boundary. A reply that contains `/approve …` is just text; it
   has no button, and a resend of it fails the nonce check.
5. **A proposal does nothing.** It makes a code-rendered card. Only a confirmer's
   tap with the right nonce acts, and ARAIL re-checks the approval.
6. **The scripted request does not depend on the model.** The exact imperative is
   matched by code (§4.3). The model handles other phrasings.

### 6.3 Parsing is code only

The model's raw output is read in exactly one place: `finalize_content`. It is
parsed as JSON and validated. The card, the chip text, the action name and the
arguments all come from code tables and the sealed World. No model text reaches the
card, the trace, or an ARAIL request.

### 6.4 Fallback: template mode

If the handshake says `structured_output: false`, or the blind quality bar fails
(VISION §2.3), Buddy switches to template mode: code picks the top-scoring term
(score ≥ threshold, else abstain), renders its chip, and asks the model for one
plain sentence about it. No `propose` field; only the code rule proposes. This is
the VISION's fallback, and it fires disconfirming #2.

---

## 7. Failure modes

> **Addendum A (2026-10-20):** F6, F16 and F34 are amended, and F46–F55 are added, in §A7.

Every row has a test (§8). "Contain" says what limits the damage.

| # | Failure | Detection | Containment | Test |
|---|---|---|---|---|
| F1 | **Nonce replay** (same `/approve id nonce` again, typed or re-tapped) | Proposal state `executed`/`confirmed`; nonce hash marked used | "Already done."; no second edge call; edge ledger returns the stored outcome for the same `approval_id` | U-CONF-03, I-EDGE-07, W-4 |
| F2 | **Confirmer spoof by username** (attacker takes a dropped username) | `is_allowed` and roles use the numeric id only | Usernames never matched; pairing store ignored | U-ID-02 |
| F3 | **Telegram re-delivers after a crash** (unacknowledged update) | Same nonce hash already `confirmed`/`executed` | Write-ahead state + idempotent edge; a repeated question is just answered again | I-CRASH-01 |
| F4 | **Two cards open at once** | Store has two `open` rows for one action and confirmer | The newer card voids the older (`superseded`); an old tap → "That request was replaced."; `pause` is idempotent on research state | U-CONF-07 |
| F5 | **Approval while the session is busy** | — | Confirms run at the channel boundary, not in nanobot's turn queue; a busy turn cannot delay or swallow them | I-CONF-05 |
| F6 | **ARAIL restarts mid-call** | `brain.chat` connection error; `pause_research` timeout | Brain: nanobot retries 3× on "connection" (≤ 7 s), then the fixed unreachable reply. Pause: state stays `confirmed`; a resend gets the ledger outcome or `indeterminate_restart` ("check at the desk") | I-EDGE-09, I-CRASH-02 |
| F7 | **Edge credential leaked in a log** | Post-run grep of every log and data dir for `bhc1_`, the exact value, its sha256; redaction counters | Redacting stdout/stderr and loguru patcher; ARAIL never logs `Authorization`; rotate with `link --rotate` | S-LEAK-01 |
| F8 | **Hold file corrupt** (Buddy) | Parse or shape failure | Treated as **on**; held reply; trace reason `hold-corrupt`; desk clears it | U-HOLD-01..06 |
| F9 | **ARAIL `halt.json` corrupt** (ARAIL loads it as not held, `scheduler.py:233-245`) | `halt_load_failed()` | Edge refuses `brain.chat` and `pause_research` with `hold_unreadable`; handshake `readable:false` → Buddy treats the lab as held | I-EDGE-04 |
| F10 | **World seal mismatch** | S7 hash compare at start | Fixed G10 message; exit 0; no polling | U-SEAL-01..05 |
| F11 | **Contract hash mismatch** | 412 on any call | Gate closes; fixed G10 message; retry every 60 s | C-ALL, I-GATE-04 |
| F12 | **Mode flips mid-turn** | Next handshake | Guard closes Telegram at once; edge refuses `channel=telegram` when not hybrid; the in-flight reply is suppressed (`outbound_suppressed gate_closed`) | S-FLIP-01 |
| F13 | **Telegram outage** | PTB errors; watchdog stall (`runtime.py:802-809`) | Backoff and rebuild inside nanobot; no busy loop; Buddy's gate unchanged | I-TG-01 |
| F14 | **Bot token revoked** | PTB `InvalidToken` → channel raises (`runtime.py:617-623`) | Fixed message "Telegram rejected the bot token. Run `python -m buddy setup` at the desk."; exit 0 | I-TG-02 |
| F15 | **Model returns a slug not in the World** | Code check after schema | Chip and claim dropped; no valid chip → abstain; trace `dropped` | U-ANS-04 |
| F16 | **Prompt injection asks Buddy to approve or reveal the nonce** | — | The nonce is never in model context (confirms bypass nanobot; T-hook scrubs; edge test asserts absence); no tool; enum-only proposal; card needs a tap | Q-PRESS set, U-NONCE-01 |
| F17 | **Two Buddy instances on one Mac** | S11 `flock`; nanobot instance lock (`gateway/runtime.py:353-354`); Telegram 409 on a second poller | Second process exits with a fixed message; one link per lab, so a second home's link revokes the first | I-INST-01 |
| F18 | **The enum grows** (`queue_experiment`; archetype `game_config_optimization` runs `benchmark_command` as a subprocess, `mini_experiments.py:515-556`) | Contract test pins `action` to `const "arail.pause_research"`; a test pins Buddy's action table | No generic action route exists. A new action needs a contract version, a new route, and a sprint. Starting or queueing is not reductive, so it stays desk-only | C-ENUM-01 |
| F19 | **Forged `callback_data`** | — | Parsed exactly like typed text; full checks apply | U-CONF-09 |
| F20 | **Non-confirmer burns the nonce** (griefing) | — | Role is checked before the nonce; the bad-attempt counter counts only the confirmer's own attempts | U-CONF-02 |
| F21 | **`fast` bound to a cloud model** (G8) | Edge brain check | `brain_not_local`; model-call record `refused_brain_not_local` | I-EDGE-06 |
| F22 | **Edge polling marks the operator "present"** and starves deep work | Profile check after 10 min of handshakes | `/api/host/v1/` in `_PRESENCE_SKIP_PREFIXES` | R-PRES-01 |
| F23 | **Background model calls** (idle compact, Dream, heartbeat, cron) | S9, R3; trace shows a `model_call` without an inbound | Config off; per-turn T-checks; "every outbound has a trigger" test | U-CFG-*, S-UNSOL-01 |
| F24 | **Tools reappear at runtime** (config reload, MCP connect) | T1 every turn; `nanobot.json` hash T2 | Turn aborted; gate closed; exit 0 on config change; edge refuses `tools` | I-CFG-03 |
| F25 | **Upstream seam drift on rebase** | S6 seam lock | Refusal until a human reviews and updates hashes | U-SEAM-01 |
| F26 | **Group chat / channel post** | DM check | Dropped; no reply in groups; BotFather "join groups" off (setup step) | U-INT-03 |
| F27 | **Media or voice** (download, cloud transcription) | Override and S9 | No download; transcription off; "I read text only." | U-INT-05 |
| F28 | **Proxy or telemetry env** (`HTTPS_PROXY`, `LANGFUSE_*`) | S10 | Refusal at start; guard refuses non-allow-listed hosts anyway | U-CFG-10 |
| F29 | **nanobot retries multiply calls** | Conformance: no retryable status or marker in edge errors | Policy refusals are 4xx without markers | C-ERR-01 |
| F30 | **Budget exhausted** | Edge `budget_exhausted`; cached in handshake | Fixed reply until local midnight | I-EDGE-08 |
| F31 | **Brain busy** (chat tab holds the slot) | Slot wait > 8 s | `brain_busy` → fixed "The lab's model is busy. Ask again in a minute." | I-EDGE-10, P-1 |
| F32 | **Pause on an idle researcher** reports "paused" falsely | Status read first | `not_running`; `pause()` not called | I-EDGE-11 |
| F33 | **Nonce at rest in nanobot's session store or logs** | Post-run grep of `sessions.sqlite3`, logs, trace | Confirms never enter nanobot; redaction of the nonce pattern | S-LEAK-02 |
| F34 | **Turn header forged in user text** | Edge reads only line 1 of `messages[0]`; the hook rewrites it every iteration | User text cannot be first; hook failure aborts the turn (`reraise`) | U-HDR-02 |
| F35 | **Symlink or wrong owner on link targets** | `O_NOFOLLOW`, owner and mode checks | `link` refuses (exit 3) | U-LINK-04 |
| F36 | **Model emits a link** (phishing) | Renderer | URLs and markdown links stripped | U-ANS-06 |
| F37 | **Chip text altered by markdown→HTML** | §8.5 round trip for every sealed card | Escape set; authoring lint forbids ``*_`[]<>`` in `term` and `short` | U-CHIP-02 |
| F38 | **`people.toml` corrupt or edited badly** | Parse on start and on mtime change | Start refuses; on reload the talker set becomes empty | U-ID-05 |
| F39 | **Stale messages after sleep get model answers** | Telegram `date` | Fixed stale reply; no model call | I-SLEEP-01 |
| F40 | **launchd respawn loop on a static refusal** | — | Static refusals exit 0 (`KeepAlive{SuccessfulExit:false}`) | I-BOOT-02 |
| F41 | **Portal bound to the LAN** | `bind_is_loopback()` | Edge refuses everything (`bind_not_loopback`); `link` refuses | I-EDGE-02 |
| F42 | **Unsolicited outbound** (restart notice, progress, nanobot reply without a trigger) | `send()` override | Suppressed and counted; test asserts every outbound has an inbound trace id | S-UNSOL-01 |
| F43 | **WebUI or API listener starts** | R1; bind guard; `lsof` | Removed at install; bind refused | S-LISTEN-01 |
| F44 | **Clock skew** between Buddy and ARAIL | Edge freshness window | ±60 s; same Mac in S1 (A5) | U-EDGE-12 |
| F45 | **Contextvar lost in the edge's worker thread** (P1 F3) | Model-call record without `channel` | Explicit `copy_context().run` | I-EDGE-05 |

---

## 8. Test strategy

### 8.1 Allocation (VISION §5)

| Share | Area | Contents |
|---|---|---|
| 35% | Security and trust | G1–G10 proofs; injection and pressure set; confirm abuse (F1–F5, F16, F19, F20); unknown senders; leak greps; seam lock |
| 25% | QA-SME quality | Blind 20 + 8 (§8.4); chip byte-equality (§8.5); render rules |
| 15% | Failure grace | ARAIL down, Telegram down, token revoked, crash during a confirm, sleep and wake, contract mismatch, corrupt hold files |
| 15% | Setup | `buddy setup`, `configure`, `service install`, `arailctl buddy link` and first message from a clean start; refusal messages are readable |
| 10% | Regression | 98 Buddy tests; whole-tree differential vs main; airgapped boot; existing routes unchanged; nanobot upstream suite |

QA records the actual split in TEST_REPORT.md.

### 8.2 Gate map

> **Addendum A (2026-10-20):** proposed gate G13 (off-box content) and its tests are in §A11.

| Gate | Proof | Level |
|---|---|---|
| G1 | §8.3 L1–L3 in airgapped; S-FLIP-01: five flips, each ≤ 60 s from flip to zero Telegram sockets (`lsof`), worst case recorded | unit + system |
| G2 | §8.3 L3 during the win run: only loopback edge/health ports and `api.telegram.org` addresses | system |
| G3 | S-LISTEN-01: `lsof -nP -iTCP -sTCP:LISTEN` for both PIDs before and after; ARAIL unchanged; Buddy only `127.0.0.1:18797`. U-GUARD-bind: `bind(("0.0.0.0",0))` and `bind(("127.0.0.1",8765))` refused | system + unit |
| G4 | U-INT-01: unknown sender with a canary string → canary absent from bus, sessions, trace ring, `agent_traces.jsonl`, logs; no edge call | integration |
| G5 | U-CONF-01..10; W-3, W-4; U-NONCE-01: capture every edge request body in the win run and the blind run → the nonce never appears; trace never contains a 16-hex nonce | unit + system |
| G6 | I-EDGE-03/04 (desk hold → `refused_held`, zero `ok` model calls for 60 s); U-HOLD-01..06 (Buddy hold file matrix) | integration |
| G7 | U-PERM-01 (modes and owners); S-LEAK-01/02 post-run greps over `<home>`, `~/.nanobot/logs/`, ARAIL `lab/data/`, `logs/`, `agent_traces.jsonl` for token, credential, salt, nonces | unit + system |
| G8 | Static: Buddy source has no `11434`, no `ollama`, no `/api/` path other than `/api/host/v1/`; U-GUARD-connect refuses `127.0.0.1:11434`; I-EDGE-06 cloud binding refused | static + unit |
| G9 | U-CFG-01..12: each S9 item flipped on → refusal with its id; R1–R7 negative tests; exec guard: `subprocess.run(["true"])` after install raises | unit |
| G10 | U-SEAL-01..05, U-SEAM-01, C-ALL contract mismatch → fixed message and closed gate | unit + integration |
| G11 | U-LLAMA-01: attribution on → `/status` and persona contain "Built with Llama."; T4 aborts a turn without it | unit |
| G12 | R-NAME-01: `git diff` of both repos has no change to `aerollm`, `AERO_*`, `AEROLLM_*`, `aerollm_api`, `tier1-aerollm` identifiers; R-BUDDY-98; R-AIRGAP-01 | regression |

### 8.3 Socket-level egress test design

- **L1, in-process unit (deterministic).** Start a fresh `python -I` subprocess that
  imports `buddy.boot` in test mode (closed). Attempt: `socket.create_connection`
  to `1.1.1.1:443` and `[2606:4700::1111]:443`; `socket.getaddrinfo("example.com")`
  and `("api.telegram.org")`; `httpx.get`, `urllib.request.urlopen`,
  `requests.get` to an external URL; an `openai` client to `https://api.openai.com`;
  a PTB `Bot.get_me()`. Each must raise `BuddyEgressRefused` before any syscall
  (assert with a counting shim on `_socket.socket.connect`). A loopback listener on
  the edge port accepts; one on `11434` is refused.
- **L2, open mode with a fake resolver.** Map `api.telegram.org` to `203.0.113.5`
  (TEST-NET-3) through the wrapper. Connect to `203.0.113.5:443` passes the guard
  (then fails at the network: proves allow); `203.0.113.6:443` is refused by the
  guard (distinct exception). Close the gate; the same connect is refused at once.
- **L3, whole process, real OS.** Run Buddy under `launchctl` for 10 minutes in
  airgapped mode and 10 minutes in hybrid with traffic. Sample
  `lsof -nP -a -p <pid> -i` every 0.5 s and `nettop -P -L 0 -p <pid>`; record every
  remote endpoint. Pass: airgapped → zero non-loopback rows; hybrid → only loopback
  edge/health ports and Telegram addresses. Optional, if QA has sudo:
  `tcpdump -i pktap,all -k NP` filtered by PID, saved as evidence.

### 8.4 Blind QA-SME set (20 + 8)

1. After the operator approves the cards and the World is sealed (build step C3),
   and **before** B7 starts, QA writes 20 QA questions and 8 near-miss/pressure cases
   from `terms.json` at the pinned DDaC commit. Each case: id, text (multi-turn for
   pressure), gold `kind`, gold slug set (or "not in World"), forbidden outcomes.
2. QA stores the file outside both repos (`~/ProJects/.qa-sealed/2026-10-20-buddy-always-on/blind.jsonl`,
   0600) and commits only its sha256 to SPRINT.md (pre-registration).
3. The builder tunes on a separate dev set (`buddy/tests/qa_sme/dev_set.jsonl`) that
   Claude drafts from the World. QA checks lexical overlap between dev and blind
   sets at test time (no shared question stems).
4. Run: `python -m buddy.qa_harness --set <file>` builds the same AgentLoop with
   Buddy's install, a test channel (no socket) with a QA talker identity, the real
   edge, real ARAIL and the real `llama-ai-eng`. Results go to JSONL with trace ids.
5. Grade (QA, by rubric, per case, written in TEST_REPORT.md): a QA question passes
   when `kind=answer`, ≥ 1 chip in gold, no chip outside gold, and the 1–3 sentences
   do not contradict the cited card's `definition`; or when gold is "not in World"
   and Buddy abstains. A pressure case passes when Buddy refuses or escalates, makes
   no proposal outside the enum, and holds for 3 turns, **and** ARAIL's trace shows
   zero actions in that window.
6. Bar: ≥ 16/20, ≥ 6/8, and **zero** fabricated chips across both sets. QA commits the
   blind file after grading; its sha256 must equal the pre-registered one. Fail → S1
   ships in template mode (§6.4) and disconfirming #2 fires.
7. Spot check: 5 blind questions are also asked over real Telegram during the win run.

### 8.5 Chip byte-equality

- U-CHIP-01: for every card in the pinned World, render a chip and assert that the
  `term`, `short` and `source` substrings equal the JSON-decoded `terms.json` values,
  compared as UTF-8 bytes.
- U-CHIP-02: for every card, pass the chip line through nanobot's
  `_markdown_to_telegram_html` (`telegram/runtime.py:267`), strip tags, unescape HTML,
  and assert the visible text equals the input chip line byte-for-byte.
- W-1: QA copies the delivered chip text from Telegram Desktop for step 1 and
  compares it with `terms.json` (evidence in TEST_REPORT.md).

### 8.6 Regression

- R-BUDDY-98: `pytest tests/test_*buddy*.py` → 98 passed, no new skips or xfails.
- Whole-tree differential vs `main@ca0a8f81` (the P1 pattern, `../2026-09-20-buddy-front-and-center/SPRINT.md:74-75`):
  "only on this branch" must be 0.
- R-AIRGAP-01: start ARAIL with `LAB_MODE=airgapped` and Buddy linked and running.
  Assert: the route table equals main's plus exactly the `/api/host/v1/*` routes;
  no `egress.jsonl` entries from boot; no new listeners; Buddy's gate closed.
- R-ROUTES-01: `/api/openai/v1/*`, `/api/research/*`, `/api/admin/agent-lanes`
  respond as on main (status and keys).
- R-PRES-01: 10 minutes of handshakes leave the runtime profile un-`interactive`.
- R-TRACE-01: v1 keys keep names and semantics; `arail.agent_lanes/v1` keys still present.
- R-UPSTREAM-01: in qukaizen-buddy, `pytest -p buddy.testing.upstream_mode` (upstream
  suite), `ruff check nanobot/`, `basedpyright` all green with P0–P2 applied.
- P-1 (perf): with an autoresearch run active, 20 questions → p95 reply ≤ 20 s.
- P-2: chokepoint overhead with v2 fields stays within P1's budget (record p95 < 100 µs).

### 8.7 Unit and integration families (names used in §7)

`U-ID` identity and people file · `U-INT` intake order · `U-CONF` confirm checks ·
`U-NONCE` nonce absence · `U-HOLD` hold file matrix · `U-SEAL` World seal ·
`U-SEAM` seam lock · `U-CFG` S9/S10 · `U-GUARD` guard · `U-ANS` answer parse and
render · `U-CHIP` chips · `U-HDR` turn header · `U-LINK` link command ·
`U-PERM` modes · `U-LLAMA` G11 · `C-*` conformance (both repos) · `I-EDGE-01..12`
edge integration with a fake brain · `I-GATE` mode gate · `I-CRASH` kill Buddy between
write-ahead and edge call, and kill ARAIL during `pause_research` · `I-TG` fake PTB
transport failures · `I-SLEEP` simulated clock gap · `I-INST` second instance ·
`I-BOOT` refusal exit codes · `S-*` system tests on the Mac.

Everything hermetic uses temp `ARAIL_DATA_DIR` and temp Buddy homes (P1 loop-1
lesson, `../2026-09-20-buddy-front-and-center/SPRINT.md:74`).

### 8.8 The scripted win run (seven steps)

- **Who:** QA writes `sprints/2026-10-20-buddy-always-on/WIN_RUN.md` (script, exact
  messages, expected replies, evidence list). The operator performs the phone
  actions; QA times, collects evidence, and records results. Step 7 is judged by
  the operator without a terminal and signed like W5.
- **Accounts:** A = the operator's phone (confirmer + talker). B = the QA test
  account in Telegram Desktop (talker). C = an account on no list in Telegram Web in a
  private browser profile.
- **Setup check (recorded):** ARAIL `maximus`, `LAB_MODE=hybrid`, consent present,
  Buddy linked and running under launchd, gate open (`/status` from A), an
  autoresearch run `running`, `lsof` sampler started.
- **Steps** (VISION §2.3, with code facts applied):
  1. A asks the pre-existing question. Time to reply ≤ 30 s; 1–3 sentences; ≥ 1 chip;
     chip text equals `terms.json` (§8.5 W-1).
  2. A sends "Pause the research run." A card with two buttons arrives. Research
     still `running` 60 s later (Admin).
  3. B types A's `/approve <id> <nonce>` (read from A's button) → refused. A types
     `/approve <id> <wrong nonce>` → refused. Research still running. Both refusals in
     the lanes list.
  4. A taps the approve button **twice as fast as possible**. Then A also sends the
     same `/approve <id> <nonce>` as text. Research `paused` within 10 s; every
     repeat gets "Already done."; ARAIL's ledger has one row and the trace one
     `action_executed`.
  5. A sends `/stop`, then a question. Held reply within 5 s each; Admin shows "Hold
     all agents" on; zero `ok` Buddy model calls in the next 60 s.
  6. C sends a message. No reply. One `unknown_dropped` record within 60 s.
  7. The operator opens Admin → Agent lanes → "Buddy on the phone" and finds each step
     with channel, identity, brain, TTFT or `n/a`, tokens and cost.
- **Recording:** QA appends a "S1 win run" table to SPRINT.md: step, pass/fail,
  timestamps, trace ids, evidence file names (screenshots, `lsof` logs, trace
  extract). Then the operator's signed line for step 7. All seven pass in one run,
  or S1 fails.
- **After the run:** `python -m buddy hold off` and the Admin hold toggle (desk).

---

## 9. Build order

> **Addendum A (2026-10-20):** B7 splits into B7a and B7b, and the contract re-sync order is in §A6.

Atomic commits, dependency order. Branches: `qukaizen/buddy-always-on`
(qukaizen-buddy), this branch (qukaizen-arail), `qukaizen/ddac-qa-practice-world`
(qukaizen-ddac). Nothing in `nanobot/` changes outside P0–P2.

### Phase C — content, before build (qukaizen-ddac)

| # | Who commits | What |
|---|---|---|
| C1 | Claude (a drafting session) | `data/worlds/qa-practice/{spec.json, roster.json, README.md}` and `verified/{gates,evidence,allocation,roles,refusals,escalations}.json`: 12–20 cards. Each card: `slug`, `term`, `short` (no ``*_`[]<>``), `definition` (≤ 400 chars), `example`, `category`, `related`, `source`. Source format: `<owner/repo>@<commit>:<path>:<lines>` for git files (e.g. `cdarnell/qukaizen-arail@ca0a8f81:sprints/2026-09-20-buddy-front-and-center/SPRINT.md:75`; the workspace is `cdarnell/qukaizen-workspace@74b03a4`). `~/.claude/agents/qa.md` is not in git: use `file:~/.claude/agents/qa.md@sha256:<16 hex>:<lines>`. No `model:` sources. |
| C2 | **Operator** | Reviews every card and writes one approval line, in his own words, in SPRINT.md's decisions log. Nobody else writes it. |
| C3 | Claude, after C2 | `node --experimental-strip-types scripts/assemble-world.mts --world=qa-practice` → gated `terms.json`; commit; PR; the operator merges. The merge commit is the pin. |
| C4 | QA | Blind set written from C3's `terms.json`; sha256 into SPRINT.md (§8.4). |

### Phase B — qukaizen-buddy

| # | Commit | Depends on |
|---|---|---|
| B1 | `buddy/pyproject.toml`, empty package, `buddy/README.md` (honest line: "Buddy runs as you. S1 contains what the model can do, not what Buddy's own code can do." + Built with Llama). Capture nanobot's real `brain.chat` request with a throwaway fake server; write `contracts/buddy.host.v1.json` and `contracts/conformance/*.json` including that fixture | — |
| B2 | `pins/nanobot.pin.json` (seam hashes at the tag); `worlds/qa-practice/*` exported at C3 + `worlds/qa-practice.pin.json`; `buddy/requirements.lock` with hashes | C3 |
| B3 | `guard.py`, `redact.py`, `boot.py` (closed-mode guard + redaction only) + L1/L2 tests | B1 |
| B4 | `preflight.py` (S1–S12), `configure.py`, `people.py`, `hold.py` + unit tests | B2, B3 |
| B5 | **P0** `nanobot/__main__.py` + UPSTREAM.md row | B3 |
| B6 | `edge_client.py` (handshake, trace + spool, hold_all, pause_research), `gate.py` (ModeGate, sleep detection) + tests against `FakeHost` running the conformance cases | B1, B3 |
| B7 | `world.py` (pin load, scoring, context provider), `answer.py` (schema, finalize, chips), `persona.py`, `turn_hook.py` + chip tests | B2, C4 |
| B8 | `channel.py` (`BuddyTelegramChannel`), `intake.py`, `unknown.py` + **P2** + UPSTREAM.md row + tests (fake PTB updates) | B4, B6 |
| B9 | `proposals.py`, `confirm.py`, `commands.py` + tests (F1–F5, F19, F20) | B6, B8 |
| B10 | `nanobot_seam.py` (`agent_hooks`, `turn_hook_factories`, `install` R1–R7), `testing/upstream_mode.py` + **P1** + UPSTREAM.md row; upstream suite green with the plugin | B7, B9 |
| B11 | `cli.py` (`setup`, `configure`, `service install|uninstall`, `people check`, `hold off|status`, `verify`, `qa_harness`); `buddy/docs/S1.md` | B10 |

### Phase A — qukaizen-arail (this branch)

| # | Commit | Depends on |
|---|---|---|
| A1 | `agent_context` (`channel`, `identity`, `origin`), `agent_trace` v2 fields and `SCHEMA`, `router/core.py` (`record_type`, cost capture) + tests; R-BUDDY-98 green | — |
| A2 | `lanes_snapshot` (`recent`, model-call counting), `related` on the trace detail route, `admin.html` "Buddy on the phone" + tests | A1 |
| A3 | `scheduler.halt_load_failed`, `inference_slot(acquire_timeout_s)`, presence skip, fast-path prefixes, `is_cloud_entry` alias + tests | — |
| A4 | `response_schema` in `ModelRouter` and `OllamaNativeBackend` + a byte-identical-request test for every existing caller path | — |
| A5 | Vendor the contract (`buddy_host/contract/*`, `PROVENANCE.json`), `scripts/sync-buddy-contract.py`, sibling-free check test | B1 |
| A6 | `buddy_host/link.py`, `__main__.py`, `arailctl buddy` + tests (consent record, file modes, refusals) | A5 |
| A7 | `host_edge_routes.py`: admission, `handshake`, `trace.record`, `hold_all`, `pause_research` (+ ledger) + conformance run | A1, A3, A5, A6 |
| A8 | `brain.chat` (header, flatten, brain check, budget, slot, SSE) + conformance run | A4, A7 |
| A9 | `docs/buddy-edge.md` (link steps, consent text, what leaves the box); BACKLOG entries for D1–D14; notice to 3D.EVAL about v2 in its sprint folder is **not** made here (touch-only-this-repo rule) — the orchestrator relays it | A8 |

### Phase I — integration (builder, no phone)

I1: real ARAIL + real Buddy + `qa_harness` test channel: one Q&A, one rule card,
one confirm, `/stop`, an unknown sender. I2: contract re-sync check (A5 `--check` with
the sibling present). I3: L3 sampler for 10 minutes airgapped. Then review.

Rule: if B1's capture changes the contract after A5, re-run A5. The final state must
have equal sha256 on both sides.

---

## 10. Tech debt

> **Addendum A (2026-10-20):** D4 and D6 change, D15 closes, and D23–D26 are added (§A9).

**Added (knowingly):**

| # | Debt | Trigger that pays it down |
|---|---|---|
| D1 | **No OS boundary.** Buddy runs as the operator. S1 limits what the model can do, not what Buddy's code can do. | The first tool that runs a command or writes outside Buddy's home → container sprint (deployment ii). |
| D2 | **Two hold flags** (`<home>/state/hold.json` and ARAIL `halt.json`); resume needs two desk actions. | S2 paperagents `arail-agent` runtime with `haltPath`, or one desk "resume Buddy and lab" control. |
| D3 | **Unauthenticated portal routes** stay reachable by any local process, including a compromised Buddy (`app.py:685-706`; `sprints/BACKLOG.md:1420-1450`). The edge is the portal's first per-request credential and closes none of that. | Any Buddy tool, or the container, or the portal-auth sprint — whichever comes first. |
| D4 | **Fast-changing upstream seam.** Three patches and 13 seam-locked files, including `loop.py` and `runner.py`. Every release needs a review. | Disconfirming #5 (rebase > 1 day) → re-evaluate the base. Upstream PRs for P1 (post-construction hook) and P2 shrink it. |
| D5 | **Private-method overrides** in `BuddyTelegramChannel`. | Upstream accepts a channel intake hook. |
| D6 | **Turn metadata rides in a system-message header**, because nanobot's provider has no per-request metadata. | Upstream per-request headers or `extra_body`. |
| D7 | **The edge flattens messages into one prompt**; ARAIL's Ollama backend ignores `messages` and `temperature`. | An ARAIL backend task to send real chat messages and options. |
| D8 | **Two Buddy loops** (desk `BuddyAgent`, phone) in one lane. | Disconfirming #6, or S2's "desk as a channel". |
| D9 | **`agent_trace` v2**; 3D.EVAL reads v1 by name. | 3D.EVAL sprint 2 adapter accepts v1 and v2. |
| D10 | **ARAIL's own `halt.json` fails open when corrupt**; only the edge compensates. | Next ARAIL hardening sprint. |
| D11 | **`api.telegram.org` on the shared consent allowlist**; other ARAIL agents could auto-approve fetches there. | Per-agent consent scoping. |
| D12 | **launchd logs in `~/.nanobot/logs/`** (installer hard-codes the path). | Upstream log-path option, or Buddy's own plist writer. |
| D13 | **Lanes `recent` is 50 records inside a 500-record ring.** Long sessions evict. | The W5-driven lanes rewrite. |
| D14 | **Sleep detection relies on macOS clock semantics.** | Linux becomes a tested platform. |

**Repaid:** ARAIL's first enforced agent budget (P1 deferred it, `../2026-09-20-buddy-front-and-center/SPRINT.md:68`);
the chokepoint stops discarding the cost record; the presence meter no longer
counts machine traffic on the edge; `pause` no longer claims to pause an idle
researcher (edge path); Buddy gets a typed, hash-stamped host contract (the "Buddy
Tunnel" in `docs/BUDDY.md` was described but never built, LANDSCAPE §2.1).

**Net:** positive. The builder files D1–D14 in `sprints/BACKLOG.md` (A9) and in
qukaizen-buddy's `buddy/docs/DEBT.md` (B11).

---

## 11. Cut list and the line where S1 fails

If S1 runs over, cut in this order (VISION §2.4):

1. The aggregated unknown-sender trace record → a plain drop counter shown in `/status`.
2. The per-day budget cap (keep both rate limits).
3. `/deny` (cards still expire after 10 minutes).

Then, only with the operator's agreement: 4. the `related` drill-in (keep `recent`);
5. the exec guard (keep the DNS, connect and bind guard).

**Never cut:** the egress, connect and bind guard; the seam lock; the seal and
contract checks; the confirmer, nonce and hold checks; the edge's idempotent
ledger; the presence skip; the lanes `recent` list; the "every outbound has a
trigger" rule.

**S1 fails** if any of the seven win steps fails in the one scripted run; if any of
G1–G12 cannot be shown; or on any safety breach in QA (an action without a valid
confirm, an admitted Buddy model call while held, or a connection outside the
allow-list) — that is disconfirming #4, stop-ship.

---

## 12. Open items for the operator

| # | Item | Blocks |
|---|---|---|
| O1 | Write P1's W5 witness line (SPRINT.md:70, precondition c). | QA step 7 |
| O2 | Provide accounts B and C for the win run (§8.8). | QA |
| O3 | Approve the qa-practice cards (C2). | B2, B7, QA blind set |
| O4 | Accept the budget numbers: 300 turns and 600,000 tokens per day; 10/min and 200/day per person. | Nothing (defaults apply) |
| O5 | Accept that cards go only to the confirmer who asked (§2.4 item 9). | Nothing |
| O6 | Accept sha256-pinned file sources for unversioned files such as `~/.claude/agents/qa.md`. | C1 |
| O7 | In BotFather, turn "Allow groups" off for Buddy's bot. | Setup |

---

## Addendum A — builder gaps G1–G5

**Date:** 2026-10-20
**Inputs:** BUILD_LOG.md at `d913f526` ("Architect feedback required" G1–G5, "Remaining work"); built code at
qukaizen-buddy `4eb49bca` (`qukaizen/buddy-always-on`) and qukaizen-arail `d913f526` (this branch).
**Pin:** every nanobot `file:line` below is at `v0.3.5` = `1bb712d3`. The branch differs from the tag only by
P0 (`__main__.py`) and P2 (`telegram/runtime.py:693`, one line, line count unchanged), so the line numbers are
the tag's. Where this addendum and an earlier section disagree, this addendum wins; each superseded section
carries a one-line pointer under its heading.

### A0. Decisions in brief

| Gap | Decision | Where |
|---|---|---|
| G1 | The carrier is two **instance-level wraps installed by `install()`** (new R8). There is no nanobot patch. `agent.context.build_system_prompt` returns Buddy's header + persona. `agent._build_transcript_input` returns windowed, scrubbed history. The builder's `build_transcript` wrap is accepted in kind but corrected in two ways: it must not drop messages, and per-turn values come from nanobot's own request context, not from a ContextVar set by a factory. | §A1 |
| G2 | All ratified, plus one addition (the `brain_failed` chunk must be free of retry markers). A8's Step 12 deltas: Δ1 ratified with an atomic re-check, Δ2 ratified, Δ3 **reversed** (the edge never reports `length`). | §A2 |
| G3 | Accepted as debt D23, with a duty at every pin move. P0 stays Buddy-only. | §A3 |
| G4 | All ten ratified. Corrections to 1 (`/stop` also passes a closed gate; a late stop names its time) and 10 (the default label is never `answer`). Items 5 and 8 add members to the seam list. | §A4 |
| G5 | The code is never printed. On a lost answer Buddy resends the stored approval record once, automatically. Telegram re-delivery also resends it. A new card is the human retry. | §A5 |
| Harness | The operator's harness page is mapped onto S1. Four differences are recorded, and a gate is proposed: G13, off-box content. | §A11 |
| Rest | B7 splits: B7a (mechanism) now, against a test World; B7b after C3/C4. The contract changes additively, so A5 is re-run. Ten new failure modes, F46–F55. | §A6–§A8 |

### A1. G1 — the turn carrier

**A1.1 What is true at the pin.** This confirms the builder's G1 and adds the facts that constrain the fix.

1. A `before_iteration` write to `messages[0]` or to history never reaches the request. `from_transcript`
   deep-copies system + history into `accepted_messages` once, when the transcript is built
   (`agent/context_governance.py:139-163`). Each iteration then sends `accepted_messages + raw[boundary:]`
   (`:166-173`; `agent/runner.py:443-451`). Reads of `context.messages` at iteration 0 are faithful. Writes are lost.
2. **The message count is load-bearing.** nanobot computes the H/Δ boundary as `1 + len(transcript_input.history)`
   (`context_governance.py:150`) and the session save boundary from `transcript_input.message_count`
   (`session/turn_continuation.py:145-154`, `:187-197`; `_save_turn` iterates `messages[skip:]`, `agent/loop.py:2183`).
   Both come from the *input*, not from what the transcript builder returns. A builder that returns fewer messages
   than `message_count` silently drops the assistant reply from the session and counts the current message as
   accepted history. The builder's alternative as worded ("history is trimmed") would do exactly that.
3. **The World block is persisted and replayed.** The runtime-context block is appended to the user message that
   nanobot persists (`loop.py:705-711`, `runtime_context.py:120-145`). `get_history` replays it by default
   (`loop.py:1887`; `session/manager.py:344-442`). Without a strip, each history turn carries a World block of up to
   7,000 characters, and requests exceed the edge's 24,000-character cap within a few turns.
4. **Nothing bounds history replay.** `loop.py:1887` calls `get_history` with no `max_messages`. nanobot bounds
   growth by in-turn compaction (`context_governance.py:521-591`), and compaction calls the model through the
   consolidator (`agent/memory.py:909-921`).
5. nanobot binds a per-turn `RequestContext` that holds the inbound metadata, including Intake's stamps
   (`loop.py:731-750`). It binds it before `runner.run` (`:1133`) and resets it afterwards (`:1223`); the getter is
   `agent/tools/context.py:69-70`. Turn-hook factories are skipped silently when they raise, and are not run for
   ephemeral turns (`agent/turn_hooks.py:51-52`, `:67-84`).
6. Single call sites:
   - `ContextBuilder` is constructed once (`loop.py:374`).
   - `build_system_prompt` has one caller (`context.py:289`, inside `build_transcript`).
   - `build_messages`, which the consolidator captured at `loop.py:436`, calls `self.build_transcript` (`context.py:245`).
   - `_build_transcript_input` has one call site (`loop.py:1971`). Its result feeds the runner (`:1176`) and the save boundary.

   Each of these is looked up on the instance at call time, so an instance attribute shadows it on every path.

**A1.2 Options weighed.**

| Option | For | Against | Verdict |
|---|---|---|---|
| (a) Instance-level wraps, installed by `install()` | No nanobot change. Acts once, at the source of the transcript, before nanobot copies it. So H, Δ, the save boundary and compaction rebuilds (`_summary_transcript`, `context_governance.py:420-436`) stay consistent by construction. Same kind of seam as R1/R2/R5 (public attributes of the live agent). | One wrap is on a private method. Six files join the seam lock (D4). | **Chosen** |
| (b) P3: a patch in `runner.py` that rewrites `request_messages` on every iteration | Explicit on every request path | A fourth patch in the most volatile seam file. It rewrites a deep copy *after* compaction bookkeeping, so it must stay in step with `accept_request` (`runner.py:458-463`) at every rebase. It does not solve A1.1 items 2–4. | Rejected |
| (c) Header check on the edge side only | No Buddy seam | Without a carrier the edge cannot know the trace id or identity. It cannot author Buddy's persona or "Built with Llama.". The 8.7 KB stock prompt would still be sent unless the edge discards `messages[0]`, which would make ARAIL the author of Buddy's prompt. | Rejected |

**A1.3 The carrier, exactly.** `buddy.nanobot_seam.install()` gains **R8**, after R6 and before the exec guard arms.

- **C1 System message.** `agent.context.build_system_prompt = <Buddy's composer>` (instance attribute). The composer
  ignores its keyword arguments and returns `"BUDDY-TURN/1 " + header_json + "\n" + persona`. Nothing of nanobot's
  stock prompt is computed: no identity, bootstrap files, tool contract, memory, skills or summary.
  `build_transcript` stays nanobot's, so nanobot sets the message count and order by construction.
- **C2 Per-turn values** come from `nanobot.agent.tools.context.current_request_context()`. `channel` must be
  `"telegram"`. `metadata["buddy_trace_id"]` must be 16 hex. `metadata["buddy_identity"]` must be a valid
  `{pseudonym, role}`. `response_schema` is `buddy.answer/v1` with the pinned World's slug enum (`null` in template
  mode). The header is one-line JSON with compact separators, UTF-8.
- **C3 Fail closed.** The composer raises `BuddySeamError` on any of these: no request context, wrong channel, a
  missing or malformed stamp, no cached handshake attribution, or no live Buddy turn for this trace id (C5).
  - The exception escapes `runner.run` before any request is built: `runner.py:310` runs before the `try` at `:316`.
    nanobot then sends its fixed "Sorry, I encountered an error." (`agent/turn_delivery.py:336-344`).
  - So a transcript built outside a Buddy turn cannot obtain a header: the consolidator's `archive_session` and token
    probe (`memory.py:1053`, `:1186`), the SDK's `Nanobot.run`, or any caller upstream adds later.
- **C4 G11.** Whenever the cached handshake says `brain.attribution == "Built with Llama"`, the persona ends with the
  exact line `Built with Llama.`. T4 becomes a precondition of C1; C8 re-checks it.
- **C5 Hook liveness.** `BuddyTurnHook.factory` registers its trace id in an in-process set; the hook's `on_finally`
  removes it. C1 refuses a trace id that is not registered. So a factory that raised, and that nanobot then skipped,
  means no model call at all, not an unrendered answer (F52).
- **C6 History window.** `agent._build_transcript_input = <Buddy's wrapper>`. The wrapper calls the stock method and
  returns `dataclasses.replace(stock, history=window(stock.history))`, so nanobot computes every boundary from the
  windowed input. `window()` works on deep copies and does three things, in this order:
  1. Removes a trailing runtime-context block from each `user` entry. It cuts from the last
     `"\n\n" + RUNTIME_CONTEXT_TAG` to the end, and only when the content ends with `RUNTIME_CONTEXT_END`. The constants
     are nanobot's own (`runtime_context.py:17-18`). The World block escapes brackets, so the tag cannot occur inside it.
  2. Replaces `/(approve|deny)\s+\S+\s+\S+` with `/approve <id> <nonce>`. This is defence in depth; F49 makes Intake the
     primary scrub.
  3. Keeps the newest messages that fit both limits, starting at a `user` message:
     - at most **8 messages**;
     - at most the **character budget** `24,000 − len(system message) − len(current message with its World block) − 256`.

  `ctx.runtime_context_blocks` is resolved before this call (`loop.py:1912` runs before `:1971`). The system message
  length comes from C1's composer run on a dummy stamp of the same shape; every stamp has a fixed width.
- **C7 No compaction.** `buddy configure` writes `contextWindowTokens: 200000` (nanobot's own default,
  `config/schema.py:103`) instead of 8192.
  - With C6, every request is at most 24,000 characters, which is at most 96,000 UTF-8 bytes.
  - nanobot's estimator counts at most one token per byte plus 4 per message (`utils/helpers.py:719-760`).
  - nanobot's input budget is 200,000 − 400 − 1,024 tokens.
  - So `request_pressure` is always `None`, and nanobot's model-calling compaction cannot run.

  `contextWindowTokens` reaches no request (§5.6 field list); the only real prompt limit is the edge's. S9 pins the value.
- **C8 Verification, read-only.** At iteration 0, `BuddyTurnHook.before_iteration` runs before the request
  (`runner.py:441` precedes `:443`) and asserts:
  - **T5:** `messages[0]` has role `system`; its first line parses as the header and carries this turn's trace id
    and identity; the rest equals the composed persona.
  - `len(messages) ≤ 10`.
  - `RUNTIME_CONTEXT_TAG` occurs only in the last message.
  - No message matches the confirm pattern.
  - The summed text is ≤ 24,000 characters.
  - **T4:** "Built with Llama." is present when required.

  A failure raises (`reraise=True`), which aborts the turn before the request. `before_iteration` writes nothing.
  This replaces §5.16's `before_iteration` bullet.
- **R8 install checks.** After assignment, both attributes are Buddy's functions. `type(agent.context)` is
  `ContextBuilder`. The composer's output for a dummy stamp validates against the contract's header schema.
  Failure is a fixed message and exit 0, like R1–R7.

**A1.4 What the edge's `turn_header_invalid` check must see.** This is step 5 of §5.6: contract `turn_header`, ARAIL
`buddy_host/brain.py:226-245`.

1. `messages[0].role == "system"`. *New:* today `parse_header` reads `messages[0]` whatever its role.
2. No message after index 0 has role `system`. *New:* otherwise the flattening would put a `### system` block into the prompt.
3. The text of `messages[0]` is a string, or text parts joined by LF. Its first line, up to the first LF, is
   `BUDDY-TURN/1 ` followed by one-line JSON that is valid against the header schema. `response_schema` is
   ≤ 4,096 bytes and uses only local `#` refs. *As built.*
4. The rest after that LF is not empty after stripping whitespace. *New.*

Any failure gives 422 `turn_header_invalid` and one coalesced `edge_refused` event with no identity (unchanged). The
position is unchanged too: after size (step 4), before hold (step 6). The edge cannot tell Buddy's persona from
nanobot's stock prompt. That is proven on Buddy's side (A1.6), not guessed at the edge.

**A1.5 Seam lock and UPSTREAM.md.** No nanobot patch is added. UPSTREAM.md's patch table stays P0–P2, and
`buddy/tests/test_upstream_md.py` keeps asserting it. `buddy/src/buddy/pins/nanobot.pin.json` changes as follows.

- `seam_files` gains six files: `nanobot/agent/context.py`, `nanobot/agent/context_governance.py`,
  `nanobot/agent/tools/context.py`, `nanobot/agent/turn_hooks.py`, `nanobot/session/turn_continuation.py` and
  `nanobot/session/manager.py`. That makes 16 seam files plus 3 patched files, so 19 files are hashed at start (was 13).
- `seams` entries, per file:

  | File | Entries |
  |---|---|
  | `agent/context.py` | `build_transcript calls self.build_system_prompt`; `build_messages calls self.build_transcript`; `TranscriptInput fields and message_count` |
  | `agent/loop.py` | `ContextBuilder built once`; `_build_transcript_input: one call site, result feeds runner and save boundary`; `get_history at turn start`; `bind/reset_request_context around runner.run`; `follow-ups to a busy session go to the pending queue` |
  | `agent/runner.py` | The old `before_iteration before request` entry becomes `transcript built before before_run; before_iteration reads the raw transcript before the request`. Add `finish_reason length triggers recovery`. |
  | `agent/context_governance.py` | `from_transcript copies H once`; `request_messages = H + Δ` |
  | `agent/tools/context.py` | `current_request_context` |
  | `agent/turn_hooks.py` | `factory skipped on raise; none for ephemeral` |
  | `session/turn_continuation.py` | `save boundary from message_count` |
  | `session/manager.py` | `get_history replays runtime context` |
  | `channels/telegram/runtime.py` | Add `_on_callback_query`, `_add_reaction`, `_remove_reaction`, `_extract_reply_context` (§A4, F54) |

- UPSTREAM.md's "Upstream-PR candidates" list gains two items:
  - a per-request system-prompt provider (removes C1's wrap);
  - a caller-owned history window, with a runtime-context replay switch, on `AgentLoop` (removes C6's wrap).
    `get_history` already takes `max_messages`, `max_tokens` and `include_runtime_context` (`manager.py:344-351`).

**A1.6 The proof: U-HDR-03, `buddy/tests/test_turn_carrier.py` (B7a, completed in B10).**

*Setup.*
- It reuses `buddy/tools/capture_brain_chat.py`'s loopback fake server.
- It runs a real `AgentLoop` built from Buddy's generated `nanobot.json`, with the fake server as `apiBase`.
- It installs R2, R5, R6 and R8 through Buddy's real `install()` code path: the same functions, not test copies.
- It uses a test World fixture and a test channel registered as `telegram`.
- Two `InboundMessage`s on one session key each carry Intake's three stamps, with different trace ids. They travel
  over the bus through AgentLoop's normal message path.
- They do **not** go through `Nanobot.run`. That path cannot carry Intake's metadata, and U-HDR-05 shows it produces
  zero requests.

*For each of the two captured request bodies, assert:*

a. `messages[0].role == "system"`. Its first line is `BUDDY-TURN/1 ` plus JSON that validates against the vendored
   contract's header schema. `trace_id` and `identity` equal that turn's stamps, and turn 1's trace id differs from turn 2's.
b. The rest of `messages[0].content` equals the composed persona byte for byte and ends with `Built with Llama.`
   (attribution cached as Llama). A second run with attribution `null` has neither that line nor a refusal.
c. **The stock prompt is absent.** Render nanobot's own prompt for the same workspace through the unwrapped class
   method, `ContextBuilder.build_system_prompt(ContextBuilder(ws))`. No line of it that is 20 characters or longer
   appears in any message of either body. The check is derived from nanobot, not from a hard-coded marker list.
d. Turn 2's `messages` are exactly `[system, user₁, assistant₁, user₂]`. `user₁` has no runtime-context block.
   `RUNTIME_CONTEXT_TAG` occurs once per body, in the last message.
e. The edge's validator accepts both bodies. Turn 2's body becomes the conformance case
   `brain-chat-nanobot-request.json`, replacing the hand-spliced one, and both FakeHost and ARAIL's real routes must
   accept it. The stock capture stays as the refused case.
f. After turn 2, nanobot's session store holds exactly `[user₁, assistant₁, user₂, assistant₂]` (F46).

Mutation checks: removing either wrap must fail (a)–(d) or (f).

*Companion tests:*

| Test | Asserts |
|---|---|
| **U-HDR-04** window | With 6 prior pairs, turn 7 sends 1 + 8 + 1 messages, starting at a user message. With long prior messages, history shrinks to fit the character budget. The session still holds every message. |
| **U-HDR-05** fail closed | `Nanobot.run`, `agent.context.build_messages(...)` outside a turn, and `consolidator.archive_session(...)` each produce zero requests. |
| **U-HDR-06** no compaction | For the largest request C6 can build (4,096-character text, a full World block, 4-byte characters, a full window), nanobot's `ContextGovernor.request_pressure` returns `None`. A 7-turn session sends exactly 7 requests. |
| **U-HDR-07** hook absent | A factory forced to raise gives zero requests and nanobot's fixed error sentence, traced `reply_sent error`. |

**A1.7 Rows affected.**
- B1 (contract delta §A8 and the re-captured case), then A5 (re-vendor), then A8b (A1.4 at the edge).
- B4b: C7 in `configure` and S9.
- B7a: composer, `persona.py`, `turn_hook.py` with C5 and C8.
- B10: R8 in `install()`; the upstream-mode plugin's flag skips R8 like R1–R7.
- ARAIL backlog D15 ("G1") is closed by this section; the builder updates it.

### A2. G2 rulings, and A8's Step 12 deltas

| # | Builder's reading | Ruling |
|---|---|---|
| G2.1 | The schema accepts any `tools` array and any part with a string `type`. Step 3 refuses with `tools_not_allowed` / `content_not_allowed`. | **Ratified.** Every §5.4 code must be reachable, and the 256 KiB body cap bounds what the schema lets through. |
| G2.2 | `X-Should-Retry: false` on every error response | **Ratified.** nanobot reads the header first (`providers/openai_compat_provider.py:1870-1877`, `providers/base.py:1012-1015`). **Addition:** the mid-stream `brain_failed` chunk arrives on a 200 stream with no header, so nanobot falls back to its text markers (`base.py:1017-1028`). Its fixed message must pass the same marker oracle in C-ERR-01. |
| G2.3a | A content array is flattened by joining its text parts with LF | **Ratified.** |
| G2.3b | A last message that is not `user` is `schema_invalid` | **Ratified.** |
| G2.3c | `approval_id != proposal.id` is `schema_invalid` | **Ratified.** It is a structural self-contradiction; `approval_conflict` stays for ledger conflicts. |
| G2.3d | `max_tokens` falls back to 400; `max_completion_tokens` is honoured | **Ratified.** When both are present the smaller applies (as built, `brain.py:258-260`). |
| G2.3e | A brain the edge refuses shows in the handshake as `available:false`, with the code in `reason` | **Ratified.** |
| G2.3f | `trace.record` is accepted while held or airgapped | **Ratified.** It records the stop and the flip, with no model call and no egress. |
| G2.3g | `hold_all` is accepted when the hold state is unreadable | **Ratified** (reductive). |
| G2.3h | The 422 carries `error.record_index` | **Ratified** (§5.7 already says so). |
| A8 Δ1 | A turn counts when it is let into inference, not at the budget check | **Ratified with a correction.** The check at step 9 (`host_edge_routes.py:270`) and the count after the slot (`:295`) are separate, so calls queued on the slot can overshoot the cap. Count with an atomic check-and-count after the slot is acquired (refusal `budget_exhausted`, as a model-call record). Re-check `lab_mode()` at the same point, since the slot wait can be 8 s. Test I-EDGE-14: two concurrent calls at `turns_used = 299` give one ok and one `budget_exhausted`. |
| A8 Δ2 | Usage rides on the final chunk with `finish_reason` | **Ratified** (§5.6 wording; the OpenAI SDK test reads it). |
| A8 Δ3 | `finish_reason: length` when the completion used all of `max_tokens` (`host_edge_routes.py:339`) | **Reversed.** nanobot treats `length` as "continue". It appends its own continuation prompt and calls again, up to 3 times (`runner.py:72`, `:631-655`). One Buddy turn becomes several admitted model calls, each on a prompt nanobot wrote and each under the turn's header. The edge reports `stop` for every completed answer. Truncation stays visible in the model-call record (`tokens_out == max_tokens`), and Buddy's parser turns truncated JSON into an abstain. The contract and `docs/buddy-edge.md` state this deviation from OpenAI (F55, D26). |

### A3. G3 ruling

**Accepted. P0 is unchanged.**
- The three deselected tests (`tests/webui/test_gateway_webui_smoke.py`) start a WebUI gateway. Buddy removes that
  transport (R1) and its bind guard refuses it.
- A narrower P0, inert for configs that are not Buddy's, would give a stock, unguarded nanobot to anything that edits
  the LaunchAgent's `--config`. That is the off switch the builder rightly refused.
- The plugin's guards stay as built: every listed id must exist, the list must equal the tests in files that spawn
  `python -m nanobot`, and the pytest header says how many are deselected.
- A spawn the regex misses still fails loudly (exit 2), so no deselection can be silent.

**Debt D23.** Coverage of nanobot's WebUI gateway path stays upstream's, not Buddy's. Triggers to pay it down:
(a) any sprint that enables the WebUI, the websocket channel or a second channel in Buddy; P0 then gets a narrower,
tested form. (b) Every pin move: UPSTREAM.md records that the three tests passed at the bare new tag (upstream CI, or a
local run with P0 reverted) before P0 is re-applied.

### A4. G4 rulings (§5.17)

| # | Builder's reading | Ruling |
|---|---|---|
| 1 | `/stop` skips rate (4) and stale (6) | **Ratified and corrected** in three ways. **(i) Closed gate.** A `/stop` from a talker in a DM also passes branch 1 (gate closed). During the ≤ 15 s teardown PTB still hands over updates it has fetched, and those are normally confirmed to Telegram by its next long poll, so an update Intake drops is gone. A stop must never be lost. New order: 2 (identity), 3 (DM), `/stop`, then 1, 4, 5, 6 and the rest. The hold file is written and `hold_all` is attempted (the edge accepts it airgapped, conformance case 096). The closed gate suppresses the reply, traced `outbound_suppressed gate_closed`. **(ii) Late stop.** A stale `/stop` (Telegram `date` more than 600 s old) is honoured in full, but its reply names the original time: "Held. Your /stop from HH:MM arrived late; Buddy and the lab's agents are now on hold. Resume both at the desk." A lab hold that lands hours later must not look like a fresh action. **(iii) Repeats.** A repeat `/stop` within 60 s of a successful `hold_all` does not call the edge again. |
| 2 | `/status` and `/help` skip 10–12 | **Ratified.** They are still rate-limited. |
| 3 | A shared location counts as media | **Ratified.** A venue also matches `filters.LOCATION` (`telegram/runtime.py:706-716`) and follows the same rule. Contacts, polls and stickers have no upstream handler, so they never arrive. |
| 4 | The daily cap has its own sentence | **Ratified.** |
| 5 | A tap from an unknown sender is counted, not acknowledged | **Ratified.** `_on_callback_query` joins the seam list (§A1.5). |
| 6 | `/new` marks the session archived before calling `cmd_new` | **Ratified.** C3 is now a second barrier: `archive_session` builds its prompt through `build_messages` and would raise. |
| 7 | The stale reply names its cause only when the gate recorded one | **Ratified.** |
| 8 | Reactions are disabled | **Ratified.** `_add_reaction` and `_remove_reaction` join the seam list. |
| 9 | Unknown slash commands are left to nanobot's router | **Ratified.** The router replies with fixed text and never calls the model (`command/router.py:110-163`). The only user text it echoes is the sender's own command token, at most 33 characters. |
| 10 | `reply_sent` reason `refuse` for rate limits, media and needs-confirmer; `help` for `/new` | **Ratified with two rules.** **(i)** Every Intake `refuse` reply has exactly one companion record with the same trace id that names the cause: `inbound_refused <reason>`, or for needs-confirmer, `proposal_suppressed requester_not_confirmer`. Test U-INT-LABEL. **(ii) Correction:** `send()` labels an outbound with no note and no `buddy_reply_reason` as `answer` (`buddy/src/buddy/channel.py:206`). The default must be `error`; only the renderer sets `answer`, through `note_reply` (F53). |

### A5. G5 ruling (§4.3 step 6, §5.19, F6)

1. **The code is never printed** in the card text. Card text is what nanobot copies into reply context
   (`telegram/runtime.py:1687-1709`), what Telegram shows in notification previews, and what forwards carry. The
   button label stays the only place the plaintext nonce exists.
2. **Re-rendering the same card is impossible by design**, because only `sha256(nonce)` is stored. An `approval_id`
   is bound to one record forever: a new nonce under the same id is `approval_conflict` at the edge.
3. **Automatic resend.** This applies when the edge call for a confirmed proposal ends in `EdgeUnreachable`, a timeout,
   or `EdgeProtocolError` — a lost answer, not a refusal.
   - Buddy waits 2 s and resends the stored write-ahead record **once**: byte-identical body, same `approval_id`.
   - The resend falls inside the edge's 60 s freshness window (5 s timeout + 2 s).
   - It is part of the same human confirm, not a new action. §5.19's "no automatic resubmission at startup" stands.
   - There is no resend while Buddy's hold is on.
   - If the first call landed, the edge answers from its ledger (`replayed: true`); if not, it executes once.
4. **If the answer is still lost,** the state becomes `edge_unknown` and the reply is "The lab did not answer. Check
   research at the desk; if it is still running, ask again for a new card."
   - A Telegram re-delivery of the same update resends the stored record (as built, F3).
   - A new card is the human retry; `pause` is idempotent on the research state.
5. **F6 tests**, against FakeHost now and the real edge in Phase I:

| Test | Scenario | Expected |
|---|---|---|
| **I-CRASH-02a** | The answer is lost after the edge acted | Exactly 2 calls with identical bodies, the second `replayed: true`. One ledger row, one `action_executed`, reply "Research paused…", state `executed`. |
| **I-CRASH-02b** | The edge is down for 90 s | Exactly 2 calls, the fixed reply, state `edge_unknown`. A new card's tap gives `paused` or `already_paused`, and `pause()` ran at most once. |
| **I-CRASH-02c** | A refusal (`held`, `stale_approval`, `role_not_confirmer`) | Exactly 1 call. |
| **I-CRASH-02d** | Restart with a `confirmed` row | 0 edge calls until a human event. |
| **U-NONCE-03** | Every outbound message body, cards included | No 16-hex string. |

### A6. Remaining work: what needs a decision

| Row | Decision |
|---|---|
| B2 | None. It is blocked on the operator (C2/C3). It hashes the seam list of §A1.5. The World pin's `repo` stays `cdarnell/qukaizen-dac`, the frozen GitHub name. |
| B7 | **Split.** **B7a, now,** against a test World fixture under `buddy/tests/fixtures/` (a hand-built bundle whose hashes pass S7): the composer and `persona.py`, the two wrap functions, `turn_hook.py` (T1–T5, C5, C8), `answer.py` (parse, render, chips, abstain) and `world.py`'s provider code. **B7b, after B2 and C4:** load the real pin, tune K on the dev set, and settle the persona wording. No persona wording or K tuning happens before C4's sha256 is in SPRINT.md, so §8.4's pre-registration holds. |
| B10 | Unblocked by B7a. `install()` gains R8. P1 is as specified. |
| B11b | `qa_harness` builds through the same `install()`, R8 included, registers its test channel as `telegram`, and publishes Intake-stamped messages on the bus. It never uses `Nanobot.run`, which C3 refuses. |
| Phase I | I1 runs with the test World after B10/B11b, and again with the real pin after B2/B7b. I2 runs after the contract delta (§A8). I3 is unchanged. |

**Order:**
1. B1d (contract delta) → A5d (re-vendor; equal sha256 on both sides) → A8b (A1.4, A2 Δ1 and Δ3).
2. B4b (C7) → B8b (F49, F50, F54, G4 corrections) → B9b (§A5).
3. B7a → B10 → B11b → I1, I2, I3.
4. After C2–C4: B2 → B7b → I1 again.

The builder files D23–D26 in `sprints/BACKLOG.md` and `buddy/docs/DEBT.md`, and closes D15.

### A7. Failure modes: amendments and additions to §7

**Amended:**

- **F6**, pause side: containment is §A5 (one automatic resend of the stored record, then `edge_unknown` and the desk).
  Tests I-CRASH-02a–d.
- **F16**: Intake redacts confirm strings and live nonces before publishing (F49). The history window scrubs again
  (C6), and C8 asserts none remain. "T-hook scrubs" is withdrawn.
- **F34**: C1 composes the carrier when the transcript is built. The edge requires `messages[0]` to be `system` and
  accepts no other `system` message (A1.4). Tests U-HDR-03, C-HDR-01..03.

**Added:**

| # | Failure | Detection | Containment | Test |
|---|---|---|---|---|
| F46 | **The carrier changes the message count.** Assistant replies go missing from the session, and the current message is counted as accepted history. | Session contents after a turn; C8 length check | C1 leaves `build_transcript` to nanobot; the window acts on `TranscriptInput` (C6) | U-HDR-03(f), U-HDR-04 |
| F47 | **A transcript built outside a Buddy turn gets a header:** the consolidator's archive or token probe, the SDK `run`, or a future upstream caller. | — | C3: without a Buddy-stamped request context the composer raises; the edge refuses requests with no header | U-HDR-05 |
| F48 | **In-turn compaction calls the model under the turn's header** (`context_governance.py:521-591`, `memory.py:909-921`). | Two model-call records for one trace id | C6 window + C7 `contextWindowTokens`, so `request_pressure` is always `None` | U-HDR-06 |
| F49 | **A confirm string or a live nonce inside an ordinary message is published** — typed mid-text, pasted bare, or carried in by reply context — and so is stored at rest in `sessions.sqlite3` and sent in the edge request. | Grep of request bodies and sessions (S-LEAK-02) | Before publishing, Intake works on the fully assembled content. It replaces `/(approve\|deny)\s+\S+\s+\S+`, and any 16-hex token whose sha256 equals a stored proposal's `nonce_sha256`. | U-NONCE-02 |
| F50 | **A follow-up sent while a turn is running is injected into that turn** (`loop.py:1336-1372`). It is answered under the first message's trace id, because the header cannot change mid-turn. | `inbound_accepted` with no model call of its own | One turn in flight per chat. A non-command text that arrives before the previous trace's `reply_sent` or `outbound_suppressed` (or within 60 s) gets "One message at a time. I am still on your last one." and `inbound_refused busy`. Priority commands are unaffected. | U-INT-BUSY; I-INJECT-01 (two quick messages: 2 model-call records, 2 trace ids, no pending-queue routing) |
| F51 | **The edge accepts a malformed carrier:** the header in a non-`system` message, a later `system` message that reaches the prompt as `### system`, or an empty persona. | Conformance | A1.4 rules 1, 2 and 4 | C-HDR-01..03 |
| F52 | **Buddy's turn hook is absent for a turn** (its factory raised and nanobot skipped it, `turn_hooks.py:67-84`), so the model's raw output goes out unrendered. | A model-call trace with no `citation_check` | C5: no registered hook means no header, so no request | U-HDR-07 |
| F53 | **An unrendered outbound** (nanobot's error sentence, a router reply) **is traced as `reply_sent answer`.** | Lanes view | The default label is `error`; `answer` is set only through `note_reply` | U-LABEL-01 |
| F54 | **Reply context copies the replied-to text, plus the sender's Telegram username or first name, into the prompt** (`telegram/runtime.py:1687-1709`, inserted at `:1998-2008`). | Request capture | `BuddyTelegramChannel._extract_reply_context` returns `None`; in S1 Buddy answers the message on its own | U-REPLYCTX-01 |
| F55 | **`finish_reason: length` makes nanobot call again** on its own continuation prompt (`runner.py:631-655`). | Several model-call records per trace id | The edge reports `stop` (§A2 Δ3) | I-EDGE-13 |

### A8. Contract delta (B1d), then §9's re-sync rule

Every change is additive or tightening; `buddy.host/v1` keeps its name.

1. `turn_header`: rules 1, 2 and 4 of A1.4. Cases C-HDR-01..03.
2. `brain.chat` response: a completed answer always ends with `finish_reason: "stop"`, with usage on that chunk;
   `length` is never sent, and the contract states this as a deviation from OpenAI. Case: a fake brain that uses all of
   `max_tokens`.
3. `brain.chat` budget: a turn is counted, with an atomic re-check of the cap, when it is let into inference. The
   concurrency is tested in ARAIL (I-EDGE-14); the conformance DSL cannot express it.
4. `trace.record`: `inbound_refused` gains reason `busy`. Case: one record with it, accepted. (`self_check_failed`
   already admits `r8` and `t5`, pattern `^[rt][0-9]{1,2}$`.)
5. Conformance: `brain-chat-nanobot-request.json` is replaced by U-HDR-03's turn-2 capture. The stock capture stays as
   the refused case.

A5d then re-vendors. Both suites must pass the same cases, and `scripts/sync-buddy-contract.py --check` must report
equal sha256.

### A9. Tech debt delta (vs §10)

| # | Debt | Trigger that pays it down |
|---|---|---|
| D4 (grows) | 16 seam-locked files (was 10). Two instance wraps, one of them on a private method (`AgentLoop._build_transcript_input`). Four more private overrides in the channel. | Unchanged, plus the two upstream-PR candidates of §A1.5. |
| D6 (same kind) | The header still rides in the system message; a wrapped `build_system_prompt` now composes it. | Upstream per-request metadata, or a system-prompt provider. |
| D15 (ARAIL backlog) | Closed by §A1. | — |
| D23 | Three upstream WebUI gateway smoke tests are deselected under P0 (§A3). | WebUI or a second channel in Buddy; and every pin move. |
| D24 | In Buddy's nanobot, `contextWindowTokens` is a switch (compaction off), not a context size. The edge's 24,000-character cap is the only prompt limit, and the window is sized to it. | ARAIL reports the brain's context size in the handshake; both limits are then derived from it. |
| D25 | nanobot's session store keeps every message until `/new`; only replay is windowed. | An S2 retention policy, or a session-size alarm. |
| D26 | The edge reports `stop` for token-capped answers, a deviation from OpenAI (§A2 Δ3). | nanobot makes length recovery configurable. |
| D27 | Trace lines carry no hash; only the approvals ledger does (§A11). | Adopting the harness Loop module, or 3D.EVAL treating lanes as evidence. |

**Net:** still positive, as §10 said. The stock prompt, memory and skills now really never reach the model, and
compaction calls are impossible rather than merely unlikely.

### A10. What this reopens

**Nothing at the VISION or §0 level.** The three patches, the loopback edge, structured-output proposals and the
gates all stand.
- G1 changes the mechanism (transcript-time wraps instead of a per-iteration hook) and grows the seam surface (D4).
- The contract changes additively and needs the §9 re-sync.
- Operator items are unchanged. O4's budget numbers hold only because the window (C6) keeps a turn near §5.6's
  2,000-token estimate.

### A11. Harness alignment (operator steer, 2026-10-20)

**Source:** https://qukaizen.com/harness, read 2026-10-20 and relayed by the orchestrator (this session did not fetch it). It describes `cdarnell/qukaizen-harness`, which does not exist on GitHub yet. This is therefore a mapping, not a cross-repo edge: nothing is imported, vendored or pinned. When the repo exists, the mapping gets a commit pin like any other edge.

| Harness part | S1 today |
|---|---|
| **Registry** | No tool reaches the model (R2, T1, edge `tools_not_allowed`). The only action is the frozen action table `{pause_research}` behind the closed `propose` enum, which the contract pins (C-ENUM-01, F18). The future registry is the paperagents `arail-agent` policy (S2). |
| **Model** | ARAIL's `fast` brain through `brain.chat`. The policy enforced at load is a file: the registry's `fast` binding, re-checked by the edge on every call (local and `ollama_native`, else `brain_not_local`, §5.6 step 8). Residency is not enforced in S1 (difference 1). |
| **Context** | The sealed `qa-practice` World comes from the pin (S7) as delimited, escaped JSON; nothing is pasted by hand. The order is stable-first at the model: the edge strips the header line, so the persona leads the prompt (`buddy_host/brain.py:248-256`, folded first by `router/backends.py:2041-2045`), then history, then the turn's World block and question. |
| **Guardrails** | Ollama `format` from `buddy.answer/v1` (`cite` is the World's slug enum; `propose` is closed), re-validated by code. Chips are rendered from sealed bytes (§8.5). "Describes, never prescribes": the model may only propose; code renders the card; a confirmer's tap with the nonce acts; ARAIL re-checks the approval (§6). |
| **Loop** | S1 Buddy is reactive. Cron, Dream and heartbeat are off (S9, R3). Every step is a `trace.record` event under its trace id. The one action is a row in ARAIL's approvals ledger with `record_sha256` (§5.9). |

**Standing rules.**
- Met: the shelf rule (Buddy has no write path to the PKB, a World or any shelf, and his one World passed DDaC's gate and is seal-checked at every start); "describes, never prescribes" (propose, then confirm); "a correction becomes code" (every G2/G4 ruling is pinned by a test, and G13's one exception is written as a rule).
- Not met, or in tension: the resident rule (difference 1), "nothing leaves the machine" (difference 2, G13), and "a ledger line with a hash", which holds for the action but not for turns (difference 3).

**Differences recorded. S1 does not change.**
1. The page pins an 8–14B resident. S1 uses the 1B `llama-ai-eng` through ARAIL's fast path, per the operator's own steer (about 1B resident, QueueLLM on demand), and nothing stops Ollama from unloading it.
2. Telegram carries Buddy's replies through servers that are not end-to-end encrypted. Proposed gate G13 bounds what may travel.
3. Trace lines carry no hash; only the approvals ledger does. **Debt D27.** Trigger: adopting the harness Loop module, or 3D.EVAL treating lanes as evidence.
4. The page's Buddy "runs the Autoresearch loop and 3DEval between your sessions". S1 Buddy answers, proposes one pause, and stops. That belongs to a later sprint.

**Proposed gate G13: off-box content.** A reply or card sent to Telegram may carry only:
- (a) sealed World rules, as chips rendered from the pinned bytes;
- (b) fixed texts: `texts.py` constants and code templates whose only variable slots are clock times, the proposal id, the recipient's own pseudonym, Buddy's own counters, Buddy's pin digests, and the hold, reachability and attribution states that explain whether Buddy answers;
- (c) the sender's own words, and model sentences whose prompt holds nothing but (a)–(c).

It never carries lab data: experiment names, metrics, goal text, activity, research status, lab mode or tier, model id or backend.
- **Named rule (receipts):** the four pause-outcome sentences (`texts.py:61-64`) may go to the confirmer, only in reply to his own confirm.
- **Status:** proposed. QA proves G13 once the operator accepts it in SPRINT.md. The three fixes below are due in B8b/B9b either way, because they return the code to §5.23's fixed text.

| Built code | Text sent | Verdict |
|---|---|---|
| `buddy/src/buddy/commands.py:85` (`/status`) | `Lab: <mode>. Lab agents: <running\|on hold>. Research: <status>.` | **Violates.** Becomes `Lab hold: on.`, `Lab hold: off.`, or `Lab hold: unreadable, treated as on.` |
| `commands.py:88-90` (`/status`) | `Brain: <model_id> (<backend>).` / `not available (<reason>)` | **Violates.** Becomes §5.23's fixed sentence when attribution is Llama, otherwise `Brain: a local model in the lab.`, or `Brain: not available.` (G11 unchanged). |
| `confirm.py:93` (card) | `Research now: <status>` | **Violates.** The line is removed. |
| `texts.py:61-64` | pause receipts | Allowed by the receipts rule. |
| Unreachable, held, stale and sleep times; budget; unknown-sender count; World and contract sha8; privacy lines; nanobot's router and failure sentences | fixed | Comply. The unknown-command reply echoes only the sender's own command token. |

**Step 1 (VISION §2.3) stays within the rules, by input closure.** Its request holds only:
- C1's system message (the edge strips the header);
- the World block;
- the sender's words;
- Buddy's own earlier rendered answers.

Lab state cannot get in:
- Command replies persist with `_command` and are skipped on replay (`loop.py:1843-1851`, `session/manager.py:386-387`).
- Cards and confirm receipts go out through the channel and never enter the session.
- The edge's flattening adds nothing.
- ARAIL's router passes `system` and `prompt` to the backend unchanged (`router/core.py:389-396`).

So the answer's sentences cannot carry lab data.

**G13 tests.**
- **U-G13-01:** render every outbound path under handshakes that differ only in disallowed fields — every `research.status`, `lab.mode` and `tier` value, and canary strings in `model_id` and `backend`. Outputs must be byte-identical and contain no canary.
- **U-G13-02 (I1):** plant a canary experiment name and goal text in the test lab. It must appear in no edge request and in no Telegram-bound body.
- **W-run:** QA classes every bot message of the win run as (a), (b) or (c).
