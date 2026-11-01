"""QA target 7: does ``arail/provisioning.py`` generalize, or does it
hardcode the three known bugs?

ARCHITECTURE.md §5 makes the strong claim:

    declared and not instantiated => a finding, never silence.
    ... Adding a mechanism to ARAIL 2.1 without registering an assertion
    should feel like an omission — the registry is the checklist.

The sprint's own test 31 registers a synthetic "instance four" and asserts
doctor reports it. That proves the *registry* dispatches. It does not prove
the *rule* holds for a mechanism shaped differently from the three the module
was written around. This file tries to construct mechanisms that slip past.

Three do. They are reported as QA findings; each has its assertion written
against the CORRECT behaviour and is expected to fail until fixed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arail import provisioning
from arail.provisioning import Assertion


@pytest.fixture(autouse=True)
def _restore_registry():
    """Every test here mutates the process-global registry. Snapshot and
    restore it so no test can leak into another (or into the rest of the
    suite, which imports the same module object)."""
    saved = dict(provisioning._REGISTRY)
    yield
    provisioning._REGISTRY.clear()
    provisioning._REGISTRY.update(saved)


# ── Baseline: does a genuinely new, fifth mechanism get caught? ─────────

def test_a_fifth_mechanism_declared_and_not_instantiated_is_a_finding():
    """The generalization claim itself, on a mechanism the module has
    never heard of and whose predicate has nothing to do with databases,
    vectors, or gates."""
    provisioning.register(
        "qa_fifth_mechanism",
        lambda **kw: Assertion("qa_fifth_mechanism", "required", True, False,
                               "declared in spec, nothing ever created it",
                               "./arailctl qa-fix"),
    )
    found = {a.key: a for a in provisioning.evaluate_all(
        repo_root=".", data_dir=".")}
    assert "qa_fifth_mechanism" in found
    assert found["qa_fifth_mechanism"].finding is True
    assert found["qa_fifth_mechanism"].tier == "required"


def test_an_off_mechanism_is_not_a_finding():
    """The other half of the three-state rule: "off" (not declared) must
    stay silent, or the check becomes noise and gets ignored."""
    provisioning.register(
        "qa_off_mechanism",
        lambda **kw: Assertion("qa_off_mechanism", "required", False, False,
                               "feature not enabled", ""),
    )
    found = {a.key: a for a in provisioning.evaluate_all(
        repo_root=".", data_dir=".")}
    assert found["qa_off_mechanism"].finding is False


def test_json_shape_is_stable_for_a_new_mechanism():
    provisioning.register(
        "qa_json_mechanism",
        lambda **kw: Assertion("qa_json_mechanism", "info", True, False, "d", "a"),
    )
    doc = provisioning.to_json(provisioning.evaluate_all(
        repo_root=".", data_dir="."))
    assert doc["schema"] == "arail.provisioning/v1"
    row = next(r for r in doc["assertions"] if r["key"] == "qa_json_mechanism")
    assert set(row) == {"key", "tier", "declared", "instantiated", "finding",
                        "detail", "action"}


# ── QA-6: a required check that CRASHES is silently demoted to info ────

def test_a_required_check_that_raises_stays_required():
    """FINDING QA-6 (medium).

    ``evaluate_all``'s except-handler is:

        out.append(Assertion(key, "info", True, False, f"check raised ..."))

    It hardcodes tier ``"info"``. So a mechanism registered as **required**
    whose predicate raises — an ImportError from a broken dependency, an
    OSError on an unreadable data dir, a bug in the predicate itself —
    becomes an *info* finding, and ``doctor`` exits 0 on it (only
    ``required`` failures degrade; see ``doctor.main``'s exit-code
    contract).

    That is this sprint's own thesis inverted: the mechanism most likely
    to be broken is the one whose check blew up, and that is exactly the
    case that gets downgraded to a line nobody's exit code reads. The
    tier belongs to the *mechanism*, not to the outcome of evaluating it.
    """
    def _boom(**kw):
        raise RuntimeError("the predicate itself is broken")

    provisioning.register("qa_crashing_required", _boom)
    found = {a.key: a for a in provisioning.evaluate_all(
        repo_root=".", data_dir=".")}
    a = found["qa_crashing_required"]
    assert a.finding is True                       # this part holds
    assert a.tier == "required", (
        "a crashing check was demoted to tier=%r, so doctor exits 0 on it"
        % a.tier
    )


# ── QA-7: register() silently replaces an existing mechanism ───────────

def test_registering_a_duplicate_key_does_not_silently_replace_a_builtin():
    """FINDING QA-7 (low).

    ``register`` is ``_REGISTRY[key] = fn`` on a plain dict. A 2.1
    mechanism (or a plugin, or a merge) that reuses an existing key
    silently *replaces* the built-in predicate — including replacing a
    real check with one that always reports healthy. The registry that
    exists to make omissions loud has a silent overwrite at its front
    door.
    """
    assert "relational_store" in provisioning.registered_keys()
    provisioning.register(
        "relational_store",
        lambda **kw: Assertion("relational_store", "required", True, True,
                               "everything is fine, trust me", ""),
    )
    found = {a.key: a for a in provisioning.evaluate_all(
        repo_root=".", data_dir="/nonexistent/data")}
    assert found["relational_store"].detail != "everything is fine, trust me", (
        "the built-in relational_store check was silently overwritten"
    )


# ── QA-5: the class check only ever looks at ONE root ──────────────────

def test_relational_store_is_asserted_for_every_resolved_root(tmp_path,
                                                              monkeypatch):
    """FINDING QA-5 (medium) — the sprint's own defect class, recurring
    inside the check built to prevent it.

    ``doctor.check_provisioning`` calls
    ``evaluate_all(repo_root=..., data_dir=str(config.DATA_DIR))`` — the
    ROOT lab's data dir, one value — and ``check_relational_store`` takes
    a single ``data_dir``. §4.3 of this same sprint exists precisely
    because "all instances" walks miss five of six roots on the
    operator's machine, and the operator's measured usage is one World at
    a time with the root lab *never started*.

    So: root lab's DB created, five World instances with no DB at all,
    and the class check reports ``relational_store: OK``. "Declared and
    not instantiated" is true for five roots and silent for all five.

    Asserted here against the correct behaviour: with any resolved root
    missing its database, ``relational_store`` must be a finding.
    """
    from arail.dbspec.ensure import DEFAULT_SPEC_DIR, ensure_db
    if not (DEFAULT_SPEC_DIR / "schema" / "migrations").is_dir():
        pytest.skip("no spec/schema/migrations in this checkout")

    repo_root = tmp_path / "checkout"
    root_data = repo_root / "lab" / "data"
    root_data.mkdir(parents=True)
    # The root lab is fully provisioned...
    assert ensure_db(root_data, apply=True).state == "created"
    # ...and five Worlds exist on disk with no database at all.
    for slug in ("ai", "qukaizen", "video-games", "debt-finance", "finance"):
        (repo_root / "lab" / "instances" / slug / "data").mkdir(parents=True)
    (repo_root / "lab" / "instances" / "registry.d").mkdir(parents=True)

    from arail.data_dirs import resolve_data_dirs
    rows = resolve_data_dirs(str(repo_root))
    assert len(rows) == 6, [r.slug for r in rows]
    missing = [r for r in rows
               if not (Path(r.data_dir) / "arail.db").exists()]
    assert len(missing) == 5, missing

    a = provisioning.check_relational_store(
        repo_root=str(repo_root), data_dir=str(root_data))
    assert a.finding is True, (
        "relational_store reported instantiated=True while %d of %d resolved "
        "roots have no arail.db: %s"
        % (len(missing), len(rows), [r.slug for r in missing])
    )


# ── Round 4: two more escapes from the "never silence" rule ────────────

def test_a_predicate_that_returns_garbage_does_not_silence_every_other_check():
    """FINDING QA-10 (MEDIUM, round 4).

    ``evaluate_all``'s per-key ``try/except`` catches a predicate that
    RAISES (QA-6, now fixed with a proper tier) but not one that simply
    returns the wrong thing. A ``None`` return sails through the loop, and
    the ``AttributeError`` surfaces later — in ``to_json``, or in
    ``doctor.check_provisioning``'s render loop, both of which sit inside
    an OUTER try that swallows the whole section.

    Measured consequence in ``doctor``: with one such mechanism
    registered, the run aborts partway and NEITHER ``relational_store``
    NOR ``vector_backend`` — the two mechanisms this entire sprint exists
    for — is evaluated or recorded. The output is one vague line,
    ``provisioning check failed: AttributeError``, and if the remaining
    checks happen to be healthy, ``doctor`` exits 0.

    A registry whose contract is "declared and not instantiated is never
    silence" must not have a single registration able to silence all the
    others.
    """
    provisioning.register("qa_garbage_return", lambda **kw: None)
    rows = provisioning.evaluate_all(repo_root=".", data_dir=".")
    assert all(isinstance(r, Assertion) for r in rows), (
        "evaluate_all returned a non-Assertion: %s"
        % sorted({type(r).__name__ for r in rows}))
    found = {a.key: a for a in rows}
    assert "qa_garbage_return" in found, (
        "a malformed predicate produced no row at all — it is invisible")
    assert found["qa_garbage_return"].finding is True
    for essential in ("relational_store", "vector_backend"):
        assert essential in found, (
            "%s was never evaluated because another mechanism's predicate "
            "was malformed" % essential)
    provisioning.to_json(rows)  # must not raise


@pytest.mark.xfail(strict=True, reason=(
    "QA-11: filed in sprints/BACKLOG.md by explicit coordinator ruling "
    "(file, do not fix). strict=True so this flips loudly the moment the "
    "back door is closed, rather than rotting into a permanently-skipped "
    "line nobody reads."))
def test_a_predicate_cannot_impersonate_another_mechanisms_key():
    """FINDING QA-11 (LOW, round 4, cosmetic).

    QA-7 closed the front door: ``register()`` now refuses a duplicate
    key. The back door is still open — a predicate registered under its
    own key may RETURN an ``Assertion`` carrying somebody else's ``key``,
    producing two rows for one mechanism (one of them healthy) in the
    printed table and in ``arail.provisioning/v1``.

    Graded LOW because it cannot flip an exit code: ``doctor._FINDINGS``
    is a list and ``degraded`` is ``any(...)``, so the genuine failing row
    still degrades. It is a reporting-integrity wart, not a mask.
    """
    provisioning.register(
        "qa_impersonator",
        lambda **kw: Assertion("relational_store", "required", True, True,
                               "all good, nothing to see here", ""))
    rows = provisioning.evaluate_all(repo_root=".", data_dir="/nonexistent/data")
    keys = [a.key for a in rows]
    assert len(keys) == len(set(keys)), (
        "one mechanism produced a row under another's key: %s"
        % sorted(k for k in keys if keys.count(k) > 1))


# ── Round 5: two latent escapes, filed rather than blocking ────────────
#
# Neither is reachable today: `provisioning.register` has NO caller outside
# src/arail/provisioning.py itself (verified by grep across src/ and lab/),
# so every registered predicate is in-repo code. They matter when ARAIL 2.1
# adds a mechanism — which is precisely what this registry exists for — so
# they are pinned here as executable repros rather than prose in a backlog
# entry.

@pytest.mark.xfail(strict=True, reason=(
    "QA-15 (LOW, filed): evaluate_all catches Exception, not BaseException. "
    "A predicate raising SystemExit/KeyboardInterrupt propagates through "
    "evaluate_all AND through doctor.check_provisioning's outer `except "
    "Exception`, aborting the whole checkup."))
def test_a_predicate_raising_baseexception_does_not_abort_the_checkup():
    def _exit(**kw):
        raise SystemExit(9)

    provisioning.register("qa_sysexit", _exit)
    rows = provisioning.evaluate_all(repo_root=".", data_dir=".")
    found = {a.key: a for a in rows}
    assert found["qa_sysexit"].finding is True
    for essential in ("relational_store", "vector_backend"):
        assert essential in found


@pytest.mark.xfail(strict=True, reason=(
    "QA-16 (LOW, filed): no tier validation. An Assertion returning an "
    "unrecognized tier is a finding that degrades nothing — doctor's exit "
    "code counts only level == 'required', so 'urgent' is silently "
    "non-degrading. A typo'd tier in a future mechanism is invisible."))
def test_an_unrecognized_tier_is_rejected_rather_than_silently_ignored():
    provisioning.register(
        "qa_bogus_tier",
        lambda **kw: Assertion("qa_bogus_tier", "urgent", True, False,
                               "declared, never instantiated", "fix me"))
    rows = provisioning.evaluate_all(repo_root=".", data_dir="/nonexistent")
    a = {x.key: x for x in rows}["qa_bogus_tier"]
    assert a.tier in ("required", "info"), (
        "tier %r is neither required nor info, so nothing acts on this "
        "finding" % a.tier)
