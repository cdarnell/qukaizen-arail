"""Paired statistics for the A/B report (ARCHITECTURE section 5.6)."""
from __future__ import annotations

import random
from typing import Dict, List, Optional, Sequence

RESAMPLES = 10_000
SEED = 20261118


def paired_delta(a: Dict[str, int], b: Dict[str, int], *, level: float = 0.95,
                 resamples: int = RESAMPLES, seed: int = SEED) -> Dict[str, object]:
    """Delta = mean_i(pass_b(i) - pass_a(i)) in percentage points over the items both arms have,
    with a paired percentile bootstrap CI. Deterministic for a given seed."""
    ids = sorted(set(a) & set(b))
    n = len(ids)
    if n == 0:
        return {"n": 0, "delta_pp": None, "ci": None, "ci_level": level, "discordant": 0, "zero_variance": False}
    zero_var = len({int(a[i]) for i in ids} | {int(b[i]) for i in ids}) == 1
    diffs = [int(b[i]) - int(a[i]) for i in ids]
    delta = 100.0 * sum(diffs) / n
    rng = random.Random(seed)
    means = sorted(sum(rng.choices(diffs, k=n)) for _ in range(resamples))
    lo_q = (1 - level) / 2
    lo = means[int(lo_q * resamples)]
    hi = means[min(resamples - 1, int((1 - lo_q) * resamples))]
    return {"n": n, "delta_pp": delta, "ci": [100.0 * lo / n, 100.0 * hi / n], "ci_level": level,
            "discordant": sum(1 for d in diffs if d),
            "zero_variance": zero_var}


def inject_gap(flags: Dict[str, int], gap_pp: float, seed: int) -> Optional[Dict[str, int]]:
    """A copy of *flags* with ``round(gap_pp% of n)`` seeded passes turned into failures, or None
    when the arm has fewer passes than that."""
    ids = sorted(flags)
    k = round(len(ids) * gap_pp / 100.0)
    passes = [i for i in ids if flags[i]]
    if len(passes) < k:
        return None
    flip = set(random.Random(seed).sample(passes, k))
    return {i: (0 if i in flip else flags[i]) for i in ids}


def synthetic_baseline(ids: Sequence[str], seed: int) -> Dict[str, int]:
    rng = random.Random(seed)
    return {i: int(rng.random() < 0.5) for i in sorted(ids)}


def percentile(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    v = sorted(values)
    return v[min(len(v) - 1, int(q * len(v)))]


def wilson(k: int, n: int, z: float = 1.96) -> Optional[List[float]]:
    """Wilson score interval for k successes in n, as fractions."""
    if n <= 0:
        return None
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return [max(0.0, centre - half), min(1.0, centre + half)]
