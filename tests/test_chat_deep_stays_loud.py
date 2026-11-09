"""Chat stays loud: a deep error comes back as "Inference failed: ...", with no fall-back.

Regression row of sprints/2026-10-29-arail-deep-honest (Chat was never silent,
and this sprint must not make it quiet). With the call-2 bug, the second Chat
turn on the deep backend fails; the reply says so, and the verdict records it.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from arail import deep_verdict as dv
import arail.portal.app as app_mod
from arail.portal.app import app
from tests.fakes.fake_deep_runtime import BUG_FRAGMENT, fake_deep  # noqa: F401  (fixture)

CSRF = {"sec-fetch-site": "same-origin"}


@pytest.fixture()
def chat(fake_deep, monkeypatch):
    fd = fake_deep("fail2", model="Qwen2.5-7B-Instruct-4bit")   # a catalog name: Chat checks the size
    monkeypatch.setenv("LAB_TIER", "maximus")
    monkeypatch.setenv("LAB_MODE", "airgapped")
    # the portal keeps one AeroLLMBackend per process; a test must not inherit the last test's
    monkeypatch.setattr(app_mod, "_OPTIONAL_CHAT_BACKEND_CACHE", {})
    return fd, TestClient(app)


def _post(client, text):
    r = client.post("/api/chat", json={"message": text, "backend": "aerollm"}, headers=CSRF)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_second_deep_chat_turn_fails_loudly_and_the_verdict_says_so(chat):
    fd, client = chat
    first = _post(client, "first question")
    assert first.get("error") is None, first
    assert first["reply"].startswith("fake deep answer 1")
    assert dv.current().state == "untested", "one answer proves nothing"

    second = _post(client, "second question")
    assert second["reply"].startswith("Inference failed:"), second
    assert BUG_FRAGMENT in second["reply"] and "has 1 elements" in second["reply"]
    assert second["error"] and second["backend"] == "aerollm"
    assert "fast answer" not in second["reply"], "no silent fall-back to the fast model"

    v = dv.current()
    assert v.state == "failing" and v.source == "call"
    assert v.failure["consumer"] == "unattributed", "Chat has no agent or system label"
    assert len(fd.gen_calls()) == 2


def test_a_third_turn_still_tries_deep_and_still_fails_loudly(chat):
    fd, client = chat
    _post(client, "one")
    _post(client, "two")
    third = _post(client, "three")
    assert third["reply"].startswith("Inference failed:")
    assert len(fd.gen_calls()) == 3, "Chat is the operator's own pick: it always makes the call"
    assert dv.current().failure["count"] == 2
