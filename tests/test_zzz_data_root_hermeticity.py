"""Regression guard: no test may leave agent-trace / flight-recorder state
in the real, developer-owned DATA_DIR.

Named ``test_zzz_...`` so pytest's default (alphabetical, filesystem-order)
collection runs this file near the very end of a session — the point at
which any earlier test's isolation failure would have already left its
mark on the real ``lab/data/`` directory. This is the check
`tests/conftest.py`'s `_isolated_agent_observability_data_root` autouse
fixture exists to make unnecessary; if this file ever fails, some test
somewhere bypassed that fixture (most likely by capturing
``arail.config.DATA_DIR`` **by value** at import time — e.g.
``from arail.config import DATA_DIR`` at module scope — rather than
reading the ``config.DATA_DIR`` attribute fresh inside a function, which
is the only shape the autouse fixture's per-test monkeypatch can redirect).

``_REAL_DATA_DIR`` is captured at collection time (module import), before
any per-test fixture has had a chance to monkeypatch ``config.DATA_DIR``
— it is the one genuine, unredirected value for the whole session.
"""

from __future__ import annotations

from arail import config

_REAL_DATA_DIR = config.DATA_DIR


def test_agent_traces_file_absent_in_real_data_dir():
    path = _REAL_DATA_DIR / "agent_traces.jsonl"
    assert not path.exists(), (
        f"{path} exists in the real DATA_DIR — some test wrote an agent "
        "trace without redirecting config.DATA_DIR. See this file's "
        "module docstring and conftest.py's "
        "_isolated_agent_observability_data_root fixture."
    )


def test_agent_traces_rotation_file_absent_in_real_data_dir():
    path = _REAL_DATA_DIR / "agent_traces.jsonl.1"
    assert not path.exists(), f"{path} exists in the real DATA_DIR"


def test_flight_recorder_state_absent_in_real_data_dir():
    path = _REAL_DATA_DIR / "flight_recorder.json"
    assert not path.exists(), (
        f"{path} exists in the real DATA_DIR — some test flipped the "
        "flight recorder without redirecting config.DATA_DIR."
    )


def test_legacy_bodies_notice_state_absent_in_real_data_dir():
    path = _REAL_DATA_DIR / "legacy_bodies_notice.json"
    assert not path.exists(), (
        f"{path} exists in the real DATA_DIR — some test dismissed the "
        "legacy-bodies notice without redirecting config.DATA_DIR."
    )
