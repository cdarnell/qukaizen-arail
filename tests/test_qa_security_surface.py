"""Security surface (arail CLAUDE.md's 20% slice).

The lab runs on other people's machines and the portal has **no
authentication** — ``onboarding_gate`` blocks every surface only until a
passphrase exists, then every request passes. So the questions here are not
"is it authenticated" (it isn't, by design, and that is filed) but:

  * with the recorder off, can a body reach **any** HTTP response that can
    serialise a record — ``/api/agents/prompts``, the lanes snapshot, the
    trace drill-in, the SSE stream — or the ``/api/activity/*`` pair;
  * can a secret reach a trace's ``attributes``, its ``error_class``, or the
    ``module:lineno`` caller field;
  * do the three mutating endpoints refuse a cross-origin POST the same way
    the repo's other state-changing routes do;
  * is the purge atomic, and does it ever claim success it did not achieve;
  * does the LAN-bind banner have two distinct branches.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from arail import activity, agent_context, agent_trace, config, jsonl_purge
from arail.router.backends import BaseBackend, ModelResponse
from arail.router.core import ModelRouter

PLANTED = "sk-securitysurface000000000000"


class _FakeBackend(BaseBackend):
    def __init__(self, text: str = "response body text") -> None:
        self.text = text

    def complete(self, prompt, max_tokens=512, temperature=0.7, top_p=None,
                 *, system=None, messages=None):
        return ModelResponse(text=self.text, model="fake-qa", tokens_used=4,
                             backend="fake", latency_ms=1.0)

    def health_check(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def isolated_costs(monkeypatch, tmp_path):
    from arail.costs import cost_tracker
    monkeypatch.setattr(cost_tracker, "_data_path", tmp_path / "qa-costs.json")
    monkeypatch.setattr(cost_tracker, "calls_by_source", {})


@pytest.fixture
def maximus(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")


@pytest.fixture
def client():
    from arail.portal.app import app
    with TestClient(app) as c:
        yield c


def _one_captured_call(prompt_secret: str = PLANTED,
                       response_secret: str = PLANTED) -> str:
    """Drive one agent call with the recorder ON so a body exists, then turn
    the recorder OFF. Returns the trace id."""
    agent_trace.set_recorder_enabled(True)
    router = ModelRouter.from_backend(
        _FakeBackend(f"response with {response_secret}"), "fake")
    with agent_context.agent_call("researcher"):
        router.complete(f"prompt with {prompt_secret}")
    rec = agent_trace.ring(1)[0]
    assert isinstance(rec["bodies"], dict), "precondition: a body was captured"
    agent_trace.set_recorder_enabled(False)
    return rec["trace_id"]


# ---------------------------------------------------------------------------
# Recorder off must mean "stop showing", on every read path
# ---------------------------------------------------------------------------

def test_prompts_endpoint_serves_no_body_keys_once_the_recorder_is_off(
        client):
    _one_captured_call()
    payload = client.get("/api/agents/prompts").json()
    assert payload["traces"], "presence first: there is a trace to inspect"
    for entry in payload["traces"]:
        assert "prompt" not in entry, entry
        assert "response" not in entry, entry
    assert PLANTED not in json.dumps(payload)


def test_lanes_snapshot_serves_no_body_once_the_recorder_is_off(client,
                                                               maximus):
    _one_captured_call()
    payload = client.get("/api/admin/agent-lanes").json()
    lanes = [l for l in payload["lanes"] if l["calls"] > 0]
    assert lanes, "presence first: a lane has a call"
    assert lanes[0]["last"] is not None
    assert lanes[0]["last"]["bodies"] is None
    assert PLANTED not in json.dumps(payload)


def test_trace_drill_in_serves_no_body_once_the_recorder_is_off(client,
                                                               maximus):
    trace_id = _one_captured_call()
    resp = client.get(f"/api/admin/agent-trace/{trace_id}")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["trace_id"] == trace_id
    assert payload["bodies"] is None
    assert PLANTED not in json.dumps(payload)


def test_sse_stream_serves_no_body_once_the_recorder_is_off():
    """The read gate must sit inside ``subscribe()`` itself, not in the
    endpoint, or the stream is a bypass. Driven directly against the
    generator so no server needs to run."""
    import asyncio

    async def _drive() -> dict:
        gen = agent_trace.subscribe()
        task = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0)
        rec = dict(agent_trace.ring(1)[0])
        agent_trace._fanout(rec)
        frame = await asyncio.wait_for(task, timeout=5)
        await gen.aclose()
        return frame

    _one_captured_call()
    frame = asyncio.run(_drive())
    assert frame["trace_id"], "presence first: a frame arrived"
    assert frame["bodies"] is None, (
        "the SSE stream bypassed the recorder-off read gate")
    assert PLANTED not in json.dumps(frame, default=str)


def test_the_body_is_never_written_to_the_activity_log_by_the_researcher(
        monkeypatch, tmp_path):
    """W4's baseline behaviour: researcher.py used to write up to 3000 chars
    of prompt + 2000 of response into activity.jsonl on every call.

    Captured at the module-under-test's own ``activity_log`` binding rather
    than read back out of the 200-event global ring. The ring is shared with
    every background daemon a previous test's ``TestClient(app)`` startup
    left running (TEST_REPORT.md F11, pre-existing and present on main), so
    reading it makes this test a function of how many tests ran before it —
    which it was, and which is what made it fail only in whole-tree sweeps.
    """
    from arail.agents import researcher

    captured: list[tuple] = []
    monkeypatch.setattr(researcher.activity_log, "emit",
                        lambda *a, **kw: captured.append((a, kw)))

    router = ModelRouter.from_backend(_FakeBackend(f"reply {PLANTED}"), "fake")
    text = researcher._llm_complete(router, f"ask about {PLANTED}")
    assert text, "presence first: the call really happened"
    assert captured, "presence first: the researcher emitted its metadata line"

    dumped = json.dumps(captured, default=str)
    assert PLANTED not in dumped, dumped[:400]
    for args, kwargs in captured:
        data = kwargs.get("data") or (args[3] if len(args) > 3 else {}) or {}
        trace = (data or {}).get("prompt_trace") or {}
        assert "prompt" not in trace
        assert "response" not in trace
        assert trace, "the metadata prompt_trace itself must still be emitted"


def test_activity_recent_endpoint_carries_no_body_after_a_purge(client,
                                                                monkeypatch):
    """B2: the purge has to reach the in-memory ring that
    ``GET /api/activity/recent`` (every tier, no auth) serves.

    The ring is ``deque(maxlen=200)`` and is shared with every background
    daemon left running by an earlier test's ``TestClient(app)`` startup
    (TEST_REPORT.md F11). In a long single-process sweep those daemons emit
    enough to evict this test's own planted event between the emit and the
    read, which is exactly how this test failed in whole-tree runs and
    passed everywhere else. Widen the ring for the duration — ``emit`` and
    ``recent`` both look the attribute up per call, so replacing it is seen
    by the daemons too — and the test becomes a statement about purge
    behaviour instead of about ring capacity.
    """
    from collections import deque
    monkeypatch.setattr(activity.activity_log, "_buffer",
                        deque(activity.activity_log._buffer, maxlen=20000))
    activity.activity_log.emit(
        "researcher", "LLM call completed", "info",
        {"prompt_trace": {"prompt": f"leaked {PLANTED}",
                          "response": "also leaked"}})
    before = client.get("/api/activity/recent").json()
    assert PLANTED in json.dumps(before), (
        "precondition: the legacy body is readable from memory")

    activity.purge_legacy_bodies()

    after = client.get("/api/activity/recent").json()
    assert PLANTED not in json.dumps(after)
    assert json.dumps(after).count("prompt_trace") >= 1, (
        "the metadata line itself must survive the purge")


# ---------------------------------------------------------------------------
# No secret in metadata fields
# ---------------------------------------------------------------------------

def test_an_error_class_is_a_class_name_never_the_exception_message():
    """A backend's exception message routinely contains the request URL, and
    an auth failure's message can echo the credential. Only the class name
    may reach disk."""
    class _LeakyBackend(_FakeBackend):
        def complete(self, *a, **kw):
            raise RuntimeError(
                f"401 Unauthorized for https://api.example.com/v1"
                f"?api_key={PLANTED} (Authorization: Bearer {PLANTED})")

    router = ModelRouter.from_backend(_LeakyBackend(), "fake")
    with agent_context.agent_call("researcher"):
        with pytest.raises(RuntimeError):
            router.complete("trigger the auth error")

    rec = agent_trace.ring(1)[0]
    assert rec["outcome"] == "error"
    assert rec["error_class"] == "RuntimeError"
    on_disk = (config.DATA_DIR / "agent_traces.jsonl").read_text()
    assert "RuntimeError" in on_disk, "presence first"
    assert PLANTED not in on_disk
    assert "Authorization" not in on_disk


def test_the_subprocess_error_field_is_a_class_name_not_the_childs_message(
        monkeypatch):
    """S2: the out-of-process leg writes the *child's* error string (80 chars)
    into a field the schema documents as a class name — recorder-independent,
    straight to disk. A child whose exception text quotes an Authorization
    header therefore leaks it."""
    import subprocess as subprocess_mod

    from arail.skills.goal_parser import GoalParser

    class _Completed:
        returncode = 0
        stdout = json.dumps({
            "ok": False,
            "error": ("AuthError: 401 for https://api.example.com/v1 "
                      f"(Authorization: Bearer {PLANTED})"),
        })
        stderr = ""

    monkeypatch.setattr(subprocess_mod, "run", lambda *a, **kw: _Completed())
    parser = GoalParser.__new__(GoalParser)
    assert parser._llm_subprocess("parse this goal") is None

    on_disk = (config.DATA_DIR / "agent_traces.jsonl").read_text()
    assert on_disk.strip(), "presence first: a trace was written"
    assert PLANTED not in on_disk, (
        "the child's raw exception text reached agent_traces.jsonl with the "
        "recorder off — error_class must be a class name, not a message")
    assert "Authorization" not in on_disk


def test_the_call_site_field_is_a_module_and_line_only():
    """``call_site`` is built from a live frame. It must never carry argument
    values, and it must be exactly ``module:lineno``."""
    import re

    router = ModelRouter.from_backend(_FakeBackend(), "fake")
    router.complete(f"a prompt containing {PLANTED}")

    site = agent_trace.ring(1)[0]["call_site"]
    assert re.fullmatch(r"[\w.]+:\d+", site), site
    assert PLANTED not in site


def test_no_secret_reaches_the_metadata_fields_of_a_captured_trace():
    """Every non-body field of a record, with the recorder on and a secret in
    both halves of the call."""
    trace_id = _one_captured_call()
    rec = agent_trace.ring(1)[0]
    metadata = {k: v for k, v in rec.items() if k != "bodies"}
    assert metadata["trace_id"] == trace_id
    assert PLANTED not in json.dumps(metadata, default=str)


def test_an_agent_id_cannot_smuggle_markup_into_the_lanes_payload():
    """The id reaches a DOM attribute. The sanitiser is the defence; this
    asserts nothing hostile survives into the JSON the card renders."""
    router = ModelRouter.from_backend(_FakeBackend(), "fake")
    with agent_context.agent_call('buddy"><img src=x onerror=alert(1)>'):
        router.complete("xss attempt")

    payload = json.dumps(agent_trace.lanes_snapshot())
    assert "<img" not in payload
    assert "onerror" not in payload
    assert 'buddy">' not in payload


# ---------------------------------------------------------------------------
# CSRF on the three mutating endpoints
# ---------------------------------------------------------------------------

MUTATING = (
    ("/api/admin/agents/hold", {"hold": True}),
    ("/api/admin/flight-recorder", {"enabled": True}),
    ("/api/admin/legacy-bodies/purge", {}),
)


@pytest.mark.parametrize("path,body", MUTATING)
def test_cross_site_post_is_refused(client, maximus, path, body):
    resp = client.post(path, json=body,
                       headers={"Sec-Fetch-Site": "cross-site"})
    assert resp.status_code == 403, path
    assert resp.json()["error"] == "cross_site"


@pytest.mark.parametrize("path,body", MUTATING)
def test_sec_fetch_site_none_is_refused(client, maximus, path, body):
    """``Sec-Fetch-Site: none`` is a typed-in URL or a bookmarklet — the
    repo's existing middleware treats it as hostile for mutations."""
    resp = client.post(path, json=body, headers={"Sec-Fetch-Site": "none"})
    assert resp.status_code == 403, path


@pytest.mark.parametrize("path,body", MUTATING)
def test_mismatched_origin_is_refused(client, maximus, path, body):
    resp = client.post(path, json=body,
                       headers={"Origin": "http://evil.example.com"})
    assert resp.status_code == 403, path
    assert resp.json()["error"] == "cross_origin"


@pytest.mark.parametrize("path,body", MUTATING)
def test_origin_null_is_refused(client, maximus, path, body):
    resp = client.post(path, json=body, headers={"Origin": "null"})
    assert resp.status_code == 403, path


@pytest.mark.parametrize("path,body", MUTATING)
def test_same_origin_post_is_allowed(client, maximus, path, body):
    """The inverse: a refusal that refuses everything is not CSRF protection,
    it is a broken endpoint."""
    resp = client.post(path, json=body,
                       headers={"Sec-Fetch-Site": "same-origin",
                                "Origin": "http://testserver"})
    assert resp.status_code == 200, (path, resp.text[:200])


def test_an_untrusted_host_is_refused_before_the_tier_gate(client, maximus):
    """DNS rebinding: the Host allowlist is checked in middleware, so it wins
    over the route's own tier gate."""
    resp = client.get("/api/admin/agent-lanes",
                      headers={"Host": "attacker.example.com"})
    assert resp.status_code == 403
    assert resp.json()["error"] == "untrusted_host"


def test_no_new_get_endpoint_mutates_state(client, maximus):
    """A GET that changes something is a CSRF hole the middleware cannot
    see, because it only guards mutating methods."""
    before_recorder = agent_trace.recorder_on()
    before_hold = agent_context.hold_state()["held"]
    for path in ("/api/admin/agent-lanes",
                 "/api/admin/agent-trace/deadbeefdeadbeef",
                 "/api/admin/legacy-bodies"):
        client.get(path)
    assert agent_trace.recorder_on() is before_recorder
    assert agent_context.hold_state()["held"] is before_hold
    assert not (config.DATA_DIR / "legacy_bodies_notice.json").exists()


def test_a_purge_only_post_does_not_silently_turn_the_recorder_off(client,
                                                                  maximus):
    """R7: ``{"purge": true}`` with no ``enabled`` key sets ``enabled`` to
    ``bool(None)`` = False. The UI always sends both, so this is a curl-only
    footgun — but the operator's recorder state should not be a side effect
    of asking for a purge."""
    agent_trace.set_recorder_enabled(True)
    resp = client.post("/api/admin/flight-recorder", json={"purge": True})
    assert resp.status_code == 200
    if agent_trace.recorder_on() is False:
        pytest.xfail(
            "REVIEW.md R7 (filed, not fixed): POST /api/admin/flight-recorder "
            '{"purge": true} with no "enabled" key turns the recorder off and '
            "rewrites changed_at as a side effect (app.py's "
            "bool(body.get('enabled')))")
    assert agent_trace.recorder_on() is True


@pytest.mark.xfail(strict=True, reason=(
    "BACKLOG 'QA fix loop (TEST_REPORT.md, commit 9d0e083f)' -> QA F6: "
    "app.py:6326/:6344 do an unguarded `await request.json()`, so malformed "
    "JSON is a 500. Ruled DEBT at re-test (2026-09-22): curl-only, lands "
    "after the tier gate, discloses nothing and changes no state. "
    "strict=True so the fix turns this red instead of passing quietly."))
def test_malformed_json_on_a_mutating_admin_endpoint_is_not_a_500(client,
                                                                 maximus):
    """A hand-rolled curl or a broken JS build must get a 4xx, not an
    unhandled exception — this is the surface a friend-and-family lab is
    most likely to poke by accident."""
    from starlette.testclient import TestClient as _TC
    from arail.portal.app import app

    with _TC(app, raise_server_exceptions=False) as c:
        resp = c.post("/api/admin/agents/hold", content=b"{not json",
                      headers={"Content-Type": "application/json"})
    assert resp.status_code < 500, (
        f"malformed JSON produced {resp.status_code} — the handler does an "
        "unguarded await request.json()")


# ---------------------------------------------------------------------------
# The purge: atomicity and honesty
# ---------------------------------------------------------------------------

def _seed_legacy(root: Path, n: int = 5) -> Path:
    path = root / "activity.jsonl"
    lines = []
    for i in range(n):
        lines.append(json.dumps({
            "ts": f"2026-09-0{i % 9 + 1}T00:00:00Z", "source": "researcher",
            "level": "info", "message": f"call {i}",
            "data": {"prompt_trace": {"prompt": f"secret {PLANTED} {i}",
                                      "response": "reply",
                                      "latency_ms": 12, "model": "m"}},
        }))
    path.write_text("\n".join(lines) + "\n")
    return path


def test_purge_preserves_every_other_field_and_the_exact_line_count(tmp_path,
                                                                   monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(activity, "LOG_FILE", tmp_path / "activity.jsonl")
    path = _seed_legacy(tmp_path, 5)
    before_lines = path.read_text().splitlines()

    result = activity.purge_legacy_bodies()

    after_lines = path.read_text().splitlines()
    assert result["purged"] == 5
    assert len(after_lines) == len(before_lines) == 5
    for before, after in zip(before_lines, after_lines):
        b, a = json.loads(before), json.loads(after)
        assert a["ts"] == b["ts"] and a["message"] == b["message"]
        assert a["source"] == b["source"] and a["level"] == b["level"]
        trace = a["data"]["prompt_trace"]
        assert trace["latency_ms"] == 12 and trace["model"] == "m"
        assert "prompt" not in trace and "response" not in trace
        assert trace["body_purged"] is True
    assert PLANTED not in path.read_text()


def test_purge_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(activity, "LOG_FILE", tmp_path / "activity.jsonl")
    path = _seed_legacy(tmp_path, 3)
    assert activity.purge_legacy_bodies()["purged"] == 3
    first = path.read_text()
    assert activity.purge_legacy_bodies()["purged"] == 0
    assert path.read_text() == first


def test_purge_leaves_the_original_intact_when_the_replace_fails(tmp_path,
                                                                monkeypatch):
    """Atomicity: an exception between the temp write and ``os.replace`` must
    leave the original file exactly as it was, and must not leave a stray
    temp file lying next to it."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(activity, "LOG_FILE", tmp_path / "activity.jsonl")
    path = _seed_legacy(tmp_path, 4)
    original = path.read_bytes()

    def _fail_replace(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(jsonl_purge.os, "replace", _fail_replace)
    result = activity.purge_legacy_bodies()

    assert path.read_bytes() == original, (
        "the original log was damaged by a failed purge")
    strays = list(tmp_path.glob("*.purge_tmp"))
    assert strays == [], f"a stray temp file survived: {strays}"
    assert result["purged"] == 0, (
        "the purge reported success it did not achieve: it returned "
        f"{result} while every body is still on disk — an operator who "
        "pressed [Purge] to delete secrets is told they are gone")


def test_purge_does_not_clear_memory_when_the_disk_half_failed(tmp_path,
                                                              monkeypatch):
    """The inverse failure of the same window: if the disk rewrite did not
    land, clearing the in-memory copy makes the UI show a clean log while the
    bodies are still on disk — the operator cannot even see what leaked."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(activity, "LOG_FILE", tmp_path / "activity.jsonl")
    _seed_legacy(tmp_path, 2)
    activity.activity_log.emit(
        "researcher", "in memory too", "info",
        {"prompt_trace": {"prompt": f"mem {PLANTED}", "response": "r"}})

    monkeypatch.setattr(jsonl_purge.os, "replace",
                        lambda src, dst: (_ for _ in ()).throw(
                            OSError(30, "Read-only file system")))
    activity.purge_legacy_bodies()

    on_disk = (tmp_path / "activity.jsonl").read_text()
    in_memory = json.dumps(activity.activity_log.recent(50))
    assert PLANTED in on_disk, "precondition: the disk half failed"
    assert PLANTED in in_memory, (
        "memory was cleared while disk still holds the bodies — the purge "
        "hid the evidence instead of removing it")


def test_purge_survives_a_log_with_malformed_lines_byte_identically(tmp_path,
                                                                   monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(activity, "LOG_FILE", tmp_path / "activity.jsonl")
    path = tmp_path / "activity.jsonl"
    good = json.dumps({"source": "researcher",
                       "data": {"prompt_trace": {"prompt": PLANTED}}})
    path.write_text("garbage not json\n" + good + "\n\n{truncated\n")

    result = activity.purge_legacy_bodies()

    text = path.read_text()
    assert result["purged"] == 1
    assert text.splitlines()[0] == "garbage not json"
    assert "{truncated" in text
    assert PLANTED not in text
    assert len(text.splitlines()) == 4


def test_flight_recorder_purge_clears_disk_and_the_live_ring(tmp_path,
                                                            monkeypatch):
    trace_id = _one_captured_call()
    agent_trace.set_recorder_enabled(True)  # bodies visible again

    result = agent_trace.purge_flight_recorder_bodies()

    assert result["purged"] >= 1 and result["purged_memory"] >= 1
    assert agent_trace.find(trace_id)["bodies"] is None
    assert agent_trace.ring(1)[0]["bodies_purged"] is True
    assert PLANTED not in (config.DATA_DIR / "agent_traces.jsonl").read_text()


# ---------------------------------------------------------------------------
# The LAN-bind banner's two branches
# ---------------------------------------------------------------------------

def test_lan_bind_with_a_live_recorder_warns_in_both_places(monkeypatch,
                                                           client, maximus):
    monkeypatch.setenv("BIND_ADDR", "0.0.0.0")
    agent_trace.set_recorder_enabled(True)
    snap = client.get("/api/admin/agent-lanes").json()
    assert snap["recorder"]["lan_exposed"] is True
    assert snap["recorder"]["enabled"] is True

    page = client.get("/admin").text
    assert "agent-lan-warning" in page
    assert "non-loopback" in page


def test_loopback_bind_does_not_warn(monkeypatch, client, maximus):
    monkeypatch.setenv("BIND_ADDR", "127.0.0.1")
    agent_trace.set_recorder_enabled(True)
    snap = client.get("/api/admin/agent-lanes").json()
    assert snap["recorder"]["lan_exposed"] is False


def test_lan_bind_with_the_recorder_off_does_not_warn(monkeypatch, client,
                                                     maximus):
    """Both conditions are required — a banner that fires on bind alone would
    be noise on every deliberately-LAN-bound lab."""
    monkeypatch.setenv("BIND_ADDR", "0.0.0.0")
    assert agent_trace.recorder_on() is False
    snap = client.get("/api/admin/agent-lanes").json()
    assert snap["recorder"]["lan_exposed"] is True
    assert snap["recorder"]["enabled"] is False


@pytest.mark.parametrize("bind,loopback", [
    ("127.0.0.1", True),
    ("localhost", True),
    ("::1", True),
    ("0.0.0.0", False),
    ("192.168.1.50", False),
    ("", False),
    ("  0.0.0.0  ", False),
    ("LOCALHOST", True),
])
def test_bind_is_loopback_classification(monkeypatch, bind, loopback):
    monkeypatch.setenv("BIND_ADDR", bind)
    assert config.bind_is_loopback() is loopback, bind


def test_the_airgap_toggle_still_agrees_with_the_shared_loopback_helper(
        monkeypatch):
    """The sprint replaced the airgap toggle's own loopback check with the
    shared helper. One definition, not two — and the security gate it feeds
    must not have changed meaning."""
    from arail.portal import app as portal_app

    for bind, expected in (("127.0.0.1", True), ("0.0.0.0", False),
                           ("::1", True), ("10.0.0.5", False)):
        monkeypatch.setenv("BIND_ADDR", bind)
        assert portal_app._toggle_bind_is_loopback() is expected
        assert portal_app._toggle_bind_is_loopback() is config.bind_is_loopback()
