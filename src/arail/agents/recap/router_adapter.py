"""RouterAdapter — wraps ModelRouter with multi-turn message history support.

Flattens a list of ``{"role": ..., "content": ...}`` dicts into a single
prompt string for backends that only accept a flat string (all current
ARAIL backends). Enforces sliding-window K=64 and §A truncation before
each call. Threads ``recap_depth`` into ``cost_tracker.track()`` via the
ContextVar set by ``recap_depth_context``.

Architecture note (from ARCHITECTURE.md):
  Do NOT modify ModelRouter. Use this wrapper so non-ReCAP callers are
  never affected.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, List, Optional

from arail import agent_context
from arail.costs import recap_depth_context
from arail.agents.recap.state import truncate_for_context, window

if TYPE_CHECKING:
    from arail.router.core import ModelRouter

# Default budget: 6000 chars ≈ 1500 tokens (configurable via env var).
_DEFAULT_PROMPT_BUDGET: int = int(os.getenv("RECAP_PROMPT_TOKEN_BUDGET", "6000"))

# Flatten role separator tokens
_ROLE_TAGS = {
    "system": "<<SYSTEM>>",
    "user": "<<USER>>",
    "assistant": "<<ASSISTANT>>",
}


def flatten_messages(messages: List[dict]) -> str:
    """Flatten a message list into a single role-tagged string.

    Format:
        <<SYSTEM>>
        {content}

        <<USER>>
        {content}

        ...
    """
    parts: List[str] = []
    for msg in messages:
        role = msg.get("role", "user")
        tag = _ROLE_TAGS.get(role, f"<<{role.upper()}>>")
        content = msg.get("content", "")
        parts.append(f"{tag}\n{content}")
    return "\n\n".join(parts)


class RouterAdapter:
    """Wraps ``ModelRouter`` with multi-turn chat and context management.

    Args:
        router:              A ``ModelRouter`` instance (or any object with
                             a ``.complete(prompt, max_tokens, temperature)``
                             method returning an object with ``.text``).
        max_tokens:          Default token budget for completions.
        temperature:         Default sampling temperature.
        prompt_token_budget: Char budget before §A truncation fires
                             (chars, not tokens; ~4 chars/token heuristic).
    """

    def __init__(
        self,
        router: "ModelRouter",
        *,
        max_tokens: int = 512,
        temperature: float = 0.7,
        prompt_token_budget: int = _DEFAULT_PROMPT_BUDGET,
    ) -> None:
        self.router = router
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.prompt_token_budget = prompt_token_budget

    def chat(
        self,
        messages: List[dict],
        *,
        depth: int = 0,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """Issue a multi-turn call to the underlying router.

        Steps:
        1. Apply sliding window K=64.
        2. Check prompt size; if over budget, apply §A truncation.
        3. Flatten to a single string.
        4. Call router.complete() inside recap_depth_context(depth).
        5. Return the response text.
        """
        # Step 1 — sliding window (K=64 by default in state.py)
        windowed = window(messages)

        # Step 2 — §A truncation if still over budget
        char_budget = self.prompt_token_budget * 4  # chars → rough tokens
        flat = flatten_messages(windowed)
        if len(flat) > char_budget:
            windowed = truncate_for_context(
                windowed,
                max_chars=char_budget,
                flatten_fn=flatten_messages,
            )
            flat = flatten_messages(windowed)

        # Step 4 — call inside depth context so cost_tracker picks it up.
        # L3 (ARCHITECTURE.md): recap is not itself an agent -- system_call
        # under a generic "recap" label, same shape as world-forge/
        # dictionary/goal-parser. No production caller constructs a
        # RouterAdapter today (this whole subsystem is dormant, like
        # _builtin_drafter.compose was before this sprint's guard), so
        # there is no existing attribution to clobber.
        with recap_depth_context(depth), agent_context.system_call("recap"):
            resp = self.router.complete(
                flat,
                max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
                temperature=temperature if temperature is not None else self.temperature,
            )
        return resp.text
