"""QA — bypass-attempt suite for the airgap egress guard.

These tests simulate adversarial-but-realistic agent code patterns and
confirm the guard either blocks or has its limits *documented and
pinned*. The goal: someone reading these tests can see exactly what's
covered and what's intentionally not.

Architect's review listed these as the highest-leverage attack surface:

  1. Pre-guard ``requests.Session()`` constructed at module-import
     time (before ``install_guard()`` runs).
  2. DNS rebind — ``evil.example.com`` → ``127.0.0.1``.
  3. ``aiohttp``, raw ``socket.connect``,
     ``subprocess.run(["curl", ...])``, ``os.system("curl ...")``,
     ``urllib3`` / ``httpcore`` / ``httpcore2`` used directly, and the
     hop to a configured proxy — declared NOT wrapped (documented
     gaps). ``httpx`` used to be on this list; its transports, and
     those of its successor ``httpx2``, are wrapped now.
  4. ``asyncio.create_task`` contextvars-leak from inside an
     ``@allow_egress`` block (documented behavior).

Tests pin BOTH the closed paths AND the documented gaps. Closed-path
tests fail if a regression is introduced. Documented-gap tests fail if
a future PR *closes* the gap — that's the desired tripwire.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
import requests

import arail.airgap
import arail.egress
from tests.egress_family_support import PROBE_URL, PUBLIC_TEST_IP, installed_independently, run_fresh


# ──────────────────────────────────────────────────────────────────────
# 1. Pre-guard Session — installed before install_guard() ran
# ──────────────────────────────────────────────────────────────────────

class TestPreGuardSession:
    """A module that runs ``s = requests.Session()`` at import time
    *before* ``install_guard()`` runs gets the un-monkeypatched
    HTTPAdapter, and would bypass the guard.

    In tree, the three ``backends.py`` Sessions are inside ``__init__``
    methods (so they fire post-startup), but a third-party agent or
    fixture module could still hit this. Pin that the bypass exists
    and is bounded to: the Session must be constructed and reused
    *before* install_guard() is called for the first time."""

    def test_pre_guard_session_uses_unguarded_adapter(self, monkeypatch, tmp_path):
        """A Session built before install_guard mounts the original
        HTTPAdapter, NOT GuardedHTTPAdapter — pin this so reviewers
        understand the bypass surface."""
        monkeypatch.setenv("LAB_MODE", "airgapped")
        monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path))
        arail.egress._reset_for_tests()

        # Construct Session BEFORE install_guard.
        pre_guard_session = requests.Session()
        pre_guard_adapter_class_name = type(
            pre_guard_session.adapters["https://"]
        ).__name__

        arail.egress.install_guard()

        # The pre-guard Session still has its original adapter — that's
        # the documented bypass surface.
        assert pre_guard_adapter_class_name == "HTTPAdapter", (
            f"Pre-guard Session should have stock HTTPAdapter; "
            f"got {pre_guard_adapter_class_name!r}"
        )
        # Newly-constructed Session post-guard is guarded — pin the contrast.
        post_guard_session = requests.Session()
        post_guard_adapter_class_name = type(
            post_guard_session.adapters["https://"]
        ).__name__
        assert post_guard_adapter_class_name == "GuardedHTTPAdapter"

    def test_post_guard_session_remounts_inherits_guard(self, monkeypatch, tmp_path):
        """A Session constructed AFTER install_guard inherits guarding
        even if it ``s.mount("https://", HTTPAdapter())`` later — because
        ``HTTPAdapter`` is monkey-patched to be ``GuardedHTTPAdapter``."""
        monkeypatch.setenv("LAB_MODE", "airgapped")
        monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(socket, "gethostbyname", lambda h: "151.101.64.81")
        arail.egress._reset_for_tests()
        arail.egress.install_guard()

        s = requests.Session()
        # Even an explicit re-mount picks up the guarded adapter.
        from requests.adapters import HTTPAdapter as MaybeGuarded
        s.mount("https://", MaybeGuarded())

        with pytest.raises(arail.airgap.EgressBlocked):
            s.get("https://example.com", timeout=1)


# ──────────────────────────────────────────────────────────────────────
# 2. DNS rebind — public-looking host that resolves locally
# ──────────────────────────────────────────────────────────────────────

class TestDNSRebind:
    """v1 threat model trusts the system resolver. A public-looking
    hostname that resolves to 127.0.0.1 is treated as local — pin
    this documented limit so a future contributor doesn't change the
    behavior accidentally."""

    def test_dns_rebind_to_loopback_is_treated_local(self, monkeypatch, tmp_path):
        """evil.example.com → 127.0.0.1 → guard does NOT block."""
        monkeypatch.setenv("LAB_MODE", "airgapped")
        monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path))
        # Resolver answer: pretend evil.example.com is local.
        monkeypatch.setattr(socket, "gethostbyname", lambda h: "127.0.0.1")
        arail.egress._reset_for_tests()
        arail.egress.install_guard()

        # Should NOT raise EgressBlocked — guard trusts resolver.
        try:
            requests.get("https://evil.example.com:65535/x", timeout=0.1)
        except arail.airgap.EgressBlocked:
            pytest.fail(
                "DNS rebind to 127.0.0.1 must NOT raise EgressBlocked "
                "(documented limit of v1 threat model)."
            )
        except Exception:
            pass  # connection error to nonexistent local port is fine

    def test_dns_rebind_to_rfc1918_is_treated_local(self, monkeypatch, tmp_path):
        """evil.example.com → 192.168.1.50 → guard does NOT block."""
        monkeypatch.setenv("LAB_MODE", "airgapped")
        monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(socket, "gethostbyname", lambda h: "192.168.1.50")
        arail.egress._reset_for_tests()
        arail.egress.install_guard()

        try:
            requests.get("https://evil.example.com:65535/x", timeout=0.1)
        except arail.airgap.EgressBlocked:
            pytest.fail("DNS rebind to RFC1918 must NOT raise EgressBlocked")
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────
# 3. Documented gaps — aiohttp / raw socket / subprocess curl (httpx and httpx2 are wrapped)
# ──────────────────────────────────────────────────────────────────────

# The lower layers the httpx family stands on, whichever are installed.  Computed here, not read from
# arail.httpx_family, so a wrong list there cannot agree with itself.
INSTALLED_CORES = tuple(
    name for name in ("httpcore2", "httpcore") if importlib.util.find_spec(name) is not None
)

_GAP_CLOSED = (
    "DOCUMENTED-GAP TRIPWIRE: {what} raised EgressBlocked, so the guard now covers it. That is good news. "
    "Update docs/PRIVACY.md, README, the Network Policy modal and airgap.KNOWN_GAPS (TD53 / TD54), then "
    "turn this pin into a closed-path test."
)


_EXISTING_OBJECTS_REACHED = (
    "DOCUMENTED-GAP TRIPWIRE: {what} raised EgressBlocked, so the guard now reaches objects that were created "
    "before it was installed, or an opener built around it. That is good news. Update docs/PRIVACY.md (the "
    "bullet about a session or adapter created before the guard) and README ('requests (new sessions)', "
    "'urllib.request (the default opener)'), then turn this pin into a closed-path test."
)


def _make_invalid_names_unresolvable(monkeypatch):
    """The gap tests send requests PAST the guard on purpose.  Make ``getaddrinfo`` (what urllib3 and
    httpcore call) fail for any ``.invalid`` name, so that even on a network whose resolver answers
    reserved names, these requests cannot reach a real host: they end in a resolution error."""
    real = socket.getaddrinfo

    def getaddrinfo(host, *args, **kwargs):
        name = host.decode("ascii", "replace") if isinstance(host, bytes) else str(host)
        if name.endswith(".invalid"):
            raise socket.gaierror(-2, "simulated: a .invalid name never resolves")
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


def _no_audit_lines(tmp_path):
    path = tmp_path / "egress.jsonl"
    return not path.exists() or not path.read_text().strip()


class TestDocumentedGaps:
    """The README, PRIVACY.md, and modal copy list the remaining gaps:
    ``aiohttp``, ``urllib3`` / ``httpcore`` / ``httpcore2`` used directly,
    the hop to a configured proxy, a library installed after the guard
    ran, a requests session or adapter created before it and a urllib
    opener built around it, raw socket, subprocess shells. These
    tests pin the gaps so a future PR that tries to close them
    (excellent!) triggers a tripwire saying "update the docs and modal
    too."

    ``httpx`` WAS on this list; it is wrapped now, and so is its
    successor ``httpx2`` (see test_httpx_is_guarded_in_airgapped, which
    pins the closure for every installed httpx-family library instead).
    """

    def test_raw_socket_connect_bypasses_guard(self, monkeypatch, tmp_path):
        """Raw ``socket.socket().connect((...))`` is NOT wrapped.

        Pin: the guard does not intercept raw sockets. Closing this
        gap requires a `socket.socket.connect` monkey-patch, which
        would also break loopback connections that the underlying
        urllib3/requests stack itself uses.

        Test strategy: connect to 1.1.1.1:443 with a 0.05s timeout.
        If we don't have internet, we get a TimeoutError. If we do,
        we get a successful connection. Either way, NO EgressBlocked
        — the guard is fundamentally not invoked on raw sockets.
        """
        monkeypatch.setenv("LAB_MODE", "airgapped")
        monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path))
        arail.egress._reset_for_tests()
        arail.egress.install_guard()

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.05)
        # We don't care if the connect succeeds — only that EgressBlocked
        # is NOT raised. (It can't be: the guard doesn't see raw sockets.)
        try:
            s.connect(("1.1.1.1", 443))
        except arail.airgap.EgressBlocked:
            pytest.fail(
                "DOCUMENTED-GAP TRIPWIRE: raw socket.connect raised "
                "EgressBlocked — has someone wrapped sockets? Update "
                "PRIVACY.md, README, and the airgap modal copy."
            )
        except OSError:
            pass  # any connection error/timeout is expected
        finally:
            s.close()

    @pytest.mark.parametrize("lib", installed_independently())
    def test_httpx_is_guarded_in_airgapped(self, lib, monkeypatch, tmp_path):
        """The httpx family is no longer a gap — install_guard() patches
        the transports of every installed member (``httpx``, ``httpx2``;
        sprint airgapped-mode-toggles, then TD50), which is what stops
        the cloud SDKs and the Hugging Face downloader from egressing
        while airgapped.

        Pin, once per library that is actually installed (independently
        computed, never skipped for being absent): in airgapped mode, a
        call to a public host raises EgressBlocked before any socket
        I/O. If this starts failing, the guard regressed for that
        library — that reopens the primary cloud-SDK leak; fix the
        guard, don't relax this test.
        """
        mod = importlib.import_module(lib)

        monkeypatch.setenv("LAB_MODE", "airgapped")
        monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path))
        # Deterministic locality check, no resolver (see PUBLIC_TEST_IP); the
        # request itself targets a .invalid name, so a regression is a fast
        # ConnectError, never a connection to a real host.
        monkeypatch.setattr(socket, "gethostbyname", lambda h: PUBLIC_TEST_IP)
        arail.egress._reset_for_tests()
        arail.egress.install_guard()

        client = mod.Client(timeout=0.05)
        try:
            with pytest.raises(arail.airgap.EgressBlocked):
                client.get(PROBE_URL)
        finally:
            client.close()
            arail.egress._reset_for_tests()

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="curl-via-subprocess test relies on POSIX shell-finding curl",
    )
    def test_subprocess_curl_bypasses_guard(self, monkeypatch, tmp_path):
        """``subprocess.run(['curl', '-s', URL])`` is NOT wrapped.

        Pin: the guard lives in-process; spawning a curl subprocess
        cannot be intercepted by Python-layer monkey-patches. Closing
        this gap would require shell-wrapping which is out of scope
        for the v1 sprint.
        """
        monkeypatch.setenv("LAB_MODE", "airgapped")
        monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path))
        arail.egress._reset_for_tests()
        arail.egress.install_guard()

        # Use a localhost target that won't actually fire — we just want
        # to confirm the subprocess call itself does NOT raise EgressBlocked.
        try:
            result = subprocess.run(
                ["curl", "-s", "--max-time", "0.05", "http://127.0.0.1:65535/x"],
                capture_output=True,
                timeout=2.0,
            )
            assert result is not None
        except arail.airgap.EgressBlocked:
            pytest.fail(
                "DOCUMENTED-GAP TRIPWIRE: subprocess curl raised EgressBlocked. "
                "Has someone wrapped subprocess? Update README/PRIVACY.md/modal."
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    def test_os_system_bypasses_guard(self, monkeypatch, tmp_path):
        """``os.system("curl https://...")`` is NOT wrapped.

        Pin: same shape as subprocess — Python-layer guard cannot see
        what the shell does. Documented gap.
        """
        monkeypatch.setenv("LAB_MODE", "airgapped")
        monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path))
        arail.egress._reset_for_tests()
        arail.egress.install_guard()

        try:
            os.system("true")  # no-op; just verify os.system doesn't trip guard
        except arail.airgap.EgressBlocked:
            pytest.fail(
                "DOCUMENTED-GAP TRIPWIRE: os.system raised EgressBlocked. "
                "If shell wrapping landed, update PRIVACY.md and modal."
            )

    def test_urllib3_used_directly_bypasses_guard(self, monkeypatch, tmp_path):
        """``urllib3`` called directly is NOT wrapped (TD53).

        Pin: the guard sits one layer up, in ``requests`` and the httpx
        family. A request made with ``urllib3.PoolManager`` to a public
        name is never seen by it: the call ends in urllib3's own
        resolution error (the name is reserved and never resolves), not
        in EgressBlocked, and writes no audit line.
        """
        import urllib3

        monkeypatch.setenv("LAB_MODE", "airgapped")
        monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path))
        _make_invalid_names_unresolvable(monkeypatch)
        arail.egress._reset_for_tests()
        arail.egress.install_guard()

        try:
            urllib3.PoolManager(retries=False).request("GET", PROBE_URL, timeout=3)
        except arail.airgap.EgressBlocked:
            pytest.fail(_GAP_CLOSED.format(what="urllib3.PoolManager().request"))
        except Exception:
            pass  # NameResolutionError: it reached urllib3's own network layer, as documented
        else:
            pytest.fail("a request to a .invalid name succeeded; the premise of this test (it never resolves) is false")
        assert _no_audit_lines(tmp_path), "a direct urllib3 call should leave no audit line"

    @pytest.mark.parametrize("core", INSTALLED_CORES)
    def test_httpcore_used_directly_bypasses_guard(self, core, monkeypatch, tmp_path):
        """``httpcore`` / ``httpcore2`` called directly is NOT wrapped (TD53).

        Pin: they are the layer under the httpx family, below the transport
        classes the guard patches. ``<core>.request(...)`` to a public name
        ends in the library's own connect error and writes no audit line.
        """
        mod = importlib.import_module(core)

        monkeypatch.setenv("LAB_MODE", "airgapped")
        monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path))
        _make_invalid_names_unresolvable(monkeypatch)
        arail.egress._reset_for_tests()
        arail.egress.install_guard()

        timeouts = {"connect": 3, "read": 3, "write": 3, "pool": 3}
        try:
            mod.request("GET", PROBE_URL, extensions={"timeout": timeouts})
        except arail.airgap.EgressBlocked:
            pytest.fail(_GAP_CLOSED.format(what=f"{core}.request"))
        except Exception:
            pass  # ConnectError: it reached the library's own network layer, as documented
        else:
            pytest.fail("a request to a .invalid name succeeded; the premise of this test (it never resolves) is false")
        assert _no_audit_lines(tmp_path), f"a direct {core} call should leave no audit line"

    @pytest.mark.parametrize("shape", [
        "session-built-before-the-guard",
        "adapter-class-imported-before-the-guard",
        "opener-from-build_opener",
    ])
    def test_objects_that_exist_before_the_guard_and_custom_openers_are_not_checked(self, shape, monkeypatch, tmp_path):
        """BY-5.  The guard SWAPS DEFAULTS; it does not reach what already exists (REVIEW.md W1).

        Pin, three shapes, each past the guard on purpose:

        - a ``requests.Session()`` built before ``install_guard()`` holds the stock adapter;
        - a session built after it that mounts the ``HTTPAdapter`` class a library imported before it
          (``from requests.adapters import HTTPAdapter`` at module top);
        - ``urllib.request.build_opener().open(...)``: the guard installs a global opener for
          ``urlopen``; an opener you build has the stock handlers.

        Each ends in the library's own connection error (the name is reserved and never resolves), not
        EgressBlocked, and writes no audit line. The httpx family has no such gap (it is patched on
        its classes: FR-1 "built before" and EG-3). In tree today nothing is in these shapes at
        runtime (measured when the gap was found: importing the portal creates no Session and no
        opener before its start-up installs the guard), but a third-party agent could be.
        """
        monkeypatch.setenv("LAB_MODE", "airgapped")
        monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path))
        _make_invalid_names_unresolvable(monkeypatch)
        arail.egress._reset_for_tests()

        stock_adapter = requests.adapters.HTTPAdapter  # what a library imported at module top holds
        assert stock_adapter is not arail.egress.GuardedHTTPAdapter, "this test starts from a polluted process"
        pre_guard_session = requests.Session()
        arail.egress.install_guard()
        assert requests.adapters.HTTPAdapter is arail.egress.GuardedHTTPAdapter  # the guard IS in

        def attempt():
            if shape == "session-built-before-the-guard":
                return pre_guard_session.get(PROBE_URL, timeout=3)
            if shape == "adapter-class-imported-before-the-guard":
                session = requests.Session()  # built after the guard...
                session.mount("http://", stock_adapter(max_retries=0))  # ...with a class from before it
                return session.get(PROBE_URL, timeout=3)
            return urllib.request.build_opener().open(PROBE_URL, timeout=3)

        try:
            attempt()
        except arail.airgap.EgressBlocked:
            pytest.fail(_EXISTING_OBJECTS_REACHED.format(what=shape))
        except Exception:
            pass  # requests.ConnectionError / urllib.error.URLError: it reached the network layer, as documented
        else:
            pytest.fail("a request to a .invalid name succeeded; the premise of this test (it never resolves) is false")
        assert _no_audit_lines(tmp_path), f"{shape} should leave no audit line"

    @pytest.mark.parametrize("lib", installed_independently())
    def test_a_library_installed_after_the_guard_is_not_guarded_until_restart(self, lib, monkeypatch, tmp_path):
        """BY-6.  A family member that becomes importable AFTER ``install_guard()`` ran is NOT guarded.

        Pin: the guard looks at what is installed once. ``coverage()`` recorded the member as
        ``"absent"`` (true then), and a second ``install_guard()`` is a no-op because a complete
        install short-circuits, so a library installed while the lab runs stays open until the
        process restarts. Reachable: ``/api/plugins/install`` runs ``pip install -r requirements.txt``
        into the portal's own interpreter, and subprocess pip is itself a documented gap. The docs say
        so (PRIVACY.md, the ``coverage()`` and ``_install_httpx_family_guard`` docstrings) and the
        plugin installer's message says to restart the lab (REVIEW.md W3).

        Simulated without installing anything: the library is hidden (``sys.modules[lib] = None``)
        while the guard is installed, then made visible again.
        """
        mod = importlib.import_module(lib)
        monkeypatch.setenv("LAB_MODE", "airgapped")
        monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path))
        _make_invalid_names_unresolvable(monkeypatch)
        arail.egress._reset_for_tests()

        monkeypatch.setitem(sys.modules, lib, None)  # "not installed yet"
        arail.egress.install_guard()
        assert arail.egress.coverage()[lib] == "absent", arail.egress.coverage()
        monkeypatch.setitem(sys.modules, lib, mod)  # ...and now a plugin's pip install has put it there
        arail.egress.install_guard()  # a second install: still a no-op

        closed = (
            "DOCUMENTED-GAP TRIPWIRE: a library that became importable after install_guard() ran is now "
            "guarded. That is good news (TD57). Update docs/PRIVACY.md, the docstrings of "
            "egress.coverage() and egress._install_httpx_family_guard(), and the plugin installer's "
            "'restart the lab' message, then turn this pin into a closed-path test."
        )
        assert arail.egress.coverage()[lib] == "absent", closed
        assert not getattr(vars(mod.HTTPTransport)["handle_request"], "_arail_guarded", False), closed
        try:
            mod.Client(timeout=3).get(PROBE_URL)
        except arail.airgap.EgressBlocked:
            pytest.fail(closed)
        except Exception:
            pass  # ConnectError: it reached the library's own network layer, as documented
        else:
            pytest.fail("a request to a .invalid name succeeded; the premise of this test (it never resolves) is false")
        assert _no_audit_lines(tmp_path), "a library the guard never saw should leave no audit line"

    @pytest.mark.parametrize("client", ["requests", *installed_independently()])
    def test_a_configured_proxy_is_not_checked(self, client, monkeypatch, tmp_path):
        """The hop to a configured proxy is NOT checked (TD54).

        Pin: the guard decides on the DESTINATION. A request for a local
        address (``127.0.0.1:9``) passes it, and if the client was
        configured with a proxy at a public name, the client then connects
        to that proxy. Here the proxy is a ``.invalid`` name, so the call
        ends in a proxy or connect error rather than EgressBlocked, and the
        guard writes no audit line. (The reverse, a loopback proxy and a
        public destination, is refused: tests/test_egress_httpx.py EG-11.)
        """
        monkeypatch.setenv("LAB_MODE", "airgapped")
        monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path))
        _make_invalid_names_unresolvable(monkeypatch)
        arail.egress._reset_for_tests()
        arail.egress.install_guard()

        proxy = "http://egress-probe.invalid:8080"
        destination = "http://127.0.0.1:9/"
        try:
            if client == "requests":
                requests.get(destination, proxies={"http": proxy, "https": proxy}, timeout=3)
            else:
                importlib.import_module(client).Client(proxy=proxy, timeout=3).get(destination)
        except arail.airgap.EgressBlocked:
            pytest.fail(_GAP_CLOSED.format(what=f"a {client} request through a configured proxy"))
        except Exception:
            pass  # ProxyError / ConnectError: it tried the proxy, as documented
        else:
            pytest.fail("a request through an unresolvable proxy succeeded; the premise of this test is false")
        assert _no_audit_lines(tmp_path), "a local destination through a proxy should leave no audit line"


# ──────────────────────────────────────────────────────────────────────
# 4. asyncio.create_task contextvars leak from @allow_egress
# ──────────────────────────────────────────────────────────────────────

class TestAsyncioContextvarsLeak:
    """``asyncio.create_task`` copies the current contextvars context
    into the task. So a task spawned inside ``with allow_egress(...)``
    keeps the bypass active even after the with-block exits.

    This is documented in
    ``learnings/2026-05-05-allow-egress-task-scope.md`` as a known
    behavior. These tests pin it so a future change is caught.

    Note: ``allow_egress`` raises immediately in airgapped mode, so
    these tests run in hybrid mode (the only mode where the bypass
    can actually be entered)."""

    def test_create_task_inherits_allow_egress_context(self, monkeypatch, tmp_path):
        """A task spawned inside @allow_egress sees the bypass even
        after the with-block exits — pin documented behavior."""
        monkeypatch.setenv("LAB_MODE", "hybrid")
        monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path))
        arail.egress._reset_for_tests()
        arail.egress.install_guard()

        captured: dict = {}

        async def runner():
            async def child():
                # When the parent's with-block exits, this task should
                # STILL see the allow_egress reason because asyncio
                # copies contextvars at task-spawn time.
                await asyncio.sleep(0.01)
                captured["allow_var"] = arail.egress._allow_egress_var.get(None)

            with arail.egress.allow_egress("test reason for asyncio"):
                # Confirm the var is set inside the block.
                captured["inside_block"] = arail.egress._allow_egress_var.get(None)
                task = asyncio.create_task(child())
            # Block has exited. Var is reset for THIS frame.
            captured["after_block"] = arail.egress._allow_egress_var.get(None)
            await task

        asyncio.run(runner())

        assert captured["inside_block"] == "test reason for asyncio"
        assert captured["after_block"] is None, (
            "Outer frame's contextvar must reset on with-block exit"
        )
        # The child task's contextvar — pinned: it inherits the bypass.
        assert captured["allow_var"] == "test reason for asyncio", (
            "DOCUMENTED-BEHAVIOR PIN: asyncio.create_task captures the "
            "context at spawn, so the child task keeps the allow_egress "
            "reason after the parent's with-block exits. If this test "
            "fails, asyncio behavior changed OR a fix landed — update "
            "learnings/2026-05-05-allow-egress-task-scope.md."
        )

    def test_thread_does_NOT_inherit_allow_egress(self, monkeypatch, tmp_path):
        """``threading.Thread`` does NOT copy contextvars — only the
        current thread's context. Spawning a thread inside @allow_egress
        does NOT propagate the bypass.

        This is the safe behavior. Pin it so a future asyncio-style
        contextvars-on-threads change in CPython is caught.
        """
        import threading

        monkeypatch.setenv("LAB_MODE", "hybrid")
        monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path))
        arail.egress._reset_for_tests()
        arail.egress.install_guard()

        captured: dict = {}

        def child_thread():
            captured["thread_var"] = arail.egress._allow_egress_var.get(None)

        with arail.egress.allow_egress("test thread isolation"):
            t = threading.Thread(target=child_thread)
            t.start()
            t.join()

        assert captured["thread_var"] is None, (
            "Threads must NOT inherit allow_egress (contextvars are per-thread). "
            "If this test fails, CPython's contextvars semantics changed — "
            "update the learnings file."
        )


# ──────────────────────────────────────────────────────────────────────
# 5. Audit log — secret leakage via URL parse failure
# ──────────────────────────────────────────────────────────────────────

class TestAuditLogSecretLeakage:
    """Architect flagged: ``record_block`` falls back to ``url[:64]``
    if urlparse returns no hostname. Could a token-bearing URL slip
    into the audit log via this fallback?

    Test strategy: pass URLs with various malformations and verify
    that no token-bearing query string is written to egress.jsonl.
    """

    def test_token_bearing_url_does_not_leak_via_fallback(
        self, monkeypatch, tmp_path
    ):
        """URL with token in query string + valid hostname → audit log
        records hostname only, not the token."""
        monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path))
        log_path = tmp_path / "egress.jsonl"

        arail.egress.record_block(
            "https://api.example.com/v1?token=SECRET_TOKEN_DO_NOT_LEAK",
            "test_caller",
            "airgapped",
        )

        content = log_path.read_text()
        assert "SECRET_TOKEN_DO_NOT_LEAK" not in content, (
            "Audit log must not contain query-string secrets — "
            f"got: {content!r}"
        )
        # url_host should be just the hostname.
        entries = [json.loads(ln) for ln in content.splitlines() if ln.strip()]
        assert entries[-1]["url_host"] == "api.example.com"

    def test_malformed_url_fallback_truncated_to_64_chars(
        self, monkeypatch, tmp_path
    ):
        """A URL that fails to parse falls back to ``url[:64] or '?'``.
        Pin: fallback length is 64 chars max.

        The current urlparse is permissive enough that essentially any
        string that has a scheme returns *something* for hostname, so
        the fallback path is dead code in practice — but pin it.
        """
        monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path))
        log_path = tmp_path / "egress.jsonl"

        # Empty-ish or hostname-less URL — urlparse returns hostname=None.
        arail.egress.record_block("not://a/url", "test", "airgapped")

        content = log_path.read_text()
        entries = [json.loads(ln) for ln in content.splitlines() if ln.strip()]
        assert len(entries) >= 1
        # The recorded url_host should be derived from the URL string itself.
        # Even in fallback: max 64 chars, no full-url leak of tokens.
        assert len(entries[-1]["url_host"]) <= 64, (
            f"url_host must be <=64 chars; got {len(entries[-1]['url_host'])}"
        )

    def test_egress_blocked_str_does_not_contain_full_url(self):
        """The exception's string form must contain only host/caller/reason
        — never the full URL with query string."""
        err = arail.airgap.EgressBlocked(
            "api.example.com", "buddy.fetch", "airgapped"
        )
        s = str(err)
        # Must contain the host but NOT a path or query.
        assert "api.example.com" in s
        assert "?token=" not in s
        assert "/v1/" not in s
        assert "secret" not in s.lower()


# ──────────────────────────────────────────────────────────────────────
# 6. jsonl write failures (record_block must swallow)
# ──────────────────────────────────────────────────────────────────────

class TestJsonlWriteFailures:
    """``record_block`` must NEVER prevent the loud failure (the actual
    EgressBlocked raise). If the disk is full or the path is read-only,
    the block is best-effort logged but the raise still happens.
    Architect's failure-modes table flagged this row as untested."""

    def test_record_block_swallows_when_dir_unwritable(
        self, monkeypatch, tmp_path
    ):
        """Make ARAIL_DATA_DIR point at a read-only location. record_block
        must not raise."""
        ro_dir = tmp_path / "ro"
        ro_dir.mkdir()
        # On most POSIX systems chmod 0o500 makes dir non-writable.
        os.chmod(ro_dir, 0o500)
        monkeypatch.setenv("ARAIL_DATA_DIR", str(ro_dir))
        try:
            # Must NOT raise.
            arail.egress.record_block("https://x.com", "t", "airgapped")
        except Exception as e:
            pytest.fail(
                f"record_block must swallow write errors; raised: {e!r}"
            )
        finally:
            os.chmod(ro_dir, 0o700)  # restore for cleanup

    def test_record_block_swallows_when_path_is_a_file(self, monkeypatch, tmp_path):
        """Pathological: ARAIL_DATA_DIR is a *file* not a directory.
        record_block must not crash the caller."""
        weird_path = tmp_path / "data_is_a_file"
        weird_path.write_text("not a directory")
        monkeypatch.setenv("ARAIL_DATA_DIR", str(weird_path))
        try:
            arail.egress.record_block("https://x.com", "t", "airgapped")
        except Exception as e:
            pytest.fail(
                f"record_block must swallow when ARAIL_DATA_DIR is a file; "
                f"raised: {e!r}"
            )

    def test_egress_blocked_still_raises_when_logging_fails(
        self, monkeypatch, tmp_path
    ):
        """Even when record_block silently fails, EgressBlocked must
        still be raised by the guard. This is the load-bearing
        invariant: airgapped is a contract; logging is best-effort."""
        ro_dir = tmp_path / "ro_logs"
        ro_dir.mkdir()
        os.chmod(ro_dir, 0o500)
        monkeypatch.setenv("LAB_MODE", "airgapped")
        monkeypatch.setenv("ARAIL_DATA_DIR", str(ro_dir))
        monkeypatch.setattr(socket, "gethostbyname", lambda h: "151.101.64.81")
        arail.egress._reset_for_tests()
        arail.egress.install_guard()
        try:
            with pytest.raises(arail.airgap.EgressBlocked):
                requests.get("https://example.com", timeout=1)
        finally:
            os.chmod(ro_dir, 0o700)


# ──────────────────────────────────────────────────────────────────────
# 7. QA pass of sprint 2026-11-01-arail-egress-httpx2: where the guard's
#    view ends.  The guard patches CLASS objects and installs ONE urllib
#    opener, at install time, and reads LAB_MODE on every request.  Each
#    test below pins one side of that boundary.  The gap pins were found
#    by the QA pass (sprints/2026-11-01-arail-egress-httpx2/TEST_REPORT.md)
#    and, when written, were not yet listed in docs/PRIVACY.md.
# ──────────────────────────────────────────────────────────────────────

_QA_GAP_CLOSED = (
    "GAP TRIPWIRE ({finding}): {what} raised EgressBlocked, so the guard now covers it. That is good "
    "news. Take it off the list of what the guard does not catch in docs/PRIVACY.md (and README / "
    "airgap.KNOWN_GAPS if they name it), see TEST_REPORT.md {finding}, then turn this pin into a "
    "closed-path test."
)


def _audit_count(tmp_path):
    path = tmp_path / "egress.jsonl"
    return sum(1 for line in path.read_text().splitlines() if line.strip()) if path.exists() else 0


class TestQaWhereTheGuardsViewEnds:
    """The httpx family is patched on its classes, which covers every client and transport OBJECT,
    whenever it was built (EG-3, FR-1).  It does not cover a reference to the original method taken
    before the patch, or a class created after it.  urllib is covered through the opener that
    ``install_guard()`` installs, and only through it.  The mode is read per request."""

    @pytest.mark.parametrize("lib", installed_independently())
    @pytest.mark.parametrize("shape", [
        "bound-method-taken-before-the-guard",
        "function-taken-before-the-guard",
        "subclass-that-copied-the-method-before-the-guard",
    ])
    def test_a_reference_to_the_transport_method_taken_before_the_guard_is_not_checked(
        self, lib, shape, monkeypatch, tmp_path
    ):
        """QA-N1 (pin).  ``t.handle_request`` (a bound method), ``HTTPTransport.handle_request`` (the
        function) and a subclass whose body says ``handle_request = HTTPTransport.handle_request``, each
        taken BEFORE ``install_guard()``, still point at the library's own function, so a request made
        through them is never checked.  The contrast is covered and asserted at the end: the same
        pre-install transport OBJECT, with the method looked up when the request is made, is refused.
        docs/PRIVACY.md says the httpx family "has no such gap" (no object that already exists escapes);
        that is true for objects, not for references to the method."""
        mod = importlib.import_module(lib)
        monkeypatch.setenv("LAB_MODE", "airgapped")
        monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(socket, "gethostbyname", lambda host: PUBLIC_TEST_IP)
        _make_invalid_names_unresolvable(monkeypatch)
        arail.egress._reset_for_tests()

        transport = mod.HTTPTransport()
        bound = transport.handle_request
        function = mod.HTTPTransport.handle_request

        class CopiedBeforeTheGuard(mod.HTTPTransport):
            handle_request = mod.HTTPTransport.handle_request

        arail.egress.install_guard()
        assert arail.egress.coverage()[lib] == "guarded"
        request = mod.Request("GET", PROBE_URL)
        other = mod.HTTPTransport()
        attempts = {
            "bound-method-taken-before-the-guard": lambda: bound(request),
            "function-taken-before-the-guard": lambda: function(other, request),
            "subclass-that-copied-the-method-before-the-guard": (
                lambda: mod.Client(transport=CopiedBeforeTheGuard(), timeout=3).get(PROBE_URL)
            ),
        }
        try:
            attempts[shape]()
        except arail.airgap.EgressBlocked:
            pytest.fail(_QA_GAP_CLOSED.format(finding="QA-N1", what=shape))
        except Exception:
            pass  # ConnectError: the library's own transport ran, as pinned
        else:
            pytest.fail("a request to a .invalid name succeeded; the premise of this test (it never resolves) is false")
        assert _no_audit_lines(tmp_path), f"{shape} should leave no audit line"

        with pytest.raises(arail.airgap.EgressBlocked):  # the covered contrast: an object, looked up now
            transport.handle_request(request)
        transport.close()
        other.close()

    @pytest.mark.parametrize("shape", [
        "urlopen-with-an-ssl-context-https",
        "urlopen-with-an-ssl-context-http",
        "http.client-used-directly",
        "another-library-replaced-the-global-opener",
    ])
    def test_urllib_requests_that_do_not_go_through_the_installed_opener_are_not_checked(
        self, shape, monkeypatch, tmp_path
    ):
        """QA-W1 (pin).  ``install_guard()`` installs one opener, the one ``urlopen(url)`` uses.  Three
        everyday shapes do not use it:

        - ``urlopen(url, context=ssl_context)`` builds a NEW opener with stock handlers (CPython's own
          ``urlopen``; ``cafile=``/``capath=`` do the same on Python < 3.13), for http and https;
        - ``http.client`` (the layer under urllib.request, as urllib3 is under requests) called directly;
        - any later ``urllib.request.install_opener(...)`` replaces the guarded opener for everyone.

        Each ends in the stdlib's own resolution error (a ``.invalid`` name, made unresolvable here),
        never EgressBlocked, and writes no audit line.  The contrast (the installed opener) is refused.
        README, docs/PRIVACY.md, the modal and airgap.KNOWN_GAPS scope the claim to ``urlopen`` without an
        SSL context and list these three as a gap (tests/test_egress_docs.py holds the words)."""
        import http.client
        import ssl

        monkeypatch.setenv("LAB_MODE", "airgapped")
        monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(socket, "gethostbyname", lambda host: PUBLIC_TEST_IP)
        _make_invalid_names_unresolvable(monkeypatch)
        # The global opener is process state no reset puts back; restore whatever was there.
        monkeypatch.setattr(urllib.request, "_opener", urllib.request._opener)
        arail.egress._reset_for_tests()
        arail.egress.install_guard()

        with pytest.raises(arail.airgap.EgressBlocked):  # the covered contrast
            urllib.request.urlopen(PROBE_URL, timeout=3)
        before = _audit_count(tmp_path)
        https_url = "https://" + PROBE_URL.split("://", 1)[1]

        def attempt():
            if shape == "urlopen-with-an-ssl-context-https":
                return urllib.request.urlopen(https_url, timeout=3, context=ssl.create_default_context())
            if shape == "urlopen-with-an-ssl-context-http":
                return urllib.request.urlopen(PROBE_URL, timeout=3, context=ssl.create_default_context())
            if shape == "http.client-used-directly":
                connection = http.client.HTTPSConnection("egress-probe.invalid", timeout=3)
                try:
                    return connection.request("GET", "/")
                finally:
                    connection.close()
            urllib.request.install_opener(urllib.request.build_opener())
            return urllib.request.urlopen(PROBE_URL, timeout=3)

        try:
            attempt()
        except arail.airgap.EgressBlocked:
            pytest.fail(_QA_GAP_CLOSED.format(finding="QA-W1", what=shape))
        except Exception:
            pass  # URLError / gaierror: the stdlib's own network layer ran, as pinned
        else:
            pytest.fail("a request to a .invalid name succeeded; the premise of this test (it never resolves) is false")
        assert _audit_count(tmp_path) == before, f"{shape} should add no audit line"

    @pytest.mark.parametrize("lib", installed_independently())
    def test_a_library_whose_classes_are_created_again_after_the_guard_is_not_guarded(self, lib, tmp_path):
        """QA-N2 (pin), in a fresh interpreter because it re-imports the library.  The guard patched the
        class objects that existed at install.  Purge the library from ``sys.modules`` and import it
        again (a hot reloader, a "reinstall" path) and its classes are new objects: not patched, a
        request through them is not checked, a second ``install_guard()`` is a no-op, and
        ``coverage()`` (which the doctor and /api/airgap/status read) still says "guarded".  No code in
        the lab does this today; BY-6 pins the sibling case (a library installed after the guard)."""
        code = r"""
import importlib, json, socket, sys
lib = sys.argv[1]
mod = importlib.import_module(lib)
import arail.egress as egress
egress.install_guard()
old = mod.HTTPTransport
for name in [m for m in list(sys.modules) if m == lib or m.startswith(lib + ".")]:
    del sys.modules[name]
new = importlib.import_module(lib)
egress.install_guard()
def no_dns(*args, **kwargs):
    raise socket.gaierror(-2, "no DNS in this test")
socket.getaddrinfo = no_dns
try:
    new.Client(timeout=3).get("http://egress-probe.invalid/")
    outcome = "no exception"
except Exception as exc:
    outcome = "EgressBlocked" if type(exc).__name__ == "EgressBlocked" else "other: " + type(exc).__name__
print(json.dumps({
    "new_class": new.HTTPTransport is not old,
    "new_class_guarded": bool(getattr(vars(new.HTTPTransport)["handle_request"], "_arail_guarded", False)),
    "coverage": egress.coverage()[lib],
    "outcome": outcome,
    "egress_file": egress.__file__,
}))
"""
        result = run_fresh(code, lib, tmp_path=tmp_path)
        assert result["new_class"], result
        assert result["coverage"] == "guarded", result  # what the doctor and the status endpoint report
        closed = _QA_GAP_CLOSED.format(finding="QA-N2", what=f"a re-imported {lib}")
        assert not result["new_class_guarded"], closed
        assert result["outcome"].startswith("other: "), (closed if result["outcome"] == "EgressBlocked" else result)
        audit = tmp_path / "data" / "egress.jsonl"
        assert not audit.exists() or not audit.read_text().strip(), "a class the guard never saw should leave no audit line"

    @pytest.mark.parametrize("client", ["requests", *installed_independently()])
    def test_lab_mode_is_read_per_request_by_clients_that_already_exist(self, client, monkeypatch, tmp_path):
        """Closed path (item 3 of the QA brief).  The airgap toggle writes ``os.environ["LAB_MODE"]`` (and
        ``ARAIL_MODE``) in the running portal.  A client, async client or session that already exists
        must follow it on its next request, in both directions, because the decision is taken per
        request.  The transports are stubbed before the guard, so "passed the guard" is a marker, not
        a network call."""

        class Reached(Exception):
            pass

        def _reached(*args, **kwargs):
            raise Reached()

        async def _reached_async(*args, **kwargs):
            raise Reached()

        monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(socket, "gethostbyname", lambda host: PUBLIC_TEST_IP)
        if client == "requests":
            monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", _reached)
        else:
            mod = importlib.import_module(client)
            monkeypatch.setattr(mod.HTTPTransport, "handle_request", _reached)
            monkeypatch.setattr(mod.AsyncHTTPTransport, "handle_async_request", _reached_async)

        def flip(mode):
            monkeypatch.setenv("LAB_MODE", mode)
            monkeypatch.setenv("ARAIL_MODE", mode)

        flip("hybrid")
        arail.egress._reset_for_tests()
        arail.egress.install_guard()
        url = "http://api.example-public.invalid/v1"
        if client == "requests":
            session = requests.Session()
            calls = [lambda: session.get(url, timeout=3)]
        else:
            sync_client, async_client = mod.Client(timeout=3), mod.AsyncClient(timeout=3)
            calls = [lambda: sync_client.get(url), lambda: asyncio.run(async_client.get(url))]

        for call in calls:
            with pytest.raises(Reached):  # hybrid: passes the guard
                call()
        flip("airgapped")  # what POST /api/airgap/toggle does in-process
        for call in calls:
            with pytest.raises(arail.airgap.EgressBlocked):  # the SAME objects are now refused
                call()
        flip("hybrid")
        for call in calls:
            with pytest.raises(Reached):  # and pass again
                call()
        assert _audit_count(tmp_path) == len(calls), "one refusal per airgapped call, nothing else"
