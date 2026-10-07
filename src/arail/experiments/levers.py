"""Where a hypothesis could actually be tested, if not here.

The Researcher refuses hypotheses the on-device engine has no lever for
(mini_experiments.classify_hypothesis). That refusal is honest but, on
its own, useless: an operator whose whole research interest is inference
speed gets a page of "not tested" and no idea what to do next.

Often there IS a lever — just on the other loop. `/tuning` sweeps a
whitelisted knob space with a real git-branch-per-variant ledger, and
several of the things the Researcher refuses map straight onto knobs
that exist there.

Two rules, both load-bearing:

  1. A knob is only ever named after it has been confirmed present in
     the live tuning schema. Pointing someone at a knob that does not
     exist is worse than saying nothing.
  2. When nothing maps, say so plainly. "Speculative decoding" has no
     knob in either config, and the useful answer is that it needs work
     in AeroLLM itself — not a hunt through a settings page.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Tuple

# Concept in a hypothesis -> knobs that would vary it. Filtered against
# the real schema before anything is shown, so a stale entry here is
# inert rather than misleading.
_LEVER_HINTS: Dict[str, Tuple[str, ...]] = {
    "prefetch": ("prefetch_enabled", "prefetch_lookahead"),
    "lookahead": ("prefetch_enabled", "prefetch_lookahead"),
    "kv cache": ("prompt_cache_enabled", "max_kv_size", "kv_bits"),
    "kv-cache": ("prompt_cache_enabled", "max_kv_size", "kv_bits"),
    "key-value cache": ("prompt_cache_enabled", "max_kv_size", "kv_bits"),
    "mixed-precision": ("aerollm_compression", "kv_bits", "model_quant_variant"),
    "mixed precision": ("aerollm_compression", "kv_bits", "model_quant_variant"),
    "quantize the": ("aerollm_compression", "kv_bits", "model_quant_variant"),
    "requantize": ("aerollm_compression", "kv_bits", "model_quant_variant"),
    "expert cache": ("expert_cache_size_mb",),
    "per-layer": ("aerollm_compression", "kv_bits"),
    "per layer": ("aerollm_compression", "kv_bits"),
}

# Knobs that are loop machinery, not things under test.
_META_KNOBS = frozenset({"bench_runs_per_config", "improvement_threshold_pct"})

_CONFIGS: Tuple[Tuple[str, str], ...] = (
    ("aerollm", "config/tuning.yml"),
    ("mlx", "config/tuning-mlx.yml"),
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent


@lru_cache(maxsize=1)
def available_knobs() -> Dict[str, str]:
    """{knob_name: backend} for every knob the tuning loop really has.

    Cached: this is read once per process to annotate reports, and the
    schema does not change under a running lab. Any read failure yields
    an empty map, which degrades to naming no knobs at all.
    """
    found: Dict[str, str] = {}
    try:
        from arail.experiments.tuning import load_tuning
    except Exception:
        return found
    for backend, rel in _CONFIGS:
        try:
            cfg = load_tuning(_repo_root() / rel)
        except Exception:
            continue
        for name in getattr(cfg, "knobs", {}) or {}:
            if name not in _META_KNOBS:
                found.setdefault(name, backend)
    return found


def levers_for(hypothesis: str) -> List[Tuple[str, str]]:
    """[(knob, backend)] the tuning loop could vary for this hypothesis.

    Empty means no knob anywhere matches — which is a real answer, not a
    lookup failure.
    """
    h = (hypothesis or "").lower()
    have = available_knobs()
    hits: List[Tuple[str, str]] = []
    seen = set()
    for concept, knobs in _LEVER_HINTS.items():
        if concept not in h:
            continue
        for knob in knobs:
            backend = have.get(knob)
            if backend and knob not in seen:
                seen.add(knob)
                hits.append((knob, backend))
    return hits


def handoff_line(hypothesis: str) -> str:
    """One sentence telling the operator where this can be tested."""
    hits = levers_for(hypothesis)
    if not hits:
        return ("No knob in either tuning config varies this — testing it "
                "means changing QueueLLM itself, not a lab setting.")
    by_backend: Dict[str, List[str]] = {}
    for knob, backend in hits:
        by_backend.setdefault(backend, []).append(knob)
    parts = [f"`{'`, `'.join(sorted(knobs))}` ({backend})"
             for backend, knobs in sorted(by_backend.items())]
    return ("The Tuning loop can vary this: " + "; ".join(parts) +
            " — see /tuning, or ./arailctl start and open the Tuning tab.")
