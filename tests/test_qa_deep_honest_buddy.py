"""QA, sprint 2026-10-29-arail-deep-honest: Buddy, the background agents, the Curator and the
failing-then-passing cycle.

Written by the QA pass (sprints/2026-10-29-arail-deep-honest/TEST_REPORT.md). A plain test that FAILS
is an open QA finding (QA-n). ``xfail(strict=True)`` marks debt that is already filed (TD-n) or that
predates the sprint: it does not turn the suite red today, and it fails the day the debt is paid, so
the marker is removed with the fix. A test that passes is a guard QA added.

Real threads, real subprocesses and the fake runtime (tests/fakes); no real wheel, model or lab.
"""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import os
import subprocess
import sys
import threading
import time

import pytest

from arail import agent_context, agent_trace
from arail import deep_verdict as dv
from arail import world_forge as wf
from arail import world_mount as wm
from arail.activity import activity_log
from arail.agents import deep_policy
from arail.portal import world_routes as wr
from arail.registry.store import TIER1_ID
from arail.router.backends import BaseBackend, ModelResponse
from arail.router.core import ModelRouter
from tests.fakes.fake_deep_runtime import fake_deep  # noqa: F401  (fixture)
from tests.test_curator_honest import (  # noqa: F401  (fixtures and the scripted judge)
    _R, Scripted, _digest, _grow, _review, client, world)
from tests.test_deep_verdict import probe_result


@pytest.fixture()
def gates(monkeypatch):
    """Maximus, kill switch off, every background gate open (the pattern of test_deep_policy_failing)."""
    monkeypatch.delenv("ARAIL_AGENT_DEEP", raising=False)
    monkeypatch.setenv("LAB_TIER", "maximus")
    from arail import runtime_profile, scheduler
    from arail.router import mlx_guard
    monkeypatch.setattr(scheduler, "jobs_halted", lambda: False)
    monkeypatch.setattr(scheduler, "current_window", lambda *a, **k: "heavy")
    monkeypatch.setattr(runtime_profile, "resolve", lambda *a, **k: ("overnight", "t"))
    monkeypatch.setattr(runtime_profile, "params", lambda p: {"background_aerollm": True})
    monkeypatch.setattr(mlx_guard, "metal_memory_pressure", lambda: 0.10)
    return monkeypatch


class _FastBackend(BaseBackend):
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, prompt, max_tokens=512, temperature=0.7, top_p=None, *, system=None, messages=None):
        self.calls += 1
        return ModelResponse(text="fast answer", model="stub-fast", tokens_used=2, backend="stub",
                             latency_ms=1.0, cost_usd=0.0)

    def health_check(self) -> bool:
        return True


@pytest.fixture()
def fast(monkeypatch):
    be = _FastBackend()
    router = ModelRouter.from_backend(be, "stub")
    router.be = be
    monkeypatch.setattr(deep_policy, "_get_fast_router", lambda: router)
    return router


@pytest.fixture()
def emitted(monkeypatch):
    """Every activity line written from here on: (source, message, level).

    Some suite tests reload modules, so the ``activity_log`` this file imported and the one
    ``arail.activity`` exposes now can be two objects. Writers fetch it either way, so both are spied."""
    got: list = []
    live = {id(o): o for o in (activity_log, sys.modules["arail.activity"].activity_log)}
    for log in live.values():
        orig = log.emit

        def spy(source, message, level="info", *a, _orig=orig, **k):
            got.append((source, message, level))
            return _orig(source, message, level, *a, **k)

        monkeypatch.setattr(log, "emit", spy)
    return got


def _model_traces() -> list[dict]:
    return [r for r in agent_trace.ring(500) if r.get("record_type") == "model_call"]


def _hosts():
    from arail.agents import _builtin_buddy as buddy
    from arail.agents import _builtin_consolidation_analyzer as cons
    from arail.agents import _builtin_debt_advisor as debt
    return [("buddy", buddy.ArailHost()), ("debt_advisor", debt.ArailHost()),
            ("consolidation_analyzer", cons.ArailHost())]


# ── Buddy and the background agents while deep is failing ───────────────────────────────────────────

def test_qa_sixty_agent_ticks_while_failing_make_no_deep_call_and_say_nothing_new(fake_deep, gates, fast, emitted):
    """Buddy (90 s watcher cadence), the debt advisor and the consolidation analyzer each call
    ``complete_preferring_deep(foreground=False)`` through their own ``ArailHost.llm_complete``.
    Once deep is recorded failing, N ticks must make 0 deep attempts, write 0 new activity lines
    (no alert spam) and stamp every fast call with ``deep_failing``."""
    fd = fake_deep("fail2")
    hosts = _hosts()
    with agent_context.agent_call("buddy", foreground=False):
        assert hosts[0][1].llm_complete("warm-up 1").startswith("fake deep answer 1")
        assert hosts[0][1].llm_complete("warm-up 2") == "fast answer"     # call 2 meets the bug
    assert dv.current().state == "failing"
    transitions = [m for s, m, lvl in emitted if s == "deep" and m.startswith("Deep model failed in ")]
    assert len(transitions) == 1, transitions
    assert "agents and the Curator use the fast model until ./arailctl deep probe passes 5 of 5" in transitions[0]

    gen_before, lines_before, traces_before = len(fd.gen_calls()), len(emitted), len(_model_traces())
    ticks = 60
    for i in range(ticks):
        agent, host = hosts[i % 3]
        with agent_context.agent_call(agent, foreground=False):
            assert host.llm_complete(f"tick {i}: say one thing about the goal") == "fast answer"

    assert len(fd.gen_calls()) == gen_before, "an agent called deep after it was recorded failing"
    assert emitted[lines_before:] == [], f"agents wrote activity lines on their ticks: {emitted[lines_before:][:3]}"
    new = _model_traces()[traces_before:]
    fast_traces = [t for t in new if t.get("backend") == "stub"]
    assert len(fast_traces) >= ticks and all(t["deep_reason_code"] == "deep_failing" for t in fast_traces)
    assert all("./arailctl deep probe" in (t["deep_reason_detail"] or "") for t in fast_traces)
    assert {t["agent_id"] for t in fast_traces} == {"buddy", "debt_advisor", "consolidation_analyzer"}
    assert deep_policy.counters()["fast_instead_of_deep"] == ticks


def test_qa_buddys_voice_falls_back_to_the_fact_when_both_models_fail(fake_deep, gates, monkeypatch):
    """With deep failing and the fast model down, Buddy's ``_voice`` says the plain fact: no deep call,
    no exception, no invented text."""
    fd = fake_deep("pass")
    dv.identity()
    dv.note_call(False, error="RuntimeError: rms_norm", consumer="agent:buddy")
    monkeypatch.setattr(deep_policy, "_get_fast_router", lambda: None)
    from arail.agents import _builtin_buddy as buddy
    monkeypatch.setattr(buddy, "_host", buddy.ArailHost())
    with agent_context.agent_call("buddy", foreground=False):
        assert buddy._voice("3 experiments finished overnight.") == "3 experiments finished overnight."
    assert fd.gen_calls() == []


# ── QA-6: a failure this lab could not save outlives a passing probe ────────────────────────────────
# When the verdict write fails (the lock busy past its 2 s cap, a full disk, a permission problem),
# _note_failure keeps the failure in this process's memory (_P.mem). record_probe never clears that
# copy and _assemble prefers it, so after `deep probe` passes 5/5 and prints "deep is cleared for
# agents and the Curator", this lab still says deep_failing, with a detail that says to run the probe
# that just passed. It ends only when the lab restarts.

def test_qa6_a_failure_this_lab_could_not_save_is_cleared_by_a_passing_probe(fake_deep):
    fd = fake_deep("pass")
    dv.identity()
    d = dv.state_dir()
    d.mkdir(parents=True)
    held = os.open(d / "verdict.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(held, fcntl.LOCK_EX)                     # another writer holds the lock past the 2 s cap
    try:
        dv.note_call(False, error="RuntimeError: rms_norm has 1 elements", consumer="agent:buddy")
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        os.close(held)
    v = dv.current()
    assert v.state == "failing" and v.store_error, "kept in memory, as designed (F12)"

    r = subprocess.run([sys.executable, "-m", "arail.deep_probe"], env=fd.env(), capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0 and "deep is cleared for agents and the Curator" in r.stdout, r.stdout + r.stderr
    v = dv.current()
    assert v.state != "failing", f"the probe passed 5/5 and said deep is cleared; this lab still says: {v.detail}"


# ── QA-7: after a probe clears deep, the tier-1 dot disagrees with explain() until Check ─────────────
# complete_preferring_deep still calls registry.report_failure(TIER1_ID, exc), which stores
# "unhealthy: RuntimeError: ..." in the entry. The read-time overlay hides it while the verdict fails.
# Once a probe clears the verdict, _refresh_cleared_overlay re-probes only a stored health that starts
# with the overlay's own prefix, so this stored record stays: explain() and the research strip say
# deep is cleared while the status-bar dot, the banner and the registry's reasoning/build bindings say
# tier 1 is unhealthy, until someone presses Check. ARCHITECTURE C5 / H12 promise the dot turns green
# with no Check.

@pytest.fixture()
def lab(fake_deep, gates, fast, monkeypatch, tmp_path):
    from arail.registry import core as reg_core
    fd = fake_deep("fail2")
    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE", str(tmp_path / "model_registry.json"))
    monkeypatch.setenv("MODEL_BACKEND", "ollama_native")
    monkeypatch.setenv("MODEL_NAME", "ai-engineer:latest")
    monkeypatch.setenv("LAB_MODE", "airgapped")
    reg_core.reset_registry()
    reg = reg_core.get_registry()
    reg._ensure_loaded()
    tier1 = reg.entries[TIER1_ID]
    tier1.model_id = fd.model_name
    assert tier1.enabled
    yield type("Lab", (), {"fd": fd, "reg": reg, "tier1": tier1})
    reg_core.reset_registry()


def _tier1_public(reg) -> dict:
    return next(e for e in reg.to_state()["entries"] if e["id"] == TIER1_ID)


def test_qa7_after_a_probe_clears_deep_the_tier1_dot_agrees_with_explain(lab):
    with agent_context.agent_call("buddy", foreground=False):
        deep_policy.complete_preferring_deep("q1", foreground=False)
        deep_policy.complete_preferring_deep("q2", foreground=False)      # fails; the registry hears it
    assert deep_policy.explain(foreground=False)[1] == "deep_failing"
    assert _tier1_public(lab.reg)["health"]["status"] == "unhealthy"

    b = dv.identity()
    dv.record_probe(probe_result(b, "pass", passed=5, started_ts=dv._now() + 1))   # ./arailctl deep probe: 5/5
    assert deep_policy.explain(foreground=False)[:2] == (True, "ok")
    health = _tier1_public(lab.reg)["health"]
    assert health["status"] != "unhealthy", (
        "explain() and the probe say deep is cleared; the tier-1 dot still says unhealthy until Check: "
        + str(health.get("detail")))


def test_qa_guard_the_failing_side_of_the_cycle_agrees_everywhere_in_a_second_process(lab):
    """While failing, a fresh process (a World instance, a CLI) reads the same answer: explain,
    the research block and the overlay all say failing, with no deep call."""
    with agent_context.agent_call("buddy", foreground=False):
        deep_policy.complete_preferring_deep("q1", foreground=False)
        deep_policy.complete_preferring_deep("q2", foreground=False)
    n = len(lab.fd.gen_calls())
    code = ("import json\n"
            "from arail.agents import deep_policy\n"
            "from arail import deep_verdict as dv\n"
            "ok, c, d = deep_policy.explain(foreground=True)\n"
            "v = dv.current(import_if_needed=False)\n"
            "print(json.dumps({'code': c, 'state': v.state, 'health': dv.health_detail(v)}))\n")
    r = subprocess.run([sys.executable, "-c", code], env=lab.fd.env(LAB_TIER="maximus"), capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    got = __import__("json").loads(r.stdout.strip().splitlines()[-1])
    assert (got["code"], got["state"]) == ("deep_failing", "failing")
    assert got["health"].startswith(f"{dv.HEALTH_PREFIX} (sha256 {dv.identity().sha256[:12]})")
    assert len(lab.fd.gen_calls()) == n, "reading generated nothing"


# ── Eject while a Buddy call, a Chat call and a Curator call are in flight ──────────────────────────

def _eject_scene(fd, slow_s: float, queue_before_close: bool):
    """Start a Chat call (running), then a Buddy call and a Curator call; close the backend (Eject)."""
    backend = fd.backend()
    router = deep_policy.get_deep_router()
    assert router is not None and router._backend is backend
    counted = wr._CountedRouter(router)
    out: dict = {}

    def chat():
        try:
            out["chat"] = backend.complete("chat turn", max_tokens=8).text
        except BaseException as e:  # noqa: BLE001
            out["chat"] = e

    def buddy():
        with agent_context.agent_call("buddy", foreground=False):
            out["buddy"] = deep_policy.complete_preferring_deep("buddy note", foreground=False)

    def curator():
        try:
            out["curator"] = counted.complete("Judge it.", max_tokens=8).text
        except BaseException as e:  # noqa: BLE001
            out["curator"] = e

    threads = [threading.Thread(target=f, name=f"qa-{f.__name__}") for f in (chat, buddy, curator)]
    threads[0].start()
    time.sleep(0.25)                                   # the Chat call is running on the worker
    if queue_before_close:
        threads[1].start()
        threads[2].start()
        time.sleep(0.25)                               # both are queued behind it
    own_workers = set(getattr(backend._executor, "_threads", ()))   # this backend's runtime thread(s)
    assert own_workers, "the Chat call started the runtime's worker thread"
    t0 = time.monotonic()
    backend._close()                                   # what Eject and a deep-model switch call
    close_s = time.monotonic() - t0
    if not queue_before_close:
        threads[1].start()
        threads[2].start()
    for t in threads:
        t.join(timeout=60)
    out["_workers_alive"] = [t.name for t in own_workers if t.is_alive()]
    return backend, counted, out, threads, close_s


@pytest.mark.parametrize("queue_before_close", [False, True],
                         ids=["calls arrive after Eject", "calls queued behind a long call, cancelled by Eject"])
def test_qa_eject_mid_flight_files_nothing_ends_every_thread_and_tells_the_truth(fake_deep, gates, fast,
                                                                               queue_before_close):
    slow = 11.0 if queue_before_close else 0.6        # past _close()'s 10 s wait, so the queue is cancelled
    fd = fake_deep(f"slow:{slow}")
    backend, counted, out, threads, close_s = _eject_scene(fd, slow, queue_before_close)

    assert not any(t.is_alive() for t in threads), "a caller is still blocked after Eject (deadlock)"
    assert out["_workers_alive"] == [], "this backend's runtime worker thread outlived Eject"
    assert close_s < 15, f"Eject took {close_s:.1f} s"
    assert not (dv.state_dir() / "verdict.json").exists(), "nothing was written to the verdict store"
    v = dv.current(import_if_needed=False)
    assert v.state == "untested", f"ARAIL's own teardown was filed as a binary failure: {v.detail}"
    assert isinstance(out["chat"], str) and out["chat"].startswith("fake deep answer"), \
        "the call that was already running finishes"
    assert out["buddy"] == "fast answer", "Buddy falls back to the fast model"
    assert isinstance(out["curator"], wr.CLOSED_BY_ARAIL) and counted.all_closed, out["curator"]
    if not queue_before_close:
        assert "Run it again once the deep model is loaded" in str(out["curator"])
    message = wr._review_report("Math World", planned=6, judged=0, failed=1, flags=0, label="QueueLLM",
                                deep=True, error=counted.first_error, cancelled=False, skip=None,
                                all_closed=counted.all_closed)[2]
    assert "closed during the pass" in message and "deep probe" not in message, message


@pytest.mark.xfail(strict=True, reason="TD10 (re-review M3): a call closed by ARAIL is still stamped deep_failing "
                                       "and reported to the registry, while the verdict says untested")
def test_qa_td10_a_call_closed_by_eject_is_not_stamped_deep_failing(fake_deep, gates, fast):
    fd = fake_deep("slow:0.6")
    _backend, _counted, out, _threads, _s = _eject_scene(fd, 0.6, queue_before_close=False)
    assert out["buddy"] == "fast answer"
    stamped = [t for t in _model_traces() if t.get("backend") == "stub" and t.get("agent_id") == "buddy"]
    assert stamped and all(t["deep_reason_code"] != "deep_failing" for t in stamped)


# ── The Curator: failure shapes the sprint's tests did not cover ────────────────────────────────────

class _FailWhen(Scripted):
    """Scripted, plus a failure chosen by the prompt (``fail_if``) rather than the call number."""

    def __init__(self, fail_if, **kw) -> None:
        super().__init__(**kw)
        self.fail_if = fail_if

    def complete(self, prompt, max_tokens=0, temperature=0, **kw):
        if self.fail_if(prompt):
            self.calls += 1
            raise RuntimeError("backend `mlx-native` failed: [rms_norm] has 1 elements")
        return super().complete(prompt, max_tokens, temperature, **kw)


@pytest.mark.parametrize("fail_calls, judged", [((1,), 5), ((6,), 5), ((2, 4), 4)],
                         ids=["first call only", "last call only", "sometimes"])
def test_qa_a_review_that_loses_any_judge_call_is_partial(world, monkeypatch, client, fail_calls, judged):
    world.mount(6)
    monkeypatch.setattr(wr, "_review_router", lambda: Scripted(name="aerollm", fail_calls=fail_calls))
    st = _review(client)
    assert (st["outcome"], st["planned"], st["judged"], st["failed"]) == ("partial", 6, judged, len(fail_calls))
    assert "All clear" not in st["message"] and "run ./arailctl deep probe" in st["message"]
    assert wr._review_state["state"] == "partial"


def test_qa_a_grow_whose_last_call_fails_changes_no_file_of_the_world(world, monkeypatch, client):
    catalog = world.mount(6)
    before = _digest(catalog)
    router = _FailWhen(lambda p: "MISSING" in p, name="aerollm")        # the propose call, after six judgments
    monkeypatch.setattr(wr, "_curation_router", lambda brain: router)
    st = _grow(client)
    assert st["state"] == "not_applied" and st["calls"]["failed"] == 1, st
    assert st["calls"]["attempted"] == router.calls
    assert _digest(catalog) == before, "a not-applied pass left the World's files byte-identical"
    assert wm.verify_seal(wm.load_bundle(catalog)).ok
    assert wr._grow_state["state"] == "not_applied"


@pytest.mark.xfail(strict=True, reason="pre-existing (not this sprint's change): an asyncio-cancelled review task "
                                       "leaves _review_state at 'running', so every later review gets 409 review_busy")
def test_qa_preexisting_a_cancelled_review_task_does_not_leave_the_curator_running(world, monkeypatch):
    catalog = world.mount(6)
    release = threading.Event()

    class Blocking:
        backend_name = "aerollm"

        def complete(self, *a, **k):
            release.wait(10)
            return _R('{"correct": true, "category_ok": true, "better_category": "", "bad_edges": []}', "aerollm")

    monkeypatch.setattr(wr, "_review_router", lambda: Blocking())
    spec, terms = wr._load_terms(catalog)

    async def main():
        wr._review_state = {"state": "running", "world": spec.get("slug")}
        task = asyncio.create_task(wr._run_review(catalog, spec, terms))
        await asyncio.sleep(0.2)
        task.cancel()
        release.set()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(main())
    assert wr._review_state["state"] != "running"


@pytest.mark.xfail(strict=True, reason="pre-existing (not this sprint's change): a growth pass the operator "
                                       "cancels is still resealed, swapped and logged as an evolution")
def test_qa_preexisting_a_cancelled_grow_pass_does_not_reseal_the_world(world, monkeypatch, client):
    world.mount(6)
    resealed, swapped = [], []
    monkeypatch.setattr(wf, "reseal_bundle", lambda *a, **k: resealed.append(1))
    monkeypatch.setattr(wm, "swap", lambda *a, **k: swapped.append(1))
    router = Scripted(name="aerollm", cancel_after=2, cancel_event=wr._grow_cancel)
    monkeypatch.setattr(wr, "_curation_router", lambda brain: router)
    st = _grow(client)
    assert resealed == [] and swapped == [], f"cancelled, yet resealed: {st.get('state')} {st.get('message', '')[:80]}"


def test_qa_guard_no_curator_state_stays_running_after_any_failure_shape(world, monkeypatch, client):
    """Always failing, a panic (BaseException), a closed call and an empty answer: every pass ends in a
    terminal state, so the next pass can start (no stuck 409)."""
    world.mount(6)
    shapes = [Scripted(name="aerollm", fail_from=1), Scripted(name="aerollm", panic_from=1),
              Scripted(name="aerollm", closed_from=2), Scripted(name="aerollm", empty_from=1),
              Scripted(name="aerollm", cancelled_from=3)]
    for router in shapes:
        monkeypatch.setattr(wr, "_review_router", lambda r=router: r)
        monkeypatch.setattr(wr, "_curation_router", lambda brain, r=router: r)
        router.calls = 0
        st = _review(client)
        assert st["state"] in ("partial", "failed") and wr._review_state["state"] != "running", st
        router.calls = 0
        st = _grow(client)
        assert st["state"] == "not_applied" and wr._grow_state["state"] != "running", st
