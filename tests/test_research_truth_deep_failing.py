"""The research truth strip says deep is failing, and says why.

C5 (truth strip) of sprints/2026-10-29-arail-deep-honest/ARCHITECTURE.md.
T1: the wire shape of ``models.deep`` with its additive ``generation`` block.
T2: the page renders ``deep_failing`` with the danger class and escapes the
reason. SEC2 (strip and switcher): an error with markup renders as text.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from arail import deep_verdict as dv
from arail.portal.app import app
from arail.registry import core as reg_core
from tests.fakes.fake_deep_runtime import fake_deep  # noqa: F401  (fixture)
from tests.test_deep_verdict import FAIL_ERR, probe_result

PORTAL = Path(__file__).resolve().parents[1] / "src" / "arail" / "portal"

# AEROLLM_RESEARCH: the operator's toggle for deep research passes (enabled_by_user)
GENERATION_KEYS = {"state", "since", "source", "consumer", "lab", "failures", "last_probe", "sha256_12"}


@pytest.fixture()
def strip(tmp_path, monkeypatch, fake_deep):
    fd = fake_deep("pass")
    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE", str(tmp_path / "model_registry.json"))
    monkeypatch.setenv("MODEL_BACKEND", "ollama_native")
    monkeypatch.setenv("MODEL_NAME", "ai-engineer:latest")
    monkeypatch.setenv("LAB_MODE", "airgapped")
    monkeypatch.setenv("LAB_TIER", "maximus")
    monkeypatch.setenv("AEROLLM_RESEARCH", "true")
    monkeypatch.delenv("ARAIL_AGENT_DEEP", raising=False)
    from arail import runtime_profile, scheduler
    from arail.router import mlx_guard
    monkeypatch.setattr(scheduler, "jobs_halted", lambda: False)
    monkeypatch.setattr(scheduler, "current_window", lambda *a, **k: "heavy")
    monkeypatch.setattr(runtime_profile, "resolve", lambda *a, **k: ("overnight", "t"))
    monkeypatch.setattr(runtime_profile, "params", lambda p: {"background_aerollm": True})
    monkeypatch.setattr(mlx_guard, "metal_memory_pressure", lambda: 0.10)
    reg_core.reset_registry()
    b = dv.identity()
    yield type("S", (), {"fd": fd, "b": b, "client": TestClient(app)})
    reg_core.reset_registry()


def _deep(client):
    r = client.get("/api/research/status")
    assert r.status_code == 200
    return r.json()["models"]["deep"]


# ── T1: the wire shape ─────────────────────────────────────────────────────

def test_t1_failing_after_a_probe(strip):
    dv.record_probe(probe_result(strip.b, "fail", passed=1, started_ts=dv._now(), first_error=FAIL_ERR,
                                 at="2026-10-29T13:35:50Z"))
    d = _deep(strip.client)
    assert d["reason_code"] == "deep_failing" and d["eligible_now"] is False
    assert d["available"] is True and d["enabled_by_user"] is True
    assert d["reason"].startswith("deep generation is failing on this build (QueueLLM 0.0.0-fake")
    assert "last probe 1/5, first error at call 2" in d["reason"]
    g = d["generation"]
    assert set(g) == GENERATION_KEYS
    assert g["state"] == "failing" and g["source"] == "probe" and g["consumer"] == "deep-probe"
    assert g["lab"] == "root" and g["failures"] == 1 and g["sha256_12"] == strip.b.sha256[:12]
    assert g["last_probe"] == {"at": "2026-10-29T13:35:50Z", "passed": 1, "total": 5}
    assert g["since"]


def test_t1_failing_after_a_deep_call_in_a_world_instance(strip, monkeypatch):
    monkeypatch.setenv("ARAIL_INSTANCE", "ai")
    dv.note_call(False, error="RuntimeError: boom", consumer="agent:buddy")
    g = _deep(strip.client)["generation"]
    assert (g["state"], g["source"], g["consumer"], g["lab"]) == ("failing", "call", "agent:buddy", "ai")
    assert g["last_probe"] is None


def test_t1_untested_and_passing_keep_todays_codes(strip):
    d = _deep(strip.client)
    assert (d["reason_code"], d["eligible_now"]) == ("ok", True)
    assert d["generation"]["state"] == "untested" and d["generation"]["last_probe"] is None
    assert d["generation"]["failures"] is None
    dv.record_probe(probe_result(strip.b, "pass", passed=5, started_ts=dv._now(), at="2026-10-29T15:00:00Z"))
    d = _deep(strip.client)
    assert (d["reason_code"], d["eligible_now"]) == ("ok", True)
    assert d["generation"]["state"] == "passing"
    assert d["generation"]["last_probe"] == {"at": "2026-10-29T15:00:00Z", "passed": 5, "total": 5}


def test_t1_post_research_deep_returns_the_same_block(strip):
    dv.note_call(False, error="boom")
    r = strip.client.post("/api/research/deep", json={"enabled": True},
                          headers={"sec-fetch-site": "same-origin"})
    assert r.status_code == 200, r.text
    deep = r.json()["models"]["deep"]
    assert deep["reason_code"] == "deep_failing" and set(deep["generation"]) == GENERATION_KEYS


def test_t1_a_cleared_failure_returns_the_strip_to_ok(strip):
    dv.note_call(False, error="boom")
    assert _deep(strip.client)["reason_code"] == "deep_failing"
    dv.record_probe(probe_result(strip.b, "pass", passed=5, started_ts=dv._now() + 1))
    assert _deep(strip.client)["reason_code"] == "ok"


def test_t1_the_toggle_still_explains_a_deep_that_is_not_failing(strip, monkeypatch):
    monkeypatch.setenv("AEROLLM_RESEARCH", "false")
    d = _deep(strip.client)
    assert d["reason_code"] == "disabled" and d["enabled_by_user"] is False and d["eligible_now"] is False
    assert "flip the toggle" in d["reason"]
    assert d["generation"]["state"] == "untested"


def test_t1_a_failing_deep_outranks_the_toggle_in_the_reason(strip, monkeypatch):
    """Review W5. With research deep passes switched off and deep failing, the strip said "deep passes are
    switched off - flip the toggle to use the QueueLLM deep model", in the dim class: it promised that the
    toggle makes deep usable, and it does not. Failing now outranks the toggle, as tier_locked and
    wheel_missing already did. The toggle's own state is still reported."""
    monkeypatch.setenv("AEROLLM_RESEARCH", "false")
    dv.note_call(False, error="boom")
    d = _deep(strip.client)
    assert d["reason_code"] == "deep_failing" and d["eligible_now"] is False
    assert d["enabled_by_user"] is False, "the toggle is still reported as off"
    assert d["reason"].startswith("deep generation is failing on this build")
    assert "flip the toggle" not in d["reason"] and "switched off" not in d["reason"]
    assert d["generation"]["state"] == "failing"
    # and the page paints that code in the danger class, whatever the toggle says
    html = (PORTAL / "templates" / "research.html").read_text(encoding="utf-8")
    assert "deep.reason_code === 'deep_failing' ? 'rx-truth-danger' : 'rx-truth-dim'" in html


# ── T2: the page ───────────────────────────────────────────────────────────

def test_t2_the_page_renders_deep_failing_with_the_danger_class():
    html = (PORTAL / "templates" / "research.html").read_text(encoding="utf-8")
    assert "deep.reason_code === 'deep_failing' ? 'rx-truth-danger' : 'rx-truth-dim'" in html
    css = (PORTAL / "static" / "research.css").read_text(encoding="utf-8")
    assert ".rx-truth-danger { color: var(--danger)" in css


# ── SEC2: error text with markup renders as text ───────────────────────────

def test_sec2_markup_in_an_error_travels_as_data_and_is_escaped_where_it_is_drawn(strip):
    nasty = "<img src=x onerror=alert(1)>"
    dv.note_call(False, error=f"RuntimeError: {nasty}")
    d = _deep(strip.client)
    assert nasty in d["reason"], "the API carries the text verbatim, as JSON data"
    html = (PORTAL / "templates" / "research.html").read_text(encoding="utf-8")
    assert "esc(deep.reason || 'off')" in html
    # the strip never inserts deep.reason without esc()
    import re
    for m in re.finditer(r"deep\.reason\b", html):
        window = html[max(0, m.start() - 8): m.end() + 12]
        assert "esc(" in window or "lock.title" in html[max(0, m.start() - 40): m.end() + 5] or \
            "reason_code" in html[m.start(): m.end() + 12], window


def test_sec2_the_switcher_draws_health_detail_only_through_esc():
    html = (PORTAL / "templates" / "_model_switcher.html").read_text(encoding="utf-8")
    import re
    uses = [m.start() for m in re.finditer(r"health\.detail", html)]
    assert uses, "the switcher shows the detail"
    for pos in uses:
        window = html[max(0, pos - 30): pos + 22]
        # a truthiness test, a direct esc(...), or the `reason` variable checked below
        assert "esc(" in window or "health.detail?" in window or "var reason = " in window, window
    # `reason` carries the detail; it may only be drawn through esc(reason), never concatenated raw
    assert "esc(reason)" in html
    assert "+reason" not in html and "reason+" not in html.replace("esc(reason)+", "")
