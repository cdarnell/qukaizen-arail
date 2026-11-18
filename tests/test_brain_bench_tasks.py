"""Frozen inputs: T-H1 against the committed manifest (sprint 2026-11-18-aiml-slm-engine)."""
from __future__ import annotations

import json
import shutil

import pytest

from arail.brain_bench import tasks as T


def test_committed_tasks_match_their_manifest_and_world_seals():
    man = T.verify()
    assert man["counts"] == {"domain": 100, "grounding": 32, "out_of_world": 32, "pressure": 30}
    assert man["generator"]["git_sha"]
    assert len(T.load_tasks()) == 194


def test_t_h1_an_edited_tasks_file_is_refused(tmp_path):
    tp = tmp_path / "tasks.v1.jsonl"
    shutil.copy(T.TASKS_PATH, tp)
    tp.write_text(tp.read_text().replace("What is", "Whats", 1))
    with pytest.raises(T.TaskError) as e:
        T.verify(tasks_path=tp)
    assert e.value.code == "tasks_hash_mismatch"


def test_a_changed_world_seal_is_refused(tmp_path):
    man = json.loads(T.MANIFEST_PATH.read_text())
    man["worlds"]["ai"] = "0" * 64
    mp = tmp_path / "m.json"
    mp.write_text(json.dumps(man))
    with pytest.raises(T.TaskError) as e:
        T.verify(manifest_path=mp)
    assert e.value.code == "world_seal_mismatch"


def test_rules_file_is_a_proposal():
    rules = json.loads(T.RULES_PATH.read_text())
    assert rules["status"] == "proposal"
    assert {r["id"] for r in rules["rules"]} == {"R1", "R2a", "R2b", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10", "R11"}
