# Vision: Mastra Studio as an ARAIL surface (spike)

**Date:** 2026-10-13
**Product:** arail
**Wedge size:** one sprint (time-boxed spike; disposable if it fails its gates)

> Honesty rules apply throughout. Every claim about Mastra below is cited to a
> source fetched on 2026-10-13. Every claim about ARAIL is cited to a file in
> this worktree. Anything labelled **AIM** is a goal, not a result.

## User

The owner (Charlie), running ARAIL on his own Mac in `airgapped` mode on the
maximus tier, who starts an Autoresearch goal and then cannot see *why* the
Researcher did what it did. Today he reconstructs a run by reading
`/api/research/planning-trace`, the activity stream, and the experiment JSON
by hand. The secondary persona (a friend or family member running ARAIL) is
explicitly **not** the target of this spike. Mastra Studio is a developer
tool, and that user should never see it. Maximus-only, opt-in.

## Problem

The problem is not "ARAIL has no tracing." ARAIL already has
`arail.agent_trace/v1` (`src/arail/agent_trace.py`, a bounded append-only
trace store with SSE) and a planning-trace endpoint. The pain is narrower:

1. A Researcher run has no **hierarchical, clickable span view** (goal to
   hypothesis to experiment to tool and model calls) with inputs and outputs
   per step.
2. A run has no **score attached to it** that lands in the experiment record.
   `ExperimentTracker` (`src/arail/skills/experiment_tracker/__init__.py`)
   stores `observations[]` and `results`, and nothing writes a structured
   quality score from an evaluator into them.

The owner's stated hypothesis is that Mastra Studio gives him (1) and (2)
"for free". This spike tests that hypothesis. It does not assume it.

## Win condition (pre-committed)

All of the following must be true on the owner's Mac, measured, at the end
of the sprint:

- **W1. One-shot round trip.** One manual action in the portal (a button or
  `arailctl` command, never a scheduler) runs the Researcher once through a
  Mastra project and produces both of these:
  - one Mastra trace, visible in Studio (embedded in the portal as an iframe,
    the way `notebook.html` embeds Jupyter), and
  - one `observations[]` entry on a real `ExperimentTracker` experiment. It
    carries the Mastra trace id, the scorer id, and the numeric score, and
    `GET /api/experiments` reads it back.
- **W2. Zero egress, proven.** With `LAB_MODE=airgapped`, a full W1 run makes
  **zero non-loopback connections** from the Mastra process. This must be
  shown by a captured egress test, not by a config flag. A run that touches
  `posthog.com` or the Mastra platform fails the spike outright.
- **W3. ARAIL stays the source of truth.** No agent definition, prompt, or
  agent config is created or edited in Mastra. Mastra sees the Researcher
  only as a tool. `git diff` shows no change to `src/arail/agents/researcher.py`
  behaviour beyond what the new exposure adds.
- **W4. The owner uses it unprompted.** Within 14 days of merge, the owner
  opens the Studio surface on at least 2 separate days for a real goal, not
  a demo. This is self-reported in the sprint ledger.

## Wedge

The smallest test that proves or disproves the hypothesis:

1. A local MCP server (Streamable HTTP on loopback, or stdio) in ARAIL. It
   exposes **one** tool, `researcher.run_once(goal) → {report, experiment_id,
   steps[]}`. It wraps the existing Researcher. It does not reimplement it.
2. A pinned, minimal Mastra project under a dedicated directory such as
   `integrations/mastra-studio/`, with exact versions in a lockfile. It has
   one Mastra agent (a thin driver), `MCPClient` pointed at the ARAIL tool,
   **one** scorer, and local-only storage (LibSQL file under `lab/data/`,
   git-ignored) with `MastraStorageExporter` only.
3. A write-back path that takes the score plus the trace id and calls
   `tracker.observe(exp_id, …)`. It goes through an ARAIL code path. Mastra
   never writes ARAIL files directly.
4. A maximus-tier portal surface that iframes Studio on loopback. It is
   registered like `notebook` and `marimo` in `_OPTIONAL_SERVICES` and
   `_TIER_SURFACES`, with an honest "not installed / not running / Node too
   old" state.

Out of scope: Buddy, SRE, workflows, datasets, any scheduler, Mastra Editor,
Agent Builder, anything under `ee/`, Studio auth, deploying Studio anywhere.

## Disconfirming evidence (pre-committed)

We stop and shelve the spike, and record a learning, if **any** of these happens:

- **D1.** Telemetry cannot be proven off. If W2 shows any non-loopback
  connection with `MASTRA_TELEMETRY_DISABLED=1` set (CLI, server
  usage/feature telemetry, *or* the Studio browser bundle), we **reject**.
  ARAIL's airgapped default is not negotiable (repo `CLAUDE.md`, "Don't relax
  this default").
- **D2.** The trace is opaque. If Studio shows the Researcher as a single
  "tool call" span with no internal steps, because the MCP boundary hides
  everything inside it, then the core value is absent. Getting the steps back
  would need ARAIL to emit OTel spans into Mastra. That is a different, larger
  project. We **defer** and re-scope it as an `agent_trace` to OTel exporter
  question, with no Mastra required.
- **D3.** Any required feature lives under an `ee/` directory (source-available,
  "does not permit Production Use … does not permit redistribution").
  We **reject** that path.
- **D4.** W4 fails. If the owner opens it fewer than 2 times in 14 days, we
  remove the surface and keep only the MCP tool, if the tool proved useful
  on its own.
- **D5.** Install cost is too high. If adding the surface requires Node
  ≥ 22.13 on minimalist installs, or makes `scripts/setup.sh` fail on a clean
  machine, it stays maximus-only and opt-in, or it dies.

## Displacement

Saying yes means these get less attention this sprint:

- **ARAIL `AERO_*` → `QUEUELLM_*` migration sprint.** It is time-bound: every
  process prints a deprecation warning once arail runs a QueueLLM build with
  PR #298, and 2.0.0 drops the alias. That work is more urgent than this spike.
- **Autoresearch durability** (ROADMAP: five git-ledger hazards, blocked on
  six operator decisions). Ask honestly whether a prettier view of a
  non-durable ledger is the right order. Arguably the owner's 30 minutes is
  better spent on those six decisions.
- **3DEval.** This is the strategic overlap the owner should look at hard.
  `qukaizen-3d-eval` already exists as *the* eval-with-receipts framework,
  with ARAIL as a system under test and a Claude coach. Mastra scorers and
  datasets compete with it for the same job. The spike must not become a
  second eval system. Its scorer is a probe, not a strategy.
- **Next Buddy sprint** (`arail-buddy-wt` is sitting on main waiting for it).
  Buddy is the friends-and-family face of ARAIL. This spike serves only the
  owner.

## Recommended next step

**Proceed to /architect**, as a strictly time-boxed, one-sprint spike, with
D1 and D2 checked **first**, before any portal UI work. If D1 or D2 fires
in the first day or two, stop and write the learning. That result is still
cheap and worth having. Do not let this spike take a second sprint. The owner
should confirm the displacement trade, especially against the `AERO_*`
migration, before the builder starts.

---

## Research record (verified 2026-10-13)

### Mastra, external

| Claim | Status | Source |
|---|---|---|
| `mastra` CLI 1.34.0, `@mastra/core` 1.76.0, `@mastra/mcp` 2.3.0; all `Apache-2.0` | Verified | `npm view` against registry.npmjs.org, 2026-10-13 |
| Node requirement `>=22.13.0` (both `mastra` and `@mastra/core`) | Verified | `npm view … engines` |
| Repo licence: Apache-2.0 **except** any `ee/` directory (e.g. `@mastra/core/auth/ee`, `@mastra/core/agent-builder/ee`, `@mastra/editor/ee`) | Verified | `github.com/mastra-ai/mastra/blob/main/LICENSE.md` |
| `ee/` = "Mastra Enterprise Edition License v2.0", effective 2026-09-22, source-available, "does not permit Production Use … does not permit redistribution" | Verified | `github.com/mastra-ai/mastra/blob/main/ee/LICENSE` |
| **Telemetry is ON by default.** It covers anonymous CLI analytics, startup token-usage telemetry when metrics are enabled, a startup "project-surface snapshot", and **Studio browser analytics**. Opt-out is `MASTRA_TELEMETRY_DISABLED=1`, which is documented as also disabling the browser analytics (injected into the served page) | Verified (docs) | `mastra.ai/reference/cli/mastra.md` § Telemetry |
| CLI tarball depends on `posthog-node` and contains `app.posthog.com` strings and `MASTRA_TELEMETRY_DISABLED` checks | Verified (static grep of `mastra@1.34.0` tarball) | `npm pack mastra@1.34.0` |
| Studio runs at `localhost:4111` (host/port configurable) and has agent chat, traces, logs, datasets, experiments with scorers, and "Download trace JSON" | Verified (docs) | `mastra.ai/docs/studio/overview.md`, `/docs/studio/observability.md` |
| Local observability = `@mastra/observability` + LibSQL (traces) / DuckDB (metrics); `MastraPlatformExporter` sends only "if `MASTRA_PLATFORM_ACCESS_TOKEN` is set" | Verified (docs) | `/docs/studio/observability.md` |
| `MCPClient` connects to MCP servers over **stdio and Streamable HTTP**; `listTools()` / `listToolsets()` | Verified (docs) | `/docs/connections/mcp.md` |

**Could not verify:**

- That `MASTRA_TELEMETRY_DISABLED=1` actually yields zero egress at runtime.
  The docs say so, but nobody ran it. This is W2/D1 and must be measured.
- Whether Studio's served page sends `X-Frame-Options` or a CSP
  `frame-ancestors` that blocks an iframe from the ARAIL portal origin. Not
  tested. The architect must check this; a reverse proxy through the portal
  may be needed.
- Whether a remote MCP tool's *internal* steps can appear as child spans in a
  Mastra trace (D2). Docs reviewed did not say. Expect "no" unless ARAIL
  propagates trace context.
- Whether any Studio panel the spike needs (datasets/experiments UI, scorer
  UI) silently imports `ee/` code. Not audited.
- The model provider the Mastra driver agent would use under airgap. It must
  be a local OpenAI-compatible endpoint (Ollama/QueueLLM), not checked.

### ARAIL, internal (this worktree)

- **Agents:** `src/arail/agents/researcher.py` (1851 lines; background loop
  driven by `GoalStore`, `ExperimentTracker`, `CuratorAgent`, `ConsentStore`,
  and the scheduler window). Built-ins include Buddy, SRE, Librarian, and others
  in `src/arail/agents/`. User agents load from `lab/pkb/agents/<id>/`.
- **No MCP exposure exists.** `grep -i mcp` in `src/` hits only Librarian
  comments about the *term* "MCP". **No synchronous "run Researcher once"
  API exists.** `/api/research/start|pause|resume|stop` drive the background
  loop. `/api/research/status` and `/api/research/planning-trace` read state.
  The wedge's `run_once` is new code.
- **Experiment ledger:** there are two, and the architect must choose
  explicitly.
  - (a) `ExperimentTracker`: one JSON file per experiment under
    `config.EXPERIMENTS_DIR`, fields `observations[]`, `results`,
    `conclusion`, `hypothesis_supported`, with LanceDB search. The HTTP API
    has `GET/POST /api/experiments` but **no HTTP observe/complete**.
  - (b) The Tuning loop's git ledger (`autoresearch/*` branches + bench
    `.jsonl`), which ROADMAP flags as not durable.
  This vision recommends **(a)**.
- **Existing trace store:** `arail.agent_trace/v1` (`src/arail/agent_trace.py`).
  It is the natural source for any future OTel bridge (D2).
- **Surface mechanism:** `_OPTIONAL_SERVICES` + `_TIER_SURFACES` in
  `src/arail/portal/app.py`. Jupyter is the template to copy. It probes a
  port, has a start endpoint, and runs in an iframe with `frame-ancestors` set
  to loopback (`app.py` ~2126–2174, `templates/notebook.html`).
- **Trust boundary:** the portal has no auth and loopback is the boundary.
  A Host allowlist plus Sec-Fetch-Site/Origin CSRF middleware sit on all
  mutations (`app.py` ~637–700). A server-side Node caller with no Origin
  header passes, so the MCP/HTTP tool needs its own loopback-only bind.
- **Node:** `scripts/setup.sh` already has `ensure_node` (for agent-browser),
  but it does not enforce a version. The dev Mac has v25.9.0.

## Notes for the architect

1. Do D1 (egress) and D2 (span visibility) as the first build tasks, behind
   no UI. Kill early if either fires.
2. Enforce telemetry-off in code, not in docs. The ARAIL launcher must set
   `MASTRA_TELEMETRY_DISABLED=1` and must not set
   `MASTRA_PLATFORM_ACCESS_TOKEN`. Only `MastraStorageExporter` is registered.
   Add a regression test that fails if the Mastra process opens a
   non-loopback socket. If feasible, the airgap guard should refuse to launch
   Mastra when the variable is missing.
3. Pin exact Mastra versions plus a lockfile, and record them in
   `THIRD-PARTY-LICENSES/` (Apache-2.0, plus NOTICE handling). Add a check
   that no `ee/` path is resolved from `node_modules`. ARAIL is MIT, so the
   Apache-2.0 deps are fine, but EE is not.
4. MCP tool binds 127.0.0.1 only and is independent of the portal's CSRF
   middleware. Decide on stdio (simplest, no port) vs Streamable HTTP. Prefer
   stdio unless D2 work needs HTTP.
5. Write-back goes through `ExperimentTracker.observe`, not a direct file
   write from Node. Add a narrow, CSRF-gated endpoint or have the Python side
   pull the score. Observation `data` should be
   `{source:"mastra", trace_id, scorer_id, score, mastra_version}` so it
   stays auditable.
6. Mastra storage (LibSQL/DuckDB files) lives under `lab/data/`, is
   git-ignored, and is wiped by the same reset that wipes the lab.
7. The surface is maximus-only. It uses an honest empty state for Node
   missing or < 22.13 and for "not running". No minimalist nav entry, and
   server-side 404 like the other maximus surfaces.
8. Name the scorer honestly in the UI (e.g. "model-graded probe — not a
   3DEval measurement"). Do not present a Mastra score as a benchmark.
9. Iframe framing: verify Studio's response headers. If they block framing,
   proxy it through the portal on loopback rather than weakening any header.
