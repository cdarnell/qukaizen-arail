"""httpx_family: the one place that names the httpx library family.

``httpx2`` is the successor of ``httpx`` and a separate package with its own import name
(``httpx2`` over ``httpcore2``).  huggingface-hub 2.x, anthropic 2.x, openai 3.x and starlette 1.x
use it in place of ``httpx``, so one machine can have either library, both, or (in a test
environment) neither, and "import httpx" is no longer a safe way to say "the HTTP library".

Everything in ARAIL that has to know which of them is installed reads this module:

- ``arail.egress`` patches the transports of every installed member (TD50);
- ``arail.doctor`` lists every member;
- ``arail.open_notebook_seed`` talks to a local service through ``preferred()`` (TD51).

``FAMILY`` is a fixed list, deliberately: guessing at runtime which packages "look like" httpx
would import arbitrary code at portal start.  A new member (a future ``httpx3``) is added here,
in the docs, and in ``tests/egress_family_support.LITERAL_FAMILY``; a test fails until all three
agree, and another test fails when an unknown ``httpx*`` module shows up on ``sys.path``.

Standard library only, and importing this module imports no member of the family.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from types import ModuleType

# Import names, newest first.  This is also starlette's own preference order.
FAMILY: tuple[str, ...] = ("httpx2", "httpx")

_MISSING = object()


def is_installed(name: str) -> bool:
    """True if ``name`` (a top-level module name) can be imported.  Never imports it.

    A module already in ``sys.modules`` is installed, whatever its ``__spec__`` says.  A
    ``None`` entry in ``sys.modules`` is how the import system records "blocked", and importing
    it raises, so it counts as absent.  Otherwise the answer is whether ``find_spec`` finds it;
    a ``ValueError`` from ``find_spec`` (a module with ``__spec__ = None``) is absent here,
    because it is not in ``sys.modules``.
    """
    module = sys.modules.get(name, _MISSING)
    if module is None:
        return False
    if module is not _MISSING:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except ValueError:
        return False


def installed() -> tuple[str, ...]:
    """The installed members of ``FAMILY``, in ``FAMILY`` order.  Imports nothing."""
    return tuple(name for name in FAMILY if is_installed(name))


def preferred() -> ModuleType:
    """Import and return the first installed member of ``FAMILY``.

    There is no fallback past a broken library: if the first installed member is present but its
    import raises, that exception propagates, because moving on to the next member would hide an
    install that is broken (which is how the egress guard went silent in TD50).

    Raises ``ModuleNotFoundError`` (``name`` is the first member) when none is installed, with a
    message that names every member and says how to fix it.
    """
    for name in FAMILY:
        if is_installed(name):
            return importlib.import_module(name)
    raise ModuleNotFoundError(
        "none of the httpx-family libraries is installed ("
        + ", ".join(FAMILY)
        + "); install one, for example: pip install "
        + FAMILY[0],
        name=FAMILY[0],
    )
