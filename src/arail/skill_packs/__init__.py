"""Skill pack management — the curated repository of skill bundles.

Mirrors the proven pattern in :mod:`arail.pkb_seed` for KB starter
packs. Every pack is a folder of ``<skill_id>/SKILL.md`` files
shipped in the repo at ``src/arail/skill_packs/<pack_id>/``. The
manifest at ``src/arail/skill_packs/manifest.yaml`` declares which
packs exist, their display metadata, and the skills they install.

Public API:

* :func:`list_packs`              — browse the catalog
* :func:`installed_skills_in_pack` — which of a pack's skills are present
* :func:`install_pack`            — copy SKILL.md folders into ``lab/pkb/skills/``
* :func:`remove_pack`             — uninstall (only files declared in manifest)

All write operations are idempotent and refuse to clobber user edits
unless ``force=True``. The Skills tab UI calls these via the
``/api/skills/packs/*`` endpoints in :mod:`arail.portal.app`.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]


_PACKS_DIR = Path(__file__).parent
_MANIFEST_PATH = _PACKS_DIR / "manifest.yaml"


@dataclass
class PackMeta:
    """One row of the manifest, deserialized."""

    id: str
    name: str
    description: str
    tags: list[str]
    skills: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tags": list(self.tags),
            "skills": list(self.skills),
        }


def _pkb_root() -> Path:
    """Resolve the lab PKB root via the central config."""
    from arail.config import PKB_ROOT
    return PKB_ROOT


def _pack_skill_dir(pack_id: str, skill_id: str) -> Path:
    """Source path: where the pack stores the skill's SKILL.md."""
    return _PACKS_DIR / pack_id / skill_id


def _installed_skill_dir(skill_id: str, *, pkb_root: Path | None = None) -> Path:
    """Destination path: where the skill lives on the running lab."""
    return (pkb_root or _pkb_root()) / "skills" / skill_id


def list_packs() -> list[PackMeta]:
    """Read manifest.yaml and return the curated packs.

    Returns [] on a missing or malformed manifest so callers can
    degrade to "no packs available" gracefully.
    """
    if not _MANIFEST_PATH.exists():
        return []
    try:
        raw = yaml.safe_load(_MANIFEST_PATH.read_text())
    except yaml.YAMLError:
        return []
    if not isinstance(raw, list):
        return []
    out: list[PackMeta] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        pid = str(entry.get("id") or "").strip()
        if not pid:
            continue
        out.append(PackMeta(
            id=pid,
            name=str(entry.get("name") or pid),
            description=str(entry.get("description") or ""),
            tags=[str(t) for t in (entry.get("tags") or [])],
            skills=[str(s) for s in (entry.get("skills") or [])],
        ))
    return out


def get_pack(pack_id: str) -> PackMeta | None:
    """Lookup a single pack by id."""
    for pack in list_packs():
        if pack.id == pack_id:
            return pack
    return None


def installed_skills_in_pack(
    pack_id: str,
    *,
    pkb_root: Path | None = None,
) -> list[str]:
    """Return the subset of ``pack``'s skills that are already on disk.

    Used by the Skills UI to show "X of Y installed" progress per
    pack and to enable the right action button (install/update/
    remove).
    """
    pack = get_pack(pack_id)
    if pack is None:
        return []
    return [
        sid for sid in pack.skills
        if (_installed_skill_dir(sid, pkb_root=pkb_root) / "SKILL.md").exists()
    ]


# sha256 of SKILL.md files as shipped before the QueueLLM display rename
# (origin/main 5f775f1c), keyed by skill id. An installed copy that is
# byte-equal to one of these was never edited, so it is refreshed even with
# ``force=False``; a hand-edited copy never matches and survives.
_PRE_RENAME_SHA256: dict[str, frozenset[str]] = {
    "frontier-local-models": frozenset({
        "3b593874699947a41c37fc59bf47ccef9dca28d874192196cc6fde3c6776169c"}),
    "optimize-aerollm": frozenset({
        "d3b59d81e52cc0818dd56da2955258d06694033e71759141318b5c79b204f597"}),
    "understanding-precision": frozenset({
        "ecea3beb63c093434895ab6c979285082cb31a75a33237e7036c7f630349db8e"}),
    "setup-arail": frozenset({
        "66a44ba74b96fbdee7b343195e242b4110d7f9e259c32290dec500068177454b"}),
}


def _is_unedited_pre_rename(skill_id: str, path: Path) -> bool:
    known = _PRE_RENAME_SHA256.get(skill_id)
    if not known:
        return False
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() in known
    except OSError:
        return False


def install_pack(
    pack_id: str,
    *,
    pkb_root: Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Materialize a pack's skills under ``lab/pkb/skills/``.

    Idempotent. With ``force=False`` (default), refuses to overwrite
    an existing SKILL.md — preserves user edits and lets the operator
    rerun installation safely. With ``force=True``, replaces every
    skill in the pack (the "Re-install" / "Update" UX action).

    Returns ``{ok, pack, installed: [], skipped_existing: [], errors: []}``.
    """
    pack = get_pack(pack_id)
    if pack is None:
        return {"ok": False, "error": f"unknown pack: {pack_id}"}

    skills_root = (pkb_root or _pkb_root()) / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    installed: list[str] = []
    skipped: list[str] = []
    errors: list[dict[str, str]] = []
    for sid in pack.skills:
        src = _pack_skill_dir(pack.id, sid) / "SKILL.md"
        if not src.exists():
            errors.append({"skill": sid, "reason": "pack source SKILL.md missing"})
            continue
        dst_dir = _installed_skill_dir(sid, pkb_root=pkb_root)
        dst = dst_dir / "SKILL.md"
        if (dst.exists() and not force
                and not _is_unedited_pre_rename(sid, dst)):
            skipped.append(sid)
            continue
        dst_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, dst)
        except OSError as e:
            errors.append({"skill": sid, "reason": str(e)})
            continue
        installed.append(sid)

    return {
        "ok": not errors,
        "pack": pack.id,
        "installed": installed,
        "skipped_existing": skipped,
        "errors": errors,
    }


def remove_pack(
    pack_id: str,
    *,
    pkb_root: Path | None = None,
) -> dict[str, Any]:
    """Remove only the skills declared in the pack's manifest.

    User-authored skills (any folder under ``lab/pkb/skills/`` that
    isn't in the manifest) survive untouched. We deliberately delete
    by-name from the manifest rather than by directory comparison so
    a user can edit a packed SKILL.md heavily and still uninstall it
    cleanly.
    """
    pack = get_pack(pack_id)
    if pack is None:
        return {"ok": False, "error": f"unknown pack: {pack_id}"}

    removed: list[str] = []
    missing: list[str] = []
    for sid in pack.skills:
        dst_dir = _installed_skill_dir(sid, pkb_root=pkb_root)
        if not dst_dir.exists():
            missing.append(sid)
            continue
        try:
            shutil.rmtree(dst_dir)
            removed.append(sid)
        except OSError:
            pass

    return {
        "ok": True,
        "pack": pack.id,
        "removed": removed,
        "missing": missing,
    }


def packs_with_status(*, pkb_root: Path | None = None) -> list[dict[str, Any]]:
    """Convenience for the API: each pack annotated with install status.

    Returns one dict per pack with ``installed_count`` /
    ``skill_count`` so the UI can show "2 of 3 installed" without a
    second roundtrip per pack.
    """
    out: list[dict[str, Any]] = []
    for pack in list_packs():
        installed = installed_skills_in_pack(pack.id, pkb_root=pkb_root)
        out.append({
            **pack.as_dict(),
            "installed_count": len(installed),
            "skill_count": len(pack.skills),
            "fully_installed": len(installed) == len(pack.skills),
        })
    return out
