"""The backend hook: AeroLLMBackend tells the deep verdict how every deep call ended.

C4 (backend hook) of sprints/2026-10-29-arail-deep-honest/ARCHITECTURE.md.
Tests B1 to B5 are named for the architecture's list. A real AeroLLMBackend
runs over the fake runtime; nothing here needs Metal.
"""
from __future__ import annotations

import concurrent.futures
import threading
import time

import pytest

from arail import agent_context
from arail import deep_verdict as dv
from arail.router.backends import _DeepBackendClosed
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


# ── B6 to B8: what is a failure of the binary, and what is not (review B1) ───────────────────
#
# A recorded failure is sticky and checkout-wide, so the hook files runtime faults and only those.
# ARAIL's own teardown (Eject, a model switch, the interpreter exiting) says nothing about the
# binary; a native panic (a BaseException) says everything.

def test_b6_a_call_after_close_is_arails_own_teardown_not_a_binary_failure(fake_deep):
    fd = fake_deep("pass")
    be = fd.backend()
    assert be.complete("one").text
    be._close()                                  # what Eject and a deep-model switch call
    with pytest.raises(_DeepBackendClosed) as ei:
        be.complete("two")
    assert isinstance(ei.value, RuntimeError), "a caller that caught the old RuntimeError still does"
    assert "closed" in str(ei.value) and "Load it again" in str(ei.value)
    assert dv.current().state == "untested", "no false claim about the binary on any surface"
    assert dv.counters() == {"ok": 1, "failed": 0}
    assert not store_path().exists()
    assert len(fd.gen_calls()) == 1, "the closed call never reached the runtime"


def test_b6_a_call_queued_when_close_dropped_the_runtime_is_not_a_binary_failure(fake_deep):
    fd = fake_deep("pass")
    be = fd.backend()
    be._runtime = None                           # _close() dropped it on the worker ...
    be._closed = True                            # ... and a call had passed the closed check just before
    with pytest.raises(_DeepBackendClosed):
        be._generate_on_worker("wrapped", {})


def test_b6_the_closed_flag_decides_not_an_empty_runtime(fake_deep):
    """A Runtime that is absent while the backend is open is a fault of this deep path and is filed
    (a load failure is filed by note_load_failure, test_b4_a_runtime_load_failure_is_persisted)."""
    fd = fake_deep("pass")
    be = fd.backend()
    be._runtime = None
    assert be._closed is False
    with pytest.raises(RuntimeError, match="not running") as ei:
        be.complete("x")
    assert not isinstance(ei.value, _DeepBackendClosed)
    assert dv.current().state == "failing" and dv.counters() == {"ok": 0, "failed": 1}


def test_b6_an_error_from_submit_itself_is_not_filed(fake_deep):
    fd = fake_deep("pass")
    be = fd.backend()
    be._executor.shutdown(wait=True)             # concurrent.futures refuses new work, closed flag unset
    assert be._closed is False
    with pytest.raises(RuntimeError, match="cannot schedule new futures"):
        be.complete("x")
    assert dv.current().state == "untested" and dv.counters() == {"ok": 0, "failed": 0}


def test_b6_a_call_cancelled_by_close_is_not_a_binary_failure(fake_deep):
    """_close() waits 10 s for the Runtime to drop, then shuts the executor down with cancel_futures=True.
    A call queued behind a long generation gets CancelledError, which is an Exception on 3.11."""
    fd = fake_deep("slow:1.5")
    be = fd.backend()
    got: dict = {}

    def call(name):
        try:
            got[name] = be.complete(name)
        except BaseException as e:               # noqa: BLE001 - the test reads what came out
            got[name] = e

    a = threading.Thread(target=call, args=("a",))
    a.start()
    end = time.monotonic() + 10
    while len(fd.gen_calls()) < 1 and time.monotonic() < end:
        time.sleep(0.02)
    assert len(fd.gen_calls()) == 1, "call a is inside the runtime"
    b = threading.Thread(target=call, args=("b",))
    b.start()
    time.sleep(0.3)                              # b is queued behind a on the single worker
    be._executor.shutdown(wait=False, cancel_futures=True)       # the call _close() makes after its wait
    a.join(15)
    b.join(15)
    assert isinstance(got["b"], concurrent.futures.CancelledError), got["b"]
    assert got["a"].text, "the call that was running finished"
    assert dv.current().state == "untested"
    assert dv.counters() == {"ok": 1, "failed": 0}


def test_b7_a_baseexception_from_the_runtime_is_filed_and_reraised_as_the_same_object(fake_deep):
    """PyO3's PanicException derives from BaseException. `except Exception` missed it, so nothing was
    filed and every surface kept saying deep was fine."""
    fd = fake_deep("panic2")
    be = fd.backend()
    import aerollm_api
    assert be.complete("one").text
    with pytest.raises(aerollm_api.PanicException) as ei:
        be.complete("two")
    assert ei.value is aerollm_api.LAST_PANIC, "the same object, not a copy or a wrapper"
    assert not isinstance(ei.value, Exception)
    v = dv.current()
    assert v.state == "failing" and v.source == "call"
    assert "PanicException" in v.failure["error"] and v.failure["count"] == 1
    assert dv.counters() == {"ok": 1, "failed": 1}
    with pytest.raises(aerollm_api.PanicException):
        be.complete("three")
    assert dv.current().failure["count"] == 2


@pytest.mark.parametrize("make", [
    lambda: KeyboardInterrupt("ctrl-c"),
    lambda: SystemExit(3),
    lambda: GeneratorExit(),
    lambda: concurrent.futures.CancelledError(),
    lambda: _DeepBackendClosed("closed"),
], ids=["KeyboardInterrupt", "SystemExit", "GeneratorExit", "CancelledError", "DeepBackendClosed"])
def test_b8_an_interruption_or_arails_own_teardown_is_not_filed_and_is_reraised_unchanged(fake_deep, make):
    fd = fake_deep("pass")
    be = fd.backend()
    assert be.complete("one").text
    boom = make()

    def generate(*a, **k):
        raise boom

    be._runtime.generate = generate
    with pytest.raises(type(boom)) as ei:
        be.complete("two")
    assert ei.value is boom, "the same object that was raised"
    assert dv.current().state == "untested" and dv.counters() == {"ok": 1, "failed": 0}
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
