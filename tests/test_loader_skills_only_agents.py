"""researcher/curator/browser's AGENT.md is a skills-loadout sidecar for an
implementation wired directly at the top level — never a folder the
generic loader is meant to import from. Before this fix, load_one() logged
"Agent 'researcher' failed to load" (error level) for all three, on every
single boot, since AGENT.md was first seeded (2026-05-01 in the field) —
three months of false-error noise with nothing actually broken, because
the real Researcher (src/arail/agents/researcher.py, imported directly by
the portal) worked the whole time.

This pins two things: (1) a missing companion .py for these three IDs is
silent, not an activity-log error; (2) discover() — what the Skills tab's
/api/agents/loadouts reads — is completely unaffected, since these three
folders' AGENT.md is the whole point of agent_seed.py's design.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def _clear_loader_cache():
    from arail.agents import loader
    loader.clear_cache()
    yield
    loader.clear_cache()


def _agent_md(folder, agent_id: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "AGENT.md").write_text(
        f"---\nid: {agent_id}\nname: {agent_id.title()}\nskills: [a, b]\n---\n\n# {agent_id}\n"
    )


class TestSkillsOnlySet:
    def test_researcher_curator_browser_are_skills_only(self):
        from arail.agents.loader import _SKILLS_ONLY
        assert _SKILLS_ONLY == {"researcher", "curator", "browser"}

    def test_skills_only_agents_are_not_in_shipped(self):
        # Disjoint by construction: _SHIPPED agents get a builtin fallback
        # and a seeded .py; skills-only agents get neither, on purpose.
        from arail.agents.loader import _SHIPPED, _SKILLS_ONLY
        assert _SHIPPED.isdisjoint(_SKILLS_ONLY)


class TestLoadOneSkillsOnly:
    @pytest.mark.parametrize("agent_id", ["researcher", "curator", "browser"])
    def test_missing_py_returns_none_quietly(self, tmp_path, agent_id, monkeypatch):
        """The core regression: no companion .py, no error emitted."""
        from arail.agents import loader
        from arail.activity import activity_log

        _agent_md(tmp_path / "agents" / agent_id, agent_id)

        emitted: list[tuple[str, str]] = []
        monkeypatch.setattr(
            activity_log, "emit",
            lambda source, message, level="info", data=None: emitted.append((level, message)),
        )

        result = loader.load_one(agent_id, pkb_root=tmp_path)

        assert result is None
        errors = [msg for lvl, msg in emitted if lvl == "error"]
        assert not errors, f"{agent_id}: expected no error-level activity log entries, got {errors}"

    @pytest.mark.parametrize("agent_id", ["researcher", "curator", "browser"])
    def test_a_real_py_still_loads_normally(self, tmp_path, agent_id):
        """If a companion .py DOES exist (hand-added, or a future seed),
        it must load exactly like any other agent — the exclusion is
        specifically for the "no .py at all" case, not a blanket ban."""
        from arail.agents import loader

        folder = tmp_path / "agents" / agent_id
        _agent_md(folder, agent_id)
        (folder / f"{agent_id}.py").write_text(f"{agent_id} = object()\n")

        result = loader.load_one(agent_id, pkb_root=tmp_path)
        assert result is not None

    @pytest.mark.parametrize("agent_id", ["researcher", "curator", "browser"])
    def test_a_broken_py_still_logs_normally(self, tmp_path, agent_id, monkeypatch):
        """A .py that DOES exist but is broken is a real bug (someone
        edited it and broke it) — that must still surface, unlike the
        "never had one" case above."""
        from arail.agents import loader
        from arail.activity import activity_log

        folder = tmp_path / "agents" / agent_id
        _agent_md(folder, agent_id)
        (folder / f"{agent_id}.py").write_text("raise RuntimeError('broken on purpose')\n")

        emitted: list[tuple[str, str]] = []
        monkeypatch.setattr(
            activity_log, "emit",
            lambda source, message, level="info", data=None: emitted.append((level, message)),
        )

        result = loader.load_one(agent_id, pkb_root=tmp_path)

        assert result is None
        errors = [msg for lvl, msg in emitted if lvl == "error"]
        assert errors, f"{agent_id}: a genuinely broken .py must still be reported"


class TestLoadAllExcludesSkillsOnlyByDefault:
    def test_load_all_never_errors_on_a_fresh_seeded_lab(self, tmp_path, monkeypatch):
        """End-to-end shape of the original bug: seed the three AGENT.md
        files the way agent_seed.py does on first boot, call load_all()
        (what app.py's startup does), and assert no error-level entry
        appears for any of the three."""
        from arail.agents import loader
        from arail.activity import activity_log

        for agent_id in ("researcher", "curator", "browser"):
            _agent_md(tmp_path / "agents" / agent_id, agent_id)

        emitted: list[tuple[str, str]] = []
        monkeypatch.setattr(
            activity_log, "emit",
            lambda source, message, level="info", data=None: emitted.append((level, message)),
        )
        # load_all() also seeds every _SHIPPED agent — irrelevant to this
        # test's assertion (no error for the three skills-only ids) and
        # side-effect-safe against tmp_path.
        loader.load_all(pkb_root=tmp_path)

        for agent_id in ("researcher", "curator", "browser"):
            bad = [msg for lvl, msg in emitted
                   if lvl == "error" and agent_id in msg]
            assert not bad, f"unexpected error for {agent_id}: {bad}"


class TestDiscoverUnaffected:
    def test_discover_still_returns_all_three(self, tmp_path):
        """The Skills tab (/api/agents/loadouts) reads discover()'s
        frontmatter for every agent folder — that must be completely
        untouched by this fix, which only changes load_one()'s
        import-and-instantiate path."""
        from arail.agents.loader import discover

        for agent_id in ("researcher", "curator", "browser"):
            _agent_md(tmp_path / "agents" / agent_id, agent_id)

        found = {aid for aid, _, _ in discover(pkb_root=tmp_path)}
        assert {"researcher", "curator", "browser"} <= found

    def test_frontmatter_skills_list_still_readable(self, tmp_path):
        from arail.agents.loader import discover

        _agent_md(tmp_path / "agents" / "researcher", "researcher")

        entries = {aid: fm for aid, _, fm in discover(pkb_root=tmp_path)}
        assert entries["researcher"].get("skills") == ["a", "b"]
