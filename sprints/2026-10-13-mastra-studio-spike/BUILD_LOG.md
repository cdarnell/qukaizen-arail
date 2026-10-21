# Build log: Mastra Studio spike

**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md)
**Started:** 2026-10-13  **Status:** stopped at gate D1

## Plan

| # | Files | Change | Test | Commit ref |
|---|---|---|---|---|
| 1 | integrations/mastra-studio/{package.json,package-lock.json,src/mastra/index.ts}, scripts/mastra/check-no-ee.sh, license-census.mjs | B0.1 pin + ee/licence check | scripts | gate commit |
| 2 | scripts/mastra/gate-d1-egress.sh | B0.2 lsof harness + negative control, B0.3 headers | scripts | gate commit |
| 3 | B0.4 D2, steps 6-9 | not started (stop rule) | - | - |

## Execution
- B0.1: exact pins installed with --ignore-scripts (no lifecycle scripts needed). Resolved: @mastra/libsql 1.26.0, @mastra/observability 1.19.0.
  ee dirs present (see gates/ee-dirs.txt); loader-hook reachability NOT run (needs run-once.mjs). Licence census: one non-allowlisted (caniuse-lite CC-BY-4.0).
- B0.2: FAIL on lsof (2 non-loopback :443 sessions, telemetry off); control saw PostHog; tcpdump NOT RUN (no root); browser log not run.
- B0.3: no X-Frame-Options/CSP; iframe permitted (decision moot while stopped).
- Deltas: minimal C1 MCP server and run-once.mjs not built (Python mcp pkg absent, Ollama not running, gate already failed on server-only run).
  `mastra dev` has no --host flag and binds *:4111.

## Architect feedback required
1. C3 assumes `mastra dev --host 127.0.0.1`; flag does not exist, server binds all interfaces.
2. Licence allowlist omits CC-BY-4.0 (caniuse-lite data).

## Final state
No portal/Python code changed. No researcher.py change. Python tests not run (no Python code touched).

---
## Resume 2026-10-21 (after owner approval; D1 server leg passed)

### Executed
- `src/arail/mcp/researcher_tool.py` (C1): stdio-only FastMCP server, one tool `researcher_run_once`; validation, `wrong_tier`, `researcher_busy`
  (checks in-process status and `run_state.json`), wraps `ResearcherAgent._run` after `goal_store.set_goal` (archives the previous goal like POST /api/goal).
  researcher.py NOT modified (0 lines).
- `integrations/mastra-studio/run-once.mjs`, `src/mastra/{config,run-probe}.mjs`, `index.ts`: exits 3 unless
  `MASTRA_TELEMETRY_DISABLED=1`, `MASTRA_OFFLINE=1`, `MASTRA_AUTO_REFRESH_PROVIDERS=false` and no platform token; loopback-only scorer endpoint.
- Tests: `tests/test_researcher_tool.py` 12 passed (validation, tier, busy leaves GoalStore untouched, steps mapping/cap, stdout is MCP frames only).
- Real run: `scripts/mastra/run-once-env.sh` produced a Researcher run, a Mastra trace and the one-line JSON (score 0 from llama3.2:1b).

### Gate results
| Gate | Verdict | Evidence |
|---|---|---|
| B0.1 ee/licence | STOP: owner decision. No `/ee/` URL, but `core/dist/ee-*.js` chunk loads. Licence census passes with CC-BY-4.0 allowed | gates/B0.1-result.md, loaded-urls.txt |
| B0.2 D1 | server leg PASS (owner); browser leg FAIL: Studio page requests registry.npmjs.org (x15) and models.dev | gates/D1-egress.json, D1-full-browser-requests.txt |
| B0.3 | iframe permitted (no XFO/CSP) | studio-headers-test.txt |
| B0.4 D2 | PASS via public span API (8 children, non-empty I/O, read from Studio API) | gates/D2-spans.json |

### Drift from ARCHITECTURE.md (for the architect)
1. Three-flag child env (`MASTRA_OFFLINE=1`, `MASTRA_AUTO_REFRESH_PROVIDERS=false` besides telemetry) is required; `_mastra_env()` was not yet written (stopped before step 6).
2. A6 wrong: `agent_trace.trace_id` is per router call, not per run. Steps are scoped by time window from run start (exact, because the MCP process only runs this one job) and the returned `trace_id` is a minted run id. Ring records exist even with the recorder off (only bodies are stripped), so steps are not empty when the recorder is off.
3. Driver calls the MCP tool deterministically inside a root `agent_run` span; no LLM tool-calling (1b model is not reliable). `researcher-driver` Agent is registered for Studio only.
4. `mastra dev` has no `--host`; bind via `server.host` (done, verified `127.0.0.1:4111`).
5. `addScore` is not persisted by LibSQL; the score lives in the ARAIL ledger.

### Stopped
Not built: `_mastra_env()`, Python write-back, portal surface, arailctl, THIRD-PARTY-LICENSES, T-U-2..4,7,8,10 (steps 6-8). Reason: browser-leg D1 FAIL and B0.1 ee-chunk decision. No researcher.py change.
Commits: see `git log` on the branch.
