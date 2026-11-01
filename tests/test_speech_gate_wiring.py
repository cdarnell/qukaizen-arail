"""F9 wiring / operator decision (a), SPRINT.md 2026-09-20-buddy-front-
and-center: pin every ``speech_gate`` call site with a test. Deleting the
gate from any one of these seven funnels must turn a passing test red —
before this file, none of them were pinned (REVIEW.md must-fix #8), and
QA-BLIND-2 alone cannot be relied on to hit the exact five (now seven)
sites blind.

Seven sites, one test pair (held / not held) each:
  1. Buddy._emit           -- the watcher/suggester funnel
  2. Buddy.start()          -- the boot notice (widened per operator decision (a))
  3. Buddy.dream()          -- the dream announcement (widened per (a))
  4. Librarian._emit        -- the shared growth/scout/horizon-watch funnel
  5. Presence._tick()       -- the profile-transition line
  6. DebtAdvisor.tick()      -- the "produced a new finding" announcement
  7. ConsolidationAnalyzer.tick() -- same, plus the threshold-crossed line
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from arail import scheduler


@pytest.fixture(autouse=True)
def _clean_halt():
    scheduler._reset_halt_for_tests()
    yield
    scheduler._reset_halt_for_tests()


# ---------------------------------------------------------------------------
# 1. Buddy._emit -- the watcher/suggester funnel
# ---------------------------------------------------------------------------

class _FakeBuddyHost:
    def __init__(self):
        self.events: List[tuple] = []

    def emit(self, *a, **kw):
        self.events.append((a, kw))

    def update_workflow(self, *a, **kw):
        pass

    def get_current_goal(self):
        return None

    def llm_complete(self, *a, **kw):
        return ""


def _buddy_observation(buddy_mod):
    return buddy_mod.Observation(watcher="w", severity="info", fact="a fact")


def test_buddy_emit_gated_when_held(monkeypatch):
    from arail.agents import _builtin_buddy as buddy_mod

    monkeypatch.setattr(buddy_mod, "_voice", lambda fact: fact)
    host = _FakeBuddyHost()
    agent = buddy_mod.BuddyAgent()
    agent._host = host
    scheduler.halt_all_jobs()
    agent._emit(_buddy_observation(buddy_mod), kind="suggest")
    assert host.events == []


def test_buddy_emit_speaks_when_not_held(monkeypatch):
    from arail.agents import _builtin_buddy as buddy_mod

    monkeypatch.setattr(buddy_mod, "_voice", lambda fact: fact)
    host = _FakeBuddyHost()
    agent = buddy_mod.BuddyAgent()
    agent._host = host
    agent._emit(_buddy_observation(buddy_mod), kind="suggest")
    assert len(host.events) == 1


# ---------------------------------------------------------------------------
# 2. Buddy.start() -- the boot notice (operator decision (a))
# ---------------------------------------------------------------------------

def _run_buddy_start(monkeypatch, tmp_path, host) -> None:
    from arail.agents import _builtin_buddy as buddy_mod
    monkeypatch.setattr(buddy_mod, "_state_file", lambda: tmp_path / "state.json")

    async def _scenario():
        agent = buddy_mod.BuddyAgent()
        agent._host = host
        agent.start()
        await asyncio.sleep(0)
        if agent._task:
            agent._task.cancel()
            try:
                await agent._task
            except BaseException:  # noqa: BLE001 - teardown only
                pass

    asyncio.run(_scenario())


def test_buddy_boot_notice_gated_when_held(monkeypatch, tmp_path):
    host = _FakeBuddyHost()
    scheduler.halt_all_jobs()
    _run_buddy_start(monkeypatch, tmp_path, host)
    assert not any("is online" in str(a) for a, kw in host.events)


def test_buddy_boot_notice_speaks_when_not_held(monkeypatch, tmp_path):
    host = _FakeBuddyHost()
    _run_buddy_start(monkeypatch, tmp_path, host)
    assert any("is online" in str(a) for a, kw in host.events)


# ---------------------------------------------------------------------------
# 3. Buddy.dream() -- the dream announcement (operator decision (a))
# ---------------------------------------------------------------------------

def _run_buddy_dream(monkeypatch, tmp_path) -> List[Dict[str, Any]]:
    from arail.agents import _builtin_buddy as buddy_mod
    import arail.pkb as pkb_mod
    from arail.activity import activity_log as real_activity_log

    monkeypatch.setattr(buddy_mod, "_state_file", lambda: tmp_path / "state.json")
    monkeypatch.setattr(buddy_mod, "_collect_today_buddy_activity", lambda today: [])
    monkeypatch.setattr(buddy_mod, "_load_yesterday_dream", lambda d, t: "")
    monkeypatch.setattr(buddy_mod, "_build_dream_prompt", lambda a, y: "prompt")

    async def _fake_call_model_for_dream(prompt):
        return "a reflection"
    monkeypatch.setattr(buddy_mod, "_call_model_for_dream", _fake_call_model_for_dream)
    # dream() imports write_buddy_dream locally (``from arail.pkb import
    # write_buddy_dream as _write_buddy_dream``) on every call, so the
    # source attribute on arail.pkb has to be patched, not a module-level
    # name on _builtin_buddy -- there isn't one.
    monkeypatch.setattr(pkb_mod, "write_buddy_dream",
                        lambda today, body, pkb_root=None: None)
    monkeypatch.setattr(buddy_mod._host, "get_pkb_root", lambda: None)

    events: List[Dict[str, Any]] = []
    monkeypatch.setattr(
        real_activity_log, "emit",
        lambda source, message, level="info", data=None: events.append(
            {"source": source, "message": message, "data": data}
        ),
    )

    async def _scenario():
        agent = buddy_mod.BuddyAgent()
        await agent.dream()

    asyncio.run(_scenario())
    return events


def test_buddy_dream_announcement_gated_when_held(monkeypatch, tmp_path):
    scheduler.halt_all_jobs()
    events = _run_buddy_dream(monkeypatch, tmp_path)
    assert not any("dreamed" in e["message"] for e in events)


def test_buddy_dream_announcement_speaks_when_not_held(monkeypatch, tmp_path):
    events = _run_buddy_dream(monkeypatch, tmp_path)
    assert any("dreamed" in e["message"] for e in events)


# ---------------------------------------------------------------------------
# 4. Librarian._emit -- the shared funnel
# ---------------------------------------------------------------------------

def test_librarian_emit_gated_when_held(monkeypatch):
    from arail.agents import _builtin_librarian as lib_mod
    from arail.activity import activity_log as real_activity_log

    # _emit imports activity_log locally from arail.activity on every
    # call -- there is no module-level name on _builtin_librarian itself.
    events = []
    monkeypatch.setattr(real_activity_log, "emit",
                        lambda *a, **kw: events.append((a, kw)))
    scheduler.halt_all_jobs()
    lib_mod.LibrarianAgent._emit("a message", "info")
    assert events == []


def test_librarian_emit_speaks_when_not_held(monkeypatch):
    from arail.agents import _builtin_librarian as lib_mod
    from arail.activity import activity_log as real_activity_log

    events = []
    monkeypatch.setattr(real_activity_log, "emit",
                        lambda *a, **kw: events.append((a, kw)))
    lib_mod.LibrarianAgent._emit("a message", "info")
    assert len(events) == 1


# ---------------------------------------------------------------------------
# 5. Presence._tick() -- the profile-transition line
# ---------------------------------------------------------------------------

def _run_presence_tick(monkeypatch) -> List[tuple]:
    import arail.runtime_profile as rp_mod
    from arail.activity import activity_log as real_activity_log
    from arail.agents import _builtin_presence as presence_mod

    transitions = iter([("interactive", "presence"), ("balanced", "default")])
    monkeypatch.setattr(rp_mod, "resolve", lambda: next(transitions))
    monkeypatch.setattr(rp_mod, "snapshot", lambda: {})
    events: List[tuple] = []
    monkeypatch.setattr(real_activity_log, "emit",
                        lambda *a, **kw: events.append((a, kw)))

    agent = presence_mod.PresenceAgent()
    agent._tick()  # baseline -- never emits on the first tick
    agent._tick()  # a real transition -- would emit if not gated
    return events


def test_presence_tick_gated_when_held(monkeypatch):
    scheduler.halt_all_jobs()
    events = _run_presence_tick(monkeypatch)
    assert events == []


def test_presence_tick_speaks_when_not_held(monkeypatch):
    events = _run_presence_tick(monkeypatch)
    assert len(events) == 1


# ---------------------------------------------------------------------------
# 6/7. DebtAdvisor.tick() / ConsolidationAnalyzer.tick() -- the "produced a
# new finding" announcements. Reuses tests/test_debt_finance_agents.py's
# proven fixture shape (FakeHost + a mounted world_bundle) rather than
# re-deriving a second harness.
# ---------------------------------------------------------------------------

_GOOD_DISCLAIMER = (
    "# Disclaimer\n\n"
    "These agents are not licensed financial advisors.\n"
)
_TERMS = {
    "version": 1,
    "terms": [
        {"slug": "credit-union", "term": "Credit Union",
         "category": "institutions", "short": "x", "definition": "x",
         "related": [], "source": "https://www.ncua.gov/consumers/consumer-resources"},
        {"slug": "penfed-credit-union", "term": "PenFed Credit Union",
         "category": "institutions", "institution_type": "credit-union",
         "short": "x", "definition": "x", "related": [],
         "source": "https://www.penfed.org/personal-loans",
         "verification_source": "https://mapping.ncua.gov/ResearchCreditUnion",
         "verified_as_of": "2026-07-27"},
        {"slug": "balance-transfer", "term": "Balance Transfer",
         "category": "strategies", "short": "x", "definition": "x",
         "related": [], "source": "https://example.gov/balance-transfer"},
    ],
}
_BALANCES = {
    "debts": [
        {"id": "card-1", "kind": "credit-card", "balance": 1000.0, "apr": 20.0},
        {"id": "card-2", "kind": "credit-card", "balance": 3000.0, "apr": 10.0},
    ],
    "candidate_scenarios": [],
}


class _FakeFinanceHost:
    def __init__(self, pkb_root: Path, data_dir: Path):
        self._pkb_root = pkb_root
        self._data_dir = data_dir
        self.events: List[Dict[str, Any]] = []

    def emit(self, source, message, level="info", data=None):
        self.events.append({"source": source, "message": message,
                             "level": level, "data": data})

    def get_pkb_root(self) -> Optional[Path]:
        return self._pkb_root

    def get_data_dir(self) -> Optional[Path]:
        return self._data_dir

    def llm_complete(self, prompt, max_tokens=120, temperature=0.4) -> str:
        return ""  # deterministic fallback framing sentence


@pytest.fixture()
def finance_world_bundle(tmp_path):
    bundle = tmp_path / "bundle"
    (bundle / "compliance").mkdir(parents=True)
    (bundle / "compliance" / "DISCLAIMER.md").write_text(_GOOD_DISCLAIMER)
    (bundle / "terms.json").write_text(json.dumps(_TERMS))
    return bundle


@pytest.fixture()
def finance_host(tmp_path, finance_world_bundle):
    pkb_root = tmp_path / "pkb"
    pkb_root.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return _FakeFinanceHost(pkb_root, data_dir)


def test_debt_advisor_finding_announcement_gated_when_held(monkeypatch, finance_host, finance_world_bundle):
    from arail.agents import _builtin_debt_advisor as mod
    monkeypatch.setattr(mod, "_host", finance_host)
    monkeypatch.setattr(mod, "find_mounted_bundle_dir", lambda: finance_world_bundle)
    scheduler.halt_all_jobs()
    mod.DebtAdvisorAgent().tick()
    assert not any("produced a new finding" in e["message"] for e in finance_host.events)


def test_debt_advisor_finding_announcement_speaks_when_not_held(monkeypatch, finance_host, finance_world_bundle):
    from arail.agents import _builtin_debt_advisor as mod
    monkeypatch.setattr(mod, "_host", finance_host)
    monkeypatch.setattr(mod, "find_mounted_bundle_dir", lambda: finance_world_bundle)
    mod.DebtAdvisorAgent().tick()
    assert any("produced a new finding" in e["message"] for e in finance_host.events)


def test_consolidation_analyzer_finding_announcement_gated_when_held(monkeypatch, finance_host, finance_world_bundle):
    from arail.agents import _builtin_consolidation_analyzer as mod
    monkeypatch.setattr(mod, "_host", finance_host)
    monkeypatch.setattr(mod, "find_mounted_bundle_dir", lambda: finance_world_bundle)
    balances_dir = finance_host.get_data_dir() / "user-import" / "debt-finance"
    balances_dir.mkdir(parents=True, exist_ok=True)
    (balances_dir / "balances.json").write_text(json.dumps(_BALANCES))
    scheduler.halt_all_jobs()
    mod.ConsolidationAnalyzerAgent().tick()
    assert not any("produced a new finding" in e["message"] for e in finance_host.events)


def test_consolidation_analyzer_finding_announcement_speaks_when_not_held(monkeypatch, finance_host, finance_world_bundle):
    from arail.agents import _builtin_consolidation_analyzer as mod
    monkeypatch.setattr(mod, "_host", finance_host)
    monkeypatch.setattr(mod, "find_mounted_bundle_dir", lambda: finance_world_bundle)
    balances_dir = finance_host.get_data_dir() / "user-import" / "debt-finance"
    balances_dir.mkdir(parents=True, exist_ok=True)
    (balances_dir / "balances.json").write_text(json.dumps(_BALANCES))
    mod.ConsolidationAnalyzerAgent().tick()
    assert any("produced a new finding" in e["message"] for e in finance_host.events)
