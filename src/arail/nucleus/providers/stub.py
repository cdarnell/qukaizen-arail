"""Deterministic stub provider — the CI seam (ARCHITECTURE.md §4.7).

Enabled only when ``ARAIL_NUCLEUS_STUB=1`` (enforced by
``runtime_names.resolve_user_runtime`` — a domain.yaml can't select
``runtime: stub`` any other way). Every card a stub build touches must
read ``runtime: stub`` everywhere, sign with an ephemeral key, and never
be appended to any ledger (T-STUB-2) — this module only produces the
deterministic generations; the "never ledgered" half of that contract
lives in cards/certified_models.py (commit 21).
"""

from __future__ import annotations

import hashlib
import os
from typing import Dict, List, Optional

from arail.nucleus.errors import DomainConfigError
from arail.nucleus.providers.base import (
    Caps, Decoding, Generation, Prompt, TopNGeneration,
)

_DEFAULT_TOP_N = 20
_VOCAB_SIZE = 32000


def _require_stub_enabled() -> None:
    if os.getenv("ARAIL_NUCLEUS_STUB", "").strip() != "1":
        raise DomainConfigError(
            "the stub provider is only usable when ARAIL_NUCLEUS_STUB=1"
        )


def _deterministic_tokens(prompt_text: str, n: int, *, salt: str = "") -> List[int]:
    """Tokens derived from sha256(prompt) — stable across runs and
    processes without needing a real tokenizer."""
    digest = hashlib.sha256(f"{salt}:{prompt_text}".encode()).digest()
    out = []
    i = 0
    while len(out) < n:
        chunk = digest[(i % len(digest)):(i % len(digest)) + 2] or digest[:2]
        val = int.from_bytes((chunk + b"\x00\x00")[:2], "big") % _VOCAB_SIZE
        out.append(val)
        i += 2
    return out


def _deterministic_topn(prompt_text: str, step: int, top_n: int) -> tuple:
    ids = _deterministic_tokens(f"{prompt_text}#{step}", top_n, salt="topn")
    # Monotonically decreasing fake logprobs so renormalization/mass math
    # (evals/composite.py, train/kd_loss.py) has something realistic to
    # chew on: the sampled (first) id gets the highest logprob.
    logprobs = [-0.1 * (i + 1) for i in range(top_n)]
    return ids, logprobs


class StubProvider:
    """``answers`` maps ``(item_id, role)`` -> generated text. Anything not
    in the table falls back to a short deterministic placeholder so the
    stub never crashes on an unexpected item — it just produces an
    obviously-synthetic answer (fine for CI; item 10's real fixture table
    in commit 10 covers every item explicitly)."""

    runtime = "stub"

    def __init__(self, role: str = "", answers: Optional[Dict[tuple, str]] = None,
                quality: str = "good"):
        _require_stub_enabled()
        self.role = role
        self.answers = answers or {}
        self.quality = quality  # a marker the stub trainer can flip (commit 17)
        self._closed = False

    def capabilities(self) -> Caps:
        return Caps(generate=True, logprobs_topn=True, max_top_n=64)

    def _answer_for(self, prompt: Prompt) -> str:
        key = (prompt.item_id, prompt.role or self.role)
        if key in self.answers:
            return self.answers[key]
        return f"[stub:{self.quality}] deterministic answer for {prompt.item_id}"

    def generate(self, prompts: List[Prompt], decoding: Decoding) -> List[Generation]:
        out = []
        for p in prompts:
            text = self._answer_for(p)
            tokens = _deterministic_tokens(text, max(len(text.split()), 1))
            out.append(Generation(item_id=p.item_id, text=text, token_ids=tokens))
        return out

    def generate_with_topn(self, prompts: List[Prompt], decoding: Decoding,
                           top_n: int = _DEFAULT_TOP_N) -> List[TopNGeneration]:
        out = []
        for p in prompts:
            text = self._answer_for(p)
            n_steps = max(len(text.split()), 1)
            sampled = _deterministic_tokens(text, n_steps)
            topn_ids, topn_logprobs = [], []
            for step in range(n_steps):
                ids, logprobs = _deterministic_topn(text, step, top_n)
                # Ensure the sampled token is always inside its own top-N
                # (mirrors a real teacher: it generated the token it's
                # reporting candidates for).
                if sampled[step] not in ids:
                    ids[0] = sampled[step]
                topn_ids.append(ids)
                topn_logprobs.append(logprobs)
            out.append(TopNGeneration(item_id=p.item_id, text=text, token_ids=sampled,
                                      topn_ids=topn_ids, topn_logprobs=topn_logprobs))
        return out

    def close(self) -> None:
        self._closed = True


class StubJudge:
    """Deterministic preference per a fixture table:
    ``preferences[item_id] = "A" | "B"``. Anything absent defaults to "A"
    (still deterministic, just an explicit fallback)."""

    runtime = "stub"

    def __init__(self, preferences: Optional[Dict[str, str]] = None):
        _require_stub_enabled()
        self.preferences = preferences or {}

    def judge(self, item_id: str, text_a: str, text_b: str) -> str:
        return self.preferences.get(item_id, "A")


class StubTrainer:
    """Fake LoRA training: writes a marker file instead of running MLX,
    and returns a `dev_proxy_composite` from a deterministic quality curve
    (rises then plateaus) so arbitrage.py's stop rules are exercised for
    real in CI. `quality` is the marker StubProvider reads to decide which
    fixture answers the "student" role returns after this cycle — the
    seam that makes end-to-end metrics exact rationals instead of noise.
    """

    def __init__(self, adapter_root, *, curve: Optional[List[float]] = None):
        _require_stub_enabled()
        self.adapter_root = adapter_root
        # Default curve: improves for a few cycles, then plateaus below a
        # perfect score -- exercises both target_reached (if target is set
        # low enough) and fidelity_plateau_3_cycles (if not).
        self.curve = curve or [0.3, 0.5, 0.62, 0.68, 0.70, 0.70, 0.70, 0.70]

    def train_cycle(self, cycle_idx: int):
        from arail.nucleus.arbitrage import CycleResult

        idx = min(cycle_idx - 1, len(self.curve) - 1)
        proxy = self.curve[idx]
        adapter_dir = self.adapter_root / f"cycle-{cycle_idx}"
        adapter_dir.mkdir(parents=True, exist_ok=True)
        (adapter_dir / "adapter.marker").write_text(f"stub-adapter quality={proxy}\n")
        return CycleResult(cycle=cycle_idx, dev_proxy_composite=proxy,
                           adapter_path=str(adapter_dir), elapsed_hours=0.01)
