"""Study surface tests — the tutor team's quiz bench.

Covers the three things that would actually hurt if they broke:

1. **Every-tier availability.** The bench is for whoever the lab is shared
   with; a minimalist user who loses the tab loses the feature entirely.
2. **The coach guard.** ``/api/study/*`` duck-types agent singletons. A
   non-coach agent (Buddy, SRE) reaching ``session()``/``record()`` would be
   an unbounded call into arbitrary agent code from an HTTP route.
3. **Honest empties.** No coaches, or nothing due, must render as a plain
   answer — never a 500, and never a fabricated question.

The fixtures build a throwaway lab so these never depend on whatever is
mounted on the developer's machine.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _client(monkeypatch, tmp_path, lab_tier: str = "minimalist") -> TestClient:
    monkeypatch.setenv("LAB_TIER", lab_tier)
    monkeypatch.setenv("ARAIL_PASSWORD", "test-passphrase-not-real")
    monkeypatch.chdir(tmp_path)
    import arail.portal.app as _app_mod
    return TestClient(_app_mod.app)


def _seed_coach(pkb_root: Path, agent_id: str = "fixture_coach",
                cards: list[dict] | None = None) -> None:
    """Write a minimal coach folder that satisfies the drill protocol.

    Deliberately NOT importing the real tutor_kit: this asserts the surface's
    published contract (session/record/cards + a ``deck:`` in frontmatter),
    not one particular implementation of it.
    """
    folder = pkb_root / "agents" / agent_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "AGENT.md").write_text(
        "---\n"
        f"name: Fixture Coach\nemoji: 🧪\ndeck: fixture\nworld: fixture-world\n"
        "---\nA coach used only by tests.\n",
        encoding="utf-8")
    (folder / f"{agent_id}.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        f"CARDS = {json.dumps(cards if cards is not None else [{'id': 'c1', 'prompt': 'Q?', 'answer': 'A', 'skill': 'solve', 'steps': []}])}\n"
        "class _C:\n"
        "    def __init__(self):\n"
        "        self.status = 'idle'\n"
        "        self._state = {'boxes': {}}\n"
        "    def cards(self):\n"
        "        return CARDS\n"
        "    def _load_state(self):\n"
        "        return self._state\n"
        "    def session(self):\n"
        "        if not CARDS:\n"
        "            return None\n"
        "        c = dict(CARDS[0])\n"
        "        c['card_id'] = c.pop('id')\n"
        "        c['explanation'] = None\n"
        "        c.setdefault('choices', [])\n"
        "        c.setdefault('correct_choice', '')\n"
        "        return c\n"
        "    def record(self, card_id, correct):\n"
        "        self._state['boxes'][card_id] = {'box': 4 if correct else 1}\n"
        f"{agent_id} = _C()\n",
        encoding="utf-8")


@pytest.fixture()
def lab(tmp_path, monkeypatch):
    """A throwaway lab root with an empty PKB, plus a cleared agent cache.

    Patches ``agents.loader._pkb_root`` rather than setting ``LAB_PKM``:
    ``arail.config.PKB_ROOT`` is a module constant resolved at import and
    never rebound in-process (a load-bearing invariant — one PKB root per
    process, see the ``pkb_index`` module docstring). An env override
    therefore only takes effect for whichever test imports the app first,
    which made these tests pass alone and fail in the full suite.
    """
    pkb = tmp_path / "lab" / "pkb"
    (pkb / "agents").mkdir(parents=True)
    from arail.agents import loader
    monkeypatch.setattr(loader, "_pkb_root", lambda: pkb)
    loader._CACHE.clear()
    yield pkb
    loader._CACHE.clear()


# ---------------------------------------------------------------------------
# Tier availability
# ---------------------------------------------------------------------------

def test_study_in_both_tier_surfaces():
    """Canary: the bench is every-tier on purpose. A future PR that makes it
    maximus-only takes the feature away from exactly the person it was built
    for — a student on a shared lab."""
    from arail.portal.app import _TIER_SURFACES
    for tier in ("minimalist", "maximus"):
        assert "study" in _TIER_SURFACES[tier], (
            f"'study' missing from _TIER_SURFACES[{tier!r}] — the study bench "
            "is every-tier by design.")


@pytest.mark.parametrize("tier", ["minimalist", "maximus"])
def test_study_page_renders_and_links_in_nav(monkeypatch, tmp_path, tier):
    client = _client(monkeypatch, tmp_path, lab_tier=tier)
    r = client.get("/study")
    assert r.status_code == 200, r.status_code
    assert 'href="/study"' in r.text, f"Study link missing from {tier} nav."
    assert 'id="team-row"' in r.text


# ---------------------------------------------------------------------------
# The coach guard
# ---------------------------------------------------------------------------

def test_non_coach_agent_is_rejected(monkeypatch, tmp_path, lab):
    """An agent that is not a coach must 404, not be invoked.

    This is the security-shaped case: without the protocol check, any agent
    id in a query string would reach an arbitrary singleton's attributes.
    """
    folder = lab / "agents" / "not_a_coach"
    folder.mkdir(parents=True)
    (folder / "AGENT.md").write_text("---\nname: Plain\n---\nNo deck.\n", encoding="utf-8")
    (folder / "not_a_coach.py").write_text(
        "class _A:\n    status='idle'\nnot_a_coach=_A()\n", encoding="utf-8")

    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/study/next", params={"agent": "not_a_coach"})
    assert r.status_code == 404
    assert r.json()["error"] == "not_a_coach"


@pytest.mark.parametrize("agent", ["", "../etc/passwd", "a/b", "nope"])
def test_bogus_agent_ids_are_rejected(monkeypatch, tmp_path, lab, agent):
    """Traversal-shaped and unknown ids get the same flat 404."""
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/study/next", params={"agent": agent})
    assert r.status_code == 404


def test_grade_rejects_cross_site(monkeypatch, tmp_path, lab):
    """The one write on this surface carries the portal's CSRF envelope."""
    _seed_coach(lab)
    client = _client(monkeypatch, tmp_path)
    r = client.post("/api/study/grade",
                    json={"agent": "fixture_coach", "card_id": "c1", "correct": True},
                    headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert r.json()["error"] == "cross_site"


def test_grade_requires_card_id(monkeypatch, tmp_path, lab):
    _seed_coach(lab)
    client = _client(monkeypatch, tmp_path)
    r = client.post("/api/study/grade", json={"agent": "fixture_coach"})
    assert r.status_code == 400
    assert r.json()["error"] == "card_id_required"


# ---------------------------------------------------------------------------
# The happy path + honest empties
# ---------------------------------------------------------------------------

def test_team_lists_only_coaches(monkeypatch, tmp_path, lab):
    """A coach appears purely by existing on disk — that is what makes
    'adding a subject is config, not code' true."""
    _seed_coach(lab)
    folder = lab / "agents" / "plain_agent"
    folder.mkdir(parents=True)
    (folder / "AGENT.md").write_text("---\nname: Plain\n---\nNo deck.\n", encoding="utf-8")
    (folder / "plain_agent.py").write_text(
        "class _A:\n    status='idle'\nplain_agent=_A()\n", encoding="utf-8")

    client = _client(monkeypatch, tmp_path)
    team = client.get("/api/study/team").json()["team"]
    assert [c["agent"] for c in team] == ["fixture_coach"]
    assert team[0]["progress"] == {"total": 1, "seen": 0, "mastered": 0}


def test_next_and_grade_round_trip(monkeypatch, tmp_path, lab):
    _seed_coach(lab)
    client = _client(monkeypatch, tmp_path)

    got = client.get("/api/study/next", params={"agent": "fixture_coach"}).json()
    assert got["card"]["card_id"] == "c1"
    assert got["card"]["answer"] == "A"

    graded = client.post("/api/study/grade",
                         json={"agent": "fixture_coach", "card_id": "c1", "correct": True})
    assert graded.status_code == 200
    body = graded.json()
    assert body["ok"] is True
    # box 4 counts as mastered — the progress the UI draws its bar from.
    assert body["progress"]["mastered"] == 1


def test_closed_choice_cards_pass_their_choices_through(monkeypatch, tmp_path, lab):
    """Predict cards are graded by the page against `correct_choice`, so both
    fields have to survive the trip. Auto-grading is only defensible because
    the answer space is finite and authored — if `choices` silently dropped,
    the card would degrade into an ungraded show-the-answer prompt."""
    _seed_coach(lab, cards=[{
        "id": "p1", "skill": "predict", "prompt": "6y = 12 — multiply or divide?",
        "answer": "Divide.", "steps": [],
        "choices": ["Divide", "Multiply"], "correct_choice": "Divide",
    }])
    client = _client(monkeypatch, tmp_path)
    card = client.get("/api/study/next", params={"agent": "fixture_coach"}).json()["card"]

    assert card["choices"] == ["Divide", "Multiply"]
    assert card["correct_choice"] == "Divide"
    assert card["correct_choice"] in card["choices"]


def test_ordinary_cards_carry_no_choices(monkeypatch, tmp_path, lab):
    """The absence is load-bearing: a free-text card must fall back to
    self-grading, because auto-grading an answer about a novel's theme would
    teach the wrong lesson."""
    _seed_coach(lab)
    client = _client(monkeypatch, tmp_path)
    card = client.get("/api/study/next", params={"agent": "fixture_coach"}).json()["card"]
    assert card["choices"] == []
    assert not card["correct_choice"]


def test_empty_team_is_a_200_not_an_error(monkeypatch, tmp_path, lab):
    """No coaches yet is a legitimate state (a fresh clone), and the page
    renders a how-to for it. A 500 here would look like a broken lab."""
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/study/team")
    assert r.status_code == 200
    assert r.json() == {"team": []}


def test_nothing_due_returns_null_card_not_a_fabrication(monkeypatch, tmp_path, lab):
    """The honesty case: a coach with an exhausted schedule returns card=null.
    Inventing a question to fill the gap is the one thing this surface must
    never do."""
    _seed_coach(lab, cards=[])
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/study/next", params={"agent": "fixture_coach"})
    assert r.status_code == 200
    assert r.json()["card"] is None
