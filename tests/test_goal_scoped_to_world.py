"""A research goal now belongs to the World it was set under.

Before this fix, goals and experiments were entirely global: mounting a
different World left whatever goal was previously running untouched, and
its experiments kept accumulating and rendering as if they belonged to
whatever goal happened to be current. Observed live: a "find the best
rates on loans to consolidate my debt" goal, set moments after mounting
the Debt Finance World, sharing an Autoresearch page with experiments
from a months-old "make aeroLLM the most efficient inference engine"
goal that had run under the AI World.

#166 fixed the DISPLAY half (research.html now scopes to goal.experiments,
which was always correctly populated). This fixes the DATA half: the goal
itself must not silently keep being "current" after the World it was set
under is no longer mounted — the same "the lab reflects the mounted
World" rule #163 already applies to the Compiled-KB gate.

Two halves, tested separately:
  1. goals.py: GoalStore.archive_if_world_mismatch() — the reconciliation
     itself, and set_goal() stamping a `world` field.
  2. world_mount.py: mount()/swap()/unmount() call it at the right
     points, AND — the guard that matters most — never touch the live
     goal_store when a caller passes an explicit, non-default data_dir
     (every test in this file that exercises world_mount would otherwise
     silently mutate whatever real goal happens to be sitting in this
     machine's actual lab/data/goals/current.json).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from arail import goals
from arail.goals import GoalStore


def _configure_goal_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(goals, "GOALS_DIR", tmp_path / "goals")
    monkeypatch.setattr(goals, "CURRENT_FILE", goals.GOALS_DIR / "current.json")
    monkeypatch.setattr(goals, "PREVIEW_FILE", goals.GOALS_DIR / "preview.json")
    monkeypatch.setattr(goals, "HISTORY_DIR", goals.GOALS_DIR / "history")


def _minimal_parsed_goal(text: str) -> dict:
    return {"goal": text, "domain": "general", "sub_objectives": []}


# ── goals.py: set_goal() stamps world ────────────────────────────────────

class TestSetGoalStampsWorld:
    def test_stamps_the_currently_mounted_world(self, tmp_path, monkeypatch):
        _configure_goal_paths(tmp_path, monkeypatch)
        monkeypatch.setattr(goals, "_mounted_world", lambda: "debt-finance")

        store = GoalStore()
        record = store.set_goal(_minimal_parsed_goal("consolidate my debt"))

        assert record["world"] == "debt-finance"
        assert store.get_current()["world"] == "debt-finance"

    def test_stamps_none_when_no_world_is_mounted(self, tmp_path, monkeypatch):
        _configure_goal_paths(tmp_path, monkeypatch)
        monkeypatch.setattr(goals, "_mounted_world", lambda: None)

        store = GoalStore()
        record = store.set_goal(_minimal_parsed_goal("anything"))

        assert record["world"] is None

    def test_mounted_world_lookup_never_raises(self, tmp_path, monkeypatch):
        """_mounted_world() must fail closed to None, not propagate — a
        goal must always be settable even if the mount sidecar is
        unreadable or world_mount can't be imported for some reason."""
        _configure_goal_paths(tmp_path, monkeypatch)

        def _boom():
            raise RuntimeError("mount sidecar is corrupt")

        import arail.world_mount as wm
        monkeypatch.setattr(wm, "current_mount", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

        store = GoalStore()
        record = store.set_goal(_minimal_parsed_goal("anything"))
        assert record["world"] is None


# ── goals.py: archive_if_world_mismatch() ────────────────────────────────

class TestArchiveIfWorldMismatch:
    def test_no_current_goal_is_a_noop(self, tmp_path, monkeypatch):
        _configure_goal_paths(tmp_path, monkeypatch)
        store = GoalStore()
        assert store.archive_if_world_mismatch("ai") is False

    def test_matching_world_leaves_the_goal_running(self, tmp_path, monkeypatch):
        _configure_goal_paths(tmp_path, monkeypatch)
        monkeypatch.setattr(goals, "_mounted_world", lambda: "ai")
        store = GoalStore()
        record = store.set_goal(_minimal_parsed_goal("about AI"))

        archived = store.archive_if_world_mismatch("ai")

        assert archived is False
        current = store.get_current()
        assert current is not None
        assert current["id"] == record["id"]
        assert current["status"] == "active"

    def test_mismatched_world_archives_and_clears(self, tmp_path, monkeypatch):
        _configure_goal_paths(tmp_path, monkeypatch)
        monkeypatch.setattr(goals, "_mounted_world", lambda: "ai")
        store = GoalStore()
        record = store.set_goal(_minimal_parsed_goal("about AI"))

        archived = store.archive_if_world_mismatch("debt-finance")

        assert archived is True
        assert store.get_current() is None
        history = store.list_history()
        assert len(history) == 1
        assert history[0]["id"] == record["id"]
        assert history[0]["status"] == "archived"
        assert history[0]["world"] == "ai"  # the world it belonged to, untouched

    def test_unmounting_a_world_archives_its_goal(self, tmp_path, monkeypatch):
        """new_world=None is the unmount case — a goal set while a World
        was mounted must not keep running once nothing is mounted."""
        _configure_goal_paths(tmp_path, monkeypatch)
        monkeypatch.setattr(goals, "_mounted_world", lambda: "debt-finance")
        store = GoalStore()
        store.set_goal(_minimal_parsed_goal("consolidate my debt"))

        archived = store.archive_if_world_mismatch(None)

        assert archived is True
        assert store.get_current() is None

    def test_legacy_goal_with_no_world_key_is_treated_as_none(self, tmp_path, monkeypatch):
        """A goal set before this field existed has no `world` key at
        all. Must be treated as world=None — so it survives a switch to
        "no World mounted" but archives on switching TO a specific World."""
        _configure_goal_paths(tmp_path, monkeypatch)
        goals.GOALS_DIR.mkdir(parents=True, exist_ok=True)
        legacy = {
            "id": "legacy01", "goal_text": "old goal", "parsed": {},
            "status": "active", "experiments": [], "findings": [],
            "report": None, "progress": 0.0,
            # deliberately no "world" key
        }
        goals.CURRENT_FILE.write_text(json.dumps(legacy))
        store = GoalStore()

        # Switching to "no World mounted" — matches (None == None) — stays.
        assert store.archive_if_world_mismatch(None) is False
        assert store.get_current()["id"] == "legacy01"

        # Switching TO a specific World — mismatch — archives.
        assert store.archive_if_world_mismatch("ai") is True
        assert store.get_current() is None

    def test_run_state_is_cleared_so_nothing_auto_resumes_the_wrong_goal(self, tmp_path, monkeypatch):
        _configure_goal_paths(tmp_path, monkeypatch)
        monkeypatch.setattr(goals, "_mounted_world", lambda: "ai")
        store = GoalStore()
        store.set_goal(_minimal_parsed_goal("about AI"))
        goals.save_run_state({"status": "running", "progress": 0.3})
        assert goals.load_run_state() is not None

        store.archive_if_world_mismatch("debt-finance")

        assert goals.load_run_state() is None

    def test_does_not_restore_a_prior_goal_from_history(self, tmp_path, monkeypatch):
        """Deliberate: switching back to a World you'd previously
        researched starts fresh, not resurrected — see the docstring on
        archive_if_world_mismatch for why."""
        _configure_goal_paths(tmp_path, monkeypatch)
        monkeypatch.setattr(goals, "_mounted_world", lambda: "ai")
        store = GoalStore()
        store.set_goal(_minimal_parsed_goal("about AI, round one"))
        store.archive_if_world_mismatch("debt-finance")  # archives round one

        monkeypatch.setattr(goals, "_mounted_world", lambda: "debt-finance")
        store.set_goal(_minimal_parsed_goal("consolidate debt"))
        # Switch back to "ai" — must NOT resurrect "about AI, round one".
        store.archive_if_world_mismatch("ai")

        assert store.get_current() is None


# ── world_mount.py integration ────────────────────────────────────────────

class TestWorldMountCallsTheSwitchHook:
    """Verifies mount()/swap()/unmount() call _switch_goal_for_world at
    the right points, WITHOUT touching the real filesystem — this is
    about the call happening, not the goal-store mechanics (covered
    above). Mocks world_mount internals the same way the module's own
    docstring says a mount is structured (load+verify → stage → sweep →
    write pointer → ...), so this doesn't need a real sealed bundle."""

    def test_switch_goal_for_world_skips_a_non_default_data_dir(self, tmp_path, monkeypatch):
        """THE guard that matters most: every world_mount test that
        passes an explicit data_dir (the standard isolation pattern used
        throughout this test suite) must never reach through to the
        real, process-global goal_store. A regression here means test
        runs could silently archive whatever goal is sitting in this
        machine's actual lab/data/goals/current.json."""
        from arail import world_mount as wm

        store_cls = MagicMock()
        monkeypatch.setattr(wm, "_default_data_dir", lambda: tmp_path / "the-real-one")
        import arail.goals as goals_mod
        monkeypatch.setattr(goals_mod, "GoalStore", store_cls)

        # A DIFFERENT path than what _default_data_dir() returns.
        wm._switch_goal_for_world("ai", tmp_path / "an-isolated-test-dir")

        store_cls.assert_not_called()

    def test_switch_goal_for_world_fires_on_the_real_default_data_dir(self, tmp_path, monkeypatch):
        from arail import world_mount as wm

        monkeypatch.setattr(wm, "_default_data_dir", lambda: tmp_path)

        instance = MagicMock()
        instance.archive_if_world_mismatch.return_value = True
        store_cls = MagicMock(return_value=instance)
        import arail.goals as goals_mod
        monkeypatch.setattr(goals_mod, "GoalStore", store_cls)

        wm._switch_goal_for_world("ai", tmp_path)

        store_cls.assert_called_once()
        instance.archive_if_world_mismatch.assert_called_once_with("ai")

    def test_switch_goal_for_world_never_raises(self, tmp_path, monkeypatch):
        """A goal-store bookkeeping failure must not be able to fail a
        mount/swap/unmount that has otherwise succeeded."""
        from arail import world_mount as wm

        monkeypatch.setattr(wm, "_default_data_dir", lambda: tmp_path)
        import arail.goals as goals_mod
        monkeypatch.setattr(
            goals_mod, "GoalStore",
            MagicMock(side_effect=RuntimeError("goals dir is unreadable")),
        )

        wm._switch_goal_for_world("ai", tmp_path)  # must not raise

    def test_mount_calls_switch_goal_with_the_new_slug(self, monkeypatch, tmp_path):
        from arail import world_mount as wm

        calls = []
        monkeypatch.setattr(wm, "_switch_goal_for_world", lambda new_world, dd: calls.append((new_world, dd)))

        bundle = MagicMock(slug="ai", world="ai", bundle_version=1)
        seal = MagicMock(ok=True, computed_sha256="abc123")
        monkeypatch.setattr(wm, "load_bundle", lambda *a, **k: bundle)
        monkeypatch.setattr(wm, "verify_seal", lambda *a, **k: seal)
        monkeypatch.setattr(wm, "check_compat", lambda *a, **k: None)
        monkeypatch.setattr(wm, "check_categories", lambda *a, **k: None)
        monkeypatch.setattr(wm, "_stage_files", lambda *a, **k: tmp_path / "staged")
        monkeypatch.setattr(wm, "_sweep_other_worlds", lambda *a, **k: 0)
        monkeypatch.setattr(wm, "_index_staged", lambda *a, **k: {"ok": True})
        monkeypatch.setattr(wm, "_emit_index_status", lambda *a, **k: None)
        monkeypatch.setattr(wm, "_write_record", lambda *a, **k: None)
        monkeypatch.setattr(wm, "_adopt_into_catalog", lambda *a, **k: None)
        monkeypatch.setattr(wm, "_refresh_kb_surfaces", lambda *a, **k: None)
        monkeypatch.setattr(wm, "_resolve_and_write_capabilities", lambda *a, **k: None)
        monkeypatch.setattr(wm, "_resolve_and_write_model_hint", lambda *a, **k: None)

        wm.mount(tmp_path / "bundle", pkb_root=tmp_path / "pkb", data_dir=tmp_path / "data")

        assert calls == [("ai", tmp_path / "data")]

    def test_unmount_calls_switch_goal_with_none(self, monkeypatch, tmp_path):
        from arail import world_mount as wm

        calls = []
        monkeypatch.setattr(wm, "_switch_goal_for_world", lambda new_world, dd: calls.append((new_world, dd)))

        record = MagicMock(staged_dir=str(tmp_path / "staged"))
        monkeypatch.setattr(wm, "current_mount", lambda dd: record)
        monkeypatch.setattr(wm, "_remove_record", lambda *a, **k: None)
        monkeypatch.setattr(wm, "_remove_capabilities_sidecar", lambda *a, **k: None)
        monkeypatch.setattr(wm, "_remove_model_hint_sidecar", lambda *a, **k: None)

        wm.unmount(data_dir=tmp_path / "data", remove_staged=False)

        assert calls == [(None, tmp_path / "data")]

    def test_unmount_calls_switch_goal_even_without_remove_staged(self, monkeypatch, tmp_path):
        """The bug this specifically guards against: an earlier draft of
        this fix only reconciled goals inside the `if remove_staged:`
        branch (copying the KB-prune call's placement without noticing
        goal-switching needs to fire on EVERY unmount, not just the ones
        that also clean up staged files)."""
        from arail import world_mount as wm

        calls = []
        monkeypatch.setattr(wm, "_switch_goal_for_world", lambda new_world, dd: calls.append((new_world, dd)))

        record = MagicMock(staged_dir=str(tmp_path / "staged"))
        monkeypatch.setattr(wm, "current_mount", lambda dd: record)
        monkeypatch.setattr(wm, "_remove_record", lambda *a, **k: None)
        monkeypatch.setattr(wm, "_remove_capabilities_sidecar", lambda *a, **k: None)
        monkeypatch.setattr(wm, "_remove_model_hint_sidecar", lambda *a, **k: None)

        wm.unmount(data_dir=tmp_path / "data", remove_staged=False)

        assert len(calls) == 1


# ── end-to-end: the exact field scenario, through the real functions ────

class TestEndToEndFieldScenario:
    def test_switching_worlds_archives_the_old_goal_and_a_new_one_starts_clean(
        self, tmp_path, monkeypatch
    ):
        """Reproduces the operator's report end-to-end through the real
        GoalStore + the real archive_if_world_mismatch (not world_mount,
        which is covered separately above with mocks for its own
        internals) — set an AI-world goal, switch worlds, confirm it's
        gone from "current", confirm a fresh Debt Finance goal starts
        with zero linked experiments (which is what #166's display fix
        actually reads)."""
        _configure_goal_paths(tmp_path, monkeypatch)

        monkeypatch.setattr(goals, "_mounted_world", lambda: "ai")
        store = GoalStore()
        old = store.set_goal(_minimal_parsed_goal(
            "make aeroLLM the most efficient and performant inference engine"
        ))
        store.link_experiment("44e91e0a")
        store.link_experiment("eb8be84e")
        assert len(store.get_current()["experiments"]) == 2

        # The World switch — exactly what world_mount now calls.
        store.archive_if_world_mismatch("debt-finance")
        assert store.get_current() is None

        # A fresh goal under the new World.
        monkeypatch.setattr(goals, "_mounted_world", lambda: "debt-finance")
        new = store.set_goal(_minimal_parsed_goal(
            "find the best rates on loans to consolidate my debt"
        ))

        current = store.get_current()
        assert current["id"] == new["id"]
        assert current["world"] == "debt-finance"
        # The core of the original bug: a fresh goal must start with NO
        # borrowed experiments from the archived one.
        assert current["experiments"] == []
        assert old["id"] != new["id"]
