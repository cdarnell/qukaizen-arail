"""Dynamic agent loader.

Walks ``lab/pkb/agents/*/AGENT.md``, dynamically imports each
companion ``.py``, and returns a dict of ``{agent_id: instance}``.
This is the generalization of the original Buddy-specific shim —
now every agent folder under the PKB is discovered the same way.

## Contract with agent folders

Each folder under ``lab/pkb/agents/`` is an agent iff it contains
an ``AGENT.md`` with YAML frontmatter. The folder name is the
``agent_id``. Siblings to ``AGENT.md``:

- ``<agent_id>.py`` — the Python module, exporting a singleton
  variable named ``<agent_id>`` that the loader grabs.
- ``state.json`` — persisted memory (optional; agent owns it).
- ``decisions.md`` — decision log (optional; human-authored).
- ``dreams/`` — nightly reflections (optional; written by
  ``agent.dream()``).

Folders without ``AGENT.md`` are ignored — that's how the loader
distinguishes agent folders (`buddy/`) from shared output folders
(`research/`, `experiments/`, …) under the same parent.

## Shipped fallback

Agents whose id appears in ``_SHIPPED`` have a builtin
(``src/arail/agents/_builtin_<id>.py``) that the loader falls
back to if the PKB copy fails to import. User-forged agents don't
have a fallback — a broken `.py` just means the agent doesn't
load and a warning goes to the activity log. The Forge will
surface these errors prominently in its own UI.

## Skills-only agents (``_SKILLS_ONLY``)

``researcher``, ``curator``, and ``browser`` are a THIRD category,
distinct from both of the above. ``agent_seed.py`` seeds their
``AGENT.md`` on first boot purely as a hot-editable skills
loadout — the actual implementations are top-level modules
(``src/arail/agents/{researcher,curator,browser}.py``) imported
directly by the portal (``GET /api/agents/status`` and friends),
never through this loader. ``researcher`` even has a matching
module-level singleton (``researcher = ResearcherAgent()``) that
*could* satisfy ``load_one``'s contract, but wiring it through here
too would risk a second, divergent instance; ``curator``/``browser``
don't have a loader-shaped singleton at all — they're helper
modules ``researcher.py`` calls into, not independent ticking
agents.

There was never meant to be a companion ``<id>.py`` in these three
folders, so a missing one is not a failure — before this was made
explicit, ``load_one`` logged "Agent 'researcher' failed to load"
(error level) for all three, on every single boot, for three
months, with nothing actually broken. ``discover()`` still returns
all three (unchanged) — the Skills tab reads their frontmatter via
``/api/agents/loadouts`` and must keep working; only the
import-and-instantiate half of the pipeline skips them.

## Cache

Agents are loaded lazily and cached per-process — repeated calls
to ``load_one(id)`` or ``load_all()`` return the same instance.
The cache prevents the "two BuddyAgents ticking in parallel"
problem when both the loader and the backcompat shim want to
reach the same agent.
"""

from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from arail import agent_context
from arail.activity import activity_log
from arail.pkb import _pkb_root
from arail.skills_loader import parse_frontmatter

log = logging.getLogger(__name__)


# Agents with a bundled builtin in src/arail/agents/_builtin_<id>.py.
# These auto-seed their PKB folder on first boot and fall back to
# the builtin if the PKB copy is broken. User-forged agents don't
# appear here — they have no fallback.
_SHIPPED: set[str] = {
    "buddy", "sre", "presence", "librarian",
    "debt_advisor", "consolidation_analyzer",
}

# Agents whose AGENT.md is a skills-loadout sidecar for an implementation
# that's wired directly at the top level (see the module docstring's
# "Skills-only agents" section) — never meant to have a companion
# lab/pkb/agents/<id>/<id>.py, so load_one() must not treat a missing one
# as a failure. discover() is untouched: the Skills tab still needs these.
_SKILLS_ONLY: set[str] = {"researcher", "curator", "browser"}

# Singleton cache. Key = agent_id, value = agent instance (or the
# sentinel _BROKEN if loading failed this session).
_CACHE: Dict[str, Any] = {}


class _BrokenAgent:
    """Sentinel returned when an agent fails to load so ``load_one``
    doesn't keep trying and spamming the log."""
    status = "error"


_BROKEN = _BrokenAgent()


def _agents_root(pkb_root: Path | None = None) -> Path:
    return (pkb_root or _pkb_root()) / "agents"


def discover(pkb_root: Path | None = None) -> List[Tuple[str, Path, Dict[str, Any]]]:
    """Scan for agent folders under the PKB.

    Returns a list of ``(agent_id, agent_dir, frontmatter)`` tuples.
    Folders without ``AGENT.md`` are skipped. Folders with broken
    frontmatter still appear with an empty dict — the caller
    decides what to do (the loader logs a warning and skips).
    """
    root = _agents_root(pkb_root)
    if not root.exists():
        return []
    out: List[Tuple[str, Path, Dict[str, Any]]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith(".") or child.name.startswith("_"):
            continue
        agent_md = child / "AGENT.md"
        if not agent_md.exists():
            continue  # output dirs (research/, experiments/, …)
        try:
            fm = parse_frontmatter(agent_md.read_text(errors="replace"))
        except OSError:
            fm = {}
        out.append((child.name, child, fm))
    return out


def _seed_if_shipped(agent_id: str) -> None:
    """For shipped agents, make sure the folder exists on disk."""
    if agent_id == "buddy":
        try:
            from arail.agents.builtin_seed import ensure_buddy_folder
            ensure_buddy_folder()
        except Exception as e:  # noqa: BLE001
            log.warning("ensure_buddy_folder failed: %s", e)
    elif agent_id == "sre":
        try:
            from arail.agents.builtin_seed import ensure_sre_folder
            ensure_sre_folder()
        except Exception as e:  # noqa: BLE001
            log.warning("ensure_sre_folder failed: %s", e)
    elif agent_id == "presence":
        try:
            from arail.agents.builtin_seed import ensure_presence_folder
            ensure_presence_folder()
        except Exception as e:  # noqa: BLE001
            log.warning("ensure_presence_folder failed: %s", e)
    elif agent_id == "librarian":
        try:
            from arail.agents.builtin_seed import ensure_librarian_folder
            ensure_librarian_folder()
        except Exception as e:  # noqa: BLE001
            log.warning("ensure_librarian_folder failed: %s", e)
    elif agent_id == "debt_advisor":
        try:
            from arail.agents.builtin_seed import ensure_debt_advisor_folder
            ensure_debt_advisor_folder()
        except Exception as e:  # noqa: BLE001
            log.warning("ensure_debt_advisor_folder failed: %s", e)
    elif agent_id == "consolidation_analyzer":
        try:
            from arail.agents.builtin_seed import ensure_consolidation_analyzer_folder
            ensure_consolidation_analyzer_folder()
        except Exception as e:  # noqa: BLE001
            log.warning("ensure_consolidation_analyzer_folder failed: %s", e)


def _import_from_path(py_file: Path, unique_name: str) -> Optional[Any]:
    """Import a module from an absolute path, return the module or None.

    Registers the module in ``sys.modules`` *before* executing it —
    Python 3.12+ decorators like ``@dataclass`` walk
    ``sys.modules.get(cls.__module__).__dict__`` during class
    creation, so the module must already be findable by the time
    ``exec_module()`` hits the first decorated class.
    """
    import sys
    try:
        spec = importlib.util.spec_from_file_location(unique_name, str(py_file))
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[unique_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            # Poisoned entry could blow up a later retry — clear it.
            sys.modules.pop(unique_name, None)
            raise
        return module
    except Exception as e:  # noqa: BLE001
        log.warning("Failed to import %s: %s", py_file, e)
        return None


def _builtin_fallback(agent_id: str) -> Optional[Any]:
    """Load the shipped builtin for an agent if one exists."""
    if agent_id not in _SHIPPED:
        return None
    try:
        module = __import__(
            f"arail.agents._builtin_{agent_id}",
            fromlist=[agent_id],
        )
    except Exception as e:  # noqa: BLE001
        log.warning("builtin fallback for %s failed: %s", agent_id, e)
        return None
    return getattr(module, agent_id, None)


def load_one(agent_id: str, pkb_root: Path | None = None) -> Optional[Any]:
    """Load a single agent by id. Cached per-process.

    Resolution order:
      1. Cache hit — return the stored instance (even the broken
         sentinel, so we don't retry).
      2. Seed folder if it's a shipped agent and missing.
      3. Skills-only agent with no companion .py — return None quietly
         (see the module docstring's "Skills-only agents" section; this
         is the expected shape, not a failure, so no error is logged).
      4. Import ``lab/pkb/agents/<id>/<id>.py`` dynamically.
      5. On failure, fall back to the shipped builtin (if any).
      6. Return None for a user-forged agent whose code is broken.
    """
    if agent_id in _CACHE:
        cached = _CACHE[agent_id]
        return None if cached is _BROKEN else cached

    # Path-traversal guard — agent_ids come from directory names so
    # they should never contain slashes, but belt-and-suspenders.
    if not agent_id or "/" in agent_id or ".." in agent_id:
        return None

    _seed_if_shipped(agent_id)

    folder = _agents_root(pkb_root) / agent_id
    py_file = folder / f"{agent_id}.py"
    unique = f"arail.agents._folder_{agent_id}"

    # A missing .py here was never a failure for these three — it's the
    # designed state (agent_seed.py seeds their AGENT.md as a skills-only
    # sidecar; the real implementation is wired directly at the top level).
    # Not cached: the check is one Path.exists(), cheaper than the sentinel
    # bookkeeping, and lets a .py that later appears (hand-added, or a
    # future seed) be picked up on the very next call instead of being
    # permanently shadowed by a stale cache entry from this boot.
    if agent_id in _SKILLS_ONLY and not py_file.exists():
        return None

    instance = None
    if py_file.exists():
        module = _import_from_path(py_file, unique)
        if module is not None:
            instance = getattr(module, agent_id, None)
            if instance is None:
                log.warning(
                    "Agent file %s imported but doesn't export '%s' singleton",
                    py_file, agent_id,
                )

    if instance is None:
        # PKB copy missing / broken / didn't export singleton — try builtin.
        instance = _builtin_fallback(agent_id)
        if instance is not None and agent_id in _SHIPPED:
            activity_log.emit(
                "agents",
                f"{agent_id} PKB copy unavailable — running on builtin fallback. "
                f"Fix {py_file.relative_to(_pkb_root().parent)} from /dac.",
                "warn",
            )

    if instance is None:
        activity_log.emit(
            "agents",
            f"Agent {agent_id!r} failed to load. Check {py_file}.",
            "error",
        )
        _CACHE[agent_id] = _BROKEN
        return None

    _CACHE[agent_id] = instance
    return instance


def load_all(pkb_root: Path | None = None) -> Dict[str, Any]:
    """Load every agent folder under the PKB. Cached.

    Install the egress guard as the very first action — belt-and-suspenders
    against entry points that load agents without booting the portal
    (e.g. CLI commands, test harnesses). The guard is idempotent so if the
    portal's _startup() already installed it, this is a no-op.
    The 3 pre-guard requests.Session() sites in src/arail/router/backends.py
    (lines 231, 440, 590) all target localhost — see noqa-airgap comments there.
    """
    try:
        from arail import egress as _egress
        _egress.install_guard()
    except Exception as _eg_err:  # noqa: BLE001
        log.warning("Egress guard install failed in load_all: %s", _eg_err)

    # Shipped agents must exist on disk before discover() can see them
    # on a fresh lab — seeding is idempotent, so this is a no-op after
    # first boot.
    for shipped_id in sorted(_SHIPPED):
        _seed_if_shipped(shipped_id)

    out: Dict[str, Any] = {}
    for agent_id, _, _fm in discover(pkb_root=pkb_root):
        instance = load_one(agent_id, pkb_root=pkb_root)
        if instance is not None:
            out[agent_id] = instance
    return out


def start_all_auto(agents: Dict[str, Any], pkb_root: Path | None = None) -> None:
    """Start every loaded agent that opts in via AGENT.md.

    Each agent's frontmatter controls two things:
      - ``auto_start_env``: name of an env var ("LAB_BUDDY") — if the
        env var is not set to off/0/false/no, the agent is started.
      - ``dream: true``: registers the agent with the dream daemon
        for the nightly reflection loop.
    """
    from arail.agents.dream_daemon import register as register_dream

    for agent_id, instance in agents.items():
        folder = _agents_root(pkb_root) / agent_id
        try:
            fm = parse_frontmatter((folder / "AGENT.md").read_text())
        except Exception:
            fm = {}

        # Honor the auto_start_env gate. Default: start unless
        # env says off. Agents without the field get started too.
        env_var = fm.get("auto_start_env")
        should_start = True
        if env_var:
            val = os.getenv(str(env_var), "on").lower()
            if val in ("off", "0", "false", "no"):
                should_start = False

        if should_start and hasattr(instance, "start"):
            try:
                # L1 — the loader contract's "cannot be forgotten" layer
                # (ARCHITECTURE.md, sprint 2026-09-20-buddy-front-and-
                # center): attribute the whole agent loop to agent_id here,
                # once. asyncio.create_task copies the calling context, so
                # an agent that spawns its loop inside start() (every
                # built-in does) is attributed for the life of that loop —
                # no per-agent edit required, including for a user-defined
                # agent the loader has never seen before.
                with agent_context.agent_call(agent_id):
                    instance.start()
            except Exception as e:  # noqa: BLE001
                activity_log.emit(
                    "agents",
                    f"{agent_id} failed to start: {type(e).__name__}: {e}",
                    "warn",
                )

        # Register with the dream daemon if the agent opts in.
        # ``dream: true`` in YAML frontmatter parses to the string
        # 'true' via the minimal parser, so accept both forms.
        dream_flag = fm.get("dream")
        if (dream_flag is True
                or (isinstance(dream_flag, str)
                    and dream_flag.lower() in ("true", "yes", "on", "1"))):
            if hasattr(instance, "dream"):
                register_dream(agent_id, instance)


def clear_cache() -> None:
    """Test helper — drop cached instances."""
    _CACHE.clear()
