"""httpx-family egress guard: the transport-level patch install_guard() puts on every installed
httpx-family library (``httpx2`` and ``httpx``; the list is arail.httpx_family.FAMILY).

TD50.  The guard once patched one module, ``httpx``.  huggingface-hub 2.x, anthropic 2.x and
openai 3.x moved to ``httpx2``, so on a fresh install their requests ran past the guard, and the
tests that pinned the guard skipped themselves where ``httpx`` was absent.  Nothing was red.

So these tests:

- run once per library that is actually installed, taken from an independent lookup
  (tests/egress_family_support.py), never from the guard's own list;
- never skip for the thing under test.  A library that is absent gets an assertion (EG-15); an
  environment with neither fails a sentinel (EG-0) in its own words;
- check the leg's declaration (EG-0b), so a CI leg that lost a library fails instead of testing less;
- contact no real host.  A request that must be refused uses a ``.invalid`` name, which never
  resolves, so a regression is a fast ConnectError.  A request that must pass reaches a stub that
  was planted on the transport before the guard was installed.

CI's no-library leg imports this module, so it imports no HTTP library and no huggingface_hub at
module level; each library is imported by name inside the test that needs it.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
import os
import pkgutil
import re
import socket
import sys
import threading
import types

import pytest
import requests

import arail.egress as egress
from arail.airgap import EgressBlocked
from tests.egress_family_support import (
    DECLARATION_ENV,
    LITERAL_FAMILY,
    PROBE_URL,
    PUBLIC_TEST_IP,
    declared_family,
    installed_independently,
)

INSTALLED = installed_independently()
lib_param = pytest.mark.parametrize("lib", INSTALLED)

# The stock requests adapter, taken at import time, before any test has installed the guard.  The
# tests that look at what a reset restores compare against this, and the fixture below puts it
# back after every test so that one failing test cannot hand a patched adapter to the next.
_STOCK_ADAPTER = requests.adapters.HTTPAdapter

# The two class-level patch targets the guard puts on every member.
TARGETS = (("HTTPTransport", "handle_request"), ("AsyncHTTPTransport", "handle_async_request"))

UNPROVEN = (
    "egress coverage of the httpx family is UNPROVEN in this environment: neither httpx2 nor "
    "httpx is importable, so no test here exercised the guard on them. Install one "
    "(CI: see .github/workflows/egress-guard.yml)."
)


class ReachedTransport(Exception):
    """Marker: the guard let the request through to the real transport."""


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.lower().endswith("_proxy"):  # a stray proxy would turn a regression into a real hop
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LAB_MODE", "airgapped")
    monkeypatch.delenv("ARAIL_MODE", raising=False)
    # The guard asks socket.gethostbyname whether a name resolves to a local address.  Answer
    # deterministically with a public address (see PUBLIC_TEST_IP) and ask no resolver.
    monkeypatch.setattr(socket, "gethostbyname", lambda host: PUBLIC_TEST_IP)
    egress._reset_for_tests()
    yield
    egress._reset_for_tests()
    # Containment, not part of the guard's own reset.  If a test proved that a reset does NOT restore
    # the adapter (or that a racing install saved the guarded class as "the original"), the next
    # test must still start from the stock one, or one red test becomes thirty.
    egress._ORIGINAL_HTTP_ADAPTER = _STOCK_ADAPTER
    requests.adapters.HTTPAdapter = _STOCK_ADAPTER
    requests.sessions.HTTPAdapter = _STOCK_ADAPTER


def _stub_transports(monkeypatch, mod):
    """Replace the real transport methods with markers BEFORE install_guard(), so a request that
    passes the guard raises ReachedTransport instead of touching the network."""

    def _sync(self, request, *args, **kwargs):
        raise ReachedTransport(str(request.url))

    async def _async(self, request, *args, **kwargs):
        raise ReachedTransport(str(request.url))

    monkeypatch.setattr(mod.HTTPTransport, "handle_request", _sync)
    monkeypatch.setattr(mod.AsyncHTTPTransport, "handle_async_request", _async)


def _audit_lines(tmp_path):
    path = tmp_path / "egress.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


async def _get_async(mod, url):
    async with mod.AsyncClient(timeout=3) as client:
        return await client.get(url)


def _owned(mod, cls_name, attr):
    """The function stored in the class's own __dict__ (what the guard replaces and restores)."""
    return vars(getattr(mod, cls_name))[attr]


# --- EG-0, EG-0b: the environment is one the claims can be proven in ------------------------

def test_httpx_family_coverage_is_provable_in_this_environment():
    """EG-0, the sentinel.  If neither library imports, every test below is parametrized over an
    empty set and proves nothing, which is exactly how TD50 stayed hidden.  This must fail."""
    importable = []
    for name in INSTALLED:
        try:
            importlib.import_module(name)
        except Exception:  # a library that is present but broken proves nothing either
            continue
        importable.append(name)
    assert importable, UNPROVEN


def test_installed_httpx_family_matches_the_ci_leg_declaration():
    """EG-0b.  Each CI leg declares which libraries it built (ARAIL_TEST_EXPECT_HTTPX_FAMILY).
    A leg that silently lost one, or gained one, is not the leg it claims to be.  Locally the
    declaration is optional (skip: it is a check of the leg, not of the guard); in CI it is not."""
    declared = declared_family()
    if declared is None:
        if os.environ.get("CI") == "true":
            pytest.fail(
                "every CI run of the egress tests must declare its libraries: set "
                "ARAIL_TEST_EXPECT_HTTPX_FAMILY to httpx2, httpx, httpx,httpx2 or none"
            )
        pytest.skip("ARAIL_TEST_EXPECT_HTTPX_FAMILY is not set; this is a CI-leg check, not a check of the guard")
    installed = installed_independently()
    assert set(installed) == set(declared), (
        f"this leg declared {sorted(declared) or 'none'} but the environment has "
        f"{sorted(installed) or 'neither'}; read the leg's 'What this leg installed' block"
    )


# --- EG-1 to EG-4: airgapped refuses, whichever way the client was built --------------------

@lib_param
def test_airgapped_public_url_is_refused_sync(lib, tmp_path):
    """EG-1.  Refused before any I/O, attributed to this test, and the URL's secret goes nowhere."""
    mod = importlib.import_module(lib)
    egress.install_guard()
    with pytest.raises(EgressBlocked) as excinfo:
        mod.Client(timeout=3).get(PROBE_URL + "v1?token=SECRET_DO_NOT_LEAK")
    lines = _audit_lines(tmp_path)
    assert len(lines) == 1
    assert lines[0]["url_host"] == "egress-probe.invalid"
    assert lines[0]["reason"] == "airgapped"
    # Module-level function: on Python 3.10 the caller falls back to co_name, which equals the
    # qualname only outside a class.
    assert lines[0]["caller"] == f"{__name__}.{sys._getframe().f_code.co_name}"
    assert "SECRET_DO_NOT_LEAK" not in (tmp_path / "egress.jsonl").read_text()
    assert "SECRET_DO_NOT_LEAK" not in str(excinfo.value)


@lib_param
def test_airgapped_public_url_is_refused_async(lib, tmp_path):
    """EG-2."""
    mod = importlib.import_module(lib)
    egress.install_guard()
    with pytest.raises(EgressBlocked):
        asyncio.run(_get_async(mod, PROBE_URL))
    lines = _audit_lines(tmp_path)
    assert len(lines) == 1
    assert lines[0]["url_host"] == "egress-probe.invalid"
    assert lines[0]["caller"] == f"{__name__}._get_async"


@lib_param
def test_clients_built_before_install_are_still_guarded(lib):
    """EG-3.  The SDK pattern: a client constructed before install_guard() ran.  The check is on
    the class, which is looked up when the request is made."""
    mod = importlib.import_module(lib)
    sync_client = mod.Client(timeout=3)
    async_client = mod.AsyncClient(timeout=3)
    egress.install_guard()
    with pytest.raises(EgressBlocked):
        sync_client.get(PROBE_URL)

    async def _go():
        await async_client.get(PROBE_URL)

    with pytest.raises(EgressBlocked):
        asyncio.run(_go())
    sync_client.close()
    asyncio.run(async_client.aclose())


@lib_param
def test_module_level_helper_is_guarded(lib):
    """EG-4.  ``lib.get(...)`` builds a throwaway client of its own."""
    mod = importlib.import_module(lib)
    egress.install_guard()
    with pytest.raises(EgressBlocked):
        mod.get(PROBE_URL, timeout=3)


# --- EG-5 to EG-9: what must still pass ------------------------------------------------------

@lib_param
@pytest.mark.parametrize("url", ["http://127.0.0.1:11434/api/tags", "http://10.0.0.5/x"])
def test_loopback_and_private_destinations_pass(lib, url, monkeypatch):
    """EG-5.  The LAN-GPU-box rule: loopback and RFC1918 always pass, sync and async."""
    mod = importlib.import_module(lib)
    _stub_transports(monkeypatch, mod)
    egress.install_guard()
    with pytest.raises(ReachedTransport):
        mod.Client(timeout=3).get(url)
    with pytest.raises(ReachedTransport):
        asyncio.run(_get_async(mod, url))


@lib_param
def test_hybrid_passes_a_public_request(lib, monkeypatch):
    """EG-6."""
    monkeypatch.setenv("LAB_MODE", "hybrid")
    mod = importlib.import_module(lib)
    _stub_transports(monkeypatch, mod)
    egress.install_guard()
    with pytest.raises(ReachedTransport):
        mod.Client(timeout=3).get(PROBE_URL)
    with pytest.raises(ReachedTransport):
        asyncio.run(_get_async(mod, PROBE_URL))


@lib_param
def test_allow_egress_in_hybrid_passes_and_is_audited(lib, monkeypatch, tmp_path):
    """EG-7."""
    monkeypatch.setenv("LAB_MODE", "hybrid")
    mod = importlib.import_module(lib)
    _stub_transports(monkeypatch, mod)
    egress.install_guard()
    with egress.allow_egress("test httpx-family bypass"):
        with pytest.raises(ReachedTransport):
            mod.Client(timeout=3).get(PROBE_URL)
    lines = _audit_lines(tmp_path)
    assert lines[-1]["reason"] == "allow:test httpx-family bypass"
    assert lines[-1]["url_host"] == "egress-probe.invalid"


@lib_param
def test_allow_egress_is_refused_on_entry_when_airgapped(lib, monkeypatch):
    """EG-8, the ratchet, unchanged: there is no way in, and the request inside is never made."""
    mod = importlib.import_module(lib)
    _stub_transports(monkeypatch, mod)
    egress.install_guard()
    made = []
    with pytest.raises(EgressBlocked):
        with egress.allow_egress("try to open the airgap"):
            made.append("entered")
            mod.Client(timeout=3).get(PROBE_URL)
    assert made == []


@lib_param
def test_bootstrap_fetch_is_host_allowlisted(lib, monkeypatch, tmp_path):
    """EG-9.  The one consent-gated exemption that works airgapped: an allowlisted host reaches
    the stub and leaves an ``allow:`` line; any other host, inside the scope, is still refused."""
    import arail.agents.consent as consent_mod
    from arail.agents.consent import ConsentStore

    monkeypatch.setattr(consent_mod, "CONSENT_DIR", tmp_path / "consent")
    store = ConsentStore(data_dir=tmp_path / "consent")
    request = store.request_access("http://allowed.egress-probe.invalid/", "bootstrap test", agent="forge")
    store.approve(request["id"])

    mod = importlib.import_module(lib)
    _stub_transports(monkeypatch, mod)
    egress.install_guard()
    with egress.allow_bootstrap_fetch("bootstrap: test", ["allowed.egress-probe.invalid"], consent_id=request["id"]):
        with pytest.raises(ReachedTransport):
            mod.Client(timeout=3).get("http://allowed.egress-probe.invalid/page")
        with pytest.raises(ReachedTransport):
            asyncio.run(_get_async(mod, "http://allowed.egress-probe.invalid/page"))
        with pytest.raises(EgressBlocked):
            mod.Client(timeout=3).get(PROBE_URL)  # not under the allowlisted suffix
    lines = _audit_lines(tmp_path)
    allowed = [line for line in lines if line["reason"].startswith("allow:")]
    refused = [line for line in lines if line["reason"] == "airgapped"]
    assert len(allowed) == 2 and all(line["url_host"] == "allowed.egress-probe.invalid" for line in allowed)
    assert len(refused) == 1 and refused[0]["url_host"] == "egress-probe.invalid"


# --- EG-10 to EG-14: client shapes that all reach the patched class --------------------------

@lib_param
def test_second_hop_of_a_redirect_is_checked(lib, tmp_path):
    """EG-10.  A local first hop (an in-process mount, so no port is bound) redirects to a public
    name.  The follow-up request goes through the default transport and is refused."""
    mod = importlib.import_module(lib)
    egress.install_guard()

    def _mounts():
        first_hop = mod.MockTransport(lambda request: mod.Response(302, headers={"Location": PROBE_URL + "next"}))
        return {"http://127.0.0.1": first_hop}

    client = mod.Client(mounts=_mounts(), follow_redirects=True, timeout=3)
    with pytest.raises(EgressBlocked):
        client.get("http://127.0.0.1:11434/start")
    async_client = mod.AsyncClient(mounts=_mounts(), follow_redirects=True, timeout=3)

    async def _go():
        await async_client.get("http://127.0.0.1:11434/start")

    with pytest.raises(EgressBlocked):
        asyncio.run(_go())
    lines = _audit_lines(tmp_path)
    assert [line["url_host"] for line in lines] == ["egress-probe.invalid"] * 2


@lib_param
@pytest.mark.parametrize("shape", ["proxy-argument", "mounts-with-proxy-transport", "environment-proxy"])
def test_a_proxied_client_is_checked_on_the_destination(lib, shape, monkeypatch):
    """EG-11.  A proxy at loopback must not make a public destination look local: the decision is
    on the destination.  (socks and http2 need socksio and h2, which are not installed with the
    dev extra, so those are QA evidence, not a unit test.)"""
    mod = importlib.import_module(lib)
    egress.install_guard()
    proxy = "http://127.0.0.1:9"
    if shape == "environment-proxy":
        monkeypatch.setenv("HTTPS_PROXY", proxy)
        monkeypatch.setenv("HTTP_PROXY", proxy)
        client = mod.Client(timeout=3)
        async_client = mod.AsyncClient(timeout=3)
    elif shape == "proxy-argument":
        client = mod.Client(proxy=proxy, timeout=3)
        async_client = mod.AsyncClient(proxy=proxy, timeout=3)
    else:
        client = mod.Client(mounts={"all://": mod.HTTPTransport(proxy=proxy)}, timeout=3)
        async_client = mod.AsyncClient(mounts={"all://": mod.AsyncHTTPTransport(proxy=proxy)}, timeout=3)
    with pytest.raises(EgressBlocked):
        client.get(PROBE_URL)

    async def _go():
        await async_client.get(PROBE_URL)

    with pytest.raises(EgressBlocked):
        asyncio.run(_go())


@lib_param
def test_explicit_and_subclassed_transports_are_checked(lib):
    """EG-12.  ``transport=HTTPTransport(retries=2)`` and a subclass that calls ``super()``."""
    mod = importlib.import_module(lib)
    egress.install_guard()

    class SubTransport(mod.HTTPTransport):
        def handle_request(self, request):
            return super().handle_request(request)

    class AsyncSubTransport(mod.AsyncHTTPTransport):
        async def handle_async_request(self, request):
            return await super().handle_async_request(request)

    for transport in (mod.HTTPTransport(retries=2), SubTransport()):
        with pytest.raises(EgressBlocked):
            mod.Client(transport=transport, timeout=3).get(PROBE_URL)
    for transport in (mod.AsyncHTTPTransport(retries=2), AsyncSubTransport()):
        client = mod.AsyncClient(transport=transport, timeout=3)

        async def _go(client=client):
            await client.get(PROBE_URL)

        with pytest.raises(EgressBlocked):
            asyncio.run(_go())


async def _asgi_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-length", b"2")]})
    await send({"type": "http.response.body", "body": b"ok"})


def _wsgi_app(environ, start_response):
    start_response("200 OK", [("Content-Length", "2")])
    return [b"ok"]


@lib_param
def test_in_process_transports_are_not_blocked(lib):
    """EG-13.  ASGI, WSGI and Mock transports, and starlette's TestClient, are other classes and
    never touch the network.  The guard must leave them alone or the portal's own tests break."""
    mod = importlib.import_module(lib)
    egress.install_guard()  # airgapped
    base_url = "http://testserver"
    mock = mod.Client(transport=mod.MockTransport(lambda request: mod.Response(200)), base_url=base_url)
    assert mock.get("/").status_code == 200
    wsgi = mod.Client(transport=mod.WSGITransport(app=_wsgi_app), base_url=base_url)
    assert wsgi.get("/").status_code == 200

    async def _asgi():
        async with mod.AsyncClient(transport=mod.ASGITransport(app=_asgi_app), base_url=base_url) as client:
            return (await client.get("/")).status_code

    assert asyncio.run(_asgi()) == 200
    from starlette.testclient import TestClient  # rides on whichever library starlette prefers

    assert TestClient(_asgi_app).get("/").status_code == 200


@lib_param
def test_a_request_from_a_worker_thread_is_refused(lib):
    """EG-14.  The patch is on the class, so it is process-wide."""
    mod = importlib.import_module(lib)
    egress.install_guard()
    outcome = {}

    def _worker():
        try:
            mod.Client(timeout=3).get(PROBE_URL)
            outcome["exc"] = None
        except BaseException as exc:  # noqa: BLE001
            outcome["exc"] = exc

    thread = threading.Thread(target=_worker)
    thread.start()
    thread.join(timeout=30)
    assert isinstance(outcome.get("exc"), EgressBlocked), outcome


# --- EG-15: a library that is not here is reported, not skipped ------------------------------

def test_every_family_member_is_reported_guarded_or_absent():
    """EG-15.  Never skips.  After install, each literal member is ``guarded`` if it is installed
    and ``absent`` if it is not (and then really cannot be found).  With both installed, the
    absent set is empty and this asserts exactly that."""
    egress.install_guard()
    coverage = egress.coverage()
    for name in LITERAL_FAMILY:
        if name in INSTALLED:
            assert coverage[name] == "guarded", (name, coverage)
        else:
            assert coverage[name] == "absent", (name, coverage)
            assert importlib.util.find_spec(name) is None
    assert coverage["requests"] == "guarded" and coverage["urllib.request"] == "guarded"


# --- EG-16 to EG-19: install and reset leave exactly what they found -------------------------

@lib_param
def test_reset_restores_the_library_exactly(lib):
    """EG-16.  An identity check, not just 'the marker is gone'."""
    mod = importlib.import_module(lib)
    originals = {(cls_name, attr): _owned(mod, cls_name, attr) for cls_name, attr in TARGETS}
    egress.install_guard()
    for cls_name, attr in TARGETS:
        assert getattr(_owned(mod, cls_name, attr), "_arail_guarded", False)
    egress._reset_for_tests()
    for (cls_name, attr), original in originals.items():
        restored = _owned(mod, cls_name, attr)
        assert restored is original
        assert not getattr(restored, "_arail_guarded", False)
        assert restored.__qualname__ == f"{cls_name}.{attr}"
        assert restored.__module__ == lib or restored.__module__.startswith(lib + ".")


@lib_param
def test_three_install_reset_cycles_leave_one_wrap_and_one_audit_line(lib, tmp_path):
    """EG-17."""
    mod = importlib.import_module(lib)
    originals = {(cls_name, attr): _owned(mod, cls_name, attr) for cls_name, attr in TARGETS}
    for cycle in range(3):
        egress.install_guard()
        first = {(cls_name, attr): _owned(mod, cls_name, attr) for cls_name, attr in TARGETS}
        egress.install_guard()  # a repeat is a no-op
        for key, original in originals.items():
            assert _owned(mod, *key) is first[key], "a repeated install changed the patched function"
            assert first[key].__wrapped__ is original, "the wrapper must wrap the library's own function"
            assert not getattr(first[key].__wrapped__, "_arail_guarded", False)
        before = len(_audit_lines(tmp_path))
        with pytest.raises(EgressBlocked):
            mod.Client(timeout=3).get(PROBE_URL)
        assert len(_audit_lines(tmp_path)) == before + 1, f"cycle {cycle}: one refusal, one audit line"
        egress._reset_for_tests()
        for key, original in originals.items():
            assert _owned(mod, *key) is original


@lib_param
def test_a_reexport_of_the_same_classes_is_wrapped_once(lib):
    """EG-18.  ``httpx2.alias_httpx()`` makes ``sys.modules['httpx']`` the httpx2 module; a
    re-export hands the guard the same class objects under another name.  They are patched once."""
    mod = importlib.import_module(lib)
    egress.install_guard()
    entries = dict(egress._PATCHED)
    alias = types.ModuleType(f"{lib}_reexport")
    alias.HTTPTransport = mod.HTTPTransport
    alias.AsyncHTTPTransport = mod.AsyncHTTPTransport
    assert egress._patch_family_member(alias.__name__, alias) == "guarded"
    assert egress._PATCHED == entries
    for cls_name, attr in TARGETS:
        assert not getattr(_owned(mod, cls_name, attr).__wrapped__, "_arail_guarded", False)


@lib_param
def test_concurrent_installs_wrap_once_and_save_the_original_adapter(lib):
    """EG-19.  Eight threads install at once.  One wrap, and a reset still restores the stock
    ``requests`` adapter (a second thread must not save the first thread's GuardedHTTPAdapter as
    'the original')."""
    mod = importlib.import_module(lib)
    assert requests.adapters.HTTPAdapter is _STOCK_ADAPTER, "this test starts from a polluted process"
    originals = {(cls_name, attr): _owned(mod, cls_name, attr) for cls_name, attr in TARGETS}
    barrier = threading.Barrier(8)
    errors = []

    def _worker():
        try:
            barrier.wait(timeout=30)
            egress.install_guard()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors, errors
    for key, original in originals.items():
        assert _owned(mod, *key).__wrapped__ is original
    assert sum(1 for cls, _attr in egress._PATCHED if cls.__module__.split(".")[0] == lib) == 2
    egress._reset_for_tests()
    assert requests.adapters.HTTPAdapter is _STOCK_ADAPTER
    for key, original in originals.items():
        assert _owned(mod, *key) is original


# --- EG-20: a library the guard cannot patch is loud, never silent ----------------------------

@lib_param
@pytest.mark.parametrize("mode", ["airgapped", "hybrid"])
def test_an_unpatchable_member_is_loud_and_everything_else_stays_guarded(lib, mode, monkeypatch):
    """EG-20, and F7: after a retried incomplete install, reset must still restore the stock
    ``requests`` adapter (the saved copy used to be overwritten with the guarded one)."""
    monkeypatch.setenv("LAB_MODE", mode)
    mod = importlib.import_module(lib)
    assert requests.adapters.HTTPAdapter is _STOCK_ADAPTER, "this test starts from a polluted process"
    monkeypatch.delattr(mod, "AsyncHTTPTransport")

    with pytest.raises(egress.EgressGuardIncomplete) as excinfo:
        egress.install_guard()
    assert lib in excinfo.value.uncovered and excinfo.value.uncovered == egress.uncovered()
    coverage = egress.coverage()
    assert coverage[lib].startswith("unguarded")
    assert coverage["requests"] == "guarded"
    assert getattr(_owned(mod, "HTTPTransport", "handle_request"), "_arail_guarded", False)
    assert egress._INSTALLED is False
    if mode == "airgapped":  # the half that could be patched is still refusing
        with pytest.raises(EgressBlocked):
            mod.Client(timeout=3).get(PROBE_URL)

    with pytest.raises(egress.EgressGuardIncomplete):  # a retry fails the same way
        egress.install_guard()
    assert egress._INSTALLED is False

    egress._reset_for_tests()
    assert requests.adapters.HTTPAdapter is _STOCK_ADAPTER
    assert requests.sessions.HTTPAdapter is _STOCK_ADAPTER


# A module that is not installed, imported from inside a "library" below: the same failure as a real
# install that has httpx2 but not the httpcore2 it imports.
_MISSING_DEPENDENCY = "arail_test_dependency_that_is_not_installed"


class _RaisingLoader(importlib.abc.Loader):
    """Importing the library fails.  ``failure`` is the exception class's name: RuntimeError, or the
    ModuleNotFoundError that a real broken install raises."""

    def __init__(self, failure):
        self.failure = failure

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        if self.failure == "ModuleNotFoundError":
            importlib.import_module(_MISSING_DEPENDENCY)  # raises the real ModuleNotFoundError
        raise RuntimeError("simulated: this library is installed but its import raises")


class _BrokenLibraryFinder(importlib.abc.MetaPathFinder):
    """Makes one top-level name look installed but broken: finding it raises (``mode == "lookup"``), or
    importing it raises ``mode``, the name of the exception class."""

    def __init__(self, name, mode):
        self.name, self.mode = name, mode

    def find_spec(self, fullname, path=None, target=None):
        if fullname != self.name:
            return None
        if self.mode == "lookup":
            raise RuntimeError("simulated: the import system fails while looking this library up")
        return importlib.machinery.ModuleSpec(fullname, _RaisingLoader(self.mode))


@pytest.mark.parametrize("mode,expected", [
    ("lookup", "unguarded: lookup raised RuntimeError"),
    ("RuntimeError", "unguarded: import raised RuntimeError"),
    ("ModuleNotFoundError", "unguarded: import raised ModuleNotFoundError"),
])
def test_a_library_that_cannot_even_be_imported_is_loud(mode, expected, monkeypatch):
    """EG-A3 (simulated, so it runs in every leg; FR-4 repeats the import cases in a fresh
    interpreter).  The first family member is made to raise while it is looked up, or while it is
    imported.  Everything else is still patched, and the install raises.

    The import is made to fail twice over: with a RuntimeError, and with the ModuleNotFoundError that
    a real broken install raises (the library is there, a library it imports is not; this is exactly
    what huggingface_hub reports in a venv with httpx2 but no httpcore2).  ModuleNotFoundError is an
    ImportError, and "I could not import it" is also how a library that is not installed looks, so a
    guard that read ImportError as "absent" would pass the RuntimeError case and leave a library that
    IS installed open without a word (REVIEW.md W4, mutant R5)."""
    first = LITERAL_FAMILY[0]
    monkeypatch.delitem(sys.modules, first, raising=False)
    monkeypatch.setattr(sys, "meta_path", [_BrokenLibraryFinder(first, mode), *sys.meta_path])
    with pytest.raises(egress.EgressGuardIncomplete) as excinfo:
        egress.install_guard()
    coverage = egress.coverage()
    assert coverage[first].startswith(expected), coverage
    assert first in excinfo.value.uncovered and excinfo.value.uncovered == egress.uncovered()
    if mode == "ModuleNotFoundError":  # the operator is told what is missing, not just that something is
        assert _MISSING_DEPENDENCY in coverage[first] and _MISSING_DEPENDENCY in str(excinfo.value)
    assert coverage["requests"] == "guarded" and coverage["urllib.request"] == "guarded"
    assert egress._INSTALLED is False
    for other in INSTALLED:  # a working member after the broken one was still patched
        if other != first:
            assert coverage[other] == "guarded"


def test_a_member_whose_attribute_lookup_raises_is_loud_and_the_next_member_is_still_patched(monkeypatch):
    """EG-A8 (QA-W2, TEST_REPORT.md).  EG-A3 makes the IMPORT of a member fail.  A member that imports fine can
    still raise when its transport classes are looked up: a module-level ``__getattr__`` (PEP 562, the shape
    httpx2 2.14 and 2.16 already use to load ``main`` lazily) whose own import fails raises ImportError, and
    ``getattr(module, name, None)`` swallows AttributeError only.  The exception used to escape the family
    step: ``install_guard()`` raised a raw ImportError, ``coverage()`` stayed empty, the next member was never
    patched, and the portal's status endpoint said "Cloud-provider APIs are blocked".  Now the member is
    reported like every other member the guard could not patch, and everything else is still patched."""
    first = LITERAL_FAMILY[0]
    lazy = types.ModuleType(first)

    def __getattr__(name):
        if name in ("HTTPTransport", "AsyncHTTPTransport"):
            raise ImportError("simulated: a lazy attribute whose import fails")
        raise AttributeError(name)

    lazy.__getattr__ = __getattr__
    monkeypatch.setitem(sys.modules, first, lazy)
    with pytest.raises(egress.EgressGuardIncomplete) as excinfo:
        egress.install_guard()
    coverage = egress.coverage()
    assert coverage[first].startswith("unguarded: ") and "ImportError" in coverage[first], coverage
    assert first in excinfo.value.uncovered
    assert coverage["requests"] == "guarded" and coverage["urllib.request"] == "guarded"
    for other in INSTALLED:
        if other != first:
            assert coverage[other] == "guarded", coverage
            assert getattr(_owned(importlib.import_module(other), "HTTPTransport", "handle_request"), "_arail_guarded", False)


def _stub_member(name, lazy_failures=()):
    """A family member made of stubs, so the test does not depend on which libraries are installed.

    ``HTTPTransport`` and ``AsyncHTTPTransport`` are classes whose methods stand for the library's network
    I/O.  A class named in ``lazy_failures`` is not an attribute of the module: looking it up reaches a
    module-level ``__getattr__`` that raises ImportError, as a lazily loaded attribute whose import fails
    does.  Returns ``(module, sync class, async class)``; a class that cannot be looked up is still returned,
    so the test can see whether the guard touched it."""
    module = types.ModuleType(name)

    class HTTPTransport:
        def handle_request(self, request):
            return "reached the network layer"

    class AsyncHTTPTransport:
        async def handle_async_request(self, request):
            return "reached the network layer"

    for cls in (HTTPTransport, AsyncHTTPTransport):
        if cls.__name__ not in lazy_failures:
            setattr(module, cls.__name__, cls)

    def __getattr__(attribute):
        if attribute in lazy_failures:
            raise ImportError(f"simulated: cannot import {attribute} (a lazy attribute whose import fails)")
        raise AttributeError(attribute)

    module.__getattr__ = __getattr__
    return module, HTTPTransport, AsyncHTTPTransport


@pytest.mark.parametrize("failing", [
    ("HTTPTransport", "AsyncHTTPTransport"),
    ("AsyncHTTPTransport",),
], ids=["both lookups raise", "only the async lookup raises"])
@pytest.mark.parametrize("broken", LITERAL_FAMILY)
def test_a_member_whose_lookup_raises_does_not_stop_the_other_member_from_being_patched(broken, failing, monkeypatch):
    """EG-A9 (QA-W2).  EG-A8 asserts that the member after the broken one is patched, but in a venv that has
    one library that assertion has nothing to look at, so this one builds both members from stubs and runs
    in every venv, with either member as the broken one.  It also covers the half-patched member: when only
    the async lookup raises, the sync class WAS patched (everything that can be patched is), the member is
    still reported as not guarded, and a reset puts the sync class back exactly."""
    other = next(name for name in LITERAL_FAMILY if name != broken)
    broken_module, broken_sync, _ = _stub_member(broken, failing)
    healthy_module, healthy_sync, healthy_async = _stub_member(other)
    monkeypatch.setitem(sys.modules, broken, broken_module)
    monkeypatch.setitem(sys.modules, other, healthy_module)
    stock_broken = vars(broken_sync)["handle_request"]
    stock_healthy = vars(healthy_sync)["handle_request"]

    with pytest.raises(egress.EgressGuardIncomplete) as excinfo:
        egress.install_guard()

    coverage = egress.coverage()
    assert coverage[broken].startswith("unguarded: patch raised ImportError: "), coverage
    assert coverage[other] == "guarded", coverage
    assert list(excinfo.value.uncovered) == [broken] and egress.uncovered() == excinfo.value.uncovered
    assert broken in str(excinfo.value) and "NOT checked against LAB_MODE" in str(excinfo.value)
    assert egress._INSTALLED is False

    request = types.SimpleNamespace(url=PROBE_URL)  # what the wrappers read: request.url
    with pytest.raises(EgressBlocked):
        healthy_sync().handle_request(request)
    with pytest.raises(EgressBlocked):
        asyncio.run(healthy_async().handle_async_request(request))

    patched = getattr(vars(broken_sync)["handle_request"], "_arail_guarded", False)
    assert patched is ("HTTPTransport" not in failing), "the guard patched what it could and nothing else"

    egress._reset_for_tests()
    assert vars(healthy_sync)["handle_request"] is stock_healthy
    assert vars(broken_sync)["handle_request"] is stock_broken


# --- EG-21, EG-22: audit attribution ----------------------------------------------------------

def _probe_in_module(module_name):
    """A function whose frame belongs to a module of that name, which calls _current_caller()."""
    namespace = {"__name__": module_name, "egress": egress}
    exec("def probe():\n    return egress._current_caller()\n", namespace)
    return namespace["probe"]


@pytest.mark.parametrize("module_name", [
    "httpx2._transports.default", "httpx._client", "httpx2", "httpx", "httpcore2._sync.connection_pool",
    "httpcore._async", "requests.sessions", "urllib3.connectionpool", "arail.egress",
])
def test_frames_of_the_guard_and_the_libraries_it_wraps_are_skipped(module_name):
    """EG-21.  The audit ``caller`` is the first frame that is not the guard or the library."""
    probe = _probe_in_module(module_name)
    assert probe() == f"{__name__}.{sys._getframe().f_code.co_name}"


@pytest.mark.parametrize("module_name", ["httpxfoo", "httpx2extra", "anthropicx", "httpcoreish"])
def test_a_lookalike_module_name_is_not_a_guard_frame(module_name):
    """EG-21, the other half.  Matching used to be ``startswith``, which skipped any module whose
    name merely began with 'httpx'.  A guard frame is an exact name or a dotted prefix."""
    probe = _probe_in_module(module_name)
    assert probe() == f"{module_name}.probe"


def test_the_family_and_its_lower_layers_are_named_in_skip_modules():
    """EG-22.  httpx2 was skipped only because "httpx2".startswith("httpx").  Name every member."""
    assert {"httpx", "httpx2", "httpcore", "httpcore2"} <= set(egress._SKIP_MODULES)


# --- small tests that pin sentences of the interface contract --------------------------------

def test_coverage_describes_the_last_install_attempt():
    """EG-A1.  ``{}`` before the first install and after a reset; otherwise the keys are
    ``requests``, ``urllib.request``, then the family in FAMILY order; a copy, not live state."""
    assert egress.coverage() == {}
    assert egress.uncovered() == {}
    egress.install_guard()
    coverage = egress.coverage()
    assert list(coverage) == ["requests", "urllib.request", *LITERAL_FAMILY]
    assert egress.GUARDED_CLIENTS == ("requests", "urllib.request", *LITERAL_FAMILY)
    coverage["requests"] = "tampered"
    assert egress.coverage()["requests"] == "guarded"
    egress._reset_for_tests()
    assert egress.coverage() == {}


@lib_param
def test_the_incomplete_install_error_says_what_is_unchecked_and_how_to_fix_it(lib, monkeypatch):
    """EG-A2.  Not an EgressBlocked (no request was refused); names the library and the reason,
    says its requests are NOT checked against LAB_MODE, what is guarded, and the fix."""
    mod = importlib.import_module(lib)
    monkeypatch.delattr(mod, "AsyncHTTPTransport")
    with pytest.raises(egress.EgressGuardIncomplete) as excinfo:
        egress.install_guard()
    error = excinfo.value
    assert isinstance(error, RuntimeError) and not isinstance(error, EgressBlocked)
    message = str(error)
    assert lib in message
    assert "AsyncHTTPTransport" in message  # the reason
    assert "NOT checked against LAB_MODE" in message
    assert "requests" in message and "urllib.request" in message  # what is still guarded
    assert "install a supported version (httpx2<3, httpx<1) or uninstall" in message


@lib_param
def test_the_wrappers_pass_extra_arguments_through_and_show_themselves(lib, monkeypatch):
    """EG-A4.  A wrapper hands every argument to the library's own method, carries the marker and
    ``__wrapped__``, and (no functools.wraps) is visibly ARAIL's."""
    mod = importlib.import_module(lib)
    seen = []

    def _sync(self, request, *args, **kwargs):
        seen.append((args, kwargs))
        return "sync-result"

    async def _async(self, request, *args, **kwargs):
        seen.append((args, kwargs))
        return "async-result"

    monkeypatch.setattr(mod.HTTPTransport, "handle_request", _sync)
    monkeypatch.setattr(mod.AsyncHTTPTransport, "handle_async_request", _async)
    egress.install_guard()
    wrapper = _owned(mod, "HTTPTransport", "handle_request")
    async_wrapper = _owned(mod, "AsyncHTTPTransport", "handle_async_request")
    assert wrapper._arail_guarded is True and wrapper.__wrapped__ is _sync
    assert async_wrapper._arail_guarded is True and async_wrapper.__wrapped__ is _async
    assert wrapper.__module__ == "arail.egress" and async_wrapper.__module__ == "arail.egress"
    request = mod.Request("GET", "http://127.0.0.1:11434/")
    assert wrapper(object(), request, 1, key=2) == "sync-result"
    assert asyncio.run(async_wrapper(object(), request, 3, other=4)) == "async-result"
    assert seen == [((1,), {"key": 2}), ((3,), {"other": 4})]


def test_a_method_the_class_only_inherits_is_deleted_again_on_reset():
    """EG-A5.  Reset puts back the exact pre-install state: ``setattr`` for an attribute the class
    owned, ``delattr`` for one it merely inherited (the real transports own theirs, so a stand-in
    module is the only way to reach the second branch)."""

    class Base:
        def handle_request(self, request):
            return "inherited"

    class AsyncBase:
        async def handle_async_request(self, request):
            return "inherited"

    class HTTPTransport(Base):
        pass

    class AsyncHTTPTransport(AsyncBase):
        pass

    fake = types.ModuleType("arail_fake_httpx_family_member")
    fake.HTTPTransport, fake.AsyncHTTPTransport = HTTPTransport, AsyncHTTPTransport
    assert egress._patch_family_member(fake.__name__, fake) == "guarded"
    assert "handle_request" in vars(HTTPTransport) and "handle_async_request" in vars(AsyncHTTPTransport)
    assert egress._PATCHED[(HTTPTransport, "handle_request")][0] is False  # not owned
    egress._reset_for_tests()
    assert "handle_request" not in vars(HTTPTransport)
    assert "handle_async_request" not in vars(AsyncHTTPTransport)
    assert HTTPTransport().handle_request(None) == "inherited"


def test_a_class_that_refuses_the_patch_is_reported_and_the_other_target_is_still_patched():
    """EG-A6.  If setting the wrapper raises (here a metaclass that forbids it), that target is
    "unguarded" with the reason, never an exception out of the install and never a silent skip, and
    nothing is recorded as patched for it.  The other transport is still patched."""

    class Frozen(type):
        def __setattr__(cls, name, value):
            raise TypeError("this class is immutable")

    class HTTPTransport(metaclass=Frozen):
        def handle_request(self, request):
            return "original"

    class AsyncHTTPTransport:
        async def handle_async_request(self, request):
            return "original"

    fake = types.ModuleType("arail_fake_frozen_member")
    fake.HTTPTransport, fake.AsyncHTTPTransport = HTTPTransport, AsyncHTTPTransport
    result = egress._patch_family_member(fake.__name__, fake)
    assert result == (
        "unguarded: HTTPTransport.handle_request could not be patched (TypeError: this class is immutable)"
    )
    assert (HTTPTransport, "handle_request") not in egress._PATCHED
    assert getattr(vars(AsyncHTTPTransport)["handle_async_request"], "_arail_guarded", False)


# --- EG-23, EG-24: tripwires on the ecosystem -------------------------------------------------
#
# The guard covers a FIXED list of libraries (arail.httpx_family.FAMILY).  It deliberately does not
# discover them at runtime (that would import arbitrary packages at portal start).  The price is that
# a new member, or a new kind of transport inside a covered library, is invisible to the guard until
# someone looks.  These two tests are the someone: they turn red the day it happens (CI runs them
# daily), and each message says what to do.

LOWER_LAYERS = ("httpcore", "httpcore2")

# What every covered library exports today, by name.  Seven names: the two bases, the two that do
# network I/O (guarded), and the three that never touch a network (in-process).
KNOWN_TRANSPORT_NAMES = {
    "BaseTransport", "AsyncBaseTransport",
    "HTTPTransport", "AsyncHTTPTransport",
    "ASGITransport", "WSGITransport", "MockTransport",
}


def test_no_unknown_httpx_or_httpcore_module_is_installed():
    """EG-23.  Tripwire: a top-level module named ``httpx<N>`` that is not in the family, or
    ``httpcore<N>`` that is not a known lower layer, means a new HTTP client (a ``httpx3``, a fork with
    a numbered name) is on ``sys.path`` and nothing guards it.  Nothing in the lab need USE it for this
    to be red: it is cheaper to decide now than after an SDK moves to it."""
    found = {info.name for info in pkgutil.iter_modules()}
    new_clients = sorted(n for n in found if re.fullmatch(r"httpx\d*", n) and n not in LITERAL_FAMILY)
    new_layers = sorted(n for n in found if re.fullmatch(r"httpcore\d*", n) and n not in LOWER_LAYERS)
    assert not new_clients and not new_layers, (
        f"New httpx-family module(s) on sys.path: {new_clients + new_layers}. "
        "If it is an HTTP client, add it to arail.httpx_family.FAMILY, to tests/egress_family_support."
        "LITERAL_FAMILY, and to the docs (README, docs/PRIVACY.md, the Network Policy modal, "
        "airgap.KNOWN_GAPS); the guard then patches it and the tests parametrize over it. If it is a "
        "lower layer (httpcore), add it to LOWER_LAYERS here and to the documented lower layers."
    )


def _subclasses(cls):
    for sub in cls.__subclasses__():
        yield sub
        yield from _subclasses(sub)


@lib_param
def test_no_covered_library_has_a_network_transport_the_guard_does_not_patch(lib):
    """EG-24.  Tripwire: every transport class DEFINED under the library (its optional
    ``websockets`` module included, when its extra is installed) must derive from a class the guard
    patches (``HTTPTransport``, ``AsyncHTTPTransport``) or from one that never touches a network
    (``ASGITransport``, ``WSGITransport``, ``MockTransport``).  A transport that derives only from the
    bases does its own I/O, and a request through it would pass the guard unseen.  The names the
    library exports must also stay within the seven known ones."""
    mod = importlib.import_module(lib)
    try:
        importlib.import_module(lib + ".websockets")  # needs wsproto (httpx2); httpx has no such module
    except ImportError:
        pass

    exported = {name for name in dir(mod) if name.endswith("Transport")}
    unknown = sorted(exported - KNOWN_TRANSPORT_NAMES)
    assert not unknown, (
        f"{lib} now exports transport(s) the guard has never seen: {unknown}. Read what they do: if they "
        "perform network I/O, extend arail.egress._FAMILY_TARGETS and the tests; if they are in-process, "
        "add the name to KNOWN_TRANSPORT_NAMES here."
    )

    patched = (mod.HTTPTransport, mod.AsyncHTTPTransport)
    in_process = (mod.ASGITransport, mod.WSGITransport, mod.MockTransport)
    stray = sorted(
        f"{sub.__module__}.{sub.__qualname__}"
        for base in (mod.BaseTransport, mod.AsyncBaseTransport)
        for sub in _subclasses(base)
        if (sub.__module__ == lib or sub.__module__.startswith(lib + "."))
        and not issubclass(sub, patched + in_process)
    )
    assert not stray, (
        f"{lib} defines transport class(es) the guard does not patch and that are not in-process: {stray}. "
        "A request through one reaches the network without passing arail.egress. Extend "
        "arail.egress._FAMILY_TARGETS to patch it (and add a test), or, if it never touches a network, "
        "derive the allowlist here from its base class."
    )


# --- the declaration parser (tests/egress_family_support.declared_family) -------------------------

@pytest.mark.parametrize("raw,expected", [
    ("httpx2", ("httpx2",)),
    ("httpx", ("httpx",)),
    ("httpx,httpx2", ("httpx2", "httpx")),
    (" httpx2 , httpx ", ("httpx2", "httpx")),
    ("none", ()),
    ("NONE", ()),
])
def test_the_declaration_parser_reads_every_documented_form(raw, expected, monkeypatch):
    """EG-A7.  The CI legs declare ``httpx2``, ``httpx``, ``httpx,httpx2`` and ``none``; the answer is
    in FAMILY order whatever order they were written in."""
    monkeypatch.setenv(DECLARATION_ENV, raw)
    assert declared_family() == expected


def test_the_declaration_parser_returns_none_when_nothing_is_declared(monkeypatch):
    monkeypatch.delenv(DECLARATION_ENV, raising=False)
    assert declared_family() is None


@pytest.mark.parametrize("raw", ["", "   ", "httpx3", "httpx2,httpx3", "none,httpx", "httpx2,,httpx", "httpx2;httpx"])
def test_the_declaration_parser_rejects_a_mistake_instead_of_guessing(raw, monkeypatch):
    """A leg whose declaration is mis-typed must fail, not quietly declare something else: a
    declaration that cannot be wrong checks nothing."""
    monkeypatch.setenv(DECLARATION_ENV, raw)
    with pytest.raises(AssertionError, match=DECLARATION_ENV):
        declared_family()
