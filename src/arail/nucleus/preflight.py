"""Memory plan + Buddy protection + capability checks (ARCHITECTURE.md §4.5).

Salvaged verbatim or near-verbatim from ``arail.build.preflight``:
``_capacity()``, ``active_params_b()``, ``_status()``, the
``Requirement``/``PreflightReport`` dataclasses, and the LoRA memory
estimator body (weights + optimizer + activations + 20%). Dropped:
Anthropic pricing, teacher amplification, remote rows — sprint-2/
gateway-shaped concerns, not this sprint's local profile.
"""

from __future__ import annotations

import math
import os
import shutil
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

from arail.nucleus.errors import RefusedByPolicy

# Bytes per parameter by precision — salvaged verbatim.
_BYTES_PER_PARAM = {"bf16": 2.0, "fp16": 2.0, "q8": 1.0, "q4": 0.55}

_RESERVE_GB = 2.0                    # fixed headroom below the OS/Metal ceiling
_STREAM_WINDOW_CAP_GB = 6.0
_STREAM_BUDGET_MARGIN_GB = 1.0
_MLX_LM_MIN = (0, 31)
_MLX_LM_MAX_EXCLUSIVE = (0, 32)


# ── salvaged dataclasses ──────────────────────────────────────────────

@dataclass
class Requirement:
    name: str
    required: str
    available: str
    status: str          # green | amber | red
    note: str = ""


@dataclass
class PreflightReport:
    rows: List[Requirement] = field(default_factory=list)
    overall: str = "green"
    budget_gb: float = 0.0
    buddy_reserve_gb: float = 0.0
    phase_requirements_gb: Dict[str, float] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def has_red(self) -> bool:
        return any(r.status == "red" for r in self.rows)


class PreflightRefusal(RefusedByPolicy):
    """Raised on any red memory row; cli.py maps this to exit 3."""

    def __init__(self, rows, phase, needed_gb, budget_gb, drop, protected):
        self.rows = rows
        self.phase = phase
        self.needed_gb = needed_gb
        self.budget_gb = budget_gb
        self.drop = list(drop)
        self.protected = list(protected)
        drop_line = (
            f"Drop: {self.drop[0]} (try a smaller alternative)."
            if self.drop else "No safe drop candidate — reduce scope manually."
        )
        protected_line = (
            f"Protected, never evicted: {', '.join(self.protected)}."
            if self.protected else ""
        )
        message = (
            f"Phase {phase} needs {needed_gb:.1f} GB but the budget is "
            f"{budget_gb:.1f} GB. {drop_line} {protected_line}"
        ).strip()
        assert not (set(self.drop) & set(self.protected)), \
            "invariant violated: drop must never include a protected model"
        super().__init__(message)


def _status(required: float, available: float) -> str:
    """green with >=15% headroom, amber inside the margin, red over capacity.
    Salvaged verbatim."""
    if available <= 0:
        return "red"
    if required > available:
        return "red"
    if required > available * 0.85:
        return "amber"
    return "green"


def active_params_b(params_b: float, *, is_moe: bool = False,
                    num_experts: int = 8, top_k: int = 2) -> float:
    """Salvaged from build/preflight.py's spec-driven version, adapted to
    take plain values instead of a training-job PreflightSpec (nucleus has
    no such spec — the student here is always the domain's declared base)."""
    if not is_moe:
        return params_b
    experts = max(num_experts, 1)
    top_k = max(top_k, 1)
    expert_fraction = 0.55
    return params_b * ((1 - expert_fraction) + expert_fraction * min(top_k / experts, 1.0))


def _capacity() -> Dict[str, float]:
    """Salvaged verbatim (module path for ARAIL_MODELS_DIR/psutil unchanged)."""
    ram_gb = 0.0
    try:
        import psutil
        ram_gb = psutil.virtual_memory().total / (1024 ** 3)
    except Exception:  # noqa: BLE001
        pass
    models_dir = os.getenv("ARAIL_MODELS_DIR", "lab/models")
    disk_gb = 0.0
    try:
        probe = models_dir if os.path.isdir(models_dir) else "."
        disk_gb = shutil.disk_usage(probe).free / (1024 ** 3)
    except Exception:  # noqa: BLE001
        pass
    return {"ram_gb": ram_gb, "vram_gb": ram_gb * 0.75, "disk_gb": disk_gb}


def _lora_memory_gb(params_b: float, *, seq_len: int = 4096, batch_size: int = 4,
                    precision: str = "q4") -> float:
    """Salvaged formula body from build/preflight.py's estimate(): weights +
    (Q)LoRA adapter optimizer state + activations, +20% headroom."""
    bpp = _BYTES_PER_PARAM.get(precision, 2.0)
    weights_gb = params_b * 1e9 * bpp / (1024 ** 3)
    adapter_params = params_b * 1e9 * 0.01
    opt_gb = adapter_params * 12 / (1024 ** 3)
    hidden_est = 1024 * max(params_b, 0.1) ** (1 / 3)
    act_gb = (batch_size * seq_len * hidden_est * 2 * 48) / (1024 ** 3)
    return (weights_gb + opt_gb + act_gb) * 1.2


def _weights_gb(model) -> float:
    return model.weights_bytes / (1024 ** 3)


# ── Buddy reserve ──────────────────────────────────────────────────────

@dataclass
class BuddyReserve:
    measured_gb: float = 0.0
    declared_gb: float = 0.0

    @property
    def reserve_gb(self) -> float:
        return max(self.measured_gb, self.declared_gb)


def _measure_buddy_reserve() -> float:
    """Best-effort: portal process RSS + Ollama /api/ps size_vram, both
    loopback. Any failure degrades to 0.0 (declared_gb is the floor that
    still applies) — this is why the promise is max(measured, declared),
    not measured alone."""
    total_bytes = 0.0
    try:
        import psutil
        for proc in psutil.process_iter(["cmdline", "memory_info"]):
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if "uvicorn" in cmdline and "arail.portal" in cmdline:
                total_bytes += proc.info["memory_info"].rss
    except Exception:  # noqa: BLE001
        pass
    try:
        import requests

        resp = requests.get("http://127.0.0.1:11434/api/ps", timeout=1.0)
        if resp.ok:
            for model in resp.json().get("models", []):
                total_bytes += float(model.get("size_vram", 0) or 0)
    except Exception:  # noqa: BLE001
        pass
    return total_bytes / (1024 ** 3)


def _declared_buddy_reserve() -> float:
    """On-disk size of the configured Buddy models: the minimalist Ollama
    tier model (best-effort; Ollama blob sizes aren't trivially path-
    addressable, so this is conservative and may under-count) plus the
    QueueLLM deep-mode model dir when configured. Any failure degrades to
    0.0 — declared is a floor, not the only signal (see measured above)."""
    total_bytes = 0.0
    try:
        from arail.nucleus.runtime_names import buddy_deep_model_env_value

        deep_model_dir = buddy_deep_model_env_value()
        if deep_model_dir:
            from arail.config import MODELS_DIR
            candidate = os.path.join(MODELS_DIR, deep_model_dir) \
                if not os.path.isabs(deep_model_dir) else deep_model_dir
            if os.path.isdir(candidate):
                for root, _dirs, files in os.walk(candidate):
                    for f in files:
                        try:
                            total_bytes += os.path.getsize(os.path.join(root, f))
                        except OSError:
                            pass
    except Exception:  # noqa: BLE001
        pass
    return total_bytes / (1024 ** 3)


def measure_buddy_reserve() -> BuddyReserve:
    return BuddyReserve(measured_gb=_measure_buddy_reserve(), declared_gb=_declared_buddy_reserve())


# ── capability probes (each independently injectable) ──────────────────

def _default_capability_probes() -> Dict[str, Callable[[], Requirement]]:
    return {
        "deep_runtime_importable": _probe_deep_runtime_importable,
        "mlx_lm_version": _probe_mlx_lm_version,
        "lab_mode": _probe_lab_mode,
    }


def _probe_deep_runtime_importable() -> Requirement:
    from arail.nucleus.runtime_names import PY_MODULE

    try:
        __import__(PY_MODULE)
        return Requirement("deep runtime importable", PY_MODULE, "present", "green")
    except ImportError as exc:
        return Requirement("deep runtime importable", PY_MODULE, "absent", "red", str(exc))


def _probe_mlx_lm_version() -> Requirement:
    try:
        import mlx_lm
        raw = getattr(mlx_lm, "__version__", "0.0.0")
        parts = tuple(int(p) for p in raw.split(".")[:2])
        ok = _MLX_LM_MIN <= parts < _MLX_LM_MAX_EXCLUSIVE
        status = "green" if ok else "red"
        note = "" if ok else (
            f"supported range is >={'.'.join(map(str, _MLX_LM_MIN))},"
            f"<{'.'.join(map(str, _MLX_LM_MAX_EXCLUSIVE))}"
        )
        return Requirement("mlx_lm version", ">=0.31,<0.32", raw, status, note)
    except ImportError as exc:
        return Requirement("mlx_lm version", ">=0.31,<0.32", "absent", "red", str(exc))


def _probe_lab_mode() -> Requirement:
    from arail.airgap import lab_mode

    mode = lab_mode()
    return Requirement("effective LAB_MODE", "airgapped (recommended)", mode, "green", "")


# ── protected (Buddy) model resolution (B6, 2026-09-23 review) ────────
#
# `protected` used to be the literal string "Buddy" — compared against a
# `drop` list that only ever holds real model directory names, so the
# "never evict Buddy" invariant was structurally untestable (the
# intersection was always empty, whatever the code did). `protected` must
# instead be Buddy's ACTUAL, resolved model identities, so a candidate
# that happens to be the same model Buddy uses (e.g. the `ai-engineer`
# judge alias resolving to the same directory as Buddy's configured deep
# model) is correctly recognized and never proposed as a drop candidate.

def _resolve_protected_model_names() -> List[str]:
    """Buddy's configured model names: the minimalist-tier chat model
    (Ollama tag, MODEL_NAME) and the deep-mode model directory name (the
    frozen runtime env var, read via runtime_names.buddy_deep_model_env_value()
    -- this module never spells that name itself). Best-effort — an unset
    or unresolvable value is simply omitted, never guessed."""
    names: List[str] = []
    try:
        from arail.config import MODEL_NAME
        if MODEL_NAME:
            names.append(MODEL_NAME)
    except Exception:  # noqa: BLE001
        pass
    try:
        from arail.nucleus.runtime_names import buddy_deep_model_env_value
        deep = buddy_deep_model_env_value()
        if deep:
            names.append(deep)
    except Exception:  # noqa: BLE001
        pass
    return names


def _model_identity_key(model: Any) -> str:
    """The identity a candidate/protected model is compared by:
    models.model_identity() (content-derived) when the model is a real,
    on-disk LocalModel; otherwise its `.name` (or its bare string form)
    as a best-effort fallback for callers that pass lighter test doubles
    or a bare protected-name string."""
    if isinstance(model, str):
        return model
    path = getattr(model, "path", None)
    if path is not None:
        try:
            from arail.nucleus.models import model_identity

            return model_identity(model)
        except Exception:  # noqa: BLE001
            pass
    return getattr(model, "name", str(model))


def _resolve_protected_identities(protected_names: List[str]) -> Dict[str, str]:
    """Maps each protected name to the identity key it should be compared
    by — resolved to a real on-disk model's content identity when that
    name is actually present under ARAIL_MODELS_DIR, otherwise the bare
    name itself (still correct: it stops matching a placeholder literal
    like "Buddy" and starts matching the real configured name)."""
    resolved: Dict[str, str] = {}
    for name in protected_names:
        key = name
        try:
            from arail.nucleus.models import model_identity, resolve_model

            key = model_identity(resolve_model(name))
        except Exception:  # noqa: BLE001
            pass
        resolved[name] = key
    return resolved


# ── main entry point ────────────────────────────────────────────────

def run_preflight(
    domain,
    *,
    capacity: Optional[Dict[str, float]] = None,
    buddy: Optional[BuddyReserve] = None,
    memory_budget_gb: Optional[float] = None,
    student_model=None,
    teacher_model=None,
    base_student_model=None,
    judge_model=None,
    runtime_streams: bool = False,
    capability_probes: Optional[Dict[str, Callable[[], Requirement]]] = None,
) -> PreflightReport:
    """Run the full preflight: memory plan + Buddy reserve + capability
    rows. Raises PreflightRefusal (mapped to exit 3) on any red memory row.

    ``capacity``/``buddy`` are injectable for tests (ARCHITECTURE.md §4.5).
    Model objects (student/teacher/base_student/judge) are optional so this
    function is usable even before a role is resolved yet — callers that
    have arail.nucleus.models.LocalModel instances pass them for the real
    per-phase sizing; omitted roles are simply skipped in the phase-C max().
    """
    cap = capacity if capacity is not None else _capacity()
    buddy_reserve = buddy if buddy is not None else measure_buddy_reserve()

    ram_ceiling = cap["ram_gb"] * 0.75
    if memory_budget_gb is not None:
        ram_ceiling = min(ram_ceiling, memory_budget_gb)
    budget_gb = ram_ceiling - buddy_reserve.reserve_gb - _RESERVE_GB

    report = PreflightReport(budget_gb=round(budget_gb, 2),
                             buddy_reserve_gb=round(buddy_reserve.reserve_gb, 2))

    protected_names = _resolve_protected_model_names()
    protected_identity_map = _resolve_protected_identities(protected_names)
    protected = list(protected_identity_map.keys())
    protected_identities = set(protected_identity_map.values())

    # Phase A / A2: teacher resident, or streamed window if it doesn't fit
    # and QueueLLM is present.
    if teacher_model is not None:
        resident_gb = _weights_gb(teacher_model) * 1.10 + _RESERVE_GB  # + a flat KV-budget estimate
        phase_a_gb = resident_gb
        note = "resident: weights * 1.10 + KV budget estimate"
        if resident_gb > budget_gb and runtime_streams:
            window_gb = min(_STREAM_WINDOW_CAP_GB, max(budget_gb - _STREAM_BUDGET_MARGIN_GB, 0.0))
            num_layers = max(int(teacher_model.config.get("num_hidden_layers", 32) or 32), 1)
            per_layer_gb = _weights_gb(teacher_model) / num_layers if num_layers else 0.0
            ring_depth = max(1, int(window_gb / per_layer_gb)) if per_layer_gb > 0 else 1
            phase_a_gb = window_gb
            note = f"streamed window (ring_depth={ring_depth}): min(6GB, budget-1GB)"
        report.phase_requirements_gb["A"] = round(phase_a_gb, 2)
        report.phase_requirements_gb["A2"] = report.phase_requirements_gb["A"]
        status = _status(phase_a_gb, budget_gb)
        report.rows.append(Requirement("Phase A/A2 (teacher)", f"{phase_a_gb:.1f} GB",
                                       f"{budget_gb:.1f} GB", status, note))
        if status == "red":
            _refuse("A", phase_a_gb, budget_gb, [teacher_model], protected, protected_identities)

    # Phase B: student LoRA estimate.
    if student_model is not None:
        phase_b_gb = _lora_memory_gb(student_model.params_est_b)
        report.phase_requirements_gb["B"] = round(phase_b_gb, 2)
        status = _status(phase_b_gb, budget_gb)
        report.rows.append(Requirement("Phase B (student LoRA)", f"{phase_b_gb:.1f} GB",
                                       f"{budget_gb:.1f} GB", status,
                                       "weights + adapter optimizer + activations, +20%"))
        if status == "red":
            _refuse("B", phase_b_gb, budget_gb, [student_model], protected, protected_identities)

    # Phase C: student, base_student, judge — loaded sequentially, so the
    # requirement is the MAX of the three, not the sum.
    phase_c_candidates = [m for m in (student_model, base_student_model, judge_model) if m is not None]
    if phase_c_candidates:
        sized = [(_weights_gb(m) * 1.10 + 1.0, m) for m in phase_c_candidates]
        phase_c_gb, _largest = max(sized, key=lambda pair: pair[0])
        report.phase_requirements_gb["C"] = round(phase_c_gb, 2)
        status = _status(phase_c_gb, budget_gb)
        report.rows.append(Requirement("Phase C (eval, sequential)", f"{phase_c_gb:.1f} GB",
                                       f"{budget_gb:.1f} GB", status,
                                       "max(student, base_student, judge) — loaded one at a time"))
        if status == "red":
            _refuse("C", phase_c_gb, budget_gb, phase_c_candidates, protected, protected_identities)

    # Capability rows.
    probes = capability_probes if capability_probes is not None else _default_capability_probes()
    for probe in probes.values():
        report.rows.append(probe())

    report.overall = ("red" if report.has_red else
                      "amber" if any(r.status == "amber" for r in report.rows)
                      else "green")
    report.notes.append(
        "Memory figures are heuristic estimates (weights + optimizer + "
        "activations + headroom); a real run can still surface an OOM this "
        "estimate missed."
    )
    return report


def _refuse(phase: str, needed_gb: float, budget_gb: float, candidates: List[Any],
           protected: List[str], protected_identities: "set[str]") -> None:
    """Picks the largest candidate that is NOT one of Buddy's protected
    model identities (B6) — never the model that merely happens to be the
    one that overflowed, if that model is protected. If every candidate
    for this phase is protected, there is no safe drop candidate at all."""
    droppable = [m for m in candidates if m is not None
                and _model_identity_key(m) not in protected_identities]
    if droppable:
        chosen = max(droppable, key=lambda m: getattr(m, "weights_bytes", 0))
        drop = [getattr(chosen, "name", str(chosen))]
    else:
        drop = []
    raise PreflightRefusal([], phase, needed_gb, budget_gb, drop, protected)
