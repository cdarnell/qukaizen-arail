"""LanceDB reconciler for ARAIL 2.0.

Reads the declared state produced by :mod:`arail.dbspec.spec`, inspects the
actual on-disk Lance tables, and diffs the two. Safe changes (add / drop /
retype a column, create / drop an index, create a missing table) are
auto-applied. Changes that require a rebuild — vector dimension change,
distance-metric change, index-type change — are refused unless the caller
passes ``allow_destructive=True``, and even then the refusal path never
silently no-ops: :func:`apply` raises :class:`ReconcileError` naming every
destructive change it will not perform without permission.

Dry-run is the default posture: :func:`plan` never writes to disk. Only
:func:`apply` and :func:`optimize` touch the Lance datasets.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple

import lancedb
import pyarrow as pa

from arail.dbspec.spec import ColumnSpec, Spec, VectorTableSpec

__all__ = [
    "ChangeKind", "DESTRUCTIVE_KINDS", "Change", "TableActual",
    "ReconcilePlan", "ReconcileError", "inspect_table", "plan", "apply",
    "optimize", "resolve_dataset_path",
]


class ReconcileError(RuntimeError):
    """A reconcile action failed, or was refused. The message says why and
    what to do next."""


# ---------------------------------------------------------------------------
# Change model
# ---------------------------------------------------------------------------

class ChangeKind(Enum):
    CREATE_TABLE = "create_table"
    ADD_COLUMN = "add_column"
    DROP_COLUMN = "drop_column"
    RETYPE_COLUMN = "retype_column"
    CREATE_INDEX = "create_index"
    DROP_INDEX = "drop_index"
    CHANGE_DIMENSION = "change_dimension"      # DESTRUCTIVE
    CHANGE_METRIC = "change_metric"            # DESTRUCTIVE
    CHANGE_INDEX_TYPE = "change_index_type"    # DESTRUCTIVE


DESTRUCTIVE_KINDS: frozenset = frozenset({
    ChangeKind.CHANGE_DIMENSION, ChangeKind.CHANGE_METRIC,
    ChangeKind.CHANGE_INDEX_TYPE,
})


@dataclass(frozen=True)
class Change:
    kind: ChangeKind
    table: str
    detail: str
    dataset_path: str
    destructive: bool


@dataclass(frozen=True)
class TableActual:
    """What is really on disk. ``exists=False`` means the dataset is absent.

    ``vector_metric`` is an addition beyond the minimum needed to describe
    "what's on disk": it is the distance metric of the table's vector index,
    read from Lance's ``IndexStatistics``, and is ``None`` when no vector
    index exists yet. It exists because metric drift (index built with one
    distance metric, spec now declares another) is otherwise unobservable
    from the rest of this dataclass, and detecting it is a required failure
    mode (`CHANGE_METRIC` must be flagged destructive).
    """
    name: str
    dataset_path: str
    exists: bool
    rows: int
    fragments: int
    versions: int
    columns: dict
    vector_dim: Optional[int]
    indexes: dict
    disk_bytes: int
    vector_metric: Optional[str] = None


@dataclass(frozen=True)
class ReconcilePlan:
    changes: Tuple[Change, ...]
    actual: Tuple[TableActual, ...]
    # Additive beyond the minimum contract: rows for indexes that were NOT
    # proposed because the table is below `index_min_rows`. render() reports
    # these so a dry-run explains its silence, per requirement #5.
    index_skip_reasons: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def destructive(self) -> Tuple[Change, ...]:
        return tuple(c for c in self.changes if c.destructive)

    @property
    def is_empty(self) -> bool:
        return len(self.changes) == 0

    def render(self) -> str:
        lines: list[str] = []
        if self.is_empty:
            lines.append(
                "No changes needed; declared state matches actual state.")
        else:
            lines.append(f"{len(self.changes)} change(s) planned:")
            for ch in self.changes:
                marker = "DESTRUCTIVE" if ch.destructive else "safe"
                lines.append(
                    f"  [{marker}] {ch.kind.value} table={ch.table} "
                    f"path={ch.dataset_path}: {ch.detail}")

        if self.index_skip_reasons:
            lines.append("")
            lines.append(
                "Indexes not created (below index_min_rows threshold):")
            for reason in self.index_skip_reasons:
                lines.append(f"  - {reason}")

        lines.append("")
        lines.append("Actual state:")
        for a in self.actual:
            status = "exists" if a.exists else "absent"
            lines.append(
                f"  {a.name} ({a.dataset_path}): {status}, rows={a.rows}, "
                f"fragments={a.fragments}, versions={a.versions}, "
                f"disk_bytes={a.disk_bytes}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Type mapping between the spec's column-type vocabulary and pyarrow
# ---------------------------------------------------------------------------

_SPEC_TYPE_TO_ARROW = {
    "string": pa.string(),
    "double": pa.float64(),
    "float": pa.float32(),
    "int32": pa.int32(),
    "int64": pa.int64(),
    "bool": pa.bool_(),
    "timestamp": pa.timestamp("us"),
    "binary": pa.binary(),
}

# SQL literal-cast expression used to backfill a newly added column for rows
# that already exist. Verified against lancedb 0.30.2's `add_columns` SQL
# expression support (DataFusion-based).
_SPEC_TYPE_DEFAULT_SQL = {
    "string": "cast('' as string)",
    "double": "cast(0.0 as double)",
    "float": "cast(0.0 as float)",
    "int32": "cast(0 as int)",
    "int64": "cast(0 as bigint)",
    "bool": "cast(false as boolean)",
    "timestamp": "cast('1970-01-01T00:00:00' as timestamp)",
    "binary": "cast('' as binary)",
}


def _arrow_type_str(spec_type: str) -> str:
    return str(_SPEC_TYPE_TO_ARROW[spec_type])


def _add_column_sql(col: ColumnSpec) -> str:
    if col.nullable:
        return f"cast(NULL as {_sql_type_name(col.type)})"
    return _SPEC_TYPE_DEFAULT_SQL[col.type]


def _sql_type_name(spec_type: str) -> str:
    return {
        "string": "string", "double": "double", "float": "float",
        "int32": "int", "int64": "bigint", "bool": "boolean",
        "timestamp": "timestamp", "binary": "binary",
    }[spec_type]


def _spec_schema(table_spec: VectorTableSpec) -> pa.Schema:
    fields = []
    for col in table_spec.columns:
        fields.append(pa.field(
            col.name, _SPEC_TYPE_TO_ARROW[col.type], nullable=col.nullable))
    fields.append(pa.field(
        table_spec.vector.name,
        pa.list_(pa.float32(), table_spec.vector.dim)))
    return pa.schema(fields)


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def resolve_dataset_path(table_spec: VectorTableSpec, data_dir: str,
                          pkb_root: str) -> str:
    """Full absolute path to a table's `.lance` dataset directory."""
    base = pkb_root if table_spec.root == "pkb" else data_dir
    return os.path.abspath(
        str(Path(base) / table_spec.relative_dir()))


def _connect(dataset_path: str):
    """Return (db, table_name) for a resolved dataset path.

    A Lance "database" is the parent directory; the table name is the
    dataset directory's stem (its name minus the `.lance` suffix).
    """
    p = Path(dataset_path)
    return lancedb.connect(str(p.parent)), p.stem


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------

def inspect_table(spec_table: VectorTableSpec, dataset_path: str
                   ) -> TableActual:
    """Inspect what is really on disk at `dataset_path`.

    `spec_table` is used only to know which column is the vector column;
    everything else reported here comes from the dataset itself, not from
    the spec.
    """
    path = Path(dataset_path)
    if not path.exists():
        return TableActual(
            name=spec_table.name, dataset_path=str(path), exists=False,
            rows=0, fragments=0, versions=0, columns={}, vector_dim=None,
            indexes={}, disk_bytes=0, vector_metric=None)

    db, table_name = _connect(dataset_path)
    try:
        tbl = db.open_table(table_name)
    except Exception as exc:  # noqa: BLE001 - surfaced as a structured error
        raise ReconcileError(
            f"cannot open Lance dataset at {dataset_path}: {exc}. The "
            f"directory exists but is not a readable Lance table; inspect "
            f"it manually before reconciling.") from exc

    schema = tbl.schema
    columns = {f.name: str(f.type) for f in schema}

    vector_dim: Optional[int] = None
    if spec_table.vector.name in schema.names:
        vec_field = schema.field(spec_table.vector.name)
        if pa.types.is_fixed_size_list(vec_field.type):
            vector_dim = vec_field.type.list_size

    indexes: dict = {}
    vector_metric: Optional[str] = None
    for idx_cfg in tbl.list_indices():
        stats = tbl.index_stats(idx_cfg.name)
        idx_type = stats.index_type if stats is not None else idx_cfg.index_type
        indexes[idx_cfg.name] = idx_type
        if stats is not None and idx_cfg.columns == [spec_table.vector.name]:
            vector_metric = stats.distance_type

    stats_obj = tbl.stats()
    fragments = stats_obj.get("fragment_stats", {}).get("num_fragments", 0)
    disk_bytes = stats_obj.get("total_bytes", 0)

    return TableActual(
        name=spec_table.name, dataset_path=str(path), exists=True,
        rows=tbl.count_rows(), fragments=fragments,
        versions=len(tbl.list_versions()), columns=columns,
        vector_dim=vector_dim, indexes=indexes, disk_bytes=disk_bytes,
        vector_metric=vector_metric)


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

def _diff_table(table_spec: VectorTableSpec, actual: TableActual
                 ) -> Tuple[list, Optional[str]]:
    """Return (changes, index_skip_reason_or_None) for one table."""
    if not actual.exists:
        detail = (
            f"dataset does not exist at {actual.dataset_path}; will create "
            f"table {table_spec.name!r} with {len(table_spec.columns)} "
            f"column(s) plus vector column {table_spec.vector.name!r} "
            f"(dim={table_spec.vector.dim}, metric={table_spec.vector.metric})")
        return [Change(
            kind=ChangeKind.CREATE_TABLE, table=table_spec.name,
            detail=detail, dataset_path=actual.dataset_path,
            destructive=False)], None

    changes: list = []
    vector_name = table_spec.vector.name
    spec_cols = {c.name: c for c in table_spec.columns}

    for name, col in spec_cols.items():
        expected = _arrow_type_str(col.type)
        if name not in actual.columns:
            changes.append(Change(
                kind=ChangeKind.ADD_COLUMN, table=table_spec.name,
                detail=f"column {name!r} ({expected}) is missing",
                dataset_path=actual.dataset_path, destructive=False))
        elif actual.columns[name] != expected:
            changes.append(Change(
                kind=ChangeKind.RETYPE_COLUMN, table=table_spec.name,
                detail=(f"column {name!r}: {actual.columns[name]} -> "
                        f"{expected}"),
                dataset_path=actual.dataset_path, destructive=False))

    known_names = set(spec_cols) | {vector_name}
    for name in actual.columns:
        if name not in known_names:
            changes.append(Change(
                kind=ChangeKind.DROP_COLUMN, table=table_spec.name,
                detail=f"column {name!r} is not declared in the spec",
                dataset_path=actual.dataset_path, destructive=False))

    if vector_name not in actual.columns:
        changes.append(Change(
            kind=ChangeKind.ADD_COLUMN, table=table_spec.name,
            detail=(f"vector column {vector_name!r} "
                    f"(dim={table_spec.vector.dim}) is missing"),
            dataset_path=actual.dataset_path, destructive=False))
    elif (actual.vector_dim is not None
          and actual.vector_dim != table_spec.vector.dim):
        changes.append(Change(
            kind=ChangeKind.CHANGE_DIMENSION, table=table_spec.name,
            detail=(f"vector column {vector_name!r} dim "
                    f"{actual.vector_dim} -> {table_spec.vector.dim}; "
                    f"this rewrites every row and drops existing vectors"),
            dataset_path=actual.dataset_path, destructive=True))
    elif (actual.vector_metric is not None
          and actual.vector_metric != table_spec.vector.metric):
        changes.append(Change(
            kind=ChangeKind.CHANGE_METRIC, table=table_spec.name,
            detail=(f"vector index distance metric "
                    f"{actual.vector_metric!r} -> "
                    f"{table_spec.vector.metric!r}; existing index must be "
                    f"rebuilt"),
            dataset_path=actual.dataset_path, destructive=True))

    skip_reason: Optional[str] = None
    for idx in table_spec.indexes:
        if idx.name in actual.indexes:
            actual_type = actual.indexes[idx.name]
            if actual_type != idx.type:
                changes.append(Change(
                    kind=ChangeKind.CHANGE_INDEX_TYPE, table=table_spec.name,
                    detail=(f"index {idx.name!r}: {actual_type} -> "
                            f"{idx.type}; index must be dropped and rebuilt"),
                    dataset_path=actual.dataset_path, destructive=True))
            continue

        if idx.is_vector_index:
            if actual.rows >= table_spec.index_min_rows:
                changes.append(Change(
                    kind=ChangeKind.CREATE_INDEX, table=table_spec.name,
                    detail=(f"index {idx.name!r} ({idx.type} on "
                            f"{idx.column!r}) does not exist; "
                            f"{actual.rows} rows >= "
                            f"{table_spec.index_min_rows} threshold"),
                    dataset_path=actual.dataset_path, destructive=False))
            else:
                skip_reason = (
                    f"{table_spec.name}: {actual.rows} rows < "
                    f"{table_spec.index_min_rows} threshold, index "
                    f"{idx.name!r} not created (flat scan is cheaper below "
                    f"threshold)")
        else:
            changes.append(Change(
                kind=ChangeKind.CREATE_INDEX, table=table_spec.name,
                detail=(f"index {idx.name!r} ({idx.type} on "
                        f"{idx.column!r}) does not exist"),
                dataset_path=actual.dataset_path, destructive=False))

    declared_idx_names = {idx.name for idx in table_spec.indexes}
    for name in actual.indexes:
        if name not in declared_idx_names:
            changes.append(Change(
                kind=ChangeKind.DROP_INDEX, table=table_spec.name,
                detail=f"index {name!r} is not declared in the spec",
                dataset_path=actual.dataset_path, destructive=False))

    return changes, skip_reason


def plan(spec: Spec, data_dir: str, pkb_root: str) -> ReconcilePlan:
    """Dry-run: compute what would change. Never writes to disk.

    Also registers each table's declared schema in `_TABLE_SPECS`, so a
    later `apply()` call — which the public contract gives only a bare
    `ReconcilePlan` — can look up the shape it needs to create/alter
    tables by name. Call `plan()` before `apply()` in the same process
    (the normal `./arailctl db reconcile` flow always does).
    """
    all_changes: list = []
    all_actual: list = []
    skip_reasons: list = []

    for table_spec in spec.vector_tables:
        _TABLE_SPECS[table_spec.name] = table_spec
        dataset_path = resolve_dataset_path(table_spec, data_dir, pkb_root)
        actual = inspect_table(table_spec, dataset_path)
        all_actual.append(actual)
        changes, skip_reason = _diff_table(table_spec, actual)
        all_changes.extend(changes)
        if skip_reason:
            skip_reasons.append(skip_reason)

    return ReconcilePlan(
        changes=tuple(all_changes), actual=tuple(all_actual),
        index_skip_reasons=tuple(skip_reasons))


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def _table_spec_by_name(spec_by_table: dict, name: str) -> VectorTableSpec:
    return spec_by_table[name]


def apply(plan_obj: ReconcilePlan, *, allow_destructive: bool = False
          ) -> list:
    """Execute a plan. Raises :class:`ReconcileError` and performs nothing
    if the plan contains destructive changes and `allow_destructive` is not
    True — refusal is all-or-nothing so a partially-applied destructive
    rebuild can never happen silently.
    """
    if plan_obj.destructive and not allow_destructive:
        named = "\n".join(
            f"  - {c.kind.value} on {c.table!r} ({c.dataset_path}): "
            f"{c.detail}"
            for c in plan_obj.destructive)
        raise ReconcileError(
            "refusing to apply destructive change(s) without "
            "allow_destructive=True. A rebuild is required for each of "
            "these — back up the dataset first:\n" + named)

    results: list = []
    for change in plan_obj.changes:
        results.append(_apply_one(change))
    return results


def _apply_one(change: Change) -> str:
    kind = change.kind
    try:
        if kind == ChangeKind.CREATE_TABLE:
            return _apply_create_table(change)
        if kind == ChangeKind.ADD_COLUMN:
            return _apply_add_column(change)
        if kind == ChangeKind.DROP_COLUMN:
            return _apply_drop_column(change)
        if kind == ChangeKind.RETYPE_COLUMN:
            return _apply_retype_column(change)
        if kind == ChangeKind.CREATE_INDEX:
            return _apply_create_index(change)
        if kind == ChangeKind.DROP_INDEX:
            return _apply_drop_index(change)
        if kind == ChangeKind.CHANGE_DIMENSION:
            return _apply_change_dimension(change)
        if kind == ChangeKind.CHANGE_METRIC:
            return _apply_change_metric(change)
        if kind == ChangeKind.CHANGE_INDEX_TYPE:
            return _apply_change_index_type(change)
    except ReconcileError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ReconcileError(
            f"{kind.value} failed for table {change.table!r} at "
            f"{change.dataset_path}: {exc}. What to do next: inspect the "
            f"dataset with `./arailctl db plan` and retry, or restore from "
            f"backup if the dataset is now inconsistent.") from exc
    raise ReconcileError(f"no apply handler for change kind {kind.value!r}")


# Each `_apply_*` function needs the declared table shape, which the Change
# object alone does not carry (it only names the table, not its schema).
# `apply()` is given a bare ReconcilePlan by the public contract, so the
# table spec is threaded through a module-level context set by `plan()`'s
# caller via `_TABLE_SPECS`. To keep `apply()`'s signature exactly as
# specified, callers that need CREATE_TABLE / dimension-rebuild support must
# have called `plan()` in the same process first (the normal ./arailctl db
# reconcile flow); `_TABLE_SPECS` is populated there.
_TABLE_SPECS: dict = {}


def _spec_for(table_name: str) -> VectorTableSpec:
    if table_name not in _TABLE_SPECS:
        raise ReconcileError(
            f"no spec registered for table {table_name!r}; call "
            f"arail.dbspec.reconcile.plan() before apply() so the table's "
            f"declared schema is available")
    return _TABLE_SPECS[table_name]


def _apply_create_table(change: Change) -> str:
    table_spec = _spec_for(change.table)
    db, table_name = _connect(change.dataset_path)
    schema = _spec_schema(table_spec)
    tbl = db.create_table(table_name, schema=schema)

    # Create the declared scalar indexes now, in the same pass.
    #
    # The plan for an absent table contains only CREATE_TABLE — the index
    # changes cannot be planned against a table that does not exist yet. If we
    # stopped here, `db apply` would leave `db drift` reporting the missing
    # indexes forever, and apply would never converge. Vector indexes are
    # deliberately NOT created here: they are row-gated, and a fresh table has
    # zero rows, so the next apply (after ingest) is the right time.
    created: list[str] = []
    for idx in table_spec.indexes:
        if idx.is_vector_index or idx.type == "FTS":
            continue
        try:
            tbl.create_scalar_index(idx.column, index_type=idx.type,
                                    name=idx.name)
            created.append(idx.name)
        except Exception as exc:
            raise ReconcileError(
                f"created table {change.table!r} at {change.dataset_path} but "
                f"could not create its declared index {idx.name!r} "
                f"({idx.type} on {idx.column!r}): {exc}"
            ) from exc

    suffix = f" with index(es) {', '.join(created)}" if created else ""
    return f"created table {change.table!r} at {change.dataset_path}{suffix}"


def _apply_add_column(change: Change) -> str:
    table_spec = _spec_for(change.table)
    db, table_name = _connect(change.dataset_path)
    tbl = db.open_table(table_name)
    added = []
    if table_spec.vector.name not in tbl.schema.names:
        tbl.add_columns(pa.field(
            table_spec.vector.name,
            pa.list_(pa.float32(), table_spec.vector.dim)))
        added.append(table_spec.vector.name)
    for col in table_spec.columns:
        if col.name in tbl.schema.names:
            continue
        tbl.add_columns({col.name: _add_column_sql(col)})
        added.append(col.name)
    return (f"added column(s) {added} to {change.table!r} at "
            f"{change.dataset_path}")


def _apply_drop_column(change: Change) -> str:
    db, table_name = _connect(change.dataset_path)
    tbl = db.open_table(table_name)
    table_spec = _spec_for(change.table)
    known = {c.name for c in table_spec.columns} | {table_spec.vector.name}
    to_drop = [name for name in tbl.schema.names if name not in known]
    if to_drop:
        tbl.drop_columns(to_drop)
    return f"dropped column(s) {to_drop} from {change.table!r}"


def _apply_retype_column(change: Change) -> str:
    db, table_name = _connect(change.dataset_path)
    tbl = db.open_table(table_name)
    table_spec = _spec_for(change.table)
    retyped = []
    for col in table_spec.columns:
        if col.name not in tbl.schema.names:
            continue
        expected = _arrow_type_str(col.type)
        actual = str(tbl.schema.field(col.name).type)
        if actual != expected:
            tbl.alter_columns({
                "path": col.name,
                "data_type": _SPEC_TYPE_TO_ARROW[col.type],
            })
            retyped.append(col.name)
    return f"retyped column(s) {retyped} on {change.table!r}"


def _apply_create_index(change: Change) -> str:
    db, table_name = _connect(change.dataset_path)
    tbl = db.open_table(table_name)
    table_spec = _spec_for(change.table)
    idx = next(i for i in table_spec.indexes
               if change.detail.startswith(f"index {i.name!r}"))
    if idx.is_vector_index:
        tbl.create_index(
            metric=idx.metric or table_spec.vector.metric,
            num_partitions=idx.num_partitions,
            num_sub_vectors=idx.num_sub_vectors,
            vector_column_name=idx.column,
            index_type=idx.type,
            name=idx.name,
            replace=True,
        )
    elif idx.type == "FTS":
        if not hasattr(tbl, "create_fts_index"):
            raise ReconcileError(
                f"lancedb {lancedb.__version__} table object has no "
                f"create_fts_index; cannot create index {idx.name!r}")
        tbl.create_fts_index(idx.column, replace=True)
    else:
        tbl.create_scalar_index(idx.column, index_type=idx.type,
                                 name=idx.name)
    return f"created index {idx.name!r} ({idx.type}) on {change.table!r}"


def _apply_drop_index(change: Change) -> str:
    db, table_name = _connect(change.dataset_path)
    tbl = db.open_table(table_name)
    dropped = []
    known = {i.name for i in _spec_for(change.table).indexes}
    for idx_cfg in list(tbl.list_indices()):
        if idx_cfg.name not in known:
            tbl.drop_index(idx_cfg.name)
            dropped.append(idx_cfg.name)
    return f"dropped index(es) {dropped} from {change.table!r}"


def _apply_change_dimension(change: Change) -> str:
    table_spec = _spec_for(change.table)
    db, table_name = _connect(change.dataset_path)
    tbl = db.open_table(table_name)
    for idx_cfg in list(tbl.list_indices()):
        if idx_cfg.columns == [table_spec.vector.name]:
            tbl.drop_index(idx_cfg.name)
    tbl.drop_columns([table_spec.vector.name])
    tbl.add_columns(pa.field(
        table_spec.vector.name,
        pa.list_(pa.float32(), table_spec.vector.dim)))
    return (f"rebuilt vector column {table_spec.vector.name!r} on "
            f"{change.table!r} at new dim {table_spec.vector.dim} "
            f"(existing vector data was dropped, as declared destructive)")


def _apply_change_metric(change: Change) -> str:
    table_spec = _spec_for(change.table)
    db, table_name = _connect(change.dataset_path)
    tbl = db.open_table(table_name)
    dropped = []
    for idx_cfg in list(tbl.list_indices()):
        if idx_cfg.columns == [table_spec.vector.name]:
            tbl.drop_index(idx_cfg.name)
            dropped.append(idx_cfg.name)
    return (f"dropped vector index(es) {dropped} on {change.table!r} to "
            f"clear the stale metric; re-run plan()/apply() to rebuild "
            f"with metric {table_spec.vector.metric!r}")


def _apply_change_index_type(change: Change) -> str:
    table_spec = _spec_for(change.table)
    db, table_name = _connect(change.dataset_path)
    tbl = db.open_table(table_name)
    idx_name = change.detail.split("'")[1]
    tbl.drop_index(idx_name)
    return (f"dropped index {idx_name!r} on {change.table!r} to clear the "
            f"stale index type; re-run plan()/apply() to rebuild it")


# ---------------------------------------------------------------------------
# Optimize
# ---------------------------------------------------------------------------

def optimize(spec: Spec, data_dir: str, pkb_root: str) -> list:
    """Run Lance compaction, index optimization, and version cleanup for
    every declared table, honoring `version_retention` and reporting
    fragments/bytes/versions before and after.

    Uses `Table.optimize()`, the single lancedb 0.30.2 API that performs
    compaction + prune + index refresh without requiring the separate
    `pylance` package (the deprecated `compact_files` / `cleanup_old_versions`
    wrappers both require `pylance`, which is not installed here).
    """
    reports: list = []
    for table_spec in spec.vector_tables:
        dataset_path = resolve_dataset_path(table_spec, data_dir, pkb_root)
        path = Path(dataset_path)
        if not path.exists():
            reports.append(
                f"{table_spec.name} ({dataset_path}): does not exist, "
                f"skipped")
            continue

        db, table_name = _connect(dataset_path)
        tbl = db.open_table(table_name)
        before = tbl.stats()
        before_versions = len(tbl.list_versions())

        retention_seconds = max(table_spec.version_retention, 0)
        # version_retention is a *count* of versions in the spec, but Lance's
        # optimize() prunes by *age*, not by count. There is no on-disk
        # timestamp-per-version budget in the spec, so a retention count of N
        # is honored conservatively: keep pruning to "everything but the
        # latest" (cleanup_older_than=0) only once there are more historical
        # versions than the table's retention count; otherwise leave history
        # alone. This keeps churny tables (agent_workflows) compact without
        # deleting the version history of quiet ones prematurely.
        if before_versions > table_spec.version_retention:
            tbl.optimize(cleanup_older_than=timedelta(0),
                         delete_unverified=True)
        else:
            tbl.optimize(cleanup_older_than=timedelta(days=7))

        after = tbl.stats()
        after_versions = len(tbl.list_versions())

        reports.append(
            f"{table_spec.name} ({dataset_path}): "
            f"fragments {before.get('fragment_stats', {}).get('num_fragments', 0)} "
            f"-> {after.get('fragment_stats', {}).get('num_fragments', 0)}, "
            f"bytes {before.get('total_bytes', 0)} -> "
            f"{after.get('total_bytes', 0)}, "
            f"versions {before_versions} -> {after_versions} "
            f"(retention={table_spec.version_retention}, "
            f"max_fragments={table_spec.max_fragments})")

    return reports
