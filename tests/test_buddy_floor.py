"""Buddy on the floor: commands are enums, apply waits for a ruling, the trigger fires once."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
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
    assert restart.approved == frozenset()  # a restart always waits for a person
    start = next(c for c in bf.COMMANDS if c.name == "arailctl.start")
    assert start.approved == frozenset({"arailctl start"})


def test_restart_is_never_unattended(tmp_path: Path) -> None:
    op = FakeOp()
    plan = [{"tool": "arailctl.restart", "args": {"flags": ""}}]
    h, buddy, _ = bf.build(Scripted(plan), ModelPolicy("m-9B"), op, tmp_path)
    t = h.tick(buddy, bf.Wake("change", "x"))
    assert t.outcome == "flagged" and op.ran == []


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
        {"tool": "arailctl.drift", "args": {}},
        {"tool": "pactl.apply", "args": {"manifest": "teams.toml"}},
    ]
    h, buddy, _ = bf.build(Scripted(plan), ModelPolicy("m-9B"), op, tmp_path)
    wake = bf.Wake("change", "eval.failure_rate 0.22 > 0.15 on g5")
    assert [h.tick(buddy, wake).outcome for _ in range(3)][:2] != ["flagged"] * 2
    assert op.ran == ["arailctl doctor", "arailctl drift"]

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
        {"tool": "arailctl.drift", "args": {}},
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


# =============================================================================
# QA pass — sprint 2026-10-22-harness-completion (qukaizen-harness repo).
# xfail(strict=True) marks a bug QA found and did not fix; the finding id
# matches that sprint's TEST_REPORT.md. A fix turns the test into XPASS (a
# failure under strict), which is the signal to drop the marker.
# =============================================================================

REPO = Path(__file__).resolve().parent.parent


def _last(tmp_path: Path, row: object) -> Path:
    f = tmp_path / "last.json"
    f.write_text(row if isinstance(row, str) else json.dumps(row))
    return f


# -- trigger -------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["[1, 2]", '"x"', "null", "42", "", "{}"])
def test_trigger_is_quiet_on_non_object_or_empty_json(tmp_path: Path, raw: str) -> None:
    assert bf.EvalFailureTrigger(_last(tmp_path, raw)).poll() is None


@pytest.mark.parametrize("rate", [-1, -0.5, 0, 0.15])
def test_trigger_does_not_fire_at_or_below_threshold(tmp_path: Path, rate: float) -> None:
    f = _last(tmp_path, {"world": "g5", "failure_rate": rate, "run_sha": "a"})
    assert bf.EvalFailureTrigger(f).poll() is None


def test_trigger_reads_a_partially_written_file_as_nothing_then_fires(tmp_path: Path) -> None:
    trig = bf.EvalFailureTrigger(_last(tmp_path, '{"world": "g5", "failure_ra'))
    assert trig.poll() is None
    _last(tmp_path, {"world": "g5", "failure_rate": 0.22, "run_sha": "a"})
    assert trig.poll() is not None


@pytest.mark.parametrize("raw_rate", ["NaN", "Infinity", '"nan"'])
def test_trigger_ignores_non_finite_rates(tmp_path: Path, raw_rate: str) -> None:
    f = _last(tmp_path, f'{{"world": "g5", "run_sha": "a", "failure_rate": {raw_rate}}}')
    assert bf.EvalFailureTrigger(f).poll() is None


def test_trigger_ignores_boolean_rate(tmp_path: Path) -> None:
    f = _last(tmp_path, {"world": "g5", "failure_rate": True, "run_sha": "a"})
    assert bf.EvalFailureTrigger(f).poll() is None


@pytest.mark.parametrize(
    "row",
    [
        {"world": "g5", "failure_rate": 0.22, "run_sha": None},
        {"world": "g5", "failure_rate": 0.22, "run_sha": ""},
        {"world": None, "failure_rate": 0.22, "run_sha": "a"},
    ],
)
def test_trigger_requires_a_real_run_sha_and_world(tmp_path: Path, row: dict) -> None:
    assert bf.EvalFailureTrigger(_last(tmp_path, row)).poll() is None


def test_trigger_does_not_refire_the_same_run_after_a_restart(tmp_path: Path) -> None:
    f = _last(tmp_path, {"world": "g5", "failure_rate": 0.22, "run_sha": "r1"})
    assert bf.EvalFailureTrigger(f).poll() is not None
    assert bf.EvalFailureTrigger(f).poll() is None  # a fresh process, same run


# -- the declared vocabulary vs the real CLI --------------------------------------


def _start_sh_flags() -> set[str]:
    """Flags scripts/start.sh's argument parser accepts (static read; never run)."""
    src = (REPO / "scripts" / "start.sh").read_text()
    loop = src[src.index('while [[ $# -gt 0 ]]; do') :]
    loop = loop[: loop.index("\ndone")]
    return {f for arm in re.findall(r"^\s+([-\w|=*]+)\)", loop, re.MULTILINE) for f in arm.split("|")}


def test_every_declared_start_or_restart_flag_is_one_start_sh_accepts() -> None:
    accepted = _start_sh_flags()
    assert "--warm" in accepted  # the static read itself works
    for spec in bf.COMMANDS:
        if spec.name in ("arailctl.restart", "arailctl.start"):
            for flag in spec.args["flags"]:
                assert flag == "" or flag in accepted, f"{spec.name}: {flag}"


# -- arg handling through build() ----------------------------------------------------


@pytest.mark.parametrize(
    "tool,args",
    [
        ("arailctl.restart", {"flags": "--force"}),
        ("arailctl.restart", {"flags": None}),
        ("arailctl.restart", {"flags": "--debug && pactl apply teams.toml"}),
        ("logs.tail", {"since": "1d"}),
        ("logs.tail", {"since": "10m; cat ~/.ssh/id_rsa"}),
        ("pactl.apply", {"manifest": "../other.toml"}),
        ("pactl.validate", {"manifest": "teams.toml", "strict": "--offline"}),
        ("arailctl.doctor", {"x": "1"}),
        ("shell", {"cmd": "ls"}),
    ],
)
def test_out_of_vocabulary_proposals_never_reach_the_operator(
    tmp_path: Path, tool: str, args: dict
) -> None:
    op = FakeOp()
    h, buddy, _ = bf.build(Scripted([{"tool": tool, "args": args}]), ModelPolicy("m-9B"), op, tmp_path)
    t = h.tick(buddy, bf.Wake("change", "x"))
    assert t.outcome in ("error", "flagged") and op.ran == []
    assert h.ledger.verify()


def test_a_ruling_for_a_near_miss_string_does_not_authorize_apply(tmp_path: Path) -> None:
    op = FakeOp()
    plan = [{"tool": "pactl.apply", "args": {"manifest": "teams.toml"}}]
    h, buddy, _ = bf.build(Scripted(plan), ModelPolicy("m-9B"), op, tmp_path)
    for s in ("pactl apply teams.toml ", "pactl  apply teams.toml", "pactl apply ./teams.toml"):
        bf.rule(s, "allow", tmp_path)
    assert h.tick(buddy, bf.Wake("change", "x")).outcome == "flagged" and op.ran == []


@pytest.mark.parametrize(
    "cmd", ["", "rm -rf /", "pactl apply teams.toml ", "pactl apply other.toml", "arailctl doctor"]
)
def test_rule_refuses_a_command_buddy_can_never_propose(tmp_path: Path, cmd: str) -> None:
    os.environ["QKZ_HOME"] = str(tmp_path)
    try:
        assert bf.main(["x", "rule", cmd, "allow"]) == 2
    finally:
        del os.environ["QKZ_HOME"]


# -- the daemon loop (_run), with stubs: last.json -> wake -> flagged -> rule -> once --


class Remembers(Scripted):
    """A model that uses the session memory: doctor, drift, then apply."""

    def __init__(self) -> None:
        super().__init__([])

    def complete(self, model: str, prompt: str, **kw: object) -> Completion:
        if "called arailctl.doctor" not in prompt:
            step: dict = {"tool": "arailctl.doctor", "args": {}}
        elif "called arailctl.drift" not in prompt:
            step = {"tool": "arailctl.drift", "args": {}}
        else:
            step = {"tool": "pactl.apply", "args": {"manifest": "teams.toml"}}
        return Completion(json.dumps(step))


class _Stop(Exception):
    pass


def _drive_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, on_sleep: dict) -> tuple[FakeOp, list]:
    """Run bf._run for 8 polls with stub runtime/operator; on_sleep[n](home) runs at poll n."""
    import arail.runtime.queuellm_adapter as qa

    monkeypatch.setenv("QKZ_HOME", str(tmp_path))
    monkeypatch.setenv("AEROLLM_MODEL", "m-9B")
    (tmp_path / "3deval").mkdir()
    _last(tmp_path / "3deval", {"world": "g5", "failure_rate": 0.22, "run_sha": "r1"})
    op = FakeOp()
    monkeypatch.setattr(qa, "QueueLLMRuntime", lambda: Remembers())
    monkeypatch.setattr(bf, "TmuxOperator", lambda log_dir: op)
    n = {"i": 0}

    def sleep(_s: float) -> None:
        n["i"] += 1
        if n["i"] in on_sleep:
            on_sleep[n["i"]](tmp_path)
        if n["i"] >= 8:
            raise _Stop

    monkeypatch.setattr(bf.time, "sleep", sleep)
    with pytest.raises(_Stop):
        bf._run(poll_s=0)
    rows = [json.loads(x) for x in (tmp_path / "ledger.jsonl").read_text().splitlines()]
    return op, [((r["tool"] or "-").split("@")[0], r["outcome"]) for r in rows]


def test_run_loop_waits_then_runs_apply_once_after_allow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    op, rows = _drive_run(
        tmp_path, monkeypatch, {2: lambda h: bf.rule("pactl apply teams.toml", "allow", h)}
    )
    assert op.ran == ["arailctl doctor", "arailctl drift", "pactl apply teams.toml"]
    assert rows[:4] == [
        ("arailctl.doctor", "acted"),
        ("arailctl.drift", "acted"),
        ("pactl.apply", "flagged"),
        ("pactl.apply", "acted"),
    ]
    assert rows[-1] == ("pactl.apply", "flagged")  # spent: waits again


def test_run_loop_does_not_wake_itself_after_a_deny(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, rows = _drive_run(
        tmp_path, monkeypatch, {2: lambda h: bf.rule("pactl apply teams.toml", "deny", h)}
    )
    # one flag before the ruling, one when the deny is consumed; nothing after
    assert rows.count(("pactl.apply", "flagged")) == 2


def test_run_loop_survives_the_rulings_file_being_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _drive_run(tmp_path, monkeypatch, {2: lambda h: (h / "rulings.jsonl").unlink()})


# -- adapter ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,b",
    [
        ("Llama-3.1-8B", 8.0),
        ("qwen2.5:14b", 14.0),
        ("Qwen3-8B-4bit", 8.0),
        ("Qwen3-30B-A3B-4bit", 30.0),  # MoE: total, not active, parameters
        ("Qwen2.5-7B-Instruct-4bit", 7.0),  # ARAIL's AEROLLM_MODEL default: below the 8B floor
    ],
)
def test_params_from_name_reads_the_total_count(name: str, b: float) -> None:
    assert params_from_name(name) == b


def test_params_from_name_reads_only_the_last_path_component() -> None:
    assert params_from_name("/Volumes/models/v2b-run/Qwen3-14B-4bit") == 14.0


def test_params_from_name_refuses_expert_product_names() -> None:
    with pytest.raises(ValueError):
        params_from_name("Mixtral-8x7B-Instruct-v0.1")


def test_adapter_complete_before_load_refuses() -> None:
    rt = QueueLLMRuntime(lambda: FakeBackend())
    with pytest.raises(RuntimeError):
        rt.complete("Qwen3-9B-4bit", "p")


def test_adapter_stops_claiming_residency_once_the_backend_closes() -> None:
    b = FakeBackend()
    rt = QueueLLMRuntime(lambda: b)
    rt.load("Qwen3-9B-4bit", pin=True)
    assert len(rt.loaded()) == 1
    b._runtime = None  # what AeroLLMBackend._close leaves behind
    assert rt.loaded() == []


# -- arailctl, in a throwaway copy (never the real lab) ----------------------------


def _box(tmp_path: Path) -> Path:
    box = tmp_path / "box"
    box.mkdir()
    shutil.copytree(REPO / "scripts", box / "scripts")
    shutil.copy2(REPO / "arailctl", box / "arailctl")
    shutil.copy2(REPO / "components.json", box / "components.json")
    (box / "lab" / "data").mkdir(parents=True)
    return box


def _ctl(box: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    e["HOME"] = str(box.parent / "home")
    e["PATH"] = f"{Path(sys.executable).parent}:/usr/bin:/bin"
    e.update(env or {})
    return subprocess.run(
        ["bash", "arailctl", *args],
        check=False,
        cwd=box, env=e, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
    )


def _activity(box: Path, lines: list[str]) -> None:
    (box / "lab" / "data" / "activity.jsonl").write_text("\n".join(lines) + "\n")


def _ev(delta: timedelta, msg: str, naive: bool = False) -> str:
    ts = datetime.now(timezone.utc) + delta
    return json.dumps(
        {"ts": (ts.replace(tzinfo=None) if naive else ts).isoformat(), "source": "t", "message": msg}
    )


def test_logs_since_keeps_the_window_and_skips_malformed_lines(tmp_path: Path) -> None:
    box = _box(tmp_path)
    _activity(
        box,
        [
            _ev(-timedelta(minutes=1), "recent"),
            _ev(-timedelta(minutes=30), "old"),
            _ev(-timedelta(minutes=1), "naive-utc", naive=True),
            "not json",
            "[1, 2]",
            "null",
            json.dumps({"ts": 1792687000, "message": "epoch"}),
            json.dumps({"message": "no ts"}),
        ],
    )
    with (box / "lab" / "data" / "activity.jsonl").open("ab") as f:
        f.write(b"\xff\xfe not utf-8\n")
    r = _ctl(box, "logs", "--since", "10m")
    assert r.returncode == 0, r.stderr
    assert "recent" in r.stdout and "naive-utc" in r.stdout
    assert "old" not in r.stdout and "epoch" not in r.stdout and "no ts" not in r.stdout


@pytest.mark.parametrize("bad", [[], ["5m"], ["1H"], ["10"], ["-1h"]])
def test_logs_since_refuses_an_undeclared_window(tmp_path: Path, bad: list[str]) -> None:
    box = _box(tmp_path)
    _activity(box, [_ev(-timedelta(minutes=1), "recent")])
    r = _ctl(box, "logs", "--since", *bad)
    assert r.returncode != 0 and "recent" not in r.stdout


def test_logs_since_excludes_future_timestamps(tmp_path: Path) -> None:
    box = _box(tmp_path)
    _activity(box, [_ev(timedelta(days=3), "from-the-future")])
    assert "from-the-future" not in _ctl(box, "logs", "--since", "2m").stdout


def test_rule_cli_argument_handling(tmp_path: Path) -> None:
    box = _box(tmp_path)
    venv = box / ".venv" / "bin"
    venv.mkdir(parents=True)
    pp = os.pathsep.join([str(REPO / "src"), *sys.path])
    (venv / "python").write_text(f'#!/bin/sh\nPYTHONPATH="{pp}" exec "{sys.executable}" "$@"\n')
    (venv / "python").chmod(0o755)
    env = {"QKZ_HOME": str(tmp_path / "qkz")}
    ok = _ctl(box, "rule", "pactl apply teams.toml", "allow", env=env)
    assert ok.returncode == 0, ok.stderr
    for bad in (["pactl apply teams.toml"], ["pactl apply teams.toml", "ALLOW"],
                ["pactl apply", "teams.toml", "allow"], []):
        assert _ctl(box, "rule", *bad, env=env).returncode != 0, bad
    rows = (tmp_path / "qkz" / "rulings.jsonl").read_text().splitlines()
    assert len(rows) == 1 and json.loads(rows[0])["command"] == "pactl apply teams.toml"


def test_rule_without_a_venv_says_so(tmp_path: Path) -> None:
    r = _ctl(_box(tmp_path), "rule", "pactl apply teams.toml", "allow")
    assert r.returncode != 0 and ".venv" in (r.stdout + r.stderr)
