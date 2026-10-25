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

import itertools
import json
import math
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
        args={"flags": [""]},
        description="restart the lab; always waits for a ruling (nothing is pre-approved)",
    ),
    CommandSpec(
        "arailctl.start",
        "arailctl start {flags}",
        "restart",
        args={"flags": [""]},
        approved=frozenset({"arailctl start"}),
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
    """Wakes when 3D.EVAL's latest run crosses the failure threshold, once per run_sha.

    Seen runs are kept in ``seen.jsonl`` beside ``last.json``, so a restart of Buddy
    does not fire the same run twice. Anything malformed reads as "nothing to report".
    """

    def __init__(self, path: Path | None = None, threshold: float = FAILURE_THRESHOLD) -> None:
        self.path = path or home() / "3deval" / "last.json"
        self.threshold = threshold
        self._seen_path = self.path.with_name("seen.jsonl")
        self._seen: set[str] = self._load_seen()

    def _load_seen(self) -> set[str]:
        try:
            return {x for x in self._seen_path.read_text().split() if x}
        except OSError:
            return set()

    def poll(self) -> Wake | None:
        try:
            row = json.loads(self.path.read_text())
            rate, sha, world = row["failure_rate"], row["run_sha"], row["world"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate):
            return None
        if not (isinstance(sha, str) and sha.strip() and isinstance(world, str) and world.strip()):
            return None
        if len(sha.split()) != 1 or rate <= self.threshold or sha in self._seen:
            return None
        self._seen.add(sha)
        try:
            with self._seen_path.open("a") as f:
                f.write(sha + "\n")
        except OSError:
            pass  # in-memory dedupe still holds for this process
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


def ruling_count(rulings: Rulings) -> int:
    """How many rulings a person has written (spend markers and junk lines do not count)."""
    try:
        return sum(1 for r in rulings._rows() if "ruling" in r)
    except OSError:
        return 0


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


def rulable() -> frozenset[str]:
    """Every rendered command a person may rule on: all non-read renderings."""
    out: set[str] = set()
    for spec in COMMANDS:
        if spec.cls == "read":
            continue
        names = list(spec.args)
        for combo in itertools.product(*(spec.args[n] for n in names)):
            out.add(spec.render(**dict(zip(names, combo, strict=True))))
    return frozenset(out)


def rule(command: str, ruling: str, state: Path | None = None) -> None:
    """The ``y`` a person presses in their own pane."""
    Rulings(( state or home()) / "rulings.jsonl").record(command, ruling)


def _run(poll_s: float = 5.0) -> None:  # pragma: no cover - needs the real engine
    from arail.runtime.queuellm_adapter import QueueLLMRuntime

    model = os.environ.get("AEROLLM_MODEL", "")
    harness, buddy, rulings = build(QueueLLMRuntime(), ModelPolicy(model))
    pending: Wake | None = None
    seen = ruling_count(rulings)
    while True:
        wake = next((w for t in buddy.triggers if (w := t.poll())), None)
        count = ruling_count(rulings)
        if wake is None and pending is not None and count > seen:
            wake = pending  # a person ruled: resume the episode that was waiting
        seen = count
        if wake is not None:
            ticks = episode(harness, buddy, wake)
            for t in ticks:
                print(f"{t.outcome}: {t.proposed.tool if t.proposed else '-'}", flush=True)
            pending = wake if ticks[-1].outcome == "flagged" else None
        time.sleep(poll_s)


def main(argv: list[str]) -> int:
    if len(argv) == 4 and argv[1] == "rule" and argv[3] in ("allow", "deny"):
        if argv[2] not in rulable():
            print(f"not a command Buddy can propose for a ruling: {argv[2]!r}", file=sys.stderr)
            print("rulable: " + ", ".join(sorted(rulable())), file=sys.stderr)
            return 2
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
