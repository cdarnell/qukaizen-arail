"""QA (2026-08-08-arail2-tier1-integration): the W0 airgapped egress guard.

arail runs on other people's machines and ``LAB_MODE=airgapped`` is the
default. ``MODEL_API_BASE`` is operator-settable and every PKB row's text
is POSTed to whatever it resolves to, so ``_assert_local`` is a security
boundary, not a convenience check.

``tests/dbspec/test_embed_airgapped.py`` covers the straightforward
allow/deny. These are the adversarial forms: hostname look-alikes,
userinfo confusion, alternate loopback encodings, the second env var that
also feeds ``ollama_root()``, and a redirect from an allowed loopback host
to somewhere else.
"""
from __future__ import annotations

import http.server
import json
import socketserver
import threading

import pytest


@pytest.fixture
def embed_mod(monkeypatch):
    """The embed module with every base-resolving env var cleared, so a
    test only sees what it sets.

    Deliberately does NOT ``importlib.reload`` the module. ``ollama_root``
    reads the environment at call time, so a reload buys nothing — and it
    costs a great deal: reloading rebinds ``EmbeddingError`` to a new class
    object, after which ``pkb_index``'s call-time ``from ... import
    EmbeddingError`` no longer matches an ``EmbeddingError`` any other test
    module imported at collection time. That silently turns C1's LOUD
    branch into the generic SKIP branch in whatever test runs next
    (reproduced: it broke three ``test_c1_error_contract`` tests).
    """
    for var in ("MODEL_API_BASE", "OLLAMA_HOST", "OLLAMA_PORT", "LAB_MODE"):
        monkeypatch.delenv(var, raising=False)
    import arail.dbspec.embed as E
    monkeypatch.setattr(E, "_LOGGED_HYBRID_EGRESS", False, raising=False)
    return E


# ---------------------------------------------------------------------------
# hostname parsing — the guard is an allowlist, so every trick must be denied
# ---------------------------------------------------------------------------

REFUSED_BASES = [
    ("http://evil.example:11434", "plain external host"),
    ("http://127.0.0.1.evil.example:11434", "loopback-prefix look-alike"),
    ("http://localhost.evil.example:11434", "localhost-prefix look-alike"),
    ("http://user@localhost@evil.example:11434", "userinfo confusion"),
    ("http://localhost.:11434", "trailing-dot localhost"),
    ("http://0.0.0.0:11434", "wildcard bind address"),
    ("http://127.0.0.2:11434", "loopback /8 that is not 127.0.0.1"),
    ("http://2130706433:11434", "decimal-encoded 127.0.0.1"),
    ("http://[::ffff:127.0.0.1]:11434", "ipv4-mapped ipv6 loopback"),
    ("https://embeddings.example.com", "https external host"),
]


@pytest.mark.parametrize("base,label", REFUSED_BASES,
                         ids=[lbl for _, lbl in REFUSED_BASES])
def test_airgapped_refuses_every_non_loopback_form(embed_mod, monkeypatch, base, label):
    monkeypatch.setenv("MODEL_API_BASE", base)
    with pytest.raises(embed_mod.EmbeddingError) as exc:
        embed_mod._assert_local(embed_mod.ollama_root())
    message = str(exc.value)
    assert "MODEL_API_BASE" in message, "the message must name the env var to fix"
    assert "LAB_MODE=hybrid" in message, "and the documented opt-in"


ALLOWED_BASES = ["http://127.0.0.1:11434", "http://localhost:11434",
                 "http://[::1]:11434", "http://LOCALHOST:11434"]


@pytest.mark.parametrize("base", ALLOWED_BASES)
def test_loopback_forms_are_allowed(embed_mod, monkeypatch, base):
    monkeypatch.setenv("MODEL_API_BASE", base)
    embed_mod._assert_local(embed_mod.ollama_root())  # must not raise


def test_ollama_host_is_not_a_second_way_around_the_guard(embed_mod, monkeypatch):
    """``ollama_root()`` reads OLLAMA_HOST too, in two shapes (bare host and
    full URL). Neither may become an unguarded egress path."""
    for value in ("evil.example", "http://evil.example:1234"):
        monkeypatch.setenv("OLLAMA_HOST", value)
        with pytest.raises(embed_mod.EmbeddingError):
            embed_mod._assert_local(embed_mod.ollama_root())


def test_hybrid_allows_non_loopback_because_the_operator_opted_in(embed_mod, monkeypatch):
    monkeypatch.setenv("MODEL_API_BASE", "http://embeddings.example.com")
    monkeypatch.setenv("LAB_MODE", "hybrid")
    embed_mod._assert_local(embed_mod.ollama_root())


@pytest.mark.parametrize("mode", ["", "airgapped", "AIRGAPPED", "Hybrid ", "off",
                                  "true", "1"])
def test_only_the_literal_hybrid_mode_opens_the_door(embed_mod, monkeypatch, mode):
    """Anything that is not ``hybrid`` (case/space-insensitively) must
    deny — a typo in .env must fail closed, not open."""
    monkeypatch.setenv("MODEL_API_BASE", "http://evil.example")
    monkeypatch.setenv("LAB_MODE", mode)
    if mode.strip().lower() == "hybrid":
        embed_mod._assert_local(embed_mod.ollama_root())
    else:
        with pytest.raises(embed_mod.EmbeddingError):
            embed_mod._assert_local(embed_mod.ollama_root())


def test_guard_raises_before_any_socket_is_opened(embed_mod, monkeypatch):
    def _explode(*a, **kw):  # pragma: no cover — must never run
        raise AssertionError("urlopen was reached with a non-loopback base")

    monkeypatch.setenv("MODEL_API_BASE", "http://evil.example")
    monkeypatch.setattr("urllib.request.urlopen", _explode)
    with pytest.raises(embed_mod.EmbeddingError):
        embed_mod._post("/api/embed", {"input": ["corpus text"]}, timeout=1)


# ---------------------------------------------------------------------------
# redirect: an allowed loopback provider must not be able to forward the body
# ---------------------------------------------------------------------------

class _Sink(http.server.BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        type(self).received.append(self.rfile.read(length).decode())
        body = json.dumps({"embeddings": [[0.0] * 768]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: A003
        pass


def _redirector(target_port, status):
    class _Redir(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.send_response(status)
            self.send_header("Location",
                             f"http://127.0.0.1:{target_port}/api/embed")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):  # noqa: A003
            pass
    return _Redir


@pytest.mark.parametrize("status", [301, 302, 307])
def test_a_loopback_provider_cannot_redirect_corpus_text_elsewhere(
        embed_mod, monkeypatch, status):
    """The guard checks the base URL string, so a *redirect* is the one
    shape it structurally cannot see. Pin that urllib never delivers the
    request body to the redirect target — if the HTTP client is ever
    swapped for one that follows redirects with the body intact (requests,
    httpx), this test is the tripwire."""
    _Sink.received = []
    sink = socketserver.TCPServer(("127.0.0.1", 0), _Sink)
    threading.Thread(target=sink.serve_forever, daemon=True).start()
    redir = socketserver.TCPServer(
        ("127.0.0.1", 0), _redirector(sink.server_address[1], status))
    threading.Thread(target=redir.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("MODEL_API_BASE",
                           f"http://127.0.0.1:{redir.server_address[1]}")
        with pytest.raises(embed_mod.EmbeddingError):
            embed_mod._post("/api/embed",
                            {"input": ["SENTINEL-CORPUS-TEXT"]}, timeout=5)
        assert not any("SENTINEL-CORPUS-TEXT" in body
                       for body in _Sink.received), \
            "corpus text was delivered to the redirect target"
    finally:
        sink.shutdown()
        redir.shutdown()


# ---------------------------------------------------------------------------
# on-disk artefacts must not carry user content
# ---------------------------------------------------------------------------

def test_lock_checkpoint_and_sidecar_carry_no_corpus_text(tmp_path):
    """The three files this sprint added under ``.cache/`` are inspected by
    humans and synced by backup tools. None may contain note text."""
    from arail import pkb_provenance, pkb_reembed

    root = tmp_path / "pkb"
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "private.md").write_text("SENTINEL-PRIVATE-CONTENT\n")

    pkb_reembed.run(root, include_docs=False)

    cache = root / ".cache"
    artefacts = [cache / "reembed.lock",
                 pkb_provenance.path_for(cache / "lancedb")]
    for artefact in artefacts:
        assert artefact.exists(), f"{artefact.name} should exist after a run"
        assert "SENTINEL-PRIVATE-CONTENT" not in artefact.read_text()
    # The checkpoint is cleared on success; when present mid-run it holds
    # paths only, never bodies — pinned by writing one explicitly.
    pkb_reembed._write_checkpoint(root, {"completed_paths": ["notes/private.md"]})
    assert "SENTINEL-PRIVATE-CONTENT" not in (
        cache / "reembed-state.json").read_text()
