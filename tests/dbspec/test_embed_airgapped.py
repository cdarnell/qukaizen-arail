"""C3 — airgapped egress guard on ``arail.dbspec.embed._assert_local``.

A non-loopback ``MODEL_API_BASE`` must never reach ``urlopen`` while
``LAB_MODE`` is the default ``airgapped``. See ARCHITECTURE.md C3 / FM16.
"""

from __future__ import annotations

import pytest

from arail.dbspec import embed


def _boom(*_args, **_kwargs):
    raise AssertionError("urlopen must not be reached")


@pytest.fixture(autouse=True)
def _reset_hybrid_log_flag(monkeypatch):
    # The INFO-once flag is module-global; keep tests independent of order.
    monkeypatch.setattr(embed, "_LOGGED_HYBRID_EGRESS", False)


@pytest.mark.parametrize(
    "base",
    [
        "http://evil.example",
        "https://api.openai.com",
        "http://10.0.0.5:11434",
        "http://[::ffff:1.2.3.4]:11434",
    ],
)
def test_assert_local_raises_for_non_loopback_hosts(monkeypatch, base):
    monkeypatch.delenv("LAB_MODE", raising=False)
    with pytest.raises(embed.EmbeddingError):
        embed._assert_local(base)


def test_non_loopback_raises_before_urlopen(monkeypatch):
    monkeypatch.setenv("MODEL_API_BASE", "http://evil.example")
    monkeypatch.delenv("LAB_MODE", raising=False)
    monkeypatch.setattr(embed.urllib.request, "urlopen", _boom)
    with pytest.raises(embed.EmbeddingError, match="evil.example"):
        embed._post("/api/embed", {"model": "x", "input": ["y"]}, timeout=1.0)


def test_non_loopback_raises_explicit_airgapped(monkeypatch):
    monkeypatch.setenv("MODEL_API_BASE", "http://evil.example")
    monkeypatch.setenv("LAB_MODE", "airgapped")
    monkeypatch.setattr(embed.urllib.request, "urlopen", _boom)
    with pytest.raises(embed.EmbeddingError, match="MODEL_API_BASE"):
        embed._post("/api/embed", {"model": "x", "input": ["y"]}, timeout=1.0)


def test_non_loopback_allowed_under_hybrid(monkeypatch):
    monkeypatch.setenv("MODEL_API_BASE", "http://example.com")
    monkeypatch.setenv("LAB_MODE", "hybrid")
    # No urlopen stub here: we only assert _assert_local doesn't raise.
    embed._assert_local(embed.ollama_root())


@pytest.mark.parametrize(
    "base",
    ["http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434"],
)
def test_loopback_forms_accepted_under_default_airgapped(monkeypatch, base):
    monkeypatch.delenv("LAB_MODE", raising=False)
    embed._assert_local(base)  # must not raise


def test_assert_local_called_before_any_socket(monkeypatch):
    """The raise must happen before urlopen is even constructed."""
    monkeypatch.setenv("MODEL_API_BASE", "http://evil.example")
    monkeypatch.delenv("LAB_MODE", raising=False)

    def _explode(*_args, **_kwargs):
        raise AssertionError("urlopen constructed — guard did not run first")

    monkeypatch.setattr(embed.urllib.request, "Request", _explode)
    monkeypatch.setattr(embed.urllib.request, "urlopen", _explode)
    with pytest.raises(embed.EmbeddingError):
        embed._post("/api/embed", {}, timeout=1.0)
