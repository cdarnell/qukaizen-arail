"""arail.httpx_family: the one place that names the httpx family (httpx2, httpx).

TD50/TD51.  The egress guard, the doctor and the Open Notebook seeding all need "which httpx
library is here"; the answer lives in one stdlib-only module so that a new family member is added
once.  These tests pin the list, the lookup (which must agree with an independent one), the
preference order, and the two ways it can fail: neither library installed, and the preferred one
installed but broken.

Every installed-state assertion compares against tests.egress_family_support, which looks the
libraries up on its own.  A test that asked httpx_family what is installed and then asserted what
httpx_family says would pass on a broken lookup.
"""

from __future__ import annotations

import ast
import importlib.abc
import importlib.machinery
import importlib.util
import sys
import types
from pathlib import Path

import pytest

from arail import httpx_family
from tests.egress_family_support import LITERAL_FAMILY, installed_independently


# --- HF-1: the list is pinned --------------------------------------------------------------

def test_family_is_pinned_newest_first():
    """The order and content of FAMILY are a decision, not an accident.

    httpx2 is the successor package (huggingface-hub 2.x, anthropic 2.x, openai 3.x, starlette
    1.x use it), so it comes first, which is also starlette's own preference.  Changing this tuple
    must also change the docs and tests/egress_family_support.LITERAL_FAMILY; this test is the
    reminder.
    """
    assert httpx_family.FAMILY == ("httpx2", "httpx")
    assert httpx_family.FAMILY == LITERAL_FAMILY


# --- HF-2: the lookup agrees with an independent one ---------------------------------------

def test_installed_equals_the_independent_lookup():
    assert httpx_family.installed() == installed_independently()
    for name in LITERAL_FAMILY:
        assert httpx_family.is_installed(name) is (name in installed_independently())


# --- HF-3: preferred() is the first installed member ---------------------------------------

def test_preferred_is_the_first_installed_member():
    present = installed_independently()
    if not present:
        # The negative-control environment (neither library): the failure is the answer.
        with pytest.raises(ModuleNotFoundError):
            httpx_family.preferred()
        return
    assert httpx_family.preferred().__name__ == present[0]


# --- HF-4: neither installed ---------------------------------------------------------------

def test_preferred_names_every_member_when_neither_is_installed(monkeypatch):
    for name in LITERAL_FAMILY:
        monkeypatch.setitem(sys.modules, name, None)
    assert httpx_family.installed() == ()
    with pytest.raises(ModuleNotFoundError) as excinfo:
        httpx_family.preferred()
    message = str(excinfo.value)
    for name in LITERAL_FAMILY:
        assert name in message
    assert "pip install" in message  # it says how to fix it
    assert excinfo.value.name == LITERAL_FAMILY[0]


# --- HF-5: a None entry in sys.modules means absent ----------------------------------------

def test_a_none_entry_in_sys_modules_counts_as_absent(monkeypatch):
    for name in LITERAL_FAMILY:
        monkeypatch.setitem(sys.modules, name, None)  # what the import system reads as "blocked"
        assert httpx_family.is_installed(name) is False
        assert name not in httpx_family.installed()


# --- HF-6: a broken preferred member propagates; no silent fallback ------------------------

class _RaisingLoader(importlib.abc.Loader):
    def create_module(self, spec):
        return None

    def exec_module(self, module):
        raise RuntimeError("simulated: this library is installed but its import raises")


class _FinderForRaisingImport(importlib.abc.MetaPathFinder):
    """Reports one top-level name as present, with a loader whose import raises."""

    def __init__(self, name):
        self.name = name

    def find_spec(self, fullname, path=None, target=None):
        if fullname == self.name:
            return importlib.machinery.ModuleSpec(fullname, _RaisingLoader())
        return None


def test_a_preferred_member_whose_import_raises_propagates_with_no_fallback(monkeypatch):
    """Simulated, so it runs in every leg: whatever is really installed, the first member
    "exists" and fails to import.  A fallback to the second member would hide a broken install,
    which is how the guard went silent in TD50."""
    first = httpx_family.FAMILY[0]
    monkeypatch.delitem(sys.modules, first, raising=False)
    monkeypatch.setattr(sys, "meta_path", [_FinderForRaisingImport(first), *sys.meta_path])
    assert httpx_family.is_installed(first) is True
    with pytest.raises(RuntimeError, match="simulated"):
        httpx_family.preferred()


# --- small properties the contract also states ---------------------------------------------

def test_is_installed_never_imports(tmp_path, monkeypatch):
    package = tmp_path / "arail_never_imported_pkg"
    package.mkdir()
    (package / "__init__.py").write_text("raise RuntimeError('is_installed imported this')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    assert httpx_family.is_installed("arail_never_imported_pkg") is True
    assert "arail_never_imported_pkg" not in sys.modules


def test_a_module_in_sys_modules_with_no_spec_counts_as_installed(monkeypatch):
    module = types.ModuleType("arail_no_spec_module")
    module.__spec__ = None
    monkeypatch.setitem(sys.modules, "arail_no_spec_module", module)
    with pytest.raises(ValueError):  # the precondition: importlib's own lookup refuses it
        importlib.util.find_spec("arail_no_spec_module")
    assert httpx_family.is_installed("arail_no_spec_module") is True
    assert httpx_family.is_installed("arail_no_such_module_anywhere") is False


def test_httpx_family_imports_only_the_standard_library():
    """It is imported by the guard, the doctor, the seeding and the tests that must run where no
    HTTP library exists, so it may not import one itself."""
    tree = ast.parse(Path(httpx_family.__file__).read_text(encoding="utf-8"))
    top_level = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            top_level.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            top_level.add((node.module or "").split(".")[0])
    assert top_level, "the module imports nothing at all?  then this check reads the wrong file"
    assert top_level <= set(sys.stdlib_module_names), sorted(top_level - set(sys.stdlib_module_names))
