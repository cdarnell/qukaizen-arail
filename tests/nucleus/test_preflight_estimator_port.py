"""Pure-math preflight estimator — ported from
tests/build/test_preflight_estimator.py (ARCHITECTURE.md §8 salvage plan).

The original tested arail.build.preflight.estimate(PreflightSpec) end to
end; that spec/estimate abstraction (Anthropic pricing, teacher
amplification, remote wall-clock rows) is intentionally dropped per §4.5.
These cases instead exercise the salvaged pieces directly — _status(),
active_params_b(), and the LoRA memory formula body (_lora_memory_gb) —
verifying the same green/red/amber judgments the original made for
equivalent inputs (salvage fidelity)."""

from __future__ import annotations

from arail.nucleus.preflight import _lora_memory_gb, _status, active_params_b


def test_small_lora_is_green_against_a_generous_budget():
    # 3B q4 LoRA — the original's test_small_lora_is_green used exactly
    # this shape and asserted VRAM status "green".
    needed = _lora_memory_gb(3.0, precision="q4")
    assert _status(needed, 24.0) == "green"


def test_20b_full_finetune_style_load_is_red_on_a_small_budget():
    # The original's test_20b_full_finetune_is_red_on_vram used bf16/full
    # (10 B/param optimizer state); _lora_memory_gb approximates the
    # weights term the same way (bf16 bpp=2.0) — a 20B bf16 load alone
    # already exceeds a 24GB budget once optimizer+activations are added.
    needed = _lora_memory_gb(20.0, precision="bf16")
    assert _status(needed, 24.0) == "red"


def test_moe_active_params_below_dense_equivalent():
    dense = active_params_b(20.0, is_moe=False)
    moe = active_params_b(20.0, is_moe=True, num_experts=32, top_k=4)
    assert moe < dense
    # gpt-oss-20b-ish shape: ~4/32 experts active -> well under half the params.
    assert moe < 20.0 * 0.55


def test_status_green_amber_red_thresholds():
    assert _status(10.0, 100.0) == "green"     # well under 85%
    assert _status(90.0, 100.0) == "amber"      # over 85%, under 100%
    assert _status(150.0, 100.0) == "red"       # over capacity
    assert _status(10.0, 0.0) == "red"          # no capacity at all


def test_larger_params_need_more_memory_monotonic():
    small = _lora_memory_gb(1.0)
    large = _lora_memory_gb(20.0)
    assert large > small
