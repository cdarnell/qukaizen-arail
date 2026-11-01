"""One-shot ARAIL 1.x -> 2.0 data migration.

1.x had no tenant column anywhere: a "lab" was a directory tree pinned to a
frozen process environment, and its four Lance tables (``pkb_pages``,
``wiki_nodes``, ``agent_workflows``, ``experiments``) stored 128-dim SHA1
token-hash projections under the name "semantic search" — not real
embeddings, and with no ``world_id`` to scope a query by. World isolation was
achieved by physically deleting the other worlds' staged files.

This module reads that layout without touching it, and produces the 2.0
layout: one ``worlds`` row per migrated lab root, every Lance row re-embedded
with the spec's real embedding model and carrying ``world_id``, and a
``content_refs`` row (for ``pkb_pages`` / ``wiki_nodes``) recording exactly
which model produced which vector.

Dry-run is the default posture, same as :mod:`arail.dbspec.reconcile`:
:func:`discover` never writes anything (it only reads), and :func:`migrate`
writes nothing unless ``apply=True``. The migration is idempotent: a world
that already carries a ``migration`` key in ``world_state`` is skipped
entirely on a second run, so re-running never duplicates worlds, entities,
content_refs, or Lance rows.

The 1.x source data is never deleted or modified. Every table this module
reads, it reads with ``lancedb`` in the ordinary open-and-query path; no
``drop``, no ``delete``, no ``add`` ever targets a 1.x path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from arail.dbspec import atlas, embed, reconcile, repo
from arail.dbspec.db import connect, database_path
from arail.dbspec.generated.models_registry import EMBEDDING_DIM, EMBEDDING_MODEL
from arail.dbspec.generated.world_resolver import World
from arail.dbspec.reconcile import resolve_dataset_path
from arail.dbspec.spec import DEFAULT_SPEC_DIR, Spec, load_spec

__all__ = [
    "MigrationError", "LabSource", "MigrationPlan", "discover", "migrate",
]

_MIGRATION_STATE_KEY = "migration"
_INSTANCES_DIRNAME = "instances"
_ROOT_WORLD_SLUG = "root"

# Which row fields feed the embedding text for each table, in order, as
# specified by the migration contract. Every one of these tables' rows gets
# a real embedding — there is no partial-migration mode.
_EMBED_FIELDS = {
    "pkb_pages": ("name", "path"),
    "wiki_nodes": ("title", "section", "slug"),
    "agent_workflows": ("objective", "summary", "current_task"),
    "experiments": ("id", "domain"),
}

# Tables that get a content_refs row per migrated row (requirement #5).
_CONTENT_REF_TABLES = ("pkb_pages", "wiki_nodes")


class MigrationError(RuntimeError):
    """Something about the 1.x source or 2.0 target could not be migrated.

    The message names the path and the table; the caller is never left
    guessing which of several Lance datasets was the problem.
    """


@dataclass(frozen=True)
class LabSource:
    world_slug: str
    data_dir: str
    pkb_root: str
    tables: dict  # lance table name -> row count found


@dataclass(frozen=True)
class MigrationPlan:
    labs: Tuple[LabSource, ...]

    def render(self) -> str:
        lines = ["ARAIL 1.x -> 2.0 migration — discovered lab source(s)", ""]
        if not self.labs:
            lines.append("  none found")
            return "\n".join(lines)
        for lab in self.labs:
            total = sum(lab.tables.values())
            lines.append(f"world {lab.world_slug!r} ({total} row(s) total)")
            lines.append(f"  data dir  {lab.data_dir}")
            lines.append(f"  pkb root  {lab.pkb_root}")
            for name in sorted(lab.tables):
                lines.append(f"    {name}: {lab.tables[name]} row(s)")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Discovery — read-only
# ---------------------------------------------------------------------------

def _open_lance_table(dataset_path: str):
    import lancedb
    p = Path(dataset_path)
    return lancedb.connect(str(p.parent)).open_table(p.stem)


def _row_count(dataset_path: str) -> int:
    p = Path(dataset_path)
    if not p.exists():
        return 0
    try:
        return _open_lance_table(dataset_path).count_rows()
    except Exception as exc:  # noqa: BLE001 - surfaced as a structured error
        raise MigrationError(
            f"cannot inspect 1.x Lance dataset at {dataset_path}: {exc}"
        ) from exc


def _discover_one(spec: Spec, world_slug: str, data_dir: Path,
                  pkb_root: Path) -> Optional[LabSource]:
    """None if neither `data_dir` nor `pkb_root` exists — the half-created
    instance dir case the migration must skip rather than fail on."""
    if not data_dir.is_dir() and not pkb_root.is_dir():
        return None
    tables: dict = {}
    for table_spec in spec.vector_tables:
        path = resolve_dataset_path(table_spec, str(data_dir), str(pkb_root))
        tables[table_spec.name] = _row_count(path)
    return LabSource(
        world_slug=world_slug,
        data_dir=str(data_dir.resolve()),
        pkb_root=str(pkb_root.resolve()),
        tables=tables,
    )


def discover(lab_root: str, *, spec: Optional[Spec] = None) -> MigrationPlan:
    """Find the root lab and every ``lab/instances/<slug>``.

    Read-only: opens Lance tables to count rows, never writes. A lab source
    is included if its ``data/`` or ``pkb/`` directory exists; an instance
    directory with neither (a half-created instance) is silently skipped —
    that is a known state (e.g. a stray ``finance`` dir), not an error.
    """
    if spec is None:
        spec = load_spec(DEFAULT_SPEC_DIR)
    root = Path(lab_root)
    labs: List[LabSource] = []

    root_lab = _discover_one(spec, _ROOT_WORLD_SLUG, root / "data", root / "pkb")
    if root_lab is not None:
        labs.append(root_lab)

    instances_dir = root / _INSTANCES_DIRNAME
    if instances_dir.is_dir():
        for child in sorted(p for p in instances_dir.iterdir() if p.is_dir()):
            lab = _discover_one(spec, child.name, child / "data", child / "pkb")
            if lab is not None:
                labs.append(lab)

    return MigrationPlan(labs=tuple(labs))


# ---------------------------------------------------------------------------
# Migration — writes only when apply=True
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _display_name(world_slug: str) -> str:
    if world_slug == _ROOT_WORLD_SLUG:
        return "Root Lab"
    return world_slug.replace("-", " ").replace("_", " ").title()


def _embedding_text(table_name: str, row: dict) -> str:
    fields = _EMBED_FIELDS.get(table_name)
    if fields is None:
        raise MigrationError(
            f"no embedding-text rule declared for table {table_name!r}; "
            f"declared rules: {', '.join(sorted(_EMBED_FIELDS))}")
    parts = [row.get(f) for f in fields]
    text = " ".join(str(p) for p in parts if p not in (None, ""))
    return text or table_name


def _world_from_row(row) -> World:
    return World(
        id=row["id"], slug=row["slug"], user_id=row["user_id"],
        display_name=row["display_name"], status=row["status"],
        bundle_dir=row["bundle_dir"], created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _get_or_create_world(conn, *, user_id: str, slug: str,
                         display_name: str) -> World:
    """Idempotent: a second call with the same (user_id, slug) returns the
    existing world rather than colliding with the UNIQUE index."""
    row = conn.execute(
        "SELECT * FROM worlds WHERE user_id = ? AND slug = ?",
        (user_id, slug)).fetchone()
    if row is not None:
        return _world_from_row(row)
    return repo.create_world(conn, user_id=user_id, slug=slug,
                             display_name=display_name)


def _read_source_rows(dataset_path: str) -> List[dict]:
    p = Path(dataset_path)
    if not p.exists():
        return []
    try:
        return _open_lance_table(dataset_path).to_arrow().to_pylist()
    except Exception as exc:  # noqa: BLE001
        raise MigrationError(
            f"cannot read 1.x Lance dataset at {dataset_path}: {exc}") from exc


def _migrate_table(conn, world: World, table_spec, lab: LabSource,
                   target_data_dir: str, target_pkb_root: str) -> List[str]:
    source_path = resolve_dataset_path(table_spec, lab.data_dir, lab.pkb_root)
    rows = _read_source_rows(source_path)
    if not rows:
        return []

    texts = [_embedding_text(table_spec.name, row) for row in rows]
    # Never a substitute vector: EmbeddingUnavailable/EmbeddingError propagate
    # to the caller unmodified, with their own actionable message.
    vectors = embed.embed_documents(texts)
    if len(vectors) != len(rows):
        raise MigrationError(
            f"embed_documents returned {len(vectors)} vector(s) for "
            f"{len(rows)} row(s) of {table_spec.name!r}; refusing to write "
            f"a misaligned batch")

    target_path = resolve_dataset_path(table_spec, target_data_dir,
                                       target_pkb_root)
    target_table = _open_lance_table(target_path)
    primary_key = table_spec.primary_key
    if primary_key is None:
        raise MigrationError(
            f"table {table_spec.name!r} declares no primary key; cannot "
            f"compute content_refs.row_key")

    to_insert = []
    for row, vector in zip(rows, vectors):
        record = {c.name: row.get(c.name) for c in table_spec.columns
                  if c.name != "world_id"}
        record["world_id"] = world.id
        record[table_spec.vector.name] = vector
        to_insert.append(record)
    target_table.add(to_insert)

    lines = [
        f"  {table_spec.name}: read {len(rows)}, embedded {len(vectors)}, "
        f"written {len(to_insert)} -> {target_path}"
    ]

    if table_spec.name in _CONTENT_REF_TABLES:
        for row in rows:
            row_key = str(row[primary_key])
            entity_id = None
            if table_spec.name == "pkb_pages":
                entity = repo.upsert_entity(
                    conn, world_id=world.id, kind="document",
                    name=str(row["path"]))
                entity_id = entity.id
            repo.record_content(
                conn, world_id=world.id, lance_table=table_spec.name,
                lance_uri=f"file://{target_path}", row_key=row_key,
                embedding_model=EMBEDDING_MODEL, embedding_dim=EMBEDDING_DIM,
                entity_id=entity_id, source_path=source_path,
            )
        lines.append(
            f"    recorded {len(rows)} content_ref(s) for {table_spec.name!r} "
            f"(embedding_model={EMBEDDING_MODEL!r}, embedding_dim={EMBEDDING_DIM})")

    return lines


def _migrate_lab(conn, spec: Spec, lab: LabSource, target_data_dir: str,
                 target_pkb_root: str, user_id: str) -> List[str]:
    lines = [f"world {lab.world_slug!r} (user={user_id!r})",
             f"  source data dir  {lab.data_dir}",
             f"  source pkb root  {lab.pkb_root}"]

    # A lab root with data/ and pkb/ directories but no rows in any table is
    # an abandoned scaffold, not a world — `lab/instances/finance` on the live
    # lab is exactly this. Creating a world for it would manufacture a
    # permanently empty world that `db doctor` then warns about forever.
    # Nothing is deleted; the source stays on disk for the operator to decide.
    if not any(lab.tables.values()):
        lines.append(
            "  skipped — no rows in any table (abandoned scaffold, not a "
            "world). Source left untouched.")
        return lines

    world = _get_or_create_world(
        conn, user_id=user_id, slug=lab.world_slug,
        display_name=_display_name(lab.world_slug))
    lines.append(f"  world id  {world.id}")

    already = repo.get_state(conn, world_id=world.id, key=_MIGRATION_STATE_KEY)
    if already is not None:
        lines.append(
            f"  already migrated at {already.get('completed_at', '?')} "
            f"(spec_sha256={already.get('spec_sha256', '?')[:12]}); skipping")
        return lines

    for table_spec in spec.vector_tables:
        lines.extend(_migrate_table(conn, world, table_spec, lab,
                                    target_data_dir, target_pkb_root))

    repo.set_state(conn, world_id=world.id, key=_MIGRATION_STATE_KEY, value={
        "from": "1.x", "spec_sha256": spec.sha256, "completed_at": _now(),
    })
    lines.append(f"  recorded migration completion for world {lab.world_slug!r}")
    return lines


def _dry_run_report(plan: MigrationPlan, *, target_data_dir: str,
                    target_pkb_root: str, user_id: str) -> List[str]:
    lines = ["DRY RUN — no changes written", ""]
    if not plan.labs:
        lines.append("  no 1.x lab source(s) discovered; nothing to do")
    for lab in plan.labs:
        lines.append(f"world {lab.world_slug!r} (user={user_id!r})")
        lines.append(f"  source data dir  {lab.data_dir}")
        lines.append(f"  source pkb root  {lab.pkb_root}")
        any_rows = False
        for name in sorted(lab.tables):
            count = lab.tables[name]
            if count:
                any_rows = True
                lines.append(
                    f"  would read {count} row(s) from {name!r}, re-embed "
                    f"with {EMBEDDING_MODEL} ({EMBEDDING_DIM}-dim), and write "
                    f"world_id={{new-world-id}}")
        if not any_rows:
            lines.append("  no rows in any table; world would be created empty")
    lines.append("")
    lines.append(f"target data dir  {Path(target_data_dir).resolve()}")
    lines.append(f"target pkb root  {Path(target_pkb_root).resolve()}")
    lines.append("re-run with --apply to execute")
    return lines


def migrate(plan: MigrationPlan, *, target_data_dir: str, target_pkb_root: str,
            user_id: str = "local", apply: bool = False,
            spec: Optional[Spec] = None) -> List[str]:
    """Migrate every discovered 1.x lab source into the 2.0 target.

    Dry-run (``apply=False``, the default) writes nothing — not the
    relational schema, not a Lance table, not a world row — and returns a
    report of what would happen. ``apply=True`` executes it.

    Idempotent: a world that already carries a ``migration`` key in
    ``world_state`` is skipped entirely, so a second ``apply=True`` run
    creates no duplicate worlds, entities, content_refs, or Lance rows.
    """
    if spec is None:
        spec = load_spec(DEFAULT_SPEC_DIR)

    if not apply:
        return _dry_run_report(plan, target_data_dir=target_data_dir,
                               target_pkb_root=target_pkb_root,
                               user_id=user_id)

    target_data_dir = str(Path(target_data_dir).resolve())
    target_pkb_root = str(Path(target_pkb_root).resolve())

    lines: List[str] = [
        f"target data dir  {target_data_dir}",
        f"target pkb root  {target_pkb_root}", "",
    ]

    # 1. Relational schema — the 2.0 target may be brand new.
    atlas.schema_apply(database_path(target_data_dir), spec.schema_sql_path)

    # 2. Vector tables — create any that are missing, with the declared
    #    (world_id-bearing) schema. Never destructive: a fresh target has
    #    only CREATE_TABLE changes pending.
    rplan = reconcile.plan(spec, target_data_dir, target_pkb_root)
    if not rplan.is_empty:
        reconcile.apply(rplan, allow_destructive=False)

    conn = connect(target_data_dir)
    try:
        for lab in plan.labs:
            lines.extend(_migrate_lab(conn, spec, lab, target_data_dir,
                                      target_pkb_root, user_id))
    finally:
        conn.close()

    return lines
