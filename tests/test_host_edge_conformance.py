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


@pytest.mark.parametrize("case", [c for c in CASES if c["method"] in IMPLEMENTED], ids=lambda c: c["name"])
def test_conformance_case(case, tmp_path, monkeypatch):
    world = World(case["setup"], case["method"], tmp_path, monkeypatch)
    problems = world.run(case)
    assert not problems, "\n".join(problems)
