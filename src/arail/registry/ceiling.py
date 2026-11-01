"""The answering-model parameter ceiling — single chokepoint.

Phase 1 review (sprints/2026-08-04-model-inference-hardening/ — see
CLAUDE.md context) found model identity resolved independently across six
layers, several of which could silently land on a model >= 8B as the
*primary* answering model: an unvalidated send-path override, a
first-installed-model fallback, a first-GGUF-on-disk scan, and a name-regex
param parser that treats anything it can't parse as "small".

This module is the fix: one function every primary-model resolution must
call before a backend is allowed to answer with it. No call site is allowed
to re-implement this check — grep for ``resolve_answering_model`` before
adding a new one.

Rules (set by the operator 2026-08-04, see the plan file):
  - PRIMARY answering model: total params must be *known* and < 8B.
    Unknown params refuse — never default to "small". No escalation flag,
    no override, no fallback exception.
  - SECONDARY (AeroLLM / AirLLM, the "2nd inference"): capped by what the
    discovered hardware can hold stably (arail.hardware.secondary_model_cap_b),
    not by the 8B ceiling. Unknown params refuse here too — a cap you can't
    verify against isn't a cap.
  - Cloud compute sources are exempt (this gate is about local memory
    stability, not model capability).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from arail import hardware as _hardware
from arail import model_specs as _model_specs

PRIMARY_CEILING_B = 8.0  # strict: params must be < 8.0, not <=

Role = Literal["primary", "secondary"]


class ModelCeilingViolation(Exception):
    """Raised when a model may not serve in the requested role."""

    def __init__(self, message: str, *, model_id: str, role: Role):
        super().__init__(message)
        self.model_id = model_id
        self.role = role


@dataclass
class ModelProvenance:
    """What actually answered, and how ARAIL knows its size.

    Attach this to every chat reply (stream final, non-stream, OpenAI shim)
    so a served model can never be silently different from what the UI
    displays — the review's other headline finding.
    """

    model_id: str
    params_b: Optional[float]
    param_source: str  # "metadata" | "override" | "name-regex" | "unknown"
    role: Role
    backend: str


def resolve_answering_model(
    model_id: str,
    *,
    role: Role,
    backend: str,
    model_path: Optional[str] = None,
) -> ModelProvenance:
    """The chokepoint. Raises ModelCeilingViolation if `model_id` may not
    serve as the answering model in `role`; otherwise returns provenance
    the caller must propagate to the response.

    `model_path`, when given, points at the actual weights file/dir on disk
    so params can be read from metadata rather than guessed from the name —
    this is what closes the opaque-GGUF and first-file-on-disk bypasses.
    """
    if not model_id:
        raise ModelCeilingViolation(
            "No model specified.", model_id=model_id or "", role=role
        )

    params_b, source = _model_specs.resolve_params_b(model_id, model_path)

    if role == "primary":
        if params_b is None:
            raise ModelCeilingViolation(
                f"'{model_id}' has an unknown parameter count. The primary "
                f"answering model must be verifiably under "
                f"{PRIMARY_CEILING_B:g}B params — an unreadable size refuses, "
                f"it doesn't default to small. Use a catalog model, or a "
                f"file ARAIL can read metadata from.",
                model_id=model_id,
                role=role,
            )
        if params_b >= PRIMARY_CEILING_B:
            raise ModelCeilingViolation(
                f"'{model_id}' is ~{params_b:g}B params — at or over the "
                f"{PRIMARY_CEILING_B:g}B primary ceiling. It cannot serve as "
                f"the answering model. Use it as the QueueLLM secondary "
                f"instead, or pick a smaller primary.",
                model_id=model_id,
                role=role,
            )
        return ModelProvenance(model_id, params_b, source, role, backend)

    # role == "secondary"
    cap = _hardware.secondary_model_cap_b()
    if params_b is None:
        raise ModelCeilingViolation(
            f"'{model_id}' has an unknown parameter count — can't verify it "
            f"fits this machine's secondary-model budget (~{cap:g}B on the "
            f"discovered hardware). Use a catalog model, or a file ARAIL "
            f"can read metadata from.",
            model_id=model_id,
            role=role,
        )
    if params_b > cap:
        raise ModelCeilingViolation(
            f"'{model_id}' is ~{params_b:g}B params — over the ~{cap:g}B "
            f"this machine's discovered RAM can hold stably as a resident "
            f"secondary model. Pick a smaller QueueLLM/AirLLM model, or run "
            f"this one on hardware with more memory.",
            model_id=model_id,
            role=role,
        )
    return ModelProvenance(model_id, params_b, source, role, backend)
