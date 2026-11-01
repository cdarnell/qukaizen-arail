"""ARAIL world resolver — explicit id or slug only.

GENERATED FILE — DO NOT EDIT.

Produced by ``arail.dbspec.codegen`` from the spec tree. Hand edits are lost
on the next ``./arailctl db apply``. Change the spec, not this file.

    spec sha256: 212d8c12bbf196a263050fd2c33529846aee67bccd5fe7e80757a3fdfc64a3aa
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional, Tuple

__all__ = [
    "World", "WorldNotFound", "AmbiguousWorldRequest", "resolve_world",
    "list_worlds", "RESOLVABLE_STATUSES", "SELECTABLE_STATUSES",
]

# Statuses a world may have and still be resolvable by explicit identifier.
# Resolving an archived world is not an error; silently substituting a
# different world would be.
RESOLVABLE_STATUSES: Tuple[str, ...] = ('active', 'archived', 'draft')

# Statuses a world may have and still be offered as a choice in a picker.
SELECTABLE_STATUSES: Tuple[str, ...] = ('active', 'draft')


class WorldNotFound(LookupError):
    """No world matched the requested identifier.

    Carries the requested identifier, the reason, and the valid alternatives
    for that user — the caller should never have to guess, and must never
    fall back to a different world.
    """

    def __init__(self, requested: str, reason: str,
                 alternatives: Tuple[str, ...], user_id: str) -> None:
        self.requested = requested
        self.reason = reason
        self.alternatives = alternatives
        self.user_id = user_id
        if alternatives:
            available = "available for this user: " + ", ".join(alternatives)
        else:
            available = "this user has no worlds"
        super().__init__(
            f"world {requested!r} could not be resolved ({reason}); "
            f"{available}"
        )


class AmbiguousWorldRequest(ValueError):
    """Both an id and a slug were supplied, or neither."""


@dataclass(frozen=True)
class World:
    id: str
    slug: str
    user_id: str
    display_name: str
    status: str
    bundle_dir: Optional[str]
    created_at: str
    updated_at: str


def _row_to_world(row: sqlite3.Row) -> World:
    return World(
        id=row["id"], slug=row["slug"], user_id=row["user_id"],
        display_name=row["display_name"], status=row["status"],
        bundle_dir=row["bundle_dir"], created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _alternatives(conn: sqlite3.Connection, user_id: str) -> Tuple[str, ...]:
    rows = conn.execute(
        "SELECT slug FROM worlds WHERE user_id = ? AND status IN (?, ?) "
        "ORDER BY slug",
        (user_id, *SELECTABLE_STATUSES),
    ).fetchall()
    return tuple(row["slug"] for row in rows)


def list_worlds(conn: sqlite3.Connection, *, user_id: str,
                selectable_only: bool = False) -> Tuple[World, ...]:
    """Worlds belonging to ``user_id``.

    Ordered by slug for stable display. Callers must NOT treat this order as
    meaningful: there is no "first" world, and indexing into this tuple to
    pick one is the positional-resolution defect this module exists to
    remove.
    """
    statuses = SELECTABLE_STATUSES if selectable_only else RESOLVABLE_STATUSES
    placeholders = ", ".join("?" for _ in statuses)
    rows = conn.execute(
        f"SELECT * FROM worlds WHERE user_id = ? AND status IN ({placeholders}) "
        f"ORDER BY slug",
        (user_id, *statuses),
    ).fetchall()
    return tuple(_row_to_world(row) for row in rows)


def resolve_world(conn: sqlite3.Connection, *, user_id: str,
                  world_id: Optional[str] = None,
                  slug: Optional[str] = None) -> World:
    """Resolve exactly one world by explicit id or slug.

    There is no fallback. If the identifier does not match, this raises
    :class:`WorldNotFound` naming the request and the alternatives; it never
    returns a different world, the only world, the newest world, or the
    alphabetically first world.
    """
    if (world_id is None) == (slug is None):
        raise AmbiguousWorldRequest(
            "supply exactly one of world_id= or slug= "
            f"(got world_id={world_id!r}, slug={slug!r})"
        )

    if world_id is not None:
        requested, column, value = world_id, "id", world_id
    else:
        requested, column, value = slug, "slug", slug

    row = conn.execute(
        f"SELECT * FROM worlds WHERE user_id = ? AND {column} = ?",
        (user_id, value),
    ).fetchone()

    if row is None:
        raise WorldNotFound(
            requested=str(requested),
            reason=f"no world with that {column} belongs to user {user_id!r}",
            alternatives=_alternatives(conn, user_id),
            user_id=user_id,
        )

    world = _row_to_world(row)
    if world.status not in RESOLVABLE_STATUSES:
        raise WorldNotFound(
            requested=str(requested),
            reason=f"world has status {world.status!r}, which is not resolvable",
            alternatives=_alternatives(conn, user_id),
            user_id=user_id,
        )
    return world
