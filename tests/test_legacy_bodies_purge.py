"""F12 — legacy bodies already on disk from before the flight recorder
existed: disclosed with a count, purged only when the operator initiates
it, [Keep] remembered so the notice does not return.
"""

from __future__ import annotations

import json

import pytest

from arail import activity, config


@pytest.fixture
def log_path(monkeypatch, tmp_path):
    path = tmp_path / "activity.jsonl"
    monkeypatch.setattr(activity, "LOG_FILE", path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    yield path


def _write_lines(path, events):
    with open(path, "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


_LEGACY_EVENT = {
    "ts": "2026-01-01T00:00:00Z", "source": "researcher", "level": "info",
    "message": "LLM call completed", "data": {"prompt_trace": {
        "prompt": "a legacy prompt", "response": "a legacy response",
        "max_tokens": 512, "latency_ms": 12.0,
    }},
}
_CLEAN_EVENT = {
    "ts": "2026-01-02T00:00:00Z", "source": "researcher", "level": "info",
    "message": "LLM call completed", "data": {"prompt_trace": {
        "max_tokens": 512, "latency_ms": 12.0,
    }},
}
_NON_TRACE_EVENT = {
    "ts": "2026-01-03T00:00:00Z", "source": "buddy", "level": "info",
    "message": "just chatting", "data": {},
}


def test_scan_finds_no_legacy_bodies_on_fresh_lab(log_path):
    result = activity.scan_for_legacy_bodies()
    assert result["count"] == 0
    assert result["dismissed"] is False


def test_scan_counts_legacy_bodies_by_source(log_path):
    _write_lines(log_path, [_LEGACY_EVENT, _LEGACY_EVENT, _CLEAN_EVENT, _NON_TRACE_EVENT])
    result = activity.scan_for_legacy_bodies()
    assert result["count"] == 2
    assert result["by_source"] == {"researcher": 2}


def test_scan_checks_rotated_file_too(log_path):
    rotated = log_path.with_suffix(".jsonl.1")
    _write_lines(rotated, [_LEGACY_EVENT])
    _write_lines(log_path, [_CLEAN_EVENT])
    result = activity.scan_for_legacy_bodies()
    assert result["count"] == 1


def test_scan_never_raises_on_malformed_lines(log_path):
    log_path.write_text("{not json\n" + json.dumps(_LEGACY_EVENT) + "\n")
    result = activity.scan_for_legacy_bodies()
    assert result["count"] == 1


# ---------------------------------------------------------------------------
# Purge: strips bodies, preserves every other field and line count, never
# automatic.
# ---------------------------------------------------------------------------

def test_purge_strips_bodies_and_stamps_body_purged(log_path):
    _write_lines(log_path, [_LEGACY_EVENT])
    result = activity.purge_legacy_bodies()
    assert result["purged"] == 1

    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    trace = event["data"]["prompt_trace"]
    assert "prompt" not in trace
    assert "response" not in trace
    assert trace["body_purged"] is True
    # Every other field preserved exactly.
    assert trace["max_tokens"] == 512
    assert trace["latency_ms"] == 12.0
    assert event["ts"] == _LEGACY_EVENT["ts"]
    assert event["message"] == _LEGACY_EVENT["message"]


def test_purge_preserves_line_count_exactly(log_path):
    _write_lines(log_path, [_LEGACY_EVENT, _CLEAN_EVENT, _NON_TRACE_EVENT])
    activity.purge_legacy_bodies()
    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 3


def test_purge_leaves_clean_events_untouched(log_path):
    _write_lines(log_path, [_CLEAN_EVENT])
    activity.purge_legacy_bodies()
    lines = log_path.read_text().strip().splitlines()
    event = json.loads(lines[0])
    assert "body_purged" not in event["data"]["prompt_trace"]


def test_purge_preserves_malformed_lines_byte_identical(log_path):
    log_path.write_text("{not json at all\n" + json.dumps(_LEGACY_EVENT) + "\n")
    activity.purge_legacy_bodies()
    lines = log_path.read_text().splitlines()
    assert lines[0] == "{not json at all"


def test_purge_after_scan_leaves_zero_legacy_bodies(log_path):
    _write_lines(log_path, [_LEGACY_EVENT, _LEGACY_EVENT])
    assert activity.scan_for_legacy_bodies()["count"] == 2
    activity.purge_legacy_bodies()
    assert activity.scan_for_legacy_bodies()["count"] == 0


def test_purge_is_never_called_automatically_by_scan(log_path, monkeypatch):
    """scan_for_legacy_bodies must have no side effect on disk."""
    _write_lines(log_path, [_LEGACY_EVENT])
    before = log_path.read_text()
    activity.scan_for_legacy_bodies()
    after = log_path.read_text()
    assert before == after


# ---------------------------------------------------------------------------
# [Keep] dismissal
# ---------------------------------------------------------------------------

def test_dismiss_notice_is_remembered(log_path, tmp_path):
    assert activity.legacy_notice_dismissed() is False
    activity.dismiss_legacy_notice()
    assert activity.legacy_notice_dismissed() is True


def test_scan_reflects_dismissed_state(log_path):
    _write_lines(log_path, [_LEGACY_EVENT])
    activity.dismiss_legacy_notice()
    result = activity.scan_for_legacy_bodies()
    assert result["dismissed"] is True
    assert result["count"] == 1  # dismissal hides the notice, not the count
