"""Study surface — the tutor team's quiz bench.

Registered in ``portal/app.py`` as ``app.include_router(study_router)`` —
kept in its own module (the wiki_routes/world_routes pattern) so app.py
stays navigable.

## What a "coach" is

A coach is any agent folder under ``lab/pkb/agents/`` whose ``AGENT.md``
frontmatter declares a ``deck``, and whose loaded instance implements the
three-method drill protocol:

    session() -> dict | None      # the next due card, with its answer
    record(card_id, correct)      # grade it; drives spaced review
    cards() -> list[dict]         # the whole deck (for the progress count)

That is the entire contract. Nothing here knows about algebra or about any
particular book — a new subject coach appears on this page the moment its
folder exists, with no change to this file. The reference implementation is
the shared body at ``lab/pkb/agents/_tutor_kit/tutor_kit.py``.

## Why the duck-typing is guarded

``loader.load_one`` returns whatever singleton an agent folder exports, and
most agents (Buddy, SRE, the Debt Advisor) are not coaches at all. Every
call site here checks the protocol with ``_as_coach`` before touching an
instance, so a non-coach agent can never be invoked by this surface — and a
half-written coach degrades to "not on the page" rather than to a 500.

Security: ``POST /api/study/grade`` carries the same CSRF envelope as the
rest of the portal's writes. It is also the only write here, and the only
thing it can change is a Leitner box number in the agent's own state.json.

Honesty: this surface renders *only* what the coach hands it — a card
authored by a human, and an explanation read from a sealed World bundle. No
endpoint in this module calls a model. If a coach has nothing due, the page
says so instead of inventing a question.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)

router = APIRouter()

# The drill protocol a coach must implement to appear on this surface.
_COACH_PROTOCOL = ("session", "record", "cards")


def _err(status: int, payload: dict) -> JSONResponse:
    return JSONResponse(status_code=status, content=payload)


def _csrf_reject(request: Request) -> Optional[JSONResponse]:
    """The portal's standard write envelope. None when acceptable."""
    sfs = request.headers.get("sec-fetch-site", "").strip().lower()
    if sfs in ("cross-site", "none"):
        return _err(403, {"error": "cross_site"})
    origin = request.headers.get("origin", "")
    host = request.headers.get("host", "")
    if origin:
        origin_host = urlparse(origin).netloc
        if origin_host and origin_host != host:
            return _err(403, {"error": "cross_origin"})
    return None


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return {}
    return body if isinstance(body, dict) else {}


def _as_coach(agent_id: str) -> Optional[Any]:
    """Load an agent and return it only if it implements the drill protocol.

    Returns None for a missing agent, a broken one, or a perfectly healthy
    agent that simply isn't a coach (Buddy, SRE, …) — all three are the same
    answer to this surface: not something to quiz from.
    """
    if not agent_id or "/" in agent_id or ".." in agent_id:
        return None
    try:
        from arail.agents.loader import load_one
        inst = load_one(agent_id)
    except Exception as e:  # noqa: BLE001 — a broken agent must not 500 the page
        log.warning("study: load_one(%s) failed: %s", agent_id, e)
        return None
    if inst is None or not all(callable(getattr(inst, m, None)) for m in _COACH_PROTOCOL):
        return None
    return inst


def _progress(coach: Any) -> dict:
    """Deck size and how far through it she is, read from the coach's own
    Leitner state. Best-effort: a coach that doesn't expose boxes still
    reports its deck size."""
    total = 0
    try:
        total = len(coach.cards())
    except Exception:  # noqa: BLE001
        pass
    seen = mastered = 0
    try:
        boxes = (coach._load_state() or {}).get("boxes", {})  # noqa: SLF001 — documented protocol
        if isinstance(boxes, dict):
            seen = len(boxes)
            mastered = sum(1 for e in boxes.values()
                           if isinstance(e, dict) and int(e.get("box", 1)) >= 4)
    except Exception:  # noqa: BLE001
        pass
    return {"total": total, "seen": seen, "mastered": mastered}


_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{0,23}$")


def _presentation(fm: dict) -> dict:
    """The subject's visual identity, sanitized.

    AGENT.md is hot-editable by the operator and these values reach CSS, so
    they are validated here rather than trusted: ``accent`` must be a plain
    6-digit hex and ``palette`` a short slug the page maps to a built-in
    look. Anything else is dropped and the page falls back to its default —
    a coach with a typo'd color renders plainly instead of injecting a
    style, and never breaks the bench for the other subject.
    """
    accent = str(fm.get("accent") or "").strip()
    palette = str(fm.get("palette") or "").strip().lower()
    return {
        "subject": str(fm.get("subject") or "").strip()[:24],
        "palette": palette if _SLUG_RE.match(palette) else "",
        "accent": accent if _HEX_RE.match(accent) else "",
        "greeting": str(fm.get("greeting") or "").strip()[:120],
    }


def _team() -> list[dict]:
    """Every coach on the team, in nav order.

    Discovery is by frontmatter (``deck:``) plus the runtime protocol check —
    so adding a subject really is dropping in a folder.
    """
    try:
        from arail.agents.loader import discover
        found = discover()
    except Exception as e:  # noqa: BLE001
        log.warning("study: agent discovery failed: %s", e)
        return []

    out: list[dict] = []
    for agent_id, _path, fm in found:
        if not isinstance(fm, dict) or not fm.get("deck"):
            continue
        coach = _as_coach(agent_id)
        if coach is None:
            continue
        out.append({
            "agent": agent_id,
            "name": str(fm.get("name") or agent_id),
            "emoji": str(fm.get("emoji") or "📚"),
            "deck": str(fm.get("deck") or ""),
            "world": str(fm.get("world") or ""),
            "voice": str(fm.get("voice") or ""),
            **_presentation(fm),
            "progress": _progress(coach),
        })
    return out


@router.get("/api/study/team")
async def study_team():
    """The roster. Empty list is a legitimate answer — it means no coach
    folders exist yet, which the page renders as a how-to rather than an
    error."""
    return {"team": _team()}


@router.get("/api/study/next")
async def study_next(agent: str):
    """The next due card for one coach.

    ``card`` is null when the coach's spaced-review schedule says nothing is
    due — an honest 200, not a 404: the coach exists and is working, it just
    isn't going to invent a question to fill the space.
    """
    coach = _as_coach(agent)
    if coach is None:
        return _err(404, {"error": "not_a_coach", "agent": agent})
    try:
        card = coach.session()
    except Exception as e:  # noqa: BLE001
        log.warning("study: session() failed for %s: %s", agent, e)
        return _err(500, {"error": "session_failed", "agent": agent})
    return {"agent": agent, "card": card, "progress": _progress(coach)}


@router.post("/api/study/grade")
async def study_grade(request: Request):
    """Grade a card. The only write on this surface.

    She grades herself — the portal never tries to mark a free-text literary
    answer right or wrong, because a wrong auto-grade on 'explain the theme'
    would teach the wrong lesson. Grading only moves a Leitner box, which
    changes when the card comes back.
    """
    rejected = _csrf_reject(request)
    if rejected is not None:
        return rejected
    body = await _json_body(request)
    agent = str(body.get("agent") or "")
    card_id = str(body.get("card_id") or "")
    correct = bool(body.get("correct"))
    if not card_id:
        return _err(400, {"error": "card_id_required"})

    coach = _as_coach(agent)
    if coach is None:
        return _err(404, {"error": "not_a_coach", "agent": agent})
    try:
        coach.record(card_id, correct)
    except Exception as e:  # noqa: BLE001
        log.warning("study: record() failed for %s/%s: %s", agent, card_id, e)
        return _err(500, {"error": "record_failed"})
    return {"ok": True, "agent": agent, "card_id": card_id,
            "correct": correct, "progress": _progress(coach)}
