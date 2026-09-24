"""Legacy calls_by_source["agent"] -> "agent:pre-p1-legacy" migration.

calls_by_source is persisted (costs.py _load/_save), so the accumulated
bare "agent" bucket from every pre-attribution call would never return to
zero no matter what new code does. This is a one-time, idempotent rename
on load (W2's precondition).
"""

from __future__ import annotations

import json

import pytest

from arail.costs import CostTracker


@pytest.fixture
def costs_path(tmp_path, monkeypatch):
    monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path))
    from arail import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    CostTracker._instance = None
    yield tmp_path / "costs.json"
    CostTracker._instance = None


def _seed(path, calls_by_source):
    path.write_text(json.dumps({
        "total_tokens_in": 0, "total_tokens_out": 0,
        "total_cloud_usd": 0.0, "total_calls": 0,
        "total_latency_ms": 0.0, "latency_by_backend": {},
        "calls_by_backend": {}, "calls_by_source": calls_by_source,
        "tokens_by_backend": {}, "cloud_by_backend": {},
        "started_at": 0.0,
    }))


def test_legacy_agent_bucket_renamed_on_load(costs_path):
    _seed(costs_path, {"agent": 42, "ui": 3})
    tracker = CostTracker()
    assert "agent" not in tracker.calls_by_source
    assert tracker.calls_by_source["agent:pre-p1-legacy"] == 42
    assert tracker.calls_by_source["ui"] == 3


def test_migration_is_idempotent_across_two_loads(costs_path):
    _seed(costs_path, {"agent": 42, "ui": 3})
    tracker1 = CostTracker()
    tracker1._save()

    CostTracker._instance = None
    tracker2 = CostTracker()
    assert "agent" not in tracker2.calls_by_source
    assert tracker2.calls_by_source["agent:pre-p1-legacy"] == 42


def test_migration_merges_into_existing_legacy_bucket(costs_path):
    _seed(costs_path, {"agent": 10, "agent:pre-p1-legacy": 5})
    tracker = CostTracker()
    assert tracker.calls_by_source["agent:pre-p1-legacy"] == 15
    assert "agent" not in tracker.calls_by_source


def test_no_legacy_bucket_is_a_no_op(costs_path):
    _seed(costs_path, {"agent:buddy": 4, "ui": 3})
    tracker = CostTracker()
    assert tracker.calls_by_source == {"agent:buddy": 4, "ui": 3}


def test_fresh_lab_with_no_costs_file_has_no_legacy_bucket(costs_path):
    tracker = CostTracker()
    assert "agent" not in tracker.calls_by_source
    assert "agent:pre-p1-legacy" not in tracker.calls_by_source
