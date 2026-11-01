"""C1/BLOCK-1 required action 4: retrieval_status() wired into the
/api/pkb/search payload (REVIEW2.md). Calls the endpoint function directly
(no TestClient/app-boot needed) with the underlying search + status
functions monkeypatched.
"""

from __future__ import annotations

import asyncio

import pytest

from arail.portal import app as portal_app


def _run(coro):
    return asyncio.run(coro)


def test_search_ok_status_no_extra_headers(monkeypatch):
    monkeypatch.setattr(portal_app, "pkb_search", lambda q: [{"path": "a.md"}])
    monkeypatch.setattr("arail.pkb.retrieval_status", lambda: (True, ""))

    result = _run(portal_app.api_pkb_search(q="hello"))
    # ok status -> bare list, unchanged contract for existing frontend code.
    assert result == [{"path": "a.md"}]


def test_search_degraded_status_sets_headers(monkeypatch):
    monkeypatch.setattr(portal_app, "pkb_search", lambda q: [])
    monkeypatch.setattr(
        "arail.pkb.retrieval_status",
        lambda: (False, "pkb_pages index provenance disagrees with the current spec"))

    result = _run(portal_app.api_pkb_search(q="hello"))
    assert result.headers["X-Retrieval-Status"] == "degraded"
    assert "provenance" in result.headers["X-Retrieval-Reason"]


def test_search_empty_query_returns_bare_empty_list(monkeypatch):
    # Must not even reach retrieval_status() -- empty query short-circuits.
    calls = []
    monkeypatch.setattr("arail.pkb.retrieval_status", lambda: calls.append(1) or (True, ""))
    result = _run(portal_app.api_pkb_search(q="  "))
    assert result == []
    assert calls == []
