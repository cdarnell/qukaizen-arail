#!/usr/bin/env python3
"""S4 kill-criterion measurement: log in, then load every dashboard surface in a real browser.

Needs `pip install playwright` and a Chrome install (it drives the system Chrome; no browser
download). Not part of the ARAIL test suite; `scripts/s4-win.sh` calls it when playwright is
importable.

    s4_surfaces.py --base http://127.0.0.1:8080 --passphrase ... \
        --surfaces dashboard:/,chat:/chat,... [--extra worlds:/worlds,...]

Per page it records the status, the final URL, console messages that mention 401/403, same-origin
responses that were 401, other failed same-origin requests, and how many EventSource streams the
page opened. The line that matters is printed last, with its meaning (standing rule).
"""
from __future__ import annotations

import argparse
import json
import sys
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

INIT = "window.__es=0;(function(){var E=window.EventSource;if(!E)return;window.EventSource=function(u,c){window.__es++;return new E(u,c)};window.EventSource.prototype=E.prototype;})();"


def pairs(text: str):
    return [tuple(p.split(":", 1)) for p in text.split(",") if p]


def measure(ctx, base: str, name: str, path: str) -> dict:
    page = ctx.new_page()
    page.add_init_script(INIT)
    origin = urlparse(base).netloc
    rec = {"page": name, "path": path, "status": None, "final_url": None, "console_401_403": 0,
           "same_origin_401": 0, "same_origin_failed": [], "event_sources": 0}

    def on_response(r):
        if urlparse(r.url).netloc != origin:
            return
        if r.status == 401:
            rec["same_origin_401"] += 1
            rec["same_origin_failed"].append(f"401 {urlparse(r.url).path}")
        elif r.status >= 400 and r.request.resource_type in ("fetch", "xhr", "eventsource", "document", "script"):
            rec["same_origin_failed"].append(f"{r.status} {urlparse(r.url).path}")

    page.on("response", on_response)
    page.on("console", lambda m: rec.__setitem__(
        "console_401_403", rec["console_401_403"] + (1 if m.type == "error" and ("401" in m.text or "403" in m.text) else 0)))
    try:
        resp = page.goto(base + path, wait_until="load", timeout=30000)
        page.wait_for_timeout(2500)          # let the page's own fetch() and EventSource calls fire
        rec["status"] = resp.status if resp else None
        rec["final_url"] = page.url.replace(base, "")
        rec["event_sources"] = page.evaluate("window.__es || 0")
    except Exception as exc:  # noqa: BLE001
        rec["status"] = f"error:{type(exc).__name__}"
    finally:
        page.close()
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--passphrase", required=True)
    ap.add_argument("--surfaces", required=True)
    ap.add_argument("--extra", default="")
    ap.add_argument("--json", default=None)
    ns = ap.parse_args()
    surfaces, extra = pairs(ns.surfaces), pairs(ns.extra)
    out = {"surfaces": [], "extra": [], "airgap": None}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context()
        login = ctx.new_page()
        login.goto(ns.base + "/login")
        login.fill("input[name=passphrase]", ns.passphrase)
        login.click("button[type=submit]")
        login.wait_for_load_state("load")
        out["login_landed"] = login.url.replace(ns.base, "")
        cookies = [c for c in ctx.cookies() if c["name"].startswith("arail_sid_")]
        out["cookie"] = {"name": cookies[0]["name"], "httpOnly": cookies[0]["httpOnly"],
                         "sameSite": cookies[0]["sameSite"]} if cookies else None
        login.close()
        for name, path in surfaces:
            out["surfaces"].append(measure(ctx, ns.base, name, path))
        for name, path in extra:
            out["extra"].append(measure(ctx, ns.base, name, path))
        # airgap round-trip with the browser's own headers (same-origin fetch, session cookie)
        pg = ctx.new_page()
        pg.goto(ns.base + "/research")
        flip = """async (t) => { const r = await fetch('/api/airgap/toggle', {method:'POST',
                    headers:{'Content-Type':'application/json'}, body: JSON.stringify({target:t})});
                    return r.status; }"""
        st = pg.evaluate("async () => (await (await fetch('/api/airgap/status')).json()).lab_mode")
        other = "hybrid" if st == "airgapped" else "airgapped"
        s1 = pg.evaluate(flip, other)
        mid = pg.evaluate("async () => (await (await fetch('/api/airgap/status')).json()).lab_mode")
        s2 = pg.evaluate(flip, st)
        end = pg.evaluate("async () => (await (await fetch('/api/airgap/status')).json()).lab_mode")
        out["airgap"] = {"start": st, "flip_status": s1, "after_flip": mid, "back_status": s2, "end": end}
        browser.close()

    print(f"login landed on: {out['login_landed']}   cookie: {out['cookie']}")
    print(f"{'page':<10}{'path':<24}{'http':<6}{'401s':<6}{'console':<9}{'other fails':<13}{'SSE':<5}final url")
    for r in out["surfaces"] + out["extra"]:
        others = [f for f in r["same_origin_failed"] if not f.startswith("401")]
        print(f"{r['page']:<10}{r['path']:<24}{str(r['status']):<6}{r['same_origin_401']:<6}"
              f"{r['console_401_403']:<9}{len(others):<13}{r['event_sources']:<5}{r['final_url']}")
        for f in r["same_origin_failed"][:4]:
            print(f"            - {f}")
    bad = [r for r in out["surfaces"] if r["same_origin_401"] or r["console_401_403"]]
    loaded = [r for r in out["surfaces"] if r["status"] == 200 or str(r["final_url"]).startswith("/welcome")]
    n = len(out["surfaces"])
    print()
    print(f"surfaces with any 401 after login: {len(bad)} of {n}  "
          f"(kill if > 3: more than 3 means the cookie model does not fit the dashboard and the "
          f"architect re-plans per surface; a 401 here means a page called an API without the session cookie)")
    print(f"surfaces that loaded (HTTP 200, or the one-shot /welcome?step=world nudge): {len(loaded)} of {n}  "
          f"(must be {n}: a page that does not load after login is a broken dashboard)")
    print(f"airgap round trip: {out['airgap']}  (start == end and the flip changed it: the toggle works "
          f"with the browser's own same-origin headers and the session cookie)")
    if ns.json:
        json.dump(out, open(ns.json, "w"), indent=1)
    return 0 if (len(bad) <= 3 and len(loaded) == n) else 1


if __name__ == "__main__":
    sys.exit(main())
