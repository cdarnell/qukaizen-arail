"""arail.experiments.mlx_backend — AeroLLM MLX bench runner.

Apple-native analog of `bench.py`. The BenchRun shape, JSONL format,
and git-snapshot behavior match the AeroLLM CUDA runner so the
dashboard can render either dataset without a branch in the UI.

What's different:

  - Backend construction calls `mlx_lm.load(...)` and `mlx_lm.generate(...)`
    directly. There's no AEROLLM_MODEL env-var shim — mlx-lm takes
    its config as function arguments, so we pass them explicitly.
  - Knob translation happens in Python, not in the environment.
    `kv_bits`, `max_kv_size`, `prefill_step_size`, etc. are
    forwarded as kwargs to `mlx_lm.generate`.
  - If mlx_lm isn't installed (e.g. running in a non-Apple CI),
    we still return a BenchRun — status="error" with a clear reason.
    The autoresearch loop records the error and moves on, so the
    page stays honest.

This module DOES NOT import mlx_lm at module scope. We defer the
import to run_mlx_bench() so that just importing the module works
on any platform.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from arail.experiments.bench import (
    BenchRun,
    _now,
    _peak_rss_mb,
    _read_io_bytes,
)


# ── File / path helpers ──────────────────────────────────────────────

def mlx_bench_file() -> Path:
    """The JSONL log for MLX bench runs. Parallel to AeroLLM's
    aerollm-bench.jsonl so both backends can coexist."""
    from arail.config import DATA_DIR
    return DATA_DIR / "mlx-bench.jsonl"


# ── Knob translation ─────────────────────────────────────────────────

# Which MLX knobs get passed as generate() kwargs vs used to pick
# which model to load. Centralized here so the rest of the loop
# doesn't need to know MLX's argument surface.
_GENERATE_KWARG_KNOBS = {
    "max_kv_size",
    "prefill_step_size",
}

# Knobs that control KV-cache quantization — translated into the two
# kwargs mlx_lm expects (kv_bits + quantized_kv_start). "fp16" means
# "don't quantize at all", which for mlx_lm means kv_bits=None.
_KV_BITS_MAP = {
    "fp16": None,
    "8bit": 8,
    "4bit": 4,
}


def _build_generate_kwargs(knob_values: Dict[str, Any]) -> Dict[str, Any]:
    """Translate a knob snapshot into the kwargs mlx_lm.generate takes."""
    kwargs: Dict[str, Any] = {}

    kv_bits_raw = knob_values.get("kv_bits", "fp16")
    kv_bits = _KV_BITS_MAP.get(kv_bits_raw)
    if kv_bits is not None:
        kwargs["kv_bits"] = kv_bits
        # Only meaningful when kv_bits is set.
        start = knob_values.get("quantized_kv_start", 0)
        if isinstance(start, int):
            kwargs["quantized_kv_start"] = start

    for k in _GENERATE_KWARG_KNOBS:
        if k in knob_values:
            kwargs[k] = knob_values[k]

    return kwargs


def unsupported_combination(knob_values: Dict[str, Any]) -> Optional[str]:
    """Why this knob combination cannot run on the current mlx_lm, or None.

    Checked BEFORE a variant branch is created, because the alternative
    is what the first real tuning pass did: run the full bench N times,
    collect N identical NotImplementedErrors, and report "no measurable
    tok/s" — a message that says nothing about the actual cause.

    Known limitation: mlx_lm raises `RotatingKVCache Quantization NYI`
    when KV quantization meets a rotating cache, and `max_kv_size` is
    what makes the cache rotating. It is set to 4096 by default, so
    every kv_bits variant in the catalog is dead on arrival — three of
    the eight MLX candidates, nine wasted model loads per pass.
    """
    kv_bits = knob_values.get("kv_bits", "fp16")
    quantizes_kv = kv_bits not in (None, "", "fp16")
    max_kv = knob_values.get("max_kv_size")
    if quantizes_kv and isinstance(max_kv, int) and max_kv > 0:
        return (f"mlx_lm cannot quantize a rotating KV cache "
                f"(kv_bits={kv_bits!r} with max_kv_size={max_kv}): "
                "'RotatingKVCache Quantization NYI'. Unset max_kv_size to "
                "test KV quantization, or drop the kv_bits knob.")
    return None


def resolve_model_path(model_id: str) -> str:
    """Prefer a local checkout under ARAIL_MODELS_DIR over an HF id.

    The lab is airgapped by default, so an uncached HF id is not a slow
    path — it is a dead one: mlx_lm raises LocalEntryNotFoundError and
    the whole tuning pass aborts at baseline. Meanwhile the checkpoint
    is usually right there on disk; `ARAIL_MODELS_DIR` is the documented
    place for it (see docs/models-on-disk.md), and on shared machines it
    points at a pooled directory.

    So: if `<ARAIL_MODELS_DIR>/<basename>` is a real model directory, use
    it. Otherwise hand back the id untouched and let mlx_lm decide —
    a hybrid lab with a warm HF cache still works exactly as before.
    """
    if not model_id or os.path.isdir(model_id):
        return model_id
    try:
        from arail.config import MODELS_DIR
    except Exception:
        return model_id
    candidate = Path(MODELS_DIR) / model_id.rsplit("/", 1)[-1]
    if (candidate / "config.json").is_file():
        return str(candidate)
    return model_id


def _pick_model_id(knob_values: Dict[str, Any], fallback: str) -> str:
    """Which model to load. If the agent specified a
    model_quant_variant override, honor it; else use the
    research_model.name from the config. Either way the result is
    resolved against the local models dir first."""
    override = knob_values.get("model_quant_variant")
    if isinstance(override, str) and override:
        return resolve_model_path(override)
    return resolve_model_path(fallback)


# ── Runner ───────────────────────────────────────────────────────────

def run_mlx_bench(
    *,
    research_model_name: str,
    prompt: str,
    max_tokens: int,
    knob_values: Dict[str, Any],
    variant_label: Optional[str] = None,
    _mlx_lm: Any = None,  # dependency injection for tests
) -> BenchRun:
    """Run the MLX research model once and return a BenchRun. The
    shape matches the AeroLLM run_bench exactly so the dashboard can
    treat them interchangeably.

    If mlx_lm is not importable on this host, returns a BenchRun with
    status='error' and error='mlx_lm not installed ...'. The caller
    should still append that row — it's real evidence that the host
    can't run MLX.
    """
    from arail.experiments.git_ops import git_state

    # Snapshot git state first, so every record is always pinnable.
    gs = git_state()

    model_id = _pick_model_id(knob_values, fallback=research_model_name)
    gen_kwargs = _build_generate_kwargs(knob_values)
    prompt_cache_enabled = bool(knob_values.get("prompt_cache_enabled", False))

    # Stage timers. Using perf_counter (monotonic, high-resolution) for
    # stage deltas while keeping time.time() for the ISO timestamp on
    # the record. Four perf_counter reads total — ~80ns overhead per
    # run — so this is well below any threshold that could affect
    # tokens/sec measurements. If future instrumentation threatens to
    # change that, gate it on knob_values.get("trace_enabled") and
    # sweep both states in autoresearch to catch regression.
    t0 = time.time()
    p_start = time.perf_counter()
    io_start = _read_io_bytes() or 0
    tokens_out = 0
    status = "ok"
    error: Optional[str] = None
    ttft_ms: Optional[float] = None
    decode_tps: Optional[float] = None
    load_ms: Optional[float] = None
    decode_ms: Optional[float] = None

    try:
        mlx_lm = _mlx_lm
        if mlx_lm is None:
            try:
                import mlx_lm  # type: ignore[import-not-found]
            except ImportError as ie:
                raise RuntimeError(
                    "mlx_lm not installed on this host. "
                    "On Apple Silicon: pip install mlx-lm. "
                    f"Underlying ImportError: {ie}"
                ) from ie

        model, tokenizer = mlx_lm.load(model_id)
        p_loaded = time.perf_counter()
        load_ms = (p_loaded - p_start) * 1000.0

        # TTFT approximation: single-token generation with the same
        # prompt. Same strategy as the AeroLLM bench — sub-token
        # precision isn't needed for a relative-improvement loop.
        t_warm = time.time()
        _ = mlx_lm.generate(
            model, tokenizer, prompt=prompt, max_tokens=1,
            verbose=False,
            **{k: v for k, v in gen_kwargs.items()
               if k in {"kv_bits", "quantized_kv_start"}},
        )
        ttft_ms = (time.time() - t_warm) * 1000.0
        p_prefilled = time.perf_counter()

        # Full run.
        text = mlx_lm.generate(
            model, tokenizer, prompt=prompt, max_tokens=max_tokens,
            verbose=False,
            **gen_kwargs,
        )
        p_decoded = time.perf_counter()
        decode_ms = (p_decoded - p_prefilled) * 1000.0
        # mlx_lm.generate returns the generated text; we approximate
        # tokens_out by encoding the text back. Not exact but stable
        # within a single tokenizer.
        try:
            tokens_out = len(tokenizer.encode(text or ""))
        except Exception:
            tokens_out = 0
        _ = prompt_cache_enabled  # reserved for a future pass
    except Exception as exc:
        status = "error"
        error = f"{type(exc).__name__}: {exc}"

    total_ms = (time.time() - t0) * 1000.0

    # Assemble the stage dict only from fields we actually measured.
    # On failure partway through, whichever stages completed are
    # retained so the UI can show "died during decode" rather than
    # "no data".
    stages: Optional[Dict[str, float]] = None
    if load_ms is not None or ttft_ms is not None or decode_ms is not None:
        stages = {}
        if load_ms is not None:
            stages["load_ms"] = round(load_ms, 2)
        if ttft_ms is not None:
            # Prefill is the TTFT call — one-token warmup. Name it
            # "prefill" in the stages dict for clarity; the top-level
            # ttft_ms field stays for backward-compat with the UI.
            stages["prefill_ms"] = round(ttft_ms, 2)
        if decode_ms is not None:
            stages["decode_ms"] = round(decode_ms, 2)
    if tokens_out > 1 and ttft_ms is not None and total_ms > ttft_ms:
        decode_tps = round(
            (tokens_out - 1) / ((total_ms - ttft_ms) / 1000.0), 3
        )

    io_end = _read_io_bytes() or 0
    bytes_read = max(io_end - io_start, 0) if (io_end and io_start) else None

    return BenchRun(
        ts=_now(),
        git_sha=gs.sha,
        git_short_sha=gs.short_sha,
        git_branch=gs.branch,
        git_dirty=gs.is_dirty,
        model=model_id,
        prompt=prompt,
        prompt_chars=len(prompt),
        max_tokens=max_tokens,
        tokens_out=tokens_out,
        total_latency_ms=round(total_ms, 2),
        ttft_ms=round(ttft_ms, 2) if ttft_ms is not None else None,
        decode_tok_per_sec=decode_tps,
        bytes_read=bytes_read,
        peak_rss_mb=_peak_rss_mb(),
        knob_values=dict(knob_values),
        variant_label=variant_label,
        status=status,
        error=error,
        stages=stages,
    )


# ── Frontier bench (the "doesn't fit any GPU" envelope) ──────────────
#
# Every frontier_models entry gets benched during baseline capture.
# Until the streaming layer exists,
# mlx_lm.load() on a 335 GB-500 GB model will fail on ANY 24 GB host
# — either with an out-of-memory error or because the repo isn't
# actually downloaded. Both outcomes are legitimate data points for
# the dashboard; we just need to capture them as structured BenchRuns
# rather than letting the exception escape and kill the loop.
#
# The key contract: `run_frontier_bench` ALWAYS returns a BenchRun.
# Never raises. The dashboard reads `.status` and `.error` to render
# either a successful row (once streaming works) or a "can't run yet:
# <reason>" card.


# Reasons the frontier bench might fail, in priority order. These
# strings are load-bearing — the dashboard pattern-matches on the
# prefix to render badges. If you change a prefix here, update
# templates/tuning.html to match.
FRONTIER_ERROR_PREFIX = {
    "streaming_required":
        "streaming_required: this model's 4-bit weights exceed the "
        "host's physical memory. Waiting on the MLX streaming layer.",
    "mlx_lm_missing":
        "mlx_lm_missing: not running on Apple Silicon / mlx-lm not "
        "installed. Frontier bench is a no-op on this host.",
    "load_failed":
        "load_failed: mlx_lm.load raised. Root cause: ",
    "oom":
        "oom: process exhausted unified memory during load. "
        "Expected — see gpu_fit table in tuning-mlx.yml.",
}


def run_frontier_bench(
    *,
    model: Any,  # FrontierModel — declared Any to avoid circular imports
    prompt: str,
    max_tokens: int,
    _mlx_lm: Any = None,          # dependency injection for tests
    _force_reason: Optional[str] = None,  # dependency injection for tests
) -> BenchRun:
    """Attempt a benchmark against a frontier model. Always returns a
    BenchRun — never raises. Until the MLX streaming layer is built,
    this function's job is to produce *honest* failure records that
    the dashboard can render.

    The success criterion for the lab is concrete: autoresearch should
    find a knob configuration that moves inference from ~5 minutes
    down to ~3 minutes on a model that shouldn't even load. Until the
    streaming layer exists, every row here reports 'streaming_required'
    and the loop moves on to the small-model track — which is still
    accumulating the baseline measurements the frontier will be
    compared against.
    """
    from arail.experiments.git_ops import git_state

    gs = git_state()
    t0 = time.time()
    status = "error"
    error: Optional[str] = None

    # Explicit short-circuits. These are the cases we can detect
    # without actually touching the model, and they dominate the
    # failure distribution in 2026.
    if _force_reason:
        error = FRONTIER_ERROR_PREFIX.get(_force_reason, _force_reason)
    elif getattr(model, "streaming_required", False):
        # Until layer streaming lands, this is the honest truth.
        error = FRONTIER_ERROR_PREFIX["streaming_required"]
    else:
        # Try to actually load it. On a 24 GB host loading a 335 GB
        # model will OOM quickly — the error message varies by macOS
        # version, so we capture whatever exception comes back.
        try:
            mlx_lm = _mlx_lm
            if mlx_lm is None:
                try:
                    import mlx_lm  # type: ignore[import-not-found]
                except ImportError:
                    error = FRONTIER_ERROR_PREFIX["mlx_lm_missing"]
                    mlx_lm = None  # skip the load block
            if mlx_lm is not None:
                _model, _tok = mlx_lm.load(model.huggingface_id)
                # If we got here, the model actually fits. Run a tiny
                # generation to confirm forward pass works.
                _ = mlx_lm.generate(
                    _model, _tok, prompt=prompt, max_tokens=1,
                    verbose=False,
                )
                status = "ok"
                error = None
        except MemoryError as mem_exc:
            error = FRONTIER_ERROR_PREFIX["oom"] + f" ({mem_exc})"
        except Exception as exc:
            error = (
                FRONTIER_ERROR_PREFIX["load_failed"]
                + f"{type(exc).__name__}: {exc}"
            )

    total_ms = (time.time() - t0) * 1000.0

    return BenchRun(
        ts=_now(),
        git_sha=gs.sha,
        git_short_sha=gs.short_sha,
        git_branch=gs.branch,
        git_dirty=gs.is_dirty,
        model=getattr(model, "huggingface_id", str(model)),
        prompt=prompt,
        prompt_chars=len(prompt),
        max_tokens=max_tokens,
        tokens_out=0,
        total_latency_ms=round(total_ms, 2),
        ttft_ms=None,
        decode_tok_per_sec=None,
        bytes_read=None,
        peak_rss_mb=_peak_rss_mb(),
        knob_values={},
        variant_label=f"frontier:{getattr(model, 'name', 'unknown')}",
        status=status,
        error=error,
    )
