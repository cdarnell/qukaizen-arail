"""Tests for arail.dbspec.repo — the repository layer over the ARAIL 2.0
SQLite store.

The schema is applied fresh (via Atlas) into a temp directory for every
test, so each test gets a clean, schema-correct database rather than a
hand-rolled approximation of one.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from arail.dbspec import db, repo

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_HCL = REPO_ROOT / "spec" / "schema" / "schema.hcl"
ATLAS = "/opt/homebrew/bin/atlas"


def _atlas_available() -> bool:
    return shutil.which(ATLAS) is not None or Path(ATLAS).exists()


@pytest.fixture()
def conn(tmp_path) -> sqlite3.Connection:
    """A connection to a freshly schema-applied database in an isolated
    temp data_dir."""
    if not _atlas_available():
        pytest.skip(f"atlas binary not found at {ATLAS}")

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = db.database_path(data_dir)

    result = subprocess.run(
        [
            ATLAS, "schema", "apply",
            "--url", f"sqlite://{db_path}",
            "--to", f"file://{SCHEMA_HCL}",
            "--dev-url", "sqlite://dev?mode=memory",
            "--auto-approve",
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"atlas schema apply failed:\nstdout={result.stdout}\n"
        f"stderr={result.stderr}"
    )

    connection = db.connect(data_dir)
    yield connection
    connection.close()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _mk_world(conn, *, slug="w1", user_id="u1", display_name="World One"):
    return repo.create_world(
        conn, user_id=user_id, slug=slug, display_name=display_name)


def _mk_entity(conn, world_id, kind, name, **kw):
    return repo.upsert_entity(conn, world_id=world_id, kind=kind, name=name,
                              **kw)


# ---------------------------------------------------------------------------
# worlds
# ---------------------------------------------------------------------------

def test_create_world_assigns_distinct_ids(conn):
    w1 = _mk_world(conn, slug="alpha")
    w2 = _mk_world(conn, slug="beta")
    assert w1.id != w2.id
    assert w1.slug == "alpha"
    assert w2.slug == "beta"


def test_create_world_duplicate_slug_raises(conn):
    _mk_world(conn, user_id="u1", slug="dup")
    with pytest.raises(db.DatabaseError):
        _mk_world(conn, user_id="u1", slug="dup")


def test_create_world_same_slug_different_user_ok(conn):
    w1 = _mk_world(conn, user_id="u1", slug="shared")
    w2 = _mk_world(conn, user_id="u2", slug="shared")
    assert w1.id != w2.id


def test_update_world_status(conn):
    w = _mk_world(conn)
    updated = repo.update_world_status(conn, world_id=w.id, status="archived")
    assert updated.status == "archived"
    assert updated.updated_at >= w.updated_at


def test_update_world_status_missing_world_raises(conn):
    with pytest.raises(db.DatabaseError):
        repo.update_world_status(conn, world_id="nope", status="archived")


# ---------------------------------------------------------------------------
# entities
# ---------------------------------------------------------------------------

def test_upsert_entity_idempotent(conn):
    w = _mk_world(conn)
    e1 = _mk_entity(conn, w.id, "term", "gradient-descent", title="GD",
                     attrs={"a": 1})
    e2 = _mk_entity(conn, w.id, "term", "gradient-descent", title="GD2",
                     attrs={"a": 2})
    assert e1.id == e2.id
    assert e2.title == "GD2"
    assert e2.attrs == {"a": 2}

    rows = conn.execute(
        "SELECT COUNT(*) AS c FROM entities WHERE world_id = ?", (w.id,)
    ).fetchone()
    assert rows["c"] == 1


def test_upsert_entity_attrs_roundtrip(conn):
    w = _mk_world(conn)
    e = _mk_entity(conn, w.id, "term", "x", attrs={"nested": [1, 2, "s"]})
    fetched = repo.get_entity(conn, world_id=w.id, kind="term", name="x")
    assert fetched.attrs == {"nested": [1, 2, "s"]}


def test_get_entity_wrong_world_not_returned(conn):
    w1 = _mk_world(conn, slug="w1")
    w2 = _mk_world(conn, slug="w2")
    _mk_entity(conn, w1.id, "term", "shared-name")
    assert repo.get_entity(conn, world_id=w2.id, kind="term",
                           name="shared-name") is None


def test_list_entities_filters_by_kind(conn):
    w = _mk_world(conn)
    _mk_entity(conn, w.id, "term", "t1")
    _mk_entity(conn, w.id, "goal", "g1")
    terms = repo.list_entities(conn, world_id=w.id, kind="term")
    assert [e.name for e in terms] == ["t1"]
    everything = repo.list_entities(conn, world_id=w.id)
    assert len(everything) == 2


# ---------------------------------------------------------------------------
# world deletion cascade
# ---------------------------------------------------------------------------

def test_delete_world_cascades(conn):
    w = _mk_world(conn)
    a = _mk_entity(conn, w.id, "term", "a")
    b = _mk_entity(conn, w.id, "term", "b")
    repo.add_relation(conn, world_id=w.id, src_entity_id=a.id,
                      dst_entity_id=b.id, kind="relates_to")
    repo.set_state(conn, world_id=w.id, key="k", value={"v": 1})
    repo.record_content(conn, world_id=w.id, lance_table="pkb_docs",
                        lance_uri="lance://pkb_docs", row_key="doc-1",
                        embedding_model="m", embedding_dim=8)

    repo.delete_world(conn, world_id=w.id)

    counts = {}
    for table in ("entities", "relations", "world_state", "content_refs"):
        counts[table] = conn.execute(
            f"SELECT COUNT(*) AS c FROM {table} WHERE world_id = ?",
            (w.id,)).fetchone()["c"]
    assert counts == {"entities": 0, "relations": 0, "world_state": 0,
                      "content_refs": 0}


def test_delete_world_missing_raises(conn):
    with pytest.raises(db.DatabaseError):
        repo.delete_world(conn, world_id="nope")


# ---------------------------------------------------------------------------
# relations
# ---------------------------------------------------------------------------

def test_add_relation_idempotent(conn):
    w = _mk_world(conn)
    a = _mk_entity(conn, w.id, "term", "a")
    b = _mk_entity(conn, w.id, "term", "b")
    r1 = repo.add_relation(conn, world_id=w.id, src_entity_id=a.id,
                           dst_entity_id=b.id, kind="relates_to", weight=1.0)
    r2 = repo.add_relation(conn, world_id=w.id, src_entity_id=a.id,
                           dst_entity_id=b.id, kind="relates_to", weight=2.0)
    assert r1 == r2
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM relations WHERE src_entity_id = ? AND "
        "dst_entity_id = ? AND kind = ?", (a.id, b.id, "relates_to")
    ).fetchone()["c"]
    assert count == 1
    weight = conn.execute(
        "SELECT weight FROM relations WHERE id = ?", (r1,)).fetchone()["weight"]
    assert weight == 2.0


def test_add_relation_self_edge_rejected(conn):
    w = _mk_world(conn)
    a = _mk_entity(conn, w.id, "term", "a")
    with pytest.raises(db.DatabaseError):
        repo.add_relation(conn, world_id=w.id, src_entity_id=a.id,
                          dst_entity_id=a.id, kind="relates_to")


def test_neighbors_out_in_both(conn):
    w = _mk_world(conn)
    a = _mk_entity(conn, w.id, "term", "a")
    b = _mk_entity(conn, w.id, "term", "b")
    c = _mk_entity(conn, w.id, "term", "c")
    repo.add_relation(conn, world_id=w.id, src_entity_id=a.id,
                      dst_entity_id=b.id, kind="relates_to")
    repo.add_relation(conn, world_id=w.id, src_entity_id=c.id,
                      dst_entity_id=a.id, kind="relates_to")

    out = repo.neighbors(conn, entity_id=a.id, direction="out")
    assert [e.name for e in out] == ["b"]

    inn = repo.neighbors(conn, entity_id=a.id, direction="in")
    assert [e.name for e in inn] == ["c"]

    both = repo.neighbors(conn, entity_id=a.id, direction="both")
    assert sorted(e.name for e in both) == ["b", "c"]


def test_neighbors_invalid_direction_raises(conn):
    w = _mk_world(conn)
    a = _mk_entity(conn, w.id, "term", "a")
    with pytest.raises(db.DatabaseError):
        repo.neighbors(conn, entity_id=a.id, direction="sideways")


# ---------------------------------------------------------------------------
# traverse
# ---------------------------------------------------------------------------

def test_traverse_three_level_chain_depths(conn):
    w = _mk_world(conn)
    a = _mk_entity(conn, w.id, "term", "a")
    b = _mk_entity(conn, w.id, "term", "b")
    c = _mk_entity(conn, w.id, "term", "c")
    repo.add_relation(conn, world_id=w.id, src_entity_id=a.id,
                      dst_entity_id=b.id, kind="relates_to")
    repo.add_relation(conn, world_id=w.id, src_entity_id=b.id,
                      dst_entity_id=c.id, kind="relates_to")

    result = repo.traverse(conn, start_entity_id=a.id, max_depth=3)
    got = [(e.name, depth) for e, depth in result]
    assert got == [("b", 1), ("c", 2)]


def test_traverse_terminates_on_cycle(conn):
    w = _mk_world(conn)
    a = _mk_entity(conn, w.id, "term", "a")
    b = _mk_entity(conn, w.id, "term", "b")
    repo.add_relation(conn, world_id=w.id, src_entity_id=a.id,
                      dst_entity_id=b.id, kind="relates_to")
    repo.add_relation(conn, world_id=w.id, src_entity_id=b.id,
                      dst_entity_id=a.id, kind="relates_to")

    # a -> b -> a -> b -> ... ; must terminate and not revisit nodes.
    result = repo.traverse(conn, start_entity_id=a.id, max_depth=10)
    got = [(e.name, depth) for e, depth in result]
    assert got == [("b", 1)]


def test_traverse_respects_max_depth(conn):
    w = _mk_world(conn)
    a = _mk_entity(conn, w.id, "term", "a")
    b = _mk_entity(conn, w.id, "term", "b")
    c = _mk_entity(conn, w.id, "term", "c")
    d = _mk_entity(conn, w.id, "term", "d")
    repo.add_relation(conn, world_id=w.id, src_entity_id=a.id,
                      dst_entity_id=b.id, kind="relates_to")
    repo.add_relation(conn, world_id=w.id, src_entity_id=b.id,
                      dst_entity_id=c.id, kind="relates_to")
    repo.add_relation(conn, world_id=w.id, src_entity_id=c.id,
                      dst_entity_id=d.id, kind="relates_to")

    result = repo.traverse(conn, start_entity_id=a.id, max_depth=2)
    got = [(e.name, depth) for e, depth in result]
    assert got == [("b", 1), ("c", 2)]


def test_traverse_filters_by_kind(conn):
    w = _mk_world(conn)
    a = _mk_entity(conn, w.id, "term", "a")
    b = _mk_entity(conn, w.id, "term", "b")
    c = _mk_entity(conn, w.id, "term", "c")
    repo.add_relation(conn, world_id=w.id, src_entity_id=a.id,
                      dst_entity_id=b.id, kind="relates_to")
    repo.add_relation(conn, world_id=w.id, src_entity_id=a.id,
                      dst_entity_id=c.id, kind="cites")

    result = repo.traverse(conn, start_entity_id=a.id, max_depth=3,
                           kind="relates_to")
    got = [(e.name, depth) for e, depth in result]
    assert got == [("b", 1)]


# ---------------------------------------------------------------------------
# world_state
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    {"a": 1, "b": [1, 2, 3]},
    [1, 2, 3],
    42,
    "a string",
])
def test_state_roundtrip_types(conn, value):
    w = _mk_world(conn)
    repo.set_state(conn, world_id=w.id, key="k", value=value)
    assert repo.get_state(conn, world_id=w.id, key="k") == value


def test_state_tick_autoincrements(conn):
    w = _mk_world(conn)
    repo.set_state(conn, world_id=w.id, key="k", value=1)
    repo.set_state(conn, world_id=w.id, key="k", value=2)
    repo.set_state(conn, world_id=w.id, key="k", value=3)
    tick = conn.execute(
        "SELECT tick FROM world_state WHERE world_id = ? AND key = ?",
        (w.id, "k")).fetchone()["tick"]
    assert tick == 2


def test_state_explicit_tick_not_incremented(conn):
    w = _mk_world(conn)
    repo.set_state(conn, world_id=w.id, key="k", value=1, tick=7)
    tick = conn.execute(
        "SELECT tick FROM world_state WHERE world_id = ? AND key = ?",
        (w.id, "k")).fetchone()["tick"]
    assert tick == 7


def test_get_state_missing_key_returns_default(conn):
    w = _mk_world(conn)
    assert repo.get_state(conn, world_id=w.id, key="missing",
                          default="fallback") == "fallback"


def test_all_state(conn):
    w = _mk_world(conn)
    repo.set_state(conn, world_id=w.id, key="k1", value=1)
    repo.set_state(conn, world_id=w.id, key="k2", value="two")
    assert repo.all_state(conn, world_id=w.id) == {"k1": 1, "k2": "two"}


# ---------------------------------------------------------------------------
# content_refs
# ---------------------------------------------------------------------------

def test_record_content_idempotent(conn):
    w = _mk_world(conn)
    c1 = repo.record_content(conn, world_id=w.id, lance_table="pkb_docs",
                             lance_uri="lance://pkb_docs", row_key="doc-1",
                             embedding_model="m1", embedding_dim=8)
    c2 = repo.record_content(conn, world_id=w.id, lance_table="pkb_docs",
                             lance_uri="lance://pkb_docs", row_key="doc-1",
                             embedding_model="m2", embedding_dim=16)
    assert c1.id == c2.id
    assert c2.embedding_model == "m2"
    assert c2.embedding_dim == 16
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM content_refs WHERE lance_table = ? AND "
        "row_key = ?", ("pkb_docs", "doc-1")).fetchone()["c"]
    assert count == 1


def test_row_keys_for_world_isolation(conn):
    """The world-scoping test: two worlds' content lands in the same
    lance_table, and row_keys_for_world must return only the caller's
    world's keys."""
    w1 = _mk_world(conn, slug="w1")
    w2 = _mk_world(conn, slug="w2")

    repo.record_content(conn, world_id=w1.id, lance_table="pkb_docs",
                        lance_uri="lance://pkb_docs", row_key="w1-doc-a",
                        embedding_model="m", embedding_dim=8)
    repo.record_content(conn, world_id=w1.id, lance_table="pkb_docs",
                        lance_uri="lance://pkb_docs", row_key="w1-doc-b",
                        embedding_model="m", embedding_dim=8)
    repo.record_content(conn, world_id=w2.id, lance_table="pkb_docs",
                        lance_uri="lance://pkb_docs", row_key="w2-doc-a",
                        embedding_model="m", embedding_dim=8)

    w1_keys = repo.row_keys_for_world(conn, world_id=w1.id,
                                      lance_table="pkb_docs")
    w2_keys = repo.row_keys_for_world(conn, world_id=w2.id,
                                      lance_table="pkb_docs")

    assert set(w1_keys) == {"w1-doc-a", "w1-doc-b"}
    assert set(w2_keys) == {"w2-doc-a"}
    assert set(w1_keys).isdisjoint(w2_keys)


def test_content_for_world_filters_by_table(conn):
    w = _mk_world(conn)
    repo.record_content(conn, world_id=w.id, lance_table="pkb_docs",
                        lance_uri="lance://pkb_docs", row_key="a",
                        embedding_model="m", embedding_dim=8)
    repo.record_content(conn, world_id=w.id, lance_table="other_table",
                        lance_uri="lance://other_table", row_key="b",
                        embedding_model="m", embedding_dim=8)
    only_pkb = repo.content_for_world(conn, world_id=w.id,
                                      lance_table="pkb_docs")
    assert [c.row_key for c in only_pkb] == ["a"]
    everything = repo.content_for_world(conn, world_id=w.id)
    assert len(everything) == 2


def test_drop_content(conn):
    w = _mk_world(conn)
    repo.record_content(conn, world_id=w.id, lance_table="pkb_docs",
                        lance_uri="lance://pkb_docs", row_key="a",
                        embedding_model="m", embedding_dim=8)
    assert repo.drop_content(conn, world_id=w.id, lance_table="pkb_docs", row_key="a") is True
    assert repo.drop_content(conn, world_id=w.id, lance_table="pkb_docs", row_key="a") is False
    assert repo.row_keys_for_world(conn, world_id=w.id,
                                   lance_table="pkb_docs") == ()


def test_content_refs_entity_on_delete_set_null(conn):
    w = _mk_world(conn)
    e = _mk_entity(conn, w.id, "document", "doc-1")
    ref = repo.record_content(conn, world_id=w.id, lance_table="pkb_docs",
                              lance_uri="lance://pkb_docs", row_key="a",
                              embedding_model="m", embedding_dim=8,
                              entity_id=e.id)
    assert ref.entity_id == e.id

    with db.transaction(conn):
        conn.execute("DELETE FROM entities WHERE id = ?", (e.id,))

    row = conn.execute(
        "SELECT entity_id FROM content_refs WHERE id = ?", (ref.id,)
    ).fetchone()
    assert row["entity_id"] is None


def test_same_row_key_in_two_worlds_does_not_collide(conn):
    """Row keys are unique per world, not globally.

    The live 1.x lab has 36 paths present in more than one world
    ('agents/README.md' is in all four). When identity was
    (lance_table, row_key), migrating the second world silently reassigned
    the first world's content_ref instead of creating its own.
    """
    a = _mk_world(conn, slug="ai")
    b = _mk_world(conn, slug="video-games")

    ref_a = repo.record_content(conn, world_id=a.id, lance_table="pkb_pages",
                                lance_uri="lance://pkb_pages",
                                row_key="agents/README.md",
                                embedding_model="nomic-embed-text",
                                embedding_dim=768)
    ref_b = repo.record_content(conn, world_id=b.id, lance_table="pkb_pages",
                                lance_uri="lance://pkb_pages",
                                row_key="agents/README.md",
                                embedding_model="nomic-embed-text",
                                embedding_dim=768)

    assert ref_a.id != ref_b.id
    assert ref_a.world_id == a.id
    assert ref_b.world_id == b.id
    assert repo.row_keys_for_world(conn, world_id=a.id,
                                   lance_table="pkb_pages") == ("agents/README.md",)
    assert repo.row_keys_for_world(conn, world_id=b.id,
                                   lance_table="pkb_pages") == ("agents/README.md",)

    # Dropping one world's reference must leave the other's intact.
    assert repo.drop_content(conn, world_id=a.id, lance_table="pkb_pages",
                             row_key="agents/README.md") is True
    assert repo.row_keys_for_world(conn, world_id=a.id,
                                   lance_table="pkb_pages") == ()
    assert repo.row_keys_for_world(conn, world_id=b.id,
                                   lance_table="pkb_pages") == ("agents/README.md",)
