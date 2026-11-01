"""Arail lab_brain — system prompt builder for lab-aware LLM calls.

Every LLM call the lab makes — chat, researcher, curator — should start
from a system prompt that tells the model **what Arail is, what it can
do, and what state it's in right now**. Without this, the model is a
generic assistant that doesn't know its own environment. With this,
it can answer questions like:

    "How do I run a new experiment?"     → knows about ./arailctl CLI
    "Where does the agent write reports?" → knows lab/pkb/agents/
    "Is the heavy window active?"         → checks the scheduler
    "How do I halt the researcher?"       → knows the Halt button

The prompt is composed from three layers:

1. **Identity** — brand + intent. Who is the lab? Who is it for?
2. **Capabilities** — a compact reference card of the lab's features
   (router, scheduler, PKB, wiki, curator, researcher, CLI, portal).
3. **Current state** — live snapshot: current goal, backend, window,
   halt flag, recent agent activity.

All three layers are optional so callers can keep the token budget
small when they need to (`build_system_prompt(include_state=False)`).
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Optional


_CHAT_RETRIEVAL_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "how", "i", "in", "is", "it", "of", "on", "or", "that", "the",
    "this", "to", "what", "where", "which", "who", "why", "with", "you",
}

_CHAT_RETRIEVAL_PHRASE_MAX = 3

# ── Static capabilities reference ────────────────────────────────────────
# This is the "brain" of the prompt — the part that teaches the model
# what this blueprint is and how its pieces connect. Tight, factual,
# structured so the model can pattern-match on user questions.

_CAPABILITIES = """\
# Lab capabilities

You are running inside a local AI research lab. Everything below
describes what this lab actually is and how its pieces fit together.
When the user asks a question, answer in terms of these concrete
capabilities — not generic "you could build X".

## Model router (arail.router)

One interface, pluggable backends, picks automatically from env:
- **MLX** (Apple Silicon) — native Metal via mlx/mlx-lm
- **CUDA** (Nvidia) — via vLLM + torch
- **CPU** — via llama-cpp-python, works anywhere
- **AirLLM** — deep tier, layer-streaming for 70B+ models from disk
- **OpenAI-compat** — LM Studio, Ollama, NVIDIA NIM, any /v1/chat/completions
- **HuggingFace / OpenRouter / Claude** — cloud, opt-in only in hybrid mode

All costs are tracked by `arail.costs.CostTracker` and shown in the
top meter bar on the dashboard (cloud equivalent vs energy actual).

## Scheduler (arail.scheduler)

The lab has two work windows so it doesn't burn the GPU while the
user is engaged:

- **Active window** (default 08:00–22:00) — light SLM work only,
  lab stays responsive.
- **Heavy window** (default 22:00–08:00) — deep-backend experiments
  (AirLLM today) and full-send GPU burns happen here.
- **Hold all agents** button in the dashboard nav stops agents from
  calling models or posting findings/suggestions/announcements, without
  taking the portal down. Operational/error lines and SRE crash alerts
  continue. Resume with one click.
- **5-minute courtesy delay** on boot before the researcher's
  first tick so the UI loads clean.

Configure via `LAB_ACTIVE_HOURS`, `LAB_HEAVY_HOURS`,
`LAB_STARTUP_DELAY_SEC` in `.env`.

## Personal Knowledge Base (lab/pkb/)

The lab's brain on disk. Folder layout:

- `inbox/` — drop zone for any material
- `sources/{papers,articles,datasets}/` — sorted ingests
- `notes/` — user's own markdown
- `agents/{research,experiments,synthesis,recommendations}/` — where
  the researcher writes its outputs
- `compiled/docs/` — auto-generated wiki pages from the repo source
- `inference/` — saved prompts, completions, chains
- `.wiki-cache/manifest.json` — compiled wiki index

CLI: `./arailctl pkb {ingest,compile,browse}`.

## Wiki (arail.wiki + arail.docgen)

A self-curating wiki at `/wiki` that:

- Renders every markdown file with `[[wikilinks]]`, backlinks,
  tags, and YAML frontmatter.
- Shows a force-directed knowledge graph at `/wiki/graph`.
- **Auto-generates pages from the repo's own source** — every
  Python module via `ast`, every shell script's header comment,
  every compose overlay, every hand-written guide, the `.env.example`
  reference. Write a better docstring, rebuild, get better docs.
- Rebuild button on the dashboard, or `./arailctl wiki build`, or
  `POST /api/wiki/rebuild`.
- Auto-rebuilds 30 seconds after any researcher write (debounced).

## Agents (arail.agents)

- **ResearcherAgent** — takes a goal, generates hypotheses, designs
  experiments, writes research reports to `lab/pkb/agents/research/`.
  Auto-starts on boot if a bootstrap goal exists. Halt from the
  dashboard or `POST /api/jobs/halt`.
- **CuratorAgent** — proposes data sources for a goal, sends them
  through the consent gate, fetches approved URLs, caches locally.
- **ConsentStore** — every external fetch requires explicit per-URL
  approval. Dashboard has a consent card for pending requests.

## Portal (arail.portal)

FastAPI app at http://127.0.0.1:{portal_port} with routes:

- `/` dashboard — goal prompt, activity feed, cost meter, halt switch
- `/dac` — PKB file browser
- `/wiki` — rendered wiki reader with knowledge graph
- `/terminal`, `/notebook`, `/plugins`, `/graph` — embedded services
- `/api/chat` — talks to you, the model, with this system prompt
- `/api/pkb/*` — PKB operations
- `/api/wiki/*` — wiki build, status, search, graph
- `/api/jobs/*` — halt, resume, state
- `/api/system/*` — health, costs, destroy, graph

## CLI (./arailctl)

One dispatcher, every subcommand:

    ./arailctl setup       # first-time provision
    ./arailctl start       # launch portal + services
    ./arailctl stop        # graceful shutdown
    ./arailctl status      # what's running
    ./arailctl doctor      # env validation
    ./arailctl reset       # wipe state (with --yes gate)
    ./arailctl pkb <op>    # knowledge base ops
    ./arailctl wiki <op>   # documentation-as-code
"""


def _identity_block(brand_name: str, tagline: str,
                    intent: str, intent_name: str,
                    domain_context: str) -> str:
    return (
        f"You are the assistant inside {brand_name} — {tagline}.\n\n"
        f"The user has configured this lab for **{intent_name}** work "
        f"(intent: `{intent}`).\n\n"
        f"{domain_context}\n"
    )


def _state_block(
    *,
    active_backend_name: Optional[str] = None,
    active_model_name: Optional[str] = None,
) -> str:
    """Build the live-state section. Best-effort — never raises.

    ``active_backend_name`` / ``active_model_name`` win over env-var
    defaults when provided. The chat path passes these in so the system
    prompt reflects the *dispatched* backend (e.g. an Ollama runtime
    override) instead of the lab's configured default. Without that,
    the model parrots stale identity from the env to users (literally
    "I'm running mlx Qwen3-8B" while answering via Ollama nemotron3).
    """
    lines: list[str] = ["# Current lab state", ""]
    # Scheduler window
    try:
        from arail.scheduler import current_window, jobs_halted, window_label
        w = current_window()
        lines.append(f"- Work window: **{window_label(w)}**")
        lines.append(f"- Jobs halted: **{jobs_halted()}**")
    except Exception:
        pass
    # Current goal
    try:
        from arail.goals import GoalStore
        current = GoalStore().get_current()
        if current:
            goal_text = current.get("goal_text", "(no text)")
            progress = current.get("progress", 0)
            lines.append(f"- Active goal: \"{goal_text[:140]}\" "
                         f"(progress: {int(progress * 100)}%)")
        else:
            lines.append("- Active goal: **none** — waiting for user to set one")
    except Exception:
        pass
    # Lab brief — World identity, approved-knowledge digest, redirects,
    # research-program headline. The same brief the Knowledge page's
    # Agent Focus renders (goal already printed above; kept deduped).
    # Volatile block on purpose: never let this reach the frozen cached
    # prompt prefix.
    try:
        from arail.lab_brief import get_cached_brief
        brief = get_cached_brief()
        w = brief.get("world")
        if w:
            lines.append(
                f"- World: **{w.get('display_name') or w.get('slug')}** "
                f"({w.get('provenance_tier') or 'unknown'} · "
                f"{w.get('term_count') if w.get('term_count') is not None else '?'} terms)")
        k = brief.get("knowledge") or {}
        gate = "approved-only" if k.get("gate_enabled") else "raw corpus (gate off)"
        lines.append(
            f"- Compiled KB ({gate}): {k.get('approved_total', 0)} approved, "
            f"{k.get('pending_total', 0)} pending review")
        for agent_id, r in (brief.get("redirects") or {}).items():
            lines.append(f"- Operator redirect ({agent_id}): {r['instruction'][:140]}")
        prog = brief.get("research_program") or {}
        if prog.get("exists") and prog.get("objective"):
            lines.append(f"- Research program: {prog['objective'][:120]}")
    except Exception:
        pass
    # Agent workflow memory
    try:
        from arail.agent_workflows import list_agent_workflows

        workflows = list_agent_workflows()
        if workflows:
            lines.append("")
            lines.append("## Agent workflow memory")
            for row in workflows:
                agent_id = str(row.get("agent_id") or "agent")
                objective = str(row.get("objective") or "").strip()
                current_task = str(row.get("current_task") or "").strip()
                next_step = str(row.get("next_step") or "").strip()
                pause_reason = str(row.get("pause_reason") or "").strip()
                completed = [
                    str(step).strip()
                    for step in (row.get("completed_steps") or [])
                    if str(step).strip()
                ]
                chatter = row.get("chatter") or {}
                lines.append(f"- {agent_id}: status=**{row.get('status', 'unknown')}**")
                if objective:
                    lines.append(f"  objective: {objective[:160]}")
                if current_task:
                    lines.append(f"  current: {current_task[:160]}")
                if next_step:
                    lines.append(f"  next: {next_step[:160]}")
                if completed:
                    lines.append(f"  completed: {', '.join(completed[-3:])[:200]}")
                if pause_reason:
                    lines.append(f"  pause: {pause_reason[:160]}")
                if chatter:
                    lines.append(
                        f"  chatter: too_chatty={bool(chatter.get('too_chatty'))}, "
                        f"global_cooldown_sec={chatter.get('global_cooldown_sec', 'n/a')}"
                    )
    except Exception:
        pass
    # Backend — prefer the dispatched values from the caller (chat path
    # passes the actually-routed backend); fall back to env defaults
    # only when no caller-provided value is available.
    backend = (active_backend_name or "").strip() or os.getenv("MODEL_BACKEND", "auto")
    model = (active_model_name or "").strip() or os.getenv("MODEL_NAME", "unknown")
    lines.append(f"- Backend: **{backend}** · model: `{model}`")
    # Cost snapshot
    try:
        from arail.costs import cost_tracker
        s = cost_tracker.get_summary()
        if s.get("total_calls"):
            cloud = s.get("cloud_equivalent_usd", 0)
            energy = s.get("energy_usd", 0)
            saved = s.get("savings_usd", 0)
            lines.append(
                f"- Cost to date: ${cloud:.2f} cloud equiv, ${energy:.4f} "
                f"energy actual → ${saved:.2f} saved across "
                f"{s['total_calls']} calls"
            )
    except Exception:
        pass
    lines.append(f"- Timestamp: {datetime.now().isoformat(timespec='seconds')}")
    return "\n".join(lines)


# The static "how to answer" guidance. Extracted to a module constant so the
# legacy build_system_prompt path and the cache-aware build_system_prompt_parts
# path share *byte-identical* text (the frozen prefix must be stable).
_HOW_TO_ANSWER = (
    "# How to answer\n\n"
    "- Ground answers in the lab's actual capabilities listed above.\n"
    "- If the user asks how to do something, name the exact CLI "
    "command, endpoint, or file path.\n"
    "- If they want to kick off research, tell them to type a goal "
    "on the dashboard (which triggers the researcher agent).\n"
    "- If the deep tier is needed but the current window is active, "
    "say so — the lab won't run heavy work until the heavy window.\n"
    "- If the user asks whether the lab is working/healthy, point them "
    "at `./arailctl status` or `./arailctl doctor` first — those give a "
    "real pass/fail read. Don't just tell them to eyeball the dashboard.\n"
    "- Keep answers short unless asked for depth. This lab runs "
    "locally; every token costs energy."
)


def build_system_prompt(
    *,
    include_capabilities: bool = True,
    include_state: bool = True,
    extra_context: Optional[str] = None,
    active_backend_name: Optional[str] = None,
    active_model_name: Optional[str] = None,
) -> str:
    """Compose the full lab-aware system prompt.

    Args:
        include_capabilities: Include the static capabilities reference
            (~600 tokens). Turn off if you're in a tight budget.
        include_state: Include the live state snapshot (~150 tokens).
            Turn off when calling from contexts that don't need it.
        extra_context: Extra guidance appended at the end — useful for
            per-call instructions like "respond in 2 sentences" or
            "output valid JSON only".
    """
    from arail.brand import load_brand
    brand = load_brand()

    intent = os.getenv("LAB_INTENT", "ai")
    intent_name = os.getenv("LAB_INTENT_NAME", "AI Engineer")

    # Pull the domain-specific system context from the researcher module
    # so we don't duplicate the content.
    try:
        from arail.agents.researcher import _get_system_context
        domain_context = _get_system_context(intent)
    except Exception:
        domain_context = "You are a research lab assistant."

    parts = [
        _identity_block(
            brand_name=brand.name,
            tagline=brand.tagline,
            intent=intent,
            intent_name=intent_name,
            domain_context=domain_context,
        )
    ]
    if include_capabilities:
        parts.append(_CAPABILITIES.replace(
            "{portal_port}", os.getenv("PORTAL_PORT", "8080")
        ))
    if include_state:
        parts.append(_state_block(
            active_backend_name=active_backend_name,
            active_model_name=active_model_name,
        ))

    parts.append(_HOW_TO_ANSWER)

    if extra_context:
        parts.append(f"# Extra instructions\n\n{extra_context}")

    return "\n\n".join(parts)


def build_system_prompt_parts(
    *,
    include_capabilities: bool = True,
    include_state: bool = True,
    extra_context: Optional[str] = None,
    active_backend_name: Optional[str] = None,
    active_model_name: Optional[str] = None,
) -> tuple[str, str]:
    """Split the system prompt into ``(frozen_prefix, volatile_remainder)``.

    ``frozen_prefix`` — identity + capabilities + how-to-answer. Byte-stable
    within a session, so it is safe to send as a cached Anthropic ``system``
    block (prompt caching). It must NOT contain per-request data.

    ``volatile_remainder`` — the live state block (window, goal, cost, and the
    per-second timestamp) plus any ``extra_context`` (KB retrieval). Changes
    every request, so it must stay OUT of the cached prefix. Empty string when
    there's nothing volatile.

    The frozen content here matches the leading sections of
    ``build_system_prompt`` (identity → capabilities → how-to-answer); only the
    *ordering* relative to the state block differs (frozen is contiguous), which
    is exactly what makes it cacheable.
    """
    from arail.brand import load_brand
    brand = load_brand()

    intent = os.getenv("LAB_INTENT", "ai")
    intent_name = os.getenv("LAB_INTENT_NAME", "AI Engineer")

    try:
        from arail.agents.researcher import _get_system_context
        domain_context = _get_system_context(intent)
    except Exception:
        domain_context = "You are a research lab assistant."

    frozen_parts = [
        _identity_block(
            brand_name=brand.name,
            tagline=brand.tagline,
            intent=intent,
            intent_name=intent_name,
            domain_context=domain_context,
        )
    ]
    if include_capabilities:
        frozen_parts.append(_CAPABILITIES.replace(
            "{portal_port}", os.getenv("PORTAL_PORT", "8080")
        ))
    frozen_parts.append(_HOW_TO_ANSWER)
    frozen = "\n\n".join(frozen_parts)

    volatile_parts: list[str] = []
    if include_state:
        volatile_parts.append(_state_block(
            active_backend_name=active_backend_name,
            active_model_name=active_model_name,
        ))
    if extra_context:
        volatile_parts.append(f"# Extra instructions\n\n{extra_context}")
    volatile = "\n\n".join(p for p in volatile_parts if p)

    return frozen, volatile


def build_chat_prompt(
    user_message: str,
    conversation: list[dict] | None = None,
    *,
    include_capabilities: bool = True,
    include_state: bool = True,
) -> str:
    """Format a full chat request as a single prompt string.

    Used as a fallback for backends that only accept plain text.

    Args:
        user_message: The user's current input.
        conversation: Prior turns as
            ``[{"role": "user"|"assistant", "content": "..."}]``.
    """
    return render_chat_transcript(
        build_chat_messages(
            user_message,
            conversation,
            include_capabilities=include_capabilities,
            include_state=include_state,
        )
    )


def _chat_search_terms(user_message: str) -> list[str]:
    words = []
    for raw in user_message.replace("/", " ").replace("_", " ").split():
        token = "".join(ch for ch in raw.lower() if ch.isalnum() or ch in {"-", "."})
        if len(token) < 3 or token in _CHAT_RETRIEVAL_STOPWORDS:
            continue
        words.append(token)

    unique: list[str] = []
    seen: set[str] = set()
    for token in words:
        if token in seen:
            continue
        seen.add(token)
        unique.append(token)
    return unique[:6]


def _chat_search_phrases(tokens: list[str]) -> list[str]:
    phrases: list[str] = []
    seen: set[str] = set()
    max_width = min(_CHAT_RETRIEVAL_PHRASE_MAX, len(tokens))
    for width in range(max_width, 1, -1):
        for idx in range(0, len(tokens) - width + 1):
            phrase = " ".join(tokens[idx:idx + width]).strip()
            if not phrase or phrase in seen:
                continue
            seen.add(phrase)
            phrases.append(phrase)
    return phrases[:6]


def _snippet_relevance(snippet: str, tokens: list[str], query: str) -> int:
    lowered = snippet.lower()
    score = 0
    if query and query in lowered:
        score += 12
    coverage = sum(1 for token in tokens if token in lowered)
    score += coverage * 3
    return score


def _path_relevance(path: str, tokens: list[str]) -> int:
    lowered = path.lower()
    return sum(2 for token in tokens if token in lowered)


def retrieve_chat_context(
    user_message: str,
    *,
    max_results: int = 4,
) -> list[dict[str, Any]]:
    """Best-effort PKB retrieval for chat.

    The built-in PKB search is lexical, so we query both the full user
    message and a small set of extracted keywords, then merge and score
    the hits. Failures are swallowed so chat keeps working even if the
    PKB is empty or temporarily unavailable.
    """
    query = user_message.strip()
    if not query:
        return []

    try:
        # Gated retrieval: chat RAG builds on the Compiled (approved) KB, not
        # the raw candidate corpus (falls back to raw when the gate is off).
        from arail.pkb import search_for_agents as pkb_search
    except Exception:
        return []

    ranked: dict[str, dict[str, Any]] = {}
    tokens = _chat_search_terms(query)
    weighted_terms: list[tuple[str, int]] = [(query, 8)]
    weighted_terms.extend((phrase, 5) for phrase in _chat_search_phrases(tokens))
    weighted_terms.extend((token, 2) for token in tokens)

    for term, weight in weighted_terms:
        try:
            results = pkb_search(term)
        except Exception:
            continue
        for item in results[:8]:
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            existing = ranked.setdefault(
                path,
                {
                    "path": path,
                    "name": item.get("name") or path.rsplit("/", 1)[-1],
                    "match_count": 0,
                    "score": 0,
                    "snippets": [],
                    "term_hits": set(),
                },
            )
            existing["match_count"] = max(
                int(existing.get("match_count") or 0),
                int(item.get("match_count") or 0),
            )
            existing["term_hits"].add(term)
            existing["score"] = int(existing.get("score") or 0) + (
                int(item.get("match_count") or 0) * weight
            ) + _path_relevance(path, tokens)
            snippets = existing["snippets"]
            for snippet in item.get("snippets") or []:
                text = str(snippet).strip()
                if text and text not in snippets:
                    snippets.append(text)

    for item in ranked.values():
        snippets = list(item.get("snippets") or [])
        snippets.sort(
            key=lambda snippet: _snippet_relevance(snippet, tokens, query.lower()),
            reverse=True,
        )
        item["snippets"] = snippets[:3]
        item["score"] = int(item.get("score") or 0) + (
            len(item.get("term_hits") or set()) * 4
        )

    ordered = sorted(
        ranked.values(),
        key=lambda item: (int(item.get("score") or 0), int(item.get("match_count") or 0)),
        reverse=True,
    )
    for item in ordered:
        item.pop("term_hits", None)
    return ordered[:max_results]


def chat_context_sources(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Shape retrieval hits into compact, display-ready source citations.

    The chat UI shows these under a grounded reply so the user can see
    *which* approved knowledge-base documents informed the answer — the
    retrieval is otherwise invisible. Kept deliberately small: path, a
    human name, and one representative snippet. No scores or internals.
    """
    sources: list[dict[str, Any]] = []
    for item in results or []:
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        snippets = [
            str(s).strip() for s in (item.get("snippets") or []) if str(s).strip()
        ]
        sources.append({
            "path": path,
            "name": str(item.get("name") or path.rsplit("/", 1)[-1]).strip(),
            "snippet": snippets[0] if snippets else "",
        })
    return sources


def _format_chat_context(results: list[dict[str, Any]]) -> str | None:
    if not results:
        return None

    lines = [
        "# Retrieved knowledge base context",
        "",
        "Use this retrieved local context when it is relevant. Prefer it over generic knowledge when it directly answers the question. If the retrieved notes are incomplete or conflicting, say so.",
    ]
    for item in results:
        path = str(item.get("path") or "unknown")
        snippets = [str(snippet).strip() for snippet in (item.get("snippets") or []) if str(snippet).strip()]
        lines.append("")
        lines.append(f"## {path}")
        for snippet in snippets[:2]:
            lines.append(snippet)
    return "\n".join(lines)


def build_chat_messages(
    user_message: str,
    conversation: list[dict] | None = None,
    *,
    include_capabilities: bool = True,
    include_state: bool = True,
    active_backend_name: Optional[str] = None,
    active_model_name: Optional[str] = None,
    retrieved: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Build chat-style messages for chat-capable models.

    ``retrieved`` lets the caller pass pre-computed PKB hits (from
    ``retrieve_chat_context``) so retrieval isn't run twice — once here
    and once by the endpoint that wants to surface the sources. When
    ``None`` the retrieval is performed internally as before.

    ``active_backend_name`` / ``active_model_name`` are the actually-
    routed backend identifiers for *this* request. Pass them in so the
    system prompt's state block reflects what the model is really
    running on, not the lab's configured default. Without this, a
    runtime override (e.g. picking an Ollama model when MODEL_BACKEND=mlx)
    leaves the system prompt advertising the wrong identity, and the
    model dutifully parrots it back to the user.
    """
    results = retrieved if retrieved is not None else retrieve_chat_context(user_message)
    context_block = _format_chat_context(results)
    system = build_system_prompt(
        include_capabilities=include_capabilities,
        include_state=include_state,
        extra_context=context_block,
        active_backend_name=active_backend_name,
        active_model_name=active_model_name,
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for turn in conversation or []:
        role = turn.get("role", "user")
        content = turn.get("content", "").strip()
        if not content:
            continue
        if role not in {"user", "assistant", "system"}:
            role = "user"
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message.strip()})
    return messages


def build_chat_payload(
    user_message: str,
    conversation: list[dict] | None = None,
    *,
    include_capabilities: bool = True,
    include_state: bool = True,
    active_backend_name: Optional[str] = None,
    active_model_name: Optional[str] = None,
    retrieved: list[dict[str, Any]] | None = None,
) -> tuple[str, list[dict]]:
    """Build a cache-friendly chat payload: ``(frozen_system, messages)``.

    ``frozen_system`` is the byte-stable cacheable prefix (identity +
    capabilities + how-to-answer) — passed as the Anthropic cached ``system``
    block.

    ``messages`` is the structured turn list:
      * prior ``conversation`` turns (history) — stable, append-only;
      * a final ``user`` turn carrying the per-request VOLATILE context (live
        state block + KB retrieval) followed by the user's question.

    Keeping volatile content in the final turn — not the system prefix — is
    what lets the frozen prefix and the accumulated history cache cleanly
    across turns. Used only for the Claude backend; local backends keep their
    existing flat-prompt path unchanged.
    """
    results = retrieved if retrieved is not None else retrieve_chat_context(user_message)
    context_block = _format_chat_context(results)
    frozen, volatile = build_system_prompt_parts(
        include_capabilities=include_capabilities,
        include_state=include_state,
        extra_context=context_block,
        active_backend_name=active_backend_name,
        active_model_name=active_model_name,
    )

    messages: list[dict] = []
    for turn in conversation or []:
        role = turn.get("role", "user")
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        if role not in {"user", "assistant", "system"}:
            role = "user"
        messages.append({"role": role, "content": content})

    final_user = user_message.strip()
    if volatile:
        final_user = f"{volatile}\n\n{final_user}"
    messages.append({"role": "user", "content": final_user})

    return frozen, messages


def render_chat_transcript(messages: list[dict[str, str]]) -> str:
    """Render chat messages into the plain-text transcript fallback."""
    parts: list[str] = []
    for message in messages:
        role = (message.get("role") or "user").strip().lower()
        content = (message.get("content") or "").strip()
        if not content:
            continue
        if role == "system":
            parts.append(f"<system>\n{content}\n</system>\n")
            continue
        label = "Assistant" if role == "assistant" else "User"
        parts.append(f"{label}: {content}")
    parts.append("Assistant:")
    return "\n\n".join(parts)
