"""Installed skill packs from before the QueueLLM display rename are refreshed
only when byte-equal to a previously shipped file (same shape as the registry
note migration); edited files survive."""
from __future__ import annotations

import hashlib

from arail.skill_packs import _PRE_RENAME_SHA256, install_pack


def test_pre_rename_hashes_do_not_match_current_shipped_files():
    from arail.skill_packs import _pack_skill_dir, get_pack
    for pack_id in ("model-building", "onboarding"):
        for sid in get_pack(pack_id).skills:
            current = (_pack_skill_dir(pack_id, sid) / "SKILL.md").read_bytes()
            assert hashlib.sha256(current).hexdigest() not in _PRE_RENAME_SHA256.get(sid, ())


def test_edited_pack_skill_is_not_refreshed(tmp_path):
    install_pack("model-building", pkb_root=tmp_path)
    dst = tmp_path / "skills" / "optimize-aerollm" / "SKILL.md"
    edited = dst.read_text() + "\nmine\n"
    dst.write_text(edited)
    res = install_pack("model-building", pkb_root=tmp_path, force=False)
    assert "optimize-aerollm" in res["skipped_existing"]
    assert dst.read_text() == edited


def test_unedited_pre_rename_skill_is_replaced_once_then_skipped(tmp_path, monkeypatch):
    import arail.skill_packs as sp

    install_pack("model-building", pkb_root=tmp_path)
    dst = tmp_path / "skills" / "optimize-aerollm" / "SKILL.md"
    legacy = b"---\nname: optimize-aerollm\n---\nOptimize AeroLLM (pre-rename bytes).\n"
    dst.write_bytes(legacy)
    monkeypatch.setitem(
        sp._PRE_RENAME_SHA256, "optimize-aerollm",
        frozenset({hashlib.sha256(legacy).hexdigest()}))

    first = install_pack("model-building", pkb_root=tmp_path, force=False)
    assert "optimize-aerollm" in first["installed"]
    refreshed = dst.read_bytes()
    assert refreshed != legacy and b"AeroLLM" not in refreshed

    second = install_pack("model-building", pkb_root=tmp_path, force=False)
    assert "optimize-aerollm" in second["skipped_existing"]
    assert "optimize-aerollm" not in second["installed"]
    assert dst.read_bytes() == refreshed
