#!/usr/bin/env python3
"""Criteria 1, 3, 4 and 5 of scripts/s4-win.sh. Every number says what it means and why the bar is what it is."""
from __future__ import annotations

import http.client
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

REPO = Path(os.environ["S4_REPO"])
TMP = Path(os.environ["S4_TMP"])
PASS = os.environ["S4_PASS"]
PORT = int(os.environ["PORTAL_PORT"])
EDGE = int(os.environ["ARAIL_EDGE_PORT"])
PID = os.environ["S4_PORTAL_PID"]
DATA = Path(os.environ["ARAIL_DATA_DIR"])
fails = 0


def check(ok: bool, label: str, value, rule: str, why: str, where: str = "") -> None:
    global fails
    fails += 0 if ok else 1
    print(f"{'ok  ' if ok else 'FAIL'} {label}: {value}")
    print(f"       {rule}. {why}" + (f" If it fails: {where}" if (where and not ok) else ""))


def req(port, method, path, headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body)
        h.setdefault("Content-Type", "application/json")
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    text = r.read().decode("utf-8", "replace")
    out = (r.status, dict((k.lower(), v) for k, v in r.getheaders()), text)
    c.close()
    return out


print("== criterion 4: a fresh install works ==")
check(not (DATA / "portal.token").exists(), "portal.token before onboarding", "absent",
      "must be absent", "With no passphrase there is nothing to protect with a token and nothing to log in with",
      "startup minted a token for an unonboarded lab (app.py startup hook)")
st, hd, _ = req(PORT, "GET", "/api/jobs/state")
check(st == 401, "an API call before onboarding", st, "must be 401 (lab_not_onboarded)",
      "The pre-onboarding gate is unchanged")
st, hd, body = req(PORT, "POST", "/api/welcome/setup", body={"passphrase": PASS, "confirm": PASS})
cookie = re.match(r"(arail_sid_\d+)=([^;]+)", hd.get("set-cookie", ""))
check(st == 200 and cookie is not None and "httponly" in hd["set-cookie"].lower()
      and "samesite=strict" in hd["set-cookie"].lower(), "setup answers with a session cookie",
      f"{st}, {cookie.group(1) if cookie else None}", "must be 200 plus an HttpOnly SameSite=Strict cookie",
      "This is what lands the browser on a logged-in dashboard with no extra step",
      "/api/welcome/setup in portal/app.py")
jar = {"Cookie": f"{cookie.group(1)}={cookie.group(2)}"} if cookie else {}
st, _, _ = req(PORT, "GET", "/research", headers=jar)
check(st == 200, "a page with that cookie", st, "must be 200", "The cookie, not the passphrase, now opens pages")
tok = DATA / "portal.token"
mode = stat.S_IMODE(os.stat(tok).st_mode) if tok.exists() else None
dmode = stat.S_IMODE(os.stat(DATA).st_mode)
check(mode == 0o600 and dmode == 0o700, "portal.token / data dir modes", f"{oct(mode) if mode else None} / {oct(dmode)}",
      "must be 0o600 / 0o700", "Only the lab's owner can read the bearer; POSIX permissions are its whole protection")
token = tok.read_text().strip()

print("\n== criterion 1: unauthenticated requests are refused, on every route ==")
dump = subprocess.run([sys.executable, "-c",
                       "import json;from arail.portal.app import app;from arail.portal.route_table import flatten;"
                       "print(json.dumps([[e.path,sorted(e.methods),e.kind] for e in flatten(app)]))"],
                      capture_output=True, text=True, env={**os.environ, "ARAIL_EDGE_PORT": "0"})
routes = json.loads(dump.stdout.strip().splitlines()[-1])
api = [r for r in routes if r[2] == "route" and r[0].startswith("/api")]
check(len(api) >= 175, "/api routes in the table", len(api), "must be >= 175",
      "A probe that tried few routes proves little; the walk includes routers pulled in with include_router "
      "(a naive walk finds fewer)", "route_table.flatten missed a router")
sys.path.insert(0, str(REPO / "src"))
from arail.portal import auth  # noqa: E402

tried = bad = 0
offenders = []
for path, methods, kind in routes:
    p = re.sub(r"\{[^}]+\}", "x", path)
    reqs = [(m, p) for m in methods if m not in ("HEAD", "OPTIONS")] if kind == "route" else [("GET", p + "/x")]
    for m, pp in reqs:
        if auth.is_exempt(pp, m) or pp.startswith(auth.EDGE_PREFIX):
            continue
        tried += 1
        s, h, _ = req(PORT, m, pp)
        if s not in (401, 302):
            bad += 1
            offenders.append(f"{m} {pp} -> {s}")
check(bad == 0, f"non-exempt answers other than 401/302 (of {tried} requests, no header at all)", bad, "must be 0",
      "Any 2xx here means a local process, or Buddy's box, can read or change the lab without the passphrase",
      "; ".join(offenders[:8]))
st, _, _ = req(PORT, "POST", "/api/airgap/toggle", body={"target": "hybrid"})
check(st == 401, "airgap flip with no Origin and no credential", st, "must be 401",
      "Headers stop browsers; curl needs the credential too")
st, _, _ = req(PORT, "GET", "/metrics")
check(st == 401, "/metrics without a credential", st, "must be 401", "Prometheus scrapers now use the bearer")
for p in ("/health", "/healthz", "/api/system/health", "/api/instance"):
    s, _, _ = req(PORT, "GET", p)
    check(s == 200, f"anonymous {p}", s, "must be 200", "Liveness and readiness probes (arailctl, start.sh, the SRE agent) stay anonymous")

print("\n== criterion 3: programs still work ==")
B = {"Authorization": f"Bearer {token}"}
st, _, _ = req(PORT, "GET", "/api/jobs/state", headers=B)
check(st == 200, "bearer on /api/jobs/state", st, "must be 200", "Scripts and arailctl send the token from portal.token")
st, _, _ = req(PORT, "GET", "/api/openai/v1/models", headers=B)
st2, _, _ = req(PORT, "GET", "/api/openai/v1/models")
check(st == 200 and st2 == 401, "OpenAI shim with / without the bearer", f"{st} / {st2}", "must be 200 / 401",
      "(model list stands in for a completion: no model server runs in this throwaway lab)")
env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
r = subprocess.run(["bash", str(REPO / "scripts/status.sh")], capture_output=True, text=True, env=env, cwd=REPO)
out = r.stdout + r.stderr
check("401" not in out and "Traceback" not in out, "arailctl status output mentions 401", "no" if "401" not in out else "yes",
      "must be no", "status uses only anonymous probes plus the token helper for the jobs line", "scripts/status.sh")
check("Scheduler" in out, "status shows the Scheduler (jobs) line", "Scheduler" in out, "must be shown",
      "svc_portal_curl handed curl the token; without it status prints 'needs arailctl as the lab owner'")
r = subprocess.run(["bash", str(REPO / "arailctl"), "auth", "token"], capture_output=True, text=True, env=env, cwd=REPO)
check(r.returncode == 0 and token not in r.stdout and "token id:" in r.stdout, "arailctl auth token", f"exit {r.returncode}",
      "must exit 0 and print an id, not the token", "The value never reaches a terminal or a log", r.stdout + r.stderr)
home = TMP / "buddy-home"
home.mkdir(mode=0o700)
r = subprocess.run(["bash", str(REPO / "arailctl"), "buddy", "link", "--buddy-home", str(home), "--yes"],
                   capture_output=True, text=True, env=env, cwd=REPO)
rec = json.loads((home / "link.json").read_text()) if (home / "link.json").exists() else {}
check(r.returncode == 0 and rec.get("edge_base_url") == f"http://127.0.0.1:{EDGE}/api/host/v1", "arailctl buddy link writes edge_base_url",
      rec.get("edge_base_url"), f"must name the edge port {EDGE}", "S5 will let the box dial exactly this one target", r.stdout + r.stderr)
r = subprocess.run(["bash", str(REPO / "arailctl"), "buddy", "status"], capture_output=True, text=True, env=env, cwd=REPO)
check(r.returncode == 0 and "401" not in r.stdout + r.stderr, "arailctl buddy status", f"exit {r.returncode}", "must exit 0",
      "It reads local files only", r.stdout + r.stderr)

print("\n== criterion 5: the edge listener is edge only ==")
st, _, body = req(EDGE, "POST", "/api/host/v1/handshake")
check(st == 401 and "credential_missing" in body, "edge handshake without a credential", st, "must be 401 credential_missing",
      "The capability credential is required on every request")
leaks = []
for p in ("/", "/login", "/api/jobs/state", "/api/instance", "/metrics", "/health", "/static/style.css", "/api/auth/whoami"):
    s, _, _ = req(EDGE, "GET", p)
    if s != 404:
        leaks.append(f"{p}->{s}")
check(not leaks, "portal paths answered on the edge port (of 8 probed)", len(leaks), "must be 0 (all 404)",
      "The edge app has no portal routes behind it, so a portal auth bug cannot be reached through it", ", ".join(leaks))
st, _, _ = req(EDGE, "GET", "/api/jobs/state", headers={"Host": "evil.example", **B})
check(st == 403, "edge with a non-loopback Host header", st, "must be 403", "DNS-rebinding guard")
ls = subprocess.run(["lsof", "-a", "-p", PID, "-iTCP", "-sTCP:LISTEN", "-nP"], capture_output=True, text=True).stdout
socks = [l.split()[8] for l in ls.splitlines()[1:] if len(l.split()) > 8]
nonloop = [s for s in socks if not s.startswith("127.0.0.1:")]
check(not nonloop and f"127.0.0.1:{EDGE}" in socks and f"127.0.0.1:{PORT}" in socks, "listening sockets of the portal process",
      ", ".join(sorted(socks)), "must be only 127.0.0.1 (portal + edge)", "Nothing is bound to 0.0.0.0, a LAN address or a bridge (G3)",
      f"non-loopback: {nonloop}")

print(f"\n{fails} check(s) failed" if fails else "\nall checks passed")
sys.exit(1 if fails else 0)
