"""keep_alive must be a value Ollama actually accepts.

The resident-pin feature sent the STRING "-1". Ollama reads a string
keep_alive as a Go duration, and "-1" has no unit, so every native
/api/chat call answered:

    400 {"error":"time: missing unit in duration \"-1\""}

That took out the whole local lane: the Tier-0 model never answered, the
Researcher fell back to generic template hypotheses, its experiments
came back `cannot_run`, and it still logged "Research complete. Report
generated." A JSON *number* is seconds, and negative means "keep loaded
indefinitely" — which is what pinning meant all along.
"""
from __future__ import annotations

import pytest

from arail.router import backends


def test_pinned_keep_alive_is_a_number_not_a_bare_string(monkeypatch):
    monkeypatch.delenv("ARAIL_OLLAMA_KEEP_ALIVE", raising=False)
    monkeypatch.setenv("ARAIL_RESIDENT_PIN", "1")

    b = object.__new__(backends.OllamaNativeBackend)
    monkeypatch.setattr(type(b), "_is_registry_tier0_model",
                        lambda self: True, raising=False)

    value = b._keep_alive()
    assert value == -1
    assert not isinstance(value, str), (
        'keep_alive "-1" is not a Go duration — Ollama 400s on it'
    )


def test_non_tier0_model_keeps_the_two_hour_duration(monkeypatch):
    monkeypatch.delenv("ARAIL_OLLAMA_KEEP_ALIVE", raising=False)
    monkeypatch.setenv("ARAIL_RESIDENT_PIN", "1")
    b = object.__new__(backends.OllamaNativeBackend)
    monkeypatch.setattr(type(b), "_is_registry_tier0_model",
                        lambda self: False, raising=False)
    assert b._keep_alive() == "2h"


def test_pin_disabled_keeps_the_two_hour_duration(monkeypatch):
    monkeypatch.delenv("ARAIL_OLLAMA_KEEP_ALIVE", raising=False)
    monkeypatch.setenv("ARAIL_RESIDENT_PIN", "0")
    b = object.__new__(backends.OllamaNativeBackend)
    monkeypatch.setattr(type(b), "_is_registry_tier0_model",
                        lambda self: True, raising=False)
    assert b._keep_alive() == "2h"


@pytest.mark.parametrize("raw,expected", [
    ("-1", -1),        # the operator form of "forever" — must not stay a str
    ("0", 0),
    ("300", 300),
    ("2h", "2h"),      # real durations pass through untouched
    ("30s", "30s"),
    ("5m", "5m"),
])
def test_operator_override_is_normalized(monkeypatch, raw, expected):
    monkeypatch.setenv("ARAIL_OLLAMA_KEEP_ALIVE", raw)
    b = object.__new__(backends.OllamaNativeBackend)
    assert b._keep_alive() == expected


def test_empty_override_omits_keep_alive(monkeypatch):
    monkeypatch.setenv("ARAIL_OLLAMA_KEEP_ALIVE", "")
    b = object.__new__(backends.OllamaNativeBackend)
    assert b._keep_alive() is None


@pytest.mark.parametrize("value", ["-1", "-1 ", " -1"])
def test_no_configuration_can_produce_a_bare_negative_string(monkeypatch, value):
    """The exact wire value Ollama rejects must be unreachable."""
    monkeypatch.setenv("ARAIL_OLLAMA_KEEP_ALIVE", value)
    b = object.__new__(backends.OllamaNativeBackend)
    assert b._keep_alive() == -1
