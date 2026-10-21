"""`./arailctl status --json` gains an optional, additive `models` object
(sprints/2026-10-20-boot-model-autoresolve): the last-resolved slot A/B
cache, read from model_defaults.yaml with no network and no model load. The
schema stays arail.status/v2 (additive keys only, docs/cli.md)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")

_YAML = """version: 2
pins: {a: null, b: none}
resolved:
  a: {model: llama-ai-eng:latest, source: auto, reason: null, size_gb: 1.3}
  b: {model: null, source: pin, reason: pinned off, size_gb: null}
  warm: {a: ok, b: skipped}
"""


def _status(tmp_path, *args, yaml_text=None):
    f = tmp_path / "model_defaults.yaml"
    if yaml_text is not None:
        f.write_text(yaml_text)
    env = dict(os.environ)
    env["ARAIL_MODEL_DEFAULTS_FILE"] = str(f)
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
    return subprocess.run(["bash", str(REPO_ROOT / "scripts" / "status.sh"),
                           "--no-probe", "--no-sizes", *args],
                          capture_output=True, text=True, env=env, cwd=REPO_ROOT,
                          timeout=60)


def test_json_has_additive_models_block(tmp_path):
    out = _status(tmp_path, "--json", yaml_text=_YAML)
    doc = json.loads(out.stdout)
    assert doc["schema"] == "arail.status/v2"
    assert doc["models"] == {
        "a": {"model": "llama-ai-eng:latest", "source": "auto", "warm": "ok"},
        "b": {"model": None, "source": "pin", "warm": "skipped"}}


def test_no_cache_means_no_models_key_and_unchanged_schema(tmp_path):
    doc = json.loads(_status(tmp_path, "--json").stdout)
    assert doc["schema"] == "arail.status/v2" and "models" not in doc


def test_instances_mode_is_untouched(tmp_path):
    out = _status(tmp_path, "--json=instances", yaml_text=_YAML)
    assert isinstance(json.loads(out.stdout), list)


def test_human_renderer_agrees_with_json(tmp_path):
    out = _status(tmp_path, "-q", yaml_text=_YAML)
    assert "models: fast llama-ai-eng:latest (auto, warm ok)" in out.stdout
