"""Coordinator finding (post-BLOCK-3, REVIEW3.md): a read-only
``ensure_ready(build=False)`` call must never satisfy, suppress, or
substitute for a later genuine ``ensure_ready(build=True)`` build.

Reproduced mechanism: ``ensure_ready``'s one-shot guard (``_initialized``)
used to be set unconditionally on EVERY call, regardless of ``build``. A
read-only call "used up" the guard without doing any work, so a later
build=True call in the same process (for the same root, or -- since the
guard was a single un-keyed global -- for ANY root) silently no-op'd.

Fix: the guard (`_initialized_roots`, a set keyed by resolved root) is now
claimed ONLY by a `build=True` call; `build=False` never reads or writes
it, so it always re-executes its (cheap, idempotent) read-only inspection
fresh, and can never block a genuine build for its own root or any other.

Reachability finding (stated plainly, not assumed): in the codebase as
shipped today, ``ensure_ready(build=False)`` has exactly one caller,
``doctor.check_knowledge_base()``, invoked only via a fresh
``python -m arail.doctor`` subprocess (``arailctl``'s dispatch). Module
globals do not survive across processes, so THIS specific sequence
(build=False then build=True in one process) is not reachable through any
currently-shipped call path -- doctor's process and the portal's process
never share this module's state. It is nonetheless a real contract defect
in `ensure_ready` itself (the function's own docstring invites exactly
this usage: "call once at portal startup, or from any genuine
content-write path", with no stated restriction against also being called
read-only), and it is the kind of landmine a future in-process caller
(an admin endpoint, a refactor that moves doctor's logic in-process)
would hit silently. Fixed regardless of today's reachability, per the
coordinator's explicit instruction.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def pkb_index_reset():
    import arail.pkb_index as pki
    pki._reset_for_tests()
    yield
    pki._reset_for_tests()


def _make_root(tmp_path: Path, name: str, *, n: int = 1) -> Path:
    root = tmp_path / name
    notes = root / "notes"
    notes.mkdir(parents=True)
    for i in range(n):
        (notes / f"doc{i}.md").write_text(f"# doc {i}\ncontent {i}\n")
    return root


def _has_index(root: Path) -> bool:
    import lancedb  # type: ignore[import-not-found]
    import arail.pkb_index as pki
    db_path = pki._vector_db_path(root)
    if not db_path.exists():
        return False
    try:
        db = lancedb.connect(str(db_path))
        from arail.vector_index import VectorIndex
        if "pkb_pages" not in VectorIndex._existing_tables(db):
            return False
        return db.open_table("pkb_pages").count_rows() > 0
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------
# Required test 1 — the exact reproduction: read-only then build
# --------------------------------------------------------------------------

def test_readonly_then_build_still_builds(tmp_path):
    import arail.pkb_index as pki

    root = _make_root(tmp_path, "root")
    assert not _has_index(root)

    pki.ensure_ready(root, build=False)   # doctor-style read-only check
    assert not _has_index(root), "read-only must not build anything"

    pki.ensure_ready(root, build=True)    # a genuine content-write path
    assert _has_index(root), (
        "a read-only check must never suppress a later genuine build "
        "for the SAME root")


# --------------------------------------------------------------------------
# Required test 2 — the reverse order still works
# --------------------------------------------------------------------------

def test_build_then_readonly_still_reports_correctly(tmp_path):
    import arail.pkb_index as pki

    root = _make_root(tmp_path, "root")

    pki.ensure_ready(root, build=True)
    assert _has_index(root)

    ok_before, _ = pki.embedding_status()
    assert ok_before is True

    pki.ensure_ready(root, build=False)
    assert _has_index(root), "a read-only check afterward must not undo the build"
    ok_after, _ = pki.embedding_status()
    assert ok_after is True


# --------------------------------------------------------------------------
# Required test 3 — two different roots in one process
# --------------------------------------------------------------------------

def test_readonly_check_on_root_a_does_not_affect_build_on_root_b(tmp_path):
    import arail.pkb_index as pki

    root_a = _make_root(tmp_path, "world_a")
    root_b = _make_root(tmp_path, "world_b")

    pki.ensure_ready(root_a, build=False)   # read-only check on A
    assert not _has_index(root_a)
    assert not _has_index(root_b)

    pki.ensure_ready(root_b, build=True)    # genuine build on B
    assert _has_index(root_b), (
        "a read-only check on root A must never suppress a build on a "
        "DIFFERENT root B")
    assert not _has_index(root_a), "root A must remain untouched (still no index)"


def test_build_on_root_a_then_build_on_root_b_both_succeed(tmp_path):
    """Sanity: the per-root guard must not falsely suppress a second,
    genuinely-different root's first build=True call (i.e. the guard
    really is keyed by root, not just "has any build=True happened yet
    in this process")."""
    import arail.pkb_index as pki

    root_a = _make_root(tmp_path, "world_a")
    root_b = _make_root(tmp_path, "world_b")

    pki.ensure_ready(root_a, build=True)
    assert _has_index(root_a)

    pki.ensure_ready(root_b, build=True)
    assert _has_index(root_b), "a distinct root's first build=True must still build"


def test_second_build_true_call_on_same_root_is_a_no_op_as_before(tmp_path):
    """The pre-existing "call once per process" contract for build=True
    on the SAME root must be preserved -- this is not a regression test
    for the fix, it's a guard that the fix didn't overcorrect into
    "always build=True runs fully every time"."""
    import arail.pkb_index as pki
    import arail.pkb as pkb_mod

    root = _make_root(tmp_path, "root")
    pki.ensure_ready(root, build=True)

    calls = []
    monkeypatch_target = pkb_mod.index_all
    pkb_mod.index_all = lambda *a, **k: calls.append(1)
    try:
        pki.ensure_ready(root, build=True)
    finally:
        pkb_mod.index_all = monkeypatch_target

    assert calls == [], "a second build=True call for the same root must remain a no-op"


# --------------------------------------------------------------------------
# Required test 4 — doctor still performs zero embeds, creates no index
# --------------------------------------------------------------------------

def test_doctor_still_zero_embeds_and_no_index_after_the_fix(tmp_path, monkeypatch):
    """BLOCK-3 must not regress while fixing this: doctor.check_knowledge_
    base() must still perform zero embed_documents calls and create no
    .cache/lancedb."""
    import importlib
    import arail.config
    import arail.pkb as pkb
    import arail.doctor as doctor
    from arail.dbspec import embed as embed_mod

    monkeypatch.setenv("LAB_PKB", str(tmp_path / "pkb"))
    importlib.reload(arail.config)
    importlib.reload(pkb)
    pkb.scaffold(tmp_path / "pkb")

    (tmp_path / "pkb" / "notes").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pkb" / "notes" / "a.md").write_text("# a\n")

    calls = []
    monkeypatch.setattr(embed_mod, "embed_documents", lambda texts: calls.append(texts) or [])
    monkeypatch.setattr("arail.pkb_index._pkb_root_from_env", lambda: tmp_path / "pkb")

    doctor._FINDINGS.clear()
    doctor.check_knowledge_base()

    assert calls == [], "doctor must never call embed_documents"
    assert not (tmp_path / "pkb" / ".cache" / "lancedb").exists(), (
        "doctor must never create an index")


# --------------------------------------------------------------------------
# "Also re-check": does a read-only degraded code on root A mislabel root B?
# --------------------------------------------------------------------------

def test_degraded_empty_code_from_root_a_readonly_check_leaks_into_root_b_status(tmp_path):
    """Documents (does not fix) a PRE-EXISTING, already-filed limitation:
    pkb_index's degraded-code dict (_degraded_codes) is process-global,
    not per-root -- this predates the build=False addition entirely (a
    search on root A setting "dimension" would already have mislabelled a
    status check for root B, with or without BLOCK-3). This test proves
    the "empty" code from a build=False check on an EMPTY root A does
    leak into embedding_status() read right after for root B, even though
    root B's own index is healthy and non-empty. Filed in
    sprints/BACKLOG.md as the already-tracked "module-global degraded
    state vs per-World roots" debt; not fixed in this pass -- see
    BUILD_LOG for the reachability/scope discussion (both real callers,
    doctor and the portal, only ever handle one root per process today,
    so this is not live-reachable through any shipped call path either,
    same as the _initialized bug before its fix)."""
    import arail.pkb_index as pki
    import arail.pkb as pkb

    root_a = _make_root(tmp_path, "world_a")  # left empty of an index
    root_b = _make_root(tmp_path, "world_b")
    pkb.index_all(pkb_root=root_b, include_docs=False)  # root B genuinely healthy

    pki.ensure_ready(root_a, build=False)  # sets "empty" -- root A has no index
    ok_a, _ = pki.embedding_status()
    assert ok_a is False

    # Root B's OWN table is fine, but the global embedding_status() still
    # reports root A's leftover "empty" code -- this is the known,
    # pre-existing, filed cross-root state-sharing limitation, reproduced
    # here explicitly rather than left implicit.
    ok_b_as_reported, _ = pki.embedding_status()
    assert ok_b_as_reported is False, (
        "documents the known limitation: global degraded state does not "
        "distinguish which root a code applies to")


# --------------------------------------------------------------------------
# REVIEW4.md ASK-1 — a read-only call must not redirect _pkb_root_cache
# --------------------------------------------------------------------------
#
# The ORCH-1 fix (per-root `_initialized_roots`) left `_pkb_root_cache =
# root` OUTSIDE the `if build:` guard, one line below. Verified by the
# reviewer: `ensure_ready(E, build=True)` then `ensure_ready(F,
# build=False)` left `_pkb_root_cache == F`. Because `_flush()` resolves
# `root = _pkb_root_cache` and joins it against `_pending`'s ROOT-RELATIVE
# paths, a redirected cache is a cross-World data WRITE (or delete) --
# strictly worse than ORCH-1's silent no-op. Fixed by moving the
# assignment inside `if build:`; these tests pin it.

def test_readonly_call_does_not_redirect_pkb_root_cache(tmp_path):
    import arail.pkb_index as pki

    root_e = _make_root(tmp_path, "world_e")
    root_f = _make_root(tmp_path, "world_f")

    pki.ensure_ready(root_e, build=True)
    with pki._lock:
        cache_after_e = pki._pkb_root_cache
    assert cache_after_e == root_e

    pki.ensure_ready(root_f, build=False)   # doctor-style read-only check on F
    with pki._lock:
        cache_after_f_readonly = pki._pkb_root_cache
    assert cache_after_f_readonly == root_e, (
        "a read-only call on a DIFFERENT root must not redirect the "
        "process-wide flush target (_pkb_root_cache)")


def test_readonly_call_on_same_root_does_not_disturb_cache(tmp_path):
    """Same-root read-only calls are the common case (doctor checking the
    World that's also running in the portal, in a hypothetical shared
    process) -- must be a true no-op on the cache, not just a no-op that
    happens to reassign the same value."""
    import arail.pkb_index as pki

    root = _make_root(tmp_path, "world")
    pki.ensure_ready(root, build=True)

    # Corrupt the cache deliberately to a sentinel so we can tell whether
    # the read-only call touches it AT ALL (reassigning the same root
    # would hide a bug where the guard was removed but the line kept).
    sentinel = object()
    with pki._lock:
        pki._pkb_root_cache = sentinel  # type: ignore[assignment]

    pki.ensure_ready(root, build=False)

    with pki._lock:
        assert pki._pkb_root_cache is sentinel, (
            "build=False must not write _pkb_root_cache at all, even for "
            "its own root")


def test_cross_world_contamination_probe(tmp_path):
    """QA attack-list item 8 / REVIEW4.md's own suggested reproduction:
    in one process, build World A, schedule an upsert under A with no
    explicit pkb_root (so it resolves via _pkb_root_cache), then run a
    read-only check on World B, then flush. The row must land in A, not
    B -- if ASK-1 were NOT fixed, the read-only check on B would have
    redirected the flush target and this row would land under B instead
    (or fail to resolve a real file at all, since _pending holds paths
    relative to A)."""
    import arail.pkb_index as pki

    root_a = _make_root(tmp_path, "world_a")
    root_b = _make_root(tmp_path, "world_b")

    pki.ensure_ready(root_a, build=True)  # sets _pkb_root_cache to A

    new_file = root_a / "notes" / "new_after_build.md"
    new_file.write_text("# new\nafter the initial build\n")
    pki.schedule_upsert(new_file, pkb_root=root_a)  # explicit root -- but exercises _pending

    pki.ensure_ready(root_b, build=False)  # doctor-style read-only check on B, in between

    pki.flush_now()  # resolves root via _pkb_root_cache internally

    import lancedb  # type: ignore[import-not-found]
    db_a = lancedb.connect(str(pki._vector_db_path(root_a)))
    table_a = db_a.open_table("pkb_pages")
    paths_a = set(table_a.to_pandas()["path"].tolist())
    assert "notes/new_after_build.md" in paths_a, (
        "the flush must land the new row under World A, not be lost or "
        "misdirected by the intervening read-only check on World B")

    # World B must remain completely untouched -- no table, no directory.
    assert not pki._vector_db_path(root_b).exists(), (
        "a read-only check on B followed by a flush targeting A must "
        "never create or write anything under B")
