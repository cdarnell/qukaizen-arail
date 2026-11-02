"""The egress guard, run the way the brief runs it: in a fresh interpreter (TD50).

``tests/test_egress_httpx.py`` proves the guard in-process.  These tests prove it in a process that
shares no patch state with pytest: the interpreter imports the library, builds clients, sets
``LAB_MODE=airgapped``, calls ``arail.egress.install_guard()``, and makes the request.  The child
only REPORTS what it saw; this file judges, so an unguarded library fails here with an explanation
instead of a crash.  ``tests.egress_family_support.run_fresh`` points the child at THIS checkout and
asserts it imported this checkout's ``arail.egress``.

- FR-1: the brief's probe, for every client shape (sync/async, built before/after the guard).
- FR-2: the guard installed BEFORE anything imported the library.
- FR-3: one class reached under two names (``httpx2.alias_httpx()``) is wrapped once.
- FR-4: an installed library whose import raises makes the install fail loudly.
- FR-5: huggingface_hub (a core dependency, the lab's model downloader), never skipped.

Every request goes to ``egress-probe.invalid``, a name that never resolves (RFC 6761): a guard that
fails shows as a fast ConnectError, never as a connection to a real host.  Nothing here imports an
HTTP library or huggingface_hub at module level; the child does.
"""

from __future__ import annotations

import json

import pytest

from tests.egress_family_support import (
    LITERAL_FAMILY,
    installed_independently,
    run_fresh,
)

INSTALLED = installed_independently()
lib_param = pytest.mark.parametrize("lib", INSTALLED)

HOST = "egress-probe.invalid"


def _audit(tmp_path):
    path = tmp_path / "data" / "egress.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _evidence(title, rows):
    """Print a table; pytest shows it on failure, and with -s or -rP when asked for."""
    print(f"\nEVIDENCE {title}")
    for row in rows:
        print("  " + " | ".join(str(cell) for cell in row))


def _explain_outcomes(lib, bad):
    """``bad`` maps a client shape to the outcome that was not EgressBlocked."""
    return (
        f"{lib}: " + "; ".join(f"{variant} -> {outcome}" for variant, outcome in bad.items()) + ".\n"
        "  Means: EgressBlocked is the guard refusing before any socket I/O; ConnectError is the request "
        "reaching the network layer (the TD50 shape: this library is not guarded).\n"
        "  Bar: EgressBlocked in every cell, because the airgap promises that cloud calls are refused at "
        "the HTTP layer and a library that is not guarded breaks that for everything built on it.\n"
        "  Out of range: NO EXCEPTION on a .invalid host means the resolver answers reserved names (a DNS "
        "wildcard); look at the machine before the guard.\n"
        "  Where: arail.egress._install_httpx_family_guard"
    )


# A child's common tail: what it saw, as one JSON line.  ``attempt`` turns an outcome into a string.
_COMMON = r'''
import asyncio, importlib, json, os, sys, time

URL = "http://egress-probe.invalid/"


def attempt(egress_blocked, fn):
    started = time.monotonic()
    try:
        fn()
        outcome = "NO EXCEPTION"
    except egress_blocked:
        outcome = "EgressBlocked"
    except BaseException as exc:
        outcome = type(exc).__name__
    return {"outcome": outcome, "seconds": round(time.monotonic() - started, 3)}


async def aget(client):
    await client.get(URL)
'''


# --- FR-1: the brief's probe ------------------------------------------------------------------

_PROBE = _COMMON + r'''
lib = sys.argv[1]
mod = importlib.import_module(lib)
# Clients built BEFORE the guard exists: the pattern SDKs follow.
sync_before = mod.Client(timeout=3)
async_before = mod.AsyncClient(timeout=3)

os.environ["LAB_MODE"] = "airgapped"
import arail.egress as egress
from arail.airgap import EgressBlocked

egress.install_guard()

variants = {
    "sync-after (the brief's probe)": attempt(EgressBlocked, lambda: mod.Client(timeout=3).get(URL)),
    "sync-before": attempt(EgressBlocked, lambda: sync_before.get(URL)),
    "async-after": attempt(EgressBlocked, lambda: asyncio.run(aget(mod.AsyncClient(timeout=3)))),
    "async-before": attempt(EgressBlocked, lambda: asyncio.run(aget(async_before))),
}
print(json.dumps({
    "egress_file": egress.__file__,
    "lib_version": getattr(mod, "__version__", "?"),
    "coverage": egress.coverage() if hasattr(egress, "coverage") else None,
    "variants": variants,
}))
'''


@lib_param
def test_the_brief_probe_ends_in_egressblocked_for_every_client_shape(lib, tmp_path):
    """FR-1.  ``LAB_MODE=airgapped`` + ``install_guard()`` + ``<lib>.Client(timeout=3).get(<.invalid>)``
    ends in EgressBlocked, and so do the async client and both kinds of client built before the guard.
    Four audit lines, all for the probe host; ``coverage()`` agrees."""
    result = run_fresh(_PROBE, lib, tmp_path=tmp_path)
    variants = result["variants"]
    _evidence(
        f"FR-1 {lib} {result['lib_version']}: variant | outcome | seconds",
        [(name, v["outcome"], v["seconds"]) for name, v in variants.items()],
    )
    bad = {name: v["outcome"] for name, v in variants.items() if v["outcome"] != "EgressBlocked"}
    assert not bad, _explain_outcomes(lib, bad)
    lines = _audit(tmp_path)
    assert len(lines) == len(variants) and {line["url_host"] for line in lines} == {HOST}, lines
    assert result["coverage"] is not None, "arail.egress.coverage() does not exist, so what the guard covered cannot be read"
    assert result["coverage"][lib] == "guarded", result["coverage"]


# --- FR-2: install first, import later --------------------------------------------------------

_INSTALL_FIRST = _COMMON + r'''
lib = sys.argv[1]
assert lib not in sys.modules, lib + " was already imported when the interpreter started"
import arail.egress as egress
from arail.airgap import EgressBlocked

assert lib not in sys.modules, "importing arail.egress imported " + lib
egress.install_guard()
imported_by_install = lib in sys.modules
mod = importlib.import_module(lib)
variants = {
    "sync": attempt(EgressBlocked, lambda: mod.Client(timeout=3).get(URL)),
    "async": attempt(EgressBlocked, lambda: asyncio.run(aget(mod.AsyncClient(timeout=3)))),
}
print(json.dumps({
    "egress_file": egress.__file__,
    "imported_by_install": imported_by_install,
    "variants": variants,
}))
'''


@lib_param
def test_a_guard_installed_before_the_library_is_imported_still_guards_it(lib, tmp_path):
    """FR-2.  ``install_guard()`` runs while the library is not in ``sys.modules``; only then is it
    imported, and clients are built and used.  The guard imports the family itself (eagerly), so the
    check exists before any code can use the library: portal start installs the guard before most
    imports."""
    result = run_fresh(_INSTALL_FIRST, lib, tmp_path=tmp_path)
    bad = {f"install-first / {name}": v["outcome"] for name, v in result["variants"].items() if v["outcome"] != "EgressBlocked"}
    assert not bad, _explain_outcomes(lib, bad)
    assert result["imported_by_install"] is True, "install_guard() should have imported the installed library"
    assert len(_audit(tmp_path)) == 2


# --- FR-3: one class under two names ----------------------------------------------------------

_ALIAS = _COMMON + r'''
lib, other = sys.argv[1], sys.argv[2]
mod = importlib.import_module(lib)
if lib == "httpx2" and hasattr(mod, "alias_httpx"):
    mod.alias_httpx()  # the real thing: sys.modules["httpx"] becomes the httpx2 module
    how = "httpx2.alias_httpx()"
else:
    sys.modules[other] = mod  # what alias_httpx() does, and a stand-in for a package that re-exports
    how = "sys.modules[%r] = <%s>" % (other, lib)
assert sys.modules[other] is mod, "the alias did not take"

import arail.egress as egress
from arail.airgap import EgressBlocked

sync_cls, async_cls = mod.HTTPTransport, mod.AsyncHTTPTransport
originals = (vars(sync_cls)["handle_request"], vars(async_cls)["handle_async_request"])
egress.install_guard()

wrapped = vars(sync_cls)["handle_request"]
outcome = attempt(EgressBlocked, lambda: sys.modules[other].Client(timeout=3).get(URL))
patched = getattr(egress, "_PATCHED", None)
n_patched = None if patched is None else len(patched)
coverage = egress.coverage() if hasattr(egress, "coverage") else None
wrapped_once = bool(getattr(wrapped, "_arail_guarded", False)) and not getattr(
    getattr(wrapped, "__wrapped__", None), "_arail_guarded", False)
egress._reset_for_tests()
restored = (vars(sync_cls)["handle_request"] is originals[0]
            and vars(async_cls)["handle_async_request"] is originals[1])
print(json.dumps({
    "egress_file": egress.__file__,
    "how": how,
    "n_patched": n_patched,
    "wrapped_once": wrapped_once,
    "outcome": outcome["outcome"],
    "coverage": coverage,
    "restored": restored,
}))
'''


@pytest.mark.parametrize("lib,other", [(l, o) for l in INSTALLED for o in LITERAL_FAMILY if o != l])
def test_one_class_reached_under_two_names_is_wrapped_once(lib, other, tmp_path):
    """FR-3.  ``alias_httpx()`` (httpx2) makes ``sys.modules["httpx"]`` the httpx2 module; a package
    that re-exports another's classes does the same in effect.  The guard must wrap each class once
    (two ``_PATCHED`` entries), refuse once (one audit line, not two), and a reset must put the
    library's own functions back."""
    result = run_fresh(_ALIAS, lib, other, tmp_path=tmp_path)
    assert result["outcome"] == "EgressBlocked", _explain_outcomes(lib, {f"via the name {other!r}": result["outcome"]})
    assert result["n_patched"] == 2, f"expected the two classes of one library ({result['how']}), got {result['n_patched']} entries"
    assert result["wrapped_once"] is True, "the transport method is wrapped more than once (or not at all)"
    assert result["coverage"] is not None, "arail.egress.coverage() does not exist, so what the guard covered cannot be read"
    assert result["coverage"][lib] == result["coverage"][other] == "guarded", result["coverage"]
    assert len(_audit(tmp_path)) == 1, "one refusal must write exactly one audit line (a double wrap writes two)"
    assert result["restored"] is True, "reset did not put the library's own functions back"


# --- FR-4: an installed library whose import raises -------------------------------------------

_BROKEN_IMPORT = _COMMON + r'''
import importlib.abc, importlib.machinery

first = sys.argv[1]


class RaisingLoader(importlib.abc.Loader):
    def create_module(self, spec):
        return None

    def exec_module(self, module):
        raise RuntimeError("simulated: this library is installed but its import raises")


class Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == first:
            return importlib.machinery.ModuleSpec(fullname, RaisingLoader())
        return None


sys.meta_path.insert(0, Finder())
import requests
import arail.egress as egress

raised = None
try:
    egress.install_guard()
except Exception as exc:
    raised = {"type": type(exc).__name__, "message": str(exc), "uncovered": getattr(exc, "uncovered", None)}
print(json.dumps({
    "egress_file": egress.__file__,
    "raised": raised,
    "coverage": egress.coverage() if hasattr(egress, "coverage") else None,
    "installed_flag": egress._INSTALLED,
    "requests_guarded": requests.adapters.HTTPAdapter is egress.GuardedHTTPAdapter,
}))
'''


def test_an_installed_library_whose_import_raises_makes_the_install_fail_loudly(tmp_path):
    """FR-4.  Simulated, so it runs in every leg: the first family member is made to look installed
    and to raise when imported.  Everything else is still guarded, the error says which library and
    why, and the guard does not call itself installed."""
    first = LITERAL_FAMILY[0]
    result = run_fresh(_BROKEN_IMPORT, first, tmp_path=tmp_path)
    assert result["raised"] is not None, (
        f"install_guard() returned quietly although {first} is installed and cannot be imported: "
        "that is the silent hole of TD50"
    )
    assert result["raised"]["type"] == "EgressGuardIncomplete", result["raised"]
    assert first in result["raised"]["uncovered"], result["raised"]
    assert result["coverage"] is not None, "arail.egress.coverage() does not exist"
    assert result["coverage"][first].startswith("unguarded: import raised RuntimeError"), result["coverage"]
    assert result["requests_guarded"] is True
    assert result["installed_flag"] is False


# --- FR-5: huggingface_hub --------------------------------------------------------------------

_HF = _COMMON + r'''
import tempfile

endpoint = "http://egress-probe.invalid"
log = os.path.join(os.environ["ARAIL_DATA_DIR"], "egress.jsonl")
import huggingface_hub
from huggingface_hub import HfApi, hf_hub_download, snapshot_download
from huggingface_hub.utils import get_session

shared_session = get_session()  # a session built BEFORE the guard exists, shared by hf's own calls
import arail.egress as egress
from arail.airgap import EgressBlocked

egress.install_guard()


def lines():
    if not os.path.exists(log):
        return 0
    with open(log) as handle:
        return sum(1 for _ in handle)


def chain_of(exc):
    names, seen, node = [], set(), exc
    while node is not None and id(node) not in seen and len(names) < 8:
        names.append(type(node).__name__)
        seen.add(id(node))
        node = node.__cause__ or node.__context__
    return names


def run(fn):
    before, started = lines(), time.monotonic()
    try:
        fn()
        final, chain = "NO EXCEPTION", []
    except BaseException as exc:
        final, chain = type(exc).__name__, chain_of(exc)
    return {"final": final, "chain": chain, "refused": lines() - before,
            "seconds": round(time.monotonic() - started, 3)}


cache = tempfile.mkdtemp(prefix="hf-cache-")
calls = {
    "HfApi(endpoint).model_info": lambda: HfApi(endpoint=endpoint).model_info("a/b"),
    "hf_hub_download": lambda: hf_hub_download("a/b", "f.txt", endpoint=endpoint, cache_dir=cache),
    "snapshot_download": lambda: snapshot_download("a/b", endpoint=endpoint, cache_dir=cache),
    "model_info through a session built before install": lambda: shared_session.get(endpoint + "/api/models/a/b"),
}
print(json.dumps({
    "egress_file": egress.__file__,
    "hf_version": huggingface_hub.__version__,
    "calls": {name: run(fn) for name, fn in calls.items()},
}))
'''


def test_huggingface_hub_downloads_are_refused_once_and_not_retried(tmp_path):
    """FR-5.  huggingface_hub is a core dependency and the lab's model downloader; it is never
    skipped.  Each call must end in EgressBlocked with exactly ONE audit line.

    Means: one audit line is one attempt the guard refused.  Before the fix, hf 2.x on a fresh install
    retried 5 times with backoff on the network before failing.  Bar: final EgressBlocked and exactly 1
    refusal per call (measured: well under a second), because a refusal must not spend hf's retry
    budget or flood the audit log.  Out of range: more than one refusal means hf started retrying a
    RuntimeError; a final LocalEntryNotFoundError with EgressBlocked in its chain means hf started
    wrapping it, which needs a decision rather than a quiet edit of this test; ConnectError means the
    library hf rides on is not guarded.  Where: huggingface_hub.utils._http; arail.egress.
    """
    result = run_fresh(_HF, tmp_path=tmp_path, timeout=180)
    calls = result["calls"]
    _evidence(
        f"FR-5 huggingface_hub {result['hf_version']}: call | final | chain | refused | seconds",
        [(name, c["final"], " <- ".join(c["chain"]), c["refused"], c["seconds"]) for name, c in calls.items()],
    )
    bad = {
        name: f"final {c['final']} (chain {' <- '.join(c['chain'])}), {c['refused']} refusal(s)"
        for name, c in calls.items()
        if c["final"] != "EgressBlocked" or c["refused"] != 1
    }
    assert not bad, (
        "huggingface_hub " + result["hf_version"] + ": " + "; ".join(f"{name}: {why}" for name, why in bad.items()) + ".\n"
        "  Means: one audit line is one attempt the guard refused; ConnectError means the library hf rides on is "
        "not guarded; more than one refusal means hf retried (it used to retry 5 times with backoff).\n"
        "  Bar: final EgressBlocked and exactly 1 refusal per call, so a refusal never spends hf's retry budget "
        "and never floods the audit log.\n"
        "  Out of range: a final LocalEntryNotFoundError with EgressBlocked in its chain means hf started wrapping "
        "the error, which needs a decision, not a quiet edit of this test.\n"
        "  Where: huggingface_hub.utils._http; arail.egress._install_httpx_family_guard"
    )
    assert {line["url_host"] for line in _audit(tmp_path)} == {HOST}


# --- the helper's own safety property (F9) ----------------------------------------------------

def test_run_fresh_rejects_a_child_that_imported_another_checkouts_guard(tmp_path):
    """A subprocess that imports ``arail.egress`` from somewhere else would be proving ANOTHER tree's
    guard (the editable install can point at another checkout).  ``run_fresh`` must refuse that, not
    pass it: this test points a child at a stand-in package outside this checkout and expects the
    refusal."""
    other_src = tmp_path / "another-checkout" / "src"
    (other_src / "arail").mkdir(parents=True)
    (other_src / "arail" / "__init__.py").write_text("")
    (other_src / "arail" / "egress.py").write_text("")
    code = "import json, arail.egress as e; print(json.dumps({'egress_file': e.__file__}))"
    with pytest.raises(AssertionError, match="not from this checkout"):
        run_fresh(code, tmp_path=tmp_path, extra_env={"PYTHONPATH": str(other_src)})
    # and the same child, pointed at this checkout, is accepted
    assert run_fresh(code, tmp_path=tmp_path)["egress_file"].endswith("egress.py")
