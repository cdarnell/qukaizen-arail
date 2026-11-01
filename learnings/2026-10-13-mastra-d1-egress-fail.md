# Mastra D1: server makes outbound connections even with telemetry disabled

Date: 2026-10-13. Sprint: 2026-10-13-mastra-studio-spike. Gate: D1 (stop rule).

- `mastra@1.34.0 dev`, env scrubbed (`env -i`), `MASTRA_TELEMETRY_DISABLED=1`, no platform token, idle 45 s, no browser:
  lsof showed two ESTABLISHED TCP sessions from the server process to non-loopback :443
  (104.26.8.108 Cloudflare, 52.14.132.233 AWS us-east-2). Neither is attributed yet. The CLI bundle references
  mastra.ai, gateway-api.mastra.ai, integrations.mastra.ai, models.dev-style registries.
- Negative control (telemetry on) produced a PostHog connection (3.41.202.171 = us.i.posthog.com), so the harness sees egress.
- `mastra dev` has no `--host` option; it listens on `*:4111` (all interfaces). ARCHITECTURE C3 assumed `--host 127.0.0.1`. (F11)
- Packet capture not run (no root). Treat D1 as FAIL on lsof evidence alone; INCONCLUSIVE on pcap.
- B0.1: 3 `ee` directories in @mastra/core dist (auth/ee, agent-builder/ee, _types/@internal_auth/dist/ee); 12 files reference `/ee/`.
  Loader-hook reachability (authoritative check) not run. Licence census: caniuse-lite CC-BY-4.0 is outside the allowlist (data package).
- Next if resumed: identify the two hosts (lsof + `NODE_DEBUG=net` or a loopback egress-blocking sandbox), find a documented
  switch (e.g. model-registry/ gateway sync disable, server.host config), re-run. Owner decides whether to continue.
