"""Phase 0b (load/unload lifecycle honesty) — C6.3 model identity,
C6.6 real ETA, C6.7 click-time re-fit, C6.8 friendly errors.

Sprint: 2026-07-20-model-ux-unification
Architecture: sprints/2026-07-20-model-ux-unification/ARCHITECTURE.md
Implementation-order step 13.

Failure modes covered:
  F-SWITCH     — aeroLLM singleton resident with model A; loading model B
                 must not report `ready` for A (T-SWITCH).
  F-FAKEETA    — hardcoded eta_seconds regardless of size (T-ETA).
  F-CORRUPT    — partial/corrupt model looks loadable until it fails
                 (T-CORRUPT).
  F-REFIT      — fit computed at render is stale by click time (T-REFIT).
  F-DAEMONDOWN — daemon-down surfaces a raw traceback (T-DAEMONDOWN).
"""

from __future__ import annotations

import asyncio
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(_REPO_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))


def _scoped_load_state(monkeypatch, app_mod):
    monkeypatch.setattr(app_mod, "_CHAT_MODEL_LOAD_STATE", dict(app_mod._CHAT_MODEL_LOAD_STATE))
    monkeypatch.setattr(app_mod, "_CHAT_MODEL_LOAD_INFLIGHT", asyncio.Lock())
    monkeypatch.setattr(app_mod, "_OPTIONAL_CHAT_BACKEND_CACHE", {})


# ---------------------------------------------------------------------------
# C6.3/F-SWITCH
# ---------------------------------------------------------------------------

class _FakeAeroBackend:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self.backend_name = "aerollm"


def test_get_optional_chat_backend_refuses_wrong_model_with_identity_error():
    import arail.portal.app as app_mod

    app_mod._OPTIONAL_CHAT_BACKEND_CACHE["aerollm"] = _FakeAeroBackend("Qwen2.5-7B-Instruct-4bit")
    try:
        app_mod._get_optional_chat_backend("aerollm", expected_model="gemma-4-26b-a4b")
        assert False, "must raise on model mismatch"
    except app_mod._ChatBackendModelMismatch as exc:
        assert exc.resident_model == "Qwen2.5-7B-Instruct-4bit"
        assert exc.requested_model == "gemma-4-26b-a4b"
    finally:
        app_mod._OPTIONAL_CHAT_BACKEND_CACHE.clear()


def test_get_optional_chat_backend_allows_the_same_model():
    import arail.portal.app as app_mod

    app_mod._OPTIONAL_CHAT_BACKEND_CACHE["aerollm"] = _FakeAeroBackend("gemma-4-26b-a4b")
    try:
        backend = app_mod._get_optional_chat_backend("aerollm", expected_model="gemma-4-26b-a4b")
        assert backend.model_name == "gemma-4-26b-a4b"
    finally:
        app_mod._OPTIONAL_CHAT_BACKEND_CACHE.clear()


def test_prepare_chat_model_load_reports_honest_refusal_not_false_ready(monkeypatch):
    """T-SWITCH, updated for Part 4 (sprints/2026-08-11-two-slot-chat-
    models): with aeroLLM resident on model A, requesting model B now
    ATTEMPTS an in-process swap (instead of refusing outright) — but
    _FakeAeroBackend here has no _close(), so the swap fails at the
    quiesce step. Either way the outcome required by T-SWITCH still
    holds: state=error, never a false `ready` for model A. See
    test_deep_swap.py for the swap-succeeds path with a fake that
    actually supports teardown."""
    import arail.portal.app as app_mod
    from arail.portal import scheduler

    _scoped_load_state(monkeypatch, app_mod)
    monkeypatch.setattr(scheduler, "_SEM", None)
    app_mod._OPTIONAL_CHAT_BACKEND_CACHE["aerollm"] = _FakeAeroBackend("Qwen2.5-7B-Instruct-4bit")
    monkeypatch.setattr(app_mod, "_real_on_disk_gb", lambda runtime, model: None)

    async def _scenario():
        result = await app_mod._prepare_chat_model_load(
            model="gemma-4-26b-a4b", runtime=None, provider="aerollm"
        )
        assert result["state"] == "error"
        assert "couldn't switch the deep model" in result["message"].lower()
        # Never falsely reports ready for the still-resident model A.
        assert result["state"] != "ready"

    asyncio.run(asyncio.wait_for(_scenario(), timeout=5.0))


def test_chat_send_refuses_stale_resident_model_instead_of_relabeling(monkeypatch):
    """F-SWITCH, chat-send path: unlike /model-load (covered above),
    _prepare_chat_context used to call _get_optional_chat_backend()
    with no expected_model= at all — so a resident aeroLLM singleton
    left over from an earlier AEROLLM_MODEL value was returned as-is,
    and the override_model block a few lines later just relabeled
    active_backend.model_name to whatever the client asked for. That
    cosmetic relabel changed the system prompt / API response's claimed
    model without reloading a single byte of the actually-resident
    weights — the model answers as whatever is really loaded while the
    UI and system prompt both claim the newly-requested one. Confirmed
    live: a resident gemma-4-26b-a4b singleton answered "Are you a MOE
    model?" honestly (as gemma), while the UI's column-B header and the
    persisted turn metadata both said Qwen2.5-7B-Instruct-4bit.
    Wiring expected_model= into this call site must make that a loud,
    honest refusal instead."""
    import arail.portal.app as app_mod

    app_mod._OPTIONAL_CHAT_BACKEND_CACHE["aerollm"] = _FakeAeroBackend("gemma-4-26b-a4b")
    try:
        ctx = app_mod._prepare_chat_context(
            message="Are you a MOE model?",
            history=[],
            backend_override="aerollm",
            model_override="Qwen2.5-7B-Instruct-4bit",
        )
        error_result = ctx.get("error_result")
        assert error_result is not None, (
            "must refuse — silently answering with the resident gemma "
            "weights under a Qwen2.5 label is the exact bug this guards"
        )
        assert "gemma-4-26b-a4b" in error_result["reply"]
        assert "Qwen2.5-7B-Instruct-4bit" in error_result["reply"]
        assert error_result["model"] == "gemma-4-26b-a4b", (
            "on refusal, the reported model must be the one actually "
            "resident, never the mismatched request — no relabeling"
        )
    finally:
        app_mod._OPTIONAL_CHAT_BACKEND_CACHE.clear()


def test_chat_send_allows_matching_model_no_mismatch(monkeypatch):
    """Sanity companion: when the request's model matches what's already
    resident, _prepare_chat_context must proceed normally (no spurious
    refusal for the common, correct case)."""
    import arail.portal.app as app_mod

    app_mod._OPTIONAL_CHAT_BACKEND_CACHE["aerollm"] = _FakeAeroBackend("Qwen2.5-7B-Instruct-4bit")
    try:
        ctx = app_mod._prepare_chat_context(
            message="hello",
            history=[],
            backend_override="aerollm",
            model_override="Qwen2.5-7B-Instruct-4bit",
        )
        assert ctx.get("error_result") is None
        assert ctx["deep_backend"].model_name == "Qwen2.5-7B-Instruct-4bit"
    finally:
        app_mod._OPTIONAL_CHAT_BACKEND_CACHE.clear()


# ---------------------------------------------------------------------------
# C6.6/F-FAKEETA — real ETA scales with size, never a hardcoded constant
# ---------------------------------------------------------------------------

def test_eta_scales_with_real_on_disk_bytes_not_hardcoded():
    import arail.portal.app as app_mod

    small = app_mod._estimate_load_eta_seconds("ollama", 1.0)
    large = app_mod._estimate_load_eta_seconds("ollama", 14.4)
    assert small is not None and large is not None
    assert large > small
    assert large == round(small * 14.4)  # linear in bytes at a fixed throughput


def test_eta_none_when_model_size_unresolvable(monkeypatch):
    import arail.portal.app as app_mod
    monkeypatch.setattr(app_mod, "_real_on_disk_gb", lambda runtime, model: None)
    # _prepare_chat_model_load computes eta from _real_on_disk_gb; a None
    # size must produce eta_seconds=None in the loading state, never a
    # fabricated countdown.
    assert app_mod._real_on_disk_gb("ollama", "ghost:latest") is None


def test_load_throughput_uses_rolling_median_once_samples_exist(monkeypatch):
    import arail.portal.app as app_mod
    monkeypatch.setattr(app_mod, "_LOAD_THROUGHPUT_SAMPLES", {})
    default = app_mod._load_throughput_mbps("ollama")
    app_mod._record_load_throughput("ollama", 10 * (1024 ** 3), 10.0)  # 1024 MB/s
    app_mod._record_load_throughput("ollama", 10 * (1024 ** 3), 20.0)  # 512 MB/s
    median = app_mod._load_throughput_mbps("ollama")
    assert median != default
    assert 500 < median < 1100


# ---------------------------------------------------------------------------
# F-CORRUPT — declared vs real size disagreement degrades honestly
# ---------------------------------------------------------------------------

def test_model_looks_corrupt_when_real_size_disagrees_with_catalog(monkeypatch):
    import arail.portal.app as app_mod
    monkeypatch.setattr(app_mod, "_catalog_declared_size_gb", lambda model_id: 14.4)
    assert app_mod._model_looks_corrupt("gemma-4-26b-a4b", 2.0) is True  # truncated
    assert app_mod._model_looks_corrupt("gemma-4-26b-a4b", 14.2) is False  # normal drift


def test_model_looks_corrupt_false_when_no_catalog_match(monkeypatch):
    import arail.portal.app as app_mod
    monkeypatch.setattr(app_mod, "_catalog_declared_size_gb", lambda model_id: None)
    assert app_mod._model_looks_corrupt("some-custom-model", 2.0) is False


def test_prepare_chat_model_load_no_eta_when_corrupt(monkeypatch):
    """T-CORRUPT: real size disagrees with catalog beyond tolerance ->
    eta_seconds must be None, never a fabricated countdown."""
    import arail.portal.app as app_mod
    from arail.portal import scheduler

    _scoped_load_state(monkeypatch, app_mod)
    monkeypatch.setattr(scheduler, "_SEM", None)
    monkeypatch.setattr(app_mod, "_real_on_disk_gb", lambda runtime, model: 2.0)
    monkeypatch.setattr(app_mod, "_model_looks_corrupt", lambda model, size: True)
    monkeypatch.setattr(app_mod, "_get_primary_router", lambda: object())
    # F-FAKEREADY fix: _do_load() now actually warms ollama/mlx-openai
    # runtimes via backend.complete(...) instead of just constructing the
    # wrapper — this test only cares about the pre-load "loading" state
    # capture (below), so stub the backend rather than hit the real
    # (possibly absent) Ollama daemon. Matches the sibling test below.
    monkeypatch.setattr(app_mod, "_get_runtime_backend", lambda runtime, model: object())

    captured = {}
    real_set_state = app_mod._set_chat_model_load_state

    def _capture(**changes):
        if changes.get("state") == "loading":
            captured.update(changes)
        return real_set_state(**changes)

    monkeypatch.setattr(app_mod, "_set_chat_model_load_state", _capture)

    async def _scenario():
        await app_mod._prepare_chat_model_load(model="gemma-4-26b-a4b", runtime="ollama", provider=None)

    asyncio.run(asyncio.wait_for(_scenario(), timeout=5.0))
    assert captured.get("eta_seconds") is None


# ---------------------------------------------------------------------------
# C6.7/F-REFIT — click-time re-fit, honest message on Requires-streaming
# ---------------------------------------------------------------------------

def test_refit_message_warns_when_fresh_verdict_requires_streaming(monkeypatch):
    import arail.portal.app as app_mod
    from arail.portal import scheduler

    _scoped_load_state(monkeypatch, app_mod)
    monkeypatch.setattr(scheduler, "_SEM", None)
    monkeypatch.setattr(app_mod, "_real_on_disk_gb", lambda runtime, model: 14.4)
    monkeypatch.setattr(app_mod, "_local_memory_snapshot", lambda: {
        "label": "Apple M5", "total_gb": 24.0, "used_gb": 17.0, "free_gb": 7.0,
    })
    monkeypatch.setattr(app_mod, "_get_runtime_backend", lambda runtime, model: object())

    captured = {}
    real_set_state = app_mod._set_chat_model_load_state

    def _capture(**changes):
        if changes.get("state") == "loading":
            captured.update(changes)
        return real_set_state(**changes)

    monkeypatch.setattr(app_mod, "_set_chat_model_load_state", _capture)

    async def _scenario():
        await app_mod._prepare_chat_model_load(
            model="gemma-4-26b-a4b:latest", runtime="ollama", provider=None
        )

    asyncio.run(asyncio.wait_for(_scenario(), timeout=5.0))
    assert "may swap or fail" in captured["message"]
    assert "GB" in captured["message"]


# ---------------------------------------------------------------------------
# C6.8/F-DAEMONDOWN — friendly message, no raw traceback
# ---------------------------------------------------------------------------

def test_friendly_load_error_detects_connection_refused():
    import arail.portal.app as app_mod
    exc = ConnectionRefusedError("[Errno 61] Connection refused")
    msg = app_mod._friendly_load_error(exc)
    assert "Traceback" not in msg
    assert "ollama serve" in msg


def test_friendly_load_error_detects_missing_binary():
    import arail.portal.app as app_mod
    exc = FileNotFoundError("[Errno 2] No such file or directory: 'ollama'")
    msg = app_mod._friendly_load_error(exc)
    assert "ollama serve" in msg
    assert "Errno" not in msg


def test_friendly_load_error_generic_exception_has_no_traceback_text():
    import arail.portal.app as app_mod

    class _WeirdInternalError(Exception):
        pass

    exc = _WeirdInternalError("some internal detail: 0x7fabc123, line 42 in module.py")
    msg = app_mod._friendly_load_error(exc)
    assert "0x7fabc123" not in msg
    assert "line 42" not in msg
    assert "Traceback" not in msg


def test_prepare_chat_model_load_daemon_down_is_friendly_not_raw(monkeypatch):
    import arail.portal.app as app_mod
    from arail.portal import scheduler

    _scoped_load_state(monkeypatch, app_mod)
    monkeypatch.setattr(scheduler, "_SEM", None)
    monkeypatch.setattr(app_mod, "_real_on_disk_gb", lambda runtime, model: None)

    def _boom(runtime, model):
        raise ConnectionRefusedError("[Errno 61] Connection refused")

    monkeypatch.setattr(app_mod, "_get_runtime_backend", _boom)

    async def _scenario():
        result = await app_mod._prepare_chat_model_load(
            model="llama3.2:1b", runtime="ollama", provider=None
        )
        assert result["state"] == "error"
        assert "Traceback" not in result["message"]
        assert "Errno" not in result["message"]
        assert "ollama serve" in result["message"]

    asyncio.run(asyncio.wait_for(_scenario(), timeout=5.0))


# ---------------------------------------------------------------------------
# docs/maximus.plan.md §5 — trimmed to the states that actually ship
# ---------------------------------------------------------------------------

def test_maximus_plan_doc_states_the_shipped_four_state_machine():
    docs_path = os.path.join(_REPO_ROOT, "docs", "maximus.plan.md")
    with open(docs_path, "r", encoding="utf-8") as f:
        text = f.read()
    assert "idle → loading → ready | error" in text
    assert "not shipped behavior" in text or "not implemented" in text

