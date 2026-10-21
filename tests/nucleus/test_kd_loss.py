"""arail.nucleus.train.kd_loss — numpy reference (T-KD-1..3)."""

from __future__ import annotations

import numpy as np
import pytest

from arail.nucleus.train.kd_loss import kd_loss, renormalize_topn


# ── T-KD-1: CE + T^2*KL on a hand-computed 3-token example ────────────

def test_kd_loss_hand_computed_3_token_example():
    # 4-entry student vocab; teacher reports top-3 ids [0,1,2] with
    # logprobs that are already a valid distribution (sums to 1.0).
    teacher_ids = [0, 1, 2]
    teacher_probs = np.array([0.5, 0.3, 0.2])
    teacher_logprobs = np.log(teacher_probs)
    sampled_id = 0
    student_logits = np.array([1.0, 0.5, 0.0, -1.0])
    temperature = 2.0

    result = kd_loss(student_logits, teacher_ids, teacher_logprobs, sampled_id,
                     temperature=temperature)

    # Independently recompute via a second, obviously-correct path.
    student_full_probs = np.exp(student_logits) / np.exp(student_logits).sum()
    expected_ce = -np.log(student_full_probs[sampled_id])

    student_topn_logits = student_logits[teacher_ids]
    student_topn_probs = np.exp(student_topn_logits) / np.exp(student_topn_logits).sum()
    expected_kl = float(np.sum(teacher_probs * (np.log(teacher_probs) - np.log(student_topn_probs))))
    expected_total = expected_ce + temperature ** 2 * expected_kl

    assert result.ce == pytest.approx(expected_ce, abs=1e-9)
    assert result.kl == pytest.approx(expected_kl, abs=1e-9)
    assert result.total == pytest.approx(expected_total, abs=1e-9)
    assert result.captured_mass == pytest.approx(1.0)
    assert result.dropped_mass == pytest.approx(0.0)


def test_kd_loss_zero_when_student_matches_teacher_exactly():
    # Student logits chosen so its top-3 softmax exactly equals the
    # teacher's distribution -> KL should be ~0.
    teacher_probs = np.array([0.5, 0.3, 0.2])
    teacher_ids = [0, 1, 2]
    student_logits = np.log(np.array([0.5, 0.3, 0.2, 1e-9]))  # matches teacher on ids 0-2
    result = kd_loss(student_logits, teacher_ids, np.log(teacher_probs), sampled_id=0)
    assert result.kl == pytest.approx(0.0, abs=1e-6)


# ── T-KD-2: dropped-id renormalization ────────────────────────────────

def test_renormalize_topn_drops_ids_beyond_student_vocab():
    ids = [0, 1, 2, 3]
    probs = np.array([0.4, 0.3, 0.2, 0.1])
    result = renormalize_topn(ids, np.log(probs), student_max_id=1)
    assert result.kept_ids == [0, 1]
    assert result.captured_mass == pytest.approx(0.7)
    assert result.dropped_mass == pytest.approx(0.3)
    # after softmax renorm over the kept logprobs (which were log(0.4), log(0.3))
    assert result.probs.sum() == pytest.approx(1.0)


def test_renormalize_topn_no_drop_when_max_id_none():
    ids = [0, 1, 2]
    probs = np.array([0.5, 0.3, 0.2])
    result = renormalize_topn(ids, np.log(probs))
    assert result.kept_ids == ids
    assert result.captured_mass == pytest.approx(1.0)
    assert result.dropped_mass == pytest.approx(0.0)


# ── T-KD-3: captured_mass computation ──────────────────────────────────

def test_captured_mass_reflects_probability_not_renormalized_count():
    # Two low-probability ids dropped out of four -> small dropped_mass
    # even though half the *ids* were dropped.
    ids = [0, 1, 2, 3]
    probs = np.array([0.94, 0.03, 0.02, 0.01])
    result = renormalize_topn(ids, np.log(probs), student_max_id=1)
    assert result.captured_mass == pytest.approx(0.97, abs=1e-6)
    assert result.dropped_mass == pytest.approx(0.03, abs=1e-6)


def test_kd_loss_reports_captured_and_dropped_mass():
    ids = [0, 1, 2]
    probs = np.array([0.6, 0.3, 0.1])
    student_logits = np.array([0.0, 0.0, 0.0, 0.0])
    result = kd_loss(student_logits, ids, np.log(probs), sampled_id=0, student_max_id=1)
    assert result.captured_mass == pytest.approx(0.9)
    assert result.dropped_mass == pytest.approx(0.1)
