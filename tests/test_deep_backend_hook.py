"""The backend hook: AeroLLMBackend tells the deep verdict how every deep call ended.

C4 (backend hook) of sprints/2026-10-29-arail-deep-honest/ARCHITECTURE.md.
Tests B1 to B5 are named for the architecture's list. A real AeroLLMBackend
runs over the fake runtime; nothing here needs Metal.
"""
from __future__ import annotations

import pytest

from arail import agent_context
from arail import deep_verdict as dv
from tests.fakes.fake_deep_runtime import BUG_FRAGMENT, fake_deep  # noqa: F401  (fixture)
from tests.test_deep_verdict import probe_result, read_store, store_path


def test_b1_a_failure_is_noted_once_and_reraised_unchanged(fake_deep):
    fd = fake_deep("fail2")
    be = fd.backend()
    assert be.complete("first").text
    assert dv.current().state == "untested", "one answer proves nothing"
    with pytest.raises(RuntimeError) as ei:
        be.complete("second")
    assert BUG_FRAGMENT in str(ei.value) and "has 1 elements" in str(ei.value)
    assert type(ei.value) is RuntimeError, "the same exception, not a wrapper"
    v = dv.current()
    assert v.state == "failing" and v.source == "call"
    assert v.failure["count"] == 1, "noted once"
    assert BUG_FRAGMENT in v.failure["error"]
    assert dv.counters() == {"ok": 1, "failed": 1}


def test_b2_a_success_clears_nothing(fake_deep):
    fd = fake_deep("pass")
    be = fd.backend()
    b = dv.identity()
    dv.record_probe(probe_result(b, "fail", passed=1, started_ts=dv._now(),
                                 first_error={"call": 2, "kind": "long", "error": "RuntimeError: rms_norm"}))
    assert dv.current().state == "failing"
    for _ in range(3):
        assert be.complete("hello").text
    assert dv.current().state == "failing"
    assert dv.counters()["ok"] == 3
    assert read_store()["binaries"][b.sha256]["failure"] is not None


def test_b3_a_raising_note_call_never_breaks_complete(fake_deep, monkeypatch):
    fd = fake_deep("pass")
    be = fd.backend()

    def boom(*a, **k):
        raise RuntimeError("the verdict module exploded")

    monkeypatch.setattr(dv, "note_call", boom)
    assert be.complete("hello").text, "a success still returns"
    fd.set_mode("fail2")
    be2 = fd.backend()
    be2.complete("one")
    with pytest.raises(RuntimeError) as ei:
        be2.complete("two")
    assert BUG_FRAGMENT in str(ei.value), "the runtime's error comes through, not the hook's"


def test_b4_a_runtime_load_failure_is_persisted(fake_deep):
    fd = fake_deep("loadfail")
    with pytest.raises(RuntimeError, match="could not start"):
        fd.backend()
    v = dv.current()
    assert v.state == "failing" and v.source == "load"
    assert read_store()["binaries"][fd.sha256]["failure"]["source"] == "load"


def test_b4_a_model_dir_failure_is_not_persisted(fake_deep, monkeypatch):
    fd = fake_deep("pass")
    monkeypatch.setenv("ARAIL_MODELS_DIR", "/nope")
    monkeypatch.setenv("AEROLLM_MODEL", "no-such-model")
    with pytest.raises(RuntimeError, match="QueueLLM model dir not found"):
        fd.backend()
    v = dv.current()
    assert v.state == "failing" and v.source == "load"
    assert "model dir not found: /nope/no-such-model" in v.detail, "the detail names the dir (F19)"
    assert not store_path().exists(), "a missing model dir says nothing about the binary"


def test_b4_a_missing_wheel_is_not_a_deep_failure(tmp_path, monkeypatch):
    import sys
    from arail.router.backends import AeroLLMBackend
    for name in [n for n in sys.modules if n.split(".")[0] in ("aerollm_api", "queuellm_api")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "path", [p for p in sys.path if "fakepkg" not in p])
    monkeypatch.setitem(sys.modules, "aerollm_api", None)     # `import aerollm_api` raises ImportError
    monkeypatch.setattr(AeroLLMBackend, "_shared", {})
    with pytest.raises(ImportError):
        AeroLLMBackend()
    assert dv.counters() == {"ok": 0, "failed": 0}
    assert not store_path().exists()


def test_b5_health_check_is_false_while_failing(fake_deep):
    fd = fake_deep("pass")
    be = fd.backend()
    b = dv.identity()
    assert be.health_check() is True
    dv.note_call(False, error="boom")
    assert be.health_check() is False, "resident weights do not make a failing runtime healthy"
    dv.record_probe(probe_result(b, "pass", passed=5, started_ts=dv._now() + 1))
    assert be.health_check() is True


def test_an_empty_answer_is_a_failed_call_but_is_returned_unchanged(fake_deep):
    fd = fake_deep("empty2")
    be = fd.backend()
    assert be.complete("one").text
    resp = be.complete("two")
    assert resp.text == "" and resp.backend == "aerollm"
    v = dv.current()
    assert v.state == "failing" and "no text" in v.failure["error"]


def test_the_identity_is_pinned_when_the_backend_is_built(fake_deep):
    fd = fake_deep("pass")
    assert dv.identity(import_if_needed=False) is None or not dv.identity(import_if_needed=False).pinned
    fd.backend()
    b = dv.identity(import_if_needed=False)
    assert b is not None and b.pinned is True and b.sha256 == fd.sha256


def test_consumers_are_named_from_the_calling_context(fake_deep):
    fd = fake_deep("fail2")
    be = fd.backend()
    be.complete("warm")
    with agent_context.agent_call("buddy"):
        with pytest.raises(RuntimeError):
            be.complete("x")
    assert dv.current().failure["consumer"] == "agent:buddy"
    # a later failure keeps the first consumer; it only bumps the count
    with agent_context.system_call("world-review"):
        with pytest.raises(RuntimeError):
            be.complete("y")
    f = dv.current().failure
    assert f["consumer"] == "agent:buddy" and f["count"] == 2


def test_system_and_unattributed_consumers(fake_deep):
    fd = fake_deep("fail2")
    be = fd.backend()
    be.complete("warm")
    with agent_context.system_call("world-review"):
        with pytest.raises(RuntimeError):
            be.complete("x")
    assert dv.current().failure["consumer"] == "system:world-review"

    import json
    from arail.deep_verdict import state_dir
    doc = json.loads((state_dir() / "verdict.json").read_text())
    doc["binaries"][fd.sha256]["failure"] = None
    (state_dir() / "verdict.json").write_text(json.dumps(doc))
    dv._reset_for_tests()
    with pytest.raises(RuntimeError):
        be.complete("z")
    assert dv.current().failure["consumer"] == "unattributed"
