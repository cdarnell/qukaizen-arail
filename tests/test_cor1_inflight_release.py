"""COR-1 — a synchronous raise inside `_prepare_chat_model_load`'s setup
span (between acquiring `_CHAT_MODEL_LOAD_INFLIGHT` and registering the
done-callback that releases it) must not leak the inflight lock.

Sprint: 2026-08-11-two-slot-chat-models
Follow-up from: sprints/2026-07-20-model-ux-unification/REVIEW.md COR-1
(dated 2026-08-10, never actioned).

Failure mode: before the fix, `_real_on_disk_gb` (or any other call in
the "compute fit/eta, set loading state" span) raising synchronously
meant `task.add_done_callback(_release_inflight_once)` was never
reached, so `_CHAT_MODEL_LOAD_INFLIGHT` stayed held forever — every
subsequent `/api/chat/model-load` call would be refused with "a load
is already in progress" until the portal restarted.
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


def test_synchronous_raise_before_task_creation_releases_the_inflight_lock(monkeypatch):
    import arail.portal.app as app_mod
    from arail.portal import scheduler

    _scoped_load_state(monkeypatch, app_mod)
    monkeypatch.setattr(scheduler, "_SEM", None)

    def _boom(runtime, model):
        raise RuntimeError("disk read blew up")

    monkeypatch.setattr(app_mod, "_real_on_disk_gb", _boom)

    async def _scenario():
        raised = False
        try:
            await app_mod._prepare_chat_model_load(model="llama-ai-eng", runtime="ollama", provider=None)
        except RuntimeError as exc:
            raised = True
            assert "disk read blew up" in str(exc)
        assert raised, "the original exception must still propagate — COR-1 fixes the leak, not the failure"

        # The bug: this lock stayed held forever because the done-callback
        # that releases it was never registered before the raise.
        assert not app_mod._CHAT_MODEL_LOAD_INFLIGHT.locked(), (
            "inflight lock leaked after a synchronous raise in the load setup span"
        )

    asyncio.run(asyncio.wait_for(_scenario(), timeout=5.0))


def test_a_second_load_is_not_refused_after_the_first_ones_setup_raised(monkeypatch):
    """The user-visible symptom: without the fix, every load after the
    first crash is refused with 'a load is already in progress' even
    though nothing is actually loading."""
    import arail.portal.app as app_mod
    from arail.portal import scheduler

    _scoped_load_state(monkeypatch, app_mod)
    monkeypatch.setattr(scheduler, "_SEM", None)
    monkeypatch.setattr(
        app_mod, "_real_on_disk_gb",
        lambda runtime, model: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    async def _scenario():
        try:
            await app_mod._prepare_chat_model_load(model="llama-ai-eng", runtime="ollama", provider=None)
        except RuntimeError:
            pass

        # Second attempt after the crash: must proceed past the inflight
        # check, not be refused with "a load is already in progress".
        monkeypatch.setattr(app_mod, "_real_on_disk_gb", lambda runtime, model: None)
        monkeypatch.setattr(app_mod, "_model_looks_corrupt", lambda model, size: False)
        monkeypatch.setattr(app_mod, "_get_runtime_backend", lambda runtime, model: object())

        result = await app_mod._prepare_chat_model_load(model="llama-ai-eng", runtime="ollama", provider=None)
        assert result.get("message") != "a load is already in progress"

    asyncio.run(asyncio.wait_for(_scenario(), timeout=5.0))
