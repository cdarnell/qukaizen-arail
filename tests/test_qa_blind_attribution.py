"""QA-BLIND-3 (W2, correctness) — authored from ARCHITECTURE.md's contract #1
and the "Attribution: which layer sets the context" section only, without
reading the builder's tests for this surface.

The contract restated:

  * ``billing_source`` is ``agent:<id>`` / ``sys:<label>`` / ``ui`` /
    ``unattributed`` — **never** a bare ``agent``, never an invented id,
    never dropped.
  * A realistic session must show >= 2 distinct ``agent:*`` keys, exactly
    ``0`` under the bare key ``agent``, and ``unattributed == 0``.
  * A non-zero ``unattributed`` is a **fail with the printed call sites as
    the bug report** (DE2 firing).
  * ``calls_by_source``'s persisted legacy ``"agent"`` bucket is renamed
    once to ``"agent:pre-p1-legacy"`` on load, idempotently.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

from arail import agent_context, agent_trace, config
from arail.router.backends import BaseBackend, ModelResponse
from arail.router.core import ModelRouter


class _FakeBackend(BaseBackend):
    def complete(self, prompt, max_tokens=512, temperature=0.7, top_p=None,
                 *, system=None, messages=None):
        return ModelResponse(text="out", model="fake-qa", tokens_used=5,
                             backend="fake", latency_ms=1.0)

    def health_check(self) -> bool:
        return True


@pytest.fixture
def tracker(monkeypatch, tmp_path):
    """The real ``cost_tracker`` singleton with its file redirected, so
    ``calls_by_source`` is this test's alone (the repo conftest deliberately
    leaves costs.json un-isolated)."""
    from arail.costs import cost_tracker
    monkeypatch.setattr(cost_tracker, "_data_path", tmp_path / "qa-costs.json")
    monkeypatch.setattr(cost_tracker, "calls_by_source", {})
    return cost_tracker


def _router(billing_source: str = "agent") -> ModelRouter:
    return ModelRouter.from_backend(_FakeBackend(), "fake",
                                    billing_source=billing_source)


def _disk_traces() -> list[dict]:
    path = config.DATA_DIR / "agent_traces.jsonl"
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


# ---------------------------------------------------------------------------
# QA-BLIND-3 proper
# ---------------------------------------------------------------------------

def test_a_realistic_session_has_no_generic_agent_bucket_and_no_unattributed(
        tracker):
    """A scripted session across the layers the sprint wired: a chat turn
    (ui), two agents (buddy, researcher), and two non-agent system callers
    (dictionary, world-forge)."""
    agent_router = _router()
    chat_router = _router(billing_source="ui")

    chat_router.complete("operator's own question")
    with agent_context.agent_call("buddy"):
        agent_router.complete("buddy's proactive line")
    with agent_context.agent_call("researcher"):
        agent_router.complete("researcher's experiment framing")
    with agent_context.system_call("dictionary"):
        agent_router.complete("define a term")
    with agent_context.system_call("world-forge"):
        agent_router.complete("forge a world")

    buckets = dict(tracker.calls_by_source)
    # Presence first — an empty bucket dict would satisfy every "not in"
    # assertion below.
    assert sum(buckets.values()) == 5, buckets

    agent_keys = [k for k in buckets if k.startswith("agent:")]
    assert len(agent_keys) >= 2, buckets
    assert "agent" not in buckets, (
        f"the generic agent bucket is back: {buckets}")
    assert buckets.get("unattributed", 0) == 0, (
        "unattributed calls in a scripted session — call sites: "
        + repr([t["call_site"] for t in _disk_traces()
                if t["attribution"] == "unattributed"]))
    assert buckets["ui"] == 1, "the chat tab must keep its own bucket"
    assert buckets["sys:dictionary"] == 1
    assert buckets["sys:world-forge"] == 1


def test_each_call_in_the_session_left_exactly_one_trace_with_its_own_id(
        tracker):
    router = _router()
    with agent_context.agent_call("buddy"):
        router.complete("one")
    with agent_context.agent_call("researcher"):
        router.complete("two")

    traces = _disk_traces()
    assert len(traces) == 2
    assert len({t["trace_id"] for t in traces}) == 2
    assert {t["attribution"] for t in traces} == {"agent:buddy",
                                                 "agent:researcher"}


def test_unattributed_is_never_recorded_as_a_plausible_agent(tracker):
    """The load-bearing row of the ``billing_source`` table: no context +
    a router built with ``billing_source="agent"`` must be
    ``unattributed`` — not ``agent``, not a guess."""
    router = _router()
    router.complete("nobody set a context")

    assert dict(tracker.calls_by_source) == {"unattributed": 1}
    trace = _disk_traces()[0]
    assert trace["attribution"] == "unattributed"
    assert trace["agent_id"] is None
    assert trace["kind"] is None
    assert trace["call_site"], "an unattributed call must name its call site"
    assert __name__.split(".")[-1] in trace["call_site"], (
        f"call_site should point at this test module, got {trace['call_site']}")


def test_the_unattributed_lane_is_always_present_and_lists_call_sites(tracker):
    router = _router()
    router.complete("leak one")
    router.complete("leak two")

    snap = agent_trace.lanes_snapshot()
    assert snap["unattributed"]["calls"] == 2
    sites = snap["unattributed"]["call_sites"]
    assert sites and all(s["call_site"] for s in sites)


# ---------------------------------------------------------------------------
# Attribution across the four hops the design depends on
# ---------------------------------------------------------------------------

def test_attribution_survives_create_task(tracker):
    """A1: an agent that spawns its loop inside ``start()`` is attributed for
    the life of that loop, because ``create_task`` copies the context. This
    is the whole basis of the L1 loader contract."""
    router = _router()

    async def _main() -> None:
        with agent_context.agent_call("buddy"):
            task = asyncio.create_task(asyncio.to_thread(
                lambda: router.complete("from the task")))
            await task

    asyncio.run(_main())
    assert _disk_traces()[0]["attribution"] == "agent:buddy"


def test_attribution_survives_to_thread(tracker):
    """A2: Buddy's dream call goes through ``asyncio.to_thread``."""
    router = _router()

    async def _main() -> None:
        with agent_context.agent_call("buddy"):
            await asyncio.to_thread(router.complete, "from the worker thread")

    asyncio.run(_main())
    assert _disk_traces()[0]["attribution"] == "agent:buddy"


def test_attribution_survives_spawn_thread_but_not_a_bare_thread(tracker):
    """A3: a bare ``threading.Thread`` does **not** copy the context — which
    is exactly why ``spawn_thread`` exists. Both halves asserted together so
    the shim cannot be deleted as "unnecessary"."""
    router = _router()

    t = agent_context.spawn_thread(router.complete, "via spawn_thread")
    with agent_context.agent_call("librarian"):
        t2 = agent_context.spawn_thread(router.complete, "via spawn_thread")
        t2.start()
        t2.join(timeout=10)
    t.start()
    t.join(timeout=10)

    attributions = [t["attribution"] for t in _disk_traces()]
    assert "agent:librarian" in attributions, (
        "spawn_thread must carry the context into the new thread")

    bare = threading.Thread(target=router.complete, args=("via bare thread",))
    with agent_context.agent_call("librarian"):
        bare.start()
        bare.join(timeout=10)
    assert _disk_traces()[-1]["attribution"] == "unattributed", (
        "a bare Thread loses the context — it must degrade to a *visible* "
        "unattributed lane, never to a wrong agent id")


def test_a_raising_body_does_not_leak_attribution_to_a_sibling(tracker):
    """The ``finally`` reset: an exception inside an ``agent_call`` block must
    not leave the contextvar set for whatever runs next."""
    router = _router()
    with pytest.raises(ValueError):
        with agent_context.agent_call("buddy"):
            raise ValueError("agent blew up")
    assert agent_context.current() is None
    router.complete("the next thing that runs")
    assert _disk_traces()[0]["attribution"] == "unattributed"


def test_two_concurrent_tasks_through_one_cached_router_keep_their_own_ids(
        tracker):
    """F5: routers *are* cached and shared. Attribution is call-scoped, so
    one router serving two agents at once must produce two different
    attributions."""
    router = _router()  # ONE router instance, shared

    async def _one(agent_id: str) -> None:
        with agent_context.agent_call(agent_id):
            await asyncio.sleep(0)
            await asyncio.to_thread(router.complete, f"work for {agent_id}")

    async def _main() -> None:
        await asyncio.gather(_one("buddy"), _one("researcher"),
                             _one("librarian"))

    asyncio.run(_main())
    got = sorted(t["attribution"] for t in _disk_traces())
    assert got == ["agent:buddy", "agent:librarian", "agent:researcher"], got


def test_out_of_process_context_does_not_double_write_a_trace(tracker):
    """Contract #9: exactly one trace writer per round-trip — the child must
    not author its own record."""
    router = _router()
    payload = {"trace_id": "a" * 16, "agent_id": "researcher",
               "brain": "reflex", "effort": "low"}
    with agent_context.from_subprocess_payload(payload):
        router.complete("child-side call")
    assert _disk_traces() == []
    assert dict(tracker.calls_by_source) == {"agent:researcher": 1}, (
        "the child still bills its own cost_tracker, only the trace is the "
        "parent's to write")


# ---------------------------------------------------------------------------
# Hostile / boundary agent ids — the id reaches a JSON file, a cost-bucket
# key and a DOM attribute
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hostile,expected", [
    ("../../etc/passwd", "etc_passwd"),
    ("..", None),
    ("/", None),
    ("  ", None),
    ("", None),
    (None, None),
    ("Buddy", "buddy"),
    ("BUDDY  ", "buddy"),
    ("a/b\\c", "a_b_c"),
    ("x" * 200, "x" * 64),
    ("<script>alert(1)</script>", "script_alert_1_script"),
    ("buddy\n\rinjected", "buddy_injected"),
    ("buddy\x00null", "buddy_null"),
    ("日本語", None),
    ("agent:buddy", "agent_buddy"),
])
def test_hostile_agent_ids_are_sanitised_or_treated_as_unattributed(
        tracker, hostile, expected):
    router = _router()
    with agent_context.agent_call(hostile):
        router.complete("hostile id")

    trace = _disk_traces()[0]
    if expected is None:
        assert trace["attribution"] == "unattributed", (
            f"{hostile!r} must not invent a label")
        assert trace["agent_id"] is None
    else:
        assert trace["agent_id"] == expected, trace["agent_id"]
        assert trace["attribution"] == f"agent:{expected}"
        # The id reaches a cost-bucket key and the DOM; it must contain
        # nothing that could break either.
        assert all(c.isalnum() or c in "_.-" for c in trace["agent_id"])
        assert len(trace["agent_id"]) <= 64


def test_a_sanitised_id_cannot_forge_the_unattributed_sentinel(tracker):
    """A hostile id must not be able to *become* the sentinel lane or the
    generic bucket by spelling it."""
    router = _router()
    for hostile in ("unattributed", "agent", "sys:dictionary"):
        with agent_context.agent_call(hostile):
            router.complete("forge attempt")
    attributions = [t["attribution"] for t in _disk_traces()]
    assert attributions == ["agent:unattributed", "agent:agent",
                            "agent:sys_dictionary"], attributions
    assert "agent" not in tracker.calls_by_source
    assert "unattributed" not in tracker.calls_by_source


# ---------------------------------------------------------------------------
# Reentrancy
# ---------------------------------------------------------------------------

def test_same_agent_nested_shares_one_trace_id(tracker):
    router = _router()
    with agent_context.agent_call("buddy"):
        with agent_context.agent_call("buddy"):
            router.complete("step one")
        router.complete("step two")
    traces = _disk_traces()
    assert len({t["trace_id"] for t in traces}) == 1, (
        "a multi-step decision by one agent shares one trace id")


def test_different_agent_nested_records_the_parent(tracker):
    router = _router()
    with agent_context.agent_call("researcher"):
        with agent_context.agent_call("browser"):
            router.complete("nested helper call")
    trace = _disk_traces()[0]
    assert trace["agent_id"] == "browser"
    assert trace["parent_agent_id"] == "researcher"
    assert trace["attribution"] == "agent:browser"


def test_an_empty_inner_agent_call_does_not_erase_the_outer_attribution(
        tracker):
    """Bad input rule: a falsy inner id sets nothing and leaves the outer
    context intact — it must not blank out a correct attribution."""
    router = _router()
    with agent_context.agent_call("buddy"):
        with agent_context.agent_call(""):
            router.complete("still buddy's work")
    assert _disk_traces()[0]["attribution"] == "agent:buddy"


# ---------------------------------------------------------------------------
# The persisted legacy bucket (W2's precondition)
# ---------------------------------------------------------------------------

def test_legacy_agent_bucket_is_renamed_once_and_idempotently(tmp_path,
                                                              monkeypatch):
    from arail import costs as costs_mod
    path = tmp_path / "costs.json"
    path.write_text(json.dumps({
        "calls_by_source": {"agent": 412, "ui": 9},
        "total_calls": 421,
    }))
    monkeypatch.setattr(costs_mod, "DATA_DIR", tmp_path, raising=False)

    def _fresh():
        t = costs_mod.CostTracker.__new__(costs_mod.CostTracker)
        t._data_path = path
        t._lock = threading.Lock()
        t.calls_by_source = {}
        t.history = []
        t._load()
        return t

    first = _fresh()
    assert "agent" not in first.calls_by_source, first.calls_by_source
    assert first.calls_by_source["agent:pre-p1-legacy"] == 412
    assert first.calls_by_source["ui"] == 9

    # Idempotent across a second load of the already-migrated file.
    first._save()
    second = _fresh()
    assert second.calls_by_source["agent:pre-p1-legacy"] == 412
    assert "agent" not in second.calls_by_source


def test_migration_merges_rather_than_clobbers_an_existing_legacy_key(
        tmp_path, monkeypatch):
    """A lab that already has both keys (upgraded, downgraded, upgraded
    again) must not lose counts."""
    from arail import costs as costs_mod
    path = tmp_path / "costs.json"
    path.write_text(json.dumps({
        "calls_by_source": {"agent": 5, "agent:pre-p1-legacy": 7},
    }))
    t = costs_mod.CostTracker.__new__(costs_mod.CostTracker)
    t._data_path = path
    t._lock = threading.Lock()
    t.calls_by_source = {}
    t.history = []
    t._load()
    assert t.calls_by_source["agent:pre-p1-legacy"] == 12
    assert "agent" not in t.calls_by_source
