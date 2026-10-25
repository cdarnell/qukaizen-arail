"""``./arailctl auth`` - portal credentials from the machine's own shell (ARCHITECTURE.md C7).

    arailctl auth token [--rotate] [--world SLUG]   print the token's id and path, never its value
    arailctl auth reset [--world SLUG]              new lab passphrase (asked twice), sessions revoked
    arailctl auth logout-all [--world SLUG]         revoke every browser session
    arailctl auth unlock [--world SLUG]             clear a login lockout (wrong guesses from any local process)

Exit codes: 0 ok, 2 usage, 3 refused (not onboarded, unsafe file, or not the lab's owner).
``--world`` is resolved by the shell wrapper (it exports ARAIL_INSTANCE and ARAIL_DATA_DIR from the
instance registry), so by the time this runs ``config.DATA_DIR`` is already that instance's.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

from arail import config
from arail.portal import auth

REFUSED = 3


def _read_pass(prompt: str) -> str:
    return getpass.getpass(prompt)


def _owner_refusal() -> Optional[str]:
    d = Path(config.DATA_DIR)
    try:
        st = os.stat(d)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"cannot read {d} ({type(exc).__name__})"
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        try:
            import pwd
            owner = pwd.getpwuid(st.st_uid).pw_name
        except Exception:  # noqa: BLE001
            owner = f"uid {st.st_uid}"
        return f"this lab belongs to {owner}; run as that user."
    return None


def _onboarded() -> bool:
    from arail.portal.app import _lab_password_set
    return _lab_password_set()


def cmd_token(rotate: bool, out) -> int:
    if not _onboarded():
        print("This lab has no passphrase yet. Finish /welcome in the browser first.", file=out)
        return REFUSED
    try:
        if rotate:
            tid = auth.rotate_token()
        else:
            auth.ensure_token()
            auth.ensure_shim_token()
            tid = auth.read_token_id()
    except OSError as exc:
        print(f"Cannot write the token file ({type(exc).__name__}).", file=out)
        return REFUSED
    if tid is None:
        print(f"{auth.token_path()} is unsafe (symlink, wrong owner, or mode wider than 0600). "
              f"Fix with: chmod 600 {auth.token_path()}", file=out)
        return REFUSED
    print(f"token id: {tid}", file=out)
    print(f"path:     {auth.token_path()}", file=out)
    if rotate:
        print("The old token stops working on the next request.", file=out)
    return 0


def cmd_reset(out) -> int:
    from arail.portal import app as portal_app
    if not _onboarded():
        print("This lab has no passphrase yet. Finish /welcome in the browser first.", file=out)
        return REFUSED
    first = _read_pass("New lab passphrase: ").strip()
    second = _read_pass("Again: ").strip()
    if len(first) < 8:
        print("The passphrase must be at least 8 characters.", file=out)
        return REFUSED
    if first != second:
        print("The two entries did not match.", file=out)
        return REFUSED
    if first in portal_app._PASSWORD_PLACEHOLDERS:
        print("That looks like a placeholder; pick something real.", file=out)
        return REFUSED
    portal_app._write_env_kv("ARAIL_PASSWORD", first)
    portal_app._write_env_kv("OPEN_NOTEBOOK_ENCRYPTION_KEY", first)
    portal_app._patch_lab_conf_password(first)
    try:
        portal_app._write_code_server_password(first)
    except OSError:
        print("Note: the code-server config could not be written; re-run setup to unlock the IDE.", file=out)
    auth.get_store().revoke_all()
    auth.unlock_logins()
    print("Passphrase changed. Every browser session was ended and the running portal takes the new passphrase at its next login; restart the lab so the IDE and notebooks pick it up too.", file=out)
    return 0


def cmd_logout_all(out) -> int:
    auth.get_store().revoke_all()
    print("All browser sessions ended.", file=out)
    return 0


def cmd_unlock(out) -> int:
    auth.unlock_logins()
    print("Login attempts unlocked.", file=out)
    return 0


def main(argv: Optional[Sequence[str]] = None, out=None) -> int:
    out = out or sys.stdout
    ap = argparse.ArgumentParser(prog="arailctl auth", add_help=True)
    sub = ap.add_subparsers(dest="verb")
    t = sub.add_parser("token")
    t.add_argument("--rotate", action="store_true")
    t.add_argument("--world", default=None)
    for name in ("reset", "logout-all", "unlock"):
        p = sub.add_parser(name)
        p.add_argument("--world", default=None)
    try:
        ns = ap.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return 2 if exc.code else 0
    if not ns.verb:
        ap.print_usage(out)
        return 2
    why = _owner_refusal()
    if why:
        print(why, file=out)
        return REFUSED
    if ns.verb == "token":
        return cmd_token(ns.rotate, out)
    if ns.verb == "reset":
        return cmd_reset(out)
    if ns.verb == "unlock":
        return cmd_unlock(out)
    return cmd_logout_all(out)


if __name__ == "__main__":
    sys.exit(main())
