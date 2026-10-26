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


@pytest.mark.parametrize("mount", ["portal", "edge", "edge-socket"])
@pytest.mark.parametrize("case", [c for c in CASES if c["method"] in IMPLEMENTED], ids=lambda c: c["name"])
def test_conformance_case(case, mount, tmp_path, monkeypatch):
    """Each shared case runs against the portal's mount, the box-only edge app in-process, and the real edge
    app over a TCP socket on 127.0.0.1:8089: identical results."""
    if mount == "edge-socket" and case["setup"].get("peer", "127.0.0.1") not in ("127.0.0.1", "::1"):
        pytest.skip("a real TCP connection to loopback always arrives from loopback")
    world = World(case["setup"], case["method"], tmp_path, monkeypatch, mount=mount)
    problems = world.run(case)
    assert not problems, "\n".join(problems)
