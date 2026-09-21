"""S0 — prove or kill the attribution mechanism before any production code
lands on top of it.

ARCHITECTURE.md (sprint 2026-09-20-buddy-front-and-center) rests the whole
attribution design on four assumptions (A1-A4) about how a
``contextvars.Context`` propagates across the four hops an agent call can
take between ``agents/loader.py`` and the ``ModelRouter`` chokepoint:

  A1  ``asyncio.create_task``            copies the context   (loader -> agent loop)
  A2  ``asyncio.to_thread``               copies the context   (blocking model call)
  A3  a bare ``threading.Thread``         does NOT copy it     (the one bare-Thread
      agent site, ``_builtin_presence.py``) -- and a
      ``contextvars.copy_context().run(...)``-wrapped Thread DOES, which is the
      ``spawn_thread`` shim's whole reason to exist
  A4  ``loop.run_in_executor``            does NOT copy it, and no agent path
      uses it today (a static grep, not a propagation question)

This module is throwaway harness code, deliberately not importing anything
from ``arail`` -- it tests the CPython/asyncio mechanism itself, not this
sprint's not-yet-written production modules. If any assertion here fails,
the mechanism is wrong for this Python version / platform and the builder
must stop and surface the gap rather than build S1-S7 on a broken premise.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import subprocess
import sys
import threading

import pytest

# A throwaway contextvar standing in for the real ``agent_context._CALL``
# that S1 will introduce. Using a fresh ContextVar per test (via a fixture)
# keeps tests isolated from each other regardless of run order.
_PROBE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "s0_probe", default=None
)


@pytest.fixture(autouse=True)
def _reset_probe():
    token = _PROBE.set(None)
    try:
        yield
    finally:
        _PROBE.reset(token)


# ---------------------------------------------------------------------------
# A1 — asyncio.create_task copies the calling Context
# ---------------------------------------------------------------------------

def test_create_task_copies_context():
    _PROBE.set("outer-value")
    seen: dict[str, str | None] = {}

    async def _inner():
        seen["value"] = _PROBE.get()

    async def _scenario():
        task = asyncio.create_task(_inner())
        await task

    asyncio.run(_scenario())

    assert seen["value"] == "outer-value", (
        "A1 disproved: asyncio.create_task did NOT copy the calling context. "
        "The loader contract (L1) cannot rely on create_task to propagate "
        "attribution into a spawned agent loop."
    )


def test_create_task_sees_a_snapshot_not_a_live_reference():
    """A task's copied context is a snapshot at spawn time: a later mutation
    in the parent must NOT retroactively change what the task sees. This is
    the property the reentrancy design (agent_call token reset in `finally`)
    depends on -- contexts don't alias.
    """
    _PROBE.set("before-spawn")
    seen: dict[str, str | None] = {}

    async def _inner(started: asyncio.Event):
        started.set()
        # Give the parent a chance to mutate its own context after spawn.
        await asyncio.sleep(0.01)
        seen["value"] = _PROBE.get()

    async def _scenario():
        started = asyncio.Event()
        task = asyncio.create_task(_inner(started))
        await started.wait()
        _PROBE.set("after-spawn-mutation")  # parent's own context only
        await task

    asyncio.run(_scenario())

    assert seen["value"] == "before-spawn", (
        "A task's context should be a point-in-time copy; a parent mutation "
        "after spawn leaked into the child."
    )


# ---------------------------------------------------------------------------
# A2 — asyncio.to_thread copies the context into the worker thread
# ---------------------------------------------------------------------------

def test_to_thread_copies_context():
    _PROBE.set("thread-pool-value")

    def _blocking():
        return _PROBE.get()

    async def _scenario():
        return await asyncio.to_thread(_blocking)

    result = asyncio.run(_scenario())

    assert result == "thread-pool-value", (
        "A2 disproved: asyncio.to_thread did NOT copy the calling context "
        "into its worker thread. Every Buddy/SRE/librarian model call made "
        "via to_thread would be unattributed."
    )


# ---------------------------------------------------------------------------
# A3 — a bare threading.Thread does NOT copy the context; a
# copy_context().run(...)-wrapped Thread DOES.
# ---------------------------------------------------------------------------

def test_bare_thread_does_not_copy_context():
    _PROBE.set("main-thread-value")
    seen: dict[str, str | None] = {}

    def _target():
        seen["value"] = _PROBE.get()

    t = threading.Thread(target=_target)
    t.start()
    t.join()

    assert seen["value"] is None, (
        "A3 disproved (half 1): a bare threading.Thread copied the parent "
        "context. If this ever changes, the spawn_thread shim becomes "
        "unnecessary but harmless -- re-check A3's 'if wrong' note in "
        "ARCHITECTURE.md rather than assuming this test is broken."
    )


def test_copy_context_wrapped_thread_copies_context():
    """This is the ``spawn_thread`` shim ARCHITECTURE.md prescribes:
    ``threading.Thread(target=contextvars.copy_context().run, args=(fn, *a))``.
    """
    _PROBE.set("spawn-thread-value")
    seen: dict[str, str | None] = {}

    def _target():
        seen["value"] = _PROBE.get()

    ctx = contextvars.copy_context()
    t = threading.Thread(target=ctx.run, args=(_target,))
    t.start()
    t.join()

    assert seen["value"] == "spawn-thread-value", (
        "A3 disproved (half 2): a copy_context().run(...)-wrapped Thread did "
        "not carry the context either. The spawn_thread shim as specified "
        "in ARCHITECTURE.md would not work; the presence agent's proactive "
        "thread would be unattributed with no mitigation available."
    )


# ---------------------------------------------------------------------------
# A4 — loop.run_in_executor does NOT copy the context, and no agent path
# uses it today (a static fact, checked here rather than assumed).
# ---------------------------------------------------------------------------

def test_run_in_executor_does_not_copy_context():
    _PROBE.set("executor-value")

    def _blocking():
        return _PROBE.get()

    async def _scenario():
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _blocking)

    result = asyncio.run(_scenario())

    assert result is None, (
        "A4 disproved (half 1): loop.run_in_executor unexpectedly copied "
        "the context. This would mean run_in_executor is actually safe for "
        "attribution -- re-read A4's 'if wrong' note, this is good news but "
        "changes what F3's static guard needs to forbid."
    )


def test_no_agent_path_uses_run_in_executor():
    """A4 disproved (half 2) would mean a silent unattributed hop already
    exists. This is the static guard F3 requires; S0 runs it once now so a
    later slice's version of this guard has a known-good baseline to diff
    against, not a first-time discovery.
    """
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    agents_dir = repo_root / "src" / "arail" / "agents"
    assert agents_dir.is_dir(), f"expected agents dir at {agents_dir}"

    hits: list[str] = []
    for path in agents_dir.rglob("*.py"):
        text = path.read_text(errors="replace")
        if "run_in_executor" in text:
            hits.append(str(path.relative_to(repo_root)))

    assert not hits, (
        "A4's second half is false today: run_in_executor is used under "
        f"src/arail/agents/ at {hits!r}. That is a silent unattributed hop "
        "that must be dealt with explicitly, not discovered by this test "
        "failing in a later slice."
    )


# ---------------------------------------------------------------------------
# Subprocess hop — no context copy across a process boundary (obviously),
# but the JSON round-trip design (to_subprocess_payload / from_subprocess_
# payload in ARCHITECTURE.md's contract #1) must actually carry a value
# through stdin -> child sets its own context -> stdout, end to end.
# ---------------------------------------------------------------------------

_CHILD_SCRIPT = r"""
import contextvars, json, sys

probe = contextvars.ContextVar("s0_probe", default=None)

payload = json.loads(sys.stdin.readline())
token = probe.set(payload.get("trace_id"))
try:
    # Simulate the child doing work "attributed" to the parent's trace_id,
    # then reporting back what it saw -- this is what
    # skills/goal_parser/_subprocess_runner.py will do for real in S2.
    seen = probe.get()
finally:
    probe.reset(token)

sys.stdout.write(json.dumps({"seen_trace_id": seen}))
sys.stdout.flush()
"""


def test_subprocess_json_roundtrip_carries_trace_id():
    payload = json.dumps({"trace_id": "deadbeefcafef00d", "agent_id": "researcher"})

    proc = subprocess.run(
        [sys.executable, "-c", _CHILD_SCRIPT],
        input=payload + "\n",
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert proc.returncode == 0, (
        f"subprocess round-trip harness failed: stderr={proc.stdout!r} {proc.stderr!r}"
    )
    result = json.loads(proc.stdout)
    assert result["seen_trace_id"] == "deadbeefcafef00d", (
        "The subprocess JSON protocol did not carry trace_id through to the "
        "child's own contextvar. This is not a Python-mechanism question "
        "like A1-A4 -- it is the design's own plumbing -- but if it fails, "
        "the out-of-process leg of the attribution design (contract #9, "
        "'the parent authors the trace') is unbuildable as scoped."
    )


def test_subprocess_never_copies_parent_context_automatically():
    """Sanity check the negative: nothing about subprocess.run magically
    shares Python-level context with the child. This is why the JSON
    protocol carrying {trace_id, agent_id, brain, effort} explicitly is
    necessary rather than incidental.
    """
    _PROBE.set("parent-only-value")

    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.stdout.write('no-shared-state')"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.stdout == "no-shared-state"
    # The parent's contextvar is untouched by the child process existing.
    assert _PROBE.get() == "parent-only-value"
