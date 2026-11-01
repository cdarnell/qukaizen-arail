"""AutoResearch end-to-end against a fake aeroLLM.

The contract under test (the bug this overhaul fixes):
  * AutoResearch's fast path binds to Tier 0 via the registry — no dead
    LM Studio :1234 default, zero "heuristic fallback" ConnectionError warns.
  * Deep steps bind to the aerollm (Tier 1) entry by default.
  * Every model call carries provider / model id / latency / token counts.
  * A dying deep engine produces a VISIBLE FallbackEvent and the run
    continues on Tier 0 — never a silent skip.
"""

from __future__ import annotations

import pytest

from arail.router.backends import ModelResponse


class _FakeBackend:
    """Records calls; returns canned text; optionally raises."""

    def __init__(self, name: str, text: str = "ok", raise_exc: bool = False):
        self.name = name
        self.text = text
        self.raise_exc = raise_exc
        self.calls: list[str] = []

    def complete(self, prompt, max_tokens=512, temperature=0.7,
                 top_p=None, *, system=None, messages=None):
        if self.raise_exc:
            raise ConnectionError(f"{self.name} down")
        self.calls.append(prompt)
        return ModelResponse(text=self.text, model=self.name,
                             tokens_used=7, backend=self.name,
                             latency_ms=1.0)

    def stream_complete(self, *a, **k):
        yield self.complete(*a, **k)

    def health_check(self):
        return not self.raise_exc


@pytest.fixture
def wired_lab(monkeypatch, tmp_path):
    """Registry seeded from the lab-default env, with fake tier-0 and
    fake aerollm backends wired in below the registry's binding layer."""
    from arail.agents import deep_policy
    from arail.registry import binding as reg_binding
    from arail.registry import core as reg_core
    from arail.router.core import ModelRouter

    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE",
                       str(tmp_path / "model_registry.json"))
    monkeypatch.setenv("MODEL_BACKEND", "ollama_native")
    monkeypatch.setenv("MODEL_NAME", "ai-engineer:latest")
    monkeypatch.delenv("MODEL_API_BASE", raising=False)
    monkeypatch.setenv("AEROLLM_MODEL", "gpt-oss-20b-MLX-4bit")
    monkeypatch.setenv("AEROLLM_RESEARCH", "true")
    monkeypatch.setenv("LAB_MODE", "airgapped")
    # Tier-1 `enabled` is a capability fact — tier + wheel importability —
    # not an AEROLLM_RESEARCH opt-in (sprints/2026-08-11-two-slot-chat-
    # models Part 2). Pin both sides so this fixture stays deterministic
    # regardless of whether the real aerollm wheel happens to be built on
    # the machine running the suite (same convention as
    # tests/registry/conftest.py's tmp_registry).
    monkeypatch.setenv("LAB_TIER", "maximus")
    import importlib.util as _importlib_util
    _orig_find_spec = _importlib_util.find_spec
    monkeypatch.setattr(
        _importlib_util, "find_spec",
        lambda name, *a, **k: object() if name == "aerollm_api" else _orig_find_spec(name, *a, **k),
    )

    fake_fast = _FakeBackend("fake-tier0", text="fast answer")
    fake_deep = _FakeBackend("fake-aerollm", text="deep answer")

    # Tier 0 constructs through _build_openai_compat; swap in the fake.
    monkeypatch.setattr(reg_binding, "_build_openai_compat",
                        lambda entry: fake_fast)
    # Tier 1 goes through deep_policy's shared router; swap the singleton.
    deep_router = ModelRouter.from_backend(fake_deep, "aerollm",
                                           billing_source="agent")
    monkeypatch.setattr(deep_policy, "get_deep_router", lambda: deep_router)
    monkeypatch.setattr(deep_policy, "prefer_deep",
                        lambda foreground=False: True)

    reg_core.reset_registry()
    reg = reg_core.get_registry()
    reg._ensure_loaded()
    yield reg, fake_fast, fake_deep
    reg_core.reset_registry()


def _activity():
    from arail.activity import activity_log
    return activity_log


def _fresh_events():
    """Clear the (ring) activity buffer and return a getter for new events.

    len()-based slicing is wrong once the deque hits maxlen — clearing is the
    only reliable way to observe exactly the events a test action produced.
    """
    log = _activity()
    log._buffer.clear()
    return lambda: list(log._buffer)


def test_fast_path_binds_tier0_no_heuristic_fallback(wired_lab):
    reg, fake_fast, _ = wired_lab
    from arail.agents import researcher as res_mod

    router = res_mod._get_router()
    assert router is not None
    assert router.entry_id == "tier0-local"
    assert router.provider == "local"

    get_events = _fresh_events()
    text = res_mod._llm_complete(router, "expand this query", max_tokens=64)
    assert text == "fast answer"
    assert fake_fast.calls == ["expand this query"]

    events = get_events()
    assert not any("heuristic fallback" in e["message"] for e in events)
    done = [e for e in events if "LLM call completed" in e["message"]]
    assert done, events
    trace = done[0]["data"]["prompt_trace"]
    assert trace["provider"] == "local"
    assert trace["model"] == "fake-tier0"
    assert trace["tokens_out"] == 7
    assert trace["latency_ms"] >= 0


def test_deep_path_binds_aerollm_by_default(wired_lab):
    reg, fake_fast, fake_deep = wired_lab
    from arail.agents import researcher as res_mod

    deep = res_mod._get_deep_router()
    assert deep is not None
    assert deep.entry_id == "tier1-aerollm"
    assert deep.provider == "aerollm"

    out = res_mod._deep_complete(deep, res_mod._get_router(),
                                 "reason hard about this", max_tokens=64)
    assert out == "deep answer"
    assert fake_deep.calls and not fake_fast.calls   # deep step stayed deep


def test_dead_deep_engine_visible_fallback_run_continues(wired_lab):
    reg, fake_fast, fake_deep = wired_lab
    from arail.agents import researcher as res_mod

    fake_deep.raise_exc = True
    deep = res_mod._get_deep_router()
    out = res_mod._deep_complete(deep, res_mod._get_router(),
                                 "reason hard", max_tokens=64)
    assert out == "fast answer"                      # continued on Tier 0
    assert fake_fast.calls

    # The failure flipped tier1 health and produced a visible event.
    assert reg.entries["tier1-aerollm"].health.status == "unhealthy"
    assert any(ev.profile == "reasoning" and ev.tab == "research"
               for ev in reg.recent_events)
    # And the NEXT resolve for reasoning falls back with a banner-worthy event.
    res = reg.resolve("reasoning", tab="research")
    assert res.entry.id == "tier0-local"
    assert res.fallback is not None


def test_every_call_lands_in_cost_tracker_with_attribution(wired_lab):
    reg, fake_fast, _ = wired_lab
    from arail.agents import researcher as res_mod
    from arail.costs import cost_tracker

    router = res_mod._get_router()
    record = None
    orig = cost_tracker.track

    def _spy(*a, **k):
        nonlocal record
        record = k
        return orig(*a, **k)

    cost_tracker.track = _spy
    try:
        res_mod._llm_complete(router, "dedup these results", max_tokens=32)
    finally:
        cost_tracker.track = orig
    assert record is not None
    assert record["provider"] == "local"
    assert record["entry_id"] == "tier0-local"
    assert record["tab"] == "research"
    assert record["tokens_out"] == 7


def test_http_research_start_smoke(wired_lab, monkeypatch):
    """The portal endpoint reaches the (registry-bound) researcher."""
    from fastapi.testclient import TestClient
    from arail.portal import app as app_mod

    started: list[dict] = []
    monkeypatch.setattr(
        app_mod, "goal_store", type("G", (), {
            "get_current": staticmethod(
                lambda: {"parsed": {"goal": "test goal"}, "progress": 0}),
        })())
    monkeypatch.setattr(
        app_mod.researcher, "start",
        lambda parsed, *, delay=None, resume_state=None: started.append(parsed))
    monkeypatch.setattr(app_mod, "jobs_halted", lambda: False)

    with TestClient(app_mod.app) as client:
        r = client.post("/api/research/start", json={"now": True})
        assert r.status_code == 200
        assert "error" not in r.json()
    assert started == [{"goal": "test goal"}]
