"""arail.nucleus.certify — end-to-end against the stub pipeline, plus the
contamination refusal path."""

from __future__ import annotations

import json

import pytest

from arail.nucleus import build as build_mod
from arail.nucleus import certify as certify_mod
from arail.nucleus.errors import RefusedByPolicy

# Reuse the staged_context fixture from test_build_phases.py.
from tests.nucleus.test_build_phases import staged_context  # noqa: F401


def test_certify_end_to_end_produces_signed_card(staged_context, tmp_path, monkeypatch):
    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(tmp_path / "signing.ed25519"))

    build_mod.run_phase_body("PA", staged_context)
    build_mod.run_phase_body("PA2", staged_context)
    build_mod.run_phase_body("PB", staged_context)
    build_mod.run_phase_body("fuse", staged_context)
    build_mod.run_phase_body("PC", staged_context)

    result = certify_mod.run_certify(staged_context["build_id"], context=staged_context)

    assert result["decision"] in ("CERTIFIED", "COMPATIBLE", "BETA", "KNOWN_ISSUE")
    from pathlib import Path

    card_path = Path(result["card_dir"]) / "dna-card.yaml"
    assert card_path.is_file()
    seal_path = Path(result["card_dir"]) / "seal.json"
    assert seal_path.is_file()
    report_path = Path(result["card_dir"]) / "build-report.md"
    assert report_path.is_file()

    from arail.nucleus.cards.dna_v2 import load_card

    card = load_card(card_path)
    assert card["signed"]["format"] == "nucleus-seal/legacy-5"
    assert card["runtime"] == "stub"

    # Stub builds are valid local artifacts but are never ledgered
    # (T-STUB-2 / T-LEDGER-3) -- card/seal/report still get written above.
    assert result["ledger_path"] is None


def test_certify_refuses_on_contamination(staged_context, tmp_path, monkeypatch):
    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(tmp_path / "signing.ed25519"))

    build_mod.run_phase_body("PA", staged_context)
    build_mod.run_phase_body("PA2", staged_context)
    build_mod.run_phase_body("PB", staged_context)
    build_mod.run_phase_body("fuse", staged_context)
    build_mod.run_phase_body("PC", staged_context)

    # Force contamination by monkeypatching the checker (imported lazily
    # inside run_certify, so patch it at its source module) to always
    # report a dirty result -- proves certify never writes a card when refused.
    import arail.nucleus.evals.contamination as contamination_module
    from arail.nucleus.evals.contamination import ContaminationReport

    class _AlwaysDirty:
        def __init__(self, *a, **kw):
            pass

        def observe_train_doc(self, *a, **kw):
            pass

        def check(self):
            return ContaminationReport(method="fake", params={}, overlap=0.5,
                                       temporal_leak="none", top_offenders=["x"])

    monkeypatch.setattr(contamination_module, "ContaminationChecker", _AlwaysDirty)

    from pathlib import Path

    with pytest.raises(RefusedByPolicy, match="contamination"):
        certify_mod.run_certify(staged_context["build_id"], context=staged_context)

    # No card written anywhere under the run dir.
    rd = build_mod._run_dir(staged_context)
    assert not any(rd.rglob("dna-card.yaml"))
