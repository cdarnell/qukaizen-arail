"""QA-6: pkb.retrieve_for_agents() distinguishes "the gate is empty, search
never ran" from "the search ran and found nothing" — both were a silent
zero before this sprint. search_for_agents() must survive unchanged as
retrieve_for_agents(...)["hits"]; tests/test_pkb_gate.py is untouched proof
of that.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arail import compiled_kb as ckb
from arail import pkb as pkb_mod


def _mk(root: Path, rel: str, text: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture()
def pkb(tmp_path):
    root = tmp_path / "pkb"
    root.mkdir()
    _mk(root, "notes/approved-note.md", "# Photosynthesis\nchlorophyll converts light")
    _mk(root, "notes/other-note.md", "# Something else entirely")
    return root


def test_gate_empty_reason_when_nothing_approved(pkb, monkeypatch):
    monkeypatch.delenv("ARAIL_APPROVED_ONLY", raising=False)
    out = pkb_mod.retrieve_for_agents("chlorophyll", pkb)
    assert out["hits"] == []
    assert out["empty_reason"] == "gate_empty"
    assert out["gate"]["state"] == "unbootstrapped"


def test_no_match_reason_when_gate_populated_but_query_misses(pkb, monkeypatch):
    monkeypatch.delenv("ARAIL_APPROVED_ONLY", raising=False)
    ckb.approve(["notes/approved-note.md"], pkb)
    out = pkb_mod.retrieve_for_agents("no-such-token-xyz", pkb)
    assert out["hits"] == []
    assert out["empty_reason"] == "no_match"
    assert out["gate"]["state"] == "populated"


def test_hits_when_match_found(pkb, monkeypatch):
    monkeypatch.delenv("ARAIL_APPROVED_ONLY", raising=False)
    ckb.approve(["notes/approved-note.md"], pkb)
    out = pkb_mod.retrieve_for_agents("chlorophyll", pkb)
    assert out["empty_reason"] is None
    assert {r["path"] for r in out["hits"]} == {"notes/approved-note.md"}


def test_gate_off_no_match_reason(pkb, monkeypatch):
    monkeypatch.setenv("ARAIL_APPROVED_ONLY", "off")
    out = pkb_mod.retrieve_for_agents("no-such-token-xyz", pkb)
    assert out["empty_reason"] == "gate_off_no_match"
    assert out["gate"]["state"] == "off"


def test_hits_byte_identical_to_search_for_agents(pkb, monkeypatch):
    monkeypatch.delenv("ARAIL_APPROVED_ONLY", raising=False)
    ckb.approve(["notes/approved-note.md"], pkb)
    hits = pkb_mod.search_for_agents("chlorophyll", pkb)
    out = pkb_mod.retrieve_for_agents("chlorophyll", pkb)
    assert hits == out["hits"]


def test_never_raises_on_internal_error(pkb, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(pkb_mod, "search", _boom)
    out = pkb_mod.retrieve_for_agents("chlorophyll", pkb)
    assert out["hits"] == []
    assert out["empty_reason"] == "gate_empty"
