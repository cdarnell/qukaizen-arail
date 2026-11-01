"""resolve_data_dirs() — the six-roots fix.

Six PKB roots, an empty registry (ARCHITECTURE.md §4.3, Assumption 6): the
operator's ``lab/instances/registry.d/`` is *literally empty* while five
instance dirs exist on disk. Any "all instances" walk driven only by the
shell registry helper (``inst_list_slugs()``) reaches zero of them. This
module is the Python mirror of that fix: it unions the registry, what's
actually on disk, and the root lab, and tags each row with where it came
from so an unregistered instance is a reported finding, not a silent miss.

**Non-promise** (load-bearing, CLAUDE.md): this does not merge, share, or
copy anything between roots. Each row gets its own ``data_dir``; nothing
here reads, writes, or enumerates any ``secrets.env``.

The shell mirror (``scripts/lib/instances.sh``) is NOT implemented in this
pass — see this sprint's BUILD_LOG.md "Deferred scope" section. Shell
callers still resolve instances via ``inst_list_slugs()`` alone today; that
gap is unchanged by this module and is filed as follow-up work.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class DataDirRecord:
    slug: str
    data_dir: str
    pkb_root: Optional[str]
    origin: str  # "root" | "registry" | "ondisk"


def _registry_slugs(registry_dir: Path) -> dict:
    """slug -> parsed json dict, for every well-formed *.json registry file.
    A malformed record is skipped, not fatal — this is a status-time read,
    never a write."""
    out = {}
    if not registry_dir.is_dir():
        return out
    for f in sorted(registry_dir.glob("*.json")):
        try:
            out[f.stem] = json.loads(f.read_text())
        except Exception:  # noqa: BLE001
            continue
    return out


def _ondisk_slugs(instances_root: Path) -> list:
    """Every lab/instances/<slug>/ directory containing data/ or
    instance.env, sorted for determinism."""
    if not instances_root.is_dir():
        return []
    slugs = []
    for d in sorted(instances_root.iterdir()):
        if not d.is_dir() or d.name == "registry.d":
            continue
        if (d / "data").is_dir() or (d / "instance.env").is_file():
            slugs.append(d.name)
    return slugs


def resolve_data_dirs(repo_root, *, root_data_dir=None) -> list:
    """Union of: the root lab's data dir (always exactly one row) ∪ every
    registry.d/*.json slug ∪ every on-disk lab/instances/<slug>/ dir with no
    registry record. Never smaller than either input (F11). No row is ever
    a parent directory of another row (F5) — each instance gets its own
    ``lab/instances/<slug>/data``, never a path that contains another row's
    data_dir."""
    repo_root = Path(repo_root)
    instances_root = repo_root / "lab" / "instances"
    registry_dir = instances_root / "registry.d"

    records = []

    root_dir = str(Path(root_data_dir) if root_data_dir is not None
                    else repo_root / "lab" / "data")
    records.append(DataDirRecord(
        slug="__root__", data_dir=root_dir,
        pkb_root=str(repo_root / "lab" / "pkb"), origin="root"))

    registry = _registry_slugs(registry_dir)
    ondisk = set(_ondisk_slugs(instances_root))

    for slug, rec in registry.items():
        data_dir = rec.get("data_dir") or str(instances_root / slug / "data")
        pkb_root = rec.get("pkb_root") or str(instances_root / slug / "pkb")
        records.append(DataDirRecord(
            slug=slug, data_dir=data_dir, pkb_root=pkb_root, origin="registry"))

    for slug in sorted(ondisk - set(registry)):
        records.append(DataDirRecord(
            slug=slug,
            data_dir=str(instances_root / slug / "data"),
            pkb_root=str(instances_root / slug / "pkb"),
            origin="ondisk"))

    return records
