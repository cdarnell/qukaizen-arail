"""Drafter — composition agent.

    A drafter takes context + intent and returns text.
    It never sends. Sending is consent's job.

That's the whole mental model. Drafter is a synchronous,
request-driven agent — invoked by blueprints (inbox-triager,
client-followup) when they need a draft. No tick loop, no
heartbeat, no background ambitions.

This file mirrors the pattern of `lab/pkb/agents/pip/pip.py`:

    1. Personality   — system prompt that shapes every draft
    2. API           — compose(context, intent, voice, max_tokens)
    3. Result type   — Draft dataclass with requires_consent=True
    4. Singleton     — module-level `drafter` instance
    5. Tests         — tests/test_drafter.py with mocked router

Drafter does NOT inherit from any framework. ARAIL's v2 agent
framework (informed by PARL prior art) is roadmap; until then
agents are POPOs (plain old Python objects) like Buddy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional


log = logging.getLogger(__name__)


# ── Personality ──────────────────────────────────────────────────────
# The system prompt Drafter prepends to every LLM call. Concise,
# voice-aware, never auto-sends. Per-blueprint voice profiles will
# layer on top in v1.1.

_DRAFT_SYSTEM_PROMPT = """\
You are a drafter. Your job is to compose a single draft response
to the context provided, in the user's voice, matching the intent.

Rules:
  - Write the draft directly — no preamble, no commentary, no
    "Here's a draft:" lead-in. Just the message body.
  - Concise by default. Match the length of the context unless the
    intent says otherwise.
  - Professional tone unless the intent specifies otherwise.
  - Plain prose. No markdown headings, no bullet lists, unless the
    context itself is structured.
  - Never invent facts. If you need information you don't have,
    write "[need: <what>]" inline so the user can fill it in.
  - You are a drafter, not a sender. Do NOT include a closing line
    like "Sending now" or "Forwarding to ..." — sending is not
    your job.
"""

_VOICE_TEMPLATES = {
    # The default voice profile. Per-blueprint templates land here
    # as the inbox-triager and client-followup blueprints mature.
    "default": "Direct, professional, warm. First person. Active voice.",
}


# ── Result type ──────────────────────────────────────────────────────


@dataclass
class Draft:
    """A draft composed by Drafter. Always requires consent before send."""

    text: str
    requires_consent: bool = True
    tokens_in: int = 0
    model: str = ""
    voice: str = "default"
    metadata: dict[str, Any] = field(default_factory=dict)


# ── The agent ────────────────────────────────────────────────────────


class DrafterAgent:
    """Synchronous composition agent. Invoked by blueprints; no loop.

    Construction is cheap (no model load); the LLM is acquired
    lazily on the first compose() call to keep agent loading fast.
    """

    def __init__(self) -> None:
        self._router: Any = None  # ModelRouter — lazy

    @property
    def name(self) -> str:
        return "Drafter"

    def _get_router(self) -> Any:
        """Lazily acquire a ModelRouter. Returns None if unavailable
        (e.g., in test environments where the router can't be
        constructed); callers that don't override the router via
        compose(router=...) will receive an empty Draft instead of
        crashing.
        """
        if self._router is not None:
            return self._router
        try:
            from arail.registry import resolve
            self._router = resolve("fast", tab="agents").router()
            if self._router is None:
                log.warning("DrafterAgent: no usable model for the 'fast' "
                            "profile; compose() will require an explicit "
                            "router= (see the model status banner)")
        except Exception as exc:
            log.warning("DrafterAgent: model resolution failed (%s); compose() will require an explicit router=", exc)
            self._router = None
        return self._router

    def compose(
        self,
        context: str,
        intent: str,
        voice: str = "default",
        max_tokens: int = 400,
        temperature: float = 0.7,
        router: Optional[Any] = None,
    ) -> Draft:
        """Compose a draft from context + intent.

        Args:
            context:   The source material (email thread, meeting notes, ...).
            intent:    What the draft should accomplish.
            voice:     Voice template id (currently only "default"; per-blueprint
                       profiles land in v1.1).
            max_tokens: Cap on draft length.
            temperature: Sampling temperature passed through to the model.
            router:    Optional ModelRouter override (used by tests for hermetic
                       runs without spinning up the real model).

        Returns:
            A Draft with requires_consent=True. Caller is responsible
            for routing any send action through the consent agent.
        """
        if not context.strip():
            raise ValueError("Drafter.compose: context must not be empty")
        if not intent.strip():
            raise ValueError("Drafter.compose: intent must not be empty")

        prompt = self._build_prompt(context=context, intent=intent, voice=voice)
        used_router = router if router is not None else self._get_router()

        if used_router is None:
            return Draft(
                text="",
                requires_consent=True,
                tokens_in=len(prompt) // 4,
                model="(unavailable)",
                voice=voice,
                metadata={"error": "no router available"},
            )

        # L3 (ARCHITECTURE.md): drafter has no tick loop the loader wraps
        # (it's request-driven, invoked by blueprints), so its one
        # model-acquisition site gets its own explicit wrapper.
        from arail import agent_context
        try:
            with agent_context.agent_call("drafter"):
                response = used_router.complete(
                    prompt=prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
        except agent_context.AgentHeldError:
            # F8 (ARCHITECTURE.md): compose() is the one model-calling site
            # that does NOT swallow broadly (no try/except around
            # router.complete() until now) -- an explicit guard, mirroring
            # the existing "no router available" degradation above, so a
            # held Drafter degrades to an empty draft instead of raising
            # into whatever blueprint invoked it.
            return Draft(
                text="",
                requires_consent=True,
                tokens_in=len(prompt) // 4,
                model="(held)",
                voice=voice,
                metadata={"error": "held"},
            )

        # ModelResponse: .text, .model (optional), .backend (optional)
        text = getattr(response, "text", str(response)).strip()
        model_label = getattr(response, "model", "") or getattr(response, "backend", "") or "unknown"

        return Draft(
            text=text,
            requires_consent=True,
            tokens_in=len(prompt) // 4,
            model=model_label,
            voice=voice,
            metadata={"intent": intent, "context_chars": len(context)},
        )

    def _build_prompt(self, *, context: str, intent: str, voice: str) -> str:
        voice_hint = _VOICE_TEMPLATES.get(voice, _VOICE_TEMPLATES["default"])
        return (
            f"{_DRAFT_SYSTEM_PROMPT}\n\n"
            f"Voice: {voice_hint}\n\n"
            f"Intent: {intent.strip()}\n\n"
            f"Context:\n{context.strip()}\n\n"
            f"Draft:"
        )


# ── Singleton ────────────────────────────────────────────────────────
# Mirrors the Buddy pattern. Import as `from arail.agents.drafter import drafter`
# OR resolved by the agent loader walking lab/pkb/agents/drafter/.

drafter = DrafterAgent()
