"""Dev-only training loop controller + stop rules (ARCHITECTURE.md §4.8).

Signature: ``run(dev, train, cfg) -> ArbitrageResult``. There is
deliberately no cert parameter anywhere on this module, and it imports
neither ``CertStore`` nor ``CertAccess`` — enforced statically by
tests/nucleus/test_splits.py::test_arbitrage_module_does_not_import_certstore
(T-CERT-3's import-lint half) now that this file exists. Arbitrage can
literally never see the cert set; fidelity.achieved is always measured
once, separately, in Phase C.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional

STOP_TARGET_REACHED = "target_reached"
STOP_FIDELITY_PLATEAU = "fidelity_plateau_3_cycles"
STOP_BUDGET = "budget"

_PLATEAU_CYCLES = 3
_PLATEAU_MIN_IMPROVEMENT = 0.005


@dataclass(frozen=True)
class ArbitrageConfig:
    target: float
    max_cycles: int = 20
    max_hours: float = 8.0


@dataclass(frozen=True)
class CycleResult:
    cycle: int
    dev_proxy_composite: float
    adapter_path: Optional[str] = None
    elapsed_hours: float = 0.0


@dataclass(frozen=True)
class ArbitrageResult:
    cycles: List[CycleResult]
    stop_metric: str
    best_cycle: CycleResult


def run(
    dev: Any, train: Any, cfg: ArbitrageConfig, *,
    train_cycle_fn: Callable[[int], CycleResult],
    log_path: Optional[Path] = None,
) -> ArbitrageResult:
    """``train_cycle_fn(cycle_idx) -> CycleResult`` does the actual work
    (one LoRA training cycle + a dev-only proxy eval) — real training
    (train/mlx_kd.py) or the stub trainer, the caller decides; this
    function only implements the stop-rule state machine and logging.

    Stop rules, first to fire wins: dev.proxy_composite >= target ->
    target_reached; no improvement >= 0.005 for 3 consecutive cycles ->
    fidelity_plateau_3_cycles; max_cycles or max_hours -> budget.
    """
    cycles: List[CycleResult] = []
    best = -1.0
    plateau_count = 0
    stop_metric = STOP_BUDGET

    for cycle_idx in range(1, cfg.max_cycles + 1):
        result = train_cycle_fn(cycle_idx)
        cycles.append(result)
        if log_path is not None:
            _append_jsonl(log_path, {
                "cycle": result.cycle, "dev_proxy_composite": result.dev_proxy_composite,
                "adapter_path": result.adapter_path, "elapsed_hours": result.elapsed_hours,
            })

        if result.dev_proxy_composite >= cfg.target:
            stop_metric = STOP_TARGET_REACHED
            break

        improvement = result.dev_proxy_composite - best
        if improvement >= _PLATEAU_MIN_IMPROVEMENT:
            plateau_count = 0
        else:
            plateau_count += 1
        best = max(best, result.dev_proxy_composite)

        if plateau_count >= _PLATEAU_CYCLES:
            stop_metric = STOP_FIDELITY_PLATEAU
            break

        if result.elapsed_hours >= cfg.max_hours:
            stop_metric = STOP_BUDGET
            break
    else:
        stop_metric = STOP_BUDGET

    best_cycle = max(cycles, key=lambda c: c.dev_proxy_composite)
    return ArbitrageResult(cycles=cycles, stop_metric=stop_metric, best_cycle=best_cycle)


def _append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, sort_keys=True) + "\n")
