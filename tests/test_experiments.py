"""Tests for the arail.experiments autoresearch loop.

Focused on the safety rails. The actual benchmark path depends on
AeroLLM + a 1 TB model being present, so we stub the backend and
drive the loop end-to-end in-process.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from arail.experiments.tuning import (
    Knob, TuningConfig, ResearchModel,
    load_tuning, save_tuning, validate_knob_value,
)
from arail.experiments.bench import (
    BenchRun, append_run, load_runs, summarize,
)
from arail.experiments.git_ops import (
    ALLOWED_WRITABLE_FILES, GitSafetyError, _repo_root,
)


# ── Knob validation (the safety-critical contract) ─────────────

def _mk_cfg():
    return TuningConfig(
        research_model=ResearchModel(
            name="fake/model", precision="fp16",
            expected_disk_gb=1024, family="moe",
            active_params_b=37, total_params_b=671,
            huggingface_id="fake/model",
        ),
        small_models=[],
        baseline_commit=None, baseline_metrics=None,
        baseline_prompt="hello", baseline_max_tokens=8,
        knobs={
            "aerollm_compression": Knob(
                "aerollm_compression", "4bit", "string",
                ["none", "8bit", "4bit"], None, None, "",
            ),
            "aerollm_max_length": Knob(
                "aerollm_max_length", 512, "int",
                None, 128, 4096, "",
            ),
            "prefetch_enabled": Knob(
                "prefetch_enabled", True, "bool",
                None, None, None, "",
            ),
        },
    )


def test_string_knob_rejects_off_schema():
    cfg = _mk_cfg()
    ok, reason = validate_knob_value(cfg, "aerollm_compression", "2bit")
    assert not ok
    assert "choices" in reason.lower()


def test_int_knob_enforces_range():
    cfg = _mk_cfg()
    ok, reason = validate_knob_value(cfg, "aerollm_max_length", 64)
    assert not ok and "minimum" in reason
    ok, reason = validate_knob_value(cfg, "aerollm_max_length", 99999)
    assert not ok and "maximum" in reason
    ok, _ = validate_knob_value(cfg, "aerollm_max_length", 1024)
    assert ok


def test_bool_knob_rejects_non_bool():
    cfg = _mk_cfg()
    ok, reason = validate_knob_value(cfg, "prefetch_enabled", 1)
    # int is not a bool in our schema — this is deliberate so the
    # agent can't smuggle ints through a bool knob.
    assert not ok
    assert "bool" in reason


def test_unknown_knob_rejected():
    cfg = _mk_cfg()
    ok, reason = validate_knob_value(cfg, "nonexistent_knob", "anything")
    assert not ok
    assert "unknown knob" in reason


# ── File-writing whitelist ─────────────────────────────────────

def test_allowed_writable_files_is_small():
    # The whole security model depends on this set staying tiny.
    # Two backends live here (AeroLLM CUDA + AeroLLM MLX), each with a
    # config file and a bench log — exactly four entries, no more.
    # Adding a third backend means justifying two more entries in a PR.
    assert ALLOWED_WRITABLE_FILES == {
        "config/tuning.yml",
        "config/tuning-mlx.yml",
        "lab/data/aerollm-bench.jsonl",
        "lab/data/mlx-bench.jsonl",
    }
    # And just to make the intent load-bearing: if this set ever grows
    # past 6 entries, something has likely gone wrong.
    assert len(ALLOWED_WRITABLE_FILES) <= 6


# ── Round-trip persistence ─────────────────────────────────────

def test_tuning_yaml_roundtrip(tmp_path):
    cfg = _mk_cfg()
    p = tmp_path / "tuning.yml"
    save_tuning(cfg, p)
    assert p.exists()
    reloaded = load_tuning(p)
    assert reloaded.research_model.name == "fake/model"
    assert set(reloaded.knobs.keys()) == set(cfg.knobs.keys())
    assert reloaded.knobs["aerollm_compression"].choices == [
        "none", "8bit", "4bit"
    ]


def test_real_tuning_yml_parses():
    # The config/tuning.yml that ships in-repo must always be valid.
    # This is the AeroLLM / CUDA track — its whole point is disk-streamed
    # big models, so the ≥ 1 TB rail stays.
    cfg = load_tuning()
    assert cfg.research_model.expected_disk_gb >= 1000, (
        "AeroLLM research_model must be >= 1 TB — this is the point "
        "of the disk-streaming loop"
    )
    # Every knob's `current` value must pass its own schema.
    for name, knob in cfg.knobs.items():
        ok, reason = knob.validate(knob.current)
        assert ok, f"{name}={knob.current!r} fails own schema: {reason}"


def test_real_mlx_tuning_yml_parses():
    # The MLX / AeroLLM track has a different physical constraint: the
    # research model must FIT in unified memory, not stream off disk.
    # So the rail is "big enough to stress KV knobs but small enough
    # to fit on an M-series Mac with headroom" — 5 GB floor, 200 GB
    # ceiling (anything larger means a mis-edit).
    cfg = load_tuning(_mlx_config_path())
    assert 5 <= cfg.research_model.expected_disk_gb <= 200, (
        "MLX research_model must be sized for Apple unified memory; "
        f"got expected_disk_gb={cfg.research_model.expected_disk_gb}"
    )
    # Every knob's `current` value must pass its own schema.
    for name, knob in cfg.knobs.items():
        ok, reason = knob.validate(knob.current)
        assert ok, f"mlx {name}={knob.current!r} fails own schema: {reason}"
    # MLX-specific knobs that the backend dispatcher relies on must
    # actually be present — if a future edit drops them, the runner
    # would silently skip them.
    required_mlx_knobs = {
        "kv_bits", "quantized_kv_start", "max_kv_size",
        "prefill_step_size", "prompt_cache_enabled",
        "bench_runs_per_config", "improvement_threshold_pct",
    }
    missing = required_mlx_knobs - set(cfg.knobs.keys())
    assert not missing, f"tuning-mlx.yml is missing knobs: {missing}"


def _mlx_config_path() -> Path:
    # Walk up from this test file to the repo root, same way the
    # autoresearch loop does it. Keeps tests independent of CWD.
    here = Path(__file__).resolve()
    return here.parent.parent / "config" / "tuning-mlx.yml"


# ── Bench persistence + summary ────────────────────────────────

def _mk_run(tps: float, variant: str = "baseline", status: str = "ok"):
    return BenchRun(
        ts="2026-04-17T12:00:00+00:00",
        git_sha="abc" * 10 + "12",
        git_short_sha="abc12345",
        git_branch="main",
        git_dirty=False,
        model="fake/model",
        prompt="hi",
        prompt_chars=2,
        max_tokens=8,
        tokens_out=8,
        total_latency_ms=8000.0,
        ttft_ms=1000.0,
        decode_tok_per_sec=tps,
        bytes_read=1024,
        peak_rss_mb=100.0,
        knob_values={"aerollm_compression": "4bit"},
        variant_label=variant,
        status=status,
    )


def test_append_and_load_roundtrip(tmp_path):
    p = tmp_path / "bench.jsonl"
    append_run(_mk_run(1.0), p)
    append_run(_mk_run(2.5, variant="prefetch-on"), p)
    rows = load_runs(path=p)
    assert len(rows) == 2
    assert rows[0]["decode_tok_per_sec"] == 1.0
    assert rows[1]["variant_label"] == "prefetch-on"


def test_summarize_picks_best():
    rows = [_mk_run(1.0).to_dict(), _mk_run(2.5).to_dict(), _mk_run(1.8).to_dict()]
    s = summarize(rows)
    assert s["count"] == 3
    assert s["ok_count"] == 3
    assert s["best_tok_per_sec"] == 2.5


def test_summarize_empty_ok():
    s = summarize([])
    assert s["count"] == 0
    assert s["latest"] is None


def test_summarize_ignores_errors():
    rows = [
        _mk_run(1.0, status="ok").to_dict(),
        _mk_run(9.9, status="error").to_dict(),
    ]
    s = summarize(rows)
    assert s["best_tok_per_sec"] == 1.0
    assert s["ok_count"] == 1


# ── Autoresearch loop rails ────────────────────────────────────

def test_autoresearch_requires_env_flag(monkeypatch):
    monkeypatch.delenv("ARAIL_AUTORESEARCH_ENABLED", raising=False)
    from arail.experiments.autoresearch import run_autoresearch
    state = run_autoresearch(require_env_flag=True, candidates=[])
    assert state.phase == "error"
    assert "ARAIL_AUTORESEARCH_ENABLED" in (state.error or "")


def test_autoresearch_refuses_invalid_candidate(monkeypatch, tmp_path):
    # Build a stub backend so bench succeeds
    class StubBackend:
        def complete(self, prompt, max_tokens=512, temperature=0.7, top_p=None):
            from arail.router.backends import ModelResponse
            import time as _t
            _t.sleep(0.01)
            return ModelResponse(
                text="ok", model="fake", tokens_used=max_tokens,
                backend="stub", latency_ms=10.0, cost_usd=0.0,
            )
    # Even with the invalid-variant case we never get to backend
    # construction (validator rejects first), but patching is
    # still important for the baseline phase.

    # Patch git ops so we don't really touch git
    from arail.experiments import autoresearch as ar
    monkeypatch.setattr(ar, "assert_clean_tree", lambda: None)

    class FakeGitState:
        sha = "a" * 40; short_sha = "aaaaaaa"
        branch = "main"; is_dirty = False; dirty_files = []
    monkeypatch.setattr(ar, "git_state", lambda: FakeGitState)
    monkeypatch.setattr(ar, "commit_experiment", lambda **kw: "deadbeef")
    monkeypatch.setattr(ar, "create_experiment_branch",
                        lambda exp_id, base_branch=None: "autoresearch/x")
    monkeypatch.setattr(ar, "abort_experiment", lambda branch: None)
    monkeypatch.setattr(
        ar, "run_bench",
        lambda **kw: _mk_run(1.0, variant=kw.get("variant_label") or "?")
    )
    monkeypatch.setattr(ar, "append_run", lambda r, path=None: None)
    monkeypatch.setattr(ar, "save_tuning", lambda cfg, path=None: None)

    # Invalid candidate: 2bit compression isn't in the schema
    state = ar.run_autoresearch(
        require_env_flag=False,
        candidates=[("evil variant", {"aerollm_compression": "2bit"})],
    )
    assert state.phase == "done"
    assert len(state.variants) == 1
    assert state.variants[0].outcome == "error"
    assert "invalid variant" in (state.variants[0].error or "").lower()


# ── MLX / AeroLLM backend ──────────────────────────────────────
#
# The MLX track is a parallel loop with its own config, bench log,
# candidate list, and LoopState. These tests exercise the backend
# dispatch layer without importing mlx_lm (which isn't available on
# non-Apple CI). The bench runner itself is mocked.

def test_unknown_backend_raises():
    from arail.experiments.autoresearch import (
        current_state, request_stop, _require_known_backend,
    )
    with pytest.raises(ValueError, match="unknown backend"):
        _require_known_backend("cuda")
    with pytest.raises(ValueError):
        current_state("tpu")
    with pytest.raises(ValueError):
        request_stop("rocm")


def test_config_and_commit_paths_per_backend():
    from arail.experiments.autoresearch import _config_path, _commit_files
    aerollm_cfg = _config_path("aerollm")
    mlx_cfg = _config_path("mlx")
    assert aerollm_cfg.name == "tuning.yml"
    assert mlx_cfg.name == "tuning-mlx.yml"
    assert aerollm_cfg != mlx_cfg
    # Commit files must be in the whitelist — else commit_experiment
    # would refuse them at runtime.
    for f in _commit_files("aerollm"):
        assert f in ALLOWED_WRITABLE_FILES
    for f in _commit_files("mlx"):
        assert f in ALLOWED_WRITABLE_FILES


def test_mlx_candidates_pass_schema():
    # Every hand-curated MLX candidate must pass the MLX config's
    # schema — if someone typos a knob name or value this catches it
    # before the loop runs for real.
    from arail.experiments.autoresearch import MLX_CANDIDATES
    cfg = load_tuning(_mlx_config_path())
    for label, delta in MLX_CANDIDATES:
        for k, v in delta.items():
            ok, reason = cfg.set_knob(k, v)
            assert ok, f"MLX candidate {label!r}: {k}={v!r} → {reason}"


def test_mlx_per_state_isolation():
    from arail.experiments.autoresearch import (
        current_state, request_stop, _STATES,
    )
    # Starting fresh per-backend
    _STATES["aerollm"].stop_requested = False
    _STATES["mlx"].stop_requested = False
    request_stop("mlx")
    # Only the MLX state flipped — AeroLLM must be untouched.
    assert current_state("mlx").stop_requested is True
    assert current_state("aerollm").stop_requested is False
    # Clean up so later tests start fresh.
    _STATES["mlx"].stop_requested = False


def test_mlx_backend_knob_translation():
    # Knob → mlx_lm kwargs mapping is the translation layer between
    # the YAML schema and Apple's runtime. Tested in isolation so a
    # future mlx_lm version bump doesn't silently break the wiring.
    from arail.experiments.mlx_backend import (
        _build_generate_kwargs, _pick_model_id, _KV_BITS_MAP,
    )
    # fp16 means "don't quantize" — must NOT set kv_bits at all
    kwargs = _build_generate_kwargs({"kv_bits": "fp16"})
    assert "kv_bits" not in kwargs
    # 4bit propagates + brings quantized_kv_start along
    kwargs = _build_generate_kwargs(
        {"kv_bits": "4bit", "quantized_kv_start": 2048}
    )
    assert kwargs["kv_bits"] == 4
    assert kwargs["quantized_kv_start"] == 2048
    # Other whitelisted knobs propagate verbatim
    kwargs = _build_generate_kwargs(
        {"max_kv_size": 8192, "prefill_step_size": 256}
    )
    assert kwargs["max_kv_size"] == 8192
    assert kwargs["prefill_step_size"] == 256
    # Model picker honors the override knob
    assert _pick_model_id(
        {"model_quant_variant": "org/my-4bit"}, fallback="fallback/x"
    ) == "org/my-4bit"
    assert _pick_model_id({}, fallback="fallback/x") == "fallback/x"
    # The KV_BITS_MAP is the safety contract — fp16 must map to None
    # (meaning "skip the kwarg entirely").
    assert _KV_BITS_MAP["fp16"] is None
    assert _KV_BITS_MAP["8bit"] == 8
    assert _KV_BITS_MAP["4bit"] == 4


def test_mlx_backend_handles_missing_mlx_lm(monkeypatch):
    # On non-Apple CI, mlx_lm isn't installed. The runner must STILL
    # return a BenchRun (not raise) so the dashboard shows a clean
    # error row rather than the loop crashing.
    from arail.experiments import mlx_backend as mb

    # Force the import-time guard to think mlx_lm is missing by
    # having _mlx_lm=None and letting the inner `import mlx_lm` fail.
    # We simulate the failure by passing a sentinel that raises when
    # .load is called — simpler than fighting the import system.
    class _FakeMissingMLX:
        def load(self, *a, **kw):
            raise RuntimeError("simulated: mlx_lm not installed")

    # Patch git_state so we don't need a real repo context.
    from arail.experiments import git_ops
    class _FakeGit:
        sha = "b" * 40; short_sha = "bbbbbbb"
        branch = "main"; is_dirty = False; dirty_files = []
    monkeypatch.setattr(git_ops, "git_state", lambda: _FakeGit)

    run = mb.run_mlx_bench(
        research_model_name="fake/mlx-model",
        prompt="hello",
        max_tokens=4,
        knob_values={"kv_bits": "fp16"},
        variant_label="test",
        _mlx_lm=_FakeMissingMLX(),
    )
    assert run.status == "error"
    assert run.error is not None
    # The record still carries git + model metadata so the dashboard
    # can render it alongside ok rows without a branch.
    assert run.model == "fake/mlx-model"
    assert run.git_short_sha == "bbbbbbb"


def test_mlx_backend_file_sibling_of_aerollm():
    # The two bench logs must live side-by-side in the same DATA_DIR
    # so a cleanup script that deletes one reaches the other too.
    from arail.experiments.mlx_backend import mlx_bench_file
    from arail.config import DATA_DIR
    p = mlx_bench_file()
    assert p.parent == DATA_DIR
    assert p.name == "mlx-bench.jsonl"


def test_autoresearch_mlx_uses_mlx_config_and_bench(monkeypatch):
    # End-to-end dispatch: run_autoresearch(backend="mlx") must route
    # every side effect to the MLX-side paths — config loader reads
    # tuning-mlx.yml, bench writes mlx-bench.jsonl, commits name the
    # MLX file pair. We stub the bench runner so nothing actually
    # tries to load a model.
    from arail.experiments import autoresearch as ar
    from arail.experiments import mlx_backend as mb

    # Capture the config path load_tuning was called with.
    captured: dict = {}
    real_load = ar.load_tuning

    def spying_load(path=None):
        captured["config_path"] = path
        return real_load(path)
    monkeypatch.setattr(ar, "load_tuning", spying_load)

    # No real git ops / no real file writes.
    monkeypatch.setattr(ar, "assert_clean_tree", lambda: None)

    class _FakeGit:
        sha = "c" * 40; short_sha = "ccccccc"
        branch = "main"; is_dirty = False; dirty_files = []
    monkeypatch.setattr(ar, "git_state", lambda: _FakeGit)
    monkeypatch.setattr(ar, "save_tuning", lambda cfg, path=None: None)
    commit_calls: list = []
    def fake_commit(**kw):
        commit_calls.append(kw)
        return "deadbeef"
    monkeypatch.setattr(ar, "commit_experiment", fake_commit)
    monkeypatch.setattr(ar, "create_experiment_branch",
                        lambda exp_id, base_branch=None: "autoresearch/x")
    monkeypatch.setattr(ar, "abort_experiment", lambda branch: None)
    monkeypatch.setattr(ar, "append_run", lambda r, path=None: None)

    # Stub the MLX runner — return a constant tok/s so nothing beats
    # baseline (threshold >= 5%), every variant lands as a "loss".
    def fake_run_mlx(**kw):
        return _mk_run(1.0, variant=kw.get("variant_label") or "?")
    monkeypatch.setattr(mb, "run_mlx_bench", fake_run_mlx)

    state = ar.run_autoresearch(
        backend="mlx",
        require_env_flag=False,
        candidates=[("kv-fp16 (sanity)", {"kv_bits": "fp16"})],
    )

    assert state.backend == "mlx"
    assert state.phase == "done"
    assert captured["config_path"] is not None
    assert captured["config_path"].name == "tuning-mlx.yml"
    # Exactly one variant result — the candidate we passed.
    assert len(state.variants) == 1
    # Commits, if any, must reference the MLX file pair only.
    for kw in commit_calls:
        for f in kw.get("files", []):
            assert f in {"config/tuning-mlx.yml",
                         "lab/data/mlx-bench.jsonl"}


# ── Frontier-model schema + backward compat ────────────────────────

def test_aerollm_config_has_no_frontier_models():
    # AeroLLM's config predates the frontier_models schema. It should
    # still parse cleanly, and the frontier list should come back
    # empty — no spurious entries, no crashes. This is the
    # backward-compat rail: adding the field to the dataclass must
    # not break any config that hasn't opted in.
    cfg = load_tuning()
    assert cfg.frontier_models == []
    assert cfg.frontier_baselines == {}


def test_mlx_config_declares_frontier_models():
    # The MLX track's whole pedagogical point is the frontier strip:
    # 670B-750B models that don't fit any commercial GPU. If this
    # list shrinks to zero or loses streaming_required flags, the
    # dashboard stops telling the truth. Pin the invariants.
    cfg = load_tuning(_mlx_config_path())

    assert len(cfg.frontier_models) >= 5, (
        "MLX config should declare the 5+ frontier challenge models "
        "(DeepSeek-V3, DeepSeek-R1, Kimi K2, Llama 3.1 405B, GLM-4.6 "
        "target — see the MLX streaming rationale in repo docs)"
    )

    # Every frontier entry is streaming-required AND declares how big
    # it actually is. A silent 0-GB row would misrepresent the problem.
    for fm in cfg.frontier_models:
        assert fm.streaming_required is True, (
            f"{fm.name}: streaming_required must be True for frontier "
            f"entries — this is what triggers the honest-failure path"
        )
        assert fm.expected_disk_gb > 0, (
            f"{fm.name}: expected_disk_gb must be non-zero — "
            f"the dashboard shows this on the card header"
        )
        # The gpu_fit table should list all three tiers we bench
        # against. Missing keys would silently render as "fits" in
        # the JS because `!!undefined === false`; requiring them
        # forces authors to make an explicit call per tier.
        for tier in ("h100_80gb", "h200_141gb", "b200_192gb"):
            assert tier in fm.gpu_fit, (
                f"{fm.name}: gpu_fit is missing '{tier}' — every "
                f"frontier entry must take a stance on every GPU tier"
            )

    # At least one GLM entry must exist — the "closest thing to AI's
    # god on commodity hardware" bet names GLM-4.5 / GLM-4.6.
    glm_hits = [fm for fm in cfg.frontier_models
                if "GLM" in (fm.name or "") or "glm" in (fm.huggingface_id or "").lower()]
    assert glm_hits, (
        "MLX config should declare at least one GLM frontier entry — "
        "the academic narrative is built around the 670B-750B GLM class"
    )


def test_frontier_model_roundtrips_through_yaml(tmp_path):
    # save_tuning must only emit frontier_models when they're present
    # (AeroLLM's config stays clean), AND must round-trip without loss
    # when they are present (MLX's config survives edit-save cycles).
    from arail.experiments.tuning import FrontierModel

    # Case 1: no frontier models → no frontier keys in output.
    cfg_aerollm = _mk_cfg()
    out_air = tmp_path / "aerollm.yml"
    save_tuning(cfg_aerollm, out_air)
    text_air = out_air.read_text()
    assert "frontier_models" not in text_air
    assert "frontier_baselines" not in text_air

    # Case 2: frontier models present → full round-trip preserved.
    cfg_mlx = _mk_cfg()
    cfg_mlx.frontier_models = [
        FrontierModel(
            name="Test Frontier 700B",
            huggingface_id="test/frontier-4bit",
            family="moe",
            active_params_b=35,
            total_params_b=700,
            precision="q4",
            expected_disk_gb=350,
            streaming_required=True,
            gpu_fit={"h100_80gb": False, "h200_141gb": False, "b200_192gb": False},
            rationale="fixture model for roundtrip test",
        ),
    ]
    cfg_mlx.frontier_baselines = {
        "test/frontier-4bit": {
            "status": "error",
            "error": "streaming_required: waiting on streaming layer",
            "ts": "2026-04-17T12:00:00+00:00",
        },
    }
    out_mlx = tmp_path / "mlx.yml"
    save_tuning(cfg_mlx, out_mlx)

    reloaded = load_tuning(out_mlx)
    assert len(reloaded.frontier_models) == 1
    fm = reloaded.frontier_models[0]
    assert fm.huggingface_id == "test/frontier-4bit"
    assert fm.total_params_b == 700
    assert fm.expected_disk_gb == 350
    assert fm.streaming_required is True
    assert fm.gpu_fit == {"h100_80gb": False, "h200_141gb": False, "b200_192gb": False}
    assert "fixture model" in fm.rationale

    assert "test/frontier-4bit" in reloaded.frontier_baselines
    assert reloaded.frontier_baselines["test/frontier-4bit"]["status"] == "error"


def test_load_tuning_skips_malformed_frontier_entries(tmp_path):
    # An entry missing huggingface_id is the "half-written" shape a
    # human is likely to leave behind mid-edit. We should skip it
    # rather than crash the whole load — the dashboard showing
    # "5 frontier models" instead of "6" is a much better failure
    # mode than the /tuning page 500ing.
    import yaml
    raw = {
        "research_model": {
            "name": "fake/research",
            "precision": "q4",
            "expected_disk_gb": 11,
            "family": "dense",
            "active_params_b": 20,
            "total_params_b": 20,
            "huggingface_id": "fake/research",
        },
        "small_models": [],
        "baseline_prompt": "hi",
        "baseline_max_tokens": 8,
        "knobs": {},
        "frontier_models": [
            # valid entry
            {
                "name": "Valid 700B",
                "huggingface_id": "real/frontier-4bit",
                "family": "moe",
                "active_params_b": 35,
                "total_params_b": 700,
                "precision": "q4",
                "expected_disk_gb": 350,
                "streaming_required": True,
                "gpu_fit": {"h100_80gb": False,
                            "h200_141gb": False,
                            "b200_192gb": False},
                "rationale": "real",
            },
            # half-written entry — no huggingface_id
            {
                "name": "Half-written",
                "family": "moe",
            },
            # not a dict at all — defensive against YAML typos
            "this is a string not a dict",
        ],
    }
    p = tmp_path / "partial.yml"
    p.write_text(yaml.safe_dump(raw))
    cfg = load_tuning(p)
    # Only the one valid entry survives.
    assert len(cfg.frontier_models) == 1
    assert cfg.frontier_models[0].huggingface_id == "real/frontier-4bit"


def test_api_tuning_config_exposes_frontier_fields():
    # The /api/tuning/config serializer must expose the frontier
    # fields for BOTH backends — AeroLLM should return empty lists
    # (not omit the keys) so the JS can safely `.length`-check
    # without a "key not in object" branch.
    from fastapi.testclient import TestClient
    from arail.portal.app import app
    client = TestClient(app)

    for backend, expect_frontier in (
        ("aerollm", False),  # empty
        ("mlx",    True),   # non-empty
    ):
        r = client.get(f"/api/tuning/config?backend={backend}")
        assert r.status_code == 200, r.text
        data = r.json()
        assert "frontier_models" in data, (
            f"backend={backend}: /api/tuning/config must always "
            f"include frontier_models key (even if empty) so the JS "
            f"renderer can branch on length without a defensive guard"
        )
        assert "frontier_baselines" in data
        assert isinstance(data["frontier_models"], list)
        assert isinstance(data["frontier_baselines"], dict)
        if expect_frontier:
            assert len(data["frontier_models"]) >= 5
            for fm in data["frontier_models"]:
                # The JS paintFrontier function pattern-matches on
                # these keys; missing any of them would silently
                # hide information in the dashboard.
                for k in ("name", "huggingface_id", "family",
                          "total_params_b", "expected_disk_gb",
                          "streaming_required", "gpu_fit", "rationale"):
                    assert k in fm, f"serialized frontier model missing '{k}'"
        else:
            assert data["frontier_models"] == []


# ── MLX backend: frontier bench always returns a BenchRun ──────────

def test_run_frontier_bench_streaming_required_short_circuit():
    # The core contract of run_frontier_bench: NEVER raise. When
    # streaming_required is True, short-circuit with a structured
    # error reason BEFORE touching mlx_lm. The dashboard pattern-
    # matches on the FRONTIER_ERROR_PREFIX["streaming_required"]
    # string; if the prefix drifts, the dashboard misrenders.
    from arail.experiments.mlx_backend import (
        run_frontier_bench, FRONTIER_ERROR_PREFIX,
    )
    from arail.experiments.tuning import FrontierModel

    m = FrontierModel(
        name="DeepSeek-V3 671B (4-bit)",
        huggingface_id="mlx-community/DeepSeek-V3-4bit",
        family="moe",
        active_params_b=37, total_params_b=671,
        precision="q4", expected_disk_gb=335,
        streaming_required=True,
        gpu_fit={"h100_80gb": False, "h200_141gb": False, "b200_192gb": False},
        rationale="test",
    )
    run = run_frontier_bench(model=m, prompt="hi", max_tokens=1)
    assert run.status == "error"
    assert run.error is not None
    # Load-bearing prefix — see templates/tuning.html paintFrontier.
    assert run.error.startswith("streaming_required:"), run.error
    assert run.error == FRONTIER_ERROR_PREFIX["streaming_required"]
    # Tagged so the dashboard can filter frontier rows.
    assert run.variant_label == "frontier:DeepSeek-V3 671B (4-bit)"


def test_run_frontier_bench_never_raises_on_load_failure():
    # Even when mlx_lm IS present but the load itself explodes
    # (OOM, missing file, unsupported quant, …), we must still return
    # a BenchRun — the loop calls this per-model during baseline
    # capture and a raise would kill the whole sweep.
    from arail.experiments.mlx_backend import run_frontier_bench
    from arail.experiments.tuning import FrontierModel

    class _BoomMlx:
        @staticmethod
        def load(model_id):
            raise RuntimeError("synthetic OOM")

    m = FrontierModel(
        name="NonStreaming",
        huggingface_id="fake/nonstreaming",
        family="dense",
        active_params_b=100, total_params_b=100,
        precision="q4", expected_disk_gb=50,
        streaming_required=False,  # force the real load path
        gpu_fit={"h100_80gb": False, "h200_141gb": False, "b200_192gb": True},
        rationale="test",
    )
    run = run_frontier_bench(
        model=m, prompt="hi", max_tokens=1, _mlx_lm=_BoomMlx,
    )
    assert run.status == "error"
    assert run.error is not None
    assert "load_failed" in run.error
    assert "synthetic OOM" in run.error


# ── Baseline never lands on the user's branch (hazard H1/H2) ────────
#
# Regression guard for the fix documented in
# docs/plans/autoresearch-integration.md §4 decision (1): the baseline
# capture used to be committed before any autoresearch/ branch existed,
# so running the loop from `main` put a commit on `main` — which later
# made `./arailctl update`'s `git pull --ff-only` refuse.

def _stub_git(monkeypatch, ar, *, start_branch="main"):
    """Patch every git seam and return a log of branch operations."""
    ops: list = []

    class _FakeGit:
        sha = "d" * 40
        short_sha = "ddddddd"
        branch = start_branch
        is_dirty = False
        dirty_files: list = []

    def fake_create(exp_id, base_branch=None):
        branch = "autoresearch/" + exp_id
        ops.append(("create", branch, base_branch))
        return branch

    def fake_commit(**kw):
        ops.append(("commit", kw.get("subject", "")))
        return "cafe" * 10

    monkeypatch.setattr(ar, "assert_clean_tree", lambda: None)
    monkeypatch.setattr(ar, "git_state", lambda: _FakeGit)
    monkeypatch.setattr(ar, "save_tuning", lambda cfg, path=None: None)
    monkeypatch.setattr(ar, "append_run", lambda r, path=None: None)
    monkeypatch.setattr(ar, "create_experiment_branch", fake_create)
    monkeypatch.setattr(ar, "commit_experiment", fake_commit)
    monkeypatch.setattr(
        ar, "abort_experiment",
        lambda branch: ops.append(("checkout", branch)),
    )
    monkeypatch.setattr(
        ar, "run_bench",
        lambda **kw: _mk_run(1.0, variant=kw.get("variant_label") or "?"),
    )
    return ops


def test_baseline_is_committed_on_its_own_autoresearch_branch(monkeypatch):
    from arail.experiments import autoresearch as ar

    ops = _stub_git(monkeypatch, ar, start_branch="main")
    state = ar.run_autoresearch(
        require_env_flag=False,
        candidates=[("context-256 (smaller KV cache)",
                     {"aerollm_max_length": 256})],
    )

    assert state.phase == "done"
    # A baseline branch was created, off main, before anything committed.
    creates = [o for o in ops if o[0] == "create"]
    assert creates, "no branch was ever created"
    first_branch, first_base = creates[0][1], creates[0][2]
    assert first_branch.startswith("autoresearch/baseline-")
    assert first_base == "main"
    assert state.baseline_branch == first_branch
    assert state.to_dict()["baseline_branch"] == first_branch

    # The critical assertion: nothing is committed before we have left
    # the user's branch. Any commit at index < the first create would
    # be a commit landing on `main`.
    first_create_at = next(i for i, o in enumerate(ops) if o[0] == "create")
    first_commit_at = next(
        (i for i, o in enumerate(ops) if o[0] == "commit"), None
    )
    assert first_commit_at is None or first_commit_at > first_create_at, (
        "a commit was made while still on the user's branch"
    )


def test_variants_branch_from_the_baseline_branch(monkeypatch):
    from arail.experiments import autoresearch as ar

    ops = _stub_git(monkeypatch, ar, start_branch="main")
    ar.run_autoresearch(
        require_env_flag=False,
        candidates=[("context-256 (smaller KV cache)",
                     {"aerollm_max_length": 256})],
    )

    creates = [o for o in ops if o[0] == "create"]
    baseline_branch = creates[0][1]
    variant_creates = creates[1:]
    assert variant_creates, "no variant branch was created"
    for _, _branch, base in variant_creates:
        # Never off the user's branch — always off the baseline.
        assert base == baseline_branch


def test_loop_returns_the_user_to_their_original_branch(monkeypatch):
    from arail.experiments import autoresearch as ar

    ops = _stub_git(monkeypatch, ar, start_branch="my-feature")
    ar.run_autoresearch(
        require_env_flag=False,
        candidates=[("context-256 (smaller KV cache)",
                     {"aerollm_max_length": 256})],
    )

    checkouts = [o[1] for o in ops if o[0] == "checkout"]
    assert checkouts, "loop never checked anything back out"
    assert checkouts[-1] == "my-feature", (
        f"loop left the user on {checkouts[-1]!r}, not their own branch"
    )


def test_dirty_tree_abort_never_discards_user_work(monkeypatch):
    """The restore path runs `git checkout -- .`. It must not fire when
    we bailed because the USER's tree was dirty — that would delete
    their uncommitted work."""
    from arail.experiments import autoresearch as ar
    from arail.experiments.git_ops import GitSafetyError

    ops = _stub_git(monkeypatch, ar)

    def boom():
        raise GitSafetyError("Working tree is dirty; refusing to run")
    monkeypatch.setattr(ar, "assert_clean_tree", boom)

    state = ar.run_autoresearch(require_env_flag=False, candidates=[])

    assert state.phase == "error"
    assert "dirty" in (state.error or "").lower()
    assert not [o for o in ops if o[0] == "checkout"], (
        "restore ran after a dirty-tree abort — would discard user work"
    )
    assert not [o for o in ops if o[0] == "create"]
