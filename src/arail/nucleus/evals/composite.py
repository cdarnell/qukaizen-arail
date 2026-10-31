"""Composite formula registry + decision rule registry (ARCHITECTURE.md
§4.9). Versioned by id so a card is only ever compared within a formula
id — the /forge viewer (commit 25) must show the id alongside the number.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict

NOT_RUN = "not_run"
# B3 (2026-09-23 review): the composite must never silently coerce a
# not_run input into a number (0.5, 0.0, ...) -- if ANY input this
# formula needs is not_run, the composite itself is not_computed, and
# `decide()` must treat that as a non-shipping decision, not compute a
# misleadingly-precise number from partial data.
NOT_COMPUTED = "not_computed"


@dataclass(frozen=True)
class CompositeResult:
    formula_id: str
    value: object  # float, or the NOT_COMPUTED sentinel string
    inputs: Dict[str, object]


def _composite_v1(metrics: dict) -> CompositeResult:
    closed_mean_f1 = metrics["closed"]["mean_f1"]
    lc_win_rate = metrics["open"]["lc_win_rate"]
    compiles = metrics["executable"]["compiles"]
    inputs = {"closed.mean_f1": closed_mean_f1, "open.lc_win_rate": lc_win_rate,
             "executable.compiles": compiles}
    if NOT_RUN in (closed_mean_f1, lc_win_rate, compiles):
        return CompositeResult("composite/v1", NOT_COMPUTED, inputs)
    value = 0.4 * closed_mean_f1 + 0.3 * lc_win_rate + 0.3 * compiles
    return CompositeResult("composite/v1", round(value, 6), inputs)


def _composite_v1_nc(metrics: dict) -> CompositeResult:
    closed_mean_f1 = metrics["closed"]["mean_f1"]
    lc_win_rate = metrics["open"]["lc_win_rate"]
    patch_applies = metrics["executable"]["patch_applies"]
    checkpatch_clean = metrics["executable"]["checkpatch_clean"]
    inputs = {"closed.mean_f1": closed_mean_f1, "open.lc_win_rate": lc_win_rate,
             "executable.patch_applies": patch_applies, "executable.checkpatch_clean": checkpatch_clean}
    if NOT_RUN in (closed_mean_f1, lc_win_rate, patch_applies, checkpatch_clean):
        return CompositeResult("composite/v1-nc", NOT_COMPUTED, inputs)
    mean_exec = (patch_applies + checkpatch_clean) / 2.0
    value = 0.4 * closed_mean_f1 + 0.3 * lc_win_rate + 0.3 * mean_exec
    return CompositeResult("composite/v1-nc", round(value, 6), inputs)


def _composite_v1_open(metrics: dict) -> CompositeResult:
    """No executable checks at all -- selected automatically (never by
    hand) when `executable` is wholly not_run (B3, 2026-09-23 review):
    this sprint's task-adapter seam has no patch-generation task, so
    compiles/patch_applies/checkpatch_clean are honestly not_run every
    run, not just "sometimes". Requiring composite/v1-nc's executable
    inputs in that case would make the composite permanently
    not_computed even though closed + open WERE genuinely measured."""
    closed_mean_f1 = metrics["closed"]["mean_f1"]
    lc_win_rate = metrics["open"]["lc_win_rate"]
    inputs = {"closed.mean_f1": closed_mean_f1, "open.lc_win_rate": lc_win_rate}
    if NOT_RUN in (closed_mean_f1, lc_win_rate):
        return CompositeResult("composite/v1-open", NOT_COMPUTED, inputs)
    value = 0.6 * closed_mean_f1 + 0.4 * lc_win_rate
    return CompositeResult("composite/v1-open", round(value, 6), inputs)


_FORMULAS: Dict[str, Callable[[dict], CompositeResult]] = {
    "composite/v1": _composite_v1,
    "composite/v1-nc": _composite_v1_nc,
    "composite/v1-open": _composite_v1_open,
}

# B4 (2026-09-23 review): a CONSTANT formula string per formula id, used
# both as the card's published `composite.formula` and as one of the
# eval_hash inputs. Previously both places used `str(composite_result.inputs)`
# -- the computed METRIC VALUES, not the formula -- so two students scored
# on an identical yardstick got different eval_hashes, and the card's
# "formula" field was really a dump of that run's numbers.
FORMULA_STRINGS: Dict[str, str] = {
    "composite/v1": "0.4*closed.mean_f1+0.3*open.lc_win_rate+0.3*executable.compiles",
    "composite/v1-nc": "0.4*closed.mean_f1+0.3*open.lc_win_rate"
                       "+0.3*mean(executable.patch_applies,executable.checkpatch_clean)",
    "composite/v1-open": "0.6*closed.mean_f1+0.4*open.lc_win_rate",
}


def formula_string(formula_id: str) -> str:
    try:
        return FORMULA_STRINGS[formula_id]
    except KeyError:
        raise ValueError(f"unknown composite formula id {formula_id!r}") from None


def select_formula_id(metrics: dict) -> str:
    """Selected automatically from what was actually measured this run,
    never by hand: all three executable checks not_run -> no-executable
    formula (composite/v1-open); only `compiles` not_run (no Linux build
    host) -> the no-compiles formula (composite/v1-nc); everything
    measured -> the full formula (composite/v1)."""
    executable = metrics.get("executable", {})
    exec_keys = ("compiles", "patch_applies", "checkpatch_clean")
    if all(executable.get(k, NOT_RUN) == NOT_RUN for k in exec_keys):
        return "composite/v1-open"
    if executable.get("compiles", NOT_RUN) == NOT_RUN:
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
# B3 (2026-09-23 review): added to decision_rule/v1 -- the composite
# genuinely could not be computed (some required input is not_run). This
# is distinct from KNOWN_ISSUE (computed, and it lost to its base): it
# means "no verdict was possible", and must never be confused with a
# real decision by a caller pattern-matching on the four original values.
DECISION_NOT_EVALUATED = "NOT_EVALUATED"

_BETA_MARGIN = 0.05


def decide(achieved, target: float, *, beats_base, residency_status: str,
          formula_id: "str | None" = None) -> str:
    """Evaluated in order — the first rule that fires wins (ARCHITECTURE.md
    §4.9 decision_rule/v1, extended by NOT_EVALUATED).

    ``beats_base`` is ``True``/``False`` when a real base-student score was
    produced, or ``None`` for "unknown" (no comparable base score) — unknown
    is treated the same as "did not beat its base": it must never let a
    build reach CERTIFIED/COMPATIBLE on an unverified claim (B3).

    ``formula_id`` enforces the v1-open Gate B precondition the architect
    recorded in review round 2 (ARCHITECTURE.md §4.9, §9 item 7): a card
    scored under composite/v1-open has NO executable evidence at all (no
    patch-generation task exists this sprint), so it can never read
    CERTIFIED — only COMPATIBLE at most — until Gate B wires
    patch_applies/checkpatch_clean and v1-open is retired."""
    if achieved == NOT_COMPUTED:
        return DECISION_NOT_EVALUATED
    if beats_base is not True:
        return DECISION_KNOWN_ISSUE
    if achieved < target - _BETA_MARGIN:
        return DECISION_BETA
    if achieved < target or residency_status == "violated":
        return DECISION_COMPATIBLE
    if formula_id == "composite/v1-open":
        return DECISION_COMPATIBLE
    return DECISION_CERTIFIED
