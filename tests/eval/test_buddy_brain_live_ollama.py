"""Buddy brain A/B harness against the real, local Ollama (smoke, T-H8).

Runs a short, 2-items-per-category pass of ``llama-ai-eng`` against stock
``qwen3:1.7b`` (both must already be installed; nothing is downloaded) and checks the
plumbing: results.json has the ``arail.buddy_brain_ab/v1`` shape, the thinking-capable
arm was sent ``think=false`` and returned no empty reply, and a subset run gives no
verdict. Auto-skipped when Ollama or either model is missing."""
from __future__ import annotations

import json

import pytest

from arail.brain_bench import ollama

pytestmark = pytest.mark.requires_ollama

BASE, STOCK = "llama-ai-eng:latest", "qwen3:1.7b"


def _installed() -> bool:
    try:
        tags = ollama.Ollama().tags()
    except Exception:  # noqa: BLE001
        return False
    return BASE in tags and STOCK in tags


@pytest.fixture(autouse=True)
def _need_models():
    if not _installed():
        pytest.skip(f"needs {BASE} and {STOCK} installed in a loopback Ollama (nothing is pulled)")


def test_smoke_run_over_two_local_models(tmp_path):
    from arail.brain_bench import run as R
    out = tmp_path / "run"
    rc = R.run({"baseline": BASE, "candidate": STOCK, "reference": STOCK, "near_aa": [],
                "out": str(out), "max_minutes": 15.0, "limit_per_category": 2})
    assert rc in (R.EXIT_OK, R.EXIT_UNTRUSTED)
    res = json.loads((out / "results.json").read_text())
    assert res["schema"] == "arail.buddy_brain_ab/v1" and res["complete"] is True and res["subset"] == 2
    for key in ("run_id", "git_sha", "tasks", "rules", "vendor", "ollama", "sampling", "arms", "metrics",
                "comparisons", "self_checks", "verdict", "failures", "latency"):
        assert key in res
    assert res["ollama"]["loopback"] is True and res["verdict"]["printed"] is False
    arms = {a["id"]: a for a in res["arms"]}
    assert arms["reference"]["thinking_capable"] is True and arms["reference"]["think_sent"] is False
    assert arms["baseline"]["thinking_capable"] is False and arms["baseline"]["think_sent"] is None
    rows = [json.loads(line) for line in (out / "rows.jsonl").read_text().splitlines()]
    qwen = [r for r in rows if r["arm"] == "reference" and not r["error"]]
    assert qwen and all(r["content"].strip() for r in qwen), "an empty reply means thinking ate the token cap"
    assert not any("<think>" in r["content"] for r in rows)
    sc = {c["id"]: c for c in res["self_checks"]}
    assert sc["SC5"]["passed"] is True and sc["SC6"]["passed"] is True and sc["SC8"]["passed"] is True
