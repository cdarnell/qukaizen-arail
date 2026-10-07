"""Local inference must not report a cloud cost, and every cost carries a source.

Regression for the owner-reported ``cloud_cost_usd: 0.008267`` on a local
QueueLLM answer: that figure was a simulated "what a 70B cloud call would have
cost" number published under a field that says it is a cloud charge, read from
a process-global "last record" that could belong to another call.
"""
from __future__ import annotations

import asyncio
import json
import threading

import pytest

from arail import costs as costs_mod
from arail.costs import cost_source
from arail.router.backends import BACKEND_MAP, ModelResponse

# F11: every backend is pinned to a specific source. A new backend with no
# entry here must be classified deliberately (and defaults to "unpriced").
EXPECTED_SOURCE = {
    "mlx": "local",
    "cuda": "local",
    "cpu": "local",
    "airllm": "local",
    "aerollm": "local",
    "ollama_native": "local",
    "claude": "billed_estimate",
    "huggingface": "billed_estimate",
    "openrouter": "billed_estimate",
    "openai_compat": "unpriced",
}


@pytest.fixture
def tracker(monkeypatch, tmp_path):
    import arail.config as config_mod

    monkeypatch.setattr(config_mod, "DATA_DIR", tmp_path)
    costs_mod.CostTracker._instance = None
    fresh = costs_mod.CostTracker()
    monkeypatch.setattr(costs_mod, "cost_tracker", fresh)
    yield fresh
    costs_mod.CostTracker._instance = None


def _resp(backend: str, model: str = "m", tokens: int = 36) -> ModelResponse:
    return ModelResponse(text="hi", model=model, tokens_used=tokens,
                         backend=backend, latency_ms=1000.0)


# ── T-COST-CLASS ─────────────────────────────────────────────────────────
def test_every_backend_has_a_pinned_source():
    assert set(EXPECTED_SOURCE) == set(BACKEND_MAP), (
        "BACKEND_MAP changed: classify the new backend in "
        "costs.COST_SOURCE_BY_BACKEND and in this table"
    )
    for name, want in EXPECTED_SOURCE.items():
        assert cost_source(name) == want, name


@pytest.mark.parametrize("bad", [None, "", "no-such-backend", 7])
def test_unknown_backend_is_unpriced_never_local(bad):
    assert cost_source(bad) == "unpriced"


# ── track() ──────────────────────────────────────────────────────────────
def test_track_local_has_no_cloud_cost_but_keeps_labelled_equivalent(tracker):
    rec = tracker.track("aerollm", "Qwen3-30B-A3B", 6800, 36, 73000.0, "ui")
    assert rec.cloud_cost_usd is None
    assert rec.cloud_cost_source == "local"
    assert rec.cloud_equivalent_usd > 0            # simulated figure survives
    last = tracker.get_last_record()
    assert last["cloud_cost_usd"] is None
    assert last["cloud_equivalent_source"] == "simulated"
    assert last["energy_cost_usd"] == last["energy_usd"] > 0
    assert last["energy_source"] == "estimated"


def test_track_cloud_has_billed_estimate(tracker):
    rec = tracker.track("claude", "claude-x", 1000, 500, 800.0, "ui")
    assert rec.cloud_cost_usd and rec.cloud_cost_usd > 0
    assert rec.cloud_cost_source == "billed_estimate"


# ── _build_chat_result ───────────────────────────────────────────────────
def test_result_local_matching_record(tracker):
    from arail.portal import app as portal

    resp = _resp("aerollm", "Qwen3-30B-A3B")
    rec = tracker.track(resp.backend, resp.model, 6800, 36, 73000.0, "ui")
    out = portal._build_chat_result(resp, wants_deep=True, cost_record=rec)
    assert out["cloud_cost_usd"] is None
    assert out["cloud_cost_source"] == "local"
    assert out["cloud_equivalent_source"] == "simulated"
    assert out["cloud_equivalent_usd"] > 0
    assert out["energy_cost_usd"] is not None and out["energy_cost_usd"] > 0
    assert out["energy_source"] == "estimated"


def test_result_local_is_forced_null_even_with_a_poisoned_record(tracker):
    from arail.portal import app as portal

    poisoned = {"backend": "aerollm", "model": "m", "cloud_cost_usd": 0.008,
                "cloud_cost_source": "billed_estimate"}
    out = portal._build_chat_result(_resp("aerollm"), wants_deep=True,
                                    cost_record=poisoned)
    assert out["cloud_cost_usd"] is None
    assert out["cloud_cost_source"] == "local"


def test_result_cloud_reports_billed_estimate(tracker):
    from arail.portal import app as portal

    resp = _resp("claude", "claude-x")
    rec = tracker.track(resp.backend, resp.model, 1000, 500, 800.0, "ui")
    out = portal._build_chat_result(resp, wants_deep=False, cost_record=rec)
    assert out["cloud_cost_usd"] and out["cloud_cost_usd"] > 0
    assert out["cloud_cost_source"] == "billed_estimate"
    assert out["cloud_equivalent_source"] == "simulated"


def test_result_router_branch_unattributed_when_last_record_is_other_call(tracker):
    from arail.portal import app as portal

    tracker.track("claude", "someone-elses-model", 1000, 500, 800.0, "agent")
    out = portal._build_chat_result(_resp("claude", "my-model"), wants_deep=False)
    assert out["cloud_cost_usd"] is None
    assert out["cloud_cost_source"] == "unattributed"
    assert out["cloud_equivalent_usd"] is None
    assert out["energy_cost_usd"] is None


def test_result_router_branch_attributes_when_last_record_matches(tracker):
    from arail.portal import app as portal

    tracker.track("claude", "my-model", 1000, 500, 800.0, "ui")
    out = portal._build_chat_result(_resp("claude", "my-model"), wants_deep=False)
    assert out["cloud_cost_source"] == "billed_estimate"
    assert out["cloud_cost_usd"] > 0


def test_result_unpriced_backend_reports_none_with_source(tracker):
    from arail.portal import app as portal

    resp = _resp("openai_compat", "lan-model")
    rec = tracker.track(resp.backend, resp.model, 100, 10, 50.0, "ui")
    out = portal._build_chat_result(resp, wants_deep=False, cost_record=rec)
    assert out["cloud_cost_usd"] is None
    assert out["cloud_cost_source"] == "unpriced"


def test_result_keeps_all_preexisting_keys(tracker):
    from arail.portal import app as portal

    out = portal._build_chat_result(_resp("aerollm"), wants_deep=True)
    for key in ("reply", "backend", "model", "latency_ms", "tokens_used",
                "tokens_per_sec", "cloud_cost_usd", "energy_cost_usd", "deep",
                "sources", "error", "model_provenance"):
        assert key in out


def test_non_null_usd_always_has_a_source(tracker):
    from arail.portal import app as portal

    for backend in EXPECTED_SOURCE:
        resp = _resp(backend)
        rec = tracker.track(resp.backend, resp.model, 100, 10, 50.0, "ui")
        out = portal._build_chat_result(resp, wants_deep=False, cost_record=rec)
        siblings = {"cloud_cost_usd": "cloud_cost_source",
                    "cloud_equivalent_usd": "cloud_equivalent_source",
                    "energy_cost_usd": "energy_source"}
        for usd, src in siblings.items():
            if out[usd] is not None:
                assert out[src], (backend, usd)
        if out["cloud_cost_source"] != "billed_estimate":
            assert out["cloud_cost_usd"] is None


# ── T-COST-RACE (F12) ────────────────────────────────────────────────────
def test_interleaved_turns_each_report_their_own_cost(tracker, monkeypatch):
    """Two turns whose track() calls interleave: each result must carry its
    own source and value, not whichever record landed last."""
    from arail.portal import app as portal

    local, cloud = _resp("aerollm", "q30"), _resp("claude", "claude-x")
    rec_local = tracker.track(local.backend, local.model, 6800, 36, 73000.0, "ui")
    rec_cloud = tracker.track(cloud.backend, cloud.model, 1000, 500, 800.0, "ui")
    # The global "last record" is now the cloud call. The old code would have
    # handed the cloud figure to the local turn.
    out_local = portal._build_chat_result(local, wants_deep=True, cost_record=rec_local)
    out_cloud = portal._build_chat_result(cloud, wants_deep=False, cost_record=rec_cloud)
    assert out_local["cloud_cost_usd"] is None
    assert out_local["cloud_cost_source"] == "local"
    assert out_cloud["cloud_cost_source"] == "billed_estimate"
    assert out_cloud["cloud_cost_usd"] == rec_cloud.cloud_cost_usd


# ── /api/system/costs ────────────────────────────────────────────────────
def test_system_costs_last_record_handles_none_and_legacy(tracker):
    from fastapi.testclient import TestClient
    from arail.portal import app as portal

    client = TestClient(portal.app, raise_server_exceptions=False)
    assert client.get("/api/system/costs").json()["last_record"] is None

    # A record persisted by an older build: fabricated figure, no source.
    tracker._history.append({"ts": 1.0, "backend": "aerollm", "model": "m",
                             "cloud_cost_usd": 0.008, "energy_usd": 0.001})
    body = client.get("/api/system/costs").json()["last_record"]
    assert body["cloud_cost_usd"] is None
    assert body["cloud_cost_source"] == "local"
    assert body["energy_cost_usd"] == 0.001


# ── T-STREAM: /api/chat/stream and /api/chat with a stubbed deep backend ─
class _StubDeepBackend:
    backend_name = "aerollm"
    model_name = "Qwen3-30B-A3B"

    def complete(self, prompt, max_tokens, temperature, top_p, **kw):
        return ModelResponse(text="ok", model=self.model_name, tokens_used=36,
                             backend="aerollm", latency_ms=73000.0)


def _stub_context(*_a, **_k):
    return {
        "wants_deep": True, "optional_backend_name": "aerollm",
        "deep_backend": _StubDeepBackend(), "runtime_backend": None,
        "router": None, "active_backend": _StubDeepBackend(),
        "prompt": "x" * 27200,           # ~6.8k estimated tokens, as measured
        "sources": [], "model_provenance": None, "frozen_system": None,
        "claude_messages": None, "error_result": None,
    }


@pytest.fixture
def stubbed_chat(tracker, monkeypatch):
    from arail.portal import app as portal

    monkeypatch.setattr(portal, "_prepare_chat_context", _stub_context)
    monkeypatch.setattr(portal, "_restore_chat_context", lambda ctx: None,
                        raising=False)
    monkeypatch.setattr(portal, "_record_aerollm_bench", lambda **k: None)
    return portal


def test_chat_stream_final_event_has_honest_cost(stubbed_chat):
    from fastapi.testclient import TestClient

    client = TestClient(stubbed_chat.app, raise_server_exceptions=False)
    r = client.post("/api/chat/stream", json={"message": "hello"})
    finals = [json.loads(x) for x in r.text.splitlines() if x.strip()]
    finals = [e for e in finals if e.get("type") == "final"]
    assert len(finals) == 1, r.text[:500]
    final = finals[0]
    assert final["cloud_cost_usd"] is None
    assert final["cloud_cost_source"] == "local"
    assert final["cloud_equivalent_source"] == "simulated"
    assert final["energy_cost_usd"] > 0
    assert final["backend"] == "aerollm"        # id is unchanged


def test_chat_non_stream_has_honest_cost(stubbed_chat):
    out = asyncio.run(stubbed_chat._run_chat_completion(
        message="hello", history=[], backend_override=None,
        model_override=None, temperature=0.7, top_p=None, max_tokens=64))
    assert out["cloud_cost_usd"] is None
    assert out["cloud_cost_source"] == "local"
