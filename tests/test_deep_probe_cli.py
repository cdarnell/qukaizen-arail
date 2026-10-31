"""`./arailctl deep probe`: the dispatch, the help line and the documented contract (Q20).

C3 of sprints/2026-10-29-arail-deep-honest/ARCHITECTURE.md.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fakes.fake_deep_runtime import fake_deep  # noqa: F401  (fixture)

REPO = Path(__file__).resolve().parents[1]
ARAILCTL = REPO / "arailctl"
CLI_DOC = (REPO / "docs" / "cli.md").read_text(encoding="utf-8")


def arailctl(env, *args, cwd=None):
    return subprocess.run(["bash", str(ARAILCTL), *args], env=env, capture_output=True, text=True, timeout=120,
                          cwd=str(cwd or REPO))


def _env(fd=None, **extra):
    base = fd.env() if fd is not None else dict(os.environ)
    base["PYTHON"] = sys.executable
    base.setdefault("PATH", "/usr/bin:/bin")
    base["NO_COLOR"] = "1"
    base.update(extra)
    return base


def test_q20_the_help_lists_probe_and_its_flags():
    r = arailctl(_env(), "help")
    out = r.stdout + r.stderr
    line = " ".join(out.split("deep <op>", 1)[1].split("version", 1)[0].split())
    assert "probe [--json] [--force]" in line and "status" in line, line


def test_q20_dispatch_reaches_the_probe_module(fake_deep):
    fd = fake_deep("pass")
    r = arailctl(_env(fd), "deep", "probe", "--help")
    assert r.returncode == 0, r.stderr
    assert "usage: ./arailctl deep probe [--json] [--force]" in r.stdout
    r = arailctl(_env(fd), "deep", "probe", "--nonsense")
    assert r.returncode == 2 and "unknown argument" in r.stderr


def test_q20_a_real_run_through_arailctl(fake_deep):
    fd = fake_deep("fail2")
    r = arailctl(_env(fd), "deep", "probe", "--json")
    assert r.returncode == 1, r.stderr
    doc = json.loads(r.stdout)
    assert doc["schema"] == "arail.deep-probe/v1" and doc["passed"] == 1


def test_q20_the_usage_error_for_an_unknown_op_lists_probe():
    r = arailctl(_env(), "deep", "bogus")
    assert r.returncode != 0
    assert "install | rebuild | update | status | probe" in (r.stdout + r.stderr)


def test_q20_the_docs_carry_the_exit_code_row_and_the_probe_contract():
    row = next(ln for ln in CLI_DOC.splitlines() if ln.startswith("| `5` |"))
    assert "Not run" in row and "deep probe" in row and "nothing was recorded" in row
    assert "#### `deep probe [--json] [--force]`" in CLI_DOC
    for needle in ("ARAIL_DEEP_STATE_DIR", "ARAIL_DEEP_PROBE_CALL_TIMEOUT_S", "ARAIL_DEEP_PROBE_LOAD_TIMEOUT_S",
                   "arail.deep-probe/v1", "never clears a failure", "Only `./arailctl deep probe` can",
                   "Explicit only", "`teaching`"):
        assert needle in CLI_DOC, needle
    # every exit code the probe can return is in its own table
    section = CLI_DOC.split("#### `deep probe", 1)[1].split("### `reset", 1)[0]
    for code in ("`0`", "`1`", "`2`", "`5`", "`130`"):
        assert re.search(rf"^\| {re.escape(code)} \|", section, re.M), code


def test_q20_the_docs_say_what_check_does():
    section = CLI_DOC.split("#### `deep probe", 1)[1]
    assert "What the Models \"Check\" button does" in section
    assert "never generates, and it never clears a failure" in section


# ── QA-13 (fix pass 3): before ./arailctl setup, `deep probe` is "not run" (5) and the other ops are errors (1) ──
# docs/cli.md gives 1 the meaning "the probe ran and deep generation is failing". Exit 5 is "not run, nothing
# recorded". A script or CI step run on a fresh clone must not be told deep failed when nothing ran.

def _fresh_checkout(tmp_path: Path) -> Path:
    """A checkout that has never been set up: arailctl and its lib, no .venv."""
    co = tmp_path / "fresh-checkout"
    (co / "scripts").mkdir(parents=True)
    shutil.copy2(ARAILCTL, co / "arailctl")
    shutil.copytree(REPO / "scripts" / "lib", co / "scripts" / "lib")
    return co


def _run_fresh(co: Path, tmp_path: Path, *args: str, **extra: str):
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(tmp_path), "TMPDIR": str(tmp_path), "NO_COLOR": "1"}
    env.update(extra)
    return subprocess.run(["bash", str(co / "arailctl"), *args], env=env, cwd=co, capture_output=True, text=True,
                          timeout=60)


@pytest.mark.parametrize("flags", [[], ["--json"], ["--force"]], ids=["plain", "--json", "--force"])
def test_qa13_probe_before_setup_is_not_run_and_says_what_why_and_what_next(tmp_path, flags):
    r = _run_fresh(_fresh_checkout(tmp_path), tmp_path, "deep", "probe", *flags)
    assert r.returncode == 5, (r.returncode, r.stderr)
    for needle in ("not run", "missing .venv", "./arailctl setup", "Nothing was recorded"):
        assert needle in r.stderr, (needle, r.stderr)
    assert "deep install" not in r.stderr, "the old wording blamed the wrong op"
    assert r.stdout == "", "no JSON: there is no interpreter yet to build the document with"


@pytest.mark.parametrize("op", ["install", "rebuild", "update", "status"])
def test_qa13_every_other_op_before_setup_still_exits_1_and_names_itself(tmp_path, op):
    r = _run_fresh(_fresh_checkout(tmp_path), tmp_path, "deep", op)
    assert r.returncode == 1, (r.returncode, r.stderr)
    assert "missing .venv" in r.stderr and "./arailctl setup" in r.stderr
    assert f"deep {op} targets the lab's venv" in r.stderr, r.stderr


def test_qa13_deep_with_no_op_before_setup_names_status(tmp_path):
    r = _run_fresh(_fresh_checkout(tmp_path), tmp_path, "deep")
    assert r.returncode == 1 and "deep status targets the lab's venv" in r.stderr, r.stderr


def test_qa13_an_unknown_op_before_setup_gets_the_unknown_op_message(tmp_path):
    r = _run_fresh(_fresh_checkout(tmp_path), tmp_path, "deep", "bogus")
    assert r.returncode == 1
    assert "deep op must be one of: install | rebuild | update | status | probe" in r.stderr
    assert "missing .venv" not in r.stderr, "a typo is a typo whether or not the lab is set up"


def test_qa13_an_explicit_python_still_wins_over_the_missing_venv(tmp_path):
    r = _run_fresh(_fresh_checkout(tmp_path), tmp_path, "deep", "probe", "--help",
                   PYTHON=sys.executable, PYTHONPATH=str(REPO / "src"))
    assert r.returncode == 0, r.stderr
    assert "usage: ./arailctl deep probe [--json] [--force]" in r.stdout


def test_qa13_with_a_venv_the_probe_runs_through_it(tmp_path):
    co = _fresh_checkout(tmp_path)
    py = co / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text(f'#!/bin/sh\ntouch "{co}/venv-was-used"\nexec "{sys.executable}" "$@"\n')
    py.chmod(0o755)
    r = _run_fresh(co, tmp_path, "deep", "probe", "--help", PYTHONPATH=str(REPO / "src"))
    assert r.returncode == 0, r.stderr
    assert (co / "venv-was-used").exists(), "the lab's own interpreter ran the probe"


def test_qa13_the_docs_name_the_case_in_the_exit_5_row():
    row = next(ln for ln in CLI_DOC.splitlines() if ln.startswith("| `5` |"))
    assert "setup" in row and ".venv" in row, row
