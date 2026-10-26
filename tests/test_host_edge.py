"""A7 - the edge beyond the shared conformance cases: ledger, budget, brain choice, trace.

ARCHITECTURE.md §5.5 to §5.9, §7 F1, F6, F9, F22, F32, F41, F45. The conformance cases
(test_host_edge_conformance.py) say what the two sides agree on; these say what only
ARAIL can promise: the ledger survives a crash and a race, errors never echo request
text, a corrupt hold file is "unreadable", the edge's records reach the lanes view.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import os
import stat
import subprocess
import sys
import pathlib
from types import SimpleNamespace

import httpx
import pytest

from arail import agent_trace, scheduler
from arail.buddy_host import CONTRACT, CONTRACT_SHA256, admission, brain, budget, ledger
from arail.buddy_host import _time as T
from tests.buddy_conformance import FakeResearcher, World, resolve

REPO = pathlib.Path(__file__).resolve().parents[1]
CRED = CONTRACT["conformance"]["fake_credential"]
TID = "0123456789abcdef"
TALKER = {"pseudonym": "p_0123456789", "role": "talker"}
CONFIRMER = {"pseudonym": "p_3f9a1c2e7b", "role": "confirmer"}
BASE = CONTRACT["transport"]["base_path"]


def H(**over):
    h = {"Authorization": "Bearer " + CRED, "X-Buddy-Contract": "sha256:" + CONTRACT_SHA256,
         "Content-Type": "application/json"}
    h.update(over)
    return {k: v for k, v in h.items() if v is not None}


def approval(aid="7f3a9c2e", nonce="cd" * 32, confirmer=CONFIRMER, now=None, **over):
    now = now or dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    f = lambda s: (now + dt.timedelta(seconds=s)).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    body = {"v": 1, "approval_id": aid, "action": "arail.pause_research", "args": {},
            "proposal": {"id": aid, "proposed_at": f(-20), "proposed_by": "rule", "requested_by": CONFIRMER,
                         "channel": "telegram"},
            "confirm": {"confirmer": confirmer, "nonce_sha256": nonce, "confirmed_at": f(-5), "via": "tap"},
            "expires_at": f(580), "trace_id": TID}
    body.update(over)
    return body


def hs_body():
    return {"v": 1, "contract_sha256": CONTRACT_SHA256,
            "buddy": {"version": "0.1.0", "nanobot_pin": "v0.3.5", "instance_id": "0123456789abcdef",
                      "started_at": "2026-10-20T14:00:00Z"},
            "world": {"slug": "qa-practice", "world_sha256": "ab" * 32}}


@pytest.fixture
def lab(tmp_path, monkeypatch):
    w = World({}, "pause_research", tmp_path, monkeypatch)
    return w


def post(lab, path, body, **hdr):
    return lab.client.post(BASE + path, content=json.dumps(body), headers=H(**hdr))


# ---------------------------------------------------------------------------
# the ledger
# ---------------------------------------------------------------------------

def test_ledger_is_wal_full_sync_and_owner_only(tmp_path):
    led = ledger.Ledger(tmp_path / "buddy" / "approvals.sqlite3")
    led.begin("aaaaaaaa", "n" * 64, "r" * 64, "2026-10-20T14:00:00Z")
    assert led._db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert led._db.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL
    for suffix in ("", "-wal", "-shm"):
        p = pathlib.Path(str(led.path) + suffix)
        if p.exists():
            assert stat.S_IMODE(os.lstat(p).st_mode) == 0o600, p.name
    assert stat.S_IMODE(os.lstat(led.path.parent).st_mode) == 0o700


def test_ledger_unique_nonce_and_primary_key(tmp_path):
    led = ledger.Ledger(tmp_path / "a.sqlite3")
    led.begin("aaaaaaaa", "1" * 64, "r1", "t")
    with pytest.raises(ledger.ApprovalConflict):
        led.begin("aaaaaaaa", "2" * 64, "r2", "t")
    with pytest.raises(ledger.ApprovalConflict):
        led.begin("bbbbbbbb", "1" * 64, "r3", "t")
    assert led.count() == 1


def test_ledger_refuses_a_symlinked_database(tmp_path):
    real = tmp_path / "real.sqlite3"
    real.write_bytes(b"")
    (tmp_path / "link.sqlite3").symlink_to(real)
    with pytest.raises(OSError):
        ledger.Ledger(tmp_path / "link.sqlite3")


def test_a_row_stuck_in_executing_is_indeterminate_restart_and_stays_that_way(lab):
    body = approval()
    sha = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    led = ledger.default_ledger()
    led.begin(body["approval_id"], body["confirm"]["nonce_sha256"], sha, "2026-10-20T14:00:00Z")  # the crash: no finish()
    for _ in range(2):
        r = post(lab, "/pause_research", body)
        assert r.status_code == 200
        assert r.json()["outcome"] == "indeterminate_restart" and r.json()["replayed"] is True
    assert lab.researcher.pause_calls == 0
    assert led.get(body["approval_id"]).state == "done"


def test_two_identical_requests_at_once_pause_the_researcher_once(tmp_path, monkeypatch):
    w = World({}, "pause_research", tmp_path, monkeypatch)
    body = approval()

    async def go():
        transport = httpx.ASGITransport(app=w.client.app, client=("127.0.0.1", 5))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
            return await asyncio.gather(*[c.post(BASE + "/pause_research", content=json.dumps(body), headers=H())
                                          for _ in range(5)])

    results = asyncio.run(go())
    assert [r.status_code for r in results] == [200] * 5
    assert sorted(r.json()["replayed"] for r in results) == [False, True, True, True, True]
    assert {r.json()["outcome"] for r in results} == {"paused"}
    assert w.researcher.pause_calls == 1
    assert ledger.default_ledger().count() == 1


def test_a_replay_is_answered_even_after_the_lab_is_held_but_a_new_approval_is_not(lab):
    first = approval()
    assert post(lab, "/pause_research", first).json()["outcome"] == "paused"
    scheduler.halt_all_jobs()
    again = post(lab, "/pause_research", first)
    assert again.status_code == 200 and again.json()["replayed"] is True
    fresh = post(lab, "/pause_research", approval(aid="11112222", nonce="ef" * 32))
    assert fresh.status_code == 403 and fresh.json()["error"]["code"] == "held"
    assert lab.researcher.pause_calls == 1


def test_an_idle_researcher_is_never_asked_to_pause(tmp_path, monkeypatch):
    w = World({"research_status": "idle"}, "pause_research", tmp_path, monkeypatch)
    r = post(w, "/pause_research", approval())
    assert r.json()["outcome"] == "not_running" and w.researcher.pause_calls == 0


def test_freshness_boundaries(lab):
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    f = lambda s: (now + dt.timedelta(seconds=s)).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731

    def attempt(aid, nonce, proposed, confirmed, expires):
        b = approval(aid=aid, nonce=nonce, now=now)
        b["proposal"]["proposed_at"], b["confirm"]["confirmed_at"], b["expires_at"] = f(proposed), f(confirmed), f(expires)
        return post(lab, "/pause_research", b)

    assert attempt("00000001", "01" * 32, -100, -50, 400).status_code == 200      # 50 s old: fine
    assert attempt("00000002", "02" * 32, -100, -58, 400).status_code == 200      # just inside 60 s
    assert attempt("00000003", "03" * 32, -100, -70, 400).json()["error"]["code"] == "stale_approval"
    assert attempt("00000004", "04" * 32, -10, -5, 590).status_code == 200        # ttl exactly 600 s
    assert attempt("00000005", "05" * 32, -10, -5, 595).json()["error"]["code"] == "stale_approval"
    assert attempt("00000006", "06" * 32, -10, 55, 590).status_code == 200        # confirmed 55 s ahead: clock skew
    assert attempt("00000007", "07" * 32, -10, 65, 590).json()["error"]["code"] == "stale_approval"


def test_the_ledger_is_per_data_dir(tmp_path, monkeypatch):
    w = World({}, "pause_research", tmp_path, monkeypatch)
    assert post(w, "/pause_research", approval()).status_code == 200
    assert str(ledger.default_ledger().path).startswith(str(tmp_path))


# ---------------------------------------------------------------------------
# errors say nothing about the request
# ---------------------------------------------------------------------------

CANARY = "CANARY-9f3c1e-do-not-echo"


@pytest.mark.parametrize("path,body", [
    ("/handshake", {**hs_body(), "extra": CANARY}),
    ("/handshake", {"v": 1, "contract_sha256": CANARY}),
    ("/trace/record", {"v": 1, "records": [{"event": CANARY}]}),
    ("/hold_all", {"v": 1, "trace_id": TID, "reason": CANARY, "by": TALKER}),
    ("/pause_research", approval(action=CANARY)),
])
def test_a_schema_error_never_echoes_request_text(lab, path, body):
    r = post(lab, path, body)
    assert r.status_code == 422 and CANARY not in r.text
    assert r.json()["error"]["message"] == CONTRACT["errors"]["codes"]["schema_invalid"]["message"]


def test_bad_headers_never_echo_either(lab):
    r = lab.client.post(BASE + "/handshake", content=json.dumps(hs_body()),
                        headers=H(**{"Authorization": "Bearer " + CANARY, "X-Buddy-Contract": "sha256:" + CANARY}))
    assert CANARY not in r.text
    r = lab.client.post(BASE + "/handshake", content=json.dumps(hs_body()),
                        headers=H(**{"X-Buddy-Contract": "sha256:" + CANARY}))
    assert r.status_code == 412 and CANARY not in r.text


def test_every_error_carries_the_no_retry_header_and_no_store(lab):
    r = lab.client.post(BASE + "/handshake", content="{}", headers=H(Authorization=None))
    assert r.status_code == 401
    assert r.headers["x-should-retry"] == "false" and r.headers["cache-control"] == "no-store"


def test_body_size_cap_and_content_type(lab):
    big = json.dumps({**hs_body(), "pad": "x" * 17000})
    r = lab.client.post(BASE + "/handshake", content=big, headers=H())
    assert r.status_code == 422 and r.json()["error"]["code"] == "schema_invalid"
    r = lab.client.post(BASE + "/handshake", content=json.dumps(hs_body()), headers=H(**{"Content-Type": "text/plain"}))
    assert r.status_code == 422
    r = lab.client.post(BASE + "/handshake", content=b"\xff\xfe not json", headers=H())
    assert r.status_code == 422
    r = lab.client.post(BASE + "/handshake", content=json.dumps(hs_body()), headers=H(**{"Content-Type": "application/json; charset=utf-8"}))
    assert r.status_code == 200


def test_a_peer_with_no_address_is_refused():
    request = SimpleNamespace(client=None, headers={})
    with pytest.raises(admission.EdgeError) as exc:
        admission.check_placement(request)
    assert exc.value.code == "peer_not_loopback"


@pytest.mark.parametrize("peer,ok", [("127.0.0.1", True), ("::1", True), ("::ffff:127.0.0.1", True),
                                      ("127.0.0.2", False), ("10.0.0.5", False), ("testclient", False)])
def test_only_loopback_peers_are_admitted(peer, ok, monkeypatch):
    monkeypatch.delenv("ARAIL_INSTANCE", raising=False)
    monkeypatch.setenv("BIND_ADDR", "127.0.0.1")
    try:
        admission.check_placement(SimpleNamespace(client=SimpleNamespace(host=peer), headers={}))
        assert ok
    except admission.EdgeError as e:
        assert not ok and e.code == "peer_not_loopback"


# ---------------------------------------------------------------------------
# handshake and the hold file
# ---------------------------------------------------------------------------

def test_a_corrupt_halt_file_reads_as_unreadable_and_held(tmp_path, monkeypatch):
    w = World({"hold_readable": False}, "handshake", tmp_path, monkeypatch)
    hold = post(w, "/handshake", hs_body()).json()["hold"]
    assert hold["readable"] is False and hold["held"] is True
    r = post(w, "/pause_research", approval())
    assert r.status_code == 403 and r.json()["error"]["code"] == "hold_unreadable"


def test_the_handshake_reports_changed_at_in_utc_with_z(lab):
    scheduler.halt_all_jobs()
    hold = post(lab, "/handshake", hs_body()).json()["hold"]
    assert hold["held"] is True and hold["changed_at"].endswith("Z")
    T.parse(hold["changed_at"])


def test_the_first_handshake_from_an_instance_writes_one_edge_link_event(lab):
    for _ in range(3):
        assert post(lab, "/handshake", hs_body()).status_code == 200
    links = [r for r in agent_trace.ring(100) if r.get("event") == "edge_link"]
    assert len(links) == 1 and links[0]["origin"] == "edge" and links[0]["agent_id"] == "buddy"
    other = hs_body()
    other["buddy"]["instance_id"] = "fedcba9876543210"
    assert post(lab, "/handshake", other).status_code == 200
    assert len([r for r in agent_trace.ring(100) if r.get("event") == "edge_link"]) == 2


def test_a_handshake_does_not_mark_the_operator_present(lab):
    from arail import runtime_profile
    runtime_profile._last_presence_ts = 0.0
    assert post(lab, "/handshake", hs_body()).status_code == 200
    assert runtime_profile._last_presence_ts == 0.0


def test_the_handshake_never_writes_lab_state(lab):
    before = (scheduler.jobs_halted(), lab.researcher.status, ledger.default_ledger().count())
    post(lab, "/handshake", hs_body())
    assert (scheduler.jobs_halted(), lab.researcher.status, ledger.default_ledger().count()) == before


# ---------------------------------------------------------------------------
# trace.record and the lanes view
# ---------------------------------------------------------------------------

def _rec(event, reason=None, attrs=None, identity=TALKER, channel="telegram", ts="2026-10-20T14:00:01Z"):
    return {"event": event, "trace_id": TID, "client_ts": ts, "channel": channel, "identity": identity,
            "outcome": None, "reason": reason, "attrs": attrs or {}}


def test_trace_record_maps_fields_and_keeps_the_client_time_in_attrs(lab):
    r = post(lab, "/trace/record", {"v": 1, "records": [_rec("reply_sent", "answer", {"chips": 1, "chars": 90, "e2e_ms": 4100})]})
    assert r.status_code == 202 and r.json() == {"accepted": 1}
    rec = agent_trace.ring(1)[0]
    assert rec["schema"] == "arail.agent_trace/v2" and rec["record_type"] == "event"
    assert (rec["kind"], rec["agent_id"], rec["label"], rec["attribution"], rec["origin"]) == ("agent", "buddy", "buddy", "agent:buddy", "edge")
    assert (rec["event"], rec["channel"], rec["reason"], rec["trace_id"]) == ("reply_sent", "telegram", "answer", TID)
    assert rec["identity"] == TALKER
    assert rec["attrs"] == {"chips": 1, "chars": 90, "e2e_ms": 4100, "client_ts": "2026-10-20T14:00:01Z"}
    assert rec["tokens_in"] is None and rec["model"] is None and rec["cost"] is None  # no model-call fields on an event


def test_a_batch_is_all_or_nothing(lab):
    bad = [_rec("reply_sent", "answer"), _rec("reply_sent", "nope")]
    r = post(lab, "/trace/record", {"v": 1, "records": bad})
    assert r.status_code == 422 and r.json()["error"]["record_index"] == 1
    kinds = [rec["event"] for rec in agent_trace.ring(50)]
    assert "reply_sent" not in kinds                     # neither record was kept
    assert kinds == ["edge_refused"]                     # only the refusal itself is traced
    assert agent_trace.ring(1)[0]["reason"] == "schema_invalid"


def test_edge_written_records_reach_the_buddy_lane_recent_list(lab):
    """Win step 7: the hold and the action must be visible in the lanes view."""
    assert post(lab, "/hold_all", {"v": 1, "trace_id": TID, "reason": "telegram_stop", "by": TALKER}).status_code == 200
    scheduler.resume_all_jobs()
    assert post(lab, "/pause_research", approval()).status_code == 200
    post(lab, "/trace/record", {"v": 1, "records": [_rec("inbound_accepted", attrs={"input_chars": 5})]})
    lane = next(l for l in agent_trace.lanes_snapshot()["lanes"] if l["id"] == "buddy")
    events = [r["event"] for r in lane["recent"]]
    assert {"hold_all", "action_executed", "inbound_accepted"} <= set(events)
    assert all(r["channel"] == "telegram" for r in lane["recent"])
    done = next(r for r in lane["recent"] if r["event"] == "action_executed")
    assert done["identity"] == CONFIRMER and done["reason"] == "paused"
    assert lane["calls"] == 0  # events are not model calls


def test_a_replay_writes_no_second_action_executed(lab):
    body = approval()
    post(lab, "/pause_research", body)
    post(lab, "/pause_research", body)
    assert len([r for r in agent_trace.ring(100) if r.get("event") == "action_executed"]) == 1


def test_hold_all_is_idempotent_and_halts_the_lab(lab):
    body = {"v": 1, "trace_id": TID, "reason": "telegram_stop", "by": TALKER}
    first = post(lab, "/hold_all", body).json()
    second = post(lab, "/hold_all", body).json()
    assert (first["already_held"], second["already_held"]) == (False, True)
    assert scheduler.jobs_halted() is True
    assert len([r for r in agent_trace.ring(100) if r.get("event") == "hold_all"]) == 2


# ---------------------------------------------------------------------------
# admission refusals are logged, but a flood is coalesced
# ---------------------------------------------------------------------------

def test_admission_refusals_are_coalesced_with_a_count(lab):
    for _ in range(40):
        lab.client.post(BASE + "/handshake", content=json.dumps(hs_body()), headers=H(Authorization="Bearer nope"))
    refused = [r for r in agent_trace.ring(500) if r.get("event") == "edge_refused"]
    assert 1 <= len(refused) <= 2
    assert refused[0]["reason"] == "credential_invalid" and refused[0]["identity"] is None
    assert sum(r["attrs"]["count"] for r in refused) <= 40


def test_post_admission_refusals_are_not_coalesced(lab):
    for _ in range(5):
        admission.note_refusal("schema_invalid")
    assert len([r for r in agent_trace.ring(100) if r.get("event") == "edge_refused"]) == 5


# ---------------------------------------------------------------------------
# the portal around the edge
# ---------------------------------------------------------------------------

def test_the_contracts_routes_exist_and_nothing_else_under_host(lab):
    paths = {p for p in lab.client.app.openapi()["paths"] if p.startswith("/api/host/")}
    assert paths == {BASE + m["path"] for m in CONTRACT["methods"].values()}


def test_the_portal_boots_without_the_edge_if_the_contract_cannot_be_trusted():
    code = (
        "import sys; sys.path.insert(0, 'src')\n"
        "sys.modules['arail.buddy_host'] = None  # what a ContractStampError import looks like to the portal\n"
        "from arail.portal import app as a\n"
        "paths = a.app.openapi()['paths']\n"
        "assert not [p for p in paths if p.startswith('/api/host/')], paths\n"
        "assert '/api/research/pause' in paths\n"
        "print('booted-without-edge')\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO, timeout=120,
                       env={**os.environ, "ARAIL_DATA_DIR": "/tmp/arail-edge-boot-test"})
    assert r.returncode == 0 and "booted-without-edge" in r.stdout, r.stderr[-800:]


# ---------------------------------------------------------------------------
# budget
# ---------------------------------------------------------------------------

def test_budget_counts_checks_and_resets_at_local_midnight(tmp_path):
    day = ["2026-10-20"]
    clock = [1000.0]
    b = budget.Budget(tmp_path / "buddy" / "budget.json", day=lambda: day[0], clock=lambda: clock[0])
    assert b.snapshot() == {"day": "2026-10-20", "turns_used": 0, "turns_cap": 300, "tokens_used": 0, "tokens_cap": 600000}
    b.check("p_a")
    b.begin_turn("p_a")
    b.add_tokens(1800)
    assert b.snapshot()["turns_used"] == 1 and b.snapshot()["tokens_used"] == 1800
    b._seed(turns=299)
    b.check("p_a")
    b.begin_turn("p_a")
    with pytest.raises(budget.BudgetRefused) as e:
        b.check("p_a")
    assert e.value.code == "budget_exhausted"
    day[0] = "2026-10-21"
    assert b.snapshot()["turns_used"] == 0
    b.check("p_a")


def test_claim_turn_checks_and_counts_as_one_step(tmp_path):
    """Addendum A section A2 delta 1: the claim made with the slot in hand is the check that holds."""
    b = budget.Budget(tmp_path / "budget.json", day=lambda: "d")
    b._seed(turns=299)
    b.claim_turn("p_a")
    assert b.snapshot()["turns_used"] == 300
    with pytest.raises(budget.BudgetRefused) as e:
        b.claim_turn("p_b")
    assert e.value.code == "budget_exhausted"
    assert b.snapshot()["turns_used"] == 300          # a refusal counts nothing


def test_claim_turn_refuses_on_the_token_cap_and_on_the_per_person_rate(tmp_path):
    b = budget.Budget(tmp_path / "budget.json", day=lambda: "d", clock=lambda: 0.0)
    b.add_tokens(600_000)
    with pytest.raises(budget.BudgetRefused) as e:
        b.claim_turn("p_a")
    assert e.value.code == "budget_exhausted"
    c = budget.Budget(tmp_path / "other.json", day=lambda: "d", clock=lambda: 0.0)
    for _ in range(20):
        c.claim_turn("p_a")
    with pytest.raises(budget.BudgetRefused) as e:
        c.claim_turn("p_a")
    assert e.value.code == "identity_throttled" and c.snapshot()["turns_used"] == 20


def test_claim_turn_is_atomic_under_threads(tmp_path):
    import threading
    b = budget.Budget(tmp_path / "budget.json", day=lambda: "d")
    b._seed(turns=290)
    outcomes: list = []
    barrier = threading.Barrier(12)

    def go(i):
        barrier.wait()
        try:
            b.claim_turn(f"p_{i}")
            outcomes.append("ok")
        except budget.BudgetRefused as refused:
            outcomes.append(refused.code)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert outcomes.count("ok") == 10 and outcomes.count("budget_exhausted") == 2
    assert b.snapshot()["turns_used"] == 300


def test_budget_token_cap(tmp_path):
    b = budget.Budget(tmp_path / "budget.json", day=lambda: "d")
    b.add_tokens(599_999)
    b.check("p")
    b.add_tokens(1)
    with pytest.raises(budget.BudgetRefused):
        b.check("p")
    b.add_tokens(-5)
    b.add_tokens(0)
    assert b.snapshot()["tokens_used"] == 600_000


def test_throttle_is_per_pseudonym_and_a_sliding_minute(tmp_path):
    clock = [0.0]
    b = budget.Budget(tmp_path / "budget.json", day=lambda: "d", clock=lambda: clock[0])
    for _ in range(20):
        b.check("p_a")
        b.begin_turn("p_a")
        clock[0] += 1.0
    with pytest.raises(budget.BudgetRefused) as e:
        b.check("p_a")
    assert e.value.code == "identity_throttled"
    b.check("p_b")                      # someone else is unaffected
    clock[0] += 41.0                    # the first call is now older than 60 s
    b.check("p_a")


def test_budget_survives_a_restart_and_files_are_private(tmp_path):
    path = tmp_path / "buddy" / "budget.json"
    b1 = budget.Budget(path, day=lambda: "d")
    b1.begin_turn("p")
    b1.add_tokens(10)
    b2 = budget.Budget(path, day=lambda: "d")
    assert b2.snapshot()["turns_used"] == 1 and b2.snapshot()["tokens_used"] == 10
    assert stat.S_IMODE(os.lstat(path).st_mode) == 0o600


@pytest.mark.parametrize("text", ["{", "[]", '{"day": "d", "turns": "x", "tokens": 1}', '{"day": "d", "turns": -1, "tokens": 0}'])
def test_a_corrupt_budget_file_starts_the_day_from_zero(tmp_path, text):
    path = tmp_path / "budget.json"
    path.write_text(text)
    assert budget.Budget(path, day=lambda: "d").snapshot()["turns_used"] == 0


def test_a_budget_file_from_yesterday_is_ignored(tmp_path):
    path = tmp_path / "budget.json"
    path.write_text(json.dumps({"day": "2026-10-19", "turns": 299, "tokens": 5}))
    assert budget.Budget(path, day=lambda: "2026-10-20").snapshot()["turns_used"] == 0


# ---------------------------------------------------------------------------
# which brain
# ---------------------------------------------------------------------------

def _entry(**kw):
    base = dict(id="t0", display_name="L", model_id="llama-ai-eng", enabled=True, endpoint="http://127.0.0.1:11434/v1",
                provider_type="local", backend="ollama_native")
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.parametrize("entry,local", [
    (_entry(), True),
    (_entry(endpoint=None), True),
    (_entry(endpoint="http://localhost:11434/v1"), True),
    (_entry(endpoint="http://[::1]:11434/v1"), True),
    (_entry(endpoint="http://192.168.1.9:11434/v1"), False),
    (_entry(endpoint="http://ollama.example.test:11434/v1"), False),
    (_entry(backend="openai_compat"), False),
    (_entry(backend="claude", provider_type="anthropic", endpoint=None), False),
    (_entry(provider_type="aerollm", endpoint=None), False),
    (_entry(provider_type="gateway", endpoint="https://api.example.test/v1"), False),
    (_entry(provider_type="xai", endpoint=None), False),
])
def test_only_a_loopback_ollama_native_entry_is_local(entry, local):
    assert brain.entry_is_local(entry) is local


def test_describe_reports_the_refusal_reasons(monkeypatch):
    monkeypatch.setattr(brain, "probe_structured_output", lambda e: True)
    monkeypatch.setattr(brain, "catalog_family", lambda m: "llama")
    monkeypatch.setattr(brain, "resolve_entry", lambda: None)
    assert brain.describe().reason == "brain_unavailable"
    monkeypatch.setattr(brain, "resolve_entry", lambda: _entry(provider_type="anthropic", backend="claude", endpoint=None))
    v = brain.describe()
    assert (v.available, v.local, v.reason, v.model_id) == (False, False, "brain_not_local", None)
    monkeypatch.setattr(brain, "resolve_entry", lambda: _entry())
    v = brain.describe()
    assert (v.available, v.local, v.structured_output, v.attribution, v.model_id, v.backend) == (
        True, True, True, "Built with Llama", "llama-ai-eng", "ollama_native")


def test_describe_fails_closed_when_the_registry_raises(monkeypatch):
    def boom():
        raise RuntimeError("registry on fire")
    monkeypatch.setattr(brain, "resolve_entry", boom)
    assert brain.describe().reason == "brain_unavailable"


def test_attribution_only_for_the_llama_family(monkeypatch):
    monkeypatch.setattr(brain, "resolve_entry", lambda: _entry(model_id="qwen2.5:7b"))
    monkeypatch.setattr(brain, "probe_structured_output", lambda e: True)
    monkeypatch.setattr(brain, "catalog_family", lambda m: "qwen")
    assert brain.describe().attribution is None


@pytest.mark.parametrize("version,ok", [("0.32.4", True), ("0.5.0", True), ("0.5.1-rc1", True), ("1.0.0", True),
                                         ("0.4.9", False), ("0.0.1", False), ("garbage", False), ("", False)])
def test_the_ollama_version_probe(version, ok, monkeypatch):
    brain._reset_for_tests()
    import requests

    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"version": version}

    monkeypatch.setattr(requests, "get", lambda url, timeout: Resp())
    assert brain.probe_structured_output(_entry()) is ok


def test_the_ollama_probe_is_cached_and_a_failure_reads_as_unsupported(monkeypatch):
    brain._reset_for_tests()
    import requests
    calls = []

    def get(url, timeout):
        calls.append(url)
        raise requests.ConnectionError("down")

    monkeypatch.setattr(requests, "get", get)
    e = _entry()
    assert brain.probe_structured_output(e) is False
    assert brain.probe_structured_output(e) is False
    assert calls == ["http://127.0.0.1:11434/api/version"]


# ---------------------------------------------------------------------------
# time helpers
# ---------------------------------------------------------------------------

def test_time_helpers():
    assert T.fmt(T.parse("2026-10-20T09:00:00-05:00")) == "2026-10-20T14:00:00Z"
    assert T.parse("2026-10-20T14:00:00Z").tzinfo is not None
    assert T.parse("2026-10-20T14:00:00").tzinfo is not None
    assert T.from_naive_local(None) is None and T.from_naive_local("nope") is None
    assert T.from_naive_local("2026-10-20T14:00:00").endswith("Z")
    with pytest.raises(ValueError):
        T.parse("2026-13-40T99:00:00Z")


# ---------------------------------------------------------------------------
# registry.peek: asking on a timer must not spam the activity log
# ---------------------------------------------------------------------------

def _registry_with_tier0():
    from arail.registry.core import HealthState, ModelEntry, ModelRegistry
    reg = ModelRegistry()
    reg._loaded = True
    e = ModelEntry(id="t0", display_name="Llama AI Engineer", provider_type="local", backend="ollama_native",
                   endpoint="http://127.0.0.1:11434/v1", model_id="llama-ai-eng", tier=0)
    reg.entries[e.id] = e
    return reg, e, HealthState


def test_peek_matches_resolve_and_records_nothing():
    reg, e, HealthState = _registry_with_tier0()
    assert reg.peek("fast", "agents") is e and reg.resolve("fast", "agents").entry is e
    e.health = HealthState(status="unhealthy", detail="down")
    before = len(reg.recent_events)
    for _ in range(5):
        assert reg.peek("fast", "agents") is None
    assert len(reg.recent_events) == before                       # peek wrote no fallback event
    assert reg.resolve("fast", "agents").entry is None
    assert len(reg.recent_events) == before + 1                   # the real path still reports


def test_peek_follows_the_same_fallback_chain_as_resolve():
    from arail.registry.core import HealthState, ModelEntry
    reg, e, _ = _registry_with_tier0()
    deep = ModelEntry(id="t1", display_name="Deep", provider_type="local", backend="ollama_native",
                      endpoint="http://127.0.0.1:11434/v1", model_id="deep", tier=1)
    reg.entries[deep.id] = deep
    e.health = HealthState(status="unhealthy")
    assert reg.peek("reasoning", "agents") is reg.resolve("reasoning", "agents").entry
    assert reg.peek("fast", "agents") is None                     # fast has no fallback


def test_peek_rejects_an_unknown_profile():
    reg, *_ = _registry_with_tier0()
    with pytest.raises(ValueError):
        reg.peek("nonsense")
