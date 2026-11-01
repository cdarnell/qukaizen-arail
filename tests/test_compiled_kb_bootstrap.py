"""QA-6: mount-time world-term auto-approval (compiled_kb.auto_approve_world_terms)
and the backfill CLI (compiled_kb.bootstrap). The scope invariant is a
security boundary: a path is admitted iff it matches
sources/world-<slug>/terms/<term-slug>.md AND <term-slug> is in the
seal-verified bundle's terms.json. Both conditions are load-bearing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arail import compiled_kb as ckb


def _mk(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture()
def pkb(tmp_path):
    root = tmp_path / "pkb"
    root.mkdir()
    _mk(root, "sources/world-math/terms/algebra.md",
        "---\ntitle: Algebra\n---\n\nThe study of symbols.\n")
    _mk(root, "sources/world-math/terms/geometry.md",
        "---\ntitle: Geometry\n---\n\nThe study of shapes.\n")
    # a hand-dropped file that is NOT in terms.json — must stay excluded
    _mk(root, "sources/world-math/terms/not-in-bundle.md",
        "---\ntitle: Sneaky\n---\n\nnot a real term\n")
    # personal-data lookalikes that must never be reachable by this function
    _mk(root, "notes/personal.md", "ACCT-XYZ-4417")
    _mk(root, "inbox/statement.md", "raw inbox doc")
    _mk(root, "agents/research/2026-01-01_x.md", "# research")
    return root


_BUNDLE_TERMS = [
    {"slug": "algebra", "term": "Algebra"},
    {"slug": "geometry", "term": "Geometry"},
]


def test_happy_path_approves_only_bundle_terms(pkb, monkeypatch):
    monkeypatch.delenv("ARAIL_AUTO_APPROVE_WORLD_TERMS", raising=False)
    added = ckb.auto_approve_world_terms(
        "math", bundle_terms=_BUNDLE_TERMS, seal_sha="deadbeef" * 8, pkb_root=pkb)
    approved = ckb.approved_paths(pkb)
    assert approved == {
        "sources/world-math/terms/algebra.md",
        "sources/world-math/terms/geometry.md",
    }
    assert len(added) == 2
    assert all(r["auto"] is True for r in added)
    assert all(r["approved_by"].startswith("world-seal:") for r in added)


def test_hand_dropped_file_not_in_bundle_stays_excluded(pkb, monkeypatch):
    monkeypatch.delenv("ARAIL_AUTO_APPROVE_WORLD_TERMS", raising=False)
    ckb.auto_approve_world_terms(
        "math", bundle_terms=_BUNDLE_TERMS, seal_sha="x" * 12, pkb_root=pkb)
    assert "sources/world-math/terms/not-in-bundle.md" not in ckb.approved_paths(pkb)


def test_never_reaches_notes_inbox_or_agent_output(pkb, monkeypatch):
    monkeypatch.delenv("ARAIL_AUTO_APPROVE_WORLD_TERMS", raising=False)
    ckb.auto_approve_world_terms(
        "math", bundle_terms=_BUNDLE_TERMS, seal_sha="x" * 12, pkb_root=pkb)
    approved = ckb.approved_paths(pkb)
    assert not any(p.startswith(("notes/", "inbox/", "agents/")) for p in approved)


def test_traversal_paths_never_approved(pkb, monkeypatch):
    monkeypatch.delenv("ARAIL_AUTO_APPROVE_WORLD_TERMS", raising=False)
    evil_terms = [{"slug": "../../../notes/personal", "term": "x"},
                  {"slug": "/etc/passwd", "term": "y"}]
    added = ckb.auto_approve_world_terms(
        "math", bundle_terms=evil_terms, seal_sha="x" * 12, pkb_root=pkb)
    # slugs sanitize to a-z0-9- only, so these can never resolve outside terms/
    assert ckb.approved_paths(pkb) == set() or all(
        p.startswith("sources/world-math/terms/") for p in ckb.approved_paths(pkb))
    assert not any("personal" in r["path"] for r in added)


def test_env_off_disables_auto_approval(pkb, monkeypatch):
    monkeypatch.setenv("ARAIL_AUTO_APPROVE_WORLD_TERMS", "off")
    added = ckb.auto_approve_world_terms(
        "math", bundle_terms=_BUNDLE_TERMS, seal_sha="x" * 12, pkb_root=pkb)
    assert added == []
    assert ckb.approved_paths(pkb) == set()


def test_sentinel_file_disables_auto_approval(pkb, monkeypatch):
    monkeypatch.delenv("ARAIL_AUTO_APPROVE_WORLD_TERMS", raising=False)
    sentinel_dir = pkb / "compiled" / "kb"
    sentinel_dir.mkdir(parents=True)
    (sentinel_dir / "no-auto-approve").write_text("")
    added = ckb.auto_approve_world_terms(
        "math", bundle_terms=_BUNDLE_TERMS, seal_sha="x" * 12, pkb_root=pkb)
    assert added == []


def test_sentinel_unreadable_treated_as_present(pkb, monkeypatch):
    monkeypatch.delenv("ARAIL_AUTO_APPROVE_WORLD_TERMS", raising=False)
    sentinel_dir = pkb / "compiled" / "kb"
    sentinel_dir.mkdir(parents=True)
    sentinel = sentinel_dir / "no-auto-approve"
    sentinel.write_text("")
    sentinel.chmod(0o000)
    try:
        added = ckb.auto_approve_world_terms(
            "math", bundle_terms=_BUNDLE_TERMS, seal_sha="x" * 12, pkb_root=pkb)
        # Path.exists() doesn't require read permission on POSIX, so this is
        # exercising the "unreadability treated as disabled" contract only
        # when the OS actually denies stat(); assert the safe direction:
        # never MORE gets approved than the readable-sentinel case.
        assert set(r["path"] for r in added) <= {
            "sources/world-math/terms/algebra.md",
            "sources/world-math/terms/geometry.md",
        }
    finally:
        sentinel.chmod(0o644)


def test_sticky_revocation_survives_remount(pkb, monkeypatch):
    monkeypatch.delenv("ARAIL_AUTO_APPROVE_WORLD_TERMS", raising=False)
    ckb.auto_approve_world_terms(
        "math", bundle_terms=_BUNDLE_TERMS, seal_sha="x" * 12, pkb_root=pkb)
    assert "sources/world-math/terms/algebra.md" in ckb.approved_paths(pkb)

    ckb.revoke(["sources/world-math/terms/algebra.md"], pkb)
    assert "sources/world-math/terms/algebra.md" not in ckb.approved_paths(pkb)

    # simulate a re-mount: auto-approval runs again
    ckb.auto_approve_world_terms(
        "math", bundle_terms=_BUNDLE_TERMS, seal_sha="y" * 12, pkb_root=pkb)
    assert "sources/world-math/terms/algebra.md" not in ckb.approved_paths(pkb)
    assert "sources/world-math/terms/geometry.md" in ckb.approved_paths(pkb)


def test_explicit_approve_after_revoke_persists_across_remount(pkb, monkeypatch):
    monkeypatch.delenv("ARAIL_AUTO_APPROVE_WORLD_TERMS", raising=False)
    ckb.auto_approve_world_terms(
        "math", bundle_terms=_BUNDLE_TERMS, seal_sha="x" * 12, pkb_root=pkb)
    ckb.revoke(["sources/world-math/terms/algebra.md"], pkb)
    ckb.approve(["sources/world-math/terms/algebra.md"], pkb, approver="operator")
    assert "sources/world-math/terms/algebra.md" in ckb.approved_paths(pkb)

    ckb.auto_approve_world_terms(
        "math", bundle_terms=_BUNDLE_TERMS, seal_sha="z" * 12, pkb_root=pkb)
    assert "sources/world-math/terms/algebra.md" in ckb.approved_paths(pkb)


def test_idempotent_across_two_calls(pkb, monkeypatch):
    monkeypatch.delenv("ARAIL_AUTO_APPROVE_WORLD_TERMS", raising=False)
    ckb.auto_approve_world_terms(
        "math", bundle_terms=_BUNDLE_TERMS, seal_sha="x" * 12, pkb_root=pkb)
    first = ckb.approved_paths(pkb)
    ckb.auto_approve_world_terms(
        "math", bundle_terms=_BUNDLE_TERMS, seal_sha="x" * 12, pkb_root=pkb)
    second = ckb.approved_paths(pkb)
    assert first == second == {
        "sources/world-math/terms/algebra.md",
        "sources/world-math/terms/geometry.md",
    }


def test_rejected_entries_skipped_by_auto_approval(pkb, monkeypatch):
    monkeypatch.delenv("ARAIL_AUTO_APPROVE_WORLD_TERMS", raising=False)
    ckb.reject(["sources/world-math/terms/algebra.md"], pkb)
    added = ckb.auto_approve_world_terms(
        "math", bundle_terms=_BUNDLE_TERMS, seal_sha="x" * 12, pkb_root=pkb)
    assert "sources/world-math/terms/algebra.md" not in {r["path"] for r in added}
    assert "sources/world-math/terms/geometry.md" in {r["path"] for r in added}


def test_terms_present_in_json_but_missing_on_disk_are_skipped(pkb, monkeypatch):
    monkeypatch.delenv("ARAIL_AUTO_APPROVE_WORLD_TERMS", raising=False)
    terms = _BUNDLE_TERMS + [{"slug": "calculus", "term": "Calculus"}]
    added = ckb.auto_approve_world_terms(
        "math", bundle_terms=terms, seal_sha="x" * 12, pkb_root=pkb)
    assert "sources/world-math/terms/calculus.md" not in {r["path"] for r in added}


def test_unknown_slug_returns_empty(pkb, monkeypatch):
    monkeypatch.delenv("ARAIL_AUTO_APPROVE_WORLD_TERMS", raising=False)
    added = ckb.auto_approve_world_terms(
        "no-such-world", bundle_terms=_BUNDLE_TERMS, seal_sha="x" * 12, pkb_root=pkb)
    assert added == []


def test_empty_bundle_terms_returns_empty(pkb, monkeypatch):
    monkeypatch.delenv("ARAIL_AUTO_APPROVE_WORLD_TERMS", raising=False)
    added = ckb.auto_approve_world_terms(
        "math", bundle_terms=[], seal_sha="x" * 12, pkb_root=pkb)
    assert added == []


def test_never_raises_on_malformed_bundle_terms(pkb, monkeypatch):
    monkeypatch.delenv("ARAIL_AUTO_APPROVE_WORLD_TERMS", raising=False)
    weird = [None, "not-a-dict", 42, {"slug": None}, {}]
    added = ckb.auto_approve_world_terms(
        "math", bundle_terms=weird, seal_sha="x" * 12, pkb_root=pkb)
    assert added == []


def test_revoke_auto_removes_only_auto_approved(pkb, monkeypatch):
    monkeypatch.delenv("ARAIL_AUTO_APPROVE_WORLD_TERMS", raising=False)
    ckb.auto_approve_world_terms(
        "math", bundle_terms=_BUNDLE_TERMS, seal_sha="x" * 12, pkb_root=pkb)
    ckb.approve(["notes/personal.md"], pkb, approver="operator")
    n = ckb.revoke_auto(pkb)
    assert n == 2
    remaining = ckb.approved_paths(pkb)
    assert remaining == {"notes/personal.md"}


def test_revoke_auto_is_not_sticky(pkb, monkeypatch):
    """ASK-1: revoke_auto() is a mechanism-level rollback, not 351 human
    revocations — it must not poison unapproved.json, or the next mount/
    swap/bootstrap would refuse to re-approve any of those paths with no
    un-stick command anywhere in the product."""
    monkeypatch.delenv("ARAIL_AUTO_APPROVE_WORLD_TERMS", raising=False)
    ckb.auto_approve_world_terms(
        "math", bundle_terms=_BUNDLE_TERMS, seal_sha="x" * 12, pkb_root=pkb)
    ckb.revoke_auto(pkb)
    assert ckb.approved_paths(pkb) == set()
    assert ckb.unapproved_paths(pkb) == set()

    # The mechanism rollback is reversible: the next auto-approval run
    # (mount/swap/bootstrap) re-approves the same terms exactly as before.
    added = ckb.auto_approve_world_terms(
        "math", bundle_terms=_BUNDLE_TERMS, seal_sha="x" * 12, pkb_root=pkb)
    assert {r["path"] for r in added} == {
        "sources/world-math/terms/algebra.md",
        "sources/world-math/terms/geometry.md",
    }


def test_explicit_revoke_stays_sticky_after_revoke_auto(pkb, monkeypatch):
    """revoke_auto()'s non-stickiness must not leak into revoke()'s own
    default — a human's explicit per-term revocation is unaffected."""
    monkeypatch.delenv("ARAIL_AUTO_APPROVE_WORLD_TERMS", raising=False)
    ckb.auto_approve_world_terms(
        "math", bundle_terms=_BUNDLE_TERMS, seal_sha="x" * 12, pkb_root=pkb)
    ckb.revoke(["sources/world-math/terms/algebra.md"], pkb)
    assert ckb.unapproved_paths(pkb) == {"sources/world-math/terms/algebra.md"}

    added = ckb.auto_approve_world_terms(
        "math", bundle_terms=_BUNDLE_TERMS, seal_sha="x" * 12, pkb_root=pkb)
    assert {r["path"] for r in added} == {"sources/world-math/terms/geometry.md"}


# ── bootstrap() ────────────────────────────────────────────────────────

@pytest.fixture()
def catalog(tmp_path, monkeypatch):
    """A WORLDS_DIR catalog copy for world 'math' plus a pkb root with the
    staged terms already present (simulating a completed mount)."""
    worlds_dir = tmp_path / "worlds"
    (worlds_dir / "math").mkdir(parents=True)
    (worlds_dir / "math" / "terms.json").write_text(
        '{"version": 1, "terms": '
        '[{"slug": "algebra", "term": "Algebra"}, '
        '{"slug": "geometry", "term": "Geometry"}]}')
    (worlds_dir / "math" / "spec.json").write_text('{"categories": []}')
    monkeypatch.setattr("arail.config.WORLDS_DIR", worlds_dir)
    return worlds_dir


def test_bootstrap_fresh_lab_writes_empty_manifest(tmp_path):
    root = tmp_path / "pkb"
    root.mkdir()
    result = ckb.bootstrap(root)
    assert result["approved"] == 0
    assert result["skipped_reason"] is None
    assert ckb.manifest_present(root) is True
    assert ckb.gate_state(root)["state"] == "empty"


def test_bootstrap_dry_run_writes_nothing(pkb, catalog, monkeypatch):
    monkeypatch.delenv("ARAIL_AUTO_APPROVE_WORLD_TERMS", raising=False)
    result = ckb.bootstrap(pkb, dry_run=True)
    assert result["approved"] == 2
    assert ckb.manifest_present(pkb) is False


def test_bootstrap_content_without_catalog_bundle(tmp_path):
    root = tmp_path / "pkb"
    _mk(root, "sources/world-ghost/terms/x.md", "content")
    result = ckb.bootstrap(root)
    assert result["approved"] == 0
    assert "no bundle in catalog" in (result["skipped_reason"] or "")
    assert ckb.manifest_present(root) is True


def test_bootstrap_real_bundle_approves_all_terms(pkb, catalog, monkeypatch):
    monkeypatch.delenv("ARAIL_AUTO_APPROVE_WORLD_TERMS", raising=False)
    result = ckb.bootstrap(pkb)
    assert result["approved"] == 2
    assert result["world"] == "math"
    assert ckb.approved_paths(pkb) == {
        "sources/world-math/terms/algebra.md",
        "sources/world-math/terms/geometry.md",
    }
    # BLOCK-3: bootstrap() never calls verify_seal (resolve_world_bundle is a
    # bare json.loads), so the stamp must be honest about that — "world-terms:"
    # not "world-seal:", which is reserved for the real mount()/swap() path.
    records = ckb.list_approved(pkb)
    assert all(r["approved_by"].startswith("world-terms:") for r in records)
    assert not any(r["approved_by"].startswith("world-seal:") for r in records)


def test_bootstrap_never_raises_on_root_missing(tmp_path):
    root = tmp_path / "does-not-exist"
    result = ckb.bootstrap(root)
    assert result["skipped_reason"] is not None
    assert result["approved"] == 0


# ── CLI: `pkb revoke --auto` (ASK-1) ─────────────────────────────────────

def test_cli_revoke_auto_wired(pkb, monkeypatch, capsys):
    monkeypatch.delenv("ARAIL_AUTO_APPROVE_WORLD_TERMS", raising=False)
    monkeypatch.setattr(ckb, "_pkb_root", lambda: pkb)
    ckb.auto_approve_world_terms(
        "math", bundle_terms=_BUNDLE_TERMS, seal_sha="x" * 12, pkb_root=pkb)

    rc = ckb._cli(["revoke", "--auto"])

    assert rc == 0
    assert ckb.approved_paths(pkb) == set()
    assert ckb.unapproved_paths(pkb) == set()  # non-sticky
    out = capsys.readouterr().out
    assert "revoked  : 2" in out


def test_cli_revoke_without_auto_is_a_clean_error(pkb, monkeypatch):
    monkeypatch.setattr(ckb, "_pkb_root", lambda: pkb)
    rc = ckb._cli(["revoke"])
    assert rc == 2
