"""Tier-0 PII-leak gate — record-instance identifiers never become terms.

The vault design's F12 / acceptance A5 (``docs/endgame/vault-and-pq.md``,
qukaizen-dac): the *derivation* of a term from a sensitive record is fine —
a W-2 legitimately creates the "wage-and-tax-boxes" vocabulary — but a
**record-instance value** (this user's SSN, EIN, account number) must never
land in a Tier-0 term, because Tier-0 is the shareable, cleartext bundle.
If that leaks, the vault's encryption is moot: the PII rode out in the
public artifact.

Scope, stated honestly:

- ENFORCED HERE (regex-decidable, high precision): SSN-shaped
  (``###-##-####``), EIN-shaped (``##-#######``), long contiguous digit
  runs (account-like, >= 9 digits), and Luhn-valid 13–19 digit runs
  (payment-card-like). All-zero matches are exempt — ``00-0000000`` /
  ``000-00-0000`` are the standard way vocabulary shows a *format*, and
  the repo's corpora already use exactly that convention (X-masked forms
  like ``XXX-XX-0000`` never match a digit pattern in the first place).
- NOT decidable by pattern (a real wage figure, a real analyte value, a
  merchant+amount pair): those are indistinguishable from illustrative
  examples by regex. The reconcile loop must gate them by *provenance* —
  a term drafted from a Tier-2 record gets held for review — which lands
  with the live reconcile wiring, not here.

Same contract as ``gate.py``: pure, total, deterministic, stdlib-only,
never raises from the scan itself. ``assert_no_record_instance_pii``
wraps the scan for callers that want a refusal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# High-precision instance-identifier shapes. Order matters only for
# reporting; every pattern is applied to every string field.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Separator class [- ] catches the spaced variants OCR and typists
    # produce ("123 45 6789") — dashless runs fall to digit-run below.
    ("ssn", re.compile(r"\b\d{3}[- ]\d{2}[- ]\d{4}\b")),
    ("ein", re.compile(r"\b\d{2}[- ]\d{7}\b")),
    # 13-19 contiguous digits, validated by Luhn below -> payment card.
    ("card", re.compile(r"\b\d{13,19}\b")),
    # 9+ contiguous digits (bank account / routing / MRN-like). The card
    # pattern wins where both match a 13-19 run that passes Luhn.
    ("digit-run", re.compile(r"\b\d{9,}\b")),
)


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _is_format_specimen(match: str) -> bool:
    """All-zero values are format illustrations, not instance values."""
    return set(match) <= {"0", "-", " "}


@dataclass
class PiiHit:
    slug: str
    fld: str
    kind: str
    match: str


@dataclass
class PiiScanResult:
    ok: bool = True
    hits: list[PiiHit] = field(default_factory=list)


class PiiRefused(Exception):
    """A term carries a record-instance identifier (F12)."""

    def __init__(self, result: PiiScanResult):
        super().__init__(
            "record-instance PII in Tier-0 terms: "
            + "; ".join(f"{h.slug}.{h.fld} [{h.kind}] {h.match!r}" for h in result.hits)
        )
        self.result = result


def _iter_strings(value: Any, fld: str):
    if isinstance(value, str):
        yield fld, value
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from _iter_strings(v, f"{fld}[{i}]")
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _iter_strings(v, f"{fld}.{k}")


def scan_terms_for_record_pii(terms: list[dict]) -> PiiScanResult:
    """Scan every string field of every term. Pure and total: malformed
    terms contribute whatever string fields they do have; empty input is
    vacuously ok."""
    result = PiiScanResult()
    for t in terms:
        slug = t.get("slug") if isinstance(t, dict) else None
        slug = slug if isinstance(slug, str) and slug.strip() else "<missing-slug>"
        if not isinstance(t, dict):
            continue
        for fld, text in _iter_strings(t, ""):
            fld = fld.lstrip(".") or "<root>"
            claimed: set[tuple[int, int]] = set()
            for kind, pattern in _PATTERNS:
                for m in pattern.finditer(text):
                    span = m.span()
                    if any(span[0] < c1 and span[1] > c0 for c0, c1 in claimed):
                        continue  # already reported by a stronger pattern
                    match = m.group(0)
                    if _is_format_specimen(match):
                        continue
                    if kind == "card" and not _luhn_ok(match):
                        continue  # not card-shaped; digit-run may still claim it
                    result.hits.append(PiiHit(slug=slug, fld=fld, kind=kind, match=match))
                    result.ok = False
                    claimed.add(span)
    return result


def assert_no_record_instance_pii(terms: list[dict]) -> PiiScanResult:
    """Raise ``PiiRefused`` if any term carries an instance identifier;
    return the (ok) scan result otherwise."""
    result = scan_terms_for_record_pii(terms)
    if not result.ok:
        raise PiiRefused(result)
    return result
