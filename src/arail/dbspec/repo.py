"""Repository (data-access) layer for the ARAIL 2.0 SQLite store.

Every function here takes an already-open ``sqlite3.Connection`` (see
``arail.dbspec.db.connect``) and speaks the schema in
``spec/schema/schema.hcl`` directly — no ORM, no query builder. Writes that
touch more than one row go through ``db.transaction`` so they land together
or not at all.

Identity is never positional. Every lookup here is keyed by an explicit id,
or by the columns a UNIQUE index enforces (``(world_id, kind, name)`` for
entities, ``(src, dst, kind)`` for relations, ``(lance_table, row_key)`` for
content_refs). Nothing here falls back to "the first row" or "any world".

sqlite3 errors are never swallowed: every write path lets ``sqlite3.Error``
propagate, or wraps it in :class:`arail.dbspec.db.DatabaseError` with an
actionable message when the cause is something the caller can act on (e.g. a
duplicate slug). There is no except-and-continue in this module.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from arail.dbspec import db
from arail.dbspec.generated.world_resolver import World

__all__ = [
    "Entity", "ContentRef",
    "create_world", "update_world_status", "delete_world",
    "upsert_entity", "get_entity", "list_entities",
    "add_relation", "neighbors", "traverse",
    "set_state", "get_state", "all_state",
    "record_content", "content_for_world", "row_keys_for_world",
    "drop_content",
]

_VALID_DIRECTIONS = ("out", "in", "both")


def _now() -> str:
    """UTC ISO-8601 timestamp with a trailing Z. The single source of truth
    for timestamps in this module — every write path calls this, never
    ``datetime.now()`` or ``time.time()`` directly."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _new_id() -> str:
    return uuid.uuid4().hex


def _row_to_world(row: sqlite3.Row) -> World:
    return World(
        id=row["id"], slug=row["slug"], user_id=row["user_id"],
        display_name=row["display_name"], status=row["status"],
        bundle_dir=row["bundle_dir"], created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------------------
# worlds
# ---------------------------------------------------------------------------

def create_world(conn: sqlite3.Connection, *, user_id: str, slug: str,
                 display_name: str, status: str = "active",
                 bundle_dir: Optional[str] = None,
                 world_id: Optional[str] = None) -> World:
    """Create a world. ``world_id`` defaults to a generated uuid4 hex —
    never positional, never derived from an existing row's ordering."""
    wid = world_id if world_id is not None else _new_id()
    now = _now()
    try:
        with db.transaction(conn):
            conn.execute(
                "INSERT INTO worlds "
                "(id, slug, user_id, display_name, status, bundle_dir, "
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (wid, slug, user_id, display_name, status, bundle_dir, now, now),
            )
    except sqlite3.IntegrityError as exc:
        raise db.DatabaseError(
            f"cannot create world: slug {slug!r} already exists for user "
            f"{user_id!r} (or id {wid!r} collides): {exc}") from exc
    row = conn.execute(
        "SELECT * FROM worlds WHERE id = ?", (wid,)).fetchone()
    return _row_to_world(row)


def update_world_status(conn: sqlite3.Connection, *, world_id: str,
                        status: str) -> World:
    now = _now()
    try:
        with db.transaction(conn):
            cur = conn.execute(
                "UPDATE worlds SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, world_id),
            )
            if cur.rowcount == 0:
                raise db.DatabaseError(
                    f"cannot update status: no world with id {world_id!r}")
    except sqlite3.IntegrityError as exc:
        raise db.DatabaseError(
            f"cannot set status {status!r} on world {world_id!r}: {exc}"
        ) from exc
    row = conn.execute(
        "SELECT * FROM worlds WHERE id = ?", (world_id,)).fetchone()
    return _row_to_world(row)


def delete_world(conn: sqlite3.Connection, *, world_id: str) -> None:
    """Delete a world. FK cascade (ON DELETE CASCADE in the schema) removes
    its entities, relations, world_state, and content_refs."""
    with db.transaction(conn):
        cur = conn.execute("DELETE FROM worlds WHERE id = ?", (world_id,))
        if cur.rowcount == 0:
            raise db.DatabaseError(
                f"cannot delete: no world with id {world_id!r}")


# ---------------------------------------------------------------------------
# entities
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Entity:
    id: str
    world_id: str
    kind: str
    name: str
    title: Optional[str]
    body: Optional[str]
    attrs: dict
    created_at: str
    updated_at: str


def _row_to_entity(row: sqlite3.Row) -> Entity:
    raw = row["attrs_json"]
    attrs = json.loads(raw) if raw is not None else {}
    return Entity(
        id=row["id"], world_id=row["world_id"], kind=row["kind"],
        name=row["name"], title=row["title"], body=row["body"],
        attrs=attrs, created_at=row["created_at"], updated_at=row["updated_at"],
    )


def upsert_entity(conn: sqlite3.Connection, *, world_id: str, kind: str,
                  name: str, title: Optional[str] = None,
                  body: Optional[str] = None,
                  attrs: Optional[dict] = None) -> Entity:
    """Idempotent on UNIQUE(world_id, kind, name): a second call with the
    same (world_id, kind, name) updates title/body/attrs in place rather
    than creating a second row."""
    attrs_json = json.dumps(attrs if attrs is not None else {})
    now = _now()
    try:
        with db.transaction(conn):
            existing = conn.execute(
                "SELECT id FROM entities WHERE world_id = ? AND kind = ? "
                "AND name = ?", (world_id, kind, name)).fetchone()
            if existing is None:
                eid = _new_id()
                conn.execute(
                    "INSERT INTO entities "
                    "(id, world_id, kind, name, title, body, attrs_json, "
                    " created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (eid, world_id, kind, name, title, body, attrs_json,
                     now, now),
                )
            else:
                eid = existing["id"]
                conn.execute(
                    "UPDATE entities SET title = ?, body = ?, "
                    "attrs_json = ?, updated_at = ? WHERE id = ?",
                    (title, body, attrs_json, now, eid),
                )
    except sqlite3.IntegrityError as exc:
        raise db.DatabaseError(
            f"cannot upsert entity ({world_id!r}, {kind!r}, {name!r}): "
            f"{exc}") from exc
    row = conn.execute(
        "SELECT * FROM entities WHERE id = ?", (eid,)).fetchone()
    return _row_to_entity(row)


def get_entity(conn: sqlite3.Connection, *, world_id: str, kind: str,
              name: str) -> Optional[Entity]:
    row = conn.execute(
        "SELECT * FROM entities WHERE world_id = ? AND kind = ? AND name = ?",
        (world_id, kind, name)).fetchone()
    return _row_to_entity(row) if row is not None else None


def list_entities(conn: sqlite3.Connection, *, world_id: str,
                  kind: Optional[str] = None) -> tuple[Entity, ...]:
    if kind is None:
        rows = conn.execute(
            "SELECT * FROM entities WHERE world_id = ? ORDER BY kind, name",
            (world_id,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM entities WHERE world_id = ? AND kind = ? "
            "ORDER BY name", (world_id, kind)).fetchall()
    return tuple(_row_to_entity(r) for r in rows)


# ---------------------------------------------------------------------------
# relations
# ---------------------------------------------------------------------------

def add_relation(conn: sqlite3.Connection, *, world_id: str,
                 src_entity_id: str, dst_entity_id: str, kind: str,
                 weight: Optional[float] = None) -> str:
    """Returns the relation id. Idempotent on UNIQUE(src, dst, kind): a
    second call with the same edge updates the weight rather than raising."""
    now = _now()
    try:
        with db.transaction(conn):
            existing = conn.execute(
                "SELECT id FROM relations WHERE src_entity_id = ? AND "
                "dst_entity_id = ? AND kind = ?",
                (src_entity_id, dst_entity_id, kind)).fetchone()
            if existing is None:
                rid = _new_id()
                conn.execute(
                    "INSERT INTO relations "
                    "(id, world_id, src_entity_id, dst_entity_id, kind, "
                    " weight, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (rid, world_id, src_entity_id, dst_entity_id, kind,
                     weight, now),
                )
            else:
                rid = existing["id"]
                conn.execute(
                    "UPDATE relations SET weight = ? WHERE id = ?",
                    (weight, rid),
                )
    except sqlite3.IntegrityError as exc:
        raise db.DatabaseError(
            f"cannot add relation {src_entity_id!r} -{kind}-> "
            f"{dst_entity_id!r}: {exc}") from exc
    return rid


def neighbors(conn: sqlite3.Connection, *, entity_id: str,
             kind: Optional[str] = None,
             direction: str = "out") -> tuple[Entity, ...]:
    if direction not in _VALID_DIRECTIONS:
        raise db.DatabaseError(
            f"invalid direction {direction!r}; must be one of "
            f"{_VALID_DIRECTIONS}")

    kind_clause = " AND r.kind = ?" if kind is not None else ""
    kind_params = (kind,) if kind is not None else ()

    if direction == "out":
        sql = (
            "SELECT e.* FROM entities e "
            "JOIN relations r ON r.dst_entity_id = e.id "
            f"WHERE r.src_entity_id = ?{kind_clause} ORDER BY e.name"
        )
        params = (entity_id, *kind_params)
    elif direction == "in":
        sql = (
            "SELECT e.* FROM entities e "
            "JOIN relations r ON r.src_entity_id = e.id "
            f"WHERE r.dst_entity_id = ?{kind_clause} ORDER BY e.name"
        )
        params = (entity_id, *kind_params)
    else:  # both
        sql = (
            "SELECT e.* FROM entities e "
            "JOIN relations r ON r.dst_entity_id = e.id "
            f"WHERE r.src_entity_id = ?{kind_clause} "
            "UNION "
            "SELECT e.* FROM entities e "
            "JOIN relations r ON r.src_entity_id = e.id "
            f"WHERE r.dst_entity_id = ?{kind_clause} ORDER BY name"
        )
        params = (entity_id, *kind_params, entity_id, *kind_params)

    rows = conn.execute(sql, params).fetchall()
    return tuple(_row_to_entity(r) for r in rows)


def traverse(conn: sqlite3.Connection, *, start_entity_id: str,
            max_depth: int = 3,
            kind: Optional[str] = None) -> tuple[tuple[Entity, int], ...]:
    """Graph traversal via a recursive CTE. Returns (entity, depth) pairs,
    depth >= 1, ordered by depth then entity name. Cycle-safe: a node is
    never revisited within the recursion, guarded by the CTE's own
    accumulated path so a->b->a terminates instead of looping forever."""
    kind_clause = " AND r.kind = :kind" if kind is not None else ""
    sql = f"""
    WITH RECURSIVE walk(entity_id, depth, path) AS (
        SELECT r.dst_entity_id, 1, ',' || r.src_entity_id || ',' || r.dst_entity_id || ','
        FROM relations r
        WHERE r.src_entity_id = :start{kind_clause}

        UNION ALL

        SELECT r.dst_entity_id, walk.depth + 1,
               walk.path || r.dst_entity_id || ','
        FROM relations r
        JOIN walk ON r.src_entity_id = walk.entity_id
        WHERE walk.depth < :max_depth
          AND walk.path NOT LIKE '%,' || r.dst_entity_id || ',%'
          {kind_clause}
    )
    SELECT e.*, MIN(walk.depth) AS depth
    FROM walk
    JOIN entities e ON e.id = walk.entity_id
    GROUP BY e.id
    ORDER BY depth, e.name
    """
    params: dict[str, Any] = {"start": start_entity_id, "max_depth": max_depth}
    if kind is not None:
        params["kind"] = kind
    rows = conn.execute(sql, params).fetchall()
    return tuple((_row_to_entity(r), int(r["depth"])) for r in rows)


# ---------------------------------------------------------------------------
# world_state
# ---------------------------------------------------------------------------

def set_state(conn: sqlite3.Connection, *, world_id: str, key: str,
             value: object, tick: Optional[int] = None) -> None:
    """Write world state. ``value`` is JSON-serialized. If ``tick`` is not
    given, the existing tick (0 if the key is new) is incremented."""
    value_json = json.dumps(value)
    now = _now()
    try:
        with db.transaction(conn):
            if tick is None:
                existing = conn.execute(
                    "SELECT tick FROM world_state WHERE world_id = ? "
                    "AND key = ?", (world_id, key)).fetchone()
                next_tick = (existing["tick"] + 1) if existing is not None else 0
            else:
                next_tick = tick
            conn.execute(
                "INSERT INTO world_state (world_id, key, value_json, tick, "
                " updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(world_id, key) DO UPDATE SET "
                "value_json = excluded.value_json, tick = excluded.tick, "
                "updated_at = excluded.updated_at",
                (world_id, key, value_json, next_tick, now),
            )
    except sqlite3.IntegrityError as exc:
        raise db.DatabaseError(
            f"cannot set state {key!r} for world {world_id!r}: {exc}"
        ) from exc


def get_state(conn: sqlite3.Connection, *, world_id: str, key: str,
             default: object = None) -> object:
    row = conn.execute(
        "SELECT value_json FROM world_state WHERE world_id = ? AND key = ?",
        (world_id, key)).fetchone()
    if row is None:
        return default
    return json.loads(row["value_json"])


def all_state(conn: sqlite3.Connection, *, world_id: str) -> dict[str, object]:
    rows = conn.execute(
        "SELECT key, value_json FROM world_state WHERE world_id = ? "
        "ORDER BY key", (world_id,)).fetchall()
    return {row["key"]: json.loads(row["value_json"]) for row in rows}


# ---------------------------------------------------------------------------
# content_refs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ContentRef:
    id: str
    world_id: str
    entity_id: Optional[str]
    lance_table: str
    lance_uri: str
    row_key: str
    source_path: Optional[str]
    content_sha256: Optional[str]
    embedding_model: str
    embedding_dim: int
    ingested_at: str


def _row_to_content_ref(row: sqlite3.Row) -> ContentRef:
    return ContentRef(
        id=row["id"], world_id=row["world_id"], entity_id=row["entity_id"],
        lance_table=row["lance_table"], lance_uri=row["lance_uri"],
        row_key=row["row_key"], source_path=row["source_path"],
        content_sha256=row["content_sha256"],
        embedding_model=row["embedding_model"],
        embedding_dim=row["embedding_dim"], ingested_at=row["ingested_at"],
    )


def record_content(conn: sqlite3.Connection, *, world_id: str,
                   lance_table: str, lance_uri: str, row_key: str,
                   embedding_model: str, embedding_dim: int,
                   entity_id: Optional[str] = None,
                   source_path: Optional[str] = None,
                   content_sha256: Optional[str] = None) -> ContentRef:
    """Idempotent on UNIQUE(world_id, lance_table, row_key).

    Row keys are unique within a world, not globally: every world's PKB has
    its own ``agents/README.md``. Matching on (lance_table, row_key) alone
    would make one world's ingest steal another world's content_ref.
    """
    now = _now()
    try:
        with db.transaction(conn):
            existing = conn.execute(
                "SELECT id FROM content_refs WHERE world_id = ? "
                "AND lance_table = ? AND row_key = ?",
                (world_id, lance_table, row_key)).fetchone()
            if existing is None:
                cid = _new_id()
                conn.execute(
                    "INSERT INTO content_refs "
                    "(id, world_id, entity_id, lance_table, lance_uri, "
                    " row_key, source_path, content_sha256, embedding_model, "
                    " embedding_dim, ingested_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (cid, world_id, entity_id, lance_table, lance_uri,
                     row_key, source_path, content_sha256, embedding_model,
                     embedding_dim, now),
                )
            else:
                cid = existing["id"]
                conn.execute(
                    "UPDATE content_refs SET entity_id = ?, "
                    "lance_uri = ?, source_path = ?, content_sha256 = ?, "
                    "embedding_model = ?, embedding_dim = ?, ingested_at = ? "
                    "WHERE id = ?",
                    (entity_id, lance_uri, source_path, content_sha256,
                     embedding_model, embedding_dim, now, cid),
                )
    except sqlite3.IntegrityError as exc:
        raise db.DatabaseError(
            f"cannot record content ({world_id!r}, {lance_table!r}, "
            f"{row_key!r}): {exc}"
        ) from exc
    row = conn.execute(
        "SELECT * FROM content_refs WHERE id = ?", (cid,)).fetchone()
    return _row_to_content_ref(row)


def content_for_world(conn: sqlite3.Connection, *, world_id: str,
                      lance_table: Optional[str] = None
                      ) -> tuple[ContentRef, ...]:
    if lance_table is None:
        rows = conn.execute(
            "SELECT * FROM content_refs WHERE world_id = ? "
            "ORDER BY lance_table, row_key", (world_id,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM content_refs WHERE world_id = ? "
            "AND lance_table = ? ORDER BY row_key",
            (world_id, lance_table)).fetchall()
    return tuple(_row_to_content_ref(r) for r in rows)


def row_keys_for_world(conn: sqlite3.Connection, *, world_id: str,
                       lance_table: str) -> tuple[str, ...]:
    """The world-scoping primitive: the row keys a world-scoped vector query
    must filter to. The vector tables also carry a ``world_id`` column, so a
    query can filter there directly; this is the relational-side view, used to
    reconcile the two stores and to detect orphans."""
    rows = conn.execute(
        "SELECT row_key FROM content_refs WHERE world_id = ? AND "
        "lance_table = ? ORDER BY row_key", (world_id, lance_table)
    ).fetchall()
    return tuple(row["row_key"] for row in rows)


def drop_content(conn: sqlite3.Connection, *, world_id: str,
                 lance_table: str, row_key: str) -> bool:
    """Drop one world's reference to a Lance row.

    ``world_id`` is required: the same row_key legitimately exists in several
    worlds, and dropping by (lance_table, row_key) alone would delete every
    world's reference at once.
    """
    with db.transaction(conn):
        cur = conn.execute(
            "DELETE FROM content_refs WHERE world_id = ? AND lance_table = ? "
            "AND row_key = ?", (world_id, lance_table, row_key))
    return cur.rowcount > 0
