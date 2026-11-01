"""Summarise a Chrome --log-net-log file: URL hosts requested, DNS jobs, non-loopback TCP connects."""
import collections, json, sys
txt = open(sys.argv[1]).read().rstrip()
try:
    d = json.loads(txt)
except ValueError:  # Chrome killed before it closed the JSON
    d = json.loads(txt.rstrip(",") + "]}")
C = d["constants"]
ev = {v: k for k, v in C["logEventTypes"].items()}
src = {v: k for k, v in C["logSourceType"].items()}
urls, dns, tcp, proxy = collections.Counter(), set(), set(), set()
for e in d["events"]:
    p = e.get("params") or {}
    u = p.get("url")
    if isinstance(u, str) and "://" in u:
        urls[u.split("/")[2]] += 1
    if src.get(e["source"]["type"]) == "HOST_RESOLVER_IMPL_JOB" and "host" in p:
        dns.add(p["host"])
    if ev.get(e["type"]) in ("TCP_CONNECT", "TCP_CONNECT_ATTEMPT") and "address" in p:
        tcp.add(p["address"])
nonloop = sorted(a for a in tcp if not a.startswith(("127.0.0.1", "[::1]")))
print(json.dumps({"url_hosts": dict(urls), "resolver_jobs": sorted(dns), "tcp_addresses": sorted(tcp),
                  "tcp_non_loopback": nonloop}, indent=1))
