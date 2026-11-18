"""The dormant opt-in catalog row (sprint 2026-11-18-aiml-slm-engine, T-C1/T-C2)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from arail import chat
from arail import model_pins as mp
from arail import model_resolve as mr
from arail import model_specs

FAMILY = "qkz-aiml-qwen3-1.7b"
CONTROL = f"{FAMILY}:control-merge"


def _row():
    return next(e for e in chat.load_catalog() if e.id == FAMILY)


def test_t_c1_row_is_dormant_and_never_downloads():
    e = _row()
    assert e.tier == "optional" and e.pin == FAMILY and e.family == "qwen"
    low = e.install.lower()
    assert "ollama pull" not in low and "ollama create" not in low and "http" not in low
    assert e.as_dict()["pin"] == FAMILY


def test_t_c1_row_is_not_directly_after_llama_ai_eng_and_never_promoted():
    ids = [e.id for e in chat.load_catalog()]
    assert ids[ids.index("llama-ai-eng") + 1] != FAMILY
    assert all(e.tier not in ("recommended", "flagship") for e in chat.load_catalog() if e.pin)
    assert FAMILY not in [e.id for e in chat.load_catalog() if e.tier == "recommended"]


def test_other_rows_have_no_pin():
    assert all(e.pin == "" for e in chat.load_catalog() if e.id != FAMILY)


def test_model_specs_override_is_under_the_ceiling():
    params, _ = model_specs.resolve_params_b(CONTROL)
    assert params == pytest.approx(2.03)


@pytest.fixture
def control_installed(monkeypatch):
    rows = [{"id": "llama-ai-eng:latest", "runtime": "ollama", "size_gb": 1.3},
            {"id": CONTROL, "runtime": "ollama", "size_gb": 1.4}]
    monkeypatch.setattr(chat, "detect_installed_models", lambda: rows)
    monkeypatch.setattr(chat, "_ollama_installed_models",
                        lambda *a, **k: [dict(r, modified="", endpoint="x") for r in rows])
    # a loopback Ollama that serves the control merge under its tag
    monkeypatch.setattr(mp, "_fetch_tags", lambda: {CONTROL: "m"})
    monkeypatch.setattr(mp, "_fetch_show", lambda t: {"modelfile": "FROM /x/sha256-" + "d6" * 32})
    mp.clear_caches()


def test_t_c2_gallery_marks_installed_control_as_not_the_pinned_build(control_installed):
    row = next(c for c in chat.gallery_view()["catalog"] if c["id"] == FAMILY)
    assert row["installed_state"] == "available"
    assert row["pin_state"] == "pending" and row["pickable"] is False
    assert "not released yet" in row["pin_note"]
    assert CONTROL in row["pin_note"]
    llama = next(c for c in chat.gallery_view()["catalog"] if c["id"] == "llama-ai-eng")
    assert "pin_state" not in llama


def test_t_c2_slot_a_candidates_carry_pin_state(control_installed, monkeypatch):
    rows = {r["id"]: r for r in mr.slot_a_candidates(False)}
    assert rows[CONTROL]["pickable"] is False and rows[CONTROL]["pin_state"] == "pending"
    assert "pin_state" not in rows["llama-ai-eng:latest"]


def test_notice_records_the_opt_in_base_without_a_rename_of_the_llama_default():
    notice = (Path(__file__).resolve().parents[1] / "NOTICE").read_text()
    assert re.search(r"5\. Opt-in house model \(not redistributed\)", notice)
    assert "Apache License, Version 2.0" in notice
