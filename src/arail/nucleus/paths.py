"""Layout, id/slug/version validation, the repo git-ignore guard, and the
build lock (ARCHITECTURE.md §4.3).

Two roots:
    NUCLEUS_DATA  = DATA_DIR/"nucleus"        private intermediates (0700)
    FORGE_ROOT    = ARAIL_MODELS_DIR/"forge"   shard output

Both are computed lazily (functions, not module-level constants) so tests
can override ``arail.config.DATA_DIR`` / ``MODELS_DIR`` via monkeypatch and
have every path in this module follow.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from arail.nucleus.errors import RefusedByPolicy

_BUILD_ID_RE = re.compile(r"^[a-z0-9-]{1,63}-\d{8}T\d{6}Z-[0-9a-f]{4}$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
_SHARD_RE = re.compile(r"^[a-z0-9-]{1,64}$")


def nucleus_data() -> Path:
    from arail.config import DATA_DIR

    return Path(DATA_DIR) / "nucleus"


def forge_root() -> Path:
    from arail.config import MODELS_DIR

    return Path(MODELS_DIR) / "forge"


def _mkdir_private(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def ensure_nucleus_data_layout() -> Path:
    """Create NUCLEUS_DATA and its fixed subdirectories (0700), guarded."""
    root = nucleus_data()
    guard_committable_output(root)
    for sub in ("keys", "corpus", "domains", "runs"):
        _mkdir_private(root / sub)
    _mkdir_private(root)
    return root


def new_build_id(domain: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(2)
    build_id = f"{domain}-{ts}-{suffix}"
    if not _BUILD_ID_RE.match(build_id):
        raise RefusedByPolicy(f"generated build_id {build_id!r} failed its own format check")
    return build_id


def validate_build_id(build_id: str) -> str:
    if not _BUILD_ID_RE.match(build_id or ""):
        raise RefusedByPolicy(
            f"build_id must match ^[a-z0-9-]{{1,63}}-\\d{{8}}T\\d{{6}}Z-[0-9a-f]{{4}}$, got {build_id!r}"
        )
    return build_id


def run_dir(build_id: str) -> Path:
    validate_build_id(build_id)
    return nucleus_data() / "runs" / build_id


def cert_dir(domain: str, cert_version: str) -> Path:
    return nucleus_data() / "domains" / domain / "cert" / cert_version


def dev_dir(domain: str) -> Path:
    return nucleus_data() / "domains" / domain / "dev"


def shard_dir(shard: str, version: str) -> Path:
    """A realpath strictly under FORGE_ROOT. Version is strict semver.
    An existing version dir is never overwritten (T-PATH-3)."""
    if not _SHARD_RE.match(shard or ""):
        raise RefusedByPolicy(f"shard must match ^[a-z0-9-]{{1,64}}$, got {shard!r}")
    if not _SEMVER_RE.match(version or ""):
        raise RefusedByPolicy(f"version must be strict semver (X.Y.Z), got {version!r}")

    root = forge_root()
    guard_committable_output(root)
    candidate = (root / shard / version).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise RefusedByPolicy(f"resolved shard path {candidate} escapes FORGE_ROOT {root}") from exc
    if candidate.exists():
        raise RefusedByPolicy(
            f"{candidate} already exists — pick a different --version "
            f"(the default is the next patch release)"
        )
    return candidate


def next_patch_version(shard: str) -> str:
    """The lowest X.Y.(Z+1) not already present under FORGE_ROOT/<shard>/,
    starting from 0.1.0 if nothing exists yet."""
    root = forge_root() / shard
    if not root.is_dir():
        return "0.1.0"
    best = (0, 0, 0)
    for child in root.iterdir():
        if child.is_dir() and _SEMVER_RE.match(child.name):
            parts = tuple(int(p) for p in child.name.split("."))
            if parts > best:
                best = parts
    major, minor, patch = best
    return f"{major}.{minor}.{patch + 1}"


# ── git-ignore guard ────────────────────────────────────────────────

def guard_committable_output(path: Path) -> None:
    """If *path* resolves inside a git worktree, it must be git-ignored.
    Refuses (RefusedByPolicy, mapped to exit 3) rather than risk a shard or
    private key becoming committable."""
    resolved = path.resolve()
    repo_root = _find_git_root(resolved)
    if repo_root is None:
        return  # not inside any git worktree — nothing to guard

    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", str(resolved)],
            cwd=str(repo_root), capture_output=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RefusedByPolicy(f"could not verify {resolved} is git-ignored: {exc}") from exc

    if result.returncode != 0:
        raise RefusedByPolicy(
            f"output would be committable: {resolved} is inside a git worktree "
            f"({repo_root}) and is not git-ignored. Set ARAIL_DATA_DIR/"
            f"ARAIL_MODELS_DIR to a location outside the repo, or add it to "
            f".gitignore, before running this command."
        )


def _find_git_root(path: Path) -> Optional[Path]:
    current = path if path.is_dir() else path.parent
    for _ in range(64):
        if (current / ".git").exists():
            return current
        if current.parent == current:
            return None
        current = current.parent
    return None


# ── build lock ──────────────────────────────────────────────────────

def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours
    return True


@contextmanager
def build_lock(build_id: str) -> Iterator[None]:
    """Exclusive, non-blocking lock on NUCLEUS_DATA/build.lock.

    A second concurrent build refuses (RefusedByPolicy) naming the running
    build_id. A stale lock (recorded pid no longer alive) is taken over with
    a warning printed to stderr (T-LOCK-1, T-LOCK-2).
    """
    root = ensure_nucleus_data_layout()
    lock_path = root / "build.lock"
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            existing = _read_lock_payload(lock_path)
            pid = existing.get("pid") if existing else None
            if pid is not None and not _pid_alive(int(pid)):
                import sys

                sys.stderr.write(
                    f"nucleus: taking over a stale lock from dead pid {pid} "
                    f"(build_id={existing.get('build_id')})\n"
                )
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise RefusedByPolicy(
                        "another nucleus build is running (lost the race to take over a stale lock)"
                    ) from exc
            else:
                running = existing.get("build_id") if existing else "unknown"
                raise RefusedByPolicy(
                    f"another nucleus build is already running: {running} "
                    f"(pid {pid}). Wait for it to finish, or if you're sure "
                    f"it's dead, remove {lock_path}."
                )

        payload = json.dumps({
            "pid": os.getpid(), "build_id": build_id,
            "started": datetime.now(timezone.utc).isoformat(),
        }).encode()
        os.ftruncate(fd, 0)
        os.pwrite(fd, payload, 0)
        os.fsync(fd)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _read_lock_payload(lock_path: Path) -> Optional[dict]:
    try:
        text = lock_path.read_text().strip()
        if not text:
            return None
        return json.loads(text)
    except (OSError, ValueError):
        return None
