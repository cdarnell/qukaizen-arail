"""arail.nucleus.certify — end-to-end against the stub pipeline, plus the
contamination refusal path."""

from __future__ import annotations

import json
from pathlib import Path

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

    # B3 item 3 ("KNOWN_ISSUE can not be reached"): the fused and base
    # students score IDENTICALLY here (both use the default StubProvider
    # with the same deterministic fallback text -- the fixture never
    # differentiates the fused shard from base), so a real, non-tied-break
    # beats_base comparison correctly reads False and KNOWN_ISSUE is the
    # genuinely reached decision -- not hardcoded, not unreachable.
    assert result["decision"] == "KNOWN_ISSUE"
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


# ── B3 (2026-09-23 review): no hardcoded metric/provenance value in a
# signed card; PC scores the fused student AND the base; beats_base and
# pipeline_hash/training_hash are real ─────────────────────────────

def test_certify_card_has_no_placeholder_values(staged_context, tmp_path, monkeypatch):
    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(tmp_path / "signing.ed25519"))

    _run_phase(staged_context, "PA")
    _run_phase(staged_context, "PA2")
    _run_phase(staged_context, "PB")
    _run_phase(staged_context, "fuse")
    _run_phase(staged_context, "PC")

    result = certify_mod.run_certify(staged_context["build_id"], context=staged_context)
    from arail.nucleus.cards.dna_v2 import load_card

    card = load_card(Path(result["card_dir"]) / "dna-card.yaml")

    # executable checks are honestly not_run, never a fake 0.0/1.0 rate.
    for name in ("compiles", "patch_applies", "checkpatch_clean"):
        assert card["executable"][name]["status"] == "not_run"

    # pipeline_hash is a real sha256, not the "sha256:not_computed" literal.
    assert card["pipeline_hash"] != "sha256:not_computed"
    assert card["pipeline_hash"].startswith("sha256:")
    assert len(card["pipeline_hash"]) == len("sha256:") + 64

    # composite/v1-open is selected (no executable measured this sprint),
    # and its formula is the constant string, never a metric-value dump.
    assert card["composite"]["formula_id"] == "composite/v1-open"
    assert card["composite"]["formula"] == "0.6*closed.mean_f1+0.4*open.lc_win_rate"
    assert isinstance(card["composite"]["value"], float)

    # A real signature over the (non-zero, non-placeholder) training_hash.
    # B3 item 3 ("KNOWN_ISSUE can not be reached"): the fused and base
    # students score IDENTICALLY here (both use the default StubProvider
    # with the same deterministic fallback text -- the fixture never
    # differentiates the fused shard from base), so a real, non-tied-break
    # beats_base comparison correctly reads False and KNOWN_ISSUE is the
    # genuinely reached decision -- not hardcoded, not unreachable.
    assert result["decision"] == "KNOWN_ISSUE"


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


# ── B4 (2026-09-23 review): eval_hash changes iff the YARDSTICK changes,
# never because of what a run happened to score ──────────────────────

def _certify_and_read_card_eval_hash(staged_context, tmp_path, monkeypatch, *, version_suffix):
    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(tmp_path / f"signing-{version_suffix}.ed25519"))
    ctx = dict(staged_context)
    ctx["build_id"] = f"kernel-2026010{version_suffix}T000000Z-{version_suffix:04x}"
    ctx["version"] = f"0.{version_suffix}.0"

    _run_phase(ctx, "PA")
    _run_phase(ctx, "PA2")
    _run_phase(ctx, "PB")
    _run_phase(ctx, "fuse")
    _run_phase(ctx, "PC")

    result = certify_mod.run_certify(ctx["build_id"], context=ctx)
    from arail.nucleus.cards.dna_v2 import load_card

    card = load_card(Path(result["card_dir"]) / "dna-card.yaml")
    return card["eval_hash"], ctx


def test_eval_hash_identical_for_different_metric_values_same_yardstick(staged_context, tmp_path, monkeypatch):
    hash_1, ctx1 = _certify_and_read_card_eval_hash(staged_context, tmp_path, monkeypatch, version_suffix=1)

    # Same yardstick, but hand-edit metrics.json before a second certify --
    # a DIFFERENT run that scored differently on the IDENTICAL task set.
    eval_dir = build_mod._run_dir(ctx1) / "eval"
    metrics = json.loads((eval_dir / "metrics.json").read_text())
    metrics["closed"]["mean_f1"] = 0.999
    (eval_dir / "metrics.json").write_text(json.dumps(metrics))

    result = certify_mod.run_certify(ctx1["build_id"], context=ctx1)
    from arail.nucleus.cards.dna_v2 import load_card

    card_2 = load_card(Path(result["card_dir"]) / "dna-card.yaml")
    assert card_2["eval_hash"] == hash_1
    assert card_2["closed_ended"]["cve_detection"]["f1"] != None  # sanity: real per-task rows still present


def test_eval_hash_changes_when_a_task_template_changes(staged_context, tmp_path, monkeypatch):
    hash_1, _ctx1 = _certify_and_read_card_eval_hash(staged_context, tmp_path, monkeypatch, version_suffix=2)

    import arail.nucleus.evals.tasks.linux_kernel as linux_kernel_mod

    monkeypatch.setitem(linux_kernel_mod.PROMPT_TEMPLATES, "cve_detection", "a totally different template {subject} {body}")

    hash_2, _ctx2 = _certify_and_read_card_eval_hash(staged_context, tmp_path, monkeypatch, version_suffix=3)
    assert hash_2 != hash_1


def test_eval_hash_changes_when_decoding_changes(staged_context, tmp_path, monkeypatch):
    hash_1, _ctx1 = _certify_and_read_card_eval_hash(staged_context, tmp_path, monkeypatch, version_suffix=4)

    from arail.nucleus.providers.base import Decoding

    # dataclass defaults are baked into __init__'s bytecode at class
    # definition time, so patching the class attribute doesn't change
    # what `Decoding()` returns -- patch the name certify.py actually
    # calls instead.
    monkeypatch.setattr(certify_mod, "Decoding", lambda: Decoding(max_new_tokens=999))

    hash_2, _ctx2 = _certify_and_read_card_eval_hash(staged_context, tmp_path, monkeypatch, version_suffix=5)
    assert hash_2 != hash_1


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
