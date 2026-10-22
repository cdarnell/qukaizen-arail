"""QueueLLM as a ``qukaizen_harness.Runtime`` (ADR-0002's one dependency).

The harness pins one resident model (8-14B) and never evicts it. This adapter
reports residency *honestly*: ``loaded()`` lists a model only when the shared
``AeroLLMBackend`` really holds a live runtime for it. It never claims
residency it has not seen.

Known limits, stated rather than hidden:

* ``AeroLLMBackend.complete`` takes no grammar argument yet, so ``grammar`` is
  ignored and every completion's ``usage`` says ``grammar_enforced: False``.
  The harness still parses and gates every proposal; it just cannot rely on
  shape-constrained decoding until the engine exposes it.
* ``describe`` needs the parameter count in the model name (``...-9B-...``).
  An unnamed size raises instead of guessing, so the policy band stays honest.
* The backend is a per-process singleton keyed by model, so this adapter
  shares the portal's runtime and never loads a second copy.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any

from qukaizen_harness import Completion, ModelInfo

_PARAMS = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*[bB](?![a-zA-Z])")


def params_from_name(name: str) -> float:
    m = _PARAMS.search(name)
    if m is None:
        raise ValueError(f"cannot read a parameter count from model name {name!r}")
    return float(m.group(1))


def _default_backend() -> Any:
    from arail.router.backends import AeroLLMBackend

    return AeroLLMBackend()


class QueueLLMRuntime:
    """Implements ``qukaizen_harness.Runtime`` over ARAIL's QueueLLM backend."""

    def __init__(self, backend_factory: Callable[[], Any] = _default_backend) -> None:
        self._factory = backend_factory
        self._backend: Any = None
        self._pinned: set[str] = set()

    def describe(self, model: str) -> ModelInfo:
        return ModelInfo(model, params_from_name(model))

    def load(self, model: str, *, pin: bool = False) -> ModelInfo:
        if self._backend is None:
            self._backend = self._factory()
        have = str(getattr(self._backend, "model_name", ""))
        if have != model:
            raise RuntimeError(
                f"QueueLLM holds {have!r}, not {model!r}; set AEROLLM_MODEL={model} "
                "(this adapter never swaps the resident)"
            )
        if pin:
            self._pinned.add(model)
        return ModelInfo(model, params_from_name(model), resident=model in self._pinned)

    def loaded(self) -> Sequence[ModelInfo]:
        b = self._backend
        if b is None or getattr(b, "_runtime", None) is None:
            return []
        name = str(b.model_name)
        return [ModelInfo(name, params_from_name(name), resident=name in self._pinned)]

    def complete(
        self,
        model: str,
        prompt: str,
        *,
        grammar: str | None = None,
        max_tokens: int = 512,
        static_prefix_tokens: int = 0,
    ) -> Completion:
        if self._backend is None or str(self._backend.model_name) != model:
            raise RuntimeError(f"{model!r} is not the loaded resident; call load() first")
        r = self._backend.complete(prompt, max_tokens=max_tokens, temperature=0.0)
        return Completion(
            r.text,
            usage={
                "tokens_approx": r.tokens_used,
                "latency_ms": round(r.latency_ms, 1),
                "grammar_enforced": False,
                "grammar_requested": grammar is not None,
            },
        )
