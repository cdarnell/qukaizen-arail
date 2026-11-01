"""QA (sprint 2026-10-20-buddy-always-on, R-ROUTES-01 / R-AIRGAP-01 route half): the Buddy host edge adds exactly the routes its
contract names, all under ``/api/host/v1/``, and nothing else under that prefix.

FastAPI 0.140 keeps an included router as one ``_IncludedRouter`` entry (``original_router``) instead of copying its routes into
``app.routes``, so a flat ``[r.path for r in app.routes]`` does not see the edge at all. QA's route-table differential against
``origin/main`` (TEST_REPORT) walks the nested routers; this test walks them the same way so the edge cannot silently vanish or grow.
"""

from __future__ import annotations

import json
from pathlib import Path

from arail.portal.app import app
from arail.portal.edge_app import edge_app

CONTRACT = Path(__file__).resolve().parents[1] / "src" / "arail" / "buddy_host" / "contract" / "buddy.host.v1.json"


def _walk(routes):
    for r in routes:
        inner = getattr(r, "original_router", None)
        sub = inner.routes if inner is not None else None
        if sub is not None:
            yield from _walk(sub)
        elif getattr(r, "path", None) is not None:
            for m in sorted(getattr(r, "methods", None) or ["*"]):
                yield r.path, m


def _contract_routes() -> set:
    c = json.loads(CONTRACT.read_text(encoding="utf-8"))
    base = c["transport"]["base_path"]
    out = set()
    for spec in c["methods"].values():
        out.add((base + spec["path"], spec.get("http_method", "POST").upper()))
    return out


def test_the_portal_mounts_no_edge_route_since_s5():
    assert [(p, m) for p, m in _walk(app.routes) if p.startswith("/api/host")] == []


def test_the_edge_routes_are_mounted_on_the_edge_app_and_are_exactly_the_contracts():
    host = {(p, m) for p, m in _walk(edge_app.routes) if p.startswith("/api/host")}
    want = _contract_routes()
    assert {p for p, _ in host} == {p for p, _ in want}
    assert all(p.startswith("/api/host/v1/") for p, _ in host)
    assert len(host) == 6


def test_no_other_route_mentions_buddy_or_host_outside_the_edge_prefix():
    stray = [(p, m) for p, m in _walk(app.routes) if ("buddy" in p.lower() or p.startswith("/api/host")) and not p.startswith("/api/host/v1/")]
    assert stray == []
