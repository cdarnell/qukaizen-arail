"""MLX LoRA + KD training loop, fuse (ARCHITECTURE.md §4.9; requires_mlx).

Every import of mlx/mlx_lm is lazy (inside functions), so this module
stays importable on CI (no MLX, no Apple Silicon) — only the functions
themselves require the real runtime, and every test that calls one is
marked ``requires_mlx`` and skipped there. The per-token loss math
mirrors train/kd_loss.py's numpy reference exactly (same renorm, same
CE + T^2*KL shape) — that reference is the ground truth this is checked
against on the M5 (T-KD-4), not a separate design.

UNVERIFIED IN THIS SPRINT'S CI SESSION: no mlx/mlx_lm install was
available where this was authored. It is written against the
mlx_lm>=0.31,<0.32 API surface preflight.py checks for (model load,
``linear_to_lora_layers``, fuse), and must be exercised for real on the
operator's M5 as part of the Gate A local `requires_mlx` run recorded in
BUILD_LOG before this is trusted for item 10.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from arail.nucleus.errors import CapabilityMissing
from arail.nucleus.train.kd_loss import renormalize_topn


def _require_mlx():
    try:
        import mlx.core as mx
        import mlx.nn as mnn
        from mlx_lm.tuner.utils import linear_to_lora_layers
        from mlx_lm.utils import load as mlx_lm_load
    except ImportError as exc:
        raise CapabilityMissing(
            f"mlx_lm is required for training and is not importable: {exc}"
        ) from exc
    return mx, mnn, linear_to_lora_layers, mlx_lm_load


@dataclass(frozen=True)
class LoRATrainConfig:
    lora_rank: int = 8
    lora_layers: int = 8
    lora_alpha: float = 16.0
    lora_dropout: float = 0.0
    learning_rate: float = 1e-5
    temperature: float = 2.0
    batch_size: int = 1
    max_steps: int = 200


@dataclass
class LoadedStudent:
    model: Any
    tokenizer: Any
    trainable_params: List[Any]


def load_student_for_training(model_path: str, cfg: LoRATrainConfig) -> LoadedStudent:
    mx, mnn, linear_to_lora_layers, mlx_lm_load = _require_mlx()

    model, tokenizer = mlx_lm_load(model_path)
    model.freeze()
    linear_to_lora_layers(model, cfg.lora_layers, {
        "rank": cfg.lora_rank, "alpha": cfg.lora_alpha, "dropout": cfg.lora_dropout,
    })
    trainable = [p for _, p in _trainable_parameters(model)]
    return LoadedStudent(model=model, tokenizer=tokenizer, trainable_params=trainable)


def _trainable_parameters(model) -> List[Tuple[str, Any]]:
    """Flatten model.trainable_parameters() into (name, array) pairs —
    mlx_lm's LoRA layers mark only the adapter matrices trainable after
    model.freeze() + linear_to_lora_layers()."""
    params = model.trainable_parameters()
    out: List[Tuple[str, Any]] = []

    def _walk(prefix: str, node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                _walk(f"{prefix}.{k}" if prefix else k, v)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _walk(f"{prefix}.{i}", v)
        else:
            out.append((prefix, node))

    _walk("", params)
    return out


def kd_train_step(
    model, batch: Dict[str, Any], *, cfg: LoRATrainConfig, student_max_id: Optional[int] = None,
):
    """One training step over a batch of teacher-generated windows.

    ``batch`` fields (arrays, shape [B, T] unless noted):
        input_ids, sampled_ids (the teacher-generated/realized tokens),
        topn_ids [B, T, N], topn_logprobs [B, T, N].

    Mirrors train/kd_loss.py's per-token formula (CE against the sampled
    token + temperature^2 * KL(teacher_topn || student_topn), both sides
    renormalized over the teacher's reported top-N ids) but vectorized
    over mx arrays instead of numpy, and averaged over the batch.
    """
    mx, mnn, _, _ = _require_mlx()

    def loss_fn(model):
        logits = model(batch["input_ids"])  # [B, T, V]
        total = mx.array(0.0)
        count = 0
        b, t = batch["sampled_ids"].shape
        for bi in range(b):
            for ti in range(t):
                student_logits_full = logits[bi, ti]
                sampled_id = int(batch["sampled_ids"][bi, ti].item())
                topn_ids = [int(x) for x in batch["topn_ids"][bi, ti].tolist()]
                topn_logprobs = [float(x) for x in batch["topn_logprobs"][bi, ti].tolist()]

                # mlx_lm's cross_entropy returns the per-example NLL directly
                # given logits and a target id -- exactly the CE term.
                ce = mnn.losses.cross_entropy(
                    student_logits_full[None, :], mx.array([sampled_id]), reduction="mean"
                )

                renorm = renormalize_topn(topn_ids, topn_logprobs, student_max_id=student_max_id)
                if renorm.kept_ids:
                    student_topn_logits = student_logits_full[mx.array(renorm.kept_ids)]
                    student_topn_logprobs = student_topn_logits - mx.logsumexp(student_topn_logits)
                    teacher_p = mx.array(renorm.probs)
                    kl = mx.sum(teacher_p * (mx.log(mx.maximum(teacher_p, 1e-12)) - student_topn_logprobs))
                else:
                    kl = mx.array(0.0)

                total = total + ce + (cfg.temperature ** 2) * kl
                count += 1
        return total / max(count, 1)

    import mlx.nn as _mnn  # noqa: F401 — ensures nn is resolved for value_and_grad below
    loss_and_grad_fn = mx.value_and_grad(loss_fn)
    loss, grads = loss_and_grad_fn(model)
    return loss, grads


def fuse(model_path: str, adapter_path: Path, output_dir: Path) -> Path:
    """Fuse the trained LoRA adapter into the base weights, writing a
    loadable MLX model directory. GGUF export is explicitly out of scope
    this sprint (ARCHITECTURE.md §3 "Brief §2 GGUF output") — the fused
    MLX dir here is what QueueLLM loads directly."""
    from mlx_lm.fuse import main as mlx_lm_fuse_main

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    mlx_lm_fuse_main([
        "--model", model_path, "--adapter-path", str(adapter_path),
        "--save-path", str(output_dir),
    ])
    return output_dir
