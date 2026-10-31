# D1 browser leg - final build (2026-10-31)

Owner-run capture `D1-browser.pcap` (gitignored; whole-machine, 14:58:54-15:00:18 local, 19,614 packets) against build 8b1c7838:
`./arailctl mastra start && ./arailctl mastra open && run-once && stop`, restricted Chrome profile closed by the owner before stop.

In-window proof the scenario ran: run lock 14:59:39, 22 Mastra trace spans started 18:59:38Z (= 14:59:38 local), browser profile touched 14:59:54, Studio stop logged 15:00:01.

## Result: PASS
- 0 DNS lookups for npmjs / models.dev / netlify / posthog / mastra / gstatic / googleapis anywhere in the capture (the ~16 requests Studio's UI attempts are refused locally by the host-resolver rule; nothing reaches the resolver).
- No STUN / QUIC / WebRTC: the only non-DNS, non-mDNS UDP in the capture is LAN broadcast (192.168.x.x and 255.255.255.255 on ports 10102, 15600, 6666, 6667, 9999, 9478: smart-home/device chatter).
- Every DNS name seen belongs to other software on the machine:

| Name(s) | Source | Evidence |
|---|---|---|
| github.com, codeload.github.com, api.github.com, broker.actions.githubusercontent.com, *.blob.core.windows.net, python.map.fastly.net | GitHub Actions self-hosted runner (`~/actions-runner`) running a job that used `astral-sh/setup-uv` | Runner `_diag/Worker_20261031-185902-utc.log` shows the setup-uv action download at 18:59Z; a re-run of run-once with a system-wide lsof poll showed `node` pid 20490 = `actions-runner/.../actions/cache` holding 8 sockets to 52.239.172.36:443 (Azure blob), parent chain Runner.Worker |
| api.anthropic.com | Claude desktop/Code | `claude` process sockets in the same lsof poll |
| *.apple.com, aaplimg, akamaiedge, safebrowsing-proxy | macOS | OS services |
| bf.dynatrace.com | another app's beacon | not Mastra/ARAIL (no such host in any ARAIL/Mastra code or log) |

## Method note and caveats
- A whole-machine pcap cannot tag packets by process; attribution rests on (1) zero target lookups, (2) timing, (3) the runner's own logs, (4) a second run-once with a system-wide, per-process lsof poll: no ARAIL process (node, python, ollama, uv) held a non-loopback socket.
- The `python.map.fastly.net` query had no preceding pypi/python query inside the capture (likely a cached CNAME target re-queried by the OS); it coincides with the runner's setup-uv job, and no ARAIL python process opened any socket to it.
- No idle baseline was taken. The Chrome enterprise-policy-forces-a-proxy assumption (REVIEW finding 3) remains untested.
