# Mastra gates, round 2: browser egress fails D1, an ee chunk loads

Date: 2026-10-21. Sprint: 2026-10-13-mastra-studio-spike. Supersedes the "D1 PASS" status for the browser leg.

- Server leg: PASS (owner pcap, MASTRA_OFFLINE + AUTO_REFRESH off + server.host 127.0.0.1).
- Browser leg: FAIL. With Studio open in a headless browser the page itself fetches
  `https://registry.npmjs.org/{mastra,@mastra/core,@mastra/mcp,@mastra/libsql,@mastra/observability}` (version checks,
  repeated) and `https://models.dev/logos/ollama.svg`. None of the three env flags governs this; it is Studio client code.
  The owner pcap never loaded the browser, so it could not see this. Evidence: gates/D1-full-browser-requests.txt.
- B0.1: no module URL contains `/ee/`, but `@mastra/core/dist/ee-*.js` (bundled auth/EE licence+RBAC/FGA code) loads on any
  `Agent` import. Dormant without a licence key. Owner must decide whether "ee code loaded but inert" breaches F4/D3.
- D2 mechanism (ii) works: root `agent_run` -> `tool_call researcher_run_once` -> 8 children (model call, planning, experiment)
  with non-empty input/output, read back from Studio's own trace API. Only public API (ObservabilityInstance.startSpan,
  Span.createChildSpan with startTime). Caveats: span I/O must be objects (a bare string stored as `{}`); child end times are
  wall-clock at export, not ARAIL's; the driver calls the tool deterministically (a 1b model cannot be trusted to call it), so
  "agent" is a span label, not an LLM in the loop; addScore is not persisted by LibSQL ("does not support batch creating scores").
- Options for the owner (builder does not pick): block browser requests (e.g. launch Studio in a browser profile with
  allowlist-only network, or a loopback-only proxy: ARCHITECTURE currently forbids a proxy in this spike), accept
  the Studio page's version/logo fetches as documented non-telemetry egress and relax the rule, or shelve.
