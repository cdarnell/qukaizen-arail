"""ARCHITECTURE.md §4.4/§9: AeroLLMBackend.__new__'s ImportError message
names three install routes, `deep install` (bundled) first — an outside
user with no sibling repo and no private-index credentials should see the
route that actually works for them before the maintainer-only ones.

We assert the contract (substring present, ordering), not the full prose.
"""
from __future__ import annotations

import sys

import pytest

import arail.router.backends as backends


def test_import_error_names_deep_install_first(monkeypatch):
    # Setting a sys.modules entry to None makes `import aerollm_api` raise
    # ImportError, per Python's import machinery — no need to touch
    # builtins.__import__ or reload the module under test (reloading
    # backends pollutes other test files that hold a reference to the
    # pre-reload module object).
    monkeypatch.setitem(sys.modules, "aerollm_api", None)
    backends.AeroLLMBackend._shared.clear()

    with pytest.raises(ImportError) as exc_info:
        backends.AeroLLMBackend()

    msg = str(exc_info.value)
    assert "./arailctl deep install" in msg
    assert "./arailctl deep rebuild" in msg
    assert "./arailctl deep update" in msg
    # deep install (bundled, no creds needed) is offered before the
    # maintainer-only rebuild/update routes.
    assert msg.index("deep install") < msg.index("deep rebuild")
    assert msg.index("deep install") < msg.index("deep update")

    # Don't leave a failed instance cached under this process's key for
    # later tests in the same session.
    backends.AeroLLMBackend._shared.clear()
