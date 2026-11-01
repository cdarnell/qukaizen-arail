"""Debt Advisor — reads the mounted debt-finance World and narrates it.

    An agent is a loop that notices things and speaks up.

Debt Advisor never reads or writes anything under ``lab/pkb/agents/``. Its
only output is ``lab/data/user-import/debt-finance/findings/debt_advisor.md``
(never ``decisions.md``, never anywhere under ``lab/pkb/`` — see
``sprints/2026-07-26-world-of-debt-finance/ARCHITECTURE.md`` §0.1/§6).

The mental model, mirroring ``_builtin_buddy.py``'s shape:

    1. Host      — the only seam to the outside world (DebtAdvisorHost)
    2. Facts     — deterministic extraction from the mounted World's terms.json
    3. Guardrail — arail.agents.debt_finance_compliance.check_guardrail
    4. Loop      — the heartbeat (DebtAdvisorAgent._run)
    5. Memory    — state.json (hash + timestamp + count ONLY, never a figure)

Numeric/institutional integrity (ARCHITECTURE.md §7.5): every institution
name and source URL in the output is inserted verbatim from a term's
structured field. The model, when consulted at all, narrates the
surrounding prose — it never supplies the fact itself. v1 keeps this
property trivially airtight by not attempting to extract a rate from an
approved scouting finding's raw excerpt at all (scouting findings are
unstructured fetched text, not a structured rate record — see BUILD_LOG.md
Phase C for why): Debt Advisor cites an approved finding only by its World/
watch/feed/date metadata, never a number pulled out of its excerpt.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from arail.agents.debt_finance_compliance import (
    Segment,
    check_guardrail,
    find_mounted_bundle_dir,
    is_verification_fresh,
    read_disclaimer,
)

WORLD_SLUG = "debt-finance"
AGENT_ID = "debt_advisor"


# ══════════════════════════════════════════════════════════════════════
#  0. HOST — the only seam between Debt Advisor and its environment
# ══════════════════════════════════════════════════════════════════════

@runtime_checkable
class DebtAdvisorHost(Protocol):
    def emit(self, source: str, message: str, level: str = "info",
              data: Optional[Dict[str, Any]] = None) -> None: ...

    def get_pkb_root(self) -> Optional[Path]: ...

    def get_data_dir(self) -> Optional[Path]: ...

    def llm_complete(self, prompt: str, max_tokens: int = 120,
                      temperature: float = 0.4) -> str: ...


class ArailHost:
    def emit(self, source: str, message: str, level: str = "info",
              data: Optional[Dict[str, Any]] = None) -> None:
        from arail.activity import activity_log
        activity_log.emit(source, message, level, data)

    def get_pkb_root(self) -> Optional[Path]:
        try:
            from arail.pkb import _pkb_root
            return _pkb_root()
        except Exception:
            return None

    def get_data_dir(self) -> Optional[Path]:
        try:
            from arail.config import DATA_DIR
            return Path(DATA_DIR)
        except Exception:
            return None

    def llm_complete(self, prompt: str, max_tokens: int = 120,
                      temperature: float = 0.4) -> str:
        try:
            from arail.agents import deep_policy
            text = deep_policy.complete_preferring_deep(
                prompt, foreground=False,
                max_tokens=max_tokens, temperature=temperature,
            )
            return text or ""
        except Exception:
            return ""


_host: DebtAdvisorHost = ArailHost()


def _state_file() -> Path:
    pkb = _host.get_pkb_root()
    if pkb is None:
        return Path.home() / ".debt_advisor" / "state.json"
    return pkb / "agents" / AGENT_ID / "state.json"


def _findings_file() -> Path:
    data_dir = _host.get_data_dir()
    root = data_dir if data_dir is not None else Path("lab/data")
    return root / "user-import" / WORLD_SLUG / "findings" / "debt_advisor.md"


def _proposed_scenarios_file() -> Path:
    data_dir = _host.get_data_dir()
    root = data_dir if data_dir is not None else Path("lab/data")
    return root / "user-import" / WORLD_SLUG / "proposed_scenarios.md"


def _relative_pointer(path: Path) -> str:
    """A short, non-identifying pointer to ``path`` for the activity
    stream — see ``_builtin_consolidation_analyzer._relative_pointer``
    (TEST_REPORT.md F7); identical fix, same reasoning, applied here too."""
    data_dir = _host.get_data_dir()
    root = data_dir if data_dir is not None else Path("lab/data")
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


# ══════════════════════════════════════════════════════════════════════
#  2. FACTS — deterministic extraction from the mounted World
# ══════════════════════════════════════════════════════════════════════

@dataclass
class VettedInstitution:
    name: str
    institution_type: str
    verification_source: str
    verified_as_of: str


def _load_terms(bundle_dir: Path) -> List[Dict[str, Any]]:
    """Returns only dict entries from ``terms.json``'s ``terms`` list.

    TEST_REPORT.md F9: a hand-edited ``terms.json`` with a non-dict entry
    (e.g. a stray string) used to raise ``AttributeError`` out of
    ``_vetted_institutions``'s ``t.get(...)`` calls — the same
    permanent-loop-death shape as F1, sourced from the World bundle rather
    than operator input. Filtering non-dict entries here, rather than at
    every downstream ``.get()`` call site, keeps this the single place that
    enforces "an entry is a dict" for this file.
    """
    try:
        doc = json.loads((bundle_dir / "terms.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(doc, dict):
        raw = list(doc.get("terms") or [])
    elif isinstance(doc, list):
        raw = doc
    else:
        return []
    return [t for t in raw if isinstance(t, dict)]


def _vetted_institutions(terms: List[Dict[str, Any]]) -> List[VettedInstitution]:
    """Specific, named, verified institutions Debt Advisor may pair with
    "credit union"/"nonprofit" language (ARCHITECTURE.md §3.3/§4.3).

    ``category == "institutions"`` alone is not enough: that category also
    holds generic glossary/concept terms (e.g. "Credit Union", "Credit
    Counseling Agency") that explain what a kind of institution IS, not a
    claim about any specific real-world entity. Only entries that carry an
    ``institution_type`` field are actual named, verified institutions —
    that field is the distinguishing marker terms.json's authoring
    convention requires before a term counts as "vetted" for guardrail or
    substitution purposes (see terms.json's PenFed Credit Union / GreenPath
    Financial Wellness entries for the shape).

    A fourth condition, ``verified_as_of``, is also required: a valid ISO
    date within the staleness threshold (``is_verification_fresh``). A
    sealed bundle cannot notice when a real institution's charter or
    membership status lapses, so an institution missing this field, or
    whose verification has gone stale, is simply not vetted — the
    mechanism degrades closed rather than asserting an unqualified fact
    forever.
    """
    out = []
    for t in terms:
        institution_type = t.get("institution_type")
        verified_as_of = str(t.get("verified_as_of") or "")
        if (t.get("category") == "institutions" and institution_type
                and t.get("verification_source")
                and is_verification_fresh(verified_as_of)):
            out.append(VettedInstitution(
                name=str(t.get("term") or t.get("slug") or ""),
                institution_type=str(institution_type),
                verification_source=str(t.get("verification_source")),
                verified_as_of=verified_as_of,
            ))
    return out


def _terms_content_hash(bundle_dir: Path) -> str:
    try:
        raw = (bundle_dir / "terms.json").read_bytes()
    except OSError:
        return ""
    return hashlib.sha256(raw).hexdigest()


def _approved_finding_count(pkb_root: Optional[Path]) -> int:
    """Count of approved scout findings for this World — used only for the
    no-op fingerprint, never for content. See _write_findings for how an
    approved finding is cited (metadata only, never a parsed figure)."""
    if pkb_root is None:
        return 0
    try:
        from arail import compiled_kb
        approved = compiled_kb.approved_paths(pkb_root)
    except Exception:
        return 0
    prefix = f"sources/scout/{WORLD_SLUG}-"
    return sum(1 for p in approved if p.startswith(prefix))


def _approved_findings(pkb_root: Optional[Path]) -> List[Dict[str, str]]:
    """Metadata (World/watch/feed/date) for each approved finding — never
    the excerpt content, and never a number parsed out of it."""
    if pkb_root is None:
        return []
    try:
        from arail import compiled_kb
        approved = compiled_kb.approved_paths(pkb_root)
    except Exception:
        return []
    out: List[Dict[str, str]] = []
    prefix = f"sources/scout/{WORLD_SLUG}-"
    for rel in sorted(approved):
        if not rel.startswith(prefix):
            continue
        path = pkb_root / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        meta: Dict[str, str] = {"path": rel}
        for line in text.splitlines():
            for key in ("Watch", "Feed", "Checked"):
                if line.startswith(f"- {key}:"):
                    meta[key.lower()] = line.split(":", 1)[1].strip()
        out.append(meta)
    return out


# ══════════════════════════════════════════════════════════════════════
#  2b. PROPOSED SCENARIOS (Workstream C: operator stays the confirmer)
# ══════════════════════════════════════════════════════════════════════
#
# agenda_watch.py's generic World-declared extraction patterns (see that
# module) can surface literal "candidate values" inside an approved
# finding — e.g. a matched APR-shaped substring from a rate page. Nothing
# in this codebase ever promotes one of those into a fact: this section
# only ever QUOTES them back to the operator, verbatim, labeled unverified,
# with explicit instructions to hand-type any value they want into
# balances.json themselves. That keeps ARCHITECTURE.md §7.5's numeric-
# integrity property intact — the operator remains the sole confirmer of
# any figure that ever reaches a candidate_scenarios entry, exactly as
# BUILD_LOG.md's Phase C reasoning already established for why this agent
# never parses a rate out of an excerpt on its own.

_CANDIDATE_SECTION_RE = re.compile(
    r"^## Candidate values \(code-extracted, unverified\)\s*$", re.M)
_CANDIDATE_LINE_RE = re.compile(r"^- \*\*(?P<label>[^*]+)\*\*:\s*(?P<values>.+)$", re.M)
_BACKTICK_VALUE_RE = re.compile(r"`([^`]*)`")


def _parse_candidate_values(text: str) -> Dict[str, List[str]]:
    """Parse the "Candidate values" section agenda_watch.py's own writer
    produces (see ``agenda_watch._finding_markdown`` — the two are a
    matched writer/reader pair, exercised together in
    tests/test_agenda_watch.py and tests/test_debt_finance_agents.py so
    they can't silently drift apart).

    Returns ``{}`` if the section is absent (most findings have none) or
    malformed. This only ever reads back OUR OWN structured writer's
    output — never free-text scraping of arbitrary excerpt/diff content,
    which stays exactly as un-parsed as it always has been.
    """
    section_match = _CANDIDATE_SECTION_RE.search(text)
    if not section_match:
        return {}
    rest = text[section_match.end():]
    next_heading = re.search(r"^## ", rest, re.M)
    body = rest[:next_heading.start()] if next_heading else rest
    out: Dict[str, List[str]] = {}
    for m in _CANDIDATE_LINE_RE.finditer(body):
        label = m.group("label").strip()
        values = _BACKTICK_VALUE_RE.findall(m.group("values"))
        if label and values:
            out[label] = values
    return out


def _approved_finding_candidates(pkb_root: Optional[Path]
                                  ) -> Dict[str, Dict[str, List[str]]]:
    """Candidate values per approved finding path, keyed the same way
    ``_approved_findings`` keys its own metadata (``path``). Reads each
    approved finding's full text — unlike ``_approved_findings``, which
    only extracts three header lines — specifically to reach the
    "Candidate values" section, if present."""
    if pkb_root is None:
        return {}
    try:
        from arail import compiled_kb
        approved = compiled_kb.approved_paths(pkb_root)
    except Exception:
        return {}
    prefix = f"sources/scout/{WORLD_SLUG}-"
    out: Dict[str, Dict[str, List[str]]] = {}
    for rel in sorted(approved):
        if not rel.startswith(prefix):
            continue
        try:
            text = (pkb_root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        candidates = _parse_candidate_values(text)
        if candidates:
            out[rel] = candidates
    return out


def _build_proposed_scenarios(findings: List[Dict[str, str]],
                               candidates_by_path: Dict[str, Dict[str, List[str]]]
                               ) -> Optional[str]:
    """Assemble the operator-facing proposed-scenarios document, or
    ``None`` if no approved finding currently has any candidate values (the
    common case — most Worlds, and most findings, have none).

    Every candidate value is a ``Segment.scouted_unverified(...)`` — a
    literal substring matched out of live-fetched, third-party page text at
    tick time, not World-sealed content. It is deliberately *not*
    ``Segment.world(...)``: a candidate value never passed the preflight
    evaluative-language scan a World's own authoring-time content is
    expected to pass before sealing (see ``debt_finance_compliance``'s
    module docstring), so it is not entitled to WORLD provenance's
    evaluative-check exemption. ``check_guardrail`` evaluative-checks
    ``SCOUTED_UNVERIFIED`` segments exactly like ``AGENT`` ones — if any
    candidate value here trips it, this whole proposed-scenarios document is
    rejected (``_GuardrailBlocked``, caught by the caller) and simply not
    written; the finding itself, and the main findings.md write, are
    unaffected either way. The ``feed``/``checked``/``path`` metadata lines
    below stay ``Segment.world(...)`` — those are the same World-sealed
    scouting-finding fields ``_build_output`` already trusts, not a
    candidate value.
    """
    if not any(candidates_by_path.get(f["path"]) for f in findings):
        return None

    lines: List[List[Segment]] = []
    lines.append([Segment.agent("# Proposed scenarios (unverified — you decide)\n")])
    lines.append([Segment.agent(
        "These are literal values a scouting finding's declared extraction "
        "pattern matched in fetched text. None of them have been checked, "
        "and nothing here has been written to your balances.json "
        "automatically. To use one, copy it into balances.json's "
        "candidate_scenarios[] yourself, and set that entry's `source` "
        "field to the finding path shown below.\n"
    )])

    for f in findings:
        candidates = candidates_by_path.get(f["path"]) or {}
        if not candidates:
            continue
        lines.append([
            Segment.agent("## From "),
            Segment.world(f.get("feed", "?")),
            Segment.agent(" (checked "),
            Segment.world(f.get("checked", "?")),
            Segment.agent(") — see `"),
            Segment.world(f.get("path", "?")),
            Segment.agent("`\n"),
        ])
        for label, values in candidates.items():
            value_line: List[Segment] = [Segment.agent(f"- **{label}**: ")]
            for i, v in enumerate(values):
                if i:
                    value_line.append(Segment.agent(", "))
                value_line.append(Segment.agent("`"))
                value_line.append(Segment.scouted_unverified(v))
                value_line.append(Segment.agent("`"))
            lines.append(value_line)
        lines.append([Segment.agent("")])

    segments: List[Segment] = []
    for i, line_segments in enumerate(lines):
        if i:
            segments.append(Segment.agent("\n"))
        segments.extend(line_segments)

    body = "".join(s.text for s in segments)
    guard = check_guardrail(segments)
    if not guard.ok:
        raise _GuardrailBlocked(guard.reason)
    return body


# ══════════════════════════════════════════════════════════════════════
#  3. WRITE PATH — guardrail, disclaimer, findings file
# ══════════════════════════════════════════════════════════════════════

_DIGIT_RE = re.compile(r"\d")


def _framing_prose(vetted: List[VettedInstitution],
                    findings: List[Dict[str, str]]) -> str:
    """Optional model-generated framing sentence around the code-inserted
    facts below. Falls back to a fixed, deterministic sentence if the model
    is unavailable — the model is never the source of a fact, only of the
    surrounding tone, so this fallback changes nothing material.

    Also rejects a model sentence containing a digit or a vetted
    institution's name — the prompt asks the model not to emit either, but
    nothing enforced that until now (REVIEW.md [ASK]); this is the last
    live path by which a model-generated number or name could reach a
    findings file."""
    prompt = (
        "Write one short, plain, non-evaluative sentence introducing an "
        "educational summary of debt-payoff and consolidation terminology. "
        "Do not name any institution, rate, or number. Do not say 'best' "
        "or give advice."
    )
    text = _host.llm_complete(prompt, max_tokens=60).strip()
    lowered = text.lower()
    if (not text or check_guardrail([Segment.agent(text)]).ok is False
            or _DIGIT_RE.search(text)
            or any(v.name.lower() in lowered for v in vetted)):
        return "Educational summary of this World's debt-finance terms and any approved findings."
    return text


def _build_output(bundle_dir: Path, terms: List[Dict[str, Any]],
                   findings: List[Dict[str, str]]) -> str:
    """Assemble the findings document as an ordered list of provenance-
    tagged segments (ARCHITECTURE.md §13.11's structural refactor, now
    implemented) rather than a flat f-string. Every institution name,
    character label, verification-source URL, verified-as-of date, and
    scouting feed/path is a ``Segment.world(...)`` — World-sealed content,
    inserted verbatim from a term's or finding's structured field, never
    generated by the model. Everything else (headings, connective prose,
    the framing sentence) is ``Segment.agent(...)``. Debt Advisor's content
    is entirely World-sourced or code-authored — it never renders an
    operator-typed value, so no ``Segment.operator(...)`` appears here.

    ``check_guardrail`` then answers every "is this text the agent's own
    words" and "is this institution name actually vetted" question by
    looking at these tags directly — no string matching, no masking, no
    proximity windows.
    """
    vetted = _vetted_institutions(terms)

    # Each entry is the list of Segments for one logical line; joined with
    # an AGENT "\n" segment between entries below, mirroring the previous
    # ``"\n".join(lines)`` assembly exactly.
    lines: List[List[Segment]] = []
    lines.append([Segment.agent("# Debt Advisor — Findings\n")])
    lines.append([Segment.agent(_framing_prose(vetted, findings) + "\n")])

    lines.append([Segment.agent(
        "## Institutions whose character claims this World verified\n"
    )])
    # Code-inserted, never model-generated (REVIEW.md addendum, condition
    # (a)): a short named-institution list under a "vetted institutions"
    # heading reads as a shortlist regardless of surrounding prose. This
    # roster exists to demonstrate what verifying an institutional-
    # character claim looks like, not to recommend or exhaustively list
    # institutions.
    lines.append([Segment.agent(
        "_This list is not exhaustive and does not rank or endorse any "
        "institution. It exists to show what verification of an "
        "institutional-character claim looks like, against a source other "
        "than the institution's own marketing._\n"
    )])
    if vetted:
        for v in vetted:
            # Every name, character label, verification-source URL, and
            # verified-as-of date below is a Segment.world(...) — inserted
            # verbatim from the term's own structured fields, never a
            # hardcoded string, never generated by the model. Each
            # institution's actual institution_type is what gets printed,
            # so a credit-counseling agency is never mislabeled as a credit
            # union (BLOCK-2). The verified-as-of date is rendered next to
            # the citation so staleness is visible on the document's face,
            # not just internal bookkeeping (REVIEW.md addendum, condition
            # (b)).
            character = v.institution_type.replace("-", " ")
            lines.append([
                Segment.agent("- **"),
                Segment.world(v.name, is_name=True),
                Segment.agent("** ("),
                Segment.world(character),
                Segment.agent(", verification source: "),
                Segment.world(v.verification_source),
                Segment.agent(", verified as of "),
                Segment.world(v.verified_as_of),
                Segment.agent(")"),
            ])
    else:
        lines.append([Segment.agent(
            "- No vetted institutions in the mounted World's terms."
        )])
    lines.append([Segment.agent("")])

    lines.append([Segment.agent(
        "## Approved scouting findings (public sources only)\n"
    )])
    if findings:
        for f in findings:
            # ``feed`` and ``path`` are the externally-authored (RSS
            # source's own title / mounted-World-relative path) text this
            # agent quotes back verbatim — not this agent's own
            # characterization of anything. Tagged Segment.world(...) for
            # the same reason a vetted institution's name is above: a
            # reader must be able to tell this is a third party's own
            # wording, not Debt Advisor's, and the evaluative-language
            # check can never fire on it no matter what words a feed title
            # contains (e.g. "Best Balance Transfer Cards - Bankrate").
            lines.append([
                Segment.agent("- Found via "),
                Segment.world(f.get("feed", "?")),
                Segment.agent(" (quoted verbatim), checked "),
                Segment.world(f.get("checked", "?")),
                Segment.agent(" — see `"),
                Segment.world(f.get("path", "?")),
                Segment.agent(
                    "` (quoted verbatim) for the reviewed excerpt. No "
                    "institutional-character label is attached unless the "
                    "source is also a vetted institution above."
                ),
            ])
    else:
        lines.append([Segment.agent("- No approved scouting findings yet.")])
    lines.append([Segment.agent("")])

    segments: List[Segment] = []
    for i, line_segments in enumerate(lines):
        if i:
            segments.append(Segment.agent("\n"))
        segments.extend(line_segments)

    body = "".join(s.text for s in segments)
    guard = check_guardrail(segments)
    if not guard.ok:
        raise _GuardrailBlocked(guard.reason)
    return body


class _GuardrailBlocked(Exception):
    pass


def _safe_write_0600(path: Path, content: str) -> bool:
    """Write ``content`` to ``path``, refusing to follow a pre-placed
    symlink. See ``_builtin_consolidation_analyzer._safe_write_0600``
    (TEST_REPORT.md F5) — identical fix, same reasoning, shared shape,
    applied here too. Not literally shared code (each agent module keeps
    its own copy, matching this codebase's existing per-agent duplication
    of ``_write_findings``/``_relative_pointer``/etc.), but byte-for-byte
    the same logic.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError:
        return False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError:
        return False
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        pass
    return True


def _write_findings(text: str, disclaimer: str) -> bool:
    """Write the findings file. See ``_safe_write_0600``."""
    content = text.rstrip() + "\n\n---\n\n" + disclaimer
    return _safe_write_0600(_findings_file(), content)


def _write_proposed_scenarios(text: str) -> bool:
    """Write the proposed-scenarios file. No disclaimer footer — this
    document is neither a financial analysis nor a recommendation, it's a
    plain listing of what a scouting finding's pattern matched, already
    labeled "unverified — you decide" in its own heading."""
    return _safe_write_0600(_proposed_scenarios_file(), text.rstrip() + "\n")


# ══════════════════════════════════════════════════════════════════════
#  4/5. LOOP + MEMORY
# ══════════════════════════════════════════════════════════════════════

class DebtAdvisorAgent:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._status = "idle"
        self._last_terms_hash = ""
        self._last_finding_count = ""
        self._last_run_at: float = 0.0

    @property
    def status(self) -> str:
        return self._status

    def _load_state(self) -> None:
        path = _state_file()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
        except Exception:
            return
        # state.json may only ever hold a hash, a timestamp, and a count —
        # never a raw balance/APR/institution name (ARCHITECTURE.md §7 new
        # constraint; enforced by construction, this is all we ever store).
        self._last_terms_hash = str(data.get("terms_hash") or "")
        # TEST_REPORT.md F6: this field now stores a fingerprint over the
        # approved findings' *identity* (see tick()), not a bare count —
        # kept under the same state.json key (schema/key-set unchanged) for
        # back-compat, but read back as a string rather than cast to int.
        self._last_finding_count = str(data.get("approved_finding_count") or "")
        self._last_run_at = float(data.get("last_run_at") or 0.0)

    def _save_state(self) -> None:
        path = _state_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "terms_hash": self._last_terms_hash,
                "approved_finding_count": self._last_finding_count,
                "last_run_at": self._last_run_at,
            }, indent=2))
        except OSError:
            pass

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._load_state()
        self._status = "running"
        self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._status = "idle"

    async def _run(self) -> None:
        interval = max(60, int(os.getenv("LAB_DEBT_ADVISOR_INTERVAL_SEC", "86400")))
        try:
            while True:
                await asyncio.sleep(interval)
                try:
                    self.tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # TEST_REPORT.md F1: same tick-loop robustness fix as
                    # Consolidation Analyzer — this agent shares the
                    # identical bare-except-CancelledError-only structure,
                    # so any future bug in World-content parsing (see also
                    # F9) must not permanently kill the loop.
                    _host.emit(
                        AGENT_ID,
                        "Debt Advisor: an unexpected error occurred during "
                        "a scheduled check — skipped this cycle.",
                        "warn",
                    )
        except asyncio.CancelledError:
            return

    def tick(self) -> None:
        """One tick: no-op if nothing changed, else compute + guardrail +
        disclaimer + write. Never crashes; always logs a path-only pointer
        or a non-identifying warning, never a figure."""
        bundle_dir = find_mounted_bundle_dir()
        if bundle_dir is None:
            return  # No World mounted — nothing to do, not an error.

        terms = _load_terms(bundle_dir)
        terms_hash = _terms_content_hash(bundle_dir)
        pkb_root = _host.get_pkb_root()
        findings = _approved_findings(pkb_root)
        # TEST_REPORT.md F6: fingerprint on the approved findings' identity
        # (path/feed/checked-date), not a bare count — swapping one approved
        # finding for another at the same total count used to leave the
        # cited feed/date metadata silently stale. Also check whether the
        # findings file still exists, so an operator-deleted findings file
        # (§6.5's documented v1 "forget" story) gets regenerated rather than
        # permanently suppressed.
        findings_fingerprint = hashlib.sha256(
            json.dumps(findings, sort_keys=True).encode("utf-8")
        ).hexdigest()
        findings_path = _findings_file()

        if (terms_hash == self._last_terms_hash
                and findings_fingerprint == self._last_finding_count
                and findings_path.exists()):
            return  # True no-op: no LLM call, no write, no activity event.

        disclaimer = read_disclaimer(bundle_dir)
        if disclaimer is None:
            _host.emit(
                AGENT_ID,
                "Debt Advisor: compliance/DISCLAIMER.md missing or altered — "
                "refusing to write findings until restored.",
                "warn",
            )
            return

        try:
            body = _build_output(bundle_dir, terms, findings)
        except _GuardrailBlocked as exc:
            reason = exc.args[0] if exc.args else ""
            # REVIEW.md addendum 2 [ASK-B]: name the reason rather than a
            # bare "see logs" pointer. Unlike Consolidation Analyzer, this
            # path is entirely World content (terms.json / scouting
            # findings) — nothing the operator typed can fix it, so point at
            # the mounted World instead.
            _host.emit(
                AGENT_ID,
                "Debt Advisor: generated output failed the language-safety "
                f"check ({reason}) and was not written. This indicates the "
                "mounted World's terms.json or an approved scouting finding "
                "names an institution with institutional-character language "
                "that isn't in this World's vetted set — check the World's "
                "content, not your own data.",
                "warn",
                data={"reason": reason},
            )
            return

        if not _write_findings(body, disclaimer):
            # TEST_REPORT.md F5: refuses rather than following a
            # pre-placed symlink at the findings path.
            _host.emit(
                AGENT_ID,
                "Debt Advisor: could not write the findings file — the "
                "destination is not a regular file.",
                "warn",
            )
            return

        # Workstream C (find good deals -> operator confirms): if any
        # approved finding has candidate values (see agenda_watch.py's
        # World-declared extraction patterns), surface them as a separate,
        # explicitly-unverified document. Best-effort — a problem here must
        # never undo the findings write that already succeeded above. The
        # main findings_fingerprint already changes whenever a finding's
        # content (and therefore its candidates) changes, since each
        # finding's own filename encodes a hash of its content — so this
        # needs no separate fingerprint input to stay in step with it.
        try:
            candidates_by_path = _approved_finding_candidates(pkb_root)
            proposed_body = _build_proposed_scenarios(findings, candidates_by_path)
            if proposed_body is not None:
                _write_proposed_scenarios(proposed_body)
        except _GuardrailBlocked:
            _host.emit(
                AGENT_ID,
                "Debt Advisor: an approved finding's candidate values "
                "failed the language-safety check and were not surfaced — "
                "the finding itself is unaffected.",
                "warn",
            )
        except Exception:  # noqa: BLE001 — best-effort, never fatal
            pass

        self._last_terms_hash = terms_hash
        self._last_finding_count = findings_fingerprint
        self._last_run_at = time.time()
        self._save_state()

        # F9 (ARCHITECTURE.md): this is the proactive-speech moment (the
        # finding's framing sentence went through the model funnel above);
        # gated separately from inference — the finding is still written,
        # only the announcement is silenced while held.
        from arail import agent_context
        if agent_context.speech_gate(AGENT_ID):
            _host.emit(
                AGENT_ID,
                "Debt Advisor produced a new finding — see "
                f"{_relative_pointer(_findings_file())}",
                "info",
            )


debt_advisor = DebtAdvisorAgent()
