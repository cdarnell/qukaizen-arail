"""Contamination check — cert vs train material (ARCHITECTURE.md §4.9).

13-gram 64-bit hashes of the CERT items are held in memory (O(cert)); the
student's actual training material (prompts + teacher outputs) is streamed
past them one document at a time, so this scales to a large train set
without holding it all in memory at once.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Set, Tuple

_WORD_RE = re.compile(r"\W+")
_N_GRAM = 13
_EXACT_MATCH_WEIGHT = 1.0
_NGRAM_OVERLAP_THRESHOLD = 0.5
_BOILERPLATE_DOC_FREQ_THRESHOLD = 0.05


def normalize(text: str) -> List[str]:
    lowered = (text or "").lower()
    tokens = [t for t in _WORD_RE.split(lowered) if t]
    return tokens


def _ngram_hash(tokens: Tuple[str, ...]) -> int:
    joined = " ".join(tokens).encode()
    return int.from_bytes(hashlib.sha256(joined).digest()[:8], "big")


def ngrams(tokens: List[str], n: int = _N_GRAM) -> Set[int]:
    if len(tokens) < n:
        return {_ngram_hash(tuple(tokens))} if tokens else set()
    return {_ngram_hash(tuple(tokens[i:i + n])) for i in range(len(tokens) - n + 1)}


def exact_sha(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()


@dataclass(frozen=True)
class ContaminationReport:
    method: str
    params: dict
    overlap: float
    temporal_leak: str  # "none" | "<first offending train item id>"
    top_offenders: List[str] = field(default_factory=list)

    @property
    def contaminated(self) -> bool:
        return self.overlap >= 0.01 or self.temporal_leak != "none"


class ContaminationChecker:
    """Construct once per cert set (holds its n-gram index), then stream
    train documents through ``observe_train_doc`` — the boilerplate
    stop-list needs a document-frequency pass, so ``check()`` does a
    second pass internally over the recorded per-doc n-gram sets rather
    than requiring the caller to buffer raw text twice."""

    def __init__(self, cert_items: List[dict], *, cutoff: str):
        self.cutoff = cutoff
        self._cert_shas: Dict[str, str] = {}
        self._cert_ngrams: Dict[str, Set[int]] = {}
        for item in cert_items:
            tokens = normalize(item.get("text", ""))
            self._cert_shas[item["id"]] = exact_sha(item.get("text", ""))
            self._cert_ngrams[item["id"]] = ngrams(tokens)

        self._train_docs: List[Tuple[str, Set[int], str]] = []  # (text_sha, ngram_set, date)
        self._ngram_doc_freq: Dict[int, int] = {}

    def observe_train_doc(self, text: str, *, date: str = "") -> None:
        tokens = normalize(text)
        doc_ngrams = ngrams(tokens)
        for g in doc_ngrams:
            self._ngram_doc_freq[g] = self._ngram_doc_freq.get(g, 0) + 1
        self._train_docs.append((exact_sha(text), doc_ngrams, date))

    def check(self) -> ContaminationReport:
        n_train = max(len(self._train_docs), 1)
        boilerplate = {g for g, freq in self._ngram_doc_freq.items()
                       if freq / n_train > _BOILERPLATE_DOC_FREQ_THRESHOLD}

        train_shas = {sha for sha, _, _ in self._train_docs}
        train_ngrams_union: Set[int] = set()
        for _, doc_ngrams, _ in self._train_docs:
            train_ngrams_union |= (doc_ngrams - boilerplate)

        temporal_leak = "none"
        for _, _, date in self._train_docs:
            if date and date > self.cutoff:
                temporal_leak = date
                break

        offenders: List[str] = []
        contaminated_count = 0
        for item_id, cert_sha in self._cert_shas.items():
            exact = cert_sha in train_shas
            cert_ngrams = self._cert_ngrams[item_id] - boilerplate
            if cert_ngrams:
                overlap_ratio = len(cert_ngrams & train_ngrams_union) / len(cert_ngrams)
            else:
                overlap_ratio = 0.0
            is_contaminated = exact or overlap_ratio >= _NGRAM_OVERLAP_THRESHOLD
            if is_contaminated:
                contaminated_count += 1
                offenders.append(item_id)

        n_cert = max(len(self._cert_shas), 1)
        overlap = contaminated_count / n_cert

        return ContaminationReport(
            method="13gram+exact-sha",
            params={"n_gram": _N_GRAM, "ngram_overlap_threshold": _NGRAM_OVERLAP_THRESHOLD,
                   "boilerplate_doc_freq_threshold": _BOILERPLATE_DOC_FREQ_THRESHOLD,
                   "n_train_docs": len(self._train_docs), "n_cert": len(self._cert_shas)},
            overlap=overlap, temporal_leak=temporal_leak,
            top_offenders=sorted(offenders)[:10],
        )


def check(cert_items: List[dict], train_docs: Iterable[Tuple[str, str]], *, cutoff: str) -> ContaminationReport:
    """Convenience one-shot wrapper: ``train_docs`` is an iterable of
    (text, date) pairs."""
    checker = ContaminationChecker(cert_items, cutoff=cutoff)
    for text, date in train_docs:
        checker.observe_train_doc(text, date=date)
    return checker.check()
