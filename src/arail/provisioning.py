"""The class check: "declared and not instantiated" as a standing assertion,
not a fourth incident.

Three instances of the same defect class shipped in two releases (QA-6: the
Compiled-KB gate shipped on with nothing ever approved; defect A: the
relational store shipped with nothing ever creating it; defect B: the
vector-backend-absent branch shipped silent). Two of the three passed
``doctor``. This module is the structural answer:

    A mechanism may be off (not declared) or on-and-working. "On, and
    nothing has ever performed the step that makes it real" is a *third*
    state, and it is always reported — never silent.

Every declared mechanism registers its own instantiation predicate here.
``./arailctl doctor`` evaluates every one and reports a finding for any
mechanism that is declared but not instantiated, tier "required" (exit 3)
or "info". ``./arailctl status`` summarizes the same table under
``arail.provisioning/v1``.

Anti-goal: this is not a health monitor. Every predicate here is a
file-stat, an import check, or a read-only ``PRAGMA`` — no embeds, no HTTP,
no index builds, ever.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Assertion:
    key: str
    tier: str          # "required" | "info"
    declared: bool
    instantiated: bool
    detail: str
    action: str

    @property
    def finding(self) -> bool:
        """declared and not instantiated => a finding, never silence."""
        return self.declared and not self.instantiated


AssertionFn = Callable[..., Assertion]

# key -> (fn, tier). ``tier`` here is the mechanism's OWN declared tier —
# used only as the fallback when ``fn`` raises (see ``evaluate_all``,
# QA-6); a successful call's own returned ``Assertion.tier`` always wins
# on the happy path, so registering "required" does not force every
# finding to that tier, only the crash case.
_REGISTRY: dict = {}


def register(key: str, fn: AssertionFn, *, tier: str = "required",
             overwrite: bool = False) -> None:
    """Add a mechanism's instantiation predicate to the registry. Adding a
    mechanism to ARAIL 2.1 without registering an assertion here should
    feel like an omission — this registry is the checklist.

    TEST_REPORT.md QA-7: a duplicate ``key`` is refused, not silently
    applied — a 2.1 mechanism, a plugin, or a bad merge that reuses an
    existing key must not be able to silently replace a built-in
    predicate (including replacing a real check with one that always
    reports healthy) with no error and no trace. Pass ``overwrite=True``
    if replacing an existing registration is genuinely intended (e.g. a
    module reload in a REPL); the built-in registrations below never do.

    ``tier`` (TEST_REPORT.md QA-6) is the mechanism's OWN tier, used only
    as the fallback classification if this predicate later raises instead
    of returning an ``Assertion`` — see ``evaluate_all``. Defaults to
    "required": a predicate that has never successfully run even once is
    not proven safe to demote, and defaulting to "info" would silently
    reintroduce QA-6 for every future ``register()`` call that forgets to
    pass a tier."""
    if key in _REGISTRY and not overwrite:
        _log.warning(
            "provisioning.register(%r) ignored: a mechanism is already "
            "registered under this key. The existing predicate stays "
            "active. Pass overwrite=True if replacing it is deliberate.",
            key)
        return
    _REGISTRY[key] = (fn, tier)


def registered_keys() -> List[str]:
    return sorted(_REGISTRY)


def _reset_for_tests() -> None:
    """Test-only: clear the registry so a test can register a synthetic
    mechanism without leaking into other tests."""
    _REGISTRY.clear()


# ── Built-in assertions ──────────────────────────────────────────────────

def check_relational_store(*, repo_root, data_dir, spec_dir=None) -> Assertion:
    """defect A: spec/schema/* declares the store; ensure_db(apply=False)
    tells us whether it has ever actually been created.

    REVIEW.md ASK-1: ``spec_dir`` resolves from the package location
    (``arail.dbspec.ensure.DEFAULT_SPEC_DIR``), never from ``repo_root``/
    CWD, for the same reason ``ensure_db`` itself does — a caller with the
    wrong working directory must not get a silent, non-degrading
    "unavailable" on a perfectly healthy database.

    TEST_REPORT.md QA-5: checking only ``data_dir`` (one root) is this
    sprint's own six-roots defect (§4.3) recurring INSIDE the class check
    built to prevent it — the operator's measured usage is one World at a
    time with the root lab never started, so five of six roots would be
    silently skipped. Now checks every root ``resolve_data_dirs(repo_root,
    root_data_dir=data_dir)`` resolves — ``data_dir`` anchors the root row
    (so a caller testing one isolated data_dir, or doctor passing the
    root lab's own DATA_DIR, still gets it checked as "the root"), and any
    registered/on-disk World instance under ``repo_root/lab/instances/``
    is checked alongside it. ``instantiated`` is True only if EVERY
    resolved root has ``state == "ok"``; a single missing database
    anywhere is a finding, naming which root(s)."""
    from arail.data_dirs import resolve_data_dirs
    from arail.dbspec.ensure import ensure_db, DEFAULT_SPEC_DIR
    spec_dir = Path(spec_dir) if spec_dir is not None else DEFAULT_SPEC_DIR
    declared = (spec_dir / "schema" / "migrations").is_dir()
    if not declared:
        return Assertion("relational_store", "required", False, False,
                         "no spec/schema/migrations/ in this checkout", "")
    rows = resolve_data_dirs(repo_root, root_data_dir=data_dir)
    reports = [(row, ensure_db(row.data_dir, apply=False, spec_dir=spec_dir))
              for row in rows]
    missing = [row.slug for row, rep in reports if rep.state != "ok"]
    instantiated = not missing
    if instantiated:
        detail = ""
        action = ""
    else:
        detail = (f"{len(missing)} of {len(rows)} resolved root(s) have no "
                  f"database: {', '.join(missing)}")
        action = "./arailctl install"
    return Assertion(
        "relational_store", "required", declared, instantiated,
        detail=detail, action=action,
    )


def check_vector_backend(**_kw) -> Assertion:
    """defect B: LanceDB is a hard dep in both tiers (declared); is it
    importable in THIS interpreter (instantiated)?"""
    from arail.vector_index import available
    ok = available()
    return Assertion(
        "vector_backend", "required", True, ok,
        detail="" if ok else "LanceDB is not importable in this interpreter",
        action="" if ok else "./arailctl install",
    )


def check_kb_gate(**_kw) -> Assertion:
    """QA-6 / ARCHITECTURE.md §10 Finding 3: the Compiled-KB gate is
    declared on only when there is a corpus that COULD be approved.

    The predicate this replaced decided on ``len(approved_paths()) > 0``
    alone and ignored ``compiled_kb.gate_state()`` entirely — even
    though QA-6 built that function's four states (``off`` /
    ``unbootstrapped`` / ``empty`` / ``populated``) specifically to keep
    "nothing to approve yet" apart from "N approvable pages, zero
    approved." Under the old predicate a lab with zero source documents
    and no World ever mounted reported IDENTICALLY to the operator's real
    `ai` World (351 approvable pages, none approved) — collapsing
    exactly the distinction QA-6 existed to preserve, and making
    ``doctor`` un-greenable on any clean machine, which teaches
    operators to ignore it.

    Fixed at ``declared``, never at ``tier`` — this is NOT the
    severity-tuning the architect rejected for ``relational_store``
    (which downgrades the tier by lifecycle and hides a real defect).
    The asymmetry: ``relational_store`` can be instantiated
    unconditionally BY THE MACHINE (an empty schema decides nothing on
    the operator's behalf — see ``setup_db_ensure``, §10 Finding 2), so
    that gap was closed by making a workflow create it. ``kb_gate``
    cannot: approving knowledge is a CONSENT decision (the
    2026-08-09 compiled-kb-bootstrap sprint ruled it must never happen
    automatically), so there is no workflow fix here that isn't CI
    fabricating consent. A lab with nothing that could be approved is
    correctly not-applicable, not a lowered-severity finding.

    Truth table (§10):
      gate off                        -> declared=False             -> not a finding
      no corpus (approved+pending==0) -> declared=False             -> not-applicable
      corpus exists, nothing approved -> declared=True,  inst=False -> REQUIRED, loud
      anything approved and live      -> declared=True,  inst=True  -> OK

    Calls ``gate_state(cheap=False)`` — ``cheap=True`` reports
    ``pending_count=-1`` ("not computed"), and this predicate must never
    read that as "no corpus": the "no corpus" check below
    (``approved == 0 and pending == 0``) already fails correctly on
    ``-1`` (a `-1` is never `0`, so it falls through to the loud
    required-finding path rather than "not-applicable") without any
    special-casing — fail loud on ignorance, never quiet. ``doctor`` is
    not a hot path; the tree walk `gate_state` does here is affordable.
    """
    try:
        from arail import compiled_kb
    except Exception as exc:  # noqa: BLE001
        return Assertion("kb_gate", "info", True, False, f"unavailable: {exc}",
                         "./arailctl pkb approve")
    try:
        state = compiled_kb.gate_state(cheap=False)
    except Exception as exc:  # noqa: BLE001
        return Assertion("kb_gate", "required", True, False, str(exc),
                         "./arailctl pkb approve")

    if state.get("state") == "off":
        return Assertion("kb_gate", "required", False, False,
                         "gate not declared on", "")

    approved = int(state.get("approved_count", 0))
    live = int(state.get("live_count", 0))
    pending = int(state.get("pending_count", -1))

    if approved == 0 and pending == 0:
        # Nothing was ever approved AND nothing is waiting to be —
        # there is no corpus to have a consent decision about yet.
        return Assertion("kb_gate", "required", False, False,
                         "no corpus to approve yet", "")

    instantiated = live > 0
    if instantiated:
        detail = ""
        action = ""
    elif pending == -1:
        detail = "gate is on and nothing has ever been approved (pending count unknown)"
        action = "./arailctl pkb approve, or approve on /dac"
    elif pending > 0:
        detail = f"gate is on, nothing approved yet — {pending} page(s) await approval"
        action = "./arailctl pkb approve, or approve on /dac"
    else:
        detail = "gate is on but nothing has ever been approved"
        action = "./arailctl pkb approve, or approve on /dac"

    return Assertion("kb_gate", "required", True, instantiated, detail, action)


def check_embedding_provenance(**_kw) -> Assertion:
    """existing C4: spec declares a model+dim; does the sidecar agree?"""
    try:
        from arail import pkb_index
    except Exception as exc:  # noqa: BLE001
        return Assertion("embedding_provenance", "info", True, False,
                         f"unavailable: {exc}", "")
    codes = pkb_index.degraded_codes()
    bad = set(codes) & {"dimension", "provenance"}
    return Assertion(
        "embedding_provenance", "required", True, not bad,
        detail="; ".join(codes.get(c, c) for c in bad) if bad else "",
        action="./arailctl pkb reembed" if bad else "",
    )


def check_instance_registry(*, repo_root, **_kw) -> Assertion:
    """the 2-of-6 miss: every on-disk instance dir should have a registry
    record (a purely informational finding — an unregistered instance
    still works, it just wasn't reached by any --all-instances walk)."""
    from arail.data_dirs import resolve_data_dirs
    rows = resolve_data_dirs(repo_root)
    ondisk_only = [r for r in rows if r.origin == "ondisk"]
    return Assertion(
        "instance_registry", "info", True, not ondisk_only,
        detail=("" if not ondisk_only else
                f"{len(ondisk_only)} instance(s) on disk with no registry "
                f"record: {', '.join(r.slug for r in ondisk_only)}"),
        action="" if not ondisk_only else "re-register via ./arailctl install",
    )


register("relational_store", check_relational_store)
register("vector_backend", check_vector_backend)
register("kb_gate", check_kb_gate)
register("embedding_provenance", check_embedding_provenance)
register("instance_registry", check_instance_registry)


def evaluate_all(**kwargs) -> List[Assertion]:
    """Run every registered assertion. A single mechanism's check failing
    with an exception becomes a finding naming the exception, never a
    crash of the whole checkup.

    TEST_REPORT.md QA-6: the crash fallback used to hardcode tier="info",
    so a mechanism registered "required" whose predicate raised — a
    broken import, an unreadable data dir, a bug in the predicate itself
    — silently demoted to a tier nobody's exit code reads (only required
    findings degrade doctor's exit code). The mechanism most likely to be
    genuinely broken is the one whose check just crashed; that is exactly
    the case that must NOT be downgraded. The fallback now uses the tier
    the mechanism was REGISTERED with (see ``register``'s ``tier``
    parameter), not a hardcoded constant — the tier belongs to the
    mechanism, not to the outcome of evaluating it."""
    out = []
    for key in registered_keys():
        fn, tier = _REGISTRY[key]
        try:
            result = fn(**kwargs)
            # TEST_REPORT.md QA-10: a predicate that RAISES was already
            # caught (above/except); one that simply returns the wrong
            # TYPE was not — a None (or any non-Assertion) return used to
            # sail through this loop, and the AttributeError it caused
            # surfaced later inside to_json or doctor's render loop, both
            # behind an OUTER try that swallows the entire provisioning
            # section — silencing every other mechanism, including
            # relational_store and vector_backend, the two this sprint
            # exists for. Treated exactly like a raise: a finding, using
            # the mechanism's registered tier, never silence.
            if not isinstance(result, Assertion):
                out.append(Assertion(
                    key, tier, True, False,
                    f"check returned {type(result).__name__}, not an "
                    f"Assertion — treated as a broken check", ""))
            else:
                out.append(result)
        except Exception as exc:  # noqa: BLE001
            out.append(Assertion(key, tier, True, False,
                                 f"check raised {type(exc).__name__}: {exc}", ""))
    return out


def to_json(assertions: List[Assertion]) -> dict:
    return {
        "schema": "arail.provisioning/v1",
        "assertions": [
            {
                "key": a.key, "tier": a.tier, "declared": a.declared,
                "instantiated": a.instantiated, "finding": a.finding,
                "detail": a.detail, "action": a.action,
            }
            for a in assertions
        ],
    }
