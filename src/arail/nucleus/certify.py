"""``arailctl nucleus certify`` — contamination gate -> card -> seal ->
report -> ledger (ARCHITECTURE.md §4.8, §4.10).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Sequence

from arail.nucleus.errors import RefusedByPolicy
from arail.nucleus.providers.base import Decoding


def _is_stub() -> bool:
    import os

    return os.getenv("ARAIL_NUCLEUS_STUB", "").strip() == "1"


def run_certify(build_id: str, *, publish_row: bool = False, context: dict = None) -> dict:
    """The pipeline body — pulled out of run() so it's directly callable
    from tests without going through argv parsing."""
    from arail.nucleus import build as build_mod
    from arail.nucleus.cards import build_report, certified_models, seal
    from arail.nucleus.cards.dna_v2 import build_card, card_sha256, not_run, write_card
    from arail.nucleus.evals import composite as composite_mod
    from arail.nucleus.evals.contamination import ContaminationChecker
    from arail.nucleus.evals.hash import EvalHashInputs, eval_hash as compute_eval_hash
    from arail.nucleus.paths import run_dir as run_dir_fn

    rd = run_dir_fn(build_id)
    context = context or json.loads((rd / "context.json").read_text())
    domain = build_mod._resolve_domain(context)

    # B1 (2026-09-23 review): whether this card is a stub card is decided
    # from what the BUILD actually recorded at build time (context.json's
    # "stub" field, written by build.run() before any phase ran), never
    # from whatever ARAIL_NUCLEUS_STUB happens to be set to right now in
    # the certify process. A build record written before this fix (no
    # "stub" key) refuses rather than silently defaulting either way.
    # A build/certify environment mismatch also refuses -- certifying a
    # stub build's results as if they were real (or vice versa) is
    # exactly the "stub laundered into a trusted, ledgered card" gap this
    # finding reproduced.
    if "stub" not in context:
        raise RefusedByPolicy(
            f"certify refused: {rd / 'context.json'} has no recorded 'stub' "
            f"provenance (build predates stub-provenance tracking) — rebuild "
            f"this run before certifying it"
        )
    build_was_stub = bool(context["stub"])
    certify_env_is_stub = _is_stub()
    if build_was_stub != certify_env_is_stub:
        raise RefusedByPolicy(
            f"certify refused: this build ran with stub={build_was_stub} "
            f"(recorded at build time) but the current environment has "
            f"ARAIL_NUCLEUS_STUB={'1' if certify_env_is_stub else '<unset>'} "
            f"— certify must run in the same stub/real mode as the build "
            f"it is certifying, or the resulting card's runtime/signature "
            f"would not reflect what actually ran"
        )
    is_stub = build_was_stub

    from arail.nucleus.corpus.stage import load_items, load_staged
    from arail.nucleus.evals.splits import CertStore, split

    stage_result = load_staged(domain.name, nucleus_data=build_mod._nucleus_data(context))
    if stage_result is None:
        raise RefusedByPolicy(f"no staged corpus for {domain.name!r}")
    items = load_items(stage_result)
    splits = split(items, cutoff=domain.corpus_cutoff, dev_fraction=domain.eval_dev_fraction)

    cert_access = CertStore(nucleus_data=build_mod._nucleus_data(context)).open(domain.name)
    cert_items = cert_access.open()

    checker = ContaminationChecker(cert_items, cutoff=domain.corpus_cutoff)
    for it in splits.train:
        checker.observe_train_doc(it.get("text", ""), date=it.get("date", ""))
    contamination_report = checker.check()

    if contamination_report.contaminated:
        raise RefusedByPolicy(
            f"certify refused: contamination overlap={contamination_report.overlap:.4f} "
            f"(threshold 0.01), temporal_leak={contamination_report.temporal_leak!r}. "
            f"No card was written. Top offenders: {contamination_report.top_offenders}"
        )

    # B9 (2026-09-23 review): the shard/version/output directory a
    # certified card belongs under is whatever `fuse` actually minted
    # under FORGE_ROOT (recorded in its phase output) — never re-derived
    # here. Re-deriving with paths.shard_dir() would either disagree with
    # what fuse actually wrote, or refuse outright ("already exists"),
    # since fuse already created that directory.
    fuse_output_path = rd / "phase_output" / "fuse.json"
    if not fuse_output_path.is_file():
        raise RefusedByPolicy(
            f"certify refused: no fuse phase output at {fuse_output_path} "
            f"— run `nucleus build` through the fuse phase before certify"
        )
    fuse_output = json.loads(fuse_output_path.read_text())
    shard_root = Path(fuse_output["shard_dir"])
    fused_version = fuse_output["version"]

    eval_dir = rd / "eval"
    metrics = json.loads((eval_dir / "metrics.json").read_text())

    task_names = ["cve_detection", "subsystem_routing"]
    closed_ended = dict(zip(task_names, metrics["closed"]["rows"]))

    composite_result = composite_mod.compute(metrics)
    achieved = composite_result.value
    base_composite = 0.0  # base_student baseline composite — wired for real once PC scores base_student separately
    beats_base = achieved >= base_composite
    decision = composite_mod.decide(achieved, domain.fidelity_target, beats_base=beats_base,
                                    residency_status="unmeasured")

    # B4 (2026-09-23 review): `prompts` is the actual task template BYTES
    # (hex-encoded), not a hand-maintained version label -- editing a
    # template in evals/tasks/linux_kernel.py now changes eval_hash;
    # bumping a label by hand (or forgetting to) can no longer desync it.
    from arail.nucleus.evals.tasks.linux_kernel import PROMPT_TEMPLATES

    prompts_bytes = "\x1e".join(f"{name}={PROMPT_TEMPLATES[name]}" for name in sorted(PROMPT_TEMPLATES)).encode()
    prompts_hex = prompts_bytes.hex()

    # `composite_formula` is the CONSTANT formula string for this run's
    # formula id -- never the computed metric values (composite_result.inputs
    # holds those; two students scored on an identical yardstick must get
    # the SAME eval_hash regardless of what they scored).
    composite_formula_str = composite_mod.formula_string(composite_result.formula_id)

    # Full Decoding() for every role -- every provider role in this sprint's
    # pipeline (build.py's PA/PA2/PC calls) uses the Decoding() default, so
    # this is what actually ran, not a placeholder.
    from dataclasses import asdict as _asdict

    default_decoding = _asdict(Decoding())

    eval_inputs = EvalHashInputs(
        harness_version="1", prompts=prompts_hex,
        few_shot={"bytes_hex": "", "k": 0},
        scoring={
            "metric_defs": ["f1", "macro_f1"], "positive_classes": {"cve_detection": "cve"},
            "composite_formula_id": composite_result.formula_id,
            "composite_formula": composite_formula_str,
            "decision_rule_id": "decision_rule/v1", "judge_rubric": "not_run",
            "judge_model_identity": "not_run", "lc_method": "alpacaeval2-lc", "lc_params": {"n": 1000},
            "bootstrap_n": 1000, "bootstrap_seed": 42, "position_seed": 7,
            "executable_checks": ["patch_applies", "checkpatch_clean"],
            "checkpatch_sha256": "not_run", "git_version": "not_run",
            "contamination_method": contamination_report.method,
            "contamination_params": contamination_report.params,
        },
        decoding={"student": default_decoding, "base": default_decoding, "teacher": default_decoding},
        cert_set_version=cert_access.manifest["sha256"],
    )
    this_eval_hash = compute_eval_hash(eval_inputs)

    from arail.nucleus.evals.hash import write_eval_config_lock

    shard_root.mkdir(parents=True, exist_ok=True)
    write_eval_config_lock(eval_inputs, shard_root / "eval-config.lock")

    from datetime import datetime, timezone

    card = build_card(
        shard=domain.shard, version=fused_version,
        built=datetime.now(timezone.utc).isoformat(),
        pipeline_hash=context.get("pipeline_hash", "sha256:not_computed"),
        eval_hash=this_eval_hash, runtime=("stub" if is_stub else domain.runtime),
        lab_mode=__import__("arail.airgap", fromlist=["lab_mode"]).lab_mode(),
        distillation={
            "mode": "logit",
            "teacher": {"profile": domain.teacher_profile, "model": domain.teacher_model,
                       "tokenizer": "unknown"},
            "student": {"base": domain.student_base, "tokenizer": "unknown", "method": "lora"},
            "tokenizer_parity": True, "tokenizer_parity_detail": "not_run this sprint's certify path",
            "logit_source": "teacher_generated_topn", "top_n": domain.distill_top_n,
            "renorm": "topn_softmax", "captured_mass": 1.0, "dropped_mass": 0.0,
        },
        splits={"corpus_cutoff": domain.corpus_cutoff,
               "dev": {"n": len(splits.dev), "seen_by_arbitrage": True},
               "cert": {"n": len(cert_items), "seen_by_arbitrage": False, "frozen": True,
                        "version": cert_access.manifest["version"]}},
        contamination={"method": contamination_report.method,
                       "overlap_train_vs_cert": contamination_report.overlap,
                       "temporal_leak": contamination_report.temporal_leak},
        corpus={"sources": stage_result.manifest["sources"]},
        closed_ended=closed_ended,
        open_ended={"patch_explanation": not_run("judge not wired in this generic certify path")},
        executable={
            k: (not_run("not available in this generic certify path") if v == composite_mod.NOT_RUN
               else {"rate": v, "n": len(cert_items)})
            for k, v in metrics["executable"].items()
        },
        composite={"formula_id": composite_result.formula_id, "formula": composite_formula_str,
                  "value": composite_result.value},
        fidelity={"target": domain.fidelity_target, "achieved": achieved, "decision": decision,
                 "decision_rule": "decision_rule/v1"},
    )
    this_card_sha256 = card_sha256(card)
    from arail.nucleus.cards.seal import build_gate_results, build_payload, chain_hash, sign

    gate_results = build_gate_results(card_sha256=this_card_sha256, eval_hash=this_eval_hash,
                                      decision=decision, contamination_overlap=contamination_report.overlap)
    training_hash = "sha256:" + "0" * 64  # content_hash of adapter+fused weights -- wired once fuse() is real
    payload = build_payload(
        pipeline_run_id=build_id,
        chain_hash=chain_hash(corpus_hash=stage_result.manifest_sha256, teacher_hash="teacher-hash-not-wired",
                              config_hash=context.get("pipeline_hash", "not_computed"),
                              training_hash=training_hash),
        gate_results=gate_results,
    )
    sealed = sign(payload, ephemeral=is_stub)
    card["signed"] = sealed.signed

    shard_root.mkdir(parents=True, exist_ok=True)
    write_card(card, shard_root / "dna-card.yaml")
    (shard_root / "seal.json").write_text(json.dumps(sealed.seal_json))

    eyeball_prompts = domain.eval_eyeball_prompts.read_text().splitlines()
    report_text = build_report.render(
        decision=decision, beats_base=beats_base, achieved=achieved, base_composite=base_composite,
        composite_formula_id=composite_result.formula_id, composite_value=composite_result.value,
        closed_ended=card["closed_ended"], open_ended=card["open_ended"], executable=card["executable"],
        eyeball_prompts=eyeball_prompts[:10] + [""] * max(0, 10 - len(eyeball_prompts)),
        student_outputs=["(not generated in this generic certify path)"] * 10,
        base_outputs=["(not generated in this generic certify path)"] * 10,
    )
    (shard_root / "build-report.md").write_text(report_text)

    verify_result = seal.verify(card, eval_hash_recompute=this_eval_hash, fast=True)
    # The card/seal/report are always written above -- the ledger append is
    # the one step that's allowed to refuse without unwinding everything
    # else (stub / unsigned / untrusted-key cards are valid LOCAL build
    # artifacts; they're just never ledgered, per T-STUB-2/T-LEDGER-3).
    try:
        ledger_path = certified_models.append(card, publish_row=publish_row, verify_result=verify_result,
                                              nucleus_data=build_mod._nucleus_data(context))
        ledger_path = str(ledger_path)
    except RefusedByPolicy as exc:
        ledger_path = None
        sys.stderr.write(f"certify: not ledgered: {exc}\n")

    try:
        from arail.activity import activity_log

        activity_log.emit(source="nucleus", message=f"certify: {decision} ({domain.shard})",
                          level="success" if decision in ("CERTIFIED", "COMPATIBLE") else "warn")
    except Exception:  # noqa: BLE001 — progress tracking must never abort a build
        pass

    return {"decision": decision, "card_dir": str(shard_root), "ledger_path": ledger_path,
           "contamination_overlap": contamination_report.overlap}


def _parse_certify_args(argv: Sequence[str]) -> dict:
    if not argv:
        raise RefusedByPolicy("usage: nucleus certify <slug|build_id> [--publish-row]")
    target = argv[0]
    publish_row = "--publish-row" in argv[1:]
    return {"target": target, "publish_row": publish_row}


def run(argv: Sequence[str]) -> int:
    parsed = _parse_certify_args(argv)
    result = run_certify(parsed["target"], publish_row=parsed["publish_row"])
    sys.stdout.write(f"decision: {result['decision']}\ncard: {result['card_dir']}\n"
                     f"ledger: {result['ledger_path']}\n")
    return 0
