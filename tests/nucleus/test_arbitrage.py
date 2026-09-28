"""arail.nucleus.arbitrage — stop rules (dev-only, no cert access)."""

from __future__ import annotations

from arail.nucleus.arbitrage import ArbitrageConfig, CycleResult, run


def test_stops_at_target_reached():
    curve = [0.3, 0.5, 0.9]

    def train_cycle_fn(idx):
        return CycleResult(cycle=idx, dev_proxy_composite=curve[idx - 1])

    result = run(None, None, ArbitrageConfig(target=0.8, max_cycles=10), train_cycle_fn=train_cycle_fn)
    assert result.stop_metric == "target_reached"
    assert len(result.cycles) == 3


def test_stops_at_plateau():
    curve = [0.3, 0.5, 0.51, 0.511, 0.512]  # improvement < 0.005 for 3 in a row

    def train_cycle_fn(idx):
        return CycleResult(cycle=idx, dev_proxy_composite=curve[min(idx - 1, len(curve) - 1)])

    result = run(None, None, ArbitrageConfig(target=0.99, max_cycles=10), train_cycle_fn=train_cycle_fn)
    assert result.stop_metric == "fidelity_plateau_3_cycles"


def test_stops_at_max_cycles_budget():
    def train_cycle_fn(idx):
        return CycleResult(cycle=idx, dev_proxy_composite=0.1 * idx)  # keeps improving, never plateaus

    result = run(None, None, ArbitrageConfig(target=0.99, max_cycles=5), train_cycle_fn=train_cycle_fn)
    assert result.stop_metric == "budget"
    assert len(result.cycles) == 5


def test_stops_at_max_hours_budget():
    def train_cycle_fn(idx):
        return CycleResult(cycle=idx, dev_proxy_composite=0.1 * idx, elapsed_hours=5.0)

    result = run(None, None, ArbitrageConfig(target=0.99, max_cycles=100, max_hours=4.0),
                train_cycle_fn=train_cycle_fn)
    assert result.stop_metric == "budget"
    assert len(result.cycles) == 1


def test_best_cycle_is_tracked():
    curve = [0.3, 0.9, 0.5]

    def train_cycle_fn(idx):
        return CycleResult(cycle=idx, dev_proxy_composite=curve[idx - 1])

    result = run(None, None, ArbitrageConfig(target=0.95, max_cycles=3), train_cycle_fn=train_cycle_fn)
    assert result.best_cycle.dev_proxy_composite == 0.9


def test_arbitrage_jsonl_log_written(tmp_path):
    log_path = tmp_path / "arbitrage.jsonl"

    def train_cycle_fn(idx):
        return CycleResult(cycle=idx, dev_proxy_composite=0.9)

    run(None, None, ArbitrageConfig(target=0.8, max_cycles=3), train_cycle_fn=train_cycle_fn,
       log_path=log_path)
    lines = log_path.read_text().splitlines()
    assert len(lines) == 1
    import json
    row = json.loads(lines[0])
    assert row["cycle"] == 1
