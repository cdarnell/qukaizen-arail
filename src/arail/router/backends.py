"""Backend implementations for every supported accelerator / cloud service."""

from __future__ import annotations

import atexit
import json
import logging
import os
import selectors
import subprocess
import sys
import threading
import time
import uuid
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Context-window override resolver (L3 — sprint 2026-05-18)
# ---------------------------------------------------------------------------

def _resolve_ctx_override(model_name: str, default: "int | None") -> "int | None":
    """Return the resolved context window size for *model_name*.

    Resolution order:
      1. Exact match in ARAIL_MODEL_CTX_OVERRIDES env (JSON dict).
      2. Substring match in ARAIL_MODEL_CTX_OVERRIDES (key is substring of model_name).
      3. model_specs.context_tokens(context_label(model_name)) — from the spec registry.
      4. *default* (passed by caller — 4096 for CPUBackend, None for Ollama).

    Result is clamped to [256, 1_000_000] when non-None (mirrors admin validation).
    Never raises — bad JSON → falls through to step 3/4.
    """
    _MIN_CTX = 256
    _MAX_CTX = 1_000_000

    def _clamp(v: int) -> int:
        return max(_MIN_CTX, min(_MAX_CTX, v))

    # Step 1 + 2 — env override (exact first, then substring)
    raw_env = os.getenv("ARAIL_MODEL_CTX_OVERRIDES", "").strip()
    if raw_env:
        try:
            overrides: dict = json.loads(raw_env)
            if isinstance(overrides, dict):
                # Exact match
                if model_name in overrides:
                    try:
                        return _clamp(int(overrides[model_name]))
                    except (TypeError, ValueError):
                        pass
                # Substring match (key contained in model_name)
                for key, val in overrides.items():
                    if key and key in model_name:
                        try:
                            return _clamp(int(val))
                        except (TypeError, ValueError):
                            pass
        except Exception:  # noqa: BLE001
            pass  # bad JSON → ignore

    # Step 3 — model spec registry
    try:
        from arail.model_specs import context_tokens, context_label
        label = context_label(model_name)
        if label is not None:
            tokens = context_tokens(label)
            if tokens is not None:
                return _clamp(tokens)
    except Exception:  # noqa: BLE001
        pass  # model_specs not available → ignore

    # Step 4 — caller's default
    return default


# ---------------------------------------------------------------------------
# Shared response type
# ---------------------------------------------------------------------------
@dataclass
class ModelResponse:
    text: str
    model: str
    tokens_used: int
    backend: str
    latency_ms: float
    cost_usd: Optional[float] = None
    # Anthropic prompt-caching usage (claude backend only; 0 elsewhere).
    # cache_read = tokens served from cache (~0.1x cost); cache_creation =
    # tokens written to cache this request (~1.25x cost). See ClaudeBackend.
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


StreamResult = str | ModelResponse


def _coerce_stream_text(item: Any, current_text: str) -> tuple[str, str]:
    """Normalize backend-specific stream items into ``(full, delta)``."""
    raw: Any
    if isinstance(item, str):
        raw = item
    else:
        raw = getattr(item, "text", None)
        if raw is None:
            raw = getattr(item, "token", None)
        if raw is None:
            raw = getattr(item, "content", None)
        if raw is None:
            raw = str(item)

    text = str(raw or "")
    if not text:
        return current_text, ""
    if current_text and text.startswith(current_text):
        return text, text[len(current_text):]
    return current_text + text, text


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------
class BaseBackend(ABC):
    @abstractmethod
    def complete(self, prompt: str, max_tokens: int = 512,
                 temperature: float = 0.7,
                 top_p: Optional[float] = None,
                 *, system: Optional[str] = None,
                 messages: Optional[list] = None) -> ModelResponse:
        """Run one completion.

        ``top_p`` is optional. When None, the backend uses its default
        sampling policy. Preset buttons on the dashboard set it to
        specific values (0.9 for Factual, 0.95 for Code, etc.).
        Backends that don't support top_p ignore it silently.

        ``system`` is an optional *stable prefix* (frozen system prompt).
        Non-Claude backends prepend it to ``prompt`` (``f"{system}\\n\\n{prompt}"``)
        so the rendered bytes match the historic single-string behavior.
        ``ClaudeBackend`` sends it as a cached ``system`` block (prompt
        caching). ``messages`` is an optional structured turn list consumed
        only by ``ClaudeBackend`` (for multi-turn cache reuse); other
        backends ignore it and use the flat ``prompt``. Both default to
        None, so existing callers are unaffected.
        """
        ...

    def stream_complete(self, prompt: str, max_tokens: int = 512,
                        temperature: float = 0.7,
                        top_p: Optional[float] = None,
                        *, system: Optional[str] = None,
                        messages: Optional[list] = None) -> Iterator[StreamResult]:
        """Yield text deltas and finish with a ``ModelResponse``.

        Backends that do not support native streaming fall back to one
        blocking completion and yield the final response as a single item.
        """
        yield self.complete(prompt, max_tokens, temperature, top_p=top_p,
                            system=system, messages=messages)

    @abstractmethod
    def health_check(self) -> bool:
        ...


# ---------------------------------------------------------------------------
# MLX  (macOS Apple Silicon — completely local)
# ---------------------------------------------------------------------------
class MLXBackend(BaseBackend):
    def __init__(self) -> None:
        try:
            from mlx_lm import load, generate  # type: ignore[import-untyped]
            import mlx_lm as _mlx_lm  # type: ignore[import-untyped]
            self._load = load
            self._generate = generate
            self._stream_generate = getattr(_mlx_lm, "stream_generate", None)
        except ImportError as exc:
            # Keep the real reason. "MLX not installed" is only one of the
            # ways this import fails — a broken/partial install, an
            # incompatible mlx/mlx-lm pair, or a sandbox that blocks the
            # native extension all land here too, and reporting them all
            # as "not installed" sends people to `pip install` for a
            # problem pip already solved.
            raise ImportError(
                f"MLX backend unavailable: {exc}. "
                "If mlx-lm is genuinely missing: pip install mlx mlx-lm"
            ) from exc

        # New-style sampler factory is preferred; fall back to the old
        # `temp=` kwarg if we're on a pre-0.19 mlx-lm.
        try:
            from mlx_lm.sample_utils import make_sampler  # type: ignore[import-untyped]
            self._make_sampler = make_sampler
        except ImportError:  # pragma: no cover — only on old mlx-lm
            self._make_sampler = None

        # Default kept under the primary ceiling (arail.registry.ceiling) —
        # an unset MODEL_NAME must not silently resolve to an 8B+ model.
        self.model_name = os.getenv("MODEL_NAME",
                                     "mlx-community/Qwen2.5-3B-Instruct-4bit")
        # Allow local path first, fallback to hub name
        model_dir = os.path.join(os.getenv("ARAIL_MODELS_DIR", "lab/models"),
                                 self.model_name.split("/")[-1])
        path = model_dir if os.path.isdir(model_dir) else self.model_name

        # Optional LoRA adapter (e.g. the qkz-expert "house voice" adapter).
        # ARAIL_MLX_ADAPTER points at a directory containing
        # adapters.safetensors. Unset -> unchanged behaviour.
        #
        # If it IS set we fail loudly rather than quietly serving the base
        # model: the operator asked for a specific adapter, and silently
        # answering as a different model is the "looks fine but isn't" failure
        # this codebase keeps paying for. The size floor is the same signal the
        # training guard uses (scripts/train_qkz_expert.py) — a simulation-mode
        # mock checkpoint is ~1KB, a real adapter is megabytes.
        self.adapter_path = (os.getenv("ARAIL_MLX_ADAPTER", "") or "").strip() or None
        if self.adapter_path:
            weights = os.path.join(self.adapter_path, "adapters.safetensors")
            if not os.path.isdir(self.adapter_path) or not os.path.isfile(weights):
                raise RuntimeError(
                    f"ARAIL_MLX_ADAPTER={self.adapter_path!r} has no "
                    "adapters.safetensors. Refusing to silently serve the base "
                    "model under an adapter's name — unset the variable or fix "
                    "the path."
                )
            if os.path.getsize(weights) < 1_000_000:
                raise RuntimeError(
                    f"ARAIL_MLX_ADAPTER={self.adapter_path!r} is "
                    f"{os.path.getsize(weights)} bytes — too small to be real "
                    "LoRA weights (a simulated/mock checkpoint is ~1KB). "
                    "Verify with: python scripts/train_qkz_expert.py "
                    "--verify-only <path>"
                )
            self.model, self.tokenizer = self._load(path, adapter_path=self.adapter_path)
        else:
            self.model, self.tokenizer = self._load(path)

        # Install a Metal soft memory limit so the allocator swaps instead
        # of throwing kIOGPUCommandBufferCallbackErrorOutOfMemory — which
        # is a C++ std::runtime_error that aborts the entire interpreter.
        # Tunable via ARAIL_MLX_MEMORY_LIMIT_PCT (default 0.85).
        from arail.router.mlx_guard import install_memory_soft_limit
        try:
            limit_pct = float(os.getenv("ARAIL_MLX_MEMORY_LIMIT_PCT", "0.85"))
        except ValueError:
            limit_pct = 0.85
        install_memory_soft_limit(fraction=limit_pct)

    def complete(self, prompt: str, max_tokens: int = 512,
                 temperature: float = 0.7,
                 top_p: Optional[float] = None,
                 *, system: Optional[str] = None,
                 messages: Optional[list] = None) -> ModelResponse:
        if system:
            prompt = f"{system}\n\n{prompt}"
        start = time.time()
        # Memory-pressure guard. Clears the Metal cache and refuses
        # the call when accumulated activations have crossed the
        # configurable threshold (default 85%). The Metal allocator
        # OOM is a C++ exception that bypasses Python try/except, so
        # we'd rather surface a typed Python error here than let it
        # nuke the parent process. Callers (e.g. goal_parser) catch
        # MetalOutOfMemory and fall back to heuristic / smaller path.
        from arail.router.mlx_guard import assert_metal_safe, clear_metal_cache
        assert_metal_safe(op=f"MLX generate({self.model_name})")
        try:
            # mlx-lm ≥ 0.19 removed the `temp=` kwarg on generate(); you
            # now build a sampler via make_sampler(temp=...) and pass it
            # as `sampler=`. Old versions still accept `temp=` directly.
            if self._make_sampler is not None:
                sampler_kwargs = {"temp": temperature}
                if top_p is not None:
                    sampler_kwargs["top_p"] = top_p
                sampler = self._make_sampler(**sampler_kwargs)
                text = self._generate(
                    self.model, self.tokenizer,
                    prompt=prompt,
                    max_tokens=max_tokens,
                    sampler=sampler,
                    verbose=False,
                )
            else:
                # Pre-0.19 mlx-lm has no top_p support; silently drop it.
                # Some mid-version mlx-lm builds removed `temp=` but also
                # lack make_sampler — guard with a fallback to avoid the
                # repeated "unexpected keyword argument 'temp'" crash.
                try:
                    text = self._generate(
                        self.model, self.tokenizer,
                        prompt=prompt,
                        max_tokens=max_tokens,
                        temp=temperature,
                        verbose=False,
                    )
                except TypeError:
                    # Newer generate() that dropped temp= but predates
                    # make_sampler.
                    text = self._generate(
                        self.model, self.tokenizer,
                        prompt=prompt,
                        max_tokens=max_tokens,
                        verbose=False,
                    )
        finally:
            # Always release activations after a generation pass —
            # leaving them in flight makes the next assert_metal_safe
            # spuriously refuse despite the model itself being smaller
            # than the limit.
            clear_metal_cache()
        return ModelResponse(
            text=text,
            model=self.model_name,
            tokens_used=len(self.tokenizer.encode(text)),
            backend="mlx",
            latency_ms=(time.time() - start) * 1000,
            cost_usd=0.0,
        )

    def health_check(self) -> bool:
        try:
            r = self.complete("test", max_tokens=5)
            return len(r.text) > 0
        except Exception:
            return False

    def stream_complete(self, prompt: str, max_tokens: int = 512,
                        temperature: float = 0.7,
                        top_p: Optional[float] = None) -> Iterator[StreamResult]:
        if self._stream_generate is None:
            yield self.complete(prompt, max_tokens, temperature, top_p=top_p)
            return

        start = time.time()
        sampler = None
        if self._make_sampler is not None:
            sampler_kwargs = {"temp": temperature}
            if top_p is not None:
                sampler_kwargs["top_p"] = top_p
            sampler = self._make_sampler(**sampler_kwargs)

        kwargs: dict[str, Any] = {
            "prompt": prompt,
            "max_tokens": max_tokens,
        }
        if sampler is not None:
            kwargs["sampler"] = sampler

        full_text = ""
        for item in self._stream_generate(self.model, self.tokenizer, **kwargs):
            full_text, delta = _coerce_stream_text(item, full_text)
            if delta:
                yield delta

        yield ModelResponse(
            text=full_text,
            model=self.model_name,
            tokens_used=len(self.tokenizer.encode(full_text)),
            backend="mlx",
            latency_ms=(time.time() - start) * 1000,
            cost_usd=0.0,
        )


# ---------------------------------------------------------------------------
# CUDA  (Linux / WSL — local Nvidia GPU via vLLM OpenAI-compat server)
# ---------------------------------------------------------------------------
class CUDABackend(BaseBackend):
    def __init__(self) -> None:
        import requests  # noqa: F811
        self._session = requests.Session()  # noqa-airgap: localhost-only (LOCAL_API_PORT target); post-guard Session — HTTPAdapter is monkeypatched at install_guard() time
        self.port = int(os.getenv("LOCAL_API_PORT", "8000"))
        # Default kept under the primary ceiling (arail.registry.ceiling) —
        # an unset MODEL_NAME must not silently resolve to an 8B+ model.
        self.model_name = os.getenv("MODEL_NAME",
                                     "Qwen/Qwen2.5-3B-Instruct")

    def complete(self, prompt: str, max_tokens: int = 512,
                 temperature: float = 0.7,
                 top_p: Optional[float] = None,
                 *, system: Optional[str] = None,
                 messages: Optional[list] = None) -> ModelResponse:
        if system:
            prompt = f"{system}\n\n{prompt}"
        start = time.time()
        body: dict = {"prompt": prompt, "max_tokens": max_tokens,
                      "temperature": temperature}
        if top_p is not None:
            body["top_p"] = top_p
        resp = self._session.post(
            f"http://localhost:{self.port}/v1/completions",
            json=body,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return ModelResponse(
            text=data["choices"][0]["text"],
            model=self.model_name,
            tokens_used=data.get("usage", {}).get("completion_tokens", 0),
            backend="cuda",
            latency_ms=(time.time() - start) * 1000,
            cost_usd=0.0,
        )

    def health_check(self) -> bool:
        try:
            r = self._session.get(f"http://localhost:{self.port}/health",
                                  timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def stream_complete(self, prompt: str, max_tokens: int = 512,
                        temperature: float = 0.7,
                        top_p: Optional[float] = None,
                        *, system: Optional[str] = None,
                        messages: Optional[list] = None) -> Iterator[StreamResult]:
        if system:
            prompt = f"{system}\n\n{prompt}"
        start = time.time()
        body: dict[str, Any] = {
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if top_p is not None:
            body["top_p"] = top_p

        full_text = ""
        tokens_used = 0
        with self._session.post(
            f"http://localhost:{self.port}/v1/completions",
            json=body,
            timeout=120,
            stream=True,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    data = __import__("json").loads(payload)
                except Exception:
                    continue
                choice = (data.get("choices") or [{}])[0]
                delta = choice.get("text") or ""
                if delta:
                    full_text += delta
                    yield delta
                usage = data.get("usage") or {}
                if usage.get("completion_tokens") is not None:
                    tokens_used = int(usage["completion_tokens"])

        yield ModelResponse(
            text=full_text,
            model=self.model_name,
            tokens_used=tokens_used or len(full_text.split()),
            backend="cuda",
            latency_ms=(time.time() - start) * 1000,
            cost_usd=0.0,
        )


# ---------------------------------------------------------------------------
# CPU  (llama.cpp via llama-cpp-python — any OS, no GPU)
# ---------------------------------------------------------------------------
class CPUBackend(BaseBackend):
    def __init__(self) -> None:
        try:
            from llama_cpp import Llama  # type: ignore[import-untyped]
        except ImportError:
            raise ImportError("Run: pip install llama-cpp-python")

        model_path = os.getenv("MODEL_NAME", "")
        models_dir = os.getenv("ARAIL_MODELS_DIR", "lab/models")
        # If MODEL_NAME isn't an absolute path, look inside models dir
        if not os.path.isabs(model_path):
            pathlib = __import__("pathlib")
            models_root = pathlib.Path(models_dir)
            requested_name = os.path.basename(model_path)
            requested_stem = requested_name.rsplit(".", 1)[0].lower()
            requested_dir = requested_name.lower()

            candidates = sorted(models_root.rglob("*.gguf"))
            preferred = [
                candidate for candidate in candidates
                if requested_stem and (
                    requested_stem in candidate.name.lower()
                    or requested_dir in str(candidate.parent).lower()
                )
            ]

            if preferred:
                model_path = str(preferred[0])
            elif candidates:
                model_path = str(candidates[0])
            else:
                raise FileNotFoundError(
                    f"No .gguf model found in {models_dir}. "
                    "Download one — see README.md"
                )
        self.model_name = os.path.basename(model_path)

        # Answering-model ceiling (single chokepoint — see
        # arail.registry.ceiling). This is the fix for the review finding
        # that CPUBackend would silently load "sorted(rglob('*.gguf'))[0]"
        # — an opaquely-named or oversized GGUF could become the answering
        # model with no notice. Read real params from the file itself so an
        # opaque filename can't dodge the check the way it dodges the
        # name-regex parser.
        from arail.registry.ceiling import resolve_answering_model
        resolve_answering_model(
            self.model_name, role="primary", backend="cpu", model_path=model_path,
        )

        # L3: resolve context window at load time. Admin/chat set-ctx persists
        # to ARAIL_MODEL_CTX_OVERRIDES; _resolve_ctx_override reads it.
        # Default 4096 is the historical hard-coded value (R2: unchanged when
        # no override is set). OOM risk is on the user — UI shows the hint.
        n_ctx = _resolve_ctx_override(self.model_name, default=4096)
        self.llm = Llama(model_path=model_path, n_ctx=n_ctx, verbose=False)

    def complete(self, prompt: str, max_tokens: int = 512,
                 temperature: float = 0.7,
                 top_p: Optional[float] = None,
                 *, system: Optional[str] = None,
                 messages: Optional[list] = None) -> ModelResponse:
        if system:
            prompt = f"{system}\n\n{prompt}"
        start = time.time()
        kwargs: dict = {"max_tokens": max_tokens, "temperature": temperature}
        if top_p is not None:
            kwargs["top_p"] = top_p
        out = self.llm(prompt, **kwargs)
        text = out["choices"][0]["text"]  # type: ignore[index]
        return ModelResponse(
            text=text,
            model=self.model_name,
            tokens_used=out.get("usage", {}).get("completion_tokens", 0),
            backend="cpu",
            latency_ms=(time.time() - start) * 1000,
            cost_usd=0.0,
        )

    def health_check(self) -> bool:
        try:
            r = self.complete("test", max_tokens=5)
            return len(r.text) > 0
        except Exception:
            return False


# ---------------------------------------------------------------------------
# HuggingFace Inference API  (cloud — free tier)
# ---------------------------------------------------------------------------
class HuggingFaceBackend(BaseBackend):
    def __init__(self) -> None:
        api_key = os.getenv("HUGGINGFACE_API_KEY")
        if not api_key:
            raise ValueError("HUGGINGFACE_API_KEY not set")
        from huggingface_hub import InferenceClient  # type: ignore[import-untyped]
        self.client = InferenceClient(api_key=api_key)
        self.model_name = os.getenv(
            "MODEL_NAME", "Qwen/Qwen3-8B"
        )

    def complete(self, prompt: str, max_tokens: int = 512,
                 temperature: float = 0.7,
                 top_p: Optional[float] = None,
                 *, system: Optional[str] = None,
                 messages: Optional[list] = None) -> ModelResponse:
        if system:
            prompt = f"{system}\n\n{prompt}"
        start = time.time()
        kwargs: dict = {
            "prompt": prompt, "max_new_tokens": max_tokens,
            "temperature": temperature, "model": self.model_name,
        }
        if top_p is not None:
            kwargs["top_p"] = top_p
        text = self.client.text_generation(**kwargs)
        return ModelResponse(
            text=text,
            model=self.model_name,
            tokens_used=len(prompt.split()) + len(text.split()),
            backend="huggingface",
            latency_ms=(time.time() - start) * 1000,
            cost_usd=0.0,
        )

    def health_check(self) -> bool:
        try:
            self.complete("test", max_tokens=5)
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# OpenRouter  (cloud — free tier, multiple models)
# ---------------------------------------------------------------------------
class OpenRouterBackend(BaseBackend):
    def __init__(self) -> None:
        self.api_key = os.getenv("OPENROUTER_API_KEY")
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY not set")
        import requests
        self._session = requests.Session()  # noqa-airgap: external host (openrouter.ai); post-guard Session — HTTPAdapter is monkeypatched at install_guard() time, egress guard applies
        self.model_name = os.getenv(
            "MODEL_NAME", "Qwen/Qwen3-8B"
        )

    def complete(self, prompt: str, max_tokens: int = 512,
                 temperature: float = 0.7,
                 top_p: Optional[float] = None,
                 *, system: Optional[str] = None,
                 messages: Optional[list] = None) -> ModelResponse:
        if system:
            prompt = f"{system}\n\n{prompt}"
        start = time.time()
        payload: dict = {"model": self.model_name,
                         "messages": [{"role": "user", "content": prompt}],
                         "temperature": temperature,
                         "max_tokens": max_tokens}
        if top_p is not None:
            payload["top_p"] = top_p
        resp = self._session.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return ModelResponse(
            text=data["choices"][0]["message"]["content"],
            model=self.model_name,
            tokens_used=data.get("usage", {}).get("completion_tokens", 0),
            backend="openrouter",
            latency_ms=(time.time() - start) * 1000,
            cost_usd=0.0,
        )

    def health_check(self) -> bool:
        try:
            self.complete("test", max_tokens=5)
            return True
        except Exception:
            return False

    def stream_complete(self, prompt: str, max_tokens: int = 512,
                        temperature: float = 0.7,
                        top_p: Optional[float] = None,
                        *, system: Optional[str] = None,
                        messages: Optional[list] = None) -> Iterator[StreamResult]:
        if system:
            prompt = f"{system}\n\n{prompt}"
        start = time.time()
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if top_p is not None:
            payload["top_p"] = top_p

        full_text = ""
        tokens_used = 0
        with self._session.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            json=payload,
            timeout=120,
            stream=True,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload_line = line[5:].strip()
                if payload_line == "[DONE]":
                    break
                try:
                    data = __import__("json").loads(payload_line)
                except Exception:
                    continue
                choice = (data.get("choices") or [{}])[0]
                delta = (((choice.get("delta") or {}).get("content"))
                         or choice.get("text")
                         or "")
                if delta:
                    full_text += delta
                    yield delta
                usage = data.get("usage") or {}
                if usage.get("completion_tokens") is not None:
                    tokens_used = int(usage["completion_tokens"])

        yield ModelResponse(
            text=full_text,
            model=self.model_name,
            tokens_used=tokens_used or len(full_text.split()),
            backend="openrouter",
            latency_ms=(time.time() - start) * 1000,
            cost_usd=0.0,
        )


# ---------------------------------------------------------------------------
# Anthropic prompt-caching helpers (claude backend only)
# ---------------------------------------------------------------------------

def _min_cacheable_prefix_tokens(model: str) -> int:
    """Minimum prefix size (tokens) Anthropic will cache for *model*.

    Prompt caching silently no-ops below this floor (no error, just
    ``cache_creation_input_tokens: 0``). Floors are model-family specific;
    unknown models fall back to the highest floor so we never *assume* a
    write will land. Most-specific families are checked first.
    """
    m = model.lower()
    if "opus-4" in m:
        return 4096
    if "haiku-4-5" in m or "haiku-4.5" in m:
        return 4096
    if "sonnet-4-6" in m or "sonnet-4.6" in m:
        return 2048
    if "haiku-3" in m:  # haiku 3 / 3.5
        return 2048
    if "sonnet-4" in m or "3-7-sonnet" in m:  # sonnet 4 / 4.5 / 3.7
        return 1024
    return 4096  # unknown → conservative


def _prefix_is_cacheable(text: str, model: str) -> bool:
    """True when *text* is large enough to actually cache on *model*.

    Token count is estimated as chars/4. Returning False means we skip the
    ``cache_control`` marker entirely — below the floor it would be a
    silent no-op anyway, so this just makes the decision explicit/testable.
    """
    approx_tokens = len(text) // 4
    return approx_tokens >= _min_cacheable_prefix_tokens(model)


def _anthropic_supports_cache(anthropic_mod: Any) -> bool:
    """True when the installed SDK supports header-free block-level
    ``cache_control`` on ``messages.create()`` (GA since anthropic 0.34.0)."""
    raw = getattr(anthropic_mod, "__version__", "0") or "0"
    try:
        parts = raw.split(".")
        major, minor = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return False
    return (major, minor) >= (0, 34)


# ---------------------------------------------------------------------------
# Anthropic Claude  (cloud — paid)
# ---------------------------------------------------------------------------
class ClaudeBackend(BaseBackend):
    def __init__(self) -> None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")
        import anthropic  # type: ignore[import-untyped]
        self.client = anthropic.Anthropic(api_key=api_key)
        # Modernized default (2026-05): claude-sonnet-4-20250514 retires
        # 2026-06-15. Sonnet 4.6 is the current same-tier model (1M context).
        # Its prompt-cache floor is 2048 tokens — see _min_cacheable_prefix_tokens.
        self.model_name = os.getenv("MODEL_NAME", "claude-sonnet-4-6")
        self._supports_cache = _anthropic_supports_cache(anthropic)

    @staticmethod
    def _with_message_cache_breakpoint(msgs: list) -> list:
        """Return a shallow copy of *msgs* with a ``cache_control`` breakpoint
        on the last turn's final content block.

        This is the multi-turn breakpoint: it caches the whole
        ``tools + system + messages`` prefix up to here, so the *next* request
        (where this turn is now history) reads it instead of reprocessing.
        Only used for the chat path; the researcher's single synthesized turn
        doesn't repeat, so it isn't marked.
        """
        if not msgs:
            return list(msgs)
        out = list(msgs)
        last = dict(out[-1])
        content = last.get("content")
        marker = {"type": "ephemeral"}
        if isinstance(content, str):
            last["content"] = [{
                "type": "text", "text": content, "cache_control": marker,
            }]
        elif isinstance(content, list) and content:
            blocks = [dict(b) if isinstance(b, dict) else b for b in content]
            if isinstance(blocks[-1], dict):
                blocks[-1]["cache_control"] = marker
            last["content"] = blocks
        else:
            return out  # nothing markable
        out[-1] = last
        return out

    def complete(self, prompt: str, max_tokens: int = 512,
                 temperature: float = 0.7,
                 top_p: Optional[float] = None,
                 *, system: Optional[str] = None,
                 messages: Optional[list] = None) -> ModelResponse:
        start = time.time()
        if messages is not None:
            # Chat multi-turn: breakpoint on the last turn so a growing
            # conversation reuses its [system + history] prefix next request.
            msgs = (self._with_message_cache_breakpoint(messages)
                    if self._supports_cache and messages else list(messages))
        else:
            # Legacy / researcher path: single user message from the flat prompt.
            msgs = [{"role": "user", "content": prompt}]
        kwargs: dict = {
            "model": self.model_name,
            "max_tokens": max_tokens,
            "messages": msgs,
        }
        # Claude 4+ rejects temperature AND top_p together (400). Send at most
        # one: honor an explicit top_p (set by dashboard presets), else
        # temperature. Historic code sent both, a latent 400 on preset+Claude.
        if top_p is not None:
            kwargs["top_p"] = top_p
        else:
            kwargs["temperature"] = temperature
        if system:
            if self._supports_cache and _prefix_is_cacheable(system, self.model_name):
                # Cached prefix: breakpoint on the frozen system block. Any byte
                # change here invalidates the cache — keep it volatile-free.
                kwargs["system"] = [{
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }]
            else:
                # Below the model's floor or an old SDK → plain system string
                # (caching would silently no-op; don't pretend it's active).
                kwargs["system"] = system
        resp = self.client.messages.create(**kwargs)
        usage = resp.usage
        # Defensive text extraction. `max_tokens: 0` cache-prewarm requests
        # return content=[]; thinking blocks may precede text blocks too.
        text = ""
        for block in (resp.content or []):
            if getattr(block, "type", None) == "text":
                text = block.text
                break
        return ModelResponse(
            text=text,
            model=self.model_name,
            tokens_used=resp.usage.output_tokens,
            backend="claude",
            latency_ms=(time.time() - start) * 1000,
            cost_usd=None,
            cache_read_input_tokens=int(
                getattr(usage, "cache_read_input_tokens", 0) or 0),
            cache_creation_input_tokens=int(
                getattr(usage, "cache_creation_input_tokens", 0) or 0),
        )

    def health_check(self) -> bool:
        try:
            self.complete("test", max_tokens=5)
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# OpenAI-compatible local server  (LM Studio / Ollama / DeployLM)
# ---------------------------------------------------------------------------
class OpenAICompatBackend(BaseBackend):
    """Talks to any server that exposes the OpenAI /v1/chat/completions
    endpoint on localhost.  Works with LM Studio, Ollama, DeployLM, etc."""

    def __init__(self) -> None:
        import requests
        self._session = requests.Session()  # noqa-airgap: localhost-only (MODEL_API_BASE defaults to localhost:1234); post-guard Session — HTTPAdapter is monkeypatched at install_guard() time
        self.base_url = os.getenv("MODEL_API_BASE",
                                   "http://localhost:1234/v1").rstrip("/")
        self.model_name = os.getenv("MODEL_NAME", "default")
        self.api_key = os.getenv("MODEL_API_KEY", "not-needed")

    def complete(self, prompt: str, max_tokens: int = 512,
                 temperature: float = 0.7,
                 top_p: Optional[float] = None,
                 *, system: Optional[str] = None,
                 messages: Optional[list] = None) -> ModelResponse:
        if system:
            prompt = f"{system}\n\n{prompt}"
        start = time.time()
        payload: dict = {"model": self.model_name,
                         "messages": [{"role": "user", "content": prompt}],
                         "temperature": temperature,
                         "max_tokens": max_tokens}
        if top_p is not None:
            payload["top_p"] = top_p
        resp = self._session.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        return ModelResponse(
            text=data["choices"][0]["message"]["content"],
            model=data.get("model", self.model_name),
            tokens_used=data.get("usage", {}).get("completion_tokens", 0),
            backend="openai_compat",
            latency_ms=(time.time() - start) * 1000,
            cost_usd=0.0,
        )

    def health_check(self) -> bool:
        try:
            r = self._session.get(f"{self.base_url}/models", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def stream_complete(self, prompt: str, max_tokens: int = 512,
                        temperature: float = 0.7,
                        top_p: Optional[float] = None,
                        *, system: Optional[str] = None,
                        messages: Optional[list] = None) -> Iterator[StreamResult]:
        if system:
            prompt = f"{system}\n\n{prompt}"
        start = time.time()
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if top_p is not None:
            payload["top_p"] = top_p

        full_text = ""
        tokens_used = 0
        with self._session.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            json=payload,
            timeout=120,
            stream=True,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload_line = line[5:].strip()
                if payload_line == "[DONE]":
                    break
                try:
                    data = __import__("json").loads(payload_line)
                except Exception:
                    continue
                choice = (data.get("choices") or [{}])[0]
                delta = (((choice.get("delta") or {}).get("content"))
                         or choice.get("text")
                         or "")
                if delta:
                    full_text += delta
                    yield delta
                usage = data.get("usage") or {}
                if usage.get("completion_tokens") is not None:
                    tokens_used = int(usage["completion_tokens"])

        yield ModelResponse(
            text=full_text,
            model=self.model_name,
            tokens_used=tokens_used or len(full_text.split()),
            backend="openai_compat",
            latency_ms=(time.time() - start) * 1000,
            cost_usd=0.0,
        )


# ---------------------------------------------------------------------------
# AirLLM  (layer-streaming baseline — 70B on constrained hardware)
# ---------------------------------------------------------------------------
class AirLLMWorkerError(RuntimeError):
    """The AirLLM subprocess died, timed out, or refused to answer.

    Carries a short tail of the worker's stderr so the chat surface
    can show *why* (Metal GPU timeout, OOM, missing weights, …)
    instead of a bare ``RuntimeError``."""


class AirLLMBackend(BaseBackend):
    """Run AirLLM out-of-process so a Metal command-buffer timeout
    (which throws a C++ ``std::runtime_error`` and aborts the whole
    interpreter) only kills the worker — the lab portal stays alive
    and respawns the worker on the next call.

    Same ``complete()`` signature as before, so the router and chat
    paths don't change. The worker script is
    :mod:`arail.router.airllm_worker`; protocol is newline-delimited
    JSON on stdio. See that module's docstring for message shapes."""

    # Defensive cap so a runaway worker can't feed the parent an
    # unbounded line. Llama-3.1-70B replies max out well below this.
    _MAX_LINE_BYTES = 16 * 1024 * 1024

    def __init__(self) -> None:
        # Surface a clean ImportError early if airllm isn't installed
        # at all, rather than waiting for a worker spawn to fail.
        try:
            import airllm  # type: ignore  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "AirLLM not installed. Run: pip install airllm"
            ) from exc

        # TODO(deep-model): set the 20–30B open deep model id here. See ARCHITECTURE
        #   sprint 2026-05-30-model-hosting-reframe § Part 1. Until set, deep mode
        #   shows a "configure your deep model" notice — it does NOT download anything.
        _sentinel = "__TODO_DEEP_MODEL__"
        self.model_name = os.getenv("AIRLLM_MODEL", _sentinel)
        if self.model_name == _sentinel:
            raise RuntimeError(
                "Deep model is not configured. Set AIRLLM_MODEL in .env to a concrete "
                "model id (e.g. a 20–30B GGUF you have downloaded) and restart the lab. "
                "See NOTICE and sprints/2026-05-30-model-hosting-reframe/ARCHITECTURE.md "
                "§ Part 1 for guidance."
            )
        # Operators on slow disks can stretch the load deadline.
        # Call timeout covers the longest 512-token gen we've seen.
        self._call_timeout_s = float(os.getenv("AIRLLM_CALL_TIMEOUT_S", "300"))
        self._load_timeout_s = float(os.getenv("AIRLLM_LOAD_TIMEOUT_S", "1200"))
        self._stderr_tail = ""
        self._proc: subprocess.Popen | None = None
        self._spawn_lock = threading.Lock()
        self._call_lock = threading.Lock()

        # Eager spawn keeps the first chat call from paying the full
        # load cost — mirrors the prior in-process behavior. Set
        # ``AIRLLM_LAZY_SPAWN=1`` to defer load until first complete().
        if os.getenv("AIRLLM_LAZY_SPAWN", "").strip().lower() not in {"1", "true", "yes"}:
            self._ensure_worker()
        atexit.register(self._shutdown)

    # ── Worker lifecycle ──────────────────────────────────────────────

    def _worker_alive(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    def _ensure_worker(self) -> None:
        with self._spawn_lock:
            if self._worker_alive():
                return
            self._teardown_locked()  # drop a dead handle if any.

            cmd = [sys.executable, "-u", "-m", "arail.router.airllm_worker"]
            # Inherit env so HF_TOKEN, AIRLLM_MODEL, AIRLLM_COMPRESSION,
            # ARAIL_MODELS_DIR, etc. propagate without copy paste.
            self._proc = subprocess.Popen(  # noqa: S603
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=os.environ.copy(),
                bufsize=0,
            )

            # Wait for the ready handshake. A "fatal" message means the
            # worker imported airllm but couldn't load the weights.
            line = self._read_line(self._load_timeout_s)
            if line is None:
                tail = self._drain_stderr(2.0)
                rc = self._proc.poll() if self._proc is not None else None
                self._teardown_locked()
                hint = f"exit={rc}" if rc is not None else "no ready signal"
                raise AirLLMWorkerError(
                    f"AirLLM worker did not start ({hint}). "
                    f"Last stderr: {tail[-800:]!r}"
                )
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as e:
                self._teardown_locked()
                raise AirLLMWorkerError(
                    f"AirLLM worker emitted non-JSON on startup: {line!r} ({e})"
                )
            if msg.get("type") == "fatal":
                err = msg.get("error", "unknown")
                self._teardown_locked()
                raise AirLLMWorkerError(f"AirLLM worker failed to load: {err}")
            if msg.get("type") != "ready":
                self._teardown_locked()
                raise AirLLMWorkerError(
                    f"AirLLM worker sent unexpected first message: {msg!r}"
                )
            # Use whatever model id the worker actually loaded.
            self.model_name = msg.get("model") or self.model_name

    def _teardown_locked(self) -> None:
        """Kill any existing worker handle. Caller holds ``_spawn_lock``."""
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        for closer in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if closer is not None:
                    closer.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=2.0)
        except Exception:  # noqa: BLE001
            pass

    def _shutdown(self) -> None:
        """atexit hook — best-effort worker cleanup on portal exit."""
        with self._spawn_lock:
            self._teardown_locked()

    # ── Pipe I/O ──────────────────────────────────────────────────────

    def _read_line(self, timeout_s: float) -> str | None:
        """Read one newline-terminated JSON line from worker stdout.

        Returns the line (without newline) or ``None`` on timeout / EOF.
        Raises :class:`AirLLMWorkerError` on oversize lines (defensive
        — prevents a runaway worker from consuming unbounded memory)."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return None
        sel = selectors.DefaultSelector()
        sel.register(proc.stdout, selectors.EVENT_READ)
        deadline = time.time() + timeout_s
        buf = bytearray()
        try:
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                events = sel.select(timeout=remaining)
                if not events:
                    return None
                try:
                    chunk = os.read(proc.stdout.fileno(), 65536)
                except OSError:
                    return None
                if not chunk:
                    return None  # EOF — worker died
                buf.extend(chunk)
                nl = buf.find(b"\n")
                if nl != -1:
                    return buf[:nl].decode("utf-8", errors="replace")
                if len(buf) > self._MAX_LINE_BYTES:
                    raise AirLLMWorkerError(
                        f"AirLLM worker emitted oversized line "
                        f"(>{self._MAX_LINE_BYTES} bytes) — pipe corrupt"
                    )
        finally:
            try:
                sel.unregister(proc.stdout)
            except Exception:  # noqa: BLE001
                pass
            sel.close()

    def _drain_stderr(self, timeout_s: float) -> str:
        """Best-effort tail of worker stderr for the diagnostic surface."""
        proc = self._proc
        if proc is None or proc.stderr is None:
            return self._stderr_tail
        sel = selectors.DefaultSelector()
        sel.register(proc.stderr, selectors.EVENT_READ)
        out = bytearray()
        deadline = time.time() + timeout_s
        try:
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                events = sel.select(timeout=remaining)
                if not events:
                    break
                try:
                    chunk = os.read(proc.stderr.fileno(), 65536)
                except OSError:
                    break
                if not chunk:
                    break
                out.extend(chunk)
                if len(out) > 64 * 1024:
                    break
        finally:
            try:
                sel.unregister(proc.stderr)
            except Exception:  # noqa: BLE001
                pass
            sel.close()
        if out:
            self._stderr_tail = (
                self._stderr_tail + out.decode("utf-8", errors="replace")
            )[-8000:]
        return self._stderr_tail

    # ── Public surface ────────────────────────────────────────────────

    def complete(self, prompt: str, max_tokens: int = 512,
                 temperature: float = 0.7,
                 top_p: Optional[float] = None,
                 *, system: Optional[str] = None,
                 messages: Optional[list] = None) -> ModelResponse:
        if system:
            prompt = f"{system}\n\n{prompt}"
        # Runtime profile cap: 'interactive' clamps long generations so
        # the layer-streaming path doesn't lock up the lab when the
        # operator is here. See arail.runtime_profile.
        try:
            from arail.runtime_profile import params, resolve
            cap = params(resolve()[0])["airllm_max_tokens_cap"]
            max_tokens = min(max_tokens, cap)
        except Exception:  # noqa: BLE001
            pass  # Profile module is optional at this layer.

        # Serialize concurrent callers — AirLLM's MLX path is single-
        # threaded on the GPU, and the stdio pipe is one channel.
        with self._call_lock:
            self._ensure_worker()
            assert self._proc is not None and self._proc.stdin is not None

            req_id = uuid.uuid4().hex
            payload = json.dumps({
                "id": req_id,
                "type": "complete",
                "prompt": prompt,
                "max_tokens": int(max_tokens),
                "temperature": float(temperature),
                "top_p": top_p,
            }) + "\n"

            try:
                self._proc.stdin.write(payload.encode("utf-8"))
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                tail = self._drain_stderr(1.0)
                with self._spawn_lock:
                    self._teardown_locked()
                raise AirLLMWorkerError(
                    f"AirLLM worker pipe closed before request: {e}. "
                    f"Last stderr: {tail[-600:]!r}"
                )

            line = self._read_line(self._call_timeout_s)
            if line is None:
                # Either timeout or worker died mid-call. Distinguish
                # via exit code so the message is honest.
                rc = self._proc.poll() if self._proc is not None else None
                tail = self._drain_stderr(1.0)
                with self._spawn_lock:
                    self._teardown_locked()
                if rc is None:
                    raise AirLLMWorkerError(
                        f"AirLLM call exceeded {self._call_timeout_s:.0f}s "
                        f"(worker still alive but unresponsive — killed). "
                        f"Last stderr: {tail[-600:]!r}"
                    )
                raise AirLLMWorkerError(
                    f"AirLLM worker died mid-call (exit={rc}). "
                    f"Likely a Metal GPU timeout — see stderr for the "
                    f"libc++abi trace. Tail: {tail[-800:]!r}"
                )

            try:
                msg = json.loads(line)
            except json.JSONDecodeError as e:
                with self._spawn_lock:
                    self._teardown_locked()
                raise AirLLMWorkerError(
                    f"AirLLM worker bad response: {e}; line={line!r}"
                )

            if msg.get("id") != req_id:
                # Out-of-order responses shouldn't happen with serial
                # request/response — treat as protocol corruption.
                with self._spawn_lock:
                    self._teardown_locked()
                raise AirLLMWorkerError(
                    f"AirLLM worker response id mismatch: "
                    f"want {req_id!r} got {msg.get('id')!r}"
                )

            if not msg.get("ok"):
                # In-worker error (caught Python exception). The worker
                # is still alive and serviceable for the next call.
                raise AirLLMWorkerError(
                    f"AirLLM call failed: {msg.get('error', 'unknown')}"
                )

            return ModelResponse(
                text=str(msg.get("text", "")),
                model=str(msg.get("model", self.model_name)),
                tokens_used=int(msg.get("tokens", 0)),
                backend="airllm",
                latency_ms=float(msg.get("latency_ms", 0.0)),
                cost_usd=0.0,
            )

    def health_check(self) -> bool:
        return self._worker_alive()


# ---------------------------------------------------------------------------
# AeroLLM KV-budget resolution
# ---------------------------------------------------------------------------

# 2 GiB. Below this, a 7B-class model's KV pool can't hold a useful
# context window (a single 4K-token Qwen sequence at 4-bit ~= 0.5 GiB;
# we want headroom for 2-4 concurrent sequences plus prefill scratch).
# Set as a floor so a transiently-busy box (e.g., during a Chrome spike)
# still gets a working model after the spike passes — better to risk
# light swap than to ship a runtime that refuses to start.
_AEROLLM_KV_MIN_FLOOR_BYTES: int = 2 * 1024 * 1024 * 1024

# 1.5 GiB. Reserved on top of the .available reading. Rationale: on
# Darwin .available already discounts inactive/cached, but it does NOT
# reserve room for (a) the portal's own growth during the same request
# that triggered backend construction, (b) the aerollm Runtime's own
# non-KV resident set (~150 MB on top of the weight file), or (c) the
# spec-decode draft when AEROLLM_DRAFT_MODEL is set. 1.5 GiB covers
# all three with margin on a 16 GB Mac without leaving a 36 GB box
# significantly under-utilized.
_AEROLLM_KV_SAFETY_HEADROOM_BYTES: int = int(1.5 * 1024 * 1024 * 1024)

# Apply 85% of .available rather than 100% — even after subtracting
# SAFETY_HEADROOM we want a buffer for short-lived allocations
# (browser tab open, file upload) that the operator should not have
# to think about. The two knobs compose: AVAILABLE_FRACTION absorbs
# *transient* spikes, SAFETY_HEADROOM absorbs *known* costs.
_AEROLLM_KV_AVAILABLE_FRACTION: float = 0.85

_AEROLLM_KV_PCT_DEFAULT: float = 0.60


def _resolve_kv_budget() -> dict[str, Any]:
    """Compute the kv_memory_budget bytes to pass to aerollm Runtime.

    Returns
    -------
    dict with keys:
        budget_bytes : int | None
            Bytes to pass as kv_memory_budget, or None to let aerollm
            auto-detect (psutil missing / total reads as 0).
        reason : str
            One-line human-readable summary for activity_log.
        fields : dict[str, Any]
            Structured detail (pct_used, total_gib, available_gib,
            ceil_total_gib, ceil_available_gib, floor_gib, headroom_gib,
            source: "env"|"default"|"floor"|"unavailable").
    """
    _gib = 1024 ** 3

    # --- resolve pct from env ---
    pct_raw = os.getenv("AEROLLM_KV_BUDGET_PCT", "").strip()
    source_pct = "default"
    pct = _AEROLLM_KV_PCT_DEFAULT
    invalid_env_note: Optional[str] = None
    if pct_raw:
        try:
            parsed = float(pct_raw)
        except ValueError:
            parsed = 0.0
            invalid_env_note = f"non-numeric AEROLLM_KV_BUDGET_PCT={pct_raw!r}"
        if 0.0 < parsed < 1.0:
            pct = parsed
            source_pct = "env"
        else:
            # parsed is 0.0 from ValueError path or out-of-range — keep default
            if invalid_env_note is None:
                invalid_env_note = f"out-of-range AEROLLM_KV_BUDGET_PCT={pct_raw!r}"

    # --- read memory ---
    try:
        import psutil  # noqa: PLC0415 — intentionally lazy
        vm = psutil.virtual_memory()
        total: int = int(vm.total)
        available: int = int(vm.available)
    except Exception as exc:  # noqa: BLE001
        reason = (
            f"psutil unavailable ({exc}); aerollm will auto-detect KV budget"
        )
        return {
            "budget_bytes": None,
            "reason": reason,
            "fields": {
                "pct_used": pct,
                "total_gib": None,
                "available_gib": None,
                "ceil_total_gib": None,
                "ceil_available_gib": None,
                "floor_gib": _AEROLLM_KV_MIN_FLOOR_BYTES / _gib,
                "headroom_gib": _AEROLLM_KV_SAFETY_HEADROOM_BYTES / _gib,
                "source": "unavailable",
            },
        }

    if total == 0:
        return {
            "budget_bytes": None,
            "reason": "psutil returned total=0; aerollm will auto-detect KV budget",
            "fields": {
                "pct_used": pct,
                "total_gib": 0.0,
                "available_gib": available / _gib,
                "ceil_total_gib": 0.0,
                "ceil_available_gib": None,
                "floor_gib": _AEROLLM_KV_MIN_FLOOR_BYTES / _gib,
                "headroom_gib": _AEROLLM_KV_SAFETY_HEADROOM_BYTES / _gib,
                "source": "unavailable",
            },
        }

    # --- apply formula ---
    ceil_total = total * pct
    ceil_available = available * _AEROLLM_KV_AVAILABLE_FRACTION - _AEROLLM_KV_SAFETY_HEADROOM_BYTES
    raw_budget = min(ceil_total, ceil_available)

    source: str
    if raw_budget < _AEROLLM_KV_MIN_FLOOR_BYTES:
        budget_bytes = _AEROLLM_KV_MIN_FLOOR_BYTES
        source = "floor"
    else:
        budget_bytes = int(raw_budget)
        source = source_pct  # "env" or "default"

    notes = []
    if invalid_env_note:
        notes.append(f"ignored invalid env: {invalid_env_note}; using default {_AEROLLM_KV_PCT_DEFAULT}")
    notes_str = "; ".join(notes)

    budget_gib = budget_bytes / _gib
    reason = (
        f"KV budget resolved to {budget_gib:.2f} GiB"
        f" (source={source}, total={total/_gib:.1f} GiB,"
        f" available={available/_gib:.1f} GiB)"
        + (f"; {notes_str}" if notes_str else "")
    )

    return {
        "budget_bytes": budget_bytes,
        "reason": reason,
        "fields": {
            "pct_used": pct,
            "total_gib": total / _gib,
            "available_gib": available / _gib,
            "ceil_total_gib": ceil_total / _gib,
            "ceil_available_gib": ceil_available / _gib,
            "floor_gib": _AEROLLM_KV_MIN_FLOOR_BYTES / _gib,
            "headroom_gib": _AEROLLM_KV_SAFETY_HEADROOM_BYTES / _gib,
            "source": source,
        },
    }


# ---------------------------------------------------------------------------
# AeroLLM  (in-process Rust runtime via the aerollm_api PyO3 wheel)
# ---------------------------------------------------------------------------

# Fed to the model's own chat template as the system turn. Deliberately
# family-agnostic prose — the template decides how it gets rendered.
_AEROLLM_SYSTEM_PROMPT = "You are a concise, helpful assistant."


class AeroLLMBackend(BaseBackend):
    """Drive the AeroLLM Rust runtime through its `aerollm_api` PyO3 wheel.

    The wheel is built from the local sibling repo ``$ARAIL_AEROLLM_REPO``
    (``./arailctl deep rebuild``). Apple Silicon uses the in-process
    ``mlx-native`` backend; on other hosts the wheel falls back to the
    legacy subprocess shim (``mlx``).

    Default model: ``Qwen2.5-7B-Instruct`` resolved against
    ``ARAIL_MODELS_DIR`` (so a local checkpoint at
    ``$ARAIL_MODELS_DIR/Qwen2.5-7B-Instruct`` is picked up
    automatically). Set ``AEROLLM_MODEL`` to override the directory
    name.

    Threading: ``aerollm_api.Runtime`` is unsendable (MLX Metal context
    is thread-affine). All Runtime ops are pinned to a dedicated
    single-worker ``ThreadPoolExecutor`` so FastAPI's threadpool
    dispatch can hop into and out of this backend without touching
    the underlying handle from a foreign thread. Process shutdown
    may print one ``RuntimeError: ... unsendable, but is being
    dropped on another thread`` to stderr — that's Python's GC
    ordering interacting with our atexit handler, not a runtime bug.
    """

    # Process-wide shared instances, keyed by resolved model. Both the chat
    # path and the agents construct AeroLLMBackend(); without sharing, each
    # would load a second copy of the multi-GB weights and OOM the box.
    # __new__ hands back the existing *initialized* instance for the same model
    # so there is exactly ONE resident Runtime per model per process.
    _shared: "dict[str, AeroLLMBackend]" = {}

    @staticmethod
    def _cache_key() -> str:
        model = os.getenv("AEROLLM_MODEL", "Qwen2.5-7B-Instruct-4bit")
        models_dir = os.getenv("ARAIL_MODELS_DIR", "lab/models")
        return f"{models_dir}::{model}"

    def __new__(cls) -> "AeroLLMBackend":
        key = cls._cache_key()
        inst = cls._shared.get(key)
        if inst is not None and getattr(inst, "_initialized", False):
            return inst
        # First construction (or a prior attempt failed before finishing):
        # build a fresh instance and let __init__ run. Replacing a failed
        # instance lets a retry succeed after the model is downloaded.
        inst = super().__new__(cls)
        cls._shared[key] = inst
        return inst

    def __init__(self) -> None:
        # __new__ may hand back an already-built shared instance; don't reload.
        if getattr(self, "_initialized", False):
            return
        try:
            import aerollm_api as _aero_mod  # type: ignore[import-untyped]
            from aerollm_api import Runtime  # type: ignore[import-untyped]
        except ImportError as e:
            raise ImportError(
                "aerollm_api not installed. Try, in order: "
                "`./arailctl deep install` (fetches a checksummed prebuilt binary "
                "from an ARAIL GitHub Release — no source repo or credentials "
                "needed; this is the route for most people). Maintainer-only "
                "alternatives: `./arailctl deep rebuild` (build from the local "
                "sibling repo — set ARAIL_AEROLLM_REPO if it's not at "
                "~/ProJects/qukaizen-queuellm) or `./arailctl deep update` (pip "
                "install the published wheel from the private index, needs "
                "credentials). Do NOT use `maturin develop` — see scripts/setup.sh "
                "for why."
            ) from e
        # Surface the wheel version for `deep status` / health (best-effort).
        self.api_version = getattr(_aero_mod, "__version__", "unknown")

        # Default model picks the 4-bit MLX quant — ~4 GB resident, fits a
        # 16 GB Apple Silicon Mac with ~6 GB headroom for portal + browser.
        # Operators upgrade by setting AEROLLM_MODEL (max tier ships with
        # Qwen2.5-72B-Instruct-4bit, ~40 GB resident, requires 48 GB+;
        # populated by setup.sh capture_tier).
        self.model_name = os.getenv(
            "AEROLLM_MODEL", "Qwen2.5-7B-Instruct-4bit"
        )
        models_dir = os.getenv("ARAIL_MODELS_DIR", "lab/models")
        # Accept either a bare directory name (resolved against
        # ARAIL_MODELS_DIR) or an absolute path. The bare-name form is
        # the common case for the chat catalog; the absolute path
        # exists for ad-hoc operator use.
        if os.path.isabs(self.model_name):
            model_path = self.model_name
        else:
            model_path = os.path.join(models_dir, self.model_name)

        if not os.path.isdir(model_path):
            # Suggest the matching huggingface-cli download for whichever
            # default the operator landed on. The mlx-community/...-4bit
            # repo IDs are the canonical 4-bit MLX conversions.
            hf_repo = "mlx-community/" + self.model_name
            raise RuntimeError(
                f"AeroLLM model dir not found: {model_path}. "
                f"Set AEROLLM_MODEL and/or ARAIL_MODELS_DIR, or "
                f"download the checkpoint with `huggingface-cli download "
                f"{hf_repo} --local-dir {model_path}`."
            )

        # Optional speculative-decoding draft. When AEROLLM_DRAFT_MODEL
        # is set and points at a directory under ARAIL_MODELS_DIR, the
        # runtime preloads a second backend; complete() does not yet
        # surface the spec-decode flag (chat path stays single-seq for
        # simplicity), but having the draft resident means a future
        # toggle costs only a kwarg flip, not a reload.
        draft_name = os.getenv("AEROLLM_DRAFT_MODEL")
        draft_path: Optional[str] = None
        if draft_name:
            cand = draft_name if os.path.isabs(draft_name) else os.path.join(models_dir, draft_name)
            if os.path.isdir(cand):
                draft_path = cand

        rt_kwargs: dict[str, Any] = {}
        if draft_path:
            rt_kwargs["draft_model"] = draft_path

        # Ring depth (sprints/2026-08-11-two-slot-chat-models Part 4):
        # AEROLLM_RING_DEPTH, if set at all, always wins — including "0" or
        # an unparseable value, both of which mean "no eviction" per the
        # existing contract (aerollm's own default: every block resident).
        # Only when the env var is entirely absent does the runtime profile
        # (interactive/balanced/throughput — see arail.runtime_profile)
        # supply a value; profile changes only take effect on the NEXT
        # Runtime construction, since ring_depth is construction-time-only.
        ring_depth_env = os.getenv("AEROLLM_RING_DEPTH")
        ring_depth_source: Optional[str] = None
        if ring_depth_env is not None:
            ring_depth_source = "env"
            if ring_depth_env.isdigit() and int(ring_depth_env) > 0:
                rt_kwargs["ring_depth"] = int(ring_depth_env)
        else:
            try:
                from arail.runtime_profile import params as _rt_params
                from arail.runtime_profile import resolve as _rt_resolve
                _profile, _ = _rt_resolve()
                _depth = _rt_params(_profile).get("aerollm_ring_depth")
                ring_depth_source = f"profile:{_profile}"
                if isinstance(_depth, int) and _depth > 0:
                    rt_kwargs["ring_depth"] = _depth
            except Exception:  # noqa: BLE001
                ring_depth_source = None

        # KV cache budget — cap by *available* RAM, not just total, so a
        # busy box (Ollama + Chrome + portal already loaded) doesn't drive
        # the runtime past real headroom into swap. _resolve_kv_budget()
        # reads AEROLLM_KV_BUDGET_PCT as a ceiling against total RAM, then
        # clamps by (available * 0.85 - 1.5 GiB). Falls back to aerollm's
        # own auto-detect (budget_bytes=None) only if psutil is unavailable.
        reasoning = _resolve_kv_budget()
        if reasoning["budget_bytes"] is not None:
            rt_kwargs["kv_memory_budget"] = reasoning["budget_bytes"]
        self._emit_budget_activity(reasoning)

        # `aerollm_batch` is NOT a construction-time Runtime kwarg (unlike
        # ring_depth) — it belongs on a per-call path into complete(),
        # which doesn't accept a batch parameter today. Wiring that is a
        # separate, larger change (every complete() call site); out of
        # scope here. ring_depth is recorded for the chat UI's deep-slot
        # display (sprints/2026-08-11-two-slot-chat-models Part 4/5).
        self._ring_depth = rt_kwargs.get("ring_depth")
        self._ring_depth_source = ring_depth_source

        self._model_path = model_path
        self._draft_path = draft_path

        # The checkpoint's own tokenizer drives prompt wrapping and output
        # stripping. Without it we'd assume one chat family for every model
        # (see _wrap_prompt) — the F-1 bug: ChatML tags from the wrong chat
        # family injected into a checkpoint produce garbage. None is a supported
        # state: _wrap_prompt falls back to the historic ChatML wrap.
        self._tokenizer: Any = self._load_tokenizer(model_path)

        # The aerollm_api Runtime is unsendable: PyO3 panics if the handle
        # is touched from a thread other than the one that constructed
        # it (MLX's Metal context is thread-affine). FastAPI's
        # `run_in_threadpool` dispatches deep_backend.complete() onto a
        # worker thread, so we'd hit the panic on the second chat turn.
        # Pin the Runtime to a dedicated single-worker executor: the
        # constructor builds it on the worker, and every complete()
        # call routes back through the same worker. The cost is one
        # extra thread-hop per call (~µs); the benefit is a runtime
        # that survives FastAPI's threading model unchanged.
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="aerollm-rt"
        )
        self._runtime: Any = None  # populated on the worker thread
        self._closed = False
        self._executor.submit(self._init_runtime, Runtime, model_path, rt_kwargs).result()
        # Drop the Runtime on the worker thread at process exit. Without
        # this, Python's GC can drop the unsendable handle from the main
        # thread and PyO3 raises RuntimeError during shutdown.
        atexit.register(self._close)
        # Mark fully constructed last — __new__ reuses only initialized
        # instances, so a failure above leaves this instance replaceable.
        self._initialized = True

    def _init_runtime(self, runtime_cls: Any, model_path: str,
                      rt_kwargs: dict[str, Any]) -> None:
        """Construct + start the Runtime. Must run on the executor
        worker thread so the Metal context is bound to it."""
        rt = runtime_cls(model_path, **rt_kwargs)
        rt.start()
        self._runtime = rt

    def _emit_budget_activity(self, reasoning: dict[str, Any]) -> None:
        """Emit one activity_log entry describing the resolved KV budget.

        Best-effort — import errors are swallowed so a headless test
        harness without the activity bus still works. Emits at ``"warn"``
        when ``source in {"floor", "unavailable"}`` (operator should see
        these), ``"info"`` otherwise.
        """
        try:
            from arail.activity import activity_log  # noqa: PLC0415 — intentionally lazy
        except ImportError:
            return
        source = reasoning["fields"].get("source", "unavailable")
        level = "warn" if source in {"floor", "unavailable"} else "info"
        try:
            activity_log.emit("aerollm", reasoning["reason"], level=level)
        except Exception as e:  # noqa: BLE001
            _log.warning("activity_log emission failed: %s", e)

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            # Drop the Runtime on the worker thread it was built on.
            self._executor.submit(self._drop_runtime_on_worker).result(timeout=10)
        except Exception:
            pass
        try:
            self._executor.shutdown(wait=True, cancel_futures=True)
        except Exception:
            pass

    def _drop_runtime_on_worker(self) -> None:
        self._runtime = None

    @staticmethod
    def _load_tokenizer(model_path: str) -> Any:
        """Load the checkpoint's own tokenizer, or None if unavailable.

        Imported lazily: transformers drags in torch, which is heavy and
        pointless on hosts that never touch the deep path. Any failure —
        transformers absent, a checkpoint it can't read — is non-fatal;
        callers degrade to the legacy ChatML wrap rather than refusing to
        serve."""
        try:
            from transformers import AutoTokenizer  # type: ignore[import-untyped]
            return AutoTokenizer.from_pretrained(model_path)
        except Exception:
            return None

    @staticmethod
    def _template_tokens(tok: Any) -> list[str]:
        """This tokenizer's turn/template markers.

        bos/pad/unk are excluded deliberately: the tokenizer adds them at
        encode time, so they never appear in an authored prompt, and
        stripping them from output would corrupt text that legitimately
        contains them. eos IS included — it's a turn marker and must be
        stripped from the continuation."""
        toks = getattr(tok, "all_special_tokens", None) or []
        skip = {
            getattr(tok, attr, None)
            for attr in ("bos_token", "pad_token", "unk_token")
        }
        return [t for t in toks if t and t not in skip]

    def _wrap_prompt(self, prompt: str) -> str:
        """Wrap a bare prompt using the model's OWN chat template.

        The whole point of F-1: never assume a chat family. Four cases:
          - no tokenizer      -> legacy Qwen2.5 ChatML (historic behaviour)
          - already wrapped   -> pass through, don't double-wrap
          - has a template    -> apply_chat_template, whatever family it is
          - loads, no template-> send raw; guessing a family is what broke
        """
        tok = self._tokenizer
        if tok is None:
            return self._wrap_chatml(prompt)

        # Built upstream (e.g. by apply_chat_template) — leave it alone.
        if any(t in prompt for t in self._template_tokens(tok)):
            return prompt

        messages = [
            {"role": "system", "content": _AEROLLM_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        try:
            return tok.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
        except Exception:
            # No chat_template on this tokenizer. Send the prompt through
            # unwrapped — a base/completion model wants exactly that, and
            # slapping on a borrowed family is the bug we're fixing.
            return prompt

    def _strip_special(self, text: str, wrapped: str) -> str:
        """Strip echoed prompt + this model's turn markers from output.

        Generic over the tokenizer's own tokens — hardcoding <|im_end|>
        left harmony's <|return|>/<|message|> in the user's face."""
        if text.startswith(wrapped):
            text = text[len(wrapped):]
        tok = self._tokenizer
        if tok is None:
            return text.replace("<|im_end|>", "").strip()
        for t in self._template_tokens(tok):
            text = text.replace(t, "")
        return text.strip()

    @staticmethod
    def _wrap_chatml(prompt: str) -> str:
        """Legacy Qwen2.5 ChatML wrap — the fallback when no tokenizer is
        available. Prefer _wrap_prompt(), which honours the model's own
        template; this hardcodes one family by construction and is only
        correct for ChatML checkpoints."""
        if "<|im_start|>" in prompt:
            return prompt
        return (
            f"<|im_start|>system\n{_AEROLLM_SYSTEM_PROMPT}<|im_end|>\n"
            f"<|im_start|>user\n{prompt}<|im_end|>\n"
            "<|im_start|>assistant\n"
        )

    def _generate_on_worker(self, wrapped: str,
                            gen_kwargs: dict[str, Any]) -> str:
        """Run on the executor worker. Holds the unsendable invariant."""
        return self._runtime.generate(wrapped, **gen_kwargs)

    def complete(self, prompt: str, max_tokens: int = 512,
                 temperature: float = 0.7,
                 top_p: Optional[float] = None,
                 *, system: Optional[str] = None,
                 messages: Optional[list] = None) -> ModelResponse:
        if system:
            prompt = f"{system}\n\n{prompt}"
        start = time.time()
        wrapped = self._wrap_prompt(prompt)

        gen_kwargs: dict[str, Any] = {
            "max_new_tokens": max_tokens,
            "temperature": float(temperature),
        }
        if top_p is not None:
            gen_kwargs["top_p"] = float(top_p)

        # Hop to the dedicated runtime thread; .result() blocks until
        # it returns. If the worker raises, .result() re-raises here.
        text = self._executor.submit(
            self._generate_on_worker, wrapped, gen_kwargs
        ).result()

        # The runtime returns just the decoded continuation, but it may
        # carry trailing turn markers; strip them using the model's own
        # special tokens. Older shim paths sometimes echo the prompt —
        # strip that defensively too.
        text = self._strip_special(text, wrapped)

        # aerollm_api doesn't surface output-token count yet (planned in
        # a follow-up via generate_with_stats). Approximate with a
        # word-count fallback to match the AirLLMBackend style — good
        # enough for the dashboard's tok/min headline; the criterion
        # bench is the authoritative source for real tok/s numbers.
        tokens_used = max(len(text.split()), 0)

        return ModelResponse(
            text=text,
            model=self.model_name,
            tokens_used=tokens_used,
            backend="aerollm",
            latency_ms=(time.time() - start) * 1000,
            cost_usd=0.0,
        )

    def health_check(self) -> bool:
        return getattr(self, "_runtime", None) is not None


# ---------------------------------------------------------------------------
# OllamaNativeBackend  (Ollama native /api/chat — carries options.num_ctx)
# ---------------------------------------------------------------------------
def _normalize_keep_alive(value: str) -> "str | int":
    """Coerce a keep_alive setting into something Ollama actually accepts.

    Ollama reads a JSON *string* as a Go duration and a JSON *number* as
    seconds. "5m" and 300 are both fine; a bare "-1" is not — Go rejects
    it with `time: missing unit in duration "-1"` and the request 400s.
    Operators reasonably write ARAIL_OLLAMA_KEEP_ALIVE=-1 meaning
    "forever", so turn any bare integer into the number form and leave
    real durations ("2h", "30s") untouched.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


class OllamaNativeBackend(OpenAICompatBackend):
    """Talks to Ollama's NATIVE /api/chat endpoint (not the OpenAI /v1 shim).

    Ollama's OpenAI-compat shim at /v1/chat/completions silently ignores the
    `num_ctx` option (F-OLLAMA-SHIM). The native endpoint at /api/chat accepts
    options.num_ctx correctly and reloads the model KV cache accordingly.

    Construction: two supported paths.
      1. ``__new__`` + explicit attributes (the chat gallery's runtime-override
         path — F-NEW: the caller must set all required attributes including
         _num_ctx). complete() reads _num_ctx defensively via
         getattr(self, '_num_ctx', None) to guard against a partial build.
      2. Plain construction via ``ModelRouter()`` / ``BACKEND_MAP`` — handled
         by ``__init__`` below. Historically this inherited
         OpenAICompatBackend.__init__'s LM Studio default (localhost:1234),
         which silently broke every env-configured consumer (AutoResearch,
         agents) when MODEL_API_BASE was unset. __init__ now defaults to the
         actual Ollama port (OLLAMA_PORT, 11434); an explicit MODEL_API_BASE
         still wins.

    The root URL is derived by stripping a trailing '/v1' from self.base_url.
    """

    def __init__(self) -> None:
        super().__init__()
        # MODEL_API_BASE (explicit) wins; otherwise talk to Ollama itself,
        # never the inherited LM Studio :1234 default.
        if not os.getenv("MODEL_API_BASE"):
            port = os.getenv("OLLAMA_PORT", "11434")
            self.base_url = f"http://127.0.0.1:{port}/v1"
        self.backend_name = "ollama:native"
        self._num_ctx = _resolve_ctx_override(self.model_name, default=None)

    def _keep_alive(self) -> "str | int | None":
        """Ollama keep_alive for every request.

        Without it Ollama evicts the model ~5 min after the last call, so
        the "resident" Tier 0 quietly went cold between interactions.

        Resolution order:
          1. ARAIL_OLLAMA_KEEP_ALIVE, if set at all (including "" — omit
             keep_alive entirely, falling back to Ollama's own default).
             An explicit operator override always wins.
          2. "-1" (pin forever) when ARAIL_RESIDENT_PIN is truthy
             (default on) AND this instance's model is the registry's
             tier0/resident entry — the "in the GPU at all times" story
             the two-slot redesign (sprints/2026-08-11-two-slot-chat-
             models Part 3) promises for the resident slot specifically.
          3. "2h" otherwise (any other model, or pin disabled).

        Pinning forever used to be rejected as a *default* because it
        fights the aeroLLM preload's 0.60 memory-pressure ceiling and the
        0.75 chat guard on 16 GB boxes — but that objection assumed an
        arbitrarily large resident model. The resident slot the picker
        offers by default is capped at <=3B (never past the <8B primary
        ceiling), so pinned residency is ~1-2 GB: pinned tier0 (~2 GB) +
        a resident aeroLLM 7B (~4 GB) is still well under 0.60 * 16 GB.
        Only the model that's actually the registry's tier0 pins — every
        other Ollama model (picked ad hoc from the rail, or used as a
        one-off) keeps the old 2h behavior. ARAIL_RESIDENT_PIN=0 restores
        the pre-Part-3 default everywhere.
        """
        raw = os.getenv("ARAIL_OLLAMA_KEEP_ALIVE")
        if raw is not None:
            value = raw.strip()
            if not value:
                return None
            return _normalize_keep_alive(value)
        pin_enabled = os.getenv("ARAIL_RESIDENT_PIN", "1").strip().lower() \
            not in ("0", "false", "no")
        if pin_enabled and self._is_registry_tier0_model():
            # int, not "-1". Ollama parses a STRING keep_alive as a Go
            # duration, and "-1" has no unit — it answers 400
            # {"error":"time: missing unit in duration \"-1\""} and the
            # whole call fails. A JSON *number* is seconds, and negative
            # means "keep loaded indefinitely", which is what pinning
            # means here.
            return -1
        return "2h"

    def _is_registry_tier0_model(self) -> bool:
        """True iff this backend's model IS the registry's tier0/resident
        entry. Best-effort: any registry hiccup (unloaded, corrupt file,
        import failure) falls back to False — a broken registry must
        never silently pin the wrong model forever, it should just leave
        keep_alive at the old, safe 2h default."""
        try:
            from arail.registry import get_registry
            from arail.registry.store import TIER0_ID
            reg = get_registry()
            reg._ensure_loaded()
            entry = reg.entries.get(TIER0_ID)
            if entry is None or entry.backend != "ollama_native":
                return False
            return self.model_name in (entry.model_id, f"{entry.model_id}:latest")
        except Exception:  # noqa: BLE001
            return False

    def _ollama_root(self) -> str:
        """Return the Ollama root URL (strip trailing /v1 from base_url)."""
        base = self.base_url.rstrip("/")
        if base.endswith("/v1"):
            return base[:-3]
        return base

    def complete(self, prompt: str, max_tokens: int = 512,
                 temperature: float = 0.7,
                 top_p: Optional[float] = None,
                 *, system: Optional[str] = None,
                 messages: Optional[list] = None,
                 think: Optional[bool] = None) -> ModelResponse:
        if system:
            prompt = f"{system}\n\n{prompt}"
        start = time.time()
        num_ctx = getattr(self, "_num_ctx", None)  # F-NEW: defensive read

        body: dict = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        # options.num_ctx / num_predict only included when set (else Ollama
        # uses its own defaults). num_predict caps *visible* output only —
        # reasoning models (e.g. gemma4) still burn an unbounded number of
        # hidden "thinking" tokens first, so a capped call can still run for
        # minutes. `think` (below) is what actually bounds that.
        options: dict = {}
        if num_ctx is not None:
            options["num_ctx"] = int(num_ctx)
        if max_tokens is not None:
            options["num_predict"] = int(max_tokens)
        if options:
            body["options"] = options
        # think=False disables the hidden reasoning pass entirely (Ollama
        # >=0.32 native /api/chat option). Callers that just need a fast,
        # bounded call — e.g. the chat UI's warm-up ping — pass think=False
        # explicitly; real chat turns leave it unset so reasoning models
        # keep their thinking traces.
        if think is not None:
            body["think"] = think
        keep_alive = self._keep_alive()
        if keep_alive is not None:
            body["keep_alive"] = keep_alive

        resp = self._session.post(
            f"{self._ollama_root()}/api/chat",   # F-OLLAMA-SHIM: native endpoint
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            json=body,
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        # Ollama native response shape: {message: {role, content}, done: bool}
        text = (data.get("message") or {}).get("content") or ""
        return ModelResponse(
            text=text,
            model=data.get("model", self.model_name),
            tokens_used=data.get("eval_count", 0) or len(text.split()),
            backend="ollama_native",
            latency_ms=(time.time() - start) * 1000,
            cost_usd=0.0,
        )

    def stream_complete(self, prompt: str, max_tokens: int = 512,
                        temperature: float = 0.7,
                        top_p: Optional[float] = None,
                        *, system: Optional[str] = None,
                        messages: Optional[list] = None,
                        think: Optional[bool] = None) -> Iterator[StreamResult]:
        """Symmetry with OpenAICompatBackend; chat path only calls complete().
        Streams via /api/chat with stream:true, yields deltas then ModelResponse."""
        if system:
            prompt = f"{system}\n\n{prompt}"
        start = time.time()
        num_ctx = getattr(self, "_num_ctx", None)

        body: dict[str, Any] = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        }
        # See complete()'s comments: num_predict must be forwarded or Ollama
        # ignores max_tokens; think=False is what actually bounds hidden
        # reasoning tokens for models like gemma4.
        options: dict[str, Any] = {}
        if num_ctx is not None:
            options["num_ctx"] = int(num_ctx)
        if max_tokens is not None:
            options["num_predict"] = int(max_tokens)
        if options:
            body["options"] = options
        if think is not None:
            body["think"] = think
        keep_alive = self._keep_alive()
        if keep_alive is not None:
            body["keep_alive"] = keep_alive

        full_text = ""
        eval_count = 0
        with self._session.post(
            f"{self._ollama_root()}/api/chat",
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            json=body,
            timeout=120,
            stream=True,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                delta = (data.get("message") or {}).get("content") or ""
                if delta:
                    full_text += delta
                    yield delta
                if data.get("eval_count"):
                    eval_count = int(data["eval_count"])
                if data.get("done"):
                    break

        yield ModelResponse(
            text=full_text,
            model=self.model_name,
            tokens_used=eval_count or len(full_text.split()),
            backend="ollama_native",
            latency_ms=(time.time() - start) * 1000,
            cost_usd=0.0,
        )


# ---------------------------------------------------------------------------
# Registry used by ModelRouter
# ---------------------------------------------------------------------------
BACKEND_MAP: dict[str, type[BaseBackend]] = {
    "mlx": MLXBackend,
    "cuda": CUDABackend,
    "cpu": CPUBackend,
    "airllm": AirLLMBackend,
    "openai_compat": OpenAICompatBackend,
    "huggingface": HuggingFaceBackend,
    "openrouter": OpenRouterBackend,
    "claude": ClaudeBackend,
    "aerollm": AeroLLMBackend,
    "ollama_native": OllamaNativeBackend,  # L3 — native /api/chat with options.num_ctx
}
