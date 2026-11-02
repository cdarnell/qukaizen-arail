"""arail.httpx_family: the one place that names the httpx family (httpx2, httpx).

TD50/TD51.  The egress guard, the doctor and the Open Notebook seeding all need "which httpx
library is here"; the answer lives in one stdlib-only module so that a new family member is added
once.  These tests pin the list, the lookup (which must agree with an independent one), the
preference order, and the two ways it can fail: neither library installed, and the preferred one
installed but broken.

Every installed-state assertion compares against tests.egress_family_support, which looks the
libraries up on its own.  A test that asked httpx_family what is installed and then asserted what
httpx_family says would pass on a broken lookup.

The second half of the file (ON-*, DR-*) covers the two runtime consumers that used to say
``import httpx`` or list only ``httpx``: the Open Notebook seeding and ``./arailctl doctor``
(TD51, runtime half).
"""

from __future__ import annotations

import ast
import base64
import http.server
import importlib
import importlib.abc
import importlib.machinery
import importlib.metadata
import importlib.util
import inspect
import json
import os
import shutil
import socket
import sys
import threading
import types
from pathlib import Path

import pytest

from arail import doctor, httpx_family
from tests.egress_family_support import (
    LITERAL_FAMILY,
    PROBE_URL,
    PUBLIC_TEST_IP,
    installed_independently,
)

# What this environment really has, from the independent lookup, fixed at collection time like the
# egress tests do.  An empty tuple (the negative-control environment) makes the parametrized tests
# below collect nothing; the sentinel in tests/test_egress_httpx.py is what fails there.
INSTALLED = installed_independently()
lib_param = pytest.mark.parametrize("lib", INSTALLED)


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


# =============================================================================================
# TD51, runtime half: the two consumers that used to name `httpx` alone
# =============================================================================================
#
# src/arail/open_notebook_seed.py had `import httpx` at module level, so on a fresh install (httpx2
# only) the portal's /api/open-notebook/start raised ModuleNotFoundError right after `docker compose
# up`.  src/arail/doctor.py listed "httpx" among the component versions, so it printed "not
# installed" for the library the lab was actually using.  Both now go through arail.httpx_family.


@pytest.fixture
def guard_state(monkeypatch, tmp_path):
    """For the tests that install the guard (the doctor does, and the seeding test does on purpose).

    They start and end with the guard uninstalled (tests/conftest.py resets it after every test as
    well), audit lines go to tmp_path and never to lab/data, a stray proxy variable cannot turn a
    loopback call into a hop through a proxy, and a name that must be refused resolves to a public
    address without asking a resolver (see PUBLIC_TEST_IP for why that address).
    """
    import arail.egress as egress

    for key in list(os.environ):
        if key.lower().endswith("_proxy"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LAB_MODE", "airgapped")
    monkeypatch.setattr(socket, "gethostbyname", lambda host: PUBLIC_TEST_IP)
    egress._reset_for_tests()
    yield egress
    egress._reset_for_tests()


def _import_seed_fresh(monkeypatch):
    """Import arail.open_notebook_seed as if for the first time, so it binds its library now."""
    monkeypatch.delitem(sys.modules, "arail.open_notebook_seed", raising=False)
    return importlib.import_module("arail.open_notebook_seed")


# --- ON-1: the seeding module takes the first installed family member ------------------------

def test_the_seeding_module_binds_the_first_installed_family_member(monkeypatch):
    """ON-1.  `httpx` in open_notebook_seed is whatever httpx_family.preferred() returns (httpx2
    before httpx), and it imports in every leg where a library exists.  Where none does (the
    negative-control environment) the import must fail, as it always did."""
    present = installed_independently()
    if not present:
        with pytest.raises(ModuleNotFoundError):
            _import_seed_fresh(monkeypatch)
        return
    seed = _import_seed_fresh(monkeypatch)
    assert seed.httpx.__name__ == present[0]


def test_the_seeding_module_still_fails_at_import_naming_both_libraries_when_neither_is_installed(monkeypatch):
    """ON-1, simulated so it runs in every leg: with both members blocked, the import fails, and
    the message names each of them and how to fix it (a bare `No module named 'httpx'` would send
    the operator after the wrong package)."""
    for name in LITERAL_FAMILY:
        monkeypatch.setitem(sys.modules, name, None)
    with pytest.raises(ModuleNotFoundError) as excinfo:
        _import_seed_fresh(monkeypatch)
    message = str(excinfo.value)
    for name in LITERAL_FAMILY:
        assert name in message
    assert "pip install" in message


# --- ON-2: the seeding code works on whichever library it got, with the guard installed -------

class _FakeOpenNotebook:
    """A stand-in for Open Notebook's HTTP API on 127.0.0.1, bound to port 0 (no fixed port).

    Records what it is sent.  GET /api/notebooks lists what was created; POST /api/notebooks
    creates one (201), except a notebook called "boom", which gets a 500 with a text body.
    """

    def __init__(self):
        self.requests = []  # (method, path, Authorization header, parsed JSON body)
        self.notebooks = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _reply(self, status, payload):
                body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                outer.requests.append(("GET", self.path, self.headers.get("Authorization"), None))
                self._reply(200, outer.notebooks)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                outer.requests.append(("POST", self.path, self.headers.get("Authorization"), body))
                if body.get("name") == "boom":
                    self._reply(500, b"the stand-in refuses this notebook")
                    return
                outer.notebooks.append({"id": "notebook:1", **body})
                self._reply(201, {"id": "notebook:1"})

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc_info):
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=5)


@pytest.mark.parametrize("lib", INSTALLED[:1])  # the preferred library; empty (skipped) where none is installed
def test_the_seeding_code_works_on_the_library_it_got_with_the_guard_installed(lib, monkeypatch, guard_state):
    """ON-2.  Not a mock: the module's own `_wait_for_api`, `_already_seeded` and `_create_notebook`
    talk to a loopback server over the real library, under an installed airgapped guard.  The
    loopback calls pass; the same library is refused for a public host, so the guard really covers
    what the seeding talks through; and the Basic credentials and the JSON body arrive."""
    from arail.airgap import EgressBlocked

    seed = _import_seed_fresh(monkeypatch)
    assert seed.httpx.__name__ == lib
    guard_state.install_guard()
    transport_method = vars(seed.httpx.HTTPTransport)["handle_request"]
    assert getattr(transport_method, "_arail_guarded", False), "the library the seeding uses is not guarded"

    with _FakeOpenNotebook() as api:
        assert seed._wait_for_api(api.port, retries=1, delay=0) is True
        assert seed._already_seeded(api.port) is False  # an empty list: nothing seeded yet
        assert seed._create_notebook(api.port, "Lab notes", "from the lab") == "notebook:1"
        assert seed._already_seeded(api.port) is True  # now there is one
        assert seed._create_notebook(api.port, "boom", "d") is None  # a 500 with a text body
    expected_auth = "Basic " + base64.b64encode(b"admin:secret").decode()
    assert api.requests, "the stand-in saw nothing"
    assert {auth for _method, _path, auth, _body in api.requests} == {expected_auth}
    assert ("POST", "/api/notebooks", expected_auth, {"name": "Lab notes", "description": "from the lab"}) in api.requests
    assert guard_state.read_recent_blocks(5) == [], "a loopback call was audited as refused"

    with pytest.raises(EgressBlocked):
        seed.httpx.get(PROBE_URL, timeout=3)


@lib_param
def test_every_call_the_seeding_makes_exists_on_each_installed_library(lib):
    """ON-3.  The seeding calls `httpx.get`, `post` and `put` with keyword arguments; `put` is only
    reached after the AI-provider setup, which needs a running Open Notebook, so it cannot be driven
    here.  The calls are read from the module's source and each keyword is checked against the
    library's signature, so a changed signature fails here and not in someone's first Open Notebook
    start."""
    source = Path(importlib.util.find_spec("arail.open_notebook_seed").origin).read_text(encoding="utf-8")
    calls = {}
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "httpx"):
            calls.setdefault(node.func.attr, set()).update(kw.arg for kw in node.keywords if kw.arg)
    assert {"get", "post", "put"} <= set(calls), f"the seeding no longer calls get/post/put as this test expects: {sorted(calls)}"

    module = importlib.import_module(lib)
    for verb, keywords in sorted(calls.items()):
        function = getattr(module, verb, None)
        assert callable(function), f"{lib} has no {verb}()"
        parameters = inspect.signature(function).parameters
        accepts_any = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())
        missing = sorted(k for k in keywords if k not in parameters and not accepts_any)
        assert not missing, f"{lib}.{verb}() does not accept {missing}, which open_notebook_seed passes"


# --- DR-1 to DR-3: the doctor ----------------------------------------------------------------

def _doctor_value(output, label):
    """The text after `label` on the doctor's `  label  : value` line, or None."""
    for line in output.splitlines():
        head, sep, value = line.partition(":")
        if sep and head.strip() == label:
            return value.strip()
    return None


def test_the_doctor_lists_every_family_member_with_its_version_or_not_installed(monkeypatch, capsys):
    """DR-1.  It listed `httpx` alone, so it said "not installed" for the library a fresh install
    runs on.  Every member now has a line: the installed distribution's version, or `not installed`."""
    monkeypatch.setattr(shutil, "which", lambda *args, **kwargs: None)  # no ollama subprocess
    doctor.check_components()
    output = capsys.readouterr().out
    present = installed_independently()
    for name in LITERAL_FAMILY:
        expected = importlib.metadata.version(name) if name in present else "not installed"
        assert _doctor_value(output, name) == expected, (name, output)


def test_the_doctor_says_which_clients_the_guard_covered(monkeypatch, capsys, guard_state):
    """DR-2.  `egress guard : installed — guards requests, urllib.request, httpx2 (not installed: httpx)`
    is built from egress.coverage() and agrees with the independent lookup; the required finding
    passes."""
    monkeypatch.setattr(doctor, "_FINDINGS", [])
    doctor.check_environment()
    output = capsys.readouterr().out
    present = installed_independently()
    guarded = ["requests", "urllib.request", *present]
    absent = [name for name in LITERAL_FAMILY if name not in present]
    expected = "installed — guards " + ", ".join(guarded)
    if absent:
        expected += " (not installed: " + ", ".join(absent) + ")"
    assert _doctor_value(output, "egress guard") == expected, output
    # The line is built from coverage(), so the two agree, and the guard itself was the other oracle.
    coverage = guard_state.coverage()
    assert [name for name, value in coverage.items() if value == "guarded"] == guarded
    assert [name for name, value in coverage.items() if value == "absent"] == absent
    findings = [f for f in doctor._FINDINGS if f.name == "egress_guard"]
    assert len(findings) == 1 and findings[0].level == "required" and findings[0].ok is True


@lib_param
def test_an_installed_library_the_guard_cannot_patch_fails_the_doctor_and_is_named(lib, monkeypatch, capsys, guard_state):
    """DR-3.  The class the guard needs is deleted from an installed member: install_guard() raises
    EgressGuardIncomplete, and the doctor records `egress_guard` as a failed REQUIRED check whose
    detail names the library (not an info line a --strict-less run would shrug at), and prints it."""
    monkeypatch.delattr(importlib.import_module(lib), "AsyncHTTPTransport")
    monkeypatch.setattr(doctor, "_FINDINGS", [])
    doctor.check_environment()
    output = capsys.readouterr().out
    findings = [f for f in doctor._FINDINGS if f.name == "egress_guard"]
    assert len(findings) == 1
    assert findings[0].level == "required" and findings[0].ok is False
    assert lib in findings[0].detail and "NOT checked" in findings[0].detail
    line = _doctor_value(output, "egress guard")
    assert line is not None and "EgressGuardIncomplete" in line and lib in line, output


def _only_the_environment_check(monkeypatch):
    for check in ("check_models", "check_knowledge_base", "check_provisioning", "check_components"):
        monkeypatch.setattr(doctor, check, lambda: None)


@lib_param
def test_an_unpatchable_library_degrades_the_doctor_exit_code(lib, monkeypatch, capsys, guard_state):
    """DR-3, the exit code.  main() returns 3 (degraded) when the guard is incomplete and 0 when it
    is whole, with every other check stubbed out so that only the egress finding decides."""
    _only_the_environment_check(monkeypatch)
    monkeypatch.setattr(doctor, "_FINDINGS", [])
    assert doctor.main([]) == 0
    guard_state._reset_for_tests()
    monkeypatch.delattr(importlib.import_module(lib), "AsyncHTTPTransport")
    assert doctor.main([]) == 3
    assert "doctor: degraded" in capsys.readouterr().out
