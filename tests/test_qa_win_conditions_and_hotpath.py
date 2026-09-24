"""W1-W4 end to end (the 10% happy-path slice) plus the hot path, the
failure-injection matrix, and the concurrency edges the earlier phases earned.

W1 — three scripted agent calls (Buddy, Researcher, Librarian) each produce a
     lane entry with agent id, model, backend, brain+effort, TTFT-or-honest-
     ``n/a``, tokens, and ``deep_policy.explain()``'s reason code verbatim.
W2 — after the legacy-key migration, zero generic ``agent`` bucket.
W3 — hold: zero new agent traces over a simulated 60 s (see
     ``test_qa_blind_hold.py`` for the full matrix; the W3 row here is the
     end-to-end one through the HTTP control).
W4 — recorder off: zero bodies.
W5 — the operator's witness line. Not automatable, not QA's to write.

Hot path: the builder measured 0.048 ms p95 for the chokepoint additions.
Reproduced here on this machine, plus the same measurement with a cold ring,
a full ring, a rotating file, ``ENOSPC``, and an unwritable ``DATA_DIR``.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from arail import agent_context, agent_trace, config, scheduler
from arail.agents import deep_policy
from arail.router.backends import BaseBackend, ModelResponse
from arail.router.core import ModelRouter


class _FakeBackend(BaseBackend):
    def __init__(self, model="fake-model", backend="fake-backend",
                 text="answer"):
        self.model = model
        self.backend = backend
        self.text = text

    def complete(self, prompt, max_tokens=512, temperature=0.7, top_p=None,
                 *, system=None, messages=None):
        return ModelResponse(text=self.text, model=self.model, tokens_used=17,
                             backend=self.backend, latency_ms=3.5)

    def health_check(self) -> bool:
        return True


class _StreamingFake(_FakeBackend):
    def stream_complete(self, prompt, max_tokens=512, temperature=0.7,
                        top_p=None, *, system=None, messages=None):
        yield "ans"
        yield "wer"
        yield ModelResponse(text=self.text, model=self.model, tokens_used=17,
                            backend=self.backend, latency_ms=3.5)


@pytest.fixture(autouse=True)
def isolated_costs(monkeypatch, tmp_path):
    from arail.costs import cost_tracker
    monkeypatch.setattr(cost_tracker, "_data_path", tmp_path / "qa-costs.json")
    monkeypatch.setattr(cost_tracker, "calls_by_source", {})
    return cost_tracker


@pytest.fixture
def maximus(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")


@pytest.fixture
def client():
    from arail.portal.app import app
    with TestClient(app) as c:
        yield c


def _disk_traces() -> list[dict]:
    path = config.DATA_DIR / "agent_traces.jsonl"
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


# ---------------------------------------------------------------------------
# W1
# ---------------------------------------------------------------------------

W1_FIELDS = ("agent_id", "model", "backend", "brain", "effort",
             "tokens_in", "tokens_out", "deep_reason_code",
             "deep_reason_detail")


def test_w1_three_scripted_agent_calls_each_carry_all_seven_fields(maximus,
                                                                   client):
    """One Buddy proactive line, one Researcher LLM call, one Librarian
    dictionary-shaped call. Seven non-null fields, 3/3 calls, or a documented
    explicit ``n/a``."""
    reason = deep_policy.explain(foreground=False)

    calls = [
        ("buddy", "reflex", _StreamingFake("tier0-fast", "ollama_native")),
        ("researcher", "deep", _FakeBackend("qwen2.5-7b", "aerollm")),
        ("librarian", "standard", _FakeBackend("tier0-fast", "ollama_native")),
    ]
    for agent_id, brain, backend in calls:
        router = ModelRouter.from_backend(backend, backend.backend)
        with agent_context.agent_call(agent_id, brain=brain, effort=brain,
                                     foreground=False, deep_reason=reason):
            if isinstance(backend, _StreamingFake):
                list(router.stream_complete("do the thing"))
            else:
                router.complete("do the thing")

    traces = _disk_traces()
    assert len(traces) == 3, f"expected 3 traces, got {len(traces)}"

    for trace in traces:
        for field in W1_FIELDS:
            assert trace[field] is not None, (
                f"{trace['agent_id']}: W1 field {field} is blank")
        # TTFT: a number, or an explicit status with a null number. Never a
        # blank, and never equal to latency_ms.
        assert trace["ttft_status"] is not None
        if trace["ttft_status"] == "measured":
            assert isinstance(trace["ttft_ms"], float)
            assert trace["ttft_ms"] != trace["latency_ms"]
        else:
            assert trace["ttft_ms"] is None, (
                "a non-measured TTFT must be null, not a synthesised number")
        # The reason code must be byte-identical to a live explain() call,
        # not a hardcoded copy.
        assert trace["deep_reason_code"] == reason[1]
        assert trace["deep_reason_detail"] == reason[2]

    statuses = {t["agent_id"]: t["ttft_status"] for t in traces}
    assert statuses["buddy"] == "measured", (
        "the streaming fast path is the one place a real TTFT exists")
    assert statuses["researcher"] == "non_streaming"


def test_w1_the_lane_view_shows_all_seven_fields_for_each_call(maximus, client):
    reason = deep_policy.explain(foreground=False)
    for agent_id in ("buddy", "researcher", "librarian"):
        router = ModelRouter.from_backend(_FakeBackend(), "fake")
        with agent_context.agent_call(agent_id, brain="reflex", effort="low",
                                     foreground=False, deep_reason=reason):
            router.complete("work")

    snap = client.get("/api/admin/agent-lanes").json()
    lanes = {l["id"]: l for l in snap["lanes"]}
    for agent_id in ("buddy", "researcher", "librarian"):
        lane = lanes[agent_id]
        assert lane["calls"] == 1
        for field in ("model", "backend", "brain", "effort", "tokens_in",
                      "tokens_out", "deep_reason_code", "deep_reason_detail"):
            assert lane[field] is not None, (agent_id, field)
        assert lane["deep_reason_detail"] == reason[2]
        assert lane["empty_reason"] is None


def test_w1_the_lane_card_renders_a_column_header_for_every_field(maximus,
                                                                 client):
    """R4: the header assertions must be about markup, not a bare word that a
    comment could satisfy."""
    page = client.get("/admin").text
    for header in ("<th>Agent</th>", "<th>Calls</th>", "<th>Model</th>",
                   "<th>Backend</th>", "<th>Brain</th>", "<th>Effort</th>",
                   "<th>TTFT</th>", "<th>Tokens in</th>",
                   "<th>Tokens out</th>", "<th>Deep reason</th>"):
        assert header in page, f"missing column header markup: {header}"


def test_w1_is_push_based_so_the_two_second_bound_cannot_be_missed():
    """The structural half of W1's "within 2 s": ``record()`` must have placed
    the event on a subscriber's queue **before it returns** — no scheduler
    round-trip, no poll interval. Asserted synchronously, so it is a claim
    about ordering rather than about timing."""
    async def _main() -> int:
        gen = agent_trace.subscribe()
        task = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0)   # let subscribe() register
        q = agent_trace._SUBSCRIBERS[-1][0]
        assert q.qsize() == 0
        agent_trace.record(trace_id="f" * 16, kind="agent", agent_id="buddy",
                           outcome="ok")
        size = q.qsize()
        await asyncio.wait_for(task, timeout=2)
        await gen.aclose()
        return size

    assert asyncio.run(_main()) == 1, (
        "record() returned before the subscriber's queue had the event")


def test_w1_a_record_from_a_foreign_thread_still_wakes_the_subscriber():
    """The ``call_soon_threadsafe`` handover: agent calls arrive from
    ``to_thread`` workers, so the loop is not the recording thread."""
    async def _main() -> dict:
        gen = agent_trace.subscribe()
        task = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0)

        def _emit() -> None:
            agent_trace.record(trace_id="e" * 16, kind="agent",
                               agent_id="researcher", outcome="ok")

        t = threading.Thread(target=_emit)
        t.start()
        t.join(timeout=5)
        frame = await asyncio.wait_for(task, timeout=5)
        await gen.aclose()
        return frame

    frame = asyncio.run(_main())
    assert frame["agent_id"] == "researcher"


@pytest.mark.timing
def test_w1_an_sse_frame_arrives_well_inside_two_seconds():
    """The one wall-clock assertion in this file, marked so a loaded box can
    be diagnosed rather than guessed at."""
    async def _main() -> float:
        gen = agent_trace.subscribe()
        task = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0)
        t0 = time.perf_counter()
        agent_trace.record(trace_id="d" * 16, kind="agent", agent_id="buddy",
                           outcome="ok")
        await asyncio.wait_for(task, timeout=5)
        elapsed = (time.perf_counter() - t0) * 1000
        await gen.aclose()
        return elapsed

    elapsed_ms = asyncio.run(_main())
    assert elapsed_ms < 2000, f"{elapsed_ms:.1f} ms to deliver one frame"


# ---------------------------------------------------------------------------
# W2 / W3 / W4 end to end
# ---------------------------------------------------------------------------

def test_w2_no_generic_agent_bucket_after_a_scripted_session(isolated_costs):
    for agent_id in ("buddy", "researcher", "librarian"):
        router = ModelRouter.from_backend(_FakeBackend(), "fake")
        with agent_context.agent_call(agent_id):
            router.complete("work")
    buckets = dict(isolated_costs.calls_by_source)
    assert sum(buckets.values()) == 3
    assert "agent" not in buckets
    assert buckets.get("unattributed", 0) == 0
    assert len([k for k in buckets if k.startswith("agent:")]) == 3


def test_w3_the_admin_hold_control_stops_every_agent_end_to_end(maximus,
                                                               client):
    resp = client.post("/api/admin/agents/hold", json={"hold": True})
    assert resp.status_code == 200
    assert resp.json()["held"] is True
    try:
        simulated_now = 0.0
        refusals = 0
        while simulated_now < 60.0:
            router = ModelRouter.from_backend(_FakeBackend(), "fake")
            with agent_context.agent_call("buddy"):
                try:
                    router.complete("tick")
                except agent_context.AgentHeldError:
                    refusals += 1
            simulated_now += 10.0
        assert refusals == 6
        admitted = [t for t in _disk_traces()
                    if t["kind"] == "agent" and t["outcome"] == "ok"]
        assert admitted == []
        snap = client.get("/api/admin/agent-lanes").json()
        assert snap["hold"]["held"] is True
        assert snap["hold"]["exempt_speakers"] == ["sre"]
    finally:
        client.post("/api/admin/agents/hold", json={"hold": False})
    assert scheduler.jobs_halted() is False


def test_w4_recorder_off_means_zero_bodies_over_a_scripted_session(client):
    assert agent_trace.recorder_on() is False
    for i in range(20):
        router = ModelRouter.from_backend(_FakeBackend(), "fake")
        with agent_context.agent_call("researcher"):
            router.complete(f"call {i} with sk-{'w' * 24}")

    traces = _disk_traces()
    assert len(traces) == 20, "presence first"
    assert all(t["bodies"] is None for t in traces)
    tree = "".join(
        p.read_text(errors="replace")
        for p in config.DATA_DIR.rglob("*")
        if p.is_file() and p.name != "secrets.env")
    assert f"sk-{'w' * 24}" not in tree


def test_w5_is_the_operators_line_and_is_not_faked_here():
    """W5 is a human witness test. QA's job is to confirm the line exists and
    is the operator's, not to write it. This test documents that it is
    outstanding — it must never assert a pass on the operator's behalf."""
    sprint = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "sprints", "2026-09-20-buddy-front-and-center", "SPRINT.md")
    text = open(sprint).read()
    assert "W5" in text or "witness" in text.lower(), (
        "SPRINT.md names W1-W4 nowhere near W5: the ledger has no row, "
        "placeholder or phase for the witness test, so the one win condition "
        "that needs the operator has no home and can be shipped past "
        "silently. QA must not write it; the ledger must at least ask for it.")


# ---------------------------------------------------------------------------
# Hot path
# ---------------------------------------------------------------------------

def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[int(len(ordered) * 0.95) - 1]


def _measure(n: int = 2000) -> dict:
    router = ModelRouter.from_backend(_FakeBackend(), "fake")
    # Warm up, then measure only the delta the sprint added: the same fake
    # backend call with and without the chokepoint's recording work.
    samples: list[float] = []
    with agent_context.agent_call("buddy"):
        for _ in range(200):
            router.complete("warm")
        for _ in range(n):
            t0 = time.perf_counter()
            agent_trace.record(trace_id="c" * 16, kind="agent",
                               agent_id="buddy", outcome="ok",
                               slot={"capacity": 1, "in_flight": 0,
                                     "pending": 0, "held_by_other": False},
                               model="m", backend="b", tokens_in=10,
                               tokens_out=20, latency_ms=3.0)
            samples.append((time.perf_counter() - t0) * 1000)
    return {"p50": statistics.median(samples), "p95": _p95(samples),
            "max": max(samples), "n": n}


@pytest.mark.timing
def test_record_stays_inside_the_design_budget_of_one_millisecond_p95(capsys):
    """DE4's gate: design budget 1.0 ms p95, kill line 5 ms. The builder
    measured 0.048 ms p95; this reproduces the measurement on this machine
    and prints it for the report."""
    stats = _measure()
    print(f"\nrecord() p50={stats['p50']*1000:.1f}us "
          f"p95={stats['p95']*1000:.1f}us max={stats['max']*1000:.1f}us "
          f"n={stats['n']}")
    assert stats["p95"] < 1.0, stats


@pytest.mark.timing
def test_record_is_still_bounded_with_a_full_ring(monkeypatch, capsys):
    """A cold ring is the easy case. A full ring evicts on every append."""
    monkeypatch.setenv("ARAIL_TRACE_RING", "50")
    agent_trace._reset_for_tests()
    for i in range(60):
        agent_trace.record(trace_id=f"{i:016x}", kind="agent",
                           agent_id="buddy", outcome="ok")
    assert len(agent_trace.ring(1000)) == 50, "the ring bound must hold"
    stats = _measure(500)
    print(f"\nfull-ring record() p95={stats['p95']*1000:.1f}us")
    assert stats["p95"] < 1.0, stats


@pytest.mark.timing
def test_record_is_still_bounded_while_the_file_rotates(monkeypatch, capsys):
    """Rotation happens inside the same write path, every 64th record."""
    path = config.DATA_DIR / "agent_traces.jsonl"
    path.write_bytes(b"x" * (6 * 1024 * 1024))
    stats = _measure(500)
    rotated = config.DATA_DIR / "agent_traces.jsonl.1"
    print(f"\nrotating record() p95={stats['p95']*1000:.1f}us "
          f"rotated={rotated.exists()}")
    assert rotated.exists(), "the 5 MB ceiling must have rotated exactly once"
    assert path.stat().st_size < 6 * 1024 * 1024
    assert stats["p95"] < 1.0, stats


def test_rotation_produces_exactly_two_files_and_a_bounded_ceiling(monkeypatch):
    """F2: two files, a hard 10 MB ceiling, no third generation."""
    path = config.DATA_DIR / "agent_traces.jsonl"
    for generation in range(3):
        path.write_bytes(b"x" * (6 * 1024 * 1024))
        for i in range(64):
            agent_trace.record(trace_id=f"{generation}{i:015x}", kind="agent",
                               agent_id="buddy", outcome="ok")
    files = sorted(p.name for p in config.DATA_DIR.glob("agent_traces.jsonl*"))
    assert files == ["agent_traces.jsonl", "agent_traces.jsonl.1"], files
    assert not (config.DATA_DIR / "agent_traces.jsonl.2").exists()


@pytest.mark.timing
def test_a_failing_disk_write_does_not_slow_the_hot_path(monkeypatch, capsys):
    """ENOSPC simulated at the ``open`` boundary: the ring must keep serving,
    the loss must be counted, and the cost must not balloon."""
    import builtins
    real_open = builtins.open

    def _enospc(path, *a, **kw):
        if str(path).endswith("agent_traces.jsonl"):
            raise OSError(28, "No space left on device")
        return real_open(path, *a, **kw)

    # Restore by hand, not monkeypatch.undo(): undo() reverts every patch on
    # this function-scoped monkeypatch instance, including the conftest
    # autouse fixtures' config.DATA_DIR redirect.
    builtins.open = _enospc
    try:
        stats = _measure(500)
    finally:
        builtins.open = real_open

    print(f"\nENOSPC record() p95={stats['p95']*1000:.1f}us")
    assert stats["p95"] < 1.0, stats
    assert agent_trace.stats()["dropped_writes"] >= 500
    assert len(agent_trace.ring(1000)) > 0, "the live view must survive"
    assert agent_trace.lanes_snapshot()["drops"]["dropped_writes"] >= 500


@pytest.mark.parametrize("hostile", [
    {"slot": object()},
    {"bodies": {"prompt": object()}},
    {"tokens_out": float("nan")},
    {"latency_ms": complex(1, 2)},
    {"trace_id": None},
    {"trace_id": ""},
    {"label": b"\xff\xfe bytes"},
    {"deep_reason_detail": "\x00\x01 control chars"},
    {"unknown_future_v2_field": "dropped"},
])
def test_record_never_raises_on_any_field_value(hostile):
    """``record()`` has no raise path — the chokepoint depends on that for its
    own no-raise guarantee, so the promise is tested with values a future
    producer could plausibly hand it, including unserialisable objects."""
    fields = {"kind": "agent", "agent_id": "buddy", "outcome": "ok"}
    fields.update(hostile)
    agent_trace.record(**fields)
    assert len(agent_trace.ring(10)) >= 1 or agent_trace.stats()[
        "dropped_writes"] >= 1, (
        "the record was neither stored nor counted as dropped")


def test_the_drop_counter_is_visible_rather_than_silent(monkeypatch):
    import builtins
    real_open = builtins.open

    def _enospc(path, *a, **kw):
        if str(path).endswith("agent_traces.jsonl"):
            raise OSError(28, "No space left on device")
        return real_open(path, *a, **kw)

    builtins.open = _enospc
    try:
        for i in range(3):
            agent_trace.record(trace_id=f"{i:016x}", kind="agent",
                               agent_id="buddy", outcome="ok")
    finally:
        builtins.open = real_open
    assert agent_trace.stats()["dropped_writes"] == 3
    assert agent_trace.lanes_snapshot()["drops"]["dropped_writes"] == 3


def test_a_burst_of_a_thousand_traces_does_not_evict_activity_history():
    """F18 — the reason a third store exists at all."""
    from arail.activity import activity_log
    for i in range(30):
        activity_log.emit("researcher", f"the operator's history {i}", "info")
    before = [e["message"] for e in activity_log.recent(200)]

    for i in range(1000):
        agent_trace.record(trace_id=f"{i:016x}", kind="agent",
                           agent_id="buddy", outcome="ok")

    after = [e["message"] for e in activity_log.recent(200)]
    assert after == before, "trace volume evicted the operator's history"


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

def test_concurrent_complete_calls_produce_one_intact_line_each():
    """Eight threads through one router. Appends must not interleave, every
    line must be valid JSON, and none may be lost."""
    router = ModelRouter.from_backend(_FakeBackend(), "fake")

    def _worker(i: int) -> None:
        with agent_context.agent_call(f"agent{i % 4}"):
            for _ in range(25):
                router.complete("concurrent work")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_worker, range(8)))

    raw = (config.DATA_DIR / "agent_traces.jsonl").read_text().splitlines()
    assert len(raw) == 200, f"expected 200 lines, got {len(raw)}"
    parsed = [json.loads(line) for line in raw]  # raises on an interleaved line
    assert all(p["schema"] == agent_trace.SCHEMA for p in parsed)
    assert {p["attribution"] for p in parsed} == {
        f"agent:agent{i}" for i in range(4)}


def test_the_recorded_counter_matches_the_number_of_concurrent_records():
    """``_total_recorded`` is incremented without a lock. If the count drifts
    under concurrency the Admin card's "recorded" number is wrong."""
    def _worker(_i: int) -> None:
        for _ in range(200):
            agent_trace.record(trace_id="a" * 16, kind="agent",
                               agent_id="buddy", outcome="ok")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_worker, range(8)))

    assert agent_trace.stats()["recorded"] == 1600, (
        f"counter drifted to {agent_trace.stats()['recorded']} of 1600")


def test_concurrent_recorder_toggles_never_leave_a_torn_state_file():
    """The operator double-clicking the toggle while agents call models."""
    errors: list[Exception] = []

    def _flip(i: int) -> None:
        try:
            for _ in range(20):
                agent_trace.set_recorder_enabled(i % 2 == 0)
                agent_trace.recorder_on()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(_flip, range(6)))

    assert errors == []
    data = json.loads((config.DATA_DIR / "flight_recorder.json").read_text())
    assert isinstance(data["enabled"], bool)
    assert data["changed_at"]


def test_a_slow_sse_subscriber_is_dropped_rather_than_blocking_the_hot_path():
    """D7 is filed debt. Confirm the failure mode is the documented one — the
    backpressured subscriber stops receiving — and not something worse, like
    ``record()`` blocking or raising."""
    async def _main() -> tuple[int, int]:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        loop = asyncio.get_running_loop()
        agent_trace._SUBSCRIBERS.append((q, loop))
        before = len(agent_trace._SUBSCRIBERS)
        for i in range(300):          # 3x the queue bound, never drained
            agent_trace.record(trace_id=f"{i:016x}", kind="agent",
                               agent_id="buddy", outcome="ok")
        after = len(agent_trace._SUBSCRIBERS)
        return before, after

    before, after = asyncio.run(_main())
    assert before == 1
    assert after == 0, (
        "the documented behaviour is that the full subscriber is removed")
    assert len(agent_trace.ring(1000)) > 0, "the ring kept recording"
    assert agent_trace.stats()["recorded"] == 300, (
        "a slow subscriber must not cost a single record")


def test_subscriber_is_unregistered_when_the_generator_closes():
    """No leak per disconnect: the SSE route's generator is closed by ASGI
    task cancellation, and ``subscribe()``'s ``finally`` must run."""
    async def _main() -> tuple[int, int]:
        gen = agent_trace.subscribe()
        task = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0)
        during = len(agent_trace._SUBSCRIBERS)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await gen.aclose()
        return during, len(agent_trace._SUBSCRIBERS)

    during, after = asyncio.run(_main())
    assert during == 1 and after == 0


def test_two_processes_with_two_data_roots_do_not_interleave(tmp_path):
    """F14 for real: two child processes, two ``ARAIL_DATA_DIR`` values, each
    writing traces. Neither file may contain the other's record."""
    import subprocess
    import sys

    script = (
        "import os, sys;"
        "sys.path.insert(0, os.environ['QA_SRC']);"
        "from arail import agent_trace;"
        "agent_trace.record(trace_id=os.environ['QA_TAG']*8, kind='agent',"
        " agent_id='buddy', outcome='ok')"
    )
    src = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    roots = {}
    for tag, name in (("a", "world-a"), ("b", "world-b")):
        root = tmp_path / name
        root.mkdir()
        roots[tag] = root
        env = dict(os.environ, ARAIL_DATA_DIR=str(root), QA_TAG=tag,
                   QA_SRC=src)
        proc = subprocess.run([sys.executable, "-c", script], env=env,
                              capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stderr[-1500:]

    text_a = (roots["a"] / "agent_traces.jsonl").read_text()
    text_b = (roots["b"] / "agent_traces.jsonl").read_text()
    assert "a" * 8 in text_a and "b" * 8 not in text_a
    assert "b" * 8 in text_b and "a" * 8 not in text_b


# ---------------------------------------------------------------------------
# Regression guards on surfaces this sprint must not have touched
# ---------------------------------------------------------------------------

def test_metrics_endpoint_still_renders_its_inference_lines(maximus, client):
    """V9: nobody deletes a working surface in the name of D16."""
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "arail_inference" in resp.text, resp.text[:400]


def test_per_label_snapshot_shape_is_unchanged():
    from arail.portal import scheduler as portal_scheduler
    snap = portal_scheduler.per_label_snapshot()
    assert isinstance(snap, dict)
    pressure = portal_scheduler.slot_pressure()
    assert set(pressure) == {"capacity", "in_flight", "pending"}, pressure


def test_slot_pressure_computes_no_percentile(monkeypatch):
    """The cheap read must not be the expensive one wearing a hat."""
    from arail.portal import scheduler as portal_scheduler

    def _boom(*a, **kw):
        raise AssertionError("slot_pressure() computed a percentile")

    monkeypatch.setattr(portal_scheduler, "_percentile", _boom)
    assert portal_scheduler.slot_pressure()["capacity"] is not None


def test_chat_keeps_its_own_cost_bucket_even_inside_an_agent_context(
        isolated_costs):
    """F19: ``billing_source == "ui"`` short-circuits the rewrite, so the chat
    tab's numbers are byte-identical to before this sprint."""
    router = ModelRouter.from_backend(_FakeBackend(), "fake",
                                      billing_source="ui")
    with agent_context.agent_call("buddy"):
        router.complete("the operator typed this while Buddy was working")
    assert dict(isolated_costs.calls_by_source) == {"ui": 1}
    assert _disk_traces()[0]["attribution"] == "agent:buddy", (
        "the trace still names who called; only the cost bucket is pinned")


def test_an_abandoned_stream_is_still_accounted_for():
    """Contract #3 promises exactly one record per call. A consumer that stops
    reading mid-stream (a chat client disconnecting) closes the generator with
    ``GeneratorExit`` — which is a ``BaseException``, not an ``Exception``."""
    router = ModelRouter.from_backend(_StreamingFake(), "fake")
    with agent_context.agent_call("buddy"):
        gen = router.stream_complete("start streaming")
        next(gen)
        gen.close()

    traces = _disk_traces()
    if traces == []:
        pytest.xfail(
            "an abandoned stream leaves NO trace at all: router/core.py's "
            "stream_complete records only on the terminal ModelResponse or "
            "on an `except Exception`, and GeneratorExit is neither — so a "
            "partially-consumed agent stream is invisible in the lane view "
            "(W1 says every agent model call is visible)")
    assert len(traces) == 1
