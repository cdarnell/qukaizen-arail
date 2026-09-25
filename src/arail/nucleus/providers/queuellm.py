"""Worker-side provider backed by the deep local runtime (ARCHITECTURE.md
§4.7 / §4.1 N1).

Imports the frozen extension module name lazily and only via
``runtime_names.PY_MODULE`` — this file otherwise never spells it (T-RT-2).
Constructs its own ``Runtime`` directly (F9: the singleton, one-model-per-
process backend elsewhere in this repo has no model parameter and can't
serve three different models — teacher, judge, student — in one build), on
one pinned worker thread (the runtime's handle is not safely usable from
another thread once constructed — same rationale noted where the portal's
own deep-mode backend does this).

Nucleus sets no environment variables for this runtime; every knob is a
``Runtime(...)`` constructor kwarg (§4.1).
"""

from __future__ import annotations

import importlib
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Optional

from arail.nucleus import runtime_names
from arail.nucleus.errors import CapabilityMissing, DomainConfigError
from arail.nucleus.providers.base import Caps, Decoding, Generation, Prompt, TopNGeneration

_REFUSED_BACKENDS = {"mlx"}  # the shim backend id — never a real inference path


def _wrap_prompt(tokenizer, prompt: str) -> str:
    """Use the model's own chat template if the tokenizer has one; if not
    (or there's no tokenizer at all), send the prompt raw rather than
    guessing a chat family (the same "never assume a chat family" rule the
    portal's own deep-mode wrapper follows)."""
    if tokenizer is None:
        return prompt
    template = getattr(tokenizer, "chat_template", None)
    if not template:
        return prompt
    try:
        return tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}],
            tokenize=False, add_generation_prompt=True,
        )
    except Exception:  # noqa: BLE001 — a malformed template must never crash generation
        return prompt


class QueueLLMProvider:
    runtime = runtime_names.USER_RUNTIME

    def __init__(self, model_path: str, *, backend: str = "mlx-native",
                ring_depth: Optional[int] = None, kv_memory_budget: Optional[int] = None,
                run_dir: Optional[Path] = None, tokenizer=None):
        if backend in _REFUSED_BACKENDS:
            raise DomainConfigError(f"backend {backend!r} is a shim, not a real runtime — refused")

        self.model_path = model_path
        self.run_dir = Path(run_dir) if run_dir else None
        self._tokenizer = tokenizer
        self._module = importlib.import_module(runtime_names.PY_MODULE)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nucleus-rt")
        kwargs = {}
        if ring_depth is not None:
            kwargs["ring_depth"] = ring_depth
        if kv_memory_budget is not None:
            kwargs["kv_memory_budget"] = kv_memory_budget
        self._runtime = self._executor.submit(
            self._module.Runtime, model_path, backend=backend, **kwargs
        ).result()
        self._closed = False

    def provenance(self, *, features: Optional[list] = None) -> dict:
        return runtime_names.runtime_provenance(self._module, features=features)

    def capabilities(self) -> Caps:
        return Caps(generate=True, logprobs_topn=True, max_top_n=64)

    def probe_logprobs_capability(self) -> None:
        """A 1-token generate(logprobs=...) probe — call this from
        preflight before Phase A (§4.2 N2). Raises CapabilityMissing with
        the exact rebuild instruction on the shipped bundle's expected
        ValueError."""
        with tempfile.TemporaryDirectory() as tmp:
            probe_path = os.path.join(tmp, "probe.jsonl")
            try:
                self._call_runtime(
                    "generate", "probe", logprobs=probe_path, logprobs_top_n=1,
                    temperature=0, max_new_tokens=1, seed=0,
                )
            except ValueError as exc:
                if "unstable-api" in str(exc):
                    raise CapabilityMissing(
                        "logprobs is unstable per STABILITY.md; rebuild the deep "
                        "runtime with --features unstable-api (opt-in feature "
                        "passthrough, ARCHITECTURE.md §10 commit 28)"
                    ) from exc
                raise

    def _call_runtime(self, method: str, *args, **kwargs):
        return self._executor.submit(getattr(self._runtime, method), *args, **kwargs).result()

    def generate(self, prompts: List[Prompt], decoding: Decoding) -> List[Generation]:
        out = []
        for p in prompts:
            wrapped = _wrap_prompt(self._tokenizer, p.text)
            text = self._call_runtime(
                "generate", wrapped, temperature=decoding.temperature,
                top_p=decoding.top_p, max_new_tokens=decoding.max_new_tokens,
                seed=decoding.seed,
            )
            out.append(Generation(item_id=p.item_id, text=text, token_ids=()))
        return out

    def generate_with_topn(self, prompts: List[Prompt], decoding: Decoding,
                           top_n: int) -> List[TopNGeneration]:
        if self.run_dir is None:
            raise DomainConfigError("generate_with_topn requires run_dir (extract/tmp/ scratch space)")
        tmp_dir = self.run_dir / "extract" / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

        out = []
        for p in prompts:
            wrapped = _wrap_prompt(self._tokenizer, p.text)
            tmp_path = tmp_dir / f"{p.item_id}.jsonl"
            try:
                self._call_runtime(
                    "generate", wrapped, logprobs=str(tmp_path), logprobs_top_n=top_n,
                    temperature=0, max_new_tokens=decoding.max_new_tokens, seed=decoding.seed,
                )
                out.append(_parse_topn_jsonl(p.item_id, tmp_path))
            except ValueError as exc:
                if "unstable-api" in str(exc):
                    raise CapabilityMissing(
                        "logprobs is unstable per STABILITY.md; rebuild the deep "
                        "runtime with --features unstable-api"
                    ) from exc
                raise
            finally:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
        return out

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._executor.submit(getattr(self._runtime, "close", lambda: None)).result()
        except Exception:  # noqa: BLE001 — close() must never raise past shutdown
            pass
        self._executor.shutdown(wait=True)
        self._closed = True


def _parse_topn_jsonl(item_id: str, path: Path) -> TopNGeneration:
    token_ids: List[int] = []
    topn_ids: List[List[int]] = []
    topn_logprobs: List[List[float]] = []
    text_parts: List[str] = []
    if path.is_file():
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            token_ids.append(row.get("token_id", -1))
            topn_ids.append(row.get("topn_ids", []))
            topn_logprobs.append(row.get("topn_logprobs", []))
            if "text" in row:
                text_parts.append(row["text"])
    return TopNGeneration(item_id=item_id, text="".join(text_parts), token_ids=token_ids,
                          topn_ids=topn_ids, topn_logprobs=topn_logprobs)
