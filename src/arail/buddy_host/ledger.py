"""The approvals ledger: why ``pause_research`` runs at most once per approval.

``<DATA_DIR>/buddy/approvals.sqlite3`` (0600, WAL, ``synchronous=FULL``)::

    approvals(approval_id PK, nonce_sha256 UNIQUE, record_sha256, state, outcome,
              received_at, executed_at)

* the same ``approval_id`` with the same ``record_sha256`` is a replay and is answered
  from the stored outcome, never executed again;
* the same ``approval_id`` with a different body, or the same ``nonce_sha256`` under
  another id, is a conflict;
* a row left in ``executing`` (the process died between the insert and the result) is
  reported as ``indeterminate_restart``: the edge does not guess whether the action ran.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from arail.buddy_host._files import create_private_file, ensure_private_dir

_SCHEMA = """
CREATE TABLE IF NOT EXISTS approvals(
    approval_id   TEXT PRIMARY KEY,
    nonce_sha256  TEXT NOT NULL UNIQUE,
    record_sha256 TEXT NOT NULL,
    state         TEXT NOT NULL CHECK (state IN ('executing', 'done')),
    outcome       TEXT,
    received_at   TEXT NOT NULL,
    executed_at   TEXT
)
"""


class ApprovalConflict(Exception):
    """The approval id or nonce hash is already taken by a different record."""


@dataclass(frozen=True)
class Row:
    approval_id: str
    nonce_sha256: str
    record_sha256: str
    state: str
    outcome: Optional[str]
    received_at: str
    executed_at: Optional[str]


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        ensure_private_dir(self.path.parent)
        create_private_file(self.path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), isolation_level=None, check_same_thread=False, timeout=5.0)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _row(r) -> Optional[Row]:
        return Row(*r) if r else None

    def get(self, approval_id: str) -> Optional[Row]:
        with self._lock:
            cur = self._db.execute(
                "SELECT approval_id, nonce_sha256, record_sha256, state, outcome, received_at, executed_at "
                "FROM approvals WHERE approval_id = ?", (approval_id,))
            return self._row(cur.fetchone())

    def count(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) FROM approvals").fetchone()[0])

    def lookup(self, approval_id: str, record_sha256: str, nonce_sha256: str) -> Optional[Row]:
        """The stored row for a replay, None for a new approval; raises on a conflict.

        A row stuck in ``executing`` is settled as ``indeterminate_restart`` here, once,
        so every later replay reads the same answer.
        """
        with self._lock:
            row = self.get(approval_id)
            if row is not None:
                if row.record_sha256 != record_sha256:
                    raise ApprovalConflict("same approval id, different record")
                if row.state == "executing":
                    self._db.execute(
                        "UPDATE approvals SET state='done', outcome='indeterminate_restart' "
                        "WHERE approval_id=? AND state='executing'", (approval_id,))
                    row = self.get(approval_id)
                return row
            other = self._db.execute(
                "SELECT approval_id FROM approvals WHERE nonce_sha256 = ?", (nonce_sha256,)).fetchone()
            if other is not None:
                raise ApprovalConflict("same nonce hash under another approval id")
            return None

    def begin(self, approval_id: str, nonce_sha256: str, record_sha256: str, received_at: str) -> None:
        """Insert the ``executing`` row and commit it (WAL + synchronous=FULL) before any action runs."""
        with self._lock:
            try:
                self._db.execute(
                    "INSERT INTO approvals(approval_id, nonce_sha256, record_sha256, state, received_at) "
                    "VALUES (?, ?, ?, 'executing', ?)", (approval_id, nonce_sha256, record_sha256, received_at))
            except sqlite3.IntegrityError:
                raise ApprovalConflict("approval id or nonce hash already recorded") from None

    def finish(self, approval_id: str, outcome: str, executed_at: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE approvals SET state='done', outcome=?, executed_at=? WHERE approval_id=?",
                (outcome, executed_at, approval_id))


_ledgers: dict[str, Ledger] = {}
_ledgers_lock = threading.Lock()


def ledger_path() -> Path:
    from arail import config
    return Path(config.DATA_DIR) / "buddy" / "approvals.sqlite3"


def default_ledger() -> Ledger:
    """One open ledger per data directory (a World instance has its own)."""
    path = ledger_path()
    key = str(path)
    with _ledgers_lock:
        led = _ledgers.get(key)
        if led is None:
            led = _ledgers[key] = Ledger(path)
        return led


def _reset_for_tests() -> None:
    with _ledgers_lock:
        for led in _ledgers.values():
            try:
                led.close()
            except Exception:  # noqa: BLE001
                pass
        _ledgers.clear()
