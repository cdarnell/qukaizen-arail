"""Consolidation Analyzer — pure arithmetic over the operator's own balances.

    An agent is a loop that notices things and speaks up.

Reads ``lab/data/user-import/debt-finance/balances.json`` (never
``lab/pkb/``) and computes, deterministically in code: blended APR, monthly
interest cost, and break-even timelines for candidate balance-transfer /
consolidation-loan scenarios. Every number in the output is inserted
verbatim from a code computation over the operator's own staged input — the
model narrates around the numbers, it never retypes or estimates them
(ARCHITECTURE.md §5.2/§7.5).

    1. Host      — the only seam to the outside world
    2. Arithmetic — blended_apr / monthly_interest_cost / breakeven_months
       (pure functions, independently unit-testable, no I/O)
    3. Input     — balances.json schema + the three specified parse-failure
       behaviors (§6.1: absent -> no-op; valid -> normal tick; malformed ->
       one non-specific warning, zero content echo, no crash)
    4. Guardrail + disclaimer + write — same shared module as Debt Advisor
    5. Loop      — the heartbeat
    6. Memory    — state.json (hash + timestamp + count ONLY)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from arail.agents.debt_finance_compliance import (
    REASON_EVALUATIVE,
    REASON_INSTITUTIONAL_PREFIX,
    Segment,
    check_guardrail,
    find_mounted_bundle_dir,
    is_verification_fresh,
    read_disclaimer,
)

_log = logging.getLogger(__name__)

WORLD_SLUG = "debt-finance"
AGENT_ID = "consolidation_analyzer"


# ══════════════════════════════════════════════════════════════════════
#  1. HOST
# ══════════════════════════════════════════════════════════════════════

@runtime_checkable
class ConsolidationAnalyzerHost(Protocol):
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


_host: ConsolidationAnalyzerHost = ArailHost()


def _state_file() -> Path:
    pkb = _host.get_pkb_root()
    if pkb is None:
        return Path.home() / ".consolidation_analyzer" / "state.json"
    return pkb / "agents" / AGENT_ID / "state.json"


def _import_dir() -> Path:
    data_dir = _host.get_data_dir()
    root = data_dir if data_dir is not None else Path("lab/data")
    return root / "user-import" / WORLD_SLUG


def _balances_file() -> Path:
    return _import_dir() / "balances.json"


def _findings_file() -> Path:
    return _import_dir() / "findings" / "consolidation_analyzer.md"


def _history_file() -> Path:
    return _import_dir() / "history.jsonl"


def _relative_pointer(path: Path) -> str:
    """A short, non-identifying pointer to ``path`` for the activity
    stream (ARCHITECTURE.md §6: "a short, non-identifying pointer to the
    findings file").

    TEST_REPORT.md F7: the previous code interpolated the absolute path
    directly, which on a real install is rooted under the operator's home
    directory and carries their OS username — and ``activity.jsonl``
    renders on the dashboard. Relative to the data root keeps the pointer
    short and stable across machines without leaking the filesystem
    location it sits in.
    """
    data_dir = _host.get_data_dir()
    root = data_dir if data_dir is not None else Path("lab/data")
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


# ══════════════════════════════════════════════════════════════════════
#  2. ARITHMETIC — pure, independently unit-testable, zero I/O
# ══════════════════════════════════════════════════════════════════════

def blended_apr(debts: List[Dict[str, Any]]) -> Optional[float]:
    """Balance-weighted average APR across debts. None if total balance is 0."""
    total_balance = sum(float(d.get("balance", 0.0)) for d in debts)
    if total_balance <= 0:
        return None
    weighted = sum(float(d.get("balance", 0.0)) * float(d.get("apr", 0.0))
                   for d in debts)
    return weighted / total_balance


def monthly_interest_cost(balance: float, apr: float) -> float:
    """Simple monthly interest on a balance at a given annual APR (percent)."""
    return balance * (apr / 100.0) / 12.0


def breakeven_months(fee_amount: float, monthly_savings: float) -> Optional[int]:
    """Months of savings needed to offset a one-time fee.

    None if there is no positive monthly saving (the scenario never breaks
    even) or the fee is non-positive (breaks even immediately, reported as 0).
    """
    if fee_amount <= 0:
        return 0
    if monthly_savings <= 0:
        return None
    return math.ceil(fee_amount / monthly_savings)


@dataclass
class ScenarioResult:
    institution: str
    product: str
    rate: float
    fee_pct: float
    source: str
    as_of: str
    fee_amount: float
    new_monthly_interest: float
    monthly_savings: float
    breakeven: Optional[int]


def _compute_scenarios(debts: List[Dict[str, Any]],
                        scenarios: List[Dict[str, Any]]) -> List[ScenarioResult]:
    total_balance = sum(float(d.get("balance", 0.0)) for d in debts)
    current_monthly = sum(
        monthly_interest_cost(float(d.get("balance", 0.0)), float(d.get("apr", 0.0)))
        for d in debts
    )
    out: List[ScenarioResult] = []
    for s in scenarios:
        rate = float(s.get("rate", 0.0))
        fee_pct = float(s.get("fee_pct", 0.0))
        fee_amount = total_balance * (fee_pct / 100.0)
        new_monthly = monthly_interest_cost(total_balance, rate)
        savings = current_monthly - new_monthly
        out.append(ScenarioResult(
            institution=str(s.get("institution", "")),
            product=str(s.get("product", "")),
            rate=rate,
            fee_pct=fee_pct,
            source=str(s.get("source", "")),
            as_of=str(s.get("as_of", "")),
            fee_amount=fee_amount,
            new_monthly_interest=new_monthly,
            monthly_savings=savings,
            breakeven=breakeven_months(fee_amount, savings),
        ))
    return out


# ══════════════════════════════════════════════════════════════════════
#  3. INPUT — schema + parse-failure behavior (ARCHITECTURE.md §6.1)
# ══════════════════════════════════════════════════════════════════════

class _MalformedInput(Exception):
    pass


# TEST_REPORT.md F1/F8: an upper magnitude bound on any numeric field this
# module does arithmetic over. Not a realistic domain limit by itself (no
# one's balance is anywhere near this) — it exists to reject the
# ``json.loads``-legal-but-not-domain-legal shapes (``1e308``) that are
# individually finite but overflow to ``inf`` the moment two of them are
# multiplied together downstream, which then raises ``OverflowError`` out of
# ``math.ceil`` in ``breakeven_months``. Rejecting at parse time keeps the
# "malformed input never crashes the tick" contract (§6.1) true without
# threading finiteness checks through every arithmetic call site.
_MAX_REASONABLE_VALUE = 1e12


def _validate_numeric_field(entry: Dict[str, Any], key: str) -> None:
    """Raise ``_MalformedInput`` if ``entry[key]`` is present but is not a
    finite, non-negative, in-range real number.

    Absent keys are left alone — the arithmetic layer's own ``.get(key,
    0.0)`` default is the schema's documented behavior for a missing field.
    This only guards a field that *is* present with a value that would
    otherwise crash or silently corrupt downstream arithmetic (§6.1's
    "malformed" bucket, not "missing"):

    - non-numeric (a string like ``"1,200.00"`` or ``"19.99%"``, or ``null``)
    - ``bool`` (Python's ``bool`` is an ``int`` subclass; a stray ``true``/
      ``false`` in a numeric field is not a number the schema means)
    - non-finite (JSON's bare ``NaN``/``Infinity``, both accepted by
      ``json.loads`` by default, but neither is a valid finite balance/rate)
    - negative (not part of this schema; see TEST_REPORT.md F8 — a negative
      balance/APR/rate/fee is not an adversarial input worth rendering
      verbatim, it's malformed)
    - out of the reasonable domain range (``_MAX_REASONABLE_VALUE``)
    """
    if key not in entry:
        return
    value = entry[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _MalformedInput()
    if not math.isfinite(value):
        raise _MalformedInput()
    if value < 0 or abs(value) > _MAX_REASONABLE_VALUE:
        raise _MalformedInput()


def _load_balances() -> Optional[Dict[str, Any]]:
    """Returns None if the file is absent (a normal no-op, not an error).
    Raises _MalformedInput if present but unparsable / schema-invalid, or if
    any numeric field's *value* (not just its container shape) is
    non-finite, non-numeric, negative, or out of domain range —
    callers must not echo any content from the exception."""
    path = _balances_file()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _MalformedInput() from exc
    if not isinstance(data, dict):
        raise _MalformedInput()
    debts = data.get("debts")
    if debts is not None and not isinstance(debts, list):
        raise _MalformedInput()
    scenarios = data.get("candidate_scenarios")
    if scenarios is not None and not isinstance(scenarios, list):
        raise _MalformedInput()
    for d in (debts or []):
        if not isinstance(d, dict):
            raise _MalformedInput()
        _validate_numeric_field(d, "balance")
        _validate_numeric_field(d, "apr")
    for s in (scenarios or []):
        if not isinstance(s, dict):
            raise _MalformedInput()
        _validate_numeric_field(s, "rate")
        _validate_numeric_field(s, "fee_pct")
        _validate_numeric_field(s, "term_months")
    # Optional operator-set alert threshold (Workstream C, tracking): a
    # top-level field, not a per-debt/per-scenario one, so it's validated
    # directly against `data` rather than one of the list entries above.
    _validate_numeric_field(data, "alert_breakeven_months")
    return data


def _content_hash() -> str:
    path = _balances_file()
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    return hashlib.sha256(raw).hexdigest()


# ══════════════════════════════════════════════════════════════════════
#  4. WRITE PATH
# ══════════════════════════════════════════════════════════════════════

_DIGIT_RE = re.compile(r"\d")


def _framing_prose() -> str:
    """See ``_builtin_debt_advisor._framing_prose`` — same digit-rejection
    fix for the same [ASK] (REVIEW.md): a model-generated sentence
    containing a digit is rejected in favor of the deterministic fallback,
    since the prompt asking the model not to emit a number was previously
    unenforced."""
    prompt = (
        "Write one short, plain, non-evaluative sentence introducing a "
        "computed debt-consolidation comparison. Do not name any "
        "institution, rate, or number. Do not say 'best' or give advice."
    )
    text = _host.llm_complete(prompt, max_tokens=60).strip()
    if not text or not check_guardrail([Segment.agent(text)]).ok or _DIGIT_RE.search(text):
        return "Computed comparison of your staged balances against staged candidate scenarios."
    return text


def _build_output(debts: List[Dict[str, Any]],
                   scenarios: List[Dict[str, Any]]) -> str:
    """Assemble the findings document as an ordered list of provenance-
    tagged segments (ARCHITECTURE.md §13.11's structural refactor, now
    implemented) rather than a flat f-string.

    ``r.institution``, ``r.product``, ``r.source``, and ``r.as_of`` are all
    parsed verbatim from the SAME operator-authored ``candidate_scenarios``
    entry — every one of them is a ``Segment.operator(...)``, by
    construction, never a value matched against a name set. This is what
    the old ``operator_names``/``quoted_spans`` machinery (REVIEW.md's
    BLOCK-5/BLOCK-6/BLOCK-7 history) was trying to approximate from flat
    text: the "(as you entered it)" marker is unconditional here because a
    candidate scenario's institution is *always* the operator's own typed
    entry, never a third party's claim — there is no other way for a name
    to reach this line.
    """
    apr = blended_apr(debts)
    results = _compute_scenarios(debts, scenarios)

    lines: List[List[Segment]] = [
        [Segment.agent("# Consolidation Analyzer — Findings\n")],
        [Segment.agent(_framing_prose() + "\n")],
    ]

    lines.append([Segment.agent("## Current position\n")])
    lines.append([Segment.agent(f"- Debts entered: {len(debts)}")])
    if apr is not None:
        # apr is a code-computed float — inserted verbatim, never retyped.
        lines.append([Segment.agent(f"- Current blended APR: {apr:.2f}%")])
    else:
        lines.append([Segment.agent(
            "- Current blended APR: not computable (zero total balance)"
        )])
    lines.append([Segment.agent("")])

    lines.append([Segment.agent("## Candidate scenarios\n")])
    if results:
        for r in results:
            breakeven_text = (
                f"{r.breakeven} months" if r.breakeven is not None
                else "does not break even at this rate/fee"
            )
            lines.append([
                Segment.agent("- **"),
                Segment.operator(r.institution, is_name=True),
                Segment.agent("** (as you entered it) — "),
                Segment.operator(r.product),
                Segment.agent(
                    f" (as entered), rate {r.rate:.2f}%, "
                    f"fee {r.fee_pct:.2f}% (${r.fee_amount:.2f}), "
                    f"monthly savings ${r.monthly_savings:.2f}, "
                    f"breakeven {breakeven_text}. Source: "
                ),
                Segment.operator(r.source),
                Segment.agent(" (as entered), as of "),
                Segment.operator(r.as_of),
                Segment.agent(" (as entered)."),
            ])
    else:
        lines.append([Segment.agent("- No candidate scenarios staged.")])
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
    symlink. Shared by every write path under ``lab/data/user-import/`` —
    findings, and now history/proposed-scenarios too.

    TEST_REPORT.md F5: ``Path.write_text`` writes *through* a symlink, and a
    subsequent ``chmod`` retargets the victim file's mode — on the
    documented shared-machine convention (multiple accounts on one box),
    another local user who pre-creates a path here as a symlink gets an
    arbitrary-file-overwrite-plus-chmod primitive running as the operator.
    ``os.O_NOFOLLOW`` at open time (not a ``islink()`` check beforehand,
    which would be a TOCTOU race against the same attacker) makes the open
    itself fail if the final path component is a symlink. Returns False
    (and writes nothing) in that case.
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


# ══════════════════════════════════════════════════════════════════════
#  4b. HISTORY + THRESHOLD ALERTING (Workstream C: ongoing tracking)
# ══════════════════════════════════════════════════════════════════════

# Cap on retained history lines — bounded growth, oldest dropped first.
_MAX_HISTORY_LINES = 500


def _scenario_key(institution: str, product: str) -> str:
    return f"{institution}|{product}"


def _load_history_lines(path: Path) -> List[str]:
    """Read and validate existing history lines. Corrupt individual lines
    are dropped, never raised — the same F1 "malformed input never crashes
    the tick" contract this module already holds for balances.json extends
    to its own history file. A wholly unreadable file is treated as empty,
    not an error.

    REVIEW.md addendum 8, BLOCK-10: JSON-*parseable* is not enough — ``5``,
    ``"x"``, ``null``, and ``[1, 2]`` are all valid JSON that is not an
    object, and ``_latest_entries_by_key`` calls ``.get()`` on every
    returned entry. Without this check, one such line survives the filter,
    then raises ``AttributeError`` downstream — caught by the tick's own
    broad ``except Exception``, but by then ``_append_history`` never runs
    either (same try block), so the poison line is never rewritten and the
    tick's history/threshold branch stays silently, permanently dead. The
    same shape ``_load_balances`` already guards against for
    ``balances.json`` (container-shape validation, not just parseability)
    applies here too.
    """
    if not path.exists():
        return []
    try:
        raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    valid: List[str] = []
    for line in raw_lines:
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        valid.append(line)
    return valid


def _latest_entries_by_key(lines: List[str]) -> Dict[str, Dict[str, Any]]:
    """The most recent history entry per scenario_key, read from already-
    validated lines (see ``_load_history_lines``)."""
    latest: Dict[str, Dict[str, Any]] = {}
    for line in lines:
        entry = json.loads(line)  # already validated by the caller
        key = entry.get("scenario_key")
        if isinstance(key, str):
            latest[key] = entry
    return latest


def _append_history(existing_lines: List[str],
                     results: List[ScenarioResult]) -> str:
    """Return the new history.jsonl content: existing valid lines plus one
    new line per candidate scenario this tick, capped at
    ``_MAX_HISTORY_LINES`` (oldest dropped first).

    Every field written here is either code-computed (rate/fee_pct/
    breakeven/monthly_savings, from ``ScenarioResult``) or the operator's
    own typed institution/product name — nothing agent-generated, nothing
    World-sourced without the operator having staged it themselves. This is
    a structured data log, not prose rendered for a human to read as an
    agent's own claim (it is never passed through ``check_guardrail``,
    exactly like ``balances.json`` itself never is), so the numeric-
    integrity property this module holds for the findings document extends
    here by the same reasoning, not by re-running the same check.
    """
    ts = time.time()
    lines = list(existing_lines)
    for r in results:
        entry = {
            "ts": ts,
            "scenario_key": _scenario_key(r.institution, r.product),
            "institution": r.institution,
            "product": r.product,
            "rate": r.rate,
            "fee_pct": r.fee_pct,
            "breakeven": r.breakeven,
            "monthly_savings": r.monthly_savings,
        }
        lines.append(json.dumps(entry, sort_keys=True))
    if len(lines) > _MAX_HISTORY_LINES:
        lines = lines[-_MAX_HISTORY_LINES:]
    return "\n".join(lines) + ("\n" if lines else "")


def threshold_crossings(prev_by_key: Dict[str, Dict[str, Any]],
                         results: List[ScenarioResult],
                         alert_breakeven_months: Optional[float]) -> List[str]:
    """Pure comparison, no I/O: which scenario_keys just crossed the
    operator's own ``alert_breakeven_months`` threshold (breakeven at or
    below it now, and was not at or below it — or didn't exist — last
    tick). Returns scenario_keys only, never a rate/dollar figure — the
    caller decides how to surface this, and any activity-stream emission
    stays pointer-only per ARCHITECTURE.md §6's convention (the actual
    numbers live only in the findings file, where every number is already
    code-inserted)."""
    if alert_breakeven_months is None:
        return []
    crossed: List[str] = []
    for r in results:
        key = _scenario_key(r.institution, r.product)
        prev = prev_by_key.get(key)
        prev_breakeven = prev.get("breakeven") if prev else None
        now_crosses = (r.breakeven is not None
                       and r.breakeven <= alert_breakeven_months)
        prev_crossed = (prev_breakeven is not None
                        and prev_breakeven <= alert_breakeven_months)
        if now_crosses and not prev_crossed:
            crossed.append(key)
    return crossed


def _vetted_institution_names(bundle_dir: Path) -> frozenset[str]:
    """Specific, named, verified institutions — see
    ``_builtin_debt_advisor._vetted_institutions`` for why ``category ==
    "institutions"`` alone is not enough: that category also holds generic
    glossary/concept terms. Only entries carrying an ``institution_type``
    field (and a third-party ``verification_source``) count as vetted.

    Also requires a fresh ``verified_as_of`` date (``is_verification_fresh``)
    — an institution missing this field, or whose verification has gone
    stale, degrades out of the vetted set rather than being trusted
    forever. See ``_builtin_debt_advisor._vetted_institutions`` for the
    identical rule.

    Filters non-dict entries the same way ``_builtin_debt_advisor._load_terms``
    does (F1/F9): a stray malformed ``terms.json`` entry (e.g. a bare
    string) must be skipped, not raise ``AttributeError`` out of an
    unguarded ``.get()`` call here. Left unfiltered, that exception
    propagates out of ``tick()``'s F1 backstop as a silent *permanent*
    stall — every subsequent tick logs "skipped this cycle" and no findings
    are ever written again — rather than a clean skip of the bad entry."""
    try:
        doc = json.loads((bundle_dir / "terms.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return frozenset()
    terms = doc.get("terms") if isinstance(doc, dict) else doc
    names = set()
    for t in terms or []:
        if not isinstance(t, dict):
            continue
        if (t.get("category") == "institutions" and t.get("institution_type")
                and t.get("verification_source")
                and is_verification_fresh(str(t.get("verified_as_of") or ""))):
            names.add(str(t.get("term") or t.get("slug") or "").lower())
    return frozenset(names)


# ══════════════════════════════════════════════════════════════════════
#  5/6. LOOP + MEMORY
# ══════════════════════════════════════════════════════════════════════

class ConsolidationAnalyzerAgent:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._status = "idle"
        self._last_input_hash = ""
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
        # never a raw balance/APR/institution name.
        self._last_input_hash = str(data.get("input_hash") or "")
        self._last_run_at = float(data.get("last_run_at") or 0.0)

    def _save_state(self) -> None:
        path = _state_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "input_hash": self._last_input_hash,
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
        interval = max(60, int(os.getenv("LAB_CONSOLIDATION_ANALYZER_INTERVAL_SEC", "86400")))
        try:
            while True:
                await asyncio.sleep(interval)
                try:
                    self.tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # TEST_REPORT.md F1: any exception escaping a single
                    # tick's body used to propagate out of this while-loop
                    # entirely — the surrounding try only caught
                    # CancelledError — permanently killing the agent while
                    # `.status` kept reporting "running". ARCHITECTURE.md
                    # §6.1 is explicit that malformed input "does not crash"
                    # the tick; this is the backstop for any *other* future
                    # field/bug shape that isn't already caught by
                    # `_load_balances`'s own validation, so no single bad
                    # tick can end the agent's lifetime. Mirrors the
                    # malformed-input warning: non-specific, no content
                    # echo.
                    _host.emit(
                        AGENT_ID,
                        "Consolidation Analyzer: an unexpected error "
                        "occurred during a scheduled check — skipped this "
                        "cycle.",
                        "warn",
                    )
        except asyncio.CancelledError:
            return

    def tick(self) -> None:
        current_hash = _content_hash()
        if not current_hash:
            return  # File absent — normal no-op, not an error.

        try:
            data = _load_balances()
        except _MalformedInput:
            _host.emit(
                AGENT_ID,
                "Consolidation Analyzer: could not read "
                f"{_relative_pointer(_balances_file())} — check its format.",
                "warn",
            )
            return
        if data is None:
            return

        bundle_dir = find_mounted_bundle_dir()
        disclaimer = read_disclaimer(bundle_dir) if bundle_dir else None
        vetted = _vetted_institution_names(bundle_dir) if bundle_dir else frozenset()

        # TEST_REPORT.md F6: the no-op fingerprint used to be the raw
        # balances.json content hash alone. The disclaimer text and the
        # vetted-institution set both come from the mounted World and both
        # affect the rendered output, but neither was hashed — so editing
        # compliance/DISCLAIMER.md never propagated to an existing findings
        # file. Folding both into the fingerprint (plus checking whether the
        # findings file still exists, so an operator-deleted findings file
        # per §6.5's documented v1 "forget" story gets regenerated rather
        # than permanently suppressed) closes both gaps.
        fingerprint = hashlib.sha256(
            " ".join([
                current_hash,
                disclaimer or "",
                ",".join(sorted(vetted)),
            ]).encode("utf-8")
        ).hexdigest()
        findings_path = _findings_file()

        if fingerprint == self._last_input_hash and findings_path.exists():
            return  # True no-op.

        if disclaimer is None:
            _host.emit(
                AGENT_ID,
                "Consolidation Analyzer: compliance/DISCLAIMER.md missing "
                "or altered — refusing to write findings until restored.",
                "warn",
            )
            return

        debts = data.get("debts") or []
        scenarios = data.get("candidate_scenarios") or []

        try:
            body = _build_output(debts, scenarios)
        except _GuardrailBlocked as exc:
            reason = exc.args[0] if exc.args else ""
            # REVIEW.md addendum 2 [ASK-B]: once BLOCK-5 lands, a block is
            # rare and always the operator's to fix — "see logs" gave no
            # actionable next step. Name the reason and point at the fix.
            #
            # REVIEW.md re-review addendum 3, item 3: the original message
            # hardcoded an institutional-character explanation pointing at
            # `institution` fields, which is wrong for the evaluative
            # branch — the branch that fires in realistic practice
            # (BLOCK-6). Branch the hint on which reason actually fired.
            #
            # REVIEW.md re-review addendum 6, item 4: since the
            # segment-based provenance refactor, `institution`, `product`,
            # `source`, and `as_of` are all OPERATOR segments, which are
            # never evaluative-checked at all (see debt_finance_compliance
            # module docstring) — this branch can no longer fire on those
            # fields. The only AGENT-provenance, non-hardcoded text in this
            # agent's output is the LLM-generated framing sentence, so that
            # is what can actually trigger this reason now.
            if reason == REASON_EVALUATIVE:
                hint = (
                    "The generated framing sentence read as evaluative or "
                    "imperative (e.g. 'best', 'guaranteed', 'you should'). "
                    "This is model output, not something staged in "
                    f"{_relative_pointer(_balances_file())} — retry the "
                    "tick; if it recurs, the fallback framing sentence "
                    "should have been used instead."
                )
            elif reason.startswith(REASON_INSTITUTIONAL_PREFIX):
                hint = (
                    "Check the `institution` fields in "
                    f"{_relative_pointer(_balances_file())} — an "
                    "institutional-character claim (e.g. 'credit union', "
                    "'nonprofit') must be paired with an institution name "
                    "you typed yourself or one this World has verified."
                )
            else:
                hint = (
                    "Check the content staged in "
                    f"{_relative_pointer(_balances_file())}."
                )
            _host.emit(
                AGENT_ID,
                "Consolidation Analyzer: generated output failed the "
                f"language-safety check ({reason}) and was not written. "
                f"{hint}",
                "warn",
                data={"reason": reason},
            )
            return

        if not _write_findings(body, disclaimer):
            # TEST_REPORT.md F5: refuses rather than following a
            # pre-placed symlink at the findings path. Non-specific,
            # matches the malformed-input warning shape.
            _host.emit(
                AGENT_ID,
                "Consolidation Analyzer: could not write the findings "
                "file — the destination is not a regular file.",
                "warn",
            )
            return

        # Workstream C (ongoing tracking): append this tick's computed
        # scenario values to history.jsonl and check whether any scenario
        # just crossed the operator's own alert_breakeven_months threshold.
        # Best-effort — a history-file problem must never block the
        # findings write that already succeeded above.
        crossed_keys: List[str] = []
        try:
            results = _compute_scenarios(debts, scenarios)
            existing_lines = _load_history_lines(_history_file())
            prev_by_key = _latest_entries_by_key(existing_lines)
            raw_threshold = data.get("alert_breakeven_months")
            alert_threshold = (
                float(raw_threshold) if isinstance(raw_threshold, (int, float))
                and not isinstance(raw_threshold, bool) else None
            )
            crossed_keys = threshold_crossings(prev_by_key, results, alert_threshold)
            new_content = _append_history(existing_lines, results)
            _safe_write_0600(_history_file(), new_content)
        except Exception:  # noqa: BLE001 — history is best-effort, never fatal
            # REVIEW.md addendum 8: a silent `pass` here is what made
            # BLOCK-10 invisible — history/threshold tracking could stop
            # forever with no signal anywhere. Log (never re-raise, and
            # never include a rate/dollar figure/institution name).
            _log.warning(
                "%s: could not update history/threshold tracking this "
                "tick — history.jsonl or alert_breakeven_months may be "
                "unreadable; the findings write above is unaffected.",
                AGENT_ID)

        self._last_input_hash = fingerprint
        self._last_run_at = time.time()
        self._save_state()

        # F9 (ARCHITECTURE.md): the proactive-speech moment (the finding's
        # framing sentence went through the model funnel above); gated
        # separately from inference — the finding is still written, only
        # the announcement is silenced while held.
        from arail import agent_context
        if agent_context.speech_gate(AGENT_ID):
            _host.emit(
                AGENT_ID,
                "Consolidation Analyzer produced a new finding — see "
                f"{_relative_pointer(_findings_file())}",
                "info",
            )
            if crossed_keys:
                # Pointer-only, per ARCHITECTURE.md §6's convention — no
                # rate, no dollar figure, no institution name in the
                # activity stream. The actual numbers are already in the
                # findings file above.
                _host.emit(
                    AGENT_ID,
                    "Consolidation Analyzer: a candidate scenario crossed "
                    "your alert-breakeven threshold — see "
                    f"{_relative_pointer(_findings_file())}",
                    "info",
                )


consolidation_analyzer = ConsolidationAnalyzerAgent()
