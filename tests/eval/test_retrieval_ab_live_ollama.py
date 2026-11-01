"""H1 harness end-to-end against a real, reachable Ollama.

ARCHITECTURE.md "Integration — always ships": a 12-row temp PKB, both arms
(hash + real nomic-embed-text), results.json validates against
`arail.retrieval_ab/v1`, verdict line present. Auto-skipped when no Ollama
is reachable (FM18) via tests/eval/conftest.py.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "eval" / "retrieval_ab.py"

_spec = importlib.util.spec_from_file_location("retrieval_ab_live", SCRIPT_PATH)
retrieval_ab = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("retrieval_ab_live", retrieval_ab)
_spec.loader.exec_module(retrieval_ab)  # type: ignore[union-attr]


@pytest.fixture
def tiny_lab(tmp_path):
    lab_root = tmp_path / "lab"
    root_pkb = lab_root / "pkb" / "skills"
    ai_pkb = lab_root / "instances" / "ai" / "pkb" / "skills"
    root_pkb.mkdir(parents=True)
    ai_pkb.mkdir(parents=True)
    for i in range(6):
        (root_pkb / f"doc{i}.md").write_text(
            f"# Doc {i}\nThis document is entirely about topic-{i} and covers "
            f"nothing else in this twelve-row corpus.\n")
    for i in range(6):
        (ai_pkb / f"note{i}.md").write_text(
            f"# Note {i}\nThis note is entirely about subject-{i} and covers "
            f"nothing else in this twelve-row corpus.\n")
    return lab_root


def _write_yaml(path: Path, obj) -> None:
    path.write_text(yaml.safe_dump(obj, sort_keys=False))


@pytest.mark.requires_ollama
def test_end_to_end_real_ollama_produces_valid_results(tmp_path, tiny_lab):
    queries_path = tmp_path / "queries.yaml"
    exact_path = tmp_path / "exact_tokens.yaml"
    _write_yaml(queries_path, [
        {
            "id": "root-001", "world": "root",
            "query": "what does topic-3 cover",
            "relevant": [{"path": "skills/doc3.md", "evidence": "topic-3"}],
            "author_note": "live-ollama smoke test",
        },
        {
            "id": "ai-001", "world": "ai",
            "query": "tell me about subject-2",
            "relevant": [{"path": "skills/note2.md", "evidence": "subject-2"}],
            "author_note": "live-ollama smoke test",
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
        "--lab-root", str(tiny_lab),
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
    result = json.loads(json_out.read_text())
    assert result["schema"] == "arail.retrieval_ab/v1"
    assert result["embedding_model"] == "nomic-embed-text"
    assert result["embedding_dim"] == 768
    assert result["verdict"] in {"PASS", "FAIL", "PASS_INCONCLUSIVE"}
    assert "delta_pp" in result
    assert "bootstrap_ci_95" in result
    md_text = md_out.read_text()
    assert "verdict" in md_text
