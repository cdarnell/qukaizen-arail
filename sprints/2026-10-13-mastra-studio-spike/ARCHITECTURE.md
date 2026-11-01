# Architecture: Mastra Studio as an ARAIL surface (spike)

**Date:** 2026-10-13
**Spec:** [VISION.md](./VISION.md) at e523955c
**Mode:** time-boxed spike. Kill gates D1 and D2 run first; any other work starts only after both pass.

## Restatement

The owner cannot see *why* a Researcher run did what it did. The spike tests one
hypothesis: Mastra Studio, running locally, can give him (1) a hierarchical,
clickable span view of a single Researcher run and (2) a numeric score attached
to that run's ARAIL experiment record. It does this without telemetry or egress,
and ARAIL stays the source of truth for the agent. Mastra is a *viewer and driver*
bolted on from the outside. It reaches the Researcher only through a single
loopback-only tool, `researcher.run_once`, and writes back only through an ARAIL
Python code path (`ExperimentTracker.observe`). The work is maximus-only, opt-in,
manually triggered, and disposable. If egress cannot be proven zero (D1), or the
Researcher appears as one opaque span (D2), the builder stops and records a learning.

## Assumptions

Each one is something that could turn out to be wrong. The **[gate]** items are checked by D1/D2/B0.

- A1 **[gate D1]** `MASTRA_TELEMETRY_DISABLED=1` plus no `MASTRA_PLATFORM_ACCESS_TOKEN` gives zero non-loopback sockets from the CLI, the server, *and* the Studio browser bundle. This is documented but unmeasured.
- A2 **[gate D2]** Mastra can show child spans under a tool-call span when the tool returns structured `steps[]`, or when custom spans are created in the driver from those steps. If neither works, D2 fires.
- A3 The pinned versions (`mastra@1.34.0`, `@mastra/core@1.76.0`, `@mastra/mcp@2.3.0`, plus `@mastra/libsql`, `@mastra/observability` versions resolved at B0 and frozen) do not resolve any `ee/` path for the features used.
- A4 A local OpenAI-compatible model endpoint (Ollama on 127.0.0.1:11434, already an ARAIL optional service) is enough for the thin driver agent and the probe scorer. No cloud key is present or needed.
- A5 `ResearcherAgent._run(parsed_goal)` (researcher.py:1002) can be awaited directly for a single goal outside the background `start()` task (researcher.py:745/773) without corrupting `GoalStore` "current goal" state. **This is unverified.** `_run` reads `goal_store.get_current()`, so `run_once` must refuse when the background loop is active (see contracts).
- A6 `agent_trace.record()` (agent_trace.py:107) records model/tool calls with a `trace_id` that can be scoped to one `run_once` invocation. That makes it the source for `steps[]`. If the recorder is off (`recorder_on()`), `steps[]` is empty, which counts as a D2 fail for that run.
- A7 The dev Mac Node (v25.9.0) satisfies `>=22.13.0`. Clean machines may have no Node, or an older one. That must never break `scripts/setup.sh`.
- A8 Studio serves on 127.0.0.1:4111 when host is configured. The default bind is not trusted; the bind is set explicitly.
- A9 Studio headers may block framing. This is unknown and resolved by check B0.3.

## Data flow

```
 owner clicks "Run once via Mastra" (portal /mastra, maximus only)
        │  POST /api/mastra/run-once   (CSRF envelope: Sec-Fetch-Site + Origin/Host, like airgap toggle)
        ▼
 ARAIL portal (Python) ── spawns ──►  node integrations/mastra-studio/run-once.mjs  {goal, exp_id}
   env: MASTRA_TELEMETRY_DISABLED=1, MASTRA_PLATFORM_ACCESS_TOKEN unset (scrubbed),
        LIBSQL url=file:lab/data/mastra/mastra.db, HOST=127.0.0.1
        │
        ▼
 Mastra driver agent ──MCPClient (stdio)──► python -m arail.mcp.researcher_tool
        │                                        │ run_once(goal)
        │                                        ▼
        │                         ResearcherAgent._run(parsed_goal)  (in-process, single run)
        │                                        │ agent_trace.record(...) keyed by run trace_id
        │                                        │ ExperimentTracker.create/observe (normal Researcher path)
        │◄──── {report, experiment_id, trace_id, steps[]} ─┘
        │
        ├─► custom child spans built from steps[]  (D2 mechanism)
        ├─► probe scorer (local Ollama) → score ∈ [0,1]
        ├─► MastraStorageExporter → lab/data/mastra/mastra.db  (only exporter)
        ▼
 stdout: one JSON line {mastra_trace_id, scorer_id, score, mastra_version, experiment_id}
        │
        ▼
 portal (Python) validates JSON → tracker.observe(exp_id, "mastra probe score", data={...})
        │
        ▼
 lab/data/experiments/<exp_id>.json  ──► GET /api/experiments  (unchanged endpoint)

 Separate long-lived process (optional): `mastra dev`/studio on 127.0.0.1:4111
   reads lab/data/mastra/mastra.db ──► /mastra page: iframe or "open in new tab"
```

Node never writes ARAIL files. Python does the write-back after parsing the child's stdout. This also avoids a new HTTP observe endpoint, because the VISION notes there is none, and adding one would widen the mutation surface.

## Ledger choice: (a) ExperimentTracker

Choice (a). Rationale from the code:
- `ExperimentTracker.observe(exp_id, observation, data)` (experiment_tracker/__init__.py:58) already appends `{date, observation, data, logged_at}` and persists it. The required shape fits in `data` with no schema change.
- `GET /api/experiments` (app.py:4698) already returns `tracker.list_all()`, so W1's read-back needs no new code.
- The Researcher already creates its experiments through the same tracker, so the `experiment_id` that `run_once` returns is a real record.
- (b) The git ledger (`autoresearch/*` branches + `.jsonl`) is flagged non-durable in ROADMAP and belongs to the Tuning loop, not the Researcher. Writing a score there would add a second writer to a known-hazardous store.

Observation written (exact):
`observation="mastra probe score (not a 3DEval measurement)"`,
`data={"source":"mastra","kind":"probe","trace_id":<mastra trace id>,"arail_trace_id":<agent_trace id>,"scorer_id":<id>,"score":<float 0..1>,"mastra_version":"1.34.0","core_version":"1.76.0"}`.

## Interface contracts

### C1. `arail.mcp.researcher_tool` (new, Python, MCP stdio server)
- **Transport:** stdio only, with no port and no socket. This makes the "own loopback bind, independent of portal CSRF" requirement trivially true: there is nothing to bind. Streamable HTTP is used only if D2 proves it is required. If so, it binds 127.0.0.1, uses an ephemeral port and a per-launch random bearer token passed via env, and that change is recorded as drift.
- **Exposes exactly one tool:** `researcher_run_once(goal: str)` returning `{report: str, experiment_id: str|null, trace_id: str, steps: [{name, kind, start_ts, end_ts, input_summary, output_summary}]}`.
- **Preconditions:** `goal` is a non-empty string of at most 2000 chars. The background Researcher is not running (status idle/stopped). `LAB_TIER=maximus`.
- **Postconditions:** the Researcher ran exactly once through `_run`, and agent prompts/config are untouched (W3). `steps[]` comes from `agent_trace` records filtered by the run's `trace_id`. Summaries are truncated to 500 chars each. Steps are capped at 200, with a `truncated: true` flag.
- **Bad input / state:** returns an MCP tool error (no exception escapes) with codes `invalid_goal`, `researcher_busy`, `wrong_tier`, or `run_failed:<class>`. It never starts the background loop.
- **Wrapping, not reimplementation:** a thin function that builds `parsed_goal` the way `/api/research/start` does (reuse that parser; do not copy it) and awaits `_run`. Any change to `researcher.py` is limited to returning or exposing the run's trace_id, if needed. If more than ~20 lines are needed there, the builder stops and reports, because that would violate W3.

### C2. `integrations/mastra-studio/` (new, Node project)
- `package.json` with exact versions (no `^`/`~`), `package-lock.json` committed, `"engines": {"node": ">=22.13.0"}`, `"private": true`.
- `src/mastra/index.ts`: one agent (`researcher-driver`, instructions limited to "call researcher_run_once with the given goal and return its report"), one `MCPClient` (stdio command `python -m arail.mcp.researcher_tool` using ARAIL's venv python, passed by absolute path via env), one scorer, LibSQL storage at `file:${ARAIL_MASTRA_DB}`, observability with **only** `MastraStorageExporter`. No workflows, datasets, editor, agent-builder, or auth.
- `run-once.mjs` **preconditions:** env `MASTRA_TELEMETRY_DISABLED=1` (otherwise exit 3 with no work), no `MASTRA_PLATFORM_ACCESS_TOKEN` (otherwise exit 3), and `ARAIL_MASTRA_DB` under the lab data dir. **Postcondition:** exactly one JSON line on stdout as above, exit 0. On any failure, a single JSON line `{error: <code>}` and a nonzero exit. Never a stack trace on stdout.
- **Scorer:** id `arail-probe-report-grounded-v0`, display label "model-graded probe — not a 3DEval measurement". It is model-graded by the local Ollama model and judges whether the report addresses the goal. Output is clamped to [0,1].

### C3. Portal additions (`src/arail/portal/app.py`, `templates/mastra.html`)
- `_OPTIONAL_SERVICES["mastra-studio"] = "maximus"`. The health probe is reported only when `_port_open("127.0.0.1", MASTRA_STUDIO_PORT or 4111)`, with the probe dict key matching (see the app.py:271 comment).
- `_TIER_SURFACES["maximus"]` gains `"mastra"`. Minimalist does not. `GET /mastra` returns 404 server-side when the tier is not maximus, the same as other maximus surfaces.
- `GET /mastra` page states (exactly one is shown): `node_missing`, `node_too_old (found vX, need >=22.13.0)`, `not_installed` (no `integrations/mastra-studio/node_modules`), `not_running` (with Start button), `running` (iframe or new-tab link per B0.3). Wording is plain, with no traceback text.
- `POST /api/mastra/start` and `/api/mastra/stop` mirror `/api/marimo/start|stop` (app.py:2557/2574). Start runs `npx --no-install mastra dev` (or `studio`, chosen at B0) with `--host 127.0.0.1`, the env from C2, and cwd `integrations/mastra-studio`. It refuses with 409 when the airgap guard fails (below).
- `POST /api/mastra/run-once {goal, experiment_id?}`: CSRF envelope identical to the airgap toggle; loopback bind required. It spawns `run-once.mjs` with a 15-minute timeout, parses the single JSON line, validates it (score is a finite float in [0,1], ids are strings of at most 128 chars, experiment exists), then calls `tracker.observe`. Returns `{experiment_id, mastra_trace_id, score}` or `{error}`.
- **Airgap/telemetry guard (`_mastra_env()`):** builds a child env from scratch with an allowlist (PATH, HOME, NODE_*, ARAIL_MASTRA_DB, OLLAMA base URL, python path), forces `MASTRA_TELEMETRY_DISABLED=1`, and never copies `MASTRA_PLATFORM_ACCESS_TOKEN` or any `*_API_KEY`. A unit test asserts this.

### C4. Setup / arailctl
- No change to `scripts/setup.sh` default path. Install is a separate opt-in `./arailctl mastra install`, which runs `npm ci --ignore-scripts` in `integrations/mastra-studio` after the Node version check. Lifecycle scripts are not executed. If Mastra needs a postinstall, the builder stops and reports it as a finding. `ensure_node` is **not** modified to enforce 22.13, because that would break the agent-browser path on older Nodes. The version check lives only in the mastra install command and the portal page.
- `./arailctl mastra run-once "<goal>"` is the CLI equivalent of the button and goes through the same Python function as the endpoint.
- `scripts/reset.sh` lab reset removes `lab/data/mastra/`. This is covered by the existing `lab/data/` gitignore (.gitignore:42); the builder verifies reset.sh's wipe scope includes it and adds it if not.

## Build steps 0: gates (must run first, no UI code before both PASS)

Artifacts go to `sprints/2026-10-13-mastra-studio-spike/gates/`.

### B0.1 Supply chain and ee/ check (prerequisite to D1)
`scripts/mastra/check-no-ee.sh`, which exits nonzero on any hit:
```
cd integrations/mastra-studio
npm ci --ignore-scripts
# 1. no ee/ directory anywhere in the installed tree
find node_modules -type d -name ee -path '*@mastra*' | tee ../../sprints/.../gates/ee-dirs.txt
# 2. no resolved import of an ee subpath from our code or bundles
grep -rEl "(@mastra/[a-z-]+/[^\"']*ee[/\"'])|(/ee/)" node_modules/@mastra node_modules/mastra --include='*.js' --include='*.mjs' \
  | xargs -r grep -lE "require\(|import" >> gates/ee-refs.txt
# 3. licence census: every package in lock must be in an allowlist (MIT, ISC, Apache-2.0, BSD-2/3, 0BSD, BlueOak-1.0.0)
npx --no-install license-checker-rseidelsohn --production --onlyAllow '<list>' --excludePrivatePackages
```
Pass rule: (1) is empty, or every listed `ee` directory is unreachable from our entry points. That is proven by (4): `node --trace-module`-equivalent load of `run-once.mjs` with `NODE_OPTIONS=--import ./gates/trace-loads.mjs`, a loader hook that logs every resolved URL. No resolved URL may contain `/ee/`. (4) is the authoritative check. (1)-(2) are early warnings recorded in the artifact. Use a licence-checker only if it is already installable offline; otherwise parse `package-lock.json` `license` fields with a 20-line node script, which is preferred because it adds no dependency.
Fail: D3, so stop and report.

### B0.2 D1: egress capture
`scripts/mastra/gate-d1-egress.sh` → `gates/D1-egress.{txt,json}` with `verdict: PASS|FAIL`.
1. Launch with the C3 env, `LAB_MODE=airgapped`: (a) `mastra dev/studio` server, (b) `run-once.mjs` with a fixed goal, (c) the Studio page loaded in headless Chromium (`agent-browser` or Playwright already on the box; do not add a dependency for this) for 60 s, visiting the traces tab.
2. Capture, two independent methods, both required:
   - **Process sockets:** poll `lsof -nP -i -a -p <pids incl. children>` every 250 ms for the full run. Log every remote address.
   - **Packet capture:** `sudo tcpdump -i any -n -w gates/D1.pcap 'not (host 127.0.0.1 or host ::1)'` for the run window, run by the owner if sudo is needed. Then `tcpdump -r` and count packets. A DNS query alone counts as egress (lookup of posthog/mastra hosts).
   - **Browser:** the headless browser logs all request URLs. Every URL host must be `127.0.0.1`/`localhost`.
3. PASS iff the lsof remote set ⊆ {127.0.0.1, ::1}, the pcap non-loopback count from our PIDs' window = 0 (with background OS noise filtered by running a 60 s idle baseline pcap first and diffing destination hosts; any destination in the run capture not in the baseline is a fail, and any host matching `posthog|mastra\.ai|mastra\.cloud` is a fail regardless), and every browser URL is loopback.
4. Negative control (proves the harness sees egress): run once **without** `MASTRA_TELEMETRY_DISABLED` in an online, non-airgapped session and confirm the harness records the PostHog connection. If the control shows nothing, the harness is blind and the result is `INCONCLUSIVE`, which is treated as FAIL.

### B0.3 Framing headers (cheap, alongside D1)
`curl -sI http://127.0.0.1:4111/` and the Studio route used, saved to `gates/studio-headers.txt`. If neither `X-Frame-Options` nor a CSP `frame-ancestors` excludes the portal origin, use an iframe. Otherwise use the **new-tab fallback** (`<a target="_blank" rel="noopener">` to `http://127.0.0.1:4111`). A reverse proxy is **not** built in this spike; it is out of time box and would add a mutation-capable proxy into the portal. Record the decision in BUILD_LOG.

### B0.4 D2: span visibility
`scripts/mastra/gate-d2-spans.sh` → `gates/D2-spans.json` + a Studio screenshot or "Download trace JSON".
1. Minimal C1 + C2 (no portal): run `run-once.mjs` with a fixed goal.
2. Export the trace from LibSQL (query the spans table) or via Studio's trace JSON download.
3. PASS iff the trace has a root agent span, a tool span for `researcher_run_once`, and at least 3 child spans **under or attached to that run** whose names map 1:1 to `steps[]` entries (planning, at least one model call, at least one experiment step), each with non-empty input/output summaries visible in Studio.
4. Mechanism order: (i) check whether Mastra renders structured tool output as nested spans natively (expected: no); (ii) in the driver, after the tool returns, create child spans from `steps[]` using the public `@mastra/observability` span API (timestamps from ARAIL). (ii) is allowed. It is *not* the "ARAIL emits OTel" project, because ARAIL only returns JSON. If (ii) requires a non-public API, `ee/`, or OTel context propagation into Python, then D2 fires.
Fail: **defer.** Stop, write `learnings/2026-10-13-mastra-d2-opaque.md`, and re-scope as an "agent_trace → OTel exporter" question.

**Builder rule on any gate FAIL/INCONCLUSIVE:** commit the gate artifacts and a learning, set the SPRINT.md build row to `stopped (D<n>)`, and report to the orchestrator. Do not attempt workarounds, proxies, patched dependencies, or alternate exporters. Do not continue to portal work.

## Failure modes

| # | Failure | Detection | Recovery |
|---|---|---|---|
| F1 | Mastra phones home (CLI/server/browser) | B0.2 lsof + pcap + browser URL log; T-SEC-1 regression | D1: reject spike, shelve |
| F2 | Telemetry env missing at launch (user override, code drift) | `_mastra_env()` forces it; run-once.mjs exits 3 if unset; T-U-3 | Refuse launch, show plain message |
| F3 | Platform token leaks into child env | allowlist env builder; T-U-4 asserts absent | Never copied; launch proceeds without it |
| F4 | `ee/` code resolved | B0.1 loader-hook URL log; T-SEC-2 in CI-able script | D3: reject that path |
| F5 | Researcher trace opaque | B0.4 | D2: defer, learning |
| F6 | `run_once` collides with running background loop | status check precondition; T-U-6 | `researcher_busy` error, no state change |
| F7 | `run_once` hangs (model stall) | 15 min subprocess timeout; MCP tool timeout | Kill process group, return `timeout`, no observe written |
| F8 | Malformed/oversized child stdout, or score NaN/out of range | strict JSON-line parse + validation; T-U-7 | `bad_child_output`, no observe written |
| F9 | Write-back to nonexistent experiment | tracker `_load` raises → caught | `experiment_missing`, logged |
| F10 | Cross-site page triggers run-once/start | CSRF envelope (Sec-Fetch-Site + Origin/Host); T-SEC-3 | 403 |
| F11 | Studio bound on 0.0.0.0 | explicit `--host 127.0.0.1`; T-SEC-4 lsof check of LISTEN addr | Refuse to report "running" if bound non-loopback; stop it |
| F12 | Node missing / <22.13 on clean machine | version probe; setup.sh untouched; T-I-3 | Page shows node_missing/node_too_old; setup still passes |
| F13 | Studio refuses framing | B0.3 headers | New-tab fallback |
| F14 | Surface leaks to minimalist | `_TIER_SURFACES` + server 404; T-U-1 | 404 |
| F15 | npm lifecycle script runs on install | `--ignore-scripts` | Stop and report if Mastra needs one |
| F16 | Mastra DB grows unbounded / survives reset | under `lab/data/mastra/`, gitignored; reset test T-I-4 | `arailctl reset` wipes it |
| F17 | Score mistaken for a benchmark | label in observation text, `kind:"probe"`, UI label; T-U-8 | n/a (copy) |
| F18 | Zombie Node processes after stop / portal exit | own process group; stop kills group; T-I-5 | `/api/mastra/stop` kills pgid |
| F19 | MCP stdio server prints to stdout (corrupts protocol) | logging forced to stderr; T-U-9 | Fix at source |
| F20 | Local model unavailable (Ollama down) | probe before run | `model_unavailable` plain message, no observe |

## Test strategy

Python tests under `tests/` (pytest, existing conventions). Node-side checks are scripts that the owner/QA run on the Mac. They are not added to default CI, because CI must not require Node 22.13.

- **Unit (Python):**
  - T-U-1 `/mastra` 404 on minimalist and 200 on maximus. `"mastra"` is absent from `_TIER_SURFACES["minimalist"]`. `_OPTIONAL_SERVICES["mastra-studio"] == "maximus"` (F14).
  - T-U-2 page-state resolver returns each of 5 states from mocked node version/install/port (F12).
  - T-U-3 `_mastra_env()` always sets `MASTRA_TELEMETRY_DISABLED=1`, even if the parent env sets it to `0` (F2).
  - T-U-4 `_mastra_env()` drops `MASTRA_PLATFORM_ACCESS_TOKEN`, `*_API_KEY`, `*_TOKEN` (F3).
  - T-U-5 tool input validation: empty, too long, non-string goal (C1).
  - T-U-6 `researcher_busy` when the background loop is active, with GoalStore unchanged (F6).
  - T-U-7 child-output parser: valid line, garbage, two lines, NaN, score 1.5, 10 MB line, missing fields, so no observe is written in bad cases (F8).
  - T-U-8 observation text and `data` match the exact contract including `kind:"probe"` (F17).
  - T-U-9 stdio server writes nothing but MCP frames to stdout during a run (F19).
  - T-U-10 `experiment_missing` and `model_unavailable` paths (F9, F20).
- **Integration (Mac, Node ≥22.13):**
  - T-I-1 **W1 end-to-end:** `arailctl mastra run-once "<fixed goal>"`. Then `GET /api/experiments` shows the observation with `trace_id` that exists in the LibSQL db, and the trace is visible in Studio.
  - T-I-2 the timeout path kills the process group (F7, F18).
  - T-I-3 **clean-machine setup:** `scripts/setup.sh` on a machine/container with no Node and with Node 20 succeeds unchanged, and the minimalist portal has no Mastra traces (F12, D5).
  - T-I-4 `arailctl reset` removes `lab/data/mastra/` (F16).
  - T-I-5 stop leaves no `node`/`mastra` processes (F18).
- **Regression:**
  - T-R-1 `git diff main -- src/arail/agents/researcher.py` is limited to the trace-id exposure, and no prompt/config strings change (W3). QA inspects this.
  - T-R-2 existing portal tests, experiment tracker tests, and CSRF middleware tests stay green.
  - T-R-3 `aerollm_*` names, backend ids, and `AERO_*` env reads are untouched (`git diff` grep).
- **Security:**
  - T-SEC-1 re-run B0.2 D1 harness on the final build (not just the gate build) (F1).
  - T-SEC-2 re-run B0.1 loader-hook ee check on the final lockfile (F4).
  - T-SEC-3 cross-site/missing-Origin POSTs to `/api/mastra/{start,stop,run-once}` get 403 (F10).
  - T-SEC-4 Studio LISTEN socket is 127.0.0.1 only. The MCP server opens no listening socket (F11).
  - T-SEC-5 Goal string with shell metacharacters reaches the child only via argv/JSON stdin, never through a shell (`shell=False`).
- **Performance:** not a hot path. Record wall time of one run-once vs the same goal via the background loop. Overhead above 20% is flagged but is not a gate.

## Licence / naming

- Add `THIRD-PARTY-LICENSES/mastra/` holding Apache-2.0 text and any NOTICE files from the pinned packages, with versions listed. The Mastra deps are **not** vendored into the release bundle. They are installed opt-in, so the hash-pinned bundle is untouched.
- Naming freeze respected: no `aerollm_*`, `AERO_*`, `tier1-aerollm`, or `AeroLLMBackend` edits. New identifiers use `mastra`.

## Disposal rule and rollback

- **Disposal (from VISION):** D1/D3 fire → reject and delete the branch's Mastra code; keep only gate artifacts + learning. D2 fires → defer; keep gate artifacts + learning; the MCP tool may be kept only if it is independently useful (owner's call). D4 (fewer than 2 unprompted opens in 14 days, self-reported in SPRINT.md) → remove the `/mastra` surface, the `_OPTIONAL_SERVICES`/`_TIER_SURFACES` entries, and `integrations/mastra-studio/`; keep the MCP tool only if it proved useful alone. D5 → maximus-only opt-in, or delete.
- **Rollback:** every addition is additive and isolated: new module `arail/mcp/`, new dir `integrations/mastra-studio/`, new template, new endpoints, two dict entries, one reset path, and one `THIRD-PARTY-LICENSES/mastra/`. Rollback is `git revert` of the merge plus `rm -rf lab/data/mastra integrations/mastra-studio/node_modules`. Probe observations already written to experiment JSON are harmless and stay as history (they are labelled `kind:"probe"`). No migration is needed.

## Tech debt

**Added:**
- A second runtime (Node ≥22.13) for an optional surface.
- A pinned third-party dependency tree that needs CVE watch.
- A `run_once` entry point that is a second way to drive the Researcher, and a gate harness (pcap/lsof) that is Mac-specific.
- If B0.3 forces new-tab, the surface is less integrated than the notebook.

**Repaid:**
- The first structured score path into `observations[]`.
- The first MCP exposure, which could later serve PaperAgents/3DEval.
- A reusable egress-capture harness that other optional services (opencode, open-notebook) currently lack.

**Net:** positive while the spike lives. Follow-ups to file at merge:
1. Generalize the egress harness to all `_OPTIONAL_SERVICES`.
2. Decide the D4 outcome at day 14.
3. Ask 3DEval whether it should consume the `kind:"probe"` observations or ignore them.

## Recommended implementation order

1. B0.1 pin and install Mastra under `integrations/mastra-studio/`; ee/licence check. FAIL → stop.
2. C1 MCP stdio tool (minimal) + C2 driver + `run-once.mjs`, with no portal code.
3. B0.2 D1 egress gate, with negative control, and B0.3 header capture. FAIL → stop.
4. B0.4 D2 span gate. FAIL → stop (defer).
5. **Checkpoint:** commit gate artifacts and report PASS in BUILD_LOG before continuing.
6. Python write-back (`_mastra_env`, child runner, validation, `tracker.observe`) + `arailctl mastra run-once|install`.
7. Portal surface: registry entries, `/mastra` page states, start/stop/run-once endpoints, iframe or new-tab per B0.3.
8. Reset path, THIRD-PARTY-LICENSES, unit tests T-U-*, then integration T-I-*, then re-run T-SEC-1/2 on the final build.
9. Add a W4 tracking row to SPRINT.md for the owner's self-report.
