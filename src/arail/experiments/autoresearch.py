"""arail.experiments.autoresearch — The autonomous tuning loop.

Contract (two backends, identical shape):

    1. Snapshot the current git HEAD as the baseline, and remember
       which branch the user was on so we can put them back.
    2. Run the benchmark `runs_per_config` times to get baseline
       metrics (tokens/sec, TTFT). Persist to the backend's tuning
       config (tuning.yml or tuning-mlx.yml) and commit that on an
       autoresearch/baseline-<ts> branch of our own — never on the
       user's branch.
    3. Enumerate candidate variants from the catalog below. A
       variant is a {knob: value} dict that passes the tuning-
       schema validator.
    4. For each variant, in its own autoresearch/<id> branch:
         a. Apply the knob change to the backend's tuning config.
         b. Run the benchmark `runs_per_config` times.
         c. Compute median tokens/sec across runs.
         d. If it beats baseline by >= improvement_threshold_pct,
            commit to the branch and tag it "win"; otherwise
            leave the branch for inspection and record it as "loss".
    5. Report the leaderboard.

Backends:

    - "aerollm" — CUDA track, multi-threaded prefetched layer streaming
                  via AeroLLM. Uses config/tuning.yml +
                  lab/data/aerollm-bench.jsonl.
    - "mlx"     — AeroLLM MLX track, Apple Silicon unified memory via
                  mlx_lm. Uses config/tuning-mlx.yml +
                  lab/data/mlx-bench.jsonl.

The loop body is identical; only the config path, bench runner,
candidate list, and committed file pair differ.

Safety rails (non-negotiable, same for both backends):

    - Working tree must be clean at loop start. We abort if not.
    - ARAIL_AUTORESEARCH_ENABLED env var must be set.
    - Only the backend's two whitelisted files are ever written
      (enforced in git_ops.commit_experiment).
    - Never touches main, or any other branch outside the
      autoresearch/ namespace. Both the baseline capture and every
      variant live on autoresearch/<id> branches, and the loop checks
      the user's original branch back out when it finishes — including
      after a win or a crash.
    - Never pushes. No network ops from this module.

The "autonomous agent" language is deliberate: the loop does not
invoke an LLM to generate code. It iterates over a hand-curated
set of variants (CANDIDATES / MLX_CANDIDATES below) that are
known-safe transforms of the tuning schema. This keeps the loop
deterministic and reviewable.
"""

from __future__ import annotations

import os
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from arail.activity import activity_log
from arail.experiments.bench import (
    BenchRun, append_run, run_bench
)
from arail.experiments.git_ops import (
    AUTORESEARCH_BRANCH_PREFIX,
    GitSafetyError,
    abort_experiment,
    assert_clean_tree,
    commit_experiment,
    create_experiment_branch,
    git_state,
)
from arail.experiments.tuning import (
    TuningConfig,
    load_tuning,
    save_tuning,
)


Candidate = tuple[str, Dict[str, Any]]


# ── Candidate variants (AeroLLM / CUDA track) ──────────────────────
# Each candidate is (label, {knob: value}). We keep this list short
# and hand-curated. New entries require a human PR to tuning.yml's
# schema (e.g. adding a choice to aerollm_package) AND this file.
# Two-file discipline makes it hard for an agent to expand the search
# space behind the maintainer's back.
CANDIDATES: List[Candidate] = [
    (
        "prefetch-off (baseline comparison)",
        {"prefetch_enabled": False, "prefetch_lookahead": 0},
    ),
    (
        "prefetch-1 layer ahead",
        {"prefetch_enabled": True, "prefetch_lookahead": 1},
    ),
    (
        "compression-4bit (default)",
        {"aerollm_compression": "4bit"},
    ),
    (
        "compression-8bit (quality over speed)",
        {"aerollm_compression": "8bit"},
    ),
    (
        "context-256 (smaller KV cache)",
        {"aerollm_max_length": 256},
    ),
    (
        "context-1024 (standard chat window)",
        {"aerollm_max_length": 1024},
    ),
    (
        "expert-cache-2GB (MoE warm set)",
        {"expert_cache_size_mb": 2048},
    ),
    (
        "expert-cache-8GB (aggressive warm set)",
        {"expert_cache_size_mb": 8192},
    ),
]


# ── Candidate variants (MLX / Apple track — AeroLLM MLX) ───────────
# Knobs here exercise the three levers that actually move on Apple:
#   - KV-cache quantization (kv_bits + quantized_kv_start)
#   - KV size cap (max_kv_size)
#   - Prompt chunking (prefill_step_size)
# Prompt cache + model quant variant are listed too for completeness.
MLX_CANDIDATES: List[Candidate] = [
    (
        "kv-fp16 (baseline comparison)",
        {"kv_bits": "fp16", "quantized_kv_start": 0},
    ),
    (
        "kv-8bit from token 0",
        {"kv_bits": "8bit", "quantized_kv_start": 0},
    ),
    (
        "kv-8bit from token 2048 (quality-preserving tail)",
        {"kv_bits": "8bit", "quantized_kv_start": 2048},
    ),
    (
        "kv-4bit from token 0 (aggressive)",
        {"kv_bits": "4bit", "quantized_kv_start": 0},
    ),
    (
        "max-kv-2048 (tight context cap)",
        {"max_kv_size": 2048},
    ),
    (
        "max-kv-8192 (roomy context)",
        {"max_kv_size": 8192},
    ),
    (
        "prefill-256 (small chunks)",
        {"prefill_step_size": 256},
    ),
    (
        "prefill-1024 (large chunks)",
        {"prefill_step_size": 1024},
    ),
]


# ── Loop state (per-backend; the /api endpoint polls it) ────────────

@dataclass
class VariantResult:
    label: str
    knob_delta: Dict[str, Any]
    branch: str
    git_sha: Optional[str]
    median_tok_per_sec: Optional[float]
    median_ttft_ms: Optional[float]
    delta_pct: Optional[float]        # vs baseline
    outcome: str                      # "win" | "loss" | "error"
    runs: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class LoopState:
    backend: str = "aerollm"          # "aerollm" | "mlx"
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    phase: str = "idle"               # idle | baseline | variant | done | error
    current_variant: Optional[str] = None
    baseline_tok_per_sec: Optional[float] = None
    baseline_sha: Optional[str] = None
    # The autoresearch/baseline-<ts> branch this pass captured its
    # baseline on. The loop commits nothing outside the autoresearch/
    # namespace, so this is where the baseline record lives — never on
    # whatever branch the user happened to be sitting on.
    baseline_branch: Optional[str] = None
    variants: List[VariantResult] = field(default_factory=list)
    error: Optional[str] = None
    # Continuous ("don't stop, won't stop") mode. When True, the outer
    # supervisor keeps re-running the full sweep after each pass until
    # `stop_requested` flips. Pass counter surfaces in the UI so it's
    # clear the loop is still alive and making progress.
    continuous: bool = False
    stop_requested: bool = False
    pass_number: int = 0
    # Schedule status — computed fresh on each supervisor tick so the
    # /tuning page can show "running" vs "waiting for 22:00" without a
    # second API call. Empty dict means "schedule not yet evaluated"
    # (only happens during the brief first-tick window).
    schedule: Dict[str, Any] = field(default_factory=dict)
    # Set when the loop's candidates came from a research recipe at
    # ``lab/pkb/research/program.md`` instead of the hardcoded
    # CANDIDATES list. Surfaces in the UI as "Driven by: program.md".
    program_path: Optional[str] = None
    program_candidate_count: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "backend": self.backend,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "phase": self.phase,
            "current_variant": self.current_variant,
            "baseline_tok_per_sec": self.baseline_tok_per_sec,
            "baseline_sha": self.baseline_sha,
            "baseline_branch": self.baseline_branch,
            "variants": [
                {
                    "label": v.label,
                    "knob_delta": v.knob_delta,
                    "branch": v.branch,
                    "git_sha": v.git_sha,
                    "median_tok_per_sec": v.median_tok_per_sec,
                    "median_ttft_ms": v.median_ttft_ms,
                    "delta_pct": v.delta_pct,
                    "outcome": v.outcome,
                    "error": v.error,
                }
                for v in self.variants
            ],
            "error": self.error,
            "continuous": self.continuous,
            "stop_requested": self.stop_requested,
            "pass_number": self.pass_number,
            "schedule": dict(self.schedule) if self.schedule else {},
            "program_path": self.program_path,
            "program_candidate_count": self.program_candidate_count,
        }


# One LoopState per backend. Each backend has its own supervisor; the
# UI picks which to show via ?backend=... query param. If a caller
# passes an unknown backend we raise — better than silently creating
# a third track.
_STATES: Dict[str, LoopState] = {
    "aerollm": LoopState(backend="aerollm"),
    "mlx": LoopState(backend="mlx"),
}


def current_state(backend: str = "aerollm") -> LoopState:
    _require_known_backend(backend)
    return _STATES[backend]


def request_stop(backend: str = "aerollm") -> None:
    """Signal the continuous supervisor to exit after the current pass.
    A single pass can still have variants in flight; those finish, then
    the supervisor returns. Never interrupts a variant mid-bench."""
    _require_known_backend(backend)
    _STATES[backend].stop_requested = True


# ── Schedule (user-controllable gate for the continuous supervisor) ─
#
# Persisted to lab/data/autoresearch-schedule.json so the user's choice
# survives portal restarts. Three modes:
#
#   - "anytime" (default) — run continuously, no time gate
#   - "window"             — only run during [window_start .. window_end]
#                            in local time. Supports overnight windows
#                            (e.g. 22:00-06:00) by handling the wrap.
#   - "paused"             — don't run at all. Supervisor polls the
#                            schedule periodically so the user can
#                            resume without a restart.
#
# We deliberately keep the schema tiny — more modes can land later, but
# the UI should stay "Anytime / Off-hours only / Paused" until the user
# asks for finer control (per-day schedules, cron strings, etc).

_DEFAULT_SCHEDULE: Dict[str, Any] = {
    "mode": "anytime",
    "window_start": "22:00",
    "window_end": "06:00",
}


def _schedule_path() -> Path:
    from arail.config import DATA_DIR
    return DATA_DIR / "autoresearch-schedule.json"


def load_schedule() -> Dict[str, Any]:
    """Read the persisted schedule. Returns the default on missing file
    or malformed content — never raises, so supervisor tick logic stays
    simple."""
    path = _schedule_path()
    if not path.exists():
        return dict(_DEFAULT_SCHEDULE)
    try:
        import json
        data = json.loads(path.read_text())
    except Exception:
        return dict(_DEFAULT_SCHEDULE)
    merged = dict(_DEFAULT_SCHEDULE)
    if isinstance(data, dict):
        for k in ("mode", "window_start", "window_end"):
            if k in data and isinstance(data[k], str):
                merged[k] = data[k]
    if merged["mode"] not in ("anytime", "window", "paused"):
        merged["mode"] = "anytime"
    return merged


def save_schedule(sched: Dict[str, Any]) -> Dict[str, Any]:
    """Write the schedule to disk after normalizing. Returns the
    written value (same shape as load_schedule). Invalid mode values
    are coerced to the default instead of raising so the API surface
    is resilient to UI bugs."""
    import json
    merged = dict(_DEFAULT_SCHEDULE)
    if isinstance(sched, dict):
        mode = str(sched.get("mode", "anytime")).strip().lower()
        if mode not in ("anytime", "window", "paused"):
            mode = "anytime"
        merged["mode"] = mode
        for k in ("window_start", "window_end"):
            v = sched.get(k)
            if isinstance(v, str) and _parse_hhmm(v) is not None:
                merged[k] = v
    path = _schedule_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2))
    return merged


def _parse_hhmm(s: str) -> Optional[tuple[int, int]]:
    """Parse "HH:MM" → (hour, minute) or None on malformed input."""
    try:
        h, m = s.split(":", 1)
        hh, mm = int(h), int(m)
        if 0 <= hh < 24 and 0 <= mm < 60:
            return hh, mm
    except (ValueError, AttributeError):
        pass
    return None


def schedule_status(sched: Optional[Dict[str, Any]] = None,
                    now: Optional[datetime] = None) -> Dict[str, Any]:
    """Compute live status for the current schedule.

    Returns:
        {
          "mode": <str>,
          "window_start": "HH:MM",
          "window_end": "HH:MM",
          "allowed_now": <bool>,                 # supervisor may run now
          "seconds_until_open": <int>|None,      # None if already open or paused
          "next_open_at": "HH:MM"|None,          # formatted for the UI
        }

    Semantics:
      - "anytime" → allowed_now=True, seconds_until_open=None
      - "paused"  → allowed_now=False, seconds_until_open=None
                    (no automatic re-open; user must flip the toggle)
      - "window"  → compute the next open edge. Wrap handled so
                    22:00-06:00 works overnight.
    """
    sched = sched if sched is not None else load_schedule()
    now = now or datetime.now()
    out: Dict[str, Any] = {
        "mode": sched["mode"],
        "window_start": sched["window_start"],
        "window_end": sched["window_end"],
        "allowed_now": True,
        "seconds_until_open": None,
        "next_open_at": None,
    }
    mode = sched["mode"]
    if mode == "anytime":
        return out
    if mode == "paused":
        out["allowed_now"] = False
        return out
    # window
    start = _parse_hhmm(sched["window_start"]) or (22, 0)
    end = _parse_hhmm(sched["window_end"]) or (6, 0)
    cur = (now.hour, now.minute)
    def _m(t): return t[0] * 60 + t[1]
    s_m, e_m, c_m = _m(start), _m(end), _m(cur)
    if s_m == e_m:
        # Degenerate — zero-width window. Treat as always disallowed.
        out["allowed_now"] = False
    elif s_m < e_m:
        # Same-day window
        out["allowed_now"] = s_m <= c_m < e_m
    else:
        # Overnight window (e.g. 22:00-06:00)
        out["allowed_now"] = c_m >= s_m or c_m < e_m
    if not out["allowed_now"]:
        # Compute seconds until the next start edge.
        today_start = now.replace(
            hour=start[0], minute=start[1], second=0, microsecond=0,
        )
        if today_start <= now:
            # Window already started earlier today (only possible for
            # same-day windows where we've passed the end); next open
            # is tomorrow at the same HH:MM.
            today_start = today_start + timedelta(days=1)
        delta = int((today_start - now).total_seconds())
        out["seconds_until_open"] = max(delta, 1)
        out["next_open_at"] = today_start.strftime("%Y-%m-%d %H:%M")
    return out


# ── Backend dispatch ────────────────────────────────────────────────

def _require_known_backend(backend: str) -> None:
    if backend not in ("aerollm", "mlx"):
        raise ValueError(
            f"unknown backend: {backend!r} (expected 'aerollm' or 'mlx')"
        )


def _config_path(backend: str) -> Path:
    """Repo-relative path to the tuning config for this backend."""
    _require_known_backend(backend)
    here = Path(__file__).resolve()
    repo_root = here.parent.parent.parent.parent
    if backend == "mlx":
        return repo_root / "config" / "tuning-mlx.yml"
    return repo_root / "config" / "tuning.yml"


def _commit_files(backend: str) -> List[str]:
    """The pair of files whitelisted for commits from this backend's
    loop. These MUST be in git_ops.ALLOWED_WRITABLE_FILES or the
    commit will be refused."""
    _require_known_backend(backend)
    if backend == "mlx":
        return ["config/tuning-mlx.yml", "lab/data/mlx-bench.jsonl"]
    return ["config/tuning.yml", "lab/data/aerollm-bench.jsonl"]


class _SkipVariant(Exception):
    """A schema-valid variant the backend cannot actually run."""


def _unsupported_combination(cfg, delta: Dict[str, Any],
                             backend: str) -> Optional[str]:
    """Backend-specific runnability check on the POST-delta knob set."""
    if backend != "mlx":
        return None
    try:
        from arail.experiments.mlx_backend import unsupported_combination
    except Exception:
        return None
    knobs = {name: k.current for name, k in cfg.knobs.items()}
    knobs.update(delta)
    try:
        return unsupported_combination(knobs)
    except Exception:
        return None


def _default_candidates(backend: str) -> List[Candidate]:
    return MLX_CANDIDATES if backend == "mlx" else CANDIDATES


# ── Core helpers ────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _median(xs: List[Optional[float]]) -> Optional[float]:
    """Median of a list that may contain None (we filter). Returns
    None if no real numbers are present."""
    clean = [x for x in xs if x is not None]
    if not clean:
        return None
    return round(statistics.median(clean), 3)


def _run_n(
    cfg: TuningConfig,
    label: Optional[str],
    *,
    backend: str,
) -> List[BenchRun]:
    """Run the bench `bench_runs_per_config` times for this backend
    and persist each run to the backend's JSONL log."""
    runs: List[BenchRun] = []
    n = int(cfg.knobs["bench_runs_per_config"].current)
    if backend == "mlx":
        from arail.experiments.mlx_backend import (
            mlx_bench_file, run_mlx_bench,
        )
        for _ in range(max(n, 1)):
            run = run_mlx_bench(
                research_model_name=cfg.research_model.name,
                prompt=cfg.baseline_prompt,
                max_tokens=cfg.baseline_max_tokens,
                knob_values=cfg.knob_values(),
                variant_label=label,
            )
            append_run(run, path=mlx_bench_file())
            runs.append(run)
    else:
        for _ in range(max(n, 1)):
            run = run_bench(
                research_model_name=cfg.research_model.name,
                prompt=cfg.baseline_prompt,
                max_tokens=cfg.baseline_max_tokens,
                knob_values=cfg.knob_values(),
                variant_label=label,
            )
            append_run(run)  # defaults to aerollm-bench.jsonl
            runs.append(run)
    return runs


def _build_commit_message(
    *,
    backend: str,
    label: str,
    knob_delta: Dict[str, Any],
    baseline_tps: float,
    variant_tps: float,
    delta_pct: float,
    runs: List[BenchRun],
) -> tuple[str, str]:
    """Return (subject, body) for a winning variant."""
    subject = (
        f"tune({backend}): {label} — +{delta_pct:.1f}% tok/s vs baseline"
    )
    lines = []
    lines.append("Variant knob delta:")
    for k, v in knob_delta.items():
        lines.append(f"  - {k}: {v}")
    lines.append("")
    lines.append(
        f"Baseline tok/s: {baseline_tps:.3f}  (median of baseline runs)"
    )
    lines.append(
        f"Variant  tok/s: {variant_tps:.3f}   (median of {len(runs)} runs)"
    )
    lines.append(f"Delta:          +{delta_pct:.1f}%")
    lines.append("")
    lines.append("Per-run samples:")
    for r in runs:
        lines.append(
            f"  - ts={r.ts}  tok/s={r.decode_tok_per_sec}  "
            f"ttft={r.ttft_ms}ms  status={r.status}"
        )
    lines.append("")
    lines.append(
        "Produced by arail.experiments.autoresearch. "
        f"backend={backend}."
    )
    return subject, "\n".join(lines)


# ── Public entry point ──────────────────────────────────────────────

_DEFAULT_PROGRAM_PATH = Path("lab/pkb/research/program.md")


def _candidates_from_program(program_path: Path) -> List[Candidate]:
    """Try to pull candidates out of program.md's ``## Knobs`` block.

    Imported lazily so autoresearch.py doesn't pull arail.research at
    module-import time — keeps the import graph cheap for code paths
    that never touch the loop.
    """
    try:
        from arail.research.program_loader import parse_program
    except Exception:
        return []
    recipe = parse_program(program_path)
    if recipe is None:
        return []
    return list(recipe.knobs)


def run_autoresearch(
    *,
    backend: str = "aerollm",
    require_env_flag: bool = True,
    candidates: Optional[List[Candidate]] = None,
    progress: Optional[Callable[[LoopState], None]] = None,
    preserve_continuous: bool = False,
    program_path: Optional[Path] = None,
) -> LoopState:
    """Run one full pass of the autoresearch loop for the given
    backend. Caller is expected to invoke this on a background
    thread (e.g. asyncio.to_thread) — the /tuning page polls
    /api/tuning/autoresearch/status for progress.

    Aborts on any safety violation (dirty tree, missing env flag,
    schema violation). Always returns the backend's _STATES entry
    so the caller can inspect partial results even on failure.

    When ``preserve_continuous`` is True the continuous/stop/pass_number
    flags are carried over from the prior state. This is how
    ``run_autoresearch_forever`` signals that a fresh pass is starting
    without blowing away the supervisor's control bits.

    Candidate selection precedence:
      1. ``candidates`` arg (explicit override — tests use this).
      2. ``## Knobs`` block in ``program_path`` (or the default
         ``lab/pkb/research/program.md`` when no path is given AND the
         file exists). This is the goal-driven path: edit program.md,
         re-run, get a different sweep.
      3. Hardcoded ``CANDIDATES`` / ``MLX_CANDIDATES`` for the backend
         (today's behavior — preserved as the fallback).
    """
    _require_known_backend(backend)
    prev = _STATES[backend]
    _STATES[backend] = LoopState(
        backend=backend, started_at=_now(), phase="baseline",
    )
    state = _STATES[backend]
    if preserve_continuous:
        state.continuous = prev.continuous
        state.stop_requested = prev.stop_requested
        state.pass_number = prev.pass_number + 1
        # Carry the supervisor's last-known schedule snapshot across
        # passes so the UI doesn't blink back to "unknown" while the
        # loop is actively running.
        state.schedule = dict(prev.schedule) if prev.schedule else {}

    config_path = _config_path(backend)
    commit_files = _commit_files(backend)

    # Resolve candidates per the precedence above.
    if candidates is not None:
        effective_candidates = candidates
    else:
        chosen_program = program_path if program_path is not None else _DEFAULT_PROGRAM_PATH
        program_candidates = _candidates_from_program(chosen_program) if chosen_program.exists() else []
        if program_candidates:
            effective_candidates = program_candidates
            state.program_path = str(chosen_program)
            state.program_candidate_count = len(program_candidates)
        else:
            effective_candidates = _default_candidates(backend)

    # Set once we've switched off the user's branch; None means "we
    # never moved, so there is nothing to restore and nothing of ours
    # in the working tree". Load-bearing: the restore path discards
    # working-tree changes, so it must never run when the reason we
    # bailed was the user's own dirty tree.
    restore_to: Optional[str] = None

    try:
        if require_env_flag and not os.getenv("ARAIL_AUTORESEARCH_ENABLED"):
            raise GitSafetyError(
                "ARAIL_AUTORESEARCH_ENABLED is not set. This flag exists "
                "to prevent the loop from running by accident. Export it "
                "(e.g. in .env) when you genuinely want the loop to make "
                "commits."
            )

        assert_clean_tree()
        origin_state = git_state()
        state.baseline_sha = origin_state.short_sha
        # Captured BEFORE any branch switch. This is the branch the user
        # was sitting on when they started the loop, and the one we put
        # them back on when we're done. Nothing is ever committed here.
        origin_branch = origin_state.branch

        cfg = load_tuning(config_path)

        # ── Baseline phase ────────────────────────────────────────
        if progress:
            progress(state)
        baseline_runs = _run_n(cfg, label="baseline", backend=backend)
        baseline_tps = _median(
            [r.decode_tok_per_sec for r in baseline_runs]
        )
        if baseline_tps is None:
            raise RuntimeError(
                "Baseline produced no measurable tokens/sec. "
                "Refusing to run variants until baseline is valid."
            )
        state.baseline_tok_per_sec = baseline_tps

        # Persist baseline into the config and commit it on a branch of
        # our own. The baseline capture used to be committed on whatever
        # branch the user started from — which meant running the loop
        # from `main` put a commit on `main`, and `./arailctl update`'s
        # `git pull --ff-only` would later refuse with an error the user
        # couldn't trace back to "I ran an experiment". The loop now
        # writes only inside the `autoresearch/` namespace.
        baseline_branch = create_experiment_branch(
            f"baseline-{time.strftime('%Y%m%d-%H%M%S')}",
            base_branch=origin_branch,
        )
        restore_to = origin_branch
        state.baseline_branch = baseline_branch
        try:
            activity_log.emit(
                "autoresearch",
                f"Baseline branch created: {baseline_branch}",
                "info",
                {"event": "branch-update", "branch": baseline_branch,
                 "outcome": "baseline", "backend": backend},
            )
        except Exception:
            pass

        cfg.baseline_commit = origin_state.sha
        cfg.baseline_metrics = {
            "median_tok_per_sec": baseline_tps,
            "median_ttft_ms": _median(
                [r.ttft_ms for r in baseline_runs]
            ),
            "sample_count": len(baseline_runs),
            "ts": _now(),
        }
        save_tuning(cfg, config_path)
        try:
            commit_experiment(
                subject=(
                    f"bench({backend}): capture baseline "
                    f"{baseline_tps:.3f} tok/s for "
                    f"{cfg.research_model.name}"
                ),
                body=(
                    f"Autoresearch loop baseline capture ({backend}).\n"
                    f"Model: {cfg.research_model.name} "
                    f"({cfg.research_model.precision}, "
                    f"~{cfg.research_model.expected_disk_gb} GB on disk)\n"
                    f"Prompt: {cfg.baseline_prompt!r}\n"
                    f"Samples: {len(baseline_runs)}"
                ),
                files=commit_files,
            )
        except GitSafetyError:
            # Empty diff (e.g. baseline was already committed). Fine.
            pass

        # ── Variant phase ─────────────────────────────────────────
        threshold = float(
            cfg.knobs["improvement_threshold_pct"].current
        )
        # Variants branch from the baseline branch, not from the user's
        # branch, so each variant carries the baseline record it is
        # being compared against — and so a losing variant returns to
        # the baseline (where tuning.yml still holds baseline_metrics)
        # rather than to a user branch that never saw them.
        variant_base = baseline_branch

        for label, delta in effective_candidates:
            state.phase = "variant"
            state.current_variant = label
            if progress:
                progress(state)

            exp_id = time.strftime("%Y%m%d-%H%M%S") + "-" + _slug(label)
            branch = AUTORESEARCH_BRANCH_PREFIX + exp_id
            result = VariantResult(
                label=label,
                knob_delta=delta,
                branch=branch,
                git_sha=None,
                median_tok_per_sec=None,
                median_ttft_ms=None,
                delta_pct=None,
                outcome="error",
            )
            try:
                # Validate the delta against the schema BEFORE
                # creating a branch. Invalid proposal → skip cleanly.
                for k, v in delta.items():
                    ok, reason = cfg.set_knob(k, v)
                    if not ok:
                        raise ValueError(
                            f"invalid variant {label!r}: {k}={v!r} "
                            f"({reason})"
                        )

                # Schema-valid is not the same as runnable. Ask the
                # backend whether this combination is supported before
                # spending a branch and N model loads on it.
                unsupported = _unsupported_combination(cfg, delta, backend)
                if unsupported:
                    result.outcome = "skipped"
                    result.error = unsupported
                    try:
                        activity_log.emit(
                            "autoresearch",
                            f"Skipped {label}: {unsupported}",
                            "warn",
                            {"event": "variant-skipped", "label": label,
                             "backend": backend},
                        )
                    except Exception:
                        pass
                    raise _SkipVariant(unsupported)

                create_experiment_branch(exp_id, base_branch=variant_base)
                try:
                    activity_log.emit(
                        "autoresearch",
                        f"Branch created: {branch}",
                        "info",
                        {"event": "branch-update", "branch": branch,
                         "exp_id": exp_id, "label": label, "backend": backend},
                    )
                except Exception:
                    pass
                save_tuning(cfg, config_path)

                variant_runs = _run_n(cfg, label=label, backend=backend)
                v_tps = _median(
                    [r.decode_tok_per_sec for r in variant_runs]
                )
                v_ttft = _median(
                    [r.ttft_ms for r in variant_runs]
                )
                if v_tps is None:
                    result.outcome = "error"
                    result.error = "no measurable tok/s"
                    abort_experiment(variant_base)
                else:
                    delta_pct = ((v_tps - baseline_tps) / baseline_tps) * 100
                    result.median_tok_per_sec = v_tps
                    result.median_ttft_ms = v_ttft
                    result.delta_pct = round(delta_pct, 2)

                    if delta_pct >= threshold:
                        subject, body = _build_commit_message(
                            backend=backend,
                            label=label,
                            knob_delta=delta,
                            baseline_tps=baseline_tps,
                            variant_tps=v_tps,
                            delta_pct=delta_pct,
                            runs=variant_runs,
                        )
                        sha = commit_experiment(
                            subject=subject,
                            body=body,
                            files=commit_files,
                        )
                        result.git_sha = sha
                        result.outcome = "win"
                        try:
                            activity_log.emit(
                                "autoresearch",
                                f"Win: {label} +{round(delta_pct, 1)}% tok/s",
                                "success",
                                {"event": "branch-update", "branch": branch,
                                 "exp_id": exp_id, "outcome": "win",
                                 "delta_pct": round(delta_pct, 2),
                                 "backend": backend},
                            )
                        except Exception:
                            pass
                    else:
                        result.outcome = "loss"
                        try:
                            activity_log.emit(
                                "autoresearch",
                                f"Loss: {label} {round(delta_pct, 1)}% tok/s vs baseline",
                                "info",
                                {"event": "branch-update", "branch": branch,
                                 "exp_id": exp_id, "outcome": "loss",
                                 "delta_pct": round(delta_pct, 2),
                                 "backend": backend},
                            )
                        except Exception:
                            pass
                        abort_experiment(variant_base)

            except _SkipVariant:
                # Recorded as "skipped" above; no branch was created and
                # nothing was written, so there is nothing to abort.
                pass
            except Exception as exc:
                result.error = f"{type(exc).__name__}: {exc}"
                result.outcome = "error"
                try:
                    abort_experiment(variant_base)
                except Exception:
                    pass
            finally:
                # Reload cfg fresh for the next variant so one variant
                # can't poison the next.
                try:
                    cfg = load_tuning(config_path)
                except Exception:
                    pass
                state.variants.append(result)
                if progress:
                    progress(state)

        state.phase = "done"
        state.finished_at = _now()
        return state

    except Exception as exc:
        state.phase = "error"
        state.error = f"{type(exc).__name__}: {exc}"
        state.finished_at = _now()
        return state

    finally:
        # Put the user back where they started. A win leaves us checked
        # out on the winning variant branch; a mid-pass crash can leave
        # us anywhere. Either way the loop should not silently move
        # someone's working checkout to a branch they didn't ask for.
        #
        # `restore_to` is None whenever we bailed before creating the
        # baseline branch — including the dirty-tree abort — so this
        # never discards work that isn't ours.
        if restore_to is not None:
            try:
                abort_experiment(restore_to)
            except Exception as exc:  # pragma: no cover - defensive
                # Don't mask a real loop result with a cleanup failure;
                # say so loudly instead, since the user is now sitting
                # on a branch they didn't choose.
                try:
                    activity_log.emit(
                        "autoresearch",
                        f"Could not return to {restore_to}: "
                        f"{type(exc).__name__}: {exc}. "
                        f"Run `git checkout {restore_to}` yourself.",
                        "warn",
                        {"event": "branch-restore-failed",
                         "branch": restore_to, "backend": backend},
                    )
                except Exception:
                    pass


def _slug(label: str) -> str:
    return "".join(
        c if c.isalnum() or c == "-" else "-"
        for c in label.lower().replace(" ", "-")
    )[:48].strip("-")


# ── "don't stop, won't stop" supervisor ─────────────────────────────
#
# AeroLLM is the product under test (github.com/cdarnell/qukaizen-aerollm). This
# lab is its performance-engineering partner: we keep sweeping the
# whitelisted knob space, pass after pass, committing wins to
# autoresearch/<id> branches so humans can review + cherry-pick.
# Nothing here forks the upstream — it just measures it relentlessly on
# whatever hardware the lab happens to be on.

def run_autoresearch_forever(
    *,
    backend: str = "aerollm",
    require_env_flag: bool = True,
    candidates: Optional[List[Candidate]] = None,
    progress: Optional[Callable[[LoopState], None]] = None,
    pause_between_passes_sec: float = 5.0,
) -> LoopState:
    """Run ``run_autoresearch`` repeatedly until ``request_stop(backend)``
    is called. Each pass re-measures baseline and re-sweeps every variant;
    if hardware thermals/load have shifted, the next pass will notice.

    Safety: identical to ``run_autoresearch``. Each pass does its own
    clean-tree assertion, so if a variant left something dirty we'll
    fail loudly rather than silently skipping a pass."""
    _require_known_backend(backend)
    state = _STATES[backend]
    state.continuous = True
    state.stop_requested = False
    state.pass_number = 0
    state.schedule = schedule_status()
    first = True
    # While waiting for the schedule window we poll in short slices so
    # stop_requested + schedule edits take effect without the user
    # having to wait for the full until-next-open delta.
    WAIT_TICK_SEC = 30
    while True:
        if _STATES[backend].stop_requested:
            break

        # Re-read the schedule on every iteration so the UI toggle
        # takes effect without a restart. Cheap — one JSON read.
        info = schedule_status()
        state.schedule = info
        if not info["allowed_now"]:
            phase_label = "paused" if info["mode"] == "paused" else "waiting"
            state.phase = phase_label
            state.current_variant = None
            if progress:
                progress(state)
            # Sleep a bounded slice so stop_requested / schedule edits
            # are picked up quickly. For paused mode there's no target
            # wake-up time, so we just tick every WAIT_TICK_SEC. For
            # window mode we sleep min(seconds_until_open, tick).
            wait = info.get("seconds_until_open") or WAIT_TICK_SEC
            time.sleep(max(1, min(wait, WAIT_TICK_SEC)))
            continue

        run_autoresearch(
            backend=backend,
            require_env_flag=require_env_flag,
            candidates=candidates,
            progress=progress,
            preserve_continuous=not first,
        )
        first = False
        state = _STATES[backend]
        if state.phase == "error":
            # Stop the supervisor on hard errors (dirty tree, missing
            # env flag, etc). The user needs to see the message and
            # decide whether to restart.
            break
        if state.stop_requested:
            break
        time.sleep(max(0.0, pause_between_passes_sec))
    state = _STATES[backend]
    state.continuous = False
    state.phase = "done" if not state.error else "error"
    state.finished_at = _now()
    return state
