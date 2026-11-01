"""Compile-time gates on the ARAIL 2.0 spec tree.

The point of putting the model ceiling and the resolver policy in the spec
rather than in runtime checks is that a violating spec must not build. These
tests hold that line.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from arail.dbspec.codegen import generate_all, render_models_registry
from arail.dbspec.hcl import HCLError, parse
from arail.dbspec.spec import SpecError, load_spec

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_DIR = REPO_ROOT / "spec"


@pytest.fixture()
def spec_copy(tmp_path: Path) -> Path:
    """An editable copy of the real spec tree."""
    dest = tmp_path / "spec"
    shutil.copytree(SPEC_DIR, dest)
    return dest


def _patch(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"anchor not found in {path}: {old!r}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _patch_attr(path: Path, key: str, value: str) -> None:
    """Set an attribute regardless of the spec's `=` alignment padding."""
    text = path.read_text(encoding="utf-8")
    patched, count = re.subn(rf"^(\s*{re.escape(key)}\s*=\s*).*$",
                             lambda m: m.group(1) + value,
                             text, count=1, flags=re.MULTILINE)
    assert count == 1, f"attribute {key!r} not found in {path}"
    path.write_text(patched, encoding="utf-8")


# ---------------------------------------------------------------------------
# The real spec must always be valid.
# ---------------------------------------------------------------------------

def test_shipped_spec_loads():
    spec = load_spec(SPEC_DIR)
    assert spec.embedding_model.name == "nomic-embed-text"
    assert spec.embedding_dim == 768
    assert {t.name for t in spec.vector_tables} == {
        "pkb_pages", "wiki_nodes", "agent_workflows", "experiments"}


def test_every_vector_table_matches_the_global_embedding_dim():
    """Schema versioning is global; per-table dims would be corruption."""
    spec = load_spec(SPEC_DIR)
    assert {t.vector.dim for t in spec.vector_tables} == {spec.embedding_dim}


def test_every_vector_table_carries_world_id():
    """World-scoped retrieval is impossible without this column. In 1.x its
    absence forced scoping by `rm -rf` of other worlds' files."""
    spec = load_spec(SPEC_DIR)
    for table in spec.vector_tables:
        assert "world_id" in {c.name for c in table.columns}, table.name


# ---------------------------------------------------------------------------
# Model ceiling — rejected at COMPILE time. Required test.
# ---------------------------------------------------------------------------

def test_answering_model_at_ceiling_fails_to_build(spec_copy: Path):
    """'at or above 8B' — exactly 8B is a violation, not a boundary pass."""
    _patch(spec_copy / "models" / "models.hcl",
           "parameter_count  = 7615616512",
           "parameter_count  = 8000000000")
    with pytest.raises(SpecError) as excinfo:
        load_spec(spec_copy)
    message = str(excinfo.value)
    assert "ceiling" in message
    assert "ai-engineer" in message
    assert "8,000,000,000" in message
    assert "no override flag" in message.lower()


def test_answering_model_above_ceiling_fails_to_build(spec_copy: Path):
    _patch(spec_copy / "models" / "models.hcl",
           "parameter_count  = 7615616512",
           "parameter_count  = 70000000000")
    with pytest.raises(SpecError, match="ceiling"):
        load_spec(spec_copy)


def test_model_just_below_ceiling_builds(spec_copy: Path):
    _patch(spec_copy / "models" / "models.hcl",
           "parameter_count  = 7615616512",
           "parameter_count  = 7999999999")
    spec = load_spec(spec_copy)
    assert spec.model("ai-engineer").parameter_count == 7999999999


def test_unknown_parameter_count_makes_a_model_ineligible_by_name(
        spec_copy: Path):
    """Filenames are never trusted. An undeclared count is not a pass."""
    _patch(spec_copy / "models" / "models.hcl",
           "parameter_count  = 7615616512",
           "parameter_count  = -1")
    with pytest.raises(SpecError) as excinfo:
        load_spec(spec_copy)
    assert "ai-engineer" in str(excinfo.value)
    assert "undeclared" in str(excinfo.value)


def test_ceiling_does_not_apply_to_the_embedding_role(spec_copy: Path):
    """The ceiling governs the answering model. An embedding model is not an
    answering model and must not be swept up by the same rule."""
    _patch(spec_copy / "models" / "models.hcl",
           "parameter_count  = 136731648",
           "parameter_count  = 20000000000")
    spec = load_spec(spec_copy)
    assert spec.embedding_model.parameter_count == 20000000000


def test_generated_registry_reasserts_the_ceiling():
    """Guards against a hand-edited generated file."""
    spec = load_spec(SPEC_DIR)
    source = render_models_registry(spec)
    assert "ANSWERING_CEILING" in source
    assert "violated_by" in source
    assert "DO NOT EDIT" in source


# ---------------------------------------------------------------------------
# Resolver policy — the spec cannot ask for a fallback.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flag", [
    "allow_positional_fallback",
    "allow_first_available",
    "allow_most_recent",
    "allow_alphabetical",
])
def test_resolver_fallbacks_cannot_be_enabled(spec_copy: Path, flag: str):
    _patch_attr(spec_copy / "worlds" / "worlds.hcl", flag, "true")
    with pytest.raises(SpecError) as excinfo:
        load_spec(spec_copy)
    assert flag in str(excinfo.value)


def test_status_drift_between_worlds_and_schema_is_rejected(spec_copy: Path):
    """spec/worlds statuses and the schema CHECK must agree, or the resolver
    and the database disagree about what a valid world is."""
    _patch(spec_copy / "worlds" / "worlds.hcl",
           'status "archived" {', 'status "retired" {')
    with pytest.raises(SpecError, match="status drift"):
        load_spec(spec_copy)


def test_entity_kind_not_allowed_by_schema_is_rejected(spec_copy: Path):
    _patch(spec_copy / "worlds" / "worlds.hcl",
           '"term", "category", "document", "note"]',
           '"term", "category", "document", "note", "sprocket"]')
    with pytest.raises(SpecError, match="sprocket"):
        load_spec(spec_copy)


# ---------------------------------------------------------------------------
# Vector spec validation
# ---------------------------------------------------------------------------

def test_vector_dim_disagreeing_with_the_default_is_rejected(spec_copy: Path):
    _patch(spec_copy / "vectors" / "vectors.hcl",
           'vector "vector" {\n    dim    = 768',
           'vector "vector" {\n    dim    = 128')
    with pytest.raises(SpecError, match="global"):
        load_spec(spec_copy)


def test_unknown_attribute_in_a_spec_block_is_rejected(spec_copy: Path):
    """A typo must fail the build, not be silently ignored."""
    _patch(spec_copy / "vectors" / "vectors.hcl",
           "max_fragments     = 100",
           "max_fragmnets     = 100")
    with pytest.raises(SpecError, match="unknown attribute"):
        load_spec(spec_copy)


def test_index_on_undeclared_column_is_rejected(spec_copy: Path):
    _patch(spec_copy / "vectors" / "vectors.hcl",
           'index "pkb_pages_world_idx" {\n    column = "world_id"',
           'index "pkb_pages_world_idx" {\n    column = "nonexistent"')
    with pytest.raises(SpecError, match="nonexistent"):
        load_spec(spec_copy)


# ---------------------------------------------------------------------------
# Spec hashing and codegen determinism
# ---------------------------------------------------------------------------

def test_spec_hash_changes_when_the_spec_changes(spec_copy: Path):
    before = load_spec(spec_copy).sha256
    _patch(spec_copy / "vectors" / "vectors.hcl",
           "version_retention = 20", "version_retention = 21")
    assert load_spec(spec_copy).sha256 != before


def test_codegen_is_deterministic(tmp_path: Path):
    spec = load_spec(SPEC_DIR)
    first = tmp_path / "a"
    second = tmp_path / "b"
    generate_all(spec, out_dir=first)
    generate_all(spec, out_dir=second)
    for name in ("models_registry.py", "world_resolver.py"):
        assert (first / name).read_text() == (second / name).read_text()


def test_committed_generated_files_match_the_spec(tmp_path: Path):
    """`db drift` in CI depends on this: generated code is committed, so a
    spec edit without a regenerate must be caught."""
    spec = load_spec(SPEC_DIR)
    generate_all(spec, out_dir=tmp_path)
    committed = REPO_ROOT / "src" / "arail" / "dbspec" / "generated"
    for name in ("models_registry.py", "world_resolver.py"):
        assert (tmp_path / name).read_text() == (committed / name).read_text(), (
            f"{name} is stale — run './arailctl db apply' to regenerate")


# ---------------------------------------------------------------------------
# HCL subset parser — strictness is what makes hand-rolling it safe.
# ---------------------------------------------------------------------------

def test_parser_rejects_interpolation():
    with pytest.raises(HCLError, match="interpolation"):
        parse('a = "${var.x}"', source="t.hcl")


def test_parser_rejects_heredocs():
    with pytest.raises(HCLError, match="heredoc"):
        parse("a = <<EOT\nx\nEOT\n", source="t.hcl")


def test_parser_rejects_bare_identifier_values():
    with pytest.raises(HCLError, match="bare identifier"):
        parse("a = someref", source="t.hcl")


def test_parser_rejects_duplicate_attributes():
    with pytest.raises(HCLError, match="duplicate attribute"):
        parse('a = 1\na = 2', source="t.hcl")


def test_parser_rejects_duplicate_blocks():
    with pytest.raises(HCLError, match="duplicate block"):
        parse('t "x" { a = 1 }\nt "x" { a = 2 }', source="t.hcl")


def test_parser_errors_name_source_and_line():
    with pytest.raises(HCLError) as excinfo:
        parse('a = 1\n\nb = @', source="myspec.hcl")
    assert "myspec.hcl:3" in str(excinfo.value)


def test_parser_handles_the_inline_comma_form():
    doc = parse('column "x" { type = "string", nullable = false }',
                source="t.hcl")
    assert doc == {"column": {"x": {"type": "string", "nullable": False}}}
