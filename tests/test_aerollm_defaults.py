"""Tests for AeroLLMBackend's 0.1.0-alpha minimum-resource defaults.

The backend should:
  1. Default ``AEROLLM_MODEL`` to ``Qwen2.5-7B-Instruct-4bit`` (~4 GB
     resident, fits a 16 GB Apple Silicon Mac with headroom).
  2. Translate ``AEROLLM_KV_BUDGET_PCT`` (a fraction of system RAM) into
     a byte-valued ``kv_memory_budget=`` kwarg passed to the Runtime.
  3. Honor an explicit ``AEROLLM_MODEL`` override.
  4. Skip the KV budget kwarg entirely when the env var is unset / invalid,
     so aerollm's own auto-detect (80% of RAM) takes over.
  5. Surface a useful error when the model directory is missing — the
     suggested ``huggingface-cli download`` line should match the
     resolved model name and point at ``mlx-community/<name>``.

These tests stub ``aerollm_api.Runtime`` so we can run on any host
(including CI) without the wheel being importable. The real wheel is
exercised in the live smoke that PR #N's verification section calls
out (start the lab, hit /chat, confirm 4-bit Qwen2.5 in the reply
footer)."""

from __future__ import annotations

import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(_REPO_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))

# Load arail.config eagerly so its load_dotenv() fires NOW, at module
# import time. Otherwise the first ``from arail.router.backends import
# AeroLLMBackend`` inside a test body re-loads .env after the env_clean
# fixture's delenv has already run, undoing the cleanup. This is a
# pytest-collection-order subtlety: when the file is run in isolation,
# nothing else has touched arail.config beforehand. Importing here
# pins load_dotenv to before any fixture runs, so monkeypatch.delenv
# reliably wins.
import arail.config  # noqa: F401, E402


@pytest.fixture
def stub_aerollm_api(monkeypatch):
    """Replace ``aerollm_api.Runtime`` with a stub that captures init
    kwargs so we can assert on the values the backend chose without
    starting a real Metal context.

    Yields the dict that gets populated with the kwargs on construction.
    """
    captured: dict = {}

    class _StubRuntime:
        def __init__(self, model_path, **kwargs):
            captured["model_path"] = model_path
            captured["kwargs"] = kwargs

        def start(self):
            pass

        def shutdown(self):
            pass

    # Make sure aerollm_api is importable; substitute Runtime in place.
    import aerollm_api  # noqa: F401  — must succeed in test env
    monkeypatch.setattr("aerollm_api.Runtime", _StubRuntime)

    # Bypass the model-dir-not-found guard for these tests by claiming
    # any path under our temp models dir exists. We're testing kwargs
    # plumbing, not filesystem semantics.
    real_isdir = os.path.isdir

    def _isdir(p):
        # Treat any aerollm-test path as present.
        if "aerollm-test-models" in str(p):
            return True
        return real_isdir(p)

    monkeypatch.setattr("os.path.isdir", _isdir)
    return captured


@pytest.fixture
def env_clean(monkeypatch):
    """Clear the aerollm env vars so tests start from a known state."""
    for var in (
        "AEROLLM_MODEL",
        "AEROLLM_KV_BUDGET_PCT",
        "AEROLLM_RING_DEPTH",
        "AEROLLM_DRAFT_MODEL",
        "ARAIL_MODELS_DIR",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ARAIL_MODELS_DIR", "/tmp/aerollm-test-models")
    yield


# ── Default model name ───────────────────────────────────────────────────


def test_default_model_is_qwen25_7b_4bit(env_clean, stub_aerollm_api):
    """The minimum-resource default ships Qwen2.5-7B-Instruct-4bit."""
    from arail.router.backends import AeroLLMBackend
    backend = AeroLLMBackend()
    assert backend.model_name == "Qwen2.5-7B-Instruct-4bit"
    # And the path the runtime sees joins ARAIL_MODELS_DIR.
    assert stub_aerollm_api["model_path"] == \
        "/tmp/aerollm-test-models/Qwen2.5-7B-Instruct-4bit"


def test_aerollm_model_env_overrides_default(env_clean, stub_aerollm_api,
                                             monkeypatch):
    """Explicit AEROLLM_MODEL wins over the default."""
    monkeypatch.setenv("AEROLLM_MODEL", "Llama-3.1-70B-Instruct-4bit")
    from arail.router.backends import AeroLLMBackend
    backend = AeroLLMBackend()
    assert backend.model_name == "Llama-3.1-70B-Instruct-4bit"
    assert stub_aerollm_api["model_path"] == \
        "/tmp/aerollm-test-models/Llama-3.1-70B-Instruct-4bit"


def test_absolute_model_path_passes_through(env_clean, stub_aerollm_api,
                                            monkeypatch):
    """An absolute AEROLLM_MODEL path is used as-is, not joined to
    ARAIL_MODELS_DIR."""
    monkeypatch.setenv("AEROLLM_MODEL", "/absolute/aerollm-test-models/foo")
    from arail.router.backends import AeroLLMBackend
    AeroLLMBackend()
    assert stub_aerollm_api["model_path"] == "/absolute/aerollm-test-models/foo"


# ── KV budget translation ────────────────────────────────────────────────


def test_kv_budget_pct_translates_to_bytes(env_clean, stub_aerollm_api,
                                           monkeypatch):
    """AEROLLM_KV_BUDGET_PCT=0.5 → kv_memory_budget = 50% of system RAM
    in bytes, passed to the Runtime constructor."""
    import psutil
    expected = int(psutil.virtual_memory().total * 0.5)

    monkeypatch.setenv("AEROLLM_KV_BUDGET_PCT", "0.5")
    from arail.router.backends import AeroLLMBackend
    AeroLLMBackend()

    assert "kv_memory_budget" in stub_aerollm_api["kwargs"]
    got = stub_aerollm_api["kwargs"]["kv_memory_budget"]
    assert got == expected, f"expected {expected:,} bytes, got {got:,}"


def test_kv_budget_pct_unset_leaves_aerollm_default(env_clean,
                                                   stub_aerollm_api):
    """When AEROLLM_KV_BUDGET_PCT is unset, the kwarg is omitted so
    aerollm's own auto-detect (80% of RAM) kicks in."""
    from arail.router.backends import AeroLLMBackend
    AeroLLMBackend()
    assert "kv_memory_budget" not in stub_aerollm_api["kwargs"], \
        "kv_memory_budget should be omitted when AEROLLM_KV_BUDGET_PCT is unset"


def test_kv_budget_pct_invalid_value_skips_kwarg(env_clean, stub_aerollm_api,
                                                 monkeypatch):
    """Garbage values (non-numeric, out-of-range) silently fall back to
    aerollm's auto-detect rather than raising."""
    for bad in ("not-a-number", "1.5", "0.0", "-0.1", "10"):
        monkeypatch.setenv("AEROLLM_KV_BUDGET_PCT", bad)
        # Re-import the backend module so the singleton-ish state doesn't
        # carry over (Runtime stub's captured dict gets overwritten).
        from arail.router.backends import AeroLLMBackend
        AeroLLMBackend()
        assert "kv_memory_budget" not in stub_aerollm_api["kwargs"], (
            f"AEROLLM_KV_BUDGET_PCT={bad!r} should fall back to aerollm "
            f"auto-detect (no kwarg)"
        )


def test_kv_budget_pct_boundary_values(env_clean, stub_aerollm_api,
                                       monkeypatch):
    """0 < pct < 1 is the accepted range; 0.01 and 0.99 should both
    translate to a positive byte value."""
    import psutil
    total = psutil.virtual_memory().total

    for pct_str, expected in (("0.01", int(total * 0.01)),
                              ("0.99", int(total * 0.99))):
        monkeypatch.setenv("AEROLLM_KV_BUDGET_PCT", pct_str)
        from arail.router.backends import AeroLLMBackend
        AeroLLMBackend()
        got = stub_aerollm_api["kwargs"].get("kv_memory_budget")
        assert got == expected, f"pct={pct_str}: expected {expected}, got {got}"


# ── Error message on missing model dir ──────────────────────────────────


def test_missing_model_dir_suggests_correct_hf_repo(env_clean, monkeypatch):
    """When the model directory is missing, the RuntimeError's
    huggingface-cli download line should target mlx-community/<model>
    (the canonical 4-bit MLX repo namespace), not the bf16 Qwen repo."""
    # Don't use stub_aerollm_api fixture — we want real isdir behavior here.
    monkeypatch.setenv("ARAIL_MODELS_DIR", "/tmp/definitely-does-not-exist")
    from arail.router.backends import AeroLLMBackend

    with pytest.raises(RuntimeError) as excinfo:
        AeroLLMBackend()
    msg = str(excinfo.value)
    assert "QueueLLM model dir not found" in msg
    # The default model is Qwen2.5-7B-Instruct-4bit; the suggested HF
    # repo is mlx-community/<name>.
    assert "mlx-community/Qwen2.5-7B-Instruct-4bit" in msg, msg
    assert "huggingface-cli download" in msg


def test_missing_model_dir_uses_overridden_model_in_hint(env_clean,
                                                       monkeypatch):
    """If the operator overrode AEROLLM_MODEL, the download hint uses
    that name (so they don't get pointed at the wrong repo)."""
    monkeypatch.setenv("ARAIL_MODELS_DIR", "/tmp/definitely-does-not-exist")
    monkeypatch.setenv("AEROLLM_MODEL", "Llama-3.1-70B-Instruct-4bit")
    from arail.router.backends import AeroLLMBackend

    with pytest.raises(RuntimeError) as excinfo:
        AeroLLMBackend()
    msg = str(excinfo.value)
    assert "mlx-community/Llama-3.1-70B-Instruct-4bit" in msg, msg
