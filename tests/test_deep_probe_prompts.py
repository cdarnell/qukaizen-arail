"""The probe's prompt set is the day-1 set, byte for byte (Q16).

C3 and Appendix C of sprints/2026-10-29-arail-deep-honest/ARCHITECTURE.md. The
hash is the proof that the shipped probe is the probe that produced DAY1.json.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from arail import deep_probe_prompts as pp

DAY1 = Path(__file__).resolve().parents[1] / "sprints" / "2026-10-29-arail-deep-honest" / "DAY1.json"


def test_q16_the_prompt_set_hash_is_pinned():
    assert pp.PROMPT_SET_SHA256 == "b5bcb562d908f5d8c843cfe0b0461bfdeb39933339b45e332e91120f7918641e"
    again = hashlib.sha256(json.dumps(pp.PROMPTS, ensure_ascii=False, separators=(",", ":"))
                           .encode("utf-8")).hexdigest()
    assert again == pp.PROMPT_SET_SHA256, "the constant must be the hash of the actual set"


def test_q16_the_hash_matches_what_day1_recorded():
    day1 = json.loads(DAY1.read_text(encoding="utf-8"))
    assert day1["method"]["prompt_set_sha256"] == pp.PROMPT_SET_SHA256


def test_the_set_is_five_prompts_in_the_day1_order():
    assert [k for k, _ in pp.PROMPTS] == ["short", "long", "short-repeat", "long-shared-prefix", "short-2"]
    assert len(pp.LAB) == 6680, "the lab-context prefix is 6,680 characters"


def test_the_repeat_is_word_for_word_and_the_long_prompts_share_a_prefix():
    by = dict(pp.PROMPTS)
    assert by["short-repeat"] == by["short"]
    assert by["long"].startswith(pp.LAB) and by["long-shared-prefix"].startswith(pp.LAB)
    assert by["long"] != by["long-shared-prefix"], "same prefix, different question"
    assert by["short-2"] != by["short"]
