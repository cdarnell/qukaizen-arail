"""Slugging + tolerant model-output parsing.

Moved verbatim from qukaizen-arail's ``src/arail/world_forge.py`` (the
`slugify` / `loose_json` / `first_array` helpers) as part of the
``dac_world`` migration — see
``sprints/2026-07-19-dac-generates-arail-worlds/ARCHITECTURE.md`` (qukaizen-ddac).
"""

from __future__ import annotations

import json
import re
from typing import Any


def slugify(s: str) -> str:
    """Lowercase, non-alnum runs -> '-', <=48 chars."""
    out = re.sub(r"[^a-z0-9]+", "-", str(s).lower().strip())
    return out.strip("-")[:48]


def loose_json(raw: str) -> Any:
    """Best-effort JSON from small-model output. Never raises; None on defeat.

    The repair ladder mirrors ARAIL's ``dictionary.parse_entries`` steps 1-4
    (fence strip -> span slice -> direct load -> trailing-comma repair)
    WITHOUT its glossary coercion -- forge stage outputs have varied shapes.
    """
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    # Strip a markdown code fence if present.
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```\s*$", text, re.S)
    if fence:
        text = fence.group(1).strip()
    # Slice to the outermost JSON value span.
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if starts:
        start = min(starts)
        closer = "}" if text[start] == "{" else "]"
        end = text.rfind(closer)
        if end > start:
            text = text[start:end + 1]
    for candidate in (text, re.sub(r",\s*([}\]])", r"\1", text)):
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def first_array(obj: Any) -> list:
    """Small models wrap arrays under arbitrary keys; find the first one."""
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, list):
                return v
    return []
