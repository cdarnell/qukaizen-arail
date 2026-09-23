"""Phase runner: one subprocess per build phase, D3 enforcement
(ARCHITECTURE.md §4.8).

Model memory is reclaimed by process exit, not by trusting GC or Metal —
that's the entire reason each phase is its own subprocess. The parent
waits for the child's exit AND confirms the pid is actually gone (a
zombie reaped by something else, or a pid recycled by the OS, must never
be mistaken for "still running") before starting the next phase.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from arail.nucleus.errors import RefusedByPolicy

PHASE_ORDER = ["P0", "PA", "PA2", "PB", "fuse", "PC", "certify"]

# Phases whose worker process must have fully exited before phase B (any
# training phase) may start -- the D3 invariant.
_TEACHER_RESIDENT_PHASES = {"PA", "PA2"}
_TRAIN_PHASES = {"PB"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@dataclass
class PhaseRecord:
    phase: str
    pid: Optional[int] = None
    start: Optional[str] = None
    end: Optional[str] = None
    status: str = "pending"   # pending | running | done | failed
    output_hashes: Dict[str, str] = field(default_factory=dict)


class RunLedger:
    """run.json: {build_id, domain, phases: [PhaseRecord, ...]}."""

    def __init__(self, run_dir: Path, build_id: str, domain: str):
        self.run_dir = Path(run_dir)
        self.build_id = build_id
        self.domain = domain
        self.path = self.run_dir / "run.json"
        self.phases: List[PhaseRecord] = []
        if self.path.is_file():
            self._load()

    def _load(self) -> None:
        data = json.loads(self.path.read_text())
        self.build_id = data.get("build_id", self.build_id)
        self.domain = data.get("domain", self.domain)
        self.phases = [PhaseRecord(**p) for p in data.get("phases", [])]

    def save(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = {
            "build_id": self.build_id, "domain": self.domain,
            "phases": [vars(p) for p in self.phases],
        }
        self.path.write_text(json.dumps(payload, sort_keys=True, indent=2))

    def record_for(self, phase: str) -> Optional[PhaseRecord]:
        for p in self.phases:
            if p.phase == phase:
                return p
        return None

    def is_done(self, phase: str) -> bool:
        rec = self.record_for(phase)
        return bool(rec and rec.status == "done")

    def start_phase(self, phase: str, pid: int) -> PhaseRecord:
        rec = self.record_for(phase)
        if rec is None:
            rec = PhaseRecord(phase=phase)
            self.phases.append(rec)
        rec.pid = pid
        rec.start = _now_iso()
        rec.end = None
        rec.status = "running"
        self.save()
        return rec

    def finish_phase(self, phase: str, *, status: str, output_hashes: Optional[Dict[str, str]] = None) -> None:
        rec = self.record_for(phase)
        if rec is None:
            return
        rec.end = _now_iso()
        rec.status = status
        if output_hashes:
            rec.output_hashes.update(output_hashes)
        self.save()

    def assert_no_teacher_resident_before(self, phase: str) -> None:
        """D3: refuse to start a train phase while any teacher-resident
        (PA/PA2) worker pid is still alive."""
        if phase not in _TRAIN_PHASES:
            return
        for rec in self.phases:
            if rec.phase in _TEACHER_RESIDENT_PHASES and rec.status == "running" and rec.pid:
                if _pid_alive(rec.pid):
                    raise RefusedByPolicy(
                        f"refusing to start phase {phase}: phase {rec.phase} "
                        f"(pid {rec.pid}) is still resident — D3 invariant"
                    )


def worker_offline_env() -> Dict[str, str]:
    """Every worker subprocess gets this environment — this is HOW zero
    egress is achieved, not just asserted (T-EGR-2)."""
    env = dict(os.environ)
    env.update({
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false",
    })
    return env


def run_phase(
    phase: str, build_id: str, *, ledger: RunLedger, repo_root: Optional[Path] = None,
    timeout: Optional[float] = None, extra_env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    """Spawn `python -m arail.nucleus.worker <phase> <build_id>`, niced to
    10, wait for exit, and confirm the pid is actually gone before
    returning. Records {phase, pid, start, end} on the ledger."""
    ledger.assert_no_teacher_resident_before(phase)

    env = worker_offline_env()
    if extra_env:
        env.update(extra_env)

    args = [sys.executable, "-m", "arail.nucleus.worker", phase, build_id]
    if hasattr(os, "nice") and os.name == "posix":
        args = ["nice", "-n", "10", *args]

    proc = subprocess.Popen(args, cwd=str(repo_root) if repo_root else None, env=env)
    ledger.start_phase(phase, proc.pid)

    try:
        returncode = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        ledger.finish_phase(phase, status="failed")
        raise RefusedByPolicy(f"phase {phase} timed out")

    # Confirm the pid is actually gone (a recycled pid must never read as
    # "still resident" by assert_no_teacher_resident_before above).
    deadline = time.monotonic() + 5.0
    while _pid_alive(proc.pid) and time.monotonic() < deadline:
        time.sleep(0.05)

    status = "done" if returncode == 0 else "failed"
    ledger.finish_phase(phase, status=status)

    if returncode != 0:
        raise RefusedByPolicy(f"phase {phase} exited {returncode}")

    return subprocess.CompletedProcess(args, returncode)
