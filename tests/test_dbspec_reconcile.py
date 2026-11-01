"""LanceDB reconciler tests — the headline case is dimension-change refusal.

Every test builds real Lance tables with lancedb + pyarrow (no mocking of
the storage layer), then reconciles them against the real, shipped
`spec/vectors/vectors.hcl` via `load_spec("spec")`. That spec is the
contract; these tests hold the reconciler to it.
"""

from __future__ import annotations

import datetime
import random
from pathlib import Path

import lancedb
import pyarrow as pa
import pytest

from arail.dbspec.reconcile import (
    ChangeKind,
    ReconcileError,
    apply,
    optimize,
    plan,
    resolve_dataset_path,
)
from arail.dbspec.spec import IndexSpec, VectorTableSpec, load_spec

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_DIR = REPO_ROOT / "spec"

_ARROW_BY_SPEC_TYPE = {
    "string": pa.string(), "double": pa.float64(), "float": pa.float32(),
    "int32": pa.int32(), "int64": pa.int64(), "bool": pa.bool_(),
    "timestamp": pa.timestamp("us"), "binary": pa.binary(),
}


# ---------------------------------------------------------------------------
# Fixtures and table-building helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def spec():
    return load_spec(SPEC_DIR)


def _sample_value(spec_type: str, i: int):
    if spec_type == "string":
        return f"v{i}"
    if spec_type in ("double", "float"):
        return float(i)
    if spec_type in ("int32", "int64"):
        return i
    if spec_type == "bool":
        return bool(i % 2)
    if spec_type == "timestamp":
        return datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=i)
    if spec_type == "binary":
        return str(i).encode()
    raise ValueError(f"unhandled spec column type: {spec_type}")


def _build_table(base_dir: Path, table_spec: VectorTableSpec, *, rows: int,
                  dim: int | None = None, skip_columns: tuple = ()):
    """Create a real Lance table at the path `plan()` would resolve for
    `table_spec` under `base_dir`, with `rows` rows. Returns
    (LanceTable, dataset_path)."""
    dim = table_spec.vector.dim if dim is None else dim

    fields = []
    for col in table_spec.columns:
        if col.name in skip_columns:
            continue
        fields.append(pa.field(col.name, _ARROW_BY_SPEC_TYPE[col.type],
                                nullable=col.nullable))
    fields.append(pa.field(table_spec.vector.name,
                            pa.list_(pa.float32(), dim)))
    schema = pa.schema(fields)

    data = {}
    for f in fields:
        if f.name == table_spec.vector.name:
            data[f.name] = [[random.random() for _ in range(dim)]
                             for _ in range(rows)]
        else:
            spec_type = next(c.type for c in table_spec.columns
                              if c.name == f.name)
            data[f.name] = [_sample_value(spec_type, i) for i in range(rows)]

    dataset_path = resolve_dataset_path(
        table_spec, str(base_dir / "data"), str(base_dir / "pkb"))
    p = Path(dataset_path)
    db = lancedb.connect(str(p.parent))
    tbl = db.create_table(p.stem, schema=schema)
    if rows:
        tbl.add(pa.table(data, schema=schema))
    return tbl, dataset_path


def _create_declared_index(tbl, idx: IndexSpec, *, metric: str | None = None):
    if idx.is_vector_index:
        tbl.create_index(
            metric=metric or idx.metric,
            num_partitions=idx.num_partitions,
            num_sub_vectors=idx.num_sub_vectors,
            vector_column_name=idx.column,
            index_type=idx.type,
            name=idx.name,
            replace=True,
        )
    else:
        tbl.create_scalar_index(idx.column, index_type=idx.type,
                                 name=idx.name)


def _plan(spec_obj, base_dir: Path):
    return plan(spec_obj, data_dir=str(base_dir / "data"),
                pkb_root=str(base_dir / "pkb"))


def _reopen(dataset_path: str):
    p = Path(dataset_path)
    db = lancedb.connect(str(p.parent))
    return db.open_table(p.stem)


# ---------------------------------------------------------------------------
# 1. Empty data dir -> CREATE_TABLE for every declared table
# ---------------------------------------------------------------------------

def test_plan_against_empty_dirs_proposes_create_table_for_all(tmp_path, spec):
    p = _plan(spec, tmp_path)
    assert len(p.changes) == 4
    assert {c.kind for c in p.changes} == {ChangeKind.CREATE_TABLE}
    assert {c.table for c in p.changes} == {t.name for t in spec.vector_tables}
    assert p.destructive == ()
    assert not p.is_empty


# ---------------------------------------------------------------------------
# 2. Missing column -> ADD_COLUMN, and apply() actually adds it
# ---------------------------------------------------------------------------

def test_missing_world_id_column_is_added(tmp_path, spec):
    pkb_pages = spec.vector_table("pkb_pages")
    _tbl, dataset_path = _build_table(
        tmp_path, pkb_pages, rows=5, skip_columns=("world_id",))

    p = _plan(spec, tmp_path)
    add_changes = [c for c in p.changes if c.table == "pkb_pages"
                   and c.kind == ChangeKind.ADD_COLUMN]
    assert add_changes, p.render()
    assert all("world_id" in c.detail for c in add_changes)
    assert p.destructive == ()

    apply(p)  # also creates the other 3 tables, which is fine: non-destructive

    reopened = _reopen(dataset_path)
    assert "world_id" in reopened.schema.names


# ---------------------------------------------------------------------------
# 3 & 4. Dimension change: destructive, refused without permission,
#         allowed with it. The headline test.
# ---------------------------------------------------------------------------

def test_dimension_change_is_destructive_and_refused_by_default(tmp_path, spec):
    pkb_pages = spec.vector_table("pkb_pages")
    _tbl, dataset_path = _build_table(tmp_path, pkb_pages, rows=5, dim=128)

    p = _plan(spec, tmp_path)
    dim_changes = [c for c in p.changes if c.table == "pkb_pages"
                   and c.kind == ChangeKind.CHANGE_DIMENSION]
    assert dim_changes, p.render()
    assert dim_changes[0].destructive
    assert dim_changes[0] in p.destructive
    assert dim_changes[0] in p.changes

    with pytest.raises(ReconcileError) as exc_info:
        apply(p, allow_destructive=False)
    assert "pkb_pages" in str(exc_info.value)

    # Refusal is all-or-nothing: the 1.x-dimension dataset must be untouched.
    reopened = _reopen(dataset_path)
    assert reopened.schema.field(pkb_pages.vector.name).type.list_size == 128


def test_dimension_change_applies_when_destructive_allowed(tmp_path, spec):
    pkb_pages = spec.vector_table("pkb_pages")
    _tbl, dataset_path = _build_table(tmp_path, pkb_pages, rows=5, dim=128)

    p = _plan(spec, tmp_path)
    apply(p, allow_destructive=True)  # must not raise

    reopened = _reopen(dataset_path)
    assert reopened.schema.field(pkb_pages.vector.name).type.list_size == 768


# ---------------------------------------------------------------------------
# 5. Metric mismatch is destructive
# ---------------------------------------------------------------------------

def test_metric_mismatch_is_destructive(tmp_path, spec):
    pkb_pages = spec.vector_table("pkb_pages")
    tbl, dataset_path = _build_table(tmp_path, pkb_pages, rows=300)
    vec_idx = next(i for i in pkb_pages.indexes if i.is_vector_index)
    _create_declared_index(tbl, vec_idx, metric="l2")  # spec declares cosine

    p = _plan(spec, tmp_path)
    metric_changes = [c for c in p.changes if c.table == "pkb_pages"
                       and c.kind == ChangeKind.CHANGE_METRIC]
    assert metric_changes, p.render()
    assert metric_changes[0].destructive
    assert metric_changes[0] in p.destructive

    with pytest.raises(ReconcileError):
        apply(p, allow_destructive=False)

    # No CHANGE_DIMENSION should have been raised alongside it; dim is fine.
    assert not any(c.kind == ChangeKind.CHANGE_DIMENSION
                   for c in p.changes if c.table == "pkb_pages")


# ---------------------------------------------------------------------------
# 6. Index creation is row-gated
# ---------------------------------------------------------------------------

def test_index_creation_skipped_below_threshold(tmp_path, spec):
    wiki = spec.vector_table("wiki_nodes")
    vec_idx_name = next(i for i in wiki.indexes if i.is_vector_index).name
    below = wiki.index_min_rows - 56
    assert below > 0
    _build_table(tmp_path, wiki, rows=below)

    p = _plan(spec, tmp_path)
    vector_index_changes = [
        c for c in p.changes if c.table == "wiki_nodes"
        and c.kind == ChangeKind.CREATE_INDEX and vec_idx_name in c.detail]
    assert vector_index_changes == []

    reasons = [r for r in p.index_skip_reasons if "wiki_nodes" in r]
    assert len(reasons) == 1
    assert f"{below} rows < {wiki.index_min_rows} threshold" in reasons[0]


def test_index_creation_proposed_at_or_above_threshold(tmp_path, spec):
    wiki = spec.vector_table("wiki_nodes")
    vec_idx_name = next(i for i in wiki.indexes if i.is_vector_index).name
    at_threshold = wiki.index_min_rows
    _build_table(tmp_path, wiki, rows=at_threshold)

    p = _plan(spec, tmp_path)
    vector_index_changes = [
        c for c in p.changes if c.table == "wiki_nodes"
        and c.kind == ChangeKind.CREATE_INDEX and vec_idx_name in c.detail]
    assert len(vector_index_changes) == 1
    assert not vector_index_changes[0].destructive
    assert f"{at_threshold} rows >= {wiki.index_min_rows} threshold" \
        in vector_index_changes[0].detail
    assert not any("wiki_nodes" in r for r in p.index_skip_reasons)


# ---------------------------------------------------------------------------
# 7. Idempotence: a fully-conforming set of tables plans no changes
# ---------------------------------------------------------------------------

def test_plan_is_idempotent_on_conforming_tables(tmp_path, spec):
    pkb_pages = spec.vector_table("pkb_pages")
    wiki = spec.vector_table("wiki_nodes")
    agent = spec.vector_table("agent_workflows")
    experiments = spec.vector_table("experiments")

    # pkb_pages: above the index threshold, every declared index built.
    tbl_pkb, _ = _build_table(tmp_path, pkb_pages, rows=300)
    for idx in pkb_pages.indexes:
        _create_declared_index(tbl_pkb, idx)

    # wiki_nodes: below threshold — only the (ungated) scalar index exists;
    # the vector index is correctly absent, which is still conforming.
    tbl_wiki, _ = _build_table(tmp_path, wiki, rows=10)
    for idx in wiki.indexes:
        if not idx.is_vector_index:
            _create_declared_index(tbl_wiki, idx)

    # agent_workflows / experiments declare no indexes at all.
    _build_table(tmp_path, agent, rows=5)
    _build_table(tmp_path, experiments, rows=5)

    p = _plan(spec, tmp_path)
    assert p.is_empty, p.render()
    assert p.destructive == ()


# ---------------------------------------------------------------------------
# 8. render() always prints full absolute paths
# ---------------------------------------------------------------------------

def test_render_includes_absolute_paths(tmp_path, spec):
    p = _plan(spec, tmp_path)
    out = p.render()
    assert p.changes  # sanity: empty dirs, there is something to report
    for change in p.changes:
        assert Path(change.dataset_path).is_absolute()
        assert change.dataset_path in out
    for a in p.actual:
        assert Path(a.dataset_path).is_absolute()
        assert a.dataset_path in out


# ---------------------------------------------------------------------------
# Extra coverage beyond the required list: optimize() runs and reports.
# ---------------------------------------------------------------------------

def test_optimize_reports_before_and_after_for_each_table(tmp_path, spec):
    for table_spec in spec.vector_tables:
        _build_table(tmp_path, table_spec, rows=5)

    reports = optimize(spec, data_dir=str(tmp_path / "data"),
                        pkb_root=str(tmp_path / "pkb"))
    assert len(reports) == len(spec.vector_tables)
    for table_spec, report in zip(spec.vector_tables, reports):
        assert table_spec.name in report
        assert "fragments" in report
        assert "versions" in report


def test_optimize_skips_absent_tables(tmp_path, spec):
    reports = optimize(spec, data_dir=str(tmp_path / "data"),
                        pkb_root=str(tmp_path / "pkb"))
    assert len(reports) == len(spec.vector_tables)
    assert all("does not exist" in r for r in reports)
