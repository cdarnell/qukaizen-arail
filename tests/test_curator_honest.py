"""The Curator reports what happened: review, grow and forge.

C6 and F4 of sprints/2026-10-29-arail-deep-honest/ARCHITECTURE.md. Tests C1 to
C10, TQ3 and SEC2 (World Terms) are named for the architecture's list. The
silent version of this code said "All clear" after twenty passes whose judge
calls had all failed, and resealed the World after each. Scripted routers stand
in for the model (the pattern of tests/test_world_growth.py).
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from arail import deep_verdict as dv
from arail import world_forge as wf
from arail import world_mount as wm
from arail.activity import activity_log
from arail.portal import world_routes as wr
from arail.portal.app import app
from tests.fakes.fake_deep_runtime import fake_deep  # noqa: F401  (fixture)
from tests.world_bundle_builder import make_bundle

CSRF = {"sec-fetch-site": "same-origin"}
CATS = [{"id": "x", "label": "X"}, {"id": "y", "label": "Y"}]
BUG = ("backend `mlx-native` failed: apply_pre_block: Qwen2 layer (chunked prefill head): "
       "\"[rms_norm] (*weight) must have the same size as the last dimension of x but has 1 elements.")
FOUR = {"meaning", "threshold", "out_of_range", "failure_lives_in"}


def _terms(n: int) -> list[dict]:
    return [{"slug": f"term-{i}", "term": f"Term {i}", "category": "x", "short": "s", "definition": "d",
             "example": "e", "related": [], "source": "model:local"} for i in range(1, n + 1)]


class _R:
    def __init__(self, text: str, model: str) -> None:
        self.text, self.model = text, model


class Scripted:
    """A router whose answers are scripted by call number and prompt."""

    def __init__(self, *, name: str = "fake-deep", fail_from: int = 0, empty_from: int = 0,
                 cancel_after: int = 0, cancel_event=None, flag_term: str = "") -> None:
        self.backend_name = name
        self.fail_from, self.empty_from = fail_from, empty_from
        self.cancel_after, self.cancel_event, self.flag_term = cancel_after, cancel_event, flag_term
        self.calls = 0

    def complete(self, prompt, max_tokens=0, temperature=0, **kw):
        self.calls += 1
        n = self.calls
        if self.fail_from and n >= self.fail_from:
            raise RuntimeError(BUG)
        if self.empty_from and n >= self.empty_from:
            return _R("", self.backend_name)
        if self.cancel_after and n == self.cancel_after and self.cancel_event is not None:
            self.cancel_event.set()
        if "Judge it" in prompt:
            if self.flag_term and f'term: "{self.flag_term}"' in prompt:
                return _R('{"correct": false, "category_ok": true, "better_category": "", "bad_edges": [], '
                          '"note": "not right"}', self.backend_name)
            return _R('{"correct": true, "category_ok": true, "better_category": "", "bad_edges": [], '
                      '"note": ""}', self.backend_name)
        if "MISSING" in prompt:
            return _R('{"terms":[{"term":"Ricci flow","category":"x"}]}', self.backend_name)
        if "mapping the subject" in prompt:
            return _R('{"categories":[{"id":"x","label":"X"}]}', self.backend_name)
        if "key concepts" in prompt:
            return _R('{"terms":[{"term":"Alpha"},{"term":"Beta"},{"term":"Gamma"}]}', self.backend_name)
        if "directly-associated" in prompt:
            return _R('{"related":[]}', self.backend_name)
        if "Define" in prompt:
            return _R('{"short":"A fine short line.","definition":"Two sentences. About it.","example":"E.g."}',
                      self.backend_name)
        return _R('{"related":["term-1"]}', self.backend_name)


@pytest.fixture()
def client():
    """One portal event loop for the whole test. Without ``with`` every request gets a loop of its
    own and the background pass (``asyncio.create_task``) is dropped when the POST returns."""
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """A mounted World with six terms; returns a factory so a test can choose the size."""
    worlds, data, pkb = tmp_path / "worlds", tmp_path / "data", tmp_path / "pkb"
    worlds.mkdir()
    data.mkdir()
    monkeypatch.setattr(wm, "_default_worlds_dir", lambda: worlds)
    monkeypatch.setattr(wm, "_default_data_dir", lambda: data)
    monkeypatch.setattr(wm, "_default_pkb_root", lambda: pkb)
    wr._grow_state = {"state": "idle"}
    wr._review_state = {"state": "idle"}
    wr._forge_state = {"state": "idle"}

    def mount(n: int = 6, slug: str = "mathworld"):
        b = make_bundle(tmp_path / f"b-{slug}", slug=slug, display_name="Math World",
                        terms_list=_terms(n), categories=CATS)
        wm.mount(b, data_dir=data, pkb_root=pkb, worlds_dir=worlds)
        return worlds / slug

    yield type("W", (), {"mount": staticmethod(mount), "worlds": worlds})
    wr._grow_state = {"state": "idle"}
    wr._review_state = {"state": "idle"}
    wr._forge_state = {"state": "idle"}


def _wait(client, url, done=("done", "partial", "failed", "error", "not_applied", "cancelled"), secs=15):
    end = time.monotonic() + secs
    body = {}
    while time.monotonic() < end:
        body = client.get(url).json()
        if body.get("state") in done:
            return body
        time.sleep(0.05)
    raise AssertionError(f"{url} never finished: {body}")


def _review(client):
    assert client.post("/api/worlds/review", headers=CSRF).status_code == 202
    return _wait(client, "/api/worlds/review")


def _grow(client, brain="auto"):
    assert client.post("/api/worlds/grow", json={"brain": brain}, headers=CSRF).status_code == 202
    return _wait(client, "/api/worlds/grow")


def _curator_lines() -> list[dict]:
    return [e for e in activity_log.recent(200) if e["source"] == "curator"]


def _digest(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


# ── C1: a review with failures ─────────────────────────────────────────────

def test_c1_a_review_that_loses_calls_is_partial_and_never_all_clear(world, monkeypatch, client):
    catalog = world.mount(6)
    router = Scripted(fail_from=2)
    monkeypatch.setattr(wr, "_review_router", lambda: router)
    st = _review(client)
    assert st["state"] == "partial" and st["outcome"] == "partial"
    assert (st["planned"], st["judged"], st["failed"]) == (6, 1, 5)
    assert st["reviewed_count"] == 1, "what the judge answered, never min(16, len(terms))"
    assert st["judge_model"] == "fake-deep" and st["deep_skip"] is None
    assert "[rms_norm]" in st["deep_error"] and len(st["deep_error"]) <= 160
    lines = [e for e in _curator_lines() if e["message"].startswith("Curator review of")]
    assert len(lines) == 1 and lines[0]["level"] == "warn"
    msg = lines[0]["message"]
    assert msg.startswith("Curator review of 'Math World' is partial: 1 of 6 terms judged. ")
    assert "A full review judges all 6; the 5 unjudged terms have no flags, so their silence means nothing." in msg
    assert "fake-deep failed on 5 calls: RuntimeError:" in msg
    assert "The fault is in the model that judged, not the World." in msg
    assert not any("All clear" in e["message"] for e in activity_log.recent(200))
    doc = json.loads((catalog / "review.json").read_text())
    assert doc["schema"] == "arail.world-review/v1"
    assert (doc["outcome"], doc["planned"], doc["judged"], doc["failed"], doc["reviewed_count"]) == \
        ("partial", 6, 1, 5, 1)
    assert st["message"] == msg


def test_c1_a_deep_judge_is_named_queuellm_and_points_at_the_probe(world, monkeypatch, client):
    world.mount(6)
    monkeypatch.setattr(wr, "_review_router", lambda: Scripted(name="aerollm", fail_from=2))
    msg = _review(client)["message"]
    assert "QueueLLM failed on 5 calls" in msg and "aerollm failed" not in msg
    assert "The fault is in the deep runtime, not the World: run ./arailctl deep probe." in msg


def test_c1b_a_review_cancelled_mid_pass_is_partial_not_all_clear(world, monkeypatch, client):
    world.mount(6)
    router = Scripted(cancel_after=2, cancel_event=wr._review_cancel)
    monkeypatch.setattr(wr, "_review_router", lambda: router)
    st = _review(client)
    assert st["outcome"] == "partial" and (st["judged"], st["failed"]) == (2, 0)
    assert "The review was stopped before it finished." in st["message"]
    assert "failed on" not in st["message"], "nothing failed; it was stopped"
    assert not any("All clear" in e["message"] for e in activity_log.recent(200))


# ── C2: everything fails ───────────────────────────────────────────────────

def test_c2_a_review_where_every_call_fails_is_failed(world, monkeypatch, client):
    world.mount(6)
    monkeypatch.setattr(wr, "_review_router", lambda: Scripted(fail_from=1))
    st = _review(client)
    assert st["state"] == "failed" and st["outcome"] == "failed"
    assert (st["planned"], st["judged"], st["failed"]) == (6, 0, 6) and st["reviewed_count"] == 0
    assert st["message"].startswith("Curator review of 'Math World' failed: 0 of 6 terms judged.")
    assert "All clear" not in st["message"]


def test_c2_an_empty_answer_is_a_failed_call_too(world, monkeypatch, client):
    world.mount(6)
    monkeypatch.setattr(wr, "_review_router", lambda: Scripted(empty_from=3))
    st = _review(client)
    assert (st["judged"], st["failed"], st["outcome"]) == (2, 4, "partial")
    assert "the model returned no text" in st["deep_error"]


# ── C3: a complete review ──────────────────────────────────────────────────

def test_c3_a_complete_review_with_no_flags_says_all_clear_with_the_full_review_clause(world, monkeypatch, client):
    world.mount(6)
    monkeypatch.setattr(wr, "_review_router", lambda: Scripted())
    st = _review(client)
    assert st["state"] == "done" and st["outcome"] == "complete"
    assert (st["planned"], st["judged"], st["failed"]) == (6, 6, 0)
    expected = ("Curator reviewed all 6 of 6 terms in 'Math World' with fake-deep — no flags. All clear. "
                "(6 of 6 is a full review; fewer is reported as partial. "
                "A flag is a term the judge wants changed: Knowledge → World Terms.)")
    assert st["message"] == expected
    line = [e for e in _curator_lines() if e["message"].startswith("Curator reviewed")]
    assert line and line[0]["level"] == "success" and line[0]["message"] == expected


def test_c3_a_complete_review_with_a_flag_is_a_warning_without_all_clear(world, monkeypatch, client):
    world.mount(6)
    monkeypatch.setattr(wr, "_review_router", lambda: Scripted(flag_term="Term 2"))
    st = _review(client)
    assert st["outcome"] == "complete" and len(st["flags"]) == 1 and st["flags"][0]["slug"] == "term-2"
    assert "— 1 flag. (6 of 6 is a full review" in st["message"] and "All clear" not in st["message"]
    line = [e for e in _curator_lines() if e["message"].startswith("Curator reviewed")][0]
    assert line["level"] == "warn"


def test_c3_a_big_world_plans_sixteen(world, monkeypatch, client):
    world.mount(20)
    router = Scripted()
    monkeypatch.setattr(wr, "_review_router", lambda: router)
    st = _review(client)
    assert (st["planned"], st["judged"], router.calls) == (16, 16, 16)
    assert "all 16 of 16 terms" in st["message"]


# ── C4: grow with a failed call changes nothing ────────────────────────────

def test_c4_a_grow_pass_with_a_failed_call_is_not_applied_and_the_world_is_unchanged(world, monkeypatch, client):
    catalog = world.mount(6)
    before = _digest(catalog)
    assert wm.verify_seal(wm.load_bundle(catalog)).ok
    resealed, swapped = [], []
    monkeypatch.setattr(wf, "reseal_bundle", lambda *a, **k: resealed.append(1))
    monkeypatch.setattr(wm, "swap", lambda *a, **k: swapped.append(1))
    router = Scripted(fail_from=3)
    monkeypatch.setattr(wr, "_curation_router", lambda brain: router)
    st = _grow(client)
    assert st["state"] == "not_applied"
    assert st["calls"]["failed"] >= 1 and st["calls"]["attempted"] >= st["calls"]["failed"]
    assert resealed == [] and swapped == [], "no reseal and no swap after a failed call"
    assert _digest(catalog) == before, "every bundle byte is unchanged"
    assert not (catalog / "evolution.json").exists(), "no evolution entry"
    assert wm.verify_seal(wm.load_bundle(catalog)).ok
    msg = st["message"]
    assert msg.startswith("Growth pass on 'Math World' not applied: fake-deep failed on ")
    assert "A pass is applied only when every call answers, so the World is unchanged: " \
           "no reseal, no swap, no evolution entry." in msg
    assert "Check the model and run it again." in msg
    line = [e for e in _curator_lines() if "not applied" in e["message"]]
    assert len(line) == 1 and line[0]["level"] == "warn" and line[0]["message"] == msg
    assert not any("evolved via" in e["message"] for e in activity_log.recent(200))


def test_c4_a_deep_grow_points_at_the_probe(world, monkeypatch, client):
    world.mount(6)
    monkeypatch.setattr(wr, "_curation_router", lambda brain: Scripted(name="aerollm", fail_from=2))
    st = _grow(client)
    assert "QueueLLM failed on " in st["message"] and st["message"].count("Run ./arailctl deep probe.") == 1


# ── C5: a clean grow is as before, and says every call answered ────────────

def test_c5_a_clean_grow_is_applied_and_says_every_call_answered(world, monkeypatch, client):
    catalog = world.mount(2)
    router = Scripted()
    monkeypatch.setattr(wr, "_curation_router", lambda brain: router)
    st = _grow(client)
    assert st["state"] == "done" and st["added"] == 1
    assert st["calls"] == {"attempted": router.calls, "failed": 0}
    assert (catalog / "evolution.json").exists() and wm.verify_seal(wm.load_bundle(catalog)).ok
    line = [e for e in _curator_lines() if "evolved via" in e["message"]]
    assert len(line) == 1 and line[0]["level"] == "success"
    assert line[0]["message"].endswith(f"Reversible in the term editor. (all {router.calls} model calls answered)")
    assert "evolved via fake-deep: +1 new term, 0 corrections." in line[0]["message"]


# ── C6: deep already failing before the pass ───────────────────────────────

def test_c6_a_review_while_deep_is_failing_names_the_fast_judge_and_the_reason(world, fake_deep, monkeypatch, client):
    fd = fake_deep("pass")
    monkeypatch.setenv("LAB_TIER", "maximus")
    catalog = world.mount(6)
    dv.identity()
    dv.note_call(False, error="RuntimeError: rms_norm", consumer="agent:buddy")
    fast = Scripted(name="llama-ai-eng")
    import arail.router as router_pkg
    monkeypatch.setattr(router_pkg, "ModelRouter", lambda *a, **k: fast)
    st = _review(client)
    assert st["outcome"] == "complete" and st["judge_model"] == "llama-ai-eng"
    assert st["deep_skip"] == "deep_failing"
    assert " Judged by llama-ai-eng; deep failing: deep generation is failing on this build" in st["message"]
    assert fast.calls == 6 and fd.gen_calls() == [], "nothing was counted as deep"
    assert json.loads((catalog / "review.json").read_text())["deep_skip"] == "deep_failing"


def test_c6_a_cloud_brain_grow_does_not_blame_deep(world, fake_deep, monkeypatch, client):
    fake_deep("pass")
    monkeypatch.setenv("LAB_TIER", "maximus")
    world.mount(2)
    dv.identity()
    dv.note_call(False, error="boom")
    monkeypatch.setattr(wr, "_curation_router", lambda brain: Scripted(name="claude"))
    st = _grow(client, brain="claude")
    assert st["state"] == "done"
    line = [e for e in _curator_lines() if "evolved via" in e["message"]][0]["message"]
    assert "deep failing" not in line


# ── C7: the forge ──────────────────────────────────────────────────────────

def _forge(client, subject="Algebra"):
    r = client.post("/api/worlds/forge", json={"subject": subject, "max_terms": 25, "brain": "local"}, headers=CSRF)
    assert r.status_code == 202, r.text
    return _wait(client, "/api/worlds/forge/status", secs=30)


def test_c7_a_forge_that_lost_calls_still_shows_the_draft_and_says_so(world, monkeypatch, client):
    router = Scripted(fail_from=8)
    monkeypatch.setattr(wr, "_curation_router", lambda brain: router)
    st = _forge(client)
    assert st["state"] == "done", st
    assert st["calls"]["failed"] >= 1
    assert re.search(r"· \d+ of \d+ model calls failed \(RuntimeError: .*\); the draft is incomplete", st["message"])
    line = [e for e in activity_log.recent(200) if e["source"] == "forge" and "model calls failed" in e["message"]]
    assert len(line) == 1 and line[0]["level"] == "warn"
    assert re.match(r"Forge of 'Algebra' finished with \d+ of \d+ model calls failed \(.*\); the draft is "
                    r"incomplete\. Review it before mounting\.", line[0]["message"])
    prev = client.get("/api/worlds/forge/preview").json()
    assert prev["failed_calls"] == st["calls"]["failed"] and prev["terms"], "the draft is still shown"
    assert any("model call" in w and "failed while this World was forged" in w for w in prev["warnings"])
    assert wr._forge_result.stats["failed_calls"] == st["calls"]["failed"]


def test_c7_a_forge_that_produced_nothing_blames_the_calls_not_the_subject(world, monkeypatch, client):
    monkeypatch.setattr(wr, "_curation_router", lambda brain: Scripted(fail_from=1))
    st = _forge(client)
    assert st["state"] == "error"
    assert re.fullmatch(r"nothing usable produced: \d+ of \d+ model calls failed \(RuntimeError: .*\)",
                        st["message"])
    assert "try a richer model" not in st["message"], "the old advice was wrong when the calls failed"
    line = [e for e in activity_log.recent(200) if e["source"] == "forge" and "produced nothing usable" in e["message"]]
    assert line and re.match(r"Forge of 'Algebra' produced nothing usable: \d+ of \d+ model calls failed",
                             line[0]["message"])


def test_c7_a_clean_forge_is_as_before(world, monkeypatch, client):
    monkeypatch.setattr(wr, "_curation_router", lambda brain: Scripted())
    st = _forge(client)
    assert st["state"] == "done" and st["calls"]["failed"] == 0 and "failed" not in st["message"]
    assert client.get("/api/worlds/forge/preview").json()["failed_calls"] == 0
    assert any(e["message"].startswith("Forged 'Algebra'") for e in activity_log.recent(200))


# ── C8: the counting wrapper ───────────────────────────────────────────────

def test_c8_counted_router_passes_attributes_through_and_reraises_unchanged():
    inner = Scripted(fail_from=2)
    inner.extra = "kept"
    c = wr._CountedRouter(inner)
    assert c.backend_name == "fake-deep" and c.extra == "kept"
    assert c.complete("Judge it").text
    boom = None
    try:
        c.complete("Judge it")
    except RuntimeError as e:
        boom = e
    assert boom is not None and BUG in str(boom) and type(boom) is RuntimeError
    assert (c.attempted, c.failed, c.judged) == (2, 1, 1)
    assert c.first_error.startswith("RuntimeError: backend `mlx-native` failed")
    with pytest.raises(AttributeError):
        c.no_such_attribute


def test_c8_counted_router_counts_empty_text_and_keeps_the_first_error():
    c = wr._CountedRouter(Scripted(empty_from=1))
    assert c.complete("x").text == ""
    assert (c.attempted, c.failed, c.first_error) == (1, 1, "the model returned no text")


def test_c8_counted_router_is_thread_safe():
    c = wr._CountedRouter(Scripted(fail_from=1))

    def work():
        for _ in range(100):
            try:
                c.complete("x")
            except RuntimeError:
                pass

    threads = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert (c.attempted, c.failed) == (800, 800)


# ── C9 / TQ3: every number is taught ───────────────────────────────────────

def _numeric(doc):
    return set(wr._numeric_paths(doc))


def _assert_taught(doc):
    need = _numeric(doc)
    assert need <= set(doc["teaching"]), sorted(need - set(doc["teaching"]))
    for p in need:
        assert set(doc["teaching"][p]) == FOUR and all(v.strip() for v in doc["teaching"][p].values()), p


def test_tq3_the_teaching_table_is_complete():
    for k, v in wr._CURATOR_TEACHING.items():
        assert set(v) == FOUR and all(x.strip() for x in v.values()), k


@pytest.mark.parametrize("router_kw,outcome", [({}, "complete"), ({"fail_from": 2}, "partial"),
                                               ({"fail_from": 1}, "failed")])
def test_tq3_review_json_and_the_endpoint_teach_every_number(world, monkeypatch, router_kw, outcome, client):
    catalog = world.mount(6)
    monkeypatch.setattr(wr, "_review_router", lambda: Scripted(**router_kw))
    st = _review(client)
    assert st["outcome"] == outcome
    _assert_taught(st)
    assert {"planned", "judged", "failed", "reviewed_count"} <= set(st["teaching"])
    doc = json.loads((catalog / "review.json").read_text())
    _assert_taught(doc)


@pytest.mark.parametrize("router_kw,state", [({}, "done"), ({"fail_from": 3}, "not_applied")])
def test_tq3_grow_endpoint_teaches_every_number(world, monkeypatch, router_kw, state, client):
    world.mount(2)
    monkeypatch.setattr(wr, "_curation_router", lambda brain: Scripted(**router_kw))
    st = _grow(client)
    assert st["state"] == state
    _assert_taught(st)
    assert {"calls.attempted", "calls.failed"} <= set(st["teaching"])
    if state == "done":
        assert {"added", "corrected"} <= set(st["teaching"])


def test_tq3_an_idle_endpoint_has_nothing_to_teach_and_no_bare_numbers(world, client):
    world.mount(2)
    for url in ("/api/worlds/review", "/api/worlds/grow"):
        body = client.get(url).json()
        assert body["state"] == "idle" and _numeric(body) == set() and body["teaching"] == {}


def test_tq3_every_curator_line_carries_its_clause(world, monkeypatch, client):
    world.mount(6)
    monkeypatch.setattr(wr, "_review_router", lambda: Scripted())
    _review(client)
    monkeypatch.setattr(wr, "_review_router", lambda: Scripted(fail_from=2))
    _review(client)
    lines = " \n".join(e["message"] for e in _curator_lines())
    for clause in ("is a full review; fewer is reported as partial", "A flag is a term the judge wants changed",
                   "A full review judges all 6", "so their silence means nothing"):
        assert clause in lines, clause


def test_unknown_numeric_field_is_visible_to_the_test_not_a_server_error():
    out = wr._with_teaching({"state": "idle", "mystery": 7})
    assert "mystery" not in out["teaching"], "production does not 500 on an untaught field"
    assert "mystery" in _numeric(out) - set(out["teaching"]), "but the walk above would catch it"
