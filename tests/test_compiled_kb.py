"""WK-10: the Compiled Knowledge Base — the human-approved layer agents build
on. Raw corpus is a candidate pool; nothing crosses the gate without an
explicit human approval; agents retrieve ONLY from approved knowledge.
"""

from __future__ import annotations

import json
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
    # a world term page (WK-1 shape) — the headline promotion candidate
    _mk(root, "sources/world-math/terms/algebra.md",
        "---\ntitle: Algebra\ntags: [world-math]\naliases: [algebra]\n---\n\n"
        "The study of symbols.\n\nSource: model:local\n")
    # an agent experiment output — 'true experiment' the user wants to promote
    _mk(root, "agents/experiments/2026-07-08_exp1.md", "# Experiment 1\nResult: good.")
    # a note
    _mk(root, "notes/scratch.md", "# Scratch\nmisc thoughts")
    # things that must NEVER be candidates
    _mk(root, "index.md", "# TOC")
    _mk(root, "compiled/docs/arail-pkb.md", "# auto-doc")
    _mk(root, "sources/world-math/terms.json", '{"terms": []}')  # bundle machinery
    return root


def test_pending_lists_only_real_candidates(pkb):
    pending = ckb.list_pending(pkb)
    paths = {c["path"] for c in pending}
    assert "sources/world-math/terms/algebra.md" in paths
    assert "agents/experiments/2026-07-08_exp1.md" in paths
    assert "notes/scratch.md" in paths
    # excluded: TOC, auto-docs, bundle machinery
    assert "index.md" not in paths
    assert "compiled/docs/arail-pkb.md" not in paths
    assert "sources/world-math/terms.json" not in paths


def test_provenance_is_derived_not_asserted(pkb):
    pending = {c["path"]: c for c in ckb.list_pending(pkb)}
    term = pending["sources/world-math/terms/algebra.md"]
    assert term["kind"] == "world_term"
    assert term["provenance"] == "model:local"      # read from the Source: line
    assert term["world"] == "world-math"
    assert term["title"] == "Algebra"
    exp = pending["agents/experiments/2026-07-08_exp1.md"]
    assert exp["kind"] == "agent_experiment"
    assert exp["provenance"] == "agent_experiment"


def test_approve_records_provenance_and_hash(pkb):
    rec = ckb.approve(["sources/world-math/terms/algebra.md"], pkb)[0]
    assert rec["provenance"] == "model:local"
    assert len(rec["sha256"]) == 64
    assert rec["approved_by"] == "operator"
    assert ckb.is_approved("sources/world-math/terms/algebra.md", pkb)
    # persisted to the manifest under compiled/kb/
    manifest = json.loads((pkb / "compiled" / "kb" / "approved.json").read_text())
    assert manifest["schema"] == ckb.SCHEMA
    assert "sources/world-math/terms/algebra.md" in manifest["items"]


def test_approved_items_leave_the_pending_queue(pkb):
    ckb.approve(["notes/scratch.md"], pkb)
    paths = {c["path"] for c in ckb.list_pending(pkb)}
    assert "notes/scratch.md" not in paths


def test_reject_hides_candidate_reversibly(pkb):
    ckb.reject(["notes/scratch.md"], pkb)
    assert "notes/scratch.md" not in {c["path"] for c in ckb.list_pending(pkb)}
    # approving a rejected item re-admits it and clears the rejection
    ckb.approve(["notes/scratch.md"], pkb)
    assert ckb.is_approved("notes/scratch.md", pkb)


def test_revoke_unapproves_without_touching_raw(pkb):
    ckb.approve(["notes/scratch.md"], pkb)
    assert ckb.revoke(["notes/scratch.md"], pkb) == 1
    assert not ckb.is_approved("notes/scratch.md", pkb)
    assert (pkb / "notes" / "scratch.md").exists()   # raw file untouched
    # back in the queue for reconsideration
    assert "notes/scratch.md" in {c["path"] for c in ckb.list_pending(pkb)}


def test_approve_rejects_path_traversal(pkb):
    # traversal / absolute paths never write outside the pkb
    assert ckb.approve(["../../etc/passwd"], pkb) == []
    assert ckb.approve(["/etc/passwd"], pkb) == []
    assert not ckb.approved_paths(pkb)


def test_approve_ignores_nonexistent_and_noncandidate(pkb):
    ckb.approve(["sources/world-math/terms.json",   # machinery, not a candidate
                 "does/not/exist.md"], pkb)
    assert not ckb.approved_paths(pkb)


def test_gate_toggle_env(monkeypatch):
    monkeypatch.delenv("ARAIL_APPROVED_ONLY", raising=False)
    assert ckb.gate_enabled() is True                 # default ON
    monkeypatch.setenv("ARAIL_APPROVED_ONLY", "off")
    assert ckb.gate_enabled() is False
    monkeypatch.setenv("ARAIL_APPROVED_ONLY", "on")
    assert ckb.gate_enabled() is True


def test_fail_closed_on_corrupt_manifest(pkb):
    (pkb / "compiled" / "kb").mkdir(parents=True)
    (pkb / "compiled" / "kb" / "approved.json").write_text("{ not json")
    # corrupt manifest reads as nothing approved, never raises
    assert ckb.approved_paths(pkb) == set()


# ── manifest_present / gate_state (QA-6 bootstrap) ───────────────────────

def test_manifest_present_false_when_missing(pkb):
    assert ckb.manifest_present(pkb) is False


def test_manifest_present_true_after_write(pkb):
    ckb.approve(["notes/scratch.md"], pkb)
    assert ckb.manifest_present(pkb) is True


@pytest.mark.parametrize("payload", ["{ not json", "null", '"x"'])
def test_manifest_present_corrupt_or_non_dict_list_shapes(pkb, payload):
    (pkb / "compiled" / "kb").mkdir(parents=True)
    (pkb / "compiled" / "kb" / "approved.json").write_text(payload)
    assert ckb.manifest_present(pkb) is False


def test_manifest_present_list_shape_is_true(pkb):
    (pkb / "compiled" / "kb").mkdir(parents=True)
    (pkb / "compiled" / "kb" / "approved.json").write_text('["a", "b"]')
    assert ckb.manifest_present(pkb) is True


def test_manifest_present_truncated_json_is_false(pkb):
    (pkb / "compiled" / "kb").mkdir(parents=True)
    (pkb / "compiled" / "kb" / "approved.json").write_text("{ not json")
    assert ckb.manifest_present(pkb) is False


def test_gate_state_off(pkb, monkeypatch):
    monkeypatch.setenv("ARAIL_APPROVED_ONLY", "off")
    gs = ckb.gate_state(pkb)
    assert gs["state"] == "off"
    assert gs["enabled"] is False


def test_gate_state_unbootstrapped(pkb, monkeypatch):
    monkeypatch.delenv("ARAIL_APPROVED_ONLY", raising=False)
    gs = ckb.gate_state(pkb)
    assert gs["state"] == "unbootstrapped"
    assert gs["manifest_present"] is False


def test_gate_state_empty_after_bootstrap_with_nothing_approved(pkb, monkeypatch):
    monkeypatch.delenv("ARAIL_APPROVED_ONLY", raising=False)
    # simulate bootstrap: write manifest with zero items
    ckb.approve([], pkb)
    gs = ckb.gate_state(pkb)
    assert gs["state"] == "empty"
    assert gs["manifest_present"] is True
    assert gs["live_count"] == 0


def test_gate_state_populated(pkb, monkeypatch):
    monkeypatch.delenv("ARAIL_APPROVED_ONLY", raising=False)
    ckb.approve(["notes/scratch.md"], pkb)
    gs = ckb.gate_state(pkb)
    assert gs["state"] == "populated"
    assert gs["live_count"] == 1


def test_gate_state_cheap_skips_pending_walk(pkb, monkeypatch):
    monkeypatch.delenv("ARAIL_APPROVED_ONLY", raising=False)
    ckb.approve(["notes/scratch.md"], pkb)

    def _boom(*a, **k):
        raise AssertionError("pending_count must not be called when cheap=True")

    monkeypatch.setattr(ckb, "pending_count", _boom)
    gs = ckb.gate_state(pkb, cheap=True)
    assert gs["pending_count"] == -1


def test_gate_state_never_raises_on_total_failure(pkb, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(ckb, "manifest_present", _boom)
    gs = ckb.gate_state(pkb)
    assert gs["state"] == "unbootstrapped"
    assert gs["manifest_present"] is False
