"""redact.py — the only function in the codebase permitted to put a body
into a trace record.

``capture_body`` cannot return unredacted text: it either returns a dict
whose strings have passed both redaction passes, or ``None``. Any exception
inside it is caught and returns ``None`` — fail-closed on bodies, fail-open
on the metadata record that still gets written around it. That is the
structural enforcement of "secrets are never logged", rather than a rule
someone must remember.

Two passes, both always applied, in this order:

1. **Known-value pass.** Every value from ``secrets.env`` (>= 8 chars),
   plus ``ARAIL_PASSWORD`` and ``OPEN_NOTEBOOK_ENCRYPTION_KEY``. The
   *shape* of this pass is lifted from ``portal/services/opencode.py``'s
   proven ``_RedactingLogWriter`` (replace known values with a fixed
   marker); the secrets.env parsing here is intentionally independent of
   ``portal.app._read_secrets()`` rather than importing it, so this module
   stays a small, dependency-light leaf that ``agent_trace.py`` and
   ``router/core.py`` can import without pulling in the whole portal.
2. **Shape pass.** Catches secrets that were never in ``secrets.env`` —
   exactly what a QA-planted key-shaped string exercises.

**Order matters: redact, then truncate.** Truncating first could split a
key so the shape pass no longer matches its head fragment. Caps after
redaction: prompt <= 2000 chars, response <= 1000 — a reduction from the
pre-existing 3000/2000 in researcher.py/browser.py's activity_log bodies.
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Optional

REDACTED = "***REDACTED***"

_MIN_SECRET_LEN = 8
_PROMPT_CAP = 2000
_RESPONSE_CAP = 1000

# Shape pass: patterns for secrets that were never in secrets.env.
_SHAPE_PATTERNS: tuple["re.Pattern[str]", ...] = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"nvapi-\S{16,}"),
    re.compile(r"(?:ghp|gho|github_pat)_\S{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}"),
    re.compile(
        r"(?i)\b(api[_-]?key|token|secret|password|passphrase)\b\s*[:=]\s*\S{8,}"
    ),
)

_cache_lock = threading.Lock()
_cache_mtime: Optional[float] = None
_cache_values: tuple[str, ...] = ()


def _secrets_path() -> Path:
    from arail import config
    override = os.getenv("ARAIL_SECRETS_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    return config.DATA_DIR / "secrets.env"


class _SecretsUnreadable(Exception):
    """Raised by :func:`_parse_secrets_env` when ``secrets.env`` *exists*
    but could not be read (REVIEW.md R1) — a permissions failure, an IO
    error, or any other read-time ``OSError``/``ValueError``. Deliberately
    distinct from "file absent" (the caller already checked
    ``path.exists()``) and from "file present and empty", both of which
    are legitimately zero known values: "couldn't read it" must never
    collapse into "found nothing", the same shape as the bug B1 closed.
    Caught by :func:`redact` (lenient, degrades to shape-pass-only) and
    deliberately NOT caught by :func:`_redact_strict` (propagates to
    :func:`capture_body`'s fail-closed ``None``)."""


def _parse_secrets_env(path: Path) -> list[str]:
    """Best-effort parse. ``errors="replace"`` so a ``secrets.env`` with one
    stray non-UTF-8 byte (a key pasted from a terminal, a file written by a
    non-Python tool) still yields every *other* line's value instead of
    raising ``UnicodeDecodeError`` (a ``ValueError`` subclass) and losing the
    whole pass — see REVIEW.md B1's other half, which this preserves: a
    mangled-but-readable file still decodes here and still redacts every
    other line's value.

    R1's narrower case is a *read* failure, not a decode one — the file
    exists but ``read_text()`` itself raises (permissions, IO). That is
    signalled by raising :class:`_SecretsUnreadable` rather than
    returning ``[]``, so it cannot be mistaken for "the file has no
    usable lines"."""
    try:
        text = path.read_text(errors="replace")
    except (OSError, ValueError) as e:
        raise _SecretsUnreadable(str(e)) from e
    values: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        _, _, v = line.partition("=")
        v = v.strip()
        if len(v) >= _MIN_SECRET_LEN:
            values.append(v)
    return values


def _known_values() -> tuple[str, ...]:
    """Cached known-secret values, refreshed on secrets.env mtime change."""
    global _cache_mtime, _cache_values
    path = _secrets_path()
    try:
        mtime = path.stat().st_mtime if path.exists() else None
    except OSError:
        mtime = None

    with _cache_lock:
        if mtime is not None and mtime == _cache_mtime:
            return _cache_values

        values = _parse_secrets_env(path) if path.exists() else []
        for env_key in ("ARAIL_PASSWORD", "OPEN_NOTEBOOK_ENCRYPTION_KEY"):
            v = os.getenv(env_key, "").strip()
            if len(v) >= _MIN_SECRET_LEN:
                values.append(v)
        # Longest first so a longer secret that happens to contain a
        # shorter one is fully replaced rather than leaving a fragment.
        values.sort(key=len, reverse=True)
        _cache_values = tuple(values)
        _cache_mtime = mtime
        return _cache_values


def redact(text: str) -> tuple[str, int]:
    """Run both passes, always both, in order. Returns ``(redacted, n)``.
    Never raises — a malformed pattern or cache-refresh failure degrades to
    "no known-value pass this call", not an exception into the caller."""
    if not text:
        return text, 0
    out = text
    n = 0
    try:
        for value in _known_values():
            if value and value in out:
                n += out.count(value)
                out = out.replace(value, REDACTED)
    except Exception:  # noqa: BLE001
        pass
    for pattern in _SHAPE_PATTERNS:
        out, count = pattern.subn(REDACTED, out)
        n += count
    return out, n


def _redact_strict(text: str) -> tuple[str, int]:
    """Like :func:`redact`, but does **not** swallow a known-value-pass
    failure (REVIEW.md B1). ``redact()`` stays lenient — best-effort
    redaction degrading to "shape pass only" — because it has callers
    (and tests) that only ever redact *display* text, never a body headed
    to disk. ``capture_body`` is the one caller for whom "the known-value
    pass silently did nothing" must never look identical to "the
    known-value pass ran and found nothing" — so this variant lets a
    failure inside ``_known_values()`` propagate, and ``capture_body``'s
    own outer ``except`` turns that into a fail-closed ``None`` instead of
    an unredacted (or partially redacted) body reaching disk."""
    if not text:
        return text, 0
    out = text
    n = 0
    for value in _known_values():  # deliberately no try/except here
        if value and value in out:
            n += out.count(value)
            out = out.replace(value, REDACTED)
    for pattern in _SHAPE_PATTERNS:
        out, count = pattern.subn(REDACTED, out)
        n += count
    return out, n


def capture_body(prompt: Optional[str], response: Optional[str]) -> Optional[dict]:
    """The only function permitted to put a body into a trace record.

    Fail-closed on bodies: any exception here — including a known-value-
    pass failure inside :func:`_redact_strict` — returns ``None`` (the
    metadata record around it is still written by the caller — this
    function never blocks that). Redact-then-truncate, always, both
    fields.
    """
    try:
        p = prompt if isinstance(prompt, str) else ""
        r = response if isinstance(response, str) else ""
        p_redacted, p_n = _redact_strict(p)
        r_redacted, r_n = _redact_strict(r)
        truncated = len(p_redacted) > _PROMPT_CAP or len(r_redacted) > _RESPONSE_CAP
        return {
            "prompt": p_redacted[:_PROMPT_CAP],
            "response": r_redacted[:_RESPONSE_CAP],
            "truncated": truncated,
            "redactions": p_n + r_n,
        }
    except Exception:  # noqa: BLE001 - fail-closed on bodies
        return None


def _reset_cache_for_tests() -> None:
    global _cache_mtime, _cache_values
    with _cache_lock:
        _cache_mtime = None
        _cache_values = ()
