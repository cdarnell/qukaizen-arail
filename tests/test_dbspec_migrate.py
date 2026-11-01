"""ARAIL 1.x -> 2.0 migration tests.

Builds a synthetic 1.x lab under `tmp_path` with real Lance tables — 128-dim
SHA1-hash-style vectors and no `world_id` column, exactly the corrupt shape
the migration exists to fix — then migrates it into a fresh 2.0 target and
checks the result against `arail.dbspec.doctor.run_doctor`, the acceptance
gate the migration must satisfy.

Requires a local Ollama with `nomic-embed-text` pulled (real embedding calls,
no mocking of the embedding path — that is the thing under test).
"""

from __future__ import annotations

import random
from pathlib import Path

import lancedb
import pyarrow as pa
import pytest

from arail.dbspec import migrate as migrate_mod
from arail.dbspec.db import connect
from arail.dbspec.doctor import run_doctor
from arail.dbspec.embed import probe
from arail.dbspec.generated.models_registry import EMBEDDING_DIM, EMBEDDING_MODEL
from arail.dbspec.reconcile import resolve_dataset_path
from arail.dbspec.spec import load_spec

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_DIR = REPO_ROOT / "spec"
ATLAS = "/opt/homebrew/bin/atlas"

_OLD_DIM = 128

pytestmark = [
    pytest.mark.skipif(not Path(ATLAS).is_file(),
                       reason="atlas binary not installed"),
]


@pytest.fixture(scope="module")
def _embedding_available():
    ok, message = probe()
    if not ok:
        pytest.skip(f"embedding unavailable: {message}")
    return True


@pytest.fixture()
def spec():
    return load_spec(SPEC_DIR)


# ---------------------------------------------------------------------------
# Synthetic 1.x lab builder
# ---------------------------------------------------------------------------

_ARROW_BY_SPEC_TYPE = {
    "string": pa.string(), "double": pa.float64(), "float": pa.float32(),
    "int32": pa.int32(), "int64": pa.int64(), "bool": pa.bool_(),
    "timestamp": pa.timestamp("us"), "binary": pa.binary(),
}

# Old 1.x row content per table, deliberately small (a "handful" of rows).
# Each lab source uses a distinct namespace prefix so its rows do not collide
# on primary key with another lab's rows in the shared 2.0 Lance table —
# `content_refs` and every Lance table's declared primary key
# (path / slug / agent_id / id) are unique per `lance_table`, not scoped by
# `world_id` (see idx_content_refs_row in spec/schema/schema.hcl); a
# migration where two 1.x labs share an identical relative path is a known,
# separate architectural gap (see BUILD_LOG.md), not something this test
# exercises.
def _old_rows(namespace: str) -> dict:
    return {
        "pkb_pages": [
            {"path": f"{namespace}/notes/yield-curve.md", "name": "Yield Curve",
             "mtime": 1.0, "source_kind": "user"},
            {"path": f"{namespace}/notes/duration.md", "name": "Duration Risk",
             "mtime": 2.0, "source_kind": "user"},
        ],
        "wiki_nodes": [
            {"slug": f"{namespace}-finance", "section": "overview",
             "title": "Finance World"},
            {"slug": f"{namespace}-finance", "section": "glossary",
             "title": "Glossary"},
        ],
        "agent_workflows": [
            {"agent_id": f"{namespace}-buddy", "status": "idle",
             "objective": "watch the lab", "current_task": None,
             "next_step": None, "pause_reason": None,
             "updated_at": "2026-08-08T00:00:00Z",
             "summary": "nothing to report"},
        ],
        "experiments": [
            {"id": f"{namespace}-exp-1", "domain": "debt", "status": "complete"},
        ],
    }


def _build_old_table(spec_obj, base_dir: Path, table_name: str, *,
                     namespace: str):
    """Write a real 1.x-shaped Lance table: old columns, no world_id,
    128-dim hash-style vectors — at the exact path `resolve_dataset_path`
    would compute for this lab root (1.x and 2.0 subpaths coincide)."""
    table_spec = spec_obj.vector_table(table_name)
    rows = _old_rows(namespace)[table_name]

    old_columns = [c for c in table_spec.columns if c.name != "world_id"]
    fields = [pa.field(c.name, _ARROW_BY_SPEC_TYPE[c.type], nullable=c.nullable)
              for c in old_columns]
    fields.append(pa.field("vector", pa.list_(pa.float32(), _OLD_DIM)))
    schema = pa.schema(fields)

    data = {f.name: [] for f in fields}
    for i, row in enumerate(rows):
        for c in old_columns:
            data[c.name].append(row[c.name])
        random.seed(f"{table_name}-{i}")
        data["vector"].append([random.random() for _ in range(_OLD_DIM)])

    dataset_path = resolve_dataset_path(
        table_spec, str(base_dir / "data"), str(base_dir / "pkb"))
    p = Path(dataset_path)
    db = lancedb.connect(str(p.parent))
    tbl = db.create_table(p.stem, schema=schema)
    tbl.add(pa.table(data, schema=schema))
    return tbl, dataset_path


def _build_lab(spec_obj, base_dir: Path, *, namespace: str,
              tables=("pkb_pages", "wiki_nodes", "agent_workflows",
                      "experiments")):
    (base_dir / "data").mkdir(parents=True, exist_ok=True)
    (base_dir / "pkb").mkdir(parents=True, exist_ok=True)
    for name in tables:
        _build_old_table(spec_obj, base_dir, name, namespace=namespace)


@pytest.fixture()
def lab_root(tmp_path, spec):
    """A synthetic 1.x lab: root lab + one instance + one half-created
    instance dir that must be skipped."""
    root = tmp_path / "1x-lab"
    _build_lab(spec, root, namespace="root")
    _build_lab(spec, root / "instances" / "photography", namespace="photo")
    # Half-created instance dir: exists, but no data/ and no pkb/.
    (root / "instances" / "finance").mkdir(parents=True)
    return root


@pytest.fixture()
def target(tmp_path):
    data_dir = tmp_path / "2x" / "data"
    pkb_root = tmp_path / "2x" / "pkb"
    return data_dir, pkb_root


# ---------------------------------------------------------------------------
# discover()
# ---------------------------------------------------------------------------

def test_discover_finds_root_and_instances_and_skips_half_created(
        lab_root, spec):
    plan = migrate_mod.discover(str(lab_root), spec=spec)
    slugs = {lab.world_slug for lab in plan.labs}
    assert slugs == {"root", "photography"}
    assert "finance" not in slugs


def test_discover_reports_row_counts(lab_root, spec):
    plan = migrate_mod.discover(str(lab_root), spec=spec)
    root_lab = next(lab for lab in plan.labs if lab.world_slug == "root")
    assert root_lab.tables["pkb_pages"] == 2
    assert root_lab.tables["wiki_nodes"] == 2
    assert root_lab.tables["agent_workflows"] == 1
    assert root_lab.tables["experiments"] == 1


def test_plan_render_shows_absolute_paths(lab_root, spec):
    plan = migrate_mod.discover(str(lab_root), spec=spec)
    out = plan.render()
    for lab in plan.labs:
        assert Path(lab.data_dir).is_absolute()
        assert Path(lab.pkb_root).is_absolute()
        assert lab.data_dir in out
        assert lab.pkb_root in out


# ---------------------------------------------------------------------------
# Dry-run writes nothing
# ---------------------------------------------------------------------------

def test_dry_run_writes_nothing(lab_root, spec, target, _embedding_available):
    data_dir, pkb_root = target
    plan = migrate_mod.discover(str(lab_root), spec=spec)
    lines = migrate_mod.migrate(
        plan, target_data_dir=str(data_dir), target_pkb_root=str(pkb_root),
        user_id="local", apply=False, spec=spec)
    assert any("DRY RUN" in line for line in lines)

    # No schema, no worlds, no vector datasets.
    assert not (data_dir / "arail.db").exists()
    assert not (pkb_root / ".cache" / "lancedb" / "pkb_pages.lance").exists()


# ---------------------------------------------------------------------------
# --apply creates one world per lab source
# ---------------------------------------------------------------------------

def test_apply_creates_one_world_per_lab_source(lab_root, spec, target,
                                                 _embedding_available):
    data_dir, pkb_root = target
    plan = migrate_mod.discover(str(lab_root), spec=spec)
    migrate_mod.migrate(
        plan, target_data_dir=str(data_dir), target_pkb_root=str(pkb_root),
        user_id="local", apply=True, spec=spec)

    conn = connect(data_dir)
    rows = conn.execute(
        "SELECT slug, user_id FROM worlds ORDER BY slug").fetchall()
    conn.close()
    slugs = {(r["slug"], r["user_id"]) for r in rows}
    assert slugs == {("root", "local"), ("photography", "local")}


# ---------------------------------------------------------------------------
# Migrated rows carry world_id and 768-dim real vectors
# ---------------------------------------------------------------------------

def test_migrated_pkb_pages_rows_carry_world_id_and_768_dim_vector(
        lab_root, spec, target, _embedding_available):
    data_dir, pkb_root = target
    plan = migrate_mod.discover(str(lab_root), spec=spec)
    migrate_mod.migrate(
        plan, target_data_dir=str(data_dir), target_pkb_root=str(pkb_root),
        user_id="local", apply=True, spec=spec)

    table_spec = spec.vector_table("pkb_pages")
    dataset_path = resolve_dataset_path(table_spec, str(data_dir),
                                        str(pkb_root))
    tbl = lancedb.connect(str(Path(dataset_path).parent)).open_table(
        Path(dataset_path).stem)
    arrow = tbl.to_arrow()
    assert "world_id" in arrow.schema.names
    world_ids = set(arrow.column("world_id").to_pylist())
    assert world_ids  # non-empty
    assert all(wid for wid in world_ids)

    vec_field = arrow.schema.field("vector")
    assert vec_field.type.list_size == EMBEDDING_DIM
    assert tbl.count_rows() == 4  # 2 rows from root + 2 from photography


# ---------------------------------------------------------------------------
# content_refs recorded with the spec's embedding model and dim
# ---------------------------------------------------------------------------

def test_content_refs_recorded_with_spec_embedding_model_and_dim(
        lab_root, spec, target, _embedding_available):
    data_dir, pkb_root = target
    plan = migrate_mod.discover(str(lab_root), spec=spec)
    migrate_mod.migrate(
        plan, target_data_dir=str(data_dir), target_pkb_root=str(pkb_root),
        user_id="local", apply=True, spec=spec)

    conn = connect(data_dir)
    rows = conn.execute(
        "SELECT lance_table, embedding_model, embedding_dim FROM content_refs"
    ).fetchall()
    conn.close()

    assert rows  # non-empty
    tables_seen = {r["lance_table"] for r in rows}
    assert tables_seen == {"pkb_pages", "wiki_nodes"}
    for r in rows:
        assert r["embedding_model"] == EMBEDDING_MODEL
        assert r["embedding_dim"] == EMBEDDING_DIM


def test_pkb_pages_entities_created_and_linked(lab_root, spec, target,
                                               _embedding_available):
    data_dir, pkb_root = target
    plan = migrate_mod.discover(str(lab_root), spec=spec)
    migrate_mod.migrate(
        plan, target_data_dir=str(data_dir), target_pkb_root=str(pkb_root),
        user_id="local", apply=True, spec=spec)

    conn = connect(data_dir)
    entities = conn.execute(
        "SELECT kind, name FROM entities WHERE kind = 'document'").fetchall()
    linked = conn.execute(
        "SELECT COUNT(*) AS n FROM content_refs WHERE lance_table = "
        "'pkb_pages' AND entity_id IS NOT NULL").fetchone()
    conn.close()

    assert len(entities) == 4  # 2 pages per lab x 2 labs
    assert {e["name"] for e in entities} == {
        "root/notes/yield-curve.md", "root/notes/duration.md",
        "photo/notes/yield-curve.md", "photo/notes/duration.md",
    }
    assert linked["n"] == 4


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------

def test_migrate_twice_is_idempotent(lab_root, spec, target,
                                     _embedding_available):
    data_dir, pkb_root = target
    plan = migrate_mod.discover(str(lab_root), spec=spec)
    migrate_mod.migrate(
        plan, target_data_dir=str(data_dir), target_pkb_root=str(pkb_root),
        user_id="local", apply=True, spec=spec)

    def _counts():
        conn = connect(data_dir)
        n_worlds = conn.execute("SELECT COUNT(*) AS n FROM worlds").fetchone()["n"]
        n_entities = conn.execute("SELECT COUNT(*) AS n FROM entities").fetchone()["n"]
        n_refs = conn.execute("SELECT COUNT(*) AS n FROM content_refs").fetchone()["n"]
        conn.close()
        return n_worlds, n_entities, n_refs

    before = _counts()
    pkb_spec = spec.vector_table("pkb_pages")
    before_rows = lancedb.connect(
        str(Path(resolve_dataset_path(pkb_spec, str(data_dir), str(pkb_root))).parent)
    ).open_table("pkb_pages").count_rows()

    plan2 = migrate_mod.discover(str(lab_root), spec=spec)
    lines2 = migrate_mod.migrate(
        plan2, target_data_dir=str(data_dir), target_pkb_root=str(pkb_root),
        user_id="local", apply=True, spec=spec)

    after = _counts()
    after_rows = lancedb.connect(
        str(Path(resolve_dataset_path(pkb_spec, str(data_dir), str(pkb_root))).parent)
    ).open_table("pkb_pages").count_rows()

    assert before == after
    assert before_rows == after_rows
    assert any("already migrated" in line for line in lines2)


# ---------------------------------------------------------------------------
# Acceptance gate: doctor is clean on the migrated target
# ---------------------------------------------------------------------------

def test_doctor_is_clean_on_migrated_target(lab_root, spec, target,
                                            _embedding_available):
    data_dir, pkb_root = target
    plan = migrate_mod.discover(str(lab_root), spec=spec)
    migrate_mod.migrate(
        plan, target_data_dir=str(data_dir), target_pkb_root=str(pkb_root),
        user_id="local", apply=True, spec=spec)

    conn = connect(data_dir)
    report = run_doctor(spec, conn, str(data_dir), str(pkb_root))
    conn.close()

    assert report.ok, report.render()
    assert not report.errors


# ---------------------------------------------------------------------------
# Source data is untouched
# ---------------------------------------------------------------------------

def test_source_data_untouched_after_migration(lab_root, spec, target,
                                                _embedding_available):
    data_dir, pkb_root = target
    table_spec = spec.vector_table("pkb_pages")
    source_path = resolve_dataset_path(
        table_spec, str(lab_root / "data"), str(lab_root / "pkb"))
    before = lancedb.connect(str(Path(source_path).parent)).open_table(
        "pkb_pages").to_arrow()
    assert before.schema.field("vector").type.list_size == _OLD_DIM
    assert "world_id" not in before.schema.names
    before_rows = before.to_pylist()

    plan = migrate_mod.discover(str(lab_root), spec=spec)
    migrate_mod.migrate(
        plan, target_data_dir=str(data_dir), target_pkb_root=str(pkb_root),
        user_id="local", apply=True, spec=spec)

    after = lancedb.connect(str(Path(source_path).parent)).open_table(
        "pkb_pages").to_arrow()
    assert after.schema.field("vector").type.list_size == _OLD_DIM
    assert "world_id" not in after.schema.names
    assert after.to_pylist() == before_rows
