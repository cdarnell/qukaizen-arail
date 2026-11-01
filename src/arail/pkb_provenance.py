"""Vector-index provenance sidecar (C4 in ARCHITECTURE.md).

The 2.0 ``content_refs`` table records ``embedding_model``/``embedding_dim``
per row, but the 1.x per-instance ``pkb_pages`` tables have no such column,
and the cutover to that store is explicitly out of scope for this
integration (the consolidation was rejected — see VISION.md). This module
is the 1.x substitute: a small JSON sidecar written **last**, only after
the data write already succeeded, so hash vectors can never masquerade as
nomic and a crash mid-write never leaves a sidecar that lies about what is
actually sitting in the table next to it.

A new, standalone module (not one of the four files ARCHITECTURE.md
restricts to C1/C2/C4-only edits) so ``pkb.py``, ``pkb_index.py``, and the
new ``pkb_reembed.py`` verb can all share one read/write implementation
without importing each other's internals or risking drift between two
copies of the same JSON shape.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "arail.vector_provenance/v1"


def path_for(db_path: Path, table_name: str = "pkb_pages") -> Path:
    return db_path / f"{table_name}.provenance.json"


def write(db_path: Path, *, table_name: str = "pkb_pages",
          embedding_model: str, embedding_dim: int, spec_sha256: str,
          rows: int) -> None:
    """Write the sidecar. Callers must call this AFTER the data write
    succeeds — never before. Writes via a temp file + ``os.replace``-style
    rename so a crash mid-write leaves either the old sidecar or no sidecar,
    never a half-written one."""
    payload = {
        "schema": SCHEMA,
        "embedding_model": embedding_model,
        "embedding_dim": embedding_dim,
        "spec_sha256": spec_sha256,
        "rows": rows,
        "written_at": datetime.now(timezone.utc).isoformat(),
    }
    p = path_for(db_path, table_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    tmp.replace(p)


def read(db_path: Path, table_name: str = "pkb_pages") -> dict[str, Any] | None:
    """Returns the sidecar dict, or None if absent/unreadable. Never raises."""
    p = path_for(db_path, table_name)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def agrees_with_spec(record: dict[str, Any] | None, *,
                      embedding_model: str, embedding_dim: int) -> bool:
    """True iff ``record`` matches the currently-declared model/dimension.

    A missing sidecar (``record is None``) is NOT "agrees" — callers decide
    what a missing sidecar means (typically: a pre-integration legacy hash
    table, degraded with an actionable message rather than silently served
    or silently dropped).
    """
    if record is None:
        return False
    return (record.get("embedding_model") == embedding_model
            and record.get("embedding_dim") == embedding_dim)
