"""Unit tests for arail.data_dirs.resolve_data_dirs — tests 13-15 of
sprints/2026-08-10-arail2-persistence-instantiated/ARCHITECTURE.md §7.
"""

from __future__ import annotations

import json
from pathlib import Path

from arail.data_dirs import resolve_data_dirs


def _make_ondisk_instance(root: Path, slug: str) -> None:
    d = root / "lab" / "instances" / slug / "data"
    d.mkdir(parents=True)


def _make_registry_entry(root: Path, slug: str) -> None:
    reg = root / "lab" / "instances" / "registry.d"
    reg.mkdir(parents=True, exist_ok=True)
    (reg / f"{slug}.json").write_text(json.dumps({
        "data_dir": str(root / "lab" / "instances" / slug / "data"),
        "pkb_root": str(root / "lab" / "instances" / slug / "pkb"),
    }))


# ── 13. empty registry + 5 on-disk instances + root -> 6 rows ──────────────

def test_empty_registry_five_ondisk_yields_six_rows(tmp_path: Path):
    for slug in ("ai", "qukaizen", "video-games", "debt-finance", "finance"):
        _make_ondisk_instance(tmp_path, slug)
    # registry.d exists but is empty (the operator's exact machine state).
    (tmp_path / "lab" / "instances" / "registry.d").mkdir(parents=True)

    rows = resolve_data_dirs(tmp_path)

    assert len(rows) == 6
    ondisk_rows = [r for r in rows if r.origin == "ondisk"]
    assert len(ondisk_rows) == 5
    assert {r.slug for r in ondisk_rows} == {
        "ai", "qukaizen", "video-games", "debt-finance", "finance"}
    assert any(r.origin == "root" for r in rows)


# ── 14. registry record with no on-disk dir -> row present, flagged ────────

def test_registry_record_without_ondisk_dir_still_present(tmp_path: Path):
    _make_registry_entry(tmp_path, "ghost")

    rows = resolve_data_dirs(tmp_path)

    ghost = [r for r in rows if r.slug == "ghost"]
    assert len(ghost) == 1
    assert ghost[0].origin == "registry"


# ── 15. no row is a parent of another row ───────────────────────────────────

def test_no_row_is_parent_of_another(tmp_path: Path):
    for slug in ("ai", "qukaizen"):
        _make_ondisk_instance(tmp_path, slug)
    _make_registry_entry(tmp_path, "finance")

    rows = resolve_data_dirs(tmp_path)
    dirs = [Path(r.data_dir).resolve() for r in rows]

    for i, a in enumerate(dirs):
        for j, b in enumerate(dirs):
            if i == j:
                continue
            assert b not in a.parents, f"{a} is inside {b}"


def test_registry_and_ondisk_union_not_double_counted(tmp_path: Path):
    _make_ondisk_instance(tmp_path, "ai")
    _make_registry_entry(tmp_path, "ai")

    rows = resolve_data_dirs(tmp_path)
    ai_rows = [r for r in rows if r.slug == "ai"]

    assert len(ai_rows) == 1
    assert ai_rows[0].origin == "registry"
