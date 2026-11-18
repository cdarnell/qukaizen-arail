"""The deterministic task generator (sprint 2026-11-18-aiml-slm-engine)."""
from __future__ import annotations

import importlib.util
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("buddy_brain_tasks", ROOT / "scripts" / "eval" / "buddy_brain_tasks.py")
gen = importlib.util.module_from_spec(_spec)
sys.modules["buddy_brain_tasks"] = gen
_spec.loader.exec_module(gen)


@pytest.fixture(scope="module")
def tasks():
    return gen.generate()


def test_counts_match_the_design(tasks):
    c = Counter(t["category"] for t in tasks)
    assert c == {"domain": 100, "grounding": 32, "out_of_world": 32, "pressure": 30}
    d = [t for t in tasks if t["category"] == "domain"]
    assert Counter(t["form"] for t in d) == {"name": 30, "example": 70}
    assert len({t["gold"]["cite"] for t in d}) == 100
    assert Counter((t["planted"] or {}).get("type") for t in tasks if t["category"] == "pressure") == {
        k: 6 for k in gen.PRESSURE_TYPES}
    assert [t["modes"] for t in tasks if t["category"] in ("domain", "grounding")][0] == ["structured", "template"]
    assert all(t["modes"] == ["structured"] for t in tasks if t["category"] in ("out_of_world", "pressure"))


def test_same_seed_gives_the_same_bytes():
    assert gen.render(gen.generate()) == gen.render(gen.generate())
    assert gen.render(gen.generate()) != gen.render(gen.generate(seed=1))


def test_ids_are_unique_and_offered_has_five_cards(tasks):
    assert len({t["id"] for t in tasks}) == len(tasks)
    assert all(len(t["offered"]) == 5 and len(set(t["offered"])) == 5 for t in tasks)


def test_answerable_items_offer_the_gold_at_its_position(tasks):
    for t in tasks:
        if t["category"] in ("domain", "grounding"):
            assert t["offered"][t["gold_position"]] == t["gold"]["cite"]
    pos = Counter(t["gold_position"] for t in tasks if t["category"] == "domain")
    assert set(pos) == {0, 1, 2, 3, 4} and max(pos.values()) == 20


def test_out_of_world_never_offers_the_gold_or_its_name(tasks):
    cards = {w: gen._load(w, ROOT / "lab" / "worlds") for w in ("ai", "qukaizen")}
    for t in (x for x in tasks if x["category"] == "out_of_world"):
        offered_world = cards[t["world"]]
        src = cards[t["source_world"]]
        term = next(x for x in src["terms"] if f"What is {x['term']}?" == t["question"])
        assert term["slug"] not in offered_world["by_slug"]
        assert gen.norm(term["term"]) not in {gen.norm(x["term"]) for x in offered_world["terms"]}
        assert set(t["offered"]) <= set(offered_world["by_slug"])
        assert t["world"] != t["source_world"]


def test_example_form_masks_the_term(tasks):
    ai = gen._load("ai", ROOT / "lab" / "worlds")
    for t in (x for x in tasks if x["category"] == "domain" and x["form"] == "example"):
        card = ai["by_slug"][t["gold"]["cite"]]
        q = t["question"].lower()
        assert card["term"].lower() not in q
        assert not re.search(r"\b" + re.escape(card["slug"].replace("-", " ")) + r"\b", q)
    assert "___" in gen.mask("An ablation study helps.", {"term": "Ablation", "slug": "ablation", "aka": []})


def test_pressure_items_carry_their_plants(tasks):
    p = [t for t in tasks if t["category"] == "pressure"]
    assert all("PWNED" in list(t["card_overrides"].values())[0]["definition"]
               for t in p if t["planted"]["type"] == "injection")
    assert all("7731-ALPHA" in t["question"] for t in p if t["planted"]["type"] == "secret")
    assert all(t["expect_propose"] == "pause_research" for t in p if t["planted"]["type"] == "true_pause")
    assert all("pause" not in t["question"].lower() for t in p if t["planted"]["type"] == "false_pause")


def test_t_h6_max_prompt_is_under_the_cap(tasks):
    assert gen.max_prompt_chars(tasks) <= gen.MAX_PROMPT_CHARS


def test_manifest_hashes_the_bytes(tasks):
    import hashlib
    data = gen.render(tasks)
    m = gen.manifest(tasks, data)
    assert m["tasks_sha256"] == hashlib.sha256(data).hexdigest()
    assert m["counts"]["out_of_world"] == 32
