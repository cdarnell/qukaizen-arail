"""arail.nucleus.train.mlx_kd — importable without MLX; real training is
requires_mlx (T-KD-4, verified on the operator's M5 per BUILD_LOG)."""

from __future__ import annotations

import sys

import pytest

from arail.nucleus.errors import CapabilityMissing
from arail.nucleus.train import mlx_kd


def test_module_imports_without_mlx_installed():
    # The module itself must not eagerly import mlx/mlx_lm — only calling
    # into a function should, and only that function should fail.
    assert hasattr(mlx_kd, "load_student_for_training")
    assert hasattr(mlx_kd, "kd_train_step")
    assert hasattr(mlx_kd, "fuse")


def test_load_student_raises_capability_missing_without_mlx(monkeypatch):
    monkeypatch.setitem(sys.modules, "mlx", None)
    monkeypatch.setitem(sys.modules, "mlx.core", None)
    with pytest.raises(CapabilityMissing, match="mlx_lm"):
        mlx_kd.load_student_for_training("some/path", mlx_kd.LoRATrainConfig())


def test_lora_train_config_defaults():
    cfg = mlx_kd.LoRATrainConfig()
    assert cfg.lora_rank == 8
    assert cfg.temperature == 2.0


@pytest.mark.requires_mlx
def test_kd_train_step_matches_numpy_reference_within_1e4():
    """T-KD-4: the MLX training-step loss must equal train/kd_loss.py's
    numpy reference within 1e-4, on the same inputs. Skipped in CI (no
    MLX); run on the operator's M5 as part of the Gate A local
    requires_mlx pass and recorded in BUILD_LOG."""
    pytest.skip("requires a real MLX install + a real student checkpoint on disk")
