"""Theme-aware learning dictionary.

Buddy populates a dictionary of interesting terms tailored to the user's
current goal/theme. Default theme is AI / model-tuning so the lab owner
learns AI by using the lab; a travel goal yields phrases + pronunciation;
any other goal yields a domain glossary.

This module is deliberately framework-free (no FastAPI import) so it stays
unit-testable in isolation, mirroring ``goals.py``. The portal layer owns
concurrency gating (``scheduler.inference_slot``) and background tasks; this
module owns theme resolution, JSON persistence, prompt construction, and the
robust parse/repair that small local models require.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from arail.config import DATA_DIR

DICT_DIR = DATA_DIR / "dictionary"

# Schema version for the on-disk files. Bump when the term shape changes.
SCHEMA_VERSION = 1

# Field-length guards — small models occasionally emit runaway strings.
_MAX_SHORT_DEF = 280
_MAX_DETAIL = 1500
_MAX_EXAMPLE = 300
_MAX_EXAMPLES = 3
_MAX_RELATED = 4
_MAX_SLUG = 48

DEFAULT_SLUG = "ai-model-tuning"

DEFAULT_THEME: Dict[str, Any] = {
    "label": "AI / Model Tuning",
    "source": "default",
    "goal_id": None,
    "archetype": "research",
    "instruction": (
        "Core concepts for someone learning artificial intelligence and "
        "language-model fine-tuning: training, datasets, evaluation, model "
        "architectures, and tuning methods such as LoRA, RLHF, quantization, "
        "embeddings, and inference. Clear, beginner-friendly definitions."
    ),
}


# ---------------------------------------------------------------------------
# Term entry
# ---------------------------------------------------------------------------

@dataclass
class TermEntry:
    term: str
    short_def: str = ""
    examples: List[str] = field(default_factory=list)
    origin: str = ""
    related: List[str] = field(default_factory=list)
    key: str = ""
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "term": self.term,
            "short_def": self.short_def,
            "examples": list(self.examples),
            "origin": self.origin,
            "related": list(self.related),
            "key": self.key,
            "created_at": self.created_at,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def norm_key(term: str) -> str:
    """Normalized dedupe key: lowercase, strip surrounding punctuation,
    collapse internal whitespace. Two terms with the same key are dupes."""
    s = (term or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = s.strip(" \t\r\n.,;:!?\"'`()[]{}")
    return s


def _coerce_str(value: Any, limit: int) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    if len(s) > limit:
        s = s[:limit].rstrip() + "…"
    return s


def _coerce_str_list(value: Any, *, max_items: int, item_limit: int) -> List[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out: List[str] = []
    for item in value:
        if not isinstance(item, (str, int, float)):
            continue
        s = _coerce_str(item, item_limit)
        if s:
            out.append(s)
        if len(out) >= max_items:
            break
    return out


def coerce_entry(obj: Any) -> Optional[Dict[str, Any]]:
    """Coerce one parsed object into a clean term dict, or None if unusable.

    Untrusted model output: every field is coerced to the expected type and
    truncated. The text is stored verbatim (not HTML-escaped) — the render
    layer is responsible for using ``textContent`` so this stays safe."""
    if not isinstance(obj, dict):
        return None
    term = _coerce_str(obj.get("term"), 120)
    if not term:
        return None
    return {
        "term": term,
        "short_def": _coerce_str(obj.get("short_def") or obj.get("definition"), _MAX_SHORT_DEF),
        "detail": _coerce_str(obj.get("detail"), _MAX_DETAIL),
        "detail_source": _coerce_str(obj.get("detail_source"), 20),
        "category": _coerce_str(obj.get("category"), 40),
        "examples": _coerce_str_list(obj.get("examples"), max_items=_MAX_EXAMPLES, item_limit=_MAX_EXAMPLE),
        "origin": _coerce_str(obj.get("origin") or obj.get("etymology"), _MAX_SHORT_DEF),
        "related": _coerce_str_list(obj.get("related"), max_items=_MAX_RELATED, item_limit=80),
        "key": norm_key(term),
        "created_at": _now(),
    }


# ---------------------------------------------------------------------------
# Robust parse/repair — small models emit malformed JSON often.
# ---------------------------------------------------------------------------

def parse_entries(raw: str) -> Tuple[List[Dict[str, Any]], int]:
    """Parse a model response into a list of clean term dicts.

    Returns ``(entries, repair_level)`` where repair_level indicates how much
    salvage was needed (0 = clean, higher = more repair, -1 = total failure).
    Never raises."""
    text = (raw or "").strip()
    if not text:
        return [], -1

    # 1. Strip markdown code fences.
    fenced = re.sub(r"^```(?:json)?\s*", "", text)
    fenced = re.sub(r"\s*```$", "", fenced).strip()

    # 2. Slice to the outermost array span if present, else object span.
    span = _slice_span(fenced, "[", "]") or _slice_span(fenced, "{", "}") or fenced

    # 3. Direct load.
    parsed = _try_load(span)
    level = 0

    # 4. Trailing-comma repair.
    if parsed is None:
        repaired = re.sub(r",\s*([}\]])", r"\1", span)
        parsed = _try_load(repaired)
        level = 1

    # 5. Per-object regex salvage (last resort): keep every object that parses.
    if parsed is None:
        objs: List[Any] = []
        for match in re.finditer(r"\{[^{}]*\}", span):
            obj = _try_load(re.sub(r",\s*([}\]])", r"\1", match.group(0)))
            if isinstance(obj, dict):
                objs.append(obj)
        parsed = objs if objs else None
        level = 2

    # 5b. Line-based fallback: small models often ignore JSON entirely and
    # emit "Term: definition" lines. Salvage those rather than show nothing.
    if parsed is None:
        line_entries = _parse_lines(fenced)
        if line_entries:
            return line_entries, 3
        return [], -1

    # 6. Normalize container: single object -> list of one.
    if isinstance(parsed, dict):
        # Some models wrap in {"terms": [...]} or {"entries": [...]}.
        for key in ("terms", "entries", "dictionary", "items"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
        else:
            parsed = [parsed]
    if not isinstance(parsed, list):
        return [], -1

    # 7. Coerce + drop unusable entries.
    entries: List[Dict[str, Any]] = []
    for obj in parsed:
        entry = coerce_entry(obj)
        if entry is not None:
            entries.append(entry)

    if not entries:
        return [], -1
    return entries, level


def _slice_span(text: str, open_ch: str, close_ch: str) -> Optional[str]:
    start = text.find(open_ch)
    end = text.rfind(close_ch)
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start:end + 1]


def _try_load(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _parse_lines(text: str) -> List[Dict[str, Any]]:
    """Last-resort plain-text fallback: parse 'Term: definition' lines."""
    entries: List[Dict[str, Any]] = []
    seen = set()
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        # Strip list bullets / numbering: "- ", "* ", "1. ", "1) ".
        line = re.sub(r"^[\-\*•]\s+", "", line)
        line = re.sub(r"^\d+[.)]\s+", "", line).strip()
        if not line:
            continue
        # Split term from definition on the first ":", "—", "–", or " - ".
        parts = re.split(r"\s*[:—–]\s+|\s+-\s+", line, maxsplit=1)
        if len(parts) != 2:
            continue
        term, definition = parts[0].strip(" *_`\""), parts[1].strip()
        if not term or not definition or len(term) > 80:
            continue
        key = norm_key(term)
        if key in seen:
            continue
        entry = coerce_entry({"term": term, "short_def": definition})
        if entry:
            seen.add(key)
            entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# Theme resolution
# ---------------------------------------------------------------------------

# In-memory, per-process override. Survives within a portal run, resets on
# restart. An override always wins over the current goal until cleared.
_OVERRIDE: Optional[str] = None


def set_override(label: str) -> None:
    global _OVERRIDE
    cleaned = (label or "").strip()
    _OVERRIDE = cleaned or None


def clear_override() -> None:
    global _OVERRIDE
    _OVERRIDE = None


def get_override() -> Optional[str]:
    return _OVERRIDE


def theme_slug(label: str) -> str:
    """URL/file-safe slug from a theme label. Empty -> default slug."""
    s = (label or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if not s:
        return DEFAULT_SLUG
    return s[:_MAX_SLUG].strip("-") or DEFAULT_SLUG


def _instruction_for(label: str, archetype: str) -> str:
    label = (label or "").strip()
    if archetype == "travel":
        return (
            f"Useful words and short phrases for: {label}. For each entry, put "
            "the foreign word or phrase in 'term', an English meaning plus a "
            "simple phonetic pronunciation in 'short_def', and short example "
            "sentences (with English) in 'examples'."
        )
    if archetype == "research":
        return (
            f"Key concepts and terminology for: {label}. Focus on the AI, "
            "machine-learning, and model training/tuning ideas that matter, "
            "with clear beginner-friendly definitions."
        )
    if archetype == "operations":
        return (
            f"Operational terminology and concepts for: {label}. Cover the "
            "reliability, deployment, and monitoring ideas a practitioner needs, "
            "with clear definitions."
        )
    return (
        f"Key terms, vocabulary, and concepts someone learning about {label} "
        "should know, with clear, interesting, beginner-friendly definitions."
    )


def resolve_theme(override: Optional[str] = None) -> Dict[str, Any]:
    """Resolve the active dictionary theme. Precedence: override > default.

    The curated AI / model-tuning glossary is the always-on foundation; it is
    only replaced when the user explicitly picks a topic (the override, set via
    the topic box or the one-click "build a glossary for my goal" action). The
    research goal never silently swaps the theme — it's surfaced as a suggested
    action instead, so the lab owner always lands on the AI glossary.

    ``override`` defaults to the module-level per-session override."""
    from arail.swarm_goals import detect_goal_archetype

    ov = override if override is not None else _OVERRIDE
    if ov:
        ov = ov.strip()
    if ov:
        archetype = detect_goal_archetype(ov, "")
        return {
            "label": ov,
            "source": "override",
            "goal_id": None,
            "archetype": archetype,
            "instruction": _instruction_for(ov, archetype),
        }

    return dict(DEFAULT_THEME)


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def build_prompt(theme: Dict[str, Any], *, count: int, avoid_terms: List[str]) -> str:
    """Build a strict JSON-output prompt for Buddy to draft dictionary terms."""
    count = max(1, min(int(count), 40))
    instruction = str(theme.get("instruction") or DEFAULT_THEME["instruction"])
    avoid = ", ".join(t for t in avoid_terms if t)[:1500]
    avoid_line = (
        f"\nDo NOT repeat or closely duplicate any of these existing terms: {avoid}."
        if avoid else ""
    )
    return (
        "You are Buddy, an obsessed study partner building a learning "
        "dictionary for your friend.\n"
        f"THEME: {instruction}\n\n"
        f"Produce exactly {count} distinct, genuinely interesting entries.\n"
        "Return ONLY a JSON array, nothing else. Each element must be an object:\n"
        '{"term": "...", "short_def": "one clear sentence", '
        '"examples": ["..."], "origin": "short origin or etymology note", '
        '"related": ["related term", "..."]}\n'
        "Rules: no prose, headers, or markdown outside the JSON array; "
        "no code fences; short_def under 30 words; 0-3 examples; "
        "related is 0-4 short strings; every entry must be distinct."
        f"{avoid_line}"
    )


def generate_terms(
    theme: Dict[str, Any],
    *,
    count: int,
    avoid_terms: Optional[List[str]] = None,
    router: Any = None,
) -> Tuple[List[Dict[str, Any]], int]:
    """Run one generation pass. ``router`` is injectable for tests.

    Synchronous on purpose — the portal wraps this in ``asyncio.to_thread``
    inside an ``inference_slot`` so generation stays serialized (OOM guard)."""
    if router is None:
        from arail.router import ModelRouter
        router = ModelRouter(billing_source="agent")
    prompt = build_prompt(theme, count=count, avoid_terms=avoid_terms or [])
    # L3 (ARCHITECTURE.md): dictionary is not an agent -- system_call so it
    # stops masquerading under the blanket "agent" bucket (V8).
    from arail import agent_context
    with agent_context.system_call("dictionary"):
        resp = router.complete(prompt, max_tokens=1400, temperature=0.7, top_p=0.9)
    return parse_entries(getattr(resp, "text", "") or "")


def build_expand_prompt(theme: Dict[str, Any], term: str, short_def: str = "") -> str:
    """Prompt for a single-term deeper explanation. Plain text out (no JSON) —
    far more reliable on small local models than a structured batch."""
    label = str(theme.get("label") or DEFAULT_THEME["label"])
    ctx = f" Its short definition is: {short_def}." if short_def else ""
    return (
        "You are Buddy, an obsessed study partner helping a curious beginner.\n"
        f"Explain the term \"{term}\" (in the context of {label}) in 2 to 4 short, "
        f"clear sentences.{ctx} Include one quick concrete example or analogy. "
        "Write plain prose only — no markdown, no bullet points, no headings, "
        "no restating the term as a title."
    )


def expand_term(
    theme: Dict[str, Any], term: str, *, short_def: str = "", router: Any = None
) -> str:
    """Generate a deeper plain-text explanation of one term. Returns "" on an
    empty model reply. ``router`` is injectable for tests."""
    if router is None:
        from arail.router import ModelRouter
        router = ModelRouter(billing_source="agent")
    from arail import agent_context
    with agent_context.system_call("dictionary"):
        resp = router.complete(
            build_expand_prompt(theme, term, short_def),
            max_tokens=320, temperature=0.6, top_p=0.9,
        )
    return (getattr(resp, "text", "") or "").strip()


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class DictionaryStore:
    """Per-theme JSON persistence under ``lab/data/dictionary/<slug>.json``."""

    def __init__(self) -> None:
        DICT_DIR.mkdir(parents=True, exist_ok=True)

    def _path(self, slug: str) -> Path:
        return DICT_DIR / f"{slug}.json"

    def load(self, slug: str) -> Optional[Dict[str, Any]]:
        path = self._path(slug)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            # Corruption is treated as "no cache" — caller offers a reseed.
            return None
        return data if isinstance(data, dict) else None

    def _save(self, doc: Dict[str, Any]) -> None:
        slug = doc["slug"]
        path = self._path(slug)
        # Ensure the directory exists at write time (not just __init__) so the
        # store survives DICT_DIR being repointed or the dir being removed.
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, indent=2, default=str))
        os.replace(tmp, path)  # atomic — never leaves a half-written file

    def get_or_init(self, theme: Dict[str, Any]) -> Dict[str, Any]:
        slug = theme_slug(theme.get("label", ""))
        doc = self.load(slug)
        if doc is not None:
            doc.setdefault("terms", [])
            doc.setdefault("generating", False)
            doc["slug"] = slug
            return doc
        # New file. The default AI theme ships pre-populated from the curated
        # glossary so the Dictionary is instantly full with no model call —
        # custom themes start empty and offer a seed CTA.
        terms: List[Dict[str, Any]] = []
        if slug == DEFAULT_SLUG:
            from arail.glossary_seed import seed_entries
            terms = seed_entries()
        doc = {
            "version": SCHEMA_VERSION,
            "slug": slug,
            "theme": dict(theme),
            "terms": terms,
            "generating": False,
            "last_generated_at": _now() if terms else None,
            "last_error": None,
        }
        self._save(doc)
        return doc

    def find_term(self, theme: Dict[str, Any], term_key: str) -> Optional[Dict[str, Any]]:
        doc = self.get_or_init(theme)
        for t in doc["terms"]:
            if str(t.get("key") or norm_key(t.get("term", ""))) == term_key:
                return t
        return None

    def set_term_detail(
        self, theme: Dict[str, Any], term_key: str, detail: str, *, source: str = "buddy"
    ) -> Optional[Dict[str, Any]]:
        """Attach an enriched explanation to one term. Returns the term, or
        None if no term with that key exists in the theme's dictionary."""
        doc = self.get_or_init(theme)
        for t in doc["terms"]:
            if str(t.get("key") or norm_key(t.get("term", ""))) == term_key:
                t["detail"] = _coerce_str(detail, _MAX_DETAIL)
                t["detail_source"] = source
                self._save(doc)
                return t
        return None

    def add_terms(
        self, theme: Dict[str, Any], new_entries: List[Dict[str, Any]]
    ) -> Tuple[int, int]:
        """Append new entries, deduped against existing + within the batch.

        Returns ``(added, skipped)``."""
        doc = self.get_or_init(theme)
        seen = {str(e.get("key") or norm_key(e.get("term", ""))) for e in doc["terms"]}
        added = 0
        skipped = 0
        for entry in new_entries:
            key = str(entry.get("key") or norm_key(entry.get("term", "")))
            if not key or key in seen:
                skipped += 1
                continue
            seen.add(key)
            entry["key"] = key
            doc["terms"].append(entry)
            added += 1
        doc["last_generated_at"] = _now()
        doc["last_error"] = None
        self._save(doc)
        return added, skipped

    def set_generating(
        self, theme: Dict[str, Any], flag: bool, error: Optional[str] = None
    ) -> None:
        doc = self.get_or_init(theme)
        doc["generating"] = bool(flag)
        if error is not None:
            doc["last_error"] = error
        self._save(doc)


# ---------------------------------------------------------------------------
# Goal-event hook — keep the active theme in sync, never auto-generate.
# ---------------------------------------------------------------------------

def on_goal_event(event: str, payload: Dict[str, Any]) -> None:
    """Registered with ``goals.add_listener``. When the goal changes, drop any
    stale override so the dictionary follows the new goal. Generation stays
    lazy (next GET offers a reseed) so inference is never triggered here —
    the OOM guard depends on generation being on-demand only."""
    if event in ("goal_set", "goal_cleared"):
        clear_override()
