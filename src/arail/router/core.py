"""ModelRouter — single entry-point for all inference backends."""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Iterator, Optional

from arail import agent_context, agent_trace
from arail.router.backends import (BACKEND_MAP, BaseBackend, ModelResponse,
                                   StreamResult)
from arail.costs import cost_tracker, current_recap_depth

# Backends whose inference calls leave this machine. Constructing one of
# these while airgapped is refused up front — the httpx/requests egress
# guard would block the eventual API call anyway, but failing here gives
# an immediate, non-network, user-readable error.
_CLOUD_BACKENDS = frozenset({"claude", "huggingface", "openrouter"})


class CloudBackendBlocked(RuntimeError):
    """A cloud backend was requested while the lab is airgapped."""


def _is_importable(module: str) -> bool:
    """Can this module actually be imported, right now, in this process?

    Deliberately a real import rather than ``find_spec``. A spec that
    cannot execute is precisely the case that matters here: on a Mac
    with mlx-lm on disk but a broken dependency chain, ``find_spec``
    says yes and the import then raises — so detection would "verify"
    MLX and hand back a backend whose constructor fails.

    The import is not wasted work: the only reason to ask is that we are
    about to construct this backend, which imports it anyway.
    """
    import importlib
    try:
        importlib.import_module(module)
        return True
    except Exception:
        # Any failure — missing, half-installed, incompatible pair, a
        # poisoned dependency — means "cannot use this backend", which is
        # the only question being asked.
        return False


def _configured_backend() -> str:
    """The lab's configured backend, or "auto" to detect.

    Read lazily rather than captured at import: ``arail.config`` computes
    MODEL_BACKEND from the env at *its* import time, and callers
    (including tests) reload it. Binding the value here would freeze
    whichever env happened to be live first.

    This exists because the fallback used to be the literal ``"mlx"``.
    That skipped ``config.MODEL_BACKEND`` entirely, so whenever
    ``MODEL_BACKEND`` was absent from the environment the router built an
    MLX backend on machines that had no MLX — ignoring the project's own
    default of ``"auto"`` — and raised "MLX not installed" instead of
    detecting what the box could actually run.
    """
    try:
        from arail import config
        return getattr(config, "MODEL_BACKEND", "auto") or "auto"
    except Exception:  # pragma: no cover - config should always import
        return "auto"


def _check_cloud_allowed(name: str) -> None:
    if name in _CLOUD_BACKENDS:
        from arail.airgap import is_airgapped
        if is_airgapped():
            raise CloudBackendBlocked(
                f"The lab is airgapped — the cloud backend '{name}' is "
                "blocked. Click the Airgapped pill in the status bar (or set "
                "LAB_MODE=hybrid in .env) to allow cloud providers, or pick "
                "a local backend."
            )


class ModelRouter:
    """Instantiate the correct backend based on env / config and expose a
    uniform ``complete()`` interface."""

    def __init__(self, backend: str | None = None,
                 *, billing_source: str = "agent") -> None:
        name = (backend or os.getenv("MODEL_BACKEND")
                or _configured_backend()).lower()
        if name == "auto":
            name = self._auto_detect()
        if name not in BACKEND_MAP:
            raise ValueError(
                f"Unknown backend '{name}'. "
                f"Choose from: {', '.join(BACKEND_MAP)}"
            )
        _check_cloud_allowed(name)
        self.backend_name = name
        self.billing_source = billing_source
        self._backend: BaseBackend = BACKEND_MAP[name]()

    # ------------------------------------------------------------------
    @classmethod
    def from_backend(cls, backend: BaseBackend, name: str, *,
                     billing_source: str = "agent") -> "ModelRouter":
        """Wrap an already-constructed backend in a router.

        Used by the model registry (arail.registry) so registry-built
        backends keep flowing through cost_tracker like every other call.
        Optional attributes ``provider`` / ``entry_id`` / ``tab`` may be set
        on the returned router by the caller; complete()/stream_complete()
        forward them to cost tracking when present.
        """
        self = cls.__new__(cls)
        self.backend_name = name
        self.billing_source = billing_source
        self._backend = backend
        return self

    # ------------------------------------------------------------------
    @staticmethod
    def _auto_detect() -> str:
        """Best-effort platform detection (mirrors setup.sh logic).

        Detection asks two questions per candidate, not one: is this the
        right *hardware*, and is the runtime for it actually importable.
        Apple Silicon without mlx-lm installed is a real configuration —
        the minimalist tier does not install it — and answering "mlx"
        there produces a hard ImportError from a code path whose whole
        job is to pick something that works.
        """
        import platform
        import shutil

        if platform.system() == "Darwin" and platform.machine() == "arm64":
            if _is_importable("mlx_lm"):
                return "mlx"
            # Apple Silicon, but MLX cannot actually be loaded: Ollama is
            # what setup installs by default and what the rest of the lab
            # assumes.
            return "ollama_native"
        if shutil.which("nvidia-smi"):
            return "cuda"
        return "cpu"

    # ------------------------------------------------------------------
    # Attribution + trace chokepoint helpers (sprint 2026-09-20
    # buddy-front-and-center, ARCHITECTURE.md interface contract #3).
    # ------------------------------------------------------------------

    def _billing_source(self, ctx: Optional[agent_context.AgentCall]) -> str:
        """Chat keeps its bucket regardless of any context that happens to
        be active (F19) — everyone else gets per-agent/system/unattributed
        attribution (W2)."""
        if self.billing_source == "ui":
            return "ui"
        return agent_context.billing_source(ctx)

    @staticmethod
    def _slot_info() -> dict:
        # D6 (REVIEW.md): the import must be inside the try too -- this is
        # called from contexts that are not the portal (the goal-parser
        # subprocess, lab/tools/model_router.py, CLI paths), and
        # arail.portal is a namespace package (no __init__.py) an
        # ImportError there must not escape into an inference the same way
        # a slot_pressure() failure already can't.
        try:
            from arail.portal import scheduler as _inference_scheduler
            slot = _inference_scheduler.slot_pressure()
        except Exception:  # noqa: BLE001 - observability must never break inference
            return {"capacity": None, "in_flight": None, "pending": None,
                    "held_by_other": None}
        slot = dict(slot)
        slot["held_by_other"] = bool((slot.get("in_flight") or 0) > 0)
        return slot

    @staticmethod
    def _halted() -> Optional[bool]:
        try:
            from arail import scheduler as _job_scheduler
            return _job_scheduler.jobs_halted()
        except Exception:  # noqa: BLE001
            return None

    def _record(self, ctx: Optional[agent_context.AgentCall], **fields: Any) -> None:
        """Exactly one ``agent_trace.record()`` per chokepoint call — success,
        failure, or refusal alike. Skipped only when the active context is
        marked ``out_of_process`` (contract #9: the parent authors that
        trace, not the child)."""
        if ctx is not None and ctx.out_of_process:
            return
        if ctx is not None:
            base = {
                "trace_id": ctx.trace_id,
                "agent_id": ctx.agent_id,
                "parent_agent_id": ctx.parent_agent_id,
                "kind": ctx.kind,
                "label": ctx.label,
                "call_site": None,
                "brain": ctx.brain,
                "effort": ctx.effort,
                "foreground": ctx.foreground,
                "deep_reason_code": ctx.deep_reason_code,
                "deep_reason_detail": ctx.deep_reason_detail,
                "out_of_process": ctx.out_of_process,
                "attribution": agent_context.billing_source(ctx),
            }
        else:
            base = {
                "trace_id": None,
                "agent_id": None,
                "parent_agent_id": None,
                "kind": None,
                "label": None,
                "call_site": agent_context.call_site(depth=3),
                "brain": None,
                "effort": None,
                "foreground": None,
                "deep_reason_code": None,
                "deep_reason_detail": None,
                "out_of_process": False,
                "attribution": "unattributed",
            }
        base["halted"] = self._halted()
        base.update(fields)
        agent_trace.record(**base)

    # ------------------------------------------------------------------
    def complete(self, prompt: str, max_tokens: int = 512,
                 temperature: float = 0.7,
                 top_p: Optional[float] = None,
                 *, system: Optional[str] = None,
                 messages: Optional[list] = None) -> ModelResponse:
        ctx = agent_context.current()
        slot = self._slot_info()
        # F10 (latching): read the recorder flag ONCE at call start and use
        # that value for this call's whole duration. off->on mid-call does
        # NOT start capturing; on->off mid-call DOES still capture — the
        # safer half, since turning it on never retroactively captures
        # in-flight work.
        rec_bodies_at_start = agent_trace.recorder_on()

        try:
            agent_context.halt_gate(ctx)
        except agent_context.AgentHeldError:
            self._record(ctx, slot=slot, streamed=False,
                         outcome="refused_halted",
                         ttft_ms=None, ttft_status="non_streaming")
            raise

        agent_context.note_agent_call_entered(ctx)
        try:
            response = self._backend.complete(
                prompt, max_tokens, temperature, top_p=top_p,
                system=system, messages=messages,
            )
        except Exception as exc:
            self._record(ctx, slot=slot, streamed=False,
                         outcome="error", error_class=type(exc).__name__)
            raise
        finally:
            agent_context.note_agent_call_exited(ctx)

        # Track cost — estimate input tokens from prompt + frozen prefix length
        tokens_in = max((len(prompt) + len(system or "")) // 4, 1)
        cost_tracker.track(
            backend=response.backend,
            model=response.model,
            tokens_in=tokens_in,
            tokens_out=response.tokens_used,
            latency_ms=response.latency_ms,
            source=self._billing_source(ctx),
            recap_depth=current_recap_depth(),
            cache_read_input_tokens=response.cache_read_input_tokens,
            cache_creation_input_tokens=response.cache_creation_input_tokens,
            provider=getattr(self, "provider", None),
            entry_id=getattr(self, "entry_id", None),
            tab=getattr(self, "tab", None),
        )
        bodies = None
        if rec_bodies_at_start:
            # QA F4/F5 (TEST_REPORT.md): both the import and the call sit
            # inside the same try -- a broken/shadowed redact module, or
            # capture_body itself raising, must never raise into an
            # inference that has already produced its answer (D6's
            # posture). capture_body is documented fail-closed on its
            # own, but the chokepoint does not borrow that guarantee
            # from a leaf module's internal discipline; it owns it here.
            try:
                from arail import redact
                bodies = redact.capture_body(prompt, response.text)
            except Exception:  # noqa: BLE001 - observability must never break inference
                bodies = None
        self._record(
            ctx, slot=slot, streamed=False, outcome="ok",
            model=response.model, backend=response.backend,
            provider=getattr(self, "provider", None),
            entry_id=getattr(self, "entry_id", None),
            tokens_in=tokens_in, tokens_out=response.tokens_used,
            latency_ms=response.latency_ms,
            ttft_ms=None, ttft_status="non_streaming",
            bodies=bodies,
        )
        return response

    def stream_complete(self, prompt: str, max_tokens: int = 512,
                        temperature: float = 0.7,
                        top_p: Optional[float] = None,
                        *, system: Optional[str] = None,
                        messages: Optional[list] = None) -> Iterator[StreamResult]:
        ctx = agent_context.current()
        slot = self._slot_info()
        tokens_in = max((len(prompt) + len(system or "")) // 4, 1)
        # F10 (latching) — see complete()'s identical comment.
        rec_bodies_at_start = agent_trace.recorder_on()
        full_text_parts: list[str] = []

        # TTFT honesty contract (ARCHITECTURE.md interface contract #3):
        # measured from perf_counter() taken here, *before* the generator
        # is entered — never derived from latency_ms. Determined by the
        # FIRST item's shape and, failing that, the first non-empty string
        # seen at any point; never overwritten once set except by the one
        # documented "no error yet" -> "error" transition below.
        t_enter = time.perf_counter()
        ttft_ms: Optional[float] = None
        ttft_status: Optional[str] = None
        is_first_item = True

        try:
            agent_context.halt_gate(ctx)
        except agent_context.AgentHeldError:
            self._record(ctx, slot=slot, streamed=True,
                         outcome="refused_halted",
                         ttft_ms=None, ttft_status=None)
            raise

        agent_context.note_agent_call_entered(ctx)
        try:
            for item in self._backend.stream_complete(
                prompt,
                max_tokens,
                temperature,
                top_p=top_p,
                system=system,
                messages=messages,
            ):
                if is_first_item:
                    is_first_item = False
                    if isinstance(item, ModelResponse):
                        # The backend emulated the stream — no real
                        # first-token signal exists to measure.
                        ttft_status = "emulated_stream"
                if isinstance(item, str) and item:
                    full_text_parts.append(item)
                if (ttft_status is None and isinstance(item, str) and item):
                    ttft_ms = (time.perf_counter() - t_enter) * 1000.0
                    ttft_status = "measured"

                if isinstance(item, ModelResponse):
                    if ttft_status is None:
                        # Every item so far was an empty string; the
                        # generator never produced a real token before its
                        # terminal response.
                        ttft_status = "no_tokens"
                    prefill_ms = getattr(item, "prefill_ms", None)
                    cost_tracker.track(
                        backend=item.backend,
                        model=item.model,
                        tokens_in=tokens_in,
                        tokens_out=item.tokens_used,
                        latency_ms=item.latency_ms,
                        source=self._billing_source(ctx),
                        cache_read_input_tokens=item.cache_read_input_tokens,
                        cache_creation_input_tokens=item.cache_creation_input_tokens,
                        provider=getattr(self, "provider", None),
                        entry_id=getattr(self, "entry_id", None),
                        tab=getattr(self, "tab", None),
                    )
                    bodies = None
                    if rec_bodies_at_start:
                        # QA F4/F5 -- same guard as complete()'s identical
                        # comment: the import and the call share one try,
                        # so this can never raise into a stream that has
                        # already yielded real tokens.
                        try:
                            from arail import redact
                            response_text = getattr(item, "text", None) or "".join(full_text_parts)
                            bodies = redact.capture_body(prompt, response_text)
                        except Exception:  # noqa: BLE001 - observability must never break inference
                            bodies = None
                    self._record(
                        ctx, slot=slot, streamed=True, outcome="ok",
                        model=item.model, backend=item.backend,
                        provider=getattr(self, "provider", None),
                        entry_id=getattr(self, "entry_id", None),
                        tokens_in=tokens_in, tokens_out=item.tokens_used,
                        latency_ms=item.latency_ms,
                        ttft_ms=ttft_ms, ttft_status=ttft_status,
                        prefill_ms=prefill_ms,
                        prefill_source="server_reported" if prefill_ms is not None else None,
                        bodies=bodies,
                    )
                yield item
        except Exception as exc:
            # A TTFT already measured before the failure is real and kept;
            # only "nothing determined yet" becomes "error" — an error
            # after real tokens arrived doesn't retroactively erase them.
            if ttft_status is None:
                ttft_status = "error"
                ttft_ms = None
            self._record(ctx, slot=slot, streamed=True,
                         outcome="error", error_class=type(exc).__name__,
                         ttft_ms=ttft_ms, ttft_status=ttft_status)
            raise
        finally:
            agent_context.note_agent_call_exited(ctx)

    def health_check(self) -> Dict[str, bool]:
        return {self.backend_name: self._backend.health_check()}

    def switch_backend(self, name: str) -> None:
        if name not in BACKEND_MAP:
            raise ValueError(f"Unknown backend: {name}")
        _check_cloud_allowed(name)
        self.backend_name = name
        self._backend = BACKEND_MAP[name]()
