"""QA target 6: the honest-failure half of the win condition, end to end.

Defect B was not "retrieval is broken" — the architect measured retrieval
working on the operator's provisioned lab (12 semantic hits for "how does
attention work", gated). Defect B was that when the running interpreter
cannot import the vector backend, semantic retrieval dies and *every honesty
surface agrees it is alive*. So the thing to prove is the honesty, on the
surfaces a human or an agent actually reads:

  * ``/api/pkb/search``'s ``X-Retrieval-Status`` header — the only one of the
    four surfaces that reaches the portal UI, and the only one never
    exercised end to end in three build rounds (the sprint's own tests call
    ``retrieval_status()`` directly).
  * the FIRST search in a fresh process, since ``_degraded_codes`` is
    process-global and starts empty: if the header were computed before the
    search ran, a freshly-booted portal would answer its first query with
    keyword results and a clean bill of health — defect B exactly.
  * the control: with the backend genuinely importable, none of this fires.

Plus one security case the fix newly created: the degraded reason string now
interpolates ``sys.executable`` and that string is emitted as an HTTP
response header value.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from arail import compiled_kb as ckb
from arail import pkb as pkb_mod
from arail import pkb_index

fastapi_testclient = pytest.importorskip(
    "fastapi.testclient", reason="portal endpoint tests need fastapi")


@pytest.fixture(autouse=True)
def _reset_degraded():
    pkb_index._reset_for_tests()
    yield
    pkb_index._reset_for_tests()


@pytest.fixture()
def seeded_pkb(tmp_path):
    root = tmp_path / "pkb"
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "attention.md").write_text(
        "# Attention\nHow does attention work in a transformer? "
        "Attention lets a model weigh tokens.", encoding="utf-8")
    ckb.approve(["notes/attention.md"], root)
    return root


# ── The header, end to end ─────────────────────────────────────────────

def _client():
    from fastapi.testclient import TestClient
    from arail.portal.app import app
    return TestClient(app)


def test_first_search_in_a_fresh_process_is_already_stamped_degraded():
    """THE ONE THAT MATTERS: ``_degraded_codes`` is process-global and
    starts EMPTY. If ``retrieval_status()`` were consulted before the
    search ran — or if the backend-absent branch did not set the code —
    the very first query a freshly-booted portal answers would come back
    with keyword hits and no degradation header. That is defect B's exact
    user-visible shape, and no test in this sprint covers the header.
    """
    assert pkb_index.degraded_codes() == {}, "fixture leaked degraded state"
    with patch("arail.vector_index.available", return_value=False):
        r = _client().get("/api/pkb/search", params={"q": "how does attention work"})
    assert r.status_code == 200
    assert r.headers.get("X-Retrieval-Status") == "degraded", (
        "the FIRST search on a broken backend answered with a clean bill of "
        "health — headers: %r" % dict(r.headers)
    )
    reason = r.headers.get("X-Retrieval-Reason") or ""
    assert "LanceDB" in reason or "backend" in reason.lower(), reason


def test_the_header_names_a_fix_the_operator_can_act_on():
    with patch("arail.vector_index.available", return_value=False):
        r = _client().get("/api/pkb/search", params={"q": "attention"})
    reason = r.headers.get("X-Retrieval-Reason") or ""
    assert "arailctl install" in reason or "python" in reason, reason


def test_control_no_degradation_header_when_the_backend_is_importable():
    """The control that makes the two tests above mean something. Skipped
    where LanceDB genuinely is not installed (this worktree) — which is
    itself the point: the assertion only has content on a provisioned
    interpreter, so it must be run on one."""
    from arail.vector_index import available
    if not available():
        pytest.skip("LanceDB not importable in this interpreter — "
                    "run this test on a provisioned .venv")
    r = _client().get("/api/pkb/search", params={"q": "attention"})
    assert r.headers.get("X-Retrieval-Status") != "degraded" or \
        "backend" not in pkb_index.degraded_codes(), (
            "the backend is importable but 'backend' was reported degraded: %r"
            % pkb_index.degraded_codes())


def test_empty_query_is_not_reported_as_a_retrieval_failure():
    """Edge: the endpoint short-circuits an empty query before it ever
    reaches the search path, so it must not stamp degraded — otherwise a
    user clearing the search box turns the banner on."""
    with patch("arail.vector_index.available", return_value=False):
        r = _client().get("/api/pkb/search", params={"q": "   "})
    assert r.status_code == 200
    assert r.headers.get("X-Retrieval-Status") is None


# ── Security: the fix put sys.executable into an HTTP header ───────────

def test_a_hostile_interpreter_path_cannot_inject_a_response_header():
    """The defect-B fix interpolates ``sys.executable`` into the degraded
    reason, and ``/api/pkb/search`` emits that reason as an HTTP response
    header value. An interpreter path is not normally attacker-controlled,
    but it is *environment*-controlled (a venv on a shared box, a path
    chosen by whoever installed the lab), and CR/LF in a header value is
    response splitting. Assert the sanitizer holds.
    """
    evil = "/tmp/py\r\nX-Injected: yes\r\n\r\n<script>alert(1)</script>"
    with patch("arail.pkb.sys") as fake_sys:
        fake_sys.executable = evil
        with patch("arail.vector_index.available", return_value=False):
            r = _client().get("/api/pkb/search", params={"q": "attention"})
    assert "X-Injected" not in r.headers
    reason = r.headers.get("X-Retrieval-Reason") or ""
    assert "\r" not in reason and "\n" not in reason, repr(reason)


# ── Degraded-code scope discipline, the untested direction ─────────────

def test_a_successful_available_observation_clears_only_the_backend_code(tmp_path):
    """F12's mirror image. The sprint tests that a successful *embed* does
    not clear ``backend``. The other direction is equally load-bearing: a
    successful ``available()`` is evidence about the backend ONLY, and must
    not clear a dimension/provenance/empty degradation that is still true —
    otherwise re-importing LanceDB silently launders an unrelated fault
    into a clean bill of health.
    """
    pkb_index.set_degraded("backend", "no lancedb")
    pkb_index.set_degraded("dimension", "table dim 384 != spec 768")
    pkb_index.set_degraded("provenance", "sidecar model mismatch")
    with patch("arail.vector_index.available", return_value=True):
        pkb_mod._semantic_search("attention", tmp_path)
    codes = pkb_index.degraded_codes()
    assert "backend" not in codes
    assert "dimension" in codes, codes
    assert "provenance" in codes, codes


def test_backend_degradation_survives_a_second_failing_search(tmp_path):
    """Idempotence of the honesty: two failing searches in a row must not
    flap the code off and on (a flapping code shows the banner
    intermittently, which trains users to ignore it)."""
    with patch("arail.vector_index.available", return_value=False):
        for _ in range(3):
            pkb_mod._semantic_search("attention", tmp_path)
            assert "backend" in pkb_index.degraded_codes()


def test_retrieve_for_agents_never_labels_a_keyword_hit_semantic(seeded_pkb):
    """Buddy's actual entry point. The failure mode that started this
    sprint is a keyword result that looks like a semantic one; assert the
    provenance label is honest for every hit, on three natural-language
    questions."""
    with patch("arail.vector_index.available", return_value=False):
        for q in ("how does attention work",
                  "what is a transformer",
                  "explain gradient descent"):
            out = pkb_mod.retrieve_for_agents(q, seeded_pkb)
            for hit in out["hits"]:
                assert hit.get("source") != "semantic", (q, hit)
            assert out.get("source") != "semantic", (q, out.get("source"))
    assert "backend" in pkb_index.degraded_codes()
