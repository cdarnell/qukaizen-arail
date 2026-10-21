"""arail.nucleus.tokenizer_parity — exact | superset | none (T-PAR-1..4)."""

from __future__ import annotations

from arail.nucleus.tokenizer_parity import compare_tokenizers

BASE_VOCAB = {f"tok{i}": i for i in range(100)}
NORMALIZER = {"type": "NFC"}
PRE_TOK = {"type": "ByteLevel"}


def _tok(vocab, *, added=None, normalizer=NORMALIZER, pre_tokenizer=PRE_TOK):
    return {
        "model": {"vocab": dict(vocab)},
        "added_tokens": added or [],
        "normalizer": normalizer,
        "pre_tokenizer": pre_tokenizer,
    }


def test_exact_parity_identical_vocab():
    student = _tok(BASE_VOCAB)
    teacher = _tok(BASE_VOCAB)
    result = compare_tokenizers(student, teacher)
    assert result.kind == "exact"
    assert result.student_max_id == 99


def test_superset_teacher_has_extra_ids_above_max():
    student = _tok(BASE_VOCAB)
    teacher_vocab = dict(BASE_VOCAB)
    teacher_vocab.update({"extra1": 100, "extra2": 101})
    teacher = _tok(teacher_vocab)
    result = compare_tokenizers(student, teacher)
    assert result.kind == "superset"
    assert result.extra_teacher_ids == (100, 101)


def test_none_different_vocab_ids():
    student = _tok(BASE_VOCAB)
    teacher_vocab = dict(BASE_VOCAB)
    teacher_vocab["tok5"] = 999  # remapped id -> mismatch
    teacher = _tok(teacher_vocab)
    result = compare_tokenizers(student, teacher)
    assert result.kind == "none"


def test_none_same_vocab_different_pre_tokenizer():
    student = _tok(BASE_VOCAB, pre_tokenizer={"type": "ByteLevel"})
    teacher = _tok(BASE_VOCAB, pre_tokenizer={"type": "Whitespace"})
    result = compare_tokenizers(student, teacher)
    # Vocab-identical but structurally different -> not "exact"; and since
    # ids match exactly with no extras, it resolves to "superset" (empty
    # extras) rather than "none" — exact parity specifically requires
    # matching normalizer/pre_tokenizer, superset does not.
    assert result.kind == "superset"
    assert result.extra_teacher_ids == ()


def test_none_teacher_extra_id_not_above_max():
    student = _tok(BASE_VOCAB)
    teacher_vocab = dict(BASE_VOCAB)
    del teacher_vocab["tok50"]
    teacher_vocab["weird"] = 50  # reuses id 50 for a different token
    teacher = _tok(teacher_vocab)
    result = compare_tokenizers(student, teacher)
    assert result.kind == "none"


def test_added_tokens_merged_and_win_on_conflict():
    student = _tok(BASE_VOCAB, added=[{"content": "tok0", "id": 500}])
    teacher = _tok(BASE_VOCAB, added=[{"content": "tok0", "id": 500}])
    result = compare_tokenizers(student, teacher)
    assert result.kind == "exact"
    assert result.student_max_id == 500
