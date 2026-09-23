"""A hardened, read-only git subprocess runner.

Shared by corpus/sources/git_kernel.py (commit 10, `git log`) and
evals/executable_kernel.py (commit 15, `git apply --check`) — both need
"run git against an untrusted or operator-provided local repo without
letting it execute anything or touch any working tree." Neither ever
does a checkout; both are read-only or index-only operations.

Threat model (ARCHITECTURE.md failure mode 17 / T-SEC-GIT-1): a local
repo with a malicious `post-checkout` hook or a `core.fsmonitor` command
config. Hooks and fsmonitor are disabled, no system/global config is
read, and no network protocol is ever allowed.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

DEFAULT_TIMEOUT = 30.0


def hardened_env(home_dir: str) -> dict:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "HOME": home_dir,
    }
    return env


def run_git(args: List[str], *, cwd: Path, timeout: float = DEFAULT_TIMEOUT,
           input_bytes: Optional[bytes] = None) -> subprocess.CompletedProcess:
    """Run a git subcommand with hooks, fsmonitor, and network protocols
    disabled, and no system/global config. Raises on timeout (caller
    decides how to report it) rather than hanging a build forever."""
    with tempfile.TemporaryDirectory() as tmp_home:
        env = hardened_env(tmp_home)
        full_args = [
            "git",
            "-c", "core.hooksPath=/dev/null",
            "-c", "core.fsmonitor=false",
            "-c", "protocol.allow=never",
            *args,
        ]
        return subprocess.run(
            full_args, cwd=str(cwd), env=env, capture_output=True,
            timeout=timeout, input=input_bytes,
        )
