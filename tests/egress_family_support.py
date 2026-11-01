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

LITERAL_FAMILY: tuple[str, ...] = ("httpx2", "httpx")


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
