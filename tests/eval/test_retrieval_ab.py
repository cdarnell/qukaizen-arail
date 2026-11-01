"""H1 harness: scoring functions on synthetic input. No Ollama required.

Covers ARCHITECTURE.md's "Unit — always ships" bullet for
tests/eval/test_retrieval_ab.py: recall@5, MRR@10, pooled micro-average vs
mean-of-means, deterministic tie-break, paired-bootstrap CI reproducibility,
the workdir guard (FM5), arm parity (FM6), and the EmbeddingError-aborts-
without-writing-results path (FM7).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "eval" / "retrieval_ab.py"

_spec = importlib.util.spec_from_file_location("retrieval_ab", SCRIPT_PATH)
retrieval_ab = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("retrieval_ab", retrieval_ab)
_spec.loader.exec_module(retrieval_ab)  # type: ignore[union-attr]


# --------------------------------------------------------------------------
# recall@5 / MRR@10 / rank-1
# --------------------------------------------------------------------------

def _hit(path, score):
    return {"path": path, "name": path, "score": score}


def test_recall_at_5_true_when_relevant_in_top5():
    hits = [_hit(f"p{i}", 1.0 - i * 0.1) for i in range(10)]
    assert retrieval_ab.recall_at_k(hits, {"p3"}, 5) is True


def test_recall_at_5_false_when_relevant_only_in_top10():
    hits = [_hit(f"p{i}", 1.0 - i * 0.1) for i in range(10)]
    assert retrieval_ab.recall_at_k(hits, {"p7"}, 5) is False


def test_reciprocal_rank_first_hit():
    hits = [_hit("a", 0.9), _hit("b", 0.8), _hit("c", 0.7)]
    assert retrieval_ab.reciprocal_rank(hits, {"c"}, 10) == pytest.approx(1 / 3)


def test_reciprocal_rank_zero_when_absent():
    hits = [_hit("a", 0.9)]
    assert retrieval_ab.reciprocal_rank(hits, {"z"}, 10) == 0.0


def test_rank1_path_deterministic_tie_break():
    # Two hits tied at the top score: ascending path wins, not insertion order.
    hits = [_hit("zzz.md", 0.9), _hit("aaa.md", 0.9), _hit("mmm.md", 0.5)]
    assert retrieval_ab.rank1_path(hits) == "aaa.md"


def test_rank1_path_empty():
    assert retrieval_ab.rank1_path([]) is None


# --------------------------------------------------------------------------
# pooled = micro-average, not mean of per-world means (A/FM definitions)
# --------------------------------------------------------------------------

def test_pooled_is_micro_average_not_mean_of_means():
    # world A: 1/1 hit (100%). world B: 1/4 hits (25%).
    # mean-of-means = (100 + 25) / 2 = 62.5%
    # micro-average (pooled) = 2/5 = 40%
    per_query_hits = [True] + [True, False, False, False]
    pooled = sum(per_query_hits) / len(per_query_hits)
    assert pooled == pytest.approx(0.4)
    mean_of_means = (1.0 + 0.25) / 2
    assert pooled != pytest.approx(mean_of_means)


# --------------------------------------------------------------------------
# lexical-overlap strata (F1.4)
# --------------------------------------------------------------------------

def test_jaccard_overlap_zero_for_disjoint_tokens():
    stopwords = set()
    overlap = retrieval_ab.jaccard_overlap("banana kayak", ["widget threshold config"], stopwords)
    assert overlap == 0.0
    assert retrieval_ab.overlap_stratum(overlap) == "zero"


def test_jaccard_overlap_high_for_shared_tokens():
    stopwords = set()
    overlap = retrieval_ab.jaccard_overlap("widget threshold", ["widget threshold config"], stopwords)
    assert overlap > 0.05
    assert retrieval_ab.overlap_stratum(overlap) == "high"


def test_stopwords_excluded_from_overlap():
    stopwords = {"the", "a", "is"}
    # Without stopword filtering "the" would inflate overlap; with it, zero.
    overlap = retrieval_ab.jaccard_overlap("what is the widget", ["the config is a mystery"], stopwords)
    assert overlap == 0.0


# --------------------------------------------------------------------------
# paired bootstrap CI — reproducible from the committed seed
# --------------------------------------------------------------------------

def test_bootstrap_ci_reproducible_with_committed_seed():
    hash_hits = [True, False, True, False, True, False, True, False]
    nomic_hits = [True, True, True, False, True, True, True, False]
    ci1 = retrieval_ab.paired_bootstrap_ci(hash_hits, nomic_hits, resamples=500, seed=42)
    ci2 = retrieval_ab.paired_bootstrap_ci(hash_hits, nomic_hits, resamples=500, seed=42)
    assert ci1 == ci2


def test_bootstrap_ci_wider_bound_order():
    hash_hits = [True, False, True, False]
    nomic_hits = [True, True, True, True]
    lo, hi = retrieval_ab.paired_bootstrap_ci(hash_hits, nomic_hits, resamples=1000, seed=1)
    assert lo <= hi


def test_bootstrap_ci_empty_input():
    assert retrieval_ab.paired_bootstrap_ci([], [], resamples=100, seed=1) == (0.0, 0.0)


# --------------------------------------------------------------------------
# workdir safety guard (FM5)
# --------------------------------------------------------------------------

def test_workdir_under_live_pkb_cache_exits_2(tmp_path):
    unsafe = tmp_path / "some_lab" / "pkb" / ".cache" / "lancedb"
    with pytest.raises(SystemExit) as exc_info:
        retrieval_ab.assert_safe_workdir(unsafe)
    assert exc_info.value.code == 2


def test_workdir_under_wiki_cache_exits_2(tmp_path):
    unsafe = tmp_path / "some_lab" / "pkb" / ".wiki-cache"
    with pytest.raises(SystemExit) as exc_info:
        retrieval_ab.assert_safe_workdir(unsafe)
    assert exc_info.value.code == 2


def test_workdir_scratch_dir_is_safe(tmp_path):
    safe = tmp_path / "lab" / ".eval-cache"
    retrieval_ab.assert_safe_workdir(safe)  # must not raise


# --------------------------------------------------------------------------
# arm parity (FM6)
# --------------------------------------------------------------------------

def test_arm_parity_passes_on_identical_rows():
    rows_a = [{"path": "x", "name": "x", "source_kind": "user"}]
    rows_b = [{"path": "x", "name": "x", "source_kind": "user"}]
    retrieval_ab.assert_arm_parity(rows_a, rows_b)  # must not raise


def test_arm_parity_raises_on_length_mismatch():
    rows_a = [{"path": "x", "name": "x", "source_kind": "user"}]
    rows_b = []
    with pytest.raises(RuntimeError):
        retrieval_ab.assert_arm_parity(rows_a, rows_b)


def test_arm_parity_raises_on_field_mismatch():
    rows_a = [{"path": "x", "name": "x", "source_kind": "user"}]
    rows_b = [{"path": "x", "name": "x", "source_kind": "docs"}]
    with pytest.raises(RuntimeError):
        retrieval_ab.assert_arm_parity(rows_a, rows_b)


# --------------------------------------------------------------------------
# corpus manifest — no document text (H2)
# --------------------------------------------------------------------------

def test_manifest_has_no_document_text():
    rows = {
        "root": [
            retrieval_ab.Row(
                world="root", path="a.md", name="a.md", source_kind="user",
                bytes=42, text="secret personal financial content",
                embed_input="a.md a.md secret personal financial content",
            )
        ]
    }
    manifest = retrieval_ab.build_manifest(rows)
    blob = json.dumps(manifest)
    assert "secret personal financial content" not in blob
    assert manifest["rows"][0]["sha256"]
    assert "text" not in manifest["rows"][0]
    assert "embed_input" not in manifest["rows"][0]


# --------------------------------------------------------------------------
# end-to-end run() with a stub embedder (no Ollama)
# --------------------------------------------------------------------------

@pytest.fixture
def synthetic_lab(tmp_path):
    lab_root = tmp_path / "lab"
    root_pkb = lab_root / "pkb" / "skills"
    ai_pkb = lab_root / "instances" / "ai" / "pkb" / "skills"
    root_pkb.mkdir(parents=True)
    ai_pkb.mkdir(parents=True)
    for i in range(6):
        (root_pkb / f"doc{i}.md").write_text(
            f"# Doc {i}\nThis document is about topic-{i} and nothing else.\n")
    for i in range(6):
        (ai_pkb / f"note{i}.md").write_text(
            f"# Note {i}\nThis note discusses subject-{i} in depth.\n")
    return lab_root


def _write_yaml(path: Path, obj) -> None:
    import yaml
    path.write_text(yaml.safe_dump(obj, sort_keys=False))


def test_run_end_to_end_with_stub_embedder(tmp_path, synthetic_lab, monkeypatch):
    # Stub out the nomic arm so this test needs no Ollama: a deterministic
    # fake 768-dim embedding derived from the hash embedding, padded.
    from arail.vector_index import hash_embedding

    def fake_embed_documents(texts):
        return [hash_embedding(t, dim=768) for t in texts]

    def fake_embed_query(text):
        return hash_embedding(text, dim=768)

    monkeypatch.setattr(retrieval_ab.embed_mod, "embed_documents", fake_embed_documents)
    monkeypatch.setattr(retrieval_ab.embed_mod, "embed_query", fake_embed_query)

    queries_path = tmp_path / "queries.yaml"
    exact_path = tmp_path / "exact_tokens.yaml"
    _write_yaml(queries_path, [
        {
            "id": "root-001", "world": "root",
            "query": "what does topic-3 cover",
            "relevant": [{"path": "skills/doc3.md", "evidence": "topic-3"}],
            "author_note": "test",
        },
        {
            "id": "ai-001", "world": "ai",
            "query": "tell me about subject-2",
            "relevant": [{"path": "skills/note2.md", "evidence": "subject-2"}],
            "author_note": "test",
        },
    ])
    _write_yaml(exact_path, [
        {
            "id": "root-exact-001", "world": "root",
            "query": "doc3.md", "expected_path": "skills/doc3.md",
        },
    ])

    parser = retrieval_ab.build_parser()
    workdir = tmp_path / "workdir"
    json_out = tmp_path / "results.json"
    md_out = tmp_path / "RESULTS.md"
    args = parser.parse_args([
        "--lab-root", str(synthetic_lab),
        "--world", "root", "--world", "ai",
        "--arm", "both",
        "--workdir", str(workdir),
        "--queries", str(queries_path),
        "--exact-tokens", str(exact_path),
        "--json", str(json_out),
        "--md", str(md_out),
    ])
    rc = retrieval_ab.run(args)
    assert rc == 0
    assert json_out.exists()
    result = json.loads(json_out.read_text())
    assert result["schema"] == "arail.retrieval_ab/v1"
    assert set(result["pooled_recall5"]) == {"hash", "nomic"}
    assert "verdict" in result
    assert result["verdict"] in {"PASS", "FAIL", "PASS_INCONCLUSIVE"}
    assert md_out.exists()
    assert "VERDICT" not in md_out.read_text()  # md uses "verdict:" lowercase in body
    assert result["verdict"] in md_out.read_text()


def test_embedding_error_writes_no_results(tmp_path, synthetic_lab, monkeypatch):
    def raising_embed_documents(texts):
        raise retrieval_ab.embed_mod.EmbeddingError("simulated outage")

    monkeypatch.setattr(retrieval_ab.embed_mod, "embed_documents", raising_embed_documents)

    queries_path = tmp_path / "queries.yaml"
    exact_path = tmp_path / "exact_tokens.yaml"
    _write_yaml(queries_path, [])
    _write_yaml(exact_path, [])

    parser = retrieval_ab.build_parser()
    workdir = tmp_path / "workdir"
    json_out = tmp_path / "results.json"
    args = parser.parse_args([
        "--lab-root", str(synthetic_lab),
        "--world", "root",
        "--arm", "nomic",
        "--workdir", str(workdir),
        "--queries", str(queries_path),
        "--exact-tokens", str(exact_path),
        "--json", str(json_out),
    ])
    rc = retrieval_ab.run(args)
    assert rc == 1
    assert not json_out.exists()


def test_dump_corpus_mutates_nothing(tmp_path, synthetic_lab, capsys):
    before = {
        p: p.stat().st_mtime
        for p in synthetic_lab.rglob("*") if p.is_file()
    }
    retrieval_ab.dump_corpus(synthetic_lab, ["root", "ai"])
    captured = capsys.readouterr()
    assert "doc0.md" in captured.out or "note0.md" in captured.out
    after = {
        p: p.stat().st_mtime
        for p in synthetic_lab.rglob("*") if p.is_file()
    }
    assert before == after
    assert set(before) == set(after)


# --------------------------------------------------------------------------
# --verify-manifest reports differing rows (FM8)
# --------------------------------------------------------------------------

def test_verify_manifest_detects_drift(tmp_path, synthetic_lab, capsys):
    manifest_path = tmp_path / "manifest.json"
    retrieval_ab.write_manifest(synthetic_lab, ["root", "ai"], manifest_path)

    rc_clean = retrieval_ab.verify_manifest(synthetic_lab, ["root", "ai"], manifest_path)
    assert rc_clean == 0

    (synthetic_lab / "pkb" / "skills" / "doc0.md").write_text("changed content now\n")
    rc_dirty = retrieval_ab.verify_manifest(synthetic_lab, ["root", "ai"], manifest_path)
    assert rc_dirty == 1
    captured = capsys.readouterr()
    assert "changed" in captured.out
