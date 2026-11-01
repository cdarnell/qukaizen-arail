"""In-process deep-model swap — sprints/2026-08-11-two-slot-chat-models
Part 4.

Uses a fake AeroLLMBackend CLASS (not just instances) so
_swap_optional_chat_backend's construction step (`AeroLLMBackend()`,
zero-arg) and its `_shared` singleton-eviction step can both be exercised
without touching the real PyO3 wheel.
"""

from __future__ import annotations

import os
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(_REPO_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))


class _FakeAeroLLMBackend:
    """Mimics the real class's load-bearing shape: a `_shared` class dict,
    a zero-arg constructor that reads AEROLLM_MODEL from env, and a
    controllable `_close()`."""

    _shared: dict = {}
    fail_construction = False
    close_raises: Exception | None = None

    def __init__(self):
        if _FakeAeroLLMBackend.fail_construction:
            raise RuntimeError("model dir not found")
        self.model_name = os.getenv("AEROLLM_MODEL", "unset")
        self.backend_name = "aerollm"
        self.close_calls = 0

    def _close(self):
        self.close_calls += 1
        if _FakeAeroLLMBackend.close_raises is not None:
            raise _FakeAeroLLMBackend.close_raises


def _isolate(monkeypatch):
    from arail.registry import core as reg_core
    tmp_dir = tempfile.mkdtemp(prefix="arail-deep-swap-registry-")
    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE", os.path.join(tmp_dir, "model_registry.json"))
    monkeypatch.setenv("MODEL_BACKEND", "ollama_native")
    monkeypatch.setenv("MODEL_NAME", "llama-ai-eng")
    monkeypatch.setenv("AEROLLM_MODEL", "Qwen2.5-7B-Instruct-4bit")
    reg_core.reset_registry()


def _reset_fake():
    _FakeAeroLLMBackend._shared = {}
    _FakeAeroLLMBackend.fail_construction = False
    _FakeAeroLLMBackend.close_raises = None


def _patch_fake_class(monkeypatch):
    import arail.router.backends as backends_mod
    _reset_fake()
    monkeypatch.setattr(backends_mod, "AeroLLMBackend", _FakeAeroLLMBackend)
    monkeypatch.setattr("arail.agents.deep_policy.invalidate_deep_router", lambda: None, raising=False)
    return _FakeAeroLLMBackend


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_swap_happy_path(monkeypatch):
    import arail.portal.app as app_mod

    _isolate(monkeypatch)
    fake_cls = _patch_fake_class(monkeypatch)

    old = fake_cls()  # model_name = "Qwen2.5-7B-Instruct-4bit" (current env)
    fake_cls._shared["lab/models::Qwen2.5-7B-Instruct-4bit"] = old
    monkeypatch.setitem(app_mod._OPTIONAL_CHAT_BACKEND_CACHE, "aerollm", old)

    invalidated = []
    monkeypatch.setattr(
        "arail.agents.deep_policy.invalidate_deep_router",
        lambda: invalidated.append(1), raising=False,
    )

    new_backend = app_mod._swap_optional_chat_backend("aerollm", "Qwen2.5-3B-Instruct-4bit")

    assert old.close_calls == 1, "the old instance must be quiesced"
    assert "lab/models::Qwen2.5-7B-Instruct-4bit" not in fake_cls._shared, (
        "the old instance must be evicted from _shared by identity"
    )
    assert os.environ.get("AEROLLM_MODEL") == "Qwen2.5-3B-Instruct-4bit", (
        "the new model must be persisted to env BEFORE construction"
    )
    assert invalidated == [1], "deep_policy's cached router must be invalidated"
    assert new_backend.model_name == "Qwen2.5-3B-Instruct-4bit"
    assert new_backend.backend_name == "aerollm"
    assert app_mod._OPTIONAL_CHAT_BACKEND_CACHE.get("aerollm") is new_backend

    from arail.registry import get_registry
    from arail.registry.store import TIER1_ID
    reg = get_registry()
    reg._ensure_loaded()
    assert reg.entries[TIER1_ID].model_id == "Qwen2.5-3B-Instruct-4bit"
    assert reg.entries[TIER1_ID].source == "user"


def test_swap_with_nothing_resident_just_constructs(monkeypatch):
    import arail.portal.app as app_mod

    _isolate(monkeypatch)
    fake_cls = _patch_fake_class(monkeypatch)

    new_backend = app_mod._swap_optional_chat_backend("aerollm", "Qwen2.5-3B-Instruct-4bit")
    assert new_backend.model_name == "Qwen2.5-3B-Instruct-4bit"
    assert app_mod._OPTIONAL_CHAT_BACKEND_CACHE.get("aerollm") is new_backend


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------

def test_swap_quiesce_failure_leaves_old_resident_and_raises(monkeypatch):
    import arail.portal.app as app_mod

    _isolate(monkeypatch)
    fake_cls = _patch_fake_class(monkeypatch)
    fake_cls.close_raises = RuntimeError("still generating")

    old = fake_cls()
    fake_cls._shared["lab/models::Qwen2.5-7B-Instruct-4bit"] = old
    monkeypatch.setitem(app_mod._OPTIONAL_CHAT_BACKEND_CACHE, "aerollm", old)

    raised = False
    try:
        app_mod._swap_optional_chat_backend("aerollm", "Qwen2.5-3B-Instruct-4bit")
    except app_mod._ChatBackendSwapFailed as exc:
        raised = True
        assert "mid-generation" in exc.reason.lower() or "retry" in exc.reason.lower()

    assert raised, "a quiesce failure must raise _ChatBackendSwapFailed"
    assert app_mod._OPTIONAL_CHAT_BACKEND_CACHE.get("aerollm") is old, (
        "the old instance must stay resident and answerable after a failed swap"
    )
    assert "lab/models::Qwen2.5-7B-Instruct-4bit" in fake_cls._shared
    assert os.environ.get("AEROLLM_MODEL") == "Qwen2.5-7B-Instruct-4bit", (
        "env must NOT be updated when the old instance never quiesced"
    )


def test_swap_construction_failure_after_teardown_reports_cold(monkeypatch):
    """The old model is already gone (quiesced fine) but the new one
    fails to load — must be an honest failure, not a silent revert."""
    import arail.portal.app as app_mod

    _isolate(monkeypatch)
    fake_cls = _patch_fake_class(monkeypatch)

    old = fake_cls()
    fake_cls._shared["lab/models::Qwen2.5-7B-Instruct-4bit"] = old
    monkeypatch.setitem(app_mod._OPTIONAL_CHAT_BACKEND_CACHE, "aerollm", old)

    fake_cls.fail_construction = True

    raised = False
    try:
        app_mod._swap_optional_chat_backend("aerollm", "nonexistent-model")
    except app_mod._ChatBackendSwapFailed as exc:
        raised = True
        assert "failed to load" in exc.reason.lower()

    assert raised
    assert old.close_calls == 1, "the old instance was quiesced before construction was attempted"
    assert "aerollm" not in app_mod._OPTIONAL_CHAT_BACKEND_CACHE, (
        "no stale/wrong instance should be left in the cache after a failed swap"
    )


def test_swap_refuses_airllm(monkeypatch):
    import arail.portal.app as app_mod

    _isolate(monkeypatch)
    _patch_fake_class(monkeypatch)

    raised = False
    try:
        app_mod._swap_optional_chat_backend("airllm", "some-model")
    except NotImplementedError:
        raised = True
    assert raised, "swap is aerollm-only; airllm must not silently no-op"


# ---------------------------------------------------------------------------
# Send-path mismatch refusal (the cosmetic-relabel fix)
# ---------------------------------------------------------------------------

def test_send_path_refuses_a_deep_model_mismatch_instead_of_relabeling(monkeypatch):
    import arail.portal.app as app_mod

    class _Resident:
        model_name = "Qwen2.5-7B-Instruct-4bit"
        backend_name = "aerollm"

    resident = _Resident()
    monkeypatch.setitem(app_mod._OPTIONAL_CHAT_BACKEND_CACHE, "aerollm", resident)

    result = app_mod._prepare_chat_context(
        message="hi", history=[], backend_override="aerollm",
        model_override="Qwen2.5-3B-Instruct-4bit", runtime_override=None,
    )
    assert "error_result" in result, "a mismatched deep model request must refuse, not relabel"
    err = result["error_result"]
    assert "Qwen2.5-3B-Instruct-4bit" in err["reply"]
    assert "Qwen2.5-7B-Instruct-4bit" in err["reply"]
    # The critical assertion: the resident backend's model_name must be
    # UNCHANGED — the old bug cosmetically overwrote it to the requested
    # (but not-actually-loaded) model.
    assert resident.model_name == "Qwen2.5-7B-Instruct-4bit"


def test_send_path_allows_a_deep_request_matching_the_resident_model(monkeypatch):
    """A matching model must sail past the identity check. The rest of
    _prepare_chat_context (ceiling, lab_brain prompt assembly, ...) is
    unrelated to this fix and hardware-dependent (the secondary-role
    ceiling reads real discovered RAM) — stub the ceiling so this test
    verifies identity specifically, not the whole downstream pipeline."""
    import arail.portal.app as app_mod
    from arail.registry import ceiling as ceiling_mod

    class _Resident:
        model_name = "Qwen2.5-7B-Instruct-4bit"
        backend_name = "aerollm"

    resident = _Resident()
    monkeypatch.setitem(app_mod._OPTIONAL_CHAT_BACKEND_CACHE, "aerollm", resident)
    monkeypatch.setattr(
        ceiling_mod, "resolve_answering_model",
        lambda model_id, *, role, backend, model_path=None: ceiling_mod.ModelProvenance(
            model_id, 7.0, "override", role, backend),
    )

    result = app_mod._prepare_chat_context(
        message="hi", history=[], backend_override="aerollm",
        model_override="Qwen2.5-7B-Instruct-4bit", runtime_override=None,
    )
    error = result.get("error_result")
    assert error is None or "isn't the loaded deep model" not in error.get("reply", ""), (
        "a model_override matching the resident model must never trip the "
        "identity-mismatch refusal"
    )
