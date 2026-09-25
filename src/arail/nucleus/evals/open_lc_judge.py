"""Pairwise, position-randomized, length-controlled judge scoring
(ARCHITECTURE.md §4.9).

Judge identity is checked BEFORE any generation happens — a judge that is
(by content identity, not name/alias) the teacher or the base student
invalidates the whole comparison, so ``JudgeIsTeacher`` fires first
(T-JUDGE-1).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

_INVALID_RATE_UNRELIABLE_THRESHOLD = 0.10
_BOOTSTRAP_N = 1000


class JudgeIsTeacher(Exception):
    pass


def assert_judge_identity_distinct(judge_identity: str, *, teacher_identity: str,
                                   student_base_identity: str,
                                   fused_student_identity: Optional[str] = None) -> None:
    """Content-identity comparison (not name/alias-based, so aliasing can't
    sneak a teacher back in as "a different judge model")."""
    if judge_identity == teacher_identity:
        raise JudgeIsTeacher("judge model_identity matches the teacher — refusing before any generation")
    if judge_identity == student_base_identity:
        raise JudgeIsTeacher("judge model_identity matches the student base — refusing before any generation")
    if fused_student_identity is not None and judge_identity == fused_student_identity:
        raise JudgeIsTeacher("judge model_identity matches the fused student — refusing before any generation")


@dataclass(frozen=True)
class PairItem:
    item_id: str
    text_model: str      # the model under test's completion
    text_baseline: str    # the comparison baseline's completion


@dataclass(frozen=True)
class JudgedPair:
    item_id: str
    order: Tuple[str, str]   # which side ("model"|"baseline") was shown as A, B
    raw_verdict: str         # "A" | "B" | "invalid"
    model_won: Optional[bool]  # None if invalid


def randomize_and_judge(
    items: Sequence[PairItem], *, seed: int,
    judge_fn: Callable[[str, str, str], str],
) -> List[JudgedPair]:
    """``judge_fn(item_id, text_a, text_b) -> "A"|"B"|anything-else``.
    Position is randomized per item by Random(seed ^ item_idx) — a fixed,
    reproducible per-item shuffle, not one shared Random instance walked
    sequentially (so results don't depend on item processing order)."""
    out: List[JudgedPair] = []
    for idx, item in enumerate(items):
        rng = random.Random(seed ^ idx)
        model_first = rng.random() < 0.5
        if model_first:
            text_a, text_b, order = item.text_model, item.text_baseline, ("model", "baseline")
        else:
            text_a, text_b, order = item.text_baseline, item.text_model, ("baseline", "model")

        raw = judge_fn(item.item_id, text_a, text_b)
        verdict = raw.strip().upper() if isinstance(raw, str) else ""
        if verdict not in ("A", "B"):
            out.append(JudgedPair(item.item_id, order, "invalid", None))
            continue

        winner_side = order[0] if verdict == "A" else order[1]
        out.append(JudgedPair(item.item_id, order, verdict, winner_side == "model"))
    return out


@dataclass(frozen=True)
class LCJudgeResult:
    n: int
    n_invalid: int
    invalid_rate: float
    raw_win_rate: float
    lc_win_rate: float
    ci95: Tuple[float, float]
    unreliable: bool
    position_a_rate: float


def _fit_length_controlled_logit(x: np.ndarray, y: np.ndarray, *, iters: int = 50) -> Tuple[float, float]:
    """Newton-Raphson logistic regression, y ~ sigmoid(b0 + b1*x). Two
    parameters, well-conditioned for this sample size — no heavy
    statsmodels dependency needed."""
    b0, b1 = 0.0, 0.0
    n = len(x)
    for _ in range(iters):
        z = b0 + b1 * x
        p = 1.0 / (1.0 + np.exp(-z))
        grad0 = np.sum(y - p)
        grad1 = np.sum((y - p) * x)
        w = p * (1 - p) + 1e-9
        h00 = -np.sum(w)
        h11 = -np.sum(w * x * x)
        h01 = -np.sum(w * x)
        det = h00 * h11 - h01 * h01
        if abs(det) < 1e-12:
            break
        db0 = -(h11 * grad0 - h01 * grad1) / det
        db1 = -(h00 * grad1 - h01 * grad0) / det
        b0 += db0
        b1 += db1
        if abs(db0) < 1e-8 and abs(db1) < 1e-8:
            break
    return b0, b1


def score(
    judged: Sequence[JudgedPair], *, len_model: Dict[str, int], len_baseline: Dict[str, int],
    seed: int,
) -> LCJudgeResult:
    n = len(judged)
    invalid = [j for j in judged if j.raw_verdict == "invalid"]
    valid = [j for j in judged if j.raw_verdict != "invalid"]
    invalid_rate = len(invalid) / n if n else 0.0

    raw_win_rate = (sum(1 for j in valid if j.model_won) / len(valid)) if valid else 0.0

    if len(valid) >= 2:
        deltas = np.array([len_model[j.item_id] - len_baseline[j.item_id] for j in valid], dtype=float)
        sigma = float(np.std(deltas)) or 1.0
        x = np.tanh(deltas / sigma)
        y = np.array([1.0 if j.model_won else 0.0 for j in valid])
        b0, b1 = _fit_length_controlled_logit(x, y)
        lc_win_rate = float(1.0 / (1.0 + np.exp(-b0)))
    else:
        lc_win_rate = raw_win_rate

    rng = np.random.default_rng(seed)
    wins = np.array([1.0 if j.model_won else 0.0 for j in valid])
    boot_means = []
    if len(wins) > 0:
        for _ in range(_BOOTSTRAP_N):
            sample = rng.choice(wins, size=len(wins), replace=True)
            boot_means.append(float(sample.mean()))
        boot_means.sort()
        lo = boot_means[int(0.025 * _BOOTSTRAP_N)]
        hi = boot_means[min(int(0.975 * _BOOTSTRAP_N), _BOOTSTRAP_N - 1)]
        ci95 = (round(lo, 4), round(hi, 4))
    else:
        ci95 = (0.0, 0.0)

    n_a = sum(1 for j in judged if j.order[0] == "model")
    position_a_rate = n_a / n if n else 0.0

    return LCJudgeResult(
        n=n, n_invalid=len(invalid), invalid_rate=round(invalid_rate, 4),
        raw_win_rate=round(raw_win_rate, 4), lc_win_rate=round(lc_win_rate, 4),
        ci95=ci95, unreliable=invalid_rate > _INVALID_RATE_UNRELIABLE_THRESHOLD,
        position_a_rate=round(position_a_rate, 4),
    )
