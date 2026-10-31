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


# ── ASK forged-context (2026-09-23 review round 2): context.json's
# "stub" flag is hand-editable, but the per-phase provider stamps aren't
# -- a forged "stub: false" over stub-stamped phase output must refuse
# just like a genuine build/certify mismatch ────────────────────────

def test_certify_refuses_on_forged_context_stub_flag(staged_context, tmp_path, monkeypatch):
    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(tmp_path / "signing.ed25519"))

    _run_phase(staged_context, "PA")
    _run_phase(staged_context, "PA2")
    _run_phase(staged_context, "PB")
    _run_phase(staged_context, "fuse")
    _run_phase(staged_context, "PC")

    # Every phase output really did stamp "provider": "stub" (written by
    # the phase itself). Forge context.json to claim stub=False while
    # unsetting the env var to match it -- so B1's build/certify env-match
    # check alone would pass.
    forged_context = dict(staged_context)
    forged_context["stub"] = False
    monkeypatch.delenv("ARAIL_NUCLEUS_STUB", raising=False)

    with pytest.raises(RefusedByPolicy, match="phase output disagrees"):
        certify_mod.run_certify(staged_context["build_id"], context=forged_context)

    rd = build_mod._run_dir(staged_context)
    assert not any(rd.rglob("dna-card.yaml"))
    from arail.nucleus.cards import certified_models
    ledger = certified_models._default_local_path(build_mod._nucleus_data(staged_context))
    assert not ledger.exists() or "kernel" not in ledger.read_text()


# ── ASK eyeball outputs (2026-09-23 review round 2): build-report.md
# carries the real PC-generated eyeball outputs, not the old placeholder
# text -- even though PC genuinely generated them ─────────────────────

def test_build_report_carries_real_eyeball_outputs(staged_context, tmp_path, monkeypatch):
    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(tmp_path / "signing.ed25519"))

    _run_phase(staged_context, "PA")
    _run_phase(staged_context, "PA2")
    _run_phase(staged_context, "PB")
    _run_phase(staged_context, "fuse")
    _run_phase(staged_context, "PC")

    result = certify_mod.run_certify(staged_context["build_id"], context=staged_context)
    report_text = Path(result["card_dir"], "build-report.md").read_text()

    assert "(not generated in this generic certify path)" not in report_text
    # the fixture's real, distinguishable stub texts for both roles.
    assert "[stub:open:fused-v1] explanation for eyeball-0" in report_text
    assert "[stub:open:base-v1] explanation for eyeball-0" in report_text


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


# ── R2 (2026-09-23 review round 2): eval_hash must cover the open-ended
# yardstick now that it's measured -- the judge's identity/rubric and the
# eyeball prompt bytes it actually judges, not just the closed templates ──

def test_eval_hash_changes_when_eyeball_file_changes(staged_context, tmp_path, monkeypatch):
    hash_1, ctx1 = _certify_and_read_card_eval_hash(staged_context, tmp_path, monkeypatch, version_suffix=6)

    domains_dir = Path(ctx1["domains_dir"])
    eyeball_path = domains_dir / "kernel.eyeball.txt"
    lines = eyeball_path.read_text().splitlines()
    lines[0] = "an edited eyeball prompt"  # still exactly 10 lines, different bytes
    eyeball_path.write_text("\n".join(lines) + "\n")

    hash_2, _ctx2 = _certify_and_read_card_eval_hash(staged_context, tmp_path, monkeypatch, version_suffix=7)
    assert hash_2 != hash_1


def test_eval_hash_changes_when_judge_winner_table_changes(staged_context, tmp_path, monkeypatch):
    hash_1, _ctx1 = _certify_and_read_card_eval_hash(staged_context, tmp_path, monkeypatch, version_suffix=8)

    # A different (still deterministic) judge behavior -- as if the stub
    # judge's fixture preferences were reconfigured -- changes its content
    # identity, which must change eval_hash even though no prompt TEMPLATE
    # or metric value changed.
    monkeypatch.setattr(build_mod, "_open_winner_table",
                        lambda prompts, *, wrong_every: {p.item_id: "baseline" for p in prompts})

    hash_2, _ctx2 = _certify_and_read_card_eval_hash(staged_context, tmp_path, monkeypatch, version_suffix=9)
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


# ── R1 (2026-09-23 review round 2) / R3-A2 (round 3): the signed card
# must carry, and be CONSISTENT WITH, every number its composite and
# decision are computed from -- recompute both straight from the card's
# own headline blocks (closed_ended, open_ended, executable, baselines,
# composite.formula_id) and assert they match the signed values exactly.
# Round 3 found the first version called decide() WITHOUT formula_id,
# which only agreed because the unit card is KNOWN_ISSUE (cap
# irrelevant); on a cap-reaching card it recomputed CERTIFIED against
# the card's COMPATIBLE. It also only asserted lc != 0.5, which a broken
# A/B un-swap (lc = 0.0) passes. ─────────────────────────────────────

# The exact open-eval golden for the 10-prompt eyeball fixture: the stub
# winner table (build._open_winner_table, wrong_every=4) + the per-item
# length padding, through open_lc_judge's real randomize/un-swap + LC fit.
# A broken position un-swap gives 0.0 / ci95 [0.0, 0.6] (REVIEW round 3).
LC_GOLDEN = 0.8676
LC_GOLDEN_CI95 = [0.5, 1.0]


def assert_card_recomputes_from_itself(card):
    """Shared with the Gate A e2e test. Returns (recomputed_value,
    recomputed_decision) for callers that want to assert more."""
    from arail.nucleus.evals import closed as closed_mod
    from arail.nucleus.evals import composite as composite_mod

    open_entry = card["open_ended"]["eyeball_explanation"]
    closed_mean_f1 = closed_mod.mean_f1(list(card["closed_ended"].values()))
    base_mean_f1 = card["baselines"]["base_student"]["closed.mean_f1"]
    reconstructed = {
        "closed": {"mean_f1": closed_mean_f1},
        "open": {"lc_win_rate": open_entry["lc_win_rate_vs_base"]},
        "executable": {
            name: (composite_mod.NOT_RUN if row.get("status") == "not_run" else row["rate"])
            for name, row in card["executable"].items()
        },
    }
    # The formula is selected from the card's own executable block, and
    # must agree with the one the card says it used.
    assert composite_mod.select_formula_id(reconstructed) == card["composite"]["formula_id"]
    assert composite_mod.formula_string(card["composite"]["formula_id"]) == card["composite"]["formula"]

    recomputed = composite_mod.compute(reconstructed, formula_id=card["composite"]["formula_id"])
    assert recomputed.value == card["composite"]["value"]
    assert card["fidelity"]["achieved"] == card["composite"]["value"]

    decision = composite_mod.decide(
        recomputed.value, card["fidelity"]["target"],
        beats_base=(closed_mean_f1 > base_mean_f1),
        residency_status=card["residency"]["status"],
        formula_id=card["composite"]["formula_id"],
    )
    assert decision == card["fidelity"]["decision"]
    return recomputed.value, decision


def _certify_card(staged_context):
    for phase in ("PA", "PA2", "PB", "fuse", "PC"):
        _run_phase(staged_context, phase)
    result = certify_mod.run_certify(staged_context["build_id"], context=staged_context)
    from arail.nucleus.cards.dna_v2 import load_card

    return load_card(Path(result["card_dir"]) / "dna-card.yaml")


def test_card_composite_and_decision_recompute_from_the_card_alone(staged_context, tmp_path, monkeypatch):
    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(tmp_path / "signing.ed25519"))
    card = _certify_card(staged_context)

    open_entry = card["open_ended"]["eyeball_explanation"]
    assert open_entry["lc_win_rate_vs_base"] == LC_GOLDEN
    assert open_entry["ci95"] == LC_GOLDEN_CI95
    assert open_entry["n"] == 10
    assert "base_student" in card["baselines"]

    _value, decision = assert_card_recomputes_from_itself(card)
    assert decision == "KNOWN_ISSUE"  # this fixture's fused student loses to its base


def test_card_recompute_holds_on_a_compatible_by_cap_card(staged_context, tmp_path, monkeypatch):
    """Same invariant on a card whose decision is COMPATIBLE *only because
    of* the v1-open cap -- the case the round-2 test could not see."""
    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(tmp_path / "signing.ed25519"))

    original = build_mod._stub_closed_answer_table

    def _fused_nearly_perfect(cert_items, *, salt, wrong_every):
        if salt == "fused-v1":
            wrong_every = 1_000_003  # effectively never wrong
        return original(cert_items, salt=salt, wrong_every=wrong_every)

    monkeypatch.setattr(build_mod, "_stub_closed_answer_table", _fused_nearly_perfect)
    card = _certify_card(staged_context)

    assert card["open_ended"]["eyeball_explanation"]["lc_win_rate_vs_base"] == LC_GOLDEN
    assert card["composite"]["formula_id"] == "composite/v1-open"
    assert card["fidelity"]["achieved"] >= card["fidelity"]["target"]
    assert card["fidelity"]["decision"] == "COMPATIBLE"

    value, decision = assert_card_recomputes_from_itself(card)
    assert decision == "COMPATIBLE"

    # Discrimination check: dropping formula_id (the round-2 bug) gives a
    # DIFFERENT decision on this card, so the helper above really does
    # depend on passing it.
    from arail.nucleus.evals import closed as closed_mod
    from arail.nucleus.evals import composite as composite_mod

    closed_mean_f1 = closed_mod.mean_f1(list(card["closed_ended"].values()))
    without_formula = composite_mod.decide(
        value, card["fidelity"]["target"],
        beats_base=closed_mean_f1 > card["baselines"]["base_student"]["closed.mean_f1"],
        residency_status=card["residency"]["status"])
    assert without_formula == "CERTIFIED"


def test_broken_position_unswap_moves_the_lc_golden(staged_context, tmp_path, monkeypatch):
    """Mutation check for the LC golden (REVIEW round 3): a
    randomize_and_judge that forgets to un-swap positions (model_won =
    verdict == "A") must NOT reproduce LC_GOLDEN -- otherwise pinning the
    golden proves nothing about the un-swap."""
    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(tmp_path / "signing.ed25519"))
    from arail.nucleus.evals import open_lc_judge

    def _broken(items, *, seed, judge_fn):
        import random

        out = []
        for idx, item in enumerate(items):
            rng = random.Random(seed ^ idx)
            model_first = rng.random() < 0.5
            a, b, order = ((item.text_model, item.text_baseline, ("model", "baseline")) if model_first
                           else (item.text_baseline, item.text_model, ("baseline", "model")))
            verdict = judge_fn(item.item_id, a, b).strip().upper()
            out.append(open_lc_judge.JudgedPair(item.item_id, order, verdict, verdict == "A"))
        return out

    monkeypatch.setattr(open_lc_judge, "randomize_and_judge", _broken)
    card = _certify_card(staged_context)
    lc = card["open_ended"]["eyeball_explanation"]["lc_win_rate_vs_base"]
    assert lc != LC_GOLDEN
    assert lc == 0.0
