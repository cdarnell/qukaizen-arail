"""RFC 3339 helpers for the edge. The contract's timestamps are UTC with a Z or an offset."""

from __future__ import annotations

import datetime as dt
from typing import Optional

_UTC = dt.timezone.utc


def now_utc() -> dt.datetime:
    return dt.datetime.now(_UTC)


def fmt(t: dt.datetime) -> str:
    """Second resolution, UTC, suffix Z."""
    return t.astimezone(_UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(value: str) -> dt.datetime:
    """Parse an RFC 3339 timestamp (raises ValueError). A naive value is read as UTC."""
    t = dt.datetime.fromisoformat(value.replace("Z", "+00:00") if value.endswith("Z") else value)
    return t if t.tzinfo is not None else t.replace(tzinfo=_UTC)


def from_naive_local(value: Optional[str]) -> Optional[str]:
    """ARAIL's scheduler stores local, naive ISO strings; the contract wants UTC with Z."""
    if not value:
        return None
    try:
        t = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.astimezone()  # interpret as local time
    return fmt(t)
