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

B8 (2026-09-23 review): the repo's own *local* ``.git/config`` is still
honored by default, so a repo carrying ``gpg.program`` + a signed commit
(with ``log.showSignature=true``), or ``core.pager``/``diff.external``
set to an arbitrary command, could still get git to execute something.
``HARDENED_CONFIG_ARGS`` neutralizes every repo-config-driven exec vector
this module knows about, in addition to the hooks/fsmonitor/protocol
overrides above — every call site (``run_git`` and the two inline
``executable_kernel.py`` invocations) must apply it.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

DEFAULT_TIMEOUT = 30.0

# Repo-local `.git/config` can still point at an arbitrary program via
# these keys even with hooks/fsmonitor/protocol.allow locked down above.
# `-c` overrides here always win over the repo's own config for the
# single invocation, without touching the repo on disk.
HARDENED_CONFIG_ARGS: List[str] = [
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.fsmonitor=false",
    "-c", "protocol.allow=never",
    "-c", "gpg.program=/usr/bin/false",
    "-c", "log.showSignature=false",
    "-c", "core.pager=cat",
    "-c", "diff.external=",
    "-c", "core.sshCommand=/usr/bin/false",
]

# Extra flags for `git log` specifically -- belt-and-suspenders on top of
# the -c overrides above, since these are documented as the canonical way
# to force off signature verification / external diff / textconv for a
# single invocation regardless of what the repo's config says.
LOG_SAFETY_ARGS: List[str] = ["--no-show-signature", "--no-ext-diff", "--no-textconv"]


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
    """Run a git subcommand with hooks, fsmonitor, network protocols, and
    the repo-config exec vectors in HARDENED_CONFIG_ARGS all disabled, and
    no system/global config. `git log` additionally gets LOG_SAFETY_ARGS
    inserted right after the subcommand. Raises on timeout (caller decides
    how to report it) rather than hanging a build forever."""
    with tempfile.TemporaryDirectory() as tmp_home:
        env = hardened_env(tmp_home)
        subcommand_args = list(args)
        if subcommand_args and subcommand_args[0] == "log":
            subcommand_args = [subcommand_args[0], *LOG_SAFETY_ARGS, *subcommand_args[1:]]
        full_args = ["git", *HARDENED_CONFIG_ARGS, *subcommand_args]
        return subprocess.run(
            full_args, cwd=str(cwd), env=env, capture_output=True,
            timeout=timeout, input=input_bytes,
        )
