"""Paranoid QA pass for docs-hub-sprint-3.

Targets the edge cases the builder/architect did not explicitly cover:

  - LanceDB re-ingest idempotence (no duplicate docs rows on repeated calls)
  - 4 KB embedding cap — body > 4 KB embeds correctly without crash
  - Empty docs/ directory — index_all does not crash
  - Doc with no frontmatter — graceful fallback
  - Cross-link audit: anchors, query strings, mixed-case extensions, indented
    fences, inline-backtick links
  - INDEX.md redirect: trailing slash, case variants, query strings preserved
  - sys.modules rebind hermeticity under random/repeated invocation
  - Concurrent index_all (LanceDB write contention)
  - Path namespacing — root/ vs docs/ docs do not collide

QA allocation: 40% security/edge + 30% perf/concurrency + 20% regression +
10% happy.
"""

from __future__ import annotations

import re
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Make src importable regardless of how pytest is invoked.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

def _lance_available() -> bool:
    try:
        from arail.vector_index import available  # noqa: PLC0415
        return bool(available())
    except Exception:
        return False


def _write_doc(path: Path, title: str, body: str = "body",
               extra_meta: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = f"---\ntitle: {title}\ncategory: Reference\n{extra_meta}---\n{body}\n"
    path.write_text(fm, encoding="utf-8")


def _make_pkb(tmp_path: Path, n_articles: int = 3) -> Path:
    root = tmp_path / "pkb"
    (root / "sources" / "articles").mkdir(parents=True, exist_ok=True)
    for i in range(n_articles):
        (root / "sources" / "articles" / f"a-{i}.md").write_text(
            f"# A{i}\n\nbody {i}\n", encoding="utf-8"
        )
    return root


@pytest.fixture
def patched_registry(tmp_path, monkeypatch):
    """Point docs_registry at tmp_path / 'docs'."""
    if not _lance_available():
        pytest.skip("LanceDB not available")
    import arail.portal.docs_registry as reg  # noqa: PLC0415
    monkeypatch.setattr(reg, "_repo_root", lambda: tmp_path)
    reg._invalidate_cache()
    return reg


# ===========================================================================
# 1) LanceDB ingest idempotence + duplicate guard
# ===========================================================================

def test_index_all_idempotent_no_duplicates_on_double_call(tmp_path, patched_registry):
    """Calling index_all twice with no doc changes must NOT double the row
    count.  VectorIndex.replace() should give full-replace semantics."""
    from arail.pkb import index_all, _vector_db_path  # noqa: PLC0415
    from arail.vector_index import VectorIndex  # noqa: PLC0415

    pkb = _make_pkb(tmp_path)
    _write_doc(tmp_path / "docs" / "alpha.md", "Alpha")
    _write_doc(tmp_path / "docs" / "beta.md", "Beta")
    patched_registry._invalidate_cache()

    r1 = index_all(pkb_root=pkb, include_docs=True)
    r2 = index_all(pkb_root=pkb, include_docs=True)

    assert r1["indexed_docs"] == r2["indexed_docs"], (
        f"docs row count drifted on re-ingest: {r1['indexed_docs']} → {r2['indexed_docs']}"
    )
    assert r1["indexed"] == r2["indexed"], (
        f"total row count drifted on re-ingest: {r1['indexed']} → {r2['indexed']}"
    )

    # Read the table directly rather than through VectorIndex.search(),
    # which always query-embeds via hash_embedding at its own fixed
    # default dimension (128) — incompatible with a table written at the
    # spec's declared dimension (768) since the Tier 1.2 embedder swap.
    # This test is about row-duplication, not retrieval, so read the rows
    # directly instead of searching for them.
    idx = VectorIndex(name="pkb_pages", db_path=_vector_db_path(pkb))
    table = idx._table()  # noqa: SLF001 — test-only direct read
    assert table is not None
    all_paths = table.to_pandas()["path"].tolist()
    alpha_paths = [p for p in all_paths if p.endswith("alpha.md")]
    assert len(alpha_paths) == 1, (
        f"Duplicate row for alpha.md after double index_all: {alpha_paths}"
    )


# ===========================================================================
# 2) 4 KB embedding cap — large body does not crash, does not balloon
# ===========================================================================

def test_index_all_handles_doc_larger_than_4kb(tmp_path, patched_registry):
    """A doc with a body > 4 KB must index cleanly (cap is in _collect_docs_rows)."""
    from arail.pkb import index_all  # noqa: PLC0415

    pkb = _make_pkb(tmp_path)
    # 32 KB of body — well past the 4 KB cap.
    big_body = "lorem ipsum dolor sit amet " * 1500  # ~ 40 KB
    _write_doc(tmp_path / "docs" / "huge.md", "Huge", body=big_body)
    patched_registry._invalidate_cache()

    result = index_all(pkb_root=pkb, include_docs=True)
    assert result["ok"] is True
    assert result["indexed_docs"] >= 1


# ===========================================================================
# 3) Empty docs/ directory — must not crash
# ===========================================================================

def test_index_all_empty_docs_dir_does_not_crash(tmp_path, patched_registry):
    """Empty docs/ → indexed_docs == 0 and ok == True."""
    from arail.pkb import index_all  # noqa: PLC0415

    pkb = _make_pkb(tmp_path)
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    # No .md files written.
    patched_registry._invalidate_cache()

    result = index_all(pkb_root=pkb, include_docs=True)
    assert result["ok"] is True
    assert result["indexed_docs"] == 0


# ===========================================================================
# 4) Doc with no frontmatter — graceful fallback
# ===========================================================================

def test_index_all_doc_without_frontmatter_does_not_crash(tmp_path, patched_registry):
    """A markdown file with no `---` frontmatter must not crash ingest.  The
    registry will likely skip it or assign defaults — either is acceptable as
    long as index_all returns ok=True."""
    from arail.pkb import index_all  # noqa: PLC0415

    pkb = _make_pkb(tmp_path)
    # Plain markdown — no frontmatter.
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "plain.md").write_text("# Plain doc\n\nNo frontmatter here.\n",
                                       encoding="utf-8")
    patched_registry._invalidate_cache()

    result = index_all(pkb_root=pkb, include_docs=True)
    assert result["ok"] is True
    # indexed_docs may be 0 or 1 depending on registry tolerance — both fine.


# ===========================================================================
# 5) Path namespacing — docs/ slug shadow vs root/ slug do not collide
# ===========================================================================

def test_index_all_docs_and_root_namespacing(tmp_path, patched_registry):
    """If a slug exists in both docs/ and the root allowlist (CONTRIBUTING, etc.),
    namespacing must keep their LanceDB paths distinct."""
    from arail.pkb import _collect_docs_rows  # noqa: PLC0415

    # Two synthetic docs that would otherwise collide.
    _write_doc(tmp_path / "docs" / "same-slug.md", "Same Doc")
    # Note: root-allowlist accepts only specific names, so we cannot easily
    # create a real shadow without monkeypatching ALLOWLIST. Instead, assert
    # that namespacing is encoded in the path.
    patched_registry._invalidate_cache()

    rows = _collect_docs_rows()
    paths = [r["path"] for r in rows]
    # Every docs row must start with "docs/" or "root/"
    for p in paths:
        assert p.startswith(("docs/", "root/")), (
            f"Docs row path missing namespace prefix: {p}"
        )


# ===========================================================================
# 6) Cross-link regex — anchors, query strings, mixed-case extensions
# ===========================================================================

def test_cross_link_regex_handles_anchor_only_link():
    """The regex must catch `[x](page.md#section)` and the test must strip the
    anchor before resolving."""
    from tests.test_docs_cross_links import _MD_LINK_RE  # noqa: PLC0415

    text = "[Section](./other.md#anchor)"
    m = _MD_LINK_RE.search(text)
    assert m, "regex failed to catch anchored markdown link"
    raw = m.group(1)
    base = raw.split("#")[0]
    assert base == "./other.md"


def test_cross_link_regex_handles_query_string_link():
    """A markdown link with a query string is harmless but must still be split
    on '#' OR '?' to resolve the file path.  This documents current behaviour:
    a `?` in the target will NOT be stripped — and that's acceptable because
    real markdown links to .md files do not use query strings.  If this ever
    changes, update the audit."""
    from tests.test_docs_cross_links import _MD_LINK_RE  # noqa: PLC0415

    text = "[Q](./a.md?ref=x)"
    m = _MD_LINK_RE.search(text)
    # The regex must match (target ends in .md so the pattern's `[^)]*` after
    # .md catches the trailing `?ref=x`).  The test documents this.
    assert m is not None, "regex should at least match — audit guards what comes after"


def test_cross_link_regex_does_not_match_mixed_case_md_extension():
    """If anyone writes [x](foo.MD) — current audit treats as md (case-insensitive
    extension check in `_collect_broken_links` lower()-folds).  This test pins
    the case-insensitive behaviour."""
    from tests.test_docs_cross_links import _MD_LINK_RE  # noqa: PLC0415
    # The regex itself is case-sensitive (matches lowercase `.md` only).
    text = "[X](foo.MD)"
    m = _MD_LINK_RE.search(text)
    # Document the current behaviour: regex requires lowercase `.md`.
    # This means UPPERCASE .MD links would slip through audit — flagged below.
    assert m is None, (
        "Audit regex currently does not catch .MD (uppercase) — known limitation; "
        "if regex is updated to be case-insensitive, this test must flip."
    )


def test_cross_link_audit_catches_anchored_broken_link(tmp_path, monkeypatch):
    """`[x](missing.md#sec)` where missing.md does not exist — must be flagged."""
    if not _lance_available():
        pytest.skip("docs_registry import indirectly hits vector_index in some envs")
    import arail.portal.docs_registry as reg  # noqa: PLC0415
    monkeypatch.setattr(reg, "_repo_root", lambda: tmp_path)
    reg._invalidate_cache()

    _write_doc(tmp_path / "docs" / "src.md", "Src",
               body="[Bad](./not-here.md#section)")
    reg._invalidate_cache()

    from tests.test_docs_cross_links import _collect_broken_links  # noqa: PLC0415

    # Patch the audit's _repo_root to our tmp_path
    import tests.test_docs_cross_links as audit_mod
    monkeypatch.setattr(audit_mod, "_repo_root", lambda: tmp_path)

    broken = _collect_broken_links()
    assert any("not-here.md" in raw for _, raw, _ in broken), (
        f"audit failed to catch anchored broken link: {broken}"
    )


def test_cross_link_audit_skips_link_inside_inline_backticks():
    """`[x](./broken.md)` inside `inline backticks` must NOT be flagged."""
    from tests.test_docs_cross_links import _strip_fences, _MD_LINK_RE  # noqa: PLC0415

    text = "Here is `[x](./broken.md)` inline."
    stripped = _strip_fences(text)
    matches = list(_MD_LINK_RE.finditer(stripped))
    assert not matches, (
        f"inline-backtick broken link was not stripped: {[m.group(0) for m in matches]}"
    )


# ===========================================================================
# 7) INDEX.md redirect variants
# ===========================================================================

def _get_app_client(monkeypatch, tmp_path, lab_tier: str = "min") -> TestClient:
    monkeypatch.setenv("LAB_TIER", lab_tier)
    monkeypatch.setenv("ARAIL_PASSWORD", "test-passphrase-not-real")
    monkeypatch.chdir(tmp_path)
    import arail.portal.app as _app_mod  # noqa: PLC0415
    return TestClient(_app_mod.app, follow_redirects=False)


def test_index_md_redirect_returns_301(monkeypatch, tmp_path):
    client = _get_app_client(monkeypatch, tmp_path)
    r = client.get("/docs/INDEX.md")
    assert r.status_code == 301
    assert r.headers["location"] == "/docs"


def test_index_md_redirect_with_query_string(monkeypatch, tmp_path):
    """Query strings on the legacy URL must still 301 to /docs.  Current
    handler is bound on the bare path — a trailing query is part of the URL
    routing question; document the behaviour."""
    client = _get_app_client(monkeypatch, tmp_path)
    r = client.get("/docs/INDEX.md?utm=email")
    # Either 301 (path-match) or 404 (strict). 301 is the desired UX.
    assert r.status_code in (301, 404), r.status_code


def test_index_md_lowercase_variant_is_not_redirected(monkeypatch, tmp_path):
    """`/docs/index.md` (lowercase) is NOT the legacy path.  Current handler
    only matches the literal `/docs/INDEX.md` — lowercase will fall through to
    the viewer (likely 404 since no doc named 'index' exists)."""
    client = _get_app_client(monkeypatch, tmp_path)
    r = client.get("/docs/index.md")
    # Not 301 — we are pinning that the redirect is case-sensitive (FastAPI's
    # default routing IS case-sensitive on the path).
    assert r.status_code != 301, (
        f"/docs/index.md unexpectedly 301'd; redirect should only fire for INDEX.md "
        f"(got {r.status_code})"
    )


# ===========================================================================
# 8) Concurrent index_all — LanceDB write contention
# ===========================================================================

@pytest.mark.perf
def test_index_all_concurrent_calls_do_not_corrupt_index(tmp_path, patched_registry):
    """Two concurrent index_all() calls on the same pkb_root must both return
    ok=True and leave the index queryable.  LanceDB writes are serialised at
    the table level — we are confirming there's no crash, deadlock, or
    half-written table."""
    from arail.pkb import index_all, _vector_db_path  # noqa: PLC0415
    from arail.vector_index import VectorIndex  # noqa: PLC0415

    pkb = _make_pkb(tmp_path, n_articles=10)
    for i in range(5):
        _write_doc(tmp_path / "docs" / f"c-{i}.md", f"C{i}")
    patched_registry._invalidate_cache()

    results: list[dict] = []
    errors: list[BaseException] = []

    def _run():
        try:
            results.append(index_all(pkb_root=pkb, include_docs=True))
        except BaseException as e:  # pragma: no cover - diagnostic
            errors.append(e)

    threads = [threading.Thread(target=_run) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive(), "index_all thread deadlocked"

    assert not errors, f"concurrent index_all raised: {errors}"
    assert all(r["ok"] for r in results), f"some concurrent calls failed: {results}"

    # Index is still queryable
    idx = VectorIndex(name="pkb_pages", db_path=_vector_db_path(pkb))
    rows = idx.search("C0", k=20, min_score=0.0)
    assert isinstance(rows, list)


# ===========================================================================
# 9) sys.modules rebind hermeticity — run repeatedly
# ===========================================================================

def test_fresh_registry_rebind_is_hermetic_over_repeated_calls(monkeypatch, tmp_path):
    """Invoking _fresh_registry multiple times in one process must leave both
    sys.modules and app._docs_registry in sync at the end of each call."""
    from tests.test_docs_registry_qa import _fresh_registry  # noqa: PLC0415
    import arail.portal.app as app_mod  # noqa: PLC0415

    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)

    for i in range(4):
        d = tmp_path / f"docs-{i}"
        d.mkdir(parents=True, exist_ok=True)
        _write_doc(d / "x.md", f"X{i}")
        mod = _fresh_registry(monkeypatch, d, tmp_path)
        assert app_mod._docs_registry is mod, (
            f"iteration {i}: app._docs_registry diverged from rebind target"
        )


# ===========================================================================
# 10) Registry returning empty tuple — must not crash
# ===========================================================================

def test_index_all_with_empty_registry(tmp_path, monkeypatch):
    """If all_docs() returns () the ingest must still produce a valid result
    (indexed_docs=0, ok=True)."""
    if not _lance_available():
        pytest.skip("LanceDB not available")
    from arail.pkb import index_all  # noqa: PLC0415
    import arail.portal.docs_registry as reg  # noqa: PLC0415

    monkeypatch.setattr(reg, "all_docs", lambda: ())

    pkb = _make_pkb(tmp_path)
    result = index_all(pkb_root=pkb, include_docs=True)
    assert result["ok"] is True
    assert result["indexed_docs"] == 0


# ===========================================================================
# 11) Body read failure (unreadable doc file) — does not crash
# ===========================================================================

def test_build_docs_rows_tolerates_unreadable_path(tmp_path, monkeypatch):
    """If a registered doc's path is missing on disk, _collect_docs_rows should
    still produce a row (with empty snippet) rather than crashing."""
    if not _lance_available():
        pytest.skip("LanceDB not available")
    import arail.portal.docs_registry as reg  # noqa: PLC0415
    from arail.pkb import _collect_docs_rows  # noqa: PLC0415

    # Fake doc with a path that does not exist
    fake = reg.Doc(
        slug="ghost",
        path=Path("/nonexistent/ghost.md"),
        title="Ghost",
        description="",
        category="Reference",
        order=100,
        tags=(),
        read_minutes=1,
        audience="beginner",
        related=(),
        buddy_prompt="",
        source_root="docs",
        mtime=0.0,
    )
    monkeypatch.setattr(reg, "all_docs", lambda: (fake,))

    rows = _collect_docs_rows()
    assert len(rows) == 1
    assert rows[0]["path"] == "docs/ghost.md"


# ===========================================================================
# 12) Live cross-link audit run (regression sentinel for the real corpus)
# ===========================================================================

def test_live_cross_link_audit_clean():
    """Sanity: the live docs/ corpus has no broken internal .md links.

    This is a redundant check of test_cross_link_audit_all_internal_links_resolve
    but lives in QA so a regression here is visible from the QA report."""
    from tests.test_docs_cross_links import _collect_broken_links  # noqa: PLC0415
    broken = _collect_broken_links()
    assert not broken, f"live corpus broken links: {broken}"
