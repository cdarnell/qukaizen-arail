"""Warm-up (sprints/2026-07-29-elite-cli/ARCHITECTURE.md §11, Ruling 6 — WP6).

Covers the Python-side half of the gate list: T22 (startup scheduling of the
boot warm, ``_warm_primary_router()`` timing/backend recording, the
``/api/instance`` field set), F16 (no model id leaks), and T29 (the
onboarding allow-list is unchanged by this sprint — the warm signal rides
the endpoint that was already allow-listed, per A6).

The CLI-side half (``start --warm`` polling and reporting) is
tests/cli/warmup_driver.sh — see that file's header for why the split.
"""
from __future__ import annotations

import ast
import asyncio
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_PY = REPO_ROOT / "src" / "arail" / "portal" / "app.py"


class _FakeRouter:
    backend_name = "ollama_native"

    def __init__(self):
        self.calls = []

    def complete(self, prompt, max_tokens=512, *a, **k):
        self.calls.append((prompt, max_tokens))

        class _R:
            text = "ok"
        return _R()


@pytest.fixture
def wired(monkeypatch, tmp_path):
    from arail.registry import core as reg_core
    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE",
                       str(tmp_path / "model_registry.json"))
    monkeypatch.setenv("MODEL_BACKEND", "ollama_native")
    monkeypatch.setenv("MODEL_NAME", "ai-engineer:latest")
    monkeypatch.delenv("MODEL_API_BASE", raising=False)
    reg_core.reset_registry()

    import arail.portal.app as app_mod
    fake = _FakeRouter()
    monkeypatch.setattr(app_mod, "_get_primary_router", lambda: fake)
    yield app_mod, fake
    reg_core.reset_registry()


# ---------------------------------------------------------------------------
# Startup scheduling (replaces the _boot_warm_explicit() gate tests: boot warm
# is default-on now, so there is no "explicit request" gate left to test)
# ---------------------------------------------------------------------------

def test_startup_schedules_boot_models_task_unconditionally():
    """Boot warm is default-on and independent of ARAIL_AUTOCHECKS
    (sprints/2026-10-20-boot-model-autoresolve): _startup() must schedule
    _boot_models() with no autochecks/explicit-warm condition. Replaces the
    old 'if _autochecks_on or _boot_warm_explicit()' pin (T22) — opting out
    is ARAIL_BOOT_WARM=0, handled inside _boot_models()."""
    text = APP_PY.read_text(encoding="utf-8")
    tree = ast.parse(text)
    startup_src = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_startup":
            startup_src = ast.get_source_segment(text, node)
            break
    assert startup_src, "_startup() not found in app.py"
    assert re.search(r"^\s*asyncio\.create_task\(_boot_models\(\)\)",
                     startup_src, re.M)


# ---------------------------------------------------------------------------
# _warm_primary_router() — timing/backend/skip-reason recording
# ---------------------------------------------------------------------------

def test_warm_records_ms_and_backend_on_a_real_completion(wired):
    app_mod, fake = wired
    app_mod._MODEL_WARM = False
    asyncio.run(app_mod._warm_primary_router())
    assert fake.calls == [("ok", 1)]
    assert app_mod._MODEL_WARM is True
    assert app_mod._MODEL_WARM_BACKEND == "ollama_native"
    assert isinstance(app_mod._MODEL_WARM_MS, int)
    assert app_mod._MODEL_WARM_MS >= 0
    assert app_mod._MODEL_WARM_SKIP_REASON is None


def test_warm_records_skip_reason_when_disabled_by_env(wired, monkeypatch):
    app_mod, fake = wired
    monkeypatch.setenv("ARAIL_TIER0_BOOT_WARM", "0")
    app_mod._MODEL_WARM = False
    asyncio.run(app_mod._warm_primary_router())
    assert fake.calls == []
    assert app_mod._MODEL_WARM is True
    assert app_mod._MODEL_WARM_MS is None
    assert app_mod._MODEL_WARM_BACKEND == "ollama_native"  # backend is still known
    assert app_mod._MODEL_WARM_SKIP_REASON == "ARAIL_TIER0_BOOT_WARM=0"


def test_warm_records_skip_reason_for_in_process_backend(wired, monkeypatch):
    """A backend that isn't ollama_native/openai_compat (weights load
    in-process, e.g. mlx/aerollm) never issues a completion — §11.1's
    'not applicable' case. warm_ms stays null; warm_skipped explains why."""
    app_mod, fake = wired
    fake.backend_name = "mlx"
    app_mod._MODEL_WARM = False
    asyncio.run(app_mod._warm_primary_router())
    assert fake.calls == []
    assert app_mod._MODEL_WARM is True
    assert app_mod._MODEL_WARM_MS is None
    assert app_mod._MODEL_WARM_BACKEND == "mlx"
    assert app_mod._MODEL_WARM_SKIP_REASON is not None
    assert "in-process" in app_mod._MODEL_WARM_SKIP_REASON


def test_warm_records_skip_reason_on_failure(wired, monkeypatch):
    """REVIEW.md B3/F16: warm_skipped is reachable anonymously, pre-
    onboarding (GET /api/instance) — it must NEVER carry the verbatim
    exception text, which can embed an absolute path (hence $HOME/the OS
    username), a model id, or a provider URL. Only the fixed,
    closed-vocabulary sentence may appear; the real text goes to
    activity_log (authenticated) instead — asserted here by simulating an
    exception whose message contains exactly the shapes F16 forbids."""
    app_mod, fake = wired

    def _boom(*a, **k):
        raise ConnectionError(
            "https://provider.example.com/v1: connection refused "
            "(/Users/alice/lab/models/ai-engineer/config.json)")
    monkeypatch.setattr(fake, "complete", _boom)
    app_mod._MODEL_WARM = False
    asyncio.run(app_mod._warm_primary_router())
    assert app_mod._MODEL_WARM is True     # overlay must never trap the user
    assert app_mod._MODEL_WARM_MS is None
    assert app_mod._MODEL_WARM_SKIP_REASON == app_mod._MODEL_WARM_SKIP_REASON_ON_EXCEPTION
    assert "ConnectionError" not in app_mod._MODEL_WARM_SKIP_REASON
    assert "provider.example.com" not in app_mod._MODEL_WARM_SKIP_REASON
    assert "/Users/alice" not in app_mod._MODEL_WARM_SKIP_REASON
    assert "ai-engineer" not in app_mod._MODEL_WARM_SKIP_REASON


# ---------------------------------------------------------------------------
# GET /api/instance — exact new field set, both branches, no model id (F16)
# ---------------------------------------------------------------------------

_WARM_FIELD_NAMES = {"warm", "warm_ms", "warm_skipped", "backend"}
_MODEL_ID_LOOKING_KEYS = {"model", "model_id", "model_name", "checkpoint",
                          "weights", "model_path"}


def test_api_instance_root_branch_exposes_warm_fields_and_no_model_id(monkeypatch):
    from fastapi.testclient import TestClient
    import arail.portal.app as app_mod
    monkeypatch.delenv("ARAIL_INSTANCE", raising=False)
    # Monkeypatch AFTER the TestClient's __enter__ has already run _startup()
    # — _startup() itself unconditionally sets _MODEL_WARM on a quiet boot
    # (the documented "flip now so the overlay dismisses" branch), which
    # would otherwise clobber a value set beforehand.
    with TestClient(app_mod.app) as client:
        monkeypatch.setattr(app_mod, "_MODEL_WARM", True)
        monkeypatch.setattr(app_mod, "_MODEL_WARM_MS", 4200)
        monkeypatch.setattr(app_mod, "_MODEL_WARM_BACKEND", "ollama_native")
        monkeypatch.setattr(app_mod, "_MODEL_WARM_SKIP_REASON", None)
        r = client.get("/api/instance")
    assert r.status_code == 200
    body = r.json()
    assert _WARM_FIELD_NAMES <= set(body.keys())
    assert body["warm"] is True
    assert body["warm_ms"] == 4200
    assert body["warm_skipped"] is None
    assert body["backend"] == "ollama_native"
    assert not (_MODEL_ID_LOOKING_KEYS & set(body.keys())), (
        "a model-identifying field leaked into /api/instance: "
        f"{_MODEL_ID_LOOKING_KEYS & set(body.keys())}"
    )
    # "ollama_native" is a backend CLASS name, never a specific model id
    # (e.g. "ai-engineer:latest" / "llama-ai-eng") anywhere in the body.
    assert "ai-engineer" not in str(body.values())
    assert "llama-ai-eng" not in str(body.values())


def test_api_instance_world_branch_exposes_warm_fields(monkeypatch):
    from fastapi.testclient import TestClient
    import arail.portal.app as app_mod
    monkeypatch.setenv("ARAIL_INSTANCE", "finance")
    monkeypatch.setenv("ARAIL_INSTANCE_TOKEN", "abc123")
    with TestClient(app_mod.app) as client:
        # See the sibling root-branch test for why this happens AFTER entry.
        monkeypatch.setattr(app_mod, "_MODEL_WARM", False)
        monkeypatch.setattr(app_mod, "_MODEL_WARM_MS", None)
        monkeypatch.setattr(app_mod, "_MODEL_WARM_BACKEND", None)
        monkeypatch.setattr(app_mod, "_MODEL_WARM_SKIP_REASON", None)
        r = client.get("/api/instance")
    assert r.status_code == 200
    body = r.json()
    assert _WARM_FIELD_NAMES <= set(body.keys())
    assert body["warm"] is False
    assert body["warm_ms"] is None
    assert body["backend"] is None


# ---------------------------------------------------------------------------
# T29 — no new pre-onboarding endpoint; the allow-list is unchanged
# ---------------------------------------------------------------------------

def _onboarding_gate_allowlist() -> tuple:
    """Mirrors tests/test_instance_live_launch_findings.py's
    _onboarding_gate_allowlist() (duplicated, not imported — that file is a
    test module, not a library; the ~15-line AST walk is cheap to keep in
    sync since both copies pin the SAME literal tuple in app.py)."""
    text = APP_PY.read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) \
                and node.name == "onboarding_gate":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "allowed_prefixes"
                    for t in sub.targets
                ):
                    return tuple(
                        e.value for e in sub.value.elts  # type: ignore[attr-defined]
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)
                    )
    raise AssertionError("onboarding_gate's allowed_prefixes not found")


# Frozen at WP6 time (sprints/2026-07-29-elite-cli) — the warm-up signal
# rides the ALREADY-allow-listed /api/instance (A6); this sprint adds no new
# prefix. An exact tuple comparison so any future addition is a deliberate,
# reviewed change to THIS test, not a silent expansion of anonymous surface.
_EXPECTED_ALLOWLIST = (
    "/welcome",
    "/api/welcome",
    "/static/",
    "/api/system/health",
    "/api/system/metrics",
    "/api/instance",
    "/favicon.ico",
    "/health",
    "/healthz",
    "/metrics",
)


def test_onboarding_gate_allowlist_unchanged_by_this_sprint():
    assert _onboarding_gate_allowlist() == _EXPECTED_ALLOWLIST
