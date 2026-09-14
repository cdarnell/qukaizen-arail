"""Content validator -- the actual fix for the XXXX/YYYY placeholder incident.

Moved verbatim from qukaizen-arail's ``src/arail/world_forge.py`` (commit
``2eb41ea``, "validate_bundle_content refuses placeholder content before
sealing") as part of the ``dac_world`` migration — see
``sprints/2026-07-19-dac-generates-arail-worlds/ARCHITECTURE.md`` (qukaizen-ddac).
"""

from __future__ import annotations

import re
from typing import Any

# Full-string anchor: 3+ chars, ENTIRELY X/Y (case-insensitive) — a filler
# shape ("XXXX", "yxyxyx"), not a substring match, so "X-ray" (mixed chars)
# never trips it.
_PLACEHOLDER_SHAPE_RE = re.compile(r"^[XY]{3,}$", re.I)
_TODO_RE = re.compile(r"\bTODO\b", re.I)
_TBD_RE = re.compile(r"\bTBD\b", re.I)
_LOREM_IPSUM_RE = re.compile(r"lorem ipsum", re.I)
_PLACEHOLDER_WORD_RE = re.compile(r"\bplaceholder\b", re.I)
# A run of one repeated non-word character, e.g. "----" or "....." (>=4).
_REPEATED_CHAR_RE = re.compile(r"(\W)\1{3,}")

# face fields that carry authored prose and must be checked (per the
# _FACE_DISPLAY_KEYS allow-list, excluding palette_hint/theme which are not
# free-text prose).
_FACE_CONTENT_KEYS = ("name", "tagline", "domain_framing", "vocabulary_register")


class ContentInvalid(Exception):
    """A display/definition string is placeholder-shaped (the XXXX/YYYY incident).

    Raised by ``validate_bundle_content``. Never written to disk when raised —
    callers (``write_bundle``, ``reseal_bundle``) check this before any file
    write, mirroring the ``GateRefused`` no-partial-write guarantee.
    """

    def __init__(self, violations: list[str], message: str = "placeholder/garbage content refused"):
        super().__init__(message)
        self.violations = violations


def _check_content_field(value: Any, *, required: bool, path: str, violations: list[str]) -> None:
    s = str(value or "").strip()
    if not s:
        if required:
            violations.append(f"{path}: empty")
        return
    if _PLACEHOLDER_SHAPE_RE.match(s):
        violations.append(f"{path}: placeholder-shaped (all X/Y characters)")
    elif _TODO_RE.search(s):
        violations.append(f"{path}: contains TODO")
    elif _TBD_RE.search(s):
        violations.append(f"{path}: contains TBD")
    elif _LOREM_IPSUM_RE.search(s):
        violations.append(f"{path}: contains lorem ipsum")
    elif _PLACEHOLDER_WORD_RE.search(s):
        violations.append(f"{path}: contains 'placeholder'")
    elif _REPEATED_CHAR_RE.search(s):
        violations.append(f"{path}: repeated-character run")


def validate_bundle_content(face: dict, spec: dict, terms: list[dict]) -> None:
    """Refuse placeholder/garbage content before it is ever sealed.

    Pure, deterministic, no I/O. Checks ``face``'s display fields (name,
    tagline, domain_framing, vocabulary_register) and each term's
    short/definition (required) and example (optional — the forge legitimately
    leaves it blank on a bad model call). ``spec`` is accepted for interface
    parity with the architecture's Interface Contracts section; the fields
    validated today all live on ``face``/``terms``.

    Raises ``ContentInvalid`` (never writes) on the first pass over all
    fields — the exception carries every violation found, not just the first,
    so an operator can fix them all in one pass.
    """
    violations: list[str] = []
    for key in _FACE_CONTENT_KEYS:
        _check_content_field(face.get(key), required=True, path=f"face.{key}", violations=violations)
    for t in terms:
        slug = str(t.get("slug") or "?")
        _check_content_field(t.get("short"), required=True, path=f"term[{slug}].short", violations=violations)
        _check_content_field(t.get("definition"), required=True, path=f"term[{slug}].definition", violations=violations)
        _check_content_field(t.get("example"), required=False, path=f"term[{slug}].example", violations=violations)
    if violations:
        raise ContentInvalid(violations)
