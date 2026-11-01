"""The "deep model is loaded" lines never say "ready" while generation is failing.

C8 (boot warm and preload lines) of sprints/2026-10-29-arail-deep-honest/
ARCHITECTURE.md; test H7. Weights being resident says nothing about whether the
runtime can answer a second call, so the line reports what is known instead.
"""
from __future__ import annotations

import asyncio

from arail import deep_verdict as dv
from arail.portal import model_warmth as mw
from arail.registry.store import TIER1_ID
from tests.fakes.fake_deep_runtime import fake_deep  # noqa: F401  (fixture)
from tests.registry.test_deep_verdict_overlay import env, events, fail  # noqa: F401  (fixtures)
from tests.test_deep_verdict import FAIL_ERR, probe_result


def test_h7_the_loaded_line_for_each_state(env):
    level, text, gen = mw._deep_loaded_line("Deep model loaded at boot")
    assert (level, gen) == ("success", "untested")
    assert text == ("Deep model loaded at boot (weights resident). "
                    "Generation not tested on this build yet: ./arailctl deep probe.")
    dv.record_probe(probe_result(env.b, "pass", passed=5, started_ts=dv._now(), at="2026-10-29T15:00:00Z"))
    level, text, gen = mw._deep_loaded_line("Deep model loaded at boot")
    assert (level, gen) == ("success", "passing")
    assert "Last probe 5/5 at 2026-10-29 15:00 UTC" in text and "5/5 is the bar" in text
    dv.record_probe(probe_result(env.b, "fail", passed=1, started_ts=dv._now() + 1, first_error=FAIL_ERR))
    level, text, gen = mw._deep_loaded_line("Deep model loaded at boot")
    assert (level, gen) == ("warn", "failing")
    assert text.startswith("Deep model loaded at boot (weights resident), but generation is failing on this build: ")
    assert "last probe 1/5, first error at call 2" in text
    assert "Chat can still pick it; agents and the Curator will not use it." in text
    for t in (text,):
        assert "ready" not in t.lower().replace("not tested", "")


def test_h7_boot_warm_and_preload_emit_the_honest_line(env, monkeypatch, tmp_path):
    from arail.agents import deep_policy
    got = events(monkeypatch)
    monkeypatch.setattr(deep_policy, "boot_warm_gate", lambda: (True, "ok", ""))
    monkeypatch.setattr(mw, "_tier1_resident", lambda: False)
    monkeypatch.setattr(mw, "_enabled", lambda: True)
    monkeypatch.setattr(mw, "sentinel_path", lambda: tmp_path / "inflight")
    monkeypatch.setattr(deep_policy, "get_deep_router", lambda: object())
    fail(env)
    assert asyncio.run(mw.boot_warm_deep("fake-model")) == "ok"
    boot = [a for a in got if a[1].startswith("Deep model loaded at boot")]
    assert boot and boot[-1][2] == "warn" and "generation is failing" in boot[-1][1]
    assert boot[-1][3]["model_event"] == {"kind": "boot_warm_ok", "entry_id": TIER1_ID, "generation": "failing"}

    got.clear()
    monkeypatch.setattr(deep_policy, "prefer_deep", lambda foreground=False: True)
    monkeypatch.setattr(deep_policy, "background_safe", lambda: True)
    assert asyncio.run(mw._preload_once()) == "loaded"
    pre = [a for a in got if a[1].startswith("Deep model preloaded")]
    assert pre and pre[-1][2] == "warn" and "ready" not in pre[-1][1].lower()



def test_qa16_the_loaded_line_is_printable_whatever_the_stored_probe_and_failure_say(env):
    nasty = "\x1b[2K\x1b[1A\x9b2K\x07\x7f"

    def bad(text):
        return sorted({hex(ord(c)) for c in text if ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F})

    dv.record_probe(probe_result(env.b, "pass", passed=5, started_ts=dv._now(), at="2026-10-29T" + nasty + "15:00:00Z"))
    _level, text, gen = mw._deep_loaded_line("Deep model loaded at boot")
    assert gen == "passing" and not bad(text), (bad(text), text)
    dv.record_probe(probe_result(env.b, "fail", passed=1, started_ts=dv._now() + 1,
                                 first_error={"call": "2" + nasty, "kind": "long", "error": "boom" + nasty}))
    _level, text, gen = mw._deep_loaded_line("Deep model loaded at boot")
    assert gen == "failing" and not bad(text), (bad(text), text)
