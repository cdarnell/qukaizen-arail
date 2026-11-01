"""QA-6: the swap path — zero coverage in review round 1, and the hot path in
practice (world_routes.py:452 means mount() runs once per lab ever; swap()
runs on every World switch and every reseal).

Covers REVIEW.md round-2 "What QA should hammer" items 2, 3, 4 plus the
approve-then-prune ordering constraint the architect flagged as "correct but
load-bearing and accidental-looking".
"""

from __future__ import annotations

import json
import pathlib
import shutil

import pytest

from arail import compiled_kb as ckb
from arail import pkb as pkb_mod
from arail import world_mount
from arail.world_mount import mount, swap

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "world-bundles"
PHYSICS = FIXTURES / "physics"
ART = FIXTURES / "art-history-skill"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ARAIL_AUTO_APPROVE_WORLD_TERMS", raising=False)
    monkeypatch.delenv("ARAIL_APPROVED_ONLY", raising=False)


def _droppable_slug(bundle_dir: pathlib.Path) -> str:
    """A term no other term links to — dropping a linked term makes
    dac_world's closed-graph gate refuse the reseal, which is a property of
    the corpus, not of this sprint."""
    terms = json.loads((bundle_dir / "terms.json").read_text())["terms"]
    linked: set[str] = set()
    for t in terms:
        linked.update(t.get("related") or [])
        linked.update(t.get("aka") or [])
    free = [t["slug"] for t in terms if t["slug"] not in linked]
    assert free, "fixture has no unlinked term to drop"
    return sorted(free)[0]


def _slugs(bundle_dir: pathlib.Path) -> set[str]:
    terms = json.loads((bundle_dir / "terms.json").read_text())["terms"]
    return {t["slug"] for t in terms}


def _expected(bundle_dir: pathlib.Path, world: str) -> set[str]:
    return {f"sources/world-{world}/terms/{s}.md" for s in _slugs(bundle_dir)}


@pytest.fixture()
def lab(tmp_path):
    dd = tmp_path / "data"
    dd.mkdir()
    return tmp_path / "pkb", dd


# ── Swap chains ──────────────────────────────────────────────────────────

def test_swap_chain_a_b_a_b_never_cross_contaminates(lab):
    pkb, dd = lab
    steps = [(PHYSICS, "physics", mount), (ART, "art-history", swap),
             (PHYSICS, "physics", swap), (ART, "art-history", swap)]
    for bundle, world, fn in steps:
        fn(bundle, pkb_root=pkb, data_dir=dd)
        approved = ckb.approved_paths(pkb)
        assert approved == _expected(bundle, world), f"after {fn.__name__} {world}"
        other = "art-history" if world == "physics" else "physics"
        assert not any(p.startswith(f"sources/world-{other}/") for p in approved)


def test_swap_chain_manifest_does_not_grow_unbounded(lab):
    """Corpse accumulation on the hot path: after four switches the manifest
    must be exactly one World's worth, not four."""
    pkb, dd = lab
    mount(PHYSICS, pkb_root=pkb, data_dir=dd)
    for bundle in (ART, PHYSICS, ART, PHYSICS, ART):
        swap(bundle, pkb_root=pkb, data_dir=dd)
    raw = json.loads((pkb / "compiled" / "kb" / "approved.json").read_text())
    assert len(raw["items"]) == len(_slugs(ART))
    # and every surviving approval points at a file that actually exists
    assert all((pkb / rel).is_file() for rel in raw["items"])


def test_swap_leaves_no_dangling_approvals(lab):
    pkb, dd = lab
    mount(PHYSICS, pkb_root=pkb, data_dir=dd)
    swap(ART, pkb_root=pkb, data_dir=dd)
    assert ckb.dangling_paths(pkb) == [] or set(ckb.dangling_paths(pkb)) == set()
    st = ckb.gate_state(pkb)
    assert st["approved_count"] == st["live_count"] == len(_slugs(ART))


def test_retrieval_follows_the_swap(lab):
    """The user-visible half: after switching, agents retrieve the NEW
    World's vocabulary and none of the old one's."""
    pkb, dd = lab
    mount(PHYSICS, pkb_root=pkb, data_dir=dd)
    art_slug = sorted(_slugs(ART))[0]
    phys_slug = sorted(_slugs(PHYSICS))[0]

    before = pkb_mod.retrieve_for_agents(phys_slug.replace("-", " "), pkb)
    assert before["gate"]["state"] == "populated"

    swap(ART, pkb_root=pkb, data_dir=dd)
    after = pkb_mod.retrieve_for_agents(art_slug.replace("-", " "), pkb)
    assert all(h["path"].startswith("sources/world-art-history/")
               for h in after["hits"])
    assert all(not h["path"].startswith("sources/world-physics/")
               for h in after["hits"])


# ── The approve-then-prune ordering constraint ───────────────────────────

def _record_order(monkeypatch) -> list[str]:
    """Instrument the two calls whose relative order ARCHITECTURE.md's data
    flow declares load-bearing."""
    seen: list[str] = []
    import arail.compiled_kb as ckb_mod
    real_auto = ckb_mod.auto_approve_world_terms
    real_refresh = world_mount._refresh_kb_surfaces

    def _auto(*a, **k):
        seen.append("approve")
        return real_auto(*a, **k)

    def _refresh(*a, **k):
        seen.append("prune")
        return real_refresh(*a, **k)

    monkeypatch.setattr(ckb_mod, "auto_approve_world_terms", _auto)
    monkeypatch.setattr(world_mount, "_refresh_kb_surfaces", _refresh)
    return seen


def test_mount_runs_auto_approve_before_the_prune(lab, monkeypatch):
    """FAILS if someone reorders step 3.5 past _refresh_kb_surfaces."""
    pkb, dd = lab
    seen = _record_order(monkeypatch)
    mount(PHYSICS, pkb_root=pkb, data_dir=dd)
    assert seen == ["approve", "prune"], seen


def test_swap_runs_auto_approve_before_the_prune(lab, monkeypatch):
    pkb, dd = lab
    mount(PHYSICS, pkb_root=pkb, data_dir=dd)
    seen = _record_order(monkeypatch)
    swap(ART, pkb_root=pkb, data_dir=dd)
    assert seen == ["approve", "prune"], seen


def test_prune_after_approve_keeps_the_incoming_world(lab):
    """The behavioral consequence of the ordering, independent of the
    instrumentation above: prune must not eat what the hook just approved."""
    pkb, dd = lab
    mount(PHYSICS, pkb_root=pkb, data_dir=dd)
    swap(ART, pkb_root=pkb, data_dir=dd)
    assert ckb.approved_paths(pkb) == _expected(ART, "art-history")
    # an explicit second prune is a no-op — nothing left to reap
    assert ckb.prune_dangling(pkb) == []


# ── Reseal (the librarian / world-forge path, via _reseal_and_swap) ──────

def _reseal_without(bundle_src: pathlib.Path, dest: pathlib.Path,
                    drop_slug: str) -> pathlib.Path:
    from dac_world.seal import reseal_bundle
    shutil.copytree(bundle_src, dest)
    terms = json.loads((dest / "terms.json").read_text())["terms"]
    kept = [t for t in terms if t["slug"] != drop_slug]
    reseal_bundle(dest, terms=kept)
    return dest


def test_reseal_dropping_a_term_prunes_its_approval(lab, tmp_path):
    pkb, dd = lab
    mount(PHYSICS, pkb_root=pkb, data_dir=dd)
    dropped = _droppable_slug(PHYSICS)
    resealed = _reseal_without(PHYSICS, tmp_path / "physics-v2", dropped)

    swap(resealed, pkb_root=pkb, data_dir=dd)

    approved = ckb.approved_paths(pkb)
    assert f"sources/world-physics/terms/{dropped}.md" not in approved
    assert not (pkb / f"sources/world-physics/terms/{dropped}.md").exists()
    # every remaining term is still approved
    assert approved == {f"sources/world-physics/terms/{s}.md"
                        for s in _slugs(PHYSICS) - {dropped}}


def test_reseal_that_changes_terms_json_reapproves_the_new_set(lab, tmp_path):
    pkb, dd = lab
    mount(PHYSICS, pkb_root=pkb, data_dir=dd)
    dropped = _droppable_slug(PHYSICS)
    v2 = _reseal_without(PHYSICS, tmp_path / "v2", dropped)
    swap(v2, pkb_root=pkb, data_dir=dd)
    # ...and back to the full set
    swap(PHYSICS, pkb_root=pkb, data_dir=dd)
    assert ckb.approved_paths(pkb) == _expected(PHYSICS, "physics")


# ── Sticky revocation across the hot path ────────────────────────────────

def test_human_revocation_survives_a_swap_round_trip(lab):
    pkb, dd = lab
    mount(PHYSICS, pkb_root=pkb, data_dir=dd)
    victim = f"sources/world-physics/terms/{sorted(_slugs(PHYSICS))[0]}.md"
    assert ckb.revoke([victim], pkb) == 1
    assert victim in ckb.unapproved_paths(pkb)

    swap(ART, pkb_root=pkb, data_dir=dd)
    swap(PHYSICS, pkb_root=pkb, data_dir=dd)

    approved = ckb.approved_paths(pkb)
    assert victim not in approved, "a human revocation was undone by a World switch"
    assert approved == _expected(PHYSICS, "physics") - {victim}


def test_prune_dangling_never_writes_unapproved_json(lab):
    """If prune_dangling made the swept World's paths sticky, switching away
    from a World would permanently poison every one of its terms."""
    pkb, dd = lab
    mount(PHYSICS, pkb_root=pkb, data_dir=dd)
    # approve() writes an (empty) unapproved.json; what must never happen is
    # the prune ADDING the swept World's paths to it.
    assert ckb.unapproved_paths(pkb) == set()

    swap(ART, pkb_root=pkb, data_dir=dd)
    assert ckb.unapproved_paths(pkb) == set(), \
        "prune poisoned the switched-away World's terms"

    swap(PHYSICS, pkb_root=pkb, data_dir=dd)
    assert ckb.approved_paths(pkb) == _expected(PHYSICS, "physics")


def test_prune_dangling_leaves_an_existing_unapproved_set_untouched(lab):
    pkb, dd = lab
    mount(PHYSICS, pkb_root=pkb, data_dir=dd)
    victim = f"sources/world-physics/terms/{sorted(_slugs(PHYSICS))[0]}.md"
    ckb.revoke([victim], pkb)
    before = ckb.unapproved_paths(pkb)
    ckb.prune_dangling(pkb)
    swap(ART, pkb_root=pkb, data_dir=dd)
    assert ckb.unapproved_paths(pkb) == before


def test_revoke_auto_is_non_sticky_and_a_swap_restores_everything(lab):
    pkb, dd = lab
    mount(PHYSICS, pkb_root=pkb, data_dir=dd)
    n = ckb.revoke_auto(pkb)
    assert n == len(_slugs(PHYSICS))
    assert ckb.approved_paths(pkb) == set()
    assert ckb.unapproved_paths(pkb) == set(), "mechanism rollback must not be sticky"

    swap(ART, pkb_root=pkb, data_dir=dd)
    swap(PHYSICS, pkb_root=pkb, data_dir=dd)
    assert ckb.approved_paths(pkb) == _expected(PHYSICS, "physics")


def test_human_revocation_survives_a_revoke_auto_rollback(lab):
    """Only revoke_auto is non-sticky; a genuine human revoke stays sticky
    even when the mechanism is rolled back and re-enabled around it."""
    pkb, dd = lab
    mount(PHYSICS, pkb_root=pkb, data_dir=dd)
    victim = f"sources/world-physics/terms/{sorted(_slugs(PHYSICS))[0]}.md"
    ckb.revoke([victim], pkb)
    ckb.revoke_auto(pkb)
    swap(ART, pkb_root=pkb, data_dir=dd)
    swap(PHYSICS, pkb_root=pkb, data_dir=dd)
    assert victim not in ckb.approved_paths(pkb)
    assert victim in ckb.unapproved_paths(pkb)


def test_explicit_reapproval_unsticks_and_then_survives_swaps(lab):
    pkb, dd = lab
    mount(PHYSICS, pkb_root=pkb, data_dir=dd)
    victim = f"sources/world-physics/terms/{sorted(_slugs(PHYSICS))[0]}.md"
    ckb.revoke([victim], pkb)
    ckb.approve([victim], pkb)  # operator changes their mind
    assert victim not in ckb.unapproved_paths(pkb)
    swap(ART, pkb_root=pkb, data_dir=dd)
    swap(PHYSICS, pkb_root=pkb, data_dir=dd)
    assert victim in ckb.approved_paths(pkb)


# ── F3: unwritable compiled/kb during mount and swap ─────────────────────

def _make_kb_unwritable(pkb: pathlib.Path):
    kb = pkb / "compiled" / "kb"
    kb.mkdir(parents=True, exist_ok=True)
    kb.chmod(0o500)
    return kb


def test_f3_mount_survives_unwritable_compiled_kb(lab):
    import os
    if os.geteuid() == 0:
        pytest.skip("running as root — permissions are not enforced")
    pkb, dd = lab
    pkb.mkdir(parents=True, exist_ok=True)
    kb = _make_kb_unwritable(pkb)
    try:
        rec = mount(PHYSICS, pkb_root=pkb, data_dir=dd)
        assert rec.world == "physics"
        assert ckb.approved_paths(pkb) == set()          # gate stays closed
        assert ckb.gate_state(pkb)["state"] == "unbootstrapped"
        assert not (kb / "approved.json").exists()
    finally:
        kb.chmod(0o700)


def test_f3_swap_survives_unwritable_compiled_kb(lab):
    import os
    if os.geteuid() == 0:
        pytest.skip("running as root — permissions are not enforced")
    pkb, dd = lab
    mount(PHYSICS, pkb_root=pkb, data_dir=dd)
    kb = pkb / "compiled" / "kb"
    kb.chmod(0o500)
    try:
        rec = swap(ART, pkb_root=pkb, data_dir=dd)
        assert rec.world == "art-history"
        # the pre-existing manifest is intact — never widened, never lost
        assert ckb.approved_paths(pkb) == _expected(PHYSICS, "physics")
    finally:
        kb.chmod(0o700)
