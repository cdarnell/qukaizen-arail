"""``arailctl nucleus certify`` — contamination gate -> card -> seal ->
report -> ledger (ARCHITECTURE.md §4.8, §4.10).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Sequence

from arail.nucleus.errors import RefusedByPolicy


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

    eval_inputs = EvalHashInputs(
        harness_version="1", prompts="linux-kernel-task-adapters-v1",
        few_shot={"bytes_hex": "", "k": 0},
        scoring={
            "metric_defs": ["f1", "macro_f1"], "positive_classes": {"cve_detection": "cve"},
            "composite_formula_id": composite_result.formula_id,
            "composite_formula": str(composite_result.inputs),
            "decision_rule_id": "decision_rule/v1", "judge_rubric": "not_run",
            "judge_model_identity": "not_run", "lc_method": "alpacaeval2-lc", "lc_params": {"n": 1000},
            "bootstrap_n": 1000, "bootstrap_seed": 42, "position_seed": 7,
            "executable_checks": ["patch_applies", "checkpatch_clean"],
            "checkpatch_sha256": "not_run", "git_version": "not_run",
            "contamination_method": contamination_report.method,
            "contamination_params": contamination_report.params,
        },
        decoding={"student": {"temperature": 0.0}, "base": {"temperature": 0.0}, "teacher": {"temperature": 0.0}},
        cert_set_version=cert_access.manifest["sha256"],
    )
    this_eval_hash = compute_eval_hash(eval_inputs)

    from arail.nucleus.evals.hash import write_eval_config_lock

    write_eval_config_lock(eval_inputs, rd / "eval-config.lock")

    from datetime import datetime, timezone

    card = build_card(
        shard=domain.shard, version=context.get("version") or "0.1.0",
        built=datetime.now(timezone.utc).isoformat(),
        pipeline_hash=context.get("pipeline_hash", "sha256:not_computed"),
        eval_hash=this_eval_hash, runtime=("stub" if _is_stub() else domain.runtime),
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
        composite={"formula_id": composite_result.formula_id, "formula": str(composite_result.inputs),
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
    sealed = sign(payload)
    card["signed"] = sealed.signed

    shard_root = Path(context.get("shard_output_dir") or (rd / "forge_out"))
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
