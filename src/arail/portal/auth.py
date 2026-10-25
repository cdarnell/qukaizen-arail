"""Portal authentication (ARAIL D3): one bearer token for programs, one session cookie for browsers.

Sprint 2026-10-25-buddy-s4-gateway-in-box, ARCHITECTURE.md C1/C2/C5. Loopback used to be the
trust boundary; on a Mac a networked container reaches every loopback listener and arrives as
127.0.0.1, so "local" no longer means "the operator". This module holds the pieces; the thin
middleware wrapper lives in ``portal/app.py`` (this module never imports it, so the edge app
and the tests can import it cleanly).

State, all per ``config.DATA_DIR`` (so every World instance has its own):

* ``portal.token``          ``arl1_`` + 43 base64url chars, ``0600``, read by programs.
* ``portal_sessions.json``  sha256 of each live session id, ``0600``. The id itself is only
                            ever in the browser's cookie.

Comparisons of secrets use ``hmac.compare_digest`` over sha256 digests and nothing else.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import stat
import threading
import time
from pathlib import Path
from typing import Optional

from arail import config
from arail.buddy_host import _files

_log = logging.getLogger(__name__)

TOKEN_PREFIX = "arl1_"
_TOKEN_RE = re.compile(r"^arl1_[A-Za-z0-9_-]{43}$")
_PLACEHOLDERS = {"", "change-me", "__needs_setup__"}

IDLE_EXPIRY_S = 30 * 86400
ABSOLUTE_EXPIRY_S = 90 * 86400
MAX_SESSIONS = 32
LAST_SEEN_WRITE_EVERY_S = 3600
COOKIE_MAX_AGE_S = IDLE_EXPIRY_S

# The anonymous set, exactly. Everything else needs a credential once the lab has a passphrase.
# /welcome and /api/welcome/* are reachable only while the lab is not onboarded; that case is
# decision rule 1 (nothing to log in with yet), so they are deliberately not in this table.
EXEMPT_EXACT: dict[str, frozenset[str]] = {
    "/login": frozenset({"GET", "POST"}),
    "/favicon.ico": frozenset({"GET"}),
    "/health": frozenset({"GET"}),
    "/healthz": frozenset({"GET"}),
    "/api/system/health": frozenset({"GET"}),
    "/api/instance": frozenset({"GET"}),
}
EXEMPT_STATIC_PREFIX = "/static/"
EDGE_PREFIX = "/api/host/v1/"
OPENAI_PREFIX = "/api/openai/v1/"
_SAFE_NEXT = re.compile(r"^/(?!/)[^\\\r\n]*$")


# --------------------------------------------------------------------------- paths
def token_path() -> Path:
    """``<DATA_DIR>/portal.token``, resolved per call (tests and instances see their own dir)."""
    return Path(config.DATA_DIR) / "portal.token"


def sessions_path() -> Path:
    return Path(config.DATA_DIR) / "portal_sessions.json"


def cookie_name() -> str:
    return "arail_sid_" + (os.getenv("PORTAL_PORT", "") or "8080").strip()


# --------------------------------------------------------------------------- token
def _sha(text: str) -> bytes:
    return hashlib.sha256(text.encode("utf-8")).digest()


def _mint_token() -> str:
    return TOKEN_PREFIX + base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def token_id(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]


def _token_file_unsafe(path: Path) -> Optional[str]:
    """Why the token file must not be trusted, or None. POSIX permissions are the whole protection."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unreadable"
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return "not a regular file"
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        return "owned by another user"
    if stat.S_IMODE(st.st_mode) & 0o077:
        return "mode wider than 0600"
    return None


def ensure_token() -> None:
    """Mint the token if the file is missing. Idempotent, never prints the value."""
    path = token_path()
    if os.path.lexists(path):
        return
    _files.ensure_private_dir(path.parent)
    _files.write_private(path, (_mint_token() + "\n").encode("ascii"))


def rotate_token() -> str:
    """Write a new token and return its 8-char id (never the token)."""
    path = token_path()
    _files.ensure_private_dir(path.parent)
    tok = _mint_token()
    _files.write_private(path, (tok + "\n").encode("ascii"))
    return token_id(tok)


def read_token_id() -> Optional[str]:
    """Id of the current token, or None when missing or unsafe."""
    path = token_path()
    if _token_file_unsafe(path) is not None:
        return None
    try:
        return token_id(path.read_text(encoding="ascii").strip())
    except (OSError, UnicodeDecodeError):
        return None


_tok_lock = threading.Lock()
_tok_cache: dict = {}   # path -> {"key": ..., "digest": ..., "warned": ...}


def _current_digest(path: Path, regex) -> Optional[bytes]:
    """sha256 of the token in ``path``; re-read only when (path, inode, mtime, mode, owner) changes."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    key = (str(path), st.st_ino, st.st_mtime_ns, st.st_mode, st.st_uid)
    with _tok_lock:
        ent = _tok_cache.setdefault(str(path), {"key": None, "digest": None, "warned": None})
        if ent["key"] == key:
            return ent["digest"]
        why = _token_file_unsafe(path)
        digest: Optional[bytes] = None
        if why is None:
            try:
                tok = path.read_text(encoding="ascii").strip()
                if regex.match(tok):
                    digest = _sha(tok)
            except (OSError, UnicodeDecodeError):
                digest = None
        elif ent["warned"] != key:
            _log.warning("%s is %s; bearer auth refused. Fix with: chmod 600 %s", path.name, why, path)
            ent["warned"] = key
        ent["key"] = key
        ent["digest"] = digest
        return digest


def _current_token_digest() -> Optional[bytes]:
    return _current_digest(token_path(), _TOKEN_RE)


def token_file_state() -> str:
    """'ok', 'missing' or 'unsafe' (for the 401 detail and the CLI)."""
    path = token_path()
    why = _token_file_unsafe(path)
    if why is None:
        return "ok"
    return "missing" if why == "missing" else "unsafe"


def check_bearer(header: str) -> bool:
    """True iff ``header`` is exactly ``Bearer arl1_<43>`` and matches the current token."""
    if not isinstance(header, str) or not header.startswith("Bearer "):
        return False
    presented = header[len("Bearer "):]
    if not _TOKEN_RE.match(presented):
        return False
    current = _current_token_digest()
    if current is None:
        return False
    return hmac.compare_digest(_sha(presented), current)


# --------------------------------------------------------------------------- shim-scoped token
# ``arl1s_`` is for the OpenCode child process, whose model drives a shell that can read its own
# env and config. It is accepted ONLY under SHIM_ALLOWED_PREFIXES; the portal bearer must never be
# placed where that shell can read it (Addendum A.2).
SHIM_PREFIX = "arl1s_"
_SHIM_RE = re.compile(r"^arl1s_[A-Za-z0-9_-]{43}$")
SHIM_ALLOWED_PREFIXES = (OPENAI_PREFIX,)


def shim_token_path() -> Path:
    return token_path().parent / "portal.shim-token"


def ensure_shim_token() -> None:
    path = shim_token_path()
    if os.path.lexists(path):
        return
    _files.ensure_private_dir(path.parent)
    tok = SHIM_PREFIX + base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    _files.write_private(path, (tok + "\n").encode("ascii"))


def read_shim_token() -> Optional[str]:
    """The shim token for OpenCode's env/config, or None when missing or unsafe."""
    ensure_shim_token()
    path = shim_token_path()
    if _token_file_unsafe(path) is not None:
        return None
    try:
        tok = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return tok if _SHIM_RE.match(tok) else None


def check_shim_bearer(header: str) -> bool:
    if not isinstance(header, str) or not header.startswith("Bearer "):
        return False
    presented = header[len("Bearer "):]
    if not _SHIM_RE.match(presented):
        return False
    current = _current_digest(shim_token_path(), _SHIM_RE)
    return current is not None and hmac.compare_digest(_sha(presented), current)


def shim_path_allowed(path: str) -> bool:
    return any(path.startswith(p) for p in SHIM_ALLOWED_PREFIXES)


# --------------------------------------------------------------------------- passphrase
def check_password(candidate: str) -> bool:
    expected = (os.getenv("ARAIL_PASSWORD") or "").strip()
    if expected in _PLACEHOLDERS or not isinstance(candidate, str):
        return False
    return hmac.compare_digest(_sha(candidate), _sha(expected))


# --------------------------------------------------------------------------- sessions
class SessionStore:
    """Server-side sessions. Only sha256(sid) is stored; ``last_seen`` is persisted at most hourly."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._rows: dict[str, dict] = {}
        self._persisted_seen: dict[str, float] = {}
        self._seen_key: Optional[tuple] = None
        self.writes = 0
        self.reloads = 0
        with self._lock:
            self._sync()

    @staticmethod
    def _key(sid: str) -> str:
        return hashlib.sha256(sid.encode("utf-8")).hexdigest()

    def _file_key(self) -> Optional[tuple]:
        try:
            st = os.lstat(self.path)
        except OSError:
            return None
        return (st.st_ino, st.st_mtime_ns, st.st_size, st.st_mode, st.st_uid)

    def _read_file(self) -> dict[str, dict]:
        """The sessions in the file; {} (everyone logs in again) if it is absent, unsafe or corrupt."""
        try:
            if not os.path.lexists(self.path):
                return {}
            st = os.lstat(self.path)
            if (stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode)
                    or (hasattr(os, "getuid") and st.st_uid != os.getuid())
                    or stat.S_IMODE(st.st_mode) & 0o077):
                raise ValueError("unsafe session file")
            data = json.loads(self.path.read_text(encoding="utf-8"))
            out: dict[str, dict] = {}
            for k, v in data["sessions"].items():
                if re.fullmatch(r"[0-9a-f]{64}", k) and float(v["created"]) > 0 and float(v["last_seen"]) > 0:
                    out[k] = {"created": float(v["created"]), "last_seen": float(v["last_seen"])}
            return out
        except Exception as exc:  # noqa: BLE001 - never crash the portal; everyone logs in again
            _log.warning("portal sessions file ignored (%s); everyone logs in again", type(exc).__name__)
            return {}

    def _sync(self) -> None:
        """Adopt the file whenever it changed under us (a second process revoked or created sessions).

        The file is the authority: a session that is in memory but no longer in the file was revoked
        elsewhere and is dropped; one only in the file was created elsewhere and is adopted. Every
        change this process makes is written through immediately, so nothing live exists only in
        memory (``last_seen`` is the one exception and the newer value is kept).
        """
        k = self._file_key()
        if k == self._seen_key and self._seen_key is not None:
            return
        if k is None and self._seen_key is None and not self._rows:
            return
        fresh = self._read_file()
        for sk, row in fresh.items():
            mine = self._rows.get(sk)
            if mine is not None:
                row["last_seen"] = max(row["last_seen"], mine["last_seen"])
        self._rows = fresh
        self._persisted_seen = {sk: r["last_seen"] for sk, r in fresh.items()}
        self._seen_key = k
        self.reloads += 1

    def _save(self) -> None:
        try:
            _files.ensure_private_dir(self.path.parent)   # callers ran _sync() first, under the same lock
            payload = json.dumps({"v": 1, "sessions": self._rows}, sort_keys=True).encode("utf-8")
            _files.write_private(self.path, payload)
            self._persisted_seen = {k: v["last_seen"] for k, v in self._rows.items()}
            self._seen_key = self._file_key()
            self.writes += 1
        except OSError as exc:
            _log.warning("portal sessions file not written (%s)", type(exc).__name__)

    def _expired(self, row: dict, now: float) -> bool:
        return now - row["last_seen"] > IDLE_EXPIRY_S or now - row["created"] > ABSOLUTE_EXPIRY_S

    def create(self) -> str:
        sid = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
        now = time.time()
        with self._lock:
            self._sync()
            for k in [k for k, r in self._rows.items() if self._expired(r, now)]:
                del self._rows[k]
            while len(self._rows) >= MAX_SESSIONS:
                oldest = min(self._rows, key=lambda k: self._rows[k]["created"])
                del self._rows[oldest]
            self._rows[self._key(sid)] = {"created": now, "last_seen": now}
            self._save()
        return sid

    def touch(self, sid: str) -> bool:
        if not sid or len(sid) > 64:
            return False
        now = time.time()
        k = self._key(sid)
        with self._lock:
            self._sync()
            row = self._rows.get(k)
            if row is None:
                return False
            if self._expired(row, now):
                del self._rows[k]
                self._save()
                return False
            row["last_seen"] = now
            if now - self._persisted_seen.get(k, 0.0) >= LAST_SEEN_WRITE_EVERY_S:
                self._save()
            return True

    def revoke(self, sid: str) -> None:
        with self._lock:
            self._sync()
            if self._rows.pop(self._key(sid), None) is not None:
                self._save()

    def revoke_all(self) -> None:
        with self._lock:
            self._sync()
            self._rows.clear()
            self._save()

    def force_expire(self, sid: str) -> None:
        """Test seam: make a live session idle-expired."""
        with self._lock:
            row = self._rows.get(self._key(sid))
            if row:
                row["last_seen"] = time.time() - IDLE_EXPIRY_S - 1


_store_lock = threading.Lock()
_stores: dict[str, SessionStore] = {}


def get_store() -> SessionStore:
    """The store for the current ``DATA_DIR`` (one per data dir, so instances and tests never mix)."""
    path = sessions_path()
    with _store_lock:
        st = _stores.get(str(path))
        if st is None:
            st = _stores[str(path)] = SessionStore(path)
        return st


# --------------------------------------------------------------------------- login rate limit
class LoginLimiter:
    """After 5 consecutive failures, one attempt per 10 s doubling to a cap of 300 s. Process-global."""

    FREE = 5
    BASE_S = 10.0
    CAP_S = 300.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.fails = 0
        self.not_before = 0.0

    def wait_s(self) -> float:
        with self._lock:
            return max(0.0, self.not_before - time.monotonic())

    def failure(self) -> int:
        with self._lock:
            self.fails += 1
            if self.fails >= self.FREE:
                delay = min(self.CAP_S, self.BASE_S * (2 ** (self.fails - self.FREE)))
                self.not_before = time.monotonic() + delay
            return self.fails

    def success(self) -> None:
        with self._lock:
            self.fails = 0
            self.not_before = 0.0


limiter = LoginLimiter()


# --------------------------------------------------------------------------- request decisions
def safe_next(value: Optional[str]) -> str:
    """Open-redirect guard for ``?next=``: a same-site absolute path or ``/``."""
    if isinstance(value, str):
        from urllib.parse import unquote
        if _SAFE_NEXT.match(value) and _SAFE_NEXT.match(unquote(value)):
            return value
    return "/"


def is_exempt(path: str, method: str) -> bool:
    m = method.upper()
    if path.startswith(EXEMPT_STATIC_PREFIX):
        return m in ("GET", "HEAD")
    allowed = EXEMPT_EXACT.get(path)
    return allowed is not None and (m in allowed or (m == "HEAD" and "GET" in allowed))


def session_mutation_ok(headers) -> bool:
    """Rule 6: a cookie-authenticated mutation must be same-origin or carry an exact Origin."""
    sfs = headers.get("sec-fetch-site", "").strip().lower()
    if sfs == "same-origin":
        return True
    origin = headers.get("origin", "")
    if origin and origin != "null":
        from urllib.parse import urlparse
        return urlparse(origin).netloc == headers.get("host", "") != ""
    return False


# --------------------------------------------------------------------------- the middleware decision
def _json_401(error: str, **extra):
    from fastapi.responses import JSONResponse
    body = {"error": error, "login": "/login", **extra}
    return JSONResponse(body, status_code=401, headers={"WWW-Authenticate": 'Bearer realm="arail"'})


async def authenticate(request, call_next, *, onboarded: bool):
    """C2, first match wins. ``onboarded`` is False until the lab has a real passphrase.

    Host allowlist and CSRF run in the outer ``local_trust_boundary`` first (403), so a rebinding
    request is rejected before it costs a session lookup or a login attempt.
    """
    if not onboarded:                                              # 1. nothing to log in with yet
        return await call_next(request)
    path = request.url.path
    if is_exempt(path, request.method):                            # 2. the short anonymous list
        return await call_next(request)
    if path.startswith(EDGE_PREFIX):                               # 3. the edge's own bhc1_ admission
        return await call_next(request)

    header = request.headers.get("authorization")
    if header is not None:                                         # 4. a wrong bearer never falls back to the cookie
        if check_bearer(header):
            request.state.principal = "bearer"
            return await call_next(request)
        if header.startswith("Bearer " + SHIM_PREFIX):
            if check_shim_bearer(header):
                if shim_path_allowed(path):
                    request.state.principal = "shim"
                    return await call_next(request)
                return _json_401("token_scope")      # valid, but not for this route
            return _json_401("auth_invalid")
        if token_file_state() == "unsafe":
            return _json_401("token_file_unsafe")
        return _json_401("auth_invalid")

    if path.startswith(OPENAI_PREFIX):                             # 5. OpenAI clients are bearer-only
        return _json_401("auth_required")

    sid = request.cookies.get(cookie_name(), "")                   # 6. browser session
    if sid and get_store().touch(sid):
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and not session_mutation_ok(request.headers):
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "cross_origin_session"}, status_code=403)
        request.state.principal = "session"
        return await call_next(request)

    if (path.startswith("/api/") or path == "/metrics"                                       # 7. (/metrics: a scraper,
            or "application/json" in request.headers.get("accept", "")):                    #    not a person: 401, not 302)
        return _json_401("auth_required")
    from urllib.parse import quote
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/login?next=" + quote(path, safe="/"), status_code=302)


def set_session_cookie(response, request, sid: str) -> None:
    """HttpOnly, SameSite=Strict, Path=/; Secure only when this request came in over https."""
    response.set_cookie(
        cookie_name(), sid, max_age=COOKIE_MAX_AGE_S, path="/", httponly=True, samesite="strict",
        secure=request.url.scheme == "https",
    )


def clear_session_cookie(response, request) -> None:
    response.delete_cookie(cookie_name(), path="/", httponly=True, samesite="strict",
                           secure=request.url.scheme == "https")
