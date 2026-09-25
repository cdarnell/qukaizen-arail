"""arail.nucleus.evals.closed + composite (T-CLOSED-1,2 and composite/
decision_rule coverage)."""

from __future__ import annotations

import pytest

from arail.nucleus.evals import closed, composite


# ── T-CLOSED-1: hand-computed confusion matrices, including 95/5 imbalance ──

def test_binary_prf1_hand_computed():
    gold = ["cve", "cve", "not", "not", "not"]
    pred = ["cve", "not", "not", "not", "cve"]
    row = closed.binary_prf1(gold, pred, positive="cve")
    # TP=1 (idx0), FP=1 (idx4), FN=1 (idx1) -> P=0.5, R=0.5, F1=0.5
    assert row["precision"] == pytest.approx(0.5)
    assert row["recall"] == pytest.approx(0.5)
    assert row["f1"] == pytest.approx(0.5)
    assert row["support"] == 2


def test_binary_prf1_imbalanced_95_5_case():
    # 95 "not", 5 "cve"; predict-the-majority-class model always says "not".
    gold = ["not"] * 95 + ["cve"] * 5
    pred = ["not"] * 100
    row = closed.binary_prf1(gold, pred, positive="cve")
    # accuracy would be 0.95, but F1 on the positive class is 0 -- the
    # whole point of the "no accuracy key" rule (T-CLOSED-2).
    assert row["f1"] == 0.0
    assert row["recall"] == 0.0
    assert "warn" in row  # no predicted positives at all


def test_binary_prf1_zero_division_no_actual_positives():
    row = closed.binary_prf1(["not", "not"], ["not", "cve"], positive="cve")
    assert row["recall"] == 0.0
    assert "warn" in row


def test_macro_f1_hand_computed():
    gold = ["a", "a", "b", "b", "c"]
    pred = ["a", "b", "b", "b", "c"]
    result = closed.macro_f1(gold, pred)
    assert set(result["per_class"]) == {"a", "b", "c"}
    assert result["n"] == 5
    # class c: perfect (P=R=F1=1); a: TP=1,FN=1,FP=0 -> F1=2/3; b: TP=2,FP=1,FN=0 -> F1=0.8
    assert result["macro_f1"] == pytest.approx((2 / 3 + 0.8 + 1.0) / 3, rel=1e-4)


# ── T-CLOSED-2: no "accuracy" key anywhere, recursively ──────────────

def _recursive_keys(obj):
    keys = set()
    if isinstance(obj, dict):
        keys |= set(obj.keys())
        for v in obj.values():
            keys |= _recursive_keys(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            keys |= _recursive_keys(v)
    return keys


def test_no_accuracy_key_anywhere():
    row = closed.binary_prf1(["cve", "not"], ["cve", "cve"], positive="cve")
    macro = closed.macro_f1(["a", "b"], ["a", "a"])
    assert "accuracy" not in _recursive_keys(row)
    assert "accuracy" not in _recursive_keys(macro)


def test_mean_f1_over_rows():
    rows = [{"f1": 0.8}, {"macro_f1": 0.6}]
    assert closed.mean_f1(rows) == pytest.approx(0.7)


# ── composite ─────────────────────────────────────────────────────────

def _metrics(*, compiles=composite.NOT_RUN, patch_applies=0.9, checkpatch_clean=0.8):
    return {
        "closed": {"mean_f1": 0.7},
        "open": {"lc_win_rate": 0.6},
        "executable": {"compiles": compiles, "patch_applies": patch_applies,
                       "checkpatch_clean": checkpatch_clean},
    }


def test_select_formula_v1_nc_when_compiles_not_run():
    assert composite.select_formula_id(_metrics()) == "composite/v1-nc"


def test_select_formula_v1_when_compiles_is_a_number():
    assert composite.select_formula_id(_metrics(compiles=0.5)) == "composite/v1"


def test_composite_v1_nc_formula_value():
    result = composite.compute(_metrics())
    expected = 0.4 * 0.7 + 0.3 * 0.6 + 0.3 * ((0.9 + 0.8) / 2)
    assert result.formula_id == "composite/v1-nc"
    assert result.value == pytest.approx(expected)


def test_composite_v1_formula_value():
    result = composite.compute(_metrics(compiles=0.5))
    expected = 0.4 * 0.7 + 0.3 * 0.6 + 0.3 * 0.5
    assert result.formula_id == "composite/v1"
    assert result.value == pytest.approx(expected)


def test_composite_v1_requires_numeric_compiles():
    # B3 (2026-09-23 review): a not_run required input makes the
    # composite not_computed, not a raised ValueError -- the caller
    # (certify.py) decides what to do with that (refuse), rather than
    # this pure function crashing.
    result = composite.compute(_metrics(), formula_id="composite/v1")
    assert result.value == composite.NOT_COMPUTED


def test_decide_returns_not_evaluated_when_composite_not_computed():
    assert composite.decide(composite.NOT_COMPUTED, 0.5, beats_base=True,
                            residency_status="ok") == "NOT_EVALUATED"


def test_decide_treats_unknown_beats_base_as_not_beating():
    assert composite.decide(0.9, 0.5, beats_base=None, residency_status="ok") == "KNOWN_ISSUE"


# ── decision_rule/v1 ────────────────────────────────────────────────

def test_decision_known_issue_when_not_beats_base():
    assert composite.decide(0.9, 0.5, beats_base=False, residency_status="ok") == "KNOWN_ISSUE"


def test_decision_beta_when_far_below_target():
    assert composite.decide(0.5, 0.75, beats_base=True, residency_status="ok") == "BETA"


def test_decision_compatible_when_slightly_below_target():
    assert composite.decide(0.72, 0.75, beats_base=True, residency_status="ok") == "COMPATIBLE"


def test_decision_compatible_when_residency_violated_even_if_achieved():
    assert composite.decide(0.9, 0.75, beats_base=True, residency_status="violated") == "COMPATIBLE"


def test_decision_certified_when_achieved_and_residency_ok():
    assert composite.decide(0.8, 0.75, beats_base=True, residency_status="ok") == "CERTIFIED"


def test_decision_order_known_issue_wins_even_if_would_be_certified():
    assert composite.decide(0.99, 0.5, beats_base=False, residency_status="ok") == "KNOWN_ISSUE"


# ── v1-open Gate B cap (2026-09-23 review round 2, ARCHITECTURE §4.9/§9
# item 7): a composite/v1-open card has NO executable evidence at all, so
# it can never read CERTIFIED, only COMPATIBLE at most, until Gate B ────

def test_v1_open_formula_caps_at_compatible_even_when_achieved_beats_target():
    assert composite.decide(0.9, 0.5, beats_base=True, residency_status="ok",
                            formula_id="composite/v1-open") == "COMPATIBLE"


def test_v1_open_formula_does_not_mask_beta_or_known_issue():
    assert composite.decide(0.5, 0.75, beats_base=True, residency_status="ok",
                            formula_id="composite/v1-open") == "BETA"
    assert composite.decide(0.9, 0.5, beats_base=False, residency_status="ok",
                            formula_id="composite/v1-open") == "KNOWN_ISSUE"


def test_other_formulas_unaffected_by_the_v1_open_cap():
    assert composite.decide(0.8, 0.75, beats_base=True, residency_status="ok",
                            formula_id="composite/v1") == "CERTIFIED"
    assert composite.decide(0.8, 0.75, beats_base=True, residency_status="ok",
                            formula_id=None) == "CERTIFIED"
