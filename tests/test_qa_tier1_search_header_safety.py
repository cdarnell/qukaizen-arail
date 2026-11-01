"""QA (2026-08-08-arail2-tier1-integration): ``X-Retrieval-Reason`` is a
header built from an exception message, and part of that message is
supplied by the embedding provider.

``embed._post`` puts up to 400 bytes of the provider's HTTP error body
into ``EmbeddingError``'s message; ``_semantic_search`` stores that string
verbatim via ``set_degraded("provider", str(e))``; ``api_pkb_search``
reflects it into a response header. So the header value is
provider-controlled data on a security-relevant surface (response-header
injection / response splitting), and the provider is whatever
``MODEL_API_BASE`` points at — which in ``hybrid`` mode may be off-box.

These tests pin that the framework refuses to emit a header carrying CR/LF
rather than splitting the response, and that the value stays bounded.
"""
from __future__ import annotations

import asyncio
import importlib

import pytest

from arail.portal import app as portal_app


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def _qa_pkb_reset():
    import arail.pkb_index as pki
    pki._reset_for_tests()
    yield
    pki._reset_for_tests()


HOSTILE_REASONS = [
    ("crlf", "boom\r\nX-Injected: yes\r\n\r\n<html>owned</html>"),
    ("lf", "boom\nSet-Cookie: session=stolen"),
    ("cr", "boom\rLocation: http://evil.example"),
    ("nul", "boom\x00hidden"),
    ("non-latin1", "boom — the provider said ¡nope! \U0001f4a5"),
]


@pytest.mark.parametrize("label,reason", HOSTILE_REASONS,
                         ids=[lbl for lbl, _ in HOSTILE_REASONS])
def test_a_hostile_provider_message_cannot_split_the_response(
        monkeypatch, label, reason):
    """Response splitting itself is blocked one layer down: both uvicorn
    transports (``h11_impl`` and ``httptools_impl``) reject a header value
    containing CR/LF before it reaches the wire. Verified directly against
    ``h11.Connection.send`` and ``uvicorn...httptools_impl.HEADER_VALUE_RE``.

    So this test is about the layer that *should* have caught it: the app
    still constructs such a header, which turns a degraded-but-usable
    search into a serialization error (a self-inflicted 500 on
    ``/api/pkb/search``, remotely triggerable in ``LAB_MODE=hybrid`` where
    the provider is off-box). Sanitising at the point of construction is
    the fix — see TEST_REPORT.md QA-4, fixed alongside QA-5 via
    ``app._header_safe``.
    """
    monkeypatch.setattr(portal_app, "pkb_search", lambda q: [])
    monkeypatch.setattr("arail.pkb.retrieval_status", lambda: (False, reason))

    result = _run(portal_app.api_pkb_search(q="hello"))

    value = result.headers.get("X-Retrieval-Reason", "")
    for forbidden in ("\r", "\n", "\x00"):
        assert forbidden not in value, (
            f"{forbidden!r} survived into the response header — "
            f"a provider-controlled string must not be able to add headers")
    assert "X-Injected" not in dict(result.headers)
    assert "Set-Cookie" not in dict(result.headers)


def _world(tmp_path, monkeypatch):
    """A scratch PKB root, same shape as test_qa_tier1_buddy_retrieval's."""
    monkeypatch.setenv("LAB_PKB", str(tmp_path))
    import arail.config
    import arail.pkb as pkb
    importlib.reload(arail.config)
    importlib.reload(pkb)
    notes = tmp_path / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    (notes / "a.md").write_text("# a\nsome content\n")
    return tmp_path


def _trigger_dimension(root):
    """Legacy 128-dim table, no sidecar -- byte-for-byte the state of four
    of the operator's five real Worlds. ``pkb.search`` (ungated) hits the
    real ``check_read_path_health`` dimension check."""
    import lancedb  # type: ignore[import-not-found]
    from arail.vector_index import hash_embedding
    import arail.pkb as pkb

    db_path = root / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[
        {"path": "notes/a.md", "name": "a.md",
         "vector": hash_embedding("a"), "mtime": 0.0, "source_kind": "user"},
    ], mode="overwrite")
    pkb.search("anything", root)


def _trigger_provenance_missing(root):
    """Correct dimension, no provenance sidecar -- ``pkb.search`` hits the
    real ``_check_provenance`` "no provenance record" branch."""
    import lancedb  # type: ignore[import-not-found]
    from arail.dbspec.generated.models_registry import EMBEDDING_DIM
    import arail.pkb as pkb

    db_path = root / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[
        {"path": "notes/a.md", "name": "a.md",
         "vector": [0.0] * EMBEDDING_DIM, "mtime": 0.0, "source_kind": "user"},
    ], mode="overwrite")
    pkb.search("anything", root)


def _trigger_empty_build_path(root):
    """No index at all -- ``ensure_ready(build=False)`` hits the real
    "index does not exist yet for this World" branch (the diagnostic /
    doctor path, distinct from the search-path empty message below)."""
    import arail.pkb_index as pki
    pki.ensure_ready(root, build=False)


def _trigger_empty_search_path(root):
    """No index at all -- ``pkb.search`` hits its own "KB index is empty
    for this world" branch (the search path, distinct from the
    ensure_ready/doctor message above)."""
    import arail.pkb as pkb
    pkb.search("anything", root)


REAL_DEGRADED_TRIGGERS = [
    ("dimension", _trigger_dimension),
    ("provenance-missing", _trigger_provenance_missing),
    ("empty-build-path", _trigger_empty_build_path),
    ("empty-search-path", _trigger_empty_search_path),
]


@pytest.mark.usefixtures("_qa_pkb_reset")
def test_the_real_clean_machine_provider_message_can_actually_be_served(
        monkeypatch, tmp_path):
    """QA-5, the state the builder's parameters do not reach.

    ``EmbeddingUnavailable``'s message is the one a friend sees while
    ``ollama pull nomic-embed-text`` is still running, and it is the only
    real reason that is **multi-line** rather than em-dashed — so it
    exercises the control-strip half of ``_header_safe`` on genuine
    product text rather than on a synthetic hostile payload. Driven
    through the real ``urllib`` path against a closed loopback port.
    """
    import socket
    import arail.dbspec.embed as E
    import arail.pkb as pkb
    import arail.pkb_index as pki
    from arail.dbspec.generated.models_registry import EMBEDDING_DIM
    from arail.vector_index import hash_embedding

    root = _world(tmp_path, monkeypatch)
    real_docs = E.embed_documents
    E.embed_documents = lambda texts: [
        hash_embedding(t, dim=EMBEDDING_DIM) for t in texts]
    try:
        pkb.index_all(pkb_root=root, include_docs=False)
    finally:
        E.embed_documents = real_docs

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    monkeypatch.setenv("MODEL_API_BASE", f"http://127.0.0.1:{port}")
    monkeypatch.delenv("LAB_MODE", raising=False)
    # Rebuild the real embed_query on top of the un-stubbed embed_texts
    # rather than reloading the module — a reload would mint a new
    # EmbeddingError class and break `except EmbeddingError` elsewhere.
    monkeypatch.setattr(E, "embed_query", lambda text: E.embed_texts(
        [text], prefix=E.embedding_model().query_prefix)[0])

    pki._reset_for_tests()
    pkb.search("anything", root)
    ok, reason = pkb.retrieval_status()
    assert ok is False
    assert "\n" in reason, (
        "the provider message is expected to be multi-line — that is what "
        "makes it exercise the control-strip half of the fix")
    assert "ollama pull" in reason

    monkeypatch.setattr(portal_app, "pkb_search",
                        lambda q: [{"path": "a.md", "source": "keyword"}])
    monkeypatch.setattr("arail.pkb.retrieval_status", lambda: (False, reason))
    result = _run(portal_app.api_pkb_search(q="hello"))

    value = result.headers["X-Retrieval-Reason"]
    assert not any(c in value for c in ("\r", "\n", "\x00"))
    assert "ollama pull nomic-embed-text" in value, (
        "the one command that fixes a clean machine must survive "
        "sanitisation and the 200-char truncation")


@pytest.mark.parametrize("code", [0x0b, 0x0c, 0x1b, 0x07, 0x7f])
def test_other_control_characters_are_also_removed(monkeypatch, code):
    """QA-7 — the residual of the QA-4/QA-5 fix, closed.

    ``_header_safe`` used to remove CR/LF/NUL by denylist and ASCII-fold
    everything above 0x7f. Every *other* C0 control passed through
    unchanged, and they are not inert: ``h11`` rejects VT (0x0b) and FF
    (0x0c), and uvicorn's httptools transport rejects 29 of the 31 — both
    verified directly. That was the identical 500 QA-5 was, reached by a
    different byte.

    Reachable the same way QA-4 is: ``embed._post`` splices up to 400
    bytes of the provider's raw HTTP error body into the message, and in
    ``LAB_MODE=hybrid`` that provider is off-box. An ANSI-coloured or
    non-JSON error page is enough.

    Fixed by switching to an allowlist (printable ASCII plus tab and the
    space a folded line break leaves behind), which closes the whole
    class instead of naming members of it one at a time.
    """
    monkeypatch.setattr(portal_app, "pkb_search", lambda q: [])
    monkeypatch.setattr("arail.pkb.retrieval_status",
                        lambda: (False, f"provider said: a{chr(code)}b"))
    result = _run(portal_app.api_pkb_search(q="hello"))
    value = result.headers["X-Retrieval-Reason"]
    assert chr(code) not in value


@pytest.mark.usefixtures("_qa_pkb_reset")
@pytest.mark.parametrize("label,trigger", REAL_DEGRADED_TRIGGERS,
                         ids=[lbl for lbl, _ in REAL_DEGRADED_TRIGGERS])
def test_the_real_degraded_messages_can_actually_be_served(
        monkeypatch, tmp_path, label, trigger):
    """QA-5 — the shipping defect.

    Every degraded reason ``pkb_index`` produces contains an EM DASH
    (U+2014). Starlette encodes response header values as latin-1, so
    building the ``X-Retrieval-Reason`` header from one raises
    ``UnicodeEncodeError`` and ``GET /api/pkb/search`` returns 500 instead
    of degraded-but-usable keyword results.

    That is the state of four of the operator's five real Worlds plus the
    root lab (legacy 128-dim, no sidecar), and of every clean machine that
    has not yet pulled ``nomic-embed-text``. Reproduced end to end against
    a scratch copy of the ``qukaizen`` World.

    ``tests/test_pkb_search_api_status.py`` missed it because its fixture
    reason was a hand-written ASCII string rather than one the product
    actually emits. Fixed here per REVIEW4/QA feedback: each parameter
    below *triggers the real code path* (a real legacy table, a real
    missing sidecar, a real empty index via each of the two distinct
    call sites that report it) and reads the reason back through
    ``retrieval_status()`` -- it is never hand-copied, so this test
    cannot drift out of sync with what ``pkb_index``/``pkb`` actually say,
    and a new degraded message some future change adds is caught the
    moment it's wired into ``retrieval_status()``.
    """
    import arail.pkb as pkb

    root = _world(tmp_path, monkeypatch)
    trigger(root)

    ok, reason = pkb.retrieval_status()
    assert ok is False, f"trigger {label!r} did not actually degrade retrieval_status()"
    assert "—" in reason, (
        f"trigger {label!r}'s real reason no longer contains an em dash -- "
        f"this test's whole point is exercising that hazard; got {reason!r}")

    monkeypatch.setattr(portal_app, "pkb_search",
                        lambda q: [{"path": "a.md", "source": "keyword"}])
    monkeypatch.setattr("arail.pkb.retrieval_status", lambda: (False, reason))

    result = _run(portal_app.api_pkb_search(q="hello"))
    assert result.headers["X-Retrieval-Status"] == "degraded"
    assert result.headers["X-Retrieval-Reason"]


def test_reason_header_is_length_bounded(monkeypatch):
    """A 400-byte provider body plus prose must not become an unbounded
    header; some proxies drop or error on large header sets."""
    monkeypatch.setattr(portal_app, "pkb_search", lambda q: [])
    monkeypatch.setattr("arail.pkb.retrieval_status", lambda: (False, "x" * 5000))

    result = _run(portal_app.api_pkb_search(q="hello"))
    assert len(result.headers["X-Retrieval-Reason"]) <= 200


def test_healthy_search_keeps_the_bare_list_contract(monkeypatch):
    """Regression: dashboard.html / agents.html / docs_hub.html all do
    ``r.json().forEach(...)``. A degraded lab must not change the body
    shape, only add headers."""
    import json

    payload = [{"path": "a.md", "name": "a.md", "match_count": 1,
                "snippets": [], "source": "semantic"}]
    monkeypatch.setattr(portal_app, "pkb_search", lambda q: payload)

    monkeypatch.setattr("arail.pkb.retrieval_status", lambda: (True, ""))
    assert _run(portal_app.api_pkb_search(q="a")) == payload

    monkeypatch.setattr("arail.pkb.retrieval_status", lambda: (False, "degraded"))
    degraded = _run(portal_app.api_pkb_search(q="a"))
    assert json.loads(bytes(degraded.body)) == payload, \
        "the JSON body must stay a bare list in both states"
