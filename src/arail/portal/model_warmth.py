"""Background model warmth — deep work feels instant, the resident model
stays resident.

Two independent loops:

  aerollm_preload_loop  — Tier-1 (aeroLLM) preload. The deep model
      historically loaded lazily on the first agent call, so the first
      deep request after every portal (re)start paid a multi-GB weight
      load. This loop preloads it in the background whenever it is SAFE
      to do so, using the exact same gate agents already obey:

          deep_policy.background_safe()  — not halted, not in the active
          window, operator absent (runtime profile), profile allows
          background aerollm, Metal memory pressure < 0.60.

      The gate is re-checked *after* acquiring the inference slot
      (presence may have arrived while queued). Construction goes through
      ``deep_policy.get_deep_router()`` — the single owner of the
      resident runtime — so there is never a second copy. Kill switch:
      ``ARAIL_AEROLLM_PRELOAD=0``. Interval:
      ``ARAIL_AEROLLM_PRELOAD_INTERVAL_SEC`` (default 300).

      Note ``get_deep_router()`` latches failures (_FAILED): if
      construction fails once, this loop emits one loud warning and
      stands down instead of thrashing a broken wheel/model every tick.

  tier0_keepwatch_loop  — Tier-0 (resident) keep-watch (sprints/2026-08-
      11-two-slot-chat-models Part 3). Ollama's keep_alive TTL (2h
      default, or pinned to -1 when ARAIL_RESIDENT_PIN=1 and this IS the
      tier0 model — see OllamaNativeBackend._keep_alive) still lapses
      for any OTHER reason a model went cold (an explicit `ollama stop`
      from outside the lab, a daemon restart) — this loop notices and
      re-warms with the same 1-token completion `_warm_primary_router()`
      uses at boot, so "the resident model is always in the GPU" stays
      true between chat turns, not just at boot. Ollama-only (no live
      residency probe exists for mlx/cpu/cuda). Kill switch:
      ``ARAIL_TIER0_KEEPWATCH=0``. Interval:
      ``ARAIL_TIER0_KEEPWATCH_INTERVAL_SEC`` (default 120, floor 30).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


def _enabled() -> bool:
    return os.getenv("ARAIL_AEROLLM_PRELOAD", "1").strip().lower() \
        not in ("0", "false", "no")


def _interval() -> float:
    try:
        return max(float(os.getenv("ARAIL_AEROLLM_PRELOAD_INTERVAL_SEC", "300")), 30.0)
    except ValueError:
        return 300.0


def _tier1_resident() -> bool:
    """Inspect the shared runtime WITHOUT constructing anything (R5)."""
    try:
        from arail.router.backends import AeroLLMBackend
        shared = getattr(AeroLLMBackend, "_shared", None) or {}
        return any(getattr(inst, "_runtime", None) is not None
                   for inst in shared.values())
    except Exception:  # noqa: BLE001
        return False


async def _preload_once() -> str:
    """One preload attempt. Returns a status string (for tests/logging)."""
    from arail.agents import deep_policy
    from arail.portal import scheduler
    from arail.registry import health as reg_health
    from arail.registry.store import TIER1_ID

    if _tier1_resident():
        return "already_resident"
    # prefer_deep(foreground=False) = maximus tier + wheel importable +
    # background_safe() (halt / window / presence / profile / pressure<0.60).
    if not deep_policy.prefer_deep(foreground=False):
        return "not_safe"

    reg_health.mark_warming(TIER1_ID)
    try:
        async with scheduler.inference_slot("aerollm-preload"):
            # Presence may have arrived while we queued behind a chat turn —
            # re-check before committing to a multi-GB synchronous load.
            if not deep_policy.background_safe():
                return "not_safe_after_wait"
            router = await asyncio.to_thread(deep_policy.get_deep_router)
    finally:
        reg_health.clear_warming(TIER1_ID)

    try:
        from arail.activity import activity_log
        from arail.registry import get_registry
        reg = get_registry()
        reg._ensure_loaded()
        entry = reg.entries.get(TIER1_ID)
        if entry is not None:
            entry.health = reg_health.probe_entry(entry)
        if router is not None:
            activity_log.emit(
                "registry",
                "Deep model preloaded — Tier 1 is resident and ready.",
                "success",
                {"model_event": {"kind": "preload_ok", "entry_id": TIER1_ID}})
            return "loaded"
        activity_log.emit(
            "registry",
            "Deep model preload failed (aeroLLM unavailable) — deep calls "
            "will fall back visibly. Standing down until restart.",
            "warn",
            {"model_event": {"kind": "preload_failed", "entry_id": TIER1_ID}})
        return "failed"
    except Exception:  # noqa: BLE001
        return "failed"


async def aerollm_preload_loop() -> None:
    """Fire-and-forget startup task. Never raises."""
    if not _enabled():
        return
    announced_skip = False
    while True:
        try:
            status = await _preload_once()
            if status in ("loaded", "failed", "already_resident"):
                if status == "failed":
                    return   # _FAILED latched — stand down, stay honest
                # Resident (either way): drop to a slow keep-watch so a
                # future manual unload can re-trigger a warm.
            elif status.startswith("not_safe") and not announced_skip:
                announced_skip = True
                try:
                    from arail.activity import activity_log
                    activity_log.emit(
                        "registry",
                        "Deep-model preload waiting for a safe window "
                        "(operator absent + memory headroom).", "info")
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            log.warning("aerollm preload tick failed: %s", exc)
        await asyncio.sleep(_interval())


# ---------------------------------------------------------------------------
# Tier-0 (resident) keep-watch — sprints/2026-08-11-two-slot-chat-models Part 3
# ---------------------------------------------------------------------------

def _tier0_keepwatch_enabled() -> bool:
    return os.getenv("ARAIL_TIER0_KEEPWATCH", "1").strip().lower() \
        not in ("0", "false", "no")


def _tier0_keepwatch_interval() -> float:
    try:
        return max(float(os.getenv("ARAIL_TIER0_KEEPWATCH_INTERVAL_SEC", "120")), 30.0)
    except ValueError:
        return 120.0


# Process-wide, like _tier1_resident's process-wide inspection above — set
# by the eject endpoint (app.py) right after a successful `ollama stop` of
# the tier0 model, read here so the loop doesn't immediately fight an
# operator's deliberate eject.
_SUPPRESS_TIER0_KEEPWATCH_UNTIL = 0.0


def suppress_tier0_keepwatch(seconds: float = 90.0) -> None:
    global _SUPPRESS_TIER0_KEEPWATCH_UNTIL
    _SUPPRESS_TIER0_KEEPWATCH_UNTIL = time.monotonic() + max(seconds, 0.0)


def _tier0_keepwatch_suppressed() -> bool:
    return time.monotonic() < _SUPPRESS_TIER0_KEEPWATCH_UNTIL


async def _tier0_keepwatch_tick() -> str:
    """One keep-watch check. Returns a status string (tests/logging)."""
    from arail.registry import get_registry
    from arail.registry.store import TIER0_ID
    from arail.registry import health as reg_health

    reg = get_registry()
    reg._ensure_loaded()
    entry = reg.entries.get(TIER0_ID)
    if entry is None:
        return "no_tier0_entry"
    if entry.backend != "ollama_native":
        # No live residency probe exists for mlx/cpu/cuda — an honest
        # "don't know" skip, not a guess (matches _build_local_model_entry's
        # `warm` convention for non-Ollama rows).
        return "skip_non_ollama"

    from arail.portal import app as app_mod
    from arail.portal import scheduler

    warm_ids = app_mod._ollama_ps_resident_ids()
    warm_candidates = {entry.model_id, f"{entry.model_id}:latest"}
    if warm_candidates & warm_ids:
        return "already_resident"

    if _tier0_keepwatch_suppressed():
        return "suppressed_after_eject"
    if app_mod._CHAT_MODEL_LOAD_INFLIGHT.locked():
        return "skip_load_inflight"  # never fight a user-initiated load

    snapshot = app_mod._local_memory_snapshot()
    free_gb = float(snapshot.get("free_gb") or 0.0)
    # The ≤3B slot cap keeps a rewarm cheap (~2 GB); this floor only
    # guards the pathological case of a box already critically tight.
    if free_gb and free_gb < 1.0:
        return "skip_low_memory"

    reg_health.mark_warming(TIER0_ID)
    try:
        async with scheduler.inference_slot("tier0-keepwatch"):
            # Free memory / inflight state may have changed while queued.
            if app_mod._CHAT_MODEL_LOAD_INFLIGHT.locked():
                return "skip_load_inflight_after_wait"
            backend = await asyncio.to_thread(
                app_mod._get_runtime_backend, "ollama", entry.model_id)
            # Same conditional as _prepare_chat_model_load's _do_load —
            # `think` is an ollama-native-only kwarg, not universal across
            # every backend.complete() implementation.
            warm_kwargs = (
                {"think": False}
                if getattr(backend, "backend_name", "") == "ollama:native"
                else {}
            )
            await asyncio.to_thread(
                backend.complete, "ok", 1, 0.0, 1.0, **warm_kwargs)
    except Exception as exc:  # noqa: BLE001
        return f"failed:{type(exc).__name__}"
    finally:
        reg_health.clear_warming(TIER0_ID)

    try:
        entry.health = reg_health.probe_entry(entry)
    except Exception:  # noqa: BLE001
        pass
    try:
        from arail.activity import activity_log
        activity_log.emit(
            "registry",
            "Resident model re-warmed (it had gone cold).", "info",
            {"model_event": {"kind": "keepwatch_rewarm", "entry_id": TIER0_ID}})
    except Exception:  # noqa: BLE001
        pass
    return "rewarmed"


async def tier0_keepwatch_loop() -> None:
    """Fire-and-forget startup task. Never raises. The small-model
    analogue of aerollm_preload_loop: watches the resident slot and
    re-warms it if it went cold between chat turns."""
    if not _tier0_keepwatch_enabled():
        return
    while True:
        try:
            await _tier0_keepwatch_tick()
        except Exception as exc:  # noqa: BLE001
            log.warning("tier0 keepwatch tick failed: %s", exc)
        await asyncio.sleep(_tier0_keepwatch_interval())


# ---------------------------------------------------------------------------
# One-shot boot warm of the deep slot + crash sentinel
# (sprints/2026-10-20-boot-model-autoresolve, FM5 / FM6 / FM17)
# ---------------------------------------------------------------------------
#
# Loading the deep model in-process can SIGABRT the portal (Metal assertion);
# Python cannot catch that. Before the load we leave a sentinel file naming
# the model and our pid. Success or a Python-level failure removes it. If the
# NEXT boot finds a sentinel whose pid is dead, that model crashed (or was
# killed during) its load: it is skipped -- never retried automatically --
# until the pin/model changes or the operator retries via POST
# /api/models/pin (which clears the sentinel).

_SENTINEL_NAME = ".aerollm_boot_warm.inflight"


def sentinel_path() -> Path:
    from arail.config import DATA_DIR
    return Path(DATA_DIR) / _SENTINEL_NAME


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def read_sentinel() -> Optional[dict]:
    try:
        data = json.loads(sentinel_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def write_sentinel(model: str) -> None:
    p = sentinel_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"model": model, "pid": os.getpid(),
                                 "started_at": time.time()}), encoding="utf-8")
    except OSError as exc:
        log.warning("boot-warm sentinel not written: %s", exc)


def clear_sentinel() -> None:
    try:
        sentinel_path().unlink()
    except OSError:
        pass


def crashed_model() -> Optional[str]:
    """Model named by a stale sentinel (pid dead, not us), else None."""
    sent = read_sentinel()
    if not sent or not sent.get("model"):
        return None
    pid = int(sent.get("pid") or 0)
    if pid == os.getpid() or _pid_alive(pid):
        return None
    return str(sent["model"])


def _loading_elsewhere() -> bool:
    """True iff another LIVE process is mid-load (sentinel pid alive, not us)."""
    sent = read_sentinel()
    if not sent:
        return False
    pid = int(sent.get("pid") or 0)
    return pid != os.getpid() and _pid_alive(pid)


def _boot_preload_enabled() -> bool:
    return _enabled()      # ARAIL_AEROLLM_PRELOAD=0 per-slot opt-out


async def boot_warm_deep(model: Optional[str]) -> str:
    """Load the deep model once at boot. Returns one of
    ``ok|skipped|failed|deferred|disabled`` (the /api/instance vocabulary).
    Never raises."""
    from arail.activity import activity_log
    if not model:
        return "skipped"
    if not _boot_preload_enabled():
        return "disabled"
    try:
        from arail.agents import deep_policy
        from arail.portal import scheduler
        from arail.registry import health as reg_health
        from arail.registry.store import TIER1_ID

        ok, code, detail = deep_policy.boot_warm_gate()
        if not ok:
            status = "deferred" if code == "deferred_now" else "skipped"
            if code == "disabled":
                status = "disabled"
            activity_log.emit("registry", f"Deep model not warmed at boot: {detail}.",
                              "info")
            return status
        if _tier1_resident():
            return "ok"
        if _loading_elsewhere():
            activity_log.emit(
                "registry", "Another ARAIL instance is loading the deep model "
                "-- deferring this one.", "info")
            return "deferred"

        reg_health.mark_warming(TIER1_ID)
        try:
            async with scheduler.inference_slot("aerollm-boot-warm"):
                again, _c, detail = deep_policy.boot_warm_gate()
                if not again:
                    return "deferred"
                write_sentinel(model)
                router = await asyncio.to_thread(deep_policy.get_deep_router)
        finally:
            reg_health.clear_warming(TIER1_ID)
        clear_sentinel()   # reached only if the process survived the load

        from arail.registry import get_registry
        reg = get_registry()
        reg._ensure_loaded()
        entry = reg.entries.get(TIER1_ID)
        if entry is not None:
            try:
                entry.health = reg_health.probe_entry(entry)
            except Exception:  # noqa: BLE001
                pass
        if router is None:
            activity_log.emit(
                "registry", "Deep model failed to load at boot -- deep calls "
                "will fall back visibly.", "warn",
                {"model_event": {"kind": "boot_warm_failed", "entry_id": TIER1_ID}})
            return "failed"
        activity_log.emit("registry", "Deep model loaded at boot -- Tier 1 is "
                          "resident and ready.", "success",
                          {"model_event": {"kind": "boot_warm_ok", "entry_id": TIER1_ID}})
        return "ok"
    except Exception as exc:  # noqa: BLE001
        clear_sentinel()
        log.warning("deep boot warm failed: %s", exc)
        return "failed"
