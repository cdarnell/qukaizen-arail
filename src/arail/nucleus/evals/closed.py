"""Closed-ended metrics: binary P/R/F1 and macro-F1 (ARCHITECTURE.md §4.9).

Deliberately produces no ``accuracy`` key anywhere — an imbalanced 95/5
split makes accuracy meaningless (predict-the-majority-class scores 0.95
while being useless), and the DNA card schema (commit 19) enforces
``additionalProperties: false`` on the closed-ended headline block so one
can never sneak back in by accident (T-CLOSED-2).
"""

from __future__ import annotations

from typing import Dict, List, Sequence


def binary_prf1(gold: Sequence, pred: Sequence, *, positive) -> Dict:
    """Precision/recall/F1 for a declared positive class. Zero-division
    (no predicted positives, or no actual positives) resolves to 0.0 with
    a `warn` field rather than raising — a degenerate model is a valid,
    scoreable outcome, not an error."""
    if len(gold) != len(pred):
        raise ValueError(f"gold/pred length mismatch: {len(gold)} vs {len(pred)}")

    tp = sum(1 for g, p in zip(gold, pred) if g == positive and p == positive)
    fp = sum(1 for g, p in zip(gold, pred) if g != positive and p == positive)
    fn = sum(1 for g, p in zip(gold, pred) if g == positive and p != positive)

    warn = None
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    if (tp + fp) == 0:
        warn = "no predicted positives"
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    if (tp + fn) == 0:
        warn = (warn + "; no actual positives") if warn else "no actual positives"
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    support = sum(1 for g in gold if g == positive)
    result = {"precision": round(precision, 6), "recall": round(recall, 6),
             "f1": round(f1, 6), "n": len(gold), "support": support}
    if warn:
        result["warn"] = warn
    return result


def macro_f1(gold: Sequence, pred: Sequence) -> Dict:
    """Unweighted mean F1 over every class present in gold (not pred —
    a class the model never predicts still counts against it at its
    natural weight of one class among many, per-class not per-example)."""
    if len(gold) != len(pred):
        raise ValueError(f"gold/pred length mismatch: {len(gold)} vs {len(pred)}")

    classes = sorted(set(gold))
    per_class: Dict[str, Dict] = {}
    for cls in classes:
        row = binary_prf1(gold, pred, positive=cls)
        per_class[str(cls)] = {"precision": row["precision"], "recall": row["recall"],
                               "f1": row["f1"], "support": row["support"]}

    macro = sum(row["f1"] for row in per_class.values()) / len(classes) if classes else 0.0
    return {"macro_f1": round(macro, 6), "per_class": per_class, "n": len(gold)}


def mean_f1(rows: List[Dict]) -> float:
    """Mean of a list of already-computed f1/macro_f1 values — the
    "closed.mean_f1" composite input (§4.9's composite.py formulas)."""
    values = [row.get("f1", row.get("macro_f1", 0.0)) for row in rows]
    return sum(values) / len(values) if values else 0.0
