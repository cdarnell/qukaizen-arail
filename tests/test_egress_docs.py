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
- a library the guard could not patch shows up in ``/api/airgap/status``, so the modal lists it, and the
  modal stops saying "Cloud-provider APIs are blocked" about it: the description names the library, the
  pill turns amber and the known-gaps list opens (DOC-5, DOC-6); the portal's start-up says so once
  (DOC-5, behavioural), and does so first (DOC-5, wiring);
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
import types
from pathlib import Path

import pytest

import arail.egress as egress
from arail import airgap
from tests.egress_family_support import LITERAL_FAMILY, PUBLIC_TEST_IP, installed_independently, run_fresh

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


def _portal_app():
    return importlib.import_module("arail.portal.app")


def _status():
    status = asyncio.run(_portal_app().get_airgap_status())
    json.dumps(status)  # it is a JSON response: nothing in it may be an exception or a tuple
    return status


@pytest.fixture
def activity_lines(monkeypatch):
    """What the portal writes to the activity log, as (source, message, level), without writing it."""
    lines = []
    monkeypatch.setattr(
        _portal_app().activity_log, "emit",
        lambda source, message, level="info", data=None: lines.append((source, message, level)),
    )
    return lines


CLOUD_SENTENCE = "Cloud-provider APIs are blocked."


def test_a_whole_guard_lists_exactly_the_documented_gaps(status_env):
    """DOC-5, the baseline: nothing is added when nothing failed, and the description is the one
    the modal always showed."""
    egress.install_guard()
    status = _status()
    assert status["guard_installed"] is True
    assert status["known_gaps"] == list(airgap.KNOWN_GAPS)
    assert CLOUD_SENTENCE in status["definition"]
    assert "NOT blocked" not in status["definition"] and "EXCEPT" not in status["definition"]


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


@lib_param
def test_the_airgapped_description_names_a_library_whose_requests_are_not_blocked(lib, monkeypatch, status_env):
    """DOC-5, the description (REVIEW.md B1).  The sentence the reader trusts, "Cloud-provider APIs are
    blocked.", is false for a library the guard could not patch (a request through it reached the
    network layer in the review's probe), so in that state it names the library and says so; the
    rest of the text stays."""
    monkeypatch.delattr(importlib.import_module(lib), "AsyncHTTPTransport")
    with pytest.raises(egress.EgressGuardIncomplete):
        egress.install_guard()
    definition = _status()["definition"]
    assert definition.startswith("Agents cannot collect information from the public internet. ")
    assert definition.endswith("Toggle LAB_MODE=hybrid in .env to allow agent fetches.")
    assert "Local services on this machine and your private network" in definition
    assert f"EXCEPT requests made through {lib}" in definition, definition
    assert "NOT blocked" in definition, definition
    assert CLOUD_SENTENCE not in definition, "the unqualified claim is still there"


@pytest.mark.parametrize("broken", [INSTALLED] if INSTALLED else [])
def test_the_description_names_every_unguarded_library(broken, monkeypatch, status_env):
    """DOC-5.  With every installed member unpatchable (both, in the leg that has both) the description
    names all of them, in FAMILY order."""
    for name in broken:
        monkeypatch.delattr(importlib.import_module(name), "AsyncHTTPTransport")
    with pytest.raises(egress.EgressGuardIncomplete):
        egress.install_guard()
    definition = _status()["definition"]
    assert f"EXCEPT requests made through {', '.join(broken)}:" in definition, definition
    assert ("could not patch it" if len(broken) == 1 else "could not patch them") in definition


@lib_param
def test_the_hybrid_description_is_left_alone(lib, monkeypatch, status_env):
    """DOC-5.  Hybrid mode does not claim cloud calls are blocked, so there is nothing to qualify; the
    unpatched library is still listed under known gaps."""
    monkeypatch.setenv("LAB_MODE", "hybrid")
    monkeypatch.delattr(importlib.import_module(lib), "AsyncHTTPTransport")
    with pytest.raises(egress.EgressGuardIncomplete):
        egress.install_guard()
    status = _status()
    assert status["definition"].startswith("Hybrid: cloud providers are reachable."), status["definition"]
    assert "EXCEPT" not in status["definition"]
    assert any(gap.startswith(f"{lib}: installed, but") for gap in status["known_gaps"])


# --- DOC-5, the portal's start-up: one visible warning, and it comes first -------------------------

@lib_param
def test_the_portal_says_so_once_when_the_guard_is_incomplete(lib, monkeypatch, status_env, activity_lines):
    """DOC-5, behavioural (REVIEW.md B1, mutant R6).  The portal's guard-install step does not stop the
    portal (the stated decision: with an incomplete guard the portal and the agent loader keep serving
    with that library unguarded) so the one thing it must do is say so: exactly one warning in the
    activity log, naming the library, saying its requests are not checked, and giving the fix.  This was
    pinned only by a source-string match, so a start-up that swallowed the error left every test green."""
    monkeypatch.delattr(importlib.import_module(lib), "AsyncHTTPTransport")
    _portal_app()._install_egress_guard()  # must not raise
    assert len(activity_lines) == 1, activity_lines
    source, message, level = activity_lines[0]
    assert (source, level) == ("system", "warn")
    assert message.startswith("Egress guard install failed: ")
    assert lib in message and "NOT checked" in message, message
    assert "install a supported version" in message and f"uninstall {lib}" in message, message
    assert egress._INSTALLED is False


def test_the_portal_is_silent_when_the_guard_is_whole(status_env, activity_lines):
    """DOC-5, behavioural: no warning, and the guard is installed."""
    _portal_app()._install_egress_guard()
    assert activity_lines == []
    assert egress._INSTALLED is True


@lib_param
@pytest.mark.parametrize("mode", ["airgapped", "hybrid"])
def test_the_model_forge_worker_stops_with_one_readable_line_when_the_guard_is_incomplete(
        lib, mode, monkeypatch, status_env, capsys):
    """DOC-5, the worker (REVIEW.md N2).  Of the callers of ``install_guard()`` only Model Forge's worker
    stops when the guard is incomplete (fail closed: a phase must never run with a library open, in
    hybrid too, because the toggle flips the mode in-process).  It used to let ``EgressGuardIncomplete``
    escape, and the parent process inherits the worker's stderr, so a Model Forge user got a raw
    traceback.  Now: exit status 1 and one ``worker: <message>`` line; the phase never starts."""
    from arail.nucleus import worker

    monkeypatch.setenv("LAB_MODE", mode)
    monkeypatch.delattr(importlib.import_module(lib), "AsyncHTTPTransport")
    started = []
    monkeypatch.setattr(worker, "_load_context", lambda build_id: started.append(build_id) or {})

    assert worker.main(["PA", "build-1"]) == 1
    captured = capsys.readouterr()
    assert started == [], "the phase started with an incomplete guard"
    assert captured.out == ""
    assert captured.err.startswith("worker: egress guard incomplete"), captured.err
    assert captured.err.count("\n") == 1 and "Traceback" not in captured.err, captured.err
    assert lib in captured.err and "NOT checked" in captured.err and f"uninstall {lib}" in captured.err


def test_the_model_forge_worker_still_starts_the_phase_when_the_guard_is_whole(monkeypatch, status_env, capsys):
    """DOC-5, the worker: the guard is installed first, and a whole guard changes nothing."""
    from arail.nucleus import worker

    started = []

    def _load_context(build_id):
        started.append(egress._INSTALLED)  # the guard must already be in when the phase begins
        raise RuntimeError("stop here: the phase body is not under test")

    monkeypatch.setattr(worker, "_load_context", _load_context)
    assert worker.main(["PA", "build-1"]) == 1  # the existing phase-boundary handler reports the stop
    assert started == [True]
    assert capsys.readouterr().err.startswith("worker: phase PA failed: stop here")


def test_the_portal_installs_the_guard_before_it_does_anything_else():
    """DOC-5, wiring.  The guard goes in "FIRST - before any agent loads, before any cloud-provider
    endpoint fires, before the security scan".  Moving the install into a function must not lose the call
    or push it down: no call in ``_startup`` precedes it."""
    source = _read(ROOT / "src" / "arail" / "portal" / "app.py")
    startup = next(
        node for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_startup"
    )
    calls = sorted((node.lineno, node.col_offset, ast.unparse(node.func))  # the body, not the decorator
                   for statement in startup.body for node in ast.walk(statement) if isinstance(node, ast.Call))
    assert calls, "_startup() makes no calls at all"
    assert calls[0][2] == "_install_egress_guard", (
        f"the first call in _startup() is {calls[0][2]}(), not _install_egress_guard()"
    )


def test_the_plugin_installer_tells_the_user_to_restart_the_lab(monkeypatch, tmp_path):
    """REVIEW.md W3.  ``/api/plugins/install`` pip-installs a plugin's requirements into the running
    portal's interpreter.  A family member that arrives that way is not guarded until the process
    restarts (BY-6), so the success line says to restart the lab.  Nothing is cloned or installed: git
    is faked."""
    from arail.plugins import manager

    plugins = tmp_path / "plugins"
    monkeypatch.setattr(manager, "PLUGINS_DIR", plugins)
    monkeypatch.setattr(manager, "MANIFEST_FILE", plugins / "manifest.json")

    def fake_run(args, **kwargs):  # the only subprocess call without a requirements.txt is `git clone`
        assert args[:2] == ["git", "clone"], args
        Path(args[-1]).mkdir(parents=True)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(manager, "subprocess", types.SimpleNamespace(run=fake_run))
    lines = []
    monkeypatch.setattr(
        manager.activity_log, "emit",
        lambda source, message, level="info", data=None: lines.append((source, message, level)),
    )

    manager.PluginManager().install("https://github.com/example/plugin")

    success = [message for _source, message, level in lines if level == "success"]
    assert len(success) == 1, lines
    assert "installed successfully" in success[0]
    assert "Restart the lab" in success[0] and "egress guard" in success[0], success[0]


def test_privacy_says_a_library_installed_later_is_not_guarded_until_restart():
    """REVIEW.md W3, the words.  The doctor line and the guard's coverage are as of the moment the process
    started; PRIVACY.md must not promise more (BY-6 pins the behaviour)."""
    text = flat(privacy_known_gaps())
    assert "not guarded until the lab restarts" in text, (
        "PRIVACY.md does not say that a library installed while the lab runs is unguarded until a restart"
    )
    assert "this lab's guard covered" not in text, "PRIVACY.md still describes the doctor line as a standing fact"


@pytest.mark.parametrize("doc", [egress.coverage, egress._install_httpx_family_guard], ids=["coverage", "family-guard"])
def test_the_guards_docstrings_do_not_call_a_not_yet_installed_library_unusable(doc):
    """REVIEW.md W3.  The docstrings said a member that is not installed is "absent: nothing in this
    process can use it".  True when the guard ran; false once a plugin's pip install runs inside the
    process.  They now say when 'absent' was decided."""
    text = " ".join((doc.__doc__ or "").split())
    assert "nothing in this process can use it" not in text and "nothing here can use it" not in text, text
    assert "restart" in text, f"{doc.__name__}'s docstring does not say a later install is unguarded until a restart"


def test_readme_scopes_what_requests_and_urllib_are_wrapped_for():
    """REVIEW.md W1.  "...and so everything built on them" over-claimed: the guard swaps the DEFAULT
    requests adapter (new sessions) and the DEFAULT urllib opener, and only the httpx family is patched on
    its classes. BY-5 pins the gap; the README must not claim more than the behaviour."""
    text = flat(readme_section())
    assert "`requests` (new sessions)" in text, "README does not say the requests guard is for new sessions"
    assert "`urllib.request` (the default opener)" in text, "README does not say the urllib guard is the default opener"
    assert "everything built on them" not in text, "README still claims everything built on requests and urllib is wrapped"


def test_privacy_lists_objects_created_before_the_guard_as_a_gap():
    """REVIEW.md W1.  PRIVACY scoped the claim ("the adapter every new Session gets", "the default opener")
    but its list of what is NOT wrapped never named what falls outside that scope."""
    # The whole bullet, first line included: start at a zero-width match, end at the first line break
    # that is followed by the next bullet or by unindented text.
    bullet = flat(_between(privacy_known_gaps(), r"^(?=- A `requests` session or adapter created before\b)", r"\n(?=- |\S)",
                           "PRIVACY.md's bullet about objects that exist before the guard"))
    assert bullet.startswith("- A `requests` session or adapter created before the guard was installed"), bullet
    assert "`build_opener()`" in bullet, bullet
    assert "already exist" in bullet, bullet
    assert "httpx family" in bullet, "the bullet should say the httpx family has no such gap"


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


def test_the_modal_turns_amber_and_opens_the_gaps_when_the_guard_is_incomplete():
    """DOC-6 (REVIEW.md B1).  ``guard_installed`` was read nowhere, so the pill stayed green and the only
    trace was a bullet in a collapsed ``<details>``.  Now nav.js reads it: the pill is ``warn`` unless the
    mode is airgapped AND the guard is whole, and the known-gaps ``<details>`` is opened.  Source-level,
    like the rest of DOC-6 (nav.js is not run here; the build log shows it run under node)."""
    js = _read(NAV_JS)
    assert re.search(r"guardIncomplete = data\.guard_installed === false;", js), (
        "nav.js does not read guard_installed"
    )
    assert re.search(
        r"function pillClass\(mode\) \{\s*return 'mp-pill ' \+ \(mode === 'airgapped' && !guardIncomplete \? 'ok' : 'warn'\);\s*\}",
        js,
    ), "the pill is not painted warn when the guard is incomplete"
    # Every place that paints the pill goes through that one function (the status fetch, the optimistic
    # flip, the confirmed flip and the two reverts), so flipping the mode cannot turn it green again
    # while a library is unguarded; no paint from the mode alone is left.
    assert len(re.findall(r"pill\.className = pillClass\(", js)) >= 5
    assert "=== 'airgapped' ? 'ok' : 'warn'" not in js, "a paint of the pill from the mode alone is back"
    assert re.search(r"var gapsBox = document\.getElementById\('airgap-gaps'\);", js)
    assert re.search(r"if \(gapsBox && guardIncomplete\) gapsBox\.open = true;", js), (
        "the known-gaps list is not opened when the guard is incomplete"
    )
    assert re.search(r'<details class="airgap-gaps" id="airgap-gaps">', _read(MODAL)), (
        "the modal's known-gaps <details> has no id nav.js can open"
    )


def test_the_modal_paragraph_does_not_say_a_library_is_caught_without_qualification():
    """DOC-6.  The paragraph under the list says the guard catches httpx2.  It sits right under an entry
    that may say the opposite, so it says that too: unless the list above names a library the guard could
    not patch."""
    text = flat(modal_gap_paragraph())
    assert "unless the list above names" in text and "could not patch" in text, text


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


# Lab-spawned Python that PRIVACY.md says runs outside the guard: (what PRIVACY calls it, the file that
# spawns it, the module it runs).  Read from the code, so a rename or a removal fails here.
LAB_SPAWNED_PYTHON = {
    "goal parser": ("src/arail/skills/goal_parser/__init__.py", "arail.skills.goal_parser._subprocess_runner"),
    "AirLLM worker": ("src/arail/router/backends.py", "arail.router.airllm_worker"),
}


def other_processes_bullet() -> str:
    return flat(_between(privacy_known_gaps(), r"^- Other processes\b.*$", r"^\S|\Z", "PRIVACY.md's 'Other processes' bullet"))


@pytest.mark.parametrize("what", sorted(LAB_SPAWNED_PYTHON))
def test_privacy_names_the_labs_own_python_subprocesses_and_they_run_without_the_guard(what, tmp_path):
    """DOC-9, REVIEW.md W2.  The lab starts two Python processes that run models and neither installs
    the guard: the goal parser's model subprocess and the AirLLM worker (read: the worker falls back to
    a Hugging Face repo id when the local model directory is missing).  'Other processes' used to list
    only what runs outside the guard in OTHER tools.  The bullet names them; this ties each name to a
    spawn that exists, to a module that exists, to no install_guard() call, and to a fresh interpreter in
    which importing the module leaves the guard uninstalled."""
    spawner, module = LAB_SPAWNED_PYTHON[what]
    assert f'"{module}"' in _read(ROOT / spawner), (
        f"{spawner} no longer starts {module}: the 'Other processes' bullet in PRIVACY.md names it"
    )
    relative = Path("src", *module.split(".")).with_suffix(".py")
    assert (ROOT / relative).is_file(), f"{module} does not exist"
    assert str(relative) not in PROCESSES_THAT_INSTALL_THE_GUARD
    assert "install_guard" not in _read(ROOT / relative), f"{relative} installs the guard: update PRIVACY.md"
    code = (  # the AirLLM worker takes over sys.stdout when imported (it reserves it for its protocol)
        "import importlib, json, sys\n"
        "out = sys.stdout\n"
        "importlib.import_module(sys.argv[1])\n"
        "import arail.egress as egress\n"
        "print(json.dumps({'egress_file': egress.__file__, 'installed': egress._INSTALLED, 'coverage': egress.coverage()}), file=out)\n"
    )
    result = run_fresh(code, module, tmp_path=tmp_path)
    assert result["installed"] is False and result["coverage"] == {}, (
        f"importing {module} installed the egress guard: update the 'Other processes' bullet in PRIVACY.md"
    )
    assert what in other_processes_bullet(), f"PRIVACY.md's 'Other processes' bullet does not name the {what}"


def test_privacy_says_other_arailctl_commands_run_outside_the_guard_and_readme_points_at_other_processes():
    """REVIEW.md W2.  The CLI commands other than the portal and the doctor run their own Python without
    the guard (the install sites are exactly the four DOC-9 pins), and the README's list of what the guard
    does not cover includes other processes, such as setup's and upgrade's model downloads."""
    bullet = other_processes_bullet()
    assert "every other `./arailctl` command" in bullet, bullet
    assert "Everything else runs outside it" in bullet, bullet
    readme = flat(readme_section())
    assert "other processes such as setup's and upgrade's model downloads" in readme, (
        "README's list of what the guard does not cover omits other processes"
    )


def test_the_router_comment_says_the_guard_blocks_only_in_a_process_that_installed_it():
    """REVIEW.md W2.  The comment above _CLOUD_BACKENDS said the egress guard 'would block the eventual API
    call anyway'.  In the goal parser's model subprocess, which does not install it, that is false: this
    refusal is the only line there."""
    source = _read(ROUTER_CORE)
    comment = flat(_between(source, r"^# Backends whose inference calls leave this machine\.", r"^_CLOUD_BACKENDS", "the router comment"))
    assert "only in a process that installed it" in comment, comment
    assert "goal parser" in comment and "only line" in comment, comment


@lib_param
def test_the_failure_privacy_quotes_is_the_string_the_portal_emits(lib, monkeypatch, status_env, activity_lines):
    """DOC-5, the words.  PRIVACY.md tells the reader what the portal records when the guard cannot be
    installed completely; the quoted text must be what the portal's start-up really writes (run, not
    matched in the source)."""
    quoted = re.search(r'"(Egress guard install failed)"', flat(privacy_known_gaps()))
    assert quoted, "PRIVACY.md no longer quotes the portal's warning"
    monkeypatch.delattr(importlib.import_module(lib), "AsyncHTTPTransport")
    _portal_app()._install_egress_guard()
    assert len(activity_lines) == 1 and activity_lines[0][1].startswith(quoted.group(1) + ": "), (
        f"the portal no longer writes the warning that PRIVACY.md quotes: {activity_lines}"
    )
