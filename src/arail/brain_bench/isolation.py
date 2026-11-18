"""SC8: did the run touch the user's lab? Split in two so a stranger's write cannot be blamed on the harness.

SC8a (attribution, hard pass/fail): every write-capable path the child resolved is inside the run's temp
root, and a positive canary proves the child's writes landed there. SC8b (quiet lab): the real roots'
mtimes are unchanged and Ollama residency was restored; if they changed while SC8a passed, something
else wrote to the lab, so the result is inconclusive (``passed: None``) and the verdict stays withheld."""
from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

CANARY = ".brain-bench-canary"
TRACE_FILE = "agent_traces.jsonl"


def child_paths() -> Dict[str, str]:
    """Every write-capable path this process resolved (run inside the child)."""
    from arail import agent_trace, config, egress, model_defaults, redact
    from arail.registry import store
    paths = {
        "LAB_ROOT": config.LAB_ROOT, "DATA_DIR": config.DATA_DIR, "PKB_ROOT": config.PKB_ROOT,
        "WORLDS_DIR": config.WORLDS_DIR, "EXPERIMENTS_DIR": config.EXPERIMENTS_DIR,
        "model_defaults": model_defaults._path(), "registry": store.registry_file(),
        "agent_trace": agent_trace._trace_path(), "secrets": redact._secrets_path(),
        "egress_audit": Path(egress._lab_data()) / "egress.jsonl",
        "airgap_audit": Path(os.environ.get("ARAIL_AIRGAP_AUDIT_FILE", "airgap_audit.jsonl")),
    }
    return {k: str(Path(v).resolve()) for k, v in paths.items()}


def write_canary() -> None:
    from arail import config
    d = Path(config.DATA_DIR)
    d.mkdir(parents=True, exist_ok=True)
    (d / CANARY).write_text("brain-bench child wrote here\n")


def inside(path: str, root: Path) -> bool:
    p, r = Path(path).resolve(), Path(root).resolve()
    return p == r or r in p.parents


def sc8a(paths_start: Dict[str, str], paths_end: Dict[str, str], tmp: Path) -> Dict[str, Any]:
    outside = sorted(k for k, v in {**paths_start, **paths_end}.items() if not inside(v, tmp))
    data = paths_end.get("DATA_DIR") or paths_start.get("DATA_DIR") or ""
    canary = bool(data) and inside(data, tmp) and (Path(data) / CANARY).exists()
    trace = bool(data) and inside(data, tmp) and (Path(data) / TRACE_FILE).exists()
    ok = bool(paths_start) and not outside and paths_start == paths_end and canary and trace
    why = []
    if not paths_start:
        why.append("the child reported no paths")
    if outside:
        why.append("outside the temp root: " + ", ".join(outside))
    if paths_start != paths_end:
        why.append("a path moved during the run")
    if not canary:
        why.append("the canary file is not in the temp data dir")
    if not trace:
        why.append("agent_traces.jsonl is not in the temp data dir")
    return {"id": "SC8a", "passed": ok,
            "expected": "every write-capable path the child resolved is inside the temp root; canary and trace landed there",
            "observed": f"{len(paths_start)} paths checked, {len(outside)} outside; canary {canary}; trace {trace}",
            "detail": "; ".join(why)}


def group_changed(changed: Iterable[str], repo_root: Path, limit: int = 8) -> str:
    """'lab/data/lance/x.lance: 277 files' style summary instead of the first few basenames."""
    root = Path(repo_root).resolve()
    counts: Counter = Counter()
    for c in changed:
        p = Path(c)
        try:
            rel = p.resolve().relative_to(root)
        except ValueError:
            rel = Path(p.name)
        counts[str(rel.parent) if len(rel.parts) > 1 else str(rel)] += 1
    top = counts.most_common(limit)
    more = len(counts) - len(top)
    return "; ".join(f"{d}: {n} files" for d, n in top) + (f"; and {more} more directories" if more > 0 else "")


def sc8b(sc8a_passed: bool, changed: List[str], restored: bool, repo_root: Path) -> Dict[str, Any]:
    base = {"id": "SC8b", "expected": "the real lab roots and model_defaults.yaml unchanged; Ollama residency restored"}
    obs = f"{len(changed)} guarded paths changed; residency restored: {restored}"
    detail = group_changed(changed, repo_root) if changed else ""
    if not restored:
        return {**base, "passed": False, "observed": obs, "detail": detail}
    if not changed:
        return {**base, "passed": True, "observed": obs, "detail": ""}
    if not sc8a_passed:
        return {**base, "passed": False, "observed": obs + " (and SC8a failed: an isolation bug)", "detail": detail}
    return {**base, "passed": None, "observed": "inconclusive: another process wrote to the lab during the run",
            "detail": detail}
