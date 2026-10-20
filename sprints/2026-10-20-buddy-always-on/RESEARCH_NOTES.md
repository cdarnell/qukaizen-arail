# Research notes for the visionary (2026-10-20)

Facts gathered by the orchestrator before the VISION pass. Everything here was
checked today unless marked *inferred* or *docs-only*. File refs for ARAIL are
relative to this repo at `origin/main` `ca0a8f81`; for other repos they are
relative to `~/ProJects/<repo>`. These notes supply inputs. They do not answer
the brief's nine questions.

## 1. Operator steers, in order (all 2026-10-20, in chat)

1. "Make Buddy an always-on bot; expand his capabilities; look at open-source
   Grok-Bot-like programs; the code is in ARAIL; may want a new repo."
2. "Break Buddy out of ARAIL." (Said after VISION v1 recommended staying in
   ARAIL. VISION v1 in this folder is superseded; reuse its reasoning where it
   still holds.)
3. "OpenBot or Grok Bot can be the vision. The difference is that Buddy works
   for me and works with my QuKaiZen harnesses."
4. "The gateway we designed is also important: rules for agents using tools."
   (Identified as the paperagents agent control plane, §3 below.)
5. "Fork the best open-source option for a head start; don't build from the
   ground up."
6. "Buddy has a nucleus-driven SLM, ~1B parameters, always on; QueueLLM is the
   on-demand option for deep reasoning."
7. Answers to direct questions: first harness = ARAIL lab; create the private
   GitHub repo now; Telegram acceptable as first channel (question 7 of the
   brief remains the visionary's to answer).
8. Fork base: first Hermes Agent ("multi-platform assistant in the end, ARAIL
   lab assistant / sysadmin, very deep memory workflows"), then reversed to
   **nanobot**: "I like the nanobot footprint… we build what we need but will
   be more configurable; otherwise, why not just use Hermes?"
9. The final brief (the one you are running) re-frames the sprint as
   `2026-10-20-buddy-always-on` in qukaizen-arail and adds: Buddy is a
   specialised QA subject-matter expert on a DDaC World; the gateway is the
   eval surface for 3D.EVAL.

## 2. The field (verified against each repo via the GitHub API today)

| | nanobot (HKUDS) | Hermes Agent (Nous) | OpenClaw | OpenBot (nightly-labs) |
|---|---|---|---|---|
| Language | Python ≥3.11 (+TS WebUI/TUI) | Python (+TS UIs) | TypeScript/Node, >1 GB RAM | TypeScript/Electron |
| License | MIT | MIT | MIT | PolyForm Noncommercial 1.0 (≤0.1.11 Apache-2.0) |
| Stars / forks | 49.2K / 8.7K | 255K / 56K | 393K / 83K | 685 / 111 |
| Repo size | 78 MB | 1.29 GB | 9.8 GB | 152 MB |
| Open issues | 860 | 50,165 | 9,661 | 73 |
| Latest release | v0.3.5, 2026-09-15 | v0.22.2, 2026-10-15 | v2026.10.5, 2026-10-18 | v0.44.3, 2026-10-19 |
| Pushed | today | today | today | today |

Grok Bot (xAI) is proprietary and cloud-only: persistent xAI-managed VM,
$200/mo or bundled with SuperGrok Heavy / Cursor Ultra; Allow once / Deny /
Always allow approvals plus "Auto Review" rules; 2–6 bots per shared VM, which
xAI says is not a security boundary; no self-host, no memory transparency.
OpenBot: persistent "teammates" with their own workspace, crash-safe FIFO
queues, agent-to-agent messages, embedded sandboxed browser, routines via
webhooks; agents run with full local access, which its README calls "an
explicit current product decision, not a security boundary"; its license
forbids vendoring into an MIT repo.

Shared shape of OpenClaw / Hermes / nanobot: one long-lived gateway process
owning sessions, tool execution, memory and cron; channels are thin adapters;
skills are markdown; MCP is the plugin bus; nightly memory consolidation; DM
pairing is the default answer to "who may talk to the bot".

Hermes security model (docs, read today): `approvals.mode` smart | manual |
off; unanswered prompts deny after 300 s; cron / single-query / unattended
default to deny; a hardline code blocklist no setting overrides;
`approvals.deny` glob patterns checked before YOLO; chat-reply approvals
(yes/no words); `command_allowlist` for "always"; DM pairing codes expire in
1 h, one request per user per 10 min, 3 pending per platform, 1 h lockout
after 5 failures; Docker backend drops capabilities, `no-new-privileges`,
PID limit 256; protected-path write denylist (`~/.ssh`, `~/.aws`, `.env`);
SSRF blocks private ranges by default. Keep as the reference for approval UX
and pairing hardening.

## 3. "The gateway we designed": the paperagents agent control plane

Source: `qukaizen-paperagents/sprints/2026-09-28-agent-control-plane/ARCHITECTURE.md`
(built; merged to main as PR #3 `4af1f83`; QA WEAK_PASS).

- Declared per agent in TOML: `policy = {deny[], ask[]}` and nothing else;
  there is no `allow` (schema rejects it; :390–393, :921). `context` carries
  `delegation.mayDelegateTo`, `maxTurns`, `budgetTokens`, `maxToolCallsPerTurn`
  (:378–388).
- Identity: subagents by `agent_type`; main sessions by `cwd` glob or
  `agentName`; else `defaults` (:951–960).
- Decision order: global HOLD → agent-scoped HOLD → delegation deny on
  `Agent`/`Task` → deny patterns → ask patterns → no opinion (:962–970).
- Pattern grammar: `Tool`, `mcp__srv__*`, `Bash(prefix:*)`, path globs,
  `WebFetch(domain:host)`, `Agent(type)`; bad patterns fail at manifest
  validation (:972–1002).
- Enforcement: PreToolUse command hook, always exits 0, no network, `node:*`
  only (:825–849, :251–263). Policy errors fail open with a degraded marker;
  a corrupt HOLD fails closed (:1018–1032). HOLD adds `continue:false`.
- Stated trust boundary (:923): catches accidents and runaway loops; not
  containment against a model trying to get around it.
- Data: `~/.paperagents/policy.json` v1 (:927–949); `hold`
  `{v,on,since,reason,scope:"global"|"agent:<n>",by,stopTurn}` (:1007);
  event lines v1 `events/<date>.jsonl` with fields kept per event type and
  content dropped, ≤4 KiB (:799–823); run receipt `paperagents.run-receipt/v1`
  with sha256 digest (:1157–1184, marked "cut #4", check whether it landed).
- Code on main: `src/hook/policyMode.ts` (`runPolicyMode` :159),
  `src/observe/policy/evaluate.ts` (`decide` :253), `match.ts`
  (`compilePattern` :84), `compile.ts`, `hold.ts` (`readHold` :41).
- Reserved for Buddy: runtime kind `arail-agent` with `tracePath`,
  `activityPath`, `haltPath` (:367–373); `policy` rejected on it "until
  sprint 2" (:431–432).
- Not built: enforced budgets (observe-only), HTTP HOLD toggle, `Workflow`
  checks, the `arail-agent` observer.
- ARAIL's design-only half: a single agent-inference gateway with priority
  (user turn > Chat tab > proactive line > prep), hourly budgets, kill switch,
  "model proposes, user taps" typed actions
  (`sprints/2026-09-20-buddy-front-and-center/OPERATOR_BRIEF.md:200–212`),
  parked at `sprints/BACKLOG.md:1485–1515` pending the `overlap_pct` number.

## 4. Brains

- **Resident 1B today:** Ollama `llama-ai-eng` (`models/ai-eng/Modelfile.default`:
  `FROM llama3.2:1b`, "Built with Llama." in the system prompt, `num_ctx 8192`);
  installed by `scripts/setup.sh:840–841, 922–928`; both `llama-ai-eng:latest`
  and `llama3.2:1b` are in `~/.ollama` on this Mac (daemon was not running
  when checked). Llama 3.2 Community License: derived names must start with
  "Llama"; "Built with Llama" in README/catalog/persona; NOTICE + AUP bundled
  (`NOTICE:18–31`, `INSTALL.md:259–261`, `licenses/`).
- **Gemma 2B path is dormant:** `Modelfile.gemma` still has
  `FROM __PLACEHOLDER_GEMMA_BASE__`; `setup.sh:888–904` refuses it.
- **Nucleus-baked resident: ROADMAP.** nucleus SSDP emits `DNA.md`,
  `superskill-spec.yaml` and a LoRA `adapter` per `models/graduated/<id>/`;
  GGUF is a placeholder README (`nucleus/certifier/packaging.py:239–274`); no
  Modelfile is written. The one real run graded "F (Unusable)". The seal is an
  Ed25519 chain (ADR-0013) recorded in `DNA.md` + `.seal.json`
  (`seal_mint.py:13–16`). ARAIL Model Forge (`docs/nucleus.md:17–21`):
  `plan|stage|build|certify|verify|status|list`; `fuse` yields an MLX
  directory (`train/mlx_kd.py:147–160`); `certify` writes `dna-card.yaml` +
  `seal.json` (`certify.py:419–420`) under `ARAIL_MODELS_DIR/forge`; GGUF
  export not built (`nucleus.md:94`); nothing registers a build with Ollama.
  `/Users/Shared/models/qkz-expert-v0.1` is an unfused, uncertified LoRA on
  `gemma-4-e2b-it-OptiQ-4bit`.
- **QueueLLM has no HTTP server.** No crate depends on axum/hyper/actix/warp;
  `docs/product/queuellm-as-agent-backend.md:23` says so, recommends a
  fast-lane/heavy-lane split (:81–98) and proposes a `queuellm-serve` crate
  (:104–106). Python API: PyO3 wheel `queuellm_api`
  (`crates/queuellm-api/src/lib.rs`): `Runtime(model_path, *, backend,
  ring_depth, draft_model, kv_memory_budget)` :700–714, `.start()` :761,
  `.generate(prompt, speculative, logprobs, grammar, on_tokens=…)` :816–835,
  thread-bound :662. ARAIL's `.venv` still carries `aerollm_api-0.1.0`
  (frozen integration name). Measured on this 36 GB M5 Max
  (`docs/COMPATIBILITY.md`): 70B-4bit ring 16 → 17.4 GB peak; gpt-oss-120b
  → 23.6 GB, 0.015 tok/s. *Inferred:* Ollama 1B + QueueLLM fit in 36 GB;
  QueueLLM is for background drafting, not interactive turns.
- **ARAIL's deep gate** (`src/arail/agents/deep_policy.py`): kill switch,
  maximus tier, `aerollm_api` importable (:110–120); foreground bypasses the
  rest; background blocked if halted, inside the active window, profile
  `interactive`, profile disallows, or Metal pressure ≥ 0.60 (:31, :63–94;
  override `ARAIL_AEROLLM_BG_PRESSURE_PCT`, clamped 0.30–0.75).
  `AeroLLMBackend` keeps one shared instance per model on a single-worker
  executor (`router/backends.py:1509–1540`).
- **ARAIL portal:** an OpenAI-compatible shim exists at
  `GET /api/openai/v1/models`, `POST /api/openai/v1/chat/completions`
  (`portal/openai_compat.py:418–424`, registered `app.py:1390–1395`, SSE
  :319–405) but it folds system messages and **drops tools** (`_to_chat_args`
  :84–140) and ignores `Authorization` (:14). No inbound auth: `ARAIL_PASSWORD`
  only gates onboarding (`app.py:421–422`); "NO login" (`:904–912`); Host
  allowlist + CSRF only (`:685–705`).

## 5. nanobot (docs-only read today; the visionary pins the source commit)

- `nanobot gateway` runs the agent loop (`AgentLoop` channel-facing,
  `AgentRunner` model-facing), sessions (SQLite `sessions.sqlite3`), tools,
  memory, cron, security policy, WebUI/TUI. Layout: `bus/`, `agent/`
  (`loop.py`, `runner.py`, `memory.py`, `tools/`), `providers/`, `channels/`,
  `session/`, `config/`, `security/`, `cron/`, `skills/`, `pairing/`,
  `triggers/`, `llm_usage/`, `sdk/`, `apps/`, `web/`.
- Tools: `agent/tools/base.py` `Tool(ABC)` with `name`, `description`,
  `parameters` (JSON Schema via `@tool_parameters`), `async execute(**kw)`,
  `read_only`, `exclusive`, plugin metadata (`config_key`, `_scopes`,
  `enabled(ctx)`, `create(ctx)`); errors returned as `ToolResult.error`.
  **No per-tool approval or confirmation hook on the base class.**
- Channels: one package per channel exporting a `ChannelPlugin`
  (`channels/<name>/`, guide `docs/channel-package-guide.md`); Telegram and
  Signal present among ~20.
- Providers: `ProviderSpec` in `providers/registry.py`; built-in `ollama`
  (`"providers": {"ollama": {"apiBase": "http://localhost:11434/v1"}}`);
  `modelPresets` (named, e.g. `fast`, `deep`), `agents.defaults.modelPreset`,
  `agents.defaults.fallbackModels`, runtime `/model`. No per-task routing in
  the docs; a plugin would add it.
- Security: `security/workspace_access.py`, `workspace_policy.py` (filesystem
  tools bounded to the workspace), `security/network.py` (SSRF),
  `agent/tools/sandbox.py`, per-channel access control. Docs do not describe
  approval prompts or allowlists.
- Memory: `SOUL.md`, `USER.md`, `memory/MEMORY.md`, `memory/history.jsonl`;
  "Dream" consolidation (`agent/memory.py`) on a schedule; heartbeat.
- Cron: `agent/tools/cron.py` + `nanobot/cron/`, jobs in
  `<workspace>/cron/jobs.json`; missed runs are not replayed after restart;
  `/goal` keeps working across turns while the gateway runs.
- MCP: `tools.mcpServers`; Agent Plugin v1 packages under
  `<workspace>/plugins/`; skills under `<workspace>/skills/`; Python SDK
  (`docs/python-sdk.md`); OpenAI-compatible API (`docs/openai-api.md`).
- Extension advice from `docs/architecture.md`: "Prefer existing
  registry/discovery patterns over ad hoc wiring."
- A deeper source read (tool-dispatch chokepoint in `runner.py`, Agent
  Plugin v1 manifest, `pairing/` internals, release habits) was still running
  when these notes were written; if it lands it will be appended as §6.

## 6. Practical notes

- All sprint writes go to this worktree
  (`~/ProJects/.claude/worktrees/sprint-command-error-060a85/arail-buddy-wt`,
  branch `qukaizen/arail-buddy-always-on`). The app's write hook refuses
  edits to `~/ProJects/arail-buddy-wt`, which is back on `main`.
- A parked draft of a Grok-Bot-class VISION (shape "new repo", written
  before the nanobot and QA-SME steers) is at
  `~/.claude/plans/delightful-brewing-babbage-agent-a48994320a1f6a8d7.md`.
  Worth carrying from it: the approvals model (typed task classes, default
  deny; Always allow only at the desk and never for high-impact classes;
  reductive actions one tap from the phone), the `buddy.host/v1` edge sketch
  (local-only, capability credential minted by ARAIL at a desk-side link
  step, read methods + `emit`/`hold_all`/`queue_experiment`), and the finding
  that ARAIL's experiment archetype `game_config_optimization` runs
  `variables.benchmark_command` as a subprocess (`mini_experiments.py:515–556`)
  while `POST /api/experiments` is typed (`app.py:4715`).
- A docs-only scaffold for a possible `qukaizen-buddy` repo exists at
  `<session>/qukaizen-buddy` (uncommitted). Its fate follows question 1.

## 6. nanobot source read (landed after §5; `main` at v0.3.5, read via the GitHub API 2026-10-20; line numbers are for current `main`)

**Runtime.** Package `nanobot-ai` 0.3.5, Python ≥3.11, hatchling + custom
`hatch_build.py`, MIT, status Alpha. Entry points `nanobot = nanobot.cli.entry:main`,
`nanobot-desktop-tui`. From source: `pip install -e ".[dev]"`, `nanobot onboard`,
`nanobot gateway` (`--background`, `status`, `logs`, `restart`, `stop`). Bare `nanobot`
opens the TUI (needs Bun in a source checkout). Telegram's library is not a core
dependency: `nanobot plugins enable telegram`. Dev checks: `pytest`, `ruff check`,
strict `basedpyright`, coverage floor 75 %, `scripts.check_import_boundaries`;
`AGENTS.md` says never run `ruff format`. Paths: `~/.nanobot/config.json`;
sessions `<config-dir>/sessions/<workspace-id>/sessions.sqlite3`; next to config:
`delivery/outbox.sqlite3`, `pairing.json`, `llm_usage.sqlite3`; workspace
`~/.nanobot/workspace/` holds `SOUL.md`, `USER.md`, `AGENTS.md`, `HEARTBEAT.md`,
`memory/{MEMORY.md,history.jsonl}`, `skills/`, `plugins/`, `cron/{jobs.json,runs/}`,
`triggers/runs`, a `.git` for memory versions. Ports: health 18790, WebUI 8765,
API 8900, Telegram webhook 8081 (no clash with ARAIL 8080). Multiple instances via
`nanobot --home <dir>`; docs say this is data separation, not an OS boundary.

**Extension points.** Tool: subclass `Tool` (`agent/tools/base.py:214`), external
tools via entry-point group `nanobot.tools` (`loader.py:74`), cannot override
built-ins (`loader.py:114–119`); `ToolContext` exposes bus, cron, sessions, subagent
manager, `runtime_control` (`tools/context.py:91–105`). Agent Plugin v1
(`<workspace>/plugins/<name>/`, agent-plugins.org 1.0.0 schema, `agent/plugins.py:54–68`)
bundles **skills and stdio MCP servers only**, no Python/hooks/tools; sha256
fingerprint disables it after any file change; runs with nanobot's full OS
permissions. MCP: `tools.mcpServers` → same `ToolRegistry`, `mcp_` prefix.
ChannelPlugin: **in-tree only** (`channels/registry.py:20–27`). ProviderSpec: in-tree,
but any extra `providers.<name>` config key becomes a custom OpenAI-compatible
provider (`schema.py:294–298`). Hooks (`AgentHook`, `hook.py:67`): only via SDK
`run(hooks=)` or `cli/gateway_runtime.py:544–557`; no entry point. Chat commands:
registered in code on `loop.commands` (`loop.py:460`). Config gotcha: root `Config`
is pydantic-settings with no `extra` setting (`schema.py:749`), so an unknown
top-level key likely fails validation; `ToolsConfig` drops unknown keys silently.
Buddy's config needs its own file.

**Tool-execution chokepoint.** `agent/runner.py:560–572` → `hook.before_execute_tools`
→ `execute_tool_calls`; the single dispatch point is
`agent/tools/execution.py::_execute_tool_call` (:107–216): repeated-lookup check →
`prepare_call` → `hook.before_execute_tool` (:158) → `tool.execute` (:165) →
after/error hooks. Main loop, subagents (`subagent.py:836`), Dream/cron/heartbeat all
pass through `AgentRunner`. **No policy layer exists:** `before_execute_tool` returns
`None`; `CompositeHook` swallows exceptions unless `_reraise=True`, and a re-raise
aborts the whole turn (`runner.py:324–330`), so hooks cannot soft-deny one call.
Subagents build their own hook chain and a fresh `ToolRegistry()`
(`subagent.py:791–806`, `:525`); Dream too (`memory.py:566`). `read_only`/`exclusive`
only govern parallelism (`execution.py:286–310`); the loop runs
`concurrent_tools=True` (`loop.py:1325`). Limits that exist:
`agents.defaults.maxToolIterations` (200 model rounds), `tools.maxSessionMessagesPerMinute`
(6), repeated-lookup and workspace-violation counters; `llm_usage` is a fail-open
usage log with **no budgets**. **Smallest fork diff:** ~15 lines in
`_execute_tool_call` between :157 and :158 calling a pluggable policy loaded via a
new entry-point group (e.g. `nanobot.policy`) returning allow / deny / ask (ask =
await an approval future); deny returns `ToolResult.error`. Identity comes from
`current_request_context()` (session_key, channel, chat_id, sender_id); usage and
iteration from `AgentHookContext`. Plus one small patch in `gateway_runtime.py`
after `AgentLoop` is built so Buddy can register commands and hooks. Upstream-PR
candidate: let `before_execute_tool` return a result to short-circuit the call.

**Approvals today: none.** `ask_user` was removed in v0.2.0. Enforced: shell `exec`
regex deny/allow patterns (`shell.py:226–245, 808–835`); `restrictToWorkspace`
path checks (`workspace_policy.py:96–128`, "not an OS sandbox"); `sandbox.py` wraps
shell only, bwrap (Linux) / seatbelt (macOS), filesystem-restricted, **no network
restriction**; SSRF guard on web fetch and HTTP MCP; `goal_permission.py`.
Telegram buttons: `inlineKeyboards` off by default; `callback_data` **is the label**
(≤64 bytes, `telegram/runtime.py:2254–2270`); a tap arrives as an inbound message
with `is_callback` (:2277–2310); if the session is busy it is queued into the running
turn unless it is a registered command (commands dispatch immediately,
`loop.py:834–867`). So an approval must be a `/approve <id>` command and the button
label must carry that text. Slash-command regex is hard-coded (`runtime.py:617`).

**Model routing.** `modelPresets` + `agents.defaults.modelPreset` + `fallbackModels`
(fallback only on provider errors). `/model <preset>` per session (next turn);
SDK `run(model_preset=…)` (`model_runtime.py:233`). **No per-turn router**: the
runtime is fixed at turn start (`loop.py:1316–1319`); subagents inherit the parent
model; Dream uses `dream.modelOverride`. Ollama spec `registry.py:849–859`
(`localhost:11434/v1`); no keep-alive key (use `extraBody` or `OLLAMA_KEEP_ALIVE`).
Custom deep endpoint: `custom` spec (`registry.py:217–225`) or a named extra provider.

**Telegram + pairing.** Keys: `token`, `allowFrom`, `proxy`, `groupPolicy`, `mode`
(polling/webhook), `streaming`, `inlineKeyboards`, `richMessages`, `webhook*`
(`telegram/manifest.py`). With `allowFrom` omitted the bot is pairing-only: unknown
DM → code `ABCD-EFGH`, 10-minute expiry (`pairing/store.py:29`), approved senders in
`pairing.json`. **No owner/admin concept**: anyone who can reach the bot can run
`/pairing approve|deny|revoke` (`command/builtin.py:938–948`). First approval from
the CLI: `nanobot agent -m "/pairing approve CODE"`.

**Cron / triggers / goals.** `CronJob` (`cron/types.py:26–177`): schedule
(`at`/`every`/`cron` + tz), payload `agent_turn`, run history, `deleteAfterRun`;
`<workspace>/cron/jobs.json`. Delivery to the creating session via a durable outbox
(retry every 30 s, up to 1 h). **Missed runs are not replayed.** Local triggers: a
durable at-least-once queue that waits for session idle. `/goal` via
`CreateGoalTool`/`UpdateGoalTool` (`long_task.py:144, 275`); subagents running at
restart are marked interrupted. **No general FIFO task queue with pause/resume.**

**Memory.** `context.py:78` loads `AGENTS.md`, `SOUL.md`, `USER.md`; `MEMORY.md` is
pasted raw into the system prompt (:113–116). Dream: on by default every 2 h
(`schema.py:68–69`), reads ≤20 new `history.jsonl` entries (`memory.py:526`), edits the
three files with its own tools (:558–600), commits to the workspace `.git`, runs via
`process_direct` on the Dream preset (`gateway_runtime.py:594–612`). Heartbeat: on by
default every 1800 s, reads `## Active Tasks` from `HEARTBEAT.md`.

**Fork hygiene.** Releases every 2–7 weeks; **529 commits on `main` in the last 30
days**; every release has "Upgrade Notes" with data moves and config migrations kept
~2 releases; internal Python APIs change without deprecation. Size: 781 files under
`nanobot/`, 411 non-test `.py`, ~5.2 MB (~130k LOC est.); `loop.py` ~120 KB.
`nanobot/web/dist` is git-ignored, built by the hatch hook with Bun; TUI needs Bun.
External calls: PyPI version check, skills.sh and skillhub.cn marketplaces, ClawHub
skill, DuckDuckGo; no HKUDS telemetry found.

**Risks noted by the reader.** (1) The policy check needs a core patch in a
fast-changing file. (2) An "ask" locks the session while the turn timeout runs;
parallel tool batches mean several cards at once. (3) The card flow depends on
in-tree Telegram code. (4) No owner role: Buddy must check the approver is the
owner. (5) A 1B model facing 30+ tool schemas is a poor tool-caller and router;
routing should be deterministic code. (6) Buddy config cannot live in
`config.json`. (7) Crash-safe queueing must be built. (8) Dream on
`dream.modelOverride` would wake the deep model every 2 h. (9) No network sandbox;
Agent Plugins run with full permissions.
