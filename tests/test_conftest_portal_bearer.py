"""The portal bearer that tests/conftest.py gives every ``TestClient`` actually reaches the request.

About 7k portal tests run unedited because an autouse fixture makes ``TestClient`` add
``Authorization: Bearer <test token>`` to any request that has none. The wrapper once found the HTTP
library by name (``import httpx``). Starlette 1.x builds ``TestClient`` on ``httpx2`` when that is
installed, and an unpinned ``pip install -e ".[dev]"`` resolves ONLY ``httpx2`` (huggingface-hub 2.x
depends on it; nothing depends on ``httpx``). The import failed, the failure was swallowed, and every
portal test ran with no credential: 3 + 52 red tests in CI, each a bare ``401`` that says nothing
about the harness. These tests fail first in that situation and say what is wrong.

They are listed in the DB-ensure and DaC workflows so that CI runs them. The first three talk to a
one-route Starlette app, not the portal: the question is whether the header *arrives*. The last asks
the real portal whether it *accepts* it.
"""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient


def _echo_app() -> Starlette:
    async def echo(request):
        return JSONResponse({"authorization": request.headers.get("authorization")})

    return Starlette(routes=[Route("/echo", echo), Route("/api/host/v1/echo", echo)])


def _built_on() -> str:
    """The library TestClient stands on, for the failure message."""
    return " > ".join(f"{c.__module__}.{c.__qualname__}" for c in TestClient.__mro__[1:3])


def test_testclient_requests_carry_the_portal_bearer(portal_bearer):
    got = TestClient(_echo_app()).get("/echo").json()["authorization"]
    assert got == portal_bearer["Authorization"], (
        f"no portal bearer reached the request (it carried {got!r}). TestClient is built on "
        f"{_built_on()}. tests/conftest.py must wrap the send that TestClient inherits, not a library "
        "it imports by name. Until it does, every portal test in this suite answers 401."
    )


def test_a_credential_a_test_sets_itself_is_kept():
    got = TestClient(_echo_app()).get("/echo", headers={"Authorization": "Bearer mine"})
    assert got.json()["authorization"] == "Bearer mine"


def test_the_host_edge_is_left_to_its_own_credential():
    """/api/host/v1/* admits with its own ``bhc1_`` token; a portal bearer there would turn
    "credential_missing" into "credential_invalid"."""
    assert TestClient(_echo_app()).get("/api/host/v1/echo").json()["authorization"] is None


@pytest.mark.real_auth
def test_real_auth_opts_out_of_the_bearer():
    """Tests of the auth itself rely on this: with the marker they must arrive anonymous."""
    assert TestClient(_echo_app()).get("/echo").json()["authorization"] is None


def test_the_portal_accepts_that_bearer():
    """A protected route, not an exempt one (``/api/instance`` would answer 200 with no credential
    and prove nothing). ``auth_required`` here means no header arrived; ``auth_invalid`` means one
    arrived and the portal refused it."""
    from arail.portal.app import app

    r = TestClient(app).get("/api/jobs/state")
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
