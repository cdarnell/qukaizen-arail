"""Provider protocol + capability/result shapes (ARCHITECTURE.md §4.7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Sequence


@dataclass(frozen=True)
class Prompt:
    item_id: str
    text: str
    role: str = ""   # "student" | "base_student" | "teacher" | "judge" — informational


@dataclass(frozen=True)
class Decoding:
    temperature: float = 0.0
    top_p: float = 1.0
    max_new_tokens: int = 512
    stop: Sequence[str] = field(default_factory=tuple)
    seed: int = 0


@dataclass(frozen=True)
class Generation:
    item_id: str
    text: str
    token_ids: Sequence[int] = field(default_factory=tuple)


@dataclass(frozen=True)
class TopNGeneration:
    item_id: str
    text: str
    token_ids: Sequence[int]                       # the realized/sampled token per step
    topn_ids: Sequence[Sequence[int]]               # per-step candidate ids, len == top_n (padded)
    topn_logprobs: Sequence[Sequence[float]]        # per-step candidate logprobs, same shape


@dataclass(frozen=True)
class Caps:
    generate: bool = True
    logprobs_topn: bool = False
    max_top_n: int = 0


class Provider(Protocol):
    runtime: str

    def capabilities(self) -> Caps: ...

    def generate(self, prompts: List[Prompt], decoding: Decoding) -> List[Generation]: ...

    def generate_with_topn(self, prompts: List[Prompt], decoding: Decoding,
                           top_n: int) -> List[TopNGeneration]: ...

    def close(self) -> None: ...
