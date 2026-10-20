"""``./arailctl buddy link | unlink | status`` - the one credential between ARAIL and Buddy.

ARCHITECTURE.md §5.2 and §5.12. A lab has at most one Buddy link:

* the credential is ``bhc1_`` + base64url(32 random bytes), 48 characters;
* ARAIL keeps only its sha256 (``<DATA_DIR>/buddy/link.json``, 0600) and never the
  plaintext; Buddy keeps the plaintext as ``BUDDY_EDGE_CREDENTIAL=`` in
  ``<buddy-home>/secrets.env`` (0600) beside a small ``link.json`` (0600);
* linking records the operator's consent to ``api.telegram.org`` in the existing
  :class:`~arail.agents.consent.ConsentStore`, after showing the consent text and
  asking for ``yes``;
* it runs on the root lab only, with the portal bound to loopback, into a Buddy home
  that is a real directory owned by this user with mode 0700. Anything else is a
  refusal (exit 3) before a byte is written or a consent is recorded.

Exit codes: 0 ok, 2 usage, 3 refused by policy. The credential is never printed.

Buddy's ``link.json`` (``qukaizen-buddy.link/v1``) is the one file shape ARAIL writes
and Buddy reads that is not part of ``buddy.host.v1.json``::

    {"schema": "qukaizen-buddy.link/v1", "edge_base_url": "http://127.0.0.1:<port>/api/host/v1",
     "credential_id": "<8 hex>", "contract_sha256": "<64 hex>", "linked_at": "<UTC Z>"}
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import sys
from pathlib import Path
from typing import Any, Optional, TextIO

from arail.buddy_host import CONTRACT_SHA256

ARAIL_LINK_SCHEMA = "arail.buddy-link/v1"
BUDDY_LINK_SCHEMA = "qukaizen-buddy.link/v1"
CREDENTIAL_PREFIX = "bhc1_"
CREDENTIAL_ENV = "BUDDY_EDGE_CREDENTIAL"
CONSENT_URL = "https://api.telegram.org/"
CONSENT_DOMAIN = "api.telegram.org"
CONSENT_REASON = "Buddy on Telegram"
CONSENT_AGENT = "buddy"

# VISION.md §3.3, verbatim. Shown before the first poll, at the desk.
CONSENT_TEXT = (
    "Buddy on your phone needs the internet. Your lab will run in hybrid mode. In hybrid mode, the lab may also\n"
    "use cloud providers you set up. Buddy himself uses only your local model.\n"
    "\n"
    "Your messages to Buddy and his replies pass through Telegram's servers. Telegram can read them. Bot chats\n"
    "are not end-to-end encrypted.\n"
    "\n"
    "You can switch back to airgapped at any time. Buddy stops listening within one minute."
)

_CRED_RE = re.compile(r"^bhc1_[A-Za-z0-9_-]{43}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_LOOPBACK_HOSTS = {"127.0.0.1": "127.0.0.1", "localhost": "127.0.0.1", "::1": "[::1]"}


class PolicyRefusal(Exception):
    """A refusal by policy (exit 3). The message is shown to the operator verbatim."""


# --------------------------------------------------------------------------- credential
def mint_credential() -> str:
    return CREDENTIAL_PREFIX + base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def credential_sha256(credential: str) -> str:
    return hashlib.sha256(credential.encode("utf-8")).hexdigest()


def credential_id(sha_hex: str) -> str:
    return sha_hex[:8]


# --------------------------------------------------------------------------- paths
def _data_dir() -> Path:
    from arail import config
    return Path(config.DATA_DIR)


def link_dir() -> Path:
    return _data_dir() / "buddy"


def link_path() -> Path:
    return link_dir() / "link.json"


def _current_uid() -> int:
    return os.getuid()


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- reading the link
_cache: dict[str, Any] = {"key": None, "rec": None}


def load_link() -> Optional[dict]:
    """ARAIL's link record, or None when there is none or it cannot be trusted.

    Re-read whenever the file changes, so ``--rotate`` and ``unlink`` act at once.
    A missing, unparseable, wrong-shaped or symlinked file is "not linked": every edge
    call then answers 401 ``not_linked``. Never raises.
    """
    path = link_path()
    try:
        st = os.lstat(path)
    except OSError:
        _cache.update(key=None, rec=None)
        return None
    if not stat.S_ISREG(st.st_mode):
        _cache.update(key=None, rec=None)
        return None
    key = (str(path), st.st_mtime_ns, st.st_size, st.st_ino)
    if _cache["key"] == key:
        return _cache["rec"]
    rec: Optional[dict] = None
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as f:
            data = json.loads(f.read(65536).decode("utf-8"))
        if (isinstance(data, dict) and data.get("schema") == ARAIL_LINK_SCHEMA
                and isinstance(data.get("credential_sha256"), str)
                and _HEX64.fullmatch(data["credential_sha256"])):
            rec = data
    except (OSError, ValueError):
        rec = None
    _cache.update(key=key, rec=rec)
    return rec


def verify_credential(presented: Any) -> Optional[dict]:
    """The link record if ``presented`` is the linked credential, else None.

    Constant-time: ``hmac.compare_digest`` over the two sha256 digests.
    """
    if not isinstance(presented, str) or not _CRED_RE.fullmatch(presented):
        return None
    rec = load_link()
    if rec is None:
        return None
    ok = hmac.compare_digest(credential_sha256(presented), rec["credential_sha256"])
    return rec if ok else None


# --------------------------------------------------------------------------- placement checks
def check_placement() -> None:
    if os.getenv("ARAIL_INSTANCE", "").strip():
        raise PolicyRefusal("buddy link runs on the root lab only (this is a World instance process).")
    from arail import config
    if not config.bind_is_loopback():
        raise PolicyRefusal(
            "the portal is not bound to loopback (BIND_ADDR). The Buddy edge is off on a LAN-bound lab; "
            "set BIND_ADDR=127.0.0.1 first."
        )


def _loopback_host() -> str:
    from arail import config
    raw = (config.get("BIND_ADDR", "127.0.0.1") or "127.0.0.1").strip().lower()
    return _LOOPBACK_HOSTS.get(raw, "127.0.0.1")


def edge_base_url() -> str:
    port = (os.getenv("PORTAL_PORT", "") or "8080").strip() or "8080"
    return f"http://{_loopback_host()}:{port}/api/host/v1"


def check_buddy_home(path_arg: str) -> Path:
    """The Buddy home must be a real directory (not a symlink), owned by this user, mode 0700."""
    p = Path(path_arg).expanduser()
    try:
        st = os.lstat(p)
    except FileNotFoundError:
        raise PolicyRefusal(f"the Buddy home {p} does not exist; create it with mode 0700 first.") from None
    except OSError as exc:
        raise PolicyRefusal(f"cannot inspect the Buddy home {p}: {exc.strerror}") from None
    if stat.S_ISLNK(st.st_mode):
        raise PolicyRefusal(f"the Buddy home {p} is a symlink; give the real directory.")
    if not stat.S_ISDIR(st.st_mode):
        raise PolicyRefusal(f"the Buddy home {p} is not a directory.")
    if st.st_uid != _current_uid():
        raise PolicyRefusal(f"the Buddy home {p} is not owned by this user.")
    if stat.S_IMODE(st.st_mode) != 0o700:
        raise PolicyRefusal(f"the Buddy home {p} must be mode 0700 (it is {stat.S_IMODE(st.st_mode):04o}); run chmod 700.")
    return p.resolve()


def _check_private_file(path: Path, label: str) -> None:
    """An existing target must be a regular, owned, 0600 file and not a symlink."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(st.st_mode):
        raise PolicyRefusal(f"{label} {path} is a symlink; refusing to write through it.")
    if not stat.S_ISREG(st.st_mode):
        raise PolicyRefusal(f"{label} {path} is not a regular file.")
    if st.st_uid != _current_uid():
        raise PolicyRefusal(f"{label} {path} is not owned by this user.")
    if stat.S_IMODE(st.st_mode) != 0o600:
        raise PolicyRefusal(f"{label} {path} must be mode 0600 (it is {stat.S_IMODE(st.st_mode):04o}); run chmod 600.")


def _ensure_arail_dir() -> Path:
    d = link_dir()
    if os.path.islink(d):
        raise PolicyRefusal(f"{d} is a symlink; refusing to write the link through it.")
    os.makedirs(d, mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)
    return d


# --------------------------------------------------------------------------- writing
def _write_private(path: Path, data: bytes) -> None:
    """Atomic write, mode 0600, never through a symlink."""
    if os.path.islink(path):
        raise PolicyRefusal(f"{path} is a symlink; refusing to write through it.")
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _json_bytes(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2) + "\n").encode("utf-8")


def _set_env_line(text: str, key: str, value: Optional[str]) -> str:
    """Replace, append (value given) or delete (value None) one KEY=VALUE line; other lines untouched."""
    lines = text.splitlines()
    out: list[str] = []
    done = False
    for line in lines:
        if line.split("=", 1)[0].strip() == key and "=" in line:
            if value is not None and not done:
                out.append(f"{key}={value}")
            done = True
            continue
        out.append(line)
    if value is not None and not done:
        out.append(f"{key}={value}")
    return ("\n".join(out) + "\n") if out else ""


def _write_buddy_side(home: Path, credential: Optional[str], rec_fields: Optional[dict]) -> None:
    secrets_path = home / "secrets.env"
    existing = ""
    if os.path.lexists(secrets_path):
        existing = secrets_path.read_text(encoding="utf-8")
    _write_private(secrets_path, _set_env_line(existing, CREDENTIAL_ENV, credential).encode("utf-8"))
    if rec_fields is not None:
        _write_private(home / "link.json", _json_bytes(rec_fields))


# --------------------------------------------------------------------------- operations
def _confirm(stdin: TextIO, stdout: TextIO, assume_yes: bool) -> None:
    if assume_yes:
        return
    if not getattr(stdin, "isatty", lambda: False)():
        raise PolicyRefusal("stdin is not a terminal; re-run at the desk, or pass --yes if you have read the text above.")
    stdout.write("\nType yes to link Buddy: ")
    stdout.flush()
    if stdin.readline().strip() != "yes":
        raise PolicyRefusal("Not linked: consent was not given.")


def do_link(buddy_home: str, *, rotate: bool, assume_yes: bool, stdin: TextIO, stdout: TextIO, consent_store=None) -> int:
    check_placement()
    home = check_buddy_home(buddy_home)
    _check_private_file(home / "secrets.env", "secrets.env")
    _check_private_file(home / "link.json", "link.json")
    existing = load_link()
    if rotate and existing is None:
        raise PolicyRefusal("not linked: nothing to rotate. Run `./arailctl buddy link --buddy-home PATH` first.")
    if existing is not None and not rotate:
        raise PolicyRefusal(
            f"already linked (credential {existing.get('credential_id')}). "
            "Use `link --rotate` to replace the credential, or `unlink` first."
        )
    _ensure_arail_dir()
    _check_private_file(link_path(), "ARAIL link.json")

    consent_request_id: Optional[str] = existing.get("consent_request_id") if existing else None
    if not rotate:
        stdout.write(CONSENT_TEXT + "\n")
        _confirm(stdin, stdout, assume_yes)
        from arail.agents.consent import ConsentStore
        store = consent_store if consent_store is not None else ConsentStore()
        req = store.request_access(CONSENT_URL, reason=CONSENT_REASON, agent=CONSENT_AGENT)
        if req.get("status") == "pending":
            store.approve(req["id"], remember_domain=True)
        consent_request_id = req["id"]

    credential = mint_credential()
    sha = credential_sha256(credential)
    now = _utc_now()
    url = edge_base_url()
    buddy_rec = {
        "schema": BUDDY_LINK_SCHEMA, "edge_base_url": url, "credential_id": credential_id(sha),
        "contract_sha256": CONTRACT_SHA256, "linked_at": now,
    }
    arail_rec = {
        "schema": ARAIL_LINK_SCHEMA, "credential_sha256": sha, "credential_id": credential_id(sha), "linked_at": now,
        "buddy_home": str(home), "edge_base_url": url, "contract_sha256": CONTRACT_SHA256,
        "consent_request_id": consent_request_id,
    }
    # Buddy's side first: "ARAIL is linked" then implies Buddy holds the credential.
    _write_buddy_side(home, credential, buddy_rec)
    _write_private(link_path(), _json_bytes(arail_rec))
    _cache.update(key=None, rec=None)
    verb = "Rotated" if rotate else "Linked"
    stdout.write(f"{verb}. Credential {credential_id(sha)} written to {home / 'secrets.env'} (mode 0600).\n")
    if rotate:
        stdout.write("The old credential is dead now. Restart Buddy so he reads the new one.\n")
    else:
        stdout.write(
            "Buddy listens only while the lab is in hybrid mode. The lab's mode is "
            f"{_lab_mode_word()} now.\n"
        )
    return 0


def _lab_mode_word() -> str:
    try:
        from arail import config
        return str(config.lab_mode())
    except Exception:  # noqa: BLE001
        return "unknown"


def do_unlink(buddy_home: Optional[str], *, stdout: TextIO, stderr: TextIO, consent_store=None) -> int:
    from arail.agents.consent import ConsentStore
    store = consent_store if consent_store is not None else ConsentStore()
    existing = load_link()
    path = link_path()
    if os.path.lexists(path):
        try:
            os.unlink(path)
        except OSError as exc:
            raise PolicyRefusal(f"could not remove {path}: {exc.strerror}") from None
    _cache.update(key=None, rec=None)
    store.remove_domain(CONSENT_DOMAIN)
    stdout.write(("Unlinked: ARAIL's link is deleted and consent to " + CONSENT_DOMAIN + " is revoked.\n")
                 if existing is not None else
                 "Not linked (nothing to remove on ARAIL's side); consent to " + CONSENT_DOMAIN + " revoked if it was present.\n")
    if buddy_home:
        try:
            home = check_buddy_home(buddy_home)
            _check_private_file(home / "secrets.env", "secrets.env")
            sec = home / "secrets.env"
            if os.path.lexists(sec):
                _write_private(sec, _set_env_line(sec.read_text(encoding="utf-8"), CREDENTIAL_ENV, None).encode("utf-8"))
            bl = home / "link.json"
            if os.path.lexists(bl):
                if os.path.islink(bl):
                    raise PolicyRefusal(f"{bl} is a symlink; not touching it.")
                os.unlink(bl)
            stdout.write(f"Removed Buddy's link and credential line from {home}.\n")
        except PolicyRefusal as exc:
            stderr.write(f"ARAIL's side is unlinked, but Buddy's files were not touched: {exc}\n")
            return 3
    return 0


def status(consent_store=None) -> dict:
    from arail.agents.consent import ConsentStore
    store = consent_store if consent_store is not None else ConsentStore()
    rec = load_link()
    try:
        consent = bool(store.is_allowed(CONSENT_URL))
    except Exception:  # noqa: BLE001 - a corrupt consent file reads as no consent
        consent = False
    return {
        "linked": rec is not None,
        "credential_id": rec.get("credential_id") if rec else None,
        "linked_at": rec.get("linked_at") if rec else None,
        "buddy_home": rec.get("buddy_home") if rec else None,
        "edge_base_url": rec.get("edge_base_url") if rec else None,
        "contract_sha256": rec.get("contract_sha256") if rec else None,
        "lab_contract_sha256": CONTRACT_SHA256,
        "contract_current": bool(rec and rec.get("contract_sha256") == CONTRACT_SHA256),
        "consent": {CONSENT_DOMAIN: consent},
    }


# --------------------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="arailctl buddy",
        description="Link this lab to Buddy, the always-on QA expert (loopback edge buddy.host/v1).",
        epilog="exit codes: 0 ok - 2 usage - 3 refused by policy. The credential is never printed.",
    )
    sub = ap.add_subparsers(dest="verb")
    p = sub.add_parser("link", help="mint the credential and record consent to api.telegram.org")
    p.add_argument("--buddy-home", required=True, metavar="PATH", help="Buddy's home (a real directory, mode 0700, yours)")
    p.add_argument("--rotate", action="store_true", help="replace the credential; the old one dies at once")
    p.add_argument("--yes", action="store_true", help="skip the typed confirmation (the consent text is still printed)")
    p = sub.add_parser("unlink", help="delete ARAIL's link and revoke consent to api.telegram.org")
    p.add_argument("--buddy-home", metavar="PATH", help="also remove Buddy's link.json and credential line")
    p = sub.add_parser("status", help="is this lab linked? (never shows the credential)")
    p.add_argument("--json", action="store_true")
    return ap


class _Usage(Exception):
    pass


def main(argv: Optional[list[str]] = None, *, stdin: Optional[TextIO] = None, stdout: Optional[TextIO] = None,
         stderr: Optional[TextIO] = None, consent_store=None) -> int:
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr
    ap = build_parser()
    try:
        saved = sys.stderr
        sys.stderr = stderr
        try:
            args = ap.parse_args(argv)
        finally:
            sys.stderr = saved
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2
    if args.verb is None:
        ap.print_usage(stderr)
        return 2
    try:
        if args.verb == "link":
            return do_link(args.buddy_home, rotate=args.rotate, assume_yes=args.yes, stdin=stdin, stdout=stdout,
                           consent_store=consent_store)
        if args.verb == "unlink":
            return do_unlink(args.buddy_home, stdout=stdout, stderr=stderr, consent_store=consent_store)
        data = status(consent_store)
        if args.json:
            stdout.write(json.dumps(data, indent=2) + "\n")
        else:
            stdout.write(_status_text(data))
        return 0
    except PolicyRefusal as exc:
        stderr.write(f"refused: {exc}\n")
        return 3


def _status_text(d: dict) -> str:
    if not d["linked"]:
        return "Buddy: not linked.\n"
    cur = "current" if d["contract_current"] else "OLDER than this lab's contract; re-link to refresh"
    return (f"Buddy: linked (credential {d['credential_id']}) since {d['linked_at']}\n"
            f"  home:     {d['buddy_home']}\n  edge:     {d['edge_base_url']}\n"
            f"  contract: {str(d['contract_sha256'])[:12]} ({cur})\n"
            f"  consent:  api.telegram.org {'granted' if d['consent'][CONSENT_DOMAIN] else 'NOT granted'}\n")
