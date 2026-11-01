"""How ModelRouter picks a backend when nothing tells it to.

The fallback used to be the literal string "mlx". That skipped the
project's own `config.MODEL_BACKEND` default of "auto", so any moment
where MODEL_BACKEND was absent from the environment produced an MLX
backend — and "MLX not installed" on machines that had no MLX, while
Ollama sat right there. In the portal that surfaced as
/api/chat/models returning a degraded 4-key payload, which is what made
33 tests fail in the full suite while passing standalone.
"""
from __future__ import annotations



import pytest

from arail.router import core


def test_unset_env_uses_configured_default_not_hardcoded_mlx(monkeypatch):
    monkeypatch.delenv("MODEL_BACKEND", raising=False)

    import arail.config as config
    monkeypatch.setattr(config, "MODEL_BACKEND", "ollama_native")

    class _Fake:
        def __init__(self):
            pass

    monkeypatch.setitem(core.BACKEND_MAP, "ollama_native", _Fake)
    r = core.ModelRouter(billing_source="ui")
    assert r.backend_name == "ollama_native", (
        "router ignored config.MODEL_BACKEND and fell back to a hardcoded "
        "platform"
    )


def test_configured_backend_is_read_lazily(monkeypatch):
    """Bound at call time, not import time — config is reloaded by tests
    and by env changes, and a captured value would freeze the first one."""
    import arail.config as config
    monkeypatch.setattr(config, "MODEL_BACKEND", "cpu")
    assert core._configured_backend() == "cpu"
    monkeypatch.setattr(config, "MODEL_BACKEND", "auto")
    assert core._configured_backend() == "auto"


def test_configured_backend_falls_back_to_auto_when_blank(monkeypatch):
    import arail.config as config
    monkeypatch.setattr(config, "MODEL_BACKEND", "")
    assert core._configured_backend() == "auto"


def test_auto_detect_does_not_claim_mlx_when_mlx_is_absent(monkeypatch):
    """Apple Silicon without mlx-lm is a real configuration — the
    minimalist tier does not install it."""
    import platform as _platform
    monkeypatch.setattr(_platform, "system", lambda: "Darwin")
    monkeypatch.setattr(_platform, "machine", lambda: "arm64")

    monkeypatch.setattr(core, "_is_importable",
                        lambda m: False if m == "mlx_lm" else True)

    assert core.ModelRouter._auto_detect() == "ollama_native"


def test_auto_detect_uses_mlx_when_present(monkeypatch):
    import platform as _platform
    monkeypatch.setattr(_platform, "system", lambda: "Darwin")
    monkeypatch.setattr(_platform, "machine", lambda: "arm64")

    monkeypatch.setattr(core, "_is_importable", lambda m: True)
    assert core.ModelRouter._auto_detect() == "mlx"


def test_is_importable_is_a_real_import_not_a_spec_lookup():
    """A spec that cannot execute must read as unusable — that is the
    whole failure this guards."""
    assert core._is_importable("json") is True
    assert core._is_importable("a_module_that_does_not_exist_xyz") is False


def test_is_importable_false_when_the_import_raises(monkeypatch):
    import importlib
    def boom(name):
        raise RuntimeError("function '_has_torch_function' already has a docstring")
    monkeypatch.setattr(importlib, "import_module", boom)
    assert core._is_importable("mlx_lm") is False


def test_auto_detect_non_apple_is_unchanged(monkeypatch):
    import platform as _platform
    import shutil as _shutil
    monkeypatch.setattr(_platform, "system", lambda: "Linux")
    monkeypatch.setattr(_platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(_shutil, "which", lambda n: "/usr/bin/nvidia-smi"
                        if n == "nvidia-smi" else None)
    assert core.ModelRouter._auto_detect() == "cuda"
    monkeypatch.setattr(_shutil, "which", lambda n: None)
    assert core.ModelRouter._auto_detect() == "cpu"


def test_explicit_backend_argument_still_wins(monkeypatch):
    monkeypatch.setenv("MODEL_BACKEND", "cpu")

    class _Fake:
        def __init__(self):
            pass

    monkeypatch.setitem(core.BACKEND_MAP, "ollama_native", _Fake)
    r = core.ModelRouter("ollama_native", billing_source="ui")
    assert r.backend_name == "ollama_native"


def test_env_still_wins_over_config(monkeypatch):
    monkeypatch.setenv("MODEL_BACKEND", "ollama_native")
    import arail.config as config
    monkeypatch.setattr(config, "MODEL_BACKEND", "cpu")

    class _Fake:
        def __init__(self):
            pass

    monkeypatch.setitem(core.BACKEND_MAP, "ollama_native", _Fake)
    assert core.ModelRouter(billing_source="ui").backend_name == "ollama_native"
