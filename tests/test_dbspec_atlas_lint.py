"""The migration lint gate.

`atlas migrate lint` is Atlas Pro-only since v0.38 and exits non-zero WITHOUT
linting when you are not logged in. Treating that as a pass would claim a gate
ran when it did not; treating it as a failure would block every apply on a
machine that has never logged in. So we detect it and run a narrower local
gate, labelled as such. These tests hold both halves of that contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arail.dbspec.atlas import _local_lint, _rebuilt_tables

# SQLite cannot alter a constraint in place, so Atlas rebuilds the table:
# create new_X, copy every row, drop X, rename. This is what a routine index
# change looks like, and it must not be reported as data loss.
REBUILD_SQL = """
CREATE TABLE `new_content_refs` (`id` text NOT NULL, `world_id` text NOT NULL);
INSERT INTO `new_content_refs` (`id`, `world_id`) SELECT `id`, `world_id` FROM `content_refs`;
DROP TABLE `content_refs`;
ALTER TABLE `new_content_refs` RENAME TO `content_refs`;
CREATE UNIQUE INDEX `idx_content_refs_row` ON `content_refs` (`world_id`);
"""


def _lint(tmp_path: Path, sql: str, name: str = "m.sql"):
    path = tmp_path / name
    path.write_text(sql, encoding="utf-8")
    return _local_lint(path)


def test_table_rebuild_is_not_reported_as_destructive(tmp_path):
    """Otherwise every index change is unshippable, which teaches operators
    to bypass the gate — the opposite of what a gate is for."""
    result = _lint(tmp_path, REBUILD_SQL)
    assert result.ok, result.render()
    assert "content_refs" in result.detail


def test_rebuild_detection_identifies_the_table(tmp_path):
    assert _rebuilt_tables(REBUILD_SQL) == {"content_refs"}


def test_bare_drop_table_blocks(tmp_path):
    result = _lint(tmp_path, "DROP TABLE `worlds`;")
    assert not result.ok
    assert "worlds" in result.findings[0]
    assert "destroys data" in result.findings[0]


def test_drop_without_the_copy_step_blocks(tmp_path):
    """A rename alone does not make a drop safe — the rows must be copied."""
    sql = ("CREATE TABLE `new_worlds` (`id` text);\n"
           "DROP TABLE `worlds`;\n"
           "ALTER TABLE `new_worlds` RENAME TO `worlds`;\n")
    result = _lint(tmp_path, sql)
    assert not result.ok, result.render()


@pytest.mark.parametrize("sql,label", [
    ("ALTER TABLE `worlds` DROP COLUMN `slug`;", "DROP COLUMN"),
    ("DELETE FROM `worlds`;", "DELETE FROM"),
    ("TRUNCATE `worlds`;", "TRUNCATE"),
])
def test_other_destructive_statements_block(tmp_path, sql, label):
    result = _lint(tmp_path, sql)
    assert not result.ok
    assert label in " ".join(result.findings)


def test_local_gate_labels_itself_as_the_local_gate(tmp_path):
    """A gate that did not run must never look like a gate that passed."""
    rendered = _lint(tmp_path, "CREATE TABLE `x` (`id` text);").render()
    assert "LOCAL" in rendered
    assert "Atlas Pro" in rendered
    assert "did not run" in rendered


def test_shipped_baseline_migration_passes(tmp_path):
    """The committed baseline must not be blocked by our own gate."""
    migrations = Path(__file__).resolve().parents[1] / "spec" / "schema" / "migrations"
    sql_files = sorted(migrations.glob("*.sql"))
    assert sql_files, f"no baseline migration in {migrations}"
    for path in sql_files:
        assert _local_lint(path).ok, _local_lint(path).render()
