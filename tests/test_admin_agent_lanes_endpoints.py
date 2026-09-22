"""S6 — the four admin endpoints over the trace store.

F13: 404 on minimalist, parameterised so a fifth endpoint added without
the gate fails. No GET mutates state. F17: the hold control's copy and
its three behavioural claims tested together. W1's "within 2s" structural
half: no setInterval polling for lane data.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from arail import agent_context, agent_trace, config, scheduler
from arail.portal.app import app


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    agent_context._reset_for_tests()
    agent_trace._reset_for_tests()
    scheduler._reset_halt_for_tests()
    yield
    agent_context._reset_for_tests()
    agent_trace._reset_for_tests()
    scheduler._reset_halt_for_tests()


def _client() -> TestClient:
    return TestClient(app)


# GET entries carry no body; POST entries carry a minimal valid JSON body.
# F13 (REVIEW.md must-fix #9): /api/admin/agent-trace-stream was missing
# from this list -- the one new endpoint that serialises whole records,
# bodies included. Its gate gets the identical 404-on-minimalist check;
# "reachable on maximus" is verified separately below because it is an
# SSE stream, not a request TestClient.get() can safely consume to EOF
# (the generator never terminates on its own).
_ENDPOINTS = [
    ("get", "/api/admin/agent-lanes", None),
    ("get", "/api/admin/agent-trace-stream", None),
    ("get", "/api/admin/agent-trace/deadbeefcafef00d", None),
    ("post", "/api/admin/agents/hold", {"hold": False}),
    ("post", "/api/admin/flight-recorder", {"enabled": False}),
]

# The 404-check is safe for the stream endpoint (the tier gate returns
# before the StreamingResponse is ever constructed); the "reachable"
# check is not (see above) and is handled by its own dedicated test.
_STREAM_PATHS = {"/api/admin/agent-trace-stream"}


@pytest.mark.parametrize("method,path,body", _ENDPOINTS)
def test_f13_404_on_minimalist(monkeypatch, method, path, body):
    monkeypatch.setenv("LAB_TIER", "minimalist")
    client = _client()
    resp = (client.get(path) if body is None
            else client.post(path, json=body))
    assert resp.status_code == 404


@pytest.mark.parametrize("method,path,body", _ENDPOINTS)
def test_reachable_on_maximus(monkeypatch, method, path, body):
    monkeypatch.setenv("LAB_TIER", "maximus")
    if path in _STREAM_PATHS:
        pytest.skip("SSE stream -- covered by test_agent_trace_stream_gated_and_reachable")
    client = _client()
    resp = (client.get(path) if body is None
            else client.post(path, json=body))
    assert resp.status_code in (200, 404)  # 404 only for the unknown trace_id


def test_agent_trace_stream_gated_and_reachable(monkeypatch):
    """The SSE endpoint's own reachability check, without consuming an
    infinite generator via TestClient -- TestClient/httpx's .stream()
    still waits for the ASGI app's response cycle to conclude on close,
    which never happens for an endpoint whose generator never terminates
    on its own (confirmed the hard way: an earlier version of this test
    hung the whole suite on exactly this). This drives the raw ASGI app
    directly instead, the same proven technique
    tests/test_world_recolor_qa.py::test_real_sse_route_streams_live
    already uses for the sibling /api/activity/stream endpoint (which has
    the identical shape and the identical characteristic: neither
    generator polls request.is_disconnected() -- both rely on the ASGI
    server cancelling the request task when the real transport closes,
    which is what asyncio.CancelledError from task.cancel() simulates
    below). Every read is wait_for-bounded so a real regression fails in
    seconds, not hangs.
    """
    monkeypatch.setenv("LAB_TIER", "maximus")
    import asyncio

    from arail import agent_trace
    from arail.portal import app as pa

    async def _run() -> tuple[int, dict[str, str], bytes, bool]:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/admin/agent-trace-stream",
            "raw_path": b"/api/admin/agent-trace-stream",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"testserver")],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
        from_app: asyncio.Queue = asyncio.Queue()

        async def receive():
            await asyncio.Event().wait()  # client never disconnects mid-test

        async def send(message):
            await from_app.put(message)

        subscribers_before = len(agent_trace._SUBSCRIBERS)
        app_task = asyncio.create_task(pa.app(scope, receive, send))
        try:
            start = await asyncio.wait_for(from_app.get(), timeout=10)
            assert start["type"] == "http.response.start"
            headers = {k.decode(): v.decode() for k, v in start["headers"]}

            # A live frame: record something and read the pushed body chunk.
            agent_trace.record(trace_id="a" * 16, agent_id="buddy", kind="agent")
            body = b""
            deadline = asyncio.get_event_loop().time() + 5
            while asyncio.get_event_loop().time() < deadline:
                msg = await asyncio.wait_for(from_app.get(), timeout=5)
                if msg["type"] == "http.response.body":
                    body += msg.get("body", b"")
                    if b"buddy" in body:
                        break
            return start["status"], headers, body, subscribers_before
        finally:
            # Simulate a real transport disconnect: cancel the task the way
            # uvicorn cancels a request when the socket closes, then prove
            # the subscriber was actually removed (D7-adjacent: this is the
            # cleanup path REVIEW.md's backpressure finding is about).
            app_task.cancel()
            try:
                await app_task
            except BaseException:  # noqa: BLE001 - teardown only
                pass

    status, headers, body, subscribers_before = asyncio.run(_run())
    assert status == 200
    assert "text/event-stream" in headers.get("content-type", "")
    assert b"data:" in body
    assert b"buddy" in body
    assert len(agent_trace._SUBSCRIBERS) == subscribers_before, (
        "the SSE subscriber was not removed from _SUBSCRIBERS after the "
        "request task was cancelled -- a real client disconnect would "
        "leak a subscriber forever on a lab that stays up for weeks"
    )


def test_agent_lanes_get_never_mutates_state(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    agent_trace.record(trace_id="a" * 16, agent_id="buddy", kind="agent")
    before = agent_trace.stats()
    _client().get("/api/admin/agent-lanes")
    assert agent_trace.stats() == before


def test_agent_trace_detail_get_never_mutates_state(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    agent_trace.record(trace_id="b" * 16, agent_id="buddy", kind="agent")
    before = agent_trace.stats()
    _client().get("/api/admin/agent-trace/" + "b" * 16)
    assert agent_trace.stats() == before


# ---------------------------------------------------------------------------
# GET /api/admin/agent-lanes
# ---------------------------------------------------------------------------

def test_agent_lanes_shape(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    r = _client().get("/api/admin/agent-lanes")
    data = r.json()
    assert data["schema"] == "arail.agent_lanes/v1"
    assert isinstance(data["lanes"], list)
    assert "hold" in data
    assert "recorder" in data
    assert "slot" in data


# ---------------------------------------------------------------------------
# B6 (REVIEW.md) — W1's seven mandatory per-call fields (agent id, model,
# backend, brain+effort, TTFT-or-n/a, tokens in/out, reason code) must all
# reach the actual rendered admin.html markup, not just the JSON.
# ---------------------------------------------------------------------------

def test_w1_seven_fields_all_render_in_admin_lane_table():
    import pathlib
    from arail.portal import app as app_mod

    admin_html = (
        pathlib.Path(app_mod.__file__).parent / "templates" / "admin.html"
    )
    src = admin_html.read_text()
    start = src.find("function renderAgentLanes")
    assert start != -1, "renderAgentLanes() not found in admin.html at all"
    end = src.find("\nfunction ", start + 1)
    section = src[start:end if end != -1 else start + 4000]

    # Header cells -- the human-visible column names.
    for header in ("Agent", "Model", "Backend", "Brain", "Effort", "TTFT",
                   "Tokens in", "Tokens out", "Deep reason"):
        assert header in section, f"{header!r} column header missing"

    # Row template -- the JSON fields lanes_snapshot() puts on each lane
    # (agent_trace.py) must actually be read here, not just declared in a
    # header with nothing underneath it.
    for field in ("lane.display", "lane.model", "lane.backend", "lane.brain",
                  "lane.effort", "lane.tokens_in", "lane.tokens_out"):
        assert field in section, f"{field!r} never read in renderAgentLanes()"


def test_lanes_snapshot_carries_all_seven_w1_fields():
    """The data-side half of B6 -- lanes_snapshot() must expose every
    field the admin markup now reads, or the previous test's field names
    would just be reading `undefined`."""
    agent_trace.record(trace_id="d" * 16, agent_id="buddy", kind="agent",
                       model="qwen2.5-7b", backend="ollama", brain="deep",
                       effort="high", tokens_in=42, tokens_out=7)
    snap = agent_trace.lanes_snapshot()
    lane = next(l for l in snap["lanes"] if l["id"] == "buddy")
    assert lane["model"] == "qwen2.5-7b"
    assert lane["backend"] == "ollama"
    assert lane["brain"] == "deep"
    assert lane["effort"] == "high"
    assert lane["tokens_in"] == 42
    assert lane["tokens_out"] == 7


# ---------------------------------------------------------------------------
# GET /api/admin/agent-trace/{trace_id}
# ---------------------------------------------------------------------------

def test_agent_trace_detail_returns_the_record(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    agent_trace.record(trace_id="c" * 16, agent_id="researcher", kind="agent",
                       model="m")
    r = _client().get("/api/admin/agent-trace/" + "c" * 16)
    assert r.status_code == 200
    assert r.json()["model"] == "m"


def test_agent_trace_detail_404_on_unknown_id(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    r = _client().get("/api/admin/agent-trace/" + "0" * 16)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/admin/agents/hold
# ---------------------------------------------------------------------------

def test_hold_endpoint_flips_the_flag(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    client = _client()
    r = client.post("/api/admin/agents/hold", json={"hold": True})
    assert r.status_code == 200
    assert r.json()["held"] is True
    assert scheduler.jobs_halted() is True

    r2 = client.post("/api/admin/agents/hold", json={"hold": False})
    assert r2.json()["held"] is False
    assert scheduler.jobs_halted() is False


def test_hold_endpoint_rejects_cross_site(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    client = _client()
    r = client.post("/api/admin/agents/hold", json={"hold": True},
                    headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/admin/flight-recorder
# ---------------------------------------------------------------------------

def test_flight_recorder_endpoint_flips_the_flag(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    client = _client()
    r = client.post("/api/admin/flight-recorder", json={"enabled": True})
    assert r.status_code == 200
    assert r.json()["enabled"] is True
    assert agent_trace.recorder_on() is True


def test_flight_recorder_endpoint_rejects_cross_site(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    client = _client()
    r = client.post("/api/admin/flight-recorder", json={"enabled": True},
                    headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# No GET mutates state (static check over the route decorators)
# ---------------------------------------------------------------------------

def test_no_new_get_route_mutates_static_check():
    """The two GET endpoints in this family must be pure reads -- checked
    by source inspection for any call that writes state."""
    import inspect
    from arail.portal import app as app_mod

    for fn_name in ("admin_agent_lanes", "admin_agent_trace_detail",
                    "admin_agent_trace_stream"):
        src = inspect.getsource(getattr(app_mod, fn_name))
        for forbidden in ("halt_all_jobs(", "resume_all_jobs(",
                          "set_recorder_enabled(", "purge_legacy_bodies(",
                          "dismiss_legacy_notice("):
            assert forbidden not in src, f"{fn_name} calls {forbidden}"


# ---------------------------------------------------------------------------
# F17 — copy + behaviour tested together (the literal control string)
# ---------------------------------------------------------------------------
#
# B4 (REVIEW.md): this used to compare behaviour against a hand-typed
# HOLD_ALL_AGENTS_COPY constant duplicated in this file -- admin.html's
# actual JS string could drift from that constant and the test would
# still pass, since it was only ever comparing itself to itself. Instead,
# extract the "held" branch of renderHoldControl()'s copy ternary straight
# out of the real template source and test THAT string's content.

def _admin_hold_control_held_copy() -> str:
    """Read admin.html's renderHoldControl() and return the literal text
    of the "held" branch of its ``copy`` ternary, with the one dynamic
    slot (``${hold.in_flight}``) left as a literal placeholder for the
    caller to substitute."""
    import pathlib
    import re
    from arail.portal import app as app_mod

    admin_html = (
        pathlib.Path(app_mod.__file__).parent / "templates" / "admin.html"
    )
    src = admin_html.read_text()
    match = re.search(
        r"const copy = hold\.held\s*\n(?P<held>.*?)\n\s*:\s*`",
        src, re.DOTALL,
    )
    assert match, (
        "renderHoldControl()'s `const copy = hold.held ? ... : ...` "
        "ternary was not found in admin.html at all -- this test would "
        "otherwise silently check nothing"
    )
    pieces = re.findall(r"`([^`]*)`", match.group("held"))
    assert pieces, "no backtick-delimited literal in the held branch"
    return "".join(pieces)


def test_f17_copy_matches_behaviour(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    client = _client()
    r = client.post("/api/admin/agents/hold", json={"hold": True})
    state = r.json()
    held_copy = _admin_hold_control_held_copy()
    # ${hold.in_flight} is the one dynamic slot in the held-branch template
    # literal -- substitute the real value the endpoint returned so
    # `rendered` matches what a browser would actually show.
    rendered = held_copy.replace("${hold.in_flight}", str(state["in_flight"]))

    # Claim 1: agents stop calling models.
    from arail.router.backends import BaseBackend, ModelResponse
    from arail.router.core import ModelRouter

    class _B(BaseBackend):
        def complete(self, *a, **kw):
            return ModelResponse(text="x", model="m", tokens_used=1,
                                 backend="fake", latency_ms=1.0)

        def stream_complete(self, *a, **kw):
            yield ModelResponse(text="x", model="m", tokens_used=1,
                                backend="fake", latency_ms=1.0)

        def health_check(self):
            return True

    router = ModelRouter.from_backend(_B(), "fake")
    with agent_context.agent_call("buddy"):
        with pytest.raises(agent_context.AgentHeldError):
            router.complete("hi")
    assert "agents stop calling models" in rendered

    # Claim 2: agents stop posting findings, suggestions and announcements
    # (operator decision (a), SPRINT.md 2026-09-20-buddy-front-and-center --
    # widened from the old "stop speaking" wording to name what actually
    # gets silenced).
    assert agent_context.speech_gate("buddy") is False
    assert "stop posting findings, suggestions and announcements" in rendered

    # Claim 3: operational/error lines and SRE crash alerts continue.
    assert agent_context.speech_gate("sre") is True
    assert "Operational/error lines and SRE crash alerts continue" in rendered
    import pathlib
    from arail.agents import _builtin_sre
    assert "jobs_halted" not in pathlib.Path(_builtin_sre.__file__).read_text()

    # Claim 4: This World only; survives a restart -- DATA_DIR-scoped,
    # persisted (already tested in test_halt_persistence.py / F14's tests).
    assert "This World only" in rendered
    assert "survives a restart" in rendered


def test_nav_halt_control_relabeled_to_hold_all_agents():
    """B3 (REVIEW.md): _nav.html's dashboard button is the SAME
    halt_all_jobs()/resume_all_jobs() mechanism as admin's "Hold all
    agents" toggle (both post to endpoints that call the scheduler
    functions directly) -- it must carry the same label and the same
    "what actually happens" copy, not the old "cancels agent work only"
    wording that predates speech_gate being wired everywhere."""
    import pathlib
    from arail.portal import app as app_mod

    nav_html = (
        pathlib.Path(app_mod.__file__).parent / "templates" / "_nav.html"
    )
    src = nav_html.read_text()
    assert "btn-halt" in src, "the halt control id changed or was removed"

    # Relabeled -- the stale "Halt jobs" label must be gone.
    assert "Halt jobs" not in src
    assert "Hold all agents" in src

    # The button's title and its confirm() dialog both carry the exact
    # operator-specified sentence (SPRINT.md 2026-09-20-buddy-front-and-center,
    # decision (a)) -- not a paraphrase.
    required = (
        "agents stop calling models and stop posting findings, "
        "suggestions and announcements. Operational/error lines and "
        "SRE crash alerts continue."
    )
    assert required in src


# ---------------------------------------------------------------------------
# W1's structural "no polling" guard
# ---------------------------------------------------------------------------

def test_admin_template_uses_sse_not_setinterval_for_lanes():
    import pathlib
    from arail.portal import app as app_mod
    admin_html = (
        pathlib.Path(app_mod.__file__).parent / "templates" / "admin.html"
    )
    if not admin_html.exists():
        pytest.skip("admin.html not present in this checkout")
    src = admin_html.read_text()
    assert "agent-trace-stream" in src or "EventSource" in src
    # No setInterval anywhere near the lanes rendering -- a poll interval
    # is the only way this path could exceed W1's 2s bound. D8 (REVIEW.md):
    # this used to be `if lanes_section_start != -1:` -- renaming the
    # element would have made the test pass while asserting nothing.
    lanes_section_start = src.find("agent-lanes")
    assert lanes_section_start != -1, (
        "'agent-lanes' not found in admin.html at all -- this assertion "
        "would otherwise silently check nothing"
    )
    window = src[max(0, lanes_section_start - 500):lanes_section_start + 3000]
    assert "setInterval" not in window
