"""arail.nucleus.evals.open_lc_judge (T-JUDGE-1..4 + bootstrap determinism)."""

from __future__ import annotations

import pytest

from arail.nucleus.evals.open_lc_judge import (
    JudgeIsTeacher, PairItem, assert_judge_identity_distinct, randomize_and_judge, score,
)


# ── T-JUDGE-1: judge == teacher (or student base) -> JudgeIsTeacher ────

def test_judge_equals_teacher_raises_before_generation():
    with pytest.raises(JudgeIsTeacher):
        assert_judge_identity_distinct("sha-teacher", teacher_identity="sha-teacher",
                                       student_base_identity="sha-base")


def test_judge_equals_student_base_raises():
    with pytest.raises(JudgeIsTeacher):
        assert_judge_identity_distinct("sha-base", teacher_identity="sha-teacher",
                                       student_base_identity="sha-base")


def test_judge_equals_fused_student_raises():
    with pytest.raises(JudgeIsTeacher):
        assert_judge_identity_distinct("sha-fused", teacher_identity="sha-teacher",
                                       student_base_identity="sha-base",
                                       fused_student_identity="sha-fused")


def test_judge_distinct_from_everything_passes():
    assert_judge_identity_distinct("sha-judge", teacher_identity="sha-teacher",
                                   student_base_identity="sha-base",
                                   fused_student_identity="sha-fused")  # no raise


def test_identity_check_is_by_content_not_name():
    # Same content hash under a different "name" (alias) still trips it --
    # the whole point is this can't be aliased around.
    with pytest.raises(JudgeIsTeacher):
        assert_judge_identity_distinct("same-hash", teacher_identity="same-hash",
                                       student_base_identity="different-hash")


# ── T-JUDGE-2: position randomization ~50/50 over 1000 seeded items ────

def test_position_randomization_balanced_over_1000_items():
    items = [PairItem(f"item-{i}", "model text", "baseline text") for i in range(1000)]
    judged = randomize_and_judge(items, seed=42, judge_fn=lambda item_id, a, b: "A")
    n_a = sum(1 for j in judged if j.order[0] == "model")
    ratio = n_a / len(judged)
    assert 0.45 < ratio < 0.55


def test_position_randomization_recorded_per_item():
    items = [PairItem("x", "model text", "baseline text")]
    judged = randomize_and_judge(items, seed=1, judge_fn=lambda i, a, b: "A")
    assert judged[0].order in (("model", "baseline"), ("baseline", "model"))


# ── T-JUDGE-3: judge that always prefers the longer answer, equal quality ──

def test_length_control_corrects_pure_length_bias():
    # Equal-quality pairs: the judge is a pure length-bias oracle (always
    # picks whichever side is longer). Give "model" a longer text half the
    # time and shorter the other half, so raw win rate is driven ONLY by
    # who happened to be longer -- but since we always make "model" the
    # longer one here, raw win rate is near 1.0. LC should reveal the
    # length-neutral win rate is close to 0.5 (equal quality).
    items = []
    for i in range(200):
        items.append(PairItem(f"item-{i}", "long " * 50, "short " * 5))

    def length_biased_judge(item_id, text_a, text_b):
        return "A" if len(text_a) >= len(text_b) else "B"

    judged = randomize_and_judge(items, seed=7, judge_fn=length_biased_judge)
    len_model = {it.item_id: len(it.text_model) for it in items}
    len_baseline = {it.item_id: len(it.text_baseline) for it in items}
    result = score(judged, len_model=len_model, len_baseline=len_baseline, seed=7)

    assert result.raw_win_rate > 0.9   # "model" (always longer) wins almost every raw comparison
    assert 0.5 - 0.05 <= result.lc_win_rate <= 0.5 + 0.15  # length-controlled reveals near-parity


# ── T-JUDGE-4: invalid outputs -> unreliable ────────────────────────

def test_invalid_rate_above_10pct_marks_unreliable():
    items = [PairItem(f"item-{i}", "m", "b") for i in range(20)]

    def flaky_judge(item_id, a, b):
        idx = int(item_id.split("-")[1])
        return "A" if idx % 5 != 0 else "garbage output"  # 4/20 = 20% invalid

    judged = randomize_and_judge(items, seed=3, judge_fn=flaky_judge)
    result = score(judged, len_model={it.item_id: 1 for it in items},
                  len_baseline={it.item_id: 1 for it in items}, seed=3)
    assert result.invalid_rate == pytest.approx(0.2)
    assert result.unreliable is True


def test_invalid_rate_below_10pct_not_unreliable():
    items = [PairItem(f"item-{i}", "m", "b") for i in range(100)]

    def mostly_valid_judge(item_id, a, b):
        idx = int(item_id.split("-")[1])
        return "garbage" if idx == 0 else "A"  # 1% invalid

    judged = randomize_and_judge(items, seed=3, judge_fn=mostly_valid_judge)
    result = score(judged, len_model={it.item_id: 1 for it in items},
                  len_baseline={it.item_id: 1 for it in items}, seed=3)
    assert result.unreliable is False


# ── bootstrap CI determinism ───────────────────────────────────────────

def test_bootstrap_ci_deterministic_for_fixed_seed():
    items = [PairItem(f"item-{i}", "m", "b") for i in range(50)]
    judged = randomize_and_judge(items, seed=9, judge_fn=lambda i, a, b: "A")
    len_model = {it.item_id: 10 for it in items}
    len_baseline = {it.item_id: 10 for it in items}
    r1 = score(judged, len_model=len_model, len_baseline=len_baseline, seed=99)
    r2 = score(judged, len_model=len_model, len_baseline=len_baseline, seed=99)
    assert r1.ci95 == r2.ci95


def test_all_invalid_gives_zero_win_rate_and_unreliable():
    items = [PairItem(f"item-{i}", "m", "b") for i in range(5)]
    judged = randomize_and_judge(items, seed=1, judge_fn=lambda i, a, b: "nope")
    result = score(judged, len_model={it.item_id: 1 for it in items},
                  len_baseline={it.item_id: 1 for it in items}, seed=1)
    assert result.raw_win_rate == 0.0
    assert result.unreliable is True
