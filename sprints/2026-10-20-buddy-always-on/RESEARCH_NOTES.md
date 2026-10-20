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
