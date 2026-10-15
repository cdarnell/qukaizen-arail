# D1 egress attribution (2026-10-15)

Source: owner-run packet capture `D1.pcap` (whole-machine, 60 s, telemetry disabled) correlated with the lsof poll of the `mastra dev` process.

| Remote (lsof) | Identified as | Evidence | Cause in Mastra |
|---|---|---|---|
| 172.67.69.147:443 (and 104.26.8.108 / .9.108 in the earlier run) | `models.dev` | DNS answer for `models.dev` returned exactly these three A records, 09:28:03.05, same second as the SYN | Provider-registry auto refresh (`@mastra/core` provider-registry, GatewayRegistry.startAutoRefresh) |
| 52.14.132.233:443 | `api.netlify.com` | DNS CNAME/A answer at 09:28:03.50; TLS SNI `api.netlify.com` seen in the flow | Netlify AI-gateway `fetchProviders()` (`GET /api/v1/ai-gateway/providers`), same refresh |

Both are unauthenticated GETs of public model/provider lists made by `mastra dev` at server start. They are NOT the PostHog analytics path (that was only seen in the negative control, with telemetry on).

Why it fired: `mastra dev` sets MASTRA_DEV, and `provider-registry` auto-refreshes when `MASTRA_AUTO_REFRESH_PROVIDERS !== "false"` and dev, unless `MASTRA_OFFLINE` is "true"/"1" (documented in the source: "all network fetches for provider data are skipped").

Remediation (committed): the child environment now sets `MASTRA_OFFLINE=1` and `MASTRA_AUTO_REFRESH_PROVIDERS=false` in addition to `MASTRA_TELEMETRY_DISABLED=1`.

Re-run (lsof poll, 45 s, env -i + the three flags): zero non-loopback sockets. Listener `*:4111`.
Second finding (F11) fixed: `server: { host: '127.0.0.1' }` in `src/mastra/index.ts` → listener `127.0.0.1:4111`.

## Status: D1 is NOT yet PASS
The lsof poll is clean, but the gate requires the packet capture (a DNS lookup counts as egress). One owner-run capture with the new flags is needed to close it.
