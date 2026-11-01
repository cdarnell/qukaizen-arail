"""The Compiled KB must stay reconciled with the raw corpus on disk.

The bug this pins: the Compiled KB is a *manifest of pointers* into the raw
corpus, and ``world_mount._sweep_other_worlds()`` deletes the previous
World's staged term files on every mount. Nothing pruned the manifest, so
approvals outlived their files — and because the retrieval gate is a
query-time intersection (approved paths ∩ live search hits), a dangling
pointer matches nothing. Observed in the field: 554 of 556 approvals were
corpses, ``search_for_agents()`` returned zero hits for every query in every
World, and no error was raised anywhere, because failing closed to "no
approved truth" is indistinguishable from "nothing approved yet".

These tests are about that silence as much as the pruning.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from arail import compiled_kb


def _pkb(tmp_path: Path) -> Path:
    root = tmp_path / "pkb"
    (root / "compiled" / "kb").mkdir(parents=True)
    return root


def _term(root: Path, world: str, name: str) -> str:
    rel = f"sources/world-{world}/terms/{name}.md"
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"# {name}\n\nA term in the {world} world.\n")
    return rel


def _approved_count(root: Path) -> int:
    raw = json.loads((root / "compiled" / "kb" / "approved.json").read_text())
    return len(raw["items"])


# ── dangling_paths / prune_dangling ──────────────────────────────────────

def test_dangling_paths_reports_only_missing_files(tmp_path):
    root = _pkb(tmp_path)
    kept = _term(root, "finance", "apr")
    swept = _term(root, "ai", "adamw")
    compiled_kb.approve([kept, swept], pkb_root=root)
    assert _approved_count(root) == 2

    # Simulate _sweep_other_worlds deleting the previous World's staged dir.
    (root / swept).unlink()

    assert compiled_kb.dangling_paths(root) == [swept]
    # Read-only: reporting must not mutate the manifest.
    assert _approved_count(root) == 2


def test_prune_drops_dangling_and_keeps_live(tmp_path):
    root = _pkb(tmp_path)
    kept = _term(root, "finance", "apr")
    swept = _term(root, "ai", "adamw")
    compiled_kb.approve([kept, swept], pkb_root=root)
    (root / swept).unlink()

    dropped = compiled_kb.prune_dangling(root)

    assert dropped == [swept]
    assert compiled_kb.approved_paths(root) == {kept}
    assert compiled_kb.dangling_paths(root) == []


def test_prune_is_idempotent_and_quiet_when_clean(tmp_path):
    root = _pkb(tmp_path)
    rel = _term(root, "finance", "apr")
    compiled_kb.approve([rel], pkb_root=root)

    assert compiled_kb.prune_dangling(root) == []
    assert compiled_kb.prune_dangling(root) == []
    assert compiled_kb.approved_paths(root) == {rel}


def test_prune_refuses_when_pkb_root_is_missing(tmp_path):
    """The guard that matters most.

    A missing/unmounted pkb root makes EVERY approved path look deleted. The
    correct reading is "the lab is misconfigured", not "the operator revoked
    everything" — a prune that fired here would destroy the manifest at
    exactly the moment it is least able to tell the difference.
    """
    root = _pkb(tmp_path)
    rel = _term(root, "finance", "apr")
    compiled_kb.approve([rel], pkb_root=root)
    before = (root / "compiled" / "kb" / "approved.json").read_text()

    gone = tmp_path / "not-a-real-root"
    assert compiled_kb.prune_dangling(gone) == []
    assert compiled_kb.dangling_paths(gone) == []
    # The real manifest is untouched.
    assert (root / "compiled" / "kb" / "approved.json").read_text() == before


# ── the mount lifecycle actually calls it ────────────────────────────────

def test_sweep_prune_helper_reconciles_after_a_sweep(tmp_path):
    """world_mount._prune_swept_approvals is the seam mount/swap/unmount use."""
    from arail import world_mount

    root = _pkb(tmp_path)
    kept = _term(root, "finance", "apr")
    swept = _term(root, "ai", "adamw")
    compiled_kb.approve([kept, swept], pkb_root=root)

    # What _sweep_other_worlds does: remove every other world's staged dir.
    import shutil
    shutil.rmtree(root / "sources" / "world-ai")

    n = world_mount._prune_swept_approvals(root)

    assert n == 1
    assert compiled_kb.approved_paths(root) == {kept}


def test_prune_helper_never_raises_on_a_broken_root(tmp_path):
    """A KB bookkeeping failure must not be able to fail a mount that has
    otherwise succeeded — the helper swallows and reports 0."""
    from arail import world_mount

    assert world_mount._prune_swept_approvals(tmp_path / "nope") == 0


def test_refresh_kb_surfaces_prunes(tmp_path, monkeypatch):
    """mount() and swap() reconcile via _refresh_kb_surfaces."""
    from arail import world_mount

    root = _pkb(tmp_path)
    swept = _term(root, "ai", "adamw")
    compiled_kb.approve([swept], pkb_root=root)
    (root / swept).unlink()

    # Isolate from the index/wiki side of the refresh — this test is about
    # the prune, and those two are already best-effort/never-raise.
    monkeypatch.setattr(world_mount, "_log", world_mount._log)
    world_mount._refresh_kb_surfaces(root)

    assert compiled_kb.approved_paths(root) == set()


def test_unmount_with_remove_staged_prunes(tmp_path):
    """unmount(remove_staged=True) deletes raw files exactly like the sweep,
    but does NOT go through _refresh_kb_surfaces — so it needs its own call.
    Without it the unmount path leaves dangling approvals the mount path
    would have cleaned.
    """
    import inspect
    from arail import world_mount

    src = inspect.getsource(world_mount.unmount)
    assert "_prune_swept_approvals" in src, (
        "unmount(remove_staged=True) rmtree's the staged dir; it must "
        "reconcile the approval manifest or it reintroduces the bug"
    )


# ── the silence this was hiding behind ───────────────────────────────────

def test_gate_goes_empty_when_every_approval_dangles(tmp_path):
    """Documents the failure mode itself: the gate reports nothing wrong.

    approved_paths() still returns 2 entries — it is a manifest read, not a
    disk check — so nothing downstream can tell that both are corpses. This
    is why the condition survived two weeks in a real lab and why doctor now
    reports dangling counts explicitly.
    """
    root = _pkb(tmp_path)
    a, b = _term(root, "ai", "adamw"), _term(root, "ai", "ablation")
    compiled_kb.approve([a, b], pkb_root=root)
    (root / a).unlink()
    (root / b).unlink()

    assert len(compiled_kb.approved_paths(root)) == 2      # looks healthy
    assert len(compiled_kb.dangling_paths(root)) == 2      # ...is not
    # No exception, no warning — the gate cannot distinguish this from a
    # lab where the operator simply approved nothing.


def test_doctor_reports_dangling_approvals():
    """doctor is where the silent state becomes visible."""
    import inspect
    from arail import doctor

    src = inspect.getsource(doctor.check_knowledge_base)
    assert "dangling" in src
    assert "compiled_kb" in src
    assert "pkb prune" in src, "doctor must name the fix, not just the symptom"
