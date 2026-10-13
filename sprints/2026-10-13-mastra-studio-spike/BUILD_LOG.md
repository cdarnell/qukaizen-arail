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
