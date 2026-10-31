"""A7/A8 - ARAIL's real edge routes against the vendored buddy.host/v1 conformance cases.

The same 126 cases run against Buddy's FakeHost in qukaizen-buddy; here they run
against the actual portal app. A case that fails is a disagreement between the two sides.
"""

from __future__ import annotations

import pytest

from tests.buddy_conformance import World, load_cases

CASES = load_cases()
IMPLEMENTED = {"handshake", "trace.record", "hold_all", "pause_research", "brain.chat", "brain.models"}


def test_the_vendored_suite_is_the_one_in_the_contract():
    from arail.buddy_host import CONTRACT
    assert len(CASES) >= 120
    assert {c["method"] for c in CASES} == set(CONTRACT["methods"])


@pytest.mark.parametrize("mount", ["edge", "edge-socket"])
@pytest.mark.parametrize("case", [c for c in CASES if c["method"] in IMPLEMENTED], ids=lambda c: c["name"])
def test_conformance_case(case, mount, tmp_path, monkeypatch):
    """Each shared case runs against the box-only edge app in-process and over a real TCP socket on 127.0.0.1: identical results.
    (The portal's own mount was removed in S5; test_the_portal_serves_no_edge below pins that.)"""
    if mount == "edge-socket" and case["setup"].get("peer", "127.0.0.1") not in ("127.0.0.1", "::1"):
        pytest.skip("a real TCP connection to loopback always arrives from loopback")
    world = World(case["setup"], case["method"], tmp_path, monkeypatch, mount=mount)
    problems = world.run(case)
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("case", [c for c in CASES if c["method"] in IMPLEMENTED][:6], ids=lambda c: c["name"])
def test_the_portal_serves_no_edge_since_s5(case, tmp_path, monkeypatch):
    """S5 removed the `:8080` mount: the portal answers every `/api/host/v1/*` request with 404, whatever the credential. The edge app is the only server of the prefix."""
    from fastapi.testclient import TestClient
    from arail.portal.app import app
    from tests.buddy_conformance import HTTP, PATHS
    World(case["setup"], case["method"], tmp_path, monkeypatch)
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="http://127.0.0.1")
    for headers in ({}, {"Authorization": "Bearer bhc1_" + "A" * 43}, {"Authorization": "Bearer bhc1_" + "A" * 43, "X-Buddy-Contract": "sha256:" + "0" * 64}):
        r = client.request(HTTP[case["method"]], PATHS[case["method"]], headers=headers)
        assert r.status_code in (404, 401), (r.status_code, r.text[:120])
        assert "buddy_host_error" not in r.text


def test_the_portal_does_not_route_the_prefix_at_all():
    from arail.portal.app import app
    assert not [r.path for r in app.routes if getattr(r, "path", "").startswith("/api/host/v1")]
