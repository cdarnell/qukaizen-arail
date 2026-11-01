#!/usr/bin/env python3
"""
Benchmark harness for model profiling.

Measures single-prompt and batched throughput (tokens/sec) for each model.
Outputs to lab/data/model_profiles.json for agent routing decisions.

Models are discovered, not hardcoded: the unified registry (lab/data/model_registry.json)
first, then whatever the local Ollama daemon has pulled, then HuggingFace-style dirs under
arail/models/.

Usage:
  python lab/tools/benchmark_models.py --list                   # what's here, and what's skippable
  python lab/tools/benchmark_models.py --model qwen2.5:7b       # substring match is fine
  python lab/tools/benchmark_models.py --all --local-only       # every free local model
  python lab/tools/benchmark_models.py --all --batch-size 1 10 50  # custom batch sizes
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic

# ── Logging ──────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


# ── Config ────────────────────────────────────────────────────────

LAB_ROOT = Path(__file__).parent.parent
ARAIL_ROOT = LAB_ROOT.parent
DATA_DIR = LAB_ROOT / "data"
# Respect ARAIL_MODELS_DIR (same env var arail.config.MODELS_DIR and every
# router/backends.py reader honor), defaulting to lab/models — NOT
# arail_root/models, which this constant claimed for years without ever
# matching where models actually get downloaded to (see arail.config:86,
# AeroLLMBackend.__init__ in router/backends.py). Benchmarking against the
# wrong directory silently found nothing to benchmark on a real lab.
MODELS_DIR = Path(os.getenv("ARAIL_MODELS_DIR") or (LAB_ROOT / "models"))
PROFILES_PATH = DATA_DIR / "model_profiles.json"
REGISTRY_PATH = DATA_DIR / "model_registry.json"  # unified model registry (source of truth)

# Ollama's OpenAI-compatible endpoint, used when the registry has no explicit one.
DEFAULT_OLLAMA_ENDPOINT = "http://127.0.0.1:11434/v1"

# Standard test prompt (measures both prompt + generation TPS)
TEST_PROMPT = """You are an AI research assistant. Analyze the following query and provide a concise, structured response.

Query: Explain the differences between MoE (Mixture of Experts) and dense transformer architectures in terms of inference efficiency and latency tradeoffs.

Provide your analysis in 200-300 tokens, structured as:
1. Throughput characteristics
2. Latency characteristics
3. When to use each

Be precise and educational."""

# Test params
DEFAULT_MAX_TOKENS = 256
DEFAULT_BATCH_SIZES = [1, 50]  # Single and full batch


@dataclass
class BenchmarkRun:
    """One timing measurement."""

    model: str
    backend: str  # "airllm" | "aerollm" | "anthropic" | "mlx"
    batch_size: int
    prompt_tokens: int
    output_tokens: int
    total_latency_ms: float
    tokens_per_sec: float  # output_tokens / (total_latency_ms / 1000)
    ts: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelBenchmark:
    """Benchmark a model via its configured backend."""

    def __init__(
        self,
        model_name: str,
        backend: str,
        endpoint: str | None = None,
        key_env: str | None = None,
    ):
        """
        Args:
            model_name: Model ID as the backend knows it (e.g. "ai-engineer:latest")
            backend: Backend type ("airllm", "anthropic", "openai_compat", ...)
            endpoint: Base URL for OpenAI-compatible backends
            key_env: Env var holding the API key, if the backend needs one
        """
        self.model_name = model_name
        self.backend = backend
        self.endpoint = endpoint
        self.key_env = key_env

    def benchmark(
        self,
        batch_size: int = 1,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> BenchmarkRun | None:
        """
        Run one benchmark iteration.

        Args:
            batch_size: How many prompts to batch (1 = single)
            max_tokens: Max output tokens

        Returns:
            BenchmarkRun with timing, or None on error.
        """
        try:
            if self.backend == "anthropic":
                return self._benchmark_anthropic(batch_size, max_tokens)
            elif self.backend == "airllm":
                return self._benchmark_airllm(batch_size, max_tokens)
            elif self.backend == "openai_compat":
                return self._benchmark_openai_compat(batch_size, max_tokens)
            elif self.backend == "aerollm":
                return self._benchmark_aerollm(batch_size, max_tokens)
            else:
                log.error(f"Unknown backend: {self.backend}")
                return None
        except Exception as e:
            log.error(f"Benchmark failed for {self.model_name} ({self.backend}): {e}")
            return None

    def _benchmark_anthropic(
        self,
        batch_size: int,
        max_tokens: int,
    ) -> BenchmarkRun | None:
        """Benchmark Claude via Anthropic API."""
        if not os.getenv("ANTHROPIC_API_KEY"):
            log.error("ANTHROPIC_API_KEY not set")
            return None

        # Map model alias to actual Claude model
        # Map model alias to a current Claude model. The previous targets
        # (claude-3-5-sonnet-20241022) retired 2026-02-19 and would 404.
        model_map = {
            "claude-opus": "claude-opus-4-7",
            "claude-sonnet": "claude-sonnet-4-6",
        }
        actual_model = model_map.get(self.model_name.lower(), self.model_name)

        client = anthropic.Anthropic()

        start_time = time.time()
        try:
            message = client.messages.create(
                model=actual_model,
                max_tokens=max_tokens,
                messages=[
                    {
                        "role": "user",
                        "content": TEST_PROMPT
                        * batch_size,  # Simple batching: repeat prompt
                    }
                ],
            )
            latency_ms = (time.time() - start_time) * 1000

            prompt_tokens = message.usage.input_tokens
            output_tokens = message.usage.output_tokens
            tps = output_tokens / (latency_ms / 1000) if latency_ms > 0 else 0

            return BenchmarkRun(
                model=self.model_name,
                backend="anthropic",
                batch_size=batch_size,
                prompt_tokens=prompt_tokens,
                output_tokens=output_tokens,
                total_latency_ms=latency_ms,
                tokens_per_sec=tps,
                ts=datetime.now(timezone.utc).isoformat(),
            )
        except anthropic.APIError as e:
            log.error(f"API error: {e}")
            return None

    def _benchmark_airllm(
        self,
        batch_size: int,
        max_tokens: int,
    ) -> BenchmarkRun | None:
        """Benchmark model via AirLLM (local layer-streaming)."""
        try:
            from transformers import pipeline
        except ImportError:
            log.error("Transformers not installed")
            return None

        try:
            # Use transformers pipeline for simpler API
            model_path = MODELS_DIR / self.model_name
            if not model_path.exists():
                log.error(f"Model not found at {model_path}")
                return None

            # Create a text generation pipeline
            pipe = pipeline(
                "text-generation",
                model=str(model_path),
                device_map="auto",
                trust_remote_code=True,
            )

            # Prepare prompts
            prompt = TEST_PROMPT * batch_size

            # Time generation
            start_time = time.time()
            outputs = pipe(
                prompt,
                max_new_tokens=max_tokens,
                return_full_text=False,
            )
            latency_ms = (time.time() - start_time) * 1000

            # Extract token counts (approximate from text)
            generated_text = outputs[0].get("generated_text", "")
            # Rough estimate: ~4 chars per token
            output_tokens = len(generated_text) // 4
            prompt_tokens = len(prompt) // 4

            tps = output_tokens / (latency_ms / 1000) if latency_ms > 0 else 0

            return BenchmarkRun(
                model=self.model_name,
                backend="airllm",
                batch_size=batch_size,
                prompt_tokens=prompt_tokens,
                output_tokens=output_tokens,
                total_latency_ms=latency_ms,
                tokens_per_sec=tps,
                ts=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as e:
            log.error(f"AirLLM benchmark error: {e}")
            return None

    def _benchmark_openai_compat(
        self,
        batch_size: int,
        max_tokens: int,
    ) -> BenchmarkRun | None:
        """Benchmark via an OpenAI-compatible /chat/completions endpoint.

        Covers Ollama (local, no key) and hosted gateways like xAI (key required).
        Uses urllib so the harness keeps working without the openai package.
        """
        endpoint = (self.endpoint or DEFAULT_OLLAMA_ENDPOINT).rstrip("/")

        headers = {"Content-Type": "application/json"}
        if self.key_env:
            api_key = os.getenv(self.key_env)
            if not api_key:
                log.error(f"{self.key_env} not set — skipping {self.model_name}")
                return None
            headers["Authorization"] = f"Bearer {api_key}"

        payload = json.dumps(
            {
                "model": self.model_name,
                "max_tokens": max_tokens,
                "stream": False,
                "messages": [{"role": "user", "content": TEST_PROMPT * batch_size}],
            }
        ).encode()

        req = urllib.request.Request(
            f"{endpoint}/chat/completions", data=payload, headers=headers
        )

        start_time = time.time()
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = e.read()[:200].decode(errors="replace")
            log.error(f"HTTP {e.code} from {endpoint}: {detail}")
            return None
        except OSError as e:
            log.error(f"Cannot reach {endpoint}: {e}")
            return None
        latency_ms = (time.time() - start_time) * 1000

        usage = body.get("usage") or {}
        output_tokens = usage.get("completion_tokens")
        prompt_tokens = usage.get("prompt_tokens")
        if output_tokens is None:
            # Some servers omit usage; fall back to a ~4 chars/token estimate.
            text = body["choices"][0]["message"].get("content", "")
            output_tokens = len(text) // 4
            prompt_tokens = len(TEST_PROMPT * batch_size) // 4

        tps = output_tokens / (latency_ms / 1000) if latency_ms > 0 else 0

        return BenchmarkRun(
            model=self.model_name,
            backend="openai_compat",
            batch_size=batch_size,
            prompt_tokens=prompt_tokens or 0,
            output_tokens=output_tokens,
            total_latency_ms=latency_ms,
            tokens_per_sec=tps,
            ts=datetime.now(timezone.utc).isoformat(),
        )

    def _benchmark_aerollm(
        self,
        batch_size: int,
        max_tokens: int,
    ) -> BenchmarkRun | None:
        """Benchmark model via QueueLLM (Rust runtime)."""
        # TODO: wire this once AeroLLM stable Rust API is available
        log.warning("QueueLLM benchmarking not yet implemented")
        return None


# ── Discovery ────────────────────────────────────────────────────


@dataclass
class Candidate:
    """A benchmarkable model discovered on this machine."""

    name: str  # model id as the backend knows it
    backend: str  # "openai_compat" | "anthropic" | "airllm" | "aerollm"
    endpoint: str | None = None
    key_env: str | None = None
    origin: str = "registry"  # where we found it, for --list

    def skip_reason(self) -> str | None:
        """Why this candidate can't run right now, or None if it can."""
        if self.key_env and not os.getenv(self.key_env):
            return f"{self.key_env} not set"
        if self.backend == "aerollm":
            return "QueueLLM backend not yet wired"
        if self.backend == "airllm" and not (MODELS_DIR / self.name).exists():
            return f"no model dir at {MODELS_DIR / self.name}"
        return None


def _backend_for(provider_type: str, backend: str) -> str:
    """Map a registry entry's provider/backend pair onto a benchmark backend."""
    if backend in ("ollama_native", "openai_compat") or provider_type in (
        "local",
        "xai",
        "gateway",
    ):
        return "openai_compat"
    if provider_type == "anthropic" or backend == "claude":
        return "anthropic"
    if provider_type == "aerollm" or backend == "aerollm":
        return "aerollm"
    return "airllm"


def discover_from_registry() -> list[Candidate]:
    """Read enabled entries out of the unified model registry."""
    if not REGISTRY_PATH.exists():
        log.warning(f"No registry at {REGISTRY_PATH}")
        return []
    try:
        entries = json.loads(REGISTRY_PATH.read_text()).get("entries", [])
    except Exception as e:
        log.error(f"Failed to read registry: {e}")
        return []

    out: list[Candidate] = []
    for entry in entries:
        if not entry.get("enabled", True):
            continue
        backend = _backend_for(
            entry.get("provider_type", ""), entry.get("backend", "")
        )
        out.append(
            Candidate(
                name=entry.get("model_id") or entry.get("display_name", ""),
                backend=backend,
                endpoint=entry.get("endpoint"),
                key_env=entry.get("key_env"),
                origin=f"registry:{entry.get('id')}",
            )
        )
    return out


def discover_from_ollama() -> list[Candidate]:
    """Ask the local Ollama daemon what it actually has pulled."""
    try:
        with urllib.request.urlopen(
            DEFAULT_OLLAMA_ENDPOINT.replace("/v1", "/api/tags"), timeout=3
        ) as resp:
            models = json.loads(resp.read()).get("models", [])
    except OSError:
        log.debug("Ollama not reachable; skipping local tag discovery")
        return []
    return [
        Candidate(
            name=m["name"],
            backend="openai_compat",
            endpoint=DEFAULT_OLLAMA_ENDPOINT,
            origin="ollama",
        )
        for m in models
        if m.get("name")
    ]


def discover_from_models_dir() -> list[Candidate]:
    """Find HuggingFace-style model dirs under arail/models/."""
    if not MODELS_DIR.exists():
        return []
    return [
        Candidate(name=d.name, backend="airllm", origin="models_dir")
        for d in sorted(MODELS_DIR.iterdir())
        if d.is_dir() and (d / "config.json").exists()
    ]


def discover_models(include_ollama: bool = True) -> list[Candidate]:
    """All benchmarkable models on this machine, registry first, deduped by name."""
    found = discover_from_registry()
    if include_ollama:
        found += discover_from_ollama()
    found += discover_from_models_dir()

    seen: set[str] = set()
    unique: list[Candidate] = []
    for c in found:
        if not c.name or c.name in seen:
            continue
        seen.add(c.name)
        unique.append(c)
    return unique


def resolve_model(query: str, candidates: list[Candidate]) -> Candidate | None:
    """Match a user-supplied name against discovered models (exact, then substring)."""
    q = query.lower()
    for c in candidates:
        if c.name.lower() == q:
            return c
    partial = [c for c in candidates if q in c.name.lower()]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        log.error(
            f"'{query}' is ambiguous: {', '.join(c.name for c in partial)}"
        )
    return None


# ── Model registry ──────────────────────────────────────────────

@dataclass
class ModelProfile:
    """Capability profile for agent routing."""

    model: str
    arch: str
    backend: str
    single_prompt_tps: float | None = None
    batched_tps: float | None = None  # Measured at batch_size=50
    context_window: int | None = None
    cost_per_1m_tokens: float = 0  # USD; 0 for local
    last_benchmarked: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_profiles() -> dict[str, ModelProfile]:
    """Load existing model_profiles.json."""
    if not PROFILES_PATH.exists():
        return {}
    try:
        data = json.loads(PROFILES_PATH.read_text())
        return {
            name: ModelProfile(**profile) for name, profile in data.items()
        }
    except Exception as e:
        log.error(f"Failed to load profiles: {e}")
        return {}


def save_profiles(profiles: dict[str, ModelProfile]) -> None:
    """Save profiles to model_profiles.json."""
    PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {name: profile.to_dict() for name, profile in profiles.items()}
    PROFILES_PATH.write_text(json.dumps(data, indent=2))
    log.info(f"Saved profiles to {PROFILES_PATH}")


# ── CLI ──────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark models for autoresearch routing decisions.",
    )
    parser.add_argument(
        "--model",
        type=str,
        help="Single model to benchmark (e.g., qwen-7b, claude-opus)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Benchmark all configured models",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        nargs="+",
        default=DEFAULT_BATCH_SIZES,
        help="Batch sizes to benchmark (default: 1 50)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_MAX_TOKENS,
        help=f"Max output tokens per run (default: {DEFAULT_MAX_TOKENS})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be benchmarked, don't run",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List discovered models (with skip reasons) and exit",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Benchmark only free local models (skip anything needing an API key)",
    )

    args = parser.parse_args()

    candidates = discover_models()

    if args.list:
        log.info(f"Discovered {len(candidates)} model(s):")
        for c in candidates:
            reason = c.skip_reason()
            status = f"SKIP ({reason})" if reason else "ready"
            log.info(f"  {c.name:<32} {c.backend:<14} [{c.origin}] {status}")
        return

    if not args.model and not args.all:
        parser.print_help()
        sys.exit(1)

    # Select what to benchmark from what's actually here
    if args.all:
        selected = candidates
    else:
        match = resolve_model(args.model, candidates)
        if not match:
            log.error(
                f"No model matching '{args.model}'. Run --list to see what's available."
            )
            sys.exit(1)
        selected = [match]

    if args.local_only:
        selected = [c for c in selected if not c.key_env]

    models_to_run: list[Candidate] = []
    for c in selected:
        reason = c.skip_reason()
        if reason:
            log.warning(f"Skipping {c.name}: {reason}")
            continue
        models_to_run.append(c)

    if not models_to_run:
        log.error("Nothing benchmarkable. Run --list to see why.")
        sys.exit(1)

    # Load existing profiles
    profiles = load_profiles()

    # Run benchmarks
    runs: list[BenchmarkRun] = []
    for cand in models_to_run:
        log.info(f"Benchmarking {cand.name} ({cand.backend}) [{cand.origin}]...")

        if args.dry_run:
            for batch_size in args.batch_size:
                log.info(f"  Would benchmark batch_size={batch_size}")
            continue

        benchmark = ModelBenchmark(
            cand.name, cand.backend, endpoint=cand.endpoint, key_env=cand.key_env
        )
        for batch_size in args.batch_size:
            log.info(f"  Running batch_size={batch_size}...")
            run = benchmark.benchmark(batch_size, args.max_tokens)
            if run:
                runs.append(run)
                log.info(
                    f"    {run.output_tokens} tokens in {run.total_latency_ms:.1f}ms "
                    f"= {run.tokens_per_sec:.2f} tok/s"
                )

    # Update profiles from runs
    for run in runs:
        profile_key = f"{run.model}_{run.backend}"
        if profile_key not in profiles:
            # Guess arch from model name
            arch = "qwen" if "qwen" in run.model.lower() else "llama"
            profiles[profile_key] = ModelProfile(
                model=run.model,
                arch=arch,
                backend=run.backend,
            )

        profile = profiles[profile_key]
        profile.last_benchmarked = run.ts

        # Store TPS by batch size
        if run.batch_size == 1:
            profile.single_prompt_tps = run.tokens_per_sec
        elif run.batch_size == 50:
            profile.batched_tps = run.tokens_per_sec

    if runs and not args.dry_run:
        save_profiles(profiles)
        log.info(f"Benchmarked {len(runs)} configurations")
        for run in runs:
            log.info(f"  {run.model} ({run.backend}) batch={run.batch_size}: {run.tokens_per_sec:.2f} tok/s")


if __name__ == "__main__":
    main()
