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


def _provider_label(domain) -> str:
    """The provider that actually ran THIS phase, for the phase's own
    output dict (B1: an auditable stamp per phase, in addition to the
    build-level record in context.json)."""
    return "stub" if _is_stub() else domain.runtime


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


def _resolve_preflight_models(domain) -> Dict[str, Any]:
    """Best-effort resolution of student/teacher/base/judge for the
    preflight memory plan (B6, 2026-09-23 review: `build.run` used to call
    `run_preflight()` with no models at all, so the memory plan never ran
    for a real build). Any role that can't be resolved yet (model not
    downloaded, teacher left as "auto" -- auto-select isn't wired this
    sprint, see BACKLOG) is simply omitted; preflight treats a missing
    role as "not sized this run", not an error -- resolution failures
    surface for real at PA/PC time instead (B7)."""
    from arail.nucleus.models import resolve_model

    models: Dict[str, Any] = {}
    for key, name in (
        ("student_model", domain.student_base),
        ("base_student_model", domain.student_base),
        ("judge_model", "ai-engineer"),
    ):
        try:
            models[key] = resolve_model(name)
        except Exception:  # noqa: BLE001 — unresolved roles are sized as "unknown", not fatal here
            models[key] = None
    if domain.teacher_model and domain.teacher_model != "auto":
        try:
            models["teacher_model"] = resolve_model(domain.teacher_model)
        except Exception:  # noqa: BLE001
            models["teacher_model"] = None
    else:
        models["teacher_model"] = None
    return models


def _provider_for_role(role: str, model_name: str, *, run_dir: Path, top_n: int,
                       model_path: "Path | str | None" = None, answers: "dict | None" = None):
    """``model_path``, when given, is used directly instead of resolving
    ``model_name`` under ARAIL_MODELS_DIR (B3, 2026-09-23 review) — PC's
    fused-student role passes fuse's own recorded output_dir here, since
    the fused shard has no ARAIL_MODELS_DIR alias/name of its own.
    ``answers``, in stub mode, is a real (item_id, role) -> text table
    (B10: tuned from real gold labels so closed-task F1 is a known
    non-trivial rational instead of the stub's generic unparseable
    fallback text)."""
    if _is_stub():
        from arail.nucleus.providers.stub import StubProvider

        return StubProvider(role=role, answers=answers)

    from arail.nucleus.providers.queuellm import QueueLLMProvider

    if model_path is not None:
        return QueueLLMProvider(str(model_path), run_dir=run_dir)

    from arail.nucleus.models import resolve_model

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
           "n_dev": len(splits.dev), "index_path": str(index_path), "provider": _provider_label(domain)}


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

    return {"n_cert": len(cert_items), "provider": _provider_label(domain)}


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
           "best_dev_proxy_composite": result.best_cycle.dev_proxy_composite,
           "provider": _provider_label(domain)}


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
    # B9 (2026-09-23 review): shard_root is the directory `certify` must
    # later write dna-card.yaml/seal.json/build-report.md/eval-config.lock
    # into -- the SAME shard/version directory this phase mints under
    # FORGE_ROOT, recorded here (not re-derived by certify calling
    # shard_dir() a second time, which would refuse with "already exists").
    shard_root = shard_dir(domain.shard, version)
    output_dir = shard_root / "weights" / "mlx"

    if _is_stub():
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "FUSED_STUB_MARKER").write_text("stub fuse — not a real model\n")
    else:
        from arail.nucleus.models import resolve_model
        from arail.nucleus.train.mlx_kd import fuse as mlx_fuse

        train_dir = _run_dir(context) / "train"
        student = resolve_model(domain.student_base)
        mlx_fuse(str(student.path), train_dir / "adapter", output_dir)

    return {"shard": domain.shard, "version": version, "shard_dir": str(shard_root),
           "output_dir": str(output_dir), "provider": _provider_label(domain)}


# ── PC: eval (student, base, judge on cert) ────────────────────────────

_CLOSED_TASKS = None  # populated lazily inside _phase_eval (avoids a module-load-order import)


def _stub_closed_answer_table(cert_items, *, salt: str, wrong_every: int) -> dict:
    """B10 (2026-09-23 review): a real ``(item_id, role) -> text`` answer
    table, built from the cert set's actual gold labels, deterministically
    WRONG on 1-in-``wrong_every`` items (a stable hash of item_id, never
    randomness) — so closed-task F1 is a known, reproducible, non-trivial
    rational (not 0.0 from the stub's generic unparseable fallback text,
    and not a hardcoded metric — the F1 is still computed for real from
    these generated answers against gold)."""
    import hashlib

    from arail.nucleus.evals.tasks.linux_kernel import cve_detection_task, subsystem_routing_task

    answers: dict = {}

    cve_prompts, cve_gold = cve_detection_task(cert_items)
    for p, gold_label in zip(cve_prompts, cve_gold):
        h = int(hashlib.sha256(f"{salt}:{p.item_id}".encode()).hexdigest(), 16)
        wrong = (h % wrong_every) == 0
        answer = ("not" if gold_label == "cve" else "cve") if wrong else gold_label
        answers[(p.item_id, "cve_detection")] = answer

    sub_prompts, sub_gold = subsystem_routing_task(cert_items)
    subsystems = sorted(set(sub_gold)) or ["unknown"]
    for p, gold_label in zip(sub_prompts, sub_gold):
        h = int(hashlib.sha256(f"{salt}:{p.item_id}".encode()).hexdigest(), 16)
        wrong = (h % wrong_every) == 0
        if wrong:
            alternatives = [s for s in subsystems if s != gold_label] or [gold_label]
            answer = alternatives[h % len(alternatives)]
        else:
            answer = gold_label
        # subsystem_routing's parser reads text.split(":")[0] -- match that shape.
        answers[(p.item_id, "subsystem_routing")] = f"{answer}: fixture answer"

    return answers


def _score_closed_tasks(cert_items, *, run_dir: Path, model_name: str = "", model_path=None,
                        answers: "dict | None" = None):
    """Runs every closed task against ONE resolved model (fused shard or
    unfused base — caller picks via model_name/model_path) and returns
    (rows, mean_f1). Shared by the fused-student and base-student passes
    so `beats_base` in certify.py is a real comparison (B3), not a
    hardcoded 0.0 baseline."""
    from arail.nucleus.evals import closed
    from arail.nucleus.evals.tasks.linux_kernel import (
        cve_detection_task, parse_closed_answer, subsystem_routing_task,
    )

    rows = []
    for task_fn, valid in (
        (cve_detection_task, ("cve", "not")),
        (subsystem_routing_task, None),
    ):
        prompts, gold = task_fn(cert_items)
        provider = _provider_for_role("student", model_name, run_dir=run_dir, top_n=1, model_path=model_path,
                                      answers=answers)
        try:
            generations = provider.generate(prompts, Decoding())
        finally:
            provider.close()

        if valid is not None:
            pred = [parse_closed_answer(g.text, valid=valid) for g in generations]
            rows.append(closed.binary_prf1(gold, pred, positive="cve"))
        else:
            pred = [g.text.strip().split(":")[0].strip() or "unknown" for g in generations]
            rows.append(closed.macro_f1(gold, pred))
    return rows, closed.mean_f1(rows)


def _open_answer_table(prompts, *, salt: str) -> dict:
    """Deterministic per-item open-eval completion text, DIFFERENT between
    the fused and base stub providers (R1, 2026-09-23 review round 2):
    previously both roles fell through to StubProvider's identical generic
    fallback text ("[stub:good] deterministic answer for <item_id>", the
    same string for every role), so the LC judge had nothing to
    distinguish and the golden lc_win_rate came out at exactly the
    position-randomization average, 0.5 -- a value that can't detect a
    broken A/B un-swap. Keyed by (item_id, "open") to match the Prompt
    role _score_open_lc uses.

    The trailing "." padding (0-6 chars, hashed from item_id AND salt, so
    it differs between the fused and base tables) gives the LC judge's
    length-control regression a genuinely varying per-item length delta
    to fit -- a constant-length template made every item's delta
    identical, which makes the regression's design matrix singular (no
    length signal to control for) and the Newton-Raphson fit silently
    stall at b0=b1=0, i.e. lc_win_rate=0.5, REGARDLESS of who actually
    won each item. This is cosmetic padding, not a quality signal --
    open_winner_table (not length) is the only thing that decides which
    side wins."""
    import hashlib

    answers = {}
    for p in prompts:
        pad_n = int(hashlib.sha256(f"open-pad:{salt}:{p.item_id}".encode()).hexdigest(), 16) % 7
        answers[(p.item_id, "open")] = f"[stub:open:{salt}] explanation for {p.item_id}" + ("." * pad_n)
    return answers


def _open_winner_table(prompts, *, wrong_every: int) -> dict:
    """item_id -> "model"|"baseline": the ground-truth winner for this
    stub run, a deterministic (hash-of-item_id, never random) function so
    the golden is reproducible -- "model" (fused) wins most items but not
    all (1-in-``wrong_every`` goes to "baseline"), so lc_win_rate is a
    known, non-trivial rational, never 0.0/1.0/0.5 by construction."""
    import hashlib

    table = {}
    for p in prompts:
        h = int(hashlib.sha256(f"open-winner:{p.item_id}".encode()).hexdigest(), 16)
        table[p.item_id] = "baseline" if (h % wrong_every) == 0 else "model"
    return table


def _score_open_lc(domain, *, fused_model_dir, run_dir: Path):
    """Pairwise LC-judged fused-vs-base on the domain's eyeball prompts
    (B3 item 7: wired for the stub path; the real-runtime judge path is
    unwired this sprint, same as every other real-mode gap tracked in the
    "Model Forge real-runtime wiring" BACKLOG entry, and B7 already
    refuses non-stub builds up front). Genuinely computed from generated
    text through evals/open_lc_judge.py's real position-randomization +
    LC-regression math -- never a constant.

    R1 (2026-09-23 review round 2): the fused and base stub providers now
    generate DIFFERENT text (_open_answer_table), and the judge used here
    is a content-based oracle that knows the true per-item winner
    (_open_winner_table) -- not StubJudge's old "always answer A"
    default, whose win rate was exactly 0.5 regardless of what either
    side actually said, so it could never catch a broken position-swap.
    """
    from arail.nucleus.evals import open_lc_judge
    from arail.nucleus.providers.base import Prompt

    if not _is_stub():
        return {"lc_win_rate": "not_run", "reason": "real-runtime judge not wired this sprint"}

    lines = [ln for ln in domain.eval_eyeball_prompts.read_text().splitlines() if ln.strip()][:10]
    prompts = [Prompt(item_id=f"eyeball-{i}", text=ln, role="open") for i, ln in enumerate(lines)]

    winner_table = _open_winner_table(prompts, wrong_every=4)
    fused_answers = _open_answer_table(prompts, salt="fused-v1")
    base_answers = _open_answer_table(prompts, salt="base-v1")

    provider_fused = _provider_for_role("student", "", run_dir=run_dir, top_n=1, model_path=fused_model_dir,
                                        answers=fused_answers)
    try:
        fused_gens = provider_fused.generate(prompts, Decoding())
    finally:
        provider_fused.close()
    provider_base = _provider_for_role("student", domain.student_base, run_dir=run_dir, top_n=1,
                                       answers=base_answers)
    try:
        base_gens = provider_base.generate(prompts, Decoding())
    finally:
        provider_base.close()

    fused_text_by_id = {g.item_id: g.text for g in fused_gens}
    base_text_by_id = {g.item_id: g.text for g in base_gens}

    pairs = [
        open_lc_judge.PairItem(item_id=p.item_id, text_model=fused_text_by_id[p.item_id],
                               text_baseline=base_text_by_id[p.item_id])
        for p in prompts
    ]

    # A content-based judge: it knows the ground-truth winner PER ITEM
    # (winner_table), not per letter -- so it answers correctly whichever
    # side (A or B) the winning text lands on after randomize_and_judge's
    # per-item position swap. A bug that failed to un-swap the position
    # correctly would make this judge disagree with the recorded winner
    # on roughly half the items, which changes lc_win_rate -- unlike the
    # old always-answer-"A" judge, whose win rate was 0.5 regardless of
    # what either side actually said.
    def _judge_fn(item_id: str, text_a: str, text_b: str) -> str:
        winner_text = fused_text_by_id[item_id] if winner_table[item_id] == "model" else base_text_by_id[item_id]
        return "A" if text_a == winner_text else "B"

    judged = open_lc_judge.randomize_and_judge(pairs, seed=7, judge_fn=_judge_fn)
    len_model = {g.item_id: len(g.text) for g in fused_gens}
    len_baseline = {g.item_id: len(g.text) for g in base_gens}
    result = open_lc_judge.score(judged, len_model=len_model, len_baseline=len_baseline, seed=42)
    return {"lc_win_rate": result.lc_win_rate, "n": result.n, "invalid_rate": result.invalid_rate,
           "ci95": list(result.ci95)}


def _phase_eval(context: dict) -> dict:
    domain = _resolve_domain(context)
    from arail.nucleus.evals import composite
    from arail.nucleus.evals.splits import CertStore

    run_dir = _run_dir(context)
    cert_access = CertStore(nucleus_data=_nucleus_data(context)).open(domain.name)
    cert_items = cert_access.open()

    # B3 (2026-09-23 review): PC evaluates the FUSED student (fuse's
    # recorded output_dir) -- never the base weights under a different
    # name -- and separately scores the unfused base student for a real
    # `beats_base` comparison downstream in certify.py.
    fuse_output_path = run_dir / "phase_output" / "fuse.json"
    if not fuse_output_path.is_file():
        raise RefusedByPolicy(
            f"PC refused: no fuse phase output at {fuse_output_path} — fuse must run before PC"
        )
    fuse_output = json.loads(fuse_output_path.read_text())
    fused_model_dir = fuse_output["output_dir"]

    fused_answers = _stub_closed_answer_table(cert_items, salt="fused-v1", wrong_every=5) if _is_stub() else None
    base_answers = _stub_closed_answer_table(cert_items, salt="base-v1", wrong_every=2) if _is_stub() else None

    fused_rows, fused_mean_f1 = _score_closed_tasks(cert_items, run_dir=run_dir, model_path=fused_model_dir,
                                                    answers=fused_answers)
    base_rows, base_mean_f1 = _score_closed_tasks(cert_items, run_dir=run_dir, model_name=domain.student_base,
                                                  answers=base_answers)

    open_metrics = _score_open_lc(domain, fused_model_dir=fused_model_dir, run_dir=run_dir)

    # `compiles`/`patch_applies`/`checkpatch_clean` need a model-generated
    # PATCH against a real base repo -- no patch-generation task exists in
    # this sprint's task-adapter seam (only cve_detection/subsystem_routing/
    # open explanation), so these stay honestly not_run rather than a
    # hardcoded number. Filed in the "Model Forge real-runtime wiring"
    # BACKLOG entry; composite/v1-open (composite.py) is selected instead
    # of v1/v1-nc whenever executable is wholly not_run, so the composite
    # is computed from what was actually measured (closed + open), not
    # blocked on an unmeasured input.
    metrics = {
        "closed": {"mean_f1": fused_mean_f1, "rows": fused_rows},
        "base_closed": {"mean_f1": base_mean_f1, "rows": base_rows},
        "open": open_metrics,
        "executable": {"compiles": composite.NOT_RUN, "patch_applies": composite.NOT_RUN,
                       "checkpatch_clean": composite.NOT_RUN},
    }

    eval_dir = run_dir / "eval"
    eval_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    (eval_dir / "metrics.json").write_text(json.dumps(metrics, sort_keys=True, default=str))

    return {"closed_mean_f1": fused_mean_f1, "base_closed_mean_f1": base_mean_f1,
           "n_cert": len(cert_items), "provider": _provider_label(domain)}


# ── dispatch ──────────────────────────────────────────────────────────

_PHASE_FUNCS = {
    "PA": _phase_extract_train, "PA2": _phase_extract_cert,
    "PB": _phase_train, "fuse": _phase_fuse, "PC": _phase_eval,
}


def run_phase_body(phase: str, context: dict) -> dict:
    if phase not in _PHASE_FUNCS:
        raise RefusedByPolicy(f"unknown worker phase {phase!r}")
    return _PHASE_FUNCS[phase](context)


def persist_phase_output(context: dict, phase: str, output: dict) -> Path:
    """Writes run_dir/phase_output/<phase>.json — the same file worker.py
    writes after a real subprocess phase, and what certify.py reads (e.g.
    fuse.json's shard/version/shard_dir, B9). Exposed here so in-process
    callers (tests, and any future non-subprocess phase runner) that call
    run_phase_body() directly can keep that file in sync without
    duplicating worker.py's write logic."""
    out_dir = _run_dir(context) / "phase_output"
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    out_path = out_dir / f"{phase}.json"
    out_path.write_text(json.dumps(output or {}, sort_keys=True, default=str))
    return out_path


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

    # B7 (2026-09-23 review): refuse a non-stub build up front, before any
    # lock, run dir, or phase exists -- not several steps in, as a
    # misleading "model '' not found" error out of a half-created run
    # (teacher auto-select, the logprob probe, and the real MLX
    # multi-cycle training loop are not wired this sprint; see
    # _mlx_train_cycle_fn and sprints/BACKLOG.md "Model Forge real-runtime
    # wiring").
    if not _is_stub():
        raise RefusedByPolicy(
            "real-runtime builds are not wired yet in sprint 1 (teacher "
            "selection, logprob probe, and MLX training cycle -- see "
            "BACKLOG 'Model Forge real-runtime wiring'). Gate A runs with "
            "ARAIL_NUCLEUS_STUB=1."
        )

    from arail.nucleus.providers.gateway import profile_gate

    profile_gate(domain.teacher_profile)

    # run_preflight() itself raises PreflightRefusal (mapped to exit 3 by
    # cli.py) for any red PHASE row (A/A2/B/C). It does NOT raise for a red
    # CAPABILITY row (deep runtime missing, mlx_lm out of range, ...) --
    # those are reported but non-fatal at the API level, so this function
    # is the one that turns report.has_red into a refusal for that case.
    preflight_models = _resolve_preflight_models(domain)
    report = preflight_mod.run_preflight(domain, memory_budget_gb=parsed["memory_budget_gb"],
                                         runtime_streams=(domain.runtime == "queuellm"),
                                         **preflight_models)
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

    # B1 (2026-09-23 review): record whether THIS build actually ran on the
    # stub provider at build time, on disk, in the one place certify reads
    # from — never re-derived from whatever ARAIL_NUCLEUS_STUB happens to
    # be set to when `certify` is invoked later (possibly a different
    # process, a different shell, minutes or days later). Without this, a
    # stub build could be certified as a trusted, ledgered "real" card
    # simply by running certify with the env var unset.
    context = {
        "domain_name": domain.name, "build_id": build_id, "version": parsed["version"],
        "domains_dir": str(_DOMAINS_DIR), "nucleus_data": str(_nucleus_data_fn()),
        "stub": _is_stub(), "provider": ("stub" if _is_stub() else domain.runtime),
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
