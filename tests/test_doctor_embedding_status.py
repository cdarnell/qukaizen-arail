"""C4/C5 doctor wiring (ARCHITECTURE.md, arail2-tier1-integration).

./arailctl doctor must exit 3 when the vector-index provenance disagrees
with the spec (C4), but stay INFO-only (exit 0 by default) when the
embedding provider is simply unreachable/not-yet-built — that state is
the legitimate clean-machine/CI case (A8), not a spec violation.
"""

from __future__ import annotations

import importlib

import pytest

import arail.doctor as doctor


@pytest.fixture(autouse=True)
def clean_findings():
    doctor._FINDINGS.clear()
    yield
    doctor._FINDINGS.clear()


@pytest.fixture
def isolated_pkb(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_PKB", str(tmp_path))
    import arail.config
    import arail.pkb as pkb
    importlib.reload(arail.config)
    importlib.reload(pkb)
    pkb.scaffold(tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def pkb_index_reset():
    import arail.pkb_index as pki
    pki._reset_for_tests()
    yield
    pki._reset_for_tests()


def _finding(name):
    return next((f for f in doctor._FINDINGS if f.name == name), None)


def test_healthy_index_does_not_degrade(isolated_pkb, monkeypatch, capsys):
    import arail.pkb as pkb
    import arail.pkb_index as pki

    (isolated_pkb / "notes").mkdir(parents=True, exist_ok=True)
    (isolated_pkb / "notes" / "a.md").write_text("# a\n")
    pkb.index_all(pkb_root=isolated_pkb, include_docs=False)
    pki._pkb_root_cache = isolated_pkb  # ensure_ready reuses this root

    monkeypatch.setattr("arail.pkb_index._pkb_root_from_env", lambda: isolated_pkb)
    doctor.check_knowledge_base()

    provenance_finding = _finding("embedding_provenance")
    assert provenance_finding is not None
    assert provenance_finding.ok is True


def test_provenance_mismatch_degrades_required(isolated_pkb, monkeypatch):
    import arail.pkb as pkb
    import arail.pkb_index as pki
    import arail.pkb_provenance as prov

    (isolated_pkb / "notes").mkdir(parents=True, exist_ok=True)
    (isolated_pkb / "notes" / "a.md").write_text("# a\n")
    pkb.index_all(pkb_root=isolated_pkb, include_docs=False)

    db_path = pkb._vector_db_path(isolated_pkb)
    prov.write(db_path, embedding_model="some-other-model", embedding_dim=768,
               spec_sha256="deadbeef", rows=1)

    pki._pkb_root_cache = isolated_pkb
    monkeypatch.setattr("arail.pkb_index._pkb_root_from_env", lambda: isolated_pkb)
    doctor.check_knowledge_base()

    provenance_finding = _finding("embedding_provenance")
    assert provenance_finding is not None
    assert provenance_finding.ok is False
    assert provenance_finding.level == "required"


def test_legacy_128dim_index_degrades_required_not_info(isolated_pkb, monkeypatch):
    """REVIEW2.md required test #5: doctor on a legacy 128-dim index must
    exit 3, not 0. Before the fix, the exit-code decision substring-matched
    "provenance" in the reason text, and the dimension-mismatch message
    doesn't contain that word -- so every one of the operator's five real
    (128-dim, no-sidecar) Worlds reported INFO-only and doctor exited 0."""
    import lancedb  # type: ignore[import-not-found]
    from arail.vector_index import hash_embedding
    import arail.pkb_index as pki

    db_path = isolated_pkb / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[{
        "path": "notes/a.md", "name": "a.md",
        "vector": hash_embedding("a"),  # 128-dim legacy hash vector
        "mtime": 0.0, "source_kind": "user",
    }], mode="overwrite")

    pki._pkb_root_cache = isolated_pkb
    monkeypatch.setattr("arail.pkb_index._pkb_root_from_env", lambda: isolated_pkb)
    doctor.check_knowledge_base()

    provenance_finding = _finding("embedding_provenance")
    assert provenance_finding is not None
    assert provenance_finding.ok is False
    assert provenance_finding.level == "required"

    # And the module-level exit-code decision (main()'s own logic, pinned
    # directly rather than re-running the whole checkup):
    degraded = any(
        not f.ok and f.level == "required"
        for f in doctor._FINDINGS
    )
    assert degraded is True, "a legacy 128-dim index must degrade doctor's exit code"


def test_no_index_yet_stays_info_only(isolated_pkb, monkeypatch):
    """A World with no pkb_pages index built yet (the legitimate clean-
    machine/first-boot state, A8) must NOT be a required finding -- only a
    provenance/dimension *disagreement* on an EXISTING table is. Since
    BLOCK-3's fix, doctor calls ensure_ready(build=False), which never
    calls index_all() at all here -- it sets the "empty" code and
    returns. This also means it makes zero embed_documents/embed_query
    calls and creates no .cache/lancedb, which
    test_doctor_never_builds_or_embeds below asserts directly."""
    monkeypatch.setattr("arail.pkb_index._pkb_root_from_env", lambda: isolated_pkb)

    doctor.check_knowledge_base()

    provenance_finding = _finding("embedding_provenance")
    assert provenance_finding is not None
    assert provenance_finding.ok is True, (
        "an as-yet-unbuilt index must not itself be a required "
        "(exit-3-degrading) finding")

    reachable_finding = _finding("embedding_reachable")
    assert reachable_finding is not None
    assert reachable_finding.level == "info"
    assert reachable_finding.ok is False  # "empty" -- there really is no index yet


def test_doctor_never_builds_or_embeds(isolated_pkb, monkeypatch):
    """REVIEW3.md BLOCK-3, required test: doctor.check_knowledge_base() on
    a World with no index yet must perform ZERO embed_documents calls and
    create NO .cache/lancedb. Reproduced on the operator's real `finance`
    World: a doctor sweep built it a 41-row index via the default
    ensure_ready(build=True) path -- a diagnostic must never be the thing
    that builds, or pays for (in embed calls, or under LAB_MODE=hybrid, in
    egressed corpus text), the thing it is checking."""
    from arail.dbspec import embed as embed_mod

    notes = isolated_pkb / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    (notes / "a.md").write_text("# a\nsome real content\n")

    calls = []
    monkeypatch.setattr(embed_mod, "embed_documents", lambda texts: calls.append(texts) or [])
    monkeypatch.setattr("arail.pkb_index._pkb_root_from_env", lambda: isolated_pkb)

    db_path = isolated_pkb / ".cache" / "lancedb"
    assert not db_path.exists()

    doctor.check_knowledge_base()

    assert calls == [], "doctor must never call embed_documents"
    assert not db_path.exists(), "doctor must never create an index"


def test_doctor_read_only_on_existing_index_still_reports_correctly(isolated_pkb, monkeypatch):
    """build=False must not regress the "existing, healthy index" case --
    doctor still reports ok and makes zero embed calls when there's
    nothing to build in the first place."""
    import arail.pkb as pkb
    from arail.dbspec import embed as embed_mod

    (isolated_pkb / "notes").mkdir(parents=True, exist_ok=True)
    (isolated_pkb / "notes" / "a.md").write_text("# a\n")
    pkb.index_all(pkb_root=isolated_pkb, include_docs=False)  # stubbed embedder, real build

    calls = []
    monkeypatch.setattr(embed_mod, "embed_documents", lambda texts: calls.append(texts) or [])
    monkeypatch.setattr("arail.pkb_index._pkb_root_from_env", lambda: isolated_pkb)

    doctor.check_knowledge_base()

    assert calls == [], "doctor must never call embed_documents even when a rebuild would be valid"
    provenance_finding = _finding("embedding_provenance")
    assert provenance_finding.ok is True
