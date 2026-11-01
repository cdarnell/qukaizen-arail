"""Tests 30-31 of sprints/2026-08-10-arail2-persistence-instantiated/
ARCHITECTURE.md §7 — the provisioning class check.

The rule under test: declared and not instantiated => a finding, never
silence. Test 31 constructs each registered mechanism in the
declared-not-instantiated state and asserts a finding, PLUS a synthetic
"instance four" to prove the mechanism generalizes rather than hardcoding
the three known bugs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arail import provisioning
from arail.provisioning import Assertion


@pytest.fixture(autouse=True)
def pkb_index_reset():
    import arail.pkb_index as pki
    pki._reset_for_tests()
    yield
    pki._reset_for_tests()


REPO_ROOT = Path(__file__).resolve().parents[1]


# ── test 30: doctor exit codes on the built-in registry ────────────────────

def test_healthy_registry_has_no_required_findings(tmp_path: Path):
    """relational_store: fresh dir -> not-yet-created is a legitimate,
    expected finding pre-install, so we ensure it first to prove the
    'clean machine, freshly provisioned' path reports OK.

    QA-5 fix note: check_relational_store now resolves EVERY root under
    repo_root/lab/instances/ too (see that function's docstring), so
    repo_root must be its own isolated checkout here — passing the real
    shared REPO_ROOT would pull in this worktree's actual lab/instances/
    state (unrelated to what this test is exercising) and make the
    finding depend on machine state outside the test's control."""
    from arail.dbspec.ensure import ensure_db
    isolated_repo = tmp_path / "checkout"
    data_dir = isolated_repo / "lab" / "data"
    data_dir.mkdir(parents=True)
    ensure_db(data_dir, apply=True, spec_dir=REPO_ROOT / "spec")

    a = provisioning.check_relational_store(
        repo_root=isolated_repo, data_dir=data_dir, spec_dir=REPO_ROOT / "spec")
    assert a.finding is False


def test_uninstantiated_relational_store_is_a_required_finding(tmp_path: Path):
    a = provisioning.check_relational_store(
        repo_root=REPO_ROOT, data_dir=tmp_path, spec_dir=REPO_ROOT / "spec")
    assert a.declared is True
    assert a.instantiated is False
    assert a.finding is True
    assert a.tier == "required"
    assert a.action


# ── test 31: one test per row + the synthetic "instance four" ──────────────

def test_vector_backend_uninstantiated_is_a_finding(monkeypatch):
    monkeypatch.setattr("arail.vector_index.available", lambda: False)
    a = provisioning.check_vector_backend()
    assert a.finding is True
    assert a.tier == "required"


def test_vector_backend_instantiated_is_not_a_finding(monkeypatch):
    monkeypatch.setattr("arail.vector_index.available", lambda: True)
    a = provisioning.check_vector_backend()
    assert a.finding is False


# ARCHITECTURE.md §10 Finding 3: check_kb_gate now decides on
# compiled_kb.gate_state(cheap=False), not approved_paths() alone —
# tests monkeypatch gate_state directly, matching the real predicate.

def _gate_state(**overrides):
    base = {
        "schema": "arail.kb-gate/v1", "enabled": True, "manifest_present": True,
        "approved_count": 0, "live_count": 0, "pending_count": 0,
        "state": "empty", "hint": "",
    }
    base.update(overrides)
    return base


def test_kb_gate_off_is_not_a_finding(monkeypatch):
    from arail import compiled_kb
    monkeypatch.setattr(
        compiled_kb, "gate_state",
        lambda **kw: _gate_state(enabled=False, state="off"), raising=False)
    a = provisioning.check_kb_gate()
    assert a.declared is False
    assert a.finding is False


def test_kb_gate_no_corpus_is_not_applicable_not_a_finding(monkeypatch):
    """Truth table row 2: nothing approved AND nothing pending — there is
    no corpus to have a consent decision about yet. declared=False, so
    this is NOT-APPLICABLE, not a lowered-severity finding."""
    from arail import compiled_kb
    monkeypatch.setattr(
        compiled_kb, "gate_state",
        lambda **kw: _gate_state(approved_count=0, pending_count=0,
                                 live_count=0, state="unbootstrapped"),
        raising=False)
    a = provisioning.check_kb_gate()
    assert a.declared is False
    assert a.finding is False
    assert "no corpus" in a.detail


def test_kb_gate_declared_and_nothing_approved_is_a_finding(monkeypatch):
    """Corpus exists (pending_count > 0), nothing approved yet —
    required, loud finding."""
    from arail import compiled_kb
    monkeypatch.setattr(
        compiled_kb, "gate_state",
        lambda **kw: _gate_state(approved_count=0, pending_count=5,
                                 live_count=0, state="empty"), raising=False)
    a = provisioning.check_kb_gate()
    assert a.declared is True
    assert a.instantiated is False
    assert a.finding is True
    assert a.tier == "required"
    assert "5" in a.detail and "await approval" in a.detail


def test_kb_gate_declared_and_approved_is_not_a_finding(monkeypatch):
    from arail import compiled_kb
    monkeypatch.setattr(
        compiled_kb, "gate_state",
        lambda **kw: _gate_state(approved_count=2, pending_count=0,
                                 live_count=2, state="populated"), raising=False)
    a = provisioning.check_kb_gate()
    assert a.finding is False


def test_kb_gate_cheap_true_pending_unknown_stays_required_not_quiet(monkeypatch):
    """The -1 ("not computed") case must never be read as "no corpus" —
    fail loud on ignorance. approved==0, pending==-1: -1 != 0, so this
    is NOT the "no corpus" branch and must stay declared + a loud
    finding (assuming a corpus exists) rather than going quiet."""
    from arail import compiled_kb
    monkeypatch.setattr(
        compiled_kb, "gate_state",
        lambda **kw: _gate_state(approved_count=0, pending_count=-1,
                                 live_count=0, state="empty"), raising=False)
    a = provisioning.check_kb_gate()
    assert a.declared is True
    assert a.instantiated is False
    assert a.finding is True
    assert "unknown" in a.detail


def test_kb_gate_calls_gate_state_with_cheap_false(monkeypatch):
    """The predicate must never use the cheap=True variant — it exists
    for hot callers (lab_brain/researcher), not doctor, and its -1
    sentinel is exactly the "unknown" case that must stay loud."""
    from arail import compiled_kb
    calls = []

    def _spy(**kw):
        calls.append(kw)
        return _gate_state(approved_count=1, pending_count=0, live_count=1,
                           state="populated")

    monkeypatch.setattr(compiled_kb, "gate_state", _spy, raising=False)
    provisioning.check_kb_gate()
    assert calls == [{"cheap": False}]


def test_kb_gate_operators_real_topology_stays_loud(monkeypatch):
    """THE BOUNDARY THAT MUST SURVIVE (coordinator's explicit ask): the
    operator's real topology — a mounted World with unapproved pages,
    pending_count > 0 — must stay declared and stay loudly degraded.
    Modeled on the measured real case: 351 approvable pages, 0 approved."""
    from arail import compiled_kb
    monkeypatch.setattr(
        compiled_kb, "gate_state",
        lambda **kw: _gate_state(approved_count=0, pending_count=351,
                                 live_count=0, state="empty"), raising=False)
    a = provisioning.check_kb_gate()
    assert a.declared is True
    assert a.instantiated is False
    assert a.finding is True
    assert a.tier == "required"
    assert "351" in a.detail


def test_embedding_provenance_finding_when_degraded():
    from arail import pkb_index
    pkb_index.set_degraded("dimension", "mismatch")
    a = provisioning.check_embedding_provenance()
    assert a.finding is True


def test_embedding_provenance_not_a_finding_when_healthy():
    a = provisioning.check_embedding_provenance()
    assert a.finding is False


def test_instance_registry_finding_when_ondisk_unregistered(tmp_path: Path):
    (tmp_path / "lab" / "instances" / "orphan" / "data").mkdir(parents=True)
    (tmp_path / "lab" / "instances" / "registry.d").mkdir(parents=True)
    a = provisioning.check_instance_registry(repo_root=tmp_path)
    assert a.finding is True
    assert "orphan" in a.detail


def test_instance_registry_no_finding_when_fully_registered(tmp_path: Path):
    a = provisioning.check_instance_registry(repo_root=tmp_path)
    assert a.finding is False


def test_synthetic_instance_four_generalizes():
    """Register a dummy mechanism declared-and-not-instantiated and assert
    it produces a finding — proving the mechanism generalizes rather than
    hardcoding three known bugs."""
    saved = dict(provisioning._REGISTRY)
    try:
        provisioning.register(
            "synthetic_instance_four",
            lambda **_kw: Assertion("synthetic_instance_four", "required",
                                    True, False, "never instantiated", "fix it"))
        results = provisioning.evaluate_all(
            repo_root=Path("."), data_dir=Path("."))
        found = [a for a in results if a.key == "synthetic_instance_four"]
        assert len(found) == 1
        assert found[0].finding is True
    finally:
        provisioning._REGISTRY.clear()
        provisioning._REGISTRY.update(saved)


def test_to_json_schema():
    a = Assertion("x", "required", True, False, "detail", "action")
    payload = provisioning.to_json([a])
    assert payload["schema"] == "arail.provisioning/v1"
    assert payload["assertions"][0]["finding"] is True


def test_check_relational_store_cwd_independent(tmp_path: Path, monkeypatch):
    """REVIEW.md ASK-1 regression: check_relational_store must not silently
    report 'unavailable' just because the caller's CWD isn't the repo
    root — doctor.check_provisioning is exposed to exactly this via
    repo_root=os.getcwd()."""
    monkeypatch.chdir(tmp_path)
    a = provisioning.check_relational_store(
        repo_root=str(REPO_ROOT), data_dir=tmp_path)
    assert a.declared is True
    assert a.detail != ""  # a real pending/blocked reason, not silence
    # Specifically: must not be the CWD-relative "no spec/schema/
    # migrations" false negative.
    assert "no spec/schema/migrations" not in a.detail
