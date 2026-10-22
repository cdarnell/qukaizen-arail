"""ARAIL's first enforced agent budget: what a phone can ask of the lab per day.

ARCHITECTURE.md §5.6. For agent ``buddy``: **300 turns** and **600,000 tokens** per
local day, and a backstop of **20 admitted calls per minute per pseudonym**
(``identity_throttled``). The caps protect lab memory and compute, not money.

``<DATA_DIR>/buddy/budget.json`` (0600) holds ``{"day", "turns", "tokens"}`` and resets at
local midnight. A turn is checked at admission (:meth:`Budget.check`) and, once the call
holds the inference slot, re-checked and counted in one atomic step (:meth:`Budget.claim_turn`),
so a call refused because the model is busy costs nothing and calls queued on the slot cannot
overshoot the cap; tokens are added when the call completes
(:meth:`Budget.add_tokens`). A corrupt file starts the day from zero rather than locking
Buddy out: the budget guards compute, and the hold switch is the safety control.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Optional

from arail.buddy_host import CONTRACT
from arail.buddy_host._files import ensure_private_dir, write_private

_LIM = CONTRACT["limits"]
CAP_TURNS = int(_LIM["budget_per_day"]["turns"])
CAP_TOKENS = int(_LIM["budget_per_day"]["tokens"])
CALLS_PER_MIN = int(_LIM["calls_per_pseudonym_per_minute"])
WINDOW_S = 60.0


class BudgetRefused(Exception):
    """``code`` is ``budget_exhausted`` or ``identity_throttled``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def budget_path() -> Path:
    from arail import config
    return Path(config.DATA_DIR) / "buddy" / "budget.json"


def _local_day() -> str:
    return dt.datetime.now().astimezone().date().isoformat()


class Budget:
    def __init__(self, path: Optional[Path] = None, *, day: Callable[[], str] = _local_day,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._path = Path(path) if path is not None else None
        self._day = day
        self._clock = clock
        self._lock = threading.Lock()
        self._state: Optional[dict] = None
        self._stamps: dict[str, deque] = {}

    @property
    def path(self) -> Path:
        return self._path if self._path is not None else budget_path()

    # ---- state
    def _load_locked(self) -> dict:
        today = self._day()
        st = self._state
        if st is None:
            st = {"day": today, "turns": 0, "tokens": 0}
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if (isinstance(data, dict) and data.get("day") == today
                        and isinstance(data.get("turns"), int) and isinstance(data.get("tokens"), int)
                        and data["turns"] >= 0 and data["tokens"] >= 0):
                    st = {"day": today, "turns": data["turns"], "tokens": data["tokens"]}
            except (OSError, ValueError):
                pass
            self._state = st
        if st["day"] != today:
            st = self._state = {"day": today, "turns": 0, "tokens": 0}
            self._persist_locked()
        return st

    def _persist_locked(self) -> None:
        try:
            ensure_private_dir(self.path.parent)
            write_private(self.path, (json.dumps(self._state, indent=2) + "\n").encode("utf-8"))
        except OSError:
            pass  # in-memory counters still apply for this process

    # ---- reads
    def snapshot(self) -> dict:
        with self._lock:
            st = self._load_locked()
            return {"day": st["day"], "turns_used": st["turns"], "turns_cap": CAP_TURNS,
                    "tokens_used": st["tokens"], "tokens_cap": CAP_TOKENS}

    # ---- admission
    def _prune(self, pseudonym: str, now: float) -> deque:
        q = self._stamps.setdefault(pseudonym, deque())
        while q and now - q[0] >= WINDOW_S:
            q.popleft()
        return q

    def check(self, pseudonym: str) -> None:
        """Raise :class:`BudgetRefused` if the day's caps are spent or this person is asking too fast."""
        with self._lock:
            st = self._load_locked()
            if st["turns"] >= CAP_TURNS or st["tokens"] >= CAP_TOKENS:
                raise BudgetRefused("budget_exhausted")
            if len(self._prune(pseudonym, self._clock())) >= CALLS_PER_MIN:
                raise BudgetRefused("identity_throttled")

    def begin_turn(self, pseudonym: str) -> None:
        with self._lock:
            self._count_locked(pseudonym)

    def claim_turn(self, pseudonym: str) -> None:
        """Check the caps and the per-person rate, and count the turn, as one step under the lock.

        Called once the inference slot is held. Step 9's :meth:`check` ran before the wait for the slot, so a few calls
        queued on the slot could each have passed it; this is the check that holds (ARCHITECTURE Addendum A, A2 delta 1).
        Raises :class:`BudgetRefused` and counts nothing."""
        with self._lock:
            st = self._load_locked()
            if st["turns"] >= CAP_TURNS or st["tokens"] >= CAP_TOKENS:
                raise BudgetRefused("budget_exhausted")
            if len(self._prune(pseudonym, self._clock())) >= CALLS_PER_MIN:
                raise BudgetRefused("identity_throttled")
            self._count_locked(pseudonym)

    def _count_locked(self, pseudonym: str) -> None:
        st = self._load_locked()
        st["turns"] += 1
        self._prune(pseudonym, self._clock()).append(self._clock())
        self._persist_locked()

    def add_tokens(self, n: int) -> None:
        if n <= 0:
            return
        with self._lock:
            st = self._load_locked()
            st["tokens"] += int(n)
            self._persist_locked()

    # ---- test seams
    def _seed(self, *, turns: int = 0, tokens: int = 0, calls: Optional[dict[str, int]] = None) -> None:
        with self._lock:
            st = self._load_locked()
            st["turns"], st["tokens"] = turns, tokens
            for who, n in (calls or {}).items():
                q = self._prune(who, self._clock())
                q.clear()
                q.extend([self._clock()] * n)


_budgets: dict[str, Budget] = {}
_budgets_lock = threading.Lock()


def default_budget() -> Budget:
    """One budget per data directory."""
    key = str(budget_path())
    with _budgets_lock:
        b = _budgets.get(key)
        if b is None:
            b = _budgets[key] = Budget()
        return b


def _reset_for_tests() -> None:
    with _budgets_lock:
        _budgets.clear()
