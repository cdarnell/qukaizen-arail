"""Auto-skip ``@pytest.mark.requires_ollama`` tests when no Ollama is
reachable (FM18) — CI stays green without Ollama; live tests run locally."""

from __future__ import annotations

import pytest

from arail.dbspec import embed


def _ollama_available() -> bool:
    try:
        ok, _msg = embed.probe()
        return ok
    except Exception:
        return False


def pytest_collection_modifyitems(config, items):
    if _ollama_available():
        return
    skip_marker = pytest.mark.skip(
        reason="requires a reachable Ollama with nomic-embed-text pulled (FM18)")
    for item in items:
        if "requires_ollama" in item.keywords:
            item.add_marker(skip_marker)
