"""Buddy on the floor: commands are enums, apply waits for a ruling, the trigger fires once."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("qukaizen_harness")

from qukaizen_harness import Completion, ModelInfo, ModelPolicy, RunResult

from arail.ops import buddy_floor as bf
from arail.runtime.queuellm_adapter import QueueLLMRuntime, params_from_name


class FakeOp:
    def __init__(self) -> None:
        self.ran: list[str] = []
        self.said: list[str] = []

    def say(self, why: str) -> None:
        self.said.append(why)

    def run(self, command: str, *, timeout_s: float = 120.0) -> RunResult:
        self.ran.append(command)
        return RunResult(command, 0, "ok", "0" * 64, 0.0)


class Scripted:
    """Plays a fixed plan, one decision per completion; honest about residency."""

    def __init__(self, plan: list[dict]) -> None:
        self.plan, self.i = plan, 0
        self._loaded: list[ModelInfo] = []

    def describe(self, model: str) -> ModelInfo:
        return ModelInfo(model, 9.0)

    def load(self, model: str, *, pin: bool = False) -> ModelInfo:
        self._loaded = [ModelInfo(model, 9.0, resident=pin)]
        return self._loaded[0]

    def loaded(self) -> list[ModelInfo]:
        return list(self._loaded)

    def complete(self, model: str, prompt: str, **kw: object) -> Completion:
        step = self.plan[min(self.i, len(self.plan) - 1)]
        self.i += 1
        return Completion(json.dumps(step))


def test_every_arg_is_an_enum_and_apply_is_never_approved() -> None:
    for spec in bf.COMMANDS:
        assert all(spec.args[a] for a in spec.args), spec.name
    apply = next(c for c in bf.COMMANDS if c.name == "pactl.apply")
    assert apply.cls == "disruptive" and not apply.approved
    restart = next(c for c in bf.COMMANDS if c.name == "arailctl.restart")
    assert restart.approved == frozenset({"arailctl restart --debug"})
    start = next(c for c in bf.COMMANDS if c.name == "arailctl.start")
    assert start.approved == frozenset({"arailctl start", "arailctl start --debug"})


def test_charter_line_is_first_segment(tmp_path: Path) -> None:
    _, buddy, _ = bf.build(Scripted([]), ModelPolicy("m-9B"), FakeOp(), tmp_path)
    assert buddy.standing[0].text.startswith(
        "You are Buddy. Your goal is to take care of this World. "
        "You describe; you never prescribe. "
        "When a step is disruptive, you stop and wait for a person."
    )


def test_apply_waits_then_runs_once_then_waits_again(tmp_path: Path) -> None:
    op = FakeOp()
    plan = [
        {"tool": "arailctl.doctor", "args": {}},
        {"tool": "arailctl.restart", "args": {"flags": "--debug"}},
        {"tool": "pactl.apply", "args": {"manifest": "teams.toml"}},
    ]
    h, buddy, _ = bf.build(Scripted(plan), ModelPolicy("m-9B"), op, tmp_path)
    wake = bf.Wake("change", "eval.failure_rate 0.22 > 0.15 on g5")
    assert [h.tick(buddy, wake).outcome for _ in range(3)][:2] != ["flagged"] * 2
    assert op.ran == ["arailctl doctor", "arailctl restart --debug"]

    bf.rule("pactl apply teams.toml", "allow", tmp_path)
    assert h.tick(buddy, wake).outcome != "flagged"
    assert op.ran[-1] == "pactl apply teams.toml"

    again = h.tick(buddy, wake)  # the ruling was spent: waits again
    assert again.outcome == "flagged" and op.ran.count("pactl apply teams.toml") == 1
    assert h.ledger.verify()


def test_episode_runs_until_apply_waits(tmp_path: Path) -> None:
    op = FakeOp()
    plan = [
        {"tool": "arailctl.doctor", "args": {}},
        {"tool": "arailctl.restart", "args": {"flags": "--debug"}},
        {"tool": "pactl.apply", "args": {"manifest": "teams.toml"}},
    ]
    h, buddy, _ = bf.build(Scripted(plan), ModelPolicy("m-9B"), op, tmp_path)
    wake = bf.Wake("change", "x")
    ticks = bf.episode(h, buddy, wake)
    assert [t.outcome for t in ticks] == ["acted", "acted", "flagged"]
    bf.rule("pactl apply teams.toml", "allow", tmp_path)
    ticks = bf.episode(h, buddy, wake)
    assert [t.outcome for t in ticks] == ["acted", "flagged"]  # ran once, waits again
    assert op.ran.count("pactl apply teams.toml") == 1


def test_unruled_apply_never_runs(tmp_path: Path) -> None:
    op = FakeOp()
    plan = [{"tool": "pactl.apply", "args": {"manifest": "teams.toml"}}]
    h, buddy, _ = bf.build(Scripted(plan), ModelPolicy("m-9B"), op, tmp_path)
    t = h.tick(buddy, bf.Wake("change", "x"))
    assert t.outcome == "flagged" and op.ran == []


def test_deny_ruling_blocks(tmp_path: Path) -> None:
    op = FakeOp()
    plan = [{"tool": "pactl.apply", "args": {"manifest": "teams.toml"}}]
    h, buddy, _ = bf.build(Scripted(plan), ModelPolicy("m-9B"), op, tmp_path)
    bf.rule("pactl apply teams.toml", "deny", tmp_path)
    assert h.tick(buddy, bf.Wake("change", "x")).outcome == "flagged" and op.ran == []


def test_trigger_fires_once_per_run_and_only_over_threshold(tmp_path: Path) -> None:
    f = tmp_path / "last.json"
    trig = bf.EvalFailureTrigger(f)
    assert trig.poll() is None  # no file
    f.write_text("not json")
    assert trig.poll() is None  # malformed
    f.write_text(json.dumps({"world": "g5", "failure_rate": 0.15, "run_sha": "a"}))
    assert trig.poll() is None  # not strictly over
    f.write_text(json.dumps({"world": "g5", "failure_rate": 0.22, "run_sha": "b"}))
    w = trig.poll()
    assert w is not None and w.label == "change:eval.failure_rate 0.22 > 0.15 on g5"
    assert trig.poll() is None  # same run_sha
    f.write_text(json.dumps({"world": "g5", "failure_rate": 0.30, "run_sha": "c"}))
    assert trig.poll() is not None  # new run


def test_rule_cli_rejects_garbage() -> None:
    assert bf.main(["x", "rule", "pactl apply teams.toml", "maybe"]) == 2


# -- adapter ------------------------------------------------------------------


class FakeBackend:
    model_name = "Qwen3-9B-4bit"

    def __init__(self, live: bool = True) -> None:
        self._runtime = object() if live else None

    def complete(self, prompt: str, max_tokens: int = 512, temperature: float = 0.7) -> object:
        class R:
            text, tokens_used, latency_ms = '{"tool": "idle", "args": {}}', 5, 1.0

        return R()


def test_params_from_name() -> None:
    assert params_from_name("Qwen3-9B-4bit") == 9.0
    assert params_from_name("gemma-2-27b-it") == 27.0
    with pytest.raises(ValueError):
        params_from_name("mystery-model")


def test_adapter_reports_residency_honestly() -> None:
    rt = QueueLLMRuntime(lambda: FakeBackend(live=False))
    assert rt.loaded() == []  # nothing loaded yet
    rt.load("Qwen3-9B-4bit", pin=True)
    assert rt.loaded() == []  # backend has no live runtime: do not claim it
    rt2 = QueueLLMRuntime(lambda: FakeBackend())
    rt2.load("Qwen3-9B-4bit", pin=True)
    assert [m.resident for m in rt2.loaded()] == [True]


def test_adapter_never_swaps_the_resident() -> None:
    rt = QueueLLMRuntime(lambda: FakeBackend())
    with pytest.raises(RuntimeError):
        rt.load("Other-12B")


def test_adapter_admits_it_ignores_grammar() -> None:
    rt = QueueLLMRuntime(lambda: FakeBackend())
    rt.load("Qwen3-9B-4bit", pin=True)
    c = rt.complete("Qwen3-9B-4bit", "p", grammar="root ::= x")
    assert c.usage["grammar_enforced"] is False and c.usage["grammar_requested"] is True
