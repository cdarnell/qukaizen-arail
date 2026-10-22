"""Buddy on the floor — the real commands, the real trigger, the real ``y``.

Declares ARAIL's operator vocabulary for ``qukaizen_harness``: every argument
is an enum, so the model cannot compose shell. ``pactl apply`` is disruptive
and approved never; it waits for a person's ruling, written by
``arailctl rule "<rendered command>" allow|deny``.

    python -m arail.ops.buddy_floor run            # poll triggers, tick Buddy
    python -m arail.ops.buddy_floor rule "pactl apply teams.toml" allow

State lives under ``$QKZ_HOME`` (default ``~/.qukaizen``): ``rulings.jsonl``,
``ledger.jsonl``, ``buddy/`` (tmux logs), ``3deval/last.json``.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from qukaizen_harness import (
    Guardrails,
    Harness,
    Ledger,
    ModelManager,
    ModelPolicy,
    Mutation,
    Resident,
    Runtime,
    Segment,
    Tick,
    ToolRegistry,
    Trigger,
    Wake,
)
from qukaizen_harness.commands import (
    CommandSpec,
    DisruptiveNeedsRuling,
    Operator,
    Rulings,
    SubprocessOperator,
    TmuxOperator,
    declare_commands,
)

CHARTER = (
    "You are Buddy. Your goal is to take care of this World. "
    "You describe; you never prescribe. "
    "When a step is disruptive, you stop and wait for a person."
)
FAILURE_THRESHOLD = 0.15


def home() -> Path:
    return Path(os.environ.get("QKZ_HOME", "~/.qukaizen")).expanduser()


COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec("arailctl.doctor", "arailctl doctor", "read", description="check the lab"),
    CommandSpec("arailctl.drift", "arailctl drift", "read", description="spec vs actual drift"),
    CommandSpec(
        "logs.tail",
        "arailctl logs --since {since}",
        "read",
        args={"since": ["2m", "10m", "1h"]},
        description="recent activity",
    ),
    CommandSpec(
        "arailctl.restart",
        "arailctl restart {flags}",
        "restart",
        args={"flags": ["", "--debug"]},
        approved=frozenset({"arailctl restart --debug"}),
        description="restart the lab; --debug captures the trace",
    ),
    CommandSpec(
        "arailctl.start",
        "arailctl start {flags}",
        "restart",
        args={"flags": ["", "--debug"]},
        approved=frozenset({"arailctl start", "arailctl start --debug"}),
        description="start the lab",
    ),
    CommandSpec(
        "pactl.validate",
        "pactl validate {manifest}",
        "read",
        args={"manifest": ["teams.toml"]},
        description="validate the charter",
    ),
    CommandSpec(
        "pactl.apply",
        "pactl apply {manifest}",
        "disruptive",
        args={"manifest": ["teams.toml"]},
        description="apply the charter; waits for a person",
    ),
)


class EvalFailureTrigger(Trigger):
    """Wakes when 3D.EVAL's latest run crosses the failure threshold, once per run_sha."""

    def __init__(self, path: Path | None = None, threshold: float = FAILURE_THRESHOLD) -> None:
        self.path = path or home() / "3deval" / "last.json"
        self.threshold = threshold
        self._seen: set[str] = set()

    def poll(self) -> Wake | None:
        try:
            row = json.loads(self.path.read_text())
            rate, sha, world = float(row["failure_rate"]), str(row["run_sha"]), str(row["world"])
        except (OSError, ValueError, KeyError, TypeError):
            return None  # absent or malformed is "nothing to report", never a crash
        if rate <= self.threshold or sha in self._seen:
            return None
        self._seen.add(sha)
        return Wake("change", f"eval.failure_rate {rate:.2f} > {self.threshold:.2f} on {world}")


def build(
    runtime: Runtime,
    policy: ModelPolicy,
    operator: Operator | None = None,
    state: Path | None = None,
) -> tuple[Harness, Resident, Rulings]:
    state = state or home()
    state.mkdir(parents=True, exist_ok=True)
    op = operator or TmuxOperator(log_dir=state / "buddy")
    registry = ToolRegistry()
    specs = declare_commands(registry, COMMANDS, op)
    rulings = Rulings(state / "rulings.jsonl")
    guards = Guardrails.standard()
    guards.rules.append(DisruptiveNeedsRuling(specs, rulings))
    harness = Harness(
        registry=registry,
        models=ModelManager(policy, runtime),
        guardrails=guards,
        ledger=Ledger(state / "ledger.jsonl"),
    )
    buddy = Resident(
        "buddy",
        [Segment("charter", CHARTER, Mutation.STATIC)],
        triggers=[EvalFailureTrigger(state / "3deval" / "last.json")],
    )
    harness.admit(buddy)
    return harness, buddy, rulings


def episode(harness: Harness, resident: Resident, wake: Wake, max_steps: int = 6) -> list[Tick]:
    """One wake is one decision; a failure takes several. Keep ticking on the same
    wake until the resident idles, is flagged (waits for a person), errors, or
    runs out of steps."""
    ticks: list[Tick] = []
    for _ in range(max_steps):
        t = harness.tick(resident, wake)
        ticks.append(t)
        if t.outcome != "acted":
            break
    return ticks


def rule(command: str, ruling: str, state: Path | None = None) -> None:
    """The ``y`` a person presses in their own pane."""
    Rulings(( state or home()) / "rulings.jsonl").record(command, ruling)


def _run(poll_s: float = 5.0) -> None:  # pragma: no cover - needs the real engine
    from arail.runtime.queuellm_adapter import QueueLLMRuntime

    model = os.environ.get("AEROLLM_MODEL", "")
    harness, buddy, rulings = build(QueueLLMRuntime(), ModelPolicy(model))
    pending: Wake | None = None
    seen = rulings.path.stat().st_mtime_ns
    while True:
        wake = next((w for t in buddy.triggers if (w := t.poll())), None)
        changed = rulings.path.stat().st_mtime_ns != seen
        if wake is None and pending is not None and changed:
            wake = pending  # a person ruled: resume the episode that was waiting
        seen = rulings.path.stat().st_mtime_ns
        if wake is not None:
            ticks = episode(harness, buddy, wake)
            for t in ticks:
                print(f"{t.outcome}: {t.proposed.tool if t.proposed else '-'}", flush=True)
            pending = wake if ticks[-1].outcome == "flagged" else None
        time.sleep(poll_s)


def main(argv: list[str]) -> int:
    if len(argv) == 4 and argv[1] == "rule" and argv[3] in ("allow", "deny"):
        rule(argv[2], argv[3])
        print(f"ruled: {argv[3]} {argv[2]!r}")
        return 0
    if len(argv) == 2 and argv[1] == "run":
        _run()
        return 0
    print('usage: buddy_floor run | rule "<rendered command>" allow|deny', file=sys.stderr)
    return 2


__all__ = ["CHARTER", "COMMANDS", "EvalFailureTrigger", "SubprocessOperator", "build", "episode", "rule"]

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
