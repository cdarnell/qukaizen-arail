"""QA, sprint 2026-10-29-arail-deep-honest: no egress, import hygiene, and the happy path end to end.

Written by the QA pass (sprints/2026-10-29-arail-deep-honest/TEST_REPORT.md). Every test here is a
guard that passes today: it pins what the sprint promises (no network on the verdict, policy, Curator
report and status paths; each touched module imports alone; a passing runtime clears deep and a later
failure flips it back) so a regression turns it red. Fake runtime only.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from arail import deep_verdict as dv
from arail.agents import deep_policy
from arail.portal import world_routes as wr
from tests.fakes.fake_deep_runtime import fake_deep  # noqa: F401  (fixture)
from tests.test_curator_honest import Scripted, _review, client, world  # noqa: F401  (fixtures)
from tests.test_deep_verdict import probe_result

REPO = Path(__file__).resolve().parents[1]
GUARD = (
    "import os, socket\n"
    "_log = os.environ.get('QA_EGRESS_LOG')\n"
    "def _no(*a, **k):\n"
    "    if _log:\n"
    "        open(_log, 'a').write(repr(a)[:200] + '\\n')\n"
    "    raise OSError('QA egress guard: a network call was attempted')\n"
    "socket.socket.connect = _no\nsocket.socket.connect_ex = _no\n"
    "socket.create_connection = _no\nsocket.getaddrinfo = _no\n"
)


@pytest.fixture()
def no_network(monkeypatch):
    """Any connect or DNS lookup in this process raises and is recorded."""
    attempts: list = []

    def _no(*a, **k):
        attempts.append(repr(a)[:200])
        raise OSError("QA egress guard: a network call was attempted")

    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, _no)
    monkeypatch.setattr(socket, "create_connection", _no)
    monkeypatch.setattr(socket, "getaddrinfo", _no)
    return attempts


@pytest.fixture()
def gates(monkeypatch):
    monkeypatch.delenv("ARAIL_AGENT_DEEP", raising=False)
    monkeypatch.setenv("LAB_TIER", "maximus")
    from arail import runtime_profile, scheduler
    from arail.router import mlx_guard
    monkeypatch.setattr(scheduler, "jobs_halted", lambda: False)
    monkeypatch.setattr(scheduler, "current_window", lambda *a, **k: "heavy")
    monkeypatch.setattr(runtime_profile, "resolve", lambda *a, **k: ("overnight", "t"))
    monkeypatch.setattr(runtime_profile, "params", lambda p: {"background_aerollm": True})
    monkeypatch.setattr(mlx_guard, "metal_memory_pressure", lambda: 0.10)
    return monkeypatch


# ── egress: the sprint adds no network call ─────────────────────────────────────────────────────────

def test_qa_guard_the_verdict_policy_overlay_and_curator_report_paths_make_no_network_call(
        fake_deep, gates, no_network, world, monkeypatch, client):
    fake_deep("fail2")
    b = dv.identity()
    dv.note_call(False, error="RuntimeError: rms_norm", consumer="agent:buddy")
    dv.note_load_failure("could not start", persist=True)
    assert dv.current().state == "failing"
    for fg in (True, False):
        assert deep_policy.explain(foreground=fg)[1] == "deep_failing"
    dv.status_lines()
    dv.health_detail(dv.current())
    dv.record_probe(probe_result(b, "fail", passed=1, started_ts=dv._now(),
                                 first_error={"call": 2, "kind": "long", "error": "e"}))
    dv.record_probe(probe_result(b, "pass", passed=5, started_ts=dv._now() + 1))
    from arail.registry.core import _deep_overlay
    from arail.registry.store import TIER1_ID
    from arail.registry import get_registry
    entry = get_registry().entries.get(TIER1_ID)
    if entry is not None:
        _deep_overlay(entry)
    world.mount(6)
    monkeypatch.setattr(wr, "_review_router", lambda: Scripted(name="aerollm", fail_from=2))
    st = _review(client)                                  # the whole Curator report path, in the portal
    assert st["outcome"] == "partial"
    assert no_network == [], f"network calls attempted: {no_network}"


def test_qa_guard_deep_status_and_the_build_script_status_make_no_network_call(fake_deep, tmp_path):
    fd = fake_deep("pass")
    guard = tmp_path / "guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(GUARD, encoding="utf-8")
    log = tmp_path / "egress.log"
    e = fd.env(QA_EGRESS_LOG=str(log), PYTHON=sys.executable, ARAIL_AEROLLM_REPO=str(tmp_path / "no-checkout"),
               NO_COLOR="1")
    e["PYTHONPATH"] = os.pathsep.join([str(guard), e["PYTHONPATH"]])
    r1 = subprocess.run([sys.executable, "-m", "arail.deep_verdict", "status"], env=e, capture_output=True,
                        text=True, timeout=60)
    r2 = subprocess.run(["bash", str(REPO / "scripts" / "build-aerollm.sh"), "status"], env=e, capture_output=True,
                        text=True, timeout=60)
    assert r1.returncode == 0 and r2.returncode == 0, r1.stderr + r2.stderr
    assert "generation:" in r1.stdout and "generation:" in r2.stdout
    assert not log.exists(), f"network calls attempted: {log.read_text() if log.exists() else ''}"


# ── secrets: no key, token or index password reaches an output or a file ────────────────────────────

CANARIES = {"ANTHROPIC_API_KEY": "sk-ant-QA-CANARY-anthropic-0001", "HF_TOKEN": "hf_QACANARYtoken0002xxxxxxxxxx",
            "OPENAI_API_KEY": "sk-QA-CANARY-openai-0004",
            "PIP_INDEX_URL": "https://qa-user:QA-CANARY-pass-0003@pypi.example.invalid/simple/"}


def _secret_hits(blobs: dict) -> list:
    needles = list(CANARIES.values()) + ["QA-CANARY-pass-0003"]
    return [f"{where}: {n[:24]}" for where, text in blobs.items() for n in needles if n in text]


def test_qa_guard_canary_secrets_never_reach_any_output_or_file(fake_deep, tmp_path):
    fd = fake_deep("fail2")
    e = fd.env(**CANARIES, PYTHON=sys.executable, ARAIL_AEROLLM_REPO=str(tmp_path / "no-checkout"), NO_COLOR="1")
    runs = {
        "deep probe": [sys.executable, "-m", "arail.deep_probe"],
        "deep probe --json": [sys.executable, "-m", "arail.deep_probe", "--json"],
        "deep_verdict status": [sys.executable, "-m", "arail.deep_verdict", "status"],
        "build-aerollm.sh status": ["bash", str(REPO / "scripts" / "build-aerollm.sh"), "status"],
    }
    blobs = {}
    for name, cmd in runs.items():
        r = subprocess.run(cmd, env=e, capture_output=True, text=True, timeout=120)
        blobs[name + " stdout"], blobs[name + " stderr"] = r.stdout, r.stderr
    assert "FAILING" in blobs["deep_verdict status stdout"], "the runs did write a failure"
    for root in (dv.state_dir(), fd.root):
        for p in root.rglob("*"):
            if p.is_file() and p.stat().st_size < 5_000_000:
                blobs[str(p)] = p.read_text(encoding="utf-8", errors="replace")
    assert any(k.endswith("verdict.json") for k in blobs) and any(k.endswith("activity.jsonl") for k in blobs)
    assert _secret_hits(blobs) == []


@pytest.mark.xfail(strict=True, reason="pre-existing (the `index:` line predates the sprint): deep status echoes "
                                       "AEROLLM_INDEX_URL verbatim, so credentials written into that URL are printed")
def test_qa_preexisting_deep_status_does_not_print_credentials_from_the_index_url(fake_deep, tmp_path):
    fd = fake_deep("pass")
    e = fd.env(PYTHON=sys.executable, ARAIL_AEROLLM_REPO=str(tmp_path / "no-checkout"), NO_COLOR="1",
               AEROLLM_INDEX_URL="https://qa-user:QA-CANARY-pass-0003@pypi.qukaizen.com/simple/")
    r = subprocess.run(["bash", str(REPO / "scripts" / "build-aerollm.sh"), "status"], env=e, capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0 and "QA-CANARY-pass-0003" not in r.stdout + r.stderr


# ── import hygiene: each module the sprint touched imports alone, with no cycle ─────────────────────

def _fresh(code: str, tmp_path: Path) -> subprocess.CompletedProcess:
    e = {k: v for k, v in os.environ.items() if not k.startswith(("ARAIL_", "AERO", "LAB_"))}
    e.update(PYTHONPATH=str(REPO / "src"), PYTHONDONTWRITEBYTECODE="1",
             ARAIL_ENV_FILE=str(tmp_path / "absent.env"), ARAIL_MODEL_DEFAULTS_FILE=str(tmp_path / "absent.yaml"),
             ARAIL_DATA_DIR=str(tmp_path / "data"), ARAIL_DEEP_STATE_DIR=str(tmp_path / "deep"))
    return subprocess.run([sys.executable, "-c", code], env=e, capture_output=True, text=True, timeout=120,
                          cwd=str(tmp_path))


@pytest.mark.parametrize("module", ["arail.deep_verdict", "arail.deep_probe", "arail.agents.deep_policy",
                                    "arail.router.backends", "arail.portal.world_routes", "arail.portal.app"])
def test_qa_guard_each_touched_module_imports_alone_in_a_fresh_interpreter(module, tmp_path):
    r = _fresh(f"import {module}", tmp_path)
    assert r.returncode == 0, r.stderr[-2000:]


def test_qa_guard_the_backend_does_not_pull_in_the_portal_and_the_verdict_is_stdlib_only(tmp_path):
    code = ("import sys\n"
            "import arail.router.backends\n"
            "print('portal' if any(m.startswith('arail.portal') for m in sys.modules) else 'no-portal')\n"
            "before = set(sys.modules)\n")
    r = _fresh(code, tmp_path)
    assert r.returncode == 0 and r.stdout.strip() == "no-portal", r.stdout + r.stderr
    code2 = ("import sys\n"
             "before = set(sys.modules)\n"
             "import arail.deep_verdict\n"
             "new = {m.split('.')[0] for m in set(sys.modules) - before}\n"
             "print(','.join(sorted(new - set(sys.stdlib_module_names) - {'arail'})))\n")
    r2 = _fresh(code2, tmp_path)
    assert r2.returncode == 0, r2.stderr
    assert r2.stdout.strip() == "", f"deep_verdict promises stdlib only; it imported: {r2.stdout.strip()}"


# ── happy path: pass, then a failure flips it, then a second probe clears it (three processes) ──────

def _status(fd) -> str:
    r = subprocess.run([sys.executable, "-m", "arail.deep_verdict", "status"], env=fd.env(), capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _probe(fd) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "arail.deep_probe", "--json"], env=fd.env(), capture_output=True,
                          text=True, timeout=120)


def test_qa_guard_happy_path_pass_then_a_failure_then_a_second_probe_agree_everywhere(fake_deep, gates):
    fd = fake_deep("pass")
    import arail.portal.app as app_mod

    r = _probe(fd)
    doc = json.loads(r.stdout)
    assert r.returncode == 0 and doc["outcome"] == "pass" and doc["passed"] == 5, r.stderr
    assert set(doc["teaching"]) >= {"passed", "total", "median_ok_s"}
    st = _status(fd)
    assert "generation:   passing — 5/5 at the last probe" in st and "Bar: 5 of 5" in st
    dv._reset_for_tests()
    dv.identity()
    assert dv.current().state == "passing"
    assert deep_policy.explain(foreground=False)[:2] == (True, "ok")
    block = app_mod._research_models_block()["deep"]
    assert (block["reason_code"], block["generation"]["state"]) == ("ok", "passing")

    # a deep call fails in another lab of the same checkout (a World instance)
    code = ("from arail import deep_verdict as dv\n"
            "dv.identity()\n"
            "dv.note_call(False, error='RuntimeError: rms_norm has 1 elements', consumer='agent:buddy')\n")
    subprocess.run([sys.executable, "-c", code], env=fd.env(ARAIL_INSTANCE="ai"), check=True, timeout=60)
    assert dv.current().state == "failing" and "in ai (agent:buddy)" in dv.current().detail
    assert deep_policy.explain(foreground=False)[1] == "deep_failing"
    block = app_mod._research_models_block()["deep"]
    assert (block["reason_code"], block["generation"]["state"], block["generation"]["lab"]) == \
        ("deep_failing", "failing", "ai")
    st = _status(fd)
    assert "generation:   FAILING — a deep call failed in lab 'ai' (agent:buddy)" in st

    # the operator re-runs the probe; the build answers 5 of 5 again
    r = _probe(fd)
    assert r.returncode == 0 and json.loads(r.stdout)["verdict_store"]["still_failing"] is False, r.stdout
    assert dv.current().state == "passing"
    assert deep_policy.explain(foreground=False)[:2] == (True, "ok")
    assert "generation:   passing" in _status(fd)
    assert len(fd.gen_calls()) == 10, "two probes, five calls each; reading the surfaces generated nothing"
