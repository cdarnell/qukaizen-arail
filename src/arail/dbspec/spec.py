"""Load and validate the ARAIL spec tree.

This module is where declared intent becomes checked intent. Everything that
can be proven wrong at build time is proven wrong here, by name, with the
spec file and the offending value in the message — a spec that violates the
model ceiling or drifts on embedding dimension does not compile.

Load order matters only for error quality: models are validated first so that
"you declared a 9B answering model" surfaces before "your vector dim does not
match the embedding model", which would otherwise be a confusing cascade.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from arail.dbspec.hcl import HCLError, parse

__all__ = [
    "Spec", "SpecError", "ModelSpec", "CeilingSpec", "VectorTableSpec",
    "ColumnSpec", "VectorColumnSpec", "IndexSpec", "ResolverSpec",
    "WorldTypeSpec", "StatusSpec", "load_spec", "DEFAULT_SPEC_DIR",
]

DEFAULT_SPEC_DIR = "spec"

_UNKNOWN_PARAMETER_COUNT = -1

_VALID_ROLES = ("answering", "embedding", "utility")
_VALID_PARAM_SOURCES = ("gguf_header", "hf_config", "vendor_card")
_VALID_METRICS = ("cosine", "l2", "dot")
_VALID_INDEX_TYPES = ("IVF_PQ", "IVF_FLAT", "HNSW_PQ", "HNSW_SQ", "BTREE",
                      "BITMAP", "LABEL_LIST", "FTS")
_VECTOR_INDEX_TYPES = ("IVF_PQ", "IVF_FLAT", "HNSW_PQ", "HNSW_SQ")
_VALID_COLUMN_TYPES = ("string", "double", "float", "int32", "int64", "bool",
                       "timestamp", "binary")
_VALID_ROOTS = ("pkb", "data")


class SpecError(ValueError):
    """The spec tree is invalid. The message names the file and the value."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CeilingSpec:
    name: str
    role: str
    max_parameters: int
    bound: str  # "exclusive" | "inclusive"
    rationale: str

    def violated_by(self, parameter_count: int) -> bool:
        if self.bound == "exclusive":
            return parameter_count >= self.max_parameters
        return parameter_count > self.max_parameters

    def describe(self) -> str:
        op = ">=" if self.bound == "exclusive" else ">"
        return f"{op} {self.max_parameters:,} parameters"


@dataclass(frozen=True)
class ModelSpec:
    name: str
    role: str
    backend: str
    parameter_count: int
    parameter_source: str
    base: Optional[str] = None
    ollama_tag: Optional[str] = None
    license: Optional[str] = None
    disclosure: Optional[str] = None
    tier: str = "minimalist"
    default: bool = False
    embedding_dim: Optional[int] = None
    query_prefix: str = ""
    document_prefix: str = ""

    @property
    def parameter_count_known(self) -> bool:
        return self.parameter_count != _UNKNOWN_PARAMETER_COUNT

    @property
    def eligible(self) -> bool:
        """A model with an unknown parameter count is ineligible for every
        role. Filenames are never trusted, so "probably 7B" is not a basis
        for serving answers."""
        return self.parameter_count_known


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    type: str
    nullable: bool = True
    primary: bool = False


@dataclass(frozen=True)
class VectorColumnSpec:
    name: str
    dim: int
    metric: str


@dataclass(frozen=True)
class IndexSpec:
    name: str
    column: str
    type: str
    metric: Optional[str] = None
    num_partitions: Optional[int] = None
    num_sub_vectors: Optional[int] = None

    @property
    def is_vector_index(self) -> bool:
        return self.type in _VECTOR_INDEX_TYPES


@dataclass(frozen=True)
class VectorTableSpec:
    name: str
    root: str          # "pkb" | "data" — which per-world root it lives under
    subpath: str       # path within that root to the lancedb directory
    description: str
    columns: Tuple[ColumnSpec, ...]
    vector: VectorColumnSpec
    indexes: Tuple[IndexSpec, ...]
    max_fragments: int
    version_retention: int
    index_min_rows: int

    @property
    def primary_key(self) -> Optional[str]:
        for col in self.columns:
            if col.primary:
                return col.name
        return None

    def relative_dir(self) -> str:
        return f"{self.subpath}/{self.name}.lance"


@dataclass(frozen=True)
class ResolverSpec:
    accept_id: bool
    accept_slug: bool
    allow_positional_fallback: bool
    allow_first_available: bool
    allow_most_recent: bool
    allow_alphabetical: bool
    on_miss: str
    report_alternatives: bool
    scope_alternatives_to_user: bool
    slug_scope: str


@dataclass(frozen=True)
class WorldTypeSpec:
    name: str
    description: str
    entity_kinds: Tuple[str, ...]
    relation_kinds: Tuple[str, ...]
    default: bool = False


@dataclass(frozen=True)
class StatusSpec:
    name: str
    resolvable: bool
    selectable: bool
    description: str


@dataclass(frozen=True)
class Spec:
    spec_dir: Path
    models: Tuple[ModelSpec, ...]
    ceilings: Tuple[CeilingSpec, ...]
    vector_tables: Tuple[VectorTableSpec, ...]
    resolver: ResolverSpec
    world_types: Tuple[WorldTypeSpec, ...]
    statuses: Tuple[StatusSpec, ...]
    schema_sql_path: Path
    sha256: str
    version: int = 1

    # -- lookups (the only resolution path) ----------------------------
    def model(self, name: str) -> ModelSpec:
        for m in self.models:
            if m.name == name:
                return m
        raise SpecError(
            f"no model named {name!r} in the spec; declared models: "
            f"{', '.join(sorted(m.name for m in self.models))}")

    def models_for_role(self, role: str) -> Tuple[ModelSpec, ...]:
        return tuple(m for m in self.models if m.role == role and m.eligible)

    def default_model(self, role: str) -> ModelSpec:
        candidates = [m for m in self.models_for_role(role) if m.default]
        if not candidates:
            raise SpecError(f"no default model declared for role {role!r}")
        if len(candidates) > 1:
            raise SpecError(
                f"role {role!r} has {len(candidates)} defaults: "
                f"{', '.join(m.name for m in candidates)}")
        return candidates[0]

    @property
    def embedding_model(self) -> ModelSpec:
        return self.default_model("embedding")

    @property
    def embedding_dim(self) -> int:
        dim = self.embedding_model.embedding_dim
        if dim is None:  # pragma: no cover - validated at load
            raise SpecError("embedding model declares no embedding_dim")
        return dim

    def vector_table(self, name: str) -> VectorTableSpec:
        for t in self.vector_tables:
            if t.name == name:
                return t
        raise SpecError(
            f"no vector table named {name!r}; declared: "
            f"{', '.join(sorted(t.name for t in self.vector_tables))}")

    def status(self, name: str) -> StatusSpec:
        for s in self.statuses:
            if s.name == name:
                return s
        raise SpecError(f"no status named {name!r}")


# ---------------------------------------------------------------------------
# Loading helpers
# ---------------------------------------------------------------------------

def _read(path: Path) -> Dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecError(f"cannot read spec file {path}: {exc}") from exc
    try:
        return parse(text, source=str(path))
    except HCLError as exc:
        raise SpecError(str(exc)) from exc


def _require(block: Dict[str, Any], key: str, *, where: str,
             kind: type | Tuple[type, ...] = object) -> Any:
    if key not in block:
        raise SpecError(f"{where}: missing required attribute {key!r}")
    value = block[key]
    if kind is not object and not isinstance(value, kind):
        raise SpecError(
            f"{where}: attribute {key!r} must be "
            f"{getattr(kind, '__name__', kind)}, got {type(value).__name__}")
    return value


def _reject_unknown(block: Dict[str, Any], allowed: Tuple[str, ...], *,
                    where: str) -> None:
    """A typo in a spec attribute must fail the build, not be ignored."""
    unknown = sorted(set(block) - set(allowed))
    if unknown:
        raise SpecError(
            f"{where}: unknown attribute(s) {', '.join(repr(u) for u in unknown)}; "
            f"supported: {', '.join(sorted(allowed))}")


def _one_of(value: Any, choices: Tuple[str, ...], *, where: str,
            key: str) -> str:
    if value not in choices:
        raise SpecError(
            f"{where}: {key} must be one of {', '.join(choices)}; "
            f"got {value!r}")
    return value


# ---------------------------------------------------------------------------
# Domain loaders
# ---------------------------------------------------------------------------

def _load_models(spec_dir: Path) -> Tuple[Tuple[ModelSpec, ...],
                                          Tuple[CeilingSpec, ...]]:
    path = spec_dir / "models" / "models.hcl"
    doc = _read(path)

    ceilings: List[CeilingSpec] = []
    for name, block in (doc.get("ceiling") or {}).items():
        where = f"{path}: ceiling {name!r}"
        _reject_unknown(block, ("role", "max_parameters", "bound",
                                "rationale"), where=where)
        ceilings.append(CeilingSpec(
            name=name,
            role=_one_of(_require(block, "role", where=where, kind=str),
                         _VALID_ROLES, where=where, key="role"),
            max_parameters=_require(block, "max_parameters", where=where,
                                    kind=int),
            bound=_one_of(block.get("bound", "exclusive"),
                          ("exclusive", "inclusive"), where=where,
                          key="bound"),
            rationale=str(block.get("rationale", "")),
        ))

    models: List[ModelSpec] = []
    for name, block in (doc.get("model") or {}).items():
        where = f"{path}: model {name!r}"
        _reject_unknown(block, (
            "role", "backend", "base", "ollama_tag", "parameter_count",
            "parameter_source", "license", "disclosure", "tier", "default",
            "embedding_dim", "query_prefix", "document_prefix"), where=where)
        role = _one_of(_require(block, "role", where=where, kind=str),
                       _VALID_ROLES, where=where, key="role")
        parameter_count = _require(block, "parameter_count", where=where,
                                   kind=int)
        if parameter_count < 0 and parameter_count != _UNKNOWN_PARAMETER_COUNT:
            raise SpecError(
                f"{where}: parameter_count must be positive, or exactly "
                f"{_UNKNOWN_PARAMETER_COUNT} to declare it unknown; got "
                f"{parameter_count}")
        source = _one_of(
            _require(block, "parameter_source", where=where, kind=str),
            _VALID_PARAM_SOURCES, where=where, key="parameter_source")
        embedding_dim = block.get("embedding_dim")
        if role == "embedding":
            if not isinstance(embedding_dim, int) or embedding_dim <= 0:
                raise SpecError(
                    f"{where}: an embedding model must declare a positive "
                    f"embedding_dim; got {embedding_dim!r}")
        models.append(ModelSpec(
            name=name, role=role,
            backend=_require(block, "backend", where=where, kind=str),
            parameter_count=parameter_count, parameter_source=source,
            base=block.get("base"), ollama_tag=block.get("ollama_tag"),
            license=block.get("license"), disclosure=block.get("disclosure"),
            tier=block.get("tier", "minimalist"),
            default=bool(block.get("default", False)),
            embedding_dim=embedding_dim,
            query_prefix=block.get("query_prefix", ""),
            document_prefix=block.get("document_prefix", ""),
        ))

    if not models:
        raise SpecError(f"{path}: no models declared")

    _enforce_ceilings(models, ceilings, path)
    return tuple(models), tuple(ceilings)


def _enforce_ceilings(models: List[ModelSpec], ceilings: List[CeilingSpec],
                      path: Path) -> None:
    """The whole point of putting the ceiling in the spec: a violating spec
    does not build. No override flag exists, deliberately."""
    violations: List[str] = []
    for ceiling in ceilings:
        for model in models:
            if model.role != ceiling.role:
                continue
            if not model.parameter_count_known:
                violations.append(
                    f"  - {model.name!r}: parameter count is undeclared, so it "
                    f"is ineligible to serve role {model.role!r}. Determine the "
                    f"count from the GGUF header or config metadata and declare "
                    f"it; filenames are not evidence.")
                continue
            if ceiling.violated_by(model.parameter_count):
                violations.append(
                    f"  - {model.name!r}: {model.parameter_count:,} parameters "
                    f"(source: {model.parameter_source}) violates ceiling "
                    f"{ceiling.name!r} for role {model.role!r}, which rejects "
                    f"{ceiling.describe()}.")
    if violations:
        raise SpecError(
            f"{path}: model ceiling violated — the spec does not build.\n"
            + "\n".join(violations)
            + "\n\nThere is no override flag. Declare a smaller model, or "
              "change its role."
        )


def _load_vectors(spec_dir: Path) -> Tuple[VectorTableSpec, ...]:
    path = spec_dir / "vectors" / "vectors.hcl"
    doc = _read(path)

    defaults = doc.get("defaults") or {}
    _reject_unknown(defaults, (
        "embedding_model", "embedding_dim", "distance", "max_fragments",
        "version_retention", "index_min_rows"), where=f"{path}: defaults")
    default_metric = _one_of(defaults.get("distance", "cosine"),
                             _VALID_METRICS, where=f"{path}: defaults",
                             key="distance")
    default_dim = defaults.get("embedding_dim")
    default_fragments = int(defaults.get("max_fragments", 100))
    default_retention = int(defaults.get("version_retention", 20))
    default_min_rows = int(defaults.get("index_min_rows", 256))

    tables: List[VectorTableSpec] = []
    for name, block in (doc.get("table") or {}).items():
        where = f"{path}: table {name!r}"
        _reject_unknown(block, (
            "root", "subpath", "description", "column", "vector", "index",
            "max_fragments", "version_retention", "index_min_rows"),
            where=where)

        columns: List[ColumnSpec] = []
        for col_name, col in (block.get("column") or {}).items():
            col_where = f"{where}: column {col_name!r}"
            _reject_unknown(col, ("type", "nullable", "primary"),
                            where=col_where)
            columns.append(ColumnSpec(
                name=col_name,
                type=_one_of(_require(col, "type", where=col_where, kind=str),
                             _VALID_COLUMN_TYPES, where=col_where, key="type"),
                nullable=bool(col.get("nullable", True)),
                primary=bool(col.get("primary", False)),
            ))
        if not columns:
            raise SpecError(f"{where}: declares no columns")
        primaries = [c.name for c in columns if c.primary]
        if len(primaries) != 1:
            raise SpecError(
                f"{where}: exactly one column must be marked primary; found "
                f"{len(primaries)} ({', '.join(primaries) or 'none'})")

        vector_blocks = block.get("vector") or {}
        if len(vector_blocks) != 1:
            raise SpecError(
                f"{where}: exactly one vector column is supported; found "
                f"{len(vector_blocks)}")
        vec_name, vec = next(iter(vector_blocks.items()))
        vec_where = f"{where}: vector {vec_name!r}"
        _reject_unknown(vec, ("dim", "metric"), where=vec_where)
        vector = VectorColumnSpec(
            name=vec_name,
            dim=_require(vec, "dim", where=vec_where, kind=int),
            metric=_one_of(vec.get("metric", default_metric), _VALID_METRICS,
                           where=vec_where, key="metric"),
        )
        if default_dim is not None and vector.dim != default_dim:
            raise SpecError(
                f"{vec_where}: dim {vector.dim} disagrees with the spec-wide "
                f"default embedding_dim {default_dim}. Schema versioning is "
                f"global: every world shares one dimension.")

        indexes: List[IndexSpec] = []
        for idx_name, idx in (block.get("index") or {}).items():
            idx_where = f"{where}: index {idx_name!r}"
            _reject_unknown(idx, ("column", "type", "metric",
                                  "num_partitions", "num_sub_vectors"),
                            where=idx_where)
            idx_type = _one_of(_require(idx, "type", where=idx_where, kind=str),
                               _VALID_INDEX_TYPES, where=idx_where, key="type")
            idx_col = _require(idx, "column", where=idx_where, kind=str)
            known = {c.name for c in columns} | {vector.name}
            if idx_col not in known:
                raise SpecError(
                    f"{idx_where}: indexes column {idx_col!r}, which the table "
                    f"does not declare; columns: {', '.join(sorted(known))}")
            spec_index = IndexSpec(
                name=idx_name, column=idx_col, type=idx_type,
                metric=idx.get("metric"),
                num_partitions=idx.get("num_partitions"),
                num_sub_vectors=idx.get("num_sub_vectors"),
            )
            if spec_index.is_vector_index and idx_col != vector.name:
                raise SpecError(
                    f"{idx_where}: {idx_type} is a vector index but targets "
                    f"non-vector column {idx_col!r}")
            if (spec_index.metric is not None
                    and spec_index.metric != vector.metric):
                raise SpecError(
                    f"{idx_where}: metric {spec_index.metric!r} disagrees with "
                    f"the vector column's metric {vector.metric!r}")
            indexes.append(spec_index)

        tables.append(VectorTableSpec(
            name=name,
            root=_one_of(_require(block, "root", where=where, kind=str),
                         _VALID_ROOTS, where=where, key="root"),
            subpath=_require(block, "subpath", where=where, kind=str),
            description=block.get("description", ""),
            columns=tuple(columns), vector=vector, indexes=tuple(indexes),
            max_fragments=int(block.get("max_fragments", default_fragments)),
            version_retention=int(
                block.get("version_retention", default_retention)),
            index_min_rows=int(block.get("index_min_rows", default_min_rows)),
        ))

    if not tables:
        raise SpecError(f"{path}: no vector tables declared")
    return tuple(tables)


def _load_worlds(spec_dir: Path) -> Tuple[ResolverSpec,
                                          Tuple[WorldTypeSpec, ...],
                                          Tuple[StatusSpec, ...]]:
    path = spec_dir / "worlds" / "worlds.hcl"
    doc = _read(path)

    block = doc.get("resolver")
    if not isinstance(block, dict):
        raise SpecError(f"{path}: missing required 'resolver' block")
    where = f"{path}: resolver"
    _reject_unknown(block, (
        "accept_id", "accept_slug", "allow_positional_fallback",
        "allow_first_available", "allow_most_recent", "allow_alphabetical",
        "on_miss", "report_alternatives", "scope_alternatives_to_user",
        "slug_scope"), where=where)
    resolver = ResolverSpec(
        accept_id=bool(block.get("accept_id", True)),
        accept_slug=bool(block.get("accept_slug", True)),
        allow_positional_fallback=bool(
            block.get("allow_positional_fallback", False)),
        allow_first_available=bool(block.get("allow_first_available", False)),
        allow_most_recent=bool(block.get("allow_most_recent", False)),
        allow_alphabetical=bool(block.get("allow_alphabetical", False)),
        on_miss=_one_of(block.get("on_miss", "raise"), ("raise",),
                        where=where, key="on_miss"),
        report_alternatives=bool(block.get("report_alternatives", True)),
        scope_alternatives_to_user=bool(
            block.get("scope_alternatives_to_user", True)),
        slug_scope=_one_of(block.get("slug_scope", "user"), ("user",),
                           where=where, key="slug_scope"),
    )
    # The generated resolver has no fallback branch to emit. Declaring one
    # here would silently generate nothing, so refuse the spec instead.
    for flag in ("allow_positional_fallback", "allow_first_available",
                 "allow_most_recent", "allow_alphabetical"):
        if getattr(resolver, flag):
            raise SpecError(
                f"{where}: {flag} = true is not supported. Positional world "
                f"resolution is the defect this spec exists to remove — see "
                f"the Phase 1 audit (scripts/start.sh:417, "
                f"world_mount.py:779). The resolver accepts an explicit id or "
                f"slug only.")
    if not (resolver.accept_id or resolver.accept_slug):
        raise SpecError(f"{where}: resolver accepts neither id nor slug")

    world_types: List[WorldTypeSpec] = []
    for name, wt in (doc.get("world_type") or {}).items():
        wt_where = f"{path}: world_type {name!r}"
        _reject_unknown(wt, ("description", "entity_kinds", "relation_kinds",
                             "default"), where=wt_where)
        world_types.append(WorldTypeSpec(
            name=name,
            description=wt.get("description", ""),
            entity_kinds=tuple(_require(wt, "entity_kinds", where=wt_where,
                                        kind=list)),
            relation_kinds=tuple(_require(wt, "relation_kinds",
                                          where=wt_where, kind=list)),
            default=bool(wt.get("default", False)),
        ))
    if not world_types:
        raise SpecError(f"{path}: no world_type declared")
    defaults = [w for w in world_types if w.default]
    if len(defaults) != 1:
        raise SpecError(
            f"{path}: exactly one world_type must be default; found "
            f"{len(defaults)}")

    statuses: List[StatusSpec] = []
    for name, st in (doc.get("status") or {}).items():
        st_where = f"{path}: status {name!r}"
        _reject_unknown(st, ("resolvable", "selectable", "description"),
                        where=st_where)
        statuses.append(StatusSpec(
            name=name,
            resolvable=bool(st.get("resolvable", True)),
            selectable=bool(st.get("selectable", True)),
            description=st.get("description", ""),
        ))
    if not statuses:
        raise SpecError(f"{path}: no status declared")

    return resolver, tuple(world_types), tuple(statuses)


# ---------------------------------------------------------------------------
# Cross-domain validation
# ---------------------------------------------------------------------------

_SCHEMA_STATUS_RE = re.compile(
    r"check\s+\"worlds_status_enum\"\s*\{[^}]*expr\s*=\s*\"([^\"]+)\"",
    re.DOTALL)
_SCHEMA_KIND_RE = re.compile(
    r"check\s+\"entities_kind_enum\"\s*\{[^}]*expr\s*=\s*\"([^\"]+)\"",
    re.DOTALL)


def _enum_members(expr: str) -> set[str]:
    return set(re.findall(r"'([^']+)'", expr))


def _cross_validate(spec_dir: Path, models: Tuple[ModelSpec, ...],
                    vector_tables: Tuple[VectorTableSpec, ...],
                    world_types: Tuple[WorldTypeSpec, ...],
                    statuses: Tuple[StatusSpec, ...]) -> None:
    embeddings = [m for m in models if m.role == "embedding" and m.default]
    if len(embeddings) != 1:
        raise SpecError(
            f"exactly one default embedding model is required (schema "
            f"versioning is global); found {len(embeddings)}")
    dim = embeddings[0].embedding_dim
    for table in vector_tables:
        if table.vector.dim != dim:
            raise SpecError(
                f"spec/vectors: table {table.name!r} declares dim "
                f"{table.vector.dim}, but the spec's embedding model "
                f"{embeddings[0].name!r} produces {dim}. Per-table dimension "
                f"variation is corruption by definition.")

    answering = [m for m in models if m.role == "answering" and m.eligible]
    if not answering:
        raise SpecError(
            "no eligible answering model: every declared answering model has "
            "an undeclared parameter count")

    # spec/worlds statuses must match the schema's CHECK constraint exactly.
    schema_path = spec_dir / "schema" / "schema.hcl"
    try:
        schema_text = schema_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecError(f"cannot read {schema_path}: {exc}") from exc

    match = _SCHEMA_STATUS_RE.search(schema_text)
    if not match:
        raise SpecError(
            f"{schema_path}: could not find the worlds_status_enum CHECK "
            f"constraint; spec/worlds statuses cannot be verified against it")
    schema_statuses = _enum_members(match.group(1))
    declared = {s.name for s in statuses}
    if schema_statuses != declared:
        raise SpecError(
            f"status drift: spec/worlds declares {sorted(declared)} but "
            f"{schema_path} CHECKs {sorted(schema_statuses)}. These must "
            f"agree — the resolver and the database would disagree about "
            f"what a valid world is.")

    kind_match = _SCHEMA_KIND_RE.search(schema_text)
    if not kind_match:
        raise SpecError(
            f"{schema_path}: could not find the entities_kind_enum CHECK "
            f"constraint")
    schema_kinds = _enum_members(kind_match.group(1))
    for wt in world_types:
        unknown = sorted(set(wt.entity_kinds) - schema_kinds)
        if unknown:
            raise SpecError(
                f"spec/worlds: world_type {wt.name!r} allows entity kind(s) "
                f"{unknown} that the schema's entities_kind_enum CHECK "
                f"rejects; schema allows {sorted(schema_kinds)}")


def _spec_sha256(spec_dir: Path) -> str:
    """Hash of the whole spec tree, recorded in schema_version.spec_sha256.

    Sorted by relative path so the digest is stable across machines.
    """
    digest = hashlib.sha256()
    for path in sorted(spec_dir.rglob("*.hcl")):
        digest.update(str(path.relative_to(spec_dir)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def load_spec(spec_dir: str | Path = DEFAULT_SPEC_DIR) -> Spec:
    """Load, validate, and hash the spec tree.

    Raises :class:`SpecError` with a message naming the file and value on any
    violation. This is the build gate: if it raises, nothing downstream runs.
    """
    root = Path(spec_dir)
    if not root.is_dir():
        raise SpecError(f"spec directory not found: {root}")

    models, ceilings = _load_models(root)
    vector_tables = _load_vectors(root)
    resolver, world_types, statuses = _load_worlds(root)
    _cross_validate(root, models, vector_tables, world_types, statuses)

    return Spec(
        spec_dir=root, models=models, ceilings=ceilings,
        vector_tables=vector_tables, resolver=resolver,
        world_types=world_types, statuses=statuses,
        schema_sql_path=root / "schema" / "schema.hcl",
        sha256=_spec_sha256(root),
    )
