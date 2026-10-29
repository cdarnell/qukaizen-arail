"""`./arailctl deep probe`: the dispatch, the help line and the documented contract (Q20).

C3 of sprints/2026-10-29-arail-deep-honest/ARCHITECTURE.md.
"""
from __future__ import annotations

import json
import os
import re
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
