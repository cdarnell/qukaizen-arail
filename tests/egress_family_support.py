"""Support code for the httpx-family egress tests.  Not a test module: pytest does not collect it.

Standard library only, and it must stay that way.  The CI leg that has neither httpx nor httpx2
imports this module (through tests/test_egress_httpx.py) and must reach the sentinel, so nothing
here may import httpx, httpx2, httpcore, httpcore2, huggingface_hub or any other HTTP client.

LITERAL_FAMILY is spelled out on purpose and is not read from arail.httpx_family.  A test that
parametrizes over the guard's own list agrees with a guard whose list is wrong; this list is the
independent one, and tests/test_httpx_family.py pins the two together.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

LITERAL_FAMILY: tuple[str, ...] = ("httpx2", "httpx")

# A name that never resolves (RFC 6761), so a request that is NOT refused by the guard ends as a
# fast ConnectError and never as a connection to a real host.
PROBE_URL = "http://egress-probe.invalid/"

# What the in-process tests make socket.gethostbyname() answer for any name, so that "does this name
# resolve to a local address" is deterministic and asks no resolver.  It must be globally routable:
# NOT 203.0.113.0/24 (TEST-NET-3) or the other documentation ranges, which ipaddress reports as
# private, so the guard would call a host that resolves there local and let it through.  Only
# gethostbyname is patched (the guard's lookup); getaddrinfo, which the HTTP libraries use, stays
# real, so a guard regression is still a ConnectError on a .invalid name.
PUBLIC_TEST_IP = "151.101.64.81"

DECLARATION_ENV = "ARAIL_TEST_EXPECT_HTTPX_FAMILY"


def installed_independently() -> tuple[str, ...]:
    """The members of LITERAL_FAMILY that are installed, in LITERAL_FAMILY order.

    Computed without arail.httpx_family: this module's own ``find_spec`` on the literal names,
    cross-checked against the installed distribution metadata.  The two must agree.  When they
    do not (a leftover directory called ``httpx`` with no metadata, or a dist-info whose package
    is gone) the environment is not one of the states the tests are written for, and this raises
    AssertionError instead of picking an answer.
    """
    found = []
    problems = []
    for name in LITERAL_FAMILY:
        spec = importlib.util.find_spec(name)
        try:
            version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            version = None
        if (spec is not None) != (version is not None):
            problems.append(
                f"{name}: find_spec says {'importable' if spec is not None else 'absent'}, "
                f"importlib.metadata says {'version ' + version if version else 'not installed'}"
            )
        if spec is not None:
            found.append(name)
    if problems:
        raise AssertionError(
            "this environment is inconsistent, so the httpx-family tests cannot say which "
            "libraries it has: " + "; ".join(problems)
        )
    return tuple(found)


def declared_family() -> tuple[str, ...] | None:
    """What this test run declares its environment has, or None when it declares nothing.

    ARAIL_TEST_EXPECT_HTTPX_FAMILY is a comma-separated list of family members (``httpx2``,
    ``httpx``, ``httpx,httpx2``) or the literal ``none``.  CI sets it on every leg, so a leg that
    silently lost a library fails instead of testing less.  Returns the members in LITERAL_FAMILY
    order (``()`` for ``none``).  An empty value, an unknown name, or ``none`` mixed with a name is
    a mistake in the declaration and raises AssertionError saying so.
    """
    raw = os.environ.get(DECLARATION_ENV)
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        raise AssertionError(
            f"{DECLARATION_ENV} is set but empty; use a comma-separated list of "
            f"{', '.join(LITERAL_FAMILY)}, or the word none"
        )
    if text.lower() == "none":
        return ()
    names = [part.strip() for part in text.split(",")]
    bad = [name for name in names if name not in LITERAL_FAMILY]
    if bad:
        raise AssertionError(
            f"{DECLARATION_ENV}={raw!r} names {bad!r}; the choices are "
            f"{', '.join(LITERAL_FAMILY)} (comma separated) or the word none on its own"
        )
    return tuple(name for name in LITERAL_FAMILY if name in names)


# This checkout, found from this file.  The editable install that put ``arail`` on sys.path may point
# at ANOTHER checkout (tests/conftest.py documents how a git worktree ends up there), so a fresh
# interpreter is pointed at this one explicitly and then checked.
CHECKOUT = Path(__file__).resolve().parents[1]
CHECKOUT_SRC = CHECKOUT / "src"


def run_fresh(code, *args, tmp_path, extra_env=None, timeout=60):
    """Run ``code`` in a fresh interpreter and return the JSON object it prints on its last line.

    Fresh means no patch state is shared with the test process: this is how the brief's probe is
    run, and how import order and aliasing are tested.  ``code`` receives ``args`` as ``sys.argv[1:]``
    and must end by printing one JSON object that has the key ``egress_file`` (``arail.egress.__file__``).

    The environment is the caller's with ``ARAIL_MODE``, ``HF_HUB_OFFLINE`` (offline mode would make
    huggingface_hub refuse before it reached any transport, so nothing would be proven) and every
    ``*_proxy`` variable removed, and ``LAB_MODE=airgapped``, ``ARAIL_DATA_DIR`` (``<tmp>/data``, where
    the audit log goes), ``HF_HOME``, ``HF_HUB_DISABLE_TELEMETRY=1`` and ``ARAIL_ENV_FILE`` (a file
    that does not exist) set.  ``PYTHONPATH`` is prefixed with this checkout's ``src``, and ``-I`` is
    never used, so a test is never talking to another checkout's guard: the printed ``egress_file`` is
    asserted to be inside this one.  Asserts exit code 0 (with both output tails in the message).
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("ARAIL_MODE", "HF_HUB_OFFLINE") and not key.lower().endswith("_proxy")
    }
    env.update(
        LAB_MODE="airgapped",
        ARAIL_DATA_DIR=str(Path(tmp_path) / "data"),
        HF_HOME=str(Path(tmp_path) / "hf-home"),
        HF_HUB_DISABLE_TELEMETRY="1",
        ARAIL_ENV_FILE=str(Path(tmp_path) / "none.env"),
    )
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(CHECKOUT_SRC) + (os.pathsep + existing if existing else "")
    env.update(extra_env or {})
    proc = subprocess.run(
        [sys.executable, "-c", code, *args],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert proc.returncode == 0, (
        f"the fresh interpreter exited {proc.returncode}\n--- stdout (tail) ---\n{proc.stdout[-2000:]}"
        f"\n--- stderr (tail) ---\n{proc.stderr[-3000:]}"
    )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert lines, f"the fresh interpreter printed nothing\n--- stderr (tail) ---\n{proc.stderr[-2000:]}"
    result = json.loads(lines[-1])
    egress_file = Path(result["egress_file"]).resolve()
    assert CHECKOUT_SRC.resolve() in egress_file.parents, (
        f"the fresh interpreter imported arail.egress from {egress_file}, not from this checkout "
        f"({CHECKOUT_SRC}); the test would be exercising another tree's guard"
    )
    return result
