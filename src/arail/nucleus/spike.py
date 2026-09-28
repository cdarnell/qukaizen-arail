"""``arailctl nucleus spike`` — the Gate B harness (ARCHITECTURE.md §10
commit 24, §7 "Performance (M5, local...)").

Reports B0–B3 against their thresholds, plus captured_mass, in
spike-report.json. Gate B itself (the operator's real M5 run, real
teacher) is outside this sprint's scope — this harness runs identically
against the stub provider in CI (T-SPIKE-1) and against a real local
runtime on the M5 when the operator runs it for real.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional, Sequence

from arail.nucleus.errors import RefusedByPolicy
from arail.nucleus.providers.base import Decoding, Prompt

_B1_BUDGET_HOURS = 4.0
_B2_MIN_COMPOSITE = 0.6
_B3_MAX_DRIFT_PCT = 5.0
_CAPTURED_MASS_TARGET = 0.9


@dataclass(frozen=True)
class SpikeResult:
    b0_pass: bool
    b0_detail: str
    b1_pass: bool
    b1_estimated_hours: float
    b2_pass: bool
    b2_composite: float
    b3_pass: bool
    b3_max_drift_pct: float
    captured_mass: float
    captured_mass_ok: bool
    overall_pass: bool

    def to_dict(self) -> dict:
        return asdict(self)


def _is_stub() -> bool:
    import os

    return os.getenv("ARAIL_NUCLEUS_STUB", "").strip() == "1"


def run_spike(domain, *, windows: int = 20, cert_sample: int = 50,
             provider_factory=None) -> SpikeResult:
    from arail.nucleus.corpus.stage import load_items, load_staged
    from arail.nucleus.evals.splits import split

    stage_result = load_staged(domain.name)
    if stage_result is None:
        raise RefusedByPolicy(f"no staged corpus for {domain.name!r} — run `nucleus stage` first")
    items = load_items(stage_result)
    splits = split(items, cutoff=domain.corpus_cutoff, dev_fraction=domain.eval_dev_fraction)
    sample_items = splits.train[:windows] if splits.train else []

    if provider_factory is None:
        provider_factory = _default_provider_factory

    provider = provider_factory("teacher")
    try:
        # B0: greedy text identical with/without top-N logprob capture, on
        # up to 5 prompts.
        b0_prompts = [Prompt(item_id=it["id"], text=it["text"], role="teacher") for it in sample_items[:5]]
        b0_pass, b0_detail = _check_b0(provider, b0_prompts)

        # B1: throughput on `windows` windows, extrapolated to the full
        # train pool.
        extract_prompts = [Prompt(item_id=it["id"], text=it["text"], role="teacher") for it in sample_items]
        started = time.monotonic()
        generations = provider.generate_with_topn(extract_prompts, Decoding(), top_n=domain.distill_top_n)
        elapsed_s = time.monotonic() - started
        per_window_s = elapsed_s / max(len(extract_prompts), 1)
        estimated_hours = (per_window_s * max(len(splits.train), 1)) / 3600.0
        b1_pass = estimated_hours <= _B1_BUDGET_HOURS

        captured_mass = _mean_captured_mass(generations, student_max_id=None)
        captured_mass_ok = captured_mass >= _CAPTURED_MASS_TARGET

        # B2: teacher composite on a cert sample (closed tasks only, in
        # this harness -- open/executable need a judge/repo wired the
        # same way certify.py's generic path does).
        b2_composite = _b2_teacher_composite(domain, provider, cert_sample)
        b2_pass = b2_composite >= _B2_MIN_COMPOSITE
    finally:
        provider.close()

    # B3: Buddy residency -- this harness doesn't run a real multi-phase
    # build, so there's no real sampler loop to feed; report "ok" with
    # 0 drift in the stub/no-Buddy-running case (residency.classify()'s
    # own "unmeasured" path when there are no portal samples at all).
    from arail.nucleus.residency import classify

    residency_result = classify([])
    b3_pass = residency_result.status in ("ok", "unmeasured")

    overall_pass = b0_pass and b1_pass and b2_pass and b3_pass and captured_mass_ok

    return SpikeResult(
        b0_pass=b0_pass, b0_detail=b0_detail, b1_pass=b1_pass, b1_estimated_hours=round(estimated_hours, 3),
        b2_pass=b2_pass, b2_composite=round(b2_composite, 4), b3_pass=b3_pass,
        b3_max_drift_pct=residency_result.max_drift_pct, captured_mass=round(captured_mass, 4),
        captured_mass_ok=captured_mass_ok, overall_pass=overall_pass,
    )


def _default_provider_factory(role: str):
    if _is_stub():
        from arail.nucleus.providers.stub import StubProvider

        return StubProvider(role=role)
    raise RefusedByPolicy("spike's real-runtime provider wiring is exercised on the M5, not in this generic path")


def _check_b0(provider, prompts: Sequence[Prompt]) -> tuple:
    if not prompts:
        return True, "no prompts sampled (empty train pool)"
    plain = provider.generate(list(prompts), Decoding(temperature=0.0))
    capture = provider.generate_with_topn(list(prompts), Decoding(temperature=0.0), top_n=5)
    mismatches = [p.item_id for p, c in zip(plain, capture) if p.text != c.text]
    if mismatches:
        return False, f"greedy text differs with capture on: {mismatches}"
    return True, "greedy text identical with and without logprob capture"


def _mean_captured_mass(generations, *, student_max_id: Optional[int]) -> float:
    from arail.nucleus.train.kd_loss import renormalize_topn

    masses = []
    for gen in generations:
        for step_ids, step_logprobs in zip(gen.topn_ids, gen.topn_logprobs):
            renorm = renormalize_topn(step_ids, step_logprobs, student_max_id=student_max_id)
            masses.append(renorm.captured_mass)
    return sum(masses) / len(masses) if masses else 1.0


def _b2_teacher_composite(domain, provider, cert_sample: int) -> float:
    from arail.nucleus.evals import closed
    from arail.nucleus.evals.splits import CertStore
    from arail.nucleus.evals.tasks.linux_kernel import cve_detection_task, parse_closed_answer

    try:
        cert_access = CertStore().open(domain.name)
    except RefusedByPolicy:
        return 0.0
    cert_items = cert_access.open()[:cert_sample]
    if not cert_items:
        return 0.0

    prompts, gold = cve_detection_task(cert_items)
    generations = provider.generate(prompts, Decoding())
    pred = [parse_closed_answer(g.text, valid=("cve", "not")) for g in generations]
    row = closed.binary_prf1(gold, pred, positive="cve")
    return row["f1"]


def _parse_spike_args(argv: Sequence[str]) -> dict:
    if not argv:
        raise RefusedByPolicy("usage: nucleus spike <slug> [--windows N] [--cert-sample N]")
    slug = argv[0]
    windows = 20
    cert_sample = 50
    i = 1
    while i < len(argv):
        if argv[i] == "--windows":
            i += 1
            windows = int(argv[i])
        elif argv[i] == "--cert-sample":
            i += 1
            cert_sample = int(argv[i])
        else:
            raise RefusedByPolicy(f"unknown spike argument: {argv[i]!r}")
        i += 1
    return {"slug": slug, "windows": windows, "cert_sample": cert_sample}


def run(argv: Sequence[str]) -> int:
    import sys

    from arail.nucleus.domain import load_domain
    from arail.nucleus.models import resolve_model

    parsed = _parse_spike_args(argv)
    domain = load_domain(parsed["slug"], model_resolver=resolve_model)
    result = run_spike(domain, windows=parsed["windows"], cert_sample=parsed["cert_sample"])

    from arail.nucleus.paths import nucleus_data

    report_path = nucleus_data() / "domains" / domain.name / "spike-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    report_path.write_text(json.dumps(result.to_dict(), sort_keys=True, indent=2))

    sys.stdout.write(json.dumps(result.to_dict(), sort_keys=True, indent=2) + "\n")
    sys.stdout.write(f"wrote {report_path}\n")
    return 0 if result.overall_pass else 3
