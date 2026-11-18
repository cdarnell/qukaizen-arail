"""brain.stream_turn: the shared call site (sprint 2026-11-18-aiml-slm-engine, T-B1..T-B5)."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from arail import agent_trace
from arail import model_pins as mp
from arail.buddy_host import brain
from arail.portal import host_edge_routes
from arail.router.backends import BaseBackend, ModelResponse, OllamaNativeBackend
from arail.router.core import ModelRouter

FAMILY = "qkz-aiml-qwen3-1.7b"
TAG = f"{FAMILY}:1.0.0"
GOOD = "ab" * 32
OTHER = "d67e0abf" + "1" * 56


def _entry(model_id="llama-ai-eng:latest"):
    return SimpleNamespace(model_id=model_id, endpoint="http://127.0.0.1:11434/v1",
                           backend="ollama_native", provider_type="ollama")


class _Spy(BaseBackend):
    def __init__(self):
        self.kw = None

    def complete(self, *a, **kw):  # pragma: no cover
        raise NotImplementedError

    def stream_complete(self, prompt, max_tokens=512, temperature=0.7, top_p=None, **kw):
        self.kw = kw
        yield "x"
        yield ModelResponse(text="x", model="m", tokens_used=1, backend="ollama_native", latency_ms=1.0)

    def health_check(self):
        return True


def _run(router, **kw):
    return list(brain.stream_turn(router, system="S", prompt="P", max_tokens=400, schema=None, **kw))


def _extra(spy):
    return {k: v for k, v in spy.kw.items() if k not in ("system", "messages")}


def test_t_b1_thinking_capable_entry_gets_think_false(monkeypatch):
    monkeypatch.setattr(brain, "probe_thinking", lambda e: True)
    spy = _Spy()
    _run(ModelRouter.from_backend(spy, "ollama_native"), entry=_entry("qwen3:1.7b"))
    assert _extra(spy)["think"] is False
    assert _extra(spy)["system_as_message"] is True


@pytest.mark.real_resolver_net   # opts out of conftest's probe_thinking stub: this tests the real seam
def test_t_b1_probe_costs_one_show_per_ttl_per_model(monkeypatch):
    calls = []

    def post(url, json=None, timeout=None):
        calls.append((url, json["model"]))
        r = MagicMock()
        r.json.return_value = {"capabilities": ["completion", "thinking"]}
        return r
    monkeypatch.setattr("requests.post", post)
    brain._reset_for_tests()
    e = _entry("qwen3:1.7b")
    assert brain.probe_thinking(e) is True and brain.probe_thinking(e) is True
    assert calls == [("http://127.0.0.1:11434/api/show", "qwen3:1.7b")]
    assert brain.probe_thinking(_entry("llama-ai-eng:latest")) is True
    assert len(calls) == 2
    brain._reset_for_tests()


@pytest.mark.real_resolver_net
def test_probe_thinking_false_without_the_capability_or_when_unreachable(monkeypatch):
    def post(url, json=None, timeout=None):
        r = MagicMock()
        r.json.return_value = {"capabilities": ["completion"]}
        return r
    monkeypatch.setattr("requests.post", post)
    brain._reset_for_tests()
    assert brain.probe_thinking(_entry("llama-ai-eng:latest")) is False

    def boom(*a, **k):
        raise OSError("down")
    monkeypatch.setattr("requests.post", boom)
    brain._reset_for_tests()
    assert brain.probe_thinking(_entry("qwen3:1.7b")) is False
    brain._reset_for_tests()


def test_t_b2_llama_request_body_is_byte_identical_to_today():
    be = OllamaNativeBackend.__new__(OllamaNativeBackend)
    be._session = MagicMock()
    be.base_url = "http://127.0.0.1:11434/v1"
    be.model_name = "llama-ai-eng:latest"
    be.api_key = "x"
    be.backend_name = "ollama:native"
    be._num_ctx = None
    cm = MagicMock()
    cm.__enter__.return_value.iter_lines.return_value = [json.dumps({"message": {"content": "a"}, "done": True})]
    be._session.post.return_value = cm
    _run(ModelRouter.from_backend(be, "ollama_native"), entry=_entry())    # probe_thinking is False for llama
    body = be._session.post.call_args.kwargs["json"]
    assert "think" not in body
    assert set(body["options"]) == {"num_predict"}
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["stream"] is True and "format" not in body


def test_t_b4_edge_drain_never_passes_sampling(monkeypatch):
    monkeypatch.setattr(brain, "probe_thinking", lambda e: True)
    spy = _Spy()
    plan = host_edge_routes.ChatPlan(
        header=None, system="S", prompt="P", max_tokens=400, schema=None, stream=True,
        router=ModelRouter.from_backend(spy, "ollama_native"), entry=_entry("qwen3:1.7b"))
    loop = SimpleNamespace(call_soon_threadsafe=lambda fn, *a: fn(*a))
    q = SimpleNamespace(put_nowait=lambda item: None)
    host_edge_routes._drain(plan, loop, q)
    assert "sampling" not in spy.kw and spy.kw["think"] is False


def test_sampling_is_passed_only_when_given():
    spy = _Spy()
    _run(ModelRouter.from_backend(spy, "ollama_native"), entry=_entry(),
         sampling={"temperature": 0, "seed": 7})
    assert _extra(spy)["sampling"] == {"temperature": 0, "seed": 7}


def test_t_b5_trace_attrs_land_in_model_call_attrs(monkeypatch):
    monkeypatch.setattr(brain, "probe_thinking", lambda e: True)
    _run(ModelRouter.from_backend(_Spy(), "ollama_native"), entry=_entry("qwen3:1.7b"))
    rec = [r for r in agent_trace.ring(50) if r.get("record_type") == "model_call"][-1]
    assert rec["attrs"]["think"] is False
    assert rec["attrs"]["served"]["tag"] == "qwen3:1.7b"
    assert rec["attrs"]["served"]["pin_state"] is None


# ── T-B3: describe() re-verifies an opt-in family entry ────────────────────

@pytest.fixture
def family_brain(monkeypatch, tmp_path):
    doc = json.loads(mp.PINS_PATH.read_text())
    rec = doc["pins"][0]
    rec.update(status="pinned", ollama_tag=TAG, weights_sha256=GOOD)
    rec["serving"]["parameters"]["num_ctx"] = 8192
    rec["release"]["source_commit"] = "c" * 40
    f = tmp_path / "pins.json"
    f.write_text(json.dumps(doc))
    monkeypatch.setenv("ARAIL_MODEL_PINS_FILE", str(f))
    st = {"weights": GOOD, "manifest": "m1", "now": 1000.0}
    monkeypatch.setattr(mp, "_fetch_tags", lambda: {TAG: st["manifest"]})
    monkeypatch.setattr(mp, "_fetch_show", lambda t: {
        "modelfile": f"FROM /Users/x/blobs/sha256-{st['weights']}\n", "template": "t",
        "parameters": "num_ctx 8192"})
    monkeypatch.setattr(mp, "time", SimpleNamespace(monotonic=lambda: st["now"]))
    monkeypatch.setattr(brain, "resolve_entry", lambda: _entry(TAG))
    monkeypatch.setattr(brain, "probe_structured_output", lambda e: True)
    mp.clear_caches()
    return st


def test_t_b3_describe_follows_the_pin_and_recovers_after_the_ttl(family_brain):
    v = brain.describe()
    assert v.available and v.model_id == TAG and v.attribution is None
    family_brain.update(weights=OTHER, manifest="m2")           # re-tagged under us
    assert brain.describe().available                           # inside the 60 s cache
    family_brain["now"] += 61
    v = brain.describe()
    assert not v.available and v.reason == "brain_unavailable"
    family_brain.update(weights=GOOD, manifest="m1")
    family_brain["now"] += 61
    assert brain.describe().available


def test_t_b3_pending_record_means_no_brain(monkeypatch):
    monkeypatch.setattr(brain, "resolve_entry", lambda: _entry("qkz-aiml-qwen3-1.7b:control-merge"))
    monkeypatch.setattr(brain, "probe_structured_output", lambda e: True)
    mp.clear_caches()
    v = brain.describe()
    assert not v.available and v.reason == "brain_unavailable"


def test_served_attrs_carry_digests_for_the_family(family_brain):
    s = brain._served_attrs(_entry(TAG))
    assert s["pin_state"] == "match" and s["weights_sha256"] == GOOD and s["manifest_digest"] == "m1"
    assert "/Users/" not in json.dumps(s)
