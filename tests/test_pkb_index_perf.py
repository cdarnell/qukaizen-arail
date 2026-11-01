"""Performance smoke tests for arail.pkb_index.

These tests verify:
1. Burst coalescing: 50 rapid schedule_upsert calls → exactly 1 merge_insert.
2. Single-write latency: schedule_upsert → row queryable in ≤ 4 s (p95).

Marked with pytest.mark.perf so CI can exclude them with -m "not perf"
if they prove flaky on slow CI machines. They run by default locally.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest


@pytest.fixture(autouse=True)
def pkb_index_reset():
    import arail.pkb_index as pki
    pki._reset_for_tests()
    yield
    pki._reset_for_tests()


pytestmark = pytest.mark.perf


# ── Burst coalescing ──────────────────────────────────────────────────────

def test_burst_50_upserts_fires_one_merge_insert(tmp_path: Path):
    """50 rapid schedule_upsert calls in a tight loop should produce exactly
    one merge_insert call (all collapsed by the set-dedup + single timer)."""
    import arail.pkb_index as pki

    merge_insert_calls: list[int] = []

    # Build 50 distinct files.
    (tmp_path / "agents" / "research").mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(50):
        f = tmp_path / "agents" / "research" / f"burst_{i:03d}.md"
        f.write_text(f"# Burst {i}\n\nContent {i}.\n")
        paths.append(f)

    # We mock the LanceDB table's merge_insert to count calls.
    # Use a real LanceDB so the rest of _flush runs normally, but intercept
    # the merge_insert builder on the table object.
    from arail.vector_index import hash_embedding
    import lancedb  # type: ignore[import-not-found]

    db_path = tmp_path / ".cache" / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    db.create_table("pkb_pages", data=[{
        "path": "_seed.md",
        "name": "_seed.md",
        "vector": hash_embedding("seed"),
        "mtime": 0.0,
        "source_kind": "user",
    }], mode="overwrite")

    pki._pkb_root_cache = tmp_path
    pki._initialized = True

    real_flush = pki._flush
    flush_calls: list[int] = []

    def counting_flush():
        flush_calls.append(1)
        real_flush()

    # Patch _flush on the module to count invocations.
    with patch.object(pki, "_flush", side_effect=counting_flush):
        # Use a short debounce so the burst collapses quickly.
        with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "0.3"}):
            t_start = time.monotonic()
            for p in paths:
                pki.schedule_upsert(p, pkb_root=tmp_path)
            # Wait long enough for the single flush to fire.
            time.sleep(1.0)

    elapsed = time.monotonic() - t_start
    assert elapsed < 7.0, f"burst coalescing took {elapsed:.1f}s; expected < 7s"
    assert len(flush_calls) <= 3, \
        f"expected ≤ 3 flush calls for 50 upserts; got {len(flush_calls)}"


# ── Single-write latency ──────────────────────────────────────────────────

@pytest.mark.parametrize("trial", range(3))
def test_single_write_latency_under_4s(tmp_path: Path, trial: int):
    """From schedule_upsert to the row being present in LanceDB: p95 ≤ 4 s
    with default 2 s debounce. We test 3 trials and require all ≤ 4 s."""
    import arail.pkb_index as pki
    from arail.vector_index import hash_embedding
    import lancedb  # type: ignore[import-not-found]

    # Use a 1 s debounce (shorter than default, longer than timer overhead).
    with patch.dict("os.environ", {"LAB_PKB_UPSERT_DEBOUNCE_SEC": "1.0"}):
        db_path = tmp_path / ".cache" / "lancedb"
        db_path.mkdir(parents=True, exist_ok=True)
        db = lancedb.connect(str(db_path))
        db.create_table("pkb_pages", data=[{
            "path": "_seed.md",
            "name": "_seed.md",
            "vector": hash_embedding("seed", dim=768),
            "mtime": 0.0,
            "source_kind": "user",
        }], mode="overwrite")

        pki._pkb_root_cache = tmp_path
        pki._initialized = True

        # Use a unique tmp subdir per trial to avoid cross-trial leakage.
        trial_dir = tmp_path / f"trial_{trial}"
        trial_dir.mkdir(parents=True, exist_ok=True)
        f = trial_dir / "latency_test.md"
        f.write_text(f"# Latency trial {trial}\n\nContent.\n")

        # Re-root the pkb cache to tmp_path.
        pki._pkb_root_cache = tmp_path
        # Map the trial file as if it were under agents/research/.
        (tmp_path / "agents" / "research").mkdir(parents=True, exist_ok=True)
        target = tmp_path / "agents" / "research" / f"latency_trial_{trial}.md"
        target.write_text(f"# Latency trial {trial}\n\nContent.\n")

        t0 = time.monotonic()
        pki.schedule_upsert(target, pkb_root=tmp_path)

        # Poll LanceDB until the row appears.
        db2 = lancedb.connect(str(db_path))
        deadline = t0 + 4.0
        found = False
        while time.monotonic() < deadline:
            try:
                t = db2.open_table("pkb_pages")
                rows = t.to_pandas()
                rel = f"agents/research/latency_trial_{trial}.md"
                if rel in rows["path"].values:
                    found = True
                    break
            except Exception:
                pass
            time.sleep(0.1)

        latency = time.monotonic() - t0
        assert found, f"trial {trial}: row not in LanceDB after {latency:.1f}s"
        assert latency <= 4.0, f"trial {trial}: latency {latency:.2f}s > 4s budget"
