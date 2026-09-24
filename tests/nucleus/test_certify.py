"""arail.nucleus.certify — end-to-end against the stub pipeline, plus the
contamination refusal path."""

from __future__ import annotations

import json

import pytest

from arail.nucleus import build as build_mod


def _run_phase(context, phase):
    """run_phase_body() + persist_phase_output() — mirrors what
    worker.py does for a real subprocess phase (B9: certify reads
    fuse.json from phase_output/), for tests that call phase bodies
    in-process rather than through a spawned worker."""
    output = build_mod.run_phase_body(phase, context)
    build_mod.persist_phase_output(context, phase, output)
    return output


from arail.nucleus import certify as certify_mod
from arail.nucleus.errors import RefusedByPolicy

# Reuse the staged_context fixture from test_build_phases.py.
from tests.nucleus.test_build_phases import staged_context  # noqa: F401


def test_certify_end_to_end_produces_signed_card(staged_context, tmp_path, monkeypatch):
    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(tmp_path / "signing.ed25519"))

    _run_phase(staged_context, "PA")
    _run_phase(staged_context, "PA2")
    _run_phase(staged_context, "PB")
    _run_phase(staged_context, "fuse")
    _run_phase(staged_context, "PC")

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


# ── B1 (2026-09-23 review): a stub build can never be laundered into a
# trusted, ledgered, "real" card by running `certify` in a different
# environment than the build ran in ─────────────────────────────────

def test_certify_refuses_when_env_disagrees_with_build_record(staged_context, tmp_path, monkeypatch):
    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(tmp_path / "signing.ed25519"))

    # The build ran with ARAIL_NUCLEUS_STUB=1 (staged_context's fixture
    # sets it) -- context.json records stub=True. Reproduce the review's
    # exact repro: certify the same build_id with the env var unset.
    _run_phase(staged_context, "PA")
    _run_phase(staged_context, "PA2")
    _run_phase(staged_context, "PB")
    _run_phase(staged_context, "fuse")
    _run_phase(staged_context, "PC")

    rd = build_mod._run_dir(staged_context)
    (rd / "context.json").write_text(json.dumps(staged_context, sort_keys=True))

    monkeypatch.delenv("ARAIL_NUCLEUS_STUB", raising=False)

    with pytest.raises(RefusedByPolicy, match="stub"):
        certify_mod.run_certify(staged_context["build_id"])  # context=None -> reads context.json from disk

    # No card was laundered as "real", trusted, and ledgered.
    assert not any(rd.rglob("dna-card.yaml"))
    from arail.nucleus.cards import certified_models
    ledger = certified_models._default_local_path(build_mod._nucleus_data(staged_context))
    assert not ledger.exists() or "kernel" not in ledger.read_text()


def test_certify_refuses_on_build_record_missing_stub_field(staged_context, tmp_path, monkeypatch):
    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(tmp_path / "signing.ed25519"))
    _run_phase(staged_context, "PA")
    _run_phase(staged_context, "PA2")
    _run_phase(staged_context, "PB")
    _run_phase(staged_context, "fuse")
    _run_phase(staged_context, "PC")

    legacy_context = dict(staged_context)
    del legacy_context["stub"]

    with pytest.raises(RefusedByPolicy, match="stub"):
        certify_mod.run_certify(staged_context["build_id"], context=legacy_context)


def test_certify_refuses_on_contamination(staged_context, tmp_path, monkeypatch):
    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(tmp_path / "signing.ed25519"))

    _run_phase(staged_context, "PA")
    _run_phase(staged_context, "PA2")
    _run_phase(staged_context, "PB")
    _run_phase(staged_context, "fuse")
    _run_phase(staged_context, "PC")

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
