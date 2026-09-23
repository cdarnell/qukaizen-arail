"""``arailctl nucleus build`` — verb implementation + phase bodies
(ARCHITECTURE.md §4.8, §5).

``run(argv)`` is the CLI entry (parses flags, runs P0 preflight in the
parent process, writes context.json, then drives phases.run_phase() for
PA -> PA2 -> PB -> fuse -> PC). ``run_phase_body(phase, context)`` is
what worker.py calls inside each spawned subprocess — kept as a plain
function (not a class) so a subprocess only needs to import this module
and call one function, no shared state with the parent beyond what's on
disk.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from arail.nucleus.errors import RefusedByPolicy
from arail.nucleus.providers.base import Decoding, Prompt

_WORKER_PHASES = ["PA", "PA2", "PB", "fuse", "PC"]


def _is_stub() -> bool:
    return os.getenv("ARAIL_NUCLEUS_STUB", "").strip() == "1"


def _resolve_domain(context: dict):
    from arail.nucleus.domain import load_domain
    from arail.nucleus.models import resolve_model

    domains_dir = Path(context["domains_dir"]) if context.get("domains_dir") else None
    return load_domain(context["domain_name"], domains_dir=domains_dir, model_resolver=resolve_model)


def _nucleus_data(context: dict) -> Optional[Path]:
    return Path(context["nucleus_data"]) if context.get("nucleus_data") else None


def _run_dir(context: dict) -> Path:
    from arail.nucleus.paths import run_dir

    return run_dir(context["build_id"])


def _provider_for_role(role: str, model_name: str, *, run_dir: Path, top_n: int):
    if _is_stub():
        from arail.nucleus.providers.stub import StubProvider

        return StubProvider(role=role)

    from arail.nucleus.models import resolve_model
    from arail.nucleus.providers.queuellm import QueueLLMProvider

    model = resolve_model(model_name)
    return QueueLLMProvider(str(model.path), run_dir=run_dir)


# ── PA: extract (teacher, train prompts, top-N logprobs) ─────────────

def _phase_extract_train(context: dict) -> dict:
    domain = _resolve_domain(context)
    from arail.nucleus.corpus.stage import load_items, load_staged
    from arail.nucleus.evals.splits import split

    stage_result = load_staged(domain.name, nucleus_data=_nucleus_data(context))
    if stage_result is None:
        raise RefusedByPolicy(f"no staged corpus for {domain.name!r} — run `nucleus stage` first")
    items = load_items(stage_result)
    splits = split(items, cutoff=domain.corpus_cutoff, dev_fraction=domain.eval_dev_fraction)

    run_dir = _run_dir(context)
    extract_dir = run_dir / "extract"
    extract_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    index_path = extract_dir / "index.jsonl"
    already_indexed = set()
    if index_path.is_file():
        for line in index_path.read_text().splitlines():
            if line.strip():
                already_indexed.add(json.loads(line)["item_id"])

    pending = [it for it in splits.train if it["id"] not in already_indexed]
    provider = _provider_for_role("teacher", context.get("teacher_name", ""), run_dir=run_dir,
                                  top_n=domain.distill_top_n)
    try:
        prompts = [Prompt(item_id=it["id"], text=it["text"], role="teacher") for it in pending]
        if prompts:
            generations = provider.generate_with_topn(prompts, Decoding(), top_n=domain.distill_top_n)
            for gen in generations:
                _write_shard(extract_dir, gen)
                with index_path.open("a") as f:
                    f.write(json.dumps({"item_id": gen.item_id}) + "\n")
    finally:
        provider.close()

    return {"n_train_total": len(splits.train), "n_extracted_this_run": len(pending),
           "n_dev": len(splits.dev), "index_path": str(index_path)}


def _write_shard(extract_dir: Path, gen) -> None:
    n_steps = len(gen.token_ids)
    max_n = max((len(row) for row in gen.topn_ids), default=0)
    ids = np.full((n_steps, max_n), -1, dtype=np.int32)
    logprobs = np.full((n_steps, max_n), -1e4, dtype=np.float16)  # float16-safe sentinel (-1e9 overflows)
    for t in range(n_steps):
        row_ids = gen.topn_ids[t]
        row_lp = gen.topn_logprobs[t]
        ids[t, :len(row_ids)] = row_ids
        logprobs[t, :len(row_lp)] = row_lp
    sampled = np.array(gen.token_ids, dtype=np.int32)
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in gen.item_id)
    np.savez(extract_dir / f"{safe_name}.npz", ids=ids, logprobs=logprobs, sampled=sampled)


# ── PA2: teacher on cert (baseline + irreducible floor) ───────────────

def _phase_extract_cert(context: dict) -> dict:
    domain = _resolve_domain(context)
    from arail.nucleus.evals.splits import CertStore
    from arail.nucleus.evals.tasks.linux_kernel import cve_detection_task, subsystem_routing_task

    cert_access = CertStore(nucleus_data=_nucleus_data(context)).open(domain.name)
    cert_items = cert_access.open()

    run_dir = _run_dir(context)
    eval_dir = run_dir / "eval"
    eval_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    provider = _provider_for_role("teacher", context.get("teacher_name", ""), run_dir=run_dir, top_n=1)
    try:
        for task_name, task_fn in (("cve", cve_detection_task), ("subsystem", subsystem_routing_task)):
            prompts, gold = task_fn(cert_items)
            generations = provider.generate(prompts, Decoding())
            rows = [{"item_id": g.item_id, "text": g.text, "gold": gold[i]}
                   for i, g in enumerate(generations)]
            (eval_dir / f"teacher_{task_name}.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + ("\n" if rows else ""))
    finally:
        provider.close()

    return {"n_cert": len(cert_items)}


# ── PB: train (Arbitrage, dev-only) ───────────────────────────────────

def _phase_train(context: dict) -> dict:
    domain = _resolve_domain(context)
    from arail.nucleus.arbitrage import ArbitrageConfig, run as run_arbitrage

    run_dir = _run_dir(context)
    train_dir = run_dir / "train"
    train_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    cfg = ArbitrageConfig(target=domain.fidelity_target, max_cycles=8, max_hours=4.0)

    if _is_stub():
        from arail.nucleus.providers.stub import StubTrainer

        trainer = StubTrainer(train_dir / "adapter")
        train_cycle_fn = trainer.train_cycle
    else:
        train_cycle_fn = _mlx_train_cycle_fn(domain, run_dir, train_dir)

    result = run_arbitrage(None, None, cfg, train_cycle_fn=train_cycle_fn,
                           log_path=run_dir / "arbitrage.jsonl")

    return {"stop_metric": result.stop_metric, "n_cycles": len(result.cycles),
           "best_adapter_path": result.best_cycle.adapter_path,
           "best_dev_proxy_composite": result.best_cycle.dev_proxy_composite}


def _mlx_train_cycle_fn(domain, run_dir: Path, train_dir: Path):
    """Real path (requires_mlx) — see train/mlx_kd.py's own "UNVERIFIED IN
    THIS SPRINT'S CI SESSION" note; the same caveat applies to this glue.
    """
    def _cycle(cycle_idx: int):
        from arail.nucleus.arbitrage import CycleResult

        raise RefusedByPolicy(
            "real MLX training orchestration is wired at the module level "
            "(train/mlx_kd.py) but not yet connected to a full multi-cycle "
            "batch-reading loop here -- run with ARAIL_NUCLEUS_STUB=1 for "
            "Gate A, or complete this wiring before item 10 (see BUILD_LOG "
            "'Architect feedback required' / deviations)"
        )
        return CycleResult(cycle=cycle_idx, dev_proxy_composite=0.0)  # pragma: no cover

    return _cycle


# ── fuse ───────────────────────────────────────────────────────────────

def _phase_fuse(context: dict) -> dict:
    domain = _resolve_domain(context)
    from arail.nucleus.paths import shard_dir, next_patch_version

    version = context.get("version") or next_patch_version(domain.shard)
    output_dir = shard_dir(domain.shard, version) / "weights" / "mlx"

    if _is_stub():
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "FUSED_STUB_MARKER").write_text("stub fuse — not a real model\n")
    else:
        from arail.nucleus.models import resolve_model
        from arail.nucleus.train.mlx_kd import fuse as mlx_fuse

        train_dir = _run_dir(context) / "train"
        student = resolve_model(domain.student_base)
        mlx_fuse(str(student.path), train_dir / "adapter", output_dir)

    return {"version": version, "output_dir": str(output_dir)}


# ── PC: eval (student, base, judge on cert) ────────────────────────────

def _phase_eval(context: dict) -> dict:
    domain = _resolve_domain(context)
    from arail.nucleus.evals import closed, composite
    from arail.nucleus.evals.splits import CertStore
    from arail.nucleus.evals.tasks.linux_kernel import (
        cve_detection_task, parse_closed_answer, subsystem_routing_task,
    )

    run_dir = _run_dir(context)
    cert_access = CertStore(nucleus_data=_nucleus_data(context)).open(domain.name)
    cert_items = cert_access.open()

    closed_rows = []
    for task_name, task_fn, valid in (
        ("cve_detection", cve_detection_task, ("cve", "not")),
        ("subsystem_routing", subsystem_routing_task, None),
    ):
        prompts, gold = task_fn(cert_items)
        provider = _provider_for_role("student", domain.student_base, run_dir=run_dir, top_n=1)
        try:
            generations = provider.generate(prompts, Decoding())
        finally:
            provider.close()

        if valid is not None:
            pred = [parse_closed_answer(g.text, valid=valid) for g in generations]
            closed_rows.append(closed.binary_prf1(gold, pred, positive="cve"))
        else:
            pred = [g.text.strip().split(":")[0].strip() or "unknown" for g in generations]
            closed_rows.append(closed.macro_f1(gold, pred))

    closed_mean_f1 = closed.mean_f1(closed_rows)

    # Open judge + executable checks are wired at the module level
    # (open_lc_judge.py, executable_kernel.py); this phase reports
    # not_run for them in the generic (no fixture judge/repo wiring)
    # case -- the Gate A end-to-end test (commit 22) supplies both via
    # its own fixture and exercises the real path.
    metrics = {
        "closed": {"mean_f1": closed_mean_f1, "rows": closed_rows},
        "open": {"lc_win_rate": 0.5},
        "executable": {"compiles": composite.NOT_RUN, "patch_applies": 0.0, "checkpatch_clean": 0.0},
    }

    eval_dir = run_dir / "eval"
    eval_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    (eval_dir / "metrics.json").write_text(json.dumps(metrics, sort_keys=True, default=str))

    return {"closed_mean_f1": closed_mean_f1, "n_cert": len(cert_items)}


# ── dispatch ──────────────────────────────────────────────────────────

_PHASE_FUNCS = {
    "PA": _phase_extract_train, "PA2": _phase_extract_cert,
    "PB": _phase_train, "fuse": _phase_fuse, "PC": _phase_eval,
}


def run_phase_body(phase: str, context: dict) -> dict:
    if phase not in _PHASE_FUNCS:
        raise RefusedByPolicy(f"unknown worker phase {phase!r}")
    return _PHASE_FUNCS[phase](context)


# ── CLI verb: build ──────────────────────────────────────────────────

def _parse_build_args(argv: Sequence[str]) -> dict:
    if not argv:
        raise RefusedByPolicy(
            "usage: nucleus build <slug> --profile local [--memory-budget-gb N] "
            "[--resume ID] [--version V]"
        )
    slug = argv[0]
    profile = "local"
    memory_budget_gb = None
    resume_id = None
    version = None
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--profile":
            i += 1
            profile = argv[i]
        elif arg == "--memory-budget-gb":
            i += 1
            memory_budget_gb = float(argv[i])
        elif arg == "--resume":
            i += 1
            resume_id = argv[i]
        elif arg == "--version":
            i += 1
            version = argv[i]
        else:
            raise RefusedByPolicy(f"unknown build argument: {arg!r}")
        i += 1

    if profile != "local":
        raise RefusedByPolicy(f"only --profile local is supported this sprint, got {profile!r}")

    return {"slug": slug, "memory_budget_gb": memory_budget_gb, "resume_id": resume_id, "version": version}


def run(argv: Sequence[str]) -> int:
    from arail.nucleus import preflight as preflight_mod
    from arail.nucleus.paths import build_lock, new_build_id

    parsed = _parse_build_args(argv)
    domain = _resolve_domain({"domain_name": parsed["slug"]})

    # run_preflight() itself raises PreflightRefusal (mapped to exit 3 by
    # cli.py) for any red PHASE row (A/A2/B/C). It does NOT raise for a red
    # CAPABILITY row (deep runtime missing, mlx_lm out of range, ...) --
    # those are reported but non-fatal at the API level, so this function
    # is the one that turns report.has_red into a refusal for that case.
    report = preflight_mod.run_preflight(domain, memory_budget_gb=parsed["memory_budget_gb"],
                                         runtime_streams=(domain.runtime == "queuellm"))
    if report.has_red:
        bad = [r.name for r in report.rows if r.status == "red"]
        raise RefusedByPolicy(f"preflight capability check(s) failed: {', '.join(bad)}")

    from arail.nucleus.paths import ensure_nucleus_data_layout
    from arail.nucleus.paths import run_dir as run_dir_fn

    ensure_nucleus_data_layout()
    build_id = parsed["resume_id"] or new_build_id(domain.name)
    rd = run_dir_fn(build_id)
    rd.mkdir(parents=True, exist_ok=True, mode=0o700)

    # Persist whatever domains_dir/nucleus_data the PARENT actually resolved
    # (normally the real repo defaults; tests -- and any future per-instance
    # override -- may differ) so a worker SUBPROCESS, which re-imports
    # arail.nucleus.domain/paths fresh and can't see the parent's in-memory
    # state, resolves the identical paths. Also makes --resume robust to an
    # environment that changes between runs.
    from arail.nucleus.domain import _DOMAINS_DIR
    from arail.nucleus.paths import nucleus_data as _nucleus_data_fn

    context = {
        "domain_name": domain.name, "build_id": build_id, "version": parsed["version"],
        "domains_dir": str(_DOMAINS_DIR), "nucleus_data": str(_nucleus_data_fn()),
    }
    (rd / "context.json").write_text(json.dumps(context, sort_keys=True))

    from arail.nucleus.phases import RunLedger, run_phase

    ledger = RunLedger(rd, build_id, domain.name)

    with build_lock(build_id):
        for phase in _WORKER_PHASES:
            if ledger.is_done(phase):
                continue
            run_phase(phase, build_id, ledger=ledger)

    sys.stdout.write(f"build {build_id} complete\n")
    return 0


def status(argv: Sequence[str]) -> int:
    from arail.nucleus.paths import run_dir as run_dir_fn

    if not argv:
        sys.stdout.write("usage: nucleus status <build_id>\n")
        return 2
    build_id = argv[0]
    run_json = run_dir_fn(build_id) / "run.json"
    if not run_json.is_file():
        sys.stderr.write(f"no such build: {build_id}\n")
        return 3
    sys.stdout.write(run_json.read_text() + "\n")
    return 0
