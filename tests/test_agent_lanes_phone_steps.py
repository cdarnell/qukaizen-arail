"""A2 — the lanes view can show Buddy on the phone, step by step.

ARCHITECTURE.md §5.10: the Admin lanes view used to show only the last record
per lane, so the win run's step 7 could not be judged there. `lanes_snapshot()`
now counts model calls only in `calls`/`last`/columns and adds a per-lane
`recent` list (the last 50 records that carry a channel, newest first, bodies
stripped); the trace-detail route adds `related`; admin.html renders "Buddy on
the phone". The schema stays `arail.agent_lanes/v1` and is only extended.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import textwrap

import pytest
from fastapi.testclient import TestClient

from arail import agent_context, agent_trace, config, scheduler
from arail.portal import app as app_mod
from arail.portal.app import app

TID = "0123456789abcdef"
WHO = {"pseudonym": "p_0123456789", "role": "talker"}


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


def _event(event, trace_id=TID, channel="telegram", identity=WHO, **kw):
    agent_trace.record(record_type="event", kind="agent", agent_id="buddy", label="buddy",
                       attribution="agent:buddy", origin="edge", event=event, channel=channel,
                       identity=identity, trace_id=trace_id, **kw)


def _model_call(trace_id=TID, channel="telegram", **kw):
    base = dict(record_type="model_call", kind="agent", agent_id="buddy", label="buddy",
                attribution="agent:buddy", origin="edge", channel=channel, identity=WHO,
                trace_id=trace_id, model="llama-ai-eng", backend="ollama_native", brain="reflex",
                ttft_ms=412.0, ttft_status="measured", tokens_in=1800, tokens_out=60, outcome="ok",
                cost={"tokens": 1860, "simulated_usd": 0.0004, "cash_usd": 0.0, "cash_basis": "local",
                      "label": "simulated"})
    base.update(kw)
    agent_trace.record(**base)


def _lane(snap, lane_id="buddy"):
    return next(l for l in snap["lanes"] if l["id"] == lane_id)


# ---------------------------------------------------------------------------
# lanes_snapshot
# ---------------------------------------------------------------------------

def test_schema_stays_v1_and_every_old_key_is_still_there():
    _model_call()
    snap = agent_trace.lanes_snapshot()
    assert snap["schema"] == "arail.agent_lanes/v1"
    for key in ("lanes", "unattributed", "user_defined", "hold", "recorder", "slot", "drops", "window"):
        assert key in snap
    for key in ("id", "display", "group", "calls", "last", "model", "backend", "brain", "effort", "ttft_ms",
                "ttft_status", "tokens_in", "tokens_out", "deep_reason_code", "deep_reason_detail", "empty_reason"):
        assert key in _lane(snap)


def test_events_do_not_count_as_calls_and_do_not_replace_last():
    _model_call()
    _event("reply_sent", reason="answer")
    _event("inbound_accepted")
    lane = _lane(agent_trace.lanes_snapshot())
    assert lane["calls"] == 1
    assert lane["last"]["record_type"] == "model_call"
    assert lane["model"] == "llama-ai-eng" and lane["tokens_out"] == 60


def test_a_lane_with_only_events_reads_as_no_calls():
    _event("startup", channel=None, identity=None)
    lane = _lane(agent_trace.lanes_snapshot())
    assert lane["calls"] == 0 and lane["last"] is None and lane["model"] is None
    assert lane["empty_reason"] == "no calls yet this session"


def test_legacy_records_without_record_type_still_count():
    agent_trace.record(trace_id="a" * 16, agent_id="researcher", kind="agent", model="m")
    assert _lane(agent_trace.lanes_snapshot(), "researcher")["calls"] == 1


def test_recent_lists_only_records_with_a_channel_newest_first():
    _event("startup", channel=None, identity=None)
    _event("inbound_accepted", trace_id="1" * 16)
    _model_call(trace_id="1" * 16)
    _event("reply_sent", trace_id="1" * 16, reason="answer")
    recent = _lane(agent_trace.lanes_snapshot())["recent"]
    assert [r.get("event") or r["record_type"] for r in recent] == ["reply_sent", "model_call", "inbound_accepted"]
    assert all(r["channel"] == "telegram" for r in recent)


def test_recent_is_capped_at_fifty():
    for i in range(60):
        _event("inbound_accepted", trace_id=f"{i:016x}")
    recent = _lane(agent_trace.lanes_snapshot())["recent"]
    assert len(recent) == 50
    assert recent[0]["trace_id"] == f"{59:016x}" and recent[-1]["trace_id"] == f"{10:016x}"


def test_recent_is_per_lane_and_empty_for_in_process_lanes():
    _event("reply_sent", reason="answer")
    agent_trace.record(trace_id="b" * 16, agent_id="researcher", kind="agent", model="m")
    snap = agent_trace.lanes_snapshot()
    assert _lane(snap, "researcher")["recent"] == []
    assert len(_lane(snap)["recent"]) == 1


def test_recent_strips_bodies_even_when_the_recorder_is_on():
    agent_trace.set_recorder_enabled(True)
    _model_call(bodies={"prompt": "SECRET-QUESTION", "response": "SECRET-ANSWER"})
    recent = _lane(agent_trace.lanes_snapshot())["recent"]
    assert recent[0]["bodies"] is None
    assert "SECRET" not in json.dumps(agent_trace.lanes_snapshot()["lanes"][0]["recent"])
    # the ring itself is untouched
    assert agent_trace.ring(1)[0]["bodies"]["prompt"] == "SECRET-QUESTION"


def test_events_do_not_inflate_unattributed_or_user_defined_counts():
    _event("reply_sent", reason="answer")
    agent_trace.record(record_type="event", kind="agent", agent_id="mystery", event="x", channel="telegram")
    snap = agent_trace.lanes_snapshot()
    assert snap["unattributed"]["calls"] == 0
    assert snap["user_defined"]["calls"] == 0


def test_tokens_out_by_agent_counts_model_calls_only():
    _model_call(tokens_out=60)
    _event("reply_sent", reason="answer", tokens_out=999)
    assert agent_trace.tokens_out_by_agent() == {"buddy": 60}


# ---------------------------------------------------------------------------
# find_with_related + the detail route
# ---------------------------------------------------------------------------

def test_find_with_related_returns_the_first_record_and_the_others_with_that_trace_id():
    _event("inbound_accepted")
    _model_call()
    _event("reply_sent", reason="answer")
    _event("inbound_accepted", trace_id="9" * 16)
    rec, related = agent_trace.find_with_related(TID)
    assert rec["event"] == "inbound_accepted"
    assert [r.get("event") or r["record_type"] for r in related] == ["model_call", "reply_sent"]
    assert all(r["trace_id"] == TID for r in related)


def test_related_is_capped_at_twenty_and_strips_bodies():
    _event("inbound_accepted")
    for _ in range(30):
        _model_call(bodies={"prompt": "p", "response": "r"})
    _, related = agent_trace.find_with_related(TID)
    assert len(related) == 20
    assert all(r["bodies"] is None for r in related)


def test_find_with_related_unknown_id_is_none():
    assert agent_trace.find_with_related("0" * 16) is None


def test_detail_route_adds_related_and_keeps_the_record(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    _event("inbound_accepted")
    _model_call()
    r = TestClient(app).get("/api/admin/agent-trace/" + TID)
    assert r.status_code == 200
    body = r.json()
    assert body["event"] == "inbound_accepted" and body["schema"] == "arail.agent_trace/v2"
    assert [x["record_type"] for x in body["related"]] == ["model_call"]


def test_detail_route_for_a_record_with_no_siblings_has_an_empty_related(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    agent_trace.record(trace_id="c" * 16, agent_id="researcher", kind="agent", model="m")
    body = TestClient(app).get("/api/admin/agent-trace/" + "c" * 16).json()
    assert body["model"] == "m" and body["related"] == []


def test_detail_route_404_and_tier_gate_unchanged(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    assert TestClient(app).get("/api/admin/agent-trace/" + "0" * 16).status_code == 404
    monkeypatch.setenv("LAB_TIER", "minimalist")
    assert TestClient(app).get("/api/admin/agent-trace/" + TID).status_code == 404


def test_the_lanes_endpoint_serves_recent(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    _model_call()
    lane = next(l for l in TestClient(app).get("/api/admin/agent-lanes").json()["lanes"] if l["id"] == "buddy")
    assert len(lane["recent"]) == 1 and lane["recent"][0]["identity"] == WHO


# ---------------------------------------------------------------------------
# admin.html
# ---------------------------------------------------------------------------

ADMIN = pathlib.Path(app_mod.__file__).parent / "templates" / "admin.html"


def _src() -> str:
    return ADMIN.read_text()


def test_admin_html_has_the_buddy_on_the_phone_block_after_the_lanes_list():
    src = _src()
    lanes = src.index('id="agent-lanes-list"')
    phone = src.index('id="buddy-phone-list"')
    assert phone > lanes
    assert "Buddy on the phone" in src[phone - 400:phone + 400]
    assert "last 50 steps" in src


def test_admin_html_renders_phone_rows_from_lane_recent_with_every_column():
    src = _src()
    start = src.index("function renderBuddyPhone")
    section = src[start:src.index("\nfunction ", start + 1)]
    for header in ("Time", "Step", "Channel", "Who", "Brain", "TTFT", "Tokens in/out", "Cost", "Outcome"):
        assert f"<th>{header}</th>" in section, header
    assert "lane.recent" in section or ".recent" in section
    assert "loadAgentLanes" in src and "renderBuddyPhone(snap)" in src


NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is needed to execute admin.html's render functions")
def test_phone_rows_escape_every_cell_and_show_honest_na(tmp_path):
    src = _src()
    start = src.index("const BUDDY_STEP_LABELS")
    end = src.index("\nfunction ", src.index("function renderBuddyPhone") + 1)
    chunk = src[start:end]
    script = textwrap.dedent("""
        const esc = (s) => String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        const elements = {};
        const document = {getElementById: (id) => elements[id] || (elements[id] = {innerHTML: '', style: {}})};
        %s
        const evil = '<img src=x onerror=alert(1)>';
        renderBuddyPhone({lanes: [{id: 'buddy', recent: [
          {record_type: 'model_call', ts: 1760968800, trace_id: '0123456789abcdef', channel: 'telegram',
           identity: {pseudonym: 'p_0123456789', role: 'talker'}, brain: 'reflex', model: 'llama-ai-eng',
           ttft_ms: 412.4, ttft_status: 'measured', tokens_in: 1800, tokens_out: 60, outcome: 'ok',
           cost: {tokens: 1860, simulated_usd: 0.0004, cash_usd: 0, cash_basis: 'local', label: 'simulated'}},
          {record_type: 'event', event: 'reply_sent', ts: 1760968801, trace_id: '0123456789abcdef', channel: 'telegram',
           identity: {pseudonym: 'p_0123456789', role: 'talker'}, reason: 'answer', outcome: null,
           brain: null, ttft_ms: null, ttft_status: null, tokens_in: null, tokens_out: null, cost: null},
          {record_type: 'event', event: evil, ts: 1760968802, trace_id: evil, channel: evil,
           identity: {pseudonym: evil, role: evil}, reason: evil, outcome: evil, cost: null},
        ]}]});
        process.stdout.write(JSON.stringify(elements['buddy-phone-list'].innerHTML));
    """) % chunk
    f = tmp_path / "render.js"
    f.write_text(script)
    out = subprocess.run([NODE, str(f)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    html = json.loads(out.stdout)
    assert "<img" not in html and "&lt;img" in html
    assert "Model call" in html and "Reply sent" in html
    assert "412ms" in html and "1800 / 60" in html
    assert "$0.00 cash" in html and "$0.0004 simulated" in html
    assert html.count("n/a") >= 4  # the event row: brain, ttft, tokens, cost
    assert "p_0123456789" in html and "talker" in html
    assert "<script" not in html


@pytest.mark.skipif(NODE is None, reason="node is needed to execute admin.html's render functions")
def test_phone_block_with_no_rows_says_so(tmp_path):
    src = _src()
    start = src.index("const BUDDY_STEP_LABELS")
    end = src.index("\nfunction ", src.index("function renderBuddyPhone") + 1)
    script = textwrap.dedent("""
        const esc = (s) => String(s);
        const elements = {};
        const document = {getElementById: (id) => elements[id] || (elements[id] = {innerHTML: '', style: {}})};
        %s
        renderBuddyPhone({lanes: [{id: 'buddy', recent: []}]});
        process.stdout.write(elements['buddy-phone-list'].innerHTML);
    """) % src[start:end]
    f = tmp_path / "empty.js"
    f.write_text(script)
    out = subprocess.run([NODE, str(f)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert "No phone traffic yet" in out.stdout


@pytest.mark.skipif(NODE is None, reason="node is needed to syntax-check admin.html's new JS")
def test_the_whole_new_phone_script_block_parses(tmp_path):
    """The render function is executed above; this also parses the drill-in and the
    click wiring, which a syntax slip would otherwise take down with the page script."""
    src = _src()
    block = src[src.index("// ── Buddy on the phone"):src.index("async function loadAgentLanes()")]
    wiring = src[src.index("const _buddyPhoneList"):src.index("loadAgentLanes();\nloadLegacyBodiesNotice();")]
    f = tmp_path / "block.js"
    f.write_text(block + "\n" + wiring)
    out = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
