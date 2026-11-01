"""SQLite connection management for the ARAIL 2.0 relational store.

Single-writer by assumption. Multi-process concurrent writers, networked
deployment, and per-world schema variation are explicit non-goals — do not
design around them.

The database file lives beside the rest of a lab's mutable state, at
``<data_dir>/arail.db``, so a World instance's relational store is scoped by
exactly the same directory boundary as its Lance tables and secrets. That is
deliberate: in 1.x the tenant boundary was the process's frozen env, and
nothing in the storage layer recorded which world a row belonged to.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

__all__ = [
    "DB_FILENAME", "database_path", "connect", "transaction",
    "applied_version", "record_version", "DatabaseError",
]

DB_FILENAME = "arail.db"


class DatabaseError(RuntimeError):
    """Something went wrong at the storage layer. Message says what to do."""


def database_path(data_dir: str | os.PathLike[str]) -> Path:
    return Path(data_dir) / DB_FILENAME


def connect(data_dir: str | os.PathLike[str], *,
            create: bool = True) -> sqlite3.Connection:
    """Open the lab's database with the pragmas the spec requires.

    ``foreign_keys=ON`` is per-connection in SQLite and off by default, so it
    must be set here rather than in the schema — a connection that forgets it
    silently loses every FK cascade in spec/schema/schema.hcl.
    """
    path = database_path(data_dir)
    if not create and not path.exists():
        raise DatabaseError(
            f"no database at {path}. Run './arailctl db apply' to create it.")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, isolation_level=None)
    except (OSError, sqlite3.Error) as exc:
        raise DatabaseError(f"cannot open database at {path}: {exc}") from exc

    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 5000")
    except sqlite3.Error as exc:
        conn.close()
        raise DatabaseError(
            f"cannot configure database at {path}: {exc}") from exc
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Explicit transaction. Rolls back on any exception.

    ``connect()`` opens in autocommit (``isolation_level=None``), so writes
    that must land together have to say so here rather than relying on
    Python's implicit-BEGIN behaviour, which does not cover DDL.
    """
    conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _has_schema_version_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    return row is not None


def applied_version(conn: sqlite3.Connection) -> Optional[tuple[int, str]]:
    """The highest applied spec version and its spec hash, or None."""
    if not _has_schema_version_table(conn):
        return None
    row = conn.execute(
        "SELECT version, spec_sha256 FROM schema_version "
        "ORDER BY version DESC LIMIT 1").fetchone()
    if row is None:
        return None
    return int(row["version"]), str(row["spec_sha256"])


def record_version(conn: sqlite3.Connection, version: int,
                   spec_sha256: str, applied_at: str) -> None:
    if not _has_schema_version_table(conn):
        raise DatabaseError(
            "schema_version table is missing; the schema has not been applied. "
            "Run './arailctl db apply'.")
    conn.execute(
        "INSERT OR REPLACE INTO schema_version (version, spec_sha256, applied_at) "
        "VALUES (?, ?, ?)", (version, spec_sha256, applied_at))
