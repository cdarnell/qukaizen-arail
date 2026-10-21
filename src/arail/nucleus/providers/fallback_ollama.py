"""Eval-only Ollama fallback (ARCHITECTURE.md §4.7).

Used ONLY when importing the deep runtime's Python extension module
raises ``ImportError`` (see runtime_names.PY_MODULE), and only for
eval-only roles (judge, student/base generation in ``nucleus eval``) — a
local ``build`` with no QueueLLM refuses Phase A instead (no fallback
teacher; logit-mode KD has no meaning without top-N logprobs, which this
provider can never produce). ``capabilities().logprobs_topn`` is always
False.
"""

from __future__ import annotations

from typing import List

from arail.nucleus.providers.base import Caps, Decoding, Generation, Prompt, TopNGeneration

_OLLAMA_BASE = "http://127.0.0.1:11434"


class OllamaFallbackProvider:
    runtime = "ollama"

    def __init__(self, model: str, *, base_url: str = _OLLAMA_BASE, timeout: float = 120.0):
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self._closed = False

    def capabilities(self) -> Caps:
        return Caps(generate=True, logprobs_topn=False, max_top_n=0)

    def generate(self, prompts: List[Prompt], decoding: Decoding) -> List[Generation]:
        import requests

        out = []
        for p in prompts:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model, "prompt": p.text, "stream": False,
                    "options": {
                        "temperature": decoding.temperature, "top_p": decoding.top_p,
                        "num_predict": decoding.max_new_tokens, "seed": decoding.seed,
                        "stop": list(decoding.stop),
                    },
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            text = resp.json().get("response", "")
            out.append(Generation(item_id=p.item_id, text=text, token_ids=()))
        return out

    def generate_with_topn(self, prompts: List[Prompt], decoding: Decoding,
                           top_n: int) -> List[TopNGeneration]:
        raise NotImplementedError(
            "OllamaFallbackProvider has no logprobs_topn capability — check "
            "capabilities().logprobs_topn before calling this"
        )

    def close(self) -> None:
        self._closed = True
