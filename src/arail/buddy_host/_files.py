"""Owner-only file writes for the edge's state (link, ledger, budget)."""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path


class SymlinkRefused(OSError):
    """The target (or its directory) is a symlink; the edge never writes through one."""


def ensure_private_dir(path: Path) -> Path:
    path = Path(path)
    if os.path.islink(path):
        raise SymlinkRefused(f"{path} is a symlink; refusing to use it")
    os.makedirs(path, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def write_private(path: Path, data: bytes) -> None:
    """Atomic write, mode 0600, never through a symlink."""
    path = Path(path)
    if os.path.islink(path):
        raise SymlinkRefused(f"{path} is a symlink; refusing to write through it")
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def create_private_file(path: Path) -> None:
    """Make sure ``path`` exists as a regular 0600 file (created empty if missing), never via a symlink."""
    path = Path(path)
    if os.path.islink(path):
        raise SymlinkRefused(f"{path} is a symlink; refusing to use it")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    os.close(fd)
    if stat.S_IMODE(os.lstat(path).st_mode) != 0o600:
        os.chmod(path, 0o600)
