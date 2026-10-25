"""QA adversarial probes for the UI-recolor middleware (sprint 2026-06-14).

Focus, in priority order:
  1. SSE / streaming is NOT broken by the body-buffering middleware
     (gate-before-drain; live incremental flush preserved; ndjson untouched).
  2. FileResponse / download integrity (not buffered into one blob, not
     corrupted, content-type preserved).
  3. Re-seal integrity: edited bundles still mount AND a tampered face still
     fails verify_seal (the re-seal did not disable the check).
  4. Content-Length correctness after rewrite (no truncation).

These ADD coverage; they do not weaken existing tests.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import pathlib

import pytest
from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse, Response

from arail import world_mount as wm
from arail.portal import app as portal_app
from arail.portal.app import inject_ui_theme

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "world-bundles"
MARK = 'id="ui-theme-vars"'


def _client():
    return TestClient(portal_app.app)


# ════════════ 1. SSE / STREAMING — THE KEY SCRUTINY ════════════

class _FakeRequest:
    pass


def _run(coro):
    # Own a fresh loop rather than asyncio.get_event_loop() — a prior suite's
    # asyncio.run() closes the default loop, and get_event_loop() would then
    # hand back a closed loop (RuntimeError under combined test ordering).
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_sse_response_is_gated_before_drain():
    """An SSE (text/event-stream) StreamingResponse must be returned BY THE SAME
    object — never drained/buffered. We prove the middleware returns the *exact*
    StreamingResponse instance it received, so the body_iterator is still live
    and flushes incrementally. If the middleware drained first, the returned
    object would be a plain buffered Response (BLOCKER)."""
    flushed = []

    async def gen():
        for i in range(3):
            flushed.append(i)
            yield f"data: {i}\n\n".encode()

    sse = StreamingResponse(gen(), media_type="text/event-stream")

    async def call_next(_req):
        return sse

    out = _run(inject_ui_theme(_FakeRequest(), call_next))
    # Same object → iterator untouched → live streaming preserved.
    assert out is sse, "SSE response was replaced/buffered by the middleware"
    # The generator must not have been consumed yet (nothing flushed at gate time).
    assert flushed == [], "middleware drained the SSE generator (live flush broken)"


def test_ndjson_stream_not_buffered():
    """application/x-ndjson StreamingResponse is likewise passed through untouched."""
    async def gen():
        yield b'{"a":1}\n'
        yield b'{"a":2}\n'

    sr = StreamingResponse(gen(), media_type="application/x-ndjson")

    async def call_next(_req):
        return sr

    out = _run(inject_ui_theme(_FakeRequest(), call_next))
    assert out is sr


def test_activity_emit_from_foreign_thread_wakes_idle_subscriber():
    """Regression guard for the missed-wakeup hang: asyncio.Queue.put_nowait
    from a foreign thread enqueues but does not wake an idle event loop, so a
    subscriber could sit on a delivered event indefinitely. ActivityLog.emit
    must hand the put to the subscriber's loop (call_soon_threadsafe)."""
    import asyncio
    import threading
    import time

    from arail.portal import app as pa

    result: dict[str, object] = {}

    async def _listen() -> None:
        gen = pa.activity_log.subscribe()
        try:
            # Idle await — nothing else runs on this loop to mask a missed wakeup.
            async def _first_probe():
                async for event in gen:
                    if event.get("source") == "qa_probe_thread":
                        return event
            result["event"] = await asyncio.wait_for(_first_probe(), timeout=10)
        finally:
            await gen.aclose()

    listener = threading.Thread(target=lambda: asyncio.run(_listen()), daemon=True)
    listener.start()
    # Emit from THIS thread (foreign to the listener's loop) until delivered.
    deadline = time.time() + 10
    while listener.is_alive() and time.time() < deadline:
        pa.activity_log.emit("qa_probe_thread", "wake")
        time.sleep(0.1)
    listener.join(timeout=5)

    event = result.get("event")
    assert isinstance(event, dict) and event.get("source") == "qa_probe_thread", (
        "idle subscriber never woke for a cross-thread emit"
    )


def test_real_sse_route_streams_live():
    """End-to-end: the real /api/activity/stream SSE route still works through the
    full middleware stack and is not collected into one buffer at the end.

    Drives the raw ASGI app directly: starlette's TestClient buffers whole
    responses, so it can never open an infinite SSE stream — this test used to
    hang the entire suite on that. Every read here is wait_for-bounded, so a
    streaming/delivery regression FAILS in seconds instead of hanging."""
    import asyncio
    import threading
    import time

    from arail.portal import app as pa

    async def _run() -> tuple[int, dict[str, str], bytes]:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/activity/stream",
            "raw_path": b"/api/activity/stream",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"testserver"),
                        (b"authorization", b"Bearer arl1_" + b"A" * 43)],   # the conftest bearer: raw ASGI bypasses TestClient
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
        from_app: asyncio.Queue = asyncio.Queue()

        async def receive():
            await asyncio.Event().wait()  # client never disconnects mid-test

        async def send(message):
            await from_app.put(message)

        app_task = asyncio.create_task(pa.app(scope, receive, send))
        stop = threading.Event()
        try:
            start = await asyncio.wait_for(from_app.get(), timeout=30)
            assert start["type"] == "http.response.start"
            headers = {k.decode(): v.decode() for k, v in start["headers"]}

            # Emit from a foreign thread (the real-world emitter path) until
            # the stream delivers; re-emit because one event can land before
            # the subscriber has attached.
            def _emit() -> None:
                while not stop.is_set():
                    try:
                        pa.activity_log.emit("qa_probe", "live")
                    except Exception:
                        pass
                    time.sleep(0.1)

            threading.Thread(target=_emit, daemon=True).start()

            deadline = time.time() + 10
            while time.time() < deadline:
                msg = await asyncio.wait_for(from_app.get(), timeout=10)
                if msg["type"] == "http.response.body" and b"qa_probe" in msg.get("body", b""):
                    return start["status"], headers, msg["body"]
            raise AssertionError("did not receive a live SSE event incrementally")
        finally:
            stop.set()
            app_task.cancel()
            try:
                await app_task
            except BaseException:  # noqa: BLE001 — teardown only
                pass

    status, headers, body = asyncio.run(_run())
    assert status == 200
    assert "text/event-stream" in headers.get("content-type", "")
    assert MARK not in headers.get("content-type", "")
    assert b"data:" in body


# ════════════ 2. FILE / DOWNLOAD INTEGRITY ════════════

def test_static_binary_not_corrupted():
    """Static assets are served by a separate StaticFiles mount; confirm bytes are
    byte-identical to source and not rewritten."""
    src = pathlib.Path(portal_app.__file__).parent / "static" / "style.css"
    r = _client().get("/static/style.css")
    assert r.status_code == 200
    assert r.content == src.read_bytes(), "static asset corrupted in transit"
    assert MARK not in r.text


def test_html_rewrite_content_length_consistent():
    """After injection, Content-Length (if present) must equal the actual body
    length — a stale length truncates the page. TestClient/httpx would itself
    error on a mismatch, but assert explicitly."""
    r = _client().get("/")
    cl = r.headers.get("content-length")
    assert MARK in r.text  # injection happened
    if cl is not None:
        assert int(cl) == len(r.content), "Content-Length != body length after rewrite"


# ════════════ 3. RE-SEAL INTEGRITY ════════════

EDITED = ["physics", "world-caps-both", "world-caps-stt"]


@pytest.mark.parametrize("bundle", EDITED)
def test_edited_bundle_reseal_matches(bundle):
    """Each re-sealed bundle's manifest.files['face.json'] == sha256(face.json)."""
    bdir = FIXTURES / bundle
    manifest = json.loads((bdir / "manifest.json").read_text())
    expected = manifest["files"]["face.json"]
    actual = hashlib.sha256((bdir / "face.json").read_bytes()).hexdigest()
    assert actual == expected, f"{bundle} face.json not re-sealed"


@pytest.mark.parametrize("bundle", EDITED)
def test_edited_bundle_verifies_clean(bundle):
    """verify_seal passes on each edited bundle (no SealMismatch)."""
    from arail.world_mount import load_bundle, verify_seal
    b = load_bundle(FIXTURES / bundle)
    res = verify_seal(b)
    assert res.ok, f"{bundle} failed seal: {res.user_message}"


@pytest.mark.parametrize("bundle", EDITED)
def test_tampered_face_still_fails(bundle, tmp_path):
    """The re-seal did NOT disable the check: corrupting face.json (without
    re-sealing) must make verify_seal FAIL. Proves integrity still enforced."""
    import shutil
    from arail.world_mount import load_bundle, verify_seal
    work = tmp_path / bundle
    shutil.copytree(FIXTURES / bundle, work)
    face = work / "face.json"
    data = json.loads(face.read_text())
    data["palette_hint"] = "TAMPERED-no-reseal"
    face.write_text(json.dumps(data))
    b = load_bundle(work)
    res = verify_seal(b)
    assert not res.ok, f"{bundle}: tampered face.json was NOT caught by verify_seal"
    assert "face.json" in res.user_message
