"""Doctor and drift-gate behaviour.

A clean doctor report only means something if the checks actually bite, so
every check here is exercised against induced corruption rather than against a
healthy lab.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from pathlib import Path

import lancedb
import numpy as np
import pyarrow as pa
import pytest

from arail.dbspec import reconcile
from arail.dbspec.cli import EXIT_OK, EXIT_PROBLEM, main
from arail.dbspec.db import connect
from arail.dbspec.doctor import ERROR, WARNING, run_doctor
from arail.dbspec.spec import load_spec

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_DIR = REPO_ROOT / "spec"
ATLAS = "/opt/homebrew/bin/atlas"

pytestmark = pytest.mark.skipif(
    not Path(ATLAS).is_file(), reason="atlas binary not installed")


@pytest.fixture()
def spec():
    return load_spec(SPEC_DIR)


@pytest.fixture()
def lab(tmp_path: Path, spec):
    """A lab with the schema applied and all vector tables created."""
    data_dir = tmp_path / "data"
    pkb_root = tmp_path / "pkb"
    data_dir.mkdir()
    pkb_root.mkdir()
    subprocess.run(
        [ATLAS, "schema", "apply",
         "--url", f"sqlite://{data_dir / 'arail.db'}",
         "--to", f"file://{SPEC_DIR / 'schema' / 'schema.hcl'}",
         "--dev-url", "sqlite://dev?mode=memory", "--auto-approve"],
        check=True, capture_output=True)
    plan = reconcile.plan(spec, str(data_dir), str(pkb_root))
    reconcile.apply(plan)
    return data_dir, pkb_root


def _world(conn, *, user_id="netsushi", slug="debt-finance",
           world_id="w-1", status="active"):
    conn.execute(
        "INSERT INTO worlds (id, slug, user_id, display_name, status, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (world_id, slug, user_id, slug.title(), status,
         "2026-08-08T00:00:00Z", "2026-08-08T00:00:00Z"))
    return world_id


def _content_ref(conn, world_id, *, row_key="a.md", model="nomic-embed-text",
                 dim=768, table="pkb_pages", ref_id="c-1"):
    conn.execute(
        "INSERT INTO content_refs (id, world_id, lance_table, lance_uri, "
        "row_key, embedding_model, embedding_dim, ingested_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (ref_id, world_id, table, f"file://{table}", row_key, model, dim,
         "2026-08-08T00:00:00Z"))


def _pkb_table(pkb_root: Path):
    return lancedb.connect(str(pkb_root / ".cache" / "lancedb")).open_table(
        "pkb_pages")


def _row(path: str, world_id: str, vector):
    return {"path": path, "name": path, "world_id": world_id,
            "mtime": 0.0, "source_kind": "user", "vector": vector}


def _findings(report, check):
    return [f for f in report.findings if f.check == check]


# ---------------------------------------------------------------------------
# Embedding provenance
# ---------------------------------------------------------------------------

def test_doctor_flags_embedding_model_drift(lab, spec):
    data_dir, pkb_root = lab
    conn = connect(data_dir)
    world = _world(conn)
    _content_ref(conn, world, model="hash-sha1-128")

    report = run_doctor(spec, conn, str(data_dir), str(pkb_root))
    conn.close()

    found = _findings(report, "embedding-model-drift")
    assert found, report.render()
    assert found[0].severity == ERROR
    assert found[0].user_id == "netsushi"
    assert not report.ok


def test_doctor_flags_embedding_dim_drift(lab, spec):
    """The 1.x dimension is the realistic case."""
    data_dir, pkb_root = lab
    conn = connect(data_dir)
    world = _world(conn)
    _content_ref(conn, world, dim=128)

    report = run_doctor(spec, conn, str(data_dir), str(pkb_root))
    conn.close()

    found = _findings(report, "embedding-dim-drift")
    assert found, report.render()
    assert found[0].severity == ERROR
    assert "128" in found[0].message


def test_doctor_attributes_findings_per_user(lab, spec):
    """Single-user corruption must be visible, not averaged away."""
    data_dir, pkb_root = lab
    conn = connect(data_dir)
    good = _world(conn, user_id="alice", slug="ai", world_id="w-alice")
    bad = _world(conn, user_id="bob", slug="ai", world_id="w-bob")
    _content_ref(conn, good, ref_id="c-good", row_key="a.md")
    _content_ref(conn, bad, ref_id="c-bad", row_key="b.md", dim=128)

    report = run_doctor(spec, conn, str(data_dir), str(pkb_root))
    conn.close()

    drift = _findings(report, "embedding-dim-drift")
    assert len(drift) == 1
    assert drift[0].user_id == "bob"


# ---------------------------------------------------------------------------
# Orphans and degenerate vectors
# ---------------------------------------------------------------------------

def test_doctor_flags_orphaned_content_refs(lab, spec):
    data_dir, pkb_root = lab
    conn = connect(data_dir)
    world = _world(conn)
    _pkb_table(pkb_root).add([_row("real.md", world, np.ones(768, dtype=np.float32))])
    _content_ref(conn, world, row_key="ghost.md")

    report = run_doctor(spec, conn, str(data_dir), str(pkb_root))
    conn.close()

    found = _findings(report, "content-ref-orphan")
    assert found, report.render()
    assert found[0].severity == ERROR


def test_doctor_flags_zero_vectors(lab, spec):
    """A zero vector is what a failed embedding call leaves behind.

    1.x's `hash_embedding` returned an all-zero vector for empty or
    tokenless input instead of raising, so this is the realistic corruption.
    """
    data_dir, pkb_root = lab
    conn = connect(data_dir)
    _world(conn)
    _pkb_table(pkb_root).add([
        _row("zero.md", "w-1", np.zeros(768, dtype=np.float32)),
        _row("fine.md", "w-1", np.ones(768, dtype=np.float32)),
    ])

    report = run_doctor(spec, conn, str(data_dir), str(pkb_root))
    conn.close()

    degenerate = _findings(report, "degenerate-vector")
    assert degenerate, report.render()
    assert "zero vector" in " ".join(f.message for f in degenerate)
    assert not report.ok


def test_lancedb_refuses_nan_vectors_at_write(lab):
    """Pins why the doctor's NaN check is defense-in-depth, not dead code.

    lancedb 0.30.2 rejects NaN vectors on both `add()` and `create_table()`,
    so a NaN cannot enter through our write path at all — the doctor check
    exists for datasets written by another tool or an older version. If a
    future lancedb stops rejecting these, this test fails and tells us the
    doctor's NaN branch has become load-bearing.
    """
    _, pkb_root = lab
    with pytest.raises((ValueError, RuntimeError), match="(?i)nan"):
        _pkb_table(pkb_root).add(
            [_row("nan.md", "w-1", np.full(768, np.nan, dtype=np.float32))])


def test_doctor_flags_world_with_no_entities(lab, spec):
    data_dir, pkb_root = lab
    conn = connect(data_dir)
    _world(conn)
    report = run_doctor(spec, conn, str(data_dir), str(pkb_root))
    conn.close()

    found = _findings(report, "world-empty")
    assert found and found[0].severity == WARNING


def test_doctor_is_clean_on_a_healthy_lab(lab, spec):
    data_dir, pkb_root = lab
    conn = connect(data_dir)
    world = _world(conn)
    conn.execute(
        "INSERT INTO entities (id, world_id, kind, name, created_at, updated_at)"
        " VALUES ('e-1', ?, 'term', 'yield curve', ?, ?)",
        (world, "2026-08-08T00:00:00Z", "2026-08-08T00:00:00Z"))
    _pkb_table(pkb_root).add([_row("a.md", world, np.ones(768, dtype=np.float32))])
    _content_ref(conn, world, row_key="a.md")

    report = run_doctor(spec, conn, str(data_dir), str(pkb_root))
    conn.close()
    assert report.ok, report.render()
    assert not report.errors


# ---------------------------------------------------------------------------
# The drift gate — required: non-zero exit on induced drift.
# ---------------------------------------------------------------------------

def _cli(lab, *argv):
    data_dir, pkb_root = lab
    return main(["--spec-dir", str(SPEC_DIR),
                 "--data-dir", str(data_dir),
                 "--pkb-root", str(pkb_root), *argv])


def test_drift_exits_zero_when_in_sync(lab):
    assert _cli(lab, "drift") == EXIT_OK


def test_drift_exits_non_zero_on_induced_vector_drift(lab):
    """Drop a declared column out of a Lance table."""
    _, pkb_root = lab
    _pkb_table(pkb_root).drop_columns(["source_kind"])
    assert _cli(lab, "drift") == EXIT_PROBLEM


def test_drift_exits_non_zero_on_induced_relational_drift(lab):
    data_dir, _ = lab
    conn = sqlite3.connect(data_dir / "arail.db")
    conn.execute("DROP TABLE content_refs")
    conn.commit()
    conn.close()
    assert _cli(lab, "drift") == EXIT_PROBLEM


def test_drift_exits_non_zero_when_generated_code_is_stale(lab, tmp_path):
    """A spec edit without a regenerate must be caught."""
    edited = tmp_path / "spec"
    shutil.copytree(SPEC_DIR, edited)
    models = edited / "models" / "models.hcl"
    models.write_text(
        models.read_text().replace("parameter_count  = 1235814432",
                                   "parameter_count  = 1235814433"))
    data_dir, pkb_root = lab
    assert main(["--spec-dir", str(edited), "--data-dir", str(data_dir),
                 "--pkb-root", str(pkb_root), "drift"]) == EXIT_PROBLEM


def test_doctor_exits_non_zero_when_it_finds_errors(lab):
    data_dir, _ = lab
    conn = connect(data_dir)
    world = _world(conn)
    _content_ref(conn, world, dim=128)
    conn.close()
    assert _cli(lab, "doctor") == EXIT_PROBLEM


@pytest.mark.parametrize("order", ["before", "after"])
def test_root_flags_are_honored_on_both_sides_of_the_subcommand(lab, order):
    """Guards an argparse trap that silently retargeted the wrong lab.

    --data-dir was defined on both the main parser and each subparser, so
    argparse applied the subparser's default after parsing and discarded a
    value given before the subcommand. The command then fell back to the
    ambient ARAIL_DATA_DIR — a different, real database — while reporting
    success. Both orders must reach the same lab.
    """
    data_dir, pkb_root = lab
    argv = ["--spec-dir", str(SPEC_DIR), "--data-dir", str(data_dir),
            "--pkb-root", str(pkb_root)]
    if order == "before":
        assert main([*argv, "drift"]) == EXIT_OK
    else:
        assert main(["drift", *argv]) == EXIT_OK
