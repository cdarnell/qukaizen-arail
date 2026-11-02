"""egress — Python-level outbound network guard.

Installs a guarded requests HTTPAdapter, a guarded urllib opener, and a
check on the transports of every installed httpx-family library (``httpx``
and its successor ``httpx2``, named once in ``arail.httpx_family``), each
of which consults the airgap policy before a request leaves.

The httpx-family check is patched onto ``HTTPTransport.handle_request`` and
``AsyncHTTPTransport.handle_async_request`` at class level, so it also
covers clients built before install_guard() ran and the clients SDKs build
for themselves (huggingface_hub, anthropic, openai). In-process transports
(ASGITransport, WSGITransport, MockTransport, starlette's TestClient) are
other classes and are left alone. Not covered (docs/PRIVACY.md): aiohttp;
urllib3 or httpcore/httpcore2 used directly; raw sockets; subprocesses; the
hop to a configured proxy (the decision is made on the destination).

If an httpx-family library is installed but cannot be patched,
install_guard() patches everything else and raises EgressGuardIncomplete:
a library the guard cannot see must never be a silent hole.

Usage (called automatically at portal startup and agent-loader time):

    from arail import egress
    egress.install_guard()

The guard is idempotent — subsequent calls are no-ops.  Tests that need
a fresh install call ``egress._reset_for_tests()``.

Bypass context manager (hybrid mode only):

    with egress.allow_egress("test the openrouter endpoint"):
        r = requests.get("https://openrouter.ai/api/v1/models", ...)

In airgapped mode, ``allow_egress`` raises ``EgressBlocked`` immediately
on entry — there is no escape hatch.  The only intentional exception is
``BUDDY_EGRESS_PROBE=1``, which uses a raw socket and never touches this
context manager (see ``probe_internet``).
"""

from __future__ import annotations

import contextlib
import importlib
import inspect
import json
import logging
import re
import os
import socket
import sys
import threading
import time
import types
import urllib.request
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import requests
import requests.adapters
import requests.sessions

from arail import httpx_family
from arail.airgap import EgressBlocked, is_airgapped, is_local_host, is_local_ip

try:
    from contextvars import ContextVar
except ImportError:  # pragma: no cover — Python < 3.7 guard
    raise RuntimeError("arail.egress requires Python 3.7+")

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

# Thread- and task-safe flag: when set, the current call stack is inside an
# @allow_egress context.  The value is the *reason* string supplied by the
# caller.  None means "no bypass active."
_allow_egress_var: ContextVar[Optional[str]] = ContextVar(
    "_allow_egress_var", default=None
)

# Idempotency guard — install_guard() is a no-op after a COMPLETE install.  It stays False after an
# install that raised EgressGuardIncomplete, so every later call retries and raises again.
_INSTALLED: bool = False

# Guards install_guard() and _reset_for_tests(): two threads installing at once must patch each
# class once and must not save the first thread's GuardedHTTPAdapter as "the original".
_LOCK = threading.RLock()

# The original HTTPAdapter class before we monkeypatched it.
_ORIGINAL_HTTP_ADAPTER: type = requests.adapters.HTTPAdapter

# What the httpx-family patch replaced: (class, attribute) -> (owned, original).  ``owned`` is True
# when the attribute was in the class's own __dict__ (``original`` is that exact object, put back
# with setattr on reset) and False when the class only inherited it (reset deletes the attribute
# again).  Keyed by class, not by library name, so one class reached under two names (httpx2's
# alias_httpx(), a re-export) is patched once and restored once.
_PATCHED: dict[tuple[type, str], tuple[bool, object]] = {}

# What the last install attempt found, per client: "guarded", "absent", or "unguarded: <why>".
# Rebound (never mutated) by install_guard(), so a reader always sees a complete snapshot.
_COVERAGE: dict[str, str] = {}
_UNGUARDED = "unguarded: "

# What the docs must name: the clients the guard covers.
GUARDED_CLIENTS: tuple[str, ...] = ("requests", "urllib.request", *httpx_family.FAMILY)

# Process-level cache for the internet probe result {result: bool, ts: float}.
_PROBE_CACHE: dict = {}
_PROBE_CACHE_TTL: float = 60.0

def invalidate_probe_cache() -> None:
    """Clear the host-internet probe cache.

    Called by the airgap toggle endpoint after a successful mode flip so
    the modal's 'host can reach internet' row reflects post-flip reality
    on the next /api/airgap/status fetch. The cache key is time-only
    (not mode-keyed), but the modal renders both fields together; users
    expect both to refresh on toggle.

    Thread-safe under the GIL for a dict.clear().
    """
    _PROBE_CACHE.clear()


# Path to the egress audit log (overridable for tests).
def _lab_data() -> Path:
    return Path(os.getenv("ARAIL_DATA_DIR", "lab/data"))


# ---------------------------------------------------------------------------
# Caller identification
# ---------------------------------------------------------------------------

_SKIP_MODULES = frozenset({
    "arail.egress",
    "arail.airgap",
    "requests.adapters",
    "requests.sessions",
    "requests.models",
    "requests.api",
    "urllib.request",
    "urllib3.connectionpool",
    "urllib3.connection",
    # The httpx family, spelled out from the one list, and the layer under it.  Matching used to be
    # str.startswith, which skipped httpx2 only because "httpx2".startswith("httpx") and would have
    # skipped any unrelated module whose name merely began with one of these.  httpcore frames are
    # never on the stack when the check runs; they are named so the set says the family on purpose.
    *httpx_family.FAMILY,
    "httpcore",
    "httpcore2",
    "anthropic",
})


def _is_guard_frame(module: str) -> bool:
    """True if ``module`` is an entry of _SKIP_MODULES or lives inside one (``entry.sub.module``)."""
    return any(module == entry or module.startswith(entry + ".") for entry in _SKIP_MODULES)


def _current_caller() -> str:
    """Walk the stack and return the first frame outside the guard modules.

    A guard frame is an exact _SKIP_MODULES name or a dotted child of one.  Falls back to
    ``"unknown"`` if no useful frame is found.
    """
    try:
        for frame_info in inspect.stack():
            module = frame_info.frame.f_globals.get("__name__", "") or ""
            if module and not _is_guard_frame(module):
                qualname = frame_info.frame.f_code.co_qualname if hasattr(
                    frame_info.frame.f_code, "co_qualname"
                ) else frame_info.frame.f_code.co_name
                return f"{module}.{qualname}"
    except Exception:  # noqa: BLE001
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def record_block(url: str, caller: str, reason: str) -> None:
    """Append one structured line to ``lab/data/egress.jsonl``.

    Schema::

        {"ts": "2026-05-05T14:33:01Z",
         "url_host": "huggingface.co",
         "caller": "arail.agents._builtin_buddy._suggest_internet_correlation",
         "reason": "airgapped",
         "lab_mode": "airgapped"}

    Errors are caught and logged to stderr — the block itself still raises
    in the caller; logging failure must NOT prevent loud enforcement.

    Rotation: if file > 5 MB, rename to ``.1`` (overwriting any existing
    ``.1``). Then create a new empty file.
    """
    try:
        parsed = urlparse(url)
        url_host = parsed.hostname or url[:64] or "?"
    except Exception:  # noqa: BLE001
        url_host = "?"

    from arail.airgap import lab_mode as _lab_mode
    line = json.dumps({
        "ts": _utcnow(),
        "url_host": url_host,
        "caller": caller,
        "reason": reason,
        "lab_mode": _lab_mode(),
    }) + "\n"
    _write_jsonl_line(line)


def record_allow(url: str, caller: str, reason: str) -> None:
    """Audit trail for ``@allow_egress`` contexts that actually fired.

    Same schema as ``record_block`` but with ``reason='allow:<context-reason>'``.
    """
    try:
        parsed = urlparse(url)
        url_host = parsed.hostname or url[:64] or "?"
    except Exception:  # noqa: BLE001
        url_host = "?"

    from arail.airgap import lab_mode as _lab_mode
    line = json.dumps({
        "ts": _utcnow(),
        "url_host": url_host,
        "caller": caller,
        "reason": f"allow:{reason}",
        "lab_mode": _lab_mode(),
    }) + "\n"
    _write_jsonl_line(line)


def _utcnow() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_jsonl_line(line: str) -> None:
    """Write one line to egress.jsonl, rotating if > 5 MB."""
    try:
        lab_data = _lab_data()
        lab_data.mkdir(parents=True, exist_ok=True)
        path = lab_data / "egress.jsonl"

        # Rotate if oversized.
        if path.exists() and path.stat().st_size > 5 * 1024 * 1024:
            rotated = path.with_suffix(".jsonl.1")
            try:
                path.rename(rotated)
            except OSError as e:
                sys.stderr.write(f"[arail.egress] rotation failed: {e}\n")
                # Still attempt to write to the (possibly oversized) file.

        with path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.flush()

        # chmod 0640 — readable by owner; group-read; no others.
        try:
            path.chmod(0o640)
        except OSError:
            pass  # Windows / containerized paths — best effort

    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[arail.egress] record failed: {e}\n")


def read_recent_blocks(n: int = 5) -> list[dict]:
    """Bounded read for the modal — last N entries by timestamp.

    Reads the tail of ``egress.jsonl`` using a chunked-from-end strategy
    (seek to end, read up to ~64KB, split lines, take last N).  Never
    full-file slurp.  If file missing or unreadable, returns ``[]``.
    """
    path = _lab_data() / "egress.jsonl"
    if not path.exists():
        return []
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            f.seek(max(0, size - 64 * 1024))
            tail = f.read().decode("utf-8", errors="replace")
        lines = [ln for ln in tail.splitlines() if ln.strip()]
        # If we sliced mid-line, drop the (partial) first line.
        if size > 64 * 1024 and lines:
            lines = lines[1:]
        out = []
        for ln in lines[-n:]:
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
        return out
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# Egress decision (shared logic for both adapter and urllib handler)
# ---------------------------------------------------------------------------

def _check_egress_or_raise(url: str) -> None:
    """Run the decision tree; raise EgressBlocked if denied."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
    except Exception:
        host = ""

    # 1. Local hosts always pass.
    if host and (is_local_host(host) or is_local_ip(host)):
        return

    # 2. Allow-context wins if active (will be audit-logged by the caller).
    active = _allow_egress_var.get(None)
    if active is not None:
        return  # caller logs the allow via record_allow

    # 2b. Consent-scoped bootstrap fetch: host-allowlisted, audited here.
    bootstrap_reason = _bootstrap_allows(host)
    if bootstrap_reason is not None:
        record_allow(url, _current_caller(), bootstrap_reason)
        return

    # 3. Airgapped + non-local + no allow → deny loudly.
    if is_airgapped():
        caller = _current_caller()
        record_block(url, caller, "airgapped")
        raise EgressBlocked(host or "?", caller, "airgapped")

    # 4. Hybrid + non-local + no allow → pass through.


# ---------------------------------------------------------------------------
# Guarded HTTP adapter (requests)
# ---------------------------------------------------------------------------

class GuardedHTTPAdapter(requests.adapters.HTTPAdapter):
    """HTTPAdapter that consults the airgap policy before sending."""

    def send(self, request, **kwargs):  # type: ignore[override]
        url = request.url or ""
        try:
            parsed = urlparse(url)
            host = parsed.hostname or ""
        except Exception:
            host = ""

        # 1. Local hosts always pass — that's the LAN-GPU-box rule.
        if host and (is_local_host(host) or is_local_ip(host)):
            return super().send(request, **kwargs)

        # 2. Allow-context wins if active (audit-logged).
        active = _allow_egress_var.get(None)
        if active is not None:
            record_allow(url, _current_caller(), active)
            return super().send(request, **kwargs)

        # 2b. Consent-scoped bootstrap fetch: host-allowlisted, audited.
        bootstrap_reason = _bootstrap_allows(host)
        if bootstrap_reason is not None:
            record_allow(url, _current_caller(), bootstrap_reason)
            return super().send(request, **kwargs)

        # 3. Airgapped + non-local + no allow → deny loudly.
        if is_airgapped():
            caller = _current_caller()
            record_block(url, caller, "airgapped")
            raise EgressBlocked(host or "?", caller, "airgapped")

        # 4. Hybrid + non-local + no allow → pass through.
        return super().send(request, **kwargs)


# ---------------------------------------------------------------------------
# Guarded httpx-family transports
# ---------------------------------------------------------------------------

def _check_httpx_url(url: str) -> None:
    """Decision tree for httpx-family requests — same semantics as
    ``GuardedHTTPAdapter.send``, including allow-context audit lines
    (``_check_egress_or_raise`` leaves allow logging to its caller).

    The decision is on the DESTINATION in ``url``; a proxy the client was
    configured with is not consulted (documented gap, docs/PRIVACY.md)."""
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        host = ""

    if host and (is_local_host(host) or is_local_ip(host)):
        return

    active = _allow_egress_var.get(None)
    if active is not None:
        record_allow(url, _current_caller(), active)
        return

    bootstrap_reason = _bootstrap_allows(host)
    if bootstrap_reason is not None:
        record_allow(url, _current_caller(), bootstrap_reason)
        return

    if is_airgapped():
        caller = _current_caller()
        record_block(url, caller, "airgapped")
        raise EgressBlocked(host or "?", caller, "airgapped")


def _make_guarded_sync(original: Callable) -> Callable:
    """Wrap a ``handle_request``: check the destination, then call the library's own method with
    every argument it was given.  No functools.wraps: introspection shows this is ARAIL's wrapper."""

    def _guarded_handle_request(self, request, *args, **kwargs):
        _check_httpx_url(str(request.url))
        return original(self, request, *args, **kwargs)

    _guarded_handle_request._arail_guarded = True  # type: ignore[attr-defined]
    _guarded_handle_request.__wrapped__ = original  # type: ignore[attr-defined]
    return _guarded_handle_request


def _make_guarded_async(original: Callable) -> Callable:
    """The ``handle_async_request`` twin of ``_make_guarded_sync``."""

    async def _guarded_handle_async_request(self, request, *args, **kwargs):
        _check_httpx_url(str(request.url))
        return await original(self, request, *args, **kwargs)

    _guarded_handle_async_request._arail_guarded = True  # type: ignore[attr-defined]
    _guarded_handle_async_request.__wrapped__ = original  # type: ignore[attr-defined]
    return _guarded_handle_async_request


# The two class-level patch targets every httpx-family library is expected to have: the class, the
# method that performs the network I/O, and how to wrap it.
_FAMILY_TARGETS = (
    ("HTTPTransport", "handle_request", _make_guarded_sync),
    ("AsyncHTTPTransport", "handle_async_request", _make_guarded_async),
)


def _patch_family_member(name: str, module: types.ModuleType) -> str:
    """Put the guard on one family member's two transport classes.

    Returns the member's coverage value: ``"guarded"``, or ``"unguarded: <why>"`` when a class or
    method is missing (or cannot be set).  Everything that CAN be patched is patched either way.

    A method that already carries ``_arail_guarded`` is skipped: it is the same class object
    reached under another name (``httpx2.alias_httpx()`` makes ``sys.modules["httpx"]`` the httpx2
    module; a package may re-export another's classes), so it is wrapped once and recorded once.
    """
    problems = []
    for cls_name, attr, make_wrapper in _FAMILY_TARGETS:
        cls = getattr(module, cls_name, None)
        original = getattr(cls, attr, None) if isinstance(cls, type) else None
        if not callable(original):
            problems.append(f"{cls_name}.{attr} is missing")
            continue
        if getattr(original, "_arail_guarded", False):
            continue
        owned = attr in vars(cls)
        saved = vars(cls)[attr] if owned else None
        try:
            setattr(cls, attr, make_wrapper(original))
        except Exception as exc:  # noqa: BLE001 — a class that refuses the patch is a hole
            problems.append(f"{cls_name}.{attr} could not be patched ({type(exc).__name__}: {exc})")
            continue
        _PATCHED[(cls, attr)] = (owned, saved)
    if problems:
        return _UNGUARDED + "; ".join(problems)
    return "guarded"


def _install_httpx_family_guard() -> dict[str, str]:
    """Patch every installed member of ``httpx_family.FAMILY``; return ``{name: coverage value}``.

    The patch is on the classes, so it covers clients built before this ran and the clients SDKs
    build for themselves (huggingface_hub, anthropic, openai).  A member that is NOT installed is
    ``"absent"``: nothing in this process can use it.  A member that IS installed but cannot be
    patched (its lookup or import raises, a class or method is missing) is ``"unguarded: <why>"``,
    and install_guard() raises after everything else is patched.  The guard used to treat a
    library it could not import as nothing to do, so it could not tell those two apart, which is
    how TD50 stayed hidden.

    Libraries are imported here, eagerly: the check must exist before any code can use them.
    """
    result: dict[str, str] = {}
    for name in httpx_family.FAMILY:
        try:
            present = httpx_family.is_installed(name)
        except Exception as exc:  # noqa: BLE001 — a broken finder: cannot say it is absent
            result[name] = f"{_UNGUARDED}lookup raised {type(exc).__name__}: {exc}"
            continue
        if not present:
            result[name] = "absent"
            continue
        try:
            module = importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 — installed but broken: fail closed, say why
            result[name] = f"{_UNGUARDED}import raised {type(exc).__name__}: {exc}"
            continue
        result[name] = _patch_family_member(name, module)
    return result


def _reset_httpx_family_guard() -> None:
    """TEST USE ONLY — put every patched class back exactly as install found it.

    ``setattr`` the saved function for an attribute the class owned, ``delattr`` for one it merely
    inherited.  Never raises: a restore that fails is logged and the rest are still attempted."""
    for (cls, attr), (owned, saved) in list(_PATCHED.items()):
        try:
            if owned:
                setattr(cls, attr, saved)
            else:
                delattr(cls, attr)
        except AttributeError:
            pass  # already gone: nothing to restore
        except Exception as exc:  # noqa: BLE001
            _log.warning("arail.egress: could not restore %s.%s: %s", cls.__qualname__, attr, exc)
    _PATCHED.clear()


# ---------------------------------------------------------------------------
# Guarded urllib handlers
# ---------------------------------------------------------------------------

class _GuardedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        _check_egress_or_raise(req.full_url)
        return super().http_open(req)


class _GuardedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        _check_egress_or_raise(req.full_url)
        return super().https_open(req)


# ---------------------------------------------------------------------------
# install_guard / _reset_for_tests / coverage
# ---------------------------------------------------------------------------

class EgressGuardIncomplete(RuntimeError):
    """install_guard() could not put the guard on an httpx-family library that is installed.

    Everything else is guarded (``coverage()`` says what); the library named here is NOT: its
    requests are not checked against LAB_MODE.  This is not an ``EgressBlocked`` — no request was
    refused.  ``uncovered`` is ``{library: reason}``, the same content as ``uncovered()``.  Every
    caller of install_guard() already turns an exception into something an operator sees (the
    portal's activity warning, the agent loader's warning, ``arailctl doctor``'s required check,
    the Model Forge worker's exit code); a log line alone would be the silent hole of TD50.
    """

    def __init__(self, message: str, uncovered: Optional[dict] = None) -> None:
        super().__init__(message)
        self.uncovered: dict = dict(uncovered or {})


def coverage() -> dict:
    """What the last install attempt guarded, per client.

    Keys are ``"requests"``, ``"urllib.request"``, then ``httpx_family.FAMILY`` in order; values
    are ``"guarded"``, ``"absent"`` (not installed: nothing here can use it) or
    ``"unguarded: <why>"``.  ``{}`` before the first install and after ``_reset_for_tests()``.
    Reflects the last attempt, including one that raised EgressGuardIncomplete.  A copy.
    """
    return dict(_COVERAGE)


def uncovered() -> dict:
    """``{library: reason}`` for every client the last install attempt found installed but could
    not guard.  Empty when the guard is complete."""
    return {
        name: value[len(_UNGUARDED):]
        for name, value in _COVERAGE.items()
        if value.startswith(_UNGUARDED)
    }


def _incomplete_message(missing: dict, coverage_now: dict) -> str:
    still_guarded = [name for name, value in coverage_now.items() if value == "guarded"]
    what = "; ".join(f"{name}: {why}" for name, why in missing.items())
    return (
        f"egress guard incomplete — {what}. Requests made through {', '.join(missing)} are NOT "
        f"checked against LAB_MODE. Still guarded: {', '.join(still_guarded)}. "
        f"Fix: install a supported version (httpx2<3, httpx<1) or uninstall {' and '.join(missing)}."
    )


def install_guard() -> None:
    """Install the egress guard.  Idempotent; safe to call N times, from any thread.

    1. ``requests``: a GuardedHTTPAdapter via a monkey-patch on
       ``requests.adapters.HTTPAdapter`` (and ``requests.sessions.HTTPAdapter``, which
       ``Session.__init__`` reads) so any future ``requests.Session()`` is guarded.  Without it, an
       agent calling ``requests.Session()`` directly would get a fresh default adapter and bypass
       the guard.  The stock class is saved only if the current one is not already the guarded
       one, so a retried install cannot save the guard as "the original".
    2. ``urllib.request``: an opener (``install_opener``) whose HTTP and HTTPS handlers consult
       the airgap policy before delegating to the default chain.
    3. The httpx family: the transports of every installed member of
       ``arail.httpx_family.FAMILY`` (``httpx2``, ``httpx``), patched on the class so clients built
       earlier, and the clients SDKs build for themselves, are covered.  A class reached under two
       names (``httpx2.alias_httpx()``) is wrapped once; the ``_arail_guarded`` marker and
       ``__wrapped__`` on each wrapper make a repeat harmless.

    Idempotency: a COMPLETE install sets the module-level ``_INSTALLED`` flag, and later calls
    return at once.  If an httpx-family library is installed but cannot be patched (its import
    raises, or a class or method is missing), everything else is patched, ``coverage()`` records
    why, the error is logged, and EgressGuardIncomplete is raised.  ``_INSTALLED`` stays False, so
    every later call retries and raises again: the guard never reports itself installed while a
    library it knows about is open.  This holds in hybrid mode too, because the airgap toggle flips
    ``LAB_MODE`` in-process.

    Tests that need a fresh install call ``_reset_for_tests()``.
    """
    global _INSTALLED, _ORIGINAL_HTTP_ADAPTER, _COVERAGE

    if _INSTALLED:
        return

    with _LOCK:
        if _INSTALLED:
            return

        found: dict[str, str] = {}

        # 1. requests.
        if requests.adapters.HTTPAdapter is not GuardedHTTPAdapter:
            _ORIGINAL_HTTP_ADAPTER = requests.adapters.HTTPAdapter
        requests.adapters.HTTPAdapter = GuardedHTTPAdapter  # type: ignore[misc]
        requests.sessions.HTTPAdapter = GuardedHTTPAdapter  # type: ignore[attr-defined]
        found["requests"] = "guarded"

        # 2. urllib.request.
        opener = urllib.request.build_opener(
            _GuardedHTTPHandler, _GuardedHTTPSHandler
        )
        urllib.request.install_opener(opener)
        found["urllib.request"] = "guarded"

        # 3. The httpx family (anthropic/openai SDKs, huggingface_hub, starlette's TestClient,
        #    the Open Notebook seed).
        found.update(_install_httpx_family_guard())
        _COVERAGE = found

        missing = uncovered()
        if missing:
            message = _incomplete_message(missing, found)
            _log.error("arail.egress: %s", message)
            raise EgressGuardIncomplete(message, missing)

        _INSTALLED = True
        _log.debug("arail.egress: guard installed (mode=%s)", "airgapped" if is_airgapped() else "hybrid")


def _reset_for_tests() -> None:
    """Reset the guard to un-installed state.  TEST USE ONLY.

    Restores the original ``requests.adapters.HTTPAdapter`` class and, for every httpx-family
    class install patched, the exact pre-install state (``setattr`` if the class owned the
    attribute, ``delattr`` if it inherited it).  Clears ``coverage()`` and the module-level
    ``_INSTALLED`` flag so the next ``install_guard()`` call re-installs cleanly.  Never raises:
    ``tests/conftest.py`` swallows exceptions from it, and a reset that fails silently would
    pollute every later test.

    The urllib opener cannot be reset without re-importing urllib.request;
    tests that care about urllib isolation must monkeypatch
    ``urllib.request.urlopen`` or ``_GuardedHTTPSHandler.https_open``
    directly.
    """
    global _INSTALLED, _COVERAGE
    with _LOCK:
        try:
            requests.adapters.HTTPAdapter = _ORIGINAL_HTTP_ADAPTER  # type: ignore[misc]
            requests.sessions.HTTPAdapter = _ORIGINAL_HTTP_ADAPTER  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            _log.warning("arail.egress: could not restore the requests adapter: %s", exc)
        try:
            _reset_httpx_family_guard()
        except Exception as exc:  # noqa: BLE001
            _log.warning("arail.egress: could not restore the httpx family: %s", exc)
        _COVERAGE = {}
        _INSTALLED = False


# ---------------------------------------------------------------------------
# @allow_egress context manager / decorator
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def allow_egress(reason: str):
    """Context manager (and decorator) that bypasses the guard with an
    audit-logged reason.

    HARD RULE: in airgapped mode, ``allow_egress`` raises ``EgressBlocked``
    *immediately on entry*, before yielding.  The only exception is the
    ``BUDDY_EGRESS_PROBE`` pathway, which uses raw socket and never
    touches this context manager.  Any future contributor who wants to
    allow egress in airgapped must edit this module and re-justify in a
    new sprint.  This is intentional ratchet logic.

    Also usable as a decorator::

        @allow_egress("save provider token by hitting /models for validation")
        def test_provider(provider, token): ...

    Reason validation:
    - non-empty string, < 200 chars (otherwise ``ValueError``)
    - logged via ``record_allow`` when bypass is consumed by a guard check

    Scope: applies to the calling stack frame and all sub-calls within
    the with-block / decorated function.  Sub-threads spawned inside
    the block do NOT inherit the bypass (contextvars semantics).

    asyncio subtlety: ``asyncio.create_task(coro)`` *does* copy the
    contextvars context to the task.  This means an ``allow_egress`` block
    that launches a task and returns will keep allowing egress in the task
    even after the with-block exits.  For v1 this is acceptable — the only
    caller pattern is "save/test/list provider token" which awaits inline.
    See ``learnings/2026-05-05-allow-egress-task-scope.md``.
    """
    if not isinstance(reason, str) or not reason or len(reason) > 200:
        raise ValueError(
            f"allow_egress reason must be a non-empty string < 200 chars; got {reason!r}"
        )
    if is_airgapped():
        # Hard ratchet: no escape hatch in airgapped.  The only intentional
        # exemption is BUDDY_EGRESS_PROBE which uses raw socket directly,
        # not this context manager.  See ARCHITECTURE.md §7.
        caller = _current_caller()
        raise EgressBlocked(
            "?", caller,
            f"allow_egress denied in airgapped: {reason!r}"
        )
    token = _allow_egress_var.set(reason)
    try:
        yield reason
    finally:
        _allow_egress_var.reset(token)


# ---------------------------------------------------------------------------
# allow_bootstrap_fetch — the ONE consent-gated exemption that works airgapped
# ---------------------------------------------------------------------------

# (reason, allowlisted host suffixes) for the active bootstrap scope, or None.
_bootstrap_fetch_var: ContextVar[Optional[tuple]] = ContextVar(
    "arail_bootstrap_fetch", default=None
)

_HOST_SUFFIX_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$")


def _bootstrap_allows(host: str) -> Optional[str]:
    """Reason string if an active bootstrap scope allowlists ``host``."""
    active = _bootstrap_fetch_var.get(None)
    if active is None:
        return None
    reason, suffixes = active
    h = (host or "").lower().rstrip(".")
    if not h:
        return None
    for suffix in suffixes:
        if h == suffix or h.endswith("." + suffix):
            return reason
    return None


@contextlib.contextmanager
def allow_bootstrap_fetch(reason: str, hosts, *, consent_id: str):
    """Consent-gated, host-allowlisted, scoped egress exemption that works
    in AIRGAPPED mode — the World Forge "sourced bootstrap" seam.

    RE-JUSTIFICATION (per the allow_egress ratchet contract above; sprint
    2026-07-08-world-kb-bootstrap): the operator explicitly chooses "Fetch
    real content" when forging a World and consents in the UI to a one-time
    fetch for that subject. That is a *user-initiated, informed* decision —
    the exact class of action the airgap exists to protect, made by the
    person it protects. The exemption is narrower than a LAB_MODE flip in
    every dimension:

      - CONSENT: entry requires an APPROVED ConsentStore record
        (``consent_id``) — a durable artifact of the user's decision in
        lab/data/consent/history.json. No consent, no scope.
      - HOSTS: only the given lowercase domain suffixes pass (e.g.
        ``wikipedia.org`` matches ``en.wikipedia.org``); every other host
        still hard-blocks with EgressBlocked, even inside the scope.
      - SCOPE: contextvars — one call stack, dies with the with-block;
        agents/threads outside the block see an unchanged airgap.
      - AUDIT: every allowed request is appended to lab/data/egress.jsonl
        via record_allow with this reason.

    The airgapped DEFAULT is unchanged: nothing reaches this scope without
    the operator forging with the fetch option on.
    """
    if not isinstance(reason, str) or not reason or len(reason) > 200:
        raise ValueError(
            f"allow_bootstrap_fetch reason must be a non-empty string < 200 chars; got {reason!r}"
        )
    suffixes = tuple(str(h).strip().lower().rstrip(".") for h in (hosts or ()))
    if not suffixes:
        raise ValueError("allow_bootstrap_fetch requires a non-empty host allowlist")
    for suffix in suffixes:
        if not _HOST_SUFFIX_RE.match(suffix) or "/" in suffix or ":" in suffix:
            raise ValueError(f"allow_bootstrap_fetch host must be a bare domain, got {suffix!r}")
    if not isinstance(consent_id, str) or not consent_id:
        raise ValueError("allow_bootstrap_fetch requires a consent_id")

    from arail.agents.consent import ConsentStore

    if not ConsentStore().is_approved(consent_id):
        caller = _current_caller()
        raise EgressBlocked(
            suffixes[0], caller,
            f"bootstrap fetch denied: consent {consent_id!r} not approved",
        )

    token = _bootstrap_fetch_var.set((reason, suffixes))
    try:
        yield reason
    finally:
        _bootstrap_fetch_var.reset(token)


# ---------------------------------------------------------------------------
# Internet probe (opt-in)
# ---------------------------------------------------------------------------

def probe_internet() -> Optional[bool]:
    """Check whether the host can reach the internet.  Opt-in.

    Only runs when ``BUDDY_EGRESS_PROBE=1``.  Uses raw
    ``socket.socket(AF_INET, SOCK_STREAM)`` — bypasses our own guard
    intentionally.  One TCP connect to ``1.1.1.1:443`` with 1s timeout.
    No payload, no DNS, no HTTP.

    Result is cached for 60 seconds in process memory so repeated modal
    opens don't hammer the test.

    Returns ``True`` if the host can reach the internet, ``False`` if it
    cannot, ``None`` if the env var is not set.

    Writes a ``reason="probe"`` line to ``egress.jsonl`` so the user can
    see the single audited bypass in the modal.
    """
    if not os.getenv("BUDDY_EGRESS_PROBE", "").strip() in ("1", "true", "yes"):
        return None

    now = time.monotonic()
    cached = _PROBE_CACHE.get("result")
    if cached is not None and (now - _PROBE_CACHE.get("ts", 0)) < _PROBE_CACHE_TTL:
        return cached

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        try:
            s.connect(("1.1.1.1", 443))
            result = True
        except (OSError, socket.timeout):
            result = False
        finally:
            s.close()
    except Exception:  # noqa: BLE001
        result = False

    from arail.airgap import lab_mode as _lab_mode
    line = json.dumps({
        "ts": _utcnow(),
        "url_host": "1.1.1.1:443",
        "caller": "arail.egress.probe_internet",
        "reason": "probe",
        "lab_mode": _lab_mode(),
    }) + "\n"
    _write_jsonl_line(line)

    _PROBE_CACHE["result"] = result
    _PROBE_CACHE["ts"] = now
    return result
