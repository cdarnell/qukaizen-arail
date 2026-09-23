"""airgap — single source of truth for ARAIL's outbound network policy.

Every other module that wants to ask "are we airgapped?" or
"is this URL local?" goes through this module. Five duplicated
helpers across the codebase collapse to delegations into here.

Threat model: well-meaning agent code that uses ``requests`` and
``urllib.request``. Not an adversary on the host — for that, run
a host firewall (pf on macOS, iptables/ufw on Linux).

Known gaps documented in PRIVACY.md:
- ``httpx`` — not wrapped; localhost-only in tree.
- ``aiohttp`` — not in tree.
- Raw sockets — wrapping would break loopback paths underneath wrapped libs.
- Subprocess shells — ``subprocess.run(["curl", ...])``, ``os.system("curl")``.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse


# Single source of truth for the airgapped-mode explanation shown to the
# user. Originally lived only in the Chat providers-status endpoint
# (portal/app.py); Model Forge's `nucleus gateway` seam (ARCHITECTURE.md
# §4.11) needs the identical string, so it moved here. Byte-identical to
# the pre-move Chat string — see tests/test_airgap.py::test_airgapped_notice_unchanged.
AIRGAPPED_NOTICE = (
    "Lab is in airgapped mode. Only My Machine is usable. "
    "To enable cloud providers, set LAB_MODE=hybrid in .env and restart."
)


class EgressBlocked(RuntimeError):
    """Raised when an outbound network call is denied by the airgap guard.

    Subclass of RuntimeError so agents that ``except Exception:`` let it
    bubble visibly rather than swallow it as a generic IOError.
    Carries ``url_host``, ``caller``, and ``reason`` attributes for the
    audit log and any catch-and-translate in agent code.

    NOTE: ``url_host`` and ``caller`` carry only the *host* portion of the
    URL and the calling function name — never the full URL, path, or query
    string — so secrets in query parameters don't leak into tracebacks.
    """

    def __init__(self, url_host: str, caller: str, reason: str) -> None:
        self.url_host = url_host
        self.caller = caller
        self.reason = reason
        super().__init__(
            f"Egress blocked: host={url_host!r} caller={caller!r} reason={reason!r}"
        )


def lab_mode() -> str:
    """Return the current lab mode: ``'airgapped'`` or ``'hybrid'``.

    Reads LAB_MODE → ARAIL_MODE → ``'airgapped'`` (the canonical
    fallback chain used in five places today). Strips and lowercases;
    anything that is not ``'hybrid'`` collapses to ``'airgapped'``
    (fail-closed).
    """
    raw = os.getenv("LAB_MODE") or os.getenv("ARAIL_MODE") or ""
    mode = raw.strip().lower()
    return "hybrid" if mode == "hybrid" else "airgapped"


def is_airgapped() -> bool:
    """True iff ``lab_mode()`` != ``'hybrid'``."""
    return lab_mode() != "hybrid"


def is_local_ip(ip: str) -> bool:
    """True iff the given IP literal is loopback, RFC1918, or link-local.

    Uses ``ipaddress.ip_address(ip).is_private`` plus ``.is_loopback``
    plus ``.is_link_local``. Pure stdlib. Strips IPv6 zone identifiers
    (e.g. ``fe80::1%en0``) before parsing.
    """
    # Strip zone identifier that ipaddress does not accept.
    if "%" in ip:
        ip = ip.split("%")[0]
    try:
        addr = ipaddress.ip_address(ip)
        return addr.is_loopback or addr.is_private or addr.is_link_local
    except ValueError:
        return False


def is_local_host(host: str) -> bool:
    """True iff host is loopback, RFC1918, or link-local.

    Accepts hostnames *and* IP literals. Handles:
      - ``'localhost'``, ``'127.0.0.1'``, ``'::1'``
      - ``'10.x.y.z'``, ``'172.16.0.0/12'``, ``'192.168.x.y'``
      - ``'169.254.x.y'``, ``'fe80::*'``

    For non-IP hostnames (e.g. ``'my-gpu-box.local'``), resolves via
    ``socket.gethostbyname`` and re-checks the resolved IP.  Resolution
    failures count as *not local* (fail-closed).

    DNS-rebind note: if the system resolver says ``evil.example.com``
    maps to ``127.0.0.1``, this function returns True — we trust the
    system resolver.  That's a documented limit of the v1 threat model.
    """
    if not host:
        return False

    # Fast-path: try parsing as an IP literal first.
    if is_local_ip(host):
        return True

    # Resolve the hostname and check the resolved IP.
    _OLD_TIMEOUT = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(1.5)
        resolved = socket.gethostbyname(host)
        return is_local_ip(resolved)
    except (OSError, socket.gaierror):
        return False
    finally:
        socket.setdefaulttimeout(_OLD_TIMEOUT)


def should_allow_egress(url: str) -> tuple[bool, str]:
    """Decide whether a URL should be allowed out.

    Returns ``(allowed, reason)``.  Reason strings:

    - ``"local"``     — host resolves to loopback/RFC1918/link-local
    - ``"hybrid"``    — lab_mode is hybrid; consent layer takes over
    - ``"allowed"``   — ``@allow_egress`` context active (audited)
    - ``"airgapped"`` — denied; airgapped + non-local + no allow context
    - ``"invalid"``   — URL did not parse; denied

    Does NOT raise.  Callers (the guard) decide what to do with False.
    The ``"allowed"`` reason is set by the guard itself when it detects
    the ``_allow_egress_var`` is active; this function does not check it.
    """
    if not url:
        return False, "invalid"

    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
    except Exception:
        return False, "invalid"

    if not host:
        return False, "invalid"

    if is_local_host(host):
        return True, "local"

    if not is_airgapped():
        return True, "hybrid"

    return False, "airgapped"
