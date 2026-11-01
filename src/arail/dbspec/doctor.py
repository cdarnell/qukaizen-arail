"""Integrity checks across both stores.

Every finding is attributed to a user, because the failure mode this exists to
catch is single-user corruption: in 1.x one instance's index had accumulated
2,472 retained versions and 253x disk amplification while its siblings were
clean, and nothing surfaced that. A whole-lab average would have hidden it.

The checks are deliberately literal about the spec's global-versioning rule.
Because all worlds share one embedding model and one dimension at any spec
version, a row that disagrees is not "configured differently" — it is
corruption, and it is reported as an error rather than a note.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from arail.dbspec.generated.models_registry import EMBEDDING_DIM, EMBEDDING_MODEL
from arail.dbspec.reconcile import TableActual, inspect_table, resolve_dataset_path
from arail.dbspec.spec import Spec, VectorTableSpec

__all__ = ["Finding", "DoctorReport", "run_doctor", "ERROR", "WARNING", "INFO"]

ERROR = "error"
WARNING = "warning"
INFO = "info"

_SEVERITY_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}

# Above this share of rows sitting outside the vector index, recall degrades
# enough to matter. Updates move rows out of the index — they stay searchable
# but unindexed — so this climbs silently under normal use.
_UNINDEXED_FRACTION_LIMIT = 0.20


@dataclass(frozen=True)
class Finding:
    severity: str
    check: str
    message: str
    user_id: Optional[str] = None
    world_id: Optional[str] = None
    path: Optional[str] = None

    def render(self) -> str:
        scope = self.user_id or "-"
        if self.world_id:
            scope = f"{scope}/{self.world_id}"
        line = f"  [{self.severity.upper():7}] {self.check:24} {scope:28} {self.message}"
        if self.path:
            line += f"\n{'':>44}{self.path}"
        return line


@dataclass(frozen=True)
class DoctorReport:
    findings: Tuple[Finding, ...]
    checked: Tuple[str, ...]

    @property
    def errors(self) -> Tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == ERROR)

    @property
    def warnings(self) -> Tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == WARNING)

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        lines = ["ARAIL database doctor", ""]
        if not self.findings:
            lines.append("  clean — no findings")
        else:
            ordered = sorted(
                self.findings,
                key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9),
                               f.check, f.user_id or "", f.world_id or ""))
            lines.extend(f.render() for f in ordered)
        lines.append("")
        lines.append(
            f"  {len(self.errors)} error(s), {len(self.warnings)} warning(s) "
            f"across {len(self.checked)} check(s)")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _worlds(conn) -> List[dict]:
    try:
        rows = conn.execute(
            "SELECT id, slug, user_id, status FROM worlds ORDER BY user_id, slug"
        ).fetchall()
    except Exception:
        return []
    return [dict(r) for r in rows]


def _check_worlds_without_entities(conn, worlds) -> List[Finding]:
    out: List[Finding] = []
    for world in worlds:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM entities WHERE world_id = ?",
            (world["id"],)).fetchone()["n"]
        if count == 0:
            out.append(Finding(
                WARNING, "world-empty",
                f"world {world['slug']!r} has no entities",
                user_id=world["user_id"], world_id=world["slug"]))
    return out


def _check_embedding_provenance(conn, worlds) -> List[Finding]:
    """Every content_ref must name the spec's model and dimension."""
    out: List[Finding] = []
    by_id = {w["id"]: w for w in worlds}
    try:
        rows = conn.execute(
            "SELECT world_id, embedding_model, embedding_dim, COUNT(*) AS n "
            "FROM content_refs GROUP BY world_id, embedding_model, embedding_dim"
        ).fetchall()
    except Exception:
        return out
    for row in rows:
        world = by_id.get(row["world_id"], {})
        user = world.get("user_id")
        slug = world.get("slug", row["world_id"])
        if row["embedding_model"] != EMBEDDING_MODEL:
            out.append(Finding(
                ERROR, "embedding-model-drift",
                f"{row['n']} row(s) embedded by {row['embedding_model']!r}, but "
                f"the spec declares {EMBEDDING_MODEL!r}. Re-embed them: "
                f"'./arailctl db migrate --apply'.",
                user_id=user, world_id=slug))
        if row["embedding_dim"] != EMBEDDING_DIM:
            out.append(Finding(
                ERROR, "embedding-dim-drift",
                f"{row['n']} row(s) at dim {row['embedding_dim']}, but the spec "
                f"declares {EMBEDDING_DIM}. Schema versioning is global, so "
                f"this is corruption, not configuration.",
                user_id=user, world_id=slug))
    return out


def _check_orphaned_content_refs(conn, worlds, row_keys_by_table
                                 ) -> List[Finding]:
    """content_refs pointing at Lance rows that are not there."""
    out: List[Finding] = []
    by_id = {w["id"]: w for w in worlds}
    try:
        rows = conn.execute(
            "SELECT world_id, lance_table, row_key FROM content_refs").fetchall()
    except Exception:
        return out
    missing: Dict[Tuple[str, str], int] = {}
    for row in rows:
        table = row["lance_table"]
        known = row_keys_by_table.get(table)
        if known is None:
            continue  # dataset absent entirely; reported by the table check
        if row["row_key"] not in known:
            key = (row["world_id"], table)
            missing[key] = missing.get(key, 0) + 1
    for (world_id, table), count in sorted(missing.items()):
        world = by_id.get(world_id, {})
        out.append(Finding(
            ERROR, "content-ref-orphan",
            f"{count} content_ref(s) point at rows missing from Lance table "
            f"{table!r}",
            user_id=world.get("user_id"), world_id=world.get("slug", world_id)))
    return out


def _scan_vectors(dataset_path: str, vector_column: str, expected_dim: int
                  ) -> Tuple[int, int, int, Dict[int, int]]:
    """Return (zero, nan, wrong_dim, dims_seen) for a dataset's vectors."""
    try:
        import lancedb
        import numpy as np
    except ImportError:
        return 0, 0, 0, {}
    parent = str(Path(dataset_path).parent)
    name = Path(dataset_path).name[: -len(".lance")]
    try:
        table = lancedb.connect(parent).open_table(name)
        arrow = table.to_arrow()
    except Exception:
        return 0, 0, 0, {}
    if vector_column not in arrow.schema.names:
        return 0, 0, 0, {}
    zeros = nans = wrong = 0
    dims: Dict[int, int] = {}
    for chunk in arrow.column(vector_column).chunks:
        for i in range(len(chunk)):
            if not chunk[i].is_valid:
                continue
            values = np.asarray(chunk[i].values)
            dim = int(values.shape[0])
            dims[dim] = dims.get(dim, 0) + 1
            if dim != expected_dim:
                wrong += 1
            if np.isnan(values).any():
                nans += 1
            elif not values.any():
                zeros += 1
    return zeros, nans, wrong, dims


def _check_table(table_spec: VectorTableSpec, actual: TableActual,
                 user_id: Optional[str]) -> List[Finding]:
    out: List[Finding] = []
    if not actual.exists:
        out.append(Finding(
            INFO, "table-absent",
            f"Lance table {table_spec.name!r} does not exist yet",
            user_id=user_id, path=actual.dataset_path))
        return out

    if actual.fragments > table_spec.max_fragments:
        out.append(Finding(
            WARNING, "fragments-high",
            f"{table_spec.name}: {actual.fragments} fragments exceeds the "
            f"declared target of {table_spec.max_fragments}. Run "
            f"'./arailctl db optimize'.",
            user_id=user_id, path=actual.dataset_path))

    if actual.versions > table_spec.version_retention:
        out.append(Finding(
            WARNING, "versions-retained",
            f"{table_spec.name}: {actual.versions} versions retained, window "
            f"is {table_spec.version_retention} "
            f"({actual.disk_bytes / 1e6:.1f} MB on disk). Run "
            f"'./arailctl db optimize'.",
            user_id=user_id, path=actual.dataset_path))

    if actual.vector_dim is not None and actual.vector_dim != EMBEDDING_DIM:
        out.append(Finding(
            ERROR, "vector-dim",
            f"{table_spec.name}: vector column is {actual.vector_dim}-dim but "
            f"the spec declares {EMBEDDING_DIM}",
            user_id=user_id, path=actual.dataset_path))

    zeros, nans, wrong, dims = _scan_vectors(
        actual.dataset_path, table_spec.vector.name, EMBEDDING_DIM)
    if zeros:
        out.append(Finding(
            ERROR, "degenerate-vector",
            f"{table_spec.name}: {zeros} zero vector(s) — a failed embedding "
            f"call was stored instead of raising",
            user_id=user_id, path=actual.dataset_path))
    if nans:
        out.append(Finding(
            ERROR, "degenerate-vector",
            f"{table_spec.name}: {nans} vector(s) containing NaN",
            user_id=user_id, path=actual.dataset_path))
    if wrong:
        out.append(Finding(
            ERROR, "degenerate-vector",
            f"{table_spec.name}: {wrong} vector(s) of the wrong dimension "
            f"(seen: {dims})",
            user_id=user_id, path=actual.dataset_path))

    has_vector_index = any(
        kind in ("IVF_PQ", "IVF_FLAT", "HNSW_PQ", "HNSW_SQ")
        for kind in actual.indexes.values())
    if actual.rows >= table_spec.index_min_rows and not has_vector_index:
        out.append(Finding(
            WARNING, "index-missing",
            f"{table_spec.name}: {actual.rows} rows at or above the "
            f"{table_spec.index_min_rows}-row threshold but has no vector "
            f"index; every search is a flat scan. Run './arailctl db apply'.",
            user_id=user_id, path=actual.dataset_path))
    return out


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run_doctor(spec: Spec, conn, data_dir: str, pkb_root: str, *,
               user_id: Optional[str] = None) -> DoctorReport:
    """Run every integrity check. Findings are attributed per user."""
    findings: List[Finding] = []
    checks = ["world-empty", "embedding-model-drift", "embedding-dim-drift",
              "content-ref-orphan", "fragments-high", "versions-retained",
              "vector-dim", "degenerate-vector", "index-missing"]

    worlds = _worlds(conn)
    if worlds:
        findings.extend(_check_worlds_without_entities(conn, worlds))
        findings.extend(_check_embedding_provenance(conn, worlds))

    row_keys_by_table: Dict[str, set] = {}
    for table_spec in spec.vector_tables:
        dataset_path = resolve_dataset_path(table_spec, data_dir, pkb_root)
        actual = inspect_table(table_spec, dataset_path)
        findings.extend(_check_table(table_spec, actual, user_id))
        if actual.exists and table_spec.primary_key:
            row_keys_by_table[table_spec.name] = _row_keys(
                dataset_path, table_spec.primary_key)

    if worlds:
        findings.extend(
            _check_orphaned_content_refs(conn, worlds, row_keys_by_table))

    return DoctorReport(tuple(findings), tuple(checks))


def _row_keys(dataset_path: str, primary_key: str) -> set:
    try:
        import lancedb
    except ImportError:
        return set()
    parent = str(Path(dataset_path).parent)
    name = Path(dataset_path).name[: -len(".lance")]
    try:
        table = lancedb.connect(parent).open_table(name)
        arrow = table.to_arrow()
        if primary_key not in arrow.schema.names:
            return set()
        return set(arrow.column(primary_key).to_pylist())
    except Exception:
        return set()
