"""Numpy reference KD loss (ARCHITECTURE.md §4.9's KD design; CI-testable
without MLX). train/mlx_kd.py's training loop must produce the same value
as this module within 1e-4 (T-KD-4, requires_mlx) — this is the ground
truth the MLX implementation is checked against, not a simplification of
it.

Loss = CE(student, sampled token) + temperature^2 * KL(teacher_topn ||
student_topn), where both the teacher and student distributions for the
KL term are restricted to the teacher's reported top-N ids and each
independently renormalized via softmax over just those ids
("topn_softmax", ARCHITECTURE.md §3 N2) — an apples-to-apples comparison
that never requires materializing either side's full vocab distribution
for the KL term (only the CE term needs the student's full logits, since
cross-entropy is inherently a full-normalization quantity).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np


def _log_softmax(x: np.ndarray) -> np.ndarray:
    x = x - np.max(x)
    return x - np.log(np.sum(np.exp(x)))


def _softmax(x: np.ndarray) -> np.ndarray:
    x = x - np.max(x)
    e = np.exp(x)
    return e / np.sum(e)


@dataclass(frozen=True)
class RenormResult:
    kept_ids: Sequence[int]
    probs: np.ndarray
    captured_mass: float
    dropped_mass: float


def renormalize_topn(ids: Sequence[int], logprobs: Sequence[float], *,
                     student_max_id: Optional[int] = None) -> RenormResult:
    """Drop any id beyond the student's vocab (student_max_id, from
    tokenizer_parity.Parity), then softmax over what's left (topn_softmax).
    captured_mass/dropped_mass are computed in PROBABILITY space from the
    reported logprobs before renormalization — they answer "how much of
    the teacher's actual probability mass did we keep", which softmax-ing
    the kept logprobs alone can't tell you (that's always exactly 1.0
    after renorm, by construction)."""
    ids = list(ids)
    logprobs = np.asarray(logprobs, dtype=np.float64)
    raw_probs = np.exp(logprobs)

    if student_max_id is None:
        keep_mask = np.ones(len(ids), dtype=bool)
    else:
        keep_mask = np.array([i <= student_max_id for i in ids])

    captured_mass = float(raw_probs[keep_mask].sum())
    dropped_mass = float(raw_probs[~keep_mask].sum())

    kept_ids = [i for i, keep in zip(ids, keep_mask) if keep]
    kept_logprobs = logprobs[keep_mask]
    probs = _softmax(kept_logprobs) if len(kept_logprobs) else np.array([])

    return RenormResult(kept_ids=kept_ids, probs=probs,
                        captured_mass=captured_mass, dropped_mass=dropped_mass)


@dataclass(frozen=True)
class KDLossResult:
    ce: float
    kl: float
    total: float
    captured_mass: float
    dropped_mass: float


def kd_loss(
    student_logits_full: np.ndarray, teacher_topn_ids: Sequence[int],
    teacher_topn_logprobs: Sequence[float], sampled_id: int, *,
    student_max_id: Optional[int] = None, temperature: float = 2.0,
) -> KDLossResult:
    student_logits_full = np.asarray(student_logits_full, dtype=np.float64)

    ce = float(-_log_softmax(student_logits_full)[sampled_id])

    renorm = renormalize_topn(teacher_topn_ids, teacher_topn_logprobs, student_max_id=student_max_id)
    if len(renorm.kept_ids) == 0:
        kl = 0.0
    else:
        student_topn_logits = student_logits_full[list(renorm.kept_ids)]
        student_topn_probs = _softmax(student_topn_logits)
        teacher_p = renorm.probs
        # KL(teacher || student); a zero teacher_p contributes 0 (0*log(0/x)=0 by convention).
        with np.errstate(divide="ignore", invalid="ignore"):
            terms = np.where(teacher_p > 0,
                             teacher_p * (np.log(teacher_p) - np.log(np.clip(student_topn_probs, 1e-12, None))),
                             0.0)
        kl = float(np.sum(terms))

    total = ce + (temperature ** 2) * kl
    return KDLossResult(ce=ce, kl=kl, total=total,
                        captured_mass=renorm.captured_mass, dropped_mass=renorm.dropped_mass)
