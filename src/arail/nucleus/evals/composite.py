"""Composite formula registry + decision rule registry (ARCHITECTURE.md
§4.9). Versioned by id so a card is only ever compared within a formula
id — the /forge viewer (commit 25) must show the id alongside the number.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict

NOT_RUN = "not_run"


@dataclass(frozen=True)
class CompositeResult:
    formula_id: str
    value: float
    inputs: Dict[str, float]


def _composite_v1(metrics: dict) -> CompositeResult:
    closed_mean_f1 = metrics["closed"]["mean_f1"]
    lc_win_rate = metrics["open"]["lc_win_rate"]
    compiles = metrics["executable"]["compiles"]
    if compiles == NOT_RUN:
        raise ValueError("composite/v1 requires executable.compiles to be a number; got not_run")
    value = 0.4 * closed_mean_f1 + 0.3 * lc_win_rate + 0.3 * compiles
    return CompositeResult("composite/v1", round(value, 6),
                           {"closed.mean_f1": closed_mean_f1, "open.lc_win_rate": lc_win_rate,
                            "executable.compiles": compiles})


def _composite_v1_nc(metrics: dict) -> CompositeResult:
    closed_mean_f1 = metrics["closed"]["mean_f1"]
    lc_win_rate = metrics["open"]["lc_win_rate"]
    patch_applies = metrics["executable"]["patch_applies"]
    checkpatch_clean = metrics["executable"]["checkpatch_clean"]
    mean_exec = (patch_applies + checkpatch_clean) / 2.0
    value = 0.4 * closed_mean_f1 + 0.3 * lc_win_rate + 0.3 * mean_exec
    return CompositeResult("composite/v1-nc", round(value, 6),
                           {"closed.mean_f1": closed_mean_f1, "open.lc_win_rate": lc_win_rate,
                            "executable.patch_applies": patch_applies,
                            "executable.checkpatch_clean": checkpatch_clean})


_FORMULAS: Dict[str, Callable[[dict], CompositeResult]] = {
    "composite/v1": _composite_v1,
    "composite/v1-nc": _composite_v1_nc,
}


def select_formula_id(metrics: dict) -> str:
    """`compiles` is `not_run` in sprint 1 (no Linux build host) -> the
    no-compiles formula is selected automatically, never by hand."""
    if metrics.get("executable", {}).get("compiles", NOT_RUN) == NOT_RUN:
        return "composite/v1-nc"
    return "composite/v1"


def compute(metrics: dict, *, formula_id: str = None) -> CompositeResult:
    formula_id = formula_id or select_formula_id(metrics)
    if formula_id not in _FORMULAS:
        raise ValueError(f"unknown composite formula id {formula_id!r}")
    return _FORMULAS[formula_id](metrics)


# ── decision_rule/v1 ────────────────────────────────────────────────

DECISION_KNOWN_ISSUE = "KNOWN_ISSUE"
DECISION_BETA = "BETA"
DECISION_COMPATIBLE = "COMPATIBLE"
DECISION_CERTIFIED = "CERTIFIED"

_BETA_MARGIN = 0.05


def decide(achieved: float, target: float, *, beats_base: bool, residency_status: str) -> str:
    """Evaluated in order — the first rule that fires wins (ARCHITECTURE.md
    §4.9 decision_rule/v1)."""
    if not beats_base:
        return DECISION_KNOWN_ISSUE
    if achieved < target - _BETA_MARGIN:
        return DECISION_BETA
    if achieved < target or residency_status == "violated":
        return DECISION_COMPATIBLE
    return DECISION_CERTIFIED
