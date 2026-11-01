"""F1/F2 fixture lint — the mechanical, reviewer-repeatable check.

This is a lint, not a warning (ARCHITECTURE.md F1): a bad fixture fails the
build. Covers FM1 (overlap-stratum floor), FM2 (verbatim evidence), FM4
(PII), and the schema/coverage promises of F1 and F2. Reads the live corpus
read-only through the same harness code the fixture was authored against.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = REPO_ROOT / "eval" / "retrieval"
LAB_ROOT = Path("/Users/netsushi/ProJects/qukaizen-arail/lab")
SCRIPT_PATH = REPO_ROOT / "scripts" / "eval" / "retrieval_ab.py"

_spec = importlib.util.spec_from_file_location("retrieval_ab", SCRIPT_PATH)
retrieval_ab = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("retrieval_ab", retrieval_ab)
_spec.loader.exec_module(retrieval_ab)  # type: ignore[union-attr]


pytestmark = pytest.mark.skipif(
    not LAB_ROOT.exists(),
    reason="fixture lint reads the primary checkout's live lab/ corpus (A1); "
           "not present on this machine",
)


def _load_yaml(name: str):
    path = EVAL_DIR / name
    return yaml.safe_load(path.read_text())


@pytest.fixture(scope="module")
def queries():
    return _load_yaml("queries.yaml")


@pytest.fixture(scope="module")
def exact_tokens():
    return _load_yaml("exact_tokens.yaml")


@pytest.fixture(scope="module")
def rows_index():
    rows_by_world = retrieval_ab.read_all_rows(LAB_ROOT, retrieval_ab.WORLDS)
    index = {}
    for world, rows in rows_by_world.items():
        for r in rows:
            index[(world, r.path)] = r
    return index


@pytest.fixture(scope="module")
def stopwords():
    return retrieval_ab._load_stopwords()


@pytest.fixture(scope="module")
def deny_patterns():
    path = EVAL_DIR / "pii_deny.txt"
    return [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


# --------------------------------------------------------------------------
# schema / coverage (F1, F2)
# --------------------------------------------------------------------------

def test_at_least_30_nl_queries(queries):
    assert len(queries) >= 30


def test_at_least_6_per_world(queries):
    from collections import Counter
    counts = Counter(q["world"] for q in queries)
    for world in retrieval_ab.WORLDS:
        assert counts.get(world, 0) >= 6, f"{world} has {counts.get(world, 0)} queries, need >=6"


def test_at_least_8_exact_token_queries(exact_tokens):
    assert len(exact_tokens) >= 8


def test_exact_token_queries_have_exactly_one_expected_path(exact_tokens):
    for q in exact_tokens:
        assert isinstance(q["expected_path"], str)
        assert q["expected_path"]


def test_unique_query_ids(queries):
    ids = [q["id"] for q in queries]
    assert len(ids) == len(set(ids)), "duplicate ids in queries.yaml"


def test_unique_exact_token_ids(exact_tokens):
    ids = [q["id"] for q in exact_tokens]
    assert len(ids) == len(set(ids)), "duplicate ids in exact_tokens.yaml"


def test_every_query_has_at_least_one_relevant_doc(queries):
    for q in queries:
        assert len(q["relevant"]) >= 1, f"{q['id']} has no relevant docs"


def test_query_worlds_are_valid(queries, exact_tokens):
    for q in queries:
        assert q["world"] in retrieval_ab.WORLDS
    for q in exact_tokens:
        assert q["world"] in retrieval_ab.WORLDS


# --------------------------------------------------------------------------
# FM2 — every relevant.path exists in the corpus, every evidence is verbatim
# --------------------------------------------------------------------------

def test_relevant_paths_exist_in_corpus(queries, rows_index):
    for q in queries:
        for rel in q["relevant"]:
            key = (q["world"], rel["path"])
            assert key in rows_index, (
                f"{q['id']}: relevant path {rel['path']!r} not found in "
                f"world {q['world']!r}'s corpus")


def test_exact_token_expected_paths_exist_in_corpus(exact_tokens, rows_index):
    for q in exact_tokens:
        key = (q["world"], q["expected_path"])
        assert key in rows_index, (
            f"{q['id']}: expected_path {q['expected_path']!r} not found in "
            f"world {q['world']!r}'s corpus")


def test_evidence_is_verbatim_in_the_labelled_file(queries, rows_index):
    """The mechanical FM2 check: re-read the file, assert byte-for-byte
    presence. This is what a reviewer repeats in one command."""
    for q in queries:
        for rel in q["relevant"]:
            key = (q["world"], rel["path"])
            row = rows_index.get(key)
            assert row is not None
            assert rel["evidence"] in row.text, (
                f"{q['id']}: evidence for {rel['path']!r} is not a verbatim "
                f"substring of the file")


def test_evidence_is_at_most_200_chars(queries):
    for q in queries:
        for rel in q["relevant"]:
            assert len(rel["evidence"]) <= 200, (
                f"{q['id']}: evidence for {rel['path']!r} is "
                f"{len(rel['evidence'])} chars, must be <=200")


# --------------------------------------------------------------------------
# FM1 — overlap-stratum floor (>=25% zero, >=25% outside zero)
# --------------------------------------------------------------------------

def test_overlap_stratum_floor(queries, rows_index, stopwords):
    counts = {"zero": 0, "low": 0, "high": 0}
    for q in queries:
        doc_texts = [
            rows_index[(q["world"], r["path"])].embed_input
            for r in q["relevant"] if (q["world"], r["path"]) in rows_index
        ]
        overlap = retrieval_ab.jaccard_overlap(q["query"], doc_texts, stopwords)
        counts[retrieval_ab.overlap_stratum(overlap)] += 1
    total = len(queries)
    zero_pct = counts["zero"] / total
    nonzero_pct = (counts["low"] + counts["high"]) / total
    assert zero_pct >= 0.25, f"zero-overlap stratum is {zero_pct:.1%}, need >=25% ({counts})"
    assert nonzero_pct >= 0.25, f"non-zero-overlap stratum is {nonzero_pct:.1%}, need >=25% ({counts})"


# --------------------------------------------------------------------------
# FM4 — PII lint on committed excerpts
# --------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_DIGIT_RUN_RE = re.compile(r"\d{6,}")
_CURRENCY_RE = re.compile(r"\$\s?\d[\d,]*(\.\d+)?")


def _significant_digits(s: str) -> int:
    return len(re.sub(r"\D", "", s))


def _pii_violations(text: str, deny_patterns: list[str]) -> list[str]:
    violations = []
    if _EMAIL_RE.search(text):
        violations.append("email address")
    if _DIGIT_RUN_RE.search(text):
        violations.append("digit run >= 6")
    for m in _CURRENCY_RE.finditer(text):
        if _significant_digits(m.group()) >= 4:
            violations.append("currency amount with >=4 significant digits")
    for pat in deny_patterns:
        if re.search(pat, text, re.IGNORECASE):
            violations.append(f"pii_deny pattern: {pat}")
    return violations


def test_no_pii_in_evidence_excerpts(queries, deny_patterns):
    for q in queries:
        for rel in q["relevant"]:
            violations = _pii_violations(rel["evidence"], deny_patterns)
            assert not violations, f"{q['id']}: PII lint failed on evidence: {violations}"


def test_no_pii_in_author_notes(queries, deny_patterns):
    for q in queries:
        note = q.get("author_note", "")
        violations = _pii_violations(note, deny_patterns)
        assert not violations, f"{q['id']}: PII lint failed on author_note: {violations}"


def test_debt_finance_research_program_not_used_as_evidence_source(queries):
    """Explicit guard for the file the operator flagged: even though a
    manual read found it currently holds generic seed content (not personal
    data — see BUILD_LOG.md), it must never be an evidence source in this
    public fixture."""
    for q in queries:
        for rel in q["relevant"]:
            assert rel["path"] != "research/program.md" or q["world"] != "debt-finance", (
                f"{q['id']}: debt-finance research/program.md must not be "
                f"used as a fixture evidence source")


# --------------------------------------------------------------------------
# FM3 — pre-registration is checked by the reviewer via git log, not here.
# See ARCHITECTURE.md F1.3: "git log --follow eval/retrieval/queries.yaml
# must show no edit after the first results commit." BUILD_LOG.md records
# the fixture commit sha and (if any) the results commit sha.
# --------------------------------------------------------------------------
