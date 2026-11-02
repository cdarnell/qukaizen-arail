"""The documentation says what the egress guard wraps, and the status endpoint says what it could not wrap (TD50).

The README, docs/PRIVACY.md, the Network Policy modal and two docstrings all promised that the guard
covers ``httpx``; one of them (airgap.py) said the opposite.  None named ``httpx2``, which is what
huggingface-hub 2.x, anthropic 2.x and openai 3.x use, and none named the gaps found while testing it
(urllib3 or httpcore used directly, the hop to a configured proxy).  A claim that drifts from the code
is a privacy bug, so these tests tie the words to the code:

- every client the guard wraps (``egress.GUARDED_CLIENTS``, which is built from
  ``arail.httpx_family.FAMILY``) is named where the docs say what is wrapped, so adding a member to the
  family fails here until the docs say so (DOC-1);
- every gap the docs list is named by the modal's list too, which is ``airgap.KNOWN_GAPS`` (DOC-2);
- nobody says ``httpx`` is not wrapped (DOC-3), and the docstrings name the whole family (DOC-4);
- a library the guard could not patch shows up in ``/api/airgap/status``, so the modal lists it (DOC-5,
  DOC-6);
- the audit grep that PRIVACY.md hands to readers finds every client PRIVACY.md names (DOC-8), and the
  list of processes PRIVACY.md says the guard lives in is the list of processes that install it (DOC-9).

Nothing here starts a server or binds a port; the endpoint function is called directly.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import json
import re
import socket
from pathlib import Path

import pytest

import arail.egress as egress
from arail import airgap
from tests.egress_family_support import LITERAL_FAMILY, PUBLIC_TEST_IP, installed_independently

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
PRIVACY = ROOT / "docs" / "PRIVACY.md"
MODAL = ROOT / "src" / "arail" / "portal" / "templates" / "_airgap_modal.html"
NAV_JS = ROOT / "src" / "arail" / "portal" / "static" / "nav.js"
ROUTER_CORE = ROOT / "src" / "arail" / "router" / "core.py"

INSTALLED = installed_independently()
lib_param = pytest.mark.parametrize("lib", INSTALLED)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _between(text: str, start: str, end: str, what: str) -> str:
    """The text after the first line matching ``start`` up to the next line matching ``end``."""
    opening = re.search(start, text, re.M)
    assert opening, f"cannot find {what}: nothing matches {start!r}"
    rest = text[opening.end():]
    closing = re.search(end, rest, re.M)
    return rest[: closing.start()] if closing else rest


def readme_section() -> str:
    return _between(_read(README), r'^## What "local-first" means$', r"^(---|## )", "README's local-first section")


def privacy_known_gaps() -> str:
    return _between(_read(PRIVACY), r"^### Known gaps\b.*$", r"^###? ", "PRIVACY.md's Known gaps section")


def privacy_enforces() -> str:
    return _between(_read(PRIVACY), r"^## What airgapped mode enforces$", r"^###? ", "PRIVACY.md's enforcement paragraph")


def modal_gap_paragraph() -> str:
    return _between(_read(MODAL), r'id="airgap-gaps-list"[^\n]*$', r"</details>", "the modal's known-gaps paragraph")


SURFACES = {
    "README": readme_section,
    "PRIVACY known gaps": privacy_known_gaps,
    "modal": modal_gap_paragraph,
}


def flat(text: str) -> str:
    """The text with every run of whitespace (a wrapped line, an indent) collapsed to one space, so a
    phrase that the doc wraps across two lines can still be found."""
    return " ".join(text.split())


def names(text: str, client: str) -> bool:
    """True if ``client`` appears as `client` (Markdown) or <code>client</code> (HTML).  The delimiters
    matter: bare ``httpx`` would also match inside ``httpx2``."""
    return f"`{client}`" in text or f"<code>{client}</code>" in text


# --- DOC-1: every client the guard wraps is named where the docs say what is wrapped -------------

@pytest.mark.parametrize("surface", sorted(SURFACES))
def test_each_surface_names_every_client_the_guard_wraps(surface):
    """DOC-1.  GUARDED_CLIENTS is ("requests", "urllib.request", *httpx_family.FAMILY).  A new family
    member, or a doc that forgot httpx2, fails here."""
    text = SURFACES[surface]()
    missing = [client for client in egress.GUARDED_CLIENTS if not names(text, client)]
    assert not missing, (
        f"{surface} does not name {missing}; the guard wraps {list(egress.GUARDED_CLIENTS)}. "
        "Say so in the docs (README, docs/PRIVACY.md, the Network Policy modal, the airgap.py docstring)."
    )


def test_privacy_says_what_airgapped_mode_blocks_with_every_client():
    """DOC-1, the enforcement paragraph: `blocks calls made through ...` names the whole family."""
    text = privacy_enforces()
    missing = [client for client in egress.GUARDED_CLIENTS if not names(text, client)]
    assert not missing, f"PRIVACY.md's 'What airgapped mode enforces' does not name {missing}"


# --- DOC-2: the gaps -----------------------------------------------------------------------------

GAP_WORDS = ("aiohttp", "urllib3", "httpcore", "proxy")


@pytest.mark.parametrize("surface", ["README", "PRIVACY known gaps", "modal"])
def test_each_surface_lists_the_gaps_the_guard_does_not_cover(surface):
    """DOC-2.  aiohttp, urllib3 and httpcore used directly, and the hop to a configured proxy, were
    found while testing the guard (TD53, TD54).  The README is held to it too: it points at the list."""
    text = SURFACES[surface]().lower()
    missing = [word for word in GAP_WORDS if word not in text]
    assert not missing, f"{surface} does not mention {missing} among the things the guard does not cover"


def test_the_status_endpoints_gap_list_names_the_same_gaps():
    """DOC-2, the list the modal renders from /api/airgap/status."""
    joined = " ".join(airgap.KNOWN_GAPS).lower()
    missing = [word for word in GAP_WORDS if word not in joined]
    assert not missing, f"airgap.KNOWN_GAPS does not mention {missing}"
    assert len(set(airgap.KNOWN_GAPS)) == len(airgap.KNOWN_GAPS), "a gap is listed twice"


def test_the_first_three_known_gaps_are_the_strings_the_endpoint_always_listed():
    """The three entries /api/airgap/status listed before TD50 move to airgap.KNOWN_GAPS unchanged; a
    client that matched on them keeps working.  The two new ones come after them."""
    assert airgap.KNOWN_GAPS[:3] == (
        "raw socket connections (BUDDY_EGRESS_PROBE is the only audited use)",
        "subprocess-spawned tools (opencode CLI is config-gated to the local "
        "provider when airgapped; curl/wget: none in tree today)",
        "aiohttp (not in tree)",
    )
    assert len(airgap.KNOWN_GAPS) == 5


# --- DOC-3: nobody says httpx is not wrapped -----------------------------------------------------

NOT_WRAPPED = re.compile(r"httpx\w*\W+(—|-)+\s*not wrapped", re.I)


def test_the_stale_claim_pattern_matches_the_stale_claim():
    """The pattern DOC-3 scans for must match the sentence airgap.py used to carry, or it proves nothing."""
    assert NOT_WRAPPED.search("- ``httpx`` — not wrapped; localhost-only in tree.")
    assert NOT_WRAPPED.search("- `httpx2` - not wrapped")
    assert not NOT_WRAPPED.search("- ``aiohttp`` — not in tree.")


def test_no_doc_or_docstring_says_httpx_is_not_wrapped():
    """DOC-3.  airgap.py said so for months after the guard started patching httpx."""
    sources = {
        "airgap.__doc__": airgap.__doc__ or "",
        "egress.__doc__": egress.__doc__ or "",
        "docs/PRIVACY.md": _read(PRIVACY),
        "README.md": _read(README),
        "the Network Policy modal": _read(MODAL),
        "router/core.py": _read(ROUTER_CORE),
    }
    for where, text in sources.items():
        found = NOT_WRAPPED.search(text)
        assert not found, f"{where} says httpx is not wrapped: {found.group(0)!r}"


# --- DOC-4: the docstrings name the whole family ---------------------------------------------------

@pytest.mark.parametrize("module", [egress, airgap], ids=["egress", "airgap"])
def test_the_module_docstring_names_every_family_member(module):
    """DOC-4.  As a word: `httpx` must not be satisfied by `httpx2`."""
    doc = module.__doc__ or ""
    missing = [name for name in LITERAL_FAMILY if not re.search(rf"\b{name}\b", doc)]
    assert not missing, f"{module.__name__}'s docstring does not name {missing}"


def test_the_router_comment_names_the_clients_that_would_block_the_call():
    """The comment above _CLOUD_BACKENDS says which guard would stop the eventual API call anyway."""
    source = _read(ROUTER_CORE)
    comment = _between(source, r"^# Backends whose inference calls leave this machine\.", r"^_CLOUD_BACKENDS", "the router comment")
    for client in egress.GUARDED_CLIENTS:
        assert re.search(rf"(?<![\w.]){re.escape(client)}(?![\w])", comment), (
            f"router/core.py's comment does not name {client}"
        )


# --- DOC-5, DOC-6: a library the guard could not patch is visible in the modal ------------------------

@pytest.fixture
def status_env(monkeypatch, tmp_path):
    """The endpoint reads the audit log (tmp_path), the mode, and the probe switch."""
    monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LAB_MODE", "airgapped")
    monkeypatch.delenv("BUDDY_EGRESS_PROBE", raising=False)
    monkeypatch.setattr(socket, "gethostbyname", lambda host: PUBLIC_TEST_IP)
    egress._reset_for_tests()
    yield
    egress._reset_for_tests()


def _status():
    portal_app = importlib.import_module("arail.portal.app")
    status = asyncio.run(portal_app.get_airgap_status())
    json.dumps(status)  # it is a JSON response: nothing in it may be an exception or a tuple
    return status


def test_a_whole_guard_lists_exactly_the_documented_gaps(status_env):
    """DOC-5, the baseline: nothing is added when nothing failed."""
    egress.install_guard()
    status = _status()
    assert status["guard_installed"] is True
    assert status["known_gaps"] == list(airgap.KNOWN_GAPS)


@lib_param
def test_a_library_the_guard_could_not_patch_is_listed_under_known_gaps(lib, monkeypatch, status_env):
    """DOC-5.  Simulated as in EG-20: the class the guard needs is deleted from an installed member.
    The endpoint reports the guard as not installed (honest: it is incomplete) and appends one entry
    that names the library, the reason, and that its requests are not checked."""
    monkeypatch.delattr(importlib.import_module(lib), "AsyncHTTPTransport")
    with pytest.raises(egress.EgressGuardIncomplete):
        egress.install_guard()
    status = _status()
    assert status["guard_installed"] is False
    gaps = status["known_gaps"]
    assert gaps[: len(airgap.KNOWN_GAPS)] == list(airgap.KNOWN_GAPS)
    extra = gaps[len(airgap.KNOWN_GAPS):]
    assert len(extra) == 1, extra
    assert extra[0].startswith(f"{lib}: installed, but the guard could not patch it (")
    assert "AsyncHTTPTransport" in extra[0] and extra[0].endswith("its requests are NOT checked")


def test_the_modal_lists_whatever_the_status_endpoint_calls_a_known_gap():
    """DOC-6.  The path is /api/airgap/status -> nav.js -> the modal's list.  The entry for an
    unpatchable library carries a library's own error text, so nav.js must write it as text, never as
    markup."""
    js = _read(NAV_JS)
    assert "fetch('/api/airgap/status')" in js
    assert re.search(r"getElementById\('airgap-gaps-list'\)", js)
    assert re.search(r"data\.known_gaps\.forEach\(function \(gap\) \{[^}]*li\.textContent = gap;", js), (
        "nav.js no longer writes each known gap into the list with textContent"
    )
    assert 'id="airgap-gaps-list"' in _read(MODAL)


# --- DOC-8: the audit command in PRIVACY.md finds what PRIVACY.md names -----------------------------

def test_the_audit_grep_in_privacy_finds_every_client_the_doc_names():
    """DOC-8.  'Everything above is verifiable with grep': the pattern must find a use of each client the
    guard wraps (and the lower layers, aiohttp and raw sockets).  Python's re reads this pattern as the
    ERE grep -E does."""
    text = _read(PRIVACY)
    block = _between(text, r"^## Auditing claims yourself$", r"^## |\Z", "PRIVACY.md's audit section")
    command = re.search(r'^grep -rnE "([^"]+)" src/arail/$', block, re.M)
    assert command, "PRIVACY.md no longer has the `grep -rnE \"...\" src/arail/` audit command"
    pattern = re.compile(command.group(1))
    samples = [
        "import httpx", "httpx2.Client()", "from httpcore import ConnectionPool", "httpcore2.request(",
        "urllib3.PoolManager()", "requests.get(url)", "urllib.request.urlopen(url)",
        "aiohttp.ClientSession()", "socket.socket(socket.AF_INET)",
    ]
    missed = [sample for sample in samples if not pattern.search(sample)]
    assert not missed, f"the audit grep in PRIVACY.md does not find: {missed}"
    assert not pattern.search("import json"), "the audit grep matches everything"


# --- DOC-9: the processes the docs say the guard lives in are the ones that install it ---------------

PROCESSES_THAT_INSTALL_THE_GUARD = {
    "src/arail/portal/app.py",
    "src/arail/agents/loader.py",
    "src/arail/nucleus/worker.py",
    "src/arail/doctor.py",
}


def test_the_guard_is_installed_exactly_where_privacy_says():
    """DOC-9.  PRIVACY.md's 'Other processes' bullet lists the Python processes that install the guard
    (the portal, the agent loader, Model Forge's worker, ./arailctl doctor) and says everything else
    runs outside it.  A call site that disappears makes that bullet over-claim; a new one makes it stale."""
    found = set()
    for path in (ROOT / "src" / "arail").rglob("*.py"):
        if path == ROOT / "src" / "arail" / "egress.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if called == "install_guard":
                found.add(str(path.relative_to(ROOT)))
    assert found == PROCESSES_THAT_INSTALL_THE_GUARD, (
        f"install_guard() is called from {sorted(found)}, not from {sorted(PROCESSES_THAT_INSTALL_THE_GUARD)}: "
        "update the 'Other processes' bullet in docs/PRIVACY.md and this list together"
    )
    bullet = flat(_between(privacy_known_gaps(), r"^- Other processes\b.*$", r"^\S|\Z", "PRIVACY.md's 'Other processes' bullet"))
    for needle in ("portal", "agent loader", "Model Forge", "doctor"):
        assert needle in bullet, f"PRIVACY.md's 'Other processes' bullet does not name {needle!r}"


def test_the_failure_privacy_quotes_is_the_string_the_portal_emits():
    """DOC-5, the words.  PRIVACY.md tells the reader what the portal records when the guard cannot be
    installed completely; the quoted text must be what portal/app.py emits."""
    quoted = re.search(r'"(Egress guard install failed)"', flat(privacy_known_gaps()))
    assert quoted, "PRIVACY.md no longer quotes the portal's warning"
    portal_source = _read(ROOT / "src" / "arail" / "portal" / "app.py")
    assert f'f"{quoted.group(1)}: {{_eg_err}}", "warn"' in portal_source, (
        "portal/app.py no longer emits the warning that PRIVACY.md quotes"
    )
