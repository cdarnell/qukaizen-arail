"""Buddy residency sampler + violation classifier (ARCHITECTURE.md §4.6).

Samples every 10s from the parent orchestrator during every phase: portal
RSS, Ollama /api/ps entries, and system available memory. The classifier
runs over the collected samples once a phase (or the whole build) finishes.
A violation never aborts the build — it's recorded, printed prominently,
and caps the certification decision at COMPATIBLE (decision_rule/v1,
composite.py, commit 13).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ResiditySample:
    ts: float
    portal_rss_bytes: Optional[int]
    ollama_models: List[Dict[str, Any]]  # [{name, size_vram, expires_at}]
    available_bytes: Optional[int]


@dataclass(frozen=True)
class ClassifyResult:
    status: str  # "ok" | "violated" | "unmeasured"
    max_drift_pct: float
    events: List[Dict[str, Any]]


def sample_once(*, portal_rss_bytes: Optional[int] = None,
                ollama_models: Optional[List[Dict[str, Any]]] = None,
                available_bytes: Optional[int] = None) -> ResiditySample:
    """One sample. Real RSS/Ollama collection is the orchestrator's job
    (phases.py, commit 18) — this function takes already-collected values so
    it (and the classifier below) is independently unit-testable without a
    real portal process or Ollama daemon running."""
    return ResiditySample(
        ts=time.time(),
        portal_rss_bytes=portal_rss_bytes,
        ollama_models=ollama_models or [],
        available_bytes=available_bytes,
    )


def write_residency_jsonl(path: Path, samples: List[ResiditySample]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps({
                "ts": s.ts, "portal_rss_bytes": s.portal_rss_bytes,
                "ollama_models": s.ollama_models, "available_bytes": s.available_bytes,
            }) + "\n")


def classify(samples: List[ResiditySample]) -> ClassifyResult:
    if not samples:
        return ClassifyResult("unmeasured", 0.0, [])

    portal_samples = [s for s in samples if s.portal_rss_bytes is not None]
    if not portal_samples:
        # Buddy wasn't running (no portal RSS ever observed) -> unmeasured,
        # not a violation.
        return ClassifyResult("unmeasured", 0.0, [])

    baseline = portal_samples[0].portal_rss_bytes
    events: List[Dict[str, Any]] = []
    max_drift_pct = 0.0
    violated = False

    for s in portal_samples[1:]:
        if baseline and baseline > 0:
            drift_pct = (baseline - s.portal_rss_bytes) / baseline * 100.0
            max_drift_pct = max(max_drift_pct, drift_pct)
            if drift_pct > 5.0:
                violated = True
                events.append({
                    "ts": s.ts, "kind": "portal_rss_drop",
                    "drift_pct": round(drift_pct, 2),
                })

    # Track Ollama models across samples: a model present at time T and
    # absent at time T+1, before its recorded expires_at, is a violation.
    # If it disappears after expires_at, that's a normal TTL lapse.
    last_seen: Dict[str, Dict[str, Any]] = {}
    for s in samples:
        current_names = set()
        for m in s.ollama_models:
            name = m.get("name")
            if not name:
                continue
            current_names.add(name)
            last_seen[name] = {"expires_at": m.get("expires_at"), "last_ts": s.ts}
        vanished = set(last_seen) - current_names
        for name in list(vanished):
            info = last_seen.pop(name, None)
            if info is None:
                continue
            expires_at = info.get("expires_at")
            if expires_at is not None and s.ts >= expires_at:
                events.append({"ts": s.ts, "kind": "ttl_expired", "model": name})
            else:
                violated = True
                events.append({"ts": s.ts, "kind": "model_evicted", "model": name})

    status = "violated" if violated else "ok"
    return ClassifyResult(status, round(max_drift_pct, 2), events)
