"""Executable kernel checks: patch_applies, checkpatch_clean, compiles
(ARCHITECTURE.md §4.9).

Every input here is an untrusted, model-generated patch. Nothing is ever
written to any working tree (``--cached`` only, against a throwaway
``GIT_INDEX_FILE``); ``--unsafe-paths`` is never passed; ``compiles`` never
executes model output — there is no code path in this module that runs a
compiler or a Makefile.
"""

from __future__ import annotations

import re
import resource
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from arail.nucleus.corpus._git_env import hardened_env

_MAX_PATCH_BYTES = 64 * 1024
_TIMEOUT_S = 30.0
_CPU_LIMIT_S = 20
_AS_LIMIT_BYTES = 512 * 1024 * 1024

_PATH_RE = re.compile(r"^(?:\+\+\+|---|diff --git) (?:a/|b/)?(.+?)(?:\s|$)", re.MULTILINE)
_SYMLINK_MODE_RE = re.compile(r"^new mode 120000", re.MULTILINE)


class NotRun:
    def __init__(self, reason: str):
        self.reason = reason

    def __repr__(self) -> str:
        return f"NotRun({self.reason!r})"

    def __eq__(self, other) -> bool:
        return isinstance(other, NotRun) and self.reason == other.reason


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    reason: str
    timed_out: bool = False


def _hostile_patch_reason(patch_bytes: bytes) -> Optional[str]:
    if len(patch_bytes) > _MAX_PATCH_BYTES:
        return f"patch exceeds {_MAX_PATCH_BYTES}-byte cap"
    if b"\x00" in patch_bytes:
        return "binary or NUL-byte content"
    try:
        text = patch_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return "not valid UTF-8 text"

    if _SYMLINK_MODE_RE.search(text):
        return "patch creates a symlink"

    for match in _PATH_RE.finditer(text):
        path = match.group(1).strip()
        if path.startswith("/"):
            return f"absolute path in patch: {path!r}"
        if ".." in Path(path).parts:
            return f"path traversal in patch: {path!r}"
    return None


def patch_applies(patch_text: str, *, base_repo: Path, base_commit: str) -> CheckResult:
    patch_bytes = patch_text.encode("utf-8", errors="surrogateescape")
    hostile = _hostile_patch_reason(patch_bytes)
    if hostile is not None:
        return CheckResult(ok=False, reason=hostile)

    base_repo = Path(base_repo)
    with tempfile.TemporaryDirectory() as tmp:
        index_file = Path(tmp) / "index"
        home_dir = Path(tmp) / "home"
        home_dir.mkdir()
        env = hardened_env(str(home_dir))
        env["GIT_INDEX_FILE"] = str(index_file)

        read_tree = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
             "-c", "protocol.allow=never", "read-tree", base_commit],
            cwd=str(base_repo), env=env, capture_output=True, timeout=_TIMEOUT_S,
        )
        if read_tree.returncode != 0:
            return CheckResult(ok=False,
                               reason=f"git read-tree failed: {read_tree.stderr.decode(errors='replace')[:300]}")

        try:
            apply_check = subprocess.run(
                ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                 "-c", "protocol.allow=never", "apply", "--check", "--cached", "-"],
                cwd=str(base_repo), env=env, input=patch_bytes,
                capture_output=True, timeout=_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return CheckResult(ok=False, reason="git apply --check timed out", timed_out=True)

        if apply_check.returncode == 0:
            return CheckResult(ok=True, reason="applies cleanly")
        return CheckResult(ok=False, reason=apply_check.stderr.decode(errors="replace")[:300])


def _set_rlimits() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (_CPU_LIMIT_S, _CPU_LIMIT_S))
    try:
        resource.setrlimit(resource.RLIMIT_AS, (_AS_LIMIT_BYTES, _AS_LIMIT_BYTES))
    except (ValueError, OSError):
        pass  # RLIMIT_AS isn't enforced the same way on every platform


def checkpatch_clean(patch_text: str, *, checkpatch_path: Path) -> CheckResult:
    patch_bytes = patch_text.encode("utf-8", errors="surrogateescape")
    hostile = _hostile_patch_reason(patch_bytes)
    if hostile is not None:
        return CheckResult(ok=False, reason=hostile)

    checkpatch_path = Path(checkpatch_path)
    if not checkpatch_path.is_file():
        return CheckResult(ok=False, reason=f"checkpatch not found at {checkpatch_path}")

    is_perl = checkpatch_path.suffix == ".pl"
    cmd = (["perl", str(checkpatch_path)] if is_perl else
          ["python3", str(checkpatch_path)]) + ["--no-tree", "--terse", "-"]

    try:
        result = subprocess.run(
            cmd, input=patch_bytes, capture_output=True, timeout=_TIMEOUT_S,
            preexec_fn=_set_rlimits if hasattr(resource, "setrlimit") else None,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(ok=False, reason="checkpatch timed out", timed_out=True)
    except OSError as exc:
        return CheckResult(ok=False, reason=f"checkpatch failed to start: {exc}")

    if result.returncode == 0:
        return CheckResult(ok=True, reason="clean")
    return CheckResult(ok=False, reason=result.stdout.decode(errors="replace")[:500] or
                       result.stderr.decode(errors="replace")[:500])


def compiles() -> NotRun:
    """Always NotRun this sprint — no Linux build host (ARCHITECTURE.md
    §4.9). There is no subprocess call anywhere in this function; model
    output is never executed, by construction (T-EXEC-3)."""
    return NotRun("requires a Linux build host; not available in sprint 1")
