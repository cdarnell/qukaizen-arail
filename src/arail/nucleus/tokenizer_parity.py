"""Tokenizer parity — exact | superset | none (ARCHITECTURE.md §4.4).

Reads each model's ``tokenizer.json`` (the standard HuggingFace fast-
tokenizer format) and compares vocabularies. This governs whether a
teacher can be a logit-KD source for a given student at all (F6): `none`
in logit mode is always a preflight refusal, never a silent fallback to
sequence-level KD (VISION disconfirming #1).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Tuple


@dataclass(frozen=True)
class Parity:
    kind: str  # "exact" | "superset" | "none"
    student_max_id: int
    extra_teacher_ids: Tuple[int, ...]
    detail: str


def _load_tokenizer_json(path: Path) -> dict:
    tok_path = path / "tokenizer.json"
    if not tok_path.is_file():
        raise FileNotFoundError(f"no tokenizer.json at {tok_path}")
    return json.loads(tok_path.read_text())


def _vocab_map(tok: dict) -> Dict[str, int]:
    """Base vocab (model.vocab) merged with added_tokens, added_tokens
    winning on id conflicts (mirrors how a real tokenizer resolves them)."""
    vocab = dict(tok.get("model", {}).get("vocab", {}) or {})
    for entry in tok.get("added_tokens", []) or []:
        content = entry.get("content")
        tid = entry.get("id")
        if content is not None and tid is not None:
            vocab[content] = tid
    return vocab


def _structural_fields(tok: dict) -> Tuple[Any, Any]:
    return tok.get("normalizer"), tok.get("pre_tokenizer")


def compare_tokenizers(student_tok: dict, teacher_tok: dict) -> Parity:
    student_vocab = _vocab_map(student_tok)
    teacher_vocab = _vocab_map(teacher_tok)
    student_max_id = max(student_vocab.values()) if student_vocab else -1

    if student_vocab == teacher_vocab and _structural_fields(student_tok) == _structural_fields(teacher_tok):
        return Parity("exact", student_max_id, (), "identical vocab, normalizer, and pre_tokenizer")

    # superset check: every student token maps to the same id in teacher,
    # and every id the teacher has beyond the student's vocab is > student_max_id.
    mismatched = [tok for tok, tid in student_vocab.items()
                 if teacher_vocab.get(tok) != tid]
    if mismatched:
        return Parity("none", student_max_id, (),
                      f"{len(mismatched)} student token(s) map to a different id "
                      f"(or are absent) in the teacher, e.g. {mismatched[0]!r}")

    extra_ids = sorted(tid for tok, tid in teacher_vocab.items() if tok not in student_vocab)
    if extra_ids and extra_ids[0] <= student_max_id:
        return Parity("none", student_max_id, tuple(extra_ids),
                      f"teacher has extra token id {extra_ids[0]} <= student_max_id "
                      f"{student_max_id} — ids are not a clean superset")

    return Parity("superset", student_max_id, tuple(extra_ids),
                  f"student vocab is a subset; {len(extra_ids)} teacher-only id(s) beyond student_max_id")


def parity(student_dir: Path, teacher_dir: Path) -> Parity:
    student_tok = _load_tokenizer_json(Path(student_dir))
    teacher_tok = _load_tokenizer_json(Path(teacher_dir))
    return compare_tokenizers(student_tok, teacher_tok)
