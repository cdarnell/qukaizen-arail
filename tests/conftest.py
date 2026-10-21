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
def _hermetic_boot_models(request, monkeypatch):
    """Keep the portal's boot model resolution/warm off the real machine.

    Starting the app (``with TestClient(app)``) now resolves the two model
    slots and warms them by default (sprints/2026-10-20-boot-model-
    autoresolve). Tests must never talk to a developer's real Ollama or load
    weights, so: warm is off (ARAIL_BOOT_WARM=0) and the resolver's two
    network reads report "Ollama unreachable". Tests that exercise the
    resolver patch these themselves (their patch runs after this one);
    ``@pytest.mark.real_resolver_net`` opts out entirely.
    """
    if request.node.get_closest_marker("real_resolver_net"):
        return
    monkeypatch.setenv("ARAIL_BOOT_WARM", "0")
    from arail import model_resolve
    monkeypatch.setattr(model_resolve, "_fetch_ollama_tags", lambda: None)
    monkeypatch.setattr(model_resolve, "_fetch_capabilities", lambda name: None)
    model_resolve.last_resolution().clear()


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
