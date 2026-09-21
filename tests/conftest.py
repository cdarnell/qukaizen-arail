"""Pytest fixtures shared by every test in this suite.

The portal middleware redirects unauthenticated requests to /welcome
when ARAIL_PASSWORD is missing/placeholder. Tests instantiate the app
directly via TestClient and don't go through setup, so we plant a
real-looking password into the environment for the test session.

Tests that specifically want to exercise the onboarding flow can
monkeypatch the env var back to empty.
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

# ---------------------------------------------------------------------------
# Package-source isolation — MUST run before any `import arail.*`.
#
# `arail` is installed editable (`pip install -e .`) against the checkout it
# was installed FROM. A git worktree is a separate checkout of a different
# branch/commit; its own `pip install -e .` was never (re-)run, so the
# editable install's .pth file still points at the ORIGINAL checkout's
# `src/`. Without this, every test file that doesn't carry its own explicit
# `sys.path.insert(0, .../src)` boilerplate silently imports `arail` from
# that other checkout — exercising code this worktree never touched, and
# missing every change this worktree DID make. (Discovered running the two-
# slot chat model redesign, sprints/2026-08-11-two-slot-chat-models: a
# `tests/registry/` run passed cleanly while testing code with none of that
# sprint's changes — `arail.portal.app` had no `_chat_slots_payload`
# attribute at all, `hasattr` confirmed it — because nothing in that
# directory ever pointed sys.path at this checkout.) Once ANY module of a
# package is imported, Python caches it for the rest of the process — so
# this must win the race and run before the FIRST `import arail` anywhere
# in the session, which means here, at the top of the root conftest, ahead
# of the .env isolation below (that isolation only protects `arail.config`'s
# import-time load_dotenv() call, not module resolution itself).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC_DIR = os.path.join(_REPO_ROOT, "src")
if _SRC_DIR in sys.path:
    sys.path.remove(_SRC_DIR)
sys.path.insert(0, _SRC_DIR)

# ---------------------------------------------------------------------------
# Session-level .env isolation — MUST run before any `import arail.*`.
#
# arail.config calls load_dotenv() at import time. Without ARAIL_ENV_FILE it
# uses python-dotenv's walk-up search, which escapes the checkout (a git
# worktree finds the parent repo's real .env) and hydrates the developer's
# lab config (LAB_INTENT, LAB_MODE, COMPUTE_SOURCE, ...) into the test
# process at collection time — the tests then behave differently than CI.
# Point it at a file that does not exist inside a session tmp dir so the
# import-time load is a no-op. The autouse fixture below re-points it at a
# per-test tmp file for endpoints that WRITE the .env (welcome, airgap
# toggle), so no test can touch a real checkout's .env either.
# ---------------------------------------------------------------------------
_SESSION_ENV_DIR = tempfile.mkdtemp(prefix="arail-pytest-env-")
os.environ["ARAIL_ENV_FILE"] = os.path.join(_SESSION_ENV_DIR, "portal.env")
os.environ["ARAIL_AIRGAP_AUDIT_FILE"] = os.path.join(
    _SESSION_ENV_DIR, "airgap_audit.jsonl"
)
os.environ["ARAIL_SECRETS_FILE"] = os.path.join(_SESSION_ENV_DIR, "secrets.env")
# Same isolation, same reason, for model_defaults.yaml (arail.model_defaults):
# arail.config applies it at import time too, and a developer's real
# model_defaults.yaml specifying an Ollama tag as default_a can crash a
# test whose sandboxed MODEL_BACKEND resolves to a non-Ollama backend
# (a real bug this exact isolation gap produced once already — see
# tests/test_model_defaults.py). Point at a file that will never exist.
os.environ["ARAIL_MODEL_DEFAULTS_FILE"] = os.path.join(
    _SESSION_ENV_DIR, "model_defaults.yaml"
)


@pytest.fixture
def isolated_secrets(monkeypatch, tmp_path):
    """Redirect the portal's secrets file to a tmp path and restore env on teardown.

    Any test that calls an endpoint that writes lab/data/secrets.env (e.g.
    POST /api/chat/default, POST /api/providers/save) must use this fixture so it
    never clobbers a developer's real secrets and never leaks COMPUTE_SOURCE /
    ARAIL_CHAT_DEFAULT_MODEL / ARAIL_MODEL_CTX_OVERRIDES into the process for
    downstream tests.

    The fixture:
      1. Monkeypatches portal_app._secrets_path to return a tmp file.
      2. Deletes the three polluting env keys before the test runs.
      3. After the test, restores any values those keys had — the endpoint
         writes os.environ directly (bypassing monkeypatch), so we must
         restore by hand in teardown.
    """
    from arail.portal import app as portal_app

    fake = tmp_path / "secrets.env"
    monkeypatch.setattr(portal_app, "_secrets_path", lambda: fake)

    _leaky = ("COMPUTE_SOURCE", "ARAIL_CHAT_DEFAULT_MODEL", "ARAIL_MODEL_CTX_OVERRIDES")
    _saved = {k: os.environ.get(k) for k in _leaky}
    for k in _leaky:
        monkeypatch.delenv(k, raising=False)

    yield fake

    # The endpoint writes os.environ directly — monkeypatch won't clean those up.
    for k, v in _saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(autouse=True)
def _isolated_env_file(monkeypatch, tmp_path):
    """Point the portal's .env (and airgap audit log) at per-test tmp files.

    ARAIL_ENV_FILE is honored by arail.config's load_dotenv, the onboarding
    writer/reader (_write_env_kv / _lab_password_set), and the airgap
    toggle's _toggle_env_path — so a test that hits POST /api/airgap/toggle
    or /api/welcome/setup without further setup writes tmp files instead of
    the developer's real .env / lab/data/airgap_audit.jsonl. Tests that
    monkeypatch app._TOGGLE_ENV_PATH / _TOGGLE_AUDIT_PATH still win (the
    module override takes precedence); tests that want a specific path can
    setenv ARAIL_ENV_FILE themselves (their setenv runs after this one).
    """
    monkeypatch.setenv("ARAIL_ENV_FILE", str(tmp_path / "portal.env"))
    monkeypatch.setenv(
        "ARAIL_AIRGAP_AUDIT_FILE", str(tmp_path / "airgap_audit.jsonl")
    )
    # Same treatment for the provider-token store: endpoints that persist
    # tokens / chat defaults (POST /api/providers/save, /api/chat/default)
    # must never rewrite a developer's real lab/data/secrets.env. Tests that
    # use the isolated_secrets fixture replace _secrets_path wholesale, which
    # still takes precedence over this env default.
    monkeypatch.setenv("ARAIL_SECRETS_FILE", str(tmp_path / "secrets.env"))


@pytest.fixture(autouse=True)
def _no_ambient_lab_mode_env(monkeypatch):
    """Delete (and restore after the test) the lab-mode/identity env keys.

    Two leak classes this closes:
      1. Ambient values from the developer's shell (arailctl sources .env)
         — e.g. LAB_INTENT=other flips identity defaults the tests assume.
      2. Endpoints that write os.environ directly (the airgap toggle sets
         LAB_MODE/ARAIL_MODE, /api/welcome/setup sets LAB_NAME and
         OPEN_NOTEBOOK_ENCRYPTION_KEY) — monkeypatch snapshots the pre-test
         state here and restores it on teardown, so a hybrid toggle in one
         test can't make lab_mode() report hybrid in the next.

    Tests that need one of these set use monkeypatch.setenv in their own
    body/fixture, which runs after this autouse fixture and wins.
    """
    for key in (
        "LAB_MODE",
        "ARAIL_MODE",
        "LAB_INTENT",
        "LAB_INTENT_NAME",
        "LAB_INTENT_DESCRIPTION",
        "LAB_NAME",
        "OPEN_NOTEBOOK_ENCRYPTION_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _arail_password_for_tests(monkeypatch):
    """Set a non-placeholder ARAIL_PASSWORD for every test by default.

    Onboarding-flow tests can monkeypatch this back if they want to
    exercise the no-password code path.
    """
    monkeypatch.setenv("ARAIL_PASSWORD", "test-passphrase-not-real")


@pytest.fixture(autouse=True)
def _allow_testclient_host(monkeypatch):
    """Let Starlette TestClient's default ``Host: testserver`` through the
    local_trust_boundary middleware (anti-DNS-rebinding Host allowlist).

    In production only loopback names pass; `testserver` is a synthetic
    host a real browser can never send while connecting to the lab, so
    allowing it in tests creates no attack surface. Tests that want to
    exercise a rejected Host monkeypatch this back or pass an explicit
    non-loopback Host header.
    """
    monkeypatch.setenv("ARAIL_ALLOWED_HOSTS", "testserver")


@pytest.fixture(autouse=True)
def _no_ambient_window_override(monkeypatch, tmp_path_factory):
    """Isolate the persisted work-window override per test.

    ``scheduler.current_window()`` now consults a persisted override
    (``lab/data/window_override.json``). Without isolation a developer's
    (or another test's) ambient override leaks into every scheduler
    consumer and makes unrelated tests non-deterministic. Point the
    override file at a fresh tmp path and clear in-memory state.
    """
    from arail import scheduler
    d = tmp_path_factory.mktemp("window_override")
    monkeypatch.setattr(scheduler, "_override_path", lambda: d / "window_override.json")
    scheduler._reset_window_override_for_tests()


@pytest.fixture(autouse=True)
def _no_ambient_halt_flag(monkeypatch, tmp_path_factory):
    """Isolate the persisted halt flag per test (same rationale as the
    window override above — a developer's halted lab must not leak into
    tests, and a test that halts must not halt the developer's lab)."""
    from arail import scheduler
    d = tmp_path_factory.mktemp("halt_flag")
    monkeypatch.setattr(scheduler, "_halt_path", lambda: d / "halt.json")
    scheduler._reset_halt_for_tests()


@pytest.fixture(autouse=True)
def _no_ambient_world_mount(monkeypatch, tmp_path_factory):
    """Hide any World a developer has mounted on this machine from the tests.

    The portal endpoints / Buddy resolve the mount via
    ``world_mount.current_mount()`` (no arg) → ``_default_data_dir()`` → the
    real ``lab/data``. If a developer has run ``./arailctl world mount`` (e.g.
    the physics World), that ambient state leaks into unrelated portal tests and
    makes them non-deterministic — CI passes only because ``lab/`` is
    git-ignored. We point the *default* data dir at a fresh empty directory, so
    the ambient lookup finds nothing mounted. Tests that set up their own mount
    re-``monkeypatch.setattr(world_mount, "_default_data_dir", ...)`` in their
    body (same monkeypatch instance → their override wins), and tests that pass
    an explicit ``current_mount(data_dir=...)`` are unaffected.
    """
    try:
        from arail import world_mount
    except Exception:  # noqa: BLE001
        return  # feature not importable in this context → nothing to isolate

    clean = tmp_path_factory.mktemp("no-ambient-world")
    monkeypatch.setattr(world_mount, "_default_data_dir", lambda: clean)
    # Also isolate the catalog dir: mount() now adopts a byte-copy of each
    # bundle into WORLDS_DIR so it survives unmount. Point that at a fresh
    # empty dir so tests never write into the real repo lab/worlds/ (and so a
    # developer's ambient catalog can't leak into listing tests). Tests that
    # need their own catalog override _default_worlds_dir in their own body.
    clean_worlds = tmp_path_factory.mktemp("no-ambient-worlds-dir")
    monkeypatch.setattr(world_mount, "_default_worlds_dir", lambda: clean_worlds)


@pytest.fixture(autouse=True)
def _isolated_agent_observability_data_root(monkeypatch, tmp_path):
    """Isolate agent_trace / flight-recorder / agent_context state per test
    (sprint 2026-09-20-buddy-front-and-center hermeticity fix).

    Since that sprint, ``ModelRouter.complete()``/``.stream_complete()`` —
    the single chokepoint every agent (and chat) inference call already
    went through — now writes an ``agent_trace`` record on every call, and
    may capture a body when the flight recorder is on. Both persist to
    ``DATA_DIR`` (lazily resolved per call, by design — see
    ``agent_trace.py``'s module docstring — specifically so a fixture like
    this one can redirect it). Without this, ANY test anywhere in the
    suite that drives a real ``ModelRouter`` — not just the sprint's own
    new test files — writes into the developer's actual
    ``lab/data/agent_traces.jsonl`` / ``flight_recorder.json``, following
    the same pre-existing pattern as ``_no_ambient_halt_flag`` /
    ``_no_ambient_window_override`` above, just for a data root instead of
    a single file.

    ``activity.py``'s ``LOG_FILE`` is the one exception in this family
    that is NOT resolved lazily — it is a module-level constant bound to
    the real ``config.DATA_DIR`` at ``activity.py``'s import time.
    Monkeypatching ``activity_mod.LOG_FILE`` still works for *future*
    ``.emit()`` disk writes (the method does a fresh global lookup of
    ``LOG_FILE`` on every call, not a value captured once at
    construction) — but the module-level ``activity_log`` singleton
    itself is a specific object every other module already holds a direct
    reference to (``from arail.activity import activity_log``), typically
    imported during collection, well before any fixture runs. Setting
    ``ActivityLog._instance = None`` does **not** reset that — it only
    affects a *future* ``ActivityLog()`` call, and nothing in the
    codebase makes one after the first (confirmed the hard way: an
    earlier version of this fixture did exactly that and still leaked
    200 stale in-memory events into every test). The only thing that
    actually clears the singleton every other module already points at
    is mutating its buffer in place — the same technique
    ``test_autoresearch_e2e_fake_aerollm.py``'s local ``_fresh_events()``
    helper already uses. This matters to this sprint specifically because
    ``GET /api/agents/status`` (the V7 fix) reads ``activity_log.recent()``
    as a fallback when an agent has no trace yet — an unisolated buffer
    leaks real, accumulated activity into that fallback's numbers.

    Deliberately does NOT isolate ``costs.json`` — ``CostTracker``'s
    singleton binds its ``_data_path`` once, at its own construction
    (also import time, also before any fixture runs), and this sprint
    added no new ``cost_tracker.track()`` call site (only changed the
    ``source=`` value an existing call already used). That leak is
    pre-existing and out of this sprint's scope; see BUILD_LOG.md.

    A companion default this redirect requires: ``portal/app.py``'s
    one-shot World nudge (``_world_prompt_pending()``) checks whether
    ``DATA_DIR / ".world-prompt-seen"`` exists to decide whether to render
    the dashboard's onboarding nudge instead of the normal page chrome.
    Before this fixture existed, most page-rendering tests were
    incidentally reading the real, already-dismissed marker on the
    developer's machine — an always-empty ``tmp_path`` makes every such
    request look like a brand-new, never-onboarded lab instead, which
    broke `tests/portal/test_base_template_smoke.py` AND
    `tests/test_boot_overlay.py` (confirmed: both fail, even standalone,
    without this). Pre-seeding "already seen" as the default restores the
    ambient state those (and presumably other, not-yet-found) tests
    already unknowingly depended on.

    The one place that default is *wrong*: a test specifically about the
    marker's absence. `tests/test_onboarding.py::test_dashboard_unblocks_
    after_onboarding` is exactly that, and — like `tests/test_world_
    first_impression.py`'s tests already do for the same reason —
    explicitly monkeypatches `portal.app._world_prompt_marker` to a
    guaranteed-absent path of its own, which wins over this fixture's
    default regardless of `DATA_DIR`. A global fixture deciding an
    ambient default is fine; a global fixture deciding it for the ONE
    test that is *about* that state is the bug this docstring's git
    history is a cautionary tale of (an earlier version of this fixture
    got exactly this backwards twice: first by not seeding at all, then
    by seeding only in one unrelated test module instead of here).
    """
    from arail import agent_context, agent_trace, config
    from arail import activity as activity_mod

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    (tmp_path / ".world-prompt-seen").touch()
    monkeypatch.setattr(activity_mod, "LOG_FILE", tmp_path / "activity.jsonl")
    activity_mod.activity_log._buffer.clear()

    agent_context._reset_for_tests()
    agent_trace._reset_for_tests()

    yield

    agent_context._reset_for_tests()
    agent_trace._reset_for_tests()
    activity_mod.activity_log._buffer.clear()


@pytest.fixture(autouse=True)
def _reset_egress_guard():
    """Reset the egress guard to un-installed state between tests.

    The guard monkey-patches ``requests.adapters.HTTPAdapter`` and the
    urllib opener.  Without this reset, a test that calls
    ``install_guard()`` would poison every subsequent test that creates a
    ``requests.Session()``.

    This fixture runs after each test (teardown phase) so the guard can
    be inspected in post-test assertions before reset.
    """
    yield
    try:
        import arail.egress
        arail.egress._reset_for_tests()
    except Exception:  # noqa: BLE001
        pass  # If egress hasn't been imported yet, nothing to reset.


@pytest.fixture(autouse=True)
def _stub_embedding_provider(request, monkeypatch):
    """Stub ``arail.dbspec.embed.embed_documents``/``embed_query`` with a
    fast, deterministic, network-free fake — UNLESS the test is marked
    ``@pytest.mark.requires_ollama``, in which case real Ollama is used.

    This exists because Tier 1.2 (arail2-tier1-integration sprint) wires
    ``pkb.index_all``/``_semantic_search`` to call the real embedding
    provider. Without this fixture, every pre-existing test that exercises
    those code paths without its own mock would suddenly need a reachable
    Ollama with ``nomic-embed-text`` pulled — turning unit tests into
    integration tests and reproducing FM18 (a CI runner with no Ollama
    goes red) far outside the eval harness this fixture mode was designed
    for. The stub reuses ``vector_index.hash_embedding`` at the spec's
    declared dimension so callers see vectors of the right shape without
    a network call; it says nothing about retrieval *quality* — that
    question is answered once, honestly, by
    ``scripts/eval/retrieval_ab.py`` (see
    ``sprints/2026-08-08-arail2-tier1-integration/RESULTS.md``), not by
    every unit test re-deciding it.
    """
    if "requires_ollama" in request.node.keywords:
        yield
        return
    try:
        from arail.dbspec import embed as embed_mod
        from arail.dbspec.generated.models_registry import EMBEDDING_DIM
        from arail.vector_index import hash_embedding
    except Exception:  # noqa: BLE001
        yield
        return

    def _fake_embed_documents(texts):
        return [hash_embedding(t, dim=EMBEDDING_DIM) for t in texts]

    def _fake_embed_query(text):
        return hash_embedding(text, dim=EMBEDDING_DIM)

    def _fake_embed(text):
        return hash_embedding(text, dim=EMBEDDING_DIM)

    monkeypatch.setattr(embed_mod, "embed_documents", _fake_embed_documents)
    monkeypatch.setattr(embed_mod, "embed_query", _fake_embed_query)
    monkeypatch.setattr(embed_mod, "embed", _fake_embed)
    yield
