"""The documentation says what the egress guard wraps, and the status endpoint says what it could not wrap (TD50).

The README, docs/PRIVACY.md, the Network Policy modal and two docstrings all promised that the guard
covers ``httpx``; one of them (airgap.py) said the opposite.  None named ``httpx2``, which is what
huggingface-hub 2.x, anthropic 2.x and openai 3.x use, and none named the gaps found while testing it
(urllib3 or httpcore used directly, the hop to a configured proxy).  A claim that drifts from the code
is a privacy bug, so these tests tie the words to the code:

- every client the guard wraps (``egress.GUARDED_CLIENTS``, which is built from
  ``arail.httpx_family.FAMILY``) is named where the docs say what is wrapped, so adding a member to the
  family fails here until the docs say so (DOC-1);
- every gap the docs list is named by the modal's list too, which is ``airgap.KNOWN_GAPS`` (DOC-2); that
  includes the ``urllib`` requests that skip the opener the guard installs (``urlopen`` with an SSL
  context, ``http.client``, a later ``install_opener``), and a tripwire checks the sentence that nothing
  in ``src/arail/`` does any of them (QA-W1);
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
import logging
import re
import socket
import sys
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
THE_LAB = ROOT / "docs" / "the-lab.md"
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

GAP_WORDS = ("aiohttp", "urllib3", "httpcore", "proxy", "http.client")


@pytest.mark.parametrize("surface", ["README", "PRIVACY known gaps", "modal"])
def test_each_surface_lists_the_gaps_the_guard_does_not_cover(surface):
    """DOC-2.  aiohttp, urllib3 and httpcore used directly, and the hop to a configured proxy, were
    found while testing the guard (TD53, TD54); http.client used directly (the layer under urllib, which
    the guard reaches only through its opener) was found by QA (QA-W1).  The README is held to it too: it
    points at the list."""
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
    client that matched on them keeps working.  The three new ones come after them (direct urllib3 or
    httpcore, the proxy hop, and the urllib requests that skip the guard's opener)."""
    assert airgap.KNOWN_GAPS[:3] == (
        "raw socket connections (BUDDY_EGRESS_PROBE is the only audited use)",
        "subprocess-spawned tools (opencode CLI is config-gated to the local "
        "provider when airgapped; curl/wget: none in tree today)",
        "aiohttp (not in tree)",
    )
    assert len(airgap.KNOWN_GAPS) == 6


def test_the_last_known_gap_is_the_urllib_requests_that_skip_the_guards_opener():
    """QA-W1.  One entry names the three shapes (the same three PRIVACY.md's bullet and QA's pin list)
    and scopes 'none today' to src/arail, where it was checked: scripts/s4_win_checks.py uses
    http.client against 127.0.0.1, so 'none in tree' would be false."""
    entry = airgap.KNOWN_GAPS[5]
    for needle in ("urllib requests", "urlopen with an SSL context", "http.client", "install_opener", "none in src/arail today"):
        assert needle in entry, f"airgap.KNOWN_GAPS[5] does not say {needle!r}: {entry!r}"


# --- the local-address check is Python's private set (REVIEW.md N1) ----------------------------------

@pytest.mark.parametrize("address,range_name", [
    ("192.0.2.7", "documentation"), ("198.51.100.7", "documentation"), ("203.0.113.7", "documentation"),
    ("2001:db8::1", "documentation (IPv6)"), ("198.18.0.1", "benchmarking"), ("240.0.0.1", "reserved"),
    ("fc00::1", "IPv6 unique-local"),
])
def test_the_local_check_lets_through_every_range_python_calls_private(address, range_name):
    """PRIVACY.md listed loopback, RFC1918 and link-local.  ``airgap.is_local_ip`` is
    ``is_loopback or is_private or is_link_local``, so it also passes the ranges Python's ``ipaddress`` calls
    private: documentation, benchmarking and reserved ranges, IPv6 unique-local.  None is routed on the
    public internet, so nothing leaks, but the doc must not describe a narrower check than the code runs."""
    assert airgap.is_local_ip(address), f"{address} ({range_name}) is no longer local"


@pytest.mark.parametrize("address", ["100.64.0.1", "8.8.8.8", "151.101.64.81", "2606:4700:4700::1111"])
def test_the_local_check_still_refuses_public_and_carrier_grade_nat_addresses(address):
    """The other side of N1: what is not in Python's private set is refused, so the airgap still holds."""
    assert not airgap.is_local_ip(address), f"{address} would pass the airgap"


def test_privacy_describes_the_local_check_as_pythons_private_set():
    """REVIEW.md N1, the words."""
    section = flat(privacy_enforces())
    assert "Python's `ipaddress`" in section, section
    for needle in ("documentation", "benchmarking", "reserved", "IPv6 unique-local"):
        assert needle in section, f"PRIVACY.md's 'What airgapped mode enforces' does not mention {needle!r}"


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
    names all of them, in FAMILY order, and the known-gaps list has one entry for each of them (the
    architect's re-review, R-N2: a list that kept only the first library passed every test)."""
    for name in broken:
        monkeypatch.delattr(importlib.import_module(name), "AsyncHTTPTransport")
    with pytest.raises(egress.EgressGuardIncomplete):
        egress.install_guard()
    status = _status()
    definition = status["definition"]
    assert f"EXCEPT requests made through {', '.join(broken)}:" in definition, definition
    assert ("could not patch it" if len(broken) == 1 else "could not patch them") in definition
    extra = status["known_gaps"][len(airgap.KNOWN_GAPS):]
    assert [gap.split(":")[0] for gap in extra] == list(broken), extra


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


# --- DOC-5, Buddy: the one sentence the operator hears when they turn the airgap back on --------------

SEALED_BACK_UP = "Sealed back up. Agents can't reach the public internet."


@pytest.fixture
def buddy_after_the_flip(monkeypatch, tmp_path, status_env):
    """Buddy's airgap watcher, run once after the lab flipped from hybrid to airgapped (what the toggle does).

    The watcher keeps its last-seen mode in a state file and reads the audit log from the data directory, so
    both are pointed at tmp_path.  The fixture returns the function that runs it."""
    buddy = importlib.import_module("arail.agents._builtin_buddy")
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"airgap_last_lab_mode": "hybrid"}))
    monkeypatch.setattr(buddy, "_state_file", lambda: state)
    monkeypatch.setattr(egress, "_lab_data", lambda: tmp_path)
    monkeypatch.setenv("LAB_MODE", "airgapped")
    return buddy._watch_airgap_events


def test_buddy_still_says_sealed_back_up_when_the_guard_is_whole(buddy_after_the_flip):
    """R-W1, the unchanged half.  With nothing unguarded the line is the one it always was, at the severity it
    always had, so the fix does not change what a healthy lab hears."""
    egress.install_guard()
    observation = buddy_after_the_flip()
    assert observation.watcher == "airgap:mode-toggle"
    assert observation.fact == SEALED_BACK_UP
    assert observation.severity == "info"


BUDDY_OPEN_LINES = {
    ("httpx2",): "Sealed back up, except httpx2: the egress guard could not patch it, so its requests still go out. "
                 "The Network Policy modal has the details.",
    ("httpx",): "Sealed back up, except httpx: the egress guard could not patch it, so its requests still go out. "
                "The Network Policy modal has the details.",
    ("httpx2", "httpx"): "Sealed back up, except httpx2 and httpx: the egress guard could not patch them, so their "
                         "requests still go out. The Network Policy modal has the details.",
}


@pytest.mark.parametrize(
    "broken",
    [(lib,) for lib in INSTALLED] + ([tuple(INSTALLED)] if len(INSTALLED) > 1 else []),
    ids=lambda broken: "+".join(broken),
)
def test_buddy_does_not_say_the_lab_is_sealed_while_the_guard_knows_a_library_is_open(
        broken, buddy_after_the_flip, monkeypatch):
    """R-W1 (the architect's re-review).  Buddy's observation on a flip back to airgapped said "Sealed back up.
    Agents can't reach the public internet." in every state, while the modal, the nav badge and the status
    endpoint said a library's requests are NOT blocked (and an async request through it reached the network
    layer).  It is the one sentence the operator hears at the moment they turn the airgap back on, so it
    names the library and says its requests still go out, at the severity that outranks an ordinary info."""
    for name in broken:
        monkeypatch.delattr(importlib.import_module(name), "AsyncHTTPTransport")
    with pytest.raises(egress.EgressGuardIncomplete):
        egress.install_guard()
    observation = buddy_after_the_flip()
    assert observation is not None and observation.watcher == "airgap:mode-toggle"
    assert observation.fact == BUDDY_OPEN_LINES[broken], observation.fact
    assert SEALED_BACK_UP not in observation.fact, "the unqualified claim is still there"
    assert observation.severity == "warn"


@lib_param
def test_buddys_door_open_line_is_left_alone_when_the_guard_is_incomplete(lib, monkeypatch, tmp_path, status_env):
    """R-W1, the other direction.  Hybrid mode does not claim anything is sealed, so there is nothing to
    qualify (as for the status description in DOC-5)."""
    buddy = importlib.import_module("arail.agents._builtin_buddy")
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"airgap_last_lab_mode": "airgapped"}))
    monkeypatch.setattr(buddy, "_state_file", lambda: state)
    monkeypatch.setattr(egress, "_lab_data", lambda: tmp_path)
    monkeypatch.delattr(importlib.import_module(lib), "AsyncHTTPTransport")
    with pytest.raises(egress.EgressGuardIncomplete):
        egress.install_guard()
    monkeypatch.setenv("LAB_MODE", "hybrid")
    observation = buddy._watch_airgap_events()
    assert observation.fact == "Door's open now \u2014 agent fetches go through. Per-domain consent still gates browser/curator."
    assert observation.severity == "info"


def test_a_library_whose_attribute_lookup_raises_is_reported_like_any_other_unguarded_library(
        monkeypatch, status_env, activity_lines):
    """DOC-5 (QA-W2).  A family member that imports but raises when its transport classes are looked up (a
    lazily loaded attribute whose import fails) used to abort the guard's family step with a raw ImportError:
    ``uncovered()`` stayed empty, so the status endpoint kept saying "Cloud-provider APIs are blocked" and its
    list named no library, while the portal's only trace was a message with no library in it.  Now it is
    reported through the same path as the missing-class case: one portal warning that names the library, the
    description names it and says its requests are NOT blocked, and the known-gaps list has its entry."""
    first = LITERAL_FAMILY[0]
    lazy = types.ModuleType(first)

    def __getattr__(name):
        if name in ("HTTPTransport", "AsyncHTTPTransport"):
            raise ImportError("simulated: a lazy attribute whose import fails")
        raise AttributeError(name)

    lazy.__getattr__ = __getattr__
    monkeypatch.setitem(sys.modules, first, lazy)

    _portal_app()._install_egress_guard()  # must not raise
    assert len(activity_lines) == 1, activity_lines
    message = activity_lines[0][1]
    assert message.startswith("Egress guard install failed: ") and first in message and "NOT checked" in message, message

    status = _status()
    assert status["guard_installed"] is False
    assert f"EXCEPT requests made through {first}" in status["definition"], status["definition"]
    assert "NOT blocked" in status["definition"] and CLOUD_SENTENCE not in status["definition"]
    extra = status["known_gaps"][len(airgap.KNOWN_GAPS):]
    assert len(extra) == 1 and extra[0].startswith(f"{first}: installed, but the guard could not patch it ("), extra
    assert "patch raised ImportError" in extra[0] and extra[0].endswith("its requests are NOT checked"), extra


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
    assert "reinstall a version the lab was tested with" in message and ".venv/bin/pip install" in message, message
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
    assert lib in captured.err and "NOT checked" in captured.err
    assert "reinstall a version the lab was tested with" in captured.err and ".venv/bin/pip install" in captured.err


# A real worker process, as `python -m arail.nucleus.worker` starts it: nothing has configured logging, so
# a record at WARNING or above is printed by logging's last-resort handler, to whatever sys.stderr is when
# it is emitted.  In pytest that handler never fires (pytest installs handlers on the root logger), which
# is why the in-process worker tests above see one line while the process printed two.
_WORKER_PROCESS = r'''
import contextlib, importlib, io, json, sys

lib = sys.argv[1]
del importlib.import_module(lib).AsyncHTTPTransport   # the EG-20 simulation: install_guard() cannot patch this library
from arail.nucleus import worker
import arail.egress as egress

buffer = io.StringIO()
with contextlib.redirect_stderr(buffer):
    rc = worker.main(["PA", "build-1"])
print(json.dumps({"rc": rc, "stderr": buffer.getvalue(), "egress_file": egress.__file__}))
'''


@lib_param
def test_a_real_worker_process_prints_the_incomplete_guard_message_once(lib, tmp_path):
    """DOC-5, the worker (QA-N4).  The module says the worker writes "one ``worker: ...`` line" and exits 1.  In
    a real process it printed two lines carrying the same message: the guard's own ``_log.error`` through
    logging's last-resort handler (``arail.egress: egress guard incomplete ...``), then ``worker: ...``.  The
    parent inherits this stderr, so a Model Forge user read the message twice."""
    result = run_fresh(_WORKER_PROCESS, lib, tmp_path=tmp_path)
    assert result["rc"] == 1
    lines = result["stderr"].splitlines()
    assert len(lines) == 1, f"the worker printed {len(lines)} lines, not one:\n{result['stderr']}"
    assert lines[0].startswith("worker: egress guard incomplete") and lib in lines[0], lines[0]
    assert "arail.egress:" not in result["stderr"], "the guard's own log line reached stderr as well"


@lib_param
def test_the_worker_keeps_the_guards_log_record_for_any_handler_that_is_configured(lib, monkeypatch, status_env):
    """QA-N4, the other half of the fix.  What keeps the duplicate line off stderr is a NullHandler on the
    guard's logger while the guard installs; it must not make the record disappear for a handler somebody
    configures (so no ``propagate = False``), and it must be gone again afterwards, so that an in-process
    caller of ``main()`` does not inherit it.

    The logger is put in a known state first, and the "configured handler" is this test's own, on the root
    logger.  Neither is incidental: pytest attaches its capture handler directly to every logger that is not
    propagating when a test starts, so a ``propagate = False`` leaked by an earlier test (the mutant that adds
    it never puts it back) lets the record through ``caplog`` and hides the bug; with caplog this test was red
    when run alone and green after any other in-process worker test."""
    from arail.nucleus import worker

    monkeypatch.delattr(importlib.import_module(lib), "AsyncHTTPTransport")
    guard_logger = logging.getLogger(egress.__name__)
    monkeypatch.setattr(guard_logger, "handlers", [])
    monkeypatch.setattr(guard_logger, "propagate", True)
    seen = []

    class _ConfiguredOnRoot(logging.Handler):
        def emit(self, record):
            seen.append(record)

    root_logger = logging.getLogger()
    configured = _ConfiguredOnRoot(level=logging.ERROR)
    root_logger.addHandler(configured)
    try:
        assert worker.main(["PA", "build-1"]) == 1
    finally:
        root_logger.removeHandler(configured)
    records = [r for r in seen if r.name == egress.__name__ and "egress guard incomplete" in r.getMessage()]
    assert len(records) == 1, [r.getMessage() for r in seen]
    assert guard_logger.handlers == [] and guard_logger.propagate is True, "the worker did not leave the guard's logger as it found it"


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


@lib_param
def test_the_agent_loader_logs_and_keeps_loading_agents_when_the_guard_is_incomplete(
        lib, monkeypatch, tmp_path, status_env, caplog):
    """DOC-5, the decision's other half (REVIEW.md B1).  The agent loader, like the portal, reports an
    incomplete guard and carries on: that is the stated behaviour ("the portal and the agent loader keep
    serving with that library unguarded; only Model Forge's worker exits"), pinned so that changing it
    (TD56, fail closed) is a visible act that must also update docs/PRIVACY.md.  Nothing is seeded or
    loaded: the seeding and the discovery are faked."""
    from arail.agents import loader

    monkeypatch.delattr(importlib.import_module(lib), "AsyncHTTPTransport")
    monkeypatch.setattr(loader, "_SHIPPED", set())
    monkeypatch.setattr(loader, "discover", lambda pkb_root=None: [])
    with caplog.at_level(logging.WARNING, logger=loader.log.name):
        assert loader.load_all(pkb_root=tmp_path) == {}  # returned: it did not stop
    messages = [record.getMessage() for record in caplog.records if record.name == loader.log.name]
    assert len(messages) == 1 and messages[0].startswith("Egress guard install failed in load_all: "), messages
    assert lib in messages[0] and "NOT checked" in messages[0], messages


def test_privacy_states_what_an_incomplete_guard_leaves_open_and_what_stops():
    """REVIEW.md B1, the words.  PRIVACY.md says plainly what happens when the guard cannot patch a library:
    where the lab says so (the activity log, the doctor, the modal), that the portal and the agents keep
    running with that library unguarded, and that only Model Forge's worker stops.  Each sentence is
    pinned by a behavioural test: the portal and the modal (DOC-5), the loader (above), the worker (above),
    the doctor (DR-3 in test_httpx_family.py)."""
    text = flat(privacy_known_gaps())
    for needle in (
        'the portal\'s activity log records "Egress guard install failed"',
        "`./arailctl doctor` fails its egress check",
        "description says that requests made through that library are NOT blocked",
        "the portal and the agent loader keep serving with that library unguarded",
        "only Model Forge's worker exits",
    ):
        assert needle in text, f"PRIVACY.md's Known gaps no longer says: {needle}"


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


def test_privacy_gives_the_same_remedy_as_the_incomplete_guard_message():
    """QA-N5.  The message tells the operator to reinstall a tested version and restart, and warns that
    uninstalling also disables the Hugging Face downloader and the cloud SDKs.  PRIVACY.md is the document
    form of that message and used to say 'or uninstall it' as if it were a fix."""
    text = flat(privacy_known_gaps())
    for needle in (
        "Until you reinstall a version of that library the lab was tested with (`httpx2<3`, `httpx<1`) and restart the lab",
        "Uninstalling it would also disable what is built on it, such as the Hugging Face downloader and the cloud SDKs",
    ):
        assert needle in text, f"PRIVACY.md no longer says: {needle}"
    assert "or uninstall it" not in text and "install a supported version" not in text


def test_privacy_says_a_library_imported_again_after_the_guard_is_not_guarded_either():
    """QA-N2.  The guard patched the class objects that existed when it ran.  A library purged from
    ``sys.modules`` and imported again has new classes, which nothing patched, and ``coverage()`` (which the
    doctor and the status endpoint read) still says "guarded".  PRIVACY.md said only that a library
    INSTALLED later is not guarded; it now says the same for one imported again.  QA's pin
    (TestQaWhereTheGuardsViewEnds, QA-N2, a fresh interpreter) holds the behaviour; this holds the words."""
    text = flat(privacy_known_gaps())
    assert "or imported again after the guard ran (its classes are new objects), is not guarded until the lab restarts" in text, (
        "PRIVACY.md does not say that a library imported again after the guard ran is unguarded until a restart"
    )


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
    assert "`urllib.request` (`urlopen` without an SSL context)" in text, (
        "README does not say the urllib guard is for urlopen without an SSL context (QA-W1)"
    )
    assert "everything built on them" not in text, "README still claims everything built on requests and urllib is wrapped"


def test_privacy_scopes_what_airgapped_mode_blocks_for_requests_and_urllib():
    """QA-W1.  The sentence that opens 'What airgapped mode enforces' named `urllib.request` without a scope,
    so a reader took `urlopen(url, context=...)` to be blocked too.  It carries the same scope as the README."""
    text = flat(privacy_enforces())
    assert "`requests` (new sessions)" in text, text
    assert "`urllib.request` (`urlopen` without an SSL context)" in text, text


# Two older paragraphs that describe the airgapped default in a sentence or two.  They said the guard covers
# "`requests` and `urllib`" (no httpx family) and passes "loopback, RFC1918, or link-local" (the check is
# Python's private set), which contradicted the README's "What local-first means" and PRIVACY.md.
OLDER_AIRGAP_PARAGRAPHS = {
    "README 'Airgapped guard'": (README, "**Airgapped guard.**"),
    "docs/the-lab.md 'Default mode is airgapped'": (THE_LAB, "**Default mode is airgapped.**"),
}


@pytest.mark.parametrize("where", sorted(OLDER_AIRGAP_PARAGRAPHS))
def test_the_older_airgap_paragraphs_agree_with_the_readme_and_privacy(where):
    """QA-N6.  Each paragraph names every client the guard wraps (with the same scope as the README's later
    paragraph and PRIVACY.md), says the allowed addresses are the ones Python's `ipaddress` calls private (not
    only loopback, RFC1918 and link-local), and points at PRIVACY.md for what the guard does not cover."""
    path, opening = OLDER_AIRGAP_PARAGRAPHS[where]
    text = flat(_between(_read(path), rf"^(?={re.escape(opening)})", r"^$", where))
    missing = [client for client in egress.GUARDED_CLIENTS if not names(text, client)]
    assert not missing, f"{where} does not name {missing}; the guard wraps {list(egress.GUARDED_CLIENTS)}: {text}"
    assert "`requests` (new sessions)" in text and "`urllib.request` (`urlopen` without an SSL context)" in text, text
    assert "Python's `ipaddress`" in text, text
    assert "PRIVACY.md" in text, text
    assert "`requests` and `urllib`" not in text, "still the pre-httpx2 client list"
    assert "loopback, RFC1918, or link-local" not in text, "still the three-range list (REVIEW.md N1)"


def test_privacy_says_the_httpx_family_covers_objects_but_not_a_method_reference_taken_before_the_guard():
    """QA-N1.  PRIVACY.md said the httpx family "has no such gap" for objects that exist before the guard.
    True for clients and transport objects (the patch is on the classes, so a transport built earlier is
    refused), not for a reference to the method itself taken earlier: a bound `handle_request`, the
    function `HTTPTransport.handle_request`, or a subclass whose body copies it still point at the library's
    own function.  QA's pin (TestQaWhereTheGuardsViewEnds, QA-N1) holds the behaviour; this holds the words."""
    bullet = flat(_between(privacy_known_gaps(), r"^(?=- A `requests` session or adapter created before\b)", r"\n(?=- |\S)",
                           "PRIVACY.md's bullet about objects that exist before the guard"))
    for needle in (
        "The httpx family is patched on its classes, so every client and transport object is covered whenever it was built",
        "a reference to the method itself taken before the guard",
        "a bound `handle_request`", "a subclass that copies it into its class body",
    ):
        assert needle in bullet, f"PRIVACY.md's bullet does not say {needle!r}: {bullet}"
    assert "has no such gap" not in bullet, "PRIVACY.md still says the httpx family has no gap at all"


def test_privacy_lists_the_urllib_requests_that_skip_the_installed_opener_as_a_gap():
    """QA-W1.  `install_guard()` installs one opener, the one `urlopen(url)` uses.  Three everyday shapes do
    not use it, and the Known gaps list did not name them: `urlopen` with a context (and, before Python 3.13,
    `cafile=`, `capath=` or `cadefault=`) builds an opener of its own; `http.client` is the layer under
    urllib (and under `xmlrpc.client`); a later `install_opener()` replaces the guard's opener.  QA's pin
    (TestQaWhereTheGuardsViewEnds, QA-W1) holds the behaviour; this holds the words."""
    bullet = flat(_between(privacy_known_gaps(), r"^(?=- A `urllib` request that does not go through the opener the guard$)", r"\n(?=- |\S)",
                           "PRIVACY.md's bullet about urllib requests that skip the guard's opener"))
    assert bullet.startswith("- A `urllib` request that does not go through the opener the guard installed:"), bullet
    for needle in (
        "`urlopen(url, context=...)`", "before Python 3.13", "`cafile=`", "`capath=`", "`cadefault=`",
        "`http.client`", "`xmlrpc.client`", "as `urllib3` is under `requests`",
        "`urllib.request.install_opener(...)`", "replaces the guarded opener",
        "Nothing in `src/arail/` does any of these today",
    ):
        assert needle in bullet, f"PRIVACY.md's bullet does not say {needle!r}: {bullet}"


URLLIB_BYPASS_KEYWORDS = {"context", "cafile", "capath", "cadefault"}
HTTP_CLIENT_MODULES = {"http.client", "xmlrpc.client"}


def _imported_module_names(node):
    """Every dotted name an import statement can bring in: `import http.client as hc` -> http.client;
    `from http import client` -> http, http.client; `from http.client import HTTPConnection` -> http.client."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if node.level:  # a relative import names a module of this package, not the standard library's
        return []
    module = node.module or ""
    return [module, *(f"{module}.{alias.name}" for alias in node.names)]


def test_no_lab_code_goes_around_the_opener_the_guard_installs():
    """QA-W1, the sentence 'Nothing in `src/arail/` does any of these today' (PRIVACY.md, airgap.KNOWN_GAPS).
    Like DOC-9 for the install sites, this reads the tree: no module under src/arail (apart from egress.py,
    which installs the guard's opener) imports http.client or xmlrpc.client, calls install_opener(), or
    passes context=, cafile=, capath= or cadefault= to urlopen().  A hit is code that talks to the network
    around the airgap guard: use requests or the httpx family, or update the three places that say 'none'."""
    found = []
    for path in sorted((ROOT / "src" / "arail").rglob("*.py")):
        if path == ROOT / "src" / "arail" / "egress.py":
            continue
        relative = path.relative_to(ROOT)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for name in _imported_module_names(node):
                    if name in HTTP_CLIENT_MODULES:
                        found.append(f"{relative}:{node.lineno} imports {name}")
            elif isinstance(node, ast.Call):
                func = node.func
                called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if called == "install_opener":
                    found.append(f"{relative}:{node.lineno} calls install_opener()")
                elif called == "urlopen":
                    for keyword in node.keywords:
                        if keyword.arg in URLLIB_BYPASS_KEYWORDS:
                            found.append(f"{relative}:{node.lineno} passes {keyword.arg}= to urlopen()")
    assert not found, (
        "lab code uses an urllib or stdlib HTTP path the airgap guard does not see: " + "; ".join(found)
    )


def test_the_tripwire_for_lab_code_that_skips_the_opener_can_fire():
    """A scan that cannot find anything proves nothing: the helper recognises every spelling of the import."""
    parsed = ast.parse("import http.client as hc\nfrom http import client\nfrom xmlrpc.client import ServerProxy\nimport json")
    names = [name for node in parsed.body for name in _imported_module_names(node)]
    assert {"http.client", "xmlrpc.client"} <= set(names) and "json" in names
    assert sum(1 for name in names if name in HTTP_CLIENT_MODULES) == 3
    # ...and does not cry wolf: a relative import names a module of the package itself, never the standard library's.
    relative = ast.parse("from .http import client\nfrom ..xmlrpc import client as other")
    assert [name for node in relative.body for name in _imported_module_names(node)] == []


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


def test_the_modal_paragraph_scopes_urllib_and_names_http_client():
    """QA-W1.  The paragraph under the list names `urllib.request` as caught and did not name `http.client` among
    the clients that are not wrapped."""
    text = flat(modal_gap_paragraph())
    assert "<code>urllib.request</code> (<code>urlopen</code> without an SSL context)" in text, text
    assert "<code>http.client</code>" in text and "used directly" in text, text


def test_the_modal_paragraph_does_not_say_a_library_is_caught_without_qualification():
    """DOC-6.  The paragraph under the list says the guard catches httpx2.  It sits right under an entry
    that may say the opposite, so it says that too: unless the list above names a library the guard could
    not patch."""
    text = flat(modal_gap_paragraph())
    assert "unless the list above names" in text and "could not patch" in text, text


# --- QA-N7: the always-visible nav badge carries the guard state ------------------------------------------

def _mode():
    mode = asyncio.run(_portal_app().get_mode())
    json.dumps(mode)  # a JSON response
    return mode


def test_the_mode_endpoint_carries_the_guard_state_the_badge_reads(status_env):
    """QA-N7.  The badge in the nav bar (#mode-badge) is the one indicator on every page, and it read
    /api/system/mode, which answered {"mode": ...} and nothing else, so it said plain "Airgapped" in
    every state of the guard while the modal behind it (/api/airgap/status) said the guard was incomplete.
    The endpoint now also carries ``guard_installed``, the field the modal reads."""
    assert _mode() == {"mode": "airgapped", "guard_installed": False}  # nothing installed yet in this test
    egress.install_guard()
    assert _mode() == {"mode": "airgapped", "guard_installed": True}
    assert _mode()["guard_installed"] == _status()["guard_installed"]


@lib_param
@pytest.mark.parametrize("mode", ["airgapped", "hybrid"])
def test_the_mode_endpoint_says_the_guard_is_incomplete_and_agrees_with_the_status_endpoint(lib, mode, monkeypatch, status_env):
    """QA-N7.  An unpatchable library makes ``guard_installed`` False on both endpoints, in either mode (the
    toggle flips the mode in-process, so a hybrid lab with an incomplete guard that is switched to airgapped
    must already show it)."""
    monkeypatch.setenv("LAB_MODE", mode)
    monkeypatch.delattr(importlib.import_module(lib), "AsyncHTTPTransport")
    with pytest.raises(egress.EgressGuardIncomplete):
        egress.install_guard()
    reported = _mode()
    assert reported == {"mode": mode, "guard_installed": False}
    assert reported["guard_installed"] == _status()["guard_installed"]


def test_the_mode_route_is_served_by_the_function_these_tests_call():
    """QA-N7.  The tests call ``get_mode()`` directly (no server, no port, no auth); this ties that function to
    the route the page fetches."""
    portal = _portal_app()
    routes = [r for r in portal.app.routes if getattr(r, "path", None) == "/api/system/mode" and "GET" in getattr(r, "methods", ())]
    assert len(routes) == 1 and routes[0].endpoint is portal.get_mode


def _js_function_body(js, name):
    """The body of ``function <name>(mode) { ... }`` inside nav.js's mode-badge closure (two-space indent)."""
    match = re.search(rf"^  function {name}\(mode\) \{{\n(.*?)^  \}}\n", js, re.S | re.M)
    assert match, f"nav.js has no function {name}(mode)"
    return match.group(1)


def test_the_nav_badge_does_not_read_plain_airgapped_while_the_guard_is_incomplete():
    """QA-N7.  Source-level, like the rest of DOC-6 (nav.js is not run here; the build log shows the badge run
    under node with a DOM stub).  ``updateBadge`` consults the flag the modal's pill already uses; when the lab
    is airgapped and the guard is not whole it paints the badge amber with the hybrid badge's existing style (no
    new CSS), says "Airgapped, guard incomplete", and its title points at the Network Policy modal, where the
    library is named.  The text goes in with textContent."""
    js = _read(NAV_JS)
    body = _js_function_body(js, "updateBadge")
    assert "var incomplete = mode === 'airgapped' && guardIncomplete;" in body, body
    assert "badge.className = 'mode-badge ' + (incomplete ? 'hybrid' : mode);" in body, body
    assert "'\\u2B24 Airgapped, guard incomplete'" in body, body
    assert "click to see which, in the Network Policy modal" in body, body
    assert "badge.textContent =" in body and "innerHTML" not in body, body
    # the plain text is still what a whole guard shows
    assert "'\\u2B24 Airgapped'" in body and "'\\u2B24 Hybrid'" in body, body


def test_the_nav_badge_is_given_the_guard_state_before_it_is_painted():
    """QA-N7.  The page-load fetch of /api/system/mode sets the shared flag from ``guard_installed`` and only then
    paints; the flag is declared once, so the modal's pill and the badge cannot disagree about it; and a flip in
    the modal repaints the badge through the same function, which still consults the flag."""
    js = _read(NAV_JS)
    assert len(re.findall(r"^  var guardIncomplete = false;", js, re.M)) == 1
    assert re.search(
        r"fetch\('/api/system/mode'\)\s*\.then\(function \(r\) \{ return r\.json\(\); \}\)\s*"
        r"\.then\(function \(d\) \{\s*guardIncomplete = d\.guard_installed === false;\s*updateBadge\(d\.mode\);\s*\}\)",
        js,
    ), "nav.js does not set the guard flag from /api/system/mode before it paints the badge"
    assert "updateBadge(confirmedMode);" in js, "the confirmed flip no longer repaints the badge"


def test_the_incomplete_badge_reuses_the_existing_amber_style():
    """QA-N7.  No new CSS: the badge takes the hybrid badge's class, which is the lab's amber for a mode that is
    not sealed.  If that rule stops being amber, the badge stops being a warning, so this ties them."""
    css = _read(ROOT / "src" / "arail" / "portal" / "static" / "style.css")
    rule = re.search(r"\.mode-badge\.hybrid \{([^}]*)\}", css)
    assert rule, "style.css has no .mode-badge.hybrid rule"
    assert "color: var(--warn);" in rule.group(1) and "var(--warn)" in rule.group(1), rule.group(1)


def test_privacy_lists_the_nav_badge_among_the_places_that_say_the_guard_is_incomplete():
    """QA-N7.  PRIVACY.md lists where the lab says so; the badge is the one every page shows."""
    text = flat(privacy_known_gaps())
    assert 'the Airgapped badge in the nav bar reads "Airgapped, guard incomplete" in amber' in text, (
        "PRIVACY.md does not list the nav badge among the places an incomplete guard shows"
    )


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


def test_privacy_says_spawned_and_forkserver_children_do_not_inherit_the_guard():
    """QA-N3.  The guard is monkey-patched state of one interpreter.  A child started with multiprocessing's
    `fork` method (or os.fork()) is a copy of it and refuses; a child started with `spawn` or `forkserver` is a
    new interpreter and does not install the guard (run in ci, lock and py310: EgressBlocked for fork,
    ConnectError for the other two).  `spawn` is the macOS default and `forkserver` is the default of every
    other POSIX platform from Python 3.14 (read in 3.14.9's multiprocessing/context.py; not run on Linux here),
    so 'Other processes' names them.  DOC-9 already fails if a call to install_guard() appears anywhere new."""
    bullet = other_processes_bullet()
    for needle in (
        "child processes that Python code starts with multiprocessing's `spawn` or `forkserver` method",
        "the default on macOS and, from Python 3.14, on Linux",
        "A child started with `fork` inherits the guard",
    ):
        assert needle in bullet, f"PRIVACY.md's 'Other processes' bullet does not say {needle!r}: {bullet}"


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
