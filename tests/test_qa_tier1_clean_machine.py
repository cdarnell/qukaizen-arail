"""QA (2026-08-08-arail2-tier1-integration): the machine that has no
embedding model yet.

30% of arail's QA budget is setup-on-a-clean-machine. ``setup.sh`` pulls
``nomic-embed-text`` warn-and-continue, so "Ollama up but the embedding
model absent" and "no Ollama at all" are both states a friend's laptop
will really be in. The contract (C1/C5/FM15) is: fail loudly, write zero
vectors, leave any existing index exactly as it was, and never substitute
a different embedding.

These tests deliberately un-stub the autouse fake embedder (see
``tests/conftest.py::_stub_embedding_provider``) and drive the real
``urllib`` path against a closed loopback port, because the thing being
tested is precisely what happens when that path fails.
"""
from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def _reset():
    import arail.pkb_index as pki
    pki._reset_for_tests()
    yield
    pki._reset_for_tests()


@pytest.fixture
def unreachable_provider(monkeypatch):
    """Restore the real embed implementation and aim it at a closed port.

    ``tests/conftest.py``'s autouse fixture replaces ``embed_documents`` /
    ``embed_query`` / ``embed`` with a network-free fake; these tests need
    the real ones. It does **not** stub ``embed_texts``, so the real
    behaviour is rebuilt on top of that rather than by
    ``importlib.reload`` — a reload would mint a new ``EmbeddingError``
    class, and ``pkb_index``'s call-time ``from ... import EmbeddingError``
    would then stop matching the one other test modules imported at
    collection time, silently downgrading C1's LOUD branch to the generic
    SKIP branch for the rest of the session.
    """
    import arail.dbspec.embed as E

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    monkeypatch.setenv("MODEL_API_BASE", f"http://127.0.0.1:{port}")
    monkeypatch.delenv("LAB_MODE", raising=False)
    monkeypatch.setattr(E, "embed_documents", lambda texts: E.embed_texts(
        texts, prefix=E.embedding_model().document_prefix))
    monkeypatch.setattr(E, "embed_query", lambda text: E.embed_texts(
        [text], prefix=E.embedding_model().query_prefix)[0])
    monkeypatch.setattr(E, "embed", lambda text: E.embed_texts([text])[0])
    return E


def _corpus(tmp_path):
    root = tmp_path / "pkb"
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "a.md").write_text("# a\nalpha\n")
    (root / "notes" / "b.md").write_text("# b\nbravo\n")
    return root


def test_first_ingest_without_a_provider_raises_with_the_pull_command(
        tmp_path, unreachable_provider):
    import arail.pkb as pkb

    root = _corpus(tmp_path)
    with pytest.raises(unreachable_provider.EmbeddingError) as exc:
        pkb.index_all(pkb_root=root, include_docs=False)
    message = str(exc.value)
    assert "ollama" in message.lower()
    assert "nomic-embed-text" in message, \
        "the message must name the model to pull"


def test_first_ingest_without_a_provider_writes_nothing(tmp_path, unreachable_provider):
    """FM15: zero vectors, and no half-made cache dir left behind."""
    import arail.pkb as pkb

    root = _corpus(tmp_path)
    with pytest.raises(unreachable_provider.EmbeddingError):
        pkb.index_all(pkb_root=root, include_docs=False)
    assert not (root / ".cache" / "lancedb" / "pkb_pages.lance").exists()
    assert not (root / ".cache" / "lancedb" / "pkb_pages.provenance.json").exists()


def test_provider_outage_leaves_an_existing_index_byte_identical(
        tmp_path, unreachable_provider):
    """The dangerous shape: a lab that WAS working, then Ollama stops. A
    rebuild attempt must not empty the index (``VectorIndex.replace`` is an
    overwrite drop)."""
    import hashlib
    import arail.pkb as pkb
    import arail.dbspec.embed as E
    from arail.dbspec.generated.models_registry import EMBEDDING_DIM
    from arail.vector_index import hash_embedding

    root = _corpus(tmp_path)
    # Build a healthy index with a working provider first.
    real_docs = E.embed_documents
    E.embed_documents = lambda texts: [
        hash_embedding(t, dim=EMBEDDING_DIM) for t in texts]
    try:
        pkb.index_all(pkb_root=root, include_docs=False)
    finally:
        E.embed_documents = real_docs

    table = root / ".cache" / "lancedb" / "pkb_pages.lance"

    def fingerprint():
        h = hashlib.sha256()
        for p in sorted(table.rglob("*")):
            if p.is_file():
                h.update(p.relative_to(table).as_posix().encode())
                h.update(p.read_bytes())
        return h.hexdigest()

    before = fingerprint()
    with pytest.raises(unreachable_provider.EmbeddingError):
        pkb.index_all(pkb_root=root, include_docs=False)
    assert fingerprint() == before


def test_a_provider_outage_never_substitutes_hash_vectors(tmp_path, unreachable_provider):
    """The lesson ``embed.py``'s module docstring is written around: no
    silent fallback to the 128-dim hash embedding. Assert it as behaviour —
    an outage produces an exception, never a table."""
    import arail.pkb as pkb
    import lancedb  # type: ignore[import-not-found]
    from arail.vector_index import VectorIndex

    root = _corpus(tmp_path)
    with pytest.raises(unreachable_provider.EmbeddingError):
        pkb.index_all(pkb_root=root, include_docs=False)

    db_path = root / ".cache" / "lancedb"
    if db_path.exists():
        db = lancedb.connect(str(db_path))
        assert "pkb_pages" not in VectorIndex._existing_tables(db)


def test_search_on_a_labless_machine_degrades_rather_than_raising(
        tmp_path, unreachable_provider):
    """A friend who opens /knowledge before the model finishes pulling must
    get a usable page, not a 500."""
    import arail.pkb as pkb
    import arail.pkb_index as pki
    from arail.dbspec.generated.models_registry import EMBEDDING_DIM
    from arail.vector_index import hash_embedding

    root = _corpus(tmp_path)
    # A populated, provenance-correct index; only the provider is down.
    import arail.dbspec.embed as E
    real_docs = E.embed_documents
    E.embed_documents = lambda texts: [
        hash_embedding(t, dim=EMBEDDING_DIM) for t in texts]
    try:
        pkb.index_all(pkb_root=root, include_docs=False)
    finally:
        E.embed_documents = real_docs

    pki._reset_for_tests()
    hits = pkb.search("alpha", root)          # must not raise
    assert all(h["source"] == "keyword" for h in hits)
    ok, reason = pkb.retrieval_status()
    assert ok is False
    assert "ollama" in reason.lower()


def test_pkb_ingest_does_not_trigger_a_synchronous_embed_storm(tmp_path, monkeypatch):
    """FM11 pinned from the user's side: typing in the search box on an
    empty index must issue zero embed calls, no matter how many times."""
    import arail.pkb as pkb
    import arail.pkb_index as pki

    calls = []
    monkeypatch.setattr("arail.dbspec.embed.embed_query",
                        lambda text: calls.append(text) or [0.0] * 768)

    root = _corpus(tmp_path)  # no index at all
    for _ in range(25):
        pkb.search("anything at all", root)
    assert calls == []
    assert "empty" in pki.degraded_codes()
    assert "pkb reembed" in pki.degraded_codes()["empty"]
